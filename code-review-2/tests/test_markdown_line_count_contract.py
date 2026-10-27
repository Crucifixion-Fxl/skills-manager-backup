"""Regression guards for deterministic evidence and unchanged review boundaries."""

import json
import re
import unittest
from pathlib import Path


SKILL = Path(__file__).resolve().parents[1]
REFERENCES = SKILL / "references"


class MarkdownLineCountContractTest(unittest.TestCase):
    def test_negotiation_matches_the_metadata_only_producer(self):
        contract = json.loads(
            (REFERENCES / "markdown-line-count-contract.json").read_text()
        )
        self.assertEqual(
            contract,
            {
                "schema_version": 1,
                "capability": "markdown-line-count/v1",
                "artifact": "markdown-metadata.json",
            },
        )
        self.assertNotIn("tool", contract)

    def test_all_consumers_reach_the_same_evidence_contract(self):
        for filename in (
            "SKILL.md",
            "references/review-data-contract.md",
            "references/ci-integration.md",
        ):
            with self.subTest(filename=filename):
                self.assertIn("markdown-line-count.md", (SKILL / filename).read_text())

    def test_line_limit_is_not_red_in_rules_template_or_example(self):
        skill = (SKILL / "SKILL.md").read_text()
        line_rules = [line for line in skill.splitlines() if "600 行" in line]
        self.assertGreaterEqual(len(line_rules), 4)
        # Preserve the existing advisory classification across all three entrypoints.
        self.assertIn("超过建议拆分", skill)
        self.assertIn("✅/⚠️/豁免/待核验", skill)
        self.assertIn("636 行 → ⚠️", skill)
        for line in line_rules:
            with self.subTest(line=line):
                self.assertNotRegex(line, r"(?:✅/🔴|→ 🔴)")
        self.assertIn("建议改进项（不阻断）", skill)

    def test_unknown_evidence_never_becomes_a_pass(self):
        guide = (REFERENCES / "markdown-line-count.md").read_text()
        decision_rows = re.findall(r"^\|.*\|$", guide, flags=re.MULTILINE)
        unknown = [row for row in decision_rows if "`unknown`" in row]
        self.assertEqual(len(unknown), 1)
        self.assertIn("禁止写通过", unknown[0])
        self.assertIn("新 Skill + 旧模板 | 缺报告记 unknown", guide)
        self.assertIn("按既有必要证据", guide)
        self.assertIn("不能使行数项变成 incomplete", guide)
        self.assertIn("内容证据缺口", guide)

    def test_identity_scope_and_digest_are_not_optional(self):
        guide = (REFERENCES / "markdown-line-count.md").read_text()
        for evidence in (
            "project_id", "head_sha", "meta.scope.head_sha", "scope_status",
            "meta.scope.status", "commit_sha", "blob_sha", "sha256", "bytes",
            "total_lines", "集合相等且无重复", "身份或摘要不匹配",
            "MR 自带同名文件", "任意附件", "CODE_REVIEW_MARKDOWN_METADATA_ENABLED",
        ):
            with self.subTest(evidence=evidence):
                self.assertIn(evidence, guide)

    def test_counting_and_permission_boundaries_are_explicit(self):
        guide = (REFERENCES / "markdown-line-count.md").read_text()
        for boundary in (
            "空文件为 0 行", "`LF`、`CRLF`、单独 `CR`", "末尾换行不额外增加一行",
            "0 至 600 行", "超过 600 行", "`over_limit`", "`exempt`",
            "`not_applicable`", "扩展名不区分大小写", "`docs/plans-other/`",
            "不扫描未修改文档", "不输出 Markdown 正文", "diff-first",
            "不扩大", "UNSUPPORTED_FILE", "不触发合并",
        ):
            with self.subTest(boundary=boundary):
                self.assertIn(boundary, guide)


if __name__ == "__main__":
    unittest.main()
