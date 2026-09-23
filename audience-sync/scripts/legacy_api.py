"""Explicit compatibility CLI for existing legacy API callers."""

# ruff: noqa: E402, I001

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from audience_sync.cli import main


if __name__ == "__main__":
    raise SystemExit(main(legacy=True))
