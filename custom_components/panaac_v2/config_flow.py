"""Config flow for the PanaAC v2 MQTT integration."""

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow
from homeassistant.data_entry_flow import FlowResult

from .const import CONF_TOPIC_PREFIX, DEFAULT_TOPIC_PREFIX, DOMAIN


class PanaACV2ConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for PanaAC v2."""

    VERSION = 1

    async def async_step_user(self, user_input: dict | None = None) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            topic_prefix = user_input[CONF_TOPIC_PREFIX]
            await self.async_set_unique_id(topic_prefix)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=f"PanaAC v2 ({topic_prefix})",
                data=user_input,
            )

        data_schema = vol.Schema(
            {
                vol.Required(CONF_TOPIC_PREFIX, default=DEFAULT_TOPIC_PREFIX): str,
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors,
        )
