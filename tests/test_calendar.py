"""The ``abstract_state_machine.calendar_event`` condition."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers import condition as condition_helper
from homeassistant.helpers import config_validation as cv
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockModule,
    MockPlatform,
    async_fire_time_changed,
    mock_integration,
    mock_platform,
)

from custom_components.abstract_state_machine.condition import event_matches

from .common import add_thing, machine, machine_data

NOW = datetime(2026, 11, 16, 10, 0)  # a Monday, local time (US/Pacific in tests)


def _local(hour: int, day: int = 16) -> datetime:
    return datetime(2026, 11, day, hour, 0, tzinfo=dt_util.get_default_time_zone())


def _all_day(summary: str, day: int, description: str | None = None) -> CalendarEvent:
    return CalendarEvent(start=date(2026, 11, day), end=date(2026, 11, day + 1), summary=summary, description=description)


def _timed(summary: str, start: datetime, end: datetime) -> CalendarEvent:
    return CalendarEvent(start=start, end=end, summary=summary)


class FakeCalendar(CalendarEntity):
    def __init__(self, name: str, events: list[CalendarEvent]) -> None:
        self._attr_name = name
        self._attr_unique_id = name
        self.events = events

    @property
    def event(self) -> CalendarEvent | None:
        return None

    async def async_get_events(self, hass: HomeAssistant, start: datetime, end: datetime) -> list[CalendarEvent]:
        return [e for e in self.events if e.start_datetime_local < end and e.end_datetime_local > start]


async def _calendar(hass: HomeAssistant, events: list[CalendarEvent]) -> FakeCalendar:
    calendar = FakeCalendar("Alice", events)

    async def setup_platform(hass: HomeAssistant, config: Any, add: Any, discovery_info: Any = None) -> None:
        add([calendar])

    mock_integration(hass, MockModule("fake_calendar"))
    mock_platform(hass, "fake_calendar.calendar", MockPlatform(async_setup_platform=setup_platform))
    assert await async_setup_component(hass, "calendar", {"calendar": {"platform": "fake_calendar"}})
    await hass.async_block_till_done()
    assert hass.states.get("calendar.alice") is not None
    return calendar


def _pto(when: str = "today", **options: Any) -> dict[str, Any]:
    return {
        "condition": "abstract_state_machine.calendar_event",
        "target": {"entity_id": "calendar.alice"},
        "options": {"when": when, "title_contains": "PTO", **options},
    }


# Matching ----------------------------------------------------------------------


def test_event_matches_when() -> None:
    now = _local(10)
    meeting = _timed("Standup", _local(9), _local(11))
    later = _timed("Dentist", _local(15), _local(16))
    pto_tomorrow = _all_day("PTO", 17)
    assert event_matches(meeting, now, "now")
    assert not event_matches(later, now, "now")
    assert event_matches(later, now, "today")
    assert not event_matches(pto_tomorrow, now, "today")
    assert event_matches(pto_tomorrow, now, "tomorrow")
    assert not event_matches(meeting, now, "tomorrow")
    # An all-day event runs midnight to midnight.
    assert event_matches(_all_day("PTO", 16), _local(0), "now")
    assert not event_matches(_all_day("PTO", 15), _local(0), "now")


def test_event_matches_text_and_kind() -> None:
    now = _local(10)
    pto = _all_day("Alice PTO", 16)
    off = _all_day("Day off", 16, description="Counts as pto")
    assert event_matches(pto, now, "today", contains="pto")  # case doesn't matter
    assert not event_matches(off, now, "today", contains="pto")
    assert event_matches(off, now, "today", contains="pto", search_description=True)
    assert event_matches(pto, now, "today", kind="all_day")
    assert not event_matches(pto, now, "today", kind="timed")
    assert event_matches(_timed("PTO", _local(13), _local(17)), now, "today", contains="PTO", kind="timed")


# As a condition --------------------------------------------------------------------


async def _checker(hass: HomeAssistant, config: dict[str, Any]) -> Any:
    (validated,) = await condition_helper.async_validate_conditions_config(hass, cv.CONDITIONS_SCHEMA([config]))
    return await condition_helper.async_from_config(hass, validated)


async def test_condition(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    freezer.move_to(_local(10))
    await add_thing(hass, "Alice", [machine("Day type", ["a", "b"])])  # loads our condition platform
    calendar = await _calendar(hass, [_all_day("PTO", 17), _timed("Standup", _local(9), _local(11))])

    today, tomorrow = await _checker(hass, _pto("today")), await _checker(hass, _pto("tomorrow"))
    any_now = await _checker(hass, {**_pto("now"), "options": {"when": "now"}})
    assert not today.async_check()
    assert tomorrow.async_check()
    assert tomorrow.needs_polling
    assert any_now.async_check()  # any event now

    # Events are re-read every 15 minutes; a change tells whoever tracks the condition.
    calls: list[None] = []
    await tomorrow.async_track_changes(lambda: calls.append(None))
    calendar.events = []
    freezer.tick(timedelta(minutes=16))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert not tomorrow.async_check()
    assert calls
    for checker in (today, tomorrow, any_now):
        checker.async_unload()


async def test_missing_calendar_is_false(hass: HomeAssistant) -> None:
    await add_thing(hass, "Alice", [machine("Day type", ["a", "b"])])
    checker = await _checker(hass, _pto("today"))
    assert not checker.async_check()
    checker.async_unload()


async def test_invalid_options(hass: HomeAssistant) -> None:
    with pytest.raises(Exception):  # noqa: B017 - probatio.Invalid or HomeAssistantError
        await condition_helper.async_validate_conditions_config(
            hass, cv.CONDITIONS_SCHEMA([{**_pto(), "options": {"when": "yesterday"}}])
        )


# In a machine --------------------------------------------------------------------


def _day_type() -> dict[str, Any]:
    day = machine("Day type", ["vacation", "workday", "weekend"], "weekend")
    day["states"][0]["rules"] = [{"id": "pto", "name": "PTO today", "kind": "condition", "conditions": [_pto("today")]}]
    return day


async def test_day_type_follows_the_calendar(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    freezer.move_to(_local(10))
    calendar = await _calendar(hass, [_all_day("Alice PTO", 16)])
    await add_thing(hass, "Alice", [_day_type()])
    state = hass.states.get("select.alice_day_type")
    assert (state.state, state.attributes["reason"]) == ("vacation", "PTO today")

    calendar.events = []  # the PTO was cancelled
    freezer.tick(timedelta(minutes=16))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("select.alice_day_type").state == "weekend"


async def test_saved_through_the_rule_form(hass: HomeAssistant) -> None:
    await _calendar(hass, [])
    entry = await add_thing(hass, "Alice", [machine("Day type", ["vacation", "weekend"], "weekend")])
    result = await hass.config_entries.options.async_init(entry.entry_id)
    flow_id = result["flow_id"]
    await hass.config_entries.options.async_configure(flow_id, {"next_step_id": "edit_machine"})
    await hass.config_entries.options.async_configure(flow_id, {"next_step_id": "rules"})
    await hass.config_entries.options.async_configure(flow_id, {"state": "vacation"})
    await hass.config_entries.options.async_configure(flow_id, {"action": "add_condition"})
    result = await hass.config_entries.options.async_configure(flow_id, {"name": "PTO today", "conditions": [_pto()]})
    assert result["step_id"] == "rules_list", result.get("errors")
    (rule,) = machine_data(entry)["states"][0]["rules"]
    assert rule["conditions"] == [_pto()]


async def test_shows_in_the_condition_picker(hass: HomeAssistant) -> None:
    await add_thing(hass, "Alice", [machine("Day type", ["a", "b"])])
    descriptions = await condition_helper.async_get_all_descriptions(hass)
    ours = descriptions["abstract_state_machine.calendar_event"]
    assert ours["target"] == {"entity": [{"domain": ["calendar"]}]}
    assert set(ours["fields"]) == {"when", "title_contains", "search_description", "event_kind"}
