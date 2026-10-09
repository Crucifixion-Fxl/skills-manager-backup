"""L2 whole installation: real audit, files, journals and loaded OS process.

Only native capability and systemd CLI/MainPID are external boundary doubles.
This proves orchestration, not a deployed timer/native/model L3 or Feishu L4.
"""
import hashlib
import importlib
import json
from pathlib import Path
import sqlite3
import subprocess
import unittest

from test_install_agent_recovery import RecoveryInstallFixture, EXPECTED
from test_recovery_install_manager import RecoveryManagerFixture, SERVICE, TIMER
from test_agent_recovery import snapshot


class RecoveryInstallApplyFixture(RecoveryInstallFixture):
    init_manager = RecoveryManagerFixture.init_manager
    verbs = RecoveryManagerFixture.verbs
    write_tick = RecoveryManagerFixture.write_tick

    def setUp(self):
        super().setUp()
        self.child = subprocess.Popen(["/usr/bin/sleep", "120"], env={"BUZZ_ACP_RECOVERY_REVISION": EXPECTED})
        self.addCleanup(self.stop_child)
        digest = hashlib.sha256(Path("/usr/bin/sleep").read_bytes()).hexdigest()
        env = self.agents / "demo-dev.env"
        old_digest = hashlib.sha256(Path("/usr/bin/true").read_bytes()).hexdigest()
        self.write(env, env.read_text().replace("/usr/bin/true", "/usr/bin/sleep").replace(old_digest, digest))
        cli_link = self.home / ".local/bin/buzz-acp"
        if cli_link.is_symlink():
            cli_link.unlink()
            cli_link.symlink_to("/usr/bin/sleep")
        self.journal_dir = self.home / ".local/state/buzz-recovery/demo-dev/runtime"
        self.journal_dir.mkdir(parents=True, mode=0o700)
        self.trust_directories(self.journal_dir.parent)
        self.current = snapshot("00000000-0000-4000-8000-000000000099")
        self.current.update(agent_pubkey=self.agent, relay="https://relay.example.test", pid=self.child.pid,
                            process_start_ticks=Path(f"/proc/{self.child.pid}/stat").read_text().rsplit(") ", 1)[1].split()[19],
                            boot_id=Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
                            channels=["00000000-0000-4000-8000-000000000001"])
        self.current["runtime_policy"]["owner"] = self.owner
        self.snapshot_path = self.journal_dir / (self.current["generation"] + ".json")
        self.write(self.snapshot_path, json.dumps(self.current))
        self.journal_dir.chmod(0o700)
        self.config_path = self.home / ".config/buzz/recovery/config.json"
        self.fail_reload_once = False
        self.inactive_after_enable = False
        self.tamper_after_enable = False
        self.tick_journal = None
        self.init_manager()
        self.reload_state()
        self.apply_module = importlib.import_module("recovery_install_apply")

    def stop_child(self):
        self.child.terminate()
        self.child.wait(timeout=5)

    def reload_state(self):
        for unit, state in self.states.items():
            path = self.units / unit
            present = path.exists()
            state.update(LoadState="loaded" if present else "not-found", FragmentPath=str(path) if present else "",
                         UnitFileState=("static" if unit == SERVICE else "disabled") if present else "",
                         ActiveState="inactive", SubState="dead")

    def command(self, argv, **kwargs):
        if argv[2] == "daemon-reload":
            if self.fail_reload_once:
                self.fail_reload_once = False
                self.calls.append((argv, kwargs))
                return subprocess.CompletedProcess(argv, 1), "fixture private reload error"
            self.reload_state()
        if argv[2:] == ["start", SERVICE]:
            self.tick_journal = self.last_record()
            state = self.home / ".local/state/buzz-recovery/controller"
            self.assertTrue(state.is_dir(), "first installation must bootstrap controller state before starting")
            self.assertEqual(state.stat().st_mode & 0o777, 0o700)
        result = RecoveryManagerFixture.command(self, argv, **kwargs)
        if argv[2] == "enable":
            if self.inactive_after_enable:
                self.states[TIMER]["ActiveState"] = "inactive"
            if self.tamper_after_enable:
                self.write(self.agents / "demo-dev.prompt.md", "concurrent owner prompt edit")
        return result

    def last_record(self):
        paths = list((self.home / ".local/state/buzz-recovery/install").glob("*/journal.json"))
        self.assertEqual(len(paths), 1)
        return json.loads(paths[0].read_text())

    def apply(self):
        return self.apply_module.apply_install(home=self.home, revision=EXPECTED, owner_env_file=self.owner_env,
                                                relay_pubkey="b" * 64, manager=self.manager,
                                                main_pid=lambda unit: self.child.pid)


class RecoveryInstallApplyTests(RecoveryInstallApplyFixture):
    def test_L2_REC_APPLY_001_first_install_only_succeeds_after_actual_full_inventory_proof(self):
        original_snapshot = self.snapshot_path.read_bytes()
        result = self.apply()
        self.assertTrue(result["installed"])
        self.assertEqual([item["name"] for item in result["runtime"]], ["demo-dev"])
        self.assertEqual(self.verbs(), ["daemon-reload", "start", "enable"])
        self.assertEqual(self.tick_journal["phase"], "runtime_started")
        self.assertEqual(self.tick_journal["manager"]["steps"], ["quiesced", "reloaded"])
        self.assertEqual(self.last_record()["phase"], "committed")
        self.assertEqual(self.last_record()["manager"]["steps"],
                         ["quiesced", "reloaded", "tick_completed", "timer_enabled", "post_audit_passed"])
        self.assertTrue(result["after_audit"]["ok"])
        self.assertEqual(self.snapshot_path.read_bytes(), original_snapshot)
        self.assertNotIn("BUZZ_PRIVATE_KEY", self.config_path.read_text())

    def test_L2_REC_APPLY_002_invalid_actual_runtime_never_mutates_manager_or_installs(self):
        self.current["process_start_ticks"] = "0"
        self.write(self.snapshot_path, json.dumps(self.current))
        self.journal_dir.chmod(0o700)
        with self.assertRaises(self.apply_module.ApplyError) as caught:
            self.apply()
        self.assertEqual(caught.exception.public()["stage"], "runtime_before")
        self.assertEqual(caught.exception.public()["error"], "recovery_runtime_unverified")
        self.assertEqual(caught.exception.public()["rollback"], "completed")
        self.assertNotIn("磁盘空间", caught.exception.public()["remediation"])
        self.assertEqual(self.last_record()["phase"], "rolled_back")
        self.assertFalse(self.config_path.exists())
        self.assertEqual(self.verbs(), [])

    def test_L2_REC_APPLY_003_unrelated_audit_failure_cannot_be_waived(self):
        self.write(self.agents / "demo-dev.prompt.md", "missing required policy")
        with self.assertRaises(self.apply_module.ApplyError) as caught:
            self.apply()
        self.assertFalse(caught.exception.public()["installed"])
        self.assertEqual(self.verbs(), [])
        self.assertFalse(self.config_path.exists())

    def test_L2_REC_APPLY_004_reload_failure_restores_absence_and_proves_manager_restored(self):
        self.fail_reload_once = True
        with self.assertRaises(self.apply_module.ApplyError) as caught:
            self.apply()
        self.assertEqual(caught.exception.public()["stage"], "reload")
        self.assertEqual(caught.exception.public()["error"], "recovery_manager_command_failed")
        self.assertEqual(caught.exception.public()["rollback"], "completed")
        self.assertEqual(caught.exception.public()["cause"]["error"], "recovery_manager_command_failed")
        self.assertNotIn("private reload", str(caught.exception.public()))
        self.assertEqual(self.last_record()["phase"], "rolled_back")
        self.assertFalse(self.config_path.exists())
        self.assertFalse((self.units / SERVICE).exists())
        self.assertEqual(self.verbs(), ["daemon-reload", "daemon-reload"])
        self.assertEqual(self.states[SERVICE]["LoadState"], "not-found")

    def test_L2_REC_APPLY_005_tick_failure_preserves_new_files_and_runtime_queue(self):
        self.fail_command = "start"
        state = self.home / ".local/state/buzz-recovery/controller"
        state.mkdir(mode=0o700)
        database = state / "pending.sqlite3"
        with sqlite3.connect(database) as db:
            db.execute("CREATE TABLE pending (event TEXT)")
            db.execute("INSERT INTO pending VALUES ('existing-signed-event')")
        database.chmod(0o600)
        original = database.read_bytes()
        with self.assertRaises(self.apply_module.ApplyError) as caught:
            self.apply()
        self.assertEqual(caught.exception.public()["stage"], "tick")
        self.assertEqual(caught.exception.public()["error"], "installation_runtime_unverified")
        self.assertEqual(self.last_record()["phase"], "forward_fix_required")
        self.assertTrue(self.config_path.exists())
        self.assertEqual(self.verbs(), ["daemon-reload", "start"])
        self.assertEqual(database.read_bytes(), original)

    def test_L2_REC_APPLY_006_timer_command_success_without_active_state_is_not_installed(self):
        self.inactive_after_enable = True
        with self.assertRaises(self.apply_module.ApplyError) as caught:
            self.apply()
        self.assertEqual(caught.exception.public()["stage"], "enable_timer")
        self.assertEqual(caught.exception.public()["cause"]["error"], "recovery_timer_unverified")
        self.assertEqual(self.last_record()["phase"], "forward_fix_required")

    def test_L2_REC_APPLY_007_post_audit_detects_concurrent_unrelated_drift_without_overwriting_it(self):
        self.tamper_after_enable = True
        with self.assertRaises(self.apply_module.ApplyError) as caught:
            self.apply()
        self.assertEqual(caught.exception.public()["stage"], "post_audit")
        self.assertEqual((self.agents / "demo-dev.prompt.md").read_text(), "concurrent owner prompt edit")
        self.assertEqual(self.last_record()["phase"], "forward_fix_required")

    def test_L2_REC_APPLY_008_unsafe_existing_controller_state_blocks_start_without_chmod(self):
        state = self.home / ".local/state/buzz-recovery/controller"
        state.mkdir(mode=0o755)
        with self.assertRaises(self.apply_module.ApplyError):
            self.apply()
        self.assertEqual(state.stat().st_mode & 0o777, 0o755)
        self.assertNotIn("start", self.verbs())


if __name__ == "__main__":
    unittest.main()


class RenderUnitsTests(unittest.TestCase):
    """The installer's unit text is the single template the L3 timer reuses."""

    def test_production_units_are_byte_identical_to_the_canonical_template(self):
        from recovery_install_apply import render_files
        files = render_files("/home/example", "a" * 40, {"k": 1})
        self.assertEqual(hashlib.sha256(files["service"]).hexdigest(),
                         "73e4f6312888ce89f02b76d1146b3b007f036f9c8754f6e29a953d3c96ef94f4")
        self.assertEqual(hashlib.sha256(files["timer"]).hexdigest(),
                         "cf0841b3d1f89ccbec3716577cf1fe1c9d158876670640f9d6dba0b1675033de")

    def test_test_only_name_and_config_change_nothing_else(self):
        from recovery_install_apply import render_files, render_units
        canonical = render_files("/home/example", "a" * 40, {"k": 1})
        release = "/home/example/.local/share/buzz-agent-setup/releases/" + "a" * 40
        test = render_units(release, config="/tmp/l3/controller.json", service="buzz-recovery-l3-abc123.service")
        self.assertEqual(test["service"], canonical["service"].replace(
            b"%h/.config/buzz/recovery/config.json", b"/tmp/l3/controller.json"))
        self.assertEqual(test["timer"], canonical["timer"].replace(
            b"buzz-agent-recovery.service", b"buzz-recovery-l3-abc123.service"))

    def test_unsafe_names_and_paths_are_refused_before_any_text(self):
        from install_agent_recovery import PlanError
        from recovery_install_apply import render_units
        for kwargs in (dict(config="/tmp/a b.json"), dict(config="relative.json"), dict(config="/tmp/../x.json"),
                       dict(service="buzz-x.service\nExecStart=/bin/sh"), dict(service="other.service")):
            with self.subTest(kwargs=kwargs), self.assertRaises(PlanError):
                render_units("/home/example/release", **kwargs)
