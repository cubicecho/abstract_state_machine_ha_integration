"""Services: ``set_state``, ``clear_override``, ``explain``."""
from __future__ import annotations

import probatio as vol  # HA 2026.10+ validates with probatio
from homeassistant.components.select import DOMAIN as SELECT_DOMAIN
from homeassistant.core import HomeAssistant, SupportsResponse, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import service

from .const import DOMAIN, SERVICE_CLEAR_OVERRIDE, SERVICE_EXPLAIN, SERVICE_SET_STATE


@callback
def async_register(hass: HomeAssistant) -> None:
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_SET_STATE,
        entity_domain=SELECT_DOMAIN,
        schema={
            vol.Required("state"): cv.string,
            vol.Optional("reason"): cv.string,
            vol.Optional("hold"): cv.positive_time_period,
        },
        func="async_set_machine_state",
    )
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_CLEAR_OVERRIDE,
        entity_domain=SELECT_DOMAIN,
        schema={},
        func="async_clear_override",
    )
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_EXPLAIN,
        entity_domain=SELECT_DOMAIN,
        schema={},
        func="async_explain",
        supports_response=SupportsResponse.ONLY,
    )
