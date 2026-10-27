"""Keep readable diff evidence bound to its scope and existing permissions."""

import json
import unittest
from pathlib import Path


SKILL = Path(__file__).resolve().parents[1]
REFERENCES = SKILL / "references"
GUIDE = REFERENCES / "diff-long-line-view.md"


class DiffLongLineViewContractTest(unittest.TestCase):
    def test_negotiation_adds_an_artifact_without_a_tool(self):
        contract = json.loads(
            (REFERENCES / "diff-long-line-view-contract.json").read_text()
        )
        self.assertEqual(
            contract,
            {
                "schema_version": 1,
                "capability": "diff-long-line-view/v1",
                "artifact": "diff-views",
            },
        )

    def test_all_entrypoints_use_the_same_contract(self):
        for filename in (
            "SKILL.md",
            "references/ci-integration.md",
            "references/review-data-contract.md",
        ):
            with self.subTest(filename=filename):
                self.assertIn("diff-long-line-view.md", (SKILL / filename).read_text())

    def test_source_and_existing_helper_boundaries_are_explicit(self):
        guide = GUIDE.read_text()
        for boundary in (
            "CODE_REVIEW_DIFF_VIEW_ENABLED", "MR 自带同名文件", "任意附件",
            'materialize-review-diff.sh "$REVIEW_DATA_DIR" <file-id>',
            "不新增工具白名单", "不增加参数变体", "不执行被审代码",
            "原始 diff 分页", "不新增源码全文权限", "不自动触发评论",
        ):
            with self.subTest(boundary=boundary):
                self.assertIn(boundary, guide)

    def test_scope_and_page_integrity_cannot_be_skipped(self):
        guide = GUIDE.read_text()
        for evidence in (
            "project_id", "base_sha", "head_sha", "file_id", "diff_sha256",
            "meta.scope.base_sha", "meta.scope.head_sha", "页列表", "页摘要",
            "顺序连续", "原始 diff 行号", "base/head 源码行号", "UTF-8",
            "身份或摘要冲突", "缺页", "重复页", "区间缺口", "页被截断",
            "pages.view", "pages.raw", "ordinal", "coverage.all_changes_present=true",
            "coverage.equal_regions_only=true", "coverage.review_completed=false",
            "status=incomplete", "index_file=null",
        ):
            with self.subTest(evidence=evidence):
                self.assertIn(evidence, guide)

    def test_omission_requires_exact_equality_and_reconstruction(self):
        guide = GUIDE.read_text()
        for invariant in (
            "可靠配对", "精确共同前缀", "共同后缀", "前后缀不得重叠",
            "仅省略已逐字节确认相等", "完整变化区间", "两侧各 120 个",
            "重构校验成功", "不能用摘要比较代替", "不做 Unicode normalization",
            "EOF/no-newline", "不凭相似度", "使用完整分页",
        ):
            with self.subTest(invariant=invariant):
                self.assertIn(invariant, guide)

    def test_generated_pages_do_not_imply_completed_review(self):
        guide = GUIDE.read_text()
        for condition in (
            "生成 complete 不等于读取或审查 complete", "实际读完全部必要页面",
            "预算不足保持 unknown/incomplete", "不评分、不输出通过",
            "不得据此新增确定性产品红线", "省略证明也不能免除必要上下文",
        ):
            with self.subTest(condition=condition):
                self.assertIn(condition, guide)

    def test_old_combinations_remain_on_their_existing_behavior(self):
        guide = GUIDE.read_text()
        self.assertIn("旧 Skill + 新模板 | 不自动启用新视图", guide)
        self.assertIn("新 Skill + 旧模板 | 缺能力保持旧行为", guide)
        self.assertIn("不替代 Markdown 确定性行数证据", guide)
        self.assertIn("不扩大 HTML/YAML/源码 reader 白名单", guide)


if __name__ == "__main__":
    unittest.main()
