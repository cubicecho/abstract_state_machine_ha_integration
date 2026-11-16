"""Runtime data shared by the entry's platforms."""
from __future__ import annotations

from dataclasses import dataclass, field

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import device_registry as dr

from .runtime import MachineRuntime


@dataclass
class Thing:
    """One person or thing (a config entry): the device its machines sit on, and the machines."""

    device: dr.AnyDeviceEntry | None
    machines: list[MachineRuntime] = field(default_factory=list)


def unique_id(machine_id: str) -> str:
    """A machine's select entity's unique id."""
    return f"{machine_id}_state"


def state_sensor_unique_id(machine_id: str, state: str) -> str:
    """The unique id of a machine's binary sensor for one state."""
    return f"{machine_id}_is_{state}"


def time_sensor_unique_id(machine_id: str, state: str) -> str:
    """The unique id of a machine's "time in this state today" sensor."""
    return f"{machine_id}_time_{state}"


def wanted_unique_ids(thing: Thing) -> set[str]:
    """Every entity this entry should have."""
    wanted: set[str] = set()
    for runtime in thing.machines:
        wanted.add(unique_id(runtime.machine_id))
        if runtime.machine.state_sensors:
            wanted.update(state_sensor_unique_id(runtime.machine_id, name) for name in runtime.machine.names)
        if runtime.machine.time_sensors:
            wanted.update(time_sensor_unique_id(runtime.machine_id, name) for name in runtime.machine.names)
    return wanted


def machine_label(name: str, device: dr.AnyDeviceEntry | None) -> str | None:
    """A machine's entity name, or ``None`` when it equals the device's (no "Dishwasher Dishwasher")."""
    if device is not None and (device.name_by_user or device.name or "").casefold() == name.casefold():
        return None
    return name


type AsmConfigEntry = ConfigEntry[Thing]
