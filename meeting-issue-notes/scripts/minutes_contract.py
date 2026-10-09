#!/usr/bin/env python3
"""Offline contract helpers; never an authorization, source verifier or SaaS writer."""
import hashlib
import json
import re
import unicodedata
from urllib.parse import urlsplit


def fingerprint(value):
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(unicodedata.normalize('NFC', canonical).encode()).hexdigest()


def parse_link(url, minute_hosts, projects):
    """projects: approved (hostname, exact project path) pairs; no network/redirects."""
    if not isinstance(url, str) or any(ord(c) < 33 or ord(c) == 127 for c in url):
        raise ValueError('invalid URL characters')
    p = urlsplit(url)
    if p.scheme != 'https' or p.username or p.password or p.port not in (None, 443):
        raise ValueError('untrusted URL')
    host = (p.hostname or '').lower()
    m = re.fullmatch(r'/minutes/([a-z0-9]+)', p.path)
    if host in minute_hosts and m:
        return {'kind': 'minutes', 'host': host, 'token': m[1],
                'url': f'https://{host}/minutes/{m[1]}'}
    m = re.fullmatch(r'/([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+)/-/issues/([1-9][0-9]*)', p.path)
    if m and (host, m[1]) in projects and all(x not in ('.', '..') for x in m[1].split('/')):
        return {'kind': 'issue', 'host': host, 'project': m[1], 'iid': int(m[2]),
                'url': f'https://{host}/{m[1]}/-/issues/{m[2]}'}
    raise ValueError('unsupported or out-of-scope link')


def delta_plan(previous, current, *, pending=False):
    """Span-ID keyed claims from a verified source snapshot, not LLM authorization.

    Caller must prove snapshot order, provenance, target and complete previous state.
    With any unknown write result, only reconciliation is allowed.
    """
    if pending:
        return {'action': 'reconcile', 'added': [], 'changed': [], 'removed': []}
    old = {k: fingerprint(v) for k, v in previous.items()}
    new = {k: fingerprint(v) for k, v in current.items()}
    added = sorted(new.keys() - old.keys())
    removed = sorted(old.keys() - new.keys())
    changed = sorted(k for k in old.keys() & new.keys() if old[k] != new[k])
    return {'action': 'delta' if added or changed or removed else 'noop',
            'added': added, 'changed': changed, 'removed': removed}
