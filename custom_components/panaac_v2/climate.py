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

"""Climate platform for PanaAC v2."""

import json
import logging
import math
from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.components.mqtt.models import ReceiveMessage
from homeassistant.components.mqtt.subscription import (
    async_prepare_subscribe_topics,
    async_subscribe_topics,
    async_unsubscribe_topics,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_DEVICE_NAME, CONF_TOPIC_PREFIX, DEFAULT_DEVICE_NAME, DOMAIN

_LOGGER = logging.getLogger(__name__)

_DEFAULT_HVAC_MODES = [
    HVACMode.OFF,
    HVACMode.COOL,
    HVACMode.HEAT,
    HVACMode.FAN_ONLY,
    HVACMode.DRY,
    HVACMode.AUTO,
]
_DEFAULT_FAN_MODES = [
    "Auto",
    "Level 1",
    "Level 2",
    "Level 3",
    "Level 4",
    "Level 5",
    "Quiet",
]
_DEFAULT_SWING_MODES = [
    "Auto",
    "Highest",
    "High",
    "Middle",
    "Low",
    "Lowest",
]
_DEFAULT_SWING_HORIZONTAL_MODES = [
    "Auto",
    "Left Max",
    "Left",
    "Middle",
    "Right",
    "Right Max",
]

def _is_finite_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _is_string_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the PanaAC v2 climate entity."""
    async_add_entities([PanaACV2Climate(hass, entry)])


class PanaACV2Climate(ClimateEntity):
    """Representation of a PanaAC v2 climate device."""

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    # This integration is entirely MQTT push-driven: every state update arrives as a retained or
    # live MQTT message on the state/availability topics, so polling would only schedule no-op
    # update cycles. Opt out of polling explicitly (Codex review issue 1).
    _attr_should_poll = False
    # Conservative capability defaults until the first retained `traits` payload arrives
    # (Codex review issue 2). We start with the minimal shape needed to render safely — only OFF,
    # no fan/swing options and no horizontal swing — so a cold start or a lost retained-traits
    # message cannot expose controls the actual device does not support. The full capability set
    # is applied in `_handle_traits` once the device advertises it.
    _attr_hvac_modes = [HVACMode.OFF]
    _attr_fan_modes: list[str] = []
    _attr_swing_modes: list[str] = []
    _attr_swing_horizontal_modes: list[str] = []
    _attr_min_temp = 16
    _attr_max_temp = 30
    _attr_target_temperature_step = 0.5
    # Enables per-fan-mode icons from icons.json (entity.climate.remote_controller).
    # _attr_name below still wins for the entity name.
    _attr_translation_key = "remote_controller"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the climate entity."""
        self.hass = hass
        self._entry = entry
        self._topic_prefix = entry.data[CONF_TOPIC_PREFIX]
        self._attr_unique_id = f"{self._topic_prefix}_climate"
        self._attr_name = "Remote Controller (v2)"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._topic_prefix)},
            name=entry.data.get(CONF_DEVICE_NAME, DEFAULT_DEVICE_NAME),
            manufacturer="ESPHome",
            model="PanaAC v2",
        )

        # Initial placeholder state (matches the device's fresh-boot defaults: off, 26 °C, fan
        # Auto, vertical swing Auto, horizontal swing Auto). Overwritten by the first MQTT state.
        self._attr_available = False
        self._current_temperature: float | None = None
        self._target_temperature = 26.0
        self._hvac_mode = HVACMode.OFF
        # Matches the placeholder hvac_mode above (OFF → OFF). Recomputed from the
        # state payload in _handle_state; see _derive_hvac_action for the mapping.
        self._attr_hvac_action: HVACAction | None = HVACAction.OFF
        self._fan_mode = "Auto"
        self._swing_mode = "Auto"
        self._swing_horizontal_mode = "Auto"

        self._sub_state: dict | None = None

    @property
    def supported_features(self) -> ClimateEntityFeature:
        """Return the list of supported features.

        Each fan/swing feature is gated on its mode list being populated, so before the first
        retained `traits` payload arrives the entity only advertises TARGET_TEMPERATURE — the
        conservative control surface from Codex review issue 2. The full feature set is exposed
        once `_handle_traits` fills the mode lists.
        """
        features = ClimateEntityFeature.TARGET_TEMPERATURE
        if self._attr_fan_modes:
            features |= ClimateEntityFeature.FAN_MODE
        if self._attr_swing_modes:
            features |= ClimateEntityFeature.SWING_MODE
        if self._attr_swing_horizontal_modes:
            features |= ClimateEntityFeature.SWING_HORIZONTAL_MODE
        # Advertise turn on/off when the climate has more than one HVAC mode and
        # supports OFF, so it is detected as a thermostat in automations/scripts
        # (matches the built-in esphome climate). The base ClimateEntity
        # async_turn_on/async_turn_off forward to async_set_hvac_mode (OFF, or a
        # fake-on to the first available heat/cool mode), which publishes to the
        # device set topic.
        if len(self.hvac_modes) > 1 and HVACMode.OFF in self.hvac_modes:
            features |= ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF
        return features

    @property
    def current_temperature(self) -> float | None:
        """Return the current temperature."""
        return self._current_temperature

    @property
    def target_temperature(self) -> float | None:
        """Return the target temperature."""
        return self._target_temperature

    @property
    def hvac_mode(self) -> HVACMode:
        """Return the current HVAC mode."""
        return self._hvac_mode

    @property
    def fan_mode(self) -> str | None:
        """Return the current fan mode."""
        return self._fan_mode

    @property
    def swing_mode(self) -> str | None:
        """Return the current swing mode."""
        return self._swing_mode

    @property
    def swing_horizontal_mode(self) -> str | None:
        """Return the current horizontal swing mode."""
        return self._swing_horizontal_mode

    async def async_added_to_hass(self) -> None:
        """Subscribe to MQTT topics."""
        topics = {
            "state": {
                "topic": f"{self._topic_prefix}/state",
                "msg_callback": self._handle_state,
                "qos": 0,
            },
            "traits": {
                "topic": f"{self._topic_prefix}/traits",
                "msg_callback": self._handle_traits,
                "qos": 0,
            },
            "availability": {
                "topic": f"{self._topic_prefix}/availability",
                "msg_callback": self._handle_availability,
                "qos": 0,
            },
        }
        self._sub_state = async_prepare_subscribe_topics(
            self.hass, self._sub_state, topics
        )
        await async_subscribe_topics(self.hass, self._sub_state)

    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe from MQTT topics."""
        self._sub_state = async_unsubscribe_topics(self.hass, self._sub_state)

    @callback
    def _handle_state(self, msg: ReceiveMessage) -> None:
        """Process a state message from the device."""
        try:
            payload = json.loads(msg.payload)
        except (json.JSONDecodeError, TypeError):
            _LOGGER.warning("Invalid JSON state payload: %s", msg.payload)
            return
        if not isinstance(payload, dict):
            _LOGGER.warning("Invalid JSON state payload shape: expected object, got %s", type(payload).__name__)
            return

        _LOGGER.debug("Received state: %s", payload)
        mode = payload.get("mode")
        if isinstance(mode, str):
            self._hvac_mode = self._hvac_mode_from_str(mode)
        target_temperature = payload.get("target_temperature")
        if _is_finite_number(target_temperature):
            self._target_temperature = float(target_temperature)
        fan_mode = payload.get("fan_mode")
        if isinstance(fan_mode, str):
            self._fan_mode = fan_mode
        swing_mode = payload.get("swing_mode")
        if isinstance(swing_mode, str):
            self._swing_mode = swing_mode
        horizontal_mode = payload.get("swing_horizontal_mode")
        if isinstance(horizontal_mode, str):
            self._swing_horizontal_mode = horizontal_mode
        current_temperature = payload.get("current_temperature")
        if current_temperature is None or _is_finite_number(current_temperature):
            self._current_temperature = current_temperature
        self._attr_hvac_action = self._derive_hvac_action(
            self._hvac_mode, self._current_temperature, self._target_temperature
        )
        self.async_write_ha_state()

    @callback
    def _handle_traits(self, msg: ReceiveMessage) -> None:
        """Process a traits message from the device."""
        try:
            payload = json.loads(msg.payload)
        except (json.JSONDecodeError, TypeError):
            _LOGGER.warning("Invalid JSON traits payload: %s", msg.payload)
            return
        if not isinstance(payload, dict):
            _LOGGER.warning("Invalid JSON traits payload shape: expected object, got %s", type(payload).__name__)
            return

        _LOGGER.debug("Received traits: %s", payload)
        hvac_modes = payload.get("hvac_modes")
        if _is_string_list(hvac_modes):
            self._attr_hvac_modes = [self._hvac_mode_from_str(mode) for mode in hvac_modes]
        fan_modes = payload.get("fan_modes")
        if _is_string_list(fan_modes):
            self._attr_fan_modes = fan_modes
        swing_modes = payload.get("swing_modes")
        if _is_string_list(swing_modes):
            self._attr_swing_modes = swing_modes
        horizontal_modes = payload.get("swing_horizontal_modes")
        if _is_string_list(horizontal_modes):
            self._attr_swing_horizontal_modes = horizontal_modes
        elif "swing_horizontal_modes" not in payload:
            self._attr_swing_horizontal_modes = []
        min_temp = payload.get("min_temp")
        if _is_finite_number(min_temp):
            self._attr_min_temp = float(min_temp)
        max_temp = payload.get("max_temp")
        if _is_finite_number(max_temp):
            self._attr_max_temp = float(max_temp)
        temp_step = payload.get("temp_step")
        if _is_finite_number(temp_step) and temp_step > 0:
            self._attr_target_temperature_step = float(temp_step)
        self.async_write_ha_state()

    @callback
    def _handle_availability(self, msg: ReceiveMessage) -> None:
        """Process an availability message from the device."""
        _LOGGER.debug("Received availability: %s", msg.payload)
        self._attr_available = msg.payload == "online"
        self.async_write_ha_state()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set a new HVAC mode."""
        await self._publish_command({"mode": hvac_mode.value})

    async def async_set_temperature(self, **kwargs) -> None:
        """Set a new target temperature, and the HVAC mode if provided.

        The climate.set_temperature service (and the "set thermostat target
        temperature" device action) can carry an hvac_mode alongside the target
        temperature. Publish both in one command so a single call changes both,
        matching the ESPHome climate behaviour.
        """
        command: dict = {}
        if (temperature := kwargs.get("temperature")) is not None:
            command["target_temperature"] = temperature
        if (hvac_mode := kwargs.get("hvac_mode")) is not None:
            command["mode"] = hvac_mode.value
        if command:
            await self._publish_command(command)

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set a new fan mode."""
        await self._publish_command({"fan_mode": fan_mode})

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        """Set a new swing mode."""
        await self._publish_command({"swing_mode": swing_mode})

    async def async_set_swing_horizontal_mode(self, swing_horizontal_mode: str) -> None:
        """Set a new horizontal swing mode."""
        await self._publish_command({"swing_horizontal_mode": swing_horizontal_mode})

    async def _publish_command(self, payload: dict) -> None:
        """Publish a command to the device set topic."""
        from homeassistant.components import mqtt

        topic = f"{self._topic_prefix}/set"
        await mqtt.async_publish(
            self.hass,
            topic,
            json.dumps(payload),
            qos=0,
            retain=False,
        )

    @staticmethod
    def _hvac_mode_from_str(value: str) -> HVACMode:
        """Map a string to a Home Assistant HVACMode."""
        try:
            return HVACMode(value)
        except ValueError:
            return HVACMode.OFF

    @staticmethod
    def _derive_hvac_action(
        hvac_mode: HVACMode,
        current_temperature: float | None,
        target_temperature: float | None,
    ) -> HVACAction:
        """Derive the current HVAC action from the commanded mode.

        The PanaAC controller is a one-way IR transmitter: it knows the mode it
        last commanded and the room/setpoint temperatures the unit reports, but
        not whether the compressor is actually running. Map each mode to its
        corresponding action so the climate building-block triggers and
        conditions (``started_cooling``, ``is_heating``, …) fire. AUTO has no
        single fixed action, so infer it from the room vs. setpoint temperature —
        the same comparison the AC's own thermostat makes — and fall back to IDLE
        when the temperatures are unknown or the room is at setpoint.
        """
        if hvac_mode == HVACMode.OFF:
            return HVACAction.OFF
        if hvac_mode == HVACMode.COOL:
            return HVACAction.COOLING
        if hvac_mode == HVACMode.HEAT:
            return HVACAction.HEATING
        if hvac_mode == HVACMode.DRY:
            return HVACAction.DRYING
        if hvac_mode == HVACMode.FAN_ONLY:
            return HVACAction.FAN
        # HVACMode.AUTO — infer from current vs. target temperature.
        if current_temperature is not None and target_temperature is not None:
            if current_temperature > target_temperature:
                return HVACAction.COOLING
            if current_temperature < target_temperature:
                return HVACAction.HEATING
        return HVACAction.IDLE
