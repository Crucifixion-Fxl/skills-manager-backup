#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Owner-authored deployment acceptance over GitLab Notes. Never deploys or approves a release."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.parse import quote, urlsplit
import uuid

# Reuse the platform-owner host and URL policy; do not introduce another auth profile.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_artifact_binding import HOST, parse_issue_url  # noqa: E402

BATCH = 'addx.acceptance.batch/v1'
RECEIPT = 'addx.acceptance.receipt/v1'
RESULTS = ('pending', 'in-progress', 'passed', 'partial', 'failed', 'blocked', 'na')
MAX_BYTES = 4 * 1024 * 1024


class Error(Exception):
    """Only fixed error codes leave this process; never CLI stderr or response bodies."""


def require(condition, code):
    if not condition:
        raise Error(code)


def compact(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(compact(value).encode()).hexdigest()


def positive(value):
    return type(value) is int and value > 0


def text(value, limit=2000):
    return isinstance(value, str) and 0 < len(value.strip()) <= limit and not any(
        ord(c) < 32 and c not in '\n\t' for c in value)


def timestamp(value):
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        require(result.tzinfo is not None, 'TIMEZONE_REQUIRED')
        return result
    except (AttributeError, ValueError, TypeError):
        raise Error('INVALID_TIMESTAMP') from None


def https_url(value):
    require(isinstance(value, str) and len(value) <= 2000 and not any(
        ord(c) <= 32 or ord(c) == 127 for c in value), 'INVALID_EVIDENCE_URL')
    try:
        u = urlsplit(value)
        require(u.scheme == 'https' and u.hostname and not u.username and not u.password
                and u.port is None and not u.query, 'INVALID_EVIDENCE_URL')
    except ValueError:
        raise Error('INVALID_EVIDENCE_URL') from None
    return value


def issue_path(url):
    try:
        repo, iid = parse_issue_url(url)
    except ValueError:
        raise Error('INVALID_ISSUE_URL') from None
    return f'projects/{quote(repo, safe="")}/issues/{iid}'


def note_ref(url):
    https_url(url)
    u = urlsplit(url)
    m = re.fullmatch(r'/(.+)/-/(issues|work_items|merge_requests)/([1-9][0-9]*)', u.path)
    n = re.fullmatch(r'note_([1-9][0-9]*)', u.fragment)
    require(HOST and u.netloc == HOST and m and n, 'INVALID_NOTE_URL')
    # Validate the repository with the existing owner's parser as well.
    issue_path(f'https://{HOST}/{m[1]}/-/issues/{m[3]}')
    resource = 'merge_requests' if m[2] == 'merge_requests' else 'issues'
    return f'projects/{quote(m[1], safe="")}/{resource}/{m[3]}', int(n[1])


def block(schema, value):
    return f'<!-- {schema} -->\n```json\n{compact(value)}\n```\n'


def marker_lines(body, marker):
    return len(re.findall(r'^' + re.escape(marker) + r'$', body, re.MULTILINE)) if isinstance(body, str) else 0


def extract(body, schema):
    marker = f'<!-- {schema} -->'
    require(marker_lines(body, marker) == 1, 'INVALID_STRUCTURED_NOTE')
    m = re.search(r'^' + re.escape(marker) + r'\n```json\n([^\n]+)\n```(?:\n|$)', body, re.MULTILINE)
    require(m is not None, 'INVALID_STRUCTURED_NOTE')
    try:
        value = json.loads(m[1])
    except (ValueError, TypeError):
        raise Error('INVALID_STRUCTURED_JSON') from None
    require(isinstance(value, dict) and value.get('schema') == schema, 'INVALID_SCHEMA')
    return value


def validate_batch(value):
    require(isinstance(value, dict) and value.get('schema') == BATCH, 'INVALID_BATCH_SCHEMA')
    require(value.get('status') == 'success', 'DEPLOYMENT_NOT_SUCCESSFUL')
    for k in ('batch_id', 'deployment_id', 'environment', 'stage'):
        require(isinstance(value.get(k), str) and re.fullmatch(r'[A-Za-z0-9_.:/-]{1,200}', value[k]),
                'INVALID_BATCH_IDENTITY')
    require(re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', str(value.get('revision', ''))) is not None,
            'FULL_REVISION_REQUIRED')
    timestamp(value.get('deployed_at'))  # Provenance clock belongs to the deployment provider.
    items = value.get('items')
    require(isinstance(items, list) and 0 < len(items) <= 500, 'INVALID_BATCH_ITEMS')
    seen = set()
    for item in items:
        require(isinstance(item, dict), 'INVALID_ITEM')
        identity = issue_path(item.get('issue'))
        require(identity not in seen, 'DUPLICATE_ITEM'); seen.add(identity)
        require(positive(item.get('owner_id')) and type(item.get('required')) is bool, 'INVALID_ITEM_OWNER')
        scope = item.get('scope')
        require(isinstance(scope, list) and 0 < len(scope) <= 100 and all(text(s, 200) for s in scope)
                and len(set(scope)) == len(scope), 'INVALID_SCOPE')
        mrs = item.get('mrs')
        require(isinstance(mrs, list) and len(mrs) <= 30 and all(isinstance(m, dict) and
                positive(m.get('project_id')) and positive(m.get('iid')) for m in mrs), 'INVALID_MRS')
        require(len({(m['project_id'], m['iid']) for m in mrs}) == len(mrs), 'DUPLICATE_MR')
    return value


class Client:
    def __init__(self, binary='glab'):
        require(HOST is not None, 'PLATFORM_OWNER_UNVERIFIED')
        self.binary = shutil.which(binary)
        require(self.binary is not None, 'GLAB_NOT_FOUND')
        self.calls = 0
        self.client_wall_seconds = 0.0
        self.cache = {}

    def request(self, endpoint, method='GET', payload=None):
        argv = [self.binary, 'api', '--hostname', HOST, '--method', method, endpoint]
        if payload is not None:
            argv += ['-H', 'Content-Type: application/json', '--input', '-']
        started = time.monotonic()
        try:
            result = subprocess.run(argv, input=compact(payload) if payload is not None else None,
                                    text=True, capture_output=True, timeout=45)
            require(result.returncode == 0, 'WRITE_OUTCOME_UNKNOWN' if method != 'GET' else 'API_READ_FAILED')
            require(len(result.stdout.encode()) <= MAX_BYTES, 'API_RESPONSE_TOO_LARGE')
            return json.loads(result.stdout)
        except (OSError, subprocess.TimeoutExpired, ValueError):
            raise Error('WRITE_OUTCOME_UNKNOWN' if method != 'GET' else 'API_READ_FAILED') from None
        finally:
            self.calls += 1
            self.client_wall_seconds += time.monotonic() - started

    def get(self, endpoint):
        if endpoint not in self.cache:
            self.cache[endpoint] = self.request(endpoint)
        return self.cache[endpoint]

    def pages(self, endpoint):
        result = []
        for page in range(1, 51):
            values = self.request(f'{endpoint}?per_page=100&page={page}')
            require(isinstance(values, list) and len(values) <= 100, 'INVALID_PAGINATION')
            result.extend(values)
            if len(values) < 100:
                return result
        raise Error('PAGINATION_INCOMPLETE')


def note_ok(note, author):
    require(isinstance(note, dict) and positive(note.get('id')) and not note.get('system')
            and isinstance(note.get('author'), dict) and note['author'].get('id') == author,
            'UNAUTHORIZED_NOTE_AUTHOR')
    require(timestamp(note.get('created_at')) == timestamp(note.get('updated_at')), 'EDITED_RECEIPT')


def private_json(path, value):
    require(not path.is_symlink(), 'UNSAFE_STATE_PATH')
    fd, name = tempfile.mkstemp(prefix='.acceptance-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(compact(value)); stream.flush(); os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextmanager
def journal(directory, actor, event):
    directory = Path(directory).expanduser().absolute()
    require(not any(p.is_symlink() for p in [directory, *directory.parents]), 'UNSAFE_STATE_PATH')
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    st = directory.stat()
    require(st.st_uid == os.getuid() and st.st_mode & 0o077 == 0, 'STATE_DIRECTORY_NOT_PRIVATE')
    path = directory / (digest([HOST, actor, event]) + '.json')
    lock = path.with_suffix('.lock')
    fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        require(os.fstat(fd).st_uid == os.getuid() and os.fstat(fd).st_mode & 0o077 == 0,
                'STATE_FILE_NOT_PRIVATE')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Error('REPORT_ALREADY_RUNNING') from None
        require(not path.is_symlink(), 'UNSAFE_STATE_PATH')
        if path.exists():
            require(path.stat().st_uid == os.getuid() and path.stat().st_mode & 0o077 == 0
                    and path.stat().st_size <= MAX_BYTES, 'STATE_FILE_NOT_PRIVATE')
            state = json.loads(path.read_text(encoding='utf-8'))
        else:
            state = {'writes': {}}
        yield state, lambda: private_json(path, state), str(path)
    finally:
        os.close(fd)


def agent_context():
    session, source, harness = 'unknown', 'runtime did not expose a session ID', 'other'
    for key, name in [('CODEX_THREAD_ID', 'codex'), ('GROK_SESSION_ID', 'grok')]:
        if os.environ.get(key):
            session, source, harness = os.environ[key], key, name
            break
    context = {'harness': harness, 'session_id': session, 'session_source': source,
               'host': socket.gethostname(), 'os': sys.platform, 'cwd': str(Path.cwd()),
               'branch': 'n/a', 'head': 'n/a', 'dirty': 'unknown'}
    for key, args in [('branch', ['branch', '--show-current']), ('head', ['rev-parse', 'HEAD']),
                      ('dirty', ['status', '--porcelain'])]:
        try:
            r = subprocess.run(['git', *args], text=True, capture_output=True, timeout=5)
            if r.returncode == 0:
                context[key] = bool(r.stdout.strip()) if key == 'dirty' else r.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            pass
    return context


class NoteWriter:
    def __init__(self, client, actor):
        self.client, self.actor = client, actor

    def post_once(self, endpoint, marker, body, state, save):
        # GitLab removes terminal LF on Notes writes; preserve all other bytes.
        body = body.rstrip('\n')
        writes = state['writes']
        record = writes.get(endpoint)
        if isinstance(record, int):
            obj = self.client.request(f'{endpoint}/{record}')
            candidates = [obj]
        else:
            candidates = [n for n in self.client.pages(endpoint) if marker_lines(n.get('body'), marker)]
        require(len(candidates) <= 1, 'DUPLICATE_EVENT')
        if candidates:
            obj = candidates[0]
            require(obj.get('author', {}).get('id') == self.actor['id'] and obj.get('body') == body,
                    'EVENT_CONTENT_CONFLICT')
            note_ok(obj, self.actor['id'])
            writes[endpoint] = obj['id']; save()
            # A direct GET is mandatory even when reconciliation found a list entry.
            obj = self.client.request(f"{endpoint}/{obj['id']}")
        else:
            require(record != 'unknown', 'UNRESOLVED_WRITE_OUTCOME')
            writes[endpoint] = 'unknown'; save()
            obj = self.client.request(endpoint, 'POST', {'body': body})
            require(positive(obj.get('id')), 'WRITE_OUTCOME_UNKNOWN')
            writes[endpoint] = obj['id']; save()
            obj = self.client.request(f"{endpoint}/{obj['id']}")
        require(obj.get('author', {}).get('id') == self.actor['id'] and obj.get('body') == body,
                'WRITE_READBACK_MISMATCH')
        note_ok(obj, self.actor['id'])
        # Include known-ID retries: another host may have posted the same event meanwhile.
        visible = [n for n in self.client.pages(endpoint) if marker_lines(n.get('body'), marker)]
        require(len(visible) <= 1, 'DUPLICATE_EVENT')
        require(visible and visible[0].get('id') == obj['id'], 'WRITE_READBACK_NOT_VISIBLE')
        require(visible[0].get('body') == body and visible[0].get('author', {}).get('id') == self.actor['id'],
                'WRITE_READBACK_MISMATCH')
        note_ok(visible[0], self.actor['id'])
        return obj


class Service(NoteWriter):
    def __init__(self, client, source, publisher):
        self.client, self.source, self.publisher = client, source, publisher
        self.progress = {}
        require(positive(publisher), 'INVALID_PUBLISHER')
        path, note_id = note_ref(source)
        require('/issues/' in path, 'BATCH_MUST_BE_ON_ISSUE')
        self.source_path = path
        note = client.get(f'{path}/notes/{note_id}')
        note_ok(note, publisher)
        require(note['id'] == note_id, 'NOTE_IDENTITY_MISMATCH')
        self.batch = validate_batch(extract(note.get('body'), BATCH))
        self.source_created_at = timestamp(note['created_at'])
        self.batch_digest = digest(self.batch)
        self.actor = client.get('user')
        require(positive(self.actor.get('id')) and self.actor.get('state') == 'active', 'INVALID_CURRENT_USER')

    def item(self, url):
        candidates = [i for i in self.batch['items'] if issue_path(i['issue']) == issue_path(url)]
        require(len(candidates) == 1, 'ITEM_NOT_IN_BATCH')
        return candidates[0]

    def target(self, item):
        path = issue_path(item['issue'])
        obj = self.client.get(path)
        require(isinstance(obj, dict) and positive(obj.get('project_id')) and positive(obj.get('iid'))
                and issue_path(obj.get('web_url')) == path, 'ISSUE_IDENTITY_MISMATCH')
        return path, obj

    def mrs(self, item, path):
        if not item['mrs']:
            return []
        related = self.client.pages(f'{path}/related_merge_requests')
        result = []
        for wanted in item['mrs']:
            matches = [m for m in related if m.get('project_id') == wanted['project_id']
                       and m.get('iid') == wanted['iid']]
            require(len(matches) == 1 and matches[0].get('state') == 'merged', 'MR_BINDING_UNVERIFIED')
            m = matches[0]
            url = https_url(m.get('web_url'))
            ref, _ = note_ref(url + '#note_1')
            require('/merge_requests/' in ref, 'MR_BINDING_UNVERIFIED')
            result.append((f"projects/{m['project_id']}/merge_requests/{m['iid']}", url, ref))
        return result

    def decision(self, item, url):
        path, ident = note_ref(url)
        allowed = {issue_path(item['issue'])}
        allowed.update(m[2] for m in self.mrs(item, issue_path(item['issue'])))
        require(path in allowed, 'WAIVER_OUTSIDE_ITEM')
        decision = self.client.request(f'{path}/notes/{ident}')
        require(decision.get('id') == ident and not decision.get('system')
                and decision.get('author', {}).get('id') == item['owner_id']
                and text(decision.get('body'), MAX_BYTES)
                and not marker_lines(decision['body'], f'<!-- {RECEIPT} -->'),
                'WAIVER_OWNER_DECISION_UNVERIFIED')
        return digest({k: decision[k] for k in ('id', 'body', 'author', 'created_at', 'updated_at')})

    def validate_receipt(self, data, item, verify_decision=True):
        require(isinstance(data, dict) and data.get('schema') == RECEIPT, 'INVALID_RECEIPT_SCHEMA')
        require(data.get('batch_note') == self.source and data.get('batch_digest') == self.batch_digest,
                'BATCH_MISMATCH')
        require(data.get('issue') == item['issue'] and data.get('owner_id') == item['owner_id']
                and data.get('scope') == item['scope'], 'RECEIPT_SCOPE_MISMATCH')
        require(data.get('result') in RESULTS and text(data.get('reason')), 'INVALID_RESULT')
        try:
            uuid.UUID(data.get('event_id', ''))
        except (ValueError, TypeError, AttributeError):
            raise Error('INVALID_EVENT_ID') from None
        evidence, remaining = data.get('evidence'), data.get('remaining')
        require(isinstance(evidence, list) and len(evidence) <= 30 and isinstance(remaining, list)
                and len(remaining) <= 100 and all(text(x) for x in remaining), 'INVALID_RESULT_DETAILS')
        for url in evidence:
            https_url(url)
        if data['result'] == 'passed':
            require(evidence and not remaining, 'PASS_REQUIRES_EVIDENCE_AND_COMPLETE_SCOPE')
        if data['result'] in ('partial', 'blocked', 'failed'):
            require(remaining, 'REMAINING_SCOPE_REQUIRED')
        if data['result'] == 'na':
            require(not remaining and data.get('decision_note'), 'WAIVER_DECISION_REQUIRED')
            require(isinstance(data.get('decision_digest'), str) and
                    re.fullmatch(r'[0-9a-f]{64}', data['decision_digest']), 'WAIVER_DECISION_REQUIRED')
            if verify_decision:
                require(self.decision(item, data['decision_note']) == data['decision_digest'],
                        'WAIVER_DECISION_CHANGED')
        timestamp(data.get('reported_at'))  # Client time is informational, not server ordering evidence.

    def receipts(self, item, path):
        result, events = [], set()
        for note in self.client.pages(f'{path}/notes'):
            if note.get('system') or not marker_lines(note.get('body'), f'<!-- {RECEIPT} -->'):
                continue
            data = extract(note['body'], RECEIPT)
            if data.get('batch_note') != self.source:
                continue
            note_ok(note, item['owner_id'])
            self.validate_receipt(data, item, verify_decision=False)
            require(data['event_id'] not in events, 'DUPLICATE_EVENT')
            events.add(data['event_id'])
            require(timestamp(note['created_at']) >= self.source_created_at, 'RECEIPT_BEFORE_BATCH')
            result.append((note, data))
        return sorted(result, key=lambda pair: (timestamp(pair[0]['created_at']), pair[0]['id']))

    def summarize(self, owner=None):
        results = []
        for item in self.batch['items']:
            if owner is not None and item['owner_id'] != owner:
                continue
            path, _ = self.target(item)
            self.mrs(item, path)
            records = self.receipts(item, path)
            note, data = records[-1] if records else (None, None)
            if data:
                self.validate_receipt(data, item)
            results.append({'issue': item['issue'], 'owner_id': item['owner_id'],
                            'required': item['required'], 'scope': item['scope'],
                            'result': data['result'] if data else 'pending',
                            'receipt': f"{item['issue']}#note_{note['id']}" if note else None,
                            'remaining': data['remaining'] if data else item['scope']})
        return results

    def check(self, expected):
        source_path, source_id = note_ref(self.source)
        fresh = self.client.request(f'{source_path}/notes/{source_id}')
        note_ok(fresh, self.publisher)
        require(fresh['id'] == source_id and digest(validate_batch(extract(fresh['body'], BATCH)))
                == self.batch_digest, 'BATCH_CHANGED')
        for key in ('deployment_id', 'environment', 'stage', 'revision'):
            require(expected.get(key) == self.batch[key], 'ACTUAL_DEPLOYMENT_MISMATCH')
        items = self.summarize()
        required = [i for i in items if i['required']]
        require(required, 'NO_REQUIRED_ITEMS')
        blockers = [i for i in required if i['result'] not in ('passed', 'na')]
        return {'verdict': 'ACCEPTANCE_SATISFIED' if not blockers else 'ACCEPTANCE_BLOCKED',
                'items': items, 'blockers': blockers, 'authorizes_deployment': False}

    def report(self, url, details, state_dir):
        item = self.item(url)
        require(self.actor['id'] == item['owner_id'], 'REPORTER_IS_NOT_OWNER')
        path, _ = self.target(item)
        mrs = self.mrs(item, path)
        require(set(details) == {'result', 'reason', 'evidence', 'remaining', 'decision_note', 'event_id'},
                'INVALID_REPORT_FIELDS')
        semantic = {'schema': RECEIPT, 'batch_note': self.source, 'batch_digest': self.batch_digest,
                    'issue': item['issue'], 'owner_id': item['owner_id'], 'scope': item['scope'], **details,
                    'decision_digest': self.decision(item, details['decision_note'])
                    if details['result'] == 'na' and details['decision_note'] else None}
        with journal(state_dir, self.actor['id'], details['event_id']) as (state, persist, state_path):
            self.progress = {'state_file': state_path, 'issue': item['issue'],
                             'issue_receipt_verified': False, 'mr_references_verified': 0,
                             'next_step': 'inspect_journal_and_reconcile_same_event; never replay unknown writes'}
            allowed = {f'{path}/notes', *(f'{m[0]}/notes' for m in mrs)}
            def save():
                persist()
                self.progress['writes'] = [
                    {'target': endpoint, 'note_id': value if positive(value) else None,
                     'state': 'response_received' if positive(value) else 'outcome_unknown'}
                    for endpoint, value in state['writes'].items() if endpoint in allowed]
            require('semantic' not in state or state['semantic'] == semantic, 'EVENT_CONTENT_CONFLICT')
            records = self.receipts(item, path)
            same_notes = [(n, d) for n, d in records if d['event_id'] == details['event_id']]
            same = [d for _, d in same_notes]
            if same:
                previous = {k: v for k, v in same[0].items() if k not in ('reported_at', 'agent')}
                require(previous == semantic, 'EVENT_CONTENT_CONFLICT')
            receipt = state.get('receipt') or (same[0] if same else {
                **semantic, 'reported_at': datetime.now(timezone.utc).isoformat(), 'agent': agent_context()})
            self.validate_receipt(receipt, item)
            state.update(semantic=semantic, receipt=receipt); save()
            marker = f"<!-- acceptance-event:{details['event_id']} -->"
            body = '## 部署验收回执\n\n' + marker + '\n' + block(RECEIPT, receipt)
            body += ('\n本回执仅描述所绑定批次的验收结论；免验不代表测试通过，不构成发布授权。\n'
                     '\n### Agent 操作与恢复信息\n'
                     f"- operation / event id / time: report / {receipt['event_id']} / {receipt['reported_at']}\n"
                     f"- context: {compact(receipt['agent'])}\n"
                     f'- resume / handoff: 当前 Issue、event ID 与执行主机私有验收 journal；state {state_path}\n'
                     '- status: released（仅本次验收回写）\n'
                     f"- next / owner: 发布方核对本阶段全部必需项 / user {item['owner_id']}\n")
            # Keep the original body on retries, including its original recovery path.
            state.setdefault('body', same_notes[0][0]['body'] if same_notes else body)
            require(extract(state['body'], RECEIPT) == receipt and marker_lines(state['body'], marker) == 1,
                    'RECEIPT_BODY_MISMATCH')
            save()
            note = self.post_once(f'{path}/notes', marker, state['body'], state, save)
            require(timestamp(note['created_at']) >= self.source_created_at, 'RECEIPT_BEFORE_BATCH')
            url = f"{item['issue']}#note_{note['id']}"
            self.progress.update(receipt=url, issue_receipt_verified=True)
            for endpoint, _, _ in mrs:
                pointer = f'{marker}\n本批部署验收以关联 Issue 回执为准：{url}\n批次：{self.batch["batch_id"]}\n'
                self.post_once(f'{endpoint}/notes', marker, pointer, state, save)
                self.progress['mr_references_verified'] += 1
            state['verified'] = True; save()
            return {'verdict': 'REPORT_VERIFIED', 'receipt': url, 'state_file': state_path,
                    'mr_references_verified': len(mrs), 'authorizes_deployment': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--glab', default='glab')
    parser.add_argument('--batch-note')
    parser.add_argument('--publisher-id', type=int,
                        help='Trusted publisher configured by release owner, never inferred from note text')
    sub = parser.add_subparsers(dest='command', required=True)
    pending = sub.add_parser('pending'); pending.add_argument('--assignee', default='@me')
    pending.add_argument('--issue')
    pending.add_argument('--stage')
    check = sub.add_parser('check')
    for key in ('deployment-id', 'environment', 'stage', 'revision'):
        check.add_argument('--expect-' + key, required=True)
    report = sub.add_parser('report')
    report.add_argument('--issue', required=True)
    report.add_argument('--result', choices=RESULTS, required=True)
    report.add_argument('--stage')
    report.add_argument('--environment')
    report.add_argument('--version')
    report.add_argument('--revision')
    report.add_argument('--scope', action='append', default=[])
    report.add_argument('--mr', action='append', default=[])
    report.add_argument('--confirmation')
    report.add_argument('--reason', required=True)
    report.add_argument('--evidence', action='append', default=[])
    report.add_argument('--remaining', action='append', default=[])
    report.add_argument('--decision-note')
    report.add_argument('--event-id', required=True, help='Persist a UUID before calling; reuse it on retries')
    report.add_argument('--state-dir', default=str(Path.home() / '.local/state/addx/acceptance'))
    args = parser.parse_args(argv)
    client, service = None, None
    try:
        client = Client(args.glab)
        bound = bool(args.batch_note or args.publisher_id is not None)
        if args.command == 'check' or bound:
            require(args.batch_note and positive(args.publisher_id), 'BATCH_CONTEXT_REQUIRED')
        if bound and args.command == 'report':
            require(not (any(v is not None for v in (args.stage, args.environment, args.version,
                                                    args.revision, args.confirmation)) or args.scope or args.mr),
                    'OWNER_OPTIONS_REQUIRE_INDEPENDENT_MODE')
        if bound and args.command == 'pending':
            require(args.issue is None and args.stage is None, 'OWNER_OPTIONS_REQUIRE_INDEPENDENT_MODE')
        if not bound and args.command != 'check':
            from owner_acceptance import OwnerReports
            service = OwnerReports(client, sys.modules[__name__])
            result = service.report(args) if args.command == 'report' else service.pending(args)
        else:
            service = Service(client, args.batch_note, args.publisher_id)
        if bound and args.command == 'pending':
            require(args.assignee == '@me' or args.assignee.isdecimal(), 'ASSIGNEE_ID_REQUIRED')
            owner = service.actor['id'] if args.assignee == '@me' else int(args.assignee)
            items = service.summarize(owner)
            result = {'verdict': 'PENDING_READ', 'items': [i for i in items if i['result'] not in ('passed', 'na')],
                      'authorizes_deployment': False}
        elif bound and args.command == 'check':
            result = service.check({k: getattr(args, 'expect_' + k)
                                    for k in ('deployment_id', 'environment', 'stage', 'revision')})
        elif bound:
            result = service.report(args.issue, {k: getattr(args, k) for k in
                                    ('result', 'reason', 'evidence', 'remaining', 'decision_note', 'event_id')},
                                    args.state_dir)
        if bound:
            result.update(batch_digest=service.batch_digest, deployment_fact='trusted_publisher_attestation')
        code = 1 if result['verdict'] == 'ACCEPTANCE_BLOCKED' else 0
    except (Error, OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        result = {'verdict': 'TOOL_FAILED', 'error': str(exc) if isinstance(exc, Error) else 'INPUT_OR_STATE_INVALID',
                  'authorizes_deployment': False}
        if service and service.progress:
            result['recovery'] = service.progress
        code = 2
    if client:
        result.update(api_calls=client.calls, glab_client_wall_seconds=round(client.client_wall_seconds, 3))
    print(compact(result))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
