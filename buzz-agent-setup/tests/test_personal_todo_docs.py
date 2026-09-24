"""Contract for the personal-channel todo sync deliverables: ADR, runbook, workflow template, docs (ADR-0013)."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest


SKILL = Path(__file__).resolve().parents[1]
REPO = SKILL.parents[1]
REFS = SKILL / "references"
ADR = REPO / "docs" / "05-adr" / "0013-run-personal-todo-sync-with-the-owners-pat.md"
RUNBOOK = REFS / "systemd" / "personal-todo-sync.md"
WORKFLOW = REFS / "workflows" / "personal-todo-wake.yaml"
GUIDE = REFS / "personal-channel.md"
EXAMPLE = REFS / "scripts" / "gitlab-todo-sync.example.json"
SCRIPTS_README = REFS / "scripts" / "README.md"
HOWTO = REPO / "public" / "work-methods" / "buzz-personal-agent-howto.html"
sys.path.insert(0, str(SKILL / "scripts"))

WHITELIST = {"HOME", "USER", "LOGNAME", "PATH", "LANG", "BUZZ_RELAY_URL", "BUZZ_PRIVATE_KEY",
             "BUZZ_AUTH_TAG", "GITLAB_TODO_TOKEN", "GITLAB_TODO_CONFIG"}
DARWIN_PYTHON_ENV = {"__CF_USER_TEXT_ENCODING", "CPATH", "LIBRARY_PATH", "MANPATH", "SDKROOT"}
RESULT_KEYS = {"status", "marked_done", "resolved", "rejected", "truncated", "seen", "delivered",
               "filtered", "invalid"}
RECOMMENDED_ACTIONS = ["assigned", "review_requested", "mentioned", "directly_addressed", "build_failed",
                       "approval_required"]


def read(path: Path) -> str:
    if not path.is_file():
        raise AssertionError(f"{path.relative_to(REPO)} is required")
    return path.read_text(encoding="utf-8")


def template(text: str, name: str, lang: str) -> str:
    """The fenced block right after `<!-- template:NAME -->`: assertions stay inside that block."""

    match = re.search(rf"<!-- template:{re.escape(name)} -->\s*```{lang}\n(.*?)\n```", text, re.S)
    if match is None:
        raise AssertionError(f"template block {name} ({lang}) is required")
    return match.group(1)


def section(text: str, heading: str) -> str:
    """The text from the heading line that starts with `heading` to the next heading of the same or a higher level.

    Lines inside fenced code blocks are never headings (the runbook's shell and ini blocks are full of `# comment` lines).
    """

    lines, in_fence, start, level = text.split("\n"), False, None, 0
    for number, line in enumerate(lines):
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        match = None if in_fence else re.match(r"(#+) (.*)", line)
        if match is None:
            continue
        if start is None:
            if match.group(2).startswith(heading):
                start, level = number, len(match.group(1))
        elif len(match.group(1)) <= level:
            return "\n".join(lines[start:number])
    if start is None:
        raise AssertionError(f"heading {heading!r} is required")
    return "\n".join(lines[start:])


def actual_result_keys() -> set[str]:
    """The keys a real (fake-backed) run returns: the docs are checked against the script, not against a copy of it."""

    import gitlab_todo_sync as todo

    owner, publisher, desk = "a" * 64, "b" * 64, "d" * 64
    config = {"version": 1, "channel_id": "11111111-2222-3333-4444-555555555555", "publisher_pubkey": publisher,
              "owner_pubkey": owner, "desk_pubkey": desk, "done_authors": [owner],
              "buzz": {"cli_path": "/opt/buzz/usr/bin/buzz", "cli_sha256": "e" * 64},
              "gitlab": {"base_url": "https://gitlab.example", "username": "jchen", "token_env": "GITLAB_TODO_TOKEN"},
              "todo": {"since": "2026-09-19T00:00:00Z"}}

    class GitLab:
        truncated = False

        def user(self):
            return {"username": "jchen"}

        def pending_todos(self):
            return []

    class Buzz:
        def channel_members(self):
            return {owner: "owner", publisher: "bot", desk: "bot"}

        def channel_messages(self, since):
            return []

    with tempfile.TemporaryDirectory() as tmp:
        result = todo.run(config, {"GITLAB_TODO_TOKEN": "x"}, state_dir=Path(tmp) / "state", gitlab=GitLab(),
                          buzz=Buzz(), clock=lambda: 1_789_819_200)
    return set(result)


class AdrTest(unittest.TestCase):
    def test_adr_records_the_exception_and_its_compensating_controls(self):
        """L1-PTS-060 ADR-0013 Accepted，明写例外范围、残余风险与补偿控制，并登记进 README。"""
        text = read(ADR)
        self.assertRegex(text, r"(?m)^status: Accepted$")
        for needle in ("GITLAB_TODO_TOKEN", "mark_as_done", "same Unix UID", "Rule 1", "Rule 11",
                       "api", "expir", "allowlist", "revoke", "personal Channel"):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)
        readme = read(REPO / "docs" / "05-adr" / "README.md")
        self.assertRegex(readme, r"\|\s*0013\s*\|.*0013-run-personal-todo-sync-with-the-owners-pat\.md")

    def test_adr_lists_the_member_set_gate_and_the_new_residual_risks(self):
        """L1-PTS-066 每条断言只看它所属的小节：补偿控制含 member-set gate、结论仍是 Option A；残余风险含「@ 洪泛未限速」与「REJECTED 需人工」；frontmatter 是 Accepted。"""
        text = read(ADR)
        frontmatter = text.split("\n# ", 1)[0]
        self.assertRegex(frontmatter, r"(?m)^status: Accepted$")
        outcome = section(text, "Decision Outcome")
        self.assertIn("Choose **Option A**", outcome)
        self.assertIn("member-set gate", outcome)
        risks = section(text, "Negative and residual risk")
        for needle in ("REJECTED", "flood"):
            with self.subTest(needle=needle):
                self.assertIn(needle, risks)

    def test_adr_records_the_defusing_and_the_marker_time_bound_without_changing_the_decision(self):
        """L1-PTS-066B 补偿控制里写明 `nostr:` 与 `gitlab-todo` 去活、完成标记必须晚于投递时间（留 300 秒余量）；REJECTED 一条与新的拒发规则一致；结论不变。"""
        text = read(ADR)
        outcome = section(text, "Decision Outcome")
        for needle in ("nostr:", "gitlab-todo", "300 seconds"):
            with self.subTest(needle=needle):
                self.assertIn(needle, outcome)
        risks = section(text, "Negative and residual risk")
        rejected = [b for b in risks.split("\n- ") if "REJECTED" in b]
        self.assertEqual(len(rejected), 1)
        for needle in ("later send", "fails the round"):
            with self.subTest(needle=needle):
                self.assertIn(needle, rejected[0])
        self.assertIn("Choose **Option A**", outcome)


    def test_adr_binds_the_done_marker_to_the_thread_of_the_todo_and_keeps_the_risk_scoped(self):
        """L1-PTS-066C 补偿控制「Bounded write」一条写明标记必须是对该待办 Thread 的回复（`e` tag）；残余风险里「被注入的助手能发完成信号」仍在，但限定在该待办的 Thread 内；结论不变。"""
        text = read(ADR)
        outcome = section(text, "Decision Outcome")
        bounded = [b for b in outcome.split("\n- ") if b.startswith("**Bounded write.**")]
        self.assertEqual(len(bounded), 1)
        for needle in ("reply into the Thread of that todo", "`e` tag", "ignored"):
            with self.subTest(section="bounded write", needle=needle):
                self.assertIn(needle, bounded[0])
        risks = section(text, "Negative and residual risk")
        marker_risk = [b for b in risks.split("\n- ") if "prompt-injected assistant" in b]
        self.assertEqual(len(marker_risk), 1)
        for needle in ("as a reply into that todo's Thread", "each by replying into that todo's Thread", "still",
                       "todos this script delivered"):
            with self.subTest(section="residual risk", needle=needle):
                self.assertIn(needle, marker_risk[0])
        self.assertIn("Choose **Option A**", outcome)

    def test_adr_bounds_the_reaction_signal_like_the_text_marker_and_keeps_the_risk_scoped(self):
        """L1-PTS-066D 「Bounded write」写明第二种信号：可信作者对该待办自己那条消息点 `todo.done_emojis` 里的表情（缺省 ✅）的 reaction（`e` tag 指向那条消息；不认 Thread 根、不认 Thread 里别的消息；晚于投递时间；撤销的不算；别的表情如 👀 不算）；残余风险里被注入的助手也能点 ✅，与文字标记同类、同样限定在本脚本投递过的待办上；结论不变。"""
        text = read(ADR)
        outcome = section(text, "Decision Outcome")
        bounded = [b for b in outcome.split("\n- ") if b.startswith("**Bounded write.**")]
        self.assertEqual(len(bounded), 1)
        for needle in ("reaction", "todo.done_emojis", "✅", "own message", "`e` tag", "Thread root", "withdrawn",
                       "todo:done:<id>"):
            with self.subTest(section="bounded write", needle=needle):
                self.assertIn(needle, bounded[0])
        risks = section(text, "Negative and residual risk")
        marker_risk = [b for b in risks.split("\n- ") if "prompt-injected assistant" in b]
        self.assertEqual(len(marker_risk), 1)
        for needle in ("✅", "reaction", "same kind", "todos this script delivered"):
            with self.subTest(section="residual risk", needle=needle):
                self.assertIn(needle, marker_risk[0])
        self.assertIn("Choose **Option A**", outcome)

    def test_adr_states_the_read_window_covers_the_same_300_seconds_the_time_bound_allows(self):
        """L1-PTS-066E 「Bounded write」写明事件读取窗口从最老待确认投递往前 300 秒开始（覆盖判定下界的余量，不会漏读 D-300..D-61 的合法信号）、最多回看 30 天；结论不变。"""
        outcome = section(read(ADR), "Decision Outcome")
        bounded = [b for b in outcome.split("\n- ") if b.startswith("**Bounded write.**")]
        self.assertEqual(len(bounded), 1)
        for needle in ("read window", "300 seconds before the oldest", "30 days"):
            with self.subTest(needle=needle):
                self.assertIn(needle, bounded[0])
        self.assertIn("Choose **Option A**", outcome)


class WorkflowTemplateTest(unittest.TestCase):
    def test_wake_workflow_filters_on_publisher_and_header_and_never_calls_webhook(self):
        """L1-PTS-061 唤醒 Workflow 只认 todo 发布者 + header 前缀，用 send_message，不出网。"""
        text = read(WORKFLOW)
        self.assertRegex(text, r"(?m)^\s*on:\s*message_posted\s*$")
        self.assertRegex(text, r'trigger_author == "<todo-publisher-hex-pubkey>"')
        self.assertRegex(text, r'str_contains\(trigger_text,\s*"\[gitlab-todo:v1\]\[action:<action>\]')
        self.assertRegex(text, r"(?m)^\s*action:\s*send_message\s*$")
        self.assertNotIn("call_webhook", text)
        self.assertIn("todo:done:", text)
        self.assertIn("不是指令", text)


    def test_wake_workflow_tells_the_assistant_to_reply_to_the_todo_message_and_states_the_rule(self):
        """L1-PTS-061B 唤醒文本里的回复方式：用 --reply-to 回复原待办消息（那条消息的 id = {{trigger.message_id}}，渲染不出来就用 buzz messages thread／搜索找），并在该 Thread 里单独回一行 todo:done:<id>，说明不满足会被忽略（另一种方式点 ✅ 见 L1-PTS-061C）；注释里的已知限制改成新规则，旧的「不要求出现在原 Thread」不再出现。"""
        text = read(WORKFLOW)
        comments = "\n".join(line for line in text.split("\n") if line.lstrip().startswith("#"))
        step = text.split("\nsteps:\n", 1)[1]
        for needle in ("--reply-to", "{{trigger.message_id}}", "buzz messages thread", "该 Thread", "todo:done:<id>",
                       "被忽略"):
            with self.subTest(block="wake text", needle=needle):
                self.assertIn(needle, step)
        self.assertRegex(step, re.compile(r"\{\{trigger\.message_id\}\}.{0,120}--reply-to 回复它", re.S))  # the id is the one to reply to
        self.assertRegex(step, re.compile(r"渲染不出来.{0,40}buzz messages thread", re.S))  # and what to do without it
        for needle in ("必须是对该待办 Thread 的回复", "reply_in_thread", "--reply-to", "被忽略"):
            with self.subTest(block="comments", needle=needle):
                self.assertIn(needle, comments)
        self.assertNotIn("不要求出现在原 Thread", text)
        self.assertNotIn("频道内 + 可信作者 + 本脚本投递过的 id", text)

    def test_wake_workflow_offers_both_ways_to_finish_the_reaction_and_the_reply(self):
        """L1-PTS-061C 唤醒文本把两种完成方式并列：对那条待办消息点 ✅（`buzz reactions add --event <那条待办消息的 id> --emoji ✅`，id 就是 {{trigger.message_id}}，渲染不出来就用 buzz messages thread／搜索找），或 --reply-to 回复它并在该 Thread 里单独回一行 todo:done:<id>；不是对那条待办消息本身的 ✅、也不是对它 Thread 的回复，完成标记会被忽略。文件头注释写明两种方式、reaction 只认待办消息本身（不认 Thread 根）、👀 之类不算；只有唤醒文本这一处讲步骤（不重复出现在别的 step 里）。"""
        text = read(WORKFLOW)
        step = re.sub(r"\s+", " ", text.split("\nsteps:\n", 1)[1])
        for needle in ("buzz reactions add --event", "--emoji ✅", "对那条待办消息点 ✅", "--reply-to", "todo:done:<id>",
                       "不是对那条待办消息本身的 ✅", "也不是对它 Thread 的回复", "完成标记会被忽略"):
            with self.subTest(block="wake text", needle=needle):
                self.assertIn(needle, step)
        self.assertRegex(step, r"buzz reactions add --event <那条待办消息的 id> --emoji ✅")
        self.assertRegex(step, r"那条待办消息的 id[^;；]{0,40}\{\{trigger\.message_id\}\}")  # what the id is
        self.assertRegex(step, r"渲染不出来.{0,40}buzz messages thread")  # and what to do without it
        self.assertLess(step.index("点 ✅"), step.index("--reply-to"))  # the reaction is named first, the reply is the other way
        self.assertNotIn("处理完成后，用 --reply-to 回复那条待办消息（它的 id", step)  # the old single-way wording
        comments = "\n".join(line for line in text.split("\n") if line.lstrip().startswith("#"))
        for needle in ("两种", "reaction", "待办消息本身", "Thread 根", "👀", "撤销", "buzz reactions add", "todo.done_emojis"):
            with self.subTest(block="comments", needle=needle):
                self.assertIn(needle, comments)
        self.assertEqual(text.count("buzz reactions add"), 2)  # one in the header comment, one in the wake text

class RunbookTest(unittest.TestCase):
    def test_timer_runs_every_600_seconds_as_a_oneshot(self):
        """L1-PTS-062 service 块：launcher + 入口 + NoNewPrivileges、oneshot、不用 EnvironmentFile=；timer 块：每 600 秒、Persistent。"""
        text = read(RUNBOOK)
        service = template(text, "todo-timer-service", "ini")
        timer = template(text, "todo-timer-timer", "ini")
        for needle in ("gitlab-todo-sync-launch.sh", "gitlab_todo_sync.py", "NoNewPrivileges=yes", "Type=oneshot"):
            with self.subTest(block="service", needle=needle):
                self.assertIn(needle, service)
        self.assertNotIn("EnvironmentFile=", service)
        for needle in ("OnUnitActiveSec=600", "Persistent=true", "Unit=gitlab-todo-sync-<name>.service"):
            with self.subTest(block="timer", needle=needle):
                self.assertIn(needle, timer)
        self.assertNotIn("EnvironmentFile=", timer)
        self.assertNotIn("Type=oneshot", timer)

    def test_runbook_documents_every_result_field_the_script_returns(self):
        """L1-PTS-067 「启用、手动运行、观察」一节里：JSON 示例的键 = 脚本真实返回的键，每个键（除 status）都有一条说明；30 页/3000 条/60 秒预算与 truncated 不收敛写在同一节。"""
        text = read(RUNBOOK)
        observe = section(text, "3. 启用").split("### state 里的状态")[0]
        sample = re.search(r'\{"status":"ok"[^}\n]*\}', observe)
        self.assertIsNotNone(sample, "the observe section needs a JSON output example")
        keys = actual_result_keys()
        self.assertEqual(set(re.findall(r'"(\w+)":', sample.group(0))), keys)
        self.assertEqual(keys, RESULT_KEYS)
        for key in sorted(keys - {"status"}):
            with self.subTest(key=key):
                self.assertIn(f"`{key}`", observe)
        for needle in ("30 页", "3000", "60 秒", "`RESOLVED` 收敛"):
            with self.subTest(needle=needle):
                self.assertIn(needle, observe)

    def test_runbook_state_table_names_every_state_the_script_defines(self):
        """L1-PTS-067B 「state 里的状态」表里每个脚本定义的状态名各占一行；REJECTED 行按新规则写：逐条被拒（有后续成功可证明）才记，系统性拒收整轮失败。"""
        import gitlab_todo_sync as todo

        table = section(read(RUNBOOK), "state 里的状态")
        rows = {m.group(1): m.group(0) for m in re.finditer(r"(?m)^\| `([A-Z]+)` \|.*$", table)}
        self.assertEqual(set(rows), set(todo.STATES))
        for needle in ("有后续成功", "系统性", "整轮失败"):
            with self.subTest(needle=needle):
                self.assertIn(needle, rows["REJECTED"])

    def test_runbook_describes_the_failure_paths_as_the_script_now_behaves(self):
        """L1-PTS-067C 观察一节：mark_as_done 逐条失败不挡后面的标记与投递、本轮抛最后一个错误；阶段 0 核对 channels members；边界一节写明 unset -f 与「所有合法的 shell 标识符名称」（含点号/连字符的非标识符名称原样透传但无害）。"""
        text = read(RUNBOOK)
        observe = section(text, "3. 启用").split("### state 里的状态")[0]
        for needle in ("后面的标记", "最后一个错误"):
            with self.subTest(section="observe", needle=needle):
                self.assertIn(needle, observe)
        phase0 = [b for b in observe.split("\n- ") if b.startswith("**阶段 0 要核对成员列表**")]
        self.assertEqual(len(phase0), 1)  # the bullet itself, not the shell comment that points at it
        for needle in ("channels members", "整轮失败关闭"):
            with self.subTest(section="phase 0 bullet", needle=needle):
                self.assertIn(needle, phase0[0])
        boundary = section(text, "边界")
        for needle in ("unset -f", "所有合法的 shell 标识符名称", "非标识符", "原样透传"):
            with self.subTest(section="boundary", needle=needle):
                self.assertIn(needle, boundary)
        self.assertNotIn("其余名字一律丢弃", boundary)

    def test_runbook_and_launcher_say_the_same_thing_about_what_is_dropped(self):
        """L1-PTS-067D launcher 注释不再说「whatever it is called」：写成「合法 shell 标识符」，并说明非标识符名称的透传；runbook 说明检查范围含入口文件、scripts/ 目录与 release 根目录的属主与组/其他可写。"""
        text = read(RUNBOOK)
        launcher = template(text, "todo-timer-launcher", "bash")
        self.assertNotIn("whatever it is called", launcher)
        self.assertIn("valid shell identifier", launcher)
        self.assertIn("not valid shell identifiers", launcher)
        checks = [ln for ln in section(text, "边界").split("\n") if "launcher 对入口路径做严格校验" in ln]
        self.assertEqual(len(checks), 1)
        for needle in ("release 根目录", "属主", "组／其他"):
            with self.subTest(needle=needle):
                self.assertIn(needle, checks[0])


    def test_runbook_says_a_marker_counts_only_as_a_reply_into_the_todos_thread(self):
        """L1-PTS-067E runbook 的 marked_done 说明与 state 表 ACKED 一行都写明：完成标记必须是对该待办 Thread 的回复；Thread 外的标记被忽略、待办保持 ACKED。"""
        text = read(RUNBOOK)
        observe = section(text, "3. 启用").split("### state 里的状态")[0]
        marked = [ln for ln in observe.split("\n") if ln.lstrip().startswith("- `marked_done`")]
        self.assertEqual(len(marked), 1)
        self.assertIn("必须是对该待办 Thread 的回复", marked[0])
        table = section(text, "state 里的状态")
        acked = [ln for ln in table.split("\n") if ln.startswith("| `ACKED` |")]
        self.assertEqual(len(acked), 1)
        for needle in ("该待办的 Thread", "忽略"):
            with self.subTest(needle=needle):
                self.assertIn(needle, acked[0])

    def test_runbook_documents_the_reaction_signal_and_the_done_emojis_key(self):
        """L1-PTS-067F runbook：`marked_done` 说明与 state 表 ACKED 一行都写明第二种信号（对待办消息本身点 ✅ 的 reaction，只认待办消息本身、别的表情与撤销的不算）；配置一节写明可选键 `todo.done_emojis`（缺省 ["✅"]）。"""
        text = read(RUNBOOK)
        observe = section(text, "3. 启用").split("### state 里的状态")[0]
        marked = [ln for ln in observe.split("\n") if ln.lstrip().startswith("- `marked_done`")]
        self.assertEqual(len(marked), 1)
        for needle in ("reaction", "✅", "待办消息本身", "todo.done_emojis", "必须是对该待办 Thread 的回复"):
            with self.subTest(block="marked_done", needle=needle):
                self.assertIn(needle, marked[0])
        table = section(text, "state 里的状态")
        acked = [ln for ln in table.split("\n") if ln.startswith("| `ACKED` |")]
        self.assertEqual(len(acked), 1)
        for needle in ("✅", "reaction", "该待办的 Thread", "忽略"):
            with self.subTest(block="ACKED row", needle=needle):
                self.assertIn(needle, acked[0])
        keys = [ln for ln in text.split("\n") if "`todo.done_emojis`" in ln and "配置" in ln]
        self.assertTrue(keys, "the config section must describe todo.done_emojis")
        for needle in ('["✅"]', "可选", "第一个", "U+FE0F"):
            with self.subTest(block="config key", needle=needle):
                self.assertIn(needle, "\n".join(keys))
        for needle in ("完成后点 ✅（表情里搜 check）或在本 Thread 回复", "只在第一个表情是 ✅"):
            with self.subTest(block="config key hint", needle=needle):
                self.assertIn(needle, "\n".join(keys))
        self.assertNotIn("完成后点 ✅ 或在本 Thread", text)

    def test_runbook_says_the_read_window_covers_the_300_second_slack_and_is_capped_at_30_days(self):
        """L1-PTS-067G runbook：`marked_done` 说明写明读取窗口从最老的待确认投递往前 300 秒开始（与判定下界的余量一致），最多回看 30 天；文字与 reaction 用同一个窗口。"""
        observe = section(read(RUNBOOK), "3. 启用").split("### state 里的状态")[0]
        marked = [ln for ln in observe.split("\n") if ln.lstrip().startswith("- `marked_done`")]
        self.assertEqual(len(marked), 1)
        for needle in ("最老的待确认投递", "往前 300 秒", "30 天", "同一个时间窗口"):
            with self.subTest(needle=needle):
                self.assertIn(needle, marked[0])


@unittest.skipUnless(shutil.which("bash"), "needs bash")
class LauncherTest(unittest.TestCase):
    """Runs the real launcher template under bash, with the interpreter path swapped for this Python."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        text = template(read(RUNBOOK), "todo-timer-launcher", "bash")
        self.assertIn("/usr/bin/python3", text)
        self.python = sys.executable
        self.launcher = self.root / "gitlab-todo-sync-launch.sh"
        self.launcher.write_text(text.replace("/usr/bin/python3", shlex.quote(self.python)) + "\n", encoding="utf-8")
        self.launcher.chmod(0o700)
        self.release = self.root / "releases" / ("a" * 40)
        self.scripts = self.release / "scripts"
        self.scripts.mkdir(parents=True)
        self.entry = self.scripts / "gitlab_todo_sync.py"
        self.entry.write_text(
            "import json, os, sys\nprint(json.dumps({'argv': sys.argv[1:], 'env': dict(os.environ)}))\n",
            encoding="utf-8")
        self.entry.chmod(0o644)  # the umask of the machine running the tests must not decide these modes
        self.scripts.chmod(0o755)
        self.release.chmod(0o755)
        self.env_file = self.root / "todo.env"
        self.write_env()
        self.base = {"HOME": str(self.root), "USER": "jchen", "LOGNAME": "jchen", "PATH": "/usr/bin:/bin",
                     "BUZZ_TODO_ENV_FILE": str(self.env_file), "LEAKED_PARENT": "x"}

    def write_env(self, extra="", drop=()):
        lines = {
            "BUZZ_RELAY_URL": "https://relay.test", "BUZZ_PRIVATE_KEY": "test-publisher-key",
            "BUZZ_AUTH_TAG": "'[\"auth\",\"owner\",\"\",\"sig\"]'", "GITLAB_TODO_TOKEN": "test-owner-pat",
            "GITLAB_TODO_CONFIG": "/owner/todo.json", "GITLAB_TOKEN": "agent-token-must-not-leak",
            "ANTHROPIC_AUTH_TOKEN": "must-not-leak",
        }
        body = "".join(f"{k}={v}\n" for k, v in lines.items() if k not in drop)
        self.env_file.write_text(body + extra, encoding="utf-8")
        self.env_file.chmod(0o600)

    def launch(self, *args, env=None):
        argv = [str(self.launcher), *(args or (self.python, str(self.entry)))]
        return subprocess.run(argv, env=env or self.base, capture_output=True, text=True, timeout=30)

    def assert_refused(self, run, label, *, mentions=None):
        self.assertNotEqual(run.returncode, 0, f"{label}: {run.stdout!r} {run.stderr!r}")
        self.assertEqual(run.stdout, "", label)
        if mentions:
            self.assertIn(mentions, run.stderr, label)

    def test_launcher_passes_only_the_todo_whitelist(self):
        """L1-PTS-063 launcher 校验 0600 env，只把 todo 白名单交给入口，且 Agent 的 GITLAB_TOKEN 进不去。"""
        ok = self.launch()
        self.assertEqual(ok.returncode, 0, ok.stderr)
        seen = json.loads(ok.stdout)
        self.assertEqual(seen["argv"], [])
        actual = set(seen["env"])
        self.assertEqual(actual - (DARWIN_PYTHON_ENV if sys.platform == "darwin" else set()), WHITELIST)
        self.assertLessEqual(actual - WHITELIST, DARWIN_PYTHON_ENV)
        self.assertEqual(seen["env"]["GITLAB_TODO_TOKEN"], "test-owner-pat")
        self.assertEqual(seen["env"]["PATH"], "/usr/local/bin:/usr/bin:/bin")

    @unittest.skipUnless(sys.platform == "darwin", "macOS system alias")
    def test_launcher_allows_only_the_macos_var_system_alias(self):
        self.assertTrue(str(self.root).startswith("/private/var/"), self.root)
        raw_root = Path(str(self.root).removeprefix("/private"))
        raw_env = {
            **self.base,
            "HOME": str(raw_root),
            "BUZZ_TODO_ENV_FILE": str(raw_root / "todo.env"),
        }
        run = subprocess.run(
            [
                str(raw_root / "gitlab-todo-sync-launch.sh"),
                self.python,
                str(raw_root / "releases" / ("a" * 40) / "scripts" / "gitlab_todo_sync.py"),
            ],
            env=raw_env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(run.returncode, 0, run.stderr)

    def test_exported_bash_functions_do_not_reach_the_entrypoint(self):
        """L1-PTS-063B 环境或 env 文件里导出的 bash 函数（BASH_FUNC_*）不外泄：白名单删除只管变量，函数要单独 unset -f。"""
        self.write_env(extra="foo() { echo pwned; }\nexport -f foo\nfail() { echo hijacked; }\nexport -f fail\n")
        env = {**self.base, "BASH_FUNC_evil%%": "() {  echo evil\n}"}
        ok = self.launch(env=env)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        seen = set(json.loads(ok.stdout)["env"])
        self.assertEqual(seen - (DARWIN_PYTHON_ENV if sys.platform == "darwin" else set()), WHITELIST)
        self.assertLessEqual(seen - WHITELIST, DARWIN_PYTHON_ENV)
        self.assertFalse([name for name in seen if name.startswith("BASH_FUNC")])

    def test_entrypoint_must_be_the_exact_canonical_owner_only_script(self):
        """L1-PTS-063C 入口路径严格校验：另一个（真实存在的）脚本、含 .. 的路径、符号链接、组/其他可写、非常规字符都拒绝。"""
        other = self.scripts / "gitlab_buzz_sync_timer.py"
        other.write_text("print('other entrypoint ran')\n", encoding="utf-8")
        dotdot = self.scripts / ".." / "scripts" / "gitlab_todo_sync.py"
        self.assertTrue(dotdot.is_file())
        link_release = self.root / "releases" / "linked"
        link_release.symlink_to(self.release)
        odd_paths = {}
        for label, dirname in (("space", "we ird"), ("semicolon", "sh;ell"), ("dollar", "dol$lar"), ("non-ascii", "uni-é")):
            odd = self.root / dirname / "scripts"
            odd.mkdir(parents=True)
            (odd / "gitlab_todo_sync.py").write_text("print('ran')\n", encoding="utf-8")
            (odd / "gitlab_todo_sync.py").chmod(0o644)  # owner-only writable: only the path characters can be at fault
            odd.chmod(0o755)
            odd.parent.chmod(0o755)  # the release root too: the umask of the test machine must not decide this
            odd_paths[f"{label} in the path"] = (self.python, str(odd / "gitlab_todo_sync.py"))
        for name in ("gitlab_todo_sync.py.bak", "gitlab_todo_syncXpy", "xgitlab_todo_sync.py"):
            (self.scripts / name).write_text("print('ran')\n", encoding="utf-8")
            (self.scripts / name).chmod(0o644)
            odd_paths[f"look-alike file {name}"] = (self.python, str(self.scripts / name))
        file_link = self.root / "elsewhere.py"
        file_link.write_text("print('ran')\n", encoding="utf-8")
        symlinked_entry = self.root / "releases" / ("b" * 40) / "scripts"
        symlinked_entry.mkdir(parents=True)
        symlinked_entry.parent.chmod(0o755)
        symlinked_entry.chmod(0o755)
        (symlinked_entry / "gitlab_todo_sync.py").symlink_to(file_link)
        cases = {
            "relative interpreter": ("python3", str(self.entry)),
            "another entrypoint that exists": (self.python, str(other)),
            "dot-dot path to the real entry": (self.python, str(dotdot)),
            "symlinked directory component": (self.python, str(link_release / "scripts" / "gitlab_todo_sync.py")),
            "entry is a symlink": (self.python, str(symlinked_entry / "gitlab_todo_sync.py")),
            "relative entry": (self.python, "scripts/gitlab_todo_sync.py"),
            "extra argv": (self.python, str(self.entry), "--config", "/tmp/x"),
            "no argv": (),
            **odd_paths,
        }
        for label, args in cases.items():
            with self.subTest(label):
                run = subprocess.run([str(self.launcher), *args], env=self.base, capture_output=True,
                                     text=True, timeout=30)
                self.assert_refused(run, label, mentions="non-symlink" if label == "entry is a symlink" else None)
        self.assertEqual(self.launch().returncode, 0)  # the exact script still runs
        for label, path, ok_mode, message in (
            ("entry", self.entry, 0o644, "entrypoint must not be"),
            ("scripts dir", self.scripts, 0o755, "entrypoint directory must not be"),
            ("release root", self.release, 0o755, "release directory must not be"),
        ):
            for kind, bad_mode in (("group-writable", ok_mode | 0o020), ("other-writable", ok_mode | 0o002)):
                with self.subTest(f"{kind} {label}"):
                    path.chmod(bad_mode)
                    self.addCleanup(path.chmod, ok_mode)
                    self.assert_refused(self.launch(), f"{kind} {label}", mentions=f"{message} group- or other-writable")
                    path.chmod(ok_mode)
        self.assertEqual(self.launch().returncode, 0)

    def test_entrypoint_its_directory_and_the_release_root_must_each_be_owned_by_the_current_user(self):
        """L1-PTS-063E 三处属主都必须是当前用户：用包装解释器只对指定路径返回别的 uid，不依赖 root 或 GNU stat。"""
        for label, victim, message in (
            ("entrypoint", self.entry, "entrypoint must be owned"),
            ("scripts directory", self.scripts, "entrypoint directory must be owned"),
            ("release root", self.release, "release directory must be owned"),
        ):
            wrapper = self.root / f"python-{label.replace(' ', '-')}"
            wrapper.write_text(
                "#!/bin/sh\n"
                f"if [ \"$1\" = -c ] && [ \"$3\" = {shlex.quote(str(victim))} ] && "
                "case \"$2\" in *st_uid*) true;; *) false;; esac; then echo 4242; exit 0; fi\n"
                f"exec {shlex.quote(self.python)} \"$@\"\n",
                encoding="utf-8",
            )
            wrapper.chmod(0o755)
            launcher = self.root / f"launcher-{label.replace(' ', '-')}"
            launcher.write_text(self.launcher.read_text().replace(self.python, str(wrapper)), encoding="utf-8")
            launcher.chmod(0o700)
            with self.subTest(label):
                run = subprocess.run([str(launcher), str(wrapper), str(self.entry)], env=self.base,
                                     capture_output=True, text=True, timeout=30)
                self.assert_refused(run, label, mentions=message)
        self.assertEqual(self.launch().returncode, 0)

    def test_names_that_are_not_shell_identifiers_pass_through_unchanged(self):
        """L1-PTS-063F 含点号/连字符的非标识符名称不会变成 shell 变量，白名单删除看不见它们，bash 原样透传给入口：与 runbook「原样透传但无害」一致（入口不读它们）；其余合法标识符名称一律被删。"""
        env = {**self.base, "a.b": "1", "x-y": "2", "PLAIN_LEAK": "3"}
        ok = self.launch(env=env)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        seen = json.loads(ok.stdout)["env"]
        self.assertEqual({k: seen[k] for k in ("a.b", "x-y")}, {"a.b": "1", "x-y": "2"})
        self.assertEqual(set(seen) - WHITELIST - (DARWIN_PYTHON_ENV if sys.platform == "darwin" else set()), {"a.b", "x-y"})

    def test_launcher_works_under_a_utf8_locale_with_non_ascii_values(self):
        """L1-PTS-063G 调用方的 locale 是 UTF-8、环境变量与 env 文件里带非 ASCII 值：launcher 照常放行，入口拿到的仍只有白名单（LANG=C.UTF-8）；非 ASCII 的入口路径仍被拒。"""
        self.write_env(extra="NON_ASCII_IN_ENV_FILE=héllo\n")
        odd = self.root / "uni-é" / "scripts"
        odd.mkdir(parents=True)
        (odd / "gitlab_todo_sync.py").write_text("print('ran')\n", encoding="utf-8")
        (odd / "gitlab_todo_sync.py").chmod(0o644)
        odd.chmod(0o755)
        odd.parent.chmod(0o755)  # owner-only writable, whatever the umask: only the path characters can be at fault
        for name in ("C.UTF-8", "en_US.UTF-8"):
            env = {**self.base, "LANG": name, "LC_ALL": name, "NON_ASCII_PARENT": "日本語"}
            with self.subTest(locale=name):
                ok = self.launch(env=env)
                self.assertEqual(ok.returncode, 0, ok.stderr)
                seen = json.loads(ok.stdout)["env"]
                actual = set(seen)
                self.assertEqual(actual - (DARWIN_PYTHON_ENV if sys.platform == "darwin" else set()), WHITELIST)
                self.assertLessEqual(actual - WHITELIST, DARWIN_PYTHON_ENV)
                self.assertEqual(seen["LANG"], "C.UTF-8")
                self.assert_refused(self.launch(self.python, str(odd / "gitlab_todo_sync.py"), env=env), "non-ASCII path")

    def test_env_file_must_be_a_private_regular_non_symlink_file_with_all_keys(self):
        """L1-PTS-063D env 文件必须 0600、非 symlink、含五个必需键；缺一个就不启动入口。"""
        self.env_file.chmod(0o640)
        self.assert_refused(self.launch(), "loose mode")
        self.env_file.chmod(0o600)
        link = self.root / "link.env"
        os.symlink(self.env_file, link)
        self.assert_refused(self.launch(env={**self.base, "BUZZ_TODO_ENV_FILE": str(link)}), "symlink",
                            mentions="symlink")
        self.assert_refused(self.launch(env={k: v for k, v in self.base.items() if k != "BUZZ_TODO_ENV_FILE"}),
                            "no env file")
        for key in ("BUZZ_RELAY_URL", "BUZZ_PRIVATE_KEY", "BUZZ_AUTH_TAG", "GITLAB_TODO_TOKEN", "GITLAB_TODO_CONFIG"):
            self.write_env(drop=(key,))
            with self.subTest(missing=key):
                self.assert_refused(self.launch(), f"missing {key}", mentions=key)


class ExampleAndDocsTest(unittest.TestCase):
    FILL = {"<owner-hex-pubkey>": "a" * 64, "<todo-publisher-hex-pubkey>": "b" * 64,
            "<personal-channel-desk-hex-pubkey>": "d" * 64,
            "<assistant-hex-pubkey>": "c" * 64, "<channel-uuid>": "11111111-2222-3333-4444-555555555555",
            "<buzz-cli-sha256>": "e" * 64, "<user>": "jchen", "<name>": "me",
            "<gitlab-username>": "jchen", "<now-utc>": "2026-09-19T00:00:00Z"}

    def test_example_config_validates_once_placeholders_are_filled(self):
        """L1-PTS-064 示例配置填好占位符后通过脚本自己的校验；actions 是推荐的六项，since 是 <now-utc> 占位符。"""
        import gitlab_todo_sync as todo

        raw = read(EXAMPLE)
        text = raw
        for key, value in self.FILL.items():
            text = text.replace(key, value)
        self.assertNotRegex(text, r"<[a-z-]+>")
        config = json.loads(text)
        todo.validate_config(config)
        self.assertNotIn(config["publisher_pubkey"], config["done_authors"])
        self.assertEqual(json.loads(raw)["todo"]["since"], "<now-utc>")
        self.assertEqual(config["todo"]["actions"], RECOMMENDED_ACTIONS)
        self.assertEqual(config["todo"]["done_emojis"], ["✅"])  # the example shows the key, at its default
        partial = raw
        for key, value in self.FILL.items():
            if key != "<now-utc>":
                partial = partial.replace(key, value)
        with self.assertRaises(todo.sync.SyncError):  # an unfilled since placeholder must not validate
            todo.validate_config(json.loads(partial))

    def test_guide_and_skill_route_to_the_personal_channel_flow(self):
        """L1-PTS-065 SKILL.md 按任务路由到个人 Channel 指南；指南含采访、处理档、验证与例外声明。"""
        skill = read(SKILL / "SKILL.md")
        self.assertIn("references/personal-channel.md", skill)
        self.assertIn("0013-run-personal-todo-sync-with-the-owners-pat.md", skill)
        guide = read(GUIDE)
        for needle in ("GITLAB_TODO_TOKEN", "todo:done:", "personal-todo-wake.yaml", "gitlab_todo_sync.py",
                       "notify", "triage", "draft", "act", "ACT", "buzz workflows create",
                       "ADR-0013", "不是指令"):
            with self.subTest(needle=needle):
                self.assertIn(needle, guide)
        self.assertIn("personal-channel.md", read(REFS / "fchac-model.md"))

    def test_guide_documents_the_member_gate_flood_gap_and_rejected_records(self):
        """L1-PTS-068 指南「安全与已知缺口」写明受众门禁的成员集合、@ 洪泛缺口（max_per_run×6/小时，别用 *）；REJECTED 一条按新规则写：逐条被拒（有后续成功可证明）才记，系统性拒收整轮响亮失败，需人工。"""
        gaps = section(read(GUIDE), "安全与已知缺口")
        for needle in ("owner_pubkey", "publisher_pubkey", "done_authors", "子集", "max_per_run", "× 6", "`*`", "限速"):
            with self.subTest(needle=needle):
                self.assertIn(needle, gaps)
        rejected = [b for b in gaps.split("\n- ") if b.startswith("**`REJECTED`")]
        self.assertEqual(len(rejected), 1)
        for needle in ("有后续成功", "系统性拒收", "整轮", "人工"):
            with self.subTest(needle=needle):
                self.assertIn(needle, rejected[0])

    def test_guide_lists_the_three_unverified_assumptions_for_the_real_machine(self):
        """L1-PTS-068B 已知缺口里列出三条待真机验证的假设：① relay Workflow 与下游不对文字做 NFKC 归一化（U+2011 连字符只是缓解）；② 阶段 0 核对 channels members 是否列出 relay Workflow 服务公钥等系统成员（列出则成员集合门禁整轮失败关闭）；③ 标记时间下界用本机时钟，作者时钟按 relay ±900 秒窗口可能偏更多。"""
        gaps = section(read(GUIDE), "安全与已知缺口")
        unverified = [b for b in gaps.split("\n- ") if "待真机验证" in b]
        self.assertGreaterEqual(len(unverified), 1)
        joined = "\n".join(unverified)
        for needle in ("NFKC", "U+2011", "channels members", "阶段 0", "整轮失败关闭", "本机时钟", "±900"):
            with self.subTest(needle=needle):
                self.assertIn(needle, joined)

    def test_guide_describes_the_defusing_the_script_really_does(self):
        """L1-PTS-068C 「数据流」一节里的去活说明：标题/摘要里的 gitlab-todo 换成不可归一化的连字符，项目路径与用户名原样显示，链接里的 gitlab-todo 编成 %2D；`[gitlab-todo:v1]` 只在末行出现一次。"""
        flow = section(read(GUIDE), "数据流")
        for needle in ("%2D", "原样", "`[gitlab-todo:v1]`", "只出现一次"):
            with self.subTest(needle=needle):
                self.assertIn(needle, flow)

    def test_guide_shows_the_concise_message_layout_the_script_renders(self):
        """L1-PTS-091 「数据流」里的消息示例是精简版式，4 行：「标题 · 发起人」行、裸网址、单行完成指引（默认 ✅ 带「（表情里搜 check）」）、末行 header（没有单独的项目路径行）；没有「链接：」前缀、「摘要（…）：」标签行和旧的两行完成指引。叙述写明链接取待办自己的 target_url（#note_ 锚点、/pipelines），不合格才回退 target.web_url，摘要只在正文与标题不同时出现且最多 3 行。"""
        flow = section(read(GUIDE), "数据流")
        blocks = [b for b in re.findall(r"```text\n(.*?)\n```", flow, re.S) if "[gitlab-todo:v1]" in b]
        self.assertEqual(len(blocks), 1)
        lines = blocks[0].split("\n")
        self.assertRegex(lines[0], r"^请你评审 · !45 \S+ · \S+$")
        self.assertRegex(lines[1], r"^https://\S+$")
        self.assertRegex(lines[-2], r"^完成后点 ✅（表情里搜 check）或在本 Thread 回复 todo:done:123$")
        self.assertRegex(lines[-1], r"^\[gitlab-todo:v1\]\[action:review_requested\]")
        self.assertEqual(len(lines), 4)
        self.assertNotIn("group/project · alice", blocks[0])
        self.assertEqual(blocks[0].count("group/project"), 1)  # only inside the link
        for old in ("链接：", "摘要（", "处理完成后", "单独一行", "完成后在本 Thread 回复", "完成后点 ✅ 或在本 Thread"):
            with self.subTest(old=old):
                self.assertNotIn(old, blocks[0])
        for needle in ("target_url", "target.web_url", "#note_", "/pipelines", "最多 3 行"):
            with self.subTest(needle=needle):
                self.assertIn(needle, flow)

    def test_guide_explains_reusing_an_existing_personal_agent_and_channel(self):
        """L1-PTS-069 指南有「复用已有的个人 agent 与频道」：把 todo 唤醒 Workflow 加成写死例外，done_authors 用那个 agent 的公钥；不写死任何 npub/UUID。"""
        guide = read(GUIDE)
        self.assertIn("## 复用已有的个人 agent 与频道", guide)
        section = guide[guide.index("## 复用已有的个人 agent 与频道"):]
        section = section[:section.index("\n## ", 5)] if "\n## " in section[5:] else section
        for needle in ("jchen-ubuntu-192-168-20-24", "jchen_personal", "写死", "Workflow", "例外", "prompt",
                       "done_authors", "公钥", "tag", "三项必需", "一项可选", "{{trigger.message_id}}", "模式匹配"):
            with self.subTest(needle=needle):
                self.assertIn(needle, section)
        self.assertNotIn("四件事", section)
        self.assertNotIn("精确匹配 [Workflow", section)
        self.assertIn("个人助手 Agent（新建，或复用本机已有的个人 agent）", guide)
        self.assertNotRegex(guide, r"npub1[0-9a-z]{20,}")
        self.assertNotRegex(guide, r"\b[0-9a-f]{64}\b")
        self.assertNotRegex(guide, r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")


    def test_guide_states_the_thread_rule_for_done_markers_everywhere_it_talks_about_them(self):
        """L1-PTS-068D 指南里凡是讲完成标记的地方都按新规则：数据流一句、助手 prompt 片段（--reply-to）、验收（不在原 Thread 的标记不触发）、已知缺口（`e` tag、--reply-to、Desktop 回复原 Thread、不满足则被忽略且待办保持 pending）；旧的「Workflow 唤醒不在原 Thread → 频道内任意位置都算」不再出现。"""
        guide = read(GUIDE)
        flow = section(guide, "数据流")
        for needle in ("必须是对该待办 Thread 的回复", "--reply-to"):
            with self.subTest(block="flow", needle=needle):
                self.assertIn(needle, flow)
        prompt = re.search(r"助手的 prompt 片段[^\n]*\n\n```text\n(.*?)\n```", guide, re.S)
        self.assertIsNotNone(prompt, "the assistant prompt snippet block is required")
        for needle in ("--reply-to", "todo:done:<id>"):
            with self.subTest(block="prompt snippet", needle=needle):
                self.assertIn(needle, prompt.group(1))
        positive = [b for b in section(guide, "验收").split("\n- ") if b.startswith("**Workflow 正向**")]
        self.assertEqual(len(positive), 1)
        self.assertIn("用 `--reply-to` 回复原待办消息并回 `todo:done:<id>`", positive[0])  # the check drives the reply too
        self.assertIn("在原待办的 Thread 里回复", guide)  # the Desktop / phone paragraph
        accept = [b for b in section(guide, "验收").split("\n- ") if "不在原 Thread" in b]
        self.assertEqual(len(accept), 1)
        for needle in ("todo:done:", "不触发", "pending"):
            with self.subTest(block="acceptance", needle=needle):
                self.assertIn(needle, accept[0])
        gaps = section(guide, "安全与已知缺口")
        rule = [b for b in gaps.split("\n- ") if "`e` tag" in b]
        self.assertEqual(len(rule), 1)
        for needle in ("必须是对该待办 Thread 的回复", "`--reply-to`", "Desktop", "忽略", "pending",
                       "reply_in_thread", "0.2.1", "被注入的助手仍能发出完成信号", "不防已被攻破的可信作者"):
            with self.subTest(block="known gaps", needle=needle):
                self.assertIn(needle, rule[0])
        for stale in ("按「频道内＋可信作者＋本脚本投递过的 id」认定", "Workflow 唤醒不在原 Thread", "不要求出现在原 Thread"):
            with self.subTest(stale=stale):
                self.assertNotIn(stale, guide)

    def test_guide_tells_the_reader_to_search_check_in_the_emoji_picker(self):
        """L1-PTS-095 指南「数据流」的完成一段写明：消息里默认 ✅ 的完成指引带「（表情里搜 check）」，只在第一个表情是 ✅ 时才带（别的表情不知道搜索名）；并有一句「在 Buzz Desktop 的表情选择器里搜 `check` 就能找到 ✅」。旧措辞（没有搜索提示）不再出现在指南里。"""
        guide = read(GUIDE)
        flow = section(guide, "数据流")
        finish = [b for b in flow.split("\n\n") if b.startswith("**完成有两种方式，并存**")]
        self.assertEqual(len(finish), 1)
        self.assertIn("在 Buzz Desktop 的表情选择器里搜 `check` 就能找到 ✅", finish[0])
        layout = [b for b in flow.split("\n\n") if b.startswith("版式尽量精简")]
        self.assertEqual(len(layout), 1)
        for needle in ("完成后点 {表情}", "（表情里搜 check）", "第一个", "只在第一个表情是 ✅", "✅"):
            with self.subTest(block="layout paragraph", needle=needle):
                self.assertIn(needle, layout[0])
        self.assertNotIn("完成后点 ✅ 或在本 Thread", guide)
        self.assertEqual(guide.count("在 Buzz Desktop 的表情选择器里搜 `check` 就能找到 ✅"), 1)

    def test_guide_describes_the_four_line_layout_and_when_the_project_line_appears(self):
        """L1-PTS-095C 指南「数据流」的版式叙述：典型待办 4 行，发起人并入第 1 行末尾（「标题 · 发起人」，用户名最多 32 个字符，标题先被截断、后缀总是完整），项目路径已经在网址里所以没有单独的项目路径行，只有没有合格链接时才多这一行（取代链接行的位置）；不可信文字的位置说明也随之更新。"""
        flow = section(read(GUIDE), "数据流")
        layout = [b for b in flow.split("\n\n") if b.startswith("版式尽量精简")]
        self.assertEqual(len(layout), 1)
        for needle in ("4 行", "「标题 · 发起人」", "第 1 行末尾", "32 个字符", "项目路径已经在网址里",
                       "没有合格链接时才会有项目路径行", "取代链接行的位置"):
            with self.subTest(block="layout paragraph", needle=needle):
                self.assertIn(needle, layout[0])
        untrusted = [b for b in flow.split("\n\n") if b.startswith("标题、正文摘要来自 GitLab")]
        self.assertEqual(len(untrusted), 1)
        for needle in ("第 1 行（标题和发起人）", "项目路径行", "以 `> ` 开头的摘要行"):
            with self.subTest(block="untrusted paragraph", needle=needle):
                self.assertIn(needle, untrusted[0])
        self.assertNotIn("项目路径与用户名原样显示在第 2 行", flow)
        self.assertNotIn("第 1 行（标题）和以", flow)

    def test_the_wake_text_does_not_carry_the_search_hint(self):
        """L1-PTS-095B 特征化：Workflow 唤醒文本不带「搜 check」提示（助手用 buzz CLI，不用表情选择器），仍写 `--emoji ✅`。"""
        wake = read(WORKFLOW)
        self.assertNotIn("check", wake)
        self.assertIn("--emoji ✅", wake)

    def test_guide_documents_the_check_mark_reaction_as_the_second_way_to_finish_a_todo(self):
        """L1-PTS-068E 指南里凡是讲完成的地方都补上 ✅ reaction：数据流（两种并存、只认待办消息本身）、助手 prompt 片段（点 ✅ 或 --reply-to 回复 todo:done）、验收（reaction 正向／负向两项：别的表情不触发、点在别的消息上不触发、撤销后不误标）、已知缺口（`todo.done_emojis`、被注入的助手也能点 ✅，与文字标记同类）；文字标记的规则一个字没删。"""
        guide = read(GUIDE)
        flow = section(guide, "数据流")
        for needle in ("reaction", "✅", "待办消息本身", "todo.done_emojis", "必须是对该待办 Thread 的回复"):
            with self.subTest(block="flow", needle=needle):
                self.assertIn(needle, flow)
        prompt = re.search(r"助手的 prompt 片段[^\n]*\n\n```text\n(.*?)\n```", guide, re.S)
        self.assertIsNotNone(prompt, "the assistant prompt snippet block is required")
        for needle in ("点 ✅", "--reply-to", "todo:done:<id>", "buzz reactions add"):
            with self.subTest(block="prompt snippet", needle=needle):
                self.assertIn(needle, prompt.group(1))
        accept = section(guide, "验收").split("\n- ")
        positive = [b for b in accept if b.startswith("**✅ reaction 正向**")]
        negative = [b for b in accept if b.startswith("**✅ reaction 负向**")]
        self.assertEqual((len(positive), len(negative)), (1, 1))
        for needle in ("buzz reactions add", "点 ✅", "mark_as_done", "变为 done"):
            with self.subTest(block="reaction positive", needle=needle):
                self.assertIn(needle, positive[0])
        for needle in ("别的表情", "👀", "点在别的消息上", "Thread 根", "撤销", "pending", "不触发"):
            with self.subTest(block="reaction negative", needle=needle):
                self.assertIn(needle, negative[0])
        gaps = section(guide, "安全与已知缺口")
        rule = [b for b in gaps.split("\n- ") if b.startswith("**完成 reaction")]
        self.assertEqual(len(rule), 1)
        for needle in ("`todo.done_emojis`", "待办消息本身", "不认", "Thread 根", "撤销", "👀", "被注入的助手也能点 ✅",
                       "同类", "本脚本投递过的"):
            with self.subTest(block="known gaps", needle=needle):
                self.assertIn(needle, rule[0])
        self.assertEqual(len([b for b in gaps.split("\n- ") if "`e` tag" in b]), 1)  # the text-marker bullet stays the one
        self.assertIn("点 ✅", section(guide, "在飞书里收到"))  # the Feishu paragraph: the way to finish from Buzz Desktop

    def test_guide_says_the_read_window_covers_the_300_second_slack(self):
        """L1-PTS-068F 指南「完成有两种方式」一段写明读取窗口从最老待确认投递往前 300 秒开始（覆盖判定下界的余量）、最多回看 30 天。"""
        paragraph = [ln for ln in read(GUIDE).split("\n") if ln.startswith("**完成有两种方式，并存**")]
        self.assertEqual(len(paragraph), 1)
        for needle in ("读取窗口", "最老", "往前 300 秒", "30 天"):
            with self.subTest(needle=needle):
                self.assertIn(needle, paragraph[0])


def bullet(block: str, prefix: str) -> str:
    """The single `- ` bullet of a section that starts with `prefix` (a bullet is everything up to the next `\\n- `)."""

    found = [b for b in block.split("\n- ") if b.lstrip("- ").startswith(prefix)]
    if len(found) != 1:
        raise AssertionError(f"exactly one bullet starting with {prefix!r} is required, found {len(found)}")
    return found[0]


class SkillGapsFromRealRunTest(unittest.TestCase):
    """Pitfalls hit in a real L4 run of the personal todo flow, written back into the skill (assertions stay in blocks)."""

    def assert_all(self, block: str, needles, label: str) -> None:
        for needle in needles:
            with self.subTest(block=label, needle=needle):
                self.assertIn(needle, block)

    def test_scripts_readme_documents_mint_agent_help_and_the_name_rule(self):
        """L1-PTS-095 scripts/README「mint-agent 用法」：`-h`／`--help` 只打印用法（不读 owner 密钥、不生成密钥）；名字规则 `[A-Za-z0-9][A-Za-z0-9._-]{0,62}`；以 `-` 开头或不合规 → stderr 报错、退出码 2、stdout 无密钥材料；**不要为试参数而铸密钥**（旧脚本把 `--help` 当名字真的铸了一对并打印 nsec）。表格里 mint-agent 一行同样点明 `--help` 与名字规则。"""
        text = read(SCRIPTS_README)
        usage = section(text, "mint-agent 用法")
        self.assert_all(usage, ("`-h`", "`--help`", "不读 owner 密钥", "不生成任何密钥", "[A-Za-z0-9][A-Za-z0-9._-]{0,62}",
                                "以 `-` 开头", "退出码 2", "stderr", "stdout", "没有任何密钥材料",
                                "不要为了试参数而铸密钥", "nsec"), "usage section")
        row = [ln for ln in text.split("\n") if ln.startswith("| `mint-agent.py")]
        self.assertEqual(len(row), 1)
        self.assert_all(row[0], ("--help", "[A-Za-z0-9][A-Za-z0-9._-]{0,62}"), "table row")

    def test_scripts_readme_separates_the_owner_wrapper_from_the_raw_elf(self):
        """L1-PTS-096 scripts/README 补一句 wrapper 区别：`~/.local/bin/buzz` 会加载 owner 密钥，测试与脚本／自动化里仍禁用；交互式 owner 操作（加／移成员、发 reaction／标记）在用户授权时可以用；典型流程里的「不要用 wrapper」注释指向这一节。"""
        text = read(SCRIPTS_README)
        wrapper = section(text, "`~/.local/bin/buzz` wrapper")
        self.assert_all(wrapper, ("`~/.local/bin/buzz`", "owner 密钥", "`~/.config/buzz/env`", "测试", "脚本", "自动化",
                                  "仍然禁用", "交互式", "用户授权", "加／移成员", "reaction", "原始 ELF"), "wrapper section")
        flow = section(text, "典型流程")
        self.assertIn("~/.local/bin/buzz", flow)
        self.assertIn("wrapper 与原始 ELF", flow)

    def test_guide_says_how_to_get_an_api_pat_and_when_an_ai_may_create_it(self):
        """L1-PTS-097 指南「三、必须人来做」的 PAT 条：自助端点 `POST /user/personal_access_tokens` 只允许 `k8s_proxy`（要 `api` 会 `scopes does not have a valid value`）；`api` 令牌要么本人在 GitLab UI 签，要么**实例管理员**用 `POST /users/:id/personal_access_tokens`（JSON body、`scopes` 是数组：`glab api --input -` 加 `Content-Type: application/json`，`-f scopes[]=api` 不当数组）；用户**明确授权**时 AI 可用其本地已登录的 `glab` 走管理员端点，令牌值只写进 0600 env、不打印、不进 argv、留不含密钥的 receipt；没有明确授权仍由本人在自己终端不回显写入。`glab` 默认指 gitlab.com：要 `--hostname gitlab.addx.ai` 或 `GITLAB_HOST=gitlab.addx.ai`，否则 401。"""
        human = section(read(GUIDE), "三、必须人来做")
        pat = bullet(human, "**签发 PAT 并写入 env**")
        self.assert_all(pat, (
            "POST /user/personal_access_tokens", "`k8s_proxy`", "scopes does not have a valid value", "GitLab UI",
            "User settings → Access tokens", "**实例管理员**", "POST /users/:id/personal_access_tokens", "JSON",
            "`scopes` 必须是数组", "glab api --input -", "Content-Type: application/json", "-f scopes[]=api",
            "用户明确授权", "本地已登录的 `glab`", "0600 env", "不打印", "argv", "receipt", "没有明确授权", "read -rs",
            "--hostname gitlab.addx.ai", "GITLAB_HOST=gitlab.addx.ai", "401", "gitlab.com"), "PAT bullet")
        self.assertNotIn("不要贴给 AI", pat)  # the value is never pasted into chat; that rule is worded as such
        counter = bullet(section(read(GUIDE), "反例"), "把本人的 PAT")
        self.assertIn("未获用户明确授权", counter)  # the anti-example no longer forbids the authorised path

    def test_guide_phase_zero_checks_members_and_removes_extra_agents_without_granting_done_rights(self):
        """L1-PTS-098 指南「二、AI 用本地权限直接完成」的阶段 0：先 `channels members` 核对；全公司共用、`respond_to=anyone` 的 agent（本机 `skill-dev`）会自动订阅每个把它加为 bot 成员的频道，进了个人频道就读得到所有待办（含 private 项目标题），成员集合门禁整轮失败关闭；用 `channels remove-member --channel <CH> --pubkey <hex>` 移出；**不要为了放行把它加进 `done_authors`**（那会给它完成权限）。"""
        local = section(read(GUIDE), "二、AI 用本地权限直接完成")
        member = bullet(local, "**阶段 0 先核对成员")
        self.assert_all(member, (
            "channels members --channel <CH>", "channels remove-member --channel <CH> --pubkey <hex>", "`skill-dev`",
            "`respond_to=anyone`", "自动订阅", "private 项目", "标题", "成员集合", "整轮失败关闭",
            "不要为了放行把它加进 `done_authors`", "完成权限"), "phase 0 members bullet")
        self.assertLess(local.index("**阶段 0 先核对成员"), local.index("阶段 0：手动"))  # checked before the first run

    def test_guide_names_the_publisher_display_name_and_both_relay_schemes(self):
        """L1-PTS-099 指南：发布者档案 `buzz users set-profile --name "GitLab 待办"` 的 `--name` 是显示名（飞书卡片标题「X 在 Y 提到了你」的 X 取它），在「二」里给命令、在「在飞书里收到」里点明；`BUZZ_RELAY_URL` 是 `https://` 或 `wss://` 都接受（`sync.validate_relay_url`；`ws`／`http` 仅限回环），不断言只能 wss。"""
        guide = read(GUIDE)
        local = section(guide, "二、AI 用本地权限直接完成")
        profile = bullet(local, "**发布者档案**")
        self.assert_all(profile, ('buzz users set-profile --name "GitLab 待办"', "`--name` 是**显示名**", "`<name>-todo`",
                                  "X 在 Y 提到了你"), "publisher profile bullet")
        feishu = section(guide, "在飞书里收到")
        self.assert_all(feishu, ('users set-profile --name "GitLab 待办"', "`--name` 就是**显示名**"), "feishu section")
        relay = bullet(local, "**relay 地址**")
        self.assert_all(relay, ("`BUZZ_RELAY_URL`", "`https://`", "`wss://`", "都接受", "`sync.validate_relay_url`",
                                "`ws`", "`http`", "回环"), "relay bullet")
        self.assertNotIn("只能", relay)

    def test_guide_acceptance_lists_the_reaction_cli_facts_verified_on_a_real_relay(self):
        """L1-PTS-100 指南「验收」一节补 reaction CLI 速查（真实 relay 已验证）：`buzz reactions add --event <hex> --emoji ✅`／`remove`／`get --event <hex>`；`buzz messages get --channel <CH> --kinds 7 --since <unix> --limit 200` 读 reaction 事件；事件只有一个 `e` tag、没有 `h`／`p` tag；撤销的 reaction 在扫描里读不到。"""
        accept = section(read(GUIDE), "验收")
        facts = bullet(accept, "**reaction CLI 速查")
        self.assert_all(facts, (
            "真实 relay", "buzz reactions add --event <hex> --emoji ✅", "buzz reactions remove", "buzz reactions get --event <hex>",
            "buzz messages get --channel <CH> --kinds 7 --since <unix> --limit 200", "只有一个 `e` tag", "没有 `h`／`p` tag",
            "撤销的 reaction 在扫描里读不到"), "reaction CLI bullet")

    def test_runbook_pat_bullet_matches_the_guide_and_relay_scheme_is_not_wss_only(self):
        """L1-PTS-101 runbook「边界」：PAT 条与指南一致（自助端点只允许 `k8s_proxy`；UI／实例管理员端点；用户明确授权时 AI 经本地 `glab`，令牌值只写进 0600 env、不打印、不进 argv、留 receipt；无授权则本人不回显写入）；不再有「不要让 AI 代签」的绝对措辞；`BUZZ_RELAY_URL` 写明 `https://` 与 `wss://` 都接受。"""
        limits = section(read(RUNBOOK), "边界")
        pat = bullet(limits, "PAT")
        self.assert_all(pat, (
            "`k8s_proxy`", "GitLab UI", "**实例管理员**", "POST /users/:id/personal_access_tokens", "用户明确授权",
            "本地已登录的 `glab`", "GITLAB_HOST=gitlab.addx.ai", "0600 env", "不打印", "argv", "receipt",
            "没有明确授权", "不回显", "personal-channel.md"), "runbook PAT bullet")
        self.assertNotIn("不要让 AI 代签", limits)
        self.assertIn("read -rs", limits)  # the no-echo recipe for the no-authorisation path stays
        env = bullet(limits, "专用 env 文件")
        self.assert_all(env, ("`https://`", "`wss://`", "都接受", "`sync.validate_relay_url`"), "runbook env bullet")


class HowtoPageTest(unittest.TestCase):
    """The how-to page (public/work-methods) mirrors the message layout and the wake text; it must not drift from them."""

    def test_the_chat_mock_and_the_feishu_preview_show_the_new_done_hint(self):
        """L1-PTS-093 howto 页里的 Buzz 聊天示意与飞书卡片预览都用新版式（「标题 · 发起人」并成第 1 行、没有项目路径行、新的完成指引行）；预览 = 压成单行后取前 120 字 + 「…」（按新措辞重取）；不再出现旧措辞。"""
        page = read(HOWTO)
        hint = "完成后点 ✅（表情里搜 check）或在本 Thread 回复 todo:done:123"
        body = re.search(r'<div class="body"><span class="ment">@jchen</span> (请你评审.*?)</div>', page)
        self.assertIsNotNone(body, "the chat mock of the todo message is required")
        lines = body.group(1).split("<br>")
        self.assertEqual(lines[-1], hint)
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines[0], "请你评审 · !45 修复登录超时 · alice")  # the author is the tail of line 1
        self.assertEqual(lines[1], "https://gitlab.addx.ai/group/project/-/merge_requests/45")
        self.assertNotIn("group/project · alice", page)  # and there is no separate project line any more
        header = re.search(r'<div class="hdr">(\[gitlab-todo:v1\]\[action:review_requested\][^<]*)</div>', page).group(1)
        flat = " ".join([*lines, header])
        preview = re.search(r'<div class="fs-preview">(.*?)</div>', page).group(1)
        self.assertEqual(preview, flat[:120] + "…")
        self.assertIn("完成后点 ✅（表情里搜 check）或在本 T", preview)  # the 120-character cut falls inside "Thread"
        self.assertEqual(page.count("表情里搜 check"), 2)  # the two places that show the message, and nowhere else
        self.assertNotIn("完成后在本 Thread 回复", page)

    def test_the_workflow_excerpt_is_the_template_text_word_for_word(self):
        """L1-PTS-094 howto 页里 Workflow 示例节选的唤醒文本与 personal-todo-wake.yaml 的 step 文本逐字一致（只有 <assistant-name>→jchen-assistant、<handling>→triage 两处占位替换；HTML 转义还原后比）。"""
        import html

        template_text = read(WORKFLOW).split("    text: >-\n", 1)[1]
        template_text = template_text.replace("<assistant-name>", "jchen-assistant").replace("<handling>", "triage")
        page = html.unescape(read(HOWTO))
        excerpt = page.split("    text: >-\n", 1)[1].split("</code></pre>", 1)[0]
        self.assertEqual(excerpt.rstrip("\n"), template_text.rstrip("\n"))


if __name__ == "__main__":
    unittest.main()
