"""Abstract State Machine: user-defined state machines as Home Assistant entities.

One config entry is one person or thing: a new virtual device, an existing
device, or no device. Each machine is a config subentry of it, with a
select entity (its state) and an event entity (its transitions).

See .agents/plan.md for the design.
"""
from __future__ import annotations

import logging

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.typing import ConfigType

from . import model, services
from .const import (
    CONF_DEVICE_ID,
    CONF_PLACEMENT,
    DOMAIN,
    PLACEMENT_EXISTING,
    PLACEMENT_VIRTUAL,
    SUBENTRY_MACHINE,
)
from .data import AsmConfigEntry, AsmData
from .runtime import MachineRuntime

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
PLATFORMS = [Platform.SELECT, Platform.EVENT]

MANUFACTURER = "Abstract State Machine"
VIRTUAL_MODEL = "Virtual device"


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register services once."""
    services.async_register(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: AsmConfigEntry) -> bool:
    """Set up one person or thing and its machines."""
    machines = {
        subentry_id: MachineRuntime(hass, subentry_id, model.parse(subentry.title, subentry.data))
        for subentry_id, subentry in entry.subentries.items()
        if subentry.subentry_type == SUBENTRY_MACHINE
    }
    entry.runtime_data = AsmData(device=_async_device(hass, entry), machines=machines)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # After the entities have moved, so removing the old device doesn't take them along.
    _async_remove_unused_virtual_device(hass, entry)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: AsmConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload(hass: HomeAssistant, entry: AsmConfigEntry) -> None:
    """Any change (machines added or edited, placement, title): rebuild."""
    hass.config_entries.async_schedule_reload(entry.entry_id)


def _async_device(hass: HomeAssistant, entry: AsmConfigEntry) -> dr.AnyDeviceEntry | None:
    """The device this entry's entities sit on, creating the virtual one if needed."""
    dev_reg = dr.async_get(hass)
    placement = entry.options.get(CONF_PLACEMENT)
    if placement == PLACEMENT_VIRTUAL:
        return dev_reg.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer=MANUFACTURER,
            model=VIRTUAL_MODEL,
        )
    if placement == PLACEMENT_EXISTING:
        device = dev_reg.async_get(entry.options.get(CONF_DEVICE_ID, ""))
        if device is None:
            _LOGGER.warning(
                "%s: its device is gone, so its machines have no device. "
                "Pick another in the entry's options",
                entry.title,
            )
        return device
    return None


def _async_remove_unused_virtual_device(hass: HomeAssistant, entry: AsmConfigEntry) -> None:
    if entry.options.get(CONF_PLACEMENT) == PLACEMENT_VIRTUAL:
        return
    dev_reg = dr.async_get(hass)
    if device := dev_reg.async_get_device_by_identifier((DOMAIN, entry.entry_id), entry.entry_id):
        dev_reg.async_remove_device(device.id)
