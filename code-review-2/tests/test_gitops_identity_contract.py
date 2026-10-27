"""Consumer contract checks; runtime admission belongs to the central reader.

Parse shipped negotiation/identity tables and requests as data. These tests must
not implement a second authorizer or claim to execute the central security policy.
"""

import json
import re
from pathlib import Path

import pytest
import yaml

SKILL = Path(__file__).resolve().parents[1]
REPO = SKILL.parents[2]
REFERENCES = SKILL / "references"
GUIDE = REFERENCES / "gitops-identity-context.md"
IDENTITY_PATH = "clusters/aws-390709477306-us-staging/streampark-access/identity.yaml"
EXPECTED_IDENTITIES = {
    ("v1", "Namespace", "staging-streampark-jobs", "无"),
    ("v1", "Namespace", "streampark-access-system", "无"),
    ("v1", "ServiceAccount", "streampark-submitter", "staging-streampark-jobs"),
    ("v1", "ServiceAccount", "streampark-flink", "staging-streampark-jobs"),
    ("v1", "ServiceAccount", "streampark-access-publisher", "streampark-access-system"),
    *{
        ("rbac.authorization.k8s.io/v1", kind, name, "staging-streampark-jobs")
        for kind in ("Role", "RoleBinding")
        for name in ("streampark-submitter", "streampark-flink", "streampark-access-publisher")
    },
}


def test_capability_has_exact_independent_trust_contract():
    contracts = {
        "source-context": "fixed-source-context/v1",
        "diff-context": "diff-scoped-context/v1",
        "k8s-yaml-context": "k8s-yaml-context/v1",
        "k8s-workload-metadata": "k8s-workload-metadata/v1",
        "gitops-identity-context": "gitops-identity-context/v1",
    }
    for stem, capability in contracts.items():
        assert json.loads((REFERENCES / f"{stem}-contract.json").read_text()) == {
            "schema_version": 1,
            "capability": capability,
            "tool": "read_review_source",
        }
    assert len(set(contracts.values())) == len(contracts)


def test_realistic_requests_only_target_the_fixed_identity_channel():
    examples = re.findall(r"```json\n(.*?)\n```", GUIDE.read_text(), re.DOTALL)
    assert examples, "A consumer needs a usable bounded request"
    for example in examples:
        request = json.loads(example)
        assert request == {
            "operation": "gitops-identity",
            "source": "root",
            "revision": "head",
            "path": IDENTITY_PATH,
        }
    assert "revision=base|head" in GUIDE.read_text()
    assert "只把 revision 改为 base" in GUIDE.read_text()


def test_documented_supported_identity_set_has_no_omissions_or_extra_objects():
    # Four-column rows are the public supported identity inventory, not YAML.
    rows = [
        tuple(cell.strip() for cell in line.strip("|").split("|"))
        for line in GUIDE.read_text().splitlines()
        if line.startswith(("| v1 |", "| rbac.authorization.k8s.io/v1 |"))
    ]
    assert len(rows) == 11
    assert len(set(rows)) == len(rows)
    assert set(rows) == EXPECTED_IDENTITIES


def test_all_four_combinations_remain_fail_closed_except_fully_negotiated():
    guide = GUIDE.read_text()
    rows = dict(
        tuple(cell.strip() for cell in line.strip("|").split("|"))
        for line in guide.splitlines()
        if re.match(r"\| [旧新]技能 \+ [旧新]工具 \|", line)
    )
    assert rows == {
        "旧技能 + 旧工具": "不授权新通道，保留原权限",
        "旧技能 + 新工具": "缺少独立契约，不授权新通道",
        "新技能 + 旧工具": "无匹配能力，不尝试新操作",
        "新技能 + 新工具": "全部条件确认后仅使用本通道",
    }
    for term in (
        "source-context-contract.json", "gitops-identity-context-contract.json",
        "CODE_REVIEW_GITOPS_IDENTITY_CONTEXT_ENABLED", "设为 false", "双端匹配",
        "实际工具列表", "操作枚举明确广告", "source-context.json", "capabilities",
        "project_id=419", "可信 meta", "meta.files", "固定 base/head",
        "缺字段、未知版本、身份不一致", "默认关闭", "回退任一侧",
    ):
        assert term in guide, term


@pytest.mark.parametrize("term", [
    "k8s-yaml-context/v1", "diff-scoped-context/v1", "k8s-workload-metadata/v1",
    "不授予本能力", "本能力也不授予", "旧 `file/search`", "read-source-file.sh",
    "均不扩大", "MR 文本、作者夹具、Agent 声明", "Git/API/网络",
    "不执行 Kustomize/Helm", "不跟随资源引用",
])
def test_independent_channel_does_not_grant_legacy_or_execution_permissions(term):
    assert term in GUIDE.read_text()


@pytest.mark.parametrize("term", [
    "256 KiB", "16 个文档", "32 层", "16384", "16 个对象版本", "8 MiB", "32 个窗口",
    "64 KiB", "普通 UTF-8 Git blob", "整个", "重复 key", "自定义 tag", "anchor",
    "alias", "merge key", "非字符串键", "NUL/控制字符", "未知 schema/字段",
    "非准入/遗漏/重复/额外身份对象", "Secret", "凭据", "私钥", "kubeconfig",
    "敏感值", "符号链接", "gitlink", "路径穿越", "任意 SHA", "行窗口", "分页",
    "不静默过滤字段", "不是不存在", "不评分、不假通过", "UNKNOWN", "incomplete",
])
def test_refusals_and_shared_budgets_are_explicit_without_reimplementing_policy(term):
    assert term in GUIDE.read_text()


def test_output_is_complete_semantics_with_fixed_provenance_and_runtime_limits():
    guide = GUIDE.read_text()
    for term in (
        "status=ok", "project_id", "parent_head_sha", "source", "revision", "commit_sha",
        "blob_sha", "size", "path", "operation", "schema_version=1",
        "coverage=complete_supported_identity_objects", "documents", "完整保留上述11个",
        "effective_configuration_resolved=false", "labels", "automountServiceAccountToken",
        "eks.amazonaws.com/role-arn", "rules", "roleRef", "subjects", "跨 namespace",
        "不补 Kubernetes 默认值", "不返回任意标量、注释", "不返回任何原 YAML 窗口",
        "不写入 `source-events.jsonl`", "metadata 事件仍可记录", "被拒正文不得进入",
        "在线 RBAC", "IRSA 生效证明", "不授权关闭 Task3 或 Root1",
    ):
        assert term in guide, term


@pytest.mark.parametrize("filename", [
    "SKILL.md", "references/ci-integration.md", "references/review-data-contract.md",
    "references/behavior-coverage-review.md", "references/diff-scoped-review.md",
])
def test_consumer_routes_resolve_to_the_shipped_reference(filename):
    source = SKILL / filename
    links = re.findall(r"\]\(([^)]*gitops-identity-context\.md)\)", source.read_text())
    assert links, filename
    assert all((source.parent / link).resolve() == GUIDE.resolve() for link in links)


def test_required_ci_job_runs_the_new_and_existing_context_contracts():
    ci = yaml.safe_load((REPO / ".gitlab/ci/code-review-policy-contract.yml").read_text())
    scripts = ci["code-review:policy-contract"]["script"]
    assert "uv run --extra dev pytest skills/quality/code-review/tests -q" in scripts
    for filename in (
        "test_gitops_identity_contract.py", "test_k8s_yaml_contract.py",
        "test_workload_metadata_contract.py",
    ):
        assert (SKILL / "tests" / filename).is_file()


def test_only_explicit_fixed_tree_absence_is_absent_evidence():
    guide = GUIDE.read_text()
    assert "固定 base/head 树明确查无唯一文件" in guide
    assert "`absent/ABSENT_IN_FIXED_TREE`" in guide
    assert "不支持结构、预算、权限或对象不完整等仍为 UNKNOWN" in guide
    assert "不能据此宣称对象不存在" in guide
