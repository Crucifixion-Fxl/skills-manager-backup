"""Consumer data contract; central reader, not this test, owns authorization."""

import json
import re
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1]
GUIDE = SKILL / "references/builder-static-context.md"


def test_independent_negotiation_contract():
    assert json.loads((SKILL / "references/builder-static-context-contract.json").read_text()) == {
        "schema_version": 1,
        "capability": "builder-static-context/v1",
        "tool": "read_review_source",
        "operation": "builder-capacity",
        "artifact": "builder-static-context.json",
        "flag": "CODE_REVIEW_BUILDER_STATIC_CONTEXT_ENABLED",
    }


def test_only_exact_usable_request_is_documented():
    examples = re.findall(r"```json\n(.*?)\n```", GUIDE.read_text(), re.DOTALL)
    assert examples
    for example in examples:
        assert json.loads(example) == {
            "operation": "builder-capacity",
            "source": "root",
            "revision": "head",
            "path": "clusters/aws-390709477306-us-staging/streampark-builder/capacity/nodepool.yaml",
        }


@pytest.mark.parametrize("term", [
    "默认关闭", "设为 false", "双端匹配", "实际工具列表", "capabilities",
    "旧技能 + 新工具", "新技能 + 旧工具", "旧 `file/search`", "no-op diff",
    "UNKNOWN", "incomplete", "不返回原 YAML", "documents 不进入 source-events.jsonl",
    "argo_project_id=1146", "直接子文件", "owners=[] 不表示全仓/集群无 owner",
    "dynamic_ownership_unknown", "pending_activation", "无需集群凭据", "真实 DS UID/controller",
    "不执行 Kustomize/Helm", "不跟随资源引用", "不补默认值", "夹读 main 和 MR",
    "60秒", "无重试", "200", "4 MiB", "64 KiB", "零 disruption budget",
])
def test_boundaries_are_explicit_without_second_authorizer(term):
    assert term in GUIDE.read_text()


def test_skill_routes_to_shipped_reference():
    assert "[builder 静态取证契约](references/builder-static-context.md)" in (SKILL / "SKILL.md").read_text()
