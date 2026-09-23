"""Contract for the agent-isolation write-back (BUZZ_AUTH_TAG quoting, ~/.bashrc gate, built-in sandbox).

Every fact was verified on the owner's host on 2026-09-20 after an isolation incident.  The tests pin
each fact to the document that carries it, so a future edit cannot silently drop one of the traps.
No secret value or identifier belongs in these documents; the tests also guard that.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


SKILL = Path(__file__).resolve().parents[1]
REPO = SKILL.parents[1]
REFS = SKILL / "references"
RUNTIME = REFS / "runtime-setup.md"
TROUBLE = REFS / "troubleshooting.md"
SCRIPTS_README = REFS / "scripts" / "README.md"
SANDBOX = REFS / "agent-sandbox.md"
ENTRYPOINT = SKILL / "SKILL.md"


def read(path: Path) -> str:
    if not path.is_file():
        raise AssertionError(f"{path.relative_to(REPO)} is required")
    return path.read_text(encoding="utf-8")


class AuthTagQuotingTest(unittest.TestCase):
    def test_mint_agent_usage_requires_single_quotes(self):
        text = read(SCRIPTS_README)
        self.assertIn("BUZZ_AUTH_TAG='[\"auth\"", text)
        self.assertIn("必须整段包单引号", text)
        self.assertIn("[auth,<hex>,,<sig>]", text)

    def test_troubleshooting_has_not_a_relay_member_entry(self):
        text = read(TROUBLE)
        self.assertIn("Auth failed: restricted: not a relay member", text)
        self.assertIn("每 5 秒重启", text)
        self.assertIn("accepted:true", text)
        self.assertIn("重新跑 `buzz users set-profile", text)
        self.assertIn("BUZZ_AUTH_TAG='[\"auth\"", text)

    def test_runtime_env_example_stays_quoted(self):
        self.assertIn("BUZZ_AUTH_TAG='[\"auth\",\"<owner_pub>\",\"\",\"<sig>\"]'", read(RUNTIME))


class BashrcGateTest(unittest.TestCase):
    def test_runtime_setup_explains_the_hole_and_the_gate(self):
        text = read(RUNTIME)
        self.assertIn("白名单只管启动那一刻", text)
        for required in (
            "没有 `login` 参数",
            "~/.bashrc",
            "~/.bash_secrets",
            "同一个 Unix UID",
            'if [ -z "${BUZZ_PRIVATE_KEY:-}" ] && [ -z "${BUZZ_ACP_AGENT_OWNER:-}" ] && [ -r "$HOME/.bash_secrets" ]; then . "$HOME/.bash_secrets"; fi',
            "docker",
            "独立的 Unix 用户",
            "bash -lc 'env'",
            "bash -lxc true",
        ):
            with self.subTest(required=required):
                self.assertIn(required, text)

    def test_detection_never_prints_values(self):
        text = read(RUNTIME)
        # -x traces print assigned values; the documented command must filter them out.
        self.assertIn("SRC=${BASH_SOURCE[0]}:${LINENO}", text)
        self.assertRegex(text, r"bash -lxc true 2>&1 \\\n\s+\| sed -nE")

    def test_no_document_still_asks_for_login_false(self):
        # The Bash tool has no `login` parameter.  Only runtime-setup.md may mention the phrase,
        # and only to say not to ask for it.
        for path in (REPO / "skills").rglob("*.md"):
            text = path.read_text(encoding="utf-8", errors="ignore")
            hits = [line for line in text.splitlines() if re.search(r"login\s*:\s*false", line)]
            if path == RUNTIME:
                for line in hits:
                    with self.subTest(line=line[:40]):
                        self.assertIn("不要", line)
            else:
                self.assertEqual(hits, [], f"{path.relative_to(REPO)} still asks for `login: false`")


class SandboxReferenceTest(unittest.TestCase):
    def test_entrypoint_routes_to_sandbox_reference(self):
        self.assertIn("references/agent-sandbox.md", read(ENTRYPOINT))

    def test_sandbox_reference_pins_prerequisites_and_config_split(self):
        text = read(SANDBOX)
        for required in (
            "https://code.claude.com/docs/en/sandboxing",
            "bubblewrap socat",
            "kernel.apparmor_restrict_unprivileged_userns",
            "setting up uid map: Permission denied",
            "/etc/apparmor.d/bwrap",
            "profile bwrap /usr/bin/bwrap flags=(unconfined)",
            "2.1.246",
            '"failIfUnavailable": true',
            '"allowUnsandboxedCommands": false',
            '"denyRead": ["~/"]',
            '"mode": "deny"',
            "Read(~/.config/buzz/**)",
            "不是完整隔离边界",
        ):
            with self.subTest(required=required):
                self.assertIn(required, text)

    def test_sandbox_reference_keeps_the_known_traps(self):
        text = read(SANDBOX)
        for required in (
            "无头",
            "strictAllowlist",
            "不限制网络",
            "test -r",
            "误报 READABLE",
            "wc -c",
            "整条命令会被权限层拒绝",
            "docker",
            "回滚",
            "网络层没有强制",
            "docker ps -q",
        ):
            with self.subTest(required=required):
                self.assertIn(required, text)

    def test_sandbox_reference_pins_scope_rollout_and_offline_probe(self):
        text = read(SANDBOX)
        for required in (
            'settingSources: ["user","project","local"]',
            "settings.local.json",
            ".git/info/exclude",
            "实际 cwd",
            "推广指引",
            "个人 agent",
            "开发类 agent",
            "harness 不是",
            "离线探针",
            "claude -p",
            "正对照",
            "反对照",
            "两层防护",
            "白名单只管启动那一刻",
        ):
            with self.subTest(required=required):
                self.assertIn(required, text)

    def test_sandbox_reference_pins_unix_socket_limit_and_tool_dir_recipe(self):
        text = read(SANDBOX)
        for required in (
            "Unix domain socket",
            "process_singleton_posix.cc: socket() failed",
            "allowAllUnixSockets",
            "/run/user/<uid>/systemd/private",
            "窄接口",
            "denyWrite",
            "沙箱外",
            "空目录",
            "~/.nvm",
        ):
            with self.subTest(required=required):
                self.assertIn(required, text)
        # The escape hatch must only ever be mentioned as something not to set.
        for line in text.splitlines():
            if "allowAllUnixSockets" in line:
                self.assertIn("不要", line)

    def test_shared_settings_do_not_enable_the_sandbox(self):
        # The shared user-level block carries policy only; enabling belongs to each agent's project file.
        text = read(SANDBOX)
        shared = text.split("### 共用：", 1)[1].split("### 每个 agent", 1)[0]
        self.assertNotIn('"enabled"', shared)
        self.assertIn('"enabled": true', text.split("### 每个 agent", 1)[1])

    def test_file_probes_use_size_not_test_r(self):
        # `test -r` reports READABLE for a blocked placeholder file, so a probe built on it can be
        # green with a broken sandbox.  Every probe row must judge the block by content size.
        text = read(SANDBOX)
        probes = text.split("## 验证", 1)[1]
        rows = [line for line in probes.splitlines() if re.match(r"\|\s*\d+\s*\|", line)]
        self.assertEqual(len(rows), 8)
        for row in rows:
            with self.subTest(row=row[:40]):
                self.assertNotRegex(row, r"test\s+-[a-z]\b")
                self.assertNotIn("READABLE", row)
        self.assertIn("wc -c < ~/.config/buzz/env", rows[0])
        self.assertIn("wc -c < ~/.bash_secrets", rows[1])
        self.assertIn("不用 `test -r`", probes)

    def test_baseline_allow_read_lists_the_five_read_only_tools(self):
        # denyRead ["~/"] hides ~/.local/bin, so glab/jq/uv are "command not found" and rg (a shell-snapshot
        # function) disappears with the snapshot directory.  The baseline JSON must re-allow exactly these.
        text = read(SANDBOX)
        per_agent = text.split("### 每个 agent", 1)[1].split("### `allowRead", 1)[0]
        allow_read = per_agent.split('"allowRead": [', 1)[1].split("]", 1)[0]
        for path in (
            "~/.local/bin/glab",
            "~/.local/bin/jq",
            "~/.local/bin/claude",
            "~/.local/share/claude/versions",
            "~/.claude-buzz/shell-snapshots",
        ):
            with self.subTest(path=path):
                self.assertIn(f'"{path}"', allow_read)
        # Never the whole bin dir, never glab's own config (may hold an admin token).
        self.assertNotIn('"~/.local/bin"', allow_read)
        self.assertNotIn("glab-cli", allow_read)
        self.assertNotIn('"~/"', allow_read)

    def test_baseline_section_explains_snapshot_check_and_glab_config(self):
        text = read(SANDBOX)
        section = text.split("### 基线还要放行的五项只读工具", 1)[1].split("## 推广指引", 1)[0]
        for required in (
            "shell-snapshots",
            "command not found",
            "shell 快照里的函数",
            "grep -h -E '^(export|declare -x)'",
            "sort | uniq -c",
            "只列变量名",
            "GITLAB_TOKEN",
            "GLAB_CONFIG_DIR",
            "没有实测",
        ):
            with self.subTest(required=required):
                self.assertIn(required, section)
        self.assertIn("不要放行 glab 的配置目录 `~/.config/glab-cli`", section)

    def test_tools_probe_is_part_of_the_standard_probe_list(self):
        text = read(SANDBOX)
        probes = text.split("## 验证", 1)[1]
        rows = [line for line in probes.splitlines() if re.match(r"\|\s*\d+\s*\|", line)]
        tools = rows[7]
        for cmd in ("glab --version", "jq --version", "rg --version"):
            with self.subTest(cmd=cmd):
                self.assertIn(cmd, tools)
        self.assertIn("版本号", tools)
        self.assertIn("8 项探针", probes)
        # The lesson: verify both "blocked what must be blocked" and "still works what must work".
        self.assertIn("同时测两面", probes)
        self.assertIn("该用的还能用", probes)

    def test_dev_agent_notes_are_marked_as_analysis_not_verified(self):
        text = read(SANDBOX)
        section = text.split("### 开发类 agent 的额外注意", 1)[1].split("## 已实测有效", 1)[0]
        head = section.splitlines()[0]
        self.assertIn("分析结论", head)
        self.assertIn("尚未实测", head)
        for required in (
            ".git/config",
            "git branch --set-upstream-to",
            "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=branch.autoSetupMerge GIT_CONFIG_VALUE_0=false",
            "git push origin HEAD",
            "~/.gitconfig",
            "./.scratch",
            "$TMPDIR",
            "allowWrite /tmp",
            "nohup",
            "Go 模块缓存",
        ):
            with self.subTest(required=required):
                self.assertIn(required, section)

    def test_probes_never_cat_secret_files(self):
        text = read(SANDBOX)
        probes = text.split("## 验证", 1)[1]
        self.assertNotRegex(probes, r"\bcat\b[^\n|]*(env|secrets)")
        self.assertIn("wc -c < ~/.bash_secrets", probes)


class NoSecretMaterialTest(unittest.TestCase):
    def test_new_text_has_no_secret_like_values(self):
        for path in (SANDBOX, RUNTIME, TROUBLE, SCRIPTS_README):
            text = read(path)
            with self.subTest(path=path.name):
                self.assertNotRegex(text, r"nsec1[02-9ac-hj-np-z]{20,}")
                self.assertNotRegex(text, r"glpat-[A-Za-z0-9_-]{10,}")
                if path == SANDBOX:
                    self.assertNotRegex(text, r"[\w.+-]+@[\w-]+\.(com|ai|io)\b")
                self.assertNotRegex(text, r"\bou_[0-9a-f]{20,}\b")


if __name__ == "__main__":
    unittest.main()
