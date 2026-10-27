"""Deterministic contracts for delivery-progress-analysis."""

from __future__ import annotations

import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
ISSUE_LINK = re.compile(
    r"(?<!!)\[(?P<label>[^\]]+)\]"
    r"\((?P<url>https://gitlab\.addx\.ai/[^)]+/issues/\d+)\)"
)
ISSUE_ID = re.compile(r"(?:Issue\s+)?#(?P<iid>\d+)\b")


class DeliveryProgressContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        cls.evals = json.loads(
            (ROOT / "evals/evals.json").read_text(encoding="utf-8")
        )
        cls.fixture = (ROOT / "evals/fixtures/naturehood-vn140.md").read_text(
            encoding="utf-8"
        )

    def assert_issue_link_audit(
        self, report: str, expected_urls: dict[str, set[str]]
    ) -> None:
        linked_spans: list[tuple[int, int]] = []
        seen_urls: dict[str, set[str]] = {}

        for match in ISSUE_LINK.finditer(report):
            label_ids = ISSUE_ID.findall(match.group("label"))
            if not label_ids:
                continue
            self.assertEqual(1, len(label_ids), "one link cannot represent multiple Issue IDs")
            iid = label_ids[0]
            url = match.group("url")
            self.assertIn(iid, expected_urls, f"unexpected linked Issue #{iid}")
            self.assertIn(url, expected_urls[iid], f"Issue #{iid} links to wrong URL")
            linked_spans.append(match.span())
            seen_urls.setdefault(iid, set()).add(url)

        visible_text = list(report)
        for start, end in linked_spans:
            visible_text[start:end] = " " * (end - start)
        bare = ISSUE_ID.search("".join(visible_text))
        self.assertIsNone(bare, f"bare Issue ID found: {bare.group(0) if bare else ''}")
        self.assertEqual(expected_urls, seen_urls)

    def test_multi_component_delivery_requires_release_and_package_closure(self) -> None:
        for phrase in ("最终 release 候选", "组合包验收结论"):
            self.assertIn(phrase, self.skill)
            self.assertIn(phrase, self.fixture)

        case = next(case for case in self.evals["evals"] if case["id"] == 1)
        semantic = next(
            assertion["check"]
            for assertion in case["assertions"]
            if assertion["name"] == "semantic_evidence_chain"
        )
        self.assertIn("最终 release 候选", semantic)
        self.assertIn("组合包验收结论", semantic)

    def test_responsible_people_mentions_require_verified_buzz_membership(self) -> None:
        for phrase in (
            "GitLab `assignee.username`",
            "唯一精确匹配",
            "当前 Channel member",
            "显式 `p` tag",
            "不得只凭 display_name",
            "不得 `@all`",
            "Workflow 不保存责任人名单",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.skill)

        case = next(case for case in self.evals["evals"] if case["id"] == 6)
        checks = "\n".join(item["check"] for item in case["assertions"])
        self.assertIn("真实 p tag", checks)
        self.assertIn("不发送 mention", checks)

    def test_every_rendered_issue_id_is_a_clickable_exact_issue_link(self) -> None:
        for phrase in (
            "报告中出现的每一个 Issue ID 都必须可点击",
            "GitLab Issue 返回的精确 `web_url`",
            "[#198](<issue-web-url>)",
            "[Issue #198](<issue-web-url>)",
            "摘要、进展、风险、待办、表格、通知、未排期列表、索引和方法反馈",
            "不得输出裸 `#198`",
            "Issue 链接审计",
            "发现裸 ID 时先修正再发布",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.skill)

        case = next(case for case in self.evals["evals"] if case["id"] == 1)
        checks = "\n".join(item["check"] for item in case["assertions"])
        self.assertIn("所有可见的 Issue ID", checks)
        self.assertIn("不允许裸 Issue ID", checks)

        expected = {
            "42": {
                "https://gitlab.addx.ai/group-a/project-a/-/issues/42",
                "https://gitlab.addx.ai/group-b/project-b/-/issues/42",
            }
        }
        valid_report = (
            "[Issue #42](https://gitlab.addx.ai/group-a/project-a/-/issues/42) "
            "已合并；"
            "[#42](https://gitlab.addx.ai/group-b/project-b/-/issues/42) 待验收。\n"
            "【覆盖缺口】离线恢复的 Issue 链接缺失。"
        )
        self.assert_issue_link_audit(valid_report, expected)

        invalid_reports = (
            "Issue #42 已合并。",
            "group-a/project-a#42 已合并。",
            "![Issue #42](https://gitlab.addx.ai/group-a/project-a/-/issues/42)",
            "[Issue #42 and #43](https://gitlab.addx.ai/group-a/project-a/-/issues/42)",
            "[Issue #42](https://gitlab.addx.ai/group-a/project-a/-/issues/159)",
            "[Issue #42](https://gitlab.addx.ai/group-a/project-a)",
            "[#77](https://gitlab.addx.ai/group-c/project-c/-/issues/77)",
        )
        for report in invalid_reports:
            with self.subTest(report=report), self.assertRaises(AssertionError):
                self.assert_issue_link_audit(report, expected)

    def test_cross_project_and_missing_url_eval_contract(self) -> None:
        fixture = (
            ROOT / "evals/fixtures/cross-project-issue-links.md"
        ).read_text(encoding="utf-8")
        self.assertIn("group-a/project-a/-/issues/42", fixture)
        self.assertIn("group-b/project-b/-/issues/42", fixture)
        self.assertIn("web_url 缺失", fixture)

        case = next(case for case in self.evals["evals"] if case["id"] == 11)
        checks = "\n".join(item["check"] for item in case["assertions"])
        self.assertIn("跨项目同号", checks)
        self.assertIn("不显示 #77", checks)
        self.assertIn("Issue 链接缺失", checks)

        for case in self.evals["evals"]:
            names = {item["name"] for item in case["assertions"]}
            self.assertIn("clickable_issue_ids", names, f"eval case {case['id']}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
