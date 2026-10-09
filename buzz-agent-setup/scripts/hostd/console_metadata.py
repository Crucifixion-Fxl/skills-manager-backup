"""Optional display-only local snapshot. No authority, Store or remote API reads."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import time

from .console_access import _open

MAX_BYTES = 512 * 1024
MAX_AGE = 24 * 60 * 60
ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}\Z')
HEX = re.compile(r'[0-9a-f]{64}\Z')
CHAT = re.compile(r'oc_[A-Za-z0-9_]{1,128}\Z')
APP = re.compile(r'cli_[A-Za-z0-9_]{1,128}\Z')


def _name(value):
    if (not isinstance(value, str) or len(value) > 256 or
            any(ord(c) < 32 or 127 <= ord(c) < 160 or 0xD800 <= ord(c) <= 0xDFFF for c in value)):
        raise ValueError('invalid_name')
    return value.strip()


def _exact(row, keys):
    if not isinstance(row, dict) or set(row) != set(keys):
        raise ValueError('invalid_fields')


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate_field')
        result[key] = value
    return result


class DisplayMetadata:
    def __init__(self, document=None):
        self._document = document

    @classmethod
    def load(cls, path):
        """Malformed/missing optional metadata cannot disable Console operations."""
        if path is None:
            return cls()
        try:
            path = Path(path)
            parent = _open(path.parent, directory=True)
            os.close(parent)
            fd = _open(path)
            with os.fdopen(fd, 'rb') as file:
                before = os.fstat(file.fileno())
                raw = file.read(MAX_BYTES + 1)
                after = os.fstat(file.fileno())
            if (len(raw) > MAX_BYTES or
                    (before.st_size, before.st_mtime_ns, before.st_ctime_ns) !=
                    (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
                raise ValueError('changed_or_large')
            doc = json.loads(raw, object_pairs_hook=_unique_object)
            _exact(doc, ('version', 'observed_at', 'bindings', 'agents'))
            if doc['version'] != 1 or type(doc['version']) is not int or type(doc['observed_at']) is not int or doc['observed_at'] < 0:
                raise ValueError('invalid_version_or_time')
            if not isinstance(doc['bindings'], list) or len(doc['bindings']) > 1000 or not isinstance(doc['agents'], list) or len(doc['agents']) > 2000:
                raise ValueError('invalid_count')
            seen, names = set(), {}
            for row in doc['bindings']:
                _exact(row, ('binding_id', 'channel_id', 'chat_ref', 'chat_id', 'chat_name', 'channel_name'))
                if (not all(isinstance(row[k], str) for k in row) or
                        not ID.fullmatch(row['binding_id']) or not ID.fullmatch(row['channel_id']) or
                        not CHAT.fullmatch(row['chat_id']) or not HEX.fullmatch(row['chat_ref']) or
                        row['chat_ref'] != hashlib.sha256(('buzz-feishu-chat:v1:' + row['chat_id']).encode()).hexdigest() or
                        row['binding_id'] in seen):
                    raise ValueError('invalid_binding_identity')
                seen.add(row['binding_id'])
                row['chat_name'] = _name(row['chat_name'])
                row['channel_name'] = _name(row['channel_name'])
                for kind, identity, name in (('chat', row['chat_ref'], row['chat_name']),
                                             ('channel', row['channel_id'], row['channel_name'])):
                    key = (kind, identity)
                    if key in names and names[key] != name:
                        raise ValueError('ambiguous_display_name')
                    names[key] = name
            seen, apps = set(), set()
            for row in doc['agents']:
                _exact(row, ('pubkey', 'app_id', 'name'))
                if (not all(isinstance(row[k], str) for k in row) or not HEX.fullmatch(row['pubkey']) or
                        not APP.fullmatch(row['app_id']) or row['pubkey'] in seen or row['app_id'] in apps):
                    raise ValueError('invalid_agent_identity')
                seen.add(row['pubkey']); apps.add(row['app_id'])
                row['name'] = _name(row['name'])
            return cls(doc)
        except (OSError, ValueError, TypeError, RecursionError):
            return cls()

    def public(self, *, now=None):
        now = time.time() if now is None else now
        empty = {'version': 1, 'status': 'unavailable', 'observed_at': None, 'bindings': [], 'agents': []}
        if self._document is None:
            return empty
        age = now - self._document['observed_at']
        if age < 0:
            return empty
        # Display cache age is not an authorization lease. Identity/edge matching
        # remains mandatory in the live graph; old names stay readable and marked.
        return {**self._document, 'status': 'stale' if age > MAX_AGE else 'available'}
