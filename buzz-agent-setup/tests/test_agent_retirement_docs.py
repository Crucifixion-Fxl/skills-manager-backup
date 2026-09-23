"""Contract for the agent-retirement / bot-credential / glab-host / systemd-unit write-back (skills#121).

Every fact below was verified on the owner's host on 2026-09-20 while retiring and
re-credentialing local Buzz agents.  The tests pin each fact to the document that carries
it, so a future edit cannot silently drop one of the traps.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import unittest
from pathlib import Path


SKILL = Path(__file__).resolve().parents[1]
REPO = SKILL.parents[1]
REFS = SKILL / "references"
RUNTIME = REFS / "runtime-setup.md"
CREDENTIALS = REFS / "agent-credentials.md"
SCRIPTS_README = REFS / "scripts" / "README.md"
ENTRYPOINT = SKILL / "SKILL.md"
HELPER = SKILL / "scripts" / "provision_gitlab_agent_token.py"


def read(path: Path) -> str:
    if not path.is_file():
        raise AssertionError(f"{path.relative_to(REPO)} is required")
    return path.read_text(encoding="utf-8")


def section(text: str, heading: str) -> str:
    """Return one markdown section (heading line up to the next heading of the same or higher level)."""
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith(heading)), None)
    if start is None:
        raise AssertionError(f"missing section heading: {heading}")
    level = len(heading) - len(heading.lstrip("#"))
    end = len(lines)
    in_fence = lines[start].lstrip().startswith("```")
    for i in range(start + 1, len(lines)):
        stripped = lines[i]
        if stripped.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue  # a `# comment` inside a code block is not a heading
        if stripped.startswith("#"):
            depth = len(stripped) - len(stripped.lstrip("#"))
            if depth <= level and stripped[depth:depth + 1] == " ":
                end = i
                break
    return "\n".join(lines[start:end])


class ContractCase(unittest.TestCase):
    def assert_contains_all(self, text: str, label: str, required: tuple[str, ...]) -> None:
        for phrase in required:
            with self.subTest(document=label, requirement=phrase):
                self.assertIn(phrase, text)


class RetirementChecklistTest(ContractCase):
    """Fact 1: one complete, ordered checklist for retiring an agent."""

    def setUp(self) -> None:
        self.section = section(read(RUNTIME), "## 9. 退役 Agent")

    def test_checklist_is_one_ordered_list_covering_every_trace_of_the_agent(self) -> None:
        self.assert_contains_all(self.section, "runtime-setup.md 退役", (
            # stop the process; persistent unit also needs disable
            "systemctl --user stop buzz-local-<name>.service",
            "systemctl --user disable buzz-local-<name>.service",
            # owner removes the member
            "channels remove-member",
            # revoke the GitLab token by id and read back
            "吊销",
            "DELETE",
            "revoked",
            # local leftovers
            "env",
            "prompt",
            "日志",
            "启动脚本",
            "责任人 helper",
            "state 目录",
            # working directory: prove nothing is unpushed first
            "git status",
            "git log --branches --not --remotes",
            # references from other places
            "Canvas",
            "Agent 表",
            "频道描述",
            "转交对象",
            "route.json",
            "role→mention",
        ))

    def test_checklist_order_puts_revocation_before_relay_policy_and_local_deletion(self) -> None:
        order = (
            "systemctl --user stop",
            "channels remove-member",
            "吊销",
            "kind:30177",
            "state 目录",
            "git log --branches --not --remotes",
            "route.json",
        )
        positions = []
        for marker in order:
            index = self.section.find(marker)
            self.assertGreaterEqual(index, 0, marker)
            positions.append(index)
        self.assertEqual(positions, sorted(positions), "retirement steps are out of order")

    def test_30177_policy_is_deleted_with_a_kind_5_that_has_exactly_one_target(self) -> None:
        self.assert_contains_all(self.section, "runtime-setup.md kind 5", (
            "kind 5",
            "只能带一个目标",
            "`a` 标签",
            "30177:<owner_pubkey>:<agent_pubkey>",
            # the relay rejects e+a together; keep the verbatim error so it is greppable
            "invalid: deletion events must reference exactly one target via e or a tag",
            "同时给 `e` 和 `a` 会被拒",
            "references/scripts/publish_event.mjs",
            "node ≥22",
            "~/.nvm/versions/node/v24.14.0/bin/node",
            # read back that the policy is really gone
            "{kinds:[30177],authors:[<owner>]}",
            "已消失",
        ))


class BotAccessLevelTest(ContractCase):
    """Fact 2 and 5: bots cannot be re-levelled; rotate instead. Admin can mark external."""

    def test_bot_access_level_cannot_be_changed_so_downgrade_means_rotate(self) -> None:
        text = read(CREDENTIALS)
        block = section(text, "### bot 的 access level 改不了")
        self.assert_contains_all(block, "agent-credentials.md access level", (
            "members API",
            "PUT /projects/:id/members/:bot_id?access_level=",
            "403",
            "实例管理员",
            "Maintainer",
            "Reporter",
            "Developer",
            "签新 token（新 access_level）→ 验证 → 写进 env → 重启 agent → 吊销旧 token",
            "旧 token 的 env 备份",
            "用完删除",
        ))

    def test_external_flag_has_a_working_write_form(self) -> None:
        text = read(CREDENTIALS)
        self.assert_contains_all(text, "agent-credentials.md external", (
            "PUT users/<bot_user_id>?external=true",
            "查询参数",
            "GET 回读",
        ))
        self.assertIn("GITLAB_HOST=gitlab.addx.ai", section(text, "### bot 的 access level 改不了"))


class DeveloperBotExternalTest(ContractCase):
    """Developer bots run MR pipelines whose CI config is an Internal project: external breaks them."""

    def test_developer_bots_are_documented_as_non_external_with_the_reason_and_the_fix(self) -> None:
        block = section(read(CREDENTIALS), "### 会提 MR 的 bot（Developer）不标 external")
        self.assert_contains_all(block, "agent-credentials.md developer bot", (
            "engineering/ci-templates",
            "创建即失败、没有任何 job",
            "project bots cannot be added to other groups / projects",
            "external=false",
            "--external yes|no",
            "membership=true",
            "不提 MR 的 bot 不要跟着改成非 external",
            "没有逐项实测",
            "internal_isolation_checked",
            "不要用在 Planner／Reporter 上",
        ))
        self.assertIn("--external auto|yes|no", read(REFS / "runtime-setup.md"))

    def test_the_helper_and_the_docs_agree_on_which_profile_is_non_external(self) -> None:
        script = read(SKILL / "scripts" / "provision_gitlab_agent_token.py")
        self.assertIn('"developer": TokenProfile(30, ("api", "write_repository"), external_default=False)', script)
        self.assertIn("Developer 不标", read(SKILL / "SKILL.md"))


class GlabHostTest(ContractCase):
    """Fact 3: `glab api` outside a git repo silently targets gitlab.com."""

    def test_runtime_setup_documents_the_host_trap(self) -> None:
        block = section(read(RUNTIME), "### 手工 `glab api` 要显式指定 host")
        self.assert_contains_all(block, "runtime-setup.md glab host", (
            "GITLAB_HOST=gitlab.addx.ai",
            "gitlab.com",
            "401",
            "空输出",
            "~/.config/buzz/agents",
            "git 仓",
            "--hostname",
        ))

    def test_helper_already_pins_the_host_and_ignores_an_inherited_override(self) -> None:
        """Guard: provision_gitlab_agent_token.py must keep passing --hostname and drop GITLAB_HOST."""
        spec = importlib.util.spec_from_file_location("provision_gitlab_agent_token_hostcheck", HELPER)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        calls = []

        def runner(command, **kwargs):
            calls.append((command, kwargs.get("env")))
            import subprocess

            return subprocess.CompletedProcess(command, 0, stdout="{}", stderr="")

        true_binary = shutil.which("true")
        self.assertTrue(true_binary)
        previous = os.environ.get("GITLAB_HOST")
        os.environ["GITLAB_HOST"] = "gitlab.com"
        try:
            client = module.GlabClient("gitlab.addx.ai", glab_path=true_binary, runner=runner)
            client.request("/user")
        finally:
            if previous is None:
                os.environ.pop("GITLAB_HOST", None)
            else:
                os.environ["GITLAB_HOST"] = previous
        command, env = calls[0]
        self.assertEqual(command[command.index("--hostname") + 1], "gitlab.addx.ai")
        self.assertNotIn("GITLAB_HOST", env)


class PersistentUnitTest(ContractCase):
    """Fact 4: agents run as persistent user units, not transient systemd-run units."""

    def test_persistent_user_unit_is_documented_with_its_exact_shape(self) -> None:
        block = section(read(RUNTIME), "### 用持久用户单元托管 Agent 进程")
        self.assert_contains_all(block, "runtime-setup.md systemd unit", (
            "~/.config/systemd/user/buzz-local-<name>.service",
            "Type=exec",
            "ExecStart=%h/.config/buzz/agents/run-<name>.sh",
            "Restart=on-failure",
            "RestartSec=5s",
            "StandardOutput=append:%h/.config/buzz/agents/<name>.log",
            "StandardError=append:%h/.config/buzz/agents/<name>.log",
            "WantedBy=default.target",
            "linger",
            "systemctl --user daemon-reload",
            "systemctl --user enable --now buzz-local-<name>.service",
            # why not systemd-run
            "systemd-run",
            "瞬时单元",
            "重启机器后不会自动恢复",
        ))


class EntrypointRoutingTest(ContractCase):
    def test_entrypoint_and_script_readme_route_to_the_new_material(self) -> None:
        entry = read(ENTRYPOINT)
        self.assertIn("退役", entry)
        self.assertIn("references/runtime-setup.md", entry)
        row = next((line for line in entry.splitlines() if "退役 Agent" in line), "")
        self.assertTrue(row, "SKILL.md routing table needs a 退役 Agent row")
        self.assertIn("runtime-setup.md", row)
        self.assert_contains_all(read(SCRIPTS_README), "scripts/README.md", (
            "kind 5",
            "退役",
        ))


if __name__ == "__main__":
    unittest.main()
