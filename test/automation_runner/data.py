"""Static test data for the PanaAC v2 automation runner."""

from __future__ import annotations

from typing import Any

TARGET_TEMPERATURE = 1
FAN_MODE = 8
SWING_MODE = 32
TURN_OFF = 128
TURN_ON = 256
SWING_HORIZONTAL_MODE = 512

DEFAULT_TOPIC_PREFIX = "panaac_v2/esphome-panaac-v2"
DEFAULT_MQTT_HOST = "127.0.0.1"
DEFAULT_MQTT_PORT = 1883

CURRENT_STATE_KEYS = (
    "state",
    "hvac_action",
    "temperature",
    "current_temperature",
    "fan_mode",
    "swing_mode",
    "swing_horizontal_mode",
)

RETAINED_BASELINE_TRAITS = {
    "hvac_modes": ["off", "cool", "heat", "fan_only", "dry", "auto"],
    "fan_modes": ["Auto", "Level 1", "Level 2", "Level 3", "Level 4", "Level 5", "Quiet"],
    "swing_modes": ["Auto", "Highest", "High", "Middle", "Low", "Lowest"],
    "swing_horizontal_modes": [
        "Auto",
        "Left Max",
        "Left",
        "Middle",
        "Right",
        "Right Max",
    ],
    "min_temp": 16,
    "max_temp": 30,
    "temp_step": 0.5,
    "temperature_unit": "C",
}

RETAINED_BASELINE_STATE = {
    "mode": "off",
    "target_temperature": 26,
    "current_temperature": 26.5,
    "fan_mode": "Auto",
    "swing_mode": "Auto",
    "swing_horizontal_mode": "Auto",
    "available": True,
}

VARIANT_TRAITS: dict[str, dict[str, Any]] = {
    "C1": {
        "hvac_modes": ["off", "cool", "dry", "auto"],
        "fan_modes": ["Auto", "Level 1", "Level 3", "Level 5"],
        "swing_modes": ["Auto", "Highest", "High", "Middle", "Low", "Lowest"],
        "swing_horizontal_modes": [],
        "min_temp": 16,
        "max_temp": 30,
        "temp_step": 1.0,
        "temperature_unit": "C",
    },
    "C2": {
        "hvac_modes": ["off", "cool", "heat", "dry", "auto"],
        "fan_modes": ["Auto", "Level 1", "Level 3", "Level 5"],
        "swing_modes": ["Auto", "Highest", "High", "Middle", "Low", "Lowest"],
        "swing_horizontal_modes": [],
        "min_temp": 16,
        "max_temp": 30,
        "temp_step": 0.5,
        "temperature_unit": "C",
    },
    "C3": RETAINED_BASELINE_TRAITS,
    "C5": {
        "hvac_modes": ["off", "cool", "dry", "auto"],
        "fan_modes": ["Auto", "Level 1", "Level 2", "Level 3", "Level 4", "Level 5"],
        "swing_modes": ["Auto", "Highest", "High", "Middle", "Low", "Lowest"],
        "swing_horizontal_modes": [
            "Auto",
            "Left Max",
            "Left",
            "Middle",
            "Right",
            "Right Max",
        ],
        "min_temp": 16,
        "max_temp": 30,
        "temp_step": 1.0,
        "temperature_unit": "C",
    },
    "C6": {
        "hvac_modes": ["off", "cool", "heat", "dry", "auto"],
        "fan_modes": ["Auto", "Level 1", "Level 3", "Level 5", "Quiet"],
        "swing_modes": ["Auto", "Highest", "High", "Middle", "Low", "Lowest"],
        "swing_horizontal_modes": [],
        "min_temp": 16,
        "max_temp": 30,
        "temp_step": 0.5,
        "temperature_unit": "C",
    },
}

HVAC_ACTION_CASES = [
    (
        "off",
        {
            "mode": "off",
            "target_temperature": 24,
            "current_temperature": 26.5,
            "fan_mode": "Level 2",
            "swing_mode": "Middle",
            "swing_horizontal_mode": "Left",
            "available": True,
        },
    ),
    (
        "cooling",
        {
            "mode": "cool",
            "target_temperature": 24,
            "current_temperature": 27,
            "fan_mode": "Level 2",
            "swing_mode": "Middle",
            "swing_horizontal_mode": "Left",
            "available": True,
        },
    ),
    (
        "heating",
        {
            "mode": "heat",
            "target_temperature": 24,
            "current_temperature": 20,
            "fan_mode": "Level 2",
            "swing_mode": "Middle",
            "swing_horizontal_mode": "Left",
            "available": True,
        },
    ),
    (
        "drying",
        {
            "mode": "dry",
            "target_temperature": 24,
            "current_temperature": 24,
            "fan_mode": "Level 2",
            "swing_mode": "Middle",
            "swing_horizontal_mode": "Left",
            "available": True,
        },
    ),
    (
        "fan",
        {
            "mode": "fan_only",
            "target_temperature": 24,
            "current_temperature": 24,
            "fan_mode": "Level 2",
            "swing_mode": "Middle",
            "swing_horizontal_mode": "Left",
            "available": True,
        },
    ),
    (
        "cooling",
        {
            "mode": "auto",
            "target_temperature": 24,
            "current_temperature": 28,
            "fan_mode": "Level 2",
            "swing_mode": "Middle",
            "swing_horizontal_mode": "Left",
            "available": True,
        },
    ),
    (
        "heating",
        {
            "mode": "auto",
            "target_temperature": 24,
            "current_temperature": 20,
            "fan_mode": "Level 2",
            "swing_mode": "Middle",
            "swing_horizontal_mode": "Left",
            "available": True,
        },
    ),
    (
        "idle",
        {
            "mode": "auto",
            "target_temperature": 24,
            "current_temperature": 24,
            "fan_mode": "Level 2",
            "swing_mode": "Middle",
            "swing_horizontal_mode": "Left",
            "available": True,
        },
    ),
]

ACTION_CASES = [
    {
        "id": "set_hvac_mode_cool",
        "service": "set_hvac_mode",
        "service_data": {"hvac_mode": "cool"},
        "expected_payload": {"mode": "cool"},
        "baseline_mode": "off",
        "expected_state": {"state": "cool"},
    },
    {
        "id": "set_temperature_24",
        "service": "set_temperature",
        "service_data": {"temperature": 24.0},
        "expected_payload": {"target_temperature": 24.0},
        "baseline_mode": "cool",
        "expected_state": {"state": "cool", "temperature": 24.0},
    },
    {
        "id": "set_temperature_24_cool",
        "service": "set_temperature",
        "service_data": {"temperature": 24.0, "hvac_mode": "cool"},
        "expected_payload": {"target_temperature": 24.0, "mode": "cool"},
        "baseline_mode": "off",
        "expected_state": {"state": "cool", "temperature": 24.0},
    },
    {
        "id": "set_fan_mode_level2",
        "service": "set_fan_mode",
        "service_data": {"fan_mode": "Level 2"},
        "expected_payload": {"fan_mode": "Level 2"},
        "baseline_mode": "cool",
        "expected_state": {"state": "cool", "fan_mode": "Level 2"},
    },
    {
        "id": "set_swing_mode_middle",
        "service": "set_swing_mode",
        "service_data": {"swing_mode": "Middle"},
        "expected_payload": {"swing_mode": "Middle"},
        "baseline_mode": "cool",
        "expected_state": {"state": "cool", "swing_mode": "Middle"},
    },
    {
        "id": "set_swing_horizontal_mode_left",
        "service": "set_swing_horizontal_mode",
        "service_data": {"swing_horizontal_mode": "Left"},
        "expected_payload": {"swing_horizontal_mode": "Left"},
        "baseline_mode": "cool",
        "expected_state": {"state": "cool", "swing_horizontal_mode": "Left"},
    },
    {
        "id": "turn_on",
        "service": "turn_on",
        "service_data": {},
        "expected_payload": {"mode": "heat"},
        "baseline_mode": "off",
        "expected_state": {"state": "heat"},
    },
    {
        "id": "turn_off",
        "service": "turn_off",
        "service_data": {},
        "expected_payload": {"mode": "off"},
        "baseline_mode": "cool",
        "expected_state": {"state": "off"},
    },
    {
        "id": "toggle",
        "service": "toggle",
        "service_data": {},
        "expected_payload": {"mode": "heat"},
        "baseline_mode": "off",
        "expected_state": {"state": "heat"},
    },
]

SUITE_CHOICES = ("esphome.g1", "ha.g1", "ha.g2", "ha.g3")
SUITE_LABELS = {
    "esphome.g1": "ESPHome Group 1 - Variant config/compile",
    "ha.g1": "HA Group 1 - Traits consistency",
    "ha.g2": "HA Group 2 - MQTT round-trip and hvac_action",
    "ha.g3": "HA Group 3 - Building-block automations",
}
