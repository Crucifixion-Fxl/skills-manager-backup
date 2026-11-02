import json
import re
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
REFERENCES = SKILL / "references"
GUIDE = REFERENCES / "k8s-secret-refs.md"


def test_secret_refs_descriptor_matches_template_negotiation():
    # engineering/ci-templates setup-result-submission.sh enables the flag only for
    # schema_version=1, capability=k8s-secret-refs/v1 and tool=read_review_source.
    assert json.loads((REFERENCES / "k8s-secret-refs-contract.json").read_text()) == {
        "schema_version": 1,
        "capability": "k8s-secret-refs/v1",
        "tool": "read_review_source",
        "operation": "secret-refs",
        "flag": "CODE_REVIEW_K8S_SECRET_REFS_ENABLED",
    }


def test_guide_is_linked_from_skill_and_relevant_k8s_entrypoints():
    for name in (
        "references/step-0-intake.md",
        "references/ci-integration.md",
        "references/k8s-yaml-context.md",
    ):
        assert "k8s-secret-refs.md" in (SKILL / name).read_text()


def test_request_shape_and_reader_vs_reviewer_boundaries():
    guide = GUIDE.read_text()
    examples = re.findall(r"```json\n(.*?)\n```", guide, re.DOTALL)
    assert len(examples) == 1
    request = json.loads(examples[0])
    assert set(request) == {"operation", "source", "revision", "path"}
    assert request["operation"] == "secret-refs" and request["source"] == "root"
    assert request["revision"] in {"base", "head"}
    assert request["path"].startswith("k8s/") and request["path"].endswith((".yaml", ".yml"))
    assert "不跟随引用" in guide and "不得递归或批量遍历引用" in guide


def test_projection_never_promises_values_or_effective_state():
    guide = GUIDE.read_text()
    for term in (
        "schema_version=1",
        'coverage="declarations_only"',
        "effective_configuration_resolved=false",
        "sync_wave",
        "env_from",
        "secret_env",
        "value_projected=false",
        "path_sha256",
        '{"sha256": ...}',
        "永不输出",
        "不渲染 Kustomize",
        "不证明 Secret 在集群中存在",
    ):
        assert term in guide
    assert all(
        term in guide
        for term in ("SENSITIVE_CONTENT", "K8S_SECRET_REFS_UNSAFE", "UNSUPPORTED_K8S_SECRET_REFS")
    )
