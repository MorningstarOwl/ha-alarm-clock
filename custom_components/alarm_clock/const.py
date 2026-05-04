"""Constants for the Alarm Clock integration."""
from __future__ import annotations

DOMAIN = "alarm_clock"

# Storage
STORAGE_KEY = "alarm_clock.alarms"
STORAGE_VERSION = 1

# Sound files folder (always /config/alarm_sounds inside HA)
SOUND_FOLDER = "/config/alarm_sounds"

# Internal scheduler key namespace (was CUE_PREFIX in v0.1.x when we
# delegated to Simple Cue; kept the same string for log continuity).
KEY_PREFIX = "alarm_clock__"
SNOOZE_SUFFIX = "__snooze"

# Events
EVENT_TRIGGERED = "alarm_clock_triggered"

# Services
SERVICE_SET = "set"
SERVICE_CANCEL = "cancel"
SERVICE_SNOOZE = "snooze"
SERVICE_DISMISS = "dismiss"
SERVICE_RING = "ring"

# Config flow keys
CONF_MCP_PORT = "mcp_port"
CONF_DEFAULT_MEDIA_PLAYER = "default_media_player"
CONF_DEFAULT_SOUND = "default_sound"
CONF_DEFAULT_LOOP = "default_loop"
CONF_DEFAULT_VOLUME = "default_volume"
CONF_DEFAULT_RAMP_DURATION = "default_ramp_duration"
CONF_DEFAULT_RAMP_START = "default_ramp_start"

# Defaults
DEFAULT_MCP_PORT = 8778
DEFAULT_LOOP = True
DEFAULT_VOLUME = 0.7
DEFAULT_RAMP_DURATION = 30  # seconds; 0 disables ramping
DEFAULT_RAMP_START = 0.1
DEFAULT_SNOOZE_MINUTES = 9

# Recurrence patterns
PATTERN_ONCE = "once"
PATTERN_DAILY = "daily"
PATTERN_WEEKDAYS = "weekdays"
PATTERN_WEEKENDS = "weekends"
DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

# Alarm definition keys
ATTR_NAME = "name"
ATTR_TIME = "time"
ATTR_DAYS = "days"
ATTR_SOUND_FILE = "sound_file"
ATTR_MEDIA_PLAYER = "media_player"
ATTR_VOLUME = "volume"
ATTR_RAMP_DURATION = "ramp_duration"
ATTR_RAMP_START = "ramp_start"
ATTR_LOOP = "loop"
ATTR_ENABLED = "enabled"
ATTR_NEXT_FIRE = "next_fire"
ATTR_ONE_SHOT_DATE = "one_shot_date"
ATTR_MINUTES = "minutes"
