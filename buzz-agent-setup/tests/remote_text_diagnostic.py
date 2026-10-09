"""Retained low diagnostics only; never imports Hostd or changes owned verdicts.

Root supplies HOSTD_TEST_DIAGNOSTIC_DIR to the collector/test environment only.
The caller directory must already be absolute, owned, private and outside both
mutable case inputs and SOURCE. Raw stderr stays private; metadata has no
exception message, locals, command line, keys, config, headers or business rows.
"""
from __future__ import annotations

from collections import Counter
from contextlib import closing, contextmanager
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import time
import uuid

import zero_local_fixture as original


MAX_EVENTS = 1024
TABLES = ('binding', 'agent', 'remote_target', 'remote_grant', 'remote_delivery')
PHASES = frozenset(('signed_query', 'native_request', 'physical_post_persisted',
                   'receipt_get_blocked', 'receipt_get_valid'))


def exception_metadata(exc_info):
    """No str(exception), exception args, source text or traceback locals."""
    kind, _, traceback = exc_info
    if kind is None:
        return None
    frames = []
    while traceback is not None:
        code = traceback.tb_frame.f_code
        frames.append({'basename': Path(code.co_filename).name,
                       'method': code.co_name, 'line': traceback.tb_lineno})
        traceback = traceback.tb_next
    return {'type': kind.__name__, 'frames': frames}


def live_identity(expected):
    """Read metadata only for an identical original pid/session/starttime."""
    result = {'pid': expected[0], 'session': expected[1], 'starttime': expected[2],
              'uid': None, 'present_original': False, 'state': None}
    if original.process_identity(expected[0]) != expected:
        return result
    proc = Path('/proc')/str(expected[0])
    try:
        text = (proc/'stat').read_text()
        fields = text[text.rindex(')')+2:].split()
        uid_line = next(line for line in (proc/'status').read_text().splitlines()
                        if line.startswith('Uid:'))
        uid = int(uid_line.split()[1])
        if original.process_identity(expected[0]) == expected:
            result.update(uid=uid, present_original=True, state=fields[0])
    except (OSError, ValueError, StopIteration):
        pass  # Disappearance/race is diagnostic uncertainty, never adoption.
    return result


class RetainedDiagnostics:
    def __init__(self, case, world, sibling_identity):
        supplied = os.environ.get('HOSTD_TEST_DIAGNOSTIC_DIR')
        if not supplied:
            raise ValueError('private collector diagnostic directory required')
        parent = Path(supplied)
        if not parent.is_absolute() or parent.resolve(strict=True) != parent:
            raise ValueError('private collector diagnostic directory must be absolute without symlinks')
        for forbidden in (case.root.parent.resolve(), case.source_root.resolve()):
            if parent == forbidden or forbidden in parent.parents:
                raise ValueError('retained diagnostics must be outside temporary inputs and source')
        descriptor = os.open(parent, os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:
            info = os.fstat(descriptor)
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise ValueError('private collector diagnostic directory must be owned mode0700')
            leaf = 'diagnostic-'+uuid.uuid4().hex
            os.mkdir(leaf, 0o700, dir_fd=descriptor)
            os.chmod(leaf, 0o700, dir_fd=descriptor, follow_symlinks=False)
            child = os.open(leaf, os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,
                            dir_fd=descriptor)
            try:
                fd = os.open('events.ndjson', os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,
                             0o600, dir_fd=child)
                try:
                    os.fchmod(fd, 0o600)
                finally:
                    os.close(fd)
            finally:
                os.close(child)
        finally:
            os.close(descriptor)
        self.directory = parent/leaf
        self.case, self.world, self.sibling_identity = case, world, sibling_identity
        self.events = self.write_failures = 0
        self.last_sample = 0.0
        self.original_uids = {}
        self.owned = None

    def stderr_fd(self):
        # Popen duplicates this descriptor; closing the parent's copy is normal.
        descriptor = os.open(self.directory/'stderr.log',
                             os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    def identities(self, capture):
        members = set(capture.observed_members)  # Already observed, no new scan.
        if capture.identity is not None:
            members.add(capture.identity)
        known = []
        if self.owned is not None:
            # Verified support54bd tuple: pid, ppid, session, starttime, uid.
            # Read ONLY the original Owned object's already observed registry.
            for row in tuple(self.owned._known_members.values()):
                expected = (row[0], row[2], row[3])
                members.add(expected)
                self.original_uids.setdefault(expected, row[4])
                known.append({'pid': row[0], 'ppid_observed': row[1], 'session': row[2],
                              'starttime': row[3], 'uid_observed': row[4]})
        records = []
        for expected in sorted(members):
            row = live_identity(expected)
            if row['uid'] is not None:
                self.original_uids.setdefault(expected, row['uid'])
            row['observed_original_uid'] = self.original_uids.get(expected)
            row['role'] = 'root' if expected == capture.identity else 'observed_session_member'
            records.append(row)
        return {'members': records, 'observed_member_count': len(members),
                'owned_known_members_readonly': sorted(known, key=lambda row: row['pid']),
                'original_sibling_control': live_identity(self.sibling_identity),
                'popen_pid': None if capture.process is None else capture.process.pid,
                'root_identity_captured': capture.identity is not None,
                'root_returncode_observed': None if capture.process is None
                else capture.process.returncode}  # No extra poll/wait/signal.

    def resources(self):
        filesystems = {}
        for role, path in (('temporary', Path('/tmp')), ('shared_memory', Path('/dev/shm')),
                           ('runtime', self.case.root), ('retained', self.directory)):
            try:
                value = os.statvfs(path)
                filesystems[role] = {'block_size': value.f_frsize,
                    'blocks_total': value.f_blocks, 'blocks_free': value.f_bfree,
                    'blocks_available': value.f_bavail,
                    'inodes_total': value.f_files, 'inodes_available': value.f_favail}
            except OSError as error:
                filesystems[role] = {'error_type': type(error).__name__}
        database = self.case.outputs[0]
        files = {}
        for role, path in (('db', database), ('wal', Path(str(database)+'-wal')),
                           ('shm', Path(str(database)+'-shm'))):
            try:
                info = path.lstat()
                files[role] = {'exists': True, 'size': info.st_size,
                    'blocks_512': info.st_blocks, 'uid': info.st_uid,
                    'mode': stat.S_IMODE(info.st_mode), 'regular': stat.S_ISREG(info.st_mode)}
            except FileNotFoundError:
                files[role] = {'exists': False}
            except OSError as error:
                files[role] = {'error_type': type(error).__name__}
        return {'filesystems': filesystems, 'runtime_files': files}

    def native_counts(self):
        # Never serialize trace payloads, filters, token/body or native schemas.
        if not self.world.lock.acquire(blocking=False):
            return {'snapshot_busy': True}
        try:
            counts = {name: len(getattr(self.world, name)) for name in
                ('failures', 'trace', 'posts', 'unknown_before_post', 'signed_queries',
                 'native_calls', 'exact_gets', 'list_reads')}
            phases = Counter(kind if kind in PHASES else 'other'
                             for _, kind, _ in self.world.trace)
            methods = Counter(method if method in ('GET', 'POST') else 'other'
                              for method, _, _ in self.world.native_calls)
            return {'counts': counts, 'phases': dict(phases), 'http_methods': dict(methods),
                    'get_available': self.world.release_get.is_set(),
                    'post_observed': self.world.posted.is_set(),
                    'blocked_get_observed': self.world.blocked_get.is_set()}
        finally:
            self.world.lock.release()

    def sql_counts(self):
        """Only original actual runtime DB, mode=ro/query_only and no Store."""
        path = self.case.outputs[0]
        if not path.is_file():
            return {'exists': False}
        try:
            with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=.02)) as db:
                db.execute('PRAGMA query_only=ON')
                counts = {table: db.execute('SELECT count(*) FROM '+table).fetchone()[0]
                          for table in TABLES}
                # Only fixed allowed delivery states, not identifiers or rows.
                states = {name: db.execute('SELECT count(*) FROM remote_delivery WHERE status=?',
                                           (name,)).fetchone()[0]
                          for name in ('reserved', 'unknown', 'acked')}
            return {'exists': True, 'counts': counts, 'delivery_status_counts': states}
        except sqlite3.Error as error:
            return {'exists': True, 'error_type': type(error).__name__}

    def note(self, stage, capture, *, exc_info=(None, None, None), sql=False):
        """Diagnostics IO failure never swallows/replaces a business exception."""
        try:
            if self.events >= MAX_EVENTS:
                return
            row = {'sequence': self.events, 'monotonic_ns': time.monotonic_ns(),
                   'stage': stage, 'exception': exception_metadata(exc_info),
                   'identities': self.identities(capture), 'resources': self.resources(),
                   'native': self.native_counts(), 'prior_diagnostic_write_failures': self.write_failures}
            self.append(row)
            if sql:
                # Persist the original exception metadata before native SQL IO.
                self.append({'sequence': self.events, 'monotonic_ns': time.monotonic_ns(),
                             'stage': stage+'.sql_counts', 'sql': self.sql_counts()})
        except Exception:
            self.write_failures += 1  # Metadata only; never alter original outcome.

    def append(self, row):
        if self.events >= MAX_EVENTS:
            return
        data = (json.dumps(row, sort_keys=True, separators=(',', ':'))+'\n').encode()
        fd = os.open(self.directory/'events.ndjson', os.O_WRONLY|os.O_APPEND|os.O_NOFOLLOW)
        try:
            view = memoryview(data)
            while view:
                count = os.write(fd, view)
                if count == 0:
                    raise OSError('diagnostic short write')
                view = view[count:]
        finally:
            os.close(fd)
        self.events += 1

    def sample(self, capture):
        now = time.monotonic()
        if now-self.last_sample >= 1.0:
            self.last_sample = now
            self.note('poll.sample', capture)

    @contextmanager
    def operation(self, name, capture, *, sql=False):
        self.note(name+'.before', capture, sql=sql)
        try:
            yield
        except BaseException:
            self.note(name+'.error', capture, exc_info=sys.exc_info(), sql=sql)
            raise
        finally:
            self.note(name+'.after', capture, sql=sql)


class DiagnosticCapturePopen(original.CapturePopen):
    """Delegates exact original capture; ONLY stderr descriptor is replaced."""
    def __init__(self, case, plan, world, sibling_identity):
        super().__init__(case, plan)
        self.diagnostics = RetainedDiagnostics(case, world, sibling_identity)

    def __call__(self, argv, **kwargs):
        assert kwargs.get('stdout') == subprocess.DEVNULL
        assert kwargs.get('stderr') == subprocess.DEVNULL
        # Original request, env/argv/wire/identity checks and actual Popen remain.
        with self.diagnostics.operation('spawn', self):
            fd = self.diagnostics.stderr_fd()
            try:
                kwargs['stderr'] = fd
                result = super().__call__(argv, **kwargs)
            finally:
                os.close(fd)
            self.diagnostics.note('spawn.returned', self)
            return result


def sibling_identity(proc):
    """Original owned PID, parent, session, starttime and UID only."""
    expected = original.process_identity(proc.pid)
    if expected is None:
        return None
    path = Path('/proc')/str(proc.pid)
    fields = (path/'stat').read_text().rsplit(') ', 1)[1].split()
    uid = int(next(line for line in (path/'status').read_text().splitlines()
                   if line.startswith('Uid:')).split()[1])
    if original.process_identity(proc.pid) != expected:
        return None
    return (*expected, int(fields[1]), uid)


def sibling_canary():
    """Finite owner-pipe canary; independent of normal business deadlines.

    The longest unchanged case has registration30 + POST90 + GET75 + ACK75
    + stop10 = 280 seconds. This 360-second emergency ceiling bounds leakage;
    ordinary cleanup closes the owner's pipe and genuinely waits its child.
    """
    proc = subprocess.Popen([sys.executable, '-c',
        'import select,sys;select.select([sys.stdin],[],[],360)'],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env={'PATH':os.defpath, 'LANG':'C.UTF-8', 'PYTHONDONTWRITEBYTECODE':'1'},
        start_new_session=True, close_fds=True, umask=0o077)
    proc._normal_canary_identity = sibling_identity(proc)
    assert proc._normal_canary_identity is not None
    assert proc._normal_canary_identity[1] == proc.pid
    assert proc._normal_canary_identity[3:] == (os.getpid(), os.getuid())
    return proc


def reap_sibling(proc):
    """Release only this original child; Popen.wait is the parent reap proof."""
    expected = proc._normal_canary_identity
    if proc.poll() is None:
        assert sibling_identity(proc) == expected
        proc.stdin.close()
    else:
        proc.stdin.close()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        assert sibling_identity(proc) == expected
        proc.terminate()
        proc.wait(timeout=5)
