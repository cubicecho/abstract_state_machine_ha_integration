"""A machine's definition: states, fallback, person. Pure, no HA imports.

A machine is stored as a config subentry. Its ``data`` looks like::

    {
        "states": [{"name": "running", "icon": "mdi:dishwasher"}, ...],
        "fallback": "cleared",
        "person": "person.alice",   # optional
    }

Each state dict will also carry its rules (phase 2). Edits here keep any
keys they don't know about, so rules survive renames and reorders.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

# HA's own states. A select can't use them as options without confusion.
RESERVED_STATES = frozenset({"unknown", "unavailable"})
MAX_STATE_LENGTH = 255
MIN_STATES = 2


class InvalidStates(ValueError):
    """The state names can't make a machine. ``key`` is a translation key."""

    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


@dataclass(frozen=True)
class StateDef:
    """One state of a machine."""

    name: str
    icon: str | None = None


@dataclass(frozen=True)
class MachineDef:
    """What a machine is, parsed from its subentry."""

    name: str
    states: tuple[StateDef, ...]
    fallback: str
    person: str | None = None

    @property
    def names(self) -> list[str]:
        return [state.name for state in self.states]

    def state(self, name: str) -> StateDef | None:
        return next((state for state in self.states if state.name == name), None)


def clean_state_names(raw: Iterable[str]) -> list[str]:
    """Trim names and drop blanks. Raise ``InvalidStates`` if the rest won't do."""
    names = [name.strip() for name in raw if name and name.strip()]
    if len(names) < MIN_STATES:
        raise InvalidStates("too_few_states")
    if any(len(name) > MAX_STATE_LENGTH for name in names):
        raise InvalidStates("state_too_long")
    if any(name.casefold() in RESERVED_STATES for name in names):
        raise InvalidStates("reserved_state")
    folded = [name.casefold() for name in names]
    if len(set(folded)) != len(folded):
        raise InvalidStates("duplicate_state")
    return names


def new_machine_data(names: Iterable[str], fallback: str | None, person: str | None) -> dict[str, Any]:
    """Subentry data for a new machine. ``names`` must already be clean."""
    states = [{"name": name} for name in names]
    return _with_fallback({"states": states, "fallback": fallback, "person": person or None})


def parse(title: str, data: Mapping[str, Any]) -> MachineDef:
    """Read a machine from subentry data. Tolerant: bad bits fall back, never raise."""
    states: list[StateDef] = []
    seen: set[str] = set()
    for raw in data.get("states") or []:
        name = str(raw.get("name", "")).strip() if isinstance(raw, Mapping) else ""
        if not name or name.casefold() in seen:
            continue
        seen.add(name.casefold())
        states.append(StateDef(name=name, icon=raw.get("icon") or None))
    names = [state.name for state in states]
    fallback = data.get("fallback")
    if fallback not in names:
        fallback = names[0] if names else ""
    return MachineDef(name=title, states=tuple(states), fallback=fallback, person=data.get("person") or None)


def with_states(data: Mapping[str, Any], names: Iterable[str]) -> dict[str, Any]:
    """Replace the state list, in the given order. Kept states keep their icon and rules.

    ``names`` must already be clean. Matching is exact, so changing a name's
    case here makes a new state; use ``rename_state`` to keep its settings.
    """
    old = {state["name"]: state for state in data.get("states") or [] if isinstance(state, Mapping)}
    states = [dict(old.get(name, {"name": name})) for name in names]
    return _with_fallback({**data, "states": states})


def rename_state(data: Mapping[str, Any], old: str, new: str) -> dict[str, Any]:
    """Rename one state, keeping its place, icon, rules and fallback role.

    Raise ``InvalidStates`` if the new name clashes or isn't allowed.
    """
    names = [state["name"] for state in data.get("states") or []]
    if old not in names:
        raise InvalidStates("unknown_state")
    renamed = clean_state_names([new if name == old else name for name in names])
    states = [
        {**state, "name": renamed[i]} for i, state in enumerate(data.get("states") or [])
    ]
    fallback = data.get("fallback")
    return _with_fallback({**data, "states": states, "fallback": new.strip() if fallback == old else fallback})


def with_icons(data: Mapping[str, Any], icons: Mapping[str, str | None]) -> dict[str, Any]:
    """Set each state's icon (``None`` or ``""`` clears it)."""
    states = []
    for state in data.get("states") or []:
        state = dict(state)
        if state["name"] in icons:
            if icon := icons[state["name"]]:
                state["icon"] = icon
            else:
                state.pop("icon", None)
        states.append(state)
    return {**data, "states": states}


def _with_fallback(data: dict[str, Any]) -> dict[str, Any]:
    """Make sure the fallback is one of the states (the first, if not)."""
    names = [state["name"] for state in data["states"]]
    if data.get("fallback") not in names:
        data["fallback"] = names[0] if names else None
    return data
