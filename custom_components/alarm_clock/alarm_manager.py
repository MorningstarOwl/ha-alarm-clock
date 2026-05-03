"""Core alarm-definition store + Simple Cue scheduling bridge."""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_DAYS,
    ATTR_ENABLED,
    ATTR_LOOP,
    ATTR_MEDIA_PLAYER,
    ATTR_NAME,
    ATTR_NEXT_FIRE,
    ATTR_ONE_SHOT_DATE,
    ATTR_RAMP_DURATION,
    ATTR_RAMP_START,
    ATTR_SOUND_FILE,
    ATTR_TIME,
    ATTR_VOLUME,
    CONF_DEFAULT_LOOP,
    CONF_DEFAULT_MEDIA_PLAYER,
    CONF_DEFAULT_RAMP_DURATION,
    CONF_DEFAULT_RAMP_START,
    CONF_DEFAULT_SOUND,
    CONF_DEFAULT_VOLUME,
    CUE_PREFIX,
    DEFAULT_LOOP,
    DEFAULT_RAMP_DURATION,
    DEFAULT_RAMP_START,
    DEFAULT_VOLUME,
    DOMAIN,
    PATTERN_ONCE,
    SNOOZE_SUFFIX,
    STORAGE_KEY,
    STORAGE_VERSION,
)
from .recurrence import next_occurrence, normalize_days

_LOGGER = logging.getLogger(__name__)


@dataclass
class Alarm:
    name: str
    time: str
    days: Any  # str pattern or list[str]
    sound_file: str | None = None
    media_player: str | None = None
    volume: float = DEFAULT_VOLUME
    ramp_duration: int = DEFAULT_RAMP_DURATION
    ramp_start: float = DEFAULT_RAMP_START
    loop: bool = DEFAULT_LOOP
    enabled: bool = True
    next_fire: str | None = None
    one_shot_date: str | None = None  # ISO date for PATTERN_ONCE

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Alarm":
        return cls(**{k: v for k, v in data.items() if k in cls.__annotations__})


def cue_name(alarm_name: str) -> str:
    return f"{CUE_PREFIX}{alarm_name}"


def snooze_cue_name(alarm_name: str) -> str:
    return f"{CUE_PREFIX}{alarm_name}{SNOOZE_SUFFIX}"


def cue_to_alarm_name(cue: str) -> str | None:
    """Reverse: alarm_clock__weekday_wakeup → weekday_wakeup. Strips snooze suffix."""
    if not cue.startswith(CUE_PREFIX):
        return None
    rest = cue[len(CUE_PREFIX):]
    if rest.endswith(SNOOZE_SUFFIX):
        rest = rest[: -len(SNOOZE_SUFFIX)]
    return rest or None


def is_snooze_cue(cue: str) -> bool:
    return cue.startswith(CUE_PREFIX) and cue.endswith(SNOOZE_SUFFIX)


class AlarmManager:
    """Persistent store + Simple Cue scheduler glue."""

    def __init__(self, hass: HomeAssistant, defaults: dict[str, Any]) -> None:
        self.hass = hass
        self.defaults = defaults
        self._store: Store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._alarms: dict[str, Alarm] = {}

    @property
    def alarms(self) -> dict[str, Alarm]:
        return self._alarms

    def get(self, name: str) -> Alarm | None:
        return self._alarms.get(name)

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        for name, raw in data.get("alarms", {}).items():
            try:
                self._alarms[name] = Alarm.from_dict(raw)
            except Exception as err:
                _LOGGER.warning("Could not load alarm %s: %s", name, err)

    async def async_save(self) -> None:
        await self._store.async_save(
            {"alarms": {n: a.to_dict() for n, a in self._alarms.items()}}
        )

    # ------------------------------------------------------------------
    # Definition mutations
    # ------------------------------------------------------------------

    async def async_set(self, payload: dict[str, Any]) -> Alarm:
        """Create or replace an alarm and queue its next Simple Cue trigger."""
        name = payload[ATTR_NAME]
        days = normalize_days(payload.get(ATTR_DAYS, PATTERN_ONCE))

        alarm = Alarm(
            name=name,
            time=payload[ATTR_TIME],
            days=days,
            sound_file=payload.get(ATTR_SOUND_FILE)
            or self.defaults.get(CONF_DEFAULT_SOUND)
            or None,
            media_player=payload.get(ATTR_MEDIA_PLAYER)
            or self.defaults.get(CONF_DEFAULT_MEDIA_PLAYER)
            or None,
            volume=float(
                payload.get(ATTR_VOLUME, self.defaults.get(CONF_DEFAULT_VOLUME, DEFAULT_VOLUME))
            ),
            ramp_duration=int(
                payload.get(
                    ATTR_RAMP_DURATION,
                    self.defaults.get(CONF_DEFAULT_RAMP_DURATION, DEFAULT_RAMP_DURATION),
                )
            ),
            ramp_start=float(
                payload.get(
                    ATTR_RAMP_START,
                    self.defaults.get(CONF_DEFAULT_RAMP_START, DEFAULT_RAMP_START),
                )
            ),
            loop=bool(
                payload.get(ATTR_LOOP, self.defaults.get(CONF_DEFAULT_LOOP, DEFAULT_LOOP))
            ),
            enabled=bool(payload.get(ATTR_ENABLED, True)),
            one_shot_date=payload.get(ATTR_ONE_SHOT_DATE),
        )

        # Replace any existing instance + cancel its outstanding cue
        if name in self._alarms:
            await self._cancel_cue(cue_name(name))
            await self._cancel_cue(snooze_cue_name(name))

        self._alarms[name] = alarm

        if alarm.enabled:
            await self._schedule_next(alarm)

        await self.async_save()
        return alarm

    async def async_cancel(self, name: str) -> bool:
        """Remove an alarm definition and its outstanding cues."""
        if name not in self._alarms:
            return False
        await self._cancel_cue(cue_name(name))
        await self._cancel_cue(snooze_cue_name(name))
        self._alarms.pop(name, None)
        await self.async_save()
        return True

    # ------------------------------------------------------------------
    # Lifecycle: ring, snooze, dismiss
    # ------------------------------------------------------------------

    async def async_handle_fire(self, alarm_name: str, was_snooze: bool) -> Alarm | None:
        """Called when Simple Cue triggers one of our cues. Re-queues the next.

        Returns the alarm definition (so the service layer can play media).
        """
        alarm = self._alarms.get(alarm_name)
        if alarm is None:
            return None

        if alarm.days == PATTERN_ONCE and not was_snooze:
            # One-shot fired — remove the definition entirely
            self._alarms.pop(alarm_name, None)
            alarm.next_fire = None
            await self.async_save()
            return alarm

        # Recurring (or snooze): queue the *next regular* occurrence
        if alarm.enabled:
            await self._schedule_next(alarm, after=dt_util.now() + timedelta(seconds=1))
        await self.async_save()
        return alarm

    async def async_snooze(self, alarm_name: str, minutes: int) -> bool:
        """Schedule a snooze cue N minutes from now."""
        if alarm_name not in self._alarms:
            return False
        fire_at = dt_util.now() + timedelta(minutes=minutes)
        await self._cancel_cue(snooze_cue_name(alarm_name))
        await self._call_simple_cue_set(
            cue_name=snooze_cue_name(alarm_name),
            when=fire_at,
            alarm_name=alarm_name,
            was_snooze=True,
        )
        return True

    async def async_dismiss(self, alarm_name: str) -> bool:
        """Cancel any pending snooze. Media stop is handled by the service layer."""
        await self._cancel_cue(snooze_cue_name(alarm_name))
        return alarm_name in self._alarms

    # ------------------------------------------------------------------
    # Reconciliation (called after HA restart)
    # ------------------------------------------------------------------

    async def async_reconcile(self) -> None:
        """Ensure every enabled alarm has a future Simple Cue trigger queued."""
        now = dt_util.now()
        for alarm in list(self._alarms.values()):
            if not alarm.enabled:
                continue
            # Always recompute — Simple Cue's storage is authoritative for
            # future fires, but re-issuing set() is idempotent (it replaces).
            await self._schedule_next(alarm, after=now)
        await self.async_save()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _schedule_next(
        self, alarm: Alarm, after: datetime | None = None
    ) -> None:
        after = after or dt_util.now()
        one_shot_date = None
        if alarm.one_shot_date:
            try:
                one_shot_date = datetime.fromisoformat(alarm.one_shot_date).date()
            except ValueError:
                _LOGGER.warning("Bad one_shot_date for %s: %s", alarm.name, alarm.one_shot_date)

        try:
            fire_at = next_occurrence(
                alarm.time, alarm.days, after, one_shot_date=one_shot_date
            )
        except ValueError as err:
            _LOGGER.error("Cannot schedule alarm %s: %s", alarm.name, err)
            return

        if fire_at is None:
            _LOGGER.info("Alarm %s has no future occurrence; skipping schedule", alarm.name)
            alarm.next_fire = None
            return

        alarm.next_fire = fire_at.isoformat()
        await self._call_simple_cue_set(
            cue_name=cue_name(alarm.name),
            when=fire_at,
            alarm_name=alarm.name,
        )

    async def _call_simple_cue_set(
        self,
        cue_name: str,
        when: datetime,
        alarm_name: str,
        was_snooze: bool = False,
    ) -> None:
        """Ask Simple Cue to schedule a cue that calls our alarm_clock.ring service."""
        ring_data: dict[str, Any] = {"name": alarm_name}
        if was_snooze:
            ring_data["was_snooze"] = True
        await self.hass.services.async_call(
            "simple_cue",
            "set",
            {
                "name": cue_name,
                "datetime": when.isoformat(),
                "action": [
                    {
                        "action": f"{DOMAIN}.ring",
                        "data": ring_data,
                    }
                ],
            },
            blocking=True,
        )

    async def _cancel_cue(self, cue_name: str) -> None:
        try:
            await self.hass.services.async_call(
                "simple_cue",
                "cancel",
                {"name": cue_name},
                blocking=True,
            )
        except Exception as err:
            _LOGGER.debug("simple_cue.cancel for %s failed (likely not set): %s", cue_name, err)
