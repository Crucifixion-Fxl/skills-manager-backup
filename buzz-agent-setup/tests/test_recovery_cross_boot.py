"""REC-004 at L2: a cross-boot simulation after a full stop, not a physical power cut.

Real files, SQLite and a real live process; only the kernel boot ID read is
substituted to model "before" and "after" a reboot, and the relay is the L2
boundary double. The pre-reboot generation's PID and start ticks are reused
by a live process after the "reboot" (the collision a real reboot produces).
Physical power-off (L4) needs an isolated machine and a separately authorised
window; this test never claims it.
"""
import contextlib
import json
from pathlib import Path
import unittest
from unittest import mock
import uuid

import test_recovery_controller as base  # module import: do not re-collect its tests
from test_agent_recovery import CHANNELS
import recovery_controller
from recovery_tick import read_tick

BOOT_FILE = "/proc/sys/kernel/random/boot_id"
REAL_READ = Path.read_text


@contextlib.contextmanager
def booted(boot_id):
    """Every boot-ID reader (controller liveness, runtime proof, tick receipt) sees this boot."""
    def read_text(path, *args, **kwargs):
        if str(path) == BOOT_FILE:
            return boot_id + "\n"
        return REAL_READ(path, *args, **kwargs)
    with mock.patch.object(Path, "read_text", read_text):
        yield


class CrossBootRecoveryTests(unittest.TestCase):
    setUp = base.ControllerTests.setUp
    tearDown = base.ControllerTests.tearDown
    write = base.ControllerTests.write
    write_snapshot = base.ControllerTests.write_snapshot
    run_round = base.ControllerTests.run_round

    def continues(self):
        return [e for e in self.relay.events.values() if e["content"] == "@test-dev continue"]

    def test_L2_REC_004_pending_work_from_the_previous_boot_resumes_in_the_original_thread(self):
        before_boot, after_boot = str(uuid.uuid4()), REAL_READ(Path(BOOT_FILE)).strip()
        self.assertNotEqual(before_boot, after_boot)
        # Before power-off: the old generation is the live one and holds pending work.
        (self.runtime / (self.current["generation"] + ".json")).unlink()
        self.old.update(boot_id=before_boot, phase="ready")
        self.write_snapshot(self.old)
        with booted(before_boot):
            first = self.run_round()["agents"]["test-dev"]
            self.assertEqual(first["continued"], 0, "a live generation must never be recovered")
            self.assertEqual(read_tick(self.config["state_dir"])["boot_id"], before_boot)
        # Power-off: no stop hook ran, the pending journal is exactly what was on disk.
        pending = json.loads((self.runtime / (self.old["generation"] + ".json")).read_text())
        self.assertEqual(pending["phase"], "ready")
        self.assertTrue(pending["active"])
        # After the reboot the same PID and start ticks belong to a different
        # (live) process: only the boot ID proves the old generation is dead.
        self.assertEqual((self.old["pid"], self.old["process_start_ticks"]),
                         (self.current["pid"], self.current["process_start_ticks"]))
        self.write_snapshot(self.current)
        with booted(after_boot):
            self.assertIs(recovery_controller.process_live(pending), False)
            self.assertIs(recovery_controller.process_live(self.current), True)
            resumed = self.run_round()["agents"]["test-dev"]
            self.assertEqual(resumed["continued"], 1, resumed)
            again = self.run_round()["agents"]["test-dev"]
            self.assertEqual(again["continued"], 0, "a second tick must not duplicate the continue")
            self.assertEqual(read_tick(self.config["state_dir"])["boot_id"], after_boot)
        [event] = self.continues()
        self.assertEqual([t for t in event["tags"] if t[0] == "h"], [["h", CHANNELS[0]]])
        self.assertEqual([t[1] for t in event["tags"] if t[0] == "e" and t[3] == "root"], ["a" * 64])
        self.assertEqual([t for t in event["tags"] if t[0] == "p"], [["p", base.AGENT]])
        [binding] = [t for t in event["tags"] if t[0] == "recovery"]
        self.assertEqual(binding[2], self.current["generation"], "continue must target the post-boot generation")
        self.assertEqual(binding[3:], sorted(pending["triggers"][next(iter(pending["active"]))]))

    def test_L2_REC_004_unknown_boot_identity_is_never_treated_as_death(self):
        self.old.update(boot_id="not-a-boot-id")
        self.write_snapshot(self.old)
        entry = self.run_round()["agents"]["test-dev"]
        self.assertEqual(entry["continued"], 0)
        self.assertTrue(entry["errors"], "an unverifiable previous boot must block, not recover")
        self.assertEqual(self.continues(), [])


if __name__ == "__main__":
    unittest.main()
