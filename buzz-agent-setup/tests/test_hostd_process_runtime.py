"""Onboarding process proof: current invocation, strict status and safe dispatch."""
import subprocess
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from hostd.join_effects import AgentRuntime,AgentSpec,ProcessOps,gs

KEY='3'*64
PUB=gs._signer_pubkey(KEY)
OWNER=gs._signer_pubkey('2'*64)
CHANNEL='11111111-1111-4111-8111-111111111111'
SPEC=AgentSpec(PUB,OWNER,'cli_agent','/owned/agent.env','/owned/prompt','/owned/responsible',
               'owned.service','/owned/timer','/owned/state')

class Ops:
    def __init__(self,*,subscribed=True):
        self.pid=123;self.start=100;self.invocation='a'*32;self.restarts=0;self.busy=[]
        self.env={'BUZZ_PRIVATE_KEY':KEY,'BUZZ_ACP_AGENT_OWNER':OWNER,'BUZZ_ACP_SYSTEM_PROMPT_FILE':SPEC.prompt_file,
                  'BUZZ_RESPONSIBLE_CONFIG':SPEC.responsible_file,'BUZZ_ACP_CHANNELS':CHANNEL if subscribed else ''}
        self.current_log='subscribed to channel '+CHANNEL if subscribed else ''
        self.stale_log='subscribed to channel '+CHANNEL
        self.on_journal=None;self.restart_error=False;self.ack_only=False
    def unit_status(self,unit):return 'loaded','active'
    def process(self,unit):return self.pid,self.start,dict(self.env)
    def invocation_id(self,unit):return self.invocation
    def journal_process(self,unit,pid):return self.stale_log
    def journal_invocation(self,unit,pid,invocation):
        assert unit==SPEC.unit and pid==self.pid and invocation==self.invocation
        if self.on_journal:self.on_journal()
        return self.current_log
    def is_busy(self,unit):return self.busy.pop(0) if self.busy else False
    def restart(self,unit):
        self.restarts+=1
        if self.restart_error:raise subprocess.TimeoutExpired('fixed command',1,output='PRIVATE BODY KEY')
        if not self.ack_only:
            self.pid=456;self.start=200;self.invocation='b'*32
            self.env['BUZZ_ACP_CHANNELS']=CHANNEL;self.current_log='subscribed to channel '+CHANNEL

class RuntimeProof(unittest.TestCase):
    def test_existing_current_invocation_subscription_verifies_without_unnecessary_restart(self):
        ops=Ops();runtime=AgentRuntime(ops)
        self.assertTrue(runtime.verify(SPEC,CHANNEL));self.assertIs(runtime.activate_receipt(SPEC,CHANNEL),True)
        self.assertEqual(ops.restarts,0)
    def test_stale_pid_journal_cannot_replace_empty_current_invocation_journal(self):
        ops=Ops();ops.current_log=''
        self.assertFalse(AgentRuntime(ops).verify(SPEC,CHANNEL))
    def test_invocation_changes_during_journal_read_cannot_supply_receipt(self):
        ops=Ops();ops.on_journal=lambda:setattr(ops,'invocation','b'*32)
        self.assertFalse(AgentRuntime(ops).verify(SPEC,CHANNEL))
    def test_missing_or_invalid_invocation_proof_has_no_legacy_pid_fallback(self):
        for value in (None,'','0'*32,'not invocation'):
            with self.subTest(value=value):
                ops=Ops()
                if value is None:ops.invocation_id=None
                else:ops.invocation=value
                self.assertFalse(AgentRuntime(ops).verify(SPEC,CHANNEL))
    def test_wrong_old_key_owner_prompt_or_missing_pid_never_dispatches_restart(self):
        for change in ('key','owner','prompt','pid'):
            with self.subTest(change=change):
                ops=Ops(subscribed=False)
                if change=='key':ops.env['BUZZ_PRIVATE_KEY']='4'*64
                elif change=='owner':ops.env['BUZZ_ACP_AGENT_OWNER']='f'*64
                elif change=='prompt':ops.env['BUZZ_ACP_SYSTEM_PROMPT_FILE']='/not-owned/prompt'
                else:ops.pid=0
                self.assertIsNone(AgentRuntime(ops).activate_receipt(SPEC,CHANNEL))
                self.assertEqual(ops.restarts,0)
    def test_busy_second_check_and_unknown_idle_are_deferred_before_dispatch(self):
        for busy in ([False,True],[None],[False,None]):
            with self.subTest(busy=busy):
                ops=Ops(subscribed=False);ops.busy=list(busy)
                self.assertIsNone(AgentRuntime(ops).activate_receipt(SPEC,CHANNEL));self.assertEqual(ops.restarts,0)
    def test_owned_old_process_may_add_new_authorized_channel_after_idle_restart(self):
        ops=Ops(subscribed=False)
        self.assertIs(AgentRuntime(ops).activate_receipt(SPEC,CHANNEL),True);self.assertEqual(ops.restarts,1)
    def test_restart_ack_without_actual_current_subscription_stays_unknown(self):
        ops=Ops(subscribed=False);ops.ack_only=True
        self.assertIs(AgentRuntime(ops).activate_receipt(SPEC,CHANNEL),False);self.assertEqual(ops.restarts,1)
    def test_dispatched_timeout_returns_unknown_not_positive_idle_defer(self):
        ops=Ops(subscribed=False);ops.restart_error=True
        self.assertIs(AgentRuntime(ops).activate_receipt(SPEC,CHANNEL),False);self.assertEqual(ops.restarts,1)

    def test_native_prewarm_belongs_only_to_dispatched_activation_never_readback(self):
        ops=Ops(subscribed=False);signals=[]
        ops.native_subscription=lambda spec,channel,identity:bool(signals)
        def prewarm(spec,identity):
            self.assertEqual(ops.restarts,1)
            signals.append((identity[0],identity[2]))
        ops.prewarm=prewarm
        runtime=AgentRuntime(ops)
        self.assertFalse(runtime.verify(SPEC,CHANNEL));self.assertEqual(signals,[])
        self.assertTrue(runtime.activate_receipt(SPEC,CHANNEL))
        self.assertEqual(len(signals),1)
        self.assertTrue(runtime.verify(SPEC,CHANNEL));self.assertTrue(runtime.activate_receipt(SPEC,CHANNEL))
        self.assertEqual(ops.restarts,1);self.assertEqual(len(signals),1)

    def test_native_signal_ack_without_subscription_proof_remains_unknown(self):
        ops=Ops(subscribed=False);signals=[]
        ops.native_subscription=lambda *args:False
        ops.prewarm=lambda *args:signals.append(True)
        runtime=AgentRuntime(ops)
        self.assertFalse(runtime.activate_receipt(SPEC,CHANNEL))
        self.assertFalse(runtime.verify(SPEC,CHANNEL))
        self.assertEqual(signals,[True]);self.assertEqual(ops.restarts,1)

class ActualProcessAdapter(unittest.TestCase):
    def test_nonzero_partial_unit_status_duplicate_or_missing_fields_fail_closed(self):
        for code,text in ((1,'LoadState=loaded\nActiveState=active\n'),
                          (0,'LoadState=missing\nLoadState=loaded\nActiveState=active\n'),
                          (0,'LoadState=loaded\nActiveState=active\nPRIVATE BODY\n')):
            with self.subTest(code=code):
                def runner(argv,**kwargs):return subprocess.CompletedProcess(argv,code,text,'PRIVATE')
                self.assertEqual(ProcessOps(runner=runner,base_env={}).unit_status(SPEC.unit),('unknown','unknown'))
    def test_actual_invocation_journal_transport_uses_exact_unit_pid_invocation_and_short_deadline(self):
        calls=[]
        def runner(argv,**kwargs):
            calls.append((argv,kwargs));self.assertLessEqual(kwargs['timeout'],10)
            return subprocess.CompletedProcess(argv,0,'a'*32+'\n' if 'InvocationID' in argv else 'subscribed to channel '+CHANNEL,'')
        ops=ProcessOps(runner=runner,base_env={})
        self.assertEqual(ops.invocation_id(SPEC.unit),'a'*32)
        self.assertIn(CHANNEL,ops.journal_invocation(SPEC.unit,123,'a'*32))
        self.assertIn('_PID=123',calls[1][0]);self.assertIn('_SYSTEMD_INVOCATION_ID='+'a'*32,calls[1][0])
    def test_timeout_is_fixed_rule16_notice_without_raw_process_output(self):
        def runner(argv,**kwargs):raise subprocess.TimeoutExpired('PRIVATE COMMAND',1,output='PRIVATE BODY',stderr='PRIVATE KEY')
        with self.assertRaises(Exception) as caught:ProcessOps(runner=runner,base_env={}).unit_status(SPEC.unit)
        text=str(caught.exception);self.assertIn('怎么解决',text);self.assertIn('复制给 AI',text);self.assertNotIn('PRIVATE',text)
    def test_failed_invocation_and_journal_return_fixed_notice_without_body(self):
        def runner(argv,**kwargs):return subprocess.CompletedProcess(argv,1,'PRIVATE BODY','PRIVATE KEY')
        ops=ProcessOps(runner=runner,base_env={})
        for call in (lambda:ops.invocation_id(SPEC.unit),lambda:ops.journal_invocation(SPEC.unit,123,'a'*32)):
            with self.subTest(operation=call):
                with self.assertRaises(Exception) as caught:call()
                self.assertIn('怎么解决',str(caught.exception));self.assertNotIn('PRIVATE',str(caught.exception))

if __name__=='__main__':unittest.main()
