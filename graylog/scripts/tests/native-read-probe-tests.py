import importlib.util,pathlib,unittest,json
p=pathlib.Path(__file__).parents[1]/'native-read-probe.py';spec=importlib.util.spec_from_file_location('probe',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
def responses(username='fixture-user'):
 return {'/api/system/sessions':(200,{'is_valid':True,'username':username,'session_id':'FIXTURE-SECRET'}),'/api/system':(200,{'version':'7.0.13','node_id':'fixture-node','hostname':'EXCLUDED'}),'/api/streams/paginated?page=1&per_page=5':(200,{'pagination':{'page':1,'per_page':5,'count':0,'total':0},'elements':[]})}
class Tests(unittest.TestCase):
 def run_fixture(self,r):
  calls=[]
  def fetch(path,headers):calls.append((path,headers));return r[path]
  return m.run_native_read_probe(fetch,'session','fixture-user'),calls
 def test_success_uses_only_three_fixed_read_paths(self):
  result,calls=self.run_fixture(responses());self.assertTrue(result['allReadsSucceeded']);self.assertEqual(len(calls),3);self.assertTrue(all(c[1]['X-Graylog-No-Session-Extension']=='true' for c in calls));self.assertNotIn('FIXTURE-SECRET',json.dumps(result));self.assertNotIn('EXCLUDED',json.dumps(result))
 def test_mismatch_or_invalid_200_stops_business_reads(self):
  for r in [responses('other'),{**responses(),'/api/system/sessions':(200,{'is_valid':False})}]:
   result,calls=self.run_fixture(r);self.assertFalse(result['allReadsSucceeded']);self.assertEqual(len(calls),1);self.assertEqual(result['system']['status'],'NOT_ATTEMPTED')
 def test_system403_keeps_identity_and_independent_streams_read(self):
  r=responses();r['/api/system']=(403,{'message':'FIXTURE-SECRET'});result,calls=self.run_fixture(r);self.assertTrue(result['identity']['identityVerified']);self.assertFalse(result['allReadsSucceeded']);self.assertEqual(result['streams']['status'],'READ_OK');self.assertEqual(len(calls),3);self.assertNotIn('FIXTURE-SECRET',json.dumps(result))
 def test_transport_failure_and_malformed_response_do_not_echo_exception(self):
  def bad(path,headers):raise RuntimeError('FIXTURE-SECRET')
  result=m.run_native_read_probe(bad,'session','fixture-user');self.assertFalse(result['allReadsSucceeded']);self.assertNotIn('FIXTURE-SECRET',json.dumps(result));self.assertEqual(result['identity']['status'],'TRANSPORT_FAILED')
 def test_token_mode_preserves_native_no_session_semantics(self):
  r=responses();r['/api/system/sessions']=(200,{'is_valid':False,'username':'fixture-user'})
  result=m.run_native_read_probe(lambda p,h:r[p],'access-token','fixture-user');self.assertTrue(result['allReadsSucceeded'])
if __name__=='__main__':unittest.main()
