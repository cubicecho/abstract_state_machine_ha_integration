"""The machine's state, as a select entity."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.core import Context, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from . import engine
from .const import ATTR_CHANGED_AT, ATTR_MANUAL, ATTR_PERSON, ATTR_PREVIOUS, ATTR_REASON
from .data import AsmConfigEntry
from .entity import MachineEntity
from .runtime import MachineRuntime


async def async_setup_entry(
    hass: HomeAssistant, entry: AsmConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    data = entry.runtime_data
    for subentry_id, runtime in data.machines.items():
        async_add_entities([MachineSelect(runtime, data.device)], config_subentry_id=subentry_id)


class MachineSelect(MachineEntity, SelectEntity, RestoreEntity):
    """Shows the state; picking an option sets it by hand."""

    def __init__(self, runtime: MachineRuntime, device: Any) -> None:
        super().__init__(runtime, device, "state")
        self._attr_options = runtime.machine.names

    @property
    def name(self) -> str | None:  # type: ignore[override]
        return self._machine_name()

    @property
    def current_option(self) -> str | None:
        status = self.runtime.status
        return status.state if status else None

    @property
    def icon(self) -> str | None:  # type: ignore[override]
        status = self.runtime.status
        state = self.runtime.machine.state(status.state) if status else None
        return state.icon if state else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        status = self.runtime.status
        if status is None:
            return {}
        attrs: dict[str, Any] = {
            ATTR_PREVIOUS: status.previous,
            ATTR_REASON: status.reason,
            ATTR_CHANGED_AT: status.changed_at.isoformat(),
            ATTR_MANUAL: status.manual,
        }
        if self.runtime.machine.person:
            attrs[ATTR_PERSON] = self.runtime.machine.person
        return attrs

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.runtime.entity_id = self.entity_id
        self.async_on_remove(self.runtime.async_add_listener(self._changed))
        self.runtime.async_restore(_restored(await self.async_get_last_state()))

    @callback
    def _changed(self, transition: engine.Transition | None, context: Context | None) -> None:
        if context is not None:
            self.async_set_context(context)
        self.async_write_ha_state()

    async def async_select_option(self, option: str) -> None:
        self.runtime.async_set_state(option, context=self._context)

    async def async_set_machine_state(self, state: str, reason: str | None = None) -> None:
        """``abstract_state_machine.set_state``: like picking an option, with a reason."""
        self.runtime.async_set_state(state, reason=reason or engine.REASON_MANUAL, context=self._context)


def _restored(last: Any) -> engine.Status | None:
    """The status saved before the restart, if there was one."""
    if last is None or last.state in (None, "unknown", "unavailable"):
        return None
    changed = last.attributes.get(ATTR_CHANGED_AT)
    changed_at = dt_util.parse_datetime(changed) if isinstance(changed, str) else None
    return engine.Status(
        state=last.state,
        previous=last.attributes.get(ATTR_PREVIOUS),
        reason=str(last.attributes.get(ATTR_REASON) or engine.REASON_RESTORED),
        changed_at=changed_at or _as_datetime(last.last_changed),
        manual=bool(last.attributes.get(ATTR_MANUAL, False)),
    )


def _as_datetime(value: Any) -> datetime:
    return value if isinstance(value, datetime) else dt_util.utcnow()
