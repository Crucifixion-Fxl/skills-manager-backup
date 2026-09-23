#!/usr/bin/env python3
"""Run local-relay integration suites with per-case evidence receipts.

Usage:
  BUZZ_SYNC_L2=1 python3 tests/integration/run_local_evidence.py \
      test_sync_local test_gitlab_buzz_sync_localstack  # module names under tests/

Each case writes <case>.json and the run writes index.md under
~/buzz-agent-work/l4-local-evidence/<run-id>/.
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from evidence import EvidenceResult, EvidenceRun  # noqa: E402


def main(argv: list[str]) -> int:
    run = EvidenceRun()
    EvidenceResult.run = run
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for name in argv[1:]:
        target = name if name.startswith("test_") else f"test_{name}"
        path = HERE / f"{target}.py"
        if path.is_file():
            spec = importlib.util.spec_from_file_location(target, path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            suite.addTests(loader.loadTestsFromModule(module))
        else:
            suite.addTests(loader.loadTestsFromName(name))
    runner = unittest.TextTestRunner(resultclass=EvidenceResult, verbosity=2)
    result = runner.run(suite)
    index = run.finalize()
    print(f"\nevidence index: {index}")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
