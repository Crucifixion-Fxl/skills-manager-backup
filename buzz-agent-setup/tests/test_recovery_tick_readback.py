"""L2 strict, read-only receipt proof with real SQLite/locks/controller/process."""
import copy
import importlib
import json
import os
from pathlib import Path
import sqlite3
import time
import unittest

import test_recovery_tick as fixtures
from agent_recovery import RecoveryStore


class RecoveryTickReadbackTests(unittest.TestCase):
    write = fixtures.RecoveryTickTests.write
    write_snapshot = fixtures.RecoveryTickTests.write_snapshot
    round = fixtures.RecoveryTickTests.round
    receipt = fixtures.RecoveryTickTests.receipt

    def setUp(self):
        fixtures.RecoveryTickTests.setUp(self)
        self.module = importlib.import_module("recovery_tick")
        self.marker = time.monotonic_ns()
        self.round()

    def verify(self):
        return self.module.verify_tick(self.config, started_after=self.marker)

    def replace(self, value):
        with sqlite3.connect(self.database) as db:
            db.execute("UPDATE scheduling_cursors SET value=? WHERE key='tick:last'", (json.dumps(value),))

    def test_L2_REC_TICK_READ_001_fresh_exact_success_is_read_without_any_file_writes(self):
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.state.iterdir() if p.is_file()}
        self.assertEqual(self.verify(), self.receipt())
        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.state.iterdir() if p.is_file()})

    def test_L2_REC_TICK_READ_002_stale_failed_incomplete_or_wrong_identity_cannot_pass(self):
        original = self.receipt()
        for patch in ({"started_ns": self.marker - 1}, {"ok": False}, {"origin": "cli"},
                      {"phase": "running", "finished_ns": None, "ok": None},
                      {"boot_id": "00000000-0000-0000-0000-000000000001"},
                      {"config_sha256": "0" * 64}, {"finished_ns": time.monotonic_ns() + 10 ** 12}):
            with self.subTest(patch=patch):
                self.replace({**original, **patch})
                with self.assertRaises(ValueError):
                    self.verify()

    def test_L2_REC_TICK_READ_003_strict_schema_and_types_cannot_be_silently_interpreted(self):
        original = self.receipt()
        for patch in ({"version": True}, {"extra": "untrusted"}, {"invocation_id": "0" * 32},
                      {"started_ns": True}, {"finished_ns": 0}, {"ok": 1}, {"phase": "unknown"},
                      {"continued_events": "not-a-list"}, {"continued_events": ["not-hex"]},
                      {"continued_events": ["1" * 64, "1" * 64]}):
            with self.subTest(patch=patch):
                self.replace({**original, **patch})
                with self.assertRaises(ValueError):
                    self.verify()

    def test_L2_REC_TICK_READ_004_actual_writer_lease_blocks_readback(self):
        with RecoveryStore(self.database):
            with self.assertRaises(ValueError):
                self.verify()
        self.verify()

    def test_L2_REC_TICK_READ_005_unsafe_database_or_lock_never_gets_repaired_on_read(self):
        for path in (self.database, Path(str(self.database) + ".lock")):
            with self.subTest(path=path.name):
                path.chmod(0o644)
                with self.assertRaises(ValueError):
                    self.verify()
                self.assertEqual(path.stat().st_mode & 0o777, 0o644)
                path.chmod(0o600)
                other = self.state / "same-inode"
                os.link(path, other)
                with self.assertRaises(ValueError):
                    self.verify()
                other.unlink()
        self.verify()

    def test_L2_REC_TICK_READ_006_missing_or_corrupt_database_is_not_created_or_reset(self):
        original = self.database.read_bytes()
        self.database.unlink()
        with self.assertRaises(ValueError):
            self.verify()
        self.assertFalse(self.database.exists())
        self.database.write_bytes(b"not-sqlite")
        self.database.chmod(0o600)
        with self.assertRaises(ValueError):
            self.verify()
        self.assertEqual(self.database.read_bytes(), b"not-sqlite")
        self.database.write_bytes(original)

    def test_L2_REC_TICK_READ_007_changed_config_cannot_reuse_previous_success(self):
        self.config = copy.deepcopy(self.config)
        self.config["agents"][0]["revision"] = "f" * 40
        with self.assertRaises(ValueError):
            self.verify()

    def test_L2_REC_TICK_READ_008_duplicate_keys_and_oversize_or_sqlite_view_fail_closed(self):
        valid = self.receipt()
        for text in (json.dumps(valid)[:-1] + ', "ok": true}', " " * 4097 + json.dumps(valid)):
            with self.subTest(size=len(text)):
                with sqlite3.connect(self.database) as db:
                    db.execute("UPDATE scheduling_cursors SET value=? WHERE key='tick:last'", (text,))
                with self.assertRaises(ValueError):
                    self.verify()
        with sqlite3.connect(self.database) as db:
            db.execute("ALTER TABLE scheduling_cursors RENAME TO saved_cursors")
            db.execute("CREATE VIEW scheduling_cursors AS SELECT * FROM saved_cursors")
        with self.assertRaises(ValueError):
            self.verify()


if __name__ == "__main__":
    unittest.main()
