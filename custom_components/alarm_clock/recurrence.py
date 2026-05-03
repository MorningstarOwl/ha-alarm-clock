"""Recurrence math for the Alarm Clock integration.

Pure functions only — no HA dependencies, easy to unit test.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Iterable

from .const import (
    DAY_NAMES,
    PATTERN_DAILY,
    PATTERN_ONCE,
    PATTERN_WEEKDAYS,
    PATTERN_WEEKENDS,
)

WEEKDAYS = {"mon", "tue", "wed", "thu", "fri"}
WEEKENDS = {"sat", "sun"}


def parse_time(time_str: str) -> time:
    """Parse a string like '6:30am', '06:30', '17:00', '5pm' into a time."""
    s = time_str.strip().lower().replace(" ", "")
    if s in {"noon", "12pm"}:
        return time(12, 0)
    if s in {"midnight", "12am"}:
        return time(0, 0)

    # am/pm form
    if s.endswith("am") or s.endswith("pm"):
        meridiem = s[-2:]
        body = s[:-2]
        if ":" in body:
            hour_s, minute_s = body.split(":", 1)
            hour, minute = int(hour_s), int(minute_s)
        else:
            hour, minute = int(body), 0
        if meridiem == "am":
            if hour == 12:
                hour = 0
        else:  # pm
            if hour != 12:
                hour += 12
        return time(hour, minute)

    # 24h form
    if ":" in s:
        hour_s, minute_s = s.split(":", 1)
        return time(int(hour_s), int(minute_s))

    # bare hour, 24h
    return time(int(s), 0)


def normalize_days(days) -> list[str] | str:
    """Normalize a days input into either a named pattern or a list of day slugs."""
    if days is None:
        return PATTERN_ONCE
    if isinstance(days, str):
        s = days.strip().lower()
        if s in {PATTERN_ONCE, PATTERN_DAILY, PATTERN_WEEKDAYS, PATTERN_WEEKENDS}:
            return s
        # Comma-separated string: "mon,wed,fri"
        if "," in s:
            return [d.strip()[:3] for d in s.split(",") if d.strip()]
        # Single day word
        if s[:3] in DAY_NAMES:
            return [s[:3]]
        raise ValueError(f"Unknown days pattern: {days!r}")
    if isinstance(days, Iterable):
        out = [str(d).strip().lower()[:3] for d in days]
        for d in out:
            if d not in DAY_NAMES:
                raise ValueError(f"Unknown day: {d!r}")
        return out
    raise ValueError(f"Could not interpret days: {days!r}")


def _matches(d: date, pattern) -> bool:
    day_slug = DAY_NAMES[d.weekday()]
    if pattern == PATTERN_DAILY:
        return True
    if pattern == PATTERN_WEEKDAYS:
        return day_slug in WEEKDAYS
    if pattern == PATTERN_WEEKENDS:
        return day_slug in WEEKENDS
    if pattern == PATTERN_ONCE:
        return False  # callers handle once specially
    if isinstance(pattern, list):
        return day_slug in pattern
    return False


def next_occurrence(
    time_str: str,
    days,
    after: datetime,
    one_shot_date: date | None = None,
) -> datetime | None:
    """Return the next datetime an alarm should fire, strictly after `after`.

    For PATTERN_ONCE, requires `one_shot_date`. Returns None if the one-shot
    date+time has already passed.
    """
    fire_time = parse_time(time_str)
    pattern = normalize_days(days)

    if pattern == PATTERN_ONCE:
        if one_shot_date is None:
            # Treat as "next occurrence at this time" — today if still future, else tomorrow
            candidate = datetime.combine(after.date(), fire_time, tzinfo=after.tzinfo)
            if candidate <= after:
                candidate = candidate + timedelta(days=1)
            return candidate
        candidate = datetime.combine(one_shot_date, fire_time, tzinfo=after.tzinfo)
        return candidate if candidate > after else None

    # Recurring patterns: walk forward up to 14 days to find a match
    for offset in range(0, 14):
        d = after.date() + timedelta(days=offset)
        if not _matches(d, pattern):
            continue
        candidate = datetime.combine(d, fire_time, tzinfo=after.tzinfo)
        if candidate > after:
            return candidate
    return None
