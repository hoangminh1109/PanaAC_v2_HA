#!/usr/bin/env python3
"""Automated runner for the PanaAC v2 HA/ESPHome full-test plans."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import sqlite3
import subprocess
import sys
import textwrap
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from urllib.error import HTTPError, URLError
from typing import Any
from urllib.request import urlopen
from urllib.request import Request
from urllib.parse import urlencode


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
    ("set_hvac_mode_cool", "set_hvac_mode", {"hvac_mode": "cool"}, {"mode": "cool"}, "off"),
    (
        "set_temperature_24",
        "set_temperature",
        {"temperature": 24.0},
        {"target_temperature": 24.0},
        "cool",
    ),
    (
        "set_temperature_24_cool",
        "set_temperature",
        {"temperature": 24.0, "hvac_mode": "cool"},
        {"target_temperature": 24.0, "mode": "cool"},
        "off",
    ),
    (
        "set_fan_mode_level2",
        "set_fan_mode",
        {"fan_mode": "Level 2"},
        {"fan_mode": "Level 2"},
        "cool",
    ),
    (
        "set_swing_mode_middle",
        "set_swing_mode",
        {"swing_mode": "Middle"},
        {"swing_mode": "Middle"},
        "cool",
    ),
    (
        "set_swing_horizontal_mode_left",
        "set_swing_horizontal_mode",
        {"swing_horizontal_mode": "Left"},
        {"swing_horizontal_mode": "Left"},
        "cool",
    ),
    ("turn_on", "turn_on", {}, {"mode": "heat"}, "off"),
    ("turn_off", "turn_off", {}, {"mode": "off"}, "cool"),
    ("toggle", "toggle", {}, {"mode": "heat"}, "off"),
]

AUTOMATION_TRIGGER_CASES = [
    (
        "cool",
        "cool",
        [
            "panaac_v2/test/is_cooling fired",
            "panaac_v2/test/is_hvac_mode_cool fired",
            "panaac_v2/test/started_cooling fired",
        ],
    ),
    (
        "heat",
        "heat",
        [
            "panaac_v2/test/is_heating fired",
            "panaac_v2/test/started_heating fired",
        ],
    ),
    (
        "dry",
        "dry",
        [
            "panaac_v2/test/is_drying fired",
            "panaac_v2/test/started_drying fired",
        ],
    ),
]


class TestFailure(RuntimeError):
    """Raised when a required automated check fails."""


@dataclass
class CaseResult:
    id: str
    title: str
    status: str
    expected: Any = None
    actual: Any = None
    evidence: dict[str, Any] = field(default_factory=dict)
    duration_s: float = 0.0


@dataclass
class GroupResult:
    id: str
    title: str
    cases: list[CaseResult] = field(default_factory=list)

    def add(self, case: CaseResult) -> None:
        self.cases.append(case)


class Runner:
    """Main orchestrator."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.repo_root = Path(__file__).resolve().parents[1]
        self.ha_core_path = Path(args.ha_core_path).resolve()
        self.esphome_repo_path = Path(args.esphome_repo_path).resolve()
        self.esphome_workspace_path = self._resolve_esphome_workspace_path()
        self.output_dir = Path(args.output_dir).resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.mqtt_host = args.mqtt_host
        self.mqtt_port = args.mqtt_port
        self.mqtt_user = args.mqtt_user
        self.mqtt_pass = args.mqtt_pass
        self.topic_prefix = args.topic_prefix
        self.mode = args.mode
        self.timestamp = datetime.now().astimezone()
        self.report_json_path = self.output_dir / "report.json"
        self.report_md_path = self.output_dir / "report.md"
        self.ha_log_path = self.output_dir / "ha.log"
        self.raw_capture_dir = self.output_dir / "captures"
        self.raw_capture_dir.mkdir(parents=True, exist_ok=True)
        self.automations_path = self.ha_core_path / "config" / "automations.yaml"
        self.original_automations = self.automations_path.read_text() if self.automations_path.exists() else "[]\n"
        self.entity_id = args.entity_id
        self.ha_api_token: str | None = None
        self.groups: list[GroupResult] = []
        self._cleanup_callbacks: list[tuple[str, callable]] = []

    def run(self) -> int:
        try:
            self._validate_environment()
            self.entity_id = self.entity_id or self._detect_entity_id()

            if self.mode != "ha-only":
                self.groups.append(self._run_esphome_group())
            else:
                group = GroupResult("esphome.g1", "ESPHome Group 1 - Variant config/compile")
                group.add(
                    CaseResult(
                        id="esphome.group1",
                        title="ESPHome variant config/compile",
                        status="skip",
                        expected="Compile/config checks disabled only in ha-only mode",
                        actual="Skipped by --mode ha-only",
                    )
                )
                self.groups.append(group)

            self.groups.extend(self._run_ha_groups())
            self._write_reports()
            return 0 if self._overall_status() else 1
        except Exception as err:  # noqa: BLE001
            failure_group = GroupResult("runner", "Runner")
            failure_group.add(
                CaseResult(
                    id="runner.failure",
                    title="Runner failure",
                    status="fail",
                    expected="Runner completes all required automated checks",
                    actual=str(err),
                    evidence={"exception_type": type(err).__name__},
                )
            )
            self.groups.append(failure_group)
            self._write_reports()
            return 1
        finally:
            self._cleanup()

    def _validate_environment(self) -> None:
        for cmd in ("mosquitto_pub", "mosquitto_sub", "pgrep"):
            if shutil.which(cmd) is None:
                raise TestFailure(f"Required command not found: {cmd}")
        if not (self.ha_core_path / ".venv" / "bin" / "hass").exists():
            raise TestFailure(f"Missing Home Assistant hass binary under {self.ha_core_path}")
        if self.mode != "ha-only":
            if not (self.esphome_workspace_path / ".venv" / "bin" / "esphome").exists():
                raise TestFailure(f"Missing ESPHome CLI under {self.esphome_workspace_path}")
        if not self.automations_path.exists():
            raise TestFailure(f"Missing automations file at {self.automations_path}")

    def _detect_entity_id(self) -> str:
        unique_id = f"{self.topic_prefix}_climate"
        registry_path = self.ha_core_path / "config" / ".storage" / "core.entity_registry"
        data = json.loads(registry_path.read_text())
        for entity in data["data"]["entities"]:
            if entity.get("unique_id") == unique_id:
                return entity["entity_id"]
        raise TestFailure(f"Could not auto-detect entity_id for unique_id {unique_id}")

    def _resolve_esphome_workspace_path(self) -> Path:
        candidates = [
            self.esphome_repo_path,
            self.esphome_repo_path.parent,
            self.esphome_repo_path.parent.parent / "esphome",
        ]
        for candidate in candidates:
            if (candidate / ".venv" / "bin" / "esphome").exists():
                return candidate.resolve()
        return self.esphome_repo_path

    def _run_esphome_group(self) -> GroupResult:
        group = GroupResult("esphome.g1", "ESPHome Group 1 - Variant config/compile")
        esphome_bin = self.esphome_workspace_path / ".venv" / "bin" / "esphome"
        env = os.environ.copy()
        env["PLATFORMIO_CORE_DIR"] = env.get("PLATFORMIO_CORE_DIR", "/tmp/platformio")
        env["XDG_CACHE_HOME"] = env.get("XDG_CACHE_HOME", "/tmp/.cache")
        variants = ["C1", "C2", "C3", "C4", "C5", "C6", "C3-automation"]
        for variant in variants:
            started = time.monotonic()
            yaml_path = self.esphome_repo_path / "test" / "variants" / f"{variant}.yaml"
            if not yaml_path.exists():
                group.add(
                    CaseResult(
                        id=f"esphome.{variant}",
                        title=f"{variant} config/compile",
                        status="fail",
                        expected=str(yaml_path),
                        actual="Variant YAML missing",
                    )
                )
                continue
            yaml_arg = str(yaml_path.relative_to(self.esphome_workspace_path))
            config_cmd = [str(esphome_bin), "config", yaml_arg]
            compile_cmd = [str(esphome_bin), "compile", yaml_arg]
            config_result = self._run_command(config_cmd, cwd=self.esphome_workspace_path, env=env, check=False)
            compile_result = self._run_command(compile_cmd, cwd=self.esphome_workspace_path, env=env, check=False)
            status = "pass" if config_result.returncode == 0 and compile_result.returncode == 0 else "fail"
            capture_path = self.raw_capture_dir / f"esphome-{variant}.log"
            capture_path.write_text(
                "\n".join(
                    [
                        f"$ {' '.join(config_cmd)}",
                        config_result.stdout,
                        config_result.stderr,
                        "",
                        f"$ {' '.join(compile_cmd)}",
                        compile_result.stdout,
                        compile_result.stderr,
                    ]
                )
            )
            group.add(
                CaseResult(
                    id=f"esphome.{variant}",
                    title=f"{variant} config/compile",
                    status=status,
                    expected="esphome config + compile exit 0",
                    actual={
                        "config_rc": config_result.returncode,
                        "compile_rc": compile_result.returncode,
                    },
                    evidence={"log_path": str(capture_path)},
                    duration_s=time.monotonic() - started,
                )
            )
        return group

    def _run_ha_groups(self) -> list[GroupResult]:
        groups: list[GroupResult] = []
        self._ensure_ha_running()
        self._ensure_entity_ready()

        groups.append(self._run_ha_group_1())
        groups.append(self._run_ha_group_2())
        groups.append(self._run_ha_group_3())
        return groups

    def _run_ha_group_1(self) -> GroupResult:
        group = GroupResult("ha.g1", "HA Group 1 - Traits consistency")

        started = time.monotonic()
        self._delete_retained("traits")
        self._restart_ha()
        cold_registry = self._read_registry_entity()
        cold_state = self._read_latest_state()
        expected_registry = {"hvac_modes": ["off"], "supported_features": TARGET_TEMPERATURE}
        actual_registry = {
            "hvac_modes": cold_registry["capabilities"].get("hvac_modes"),
            "supported_features": cold_registry["supported_features"],
            "fan_modes": cold_registry["capabilities"].get("fan_modes"),
            "swing_modes": cold_registry["capabilities"].get("swing_modes"),
            "swing_horizontal_modes": cold_registry["capabilities"].get("swing_horizontal_modes"),
        }
        status = (
            "pass"
            if actual_registry["hvac_modes"] == ["off"]
            and actual_registry["supported_features"] == TARGET_TEMPERATURE
            and actual_registry["fan_modes"] is None
            and actual_registry["swing_modes"] is None
            and actual_registry["swing_horizontal_modes"] is None
            else "fail"
        )
        group.add(
            CaseResult(
                id="ha.g1.1",
                title="Cold start without retained traits",
                status=status,
                expected=expected_registry,
                actual={"registry": actual_registry, "state": cold_state},
                duration_s=time.monotonic() - started,
            )
        )

        for variant, payload in VARIANT_TRAITS.items():
            started = time.monotonic()
            self._publish_retained("traits", payload)
            time.sleep(0.8)
            registry = self._read_registry_entity()
            state = self._read_latest_state()
            expected = {
                "hvac_modes": payload["hvac_modes"],
                "fan_modes": payload["fan_modes"],
                "swing_modes": payload["swing_modes"],
                "swing_horizontal_modes": payload["swing_horizontal_modes"] or None,
                "min_temp": payload["min_temp"],
                "max_temp": payload["max_temp"],
                "target_temp_step": payload["temp_step"],
                "supported_features": self._expected_supported_features(payload),
            }
            actual = {
                "hvac_modes": registry["capabilities"].get("hvac_modes"),
                "fan_modes": registry["capabilities"].get("fan_modes"),
                "swing_modes": registry["capabilities"].get("swing_modes"),
                "swing_horizontal_modes": registry["capabilities"].get("swing_horizontal_modes"),
                "min_temp": registry["capabilities"].get("min_temp"),
                "max_temp": registry["capabilities"].get("max_temp"),
                "target_temp_step": registry["capabilities"].get("target_temp_step"),
                "supported_features": registry["supported_features"],
            }
            mismatches = self._compare_expected(expected, actual)
            status = "pass" if not mismatches else "fail"
            evidence = {"latest_state": state}
            if mismatches:
                evidence["mismatches"] = mismatches
            group.add(
                CaseResult(
                    id=f"ha.g1.2.{variant.lower()}",
                    title=f"Variant {variant} traits adoption",
                    status=status,
                    expected=expected,
                    actual=actual,
                    evidence=evidence,
                    duration_s=time.monotonic() - started,
                )
            )

        group.add(
            CaseResult(
                id="ha.g1.2.c4",
                title="Variant C4 traits adoption",
                status="skip",
                expected="C4 would use the custom MQTT traits contract",
                actual="C4 is v1/native mode and does not publish the custom traits contract",
            )
        )
        self._restore_baseline_topics()
        return group

    def _run_ha_group_2(self) -> GroupResult:
        group = GroupResult("ha.g2", "HA Group 2 - MQTT round-trip and hvac_action")
        self._restore_baseline_topics()
        time.sleep(0.8)

        started = time.monotonic()
        representative_state = {
            "mode": "cool",
            "target_temperature": 24,
            "current_temperature": 27,
            "fan_mode": "Level 2",
            "swing_mode": "Middle",
            "swing_horizontal_mode": "Left",
            "available": True,
        }
        self._publish_retained("state", representative_state)
        snapshot = self._poll_state(lambda s: s.get("state") == "cool" and s.get("hvac_action") == "cooling")
        state_ok = all(snapshot.get(key) == representative_state.get(self._state_key_to_payload_key(key), representative_state.get(key)) for key in ("temperature", "current_temperature", "fan_mode", "swing_mode", "swing_horizontal_mode"))
        group.add(
            CaseResult(
                id="ha.g2.1.state_ingestion",
                title="Representative state ingestion",
                status="pass" if snapshot.get("state") == "cool" and snapshot.get("hvac_action") == "cooling" and state_ok else "fail",
                expected={
                    "state": "cool",
                    "hvac_action": "cooling",
                    "temperature": 24,
                    "current_temperature": 27,
                    "fan_mode": "Level 2",
                    "swing_mode": "Middle",
                    "swing_horizontal_mode": "Left",
                },
                actual=snapshot,
                duration_s=time.monotonic() - started,
            )
        )

        for availability in ("offline", "online"):
            started = time.monotonic()
            self._publish_retained("availability", availability)
            snapshot = self._poll_state(
                (lambda s: s.get("state") == "unavailable")
                if availability == "offline"
                else (lambda s: s.get("state") != "unavailable")
            )
            expected_state = "unavailable" if availability == "offline" else "cool"
            status = "pass" if snapshot.get("state") == expected_state else "fail"
            group.add(
                CaseResult(
                    id=f"ha.g2.1.availability_{availability}",
                    title=f"Availability {availability}",
                    status=status,
                    expected={"state": expected_state},
                    actual=snapshot,
                    duration_s=time.monotonic() - started,
                )
            )
        self._publish_retained("availability", "online")
        time.sleep(0.5)

        for case_id, service, service_data, expected_payload, baseline_mode in ACTION_CASES:
            started = time.monotonic()
            self._publish_retained("state", self._baseline_state_for_mode(baseline_mode))
            time.sleep(0.6)
            actual_payload = self._capture_set_payload(service, service_data)
            status = "pass" if actual_payload == expected_payload else "fail"
            group.add(
                CaseResult(
                    id=f"ha.g2.2.{case_id}",
                    title=case_id,
                    status=status,
                    expected=expected_payload,
                    actual=actual_payload,
                    duration_s=time.monotonic() - started,
                )
            )

        for expected_action, payload in HVAC_ACTION_CASES:
            started = time.monotonic()
            self._publish_retained("state", payload)
            snapshot = self._poll_state(
                lambda s, expected=payload["mode"], action=expected_action: s.get("state") == expected and s.get("hvac_action") == action
            )
            status = "pass" if snapshot.get("hvac_action") == expected_action else "fail"
            group.add(
                CaseResult(
                    id=f"ha.g2.3.{payload['mode']}.{expected_action}",
                    title=f"hvac_action for {payload['mode']}",
                    status=status,
                    expected={"hvac_action": expected_action},
                    actual=snapshot,
                    duration_s=time.monotonic() - started,
                )
            )

        started = time.monotonic()
        self._delete_retained("traits")
        self._restart_ha()
        cold_registry = self._read_registry_entity()
        cold_status = "pass" if cold_registry["capabilities"].get("hvac_modes") == ["off"] else "fail"
        self._restore_baseline_topics()
        time.sleep(0.8)
        restored_registry = self._read_registry_entity()
        restored_status = (
            "pass"
            if restored_registry["capabilities"].get("hvac_modes") == RETAINED_BASELINE_TRAITS["hvac_modes"]
            else "fail"
        )
        status = "pass" if cold_status == "pass" and restored_status == "pass" else "fail"
        group.add(
            CaseResult(
                id="ha.g2.4.retained_resilience",
                title="Traits deletion + HA restart resilience",
                status=status,
                expected={
                    "cold_hvac_modes": ["off"],
                    "restored_hvac_modes": RETAINED_BASELINE_TRAITS["hvac_modes"],
                },
                actual={
                    "cold_hvac_modes": cold_registry["capabilities"].get("hvac_modes"),
                    "restored_hvac_modes": restored_registry["capabilities"].get("hvac_modes"),
                },
                duration_s=time.monotonic() - started,
            )
        )

        if self.mode == "full-hil":
            group.add(
                CaseResult(
                    id="ha.g2.4.broker_cycle",
                    title="Broker stop/start resilience",
                    status="blocked",
                    expected="Runner controls broker lifecycle and verifies recovery",
                    actual="Broker lifecycle automation is not implemented in this runner version",
                )
            )
        else:
            group.add(
                CaseResult(
                    id="ha.g2.4.broker_cycle",
                    title="Broker stop/start resilience",
                    status="skip",
                    expected="Runner stops and restarts the broker",
                    actual="Skipped outside full-hil mode",
                )
            )
        return group

    def _run_ha_group_3(self) -> GroupResult:
        group = GroupResult("ha.g3", "HA Group 3 - Building-block automations")
        started = time.monotonic()
        trigger_log = self.raw_capture_dir / "ha-climate-trigger-tests.log"
        trigger_cmd = [
            "uv",
            "run",
            "--with",
            "pytest",
            "pytest",
            "tests/components/climate/test_trigger.py",
            "-k",
            "started_cooling or started_heating or started_drying",
            "-q",
        ]
        trigger_result = self._run_command(trigger_cmd, cwd=self.ha_core_path, check=False)
        trigger_log.write_text(
            "\n".join(
                [
                    f"$ {' '.join(trigger_cmd)}",
                    trigger_result.stdout,
                    trigger_result.stderr,
                ]
            )
        )
        group.add(
            CaseResult(
                id="ha.g3.4.triggers",
                title="Climate started_* trigger coverage",
                status="pass" if trigger_result.returncode == 0 else "fail",
                expected="climate.started_cooling/heating/drying trigger tests pass",
                actual={"returncode": trigger_result.returncode},
                evidence={"log_path": str(trigger_log)},
                duration_s=time.monotonic() - started,
            )
        )

        started = time.monotonic()
        condition_log = self.raw_capture_dir / "ha-climate-condition-tests.log"
        condition_cmd = [
            "uv",
            "run",
            "--with",
            "pytest",
            "pytest",
            "tests/components/climate/test_condition.py",
            "-k",
            "is_cooling or is_heating or is_drying or is_hvac_mode",
            "-q",
        ]
        condition_result = self._run_command(condition_cmd, cwd=self.ha_core_path, check=False)
        condition_log.write_text(
            "\n".join(
                [
                    f"$ {' '.join(condition_cmd)}",
                    condition_result.stdout,
                    condition_result.stderr,
                ]
            )
        )
        group.add(
            CaseResult(
                id="ha.g3.4.conditions",
                title="Climate condition coverage",
                status="pass" if condition_result.returncode == 0 else "fail",
                expected="climate.is_cooling/heating/drying/is_hvac_mode tests pass",
                actual={"returncode": condition_result.returncode},
                evidence={"log_path": str(condition_log)},
                duration_s=time.monotonic() - started,
            )
        )
        blocked_status = "blocked" if self.mode == "full-hil" else "skip"
        blocked_actual = (
            "full-hil mode requested but DUT flashing/log capture is not implemented"
            if self.mode == "full-hil"
            else "Skipped in auto mode without DUT flash/log hooks"
        )
        for suffix, title in (
            ("3.1", "ESPHome climate.control observed via HA"),
            ("3.2", "ESPHome lambda make_call observed via HA"),
            ("3.3", "ESPHome on_state / on_control observed via HA"),
        ):
            group.add(
                CaseResult(
                    id=f"ha.g{suffix}",
                    title=title,
                    status=blocked_status,
                    expected="DUT exposes the automation test build and runtime logs",
                    actual=blocked_actual,
                )
            )
        return group

    def _install_temp_automations(self) -> None:
        content = textwrap.dedent(
            f"""\
            - id: panaac_test_started_cooling
              alias: panaac test started_cooling
              mode: single
              triggers:
                - trigger: climate.started_cooling
                  target:
                    entity_id: {self.entity_id}
              actions:
                - action: mqtt.publish
                  data:
                    topic: panaac_v2/test/started_cooling
                    payload: fired

            - id: panaac_test_started_heating
              alias: panaac test started_heating
              mode: single
              triggers:
                - trigger: climate.started_heating
                  target:
                    entity_id: {self.entity_id}
              actions:
                - action: mqtt.publish
                  data:
                    topic: panaac_v2/test/started_heating
                    payload: fired

            - id: panaac_test_started_drying
              alias: panaac test started_drying
              mode: single
              triggers:
                - trigger: climate.started_drying
                  target:
                    entity_id: {self.entity_id}
              actions:
                - action: mqtt.publish
                  data:
                    topic: panaac_v2/test/started_drying
                    payload: fired

            - id: panaac_test_is_cooling
              alias: panaac test is_cooling
              mode: single
              triggers:
                - trigger: climate.started_cooling
                  target:
                    entity_id: {self.entity_id}
              conditions:
                - condition: climate.is_cooling
                  target:
                    entity_id: {self.entity_id}
              actions:
                - action: mqtt.publish
                  data:
                    topic: panaac_v2/test/is_cooling
                    payload: fired

            - id: panaac_test_is_heating
              alias: panaac test is_heating
              mode: single
              triggers:
                - trigger: climate.started_heating
                  target:
                    entity_id: {self.entity_id}
              conditions:
                - condition: climate.is_heating
                  target:
                    entity_id: {self.entity_id}
              actions:
                - action: mqtt.publish
                  data:
                    topic: panaac_v2/test/is_heating
                    payload: fired

            - id: panaac_test_is_drying
              alias: panaac test is_drying
              mode: single
              triggers:
                - trigger: climate.started_drying
                  target:
                    entity_id: {self.entity_id}
              conditions:
                - condition: climate.is_drying
                  target:
                    entity_id: {self.entity_id}
              actions:
                - action: mqtt.publish
                  data:
                    topic: panaac_v2/test/is_drying
                    payload: fired

            - id: panaac_test_is_hvac_mode_cool
              alias: panaac test is_hvac_mode_cool
              mode: single
              triggers:
                - trigger: state
                  entity_id: {self.entity_id}
                  to: cool
              conditions:
                - condition: climate.is_hvac_mode
                  target:
                    entity_id: {self.entity_id}
                  options:
                    hvac_mode: cool
              actions:
                - action: mqtt.publish
                  data:
                    topic: panaac_v2/test/is_hvac_mode_cool
                    payload: fired

            - id: panaac_test_action_set_hvac_mode_cool
              alias: panaac test action set_hvac_mode cool
              mode: single
              triggers:
                - trigger: mqtt
                  topic: panaac_v2/test/cmd/set_hvac_mode_cool
              actions:
                - action: climate.set_hvac_mode
                  target:
                    entity_id: {self.entity_id}
                  data:
                    hvac_mode: cool

            - id: panaac_test_action_set_temperature_24
              alias: panaac test action set_temperature 24
              mode: single
              triggers:
                - trigger: mqtt
                  topic: panaac_v2/test/cmd/set_temperature_24
              actions:
                - action: climate.set_temperature
                  target:
                    entity_id: {self.entity_id}
                  data:
                    temperature: 24

            - id: panaac_test_action_set_temperature_24_cool
              alias: panaac test action set_temperature 24 cool
              mode: single
              triggers:
                - trigger: mqtt
                  topic: panaac_v2/test/cmd/set_temperature_24_cool
              actions:
                - action: climate.set_temperature
                  target:
                    entity_id: {self.entity_id}
                  data:
                    temperature: 24
                    hvac_mode: cool

            - id: panaac_test_action_set_fan_mode_level2
              alias: panaac test action set_fan_mode level2
              mode: single
              triggers:
                - trigger: mqtt
                  topic: panaac_v2/test/cmd/set_fan_mode_level2
              actions:
                - action: climate.set_fan_mode
                  target:
                    entity_id: {self.entity_id}
                  data:
                    fan_mode: Level 2

            - id: panaac_test_action_set_swing_mode_middle
              alias: panaac test action set_swing_mode middle
              mode: single
              triggers:
                - trigger: mqtt
                  topic: panaac_v2/test/cmd/set_swing_mode_middle
              actions:
                - action: climate.set_swing_mode
                  target:
                    entity_id: {self.entity_id}
                  data:
                    swing_mode: Middle

            - id: panaac_test_action_set_swing_horizontal_mode_left
              alias: panaac test action set_swing_horizontal_mode left
              mode: single
              triggers:
                - trigger: mqtt
                  topic: panaac_v2/test/cmd/set_swing_horizontal_mode_left
              actions:
                - action: climate.set_swing_horizontal_mode
                  target:
                    entity_id: {self.entity_id}
                  data:
                    swing_horizontal_mode: Left

            - id: panaac_test_action_turn_on
              alias: panaac test action turn_on
              mode: single
              triggers:
                - trigger: mqtt
                  topic: panaac_v2/test/cmd/turn_on
              actions:
                - action: climate.turn_on
                  target:
                    entity_id: {self.entity_id}

            - id: panaac_test_action_turn_off
              alias: panaac test action turn_off
              mode: single
              triggers:
                - trigger: mqtt
                  topic: panaac_v2/test/cmd/turn_off
              actions:
                - action: climate.turn_off
                  target:
                    entity_id: {self.entity_id}

            - id: panaac_test_action_toggle
              alias: panaac test action toggle
              mode: single
              triggers:
                - trigger: mqtt
                  topic: panaac_v2/test/cmd/toggle
              actions:
                - action: climate.toggle
                  target:
                    entity_id: {self.entity_id}
            """
        )
        self.automations_path.write_text(content)
        self._cleanup_callbacks.append(("restore_automations", self._restore_automations))
        self._restart_ha()
        self._ensure_entity_ready()

    def _restore_automations(self) -> None:
        if self.automations_path.read_text() != self.original_automations:
            self.automations_path.write_text(self.original_automations)
            self._restart_ha()

    def _capture_set_payload(self, service: str, service_data: dict[str, Any]) -> Any:
        last_error = ""
        for _ in range(2):
            self._ensure_entity_ready()
            sub_cmd = self._mosquitto_sub_command(
                topic=f"{self.topic_prefix}/set",
                count=1,
                timeout=5,
            )
            proc = subprocess.Popen(sub_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            time.sleep(0.8)
            self._call_ha_service("climate", service, service_data)
            stdout, stderr = proc.communicate(timeout=7)
            if proc.returncode == 0 and stdout.strip():
                return json.loads(stdout.strip())
            last_error = stderr.strip() or stdout.strip() or f"returncode={proc.returncode}"
            time.sleep(1.0)
        raise TestFailure(f"Failed to capture set payload for {service}: {last_error}")

    def _capture_trigger_outputs(self, target_mode: str, expected_count: int) -> list[str]:
        sub_cmd = self._mosquitto_sub_command(topic="panaac_v2/test/+", count=expected_count, timeout=6, verbose=True)
        proc = subprocess.Popen(sub_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        time.sleep(0.2)
        self._publish_retained("state", self._baseline_state_for_mode("off"))
        time.sleep(1.0)
        payload = {
            "mode": target_mode,
            "target_temperature": 24,
            "current_temperature": 27 if target_mode != "heat" else 20,
            "fan_mode": "Level 2",
            "swing_mode": "Middle",
            "swing_horizontal_mode": "Left",
            "available": True,
        }
        self._publish_retained("state", payload)
        stdout, stderr = proc.communicate(timeout=8)
        if proc.returncode != 0:
            raise TestFailure(f"Failed to capture automation outputs for {target_mode}: {stderr.strip()}")
        return [line.strip() for line in stdout.splitlines() if line.strip()]

    def _poll_state(self, predicate: callable, timeout: float = 8.0, interval: float = 0.4) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        latest = {}
        while time.monotonic() < deadline:
            latest = self._read_latest_state()
            if predicate(latest):
                return latest
            time.sleep(interval)
        return latest

    def _read_latest_state(self) -> dict[str, Any]:
        db_path = self.ha_core_path / "config" / "home-assistant_v2.db"
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        row = con.execute(
            """
            SELECT s.state, sa.shared_attrs, s.last_updated_ts
            FROM states s
            JOIN states_meta m ON s.metadata_id = m.metadata_id
            LEFT JOIN state_attributes sa ON s.attributes_id = sa.attributes_id
            WHERE m.entity_id = ?
            ORDER BY s.last_updated_ts DESC
            LIMIT 1
            """,
            (self.entity_id,),
        ).fetchone()
        con.close()
        if row is None:
            raise TestFailure(f"No recorder state found for {self.entity_id}")
        attrs = json.loads(row["shared_attrs"]) if row["shared_attrs"] else {}
        result = {"last_updated_ts": row["last_updated_ts"], "state": row["state"]}
        for key in CURRENT_STATE_KEYS[1:]:
            result[key] = attrs.get(key)
        return result

    def _read_registry_entity(self) -> dict[str, Any]:
        registry_path = self.ha_core_path / "config" / ".storage" / "core.entity_registry"
        data = json.loads(registry_path.read_text())
        for entity in data["data"]["entities"]:
            if entity["entity_id"] == self.entity_id:
                return entity
        raise TestFailure(f"Entity {self.entity_id} not found in entity registry")

    def _restart_ha(self) -> None:
        self._stop_ha()
        self._start_ha()
        self._wait_for_ha()
        time.sleep(1.0)

    def _ensure_ha_running(self) -> None:
        try:
            if self._http_ready():
                return
        except Exception:  # noqa: BLE001
            pass
        self._start_ha()
        self._wait_for_ha()

    def _ensure_entity_ready(self) -> None:
        self._restore_baseline_topics()
        snapshot = self._poll_state(lambda s: s.get("state") != "unavailable", timeout=12.0, interval=0.5)
        if snapshot.get("state") == "unavailable":
            raise TestFailure(f"Entity {self.entity_id} did not become available after baseline topic restore")

    def _start_ha(self) -> None:
        with self.ha_log_path.open("a") as log_file:
            subprocess.Popen(
                ["./.venv/bin/hass", "-c", "config"],
                cwd=self.ha_core_path,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )

    def _stop_ha(self) -> None:
        pid_file = self.ha_core_path / "config" / "home-assistant.pid"
        pids: list[int] = []
        if pid_file.exists():
            content = pid_file.read_text().strip()
            if content.isdigit():
                pids.append(int(content))
        if not pids:
            result = self._run_command(["pgrep", "-f", "hass -c config"], check=False)
            for line in result.stdout.splitlines():
                line = line.strip()
                if line.isdigit():
                    pids.append(int(line))
        for pid in sorted(set(pids)):
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                continue
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            alive = [pid for pid in pids if self._pid_alive(pid)]
            if not alive:
                return
            time.sleep(0.5)
        for pid in pids:
            if self._pid_alive(pid):
                os.kill(pid, signal.SIGKILL)

    def _wait_for_ha(self, timeout: float = 60.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._http_ready():
                return
            time.sleep(1.0)
        raise TestFailure("Home Assistant did not become ready before timeout")

    def _http_ready(self) -> bool:
        try:
            with urlopen("http://127.0.0.1:8123", timeout=3) as response:
                return response.status in (200, 401, 405)
        except URLError:
            return False

    def _pid_alive(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    def _publish_text(self, topic: str, payload: str, retain: bool = False, null_retained: bool = False) -> None:
        cmd = self._mosquitto_pub_command(topic=topic, payload=payload, retain=retain, null_retained=null_retained)
        result = self._run_command(cmd, check=False)
        if result.returncode != 0:
            raise TestFailure(f"MQTT publish failed for {topic}: {result.stderr.strip()}")

    def _publish_retained(self, suffix: str, payload: Any) -> None:
        topic = f"{self.topic_prefix}/{suffix}"
        self._publish_text(topic, json.dumps(payload, separators=(",", ":")) if not isinstance(payload, str) else payload, retain=True)

    def _delete_retained(self, suffix: str) -> None:
        topic = f"{self.topic_prefix}/{suffix}"
        self._publish_text(topic, "", retain=True, null_retained=True)

    def _restore_baseline_topics(self) -> None:
        self._publish_retained("traits", RETAINED_BASELINE_TRAITS)
        self._publish_retained("availability", "online")
        self._publish_retained("state", RETAINED_BASELINE_STATE)

    def _cleanup_test_topics(self) -> None:
        for topic in (
            "panaac_v2/test/started_cooling",
            "panaac_v2/test/started_heating",
            "panaac_v2/test/started_drying",
            "panaac_v2/test/is_cooling",
            "panaac_v2/test/is_heating",
            "panaac_v2/test/is_drying",
            "panaac_v2/test/is_hvac_mode_cool",
        ):
            try:
                self._publish_text(topic, "", retain=True, null_retained=True)
            except Exception:  # noqa: BLE001
                pass

    def _cleanup(self) -> None:
        self._cleanup_test_topics()
        while self._cleanup_callbacks:
            _, callback = self._cleanup_callbacks.pop()
            try:
                callback()
            except Exception:  # noqa: BLE001
                continue

    def _overall_status(self) -> bool:
        failing_statuses = {"fail", "blocked"}
        if self.mode != "full-hil":
            failing_statuses = {"fail"}
        for group in self.groups:
            for case in group.cases:
                if case.status in failing_statuses:
                    return False
        return True

    def _compare_expected(self, expected: dict[str, Any], actual: dict[str, Any]) -> dict[str, dict[str, Any]]:
        mismatches: dict[str, dict[str, Any]] = {}
        for key, value in expected.items():
            if actual.get(key) != value:
                mismatches[key] = {"expected": value, "actual": actual.get(key)}
        return mismatches

    def _expected_supported_features(self, payload: dict[str, Any]) -> int:
        features = TARGET_TEMPERATURE
        if payload["fan_modes"]:
            features |= FAN_MODE
        if payload["swing_modes"]:
            features |= SWING_MODE
        if payload["swing_horizontal_modes"]:
            features |= SWING_HORIZONTAL_MODE
        if len(payload["hvac_modes"]) > 1 and "off" in payload["hvac_modes"]:
            features |= TURN_ON | TURN_OFF
        return features

    def _baseline_state_for_mode(self, mode: str) -> dict[str, Any]:
        if mode == "off":
            return {
                "mode": "off",
                "target_temperature": 26,
                "current_temperature": 26.5,
                "fan_mode": "Auto",
                "swing_mode": "Auto",
                "swing_horizontal_mode": "Auto",
                "available": True,
            }
        return {
            "mode": "cool",
            "target_temperature": 24,
            "current_temperature": 27,
            "fan_mode": "Auto",
            "swing_mode": "Auto",
            "swing_horizontal_mode": "Auto",
            "available": True,
        }

    def _state_key_to_payload_key(self, key: str) -> str:
        if key == "temperature":
            return "target_temperature"
        return key

    def _mosquitto_common(self) -> list[str]:
        return [
            "-h",
            self.mqtt_host,
            "-p",
            str(self.mqtt_port),
            "-u",
            self.mqtt_user,
            "-P",
            self.mqtt_pass,
        ]

    def _mosquitto_pub_command(
        self,
        *,
        topic: str,
        payload: str,
        retain: bool,
        null_retained: bool,
    ) -> list[str]:
        cmd = ["mosquitto_pub", *self._mosquitto_common(), "-t", topic]
        if retain:
            cmd.append("-r")
        if null_retained:
            cmd.append("-n")
        else:
            cmd.extend(["-m", payload])
        return cmd

    def _mosquitto_sub_command(
        self,
        *,
        topic: str,
        count: int,
        timeout: int,
        verbose: bool = False,
    ) -> list[str]:
        cmd = ["mosquitto_sub", *self._mosquitto_common(), "-C", str(count), "-t", topic, "-W", str(timeout)]
        if verbose:
            cmd.append("-v")
        return cmd

    def _run_command(
        self,
        cmd: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        check: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            env=env,
            text=True,
            capture_output=True,
        )
        if check and result.returncode != 0:
            raise TestFailure(f"Command failed: {' '.join(cmd)}\n{result.stderr}")
        return result

    def _resolve_ha_api_token(self) -> str:
        auth_path = self.ha_core_path / "config" / ".storage" / "auth"
        data = json.loads(auth_path.read_text())
        tokens = data["data"].get("refresh_tokens", [])
        for token in sorted(tokens, key=lambda item: item.get("created_at", ""), reverse=True):
            if token.get("last_used_ip") == "127.0.0.1" and token.get("token"):
                if access_token := self._exchange_refresh_token(token["token"], token.get("client_id")):
                    return access_token
        for token in sorted(tokens, key=lambda item: item.get("created_at", ""), reverse=True):
            if token.get("client_id") in {"http://127.0.0.1:8123/", "http://localhost:8123/"} and token.get("token"):
                if access_token := self._exchange_refresh_token(token["token"], token.get("client_id")):
                    return access_token
        for token in sorted(tokens, key=lambda item: item.get("created_at", ""), reverse=True):
            if token.get("token"):
                if access_token := self._exchange_refresh_token(token["token"], token.get("client_id")):
                    return access_token
        raise TestFailure(f"Could not find a usable Home Assistant API token in {auth_path}")

    def _exchange_refresh_token(self, refresh_token: str, client_id: str | None) -> str | None:
        body: dict[str, str] = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }
        if client_id:
            body["client_id"] = client_id
        request = Request(
            "http://127.0.0.1:8123/auth/token",
            data=urlencode(body).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=10) as response:
                payload = json.loads(response.read().decode())
        except Exception:  # noqa: BLE001
            return None
        access_token = payload.get("access_token")
        if isinstance(access_token, str) and access_token:
            return access_token
        return None

    def _call_ha_service(self, domain: str, service: str, data: dict[str, Any]) -> None:
        if self.ha_api_token is None:
            self.ha_api_token = self._resolve_ha_api_token()
        url = f"http://127.0.0.1:8123/api/services/{domain}/{service}"
        body = json.dumps({"entity_id": self.entity_id, **data}).encode()
        request = Request(
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {self.ha_api_token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=10) as response:
                response.read()
        except HTTPError as err:
            raise TestFailure(f"HA service call failed for {domain}.{service}: {err.read().decode(errors='ignore')}") from err
        except URLError as err:
            raise TestFailure(f"HA service call failed for {domain}.{service}: {err}") from err

    def _write_reports(self) -> None:
        payload = {
            "timestamp": self.timestamp.isoformat(),
            "git": {
                "ha_repo": self._git_head(self.repo_root),
                "esphome_repo": self._git_head(self.esphome_repo_path),
            },
            "environment": {
                "ha_core_path": str(self.ha_core_path),
                "esphome_repo_path": str(self.esphome_repo_path),
                "esphome_workspace_path": str(self.esphome_workspace_path),
                "entity_id": self.entity_id,
                "topic_prefix": self.topic_prefix,
                "mqtt_host": self.mqtt_host,
                "mqtt_port": self.mqtt_port,
                "mode": self.mode,
            },
            "inputs": {
                "output_dir": str(self.output_dir),
            },
            "summary": self._summary(),
            "groups": [self._group_to_json(group) for group in self.groups],
        }
        self.report_json_path.write_text(json.dumps(payload, indent=2, sort_keys=True))
        self.report_md_path.write_text(self._render_markdown())

    def _summary(self) -> dict[str, Any]:
        counts = {"pass": 0, "fail": 0, "skip": 0, "blocked": 0}
        for group in self.groups:
            for case in group.cases:
                counts[case.status] = counts.get(case.status, 0) + 1
        counts["overall"] = "pass" if self._overall_status() else "fail"
        return counts

    def _group_to_json(self, group: GroupResult) -> dict[str, Any]:
        return {
            "id": group.id,
            "title": group.title,
            "cases": [asdict(case) for case in group.cases],
        }

    def _render_markdown(self) -> str:
        lines = [
            "# PanaAC v2 automated test report",
            "",
            f"Timestamp: `{self.timestamp.isoformat()}`",
            f"Mode: `{self.mode}`",
            f"Entity: `{self.entity_id}`",
            f"Topic prefix: `{self.topic_prefix}`",
            "",
            "## Summary",
            "",
        ]
        summary = self._summary()
        for key in ("overall", "pass", "fail", "skip", "blocked"):
            lines.append(f"- `{key}`: {summary[key]}")
        for group in self.groups:
            lines.extend(["", f"## {group.title}", ""])
            for case in group.cases:
                lines.append(f"### {case.id} — {case.status}")
                lines.append("")
                lines.append(f"- Expected: `{json.dumps(case.expected, sort_keys=True) if isinstance(case.expected, (dict, list)) else case.expected}`")
                lines.append(f"- Actual: `{json.dumps(case.actual, sort_keys=True) if isinstance(case.actual, (dict, list)) else case.actual}`")
                if case.evidence:
                    lines.append(f"- Evidence: `{json.dumps(case.evidence, sort_keys=True)}`")
                lines.append(f"- Duration: `{case.duration_s:.2f}s`")
                lines.append("")
        return "\n".join(lines)

    def _git_head(self, repo_path: Path) -> dict[str, str] | None:
        if not (repo_path / ".git").exists():
            return None
        head = self._run_command(["git", "rev-parse", "HEAD"], cwd=repo_path, check=False)
        branch = self._run_command(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_path, check=False)
        if head.returncode != 0 or branch.returncode != 0:
            return None
        return {"branch": branch.stdout.strip(), "commit": head.stdout.strip()}


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    workspace_root = repo_root.parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ha-core-path",
        default=str(workspace_root / "ha" / "core"),
        help="Path to the Home Assistant core checkout.",
    )
    parser.add_argument(
        "--esphome-repo-path",
        default=str(workspace_root / "esphome" / "PanaAC_v2_ESPHome"),
        help="Path to the ESPHome repo checkout.",
    )
    parser.add_argument("--entity-id", help="HA climate entity id. Auto-detected if omitted.")
    parser.add_argument("--topic-prefix", default=DEFAULT_TOPIC_PREFIX, help="MQTT topic prefix under test.")
    parser.add_argument("--mqtt-host", default=DEFAULT_MQTT_HOST, help="MQTT broker host.")
    parser.add_argument("--mqtt-port", type=int, default=DEFAULT_MQTT_PORT, help="MQTT broker port.")
    parser.add_argument("--mqtt-user", required=True, help="MQTT username.")
    parser.add_argument("--mqtt-pass", required=True, help="MQTT password.")
    parser.add_argument(
        "--output-dir",
        default=str(repo_root / "test" / "results" / datetime.now().strftime("%Y%m%d-%H%M%S")),
        help="Directory for JSON/Markdown reports and captured logs.",
    )
    parser.add_argument(
        "--mode",
        choices=("auto", "ha-only", "full-hil"),
        default="auto",
        help="Execution mode. auto runs all automatable checks and skips unmet DUT-only steps.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    runner = Runner(args)
    return runner.run()


if __name__ == "__main__":
    sys.exit(main())
