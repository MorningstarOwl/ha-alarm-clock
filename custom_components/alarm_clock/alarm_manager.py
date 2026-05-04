"""Alarm definition store + native HA scheduling.

Owns the persistent alarm definitions and arms ``async_track_point_in_time``
callbacks for each occurrence. When a timer fires it dispatches the
``alarm_clock.ring`` service so all media/event/dispatcher work lives in one
place (services.py).
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_DAYS,
    ATTR_ENABLED,
    ATTR_LOOP,
    ATTR_MEDIA_PLAYER,
    ATTR_NAME,
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
    DEFAULT_LOOP,
    DEFAULT_RAMP_DURATION,
    DEFAULT_RAMP_START,
    DEFAULT_VOLUME,
    DOMAIN,
    KEY_PREFIX,
    PATTERN_ONCE,
    SERVICE_RING,
    SNOOZE_SUFFIX,
    STORAGE_KEY,
    STORAGE_VERSION,
)
from .recurrence import next_occurrence, normalize_days

_LOGGER = logging.getLogger(__name__)


@dataclass
class Alarm:
    """A single alarm definition. Persisted to .storage/alarm_clock.alarms."""

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


def _main_key(alarm_name: str) -> str:
    """Internal scheduler key for the regular fire of an alarm."""
    return f"{KEY_PREFIX}{alarm_name}"


def _snooze_key(alarm_name: str) -> str:
    """Internal scheduler key for a pending snooze fire."""
    return f"{KEY_PREFIX}{alarm_name}{SNOOZE_SUFFIX}"


class AlarmManager:
    """Persistent definition store + ``async_track_point_in_time`` scheduler."""

    def __init__(self, hass: HomeAssistant, defaults: dict[str, Any]) -> None:
        self.hass = hass
        self.defaults = defaults
        self._store: Store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._alarms: dict[str, Alarm] = {}
        # Scheduler keys -> async_track_point_in_time unsub callbacks.
        # Two keys per alarm at most: the main fire and an outstanding snooze.
        self._unsubs: dict[str, CALLBACK_TYPE] = {}

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

    @callback
    def async_unload(self) -> None:
        """Cancel all in-flight timers. Called from async_unload_entry."""
        for unsub in list(self._unsubs.values()):
            try:
                unsub()
            except Exception:  # pragma: no cover - defensive
                pass
        self._unsubs.clear()

    # ------------------------------------------------------------------
    # Definition mutations
    # ------------------------------------------------------------------

    async def async_set(self, payload: dict[str, Any]) -> Alarm:
        """Create or replace an alarm and arm its next fire."""
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
                payload.get(
                    ATTR_VOLUME, self.defaults.get(CONF_DEFAULT_VOLUME, DEFAULT_VOLUME)
                )
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

        # Replace any existing instance + cancel its timers
        if name in self._alarms:
            self._disarm(_main_key(name))
            self._disarm(_snooze_key(name))

        self._alarms[name] = alarm

        if alarm.enabled:
            await self._schedule_next(alarm)

        await self.async_save()
        return alarm

    async def async_cancel(self, name: str) -> bool:
        """Remove an alarm definition and its outstanding timers."""
        if name not in self._alarms:
            return False
        self._disarm(_main_key(name))
        self._disarm(_snooze_key(name))
        self._alarms.pop(name, None)
        await self.async_save()
        return True

    # ------------------------------------------------------------------
    # Lifecycle: post-fire bookkeeping, snooze, dismiss
    # ------------------------------------------------------------------

    async def async_handle_fire(self, alarm_name: str, was_snooze: bool) -> Alarm | None:
        """Called by services._ring after media has started.

        For recurring alarms: queue the next regular occurrence.
        For one-shots: clear next_fire but keep the definition until
        explicitly dismissed/cancelled, so the user can still snooze.
        """
        alarm = self._alarms.get(alarm_name)
        if alarm is None:
            return None

        if alarm.days == PATTERN_ONCE:
            alarm.next_fire = None
            await self.async_save()
            return alarm

        if alarm.enabled:
            await self._schedule_next(
                alarm, after=dt_util.now() + timedelta(seconds=1)
            )
        await self.async_save()
        return alarm

    async def async_snooze(self, alarm_name: str, minutes: int) -> bool:
        """Arm a one-shot snooze fire N minutes from now."""
        if alarm_name not in self._alarms:
            return False
        fire_at = dt_util.now() + timedelta(minutes=minutes)
        self._arm(_snooze_key(alarm_name), fire_at, alarm_name, was_snooze=True)
        return True

    async def async_dismiss(self, alarm_name: str) -> bool:
        """Cancel any pending snooze. One-shot definitions are removed."""
        self._disarm(_snooze_key(alarm_name))
        alarm = self._alarms.get(alarm_name)
        if alarm is None:
            return False
        if alarm.days == PATTERN_ONCE:
            self._alarms.pop(alarm_name, None)
            await self.async_save()
        return True

    # ------------------------------------------------------------------
    # Reconciliation (called after HA startup)
    # ------------------------------------------------------------------

    async def async_reconcile(self) -> None:
        """Re-arm timers for every enabled alarm; drop expired one-shots."""
        now = dt_util.now()
        to_drop: list[str] = []
        for alarm in list(self._alarms.values()):
            if not alarm.enabled:
                alarm.next_fire = None
                continue
            await self._schedule_next(alarm, after=now)
            if alarm.next_fire is None and alarm.days == PATTERN_ONCE:
                to_drop.append(alarm.name)

        for name in to_drop:
            self._alarms.pop(name, None)
            _LOGGER.info("Dropped stale one-shot alarm: %s", name)

        await self.async_save()

    # ------------------------------------------------------------------
    # Internals: scheduling primitives
    # ------------------------------------------------------------------

    async def _schedule_next(
        self, alarm: Alarm, after: datetime | None = None
    ) -> None:
        """Compute the next fire time and arm a timer for it."""
        after = after or dt_util.now()
        one_shot_date = None
        if alarm.one_shot_date:
            try:
                one_shot_date = datetime.fromisoformat(alarm.one_shot_date).date()
            except ValueError:
                _LOGGER.warning(
                    "Bad one_shot_date for %s: %s", alarm.name, alarm.one_shot_date
                )

        try:
            fire_at = next_occurrence(
                alarm.time, alarm.days, after, one_shot_date=one_shot_date
            )
        except ValueError as err:
            _LOGGER.error("Cannot schedule alarm %s: %s", alarm.name, err)
            return

        if fire_at is None:
            _LOGGER.info("Alarm %s has no future occurrence", alarm.name)
            alarm.next_fire = None
            self._disarm(_main_key(alarm.name))
            return

        alarm.next_fire = fire_at.isoformat()
        self._arm(_main_key(alarm.name), fire_at, alarm.name)

    @callback
    def _arm(
        self,
        key: str,
        when: datetime,
        alarm_name: str,
        was_snooze: bool = False,
    ) -> None:
        """Schedule a fire callback under ``key``; replaces any existing one."""
        self._disarm(key)

        @callback
        def _fire(_now: datetime) -> None:
            # async_track_point_in_time auto-unregisters after firing,
            # but our dict still holds the (now-stale) unsub. Drop it.
            self._unsubs.pop(key, None)
            self.hass.async_create_task(
                self._dispatch_ring(alarm_name, was_snooze)
            )

        self._unsubs[key] = async_track_point_in_time(self.hass, _fire, when)
        _LOGGER.debug("Armed %s for %s", key, when.isoformat())

    @callback
    def _disarm(self, key: str) -> None:
        """Cancel the timer registered under ``key`` if any."""
        unsub = self._unsubs.pop(key, None)
        if unsub:
            try:
                unsub()
            except Exception:  # pragma: no cover - defensive
                pass

    async def _dispatch_ring(self, alarm_name: str, was_snooze: bool) -> None:
        """Call the alarm_clock.ring service when a timer fires."""
        try:
            await self.hass.services.async_call(
                DOMAIN,
                SERVICE_RING,
                {ATTR_NAME: alarm_name, "was_snooze": was_snooze},
                blocking=False,
            )
        except Exception:  # pragma: no cover - defensive
            _LOGGER.exception("Failed to dispatch ring for %s", alarm_name)
