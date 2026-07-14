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

"""CLI and menu interface for the PanaAC v2 automation runner."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys

from .core import Runner, TestFailure, resolve_suite_selection
from .data import DEFAULT_MQTT_HOST, DEFAULT_MQTT_PORT, DEFAULT_TOPIC_PREFIX, SUITE_CHOICES, SUITE_LABELS

RUNNER_CONFIG_BASENAME = "runner_config.json"


def default_config_path() -> Path:
    return Path(__file__).resolve().parents[1] / RUNNER_CONFIG_BASENAME


def build_parser() -> argparse.ArgumentParser:
    repo_root = Path(__file__).resolve().parents[2]
    workspace_root = repo_root.parents[1]
    parser = argparse.ArgumentParser(description="PanaAC v2 automated test runner")
    subparsers = parser.add_subparsers(dest="command")

    def add_config_argument(target: argparse.ArgumentParser) -> None:
        target.add_argument("--config", default=str(default_config_path()), help="Path to runner JSON config")

    def add_common_arguments(target: argparse.ArgumentParser) -> None:
        add_config_argument(target)
        target.add_argument("--ha-core-path", default=str(workspace_root / "ha" / "core"))
        target.add_argument("--ha-config-path", help="Override Home Assistant config directory")
        target.add_argument("--ha-port", type=int, default=8123, help="Home Assistant HTTP port")
        target.add_argument("--fresh-ha-config", action="store_true", help="Use an isolated HA test config instead of ha/core/config")
        target.add_argument("--reset-fresh-ha-config", action="store_true", help="Delete and recreate the isolated HA test config before setup")
        target.add_argument("--keep-test-config", action="store_true", help="Keep the isolated HA test config after the run finishes")
        target.add_argument("--ha-test-name", default="PanaAC Test Home")
        target.add_argument("--ha-test-username", default="tester")
        target.add_argument("--ha-test-password", default="tester-pass-123")
        target.add_argument("--device-name", default="Test AC")
        target.add_argument("--esphome-repo-path", default=str(workspace_root / "esphome" / "PanaAC_v2_ESPHome"))
        target.add_argument("--entity-id")
        target.add_argument("--topic-prefix", default=DEFAULT_TOPIC_PREFIX)
        target.add_argument("--mqtt-host", default=DEFAULT_MQTT_HOST)
        target.add_argument("--mqtt-port", type=int, default=DEFAULT_MQTT_PORT)
        target.add_argument(
            "--mqtt-broker-mode",
            choices=("external", "spawn"),
            default="external",
            help="Use an existing broker or spawn an isolated broker for this run",
        )
        target.add_argument("--mqtt-user")
        target.add_argument("--mqtt-pass")
        target.add_argument(
            "--output-dir",
            default=str(repo_root / "test" / "results" / datetime.now().strftime("%Y%m%d-%H%M%S")),
        )
        target.add_argument("--mode", choices=("auto", "ha-only", "full-hil"), default="auto")

    run_parser = subparsers.add_parser("run", help="Run selected automated suites")
    add_common_arguments(run_parser)
    run_parser.add_argument(
        "--suite",
        dest="suite_values",
        action="append",
        choices=("all", *SUITE_CHOICES),
        help="Select one or more suites. Repeat to choose multiple.",
    )

    setup_parser = subparsers.add_parser("setup-env", help="Validate and prepare the local HIL test environment")
    add_common_arguments(setup_parser)
    setup_parser.add_argument("--no-start-ha", action="store_true", help="Skip starting Home Assistant")
    setup_parser.add_argument("--no-seed-baseline", action="store_true", help="Skip publishing baseline retained topics")
    setup_parser.add_argument("--no-verify-mqtt", action="store_true", help="Skip MQTT broker round-trip validation")

    fresh_parser = subparsers.add_parser("fresh-env", help="Create a fresh isolated HA test environment and prepare it for HIL")
    add_common_arguments(fresh_parser)
    fresh_parser.add_argument("--no-seed-baseline", action="store_true", help="Skip publishing baseline retained topics")
    fresh_parser.add_argument("--no-verify-mqtt", action="store_true", help="Skip MQTT broker round-trip validation")

    dev_parser = subparsers.add_parser("dev-env", help="Validate the local developer environment only")
    add_common_arguments(dev_parser)

    stubbed_parser = subparsers.add_parser("stubbed", help="Run the stubbed HA pytest suite")
    add_config_argument(stubbed_parser)
    stubbed_parser.add_argument("--ha-core-path", default=str(workspace_root / "ha" / "core"))
    stubbed_parser.add_argument("--group", choices=("all", "subscriptions", "state", "commands"), default="all")

    list_parser = subparsers.add_parser("list", help="List available suites")
    add_config_argument(list_parser)

    menu_parser = subparsers.add_parser("menu", help="Interactive menu")
    add_common_arguments(menu_parser)

    return parser


def normalize_argv(argv: list[str]) -> list[str]:
    if not argv:
        return ["run"]
    if argv[0] in {"run", "setup-env", "fresh-env", "dev-env", "stubbed", "list", "menu"}:
        return argv
    return ["run", *argv]


def load_runner_config(path_str: str) -> dict[str, object]:
    path = Path(path_str)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as err:
        raise TestFailure(f"Invalid JSON in runner config {path}: {err}") from err
    if not isinstance(data, dict):
        raise TestFailure(f"Runner config {path} must contain a JSON object")
    return data


def apply_runner_config(args: argparse.Namespace) -> None:
    config = load_runner_config(getattr(args, "config", str(default_config_path())))
    mqtt = config.get("mqtt")
    if isinstance(mqtt, dict):
        if (
            getattr(args, "mqtt_broker_mode", "external") == "external"
            and isinstance(mqtt.get("broker_mode"), str)
            and mqtt["broker_mode"] in {"external", "spawn"}
            and not getattr(args, "mqtt_broker_mode_explicit", False)
        ):
            args.mqtt_broker_mode = mqtt["broker_mode"]
        if getattr(args, "mqtt_host", DEFAULT_MQTT_HOST) == DEFAULT_MQTT_HOST and isinstance(mqtt.get("host"), str):
            args.mqtt_host = mqtt["host"]
        if getattr(args, "mqtt_port", DEFAULT_MQTT_PORT) == DEFAULT_MQTT_PORT and isinstance(mqtt.get("port"), int):
            args.mqtt_port = mqtt["port"]
        if not getattr(args, "mqtt_user", None) and isinstance(mqtt.get("user"), str):
            args.mqtt_user = mqtt["user"]
        if not getattr(args, "mqtt_pass", None) and isinstance(mqtt.get("pass"), str):
            args.mqtt_pass = mqtt["pass"]


def save_runner_config(args: argparse.Namespace) -> None:
    path = Path(getattr(args, "config", str(default_config_path())))
    data = load_runner_config(str(path)) if path.exists() else {}
    mqtt = data.get("mqtt")
    if not isinstance(mqtt, dict):
        mqtt = {}
        data["mqtt"] = mqtt
    mqtt["broker_mode"] = args.mqtt_broker_mode
    mqtt["host"] = args.mqtt_host
    mqtt["port"] = args.mqtt_port
    mqtt["user"] = args.mqtt_user
    mqtt["pass"] = args.mqtt_pass
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    print(f"Stored MQTT settings in {path}")


def prompt_required(value: str | None, label: str) -> str:
    if value:
        return value
    entered = input(f"{label}: ").strip()
    if not entered:
        raise TestFailure(f"Missing required value for {label}")
    return entered


def ensure_mqtt_credentials(args: argparse.Namespace) -> None:
    if args.mqtt_broker_mode == "spawn":
        return
    if args.mqtt_user and args.mqtt_pass:
        return
    raise TestFailure(
        "Missing MQTT credentials. Pass --mqtt-user/--mqtt-pass or create test/runner_config.json"
    )


def select_default_mqtt_broker_mode(command: str, suites: list[str], explicit: bool, current_mode: str) -> str:
    if explicit:
        return current_mode
    if command == "fresh-env":
        return "spawn"
    if command == "run" and "ha.g3" not in suites:
        return "spawn"
    return current_mode


def run_menu(args: argparse.Namespace) -> int:
    print("PanaAC v2 automated test runner")
    print("")
    print("1. Run all suites")
    for index, suite in enumerate(SUITE_CHOICES, start=2):
        print(f"{index}. Run {suite} - {SUITE_LABELS[suite]}")
    dev_choice = len(SUITE_CHOICES) + 2
    setup_choice = dev_choice + 1
    fresh_choice = setup_choice + 1
    print(f"{dev_choice}. Validate dev environment only")
    print(f"{setup_choice}. Prepare HIL environment only")
    print(f"{fresh_choice}. Prepare fresh isolated HA environment")
    print("q. Quit")
    selection = input("Select option: ").strip().lower()
    if selection == "q":
        return 0

    if selection == str(dev_choice):
        args.command = "dev-env"
        args.suites = []
        return dispatch(args)

    suite_selection = selection.isdigit() and 2 <= int(selection) <= len(SUITE_CHOICES) + 1
    target_command: str
    target_suites: list[str]
    if selection == "1":
        target_command = "run"
        target_suites = list(SUITE_CHOICES)
    elif selection == str(setup_choice):
        target_command = "setup-env"
        target_suites = []
    elif selection == str(fresh_choice):
        target_command = "fresh-env"
        target_suites = []
    else:
        try:
            suite = SUITE_CHOICES[int(selection) - 2]
        except (ValueError, IndexError) as err:
            raise TestFailure(f"Invalid menu selection: {selection}") from err
        target_command = "run"
        target_suites = [suite]

    args.mqtt_broker_mode = select_default_mqtt_broker_mode(
        target_command,
        target_suites,
        getattr(args, "mqtt_broker_mode_explicit", False),
        args.mqtt_broker_mode,
    )
    needs_mqtt = (selection in {"1", str(setup_choice), str(fresh_choice)} or suite_selection) and args.mqtt_broker_mode != "spawn"
    if needs_mqtt:
        had_missing_credentials = not args.mqtt_user or not args.mqtt_pass
        args.mqtt_user = prompt_required(args.mqtt_user, "MQTT user")
        args.mqtt_pass = prompt_required(args.mqtt_pass, "MQTT password")
        if had_missing_credentials:
            save_runner_config(args)

    args.command = target_command
    args.suites = target_suites
    if target_command == "setup-env":
        args.no_start_ha = False
        args.no_seed_baseline = False
        args.no_verify_mqtt = False
    elif target_command == "fresh-env":
        args.no_seed_baseline = False
        args.no_verify_mqtt = False
    return dispatch(args)


def dispatch(args: argparse.Namespace) -> int:
    if args.command == "list":
        for suite in SUITE_CHOICES:
            print(f"{suite}: {SUITE_LABELS[suite]}")
        return 0

    if args.command == "dev-env":
        args.suites = []
        runner = Runner(args)
        status = runner.validate_dev_environment()
        for check in status.checks:
            print(f"- {check}")
        return 0

    if args.command == "fresh-env":
        args.mqtt_broker_mode = select_default_mqtt_broker_mode(
            "fresh-env", [], getattr(args, "mqtt_broker_mode_explicit", False), args.mqtt_broker_mode
        )
        ensure_mqtt_credentials(args)
        args.fresh_ha_config = True
        args.reset_fresh_ha_config = True
        args.suites = []
        runner = Runner(args)
        status = runner.setup_environment(
            start_ha=True,
            seed_baseline=not args.no_seed_baseline,
            verify_mqtt=not args.no_verify_mqtt,
        )
        for check in status.checks:
            print(f"- {check}")
        return 0

    if args.command == "setup-env":
        ensure_mqtt_credentials(args)
        args.suites = []
        runner = Runner(args)
        status = runner.setup_environment(
            start_ha=not args.no_start_ha,
            seed_baseline=not args.no_seed_baseline,
            verify_mqtt=not args.no_verify_mqtt,
        )
        for check in status.checks:
            print(f"- {check}")
        return 0

    if args.command == "stubbed":
        repo_root = Path(__file__).resolve().parents[2]
        cmd = [sys.executable, str(repo_root / "test" / "run_stubbed_pytest.py"), "--ha-core-path", args.ha_core_path, "--group", args.group]
        env = os.environ.copy()
        env["PYTHONPATH"] = str(repo_root)
        return subprocess.run(cmd, env=env, check=False).returncode

    if args.command == "menu":
        return run_menu(args)

    suite_values = getattr(args, "suite_values", None)
    if suite_values is not None:
        args.suites = resolve_suite_selection(suite_values)
    args.mqtt_broker_mode = select_default_mqtt_broker_mode(
        "run", list(args.suites or SUITE_CHOICES), getattr(args, "mqtt_broker_mode_explicit", False), args.mqtt_broker_mode
    )
    ensure_mqtt_credentials(args)
    if not getattr(args, "ha_config_path", None):
        args.fresh_ha_config = True
        args.reset_fresh_ha_config = True
        args.cleanup_test_config = not args.keep_test_config
    else:
        args.cleanup_test_config = False
    runner = Runner(args)
    return runner.run()


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        raw_argv = normalize_argv(sys.argv[1:] if argv is None else argv)
        args = parser.parse_args(raw_argv)
        args.mqtt_broker_mode_explicit = "--mqtt-broker-mode" in raw_argv
        apply_runner_config(args)
        return dispatch(args)
    except TestFailure as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1
