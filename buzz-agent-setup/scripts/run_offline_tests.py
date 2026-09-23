#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Run buzz-agent-setup offline tests with a canonical temporary directory."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


SKILL_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = SKILL_DIR.parents[1]
TEST_DIR = SKILL_DIR / "tests"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pattern", default="test_*.py", help="unittest discovery filename pattern")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    if Path(args.pattern).name != args.pattern or any(char in args.pattern for char in "\r\n\0"):
        parser.error("--pattern must be a filename pattern, not a path")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    # macOS commonly exposes /var as a symlink to /private/var. Security launchers
    # intentionally reject non-canonical paths, so every test process gets the
    # canonical spelling instead of relying on a caller-specific TMPDIR override.
    canonical_tmp = Path(tempfile.gettempdir()).resolve()
    env = os.environ.copy()
    env["TMPDIR"] = str(canonical_tmp)
    command = [
        sys.executable,
        "-m",
        "unittest",
        "discover",
        "-s",
        str(TEST_DIR),
        "-p",
        args.pattern,
    ]
    if args.verbose:
        command.append("-v")
    try:
        returncode = subprocess.run(command, cwd=REPO_ROOT, env=env, check=False).returncode
    except OSError as exc:
        print(json.dumps({"status": "error", "error_type": type(exc).__name__}), file=sys.stderr)
        return 2
    print(json.dumps({"status": "ok" if returncode == 0 else "failed", "returncode": returncode}))
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
