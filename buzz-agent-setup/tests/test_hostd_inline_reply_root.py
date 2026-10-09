"""Chat-history inline replies keep their real root, not a top-level fallback."""
import test_hostd_delivery_mapping as fixture

base = fixture.base
setUpModule = fixture.setUpModule
tearDownModule = fixture.tearDownModule


class InlineReplyRoot(base.TmpCase):
    assembly = fixture.MappedAssembly.assembly

    def setup_reply(self):
        _, _, world, run = self.assembly()
        root = base.fmsg('om_root', base.ALICE_OPEN, 'root')
        reply = base.fmsg('om_reply', base.ALICE_OPEN, 'reply')
        reply['root_id'] = 'om_root'
        world.messages = [root, reply]
        return world, run, root, reply

    def test_chat_history_reply_uses_the_original_root_and_acks_once(self):
        world, run, _, _ = self.setup_reply()
        run.feishu_to_buzz(only_threads=set())
        from datetime import timedelta
        run.now += timedelta(seconds=31); world.clock = run.now; run.auth_clock = lambda: run.now
        run.feishu_to_buzz(only_threads=set())
        events = [ev for ev in world.relay_writes if ev['kind'] == 9]
        self.assertEqual(len(events), 2)
        root, reply = events
        self.assertIn(['feishu', 'om_reply'], reply['tags'])
        self.assertIn(['feishu-root', 'om_root'], reply['tags'])
        self.assertIn(['e', root['id'], '', 'reply'], reply['tags'])
        self.assertIn(['e', root['id'], '', 'root'], reply['tags'])
        row = run.mapping_store.delivery_by_source('test', 'om_reply', 'f2b')
        self.assertEqual((row['status'], row['target_id']), ('acked', reply['id']))

    def test_missing_inline_root_never_creates_top_level_reply(self):
        world, run, _, reply = self.setup_reply()
        world.messages = [reply]
        run.feishu_to_buzz(only_threads=set())
        self.assertGreater(run.report['errors'], 0)
        retained = run.mapping_store.conn.execute('SELECT done FROM feishu_ingest WHERE message_id=?', ('om_reply',)).fetchone()
        self.assertEqual(retained['done'], 0)
        self.assertEqual(world.relay_writes, [])
        row = run.mapping_store.delivery_by_source('test', 'om_reply', 'f2b')
        self.assertTrue(row is None or row['status'] != 'acked')

    def test_explicit_self_root_remains_a_top_level_message(self):
        world, run, root, _ = self.setup_reply()
        root['root_id'] = root['message_id']
        world.messages = [root]
        run.feishu_to_buzz(only_threads=set())
        events = [ev for ev in world.relay_writes if ev['kind'] == 9]
        self.assertEqual(len(events), 1)
        self.assertFalse(any(tag[0] == 'e' for tag in events[0]['tags']))

    def test_unknown_inline_reply_is_not_republished(self):
        world, run, _, reply = self.setup_reply()
        world.messages = [reply]
        run.state.f2b['om_reply'] = base.FGS.UNKNOWN
        run.persist()
        run.feishu_to_buzz(only_threads=set())
        self.assertEqual(world.relay_writes, [])
        self.assertEqual(run.state.f2b['om_reply'], base.FGS.UNKNOWN)

    def test_malformed_root_does_not_fall_back_to_top_level(self):
        world, run, _, reply = self.setup_reply()
        world.messages = [reply]
        reply['root_id'] = ['om_root']
        run.feishu_to_buzz(only_threads=set())
        self.assertGreater(run.report['errors'], 0)
        retained = run.mapping_store.conn.execute('SELECT done FROM feishu_ingest WHERE message_id=?', ('om_reply',)).fetchone()
        self.assertEqual(retained['done'], 0)
        self.assertEqual(world.relay_writes, [])
