"""Time spent in each state today. Pure, no HA imports.

Times are local (timezone-aware) datetimes; "today" is the local date of
``now``. Totals hold closed stretches; the current stretch runs from
``since``. A new day starts from zero at local midnight.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, time
from typing import Any


@dataclass(frozen=True)
class TimeInState:
    day: date
    state: str | None = None
    since: datetime | None = None  # start of the current stretch, never before the day's midnight
    totals: dict[str, float] = field(default_factory=dict)  # seconds, closed stretches only

    @classmethod
    def start(cls, state: str | None, now: datetime) -> TimeInState:
        return cls(now.date(), state, now if state else None)

    def roll(self, now: datetime) -> TimeInState:
        """Start a new day at midnight if ``now`` is on a later date."""
        if now.date() <= self.day:
            return self
        midnight = datetime.combine(now.date(), time(0), tzinfo=now.tzinfo)
        return TimeInState(now.date(), self.state, midnight if self.state else None)

    def switch(self, state: str | None, now: datetime) -> TimeInState:
        """The machine moved to ``state`` at ``now``."""
        current = self.roll(now)
        if state == current.state:
            return current
        totals = dict(current.totals)
        if current.state and current.since is not None:
            totals[current.state] = totals.get(current.state, 0.0) + max(0.0, (now - current.since).total_seconds())
        return replace(current, state=state, since=now if state else None, totals=totals)

    def seconds(self, state: str, now: datetime) -> float:
        """Seconds in ``state`` today, up to ``now``."""
        current = self.roll(now)
        total = current.totals.get(state, 0.0)
        if current.state == state and current.since is not None:
            total += max(0.0, (now - current.since).total_seconds())
        return total

    def resumed(self, now: datetime) -> TimeInState:
        """After a restart: keep today's totals, count the current state again from ``now``."""
        current = self.roll(now)
        return replace(current, since=now if current.state else None)

    def as_dict(self) -> dict[str, Any]:
        return {
            "day": self.day.isoformat(),
            "state": self.state,
            "since": self.since.isoformat() if self.since else None,
            "totals": dict(self.totals),
        }

    @classmethod
    def from_dict(cls, data: Any) -> TimeInState | None:
        try:
            since = datetime.fromisoformat(data["since"]) if data.get("since") else None
            totals = {str(k): float(v) for k, v in (data.get("totals") or {}).items()}
            return cls(date.fromisoformat(data["day"]), data.get("state"), since, totals)
        except (KeyError, TypeError, ValueError, AttributeError):
            return None
