# Changelog

## 0.2.0 — Native scheduling

**Architectural change.** Alarm Clock no longer depends on Simple Cue.
Scheduling is now handled natively via `async_track_point_in_time`,
removing a layer of indirection that was the source of timing and
delivery flakiness in the 0.1.x series.

### Changed
- `manifest.json` — `dependencies: ["simple_cue"]` removed.
- `AlarmManager` — replaced the `simple_cue.set` / `simple_cue.cancel`
  bridge with an in-memory dict of `async_track_point_in_time` unsub
  callbacks. Two keys per alarm at most: `alarm_clock__{name}` for the
  regular fire, `alarm_clock__{name}__snooze` for an outstanding snooze.
- `MediaController.start_ring` — when the target player advertises
  `MediaPlayerEntityFeature.REPEAT_SET`, the integration now sets
  `repeat: all` and skips the state-change watcher entirely. The watcher
  remains as a fallback for players without native repeat. Repeat is
  reset to `off` on `stop_ring`.
- One-shot alarms now persist until the user dismisses them, so
  `snooze` works after the initial fire. Stale one-shots whose date+time
  has already passed are dropped at next HA startup.
- `__init__.async_unload_entry` calls `manager.async_unload()` to cancel
  every armed timer cleanly when the integration is reloaded or removed.

### Added
- One-shot migration step: on first start of 0.2.0, if Simple Cue is
  still installed, the integration issues a best-effort `simple_cue.cancel`
  for each `alarm_clock__{name}` cue parked there in 0.1.x. Silent no-op
  when Simple Cue is absent.

### Removed
- `cue_to_alarm_name` and `is_snooze_cue` helpers (dead code in 0.1.x —
  the integration never listened for `simple_cue_triggered` events).
- `EVENT_SIMPLE_CUE_TRIGGERED` constant.

### Notes
- Alarm definitions stored under 0.1.x load and re-arm automatically.
  No manual migration is required.
- Storage version is unchanged (1).
- MCP tool surface is unchanged.

## 0.1.1

- Initial public release as a Simple Cue companion.
