"""Issue #200: applicant/admin contracts and public CLI with external CLI doubles."""
import copy
from contextlib import redirect_stdout,redirect_stderr
import io
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = Path(os.environ.get('DATA_ACCESS_TEST_SCRIPT', ROOT / 'scripts/data_access.py'))
spec = importlib.util.spec_from_file_location('data_access', SCRIPT)
workflow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(workflow)
CODE = '1882E07A-49A1-4885-82E4-2C6129CB37D2'
APPROVER = 'ou_755158d120e03b0c18dd4a9334bc3aad'
CREATOR = 'ou_creatorfixture'
EXECUTOR = 'ou_6d7b771147382dd4baaee27ebba8487f'
NOW = '2026-10-06T12:00:00Z'

def fixture():
    template = json.loads((ROOT / 'tests/template.json').read_text())
    scope = dict(region='us-east-1', environment='prod', business='ecommerce',
                 pii='default', layer='mart', resources=[dict(database='bi', table='orders',
                 columns=['order_count'], row_filter="product = 'fixture'")])
    request = dict(request_id='fixture-bi-v1', agent_id='fixture-bi',
        agent_name_type='fixture-bi / BI', business_owner='fixture owner',
        business_line_issue='fixture / https://gitlab.addx.ai/DATA/dbt/-/issues/1',
        background='analyse product conversion', query_frequency='daily',
        audience_destination='product team / business Issue only',
        region_environment='us-east-1/prod', data_scope=scope, pii_reason='no PII',
        validity=dict(start='2026-10-06T00:00:00Z', end='2026-11-06T00:00:00Z'),
        account_name='agent_fixture_bi')
    catalog = dict(account_id='769494896000', partition='aws', region='us-east-1',
        role_path='/employee_role/', terraform_project='DEV/IaC',
        resources=[copy.deepcopy(scope)], executor_open_id=EXECUTOR)
    return request, template, catalog

def approved(receipt, template):
    return dict(instance_code='instance-fixture', definition_code=CODE, user_id=CREATOR,
        status='APPROVED', reverted=False, form=receipt['payload']['form'],
        tasks=[dict(node_id=template['node_list'][0]['node_id'], user_id=APPROVER,
                    status='APPROVED')])

class ContractTests(unittest.TestCase):
    def setUp(self):
        self.request, self.template, self.catalog = fixture()

    def receipt(self):
        r = workflow.preview(self.request, self.template, CREATOR, NOW)
        r['instance_code'] = 'instance-fixture'
        return r

    def test_creator_and_schema_ids_are_derived_not_user_supplied(self):
        r = self.receipt()
        values = json.loads(r['payload']['form'])
        self.assertEqual(next(v['value'] for v in values if v['id'] == 'widget17912912172'), CREATOR)
        self.assertEqual(r['payload']['approval_code'], CODE)
        moved = copy.deepcopy(self.template)
        fields = json.loads(moved['form']); fields[0]['id'] = 'new-widget'
        moved['form'] = json.dumps(fields)
        self.assertEqual(json.loads(workflow.preview(self.request, moved, CREATOR, NOW)['payload']['form'])[0]['id'], 'new-widget')

    def test_retries_stable_uuid_revision_changes_uuid(self):
        a = self.receipt(); b = self.receipt()
        self.assertEqual(a['payload']['uuid'], b['payload']['uuid'])
        self.request['background'] = 'different purpose'
        c = self.receipt()
        self.assertNotEqual(a['payload']['uuid'], c['payload']['uuid'])

    def test_missing_unknown_and_secret_fields_rejected(self):
        variants = []
        missing = copy.deepcopy(self.request); del missing['background']; variants.append(missing)
        extra = copy.deepcopy(self.request); extra['password'] = 'example'; variants.append(extra)
        secret = copy.deepcopy(self.request); secret['background'] = 'Authorization: Bearer fixture-secret'; variants.append(secret)
        creator = copy.deepcopy(self.request); creator['creator'] = 'ou_other'; variants.append(creator)
        account = copy.deepcopy(self.request); account['account_name'] = 'admin'; variants.append(account)
        nested = copy.deepcopy(self.request); nested['data_scope'] = {'password':'fixture-secret'}; variants.append(nested)
        for request in variants:
            with self.subTest(request=request), self.assertRaises(workflow.AccessError):
                workflow.preview(request, self.template, CREATOR, NOW)

    def test_invalid_validity_and_region_rejected(self):
        for value in ['永久', {'start': '2026-12-01T00:00:00Z', 'end': '2026-11-01T00:00:00Z'}]:
            self.request['validity'] = value
            with self.assertRaises(workflow.AccessError): self.receipt()

    def test_optional_approver_or_schema_drift_rejected(self):
        changed = copy.deepcopy(self.template); changed['node_list'][0]['need_approver'] = True
        with self.assertRaises(workflow.AccessError):
            workflow.preview(self.request, changed, CREATOR, NOW)
        r = self.receipt(); self.template['approval_name'] = 'changed template'
        with self.assertRaises(workflow.AccessError):
            workflow.prepare(r, self.template, approved(r, self.template), self.catalog, NOW)

    def test_only_fresh_approved_expected_approver_can_prepare(self):
        r = self.receipt(); instance = approved(r, self.template)
        changes = [dict(status=s) for s in ['PENDING','REJECTED','CANCELED','DELETED']]
        changes += [dict(reverted=True), dict(user_id='ou_other'), dict(definition_code='other'),
                    dict(instance_code='other'), dict(tasks=[]),
                    dict(tasks=[dict(node_id=self.template['node_list'][0]['node_id'], user_id='ou_other',status='APPROVED')])]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(workflow.AccessError):
                workflow.prepare(r, self.template, {**instance, **change}, self.catalog, NOW)

    def test_expired_future_and_edited_form_stop_preparation(self):
        r = self.receipt(); instance = approved(r, self.template)
        for now in ['2026-10-05T23:00:00Z','2026-11-06T00:00:00Z']:
            with self.assertRaises(workflow.AccessError):
                workflow.prepare(r, self.template, instance, self.catalog, now)
        fields = json.loads(instance['form']); fields[0]['value'] = 'other-agent'; instance['form'] = json.dumps(fields)
        with self.assertRaises(workflow.AccessError):
            workflow.prepare(r, self.template, instance, self.catalog, NOW)

    def test_exact_scoped_readonly_terraform_and_receipt(self):
        r = self.receipt()
        bundle = workflow.prepare(r, self.template, approved(r,self.template), self.catalog, NOW)
        resources = bundle['terraform']['resource']
        grants = list(resources['aws_lakeformation_permissions'].values())
        self.assertEqual(len(grants), 2)
        self.assertEqual(sorted(g['permissions'] for g in grants), [['DESCRIBE'],['SELECT']])
        self.assertTrue(all('permissions_with_grant_option' not in g for g in grants))
        self.assertTrue(all(g['principal'] == 'arn:aws:iam::769494896000:role/employee_role/agent_fixture_bi' for g in grants))
        filters = list(resources['aws_lakeformation_data_cells_filter'].values())
        self.assertEqual(filters[0]['table_data'][0]['column_names'], ['order_count'])
        self.assertEqual(filters[0]['table_data'][0]['row_filter'][0]['filter_expression'], "product = 'fixture'")
        self.assertEqual(bundle['receipt']['state'], 'prepared')
        self.assertFalse(bundle['receipt']['provisioned'])
        self.assertEqual(bundle, workflow.prepare(r,self.template,approved(r,self.template),self.catalog,NOW))

    def test_unresolved_or_uncatalogued_scope_stops_without_guessing(self):
        for change in ['please grant ecommerce', {**self.request['data_scope'], 'business':'golf'},
                       {**self.request['data_scope'], 'resources':[dict(database='bi',table='orders',columns=['*'],row_filter='true')]}]:
            self.request['data_scope'] = change
            r = self.receipt()
            with self.assertRaises(workflow.AccessError):
                workflow.prepare(r,self.template,approved(r,self.template),self.catalog,NOW)

    def test_injected_account_region_catalog_and_receipt_rejected(self):
        r = self.receipt(); instance = approved(r,self.template)
        for key,value in [('region','cn-north-1'),('account_id','bad'),('role_path','/shared/'),('executor_open_id','ou_other')]:
            c = {**self.catalog,key:value}
            with self.subTest(key=key), self.assertRaises(workflow.AccessError):
                workflow.prepare(r,self.template,instance,c,NOW)
        changed = copy.deepcopy(r); changed['request']['data_scope']['resources'][0]['columns'] = ['pii']
        with self.assertRaises(workflow.AccessError):
            workflow.prepare(changed,self.template,instance,self.catalog,NOW)

class CLITests(unittest.TestCase):
    def test_public_cli_preview_submit_replay_status_and_prepare(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request,template,catalog = fixture()
            request['validity'] = dict(start='2000-01-01T00:00:00Z',end='2100-01-01T00:00:00Z')
            (root/'request.json').write_text(json.dumps(request))
            (root/'catalog.json').write_text(json.dumps(catalog))
            # Real CLI, disk/lock/idempotency/serialization; only the external lark process is doubled.
            harness = root/'harness.py'
            harness.write_text('''import importlib.util,json,subprocess,sys
from pathlib import Path
spec=importlib.util.spec_from_file_location("flow",sys.argv[1]); m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
root=Path(sys.argv[2]); template=json.loads(Path(sys.argv[3]).read_text())
def external(argv,**kwargs):
 assert argv[:2]==[m.NODE,m.LARK_ENTRY]
 assert "--profile" in argv
 if "auth" in argv:
  d={"identity":"user","verified":True,"appId":m.APP_ID,"identities":{"user":{"openId":m.APPROVER,"verified":True,"tokenStatus":"valid"}}}
 else:
  assert argv[argv.index("--as")+1]=="user"
  if "approvals" in argv: d=template
  elif "create" in argv:
   with (root/"creates").open("a") as f: f.write("created\\n")
   payload=json.loads(kwargs["input"]); (root/"submitted.json").write_text(json.dumps(payload))
   d={"instance_code":"instance-fixture"}
  else:
   p=json.loads((root/"submitted.json").read_text()); d={"instance_code":"instance-fixture","definition_code":m.APPROVAL_CODE,"status":"APPROVED","user_id":m.APPROVER,"form":p["form"],"tasks":[{"node_id":template["node_list"][0]["node_id"],"user_id":m.APPROVER,"status":"APPROVED"}]}
  d={"ok":True,"identity":"user","data":d}
 return subprocess.CompletedProcess(argv,0,json.dumps(d),"")
m.subprocess.run=external
sys.exit(m.main(sys.argv[4:]))
''')
            def run(*args):
                return subprocess.run([sys.executable,str(harness),str(SCRIPT),str(root),str(ROOT/'tests/template.json'),*args],capture_output=True,text=True)
            args = ['--request',str(root/'request.json'),'--state',str(root/'state.json')]
            p = run('preview',*args)
            self.assertEqual(p.returncode,0,p.stderr)
            digest = json.loads(p.stdout)['request_digest']
            p = run('submit',*args)
            self.assertNotEqual(p.returncode,0); self.assertFalse((root/'creates').exists())
            for _ in range(2):
                p = run('submit',*args,'--confirm-request',digest)
                self.assertEqual(p.returncode,0,p.stderr)
            self.assertEqual((root/'creates').read_text(),'created\n')
            self.assertEqual((root/'state.json').stat().st_mode & 0o777,0o600)
            p = run('status','--state',str(root/'state.json'))
            self.assertEqual(p.returncode,0,p.stderr)
            self.assertEqual(json.loads(p.stdout)['execution_state'],'approved_waiting_execution')
            p = run('prepare','--state',str(root/'state.json'),'--catalog',str(root/'catalog.json'),'--out',str(root/'bundle'))
            self.assertEqual(p.returncode,0,p.stderr)
            self.assertTrue((root/'bundle/permissions.tf.json').exists())
            self.assertEqual(json.loads((root/'bundle/receipt.json').read_text())['state'],'prepared')

class IdentityAndRecoveryTests(unittest.TestCase):
    def test_handoff_does_not_write_after_approval_expires_during_search(self):
        from unittest.mock import Mock,patch
        request,template,catalog = fixture()
        request['validity']['end'] = '2026-10-06T12:00:01Z'
        receipt = workflow.preview(request,template,CREATOR,NOW) | dict(instance_code='instance-fixture')
        clock = [workflow.moment(NOW)]
        original = workflow.moment
        gateway = Mock()
        def search(marker):
            clock[0] = original('2026-10-06T12:00:02Z')
            return []
        gateway.find_execution.side_effect = search
        with patch.object(workflow,'moment',side_effect=lambda value=None: original(value) if value is not None else clock[0]):
            with self.assertRaises(workflow.AccessError):
                workflow.handoff(receipt,template,approved(receipt,template),gateway)
        gateway.reserve_execution.assert_not_called()
        gateway.create_execution.assert_not_called()

    def test_recover_lost_create_response_reads_unique_real_instance(self):
        from unittest.mock import Mock
        request,template,catalog = fixture()
        receipt = workflow.preview(request,template,CREATOR,NOW)
        receipt['submitted_after'] = '1791200000'
        client = workflow.LarkClient()
        client.run = Mock(return_value=dict(instances=[dict(instance_code='instance-fixture',definition_code=CODE)],has_more=False))
        live = approved(receipt | dict(instance_code='instance-fixture'),template)
        live['start_time'] = '1791200000000'
        client.instance = Mock(return_value=live)
        self.assertEqual(client.recover(receipt),'instance-fixture')
        client.run.return_value = dict(instances=[],has_more=False)
        with self.assertRaises(workflow.AccessError): client.recover(receipt)
        client.run.return_value = dict(instances=[dict(instance_code='a'),dict(instance_code='b')],has_more=False)
        with self.assertRaises(workflow.AccessError): client.recover(receipt)

    def test_journal_unknown_secret_extras_cannot_be_printed(self):
        request,template,catalog = fixture()
        receipt = workflow.preview(request,template,CREATOR,NOW)
        receipt['password'] = 'fixture-secret'
        with self.assertRaises(workflow.AccessError):
            workflow.verify_receipt(receipt,template,NOW)

    def test_gitlab_rejects_unapproved_host_before_transport(self):
        from unittest.mock import patch
        for host in ('attacker.example', 'gitlab.addx.ai.evil.example', ''):
            with patch.dict(os.environ, {'GITLAB_HOST':host}), patch.object(workflow.subprocess,'run') as transport:
                with self.assertRaises(workflow.AccessError):
                    workflow.GitLabClient()
                transport.assert_not_called()

    def test_gitlab_dispatcher_must_match_live_feishu_administrator(self):
        from unittest.mock import patch
        with patch.dict(os.environ, {'GITLAB_HOST':'gitlab.addx.ai'}):
            client = workflow.GitLabClient()
        with patch.object(client,'run',return_value=dict(id=216,username='wli1',state='active')):
            with self.assertRaises(workflow.AccessError):
                client.find_execution('marker')

    def test_server_reservation_is_atomic_and_never_reused_for_creation(self):
        from unittest.mock import patch
        with patch.dict(os.environ, {'GITLAB_HOST':'gitlab.addx.ai'}):
            clients = [workflow.GitLabClient(),workflow.GitLabClient()]
        reservations = set()
        def server(endpoint,data=None):
            if endpoint == 'projects/92':
                return dict(id=92,default_branch='master')
            if endpoint.startswith('projects/92/repository/branches/'):
                return dict(commit=dict(id='a'*40))
            self.assertEqual(endpoint,'projects/92/repository/branches')
            if data['branch'] in reservations:
                raise workflow.AccessError('branch exists; reconcile reservation')
            reservations.add(data['branch'])
            return dict(name=data['branch'],commit=dict(id='a'*40))
        for client in clients:
            client.run = server
        clients[0].reserve_execution('same-request-marker')
        with self.assertRaises(workflow.AccessError):
            clients[1].reserve_execution('same-request-marker')
        self.assertEqual(len(reservations),1)

    def test_reservation_rechecks_authorization_after_repository_reads(self):
        from unittest.mock import patch,Mock
        with patch.dict(os.environ, {'GITLAB_HOST':'gitlab.addx.ai'}):
            client = workflow.GitLabClient()
        reads = [dict(id=92,default_branch='master'),dict(commit=dict(id='a'*40))]
        guard = Mock(side_effect=workflow.AccessError('approval expired after repository reads'))
        with patch.object(client,'run',side_effect=reads) as transport:
            with self.assertRaises(workflow.AccessError):
                client.reserve_execution('marker',before_write=guard)
        guard.assert_called_once()
        self.assertEqual(transport.call_count,2)
        self.assertTrue(all(len(call.args)==1 for call in transport.call_args_list))

    def test_failed_or_unknown_reservation_never_creates_issue(self):
        from unittest.mock import Mock
        request,template,_ = fixture()
        receipt = workflow.preview(request,template,CREATOR,NOW)
        receipt['instance_code'] = 'instance-fixture'
        gateway = Mock()
        gateway.find_execution.return_value = []
        gateway.reserve_execution.side_effect = workflow.AccessError('unknown reservation result')
        with self.assertRaises(workflow.AccessError):
            workflow.handoff(receipt,template,approved(receipt,template),gateway,NOW)
        gateway.create_execution.assert_not_called()

    def test_handoff_reuses_server_issue_and_is_assigned_to_executor(self):
        from unittest.mock import Mock
        request,template,catalog = fixture()
        receipt = workflow.preview(request,template,CREATOR,NOW)
        receipt['instance_code'] = 'instance-fixture'
        instance = approved(receipt,template)
        gateway = Mock()
        gateway.find_execution.return_value = []
        gateway.create_execution.return_value = dict(iid=9,project_id=92,web_url='https://gitlab.addx.ai/DEV/IaC/-/issues/9',assignees=[dict(id=216)])
        gateway.create_execution.side_effect = lambda body: gateway.create_execution.return_value | {'description':body['description']}
        result = workflow.handoff(receipt,template,instance,gateway,NOW)
        body = gateway.create_execution.call_args.args[0]
        self.assertEqual(body['assignee_ids'],[216])
        self.assertIn(receipt['request_digest'],body['description'])
        self.assertIn('approved_waiting_execution',body['description'])
        self.assertEqual(result['execution_issue_iid'],9)
        existing = gateway.create_execution.return_value | {'description':body['description']}
        gateway.find_execution.return_value = [existing]
        workflow.handoff(receipt,template,instance,gateway,NOW)
        self.assertEqual(gateway.create_execution.call_count,1)
        gateway.find_execution.return_value = [existing,existing]
        with self.assertRaises(workflow.AccessError):
            workflow.handoff(receipt,template,instance,gateway,NOW)

    def test_rejected_or_expired_approval_never_dispatches(self):
        from unittest.mock import Mock
        request,template,catalog = fixture()
        receipt = workflow.preview(request,template,CREATOR,NOW)
        receipt['instance_code'] = 'instance-fixture'
        gateway = Mock()
        for status in ('PENDING','REJECTED','CANCELED','DELETED'):
            instance = approved(receipt,template) | dict(status=status)
            with self.assertRaises(workflow.AccessError):
                workflow.handoff(receipt,template,instance,gateway,NOW)
        gateway.find_execution.assert_not_called()
        gateway.create_execution.assert_not_called()

    def test_personal_identity_configuration_checks_live_user(self):
        from unittest.mock import patch
        config = dict(profile='wli1-personal',expected_open_id=EXECUTOR)
        state = dict(appId=workflow.APP_ID,identity='user',verified=True,
                     identities=dict(user=dict(verified=True,openId=EXECUTOR)))
        with patch.object(workflow.LarkClient,'run',return_value=state):
            self.assertEqual(workflow.LarkClient(config).identity(),EXECUTOR)
        state['identities']['user']['openId'] = APPROVER
        with patch.object(workflow.LarkClient,'run',return_value=state):
            with self.assertRaises(workflow.AccessError): workflow.LarkClient(config).identity()

    def test_local_profile_requires_expected_user_and_never_bot(self):
        from unittest.mock import patch
        state = dict(appId=workflow.APP_ID,identity='user',verified=True,
                     identities=dict(user=dict(verified=True,openId='ou_other')))
        with patch.object(workflow.LarkClient,'run',return_value=state):
            with self.assertRaises(workflow.AccessError): workflow.LarkClient().identity()

    def test_cli_errors_do_not_echo_secret_or_raw_response(self):
        from unittest.mock import patch
        reply = subprocess.CompletedProcess([],1,'',json.dumps(dict(error=dict(code='fixture-secret',message='fixture-secret'))))
        with patch.object(workflow.subprocess,'run',return_value=reply):
            with self.assertRaises(workflow.AccessError) as error:
                workflow.LarkClient().run(['approval','instances','get'])
        self.assertNotIn('fixture-secret',str(error.exception))

    def test_expired_receipt_integrity_can_be_read_without_authorizing_execution(self):
        request,template,catalog = fixture()
        receipt = workflow.preview(request,template,CREATOR,NOW)
        receipt['instance_code'] = 'instance-fixture'
        workflow.verify_receipt(receipt,template,'2026-12-06T00:00:00Z')
        with self.assertRaises(workflow.AccessError):
            workflow.prepare(receipt,template,approved(receipt,template),catalog,'2026-12-06T00:00:00Z')

class PublicRecoveryTests(unittest.TestCase):
    """Run real CLI/state/transport, replacing only the two external CLI processes."""
    def test_unknown_submission_recovers_and_duplicate_handoff_reuses_execution_issue(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request,template,catalog = fixture()
            request['validity'] = dict(start='2000-01-01T00:00:00Z',end='2100-01-01T00:00:00Z')
            (root/'request.json').write_text(json.dumps(request))
            (root/'catalog.json').write_text(json.dumps(catalog))
            external_state = dict(creates=0,issue_creates=0,issue=None,status='APPROVED',missing_scope=True)
            def process(argv,**kwargs):
                if argv[0] == 'glab':
                    endpoint = argv[argv.index('gitlab.addx.ai')+1]
                    if endpoint == 'user':
                        result = dict(id=400,username='jchen',state='active')
                    elif endpoint == 'projects/92':
                        result = dict(id=92,default_branch='master')
                    elif endpoint.startswith('projects/92/repository/branches/'):
                        result = dict(commit=dict(id='a'*40))
                    elif endpoint == 'projects/92/repository/branches':
                        body = json.loads(Path(argv[argv.index('--input')+1]).read_text())
                        result = dict(name=body['branch'],commit=dict(id=body['ref']))
                    elif endpoint == 'users/216':
                        result = dict(username='wli1',state='active')
                    elif '?' in endpoint:
                        result = [external_state['issue']] if external_state['issue'] else []
                    elif '-X' in argv:
                        body = json.loads(Path(argv[argv.index('--input')+1]).read_text())
                        external_state['issue_creates'] += 1
                        result = body | dict(iid=9,project_id=92,web_url='https://gitlab.addx.ai/DEV/IaC/-/issues/9',assignees=[dict(id=216)])
                        external_state['issue'] = result
                    else:
                        result = external_state['issue']
                    return subprocess.CompletedProcess(argv,0,json.dumps(result),'')
                self.assertEqual(argv[:2],[workflow.NODE,workflow.LARK_ENTRY])
                self.assertEqual(argv[argv.index('--profile')+1],'jchen-personal')
                if 'auth' in argv:
                    result = dict(appId=workflow.APP_ID,identity='user',verified=True,identities=dict(user=dict(openId=APPROVER,verified=True)))
                else:
                    self.assertEqual(argv[argv.index('--as')+1],'user')
                    if 'approvals' in argv:
                        result = template
                    elif 'create' in argv:
                        if external_state['missing_scope']:
                            external_state['missing_scope'] = False
                            return subprocess.CompletedProcess(argv,1,'',json.dumps(dict(ok=False,error=dict(type='authorization',subtype='missing_scope',missing_scopes=['approval:instance:write']))))
                        external_state['creates'] += 1
                        external_state['payload'] = json.loads(kwargs['input'])
                        # Server accepted the submission; only response delivery timed out.
                        raise subprocess.TimeoutExpired(argv,60)
                    elif 'initiated' in argv:
                        result = dict(instances=[dict(instance_code='instance-fixture')],has_more=False)
                    else:
                        result = dict(instance_code='instance-fixture',definition_code=CODE,user_id=APPROVER,
                            status=external_state['status'],form=external_state['payload']['form'],
                            start_time=str(int(__import__('time').time())*1000),
                            tasks=[dict(node_id=template['node_list'][0]['node_id'],user_id=APPROVER,status='APPROVED')])
                    result = dict(ok=True,identity='user',data=result)
                return subprocess.CompletedProcess(argv,0,json.dumps(result),'')
            def run(*args):
                out,err = io.StringIO(),io.StringIO()
                with patch.dict(os.environ,{'GITLAB_HOST':'gitlab.addx.ai'}),patch.object(workflow.subprocess,'run',side_effect=process),redirect_stdout(out),redirect_stderr(err):
                    code = workflow.main(list(args))
                return code,out.getvalue(),err.getvalue()
            common = ('--request',str(root/'request.json'),'--state',str(root/'state.json'))
            code,output,error = run('preview',*common)
            self.assertEqual(code,0,error)
            confirm = ('--confirm-request',json.loads(output)['request_digest'])
            code,output,error = run('submit',*common,*confirm)
            self.assertEqual(code,1)
            self.assertEqual(external_state['creates'],0)
            self.assertFalse(json.loads((root/'state.json').read_text())['submission_attempted'])
            code,output,error = run('submit',*common,*confirm)
            self.assertEqual(code,1)
            self.assertEqual(external_state['creates'],1)
            self.assertNotIn('instance_code',json.loads((root/'state.json').read_text()))
            code,output,error = run('submit',*common,*confirm)
            self.assertEqual(code,0,error)
            self.assertEqual(external_state['creates'],1)
            self.assertEqual(json.loads(output)['instance_code'],'instance-fixture')
            for _ in range(2):
                code,output,error = run('handoff','--state',str(root/'state.json'),*confirm)
                self.assertEqual(code,0,error)
            self.assertEqual(external_state['issue_creates'],1)
            for status in ('PENDING','REJECTED','CANCELED','DELETED'):
                external_state['status'] = status
                code,output,error = run('handoff','--state',str(root/'state.json'),*confirm)
                self.assertEqual(code,1)
                code,output,error = run('status','--state',str(root/'state.json'))
                self.assertEqual(code,0,error)
                self.assertEqual(json.loads(output)['execution_state'],'not_authorized')
            self.assertEqual(external_state['issue_creates'],1)

    def test_applicant_entrypoint_help_is_installed_and_offline(self):
        repo_root = Path(__file__).resolve().parents[4]
        wrapper = repo_root/'skills/agent-harness/buzz-agent-setup/references/scripts/buzz_data_access.py'
        result = subprocess.run([sys.executable,str(wrapper),'--help'],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('preview',result.stdout)
        self.assertIn('handoff',result.stdout)

    def test_applicant_entrypoint_supports_legacy_flat_plugin_layout(self):
        repo_root = Path(__file__).resolve().parents[4]
        source_wrapper = repo_root/'skills/agent-harness/buzz-agent-setup/references/scripts/buzz_data_access.py'
        source_script = ROOT/'scripts/data_access.py'
        with tempfile.TemporaryDirectory() as temporary:
            plugin = Path(temporary)/'skills'
            wrapper = plugin/'buzz-agent-setup/references/scripts/buzz_data_access.py'
            script = plugin/'buzz-data-access-admin/scripts/data_access.py'
            wrapper.parent.mkdir(parents=True)
            script.parent.mkdir(parents=True)
            shutil.copyfile(source_wrapper, wrapper)
            shutil.copyfile(source_script, script)
            result = subprocess.run([sys.executable,str(wrapper),'--help'],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('preview',result.stdout)
        self.assertIn('handoff',result.stdout)

if __name__ == '__main__': unittest.main()
