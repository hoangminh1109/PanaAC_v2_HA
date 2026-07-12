# Copyright 2026 Minh Hoang
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Config flow for the PanaAC v2 MQTT integration."""

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow
from homeassistant.data_entry_flow import FlowResult

from .const import (
    CONF_DEVICE_NAME,
    CONF_TOPIC_PREFIX,
    DEFAULT_DEVICE_NAME,
    DEFAULT_TOPIC_PREFIX,
    DOMAIN,
)


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
                title=f"{user_input[CONF_DEVICE_NAME]} ({topic_prefix})",
                data=user_input,
            )

        data_schema = vol.Schema(
            {
                vol.Required(
                    CONF_DEVICE_NAME, default=DEFAULT_DEVICE_NAME
                ): str,
                vol.Required(
                    CONF_TOPIC_PREFIX, default=DEFAULT_TOPIC_PREFIX
                ): str,
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors,
        )
# Copyright 2026 Minh Hoang
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
