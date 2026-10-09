"""Schema-10 Store-only metadata acceptance; no physical admission authority.

The sample process/channel values below validate SQL persistence shape only.
They prove no source approval, native membership, protected-file CAS,
AgentSpec identity, live process, or authority to admit a channel.
"""
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import sys

TESTS = Path(__file__).resolve().parent
sys.path[:0] = [str(TESTS), str(TESTS.parent / 'scripts')]
from hostd import store
import buzz_feishu_group_sync as gs


class OwnHomeJournalStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db_path = self.root / "state" / "hostd.sqlite3"
        self.db = store.Store(self.db_path)
        self.addCleanup(self.db.close)
        self.db.register_agent("1" * 64, owner_pubkey="2" * 64,
            app_id="cli_store_fixture", config_path=str(self.root / "agent.env"), now=1000)

    def require_schema10(self):
        version = self.db.conn.execute("PRAGMA user_version").fetchone()[0]
        self.assertGreaterEqual(version, 10, "schema-10 append-only journal is missing")
        self.assertEqual(version, store.SCHEMA_VERSION)
        names = {row[0] for row in self.db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"own_home_admission", "own_home_admission_channel",
                         "own_home_admission_restart"} <= names)

    def require_api(self):
        self.require_schema10()
        names = ("own_home_admission", "active_own_home_admission",
            "own_home_admission_channels", "reserve_own_home_admission",
            "mark_own_home_admission_unknown", "link_own_home_admission_restart",
            "ack_own_home_admission")
        missing = [name for name in names if not callable(getattr(self.db, name, None))]
        self.assertEqual(missing, [], "schema-10 Store journal API is missing")

    @staticmethod
    def digest(value):
        return hashlib.sha256(value.encode()).hexdigest()

    @staticmethod
    def channel_rows():
        # Bounded persistence fixtures, not verified approvals or grants.
        return (
            {"channel_id": "00000000-0000-0000-0000-000000000001",
             "chat_id": "oc_fixture_one", "chat_ref": gs.chat_ref("oc_fixture_one"),
             "source_kind": "own_approval", "approval_id": "b" * 64,
             "approval_hash": "c" * 64, "mirror_pubkey": "3" * 64,
             "mirror_owner_pubkey": "4" * 64, "claimed_at": 900,
             "claim_event_id": "d" * 64, "policy_event_id": "e" * 64,
             "roster_event_id": "f" * 64, "authorization_hash": "1" * 64},
            {"channel_id": "00000000-0000-0000-0000-000000000002",
             "chat_id": "oc_fixture_two", "chat_ref": gs.chat_ref("oc_fixture_two"),
             "source_kind": "own_approval", "approval_id": "5" * 64,
             "approval_hash": "6" * 64, "mirror_pubkey": "7" * 64,
             "mirror_owner_pubkey": "8" * 64, "claimed_at": 901,
             "claim_event_id": "9" * 64, "policy_event_id": "a" * 64,
             "roster_event_id": "b" * 64, "authorization_hash": "c" * 64},
        )

    def snapshot_args(self, *, channels=None, **overrides):
        rows = self.channel_rows() if channels is None else channels
        channel_ids = sorted(row["channel_id"] for row in rows)
        channels_hash = self.digest(json.dumps(channel_ids, separators=(",", ":")))
        args = dict(admission_id="9" * 64, agent_id="1" * 64,
            snapshot_hash="a" * 64, scope_hash="b" * 64,
            protectedfiles_hash="c" * 64, catalog_hash="d" * 64,
            legacy_join_hash="e" * 64, profile_hash="f" * 64,
            env_before_hash="1" * 64, env_after_hash="2" * 64,
            agent_spec_hash="3" * 64,
            prior_channels_hash=self.digest("[]"),
            proposed_channels_hash=channels_hash,
            approval_set_hash="4" * 64,
            old_process=store.RestartProcess(501, 900001, "1" * 32),
            channels=rows, now=1000)
        args.update(overrides)
        return args

    def reserve(self, **kwargs):
        return self.db.reserve_own_home_admission(**self.snapshot_args(**kwargs))

    def test_fresh_schema_is_version10_with_only_appended_journal_objects(self):
        self.require_schema10()
        self.assertEqual(self.db.conn.execute("PRAGMA foreign_key_check").fetchone(), None)
        self.assertEqual(self.db.conn.execute("PRAGMA quick_check").fetchone()[0], "ok")
        self.assertEqual(self.db.conn.execute("SELECT count(*) FROM own_home_admission").fetchone()[0], 0)
        self.assertEqual(self.db.conn.execute("SELECT count(*) FROM binding").fetchone()[0], 0)
        self.assertEqual(self.db.conn.execute("SELECT count(*) FROM agent_chat").fetchone()[0], 0)
        self.assertEqual(self.db.conn.execute("SELECT count(*) FROM remote_grant").fetchone()[0], 0)

    def test_exact_v9_schema_and_rows_survive_the_append_only_upgrade(self):
        schema_v8 = getattr(store, "_SCHEMA_V8", None)
        upgrade_v9 = getattr(store, "_UPGRADE_V9_SCHEMA", None)
        self.assertIsNotNone(schema_v8, "frozen schema-v8 SQL is required")
        self.assertIsNotNone(upgrade_v9, "frozen schema-v9 append SQL is required")
        if schema_v8 is None or upgrade_v9 is None:
            return
        frozen_v9 = schema_v8 + upgrade_v9
        path = self.root / "migration" / "v9.sqlite3"
        path.parent.mkdir(mode=0o700, parents=True)
        raw = sqlite3.connect(path)
        raw.execute("PRAGMA foreign_keys=ON")
        for statement in frozen_v9.split(";"):
            if statement.strip():
                raw.execute(statement)
        raw.executemany("INSERT INTO app_profile(app_id,config_dir,data_dir) VALUES(?,?,?)", [
            ("cli_fixture_one", "/protected/one", "/protected/data-one"),
            ("cli_fixture_two", "/protected/two", "/protected/data-two"),
        ])
        raw.execute("PRAGMA user_version=9")
        raw.commit(); raw.close(); path.chmod(0o600)

        before = sqlite3.connect(path)
        old_schema = tuple(before.execute("SELECT type,name,tbl_name,sql FROM sqlite_master "
            "WHERE sql IS NOT NULL ORDER BY type,name"))
        old_tables = tuple(row[0] for row in before.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"))
        old_data = {
            table: tuple(before.execute('SELECT * FROM "' + table.replace('"', '""') + '"'))
            for table in old_tables
        }
        before.close()
        migrated = store.Store(path)
        try:
            self.assertGreaterEqual(migrated.conn.execute("PRAGMA user_version").fetchone()[0], 10)
            self.assertEqual(migrated.conn.execute("PRAGMA user_version").fetchone()[0], store.SCHEMA_VERSION)
            after_schema = tuple(tuple(row) for row in migrated.conn.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE sql IS NOT NULL "
                "ORDER BY type,name") if (row[0], row[1]) in {
                    (entry[0], entry[1]) for entry in old_schema})
            after_data = {
                table: tuple(tuple(row) for row in migrated.conn.execute('SELECT * FROM "' + table.replace('"', '""') + '"'))
                for table in old_tables
            }
            self.assertEqual(after_schema, old_schema)
            self.assertEqual(after_data, old_data)
            self.assertEqual(migrated.conn.execute("PRAGMA foreign_key_check").fetchone(), None)
            self.assertEqual(migrated.conn.execute("PRAGMA quick_check").fetchone()[0], "ok")
        finally:
            migrated.close()

    def test_metadata_reservation_is_idempotent_and_complete_channel_rows_are_immutable(self):
        self.require_api()
        first = self.reserve()
        duplicate = self.reserve()
        self.assertTrue(first.created)
        self.assertFalse(duplicate.created)
        self.assertEqual(duplicate.record, first.record)
        actual = self.db.own_home_admission_channels(first.record.admission_id)
        self.assertEqual(tuple(row.channel_id for row in actual), tuple(sorted(
            row["channel_id"] for row in self.channel_rows())))
        with self.assertRaises(store.StoreError):
            self.reserve(channels=self.channel_rows()[:1])
        self.assertEqual(self.db.own_home_admission_channels(first.record.admission_id), actual)
        self.assertIsNone(self.db.conn.execute("SELECT 1 FROM binding LIMIT 1").fetchone())
        self.assertIsNone(self.db.conn.execute("SELECT 1 FROM agent_chat LIMIT 1").fetchone())
        self.assertIsNone(self.db.conn.execute("SELECT 1 FROM remote_grant LIMIT 1").fetchone())

    def test_typed_bounds_duplicates_and_changed_immutable_fields_fail_closed(self):
        self.require_api()
        malformed = [
            {"snapshot_hash": "not-a-hash"},
            {"now": True},
            {"channels": self.channel_rows() + self.channel_rows()},
            {"channels": (dict(self.channel_rows()[0], channel_id="../bad"),)},
            {"old_process": (501, 900001, "1" * 32)},
            {"channels": ()},
            {"channels": (dict(self.channel_rows()[0], claimed_at=True),)},
            {"channels": (dict(self.channel_rows()[0], chat_ref="f" * 64),)},
        ]
        for change in malformed:
            with self.subTest(field=tuple(change)):
                with self.assertRaises(store.StoreError):
                    self.reserve(**change)
        first = self.reserve()
        with self.assertRaises(store.StoreError):
            self.reserve(snapshot_hash="f" * 64)
        self.assertEqual(self.db.own_home_admission(first.record.admission_id), first.record)

    def test_reserved_to_unknown_is_conditional_and_unknown_is_terminal(self):
        self.require_api()
        record = self.reserve().record
        self.assertTrue(self.db.mark_own_home_admission_unknown(record.admission_id,
            snapshot_hash=record.snapshot_hash, now=1001))
        self.assertFalse(self.db.mark_own_home_admission_unknown(record.admission_id,
            snapshot_hash=record.snapshot_hash, now=1002))
        self.assertFalse(self.db.mark_own_home_admission_unknown(record.admission_id,
            snapshot_hash="f" * 64, now=1002))
        self.assertEqual(self.db.own_home_admission(record.admission_id).state, "unknown")

    def test_restart_and_admission_link_commit_or_rollback_as_one_sql_transaction(self):
        self.require_api()
        record = self.reserve().record
        self.assertTrue(self.db.mark_own_home_admission_unknown(record.admission_id,
            snapshot_hash=record.snapshot_hash, now=1001))
        before = self.db.conn.execute("SELECT count(*) FROM restart_operation").fetchone()[0]
        with self.assertRaises(store.StoreError):
            with self.db.transaction():
                restart = self.db.reserve_restart("own-home-test-op", record.agent_id,
                    record.scope_hash, record.protectedfiles_hash, record.old_process, now=1002)
                self.assertTrue(restart.created)
                self.assertTrue(self.db.mark_restart_unknown(restart.record.operation_id,
                    restart.record.agent_id, restart.record.scope_hash,
                    restart.record.protectedfiles_hash, now=1002))
                self.assertFalse(self.db.link_own_home_admission_restart(
                    record.admission_id, restart.record.operation_id,
                    scope_hash="f" * 64, now=1002))
                raise store.StoreError("rollback test transaction")
        self.assertEqual(self.db.conn.execute("SELECT count(*) FROM restart_operation").fetchone()[0], before)
        self.assertIsNone(self.db.own_home_admission_restart(record.admission_id))

        with self.db.transaction():
            restart = self.db.reserve_restart("own-home-test-op", record.agent_id,
                record.scope_hash, record.protectedfiles_hash, record.old_process, now=1003)
            self.assertTrue(restart.created)
            self.assertTrue(self.db.mark_restart_unknown(restart.record.operation_id,
                restart.record.agent_id, restart.record.scope_hash,
                restart.record.protectedfiles_hash, now=1003))
            self.assertTrue(self.db.link_own_home_admission_restart(
                record.admission_id, restart.record.operation_id,
                scope_hash=record.scope_hash, now=1003))
        link = self.db.own_home_admission_restart(record.admission_id)
        self.assertEqual(link.restart_operation_id, restart.record.operation_id)

    def test_ack_requires_same_linked_restart_ack_and_exact_metadata_cas_only(self):
        # This exercises SQL linkage consistency with shape-only process values;
        # it proves no original live invocation or physical success.
        self.require_api()
        record = self.reserve().record
        self.assertTrue(self.db.mark_own_home_admission_unknown(record.admission_id,
            snapshot_hash=record.snapshot_hash, now=1001))
        receipt_hash = "d" * 64
        self.assertFalse(self.db.ack_own_home_admission(record.admission_id,
            snapshot_hash=record.snapshot_hash, restart_operation_id="missing-op",
            receipt_hash=receipt_hash, now=1002))
        with self.db.transaction():
            restart = self.db.reserve_restart("own-home-ack-op", record.agent_id,
                record.scope_hash, record.protectedfiles_hash, record.old_process, now=1003)
            self.assertTrue(restart.created)
            self.assertTrue(self.db.mark_restart_unknown(restart.record.operation_id,
                restart.record.agent_id, restart.record.scope_hash,
                restart.record.protectedfiles_hash, now=1003))
            self.assertTrue(self.db.link_own_home_admission_restart(
                record.admission_id, restart.record.operation_id,
                scope_hash=record.scope_hash, now=1003))
        self.assertFalse(self.db.ack_own_home_admission(record.admission_id,
            snapshot_hash=record.snapshot_hash, restart_operation_id=restart.record.operation_id,
            receipt_hash=receipt_hash, now=1004))
        new_process = store.RestartProcess(502, 900002, "2" * 32)
        self.assertTrue(self.db.pin_restart(restart.record.operation_id, record.agent_id,
            record.scope_hash, record.protectedfiles_hash, new_process, now=1005))
        self.assertTrue(self.db.ack_restart(restart.record.operation_id, record.agent_id,
            record.scope_hash, record.protectedfiles_hash, new_process, now=1006))
        self.assertTrue(self.db.ack_own_home_admission(record.admission_id,
            snapshot_hash=record.snapshot_hash, restart_operation_id=restart.record.operation_id,
            receipt_hash=receipt_hash, now=1007))
        self.assertEqual(self.db.own_home_admission(record.admission_id).state, "acked")
        self.assertTrue(self.db.ack_own_home_admission(record.admission_id,
            snapshot_hash=record.snapshot_hash, restart_operation_id=restart.record.operation_id,
            receipt_hash=receipt_hash, now=1008))
        self.assertFalse(self.db.ack_own_home_admission(record.admission_id,
            snapshot_hash=record.snapshot_hash, restart_operation_id=restart.record.operation_id,
            receipt_hash='f' * 64, now=1009))

    def test_reopen_preserves_unpinned_unknown_as_metadata_only(self):
        self.require_api()
        record = self.reserve().record
        self.assertTrue(self.db.mark_own_home_admission_unknown(record.admission_id,
            snapshot_hash=record.snapshot_hash, now=1001))
        with self.db.transaction():
            restart = self.db.reserve_restart("own-home-reopen-op", record.agent_id,
                record.scope_hash, record.protectedfiles_hash, record.old_process, now=1002)
            self.assertTrue(restart.created)
            self.assertTrue(self.db.mark_restart_unknown(restart.record.operation_id,
                restart.record.agent_id, restart.record.scope_hash,
                restart.record.protectedfiles_hash, now=1002))
            self.assertTrue(self.db.link_own_home_admission_restart(
                record.admission_id, restart.record.operation_id,
                scope_hash=record.scope_hash, now=1002))
        self.db.close()
        reopened = store.Store(self.db_path)
        try:
            current = reopened.own_home_admission(record.admission_id)
            linked = reopened.own_home_admission_restart(record.admission_id)
            operation = reopened.restart_record(linked.restart_operation_id)
            self.assertEqual(current.state, "unknown")
            self.assertEqual(operation.state, "unknown")
            self.assertEqual(operation.old_process, record.old_process)
            self.assertIsNone(operation.new_process)
        finally:
            reopened.close()

    def test_immutable_metadata_and_channel_rows_reject_direct_sql_update_or_delete(self):
        self.require_api()
        record = self.reserve().record
        statements = (
            ("UPDATE own_home_admission SET env_after_hash=? WHERE admission_id=?", ('f' * 64, record.admission_id)),
            ("UPDATE own_home_admission_channel SET approval_hash=? WHERE admission_id=?", ('f' * 64, record.admission_id)),
            ("DELETE FROM own_home_admission_channel WHERE admission_id=?", (record.admission_id,)),
            ("DELETE FROM own_home_admission WHERE admission_id=?", (record.admission_id,)),
        )
        for sql, args in statements:
            with self.subTest(sql=sql):
                with self.assertRaises(store.StoreError):
                    with self.db.transaction():
                        self.db.conn.execute(sql, args)
        self.assertEqual(self.db.own_home_admission(record.admission_id), record)
        self.assertEqual(len(self.db.own_home_admission_channels(record.admission_id)), 2)

    def test_created_at_cannot_change_during_an_otherwise_valid_unknown_transition(self):
        self.require_api()
        record = self.reserve().record
        with self.assertRaises(store.StoreError):
            with self.db.transaction():
                self.db.conn.execute(
                    "UPDATE own_home_admission SET state='unknown',created_at=?,updated_at=? WHERE admission_id=?",
                    (record.created_at - 1, record.updated_at + 1, record.admission_id))
        self.assertEqual(self.db.own_home_admission(record.admission_id), record)

    def test_second_identity_cannot_replace_active_unknown_snapshot(self):
        self.require_api()
        record = self.reserve().record
        self.assertTrue(self.db.mark_own_home_admission_unknown(record.admission_id,
            snapshot_hash=record.snapshot_hash, now=1001))
        with self.assertRaises(store.StoreError):
            self.reserve(admission_id='8' * 64)
        self.assertEqual(self.db.active_own_home_admission(record.agent_id).admission_id,
            record.admission_id)

    def test_prior_unlinked_unknown_restart_cannot_be_adopted_after_original_transaction(self):
        self.require_api()
        record = self.reserve().record
        self.assertTrue(self.db.mark_own_home_admission_unknown(record.admission_id,
            snapshot_hash=record.snapshot_hash, now=1001))
        with self.db.transaction():
            restart = self.db.reserve_restart('preexisting-unlinked-op', record.agent_id,
                record.scope_hash, record.protectedfiles_hash, record.old_process, now=1002)
            self.assertTrue(self.db.mark_restart_unknown(restart.record.operation_id,
                record.agent_id, record.scope_hash, record.protectedfiles_hash, now=1002))
        with self.db.transaction():
            self.assertFalse(self.db.link_own_home_admission_restart(record.admission_id,
                restart.record.operation_id, scope_hash=record.scope_hash, now=1003))
        self.assertIsNone(self.db.own_home_admission_restart(record.admission_id))

    def test_all_prior_versions_preserve_schema_definitions_and_seed_rows(self):
        self.require_schema10()
        versions = [(version, getattr(store, '_SCHEMA_V' + str(version)))
            for version in range(1, 9)]
        versions.append((9, store._SCHEMA_V8 + store._UPGRADE_V9_SCHEMA))
        for version, schema in versions:
            with self.subTest(version=version):
                path = self.root / ('upgrade-' + str(version)) / 'db.sqlite3'
                path.parent.mkdir(mode=0o700)
                before = sqlite3.connect(path)
                for sql in schema.split(';'):
                    if sql.strip(): before.execute(sql)
                before.execute('INSERT INTO app_profile VALUES(?,?,?)',
                    ('cli_v' + str(version), '/original/config', '/original/data'))
                before.execute('PRAGMA user_version=' + str(version)); before.commit()
                objects = tuple(before.execute('SELECT type,name,tbl_name,sql FROM sqlite_master '
                    'WHERE sql IS NOT NULL ORDER BY type,name'))
                profiles = tuple(before.execute('SELECT * FROM app_profile'))
                before.close(); path.chmod(0o600)
                upgraded = store.Store(path)
                try:
                    self.assertEqual(upgraded.conn.execute('PRAGMA user_version').fetchone()[0], store.SCHEMA_VERSION)
                    actual = {(row[0],row[1]):tuple(row) for row in upgraded.conn.execute(
                        'SELECT type,name,tbl_name,sql FROM sqlite_master WHERE sql IS NOT NULL')}
                    for obj in objects: self.assertEqual(actual[(obj[0],obj[1])], obj)
                    self.assertEqual(tuple(tuple(row) for row in upgraded.conn.execute(
                        'SELECT * FROM app_profile')), profiles)
                    self.assertIsNone(upgraded.conn.execute('PRAGMA foreign_key_check').fetchone())
                    self.assertEqual(upgraded.conn.execute('PRAGMA quick_check').fetchone()[0], 'ok')
                finally:
                    upgraded.close()
