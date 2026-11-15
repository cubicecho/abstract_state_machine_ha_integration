"""One event per change of a machine's state."""
from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.core import Context, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import engine
from .const import ATTR_FROM, ATTR_MANUAL, ATTR_REASON, ATTR_TO
from .data import AsmConfigEntry
from .entity import MachineEntity
from .runtime import MachineRuntime


async def async_setup_entry(
    hass: HomeAssistant, entry: AsmConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    data = entry.runtime_data
    for subentry_id, runtime in data.machines.items():
        async_add_entities([MachineTransition(runtime, data.device)], config_subentry_id=subentry_id)


class MachineTransition(MachineEntity, EventEntity):
    """The event type is the state entered; attributes say where from and why."""

    _attr_icon = "mdi:state-machine"

    def __init__(self, runtime: MachineRuntime, device: Any) -> None:
        super().__init__(runtime, device, "transition")
        self._attr_event_types = runtime.machine.names

    @property
    def name(self) -> str | None:  # type: ignore[override]
        return self._machine_name("transition")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.runtime.async_add_listener(self._changed))

    @callback
    def _changed(self, transition: engine.Transition | None, context: Context | None) -> None:
        if transition is None:  # restored on startup, not a change
            return
        if context is not None:
            self.async_set_context(context)
        self._trigger_event(
            transition.to_state,
            {
                ATTR_FROM: transition.from_state,
                ATTR_TO: transition.to_state,
                ATTR_REASON: transition.reason,
                ATTR_MANUAL: transition.manual,
            },
        )
        self.async_write_ha_state()
