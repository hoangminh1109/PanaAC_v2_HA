"""Static data for stubbed HA integration tests."""

from __future__ import annotations

from homeassistant.components.climate import HVACAction, HVACMode

DEFAULT_TOPIC_PREFIX = "panaac_v2/esphome-panaac-v2"


def pytest_param(*values: object) -> tuple[object, ...]:
    """Keep data.py free from a direct pytest import."""
    return values

BASELINE_TRAITS = {
    "hvac_modes": ["off", "cool", "heat", "fan_only", "dry", "auto"],
    "fan_modes": ["Auto", "Level 1", "Level 2", "Level 3", "Level 4", "Level 5", "Quiet"],
    "swing_modes": ["Auto", "Highest", "High", "Middle", "Low", "Lowest"],
    "swing_horizontal_modes": ["Auto", "Left Max", "Left", "Middle", "Right", "Right Max"],
    "min_temp": 16,
    "max_temp": 30,
    "temp_step": 0.5,
}

REPRESENTATIVE_STATE = {
    "mode": "cool",
    "target_temperature": 24,
    "current_temperature": 27,
    "fan_mode": "Level 2",
    "swing_mode": "Middle",
    "swing_horizontal_mode": "Left",
    "available": True,
}

HVAC_ACTION_CASES = [
    pytest_param(
        {
            "mode": "off",
            "target_temperature": 24,
            "current_temperature": 26.5,
            "fan_mode": "Level 2",
            "swing_mode": "Middle",
            "swing_horizontal_mode": "Left",
            "available": True,
        },
        HVACMode.OFF,
        HVACAction.OFF,
        "off",
    ),
    pytest_param(
        {
            "mode": "cool",
            "target_temperature": 24,
            "current_temperature": 27,
            "fan_mode": "Level 2",
            "swing_mode": "Middle",
            "swing_horizontal_mode": "Left",
            "available": True,
        },
        HVACMode.COOL,
        HVACAction.COOLING,
        "cooling",
    ),
    pytest_param(
        {
            "mode": "heat",
            "target_temperature": 24,
            "current_temperature": 20,
            "fan_mode": "Level 2",
            "swing_mode": "Middle",
            "swing_horizontal_mode": "Left",
            "available": True,
        },
        HVACMode.HEAT,
        HVACAction.HEATING,
        "heating",
    ),
    pytest_param(
        {
            "mode": "dry",
            "target_temperature": 24,
            "current_temperature": 24,
            "fan_mode": "Level 2",
            "swing_mode": "Middle",
            "swing_horizontal_mode": "Left",
            "available": True,
        },
        HVACMode.DRY,
        HVACAction.DRYING,
        "drying",
    ),
    pytest_param(
        {
            "mode": "fan_only",
            "target_temperature": 24,
            "current_temperature": 24,
            "fan_mode": "Level 2",
            "swing_mode": "Middle",
            "swing_horizontal_mode": "Left",
            "available": True,
        },
        HVACMode.FAN_ONLY,
        HVACAction.FAN,
        "fan",
    ),
    pytest_param(
        {
            "mode": "auto",
            "target_temperature": 24,
            "current_temperature": 28,
            "fan_mode": "Level 2",
            "swing_mode": "Middle",
            "swing_horizontal_mode": "Left",
            "available": True,
        },
        HVACMode.AUTO,
        HVACAction.COOLING,
        "auto_cooling",
    ),
    pytest_param(
        {
            "mode": "auto",
            "target_temperature": 24,
            "current_temperature": 20,
            "fan_mode": "Level 2",
            "swing_mode": "Middle",
            "swing_horizontal_mode": "Left",
            "available": True,
        },
        HVACMode.AUTO,
        HVACAction.HEATING,
        "auto_heating",
    ),
    pytest_param(
        {
            "mode": "auto",
            "target_temperature": 24,
            "current_temperature": 24,
            "fan_mode": "Level 2",
            "swing_mode": "Middle",
            "swing_horizontal_mode": "Left",
            "available": True,
        },
        HVACMode.AUTO,
        HVACAction.IDLE,
        "auto_idle",
    ),
]

COMMAND_CASES = [
    pytest_param("async_set_hvac_mode", {"hvac_mode": HVACMode.COOL}, {"mode": "cool"}, "set_hvac_mode_cool"),
    pytest_param("async_set_temperature", {"temperature": 24.0}, {"target_temperature": 24.0}, "set_temperature_24"),
    pytest_param(
        "async_set_temperature",
        {"temperature": 24.0, "hvac_mode": HVACMode.COOL},
        {"target_temperature": 24.0, "mode": "cool"},
        "set_temperature_24_cool",
    ),
    pytest_param("async_set_fan_mode", {"fan_mode": "Level 2"}, {"fan_mode": "Level 2"}, "set_fan_mode_level2"),
    pytest_param("async_set_swing_mode", {"swing_mode": "Middle"}, {"swing_mode": "Middle"}, "set_swing_mode_middle"),
    pytest_param(
        "async_set_swing_horizontal_mode",
        {"swing_horizontal_mode": "Left"},
        {"swing_horizontal_mode": "Left"},
        "set_swing_horizontal_mode_left",
    ),
]
