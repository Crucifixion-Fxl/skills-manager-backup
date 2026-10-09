"""L2 public installer CLI (contract §8): --check / --dry-run / --apply / --repair.

Real audit, files, journals, locks, tick receipts and a loaded OS process;
only the systemd CLI/MainPID and native capability probe are boundary
doubles. The installer is never run against the real host.
"""
import contextlib
import io
import json
import unittest

from test_recovery_install_repair import RecoveryRepairFixture
from test_install_agent_recovery import EXPECTED
from test_recovery_install_manager import SERVICE


class InstallerCliTests(RecoveryRepairFixture):
    def run_cli(self, *mode):
        argv = [*mode, "--revision", EXPECTED, "--owner-env-file", str(self.owner_env), "--relay-pubkey", "b" * 64]
        out = io.StringIO()
        # Inject the fixture home; never patch Path.home, which would switch
        # the auditor into host mode against the real user manager.
        with contextlib.redirect_stdout(out):
            code = self.installer.main(argv, home=self.home, manager=self.manager,
                                       main_pid=lambda unit: self.child.pid)
        text = out.getvalue()
        self.assertNotIn("1" * 64, text, "owner private key must never be printed")
        self.assertNotIn("2" * 64, text, "Agent private key must never be printed")
        lines = text.splitlines()
        self.assertEqual(len(lines), 1, "exactly one machine-readable JSON line")
        return code, json.loads(lines[0])

    def tree(self):
        return {str(path): (path.read_bytes() if path.is_file() and not path.is_symlink() else None)
                for path in self.home.rglob("*")}

    def usage_error(self, argv):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as caught:
            self.installer.main(argv)
        return caught.exception.code, err.getvalue()

    def test_L2_REC_CLI_001_exactly_one_mode_is_required(self):
        base = ["--revision", EXPECTED, "--owner-env-file", str(self.owner_env), "--relay-pubkey", "b" * 64]
        code, text = self.usage_error(base)
        self.assertEqual(code, 2)
        for flag in ("--check", "--dry-run", "--apply", "--repair"):
            self.assertIn(flag, text)
        self.assertEqual(self.usage_error(["--apply", "--dry-run", *base])[0], 2)
        self.assertEqual(self.usage_error(["--check", "--repair", *base])[0], 2)

    def test_L2_REC_CLI_002_exit_codes_are_distinct_and_do_not_reuse_usage(self):
        exits = self.installer.EXIT
        self.assertEqual(set(exits), {"ok", "refused", "rolled_back", "needs_repair"})
        self.assertEqual(exits["ok"], 0)
        self.assertEqual(len(set(exits.values())), 4)
        self.assertNotIn(2, exits.values(), "2 is argparse usage")

    def test_L2_REC_CLI_003_check_is_read_only_and_reports_plan_preflight_and_state(self):
        before = self.tree()
        code, out = self.run_cli("--check")
        self.assertEqual((code, out.get("mode"), out.get("outcome")), (0, "check", "ok"))
        self.assertFalse(out["installed"])
        self.assertEqual(out["agents"], ["demo-dev"])
        self.assertEqual(out["journal"], "clean")
        self.assertEqual(out["runtime"], [{"name": "demo-dev", "phase": "ready"}])
        self.assertFalse(out["manager"]["service_present"])
        self.assertEqual(self.tree(), before, "--check must not write anything")
        self.assertEqual(self.verbs(), [], "--check may only read the user manager")

    def test_L2_REC_CLI_004_check_reports_an_interrupted_installation_as_needs_repair(self):
        self.apply_until_crash("daemon-reload")
        before = self.tree()
        code, out = self.run_cli("--check")
        self.assertEqual((code, out.get("outcome")), (self.installer.EXIT["needs_repair"], "needs_repair"))
        self.assertEqual(out["error"], "installation_interrupted")
        self.assertEqual(self.tree(), before)
        self.assertEqual(self.verbs(), [])

    def test_L2_REC_CLI_005_dry_run_keeps_its_plan_only_contract(self):
        before = self.tree()
        code, out = self.run_cli("--dry-run")
        self.assertEqual((code, out.get("mode"), out.get("outcome")), (0, "dry-run", "ok"))
        self.assertEqual((out["installed"], out["plan_valid"], out["agents"]), (False, True, ["demo-dev"]))
        self.assertEqual(self.tree(), before)
        self.assertEqual(self.calls, [])

    def test_L2_REC_CLI_006_apply_reports_installed_only_with_full_post_install_proof(self):
        code, out = self.run_cli("--apply")
        self.assertEqual((code, out.get("mode"), out.get("outcome")), (0, "apply", "ok"))
        self.assertTrue(out["installed"])
        self.assertEqual(out["agents"], ["demo-dev"])
        self.assertRegex(out["tick"]["invocation_id"], r"^[0-9a-f]{32}$")
        self.assertEqual(out["timer"], {"enabled": True, "active": True})
        self.assertTrue(out["after_audit"]["ok"])
        self.assertEqual(self.last_record()["phase"], "committed")

    def test_L2_REC_CLI_007_apply_refusal_before_any_change(self):
        self.write(self.agents / "demo-dev.prompt.md", "missing required policy")
        code, out = self.run_cli("--apply")
        self.assertEqual((code, out.get("outcome")), (self.installer.EXIT["refused"], "refused"))
        self.assertFalse(out["installed"])
        self.assertTrue(out.get("remediation"))
        self.assertFalse(self.install_root.exists())
        self.assertEqual(self.verbs(), [])

    def test_L2_REC_CLI_008_apply_failure_with_verified_rollback(self):
        self.fail_reload_once = True
        code, out = self.run_cli("--apply")
        self.assertEqual((code, out.get("outcome")), (self.installer.EXIT["rolled_back"], "rolled_back"))
        self.assertEqual(out["rollback"], "completed")
        self.assertFalse(self.config_path.exists())

    def test_L2_REC_CLI_009_apply_after_runtime_fence_failure_needs_repair(self):
        self.fail_command = "start"
        code, out = self.run_cli("--apply")
        self.assertEqual((code, out.get("outcome")), (self.installer.EXIT["needs_repair"], "needs_repair"))
        self.assertEqual(out["error"], "installation_runtime_unverified")
        self.assertIn("--repair", out["next"])

    def test_L2_REC_CLI_010_apply_on_interrupted_journal_points_to_repair(self):
        self.apply_until_crash("daemon-reload")
        code, out = self.run_cli("--apply")
        self.assertEqual((code, out.get("outcome")), (self.installer.EXIT["needs_repair"], "needs_repair"))
        self.assertIn("--repair", out["next"])
        self.assertEqual(self.verbs(), [])

    def test_L2_REC_CLI_011_repair_completes_forward_or_refuses(self):
        self.apply_until_crash("start", SERVICE)
        code, out = self.run_cli("--repair")
        self.assertEqual((code, out.get("mode"), out.get("outcome"), out.get("repair")), (0, "repair", "ok", "completed"))
        self.assertTrue(out["installed"])
        code, out = self.run_cli("--repair")
        self.assertEqual((code, out.get("repair"), out.get("installed")), (0, "not_needed", False))

    def test_L2_REC_CLI_012_repair_refusal_is_distinct_from_needs_repair(self):
        self.apply_until_crash("daemon-reload")
        self.write(self.config_path, '{"owner": "manual edit"}\n', 0o600)
        code, out = self.run_cli("--repair")
        self.assertEqual((code, out.get("outcome")), (self.installer.EXIT["refused"], "refused"))
        self.assertEqual(out["error"], "installation_rollback_conflict")
        self.assertFalse(out["installed"])


if __name__ == "__main__":
    unittest.main()
