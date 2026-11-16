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
from homeassistant.core import CALLBACK_TYPE, Context, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import condition as condition_helper
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers import trigger as trigger_helper
from homeassistant.helpers.event import async_track_point_in_utc_time, async_track_time_change
from homeassistant.util import dt as dt_util
from homeassistant.util.hass_dict import HassKey

from . import engine
from .const import DOMAIN
from .model import KIND_CONDITION, KIND_TRIGGER, MachineDef, RuleDef

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

    @callback
    def async_add_listener(self, listener: Listener) -> Callable[[], None]:
        """Call ``listener(context)`` on every change of state or attributes."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    # Lifecycle ------------------------------------------------------------

    @callback
    def async_restore(self, restored: engine.Status | None) -> None:
        """Start from the restored status, or the fallback. Not a change of state."""
        renames = self.hass.data.get(_RENAMES, {}).pop(self.machine_id, {})
        if restored is not None and renames:
            restored = replace(
                restored,
                state=renames.get(restored.state, restored.state),
                previous=renames.get(restored.previous, restored.previous) if restored.previous else None,
                held_against=renames.get(restored.held_against, restored.held_against)
                if restored.held_against else None,
            )
        self.status = engine.initial(self.machine, restored, dt_util.utcnow())
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
                _LOGGER.error("%s: rule \"%s\" (into %s) is invalid, skipping it: %s",
                              self.machine.name, rule.name, state.name, err)
        if polling:  # time, sun and template conditions can change without telling us
            self._unsubs.append(async_track_time_change(self.hass, self._tick, second=0))
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
        for timer in (self._hold_timer, self._resume_timer):
            if timer:
                timer()
        self._hold_timer = self._resume_timer = None

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
        self.computed = self._compute()
        now = max(now, dt_util.utcnow()) if now else dt_util.utcnow()
        self._apply(engine.apply_computed(self.status, self.computed, now), None)

    def _compute(self) -> engine.Computed | None:
        matches = {rule_id: _check(checkers, None) for rule_id, checkers in self._conditions.items()}
        return engine.compute(self.machine, matches)

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

    # Output -------------------------------------------------------------------

    @callback
    def _apply(self, result: engine.Result, context: Context | None) -> None:
        if result is None:
            return
        status, transition = result
        if transition is not None and not transition.manual and not self._allow_flip(transition.at):
            return
        self.status = status
        self._schedule_hold_timer()
        self._notify(context)

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
