"""Independent Runner evidence authority; no legacy permission migration."""

import json
import re
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]


def test_runner_contract_and_precise_request():
    assert json.loads(
        (SKILL / "references/runner-selector-contract.json").read_text()
    ) == {
        "schema_version": 1,
        "capability": "runner-selector/v1",
        "tool": "read_review_source",
        "operation": "runner-selector",
        "flag": "CODE_REVIEW_RUNNER_SELECTOR_ENABLED",
    }
    guide = (SKILL / "references/runner-selector.md").read_text()
    example = json.loads(re.search(r"```json\n(.*?)\n```", guide, re.DOTALL)[1])
    assert set(example) == {"operation", "source", "revision", "path"}
    assert example["source"] == "root" and example["revision"] == "base"
    assert (
        example["path"]
        == "clusters/aws-125710977284-sg-devops/cicd/gitlab-runner/values-override-amd64-ultra.yaml"
    )
    assert "runner-selector.md" in (SKILL / "SKILL.md").read_text()
    literal = re.search(r"`(\^karpenter[^`]+)`", guide)[1]
    assert literal == r"^karpenter\.sh/nodepool=gitlab-runner-ultra-ondemand$"
    for term in (
        "UNKNOWN",
        "incomplete",
        "不执行",
        "不扩大",
        "effective_configuration_resolved=false",
        "status=absent",
        "固定 base/head",
        "不表示最终 Helm",
    ):
        assert term in guide


def test_legacy_descriptors_unchanged_in_scope():
    for name, capability in [
        ("source-context-contract.json", "fixed-source-context/v1"),
        ("k8s-yaml-context-contract.json", "k8s-yaml-context/v1"),
        ("diff-context-contract.json", "diff-scoped-context/v1"),
        ("k8s-workload-metadata-contract.json", "k8s-workload-metadata/v1"),
        ("k8s-placement-contract.json", "k8s-placement/v1"),
        ("gitops-identity-context-contract.json", "gitops-identity-context/v1"),
        ("ci-resources-contract.json", "ci-resources/v1"),
    ]:
        expected = {
            "schema_version": 1,
            "capability": capability,
            "tool": "read_review_source",
        }
        if name == "k8s-placement-contract.json":
            expected.update(
                operation="k8s-placement", flag="CODE_REVIEW_K8S_PLACEMENT_ENABLED"
            )
        assert json.loads((SKILL / "references" / name).read_text()) == expected
