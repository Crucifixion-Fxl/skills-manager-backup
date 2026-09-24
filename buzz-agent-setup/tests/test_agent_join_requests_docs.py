"""Documentation contract for who may put an agent into a channel (skills#144, ADR-0018)."""

from __future__ import annotations

from pathlib import Path
import unittest


TEST_DIR = Path(__file__).resolve().parent
SKILL_DIR = TEST_DIR.parent
REPO = SKILL_DIR.parents[1]
SKILL = SKILL_DIR / "SKILL.md"
RUNTIME = SKILL_DIR / "references" / "runtime-setup.md"
MODEL = SKILL_DIR / "references" / "fchac-model.md"
SCRIPTS_README = SKILL_DIR / "references" / "scripts" / "README.md"
ADR = REPO / "docs" / "05-adr" / "0018-treat-a-bot-invite-as-a-join-request-the-agent-owner-approves-in-the-channel.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _section(text: str, start: str, end: str) -> str:
    begin = text.index(start)
    return text[begin:text.index(end, begin + len(start))]


class WhoMayAddAnAgentTest(unittest.TestCase):
    """relay-v0.2.1: any channel admin (any member of an open channel) can add an agent; the agent's own
    channel_add_policy is the real switch.  The skill used to say only the owner can."""

    def test_no_document_claims_only_the_owner_can_add_members(self) -> None:
        for path in (RUNTIME, MODEL, SCRIPTS_README, SKILL):
            for stale in ("加成员只能由 owner", "只能由 owner `add-member", "只能由 owner `channels add-member"):
                with self.subTest(document=path.name, forbidden=stale):
                    self.assertNotIn(stale, _read(path))

    def test_runtime_setup_explains_the_add_policy_and_sets_it_per_agent_type(self) -> None:
        section = _section(_read(RUNTIME), "### 谁能把 agent 拉进频道", "## 4. Env 与注册")
        for needle in (
            "`channel_add_policy`",
            "`anyone`",
            "`owner_only`",
            "`nobody`",
            "buzz channels set-add-policy",
            "kind:10100",
            "open 频道",
            "private 频道",
            "该频道的 owner/admin",
            "业务角色 agent 保持 `anyone`",
            "平台类 agent 设 `owner_only`",
            "executor 设 `nobody`",
            "`BUZZ_ACP_CHANNELS`",
            "buzz_agent_join_requests.py",
            "ADR-0018",
        ):
            with self.subTest(requirement=needle):
                self.assertIn(needle, section)

    def test_the_condition_table_no_longer_says_only_the_owner_adds_the_bot(self) -> None:
        table = _section(_read(RUNTIME), "## 3. Agent 可被 @ 的三个条件", "CLI 创建的 Agent 不会自动发 30177")
        self.assertIn("该频道的 owner/admin", table)
        self.assertIn("`channel_add_policy`", table)
        self.assertNotIn("| owner 用上述 `BUZZ_CLI` 执行 `channels add-member", table)

    def test_platform_agents_are_owner_only_and_subscribe_without_a_restart(self) -> None:
        runtime = _section(_read(RUNTIME), "### 平台 Desk", "用 Agent 自己身份注册档案并 join")
        for needle in ("`owner_only`", "动态订阅", "membership notification: subscribing to new channel"):
            with self.subTest(document="runtime", requirement=needle):
                self.assertIn(needle, runtime)
        model = _section(_read(MODEL), "## 平台 Desk", "**转交协议")
        self.assertIn("`owner_only`", model)

    def test_skill_step_three_sets_the_add_policy(self) -> None:
        step = _section(_read(SKILL), "### 3. 配 Agent 准入与运行时", "### 4. 配 Workflow 与 GitLab 同步")
        self.assertIn("set-add-policy", step)
        self.assertIn("ADR-0018", step)

    def test_adr_records_the_source_facts_and_the_decision(self) -> None:
        adr = _read(ADR)
        for needle in ("relay-v0.2.1", "desktop-v0.5.23", "channel_add_policy", "Option D", "`/approve JOIN-<id>`",
                       "owner_only", "nobody", "BASELINE"):
            with self.subTest(requirement=needle):
                self.assertIn(needle, adr)


JOIN_REFERENCE = SKILL_DIR / "references" / "agent-channel-join.md"
JOIN_EXAMPLE = SKILL_DIR / "references" / "scripts" / "buzz-agent-join.example.json"


class JoinRequestReferenceTest(unittest.TestCase):
    """The owner-facing runbook for buzz_agent_join_requests.py."""

    def test_reference_covers_the_flow_the_approval_and_the_limits(self) -> None:
        text = _read(JOIN_REFERENCE)
        for needle in (
            "buzz_agent_join_requests.py",
            "ADR-0018",
            "BUZZ_JOIN_CONFIG",
            "BASELINE",
            "`/approve JOIN-<id>`",
            "`/deny JOIN-<id>`",
            "✅",
            "❌",
            "Buzz Desktop",
            "镜像身份",
            "<!-- buzz-agent-channels:v1 -->",
            "<!-- /buzz-agent-channels:v1 -->",
            "subscribed to channel",
            "BUZZ_RESPONSIBLE_CONFIG",
            "`owner_only`",
            "`nobody`",
            "OnUnitActiveSec=120",
            "systemctl --user enable --now",
            "systemctl --user disable --now",
            "<immutable-release>",
            "buzz-join:v1",
            "set-add-policy",
            "原群",
            "失败原因",
            "恢复方法",
            "无法确认邀请来源",
        ):
            with self.subTest(requirement=needle):
                self.assertIn(needle, text)
        self.assertNotIn("失败只进 user journal，频道保持安静", text)

    def test_reference_units_carry_no_secret(self) -> None:
        text = _read(JOIN_REFERENCE)
        for forbidden in ("BUZZ_PRIVATE_KEY=", "nsec1", "GITLAB_TOKEN=", "EnvironmentFile="):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, text)

    def test_the_example_config_is_accepted_by_the_script(self) -> None:
        import importlib.util
        import json

        spec = importlib.util.spec_from_file_location(
            "buzz_agent_join_requests_docs", SKILL_DIR / "scripts" / "buzz_agent_join_requests.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.validate_config(json.loads(_read(JOIN_EXAMPLE)))

    def test_docs_follow_the_review_fixes(self) -> None:
        """No invite: record only; DMs excluded; role=bot; 900 s ambiguity; manual allowlist; re-apply."""
        reference, adr = _read(JOIN_REFERENCE), _read(ADR)
        for document, text in (("reference", reference), ("adr", adr)):
            for needle in ("NO_INVITE", "dms list", "role=bot", "900 秒", "manual"):
                with self.subTest(document=document, requirement=needle):
                    self.assertIn(needle, text)
        self.assertNotIn("普通成员拉的，或者找不到邀请记录", reference)
        self.assertNotIn("其他情况（普通成员、guest、bot、找不到邀请记录）", adr)
        self.assertIn("重新写入", reference)

    def test_docs_follow_the_second_review(self) -> None:
        """Membership events, cgroup idleness, journal logs, grace period, prompt row content, output and config."""
        reference, adr = _read(JOIN_REFERENCE), _read(ADR)
        for document, text in (("reference", reference), ("adr", adr)):
            for needle in ("9021", "cgroup", "journal", "600 秒", "只写频道 UUID", "不会拉起", "30 天", "正在开通"):
                with self.subTest(document=document, requirement=needle):
                    self.assertIn(needle, text)
            for stale in ("120 秒内有写入", "最多推迟 1 小时", "不一致整轮失败", "在输出里列出"):
                with self.subTest(document=document, forbidden=stale):
                    self.assertNotIn(stale, text)
        for needle in ("`version`", "`log_file`", "`states`", "`locked`", "BUZZ_ACP_SYSTEM_PROMPT_FILE",
                       "TimeoutStartSec", "200"):
            with self.subTest(document="reference", requirement=needle):
                self.assertIn(needle, reference)
        self.assertIn("status: Accepted", adr)

    def test_docs_follow_the_verification_round(self) -> None:
        reference, adr = _read(JOIN_REFERENCE), _read(ADR)
        for document, text in (("reference", reference), ("adr", adr)):
            for needle in ("最多取 500 个仓库", "最多重新写入 2 次", "恢复", "自己签的退群", "15 分钟"):
                with self.subTest(document=document, requirement=needle):
                    self.assertIn(needle, text)
            self.assertNotIn("最多解析 500 行", text)

    def test_docs_state_signature_checks_and_the_journal_cursor(self) -> None:
        reference, adr = _read(JOIN_REFERENCE), _read(ADR)
        for document, text in (("reference", reference), ("adr", adr)):
            for needle in ("验签", "--after-cursor"):
                with self.subTest(document=document, requirement=needle):
                    self.assertIn(needle, text)
            self.assertNotIn("从 journal 读重启之后的内容", text)

    def test_adr_index_lists_0018(self) -> None:
        index = _read(REPO / "docs" / "05-adr" / "README.md")
        self.assertIn("(0018-treat-a-bot-invite-as-a-join-request-the-agent-owner-approves-in-the-channel.md)", index)

    def test_add_policy_wording_keeps_both_conditions(self) -> None:
        readme = _read(SCRIPTS_README)
        self.assertIn("还必须是 agent 的 owner", readme)
        self.assertNotIn("时只能是 agent 的 owner", readme)

    def test_runtime_setup_says_where_the_dynamic_subscription_line_comes_from(self) -> None:
        section = _section(_read(RUNTIME), "### 平台 Desk", "用 Agent 自己身份注册档案并 join")
        self.assertIn("源码读出来的", section)

    def test_skill_and_runtime_setup_point_to_the_reference(self) -> None:
        skill = _read(SKILL)
        self.assertIn("(references/agent-channel-join.md)", skill)
        runtime = _section(_read(RUNTIME), "### 谁能把 agent 拉进频道", "## 4. Env 与注册")
        self.assertIn("(agent-channel-join.md)", runtime)


if __name__ == "__main__":
    unittest.main()
