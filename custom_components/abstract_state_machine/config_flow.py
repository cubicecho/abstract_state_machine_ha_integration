"""Flows.

- Config flow: one entry per person or thing. A new virtual device, an
  existing device, or no device. Creating one goes straight on to its first
  machine.
- Options flow: change where the entry's entities sit.
- Subentry flow ("machine"): add a machine; reconfigure its states, names,
  fallback, person and icons.
"""
from __future__ import annotations

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
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import selector

from . import model
from .const import (
    CONF_DEVICE_ID,
    CONF_FALLBACK,
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
from .runtime import async_note_rename

CONF_NAME = "name"
CONF_STATE = "state"
CONF_NEW_NAME = "new_name"

TEXT = selector.TextSelector()
STATE_LIST = selector.TextSelector(selector.TextSelectorConfig(multiple=True))
PERSON = selector.EntitySelector(selector.EntitySelectorConfig(domain="person"))
DEVICE = selector.DeviceSelector()


def _choice(options: list[str], translation_key: str | None = None) -> selector.SelectSelector:
    config = selector.SelectSelectorConfig(options=options, mode=selector.SelectSelectorMode.DROPDOWN)
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
            menu_options=["edit_states", "rename_state", "settings", "icons"],
            description_placeholders={"machine": self._get_reconfigure_subentry().title},
        )

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
                return self._save(data=model.with_states(subentry.data, names))
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
                return self._save(data=data)
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
                data = {
                    **subentry.data,
                    CONF_FALLBACK: user_input[CONF_FALLBACK],
                    CONF_PERSON: user_input.get(CONF_PERSON) or None,
                }
                return self._save(title=name, data=data)
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=machine.name): TEXT,
                vol.Required(CONF_FALLBACK, default=machine.fallback): _choice(machine.names),
                vol.Optional(CONF_PERSON, description={"suggested_value": machine.person}): PERSON,
            }
        )
        return self.async_show_form(step_id="settings", data_schema=schema, errors=errors)

    async def async_step_icons(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        subentry = self._get_reconfigure_subentry()
        machine = model.parse(subentry.title, subentry.data)
        if user_input is not None:
            icons = {state.name: user_input.get(state.name) for state in machine.states}
            return self._save(data=model.with_icons(subentry.data, icons))
        schema = vol.Schema(
            {
                vol.Optional(state.name, description={"suggested_value": state.icon}): selector.IconSelector()
                for state in machine.states
            }
        )
        return self.async_show_form(step_id="icons", data_schema=schema)

    def _save(self, **changes: Any) -> SubentryFlowResult:
        """Store the change. The entry's update listener reloads it."""
        return self.async_update_and_abort(self._get_entry(), self._get_reconfigure_subentry(), **changes)
