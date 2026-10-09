#!/usr/bin/env python3
"""Applicant entrypoint; the shared deterministic implementation ships with addx."""
from pathlib import Path
import runpy
import sys

_WRAPPER_PATH = Path(__file__).resolve()
_CANDIDATES = (
    _WRAPPER_PATH.parents[5] / 'skills/security/buzz-data-access-admin/scripts/data_access.py',
    _WRAPPER_PATH.parents[3] / 'buzz-data-access-admin/scripts/data_access.py',
)
SCRIPT = next((candidate for candidate in _CANDIDATES if candidate.is_file()), _CANDIDATES[0])
if not SCRIPT.is_file():
    print('buzz-data-access-admin is required; install both Skills from the same addx revision', file=sys.stderr)
    sys.exit(1)
if __name__ == '__main__':
    runpy.run_path(str(SCRIPT), run_name='__main__')
