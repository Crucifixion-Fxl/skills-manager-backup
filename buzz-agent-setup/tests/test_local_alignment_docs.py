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
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
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
        "release_dir",
        "BUZZ_DESK_RUNNER_MANIFEST",
        "desk-runner.json",
        "<immutable-release>",
        "两处必须是同一个目录",
        "buzz-feishu-<channel>.service",
        "gitlab-todo-sync-<name>.service",
        "buzz_send_with_responsible_mentions.py",
        "allowRead",
        "people_file",
        "BUZZ_RESPONSIBLE_CONFIG",
        "插件副本",
        "两种别放进同一个目录",
    ),
    "## 2. 改了什么，该动哪几处": (
        "git diff <旧 40 位> <新 40 位> -- skills/buzz-agent-setup/scripts skills/buzz-agent-setup/references/scripts",
        "逐字相同",
        "不用重装 prompt、沙箱和配置，也不用重启",
        "da298c11",
        "15f888b6",
    ),
    "## 3. 顺序：先做不破坏的部分": (
        "git archive",
        "--strip-components=2",
        "chmod -R a-w",
        "相同的白名单 env",
        "--dry-run",
        "不要从自己的交互 shell 直接跑",
        "备份成 `<原名>.bak.<时间>`",
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
        "resolve_plugin_install.py",
        "git_commit_sha",
        "`errors` 为 0",
    ),
    "## 6. 回滚": (
        "不删、不覆盖",
        "*.bak.<时间>",
        "daemon-reload",
        "三样一起还原",
        "Canvas 别名表还在，才回得去",
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
            "`gitlab_buzz_route_reply.py`": ("release_dir", "ExecStart"),
            "`buzz_feishu_group_sync.py`": ("buzz-feishu-<channel>.service", "ExecStart"),
            "`gitlab_todo_sync.py`": ("gitlab-todo-sync-<name>.service", "ExecStart"),
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
        order = ["git archive", "--dry-run", "备份成 `<原名>.bak.<时间>`", "读 `status`"]
        positions = [index_of(body, marker) for marker in order]
        self.assertEqual(positions, sorted(positions), order)

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
        shutil.which("git") and shutil.which("tar") and (REPO / ".git").exists(),
        "needs git, tar and a git checkout (the CI image has no git)",
    )
    def test_the_release_commands_build_the_layout_the_page_describes(self) -> None:
        body = section(self.text, "## 3. 顺序：先做不破坏的部分")
        block = next(b for b in code_blocks(body) if "git" in b and "archive" in b)
        script = "\n".join(
            line for line in block.replace("<skills 克隆>", str(REPO)).replace("origin/main", "HEAD").splitlines()
            if " fetch " not in line  # no network in a test
        )
        with tempfile.TemporaryDirectory() as tmp:
            try:
                done = subprocess.run(
                    ["bash", "-euo", "pipefail", "-c", script],
                    env={"HOME": tmp, "PATH": os.environ.get("PATH", "/usr/bin:/bin")},
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                releases = Path(tmp) / ".local" / "share" / "buzz-agent-setup" / "releases"
                (release,) = list(releases.iterdir())
                self.assertRegex(release.name, r"^[0-9a-f]{40}$")
                for path in ("scripts/gitlab_buzz_sync.py", "scripts/gitlab_buzz_desk_runner.py",
                             "scripts/gitlab_todo_sync.py", "scripts/buzz_send_with_responsible_mentions.py",
                             "references/scripts/nostrkit.py"):
                    with self.subTest(path=path):
                        self.assertTrue((release / path).is_file())
                self.assertEqual(release.stat().st_mode & 0o222, 0, "the release must be read-only")
            finally:
                subprocess.run(["chmod", "-R", "u+w", tmp], check=False)

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

    def test_the_known_gaps_point_at_their_issues(self) -> None:
        for issue in ("skills#137", "skills#138", "skills#140", "skills#141"):
            with self.subTest(issue=issue):
                self.assertIn(issue, self.text)


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


if __name__ == "__main__":
    unittest.main()
