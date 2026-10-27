"""Reviewer-owned stage result publisher; invoked by the existing agent, no poller.

The agent reasons about the immutable brief, then supplies a structured result.
This CLI validates current signed routing and source before claiming/publishing.
It writes version-bound notes only; labels and human approval are never mutated.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
from types import SimpleNamespace
import gitlab_buzz_sync as sync
import gitlab_label_review as kernel
import gitlab_stage_review_dispatch as dispatch


class WorkerError(ValueError):
    pass


class SupersededRequest(WorkerError):
    def __init__(self, current):
        super().__init__('request is no longer the current source version')
        self.current = current


def current_request(config, gitlab, buzz, event):
    adapter = config['adapter']
    reviewer = adapter['publisher_pubkey']
    sync.verify_nostr_event_signature(event, label='stage review request')
    if (event.get('kind') != 9 or event.get('pubkey') != config['desk_pubkey']
            or [t[:2] for t in sync._tag_values(event, 'h')] != [['h', adapter['channel_id']]]
            or [t[:2] for t in sync._tag_values(event, 'p')] != [['p', reviewer]]):
        raise WorkerError('request sender, channel or independent recipient mismatch')
    matches = dispatch.REQUEST.findall(event.get('content') or '')
    if len(matches) != 1:
        raise WorkerError('exactly one stage request manifest required')
    requested = json.loads(matches[0])
    ctx = SimpleNamespace(config=adapter, channel=adapter['channel_id'],
                          publisher=config['desk_pubkey'], buzz=buzz)
    policy = dispatch._policy(ctx, requested['project_id'], requested['scope'])
    if policy['reviewer_user_id'] != adapter['gitlab']['bot_user_id']:
        raise WorkerError('policy reviewer differs from this runtime principal')
    role = dispatch.load_route(ctx, policy)
    if role['mention_pubkey'] != reviewer:
        raise WorkerError('current signed route does not select this reviewer')
    identity, _ = gitlab.request('GET', 'user')
    if (identity.get('id') != policy['reviewer_user_id']
            or identity.get('username') != adapter['gitlab']['bot_username']):
        raise WorkerError('actual GitLab principal is not the configured reviewer')
    current, _ = dispatch.read_request(gitlab, policy, adapter['channel_id'], requested['issue_iid'])
    if current != requested:
        raise SupersededRequest(current)
    issue = gitlab.issue(current['project_id'], current['issue_iid'])
    if not sync.issue_selected(issue, adapter)[0]:
        raise WorkerError('Issue is not selected by the current channel contract')
    root = sync.parse_binding(gitlab.notes(current['project_id'], current['issue_iid']),
        config['desk_user_id'], current['project_id'], 'issue', current['issue_iid'], adapter['channel_id'])
    replies = [t for t in sync._tag_values(event, 'e') if len(t) >= 4 and t[3] == 'reply']
    if not root or len(replies) != 1 or replies[0][1] != root:
        raise WorkerError('request is not in the canonical Issue thread')
    return current


def result_body(request, result):
    if not isinstance(result, dict) or set(result) != {'task_key', 'verdict', 'summary', 'findings'}:
        raise WorkerError('result must contain task_key, verdict, summary and findings')
    if result['task_key'] != request['task_key'] or result['verdict'] not in ('review-passed', 'changes-requested', 'blocked'):
        raise WorkerError('wrong task or forbidden approval verdict')
    def text(value):
        if not isinstance(value, str) or not value.strip() or len(value) > 4000:
            raise WorkerError('finding text missing or too long')
        if '<!--' in value or any(ord(c) < 32 and c not in '\n\t' for c in value):
            raise WorkerError('finding text contains machine markers or control characters')
        return sync.neutralize(value)
    findings = result['findings']
    if not isinstance(findings, list) or len(findings) > 30:
        raise WorkerError('findings must be a bounded list')
    if result['verdict'] != 'review-passed' and not findings:
        raise WorkerError('rejection or block requires actionable findings')
    verdict_label = {'review-passed': '本轮检查通过', 'changes-requested': '需要修改',
                     'blocked': '暂不能继续'}[result['verdict']]
    lines = [f"## 设计阶段审查：{verdict_label}", text(result['summary']),
             '结论仅适用于本轮需求说明对应的版本；不代表人工批准或送印许可。']
    for finding in findings:
        if not isinstance(finding, dict) or set(finding) != {'severity', 'location', 'rule', 'evidence', 'action'}:
            raise WorkerError('finding requires severity, location, rule, evidence and action')
        if finding['severity'] not in ('blocking', 'suggestion'):
            raise WorkerError('unsupported finding severity')
        if result['verdict'] == 'review-passed' and finding['severity'] == 'blocking':
            raise WorkerError('blocking finding cannot pass')
        severity_label = {'blocking': '需要修改', 'suggestion': '建议优化'}[finding['severity']]
        lines += [f"### {severity_label} · {text(finding['location'])}",
                  '规则：' + text(finding['rule']), '证据：' + text(finding['evidence']),
                  '建议修改：' + text(finding['action'])]
    lines += ['<details><summary>版本与检查范围</summary>',
              f"版本：{request['revision']}；commit：{request['source_commit']}；digest：{request['content_digest']}",
              '</details>']
    marker = {'task_key': request['task_key'], 'verdict': result['verdict']}
    lines.append('<!-- stage-review-result:v1 ' + json.dumps(marker, sort_keys=True) + ' -->')
    return '\n\n'.join(lines)


def publish(config, gitlab, buzz, event, result, ledger):
    request = current_request(config, gitlab, buzz, event)
    body = result_body(request, result)
    status = ledger.claim(request)
    if status in ('ALREADY_RECORDED', 'SUPERSEDED'):
        return {'status': status, 'task_key': request['task_key']}
    notes = gitlab.notes(request['project_id'], request['issue_iid'])
    matches = [note for note in notes if not note.get('system') and note.get('body') == body
               and (note.get('author') or {}).get('id') == request['reviewer_user_id']]
    if len(matches) > 1:
        raise WorkerError('duplicate result notes require manual reconciliation')
    if matches:
        note_id = matches[0]['id']
    elif status == 'RECONCILE_PENDING':
        raise WorkerError('unknown prior attempt: reconcile, never automatically resend')
    else:
        # Fresh source and route check immediately before the sole external write.
        if current_request(config, gitlab, buzz, event) != request:
            raise WorkerError('source changed before publication')
        note_id = gitlab.add_note(request['project_id'], request['issue_iid'], body)
        # The shared client verifies its own actual author and exact body on POST readback.
    readback = gitlab.notes(request['project_id'], request['issue_iid'])
    if not any(note.get('id') == note_id and note.get('body') == body
               and (note.get('author') or {}).get('id') == request['reviewer_user_id']
               and not note.get('system') for note in readback):
        raise WorkerError('result author/body readback missing; reconcile before retry')
    receipt = dict(note_id=note_id, author_id=request['reviewer_user_id'],
                   task_key=request['task_key'], verdict=result['verdict'])
    # A concurrent update may occur after POST; the note remains explicitly version-bound.
    try:
        current = current_request(config, gitlab, buzz, event)
    except SupersededRequest as changed:
        current = changed.current
    state = ledger.record_result(request, current, receipt)
    return {'status': state, **receipt}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--event', type=Path, required=True)
    parser.add_argument('--result', type=Path, help='Omit to inspect the authenticated current request without writes')
    args = parser.parse_args()
    config = sync._read_owner_only_json(args.config, 'reviewer config')
    if set(config) != {'adapter', 'desk_pubkey', 'desk_user_id', 'state_dir'}:
        raise WorkerError('unknown reviewer configuration keys')
    sync.validate_config(config['adapter'])
    if (not sync.HEX64_RE.fullmatch(str(config['desk_pubkey']))
            or not kernel._positive(config['desk_user_id'])
            or config['desk_user_id'] == config['adapter']['gitlab']['bot_user_id']
            or config['desk_pubkey'] == config['adapter']['publisher_pubkey']):
        raise WorkerError('independent Desk/Reviewer identities required')
    sync.validate_publisher_identity(dict(os.environ), config['adapter']['publisher_pubkey'])
    gitlab = sync.GitLabClient(config['adapter'], dict(os.environ))
    buzz = sync.BuzzCli(config['adapter'], dict(os.environ))
    event = sync._read_owner_only_json(args.event, 'signed request event')
    if args.result is None:
        output = current_request(config, gitlab, buzz, event)
    else:
        result = sync._read_owner_only_json(args.result, 'reviewer result')
        state = Path(config['state_dir'])
        sync.prepare_private_dir(state, 'reviewer ledger')
        os.umask(0o077)
        ledger_path = state / 'stage-reviews.sqlite3'
        if ledger_path.exists() and (ledger_path.is_symlink() or ledger_path.stat().st_uid != os.geteuid()
                                    or ledger_path.stat().st_mode & 0o077):
            raise WorkerError('ledger must be an owner-only regular file')
        with kernel.Ledger(ledger_path) as ledger:
            output = publish(config, gitlab, buzz, event, result, ledger)
    print(json.dumps(output, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except (WorkerError, kernel.ReviewError, dispatch.StageError, sync.SyncError, KeyError, TypeError) as error:
        # Do not echo exception text that may include upstream response data or credentials.
        print(json.dumps({'status': 'blocked', 'error_type': type(error).__name__}))
        raise SystemExit(1)
