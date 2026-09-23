"""Run the fixed-operation Personal API CLI directly from a cloned Skill."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from user_research.cli import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
