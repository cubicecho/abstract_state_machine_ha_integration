"""The select and event entities, the bus event and set_state."""
from __future__ import annotations

import pytest
from homeassistant.core import Context, HomeAssistant, State
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events, mock_restore_cache

from custom_components.abstract_state_machine.const import (
    DOMAIN,
    EVENT_TRANSITION,
    PLACEMENT_EXISTING,
    PLACEMENT_NONE,
)

from .common import DISHWASHER_STATES, add_entry, machine

SELECT = "select.alice_time_day_type"
EVENT = "event.alice_time_day_type_transition"


async def _alice(hass: HomeAssistant) -> MockConfigEntry:
    return await add_entry(
        hass,
        "Alice time",
        [
            machine("Day type", ["workday", "weekend", "holiday"], "weekend", "person.alice"),
            machine("Hour type", ["sleeping", "working", "free"], "free"),
        ],
    )


async def test_machines_are_entities_on_one_virtual_device(hass: HomeAssistant) -> None:
    entry = await _alice(hass)
    state = hass.states.get(SELECT)
    assert state is not None
    assert state.state == "weekend"
    assert state.attributes["options"] == ["workday", "weekend", "holiday"]
    assert state.attributes["reason"] == "initial"
    assert state.attributes["previous"] is None
    assert state.attributes["person"] == "person.alice"
    assert hass.states.get("select.alice_time_hour_type").state == "free"
    assert hass.states.get(EVENT).state == "unknown"  # no change yet

    device = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, entry.entry_id), entry.entry_id)
    assert device is not None
    assert device.name == "Alice time"
    entities = er.async_entries_for_device(er.async_get(hass), device.id)
    assert {e.entity_id for e in entities} == {
        SELECT, EVENT, "select.alice_time_hour_type", "event.alice_time_hour_type_transition",
    }
    assert {e.config_subentry_id for e in entities} == set(entry.subentries)


async def test_picking_an_option_changes_state_and_fires_events(hass: HomeAssistant) -> None:
    await _alice(hass)
    bus = async_capture_events(hass, EVENT_TRANSITION)
    context = Context()
    await hass.services.async_call(
        "select", "select_option", {"entity_id": SELECT, "option": "holiday"}, blocking=True, context=context
    )
    state = hass.states.get(SELECT)
    assert state.state == "holiday"
    assert state.attributes["previous"] == "weekend"
    assert state.attributes["reason"] == "manual"
    assert state.attributes["manual"] is True
    assert state.context.id == context.id

    event = hass.states.get(EVENT)
    assert event.attributes["event_type"] == "holiday"
    assert event.attributes["from"] == "weekend"
    assert event.attributes["to"] == "holiday"
    assert event.attributes["event_types"] == ["workday", "weekend", "holiday"]

    assert len(bus) == 1
    assert bus[0].data == {
        "entity_id": SELECT, "machine": "Day type", "from": "weekend", "to": "holiday",
        "reason": "manual", "manual": True,
    }
    assert bus[0].context.id == context.id


async def test_same_state_fires_nothing(hass: HomeAssistant) -> None:
    await _alice(hass)
    bus = async_capture_events(hass, EVENT_TRANSITION)
    await hass.services.async_call("select", "select_option", {"entity_id": SELECT, "option": "weekend"}, blocking=True)
    assert bus == []
    assert hass.states.get(EVENT).state == "unknown"


async def test_set_state_service_records_reason(hass: HomeAssistant) -> None:
    await _alice(hass)
    await hass.services.async_call(
        DOMAIN, "set_state", {"entity_id": SELECT, "state": "workday", "reason": "NFC tag"}, blocking=True
    )
    state = hass.states.get(SELECT)
    assert state.state == "workday"
    assert state.attributes["reason"] == "NFC tag"
    assert hass.states.get(EVENT).attributes["reason"] == "NFC tag"


async def test_set_state_unknown_state(hass: HomeAssistant) -> None:
    await _alice(hass)
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(DOMAIN, "set_state", {"entity_id": SELECT, "state": "vacation"}, blocking=True)
    assert err.value.translation_key == "unknown_state"


async def test_restores_state_after_restart(hass: HomeAssistant) -> None:
    mock_restore_cache(
        hass,
        [State(SELECT, "holiday", {"previous": "workday", "reason": "manual", "changed_at": "2026-11-14T08:00:00+00:00"})],
    )
    await _alice(hass)
    state = hass.states.get(SELECT)
    assert state.state == "holiday"
    assert state.attributes["previous"] == "workday"
    assert state.attributes["reason"] == "restored"
    assert state.attributes["changed_at"] == "2026-11-14T08:00:00+00:00"


async def test_restored_state_that_was_removed_falls_back(hass: HomeAssistant) -> None:
    mock_restore_cache(hass, [State(SELECT, "vacation", {})])
    await _alice(hass)
    assert hass.states.get(SELECT).state == "weekend"


async def test_existing_device(hass: HomeAssistant) -> None:
    plug_entry = MockConfigEntry(domain="test")
    plug_entry.add_to_hass(hass)
    plug = dr.async_get(hass).async_get_or_create(
        config_entry_id=plug_entry.entry_id, identifiers={("test", "plug")}, name="Dishwasher plug"
    )
    entry = await add_entry(
        hass, "Dishwasher plug", [machine("Mode", DISHWASHER_STATES)], placement=PLACEMENT_EXISTING, device_id=plug.id
    )
    reg = er.async_get(hass).async_get("select.dishwasher_plug_mode")
    assert reg is not None
    assert reg.device_id == plug.id
    assert hass.states.get("select.dishwasher_plug_mode").state == "cleared"
    # We didn't make a device of our own, or join the plug's config entry.
    assert dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id) == []
    assert dr.async_get(hass).async_get(plug.id).config_entry_id == plug_entry.entry_id


async def test_no_device(hass: HomeAssistant) -> None:
    entry = await add_entry(hass, "House", [machine("House mode", ["home", "away", "sleeping"])], placement=PLACEMENT_NONE)
    reg = er.async_get(hass).async_get("select.house_mode")
    assert reg is not None
    assert reg.device_id is None
    assert hass.states.get("event.house_mode_transition") is not None
    assert dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id) == []


async def test_machine_named_like_its_device_isnt_doubled(hass: HomeAssistant) -> None:
    await add_entry(hass, "Dishwasher", [machine("Dishwasher", DISHWASHER_STATES)])
    assert hass.states.get("select.dishwasher").attributes["friendly_name"] == "Dishwasher"
    assert hass.states.get("event.dishwasher_transition").attributes["friendly_name"] == "Dishwasher Transition"


async def test_state_icon(hass: HomeAssistant) -> None:
    sub = machine("Mode", DISHWASHER_STATES)
    sub["data"]["states"][0]["icon"] = "mdi:dishwasher-off"
    await add_entry(hass, "Dishwasher", [sub])
    assert hass.states.get("select.dishwasher_mode").attributes["icon"] == "mdi:dishwasher-off"
    await hass.services.async_call(
        "select", "select_option", {"entity_id": "select.dishwasher_mode", "option": "running"}, blocking=True
    )
    assert "icon" not in hass.states.get("select.dishwasher_mode").attributes


async def test_unload(hass: HomeAssistant) -> None:
    entry = await _alice(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(SELECT).state == "unavailable"
