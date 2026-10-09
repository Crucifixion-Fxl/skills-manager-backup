from pathlib import Path
import unittest


SKILL = Path(__file__).resolve().parents[1] / "SKILL.md"
PROGRESS = Path(__file__).resolve().parents[1] / "references" / "progress-comments.md"


class AiClaimContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.skill = SKILL.read_text(encoding="utf-8")
        cls.progress = PROGRESS.read_text(encoding="utf-8")

    def test_description_triggers_claim_before_mutation(self) -> None:
        description = self.skill.split("---", 2)[1]
        self.assertIn("会话认领", description)
        self.assertIn("harness", description)
        self.assertIn("session id", description)
        self.assertIn("恢复指针", description)
        self.assertIn("push", description)
        self.assertIn("session 清理", description)

    def test_claim_is_append_only_and_stops_on_another_active_session(self) -> None:
        section = self.skill.split("## AI 会话认领", 1)[1].split("## 进展评论 SOP", 1)[0]
        for field in (
            "不改 description",
            "不改历史认领",
            "status: active",
            "supersedes:",
            "尚未创建",
            "unknown",
            "GROK_SESSION_ID",
            "不编造",
            "用户级 memory",
            "memory 不是锁",
            "issue-sync",
            "session 清理",
            "push",
        ):
            with self.subTest(field=field):
                self.assertIn(field, section)

    def test_missing_session_id_is_unknown_not_an_invented_variable(self) -> None:
        section = self.skill.split("## AI 会话认领", 1)[1].split("## 进展评论 SOP", 1)[0]
        self.assertIn("没有则写 `unknown`", section)
        self.assertNotIn("CLAUDE_SESSION_ID", section)
        self.assertNotIn("CODEX_SESSION_ID", section)

    def test_red_line_requires_claim_and_writeback(self) -> None:
        rules = self.skill.split("### 操作红线", 1)[1]
        self.assertIn("AI 开工前先认领，push / MR / session 清理都回写 Issue", rules)
        self.assertIn("用户级 memory 不能代替 Issue 上的锁", rules)

    def test_progress_templates_cover_claim_push_and_cleanup(self) -> None:
        for field in (
            "## AI 接手 (YYYY-MM-DD)",
            "尚未创建",
            "已 push 本 Issue 分支",
            "session 清理",
            "released",
            "今天写代码了",
        ):
            with self.subTest(field=field):
                self.assertIn(field, self.progress)
        self.assertIn("本地每次 commit", self.progress)


if __name__ == "__main__":
    unittest.main()
