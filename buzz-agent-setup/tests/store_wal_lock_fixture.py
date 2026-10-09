"""Real stdlib/Linux owned Store writer and read-only lock/SQL observer."""
from __future__ import annotations
from contextlib import closing
import errno
import fcntl
import json
import os
from pathlib import Path
import select
import signal
import sqlite3
import subprocess
import sys
import time


def identity(pid):
    try:
        path = Path('/proc')/str(pid)
        text = (path/'stat').read_text()
        fields = text[text.rindex(')')+2:].split()
        return (pid, int(fields[1]), int(fields[3]), int(fields[19]), path.stat().st_uid)
    except (FileNotFoundError, ProcessLookupError):
        return None


def members(session):
    rows = set()
    for entry in Path('/proc').iterdir():
        if entry.name.isdigit():
            row = identity(int(entry.name))
            if row is not None and row[2] == session:
                rows.add(row)
    return rows


def writer_locked(shm):
    """Actual POSIX SHM-byte120 shared-lock conflict against SQLite WAL writer."""
    descriptor = os.open(shm, os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        try:
            fcntl.lockf(descriptor, fcntl.LOCK_SH|fcntl.LOCK_NB, 1, 120, os.SEEK_SET)
        except OSError as error:
            if error.errno in (errno.EACCES, errno.EAGAIN):
                return True
            raise
        fcntl.lockf(descriptor, fcntl.LOCK_UN, 1, 120, os.SEEK_SET)
        return False
    finally:
        os.close(descriptor)  # Parent owns NO SQLite connection in lock phases.


def readonly_count(database):
    with closing(sqlite3.connect(database.as_uri()+'?mode=ro', uri=True, timeout=.2)) as db:
        db.execute('PRAGMA query_only=ON')
        return db.execute('SELECT count(*) FROM sqlite_master WHERE type="table"').fetchone()[0]


class OwnedWriter:
    def __init__(self, helper, scripts, database):
        self.process = subprocess.Popen([sys.executable, '-S', '-u', str(helper),
            str(scripts), str(database)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, bufsize=0, close_fds=True, start_new_session=True,
            umask=0o077, env={'PATH':os.defpath,'LANG':'C.UTF-8','PYTHONDONTWRITEBYTECODE':'1'})
        self.original = identity(self.process.pid)
        self.buffer = b''
        self.exiting = False
        try:
            assert self.original is not None and self.original[2] == self.process.pid
            assert self.original[1] == os.getpid()
            assert self.original[4] == os.geteuid()
        except BaseException:
            self.close()
            raise

    def send(self, command):
        assert identity(self.process.pid) == self.original
        self.process.stdin.write((command+'\n').encode())
        self.process.stdin.flush()
        if command == 'EXIT': self.exiting = True

    def receive(self, stage):
        deadline = time.monotonic()+5
        while b'\n' not in self.buffer:
            assert select.select([self.process.stdout], [], [], max(0,deadline-time.monotonic()))[0], 'owned writer IPC timeout'
            raw = os.read(self.process.stdout.fileno(),4096)
            assert raw, 'owned writer exited before requested stage'
            self.buffer += raw
            assert len(self.buffer) <= 8192
        raw,self.buffer = self.buffer.split(b'\n',1)
        data = json.loads(raw)
        assert data['stage'] == stage
        assert data['identity'] == list(self.original)
        assert identity(self.process.pid) == self.original
        assert self.process.poll() is None
        return data

    def close(self):
        if not self.exiting and self.original is not None and self.process.poll() is None and identity(self.process.pid) == self.original:
            try:
                self.send('ABORT')
            except BrokenPipeError:
                pass
        try:
            result = self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            descriptor = os.pidfd_open(self.process.pid)
            try:
                assert identity(self.process.pid) == self.original
                signal.pidfd_send_signal(descriptor, signal.SIGKILL)
            finally:
                os.close(descriptor)
            result = self.process.wait(timeout=3)
        finally:
            self.process.stdin.close()
            self.process.stdout.close()
        if self.original is not None:
            assert identity(self.process.pid) != self.original
            assert members(self.original[2]) == set()
        return result


def writer(scripts, database):
    import faulthandler
    faulthandler.enable()
    sys.path.insert(0, str(scripts))
    from hostd import store as module
    assert Path(module.__file__).resolve() == (scripts/'hostd'/'store.py').resolve()
    class Abort(Exception):
        pass
    def emit(stage, **data):
        print(json.dumps(dict(stage=stage, identity=identity(os.getpid()), **data)), flush=True)
    def command(expected):
        assert select.select([sys.stdin], [], [], 5)[0], 'owned writer command timeout'
        received = sys.stdin.readline().strip()
        if received == 'ABORT' or not received:
            raise Abort()
        assert received == expected
    try:
        with module.Store(database) as store:
            with store.transaction():  # Real BEGIN IMMEDIATE obtains WAL_WRITE_LOCK.
                count = store.conn.execute('SELECT count(*) FROM sqlite_master WHERE type="table"').fetchone()[0]
                emit('READY', transaction=store.conn.in_transaction,
                     journal=store.conn.execute('PRAGMA journal_mode').fetchone()[0], tables=count,
                     sqlite_version=sqlite3.sqlite_version)
                command('CHECK')
                store._check_files()  # Actual protected metadata check, never patched.
                emit('CHECKED', transaction=store.conn.in_transaction)
                command('FINISH')
                assert store.conn.execute('SELECT count(*) FROM sqlite_master WHERE type="table"').fetchone()[0] == count
            emit('COMMITTED', transaction=store.conn.in_transaction)
        emit('CLOSED')  # Real Store.close completed; do not invent a reaping receipt.
        command('EXIT')
    except Abort:
        pass  # Original transaction rollback and Store.close already unwound.


if __name__ == '__main__':
    writer(Path(sys.argv[1]), Path(sys.argv[2]))
