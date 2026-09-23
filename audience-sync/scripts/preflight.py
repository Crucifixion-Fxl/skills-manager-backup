"""Offline preflight for config, published contract, and provenance."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from audience_sync import (  # noqa: E402
    CONTRACT_REVISION,
    CONTRACT_STATE,
    AudienceSyncClient,
    AudienceSyncConfig,
    SafeApiError,
)
from audience_sync.operations import Operation  # noqa: E402
from audience_sync.project_operations import (  # noqa: E402
    PROJECT_CONTRACT_REVISION,
    PersonalKeyOperation,
    ProjectOperation,
)
from audience_sync.provenance import ProvenanceError, load_and_verify  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    family = parser.add_mutually_exclusive_group()
    family.add_argument("--project", action="store_true", help="require Project configuration")
    family.add_argument("--legacy", action="store_true", help=argparse.SUPPRESS)
    family.add_argument(
        "--personal-key", action="store_true", help="check self-context configuration"
    )
    parser.add_argument("--key-alias")
    arguments = parser.parse_args()
    try:
        source = load_and_verify(ROOT)
        config = AudienceSyncConfig.from_environment(
            key_alias=arguments.key_alias, project_operation=not arguments.legacy
        )
        client = AudienceSyncClient(config)
        project = not arguments.legacy
        if arguments.legacy:
            operation = Operation.GET_SYNC_CAPABILITIES
        elif arguments.project:
            operation = ProjectOperation.GET_QUERY_CAPABILITIES
        else:
            operation = PersonalKeyOperation.GET_PROJECT_PERSONAL_KEY_CONTEXT
        client.validate_configuration(operation)
        contract_source = source["contract_source"]
        if (
            contract_source.get("contract_revision") != CONTRACT_REVISION
            or contract_source.get("availability") != CONTRACT_STATE
        ):
            raise SafeApiError("contract_revision_mismatch")
    except (ProvenanceError, SafeApiError, ValueError) as exc:
        print(f"preflight_failed:{getattr(exc, 'code', str(exc))}")
        return 1
    result = PROJECT_CONTRACT_REVISION if project else CONTRACT_STATE
    print(f"preflight_ok:{result}")
    print(json.dumps({
        "validation": "offline",
        "authentication": "not_attempted",
        "credential_family": "awpk_v2" if isinstance(
            operation, (ProjectOperation, PersonalKeyOperation)
        ) else "awpk_v1",
        "api_contract_revision": config.contract_revision,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
