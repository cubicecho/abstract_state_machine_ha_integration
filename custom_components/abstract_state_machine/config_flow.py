"""Flows.

- Config flow: one entry per person or thing (a new virtual device, an
  existing device, or no device), with its first machine.
- Options flow (Configure): add, edit or remove machines, rename, or move
  them to another device. Editing a machine covers its rules, states, names,
  fallback, person, flip limit and icons.
"""
from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

import probatio as vol  # HA 2026.10+ validates with probatio
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector

from . import model
from .const import (
    CONF_DEVICE_ID,
    CONF_FALLBACK,
    CONF_MACHINES,
    CONF_MAX_FLIPS,
    CONF_PERSON,
    CONF_PLACEMENT,
    CONF_STATES,
    DOMAIN,
    PLACEMENT_EXISTING,
    PLACEMENT_NONE,
    PLACEMENT_VIRTUAL,
    PLACEMENTS,
)
from .data import state_sensor_unique_id, unique_id
from .runtime import async_note_rename, async_validate_conditions, async_validate_triggers

CONF_NAME = "name"
CONF_MACHINE = "machine"
CONF_STATE = "state"
CONF_NEW_NAME = "new_name"
CONF_ACTION = "action"
CONF_FROM = "from"
CONF_TRIGGERS = "triggers"
CONF_CONDITIONS = "conditions"
CONF_SOURCE = "source"
CONF_STATE_SENSORS = "state_sensors"

TEXT = selector.TextSelector()
STATE_LIST = selector.TextSelector(selector.TextSelectorConfig(multiple=True))
PERSON = selector.EntitySelector(selector.EntitySelectorConfig(domain="person"))
DEVICE = selector.DeviceSelector()
MAX_FLIPS = selector.NumberSelector(
    selector.NumberSelectorConfig(min=1, max=model.MAX_MAX_FLIPS, step=1, mode=selector.NumberSelectorMode.BOX)
)


class _RawTriggerSelector(selector.TriggerSelector):
    """The automation editor's trigger picker, keeping the value exactly as sent.

    The stock selector validates into Python objects (timedeltas, templates)
    that can't be stored in a config entry. We store the plain JSON and
    validate a copy ourselves (``runtime.async_validate_triggers``).
    """

    def __call__(self, data: Any) -> Any:
        return data


class _RawConditionSelector(selector.ConditionSelector):
    """The automation editor's condition picker, keeping the value exactly as sent (see above)."""

    def __call__(self, data: Any) -> Any:
        return data


TRIGGERS = _RawTriggerSelector()
CONDITIONS = _RawConditionSelector()


def _choice(
    options: list[str] | list[selector.SelectOptionDict],
    translation_key: str | None = None,
    *,
    multiple: bool = False,
) -> selector.SelectSelector:
    config = selector.SelectSelectorConfig(
        options=options, mode=selector.SelectSelectorMode.DROPDOWN, multiple=multiple
    )
    if translation_key:
        config["translation_key"] = translation_key
    return selector.SelectSelector(config)


def _device_name(device: dr.AnyDeviceEntry) -> str:
    return device.name_by_user or device.name or device.id


class _NewMachine:
    """The new-machine form, shared by the config flow (first machine) and Configure (another one)."""

    def __init__(self) -> None:
        self.name = ""
        self.names: list[str] = []
        self.person: str | None = None

    def read(self, user_input: dict[str, Any], taken: Callable[[str], bool]) -> dict[str, str]:
        """Take the form's input; the errors, if any."""
        errors: dict[str, str] = {}
        self.name = user_input[CONF_NAME].strip()
        self.person = user_input.get(CONF_PERSON) or None
        try:
            self.names = model.clean_state_names(user_input.get(CONF_STATES) or [])
        except model.InvalidStates as err:
            errors[CONF_STATES] = err.key
        if not self.name:
            errors[CONF_NAME] = "name_required"
        elif taken(self.name):
            errors[CONF_NAME] = "duplicate_machine"
        return errors

    def schema(self) -> vol.Schema:
        return vol.Schema(
            {
                vol.Required(CONF_NAME, default=self.name or vol.UNDEFINED): TEXT,
                vol.Required(CONF_STATES, default=self.names or vol.UNDEFINED): STATE_LIST,
                vol.Optional(CONF_PERSON, description={"suggested_value": self.person}): PERSON,
            }
        )

    def fallback_schema(self) -> vol.Schema:
        return vol.Schema({vol.Required(CONF_FALLBACK, default=self.names[0]): _choice(self.names)})

    def build(self, user_input: dict[str, Any]) -> dict[str, Any]:
        return model.new_machine_data(self.name, self.names, user_input[CONF_FALLBACK], self.person)


# Config flow: a person or thing and its first machine ------------------------------


class AbstractStateMachineConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add a person or thing that machines live on."""

    VERSION = 1

    def __init__(self) -> None:
        self._title = ""
        self._options: dict[str, Any] = {}
        self._new = _NewMachine()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="user", menu_options=list(PLACEMENTS))

    async def async_step_virtual_device(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._named(PLACEMENT_VIRTUAL, user_input)

    async def async_step_none(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._named(PLACEMENT_NONE, user_input)

    async def _named(self, placement: str, user_input: dict[str, Any] | None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if name := user_input[CONF_NAME].strip():
                self._title, self._options = name, {CONF_PLACEMENT: placement}
                return await self.async_step_machine()
            errors[CONF_NAME] = "name_required"
        return self.async_show_form(
            step_id=placement, data_schema=vol.Schema({vol.Required(CONF_NAME): TEXT}), errors=errors
        )

    async def async_step_existing_device(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            device = dr.async_get(self.hass).async_get(user_input[CONF_DEVICE_ID])
            if device is None:
                errors[CONF_DEVICE_ID] = "device_not_found"
            else:
                self._title = _device_name(device)
                self._options = {CONF_PLACEMENT: PLACEMENT_EXISTING, CONF_DEVICE_ID: device.id}
                return await self.async_step_machine()
        return self.async_show_form(
            step_id=PLACEMENT_EXISTING,
            data_schema=vol.Schema({vol.Required(CONF_DEVICE_ID): DEVICE}),
            errors=errors,
        )

    async def async_step_machine(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors = self._new.read(user_input, lambda _: False) if user_input is not None else {}
        if user_input is not None and not errors:
            return await self.async_step_fallback()
        return self.async_show_form(
            step_id="machine", data_schema=self._new.schema(), errors=errors,
            description_placeholders={"thing": self._title},
        )

    async def async_step_fallback(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            options = {**self._options, CONF_MACHINES: [self._new.build(user_input)]}
            return self.async_create_entry(title=self._title, data={}, options=options)
        return self.async_show_form(step_id="fallback", data_schema=self._new.fallback_schema())

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return MachinesOptionsFlow()


# Options flow (Configure): machines, name and device --------------------------------


class MachinesOptionsFlow(OptionsFlow):
    """Add, edit or remove a person or thing's machines; rename it or move it."""

    def __init__(self) -> None:
        self._new = _NewMachine()
        self._machine_id = ""
        self._rule_state = ""  # the state whose rules are being edited
        self._rule_id: str | None = None  # the rule being edited; None = a new one
        self._pending_rule: dict[str, Any] | None = None  # waiting for the loop warning

    def _machines(self) -> list[dict[str, Any]]:
        return model.machines_of(self.config_entry.options)

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        machines = self._machines()
        options = ["edit_machine"] if machines else []
        options += ["add_machine", "copy_machine"]
        if len(machines) > 1:
            options.append("remove_machine")
        options.append("placement")
        return self.async_show_menu(
            step_id="init",
            menu_options=options,
            description_placeholders={
                "thing": self.config_entry.title,
                "machines": ", ".join(model.parse(m).name for m in machines) or "none yet",
            },
        )

    async def async_step_add_machine(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self.async_step_machine()

    async def async_step_machine(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors = self._new.read(user_input, self._name_taken) if user_input is not None else {}
        if user_input is not None and not errors:
            return await self.async_step_fallback()
        return self.async_show_form(
            step_id="machine", data_schema=self._new.schema(), errors=errors,
            description_placeholders={"thing": self.config_entry.title},
        )

    async def async_step_fallback(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self._save_and_finish(self._new.build(user_input))
        return self.async_show_form(step_id="fallback", data_schema=self._new.fallback_schema())

    async def async_step_copy_machine(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Copy any person or thing's machine into this one (Bob's day type from Alice's)."""
        sources = _all_machines(self.hass)
        errors: dict[str, str] = {}
        if user_input is not None:
            source = sources.get(user_input[CONF_SOURCE])
            name = (user_input.get(CONF_NAME) or "").strip() or (model.parse(source[1]).name if source else "")
            if source is None:
                errors[CONF_SOURCE] = "unknown_machine"
            elif self._name_taken(name):
                errors[CONF_NAME] = "duplicate_machine"
            else:
                return self._save_and_finish(model.copy_machine(source[1], name))
        options: list[selector.SelectOptionDict] = [
            {"value": key, "label": f"{title}: {model.parse(raw).name}"} for key, (title, raw) in sources.items()
        ]
        schema = vol.Schema(
            {
                vol.Required(CONF_SOURCE): _choice(options),
                vol.Optional(CONF_NAME): TEXT,
            }
        )
        return self.async_show_form(step_id="copy_machine", data_schema=schema, errors=errors)

    async def async_step_edit_machine(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Pick a machine to edit (straight through when there's only one)."""
        machines = self._machines()
        if user_input is not None or len(machines) == 1:
            self._machine_id = user_input[CONF_MACHINE] if user_input is not None else machines[0]["id"]
            return await self.async_step_machine_menu()
        return self.async_show_form(
            step_id="edit_machine", data_schema=vol.Schema({vol.Required(CONF_MACHINE): self._machine_choice()})
        )

    async def async_step_remove_machine(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                data=model.without_machine(self.config_entry.options, user_input[CONF_MACHINE])
            )
        return self.async_show_form(
            step_id="remove_machine", data_schema=vol.Schema({vol.Required(CONF_MACHINE): self._machine_choice()})
        )

    async def async_step_placement(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Rename the person or thing, or move its machines to another device, or off devices."""
        entry = self.config_entry
        errors: dict[str, str] = {}
        if user_input is not None:
            name = user_input[CONF_NAME].strip()
            placement = user_input[CONF_PLACEMENT]
            options = {k: v for k, v in entry.options.items() if k != CONF_DEVICE_ID}
            options[CONF_PLACEMENT] = placement
            if not name:
                errors[CONF_NAME] = "name_required"
            if placement == PLACEMENT_EXISTING:
                device_id = user_input.get(CONF_DEVICE_ID)
                if not device_id:
                    errors[CONF_DEVICE_ID] = "device_required"
                elif dr.async_get(self.hass).async_get(device_id) is None:
                    errors[CONF_DEVICE_ID] = "device_not_found"
                options[CONF_DEVICE_ID] = device_id
            if not errors:
                if name != entry.title:
                    self.hass.config_entries.async_update_entry(entry, title=name)
                return self.async_create_entry(data=options)
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=entry.title): TEXT,
                vol.Required(
                    CONF_PLACEMENT, default=entry.options.get(CONF_PLACEMENT, PLACEMENT_VIRTUAL)
                ): _choice(list(PLACEMENTS), CONF_PLACEMENT),
                vol.Optional(
                    CONF_DEVICE_ID, description={"suggested_value": entry.options.get(CONF_DEVICE_ID)}
                ): DEVICE,
            }
        )
        return self.async_show_form(step_id="placement", data_schema=schema, errors=errors)

    def _machine_choice(self) -> selector.SelectSelector:
        options: list[selector.SelectOptionDict] = [
            {"value": m["id"], "label": model.parse(m).name} for m in self._machines()
        ]
        return _choice(options)

    def _name_taken(self, name: str, machine_id: str | None = None) -> bool:
        """Another machine of this person or thing already has the name."""
        return any(
            model.parse(m).name.casefold() == name.casefold() for m in self._machines() if m["id"] != machine_id
        )

    # Edit one machine -------------------------------------------------------------

    async def async_step_machine_menu(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="machine_menu",
            menu_options=["rules", "edit_states", "rename_state", "settings", "icons"],
            description_placeholders={"machine": self._machine().name},
        )

    def _raw_machine(self) -> dict[str, Any]:
        machine = model.find_machine(self.config_entry.options, self._machine_id)
        if machine is None:
            raise HomeAssistantError("machine gone")  # removed by another flow meanwhile
        return machine

    def _machine(self) -> model.MachineDef:
        return model.parse(self._raw_machine())

    def _save(self, machine: dict[str, Any]) -> None:
        """Store a machine change and stay in the flow. The update listener reloads the entry."""
        entry = self.config_entry
        self.hass.config_entries.async_update_entry(entry, options=model.with_machine(entry.options, machine))

    def _save_and_finish(self, machine: dict[str, Any]) -> ConfigFlowResult:
        """Store a machine change and close. The entry's update listener reloads it."""
        return self.async_create_entry(data=model.with_machine(self.config_entry.options, machine))

    # Rules -----------------------------------------------------------------

    async def async_step_rules(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Pick the state whose rules to edit."""
        machine = self._machine()
        if user_input is not None:
            self._rule_state = user_input[CONF_STATE]
            return await self.async_step_rules_list()
        options: list[selector.SelectOptionDict] = [
            {"value": state.name, "label": f"{state.name} ({_count(len(state.rules), 'rule')})"}
            for state in machine.states
        ]
        schema = vol.Schema({vol.Required(CONF_STATE, default=self._rule_state or machine.names[0]): _choice(options)})
        return self.async_show_form(
            step_id="rules", data_schema=schema, description_placeholders={"fallback": machine.fallback}
        )

    async def async_step_rules_list(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """One state's rules: add, edit or delete."""
        machine = self._machine()
        state = machine.state(self._rule_state)
        if state is None:  # renamed or removed meanwhile
            return await self.async_step_rules()
        if user_input is not None:
            action: str = user_input[CONF_ACTION]
            if action == "done":
                return self.async_create_entry(data=dict(self.config_entry.options))  # already saved
            if action == "other_state":
                return await self.async_step_rules()
            self._rule_id = None
            if action == "add_trigger":
                return await self.async_step_trigger_rule()
            if action == "add_condition":
                return await self.async_step_condition_rule()
            verb, _, rule_id = action.partition(":")
            rule = next((r for r in state.rules if r.id == rule_id), None)
            if rule is not None and verb == "delete":
                self._save(model.remove_rule(self._raw_machine(), rule_id))
                return await self.async_step_rules_list()
            if rule is not None and verb == "edit":
                self._rule_id = rule_id
                if rule.kind == model.KIND_TRIGGER:
                    return await self.async_step_trigger_rule()
                return await self.async_step_condition_rule()
        options: list[selector.SelectOptionDict] = [
            {"value": "add_trigger", "label": "Add a trigger rule: when something happens, go here"},
            {"value": "add_condition", "label": "Add a condition rule: while something is true, be here"},
        ]
        for rule in state.rules:
            options.append({"value": f"edit:{rule.id}", "label": f"Edit “{rule.name}”"})
        for rule in state.rules:
            options.append({"value": f"delete:{rule.id}", "label": f"Delete “{rule.name}”"})
        options += [
            {"value": "other_state", "label": "Rules of another state"},
            {"value": "done", "label": "Done"},
        ]
        schema = vol.Schema({vol.Required(CONF_ACTION, default="add_trigger" if not state.rules else "done"): _choice(options)})
        return self.async_show_form(
            step_id="rules_list",
            data_schema=schema,
            description_placeholders={"state": state.name, "rules": _describe_rules(state)},
        )

    async def async_step_trigger_rule(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """When a trigger fires (and the machine is in one of ``from``, and conditions pass), go to the state."""
        current = self._current_rule()
        errors: dict[str, str] = {}
        placeholders = {"state": self._rule_state, "error": ""}
        if user_input is not None:
            rule = {
                "id": self._rule_id or uuid.uuid4().hex,
                "name": (user_input.get(CONF_NAME) or "").strip(),
                "kind": model.KIND_TRIGGER,
                "from": list(user_input.get(CONF_FROM) or []),
                "triggers": _as_list(user_input.get(CONF_TRIGGERS)),
                "conditions": _as_list(user_input.get(CONF_CONDITIONS)),
            }
            current = rule
            if not rule["name"]:
                errors[CONF_NAME] = "name_required"
            elif not rule["triggers"]:
                errors[CONF_TRIGGERS] = "triggers_required"
            elif error := await _invalid(async_validate_triggers(self.hass, rule["triggers"])):
                errors[CONF_TRIGGERS], placeholders["error"] = "invalid_triggers", error
            elif rule["conditions"] and (error := await _invalid(async_validate_conditions(self.hass, rule["conditions"]))):
                errors[CONF_CONDITIONS], placeholders["error"] = "invalid_conditions", error
            if not errors:
                return await self._async_save_rule(rule)
        others = [name for name in self._machine().names if name != self._rule_state]
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=(current or {}).get("name") or vol.UNDEFINED): TEXT,
                vol.Optional(CONF_FROM, description={"suggested_value": (current or {}).get("from") or []}): _choice(
                    others, multiple=True
                ),
                vol.Required(CONF_TRIGGERS, description={"suggested_value": (current or {}).get("triggers")}): TRIGGERS,
                vol.Optional(
                    CONF_CONDITIONS, description={"suggested_value": (current or {}).get("conditions")}
                ): CONDITIONS,
            }
        )
        return self.async_show_form(
            step_id="trigger_rule", data_schema=schema, errors=errors, description_placeholders=placeholders
        )

    async def async_step_condition_rule(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """While all conditions are true, be in the state (first matching state from the top wins)."""
        current = self._current_rule()
        errors: dict[str, str] = {}
        placeholders = {"state": self._rule_state, "error": ""}
        if user_input is not None:
            rule = {
                "id": self._rule_id or uuid.uuid4().hex,
                "name": (user_input.get(CONF_NAME) or "").strip(),
                "kind": model.KIND_CONDITION,
                "conditions": _as_list(user_input.get(CONF_CONDITIONS)),
            }
            current = rule
            if not rule["name"]:
                errors[CONF_NAME] = "name_required"
            elif not rule["conditions"]:
                errors[CONF_CONDITIONS] = "conditions_required"
            elif error := await _invalid(async_validate_conditions(self.hass, rule["conditions"])):
                errors[CONF_CONDITIONS], placeholders["error"] = "invalid_conditions", error
            if not errors:
                return await self._async_save_rule(rule)
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=(current or {}).get("name") or vol.UNDEFINED): TEXT,
                vol.Required(
                    CONF_CONDITIONS, description={"suggested_value": (current or {}).get("conditions")}
                ): CONDITIONS,
            }
        )
        return self.async_show_form(
            step_id="condition_rule", data_schema=schema, errors=errors, description_placeholders=placeholders
        )

    async def async_step_loop_warning(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """The new rule reads a machine that (through others) reads this one."""
        assert self._pending_rule is not None
        if user_input is not None:
            rule, self._pending_rule = self._pending_rule, None
            self._save(model.upsert_rule(self._raw_machine(), self._rule_state, rule))
            return await self.async_step_rules_list()
        loop = _find_loop(self.hass, self._machine_id, self._with_pending())
        return self.async_show_form(
            step_id="loop_warning",
            data_schema=vol.Schema({}),
            description_placeholders={
                "loop": " → ".join(loop or []),
                "max_flips": str(self._machine().max_flips),
            },
        )

    async def _async_save_rule(self, rule: dict[str, Any]) -> ConfigFlowResult:
        machine = model.upsert_rule(self._raw_machine(), self._rule_state, rule)
        if _find_loop(self.hass, self._machine_id, machine):
            self._pending_rule = rule
            return await self.async_step_loop_warning()
        self._save(machine)
        return await self.async_step_rules_list()

    def _with_pending(self) -> dict[str, Any]:
        machine = self._raw_machine()
        return model.upsert_rule(machine, self._rule_state, self._pending_rule) if self._pending_rule else machine

    def _current_rule(self) -> dict[str, Any] | None:
        if self._rule_id is None:
            return None
        found = model.find_rule(self._raw_machine(), self._rule_id)
        return found[1] if found else None

    # States and settings -----------------------------------------------------

    async def async_step_edit_states(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        raw = self._raw_machine()
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                names = model.clean_state_names(user_input.get(CONF_STATES) or [])
            except model.InvalidStates as err:
                errors[CONF_STATES] = err.key
            else:
                return self._save_and_finish(model.with_states(raw, names))
        schema = vol.Schema({vol.Required(CONF_STATES, default=model.parse(raw).names): STATE_LIST})
        return self.async_show_form(step_id="edit_states", data_schema=schema, errors=errors)

    async def async_step_rename_state(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        raw = self._raw_machine()
        errors: dict[str, str] = {}
        if user_input is not None:
            old, new = user_input[CONF_STATE], user_input[CONF_NEW_NAME].strip()
            try:
                renamed = model.rename_state(raw, old, new)
            except model.InvalidStates as err:
                errors[CONF_NEW_NAME] = err.key
            else:
                async_note_rename(self.hass, self._machine_id, old, new)
                self._move_state_sensor(old, new)
                return self._save_and_finish(renamed)
        schema = vol.Schema(
            {vol.Required(CONF_STATE): _choice(model.parse(raw).names), vol.Required(CONF_NEW_NAME): TEXT}
        )
        return self.async_show_form(step_id="rename_state", data_schema=schema, errors=errors)

    async def async_step_settings(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        raw = self._raw_machine()
        machine = model.parse(raw)
        errors: dict[str, str] = {}
        if user_input is not None:
            name = user_input[CONF_NAME].strip()
            if not name:
                errors[CONF_NAME] = "name_required"
            elif self._name_taken(name, self._machine_id):
                errors[CONF_NAME] = "duplicate_machine"
            else:
                return self._save_and_finish(model.with_settings(
                    raw,
                    name,
                    user_input[CONF_FALLBACK],
                    user_input.get(CONF_PERSON),
                    user_input.get(CONF_MAX_FLIPS, machine.max_flips),
                    user_input.get(CONF_STATE_SENSORS, machine.state_sensors),
                ))
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=machine.name): TEXT,
                vol.Required(CONF_FALLBACK, default=machine.fallback): _choice(machine.names),
                vol.Optional(CONF_PERSON, description={"suggested_value": machine.person}): PERSON,
                vol.Required(CONF_MAX_FLIPS, default=machine.max_flips): MAX_FLIPS,
                vol.Required(CONF_STATE_SENSORS, default=machine.state_sensors): selector.BooleanSelector(),
            }
        )
        return self.async_show_form(step_id="settings", data_schema=schema, errors=errors)

    def _move_state_sensor(self, old: str, new: str) -> None:
        """A renamed state keeps its binary sensor (and its entity id and history)."""
        ent_reg = er.async_get(self.hass)
        old_id = state_sensor_unique_id(self._machine_id, old)
        if entity_id := ent_reg.async_get_entity_id("binary_sensor", DOMAIN, old_id):
            ent_reg.async_update_entity(entity_id, new_unique_id=state_sensor_unique_id(self._machine_id, new))

    async def async_step_icons(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        raw = self._raw_machine()
        machine = model.parse(raw)
        if user_input is not None:
            icons = {state.name: user_input.get(state.name) for state in machine.states}
            return self._save_and_finish(model.with_icons(raw, icons))
        schema = vol.Schema(
            {
                vol.Optional(state.name, description={"suggested_value": state.icon}): selector.IconSelector()
                for state in machine.states
            }
        )
        return self.async_show_form(step_id="icons", data_schema=schema)


# Helpers ------------------------------------------------------------------------


def _as_list(value: Any) -> list[Any]:
    if not value:
        return []
    return list(value) if isinstance(value, list) else [value]


async def _invalid(validation: Any) -> str | None:
    """Run a validation coroutine; the error message if it fails."""
    try:
        await validation
    except (vol.Invalid, HomeAssistantError, ValueError) as err:
        return str(err)
    return None


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _describe_rules(state: model.StateDef) -> str:
    if not state.rules:
        return "No rules yet: this state is entered only by hand."
    lines = []
    for rule in state.rules:
        if rule.kind == model.KIND_TRIGGER:
            where = f" from {', '.join(rule.from_states)}" if rule.from_states else ""
            extra = f", if {_count(len(rule.conditions), 'condition')}" if rule.conditions else ""
            lines.append(f"- **{rule.name}**: trigger rule{where} ({_count(len(rule.triggers), 'trigger')}{extra})")
        else:
            lines.append(f"- **{rule.name}**: condition rule ({_count(len(rule.conditions), 'condition')})")
    return "\n".join(lines)


def _all_machines(hass: HomeAssistant) -> dict[str, tuple[str, dict[str, Any]]]:
    """Every machine of every person or thing: ``"entry_id/machine_id"`` → (entry title, machine data)."""
    return {
        f"{entry.entry_id}/{raw['id']}": (entry.title, raw)
        for entry in hass.config_entries.async_entries(DOMAIN)
        for raw in model.machines_of(entry.options)
    }


def _find_loop(hass: HomeAssistant, machine_id: str, machine: dict[str, Any]) -> list[str] | None:
    """Machine names in a loop through ``machine_id`` (with ``machine`` as its new data), or ``None``."""
    ent_reg = er.async_get(hass)
    owner: dict[str, str] = {}  # entity id → machine id
    machines: dict[str, dict[str, Any]] = {}  # machine id → data
    for entry in hass.config_entries.async_entries(DOMAIN):
        for raw in model.machines_of(entry.options):
            mid = raw["id"]
            machines[mid] = machine if mid == machine_id else raw
            if entity_id := ent_reg.async_get_entity_id("select", DOMAIN, unique_id(mid)):
                owner[entity_id] = mid
    depends_on = {
        mid: {owner[e] for e in model.referenced_entities(raw.get("states") or []) if e in owner}
        for mid, raw in machines.items()
    }
    loop = model.find_loop(depends_on, machine_id)
    return [model.parse(machines[mid]).name for mid in loop] if loop else None
