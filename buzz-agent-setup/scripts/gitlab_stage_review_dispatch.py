"""Opt-in stage review in the existing Desk Syncer, never a second poller.

All writes use Syncer's canonical thread, project gates and durable outbox.
The independent reviewer consumes the immutable request and writes its own result.
"""
from __future__ import annotations
import base64
import hashlib
import json
from pathlib import PurePosixPath, Path
import re
from urllib.parse import quote

import gitlab_label_review as kernel

MARKER = '<!-- stage-review-request:v1 '
SUBMISSION = re.compile(r'<!-- stage-review-submission:v1 (\{[^\n]*\}) -->')
REQUEST = re.compile(r'<!-- stage-review-request:v1 (\{[^\n]*\}) -->')
POLICY_KEYS = {'project_id', 'scope', 'request_value', 'policy_revision',
               'reviewer_user_id', 'submitter_user_ids', 'route_id', 'source_prefix'}


class StageError(ValueError):
    """No review wake is safe for the current source."""


class SupersededStage(StageError):
    """Authoritative readback revoked an unattempted request."""


def request_marker(request):
    # Reversible JSON escapes prevent Buzz CLI from auto-resolving mentions.
    encoded = json.dumps(request, sort_keys=True, separators=(',', ':'))
    encoded = re.sub(r'(?i)nostr:', lambda match: match[0][:-1] + r'\u003a', encoded)
    return MARKER + encoded.replace('@', r'\u0040') + ' -->'


def trigger_prefix(policy):
    return (f"[gitlab-stage-review:v1][scope:{policy['scope']}]"
            f"[value:{policy['request_value']}]")


def validate_config(config):
    """Strict owner configuration; role identities remain in shared route config."""
    value = config.get('stage_review')
    if value is None:
        return
    if not isinstance(value, dict) or set(value) != {'route_config', 'policies'}:
        raise StageError('stage_review requires route_config and policies')
    path = value['route_config']
    if not isinstance(path, str) or not Path(path).is_absolute() or '..' in Path(path).parts:
        raise StageError('stage review route config must be an absolute fixed path')
    policies = value['policies']
    if not isinstance(policies, list) or not 1 <= len(policies) <= 16:
        raise StageError('stage review requires 1..16 policies')
    seen = set()
    for policy in policies:
        if not isinstance(policy, dict) or set(policy) != POLICY_KEYS:
            raise StageError('invalid stage review policy keys')
        if policy['project_id'] not in config['gitlab']['projects']:
            raise StageError('stage project is outside sync scope')
        for key in ('scope', 'request_value', 'route_id', 'policy_revision'):
            if not isinstance(policy[key], str) or not re.fullmatch(r'[a-z0-9][a-z0-9.-]{0,63}', policy[key]):
                raise StageError('invalid stage review policy identifier')
        if not kernel._positive(policy['reviewer_user_id']):
            raise StageError('invalid reviewer identity')
        authors = policy['submitter_user_ids']
        if not isinstance(authors, list) or not authors or not all(kernel._positive(x) for x in authors):
            raise StageError('invalid submission author allowlist')
        prefix = policy['source_prefix']
        if not isinstance(prefix, str) or not prefix.endswith('/') or prefix.startswith('/') or '..' in PurePosixPath(prefix).parts:
            raise StageError('source_prefix must be a relative repository directory')
        identity = policy['project_id'], policy['scope']
        if identity in seen:
            raise StageError('duplicate stage review project/scope')
        seen.add(identity)


def read_request(gitlab, policy, channel, iid):
    """Read actual label events, author and immutable bytes; never trust claimed digest."""
    import gitlab_buzz_sync as sync
    project = policy['project_id']
    issue = gitlab.issue(project, iid)
    if not isinstance(issue, dict):
        raise StageError('Issue readback unavailable')
    notes = gitlab.notes(project, iid)
    candidates = []
    for note in notes:
        if note.get('system') or (note.get('author') or {}).get('id') not in policy['submitter_user_ids']:
            continue
        matches = SUBMISSION.findall(note.get('body') or '')
        if len(matches) > 1:
            raise StageError('submission note contains ambiguous manifests')
        if matches:
            candidates.append((note, matches[0]))
    if not candidates:
        raise StageError('no authorized versioned submission note')
    note, raw = max(candidates, key=lambda item: item[0]['id'])
    try:
        source = json.loads(raw)
    except (ValueError, TypeError):
        raise StageError('invalid submission manifest') from None
    if not isinstance(source, dict) or set(source) != {'revision', 'source_commit', 'source_path', 'content_digest'}:
        raise StageError('submission manifest must contain only immutable source coordinates')
    path = source['source_path']
    if (not isinstance(path, str) or not path.startswith(policy['source_prefix'])
            or path.startswith('/') or '..' in PurePosixPath(path).parts or '\\' in path
            or any(ord(char) < 32 or ord(char) == 127 for char in path)):
        raise StageError('source path is outside reviewed repository directory')
    submission = {**source, 'project_id': project, 'issue_iid': iid, 'note_id': note['id'],
                  'author_id': note['author']['id']}
    try:
        request = kernel.build_request({**policy, 'channel_id': channel}, issue, submission)
    except kernel.ReviewError as exc:
        raise StageError(str(exc)) from None
    events = gitlab.paged(f'projects/{project}/issues/{iid}/resource_label_events', {})
    scoped = [event for event in events if isinstance((event.get('label') or {}).get('name'), str)
              and event['label']['name'].startswith(policy['scope'] + '::')]
    wanted = policy['scope'] + '::' + policy['request_value']
    # Latest family event must be a request addition. Removals or later machine
    # transitions do not constitute a new request; unrelated-family edits do not count.
    if not scoped:
        raise StageError('no stage label transition evidence')
    ordered = sorted(scoped, key=lambda item: item['id'])
    requested = [event for event in ordered if event['label']['name'] == wanted]
    if not requested:
        raise StageError('no request-label transition evidence')
    event = requested[-1]
    old_labels = set()
    for earlier in ordered:
        if earlier['id'] >= event['id']:
            break
        name = earlier['label']['name']
        if earlier.get('action') == 'add':
            old_labels.add(name)
        elif earlier.get('action') == 'remove':
            old_labels.discard(name)
        else:
            raise StageError('unknown resource-label event action')
    if wanted in old_labels:
        raise StageError('request event did not change the label')
    if (event.get('action') != 'add' or event['label']['name'] != wanted
            or (event.get('user') or {}).get('id') not in policy['submitter_user_ids']):
        raise StageError('latest stage transition is not an authorized request')
    if sync.parse_timestamp(event.get('created_at'), 'label event') < sync.parse_timestamp(note.get('updated_at') or note.get('created_at'), 'submission note'):
        raise StageError('submission changed after request; resubmit the stage label')
    file, _ = gitlab.request('GET', f'projects/{project}/repository/files/{quote(path, safe="")}',
                            params={'ref': source['source_commit']})
    if (not isinstance(file, dict) or file.get('encoding') != 'base64'
            or file.get('file_path') != path or file.get('ref') != source['source_commit']
            or not isinstance(file.get('content'), str) or len(file['content']) > 2_000_000):
        raise StageError('immutable repository file readback mismatch')
    try:
        content = base64.b64decode(file['content'].replace('\n', ''), validate=True)
    except (ValueError, TypeError):
        raise StageError('invalid repository bytes') from None
    if hashlib.sha256(content).hexdigest() != source['content_digest']:
        raise StageError('actual repository digest differs from submission')
    # Path is part of the request identity, even if two files have identical bytes.
    request['source_path'] = path
    request['task_key'] = hashlib.sha256(json.dumps({k: v for k, v in request.items() if k != 'task_key'}, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return request, event['id']


def load_route(syncer, policy):
    import gitlab_buzz_route_reply as route
    import gitlab_buzz_sync as sync
    cfg = route.load_config(Path(syncer.config['stage_review']['route_config']), mode='scan')
    if set(cfg['channels']) != {syncer.channel} or cfg['sender_pubkey'] != syncer.publisher:
        raise StageError('stage route must use the existing channel and Desk identity')
    channel_rule = cfg['channels'][syncer.channel]
    if channel_rule['publisher_pubkey'] != syncer.publisher:
        raise StageError('stage route publisher differs from Syncer')
    raw = sync._collect_events(syncer.buzz.command([
        'messages', 'get', '--channel', syncer.channel, '--kinds', '40100',
        '--limit', str(route.MAX_CANVAS_EVENTS),
    ]))
    if len(raw) >= route.MAX_CANVAS_EVENTS:
        raise StageError('Canvas event window incomplete')
    trusted = route.select_canvas_policy(raw, syncer.channel, channel_rule)
    selected = trusted['routes'].get(policy['route_id'])
    if not selected or selected['trigger_prefix'] != trigger_prefix(policy):
        raise SupersededStage('current signed Canvas does not permit this stage review')
    return selected


def _policy(syncer, project, scope):
    matches = [p for p in syncer.config.get('stage_review', {}).get('policies', [])
               if p['project_id'] == project and p['scope'] == scope]
    if len(matches) != 1:
        raise SupersededStage('stage request has no unique current owner policy')
    return matches[0]


def recheck_delivery(syncer, payload):
    """Called by common outbox gate before both first send and unattempted replay."""
    matches = REQUEST.findall(payload.get('content') or '')
    if len(matches) != 1:
        raise StageError('stage delivery manifest missing or ambiguous')
    try:
        recorded = json.loads(matches[0])
        policy = _policy(syncer, recorded['project_id'], recorded['scope'])
    except SupersededStage:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise StageError('stage delivery source cannot be revalidated') from exc
    try:
        current, _ = read_request(syncer.gitlab, policy, syncer.channel, recorded['issue_iid'])
    except (StageError, kernel.ReviewError) as exc:
        raise SupersededStage('stage submission is no longer valid: ' + str(exc)) from None
    if current != recorded:
        raise SupersededStage('stage delivery superseded; do not wake old input')
    import gitlab_buzz_sync as sync
    current_issue = syncer.gitlab.issue(recorded['project_id'], recorded['issue_iid'])
    if not sync.issue_selected(current_issue, syncer.config)[0]:
        raise SupersededStage('stage Issue no longer selected for this channel')
    role = load_route(syncer, policy)
    if payload.get('mentions') != [role['mention_pubkey']]:
        raise SupersededStage('stage delivery recipient no longer matches signed policy')
    import gitlab_buzz_sync as sync
    root = sync.parse_binding(syncer.gitlab.notes(recorded['project_id'], recorded['issue_iid']),
                              syncer.bot_user_id, recorded['project_id'], 'issue', recorded['issue_iid'], syncer.channel)
    if not root or payload.get('reply_to') != root:
        raise SupersededStage('stage delivery no longer targets the canonical Issue thread')
    syncer._thread(root, recorded['project_id'], 'issue', recorded['issue_iid'])


def dispatch(syncer, project, iid, summary, dry_run):
    import gitlab_buzz_sync as sync
    policies = [p for p in syncer.config['stage_review']['policies'] if p['project_id'] == project]
    for policy in policies:
        issue = syncer.gitlab.issue(project, iid)
        if not sync.issue_selected(issue, syncer.config)[0]:
            continue
        labels = issue.get('labels') or []
        wanted = policy['scope'] + '::' + policy['request_value']
        if wanted not in labels:
            continue
        try:
            request, event_id = read_request(syncer.gitlab, policy, syncer.channel, iid)
            role = load_route(syncer, policy)
            root = sync.parse_binding(syncer.gitlab.notes(project, iid), syncer.bot_user_id,
                                      project, 'issue', iid, syncer.channel)
            if not root:
                if dry_run:
                    summary.setdefault('stage_review', []).append({'issue': iid, 'status': 'needs_binding'})
                    continue
                raise StageError('stage review needs the existing canonical Issue thread')
            events = syncer._thread(root, project, 'issue', iid)
            marker = request_marker(request)
            for event in events:
                if event.get('pubkey') == syncer.publisher and marker in (event.get('content') or '').splitlines():
                    sync.verify_nostr_event_signature(event, label='stage request deduplication')
                    if (['h', syncer.channel] not in event.get('tags', [])
                            or ['e', root, '', 'reply'] not in event.get('tags', [])
                            or ['p', role['mention_pubkey']] not in event.get('tags', [])):
                        raise StageError('existing stage request has conflicting channel/thread/recipient')
                    summary.setdefault('stage_review', []).append({'issue': iid, 'status': 'already_sent'})
                    break
            else:
                if dry_run:
                    summary.setdefault('stage_review', []).append({'issue': iid, 'status': 'would_send'})
                    continue
                fact = sync.issue_fact(issue, project)
                base = sync.render_message(fact, 'activity')
                detail = (
                    '已请求独立审查本轮需求说明，意见会写回同一需求单。'
                    '资料更新后需要重新检查；标签不代表人工接收或送印许可。'
                    f" 本轮资料：{sync.neutralize(request['source_path'])}。"
                )
                content = sync._notice([*sync.notice_body_lines(base), marker, detail], sync.header_line(base))
                syncer._send_message(content, reply_to=root, mentions=[role['mention_pubkey']], project_id=project)
                summary.setdefault('stage_review', []).append({'issue': iid, 'status': 'sent'})
        except (StageError, kernel.ReviewError) as exc:
            raise sync.ObjectError(f'issue {iid} stage review blocked: {exc}') from None
