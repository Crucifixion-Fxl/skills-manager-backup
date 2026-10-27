"""Negotiation, bounded output and safe comparison guidance for workload evidence."""

import hashlib
import json
import re
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1]
REFERENCES = SKILL / "references"
GUIDE = REFERENCES / "k8s-workload-metadata.md"
POLICY_REVISION = "6578630c0ff5e0944c7e0894d67724d36e801354"
POLICY_CASES = (
    ("app", "example-api", None),
    ("env", "prod-eu", None),
    ("example.com/component", "api", None),
    ("password", "example", "SENSITIVE_CONTENT"),
    ("passwd", "example", "SENSITIVE_CONTENT"),
    ("secret", "example", "SENSITIVE_CONTENT"),
    ("token", "example", "SENSITIVE_CONTENT"),
    ("apikey", "example", "SENSITIVE_CONTENT"),
    ("authorization", "example", "SENSITIVE_CONTENT"),
    ("credential", "example", "SENSITIVE_CONTENT"),
    ("privatekey", "example", "SENSITIVE_CONTENT"),
    ("accesskey", "example", "SENSITIVE_CONTENT"),
    ("ApI_Key", "example", "SENSITIVE_CONTENT"),
    ("app", "prod-s.e_c-r_e-t", "SENSITIVE_CONTENT"),
)


def test_digest_verification_never_spawns_a_child(monkeypatch):
    def reject_child(*args, **kwargs):
        raise AssertionError("Digest verification must not execute Markdown")

    monkeypatch.setattr("subprocess.run", reject_child)
    test_documented_literal_digest_uses_the_projector_serialization()


def test_sensitive_label_policy_has_an_immutable_central_source():
    guide = GUIDE.read_text()
    assert (
        f"/-/blob/{POLICY_REVISION}/.gitlab-ci/scripts/code-review/"
        "project-workload-metadata.js#L49"
    ) in guide
    for term in ("ASCII", "不区分大小写", "不重新实现", "SENSITIVE_CONTENT"):
        assert term in guide


@pytest.mark.parametrize(
    "key,value,reason",
    POLICY_CASES,
    ids=[f"label-{n}" for n in range(len(POLICY_CASES))],
)
def test_sensitive_label_policy_examples_match_the_guide(key, value, reason):
    assert f"| `{key}` | `{value}` | {reason or 'allowed'} |" in GUIDE.read_text()


def test_metadata_contract_is_exact_and_independent():
    for filename, capability in (
        ("source-context-contract.json", "fixed-source-context/v1"),
        ("k8s-yaml-context-contract.json", "k8s-yaml-context/v1"),
        ("k8s-workload-metadata-contract.json", "k8s-workload-metadata/v1"),
    ):
        assert json.loads((REFERENCES / filename).read_text()) == {
            "schema_version": 1,
            "capability": capability,
            "tool": "read_review_source",
        }


def test_metadata_guide_is_reachable_from_affected_entrypoints():
    for filename in (
        "SKILL.md",
        "references/ci-integration.md",
        "references/review-data-contract.md",
        "references/behavior-coverage-review.md",
        "references/diff-scoped-review.md",
        "references/k8s-yaml-context.md",
    ):
        assert "k8s-workload-metadata.md" in (SKILL / filename).read_text(), filename


def test_metadata_guide_requires_live_negotiation_and_preserves_old_permissions():
    guide = GUIDE.read_text()
    for required in (
        "source-context-contract.json",
        "k8s-workload-metadata-contract.json",
        "fixed-source-context/v1",
        "source-context.json",
        "capabilities",
        "工具列表",
        "项目",
        "base/head",
        "旧技能 + 旧工具",
        "旧技能 + 新工具",
        "新技能 + 旧工具",
        "新技能 + 新工具",
        "不得自行",
        "file/search",
        "read-source-file.sh",
        "不扩大",
        "不依赖",
        "k8s-yaml-context/v1",
    ):
        assert required in guide, required


def test_metadata_scope_output_and_budgets_are_explicit():
    guide = GUIDE.read_text()
    for required in (
        "source=root",
        "revision=base|head",
        "k8s/",
        ".yaml",
        ".yml",
        "Deployment",
        "apps/v1",
        "Rollout",
        "argoproj.io/v1alpha1",
        "256 KiB",
        "单文档",
        "32 层",
        "16384",
        "128",
        "1000000",
        "16 个对象版本",
        "8 MiB",
        "32 个窗口",
        "null",
        "不补默认值",
        "literal_workload_metadata_only",
        "pod_label_sha256",
        "sha256-json-string-pair-v1",
        "effective_configuration_resolved=false",
        "parent_head_sha",
        "commit_sha",
        "blob_sha",
        "不返回",
        "Secret",
        "符号链接",
        "子仓",
        "不执行 Kustomize",
    ):
        assert required in guide, required


def test_comparison_and_failure_guidance_do_not_claim_runtime_acceptance():
    guide = GUIDE.read_text()
    for required in (
        "diff-first",
        "已准入",
        "matchLabels",
        "matchExpressions",
        "摘要",
        "不是匿名化",
        "猜测",
        "不得",
        "敏感",
        "overlay",
        "UNKNOWN",
        "incomplete",
        "CAPABILITY_UNAVAILABLE",
        "不是不存在",
        "必要证据",
        "可选上下文",
        "不得主动额外公开",
        "不重跑",
        "!705",
        "生产",
        "单一发布器",
        "不得执行",
    ):
        assert required in guide, required


def test_request_examples_do_not_request_raw_windows_or_other_permissions():
    examples = re.findall(r"```json\n(.*?)\n```", GUIDE.read_text(), re.DOTALL)
    assert examples
    for example in examples:
        request = json.loads(example)
        assert set(request) == {"source", "revision", "path", "operation"}
        assert request["source"] == "root"
        assert request["revision"] in {"base", "head"}
        assert request["path"].startswith("k8s/")
        assert request["path"].endswith((".yaml", ".yml"))
        assert request["operation"] == "workload-metadata"


def test_existing_diagnostic_artifacts_are_not_claimed_to_be_newly_isolated():
    guide = GUIDE.read_text()
    for required in ("source-events.jsonl", "含摘要", "2天", "不改变", "主动额外"):
        assert required in guide, required


def test_documented_literal_digest_uses_the_projector_serialization():
    examples = re.findall(r"```javascript\n(.*?)\n```", GUIDE.read_text(), re.DOTALL)
    # Markdown is data: compare the fixed example, never execute it.
    assert examples == [
        (
            'const { createHash } = require("node:crypto");\n'
            'const pair = ["app", "example-api"];\n'
            'console.log(createHash("sha256").update(JSON.stringify(pair), "utf8").digest("hex"));'
        )
    ]
    pair = json.dumps(["app", "example-api"], ensure_ascii=False, separators=(",", ":"))
    assert hashlib.sha256(pair.encode("utf-8")).hexdigest() == (
        "cf31165c07be8f8d112f8ca3325a506b5585deaaca446b18bc9fb8a9639e9666"
    )
