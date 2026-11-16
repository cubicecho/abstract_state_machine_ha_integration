"""Optional: one binary sensor per state, on while the machine is in it ("Alice hour type sleeping")."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import Context, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .data import AsmConfigEntry, machine_label, state_sensor_unique_id
from .runtime import MachineRuntime


async def async_setup_entry(
    hass: HomeAssistant, entry: AsmConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    thing = entry.runtime_data
    async_add_entities(
        StateSensor(runtime, state, thing.device)
        for runtime in thing.machines
        if runtime.machine.state_sensors
        for state in runtime.machine.names
    )


class StateSensor(BinarySensorEntity):
    """On while the machine is in ``state``. Follows the machine's select; stores nothing itself."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, runtime: MachineRuntime, state: str, device: dr.AnyDeviceEntry | None) -> None:
        self.runtime = runtime
        self.state_name = state
        self._attr_unique_id = state_sensor_unique_id(runtime.machine_id, state)
        if device is not None:
            self.device_entry = device
        label = machine_label(runtime.machine.name, device)
        self._attr_name = f"{label} {state}" if label else state
        state_def = runtime.machine.state(state)
        self._attr_icon = state_def.icon if state_def else None

    @property
    def available(self) -> bool:
        return self.runtime.status is not None

    @property
    def is_on(self) -> bool | None:
        status = self.runtime.status
        return status.state == self.state_name if status else None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.runtime.async_add_listener(self._changed))

    @callback
    def _changed(self, context: Context | None) -> None:
        if context is not None:
            self.async_set_context(context)
        self.async_write_ha_state()
