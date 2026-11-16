"""The select entity, its devices, set_state and restoring."""
from __future__ import annotations

import pytest
from homeassistant.core import Context, HomeAssistant, State
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry, mock_restore_cache

from custom_components.abstract_state_machine.const import DOMAIN, PLACEMENT_EXISTING, PLACEMENT_NONE

from .common import DISHWASHER_STATES, add_thing, machine

SELECT = "select.alice_time_day_type"


async def _alice(hass: HomeAssistant) -> MockConfigEntry:
    return await add_thing(
        hass,
        "Alice time",
        [
            machine("Day type", ["workday", "weekend", "holiday"], "weekend", "person.alice"),
            machine("Hour type", ["sleeping", "working", "free"], "free"),
        ],
    )


async def test_machines_are_entities_on_the_entrys_device(hass: HomeAssistant) -> None:
    entry = await _alice(hass)
    state = hass.states.get(SELECT)
    assert state is not None
    assert state.state == "weekend"
    assert state.attributes["options"] == ["workday", "weekend", "holiday"]
    assert state.attributes["reason"] == "initial"
    assert state.attributes["previous"] is None
    assert state.attributes["person"] == "person.alice"
    assert hass.states.get("select.alice_time_hour_type").state == "free"

    device = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, entry.entry_id), entry.entry_id)
    assert device is not None
    assert device.name == "Alice time"
    assert device.config_subentry_id is None  # no subentries at all
    entities = er.async_entries_for_device(er.async_get(hass), device.id)
    assert {e.entity_id for e in entities} == {SELECT, "select.alice_time_hour_type"}
    assert {e.config_subentry_id for e in entities} == {None}


async def test_each_entry_has_its_own_device(hass: HomeAssistant) -> None:
    alice = await add_thing(hass, "Alice", [machine("Day type", ["workday", "weekend"])])
    dishwasher = await add_thing(hass, "Dishwasher", [machine("Mode", DISHWASHER_STATES)])
    dev_reg = dr.async_get(hass)
    assert [d.name for d in dr.async_entries_for_config_entry(dev_reg, alice.entry_id)] == ["Alice"]
    assert [d.name for d in dr.async_entries_for_config_entry(dev_reg, dishwasher.entry_id)] == ["Dishwasher"]
    assert hass.states.get("select.alice_day_type") is not None
    assert hass.states.get("select.dishwasher_mode") is not None


async def test_picking_an_option_changes_state(hass: HomeAssistant) -> None:
    await _alice(hass)
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


async def test_no_event_entities(hass: HomeAssistant) -> None:
    await _alice(hass)
    assert hass.states.async_entity_ids("event") == []


async def test_set_state_service_records_reason(hass: HomeAssistant) -> None:
    await _alice(hass)
    await hass.services.async_call(
        DOMAIN, "set_state", {"entity_id": SELECT, "state": "workday", "reason": "NFC tag"}, blocking=True
    )
    state = hass.states.get(SELECT)
    assert state.state == "workday"
    assert state.attributes["reason"] == "NFC tag"


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
    entry = await add_thing(
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
    entry = await add_thing(hass, "House", [machine("House mode", ["home", "away", "sleeping"])], placement=PLACEMENT_NONE)
    reg = er.async_get(hass).async_get("select.house_mode")
    assert reg is not None
    assert reg.device_id is None
    assert dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id) == []


async def test_machine_named_like_its_device_isnt_doubled(hass: HomeAssistant) -> None:
    await add_thing(hass, "Dishwasher", [machine("Dishwasher", DISHWASHER_STATES)])
    assert hass.states.get("select.dishwasher").attributes["friendly_name"] == "Dishwasher"


async def test_state_icon(hass: HomeAssistant) -> None:
    m = machine("Mode", DISHWASHER_STATES)
    m["states"][0]["icon"] = "mdi:dishwasher-off"
    await add_thing(hass, "Dishwasher", [m])
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


async def test_state_sensors_follow_the_machine(hass: HomeAssistant) -> None:
    hour = machine("Hour type", ["sleeping", "working", "free"], "free")
    hour["state_sensors"] = True
    hour["states"][0]["icon"] = "mdi:sleep"
    await add_thing(hass, "Alice", [hour, machine("Day type", ["workday", "weekend"])])
    sleeping = "binary_sensor.alice_hour_type_sleeping"
    assert hass.states.get(sleeping).state == "off"
    assert hass.states.get(sleeping).attributes["icon"] == "mdi:sleep"
    assert hass.states.get("binary_sensor.alice_hour_type_free").state == "on"
    assert len(hass.states.async_entity_ids("binary_sensor")) == 3  # only the machine that asked for them

    await hass.services.async_call(
        "select", "select_option", {"entity_id": "select.alice_hour_type", "option": "sleeping"}, blocking=True
    )
    assert hass.states.get(sleeping).state == "on"
    assert hass.states.get("binary_sensor.alice_hour_type_free").state == "off"
    device = er.async_get(hass).async_get(sleeping).device_id
    assert device == er.async_get(hass).async_get("select.alice_hour_type").device_id


async def test_state_sensor_named_like_the_device(hass: HomeAssistant) -> None:
    dw = machine("Dishwasher", DISHWASHER_STATES)
    dw["state_sensors"] = True
    await add_thing(hass, "Dishwasher", [dw])
    assert hass.states.get("binary_sensor.dishwasher_clean").attributes["friendly_name"] == "Dishwasher clean"


async def test_device_triggers_come_from_ha(hass: HomeAssistant) -> None:
    """HA's select integration already offers "<machine> changed from … to …" as a device trigger."""
    from homeassistant.components.device_automation import DeviceAutomationType, async_get_device_automations
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "device_automation", {})
    entry = await add_thing(hass, "Dishwasher", [machine("Mode", DISHWASHER_STATES)])
    device = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, entry.entry_id), entry.entry_id)
    automations = await async_get_device_automations(hass, DeviceAutomationType.TRIGGER, [device.id])
    reg = er.async_get(hass).async_get("select.dishwasher_mode")
    assert any(t["type"] == "current_option_changed" and t["entity_id"] == reg.id for t in automations[device.id])
