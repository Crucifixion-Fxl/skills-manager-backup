"""One finite actual Store descriptor/WAL locking regression, Linux stdlib only."""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import store_wal_lock_fixture as low

TESTS = Path(__file__).resolve().parent
SCRIPTS = TESTS.parent/'scripts'
HELPER = TESTS/'store_wal_lock_fixture.py'


class StoreWalMetadataLockTests(unittest.TestCase):
    def test_metadata_check_preserves_live_sqlite_writer_lock_and_readonly_observer(self):
        protected = {path:hashlib.sha256(path.read_bytes()).hexdigest()
                     for path in (HELPER,SCRIPTS/'hostd'/'store.py',SCRIPTS/'hostd'/'__init__.py')}
        with tempfile.TemporaryDirectory(prefix='hostd-store-wal-lock-') as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            database = root/'state.db'
            sibling = subprocess.Popen([sys.executable,'-S','-c','import time;time.sleep(60)'],
                stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                close_fds=True,start_new_session=True,umask=0o077,
                env={'PATH':os.defpath,'LANG':'C.UTF-8','PYTHONDONTWRITEBYTECODE':'1'})
            sibling_identity = low.identity(sibling.pid)
            writer = None
            try:
                self.assertIsNotNone(sibling_identity)
                self.assertEqual(sibling_identity[2],sibling.pid)
                self.assertEqual(sibling_identity[1],os.getpid())
                self.assertEqual(sibling_identity[4],os.geteuid())
                writer = low.OwnedWriter(HELPER,SCRIPTS,database)
                ready = writer.receive('READY')
                self.assertTrue(ready['transaction'])
                self.assertEqual(ready['journal'],'wal')
                self.assertGreater(ready['tables'],0)
                shm = Path(str(database)+'-shm')
                before = shm.stat()
                self.assertGreaterEqual(before.st_size,32768)
                self.assertTrue(low.writer_locked(shm),'control: actual SQLite WAL writer lock was not held')
                writer.send('CHECK')
                self.assertTrue(writer.receive('CHECKED')['transaction'])
                self.assertTrue(low.writer_locked(shm),
                    'Store metadata check released the original live SQLite WAL writer lock')
                # Fail first on missing lock; unsafe old writer is never exposed to reader truncation.
                self.assertEqual(low.readonly_count(database),ready['tables'])
                after = shm.stat()
                self.assertEqual((after.st_dev,after.st_ino),(before.st_dev,before.st_ino))
                self.assertGreaterEqual(after.st_size,before.st_size)
                self.assertTrue(low.writer_locked(shm))
                self.assertIsNone(writer.process.poll())
                self.assertEqual(low.identity(writer.process.pid),writer.original)
                writer.send('FINISH')
                self.assertFalse(writer.receive('COMMITTED')['transaction'])
                writer.receive('CLOSED')
                writer.send('EXIT')
            finally:
                try:
                    if writer is not None:
                        self.assertEqual(writer.close(),0)
                    self.assertIsNone(sibling.poll())
                    self.assertEqual(low.identity(sibling.pid),sibling_identity)
                    for path,expected in protected.items():
                        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),expected)
                finally:
                    if sibling.poll() is None:
                        descriptor = os.pidfd_open(sibling.pid)
                        try:
                            self.assertEqual(low.identity(sibling.pid),sibling_identity)
                            import signal
                            signal.pidfd_send_signal(descriptor,signal.SIGTERM)
                        finally:
                            os.close(descriptor)
                    sibling.wait(timeout=3)
                    self.assertNotEqual(low.identity(sibling.pid),sibling_identity)
