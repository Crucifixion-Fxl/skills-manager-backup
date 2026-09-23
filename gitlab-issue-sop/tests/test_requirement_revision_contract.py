from pathlib import Path
import unittest


SKILL = Path(__file__).resolve().parents[1] / "SKILL.md"


class RequirementRevisionContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.skill = SKILL.read_text(encoding="utf-8")

    def test_ai_conversation_requirement_change_is_a_trigger(self) -> None:
        self.assertIn("AI 对话中的需求变化", self.skill)
        self.assertIn("已确认的需求变化", self.skill)

    def test_revision_is_appended_without_overwriting_intake(self) -> None:
        section = self.skill.split("## AI 对话中的需求变化", 1)[1]
        self.assertIn("新增 comment", section)
        self.assertIn("不得覆盖原始描述", section)
        self.assertIn("canonical Issue", section)

    def test_revision_template_preserves_required_evidence(self) -> None:
        section = self.skill.split("## AI 对话中的需求变化", 1)[1]
        for field in (
            "变更来源",
            "需求变化",
            "Scope / AC delta",
            "影响范围",
            "待确认项",
        ):
            with self.subTest(field=field):
                self.assertIn(field, section)

    def test_revision_rule_distinguishes_change_from_clarification(self) -> None:
        section = self.skill.split("## AI 对话中的需求变化", 1)[1]
        self.assertIn("不构成需求变化", section)
        self.assertIn("需求修订", section)

    def test_each_eval_inner_loop_has_one_deduplicated_review_task(self) -> None:
        section = self.skill.split("## Eval / TDD 内外环 Review Task", 1)[1]
        normalized = " ".join(section.split())
        for contract in (
            "每个 inner-loop round 必须创建一个且仅一个 GitLab Task",
            "round_id",
            "同一 `round_id`",
            "新的 `round_id`",
            "assignee",
            "append-only comment",
            "人类外环",
        ):
            with self.subTest(contract=contract):
                self.assertIn(contract, normalized)


if __name__ == "__main__":
    unittest.main()
