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

"""Stubbed pytest coverage for the PanaAC v2 climate entity."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.components.climate import ClimateEntityFeature, HVACAction, HVACMode

from custom_components.panaac_v2.climate import PanaACV2Climate
from test.pytest_stubbed.data import BASELINE_TRAITS, COMMAND_CASES, DEFAULT_TOPIC_PREFIX, HVAC_ACTION_CASES, REPRESENTATIVE_STATE


def _make_entity(topic_prefix: str = DEFAULT_TOPIC_PREFIX, device_name: str = "ESPHome_PanaAC_v2") -> PanaACV2Climate:
    entity = PanaACV2Climate(
        SimpleNamespace(),
        SimpleNamespace(data={"topic_prefix": topic_prefix, "device_name": device_name}),
    )
    entity.async_write_ha_state = Mock()
    return entity


def _message(payload: str) -> SimpleNamespace:
    return SimpleNamespace(payload=payload)


def test_defaults_before_traits_are_conservative() -> None:
    entity = _make_entity()

    assert entity.should_poll is False
    assert entity.hvac_modes == [HVACMode.OFF]
    assert entity.fan_modes == []
    assert entity.swing_modes == []
    assert entity.swing_horizontal_modes == []
    assert entity.supported_features == ClimateEntityFeature.TARGET_TEMPERATURE
    assert entity.available is False
    assert entity.hvac_action == HVACAction.OFF


def test_async_added_to_hass_subscribes_expected_topics() -> None:
    entity = _make_entity()
    prepared_topics: dict[str, dict[str, str]] = {}

    def fake_prepare_topics(hass: object, sub_state: object, topics: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
        nonlocal prepared_topics
        prepared_topics = topics
        return topics

    with (
        patch("custom_components.panaac_v2.climate.async_prepare_subscribe_topics", side_effect=fake_prepare_topics),
        patch("custom_components.panaac_v2.climate.async_subscribe_topics", new=AsyncMock()) as subscribe_topics,
    ):
        asyncio.run(entity.async_added_to_hass())

    assert prepared_topics["state"]["topic"] == f"{DEFAULT_TOPIC_PREFIX}/state"
    assert prepared_topics["traits"]["topic"] == f"{DEFAULT_TOPIC_PREFIX}/traits"
    assert prepared_topics["availability"]["topic"] == f"{DEFAULT_TOPIC_PREFIX}/availability"
    subscribe_topics.assert_awaited_once()


def test_handle_traits_updates_supported_modes_and_features() -> None:
    entity = _make_entity()

    entity._handle_traits(_message(json.dumps(BASELINE_TRAITS)))

    assert entity.hvac_modes == [HVACMode.OFF, HVACMode.COOL, HVACMode.HEAT, HVACMode.FAN_ONLY, HVACMode.DRY, HVACMode.AUTO]
    assert entity.fan_modes == BASELINE_TRAITS["fan_modes"]
    assert entity.swing_modes == BASELINE_TRAITS["swing_modes"]
    assert entity.swing_horizontal_modes == BASELINE_TRAITS["swing_horizontal_modes"]
    assert entity.min_temp == 16
    assert entity.max_temp == 30
    assert entity.target_temperature_step == 0.5
    assert entity.supported_features == (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.FAN_MODE
        | ClimateEntityFeature.SWING_MODE
        | ClimateEntityFeature.SWING_HORIZONTAL_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )


def test_handle_state_maps_representative_payload_to_entity_attributes() -> None:
    entity = _make_entity()

    entity._handle_state(_message(json.dumps(REPRESENTATIVE_STATE)))

    assert entity.hvac_mode == HVACMode.COOL
    assert entity.hvac_action == HVACAction.COOLING
    assert entity.target_temperature == 24
    assert entity.current_temperature == 27
    assert entity.fan_mode == "Level 2"
    assert entity.swing_mode == "Middle"
    assert entity.swing_horizontal_mode == "Left"


@pytest.mark.parametrize(("payload", "expected_mode", "expected_action", "_case_id"), HVAC_ACTION_CASES)
def test_handle_state_derives_hvac_action(payload: dict[str, object], expected_mode: HVACMode, expected_action: HVACAction, _case_id: str) -> None:
    entity = _make_entity()

    entity._handle_state(_message(json.dumps(payload)))

    assert entity.hvac_mode == expected_mode
    assert entity.hvac_action == expected_action


def test_handle_availability_tracks_online_and_offline() -> None:
    entity = _make_entity()

    entity._handle_availability(_message("online"))
    assert entity.available is True

    entity._handle_availability(_message("offline"))
    assert entity.available is False


def test_invalid_payloads_do_not_mutate_entity_state() -> None:
    entity = _make_entity()
    baseline = (entity.hvac_mode, entity.target_temperature, entity.fan_mode, entity.swing_mode, entity.swing_horizontal_mode)

    entity._handle_state(_message("{invalid"))
    entity._handle_traits(_message("{invalid"))

    assert (entity.hvac_mode, entity.target_temperature, entity.fan_mode, entity.swing_mode, entity.swing_horizontal_mode) == baseline


@pytest.mark.asyncio
@pytest.mark.parametrize(("method_name", "kwargs", "expected_payload", "_case_id"), COMMAND_CASES)
async def test_command_methods_publish_expected_payload(
    method_name: str,
    kwargs: dict[str, object],
    expected_payload: dict[str, object],
    _case_id: str,
) -> None:
    entity = _make_entity()

    with patch("homeassistant.components.mqtt.async_publish", new=AsyncMock()) as async_publish:
        await getattr(entity, method_name)(**kwargs)

    async_publish.assert_awaited_once_with(
        entity.hass,
        f"{DEFAULT_TOPIC_PREFIX}/set",
        json.dumps(expected_payload),
        qos=0,
        retain=False,
    )


@pytest.mark.asyncio
async def test_turn_off_publishes_off_command() -> None:
    entity = _make_entity()
    entity._attr_hvac_modes = [HVACMode.OFF, HVACMode.COOL, HVACMode.HEAT]

    with patch("homeassistant.components.mqtt.async_publish", new=AsyncMock()) as async_publish:
        await entity.async_turn_off()

    async_publish.assert_awaited_once_with(
        entity.hass,
        f"{DEFAULT_TOPIC_PREFIX}/set",
        json.dumps({"mode": "off"}),
        qos=0,
        retain=False,
    )


@pytest.mark.asyncio
async def test_turn_on_publishes_first_available_active_mode() -> None:
    entity = _make_entity()
    entity._attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT, HVACMode.COOL]

    with patch("homeassistant.components.mqtt.async_publish", new=AsyncMock()) as async_publish:
        await entity.async_turn_on()

    async_publish.assert_awaited_once_with(
        entity.hass,
        f"{DEFAULT_TOPIC_PREFIX}/set",
        json.dumps({"mode": "heat"}),
        qos=0,
        retain=False,
    )
