"""Abstract State Machine: user-defined state machines as Home Assistant entities.

One config entry per person or thing: a new virtual device, an existing
device, or no device. Its machines live in the entry's options; each is a
select entity on that device.

See .agents/plan.md for the design.
"""
from __future__ import annotations

import logging

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.typing import ConfigType

from . import model, services
from .const import (
    CONF_DEVICE_ID,
    CONF_PLACEMENT,
    DOMAIN,
    PLACEMENT_EXISTING,
    PLACEMENT_VIRTUAL,
)
from .data import AsmConfigEntry, Thing, wanted_unique_ids
from .runtime import MachineRuntime

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
PLATFORMS = [Platform.SELECT, Platform.BINARY_SENSOR, Platform.SENSOR]

MANUFACTURER = "Abstract State Machine"
VIRTUAL_MODEL = "Virtual device"


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register services once."""
    services.async_register(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: AsmConfigEntry) -> bool:
    """Set up one person or thing and its machines."""
    machines = [MachineRuntime(hass, model.parse(raw)) for raw in model.machines_of(entry.options)]
    entry.runtime_data = Thing(_async_device(hass, entry), machines)
    _async_remove_removed_machines(hass, entry)
    _async_remove_stale_issues(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # After the entities have moved, so removing the old device doesn't take them along.
    _async_remove_unused_virtual_device(hass, entry)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: AsmConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload(hass: HomeAssistant, entry: AsmConfigEntry) -> None:
    """Any change (machines added, edited or removed, placement, title): rebuild."""
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
                "%s: its device is gone, so its machines have no device. Pick another under Configure",
                entry.title,
            )
        return device
    return None


def _async_remove_removed_machines(hass: HomeAssistant, entry: AsmConfigEntry) -> None:
    """Drop the registry entries of removed machines, and of state sensors that were turned off or removed."""
    ent_reg = er.async_get(hass)
    wanted = wanted_unique_ids(entry.runtime_data)
    for reg_entry in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
        if reg_entry.unique_id not in wanted:
            ent_reg.async_remove(reg_entry.entity_id)


def _async_remove_stale_issues(hass: HomeAssistant) -> None:
    """Repair issues of machines that no longer exist (removed, or their entry deleted)."""
    machine_ids = {
        raw["id"] for entry in hass.config_entries.async_entries(DOMAIN) for raw in model.machines_of(entry.options)
    }
    for domain, issue_id in list(ir.async_get(hass).issues):
        if domain != DOMAIN:
            continue
        for prefix in ("broken_rules_", "flip_limit_"):
            if issue_id.startswith(prefix) and issue_id.removeprefix(prefix) not in machine_ids:
                ir.async_delete_issue(hass, DOMAIN, issue_id)


def _async_remove_unused_virtual_device(hass: HomeAssistant, entry: AsmConfigEntry) -> None:
    if entry.options.get(CONF_PLACEMENT) == PLACEMENT_VIRTUAL:
        return
    dev_reg = dr.async_get(hass)
    if device := dev_reg.async_get_device_by_identifier((DOMAIN, entry.entry_id), entry.entry_id):
        dev_reg.async_remove_device(device.id)
