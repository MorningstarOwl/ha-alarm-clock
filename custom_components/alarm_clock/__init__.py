"""Alarm Clock integration — companion to Simple Cue.

Stores alarm definitions, calls simple_cue.set to schedule each occurrence,
plays a sound (with looping + volume ramp) when Simple Cue fires the cue,
and exposes an MCP SSE server for the Assist LLM.
"""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .alarm_manager import AlarmManager
from .const import (
    CONF_DEFAULT_LOOP,
    CONF_DEFAULT_MEDIA_PLAYER,
    CONF_DEFAULT_RAMP_DURATION,
    CONF_DEFAULT_RAMP_START,
    CONF_DEFAULT_SOUND,
    CONF_DEFAULT_VOLUME,
    CONF_MCP_PORT,
    DEFAULT_MCP_PORT,
    DOMAIN,
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

    # Re-queue Simple Cue triggers for any enabled alarms that lost their cues
    # (e.g. across a HA restart). simple_cue.set is idempotent — re-issuing
    # replaces an existing cue with the same name.
    async def _reconcile_when_ready(_event=None) -> None:
        try:
            await manager.async_reconcile()
        except Exception as err:  # pragma: no cover - defensive
            _LOGGER.exception("Alarm reconciliation failed: %s", err)

    if hass.is_running:
        await _reconcile_when_ready()
    else:
        hass.bus.async_listen_once("homeassistant_started", _reconcile_when_ready)

    # Start the MCP SSE server
    port = defaults.get(CONF_MCP_PORT, DEFAULT_MCP_PORT)
    mcp = AlarmMcpServer(hass, port)
    await mcp.async_start()
    hass.data[DOMAIN]["mcp"] = mcp

    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    mcp: AlarmMcpServer | None = hass.data.get(DOMAIN, {}).get("mcp")
    if mcp is not None:
        await mcp.async_stop()

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await async_unregister_services(hass)
        hass.data.pop(DOMAIN, None)
    return unload_ok


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry so new options (port, defaults) take effect."""
    await hass.config_entries.async_reload(entry.entry_id)
