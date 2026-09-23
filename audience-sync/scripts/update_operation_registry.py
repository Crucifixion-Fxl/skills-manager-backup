"""Generate or verify the reviewable registry from bundled OpenAPI."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from audience_sync.allowlist import operation_registry_document  # noqa: E402
from audience_sync.project_operations import (  # noqa: E402
    PERSONAL_KEY_OPERATION_SPECS,
    PROJECT_OPERATION_SPECS,
)

REGISTRY_PATH = ROOT / "contracts" / "operation-registry.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    project = operation_registry_document(
        {operation.value: spec for operation, spec in {
            **PROJECT_OPERATION_SPECS, **PERSONAL_KEY_OPERATION_SPECS
        }.items()}
    )
    project["contract_revision"] = "audience-project-v3"
    project_path = ROOT / "contracts" / "project-operation-registry.json"
    project_expected = json.dumps(project, indent=2) + "\n"
    expected = json.dumps(operation_registry_document(), indent=2) + "\n"
    if arguments.check:
        try:
            actual = REGISTRY_PATH.read_text(encoding="utf-8")
        except OSError:
            print("operation_registry_missing")
            return 1
        if (
            actual != expected
            or not project_path.exists()
            or project_path.read_text() != project_expected
        ):
            print("operation_registry_drift")
            return 1
        print("operation_registry_ok")
        return 0
    REGISTRY_PATH.write_text(expected, encoding="utf-8")
    project_path.write_text(project_expected, encoding="utf-8")
    print("operation_registry_updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
