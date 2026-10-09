"""L1 regression of the L3 runner; these doubles do not constitute an L3 pass."""
import importlib.util
import copy
import hashlib
import select
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


SPEC = importlib.util.spec_from_file_location(
    "recovery_e2e_runner", Path(__file__).parent / "localstack/recovery_e2e.py")
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)
import recovery_controller


class ProviderObservationTests(unittest.TestCase):
    def test_recheck_observer_requires_all_same_generation_blocked_deliveries_and_notice_ack(self):
        state = dict(generation="current", active={}, receipts={"one": "blocked", "two": "blocked"},
                     deferred={key: dict(checks=1, notice=dict(delivered=True)) for key in ("one", "two")})
        self.assertIs(RUNNER.deferred_rechecks(state, "current", {"one", "two"}, {"one": 2, "two": 2}), state)
        for mutation in ("generation", "active", "missing", "receipt", "checks", "notice", "acked", "not_observed"):
            value = copy.deepcopy(state)
            denials = {"one": 2, "two": 2}
            if mutation == "generation": value["generation"] = "restarted-again"
            elif mutation == "active": value["active"] = {"running": []}
            elif mutation == "missing": value["deferred"].pop("two")
            elif mutation == "receipt": value["receipts"]["two"] = "active"
            elif mutation == "checks": value["deferred"]["two"]["checks"] = 0
            elif mutation == "notice": value["deferred"]["two"]["notice"] = None
            elif mutation == "acked": value["deferred"]["two"]["notice"]["delivered"] = False
            else: denials["two"] = 1
            with self.subTest(mutation=mutation):
                self.assertIsNone(RUNNER.deferred_rechecks(value, "current", {"one", "two"}, denials))

    def test_recheck_log_reader_counts_only_exact_native_denials_for_the_selected_process(self):
        exact = "cannot durably admit event; task was not executed error=recovery_source_not_member input_id=" + "a" * 64
        colored = exact.replace("error=", "\x1b[3merror\x1b[0m\x1b[2m=\x1b[0m").replace(
            "input_id=", "\x1b[3minput_id\x1b[0m\x1b[2m=\x1b[0m") + "\x1b[0m"
        result = subprocess.CompletedProcess([], 0, exact + "\n" + colored + "\nunrelated input_id=" + "b" * 64 + "\n", "")
        with mock.patch.object(RUNNER.subprocess, "run", return_value=result) as command:
            self.assertEqual(RUNNER.native_denial_counts("buzz-test.service", 123), {"a" * 64: 2})
        self.assertIn("_PID=123", command.call_args.args[0])

    def test_receipt_readback_does_not_require_provider_to_stay_awake_after_completion(self):
        """Actual completed journal remains readable after the idle bound."""
        snapshot = {"phase": "starting", "receipts": {"delivery": "completed"},
                    "input_sources": {"delivery": [{"event_id": "original"}]}}
        current = mock.Mock(side_effect=lambda require_ready=True: None if require_ready else snapshot)
        with mock.patch.object(recovery_controller, "process_live", return_value=True):
            # Bind the actual helper used by the L3 completion oracle.
            self.assertEqual(RUNNER.completed_source_binding(current, "delivery"), [{"event_id": "original"}])
        current.assert_called_once_with(require_ready=False)

    def test_receipt_readback_never_treats_active_or_dead_runtime_as_completed(self):
        for status, live in (("active", True), ("cancelled", True), ("completed", False)):
            snapshot = {"receipts": {"delivery": status}, "input_sources": {"delivery": [{"event_id": "original"}]}}
            with self.subTest(status=status, live=live), mock.patch.object(recovery_controller, "process_live", return_value=live):
                with self.assertRaises(AssertionError):
                    RUNNER.completed_source_binding(lambda **_: snapshot, "delivery")

    def test_provider_spawned_by_worker_thread_is_not_mistaken_for_idle(self):
        script = ("import subprocess, sys, threading\n"
                  "def worker():\n"
                  " p = subprocess.Popen(['/usr/bin/sleep', '60'])\n"
                  " print(p.pid, flush=True)\n"
                  " sys.stdin.readline()\n"
                  " p.terminate(); p.wait(timeout=5)\n"
                  "t = threading.Thread(target=worker); t.start(); t.join()\n")
        child = subprocess.Popen([sys.executable, "-I", "-c", script], env={},
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        try:
            self.assertTrue(select.select([child.stdout], [], [], 5)[0])
            provider_pid = int(child.stdout.readline())
            self.assertIn(provider_pid, RUNNER.provider_children(child.pid))
        finally:
            child.communicate(b"stop\n", timeout=10)

    def test_missing_process_is_unknown_not_an_empty_provider_pool(self):
        with self.assertRaises(FileNotFoundError):
            RUNNER.provider_children(999999999)


class BinaryTests(unittest.TestCase):
    def test_selected_binary_executes_the_supplied_program(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = RUNNER.runtime_binary(Path("/usr/bin/true"), Path(directory))
            self.assertEqual(subprocess.run([str(binary)], check=False).returncode, 0)

    def test_rebuild_of_source_does_not_change_the_next_generation_executable(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            work = base / "run"
            work.mkdir(mode=0o700)
            source = base / "build-output"
            shutil.copyfile("/usr/bin/true", source)
            source.chmod(0o700)
            binary = RUNNER.runtime_binary(source, work)
            original = hashlib.sha256(binary.read_bytes()).hexdigest()
            replacement = base / "new-build-output"
            shutil.copyfile("/usr/bin/false", replacement)
            replacement.chmod(0o700)
            replacement.replace(source)
            self.assertEqual(subprocess.run([str(binary)], check=False).returncode, 0,
                             "restart switched to a concurrently rebuilt binary")
            self.assertEqual(hashlib.sha256(binary.read_bytes()).hexdigest(), original)

    def test_binary_is_a_private_read_only_copy_not_a_link_to_build_output(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            source = Path("/usr/bin/true")
            binary = RUNNER.runtime_binary(source, work)
            self.assertEqual(binary.parent, work)
            self.assertNotEqual((binary.stat().st_dev, binary.stat().st_ino),
                                (source.stat().st_dev, source.stat().st_ino))
            self.assertFalse(binary.is_symlink())
            self.assertEqual(stat.S_IMODE(binary.stat().st_mode), 0o500)
            self.assertEqual(binary.read_bytes(), source.read_bytes())

    def test_staging_collision_does_not_overwrite_an_existing_run_binary(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            binary = RUNNER.runtime_binary(Path("/usr/bin/true"), work)
            original = binary.read_bytes()
            with self.assertRaises(FileExistsError):
                RUNNER.runtime_binary(Path("/usr/bin/false"), work)
            self.assertEqual(binary.read_bytes(), original)


class ReplacementTests(unittest.TestCase):
    def setUp(self):
        self.before = {"generation": "old", "pid": 12001}
        self.after = {"generation": "new", "pid": 12002}
        self.unit = "buzz-recovery-runner-test.service"
        self.command = self.enterContext(mock.patch.object(RUNNER, "systemctl"))
        self.command.return_value = subprocess.CompletedProcess([], 0, "", "")
        self.wait = self.enterContext(mock.patch.object(RUNNER, "wait_for", side_effect=self.wait_for))
        self.live = self.enterContext(mock.patch.object(
            recovery_controller, "process_live", side_effect=lambda value: value == self.after))

    @staticmethod
    def wait_for(label, predicate, timeout=90):
        for _ in range(3):
            value = predicate()
            if value:
                return value
        raise RuntimeError("timed out: " + label)

    def replace(self, mode, current=None):
        return RUNNER.replace_runtime(mode, self.unit, self.before, current or (lambda: self.after))

    def test_planned_restart_observes_new_generation(self):
        """L1-REC-RUN-001 Preserve the existing planned replacement boundary."""
        self.assertEqual(self.replace("planned"), self.after)
        self.command.assert_called_once_with("restart", self.unit)

    def test_crash_signals_the_entire_control_group(self):
        """L1-REC-RUN-002 Do not weaken the scenario to main-process-only kill."""
        self.assertEqual(self.replace("crash"), self.after)
        self.assertEqual(self.command.call_args.args,
                         ("kill", "--kill-whom=all", "--signal=SIGKILL", self.unit))

    def test_same_generation_does_not_count_as_replacement(self):
        """L1-REC-RUN-003 A successful systemctl command is not readiness proof."""
        with self.assertRaisesRegex(RuntimeError, "replacement runtime"):
            self.replace("planned", lambda: self.before)

    def test_nonzero_kill_with_old_process_dead_observes_replacement(self):
        """L1-REC-RUN-004 Auxiliary-process EINVAL must not skip real recovery observation."""
        def nonzero(*args, check=True):
            result = subprocess.CompletedProcess(args, 1, "", "auxiliary process: Invalid argument")
            if check:
                result.check_returncode()
            return result
        self.command.side_effect = nonzero
        self.assertEqual(self.replace("crash"), self.after)
        self.live.assert_any_call(self.before)
        self.live.assert_any_call(self.after)

    def test_successful_kill_does_not_prove_old_process_died(self):
        """L1-REC-RUN-005 A new journal cannot hide a still-live old process."""
        self.live.side_effect = lambda value: True
        with self.assertRaisesRegex(RuntimeError, "old process"):
            self.replace("crash")

    def test_unknown_old_process_liveness_fails_without_readiness_success(self):
        """L1-REC-RUN-006 Permission errors are unknown, never death proof."""
        self.live.side_effect = PermissionError("proc unavailable")
        with self.assertRaises(PermissionError):
            self.replace("crash")

    def test_dead_replacement_journal_is_not_readiness(self):
        """L1-REC-RUN-007 A stale ready journal is not a live replacement."""
        self.live.side_effect = lambda value: False
        with self.assertRaisesRegex(RuntimeError, "replacement runtime"):
            self.replace("crash")

    def test_no_replacement_after_verified_death_still_fails(self):
        """L1-REC-RUN-008 Killing without restarting is not recovery."""
        with self.assertRaisesRegex(RuntimeError, "replacement runtime"):
            self.replace("crash", lambda: None)


if __name__ == "__main__":
    unittest.main()
