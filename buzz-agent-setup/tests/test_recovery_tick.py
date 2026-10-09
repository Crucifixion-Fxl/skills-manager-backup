"""L2 real controller/SQLite/OS process; only external relay/manager are doubles."""
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import test_recovery_controller as fixtures
from agent_recovery import RecoveryStore
from recovery_controller import run_round
from recovery_schedule import RecoverySchedule


class RecoveryTickTests(unittest.TestCase):
    write = fixtures.ControllerTests.write
    write_snapshot = fixtures.ControllerTests.write_snapshot

    def setUp(self):
        fixtures.ControllerTests.setUp(self)
        self.addCleanup(fixtures.ControllerTests.tearDown, self)
        self.invocation = "1" * 32
        self.database = self.state / ".scheduler.sqlite3"

    def round(self):
        with mock.patch.dict(os.environ, {"INVOCATION_ID": self.invocation}):
            return run_round(self.config, main_pid=lambda unit: self.pid, transport=self.relay)

    def receipt(self):
        with sqlite3.connect(self.database) as db:
            row = db.execute("SELECT value FROM scheduling_cursors WHERE key='tick:last'").fetchone()
        self.assertIsNotNone(row, "actual controller round must persist its own receipt")
        return json.loads(row[0])

    def test_L2_REC_TICK_001_actual_round_persists_fresh_completed_receipt(self):
        before = time.monotonic_ns()
        result = self.round()
        after = time.monotonic_ns()
        self.assertEqual(result["agents"]["test-dev"]["continued"], 1)
        receipt = self.receipt()
        continue_event = next(e for e in self.relay.events.values() if e["content"] == "@test-dev continue")
        self.assertEqual(receipt["continued_events"], [continue_event["id"]])
        self.assertEqual(receipt["invocation_id"], self.invocation)
        self.assertEqual(receipt["origin"], "systemd")
        self.assertEqual(receipt["phase"], "completed")
        self.assertTrue(receipt["ok"])
        self.assertLessEqual(before, receipt["started_ns"])
        self.assertLessEqual(receipt["started_ns"], receipt["finished_ns"])
        self.assertLessEqual(receipt["finished_ns"], after)
        self.assertEqual(receipt["boot_id"], Path("/proc/sys/kernel/random/boot_id").read_text().strip())
        self.assertNotIn(fixtures.KEY, json.dumps(receipt))

    def test_L2_REC_TICK_002_running_receipt_precedes_any_remote_publication(self):
        observed = []
        publish = self.relay.publish

        def external_publish(event):
            observed.append(self.receipt())
            return publish(event)

        self.relay.publish = external_publish
        self.round()
        self.assertTrue(observed)
        self.assertTrue(all(r["phase"] == "running" and r["finished_ns"] is None and r["ok"] is None for r in observed))

    def test_L2_REC_TICK_003_failed_round_overwrites_old_success_with_failed_result(self):
        self.round()
        self.current["phase"] = "stopping"
        self.write_snapshot(self.current)
        self.invocation = "2" * 32
        result = self.round()
        self.assertTrue(result["agents"]["test-dev"]["errors"])
        receipt = self.receipt()
        self.assertEqual(receipt["invocation_id"], self.invocation)
        self.assertEqual(receipt["phase"], "completed")
        self.assertFalse(receipt["ok"])

    def test_L2_REC_TICK_004_malformed_systemd_invocation_never_runs_recovery(self):
        for value in ("", "0" * 32, "A" * 32, "not-a-systemd-invocation"):
            with self.subTest(value=value):
                self.invocation = value
                with self.assertRaises(ValueError):
                    self.round()
        self.assertEqual(self.relay.events, {})

    def block_receipt(self, phase):
        with RecoveryStore(self.database) as store:
            RecoverySchedule(store.db)
            store.db.execute("CREATE TRIGGER fail_tick BEFORE INSERT ON scheduling_cursors "
                             "WHEN NEW.key='tick:last' AND json_extract(NEW.value,'$.phase')='" + phase + "' "
                             "BEGIN SELECT RAISE(ABORT,'fixture receipt commit failure'); END")
            store.db.commit()

    def test_L2_REC_TICK_005_begin_commit_failure_prevents_remote_work(self):
        self.block_receipt("running")
        with self.assertRaises(sqlite3.Error):
            self.round()
        self.assertEqual(self.relay.events, {})

    def test_L2_REC_TICK_006_complete_commit_failure_keeps_running_receipt_and_delivery(self):
        self.block_receipt("completed")
        with self.assertRaises(sqlite3.Error):
            self.round()
        self.assertTrue(self.relay.events, "the real round must have published before the completion fault")
        self.assertEqual(self.receipt()["phase"], "running")
        self.assertIsNone(self.receipt()["ok"])

    def test_L2_REC_TICK_007_cli_receipt_cannot_claim_systemd_origin(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("INVOCATION_ID", None)
            run_round(self.config, main_pid=lambda unit: self.pid, transport=self.relay)
        self.assertEqual(self.receipt()["origin"], "cli")


if __name__ == "__main__":
    unittest.main()
