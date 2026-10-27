"""Owner facts can be written immediately; applicability to a deployment is a separate consumer decision."""
from datetime import datetime, timezone
import uuid

SCHEMA = 'addx.acceptance.owner-report/v1'


class OwnerReports:
    def __init__(self, client, core):
        self.client, self.c = client, core
        self.actor = client.get('user')
        core.require(core.positive(self.actor.get('id')) and self.actor.get('state') == 'active',
                     'INVALID_CURRENT_USER')
        self.writer = core.NoteWriter(client, self.actor)
        self.progress = {}

    def context(self, url, mr_urls=(), write=False):
        c = self.c
        c.require(url, 'ISSUE_CONTEXT_REQUIRED')
        path = c.issue_path(url)
        issue = self.client.get(path)
        c.require(c.positive(issue.get('project_id')) and c.positive(issue.get('iid'))
                  and c.issue_path(issue.get('web_url')) == path, 'ISSUE_IDENTITY_MISMATCH')
        c.require(len(mr_urls) <= 30 and len(set(mr_urls)) == len(mr_urls), 'INVALID_MRS')
        related = self.client.pages(path + '/related_merge_requests') if mr_urls else []
        mrs, authored = [], False
        for mr_url in mr_urls:
            mr_path, _ = c.note_ref(mr_url + '#note_1')
            c.require('/merge_requests/' in mr_path, 'INVALID_MR_URL')
            m = self.client.get(mr_path)
            c.require(c.positive(m.get('project_id')) and c.positive(m.get('iid'))
                      and m.get('web_url') == mr_url, 'MR_IDENTITY_MISMATCH')
            c.require(sum(x.get('project_id') == m['project_id'] and x.get('iid') == m['iid']
                          for x in related) == 1, 'MR_BINDING_UNVERIFIED')
            authored = authored or m.get('author', {}).get('id') == self.actor['id']
            mrs.append({'project_id': m['project_id'], 'iid': m['iid'], 'url': mr_url, 'path': mr_path})
        assigned = any(x.get('id') == self.actor['id'] for x in issue.get('assignees', []))
        c.require(not write or assigned or authored, 'REPORTER_IS_NOT_OWNER')
        return path, issue['web_url'], mrs

    def validate(self, data):
        c = self.c
        c.require(data.get('schema') == SCHEMA and c.positive(data.get('owner_id')), 'INVALID_OWNER_RECORD')
        c.issue_path(data.get('issue'))
        c.require(data.get('result') in c.RESULTS and c.text(data.get('reason')), 'INVALID_RESULT')
        scope, evidence, remaining = data.get('scope'), data.get('evidence'), data.get('remaining')
        c.require(isinstance(scope, list) and 0 < len(scope) <= 100 and all(c.text(v, 200) for v in scope)
                  and len(set(scope)) == len(scope), 'INVALID_SCOPE')
        c.require(isinstance(evidence, list) and len(evidence) <= 30 and isinstance(remaining, list)
                  and len(remaining) <= 100 and all(c.text(v) for v in remaining), 'INVALID_RESULT_DETAILS')
        for url in evidence:
            c.https_url(url)
        for key in ('stage', 'environment', 'version'):
            c.require(data.get(key) is None or c.text(data[key], 200), 'INVALID_OWNER_CONTEXT')
        revision = data.get('revision')
        c.require(revision is None or isinstance(revision, str) and c.re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', revision),
                  'FULL_REVISION_REQUIRED')
        confirmation = data.get('owner_confirmation')
        c.require(confirmation is None or c.text(confirmation), 'INVALID_OWNER_CONFIRMATION')
        c.require(data.get('deployment_verified') is False and data.get('authorizes_deployment') is False,
                  'OWNER_RECORD_IS_NOT_DEPLOYMENT_PROOF')
        if data['result'] == 'passed':
            c.require(not remaining and (evidence or confirmation), 'PASS_REQUIRES_EVIDENCE_OR_CONFIRMATION')
        if data['result'] in ('partial', 'failed', 'blocked'):
            c.require(remaining, 'REMAINING_SCOPE_REQUIRED')
        if data['result'] == 'na':
            c.require(not remaining and (confirmation or data.get('decision_note')), 'WAIVER_DECISION_REQUIRED')
        try:
            uuid.UUID(data.get('event_id', ''))
        except (ValueError, TypeError, AttributeError):
            raise c.Error('INVALID_EVENT_ID') from None
        c.timestamp(data.get('reported_at'))

    def records(self, path):
        c = self.c
        records, events = [], set()
        for n in self.client.pages(path + '/notes'):
            if n.get('system') or not c.marker_lines(n.get('body'), '<!-- ' + SCHEMA + ' -->'):
                continue
            d = c.extract(n['body'], SCHEMA)
            self.validate(d)
            c.note_ok(n, d['owner_id'])
            c.require(c.issue_path(d['issue']) == path, 'ISSUE_IDENTITY_MISMATCH')
            event = (d['owner_id'], d['event_id'])
            c.require(event not in events, 'DUPLICATE_EVENT'); events.add(event)
            records.append((n, d))
        return sorted(records, key=lambda pair: (c.timestamp(pair[0]['created_at']), pair[0]['id']))

    def decision(self, url, path, mrs):
        c = self.c
        if not url:
            return None
        target, ident = c.note_ref(url)
        c.require(target in {path, *(m['path'] for m in mrs)}, 'WAIVER_OUTSIDE_ITEM')
        n = self.client.request(f'{target}/notes/{ident}')
        c.require(n.get('id') == ident and n.get('author', {}).get('id') == self.actor['id']
                  and not n.get('system') and c.text(n.get('body'), c.MAX_BYTES)
                  and not c.marker_lines(n['body'], '<!-- ' + SCHEMA + ' -->'), 'WAIVER_OWNER_DECISION_UNVERIFIED')
        return c.digest({k: n[k] for k in ('id', 'author', 'body', 'created_at', 'updated_at')})

    def report(self, args):
        c = self.c
        path, issue, mrs = self.context(args.issue, args.mr, write=True)
        semantic = {'schema': SCHEMA, 'issue': issue, 'owner_id': self.actor['id'],
                    'stage': args.stage, 'environment': args.environment, 'version': args.version,
                    'revision': args.revision, 'scope': args.scope, 'result': args.result,
                    'reason': args.reason, 'evidence': args.evidence, 'remaining': args.remaining,
                    'owner_confirmation': args.confirmation, 'decision_note': args.decision_note,
                    'decision_digest': self.decision(args.decision_note, path, mrs),
                    'mrs': [{k: m[k] for k in ('project_id', 'iid', 'url')} for m in mrs],
                    'event_id': args.event_id, 'deployment_verified': False, 'authorizes_deployment': False}
        with c.journal(args.state_dir, self.actor['id'], args.event_id) as (state, persist, state_path):
            self.progress = {'state_file': state_path, 'issue': issue, 'issue_receipt_verified': False,
                             'mr_references_verified': 0,
                             'next_step': 'reconcile_same_event; never replay unknown writes'}
            allowed = {path + '/notes', *(m['path'] + '/notes' for m in mrs)}
            def save():
                persist()
                self.progress['writes'] = [{'target': k, 'note_id': v if c.positive(v) else None,
                                           'state': 'response_received' if c.positive(v) else 'outcome_unknown'}
                                          for k, v in state['writes'].items() if k in allowed]
            c.require('semantic' not in state or state['semantic'] == semantic, 'EVENT_CONTENT_CONFLICT')
            same = [(n, d) for n, d in self.records(path) if d['owner_id'] == self.actor['id'] and d['event_id'] == args.event_id]
            if same:
                c.require({k: v for k, v in same[0][1].items() if k not in ('reported_at', 'agent')} == semantic,
                          'EVENT_CONTENT_CONFLICT')
            record = state.get('receipt') or (same[0][1] if same else {
                **semantic, 'reported_at': datetime.now(timezone.utc).isoformat(), 'agent': c.agent_context()})
            self.validate(record)
            state.update(semantic=semantic, receipt=record); save()
            marker = '<!-- acceptance-event:' + args.event_id + ' -->'
            body = ('## 负责人验收记录\n\n' + marker + '\n' + c.block(SCHEMA, record)
                    + '\n本记录为负责人已知验收事实；部署适用性由发布方另行核对，不构成发布授权。\n'
                    + '\n### Agent 操作与恢复信息\n'
                    + f"- operation / event id / time: owner report / {args.event_id} / {record['reported_at']}\n"
                    + '- context: ' + c.compact(record['agent']) + '\n'
                    + '- resume / handoff: 当前Issue、event及执行主机私有journal ' + state_path + '\n'
                    + '- status: released（仅本次事实回写）\n'
                    + '- next / owner: 发布方消费并核对版本适用性 / user ' + str(self.actor['id']) + '\n')
            state.setdefault('body', same[0][0]['body'] if same else body)
            c.require(c.extract(state['body'], SCHEMA) == record and c.marker_lines(state['body'], marker) == 1,
                      'RECEIPT_BODY_MISMATCH'); save()
            n = self.writer.post_once(path + '/notes', marker, state['body'], state, save)
            receipt = issue + '#note_' + str(n['id'])
            self.progress.update(receipt=receipt, issue_receipt_verified=True)
            for m in mrs:
                self.writer.post_once(m['path'] + '/notes', marker,
                                      marker + '\n负责人验收事实以关联Issue为准：' + receipt, state, save)
                self.progress['mr_references_verified'] += 1
            state['verified'] = True; save()
            missing = [key for key in ('stage', 'environment') if not record[key]]
            if not record['version'] and not record['revision']:
                missing.append('version_or_revision')
            if not record['revision']:
                missing.append('runtime_revision')
            if not record['evidence']:
                missing.append('evidence_link')
            return {'verdict': 'REPORT_VERIFIED', 'receipt': receipt, 'state_file': state_path,
                    'mr_references_verified': len(mrs), 'record_origin': 'owner',
                    'missing_context': missing, 'deployment_verified': False, 'authorizes_deployment': False}

    def owner_verified(self, path, record, associations):
        owner = record['owner_id']
        issue = self.client.get(path)
        if any(a.get('id') == owner for a in issue.get('assignees', [])):
            return True
        mrs = record.get('mrs')
        if not isinstance(mrs, list) or not mrs:
            return False
        if path not in associations:
            associations[path] = self.client.pages(path + '/related_merge_requests')
        related = associations[path]
        for selected in mrs:
            if not isinstance(selected, dict):
                continue
            matches = [m for m in related if m.get('project_id') == selected.get('project_id')
                       and m.get('iid') == selected.get('iid') and m.get('web_url') == selected.get('url')]
            if len(matches) != 1:
                continue
            target, _ = self.c.note_ref(matches[0]['web_url'] + '#note_1')
            native = self.client.get(target)
            if (native.get('project_id') == selected['project_id'] and native.get('iid') == selected['iid']
                    and native.get('author', {}).get('id') == owner):
                return True
        return False

    def pending(self, args):
        c = self.c
        path, issue, _ = self.context(args.issue)
        c.require(args.assignee == '@me' or args.assignee.isdecimal() and int(args.assignee) > 0,
                  'ASSIGNEE_ID_REQUIRED')
        owner = self.actor['id'] if args.assignee == '@me' else int(args.assignee)
        latest, unverified, associations = {}, [], {}
        for n, d in self.records(path):
            if d['owner_id'] != owner or args.stage and d['stage'] != args.stage:
                continue
            if not self.owner_verified(path, d, associations):
                unverified.append({'receipt': issue + '#note_' + str(n['id']), 'owner_id': owner,
                                   'reported_result': d['result'], 'reason': 'OWNER_ROLE_UNVERIFIED'})
                continue
            key = c.compact([d['stage'], d['environment'], d['version'], d['revision'], d['scope']])
            latest[key] = {'issue': issue, 'owner_id': owner, 'stage': d['stage'], 'environment': d['environment'],
                           'version': d['version'], 'revision': d['revision'], 'scope': d['scope'],
                           'result': d['result'], 'remaining': d['remaining'], 'receipt': issue + '#note_' + str(n['id'])}
        return {'verdict': 'OWNER_STATUS_READ', 'items': list(latest.values()) or [
            {'issue': issue, 'owner_id': owner, 'result': 'not-recorded', 'receipt': None}],
            'record_origin': 'owner', 'unverified_records': unverified,
            'deployment_verified': False, 'authorizes_deployment': False}
