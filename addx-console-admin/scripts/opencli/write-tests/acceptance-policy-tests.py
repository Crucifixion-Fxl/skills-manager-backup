import importlib.util,json,pathlib,subprocess,types,unittest,uuid,tempfile
from unittest.mock import patch
source=pathlib.Path(__file__).parents[1]/'acceptance-remote.py'
spec=importlib.util.spec_from_file_location('acceptance',source);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
def payload(access='read',flags=None,source=None):
 return {'site':'addx-console-acceptance-'+uuid.uuid4().hex,'token':'TEST-NOT-A-CREDENTIAL','files':{'sample.js':source or "cli({site:'addx-console',name:'sample',access:'"+access+"',func:async()=>[]});"},'commands':[['sample',flags or []]]}
class PolicyTests(unittest.TestCase):
 def setUp(self):
  self.original=subprocess.run;self.calls=[]
  self.test_home=tempfile.TemporaryDirectory();self.addCleanup(self.test_home.cleanup)
  self.home_patch=patch.object(pathlib.Path,'home',return_value=pathlib.Path(self.test_home.name));self.home_patch.start();self.addCleanup(self.home_patch.stop)
  def fake(args,**kwargs):
   self.calls.append((args,kwargs.get('env')))
   return types.SimpleNamespace(returncode=0,stdout='[]')
  subprocess.run=fake
 def tearDown(self):subprocess.run=self.original
 def execute(self,p):
  r=module.run(p);self.assertTrue(r['cleanup']['temporaryRuntimeRemoved']);self.assertTrue(r['cleanup']['ownedAdapterRemoved']);self.assertNotIn('token',p);return r
 def test_read_allowed(self):self.assertEqual(self.execute(payload())['status'],'FINISHED');self.assertEqual(len(self.calls),2)
 def test_explicit_write_dryrun_allowed(self):self.assertEqual(self.execute(payload('write',['--mode','dry-run']))['status'],'FINISHED');self.assertNotIn('CONSOLE_ALLOW_WRITE',self.calls[-1][1])
 def test_equal_mode_and_batch_prevalidation(self):
  self.assertEqual(self.execute(payload('write',['--mode=dry-run']))['status'],'FINISHED')
  p=payload();p['files']['danger.js']="cli({site:'addx-console',name:'danger',access:'write',func:()=>[]});";p['commands'].append(['danger',['--mode','submit']]);self.calls=[]
  self.assertEqual(self.execute(p)['status'],'POLICY_REJECTED');self.assertEqual(self.calls,[])
 def test_write_missing_submit_duplicate_mode_rejected(self):
  for flags in [[],['--mode','submit'],['--mode=dry-run','--mode','dry-run'],['--mode','dry-run','--mode=submit'],['--mode'],['--mode','dry-run','--','--mode','submit']]:
   self.calls=[];self.assertEqual(self.execute(payload('write',flags))['status'],'POLICY_REJECTED');self.assertEqual(self.calls,[])
 def test_missing_unknown_multiple_metadata_rejected(self):
  bad=["cli({site:'addx-console',name:'sample',access:ACCESS});","cli({site:'addx-console',name:'wrong',access:'read'});","cli({site:'addx-console',name:'sample',access:'read',access:'write'});","cli({site:'addx-console',name:'sample',access:'read'});cli({name:'other',access:'write'});","// cli({site:'addx-console',name:'sample',access:'read'});\nconst x=1;","cli({...metadata,name:'sample',access:'read'});"]
  for src in bad:
   self.calls=[];self.assertEqual(self.execute(payload(source=src))['status'],'POLICY_REJECTED');self.assertEqual(self.calls,[])
  p=payload();p['files']={};self.assertEqual(self.execute(p)['status'],'POLICY_REJECTED')
  p=payload();p['files']['sample.mjs']=p['files']['sample.js'];self.assertEqual(self.execute(p)['status'],'POLICY_REJECTED')
 def test_original_cleanup_failures(self):
  for mode in ['install-failure','install-timeout','namespace-collision']:
   p=payload();p['files']={};p['commands']=[];folder=pathlib.Path.home()/'.opencli/clis'/p['site']
   if mode=='namespace-collision':folder.mkdir(parents=True);(folder/'sentinel').write_text('test sentinel')
   def fail(*args,**kwargs):
    if mode=='install-timeout':raise subprocess.TimeoutExpired('npm',50)
    return types.SimpleNamespace(returncode=1)
   subprocess.run=fail
   try:
    r=self.execute(p);self.assertEqual(r['status'],{'install-failure':'INSTALL_FAILED','install-timeout':'TIMEOUT','namespace-collision':'NAMESPACE_EXISTS'}[mode])
    if mode=='namespace-collision':self.assertEqual((folder/'sentinel').read_text(),'test sentinel')
   finally:
    if folder.exists():import shutil;shutil.rmtree(folder)
 def test_cleanup_semantics_new_namespace_and_invalid(self):
  with tempfile.TemporaryDirectory() as home, patch.object(pathlib.Path,'home',return_value=pathlib.Path(home)):
   for mode in ['success','failure','timeout']:
    def injected(args,**kwargs):
     if mode=='timeout':raise subprocess.TimeoutExpired('npm',50)
     return types.SimpleNamespace(returncode=1 if mode=='failure' else 0,stdout='[]')
    subprocess.run=injected
    r=self.execute(payload());c=r['cleanup']
    self.assertEqual(c['preexistingAdapterExisted'],False)
    self.assertIsNone(c['preexistingAdapterPreserved'])
    self.assertEqual(c['preexistingAdapterPreservationStatus'],'NOT_APPLICABLE')
   p=payload();p['site']='../invalid';r=self.execute(p);c=r['cleanup']
   self.assertIsNone(c['preexistingAdapterExisted']);self.assertIsNone(c['preexistingAdapterPreserved'])
   self.assertEqual(c['preexistingAdapterPreservationStatus'],'NOT_APPLICABLE')
 def test_collision_preserves_entire_namespace(self):
  with tempfile.TemporaryDirectory() as home, patch.object(pathlib.Path,'home',return_value=pathlib.Path(home)):
   p=payload();folder=pathlib.Path(home)/'.opencli/clis'/p['site'];folder.mkdir(parents=True)
   (folder/'sample.js').write_bytes(b'original adapter');(folder/'nested').mkdir();(folder/'nested'/'sentinel').write_bytes(b'untouched')
   (folder/'link').symlink_to('nested/sentinel')
   def snapshot():return sorted((str(q.relative_to(folder)),q.read_bytes() if q.is_file() else None,q.is_symlink()) for q in folder.rglob('*'))
   before=snapshot();self.calls=[];r=self.execute(p)
   self.assertEqual(r['status'],'NAMESPACE_EXISTS');self.assertEqual(self.calls,[]);self.assertEqual(before,snapshot())
   self.assertTrue(r['cleanup']['preexistingAdapterExisted']);self.assertTrue(r['cleanup']['preexistingAdapterPreserved'])
   self.assertEqual(r['cleanup']['preexistingAdapterPreservationStatus'],'PRESERVED')
 def test_typed_exit_whitelist_and_secret_stderr(self):
  for code,category in [(66,'EMPTY_RESULT'),(75,'TIMEOUT'),(77,'AUTH_REQUIRED'),(69,'BROWSER_UNAVAILABLE'),(78,'CONFIG_ERROR'),(2,'ARGUMENT_ERROR'),(130,'INTERRUPTED'),(1,'EXECUTION_FAILED'),(999,'EXECUTION_FAILED')]:
   self.assertEqual(module.classify_opencli_exit(code),category)
   def fake(args,**kwargs):return types.SimpleNamespace(returncode=0 if args[0]=='npm' else code,stdout='SECRET-STDOUT',stderr='SECRET-STDERR')
   subprocess.run=fake;r=self.execute(payload());self.assertFalse(r['readsSucceeded']);self.assertEqual(r['reads'][0]['typedExitCategory'],category);self.assertTrue(r['reads'][0]['output']['failed']);self.assertNotIn('SECRET',json.dumps(r))
 def test_same_failure_not_success_and_expected_count(self):
  rows=[{'exitCode':66,'typedExitCategory':'EMPTY_RESULT','output':{'failed':True}}]
  s=module.acceptance_results_summary(rows,{'reads':rows,'status':'FINISHED'},1)
  self.assertTrue(s['sameResult']);self.assertFalse(s['allReadsSucceeded'])
  rows=[{'exitCode':0,'output':[]}];self.assertTrue(module.acceptance_results_summary(rows,{'reads':rows,'status':'FINISHED'},1)['allReadsSucceeded'])
  self.assertFalse(module.acceptance_results_summary(rows,{'reads':rows,'status':'FINISHED'},2)['allReadsSucceeded'])
class ImportClosureTests(PolicyTests):
 def test_missing_direct_import_before_runtime_or_credential(self):
  p=payload(source="import {x} from './expansion-records.mjs';cli({site:'addx-console',name:'sample',access:'read'});")
  self.assertEqual(self.execute(p)['status'],'POLICY_REJECTED');self.assertEqual(self.calls,[])
 def test_missing_transitive_and_reexport_rejected(self):
  for helper in ["import './missing.mjs';", "export {x} from './missing.mjs';", "export * from './missing.mjs';", "const x=import('./missing.mjs');", "const x=require('./missing.mjs');"]:
   p=payload(source="import './helper.mjs';cli({site:'addx-console',name:'sample',access:'read'});");p['files']['helper.mjs']=helper
   self.calls=[];self.assertEqual(self.execute(p)['status'],'POLICY_REJECTED');self.assertEqual(self.calls,[])
 def test_closed_cycle_builtins_external_and_comments_allowed(self):
  p=payload(source="import {cli} from '@jackwener/opencli/registry';import './a.mjs';cli({site:'addx-console',name:'sample',access:'read'});")
  p['files']['a.mjs']="import {createHash} from 'node:crypto';export * from './b.mjs';// import './missing.mjs';\nconst text=\"import './missing.mjs'\";"
  p['files']['b.mjs']="import './a.mjs';export const x=1;"
  self.assertEqual(self.execute(p)['status'],'FINISHED')
 def test_dynamic_computed_traversal_and_absolute_rejected(self):
  for statement in ["import(name);", "import(`./${name}.mjs`);", "require(name);", "import '../a.mjs';", "import '/tmp/a.mjs';", "import 'file:///tmp/a.mjs';", "import './a';"]:
   p=payload(source=statement+"cli({site:'addx-console',name:'sample',access:'read'});");self.calls=[]
   self.assertEqual(self.execute(p)['status'],'POLICY_REJECTED');self.assertEqual(self.calls,[])
 def test_literal_dynamic_present_allowed(self):
  p=payload(source="const helper=()=>import('./helper.mjs');cli({site:'addx-console',name:'sample',access:'read'});");p['files']['helper.mjs']='export const x=1;'
  self.assertEqual(self.execute(p)['status'],'FINISHED')
class NamespacePreflightTests(unittest.TestCase):
 def test_pure_namespace_preserves_existing_predicate(self):
  for value in ['addx-console-acceptance-case1','addx-console-acceptance-','addx-console-acceptance-中文']:
   self.assertEqual(module.validate_acceptance_namespace(value),value)
  for value in ['https://console.addx.live','../addx-console-acceptance-a','addx-console-acceptance-a/../b',None,23,{},'addx-console','addx-console-acceptance_a']:
   with self.assertRaises(module.PolicyRejected):module.validate_acceptance_namespace(value)
 def test_remote_invalid_status_cleanup_no_network(self):
  p=payload();p['site']='https://console.addx.live'
  with patch.object(module.subprocess,'run',side_effect=AssertionError('network forbidden')) as network:
   r=module.run(p)
  network.assert_not_called();self.assertNotIn('token',p)
  self.assertEqual(r['status'],'INVALID_NAMESPACE');self.assertFalse(r['readsSucceeded'])
  self.assertTrue(r['cleanup']['temporaryRuntimeRemoved']);self.assertIsNone(r['cleanup']['preexistingAdapterExisted'])
  self.assertEqual(r['cleanup']['preexistingAdapterPreservationStatus'],'NOT_APPLICABLE')
 def test_local_runners_reject_before_private_input_and_network(self):
  import io,runpy,termios,sys
  reports=pathlib.Path(__file__).parent/'fixtures'
  class NoPrivateInput(io.StringIO):
   def readline(self,*args):raise AssertionError('private input must not be read')
  for runner in ['console-read-batch.py','console-read-remote-only.py']:
   with tempfile.TemporaryDirectory() as temp:
    plan=pathlib.Path(temp)/'plan.json';plan.write_text(json.dumps({'site':'https://console.addx.live','files':[],'commands':[]}))
    out=io.StringIO()
    with patch.object(sys,'argv',[runner,str(plan)]),patch.object(sys,'stdin',NoPrivateInput()),patch.object(sys,'stdout',out),patch.object(termios,'tcgetattr',return_value=[0,0,0,0,0,0,[0]*32]),patch.object(termios,'tcsetattr'),patch.object(subprocess,'run',side_effect=AssertionError('network forbidden')) as network:
     with self.assertRaises(Exception) as rejected:runpy.run_path(str(reports/runner),run_name='offline_runner_test')
    self.assertEqual(type(rejected.exception).__name__,'PolicyRejected');network.assert_not_called();self.assertNotIn('READY_FOR_PRIVATE_INPUT',out.getvalue())
if __name__=='__main__':unittest.main()
