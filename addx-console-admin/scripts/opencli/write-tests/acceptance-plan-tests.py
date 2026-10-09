import importlib.util,pathlib,tempfile,unittest
from unittest.mock import patch
source=pathlib.Path(__file__).parents[1]/'acceptance-plan.py'
spec=importlib.util.spec_from_file_location('planbuilder',source);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
NS='addx-console-acceptance-fixture'
REG="import {cli} from '@jackwener/opencli/registry';cli({site:'addx-console',name:'sample',access:'read'});"
class PlanTests(unittest.TestCase):
 def setUp(self):self.temp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.temp.name)
 def tearDown(self):self.temp.cleanup()
 def put(self,n,s):self.root.joinpath(n).write_text(s)
 def build(self,ns=NS,commands=None):return m.build_acceptance_plan(self.root,ns,commands or [['sample',['--limit','10']]])
 def test_multilevel_exact_closure_excludes_unrelated(self):
  self.put('sample.js',REG+"import './a.mjs';");self.put('a.mjs',"export {x} from './b.mjs';");self.put('b.mjs',"export const x=1;");self.put('unrelated.js','DONT_INCLUDE');p=self.build();self.assertEqual(p,{'site':NS,'files':['a.mjs','b.mjs','sample.js'],'commands':[['sample',['--limit','10']]]})
 def test_missing_dependency_rejected(self):
  self.put('sample.js',REG+"import './missing.mjs';")
  with self.assertRaises(Exception):self.build()
 def test_cycle_valid_closure(self):
  self.put('sample.js',REG+"import './a.mjs';");self.put('a.mjs',"import './b.mjs';");self.put('b.mjs',"import './a.mjs';");self.assertEqual(len(self.build()['files']),3)
 def test_invalid_namespace_before_adapter_read(self):
  with patch.object(pathlib.Path,'read_text',side_effect=AssertionError('must not read files')) as read:
   with self.assertRaises(Exception) as exc:self.build('https://console.addx.live')
  self.assertEqual(type(exc.exception).__name__,'PolicyRejected');read.assert_not_called()
 def test_dynamic_traversal_external_unknown_reject(self):
  for code in ["import(name);","import(\'./a.mjs\');","import(`./${name}.mjs`);","import '../a.mjs';","import '/tmp/a.mjs';","import 'unknown-package';"]:
   self.put('sample.js',REG+code)
   with self.assertRaises(Exception):self.build()
 def test_static_and_pinned_runtime_allowed(self):
  self.put('sample.js',REG+"import 'node:crypto';import './a.mjs';");self.put('a.mjs',"import {AuthRequiredError} from '@jackwener/opencli/errors';");self.assertEqual(len(self.build()['files']),2)
 def test_missing_ambiguous_source_and_write_reject(self):
  with self.assertRaises(Exception):self.build()
  self.put('sample.js',REG);self.put('sample.mjs',REG)
  with self.assertRaises(Exception):self.build()
  self.root.joinpath('sample.mjs').unlink();self.put('sample.js',REG.replace("access:'read'","access:'write'"))
  with self.assertRaises(Exception):self.build(commands=[['sample',['--mode','dry-run']]])
 def test_flags_not_credentials_or_environment(self):
  self.put('sample.js',REG);p=self.build();self.assertEqual(set(p),{'site','files','commands'});self.assertNotIn('DONT_INCLUDE',str(p))
  with self.assertRaises(Exception):self.build(commands=[['../sample',[]]])
 def test_dependency_symlink_escape_reject(self):
  self.put('sample.js',REG+"import './a.mjs';")
  with tempfile.TemporaryDirectory() as other:
   p=pathlib.Path(other)/'a.mjs';p.write_text('export const x=1;');self.root.joinpath('a.mjs').symlink_to(p)
   with self.assertRaises(Exception):self.build()
if __name__=='__main__':unittest.main()
