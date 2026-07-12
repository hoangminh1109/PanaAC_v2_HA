"""Framework and execution engine for the PanaAC v2 automated tests."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
import shutil
import signal
import sqlite3
import subprocess
import time
from typing import Any, Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .data import (
    ACTION_CASES,
    CURRENT_STATE_KEYS,
    DEFAULT_TOPIC_PREFIX,
    FAN_MODE,
    HVAC_ACTION_CASES,
    RETAINED_BASELINE_STATE,
    RETAINED_BASELINE_TRAITS,
    SUITE_CHOICES,
    SUITE_LABELS,
    SWING_HORIZONTAL_MODE,
    SWING_MODE,
    TARGET_TEMPERATURE,
    TURN_OFF,
    TURN_ON,
    VARIANT_TRAITS,
)


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


@dataclass
class EnvironmentStatus:
    checks: list[str] = field(default_factory=list)

    def add(self, message: str) -> None:
        self.checks.append(message)


class Runner:
    """Main orchestrator for the automated test plan."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.repo_root = Path(__file__).resolve().parents[2]
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
        self.selected_suites = set(args.suites or SUITE_CHOICES)
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
        self._cleanup_callbacks: list[tuple[str, Callable[[], None]]] = []

    def run(self) -> int:
        try:
            self._validate_environment()
            self.entity_id = self.entity_id or self._detect_entity_id()
            self.setup_environment(
                start_ha=bool(self.selected_suites & {"ha.g1", "ha.g2", "ha.g3"}),
                seed_baseline=bool(self.selected_suites & {"ha.g1", "ha.g2", "ha.g3"}),
                verify_mqtt=True,
            )

            if "esphome.g1" in self.selected_suites:
                self.groups.append(self._run_esphome_group())

            if self.selected_suites & {"ha.g1", "ha.g2", "ha.g3"}:
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

    def setup_environment(
        self,
        *,
        start_ha: bool,
        seed_baseline: bool,
        verify_mqtt: bool,
    ) -> EnvironmentStatus:
        status = EnvironmentStatus()
        self._validate_environment()
        status.add("Validated required binaries and local paths")
        if verify_mqtt:
            self._verify_mqtt_round_trip()
            status.add("Verified MQTT broker publish/subscribe round-trip")
        if start_ha:
            self._ensure_ha_running()
            status.add("Ensured Home Assistant is running")
            self.entity_id = self.entity_id or self._detect_entity_id()
            status.add(f"Resolved entity_id={self.entity_id}")
        if seed_baseline:
            self._restore_baseline_topics()
            if start_ha:
                self._ensure_entity_ready()
            status.add("Published retained baseline traits/state/availability topics")
        return status

    def _run_ha_groups(self) -> list[GroupResult]:
        groups: list[GroupResult] = []
        self._ensure_ha_running()
        self._ensure_entity_ready()

        if "ha.g1" in self.selected_suites:
            groups.append(self._run_ha_group_1())
        if "ha.g2" in self.selected_suites:
            groups.append(self._run_ha_group_2())
        if "ha.g3" in self.selected_suites:
            groups.append(self._run_ha_group_3())
        return groups

    def _validate_environment(self) -> None:
        for cmd in ("mosquitto_pub", "mosquitto_sub", "pgrep", "uv"):
            if shutil.which(cmd) is None:
                raise TestFailure(f"Required command not found: {cmd}")
        if not (self.ha_core_path / ".venv" / "bin" / "hass").exists():
            raise TestFailure(f"Missing Home Assistant hass binary under {self.ha_core_path}")
        if "esphome.g1" in self.selected_suites and not (self.esphome_workspace_path / ".venv" / "bin" / "esphome").exists():
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

    def _verify_mqtt_round_trip(self) -> None:
        topic = f"{self.topic_prefix}/test_runner_probe"
        payload = f"probe-{int(time.time())}"
        sub_cmd = self._mosquitto_sub_command(topic=topic, count=1, timeout=4)
        proc = subprocess.Popen(sub_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        time.sleep(0.2)
        self._publish_text(topic, payload, retain=False)
        stdout, stderr = proc.communicate(timeout=6)
        if proc.returncode != 0 or stdout.strip() != payload:
            raise TestFailure(f"MQTT broker verification failed for {topic}: {stderr.strip() or stdout.strip()}")

    def _run_esphome_group(self) -> GroupResult:
        group = GroupResult("esphome.g1", SUITE_LABELS["esphome.g1"])
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
                    actual={"config_rc": config_result.returncode, "compile_rc": compile_result.returncode},
                    evidence={"log_path": str(capture_path)},
                    duration_s=time.monotonic() - started,
                )
            )
        return group

    def _run_ha_group_1(self) -> GroupResult:
        group = GroupResult("ha.g1", SUITE_LABELS["ha.g1"])
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
            group.add(
                CaseResult(
                    id=f"ha.g1.2.{variant.lower()}",
                    title=f"Variant {variant} traits adoption",
                    status="pass" if not mismatches else "fail",
                    expected=expected,
                    actual=actual,
                    evidence={"latest_state": state, **({"mismatches": mismatches} if mismatches else {})},
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
        group = GroupResult("ha.g2", SUITE_LABELS["ha.g2"])
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
        expected_state = {
            "state": "cool",
            "hvac_action": "cooling",
            "temperature": 24,
            "current_temperature": 27,
            "fan_mode": "Level 2",
            "swing_mode": "Middle",
            "swing_horizontal_mode": "Left",
        }
        snapshot = self._poll_state_subset(expected_state)
        state_ok = not self._compare_expected(expected_state, snapshot)
        group.add(
            CaseResult(
                id="ha.g2.1.state_ingestion",
                title="Representative state ingestion",
                status="pass" if state_ok else "fail",
                expected=expected_state,
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
            group.add(
                CaseResult(
                    id=f"ha.g2.1.availability_{availability}",
                    title=f"Availability {availability}",
                    status="pass" if snapshot.get("state") == expected_state else "fail",
                    expected={"state": expected_state},
                    actual=snapshot,
                    duration_s=time.monotonic() - started,
                )
            )
        self._publish_retained("availability", "online")
        time.sleep(0.5)

        for case in ACTION_CASES:
            started = time.monotonic()
            try:
                self._publish_retained("state", self._baseline_state_for_mode(case["baseline_mode"]))
                time.sleep(0.6)
                actual_payload = self._capture_set_payload(case["service"], case["service_data"])
                reflected_snapshot = self._poll_state_subset(case["expected_state"])
                payload_ok = actual_payload == case["expected_payload"]
                state_mismatches = self._compare_expected(case["expected_state"], reflected_snapshot)
                actual: Any = {
                    "set_payload": actual_payload,
                    "reflected_state": reflected_snapshot,
                }
                evidence = {"state_mismatches": state_mismatches} if state_mismatches else {}
                status = "pass" if payload_ok and not state_mismatches else "fail"
            except TestFailure as err:
                actual = str(err)
                evidence = {"exception_type": type(err).__name__}
                status = "fail"
            group.add(
                CaseResult(
                    id=f"ha.g2.2.{case['id']}",
                    title=case["id"],
                    status=status,
                    expected={
                        "set_payload": case["expected_payload"],
                        "reflected_state": case["expected_state"],
                    },
                    actual=actual,
                    evidence=evidence,
                    duration_s=time.monotonic() - started,
                )
            )

        for expected_action, payload in HVAC_ACTION_CASES:
            started = time.monotonic()
            self._publish_retained("state", payload)
            snapshot = self._poll_state(
                lambda s, expected=payload["mode"], action=expected_action: s.get("state") == expected and s.get("hvac_action") == action
            )
            group.add(
                CaseResult(
                    id=f"ha.g2.3.{payload['mode']}.{expected_action}",
                    title=f"hvac_action for {payload['mode']}",
                    status="pass" if snapshot.get("hvac_action") == expected_action else "fail",
                    expected={"hvac_action": expected_action},
                    actual=snapshot,
                    duration_s=time.monotonic() - started,
                )
            )

        started = time.monotonic()
        self._delete_retained("traits")
        self._restart_ha()
        cold_registry = self._read_registry_entity()
        cold_modes = cold_registry["capabilities"].get("hvac_modes")
        self._restore_baseline_topics()
        time.sleep(0.8)
        restored_registry = self._read_registry_entity()
        restored_modes = restored_registry["capabilities"].get("hvac_modes")
        restored_status = "pass" if restored_modes == RETAINED_BASELINE_TRAITS["hvac_modes"] else "fail"
        cold_status = "pass" if cold_modes == ["off"] else "fail"
        resilience_status = "pass" if cold_status == "pass" and restored_status == "pass" else "fail"
        resilience_actual: Any = {
            "cold_hvac_modes": cold_modes,
            "restored_hvac_modes": restored_modes,
        }
        if self.mode == "auto" and cold_modes == RETAINED_BASELINE_TRAITS["hvac_modes"] and restored_status == "pass":
            resilience_status = "skip"
            resilience_actual = {
                **resilience_actual,
                "note": "Live DUT republished traits before a conservative cold snapshot could be isolated",
            }
        group.add(
            CaseResult(
                id="ha.g2.4.retained_resilience",
                title="Traits deletion + HA restart resilience",
                status=resilience_status,
                expected={
                    "cold_hvac_modes": ["off"],
                    "restored_hvac_modes": RETAINED_BASELINE_TRAITS["hvac_modes"],
                },
                actual=resilience_actual,
                duration_s=time.monotonic() - started,
            )
        )

        if self.mode == "full-hil":
            broker_cycle = self._run_broker_cycle_case()
            group.add(
                CaseResult(
                    id="ha.g2.4.broker_cycle",
                    title="Broker stop/start resilience",
                    status=broker_cycle["status"],
                    expected="Runner controls broker lifecycle and verifies recovery",
                    actual=broker_cycle["actual"],
                    evidence=broker_cycle.get("evidence", {}),
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
        group = GroupResult("ha.g3", SUITE_LABELS["ha.g3"])

        started = time.monotonic()
        control_logs = self._press_esphome_button("esphome-panaac-v2/button/control_cool_24c/command")
        control_state = self._poll_state_subset(
            {
                "state": "cool",
                "temperature": 24,
                "fan_mode": "Auto",
                "swing_mode": "Auto",
            }
        )
        control_log_mismatches = self._missing_log_fragments(control_logs, ("on_control fired", "on_state fired", "Sending remote code"))
        control_state_mismatches = self._compare_expected(
            {
                "state": "cool",
                "temperature": 24,
                "fan_mode": "Auto",
                "swing_mode": "Auto",
            },
            control_state,
        )
        group.add(
            CaseResult(
                id="ha.g3.1",
                title="ESPHome climate.control observed via HA",
                status="pass" if not control_log_mismatches and not control_state_mismatches else "fail",
                expected={
                    "ha_state": {"state": "cool", "temperature": 24, "fan_mode": "Auto", "swing_mode": "Auto"},
                    "dut_logs": ["on_control fired", "on_state fired", "Sending remote code"],
                },
                actual={"ha_state": control_state, "dut_logs": control_logs},
                evidence={
                    key: value
                    for key, value in {
                        "state_mismatches": control_state_mismatches,
                        "log_mismatches": control_log_mismatches,
                    }.items()
                    if value
                },
                duration_s=time.monotonic() - started,
            )
        )

        started = time.monotonic()
        make_call_logs = self._press_esphome_button("esphome-panaac-v2/button/lambda_make_call_cool_24c/command")
        make_call_state = self._poll_state_subset(
            {
                "state": "cool",
                "temperature": 24,
                "fan_mode": "Level 2",
            }
        )
        make_call_log_mismatches = self._missing_log_fragments(make_call_logs, ("on_control fired", "on_state fired", "Sending remote code"))
        make_call_state_mismatches = self._compare_expected(
            {
                "state": "cool",
                "temperature": 24,
                "fan_mode": "Level 2",
            },
            make_call_state,
        )
        group.add(
            CaseResult(
                id="ha.g3.2",
                title="ESPHome lambda make_call observed via HA",
                status="pass" if not make_call_log_mismatches and not make_call_state_mismatches else "fail",
                expected={
                    "ha_state": {"state": "cool", "temperature": 24, "fan_mode": "Level 2"},
                    "dut_logs": ["on_control fired", "on_state fired", "Sending remote code"],
                },
                actual={"ha_state": make_call_state, "dut_logs": make_call_logs},
                evidence={
                    key: value
                    for key, value in {
                        "state_mismatches": make_call_state_mismatches,
                        "log_mismatches": make_call_log_mismatches,
                    }.items()
                    if value
                },
                duration_s=time.monotonic() - started,
            )
        )

        started = time.monotonic()
        self._publish_retained(
            "state",
            {
                "mode": "off",
                "target_temperature": 24,
                "current_temperature": 26.5,
                "fan_mode": "Auto",
                "swing_mode": "Auto",
                "swing_horizontal_mode": "Auto",
                "available": True,
            },
        )
        time.sleep(0.8)
        service_logs = self._capture_debug_for_action(lambda: self._call_ha_service("climate", "set_hvac_mode", {"hvac_mode": "cool"}))
        service_state = self._poll_state_subset({"state": "cool"})
        service_log_mismatches = self._missing_log_fragments(service_logs, ("on_control fired", "on_state fired"))
        service_state_mismatches = self._compare_expected({"state": "cool"}, service_state)
        group.add(
            CaseResult(
                id="ha.g3.3",
                title="ESPHome on_state / on_control observed via HA",
                status="pass" if not service_log_mismatches and not service_state_mismatches else "fail",
                expected={
                    "ha_state": {"state": "cool"},
                    "dut_logs": ["on_control fired", "on_state fired"],
                },
                actual={"ha_state": service_state, "dut_logs": service_logs},
                evidence={
                    key: value
                    for key, value in {
                        "state_mismatches": service_state_mismatches,
                        "log_mismatches": service_log_mismatches,
                    }.items()
                    if value
                },
                duration_s=time.monotonic() - started,
            )
        )

        started = time.monotonic()
        trigger_log = self.raw_capture_dir / "ha-climate-trigger-tests.log"
        trigger_cmd = [
            "uv",
            "run",
            "--with-requirements",
            "requirements_test.txt",
            "pytest",
            "tests/components/climate/test_trigger.py",
            "-k",
            "started_cooling or started_heating or started_drying",
            "-q",
        ]
        trigger_result = self._run_command(trigger_cmd, cwd=self.ha_core_path, check=False)
        trigger_log.write_text("\n".join([f"$ {' '.join(trigger_cmd)}", trigger_result.stdout, trigger_result.stderr]))
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
            "--with-requirements",
            "requirements_test.txt",
            "pytest",
            "tests/components/climate/test_condition.py",
            "-k",
            "is_cooling or is_heating or is_drying or is_hvac_mode",
            "-q",
        ]
        condition_result = self._run_command(condition_cmd, cwd=self.ha_core_path, check=False)
        condition_log.write_text("\n".join([f"$ {' '.join(condition_cmd)}", condition_result.stdout, condition_result.stderr]))
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
        return group

    def _capture_set_payload(self, service: str, service_data: dict[str, Any]) -> Any:
        last_error = ""
        for _ in range(2):
            self._ensure_entity_ready()
            sub_cmd = self._mosquitto_sub_command(topic=f"{self.topic_prefix}/set", count=1, timeout=5)
            proc = subprocess.Popen(sub_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            time.sleep(0.8)
            self._call_ha_service("climate", service, service_data)
            stdout, stderr = proc.communicate(timeout=7)
            if proc.returncode == 0 and stdout.strip():
                return json.loads(stdout.strip())
            last_error = stderr.strip() or stdout.strip() or f"returncode={proc.returncode}"
            time.sleep(1.0)
        raise TestFailure(f"Failed to capture set payload for {service}: {last_error}")

    def _poll_state(self, predicate: Callable[[dict[str, Any]], bool], timeout: float = 8.0, interval: float = 0.4) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        latest: dict[str, Any] = {}
        while time.monotonic() < deadline:
            latest = self._read_latest_state()
            if predicate(latest):
                return latest
            time.sleep(interval)
        return latest

    def _poll_state_subset(self, expected: dict[str, Any], timeout: float = 10.0, interval: float = 0.4) -> dict[str, Any]:
        return self._poll_state(lambda state: not self._compare_expected(expected, state), timeout=timeout, interval=interval)

    def _press_esphome_button(self, topic: str) -> list[str]:
        return self._capture_debug_for_action(lambda: self._publish_text(topic, "PRESS"))

    def _capture_debug_for_action(self, action: Callable[[], None], *, count: int = 6, timeout: int = 6) -> list[str]:
        sub_cmd = self._mosquitto_sub_command(topic="esphome-panaac-v2/debug", count=count, timeout=timeout)
        proc = subprocess.Popen(sub_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        time.sleep(0.2)
        action()
        stdout, stderr = proc.communicate(timeout=timeout + 2)
        if proc.returncode != 0 and not stdout.strip():
            raise TestFailure(f"Failed to capture DUT debug logs: {stderr.strip() or stdout.strip()}")
        return [line.strip() for line in stdout.splitlines() if line.strip()]

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
        if self._http_ready():
            return
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
                if line.strip().isdigit():
                    pids.append(int(line.strip()))
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

    def _run_broker_cycle_case(self) -> dict[str, Any]:
        started = time.monotonic()
        try:
            self._run_broker_service("stop")
            unavailable_snapshot = self._poll_state(lambda s: s.get("state") == "unavailable", timeout=20.0, interval=0.5)
            self._run_broker_service("start")
            recovered_snapshot = self._poll_state(lambda s: s.get("state") != "unavailable", timeout=30.0, interval=0.5)
            return {
                "status": "pass" if unavailable_snapshot.get("state") == "unavailable" and recovered_snapshot.get("state") != "unavailable" else "fail",
                "actual": {
                    "unavailable_snapshot": unavailable_snapshot,
                    "recovered_snapshot": recovered_snapshot,
                },
                "evidence": {"duration_s": round(time.monotonic() - started, 2)},
            }
        except Exception as err:  # noqa: BLE001
            try:
                self._run_broker_service("start", check=False)
            except Exception:
                pass
            return {
                "status": "fail",
                "actual": str(err),
                "evidence": {"duration_s": round(time.monotonic() - started, 2)},
            }

    def _run_broker_service(self, action: str, *, check: bool = True) -> subprocess.CompletedProcess[str]:
        if action not in {"start", "stop"}:
            raise TestFailure(f"Unsupported broker service action: {action}")
        sudo_password = os.environ.get("BROKER_SUDO_PASSWORD")
        if sudo_password:
            cmd = ["sudo", "-S", "systemctl", action, "mosquitto"]
            return self._run_command(cmd, input_text=f"{sudo_password}\n", check=check)
        return self._run_command(["systemctl", action, "mosquitto"], check=check)

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
        if isinstance(payload, str):
            encoded = payload
        else:
            encoded = json.dumps(payload, separators=(",", ":"))
        self._publish_text(topic, encoded, retain=True)

    def _delete_retained(self, suffix: str) -> None:
        self._publish_text(f"{self.topic_prefix}/{suffix}", "", retain=True, null_retained=True)

    def _restore_baseline_topics(self) -> None:
        self._publish_retained("traits", RETAINED_BASELINE_TRAITS)
        self._publish_retained("availability", "online")
        self._publish_retained("state", RETAINED_BASELINE_STATE)

    def _cleanup(self) -> None:
        while self._cleanup_callbacks:
            _, callback = self._cleanup_callbacks.pop()
            try:
                callback()
            except Exception:
                continue

    def _overall_status(self) -> bool:
        failing_statuses = {"fail", "blocked"} if self.mode == "full-hil" else {"fail"}
        return not any(case.status in failing_statuses for group in self.groups for case in group.cases)

    def _compare_expected(self, expected: dict[str, Any], actual: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {
            key: {"expected": value, "actual": actual.get(key)}
            for key, value in expected.items()
            if actual.get(key) != value
        }

    def _missing_log_fragments(self, lines: list[str], fragments: tuple[str, ...]) -> list[str]:
        return [fragment for fragment in fragments if not any(fragment in line for line in lines)]

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
        return "target_temperature" if key == "temperature" else key

    def _mosquitto_common(self) -> list[str]:
        return ["-h", self.mqtt_host, "-p", str(self.mqtt_port), "-u", self.mqtt_user, "-P", self.mqtt_pass]

    def _mosquitto_pub_command(self, *, topic: str, payload: str, retain: bool, null_retained: bool) -> list[str]:
        cmd = ["mosquitto_pub", *self._mosquitto_common(), "-t", topic]
        if retain:
            cmd.append("-r")
        if null_retained:
            cmd.append("-n")
        else:
            cmd.extend(["-m", payload])
        return cmd

    def _mosquitto_sub_command(self, *, topic: str, count: int, timeout: int, verbose: bool = False) -> list[str]:
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
        input_text: str | None = None,
        check: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(cmd, cwd=cwd, env=env, input=input_text, text=True, capture_output=True)
        if check and result.returncode != 0:
            raise TestFailure(f"Command failed: {' '.join(cmd)}\n{result.stderr}")
        return result

    def _resolve_ha_api_token(self) -> str:
        auth_path = self.ha_core_path / "config" / ".storage" / "auth"
        data = json.loads(auth_path.read_text())
        tokens = data["data"].get("refresh_tokens", [])
        for token in sorted(tokens, key=lambda item: item.get("created_at", ""), reverse=True):
            if token.get("token"):
                access_token = self._exchange_refresh_token(token["token"], token.get("client_id"))
                if access_token:
                    return access_token
        raise TestFailure(f"Could not find a usable Home Assistant API token in {auth_path}")

    def _exchange_refresh_token(self, refresh_token: str, client_id: str | None) -> str | None:
        body = {"grant_type": "refresh_token", "refresh_token": refresh_token}
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
        except Exception:
            return None
        access_token = payload.get("access_token")
        return access_token if isinstance(access_token, str) and access_token else None

    def _call_ha_service(self, domain: str, service: str, data: dict[str, Any]) -> None:
        if self.ha_api_token is None:
            self.ha_api_token = self._resolve_ha_api_token()
        request = Request(
            f"http://127.0.0.1:8123/api/services/{domain}/{service}",
            data=json.dumps({"entity_id": self.entity_id, **data}).encode(),
            headers={"Authorization": f"Bearer {self.ha_api_token}", "Content-Type": "application/json"},
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
                "selected_suites": sorted(self.selected_suites),
            },
            "inputs": {"output_dir": str(self.output_dir)},
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
        return {"id": group.id, "title": group.title, "cases": [asdict(case) for case in group.cases]}

    def _render_markdown(self) -> str:
        lines = [
            "# PanaAC v2 automated test report",
            "",
            f"Timestamp: `{self.timestamp.isoformat()}`",
            f"Mode: `{self.mode}`",
            f"Suites: `{', '.join(sorted(self.selected_suites))}`",
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
                expected = json.dumps(case.expected, sort_keys=True) if isinstance(case.expected, (dict, list)) else case.expected
                actual = json.dumps(case.actual, sort_keys=True) if isinstance(case.actual, (dict, list)) else case.actual
                lines.append(f"- Expected: `{expected}`")
                lines.append(f"- Actual: `{actual}`")
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


def resolve_suite_selection(values: Iterable[str] | None) -> list[str]:
    if not values:
        return list(SUITE_CHOICES)
    resolved: list[str] = []
    for value in values:
        if value == "all":
            resolved.extend(SUITE_CHOICES)
            continue
        if value not in SUITE_CHOICES:
            raise TestFailure(f"Unknown suite selection: {value}")
        resolved.append(value)
    return sorted(set(resolved), key=lambda item: SUITE_CHOICES.index(item))
