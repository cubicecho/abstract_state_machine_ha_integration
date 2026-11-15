"""Services: ``set_state``."""
from __future__ import annotations

import probatio as vol  # HA 2026.10+ validates with probatio
from homeassistant.components.select import DOMAIN as SELECT_DOMAIN
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import service

from .const import DOMAIN, SERVICE_SET_STATE


@callback
def async_register(hass: HomeAssistant) -> None:
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_SET_STATE,
        entity_domain=SELECT_DOMAIN,
        schema={vol.Required("state"): cv.string, vol.Optional("reason"): cv.string},
        func="async_set_machine_state",
    )
