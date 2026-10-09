#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Offline window/coverage planner. No source verification, authorization or SaaS writes.

Inputs must come from the verified read boundary described in SKILL.md. Neither
model-supplied origin fields nor a successful plan grant authority to post notes.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import stat
from pathlib import Path
import re

HEX64 = re.compile(r'[0-9a-f]{64}')
CHANNEL = re.compile(r'(?:[0-9a-f]{64}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})')
PROJECT = re.compile(r'[a-z0-9]+(?:[.-][a-z0-9]+)*\.[a-z]{2,}/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+')
KINDS = {'fact', 'proposal', 'decision', 'progress', 'blocker', 'action', 'question', 'conflict', 'inference'}
ORIGINAL = {'human', 'agent'}
ECHO = {'gitlab_sync', 'workflow', 'result'}
EXCLUSIONS = {'non_substantive', 'already_in_issue', 'workflow_output'}
MAX_INPUT_BYTES = 2 * 1024 * 1024
MAX_EVENTS = 5000
MAX_ITEMS = 10000
MAX_DEPTH = 16
MAX_NODES = 100000
MAX_STRING = 65536



def _check_shape_limits(value):
    pending = [(value, 0)]
    visited = 0
    while pending:
        current, depth = pending.pop()
        visited += 1
        if visited > MAX_NODES or depth > MAX_DEPTH:
            raise ValueError('input structure exceeds bounded limits')
        if isinstance(current, dict):
            if len(current) > MAX_ITEMS or any(not isinstance(k, str) or len(k) > 128 for k in current):
                raise ValueError('invalid object size/key')
            pending.extend((v, depth + 1) for v in current.values())
        elif isinstance(current, (list, tuple, set)):
            if len(current) > MAX_ITEMS:
                raise ValueError('input array exceeds bounded limit')
            pending.extend((v, depth + 1) for v in current)
        elif isinstance(current, str) and len(current) > MAX_STRING:
            raise ValueError('input string exceeds bounded limit')


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def _reject_constant(_):
    raise ValueError('non-finite JSON value')


def read_snapshot(path):
    """Bounded regular-file read; reject symlinks in every component and races."""
    path = Path(path)
    if '..' in path.parts:
        raise ValueError('parent traversal is not accepted')
    path = path.absolute()
    # Walk with openat: no check/open gap for symlink parents. A pinned parent
    # directory FD also makes the leaf's readback independent of path swaps.
    directory = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    fd = None
    try:
        for part in path.parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory)
            os.close(directory)
            directory = child
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=directory)
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_INPUT_BYTES:
            raise ValueError('input must be a bounded regular file')
        blocks, total = [], 0
        while True:
            block = os.read(fd, min(65536, MAX_INPUT_BYTES + 1 - total))
            if not block:
                break
            total += len(block)
            if total > MAX_INPUT_BYTES:
                raise ValueError('input exceeds byte limit')
            blocks.append(block)
        stamp = lambda st: (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
        if (stamp(before) != stamp(os.fstat(fd)) or
                stamp(before) != stamp(os.stat(path.name, dir_fd=directory, follow_symlinks=False))):
            raise ValueError('input changed while reading')
    finally:
        if fd is not None:
            os.close(fd)
        os.close(directory)
    text = b''.join(blocks).decode('utf-8')
    # Bound nesting *before* json.loads; braces inside strings are data.
    depth, quoted, escaped = 0, False, False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in '[{':
            depth += 1
            if depth > MAX_DEPTH:
                raise ValueError('JSON nesting exceeds bounded limit')
        elif char in ']}':
            depth -= 1
    value = json.loads(text, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    _check_shape_limits(value)
    if not isinstance(value, dict):
        raise ValueError('input must be a JSON object')
    return value


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def daily_window(scheduled_for):
    """Use a scheduled tick, never the wall clock of a delayed/replayed run."""
    if not isinstance(scheduled_for, str):
        raise ValueError('scheduled_for must be an explicit timezone-aware tick')
    tick = datetime.fromisoformat(scheduled_for.replace('Z', '+00:00'))
    if tick.tzinfo is None or tick.utcoffset() is None:
        raise ValueError('scheduled tick requires timezone')
    end = tick.astimezone(timezone.utc)
    if (end.hour, end.minute, end.second, end.microsecond) != (14, 0, 0, 0):
        raise ValueError('scheduled tick must be Beijing 22:00 / UTC 14:00')
    start = end - timedelta(days=1)
    return {'start': start.isoformat().replace('+00:00', 'Z'),
            'end': end.isoformat().replace('+00:00', 'Z'),
            'start_epoch': int(start.timestamp()), 'end_epoch': int(end.timestamp())}


def valid_project(value):
    return (isinstance(value, str) and PROJECT.fullmatch(value) is not None
            and all(p not in ('.', '..') for p in value.split('/')))


def parse_target(value):
    if not isinstance(value, str):
        raise ValueError('target must be an exact host/project#IID')
    project, separator, iid = value.rpartition('#')
    if not separator or not valid_project(project) or re.fullmatch(r'[1-9][0-9]*', iid) is None:
        raise ValueError('invalid target')
    return project, int(iid)


def plan(window, events, items, exclusions, allowed_projects, *, channel_id, previous=(), pending=False, complete=False):
    """Account for every in-window message and propose minimal existing-Issue deltas.

    `complete` reports an independently verified full scan, not proof generated
    here. `previous` contains keys from successfully read-back writes/verified
    equivalent evidence, never merely previously *planned* keys. `origin` comes
    from the trusted reader, not message text or the summarizing model.
    `can_advance_window` is true only when no new writes or unresolved items
    remain; the caller still has to verify receipt/ledger/source preconditions.
    """
    if not isinstance(events, list) or len(events) > MAX_EVENTS or not isinstance(items, list) or len(items) > MAX_ITEMS:
        raise ValueError('events/items exceeds bounded limit')
    _check_shape_limits([events, items, exclusions, allowed_projects, previous])
    if not isinstance(channel_id, str) or CHANNEL.fullmatch(channel_id) is None:
        raise ValueError('exact verified channel id required')
    if not isinstance(window, dict) or daily_window(window.get('end')) != window:
        raise ValueError('window does not match the scheduled tick')
    if type(complete) is not bool or type(pending) is not bool:
        raise ValueError('complete/pending must be booleans')
    if not isinstance(allowed_projects, (list, tuple)) or not allowed_projects or not all(valid_project(p) for p in allowed_projects):
        raise ValueError('exact approved project intersection required')
    if not isinstance(events, list) or not isinstance(items, list) or not isinstance(exclusions, dict):
        raise ValueError('events/items/exclusions shape invalid')
    if not isinstance(previous, (list, tuple, set)) or not all(isinstance(k, str) and HEX64.fullmatch(k) for k in previous):
        raise ValueError('previous must contain confirmed evidence keys')
    by_id = {}
    for event in events:
        if not isinstance(event, dict) or not isinstance(event.get('id'), str) or not HEX64.fullmatch(event['id']):
            raise ValueError('invalid source event id')
        if event.get('channel_id') != channel_id:
            raise ValueError('source event belongs to another channel')
        if not isinstance(event.get('pubkey'), str) or HEX64.fullmatch(event['pubkey']) is None:
            raise ValueError('verified original signer required')
        if type(event.get('created_at')) is not int or event['created_at'] < 0:
            raise ValueError('invalid source timestamp')
        if not isinstance(event.get('origin'), str) or event.get('origin') not in ORIGINAL | ECHO or not isinstance(event.get('content'), str):
            raise ValueError('unknown source provenance or invalid body')
        if not isinstance(event.get('thread_root'), str) or not HEX64.fullmatch(event['thread_root']):
            raise ValueError('verified thread root required')
        # Keep only stable evidence; read timestamp, display name and snapshot
        # metadata do not create a new source version on redelivery.
        event = {k: event[k] for k in ('id', 'channel_id', 'pubkey', 'created_at',
                                     'thread_root', 'content', 'origin')}
        if event['id'] in by_id and by_id[event['id']] != event:
            raise ValueError('conflicting duplicate event')
        by_id[event['id']] = event
    active = {key: value for key, value in by_id.items()
              if window['start_epoch'] <= value['created_at'] < window['end_epoch']}
    result = {'action': 'plan', 'window': window, 'updates': [], 'noops': [],
              'pending_items': [], 'covered_event_ids': [], 'uncovered_event_ids': sorted(active),
              'context_event_ids': sorted(set(by_id) - set(active)),
              'coverage_complete': False, 'can_advance_window': False}
    # These states allow only source completion or read-only reconciliation.
    if pending or not complete:
        result['action'] = 'reconcile' if pending else 'incomplete'
        return result
    covered = set()
    for key, reason in exclusions.items():
        if key not in active or reason not in EXCLUSIONS:
            raise ValueError('unknown exclusion or source')
        if reason == 'workflow_output' and active[key]['origin'] not in {'workflow', 'result'}:
            raise ValueError('original discussion is not workflow output')
        if reason == 'non_substantive' and active[key]['origin'] not in ORIGINAL:
            raise ValueError('echo requires an explicit evidence/output disposition')
        # already_in_issue requires equivalent existing-Issue evidence checked by
        # caller; an origin label alone is insufficient to claim that evidence.
        covered.add(key)
    seen_ids, seen_keys = set(), set()
    for row in items:
        if not isinstance(row, dict) or not isinstance(row.get('id'), str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', row['id']):
            raise ValueError('stable item id required')
        if row['id'] in seen_ids:
            raise ValueError('duplicate item id')
        seen_ids.add(row['id'])
        refs = row.get('event_ids')
        if not isinstance(refs, list) or not refs or any(not isinstance(k, str) or k not in active for k in refs):
            raise ValueError('item references missing/out-of-window event')
        refs = sorted(set(refs))
        if any(active[k]['origin'] not in ORIGINAL for k in refs):
            raise ValueError('echo cannot be used as original progress evidence')
        if any(k in exclusions for k in refs):
            raise ValueError('event cannot be both excluded and mapped')
        if not isinstance(row.get('kind'), str) or row.get('kind') not in KINDS or not isinstance(row.get('summary'), str) or not row['summary'].strip():
            raise ValueError('typed substantive summary required')
        span = row.get('span_id', 'whole-event')
        if not isinstance(span, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', span):
            raise ValueError('stable source span required')
        target = row.get('target')
        covered.update(refs)
        if target is None or isinstance(target, list):
            if isinstance(target, list):
                if len(target) < 2:
                    raise ValueError('ambiguous target needs multiple candidates')
                for candidate in target:
                    project, _ = parse_target(candidate)
                    if project not in allowed_projects:
                        raise ValueError('candidate outside allowed projects')
            result['pending_items'].append({'id': row['id'], 'event_ids': refs,
                                            'reason': 'target_unmatched' if target is None else 'target_ambiguous'})
            continue
        project, iid = parse_target(target)
        if project not in allowed_projects:
            result['pending_items'].append({'id': row['id'], 'event_ids': refs, 'reason': 'target_out_of_scope'})
            continue
        # Ignore LLM wording and generated item ID for dedupe: source span,
        # original source bytes, and target are the evidence identity.
        key = fingerprint({'target': target, 'span': span,
                           'sources': [{field: active[k][field] for field in
                                       ('id', 'channel_id', 'pubkey', 'created_at', 'thread_root', 'content')}
                                      for k in refs]})
        if key in seen_keys:
            raise ValueError('duplicate source span/target in plan')
        seen_keys.add(key)
        entry = {'id': row['id'], 'target': target, 'project': project, 'iid': iid,
                 'kind': row['kind'], 'summary': row['summary'], 'event_ids': refs,
                 'thread_roots': sorted({active[k]['thread_root'] for k in refs}),
                 'span_id': span, 'evidence_key': key}
        result['noops' if key in previous else 'updates'].append(entry)
    result['covered_event_ids'] = sorted(covered)
    result['uncovered_event_ids'] = sorted(set(active) - covered)
    result['coverage_complete'] = not result['uncovered_event_ids']
    result['can_advance_window'] = result['coverage_complete'] and not result['pending_items'] and not result['updates']
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    tick = commands.add_parser('window', help='derive the fixed scheduled 24-hour window')
    tick.add_argument('--scheduled-for', required=True)
    draft = commands.add_parser('plan', help='offline only; JSON input is not authority to write')
    draft.add_argument('--input', required=True, type=Path)
    args = parser.parse_args()
    try:
        result = daily_window(args.scheduled_for) if args.command == 'window' else plan(**read_snapshot(args.input))
    except (ValueError, TypeError, OSError, UnicodeError, RecursionError):
        parser.error('invalid or unsafe input; no plan produced')
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == '__main__':
    main()
