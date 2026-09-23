"""Generate the deterministic fixed-operation registry from bundled contracts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from string import Formatter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from user_research.operations import OPERATION_SPECS  # noqa: E402

TARGET = ROOT / "contracts" / "operation-registry.json"


def expected() -> str:
    operations = []
    for operation, spec in sorted(OPERATION_SPECS.items(), key=lambda item: item[0].value):
        path_parameters = sorted(
            name for _, name, _, _ in Formatter().parse(spec.path) if name is not None
        )
        operations.append(
            {
                "method": spec.method,
                "name": operation.value,
                "path": spec.path,
                "path_parameters": path_parameters,
                "query_parameters": sorted(spec.query),
            }
        )
    return (
        json.dumps(
            {"contract_revision": "user-research-project-v3", "operations": operations},
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    content = expected()
    if arguments.check:
        try:
            current = TARGET.read_text(encoding="utf-8")
        except OSError:
            print("operation_registry_missing")
            return 1
        if current != content:
            print("operation_registry_drift")
            return 1
        print("operation_registry_ok")
        return 0
    TARGET.write_text(content, encoding="utf-8")
    print("operation_registry_updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
