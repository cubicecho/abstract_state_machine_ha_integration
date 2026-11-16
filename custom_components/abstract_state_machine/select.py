"""The machine's state, as a select entity."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.core import Context, HomeAssistant, ServiceResponse, State, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import ExtraStoredData, RestoreEntity
from homeassistant.helpers.start import async_at_started
from homeassistant.util import dt as dt_util

from . import engine
from .const import (
    ATTR_CHANGED_AT,
    ATTR_FLIP_LIMITED,
    ATTR_MANUAL,
    ATTR_OVERRIDE,
    ATTR_OVERRIDE_UNTIL,
    ATTR_PERSON,
    ATTR_PREVIOUS,
    ATTR_REASON,
    ATTR_RULES_STATE,
)
from .data import AsmConfigEntry, machine_label, unique_id
from .runtime import MachineRuntime
from .timeinstate import TimeInState


async def async_setup_entry(
    hass: HomeAssistant, entry: AsmConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    thing = entry.runtime_data
    async_add_entities([MachineSelect(runtime, thing.device) for runtime in thing.machines])


@dataclass
class StoredStatus(ExtraStoredData):
    """The full status, holds included, and today's time in each state, kept across restarts."""

    status: engine.Status
    time: TimeInState | None = None

    def as_dict(self) -> dict[str, Any]:
        s = self.status
        return {
            "state": s.state,
            "previous": s.previous,
            "reason": s.reason,
            "changed_at": s.changed_at.isoformat(),
            "manual": s.manual,
            "held": s.held,
            "held_against": s.held_against,
            "hold_until": s.hold_until.isoformat() if s.hold_until else None,
            "time": self.time.as_dict() if self.time else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StoredStatus | None:
        try:
            changed_at = dt_util.parse_datetime(data["changed_at"])
            hold_until = dt_util.parse_datetime(data["hold_until"]) if data.get("hold_until") else None
            if changed_at is None:
                return None
            return cls(engine.Status(
                state=str(data["state"]),
                previous=data.get("previous"),
                reason=str(data.get("reason") or engine.REASON_RESTORED),
                changed_at=changed_at,
                manual=bool(data.get("manual")),
                held=bool(data.get("held")),
                held_against=data.get("held_against"),
                hold_until=hold_until,
            ), TimeInState.from_dict(data.get("time")) if data.get("time") else None)
        except (KeyError, TypeError, ValueError):
            return None


class MachineSelect(SelectEntity, RestoreEntity):
    """Shows the state; picking an option sets it by hand.

    The device is set through ``device_entry``: the virtual device is made in
    ``async_setup_entry``, and an existing device belongs to another integration.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, runtime: MachineRuntime, device: dr.AnyDeviceEntry | None) -> None:
        self.runtime = runtime
        self._attr_unique_id = unique_id(runtime.machine_id)
        self._attr_options = runtime.machine.names
        if device is not None:
            self.device_entry = device

    @property
    def name(self) -> str | None:  # type: ignore[override]
        return machine_label(self.runtime.machine.name, self.device_entry)

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
        if self.runtime.machine.has_condition_rules:
            computed = self.runtime.computed
            attrs[ATTR_RULES_STATE] = computed.state if computed else None
            attrs[ATTR_OVERRIDE] = status.held
            attrs[ATTR_OVERRIDE_UNTIL] = status.hold_until.isoformat() if status.held and status.hold_until else None
        if self.runtime.flip_limited:
            attrs[ATTR_FLIP_LIMITED] = True
        if self.runtime.machine.person:
            attrs[ATTR_PERSON] = self.runtime.machine.person
        return attrs

    @property
    def extra_restore_state_data(self) -> StoredStatus | None:
        status = self.runtime.status
        return StoredStatus(status, self.runtime.time) if status else None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.runtime.entity_id = self.entity_id
        self.async_on_remove(self.runtime.async_add_listener(self._changed))
        self.runtime.async_restore(*await self._async_restored())

        async def start(_hass: HomeAssistant) -> None:
            await self.runtime.async_start()

        self.async_on_remove(async_at_started(self.hass, start))
        self.async_on_remove(self.runtime.async_stop)

    async def _async_restored(self) -> tuple[engine.Status | None, TimeInState | None]:
        extra = await self.async_get_last_extra_data()
        if extra is not None and (stored := StoredStatus.from_dict(extra.as_dict())) is not None:
            return stored.status, stored.time
        return _from_state(await self.async_get_last_state()), None

    @callback
    def _changed(self, context: Context | None) -> None:
        if context is not None:
            self.async_set_context(context)
        self.async_write_ha_state()

    async def async_select_option(self, option: str) -> None:
        self.runtime.async_set_state(option, context=self._context)

    async def async_set_machine_state(
        self, state: str, reason: str | None = None, hold: timedelta | None = None
    ) -> None:
        """``abstract_state_machine.set_state``: like picking an option, with a reason and an optional hold."""
        self.runtime.async_set_state(state, reason=reason or engine.REASON_MANUAL, hold=hold, context=self._context)

    async def async_clear_override(self) -> None:
        """``abstract_state_machine.clear_override``."""
        self.runtime.async_clear_override(context=self._context)

    async def async_explain(self) -> ServiceResponse:
        """``abstract_state_machine.explain``: what each rule says right now."""
        return self.runtime.explain()


def _from_state(last: State | None) -> engine.Status | None:
    """Fallback when there's no stored status: rebuild one from the last state's attributes."""
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
