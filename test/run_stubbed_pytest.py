#!/usr/bin/env python3
"""Run the stubbed pytest suite for the PanaAC v2 HA integration."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys


def build_parser() -> argparse.ArgumentParser:
    repo_root = Path(__file__).resolve().parents[1]
    workspace_root = repo_root.parents[1]
    parser = argparse.ArgumentParser(description="Run stubbed pytest coverage for PanaAC v2 HA")
    parser.add_argument("--ha-core-path", default=str(workspace_root / "ha" / "core"))
    parser.add_argument("--group", choices=("all", "subscriptions", "state", "commands"), default="all")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = Path(__file__).resolve().parents[1]
    ha_core_path = Path(args.ha_core_path)
    test_path = repo_root / "test" / "pytest_stubbed" / "test_climate_entity.py"
    k_filters = {
        "all": None,
        "subscriptions": "subscribes",
        "state": "traits or state or availability or invalid_payloads or defaults",
        "commands": "publish_expected_payload or turn_off or turn_on",
    }
    cmd = [
        str(ha_core_path / ".venv" / "bin" / "uv"),
        "run",
        "--with-requirements",
        "requirements_test.txt",
        "pytest",
        str(test_path),
        "-q",
    ]
    if k_filters[args.group]:
        cmd.extend(["-k", k_filters[args.group]])
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo_root)
    env["UV_CACHE_DIR"] = env.get("UV_CACHE_DIR", "/tmp/uv-cache")
    env["XDG_CACHE_HOME"] = env.get("XDG_CACHE_HOME", "/tmp/.cache")
    return subprocess.run(cmd, cwd=ha_core_path, env=env, check=False).returncode


if __name__ == "__main__":
    sys.exit(main())
