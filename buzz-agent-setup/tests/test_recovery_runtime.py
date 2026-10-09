"""L2-ARC process/revision proof using a real, disposable local child process."""
import hashlib
import os
import select
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from recovery_runtime import verify_process
import recovery_runtime


def prewarm_child():
    """Real OS signal recipient; no inherited credentials or business process."""
    script = ("import os, signal; "
              "signal.signal(signal.SIGUSR1, lambda *_: os.write(1, b'prewarmed\\n')); "
              "print('ready', flush=True)\n"
              "while True: signal.pause()\n")
    child = subprocess.Popen([sys.executable, "-I", "-c", script],
                             env={"BUZZ_ACP_RECOVERY_REVISION": "a" * 40}, stdout=subprocess.PIPE)
    if not select.select([child.stdout], [], [], 5)[0] or child.stdout.readline() != b"ready\n":
        child.terminate()
        child.wait(timeout=5)
        child.stdout.close()
        raise RuntimeError("prewarm child not ready")
    return child


class ProcessProofTests(unittest.TestCase):
    def setUp(self):
        self.child = subprocess.Popen(["/usr/bin/sleep", "30"], env={"BUZZ_ACP_RECOVERY_REVISION": "a" * 40})
        self.pid = self.child.pid
        self.sha = hashlib.sha256(Path(f"/proc/{self.pid}/exe").read_bytes()).hexdigest()
        stat = Path(f"/proc/{self.pid}/stat").read_text().rsplit(") ", 1)[1].split()
        self.snapshot = dict(pid=self.pid, process_start_ticks=stat[19],
                             boot_id=Path("/proc/sys/kernel/random/boot_id").read_text().strip())

    def tearDown(self):
        self.child.terminate()
        self.child.wait(timeout=5)

    def verify(self, **overrides):
        args = dict(snapshot=self.snapshot, main_pid=self.pid, binary_sha256=self.sha, revision="a" * 40)
        args.update(overrides)
        return verify_process(**args)

    def test_live_exact_process_binary_and_loaded_revision_are_ready(self):
        self.assertTrue(self.verify())

    def test_reboot_with_same_pid_never_matches_old_journal(self):
        self.assertFalse(self.verify(snapshot={**self.snapshot, "boot_id": "old-boot"}))

    def test_pid_reuse_with_same_boot_never_matches_old_journal(self):
        self.assertFalse(self.verify(snapshot={**self.snapshot, "process_start_ticks": "0"}))

    def test_another_systemd_main_pid_is_not_accepted(self):
        self.assertFalse(self.verify(main_pid=os.getpid()))

    def test_new_config_but_old_running_revision_cannot_resume(self):
        self.assertFalse(self.verify(revision="b" * 40))

    def test_wrong_binary_hash_cannot_resume(self):
        self.assertFalse(self.verify(binary_sha256="0" * 64))

    def test_dead_process_cannot_resume(self):
        self.child.terminate()
        self.child.wait(timeout=5)
        self.assertFalse(self.verify())


class PrewarmProofTests(unittest.TestCase):
    def setUp(self):
        self.child = prewarm_child()
        pid = self.child.pid
        birth = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()[19]
        self.args = dict(snapshot=dict(version=10, phase="starting", pid=pid, process_start_ticks=birth,
                                      boot_id=Path("/proc/sys/kernel/random/boot_id").read_text().strip()),
                         main_pid=lambda: pid,
                         binary_sha256=hashlib.sha256(Path(f"/proc/{pid}/exe").read_bytes()).hexdigest(),
                         revision="a" * 40)

    def tearDown(self):
        self.child.terminate()
        self.child.wait(timeout=5)
        self.child.stdout.close()

    def request(self, **overrides):
        return recovery_runtime.request_prewarm(**{**self.args, **overrides})

    def assert_not_signalled(self):
        self.assertFalse(select.select([self.child.stdout], [], [], 0.05)[0])
        self.assertIsNone(self.child.poll())

    def test_exact_verified_process_receives_repeatable_prewarm_only(self):
        for _ in range(3):
            self.assertTrue(self.request())
            self.assertTrue(select.select([self.child.stdout], [], [], 1)[0])
            self.assertEqual(self.child.stdout.readline(), b"prewarmed\n")

    def test_old_protocol_or_nonstarting_phase_is_never_signalled(self):
        for overrides in ({"version": 9}, {"version": 8}, {"version": 7}, {"version": 6}, {"version": 5}, {"version": 4}, {"version": 3}, {"version": 2}, {"version": 1}, {"version": True}, {"version": 9.0},
                          {"phase": "ready"}, {"phase": "stopping"}, {"phase": "unknown"}):
            with self.subTest(overrides=overrides):
                self.assertFalse(self.request(snapshot={**self.args["snapshot"], **overrides}))
                self.assert_not_signalled()

    def test_wrong_binary_revision_birth_or_main_pid_never_signals(self):
        for overrides in ({"binary_sha256": "0" * 64}, {"revision": "b" * 40},
                          {"main_pid": lambda: os.getpid()},
                          {"snapshot": {**self.args["snapshot"], "process_start_ticks": "0"}},
                          {"snapshot": {**self.args["snapshot"], "boot_id": "wrong"}}):
            with self.subTest(fields=list(overrides)):
                self.assertFalse(self.request(**overrides))
                self.assert_not_signalled()

    def test_unit_replacement_during_pidfd_open_never_signals_the_old_process(self):
        values = iter([self.child.pid, os.getpid()])
        self.assertFalse(self.request(main_pid=lambda: next(values)))
        self.assert_not_signalled()

    def test_process_death_after_pidfd_open_never_falls_back_to_bare_pid(self):
        real_open = os.pidfd_open

        def open_then_exit(pid, flags):
            fd = real_open(pid, flags)
            self.child.terminate()
            self.child.wait(timeout=5)
            return fd

        with patch.object(recovery_runtime.os, "pidfd_open", side_effect=open_then_exit), \
                patch.object(recovery_runtime.signal, "pidfd_send_signal") as send:
            self.assertFalse(self.request())
            send.assert_not_called()

    def test_unavailable_pidfd_never_uses_an_unsafe_fallback(self):
        for failure in (PermissionError, ProcessLookupError, OSError):
            with self.subTest(failure=failure), \
                    patch.object(recovery_runtime.os, "pidfd_open", side_effect=failure), \
                    patch.object(recovery_runtime.os, "kill") as kill:
                self.assertFalse(self.request())
                kill.assert_not_called()
                self.assert_not_signalled()


if __name__ == "__main__":
    unittest.main()
