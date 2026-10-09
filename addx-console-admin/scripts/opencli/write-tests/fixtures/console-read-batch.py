"""Synthetic preflight fixture only; no private input or remote transport implementation."""
import importlib.util
import json
from pathlib import Path
import sys

root = Path(__file__).parents[2]
spec = importlib.util.spec_from_file_location("checkout_acceptance_policy", root / "acceptance-remote.py")
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)
plan = json.loads(Path(sys.argv[1]).read_text())
policy.validate_acceptance_policy({
    "files": {name: (root / "addx-console" / name).read_text() for name in plan["files"]},
    "commands": plan["commands"],
})
policy.validate_acceptance_namespace(plan.get("site"))
print("READY_FOR_PRIVATE_INPUT")
