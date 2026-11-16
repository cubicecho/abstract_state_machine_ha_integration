"""One running machine: its status and its rules wired to HA."""
from __future__ import annotations

import copy
import logging
from collections import deque
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

import probatio as vol  # HA 2026.10+ validates with probatio
from homeassistant.core import CALLBACK_TYPE, Context, Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import condition as condition_helper
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers import trigger as trigger_helper
from homeassistant.helpers.event import async_track_point_in_utc_time, async_track_time_change
from homeassistant.util import dt as dt_util
from homeassistant.util.hass_dict import HassKey

from . import engine
from .const import DOMAIN, EVENT_RULE_APPLIED
from .model import KIND_CONDITION, KIND_TRIGGER, MachineDef, RuleDef, referenced_entities
from .timeinstate import TimeInState

_LOGGER = logging.getLogger(__name__)

# Rule-driven changes are counted over this window against ``max_flips``.
FLIP_WINDOW = timedelta(minutes=1)

type Listener = Callable[[Context | None], None]
type Checker = condition_helper.ConditionChecker

# Renames made in the reconfigure flow, waiting for the reload that follows,
# so a machine sitting in a renamed state stays in it: {machine_id: {old: new}}.
_RENAMES: HassKey[dict[str, dict[str, str]]] = HassKey(f"{DOMAIN}_renames")


@callback
def async_note_rename(hass: HomeAssistant, machine_id: str, old: str, new: str) -> None:
    renames = hass.data.setdefault(_RENAMES, {}).setdefault(machine_id, {})
    for earlier, target in renames.items():
        if target == old:
            renames[earlier] = new
    renames[old] = new


async def async_validate_triggers(hass: HomeAssistant, raw: Any) -> list[dict[str, Any]]:
    """Validate triggers as the automation editor sends them. Raises ``vol.Invalid`` / ``HomeAssistantError``."""
    return await trigger_helper.async_validate_trigger_config(hass, cv.TRIGGER_SCHEMA(copy.deepcopy(raw)))


async def async_validate_conditions(hass: HomeAssistant, raw: Any) -> list[Any]:
    """Validate conditions as the automation editor sends them. Raises ``vol.Invalid`` / ``HomeAssistantError``."""
    return await condition_helper.async_validate_conditions_config(hass, cv.CONDITIONS_SCHEMA(copy.deepcopy(raw)))


class MachineRuntime:
    """The live side of one machine.

    The select entity owns persistence: it restores the last status, hands
    it to ``async_restore``, then calls ``async_start`` once HA has started.
    """

    def __init__(self, hass: HomeAssistant, machine: MachineDef) -> None:
        self.hass = hass
        self.machine_id = machine.id
        self.machine = machine
        self.status: engine.Status | None = None
        self.computed: engine.Computed | None = None  # what the condition rules say
        self.flip_limited = False
        self.invalid_rules: dict[str, str] = {}  # rule id → why it was skipped at setup
        self.time: TimeInState | None = None  # time in each state today
        self.entity_id: str | None = None  # the select's, once added (for the activity log)
        self._true_since: dict[str, datetime] = {}  # condition rules with a wait: true since when
        self._wait_timer: CALLBACK_TYPE | None = None
        self._listeners: list[Listener] = []
        self._conditions: dict[str, list[Checker]] = {}  # condition rules, by rule id
        self._trigger_conditions: dict[str, list[Checker]] = {}  # trigger rules' extra checks
        self._unsubs: list[CALLBACK_TYPE] = []
        self._hold_timer: CALLBACK_TYPE | None = None
        self._resume_timer: CALLBACK_TYPE | None = None
        self._flips: deque[datetime] = deque()
        self._evaluate_pending = False
        self._started = False

    @property
    def issue_id(self) -> str:
        return f"flip_limit_{self.machine_id}"

    @property
    def broken_issue_id(self) -> str:
        return f"broken_rules_{self.machine_id}"

    @callback
    def async_add_listener(self, listener: Listener) -> Callable[[], None]:
        """Call ``listener(context)`` on every change of state or attributes."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    # Lifecycle ------------------------------------------------------------

    @callback
    def async_restore(self, restored: engine.Status | None, time: TimeInState | None = None) -> None:
        """Start from the restored status, or the fallback. Not a change of state."""
        renames = self.hass.data.get(_RENAMES, {}).pop(self.machine_id, {})
        if time is not None and renames:
            time = replace(
                time,
                state=renames.get(time.state, time.state) if time.state else None,
                totals={renames.get(name, name): seconds for name, seconds in time.totals.items()},
            )
        if restored is not None and renames:
            restored = replace(
                restored,
                state=renames.get(restored.state, restored.state),
                previous=renames.get(restored.previous, restored.previous) if restored.previous else None,
                held_against=renames.get(restored.held_against, restored.held_against)
                if restored.held_against else None,
            )
        self.status = engine.initial(self.machine, restored, dt_util.utcnow())
        now = dt_util.now()
        self.time = (
            time.resumed(now).switch(self.status.state, now) if time is not None
            else TimeInState.start(self.status.state, now)
        )
        self._notify(None)

    async def async_start(self) -> None:
        """Wire up the rules (after HA has started, so inputs exist), then apply condition rules."""
        ir.async_delete_issue(self.hass, DOMAIN, self.issue_id)
        polling = False
        for state, rule in self.machine.rules():
            try:
                if rule.kind == KIND_CONDITION:
                    checkers = await self._async_checkers(rule.conditions)
                    self._conditions[rule.id] = checkers
                    for checker in checkers:
                        self._unsubs.append(await checker.async_track_changes(self._schedule_evaluate))
                        polling = polling or checker.needs_polling
                elif rule.kind == KIND_TRIGGER:
                    await self._async_attach_trigger(state.name, rule)
            except (vol.Invalid, HomeAssistantError) as err:
                self.invalid_rules[rule.id] = str(err)
                _LOGGER.error("%s: rule \"%s\" (into %s) is invalid, skipping it: %s",
                              self.machine.name, rule.name, state.name, err)
        if polling:  # time, sun and template conditions can change without telling us
            self._unsubs.append(async_track_time_change(self.hass, self._tick, second=0))
        self._seed_waits()
        self._async_check_broken()
        self._unsubs.append(self.hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, self._registry_updated))
        self._started = True
        self._schedule_hold_timer()
        self._evaluate()

    @callback
    def async_stop(self) -> None:
        self._started = False
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        for checkers in (*self._conditions.values(), *self._trigger_conditions.values()):
            for checker in checkers:
                checker.async_unload()
        self._conditions.clear()
        self._trigger_conditions.clear()
        self.invalid_rules.clear()
        self._true_since.clear()
        for timer in (self._hold_timer, self._resume_timer, self._wait_timer):
            if timer:
                timer()
        self._hold_timer = self._resume_timer = self._wait_timer = None

    async def _async_checkers(self, raw: tuple[dict[str, Any], ...]) -> list[Checker]:
        configs = await async_validate_conditions(self.hass, list(raw))
        return [await condition_helper.async_from_config(self.hass, config) for config in configs]

    async def _async_attach_trigger(self, to: str, rule: RuleDef) -> None:
        configs = await async_validate_triggers(self.hass, list(rule.triggers))
        self._trigger_conditions[rule.id] = await self._async_checkers(rule.conditions) if rule.conditions else []

        async def fired(run_variables: dict[str, Any], context: Context | None = None) -> None:
            self._fired(to, rule, run_variables, context)

        def log(level: int, msg: str, **kwargs: Any) -> None:
            _LOGGER.log(level, "%s rule \"%s\": %s", self.machine.name, rule.name, msg, **kwargs)

        unsub = await trigger_helper.async_initialize_triggers(
            self.hass, configs, fired, DOMAIN, f"{self.machine.name}: {rule.name}", log
        )
        if unsub is not None:
            self._unsubs.append(unsub)

    # Inputs -------------------------------------------------------------------

    @callback
    def _tick(self, now: datetime) -> None:
        self._evaluate(now)

    @callback
    def _schedule_evaluate(self) -> None:
        """Coalesce bursts of input changes into one check, outside the caller's stack.

        A tracked task, not ``call_soon``: HA waits for it (``async_block_till_done``),
        and machines that read each other can't recurse.
        """
        if self._evaluate_pending:
            return
        self._evaluate_pending = True

        async def run() -> None:
            self._evaluate_pending = False
            self._evaluate()

        self.hass.async_create_task(run(), f"{DOMAIN} evaluate {self.machine.name}", eager_start=False)

    @callback
    def _evaluate(self, now: datetime | None = None) -> None:
        """Re-check the condition rules and follow them (unless a hold is on).

        ``now``: the time a timer fired for, so a hold that just ran out counts as expired.
        """
        if not self._started or self.status is None:
            return
        now = max(now, dt_util.utcnow()) if now else dt_util.utcnow()
        self.computed = self._compute(now)
        self._apply(engine.apply_computed(self.status, self.computed, now), None)

    def _compute(self, now: datetime | None = None) -> engine.Computed | None:
        """What the condition rules say. A rule with a wait counts once it's been true that long."""
        now = now or dt_util.utcnow()
        matches: dict[str, bool] = {}
        next_deadline: datetime | None = None
        for _state, rule in self.machine.rules():
            if (checkers := self._conditions.get(rule.id)) is None:
                continue
            true = _check(checkers, None)
            if not true or not rule.for_seconds:
                self._true_since.pop(rule.id, None)
                matches[rule.id] = true
                continue
            deadline = self._true_since.setdefault(rule.id, now) + timedelta(seconds=rule.for_seconds)
            matches[rule.id] = now >= deadline
            if now < deadline and (next_deadline is None or deadline < next_deadline):
                next_deadline = deadline
        self._schedule_wait_timer(next_deadline)
        return engine.compute(self.machine, matches)

    def _seed_waits(self) -> None:
        """After a restart, a waiting rule that already put the machine where it is counts as waited.

        Otherwise every restart would drop the machine out of, say, "away" until the wait ran again.
        """
        status = self.status
        if status is None:
            return
        state = self.machine.state(status.state)
        for rule in state.rules if state else ():
            if rule.for_seconds and rule.id in self._conditions and _check(self._conditions[rule.id], None):
                self._true_since[rule.id] = min(status.changed_at, dt_util.utcnow()) - timedelta(seconds=rule.for_seconds)

    def _schedule_wait_timer(self, deadline: datetime | None) -> None:
        if self._wait_timer:
            self._wait_timer()
            self._wait_timer = None
        if deadline is None or not self._started:
            return

        @callback
        def waited(now: datetime) -> None:
            self._wait_timer = None
            self._evaluate(now)

        self._wait_timer = async_track_point_in_utc_time(self.hass, waited, deadline)

    @callback
    def _fired(self, to: str, rule: RuleDef, variables: dict[str, Any], context: Context | None) -> None:
        if self.status is None or not _check(self._trigger_conditions.get(rule.id, []), variables):
            return
        self.computed = self._compute()
        self._apply(engine.fire_trigger(self.status, to, rule, self.computed, dt_util.utcnow()), context)

    # Manual -------------------------------------------------------------------

    @callback
    def async_set_state(
        self,
        to: str,
        *,
        reason: str = engine.REASON_MANUAL,
        hold: timedelta | None = None,
        context: Context | None = None,
    ) -> None:
        """Move to ``to`` by hand. On a machine with condition rules it holds until they change."""
        if self.status is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="not_ready",
                translation_placeholders={"machine": self.machine.name},
            )
        now = dt_util.utcnow()
        if self._started:
            self.computed = self._compute()
        try:
            result = engine.set_manual(
                self.machine, self.status, to, self.computed, now,
                reason=reason, hold_until=now + hold if hold else None,
            )
        except engine.UnknownState as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="unknown_state",
                translation_placeholders={
                    "state": to, "machine": self.machine.name, "states": ", ".join(self.machine.names),
                },
            ) from err
        self._apply(result, context)

    @callback
    def async_clear_override(self, context: Context | None = None) -> None:
        """Drop a hold and follow the condition rules now."""
        if self.status is None or not self.status.held:
            return
        self.status = engine.clear_hold(self.status)
        if self._started:
            self.computed = self._compute()
            result = engine.apply_computed(self.status, self.computed, dt_util.utcnow())
            if result is not None:
                self._apply(result, context)
                return
        self._apply((self.status, None), context)

    # Explain --------------------------------------------------------------------

    @callback
    def explain(self) -> dict[str, Any]:
        """Where the machine is, why, and what each rule says right now (``explain`` action)."""
        status = self.status
        computed = self._compute() if self._started else None
        rules: list[dict[str, Any]] = []
        for state, rule in self.machine.rules():
            item: dict[str, Any] = {"state": state.name, "name": rule.name, "kind": rule.kind}
            if rule.id in self.invalid_rules:
                item["error"] = self.invalid_rules[rule.id]
            elif self._started and rule.kind == KIND_CONDITION:
                true = _check(self._conditions.get(rule.id, []), None)
                item["matches"] = true and (not rule.for_seconds or rule.id not in self._waiting())
                if rule.for_seconds:
                    item["for_seconds"] = rule.for_seconds
                    if rule.id in self._waiting():
                        item["waiting_until"] = self._waiting()[rule.id].isoformat()
            elif self._started:
                item["from"] = list(rule.from_states)
                item["from_ok"] = status is not None and (not rule.from_states or status.state in rule.from_states)
                if rule.conditions:
                    item["conditions_pass"] = _check(self._trigger_conditions.get(rule.id, []), None)
            rules.append(item)
        result: dict[str, Any] = {
            "machine": self.machine.name,
            "state": status.state if status else None,
            "previous": status.previous if status else None,
            "reason": status.reason if status else None,
            "changed_at": status.changed_at.isoformat() if status else None,
            "manual": status.manual if status else None,
            "fallback": self.machine.fallback,
            "started": self._started,
            "flip_limited": self.flip_limited,
            "rules": rules,
        }
        if self.machine.has_condition_rules:
            result["rules_state"] = computed.state if computed else None
            result["rules_reason"] = computed.reason if computed else None
            result["override"] = bool(status and status.held)
            result["override_until"] = (
                status.hold_until.isoformat() if status and status.held and status.hold_until else None
            )
        return result

    def _waiting(self) -> dict[str, datetime]:
        """Rules true but still waiting: rule id → when they'll count."""
        now = dt_util.utcnow()
        waits = {rule.id: rule.for_seconds for _, rule in self.machine.rules() if rule.for_seconds}
        return {
            rule_id: deadline
            for rule_id, since in self._true_since.items()
            if rule_id in waits and (deadline := since + timedelta(seconds=waits[rule_id])) > now
        }

    # Broken rules -------------------------------------------------------------

    @callback
    def _registry_updated(self, event: Event[er.EventEntityRegistryUpdatedData]) -> None:
        """An entity was added, removed or renamed: a rule may have broken, or been mended."""
        if self._started:
            self._async_check_broken()

    @callback
    def _async_check_broken(self) -> None:
        """A repair issue listing rules that were skipped, or that read entities that don't exist."""
        ent_reg = er.async_get(self.hass)
        problems: list[str] = []
        for state, rule in self.machine.rules():
            if rule.id in self.invalid_rules:
                problems.append(f"- **{rule.name}** (into {state.name}): {self.invalid_rules[rule.id]}")
                continue
            missing = sorted(
                entity_id
                for entity_id in referenced_entities([list(rule.triggers), list(rule.conditions)])
                if self.hass.states.get(entity_id) is None and ent_reg.async_get(entity_id) is None
            )
            if missing:
                problems.append(f"- **{rule.name}** (into {state.name}): reads {', '.join(missing)}, which doesn't exist")
        if not problems:
            ir.async_delete_issue(self.hass, DOMAIN, self.broken_issue_id)
            return
        ir.async_create_issue(
            self.hass, DOMAIN, self.broken_issue_id,
            is_fixable=False, severity=ir.IssueSeverity.WARNING, translation_key="broken_rules",
            translation_placeholders={"machine": self.machine.name, "problems": "\n".join(problems)},
        )

    # Output -------------------------------------------------------------------

    @callback
    def _apply(self, result: engine.Result, context: Context | None) -> None:
        if result is None:
            return
        status, transition = result
        if transition is not None and not transition.manual and not self._allow_flip(transition.at):
            return
        self.status = status
        if transition is not None:
            if self.time is not None:
                self.time = self.time.switch(status.state, dt_util.now())
            if not transition.manual or transition.reason != engine.REASON_MANUAL:
                context = self._log_reason(transition, context)
        self._schedule_hold_timer()
        self._notify(context)

    def _log_reason(self, transition: engine.Transition, context: Context | None) -> Context:
        """Say why in the activity log: "Day type moved to holiday (PTO today)".

        Like automations do it: an event in a new context (a child of the input's),
        and the state change in that same context. ``logbook.py`` describes the event.
        """
        reason_context = Context(parent_id=context.id if context else None)
        self.hass.bus.async_fire(
            EVENT_RULE_APPLIED,
            {
                "entity_id": self.entity_id,
                "machine": self.machine.name,
                "reason": transition.reason,
                "to": transition.to_state,
            },
            context=reason_context,
        )
        return reason_context

    def _notify(self, context: Context | None) -> None:
        for listener in list(self._listeners):
            listener(context)

    def _allow_flip(self, now: datetime) -> bool:
        """At most ``max_flips`` rule-driven changes per minute: stops loops between machines."""
        while self._flips and now - self._flips[0] >= FLIP_WINDOW:
            self._flips.popleft()
        if len(self._flips) < self.machine.max_flips:
            self._flips.append(now)
            if self.flip_limited:
                self.flip_limited = False
            return True
        if not self.flip_limited:
            self.flip_limited = True
            _LOGGER.warning(
                "%s changed state %s times in a minute, its limit; ignoring its rules until the minute is up. "
                "Its rules may form a loop with another machine",
                self.machine.name, self.machine.max_flips,
            )
            ir.async_create_issue(
                self.hass, DOMAIN, self.issue_id,
                is_fixable=False, severity=ir.IssueSeverity.WARNING, translation_key="flip_limit",
                translation_placeholders={"machine": self.machine.name, "max_flips": str(self.machine.max_flips)},
            )
            self._notify(None)
        if self._resume_timer is None:
            @callback
            def resume(now: datetime) -> None:
                self._resume_timer = None
                self._evaluate(now)

            self._resume_timer = async_track_point_in_utc_time(self.hass, resume, self._flips[0] + FLIP_WINDOW)
        return False

    def _schedule_hold_timer(self) -> None:
        if self._hold_timer:
            self._hold_timer()
            self._hold_timer = None
        until = self.status.hold_until if self.status and self.status.held else None
        if until is None or not self._started:
            return

        @callback
        def expired(now: datetime) -> None:
            self._hold_timer = None
            self._evaluate(now)

        self._hold_timer = async_track_point_in_utc_time(self.hass, expired, until)


def _check(checkers: list[Checker], variables: dict[str, Any] | None) -> bool:
    """All conditions true. Errors and unavailable inputs count as false (HA's default)."""
    try:
        return all(checker.async_check(variables=variables) is not False for checker in checkers)
    except (HomeAssistantError, ValueError) as err:
        _LOGGER.debug("Condition check failed: %s", err)
        return False
