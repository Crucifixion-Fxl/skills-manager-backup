#!/usr/bin/env python3
"""No-secret applicant submission and approved Terraform preparation, Issue #200.

This tool never creates Superset/IAM accounts, applies Terraform, or reports access
as active. Live authorization is always read from Feishu, never from event input.
"""
import argparse
import copy
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from urllib.parse import quote
import uuid
from contextlib import contextmanager

NODE = '/home/jchen/.nvm/versions/node/v20.20.1/bin/node'
LARK_ENTRY = '/home/jchen/.npm-global/lib/node_modules/@larksuite/cli/scripts/run.js'
APP_ID = 'cli_a940faa4ec381bc4'
APPROVAL_CODE = '1882E07A-49A1-4885-82E4-2C6129CB37D2'
APPROVER = 'ou_755158d120e03b0c18dd4a9334bc3aad'
EXECUTOR = 'ou_6d7b771147382dd4baaee27ebba8487f'
PROFILE = 'jchen-personal'
FIELDS = ('agent_id', 'agent_name_type', 'creator', 'business_owner',
          'business_line_issue', 'background', 'query_frequency', 'audience_destination',
          'region_environment', 'data_scope', 'pii_reason', 'validity', 'account_name')
META = ('region', 'environment', 'business', 'pii', 'layer')
IDENTIFIER = re.compile(r'[a-zA-Z_][a-zA-Z0-9_]{0,127}\Z')
SECRET = re.compile(r'(?i)(authorization\s*:|bearer\s+\S+|(?:password|passwd|token|secret|access[_ -]?key)["\x27]?\s*[=:]|-----BEGIN .*PRIVATE KEY|\b(?:AKIA|ASIA)[A-Z0-9]{16}\b|https?://[^\s]+[?&](?:token|signature|x-amz-credential)=)')

class AccessError(ValueError):
    """Safe actionable error; never echo untrusted input or CLI output."""

def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))

def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()

def load(path):
    if Path(path).stat().st_size > 1024 * 1024:
        raise AccessError('input exceeds 1 MiB')
    return json.loads(Path(path).read_text())

def no_secret(value):
    if isinstance(value,dict):
        for key,item in value.items():
            if re.search(r'(?i)(password|passwd|token|secret|credential|cookie|private.?key|access.?key)',str(key)):
                raise AccessError('secret fields are forbidden')
            no_secret(item)
    elif isinstance(value,list):
        for item in value:
            no_secret(item)
    if SECRET.search(canonical(value)):
        raise AccessError('secret-like content is forbidden; use the approved credential delivery channel')

def moment(value=None):
    if value is None:
        return dt.datetime.now(dt.timezone.utc)
    try:
        result = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
        if result.tzinfo is None:
            raise ValueError
        return result.astimezone(dt.timezone.utc)
    except (ValueError, AttributeError, TypeError) as exc:
        raise AccessError('validity requires ISO 8601 timestamps with timezone') from exc

def require_keys(value, required):
    if not isinstance(value, dict) or set(value) != set(required):
        raise AccessError('missing or unknown fields; follow the request/catalog contract')

def scope_shape(scope):
    require_keys(scope,META+('resources',))
    if not isinstance(scope['resources'],list) or not 1 <= len(scope['resources']) <= 50:
        raise AccessError('scope needs 1..50 explicit tables')
    for resource in scope['resources']:
        require_keys(resource,('database','table','columns','row_filter'))

def schema(template):
    if not isinstance(template, dict) or template.get('status') != 'ACTIVE' or template.get('is_external'):
        raise AccessError('approval template must be active and native')
    fields = json.loads(template['form'])
    if not isinstance(fields, list) or len(fields) != len(FIELDS):
        raise AccessError('template form changed; refresh the integration contract')
    mapped = {f.get('custom_id'): f for f in fields}
    if set(mapped) != set(FIELDS) or len({f.get('id') for f in fields}) != len(FIELDS):
        raise AccessError('missing/duplicate template custom_id or widget id')
    for field in fields:
        if not field.get('id') or field.get('type') not in ('input', 'textarea') or not field.get('required') or not field.get('visible'):
            raise AccessError('template fields must be visible required text controls')
    nodes = template.get('node_list', [])
    approval = [n for n in nodes if n.get('custom_node_id') == 'data_access_approval']
    if len(approval) != 1 or not approval[0].get('node_id') or approval[0].get('node_type') != 'AND' or any(n.get('need_approver') for n in nodes):
        raise AccessError('fixed approval node changed; applicant cannot choose approvers')
    return mapped, approval[0]['node_id']

def validity(request, now=None, for_execution=False):
    period = request.get('validity')
    require_keys(period, ('start', 'end'))
    start, end, current = moment(period['start']), moment(period['end']), moment(now)
    if start >= end or current >= end:
        raise AccessError('invalid or expired validity; submit a new approval')
    if for_execution and current < start:
        raise AccessError('approval is not yet effective')

def preview(request, template, creator, now=None):
    require_keys(request, tuple(f for f in FIELDS if f != 'creator') + ('request_id',))
    no_secret(request)
    for name in request:
        if name in ('data_scope', 'validity'):
            continue
        if not isinstance(request[name], str) or not request[name].strip() or len(request[name]) > 8192:
            raise AccessError('all business fields require nonempty bounded text')
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{2,63}', request['request_id']):
        raise AccessError('request_id must be a stable lowercase request identifier')
    if not re.fullmatch(r'agent_[a-z0-9_]{2,48}', request['account_name']):
        raise AccessError('account_name must be an Agent dedicated agent_ account')
    if not isinstance(creator, str) or not re.fullmatch(r'ou_[a-zA-Z0-9]+', creator):
        raise AccessError('verified Creator open_id is required')
    if not isinstance(request['data_scope'], (str, dict)) or not request['data_scope']:
        raise AccessError('data_scope is required')
    if isinstance(request['data_scope'],dict):
        scope_shape(request['data_scope'])
    validity(request, now)
    fields, _ = schema(template)
    values = {**copy.deepcopy(request), 'creator': creator}
    form = [dict(id=fields[name]['id'], type=fields[name]['type'],
                 value=canonical(values[name]) if isinstance(values[name], dict) else values[name]) for name in FIELDS]
    revision = digest(dict(request=request, creator=creator, template_digest=digest(template)))
    return dict(contract='buzz-data-access:v1', request=copy.deepcopy(request), creator_open_id=creator,
        request_digest=revision, template_digest=digest(template),
        payload=dict(approval_code=APPROVAL_CODE, form=canonical(form),
                     uuid=str(uuid.uuid5(uuid.NAMESPACE_URL, revision))))

def verify_receipt(receipt, template, now=None):
    allowed = {'contract','request','creator_open_id','request_digest','template_digest',
               'payload','instance_code','submitted_after','submission_attempted','execution_issue'}
    if not isinstance(receipt,dict) or set(receipt) - allowed:
        raise AccessError('unknown journal fields; refuse unverified output')
    no_secret(receipt)
    # Integrity is independent of time; expired requests remain inspectable.
    expected = preview(receipt['request'], template, receipt['creator_open_id'],
                       receipt['request']['validity']['start'])
    for key in ('contract', 'request_digest', 'template_digest', 'payload'):
        if receipt.get(key) != expected[key]:
            raise AccessError('request/template revision changed; obtain a new approval')
    return expected

def form_values(value):
    fields = json.loads(value)
    if not isinstance(fields, list) or any(not isinstance(f, dict) for f in fields):
        raise AccessError('invalid instance form')
    values = {f['id']: (f['type'], f['value']) for f in fields}
    if len(values) != len(fields):
        raise AccessError('duplicate instance form field')
    return values

def verify_instance(receipt, template, instance, now=None):
    verify_receipt(receipt, template, now)
    if not receipt.get('instance_code') or instance.get('instance_code') != receipt['instance_code']:
        raise AccessError('approval instance does not match request')
    if instance.get('definition_code') != APPROVAL_CODE or instance.get('user_id') != receipt['creator_open_id']:
        raise AccessError('approval definition or actual Creator mismatch')
    if form_values(instance['form']) != form_values(receipt['payload']['form']):
        raise AccessError('approved form differs from submitted revision; reapply')
    if instance.get('status') != 'APPROVED' or instance.get('reverted', False):
        raise AccessError('only a current approved non-reverted instance permits preparation')
    _, node = schema(template)
    tasks = [t for t in instance.get('tasks', []) if t.get('node_id') == node]
    if not tasks or not all(t.get('user_id') == APPROVER and t.get('status') == 'APPROVED' for t in tasks):
        raise AccessError('fixed approver Chen Jingmin has not approved the expected node')
    validity(receipt['request'], now, for_execution=True)

def validate_scope(scope, catalog):
    scope_shape(scope)
    require_keys(catalog, ('account_id', 'partition', 'region', 'role_path', 'terraform_project', 'resources', 'executor_open_id'))
    no_secret(catalog)
    if catalog['executor_open_id'] != EXECUTOR or catalog['terraform_project'] != 'DEV/IaC':
        raise AccessError('catalog must be owned by the designated executor in DEV/IaC')
    region = scope['region']
    if region not in ('us-east-1', 'eu-central-1', 'cn-north-1') or region != catalog['region']:
        raise AccessError('region must match a verified catalog; do not copy US configuration')
    partition = 'aws-cn' if region == 'cn-north-1' else 'aws'
    if catalog['partition'] != partition or not re.fullmatch(r'[0-9]{12}', catalog['account_id']) or catalog['role_path'] != '/employee_role/':
        raise AccessError('invalid AWS account/partition or unverified Superset role mapping')
    for key in META:
        if not isinstance(scope[key], str) or not re.fullmatch(r'[a-z0-9_-]{1,64}', scope[key]):
            raise AccessError('invalid scope metadata')
    if not isinstance(scope['resources'], list) or not 1 <= len(scope['resources']) <= 50:
        raise AccessError('scope needs 1..50 explicit tables')
    if not isinstance(catalog['resources'], list):
        raise AccessError('invalid administrator catalog')
    seen = set()
    for resource in scope['resources']:
        require_keys(resource, ('database', 'table', 'columns', 'row_filter'))
        for key in ('database', 'table'):
            if not isinstance(resource[key], str) or not IDENTIFIER.fullmatch(resource[key]):
                raise AccessError('explicit simple database/table names required')
        key = (resource['database'], resource['table'])
        if key in seen:
            raise AccessError('duplicate requested table')
        seen.add(key)
        columns = resource['columns']
        if not isinstance(columns, list) or not 1 <= len(columns) <= 100 or any(not isinstance(c,str) or not IDENTIFIER.fullmatch(c) for c in columns) or len(set(columns)) != len(columns):
            raise AccessError('explicit unique columns required; wildcards are forbidden')
        if resource['row_filter'] is not None and (not isinstance(resource['row_filter'], str) or not resource['row_filter'].strip() or len(resource['row_filter']) > 4096 or '${' in resource['row_filter'] or '%{' in resource['row_filter']):
            raise AccessError('invalid row filter')
        matches = []
        for entry in catalog['resources']:
            if all(entry.get(k) == scope[k] for k in META):
                matches.extend(r for r in entry.get('resources', []) if (r.get('database'),r.get('table')) == key
                    and r.get('row_filter') == resource['row_filter']
                    and set(columns).issubset(set(r.get('columns', []))))
        if len(matches) != 1:
            raise AccessError('data scope is unresolved, ambiguous or outside the executor verified catalog; resolve and reapply')

def prepare(request_receipt, template, instance, catalog, now=None):
    verify_instance(request_receipt, template, instance, now)
    request = request_receipt['request']
    scope = request['data_scope']
    validate_scope(scope, catalog)
    if request['region_environment'] != scope['region'] + '/' + scope['environment']:
        raise AccessError('region/environment text differs from structured scope')
    account = catalog['account_id']
    principal = f"arn:{catalog['partition']}:iam::{account}:role{catalog['role_path']}{request['account_name']}"
    grants, filters = {}, {}
    for resource in scope['resources']:
        db, table = resource['database'], resource['table']
        # Addresses are account/table based, so renewed approvals update rather than add grants.
        prefix = 'agent_' + digest([principal, db, table])[:24]
        dbkey = 'db_' + digest([principal, db])[:24]
        grants[dbkey] = dict(principal=principal, permissions=['DESCRIBE'],
                             database=[dict(catalog_id=account, name=db)])
        select = dict(principal=principal, permissions=['SELECT'])
        if resource['row_filter'] is None:
            select['table_with_columns'] = [dict(catalog_id=account, database_name=db,
                name=table, column_names=sorted(resource['columns']))]
        else:
            filter_name = prefix
            filters[prefix] = dict(table_data=[dict(database_name=db, name=filter_name,
                table_catalog_id=account, table_name=table, column_names=sorted(resource['columns']),
                row_filter=[dict(filter_expression=resource['row_filter'])])])
            select['data_cells_filter'] = [dict(database_name=db, name=filter_name,
                table_catalog_id=account, table_name=table)]
            select['depends_on'] = [f'aws_lakeformation_data_cells_filter.{prefix}']
        grants[prefix] = select
    terraform = dict(resource=dict(aws_lakeformation_permissions=grants))
    if filters:
        terraform['resource']['aws_lakeformation_data_cells_filter'] = filters
    receipt = dict(contract='buzz-data-access-preparation:v1', state='prepared', provisioned=False,
        approval_code=APPROVAL_CODE, instance_code=request_receipt['instance_code'],
        creator_open_id=request_receipt['creator_open_id'], executor_open_id=EXECUTOR,
        request_digest=request_receipt['request_digest'], template_digest=digest(template),
        catalog_digest=digest(catalog), terraform_digest=digest(terraform),
        agent_id=request['agent_id'], account_name=request['account_name'], principal=principal,
        region=scope['region'], validity=request['validity'], terraform_project='DEV/IaC')
    return dict(terraform=terraform, receipt=receipt)

class LarkClient:
    """Only the installed, explicitly selected CLI; no identity/token fallback."""
    def __init__(self, identity_config=None):
        config = identity_config or dict(profile=PROFILE,expected_open_id=APPROVER)
        if not isinstance(config,dict) or not {'profile','expected_open_id'}.issubset(config) or set(config)-{'profile','expected_open_id','node_path','cli_entry'}:
            raise AccessError('identity configuration requires profile/expected_open_id and optional installed runtime paths')
        if not isinstance(config['profile'],str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}',config['profile']) or not re.fullmatch(r'ou_[a-zA-Z0-9]+',config['expected_open_id']):
            raise AccessError('invalid host approved personal identity configuration')
        self.profile = config['profile']
        self.expected_user = config['expected_open_id']
        self.node_path = config.get('node_path',NODE)
        self.cli_entry = config.get('cli_entry',LARK_ENTRY)
        if any(not isinstance(path,str) or not Path(path).is_absolute() or '\0' in path for path in (self.node_path,self.cli_entry)):
            raise AccessError('installed Node and CLI entry require explicit absolute paths')
        self.deadline = None

    def run(self, args, data=None, business=True):
        argv = [self.node_path, self.cli_entry, *args, '--profile', self.profile]
        if business:
            argv += ['--as', 'user']
        try:
            timeout = 60 if self.deadline is None else min(60,self.deadline-time.monotonic())
            if timeout <= 0:
                raise AccessError('recovery read budget exhausted; retry read-only recovery later')
            result = subprocess.run(argv, input=canonical(data) if data is not None else None,
                text=True, capture_output=True, timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AccessError('lark CLI unavailable/timed out; retain request UUID and retry after recovery') from exc
        if result.returncode:
            # Print error classification only; raw stderr may contain payloads or credentials.
            try:
                error = json.loads(result.stderr).get('error', {})
                raw_code = error.get('code', '')
                code = str(raw_code) if isinstance(raw_code,int) else ''
                scopes = error.get('missing_scopes', [])
                scopes = [s for s in scopes if isinstance(s,str) and re.fullmatch(r'[a-z_:]+',s)]
            except (ValueError, AttributeError, TypeError):
                code, scopes, error = '', [], {}
            failure = AccessError(f'lark CLI stopped (exit={result.returncode}, code={code[:12]}, missing_scopes={",".join(scopes)}); no identity fallback')
            # Only known local CLI preflight failures can permit a later same-UUID write.
            failure.request_not_sent = error.get('subtype') in ('missing_scope','invalid_argument','confirmation_required') and error.get('type') in ('authorization','validation','confirmation')
            raise failure
        response = json.loads(result.stdout)
        if not business:
            return response
        if response.get('ok') is not True or response.get('identity') != 'user' or not isinstance(response.get('data'), dict):
            raise AccessError('unexpected CLI output/identity; no writes permitted')
        return response['data']

    def identity(self):
        state = self.run(['auth', 'status', '--json', '--verify'], business=False)
        user = state.get('identities', {}).get('user', {})
        if state.get('appId') != APP_ID or state.get('identity') != 'user' or state.get('verified') is not True or user.get('verified') is not True:
            raise AccessError('verified personal app/user identity required')
        if user.get('openId') != self.expected_user:
            raise AccessError('live user differs from host approved expected user; no identity fallback')
        return user.get('openId')

    def template(self):
        return self.run(['approval', 'approvals', 'get', '--params', canonical(dict(approval_code=APPROVAL_CODE,locale='zh-CN'))])

    def instance(self, code):
        return self.run(['approval', 'instances', 'get', '--params', canonical(dict(instance_code=code, user_id_type='open_id',locale='zh-CN'))])

    def submit(self, payload):
        return self.run(['approval', 'instances', 'create', '--data', '-', '--yes'], payload)

    def recover(self, receipt):
        """Adopt only a unique live form match; never repeat a write with unknown outcome."""
        if not receipt.get('submitted_after'):
            raise AccessError('legacy journal requires operator recovery; missing submission time')
        params = dict(definition_code=APPROVAL_CODE,user_id_type='open_id',locale='zh-CN',
            page_size=20,start_timestamp=receipt['submitted_after'])
        matches,seen = set(),set()
        self.deadline = time.monotonic()+45
        try:
            for _ in range(2):
                result = self.run(['approval','instances','initiated','--params',canonical(params)])
                for candidate in result.get('instances',[]):
                    code = candidate.get('instance_code')
                    if not code or code in seen:
                        continue
                    seen.add(code)
                    live = self.instance(code)
                    if (live.get('instance_code') == code and live.get('definition_code') == APPROVAL_CODE
                        and live.get('user_id') == receipt['creator_open_id']
                        and int(live.get('start_time','0')) >= int(receipt['submitted_after'])*1000
                        and form_values(live['form']) == form_values(receipt['payload']['form'])):
                        matches.add(code)
                if result.get('has_more') is False:
                    break
                token = result.get('page_token')
                if not token or token == params.get('page_token'):
                    raise AccessError('incomplete recovery listing; no writes or adoption')
                params['page_token'] = token
            else:
                raise AccessError('recovery listing exceeded 40 instances; operator narrows the search')
        finally:
            self.deadline = None
        if len(matches) != 1:
            raise AccessError('recovery needs exactly one live form/time/Creator match; wait for visibility or operator reconciliation, never delete the journal')
        return next(iter(matches))

class GitLabClient:
    """The authenticated glab user; no direct token handling or shell interpolation."""
    def __init__(self,identity=APPROVER):
        policy = load(Path(__file__).resolve().parents[1]/'references/execution-policy.json')
        self.host = os.environ.get('GITLAB_HOST','')
        if self.host != policy['gitlab_host']:
            raise AccessError('GITLAB_HOST must equal the packaged trusted execution host; no host fallback')
        users = {APPROVER:(400,'jchen'),EXECUTOR:(216,'wli1')}
        if identity not in users:
            raise AccessError('execution dispatcher is not the approver or executor')
        self.expected_user = users[identity]

    def run(self,endpoint,data=None):
        args = ['glab','api','--hostname',self.host,endpoint]
        if data is not None:
            # glab's JSON file input avoids shell escaping; the file contains no secret.
            with tempfile.TemporaryDirectory(prefix='buzz-data-access-') as directory:
                body = Path(directory)/'body.json'
                private_write(body,data)
                result = subprocess.run(args+['-X','POST','-H','Content-Type: application/json','--input',str(body)],capture_output=True,text=True,timeout=60,check=False)
        else:
            result = subprocess.run(args,capture_output=True,text=True,timeout=60,check=False)
        if result.returncode:
            raise AccessError('GitLab execution record read/write failed; reconcile the marker before any retry')
        return json.loads(result.stdout)

    def find_execution(self,marker):
        user = self.run('user')
        if (user.get('id'),user.get('username')) != self.expected_user or user.get('state') != 'active':
            raise AccessError('GitLab user must match the live Feishu administrator')
        executor = self.run('users/216')
        if executor.get('username') != 'wli1' or executor.get('state') != 'active':
            raise AccessError('designated GitLab executor identity changed')
        issues = self.run('projects/92/issues?scope=all&state=all&per_page=100&search='+quote(marker,safe=''))
        if not isinstance(issues,list) or len(issues) >= 100:
            raise AccessError('incomplete execution issue search; no writes')
        return [issue for issue in issues if marker in (issue.get('description') or '')]

    def reserve_execution(self,marker,before_write=None):
        # GitLab's unique branch name is the shared atomic reservation. Never adopt
        # a pre-existing reservation: it may belong to an in-flight or lost POST.
        project = self.run('projects/92')
        if project.get('id') != 92 or not project.get('default_branch'):
            raise AccessError('execution repository identity/default branch changed')
        base = self.run('projects/92/repository/branches/'+quote(project['default_branch'],safe=''))
        sha = base.get('commit',{}).get('id','')
        if not re.fullmatch(r'[a-f0-9]{40}',sha):
            raise AccessError('execution reservation needs a verified repository commit')
        name = 'buzz-data-access-reservations/'+hashlib.sha256(marker.encode()).hexdigest()
        if before_write is not None:
            before_write()
        result = self.run('projects/92/repository/branches',dict(branch=name,ref=sha))
        if result.get('name') != name or result.get('commit',{}).get('id') != sha:
            raise AccessError('reservation readback mismatch; reconcile, never retry Issue creation')

    def create_execution(self,body):
        result = self.run('projects/92/issues',body)
        return self.run('projects/92/issues/'+str(result['iid']))

def handoff(receipt,template,instance,gateway,now=None,fresh_read=None):
    verify_instance(receipt,template,instance,now)
    marker = 'buzz-data-access-execution:v1:'+receipt['instance_code']+':'+receipt['request_digest']
    existing = gateway.find_execution(marker)
    if len(existing) > 1:
        raise AccessError('duplicate execution records; reconcile before provisioning')
    body = dict(title='Buzz Agent 数据开通：'+receipt['request']['agent_id'],assignee_ids=[216],
        description=marker+'\n\nstate: approved_waiting_execution\nowner: wli1\n'+
            'Approval: '+APPROVAL_CODE+' / '+receipt['instance_code']+'\n'+
            'Tracking: engineering/skills#200\n\n'+
            'Request revision: '+receipt['request_digest']+'\nTemplate revision: '+receipt['template_digest']+
            '\n\n```json\n'+json.dumps(receipt['request'],ensure_ascii=False,indent=2)+'\n```\n\n'+
            '仅授权范围内执行；账号/Role owner、Terraform MR/plan/review/apply、Agent 身份正负向验收与到期回收逐步回填。未验收不得标记 active。')
    def reauthorize():
        if fresh_read is not None:
            live_template,live_instance = fresh_read()
            verify_instance(receipt,live_template,live_instance,now)
        validity(receipt['request'],now,for_execution=True)
    if not existing:
        reauthorize()
        gateway.reserve_execution(marker,before_write=reauthorize)
        reauthorize()
    issue = existing[0] if existing else gateway.create_execution(body)
    if issue.get('project_id') != 92 or not issue.get('iid') or not any(a.get('id') == 216 for a in issue.get('assignees',[])):
        raise AccessError('execution issue readback/assignee mismatch')
    if not existing and issue.get('description') != body['description']:
        raise AccessError('execution issue description readback mismatch; reconcile marker')
    return dict(execution_project_id=92,execution_issue_iid=issue['iid'],execution_issue_url=issue['web_url'])

def private_write(path, value):
    path = Path(path)
    if path.is_symlink():
        raise AccessError('refuse symlink output')
    fd, temporary = tempfile.mkstemp(prefix='.data-access-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

@contextmanager
def state_lock(path):
    path = Path(path)
    if not path.parent.is_dir() or path.is_symlink():
        raise AccessError('state requires an existing private local directory and a regular file')
    flags = os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW
    fd = os.open(str(path) + '.lock', flags, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for command in ('preview', 'submit', 'status', 'recover', 'prepare', 'handoff'):
        p = sub.add_parser(command)
        p.add_argument('--state', required=True, help='local no-secret request journal')
        p.add_argument('--identity-config', help='host approved local profile/expected_open_id (same app; never requester data)')
        if command in ('preview', 'submit'):
            p.add_argument('--request', required=True)
        if command in ('submit','handoff'):
            p.add_argument('--confirm-request', help='exact request_digest explicitly authorized after preview')
        if command == 'prepare':
            p.add_argument('--catalog', required=True, help='executor owned verified resource allowlist')
            p.add_argument('--out', required=True, help='new private bundle directory')
    args = parser.parse_args(argv)
    try:
        client = LarkClient(load(args.identity_config) if args.identity_config else None)
        identity = client.identity()
        template = client.template()
        if args.command in ('preview', 'submit'):
            receipt = preview(load(args.request), template, identity)
            if args.command == 'preview':
                print(json.dumps(receipt, ensure_ascii=False, indent=2)); return 0
            if args.confirm_request != receipt['request_digest']:
                raise AccessError('preview then obtain Creator authorization for this exact request_digest before submitting')
            with state_lock(args.state):
                state = Path(args.state)
                if state.exists():
                    saved = load(state)
                    verify_receipt(saved,template)
                    if saved.get('request_digest') != receipt['request_digest']:
                        raise AccessError('state belongs to another revision; create a new request journal')
                    if saved.get('instance_code'):
                        current = client.instance(saved['instance_code'])
                        if current.get('instance_code') != saved['instance_code'] or current.get('user_id') != identity or form_values(current['form']) != form_values(receipt['payload']['form']):
                            raise AccessError('existing instance does not match this Creator/request')
                        print(json.dumps(saved, ensure_ascii=False, indent=2)); return 0
                    if saved.get('submission_attempted'):
                        saved['instance_code'] = client.recover(saved)
                        private_write(state,saved)
                        print(json.dumps(saved,ensure_ascii=False,indent=2)); return 0
                    receipt = saved  # Preserve the first submission timestamp after a local preflight failure.
                else:
                    receipt['submitted_after'] = str(int(time.time()))
                receipt['submission_attempted'] = True
                private_write(state, receipt)  # Persist before network mutation, including unknown outcome.
                try:
                    result = client.submit(receipt['payload'])
                except AccessError as exc:
                    if getattr(exc,'request_not_sent',False):
                        receipt['submission_attempted'] = False
                        private_write(state,receipt)
                    raise
                if not result.get('instance_code'):
                    raise AccessError('submit result has no instance_code; retain journal, recover with same UUID')
                receipt['instance_code'] = result['instance_code']
                private_write(state, receipt)  # Persist before readback; a read timeout must not resubmit.
                instance = client.instance(receipt['instance_code'])
                if instance.get('user_id') != identity or instance.get('definition_code') != APPROVAL_CODE or form_values(instance['form']) != form_values(receipt['payload']['form']):
                    raise AccessError('created instance readback mismatch; stop and investigate')
                print(json.dumps(receipt, ensure_ascii=False, indent=2)); return 0
        with state_lock(args.state):
            receipt = load(args.state)
            verify_receipt(receipt, template)
            if args.command == 'recover':
                if identity != receipt['creator_open_id']:
                    raise AccessError('only the actual Creator can recover their submission')
                if not receipt.get('instance_code'):
                    receipt['instance_code'] = client.recover(receipt)
                    private_write(args.state,receipt)
                print(json.dumps(receipt,ensure_ascii=False,indent=2)); return 0
            if not receipt.get('instance_code'):
                raise AccessError('no submitted instance; resume the original confirmed submission')
            instance = client.instance(receipt['instance_code'])
            if instance.get('instance_code') != receipt['instance_code'] or instance.get('definition_code') != APPROVAL_CODE or instance.get('user_id') != receipt['creator_open_id'] or form_values(instance['form']) != form_values(receipt['payload']['form']):
                raise AccessError('live instance does not match this journal')
            if args.command == 'status':
                if identity not in (receipt['creator_open_id'],APPROVER,EXECUTOR):
                    raise AccessError('only Creator/approver/executor can inspect this request')
                state = instance.get('status')
                execution = 'not_authorized'
                if state == 'APPROVED' and not instance.get('reverted',False):
                    start,end = (moment(receipt['request']['validity'][k]) for k in ('start','end'))
                    if moment() >= end:
                        execution = 'expired_revocation_required'
                    elif moment() < start:
                        execution = 'approved_not_yet_effective'
                    else:
                        verify_instance(receipt,template,instance)
                        execution = 'approved_waiting_execution'
                print(json.dumps(dict(instance_code=receipt['instance_code'], approval_status=state,
                    execution_state=execution, provisioned=False), ensure_ascii=False)); return 0
            if args.command == 'handoff':
                if identity not in (APPROVER,EXECUTOR) or args.confirm_request != receipt['request_digest']:
                    raise AccessError('handoff requires approver/executor authorization for this request_digest')
                result = handoff(receipt,template,instance,GitLabClient(identity),
                    fresh_read=lambda: (client.template(),client.instance(receipt['instance_code'])))
                receipt['execution_issue'] = result
                private_write(args.state,receipt)
                print(json.dumps(result,ensure_ascii=False,indent=2)); return 0
            # Preparation is a read-only operation usable by the approver for review.
            # Only the designated executor provisions resources using the admin runbook.
            if identity not in (APPROVER,EXECUTOR):
                raise AccessError('preparation requires the designated approver or executor')
            bundle = prepare(receipt,template,instance,load(args.catalog))
            out = Path(args.out)
            if out.exists():
                if out.is_symlink() or load(out/'receipt.json') != bundle['receipt'] or load(out/'permissions.tf.json') != bundle['terraform']:
                    raise AccessError('existing bundle differs; do not overwrite previous execution evidence')
            else:
                out.mkdir(mode=0o700)
                private_write(out/'permissions.tf.json',bundle['terraform'])
                private_write(out/'receipt.json',bundle['receipt'])
            print(json.dumps(bundle['receipt'],ensure_ascii=False,indent=2)); return 0
    except (AccessError, KeyError, TypeError, ValueError, OSError) as exc:
        message = str(exc) if isinstance(exc,AccessError) else 'invalid input/state/schema; inspect the contract without exposing secrets'
        print(json.dumps(dict(ok=False,error=message),ensure_ascii=False),file=sys.stderr)
        return 1

if __name__ == '__main__':
    sys.exit(main())
