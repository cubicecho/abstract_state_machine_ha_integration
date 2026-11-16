"""model.py: state names and edits."""
from __future__ import annotations

import pytest

from custom_components.abstract_state_machine import model


def test_clean_state_names_trims_and_drops_blanks() -> None:
    assert model.clean_state_names([" workday ", "", "  ", "weekend"]) == ["workday", "weekend"]


@pytest.mark.parametrize(
    ("names", "key"),
    [
        (["only"], "too_few_states"),
        ([], "too_few_states"),
        (["Running", "running"], "duplicate_state"),
        (["on", "Unavailable"], "reserved_state"),
        (["ok", "x" * 256], "state_too_long"),
    ],
)
def test_clean_state_names_rejects(names: list[str], key: str) -> None:
    with pytest.raises(model.InvalidStates) as err:
        model.clean_state_names(names)
    assert err.value.key == key


def test_new_machine_fallback_defaults_to_first() -> None:
    data = model.new_machine_data("M", ["a", "b"], None, None, machine_id="m1")
    assert data == {"id": "m1", "name": "M", "states": [{"name": "a"}, {"name": "b"}], "fallback": "a", "person": None}
    assert model.new_machine_data("M", ["a", "b"], "b", "person.alice")["fallback"] == "b"
    assert len(model.new_machine_data("M", ["a", "b"], None, None)["id"]) == 32  # a fresh uuid


def test_machines_of_a_thing() -> None:
    a, b = model.new_machine_data("A", ["x", "y"], None, None, "a"), model.new_machine_data("B", ["x", "y"], None, None, "b")
    thing = {"placement": "none", "machines": [a, {"name": "no id"}, "junk"]}
    assert model.machines_of(thing) == [a]
    thing = model.with_machine(thing, b)
    assert [m["id"] for m in thing["machines"]] == ["a", "b"]
    assert thing["placement"] == "none"
    thing = model.with_machine(thing, {**a, "name": "A2"})  # replaced in place
    assert [m["name"] for m in thing["machines"]] == ["A2", "B"]
    assert model.find_machine(thing, "b") == b
    assert model.find_machine(thing, "zz") is None
    assert model.machines_of(model.without_machine(thing, "a")) == [b]


def test_parse_is_tolerant() -> None:
    machine = model.parse(
        {"states": [{"name": "a", "icon": "mdi:a"}, {"name": "A"}, {"nope": 1}, "junk", {"name": " b "}], "fallback": "zz"},
    )
    assert machine.name == "State machine"  # no name stored
    assert machine.names == ["a", "b"]
    assert machine.fallback == "a"
    assert machine.state("a") == model.StateDef("a", "mdi:a")
    assert machine.person is None


def _trig(rule_id: str, from_states: list[str]) -> dict:
    return {"id": rule_id, "name": rule_id, "kind": "trigger", "from": from_states, "triggers": [{"trigger": "x"}]}


def test_with_states_keeps_settings_of_kept_states() -> None:
    data = {
        "states": [{"name": "a", "icon": "mdi:a", "rules": [_trig("r", ["b", "c"])]}, {"name": "b"}],
        "fallback": "b",
        "person": "person.alice",
    }
    new = model.with_states(data, ["c", "a"])
    assert new["states"] == [{"name": "c"}, {"name": "a", "icon": "mdi:a", "rules": [_trig("r", ["c"])]}]
    assert new["fallback"] == "c"  # b is gone
    assert new["person"] == "person.alice"


def test_rename_state_keeps_place_icon_rules_and_fallback() -> None:
    data = {"states": [{"name": "a", "rules": [_trig("r", ["b"])]}, {"name": "b", "icon": "mdi:b"}], "fallback": "b"}
    new = model.rename_state(data, "b", " busy ")
    assert new["states"] == [{"name": "a", "rules": [_trig("r", ["busy"])]}, {"name": "busy", "icon": "mdi:b"}]
    assert new["fallback"] == "busy"


def test_rename_state_rejects_clash_and_unknown() -> None:
    data = {"states": [{"name": "a"}, {"name": "b"}], "fallback": "a"}
    with pytest.raises(model.InvalidStates, match="duplicate_state"):
        model.rename_state(data, "b", "A")
    with pytest.raises(model.InvalidStates, match="unknown_state"):
        model.rename_state(data, "zz", "c")


def test_rename_state_can_change_case() -> None:
    data = {"states": [{"name": "a"}, {"name": "b"}], "fallback": "a"}
    assert model.rename_state(data, "a", "A")["states"][0] == {"name": "A"}


def test_with_icons_sets_and_clears() -> None:
    data = {"states": [{"name": "a", "icon": "mdi:a"}, {"name": "b"}], "fallback": "a"}
    new = model.with_icons(data, {"a": None, "b": "mdi:b"})
    assert new["states"] == [{"name": "a"}, {"name": "b", "icon": "mdi:b"}]


def test_parse_rules() -> None:
    machine = model.parse(
        {
            "name": "Dishwasher",
            "states": [
                {"name": "running", "rules": [
                    _trig("go", ["cleared", "gone"]),
                    {"id": "bad", "kind": "trigger", "triggers": []},  # no triggers: dropped
                    {"kind": "condition", "conditions": [{"condition": "x"}]},  # no id: dropped
                    {"id": "c", "name": " ", "kind": "condition", "conditions": [{"condition": "x"}], "from": ["cleared"]},
                ]},
                {"name": "cleared"},
            ],
            "max_flips": "500",
        },
    )
    (state, _) = machine.states
    assert [r.id for r in state.rules] == ["go", "c"]
    assert state.rules[0].from_states == ("cleared",)  # unknown state dropped
    assert state.rules[1].name == "condition"  # blank name → kind
    assert state.rules[1].from_states == ()  # condition rules have no from
    assert machine.has_condition_rules
    assert machine.max_flips == model.MAX_MAX_FLIPS


@pytest.mark.parametrize(("value", "expected"), [(None, 10), ("x", 10), (0, 1), (5, 5), (1000, 100)])
def test_max_flips_clamped(value: object, expected: int) -> None:
    assert model.parse({"states": [{"name": "a"}, {"name": "b"}], "max_flips": value}).max_flips == expected


def test_upsert_find_and_remove_rule() -> None:
    data = model.new_machine_data("M", ["a", "b", "c"], "a", None)
    data = model.upsert_rule(data, "b", _trig("r1", ["a", "zz"]))
    data = model.upsert_rule(data, "b", _trig("r2", []))
    assert [r["id"] for r in data["states"][1]["rules"]] == ["r1", "r2"]
    assert data["states"][1]["rules"][0]["from"] == ["a"]
    # Replacing keeps the rule's place.
    data = model.upsert_rule(data, "b", {**_trig("r1", ["c"]), "name": "renamed"})
    assert [r["name"] for r in data["states"][1]["rules"]] == ["renamed", "r2"]
    assert model.find_rule(data, "r1") == ("b", {**_trig("r1", ["c"]), "name": "renamed"})
    data = model.remove_rule(data, "r1")
    assert model.find_rule(data, "r1") is None
    with pytest.raises(model.InvalidStates):
        model.upsert_rule(data, "zz", _trig("r3", []))


def test_with_settings() -> None:
    data = model.with_settings(model.new_machine_data("M", ["a", "b"], "a", "person.x"), "N", "b", "", 7)
    assert (data["name"], data["fallback"], data["person"], data["max_flips"]) == ("N", "b", None, 7)


def test_referenced_entities() -> None:
    config = [
        {"rules": [{"triggers": [{"trigger": "state", "entity_id": ["sensor.a", "sensor.b"]}]}]},
        {"conditions": [{"condition": "and", "conditions": [{"condition": "state", "entity_id": "select.x, select.y"}]}]},
    ]
    assert model.referenced_entities(config) == {"sensor.a", "sensor.b", "select.x", "select.y"}


def test_find_loop() -> None:
    assert model.find_loop({"a": {"b"}, "b": {"c"}, "c": {"a"}}, "a") == ["a", "b", "c", "a"]
    assert model.find_loop({"a": {"a", "b"}, "b": set()}, "a") is None  # self-reference is fine
    assert model.find_loop({"a": {"b"}, "b": {"c"}, "c": {"b"}}, "a") is None  # a loop, but not through a
