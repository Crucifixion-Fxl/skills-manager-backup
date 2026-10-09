"""Existing ledger admission must preserve another live SQLite connection's locks."""
import concurrent.futures
import os
from pathlib import Path
import subprocess
import signal
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from hostd import store
LOCK_PROBE = '''import errno,fcntl,os,sys
fd=os.open(sys.argv[1],os.O_RDWR|os.O_NOFOLLOW|os.O_CLOEXEC)
try:
 try: fcntl.lockf(fd,fcntl.LOCK_EX|fcntl.LOCK_NB,510,1073741826,os.SEEK_SET)
 except OSError as e:
  if e.errno not in (errno.EACCES,errno.EAGAIN): raise
  sys.exit(0)
 sys.exit(1)
finally: os.close(fd)
'''
class StoreConstructorLocks(unittest.TestCase):
    def test_existing_constructor_does_not_release_live_database_shared_lock(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'state.db'
            with store.Store(path) as original:
                inode = (path.stat().st_dev, path.stat().st_ino)
                observations = []
                def locked():
                    child = subprocess.Popen([sys.executable, '-S', '-c', LOCK_PROBE, str(path)],
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        close_fds=True, start_new_session=True)
                    descriptor = None
                    try:
                        descriptor = os.pidfd_open(child.pid)
                        result = child.wait(timeout=3)
                    finally:
                        if child.poll() is None:
                            if descriptor is None:
                                child.kill()  # original unreaped direct child cannot recycle its PID
                            else:
                                signal.pidfd_send_signal(descriptor, signal.SIGKILL)
                            child.wait(timeout=3)
                        if descriptor is not None:
                            os.close(descriptor)
                    self.assertIn(result, (0, 1))
                    return result == 0
                self.assertTrue(locked(), 'control: original SQLite shared lock is absent')
                real_close = os.close
                def observe_close(fd):
                    meta = os.fstat(fd)
                    same = (meta.st_dev, meta.st_ino) == inode
                    real_close(fd)
                    if same:
                        observations.append(locked())
                with patch.object(store.os, 'close', observe_close):
                    other = store.Store(path)
                try:
                    self.assertTrue(all(observations),
                        'existing ledger probe released the original SQLite DB shared lock')
                    self.assertTrue(locked())
                finally:
                    other.close()
                self.assertEqual(original.conn.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
    def test_sixteen_concurrent_first_creators_admit_one_private_ledger(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'state.db'
            barrier = threading.Barrier(16)
            def create(_):
                barrier.wait(timeout=5)
                with store.Store(path) as db:
                    return db.conn.execute('PRAGMA user_version').fetchone()[0]
            with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
                self.assertEqual(list(pool.map(create, range(16))), [store.SCHEMA_VERSION]*16)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with store.Store(path) as db:
                self.assertEqual(db.conn.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
    def test_sixteen_concurrent_constructors_preserve_committed_rows(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'state.db'
            with store.Store(path) as original:
                original.reconcile_bindings([store.BindingRecord('alpha','channel_a','oc_a','cli_a',
                    '/cfg/a','/lark/a','/data/a')], now=1)
                barrier = threading.Barrier(16)
                def write(number):
                    barrier.wait(timeout=5)
                    for iteration in range(8):
                        with store.Store(path) as db:
                            delivery = db.reserve_delivery('alpha', f'om_{number}_{iteration}',
                                'f2b', source_at=1, now=1)
                            db.ack_delivery(delivery.id, 'a'*64, now=2)
                with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
                    list(pool.map(write, range(16)))
                self.assertEqual(original.conn.execute('SELECT count(*) FROM delivery').fetchone()[0], 128)
                self.assertEqual(original.conn.execute("SELECT count(*) FROM delivery WHERE status='acked'").fetchone()[0], 128)
                self.assertEqual(original.conn.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
                self.assertEqual(original.conn.execute('PRAGMA foreign_key_check').fetchall(), [])
if __name__ == '__main__':
    unittest.main()
