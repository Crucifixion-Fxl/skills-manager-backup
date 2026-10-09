"""Worker assembly exercises the actual bot boundary and SQLite checkpoint."""
import copy
import json
from pathlib import Path
import sys
import unittest

TESTS = Path(__file__).resolve().parent
SCRIPTS = TESTS.parent / 'scripts'
sys.path[:0] = [str(TESTS), str(SCRIPTS), str(SCRIPTS / 'hostd')]
import test_buzz_feishu_group_sync as base
import test_hostd_bot_clients as bot_fixture
import test_hostd_delivery_mapping as mapping_fixture
import binding_worker as bw
import registry
from test_hostd_wiring_lifecycle import hd
from store import Store
from state_store import StateAdapter


class WorkerAssembly(base.TmpCase):
    def assembly(self):
        env, cfg, _, _ = bot_fixture.RoundAssembly.assembly(self)
        world = mapping_fixture.MappingWorld(self.tmp)
        world.members = [m for m in world.members if m['pubkey'] != base.CAROL_PK]
        world.needs_auth_tag.clear()
        db = self.tmp / 'metadata' / 'hostd.sqlite3'
        def clients(config, environment):
            return bot_fixture.bc.build_clients(config, environment, runner=world,
                http=world.http_get, trusted_relays={'https://relay.test'})
        def worker(migrate=False):
            return bw.Worker(env.config, env.state_dir, base_env=env.base_env,
                store_path=db, binding_id='test-binding', client_factory=clients,
                migrate_bot_readers=migrate, clock=lambda: base.NOW)
        return env, cfg, world, db, worker

    def test_parent_registers_bindings_and_supplies_one_shared_database(self):
        env, cfg, world, db, _ = self.assembly()
        app = cfg['agents'][cfg['desk_pubkey']]
        reg = registry.Registry()
        reg.bindings['test-binding'] = registry.Binding('test-binding', env.config, env.state_dir,
            cfg['channel_id'], cfg['chat_id'], app['app_id'], app['lark_config_dir'],
            app['lark_data_dir'], 'https://relay.test')
        host = hd.Hostd(reg, self.tmp/'status.json', state_db=db, migrate_bot_readers=True)
        self.assertEqual(host.workers['test-binding'].store_path, db)
        self.assertEqual(host.workers['test-binding'].binding_id, 'test-binding')
        self.assertTrue(host.workers['test-binding'].migrate_bot_readers)
        with Store(db) as store:
            self.assertEqual([r['binding_id'] for r in store.bindings()], ['test-binding'])

    def test_new_binding_checkpoints_actual_send_and_restart_deduplicates(self):
        env, cfg, world, db, worker = self.assembly()
        world.messages = [base.fmsg('om_inbound', base.ALICE_OPEN, 'one message')]
        rep = worker().run({'feishu'}, threads=set())
        self.assertEqual(rep['to_buzz'], 1)
        with Store(db) as store:
            adapter = StateAdapter(store, 'test-binding', env.state_dir)
            state = adapter.load()
            self.assertEqual(adapter.metadata()['reader_app_id'], base.AGENT_APP)
            self.assertTrue(base.FGS.HEX64_RE.fullmatch(state.f2b['om_inbound']))
            row = store.conn.execute("SELECT status,target_id FROM delivery WHERE direction='f2b'").fetchone()
            self.assertEqual(tuple(row), ('acked', state.f2b['om_inbound']))
        self.assertFalse((env.state_dir / base.FGS.STATE_FILE).exists())
        self.assertEqual(worker().run({'feishu'}, threads=set())['to_buzz'], 0)
        sent = [ev for ev in world.relay_writes if ev['kind'] == 9]
        self.assertEqual(len(sent), 1)
        self.assertIn(['feishu', 'om_inbound'], sent[0]['tags'])
        self.assertIn(['feishu-root', 'om_inbound'], sent[0]['tags'])
        self.assertTrue(base.FGS._nip01_event_verified(sent[0]))
        self.assertTrue(all(argv[argv.index('--as')+1] == 'bot' for argv, _ in world.bot_calls))

    def test_legacy_state_requires_explicit_reader_migration_and_is_untouched(self):
        env, cfg, world, db, worker = self.assembly()
        state = base.FGS.State(binding=f'{base.CHANNEL}|{base.CHAT}', floor=123,
            buzz_since=123, feishu_since=123, react_since=123)
        state.b2f['ab'*32] = 'om_existing'
        state.f2r['om_existing|' + base.union_of(base.ALICE_OPEN) + '|SMILE'] = 'cd'*32
        base.FGS.save_state(env.state_dir, state)
        before = (env.state_dir / base.FGS.STATE_FILE).read_bytes()
        with self.assertRaises(base.FGS.GroupSyncError) as caught:
            worker().run({'buzz'})
        self.assertIn('怎么解决', str(caught.exception))
        self.assertIn('复制给 AI', str(caught.exception))
        self.assertEqual(world.buzz_sends(), [])
        worker(migrate=True).run({'buzz'})
        self.assertEqual((env.state_dir / base.FGS.STATE_FILE).read_bytes(), before)
        self.assertEqual(base.FGS.load_config(env.config), cfg)
        with Store(db) as store:
            adapter = StateAdapter(store, 'test-binding', env.state_dir)
            current = adapter.load()
            self.assertEqual(current.b2f, state.b2f)
            self.assertEqual(current.f2r, state.f2r)
            self.assertEqual(adapter.metadata()['reader_app_id'], base.AGENT_APP)
        # A new worker does not need migration enabled once the namespace is durable.
        worker().run({'buzz'})

    def test_unknown_sender_after_cached_setup_cannot_borrow_known_identity(self):
        env, cfg, world, _, factory = self.assembly()
        cfg['feishu_unmapped_senders'] = 'skip'
        base.write_owner_only(env.config, json.dumps(cfg))
        worker = factory()
        worker.run({'members'})
        world.messages = [base.fmsg('om_newcomer', base.EXTRA_OPEN, 'new arrival')]
        # The base transport fixture normally aliases every GET sender to
        # Alice; this wire response preserves the actual unknown identity.
        world.message_get_tamper = lambda mid, kind, item: dict(item, sender=dict(
            item['sender'], id=base.union_of(base.EXTRA_OPEN) if kind == 'union_id' else base.EXTRA_OPEN))
        rep = worker.run({'feishu'}, threads=set())
        self.assertEqual(rep['hostd']['setup'], 'cached')
        sent = [event for event in world.relay_writes if event['kind'] == 9]
        self.assertEqual(sent, [])

    def test_failed_identity_preflight_leaves_legacy_epoch_and_ledger_intact(self):
        env, cfg, world, db, worker = self.assembly()
        state = base.FGS.State(binding=f'{base.CHANNEL}|{base.CHAT}')
        state.idmap[base.ALICE_OPEN] = base.union_of(base.ALICE_OPEN)
        state.f2b['om_existing'] = 'ab'*32
        base.FGS.save_state(env.state_dir, state)
        before = (env.state_dir / base.FGS.STATE_FILE).read_bytes()
        world.union_missing.add(base.OWNER_PK)
        with self.assertRaises(base.FGS.GroupSyncError):
            worker(migrate=True).run({'members', 'feishu'})
        self.assertEqual(world.buzz_sends(), [])
        self.assertEqual((env.state_dir / base.FGS.STATE_FILE).read_bytes(), before)
        with Store(db) as store:
            adapter = StateAdapter(store, 'test-binding', env.state_dir)
            current = adapter.load()
            self.assertIsNone(adapter.metadata()['reader_app_id'])
            self.assertEqual(current.idmap, state.idmap)
            self.assertEqual(current.f2b, state.f2b)

    def test_verified_union_epoch_supplies_runtime_config_after_restart(self):
        env, cfg, world, db, worker = self.assembly()
        cfg['identity'] = 'email'
        base.write_owner_only(env.config, json.dumps(cfg))
        state = base.FGS.State(binding=f'{base.CHANNEL}|{base.CHAT}')
        base.FGS.save_state(env.state_dir, state)
        original = env.config.read_bytes()
        worker(migrate=True).run({'buzz'})
        worker().run({'buzz'})
        self.assertEqual(env.config.read_bytes(), original)
        with Store(db) as store:
            metadata = StateAdapter(store, 'test-binding', env.state_dir).metadata()
            self.assertEqual(metadata['reader_app_id'], base.AGENT_APP)

    def test_durable_old_reply_target_recovers_root_before_actual_round(self):
        env, cfg, world, db, worker = self.assembly()
        worker().run({'buzz'})
        root = base.fmsg('om_old', base.ALICE_OPEN, 'old root')
        root['create_time'] = str((int(base.NOW.timestamp()) - 5000) * 1000)
        reply = base.fmsg('om_reply', base.ALICE_OPEN, 'reply', when=base.NOW)
        reply['root_id'] = 'om_old'
        event = mapping_fixture.signed(mid='om_old', root='om_old')
        world.events = [event]
        world.messages = [root]
        world.threads = {'om_old': [reply]}
        with Store(db) as store:
            store.enqueue_target('test-binding', base.AGENT_APP, 'om_reply', '',
                'im.message.reaction.created_v1', now=int(base.NOW.timestamp()))
        report = worker().run({'feishu'}, threads=None)
        self.assertEqual(report['to_buzz'], 1)
        with Store(db) as store:
            state = StateAdapter(store, 'test-binding', env.state_dir).load()
            self.assertEqual(state.f2b['om_old'], event['id'])
            self.assertEqual(store.pending_targets('test-binding'), [])
        posted = [ev for ev in world.relay_writes if ev['kind'] == 9]
        self.assertEqual(len(posted), 1)
        self.assertIn(['e', event['id'], '', 'reply'], posted[0]['tags'])


if __name__ == '__main__':
    unittest.main()
