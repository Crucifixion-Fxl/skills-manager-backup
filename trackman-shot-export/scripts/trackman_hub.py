#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["rerun-sdk==0.38.1", "pyarrow", "numpy"]
# ///
"""Import TrackMan share links onto a Launch Monitor session in rerun hub and pair every stroke with the LM shots.

    python trackman_hub.py preview --dataset <dataset> --session <session_id>   # read-only dry run
    python trackman_hub.py import  --dataset <dataset> --session <session_id>   # write to the Hub
    python trackman_hub.py shots   --dataset <dataset> --session <session_id>   # re-pair only

`python trackman_hub.py <command> --help` lists the options. Needs Python 3.10+ with rerun-sdk==0.38.1 and pyarrow
(or `uv run trackman_hub.py …`, which reads the inline dependencies above); environment RERUN_HUB_URL, RERUN_HUB_HTTP_URL,
RERUN_HUB_TOKEN, and TRACKMAN_URLS (or --urls-stdin). The work is done by the vendored cloud_ingest package
(same code as the platform's cloud-ingest job); see ../SKILL.md.
"""
import sys
from pathlib import Path

SKILL_VERSION = "1.0.0"

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cloud_ingest.cli import main  # noqa: E402


def argv_with_executor(argv: list[str]) -> list[str]:
    """Record which skill version wrote the shots layer (/shots/summary.algo_version)."""
    if argv and argv[0] in ("preview", "import", "shots") and "--executor" not in argv:
        return [*argv, "--executor", f"skill:trackman-shot-export@{SKILL_VERSION}"]
    return argv


if __name__ == "__main__":
    sys.exit(main(argv_with_executor(sys.argv[1:])))
