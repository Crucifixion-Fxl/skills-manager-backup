"""Original feed, synthetic transport; no credentials/network/runtime services."""
import asyncio,json,sqlite3,types,unittest
from pathlib import Path
from unittest import mock
import test_hostd_relay_malformed as base

class FeedFailures(base.MalformedRelay):
 async def observed(self,phase,callback=None):
  sock=base.Socket([]);seen=[];statuses=[]
  if phase=='auth_wait':
   sock.frames=[lambda: (_ for _ in ()).throw(TimeoutError('SECRET challenge'))]
  original=sock.send
  async def send(raw):
   kind=json.loads(raw)[0]
   if (phase=='auth_send' and kind=='AUTH') or (phase=='subscribe' and kind=='REQ'):raise OSError('SECRET https://private/headers')
   await original(raw)
  sock.send=send
  async def event(*args):
   if phase=='catch_up':raise RuntimeError('SECRET callback payload')
  def replay():
   if phase=='replay':raise sqlite3.OperationalError('SECRET database path')
   return 0
  async def stop(delay):raise asyncio.CancelledError
  with mock.patch.object(base.relay,'_connect',return_value=sock),mock.patch.object(base.relay.asyncio,'sleep',side_effect=stop):
   with self.assertRaises(asyncio.CancelledError):
    await base.relay.follow('binding',str(self.env),'channel-a',event,lambda n,k,v:statuses.append(v),replay_since=replay,trusted_relays=('wss://relay.test',),on_failure=callback or (lambda n,k,d:seen.append(d)))
  return seen,statuses
 async def test_original_stage_and_redaction(self):
  for phase in ('replay','auth_wait','auth_send','subscribe','catch_up'):
   with self.subTest(phase=phase):
    seen,statuses=await self.observed(phase)
    self.assertEqual(len(seen),1);self.assertEqual(seen[0]['stage'],phase)
    self.assertEqual(set(seen[0]),{'stage','error_type','location'})
    self.assertNotIn('SECRET',json.dumps(seen));self.assertNotIn('private',json.dumps(seen))
    self.assertTrue(any(x['file']=='relay_feed.py' and x['function']=='follow' for x in seen[0]['location']))
    self.assertIn(base.relay.notice('reconnect'),statuses)
 async def test_metadata_callback_failure_preserves_original_reconnect(self):
  def failed(*args):raise RuntimeError('SECRET logger')
  _,statuses=await self.observed('replay',failed)
  self.assertIn(base.relay.notice('reconnect'),statuses)
 def test_untrusted_exception_class_and_frame_are_not_disclosed(self):
  SecretError=type('SECRET_CLASS',(Exception,),{})
  try:raise SecretError('SECRET_BODY')
  except Exception as error:doc=base.relay.failure_diagnostic('replay',error)
  self.assertEqual(doc['error_type'],'OtherError');self.assertEqual(doc['location'],[])
  self.assertNotIn('SECRET',json.dumps(doc))

import test_hostd_unit as unit
class WorkerFailures(unit.WorkerPhases):
 def test_business_errors_are_not_reported_as_generic_runtime_failures(self):
  for error,label in [(unit.FGS.GroupSyncError('SECRET body'),'GroupSyncError'),
                      (unit.FGS.CliError('SECRET',230027,'permission',definite=True),'CliError')]:
   doc=base.relay.failure_diagnostic('round_dispatch',error)
   self.assertEqual(doc['error_type'],label)
   self.assertTrue(base.relay.valid_diagnostic(doc))
   self.assertNotIn('SECRET',json.dumps(doc))
 def test_phase_error_keeps_verdict_retry_and_redacts_metadata(self):
  made,state,saves=self.patched()
  def failed(run):raise unit.FGS.GroupSyncError('SECRET keys/body/header')
  with mock.patch.object(unit.bw.dm.MappedHostdRound,'buzz_to_feishu',failed):
   w=unit.bw.Worker(config=unit.Path('cfg.json'),state_dir=unit.Path('state'))
   w.failure_formatter=base.relay.failure_diagnostic
   result=w.run({'buzz'})
  self.assertEqual(result['errors'],1);self.assertIn('buzz',result['hostd']['retry_phases'])
  self.assertEqual(result['hostd']['failure_diagnostics'][0]['stage'],'phase_buzz')
  self.assertNotIn('SECRET',json.dumps(result['hostd']['failure_diagnostics']))
 def test_formatter_failure_preserves_original_phase_error(self):
  self.patched()
  def failed(run):raise unit.FGS.GroupSyncError('SECRET')
  def formatter(*args):raise RuntimeError('SECRET logger')
  with mock.patch.object(unit.bw.dm.MappedHostdRound,'buzz_to_feishu',failed):
   w=unit.bw.Worker(config=unit.Path('cfg.json'),state_dir=unit.Path('state'));w.failure_formatter=formatter
   result=w.run({'buzz'})
  self.assertEqual(result['errors'],1);self.assertIn('buzz',result['hostd']['retry_phases'])
  self.assertEqual(result['hostd']['failure_diagnostics'],[])


class StatusFailures(unittest.TestCase):
 @staticmethod
 def http_doc(stage='round_dispatch'):
  return {'stage':stage,'error_type':'CliError','location':[],
          'http':{'stage':'headers','exception':'disconnected','phase':'api',
                  'http_status':-1,'api_code':-1,'elapsed_ms':17}}
 @staticmethod
 def host():
  # Load original Hostd class through the existing unit fixture's import seam.
  import importlib.util
  spec=importlib.util.spec_from_file_location('observer_hostd',unit.SCRIPTS/'hostd/__main__.py')
  module=importlib.util.module_from_spec(spec)
  with mock.patch.dict(unit.sys.modules,{'relay_feed':base.relay}):spec.loader.exec_module(module)
  obj=object.__new__(module.Hostd);obj.status={'bindings':{'binding':{}},'apps':{}};obj.save_status=mock.Mock()
  return obj
 def test_status_copies_fixed_schema_and_persistence_failure_is_noninterfering(self):
  host=self.host();doc={'stage':'auth_wait','error_type':'TimeoutError','location':[]}
  host.save_status.side_effect=OSError('SECRET status path')
  host.set_failure_diagnostic('binding','relay',doc)
  doc['location'].append({'SECRET':'payload'})
  self.assertEqual(host.status['bindings']['binding']['failure_diagnostics']['relay']['location'],[])
 def test_invalid_and_unbounded_diagnostics_never_enter_status(self):
  host=self.host()
  for doc in ({'stage':[],'error_type':'OtherError','location':[]},
       {'stage':'replay','error_type':'OtherError','location':[],'SECRET':'payload'},
       {'stage':'replay','error_type':'OtherError','location':[{}]*7}):
   host.set_failure_diagnostic('binding','relay',doc)
  self.assertEqual(host.status['bindings']['binding'],{});host.save_status.assert_not_called()
 def test_safe_http_metadata_survives_status_copy_without_aliasing(self):
  host=self.host();doc=self.http_doc()
  host.save_status.side_effect=OSError('SECRET path')
  host.set_failure_diagnostic('binding','round',doc)
  doc['http']['elapsed_ms']=999
  self.assertEqual(host.status['bindings']['binding']['failure_diagnostics']['round']['http']['elapsed_ms'],17)
  invalid=self.http_doc();invalid['http']['secret']='PRIVATE'
  host.set_failure_diagnostic('binding','relay',invalid)
  self.assertNotIn('relay',host.status['bindings']['binding']['failure_diagnostics'])


class OutletFailures(unittest.TestCase):
 def test_original_outlet_catch_records_only_fixed_metadata(self):
  pub='a'*64;owner='b'*64
  spec=types.SimpleNamespace(pubkey=pub,owner_pubkey=owner,status='own_bot_verified',app_id='cli_fixture',lark_config_dir='/fixture/config',lark_data_dir='/fixture/data',reader_app_id='cli_fixture',env_file='/fixture/env')
  worker=unit.bw.Worker(unit.Path('cfg'),unit.Path('state'));worker.failure_formatter=base.relay.failure_diagnostic
  worker.outlet_specs={pub:spec};worker.outlet_responsibilities={pub}
  run=types.SimpleNamespace(cfg={'agents':{},'desk_pubkey':'c'*64,'mirror_pubkey':'d'*64},other_mirrors=set())
  with mock.patch.object(unit.bw,'read_owned',side_effect=OSError('SECRET header/key')):
   result=worker._run_outlets(run,unit.base.NOW)
  self.assertEqual(result[pub]['status'],'pending');self.assertEqual(result[pub]['diagnostic']['stage'],'own_outlet')
  self.assertNotIn('SECRET',json.dumps(result[pub]['diagnostic']))
  self.assertTrue(any(row['file']=='binding_worker.py' and row['function']=='_run_outlets' for row in result[pub]['diagnostic']['location']))


class OutletSourceFailures(unittest.TestCase):
 def test_real_outlet_failure_exposes_location_without_body_or_context(self):
  import outlet
  adapter=object.__new__(outlet.AgentOutlet)
  # Invalid public envelope fails before touching any client, key or store.
  adapter._valid=lambda event:False
  try:adapter._deliver({'SECRET':'message body'},frozenset())
  except Exception as error:doc=base.relay.failure_diagnostic('own_outlet',error)
  self.assertTrue(base.relay.valid_diagnostic(doc))
  self.assertTrue(any(row['file']=='outlet.py' and row['function']=='_deliver' for row in doc['location']))
  self.assertNotIn('SECRET',json.dumps(doc))
  self.assertEqual(set(doc),{'stage','error_type','location'})
 def test_unknown_file_with_trusted_function_name_is_not_exposed(self):
  namespace={}
  exec(compile('def _deliver():\n raise RuntimeError("SECRET")','/private/outlet.py','exec'),namespace)
  try:namespace['_deliver']()
  except Exception as error:doc=base.relay.failure_diagnostic('own_outlet',error)
  self.assertEqual(doc['location'],[])
  self.assertNotIn('private',json.dumps(doc))


class RoundFailures(unittest.IsolatedAsyncioTestCase):
 async def test_round_copies_http_details_and_preserves_business_retry(self):
  host=StatusFailures.host();host.onboarding=None;host.notice_hints={'binding':set()};host.notice_overflow=set();host.notice_clock=lambda:0
  host._binding_status_store=mock.Mock();host.schedule_retry=mock.Mock();host.notify=mock.Mock()
  host._binding_state=mock.Mock(return_value='active')  # Pause admission is covered by the real-store lifecycle suites.
  host.dirty={'binding':set()};host.threads={'binding':set()};host.notice_retry_at={}
  host.status['bindings']['binding']['runs']=0
  host._round_slots=__import__('hostd.round_slots',fromlist=['RoundSlots']).RoundSlots(4);host._round_executor=None
  self.addCleanup(lambda:host._round_executor and host._round_executor.shutdown(wait=True))
  self.addCleanup(lambda:host._ledger_executor and host._ledger_executor.shutdown(wait=True))
  doc=StatusFailures.http_doc('own_outlet');invalid=StatusFailures.http_doc();invalid['http']['secret']='PRIVATE'
  report={'errors':1,'hostd':{'verdict':'won','retry_phases':['buzz'],'failure_diagnostics':[doc,invalid]}}
  host.workers={'binding':types.SimpleNamespace(run=lambda *args,**kw:report)}
  self.assertIs(await host._round('binding',{'buzz'},set()),report)
  stored=host.status['bindings']['binding']['worker_failure_diagnostics']
  self.assertEqual(stored,[doc]);doc['http']['elapsed_ms']=999
  self.assertEqual(stored[0]['http']['elapsed_ms'],17)
  host.schedule_retry.assert_called_once()
  host._binding_status_store.assert_called_once_with('binding','degraded')
 async def test_formatter_failure_preserves_original_round_exception(self):
  host=StatusFailures.host();host.onboarding=None;host.notice_hints={'binding':set()};host.notice_overflow=set();host.notice_clock=lambda:0
  host._binding_status_store=mock.Mock();host.schedule_retry=mock.Mock();host.notify=mock.Mock()
  host._binding_state=mock.Mock(return_value='active')  # Pause admission is covered by the real-store lifecycle suites.
  host.dirty={'binding':set()};host.threads={'binding':set()}
  host._round_slots=__import__('hostd.round_slots',fromlist=['RoundSlots']).RoundSlots(4);host._round_executor=None
  self.addCleanup(lambda:host._round_executor and host._round_executor.shutdown(wait=True))
  self.addCleanup(lambda:host._ledger_executor and host._ledger_executor.shutdown(wait=True))
  def failed(*args,**kwargs):raise OSError('SECRET original worker')
  host.workers={'binding':types.SimpleNamespace(run=failed)}
  relay=host._round.__func__.__globals__['relay_feed']
  with mock.patch.object(relay,'failure_diagnostic',side_effect=RuntimeError('SECRET logger')):
   with self.assertRaises(OSError):await host._round('binding',{'buzz'},set())
  host.schedule_retry.assert_called_once()
