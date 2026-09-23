"""Generate or verify the canonical repository's deterministic source lock."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from audience_sync.openapi_lineage import verify_upstream_lineage  # noqa: E402

LOCK_PATH = ROOT / "contracts" / "source.json"
OPENAPI_LOCK_PATH = "contracts/audience-sync-v2.openapi.json"
PROJECT_UPSTREAM = {
    "repository": "services/audiences",
    "revision": "d40108fe69eb5eeaa6879ca4e9aee7f513aa6e2e",
    "path": "audience-workflow/api/project-control-plane.openapi.json",
    "sha256": "7ea8ccad8e98135279e2979d6fe5b2c4ad0021be126053a87fc287d11e69d675",
}
OPENAPI_UPSTREAM = {
    "repository": "services/audiences",
    "revision": "53097e58faf98280e08560ef7361a5e1af3b140c",
    "path": "audience-workflow/api/audience-sync-v2.openapi.json",
    "sha256": "a1720465b316b19158bcffb4e4525af5e522762868a98db79c5c062d168c0f33",
}
OPENAPI_BUNDLE = {
    "sha256": "3382b67e825563282aec68bf9a20db7f4e21667e7e50ee3f8fd7d0475c26e4fe",
    "derived_from_upstream_sha256": OPENAPI_UPSTREAM["sha256"],
    "transformation": "remove_local_effect_policy_metadata",
    "removed_policy_markers": [
        "info.description default-dark qualifier",
        "x-audience-sync-effects-default",
    ],
}
DECLARED_FILES = (
    "docs/plans/2026-09-14-profile-user-aggregates.md",
    "tests/test_project_aggregates.py",
    "docs/plans/2026-09-11-business-results-and-installed-client.md",
    ".gitignore",
    ".gitlab-ci.yml",
    "MANIFEST.in",
    "README.md",
    "SKILL.md",
    "agents/openai.yaml",
    "contracts/audience-sync-v2.openapi.json",
    "contracts/operation-registry.json",
    "contracts/semantic-owner.json",
    "pyproject.toml",
    "references/confirmations.md",
    "references/audience-filter.md",
    "references/audience-profile-selection.md",
    "references/errors-and-recovery.md",
    "references/host-configuration.md",
    "references/platform-api.md",
    "scripts/api.py",
    "scripts/legacy_api.py",
    "scripts/check_secrets.py",
    "scripts/preflight.py",
    "scripts/tdd_audience_journey.py",
    "scripts/update_source_lock.py",
    "scripts/update_operation_registry.py",
    "setup.py",
    "src/audience_sync/__init__.py",
    "src/audience_sync/allowlist.py",
    "src/audience_sync/cli.py",
    "src/audience_sync/legacy_cli.py",
    "src/audience_sync/key_configuration.py",
    "src/audience_sync/client.py",
    "src/audience_sync/contract_validation.py",
    "src/audience_sync/operations.py",
    "src/audience_sync/openapi_lineage.py",
    "src/audience_sync/provenance.py",
    "src/audience_sync/py.typed",
    "tests/test_cli_and_preflight.py",
    "tests/test_allowlist.py",
    "tests/test_client.py",
    "tests/test_contract_validation.py",
    "tests/test_contracts.py",
    "tests/fixtures/guide-zh-CN.json",
    "tests/fixtures/client-cases.json",
    "tests/test_provenance_negative.py",
    "tests/test_secret_check.py",
    "tests/test_redirects.py",
    "tests/test_token_only_journey.py",
    "contracts/project-control-plane.openapi.json",
    "contracts/project-operation-registry.json",
    "references/project-query.md",
    "references/project-query-whitelist.md",
    "docs/plans/project-query-client.md",
    "docs/plans/2026-09-11-chinese-skill-body.md",
    "docs/plans/2026-09-11-contribution-compatibility.md",
    "docs/plans/2026-09-11-project-key-secret-scan.md",
    "docs/plans/2026-09-11-operation-scope-redirect-test.md",
    "docs/plans/2026-09-09-project-human-links.md",
    "docs/plans/2026-09-09-publishing-cold-start-delivery.md",
    "evals/evals.json",
    "src/audience_sync/project_operations.py",
    "src/audience_sync/project_query.py",
    "tests/test_project_query.py",
    "tests/test_project_criteria_readback.py",
    "tests/test_project_human_links.py",
    "tests/test_project_discovery.py",
    "tests/test_personal_key_context.py",
    "tests/test_project_query_whitelist.py",
    "scripts/check_project_query_whitelist.py",
    "scripts/tdd_project_query_journey.py",
)


def expected_lock() -> dict[str, object]:
    owner = json.loads((ROOT / "contracts/semantic-owner.json").read_text(encoding="utf-8"))
    if (
        not isinstance(owner, dict)
        or set(owner) != {"repository", "origin", "availability"}
        or any(not isinstance(value, str) or not value.strip() for value in owner.values())
    ):
        raise ValueError("invalid_semantic_owner")
    files = {
        relative: hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        for relative in DECLARED_FILES
    }
    bundle_sha256 = verify_upstream_lineage(
        (ROOT / OPENAPI_LOCK_PATH).read_bytes(), OPENAPI_UPSTREAM["sha256"]
    )
    if bundle_sha256 != OPENAPI_BUNDLE["sha256"]:
        raise ValueError("openapi_bundle_hash_mismatch")
    if files["contracts/project-control-plane.openapi.json"] != PROJECT_UPSTREAM["sha256"]:
        raise ValueError("project_openapi_hash_mismatch")
    return {
        "schema_version": 1,
        "semantic_owner": owner,
        "contract_source": {
            "repository": "lli/audience-sync-skill",
            "release": "0.8.1",
            "contract_revision": "audience-sync-v2",
            "availability": "published_operations",
        },
        "openapi_upstream": OPENAPI_UPSTREAM,
        "openapi_bundle": OPENAPI_BUNDLE,
        "project_openapi_upstream": PROJECT_UPSTREAM,
        "files": files,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    try:
        expected = json.dumps(expected_lock(), indent=2, sort_keys=True) + "\n"
    except (OSError, UnicodeError, ValueError):
        print("source_lock_generation_failed")
        return 1
    if arguments.check:
        try:
            actual = LOCK_PATH.read_text(encoding="utf-8")
        except OSError:
            print("source_lock_missing")
            return 1
        if actual != expected:
            print("source_lock_drift")
            return 1
        print("source_lock_ok")
        return 0
    LOCK_PATH.write_text(expected, encoding="utf-8")
    print("source_lock_updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
