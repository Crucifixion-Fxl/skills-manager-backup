import subprocess,json,unittest,pathlib,os
HARNESS=pathlib.Path(__file__).parent/'fixtures/minimum-firmware-template-harness.mjs'
class TemplateTests(unittest.TestCase):
 def execute(self,args):
  env={'PATH':os.environ['PATH'],'LANG':'C.UTF-8','NODE_NO_WARNINGS':'1'}
  return subprocess.run(['node','--experimental-vm-modules',str(HARNESS),json.dumps(args)],env=env,capture_output=True,text=True,timeout=15)
 def test_checkout_registered_template_handles_internal_option_sources(self):
  p=self.execute(['--action','template','--mode','dry-run']);self.assertEqual(p.returncode,0)
  result=json.loads(p.stdout);rows=result['rows'];self.assertEqual(rows[0]['status'],'TEMPLATE_ONLY_NOT_A_PLAN');self.assertFalse(rows[0]['submissionImplemented']);self.assertEqual(result['fetchCalls'],0);self.assertTrue(result['consumerCalled']);self.assertEqual(result['scope'],'OFFLINE_REGISTRY_HYDRATION_NOT_REAL_CLI')
 def test_registry_unknown_action_or_submit_refused_before_consumer(self):
  for args in [['--action','unknown'],['--mode','submit']]:
   p=self.execute(args);self.assertNotEqual(p.returncode,0);result=json.loads(p.stdout);self.assertFalse(result['consumerCalled']);self.assertEqual(result['fetchCalls'],0)
if __name__=='__main__':unittest.main()
