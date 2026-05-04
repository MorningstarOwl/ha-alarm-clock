"""Alarm Clock integration.

Self-contained alarm scheduler built on ``async_track_point_in_time``.
Stores alarm definitions in ``.storage/alarm_clock.alarms``, plays a sound
(with looping + volume ramp) when each alarm fires, fires an
``alarm_clock_triggered`` event, and exposes an MCP SSE server for the
Assist LLM.
"""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .alarm_manager import AlarmManager
from .const import (
    CONF_MCP_PORT,
    DEFAULT_MCP_PORT,
    DOMAIN,
    KEY_PREFIX,
    SNOOZE_SUFFIX,
)
from .mcp_server import AlarmMcpServer
from .media import MediaController, ensure_sound_folder
from .services import async_register_services, async_unregister_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    defaults = {**entry.data, **entry.options}

    ensure_sound_folder()

    manager = AlarmManager(hass, defaults)
    await manager.async_load()

    media = MediaController(hass)

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].update(
        {
            "manager": manager,
            "media": media,
            "defaults": defaults,
        }
    )

    await async_register_services(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def _on_started(_event=None) -> None:
        # Best-effort migration: if Simple Cue is still installed and has
        # leftover cues from v0.1.x (when alarm_clock delegated scheduling
        # to it), cancel them so they don't double-fire alongside our own
        # native timers. Silent no-op when Simple Cue is absent.
        await _migrate_clear_simple_cue_leftovers(hass, manager)
        try:
            await manager.async_reconcile()
        except Exception as err:  # pragma: no cover - defensive
            _LOGGER.exception("Alarm reconciliation failed: %s", err)

    if hass.is_running:
        await _on_started()
    else:
        hass.bus.async_listen_once("homeassistant_started", _on_started)

    # Start the MCP SSE server
    port = defaults.get(CONF_MCP_PORT, DEFAULT_MCP_PORT)
    mcp = AlarmMcpServer(hass, port)
    await mcp.async_start()
    hass.data[DOMAIN]["mcp"] = mcp

    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    domain_data = hass.data.get(DOMAIN, {})

    mcp: AlarmMcpServer | None = domain_data.get("mcp")
    if mcp is not None:
        await mcp.async_stop()

    manager: AlarmManager | None = domain_data.get("manager")
    if manager is not None:
        manager.async_unload()

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await async_unregister_services(hass)
        hass.data.pop(DOMAIN, None)
    return unload_ok


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry so new options (port, defaults) take effect."""
    await hass.config_entries.async_reload(entry.entry_id)


async def _migrate_clear_simple_cue_leftovers(
    hass: HomeAssistant, manager: AlarmManager
) -> None:
    """Cancel any v0.1.x cues parked in Simple Cue for our alarms.

    Best-effort. Skips entirely if Simple Cue isn't installed.
    """
    if not hass.services.has_service("simple_cue", "cancel"):
        return
    for name in list(manager.alarms.keys()):
        for cue in (f"{KEY_PREFIX}{name}", f"{KEY_PREFIX}{name}{SNOOZE_SUFFIX}"):
            try:
                await hass.services.async_call(
                    "simple_cue", "cancel", {"name": cue}, blocking=True
                )
            except Exception:
                # Cue didn't exist, or some other transient — ignore.
                pass
