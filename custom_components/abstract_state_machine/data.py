"""Runtime data shared by the entry's platforms."""
from __future__ import annotations

from dataclasses import dataclass, field

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import device_registry as dr

from .runtime import MachineRuntime


@dataclass
class AsmData:
    """One config entry: the device its machines sit on, and the machines."""

    device: dr.AnyDeviceEntry | None
    machines: dict[str, MachineRuntime] = field(default_factory=dict)  # by subentry id


type AsmConfigEntry = ConfigEntry[AsmData]
