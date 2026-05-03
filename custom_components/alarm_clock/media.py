"""Sound discovery, media playback, looping, and volume ramping."""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from homeassistant.components.media_player import (
    ATTR_MEDIA_CONTENT_ID,
    ATTR_MEDIA_CONTENT_TYPE,
    DOMAIN as MEDIA_PLAYER_DOMAIN,
    SERVICE_PLAY_MEDIA,
)
from homeassistant.const import ATTR_ENTITY_ID, STATE_IDLE, STATE_OFF, STATE_PAUSED
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers.event import async_track_state_change_event

from .const import SOUND_FOLDER

_LOGGER = logging.getLogger(__name__)

AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac", ".opus"}
QUIESCENT_STATES = {STATE_IDLE, STATE_OFF, STATE_PAUSED, "standby", "stopped"}
RAMP_STEPS = 20


def list_sound_files() -> list[str]:
    """Return filenames in the sound folder, sorted."""
    folder = Path(SOUND_FOLDER)
    if not folder.exists():
        return []
    return sorted(
        f.name
        for f in folder.iterdir()
        if f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS
    )


def ensure_sound_folder() -> None:
    """Create the sound folder if it does not exist."""
    try:
        os.makedirs(SOUND_FOLDER, exist_ok=True)
    except OSError as err:
        _LOGGER.warning("Could not create %s: %s", SOUND_FOLDER, err)


def resolve_sound_path(filename: str | None, default: str | None) -> str | None:
    """Resolve a sound filename to an absolute path under SOUND_FOLDER."""
    name = (filename or default or "").strip()
    if not name:
        files = list_sound_files()
        if not files:
            return None
        name = files[0]
    candidate = Path(SOUND_FOLDER) / name
    if not candidate.exists():
        _LOGGER.warning("Sound file not found: %s", candidate)
        return None
    return str(candidate)


@dataclass
class RingHandle:
    """Tracks the running tasks for a single ringing alarm."""

    name: str
    media_player: str
    ramp_task: asyncio.Task | None = None
    loop_unsub: callable | None = None

    def cancel(self) -> None:
        if self.ramp_task and not self.ramp_task.done():
            self.ramp_task.cancel()
        if self.loop_unsub:
            try:
                self.loop_unsub()
            except Exception:  # pragma: no cover - defensive
                pass
        self.loop_unsub = None


class MediaController:
    """Owns ringing state for all alarms and dispatches play / stop / ramp."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._handles: dict[str, RingHandle] = {}

    def is_ringing(self, name: str) -> bool:
        return name in self._handles

    async def start_ring(
        self,
        name: str,
        media_player: str,
        sound_path: str,
        target_volume: float,
        loop: bool,
        ramp_duration: int,
        ramp_start: float,
    ) -> None:
        """Start playing the sound, with optional volume ramping and looping."""
        await self.stop_ring(name)  # idempotent

        handle = RingHandle(name=name, media_player=media_player)
        self._handles[name] = handle

        # Set initial volume before playback so the first beat lands at the right level
        initial_volume = ramp_start if ramp_duration > 0 else target_volume
        await self._set_volume(media_player, initial_volume)
        await self._play_media(media_player, sound_path)

        if ramp_duration > 0 and target_volume > ramp_start:
            handle.ramp_task = self.hass.async_create_task(
                self._ramp(media_player, ramp_start, target_volume, ramp_duration)
            )

        if loop:
            handle.loop_unsub = self._install_loop_watcher(name, media_player, sound_path)

    async def stop_ring(self, name: str) -> None:
        """Cancel ramp + loop watcher and stop the media player."""
        handle = self._handles.pop(name, None)
        if handle is None:
            return
        handle.cancel()
        try:
            await self.hass.services.async_call(
                MEDIA_PLAYER_DOMAIN,
                "media_stop",
                {ATTR_ENTITY_ID: handle.media_player},
                blocking=False,
            )
        except Exception as err:  # pragma: no cover - defensive
            _LOGGER.debug("media_stop failed for %s: %s", handle.media_player, err)

    async def _play_media(self, media_player: str, sound_path: str) -> None:
        await self.hass.services.async_call(
            MEDIA_PLAYER_DOMAIN,
            SERVICE_PLAY_MEDIA,
            {
                ATTR_ENTITY_ID: media_player,
                ATTR_MEDIA_CONTENT_ID: sound_path,
                ATTR_MEDIA_CONTENT_TYPE: "music",
            },
            blocking=False,
        )

    async def _set_volume(self, media_player: str, volume: float) -> None:
        volume = max(0.0, min(1.0, float(volume)))
        try:
            await self.hass.services.async_call(
                MEDIA_PLAYER_DOMAIN,
                "volume_set",
                {ATTR_ENTITY_ID: media_player, "volume_level": volume},
                blocking=False,
            )
        except Exception as err:  # pragma: no cover - defensive
            _LOGGER.debug("volume_set failed for %s: %s", media_player, err)

    async def _ramp(
        self, media_player: str, start: float, end: float, duration: int
    ) -> None:
        """Linearly ramp volume from start to end over `duration` seconds."""
        try:
            step_duration = duration / RAMP_STEPS
            delta = (end - start) / RAMP_STEPS
            for i in range(1, RAMP_STEPS + 1):
                await asyncio.sleep(step_duration)
                await self._set_volume(media_player, start + delta * i)
        except asyncio.CancelledError:
            pass

    def _install_loop_watcher(
        self, name: str, media_player: str, sound_path: str
    ):
        """Replay the sound whenever the media player goes idle."""

        @callback
        def _on_state(event) -> None:
            new_state: State | None = event.data.get("new_state")
            if new_state is None:
                return
            if name not in self._handles:
                return
            if new_state.state in QUIESCENT_STATES:
                # Re-issue play_media; volume stays at whatever the ramp left it.
                self.hass.async_create_task(self._play_media(media_player, sound_path))

        return async_track_state_change_event(self.hass, [media_player], _on_state)
