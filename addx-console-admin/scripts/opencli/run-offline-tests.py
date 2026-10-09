#!/usr/bin/env python3
"""Run all standalone adapter tests from a clean HOME; never call business APIs."""
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[4]
FIXTURES = ROOT / "write-tests" / "fixtures"
node_tests = sorted(p for p in (ROOT / "write-tests").glob("*.mjs") if "test" in p.name and "fixtures" not in p.parts)
python_tests = sorted(p for p in ROOT.rglob("*tests.py") if p != Path(__file__).resolve()) + sorted((REPO / "skills/observability/graylog/scripts/tests").glob("*.py")) + sorted((REPO / "skills/customer-care/zendesk/scripts/tests").glob("test_*.py"))
failures = []
with tempfile.TemporaryDirectory(prefix="addx-offline-tests-") as home:
    env = {"PATH": os.environ["PATH"], "HOME": home, "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1", "NODE_NO_WARNINGS": "1"}
    for path in node_tests + python_tests:
        command = (["node", "--experimental-vm-modules", "--import", str(FIXTURES / "no-network.mjs"), str(path)] if path.suffix == ".mjs" else ["python3", str(FIXTURES / "no-network.py"), str(path)])
        result = subprocess.run(command, env=env, cwd=REPO, capture_output=True, text=True, timeout=60)
        print(("PASS " if result.returncode == 0 else "FAIL ") + str(path.relative_to(REPO)), flush=True)
        if result.returncode:
            failures.append(str(path.relative_to(REPO)))
            print(result.stdout[-3000:] + result.stderr[-3000:])
print(f"{len(node_tests) + len(python_tests)} standalone test entries; {len(failures)} failures")
raise SystemExit(bool(failures))
