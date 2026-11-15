"""Config, options and machine (subentry) flows."""
from __future__ import annotations

from typing import Any

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.abstract_state_machine.const import (
    CONF_PLACEMENT,
    DOMAIN,
    PLACEMENT_EXISTING,
    PLACEMENT_NONE,
    PLACEMENT_VIRTUAL,
    SUBENTRY_MACHINE,
)

from .common import DISHWASHER_STATES, add_entry, machine


async def _start(hass: HomeAssistant, placement: str) -> dict[str, Any]:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.MENU
    return await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": placement})


async def _first_machine(hass: HomeAssistant, result: dict[str, Any], name: str, states: list[str], fallback: str) -> None:
    """The entry flow hands over to the machine flow; fill it in."""
    assert result["type"] is FlowResultType.CREATE_ENTRY
    flow_type, flow_id = result["next_flow"]
    assert flow_type == config_entries.FlowType.CONFIG_SUBENTRIES_FLOW
    sub = await hass.config_entries.subentries.async_configure(flow_id, {"name": name, "states": states})
    assert sub["type"] is FlowResultType.FORM
    assert sub["step_id"] == "fallback"
    sub = await hass.config_entries.subentries.async_configure(flow_id, {"fallback": fallback})
    assert sub["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()


async def test_virtual_device_then_first_machine(hass: HomeAssistant) -> None:
    result = await _start(hass, PLACEMENT_VIRTUAL)
    assert result["step_id"] == PLACEMENT_VIRTUAL
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"name": " Alice time "})
    assert result["title"] == "Alice time"
    assert result["options"] == {CONF_PLACEMENT: PLACEMENT_VIRTUAL}
    await _first_machine(hass, result, "Day type", ["workday", "weekend", "holiday"], "weekend")

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    (sub,) = entry.subentries.values()
    assert sub.title == "Day type"
    assert sub.data["fallback"] == "weekend"
    assert hass.states.get("select.alice_time_day_type").state == "weekend"


async def test_add_a_second_machine(hass: HomeAssistant) -> None:
    entry = await add_entry(hass, "Alice time", [machine("Day type", ["workday", "weekend"])])
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_MACHINE), context={"source": config_entries.SOURCE_USER}
    )
    assert result["description_placeholders"] == {"entry": "Alice time"}
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Hour type", "states": ["sleeping", "working", "free"], "person": "person.alice"}
    )
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {"fallback": "free"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    state = hass.states.get("select.alice_time_hour_type")
    assert state.state == "free"
    assert state.attributes["person"] == "person.alice"


async def test_machine_state_errors(hass: HomeAssistant) -> None:
    entry = await add_entry(hass, "Alice time", [])
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_MACHINE), context={"source": config_entries.SOURCE_USER}
    )
    for states, error in (
        (["one"], "too_few_states"),
        (["Busy", "busy"], "duplicate_state"),
        (["unknown", "x"], "reserved_state"),
    ):
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], {"name": "Day type", "states": states}
        )
        assert result["errors"] == {"states": error}
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "  ", "states": ["a", "b"]}
    )
    assert result["errors"] == {"name": "name_required"}


async def test_existing_device_flow(hass: HomeAssistant) -> None:
    plug_entry = MockConfigEntry(domain="test")
    plug_entry.add_to_hass(hass)
    plug = dr.async_get(hass).async_get_or_create(
        config_entry_id=plug_entry.entry_id, identifiers={("test", "plug")}, name="Dishwasher plug"
    )
    result = await _start(hass, PLACEMENT_EXISTING)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"device_id": plug.id})
    assert result["title"] == "Dishwasher plug"
    assert result["options"] == {CONF_PLACEMENT: PLACEMENT_EXISTING, "device_id": plug.id}
    await _first_machine(hass, result, "Mode", DISHWASHER_STATES, "cleared")
    assert er.async_get(hass).async_get("select.dishwasher_plug_mode").device_id == plug.id


async def test_no_device_flow(hass: HomeAssistant) -> None:
    result = await _start(hass, PLACEMENT_NONE)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"name": "House"})
    await _first_machine(hass, result, "House mode", ["home", "away"], "home")
    assert er.async_get(hass).async_get("select.house_mode").device_id is None


# Reconfigure a machine ----------------------------------------------------------


async def _reconfigure(hass: HomeAssistant, entry: MockConfigEntry, choice: str) -> dict[str, Any]:
    (sub_id,) = entry.subentries
    result = await entry.start_subentry_reconfigure_flow(hass, sub_id)
    assert result["type"] is FlowResultType.MENU
    return await hass.config_entries.subentries.async_configure(result["flow_id"], {"next_step_id": choice})


async def test_edit_states(hass: HomeAssistant) -> None:
    entry = await add_entry(hass, "Dishwasher", [machine("Mode", DISHWASHER_STATES)])
    result = await _reconfigure(hass, entry, "edit_states")
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"states": ["loaded", "running", "clean", "cleared", "drying"]}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    state = hass.states.get("select.dishwasher_mode")
    assert state.attributes["options"] == ["loaded", "running", "clean", "cleared", "drying"]
    assert state.state == "cleared"  # still there, so kept
    assert hass.states.get("event.dishwasher_mode_transition").attributes["event_types"][-1] == "drying"


async def test_edit_states_error(hass: HomeAssistant) -> None:
    entry = await add_entry(hass, "Dishwasher", [machine("Mode", DISHWASHER_STATES)])
    result = await _reconfigure(hass, entry, "edit_states")
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {"states": ["a"]})
    assert result["errors"] == {"states": "too_few_states"}


async def test_rename_keeps_the_current_state(hass: HomeAssistant) -> None:
    entry = await add_entry(hass, "Dishwasher", [machine("Mode", DISHWASHER_STATES)])
    await hass.services.async_call(
        "select", "select_option", {"entity_id": "select.dishwasher_mode", "option": "running"}, blocking=True
    )
    result = await _reconfigure(hass, entry, "rename_state")
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"state": "running", "new_name": "washing"}
    )
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    state = hass.states.get("select.dishwasher_mode")
    assert state.state == "washing"
    assert state.attributes["previous"] == "cleared"
    assert state.attributes["options"] == ["cleared", "loaded", "washing", "clean"]


async def test_rename_clash(hass: HomeAssistant) -> None:
    entry = await add_entry(hass, "Dishwasher", [machine("Mode", DISHWASHER_STATES)])
    result = await _reconfigure(hass, entry, "rename_state")
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"state": "running", "new_name": "Clean"}
    )
    assert result["errors"] == {"new_name": "duplicate_state"}


async def test_settings(hass: HomeAssistant) -> None:
    entry = await add_entry(hass, "Dishwasher", [machine("Mode", DISHWASHER_STATES, person="person.alice")])
    result = await _reconfigure(hass, entry, "settings")
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Cycle", "fallback": "clean"}
    )
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    (sub,) = entry.subentries.values()
    assert sub.title == "Cycle"
    assert sub.data["fallback"] == "clean"
    assert sub.data["person"] is None  # cleared
    # The entity id stays; the friendly name follows the new name.
    assert hass.states.get("select.dishwasher_mode").attributes["friendly_name"] == "Dishwasher Cycle"


async def test_icons(hass: HomeAssistant) -> None:
    entry = await add_entry(hass, "Dishwasher", [machine("Mode", DISHWASHER_STATES)])
    result = await _reconfigure(hass, entry, "icons")
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {"cleared": "mdi:dishwasher-off"})
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    assert hass.states.get("select.dishwasher_mode").attributes["icon"] == "mdi:dishwasher-off"


# Options: move the machines ------------------------------------------------------


async def test_move_from_virtual_device_to_existing_and_back_to_none(hass: HomeAssistant) -> None:
    entry = await add_entry(hass, "Dishwasher", [machine("Mode", DISHWASHER_STATES)])
    dev_reg, ent_reg = dr.async_get(hass), er.async_get(hass)
    virtual = dev_reg.async_get_device_by_identifier((DOMAIN, entry.entry_id), entry.entry_id)
    assert virtual is not None
    await hass.services.async_call(
        "select", "select_option", {"entity_id": "select.dishwasher_mode", "option": "running"}, blocking=True
    )

    plug_entry = MockConfigEntry(domain="test")
    plug_entry.add_to_hass(hass)
    plug = dev_reg.async_get_or_create(config_entry_id=plug_entry.entry_id, identifiers={("test", "plug")}, name="Plug")

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"placement": PLACEMENT_EXISTING})
    assert result["errors"] == {"device_id": "device_required"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"placement": PLACEMENT_EXISTING, "device_id": plug.id}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    reg = ent_reg.async_get("select.dishwasher_mode")
    assert reg is not None and reg.device_id == plug.id
    assert dev_reg.async_get(virtual.id) is None  # our virtual device is gone
    assert hass.states.get("select.dishwasher_mode").state == "running"  # state kept

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"placement": PLACEMENT_NONE})
    await hass.async_block_till_done()
    assert ent_reg.async_get("select.dishwasher_mode").device_id is None
    assert dev_reg.async_get(plug.id) is not None  # someone else's device: left alone


# Rules ----------------------------------------------------------------------------

POWER_TRIGGER = [{"trigger": "numeric_state", "entity_id": ["sensor.dishwasher_power"], "above": 10}]


def _fields(result: dict[str, Any]) -> dict[str, Any]:
    """The form as the frontend gets it."""
    import probatio
    from homeassistant.helpers import config_validation as cv

    return {f["name"]: f for f in probatio.to_field_list(result["data_schema"], custom_serializer=cv.custom_serializer)}


async def _rules_of(hass: HomeAssistant, entry: MockConfigEntry, state: str) -> dict[str, Any]:
    result = await _reconfigure(hass, entry, "rules")
    assert result["step_id"] == "rules"
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {"state": state})
    assert result["step_id"] == "rules_list"
    return result


def _rules(entry: MockConfigEntry, state: str) -> list[dict[str, Any]]:
    (sub,) = entry.subentries.values()
    return next(s.get("rules", []) for s in sub.data["states"] if s["name"] == state)


async def test_add_edit_delete_a_trigger_rule(hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.dishwasher_power", "0")
    entry = await add_entry(hass, "Dishwasher", [machine("Mode", DISHWASHER_STATES)])
    result = await _rules_of(hass, entry, "running")
    assert "No rules yet" in result["description_placeholders"]["rules"]
    flow_id = result["flow_id"]

    result = await hass.config_entries.subentries.async_configure(flow_id, {"action": "add_trigger"})
    assert result["step_id"] == "trigger_rule"
    fields = _fields(result)
    assert fields["triggers"]["selector"] == {"trigger": {}}  # the automation editor's picker
    assert fields["conditions"]["selector"] == {"condition": {}}
    assert fields["from"]["selector"]["select"]["options"] == ["cleared", "loaded", "clean"]

    result = await hass.config_entries.subentries.async_configure(
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
    result = await hass.config_entries.subentries.async_configure(flow_id, {"action": f"edit:{rule['id']}"})
    assert result["step_id"] == "trigger_rule"
    result = await hass.config_entries.subentries.async_configure(
        flow_id, {"name": "Power on", "from": [], "triggers": POWER_TRIGGER}
    )
    (edited,) = _rules(entry, "running")
    assert (edited["id"], edited["name"], edited["from"]) == (rule["id"], "Power on", [])

    result = await hass.config_entries.subentries.async_configure(flow_id, {"action": f"delete:{rule['id']}"})
    assert result["step_id"] == "rules_list"
    assert _rules(entry, "running") == []

    result = await hass.config_entries.subentries.async_configure(flow_id, {"action": "done"})
    assert result["reason"] == "reconfigure_successful"


async def test_trigger_rule_errors(hass: HomeAssistant) -> None:
    entry = await add_entry(hass, "Dishwasher", [machine("Mode", DISHWASHER_STATES)])
    result = await _rules_of(hass, entry, "running")
    flow_id = result["flow_id"]
    result = await hass.config_entries.subentries.async_configure(flow_id, {"action": "add_trigger"})
    result = await hass.config_entries.subentries.async_configure(flow_id, {"name": "x", "triggers": []})
    assert result["errors"] == {"triggers": "triggers_required"}
    result = await hass.config_entries.subentries.async_configure(
        flow_id, {"name": "x", "triggers": [{"trigger": "no_such_trigger"}]}
    )
    assert result["errors"] == {"triggers": "invalid_triggers"}
    assert result["description_placeholders"]["error"]
    result = await hass.config_entries.subentries.async_configure(
        flow_id, {"name": "x", "triggers": POWER_TRIGGER, "conditions": [{"condition": "nope"}]}
    )
    assert result["errors"] == {"conditions": "invalid_conditions"}
    assert _rules(entry, "running") == []


async def test_add_a_condition_rule(hass: HomeAssistant) -> None:
    hass.states.async_set("binary_sensor.workday", "on")
    entry = await add_entry(hass, "Alice time", [machine("Day type", ["holiday", "workday", "weekend"], "weekend")])
    result = await _rules_of(hass, entry, "workday")
    flow_id = result["flow_id"]
    result = await hass.config_entries.subentries.async_configure(flow_id, {"action": "add_condition"})
    assert result["step_id"] == "condition_rule"
    result = await hass.config_entries.subentries.async_configure(flow_id, {"name": "x", "conditions": []})
    assert result["errors"] == {"conditions": "conditions_required"}
    result = await hass.config_entries.subentries.async_configure(
        flow_id,
        {"name": "Workday sensor", "conditions": [{"condition": "state", "entity_id": "binary_sensor.workday", "state": "on"}]},
    )
    assert result["step_id"] == "rules_list"
    await hass.async_block_till_done()
    state = hass.states.get("select.alice_time_day_type")
    assert (state.state, state.attributes["reason"]) == ("workday", "Workday sensor")

    # The rules step lists rule counts per state.
    result = await hass.config_entries.subentries.async_configure(flow_id, {"action": "other_state"})
    labels = {o["value"]: o["label"] for o in _fields(result)["state"]["selector"]["select"]["options"]}
    assert labels == {"holiday": "holiday (0 rules)", "workday": "workday (1 rule)", "weekend": "weekend (0 rules)"}


async def test_loop_warning(hass: HomeAssistant) -> None:
    a = machine("A", ["x", "y"], "x", sub_id="sub_a")
    a["data"]["states"][1]["rules"] = [{
        "id": "r", "name": "B is y", "kind": "condition",
        "conditions": [{"condition": "state", "entity_id": "select.loop_b", "state": "y"}],
    }]
    entry_a = await add_entry(hass, "Loop", [a])
    entry_b = await add_entry(hass, "Loop B", [machine("B", ["x", "y"], "x", sub_id="sub_b")])
    assert hass.states.get("select.loop_b_b") is not None
    # Point A at B's real entity id.
    hass.config_entries.async_update_subentry(
        entry_a, entry_a.subentries["sub_a"],
        data={**a["data"], "states": [a["data"]["states"][0], {"name": "y", "rules": [{
            "id": "r", "name": "B is y", "kind": "condition",
            "conditions": [{"condition": "state", "entity_id": "select.loop_b_b", "state": "y"}],
        }]}]},
    )
    await hass.async_block_till_done()

    result = await _rules_of(hass, entry_b, "y")
    flow_id = result["flow_id"]
    await hass.config_entries.subentries.async_configure(flow_id, {"action": "add_condition"})
    result = await hass.config_entries.subentries.async_configure(
        flow_id, {"name": "A is x", "conditions": [{"condition": "state", "entity_id": "select.loop_a", "state": "x"}]}
    )
    assert result["step_id"] == "loop_warning"
    assert result["description_placeholders"]["loop"] == "B → A → B"
    assert _rules(entry_b, "y") == []  # not saved yet
    result = await hass.config_entries.subentries.async_configure(flow_id, {})
    assert result["step_id"] == "rules_list"
    assert [r["name"] for r in _rules(entry_b, "y")] == ["A is x"]


async def test_settings_flip_limit(hass: HomeAssistant) -> None:
    entry = await add_entry(hass, "Dishwasher", [machine("Mode", DISHWASHER_STATES)])
    result = await _reconfigure(hass, entry, "settings")
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Mode", "fallback": "cleared", "max_flips": 4}
    )
    assert result["reason"] == "reconfigure_successful"
    (sub,) = entry.subentries.values()
    assert sub.data["max_flips"] == 4


async def test_rename_updates_rules_from(hass: HomeAssistant) -> None:
    sub = machine("Mode", DISHWASHER_STATES)
    sub["data"]["states"][3]["rules"] = [{"id": "r", "name": "done", "kind": "trigger", "from": ["running"],
                                          "triggers": POWER_TRIGGER}]
    entry = await add_entry(hass, "Dishwasher", [sub])
    result = await _reconfigure(hass, entry, "rename_state")
    await hass.config_entries.subentries.async_configure(result["flow_id"], {"state": "running", "new_name": "washing"})
    assert _rules(entry, "clean")[0]["from"] == ["washing"]
