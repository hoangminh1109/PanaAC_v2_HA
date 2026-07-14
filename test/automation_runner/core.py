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

"""Framework and execution engine for the PanaAC v2 automated tests."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
import re
import selectors
import socket
from pathlib import Path
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
import uuid
from typing import Any, Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .data import (
    ACTION_CASES,
    CURRENT_STATE_KEYS,
    DEFAULT_MQTT_PORT,
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
        self.fresh_ha_config = getattr(args, "fresh_ha_config", False)
        self.reset_fresh_ha_config = getattr(args, "reset_fresh_ha_config", False)
        self.cleanup_test_config = getattr(args, "cleanup_test_config", False)
        self.test_env_root = (self.repo_root / "test" / "test_env").resolve()
        requested_config_path = getattr(args, "ha_config_path", None)
        if requested_config_path:
            self.ha_config_path = Path(requested_config_path).resolve()
        elif self.fresh_ha_config:
            self.ha_config_path = self._allocate_fresh_ha_config_path()
        else:
            self.ha_config_path = (self.ha_core_path / "config").resolve()
        self.esphome_repo_path = Path(args.esphome_repo_path).resolve()
        self.esphome_workspace_path = self._resolve_esphome_workspace_path()
        self.output_dir = Path(args.output_dir).resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        requested_port = getattr(args, "ha_port", 8123)
        if self.fresh_ha_config and requested_port == 8123:
            requested_port = 8125
        self.ha_port = requested_port
        self.ha_base_url = f"http://127.0.0.1:{self.ha_port}"
        self.ha_test_name = getattr(args, "ha_test_name", "PanaAC Test Home")
        self.ha_test_username = getattr(args, "ha_test_username", "tester")
        self.ha_test_password = getattr(args, "ha_test_password", "tester-pass-123")
        self.device_name = getattr(args, "device_name", "Test AC")
        self.mqtt_broker_mode = getattr(args, "mqtt_broker_mode", "external")
        self.mqtt_host = args.mqtt_host
        self.mqtt_port = args.mqtt_port
        self.mqtt_user = args.mqtt_user
        self.mqtt_pass = args.mqtt_pass
        if self.mqtt_broker_mode == "spawn":
            self.mqtt_host = "127.0.0.1"
            if self.mqtt_port == DEFAULT_MQTT_PORT:
                self.mqtt_port = self._allocate_free_tcp_port()
            self.mqtt_user = None
            self.mqtt_pass = None
        self.topic_prefix = args.topic_prefix
        self.mode = args.mode
        self.selected_suites = set(args.suites or SUITE_CHOICES)
        self.timestamp = datetime.now().astimezone()
        self.report_json_path = self.output_dir / "report.json"
        self.report_md_path = self.output_dir / "report.md"
        self.ha_log_path = self.output_dir / "ha.log"
        self.mqtt_log_path = self.output_dir / "mqtt-broker.log"
        self.mqtt_config_path = self.output_dir / "mosquitto.conf"
        self.raw_capture_dir = self.output_dir / "captures"
        self.raw_capture_dir.mkdir(parents=True, exist_ok=True)
        self.automations_path = self.ha_config_path / "automations.yaml"
        self.original_automations = self.automations_path.read_text() if self.automations_path.exists() else "[]\n"
        self.entity_id = args.entity_id
        self.ha_api_token: str | None = None
        self.groups: list[GroupResult] = []
        self._cleanup_callbacks: list[tuple[str, Callable[[], None]]] = []
        self._fresh_config_prepared = False
        self._fresh_profile_bootstrapped = False
        self._mqtt_broker_proc: subprocess.Popen[bytes] | None = None

    def run(self) -> int:
        try:
            self._log("Starting PanaAC v2 HA automated tests")
            if self.fresh_ha_config:
                self._prepare_fresh_ha_config()
            if self.mqtt_broker_mode == "spawn":
                self._ensure_mqtt_broker_ready()
            self._validate_environment()
            self.setup_environment(
                start_ha=bool(self.selected_suites & {"ha.g1", "ha.g2", "ha.g3"}),
                seed_baseline=bool(self.selected_suites & {"ha.g1", "ha.g2", "ha.g3"}),
                verify_mqtt=True,
            )
            if self.selected_suites & {"ha.g1", "ha.g2", "ha.g3"}:
                self.entity_id = self.entity_id or self._detect_entity_id()

            if self.selected_suites & {"ha.g1", "ha.g2", "ha.g3"}:
                self.groups.extend(self._run_ha_groups())

            self._write_reports()
            self._log(f"Completed automated tests with overall status: {'pass' if self._overall_status() else 'fail'}")
            return 0 if self._overall_status() else 1
        except Exception as err:  # noqa: BLE001
            self._log(f"Runner failed: {err}")
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
        if self.fresh_ha_config:
            self._prepare_fresh_ha_config()
            status.add(f"Prepared isolated HA config at {self.ha_config_path}")
        if self.mqtt_broker_mode == "spawn":
            self._ensure_mqtt_broker_ready()
            status.add(f"Started isolated MQTT broker on {self.mqtt_host}:{self.mqtt_port}")
        self._validate_environment()
        status.add("Validated required binaries and local paths")
        if verify_mqtt:
            self._log("Verifying MQTT broker publish/subscribe round-trip")
            self._verify_mqtt_round_trip()
            status.add("Verified MQTT broker publish/subscribe round-trip")
        if start_ha:
            self._log("Ensuring Home Assistant is running")
            self._ensure_ha_running()
            status.add(f"Ensured Home Assistant is running on port {self.ha_port}")
            if self.fresh_ha_config:
                self._bootstrap_fresh_ha_profile()
                status.add(
                    f"Bootstrapped fresh HA profile with tester user '{self.ha_test_username}' and integration entries"
                )
            if self.entity_id is not None:
                status.add(f"Resolved entity_id={self.entity_id}")
        if seed_baseline:
            self._log("Restoring baseline retained topics")
            self._restore_baseline_topics()
            if start_ha:
                if self.entity_id is None:
                    self.entity_id = self._detect_entity_id(timeout=45.0)
                    status.add(f"Resolved entity_id={self.entity_id}")
                self._ensure_entity_ready()
            status.add("Published retained baseline traits/state/availability topics")
        return status

    def validate_dev_environment(self) -> EnvironmentStatus:
        if self.fresh_ha_config:
            self._prepare_fresh_ha_config()
        status = EnvironmentStatus()
        if shutil.which("uv") is None:
            raise TestFailure("Required command not found: uv")
        status.add("Found uv on PATH")

        hass_bin = self.ha_core_path / ".venv" / "bin" / "hass"
        if not hass_bin.exists():
            raise TestFailure(
                f"Missing Home Assistant hass binary under {self.ha_core_path}; run script/setup from ha/core first"
            )
        status.add(f"Found Home Assistant hass binary at {hass_bin}")

        if not self.ha_config_path.exists():
            raise TestFailure(
                f"Missing Home Assistant config directory at {self.ha_config_path}; start Home Assistant once to create it"
            )
        status.add(f"Found Home Assistant config directory at {self.ha_config_path}")

        integration_path = self.ha_config_path / "custom_components" / "panaac_v2"
        if integration_path.exists():
            status.add(f"Found custom integration link at {integration_path}")
        else:
            status.add(
                f"Missing custom integration link at {integration_path}; link ha/PanaAC_v2_HA/custom_components/panaac_v2 into ha/core/config/custom_components"
            )

        if self.automations_path.exists():
            status.add(f"Found automations file at {self.automations_path}")
        else:
            status.add(
                f"Missing automations file at {self.automations_path}; start Home Assistant once to create the default config scaffold"
            )
        return status

    def _allocate_free_tcp_port(self) -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            sock.listen(1)
            return int(sock.getsockname()[1])

    def _ensure_mqtt_broker_ready(self) -> None:
        if self.mqtt_broker_mode != "spawn" or self._mqtt_broker_proc is not None:
            return
        mosquitto_bin = shutil.which("mosquitto")
        if mosquitto_bin is None:
            raise TestFailure("Required command not found: mosquitto")
        self._log(f"Starting isolated MQTT broker on {self.mqtt_host}:{self.mqtt_port}")
        self.mqtt_config_path.write_text(
            "\n".join(
                (
                    f"listener {self.mqtt_port} {self.mqtt_host}",
                    "allow_anonymous true",
                    "persistence false",
                )
            )
            + "\n"
        )
        log_file = self.mqtt_log_path.open("a")
        self._mqtt_broker_proc = subprocess.Popen(
            [mosquitto_bin, "-c", str(self.mqtt_config_path)],
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            if self._mqtt_broker_proc.poll() is not None:
                break
            try:
                with socket.create_connection((self.mqtt_host, self.mqtt_port), timeout=1.0):
                    return
            except OSError:
                time.sleep(0.2)
        broker_log = self.mqtt_log_path.read_text() if self.mqtt_log_path.exists() else ""
        raise TestFailure(
            f"Spawned MQTT broker failed to become ready on {self.mqtt_host}:{self.mqtt_port}: {broker_log.strip()}"
        )

    def _stop_mqtt_broker(self) -> None:
        if self._mqtt_broker_proc is None:
            return
        self._log("Stopping isolated MQTT broker")
        proc = self._mqtt_broker_proc
        self._mqtt_broker_proc = None
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)

    def _allocate_fresh_ha_config_path(self) -> Path:
        return (self.test_env_root / f"ha_config_{uuid.uuid4().hex}").resolve()

    def _prune_empty_test_env_dirs(self) -> None:
        current = self.test_env_root
        allowed_root = (self.repo_root / "test").resolve()
        while current != allowed_root and current.exists():
            try:
                current.rmdir()
            except OSError:
                return
            current = current.parent

    def _prepare_fresh_ha_config(self) -> None:
        if not self.fresh_ha_config or self._fresh_config_prepared:
            return
        allowed_root = (self.repo_root / "test").resolve()
        if self.reset_fresh_ha_config and self.ha_config_path.exists():
            if not self.ha_config_path.is_relative_to(allowed_root):
                raise TestFailure(f"Refusing to delete non-test HA config path: {self.ha_config_path}")
            self._stop_ha(self.ha_config_path)
            shutil.rmtree(self.ha_config_path)
        self.ha_config_path.mkdir(parents=True, exist_ok=True)
        custom_components_path = self.ha_config_path / "custom_components"
        custom_components_path.mkdir(parents=True, exist_ok=True)
        integration_source = self.repo_root / "custom_components" / "panaac_v2"
        integration_target = custom_components_path / "panaac_v2"
        if integration_target.is_symlink() or integration_target.exists():
            if integration_target.is_symlink() and integration_target.resolve() == integration_source.resolve():
                pass
            else:
                if integration_target.is_dir() and not integration_target.is_symlink():
                    shutil.rmtree(integration_target)
                else:
                    integration_target.unlink()
        if not integration_target.exists():
            integration_target.symlink_to(integration_source)
        scaffold = {
            "configuration.yaml": (
                "default_config:\n\n"
                f"homeassistant:\n  name: {self.ha_test_name}\n\n"
                f"http:\n  server_port: {self.ha_port}\n\n"
                "automation: !include automations.yaml\n"
                "script: !include scripts.yaml\n"
                "scene: !include scenes.yaml\n"
            ),
            "automations.yaml": "[]\n",
            "scripts.yaml": "{}\n",
            "scenes.yaml": "[]\n",
        }
        for filename, contents in scaffold.items():
            file_path = self.ha_config_path / filename
            if self.reset_fresh_ha_config or not file_path.exists():
                file_path.write_text(contents)
        self.automations_path = self.ha_config_path / "automations.yaml"
        self.original_automations = self.automations_path.read_text() if self.automations_path.exists() else "[]\n"
        self._seed_fresh_mqtt_config_entry()
        self._fresh_config_prepared = True

    def _seed_fresh_mqtt_config_entry(self) -> None:
        storage_dir = self.ha_config_path / ".storage"
        storage_dir.mkdir(parents=True, exist_ok=True)
        storage_path = storage_dir / "core.config_entries"
        payload = self._config_entries_storage_payload(storage_path)
        entries = payload.setdefault("data", {}).setdefault("entries", [])
        existing_entry = next((entry for entry in entries if entry.get("domain") == "mqtt"), None)
        created_at = (
            existing_entry.get("created_at")
            if isinstance(existing_entry, dict) and isinstance(existing_entry.get("created_at"), str)
            else self.timestamp.astimezone().isoformat()
        )
        mqtt_data: dict[str, Any] = {
            "broker": self.mqtt_host,
            "port": self.mqtt_port,
            "protocol": "5",
        }
        if self.mqtt_user:
            mqtt_data["username"] = self.mqtt_user
        if self.mqtt_pass:
            mqtt_data["password"] = self.mqtt_pass
        mqtt_entry = {
            "created_at": created_at,
            "modified_at": self.timestamp.astimezone().isoformat(),
            "data": mqtt_data,
            "disabled_by": None,
            "discovery_keys": {},
            "domain": "mqtt",
            "entry_id": existing_entry.get("entry_id") if isinstance(existing_entry, dict) and existing_entry.get("entry_id") else self._new_config_entry_id(),
            "minor_version": 1,
            "options": existing_entry.get("options", {}) if isinstance(existing_entry, dict) else {},
            "pref_disable_new_entities": False,
            "pref_disable_polling": False,
            "source": "user",
            "subentries": existing_entry.get("subentries", []) if isinstance(existing_entry, dict) else [],
            "title": self.mqtt_host,
            "unique_id": None,
            "version": 2,
        }
        for index, entry in enumerate(entries):
            if entry.get("domain") == "mqtt":
                entries[index] = mqtt_entry
                break
        else:
            entries.append(mqtt_entry)
        storage_path.write_text(json.dumps(payload, indent=2) + "\n")

    def _config_entries_storage_payload(self, storage_path: Path) -> dict[str, Any]:
        if storage_path.exists():
            return json.loads(storage_path.read_text())
        storage_key, storage_version, storage_minor_version = self._config_entries_storage_defaults()
        return {
            "version": storage_version,
            "minor_version": storage_minor_version,
            "key": storage_key,
            "data": {"entries": []},
        }

    def _config_entries_storage_defaults(self) -> tuple[str, int, int]:
        config_entries_source = (self.ha_core_path / "homeassistant" / "config_entries.py").read_text()
        key_match = re.search(r'^STORAGE_KEY = "([^"]+)"$', config_entries_source, re.MULTILINE)
        version_match = re.search(r'^STORAGE_VERSION = (\d+)$', config_entries_source, re.MULTILINE)
        minor_match = re.search(r'^STORAGE_VERSION_MINOR = (\d+)$', config_entries_source, re.MULTILINE)
        if not (key_match and version_match and minor_match):
            raise TestFailure("Could not derive Home Assistant config-entry storage defaults from source")
        return key_match.group(1), int(version_match.group(1)), int(minor_match.group(1))

    def _new_config_entry_id(self) -> str:
        return uuid.uuid4().hex

    def _bootstrap_fresh_ha_profile(self) -> None:
        if not self.fresh_ha_config or self._fresh_profile_bootstrapped:
            return
        steps = self._get_onboarding_steps()
        done = {item["step"]: item["done"] for item in steps}
        if not done.get("user"):
            self._log("Creating tester user via Home Assistant onboarding")
            auth_code = self._api_json_request(
                "/api/onboarding/users",
                method="POST",
                data={
                    "name": self.ha_test_name,
                    "username": self.ha_test_username,
                    "password": self.ha_test_password,
                    "client_id": self._ha_client_id(),
                    "language": "en",
                },
                require_auth=False,
            )["auth_code"]
            self.ha_api_token = self._exchange_auth_code_for_access_token(auth_code)
            done["user"] = True
        elif self.ha_api_token is None:
            self.ha_api_token = self._resolve_ha_api_token()
        if not done.get("core_config"):
            self._api_json_request("/api/onboarding/core_config", method="POST", data={}, require_auth=True)
        if not done.get("integration"):
            self._api_json_request(
                "/api/onboarding/integration",
                method="POST",
                data={"client_id": self._ha_client_id(), "redirect_uri": self._ha_client_id()},
                require_auth=True,
            )
        if not done.get("analytics"):
            self._api_json_request("/api/onboarding/analytics", method="POST", data={}, require_auth=True)
        self._wait_for_component_loaded("mqtt")
        self._ensure_config_entry_via_flow(
            "panaac_v2",
            {"device_name": self.device_name, "topic_prefix": self.topic_prefix},
        )
        self._wait_for_config_entries("panaac_v2")
        self._fresh_profile_bootstrapped = True

    def _ha_client_id(self) -> str:
        return f"{self.ha_base_url}/"

    def _get_onboarding_steps(self) -> list[dict[str, Any]]:
        payload = self._api_json_request("/api/onboarding", require_auth=False)
        if not isinstance(payload, list):
            raise TestFailure(f"Unexpected onboarding payload: {payload!r}")
        return payload

    def _wait_for_component_loaded(self, domain: str, timeout: float = 30.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            payload = self._api_json_request(
                "/api/onboarding/integration/wait",
                method="POST",
                data={"domain": domain},
                require_auth=False,
            )
            if payload.get("integration_loaded"):
                return
            time.sleep(1.0)
        raise TestFailure(f"Timed out waiting for Home Assistant component {domain}")

    def _exchange_auth_code_for_access_token(self, auth_code: str) -> str:
        body = {
            "grant_type": "authorization_code",
            "client_id": self._ha_client_id(),
            "code": auth_code,
        }
        request = Request(
            f"{self.ha_base_url}/auth/token",
            data=urlencode(body).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=10) as response:
                payload = json.loads(response.read().decode())
        except HTTPError as err:
            raise TestFailure(f"HA auth code exchange failed: {err.read().decode(errors='ignore')}") from err
        except URLError as err:
            raise TestFailure(f"HA auth code exchange failed: {err}") from err
        access_token = payload.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise TestFailure(f"HA auth code exchange did not return an access token: {payload!r}")
        return access_token

    def _api_json_request(
        self,
        path: str,
        *,
        method: str = "GET",
        data: Any | None = None,
        require_auth: bool = True,
    ) -> Any:
        headers: dict[str, str] = {}
        payload: bytes | None = None
        if data is not None:
            headers["Content-Type"] = "application/json"
            payload = json.dumps(data).encode()
        if require_auth:
            if self.ha_api_token is None:
                self.ha_api_token = self._resolve_ha_api_token()
            headers["Authorization"] = f"Bearer {self.ha_api_token}"
        request = Request(f"{self.ha_base_url}{path}", data=payload, headers=headers, method=method)
        try:
            with urlopen(request, timeout=15) as response:
                raw = response.read().decode()
        except HTTPError as err:
            raise TestFailure(f"HA request failed for {path}: {err.read().decode(errors='ignore')}") from err
        except URLError as err:
            raise TestFailure(f"HA request failed for {path}: {err}") from err
        if not raw:
            return {}
        return json.loads(raw)

    def _ensure_config_entry_via_flow(self, handler: str, payload: dict[str, Any]) -> None:
        for attempt in range(1, 4):
            started = self._api_json_request(
                "/api/config/config_entries/flow",
                method="POST",
                data={"handler": handler},
                require_auth=True,
            )
            if started.get("type") == "abort":
                reason = started.get("reason")
                if reason in {"already_configured", "single_instance_allowed"}:
                    return
                raise TestFailure(f"Failed to initialize config flow for {handler}: {started}")
            flow_id = started.get("flow_id")
            if not isinstance(flow_id, str):
                raise TestFailure(f"Config flow for {handler} did not return a flow_id: {started}")
            result = self._api_json_request(
                f"/api/config/config_entries/flow/{flow_id}",
                method="POST",
                data=payload,
                require_auth=True,
            )
            result_type = result.get("type")
            if result_type == "create_entry":
                return
            if result_type == "abort" and result.get("reason") in {"already_configured", "single_instance_allowed"}:
                return
            if handler == "mqtt" and result_type == "form" and result.get("errors", {}).get("base") == "cannot_connect" and attempt < 3:
                time.sleep(2.0)
                continue
            raise TestFailure(f"Config flow for {handler} failed: {result}")

    def _wait_for_config_entries(self, domain: str, timeout: float = 30.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            entries = self._api_json_request(
                f"/api/config/config_entries/entry?domain={domain}",
                require_auth=True,
            )
            if isinstance(entries, list) and entries:
                return
            time.sleep(1.0)
        raise TestFailure(f"Timed out waiting for Home Assistant config entry domain {domain}")

    def _run_ha_groups(self) -> list[GroupResult]:
        groups: list[GroupResult] = []
        self._log("Running HA validation groups")
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
        if self.fresh_ha_config:
            self._prepare_fresh_ha_config()
        required_commands = ["mosquitto_pub", "mosquitto_sub", "pgrep", "uv"]
        if self.mqtt_broker_mode == "spawn":
            required_commands.append("mosquitto")
        for cmd in required_commands:
            if shutil.which(cmd) is None:
                raise TestFailure(f"Required command not found: {cmd}")
        if not (self.ha_core_path / ".venv" / "bin" / "hass").exists():
            raise TestFailure(f"Missing Home Assistant hass binary under {self.ha_core_path}")
        if not self.automations_path.exists():
            raise TestFailure(f"Missing automations file at {self.automations_path}")

    def _entity_present(self, entity_id: str | None = None) -> bool:
        target = entity_id or self.entity_id
        if target is None:
            return False
        registry_path = self.ha_config_path / ".storage" / "core.entity_registry"
        if not registry_path.exists():
            return False
        data = json.loads(registry_path.read_text())
        return any(entity.get("entity_id") == target for entity in data["data"]["entities"])

    def _wait_for_entity_registration(self, timeout: float = 45.0, interval: float = 0.5) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._find_entity_id() is not None:
                return
            time.sleep(interval)
        raise TestFailure(f"Entity {self.entity_id} did not appear in entity registry before timeout")

    def _find_entity_id(self) -> str | None:
        unique_id = f"{self.topic_prefix}_climate"
        registry_path = self.ha_config_path / ".storage" / "core.entity_registry"
        if not registry_path.exists():
            return None
        data = json.loads(registry_path.read_text())
        candidates: list[str] = []
        for entity in data["data"]["entities"]:
            if entity.get("unique_id") == unique_id:
                candidates.append(entity["entity_id"])
        if not candidates:
            return None

        scored: list[tuple[int, float, str]] = []
        for entity_id in candidates:
            snapshot = self._read_latest_state_for_entity(entity_id)
            if snapshot is None:
                scored.append((0, 0.0, entity_id))
                continue
            score = 2 if snapshot.get("state") != "unavailable" else 1
            scored.append((score, float(snapshot.get("last_updated_ts") or 0.0), entity_id))

        scored.sort(reverse=True)
        return scored[0][2]

    def _detect_entity_id(self, timeout: float = 90.0, interval: float = 0.5) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            entity_id = self._find_entity_id()
            if entity_id is not None:
                return entity_id
            time.sleep(interval)
        raise TestFailure(f"Could not auto-detect entity_id for unique_id {self.topic_prefix}_climate")

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
        self._log(f"Checking MQTT round-trip on {topic}")
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
            self._log(f"[esphome.g1] {variant}: config and compile")
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
        self._log("[ha.g1] C3 retained baseline verification")
        started = time.monotonic()
        self._restore_baseline_topics()
        time.sleep(0.8)
        c3_registry = self._read_registry_entity()
        c3_state = self._read_latest_state()
        expected_registry = {
            "hvac_modes": RETAINED_BASELINE_TRAITS["hvac_modes"],
            "fan_modes": RETAINED_BASELINE_TRAITS["fan_modes"],
            "swing_modes": RETAINED_BASELINE_TRAITS["swing_modes"],
            "swing_horizontal_modes": RETAINED_BASELINE_TRAITS["swing_horizontal_modes"],
            "min_temp": RETAINED_BASELINE_TRAITS["min_temp"],
            "max_temp": RETAINED_BASELINE_TRAITS["max_temp"],
            "target_temp_step": RETAINED_BASELINE_TRAITS["temp_step"],
            "supported_features": self._expected_supported_features(RETAINED_BASELINE_TRAITS),
        }
        actual_registry = {
            "hvac_modes": c3_registry["capabilities"].get("hvac_modes"),
            "supported_features": c3_registry["supported_features"],
            "fan_modes": c3_registry["capabilities"].get("fan_modes"),
            "swing_modes": c3_registry["capabilities"].get("swing_modes"),
            "swing_horizontal_modes": c3_registry["capabilities"].get("swing_horizontal_modes"),
            "min_temp": c3_registry["capabilities"].get("min_temp"),
            "max_temp": c3_registry["capabilities"].get("max_temp"),
            "target_temp_step": c3_registry["capabilities"].get("target_temp_step"),
        }
        c3_mismatches = self._compare_expected(expected_registry, actual_registry)
        group.add(
            CaseResult(
                id="ha.g1.1",
                title="C3 retained baseline traits",
                status="pass" if not c3_mismatches else "fail",
                expected=expected_registry,
                actual={"registry": actual_registry, "state": c3_state},
                evidence={"mismatches": c3_mismatches} if c3_mismatches else {},
                duration_s=time.monotonic() - started,
            )
        )
        self._restore_baseline_topics()
        return group

    def _run_ha_group_2(self) -> GroupResult:
        group = GroupResult("ha.g2", SUITE_LABELS["ha.g2"])
        self._log("[ha.g2] State, service, and resilience checks")
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
            self._log(f"[ha.g2] availability -> {availability}")
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
            self._log(f"[ha.g2] service case: {case['id']}")
            try:
                prior_snapshot = self._read_latest_state()
                baseline_state = self._baseline_state_for_mode(case["baseline_mode"])
                self._publish_retained("state", baseline_state)
                baseline_snapshot = self._poll_state_subset(
                    self._baseline_state_snapshot(case["baseline_mode"]),
                    newer_than=prior_snapshot.get("last_updated_ts"),
                )
                actual_payload = self._capture_set_payload(case["service"], case["service_data"])
                reflected_snapshot = self._poll_state_subset(
                    case["expected_state"],
                    newer_than=baseline_snapshot.get("last_updated_ts"),
                )
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
            self._log(f"[ha.g2] hvac_action case: {payload['mode']} -> {expected_action}")
            prior_snapshot = self._read_latest_state()
            self._publish_retained("state", payload)
            snapshot = self._poll_state(
                lambda s, expected=payload["mode"], action=expected_action: s.get("state") == expected and s.get("hvac_action") == action,
                newer_than=prior_snapshot.get("last_updated_ts"),
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
        self._log("[ha.g3] ESPHome button and HA service integration checks")

        started = time.monotonic()
        self._log("[ha.g3] climate.control button press")
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
        self._log("[ha.g3] lambda make_call button press")
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
        self._log("[ha.g3] climate.service test")
        prior_snapshot = self._read_latest_state()
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
        baseline_snapshot = self._poll_state_subset(
            {"state": "off", "temperature": 24, "fan_mode": "Auto", "swing_mode": "Auto"},
            newer_than=prior_snapshot.get("last_updated_ts"),
        )
        service_logs = self._capture_debug_for_action(lambda: self._call_ha_service("climate", "set_hvac_mode", {"hvac_mode": "cool"}))
        service_state = self._poll_state_subset({"state": "cool"}, newer_than=baseline_snapshot.get("last_updated_ts"))
        service_log_mismatches = self._missing_log_fragments(service_logs, ("on_control fired",))
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
        self._log("[ha.g3] climate trigger tests")
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
        self._log("[ha.g3] climate condition tests")
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

    def _poll_state(
        self,
        predicate: Callable[[dict[str, Any]], bool],
        timeout: float = 8.0,
        interval: float = 0.4,
        newer_than: float | None = None,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        latest: dict[str, Any] | None = {}
        while time.monotonic() < deadline:
            latest = self._read_latest_state()
            if latest is not None and predicate(latest) and (
                newer_than is None or float(latest.get("last_updated_ts") or 0.0) > newer_than
            ):
                return latest
            time.sleep(interval)
        return latest or {"state": "unavailable"}

    def _poll_state_subset(
        self,
        expected: dict[str, Any],
        timeout: float = 10.0,
        interval: float = 0.4,
        newer_than: float | None = None,
    ) -> dict[str, Any]:
        return self._poll_state(
            lambda state: not self._compare_expected(expected, state),
            timeout=timeout,
            interval=interval,
            newer_than=newer_than,
        )

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
        return self._read_latest_state_for_entity(self.entity_id)

    def _read_latest_state_for_entity(self, entity_id: str | None) -> dict[str, Any] | None:
        if entity_id is None:
            return None
        db_path = self.ha_config_path / "home-assistant_v2.db"
        if not db_path.exists():
            return None
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
            (entity_id,),
        ).fetchone()
        con.close()
        if row is None:
            return None
        attrs = json.loads(row["shared_attrs"]) if row["shared_attrs"] else {}
        result = {"last_updated_ts": row["last_updated_ts"], "state": row["state"]}
        for key in CURRENT_STATE_KEYS[1:]:
            result[key] = attrs.get(key)
        return result

    def _read_registry_entity(self) -> dict[str, Any]:
        registry_path = self.ha_config_path / ".storage" / "core.entity_registry"
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

    def _start_ha(self, config_path: Path | None = None) -> None:
        target_config_path = config_path or self.ha_config_path
        self._log(f"Starting Home Assistant at {target_config_path}")
        with self.ha_log_path.open("a") as log_file:
            subprocess.Popen(
                ["./.venv/bin/hass", "-c", str(target_config_path)],
                cwd=self.ha_core_path,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )

    def _stop_ha(self, config_path: Path | None = None) -> None:
        target_config_path = config_path or self.ha_config_path
        self._log("Stopping Home Assistant")
        pid_file = target_config_path / "home-assistant.pid"
        pids: list[int] = []
        if pid_file.exists():
            content = pid_file.read_text().strip()
            if content.isdigit():
                pids.append(int(content))
        if not pids:
            result = self._run_command(["pgrep", "-f", f"hass -c {target_config_path}"], check=False)
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
            self._log("Cycling mosquitto broker")
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
        if self.mqtt_broker_mode == "spawn":
            if action == "start":
                self._ensure_mqtt_broker_ready()
            else:
                self._stop_mqtt_broker()
            return subprocess.CompletedProcess(["mosquitto", action], 0, "", "")
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
            with urlopen(self.ha_base_url, timeout=3) as response:
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
        if self.cleanup_test_config and self.fresh_ha_config:
            self._delete_fresh_ha_config()
        self._stop_mqtt_broker()

    def _delete_fresh_ha_config(self) -> None:
        allowed_root = (self.repo_root / "test").resolve()
        if not self.ha_config_path.exists():
            return
        if not self.ha_config_path.is_relative_to(allowed_root):
            self._log(f"Skipping cleanup for non-test HA config path: {self.ha_config_path}")
            return
        self._log(f"Deleting isolated HA config at {self.ha_config_path}")
        try:
            self._stop_ha(self.ha_config_path)
        except Exception:
            pass
        shutil.rmtree(self.ha_config_path, ignore_errors=True)
        self._prune_empty_test_env_dirs()

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

    def _baseline_state_snapshot(self, mode: str) -> dict[str, Any]:
        state = self._baseline_state_for_mode(mode)
        return {
            "state": state["mode"],
            "temperature": state["target_temperature"],
            "fan_mode": state["fan_mode"],
            "swing_mode": state["swing_mode"],
            "swing_horizontal_mode": state["swing_horizontal_mode"],
        }

    def _state_key_to_payload_key(self, key: str) -> str:
        return "target_temperature" if key == "temperature" else key

    def _mosquitto_common(self) -> list[str]:
        cmd = ["-h", self.mqtt_host, "-p", str(self.mqtt_port)]
        if self.mqtt_user:
            cmd.extend(["-u", self.mqtt_user])
        if self.mqtt_pass:
            cmd.extend(["-P", self.mqtt_pass])
        return cmd

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
        self._log(f"$ {' '.join(cmd)}")
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE if input_text is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        if input_text is not None and proc.stdin is not None:
            proc.stdin.write(input_text)
            proc.stdin.close()
        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []
        selector = selectors.DefaultSelector()
        if proc.stdout is not None:
            selector.register(proc.stdout, selectors.EVENT_READ, ("stdout", stdout_chunks))
        if proc.stderr is not None:
            selector.register(proc.stderr, selectors.EVENT_READ, ("stderr", stderr_chunks))
        while selector.get_map():
            for key, _ in selector.select():
                stream_name, chunks = key.data
                line = key.fileobj.readline()
                if line == "":
                    selector.unregister(key.fileobj)
                    continue
                chunks.append(line)
                target = sys.stdout if stream_name == "stdout" else sys.stderr
                target.write(line)
                target.flush()
        returncode = proc.wait()
        result = subprocess.CompletedProcess(cmd, returncode, "".join(stdout_chunks), "".join(stderr_chunks))
        if stderr_chunks and returncode == 0:
            self._log(f"[warn] command wrote to stderr: {' '.join(cmd)}")
        if check and result.returncode != 0:
            raise TestFailure(f"Command failed: {' '.join(cmd)}\n{result.stderr}")
        return result

    def _log(self, message: str) -> None:
        print(message, flush=True)

    def _resolve_ha_api_token(self) -> str:
        auth_path = self.ha_config_path / ".storage" / "auth"
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
            f"{self.ha_base_url}/auth/token",
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
            f"{self.ha_base_url}/api/services/{domain}/{service}",
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
        self._log(f"Writing JSON report to {self.report_json_path}")
        self._log(f"Writing Markdown report to {self.report_md_path}")
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
                "mqtt_broker_mode": self.mqtt_broker_mode,
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
            f"MQTT broker: `{self.mqtt_broker_mode}` @ {self.mqtt_host}:{self.mqtt_port}",
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
