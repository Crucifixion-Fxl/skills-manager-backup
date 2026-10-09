"""Actual BotLarkCli + HttpPool + Scheduler; only HTTPS/subprocess transport is fake."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'scripts'), str(Path(__file__).resolve().parents[1] / 'scripts' / 'hostd')]
import buzz_feishu_group_sync as gs
from bot_clients import BotLarkCli, NODE, CLI_ENTRY
from http_pool import HttpPool, PoolError
from scheduler import Scheduler
import test_hostd_http_pool as fixtures
AESGCM, Server, Response, Clock = fixtures.AESGCM, fixtures.Server, fixtures.Response, fixtures.Clock

CHAT = 'oc_' + 'a' * 32
MID = 'om_' + 'b' * 32
ROOT = 'om_' + 'c' * 32
THREAD = 'omt_' + 'd' * 32

def page(items, more=False, token=''):
    return Response(200, {'code': 0, 'data': {'items': items, 'has_more': more, 'page_token': token}})

def message(mid=MID, kind='text', body=None):
    return {'message_id': mid, 'chat_id': CHAT, 'msg_type': kind, 'create_time': '1700000000000',
            'sender': {'id': 'ou_' + 'e' * 32, 'sender_type': 'user', 'name': 'Human'},
            'body': {'content': json.dumps(body or {'text': 'hello'})}}

class PureIdentity(unittest.TestCase):
    def test_poolmode_user_and_profile_override_rejected_before_transport(self):
        with tempfile.TemporaryDirectory() as root:
            cfg = Path(root); (cfg / 'config.json').write_text(json.dumps({'apps': [{'appId': 'cli_one', 'name': 'local'}]})); (cfg / 'config.json').chmod(0o600)
            server = Server(); pool = HttpPool(connection_factory=server.connect)
            self.addCleanup(pool.close)
            cli = BotLarkCli('cli_one', cfg, cfg, base_env={'HOME': root}, http_pool=pool, runner=lambda *a, **k: self.fail('CLI fallback'))
            for flags in (['--as', 'user'], ['--as=user'], ['--profile', 'personal'], ['--profile=personal']):
                with self.assertRaises(gs.GroupSyncError): cli.call('read', ['api', 'GET', '/open-apis/im/v1/chats', *flags])
            with self.assertRaises(gs.GroupSyncError): cli.call('unknown', ['im', '+unknown'])
            self.assertEqual(server.calls, [])

@unittest.skipUnless(AESGCM, 'actual protected app-secret path requires cryptography')
class Wiring(unittest.TestCase):
    profile = fixtures.ProtectedPool.profile
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.config, self.data = self.profile('cli_one')
        self.clock, self.server = Clock(), Server()
        self.scheduler = Scheduler(clock=self.clock, wait=lambda condition, seconds: setattr(self.clock, 'now', self.clock.now + seconds))
        self.pool = HttpPool(connection_factory=self.server.connect, clock=self.clock, scheduler=self.scheduler)
        self.addCleanup(self.pool.close)
        self.cli_calls = []
        self.cli = BotLarkCli('cli_one', self.config, self.data, base_env={'HOME': str(self.root)},
                            http_pool=self.pool, scheduler=self.scheduler, chat_id=CHAT, runner=self.no_cli)
    def no_cli(self, *args, **kwargs): self.fail('API unexpectedly used subprocess')
    def api_calls(self): return [r for r in self.server.calls if not r['path'].endswith('/tenant_access_token/internal')]
    def respond(self, value): self.server.responses.append(Response(200, {'code': 0, 'data': value}))
    def test_shared_pool_token_and_each_real_http_request_admitted_once(self):
        timestamps = []
        original = self.server.connect
        def connect(*args, **kwargs):
            conn = original(*args, **kwargs); request = conn.request
            def timed(*args, **kwargs): timestamps.append(self.clock()); return request(*args, **kwargs)
            conn.request = timed; return conn
        self.pool.factory = connect
        self.cli.chat(CHAT); self.cli.chat(CHAT)
        self.assertEqual(self.server.mints, 1)
        self.assertEqual(len(timestamps), 3)
        self.assertAlmostEqual(timestamps[0], 0)
        self.assertAlmostEqual(timestamps[1], .06)  # token consumes app budget, no chat budget
        self.assertAlmostEqual(timestamps[2], .26)
        self.assertEqual(self.scheduler.pending_count, 0)
    def test_text_card_reply_update_have_real_api_shapes_and_stable_uuid(self):
        for _ in range(4): self.respond({'message_id': MID})
        self.assertEqual(self.cli.send(CHAT, 'literal hello', 'stable'), MID)
        self.assertEqual(self.cli.reply(MID, 'reply', 'stable-reply'), MID)
        card = json.dumps({'elements': []})
        self.assertEqual(self.cli.send_card(CHAT, card, 'card'), MID)
        self.assertEqual(self.cli.reply_card(MID, card, 'card-reply'), MID)
        self.respond({}); self.cli.update_card(MID, card)
        self.respond({}); self.cli.update_text(MID, 'edit')
        calls = self.api_calls(); self.assertEqual([c['method'] for c in calls], ['POST'] * 4 + ['PATCH', 'PUT'])
        self.assertEqual(parse_qs(urlsplit(calls[0]['path']).query), {'receive_id_type': ['chat_id']})
        send = json.loads(calls[0]['body']); self.assertEqual(send, {'receive_id': CHAT, 'msg_type': 'text', 'content': json.dumps({'text': 'literal hello'}, ensure_ascii=False), 'uuid': 'stable'})
        reply = json.loads(calls[1]['body']); self.assertTrue(reply['reply_in_thread']); self.assertEqual(reply['uuid'], 'stable-reply')
        self.assertEqual(json.loads(calls[2]['body'])['content'], card)
        self.assertEqual(json.loads(calls[4]['body']), {'content': card})
    def test_reactions_create_delete_batch_query_quote_opaque_id(self):
        self.respond({'reaction_id': 'opaque'}); self.assertEqual(self.cli.react(MID, 'Typing'), 'opaque')
        self.respond({}); rid = 'A' * 86 + '==' ; self.cli.unreact(MID, rid)
        self.respond({'success_msg_reaction_details': [{'message_id': MID, 'message_reaction_items': [], 'has_more': False}]})
        self.assertEqual(self.cli.reaction_details([MID], 'union_id'), {MID: []})
        calls = self.api_calls(); self.assertTrue(calls[0]['path'].endswith('/reactions'))
        self.assertIn('%3D%3D', calls[1]['path']); self.assertNotIn('==', calls[1]['path'])
        self.assertIn('/messages/reactions/batch_query?', calls[2]['path'])
        self.assertEqual(json.loads(calls[2]['body'])['page_size_per_message'], 10)
    def test_members_full_pages_use_actual_list_endpoint_humans_and_bots(self):
        self.respond({'users': [{'member_id': 'ou_human'}], 'bots': [], 'has_more': True, 'page_token': 'next', 'truncations': [], 'user_total': 2, 'bot_total': 1})
        self.respond({'users': [{'member_id': 'ou_second'}], 'bots': [{'member_id': 'ou_bot', 'app_id': 'cli_bot'}], 'has_more': False, 'page_token': '', 'truncations': [], 'user_total': 2, 'bot_total': 1})
        listing = self.cli.member_listing(CHAT)
        self.assertTrue(listing.complete); self.assertEqual(set(listing.users), {'ou_human', 'ou_second'}); self.assertEqual(listing.bots, {'cli_bot': 'ou_bot'})
        calls = self.api_calls(); self.assertEqual(len(calls), 2)
        self.assertTrue(all('/members/list?' in c['path'] for c in calls))
        self.assertEqual(parse_qs(urlsplit(calls[0]['path']).query)['member_types'], ['user,bot'])
        self.assertEqual(parse_qs(urlsplit(calls[1]['path']).query)['page_token'], ['next'])
    def test_union_members_separate_bot_openid_and_human_union_requests(self):
        self.respond({'users': [{'member_id': 'on_human'}], 'bots': [], 'has_more': False, 'truncations': [], 'user_total': 1})
        self.respond({'users': [], 'bots': [{'member_id': 'ou_bot', 'app_id': 'cli_bot'}], 'has_more': False, 'truncations': [], 'bot_total': 1})
        self.assertTrue(self.cli.member_listing(CHAT, 'union_id').complete)
        query = [parse_qs(urlsplit(c['path']).query) for c in self.api_calls()]
        self.assertEqual([q['member_id_type'] for q in query], [['union_id'], ['open_id']])
    def test_member_truncation_on_earlier_page_cannot_disappear(self):
        self.cli.active_phase = 'members'
        self.respond({'users': [{'member_id': 'ou_human'}], 'bots': [], 'has_more': True, 'page_token': 'n', 'truncations': ['security'], 'user_total': 1, 'bot_total': 0})
        self.respond({'users': [], 'bots': [], 'has_more': False, 'truncations': [], 'user_total': 1, 'bot_total': 0})
        self.assertFalse(self.cli.member_listing(CHAT).complete); self.assertIn('members', self.cli.partial_reads)
    def test_member_total_mismatch_blocks_destructive_mutation(self):
        self.respond({'users': [], 'bots': [], 'has_more': False, 'truncations': [], 'user_total': 1, 'bot_total': 0})
        with self.assertRaises(gs.GroupSyncError): self.cli.change_members('DELETE', CHAT, ['ou_human'], 'open_id')
        self.assertEqual([c['method'] for c in self.api_calls()], ['GET'])
    def test_messages_raw_items_normalize_times_text_images_and_keep_raw_proof(self):
        row = message(); row['mentions'] = [{'key': '@_user_1', 'name': 'Agent', 'id': {'open_id': 'ou_bot'}}]
        row['body']['content'] = json.dumps({'text': 'hello @_user_1'})
        self.server.responses += [page([row], True, 'n'), page([message(ROOT, 'image', {'image_key': 'img_v3_hello'})])]
        rows, partial = self.cli.messages(CHAT, datetime(2020,1,1,tzinfo=timezone.utc))
        self.assertFalse(partial); self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['create_time'], '2023-11-14 22:13')
        self.assertIn('ou_bot', rows[0]['content']); self.assertEqual(rows[0]['body'], row['body'])
        self.assertEqual(rows[1]['content'], '[Image: img_v3_hello]')
        q = parse_qs(urlsplit(self.api_calls()[0]['path']).query)
        self.assertEqual(q['container_id_type'], ['chat']); self.assertEqual(q['start_time'], ['1577836800'])
    def test_duplicate_token_or_page_limit_or_missing_has_more_never_complete(self):
        for responses, limit in (([page([message()], True, 'n'), page([], True, 'n')], 40), ([page([message()], True, 'n')], 1), ([Response(200, {'code':0,'data':{'items':[]}})], 40)):
            with self.subTest(limit=limit, responses=len(responses)):
                self.server.responses += responses
                rows, partial = self.cli.messages(CHAT, datetime.now(timezone.utc), page_limit=limit)
                self.assertTrue(partial)
    def test_thread_reader_resolves_actual_thread_id_before_paginating(self):
        row = message(ROOT); row['thread_id'] = THREAD
        self.respond({'items': [row]}); self.server.responses.append(page([message()]))
        rows, partial = self.cli.thread_messages(ROOT)
        self.assertFalse(partial); self.assertEqual(len(rows), 1)
        q = parse_qs(urlsplit(self.api_calls()[-1]['path']).query)
        self.assertEqual(q['container_id_type'], ['thread']); self.assertEqual(q['container_id'], [THREAD])
    def test_exact_http404_is_missing_other_validation_preserves_partial(self):
        self.cli.active_phase = 'feishu'
        self.server.responses.append(Response(404, {'code':1234}))
        self.assertIsNone(self.cli.message_view(MID, 'open_id')); self.assertEqual(self.cli.partial_reads, set())
        self.server.responses.append(Response(400, {'code':1234}))
        with self.assertRaises(PoolError): self.cli.message_view(MID, 'open_id')
        self.assertIn('feishu', self.cli.partial_reads)
    def test_unknown_mutation_outcome_not_retried_or_logged(self):
        self.server.responses.append(Response(503, {'code':999,'msg':'private body'}))
        with self.assertRaises(PoolError) as caught: self.cli.send(CHAT, 'private body', 'stable')
        self.assertFalse(caught.exception.definite); self.assertTrue(caught.exception.dispatched)
        self.assertEqual(len(self.api_calls()), 1); self.assertNotIn('private', str(caught.exception))
    def test_media_only_fallback_preserves_exact_bot_cli_identity(self):
        import subprocess
        def run(argv, **kwargs):
            self.cli_calls.append((argv, kwargs)); return subprocess.CompletedProcess(argv, 0, json.dumps({'ok':True,'identity':'bot','data':{'message_id':MID}}), '')
        self.cli.runner = run
        self.assertEqual(self.cli.send_image(CHAT, 'image.png', 'img', cwd=self.root), MID)
        argv, opts = self.cli_calls[0]; self.assertEqual(argv[:2], [NODE, CLI_ENTRY]); self.assertIn('--as', argv); self.assertEqual(argv[argv.index('--as')+1], 'bot')
        self.assertEqual(opts['env']['LARKSUITE_CLI_CONFIG_DIR'], str(self.config)); self.assertEqual(self.server.calls, [])
    def test_member_totals_changing_across_pages_hold_membership(self):
        self.respond({'users': [], 'bots': [], 'has_more': True, 'page_token': 'n', 'truncations': [], 'user_total': 1, 'bot_total': 0})
        self.respond({'users': [], 'bots': [], 'has_more': False, 'truncations': [], 'user_total': 0, 'bot_total': 0})
        self.assertFalse(self.cli.member_listing(CHAT).complete)
    def test_missing_timestamp_unsupported_attachment_and_duplicate_message_hold(self):
        for rows in ([dict(message(), create_time=None)], [message(kind='file', body={'file_key':'file_x','file_name':'x'})], [message(),message()]):
            self.server.responses.append(page(rows))
            self.assertTrue(self.cli.messages(CHAT, datetime.now(timezone.utc))[1])
    def test_deleted_empty_body_does_not_poison_full_history(self):
        row = message(); row.update(deleted=True, body={'content':''})
        self.server.responses.append(page([row]))
        rows, partial = self.cli.messages(CHAT, datetime.now(timezone.utc))
        self.assertFalse(partial); self.assertTrue(rows[0]['deleted'])
    def test_full_chat_pagination_and_shared_pool_second_client(self):
        second = BotLarkCli('cli_one', self.config, self.data, base_env={'HOME':str(self.root)}, http_pool=self.pool, scheduler=self.scheduler, runner=self.no_cli)
        self.server.responses += [page([{'chat_id':CHAT}], True, 'n'), page([{'chat_id':'oc_second'}])]
        data = second.call('chats', ['im','chats','list','--page-all'])
        self.assertFalse(data['has_more']); self.assertEqual(len(data['items']),2)
        self.cli.chat(CHAT); self.assertEqual(self.server.mints,1)
    def test_actual_mapped_round_store_and_bot_transport_send_receipt(self):
        import test_hostd_bot_clients as bots
        import test_hostd_delivery_mapping as mapping_tests
        from bot_clients import build_clients
        from delivery_mapping import MappedHostdRound
        from store import Store, BindingRecord
        from state_store import StateAdapter
        base = bots.base
        base.setUpModule(); self.addCleanup(base.tearDownModule)
        root = self.root / 'assembly'; root.mkdir(mode=0o700)
        env = base.Env(root); cfg = base.FGS.load_config(env.config)
        config, data = self.profile(base.AGENT_APP)
        cfg['agents'][base.AGENT_PK].update(lark_config_dir=str(config), lark_data_dir=str(data))
        world = mapping_tests.MappingWorld(root)
        world.members = [m for m in world.members if m['pubkey'] != base.CAROL_PK]; world.needs_auth_tag.clear()
        def runner(argv, **kwargs):
            if argv[0] == NODE: self.fail('bot subprocess in mapped assembly')
            return world(argv, **kwargs)
        clients = build_clients(cfg, env.base_env, runner=runner, http=world.http_get, trusted_relays={'https://relay.test'}, http_pool=self.pool, scheduler=self.scheduler)
        db = Store(root / 'hostd.db'); self.addCleanup(db.close)
        db.reconcile_bindings([BindingRecord('test',cfg['channel_id'],cfg['chat_id'],base.AGENT_APP,str(env.config),str(config),str(data),cfg['mirror_pubkey'])],now=int(base.NOW.timestamp()))
        adapter = StateAdapter(db,'test',env.state_dir,profile_id=f'bot:{base.AGENT_APP}:union_id:v1')
        run = MappedHostdRound(cfg,clients,adapter.load(),base.FGS._new_report(),base.NOW,lambda:adapter.save(run.state,now=int(base.NOW.timestamp())),reader_namespace=base.AGENT_APP,store=db,binding_id='test',auth_clock=lambda:base.NOW)
        self.respond({'scopes':[{'scope_name': scope,'scope_type':'tenant','grant_status':1} for scope in bots.bc.READ_SCOPE_GROUPS]})
        self.respond({'users':[{'member_id':base.union_of(base.OWNER_OPEN)}], 'has_more':False,'truncations':[],'user_total':1})
        self.respond({'bots':[{'app_id':base.AGENT_APP,'member_id':base.AGENT_BOT_MEMBER}],'has_more':False,'truncations':[],'bot_total':1})
        run.verify_identities();run.load_people();run.load_directory();run.verify_desk()
        event = base.FGS.sign_event(base.OWNER_KEY,9,[['h',base.CHANNEL]],'human hello',int(base.NOW.timestamp()));world.events=[event]
        self.server.responses.append(page([])); self.respond({'message_id':MID}); run.buzz_to_feishu()
        self.assertEqual(run.state.b2f[event['id']],MID)
        sent = next(call for call in self.api_calls() if call['method']=='POST')
        card=json.loads(json.loads(sent['body'])['content']); self.assertIn(event['id'],json.dumps(card))
        record=db.delivery_by_source('test',event['id'],'b2f');self.assertEqual(record['status'],'acked');self.assertEqual(record['target_id'],MID)
    def test_malformed_sender_or_mentions_and_invalid_member_ids_fail_closed(self):
        for row in (dict(message(), sender=['bad']), dict(message(), mentions=[{'id':{'open_id':[]},'key':'x'}])):
            self.server.responses.append(page([row]))
            self.assertTrue(self.cli.messages(CHAT,datetime.now(timezone.utc))[1])
        self.respond({'users':[{'member_id':True}], 'bots':[], 'has_more':False, 'truncations':[], 'user_total':1, 'bot_total':0})
        self.assertFalse(self.cli.member_listing(CHAT).complete)
    def test_scheduler_refusal_keeps_definite_undispatched_outcome(self):
        self.scheduler.close()
        with self.assertRaises(PoolError) as caught: self.cli.send(CHAT,'hello','stable')
        self.assertTrue(caught.exception.definite); self.assertFalse(caught.exception.dispatched)
        self.assertEqual(self.server.calls,[])
    def test_token404_cannot_be_misclassified_as_missing_message(self):
        self.cli.active_phase = 'feishu'
        self.server.token_response = Response(404, {'code':1234})
        with self.assertRaises(PoolError) as caught: self.cli.message_view(MID,'open_id')
        self.assertEqual(caught.exception.phase,'token'); self.assertNotEqual(caught.exception.kind,'not_found')
        self.assertIn('feishu',self.cli.partial_reads)
    def test_known_target_chat_overrides_default_lane_without_mutating_client(self):
        other = 'oc_' + 'f' * 32
        self.cli.chat(CHAT); self.cli.chat(other); self.cli.chat(other)
        self.assertAlmostEqual(self.clock(), .32)
        self.assertEqual(self.cli.chat_id, CHAT)
