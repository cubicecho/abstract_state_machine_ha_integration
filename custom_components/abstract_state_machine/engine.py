"""Decides a machine's state. Pure, no HA imports.

Three things move a machine:

- **Condition rules** (``apply_computed``): the first state, top to bottom,
  with a matching condition rule wins; the fallback when none match.
- **Trigger rules** (``fire_trigger``): a trigger fired, and the machine is
  in one of the rule's ``from`` states.
- **Manual changes** (``set_manual``): the select, ``set_state``.

On a machine with condition rules, a manual change or a trigger rule's
change **holds** against them until what they say changes (or ``hold``
runs out, or ``clear_override``). Then the machine follows them again.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime

from .model import KIND_CONDITION, MachineDef, RuleDef

REASON_MANUAL = "manual"
REASON_RESTORED = "restored"
REASON_INITIAL = "initial"
REASON_FALLBACK = "fallback"


class UnknownState(ValueError):
    """The requested state isn't one of the machine's states."""


@dataclass(frozen=True)
class Status:
    """Where a machine is and how it got there."""

    state: str
    previous: str | None
    reason: str
    changed_at: datetime
    manual: bool = False
    # Holding against the condition rules: since they said ``held_against``.
    held: bool = False
    held_against: str | None = None
    hold_until: datetime | None = None


@dataclass(frozen=True)
class Computed:
    """What the condition rules say right now."""

    state: str
    reason: str


@dataclass(frozen=True)
class Transition:
    """One change of state."""

    from_state: str | None
    to_state: str
    reason: str
    manual: bool
    at: datetime


type Result = tuple[Status, Transition | None] | None
"""``None``: nothing changed. ``(status, None)``: attributes changed, no transition."""


def initial(machine: MachineDef, restored: Status | None, now: datetime) -> Status:
    """The state to start in: the restored one if it still exists, else the fallback.

    Starting never counts as a transition: no events on startup. Condition
    rules are applied right after, by the caller.
    """
    names = machine.names
    if restored is not None and restored.state in names:
        previous = restored.previous if restored.previous in names else None
        held = restored.held and machine.has_condition_rules
        return Status(
            restored.state,
            previous,
            REASON_RESTORED,
            restored.changed_at,
            restored.manual,
            held,
            restored.held_against if held else None,
            restored.hold_until if held else None,
        )
    return Status(machine.fallback, None, REASON_INITIAL, now)


def compute(machine: MachineDef, matches: Mapping[str, bool]) -> Computed | None:
    """What the condition rules say, given which rules match. ``None``: the machine has none."""
    if not machine.has_condition_rules:
        return None
    for state, rule in machine.rules():
        if rule.kind == KIND_CONDITION and matches.get(rule.id):
            return Computed(state.name, rule.name)
    return Computed(machine.fallback, REASON_FALLBACK)


def apply_computed(status: Status, computed: Computed | None, now: datetime) -> Result:
    """Follow the condition rules, unless a hold is still on."""
    if computed is None:
        return None
    changed = False
    if status.held:
        expired = status.hold_until is not None and now >= status.hold_until
        if not expired and computed.state == status.held_against:
            return None
        status = replace(status, held=False, held_against=None, hold_until=None)
        changed = True
    if computed.state == status.state:
        if status.reason != computed.reason or status.manual:
            return replace(status, reason=computed.reason, manual=False), None
        return (status, None) if changed else None
    new = Status(computed.state, status.state, computed.reason, now)
    return new, Transition(status.state, computed.state, computed.reason, False, now)


def set_manual(
    machine: MachineDef,
    status: Status,
    to: str,
    computed: Computed | None,
    now: datetime,
    *,
    reason: str = REASON_MANUAL,
    hold_until: datetime | None = None,
) -> Result:
    """Move to ``to`` by hand. Holds against the condition rules when they disagree."""
    if to not in machine.names:
        raise UnknownState(to)
    held = computed is not None and computed.state != to
    against = computed.state if held and computed else None
    until = hold_until if held else None
    if to == status.state:
        new = replace(status, reason=reason, manual=True, held=held, held_against=against, hold_until=until)
        return (new, None) if new != status else None
    new = Status(to, status.state, reason, now, True, held, against, until)
    return new, Transition(status.state, to, reason, True, now)


def fire_trigger(status: Status, to: str, rule: RuleDef, computed: Computed | None, now: datetime) -> Result:
    """A trigger rule into ``to`` fired (its conditions already passed)."""
    if rule.from_states and status.state not in rule.from_states:
        return None
    if to == status.state:
        return None
    held = computed is not None and computed.state != to
    new = Status(to, status.state, rule.name, now, False, held, computed.state if held and computed else None)
    return new, Transition(status.state, to, rule.name, False, now)


def clear_hold(status: Status) -> Status:
    return replace(status, held=False, held_against=None, hold_until=None)
