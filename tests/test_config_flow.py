"""The config flow (a person or thing + first machine) and Configure (the options flow)."""
from __future__ import annotations

from typing import Any

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.abstract_state_machine.const import (
    DOMAIN,
    PLACEMENT_EXISTING,
    PLACEMENT_NONE,
    PLACEMENT_VIRTUAL,
)

from .common import DISHWASHER_STATES, add_thing, machine, machine_data

# Adding -----------------------------------------------------------------------------


async def _add(
    hass: HomeAssistant, placement: str, first: dict[str, Any], name: str, states: list[str], fallback: str
) -> dict[str, Any]:
    """Add integration: placement menu, name or device, first machine, fallback."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.MENU
    flow_id = result["flow_id"]
    result = await hass.config_entries.flow.async_configure(flow_id, {"next_step_id": placement})
    assert result["step_id"] == placement
    result = await hass.config_entries.flow.async_configure(flow_id, first)
    assert result["step_id"] == "machine"
    result = await hass.config_entries.flow.async_configure(flow_id, {"name": name, "states": states})
    assert result["step_id"] == "fallback"
    result = await hass.config_entries.flow.async_configure(flow_id, {"fallback": fallback})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    return result


async def test_virtual_device_then_first_machine(hass: HomeAssistant) -> None:
    result = await _add(hass, PLACEMENT_VIRTUAL, {"name": " Alice time "}, "Day type", ["workday", "weekend"], "weekend")
    assert result["title"] == "Alice time"
    entry = result["result"]
    assert entry.data == {}
    assert entry.options["placement"] == PLACEMENT_VIRTUAL
    (day,) = entry.options["machines"]
    assert (day["name"], day["fallback"]) == ("Day type", "weekend")
    assert hass.states.get("select.alice_time_day_type").state == "weekend"
    device = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, entry.entry_id), entry.entry_id)
    assert device is not None and device.name == "Alice time"


async def test_several_entries(hass: HomeAssistant) -> None:
    await _add(hass, PLACEMENT_VIRTUAL, {"name": "Alice"}, "Day type", ["workday", "weekend"], "weekend")
    await _add(hass, PLACEMENT_NONE, {"name": "House"}, "House mode", ["home", "away"], "home")
    assert len(hass.config_entries.async_entries(DOMAIN)) == 2
    assert er.async_get(hass).async_get("select.house_mode").device_id is None
    assert hass.states.get("select.alice_day_type") is not None


async def test_existing_device_flow(hass: HomeAssistant) -> None:
    plug_entry = MockConfigEntry(domain="test")
    plug_entry.add_to_hass(hass)
    plug = dr.async_get(hass).async_get_or_create(
        config_entry_id=plug_entry.entry_id, identifiers={("test", "plug")}, name="Dishwasher plug"
    )
    result = await _add(hass, PLACEMENT_EXISTING, {"device_id": plug.id}, "Mode", DISHWASHER_STATES, "cleared")
    assert result["title"] == "Dishwasher plug"
    assert result["result"].options["device_id"] == plug.id
    assert er.async_get(hass).async_get("select.dishwasher_plug_mode").device_id == plug.id


async def test_machine_form_errors(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    flow_id = result["flow_id"]
    await hass.config_entries.flow.async_configure(flow_id, {"next_step_id": PLACEMENT_VIRTUAL})
    result = await hass.config_entries.flow.async_configure(flow_id, {"name": "  "})
    assert result["errors"] == {"name": "name_required"}
    result = await hass.config_entries.flow.async_configure(flow_id, {"name": "Alice"})
    assert result["description_placeholders"] == {"thing": "Alice"}
    for states, error in (
        (["one"], "too_few_states"),
        (["Busy", "busy"], "duplicate_state"),
        (["unknown", "x"], "reserved_state"),
    ):
        result = await hass.config_entries.flow.async_configure(flow_id, {"name": "Day type", "states": states})
        assert result["errors"] == {"states": error}
    result = await hass.config_entries.flow.async_configure(flow_id, {"name": "  ", "states": ["a", "b"]})
    assert result["errors"] == {"name": "name_required"}


# Configure (the options flow) --------------------------------------------------------------


async def _reconfigure(hass: HomeAssistant, entry: MockConfigEntry, choice: str) -> dict[str, Any]:
    """Configure → ``choice``."""
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    return await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": choice})


async def _edit(hass: HomeAssistant, entry: MockConfigEntry, choice: str, machine_id: str | None = None) -> dict[str, Any]:
    """Configure → Edit a state machine (→ pick it) → ``choice``."""
    result = await _reconfigure(hass, entry, "edit_machine")
    if machine_id is not None:
        assert result["step_id"] == "edit_machine"
        result = await hass.config_entries.options.async_configure(result["flow_id"], {"machine": machine_id})
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "machine_menu"
    return await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": choice})


async def test_reconfigure_menu(hass: HomeAssistant) -> None:
    entry = await add_thing(hass, "Alice", [machine("Day type", ["a", "b"])])
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["menu_options"] == ["edit_machine", "add_machine", "copy_machine", "placement"]  # no removing the only one
    assert result["description_placeholders"] == {"thing": "Alice", "machines": "Day type"}


async def test_add_a_machine(hass: HomeAssistant) -> None:
    entry = await add_thing(hass, "Alice time", [machine("Day type", ["workday", "weekend"])])
    result = await _reconfigure(hass, entry, "add_machine")
    assert result["step_id"] == "machine"
    flow_id = result["flow_id"]
    result = await hass.config_entries.options.async_configure(flow_id, {"name": "day TYPE", "states": ["a", "b"]})
    assert result["errors"] == {"name": "duplicate_machine"}
    result = await hass.config_entries.options.async_configure(
        flow_id, {"name": "Hour type", "states": ["sleeping", "working", "free"], "person": "person.alice"}
    )
    result = await hass.config_entries.options.async_configure(flow_id, {"fallback": "free"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    state = hass.states.get("select.alice_time_hour_type")
    assert state.state == "free"
    assert state.attributes["person"] == "person.alice"
    assert hass.states.get("select.alice_time_day_type").state == "workday"


async def test_edit_picks_a_machine_when_there_are_several(hass: HomeAssistant) -> None:
    entry = await add_thing(hass, "Alice", [machine("Day type", ["a", "b"]), machine("Hour type", ["x", "y"])])
    result = await _edit(hass, entry, "edit_states", machine_id="m_hour_type")
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"states": ["x", "y", "z"]})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert [s["name"] for s in machine_data(entry, "Hour type")["states"]] == ["x", "y", "z"]
    assert [s["name"] for s in machine_data(entry, "Day type")["states"]] == ["a", "b"]


async def test_remove_a_machine(hass: HomeAssistant) -> None:
    entry = await add_thing(hass, "Alice", [machine("Day type", ["a", "b"]), machine("Hour type", ["x", "y"])])
    result = await _reconfigure(hass, entry, "remove_machine")
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"machine": "m_hour_type"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    ent_reg = er.async_get(hass)
    assert ent_reg.async_get("select.alice_hour_type") is None
    assert hass.states.get("select.alice_hour_type") is None
    assert ent_reg.async_get("select.alice_day_type") is not None


async def test_edit_states(hass: HomeAssistant) -> None:
    entry = await add_thing(hass, "Dishwasher", [machine("Mode", DISHWASHER_STATES)])
    result = await _edit(hass, entry, "edit_states")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"states": ["loaded", "running", "clean", "cleared", "drying"]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    state = hass.states.get("select.dishwasher_mode")
    assert state.attributes["options"] == ["loaded", "running", "clean", "cleared", "drying"]
    assert state.state == "cleared"  # still there, so kept


async def test_edit_states_error(hass: HomeAssistant) -> None:
    entry = await add_thing(hass, "Dishwasher", [machine("Mode", DISHWASHER_STATES)])
    result = await _edit(hass, entry, "edit_states")
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"states": ["a"]})
    assert result["errors"] == {"states": "too_few_states"}


async def test_rename_keeps_the_current_state(hass: HomeAssistant) -> None:
    entry = await add_thing(hass, "Dishwasher", [machine("Mode", DISHWASHER_STATES)])
    await hass.services.async_call(
        "select", "select_option", {"entity_id": "select.dishwasher_mode", "option": "running"}, blocking=True
    )
    result = await _edit(hass, entry, "rename_state")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"state": "running", "new_name": "washing"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    state = hass.states.get("select.dishwasher_mode")
    assert state.state == "washing"
    assert state.attributes["previous"] == "cleared"
    assert state.attributes["options"] == ["cleared", "loaded", "washing", "clean"]


async def test_rename_clash(hass: HomeAssistant) -> None:
    entry = await add_thing(hass, "Dishwasher", [machine("Mode", DISHWASHER_STATES)])
    result = await _edit(hass, entry, "rename_state")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"state": "running", "new_name": "Clean"}
    )
    assert result["errors"] == {"new_name": "duplicate_state"}


async def test_settings(hass: HomeAssistant) -> None:
    entry = await add_thing(hass, "Dishwasher", [machine("Mode", DISHWASHER_STATES, person="person.alice")])
    result = await _edit(hass, entry, "settings")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"name": "Cycle", "fallback": "clean", "max_flips": 4}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    data = machine_data(entry)
    assert (data["name"], data["fallback"], data["person"], data["max_flips"]) == ("Cycle", "clean", None, 4)
    # The entity id stays; the friendly name follows the new name.
    assert hass.states.get("select.dishwasher_mode").attributes["friendly_name"] == "Dishwasher Cycle"


async def test_settings_duplicate_name(hass: HomeAssistant) -> None:
    entry = await add_thing(hass, "Alice", [machine("Day type", ["a", "b"]), machine("Hour type", ["x", "y"])])
    result = await _edit(hass, entry, "settings", machine_id="m_hour_type")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"name": "Day type", "fallback": "x", "max_flips": 10}
    )
    assert result["errors"] == {"name": "duplicate_machine"}


async def test_icons(hass: HomeAssistant) -> None:
    entry = await add_thing(hass, "Dishwasher", [machine("Mode", DISHWASHER_STATES)])
    result = await _edit(hass, entry, "icons")
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"cleared": "mdi:dishwasher-off"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert hass.states.get("select.dishwasher_mode").attributes["icon"] == "mdi:dishwasher-off"


# Name and device ---------------------------------------------------------------------


async def test_move_from_virtual_device_to_existing_and_back_to_none(hass: HomeAssistant) -> None:
    entry = await add_thing(hass, "Dishwasher", [machine("Mode", DISHWASHER_STATES)])
    dev_reg, ent_reg = dr.async_get(hass), er.async_get(hass)
    virtual = dev_reg.async_get_device_by_identifier((DOMAIN, entry.entry_id), entry.entry_id)
    assert virtual is not None
    await hass.services.async_call(
        "select", "select_option", {"entity_id": "select.dishwasher_mode", "option": "running"}, blocking=True
    )

    plug_entry = MockConfigEntry(domain="test")
    plug_entry.add_to_hass(hass)
    plug = dev_reg.async_get_or_create(config_entry_id=plug_entry.entry_id, identifiers={("test", "plug")}, name="Plug")

    result = await _reconfigure(hass, entry, "placement")
    flow_id = result["flow_id"]
    result = await hass.config_entries.options.async_configure(
        flow_id, {"name": "Dishwasher", "placement": PLACEMENT_EXISTING}
    )
    assert result["errors"] == {"device_id": "device_required"}
    result = await hass.config_entries.options.async_configure(
        flow_id, {"name": "Dishwasher", "placement": PLACEMENT_EXISTING, "device_id": plug.id}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    reg = ent_reg.async_get("select.dishwasher_mode")
    assert reg is not None and reg.device_id == plug.id
    assert dev_reg.async_get(virtual.id) is None  # our virtual device is gone
    assert hass.states.get("select.dishwasher_mode").state == "running"  # state kept

    result = await _reconfigure(hass, entry, "placement")
    await hass.config_entries.options.async_configure(
        result["flow_id"], {"name": "Dishwasher", "placement": PLACEMENT_NONE}
    )
    await hass.async_block_till_done()
    assert ent_reg.async_get("select.dishwasher_mode").device_id is None
    assert "device_id" not in entry.options
    assert dev_reg.async_get(plug.id) is not None  # someone else's device: left alone


async def test_rename_a_thing_renames_its_device(hass: HomeAssistant) -> None:
    entry = await add_thing(hass, "Alice", [machine("Day type", ["a", "b"])])
    result = await _reconfigure(hass, entry, "placement")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"name": "Alice Smith", "placement": PLACEMENT_VIRTUAL}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.title == "Alice Smith"
    device = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, entry.entry_id), entry.entry_id)
    assert device.name == "Alice Smith"


# Rules ----------------------------------------------------------------------------

POWER_TRIGGER = [{"trigger": "numeric_state", "entity_id": ["sensor.dishwasher_power"], "above": 10}]


def _fields(result: dict[str, Any]) -> dict[str, Any]:
    """The form as the frontend gets it."""
    import probatio
    from homeassistant.helpers import config_validation as cv

    return {f["name"]: f for f in probatio.to_field_list(result["data_schema"], custom_serializer=cv.custom_serializer)}


async def _rules_of(hass: HomeAssistant, entry: MockConfigEntry, state: str, machine_id: str | None = None) -> dict[str, Any]:
    result = await _edit(hass, entry, "rules", machine_id)
    assert result["step_id"] == "rules"
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"state": state})
    assert result["step_id"] == "rules_list"
    return result


def _rules(entry: MockConfigEntry, state: str, name: str | None = None) -> list[dict[str, Any]]:
    return next(s.get("rules", []) for s in machine_data(entry, name)["states"] if s["name"] == state)


async def test_add_edit_delete_a_trigger_rule(hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.dishwasher_power", "0")
    entry = await add_thing(hass, "Dishwasher", [machine("Mode", DISHWASHER_STATES)])
    result = await _rules_of(hass, entry, "running")
    assert "No rules yet" in result["description_placeholders"]["rules"]
    flow_id = result["flow_id"]

    result = await hass.config_entries.options.async_configure(flow_id, {"action": "add_trigger"})
    assert result["step_id"] == "trigger_rule"
    fields = _fields(result)
    assert fields["triggers"]["selector"] == {"trigger": {}}  # the automation editor's picker
    assert fields["conditions"]["selector"] == {"condition": {}}
    assert fields["from"]["selector"]["select"]["options"] == ["cleared", "loaded", "clean"]

    result = await hass.config_entries.options.async_configure(
        flow_id, {"name": "Drawing power", "from": ["cleared", "loaded"], "triggers": POWER_TRIGGER}
    )
    assert result["step_id"] == "rules_list"
    assert "**Drawing power**: trigger rule from cleared, loaded (1 trigger)" in result["description_placeholders"]["rules"]
    (rule,) = _rules(entry, "running")
    assert rule["triggers"] == POWER_TRIGGER  # stored exactly as sent
    assert rule["from"] == ["cleared", "loaded"]
    await hass.async_block_till_done()  # the entry reloads with the rule

    hass.states.async_set("sensor.dishwasher_power", "1500")
    await hass.async_block_till_done()
    assert hass.states.get("select.dishwasher_mode").state == "running"

    # Edit: the form starts from the rule.
    result = await hass.config_entries.options.async_configure(flow_id, {"action": f"edit:{rule['id']}"})
    assert result["step_id"] == "trigger_rule"
    result = await hass.config_entries.options.async_configure(
        flow_id, {"name": "Power on", "from": [], "triggers": POWER_TRIGGER}
    )
    (edited,) = _rules(entry, "running")
    assert (edited["id"], edited["name"], edited["from"]) == (rule["id"], "Power on", [])

    result = await hass.config_entries.options.async_configure(flow_id, {"action": f"delete:{rule['id']}"})
    assert result["step_id"] == "rules_list"
    assert _rules(entry, "running") == []

    result = await hass.config_entries.options.async_configure(flow_id, {"action": "done"})
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_trigger_rule_errors(hass: HomeAssistant) -> None:
    entry = await add_thing(hass, "Dishwasher", [machine("Mode", DISHWASHER_STATES)])
    result = await _rules_of(hass, entry, "running")
    flow_id = result["flow_id"]
    result = await hass.config_entries.options.async_configure(flow_id, {"action": "add_trigger"})
    result = await hass.config_entries.options.async_configure(flow_id, {"name": "x", "triggers": []})
    assert result["errors"] == {"triggers": "triggers_required"}
    result = await hass.config_entries.options.async_configure(
        flow_id, {"name": "x", "triggers": [{"trigger": "no_such_trigger"}]}
    )
    assert result["errors"] == {"triggers": "invalid_triggers"}
    assert result["description_placeholders"]["error"]
    result = await hass.config_entries.options.async_configure(
        flow_id, {"name": "x", "triggers": POWER_TRIGGER, "conditions": [{"condition": "nope"}]}
    )
    assert result["errors"] == {"conditions": "invalid_conditions"}
    assert _rules(entry, "running") == []


async def test_add_a_condition_rule(hass: HomeAssistant) -> None:
    hass.states.async_set("binary_sensor.workday", "on")
    entry = await add_thing(hass, "Alice time", [machine("Day type", ["holiday", "workday", "weekend"], "weekend")])
    result = await _rules_of(hass, entry, "workday")
    flow_id = result["flow_id"]
    result = await hass.config_entries.options.async_configure(flow_id, {"action": "add_condition"})
    assert result["step_id"] == "condition_rule"
    result = await hass.config_entries.options.async_configure(flow_id, {"name": "x", "conditions": []})
    assert result["errors"] == {"conditions": "conditions_required"}
    result = await hass.config_entries.options.async_configure(
        flow_id,
        {"name": "Workday sensor", "conditions": [{"condition": "state", "entity_id": "binary_sensor.workday", "state": "on"}]},
    )
    assert result["step_id"] == "rules_list"
    await hass.async_block_till_done()
    state = hass.states.get("select.alice_time_day_type")
    assert (state.state, state.attributes["reason"]) == ("workday", "Workday sensor")

    # The rules step lists rule counts per state.
    result = await hass.config_entries.options.async_configure(flow_id, {"action": "other_state"})
    labels = {o["value"]: o["label"] for o in _fields(result)["state"]["selector"]["select"]["options"]}
    assert labels == {"holiday": "holiday (0 rules)", "workday": "workday (1 rule)", "weekend": "weekend (0 rules)"}


async def test_loop_warning(hass: HomeAssistant) -> None:
    a = machine("A", ["x", "y"], "x")
    a["states"][1]["rules"] = [{
        "id": "r", "name": "B is y", "kind": "condition",
        "conditions": [{"condition": "state", "entity_id": "select.loop_b", "state": "y"}],
    }]
    entry = await add_thing(hass, "Loop", [a, machine("B", ["x", "y"], "x")])
    assert hass.states.get("select.loop_b") is not None

    result = await _rules_of(hass, entry, "y", machine_id="m_b")
    flow_id = result["flow_id"]
    await hass.config_entries.options.async_configure(flow_id, {"action": "add_condition"})
    result = await hass.config_entries.options.async_configure(
        flow_id, {"name": "A is x", "conditions": [{"condition": "state", "entity_id": "select.loop_a", "state": "x"}]}
    )
    assert result["step_id"] == "loop_warning"
    assert result["description_placeholders"]["loop"] == "B → A → B"
    assert _rules(entry, "y", "B") == []  # not saved yet
    result = await hass.config_entries.options.async_configure(flow_id, {})
    assert result["step_id"] == "rules_list"
    assert [r["name"] for r in _rules(entry, "y", "B")] == ["A is x"]


async def test_rename_updates_rules_from(hass: HomeAssistant) -> None:
    m = machine("Mode", DISHWASHER_STATES)
    m["states"][3]["rules"] = [{"id": "r", "name": "done", "kind": "trigger", "from": ["running"],
                                "triggers": POWER_TRIGGER}]
    entry = await add_thing(hass, "Dishwasher", [m])
    result = await _edit(hass, entry, "rename_state")
    await hass.config_entries.options.async_configure(result["flow_id"], {"state": "running", "new_name": "washing"})
    assert _rules(entry, "clean")[0]["from"] == ["washing"]


# Copy, state sensors ----------------------------------------------------------------


async def test_copy_a_machine_from_another_entry(hass: HomeAssistant) -> None:
    day = machine("Day type", ["vacation", "workday", "weekend"], "weekend", machine_id="alice_day")
    day["states"][0]["rules"] = [{"id": "r1", "name": "PTO", "kind": "condition",
                                  "conditions": [{"condition": "state", "entity_id": "input_boolean.pto", "state": "on"}]}]
    alice = await add_thing(hass, "Alice", [day])
    bob = await add_thing(hass, "Bob", [machine("Hour type", ["a", "b"], machine_id="bob_hour")])

    result = await _reconfigure(hass, bob, "copy_machine")
    labels = {o["value"]: o["label"] for o in _fields(result)["source"]["selector"]["select"]["options"]}
    assert labels == {f"{alice.entry_id}/alice_day": "Alice: Day type", f"{bob.entry_id}/bob_hour": "Bob: Hour type"}
    flow_id = result["flow_id"]
    result = await hass.config_entries.options.async_configure(flow_id, {"source": f"{bob.entry_id}/bob_hour"})
    assert result["errors"] == {"name": "duplicate_machine"}  # same name, same entry
    result = await hass.config_entries.options.async_configure(flow_id, {"source": f"{alice.entry_id}/alice_day"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    copy = machine_data(bob, "Day type")
    assert copy["id"] != "alice_day"
    assert copy["states"][0]["rules"][0]["id"] != "r1"
    assert copy["states"][0]["rules"][0]["conditions"] == day["states"][0]["rules"][0]["conditions"]
    assert hass.states.get("select.bob_day_type").state == "weekend"
    assert machine_data(alice)["id"] == "alice_day"  # the original is untouched


async def test_state_sensors_on_off_and_rename(hass: HomeAssistant) -> None:
    entry = await add_thing(hass, "Alice", [machine("Hour type", ["sleeping", "working", "free"], "free")])
    ent_reg = er.async_get(hass)
    result = await _edit(hass, entry, "settings")
    await hass.config_entries.options.async_configure(
        result["flow_id"], {"name": "Hour type", "fallback": "free", "max_flips": 10, "state_sensors": True}
    )
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.alice_hour_type_free").state == "on"
    assert hass.states.get("binary_sensor.alice_hour_type_sleeping").state == "off"

    result = await _edit(hass, entry, "rename_state")
    await hass.config_entries.options.async_configure(result["flow_id"], {"state": "working", "new_name": "busy"})
    await hass.async_block_till_done()
    # The renamed state keeps its sensor: same entity id, new unique id.
    reg = ent_reg.async_get("binary_sensor.alice_hour_type_working")
    assert reg is not None and reg.unique_id == "m_hour_type_is_busy"
    assert hass.states.get("binary_sensor.alice_hour_type_working").attributes["friendly_name"] == "Alice Hour type busy"

    result = await _edit(hass, entry, "settings")
    await hass.config_entries.options.async_configure(
        result["flow_id"], {"name": "Hour type", "fallback": "free", "max_flips": 10, "state_sensors": False}
    )
    await hass.async_block_till_done()
    assert hass.states.async_entity_ids("binary_sensor") == []
    assert ent_reg.async_get("binary_sensor.alice_hour_type_free") is None
