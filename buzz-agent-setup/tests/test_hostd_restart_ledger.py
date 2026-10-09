"""Real SQLite restart intents; no processes, credentials, or external transport."""
import concurrent.futures
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "hostd"))
import store

AGENT = "a" * 64
OWNER = "b" * 64
SCOPE = "c" * 64
FILES = "d" * 64


class RestartLedger(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "private" / "state.db"
        self.db = store.Store(self.path)
        self.addCleanup(self.db.close)
        self.db.register_agent(AGENT, owner_pubkey=OWNER, app_id="cli_agent", now=10)

    def process(self, pid=41, start=100, invocation="1" * 32):
        self.assertTrue(hasattr(store, "RestartProcess"), "typed restart process is absent")
        return store.RestartProcess(pid, start, invocation)

    def reserve(self, db=None, op="op1", **kwargs):
        return (db or self.db).reserve_restart(op, AGENT, SCOPE, FILES,
                                              kwargs.pop("old", self.process()), now=11, **kwargs)

    def unknown(self):
        reservation = self.reserve()
        self.assertTrue(self.db.mark_restart_unknown("op1", AGENT, SCOPE, FILES, now=12))
        return reservation.record

    def test_reserved_reopen_is_held_and_duplicate_intent_reuses_record(self):
        first = self.reserve()
        self.assertTrue(first.created)
        self.db.close()
        with store.Store(self.path) as db:
            second = self.reserve(db, op="different_intent")
            self.assertFalse(second.created)
            self.assertEqual(second.record, first.record)
            self.assertEqual(db.active_restart(AGENT), first.record)
            self.assertEqual(db.pending_restarts(), [first.record])
            self.assertIsNone(db.restart_record("different_intent"))

    def test_unknown_first_pin_survives_reopen_and_only_exact_ack_releases(self):
        self.unknown()
        new = self.process(42, 120, "2" * 32)
        self.assertTrue(self.db.pin_restart("op1", AGENT, SCOPE, FILES, new, now=13))
        self.db.close()
        with store.Store(self.path) as db:
            row = db.active_restart(AGENT)
            self.assertEqual((row.state, row.new_process), ("unknown", new))
            self.assertFalse(db.pin_restart("op1", AGENT, SCOPE, FILES,
                                             self.process(43, 130, "3" * 32), now=14))
            self.assertFalse(db.ack_restart("op1", AGENT, SCOPE, "e" * 64, new, now=14))
            self.assertFalse(db.ack_restart("op1", AGENT, SCOPE, FILES,
                                             self.process(43, 130, "3" * 32), now=14))
            self.assertTrue(db.ack_restart("op1", AGENT, SCOPE, FILES, new, now=14))
            self.assertTrue(db.ack_restart("op1", AGENT, SCOPE, FILES, new, now=15))
            self.assertIsNone(db.active_restart(AGENT))
            self.assertEqual(db.restart_record("op1").state, "acked")
            self.assertTrue(self.reserve(db, op="op2", old=new).created)

    def test_unpinned_unknown_has_no_success_or_implicit_new_operation(self):
        self.unknown()
        self.db.close()
        with store.Store(self.path) as db:
            self.assertIsNone(db.active_restart(AGENT).new_process)
            self.assertFalse(db.ack_restart("op1", AGENT, SCOPE, FILES,
                                             self.process(42, 120, "2" * 32), now=20))
            self.assertFalse(self.reserve(db, op="op2").created)
            self.assertEqual(len(db.pending_restarts()), 1)

    def test_reserved_cannot_pin_or_ack_before_dispatch_mark(self):
        self.reserve()
        new = self.process(42, 120, "2" * 32)
        self.assertFalse(self.db.pin_restart("op1", AGENT, SCOPE, FILES, new, now=12))
        self.assertFalse(self.db.ack_restart("op1", AGENT, SCOPE, FILES, new, now=12))
        self.assertEqual(self.db.active_restart(AGENT).state, "reserved")

    def test_revoked_agent_cannot_mark_dispatch_but_receipt_remains_readable(self):
        before = self.reserve().record
        self.db.conn.execute("UPDATE agent SET status='paused' WHERE pubkey=?", (AGENT,))
        self.assertFalse(self.db.mark_restart_unknown("op1", AGENT, SCOPE, FILES, now=12))
        self.assertEqual(self.db.active_restart(AGENT), before)
        self.assertEqual(self.db.restart_record("op1"), before)
        self.assertEqual(self.db.pending_restarts(), [before])

    def test_pin_exact_repeat_and_old_identity_rejected(self):
        self.unknown()
        for unchanged in (self.process(), self.process(42, 120, "1" * 32),
                          self.process(41, 100, "2" * 32)):
            self.assertFalse(self.db.pin_restart("op1", AGENT, SCOPE, FILES, unchanged, now=13))
        new = self.process(41, 120, "2" * 32)  # PID reuse is valid only with new start/invocation.
        self.assertTrue(self.db.pin_restart("op1", AGENT, SCOPE, FILES, new, now=13))
        self.assertTrue(self.db.pin_restart("op1", AGENT, SCOPE, FILES, new, now=14))
        self.assertEqual(self.db.active_restart(AGENT).new_process, new)

    def test_conflicts_scope_files_agent_and_opid_do_not_change_original(self):
        before = self.reserve().record
        for scope, files, old in (("e" * 64, FILES, self.process()),
                                  (SCOPE, "e" * 64, self.process()),
                                  (SCOPE, FILES, self.process(42, 120, "2" * 32))):
            with self.assertRaises(store.StoreError):
                self.db.reserve_restart("op2", AGENT, scope, files, old, now=12)
        self.db.register_agent("f" * 64, owner_pubkey=OWNER, app_id="cli_other", now=12)
        with self.assertRaises(store.StoreError):
            self.db.reserve_restart("op1", "f" * 64, SCOPE, FILES, self.process(), now=12)
        with self.assertRaises(store.StoreError):
            self.db.reserve_restart("op3", "e" * 64, SCOPE, FILES, self.process(), now=12)
        self.db.conn.execute("UPDATE agent SET status='paused' WHERE pubkey=?", (AGENT,))
        with self.assertRaises(store.StoreError):
            self.reserve(op="op4")
        self.assertEqual(self.db.active_restart(AGENT), before)

    def test_concurrent_writers_reserve_one_operation_and_pin_once(self):
        def reserve(index):
            with store.Store(self.path) as db:
                return self.reserve(db, op=f"op{index}")
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(reserve, range(8)))
        self.assertEqual(sum(row.created for row in results), 1)
        self.assertEqual(len({row.record.operation_id for row in results}), 1)
        opid = results[0].record.operation_id
        self.db.mark_restart_unknown(opid, AGENT, SCOPE, FILES, now=12)
        def pin(index):
            with store.Store(self.path) as db:
                return db.pin_restart(opid, AGENT, SCOPE, FILES,
                                      self.process(50 + index, 150 + index, f"{index + 2:032x}"), now=13)
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(sum(pool.map(pin, range(8))), 1)

    def test_transaction_abort_retains_prior_unknown_and_pin(self):
        self.unknown()
        before = self.db.active_restart(AGENT)
        self.db.conn.execute("CREATE TRIGGER fail_restart BEFORE UPDATE ON restart_operation BEGIN SELECT RAISE(ABORT,'private-canary'); END")
        with self.assertRaises(store.StoreError) as caught:
            self.db.pin_restart("op1", AGENT, SCOPE, FILES,
                                self.process(42, 120, "2" * 32), now=13)
        self.assertNotIn("private-canary", str(caught.exception))
        self.db.conn.execute("DROP TRIGGER fail_restart")
        self.assertEqual(self.db.active_restart(AGENT), before)
        with self.assertRaises(RuntimeError):
            with self.db.transaction():
                self.db.pin_restart("op1", AGENT, SCOPE, FILES,
                                    self.process(42, 120, "2" * 32), now=13)
                raise RuntimeError("simulated crash before commit")
        self.assertEqual(self.db.active_restart(AGENT), before)

    def test_typed_validation_and_body_canary_absent_from_sql_and_files(self):
        for values in ((True, 100, "1" * 32), (0, 100, "1" * 32),
                       (41, -1, "1" * 32), (41, 100, "0" * 32),
                       (41, 100, "A" * 32), (41, 100, "token-canary")):
            with self.assertRaises(store.StoreError):
                self.process(*values)
        self.reserve()
        for key in ("message_body", "env", "key", "prompt"):
            with self.assertRaises(TypeError):
                self.db.reserve_restart("canary", AGENT, SCOPE, FILES,
                                        self.process(), now=12, **{key: "BODY-CANARY-DO-NOT-STORE"})
        with self.assertRaises(store.StoreError):
            self.db.reserve_restart("BODY-CANARY-DO-NOT-STORE\n", AGENT, SCOPE, FILES, self.process(), now=12)
        dump = "\n".join(self.db.conn.iterdump())
        self.assertNotIn("BODY-CANARY-DO-NOT-STORE", dump)
        for path in self.path.parent.iterdir():
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertNotIn(b"BODY-CANARY-DO-NOT-STORE", path.read_bytes())
        for limit in (True, 0, -1, 257):
            with self.assertRaises(store.StoreError):
                self.db.pending_restarts(limit=limit)

    def test_exact_schema_one_two_three_upgrade_preserves_rows(self):
        for version, schema in ((1, store._SCHEMA_V1), (2, store._SCHEMA_V2),
                                (3, store._SCHEMA_V2 + store._UPGRADE_V3_SCHEMA)):
            with self.subTest(version=version):
                path = Path(self.tmp.name) / f"version{version}" / "state.db"
                path.parent.mkdir(mode=0o700)
                conn = sqlite3.connect(path)
                path.chmod(0o600)
                conn.executescript(schema)
                conn.execute("INSERT INTO agent VALUES(?,?,?,?,'active',?)",
                             (AGENT, OWNER, "cli_agent", "/protected/reference", 10))
                conn.execute("INSERT INTO app_profile VALUES('cli_agent','/private/config','/private/data')")
                conn.execute("INSERT INTO binding VALUES('alpha','channel','oc_group','cli_agent','/cfg','','','active',10,10)")
                conn.execute("INSERT INTO cursor VALUES('alpha','','feishu',42,10)")
                conn.execute("INSERT INTO delivery VALUES(?,'alpha',NULL,'om_source','f2b','feishu',?,'','','acked',42,1,10,10)",
                             ("e" * 64, "f" * 64))
                conn.execute("INSERT INTO state_snapshot VALUES('alpha',7,?,'/legacy/state.json',?,'legacy:cli_agent:union_id',10)",
                             ("f" * 64, "0" * 64))
                conn.execute("INSERT INTO state_map VALUES('alpha','b2f',?,'om_target',NULL)", ("f" * 64,))
                conn.execute("INSERT INTO join_request VALUES('JOIN-12345678',?,?,'cli_agent','oc_group',NULL,'new_binding','done','om_card',1,10,20,604810)",
                             (AGENT, OWNER))
                if version >= 2:
                    conn.execute("INSERT INTO join_transport VALUES('JOIN-12345678',2,100,1,1,'sent',0,'om_card',20)")
                if version >= 3:
                    conn.execute("INSERT INTO event_target VALUES('alpha','cli_agent','om_target','om_root','im.message.receive_v1',10)")
                    conn.execute("INSERT INTO outlet_receipt VALUES(?,'cli_agent','om_target','','')", ("e" * 64,))
                    conn.execute("INSERT INTO effect_plan VALUES('JOIN-12345678','channel','alpha',?,'/private/mirror.env','/cfg',10,20)",
                                 ("f" * 64,))
                    conn.execute("INSERT INTO effect_step VALUES('JOIN-12345678','runtime','unknown',?,'','',100,1,10,20)", (SCOPE,))
                conn.execute(f"PRAGMA user_version={version}")
                before = {name: conn.execute(f'SELECT * FROM "{name}"').fetchall()
                          for name, in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
                conn.commit()
                conn.close()
                with store.Store(path) as db:
                    self.assertEqual(db.conn.execute("PRAGMA user_version").fetchone()[0], store.SCHEMA_VERSION)
                    for name, rows in before.items():
                        self.assertEqual([tuple(row) for row in db.conn.execute(f'SELECT * FROM "{name}"')], rows)
                    self.assertEqual(db.pending_restarts(), [])

    def test_drift_schema_three_and_unsupported_version_leave_original_untouched(self):
        for version, drift in ((3, True), (99, False)):
            with self.subTest(version=version):
                path = Path(self.tmp.name) / f"bad{version}" / "state.db"
                path.parent.mkdir(mode=0o700)
                conn = sqlite3.connect(path)
                path.chmod(0o600)
                conn.executescript(store._SCHEMA_V2 + store._UPGRADE_V3_SCHEMA)
                conn.execute("INSERT INTO agent VALUES(?,?,NULL,NULL,'active',10)", (AGENT, OWNER))
                if drift:
                    conn.execute("CREATE TABLE alien(body TEXT)")
                conn.execute(f"PRAGMA user_version={version}")
                conn.commit()
                conn.close()
                with self.assertRaises(store.StoreError):
                    store.Store(path)
                with sqlite3.connect(path) as conn:
                    self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], version)
                    self.assertEqual(conn.execute("SELECT pubkey FROM agent").fetchone()[0], AGENT)
                    self.assertIsNone(conn.execute("SELECT name FROM sqlite_master WHERE name='restart_operation'").fetchone())

    def test_sql_constraints_block_second_active_and_partial_pin(self):
        self.reserve()
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.conn.execute("INSERT INTO restart_operation SELECT 'op2',agent_id,scope_hash,protectedfiles_hash,old_pid,old_start,old_invocation,new_pid,new_start,new_invocation,state,created_at,updated_at FROM restart_operation")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.conn.execute("UPDATE restart_operation SET state='unknown',new_pid=42 WHERE operation_id='op1'")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.conn.execute("UPDATE restart_operation SET state='acked' WHERE operation_id='op1'")


if __name__ == "__main__":
    unittest.main()
