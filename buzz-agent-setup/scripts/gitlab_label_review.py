"""Candidate deterministic stage-review gate (#359).

No credential loading, polling, network, label mutation, or model invocation lives
here. The shared sync/route adapter must supply fresh API-derived facts and verify
its existing signed publisher/Canvas/channel contract before calling this gate.
A label requests review; it never constitutes human approval.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import re
import sqlite3


class ReviewError(ValueError):
    """Input cannot safely authorize a review operation."""


def _positive(value):
    return type(value) is int and value > 0


def build_request(policy, issue, submission):
    """Bind a static reviewed policy to freshly read Issue + submission facts.

    submission is an adapter-produced record, NOT a parsed instruction from a
    comment. The adapter verifies note author, immutable source commit and full
    content digest from the source; this pure gate does not authenticate HTTP.
    """
    if not all(isinstance(x, dict) for x in (policy, issue, submission)):
        raise ReviewError('missing policy, Issue or submission readback')
    required = ('project_id','channel_id','scope','request_value','policy_revision',
                'reviewer_user_id','submitter_user_ids')
    if any(k not in policy for k in required):
        raise ReviewError('incomplete reviewed policy')
    for key in ('project_id','reviewer_user_id'):
        if not _positive(policy[key]): raise ReviewError('invalid policy identity')
    for key in ('channel_id','scope','request_value','policy_revision'):
        if not isinstance(policy[key],str) or not re.fullmatch(r'[a-zA-Z0-9_.-]{1,128}',policy[key]):
            raise ReviewError('invalid policy field')
    authors=policy['submitter_user_ids']
    if not isinstance(authors,list) or not authors or not all(_positive(x) for x in authors):
        raise ReviewError('invalid submitter allowlist')
    if issue.get('project_id') != policy['project_id'] or issue.get('state') != 'opened' or not _positive(issue.get('iid')):
        raise ReviewError('Issue is not open in the allowed project')
    labels=issue.get('labels')
    if not isinstance(labels,list) or not all(isinstance(x,str) for x in labels):
        raise ReviewError('missing structured labels')
    prefix=policy['scope']+'::'
    if [x for x in labels if x.startswith(prefix)] != [prefix+policy['request_value']]:
        raise ReviewError('review request label absent or conflicting')
    if (submission.get('project_id'),submission.get('issue_iid')) != (policy['project_id'],issue['iid']):
        raise ReviewError('submission belongs to another Issue')
    if not _positive(submission.get('note_id')) or submission.get('author_id') not in authors:
        raise ReviewError('submission author is not allowed')
    if submission['author_id'] == policy['reviewer_user_id']:
        raise ReviewError('independent reviewer required')
    for key,pattern in [('content_digest',r'[a-f0-9]{64}'),('source_commit',r'[a-f0-9]{40}'),('revision',r'[a-zA-Z0-9_.-]{1,128}')]:
        if not isinstance(submission.get(key),str) or not re.fullmatch(pattern,submission[key]):
            raise ReviewError('submission version or source fingerprint missing')
    record={k:policy[k] for k in ('project_id','channel_id','scope','request_value','policy_revision','reviewer_user_id')}
    record.update(issue_iid=issue['iid'],submission_note_id=submission['note_id'],
                  submitter_user_id=submission['author_id'],revision=submission['revision'],
                  content_digest=submission['content_digest'],source_commit=submission['source_commit'])
    key=hashlib.sha256(json.dumps(record,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return {**record,'task_key':key}


class Ledger:
    """Durable claims: a crashed/unknown write is never automatically replayed.

    The owner adapter reconciles pending claims against exact GitLab marker AND
    actual reviewer identity before record_result. This ledger alone does not
    close the API read/write race; final source reread is mandatory in the adapter.
    """
    def __init__(self,path):
        self.db=sqlite3.connect(Path(path),timeout=10)
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS reviews (task_key TEXT PRIMARY KEY, request TEXT NOT NULL, status TEXT NOT NULL, receipt TEXT)')
        self.db.commit()
    def __enter__(self): return self
    def __exit__(self,*args): self.db.close()
    def claim(self,request):
        key=request['task_key']
        with self.db:
            cur=self.db.execute('INSERT OR IGNORE INTO reviews VALUES (?,?,?,NULL)',
                                (key,json.dumps(request,sort_keys=True),'PENDING'))
            if cur.rowcount: return 'CLAIMED'
            row=self.db.execute('SELECT request,status FROM reviews WHERE task_key=?',(key,)).fetchone()
            if json.loads(row[0]) != request: raise ReviewError('claim payload changed')
            return {'PENDING':'RECONCILE_PENDING','RECORDED':'ALREADY_RECORDED','SUPERSEDED':'SUPERSEDED'}[row[1]]
    def record_result(self,request,current_request,receipt):
        if not isinstance(current_request, dict) or 'task_key' not in current_request:
            raise ReviewError('fresh current submission readback missing')
        if not isinstance(receipt,dict) or receipt.get('author_id') != request['reviewer_user_id']:
            raise ReviewError('result author is not the independent reviewer')
        if (not _positive(receipt.get('note_id')) or receipt.get('task_key') != request['task_key']
            or receipt.get('verdict') not in ('review-passed','changes-requested','blocked')):
            raise ReviewError('result readback is missing or attempts human approval')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row=self.db.execute('SELECT request,status,receipt FROM reviews WHERE task_key=?',(request['task_key'],)).fetchone()
            if not row or json.loads(row[0]) != request: raise ReviewError('no matching claim')
            if row[1] != 'PENDING':
                if row[2] and json.loads(row[2]) == receipt: return row[1]
                raise ReviewError('terminal result cannot be overwritten')
            status='RECORDED' if current_request == request else 'SUPERSEDED'
            self.db.execute('UPDATE reviews SET status=?,receipt=? WHERE task_key=?',
                            (status,json.dumps(receipt,sort_keys=True),request['task_key']))
            return status
