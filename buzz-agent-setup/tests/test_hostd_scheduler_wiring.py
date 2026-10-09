"""Actual CLI dispatch and Hostd workers share the same admission boundary."""
from pathlib import Path
import sys
import unittest
sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1] / 'scripts')]
import test_hostd_bot_clients as bots
import test_hostd_worker_store as worker_fixture
AdmissionError = bots.bc.AdmissionError


class Admission:
    def __init__(self, reject=False): self.calls, self.reject = [], reject
    def run(self, operation, **kwargs):
        self.calls.append(kwargs)
        if self.reject: raise AdmissionError()
        return operation()


class CliAdmission(bots.base.TmpCase):
    client = bots.ClientContracts.client
    def test_actual_cli_is_admitted_with_exact_app_chat_and_priority(self):
        client, runner = self.client([{'messages': [], 'has_more': False}])
        admission = Admission()
        client.scheduler = admission
        client.chat_id = bots.base.CHAT
        client.priority = 'backfill'
        client.messages(bots.base.CHAT, bots.base.NOW)
        self.assertEqual(len(runner.calls), 1)
        self.assertEqual(admission.calls, [{'app_id': bots.base.AGENT_APP, 'chat_id': bots.base.CHAT, 'priority': 'backfill', 'timeout': 90}])

    def test_cancelled_admission_is_definite_without_subprocess(self):
        client, runner = self.client([])
        client.scheduler = Admission(reject=True)
        with self.assertRaises(bots.bc.BotCliError) as caught:
            client.messages(bots.base.CHAT, bots.base.NOW)
        self.assertTrue(caught.exception.definite)
        self.assertEqual(runner.calls, [])


class WorkerAdmission(bots.base.TmpCase):
    assembly = worker_fixture.WorkerAssembly.assembly
    def test_every_real_round_client_uses_worker_shared_scheduler(self):
        _, cfg, world, _, factory = self.assembly()
        worker = factory()
        admission = Admission()
        worker.scheduler, worker.request_priority = admission, 'backfill'
        worker.run({'feishu'})
        self.assertGreater(len(admission.calls), 0)
        self.assertTrue(all(row['chat_id'] == cfg['chat_id'] and row['priority'] == 'backfill' for row in admission.calls))

    def test_hostd_supplies_one_process_scheduler_to_all_workers(self):
        env, cfg, _, db, _ = self.assembly()
        app = cfg['agents'][cfg['desk_pubkey']]
        registry = worker_fixture.registry
        reg = registry.Registry()
        reg.bindings['test-binding'] = registry.Binding('test-binding', env.config, env.state_dir,
            cfg['channel_id'], cfg['chat_id'], app['app_id'], app['lark_config_dir'], app['lark_data_dir'], 'https://relay.test')
        host = worker_fixture.hd.Hostd(reg, self.tmp / 'status.json', state_db=db)
        self.assertIs(host.workers['test-binding'].scheduler, host.scheduler)


if __name__ == '__main__': unittest.main()
