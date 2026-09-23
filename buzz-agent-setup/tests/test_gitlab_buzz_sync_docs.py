"""Contract checks that tie the GitLab → Buzz sync references to the script, so prose and code cannot drift."""
import importlib.util
import json
import re
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
SCRIPT = SKILL / "scripts" / "gitlab_buzz_sync.py"
REFERENCE = SKILL / "references" / "gitlab-buzz-sync.md"
DESK_PROMPT = SKILL / "references" / "gitlab-buzz-sync.desk-prompt.md"
WAKE_TEMPLATE = SKILL / "references" / "workflows" / "webhook-wake-desk.yaml"
REPO = SKILL.parents[1]
USER_STORY = REPO / "docs" / "04-user-stories" / "buzz-agent-setup-gitlab-buzz-sync.md"
ADR_DIR = REPO / "docs" / "05-adr"
TIMER_ADR = ADR_DIR / "0008-run-gitlab-sync-from-owner-systemd-timer.md"
HEARTBEAT_ADR = ADR_DIR / "0005-use-local-heartbeat-and-gate-it-on-restricted-runtime-eval.md"
DESK_OWNED_ADR = ADR_DIR / "0004-run-gitlab-sync-as-desk-owned-agent-step.md"
ADR_INDEX = ADR_DIR / "README.md"
SKILL_MD = SKILL / "SKILL.md"
RUNTIME = SKILL / "references" / "runtime-setup.md"
SCRIPTS_README = SKILL / "references" / "scripts" / "README.md"
EVALS = SKILL / "evals" / "evals.json"
SCENARIO_DOC = REPO / "docs" / "testing" / "scenarios" / "tech-gitlab-buzz-bridge.html"
TEST_PLAN = REPO / "docs" / "plans" / "2026-09-13-buzz-agent-setup-gitlab-buzz-sync-test-plan.md"

# Operational requirements of the retired heartbeat design (ADR-0005). None of them may
# remain a prerequisite for GitLab sync after ADR-0008.
RETIRED_SYNC_RUNTIME = (
    "BUZZ_ACP_HEARTBEAT_INTERVAL",
    "BUZZ_ACP_HEARTBEAT_PROMPT_FILE",
    "BUZZ_ACP_PERMISSION_REQUESTS=deny",
    "INITIAL_AGENT_MODE=read-only",
    "reject_once",
    "prefix_rule(",
    "receipt v2",
)


def load_module():
    spec = importlib.util.spec_from_file_location("gitlab_buzz_sync_docs", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SYNC = load_module()


def read_wake_template() -> tuple[str, str]:
    lines = WAKE_TEMPLATE.read_text(encoding="utf-8").splitlines()
    comments = "\n".join(line for line in lines if line.lstrip().startswith("#"))
    body = "\n".join(line for line in lines if not line.lstrip().startswith("#"))
    return comments, body


class WebhookWakeTemplateContractTest(unittest.TestCase):
    def test_filter_kinds_equal_webhook_handlers(self):
        """L1-GIS-037 webhook 唤醒模板步骤 if 的类型集合与 WEBHOOK_HANDLERS 完全一致（含 work_item）；trigger 下没有会被 relay 丢弃的 filter。"""
        _, body = read_wake_template()
        self.assertNotRegex(body, r"(?m)^\s*filter:")
        if_match = re.search(r"(?ms)^\s*if:\s*>-?\s*\n(.*?)\n\s*action:", body)
        self.assertIsNotNone(if_match)
        kinds = re.findall(r'trigger_object_kind == "([a-z_]+)"', if_match.group(1))
        self.assertEqual(len(kinds), len(set(kinds)))
        self.assertEqual(set(kinds), set(SYNC.WEBHOOK_HANDLERS))

    def test_secret_travels_in_a_header_and_no_debounce_is_declared(self):
        """L1-GIS-037 secret 走 X-Webhook-Secret 请求头，hook URL 不带查询参数，模板正文不含 secret；注释声明没有去抖。"""
        comments, body = read_wake_template()
        self.assertIn("X-Webhook-Secret", comments)
        urls = re.findall(r"https://[^\s，,。；]*/hooks/[^\s，,。；]*", comments)
        self.assertTrue(urls)
        for url in urls:
            with self.subTest(url=url):
                self.assertNotIn("?", url)
        self.assertNotIn("secret", body.lower())
        self.assertIn("没有去抖", comments)


class ReferenceContractTest(unittest.TestCase):
    def test_user_story_keeps_implementation_details_in_technical_documents(self):
        """L1-GIS-103 The User Story contains observable behavior, not implementation contracts."""
        text = USER_STORY.read_text(encoding="utf-8")
        for story_id in range(1, 13):
            self.assertIn(f"US-GIS-{story_id:02d}", text)
        for keyword in ("Given", "When", "Then"):
            self.assertIn(keyword, text)
        for forbidden in (
            ".py", "/v1/", "publisher_pubkey", "allowed_pubkeys", "BUZZ_",
            "X-Route-Secret", "PENDING", "ACKED", "outbox", "cursor", "loopback",
            "gitlab_buzz_", "reply_in_thread", "PRIVATE-TOKEN",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, text)

    def test_reference_declares_every_polling_gap(self):
        """L1-GIS-032 reference 的缺口表逐项列出脚本在 stdout gaps 里声明的每个轮询缺口。"""
        text = REFERENCE.read_text(encoding="utf-8")
        for gap in SYNC.POLLING_GAPS:
            with self.subTest(gap=gap):
                self.assertIn(f"| `{gap}` |", text)

    def test_desk_prompt_is_an_ordinary_desk_fragment_without_sync_commands(self):
        """L1-GIS-054 ADR-0008: the Desk prompt runs no runner/publisher; it only explains the owner timer."""
        text = DESK_PROMPT.read_text(encoding="utf-8")
        block = re.search(r"```text\n(.*?)\n```", text, re.S)

        self.assertIsNotNone(block)
        commands = [
            line.strip() for line in text.splitlines()
            if re.match(r"^\s*(?:/usr/bin/)?python3\s", line)
        ]
        self.assertEqual(commands, [])
        for required in (
            "gitlab_buzz_sync_timer.py",
            "systemd --user",
            "gitlab-buzz-sync-<channel>.timer",
            "不运行",
            "ADR-0008",
        ):
            with self.subTest(required=required):
                self.assertIn(required, text)
        for forbidden in (
            "--summary",
            "--facts",
            "逐字复制",
            "--config",
            "--state-dir",
            "BUZZ_ACP_HEARTBEAT",
            "BUZZ_ACP_PERMISSION_REQUESTS",
            "INITIAL_AGENT_MODE",
            "CODEX_HOME",
            "reject_once",
            "gitlab_buzz_sync_trigger.py",
            "SYNC_TRIGGER_URL",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, text)

    def test_obsolete_loopback_service_is_not_part_of_the_skill(self):
        """L1-GIS-073 Desk-owned sync has no service/trigger sidecar or separate publisher runtime."""
        self.assertFalse((SKILL / "scripts" / "gitlab_buzz_sync_service.py").exists())
        self.assertFalse((SKILL / "scripts" / "gitlab_buzz_sync_trigger.py").exists())
        reference = REFERENCE.read_text(encoding="utf-8")
        self.assertIn("Desk-owned Agent Step", reference)
        for path in (REFERENCE, DESK_PROMPT):
            with self.subTest(document=path.name):
                self.assertNotIn("独立 Bridge service", path.read_text(encoding="utf-8"))



def section(text: str, heading: str) -> str:
    match = re.search(rf"(?ms)^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", text)
    if match is None:
        raise AssertionError(f"missing section: {heading}")
    return match.group(1)


class OwnerTimerDecisionContractTest(unittest.TestCase):
    def test_adr_0008_accepts_the_owner_timer_and_supersedes_the_heartbeat(self):
        """L1-GIS-186 ADR-0008 is Accepted, supersedes ADR-0005 and only ADR-0004's runtime ownership."""
        self.assertTrue(TIMER_ADR.is_file(), "ADR-0008 is required")
        text = TIMER_ADR.read_text(encoding="utf-8")
        front = re.match(r"(?s)^---\n(.*?)\n---\n", text)
        self.assertIsNotNone(front)
        for required in (
            "status: Accepted",
            'date: "2026-09-17"',
            "deciders:\n  - jchen",
            "0005-use-local-heartbeat-and-gate-it-on-restricted-runtime-eval",
            "0004-run-gitlab-sync-as-desk-owned-agent-step",
            # ADR-0009 (2026-09-19) later narrowed only the object-level "first failure stops the run" clause.
            "superseded-by:\n  - 0009-isolate-object-level-data-errors-in-gitlab-sync",
        ):
            with self.subTest(front_matter=required):
                self.assertIn(required, front.group(1))
        for heading in (
            "## Context and Problem Statement",
            "## Considered Options",
            "## Trade-off Analysis",
            "## Decision Outcome",
            "## Consequences",
        ):
            with self.subTest(heading=heading):
                self.assertIn(heading, text)
        options = re.findall(r"(?m)^- \*\*Option [A-Z]:", section(text, "Considered Options"))
        self.assertGreaterEqual(len(options), 2)
        self.assertRegex(section(text, "Trade-off Analysis"), r"\|[^\n]*Benefits[^\n]*\|")
        for required in (
            "systemd --user",
            "gitlab_buzz_sync_timer.py",
            "OnUnitActiveSec=300",
            "Persistent=true",
            "whitelist launcher",
            "BUZZ_DESK_RUNNER_MANIFEST",
            "template_summary",
            "`buzz messages send *`",
            "prompt injection",
            "patched buzz-acp",
            "Same Unix UID",
            "no listener",
            "public schedule Workflow",
            "stays decommissioned",
            "cursor, outbox and binding",
            "double writer",
        ):
            with self.subTest(required=required):
                self.assertIn(required, text)

    def test_superseded_adrs_keep_their_conclusions_and_point_to_0008(self):
        """L1-GIS-186 ADR-0005 becomes Superseded by 0008; ADR-0004 keeps Accepted with a runtime-ownership pointer."""
        heartbeat = HEARTBEAT_ADR.read_text(encoding="utf-8")
        front = re.match(r"(?s)^---\n(.*?)\n---\n", heartbeat).group(1)
        self.assertIn("status: Superseded\n", front + "\n")
        self.assertIn("0008-run-gitlab-sync-from-owner-systemd-timer", front)
        self.assertIn("Choose **Option B**", heartbeat)  # the historical conclusion is not rewritten
        self.assertIn("(0008-run-gitlab-sync-from-owner-systemd-timer.md)", heartbeat)

        desk_owned = DESK_OWNED_ADR.read_text(encoding="utf-8")
        front = re.match(r"(?s)^---\n(.*?)\n---\n", desk_owned).group(1)
        self.assertIn("status: Accepted", front)
        self.assertIn("0008-run-gitlab-sync-from-owner-systemd-timer", front)
        self.assertIn("Choose **Option B: Desk-owned Agent Steps**", desk_owned)
        self.assertIn("(0008-run-gitlab-sync-from-owner-systemd-timer.md)", desk_owned)

        index = ADR_INDEX.read_text(encoding="utf-8")
        self.assertRegex(index, r"\| 0008 \| \[[^\]]+\]\(0008-run-gitlab-sync-from-owner-systemd-timer\.md\) \| Accepted \|")
        self.assertRegex(index, r"\| 0005 \| \[[^\]]+\]\(0005-[^)]+\) \| Superseded \|[^\n]*\| 0008 \|")
        self.assertRegex(
            index, r"\| 0004 \|[^\n]*\| 0006（exact-audience 条款）, 0008（运行归属条款）, 0009（对象级数据错误条款） \|")

    def test_reference_runs_sync_from_the_owner_timer_without_an_llm(self):
        """L1-GIS-187 The reference 运行方式 is systemd timer → whitelist launcher → timer entrypoint → runner/publisher."""
        text = REFERENCE.read_text(encoding="utf-8")
        run_mode = section(text, "运行方式")
        for required in (
            "systemd --user",
            "gitlab-buzz-sync-<channel>.timer",
            "gitlab_buzz_sync_timer.py",
            "OnUnitActiveSec=300",
            "Persistent=true",
            "白名单 launcher",
            "BUZZ_DESK_RUNNER_MANIFEST",
            "template_summary",
            "不经过 LLM",
            "ADR-0008",
        ):
            with self.subTest(required=required):
                self.assertIn(required, run_mode)
        self.assertNotIn("heartbeat", run_mode)
        for forbidden in RETIRED_SYNC_RUNTIME + ("<!-- template:schedule-desk -->",):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, text)

    def test_skill_and_runtime_setup_no_longer_gate_sync_on_a_restricted_llm_runtime(self):
        """L1-GIS-188 SKILL.md/runtime-setup/scripts README point to the timer; no heartbeat/deny preflight for sync."""
        for path in (SKILL_MD, RUNTIME, SCRIPTS_README):
            text = path.read_text(encoding="utf-8")
            with self.subTest(document=path.name):
                self.assertIn("gitlab_buzz_sync_timer.py", text)
                self.assertIn("0008-run-gitlab-sync-from-owner-systemd-timer.md", text)
                for forbidden in RETIRED_SYNC_RUNTIME + ("本地 heartbeat", "patched buzz-acp"):
                    self.assertNotIn(forbidden, text)
        skill = SKILL_MD.read_text(encoding="utf-8")
        self.assertIn("普通 Buzz Agent", skill)
        runtime = RUNTIME.read_text(encoding="utf-8")
        self.assertNotIn("### Codex runtime preflight", runtime)
        self.assertIn("systemd --user timer", runtime)

    def test_evals_scenarios_and_test_plan_encode_the_timer_gate(self):
        """L1-GIS-193 Evals, the L4 scenario page and the test plan gate release on the timer, not a heartbeat."""
        evals = json.loads(EVALS.read_text(encoding="utf-8"))
        self.assertEqual(len(evals["evals"]), evals["contract"]["case_count"])
        serialized = json.dumps(evals, ensure_ascii=False)
        self.assertIn("gitlab_buzz_sync_timer.py", serialized)
        for forbidden in ("本地 heartbeat", "heartbeat 入口", "自动 heartbeat", "patched", "受限 runtime preflight"):
            with self.subTest(evals_forbidden=forbidden):
                self.assertNotIn(forbidden, serialized)

        for path in (SCENARIO_DOC, TEST_PLAN):
            text = path.read_text(encoding="utf-8")
            with self.subTest(document=path.name):
                self.assertIn("gitlab_buzz_sync_timer.py", text)
                self.assertIn("OnUnitActiveSec=300", text)
                self.assertIn("0008-run-gitlab-sync-from-owner-systemd-timer.md", text)
                for forbidden in RETIRED_SYNC_RUNTIME + ("内部 heartbeat", "自动 heartbeat"):
                    self.assertNotIn(forbidden, text)


class PeopleGeneratorContractTest(unittest.TestCase):
    """The people generator (#51) keeps the reference and the --help honest."""

    GENERATOR = SKILL / "scripts" / "gitlab_buzz_people_generate.py"

    def test_generator_help_promises_manual_wins_and_no_deletes(self):
        text = self.GENERATOR.read_text(encoding="utf-8")
        self.assertIn("manual entries win", text)
        self.assertIn("nothing is ever deleted", text)

    def test_reference_documents_the_generator_contract(self):
        text = REFERENCE.read_text(encoding="utf-8")
        for needle in (
            "gitlab_buzz_people_generate.py",
            "手填条目永远赢",
            "永不删除",
            "ops export-people",
            "unmapped",
        ):
            self.assertIn(needle, text)

    def test_reference_documents_the_shared_people_file(self):
        """L1-GIS-PF-201 参考文档写清 people_file：键表、优先级、fail closed、生成器共享模式与迁移步骤。"""
        text = REFERENCE.read_text(encoding="utf-8")
        for needle in (
            "`people_file`",
            "同名内联优先",
            "fail closed",
            "--people-file",
            "从各频道内联 people 迁到共享文件",
            "取并集",
        ):
            self.assertIn(needle, text)

    def test_generator_help_mentions_the_shared_mode(self):
        """L1-GIS-PF-202 生成器 --help 提到共享模式，且仍承诺手填优先、不删除。"""
        text = self.GENERATOR.read_text(encoding="utf-8")
        self.assertIn("--people-file", text)
        self.assertIn("manual entries win", text)
        self.assertIn("nothing is ever deleted", text)

    def test_reference_documents_the_bridge_api_mode_as_the_recommended_source(self):
        """L1-GIS-PA-201 参考文档把 API 模式写成推荐：参数、bridge 开关、签名者角色、fail closed、--export 回退、迁移步骤、部署前置。"""
        text = REFERENCE.read_text(encoding="utf-8")
        generator = text[text.index("### people 的生成器"):text.index("### 同频道多份配置")]
        for needle in (
            "--people-api-base-url",
            "--signer-env-file",
            "CHANNEL_PEOPLE_EMAILS_ENABLED",
            "/bind/api/channels/{channel_id}/people",
            "owner 或 admin",
            "fail closed",
            "推荐",
            "回退",
            "离线",
            "--export",
            "从 `--export` 迁到 API 模式",
            "infra/buzz-deploy#77",
            "BIND_PUBLIC_ORIGIN",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)
        # The recommended source comes before the fallback in the generator section, and the old promises stay.
        self.assertLess(generator.index("--people-api-base-url"), generator.index("`--export` 回退"))
        for needle in ("手填条目永远赢", "永不删除", "ops export-people", "unmapped"):
            with self.subTest(kept=needle):
                self.assertIn(needle, text)

    def test_generator_script_help_lists_both_sources(self):
        """L1-GIS-PA-202 生成器脚本的 --help 写明 API 模式（推荐）、--export（回退），并仍承诺手填优先、不删除。"""
        text = self.GENERATOR.read_text(encoding="utf-8")
        for needle in ("--people-api-base-url", "--signer-env-file", "--export", "CHANNEL_PEOPLE_EMAILS_ENABLED",
                       "manual entries win", "nothing is ever deleted", "--people-file"):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)

    def test_scripts_readme_lists_the_people_generator_with_both_sources(self):
        """L1-GIS-PA-203 scripts README 有生成器一行：API 模式（推荐）、--export 回退、共享模式、手填优先不删除、fail closed。"""
        rows = [line for line in SCRIPTS_README.read_text(encoding="utf-8").splitlines()
                if "gitlab_buzz_people_generate.py" in line]
        self.assertEqual(len(rows), 1)
        for needle in ("--people-api-base-url", "--signer-env-file", "CHANNEL_PEOPLE_EMAILS_ENABLED", "--export",
                       "--people-file", "fail closed", "手填", "不删除", "test_gitlab_buzz_people_generate_api.py"):
            with self.subTest(needle=needle):
                self.assertIn(needle, rows[0])


    def test_reference_documents_the_several_keys_rule(self):
        """L1-GIS-PA-204 参考文档写清「同一邮箱绑了多把 key：选一把」：为什么（要保证用户收得到）、怎么选（最多个已配置频道里是成员、
        并列取 hex 字典序最小）、被拒的先剔除、警告只带用户名 / 前 8 位 / 放弃数、只改 API 模式；旧的「冲突不猜、进 unmapped」说法不许留着，
        而手填优先、不删除、localpart 兜底时不同邮箱不猜这些照旧写着。"""
        text = REFERENCE.read_text(encoding="utf-8")
        generator = text[text.index("### people 的生成器"):text.index("### 同频道多份配置")]
        for needle in (
            "同一邮箱绑了多把 key：选一把",
            "要保证用户可以收到消息",
            "在最多个已配置频道里是成员",
            "hex 字典序最小",
            "先剔除再选",
            "前 8 位",
            "放弃了几把",
            "`--export` 模式不变",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, generator)
        for stale in ("**冲突不猜**", "对应到**不同**的 pubkey，与单频道里 localpart 歧义同一条规则——不写这个人"):
            with self.subTest(stale=stale):
                self.assertNotIn(stale, text)
        for kept in ("手填条目永远赢", "永不删除", "localpart 兜底", "不同邮箱"):
            with self.subTest(kept=kept):
                self.assertIn(kept, generator)

    def test_scripts_readme_row_says_one_key_is_chosen_for_one_address(self):
        """L1-GIS-PA-205 scripts README 的生成器一行：同一邮箱多把 key 选一把（不再说「同一人映射到不同公钥不猜」），手填优先不删除照旧。"""
        rows = [line for line in SCRIPTS_README.read_text(encoding="utf-8").splitlines()
                if "gitlab_buzz_people_generate.py" in line]
        self.assertEqual(len(rows), 1)
        for needle in ("同一邮箱", "选一把", "手填", "不删除"):
            with self.subTest(needle=needle):
                self.assertIn(needle, rows[0])
        self.assertNotIn("同一人映射到不同公钥不猜", rows[0])

if __name__ == "__main__":
    unittest.main()
