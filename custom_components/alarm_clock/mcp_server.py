"""FastMCP SSE server exposing alarm tools to the Assist LLM."""
from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from homeassistant.core import HomeAssistant

from .alarm_manager import AlarmManager
from .const import DEFAULT_SNOOZE_MINUTES, DOMAIN
from .media import list_sound_files

_LOGGER = logging.getLogger(__name__)


class AlarmMcpServer:
    """Wraps a FastMCP SSE app running on its own port inside HA's event loop."""

    def __init__(self, hass: HomeAssistant, port: int) -> None:
        self.hass = hass
        self.port = port
        self._task: asyncio.Task | None = None
        self._shutdown: asyncio.Event | None = None

    async def async_start(self) -> None:
        if self._task is not None:
            return
        self._shutdown = asyncio.Event()
        self._task = self.hass.async_create_background_task(
            self._run(), name=f"{DOMAIN}_mcp_server"
        )

    async def async_stop(self) -> None:
        if self._shutdown is not None:
            self._shutdown.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None

    async def _run(self) -> None:
        try:
            from mcp.server.fastmcp import FastMCP
        except ImportError:
            _LOGGER.error(
                "mcp package not installed — Alarm Clock MCP server cannot start"
            )
            return

        manager: AlarmManager = self.hass.data[DOMAIN]["manager"]
        mcp = FastMCP("alarm-clock", port=self.port, host="0.0.0.0")

        @mcp.tool()
        async def set_alarm(
            name: str,
            time: str,
            days: str = "once",
            sound_file: str | None = None,
            media_player: str | None = None,
            volume: float | None = None,
            ramp_duration: int | None = None,
            ramp_start: float | None = None,
            loop: bool | None = None,
        ) -> str:
            """Create or replace a wake-up alarm.

            days: "once" | "daily" | "weekdays" | "weekends" | comma list like "mon,wed,fri"
            time: "06:30" | "6:30am" | "5pm" | "noon"
            volume: target volume 0.0-1.0
            ramp_duration: seconds to ramp from ramp_start to volume (0 disables)
            """
            payload: dict[str, Any] = {"name": name, "time": time, "days": days}
            if sound_file is not None:
                payload["sound_file"] = sound_file
            if media_player is not None:
                payload["media_player"] = media_player
            if volume is not None:
                payload["volume"] = volume
            if ramp_duration is not None:
                payload["ramp_duration"] = ramp_duration
            if ramp_start is not None:
                payload["ramp_start"] = ramp_start
            if loop is not None:
                payload["loop"] = loop

            await self.hass.services.async_call(
                DOMAIN, "set", payload, blocking=True
            )
            alarm = manager.get(name)
            if alarm is None:
                return f"Failed to create alarm {name!r}."
            return (
                f"Alarm {name!r} set for {alarm.time} ({alarm.days}). "
                f"Next fire: {alarm.next_fire or 'unscheduled'}."
            )

        @mcp.tool()
        async def cancel_alarm(name: str) -> str:
            """Cancel an alarm by name."""
            await self.hass.services.async_call(
                DOMAIN, "cancel", {"name": name}, blocking=True
            )
            return f"Cancelled alarm {name!r}."

        @mcp.tool()
        async def list_alarms() -> list[dict[str, Any]]:
            """List all configured alarms with their next fire times."""
            return [
                {
                    "name": a.name,
                    "time": a.time,
                    "days": a.days,
                    "enabled": a.enabled,
                    "next_fire": a.next_fire,
                    "sound_file": a.sound_file,
                    "media_player": a.media_player,
                    "volume": a.volume,
                    "ramp_duration": a.ramp_duration,
                    "loop": a.loop,
                }
                for a in manager.alarms.values()
            ]

        @mcp.tool()
        async def snooze_alarm(name: str, minutes: int = DEFAULT_SNOOZE_MINUTES) -> str:
            """Stop the currently ringing alarm and re-fire after N minutes."""
            await self.hass.services.async_call(
                DOMAIN, "snooze", {"name": name, "minutes": minutes}, blocking=True
            )
            return f"Snoozed {name!r} for {minutes} minutes."

        @mcp.tool()
        async def dismiss_alarm(name: str) -> str:
            """Stop the currently ringing alarm. Recurring alarms keep their schedule."""
            await self.hass.services.async_call(
                DOMAIN, "dismiss", {"name": name}, blocking=True
            )
            return f"Dismissed {name!r}."

        @mcp.tool()
        async def get_sound_files() -> list[str]:
            """List available alarm sound filenames in /config/alarm_sounds."""
            return list_sound_files()

        @mcp.tool()
        async def get_media_players() -> list[dict[str, str]]:
            """Return media player entities with friendly names so the LLM can pick one."""
            out: list[dict[str, str]] = []
            for state in self.hass.states.async_all("media_player"):
                out.append(
                    {
                        "entity_id": state.entity_id,
                        "name": state.attributes.get("friendly_name", state.entity_id),
                        "state": state.state,
                    }
                )
            return out

        try:
            await mcp.run_sse_async()
        except asyncio.CancelledError:
            raise
        except Exception as err:  # pragma: no cover - server lifecycle
            _LOGGER.exception("Alarm Clock MCP server crashed: %s", err)
