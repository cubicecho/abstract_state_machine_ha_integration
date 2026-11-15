"""Flows.

- Config flow: one entry per person or thing. A new virtual device, an
  existing device, or no device. Creating one goes straight on to its first
  machine.
- Options flow: change where the entry's entities sit.
- Subentry flow ("machine"): add a machine; reconfigure its rules, states,
  names, fallback, person, flip limit and icons.
"""
from __future__ import annotations

import uuid
from typing import Any

import probatio as vol  # HA 2026.10+ validates with probatio
from homeassistant.config_entries import (
    SOURCE_USER,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    FlowType,
    OptionsFlow,
    SubentryFlowContext,
    SubentryFlowResult,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector

from . import model
from .const import (
    CONF_DEVICE_ID,
    CONF_FALLBACK,
    CONF_MAX_FLIPS,
    CONF_PERSON,
    CONF_PLACEMENT,
    CONF_STATES,
    DOMAIN,
    PLACEMENT_EXISTING,
    PLACEMENT_NONE,
    PLACEMENT_VIRTUAL,
    PLACEMENTS,
    SUBENTRY_MACHINE,
)
from .runtime import async_note_rename, async_validate_conditions, async_validate_triggers

CONF_NAME = "name"
CONF_STATE = "state"
CONF_NEW_NAME = "new_name"
CONF_ACTION = "action"
CONF_FROM = "from"
CONF_TRIGGERS = "triggers"
CONF_CONDITIONS = "conditions"

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


# Config flow: the person or thing --------------------------------------------


class AbstractStateMachineConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add a person or thing that machines live on."""

    VERSION = 1

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
                return self.async_create_entry(title=name, data={}, options={CONF_PLACEMENT: placement})
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
                return self.async_create_entry(
                    title=_device_name(device),
                    data={},
                    options={CONF_PLACEMENT: PLACEMENT_EXISTING, CONF_DEVICE_ID: device.id},
                )
        return self.async_show_form(
            step_id=PLACEMENT_EXISTING,
            data_schema=vol.Schema({vol.Required(CONF_DEVICE_ID): DEVICE}),
            errors=errors,
        )

    async def async_on_create_entry(self, result: ConfigFlowResult) -> ConfigFlowResult:
        """Go straight on to the first machine."""
        sub = await self.hass.config_entries.subentries.async_init(
            (result["result"].entry_id, SUBENTRY_MACHINE),
            context=SubentryFlowContext(source=SOURCE_USER),
        )
        result["next_flow"] = (FlowType.CONFIG_SUBENTRIES_FLOW, sub["flow_id"])
        return result

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return PlacementOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(cls, config_entry: ConfigEntry) -> dict[str, type[ConfigSubentryFlow]]:
        return {SUBENTRY_MACHINE: MachineSubentryFlow}


# Options flow: placement ------------------------------------------------------


class PlacementOptionsFlow(OptionsFlow):
    """Move the entry's machines to another device, or off devices."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        options = self.config_entry.options
        if user_input is not None:
            placement = user_input[CONF_PLACEMENT]
            new: dict[str, Any] = {CONF_PLACEMENT: placement}
            if placement == PLACEMENT_EXISTING:
                device_id = user_input.get(CONF_DEVICE_ID)
                if not device_id:
                    errors[CONF_DEVICE_ID] = "device_required"
                elif dr.async_get(self.hass).async_get(device_id) is None:
                    errors[CONF_DEVICE_ID] = "device_not_found"
                new[CONF_DEVICE_ID] = device_id
            if not errors:
                return self.async_create_entry(data=new)
        schema = vol.Schema(
            {
                vol.Required(CONF_PLACEMENT, default=options.get(CONF_PLACEMENT, PLACEMENT_VIRTUAL)): _choice(
                    list(PLACEMENTS), CONF_PLACEMENT
                ),
                vol.Optional(CONF_DEVICE_ID, description={"suggested_value": options.get(CONF_DEVICE_ID)}): DEVICE,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)


# Subentry flow: one machine ---------------------------------------------------


class MachineSubentryFlow(ConfigSubentryFlow):
    """Add or edit one machine."""

    def __init__(self) -> None:
        self._name = ""
        self._names: list[str] = []
        self._person: str | None = None
        self._rule_state = ""  # the state whose rules are being edited
        self._rule_id: str | None = None  # the rule being edited; None = a new one
        self._pending_rule: dict[str, Any] | None = None  # waiting for the loop warning

    # Add -----------------------------------------------------------------

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._name = user_input[CONF_NAME].strip()
            self._person = user_input.get(CONF_PERSON) or None
            try:
                self._names = model.clean_state_names(user_input.get(CONF_STATES) or [])
            except model.InvalidStates as err:
                errors[CONF_STATES] = err.key
            if not self._name:
                errors[CONF_NAME] = "name_required"
            if not errors:
                return await self.async_step_fallback()
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=self._name or vol.UNDEFINED): TEXT,
                vol.Required(CONF_STATES, default=self._names or vol.UNDEFINED): STATE_LIST,
                vol.Optional(CONF_PERSON, description={"suggested_value": self._person}): PERSON,
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
            description_placeholders={"entry": self._get_entry().title},
        )

    async def async_step_fallback(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                title=self._name,
                data=model.new_machine_data(self._names, user_input[CONF_FALLBACK], self._person),
            )
        schema = vol.Schema({vol.Required(CONF_FALLBACK, default=self._names[0]): _choice(self._names)})
        return self.async_show_form(step_id="fallback", data_schema=schema)

    # Reconfigure ---------------------------------------------------------

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return self.async_show_menu(
            step_id="reconfigure",
            menu_options=["rules", "edit_states", "rename_state", "settings", "icons"],
            description_placeholders={"machine": self._get_reconfigure_subentry().title},
        )

    # Rules -----------------------------------------------------------------

    async def async_step_rules(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
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

    async def async_step_rules_list(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """One state's rules: add, edit or delete."""
        machine = self._machine()
        state = machine.state(self._rule_state)
        if state is None:  # renamed or removed meanwhile
            return await self.async_step_rules()
        if user_input is not None:
            action: str = user_input[CONF_ACTION]
            if action == "done":
                return self.async_abort(reason="reconfigure_successful")
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
                self._save(model.remove_rule(self._get_reconfigure_subentry().data, rule_id))
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
            options.append({"value": f"edit:{rule.id}", "label": f"Edit \u201c{rule.name}\u201d"})
        for rule in state.rules:
            options.append({"value": f"delete:{rule.id}", "label": f"Delete \u201c{rule.name}\u201d"})
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

    async def async_step_trigger_rule(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
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

    async def async_step_condition_rule(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
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

    async def async_step_loop_warning(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """The new rule reads a machine that (through others) reads this one."""
        assert self._pending_rule is not None
        if user_input is not None:
            rule, self._pending_rule = self._pending_rule, None
            self._save(model.upsert_rule(self._get_reconfigure_subentry().data, self._rule_state, rule))
            return await self.async_step_rules_list()
        loop = _find_loop(self.hass, self._get_reconfigure_subentry().subentry_id, self._with_pending())
        return self.async_show_form(
            step_id="loop_warning",
            data_schema=vol.Schema({}),
            description_placeholders={
                "loop": " \u2192 ".join(loop or []),
                "max_flips": str(self._machine().max_flips),
            },
        )

    async def _async_save_rule(self, rule: dict[str, Any]) -> SubentryFlowResult:
        subentry = self._get_reconfigure_subentry()
        data = model.upsert_rule(subentry.data, self._rule_state, rule)
        if _find_loop(self.hass, subentry.subentry_id, data):
            self._pending_rule = rule
            return await self.async_step_loop_warning()
        self._save(data)
        return await self.async_step_rules_list()

    def _with_pending(self) -> dict[str, Any]:
        data = self._get_reconfigure_subentry().data
        return model.upsert_rule(data, self._rule_state, self._pending_rule) if self._pending_rule else dict(data)

    def _current_rule(self) -> dict[str, Any] | None:
        if self._rule_id is None:
            return None
        found = model.find_rule(self._get_reconfigure_subentry().data, self._rule_id)
        return found[1] if found else None

    def _save(self, data: dict[str, Any]) -> None:
        """Store a rule change and stay in the flow. The update listener reloads the entry."""
        self.hass.config_entries.async_update_subentry(self._get_entry(), self._get_reconfigure_subentry(), data=data)

    def _machine(self) -> model.MachineDef:
        subentry = self._get_reconfigure_subentry()
        return model.parse(subentry.title, subentry.data)

    # States and settings -----------------------------------------------------

    async def async_step_edit_states(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        subentry = self._get_reconfigure_subentry()
        current = model.parse(subentry.title, subentry.data).names
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                names = model.clean_state_names(user_input.get(CONF_STATES) or [])
            except model.InvalidStates as err:
                errors[CONF_STATES] = err.key
            else:
                return self._save_and_finish(data=model.with_states(subentry.data, names))
        schema = vol.Schema({vol.Required(CONF_STATES, default=current): STATE_LIST})
        return self.async_show_form(step_id="edit_states", data_schema=schema, errors=errors)

    async def async_step_rename_state(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        subentry = self._get_reconfigure_subentry()
        names = model.parse(subentry.title, subentry.data).names
        errors: dict[str, str] = {}
        if user_input is not None:
            old, new = user_input[CONF_STATE], user_input[CONF_NEW_NAME].strip()
            try:
                data = model.rename_state(subentry.data, old, new)
            except model.InvalidStates as err:
                errors[CONF_NEW_NAME] = err.key
            else:
                async_note_rename(self.hass, subentry.subentry_id, old, new)
                return self._save_and_finish(data=data)
        schema = vol.Schema(
            {vol.Required(CONF_STATE): _choice(names), vol.Required(CONF_NEW_NAME): TEXT}
        )
        return self.async_show_form(step_id="rename_state", data_schema=schema, errors=errors)

    async def async_step_settings(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        subentry = self._get_reconfigure_subentry()
        machine = model.parse(subentry.title, subentry.data)
        errors: dict[str, str] = {}
        if user_input is not None:
            name = user_input[CONF_NAME].strip()
            if not name:
                errors[CONF_NAME] = "name_required"
            else:
                data = model.with_settings(
                    subentry.data,
                    user_input[CONF_FALLBACK],
                    user_input.get(CONF_PERSON),
                    user_input.get(CONF_MAX_FLIPS, machine.max_flips),
                )
                return self._save_and_finish(title=name, data=data)
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=machine.name): TEXT,
                vol.Required(CONF_FALLBACK, default=machine.fallback): _choice(machine.names),
                vol.Optional(CONF_PERSON, description={"suggested_value": machine.person}): PERSON,
                vol.Required(CONF_MAX_FLIPS, default=machine.max_flips): MAX_FLIPS,
            }
        )
        return self.async_show_form(step_id="settings", data_schema=schema, errors=errors)

    async def async_step_icons(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        subentry = self._get_reconfigure_subentry()
        machine = model.parse(subentry.title, subentry.data)
        if user_input is not None:
            icons = {state.name: user_input.get(state.name) for state in machine.states}
            return self._save_and_finish(data=model.with_icons(subentry.data, icons))
        schema = vol.Schema(
            {
                vol.Optional(state.name, description={"suggested_value": state.icon}): selector.IconSelector()
                for state in machine.states
            }
        )
        return self.async_show_form(step_id="icons", data_schema=schema)

    def _save_and_finish(self, **changes: Any) -> SubentryFlowResult:
        """Store the change and close. The entry's update listener reloads it."""
        return self.async_update_and_abort(self._get_entry(), self._get_reconfigure_subentry(), **changes)


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


def _find_loop(hass: HomeAssistant, subentry_id: str, data: dict[str, Any]) -> list[str] | None:
    """Machine names in a loop through ``subentry_id`` (with ``data`` as its new data), or ``None``."""
    ent_reg = er.async_get(hass)
    owner: dict[str, str] = {}  # entity id → subentry id
    machines: dict[str, tuple[str, Any]] = {}  # subentry id → (title, data)
    for entry in hass.config_entries.async_entries(DOMAIN):
        for sid, sub in entry.subentries.items():
            if sub.subentry_type != SUBENTRY_MACHINE:
                continue
            machines[sid] = (sub.title, data if sid == subentry_id else sub.data)
            for platform, key in (("select", "state"), ("event", "transition")):
                if entity_id := ent_reg.async_get_entity_id(platform, DOMAIN, f"{sid}_{key}"):
                    owner[entity_id] = sid
    depends_on = {
        sid: {owner[e] for e in model.referenced_entities(raw.get("states") or []) if e in owner}
        for sid, (_, raw) in machines.items()
    }
    loop = model.find_loop(depends_on, subentry_id)
    return [machines[sid][0] for sid in loop] if loop else None
