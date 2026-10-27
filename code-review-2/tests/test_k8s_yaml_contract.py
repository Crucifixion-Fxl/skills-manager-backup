"""Contract and guidance for the independently negotiated K8s evidence channel."""

import json
import re
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
REFERENCES = SKILL / "references"
GUIDE = REFERENCES / "k8s-yaml-context.md"


def test_k8s_capability_is_separate_from_existing_permissions():
    for filename, capability in (
        ("source-context-contract.json", "fixed-source-context/v1"),
        ("diff-context-contract.json", "diff-scoped-context/v1"),
        ("k8s-yaml-context-contract.json", "k8s-yaml-context/v1"),
    ):
        assert json.loads((REFERENCES / filename).read_text()) == {
            "schema_version": 1,
            "capability": capability,
            "tool": "read_review_source",
        }


def test_all_affected_entrypoints_link_the_narrow_contract():
    for filename in (
        "SKILL.md",
        "references/ci-integration.md",
        "references/review-data-contract.md",
        "references/behavior-coverage-review.md",
        "references/diff-scoped-review.md",
    ):
        assert "k8s-yaml-context.md" in (SKILL / filename).read_text(), filename


def test_scope_and_budgets_match_the_central_reader():
    guide = GUIDE.read_text()
    for required in (
        "source=root",
        "revision=base|head",
        "k8s/",
        ".yaml",
        ".yml",
        "kustomize.config.k8s.io/v1beta1",
        "PodDisruptionBudget",
        "policy/v1",
        "Deployment",
        "apps/v1",
        "Rollout",
        "argoproj.io/v1alpha1",
        "256 KiB",
        "16 个文档",
        "32 层",
        "16384",
        "16 个对象版本",
        "8 MiB",
        "32 个窗口",
        "400 行",
        "64 KiB",
        "diff-first",
    ):
        assert required in guide, required


def test_admission_precedes_windows_and_does_not_promise_secret_detection():
    guide = GUIDE.read_text()
    for required in (
        "整个 blob",
        "Secret",
        "ConfigMap",
        "Helm",
        "子仓",
        "符号链接",
        "重复 key",
        "tag",
        "anchor",
        "alias",
        "JSON6902",
        "标签",
        "不得执行",
        "不跟随",
        "不是通用秘密检测保证",
        "已知含秘密",
        "CAPABILITY_UNAVAILABLE",
        "unknown",
        "incomplete",
        "不是不存在",
        "必要证据",
        "可选上下文",
        "不公开",
        "单一发布器",
    ):
        assert required in guide, required


def test_old_and_new_combinations_remain_fail_closed():
    guide = GUIDE.read_text()
    for required in (
        "source-context-contract.json",
        "k8s-yaml-context-contract.json",
        "source-context.json",
        "capabilities",
        "工具列表",
        "项目",
        "base/head",
        "旧技能 + 旧工具",
        "旧技能 + 新工具",
        "新技能 + 旧工具",
        "新技能 + 新工具",
        "read-source-file.sh",
        "不扩大",
        "单独协商",
        "search",
        "diff-scoped-context/v1",
    ):
        assert required in guide, required


def test_examples_only_use_the_negotiated_root_file_channel():
    guide = GUIDE.read_text()
    examples = re.findall(r"```json\n(.*?)\n```", guide, re.DOTALL)
    assert examples
    for example in examples:
        request = json.loads(example)
        assert request["source"] == "root"
        assert request["revision"] in {"base", "head"}
        assert request["path"].startswith("k8s/")
        assert request["path"].endswith((".yaml", ".yml"))
        assert request["operation"] == "file"
        assert 1 <= request["line_count"] <= 400
