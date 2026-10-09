"""Doc contract for automatic Agent recovery (#157): install, upgrade, verify, roll back.

Where a fact is also code (the installer's modes and exit codes, the launcher's
mandatory env key, the recovery units) the test reads the code instead of a
second copy of the value, so the pages cannot drift from what ships.
"""

from __future__ import annotations

import contextlib
import io
import re
import sys
import unittest

import test_local_alignment_docs as base
from test_local_alignment_docs import (ENTRYPOINT, REFS, RUN_AGENT, RUNBOOK, RUNTIME, SCRIPTS, ContractCase,
                                       read, section, table_row)

sys.path.insert(0, str(SCRIPTS))

RECOVERY_HEADING = "## 7. 自动恢复"
RESTART_TDD = REFS / "restart-continue-tdd.md"
MODES = ("--check", "--dry-run", "--apply", "--repair")


def installer_help() -> str:
    import install_agent_recovery
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.suppress(SystemExit):
        install_agent_recovery.main(["--help"])
    return out.getvalue()


class RecoveryRunbookTest(ContractCase):
    def setUp(self) -> None:
        self.body = section(read(RUNBOOK), RECOVERY_HEADING)

    def test_every_installer_mode_is_documented_and_really_exists(self) -> None:
        text = installer_help()
        self.assertIn("install_agent_recovery.py", self.body)
        for flag in MODES:
            with self.subTest(flag=flag):
                self.assertIn(flag, text, "the code must offer the documented mode")
                self.assertIn(f"`{flag}`", self.body)

    def test_each_exit_code_row_matches_the_code(self) -> None:
        import install_agent_recovery
        for outcome, code in install_agent_recovery.EXIT.items():
            with self.subTest(outcome=outcome):
                row = table_row(self.body, f"`{outcome}`")
                self.assertIn(f"`{code}`", row)

    def test_release_migration_verification_and_rollback_facts(self) -> None:
        self.assert_contains_all(self.body, "runbook recovery", (
            "BUZZ_ACP_RECOVERY_REVISION",
            "run-agent.py",
            "拒绝启动",
            "同一次升级",
            "recovery-schema",
            "CAPABILITIES",
            "buzz-agent-recovery.service",
            "buzz-agent-recovery.timer",
            "~/.local/state/buzz-recovery/install/",
            "installed=true",
            "tick",
            "fail=0, unknown=0",
            "--allow-recovery-install",
            "forward_fix_required",
            "向前修复",
            "pending",
            "不删",
        ))

    def test_the_runbook_links_recovery_from_its_first_section(self) -> None:
        first = section(read(RUNBOOK), "## 1. release 钉在几处")
        self.assertIn("自动恢复", first)
        self.assertNotIn("它不会替升级器发现中断任务或自动发送消息", first)


class RecoveryLauncherAndEntryTest(ContractCase):
    def test_launcher_really_requires_the_recovery_revision(self) -> None:
        self.assertIn('"BUZZ_ACP_RECOVERY_REVISION"', read(RUN_AGENT))

    def test_runtime_env_example_carries_the_mandatory_key(self) -> None:
        body = section(read(RUNTIME), "## 4. Env 与注册")
        self.assertRegex(body, r"(?m)^BUZZ_ACP_RECOVERY_REVISION=<")
        self.assert_contains_all(body, "runtime-setup env", ("拒绝启动", "install_agent_recovery.py",
                                                             "local-upgrade-runbook.md"))

    def test_skill_entry_routes_install_and_restart_rows(self) -> None:
        entry = read(ENTRYPOINT)
        self.assertIn("install_agent_recovery.py", table_row(entry, "local-upgrade-runbook.md"))
        row = table_row(entry, "restart-continue-tdd.md")
        self.assertIn("buzz-agent-recovery.timer", row)


class RestartContinueDocTest(ContractCase):
    def setUp(self) -> None:
        self.text = read(RESTART_TDD)

    def test_l1_still_documents_the_send_contract(self) -> None:
        body = section(self.text, "## L1")
        self.assert_contains_all(body, "restart-continue L1", ("restart_continue_message.py", "build_continue_args"))

    def test_l4_accepts_only_the_installed_automatic_controller(self) -> None:
        body = section(self.text, "## L4")
        self.assert_contains_all(body, "restart-continue L4", (
            "buzz-agent-recovery.timer", "install_agent_recovery.py", "不能手发", "tick"))
        self.assertNotIn("用 L1 构造器在原 Thread 发送", body)


class RecoveryDocsCarryNoSecretsTest(unittest.TestCase):
    def test_no_secret_shaped_value(self) -> None:
        material = {"runbook recovery": section(read(RUNBOOK), RECOVERY_HEADING),
                    "runtime-setup env": section(read(RUNTIME), "## 4. Env 与注册")}
        for label, text in material.items():
            for pattern in base.NewDocsCarryNoSecretsTest.PATTERNS:
                with self.subTest(document=label, pattern=pattern):
                    self.assertIsNone(re.search(pattern, text))


if __name__ == "__main__":
    unittest.main()
