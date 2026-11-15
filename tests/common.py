"""Set up entries and machines directly, without the flows."""
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigSubentryDataWithId
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.abstract_state_machine import model
from custom_components.abstract_state_machine.const import (
    CONF_DEVICE_ID,
    CONF_PLACEMENT,
    DOMAIN,
    PLACEMENT_EXISTING,
    PLACEMENT_VIRTUAL,
    SUBENTRY_MACHINE,
)

DISHWASHER_STATES = ["cleared", "loaded", "running", "clean"]


def machine(
    title: str, states: list[str], fallback: str | None = None, person: str | None = None, sub_id: str | None = None
) -> ConfigSubentryDataWithId:
    return ConfigSubentryDataWithId(
        data=model.new_machine_data(states, fallback, person),
        subentry_type=SUBENTRY_MACHINE,
        title=title,
        unique_id=None,
        subentry_id=sub_id or f"sub_{title.lower().replace(' ', '_')}",
    )


async def add_entry(
    hass: HomeAssistant,
    title: str,
    machines: list[ConfigSubentryDataWithId],
    placement: str = PLACEMENT_VIRTUAL,
    device_id: str | None = None,
) -> MockConfigEntry:
    options: dict[str, Any] = {CONF_PLACEMENT: placement}
    if placement == PLACEMENT_EXISTING:
        options[CONF_DEVICE_ID] = device_id
    entry = MockConfigEntry(domain=DOMAIN, title=title, options=options, subentries_data=machines)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry
