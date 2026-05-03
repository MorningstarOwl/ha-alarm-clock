"""Sensor entities for the Alarm Clock integration."""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN

SIGNAL_UPDATE = f"{DOMAIN}_sensor_update"


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    manager = hass.data[DOMAIN]["manager"]

    count_entity = AlarmClockCountSensor(manager)
    entities: list[SensorEntity] = [count_entity]

    existing: dict[str, AlarmDefinitionSensor] = {}
    for name in manager.alarms:
        sensor = AlarmDefinitionSensor(manager, name)
        existing[name] = sensor
        entities.append(sensor)

    async_add_entities(entities)

    @callback
    def _on_change(payload: dict[str, Any]) -> None:
        action = payload.get("action")
        name = payload.get("name")
        if action == "added" and name not in existing:
            sensor = AlarmDefinitionSensor(manager, name)
            existing[name] = sensor
            async_add_entities([sensor])
        elif action == "removed" and name in existing:
            hass.async_create_task(existing.pop(name).async_remove())
        # For "updated" / count refresh, all entities self-poll via the same signal.
        count_entity.async_write_ha_state()
        for s in existing.values():
            s.async_write_ha_state()

    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_UPDATE, _on_change))


class AlarmDefinitionSensor(SensorEntity):
    """Represents one configured alarm — state is the next fire time."""

    _attr_should_poll = False
    _attr_icon = "mdi:alarm"

    def __init__(self, manager, name: str) -> None:
        self._manager = manager
        self._name = name
        self._attr_unique_id = f"{DOMAIN}_{name}"
        self._attr_name = f"Alarm {name}"

    @property
    def native_value(self) -> str | None:
        alarm = self._manager.get(self._name)
        if alarm is None:
            return None
        return alarm.next_fire

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        alarm = self._manager.get(self._name)
        if alarm is None:
            return {}
        return {
            "time": alarm.time,
            "days": alarm.days,
            "sound_file": alarm.sound_file,
            "media_player": alarm.media_player,
            "volume": alarm.volume,
            "ramp_duration": alarm.ramp_duration,
            "ramp_start": alarm.ramp_start,
            "loop": alarm.loop,
            "enabled": alarm.enabled,
            "next_fire": alarm.next_fire,
        }

    @property
    def available(self) -> bool:
        return self._manager.get(self._name) is not None


class AlarmClockCountSensor(SensorEntity):
    """Always-on summary entity: count of alarms + map of names to fire times."""

    _attr_should_poll = False
    _attr_icon = "mdi:alarm-multiple"
    _attr_name = "Alarm Clock Count"
    _attr_unique_id = f"{DOMAIN}_count"

    def __init__(self, manager) -> None:
        self._manager = manager

    @property
    def native_value(self) -> int:
        return len(self._manager.alarms)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "alarms": {
                name: alarm.next_fire
                for name, alarm in self._manager.alarms.items()
            },
            "enabled_count": sum(
                1 for a in self._manager.alarms.values() if a.enabled
            ),
        }
