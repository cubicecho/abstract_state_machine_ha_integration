"""Decides a machine's state. Pure, no HA imports.

Phase 1 only knows manual changes and restoring after a restart. Rules
(trigger and condition) land here in phase 2.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .model import MachineDef

REASON_MANUAL = "manual"
REASON_RESTORED = "restored"
REASON_INITIAL = "initial"


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


@dataclass(frozen=True)
class Transition:
    """One change of state."""

    from_state: str | None
    to_state: str
    reason: str
    manual: bool
    at: datetime


def initial(machine: MachineDef, restored: Status | None, now: datetime) -> Status:
    """The state to start in: the restored one if it still exists, else the fallback.

    Starting never counts as a transition: no events on startup.
    """
    names = machine.names
    if restored is not None and restored.state in names:
        previous = restored.previous if restored.previous in names else None
        return Status(restored.state, previous, REASON_RESTORED, restored.changed_at, restored.manual)
    return Status(machine.fallback, None, REASON_INITIAL, now)


def set_state(
    machine: MachineDef,
    status: Status,
    to: str,
    now: datetime,
    *,
    reason: str = REASON_MANUAL,
    manual: bool = True,
) -> tuple[Status, Transition] | None:
    """Move to ``to``. ``None`` when already there (nothing changes, no event)."""
    if to not in machine.names:
        raise UnknownState(to)
    if to == status.state:
        return None
    new = Status(to, status.state, reason, now, manual)
    return new, Transition(status.state, to, reason, manual, now)
