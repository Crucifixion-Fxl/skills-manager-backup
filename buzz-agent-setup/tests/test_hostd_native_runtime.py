"""Native subscription proof: real process/birth/executable, private journals."""
import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import test_recovery_controller as fixture
from recovery_controller import read_env
from hostd import native_runtime


class NativeProof(unittest.TestCase):
    write=fixture.ControllerTests.write
    write_snapshot=fixture.ControllerTests.write_snapshot

    def setUp(self):
        fixture.ControllerTests.setUp(self)
        self.addCleanup(fixture.ControllerTests.tearDown,self)
        self.spec=SimpleNamespace(pubkey=fixture.AGENT,owner_pubkey=fixture.OWNER,
            env_file=str(self.agent_env),prompt_file='/owned/prompt',responsible_file='/owned/people')
        extra=('BUZZ_ACP_SYSTEM_PROMPT_FILE=/owned/prompt\nBUZZ_RESPONSIBLE_CONFIG=/owned/people\n'
               'BUZZ_ACP_CHANNELS='+','.join(self.current['channels'])+'\n')
        self.write(self.agent_env,self.agent_env.read_text()+extra)
        self.actual=read_env(self.agent_env)
        self.identity=(self.pid,int(self.current['process_start_ticks']),'a'*32,self.actual)
        self.channel=self.current['channels'][0]

    def test_exact_native_ready_proves_subscriptions_without_log_or_mutation(self):
        before={p:p.read_bytes() for p in self.base.rglob('*') if p.is_file()}
        self.assertIs(native_runtime.subscribed(self.spec,self.channel,self.identity),True)
        self.assertEqual(before,{p:p.read_bytes() for p in self.base.rglob('*') if p.is_file()})

    def test_launcher_supplied_directory_is_read_from_exact_process_environment(self):
        raw=self.agent_env.read_text();self.write(self.agent_env,'\n'.join(l for l in raw.splitlines() if not l.startswith('BUZZ_ACP_RECOVERY_DIR='))+'\n')
        self.assertTrue(native_runtime.subscribed(self.spec,self.channel,self.identity))
        self.actual['BUZZ_ACP_RECOVERY_DIR']=str(self.base/'missing')
        self.assertFalse(native_runtime.subscribed(self.spec,self.channel,self.identity))

    def test_idle_lazy_pool_keeps_exact_same_process_subscription_proof(self):
        self.write_snapshot(dict(self.current, phase='starting'))
        before={p:p.read_bytes() for p in self.base.rglob('*') if p.is_file()}
        self.assertTrue(native_runtime.subscribed(self.spec,self.channel,self.identity))
        self.assertEqual(before,{p:p.read_bytes() for p in self.base.rglob('*') if p.is_file()})
        self.write_snapshot(dict(self.current, phase='stopping'))
        self.assertFalse(native_runtime.subscribed(self.spec,self.channel,self.identity))

    def test_sleeping_pool_partial_subscription_cannot_prove_added_allowlist(self):
        other='11111111-1111-4111-8111-111111111111'
        self.write(self.agent_env,self.agent_env.read_text().replace(
            'BUZZ_ACP_CHANNELS='+','.join(self.current['channels']),
            'BUZZ_ACP_CHANNELS='+','.join([*self.current['channels'],other])))
        self.actual['BUZZ_ACP_CHANNELS']=','.join([*self.current['channels'],other])
        self.write_snapshot(dict(self.current,phase='starting'))
        self.assertFalse(native_runtime.subscribed(self.spec,self.channel,self.identity))

    def test_starting_wrong_channels_owner_process_binary_and_revision_are_not_ready(self):
        original=copy.deepcopy(self.current)
        for update in ({'phase':'starting','channels':[]},{'channels':[]},
                       {'runtime_policy':dict(original['runtime_policy'],owner='0'*64)},
                       {'process_start_ticks':'0'}):
            with self.subTest(update=update):
                self.write_snapshot(dict(original,**update))
                self.assertFalse(native_runtime.subscribed(self.spec,self.channel,self.identity))
        self.write_snapshot(original)
        for key,value in (('BUZZ_ACP_BINARY_SHA256','0'*64),('BUZZ_ACP_RECOVERY_REVISION','b'*40)):
            old=self.actual[key];self.actual[key]=value
            self.assertFalse(native_runtime.subscribed(self.spec,self.channel,self.identity));self.actual[key]=old

    def test_duplicate_live_generation_or_unreadable_history_blocks_native_proof(self):
        duplicate=dict(self.current,generation='00000000-0000-0000-0000-000000000097')
        self.write_snapshot(duplicate)
        self.assertFalse(native_runtime.subscribed(self.spec,self.channel,self.identity))
        (self.runtime/(duplicate['generation']+'.json')).unlink()
        self.write(self.runtime/(self.old['generation']+'.json'),'{incomplete')
        self.assertFalse(native_runtime.subscribed(self.spec,self.channel,self.identity))

    def test_new_channel_not_in_actual_snapshot_and_config_drift_never_pass(self):
        self.assertFalse(native_runtime.subscribed(self.spec,'11111111-1111-4111-8111-111111111111',self.identity))
        self.actual['BUZZ_ACP_CHANNELS']=''
        self.assertFalse(native_runtime.subscribed(self.spec,self.channel,self.identity))
