# Alarm Clock

A self-contained Home Assistant alarm-clock integration with custom ringtones, recurrence patterns, configurable volume ramping, looping playback, and a built-in MCP SSE server so the Assist LLM can manage alarms by voice.

Scheduling is handled natively via `async_track_point_in_time` — no external scheduler dependency.

---

## Requirements

- Home Assistant 2024.6 or newer
- A media player entity for playback (any HA-integrated speaker)

---

## Installation (HACS)

1. In HACS, add this repo as a **custom repository** (type: Integration)
2. Search for **Alarm Clock** and install
3. Restart Home Assistant
4. Go to **Settings → Devices & Services → Add Integration → Alarm Clock**
5. Configure the MCP port (default `8778`), default media player, default sound, and ramping defaults

Sound files go in `/config/alarm_sounds/` — the folder is created automatically. Drop in any `.mp3`, `.wav`, `.ogg`, `.flac`, `.m4a`, `.aac`, or `.opus` file.

---

## Recurrence patterns

| Value | Behavior |
|---|---|
| `once` | Single fire; alarm definition is removed when dismissed or cancelled |
| `daily` | Every day |
| `weekdays` | Mon–Fri |
| `weekends` | Sat, Sun |
| `mon,wed,fri` (or list) | Custom day list |

Day slugs: `mon` `tue` `wed` `thu` `fri` `sat` `sun`.

---

## Services

### `alarm_clock.set`

Create or replace an alarm.

| Field | Type | Default | Description |
|---|---|---|---|
| `name` | string | required | Slug, e.g. `weekday_wakeup` |
| `time` | string | required | `06:30`, `6:30am`, `5pm`, `noon`, `midnight` |
| `days` | string \| list | `once` | Recurrence pattern (see above) |
| `sound_file` | string | configured default | Filename from `/config/alarm_sounds/` |
| `media_player` | entity_id | configured default | Speaker to play on |
| `volume` | float (0–1) | configured default | Target volume after ramp |
| `ramp_duration` | int (seconds) | configured default | 0 disables ramp |
| `ramp_start` | float (0–1) | configured default | Volume at start of ramp |
| `loop` | bool | configured default | Replay until dismissed |
| `enabled` | bool | `true` | Stored but not scheduled when false |
| `one_shot_date` | string | — | `YYYY-MM-DD`, only used with `days: once` |

```yaml
action: alarm_clock.set
data:
  name: weekday_wakeup
  time: "6:30am"
  days: weekdays
  sound_file: gentle_chimes.mp3
  media_player: media_player.bedroom
  volume: 0.7
  ramp_duration: 60
  ramp_start: 0.05
  loop: true
```

### `alarm_clock.cancel`

| Field | Description |
|---|---|
| `name` | Alarm to delete (also stops it if currently ringing) |

### `alarm_clock.snooze`

Stops the current ring and arms a one-shot fire N minutes from now. The next regular occurrence is unaffected.

| Field | Default | Description |
|---|---|---|
| `name` | required | Alarm to snooze |
| `minutes` | `9` | Snooze duration |

### `alarm_clock.dismiss`

Stops the current ring. Recurring alarms keep their next-fire schedule; one-shot alarms are removed.

### `alarm_clock.ring`

Internal — invoked by the integration's own scheduler when an alarm fires. Safe to call manually for testing.

---

## Entities

### `sensor.alarm_clock_{name}`

State is the next fire datetime (ISO-8601). Attributes expose the full alarm definition: `time`, `days`, `sound_file`, `media_player`, `volume`, `ramp_duration`, `ramp_start`, `loop`, `enabled`, `next_fire`.

### `sensor.alarm_clock_count`

State is the number of configured alarms. Attribute `alarms` is a `name → next_fire` map; `enabled_count` shows how many are active.

### `binary_sensor.alarm_clock_ringing_{name}`

`on` while an alarm is actively ringing. Useful for triggering automations such as "turn on the bedroom lights when the alarm rings."

---

## Events

### `alarm_clock_triggered`

Fired when an alarm starts ringing.

```yaml
event_type: alarm_clock_triggered
event_data:
  name: weekday_wakeup
  sound_file: gentle_chimes.mp3
  media_player: media_player.bedroom
  was_snooze: false
```

Use this to layer extra behaviors — gradual lights, TTS, blinds, etc.

---

## Volume ramping

When an alarm fires, the media player's volume is set to `ramp_start`, playback begins, and the volume is then linearly interpolated toward `volume` over `ramp_duration` seconds in 20 steps.

Set `ramp_duration: 0` to disable ramping (alarm starts at full target volume).

The ramp is cancelled when the alarm is snoozed or dismissed.

---

## Looping

When `loop: true` and the target media player advertises the `REPEAT_SET` feature (Music Assistant, MPD, Squeezelite, most modern players), the integration sets `repeat_set: all` before starting playback and lets the player loop natively. On stop, repeat is set back to `off` so non-alarm playback isn't sticky.

For players that don't support repeat, the integration falls back to a state-change watcher: when the player transitions from `playing` to a quiescent state, it re-issues `media_player.play_media`. The watcher debounces to one replay every 5 seconds and won't replay if the player never reached a `playing` state at all.

---

## MCP voice interface

The integration runs a FastMCP SSE server on the configured port (default `8778`). Add it as an MCP integration:

**Settings → Devices & Services → Add Integration → Model Context Protocol**

```
http://homeassistant.local:8778/sse
```

### Tools

| Tool | Purpose |
|---|---|
| `set_alarm(name, time, days, sound_file?, volume?, ramp_duration?, ramp_start?, loop?, media_player?)` | Create an alarm |
| `cancel_alarm(name)` | Delete an alarm |
| `list_alarms()` | Return all alarms with next fire times |
| `snooze_alarm(name, minutes?)` | Snooze the current ring |
| `dismiss_alarm(name)` | Stop the current ring |
| `get_sound_files()` | List available ringtones |
| `get_media_players()` | List media player entities + friendly names |

### Example phrases

- *"Wake me up at 6:30 every weekday"*
- *"Set a 7am alarm for tomorrow on the bedroom speaker"*
- *"Snooze for 10 minutes"*
- *"Dismiss the alarm"*
- *"What alarms do I have set?"*
- *"What ringtones are available?"*

---

## Architecture

```
User / LLM / Automation
  ↓
alarm_clock.set service
  ↓
AlarmManager.async_set:
  - stores definition in .storage/alarm_clock
  - calculates next occurrence
  - arms async_track_point_in_time(_fire, when)

[HA waits for the time]
  ↓ fires
AlarmManager._fire (callback):
  - dispatches alarm_clock.ring service

alarm_clock.ring service:
  - looks up the stored alarm
  - MediaController.start_ring:
      • set initial (ramp_start) volume
      • set repeat=all if supported, else install loop watcher
      • play_media
      • async ramp task to target volume
  - calls AlarmManager.async_handle_fire to queue the next regular
    occurrence (recurring) or clear next_fire (one-shot)
  - fires alarm_clock_triggered event
```

On HA startup, `async_reconcile()` walks every enabled alarm and re-arms its timer (timers don't survive restart). Any one-shot whose date+time has already passed is dropped at this point so storage stays clean.

---

## Troubleshooting

**Alarm fires but nothing plays**
- Check `/config/alarm_sounds/` contains the file (or any file, if no `sound_file` was given)
- Verify the media player entity is reachable
- Look in **Settings → System → Logs** for `alarm_clock` errors

**Alarm doesn't fire**
- Check `sensor.alarm_clock_{name}` — its state should be the upcoming ISO datetime
- If `next_fire` is `None`, the recurrence pattern produced no future match (e.g. an expired one-shot)
- Enable debug logging for the integration to see arm/disarm events:
  ```yaml
  logger:
    logs:
      custom_components.alarm_clock: debug
  ```

**Looping doesn't work**
- The integration auto-detects native `repeat_set` support. If your player advertises the feature but doesn't honor it, set `loop: false` and use a longer/looped audio file directly.
- For players without repeat support, the fallback watcher needs the player to transition cleanly from `playing` to `idle`/`paused`/`off`. Some players hang on `unknown` instead — set `loop: false` if that's the case.

**MCP client can't connect**
- Confirm the port matches your config flow setting (default `8778`)
- The MCP SSE server lives inside Home Assistant — check **Settings → System → Logs** for FastMCP startup errors

---

## Upgrading from 0.1.x

Versions before 0.2.0 used [Simple Cue](https://github.com/MorningstarOwl/simple-cue) as the scheduler. Starting with 0.2.0 the integration schedules natively and no longer depends on Simple Cue.

The upgrade is in-place — alarm definitions stored under v0.1.x load and re-arm automatically. On first start of v0.2.0, the integration also issues a best-effort `simple_cue.cancel` for each `alarm_clock__{name}` cue so your alarms don't double-fire while Simple Cue still has stale entries. If you've already removed Simple Cue, that's a no-op.

You can keep Simple Cue installed alongside Alarm Clock — the two are now fully independent.

---

## License

MIT
