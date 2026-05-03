"""Config flow for the Alarm Clock integration."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .const import (
    CONF_DEFAULT_LOOP,
    CONF_DEFAULT_MEDIA_PLAYER,
    CONF_DEFAULT_RAMP_DURATION,
    CONF_DEFAULT_RAMP_START,
    CONF_DEFAULT_SOUND,
    CONF_DEFAULT_VOLUME,
    CONF_MCP_PORT,
    DEFAULT_LOOP,
    DEFAULT_MCP_PORT,
    DEFAULT_RAMP_DURATION,
    DEFAULT_RAMP_START,
    DEFAULT_VOLUME,
    DOMAIN,
)


def _schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_MCP_PORT, default=defaults.get(CONF_MCP_PORT, DEFAULT_MCP_PORT)
            ): vol.All(int, vol.Range(min=1024, max=65535)),
            vol.Optional(
                CONF_DEFAULT_MEDIA_PLAYER,
                default=defaults.get(CONF_DEFAULT_MEDIA_PLAYER, ""),
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="media_player")
            ),
            vol.Optional(
                CONF_DEFAULT_SOUND, default=defaults.get(CONF_DEFAULT_SOUND, "")
            ): str,
            vol.Required(
                CONF_DEFAULT_LOOP, default=defaults.get(CONF_DEFAULT_LOOP, DEFAULT_LOOP)
            ): bool,
            vol.Required(
                CONF_DEFAULT_VOLUME,
                default=defaults.get(CONF_DEFAULT_VOLUME, DEFAULT_VOLUME),
            ): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=1.0)),
            vol.Required(
                CONF_DEFAULT_RAMP_DURATION,
                default=defaults.get(CONF_DEFAULT_RAMP_DURATION, DEFAULT_RAMP_DURATION),
            ): vol.All(int, vol.Range(min=0, max=600)),
            vol.Required(
                CONF_DEFAULT_RAMP_START,
                default=defaults.get(CONF_DEFAULT_RAMP_START, DEFAULT_RAMP_START),
            ): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=1.0)),
        }
    )


class AlarmClockConfigFlow(ConfigFlow, domain=DOMAIN):
    """Initial setup flow."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        if user_input is not None:
            return self.async_create_entry(title="Alarm Clock", data=user_input)

        return self.async_show_form(step_id="user", data_schema=_schema({}))

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return AlarmClockOptionsFlow(entry)


class AlarmClockOptionsFlow(OptionsFlow):
    """Allow editing defaults after initial setup."""

    def __init__(self, entry: ConfigEntry) -> None:
        self.entry = entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        defaults = {**self.entry.data, **self.entry.options}
        return self.async_show_form(step_id="init", data_schema=_schema(defaults))
