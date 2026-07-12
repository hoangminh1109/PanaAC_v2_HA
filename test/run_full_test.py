#!/usr/bin/env python3
"""Compatibility entrypoint for the PanaAC v2 automated test runner."""

from __future__ import annotations

import sys

from automation_runner.cli import main


if __name__ == "__main__":
    sys.exit(main())
