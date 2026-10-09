"""Private Store-v11 tests-first contract for the own-image SQL journal.

Only SQLite state is exercised. The real Store, signed proof fixture, and
remote-grant evidence remain authoritative; no image transport is invoked.
"""
import asyncio
import hashlib
import inspect
import json
import os
from pathlib import Path
import sqlite3
import sys
import unittest

TESTS = Path(__file__).resolve().parent
if not (TESTS / 'test_hostd_remote_reactions.py').is_file():
    # The private draft lives under .briefs; bind it to the pinned development
    # checkout's real fixture. Once copied beside the normal tests, this branch
    # is unnecessary and the adjacent test/script layout is used directly.
    worktree = next((parent for parent in TESTS.parents
                     if parent.name == '.worktree'), None)
    candidate = (worktree / 'hostd-codex-implementation' /
                 'skills' / 'buzz-agent-setup') if worktree else None
    if candidate and (candidate / 'tests' / 'test_hostd_remote_reactions.py').is_file():
        TESTS = candidate / 'tests'
SCRIPTS = TESTS.parent / 'scripts'
sys.path[:0] = [str(TESTS), str(SCRIPTS)]
import test_hostd_remote_reactions as reactions_fixture
from hostd import store


JOURNAL_METHODS = (
    'remote_image_upload_by_source',
    'reserve_remote_image_upload',
    'pin_remote_image_key',
    'ack_remote_image_upload',
)
IMAGE_COLUMNS = {
    'target_id', 'source_id', 'ordinal', 'revision', 'scope_hash', 'source_at',
    'content_hash', 'mime_type', 'byte_size', 'state', 'image_key',
    'created_at', 'updated_at',
}
IMAGE_DIGEST = hashlib.sha256(b'journal-metadata-only-fixture').hexdigest()


class RemoteImageJournalTests(reactions_fixture.Base):
    """Use Base's actual signed proof, active grant, and protected Store setup."""

    def require_store11_contract(self):
        missing = [name for name in JOURNAL_METHODS
                   if not callable(getattr(type(self.db), name, None))]
        self.assertFalse(missing,
            'genuine missing Store v11 image-journal feature: ' + ', '.join(missing))
        self.assertGreaterEqual(getattr(store, 'SCHEMA_VERSION', None), 11,
            'genuine missing Store v11 image-journal schema version')
        table = self.db.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='remote_image_upload'").fetchone()
        self.assertIsNotNone(table,
            'genuine missing Store v11 remote_image_upload table')
        columns = {row['name'] for row in self.db.conn.execute(
            'PRAGMA table_info(remote_image_upload)')}
        self.assertEqual(columns, IMAGE_COLUMNS,
            'image journal must contain only bounded digest/key metadata')
        self.assertTrue(callable(getattr(store, 'RemoteImageUploadRecord', None)),
            'genuine missing immutable Store v11 image-journal record type')
        self.assertTrue(callable(getattr(store, '_schema_statements', None)),
            'frozen Store SQL statement parser missing from migration fixture')
        self.assertTrue(getattr(store, '_SCHEMA_V9', None),
            'frozen Store v9 SQL missing from v10 migration fixture')
        self.assertTrue(getattr(store, '_UPGRADE_V10_SCHEMA', None),
            'frozen v10 upgrade SQL missing from migration fixture')

    def claim(self, event, ordinal=0, **changes):
        fields = {
            'target_id': self.w.target_id,
            'source_id': event['id'],
            'ordinal': ordinal,
            'revision': self.db.remote_grant(self.w.target_id).revision,
            'scope_hash': self.db.remote_grant(self.w.target_id).scope_hash,
            'source_at': event['created_at'],
            'content_hash': IMAGE_DIGEST,
            'mime_type': 'image/png',
            'byte_size': 37,
        }
        fields.update(changes)
        return fields

    def reserve(self, fields, *, now=None):
        return self.db.reserve_remote_image_upload(
            fields['target_id'], fields['source_id'], fields['ordinal'],
            fields['revision'], fields['scope_hash'], fields['source_at'],
            fields['content_hash'], fields['mime_type'], fields['byte_size'],
            now=self.w.now if now is None else now)

    def pin(self, fields, image_key, *, revision=None, scope_hash=None, now=None):
        return self.db.pin_remote_image_key(
            fields['target_id'], fields['source_id'], fields['ordinal'],
            fields['revision'] if revision is None else revision,
            fields['scope_hash'] if scope_hash is None else scope_hash,
            image_key, now=self.w.now if now is None else now)

    def ack(self, fields, image_key, *, revision=None, scope_hash=None,
            content_hash=None, mime_type=None, byte_size=None, now=None):
        return self.db.ack_remote_image_upload(
            fields['target_id'], fields['source_id'], fields['ordinal'],
            fields['revision'] if revision is None else revision,
            fields['scope_hash'] if scope_hash is None else scope_hash,
            image_key,
            fields['content_hash'] if content_hash is None else content_hash,
            fields['mime_type'] if mime_type is None else mime_type,
            fields['byte_size'] if byte_size is None else byte_size,
            now=self.w.now if now is None else now)

    async def test_missing_v11_contract_is_an_explicit_feature_red(self):
        self.require_store11_contract()

    async def test_v10_upgrade_preserves_existing_sql_definitions_and_real_rows(self):
        self.require_store11_contract()
        grant = await self.grant(('message',))
        old_path = self.w.root / 'metadata' / 'image-journal-v10.db'
        schema_v10 = store._SCHEMA_V9 + store._UPGRADE_V10_SCHEMA
        raw = sqlite3.connect(old_path, isolation_level=None)
        raw.execute('PRAGMA foreign_keys=ON')
        raw.create_function('hostd_own_home_writer', 0, lambda: 1)
        raw.create_function('hostd_own_home_restart_witness', 2, lambda *_: 0)
        for statement in store._schema_statements(schema_v10):
            raw.execute(statement)
        raw.execute('BEGIN')
        copied_tables = ('agent', 'remote_target', 'remote_grant')
        for table_name in copied_tables:
            columns = [row['name'] for row in self.db.conn.execute(
                f'PRAGMA table_info({table_name})')]
            rows = self.db.conn.execute(f'SELECT * FROM {table_name}').fetchall()
            placeholders = ','.join('?' for _ in columns)
            names = ','.join(columns)
            raw.executemany(f'INSERT INTO {table_name} ({names}) VALUES ({placeholders})',
                            [tuple(row) for row in rows])
        raw.execute('PRAGMA user_version=10')
        raw.commit()
        self.assertEqual(raw.execute('PRAGMA user_version').fetchone()[0], 10)
        object_sql_before = tuple(raw.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master "
            "WHERE sql IS NOT NULL ORDER BY type,name"))
        rows_before = {name: tuple(tuple(row) for row in raw.execute(
            f'SELECT * FROM {name} ORDER BY rowid')) for name in copied_tables}
        raw.close()
        os.chmod(old_path, 0o600)

        upgraded = store.Store(old_path)
        try:
            self.assertEqual(upgraded.conn.execute('PRAGMA user_version').fetchone()[0], store.SCHEMA_VERSION)
            object_sql_after = set(tuple(row) for row in upgraded.conn.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master "
                "WHERE sql IS NOT NULL"))
            self.assertTrue(set(object_sql_before) <= object_sql_after,
                'schema v11 must add its journal without rewriting existing SQL definitions')
            rows_after = {name: tuple(tuple(row) for row in upgraded.conn.execute(
                f'SELECT * FROM {name} ORDER BY rowid')) for name in copied_tables}
            self.assertEqual(rows_after, rows_before)
            self.assertEqual(upgraded.remote_grant(grant.target_id).scope_hash,
                             grant.scope_hash)
        finally:
            upgraded.close()

    async def test_reserve_is_digest_only_keyless_unknown_and_exact_retry_is_immutable(self):
        self.require_store11_contract()
        grant = await self.grant(('message',))
        event = self.w.event()
        fields = self.claim(event)
        row = self.reserve(fields)
        self.assertIsNotNone(row)
        self.assertEqual((row.state, row.image_key), ('unknown', ''))
        self.assertEqual((row.target_id, row.source_id, row.ordinal, row.revision,
                          row.scope_hash, row.source_at, row.content_hash, row.mime_type,
                          row.byte_size, row.created_at, row.updated_at),
                         (fields['target_id'], fields['source_id'], fields['ordinal'],
                          grant.revision, grant.scope_hash, event['created_at'],
                          IMAGE_DIGEST, 'image/png', 37, self.w.now, self.w.now))
        self.assertEqual(grant.evidence.capabilities, ('message',))
        retry = self.reserve(fields, now=self.w.now + 5)
        self.assertEqual(retry, row, 'exact retry returns the original row without timestamp change')
        self.assertEqual(self.db.remote_image_upload_by_source(
            fields['target_id'], fields['source_id'], fields['ordinal']), row)
        for changed in (
            {'source_at': fields['source_at'] + 1},
            {'content_hash': hashlib.sha256(b'other').hexdigest()},
            {'mime_type': 'image/jpeg'},
            {'byte_size': fields['byte_size'] + 1},
            {'scope_hash': '0' * 64},
            {'revision': grant.revision + 1},
        ):
            self.assertIsNone(self.reserve({**fields, **changed}, now=self.w.now + 6))
            self.assertEqual(self.db.remote_image_upload_by_source(
                fields['target_id'], fields['source_id'], fields['ordinal']), row)
        for ordinal in (1, 2, 3):
            extra = self.reserve(self.claim(event, ordinal=ordinal))
            self.assertEqual(extra.ordinal, ordinal)
        count = self.db.conn.execute(
            'SELECT count(*) FROM remote_image_upload WHERE target_id=? AND source_id=?',
            (fields['target_id'], fields['source_id'])).fetchone()[0]
        self.assertEqual(count, 4, 'ordinal bounds permit at most four rows per source')
        with self.assertRaises(store.StoreError):
            self.reserve(self.claim(event, ordinal=4))
        columns = {r['name'] for r in self.db.conn.execute(
            'PRAGMA table_info(remote_image_upload)')}
        self.assertFalse({'url', 'source_url', 'body', 'raw_bytes', 'image_bytes'} & columns)

    async def test_pin_and_ack_are_one_way_exact_and_use_monotonic_timestamps(self):
        self.require_store11_contract()
        grant = await self.grant(('message',))
        event = self.w.event()
        fields = self.claim(event)
        row = self.reserve(fields)
        self.assertEqual((row.state, row.image_key), ('unknown', ''))
        key = 'img_sql_fixture_01'
        self.assertTrue(self.pin(fields, key, now=self.w.now + 1))
        pinned = self.db.remote_image_upload_by_source(
            fields['target_id'], fields['source_id'], fields['ordinal'])
        self.assertEqual((pinned.state, pinned.image_key), ('unknown', key))
        self.assertGreaterEqual(pinned.updated_at, row.updated_at)
        self.assertFalse(self.pin(fields, 'img_sql_replacement_02', now=self.w.now + 2))
        self.assertTrue(self.pin(fields, key, now=self.w.now + 20))
        same_pin = self.db.remote_image_upload_by_source(
            fields['target_id'], fields['source_id'], fields['ordinal'])
        self.assertEqual(same_pin.updated_at, pinned.updated_at)
        self.assertFalse(self.ack(fields, 'img_sql_wrong_03', now=self.w.now + 21))
        self.assertFalse(self.ack(fields, key, content_hash='0' * 64,
                                  now=self.w.now + 21))
        self.assertFalse(self.ack(fields, key, mime_type='image/jpeg',
                                  now=self.w.now + 21))
        self.assertFalse(self.ack(fields, key, byte_size=38, now=self.w.now + 21))
        self.assertTrue(self.ack(fields, key, now=self.w.now + 2))
        acked = self.db.remote_image_upload_by_source(
            fields['target_id'], fields['source_id'], fields['ordinal'])
        self.assertEqual((acked.state, acked.image_key), ('acked', key))
        self.assertGreaterEqual(acked.updated_at, pinned.updated_at)
        self.assertTrue(self.ack(fields, key, now=self.w.now + 25))
        exact_ack = self.db.remote_image_upload_by_source(
            fields['target_id'], fields['source_id'], fields['ordinal'])
        self.assertEqual(exact_ack, acked,
            'exact ACK retry must not rewrite immutable values or timestamps')
        self.assertFalse(self.pin(fields, 'img_sql_replacement_02', now=self.w.now + 31))
        self.assertEqual(grant.evidence.capabilities, ('message',))

    async def test_current_revision_and_scope_are_checked_on_reserve_pin_and_ack(self):
        self.require_store11_contract()
        grant = await self.grant(('message',))
        event = self.w.event()
        fields = self.claim(event)
        self.assertIsNone(self.reserve({**fields, 'revision': grant.revision + 1}))
        self.assertIsNone(self.reserve({**fields, 'scope_hash': '0' * 64}))
        self.assertTrue(self.reserve(fields))
        key = 'img_sql_current_04'
        self.assertFalse(self.pin(fields, key, revision=grant.revision + 1))
        self.assertFalse(self.pin(fields, key, scope_hash='0' * 64))
        self.assertEqual(self.db.remote_image_upload_by_source(
            fields['target_id'], fields['source_id'], fields['ordinal']).image_key, '')
        self.assertTrue(self.pin(fields, key))
        self.assertFalse(self.ack(fields, key, revision=grant.revision + 1))
        self.assertFalse(self.ack(fields, key, scope_hash='0' * 64))
        self.assertEqual(self.db.remote_image_upload_by_source(
            fields['target_id'], fields['source_id'], fields['ordinal']).state, 'unknown')

    async def test_superseded_real_grant_cannot_adopt_pin_or_ack_old_rows(self):
        self.require_store11_contract()
        original = await self.grant(('message',))
        first_event = self.w.event(tags=[['t', 'journal-first']])
        second_event = self.w.event(tags=[['t', 'journal-second']])
        unpinned = self.claim(first_event, ordinal=0)
        pinned_fields = self.claim(second_event, ordinal=0)
        self.assertTrue(self.reserve(unpinned))
        self.assertTrue(self.reserve(pinned_fields))
        key = 'img_sql_superseded_05'
        self.assertTrue(self.pin(pinned_fields, key))

        self.w.now += 1
        verification = await self.request(('message', 'reaction_add'))
        self.assertEqual(verification.status, 'verified')
        newer = self.db.activate_remote_grant(verification.authorization.evidence,
            expected_revision=original.revision, now=self.w.now)
        self.assertNotEqual((newer.revision, newer.scope_hash),
                            (original.revision, original.scope_hash))
        self.assertTrue(self.db.refresh_remote_proof(newer.target_id,
            verification.authorization.evidence, revision=newer.revision,
            scope_hash=newer.scope_hash, now=self.w.now))

        self.assertIsNone(self.reserve(unpinned))
        self.assertFalse(self.pin(unpinned, 'img_sql_late_06'))
        self.assertFalse(self.ack(pinned_fields, key))
        self.assertEqual(self.db.remote_image_upload_by_source(
            unpinned['target_id'], unpinned['source_id'], 0).image_key, '')
        self.assertEqual(self.db.remote_image_upload_by_source(
            pinned_fields['target_id'], pinned_fields['source_id'], 0).state, 'unknown')

    async def test_exact_python_bounds_reject_bools_and_out_of_range_values(self):
        self.require_store11_contract()
        await self.grant(('message',))
        fields = self.claim(self.w.event())
        invalid = (
            {'target_id': True}, {'source_id': 'x'},
            {'ordinal': True}, {'ordinal': -1}, {'ordinal': 4},
            {'revision': True}, {'revision': 0},
            {'source_at': True}, {'source_at': -1}, {'source_at': 2 ** 63},
            {'content_hash': 'A' * 64}, {'scope_hash': 'A' * 64},
            {'mime_type': 'image/gif'}, {'byte_size': True},
            {'byte_size': 0}, {'byte_size': 10_000_001},
        )
        for change in invalid:
            with self.subTest(field=next(iter(change))):
                with self.assertRaises(store.StoreError):
                    self.reserve({**fields, **change})
        with self.assertRaises(store.StoreError):
            self.reserve(fields, now=True)
        with self.assertRaises(store.StoreError):
            self.pin(fields, 'bad\nkey')
        with self.assertRaises(store.StoreError):
            self.pin(fields, 'x' * 257)
        with self.assertRaises(store.StoreError):
            self.pin(fields, True)
        with self.assertRaises(store.StoreError):
            self.pin(fields, 'valid_sql_key', revision=True)
        with self.assertRaises(store.StoreError):
            self.pin(fields, 'valid_sql_key', scope_hash='A' * 64)
        with self.assertRaises(store.StoreError):
            self.pin(fields, 'valid_sql_key', now=True)
        with self.assertRaises(store.StoreError):
            self.pin(fields, 'k' * 257)
        with self.assertRaises(store.StoreError):
            self.ack(fields, True)
        with self.assertRaises(store.StoreError):
            self.ack(fields, 'valid_sql_key', revision=True)
        with self.assertRaises(store.StoreError):
            self.ack(fields, 'valid_sql_key', byte_size=True)
        with self.assertRaises(store.StoreError):
            self.ack(fields, 'valid_sql_key', now=True)

    async def test_sql_checks_unique_foreign_key_and_immutable_transition_triggers(self):
        self.require_store11_contract()
        grant = await self.grant(('message',))
        event = self.w.event()
        fields = self.claim(event)
        row = self.reserve(fields)
        self.assertIsNotNone(row)
        columns = ('target_id', 'source_id', 'ordinal', 'revision', 'scope_hash',
                   'source_at', 'content_hash', 'mime_type', 'byte_size', 'state',
                   'image_key', 'created_at', 'updated_at')
        statement = 'INSERT INTO remote_image_upload (' + ','.join(columns) + ') VALUES (' + ','.join('?' for _ in columns) + ')'
        base = [fields['target_id'], fields['source_id'], 0, grant.revision,
                grant.scope_hash, event['created_at'], IMAGE_DIGEST, 'image/png',
                37, 'unknown', '', self.w.now, self.w.now]
        foreign_keys = [dict(row) for row in self.db.conn.execute(
            'PRAGMA foreign_key_list(remote_image_upload)')]
        self.assertEqual({(row['table'], row['from'], row['to']) for row in foreign_keys},
            {('remote_grant', 'target_id', 'target_id'),
             ('remote_grant', 'revision', 'revision')})

        def rejected_sql(values):
            with self.assertRaises(store.StoreError):
                with self.db.transaction():
                    self.db.conn.execute(statement, values)

        rejected_sql(base)  # duplicate primary/ordinal key
        for index, changes in enumerate((
            {'ordinal': 4}, {'source_id': 'Z' * 64}, {'scope_hash': 'x' * 64},
            {'content_hash': 'x' * 64}, {'mime_type': 'image/gif'},
            {'byte_size': 0}, {'byte_size': 10_000_001},
            {'revision': grant.revision + 1000}, {'state': 'reserved'},
            {'state': 'acked'}, {'image_key': 'contains\ncontrol'},
            {'updated_at': self.w.now - 1},
        )):
            candidate = list(base)
            for field, value in changes.items():
                candidate[columns.index(field)] = value
            if 'source_id' not in changes:
                candidate[columns.index('source_id')] = f'{index + 1:064x}'
            rejected_sql(candidate)

        key = 'img_sql_trigger_07'
        self.assertTrue(self.pin(fields, key, now=self.w.now + 5))
        protected_updates = (
            ('content_hash=?', ('0' * 64,)),
            ('image_key=?', ('img_sql_forged_08',)),
            ('source_id=?', ('f' * 64,)),
            ('created_at=?', (self.w.now + 1,)),
            ('revision=?', (grant.revision + 1,)),
            ('scope_hash=?', ('0' * 64,)),
            ('source_at=?', (event['created_at'] + 1,)),
            ('byte_size=?', (38,)),
            ('updated_at=?', (self.w.now + 4,)),
        )
        for assignment, values in protected_updates:
            with self.subTest(assignment=assignment):
                with self.assertRaises(store.StoreError):
                    with self.db.transaction():
                        self.db.conn.execute(
                            f'UPDATE remote_image_upload SET {assignment} WHERE target_id=? AND source_id=? AND ordinal=0',
                            (*values, fields['target_id'], fields['source_id']))
        with self.assertRaises(store.StoreError):
            with self.db.transaction():
                self.db.conn.execute(
                    'DELETE FROM remote_image_upload WHERE target_id=? AND source_id=? AND ordinal=0',
                    (fields['target_id'], fields['source_id']))


if __name__ == '__main__':
    unittest.main()
