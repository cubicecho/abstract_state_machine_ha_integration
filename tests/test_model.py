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
    data = model.new_machine_data(["a", "b"], None, None)
    assert data == {"states": [{"name": "a"}, {"name": "b"}], "fallback": "a", "person": None}
    assert model.new_machine_data(["a", "b"], "b", "person.alice")["fallback"] == "b"


def test_parse_is_tolerant() -> None:
    machine = model.parse(
        "Odd",
        {"states": [{"name": "a", "icon": "mdi:a"}, {"name": "A"}, {"nope": 1}, "junk", {"name": " b "}], "fallback": "zz"},
    )
    assert machine.names == ["a", "b"]
    assert machine.fallback == "a"
    assert machine.state("a") == model.StateDef("a", "mdi:a")
    assert machine.person is None


def test_with_states_keeps_settings_of_kept_states() -> None:
    data = {
        "states": [{"name": "a", "icon": "mdi:a", "rules": [1]}, {"name": "b"}],
        "fallback": "b",
        "person": "person.alice",
    }
    new = model.with_states(data, ["c", "a"])
    assert new["states"] == [{"name": "c"}, {"name": "a", "icon": "mdi:a", "rules": [1]}]
    assert new["fallback"] == "c"  # b is gone
    assert new["person"] == "person.alice"


def test_rename_state_keeps_place_icon_rules_and_fallback() -> None:
    data = {"states": [{"name": "a"}, {"name": "b", "icon": "mdi:b", "rules": [1]}], "fallback": "b"}
    new = model.rename_state(data, "b", " busy ")
    assert new["states"] == [{"name": "a"}, {"name": "busy", "icon": "mdi:b", "rules": [1]}]
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
