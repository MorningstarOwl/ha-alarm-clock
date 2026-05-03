"""Ringing-state binary sensors for the Alarm Clock integration."""
from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN

SIGNAL_RINGING = f"{DOMAIN}_ringing_update"


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    manager = hass.data[DOMAIN]["manager"]
    media = hass.data[DOMAIN]["media"]

    existing: dict[str, AlarmRingingBinarySensor] = {}

    def _ensure(name: str) -> None:
        if name in existing:
            return
        sensor = AlarmRingingBinarySensor(manager, media, name)
        existing[name] = sensor
        async_add_entities([sensor])

    for name in manager.alarms:
        _ensure(name)

    @callback
    def _on_change(payload: dict[str, Any]) -> None:
        name = payload.get("name")
        action = payload.get("action")
        if name is None:
            return
        if action in {"started", "stopped"}:
            _ensure(name)
            sensor = existing.get(name)
            if sensor is not None:
                sensor.async_write_ha_state()
        elif action == "removed" and name in existing:
            hass.async_create_task(existing.pop(name).async_remove())

    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_RINGING, _on_change))


class AlarmRingingBinarySensor(BinarySensorEntity):
    _attr_should_poll = False
    _attr_icon = "mdi:bell-ring"

    def __init__(self, manager, media, name: str) -> None:
        self._manager = manager
        self._media = media
        self._name = name
        self._attr_unique_id = f"{DOMAIN}_ringing_{name}"
        self._attr_name = f"Alarm {name} Ringing"

    @property
    def is_on(self) -> bool:
        return self._media.is_ringing(self._name)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        alarm = self._manager.get(self._name)
        if alarm is None:
            return {}
        return {
            "sound_file": alarm.sound_file,
            "media_player": alarm.media_player,
        }
