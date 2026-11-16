"""The activity log says why a machine changed: "Day type moved to holiday (PTO today)".

The runtime fires ``EVENT_RULE_APPLIED`` in a new context and writes the
state change in the same context, the way automations do. Describing the
event here gives the log the rule's name.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.components.logbook import (
    LOGBOOK_ENTRY_ENTITY_ID,
    LOGBOOK_ENTRY_ICON,
    LOGBOOK_ENTRY_MESSAGE,
    LOGBOOK_ENTRY_NAME,
    LOGBOOK_ENTRY_SOURCE,
    LazyEventPartialState,
)
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN, EVENT_RULE_APPLIED


@callback
def async_describe_events(
    hass: HomeAssistant,
    async_describe_event: Callable[[str, str, Callable[[LazyEventPartialState], dict[str, Any]]], None],
) -> None:
    @callback
    def describe(event: LazyEventPartialState) -> dict[str, Any]:
        data = event.data
        return {
            LOGBOOK_ENTRY_NAME: data.get("machine"),
            LOGBOOK_ENTRY_MESSAGE: f"moved to {data.get('to')} ({data.get('reason')})",
            LOGBOOK_ENTRY_SOURCE: data.get("reason"),
            LOGBOOK_ENTRY_ENTITY_ID: data.get("entity_id"),
            LOGBOOK_ENTRY_ICON: "mdi:state-machine",
        }

    async_describe_event(DOMAIN, EVENT_RULE_APPLIED, describe)
