"""Durable console metadata CAS against real isolated SQLite, never transport."""
import concurrent.futures
from dataclasses import FrozenInstanceError
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "hostd"))
import store

PRINCIPAL = "1" * 64
KEY = "2" * 64
EPOCH = "3" * 64
RECEIPT = "4" * 64
AGENT = "a" * 64
OWNER = "b" * 64
SCOPE = "c" * 64
FILES = "d" * 64


class ConsoleLedger(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "private" / "state.db"
        self.db = store.Store(self.path)
        self.addCleanup(self.db.close)
        self.db.reconcile_bindings([store.BindingRecord("alpha", "channel", "oc_group", "cli_reader",
                                   "/cfg", "/private/config", "/private/data")], now=10)
        self.db.register_agent(AGENT, owner_pubkey=OWNER, app_id="cli_agent", now=10)

    def reserve(self, db=None, *, key=KEY, principal=PRINCIPAL, epoch=EPOCH,
                kind="binding", target="alpha", action="pause", now=11):
        return (db or self.db).reserve_console_operation(principal, key, kind, target, action,
                                                       execution_epoch=epoch, now=now)

    def claim(self, row):
        return self.db.claim_console_operation(row.id, row.principal, row.execution_epoch, now=12)

    def link(self, row, op="restart1", *, scope=SCOPE, files=FILES):
        return self.db.link_console_restart(row.id, op, scope_hash=scope,
                                            protectedfiles_hash=files, now=13)

    def restart(self, op="restart1", agent=AGENT):
        old = store.RestartProcess(101, 1001, "a" * 32)
        self.db.reserve_restart(op, agent, SCOPE, FILES, old, now=12)
        self.db.mark_restart_unknown(op, agent, SCOPE, FILES, now=12)

    def ack(self, op="restart1", agent=AGENT):
        new = store.RestartProcess(202, 2002, "b" * 32)
        self.db.pin_restart(op, agent, SCOPE, FILES, new, now=14)
        self.db.ack_restart(op, agent, SCOPE, FILES, new, now=15)

    def test_committed_queue_reopens_with_same_key_same_id_original_epoch(self):
        first = self.reserve()
        self.assertTrue(first.created)
        self.assertRegex(first.record.id, "^[0-9a-f]{64}$")
        with self.assertRaises(FrozenInstanceError):
            first.record.status = "completed"
        self.db.close()
        with store.Store(self.path) as db:
            retry = self.reserve(db, epoch="5" * 64)
            self.assertFalse(retry.created)
            self.assertEqual(retry.record, first.record)
            self.assertFalse(db.claim_console_operation(first.record.id, PRINCIPAL, "5" * 64, now=20))
            self.assertEqual(db.console_operation(first.record.id, PRINCIPAL).status, "queued")
            self.assertIsNone(db.console_operation(first.record.id, "6" * 64))

    def test_same_key_different_identity_target_kind_or_action_cannot_be_new_intent(self):
        first = self.reserve().record
        for changes in ({"principal": "6" * 64}, {"target": "unknown"},
                        {"action": "resume"}, {"kind": "agent", "target": AGENT, "action": "restart"}):
            with self.subTest(changes=changes), self.assertRaises(store.StoreError):
                self.reserve(**changes)
        self.assertEqual(self.db.console_operation(first.id, PRINCIPAL), first)

    def test_all_unresolved_states_block_new_keys_until_real_completion(self):
        row = self.reserve().record
        for expected in ("queued", "dispatched", "unknown"):
            if expected == "dispatched":
                self.assertTrue(self.claim(row))
            if expected == "unknown":
                self.assertTrue(self.db.hold_console_operation(row.id, reason="timeout", now=13))
            with self.subTest(state=expected), self.assertRaises(store.StoreError):
                self.reserve(key="7" * 64, action="resume")
            self.assertEqual(self.db.console_operation(row.id, PRINCIPAL).status, expected)
        self.assertTrue(self.db.finish_console_operation(row.id, expected="unknown", observed="paused",
                                                        receipt_hash=RECEIPT, now=14))
        self.assertTrue(self.reserve(key="7" * 64, action="resume").created)
        self.assertFalse(self.reserve().created)  # terminal key is not evicted or redispatched.

    def test_claim_requires_exact_principal_epoch_queued_and_current_target(self):
        row = self.reserve(kind="agent", target=AGENT, action="restart").record
        self.assertFalse(self.db.claim_console_operation(row.id, "6" * 64, EPOCH, now=12))
        self.assertFalse(self.db.claim_console_operation(row.id, PRINCIPAL, "5" * 64, now=12))
        self.db.conn.execute("UPDATE agent SET status='paused' WHERE pubkey=?", (AGENT,))
        self.assertFalse(self.claim(row))
        self.db.conn.execute("UPDATE agent SET status='active' WHERE pubkey=?", (AGENT,))
        self.assertTrue(self.claim(row))
        self.assertFalse(self.claim(row))
        self.assertEqual(self.db.console_operation(row.id, PRINCIPAL).status, "dispatched")

    def test_actual_foreign_keys_and_fixed_action_scope_reject_missing_objects(self):
        for changes in ({"kind": "binding", "action": "restart"},
                        {"kind": "agent", "target": AGENT, "action": "pause"},
                        {"kind": "group", "target": "oc_group"},
                        {"target": "unknown"}, {"kind": "agent", "target": "f" * 64, "action": "restart"}):
            with self.subTest(changes=changes), self.assertRaises(store.StoreError):
                self.reserve(**changes)
        row = self.reserve().record
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.conn.execute("UPDATE console_operation SET binding_id='unknown' WHERE id=?", (row.id,))
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.conn.execute("UPDATE console_operation SET action='restart' WHERE id=?", (row.id,))

    def test_rejection_is_only_proven_before_dispatch_and_unknown_never_becomes_rejected(self):
        row = self.reserve().record
        self.assertFalse(self.db.reject_console_operation(row.id, "6" * 64, EPOCH, reason="missing", now=12))
        self.assertTrue(self.db.reject_console_operation(row.id, PRINCIPAL, EPOCH, reason="missing", now=12))
        self.assertFalse(self.reserve().created)
        next_row = self.reserve(key="7" * 64).record
        self.claim(next_row)
        self.assertFalse(self.db.reject_console_operation(next_row.id, PRINCIPAL, EPOCH, reason="missing", now=13))
        self.db.hold_console_operation(next_row.id, reason="cancellation", now=13)
        self.assertFalse(self.db.reject_console_operation(next_row.id, PRINCIPAL, EPOCH, reason="missing", now=14))
        self.assertEqual(self.db.console_operation(next_row.id, PRINCIPAL).status, "unknown")

    def test_finish_requires_exact_state_action_outcome_and_nonempty_digest(self):
        row = self.reserve().record
        self.assertFalse(self.db.finish_console_operation(row.id, expected="dispatched", observed="paused",
                                                         receipt_hash=RECEIPT, now=12))
        self.claim(row)
        self.assertFalse(self.db.finish_console_operation(row.id, expected="unknown", observed="paused",
                                                         receipt_hash=RECEIPT, now=13))
        self.assertFalse(self.db.finish_console_operation(row.id, expected="dispatched", observed="active",
                                                         receipt_hash=RECEIPT, now=13))
        for receipt in ("", None, "raw body", {"body": "PRIVATE-CANARY"}):
            with self.subTest(receipt=receipt), self.assertRaises(store.StoreError):
                self.db.finish_console_operation(row.id, expected="dispatched", observed="paused",
                                                 receipt_hash=receipt, now=13)
        self.assertTrue(self.db.finish_console_operation(row.id, expected="dispatched", observed="paused",
                                                        receipt_hash=RECEIPT, now=13))
        self.assertTrue(self.db.finish_console_operation(row.id, expected="dispatched", observed="paused",
                                                        receipt_hash=RECEIPT, now=14))
        self.assertFalse(self.db.finish_console_operation(row.id, expected="dispatched", observed="paused",
                                                         receipt_hash="8" * 64, now=14))
        self.assertFalse(self.db.hold_console_operation(row.id, reason="timeout", now=14))

    def test_active_binding_does_not_complete_unknown_backfill_or_resume(self):
        for action in ("backfill", "resume"):
            with self.subTest(action=action):
                row = self.reserve(key=("7" if action == "backfill" else "8") * 64, action=action).record
                self.claim(row)
                self.db.hold_console_operation(row.id, reason="recovered", now=13)
                self.assertFalse(self.db.reconcile_console_restart(row.id, now=14))
                self.assertEqual(self.db.console_operation(row.id, PRINCIPAL).status, "unknown")
                # Only a supplied associated actual receipt may release this target.
                self.db.finish_console_operation(row.id, expected="unknown",
                    observed="backfilled" if action == "backfill" else "active", receipt_hash=RECEIPT, now=15)

    def test_restart_cannot_complete_without_linked_actual_ack(self):
        row = self.reserve(kind="agent", target=AGENT, action="restart").record
        self.claim(row)
        self.assertFalse(self.db.finish_console_operation(row.id, expected="dispatched", observed="restarted",
                                                         receipt_hash=RECEIPT, now=13))
        self.assertFalse(self.db.reconcile_console_restart(row.id, now=13))
        with self.db.transaction():
            self.restart()
            self.assertTrue(self.link(row))
        self.db.hold_console_operation(row.id, reason="cancellation", now=13)
        self.assertFalse(self.db.reconcile_console_restart(row.id, now=14))
        self.ack()
        self.db.close()
        with store.Store(self.path) as db:
            self.assertTrue(db.reconcile_console_restart(row.id, now=16))
            receipt = db.console_operation(row.id, PRINCIPAL)
            self.assertEqual((receipt.status, receipt.restart_operation_id), ("completed", "restart1"))
            self.assertRegex(receipt.receipt_hash, "^[0-9a-f]{64}$")
            self.assertFalse(self.reserve(db, kind="agent", target=AGENT, action="restart").created)
            self.assertEqual(db.conn.execute("SELECT count(*) FROM restart_operation").fetchone()[0], 1)

    def test_link_requires_same_actual_reservation_transaction_and_matching_agent_digests(self):
        row = self.reserve(kind="agent", target=AGENT, action="restart").record
        self.claim(row)
        self.restart()  # previously committed unknown must not be borrowed by a new console intent.
        with self.assertRaises(store.StoreError):
            self.link(row)
        with self.db.transaction(), self.assertRaises(store.StoreError):
            self.link(row)
        self.db.conn.execute("DELETE FROM restart_operation WHERE operation_id='restart1'")
        with self.db.transaction():
            self.restart()
            self.assertFalse(self.link(row, scope="e" * 64))
            self.assertFalse(self.link(row, files="e" * 64))
            self.assertTrue(self.link(row))
            self.assertTrue(self.link(row))
        # Existing exact immutable link can be checked inside a later transaction.
        with self.db.transaction():
            self.assertTrue(self.link(row))
            self.assertFalse(self.link(row, op="different"))

    def test_link_rollback_restores_domain_reservation_and_queue_reference_together(self):
        row = self.reserve(kind="agent", target=AGENT, action="restart").record
        self.claim(row)
        with self.assertRaises(RuntimeError):
            with self.db.transaction():
                self.restart()
                self.assertTrue(self.link(row))
                raise RuntimeError("simulated crash before transaction commit")
        self.assertIsNone(self.db.active_restart(AGENT))
        self.assertIsNone(self.db.console_operation(row.id, PRINCIPAL).restart_operation_id)
        with self.db.transaction():
            self.restart()
            self.assertTrue(self.link(row))

    def test_nested_rollback_clears_only_rolled_back_reservation_provenance(self):
        row = self.reserve(kind="agent", target=AGENT, action="restart").record
        self.claim(row)
        with self.db.transaction():
            with self.assertRaises(RuntimeError):
                with self.db.transaction():
                    self.restart()
                    raise RuntimeError("savepoint rollback")
            self.assertIsNone(self.db.active_restart(AGENT))
            # A metadata row introduced without the live reserve API must not
            # inherit provenance from the rolled-back reservation of this ID.
            self.db.conn.execute("INSERT INTO restart_operation VALUES('restart1',?,?,?,101,1001,?,NULL,NULL,NULL,'unknown',12,12)",
                                 (AGENT, SCOPE, FILES, "a" * 32))
            with self.assertRaises(store.StoreError):
                self.link(row)
            self.db.conn.execute("DELETE FROM restart_operation WHERE operation_id='restart1'")
            self.restart()
            with self.assertRaises(RuntimeError):
                with self.db.transaction():
                    self.assertTrue(self.link(row))
                    raise RuntimeError("only link rolled back")
            self.assertIsNone(self.db.console_operation(row.id, PRINCIPAL).restart_operation_id)
            self.assertTrue(self.link(row))  # original outer reservation remains live.

    def test_concurrent_writers_same_key_return_one_operation(self):
        def reserve(_):
            with store.Store(self.path) as db:
                return self.reserve(db)
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            rows = list(pool.map(reserve, range(8)))
        self.assertEqual(sum(row.created for row in rows), 1)
        self.assertEqual(len({row.record.id for row in rows}), 1)
        self.assertEqual(len(self.db.console_operations(PRINCIPAL)), 1)

    def test_terminal_keys_survive_small_ui_limit_and_reopen(self):
        first = None
        for index in range(8):
            row = self.reserve(key=f"{index:064x}", now=11 + index).record
            if first is None:
                first = row
            self.db.reject_console_operation(row.id, PRINCIPAL, EPOCH, reason="missing", now=12)
        visible = self.db.console_operations(PRINCIPAL, limit=1)
        self.assertEqual(len(visible), 1)
        self.assertNotEqual(visible[0].id, first.id)
        self.db.close()
        with store.Store(self.path) as db:
            self.assertFalse(self.reserve(db, key="0" * 64).created)
            self.assertEqual(db.console_operation(first.id, PRINCIPAL).status, "rejected")
            self.assertEqual(db.conn.execute("SELECT count(*) FROM console_operation").fetchone()[0], 8)

    def test_exact_request_lookup_is_identity_scoped_not_limited_by_ui_or_current_target(self):
        first = self.reserve(kind="agent", target=AGENT, action="restart").record
        self.db.reject_console_operation(first.id, PRINCIPAL, EPOCH, reason="driver", now=12)
        other = self.reserve(key="9" * 64, now=30).record
        self.db.reject_console_operation(other.id, PRINCIPAL, EPOCH, reason="missing", now=12)
        self.db.conn.execute("UPDATE agent SET status='retired' WHERE pubkey=?", (AGENT,))
        visible = self.db.console_operations(PRINCIPAL, limit=1)
        self.assertEqual(len(visible), 1)
        self.assertEqual(visible[0].id, other.id)
        saved = self.db.console_operation(first.id, PRINCIPAL)
        self.assertEqual(self.db.console_request(PRINCIPAL, KEY), saved)
        self.assertIsNone(self.db.console_request("6" * 64, KEY))
        self.assertIsNone(self.db.console_request(PRINCIPAL, "8" * 64))
        for principal, key in (("raw token", KEY), (PRINCIPAL, "raw key")):
            with self.assertRaises(store.StoreError):
                self.db.console_request(principal, key)

    def test_typed_hashes_fixed_reasons_bounded_read_and_canary_absent(self):
        for key in (None, "", "F" * 64, "a" * 63, "token-canary", {"body": "PRIVATE-CANARY"}):
            with self.subTest(key=key), self.assertRaises(store.StoreError):
                self.reserve(key=key)
        row = self.reserve().record
        with self.assertRaises(store.StoreError):
            self.db.hold_console_operation(row.id, reason="PRIVATE-CANARY", now=12)
        with self.assertRaises(TypeError):
            self.db.reserve_console_operation(PRINCIPAL, KEY, "binding", "alpha", "pause",
                                              execution_epoch=EPOCH, now=11, body="PRIVATE-CANARY")
        for limit in (True, 0, -1, 129):
            with self.assertRaises(store.StoreError):
                self.db.console_operations(PRINCIPAL, limit=limit)
        self.assertNotIn("PRIVATE-CANARY", "\n".join(self.db.conn.iterdump()))
        for path in self.path.parent.iterdir():
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertNotIn(b"PRIVATE-CANARY", path.read_bytes())

    def test_pending_query_keyset_is_bounded_identity_scoped_and_status_stable(self):
        oldest = self.reserve().record
        self.claim(oldest)
        terminal = self.reserve(kind="agent", target=AGENT, action="restart", key="9" * 64, now=30).record
        self.db.reject_console_operation(terminal.id, PRINCIPAL, EPOCH, reason="missing", now=31)
        first = self.db.console_pending_operations(PRINCIPAL, limit=1)
        self.assertEqual(first, [self.db.console_operation(oldest.id, PRINCIPAL)])
        self.assertEqual(self.db.console_pending_operations("6" * 64), [])
        self.db.hold_console_operation(oldest.id, reason="recovered", now=40)
        self.assertEqual(self.db.console_pending_operations(PRINCIPAL, after=(oldest.created_at, oldest.id)), [])
        self.assertEqual(self.db.console_pending_operations(PRINCIPAL)[0].id, oldest.id)
        for after in ([11, oldest.id], (True, oldest.id), (11, "invalid"), (11,), "payload"):
            with self.assertRaises(store.StoreError):
                self.db.console_pending_operations(PRINCIPAL, after=after)
        for limit in (True, 0, 129):
            with self.assertRaises(store.StoreError):
                self.db.console_pending_operations(PRINCIPAL, limit=limit)

    def test_schema_four_exact_upgrade_preserves_all_old_rows_and_rejects_drift(self):
        self.db.close()
        schema_four = store._SCHEMA_V3 + store._UPGRADE_V4_SCHEMA
        for drift in (False, True):
            with self.subTest(drift=drift):
                path = Path(self.tmp.name) / ("drift" if drift else "version4") / "state.db"
                path.parent.mkdir(mode=0o700)
                conn = sqlite3.connect(path)
                path.chmod(0o600)
                conn.executescript(schema_four)
                conn.execute("INSERT INTO agent VALUES(?,?,NULL,NULL,'active',10)", (AGENT, OWNER))
                conn.execute("INSERT INTO restart_operation VALUES('restart1',?,?,?,101,1001,?,202,2002,?,'unknown',10,11)",
                             (AGENT, SCOPE, FILES, "a" * 32, "b" * 32))
                conn.execute("INSERT INTO app_profile VALUES('cli_reader','/private/config','/private/data')")
                conn.execute("INSERT INTO binding VALUES('alpha','channel','oc_group','cli_reader','/cfg','','','active',10,10)")
                conn.execute("INSERT INTO cursor VALUES('alpha','','relay',42,10)")
                conn.execute("INSERT INTO delivery VALUES(?,'alpha',NULL,'om_source','f2b','feishu',?,'','','acked',42,1,10,10)",
                             ("e" * 64, "f" * 64))
                if drift:
                    conn.execute("CREATE TABLE alien(body TEXT)")
                conn.execute("PRAGMA user_version=4")
                before = {name: conn.execute(f'SELECT * FROM "{name}"').fetchall()
                          for name, in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
                conn.commit(); conn.close()
                if drift:
                    with self.assertRaises(store.StoreError):
                        store.Store(path)
                    with sqlite3.connect(path) as conn:
                        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 4)
                        self.assertIsNone(conn.execute("SELECT name FROM sqlite_master WHERE name='console_operation'").fetchone())
                else:
                    with store.Store(path) as db:
                        self.assertEqual(db.conn.execute("PRAGMA user_version").fetchone()[0], store.SCHEMA_VERSION)
                        for name, rows in before.items():
                            self.assertEqual([tuple(row) for row in db.conn.execute(f'SELECT * FROM "{name}"')], rows)
                        self.assertEqual(db.console_operations(PRINCIPAL), [])


if __name__ == "__main__":
    unittest.main()
