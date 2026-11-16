"""Set up people/things (entries) and machines directly, without the flows."""
from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.abstract_state_machine import model
from custom_components.abstract_state_machine.const import (
    CONF_DEVICE_ID,
    CONF_MACHINES,
    CONF_PLACEMENT,
    DOMAIN,
    PLACEMENT_EXISTING,
    PLACEMENT_VIRTUAL,
)

DISHWASHER_STATES = ["cleared", "loaded", "running", "clean"]


def _slug(text: str) -> str:
    return text.lower().replace(" ", "_")


def machine(
    name: str, states: list[str], fallback: str | None = None, person: str | None = None, machine_id: str | None = None
) -> dict[str, Any]:
    return model.new_machine_data(name, states, fallback, person, machine_id=machine_id or f"m_{_slug(name)}")


async def add_thing(
    hass: HomeAssistant,
    title: str,
    machines: list[dict[str, Any]],
    placement: str = PLACEMENT_VIRTUAL,
    device_id: str | None = None,
) -> MockConfigEntry:
    """One person or thing (an entry) with its machines, set up."""
    options: dict[str, Any] = {CONF_PLACEMENT: placement, CONF_MACHINES: machines}
    if placement == PLACEMENT_EXISTING:
        options[CONF_DEVICE_ID] = device_id
    entry = MockConfigEntry(domain=DOMAIN, title=title, options=options)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def machine_data(entry: MockConfigEntry, name: str | None = None) -> dict[str, Any]:
    """A machine's stored data: the only one, or by name."""
    found = model.machines_of(entry.options)
    if name is None:
        (only,) = found
        return only
    return next(m for m in found if m["name"] == name)
