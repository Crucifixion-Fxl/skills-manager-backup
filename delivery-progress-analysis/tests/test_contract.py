"""Deterministic contracts for delivery-progress-analysis."""

from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
