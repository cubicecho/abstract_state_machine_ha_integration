"""Rules running inside HA: triggers, conditions, holds, flip limit, restore."""
from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed,
    mock_restore_cache_with_extra_data,
)

from custom_components.abstract_state_machine import model
from custom_components.abstract_state_machine.const import DOMAIN

from .common import add_thing, machine

POWER = "sensor.dishwasher_power"
DOOR = "binary_sensor.dishwasher_door"
DW = "select.dishwasher_mode"
DAY = "select.alice_time_day_type"


def _trigger_rule(rule_id: str, name: str, triggers: list[dict[str, Any]], from_states: list[str] | None = None,
                  conditions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"id": rule_id, "name": name, "kind": "trigger", "from": from_states or [], "triggers": triggers,
            "conditions": conditions or []}


def _condition_rule(rule_id: str, name: str, conditions: list[dict[str, Any]]) -> dict[str, Any]:
    return {"id": rule_id, "name": name, "kind": "condition", "conditions": conditions}


def _with_rules(data: dict[str, Any], rules: dict[str, list[dict[str, Any]]], **extra: Any) -> dict[str, Any]:
    for state, state_rules in rules.items():
        for rule in state_rules:
            data = model.upsert_rule(data, state, rule)
    return {**data, **extra}


def _dishwasher(**extra: Any) -> Any:
    return _with_rules(
        machine("Mode", ["cleared", "loaded", "running", "clean"], "cleared"),
        {
            "running": [_trigger_rule("go", "Drawing power", [
                {"trigger": "numeric_state", "entity_id": [POWER], "above": 10, "for": {"minutes": 2}},
            ])],
            "clean": [_trigger_rule("done", "Power dropped", [
                {"trigger": "numeric_state", "entity_id": [POWER], "below": 3},
            ], from_states=["running"])],
            "cleared": [_trigger_rule("door", "Door opened", [
                {"trigger": "state", "entity_id": DOOR, "to": "on"},
            ], from_states=["clean"], conditions=[{"condition": "state", "entity_id": POWER, "state": "0"}])],
        },
        **extra,
    )


def _day_type() -> Any:
    def on(entity: str) -> list[dict[str, Any]]:
        return [{"condition": "state", "entity_id": entity, "state": "on"}]

    return _with_rules(
        machine("Day type", ["vacation", "holiday", "workday", "weekend"], "weekend"),
        {
            "vacation": [_condition_rule("pto", "PTO", on("input_boolean.pto"))],
            "holiday": [_condition_rule("public", "Public holiday", on("calendar.holidays"))],
            "workday": [_condition_rule("work", "Workday sensor", on("binary_sensor.workday"))],
        },
    )


async def _later(hass: HomeAssistant, freezer: FrozenDateTimeFactory | None = None, **delta: float) -> None:
    """Move time on: the clock too when ``freezer`` is given, else only HA's timers."""
    if freezer is not None:
        freezer.tick(timedelta(**delta))
        async_fire_time_changed(hass)
    else:
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(**delta))
    await hass.async_block_till_done()


async def _set(hass: HomeAssistant, entity_id: str, state: str) -> None:
    hass.states.async_set(entity_id, state)
    await hass.async_block_till_done()


# Trigger rules ---------------------------------------------------------------


async def test_dishwasher_cycle(hass: HomeAssistant) -> None:
    hass.states.async_set(POWER, "0")
    hass.states.async_set(DOOR, "off")
    await add_thing(hass, "Dishwasher", [_dishwasher()])
    assert hass.states.get(DW).state == "cleared"

    await _set(hass, POWER, "2")
    await _set(hass, POWER, "1500")
    assert hass.states.get(DW).state == "cleared"  # needs 2 minutes above 10 W
    await _later(hass, minutes=3)
    state = hass.states.get(DW)
    assert (state.state, state.attributes["reason"], state.attributes["manual"]) == ("running", "Drawing power", False)
    assert "rules_state" not in state.attributes  # no condition rules

    await _set(hass, POWER, "0")
    state = hass.states.get(DW)
    assert (state.state, state.attributes["reason"], state.attributes["previous"]) == ("clean", "Power dropped", "running")

    await _set(hass, DOOR, "on")
    state = hass.states.get(DW)
    assert (state.state, state.attributes["reason"]) == ("cleared", "Door opened")


async def test_trigger_rule_from_and_conditions(hass: HomeAssistant) -> None:
    hass.states.async_set(POWER, "0")
    hass.states.async_set(DOOR, "off")
    await add_thing(hass, "Dishwasher", [_dishwasher()])
    # Power drop while cleared: "clean" only comes from running.
    await _set(hass, POWER, "5")
    await _set(hass, POWER, "1")
    assert hass.states.get(DW).state == "cleared"
    # Door rule: from clean, and only if power is 0.
    await hass.services.async_call("select", "select_option", {"entity_id": DW, "option": "clean"}, blocking=True)
    await _set(hass, POWER, "1")
    await _set(hass, DOOR, "on")
    assert hass.states.get(DW).state == "clean"


async def test_invalid_rule_is_skipped(hass: HomeAssistant, caplog: pytest.LogCaptureFixture) -> None:
    sub = _with_rules(machine("Mode", ["a", "b"]), {"b": [
        _trigger_rule("bad", "Broken", [{"trigger": "no_such_trigger"}]),
        _trigger_rule("ok", "Works", [{"trigger": "state", "entity_id": DOOR, "to": "on"}]),
    ]})
    await add_thing(hass, "Thing", [sub])
    assert 'rule "Broken" (into b) is invalid' in caplog.text
    await _set(hass, DOOR, "on")
    assert hass.states.get("select.thing_mode").state == "b"


# Condition rules -------------------------------------------------------------


async def test_condition_rules_first_state_from_the_top_wins(hass: HomeAssistant) -> None:
    hass.states.async_set("binary_sensor.workday", "on")
    await add_thing(hass, "Alice time", [_day_type()])
    state = hass.states.get(DAY)
    assert (state.state, state.attributes["reason"], state.attributes["rules_state"]) == (
        "workday", "Workday sensor", "workday"
    )
    assert state.attributes["override"] is False

    await _set(hass, "calendar.holidays", "on")
    assert hass.states.get(DAY).state == "holiday"
    await _set(hass, "input_boolean.pto", "on")
    assert hass.states.get(DAY).attributes["reason"] == "PTO"
    for entity in ("input_boolean.pto", "calendar.holidays", "binary_sensor.workday"):
        hass.states.async_set(entity, "off")
    await hass.async_block_till_done()
    state = hass.states.get(DAY)
    assert (state.state, state.attributes["reason"]) == ("weekend", "fallback")


async def test_time_conditions_are_polled(hass: HomeAssistant) -> None:
    template = "{{ states('sensor.hour') | int(0) >= 9 }}"
    sub = _with_rules(machine("Hour type", ["working", "free"], "free"),
                      {"working": [_condition_rule("t", "Work hours", [{"condition": "template", "value_template": template}])]})
    hass.states.async_set("sensor.hour", "8")
    await add_thing(hass, "Alice time", [sub])
    assert hass.states.get("select.alice_time_hour_type").state == "free"
    hass.states.async_set("sensor.hour", "9")
    await _later(hass, minutes=1)
    assert hass.states.get("select.alice_time_hour_type").state == "working"


# Holds ------------------------------------------------------------------------


async def test_manual_holds_until_the_rules_change(hass: HomeAssistant) -> None:
    hass.states.async_set("binary_sensor.workday", "on")
    await add_thing(hass, "Alice time", [_day_type()])
    await hass.services.async_call("select", "select_option", {"entity_id": DAY, "option": "holiday"}, blocking=True)
    state = hass.states.get(DAY)
    assert (state.state, state.attributes["override"], state.attributes["rules_state"]) == ("holiday", True, "workday")

    await _later(hass, minutes=5)  # rules still say workday
    assert hass.states.get(DAY).state == "holiday"
    await _set(hass, "binary_sensor.workday", "off")  # the rules change their answer
    state = hass.states.get(DAY)
    assert (state.state, state.attributes["override"], state.attributes["reason"]) == ("weekend", False, "fallback")


async def test_hold_for_a_duration_and_clear_override(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    hass.states.async_set("binary_sensor.workday", "on")
    await add_thing(hass, "Alice time", [_day_type()])
    await hass.services.async_call(
        DOMAIN, "set_state", {"entity_id": DAY, "state": "vacation", "hold": {"hours": 1}}, blocking=True
    )
    assert hass.states.get(DAY).attributes["override_until"] is not None
    await _later(hass, freezer, minutes=30)
    assert hass.states.get(DAY).state == "vacation"
    await _later(hass, freezer, minutes=31)
    assert hass.states.get(DAY).state == "workday"

    await hass.services.async_call("select", "select_option", {"entity_id": DAY, "option": "holiday"}, blocking=True)
    await hass.services.async_call(DOMAIN, "clear_override", {"entity_id": DAY}, blocking=True)
    state = hass.states.get(DAY)
    assert (state.state, state.attributes["override"]) == ("workday", False)


async def test_trigger_rule_holds_against_condition_rules(hass: HomeAssistant) -> None:
    sub = _with_rules(_day_type(), {"holiday": [
        _trigger_rule("snow", "Snow day", [{"trigger": "state", "entity_id": "input_boolean.snow", "to": "on"}]),
    ]})
    hass.states.async_set("binary_sensor.workday", "on")
    await add_thing(hass, "Alice time", [sub])
    await _set(hass, "input_boolean.snow", "on")
    state = hass.states.get(DAY)
    assert (state.state, state.attributes["reason"], state.attributes["override"]) == ("holiday", "Snow day", True)
    await _later(hass, minutes=1)
    assert hass.states.get(DAY).state == "holiday"
    await _set(hass, "binary_sensor.workday", "off")
    assert hass.states.get(DAY).state == "weekend"


async def test_hold_survives_a_restart(hass: HomeAssistant) -> None:
    hass.states.async_set("binary_sensor.workday", "on")
    stored = {
        "state": "holiday", "previous": "workday", "reason": "manual", "changed_at": "2026-11-14T08:00:00+00:00",
        "manual": True, "held": True, "held_against": "workday", "hold_until": None,
    }
    mock_restore_cache_with_extra_data(hass, [(State(DAY, "holiday"), stored)])
    await add_thing(hass, "Alice time", [_day_type()])
    state = hass.states.get(DAY)
    assert (state.state, state.attributes["override"], state.attributes["reason"]) == ("holiday", True, "restored")


async def test_hold_released_on_restart_when_the_rules_changed_meanwhile(hass: HomeAssistant) -> None:
    hass.states.async_set("binary_sensor.workday", "off")
    stored = {
        "state": "holiday", "previous": "workday", "reason": "manual", "changed_at": "2026-11-14T08:00:00+00:00",
        "manual": True, "held": True, "held_against": "workday", "hold_until": None,
    }
    mock_restore_cache_with_extra_data(hass, [(State(DAY, "holiday"), stored)])
    await add_thing(hass, "Alice time", [_day_type()])
    assert hass.states.get(DAY).state == "weekend"


# Flip limit ---------------------------------------------------------------------


async def test_flip_limit(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    sub = _with_rules(machine("Mode", ["a", "b"]), {
        "a": [_trigger_rule("to_a", "Off", [{"trigger": "state", "entity_id": DOOR, "to": "off"}])],
        "b": [_trigger_rule("to_b", "On", [{"trigger": "state", "entity_id": DOOR, "to": "on"}])],
    }, max_flips=3)
    hass.states.async_set(DOOR, "off")
    await add_thing(hass, "Thing", [sub])
    for value in ("on", "off", "on", "off", "on"):
        await _set(hass, DOOR, value)
    state = hass.states.get("select.thing_mode")
    assert state.state == "b"  # a → b → a → b, then stopped
    assert state.attributes["flip_limited"] is True
    assert ir.async_get(hass).async_get_issue(DOMAIN, "flip_limit_m_mode") is not None

    await _later(hass, freezer, minutes=2)
    await _set(hass, DOOR, "off")
    state = hass.states.get("select.thing_mode")
    assert state.state == "a"
    assert "flip_limited" not in state.attributes


async def test_flip_limit_stops_a_loop_between_machines(hass: HomeAssistant) -> None:
    # A follows B; B is the opposite of A. They'd flip forever.
    a = _with_rules(machine("A", ["x", "y"], "x"), {
        "y": [_condition_rule("a", "B is y", [{"condition": "state", "entity_id": "select.loop_b", "state": "y"}])],
    }, max_flips=5)
    b = _with_rules(machine("B", ["x", "y"], "x"), {
        "y": [_condition_rule("b", "A is x", [{"condition": "state", "entity_id": "select.loop_a", "state": "x"}])],
    }, max_flips=5)
    await add_thing(hass, "Loop", [a, b])
    # Settled (the loop stopped), and one of them hit its limit.
    states = (hass.states.get("select.loop_a"), hass.states.get("select.loop_b"))
    assert any(s.attributes.get("flip_limited") for s in states)


# Composition ----------------------------------------------------------------------


async def test_house_mode_reads_everyones_hour_type(hass: HomeAssistant) -> None:
    """The README's house mode: other entries' machines as inputs, and a trigger rule that holds."""
    for person in ("Alice", "Bob"):
        await add_thing(hass, person, [machine("Hour type", ["sleeping", "working", "free"], "free", machine_id=person)])

    def sleeping(person: str) -> dict[str, Any]:
        return {"condition": "state", "entity_id": f"select.{person}_hour_type", "state": "sleeping"}

    house = _with_rules(machine("Mode", ["sleeping", "wake_up", "home"], "home"), {
        "sleeping": [_condition_rule("s", "Everyone asleep", [sleeping("alice"), sleeping("bob")])],
        "wake_up": [_trigger_rule("w", "Alarm", [{"trigger": "state", "entity_id": "input_boolean.alarm", "to": "on"}],
                                  from_states=["sleeping"])],
    })
    await add_thing(hass, "House", [house])
    mode = "select.house_mode"
    assert hass.states.get(mode).state == "home"

    for person in ("alice", "bob"):
        await hass.services.async_call(
            "select", "select_option", {"entity_id": f"select.{person}_hour_type", "option": "sleeping"}, blocking=True
        )
    await hass.async_block_till_done()
    assert hass.states.get(mode).state == "sleeping"

    await _set(hass, "input_boolean.alarm", "on")
    state = hass.states.get(mode)
    assert (state.state, state.attributes["override"], state.attributes["rules_state"]) == ("wake_up", True, "sleeping")

    await hass.services.async_call(
        "select", "select_option", {"entity_id": "select.alice_hour_type", "option": "free"}, blocking=True
    )
    await hass.async_block_till_done()
    state = hass.states.get(mode)
    assert (state.state, state.attributes["override"]) == ("home", False)
