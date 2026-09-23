"""Contract: long agent reports become Feishu docs, made and shared by the agent's own bot, not by the owner's user token."""

from __future__ import annotations

from pathlib import Path
import re
import unittest


SKILL = Path(__file__).resolve().parents[1]
REFS = SKILL / "references"
GUIDE = REFS / "feishu-doc-report.md"
SKILL_MD = SKILL / "SKILL.md"
SCHEDULED = REFS / "scheduled-workflows.md"
TEMPLATES = sorted((REFS / "analysis-workflows").glob("*.yaml"))


def read(path: Path) -> str:
    if not path.is_file():
        raise AssertionError(f"{path.relative_to(SKILL)} is required")
    return path.read_text(encoding="utf-8")


def code_blocks(text: str) -> list[str]:
    return re.findall(r"```(?:bash|sh)\n(.*?)\n```", text, re.S)


class FeishuDocReportGuideTest(unittest.TestCase):
    def setUp(self) -> None:
        self.text = read(GUIDE)
        self.commands = "\n".join(code_blocks(self.text))

    def test_the_agent_that_wrote_the_report_publishes_it_as_its_own_bot(self) -> None:
        for step in ("docs +create", "drive +member-add", "im +messages-send"):
            lines = [line for line in self.commands.splitlines() if step in line]
            self.assertTrue(lines, f"{step} is a required step")
            for line in lines:
                self.assertIn("--as bot", line, f"{step} must run as the agent's own bot")

    def test_no_command_uses_the_owners_user_identity(self) -> None:
        self.assertNotIn("--as user", self.commands)
        self.assertRegex(self.text, r"谁产出谁发")

    def test_the_agent_uses_its_own_isolated_lark_profile(self) -> None:
        self.assertIn("LARKSUITE_CLI_CONFIG_DIR", self.commands)
        self.assertIn("LARKSUITE_CLI_DATA_DIR", self.commands)

    def test_the_document_is_shared_to_the_group_read_only(self) -> None:
        self.assertIn("--member-type openchat", self.commands)
        self.assertIn("--perm view", self.commands)
        self.assertNotRegex(self.commands, r"--perm (edit|full_access)")

    def test_the_body_goes_through_stdin_not_a_file_path(self) -> None:
        self.assertRegex(self.commands, r"--content -")
        self.assertNotRegex(self.commands, r"--content @/")

    def test_a_missing_bot_scope_is_a_named_blocker_for_the_owner(self) -> None:
        for token in ("docx:document:create", "docs:permission.member:create", "app_scope_not_applied"):
            self.assertIn(token, self.text)
        self.assertIn("没有 API", self.text)
        self.assertRegex(self.text, r"不得(?:退回|改用|回退)[^\n]*user")

    def test_the_channel_only_gets_a_short_summary_and_the_link(self) -> None:
        self.assertRegex(self.text, r"摘要")
        self.assertRegex(self.text, r"链接")
        self.assertRegex(self.text, r"Thread")

    def test_the_guide_prefers_the_publish_helper_and_explains_buzz_hosted_images(self) -> None:
        self.assertIn("feishu_doc_publish.py", self.text)
        self.assertIn("buzz media get", self.text)
        self.assertIn("@./", self.text)
        self.assertRegex(self.text, r"30\s*秒")

    def test_a_channel_mirrored_to_a_feishu_group_needs_no_extra_group_message(self) -> None:
        self.assertRegex(self.text, r"镜像[^\n]*不用另发|不用另发[^\n]*镜像")

    def test_no_secret_or_identity_material_goes_into_the_document(self) -> None:
        self.assertRegex(self.text, r"token")
        self.assertRegex(self.text, r"open_id")


class FeishuDocReportWiringTest(unittest.TestCase):
    def test_skill_index_points_at_the_guide(self) -> None:
        self.assertIn("references/feishu-doc-report.md", read(SKILL_MD))

    def test_scheduled_workflows_route_long_reports_to_the_guide(self) -> None:
        text = read(SCHEDULED)
        self.assertIn("feishu-doc-report.md", text)
        self.assertRegex(text, r"飞书文档")

    def test_every_analysis_template_tells_the_agent_to_publish_long_reports_as_a_doc(self) -> None:
        self.assertTrue(TEMPLATES)
        for path in TEMPLATES:
            with self.subTest(template=path.name):
                text = read(path)
                self.assertIn("飞书文档", text)
                self.assertIn("feishu-doc-report", text)


if __name__ == "__main__":
    unittest.main()
