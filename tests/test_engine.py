"""engine.py: starting, condition rules, trigger rules, manual changes and holds."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from custom_components.abstract_state_machine import engine, model

T0 = datetime(2026, 11, 15, 8, 0, tzinfo=UTC)
T1 = T0 + timedelta(hours=1)
T2 = T0 + timedelta(hours=2)

DISHWASHER = model.parse(model.new_machine_data("Dishwasher", ["cleared", "loaded", "running", "clean"], "cleared", None))


def _cond(rule_id: str, name: str = "") -> dict:
    return {"id": rule_id, "name": name or rule_id, "kind": "condition", "conditions": [{"condition": "x"}]}


def _trig(rule_id: str, from_states: list[str] | None = None) -> dict:
    return {"id": rule_id, "name": rule_id, "kind": "trigger", "from": from_states or [], "triggers": [{"trigger": "x"}]}


DAY = model.parse(
    {
        "name": "Day type",
        "states": [
            {"name": "vacation", "rules": [_cond("pto", "PTO")]},
            {"name": "holiday", "rules": [_cond("public", "Public holiday"), _cond("company", "Company holiday")]},
            {"name": "workday", "rules": [_cond("workday", "Workday sensor")]},
            {"name": "weekend"},
        ],
        "fallback": "weekend",
    },
)
RUNNING = model.RuleDef("go", "Drawing power", "trigger", (), ({"trigger": "x"},))
CLEAN = model.RuleDef("done", "Power dropped", "trigger", ("running",), ({"trigger": "x"},))


def _status(state: str, **kw) -> engine.Status:
    return engine.Status(state, kw.pop("previous", None), kw.pop("reason", "initial"), kw.pop("at", T0), **kw)


# Starting ---------------------------------------------------------------------


def test_initial_without_restore_is_fallback() -> None:
    assert engine.initial(DISHWASHER, None, T1) == engine.Status("cleared", None, engine.REASON_INITIAL, T1)


def test_initial_restores_known_state() -> None:
    restored = engine.Status("running", "loaded", "manual", T0, True)
    assert engine.initial(DISHWASHER, restored, T1) == engine.Status("running", "loaded", "restored", T0, True)


def test_initial_with_removed_state_falls_back() -> None:
    assert engine.initial(DISHWASHER, engine.Status("drying", "running", "manual", T0), T1).state == "cleared"


def test_initial_drops_removed_previous() -> None:
    assert engine.initial(DISHWASHER, engine.Status("clean", "drying", "manual", T0), T1).previous is None


def test_initial_keeps_a_hold_only_with_condition_rules() -> None:
    held = engine.Status("holiday", None, "manual", T0, True, True, "workday", T2)
    assert engine.initial(DAY, held, T1).held_against == "workday"
    assert engine.initial(DAY, held, T1).hold_until == T2
    held = engine.Status("running", None, "manual", T0, True, True, "clean")
    assert engine.initial(DISHWASHER, held, T1).held is False


# Condition rules ----------------------------------------------------------------


def test_compute_first_state_from_the_top_wins() -> None:
    assert engine.compute(DAY, {"workday": True, "company": True}) == engine.Computed("holiday", "Company holiday")
    assert engine.compute(DAY, {"workday": True, "pto": True}) == engine.Computed("vacation", "PTO")
    assert engine.compute(DAY, {"workday": True}) == engine.Computed("workday", "Workday sensor")


def test_compute_falls_back() -> None:
    assert engine.compute(DAY, {}) == engine.Computed("weekend", engine.REASON_FALLBACK)


def test_compute_none_without_condition_rules() -> None:
    assert engine.compute(DISHWASHER, {}) is None


def test_apply_computed_moves() -> None:
    new, transition = engine.apply_computed(_status("weekend"), engine.Computed("workday", "Workday sensor"), T1)
    assert new == engine.Status("workday", "weekend", "Workday sensor", T1)
    assert transition == engine.Transition("weekend", "workday", "Workday sensor", False, T1)


def test_apply_computed_same_state_updates_reason_only() -> None:
    status = _status("holiday", reason="Public holiday")
    new, transition = engine.apply_computed(status, engine.Computed("holiday", "Company holiday"), T1)
    assert transition is None
    assert new.reason == "Company holiday"
    assert new.changed_at == T0
    assert engine.apply_computed(new, engine.Computed("holiday", "Company holiday"), T2) is None


def test_hold_stays_until_rules_change() -> None:
    held = engine.Status("holiday", "workday", "manual", T0, True, True, "workday")
    assert engine.apply_computed(held, engine.Computed("workday", "Workday sensor"), T1) is None
    new, transition = engine.apply_computed(held, engine.Computed("weekend", "fallback"), T1)
    assert new == engine.Status("weekend", "holiday", "fallback", T1)
    assert transition is not None


def test_hold_released_into_the_same_state_has_no_transition() -> None:
    held = engine.Status("weekend", "workday", "manual", T0, True, True, "workday")
    new, transition = engine.apply_computed(held, engine.Computed("weekend", "fallback"), T1)
    assert transition is None
    assert (new.state, new.held, new.manual, new.reason) == ("weekend", False, False, "fallback")


def test_hold_until_expires() -> None:
    held = engine.Status("holiday", "workday", "manual", T0, True, True, "workday", T1)
    assert engine.apply_computed(held, engine.Computed("workday", "w"), T0 + timedelta(minutes=30)) is None
    new, transition = engine.apply_computed(held, engine.Computed("workday", "w"), T1)
    assert new.state == "workday" and transition is not None


# Manual -----------------------------------------------------------------------------


def test_set_manual_without_condition_rules() -> None:
    new, transition = engine.set_manual(DISHWASHER, _status("cleared"), "loaded", None, T1, reason="NFC tag") or (None, None)
    assert new == engine.Status("loaded", "cleared", "NFC tag", T1, True)
    assert transition == engine.Transition("cleared", "loaded", "NFC tag", True, T1)


def test_set_manual_to_current_is_a_no_op() -> None:
    assert engine.set_manual(DISHWASHER, _status("cleared", reason="manual", manual=True), "cleared", None, T1) is None


def test_set_manual_unknown() -> None:
    with pytest.raises(engine.UnknownState):
        engine.set_manual(DISHWASHER, _status("cleared"), "drying", None, T1)


def test_set_manual_holds_against_condition_rules() -> None:
    new, _ = engine.set_manual(DAY, _status("workday"), "holiday", engine.Computed("workday", "w"), T1, hold_until=T2)
    assert (new.held, new.held_against, new.hold_until) == (True, "workday", T2)


def test_set_manual_no_hold_when_rules_agree() -> None:
    new, _ = engine.set_manual(DAY, _status("weekend"), "workday", engine.Computed("workday", "w"), T1, hold_until=T2)
    assert (new.held, new.hold_until) == (False, None)


def test_set_manual_pins_the_current_state() -> None:
    """Picking the current state when the rules already say otherwise pins it there."""
    status = _status("workday", reason="Workday sensor")
    new, transition = engine.set_manual(DAY, status, "workday", engine.Computed("weekend", "fallback"), T1)
    assert transition is None
    assert (new.held, new.held_against, new.manual) == (True, "weekend", True)


def test_clear_hold() -> None:
    held = engine.Status("holiday", None, "manual", T0, True, True, "workday", T2)
    assert engine.clear_hold(held) == engine.Status("holiday", None, "manual", T0, True)


# Trigger rules ------------------------------------------------------------------


def test_fire_trigger_moves() -> None:
    new, transition = engine.fire_trigger(_status("loaded"), "running", RUNNING, None, T1) or (None, None)
    assert new == engine.Status("running", "loaded", "Drawing power", T1)
    assert transition == engine.Transition("loaded", "running", "Drawing power", False, T1)


def test_fire_trigger_respects_from() -> None:
    assert engine.fire_trigger(_status("cleared"), "clean", CLEAN, None, T1) is None
    assert engine.fire_trigger(_status("running"), "clean", CLEAN, None, T1) is not None


def test_fire_trigger_into_current_state_is_a_no_op() -> None:
    assert engine.fire_trigger(_status("running"), "running", RUNNING, None, T1) is None


def test_fire_trigger_holds_against_condition_rules() -> None:
    new, _ = engine.fire_trigger(_status("workday"), "holiday", RUNNING, engine.Computed("workday", "w"), T1)
    assert (new.held, new.held_against, new.hold_until, new.manual) == (True, "workday", None, False)
