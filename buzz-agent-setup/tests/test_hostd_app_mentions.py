"""Native bot app_id mentions retain typed, current-roster identity through signing."""
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1] / 'scripts')]
import test_hostd_delivery_mapping as fixture

base, gs = fixture.base, fixture.base.FGS
setUpModule = base.setUpModule
tearDownModule = base.tearDownModule


def selected(identity, kind='app_id', key='@_user_1'):
    return {'id': identity, 'id_type': kind, 'key': key, 'name': 'display is not authority'}


class NativeAppMentions(base.TmpCase):
    assembly = fixture.MappedAssembly.assembly

    def source(self, world, mentions, *, kind='text'):
        world.messages = [base.fmsg('om_selected', base.ALICE_OPEN, 'hello @_user_1', mentions=mentions, msg_type=kind)]
        def native(mid, space, item):
            # Actual Feishu GET keeps bot mentions as app_id under BOTH human
            # user_id_type projections; the older fixture converts all IDs.
            item['mentions'] = copy.deepcopy(mentions)
            if kind == 'post':
                item['body'] = {'content': json.dumps({'title': '', 'content': [[{'tag': 'text', 'text': 'hello '},
                    {'tag': 'at', 'user_id': '@_user_1', 'user_name': 'display is not authority'}]]})}
            return item
        world.message_get_tamper = native

    def test_fresh_exact_get_app_id_entity_produces_signed_p_tag_and_deduplicates(self):
        _, _, world, run = self.assembly()
        self.source(world, [selected(base.AGENT_APP)])
        run.feishu_to_buzz(only_threads=set())
        events = [ev for ev in world.relay_writes if ev['kind'] == 9]
        self.assertEqual(len(events), 1)
        self.assertTrue(gs._nip01_event_verified(events[0]))
        self.assertEqual([tag for tag in events[0]['tags'] if tag[0] == 'p'], [['p', base.AGENT_PK]])
        run.feishu_to_buzz(only_threads=set())
        self.assertEqual(len([ev for ev in world.relay_writes if ev['kind'] == 9]), 1)

    def test_native_post_app_id_entity_and_open_id_alias_collapse(self):
        _, _, world, run = self.assembly()
        self.source(world, [selected(base.AGENT_APP), selected(base.AGENT_BOT_MEMBER, 'open_id', '@_user_2')], kind='post')
        run.feishu_to_buzz(only_threads=set())
        events = [ev for ev in world.relay_writes if ev['kind'] == 9]
        self.assertEqual(len(events), 1)
        self.assertEqual([tag for tag in events[0]['tags'] if tag[0] == 'p'], [['p', base.AGENT_PK]])

    def test_unknown_app_wrong_namespace_and_plain_at_never_wake(self):
        _, _, world, run = self.assembly()
        cases = [[], [selected('cli_unknown')], [selected(base.AGENT_APP, 'open_id')],
                 [selected(base.AGENT_APP, 'union_id')], [dict(id=base.AGENT_APP, key='@_user_1')],
                 [selected(base.ALICE_OPEN, 'app_id')]]
        for n, mentions in enumerate(cases):
            with self.subTest(n=n):
                self.source(world, mentions)
                world.messages[0]['message_id'] = 'om_negative' + str(n)
                world.messages[0]['content'] = 'plain @agent cli_unknown @_user_1'
                run.feishu_to_buzz(only_threads=set())
        self.assertEqual(len(world.relay_writes), len(cases))
        self.assertFalse(any(tag[0] == 'p' for ev in world.relay_writes for tag in ev['tags']))

    def test_preupgrade_acked_payload_is_not_rewritten_or_replayed(self):
        _, _, world, run = self.assembly()
        self.source(world, [selected(base.AGENT_APP)])
        with mock.patch.object(run, '_feishu_bot_apps', return_value={}):
            run.feishu_to_buzz(only_threads=set())
        original = copy.deepcopy(world.relay_writes[0])
        self.assertFalse(any(tag[0] == 'p' for tag in original['tags']))
        run.feishu_to_buzz(only_threads=set())
        self.assertEqual(world.relay_writes, [original])
        self.assertEqual(run.state.f2b['om_selected'], original['id'])

    def test_preupgrade_unknown_content_seal_cannot_be_replaced_with_new_p_tags(self):
        _, _, world, run = self.assembly()
        self.source(world, [selected(base.AGENT_APP)])
        world.relay_write_fail = ['network']
        with mock.patch.object(run, '_feishu_bot_apps', return_value={}):
            run.feishu_to_buzz(only_threads=set())
        self.assertTrue(gs._is_pending(run.state.f2b['om_selected']) or gs._is_retry(run.state.f2b['om_selected']))
        original = copy.deepcopy(world.relay_write_requests[0])
        # Keep the unknown operation unobservable: the new routing must not
        # change its sealed event or dispatch an amended replacement.
        world.events.clear()
        from datetime import timedelta
        run.now += timedelta(seconds=31); world.clock = run.now; run.auth_clock = lambda: run.now
        run.feishu_to_buzz(only_threads=set())
        self.assertEqual(world.relay_write_requests, [original])
        self.assertEqual(run.state.f2b['om_selected'], gs.UNKNOWN)

    def test_dynamic_granted_directory_agent_not_in_static_config_gets_p_tag(self):
        _, _, world, run = self.assembly()
        app = base.TEAM_BOT_APP
        self.assertNotIn(base.AGENT2_PK, run.cfg['agents'])
        run.mapping_store.register_agent(base.AGENT2_PK, owner_pubkey=base.OWNER_PK, app_id=app, now=run.now_ts)
        run.mapping_store.record_agent_chat(base.AGENT2_PK, base.CHAT, gs.chat_ref(base.CHAT),
            binding_id=run.binding_id, status='active', now=run.now_ts)
        world.relay_events = [gs.sign_event(base.AGENT2_KEY, 0, [['auth', base.OWNER_PK]], '{}', run.now_ts),
            gs.sign_event(base.OWNER_KEY, 30177, [['d', base.AGENT2_PK]], json.dumps({'feishu': {'app_id': app}}), run.now_ts)]
        world.bots[app] = world.bot_members[app]
        run.verify_desk()
        run.load_directory()
        self.assertEqual(run.directory.get(base.AGENT2_PK), app)
        self.source(world, [selected(app)])
        run.feishu_to_buzz(only_threads=set())
        self.assertEqual([tag for ev in world.relay_writes for tag in ev['tags'] if tag[0] == 'p'], [['p', base.AGENT2_PK]])

    def test_same_agent_alias_is_scoped_to_each_binding_roster(self):
        _, _, _, first = self.assembly()
        original_tmp = self.tmp
        self.tmp = original_tmp / 'second-binding'; self.tmp.mkdir(mode=0o700)
        try:
            _, _, _, second = self.assembly()
        finally:
            self.tmp = original_tmp
        self.assertEqual(first._feishu_bot_apps(), second._feishu_bot_apps())
        second.bot_members.pop(base.AGENT_APP)
        self.assertEqual(second._feishu_bot_apps(), {})
        self.assertEqual(first._feishu_bot_apps(), {base.AGENT_APP: base.AGENT_PK})

    def test_mapping_requires_verified_local_agent_channel_role_and_bot_roster(self):
        _, _, _, run = self.assembly()
        self.assertEqual(run._feishu_bot_apps(), {base.AGENT_APP: base.AGENT_PK})
        run.verified_agents.remove(base.AGENT_PK)
        self.assertEqual(run._feishu_bot_apps(), {})
        run.verified_agents.add(base.AGENT_PK)
        run.roles[base.AGENT_PK] = 'member'
        self.assertEqual(run._feishu_bot_apps(), {})
        run.roles[base.AGENT_PK] = 'bot'
        run.bot_members.pop(base.AGENT_APP)
        self.assertEqual(run._feishu_bot_apps(), {})

    def test_directory_app_requires_current_channel_roster_and_unique_pubkey(self):
        _, _, _, run = self.assembly()
        run.directory = {base.AGENT2_PK: 'cli_foreign'}
        run.bot_members['cli_foreign'] = 'ou_foreignbot'
        self.assertEqual(run._feishu_bot_apps()['cli_foreign'], base.AGENT2_PK)
        run.bot_members.pop('cli_foreign')
        self.assertNotIn('cli_foreign', run._feishu_bot_apps())
        run.bot_members['cli_foreign'] = 'ou_foreignbot'
        run.other_mirrors.add(base.AGENT2_PK)
        self.assertNotIn('cli_foreign', run._feishu_bot_apps())
        run.other_mirrors.remove(base.AGENT2_PK)
        run.directory[base.AGENT2_PK] = base.AGENT_APP
        self.assertNotIn(base.AGENT_APP, run._feishu_bot_apps())
        run.agent_lookup_failed = True
        self.assertEqual(run._feishu_bot_apps(), {base.AGENT_APP: base.AGENT_PK})


class TypedRoute(unittest.TestCase):
    def route(self, mentions, *, stranger=False, app_map=None):
        message = base.fmsg('om_route', base.ALICE_OPEN, 'plain @agent @_user_1', mentions=mentions)
        return gs.route_feishu_message(message, open_id_to_pubkey={} if stranger else {base.ALICE_OPEN: base.OWNER_PK},
            bot_member_to_pubkey={base.AGENT_BOT_MEMBER: base.AGENT_PK},
            bot_app_to_pubkey=app_map or {base.AGENT_APP: base.AGENT_PK},
            channel_members={base.OWNER_PK, base.AGENT_PK}, names={}, now=base.NOW,
            unmapped_senders='context')

    def test_typed_native_app_entity_works_for_member_and_context_sender(self):
        for stranger in (False, True):
            with self.subTest(stranger=stranger):
                result = self.route([selected(base.AGENT_APP)], stranger=stranger)
                self.assertEqual(result.mentions, (base.AGENT_PK,))
                self.assertEqual(result.context_only, stranger)

    def test_app_id_never_falls_back_to_person_identity_or_untyped_alias(self):
        for identity, kind in ((base.ALICE_OPEN, 'app_id'), (base.AGENT_APP, 'open_id'),
                               (base.AGENT_APP, 'union_id'), (base.AGENT_BOT_MEMBER, 'app_id')):
            for stranger in (False, True):
                with self.subTest(identity=identity, kind=kind, stranger=stranger):
                    self.assertEqual(self.route([selected(identity, kind)], stranger=stranger).mentions, ())


if __name__ == '__main__': unittest.main()
