"""Durable untrusted candidate admission cannot authorize notice effects."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from hostd import store

class HintQueue(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'hostd.sqlite3'
        self.db = store.Store(self.path)
        self.addCleanup(lambda: self.db.close())
        self.db.reconcile_bindings([store.BindingRecord('a','channel-a','oc_a','cli_a','/cfg','/lark','/data')],now=1)

    def test_reopen_deduplicate_rotate_without_authorizing_notice(self):
        ids = [f'{i:064x}' for i in range(3)]
        self.assertTrue(self.db.enqueue_notice_hints('a',ids,now=2))
        self.db.close(); self.db = store.Store(self.path)
        self.assertTrue(self.db.enqueue_notice_hints('a',ids,now=9))
        self.assertEqual(len(self.db.pending_notice_hints('a',limit=256)),3)
        self.assertEqual(self.db.pending_notice_hints('a')[0]['queued_at'],2)
        self.db.finish_notice_hint('a',ids[0],now=10)
        self.assertEqual(self.db.pending_notice_hints('a')[0]['source_id'],ids[1])
        self.assertEqual(self.db.conn.execute('SELECT count(*) FROM delivery_notice').fetchone()[0],0)

    def test_full_queue_is_atomic_and_duplicates_still_fit(self):
        ids = [f'{i:064x}' for i in range(4096)]
        self.assertTrue(self.db.enqueue_notice_hints('a',ids,now=1))
        self.assertTrue(self.db.enqueue_notice_hints('a',ids[:2],now=2))
        self.assertFalse(self.db.enqueue_notice_hints('a',[f'{4096:064x}', f'{4097:064x}'],now=3))
        self.assertEqual(self.db.conn.execute('SELECT count(*) FROM notice_hint').fetchone()[0],4096)
        with self.assertRaises(store.StoreError):
            self.db.enqueue_notice_hints('a',['raw-message-body'],now=3)

    def test_exact_v12_upgrade_preserves_binding(self):
        self.db.close()
        raw=sqlite3.connect(self.path)
        raw.execute('DROP TABLE join_adoption');raw.execute('DROP TABLE feishu_scan_token');raw.execute('DROP TABLE feishu_scan');raw.execute('DROP TABLE feishu_ingest');raw.execute('DROP TABLE outlet_work_turn');raw.execute('DROP TABLE outlet_work');raw.execute('DROP TABLE outlet_pending'); raw.execute('DROP TABLE console_pause'); raw.execute('DROP TABLE reaction_foreign'); raw.execute('DROP TABLE reaction_inbox')
        raw.execute('DROP TABLE notice_hint');raw.execute('PRAGMA user_version=12');raw.commit();raw.close()
        self.db=store.Store(self.path)
        self.assertEqual(self.db.conn.execute('PRAGMA user_version').fetchone()[0],store.SCHEMA_VERSION)
        self.assertEqual(self.db.bindings()[0]['binding_id'],'a')
        self.assertEqual(self.db.pending_notice_hints('a'),[])

if __name__ == '__main__': unittest.main()
