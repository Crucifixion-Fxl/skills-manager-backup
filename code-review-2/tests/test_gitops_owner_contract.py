"""Consumer data contract; central handler tests own the actual authorization."""

import json
import re
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
REFS = SKILL / "references"


def test_independent_contract_does_not_grant_old_identity_or_raw_source():
    assert json.loads((REFS / "gitops-owner-context-contract.json").read_text()) == {
        "schema_version": 1,
        "capability": "gitops-owner-fixed-context/v1",
        "tool": "read_review_source",
        "operation": "gitops-owner",
    }
    assert json.loads((REFS / "gitops-identity-context-contract.json").read_text())[
        "capability"
    ] != ("gitops-owner-fixed-context/v1")


def test_usable_request_is_bounded_to_the_single_identity_channel():
    guide = (REFS / "gitops-owner-context.md").read_text()
    examples = re.findall(r"```json\n(.*?)\n```", guide, re.DOTALL)
    assert examples
    for raw in examples:
        assert json.loads(raw) == {
            "operation": "gitops-owner",
            "source": "streampark-access",
            "revision": "head",
            "path": "clusters/aws-390709477306-us-staging/streampark-access/identity.yaml",
        }


def test_success_evidence_is_actual_fixed_git_not_author_snapshot():
    guide = (REFS / "gitops-owner-context.md").read_text()
    for required in [
        "1146",
        "2810",
        "419",
        "297ddbc797c9ddb611d24874894cfc6edaa2c1fb",
        "171736f6bb1ddb49ba721438372e413f4d95ba66",
        "parent_head_sha",
        "commit_sha",
        "git_blob_oid",
        "sha256",
        "closure",
        "documents",
        "source-events.jsonl",
        "作者快照不是权威",
        "默认关闭",
        "incomplete",
    ]:
        assert required in guide


def test_limits_and_postdeploy_gates_are_explicit():
    guide = (REFS / "gitops-owner-context.md").read_text()
    for required in [
        "16",
        "32",
        "8 MiB",
        "64 KiB",
        "file/tree/search",
        "不执行脚本",
        "不渲染",
        "Role-only",
        "正负权限",
    ]:
        assert required in guide


def test_main_review_and_coverage_contracts_reach_the_new_independent_contract():
    for name in [
        "SKILL.md",
        "references/review-data-contract.md",
        "references/behavior-coverage-review.md",
    ]:
        assert "gitops-owner-context.md" in (SKILL / name).read_text()
