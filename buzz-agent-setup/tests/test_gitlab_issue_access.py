import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import gitlab_issue_access as m

class Response:
    status = 200
    def __init__(self, value): self.value = value
    def read(self, size): return json.dumps(self.value).encode()
    def __enter__(self): return self
    def __exit__(self, *args): pass

class Transport:
    def __init__(self):
        self.calls=[]; self.wrong_user=False; self.bad_readback=False; self.role=20; self.extra=False; self.fail_write=False
        self.issue={'project_id':389,'iid':7,'title':'test','description':'test','state':'opened','author':{'id':1138}}
    def open(self, request, timeout):
        path=request.full_url.removeprefix('https://gitlab.addx.ai/api/v4'); method=request.method
        self.calls.append((method,path)); data=json.loads(request.data) if request.data else {}
        if method!='GET' and self.fail_write:raise TimeoutError('unknown result')
        if path=='/user': value={'id':999 if self.wrong_user else 1138,'external':True}
        elif path=='/personal_access_tokens/self': value={'id':1225,'user_id':1138,'active':True,'revoked':False,'scopes':['api']}
        elif path=='/projects/389': value={'id':389,'path_with_namespace':'FAC/factory-backend','permissions':{'project_access':{'access_level':self.role},'group_access':None}}
        elif path.startswith('/projects?'):value=[{'id':389}]+([{'id':390}] if self.extra else [])
        elif '/notes' in path: value={'id':17,'body':data.get('body','comment'), 'author':{'id':1138}}
        else:
            if method!='GET':
                self.issue.update({k:v for k,v in data.items() if k!='state_event'})
                if 'state_event' in data:self.issue['state']='closed' if data['state_event']=='close' else 'opened'
            value=copy.deepcopy(self.issue)
            if self.bad_readback and method=='GET':value['project_id']=999
        return Response(value)

class IssueAccessTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.p=Path(self.tmp.name)
        raw={'version':1,'host':'gitlab.addx.ai','projects':[{'project_id':389,'project_path':'FAC/factory-backend','token_env':'FAC_FACTORY_BACKEND_GITLAB_TOKEN','profile':'reporter'}]}
        self.save('map',raw); mapping=m.load_mapping(self.p/'map')
        provision={'schema_version':'2.0','secret_material_in_receipt':False,'mapping':m.mapping_to_public_dict(mapping),'tokens':[{'project_id':389,'project_path':'FAC/factory-backend','profile':'reporter','bot_external':True,'membership_project_ids':[389],'access_level':20,'scopes':['api','read_repository'],'bot_user_id':1138,'token_id':1225}]}
        self.save('provision',provision)
        self.grant={'schema':'agent-issue-access-v1','status':'verified','map_sha256':m.mapping_sha256(mapping),'helper_sha256':hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest(),'provisioning_sha256':m.canonical_json_sha256(provision),'actions':list(m.ACTIONS),'authorization_ref':'owner explicit Issue scope','projects':[{'project_id':389,'bot_user_id':1138,'token_id':1225,'issue_iid':7,'note_id':17,'actions':list(m.ACTIONS),'verified_at':(dt.datetime.now(dt.timezone.utc)-dt.timedelta(seconds=1)).isoformat()}]}
        self.save('grant',self.grant)
        self.env={'BUZZ_GITLAB_PROJECT_TOKEN_MAP':str(self.p/'map'),m.GRANT_ENV:str(self.p/'grant'),m.PROVISION_ENV:str(self.p/'provision'),'FAC_FACTORY_BACKEND_GITLAB_TOKEN':'test-credential'}
        self.transport=Transport()
    def save(self,name,data):
        p=self.p/name;p.write_text(json.dumps(data));p.chmod(0o600)
    def access(self,project=389):return m.IssueAccess(project,environ=self.env,opener=self.transport)
    def test_full_issue_lifecycle_fixed_endpoints_readback(self):
        a=self.access()
        for action,issue,data in [('create',None,{'title':'canary','description':'test'}),('comment',7,{'body':'comment'}),('update',7,{'title':'updated'}),('close',7,{}),('reopen',7,{})]:
            self.assertTrue(a.execute(action,issue=issue,data=data)['verified'])
        self.assertEqual(len([c for c in self.transport.calls if c[0]!='GET']),5)
        self.assertTrue(all('/issues' in path for method,path in self.transport.calls if method!='GET'))
    def test_missing_stale_or_expanded_grant_fails_before_network(self):
        for field,value in [('helper_sha256','0'*64),('map_sha256','0'*64),('provisioning_sha256','0'*64),('actions',['create','delete'])]:
            with self.subTest(field=field):
                self.save('grant',{**self.grant,field:value})
                with self.assertRaises(Exception):self.access().execute('close',issue=7)
                self.assertEqual(self.transport.calls,[])
    def test_wrong_identity_cannot_write(self):
        self.transport.wrong_user=True
        with self.assertRaises(m.IssueAccessError):self.access().execute('close',issue=7)
        self.assertFalse(any(method!='GET' for method,path in self.transport.calls))
    def test_foreign_project_endpoint_and_payload_blocked_without_network(self):
        with self.assertRaises(Exception):self.access(390)
        a=self.access()
        for path in ['/projects/390/issues','/projects/389/repository/files/a','/projects/389/issues/7?sudo=400']:
            with self.assertRaises(m.IssueAccessError):a._http(path,'POST',{})
        with self.assertRaises(m.IssueAccessError):a._http('/projects/389/issues','POST',{})
        for action,issue,data in [('delete',7,{}),('update',7,{'sudo':400}),('create',None,{'title':'x','description':'x','project_id':390})]:
            with self.assertRaises(m.IssueAccessError):a.execute(action,issue=issue,data=data)
        self.assertEqual(self.transport.calls,[])
    def test_quick_actions_rejected_before_network_even_in_code_blocks(self):
        for text in ('/move FAC/other', 'hello\n /create_merge_request branch', '```\n/publish\n```', '\t/close'):
            for action,issue,data in [('create',None,{'title':'x','description':text}),('update',7,{'description':text}),('comment',7,{'body':text})]:
                with self.subTest(action=action,text=text):
                    with self.assertRaises(m.IssueAccessError):self.access().execute(action,issue=issue,data=data)
                    self.assertEqual(self.transport.calls,[])
    def test_role_or_membership_drift_blocks_write(self):
        for role,extra in [(30,False),(20,True),(10,False)]:
            self.transport=Transport();self.transport.role=role;self.transport.extra=extra
            with self.assertRaises(m.IssueAccessError):self.access().execute('close',issue=7)
            self.assertFalse(any(method!='GET' for method,path in self.transport.calls))
    def test_unknown_write_is_attempted_once_without_retry(self):
        self.transport.fail_write=True
        with self.assertRaises(m.IssueAccessError):self.access().execute('create',data={'title':'x','description':'x'})
        self.assertEqual(sum(method!='GET' for method,path in self.transport.calls),1)
    def test_wrong_existing_issue_readback_blocks_write(self):
        self.transport.bad_readback=True
        with self.assertRaises(m.IssueAccessError):self.access().execute('close',issue=7)
        self.assertFalse(any(method!='GET' for method,path in self.transport.calls))

if __name__=='__main__':unittest.main()
