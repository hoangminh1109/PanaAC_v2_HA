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
import os
from pathlib import Path
import subprocess
import sys

from .core import Runner, TestFailure, resolve_suite_selection
from .data import DEFAULT_MQTT_HOST, DEFAULT_MQTT_PORT, DEFAULT_TOPIC_PREFIX, SUITE_CHOICES, SUITE_LABELS


def build_parser() -> argparse.ArgumentParser:
    repo_root = Path(__file__).resolve().parents[2]
    workspace_root = repo_root.parents[1]
    parser = argparse.ArgumentParser(description="PanaAC v2 automated test runner")
    subparsers = parser.add_subparsers(dest="command")

    def add_common_arguments(target: argparse.ArgumentParser, *, require_mqtt: bool) -> None:
        target.add_argument("--ha-core-path", default=str(workspace_root / "ha" / "core"))
        target.add_argument("--esphome-repo-path", default=str(workspace_root / "esphome" / "PanaAC_v2_ESPHome"))
        target.add_argument("--entity-id")
        target.add_argument("--topic-prefix", default=DEFAULT_TOPIC_PREFIX)
        target.add_argument("--mqtt-host", default=DEFAULT_MQTT_HOST)
        target.add_argument("--mqtt-port", type=int, default=DEFAULT_MQTT_PORT)
        target.add_argument("--mqtt-user", required=require_mqtt)
        target.add_argument("--mqtt-pass", required=require_mqtt)
        target.add_argument(
            "--output-dir",
            default=str(repo_root / "test" / "results" / datetime.now().strftime("%Y%m%d-%H%M%S")),
        )
        target.add_argument("--mode", choices=("auto", "ha-only", "full-hil"), default="auto")

    run_parser = subparsers.add_parser("run", help="Run selected automated suites")
    add_common_arguments(run_parser, require_mqtt=True)
    run_parser.add_argument(
        "--suite",
        dest="suite_values",
        action="append",
        choices=("all", *SUITE_CHOICES),
        help="Select one or more suites. Repeat to choose multiple.",
    )

    setup_parser = subparsers.add_parser("setup-env", help="Validate and prepare the local test environment")
    add_common_arguments(setup_parser, require_mqtt=True)
    setup_parser.add_argument("--no-start-ha", action="store_true", help="Skip starting Home Assistant")
    setup_parser.add_argument("--no-seed-baseline", action="store_true", help="Skip publishing baseline retained topics")
    setup_parser.add_argument("--no-verify-mqtt", action="store_true", help="Skip MQTT broker round-trip validation")

    stubbed_parser = subparsers.add_parser("stubbed", help="Run the stubbed HA pytest suite")
    stubbed_parser.add_argument("--ha-core-path", default=str(workspace_root / "ha" / "core"))
    stubbed_parser.add_argument("--group", choices=("all", "subscriptions", "state", "commands"), default="all")

    subparsers.add_parser("list", help="List available suites")

    menu_parser = subparsers.add_parser("menu", help="Interactive menu")
    add_common_arguments(menu_parser, require_mqtt=False)

    return parser


def normalize_argv(argv: list[str]) -> list[str]:
    if not argv:
        return ["run"]
    if argv[0] in {"run", "setup-env", "stubbed", "list", "menu"}:
        return argv
    return ["run", *argv]


def prompt_required(value: str | None, label: str) -> str:
    if value:
        return value
    entered = input(f"{label}: ").strip()
    if not entered:
        raise TestFailure(f"Missing required value for {label}")
    return entered


def run_menu(args: argparse.Namespace) -> int:
    print("PanaAC v2 automated test runner")
    print("")
    print("1. Run all suites")
    for index, suite in enumerate(SUITE_CHOICES, start=2):
        print(f"{index}. Run {suite} - {SUITE_LABELS[suite]}")
    print(f"{len(SUITE_CHOICES) + 2}. Setup environment only")
    print("q. Quit")
    selection = input("Select option: ").strip().lower()
    if selection == "q":
        return 0

    args.mqtt_user = prompt_required(args.mqtt_user, "MQTT user")
    args.mqtt_pass = prompt_required(args.mqtt_pass, "MQTT password")

    setup_choice = len(SUITE_CHOICES) + 2
    if selection == "1":
        args.command = "run"
        args.suites = list(SUITE_CHOICES)
    elif selection == str(setup_choice):
        args.command = "setup-env"
        args.no_start_ha = False
        args.no_seed_baseline = False
        args.no_verify_mqtt = False
        args.suites = []
    else:
        try:
            suite = SUITE_CHOICES[int(selection) - 2]
        except (ValueError, IndexError) as err:
            raise TestFailure(f"Invalid menu selection: {selection}") from err
        args.command = "run"
        args.suites = [suite]
    return dispatch(args)


def dispatch(args: argparse.Namespace) -> int:
    if args.command == "list":
        for suite in SUITE_CHOICES:
            print(f"{suite}: {SUITE_LABELS[suite]}")
        return 0

    if args.command == "setup-env":
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

    args.suites = resolve_suite_selection(getattr(args, "suite_values", None))
    runner = Runner(args)
    return runner.run()


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(normalize_argv(sys.argv[1:] if argv is None else argv))
    return dispatch(args)
