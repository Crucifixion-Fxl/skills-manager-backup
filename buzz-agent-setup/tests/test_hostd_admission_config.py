"""The effect gate must validate the same protected hostd config as the worker."""
import json
import test_hostd_worker_outlets as fixture
import test_hostd_bot_approval_gate as gate

base = fixture.base
setUpModule = fixture.setUpModule
tearDownModule = fixture.tearDownModule


class AdmissionConfig(base.TmpCase):
    assembly = fixture.WorkerOutlets.assembly
    event = fixture.WorkerOutlets.event
    put = fixture.WorkerOutlets.put
    sends = fixture.WorkerOutlets.sends
    own_record = fixture.WorkerOutlets.own_record
    grant = gate.BotApprovalTests.grant

    def configured(self):
        world, run, worker, spec = self.assembly()
        cfg = json.loads(worker.config.read_text())
        cfg['human_membership_sync'] = 'feishu_to_buzz'
        base.write_owner_only(worker.config, json.dumps(cfg))
        return world, run, worker, spec

    def test_supported_hostd_policy_allows_original_own_bot_delivery(self):
        world, run, worker, _ = self.configured()
        event = self.event(); self.put(world, event)
        worker.run({'buzz'})
        row = self.own_record(run, event)
        self.assertIsNotNone(row)
        self.assertEqual(row['status'], 'acked')
        self.assertEqual(len(self.sends(world)), 1)
        self.assertEqual(json.loads(worker.config.read_text())['human_membership_sync'], 'feishu_to_buzz')

    def test_supported_policy_cannot_override_negative_grant(self):
        world, run, worker, _ = self.configured()
        self.grant(run, status='paused')
        event = self.event(); self.put(world, event)
        worker.run({'buzz'})
        self.assertIsNone(self.own_record(run, event))
        self.assertFalse(self.sends(world))

    def test_unrecognized_policy_still_fails_before_any_send(self):
        world, run, worker, _ = self.configured()
        cfg = json.loads(worker.config.read_text()); cfg['human_membership_sync'] = 'anything'
        base.write_owner_only(worker.config, json.dumps(cfg))
        event = self.event(); self.put(world, event)
        with self.assertRaises(base.FGS.GroupSyncError):
            worker.run({'buzz'})
        self.assertIsNone(self.own_record(run, event))
        self.assertFalse(self.sends(world))
