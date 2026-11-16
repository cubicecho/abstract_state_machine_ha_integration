"""Conditions this integration adds to HA's condition picker (automations, and our rules).

``abstract_state_machine.calendar_event``: one of the target calendars has an
event now / today / tomorrow, optionally only all-day or timed ones, and
optionally only ones whose title (or description) contains some text::

    condition: abstract_state_machine.calendar_event
    target:
      entity_id: calendar.alice
    options:
      when: today            # now | today | tomorrow
      title_contains: PTO    # optional, case doesn't matter
      search_description: false
      event_kind: any        # any | all_day | timed

A calendar entity's own state only shows one event at a time, so "is any
event today titled PTO?" can't be a state condition. This keeps the
calendars' events for today and tomorrow, refreshed every 15 minutes and
at midnight.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, Unpack, cast, override

import probatio as vol  # HA 2026.10+ validates with probatio
from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.components.calendar.const import DATA_COMPONENT
from homeassistant.const import CONF_OPTIONS, CONF_TARGET
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback, split_entity_id
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.condition import Condition, ConditionCheckParams, ConditionConfig
from homeassistant.helpers.event import async_track_time_change, async_track_time_interval
from homeassistant.helpers.target import TargetSelection, async_extract_referenced_entity_ids
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

CONF_WHEN = "when"
CONF_TITLE_CONTAINS = "title_contains"
CONF_SEARCH_DESCRIPTION = "search_description"
CONF_EVENT_KIND = "event_kind"

WHEN_NOW = "now"
WHEN_TODAY = "today"
WHEN_TOMORROW = "tomorrow"
KIND_ANY = "any"
KIND_ALL_DAY = "all_day"
KIND_TIMED = "timed"

REFRESH_INTERVAL = timedelta(minutes=15)

_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_TARGET): cv.TARGET_FIELDS,
        vol.Required(CONF_OPTIONS, default={}): {
            vol.Required(CONF_WHEN, default=WHEN_NOW): vol.In([WHEN_NOW, WHEN_TODAY, WHEN_TOMORROW]),
            vol.Optional(CONF_TITLE_CONTAINS, default=""): vol.Any(None, cv.string),
            vol.Optional(CONF_SEARCH_DESCRIPTION, default=False): cv.boolean,
            vol.Required(CONF_EVENT_KIND, default=KIND_ANY): vol.In([KIND_ANY, KIND_ALL_DAY, KIND_TIMED]),
        },
    }
)


def event_matches(
    event: CalendarEvent,
    now: datetime,
    when: str = WHEN_NOW,
    contains: str = "",
    search_description: bool = False,
    kind: str = KIND_ANY,
) -> bool:
    """Whether ``event`` counts, at local time ``now``. Pure."""
    if kind == KIND_ALL_DAY and not event.all_day:
        return False
    if kind == KIND_TIMED and event.all_day:
        return False
    if contains:
        text = event.summary or ""
        if search_description and event.description:
            text = f"{text}\n{event.description}"
        if contains.casefold() not in text.casefold():
            return False
    start, end = event.start_datetime_local, event.end_datetime_local
    if when == WHEN_NOW:
        return start <= now < end
    day = dt_util.start_of_local_day(now)
    if when == WHEN_TOMORROW:
        day += timedelta(days=1)
    return start < day + timedelta(days=1) and end > day


class CalendarEventCondition(Condition):
    """One of the target calendars has a matching event now, today or tomorrow."""

    @override
    @classmethod
    async def async_validate_config(cls, hass: HomeAssistant, config: ConfigType) -> ConfigType:
        return cast(ConfigType, _SCHEMA(config))

    def __init__(self, hass: HomeAssistant, config: ConditionConfig) -> None:
        super().__init__(hass, config)
        assert config.target is not None and config.options is not None
        self._target = TargetSelection(config.target)
        options = config.options
        self._when: str = options[CONF_WHEN]
        self._contains = (options.get(CONF_TITLE_CONTAINS) or "").strip()
        self._search_description: bool = options[CONF_SEARCH_DESCRIPTION]
        self._kind: str = options[CONF_EVENT_KIND]
        self._events: dict[str, list[CalendarEvent]] = {}  # by calendar entity id
        self._actions: list[Callable[[], None]] = []
        self._unsubs: list[CALLBACK_TYPE] = []

    @override
    async def _async_setup(self) -> None:
        await self._async_refresh()
        self._unsubs.append(async_track_time_interval(self._hass, self._async_refresh_at, REFRESH_INTERVAL))
        # At midnight "today" and "tomorrow" move on: load the new day straight away.
        self._unsubs.append(async_track_time_change(self._hass, self._async_refresh_at, hour=0, minute=0, second=1))

    @override
    def _async_unload(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        self._actions.clear()

    @property
    @override
    def needs_polling(self) -> bool:
        return True  # events start and end as time passes

    @override
    async def _async_track_changes(self, action: Callable[[], None]) -> CALLBACK_TYPE:
        """Call ``action`` after a refresh brings different events."""
        self._actions.append(action)

        @callback
        def remove() -> None:
            if action in self._actions:
                self._actions.remove(action)

        return remove

    @override
    def _async_check(self, **kwargs: Unpack[ConditionCheckParams]) -> bool:
        now = dt_util.now()
        return any(
            event_matches(event, now, self._when, self._contains, self._search_description, self._kind)
            for events in self._events.values()
            for event in events
        )

    def _calendar_ids(self) -> set[str]:
        selected = async_extract_referenced_entity_ids(self._hass, self._target)
        return {
            entity_id
            for entity_id in selected.referenced | selected.indirectly_referenced
            if split_entity_id(entity_id)[0] == "calendar"
        }

    async def _async_refresh_at(self, _now: datetime) -> None:
        await self._async_refresh()

    async def _async_refresh(self) -> None:
        """Load today's and tomorrow's events. A calendar that fails keeps what it had."""
        start = dt_util.start_of_local_day()
        end = start + timedelta(days=2)
        component = self._hass.data.get(DATA_COMPONENT)
        events: dict[str, list[CalendarEvent]] = {}
        for entity_id in sorted(self._calendar_ids()):
            entity = component.get_entity(entity_id) if component else None
            if not isinstance(entity, CalendarEntity):
                _LOGGER.debug("%s isn't a calendar (yet)", entity_id)
                continue
            try:
                events[entity_id] = await entity.async_get_events(self._hass, start, end)
            except HomeAssistantError as err:
                _LOGGER.warning("Couldn't read events from %s, keeping the last ones: %s", entity_id, err)
                events[entity_id] = self._events.get(entity_id, [])
        changed = _fingerprint(events) != _fingerprint(self._events)
        self._events = events
        if changed:
            for action in list(self._actions):
                action()


def _fingerprint(events: dict[str, list[CalendarEvent]]) -> set[tuple[Any, ...]]:
    return {
        (entity_id, event.summary, event.description, event.start, event.end)
        for entity_id, items in events.items()
        for event in items
    }


async def async_get_conditions(hass: HomeAssistant) -> dict[str, type[Condition]]:
    """The conditions this integration provides."""
    return {"calendar_event": CalendarEventCondition}
