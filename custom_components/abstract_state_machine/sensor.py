"""Optional: time in each state today, one sensor per state ("Alice hour type working today")."""
from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import UnitOfTime
from homeassistant.core import Context, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .data import AsmConfigEntry, machine_label, time_sensor_unique_id
from .runtime import MachineRuntime

UPDATE_INTERVAL = timedelta(minutes=1)


async def async_setup_entry(
    hass: HomeAssistant, entry: AsmConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    thing = entry.runtime_data
    async_add_entities(
        TimeInStateSensor(runtime, state, thing.device)
        for runtime in thing.machines
        if runtime.machine.time_sensors
        for state in runtime.machine.names
    )


class TimeInStateSensor(SensorEntity):
    """Hours in one state since local midnight. Counts from the runtime's tracker; stores nothing itself."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.HOURS
    _attr_state_class = SensorStateClass.TOTAL_INCREASING  # drops to 0 at midnight: a reset
    _attr_suggested_display_precision = 2

    def __init__(self, runtime: MachineRuntime, state: str, device: dr.AnyDeviceEntry | None) -> None:
        self.runtime = runtime
        self.state_name = state
        self._attr_unique_id = time_sensor_unique_id(runtime.machine_id, state)
        if device is not None:
            self.device_entry = device
        label = machine_label(runtime.machine.name, device)
        self._attr_name = f"{label} {state} today" if label else f"{state} today"
        state_def = runtime.machine.state(state)
        self._attr_icon = state_def.icon if state_def and state_def.icon else "mdi:timer-outline"

    @property
    def available(self) -> bool:
        return self.runtime.time is not None

    @property
    def native_value(self) -> float | None:
        time = self.runtime.time
        return round(time.seconds(self.state_name, dt_util.now()) / 3600, 4) if time else None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.runtime.async_add_listener(self._changed))
        self.async_on_remove(async_track_time_interval(self.hass, self._tick, UPDATE_INTERVAL))

    @callback
    def _changed(self, context: Context | None) -> None:
        self.async_write_ha_state()

    @callback
    def _tick(self, _now: datetime) -> None:
        self.async_write_ha_state()
