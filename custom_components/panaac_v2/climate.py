"""Climate platform for PanaAC v2."""

import json
import logging

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
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
    _attr_hvac_modes = list(_DEFAULT_HVAC_MODES)
    _attr_fan_modes = list(_DEFAULT_FAN_MODES)
    _attr_swing_modes = list(_DEFAULT_SWING_MODES)
    _attr_swing_horizontal_modes = list(_DEFAULT_SWING_HORIZONTAL_MODES)
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
        self._fan_mode = "Auto"
        self._swing_mode = "Auto"
        self._swing_horizontal_mode = "Auto"

        self._sub_state: dict | None = None

    @property
    def supported_features(self) -> ClimateEntityFeature:
        """Return the list of supported features."""
        features = (
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.FAN_MODE
            | ClimateEntityFeature.SWING_MODE
        )
        if self._attr_swing_horizontal_modes:
            features |= ClimateEntityFeature.SWING_HORIZONTAL_MODE
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

        _LOGGER.debug("Received state: %s", payload)
        self._hvac_mode = self._hvac_mode_from_str(payload.get("mode", "off"))
        self._target_temperature = payload.get("target_temperature", self._target_temperature)
        self._fan_mode = payload.get("fan_mode", self._fan_mode)
        self._swing_mode = payload.get("swing_mode", self._swing_mode)
        self._swing_horizontal_mode = payload.get("swing_horizontal_mode")
        self._current_temperature = payload.get("current_temperature")
        self.async_write_ha_state()

    @callback
    def _handle_traits(self, msg: ReceiveMessage) -> None:
        """Process a traits message from the device."""
        try:
            payload = json.loads(msg.payload)
        except (json.JSONDecodeError, TypeError):
            _LOGGER.warning("Invalid JSON traits payload: %s", msg.payload)
            return

        _LOGGER.debug("Received traits: %s", payload)
        if "hvac_modes" in payload:
            self._attr_hvac_modes = [
                self._hvac_mode_from_str(m) for m in payload["hvac_modes"]
            ]
        if "fan_modes" in payload:
            self._attr_fan_modes = payload["fan_modes"]
        if "swing_modes" in payload:
            self._attr_swing_modes = payload["swing_modes"]
        if "swing_horizontal_modes" in payload:
            self._attr_swing_horizontal_modes = payload["swing_horizontal_modes"]
        else:
            self._attr_swing_horizontal_modes = []
        self._attr_min_temp = payload.get("min_temp", self._attr_min_temp)
        self._attr_max_temp = payload.get("max_temp", self._attr_max_temp)
        self._attr_target_temperature_step = payload.get(
            "temp_step", self._attr_target_temperature_step
        )
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
        """Set a new target temperature."""
        temperature = kwargs.get("temperature")
        if temperature is not None:
            await self._publish_command({"target_temperature": temperature})

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
