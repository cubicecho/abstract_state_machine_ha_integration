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


type AsmConfigEntry = ConfigEntry[Thing]
