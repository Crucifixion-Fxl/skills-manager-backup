"""All-member is a harness mode, never a substitute for local authorization."""
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import unittest

TESTS = Path(__file__).resolve().parent
sys.path[:0] = [str(TESTS), str(TESTS.parent / 'scripts'), str(TESTS.parent / 'scripts' / 'hostd')]
import test_hostd_bot_approval_gate as gate
import test_hostd_worker_outlets as outlets
import bot_admission as admission
base = gate.base
setUpModule = base.setUpModule
tearDownModule = base.tearDownModule
PIN_KEY = '91' * 32
PIN = base.FGS._signer_pubkey(PIN_KEY)


class AllMemberOutlets(base.TmpCase):
    assembly = outlets.WorkerOutlets.assembly
    grant = gate.BotApprovalTests.grant
    approve = gate.BotApprovalTests.approve
    event = outlets.WorkerOutlets.event
    put = outlets.WorkerOutlets.put
    sends = outlets.WorkerOutlets.sends
    own_record = outlets.WorkerOutlets.own_record

    def setup_mode(self, configured=True):
        world, run, worker, spec = self.assembly()
        raw = '\n'.join(line for line in spec.env_file.read_text().splitlines()
                        if not line.startswith('BUZZ_ACP_CHANNELS=')) + '\nBUZZ_ACP_SUBSCRIBE=mentions\n'
        base.write_owner_only(spec.env_file, raw)
        if not configured:
            cfg = json.loads(worker.config.read_text()); cfg['agents'].pop(base.AGENT2_PK)
            base.write_owner_only(worker.config, json.dumps(cfg))
        worker.claim_relay_pubkey = PIN
        self.add_roster(world)
        return world, run, worker, spec

    def add_roster(self, world, *, removed=False, key=PIN_KEY):
        world.relay_events[:] = [e for e in world.relay_events if e['kind'] != 39002]
        members = [m for m in world.members if not (removed and m['pubkey'] == base.AGENT2_PK)]
        world.relay_events.append(base.FGS.sign_event(key, 39002,
            [['d', base.CHANNEL]] + [['p', m['pubkey'], '', m['role']] for m in members], '', int(base.NOW.timestamp()) - 1))

    def attempt(self, world, run, worker, *, expected):
        event = self.event(); self.put(world, event)
        worker.run({'buzz'})
        row = self.own_record(run, event)
        if expected:
            self.assertIsNotNone(row); self.assertEqual(row['status'], 'acked')
            self.assertEqual(len(self.sends(world)), 1)
        else:
            self.assertIsNone(row); self.assertFalse(self.sends(world))
            self.assertEqual(run.mapping_store.cursor_position('test', 'relay', agent_id=base.AGENT2_PK), 0)

    def test_explicit_legacy_all_member_own_worker_delivers(self):
        self.attempt(*self.setup_mode()[:3], expected=True)

    def test_done_exact_grant_all_member_worker_delivers(self):
        world, run, worker, _ = self.setup_mode(configured=False)
        self.grant(run); self.approve(run, worker, done=True)
        self.attempt(world, run, worker, expected=True)

    def test_catalog_and_poisoned_membership_without_local_authority_denied(self):
        self.attempt(*self.setup_mode(configured=False)[:3], expected=False)

    def test_approved_pregrant_cannot_borrow_all_member_mode(self):
        world, run, worker, _ = self.setup_mode(configured=False)
        run.mapping_store.register_agent(base.AGENT2_PK, owner_pubkey=base.OWNER_PK, app_id=outlets.own.APP, now=int(base.NOW.timestamp()))
        self.approve(run, worker)
        self.attempt(world, run, worker, expected=False)

    def test_negative_grant_wins_over_legacy(self):
        world, run, worker, _ = self.setup_mode(); self.grant(run, status='paused')
        self.attempt(world, run, worker, expected=False)

    def test_signed_roster_removal_is_not_repaired_from_native_invitation(self):
        world, run, worker, _ = self.setup_mode(); self.add_roster(world, removed=True)
        self.attempt(world, run, worker, expected=False)
        report = worker.run({'members'})
        self.assertEqual(report['agent_intros_sent'], 0)
        self.assertFalse([e for e in world.relay_writes if e['kind'] == 9000 and ['p', base.AGENT2_PK] in e['tags']])

    def test_wrong_roster_author_rejected(self):
        world, run, worker, _ = self.setup_mode(); self.add_roster(world, key=base.OWNER_KEY)
        self.attempt(world, run, worker, expected=False)

    def test_add_policy_does_not_revoke_existing_per_channel_authority(self):
        world, run, worker, _ = self.setup_mode()
        world.relay_events.append(base.FGS.sign_event(base.AGENT2_KEY, 10100, [],
            json.dumps({'channel_add_policy': 'anyone'}), int(base.NOW.timestamp())))
        self.attempt(world, run, worker, expected=True)

    def test_missing_pin_fails_closed(self):
        world, run, worker, _ = self.setup_mode()
        world.relay_events[:] = [e for e in world.relay_events if e['kind'] != 10100]
        worker.claim_relay_pubkey = None
        self.attempt(world, run, worker, expected=False)

    def test_no_10100_event_still_requires_local_per_channel_authority(self):
        world, run, worker, _ = self.setup_mode(configured=False)
        self.assertFalse([event for event in world.relay_events if event['kind'] == 10100])
        self.attempt(world, run, worker, expected=False)

    def test_global_owner_only_policy_is_not_a_local_channel_grant(self):
        world, run, worker, _ = self.setup_mode(configured=False)
        world.relay_events.append(base.FGS.sign_event(base.AGENT2_KEY, 10100, [],
            json.dumps({'channel_add_policy': 'owner_only'}), int(base.NOW.timestamp())))
        self.attempt(world, run, worker, expected=False)

    def test_root_candidate_rejects_catalog_only_and_negative_grant(self):
        from hostd.registry import Binding
        world, run, worker, spec = self.setup_mode(configured=False)
        cfg = run.cfg; app = cfg['agents'][cfg['desk_pubkey']]
        binding = Binding('test', worker.config, self.tmp / 'state', base.CHANNEL, base.CHAT,
            app['app_id'], app['lark_config_dir'], app['lark_data_dir'], 'https://relay.test')
        check = lambda: admission.channel_candidate(binding, spec, self.tmp / 'hostd.db', base.OWNER_PK, ('https://relay.test',))
        self.assertFalse(check())
        self.grant(run); self.approve(run, worker, done=True)
        self.assertTrue(check())
        self.grant(run, status='paused'); self.assertFalse(check())

    def test_explicit_mode_parser_does_not_treat_missing_empty_or_config_as_grant(self):
        for env in ({}, {'BUZZ_ACP_CHANNELS': ''}, {'BUZZ_ACP_SUBSCRIBE': 'config'},
                    {'BUZZ_ACP_SUBSCRIBE': 'mentions', 'BUZZ_ACP_CHANNELS': ''}):
            self.assertIsNone(admission.channel_mode(env, base.CHANNEL))
        for mode in ('mentions', 'all'):
            self.assertEqual(admission.channel_mode({'BUZZ_ACP_SUBSCRIBE': mode}, base.CHANNEL), 'all_member')

    def test_real_root_refresh_starts_one_authorized_feed_and_removes_revoked(self):
        import hostd.__main__ as hd
        from hostd.registry import Binding, Registry
        world, run, worker, spec = self.setup_mode()
        cfg = run.cfg; app = cfg['agents'][cfg['desk_pubkey']]
        binding = Binding('test', worker.config, self.tmp / 'state', base.CHANNEL, base.CHAT,
            app['app_id'], app['lark_config_dir'], app['lark_data_dir'], 'https://relay.test')
        reg = Registry(); reg.bindings['test'] = binding
        host = hd.Hostd(reg, self.tmp / 'status.json', state_db=self.tmp / 'hostd.db')
        host._running = True
        host.workers['test'] = worker
        host.onboarding = SimpleNamespace(catalog=SimpleNamespace(records=(spec,)),
            relay=SimpleNamespace(owner=base.OWNER_PK), effects=SimpleNamespace(specs={spec.pubkey: spec}))
        host.onboarding_config = SimpleNamespace(trusted_relays=('https://relay.test',))
        feeds = []
        async def follow(*args, **kwargs):
            feeds.append(args); await asyncio.Event().wait()
        async def check():
            try:
                with mock.patch.object(hd.relay_feed, 'follow', follow):
                    await host.refresh_outlets('test'); await asyncio.sleep(0)
                    await host.refresh_outlets('test'); await asyncio.sleep(0)
                    self.assertEqual(len(feeds), 1); self.assertIn(spec.pubkey, worker.outlet_specs)
                    self.grant(run, status='paused')
                    await host.refresh_outlets('test')
                    self.assertNotIn(spec.pubkey, worker.outlet_specs); self.assertFalse(host.outlet_tasks)
            finally:
                for task in host._tasks: task.cancel()
                await asyncio.gather(*host._tasks, return_exceptions=True)
                if host._round_executor is not None: host._round_executor.shutdown(wait=True)
        asyncio.run(check())


if __name__ == '__main__': unittest.main()
