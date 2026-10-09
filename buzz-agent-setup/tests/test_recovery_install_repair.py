"""L2 explicit repair of an interrupted installation (contract §7/§8, REC-001/018).

A crash is modelled faithfully: at a chosen systemd command the installer
process dies, the kernel drops its flock and no Python unwinding touches the
journal. Real audit, files, journals, locks, tick receipts and a loaded OS
process are used; only the systemd CLI/MainPID and native capability probe are
external boundary doubles. No real unit, Agent, relay or Feishu is touched.
"""
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import unittest
from unittest import mock
import uuid

from test_recovery_install_apply import RecoveryInstallApplyFixture
from test_recovery_install_manager import SERVICE, TIMER


class Crash(BaseException):
    """The installer process died; nothing below the call site runs."""


class RecoveryRepairFixture(RecoveryInstallApplyFixture):
    def setUp(self):
        super().setUp()
        self.crash = None
        self.realistic_failure = False
        self.files_module = __import__("recovery_install_files")
        self.install_root = self.home / ".local/state/buzz-recovery/install"

    def reload_state(self):
        # Real daemon-reload keeps enablement/activity of units that remain.
        for unit, state in self.states.items():
            path = self.units / unit
            present = path.exists()
            if present and state.get("LoadState") == "loaded":
                continue
            state.update(LoadState="loaded" if present else "not-found", FragmentPath=str(path) if present else "",
                         UnitFileState=("static" if unit == SERVICE else "disabled") if present else "",
                         ActiveState="inactive", SubState="dead")

    def command(self, argv, **kwargs):
        if self.crash is not None and argv[2:3] == [self.crash[0]] and (self.crash[1] is None or self.crash[1] in argv):
            self.crash = None
            raise Crash()
        if self.realistic_failure and argv[2] in {"start", "stop", "reset-failed"} and argv[3:] == [SERVICE]:
            # Real systemd: a failed oneshot stays "failed" across stop until
            # reset-failed; it is not silently inactive.
            self.calls.append((argv, kwargs))
            state = self.states[SERVICE]
            if argv[2] == "start" and self.fail_command == "start":
                state.update(ActiveState="failed", SubState="failed", Result="exit-code", ExecMainStatus="1")
                return subprocess.CompletedProcess(argv, 1), "fixture private start error"
            if argv[2] == "reset-failed":
                state.update(ActiveState="inactive", SubState="dead", Result="success", ExecMainStatus="0")
                return subprocess.CompletedProcess(argv, 0), ""
            if argv[2] == "stop" and state["ActiveState"] == "failed":
                return subprocess.CompletedProcess(argv, 0), ""
        # Real systemd gives every start a new InvocationID; a repeated one
        # would look like the old run and correctly fail freshness.
        self.invocation = uuid.uuid4().hex
        result = super().command(argv, **kwargs)
        if argv[2:] == ["start", SERVICE] and self.states[SERVICE]["InvocationID"] == "2" * 32:
            self.states[SERVICE]["InvocationID"] = self.invocation
        return result

    def write_tick(self):
        from agent_recovery import RecoveryStore
        from recovery_schedule import RecoverySchedule
        from recovery_controller import load_config
        from recovery_tick import begin_tick, complete_tick
        config = load_config(self.config_path)
        with RecoveryStore(Path(config["state_dir"]) / ".scheduler.sqlite3") as store:
            RecoverySchedule(store.db)
            receipt = begin_tick(store.db, config, getattr(self, "invocation", "2" * 32))
            complete_tick(store.db, receipt, dict(agents={a["name"]: dict(errors=[], retry_pending=0)
                                                          for a in config["agents"]}))

    def apply_until_crash(self, verb, unit=None):
        self.crash = (verb, unit)

        def died(transaction, *_exception):
            transaction._release()  # the kernel releases flock on process death
            return False

        with mock.patch.object(self.files_module.InstallationFiles, "__exit__", died):
            with self.assertRaises(Crash):
                self.apply()
        self.assertIsNone(self.crash, "fixture crash point was never reached")
        self.calls.clear()
        return self.journal_bytes()

    def last_record(self):
        paths = sorted(self.install_root.glob("*/journal.json"), key=lambda path: path.stat().st_mtime_ns)
        self.assertTrue(paths)
        return json.loads(paths[-1].read_text())

    def journal_bytes(self):
        paths = sorted(self.install_root.glob("*/journal.json"))
        return {path.parent.name: path.read_bytes() for path in paths}

    def repair(self):
        return self.apply_module.repair_install(home=self.home, revision=EXPECTED_REVISION(),
                                                owner_env_file=self.owner_env, relay_pubkey="b" * 64,
                                                manager=self.manager, main_pid=lambda unit: self.child.pid)

    def assert_refused(self, code):
        journal = self.journal_bytes()
        files = {path: path.read_bytes() for path in (self.config_path, self.units / SERVICE, self.units / TIMER)
                 if path.exists()}
        with self.assertRaises(self.apply_module.ApplyError) as caught:
            self.repair()
        public = caught.exception.public()
        self.assertEqual(public["error"], code)
        self.assertEqual(public["outcome"], "refused")
        self.assertFalse(public["installed"])
        self.assertTrue(public["remediation"])
        self.assertEqual(self.journal_bytes(), journal, "a refused repair must not rewrite the evidence")
        self.assertEqual({path: path.read_bytes() for path in files}, files)
        self.assertEqual(self.verbs(), [], "a refused repair must not mutate the user manager")
        return public


def EXPECTED_REVISION():
    from test_install_agent_recovery import EXPECTED
    return EXPECTED


class RecoveryRepairTests(RecoveryRepairFixture):
    def test_L2_REC_REPAIR_001_crash_before_runtime_fence_rolls_back_to_exact_prior_state(self):
        self.apply_until_crash("daemon-reload")
        record = self.last_record()
        self.assertEqual((record["phase"], record["manager"]["steps"]), ("published", ["quiesced"]))
        self.assertTrue(self.config_path.exists())
        with self.assertRaises(self.apply_module.ApplyError) as blocked:
            self.apply()
        self.assertEqual(blocked.exception.public()["error"], "installation_interrupted")
        self.calls.clear()
        result = self.repair()
        self.assertEqual(result["repair"], "rolled_back")
        self.assertFalse(result["installed"])
        self.assertEqual(self.last_record()["phase"], "rolled_back")
        self.assertFalse(self.config_path.exists())
        self.assertFalse((self.units / SERVICE).exists())
        self.assertFalse((self.units / TIMER).exists())
        self.assertEqual(self.states[SERVICE]["LoadState"], "not-found")
        self.assertNotIn("start", self.verbs())
        self.calls.clear()
        self.assertTrue(self.apply()["installed"], "a repaired host installs normally")

    def test_L2_REC_REPAIR_002_crash_after_runtime_fence_is_only_completed_forward(self):
        self.apply_until_crash("enable")
        record = self.last_record()
        self.assertEqual(record["phase"], "runtime_started")
        self.assertEqual(record["manager"]["steps"], ["quiesced", "reloaded", "tick_completed"])
        published = self.config_path.read_bytes()
        result = self.repair()
        self.assertEqual(result["repair"], "completed")
        self.assertTrue(result["installed"])
        self.assertEqual(self.config_path.read_bytes(), published)
        self.assertEqual(self.last_record()["phase"], "committed")
        self.assertEqual(self.last_record()["manager"]["steps"],
                         ["quiesced", "reloaded", "tick_completed", "timer_enabled", "post_audit_passed"])
        self.assertIn("start", self.verbs(), "forward repair needs a fresh tick receipt, not the pre-crash one")
        self.assertEqual(self.states[TIMER]["UnitFileState"], "enabled")
        self.assertEqual(self.states[TIMER]["ActiveState"], "active")
        self.assertTrue(result["after_audit"]["ok"])
        self.assertEqual([item["name"] for item in result["runtime"]], ["demo-dev"])

    def test_L2_REC_REPAIR_003_forward_fix_record_is_never_rolled_back_even_when_repair_fails(self):
        self.fail_command = "start"
        with self.assertRaises(self.apply_module.ApplyError):
            self.apply()
        self.assertEqual(self.last_record()["phase"], "forward_fix_required")
        database = self.home / ".local/state/buzz-recovery/controller/pending.sqlite3"
        with sqlite3.connect(database) as db:
            db.execute("CREATE TABLE pending (event TEXT)")
            db.execute("INSERT INTO pending VALUES ('signed-after-fence')")
        database.chmod(0o600)
        original = database.read_bytes()
        published = self.config_path.read_bytes()
        self.calls.clear()
        with self.assertRaises(self.apply_module.ApplyError) as caught:
            self.repair()
        self.assertEqual(caught.exception.public()["outcome"], "needs_repair")
        self.assertEqual(self.last_record()["phase"], "forward_fix_required")
        self.assertEqual(self.config_path.read_bytes(), published, "forward-only: new files stay")
        self.assertEqual(database.read_bytes(), original)
        self.fail_command = None
        self.calls.clear()
        result = self.repair()
        self.assertTrue(result["installed"])
        self.assertEqual(self.last_record()["phase"], "committed")
        self.assertEqual(database.read_bytes(), original)

    def test_L2_REC_REPAIR_004_pre_fence_record_with_new_config_tick_evidence_is_refused(self):
        state = self.home / ".local/state/buzz-recovery/controller"
        state.mkdir(mode=0o700)
        self.apply_until_crash("daemon-reload")
        self.write_tick()  # something outside the installer ran the NEW controller
        self.assert_refused("installation_repair_runtime_evidence")
        self.assertTrue(self.config_path.exists())

    def test_L2_REC_REPAIR_005_concurrent_owner_edit_is_refused_without_writing_journal(self):
        self.apply_until_crash("daemon-reload")
        self.write(self.config_path, '{"owner": "manual edit"}\n', 0o600)
        self.assert_refused("installation_rollback_conflict")
        self.assertEqual(self.config_path.read_text(), '{"owner": "manual edit"}\n')

    def test_L2_REC_REPAIR_006_live_recovery_service_process_is_refused(self):
        self.apply_until_crash("daemon-reload")
        # Consistent manager view of a running oneshot: loaded (someone ran
        # daemon-reload) and activating with a live MainPID.
        self.states[SERVICE].update(LoadState="loaded", FragmentPath=str(self.units / SERVICE), UnitFileState="static",
                                    ActiveState="activating", SubState="start", MainPID=str(self.child.pid))
        self.assert_refused("installation_repair_runtime_active")

    def test_L2_REC_REPAIR_007_forward_repair_without_topology_baseline_is_refused(self):
        self.apply_until_crash("start", SERVICE)
        for path in self.install_root.glob("*/before-audit.json"):
            path.unlink()
        self.assert_refused("installation_repair_baseline_missing")

    def test_L2_REC_REPAIR_008_ambiguous_record_set_is_refused(self):
        self.apply_until_crash("daemon-reload")
        stray = self.install_root / ("f" * 32)
        stray.mkdir(mode=0o700)
        self.assert_refused("installation_repair_unverified")

    def test_L2_REC_REPAIR_009_live_installer_lock_is_refused(self):
        self.apply_until_crash("daemon-reload")
        fd = os.open(self.install_root / ".lock", os.O_RDWR)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.assert_refused("installation_busy")

    def test_L2_REC_REPAIR_010_nothing_to_repair_writes_nothing(self):
        result = self.repair()
        self.assertEqual(result["repair"], "not_needed")
        self.assertFalse(result["installed"])
        self.assertFalse(self.install_root.exists(), "repair must not create installation state")
        self.assertEqual(self.verbs(), [])

    def test_L2_REC_REPAIR_011_upgrade_crash_before_quiesce_leaves_old_timer_running(self):
        self.assertTrue(self.apply()["installed"])
        self.calls.clear()
        self.apply_until_crash("disable")
        record = self.last_record()
        self.assertEqual((record["phase"], record["manager"]["steps"]), ("prepared", []))
        result = self.repair()
        self.assertEqual(result["repair"], "rolled_back")
        self.assertEqual(self.last_record()["phase"], "rolled_back")
        self.assertNotIn("disable", self.verbs())
        self.assertNotIn("start", self.verbs())
        self.assertEqual((self.states[TIMER]["UnitFileState"], self.states[TIMER]["ActiveState"]),
                         ("enabled", "active"))

    def test_L2_REC_REPAIR_012_forward_record_survives_a_failed_resume_write(self):
        self.fail_command = "start"
        with self.assertRaises(self.apply_module.ApplyError):
            self.apply()
        self.fail_command = None
        published = {path: path.read_bytes() for path in (self.config_path, self.units / SERVICE, self.units / TIMER)}
        original = os.replace
        failed = []

        def replace(source, target, *args, **kwargs):
            if Path(target).name == "journal.json" and not failed:
                failed.append(target)
                raise OSError("fixture journal disk failure")
            return original(source, target, *args, **kwargs)

        self.calls.clear()
        with mock.patch.object(os, "replace", side_effect=replace):
            with self.assertRaises(self.apply_module.ApplyError) as caught:
                self.repair()
        self.assertTrue(failed)
        self.assertEqual(caught.exception.public()["outcome"], "needs_repair")
        self.assertEqual(self.last_record()["phase"], "forward_fix_required")
        self.assertEqual({path: path.read_bytes() for path in published}, published,
                         "a forward-only record must never fall back to file rollback")

    def test_L2_REC_REPAIR_013_forward_repair_clears_only_the_failed_oneshot_marker(self):
        self.realistic_failure = True
        self.fail_command = "start"
        with self.assertRaises(self.apply_module.ApplyError):
            self.apply()
        self.assertEqual(self.states[SERVICE]["ActiveState"], "failed")
        self.assertEqual(self.last_record()["phase"], "forward_fix_required")
        self.fail_command = None
        self.calls.clear()
        try:
            result = self.repair()
        except self.apply_module.ApplyError as error:
            self.fail("forward repair was blocked by the failed oneshot marker: "
                      + error.public().get("cause", error.public())["error"])
        self.assertTrue(result["installed"])
        self.assertEqual(self.verbs()[:3], ["disable", "stop", "reset-failed"])
        self.assertEqual(self.last_record()["phase"], "committed")


if __name__ == "__main__":
    unittest.main()
