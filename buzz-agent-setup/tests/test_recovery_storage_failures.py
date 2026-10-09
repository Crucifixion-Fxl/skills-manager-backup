"""Real SQLite failures at the public controller seam; Relay is a boundary fake."""
import contextlib
import io
import json
from pathlib import Path
import sqlite3
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import recovery_controller
from agent_recovery import RecoveryStore
import test_recovery_fairness as fixture


class StorageFailureTests(unittest.TestCase):
    write = fixture.FairnessTests.write
    write_snapshot = fixture.FairnessTests.write_snapshot
    validate_route = fixture.FairnessTests.validate_route
    inventory = fixture.FairnessTests.inventory
    tick = fixture.FairnessTests.tick

    def setUp(self):
        fixture.FairnessTests.setUp(self)
        self.inventory(2, 1)
        self.failed_path = self.state / "fair-0.sqlite3"
        self.connect = sqlite3.connect
        self.connections = []

    def tearDown(self):
        for connection in self.connections:
            connection.close()
        fixture.FairnessTests.tearDown(self)

    def assert_isolated(self, result, code):
        failed = result["agents"]["fair-0"]
        self.assertEqual(failed["continued"], 0)
        self.assertIn(code, failed["errors"])
        self.assertIn("owner", failed["remediation"])
        self.assertIn("保留", failed["remediation"])
        self.assertEqual(result["agents"]["fair-1"]["continued"], 1)
        self.assertFalse(any(e["content"] == "@fair-0 continue" for e in self.relay.events.values()))

    def test_corrupt_agent_database_reports_reason_without_starving_healthy_agent(self):
        original = "private database content must not be printed"
        self.write(self.failed_path, original)
        result = self.tick()
        self.assert_isolated(result, "recovery_storage_invalid")
        self.assertEqual(self.failed_path.read_text(), original, "corrupt outbox was reset")
        self.assertNotIn(original, json.dumps(result))

    def test_corrupt_scheduler_cli_returns_structured_failure_without_traceback(self):
        path = self.state / ".scheduler.sqlite3"
        original = "private scheduler content must be preserved"
        self.write(path, original)
        config = self.base / "controller.json"
        self.write(config, json.dumps(self.config))
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(recovery_controller, "RecoveryRelay", return_value=self.relay), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = recovery_controller.main(["--config", str(config)])
        self.assertEqual(status, 2)
        self.assertEqual(stdout.getvalue(), "")
        message = json.loads(stderr.getvalue())
        self.assertEqual(message["error"], "recovery_storage_invalid")
        self.assertIn("owner", message["remediation"])
        self.assertIn("保留", message["remediation"])
        self.assertNotIn(original, stderr.getvalue())
        self.assertEqual(path.read_text(), original)
        self.assertEqual(self.relay.published, [])

    def connection_factory(self, configure):
        def connect(path, *args, **kwargs):
            connection = self.connect(path, *args, **kwargs)
            self.connections.append(connection)
            if Path(path) == self.failed_path:
                configure(connection)
            return connection
        return connect

    def test_readonly_outbox_cannot_publish_and_next_tick_recovers_without_reset(self):
        with RecoveryStore(self.failed_path):
            pass
        def readonly(connection):
            connection.execute("PRAGMA query_only=ON")
        with mock.patch("sqlite3.connect", side_effect=self.connection_factory(readonly)):
            result = self.tick()
        self.assert_isolated(result, "recovery_storage_unavailable")
        recovered = self.tick()
        self.assertEqual(recovered["agents"]["fair-0"]["continued"], 1)
        self.assertEqual(recovered["agents"]["fair-1"]["continued"], 0)
        self.assertEqual(sum(e["content"] == "@fair-0 continue" for e in self.relay.events.values()), 1)

    def test_actual_sqlite_full_preserves_work_and_reports_capacity_not_corruption(self):
        with RecoveryStore(self.failed_path) as store:
            store.db.execute("CREATE TABLE capacity_fixture (payload BLOB)")
            store.db.execute("CREATE TRIGGER capacity_fixture_insert AFTER INSERT ON messages "
                             "BEGIN INSERT INTO capacity_fixture VALUES (zeroblob(1048576)); END")
            store.db.commit()
        def cap(connection):
            pages = connection.execute("PRAGMA page_count").fetchone()[0]
            self.assertEqual(connection.execute(f"PRAGMA max_page_count={pages + 1}").fetchone()[0], pages + 1)
        with mock.patch("sqlite3.connect", side_effect=self.connection_factory(cap)):
            result = self.tick()
        self.assert_isolated(result, "recovery_storage_capacity_exceeded")
        with self.connect(self.failed_path) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM messages").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM covered").fetchone()[0], 0)
            db.execute("DROP TRIGGER capacity_fixture_insert")
        self.assertEqual(self.tick()["agents"]["fair-0"]["continued"], 1)

    def test_real_database_lock_is_reported_and_does_not_starve_other_agents(self):
        with RecoveryStore(self.failed_path):
            pass
        blocker = self.connect(self.failed_path)
        self.connections.append(blocker)
        blocker.execute("BEGIN EXCLUSIVE")
        def short_busy_timeout(connection):
            connection.execute("PRAGMA busy_timeout=1")
        with mock.patch("sqlite3.connect", side_effect=self.connection_factory(short_busy_timeout)):
            result = self.tick()
        self.assert_isolated(result, "recovery_storage_busy")
        blocker.rollback()
        self.assertEqual(self.tick()["agents"]["fair-0"]["continued"], 1)

    def test_ack_write_failure_reconciles_same_signed_continue_after_repair(self):
        failed_connection = []
        publish = self.relay.publish
        def remember(connection):
            failed_connection[:] = [connection]
        def publish_then_storage_failure(event):
            publish(event)
            if event["content"] == "@fair-0 continue":
                failed_connection[0].execute("PRAGMA query_only=ON")
        self.relay.publish = publish_then_storage_failure
        with mock.patch("sqlite3.connect", side_effect=self.connection_factory(remember)):
            result = self.tick()
        failed = result["agents"]["fair-0"]
        self.assertIn("recovery_storage_unavailable", failed["errors"])
        self.assertEqual(failed["continued"], 0, "failed durable ACK was reported as success")
        event_ids = [e["id"] for e in self.relay.events.values() if e["content"] == "@fair-0 continue"]
        self.assertEqual(len(event_ids), 1, "fixture never published the frozen continue")
        with self.connect(self.failed_path) as db:
            row = db.execute("SELECT acked FROM messages WHERE json_extract(event, '$.id')=?", (event_ids[0],)).fetchone()
            self.assertEqual(row, (0,))
            self.assertEqual(db.execute("SELECT count(*) FROM covered").fetchone()[0], 0)
        published = list(self.relay.published)
        self.relay.publish = publish
        repaired = self.tick()
        self.assertEqual(repaired["agents"]["fair-0"]["continued"], 1)
        self.assertEqual(self.relay.published, published, "storage repair re-signed or reposted a committed event")
        with self.connect(self.failed_path) as db:
            self.assertEqual(db.execute("SELECT acked FROM messages WHERE json_extract(event, '$.id')=?", (event_ids[0],)).fetchone(), (1,))

    def test_failed_store_open_closes_sqlite_connection_and_releases_writer_lock(self):
        self.write(self.failed_path, "not a sqlite database")
        failed_connection = []
        def remember(connection):
            failed_connection.append(connection)
        with mock.patch("sqlite3.connect", side_effect=self.connection_factory(remember)):
            with self.assertRaises(sqlite3.DatabaseError):
                with RecoveryStore(self.failed_path):
                    self.fail("corrupt state was accepted")
        with self.assertRaises(sqlite3.ProgrammingError, msg="failed open leaked its SQLite connection"):
            failed_connection[0].execute("SELECT 1")
        # A second open must reach SQLite, not fail on an orphan controller lock.
        with self.assertRaises(sqlite3.DatabaseError):
            with RecoveryStore(self.failed_path):
                self.fail("corrupt state was accepted after reopen")


if __name__ == "__main__":
    unittest.main()
