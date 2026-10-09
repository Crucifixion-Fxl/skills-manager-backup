"""Finite schema-12 Store metadata tests; no native delivery is performed.

The inherited reaction fixture supplies one real Store and real verified grant
for constructing a v11 image-journal row in the migration case. Admission rows
are produced only through the existing Store's metadata API and never asserted
as verified approvals or physical admission.
"""
import concurrent.futures
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

TESTS = Path(__file__).resolve().parent
if not (TESTS / 'test_hostd_remote_reactions.py').is_file():
    worktree = next((parent for parent in TESTS.parents
                     if parent.name == '.worktree'), None)
    candidate = (worktree / 'hostd-codex-implementation' /
                 'skills' / 'buzz-agent-setup') if worktree else None
    if candidate and (candidate / 'tests' / 'test_hostd_remote_reactions.py').is_file():
        TESTS = candidate / 'tests'
SCRIPTS = TESTS.parent / 'scripts'
sys.path[:0] = [str(TESTS), str(SCRIPTS)]

import test_hostd_remote_reactions as reactions_fixture
import test_hostd_own_home_journal as own_home_fixture
from hostd import store

NOTICE_METHODS = (
    'observe_delivery_notice',
    'get_delivery_notice',
    'list_delivery_notices',
    'reserve_delivery_notice_send',
    'resolve_delivery_notice_without_notice',
    'mark_delivery_notice_unknown',
    'record_delivery_notice_readback',
    'reserve_delivery_notice_recovery',
    'record_delivery_notice_recovery_readback',
)
NOTICE_COLUMNS = {
    'binding_id', 'source_id', 'source_author_pubkey', 'source_app_id',
    'source_created_at', 'sync_app_id', 'channel_id', 'chat_id',
    'root_message_id', 'first_observed_at', 'delay_seconds', 'deadline_at',
    'notice_uuid', 'notice_version', 'notice_content_sha256', 'state',
    'resolved_without_notice_at', 'notice_message_id', 'recovery_version',
    'recovery_content_sha256', 'created_at', 'updated_at',
}
NOTICE_HASH = hashlib.sha256(b'fixed versioned notice metadata fixture').hexdigest()
RECOVERY_HASH = hashlib.sha256(b'fixed versioned recovery metadata fixture').hexdigest()


class DeliveryNoticeStoreTests(reactions_fixture.Base):
    """Actual Store/SQLite tests. Store inputs never stand in for native proof."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.db.reconcile_bindings([
            store.BindingRecord('notice-alpha', 'channel_notice', 'oc_notice',
                                'cli_notice', '/protected/config',
                                '/protected/lark-config', '/protected/lark-data')
        ], now=self.w.now)

    def require_notice_contract(self):
        missing = [name for name in NOTICE_METHODS
                   if not callable(getattr(type(self.db), name, None))]
        self.assertFalse(missing,
            'genuine missing Store schema-12 delivery-notice API: ' + ', '.join(missing))
        self.assertEqual(getattr(store, 'SCHEMA_VERSION', None), 19,
                         'genuine missing Store schema-12 version')
        self.assertTrue(callable(getattr(store, 'DeliveryNoticeRecord', None)),
                        'genuine missing immutable schema-12 record type')
        table = self.db.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='delivery_notice'").fetchone()
        self.assertIsNotNone(table, 'genuine missing delivery_notice SQL table')
        columns = {row['name'] for row in self.db.conn.execute(
            'PRAGMA table_info(delivery_notice)')}
        self.assertEqual(columns, NOTICE_COLUMNS,
                         'delivery_notice must contain only the approved bounded metadata')

    @staticmethod
    def notice_args(source_id='om_source_1', **changes):
        notice_suffix = hashlib.sha256(source_id.encode()).hexdigest()[:12]
        fields = {
            'binding_id': 'notice-alpha',
            'source_id': source_id,
            'source_author_pubkey': 'f' * 64,
            'source_app_id': 'cli_foreign_agent',
            'source_created_at': 70,
            'sync_app_id': 'cli_notice',
            'channel_id': 'channel_notice',
            'chat_id': 'oc_notice',
            'root_message_id': 'om_root_notice',
            'first_observed_at': 100,
            'delay_seconds': 60,
            'deadline_at': 160,
            'notice_uuid': ('00000000-0000-4000-8000-000000000001'
                            if source_id == 'om_source_1'
                            else '00000000-0000-4000-8000-' + notice_suffix),
            'notice_version': 1,
            'notice_content_sha256': NOTICE_HASH,
        }
        fields.update(changes)
        return fields

    def observe(self, source_id='om_source_1', **changes):
        fields = self.notice_args(source_id, **changes)
        immutable = {key: fields[key] for key in (
            'source_author_pubkey', 'source_app_id', 'source_created_at',
            'sync_app_id', 'channel_id', 'chat_id', 'root_message_id',
            'first_observed_at', 'delay_seconds', 'deadline_at', 'notice_uuid',
            'notice_version', 'notice_content_sha256')}
        return self.db.observe_delivery_notice(
            fields['binding_id'], fields['source_id'], **immutable)

    async def test_v1_through_v11_migrations_preserve_real_legacy_rows_and_v11_journals(self):
        self.require_notice_contract()
        old_schemas = {
            1: store._SCHEMA_V1,
            2: store._SCHEMA_V2,
            3: store._SCHEMA_V3,
            4: store._SCHEMA_V4,
            5: store._SCHEMA_V5,
            6: store._SCHEMA_V6,
            7: store._SCHEMA_V7,
            8: store._SCHEMA_V8,
            9: store._SCHEMA_V9,
            10: store._SCHEMA_V10,
        }
        for version, schema in old_schemas.items():
            with self.subTest(from_version=version):
                directory = self.w.root / f'legacy-v{version}'
                directory.mkdir(mode=0o700)
                path = directory / 'hostd.sqlite3'
                raw = sqlite3.connect(path, isolation_level=None)
                raw.execute('PRAGMA foreign_keys=ON')
                raw.create_function('hostd_own_home_writer', 0, lambda: 1)
                raw.create_function('hostd_own_home_restart_witness', 2, lambda *_: 0)
                raw.create_function('hostd_remote_image_witness', 4, lambda *_: 0)
                for statement in store._schema_statements(schema):
                    raw.execute(statement)
                raw.execute('INSERT INTO app_profile(app_id,config_dir,data_dir) VALUES(?,?,?)',
                            ('cli_legacy', '/legacy/config', '/legacy/data'))
                raw.execute(f'PRAGMA user_version={version}')
                raw.commit()
                before_objects = tuple(raw.execute(
                    'SELECT type,name,tbl_name,sql FROM sqlite_master '
                    'WHERE sql IS NOT NULL ORDER BY type,name'))
                before_profile = tuple(raw.execute(
                    'SELECT app_id,config_dir,data_dir FROM app_profile'))
                raw.close()
                path.chmod(0o600)

                upgraded = store.Store(path)
                try:
                    self.assertEqual(upgraded.conn.execute('PRAGMA user_version').fetchone()[0], store.SCHEMA_VERSION)
                    after_objects = set(tuple(row) for row in upgraded.conn.execute(
                        'SELECT type,name,tbl_name,sql FROM sqlite_master WHERE sql IS NOT NULL'))
                    self.assertTrue(set(before_objects) <= after_objects)
                    self.assertEqual(tuple(tuple(row) for row in upgraded.conn.execute(
                        'SELECT app_id,config_dir,data_dir FROM app_profile')), before_profile)
                    self.assertEqual(upgraded.conn.execute('PRAGMA foreign_key_check').fetchone(), None)
                    self.assertEqual(upgraded.conn.execute('PRAGMA quick_check').fetchone()[0], 'ok')
                finally:
                    upgraded.close()

        # Build a genuine v11 image row through the real signed proof/grant and
        # Store API. The own-home row is the existing SQL-only metadata fixture;
        # it is never treated as approval, current source proof, or admission.
        grant = await self.grant(('message',))
        event = self.w.event()
        image = self.db.reserve_remote_image_upload(
            grant.target_id, event['id'], 0, grant.revision, grant.scope_hash,
            event['created_at'], hashlib.sha256(b'actual signed-event metadata').hexdigest(),
            'image/png', 37, now=self.w.now)
        self.assertEqual(image.state, 'unknown')
        channels = own_home_fixture.OwnHomeJournalStoreTests.channel_rows()
        channel_ids = sorted(row['channel_id'] for row in channels)
        channel_hash = hashlib.sha256(json.dumps(
            channel_ids, separators=(',', ':')).encode()).hexdigest()
        own_row = self.db.reserve_own_home_admission(
            admission_id='9' * 64, agent_id=reactions_fixture.PUB,
            snapshot_hash='a' * 64, scope_hash='b' * 64,
            protectedfiles_hash='c' * 64, catalog_hash='d' * 64,
            legacy_join_hash='e' * 64, profile_hash='f' * 64,
            env_before_hash='1' * 64, env_after_hash='2' * 64,
            agent_spec_hash='3' * 64,
            prior_channels_hash=hashlib.sha256(b'[]').hexdigest(),
            proposed_channels_hash=channel_hash, approval_set_hash='4' * 64,
            old_process=store.RestartProcess(501, 900001, '1' * 32),
            channels=channels, now=self.w.now)
        self.assertTrue(own_row.created)

        legacy_dir = self.w.root / 'legacy-v11'
        legacy_dir.mkdir(mode=0o700)
        legacy_path = legacy_dir / 'hostd.sqlite3'
        source_path = self.path
        self.db.close()
        source = sqlite3.connect(source_path)
        raw = sqlite3.connect(legacy_path)
        source.backup(raw)
        source.close()
        raw.row_factory = sqlite3.Row
        raw.execute('PRAGMA foreign_keys=ON')
        schema_v11 = store._SCHEMA_V10 + store._UPGRADE_V11_SCHEMA
        raw.execute('PRAGMA user_version=11')
        # The starting database and these business rows were created by the
        # actual schema-12 Store APIs above. Remove only its additive notice
        # table to produce the exact frozen v11 database being upgraded.
        notice_triggers = [row['name'] for row in raw.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='delivery_notice'")]
        for trigger in notice_triggers:
            quoted = '"' + trigger.replace('"', '""') + '"'
            raw.execute('DROP TRIGGER ' + quoted)
        raw.execute('DROP TABLE join_adoption');raw.execute('DROP TABLE feishu_scan_token');raw.execute('DROP TABLE feishu_scan');raw.execute('DROP TABLE feishu_ingest');raw.execute('DROP TABLE outlet_work_turn');raw.execute('DROP TABLE outlet_work');raw.execute('DROP TABLE outlet_pending'); raw.execute('DROP TABLE console_pause'); raw.execute('DROP TABLE reaction_foreign'); raw.execute('DROP TABLE reaction_inbox')
        raw.execute('DROP TABLE notice_hint')
        raw.execute('DROP TABLE delivery_notice')
        raw.execute('PRAGMA user_version=11')
        expected_v11 = store._schema_sql(schema_v11)
        actual_v11 = {" ".join(row[0].rstrip().rstrip(';').split()).lower()
                      for row in raw.execute('SELECT sql FROM sqlite_master WHERE sql IS NOT NULL')}
        self.assertEqual(actual_v11, expected_v11)
        before_objects = tuple(tuple(row) for row in raw.execute(
            'SELECT type,name,tbl_name,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type,name'))
        before_tables = [row['name'] for row in raw.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        before_rows = {table: tuple(tuple(row) for row in raw.execute(f'SELECT * FROM "{table}"'))
                       for table in before_tables}
        self.assertEqual(raw.execute('SELECT count(*) FROM remote_image_upload').fetchone()[0], 1)
        self.assertEqual(raw.execute('SELECT count(*) FROM own_home_admission').fetchone()[0], 1)
        self.assertEqual(raw.execute('SELECT count(*) FROM own_home_admission_channel').fetchone()[0], len(channels))
        raw.close()
        legacy_path.chmod(0o600)
        self.db = store.Store(source_path)
        self.w.db = self.db

        upgraded = store.Store(legacy_path)
        try:
            self.assertEqual(upgraded.conn.execute('PRAGMA user_version').fetchone()[0], store.SCHEMA_VERSION)
            after_objects = set(tuple(row) for row in upgraded.conn.execute(
                'SELECT type,name,tbl_name,sql FROM sqlite_master WHERE sql IS NOT NULL'))
            self.assertTrue(set(before_objects) <= after_objects)
            for table, rows in before_rows.items():
                self.assertEqual(tuple(tuple(row) for row in upgraded.conn.execute(
                    f'SELECT * FROM "{table}"')), rows, table)
            self.assertEqual(upgraded.conn.execute('PRAGMA foreign_key_check').fetchone(), None)
            self.assertEqual(upgraded.conn.execute('PRAGMA quick_check').fetchone()[0], 'ok')
        finally:
            upgraded.close()

    async def test_initial_metadata_replay_pins_source_target_timer_and_notice_intent(self):
        self.require_notice_contract()
        first, created = self.observe()
        duplicate, inserted = self.observe()
        self.assertTrue(created)
        self.assertFalse(inserted)
        self.assertEqual(duplicate, first)
        for change in (
            {'source_author_pubkey': 'e' * 64},
            {'source_app_id': 'cli_other'},
            {'source_created_at': 71},
            {'sync_app_id': 'cli_other'},
            {'channel_id': 'channel_other'},
            {'chat_id': 'oc_other'},
            {'root_message_id': 'om_other'},
            {'first_observed_at': 101, 'deadline_at': 161},
            {'delay_seconds': 61, 'deadline_at': 161},
            {'deadline_at': 161},
            {'notice_uuid': '00000000-0000-4000-8000-000000000002'},
            {'notice_version': 2},
            {'notice_content_sha256': RECOVERY_HASH},
        ):
            with self.subTest(change=tuple(change)):
                with self.assertRaises(store.StoreError):
                    self.observe(**change)
        self.assertEqual(self.db.get_delivery_notice('notice-alpha', 'om_source_1'), first)
        rows = self.db.list_delivery_notices(states=('waiting',), limit=8)
        self.assertEqual(rows, (first,))

    async def test_foreign_source_identity_has_no_local_agent_foreign_key(self):
        self.require_notice_contract()
        row, created = self.observe(source_id='om_foreign_source')
        self.assertTrue(created)
        self.assertIsNone(self.db.conn.execute(
            'SELECT 1 FROM agent WHERE pubkey=?', ('f' * 64,)).fetchone())
        foreign_tables = {item['table'] for item in self.db.conn.execute(
            'PRAGMA foreign_key_list(delivery_notice)')}
        self.assertNotIn('agent', foreign_tables)
        self.assertIn('binding', foreign_tables)
        self.assertEqual(row.source_author_pubkey, 'f' * 64)

    async def test_reservation_is_due_only_and_single_cas_before_native_effect(self):
        self.require_notice_contract()
        self.observe()
        with self.assertRaises(store.StoreError):
            self.db.reserve_delivery_notice_send(
                'notice-alpha', 'om_source_1', expected_state='waiting', now=159)
        self.assertEqual(self.db.get_delivery_notice(
            'notice-alpha', 'om_source_1').state, 'waiting')
        reserved = self.db.reserve_delivery_notice_send(
            'notice-alpha', 'om_source_1', expected_state='waiting', now=160)
        self.assertEqual(reserved.state, 'reserved')
        with self.assertRaises(store.StoreError):
            self.db.reserve_delivery_notice_send(
                'notice-alpha', 'om_source_1', expected_state='waiting', now=161)
        self.assertEqual(self.db.get_delivery_notice(
            'notice-alpha', 'om_source_1').notice_uuid,
            '00000000-0000-4000-8000-000000000001')

    async def test_late_or_early_pre_reservation_delivery_resolves_without_notice(self):
        self.require_notice_contract()
        for source_id, observed_at in (
            ('om_delivered_early', 159),
            ('om_delivered_at_deadline', 160),
            ('om_delivered_late_before_reserve', 161),
        ):
            with self.subTest(observed_at=observed_at):
                self.observe(source_id)
                record = self.db.resolve_delivery_notice_without_notice(
                    'notice-alpha', source_id, expected_state='waiting',
                    observed_delivery_at=observed_at, now=162)
                self.assertEqual(record.state, 'resolved_without_notice')
                self.assertEqual(record.resolved_without_notice_at, observed_at)
                self.assertIsNone(record.notice_message_id)
                self.assertIsNone(record.recovery_version)
                with self.assertRaises(store.StoreError):
                    self.db.reserve_delivery_notice_send(
                        'notice-alpha', source_id, expected_state='waiting', now=200)
        self.observe('om_future_delivery')
        with self.assertRaises(store.StoreError):
            self.db.resolve_delivery_notice_without_notice(
                'notice-alpha', 'om_future_delivery', expected_state='waiting',
                observed_delivery_at=163, now=162)
        self.assertEqual(self.db.get_delivery_notice(
            'notice-alpha', 'om_future_delivery').state, 'waiting')

    async def test_unknown_first_id_metadata_is_immutable_across_reopen(self):
        self.require_notice_contract()
        self.observe()
        self.db.reserve_delivery_notice_send(
            'notice-alpha', 'om_source_1', expected_state='waiting', now=160)
        self.db.mark_delivery_notice_unknown(
            'notice-alpha', 'om_source_1', expected_state='reserved', now=161)
        # This validates only SQL metadata shape/CAS. The arguments do not
        # simulate a native GET or establish marker uniqueness/authority.
        self.db.close()
        self.db = store.Store(self.path)
        self.w.db = self.db
        unknown = self.db.get_delivery_notice('notice-alpha', 'om_source_1')
        self.assertEqual((unknown.state, unknown.notice_uuid, unknown.notice_message_id),
                         ('unknown', '00000000-0000-4000-8000-000000000001', None))
        for bad_readback in (
            {'observed_notice_version': 2, 'observed_content_sha256': NOTICE_HASH},
            {'observed_notice_version': 1, 'observed_content_sha256': RECOVERY_HASH},
        ):
            with self.subTest(bad_readback=tuple(bad_readback)):
                with self.assertRaises(store.StoreError):
                    self.db.record_delivery_notice_readback(
                        'notice-alpha', 'om_source_1', expected_state='unknown',
                        notice_message_id='om_notice_1', now=162, **bad_readback)
                unchanged = self.db.get_delivery_notice('notice-alpha', 'om_source_1')
                self.assertEqual((unchanged.state, unchanged.notice_message_id),
                                 ('unknown', None))
        with self.assertRaises(store.StoreError):
            self.db.reserve_delivery_notice_send(
                'notice-alpha', 'om_source_1', expected_state='waiting', now=162)
        noticed = self.db.record_delivery_notice_readback(
            'notice-alpha', 'om_source_1', expected_state='unknown',
            notice_message_id='om_notice_1', observed_notice_version=1,
            observed_content_sha256=NOTICE_HASH, now=162)
        self.assertEqual((noticed.state, noticed.notice_message_id), ('noticed', 'om_notice_1'))
        with self.assertRaises(store.StoreError):
            self.db.record_delivery_notice_readback(
                'notice-alpha', 'om_source_1', expected_state='noticed',
                notice_message_id='om_replacement', observed_notice_version=1,
                observed_content_sha256=NOTICE_HASH, now=163)
        self.assertEqual(self.db.get_delivery_notice(
            'notice-alpha', 'om_source_1').notice_message_id, 'om_notice_1')

    async def test_recovery_cas_keeps_original_notice_id_and_hash_after_reopen(self):
        self.require_notice_contract()
        self.observe()
        self.db.reserve_delivery_notice_send(
            'notice-alpha', 'om_source_1', expected_state='waiting', now=160)
        self.db.record_delivery_notice_readback(
            'notice-alpha', 'om_source_1', expected_state='reserved',
            notice_message_id='om_notice_1', observed_notice_version=1,
            observed_content_sha256=NOTICE_HASH, now=161)
        for bad_version in (0, -1, True):
            with self.subTest(recovery_version=bad_version):
                with self.assertRaises(store.StoreError):
                    self.db.reserve_delivery_notice_recovery(
                        'notice-alpha', 'om_source_1', expected_state='noticed',
                        notice_message_id='om_notice_1', recovery_version=bad_version,
                        recovery_content_sha256=RECOVERY_HASH, now=169)
                unchanged = self.db.get_delivery_notice('notice-alpha', 'om_source_1')
                self.assertEqual((unchanged.state, unchanged.recovery_version),
                                 ('noticed', None))
        reserved = self.db.reserve_delivery_notice_recovery(
            'notice-alpha', 'om_source_1', expected_state='noticed',
            notice_message_id='om_notice_1', recovery_version=2,
            recovery_content_sha256=RECOVERY_HASH, now=170)
        self.assertEqual(reserved.state, 'recovery_reserved')
        unknown = self.db.mark_delivery_notice_unknown(
            'notice-alpha', 'om_source_1', expected_state='recovery_reserved', now=171)
        self.assertEqual(unknown.state, 'recovery_unknown')
        self.db.close()
        self.db = store.Store(self.path)
        self.w.db = self.db
        for bad_readback in (
            {'notice_message_id': 'om_replacement', 'observed_recovery_version': 2,
             'observed_content_sha256': RECOVERY_HASH},
            {'notice_message_id': 'om_notice_1', 'observed_recovery_version': 3,
             'observed_content_sha256': RECOVERY_HASH},
            {'notice_message_id': 'om_notice_1', 'observed_recovery_version': 2,
             'observed_content_sha256': NOTICE_HASH},
        ):
            with self.subTest(bad_readback=tuple(bad_readback)):
                with self.assertRaises(store.StoreError):
                    self.db.record_delivery_notice_recovery_readback(
                        'notice-alpha', 'om_source_1', expected_state='recovery_unknown',
                        now=172, **bad_readback)
                unchanged = self.db.get_delivery_notice('notice-alpha', 'om_source_1')
                self.assertEqual((unchanged.state, unchanged.notice_message_id,
                                  unchanged.recovery_version, unchanged.recovery_content_sha256),
                                 ('recovery_unknown', 'om_notice_1', 2, RECOVERY_HASH))
        recovered = self.db.record_delivery_notice_recovery_readback(
            'notice-alpha', 'om_source_1', expected_state='recovery_unknown',
            notice_message_id='om_notice_1', observed_recovery_version=2,
            observed_content_sha256=RECOVERY_HASH, now=172)
        self.assertEqual((recovered.state, recovered.notice_message_id,
                          recovered.recovery_content_sha256),
                         ('recovered', 'om_notice_1', RECOVERY_HASH))
        with self.assertRaises(store.StoreError):
            self.db.record_delivery_notice_recovery_readback(
                'notice-alpha', 'om_source_1', expected_state='recovered',
                notice_message_id='om_replacement', observed_recovery_version=2,
                observed_content_sha256=RECOVERY_HASH, now=173)
        self.assertEqual(self.db.conn.execute(
            'SELECT count(*) FROM delivery_notice WHERE binding_id=? AND source_id=?',
            ('notice-alpha', 'om_source_1')).fetchone()[0], 1)

    async def test_bounds_sql_immutability_cross_connection_cas_and_reopen(self):
        self.require_notice_contract()
        malformed = (
            {'source_id': '../path'},
            {'source_id': 'x' * 257},
            {'source_id': 'om_bad\x01id'},
            {'first_observed_at': True},
            {'first_observed_at': -1},
            {'first_observed_at': 2**63 - 60, 'deadline_at': 2**63},
            {'source_created_at': 2**63},
            {'delay_seconds': 0},
            {'delay_seconds': 86401, 'deadline_at': 86501},
            {'delay_seconds': True},
            {'deadline_at': 161},
            {'notice_content_sha256': 'not-a-hash'},
            {'notice_version': True},
            {'notice_version': 0},
            {'notice_version': -1},
            {'notice_uuid': 'not/a/stable/uuid'},
            {'source_author_pubkey': 'not-a-pubkey'},
            {'source_created_at': True},
        )
        for index, change in enumerate(malformed):
            with self.subTest(change=tuple(change)):
                source_id = f'om_invalid_{index}'
                before_count = self.db.conn.execute(
                    'SELECT count(*) FROM delivery_notice').fetchone()[0]
                invalid_key = change.get('source_id', source_id)
                invalid_fields = self.notice_args(
                    invalid_key, **{key: value for key, value in change.items()
                                   if key != 'source_id'})
                invalid_key = invalid_fields['source_id']
                invalid_metadata = {key: value for key, value in invalid_fields.items()
                                    if key not in ('binding_id', 'source_id')}
                with self.assertRaises(store.StoreError):
                    self.db.observe_delivery_notice(
                        invalid_fields['binding_id'], invalid_key, **invalid_metadata)
                self.assertEqual(self.db.conn.execute(
                    'SELECT count(*) FROM delivery_notice').fetchone()[0], before_count)

        self.observe('om_sort_later', first_observed_at=200, deadline_at=260)
        self.observe('om_sort_earlier')
        for states, limit in ((('waiting',), 0), (('waiting',), 257), (('invalid',), 2)):
            with self.subTest(states=states, limit=limit):
                with self.assertRaises(store.StoreError):
                    self.db.list_delivery_notices(states=states, limit=limit)
        ordered = self.db.list_delivery_notices(states=('waiting',), limit=2)
        self.assertEqual(tuple(row.source_id for row in ordered),
                         ('om_sort_earlier', 'om_sort_later'))
        row, _ = self.observe('om_race_source')
        def reserve_on_new_connection(_):
            other = store.Store(self.path)
            try:
                return ('ok', other.reserve_delivery_notice_send(
                    'notice-alpha', 'om_race_source', expected_state='waiting', now=160).state)
            except store.StoreError:
                return ('stale', None)
            finally:
                other.close()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(reserve_on_new_connection, range(2)))
        self.assertEqual(sum(result[0] == 'ok' for result in results), 1)
        self.assertEqual(sum(result[0] == 'stale' for result in results), 1)
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.conn.execute(
                "UPDATE delivery_notice SET chat_id='oc_tampered' WHERE binding_id=? AND source_id=?",
                ('notice-alpha', 'om_race_source'))
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.conn.execute(
                'DELETE FROM delivery_notice WHERE binding_id=? AND source_id=?',
                ('notice-alpha', 'om_race_source'))
        before = self.db.list_delivery_notices(states=('reserved',), limit=8)
        self.db.close()
        self.db = store.Store(self.path)
        self.w.db = self.db
        self.assertEqual(self.db.list_delivery_notices(states=('reserved',), limit=8), before)
        closed = self.db
        closed.close()
        with self.assertRaises(store.StoreError):
            closed.get_delivery_notice('notice-alpha', 'om_race_source')
        self.db = store.Store(self.path)
        self.w.db = self.db
