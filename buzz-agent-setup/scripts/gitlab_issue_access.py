#!/usr/bin/env python3
"""Owner-granted Issue operations using an agent's existing project identity.

This separate capability does not enable the general multi-project write
wrapper. It has no arbitrary endpoint, method, credential, or host argument.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.request

from gitlab_agent_project_tokens import load_mapping, mapping_sha256, mapping_to_public_dict
from gitlab_l4_receipt import _secure_json, canonical_json_sha256
from gitlab_project_token import NoRedirect

GRANT_ENV = 'BUZZ_GITLAB_ISSUE_GRANT'
PROVISION_ENV = 'BUZZ_GITLAB_ISSUE_PROVISIONING_RECEIPT'
ACTIONS = ('create', 'comment', 'update', 'close', 'reopen')


class IssueAccessError(ValueError):
    pass


def fail():
    raise IssueAccessError('Issue permission or operation could not be verified; no automatic retry is authorized')


def positive(value):
    return type(value) is int and 0 < value < 2**63


def operation(action, issue, data):
    if action not in ACTIONS or not isinstance(data, dict): fail()
    # GitLab parses quick actions inside Issue descriptions and comments.
    # Reject conservatively, including fenced code, before any network I/O.
    for field in ('description', 'body'):
        value = data.get(field)
        if isinstance(value, str) and any(line.lstrip().startswith('/') for line in value.splitlines()): fail()
    if action == 'create':
        if issue is not None or set(data) != {'title', 'description'}: fail()
        if not isinstance(data['title'], str) or not 1 <= len(data['title'].strip()) <= 255: fail()
        if not isinstance(data['description'], str) or len(data['description']) > 100000: fail()
        return 'POST', '/issues', data
    if not positive(issue): fail()
    if action == 'comment':
        if set(data) != {'body'} or not isinstance(data['body'], str) or not 1 <= len(data['body']) <= 100000: fail()
        return 'POST', f'/issues/{issue}/notes', data
    if action in ('close', 'reopen'):
        if data: fail()
        return 'PUT', f'/issues/{issue}', {'state_event': action}
    if not data or set(data) - {'title', 'description'}: fail()
    if any(not isinstance(v, str) or len(v) > (255 if k == 'title' else 100000)
           or (k == 'title' and not v.strip()) for k, v in data.items()): fail()
    return 'PUT', f'/issues/{issue}', data


class IssueAccess:
    def __init__(self, project, *, environ=None, opener=None):
        self.env = os.environ if environ is None else environ
        self.mapping = load_mapping(self.env['BUZZ_GITLAB_PROJECT_TOKEN_MAP'])
        self.entry = self.mapping.select(project_id=project)
        if self.entry.profile not in ('planner', 'reporter'): fail()
        self.token = self.mapping.token_values(self.env)[self.entry.token_env]
        self.opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        self.base = f'https://{self.mapping.host}/api/v4'
        self._write_permit = None

    def _grant(self):
        grant = _secure_json(Path(self.env[GRANT_ENV]), 'Issue grant')
        provision = _secure_json(Path(self.env[PROVISION_ENV]), 'Issue provisioning receipt')
        if set(grant) != {'schema', 'status', 'map_sha256', 'helper_sha256',
                         'provisioning_sha256', 'actions', 'projects', 'authorization_ref'}: fail()
        if (grant['schema'] != 'agent-issue-access-v1' or grant['status'] != 'verified'
                or grant['map_sha256'] != mapping_sha256(self.mapping)
                or grant['helper_sha256'] != hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
                or grant['provisioning_sha256'] != canonical_json_sha256(provision)
                or grant['actions'] != list(ACTIONS)
                or not isinstance(grant['authorization_ref'], str) or not grant['authorization_ref']): fail()
        if (provision.get('schema_version') != '2.0' or provision.get('secret_material_in_receipt') is not False
                or provision.get('mapping') != mapping_to_public_dict(self.mapping)): fail()
        tokens = provision.get('tokens'); projects = grant['projects']
        if not isinstance(tokens, list) or not isinstance(projects, list): fail()
        expected = {e.project_id for e in self.mapping.projects}
        if (len(tokens) != len(expected) or len(projects) != len(expected)
                or any(not isinstance(v, dict) or not positive(v.get('project_id')) for v in [*tokens, *projects])
                or {v['project_id'] for v in tokens} != expected or {v['project_id'] for v in projects} != expected): fail()
        for entry in self.mapping.projects:
            token = next(t for t in tokens if t['project_id'] == entry.project_id)
            proof = next(t for t in projects if t['project_id'] == entry.project_id)
            if (set(proof) != {'project_id', 'bot_user_id', 'token_id', 'issue_iid', 'note_id', 'actions', 'verified_at'}
                    or token.get('project_path') != entry.project_path or token.get('profile') != entry.profile
                    or token.get('bot_external') is not True or token.get('membership_project_ids') != [entry.project_id]
                    or token.get('access_level') != {'planner': 15, 'reporter': 20}.get(entry.profile)
                    or 'api' not in token.get('scopes', [])
                    or any(not positive(proof.get(k)) for k in ('bot_user_id', 'token_id', 'issue_iid', 'note_id'))
                    or proof['bot_user_id'] != token.get('bot_user_id') or proof['token_id'] != token.get('token_id')
                    or proof['actions'] != list(ACTIONS)): fail()
            verified = dt.datetime.fromisoformat(proof['verified_at'])
            if verified.tzinfo is None or verified > dt.datetime.now(dt.timezone.utc): fail()
        return next(t for t in tokens if t['project_id'] == self.entry.project_id)

    def _http(self, path, method='GET', payload=None):
        prefix = f'/projects/{self.entry.project_id}'
        if path not in ('/user', '/personal_access_tokens/self', '/projects?membership=true&per_page=2&page=1', prefix) and not re.fullmatch(
                re.escape(prefix) + r'/issues(?:/[1-9][0-9]*(?:/notes(?:/[1-9][0-9]*)?)?)?', path): fail()
        if method != 'GET':
            permit, self._write_permit = self._write_permit, None
            if permit != (method, path, canonical_json_sha256(payload)): fail()
        body = None if payload is None else json.dumps(payload).encode()
        req = urllib.request.Request(self.base + path, data=body, method=method,
            headers={'PRIVATE-TOKEN': self.token, 'Accept': 'application/json', 'Content-Type': 'application/json'})
        try:
            with self.opener.open(req, timeout=30) as response:
                if not 200 <= response.status < 300: fail()
                return json.loads(response.read(2**21))
        except (OSError, ValueError, urllib.error.URLError):
            fail()  # Never print credential-bearing request/response/exception objects.

    def execute(self, action, *, issue=None, data=None):
        method, endpoint, payload = operation(action, issue, {} if data is None else data)
        token = self._grant()
        user = self._http('/user'); current = self._http('/personal_access_tokens/self')
        if (user.get('id') != token['bot_user_id'] or user.get('external') is not True
                or current.get('id') != token['token_id'] or current.get('user_id') != token['bot_user_id']
                or current.get('active') is not True or current.get('revoked') is not False
                or 'api' not in current.get('scopes', [])): fail()
        prefix = f'/projects/{self.entry.project_id}'
        project = self._http(prefix)
        if (project.get('id'), project.get('path_with_namespace')) != (self.entry.project_id, self.entry.project_path): fail()
        permissions = project.get('permissions')
        if not isinstance(permissions, dict): fail()
        roles = [value.get('access_level') for value in permissions.values() if isinstance(value, dict)]
        if not roles or any(not positive(role) for role in roles) or max(roles) != {'planner': 15, 'reporter': 20}[self.entry.profile]: fail()
        memberships = self._http('/projects?membership=true&per_page=2&page=1')
        if (not isinstance(memberships, list) or len(memberships) != 1
                or not isinstance(memberships[0], dict) or memberships[0].get('id') != self.entry.project_id): fail()
        if issue is not None:
            before = self._http(prefix + f'/issues/{issue}')
            if (before.get('project_id'), before.get('iid')) != (self.entry.project_id, issue): fail()
        self._write_permit = (method, prefix + endpoint, canonical_json_sha256(payload))
        answer = self._http(prefix + endpoint, method, payload)
        if action == 'comment':
            if not positive(answer.get('id')): fail()
            result = self._http(prefix + endpoint + '/' + str(answer['id']))
            if (result.get('id') != answer['id'] or result.get('body') != payload['body']
                    or (result.get('author') or {}).get('id') != token['bot_user_id']): fail()
            return {'project_id': self.entry.project_id, 'issue_iid': issue, 'note_id': answer['id'], 'verified': True}
        iid = answer.get('iid') if action == 'create' else issue
        if not positive(iid): fail()
        result = self._http(prefix + f'/issues/{iid}')
        if (result.get('project_id'), result.get('iid')) != (self.entry.project_id, iid): fail()
        if action == 'create' and (result.get('author') or {}).get('id') != token['bot_user_id']: fail()
        if action in ('close', 'reopen'):
            if result.get('state') != ('closed' if action == 'close' else 'opened'): fail()
        elif any(result.get(k) != v for k, v in payload.items()): fail()
        return {'project_id': self.entry.project_id, 'issue_iid': iid, 'state': result.get('state'), 'verified': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-id', type=int, required=True)
    parser.add_argument('--action', choices=ACTIONS, required=True)
    parser.add_argument('--issue-iid', type=int)
    parser.add_argument('--data-file', type=Path)
    args = parser.parse_args()
    try:
        data = json.loads(args.data_file.read_text()) if args.data_file else {}
        result = IssueAccess(args.project_id).execute(args.action, issue=args.issue_iid, data=data)
    except Exception:
        print(json.dumps({'verified': False, 'error': 'Issue permission/readback not verified; do not automatically repeat a write'}))
        return 1
    print(json.dumps(result)); return 0


if __name__ == '__main__':
    raise SystemExit(main())
