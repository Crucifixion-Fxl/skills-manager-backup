"""Contract for the 2026-09-21 local-alignment write-back: five documents, each pinned to the fact it carries.

Every fact below was found on the owner's host on 2026-09-21 while bringing every local Buzz agent, Workflow and
background unit back in line with main.  The tests pin each fact to the document that carries it, so a later edit
cannot silently drop one of the traps.  Where a fact is also a code constant (the harness homes, the resolver's flags,
the helper's config version and keys, the launcher's env whitelist, the manifest keys, the sync CLI flags) the test
compares the document with the code instead of with a second copy of the value.  No secret value, key or identifier
belongs in these documents; the last class guards that.

Only the standard library is used: CI runs this directory with a bare ``python3`` (no git in that image, so the one
test that needs it is skipped there and runs locally).
"""

from __future__ import annotations

import calendar
import hashlib
import importlib.util
import json
import os
import re
import shlex
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path


SKILL = Path(__file__).resolve().parents[1]
REPO = SKILL.parents[1]
REFS = SKILL / "references"
SCRIPTS = SKILL / "scripts"
RUNTIME = REFS / "runtime-setup.md"
SCHEDULED = REFS / "scheduled-workflows.md"
FEISHU_SYNC = REFS / "feishu-group-sync.md"
SYSTEMD_README = REFS / "systemd" / "README.md"
RUNBOOK = REFS / "local-upgrade-runbook.md"
ENTRYPOINT = SKILL / "SKILL.md"
FAILOVER_PROFILES = REPO / "skills" / "harness-failover" / "assets" / "profiles.default.json"
REGISTER_SCRIPT = REFS / "scripts" / "feishu_register_agent_apps.sh"
RUN_AGENT = REFS / "scripts" / "run-agent.py"

SKILL_COPY_HEADING = "### 验证运行时实际加载的 Skill revision"
PERSISTENT_UNIT_HEADING = "### 用持久用户单元托管 Agent 进程"
WORKFLOW_NOTES_HEADING = "## Workflow 维护须知"
FEISHU_IDENTITY_HEADING = "## agent 的飞书身份"

# The facts each document must carry, kept as data so that a mutation check can delete them one at a time.
SKILL_COPY_FACTS = (
    "自己的配置目录",
    "owner 交互会话用的 `~/.claude`",
    "addx@addx-engineering",
    "不是 agent 读的",
    "插件 `addx@addx`",
    "marketplace `addx`",
    "CLAUDE_CONFIG_DIR",
    "CODEX_HOME",
    "claude-buzz plugin marketplace update addx && claude-buzz plugin update addx@addx",
    "命令里的 `claude-buzz` 换成 `claude-glm`",
    "CODEX_HOME=~/.codex-buzz codex plugin marketplace upgrade addx",
    "grok plugin update",
    "要重启 agent 才生效",
    "marketplace 源必须是 git 远端",
    "git@gitlab.addx.ai:engineering/skills.git",
    "detached worktree",
    "不能指向开发者的工作树目录",
    "当前 HEAD",
    "09-11 旧功能分支",
    "比更新前还旧",
    "plugin marketplace list",
    "`Source:`",
    "Git (",
    "a74b604b",
    "342 个提交",
    "Unknown skill: gitlab-pipeline-health",
    "local-upgrade-runbook.md",
    "先 `export`",
    "`git_commit_sha` 要等于",
)

TRANSIENT_UNIT_FACTS = (
    "安全后果",
    "/run/user/<uid>/systemd/transient/",
    "目录 0755，文件 0644",
    "整份环境",
    "同机其它用户都读得到",
    "持久单元 + 0600 env 文件",
    "例外",
    "个人助手",
    "personal-channel.md",
    "评估同机其它用户的风险",
    "轮换",
    "2026-09-21",
    "Superset 密码",
    "值不写进任何文档或 issue",
    "stat -c '%a %n'",
)

WORKFLOW_NOTES_FACTS = (
    "relay 0.2.1",
    "只会替换同一把 key 写的 Workflow",
    "第二条同 id 的定义",
    "workflows list",
    "原作者",
    "先和作者协调",
    "f1ab9984",
    "qlv",
    "03:06",
    "07:52",
    "canvas_alias",
    "`person`",
    "只返回 `accepted`，不生效",
    "RETIRED",
    "`workflows runs` 返回空",
    "触发消息的时间",
    "cron 的星期字段",
)

QR_FACTS = (
    "2026-09-21 补充",
    "约 2 分钟",
    "发码前先确认 owner 在飞书前",
    "白发一张",
    "发码前后各配一条文字",
    "用途、扫哪张、有效期",
    "当前目录内的相对路径",
    "绝对路径（以及带 `..` 的路径）会失败（返回 `ok:false`）",
    "cd <二维码所在目录> && lark-cli im +messages-send --as bot --user-id <owner> --image ./qr.png",
    "约三分之一次",
    "重跑同一个 agent",
    "没有自带重试",
    "--markdown",
    "每个链接前写 agent 名",
)

RUNBOOK_FACTS = {
    "## 1. release 钉在几处": (
        "适用的新功能默认启用",
        "静默跳过",
        "release_dir",
        "BUZZ_DESK_RUNNER_MANIFEST",
        "gitlab-buzz-sync-launch.sh",
        "gitlab_buzz_sync_timer.py",
        "两处都切到同一个 release",
        "buzz-feishu-<channel>.service",
        "gitlab-todo-sync-<name>.service",
        "buzz-agent-join.service",
        "buzz_send_with_responsible_mentions.py",
        "allowRead",
        "people_file",
        "BUZZ_RESPONSIBLE_CONFIG",
        "插件副本",
        "两种别放进同一个目录",
    ),
    "## 2. 改了什么，该动哪几处": (
        "git diff <旧 40 位> <新 40 位> -- skills/buzz-agent-setup/scripts skills/buzz-agent-setup/references/scripts",
        "不用于豁免 full convergence",
        "所有 pin 仍统一切新 SHA",
        "长期保留多个 release pin",
        "revision 仍统一",
    ),
    "## 3. 顺序：先做不破坏的部分": (
        "cat-file blob",
        "--no-replace-objects",
        "umask 022",
        "--extract",
        "chmod -R a-w",
        "audit_local_alignment.py",
        "/run/user/<uid>/systemd/transient/",
        "绝不读取其内容",
        "Buzz CLI",
        "相同的白名单 env",
        "--dry-run",
        "不要从自己的交互 shell 直接跑",
        "备份成 `<原名>.bak.<时间>`",
        "run-agent.py",
        "local-alignment-roles.json",
        "compare_local_alignment_receipts.py",
        "disable --now",
        "daemon-reload",
        "读 `status`",
    ),
    "## 4. 破坏性变更窗口：责任人 helper 配置 v1 ↔ v2": (
        "两边互不认",
        "旧 helper 只认 v1",
        "v2 helper 又不认 `canvas_alias`",
        "一起换、随即重启",
        "逐个 agent",
        "空闲",
        "立即重启",
        "canvas_alias",
        "person",
        "被它唤醒的那个 agent 切换之后",
        "别名表",
        "最后",
    ),
    "## 5. 验证": (
        "systemctl --user is-active buzz-local-<name>.service",
        "wc -c < <people_file>",
        "load_config",
        "importlib",
        "audit_local_alignment.py",
        "fail=0, unknown=0",
        "固定 Buzz CLI 哈希",
        "resolve_plugin_install.py",
        "git_commit_sha",
        "`errors` 为 0",
    ),
    "## 6. 回滚": (
        "不删、不覆盖",
        "*.bak.<时间>",
        "daemon-reload",
        "三样一起还原",
        "run-agent.py",
        "local-alignment-roles.json",
        "共享 launcher",
        "absent",
        "Canvas 别名表还在，才回得去",
        "旧目标 SHA",
    ),
}


def read(path: Path) -> str:
    if not path.is_file():
        raise AssertionError(f"{path.relative_to(REPO)} is required")
    return path.read_text(encoding="utf-8")


def section(text: str, heading: str) -> str:
    """One markdown section: the heading line up to the next heading of the same or a higher level (fences skipped)."""
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith(heading)), None)
    if start is None:
        raise AssertionError(f"missing section heading: {heading}")
    level = len(heading) - len(heading.lstrip("#"))
    end = len(lines)
    in_fence = False
    for i in range(start + 1, len(lines)):
        line = lines[i]
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue  # a `# comment` inside a code block is not a heading
        if line.startswith("#"):
            depth = len(line) - len(line.lstrip("#"))
            if depth <= level and line[depth:depth + 1] == " ":
                end = i
                break
    return "\n".join(lines[start:end])


def code_blocks(text: str) -> list[str]:
    """Fenced bash blocks (indentation of a list item is tolerated)."""
    blocks = []
    for match in re.finditer(r"^[ \t]*```(?:bash|sh)\n(.*?)^[ \t]*```", text, re.S | re.M):
        blocks.append("\n".join(line.strip() if not line.strip() else line for line in match.group(1).splitlines()))
    return blocks


def table_row(text: str, needle: str) -> str:
    row = next((line for line in text.splitlines() if line.startswith("|") and needle in line), None)
    if row is None:
        raise AssertionError(f"no table row mentions {needle}")
    return row


def index_of(text: str, marker: str) -> int:
    """Position of a marker, as a test failure (not a ValueError) when it is missing."""
    position = text.find(marker)
    if position < 0:
        raise AssertionError(f"missing: {marker}")
    return position


def load_script(name: str):
    path = SCRIPTS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"local_alignment_{name}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_python(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class ContractCase(unittest.TestCase):
    def assert_contains_all(self, text: str, label: str, required: tuple[str, ...]) -> None:
        for phrase in required:
            with self.subTest(document=label, requirement=phrase):
                self.assertIn(phrase, text)


class AgentSkillCopyDocsTest(ContractCase):
    """Doc 1: which copy of the skill an agent reads, and how it is updated."""

    def setUp(self) -> None:
        self.section = section(read(RUNTIME), SKILL_COPY_HEADING)

    def test_each_harness_reads_its_own_copy_and_the_update_commands_are_listed(self) -> None:
        self.assert_contains_all(self.section, "runtime-setup.md 插件副本", SKILL_COPY_FACTS)

    def test_the_owners_claude_dir_is_only_named_to_say_it_is_not_the_agents(self) -> None:
        for block in code_blocks(self.section):
            with self.subTest(block=block[:40]):
                self.assertNotIn("addx@addx-engineering", block)
                self.assertNotIn(".claude/plugins", block)

    def test_every_harness_home_and_wrapper_of_the_failover_profiles_is_covered(self) -> None:
        profiles = json.loads(read(FAILOVER_PROFILES))["profiles"]
        self.assertGreaterEqual(len(profiles), 4)
        for profile in profiles:
            if profile.get("home"):
                with self.subTest(profile=profile["id"], field="home"):
                    self.assertIn(profile["home"], self.section)
            if profile.get("wrapper"):
                with self.subTest(profile=profile["id"], field="wrapper"):
                    self.assertIn(Path(profile["wrapper"]).name, self.section)
        self.assertIn("~/.grok", self.section)  # grok's profile has no home key, its account lives in ~/.grok

    def test_the_registry_example_is_parameterized_and_runs_as_written(self) -> None:
        block = next((b for b in code_blocks(self.section) if "resolve_plugin_install.py" in b), None)
        self.assertIsNotNone(block, "the section needs the resolver example")
        self.assertIn('--registry "$CLAUDE_CONFIG_DIR/plugins/installed_plugins.json"', block)
        self.assertIn("export CLAUDE_CONFIG_DIR=", block)
        self.assertIn("--plugin-id addx@addx", block)
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp).resolve()
            install = home / ".claude-buzz" / "plugins" / "cache" / "addx" / "addx" / "abc123def456"
            for name in ("buzz-agent-setup", "gitlab-issue-sop"):
                skill = install / "skills" / name / "SKILL.md"
                skill.parent.mkdir(parents=True)
                skill.write_text(f"---\nname: {name}\n---\n", encoding="utf-8")
            record = {"installPath": str(install), "gitCommitSha": "a" * 40, "version": "abc123def456", "scope": "user"}
            registry = home / ".claude-buzz" / "plugins" / "installed_plugins.json"
            registry.write_text(json.dumps({"version": 2, "plugins": {"addx@addx": [record]}}), encoding="utf-8")
            done = subprocess.run(
                ["bash", "-euo", "pipefail", "-c", block],
                cwd=REPO,
                env={"HOME": str(home), "PATH": os.environ.get("PATH", "/usr/bin:/bin")},
                capture_output=True,
                text=True,
                timeout=60,
            )
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        receipt = json.loads(done.stdout.strip().splitlines()[-1])
        self.assertTrue(receipt["ok"], receipt)
        self.assertEqual(receipt["plugin_id"], "addx@addx")
        self.assertEqual(receipt["git_commit_sha"], "a" * 40)


class TransientUnitSecurityDocsTest(ContractCase):
    """Doc 5: a transient systemd-run unit is a reboot problem and a secret-exposure problem."""

    def setUp(self) -> None:
        self.section = section(read(RUNTIME), PERSISTENT_UNIT_HEADING)

    def test_the_security_consequence_is_written_next_to_the_reboot_one(self) -> None:
        self.assert_contains_all(self.section, "runtime-setup.md 瞬时单元", TRANSIENT_UNIT_FACTS)
        # the reboot consequence (pinned by test_agent_retirement_docs) is still there
        self.assertIn("重启机器后不会自动恢复", self.section)
        self.assertLess(index_of(self.section, "重启机器后不会自动恢复"), index_of(self.section, "安全后果"))

    def test_the_safe_form_stays_safe(self) -> None:
        # the persistent unit example must not carry the environment itself: no Environment= with a secret-looking name
        unit = next(block for block in re.findall(r"```ini\n(.*?)```", self.section, re.S) if "[Service]" in block)
        self.assertNotRegex(unit, r"(?im)^EnvironmentFile=")
        self.assertNotRegex(unit, r"(?im)^Environment=.*(KEY|TOKEN|SECRET|PASS)")


class WorkflowMaintenanceDocsTest(ContractCase):
    """Doc 3: update / delete / runs behave differently from their names on this relay."""

    def setUp(self) -> None:
        self.text = read(SCHEDULED)
        self.section = section(self.text, WORKFLOW_NOTES_HEADING)

    def test_update_delete_and_runs_caveats_are_written_with_their_evidence(self) -> None:
        self.assert_contains_all(self.section, "scheduled-workflows.md Workflow 维护须知", WORKFLOW_NOTES_FACTS)

    def test_the_section_sits_after_the_cron_section_and_does_not_repeat_it(self) -> None:
        self.assertLess(index_of(self.text, "## cron 的星期字段"), index_of(self.text, WORKFLOW_NOTES_HEADING))
        self.assertLess(index_of(self.text, WORKFLOW_NOTES_HEADING), index_of(self.text, "## Setup 必问席位"))
        self.assertNotIn("1=周日", self.section)  # the weekday convention lives in the cron section

    def test_the_retirement_cron_can_never_fire(self) -> None:
        found = re.search(r"cron 改成 `([^`]+)`", self.section)
        self.assertIsNotNone(found, "the section must give the never-firing cron")
        fields = found.group(1).split()
        self.assertEqual(len(fields), 5, found.group(1))
        _minute, _hour, day, month, weekday = fields
        # day-of-month 31 in February never happens; a restricted weekday would make many crons fire on either
        self.assertEqual(month, "2")
        self.assertEqual(weekday, "*")
        self.assertTrue(day.isdigit())
        self.assertGreater(int(day), max(calendar.monthrange(year, 2)[1] for year in range(2024, 2032)))

    def test_runtime_setup_points_its_delete_sentence_at_the_new_section(self) -> None:
        runtime = read(RUNTIME)
        delete_line = next(line for line in runtime.splitlines() if "workflows delete" in line and "accepted=true" in line)
        self.assertIn("Workflow 维护须知", delete_line)


class HeadlessBrowserQrDocsTest(ContractCase):
    """Doc 4: what the 2026-09-21 batch of Feishu app registrations added to the headless-browser notes."""

    def setUp(self) -> None:
        self.section = section(read(FEISHU_SYNC), FEISHU_IDENTITY_HEADING)

    def test_the_qr_and_scope_link_notes_are_written(self) -> None:
        self.assert_contains_all(self.section, "feishu-group-sync.md 「agent 的飞书身份」", QR_FACTS)

    def test_every_image_send_uses_a_path_inside_the_current_directory(self) -> None:
        lines = [line for line in self.section.splitlines() if "--image" in line]
        self.assertTrue(lines)
        for line in lines:
            with self.subTest(line=line[:60]):
                self.assertNotRegex(line, r"--image\s+/")  # never an absolute path
                self.assertNotRegex(line, r"--image\s+(\.\./|[A-Za-z0-9_-]+/)")  # nor one that leaves or descends
        send = next(line for line in lines if "lark-cli im +messages-send" in line)
        self.assertRegex(send, r"cd <[^>]+> && lark-cli im \+messages-send .*--image \./[A-Za-z0-9_.-]+")

    def test_the_relative_path_rule_is_the_same_limit_the_image_sync_section_states(self) -> None:
        boundary = section(read(FEISHU_SYNC), "## 边界")
        self.assertIn("`--image`", boundary)
        self.assertIn("只收相对当前目录的路径", boundary)

    def test_the_script_really_has_no_retry_and_really_reports_the_failure_text(self) -> None:
        # the document tells the reader to rerun: that stops being true the day the script retries by itself
        script = read(REGISTER_SCRIPT)
        self.assertIn("创建失败", script)
        self.assertNotRegex(script.lower(), r"retry|retries|重试|attempt")


class LocalUpgradeRunbookTest(ContractCase):
    """Doc 2: the new page listing every place a release is pinned, what to touch for what, and in which order."""

    def setUp(self) -> None:
        self.text = read(RUNBOOK)

    def test_entrypoint_and_runtime_setup_route_to_the_runbook(self) -> None:
        entry = read(ENTRYPOINT)
        row = table_row(entry, "local-upgrade-runbook.md")
        self.assertIn("references/local-upgrade-runbook.md", row)
        for word in ("release", "破坏性变更窗口", "回滚"):
            with self.subTest(word=word):
                self.assertIn(word, row)
        self.assertIn("local-upgrade-runbook.md", read(RUNTIME))
        self.assertLessEqual(len(entry.splitlines()), 500, "SKILL.md must stay within the 500-line budget")

    def test_each_section_carries_its_facts(self) -> None:
        self.assertIn("main 上的 **40 位 commit**", self.text)
        self.assertIn("~/.local/share/buzz-agent-setup/releases/<40 位>", self.text)
        self.assertIn("不用分支名、`latest` 或开发者的工作树", self.text)
        for heading, required in RUNBOOK_FACTS.items():
            body = section(self.text, heading)
            self.assert_contains_all(body, f"local-upgrade-runbook.md {heading}", required)

    def test_every_script_and_example_the_page_names_exists(self) -> None:
        names = set(re.findall(r"`([A-Za-z0-9_-]+\.(?:py|example\.json))`", self.text))
        self.assertGreaterEqual(len(names), 8, names)
        for name in sorted(names):
            with self.subTest(name=name):
                self.assertTrue(
                    (SCRIPTS / name).is_file() or (REFS / "scripts" / name).is_file(),
                    f"{name} is named in the runbook but is not shipped",
                )

    def test_the_diff_command_watches_directories_that_exist(self) -> None:
        for path in ("skills/buzz-agent-setup/scripts", "skills/buzz-agent-setup/references/scripts"):
            with self.subTest(path=path):
                self.assertTrue((REPO / path).is_dir())

    def test_the_mapping_rows_point_at_the_right_places(self) -> None:
        body = section(self.text, "## 2. 改了什么，该动哪几处")
        expected = {
            "`gitlab_buzz_route_reply.py`": ("release_dir", "unit 入口"),
            "`buzz_feishu_group_sync.py`": ("buzz-feishu-<channel>.service", "ExecStart"),
            "`gitlab_todo_sync.py`": ("gitlab-todo-sync-<name>.service", "ExecStart"),
            "`buzz_agent_join_requests.py`": ("buzz-agent-join.service", "timer"),
            "`buzz_acp_media_proxy.py`": ("agent", "摘要目录", "重启"),
            "`buzz_send_with_responsible_mentions.py`": ("prompt", "allowRead", "BUZZ_RESPONSIBLE_CONFIG", "重启"),
            "`SKILL.md`": ("插件副本", "重启"),
            "`provision_gitlab_agent_token.py`": ("不钉在任何地方",),
        }
        for needle, targets in expected.items():
            row = table_row(body, needle)
            for target in targets:
                with self.subTest(row=needle, target=target):
                    self.assertIn(target, row)

    def test_the_safe_steps_come_in_the_documented_order(self) -> None:
        body = section(self.text, "## 3. 顺序：先做不破坏的部分")
        order = [
            "cat-file blob",
            "audit_local_alignment.py",
            "--dry-run",
            "备份成 `<原名>.bak.<时间>`",
            "读 live 结果",
            "最终审计必须归零",
        ]
        positions = [index_of(body, marker) for marker in order]
        self.assertEqual(positions, sorted(positions), order)
        self.assertLess(
            index_of(body, "备份成 `<原名>.bak.<时间>`"),
            index_of(body, "把 [agent-prompt-contract.md]"),
        )
        audit_block = next(b for b in code_blocks(body) if "audit_local_alignment.py" in b)
        self.assertIn("/usr/bin/python3 -I", audit_block)
        self.assertIn("unset LD_PRELOAD", audit_block)
        self.assertIn("set +e", body)
        self.assertIn("AUDIT_RC", body)
        self.assertIn("PRE_RECEIPT", body)
        self.assertIn("POST_RECEIPT", body)
        self.assertIn("inventory_ids", body)
        self.assertIn("transient_services", body)
        self.assertIn("lookup_only_units", body)
        self.assertIn("不 import／执行 target release 的其它 Python module", body)
        self.assertIn("不执行 Claude／Codex／Grok wrapper", body)
        self.assertIn("`checks`↔`summary`↔`gaps`", body)

    def test_the_dry_run_uses_the_launchers_env_whitelist_and_the_syncs_real_flags(self) -> None:
        body = section(self.text, "## 3. 顺序：先做不破坏的部分")
        block = next(b for b in code_blocks(body) if "--dry-run" in b)
        self.assertIn("exec env -i", block)
        keep = re.search(r'KEEP="\s*(.*?)\s*\$\{GITLAB_TOKEN_ENVS', read(SYSTEMD_README))
        self.assertIsNotNone(keep, "the launcher template must still define KEEP")
        names = [name for name in keep.group(1).split() if not name.startswith("$")]
        self.assertIn("BUZZ_PRIVATE_KEY", names)
        for name in names:
            with self.subTest(whitelisted=name):
                self.assertRegex(block, rf"\b{name}=")
        helped = subprocess.run(
            [sys.executable, str(SCRIPTS / "gitlab_buzz_sync.py"), "--help"], capture_output=True, text=True, timeout=60
        ).stdout
        usage = helped.split("\n\n", 1)[0]  # the description below may still name a flag the parser no longer has
        self.assertTrue(usage.startswith("usage:"), usage)
        for flag in ("--config", "--state-dir", "--dry-run"):
            with self.subTest(flag=flag):
                self.assertIn(flag, usage)
                self.assertIn(flag, block)

    @unittest.skipUnless(
        shutil.which("git") and (REPO / ".git").exists(),
        "needs git and a git checkout (the CI image has no git)",
    )
    def test_the_release_commands_build_the_layout_the_page_describes(self) -> None:
        body = section(self.text, "## 3. 顺序：先做不破坏的部分")
        block = next(b for b in code_blocks(body) if "git" in b and "cat-file blob" in b)
        self.assertIn("set -euo pipefail", block)
        source_branch = subprocess.run(
            ["git", "-C", str(REPO), "branch", "--show-current"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        self.assertTrue(source_branch)
        script = block.replace(
            "SOURCE_REMOTE=git@gitlab.addx.ai:engineering/skills.git",
            f"SOURCE_REMOTE={shlex.quote(str(REPO))}",
        ).replace("TARGET_REF=refs/heads/main", "TARGET_REF=HEAD").replace(
            "--branch main", f"--branch {shlex.quote(source_branch)}"
        )
        self.assertGreaterEqual(block.count("/usr/bin/env -i"), 4)
        with tempfile.TemporaryDirectory() as unsafe_home:
            Path(unsafe_home).chmod(0o770)
            rejected_home = subprocess.run(
                ["bash", "-c", script],
                env={"HOME": unsafe_home, "PATH": "/usr/bin:/bin"},
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertNotEqual(0, rejected_home.returncode)
            self.assertFalse((Path(unsafe_home) / ".local").exists())
        with tempfile.TemporaryDirectory() as tmp:
            try:
                done = subprocess.run(
                    ["bash", "-c", script],
                    env={
                        "HOME": tmp,
                        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                        "GIT_CONFIG_COUNT": "1",
                        "GIT_CONFIG_KEY_0": "url.file:///definitely/missing.insteadOf",
                        "GIT_CONFIG_VALUE_0": str(REPO),
                        "GIT_SSH_COMMAND": "/bin/false",
                    },
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                releases = Path(tmp) / ".local" / "share" / "buzz-agent-setup" / "releases"
                (release,) = [
                    path for path in releases.iterdir() if re.fullmatch(r"[0-9a-f]{40}", path.name)
                ]
                self.assertRegex(release.name, r"^[0-9a-f]{40}$")
                manifest = json.loads(read(release / ".release-manifest.json"))
                self.assertEqual(release.name, manifest["commit"])
                self.assertIn("scripts/audit_local_alignment.py", manifest["files"])
                for relative, record in manifest["files"].items():
                    deployed = release / relative
                    self.assertEqual(stat.S_IMODE(deployed.stat().st_mode), record["mode"])
                    self.assertEqual(
                        hashlib.sha256(deployed.read_bytes()).hexdigest(),
                        record["sha256"],
                    )
                for path in ("scripts/gitlab_buzz_sync.py", "scripts/gitlab_buzz_desk_runner.py",
                             "scripts/gitlab_todo_sync.py", "scripts/buzz_send_with_responsible_mentions.py",
                             "references/scripts/nostrkit.py"):
                    with self.subTest(path=path):
                        self.assertTrue((release / path).is_file())
                self.assertEqual(release.stat().st_mode & 0o222, 0, "the release must be read-only")
                verified = subprocess.run(
                    [
                        "/usr/bin/python3",
                        "-I",
                        "-c",
                        (
                            "import importlib.util,sys; from pathlib import Path; "
                            "spec=importlib.util.spec_from_file_location('audit',sys.argv[1]); "
                            "module=importlib.util.module_from_spec(spec); "
                            "spec.loader.exec_module(module); "
                            "auditor=module.Auditor(Path(sys.argv[2]),sys.argv[3]); "
                            "raise SystemExit(0 if auditor.validate_release_manifest(Path(sys.argv[4])) else 1)"
                        ),
                        str(release / "scripts/audit_local_alignment.py"),
                        tmp,
                        release.name,
                        str(release),
                    ],
                    env={"HOME": tmp, "PATH": "/usr/bin:/bin"},
                    capture_output=True,
                    text=True,
                    timeout=120,
                    check=False,
                )
                self.assertEqual(0, verified.returncode, verified.stdout + verified.stderr)
                release.chmod(0o755)
                manifest_path = release / ".release-manifest.json"
                manifest_path.chmod(0o644)
                manifest_path.unlink()
                drifted = release / "scripts/gitlab_buzz_sync.py"
                drifted.chmod(0o644)
                drifted.write_text(
                    drifted.read_text(encoding="utf-8") + "# not in source commit\n",
                    encoding="utf-8",
                )
                rejected = subprocess.run(
                    [
                        "/usr/bin/python3",
                        "-I",
                        str(release / "scripts/build_release_manifest.py"),
                        "--release",
                        str(release),
                        "--commit",
                        release.name,
                        "--source-repo",
                        str(REPO),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=120,
                    check=False,
                )
                self.assertEqual(2, rejected.returncode)
                duplicate = subprocess.run(
                    ["bash", "-c", script],
                    env={"HOME": tmp, "PATH": os.environ.get("PATH", "/usr/bin:/bin")},
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                self.assertNotEqual(duplicate.returncode, 0)
                self.assertFalse(list(releases.glob(".release-*")))
            finally:
                subprocess.run(["chmod", "-R", "u+w", tmp], check=False)

        with tempfile.TemporaryDirectory() as tmp:
            try:
                restrictive = subprocess.run(
                    ["bash", "-c", "umask 077\n" + script],
                    env={"HOME": tmp, "PATH": os.environ.get("PATH", "/usr/bin:/bin")},
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                self.assertEqual(
                    restrictive.returncode,
                    0,
                    restrictive.stdout + restrictive.stderr,
                )
            finally:
                subprocess.run(["chmod", "-R", "u+w", tmp], check=False)

        failed_script = "\n".join(
            (line[: len(line) - len(line.lstrip())] + "false \\")
            if " cat-file blob " in line
            else line
            for line in script.splitlines()
        )
        with tempfile.TemporaryDirectory() as tmp:
            try:
                failed = subprocess.run(
                    ["bash", "-c", failed_script],
                    env={"HOME": tmp, "PATH": os.environ.get("PATH", "/usr/bin:/bin")},
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                self.assertNotEqual(failed.returncode, 0)
                releases = Path(tmp) / ".local/share/buzz-agent-setup/releases"
                self.assertFalse(
                    [path for path in releases.iterdir() if re.fullmatch(r"[0-9a-f]{40}", path.name)]
                )
                self.assertFalse(list(releases.glob(".release-*")))
                self.assertFalse(list(releases.glob(".source.*")))
            finally:
                subprocess.run(["chmod", "-R", "u+w", tmp], check=False)

        clone_failed_script = re.sub(
            r"(?m)^\s*SOURCE_REMOTE=.*$",
            "SOURCE_REMOTE=/definitely/missing/skills.git",
            script,
        )
        with tempfile.TemporaryDirectory() as tmp:
            clone_failed = subprocess.run(
                ["bash", "-c", clone_failed_script],
                env={"HOME": tmp, "PATH": "/usr/bin:/bin"},
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertNotEqual(0, clone_failed.returncode)
            releases = Path(tmp) / ".local/share/buzz-agent-setup/releases"
            self.assertFalse(list(releases.glob(".source.*")))

    @unittest.skipUnless(shutil.which("git"), "needs git")
    def test_manifest_builder_ignores_replace_refs_and_archive_attributes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repo = root / "repo"
            repo.mkdir(mode=0o700)
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            subprocess.run(
                ["git", "-C", str(repo), "config", "user.email", "test@example.test"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(repo), "config", "user.name", "Test"],
                check=True,
            )
            source = repo / "skills/buzz-agent-setup/SKILL.md"
            source.parent.mkdir(parents=True)
            original = "---\nname: buzz-agent-setup\n---\n$Format:%H$\n"
            source.write_text(original, encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
            subprocess.run(
                ["git", "-C", str(repo), "commit", "-q", "-m", "original"],
                check=True,
            )
            target = subprocess.run(
                ["git", "-C", str(repo), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            source.write_text("replacement\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
            subprocess.run(
                ["git", "-C", str(repo), "commit", "-q", "-m", "replacement"],
                check=True,
            )
            replacement = subprocess.run(
                ["git", "-C", str(repo), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            subprocess.run(
                ["git", "-C", str(repo), "replace", target, replacement],
                check=True,
            )
            attributes = repo / ".git/info/attributes"
            attributes.parent.mkdir(parents=True, exist_ok=True)
            attributes.write_text(
                "skills/buzz-agent-setup/SKILL.md export-subst\n",
                encoding="utf-8",
            )
            release = root / "release"
            release.mkdir(mode=0o700)
            (release / "SKILL.md").write_text(original, encoding="utf-8")
            completed = subprocess.run(
                [
                    "/usr/bin/python3",
                    "-I",
                    str(SCRIPTS / "build_release_manifest.py"),
                    "--release",
                    str(release),
                    "--commit",
                    target,
                    "--source-repo",
                    str(repo),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            manifest = json.loads(read(release / ".release-manifest.json"))
            self.assertEqual(
                hashlib.sha256(original.encode()).hexdigest(),
                manifest["files"]["SKILL.md"]["sha256"],
            )

    def test_manifest_builder_rejects_group_writable_source_ancestor(self) -> None:
        builder = load_python(
            SCRIPTS / "build_release_manifest.py",
            "strict_release_manifest_builder",
        )
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            parent = root / "private-group-writable"
            repo = parent / "repo"
            repo.mkdir(parents=True)
            parent.chmod(0o770)
            repo.chmod(0o700)
            self.assertFalse(builder.trusted_directory_chain(repo, os.geteuid()))

    def test_manifest_builder_rejects_one_oversized_release_file(self) -> None:
        builder = load_python(
            SCRIPTS / "build_release_manifest.py",
            "bounded_release_manifest_builder",
        )
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            oversized = root / "oversized.bin"
            with oversized.open("wb") as handle:
                handle.truncate(builder.MAX_FILE_BYTES + 1)
            with mock.patch.object(builder, "archive_records") as archive:
                with self.assertRaisesRegex(ValueError, "oversized file"):
                    builder.build(root, "a" * 40, root)
            archive.assert_not_called()

    def test_manifest_builder_maps_git_timeout_to_stable_exit_two(self) -> None:
        builder = load_python(
            SCRIPTS / "build_release_manifest.py",
            "timeout_release_manifest_builder",
        )
        argv = [
            "build_release_manifest.py",
            "--release",
            "/unavailable",
            "--commit",
            "a" * 40,
            "--source-repo",
            "/unavailable",
            "--extract",
        ]
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(
                builder,
                "materialize",
                side_effect=subprocess.TimeoutExpired(["git"], 30),
            ),
        ):
            self.assertEqual(2, builder.main())

    def test_the_layout_claim_matches_how_the_scripts_find_each_other(self) -> None:
        sync = read(SCRIPTS / "gitlab_buzz_sync.py")
        self.assertRegex(sync, r'REFERENCE_SCRIPTS = Path\(__file__\)\.resolve\(\)\.parents\[1\] / "references" / "scripts"')
        runner = read(SCRIPTS / "gitlab_buzz_desk_runner.py")
        self.assertIn('release_dir / "scripts" / name', runner)
        self.assertIn("release_dir", load_script("gitlab_buzz_desk_runner").MANIFEST_KEYS)

    def test_the_v1_v2_facts_match_the_helper_and_its_example(self) -> None:
        helper_source = read(SCRIPTS / "buzz_send_with_responsible_mentions.py")
        version = re.search(r'config\.get\("version"\) != (\d+)', helper_source)
        self.assertIsNotNone(version, "the helper must still pin one config version")
        window = section(self.text, "## 4. 破坏性变更窗口：责任人 helper 配置 v1 ↔ v2")
        self.assertIn(f"`version` 是整数 `{version.group(1)}`", window)
        self.assertIn("`channels` 由对象改成 UUID 数组", window)
        self.assertIn("新增 `people_file`", window)
        helper = load_script("buzz_send_with_responsible_mentions")
        self.assertIn("people_file", helper.CONFIG_KEYS)
        example = json.loads(read(REFS / "scripts" / "buzz-responsible-mentions.example.json"))
        self.assertEqual(str(example["version"]), version.group(1))
        self.assertIsInstance(example["channels"], list)
        self.assertIn("people_file", example)
        self.assertNotIn("canvas_alias", json.dumps(example))

    def test_the_destructive_window_is_ordered(self) -> None:
        window = section(self.text, "## 4. 破坏性变更窗口：责任人 helper 配置 v1 ↔ v2")
        steps = ["备份 prompt、配置和沙箱 settings", "一次装好", "立即重启", "按第 5 节验证"]
        positions = [index_of(window, step) for step in steps]
        self.assertEqual(positions, sorted(positions), steps)
        # Workflows follow their agent; the Canvas alias table goes last of all
        workflow = index_of(window, "Workflow 正文里的 `canvas_alias` locator 改成 `person`")
        canvas = index_of(window, "Canvas 里的别名表**最后**才删")
        self.assertLess(positions[-1], workflow)
        self.assertLess(workflow, canvas)

    def test_the_previous_manual_gap_is_now_an_executable_gate(self) -> None:
        self.assertIn("scripts/audit_local_alignment.py", self.text)
        self.assertIn("任一 `fail` 或 `unknown` 都返回非零", self.text)
        self.assertTrue((SCRIPTS / "audit_local_alignment.py").is_file())


class NewDocsCarryNoSecretsTest(unittest.TestCase):
    """Values never go into these documents (the evidence names files, modes and counts, not secrets or key ids)."""

    PATTERNS = (
        r"\b[0-9a-f]{40,}\b",  # a full commit id, key or digest
        r"\bnsec1[0-9a-z]{20,}",
        r"\bnpub1[0-9a-z]{20,}",
        r"glpat-[A-Za-z0-9_-]{10,}",
        r"Bearer\s+[A-Za-z0-9._-]{20,}",
        r"\bsk-[A-Za-z0-9]{20,}",
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    )

    def test_no_secret_shaped_value_in_the_new_material(self) -> None:
        material = {
            "local-upgrade-runbook.md": read(RUNBOOK),
            "runtime-setup.md 插件副本": section(read(RUNTIME), SKILL_COPY_HEADING),
            "runtime-setup.md 瞬时单元": section(read(RUNTIME), PERSISTENT_UNIT_HEADING),
            "scheduled-workflows.md": section(read(SCHEDULED), WORKFLOW_NOTES_HEADING),
            "feishu-group-sync.md": section(read(FEISHU_SYNC), FEISHU_IDENTITY_HEADING),
        }
        for label, text in material.items():
            for pattern in self.PATTERNS:
                with self.subTest(document=label, pattern=pattern):
                    self.assertIsNone(re.search(pattern, text), f"{label} matches {pattern}")


class CanonicalAgentLauncherTest(unittest.TestCase):
    def test_documented_isolated_interpreter_ignores_hostile_python_env(self) -> None:
        documented = Path("/usr/bin/python3")
        interpreter = documented if documented.is_file() else Path(sys.executable)
        completed = subprocess.run(
            [str(interpreter), "-I", str(RUN_AGENT)],
            env={
                "PATH": "/usr/bin:/bin",
                "PYTHONHOME": "/definitely/missing",
                "PYTHONPATH": "/hostile",
            },
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(2, completed.returncode, completed.stderr)
        self.assertIn("usage: run-agent.py", completed.stderr)
        self.assertNotIn("Fatal Python error", completed.stderr)

    def test_launcher_sanitizes_parent_environment_and_preserves_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp).resolve()
            agents = home / ".config/buzz/agents"
            workdir = home / "buzz-agent-work/demo"
            safe_bin = home / ".local/bin"
            agents.mkdir(parents=True)
            (workdir / ".git").mkdir(parents=True)
            safe_bin.mkdir(parents=True)
            for path in (
                home / ".config",
                home / ".config/buzz",
                agents,
                home / "buzz-agent-work",
                workdir,
                home / ".local",
                safe_bin,
            ):
                path.chmod(0o755)
            (home / "buzz-agent-work").chmod(0o755)
            workdir.chmod(0o755)
            safe_bin.chmod(0o755)
            (safe_bin / "buzz-acp").symlink_to("/usr/bin/true")
            buzz_acp_digest = hashlib.sha256(
                Path("/usr/bin/true").read_bytes()
            ).hexdigest()
            executables = {}
            for name in ("media-proxy", "claude-agent-acp", "codex-acp", "buzz", "claude-buzz"):
                path = home / ".local/lib/buzz-agents" / name
                path.parent.mkdir(parents=True, exist_ok=True)
                (home / ".local/lib").chmod(0o755)
                path.parent.chmod(0o755)
                path.write_text("fixture\n", encoding="utf-8")
                path.chmod(0o500)
                executables[name] = path
            env_file = agents / "demo.env"
            (home / ".claude-buzz").mkdir(mode=0o700)
            env_file.write_text(
                "BUZZ_RELAY_URL='wss://relay.example.test'\n"
                f"AGENT_WORKDIR='{workdir}'\n"
                f"BUZZ_AGENT_SAFE_PATH='{safe_bin}:/usr/bin'\n"
                "BUZZ_PRIVATE_KEY=$OWNER_TOKEN\n"
                "BUZZ_ACP_AGENT_OWNER=owner\n"
                "BUZZ_ACP_BINARY=/usr/bin/true\n"
                f"BUZZ_ACP_BINARY_SHA256={buzz_acp_digest}\n"
                f"BUZZ_ACP_AGENT_COMMAND='{executables['media-proxy']}'\n"
                f"BUZZ_ACP_MEDIA_ADAPTER_COMMAND='{executables['claude-agent-acp']}'\n"
                f"BUZZ_ACP_MEDIA_BUZZ_CLI='{executables['buzz']}'\n"
                f"HARNESS_CLAUDE_WRAPPER='{executables['claude-buzz']}'\n"
                f"CLAUDE_CODE_EXECUTABLE='{executables['claude-buzz']}'\n"
                f"CLAUDE_CONFIG_DIR='{home / '.claude-buzz'}'\n"
                f"BUZZ_ACP_SYSTEM_PROMPT_FILE='{agents / 'demo.prompt.md'}'\n"
                f"BUZZ_RESPONSIBLE_CONFIG='{agents / 'demo.json'}'\n",
                encoding="utf-8",
            )
            env_file.chmod(0o600)
            launcher = load_python(RUN_AGENT, "canonical_run_agent")
            with mock.patch.dict(
                os.environ,
                {
                    "OWNER_TOKEN": "parent-secret-must-not-expand",
                    "HOSTILE_MARKER": "must-not-survive",
                },
            ):
                _, binary, argv, environment, digest = launcher.prepare_launch(
                    "demo", home=home, uid=os.geteuid(), username="fixture-user"
                )
            self.assertEqual(binary, Path("/usr/bin/true"))
            self.assertEqual(argv[1:], ("--relay-url", "wss://relay.example.test"))
            self.assertEqual(digest, buzz_acp_digest)
            self.assertEqual(environment["HOME"], str(home))
            self.assertNotIn("HOSTILE_MARKER", environment)
            self.assertEqual(environment["BUZZ_PRIVATE_KEY"], "$OWNER_TOKEN")
            self.assertNotIn("parent-secret-must-not-expand", environment.values())

            agents.chmod(0o770)
            with self.assertRaises(launcher.LauncherError):
                launcher.prepare_launch(
                    "demo", home=home, uid=os.geteuid(), username="fixture-user"
                )
            agents.chmod(0o755)

            original = env_file.read_text(encoding="utf-8")
            unsafe_bin = home / "unsafe-bin"
            unsafe_bin.mkdir()
            unsafe_bin.chmod(0o777)
            env_file.write_text(
                original.replace(
                    f"BUZZ_AGENT_SAFE_PATH='{safe_bin}:/usr/bin'",
                    f"BUZZ_AGENT_SAFE_PATH='{unsafe_bin}:/usr/bin'",
                ),
                encoding="utf-8",
            )
            with self.assertRaises(launcher.LauncherError):
                launcher.prepare_launch(
                    "demo", home=home, uid=os.geteuid(), username="fixture-user"
                )

            codex_home = home / ".codex-buzz"
            codex_home.mkdir()
            codex_home.chmod(0o755)
            codex_path = safe_bin / "codex"
            codex_path.write_text("fixture\n", encoding="utf-8")
            codex_path.chmod(0o500)
            codex_env = original.replace(
                str(executables["claude-agent-acp"]),
                str(executables["codex-acp"]),
            ) + f"CODEX_HOME={codex_home}\nCODEX_PATH={codex_path}\n"
            env_file.write_text(codex_env, encoding="utf-8")
            _, _, _, codex_environment, _ = launcher.prepare_launch(
                "demo", home=home, uid=os.geteuid(), username="fixture-user"
            )
            self.assertEqual(str(codex_home), codex_environment["CODEX_HOME"])
            self.assertEqual(str(codex_path), codex_environment["CODEX_PATH"])
            env_file.write_text(
                codex_env.replace(str(codex_path), "$HOME/.local/bin/codex"),
                encoding="utf-8",
            )
            with self.assertRaises(launcher.LauncherError):
                launcher.prepare_launch(
                    "demo", home=home, uid=os.geteuid(), username="fixture-user"
                )

            outside = home / "outside"
            (outside / ".git").mkdir(parents=True)
            outside.chmod(0o755)
            env_file.write_text(
                original.replace(
                    f"AGENT_WORKDIR='{workdir}'", f"AGENT_WORKDIR='{outside}'"
                ),
                encoding="utf-8",
            )
            with self.assertRaises(launcher.LauncherError):
                launcher.prepare_launch(
                    "demo", home=home, uid=os.geteuid(), username="fixture-user"
                )

            other_wrapper = home / ".local/lib/buzz-agents/other-claude"
            other_wrapper.write_text("fixture\n", encoding="utf-8")
            other_wrapper.chmod(0o500)
            env_file.write_text(
                original.replace(
                    f"HARNESS_CLAUDE_WRAPPER='{executables['claude-buzz']}'",
                    f"HARNESS_CLAUDE_WRAPPER='{other_wrapper}'",
                ),
                encoding="utf-8",
            )
            with self.assertRaises(launcher.LauncherError):
                launcher.prepare_launch(
                    "demo", home=home, uid=os.geteuid(), username="fixture-user"
                )

    def test_launcher_rejects_command_syntax_without_executing_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp).resolve()
            agents = home / ".config/buzz/agents"
            sentinel = home / "must-not-exist"
            agents.mkdir(parents=True)
            env_file = agents / "demo.env"
            env_file.write_text(
                f"EXTRA=$(touch {sentinel})\n",
                encoding="utf-8",
            )
            env_file.chmod(0o600)
            launcher = load_python(RUN_AGENT, "canonical_run_agent_reject")
            with self.assertRaises(launcher.LauncherError):
                launcher._read_env(env_file, os.geteuid())
            self.assertFalse(sentinel.exists())

    def test_launcher_rejects_nul_and_noncanonical_binary_pin(self) -> None:
        launcher = load_python(RUN_AGENT, "canonical_run_agent_literal_safety")
        with self.assertRaises(launcher.LauncherError):
            launcher._literal("bad\x00value")
        with tempfile.TemporaryDirectory() as tmp:
            alias = Path(tmp) / "buzz-acp"
            alias.symlink_to("/usr/bin/true")
            with self.assertRaises(launcher.LauncherError):
                launcher._trusted_path(
                    str(alias), os.geteuid(), executable=True, canonical=True
                )

    def test_launcher_rejects_an_unpinned_shebang_interpreter_chain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / "buzz-acp"
            binary.write_text("#!/bin/sh\nprintf fd-exec-ok\n", encoding="utf-8")
            binary.chmod(0o500)
            launcher = load_python(RUN_AGENT, "canonical_run_agent_elf_only")
            with self.assertRaises(launcher.LauncherError):
                launcher._open_binary(binary, os.geteuid(), None)

    def test_launcher_rejects_malformed_elf_and_execs_verified_fd(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / "buzz-acp"
            binary.write_bytes(b"\x7fELF" + b"\x00" * 128)
            binary.chmod(0o500)
            launcher = load_python(RUN_AGENT, "canonical_run_agent_fd_exec")
            digest = hashlib.sha256(binary.read_bytes()).hexdigest()
            with self.assertRaises(launcher.LauncherError):
                launcher._open_binary(binary, os.geteuid(), digest)

            unsafe_parent = Path(tmp) / "cross-uid-writable"
            unsafe_parent.mkdir()
            unsafe_parent.chmod(0o777)
            unsafe_child = unsafe_parent / "adapter"
            unsafe_child.write_text("fixture\n", encoding="utf-8")
            unsafe_child.chmod(0o500)
            with self.assertRaises(launcher.LauncherError):
                launcher._trusted_path(
                    str(unsafe_child),
                    os.geteuid(),
                    executable=True,
                )

            group_writable_parent = Path(tmp) / "private-group-writable"
            group_writable_parent.mkdir()
            group_writable_parent.chmod(0o770)
            group_writable_child = group_writable_parent / "adapter"
            group_writable_child.write_text("fixture\n", encoding="utf-8")
            group_writable_child.chmod(0o500)
            with self.assertRaises(launcher.LauncherError):
                launcher._trusted_path(
                    str(group_writable_child),
                    os.geteuid(),
                    executable=True,
                )

            bad_entry_data = bytearray(Path("/usr/bin/true").read_bytes())
            struct.pack_into("<H", bad_entry_data, 54, 64)
            bad_entry_size = Path(tmp) / "bad-entry-size"
            bad_entry_size.write_bytes(bad_entry_data)
            bad_entry_size.chmod(0o500)
            with self.assertRaises(launcher.LauncherError):
                launcher._open_binary(
                    bad_entry_size,
                    os.geteuid(),
                    hashlib.sha256(bad_entry_size.read_bytes()).hexdigest(),
                )

            no_load_data = bytearray(Path("/usr/bin/true").read_bytes())
            program_offset = struct.unpack_from("<Q", no_load_data, 32)[0]
            program_size = struct.unpack_from("<H", no_load_data, 54)[0]
            program_count = struct.unpack_from("<H", no_load_data, 56)[0]
            for index in range(program_count):
                offset = program_offset + index * program_size
                if struct.unpack_from("<I", no_load_data, offset)[0] == 1:
                    struct.pack_into("<I", no_load_data, offset, 0)
            no_load = Path(tmp) / "no-load-segments"
            no_load.write_bytes(no_load_data)
            no_load.chmod(0o500)
            with self.assertRaises(launcher.LauncherError):
                launcher._open_binary(
                    no_load,
                    os.geteuid(),
                    hashlib.sha256(no_load.read_bytes()).hexdigest(),
                )

            load_offsets = [
                program_offset + index * program_size
                for index in range(program_count)
                if struct.unpack_from(
                    "<I", bad_entry_data, program_offset + index * program_size
                )[0]
                == 1
            ]
            self.assertTrue(load_offsets)

            def reject_mutated_elf(name: str, data: bytearray) -> None:
                target = Path(tmp) / name
                target.write_bytes(data)
                target.chmod(0o500)
                with self.assertRaises(launcher.LauncherError):
                    launcher._open_binary(
                        target,
                        os.geteuid(),
                        hashlib.sha256(target.read_bytes()).hexdigest(),
                    )

            bad_sizes = bytearray(Path("/usr/bin/true").read_bytes())
            memory_size = struct.unpack_from("<Q", bad_sizes, load_offsets[0] + 40)[0]
            struct.pack_into("<Q", bad_sizes, load_offsets[0] + 32, memory_size + 1)
            reject_mutated_elf("load-filesz-over-memsz", bad_sizes)

            bad_alignment = bytearray(Path("/usr/bin/true").read_bytes())
            struct.pack_into("<Q", bad_alignment, load_offsets[0] + 48, 3)
            reject_mutated_elf("load-bad-alignment", bad_alignment)

            out_of_bounds = bytearray(Path("/usr/bin/true").read_bytes())
            struct.pack_into("<Q", out_of_bounds, load_offsets[0] + 8, len(out_of_bounds))
            struct.pack_into("<Q", out_of_bounds, load_offsets[0] + 32, 2)
            struct.pack_into("<Q", out_of_bounds, load_offsets[0] + 40, 2)
            reject_mutated_elf("load-out-of-bounds", out_of_bounds)

            no_executable_load = bytearray(Path("/usr/bin/true").read_bytes())
            for offset in load_offsets:
                flags = struct.unpack_from("<I", no_executable_load, offset + 4)[0]
                struct.pack_into("<I", no_executable_load, offset + 4, flags & ~0x1)
            reject_mutated_elf("entry-not-executable", no_executable_load)

            descriptor = launcher._open_binary(
                Path("/usr/bin/true"),
                os.geteuid(),
                hashlib.sha256(Path("/usr/bin/true").read_bytes()).hexdigest(),
            )
            os.close(descriptor)
            code = (
                "import hashlib,importlib.util,os,sys; from pathlib import Path; "
                "s=importlib.util.spec_from_file_location('fd_exec',sys.argv[1]); "
                "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); "
                "p=Path(sys.argv[2]); d=hashlib.sha256(p.read_bytes()).hexdigest(); "
                "f=m._open_binary(p,os.geteuid(),d); m._exec_binary(f,(str(p),),{})"
            )
            completed = subprocess.run(
                [sys.executable, "-I", "-c", code, str(RUN_AGENT), "/usr/bin/true"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10,
                check=False,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)


if __name__ == "__main__":
    unittest.main()
