"""SQLite persistence and concurrent transaction contracts; isolated temporary files."""
import concurrent.futures
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

HOSTD = Path(__file__).resolve().parents[1] / "scripts" / "hostd"
sys.path.insert(0, str(HOSTD))
try:
    import store
except ImportError:
    store = None


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(store, "hostd.store not implemented")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name) / "hostd"
        self.path = self.directory / "hostd.db"
        self.db = store.Store(self.path)
        self.addCleanup(self.db.close)
        self.db.reconcile_bindings([self.binding()], now=100)

    def binding(self, name="alpha", channel="channel_a", chat="oc_a", app="cli_a", profile="a"):
        return store.BindingRecord(name, channel, chat, app, "/cfg/" + name,
                                   "/lark/" + profile, "/data/" + profile)

    def reserve(self, db=None, source="om_a", at=100):
        return (db or self.db).reserve_delivery("alpha", source, "f2b", source_at=at, now=100)

    def test_wal_foreign_keys_and_private_files(self):
        self.assertEqual(self.db.conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        self.assertEqual(self.db.conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertEqual(self.directory.stat().st_mode & 0o777, 0o700)
        for path in self.directory.iterdir():
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        names = {row[0] for row in self.db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"binding", "agent", "delivery", "join_request", "agent_chat", "cursor", "connection"} <= names)

    def test_same_app_same_profile_groups_but_conflicts_rollback(self):
        self.db.reconcile_bindings([self.binding(), self.binding("beta", "channel_b", "oc_b")], now=100)
        for bad in (self.binding("gamma", "channel_b", "oc_c"),
                    self.binding("gamma", "channel_c", "oc_b"),
                    self.binding("gamma", "channel_c", "oc_c", profile="different")):
            with self.assertRaises(store.StoreError):
                self.db.reconcile_bindings([self.binding(), self.binding("beta", "channel_b", "oc_b"), bad], now=101)
            self.assertEqual({row["binding_id"] for row in self.db.bindings()}, {"alpha", "beta"})

    def test_reservation_survives_restart_without_new_id(self):
        before = self.reserve()
        other = store.Store(self.path)
        self.addCleanup(other.close)
        after = self.reserve(other)
        self.assertEqual((before.id, after.id, after.status), (before.id, before.id, "pending"))
        self.assertEqual(other.replay_since("alpha", "feishu"), 0)

    def test_two_process_like_writers_reserve_one_operation(self):
        def reserve():
            with store.Store(self.path) as db:
                return self.reserve(db).id
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            ids = list(pool.map(lambda _: reserve(), range(8)))
        self.assertEqual(len(set(ids)), 1)
        self.assertEqual(self.db.conn.execute("SELECT count(*) FROM delivery").fetchone()[0], 1)

    def test_ack_is_idempotent_and_cursor_never_crosses_unacked_earlier_item(self):
        first = self.reserve(source="om_first", at=1000)
        later = self.reserve(source="om_later", at=2000)
        self.db.ack_delivery(later.id, "b" * 64, now=2100)
        self.assertLess(self.db.cursor_position("alpha", "feishu"), 1000)
        self.db.ack_delivery(first.id, "a" * 64, now=2101)
        self.assertEqual(self.db.cursor_position("alpha", "feishu"), 2000)
        self.assertEqual(self.db.replay_since("alpha", "feishu"), 1100)
        self.db.ack_delivery(first.id, "a" * 64, now=2200)
        with self.assertRaises(store.StoreError):
            self.db.ack_delivery(first.id, "c" * 64, now=2200)
        self.assertEqual(self.db.cursor_position("alpha", "feishu"), 2000)

    def test_transaction_rolls_back_reservation_and_ack_together(self):
        with self.assertRaises(RuntimeError):
            with self.db.transaction():
                op = self.reserve()
                self.db.ack_delivery(op.id, "a" * 64, now=101)
                raise RuntimeError("crash before commit")
        self.assertEqual(self.db.conn.execute("SELECT count(*) FROM delivery").fetchone()[0], 0)
        self.assertEqual(self.db.cursor_position("alpha", "feishu"), 0)

    def test_unknown_outcome_is_retained_and_not_silently_rereserved(self):
        op = self.reserve()
        self.db.fail_delivery(op.id, unknown=True, now=101)
        self.assertEqual(self.reserve().status, "unknown")

    def test_failed_retry_is_explicit_cas_and_reuses_delivery_id(self):
        op = self.reserve()
        self.db.fail_delivery(op.id, now=101)
        self.assertTrue(self.db.retry_delivery(op.id, now=102))
        self.assertFalse(self.db.retry_delivery(op.id, now=103))
        retried = self.reserve()
        self.assertEqual((retried.id, retried.status, retried.attempts), (op.id, "pending", 2))
        self.db.fail_delivery(op.id, unknown=True, now=104)
        self.assertFalse(self.db.retry_delivery(op.id, now=105))

    def test_ack_target_and_outlet_chat_must_match_typed_scope(self):
        op = self.reserve()
        with self.assertRaises(store.StoreError):
            self.db.ack_delivery(op.id, "om_wrong_direction", now=101)
        self.db.register_agent("a" * 64, owner_pubkey="b" * 64, app_id="cli_a", now=100)
        with self.assertRaises(store.StoreError):
            self.db.record_agent_chat("a" * 64, "oc_other", "c" * 64, binding_id="alpha", now=100)

    def test_zero_timestamp_unknown_blocks_advance_and_lowers_backfill(self):
        known = self.reserve(source="om_known", at=2000)
        self.db.ack_delivery(known.id, "a" * 64, now=2001)
        unknown = self.reserve(source="om_unknown", at=0)
        self.db.fail_delivery(unknown.id, unknown=True, now=2002)
        later = self.reserve(source="om_later", at=3000)
        self.db.ack_delivery(later.id, "b" * 64, now=3001)
        self.assertEqual(self.db.cursor_position("alpha", "feishu"), 2000)
        self.assertEqual(self.db.replay_since("alpha", "feishu"), 0)

    def test_missing_binding_fk_and_arbitrary_body_are_refused(self):
        with self.assertRaises(store.StoreError):
            self.db.reserve_delivery("missing", "om_a", "f2b", now=100)
        with self.assertRaises(store.StoreError):
            self.db.reserve_delivery("alpha", "SECRET BODY canary", "f2b", now=100)
        with self.assertRaises(TypeError):
            self.db.reserve_delivery("alpha", "om_a", "f2b", body="SECRET BODY canary", now=100)
        for path in self.directory.iterdir():
            self.assertNotIn(b"SECRET BODY canary", path.read_bytes())

    def test_schema_upgrade_drift_fails_closed(self):
        bad = self.directory / "unsupported.db"
        conn = sqlite3.connect(bad)
        conn.execute("PRAGMA user_version=999")
        conn.close()
        bad.chmod(0o600)
        with self.assertRaises(store.StoreError):
            store.Store(bad)

    def test_supported_version_with_missing_tables_is_not_accepted(self):
        bad = self.directory / "drift.db"
        conn = sqlite3.connect(bad)
        conn.execute("PRAGMA user_version=1"); conn.close(); bad.chmod(0o600)
        with self.assertRaises(store.StoreError):
            store.Store(bad)

    def test_runtime_sidecar_symlink_failure_is_sanitized(self):
        wal = self.path.with_name(self.path.name + "-journal")
        wal.symlink_to(self.path)
        with self.assertRaises(store.StoreError):
            self.reserve()

    def test_connection_error_is_enum_and_agent_chat_survives_restart(self):
        self.db.register_agent("a" * 64, owner_pubkey="b" * 64, app_id="cli_a", now=100)
        self.db.record_agent_chat("a" * 64, "oc_a", "c" * 64, binding_id="alpha", now=100)
        self.db.record_connection("feishu", "cli_a", status="connected", now=100)
        self.db.record_connection("feishu", "cli_a", status="disconnected", error_code="network", now=101)
        self.db.record_connection("feishu", "cli_a", status="connected", now=102)
        with self.assertRaises(store.StoreError):
            self.db.record_connection("feishu", "cli_a", status="failed", error_code="SECRET BODY canary", now=103)
        with store.Store(self.path) as db:
            self.assertEqual(db.conn.execute("SELECT reconnects FROM connection").fetchone()[0], 1)
            self.assertEqual(db.conn.execute("SELECT chat_ref FROM agent_chat").fetchone()[0], "c" * 64)

    def test_unsafe_parent_and_db_symlink_are_refused(self):
        unsafe = Path(self.tmp.name) / "unsafe"
        unsafe.mkdir(mode=0o755)
        with self.assertRaises(store.StoreError):
            store.Store(unsafe / "hostd.db")
        link = self.directory / "linked.db"
        link.symlink_to(self.path)
        with self.assertRaises(store.StoreError):
            store.Store(link)

    def request(self):
        agent = "a" * 64
        self.db.register_agent(agent, owner_pubkey="b" * 64, app_id="cli_a", now=100)
        return self.db.create_join("JOIN-12345678", agent, "b" * 64, "cli_a", "oc_a", kind="channel",
                                   binding_id="alpha", now=100)

    def test_join_current_card_owner_app_and_event_bound_cas(self):
        self.request()
        self.db.rotate_card("JOIN-12345678", "om_card1", now=101)
        self.db.rotate_card("JOIN-12345678", "om_card2", now=102)
        common = dict(request_id="JOIN-12345678", event_id="ev_1", owner_pubkey="b" * 64,
                      app_id="cli_a", card_message_id="om_card2", generation=2, approved=True, now=103)
        for change in ({"owner_pubkey": "c" * 64}, {"app_id": "cli_other"},
                       {"card_message_id": "om_card1"}, {"generation": 1}):
            self.assertFalse(self.db.decide_join(**{**common, **change}))
        self.assertTrue(self.db.decide_join(**common))
        self.assertFalse(self.db.decide_join(**common))
        self.assertFalse(self.db.decide_join(**{**common, "event_id": "ev_2", "approved": False}))
        self.assertTrue(self.db.advance_join("JOIN-12345678", expected="approved", target="applied", now=104))
        self.assertTrue(self.db.advance_join("JOIN-12345678", expected="applied", target="done", now=105))
        self.assertFalse(self.db.advance_join("JOIN-12345678", expected="done", target="requested", now=106))

    def test_concurrent_join_decisions_have_one_winner_and_deadline_is_seven_days(self):
        row = self.request()
        self.assertEqual(row["deadline"], 100 + 7 * 86400)
        self.db.rotate_card("JOIN-12345678", "om_card", now=101)
        def decide(index):
            with store.Store(self.path) as db:
                return db.decide_join("JOIN-12345678", "ev_" + str(index), "b" * 64, "cli_a", "om_card", 1,
                                      approved=bool(index % 2), now=102)
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(sum(pool.map(decide, range(8))), 1)

    def test_expired_request_cannot_be_approved(self):
        self.request()
        self.db.rotate_card("JOIN-12345678", "om_card", now=101)
        self.assertFalse(self.db.decide_join("JOIN-12345678", "ev_a", "b" * 64, "cli_a", "om_card", 1,
                                            approved=True, now=100 + 7 * 86400))
        self.assertEqual(self.db.join_request("JOIN-12345678")["status"], "expired")


if __name__ == "__main__":
    unittest.main()
