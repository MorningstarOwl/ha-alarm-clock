"""Service registration for the Alarm Clock integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .alarm_manager import AlarmManager
from .const import (
    ATTR_DAYS,
    ATTR_ENABLED,
    ATTR_LOOP,
    ATTR_MEDIA_PLAYER,
    ATTR_MINUTES,
    ATTR_NAME,
    ATTR_ONE_SHOT_DATE,
    ATTR_RAMP_DURATION,
    ATTR_RAMP_START,
    ATTR_SOUND_FILE,
    ATTR_TIME,
    ATTR_VOLUME,
    DEFAULT_SNOOZE_MINUTES,
    DOMAIN,
    EVENT_TRIGGERED,
    SERVICE_CANCEL,
    SERVICE_DISMISS,
    SERVICE_RING,
    SERVICE_SET,
    SERVICE_SNOOZE,
)
from .media import MediaController, resolve_sound_path

_LOGGER = logging.getLogger(__name__)

SET_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_NAME): str,
        vol.Required(ATTR_TIME): str,
        vol.Optional(ATTR_DAYS, default="once"): vol.Any(str, list),
        vol.Optional(ATTR_SOUND_FILE): vol.Any(str, None),
        vol.Optional(ATTR_MEDIA_PLAYER): vol.Any(str, None),
        vol.Optional(ATTR_VOLUME): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=1.0)),
        vol.Optional(ATTR_RAMP_DURATION): vol.All(int, vol.Range(min=0, max=600)),
        vol.Optional(ATTR_RAMP_START): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=1.0)),
        vol.Optional(ATTR_LOOP): bool,
        vol.Optional(ATTR_ENABLED, default=True): bool,
        vol.Optional(ATTR_ONE_SHOT_DATE): vol.Any(str, None),
    }
)

NAME_ONLY_SCHEMA = vol.Schema({vol.Required(ATTR_NAME): str})

SNOOZE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_NAME): str,
        vol.Optional(ATTR_MINUTES, default=DEFAULT_SNOOZE_MINUTES): vol.All(
            int, vol.Range(min=1, max=120)
        ),
    }
)


async def async_register_services(hass: HomeAssistant) -> None:
    manager: AlarmManager = hass.data[DOMAIN]["manager"]
    media: MediaController = hass.data[DOMAIN]["media"]

    async def _set(call: ServiceCall) -> None:
        alarm = await manager.async_set(dict(call.data))
        async_dispatcher_send(
            hass,
            f"{DOMAIN}_sensor_update",
            {"action": "added", "name": alarm.name},
        )

    async def _cancel(call: ServiceCall) -> None:
        name = call.data[ATTR_NAME]
        await media.stop_ring(name)
        removed = await manager.async_cancel(name)
        if removed:
            async_dispatcher_send(
                hass,
                f"{DOMAIN}_sensor_update",
                {"action": "removed", "name": name},
            )
            async_dispatcher_send(
                hass,
                f"{DOMAIN}_ringing_update",
                {"action": "stopped", "name": name},
            )

    async def _snooze(call: ServiceCall) -> None:
        name = call.data[ATTR_NAME]
        minutes = call.data.get(ATTR_MINUTES, DEFAULT_SNOOZE_MINUTES)
        await media.stop_ring(name)
        async_dispatcher_send(
            hass,
            f"{DOMAIN}_ringing_update",
            {"action": "stopped", "name": name},
        )
        await manager.async_snooze(name, minutes)

    async def _dismiss(call: ServiceCall) -> None:
        name = call.data[ATTR_NAME]
        await media.stop_ring(name)
        await manager.async_dismiss(name)
        async_dispatcher_send(
            hass,
            f"{DOMAIN}_ringing_update",
            {"action": "stopped", "name": name},
        )

    async def _ring(call: ServiceCall) -> None:
        """Internal trigger called by Simple Cue when an alarm fires."""
        name = call.data[ATTR_NAME]
        was_snooze = bool(call.data.get("was_snooze", False))
        alarm = manager.get(name)
        if alarm is None:
            _LOGGER.warning("alarm_clock.ring fired for unknown alarm: %s", name)
            return

        media_player = alarm.media_player or manager.defaults.get("default_media_player")
        if not media_player:
            _LOGGER.error("Alarm %s has no media_player configured", name)
            return

        sound_path = resolve_sound_path(
            alarm.sound_file, manager.defaults.get("default_sound")
        )
        if sound_path is None:
            _LOGGER.error(
                "Alarm %s: no playable sound file in /config/alarm_sounds", name
            )
            return

        await media.start_ring(
            name=name,
            media_player=media_player,
            sound_path=sound_path,
            target_volume=alarm.volume,
            loop=alarm.loop,
            ramp_duration=alarm.ramp_duration,
            ramp_start=alarm.ramp_start,
        )
        async_dispatcher_send(
            hass,
            f"{DOMAIN}_ringing_update",
            {"action": "started", "name": name},
        )

        # Re-queue the next occurrence (for recurring) or remove the definition (one-shot).
        await manager.async_handle_fire(name, was_snooze=was_snooze)
        async_dispatcher_send(
            hass,
            f"{DOMAIN}_sensor_update",
            {"action": "updated", "name": name},
        )

        hass.bus.async_fire(
            EVENT_TRIGGERED,
            {
                "name": name,
                "sound_file": alarm.sound_file,
                "media_player": media_player,
                "was_snooze": was_snooze,
            },
        )

    hass.services.async_register(DOMAIN, SERVICE_SET, _set, schema=SET_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_CANCEL, _cancel, schema=NAME_ONLY_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_SNOOZE, _snooze, schema=SNOOZE_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_DISMISS, _dismiss, schema=NAME_ONLY_SCHEMA)
    hass.services.async_register(
        DOMAIN,
        SERVICE_RING,
        _ring,
        schema=vol.Schema(
            {vol.Required(ATTR_NAME): str, vol.Optional("was_snooze"): bool}
        ),
    )


async def async_unregister_services(hass: HomeAssistant) -> None:
    for svc in (SERVICE_SET, SERVICE_CANCEL, SERVICE_SNOOZE, SERVICE_DISMISS, SERVICE_RING):
        hass.services.async_remove(DOMAIN, svc)
