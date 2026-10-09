"""L2 installer/systemd boundary: real parser and command adapter, CLI double.

No business service is touched. Exact show output is based on the real user
manager; this is not installed-systemd L3 or runtime/native readiness evidence.
"""
import copy
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

SERVICE = "buzz-agent-recovery.service"
TIMER = "buzz-agent-recovery.timer"


class RecoveryManagerFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve()
        self.init_manager()
        self.config = dict(version=1, owner_pubkey="1" * 64, relay_pubkey="b" * 64, relay_url="https://relay.invalid",
                           owner_env_file=str(self.home / "owner.env"),
                           state_dir=str(self.home / ".local/state/buzz-recovery/controller"), agents=[
                               dict(name="test-dev", pubkey="2" * 64, unit="buzz-test-dev.service",
                                    env_file=str(self.home / "agent.env"), journal_dir=str(self.home / "runtime"),
                                    revision="a" * 40, binary_sha256="3" * 64)])
        path = self.home / ".config/buzz/recovery/config.json"
        path.parent.mkdir(parents=True, mode=0o700)
        Path(self.config["state_dir"]).mkdir(parents=True, mode=0o700)
        for directory in self.home.rglob("*"):
            if directory.is_dir():
                directory.chmod(0o700)
        path.write_text(json.dumps(self.config))
        path.chmod(0o600)

    def init_manager(self):
        self.runtime = self.home / "user-runtime"
        self.runtime.mkdir(mode=0o700)
        self.module = importlib.import_module("recovery_install_manager")
        self.calls = []
        self.states = {}
        for name in (SERVICE, TIMER):
            self.states[name] = dict(Id=name, LoadState="loaded", ActiveState="inactive", SubState="dead",
                                     UnitFileState="static" if name == SERVICE else "disabled",
                                     FragmentPath=str(self.home / ".config/systemd/user" / name),
                                     DropInPaths="", NeedDaemonReload="no")
        self.states[SERVICE].update(MainPID="0", ControlPID="0", InvocationID="1" * 32, Result="success",
                                    ExecMainCode="1", ExecMainStatus="0", ExecMainStartTimestampMonotonic="1",
                                    ExecMainExitTimestampMonotonic="2")
        self.states[TIMER].update(Triggers=SERVICE, LastTriggerUSecMonotonic="0")
        self.fail_command = None
        self.after_start = None
        self.write_receipt = True
        self.receipt_ok = True
        self.show_transform = lambda name, output: output
        self.patch = mock.patch.object(self.module, "run_bounded_text", side_effect=self.command)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.manager = self.module.RecoveryManager(self.home, self.runtime)

    def write_tick(self):
        from agent_recovery import RecoveryStore
        from recovery_schedule import RecoverySchedule
        from recovery_controller import load_config
        from recovery_tick import begin_tick, complete_tick
        config = load_config(self.home / ".config/buzz/recovery/config.json")
        with RecoveryStore(Path(config["state_dir"]) / ".scheduler.sqlite3") as store:
            RecoverySchedule(store.db)
            receipt = begin_tick(store.db, config, "2" * 32)
            result = dict(agents={a["name"]: dict(errors=[] if self.receipt_ok else ["fixture_failure"], retry_pending=0)
                                  for a in config["agents"]})
            complete_tick(store.db, receipt, result)

    def command(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        self.assertEqual(argv[:2], ["/usr/bin/systemctl", "--user"])
        verb = argv[2]
        if self.fail_command == verb:
            return subprocess.CompletedProcess(argv, 1), "fixture secret error must not be published"
        if verb == "show":
            name = argv[3]
            output = "\n".join(key + "=" + value for key, value in self.states[name].items()) + "\n"
            return subprocess.CompletedProcess(argv, 0), self.show_transform(name, output)
        if verb == "disable":
            self.assertEqual(argv[3:], ["--now", TIMER])
            self.states[TIMER].update(UnitFileState="disabled", ActiveState="inactive", SubState="dead")
        elif verb == "stop":
            self.assertEqual(argv[3:], [SERVICE])
            self.states[SERVICE].update(ActiveState="inactive", SubState="dead", MainPID="0", ControlPID="0")
        elif verb == "start":
            if argv[3:] == [TIMER]:
                self.states[TIMER].update(ActiveState="active", SubState="waiting")
            else:
                self.assertEqual(argv[3:], [SERVICE])
                now = str(time.monotonic_ns() // 1000)
                if self.write_receipt:
                    self.write_tick()
                self.states[SERVICE].update(InvocationID="2" * 32, ExecMainStartTimestampMonotonic=now,
                                            ExecMainExitTimestampMonotonic=str(time.monotonic_ns() // 1000))
                if self.after_start:
                    self.states[SERVICE].update(self.after_start)
        elif verb == "enable":
            self.assertIn(argv[3:], (["--now", TIMER], [TIMER]))
            self.states[TIMER]["UnitFileState"] = "enabled"
            if "--now" in argv:
                self.states[TIMER].update(ActiveState="active", SubState="waiting")
        else:
            self.assertEqual(argv[2:], ["daemon-reload"])
        return subprocess.CompletedProcess(argv, 0), ""

    def verbs(self):
        return [args[2] for args, _kwargs in self.calls if args[2] != "show"]


class RecoveryInstallManagerTests(RecoveryManagerFixture):
    def test_L2_REC_MANAGER_001_quiesce_proves_both_units_stopped_before_returning(self):
        self.states[TIMER].update(UnitFileState="enabled", ActiveState="active", SubState="waiting")
        self.states[SERVICE].update(ActiveState="activating", SubState="start", MainPID="123", ControlPID="124")
        receipt = self.manager.quiesce()
        self.assertTrue(receipt["timer_was_enabled"])
        self.assertTrue(receipt["timer_was_active"])
        self.assertEqual(self.verbs(), ["disable", "stop"])
        self.assertEqual(self.states[SERVICE]["MainPID"], "0")
        self.assertEqual(self.states[TIMER]["ActiveState"], "inactive")

    def test_L2_REC_MANAGER_002_missing_units_are_explicitly_absent_not_failed_commands(self):
        for state in self.states.values():
            state.update(LoadState="not-found", FragmentPath="", UnitFileState="")
        result = self.manager.quiesce()
        self.assertFalse(result["timer_was_enabled"])
        self.assertFalse(result["timer_was_active"])
        self.assertEqual(self.verbs(), [])

    def test_L2_REC_MANAGER_003_shadow_dropin_or_reload_pending_never_mutates_manager(self):
        original = copy.deepcopy(self.states)
        for field, value in (("FragmentPath", "/tmp/foreign.service"), ("DropInPaths", "/tmp/extra.conf"),
                             ("NeedDaemonReload", "yes"), ("LoadState", "masked"), ("Id", "another.service")):
            with self.subTest(field=field):
                self.states = copy.deepcopy(original)
                self.states[SERVICE][field] = value
                self.calls.clear()
                with self.assertRaises(self.module.ManagerError):
                    self.manager.quiesce()
                self.assertEqual(self.verbs(), [])

    def test_L2_REC_MANAGER_004_command_failure_is_visible_and_has_no_raw_output(self):
        self.fail_command = "disable"
        with self.assertRaises(self.module.ManagerError) as caught:
            self.manager.quiesce()
        self.assertEqual(caught.exception.code, "recovery_manager_command_failed")
        self.assertNotIn("fixture secret", str(caught.exception.public()))
        self.assertIn("请", caught.exception.public()["remediation"])
        self.assertEqual(self.verbs(), ["disable"])

    def test_L2_REC_MANAGER_005_stop_success_with_live_pid_is_not_quiescence(self):
        original = self.command

        def still_running(argv, **kwargs):
            result = original(argv, **kwargs)
            if argv[2] == "stop":
                self.states[SERVICE].update(MainPID="123", ActiveState="deactivating", SubState="stop-sigterm")
            return result

        with mock.patch.object(self.module, "run_bounded_text", side_effect=still_running):
            with self.assertRaises(self.module.ManagerError) as caught:
                self.manager.quiesce()
        self.assertEqual(caught.exception.code, "recovery_manager_not_quiescent")

    def test_L2_REC_MANAGER_006_fresh_completed_oneshot_and_enabled_timer_are_separate_proofs(self):
        self.manager.reload()
        result = self.manager.start_verified()
        self.assertEqual(result["invocation_id"], "2" * 32)
        self.assertFalse(result["installed"])
        self.manager.enable_verified()
        self.assertEqual(self.verbs(), ["daemon-reload", "start", "enable"])

    def test_L2_REC_MANAGER_007_old_success_running_or_failed_exit_cannot_pass(self):
        initial = copy.deepcopy(self.states[SERVICE])
        for mutation in ({"InvocationID": "1" * 32}, {"InvocationID": ""}, {"Result": "exit-code"},
                         {"ExecMainStatus": "1"}, {"ExecMainCode": "0"}, {"MainPID": "22"},
                         {"ControlPID": "22"}, {"ActiveState": "activating"},
                         {"ExecMainStartTimestampMonotonic": "1"}, {"ExecMainExitTimestampMonotonic": "0"}):
            with self.subTest(mutation=mutation):
                self.states[SERVICE] = copy.deepcopy(initial)
                self.after_start = mutation
                with self.assertRaises(self.module.ManagerError) as caught:
                    self.manager.start_verified()
                self.assertEqual(caught.exception.code, "recovery_tick_unverified")

    def test_L2_REC_MANAGER_008_duplicate_missing_or_unknown_show_fields_fail_closed(self):
        for transform in (lambda name, text: text + "ActiveState=inactive\n",
                          lambda name, text: "\n".join(s for s in text.splitlines() if not s.startswith("LoadState=")),
                          lambda name, text: text + "Unexpected=data\n",
                          lambda name, text: text.replace("MainPID=0", "MainPID=not-a-pid")):
            with self.subTest(transform=transform):
                self.show_transform = transform
                with self.assertRaises(self.module.ManagerError):
                    self.manager.quiesce()

    def test_L2_REC_MANAGER_009_only_fixed_recovery_units_and_clean_user_bus_environment(self):
        with mock.patch.dict(os.environ, {"BUZZ_PRIVATE_KEY": "secret", "LD_PRELOAD": "secret",
                                          "PYTHONPATH": "secret", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/other/bus"}):
            self.manager.quiesce()
        for args, kwargs in self.calls:
            self.assertEqual(kwargs["env"], {"HOME": str(self.home), "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8",
                                               "XDG_RUNTIME_DIR": str(self.runtime)})
            self.assertLessEqual(kwargs["timeout"], 330)
        before = len(self.calls)
        with self.assertRaises(self.module.ManagerError):
            self.manager.status("buzz-local-business-dev.service")
        self.assertEqual(len(self.calls), before)

    def test_L2_REC_MANAGER_010_enable_success_without_active_timer_is_not_success(self):
        original = self.command

        def disabled(argv, **kwargs):
            result = original(argv, **kwargs)
            if argv[2] == "enable":
                self.states[TIMER]["ActiveState"] = "inactive"
            return result

        with mock.patch.object(self.module, "run_bounded_text", side_effect=disabled):
            with self.assertRaises(self.module.ManagerError) as caught:
                self.manager.enable_verified()
        self.assertEqual(caught.exception.code, "recovery_timer_unverified")

    def test_L2_REC_MANAGER_011_capture_is_read_only_and_drift_blocks_quiesce(self):
        before = self.manager.capture()
        self.assertEqual(before, dict(service_present=True, timer_present=True,
                                      timer_was_enabled=False, timer_was_active=False))
        self.assertEqual(self.verbs(), [])
        self.states[TIMER]["UnitFileState"] = "enabled"
        with self.assertRaises(self.module.ManagerError):
            self.manager.quiesce(expected=before)
        self.assertEqual(self.verbs(), [])

    def test_L2_REC_MANAGER_012_restore_enabled_inactive_timer_does_not_start_it(self):
        self.states[TIMER]["UnitFileState"] = "enabled"
        before = self.manager.capture()
        self.manager.quiesce(expected=before)
        self.calls.clear()
        self.manager.restore(before)
        self.assertEqual(self.manager.capture(), before)
        self.assertEqual(self.verbs(), ["daemon-reload", "enable"])

    def test_L2_REC_MANAGER_013_restore_active_unenabled_timer_does_not_enable_it(self):
        self.states[TIMER].update(ActiveState="active", SubState="waiting")
        before = self.manager.capture()
        self.manager.quiesce(expected=before)
        self.calls.clear()
        self.manager.restore(before)
        self.assertEqual(self.manager.capture(), before)
        self.assertEqual(self.verbs(), ["daemon-reload", "start"])

    def test_L2_REC_MANAGER_014_restore_wrong_presence_or_running_service_never_enables_old_timer(self):
        before = dict(service_present=True, timer_present=True, timer_was_enabled=True, timer_was_active=True)
        initial = copy.deepcopy(self.states[SERVICE])
        for update in (dict(LoadState="not-found", FragmentPath="", UnitFileState=""),
                       dict(ActiveState="activating", SubState="start", MainPID="4321")):
            with self.subTest(update=update):
                self.states[SERVICE] = {**initial, **update}
                self.calls.clear()
                with self.assertRaises(self.module.ManagerError):
                    self.manager.restore(before)
                self.assertEqual(self.verbs(), ["daemon-reload"])

    def test_L2_REC_MANAGER_015_restore_checks_readback_not_just_command_exit(self):
        before = dict(service_present=True, timer_present=True, timer_was_enabled=True, timer_was_active=True)
        original = self.command

        def ignored_enable(argv, **kwargs):
            result = original(argv, **kwargs)
            if argv[2] == "enable":
                self.states[TIMER]["UnitFileState"] = "disabled"
            return result

        with mock.patch.object(self.module, "run_bounded_text", side_effect=ignored_enable):
            with self.assertRaises(self.module.ManagerError):
                self.manager.restore(before)

    def test_L2_REC_MANAGER_016_collected_oneshot_uses_exact_fresh_durable_tick(self):
        self.after_start = dict(InvocationID="", ExecMainCode="0", ExecMainStatus="0",
                               ExecMainStartTimestampMonotonic="0", ExecMainExitTimestampMonotonic="0")
        result = self.manager.start_verified()
        self.assertEqual(result["invocation_id"], "2" * 32)
        self.assertFalse(result["installed"])

    def test_L2_REC_MANAGER_017_manager_exit_success_without_durable_tick_is_not_success(self):
        self.write_receipt = False
        with self.assertRaises(self.module.ManagerError) as caught:
            self.manager.start_verified()
        self.assertEqual(caught.exception.code, "recovery_tick_unverified")

    def test_L2_REC_MANAGER_018_old_receipt_does_not_prove_a_fresh_invocation(self):
        self.write_tick()
        self.write_receipt = False
        for collected in (False, True):
            with self.subTest(collected=collected):
                if collected:
                    self.after_start = dict(InvocationID="", ExecMainCode="0", ExecMainStatus="0",
                                            ExecMainStartTimestampMonotonic="0", ExecMainExitTimestampMonotonic="0")
                with self.assertRaises(self.module.ManagerError):
                    self.manager.start_verified()

    def test_L2_REC_MANAGER_019_failed_controller_result_cannot_be_hidden_by_manager_success(self):
        self.receipt_ok = False
        with self.assertRaises(self.module.ManagerError):
            self.manager.start_verified()
