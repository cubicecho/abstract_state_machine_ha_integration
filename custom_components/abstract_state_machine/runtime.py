"""One running machine: holds its status, applies changes, fires events."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from homeassistant.core import Context, HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util
from homeassistant.util.hass_dict import HassKey

from . import engine
from .const import (
    ATTR_FROM,
    ATTR_MACHINE,
    ATTR_MANUAL,
    ATTR_REASON,
    ATTR_TO,
    DOMAIN,
    EVENT_TRANSITION,
)
from .model import MachineDef

type Listener = Callable[[engine.Transition | None, Context | None], None]

# Renames made in the reconfigure flow, waiting for the reload that follows,
# so a machine sitting in a renamed state stays in it: {subentry_id: {old: new}}.
_RENAMES: HassKey[dict[str, dict[str, str]]] = HassKey(f"{DOMAIN}_renames")


@callback
def async_note_rename(hass: HomeAssistant, subentry_id: str, old: str, new: str) -> None:
    renames = hass.data.setdefault(_RENAMES, {}).setdefault(subentry_id, {})
    for earlier, target in renames.items():
        if target == old:
            renames[earlier] = new
    renames[old] = new


class MachineRuntime:
    """The live side of one machine (one config subentry).

    The select entity owns persistence: it restores the last state and hands
    it to ``async_restore``. Until then ``status`` is ``None``.
    """

    def __init__(self, hass: HomeAssistant, subentry_id: str, machine: MachineDef) -> None:
        self.hass = hass
        self.subentry_id = subentry_id
        self.machine = machine
        self.status: engine.Status | None = None
        self.entity_id: str | None = None  # the select's, once added
        self._listeners: list[Listener] = []

    @callback
    def async_add_listener(self, listener: Listener) -> Callable[[], None]:
        """Call ``listener(transition, context)`` on every change (``None`` on restore)."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    @callback
    def async_restore(self, restored: engine.Status | None) -> None:
        """Start from the restored status, or the fallback. Fires no events."""
        renames = self.hass.data.get(_RENAMES, {}).pop(self.subentry_id, {})
        if restored is not None and renames:
            restored = replace(
                restored,
                state=renames.get(restored.state, restored.state),
                previous=renames.get(restored.previous, restored.previous) if restored.previous else None,
            )
        self.status = engine.initial(self.machine, restored, dt_util.utcnow())
        for listener in list(self._listeners):
            listener(None, None)

    @callback
    def async_set_state(
        self,
        to: str,
        *,
        reason: str = engine.REASON_MANUAL,
        manual: bool = True,
        context: Context | None = None,
    ) -> None:
        """Move to ``to``. A no-op when already there."""
        if self.status is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="not_ready",
                translation_placeholders={"machine": self.machine.name},
            )
        try:
            result = engine.set_state(
                self.machine, self.status, to, dt_util.utcnow(), reason=reason, manual=manual
            )
        except engine.UnknownState as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="unknown_state",
                translation_placeholders={
                    "state": to, "machine": self.machine.name, "states": ", ".join(self.machine.names),
                },
            ) from err
        if result is None:
            return
        self.status, transition = result
        for listener in list(self._listeners):
            listener(transition, context)
        self.hass.bus.async_fire(
            EVENT_TRANSITION,
            {
                "entity_id": self.entity_id,
                ATTR_MACHINE: self.machine.name,
                ATTR_FROM: transition.from_state,
                ATTR_TO: transition.to_state,
                ATTR_REASON: transition.reason,
                ATTR_MANUAL: transition.manual,
            },
            context=context,
        )
