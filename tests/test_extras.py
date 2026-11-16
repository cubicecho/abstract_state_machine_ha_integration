"""Rule waits, the activity-log reason, broken-rule repairs, time-in-state sensors."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    async_capture_events,
    async_fire_time_changed,
    mock_restore_cache_with_extra_data,
)

from custom_components.abstract_state_machine import logbook, model
from custom_components.abstract_state_machine.const import DOMAIN, EVENT_RULE_APPLIED
from custom_components.abstract_state_machine.timeinstate import TimeInState

from .common import add_thing, machine

NOBODY = "input_boolean.nobody_home"
HOUSE = "select.house_mode"


def _local(hour: int, minute: int = 0, day: int = 16) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=dt_util.get_default_time_zone())


async def _later(hass: HomeAssistant, freezer: FrozenDateTimeFactory, **delta: float) -> None:
    freezer.tick(timedelta(**delta))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def _set(hass: HomeAssistant, entity_id: str, state: str) -> None:
    hass.states.async_set(entity_id, state)
    await hass.async_block_till_done()


def _house(wait: Any = None) -> dict[str, Any]:
    house = machine("Mode", ["away", "home"], "home")
    rule: dict[str, Any] = {"id": "away", "name": "Nobody home", "kind": "condition",
                            "conditions": [{"condition": "state", "entity_id": NOBODY, "state": "on"}]}
    if wait is not None:
        rule["for"] = wait
    house["states"][0]["rules"] = [rule]
    return house


# 1. Waiting before switching ---------------------------------------------------------


async def test_condition_rule_waits(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    hass.states.async_set(NOBODY, "off")
    await add_thing(hass, "House", [_house({"minutes": 10})])
    await _set(hass, NOBODY, "on")
    assert hass.states.get(HOUSE).state == "home"  # not yet
    response = await hass.services.async_call(DOMAIN, "explain", {"entity_id": HOUSE}, blocking=True, return_response=True)
    (rule,) = response[HOUSE]["rules"]
    assert (rule["matches"], rule["for_seconds"]) == (False, 600)
    assert "waiting_until" in rule

    await _later(hass, freezer, minutes=9)
    assert hass.states.get(HOUSE).state == "home"
    await _later(hass, freezer, minutes=2)
    state = hass.states.get(HOUSE)
    assert (state.state, state.attributes["reason"]) == ("away", "Nobody home")

    await _set(hass, NOBODY, "off")  # coming back: immediate
    assert hass.states.get(HOUSE).state == "home"


async def test_a_blip_restarts_the_wait(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    hass.states.async_set(NOBODY, "off")
    await add_thing(hass, "House", [_house("00:10:00")])
    await _set(hass, NOBODY, "on")
    await _later(hass, freezer, minutes=8)
    await _set(hass, NOBODY, "off")  # phone back on Wi-Fi for a moment
    await _set(hass, NOBODY, "on")
    await _later(hass, freezer, minutes=8)
    assert hass.states.get(HOUSE).state == "home"
    await _later(hass, freezer, minutes=3)
    assert hass.states.get(HOUSE).state == "away"


async def test_a_wait_doesnt_drop_the_state_on_restart(hass: HomeAssistant) -> None:
    hass.states.async_set(NOBODY, "on")
    stored = {"state": "away", "previous": "home", "reason": "Nobody home",
              "changed_at": "2026-11-14T08:00:00+00:00", "manual": False, "held": False}
    mock_restore_cache_with_extra_data(hass, [(State(HOUSE, "away"), stored)])
    await add_thing(hass, "House", [_house({"minutes": 10})])
    assert hass.states.get(HOUSE).state == "away"


def test_duration_seconds() -> None:
    assert model.duration_seconds({"hours": 1, "minutes": 2, "seconds": 3}) == 3723
    assert model.duration_seconds({"days": 1}) == 86400
    assert model.duration_seconds("00:10:00") == 600
    assert model.duration_seconds("10:00") == 600  # MM:SS
    assert model.duration_seconds(90) == 90
    for nothing in (None, "", "soon", {"minutes": "x"}, [], -5):
        assert model.duration_seconds(nothing) == 0
    rule = {"id": "r", "name": "r", "kind": "condition", "conditions": [{"condition": "x"}], "for": {"minutes": 5}}
    parsed = model.parse({"states": [{"name": "a", "rules": [rule]}, {"name": "b"}]})
    assert parsed.states[0].rules[0].for_seconds == 300


# 6. The reason in the activity log -----------------------------------------------------


async def test_rule_changes_say_why(hass: HomeAssistant) -> None:
    hass.states.async_set(NOBODY, "off")
    await add_thing(hass, "House", [_house()])
    events = async_capture_events(hass, EVENT_RULE_APPLIED)
    await _set(hass, NOBODY, "on")
    (event,) = events
    assert event.data == {"entity_id": HOUSE, "machine": "Mode", "reason": "Nobody home", "to": "away"}
    # The state change shares the event's context, so the log links them.
    assert hass.states.get(HOUSE).context.id == event.context.id

    # Picking by hand: the log already says who did it, no event.
    await hass.services.async_call("select", "select_option", {"entity_id": HOUSE, "option": "home"}, blocking=True)
    assert len(events) == 1
    # set_state with a reason does say why.
    await hass.services.async_call(DOMAIN, "set_state", {"entity_id": HOUSE, "state": "away", "reason": "NFC tag"},
                                   blocking=True)
    assert events[-1].data["reason"] == "NFC tag"


def test_logbook_describes_the_event() -> None:
    described: dict[str, Any] = {}

    def collect(domain: str, event_type: str, describe: Any) -> None:
        described[event_type] = (domain, describe)

    logbook.async_describe_events(None, collect)  # type: ignore[arg-type]
    domain, describe = described[EVENT_RULE_APPLIED]

    event = SimpleNamespace(data={"entity_id": "select.alice_day_type", "machine": "Day type", "reason": "PTO today",
                                  "to": "holiday"})
    assert domain == DOMAIN
    assert describe(event) == {
        "name": "Day type", "message": "moved to holiday (PTO today)", "source": "PTO today",
        "entity_id": "select.alice_day_type", "icon": "mdi:state-machine",
    }


# 7. Broken rules -----------------------------------------------------------------------


def _broken_issue(hass: HomeAssistant, machine_id: str = "m_mode") -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(DOMAIN, f"broken_rules_{machine_id}")


async def test_rule_reading_a_missing_entity_raises_an_issue(hass: HomeAssistant) -> None:
    m = machine("Mode", ["a", "b"])
    m["states"][1]["rules"] = [
        {"id": "r1", "name": "Power", "kind": "trigger", "triggers": [{"trigger": "state", "entity_id": "sensor.gone"}]},
        {"id": "r2", "name": "Broken", "kind": "trigger", "triggers": [{"trigger": "no_such_trigger"}]},
    ]
    await add_thing(hass, "Thing", [m])
    issue = _broken_issue(hass)
    assert issue is not None
    problems = issue.translation_placeholders["problems"]
    assert "**Power** (into b): reads sensor.gone" in problems
    assert "**Broken** (into b):" in problems


async def test_issue_clears_when_the_entity_appears(hass: HomeAssistant) -> None:
    m = machine("Mode", ["a", "b"])
    m["states"][1]["rules"] = [
        {"id": "r1", "name": "Power", "kind": "trigger", "triggers": [{"trigger": "state", "entity_id": "sensor.plug"}]},
    ]
    await add_thing(hass, "Thing", [m])
    assert _broken_issue(hass) is not None
    er.async_get(hass).async_get_or_create("sensor", "test", "plug", suggested_object_id="plug")
    await hass.async_block_till_done()
    assert _broken_issue(hass) is None


async def test_no_issue_when_all_is_well(hass: HomeAssistant) -> None:
    hass.states.async_set(NOBODY, "off")
    await add_thing(hass, "House", [_house()])
    assert _broken_issue(hass, "m_mode") is None


async def test_issue_of_a_removed_machine_goes(hass: HomeAssistant) -> None:
    m = machine("Mode", ["a", "b"])
    m["states"][1]["rules"] = [
        {"id": "r1", "name": "Power", "kind": "trigger", "triggers": [{"trigger": "state", "entity_id": "sensor.gone"}]},
    ]
    entry = await add_thing(hass, "Thing", [m, machine("Other", ["x", "y"])])
    assert _broken_issue(hass) is not None
    hass.config_entries.async_update_entry(entry, options=model.without_machine(entry.options, "m_mode"))
    await hass.async_block_till_done()
    assert _broken_issue(hass) is None


# 9. Time in each state today --------------------------------------------------------------


def test_time_in_state() -> None:
    t = TimeInState.start("free", _local(10))
    assert t.seconds("free", _local(11)) == 3600
    t = t.switch("working", _local(11))
    t = t.switch("free", _local(11, 30))
    assert t.seconds("working", _local(12)) == 1800
    assert t.seconds("free", _local(12)) == 3600 + 1800
    assert t.switch("free", _local(12)) == t.roll(_local(12))  # same state: nothing to close
    # Midnight: a new day, counting the current state from midnight.
    tomorrow = _local(1, day=17)
    assert t.seconds("free", tomorrow) == 3600
    assert t.seconds("working", tomorrow) == 0
    assert t.roll(tomorrow).day == date(2026, 11, 17)
    # Restart: totals kept, the current state counts again from now.
    resumed = TimeInState.from_dict(t.as_dict()).resumed(_local(14))
    assert resumed.seconds("working", _local(14)) == 1800
    assert resumed.seconds("free", _local(15)) == 3600 + 3600  # 11:30–14:00 was "down": not counted
    assert TimeInState.from_dict({"nope": 1}) is None


async def test_time_sensors(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    freezer.move_to(_local(10))
    hour = machine("Hour type", ["sleeping", "working", "free"], "free")
    hour["time_sensors"] = True
    await add_thing(hass, "Alice", [hour])
    free, working = "sensor.alice_hour_type_free_today", "sensor.alice_hour_type_working_today"
    state = hass.states.get(free)
    assert state.state == "0.0"
    assert (state.attributes["unit_of_measurement"], state.attributes["device_class"]) == ("h", "duration")

    await _later(hass, freezer, hours=1)
    assert float(hass.states.get(free).state) == 1.0
    await hass.services.async_call(
        "select", "select_option", {"entity_id": "select.alice_hour_type", "option": "working"}, blocking=True
    )
    await _later(hass, freezer, minutes=30)
    assert float(hass.states.get(working).state) == 0.5
    assert float(hass.states.get(free).state) == 1.0

    freezer.move_to(_local(0, 30, day=17))  # past midnight
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert float(hass.states.get(working).state) == 0.5  # since midnight
    assert float(hass.states.get(free).state) == 0.0


async def test_time_sensors_restore(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    freezer.move_to(_local(12))
    stored = {
        "state": "working", "previous": "free", "reason": "manual", "changed_at": "2026-11-16T15:00:00+00:00",
        "manual": True, "held": False,
        "time": {"day": "2026-11-16", "state": "working", "since": _local(9).isoformat(), "totals": {"free": 3600}},
    }
    mock_restore_cache_with_extra_data(hass, [(State("select.alice_hour_type", "working"), stored)])
    hour = machine("Hour type", ["sleeping", "working", "free"], "free")
    hour["time_sensors"] = True
    await add_thing(hass, "Alice", [hour])
    assert float(hass.states.get("sensor.alice_hour_type_free_today").state) == 1.0
    # The stretch before the restart was cut off at the restart; it counts again from now.
    await _later(hass, freezer, minutes=30)
    assert float(hass.states.get("sensor.alice_hour_type_working_today").state) == 0.5
