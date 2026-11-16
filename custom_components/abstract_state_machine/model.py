"""A machine's definition: states, their rules, fallback, person. Pure, no HA imports.

A person or thing is a config entry. Its ``options`` hold where its
entities go and its machines::

    {
        "placement": "virtual_device",   # or "existing_device" (+ "device_id"), "none"
        "machines": [<machine>, ...],
    }

A machine looks like::

    {
        "id": "9b2e…",
        "name": "Mode",
        "states": [
            {
                "name": "running",
                "icon": "mdi:dishwasher",
                "rules": [
                    {
                        "id": "4f1c…",
                        "name": "Drawing power",
                        "kind": "trigger",
                        "from": ["cleared", "loaded"],
                        "triggers": [{"trigger": "numeric_state", …}],
                        "conditions": [],
                    },
                ],
            },
            ...
        ],
        "fallback": "cleared",
        "person": "person.alice",   # optional
        "max_flips": 10,            # rule-driven changes allowed per minute
        "state_sensors": false,     # a binary sensor per state
    }

Triggers and conditions are stored exactly as the automation editor sends
them (plain JSON). The runtime validates them when it sets them up.

Edits here keep keys they don't know about.
"""
from __future__ import annotations

import copy
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

# HA's own states. A select can't use them as options without confusion.
RESERVED_STATES = frozenset({"unknown", "unavailable"})
MAX_STATE_LENGTH = 255
MIN_STATES = 2

KIND_TRIGGER = "trigger"
KIND_CONDITION = "condition"
RULE_KINDS = (KIND_TRIGGER, KIND_CONDITION)

DEFAULT_MAX_FLIPS = 10
MAX_MAX_FLIPS = 100


class InvalidStates(ValueError):
    """The state names can't make a machine. ``key`` is a translation key."""

    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


@dataclass(frozen=True)
class RuleDef:
    """One way into a state.

    A trigger rule fires on ``triggers`` (if the machine is in one of
    ``from_states``, or any state when empty, and ``conditions`` pass). A
    condition rule matches while all its ``conditions`` are true.
    """

    id: str
    name: str
    kind: str
    from_states: tuple[str, ...] = ()
    triggers: tuple[dict[str, Any], ...] = ()
    conditions: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class StateDef:
    """One state of a machine."""

    name: str
    icon: str | None = None
    rules: tuple[RuleDef, ...] = field(default=())


@dataclass(frozen=True)
class MachineDef:
    """What a machine is, parsed from its dict in the entry's options."""

    name: str
    states: tuple[StateDef, ...]
    fallback: str
    person: str | None = None
    max_flips: int = DEFAULT_MAX_FLIPS
    id: str = ""
    state_sensors: bool = False

    @property
    def names(self) -> list[str]:
        return [state.name for state in self.states]

    def state(self, name: str) -> StateDef | None:
        return next((state for state in self.states if state.name == name), None)

    def rules(self) -> Iterable[tuple[StateDef, RuleDef]]:
        for state in self.states:
            for rule in state.rules:
                yield state, rule

    @property
    def has_condition_rules(self) -> bool:
        return any(rule.kind == KIND_CONDITION for _, rule in self.rules())


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


def new_machine_data(
    name: str, names: Iterable[str], fallback: str | None, person: str | None, machine_id: str | None = None
) -> dict[str, Any]:
    """A new machine. ``names`` must already be clean."""
    states = [{"name": state} for state in names]
    return _with_fallback({
        "id": machine_id or uuid.uuid4().hex,
        "name": name,
        "states": states,
        "fallback": fallback,
        "person": person or None,
    })


# A person or thing's machines ---------------------------------------------------


def machines_of(thing: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The machines in a person or thing's options, in order (ones without an id dropped)."""
    return [dict(m) for m in thing.get("machines") or [] if isinstance(m, Mapping) and m.get("id")]


def find_machine(thing: Mapping[str, Any], machine_id: str) -> dict[str, Any] | None:
    return next((m for m in machines_of(thing) if m["id"] == machine_id), None)


def with_machine(thing: Mapping[str, Any], machine: Mapping[str, Any]) -> dict[str, Any]:
    """Replace the machine with the same id, in place, or add it at the end."""
    machines = machines_of(thing)
    index = next((i for i, m in enumerate(machines) if m["id"] == machine["id"]), None)
    if index is None:
        machines.append(dict(machine))
    else:
        machines[index] = dict(machine)
    return {**thing, "machines": machines}


def without_machine(thing: Mapping[str, Any], machine_id: str) -> dict[str, Any]:
    return {**thing, "machines": [m for m in machines_of(thing) if m["id"] != machine_id]}


# Parsing -------------------------------------------------------------------


def parse(data: Mapping[str, Any]) -> MachineDef:
    """Read a machine from its dict. Tolerant: bad bits are dropped, never raise."""
    raw_states = [raw for raw in data.get("states") or [] if isinstance(raw, Mapping)]
    names: list[str] = []
    for raw in raw_states:
        name = str(raw.get("name", "")).strip()
        if name and name.casefold() not in {n.casefold() for n in names}:
            names.append(name)
    states: list[StateDef] = []
    for raw in raw_states:
        name = str(raw.get("name", "")).strip()
        if name not in names or any(state.name == name for state in states):
            continue
        rules = tuple(rule for r in raw.get("rules") or [] if (rule := _parse_rule(r, names)) is not None)
        states.append(StateDef(name=name, icon=raw.get("icon") or None, rules=rules))
    fallback = data.get("fallback")
    if fallback not in names:
        fallback = names[0] if names else ""
    return MachineDef(
        id=str(data.get("id") or ""),
        name=str(data.get("name") or "").strip() or "State machine",
        states=tuple(states),
        fallback=fallback,
        person=data.get("person") or None,
        max_flips=_max_flips(data.get("max_flips")),
        state_sensors=bool(data.get("state_sensors")),
    )


def _parse_rule(raw: Any, names: list[str]) -> RuleDef | None:
    if not isinstance(raw, Mapping) or raw.get("kind") not in RULE_KINDS or not raw.get("id"):
        return None
    triggers = tuple(t for t in raw.get("triggers") or [] if isinstance(t, Mapping))
    conditions = tuple(c for c in raw.get("conditions") or [] if isinstance(c, Mapping))
    kind = raw["kind"]
    if (kind == KIND_TRIGGER and not triggers) or (kind == KIND_CONDITION and not conditions):
        return None
    return RuleDef(
        id=str(raw["id"]),
        name=str(raw.get("name") or "").strip() or kind,
        kind=kind,
        from_states=tuple(s for s in raw.get("from") or [] if s in names) if kind == KIND_TRIGGER else (),
        triggers=tuple(dict(t) for t in triggers),
        conditions=tuple(dict(c) for c in conditions),
    )


def _max_flips(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return DEFAULT_MAX_FLIPS
    return min(max(number, 1), MAX_MAX_FLIPS)


# Edits ------------------------------------------------------------------------


def with_states(data: Mapping[str, Any], names: Iterable[str]) -> dict[str, Any]:
    """Replace the state list, in the given order. Kept states keep their icon and rules.

    ``names`` must already be clean. Matching is exact, so changing a name's
    case here makes a new state; use ``rename_state`` to keep its settings.
    Removed states drop out of every rule's ``from``.
    """
    names = list(names)
    old = {state["name"]: state for state in data.get("states") or [] if isinstance(state, Mapping)}
    states = [_map_from(dict(old.get(name, {"name": name})), lambda s: s if s in names else None) for name in names]
    return _with_fallback({**data, "states": states})


def rename_state(data: Mapping[str, Any], old: str, new: str) -> dict[str, Any]:
    """Rename one state, keeping its place, icon, rules, fallback role and ``from`` uses.

    Raise ``InvalidStates`` if the new name clashes or isn't allowed.
    """
    names = [state["name"] for state in data.get("states") or []]
    if old not in names:
        raise InvalidStates("unknown_state")
    renamed = clean_state_names([new if name == old else name for name in names])
    new = new.strip()
    states = [
        _map_from({**state, "name": renamed[i]}, lambda s: new if s == old else s)
        for i, state in enumerate(data.get("states") or [])
    ]
    fallback = data.get("fallback")
    return _with_fallback({**data, "states": states, "fallback": new if fallback == old else fallback})


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


def with_settings(
    data: Mapping[str, Any],
    name: str,
    fallback: str,
    person: str | None,
    max_flips: Any,
    state_sensors: bool = False,
) -> dict[str, Any]:
    return _with_fallback({
        **data,
        "name": name,
        "fallback": fallback,
        "person": person or None,
        "max_flips": _max_flips(max_flips),
        "state_sensors": bool(state_sensors),
    })


def copy_machine(data: Mapping[str, Any], name: str) -> dict[str, Any]:
    """A copy of a machine under a new name: new machine id, new rule ids, everything else the same."""
    new = copy.deepcopy(dict(data))
    new["id"] = uuid.uuid4().hex
    new["name"] = name
    for state in new.get("states") or []:
        for rule in state.get("rules") or []:
            rule["id"] = uuid.uuid4().hex
    return new


def upsert_rule(data: Mapping[str, Any], state_name: str, rule: Mapping[str, Any]) -> dict[str, Any]:
    """Add ``rule`` to ``state_name``, or replace the rule with the same id (wherever it is)."""
    names = [state["name"] for state in data.get("states") or []]
    if state_name not in names:
        raise InvalidStates("unknown_state")
    rule = {**rule, "from": [s for s in rule.get("from") or [] if s in names]}
    states = []
    for state in data.get("states") or []:
        rules = [r for r in state.get("rules") or [] if r.get("id") != rule["id"]]
        if state["name"] == state_name:
            old = next((i for i, r in enumerate(state.get("rules") or []) if r.get("id") == rule["id"]), None)
            if old is None:
                rules.append(dict(rule))
            else:
                rules.insert(old, dict(rule))
        states.append({**state, "rules": rules})
    return {**data, "states": states}


def remove_rule(data: Mapping[str, Any], rule_id: str) -> dict[str, Any]:
    states = [
        {**state, "rules": [r for r in state.get("rules") or [] if r.get("id") != rule_id]}
        for state in data.get("states") or []
    ]
    return {**data, "states": states}


def find_rule(data: Mapping[str, Any], rule_id: str) -> tuple[str, dict[str, Any]] | None:
    """The state a rule leads to, and the rule's raw dict."""
    for state in data.get("states") or []:
        for rule in state.get("rules") or []:
            if rule.get("id") == rule_id:
                return state["name"], dict(rule)
    return None


def _map_from(state: dict[str, Any], change: Any) -> dict[str, Any]:
    """Apply ``change`` to every name in the state's rules' ``from`` (``None`` drops it)."""
    if not state.get("rules"):
        return state
    rules = []
    for rule in state["rules"]:
        if rule.get("from"):
            rule = {**rule, "from": [new for s in rule["from"] if (new := change(s)) is not None]}
        rules.append(rule)
    return {**state, "rules": rules}


def _with_fallback(data: dict[str, Any]) -> dict[str, Any]:
    """Make sure the fallback is one of the states (the first, if not)."""
    names = [state["name"] for state in data["states"]]
    if data.get("fallback") not in names:
        data["fallback"] = names[0] if names else None
    return data


# Loops between machines -------------------------------------------------------------


def referenced_entities(config: Any) -> set[str]:
    """Every ``entity_id`` named anywhere in trigger or condition config (best effort: no templates)."""
    found: set[str] = set()
    stack = [config]
    while stack:
        item = stack.pop()
        if isinstance(item, Mapping):
            for key, value in item.items():
                if key == "entity_id":
                    if isinstance(value, str):
                        found.update(v.strip() for v in value.split(",") if v.strip())
                    elif isinstance(value, list):
                        found.update(v for v in value if isinstance(v, str))
                else:
                    stack.append(value)
        elif isinstance(item, list):
            stack.extend(item)
    return found


def find_loop(depends_on: Mapping[str, set[str]], start: str) -> list[str] | None:
    """A path ``start → … → start`` through other machines, or ``None``.

    ``depends_on[a]`` = the machines whose entities ``a``'s rules read.
    Self-references (time in own state) don't count.
    """
    path: list[str] = [start]
    seen: set[str] = set()

    def visit(node: str) -> list[str] | None:
        for nxt in sorted(depends_on.get(node, set())):
            if nxt == node:
                continue
            if nxt == start and len(path) > 1:
                return [*path, start]
            if nxt in seen or nxt == start:
                continue
            seen.add(nxt)
            path.append(nxt)
            if found := visit(nxt):
                return found
            path.pop()
        return None

    return visit(start)
