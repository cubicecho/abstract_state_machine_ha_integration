"""Shared entity bits: naming and which device a machine's entities sit on."""
from __future__ import annotations

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import Entity

from .runtime import MachineRuntime


class MachineEntity(Entity):
    """Base for a machine's entities.

    The device is set through ``device_entry``, not ``device_info``: a
    ``device_info`` would make one device per subentry, and an existing
    device belongs to another integration's config entry.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, runtime: MachineRuntime, device: dr.AnyDeviceEntry | None, key: str) -> None:
        self.runtime = runtime
        self._attr_unique_id = f"{runtime.subentry_id}_{key}"
        if device is not None:
            self.device_entry = device

    def _machine_name(self, suffix: str | None = None) -> str | None:
        """The machine's name, or ``None`` when it equals the device's name (no "Dishwasher Dishwasher")."""
        name = self.runtime.machine.name
        device = self.device_entry
        same = device is not None and (device.name_by_user or device.name or "").casefold() == name.casefold()
        if suffix is None:
            return None if same else name
        return suffix.capitalize() if same else f"{name} {suffix}"
