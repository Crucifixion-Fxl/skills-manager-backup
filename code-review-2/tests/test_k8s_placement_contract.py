import json
import re
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
REFERENCES = SKILL / "references"
GUIDE = REFERENCES / "k8s-placement.md"


def test_placement_descriptor_is_independent_and_legacy_descriptors_stay_exact():
    assert json.loads((REFERENCES / "k8s-placement-contract.json").read_text()) == {
        "schema_version": 1,
        "capability": "k8s-placement/v1",
        "tool": "read_review_source",
        "operation": "k8s-placement",
        "flag": "CODE_REVIEW_K8S_PLACEMENT_ENABLED",
    }
    for name, capability in (
        ("k8s-yaml-context-contract.json", "k8s-yaml-context/v1"),
        ("k8s-workload-metadata-contract.json", "k8s-workload-metadata/v1"),
    ):
        assert json.loads((REFERENCES / name).read_text()) == {
            "schema_version": 1,
            "capability": capability,
            "tool": "read_review_source",
        }


def test_guide_is_linked_from_skill_and_relevant_k8s_entrypoints():
    for name in (
        "SKILL.md",
        "references/ci-integration.md",
        "references/k8s-yaml-context.md",
    ):
        assert "k8s-placement.md" in (SKILL / name).read_text()


def test_request_shape_and_reader_vs_reviewer_boundaries():
    guide = GUIDE.read_text()
    examples = re.findall(r"```json\n(.*?)\n```", guide, re.DOTALL)
    assert len(examples) == 1
    request = json.loads(examples[0])
    assert set(request) == {"operation", "source", "revision", "path"}
    assert request["operation"] == "k8s-placement" and request["source"] == "root"
    assert request["revision"] in {"base", "head"}
    assert request["path"].startswith("k8s/") and request["path"].endswith((".yaml", ".yml"))
    assert "不判断路径" in guide and "不跟随引用" in guide
    assert "规范化为 root `k8s/` 内的实际路径" in guide
    assert "不得递归或批量遍历引用" in guide


def test_projection_schema_keeps_declarations_and_unknowns_explicit():
    guide = GUIDE.read_text()
    for field in (
        "schema_version=1",
        'coverage="declarations_only"',
        "effective_configuration_resolved=false",
        "metadata_name",
        "node_selector_present",
        "node_selector",
        "declared_resources",
        "other_configuration_present",
        "placement_unknown",
        "placement_classification=\"unknown\"",
        "RFC6902Patch",
    ):
        assert field in guide
    assert "helmCharts" in guide and "vars" in guide and "strategic merge" in guide
    assert "不得渲染 Kustomize" in guide and "推断 Pod 会被调度到某节点" in guide
    assert all(term in guide for term in ("SENSITIVE_CONTENT", "UNSUPPORTED_K8S_PLACEMENT"))
