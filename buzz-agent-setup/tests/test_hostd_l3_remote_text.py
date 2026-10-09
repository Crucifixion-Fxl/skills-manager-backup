"""Three normal-CLI synthetic-service remote text contracts, no model/live L3.

Collect in Root's FULL frozen layout beside unchanged zero_local_fixture.py and
the separately reviewed remote_text_fixture.py. No direct Manager/Store seeds.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

TESTS = Path(__file__).resolve().parent
SCRIPTS = TESTS.parent/'scripts'
SOURCE = TESTS.parents[3]
MODULE = TESTS/'integration'/'hostd_l3'/'zero_local_run.py'


class NormalRemoteTextTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(MODULE.is_file(), 'missing distinct zero-local host-B run support')
        missing = []
        for name in ('cryptography', 'lark-oapi', 'websockets'):
            try:
                importlib.metadata.distribution(name)
            except importlib.metadata.PackageNotFoundError:
                missing.append(name)
        if shutil.which('git', path=os.defpath) is None:
            missing.append('git executable on fixed PATH')
        if missing:
            raise unittest.SkipTest(
                'normal remote text requires actual dependencies: '+', '.join(missing)+
                '; ordinary CI skip is not proof: mandatory actualdeps must execute '
                'ALL THREE methods with zero failures, errors and skips')
        previous = sys.dont_write_bytecode
        sys.dont_write_bytecode = True
        self.addCleanup(setattr, sys, 'dont_write_bytecode', previous)
        for path in (TESTS, SCRIPTS):
            if str(path) not in sys.path: sys.path.insert(0, str(path))
        spec = importlib.util.spec_from_file_location('hostd_l3_remote_text_candidate', MODULE)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        self.module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = self.module
        self.addCleanup(sys.modules.pop, spec.name, None)
        spec.loader.exec_module(self.module)
        for name in ('ZeroLocalRunPlan','OwnedZeroLocalRun','ZeroLocalRunError'):
            self.assertTrue(callable(getattr(self.module, name, None)))
        import zero_local_fixture as original
        import remote_text_fixture as wire
        import remote_text_diagnostic as diagnostic
        self.original, self.wire, self.diagnostic = original, wire, diagnostic
        if 'HOSTD_TEST_DIAGNOSTIC_DIR' not in os.environ:
            diagnostic_parent = Path.home()/'.codex'/'hostd-normal-remote-text-diagnostics'
            diagnostic_parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.assertFalse(diagnostic_parent.is_symlink())
            metadata = diagnostic_parent.stat()
            self.assertEqual(metadata.st_uid, os.geteuid())
            self.assertEqual(metadata.st_mode & 0o777, 0o700)
            diagnostics = tempfile.TemporaryDirectory(prefix='collection-', dir=diagnostic_parent)
            self.addCleanup(diagnostics.cleanup)
            os.environ['HOSTD_TEST_DIAGNOSTIC_DIR'] = diagnostics.name
            self.addCleanup(os.environ.pop, 'HOSTD_TEST_DIAGNOSTIC_DIR', None)
        temporary = tempfile.TemporaryDirectory(prefix='hostd-normal-remote-text-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.root.chmod(0o700)
        actual = subprocess.run(['git','-C',str(SOURCE),'rev-parse','HEAD'], check=True,
            capture_output=True,text=True,timeout=5,env={'PATH':os.defpath,'LANG':'C.UTF-8'})
        self.revision = actual.stdout.strip()
        self.assertRegex(self.revision, r'^[0-9a-f]{40}$')
        self.source_hashes = {str(p.relative_to(SOURCE)): original.digest(p)
                              for p in sorted(SCRIPTS.rglob('*.py'))}

    def case(self, **options):
        case = self.original.Fixture(self.root, SOURCE, self.revision, dict(self.source_hashes))
        self.addCleanup(case.close)
        world = self.wire.RemoteTextFixture(case, self.original, **options)
        plan = self.module.ZeroLocalRunPlan.check(case.manifest, **case.check_args)
        self.assertIs(type(plan), self.module.ZeroLocalRunPlan)
        prepared = plan.readback()
        self.assertEqual(prepared['status'], 'prepared')
        self.assertEqual((prepared['registry_bindings'],prepared['target_channel_bindings']), (0,0))
        self.assertEqual(prepared['registered_agent_pubkeys'], [])
        self.assertFalse(prepared['runtime_started'])
        self.assertFalse(prepared['live_verified'])
        self.assertTrue(all(not p.exists() for p in case.outputs))
        self.assertFalse((case.home/'.local').exists())
        self.assertEqual(plan.argv(), [str(Path(sys.executable).absolute()),'-m','hostd','run',
            '--only',case.doc['selector'],'--onboarding-config',str(case.onboarding),
            '--state-db',str(case.outputs[0]),'--status-file',str(case.outputs[1]),
            '--console-dir',str(case.outputs[2])])
        self.assertNotEqual(case.owner, case.source_owner)
        return case, world, plan

    def poll(self, capture, predicate, *, seconds, message):
        deadline = time.monotonic()+seconds
        with capture.diagnostics.operation('poll', capture):
            while time.monotonic() < deadline:
                self.assertIsNone(capture.process.poll(), 'normal root exited before bounded evidence')
                self.assertEqual(self.original.process_identity(capture.process.pid), capture.identity)
                capture.observed_members |= self.original.session_members(capture.process.pid)
                capture.diagnostics.sample(capture)
                value = predicate()
                if value: return value
                time.sleep(.05)
            self.fail(message)

    def own_registered(self, case, capture):
        if not case.outputs[0].is_file() or not case.outputs[1].is_file(): return False
        rows,target,agents = self.original.db_readback(case.outputs[0])
        self.assertEqual((rows,target), ([],[]))
        expected = [(case.agent_pub,case.owner,'cli_agent','active')]
        status = json.loads(case.outputs[1].read_text())
        sdk = [str(Path(sys.executable).absolute()),str(SCRIPTS/'hostd'/'feishu_feed.py'),
               'cli_agent',str(case.cfg),str(case.data)]
        sdk_present = False
        for member in self.original.session_members(capture.process.pid):
            try: argv = Path(f'/proc/{member[0]}/cmdline').read_bytes().decode().strip('\0').split('\0')
            except FileNotFoundError: continue
            if argv == sdk: sdk_present = True
        return agents == expected and status.get('bindings') == {} and 'cli_agent' in status.get('apps', {}) and sdk_present

    def assert_inputs(self, case, original):
        snapshot = case.snapshot()
        for relative,value in original.items(): self.assertEqual(snapshot[relative], value, relative)
        self.assertEqual(self.source_hashes, {str(p.relative_to(SOURCE)):self.original.digest(p)
                                            for p in sorted(SCRIPTS.rglob('*.py'))})

    @contextmanager
    def normal(self, case, world, plan):
        original = case.snapshot()
        sibling = self.diagnostic.sibling_canary()
        sibling_identity = self.original.process_identity(sibling.pid)
        self.addCleanup(self.diagnostic.reap_sibling, sibling)
        self.assertIsNotNone(sibling_identity)
        capture = self.diagnostic.DiagnosticCapturePopen(case, plan, world, sibling_identity)
        self.addCleanup(capture.emergency_reap)  # Failure-only cleanup, no pass receipt.
        owned = self.module.OwnedZeroLocalRun(plan, popen_factory=capture)
        capture.diagnostics.owned = owned  # Readonly already observed identity metadata.
        case.relay.start()
        try:
            self.assertEqual(owned.start()['status'], 'running')
            capture.diagnostics.note('normal.started', capture)
            self.poll(capture, lambda:self.own_registered(case,capture),seconds=30,
                      message='normal root did not register actual own agent and SDK child')
            observed = owned.readback()
            capture.diagnostics.note('normal.readback', capture, sql=True)
            self.assertEqual(observed['registered_agent_pubkeys'], [case.agent_pub])
            self.assertEqual(observed['registered_agent_app_ids'], ['cli_agent'])
            self.assertEqual((observed['registry_bindings'],observed['target_channel_bindings']), (0,0))
            self.assertFalse(observed['live_verified'])
            yield capture
            self.assertEqual(world.snapshot()['failures'], [])
            plan.revalidate(phase='running')
            self.assert_inputs(case,original)
            self.assertIsNone(sibling.poll())
            self.assertEqual(self.original.process_identity(sibling.pid),sibling_identity)
        finally:
            # Persist the propagating exception BEFORE unchanged stop can mask it.
            capture.diagnostics.note('normal.exception_before_stop', capture,
                                     exc_info=sys.exc_info(), sql=True)
            with capture.diagnostics.operation('stop', capture, sql=True):
                stopped = owned.stop(timeout=10)  # Original ROOT SIGINT; real descendants join.
        self.assertEqual(stopped['status'],'stopped')
        self.assertTrue(stopped['child_reaped'])
        self.assertTrue(stopped['session_reaped'])
        self.assertIsNotNone(capture.process.poll())
        self.assertEqual(self.original.session_members(capture.process.pid),set())
        for original_identity in capture.observed_members:
            self.assertNotEqual(self.original.process_identity(original_identity[0]),original_identity)
        self.assertIsNone(sibling.poll())
        self.assertEqual(self.original.process_identity(sibling.pid),sibling_identity)
        self.assertEqual(owned.stop(timeout=10),stopped)
        self.assert_inputs(case,original)
        rows,target,agents = self.original.db_readback(case.outputs[0])
        self.assertEqual((rows,target),( [],[]))
        self.assertEqual(agents,[(case.agent_pub,case.owner,'cli_agent','active')])

    def assert_grant(self, state, case, world):
        self.assertIsNotNone(state)
        self.assertEqual(state['binding'],[])
        self.assertEqual(len(state['remote_target']),1)
        self.assertEqual(len(state['remote_grant']),1)
        target,grant = state['remote_target'][0],state['remote_grant'][0]
        self.assertEqual((target['agent_id'],target['owner_pubkey'],target['app_id'],target['channel_id'],
                          target['chat_id'],target['chat_ref'],target['relay_origin'],target['status']),
                         (case.agent_pub,case.owner,'cli_agent',self.wire.CHANNEL,world.chat,
                          world.gs.chat_ref(world.chat),case.relay.origin,'active'))
        self.assertEqual((target['target_id'],target['current_revision']), (grant['target_id'],grant['revision']))
        expected_target_id = hashlib.sha256(self.wire.encoded([case.agent_pub,self.wire.CHANNEL]).encode()).hexdigest()
        self.assertEqual(grant['target_id'],expected_target_id)
        self.assertEqual(grant['revision'],1)
        self.assertRegex(grant['scope_hash'],r'^[0-9a-f]{64}$')
        self.assertEqual((grant['mirror_pubkey'],grant['mirror_owner_pubkey'],grant['claimed_at'],
                          grant['claim_event_id'],grant['agent_profile_event_id'],grant['agent_policy_event_id'],
                          grant['roster_event_id'],grant['approval_kind'],grant['approval_id'],
                          grant['approval_hash'],grant['capabilities']),
                         (world.mirror,case.source_owner,world.claimed,world.mirror_policy_id,
                          world.event_ids[0],world.event_ids[30177],world.event_ids[39002],
                          'mirror_approval',world.approval['id'],world.approval_hash,1))
        allowlist_hash = hashlib.sha256(self.wire.encoded([self.wire.CHANNEL]).encode()).hexdigest()
        self.assertEqual(grant['allowlist_hash'],allowlist_hash)
        expected_scope = dict(agent_id=case.agent_pub,owner_pubkey=case.owner,app_id='cli_agent',
            channel_id=self.wire.CHANNEL,chat_id=world.chat,chat_ref=world.gs.chat_ref(world.chat),
            relay_origin=case.relay.origin,mirror_pubkey=world.mirror,mirror_owner_pubkey=case.source_owner,
            allowlist_hash=allowlist_hash,approval_kind='mirror_approval',approval_id=world.approval['id'],
            approval_hash=world.approval_hash,capabilities=['message'],claimed_at=world.claimed)
        self.assertEqual(grant['scope_hash'],hashlib.sha256(self.wire.encoded(expected_scope).encode()).hexdigest())
        return grant

    def assert_ack(self, state, case, world):
        grant = self.assert_grant(state,case,world)
        self.assertEqual(len(state['remote_delivery']),1)
        delivery = state['remote_delivery'][0]
        self.assertEqual((delivery['source_id'],delivery['action'],delivery['root_id'],delivery['status'],
                          delivery['sender_app_id'],delivery['message_id']),
                         (world.reply['id'],'message',world.root_event['id'],'acked','cli_agent',world.sent_mid))
        self.assertEqual((delivery['target_id'],delivery['revision'],delivery['scope_hash']),
                         (grant['target_id'],grant['revision'],grant['scope_hash']))
        captured = world.snapshot()
        self.assertEqual(len(captured['posts']),1)
        self.assertEqual(len(captured['unknown_before_post']),1)
        posted = captured['posts'][0]
        self.assertEqual(posted['path'],'/open-apis/im/v1/messages/'+world.root_mid+'/reply')
        self.assertEqual(posted['delivery']['status'],'unknown')
        self.assertEqual(posted['delivery']['id'],delivery['id'])
        self.assertEqual(delivery['content_hash'],hashlib.sha256(posted['data']['content'].encode()).hexdigest())
        self.assertEqual(captured['messages'][world.sent_mid]['body']['content'],posted['data']['content'])
        self.assertIn(world.root_mid,captured['exact_gets'])
        self.assertIn(world.sent_mid,captured['exact_gets'])
        self.assertRegex(delivery['receipt_hash'],r'^[0-9a-f]{64}$')
        post_order = next(i for i,k,v in captured['trace'] if k == 'physical_post_persisted')
        self.assertTrue(any(i > post_order and k == 'receipt_get_valid' and v == world.sent_mid
                            for i,k,v in captured['trace']))
        self.assertTrue(any(q['principal'] == case.agent_pub and any(f.get('kinds') == [30078]
                            for f in q['filters']) for q in captured['signed_queries']))
        self.assertTrue(any(q['principal'] == case.agent_pub and any(f.get('ids') == [world.reply['id']]
                            for f in q['filters']) for q in captured['signed_queries']))
        self.assertEqual(captured['failures'],[])
        return grant,delivery

    def acked(self, case, world):
        state = self.wire.readonly(case.outputs[0])
        return state if state and any(r['source_id'] == world.reply['id'] and r['status'] == 'acked'
                                     for r in state['remote_delivery']) else None

    def test_normal_signed_foreign_approval_and_b_reply_reach_native_unknown_post_get_ack(self):
        case,world,plan = self.case()
        with self.normal(case,world,plan) as capture:
            state = self.poll(capture,lambda:self.acked(case,world),seconds=90,
                              message='actual background remote dispatch did not ACK signed reply')
            grant,delivery = self.assert_ack(state,case,world)
            before = (dict(grant),dict(delivery))
            # Native immutable source/approval remains sealed, all effects stay
            # in the actual root Store and owned synthetic physical service.
            self.assertEqual(world.gs._nip01_event_verified(world.approval),True)
            self.assertEqual(world.gs._nip01_event_verified(world.reply),True)
            self.assertEqual(world.gs._nip01_event_verified(world.root_event),True)
            self.assertEqual(world.original.digest(world.wire_path),world.wire_hash)
            self.assertEqual(len(world.snapshot()['posts']),1)
        stopped_state = self.wire.readonly(case.outputs[0])
        self.assertEqual((stopped_state['remote_grant'][0],stopped_state['remote_delivery'][0]),before)

    def test_missing_approval_or_incomplete_native_scopes_create_no_grant_or_send(self):
        for denial in ('missing_approval','incomplete_scopes'):
            with self.subTest(denial=denial):
                case,world,plan = self.case(denial=denial)
                with self.normal(case,world,plan) as capture:
                    def denied_attempts():
                        captured = world.snapshot()
                        scopes = sum(method == 'GET' and path == '/open-apis/application/v6/scopes'
                                     for method,path,params in captured['native_calls'])
                        approvals = sum(q['principal'] == case.agent_pub and any(f.get('kinds') == [30078]
                                        for f in q['filters']) for q in captured['signed_queries'])
                        # At least two real background attempts, including the
                        # unchanged periodic fallback; no fabricated pending DTO.
                        enough = approvals >= 2 and scopes >= 1 if denial == 'missing_approval' else scopes >= 2
                        state = self.wire.readonly(case.outputs[0])
                        if state:
                            self.assertEqual(state['binding'],[])
                            self.assertEqual(state['remote_target'],[])
                            self.assertEqual(state['remote_grant'],[])
                            self.assertEqual(state['remote_delivery'],[])
                        self.assertEqual(captured['posts'],[])
                        self.assertEqual(captured['failures'],[])
                        return enough
                    self.poll(capture,denied_attempts,seconds=105,
                              message='finite negative fixture did not exercise two actual background attempts')
                    state = self.wire.readonly(case.outputs[0])
                    self.assertEqual((state['remote_target'],state['remote_grant'],state['remote_delivery']),([],[],[]))
                    self.assertFalse(world.posted.is_set())
                self.assertEqual(world.snapshot()['posts'],[])

    def test_persisted_post_lost_response_and_blocked_get_recover_original_unknown_with_one_post(self):
        case,world,plan = self.case(ambiguous=True)
        with self.normal(case,world,plan) as capture:
            self.poll(capture,world.posted.is_set,seconds=90,
                      message='native fixture did not physically persist actual POST')
            first = self.wire.readonly(case.outputs[0])
            original_grant = dict(self.assert_grant(first,case,world))
            self.assertEqual(len(first['remote_delivery']),1)
            original_delivery = dict(first['remote_delivery'][0])
            self.assertEqual(original_delivery['status'],'unknown')
            self.assertEqual(original_delivery['root_id'],world.root_event['id'])
            self.assertIsNone(original_delivery['message_id'])
            self.poll(capture,world.blocked_get.is_set,seconds=75,
                      message='actual UNKNOWN recovery did not query exact blocked native receipt')
            blocked = self.wire.readonly(case.outputs[0])
            self.assertEqual(blocked['remote_grant'],[original_grant])
            self.assertEqual(blocked['remote_delivery'],[original_delivery])
            captured = world.snapshot()
            self.assertEqual(len(captured['posts']),1)
            self.assertTrue(captured['list_reads'])
            self.assertEqual(captured['messages'][world.sent_mid]['body']['content'],captured['posts'][0]['data']['content'])
            world.release_get.set()  # Native service availability only; no proof/callback/DB mutation.
            recovered = self.poll(capture,lambda:self.acked(case,world),seconds=75,
                                  message='UNKNOWN did not recover by native GET using original grant')
            grant,delivery = self.assert_ack(recovered,case,world)
            self.assertEqual(grant,original_grant)
            for name in ('id','target_id','source_id','action','revision','scope_hash','source_at','content_hash','root_id','created_at'):
                self.assertEqual(delivery[name],original_delivery[name])
            self.assertEqual(len(world.snapshot()['posts']),1)
            trace = world.snapshot()['trace']
            unavailable = next(i for i,k,v in trace if k == 'receipt_get_blocked' and v == world.sent_mid)
            self.assertTrue(any(i > unavailable and k == 'receipt_get_valid' and v == world.sent_mid for i,k,v in trace))
        self.assertEqual(len(world.snapshot()['posts']),1)
