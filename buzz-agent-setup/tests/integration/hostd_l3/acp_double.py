#!/usr/bin/env python3
"""Deterministic run-owned ACP provider DOUBLE; never a publication oracle.

Stdout is ACP v1 only. No network, shell, provider, prompt-driven file access,
credentials or native buzz-acp execution. Native session routing is external.
"""
import argparse
from datetime import datetime
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import select
import stat
import sys
import time

NOTICE = ('本次 ACP 测试替身未通过受保护输入或协议检查。怎么解决：核对本次私有目录、'
          '已审核清单与 ACP 输入格式。复制给 AI：ACP_DOUBLE_INPUT_OR_TRANSPORT_FAILED。')
FRAME_LIMIT = 65536
WRITE_BUDGET = 2.0
HOLD_BUDGET = 10.0
IDLE_BUDGET = 120.0
SESSION_LIMIT = 16
PROMPT_LIMIT = 64


class DoubleError(Exception):
    pass


class TransportError(Exception):
    pass


class SafeParser(argparse.ArgumentParser):
    def error(self, unused):
        raise DoubleError()


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DoubleError()
        result[key] = value
    return result


def _json(raw):
    return json.loads(raw, object_pairs_hook=_pairs,
                      parse_constant=lambda unused: (_ for _ in ()).throw(DoubleError()))


def _canonical(value):
    if not isinstance(value, str) or len(value) > 4096 or any(ord(c) < 32 for c in value):
        raise DoubleError()
    path = Path(value)
    if not path.is_absolute() or str(path) != value or '..' in path.parts:
        raise DoubleError()
    return path


def _open(path, directory=False):
    """Walk every ancestor without following a symlink; never resolve aliases."""
    path = _canonical(str(path))
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for index, name in enumerate(path.parts[1:]):
            last = index == len(path.parts) - 2
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            if not last or directory:
                flags |= os.O_DIRECTORY
            child = os.open(name, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _private(meta, directory=False):
    if (meta.st_uid != os.geteuid() or stat.S_IMODE(meta.st_mode) != (0o700 if directory else 0o600)
            or not (stat.S_ISDIR(meta.st_mode) if directory else stat.S_ISREG(meta.st_mode))
            or (not directory and meta.st_nlink != 1)):
        raise DoubleError()


def _identity(meta):
    return meta.st_dev, meta.st_ino


class Manifest:
    def __init__(self, path, pin):
        self.path = _canonical(path)
        if not isinstance(pin, str) or not re.fullmatch('[0-9a-f]{64}', pin):
            raise DoubleError()
        self.pin = pin
        self.root = self.path.parent
        fd = _open(self.root, True)
        try:
            meta = os.fstat(fd); _private(meta, True)
            self.root_identity = _identity(meta)
        finally:
            os.close(fd)
        raw, self.file_identity = self._read()
        doc = _json(raw)
        if (not isinstance(doc, dict) or set(doc) != {'version', 'run_id', 'work_dir', 'receipt_file', 'reply_text', 'hold_marker'}
                or type(doc['version']) is not int or doc['version'] != 1
                or not isinstance(doc['run_id'], str) or not re.fullmatch('[0-9a-f]{32}', doc['run_id'])):
            raise DoubleError()
        for key in ('reply_text', 'hold_marker'):
            if not isinstance(doc[key], str) or not doc[key] or len(doc[key].encode()) > 4096:
                raise DoubleError()
        if doc['hold_marker'] != 'L3-HOLD-' + doc['run_id']:
            raise DoubleError()
        self.work = _canonical(doc['work_dir']); self.receipt = _canonical(doc['receipt_file'])
        if self.work.parent != self.root or self.receipt.parent != self.root or self.receipt == self.path:
            raise DoubleError()
        fd = _open(self.work, True)
        try:
            meta = os.fstat(fd); _private(meta, True)
            self.work_identity = _identity(meta)
        finally:
            os.close(fd)
        if os.path.lexists(self.receipt):
            raise DoubleError()
        self.doc = doc
        self.check()

    def _read(self):
        fd = _open(self.path)
        try:
            meta = os.fstat(fd); _private(meta)
            if meta.st_size > 16384:
                raise DoubleError()
            raw = os.read(fd, 16385)
            if len(raw) > 16384 or hashlib.sha256(raw).hexdigest() != self.pin:
                raise DoubleError()
            return raw, _identity(meta)
        finally:
            os.close(fd)

    def check(self):
        if self._read()[1] != self.file_identity:
            raise DoubleError()
        for path, expected in ((self.root, self.root_identity), (self.work, self.work_identity)):
            fd = _open(path, True)
            try:
                meta = os.fstat(fd); _private(meta, True)
                if _identity(meta) != expected:
                    raise DoubleError()
            finally:
                os.close(fd)
        if os.path.lexists(self.receipt):
            raise DoubleError()

    def finish(self, counts, status):
        self.check()
        fd = _open(self.root, True)
        try:
            if _identity(os.fstat(fd)) != self.root_identity:
                raise DoubleError()
            out = os.open(self.receipt.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                          0o600, dir_fd=fd)
            try:
                _private(os.fstat(out))
                body = json.dumps(dict(backend='deterministic_acp_double', live_verified=False,
                                       status=status, **counts), separators=(',', ':')).encode() + b'\n'
                offset = 0
                while offset < len(body):
                    offset += os.write(out, body[offset:])
                os.fsync(out)
            finally:
                os.close(out)
        finally:
            os.close(fd)

def _hash_json(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                   ensure_ascii=False).encode()).hexdigest()


def parse_prompt(blocks):
    """Extract bounded hash metadata; this is never a signature/owner oracle.

Only the single semantic mention frame is supported. Native framing preserves
raw content, so reserved headers/delimiters in that content are ambiguous.
"""
    try:
        if (not isinstance(blocks, list) or not 1 <= len(blocks) <= 32
                or len(json.dumps(blocks, ensure_ascii=False).encode()) > FRAME_LIMIT):
            return None
        selected = []
        for block in blocks:
            if not isinstance(block, dict):
                return None
            if block.get('type') != 'text':
                if block.get('type') not in ('image', 'resource_link'):
                    return None
                continue  # Metadata only; never open a resource URI.
            text = block.get('text')
            if not isinstance(text, str) or len(text.encode()) > 16384:
                return None
            prefix, suffix = '<buzz-event type="@mention">\n', '\n</buzz-event>'
            if text.startswith(prefix) and text.endswith(suffix):
                selected.append(text[len(prefix):-len(suffix)])
            elif any(marker in text for marker in ('buzz-event', 'Event ID:', 'Tags:')):
                return None
        if len(selected) != 1:
            return None
        raw = selected[0]
        header = re.fullmatch(r'Event ID: ([0-9a-f]{64})\nChannel: ([^\n]+)\nKind: 9\n'
                             r'From: ([^\n]+)\nTime: ([^\n]+)\nContent: ([\s\S]*)', raw)
        if header is None:
            return None
        event_id, channel_text, actor, stamp, rest = header.groups()
        channel_match = re.fullmatch(r'(?:[^\n<>]+ \(#)?([0-9a-f]{8}-[0-9a-f]{4}-'
                                    r'[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?:\))?', channel_text)
        if channel_match is None:
            return None
        channel = channel_match.group(1)
        if channel_text != channel and not channel_text.endswith('(#' + channel + ')'):
            return None
        sender = re.fullmatch(r'(?:[^\n<>]+ \(npub: )?(npub1[023456789acdefghjklmnpqrstuvwxyz]+)'
                              r'(?:, hex: | \(hex: )([0-9a-f]{64})\)', actor)
        if sender is None:
            return None
        # A terminal hex field is structural, not a displayed actor name.
        author = sender.group(2)
        when = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
        if when.tzinfo is None or when.microsecond or not 0 <= when.timestamp() < 2 ** 63:
            return None
        created_at = int(when.timestamp())
        if rest.count('\nTags: ') != 1:
            return None
        body, tags_text = rest.split('\nTags: ', 1)
        if '\nParsed: ' in tags_text:
            tags_text, parsed = tags_text.split('\nParsed: ', 1)
            if (not parsed or '\n' in parsed or len(parsed.encode()) > 2048
                    or any(marker in parsed for marker in ('<', '>', 'Event ID:', 'Tags:'))):
                return None
        if (any(ord(c) < 32 and c not in '\n\t' for c in body)
                or any(marker in body for marker in ('Event ID:', 'Channel:', 'Kind:', 'From:',
                    'Time:', 'Tags:', 'Parsed:', '<buzz-event', '</buzz-event>'))):
            return None
        tags = _json(tags_text)
        if (not isinstance(tags, list) or len(tags) > 128
                or any(not isinstance(t, list) or not 1 <= len(t) <= 16
                    or any(not isinstance(v, str) or len(v.encode()) > 4096 for v in t) for t in tags)):
            return None
        if [t for t in tags if t[:1] == ['h']] != [['h', channel]]:
            return None
        mentions = [t[1] for t in tags if t[:1] == ['p'] and len(t) >= 2]
        if (len(mentions) != len(set(mentions))
                or any(not re.fullmatch('[0-9a-f]{64}', item) for item in mentions)):
            return None
        metas = [t for t in tags if t[:1] == ['imeta']]
        if len(metas) != 1:
            return None
        fields = {}
        for value in metas[0][1:]:
            key, separator, item = value.partition(' ')
            if not separator or not item or key in fields:
                return None
            fields[key] = item
        image = fields.get('x')
        if not isinstance(image, str) or not re.fullmatch('[0-9a-f]{64}', image):
            return None
        return {'event_id': event_id, 'channel_id': channel, 'kind': 9,
                'author_pubkey': author, 'created_at': created_at,
                'body_sha256': hashlib.sha256(body.encode()).hexdigest(),
                'tags_sha256': _hash_json(tags), 'image_sha256': image}
    except (ValueError, TypeError, UnicodeError, OverflowError, DoubleError):
        return None


def process_metadata(pid):
    """Fixed kernel process metadata only; never read environment or secrets."""
    if type(pid) is not int or not 0 < pid < 2 ** 31:
        raise DoubleError()
    directory = os.open('/proc/' + str(pid), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        if os.fstat(directory).st_uid != os.geteuid():
            raise DoubleError()
        def read(name):
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory)
            try:
                raw = os.read(fd, 65537)
                if len(raw) > 65536:
                    raise DoubleError()
                return raw
            finally:
                os.close(fd)
        first = read('stat').decode().rsplit(')', 1)[1].split()
        start, parent = int(first[19]), int(first[1])
        group = read('cgroup')
        if (start <= 0 or len(group.splitlines()) != 1 or not group.startswith(b'0::/')
                or read('stat').decode().rsplit(')', 1)[1].split()[19] != str(start)):
            raise DoubleError()
        return {'pid': pid, 'start_ticks': start, 'parent_pid': parent,
                'cgroup_sha256': hashlib.sha256(group).hexdigest()}
    finally:
        os.close(directory)


def process_ancestry(pid):
    own = process_metadata(pid)
    chain, seen = [], {pid}
    parent = own['parent_pid']
    while parent > 0 and len(chain) < 16:
        if parent in seen:
            raise DoubleError()
        seen.add(parent)
        try:
            value = process_metadata(parent)
        except (OSError, DoubleError):
            break  # Only the bounded same-user chain is observable.
        chain.append([parent, value['start_ticks']])
        parent = value['parent_pid']
    if not chain or process_metadata(pid) != own:
        raise DoubleError()
    return own, chain


class WitnessConfig:
    """Optional four-key configuration; no prompt-derived filesystem path."""
    def __init__(self, manifest, path, pin):
        self.manifest = manifest
        self.path = _canonical(path)
        if self.path.parent != manifest.root or self.path == manifest.path:
            raise DoubleError()
        if not isinstance(pin, str) or not re.fullmatch('[0-9a-f]{64}', pin):
            raise DoubleError()
        self.pin = pin
        raw, self.file_identity = self._read()
        value = _json(raw)
        if (not isinstance(value, dict) or set(value) != {'version', 'run_id', 'work_dir', 'witness_file'}
                or type(value['version']) is not int or value['version'] != 1
                or value['run_id'] != manifest.doc['run_id'] or value['work_dir'] != str(manifest.work)):
            raise DoubleError()
        self.witness = _canonical(value['witness_file'])
        if (self.witness.parent != manifest.root or self.witness in
                (manifest.path, self.path, manifest.receipt, manifest.work)):
            raise DoubleError()
        self.check()

    def _read(self):
        fd = _open(self.path)
        try:
            meta = os.fstat(fd); _private(meta)
            raw = os.read(fd, 16385)
            if meta.st_size > 16384 or len(raw) > 16384 or hashlib.sha256(raw).hexdigest() != self.pin:
                raise DoubleError()
            return raw, _identity(meta)
        finally:
            os.close(fd)

    def check(self):
        self.manifest.check()
        if self._read()[1] != self.file_identity:
            raise DoubleError()


class PromptWitness:
    def __init__(self, config):
        self.config = config
        self.fd = None
        self.identity = None
        self.sealed_fd = None
        self.records = []
        if os.path.lexists(config.witness):
            raise DoubleError()

    def record(self, session, sequence, request_id, prompt):
        self.config.check()
        event = parse_prompt(prompt)
        if event is None:
            return
        own, ancestry = process_ancestry(os.getpid())
        if len(self.records) >= PROMPT_LIMIT:
            raise DoubleError()
        row = {'session_id': session, 'prompt_sequence': sequence,
               'request_id_sha256': _hash_json(request_id), 'prompt_sha256': _hash_json(prompt),
               'provider_pid': own['pid'], 'provider_start_ticks': own['start_ticks'],
               'ancestors': ancestry, 'cgroup_sha256': own['cgroup_sha256'], 'event': event}
        value = {'version': 1, 'run_id': self.config.manifest.doc['run_id'], 'records': [*self.records, row]}
        raw = json.dumps(value, sort_keys=True, separators=(',', ':')).encode() + b'\n'
        if len(raw) > 256 * 1024:
            raise DoubleError()
        sealed = self._sealed_snapshot(raw)
        directory = None
        try:
            directory = _open(self.config.manifest.root, True)
            meta = os.fstat(directory); _private(meta, True)
            if _identity(meta) != self.config.manifest.root_identity:
                raise DoubleError()
            if self.fd is None:
                self.fd = os.open(self.config.witness.name,
                    os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                    0o600, dir_fd=directory)
                self.identity = _identity(os.fstat(self.fd))
            current = os.stat(self.config.witness.name, dir_fd=directory, follow_symlinks=False)
            _private(current); _private(os.fstat(self.fd))
            if _identity(current) != self.identity or _identity(os.fstat(self.fd)) != self.identity:
                raise DoubleError()
            self.config.check()
            os.lseek(self.fd, 0, os.SEEK_SET); os.ftruncate(self.fd, 0)
            offset = 0
            while offset < len(raw):
                written = os.write(self.fd, raw[offset:])
                if written <= 0:
                    raise DoubleError()
                offset += written
            os.fsync(self.fd)
            self.config.check()
            after = os.stat(self.config.witness.name, dir_fd=directory, follow_symlinks=False)
            _private(after)
            if _identity(after) != self.identity:
                raise DoubleError()
            self.records.append(row)
            previous = self.sealed_fd
            self.sealed_fd = None
            if previous is not None:
                os.close(previous)
            self.sealed_fd = sealed
            sealed = None
        finally:
            if directory is not None:
                os.close(directory)
            if sealed is not None:
                os.close(sealed)

    def _sealed_snapshot(self, raw):
        """Create the immutable kernel copy before publishing its regular twin."""
        required = ('F_SEAL_WRITE', 'F_SEAL_GROW', 'F_SEAL_SHRINK', 'F_SEAL_SEAL',
                    'F_ADD_SEALS', 'F_GET_SEALS')
        if (not hasattr(os, 'memfd_create') or not hasattr(os, 'MFD_CLOEXEC')
                or not hasattr(os, 'MFD_ALLOW_SEALING')
                or any(not hasattr(fcntl, name) for name in required)):
            raise DoubleError()
        fd = -1
        try:
            fd = os.memfd_create('hostd-acp-witness-' + self.config.manifest.doc['run_id'],
                                 os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
            os.fchmod(fd, 0o600)
            meta = os.fstat(fd)
            if (meta.st_uid != os.geteuid() or stat.S_IMODE(meta.st_mode) != 0o600
                    or meta.st_nlink != 0 or not stat.S_ISREG(meta.st_mode)):
                raise DoubleError()
            offset = 0
            while offset < len(raw):
                written = os.write(fd, raw[offset:])
                if written <= 0:
                    raise DoubleError()
                offset += written
            os.fsync(fd)
            if os.fstat(fd).st_size != len(raw):
                raise DoubleError()
            mask = (fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW |
                    fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL)
            fcntl.fcntl(fd, fcntl.F_ADD_SEALS, mask)
            if fcntl.fcntl(fd, fcntl.F_GET_SEALS) & mask != mask:
                raise DoubleError()
            os.lseek(fd, 0, os.SEEK_SET)
            snapshot = bytearray()
            while len(snapshot) <= 256 * 1024:
                chunk = os.read(fd, min(65536, 256 * 1024 + 1 - len(snapshot)))
                if not chunk:
                    break
                snapshot.extend(chunk)
            if bytes(snapshot) != raw:
                raise DoubleError()
            return fd
        except BaseException:
            if fd >= 0:
                os.close(fd)
            raise

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        if self.sealed_fd is not None:
            os.close(self.sealed_fd)
            self.sealed_fd = None


def provider_snapshot(pid, run_id, file_identity):
    """Read the current sealed ledger and verify the provider's held file FD."""
    if type(pid) is not int or not 0 < pid < 2 ** 31 or not re.fullmatch('[0-9a-f]{32}', run_id):
        raise DoubleError()
    directory = os.open('/proc/' + str(pid) + '/fd',
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        if os.fstat(directory).st_uid != os.geteuid():
            raise DoubleError()
        names = os.listdir(directory)
        if len(names) > 512:
            raise DoubleError()
        regular_matches = 0
        sealed_values = []
        for name in names:
            if not re.fullmatch('[0-9]{1,9}', name):
                raise DoubleError()
            try:
                target = os.readlink(name, dir_fd=directory)
                meta = os.stat(name, dir_fd=directory)
            except FileNotFoundError:
                continue
            if (meta.st_dev, meta.st_ino) == file_identity:
                if (not stat.S_ISREG(meta.st_mode) or meta.st_uid != os.geteuid()
                        or stat.S_IMODE(meta.st_mode) != 0o600 or meta.st_nlink != 1):
                    raise DoubleError()
                info = os.open('/proc/' + str(pid) + '/fdinfo/' + name,
                               os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
                try:
                    raw_info = os.read(info, 4097)
                finally:
                    os.close(info)
                if len(raw_info) > 4096:
                    raise DoubleError()
                flags = [line.split(':', 1)[1].strip() for line in raw_info.decode().splitlines()
                         if line.startswith('flags:')]
                if (len(flags) != 1 or not re.fullmatch('[0-7]+', flags[0])
                        or int(flags[0], 8) & os.O_ACCMODE not in (os.O_WRONLY, os.O_RDWR)):
                    raise DoubleError()
                regular_matches += 1
            if target.startswith('/memfd:hostd-acp-witness-'):
                expected = '/memfd:hostd-acp-witness-' + run_id + ' (deleted)'
                if target != expected:
                    raise DoubleError()
                fd = os.open('/proc/' + str(pid) + '/fd/' + name,
                             os.O_RDONLY | os.O_CLOEXEC)
                try:
                    mem = os.fstat(fd)
                    after_target = os.readlink(name, dir_fd=directory)
                    after_entry = os.stat(name, dir_fd=directory)
                    mask = (fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW |
                            fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL)
                    if (after_target != expected
                            or (after_entry.st_dev, after_entry.st_ino) != (mem.st_dev, mem.st_ino)
                            or not stat.S_ISREG(mem.st_mode) or mem.st_uid != os.geteuid()
                            or stat.S_IMODE(mem.st_mode) != 0o600 or mem.st_nlink != 0
                            or mem.st_size > 256 * 1024
                            or fcntl.fcntl(fd, fcntl.F_GET_SEALS) & mask != mask):
                        raise DoubleError()
                    content = bytearray()
                    while len(content) <= mem.st_size:
                        chunk = os.read(fd, min(65536, mem.st_size + 1 - len(content)))
                        if not chunk:
                            break
                        content.extend(chunk)
                    if len(content) != mem.st_size:
                        raise DoubleError()
                    sealed_values.append(bytes(content))
                finally:
                    os.close(fd)
        if regular_matches != 1 or len(sealed_values) != 1:
            raise DoubleError()
        return sealed_values[0]
    finally:
        os.close(directory)


class ACPDouble:
    def __init__(self, manifest, witness=None):
        self.manifest = manifest
        self.witness = witness
        self.initialized = False
        self.sessions = set()
        self.active = {}
        self.ids = set()
        self.counts = {'sessions': 0, 'prompts': 0, 'cancelled': 0, 'refused': 0, 'images': 0}
        self.input = b''
        self.last_input = time.monotonic()

    def emit(self, value):
        body = json.dumps(value, separators=(',', ':'), ensure_ascii=False).encode() + b'\n'
        if len(body) > FRAME_LIMIT:
            raise TransportError()
        end = time.monotonic() + WRITE_BUDGET
        offset = 0
        while offset < len(body):
            left = end - time.monotonic()
            if left <= 0:
                raise TransportError()
            if not select.select([], [1], [], left)[1]:
                raise TransportError()
            try:
                offset += os.write(1, body[offset:])
            except BlockingIOError:
                continue

    def reply(self, rid, result):
        self.emit({'jsonrpc': '2.0', 'id': rid, 'result': result})

    def error(self, rid, code):
        self.counts['refused'] += 1
        self.emit({'jsonrpc': '2.0', 'id': rid, 'error': {'code': code, 'message': NOTICE}})

    def complete(self, sid, reason):
        rid, unused = self.active.pop(sid)
        if reason == 'cancelled':
            self.counts['cancelled'] += 1
        self.reply(rid, {'stopReason': reason})

    def content(self, value):
        if not isinstance(value, list) or not value or len(value) > 32:
            raise DoubleError()
        texts = []
        images = 0
        for block in value:
            if not isinstance(block, dict):
                raise DoubleError()
            kind = block.get('type')
            if kind == 'text':
                text = block.get('text')
                if not isinstance(text, str) or len(text.encode()) > 16384:
                    raise DoubleError()
                texts.append(text)
            elif kind == 'image':
                if block.get('mimeType') not in ('image/png', 'image/jpeg', 'image/webp'):
                    raise DoubleError()
                data = block.get('data')
                if not isinstance(data, str) or len(data) > 32768:
                    raise DoubleError()
                base64.b64decode(data, validate=True)
                images += 1
            elif kind == 'resource_link':
                # Baseline ACP metadata only: never open/fetch the URI.
                if (not isinstance(block.get('uri'), str) or len(block['uri']) > 4096
                        or not isinstance(block.get('name'), str) or len(block['name']) > 4096):
                    raise DoubleError()
            else:
                raise DoubleError()
        return texts, images

    def dispatch(self, req):
        rid = None
        try:
            if not isinstance(req, dict) or req.get('jsonrpc') != '2.0':
                raise DoubleError()
            rid = req.get('id')
            notification = 'id' not in req
            if not notification and (type(rid) not in (int, str) or isinstance(rid, str) and len(rid) > 128):
                rid = None
                raise DoubleError()
            method = req.get('method'); params = req.get('params', {})
            if not isinstance(method, str) or not isinstance(params, dict):
                raise DoubleError()
            if notification:
                if method != 'session/cancel':
                    return
                sid = params.get('sessionId')
                if isinstance(sid, str) and sid in self.active:
                    self.complete(sid, 'cancelled')
                return
            marker = (type(rid).__name__, rid)
            if marker in self.ids or len(self.ids) >= 256:
                self.error(rid, -32600); return
            self.ids.add(marker)
            if method == 'initialize':
                if self.initialized:
                    self.error(rid, -32000); return
                if type(params.get('protocolVersion')) is not int or params['protocolVersion'] != 1:
                    raise DoubleError()
                self.initialized = True
                self.reply(rid, {'protocolVersion': 1, 'agentCapabilities': {'loadSession': False,
                    'promptCapabilities': {'image': True}}, 'authMethods': [],
                    'agentInfo': {'name': 'hostd-l3-deterministic-double', 'version': '1'}})
                return
            if not self.initialized:
                self.error(rid, -32000); return
            if method == 'session/new':
                if (params.get('cwd') != str(self.manifest.work) or params.get('mcpServers') != []
                        or len(self.sessions) >= SESSION_LIMIT):
                    raise DoubleError()
                sid = 'l3-' + self.manifest.doc['run_id'] + '-' + str(len(self.sessions) + 1)
                self.sessions.add(sid); self.counts['sessions'] += 1
                self.reply(rid, {'sessionId': sid}); return
            if method == 'session/prompt':
                sid = params.get('sessionId')
                if not isinstance(sid, str) or sid not in self.sessions or self.counts['prompts'] >= PROMPT_LIMIT:
                    raise DoubleError()
                if sid in self.active:
                    self.error(rid, -32000); return
                texts, images = self.content(params.get('prompt'))
                self.counts['prompts'] += 1; self.counts['images'] += images
                if self.witness is not None:
                    try:
                        self.witness.record(sid, self.counts['prompts'], rid, params['prompt'])
                    except DoubleError:
                        raise TransportError() from None
                self.active[sid] = (rid, time.monotonic() + HOLD_BUDGET)
                if self.manifest.doc['hold_marker'] in texts:
                    return
                self.emit({'jsonrpc': '2.0', 'method': 'session/update', 'params': {'sessionId': sid,
                    'update': {'sessionUpdate': 'agent_message_chunk', 'content': {
                        'type': 'text', 'text': self.manifest.doc['reply_text']}}}})
                self.complete(sid, 'end_turn'); return
            self.error(rid, -32601)
        except (DoubleError, ValueError, TypeError, UnicodeError):
            self.error(rid, -32602)

    def run(self):
        os.set_blocking(0, False); os.set_blocking(1, False)
        while True:
            now = time.monotonic()
            for sid, (unused, deadline) in tuple(self.active.items()):
                if now >= deadline:
                    self.complete(sid, 'max_turn_requests')
            if now - self.last_input > IDLE_BUDGET:
                raise DoubleError()
            if self.input and now - self.last_input > WRITE_BUDGET:
                raise DoubleError()
            if not select.select([0], [], [], 0.05)[0]:
                continue
            try:
                piece = os.read(0, 8192)
            except BlockingIOError:
                continue
            if not piece:
                if self.input:
                    raise DoubleError()
                for sid in tuple(self.active):
                    self.complete(sid, 'cancelled')
                return
            self.last_input = time.monotonic(); self.input += piece
            while b'\n' in self.input:
                line, self.input = self.input.split(b'\n', 1)
                if len(line) > FRAME_LIMIT:
                    raise DoubleError()
                try:
                    req = _json(line)
                except (DoubleError, ValueError, UnicodeError):
                    self.error(None, -32700); continue
                self.manifest.check()
                self.dispatch(req)
            if len(self.input) > FRAME_LIMIT:
                raise DoubleError()


def main(argv=None):
    manifest = backend = witness = None
    failed = False
    try:
        parser = SafeParser(add_help=False, exit_on_error=False)
        parser.add_argument('--manifest', required=True)
        parser.add_argument('--sha256', required=True)
        parser.add_argument('--witness-manifest')
        parser.add_argument('--witness-sha256')
        args = parser.parse_args(argv)
        manifest = Manifest(args.manifest, args.sha256)
        if bool(args.witness_manifest) != bool(args.witness_sha256):
            raise DoubleError()
        if args.witness_manifest:
            witness = PromptWitness(WitnessConfig(manifest, args.witness_manifest, args.witness_sha256))
        backend = ACPDouble(manifest, witness)
        backend.run()
    except BaseException:
        failed = True
    if witness is not None:
        witness.close()
    if backend is not None:
        try:
            manifest.finish(backend.counts, 'failed' if failed else 'closed')
        except BaseException:
            failed = True
    if failed:
        # Fixed human guidance only; never print parser values or exceptions.
        try:
            os.write(2, (NOTICE + '\n').encode())
        except OSError:
            pass
    return int(failed)


if __name__ == '__main__':
    sys.exit(main())
