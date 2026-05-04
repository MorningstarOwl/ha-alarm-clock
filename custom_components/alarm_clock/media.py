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
    MediaPlayerEntityFeature,
    SERVICE_PLAY_MEDIA,
)
from homeassistant.const import ATTR_ENTITY_ID, STATE_IDLE, STATE_OFF, STATE_PAUSED
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers.event import async_track_state_change_event

from .const import SOUND_FOLDER

_LOGGER = logging.getLogger(__name__)

AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac", ".opus"}
QUIESCENT_STATES = {STATE_IDLE, STATE_OFF, STATE_PAUSED, "standby", "stopped"}
PLAYING_STATES = {"playing", "buffering"}
RAMP_STEPS = 20
# Minimum seconds between consecutive replays for a single ringing alarm.
# Guards against feedback loops if a media player rapidly toggles state
# (e.g. when a file fails to play and the player stays/returns to idle).
REPLAY_MIN_INTERVAL = 5.0


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
    has_played: bool = False  # true once the player has entered a PLAYING_STATES
    last_replay_at: float = 0.0  # monotonic timestamp of the last replay dispatch
    used_native_repeat: bool = False  # we set repeat=all on the player

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

        # Decide how to handle looping. When the player supports REPEAT_SET
        # natively (Music Assistant, MPD, Squeezelite, most modern players)
        # the cleanest approach is to set repeat=all and play once. The
        # state-transition watcher is a fallback for players that don't.
        use_native_repeat = loop and self._supports_repeat(media_player)

        # Set initial volume before playback so the first beat lands at the right level
        initial_volume = ramp_start if ramp_duration > 0 else target_volume
        await self._set_volume(media_player, initial_volume)

        if use_native_repeat:
            await self._set_repeat(media_player, "all")
            handle.used_native_repeat = True

        await self._play_media(media_player, sound_path)

        if ramp_duration > 0 and target_volume > ramp_start:
            handle.ramp_task = self.hass.async_create_task(
                self._ramp(media_player, ramp_start, target_volume, ramp_duration)
            )

        # Only install the state-change watcher if loop is requested AND
        # we couldn't satisfy it via native repeat.
        if loop and not use_native_repeat:
            handle.loop_unsub = self._install_loop_watcher(name, media_player, sound_path)

    async def stop_ring(self, name: str) -> None:
        """Cancel ramp + loop watcher and stop the media player."""
        handle = self._handles.pop(name, None)
        if handle is None:
            return
        handle.cancel()
        if handle.used_native_repeat:
            # Restore the player's loop mode so non-alarm playback isn't sticky.
            await self._set_repeat(handle.media_player, "off")
        try:
            await self.hass.services.async_call(
                MEDIA_PLAYER_DOMAIN,
                "media_stop",
                {ATTR_ENTITY_ID: handle.media_player},
                blocking=False,
            )
        except Exception as err:  # pragma: no cover - defensive
            _LOGGER.debug("media_stop failed for %s: %s", handle.media_player, err)

    # ------------------------------------------------------------------
    # Service helpers
    # ------------------------------------------------------------------

    def _supports_repeat(self, media_player: str) -> bool:
        """Best-effort capability probe for media_player.repeat_set."""
        state = self.hass.states.get(media_player)
        if state is None:
            return False
        try:
            features = int(state.attributes.get("supported_features") or 0)
        except (TypeError, ValueError):
            return False
        return bool(features & MediaPlayerEntityFeature.REPEAT_SET)

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

    async def _set_repeat(self, media_player: str, mode: str) -> None:
        """Set repeat mode (all/one/off). Best-effort; errors are logged only."""
        try:
            await self.hass.services.async_call(
                MEDIA_PLAYER_DOMAIN,
                "repeat_set",
                {ATTR_ENTITY_ID: media_player, "repeat": mode},
                blocking=False,
            )
        except Exception as err:  # pragma: no cover - defensive
            _LOGGER.debug("repeat_set %s failed for %s: %s", mode, media_player, err)

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

    # ------------------------------------------------------------------
    # Fallback loop watcher (only used when REPEAT_SET is unsupported)
    # ------------------------------------------------------------------

    def _install_loop_watcher(
        self, name: str, media_player: str, sound_path: str
    ):
        """Replay the sound on a clean playing→idle transition.

        Guards against three failure modes that previously could feedback-loop:
        - attribute-only state changes (e.g. volume_set during ramping) re-firing
          the watcher
        - replaying when the player never reached "playing" (e.g. a broken file
          path leaves it stuck in idle and we'd spin)
        - replays issued faster than REPLAY_MIN_INTERVAL
        """

        @callback
        def _on_state(event) -> None:
            new_state: State | None = event.data.get("new_state")
            old_state: State | None = event.data.get("old_state")
            if new_state is None:
                return
            handle = self._handles.get(name)
            if handle is None:
                return
            # Ignore attribute-only updates; only act on actual state transitions.
            if old_state is not None and old_state.state == new_state.state:
                return
            if new_state.state in PLAYING_STATES:
                handle.has_played = True
                return
            if new_state.state not in QUIESCENT_STATES:
                return
            if not handle.has_played:
                # Player never reached playback — don't try again or we'll spin.
                return
            now = self.hass.loop.time()
            if now - handle.last_replay_at < REPLAY_MIN_INTERVAL:
                return
            handle.last_replay_at = now
            handle.has_played = False  # require another playing→idle cycle
            self.hass.async_create_task(self._play_media(media_player, sound_path))

        return async_track_state_change_event(self.hass, [media_player], _on_state)
