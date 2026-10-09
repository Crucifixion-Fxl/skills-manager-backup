"""ADR0026 own-agent outlet through actual bot/SQL adapters, fake transport only."""
import asyncio
import base64
import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest
from urllib.parse import unquote

TESTS = Path(__file__).resolve().parent
for path in (TESTS, TESTS.parent / 'scripts', TESTS.parent / 'scripts' / 'hostd'):
    sys.path.insert(0, str(path))
import test_hostd_delivery_mapping as mapped
import outlet
from bot_clients import BotLarkCli

base = mapped.base
setUpModule = base.setUpModule
tearDownModule = base.tearDownModule
APP = 'cli_outletbot2'
MEMBER = 'ou_outletbot000000000000000000002'


class Socket:
    def __init__(self, events):
        self.events, self.sent, self.closed = events, [], False
        self.queue = asyncio.Queue()
        self.queue.put_nowait(json.dumps(['AUTH', 'challenge']))
    async def __aenter__(self): return self
    async def __aexit__(self, *args): self.closed = True
    def __aiter__(self): return self
    async def __anext__(self): return await self.queue.get()
    async def send(self, raw):
        msg = json.loads(raw)
        self.sent.append(msg)
        if msg[0] == 'AUTH':
            self.queue.put_nowait(json.dumps(['OK', msg[1]['id'], True, '']))
        if msg[0] == 'REQ':
            self.queue.put_nowait('{invalid json')
            for event in self.events:
                self.queue.put_nowait(json.dumps(['EVENT', msg[1], event]))


def auth(agent):
    digest = hashlib.sha256(f'nostr:agent-auth:{agent}:'.encode()).digest()
    sig = base.FGS.sync.nk.schnorr_sign(digest, bytes.fromhex(base.OWNER_KEY), bytes(32)).hex()
    return ['auth', base.OWNER_PK, '', sig]


def profile(key):
    agent = base.FGS._signer_pubkey(key)
    return base.FGS.sign_event(key, 0, [auth(agent)], '{}', int(base.NOW.timestamp()) - 100)


class OwnOutlet(base.TmpCase):
    def test_sliced_catchup_preserves_same_second_events_and_quiets_after_ack(self):
        world, run, adapter = self.assembly()
        events = [self.event(content='slice '+str(i)) for i in range(3)]
        world.events.extend(events); world.relay_events.extend(events)
        for count in range(1,4):
            adapter.catch_up(max_events=1)
            rows = [run.mapping_store.delivery_by_source('test',e['id'],'b2f',agent_id=base.AGENT2_PK) for e in events]
            self.assertEqual(sum(r is not None and r['status']=='acked' for r in rows), count)
            self.assertEqual(adapter.slice_pending, count<3)
        adapter.catch_up(max_events=1)
        self.assertFalse(adapter.slice_pending)
        sends = [a for a,_ in world.bot_calls if '--content' in a]
        self.assertEqual(len(sends),3)

    def assembly(self):
        _, _, world, run = mapped.MappedAssembly.assembly(self)
        original_get = world._message_item
        def raw_card(mid, id_type):
            item = original_get(mid, id_type)
            source = next((m for m in world.messages if m['message_id'] == mid and m.get('msg_type') == 'interactive'), None)
            if item is not None and source:
                item['body'] = {'content': source.get('content') or source['body']['content']}
            return item
        world._message_item = raw_card
        original_reactions = world._reactions
        def opaque_reactions(args, app):
            result = original_reactions(args, app)
            if args[2] == 'create' and result.get('ok'):
                old = result['data']['reaction_id']
                actual = base64.urlsafe_b64encode(bytes(range(64))).decode()
                for item in world.reactions:
                    if item['id'] == old:
                        item['id'] = actual
                result['data']['reaction_id'] = actual
            return result
        world._reactions = opaque_reactions
        config_dir, data_dir = self.tmp / 'outlet-cfg', self.tmp / 'outlet-data'
        config_dir.mkdir(mode=0o700)
        base.write_owner_only(config_dir / 'config.json', json.dumps({'apps': [{'appId': APP}]}))
        data_dir.mkdir(mode=0o700)
        client = BotLarkCli(APP, config_dir, data_dir, base_env={}, runner=world)
        def bot_transport(argv, **kwargs):
            if argv[2:4] == ['api', 'DELETE'] and '/reactions/' in argv[4]:
                world.bot_calls.append((argv, kwargs))
                parts = argv[4].split('/')
                params = {'message_id': unquote(parts[-3]), 'reaction_id': unquote(parts[-1])}
                args = ['im', 'reactions', 'delete', '--params', json.dumps(params), '--as', 'bot']
                result = world._reactions(args, APP)
                return mapped.bots.ok(result['data']) if isinstance(result, dict) else result
            return world(argv, **kwargs)
        client.runner = bot_transport
        world.profiles[APP] = ''
        world.agent_dirs[str(config_dir)] = APP
        world.bots[APP] = world.bot_members[APP] = MEMBER
        run.clients.agents[APP] = client
        run.cfg['agents'][base.AGENT2_PK] = {'app_id': APP, 'lark_config_dir': str(config_dir), 'lark_data_dir': str(data_dir)}
        run.verified_agents.add(base.AGENT2_PK)
        run.bot_members[APP] = MEMBER
        own_policy = base.FGS.sign_event(base.OWNER_KEY, 30177, [['d', base.AGENT2_PK]],
                                        json.dumps({'feishu': {'app_id': APP}}), run.now_ts - 50)
        claim = {'mirror': True, 'bindings': [{'channel': base.CHANNEL, 'chat_ref': base.FGS.chat_ref(base.CHAT),
                                               'claimed_at': run.now_ts - 50, 'heartbeat': run.now_ts}]}
        mirror_policy = base.FGS.sign_event(base.OWNER_KEY, 30177, [['d', base.MIRROR_PK]],
                                            json.dumps({'feishu': claim}), run.now_ts - 50)
        world.relay_events = [profile(base.AGENT2_KEY), own_policy, profile(base.MIRROR_KEY), mirror_policy]
        credential = self.tmp / 'outlet.env'
        base.write_owner_only(credential, f'BUZZ_PRIVATE_KEY={base.AGENT2_KEY}\nBUZZ_RELAY_URL=https://relay.test\nBUZZ_AUTH_TAG={json.dumps(auth(base.AGENT2_PK))}\nBUZZ_ACP_AGENT_OWNER={base.OWNER_PK}\nBUZZ_ACP_CHANNELS={base.CHANNEL}\n')
        # Explicit protected legacy authorization, not the relay role alone.
        config_path = self.tmp / 'config.json'
        protected = json.loads(config_path.read_text())
        protected['agents'][base.AGENT2_PK] = dict(run.cfg['agents'][base.AGENT2_PK])
        base.write_owner_only(config_path, json.dumps(protected))
        adapter = outlet.AgentOutlet(run, base.AGENT2_PK, credential, bot_client=client,
                                     trusted_relays={'https://relay.test'}, http=world.http_get, clock=lambda: base.NOW)
        return world, run, adapter

    def event(self, kind=9, tags=(), content='own message', created=None, key=base.AGENT2_KEY):
        return base.FGS.sign_event(key, kind, [['h', base.CHANNEL], *tags], content,
                                   int(base.NOW.timestamp()) if created is None else created)

    def history_http(self, adapter, events):
        original = adapter.http
        def http(url, headers, timeout, *, body=None):
            filters = json.loads(body) if body and url.endswith('/query') else []
            if filters and filters[0].get('kinds') == outlet.KINDS:
                query = filters[0]
                adapter.last_history_filter = query
                rows = [e for e in events if query['since'] <= e['created_at'] <= query['until']]
                rows.sort(key=lambda e: (-e['created_at'], e['id']))
                return 200, json.dumps(rows[:query['limit']]).encode()
            return original(url, headers, timeout, body=body)
        return http

    def test_own_message_uses_own_bot_and_agent_cursor_without_other_channel_read(self):
        world, run, adapter = self.assembly()
        event = self.event()
        world.events = [event]
        mid = adapter.deliver(event)
        self.assertTrue(mid.startswith('om_'))
        self.assertEqual(adapter.deliver(event), mid)
        sends = [argv for argv, _ in world.bot_calls if '--content' in argv]
        self.assertEqual(len(sends), 1)
        self.assertEqual(sends[0][sends[0].index('--profile') + 1], APP)
        self.assertEqual(sends[0][sends[0].index('--as') + 1], 'bot')
        self.assertIn(event['id'], sends[0][sends[0].index('--content') + 1])
        self.assertEqual(run.mapping_store.cursor_position('test', 'relay', agent_id=base.AGENT2_PK), event['created_at'])
        self.assertEqual(run.mapping_store.cursor_position('test', 'relay'), 0)

    def test_foreign_author_channel_and_signature_rejected_before_any_send(self):
        world, _, adapter = self.assembly()
        variants = [self.event(key=base.OWNER_KEY), self.event(tags=[['h', base.CHANNEL]]),
                    dict(self.event(), content='tampered'), self.event(tags=[['h', 'other-channel']])]
        for event in variants:
            with self.assertRaises(base.FGS.GroupSyncError):
                adapter.deliver(event)
        self.assertFalse(any('--content' in argv for argv, _ in world.bot_calls))

    def test_policy_app_mismatch_and_revocation_hold_delivery(self):
        world, run, adapter = self.assembly()
        changed = base.FGS.sign_event(base.OWNER_KEY, 30177, [['d', base.AGENT2_PK]],
                                     json.dumps({'feishu': {'app_id': 'cli_otherapp'}}), run.now_ts)
        world.relay_events.append(changed)
        with self.assertRaises(base.FGS.GroupSyncError):
            adapter.deliver(self.event())
        self.assertFalse(any('--content' in argv for argv, _ in world.bot_calls))

    def test_fresh_verification_batches_public_reads_without_reusing_authority(self):
        world, run, adapter = self.assembly()
        world.relay_queries.clear()
        adapter.verify()
        expected = [{'kinds': [0], 'authors': [base.AGENT2_PK], 'limit': 257},
                    {'kinds': [30177], 'limit': 1000}]
        self.assertEqual(world.relay_queries[0]['filters'], expected)
        self.assertEqual(sum(q['filters'] == expected for q in world.relay_queries), 1)
        self.assertFalse(any(q['filters'] == [{'kinds': [30177]}] for q in world.relay_queries))
        # A successful read is not authority for the next effect.
        changed = base.FGS.sign_event(base.OWNER_KEY, 30177, [['d', base.AGENT2_PK]],
                                     json.dumps({'feishu': {'app_id': 'cli_revoked'}}), run.now_ts)
        world.relay_events.append(changed)
        before = '\n'.join(run.mapping_store.conn.iterdump())
        with self.assertRaises(base.FGS.GroupSyncError):
            adapter.deliver(self.event())
        self.assertEqual(before, '\n'.join(run.mapping_store.conn.iterdump()))
        self.assertFalse(any('--content' in argv for argv, _ in world.bot_calls))

    def test_each_batched_filter_must_be_complete_before_any_effect(self):
        from unittest import mock
        world, run, adapter = self.assembly()
        profile_event = next(e for e in world.relay_events if e['kind'] == 0 and e['pubkey'] == base.AGENT2_PK)
        policy = next(e for e in world.relay_events if e['kind'] == 30177)
        before = '\n'.join(run.mapping_store.conn.iterdump())
        for snapshot in ([profile_event] * 257 + [policy], [profile_event] + [policy] * 1000,
                         [profile_event, policy, self.event()]):
            with self.subTest(count=len(snapshot)), mock.patch.object(adapter, '_query', return_value=snapshot):
                with self.assertRaises(base.FGS.GroupSyncError):
                    adapter.verify()
                self.assertEqual(before, '\n'.join(run.mapping_store.conn.iterdump()))
                self.assertEqual(adapter._verification_count, 0)

    def test_owner_attestation_must_be_cryptographically_verified(self):
        world, run, adapter = self.assembly()
        world.relay_events[0] = base.FGS.sign_event(base.AGENT2_KEY, 0,
            [['auth', base.OWNER_PK, '', '00' * 64]], '{}', run.now_ts)
        with self.assertRaises(base.FGS.GroupSyncError):
            adapter.deliver(self.event())

    def test_stale_binding_claim_stops_own_outlet(self):
        world, run, adapter = self.assembly()
        revoked = base.FGS.sign_event(base.OWNER_KEY, 30177, [['d', base.MIRROR_PK]],
            json.dumps({'feishu': {'mirror': True, 'bindings': []}}), run.now_ts)
        world.relay_events.append(revoked)
        with self.assertRaises(base.FGS.GroupSyncError):
            adapter.deliver(self.event())

    def test_unknown_bot_send_retries_same_actual_request_and_idempotency_key(self):
        world, run, adapter = self.assembly()
        event = self.event()
        world.events = [event]
        world.lark_send_fail = ['timeout']
        with self.assertRaises(base.FGS.GroupSyncError):
            adapter.deliver(event)
        pending = run.mapping_store.conn.execute('SELECT status FROM delivery WHERE agent_id=?', (base.AGENT2_PK,)).fetchone()
        self.assertEqual(pending['status'], 'pending')
        adapter.deliver(event)
        sends = [argv for argv, _ in world.bot_calls if '--content' in argv]
        self.assertEqual(sends[0], sends[1])
        self.assertEqual(len(world.messages), 1)

    def test_pending_precompact_payload_keeps_same_request_after_upgrade(self):
        from unittest import mock
        world, run, adapter = self.assembly()
        event=self.event(content='long reply '*100)
        world.events=[event]
        world.lark_send_fail=['timeout']
        renderer=outlet.message_card
        def legacy(*args,**kwargs):
            kwargs.pop('compact',None)
            return renderer(*args,**kwargs)
        with mock.patch.object(outlet,'message_card',side_effect=legacy):
            with self.assertRaises(base.FGS.GroupSyncError):adapter.deliver(event)
        adapter.deliver(event)
        sends=[argv for argv,_ in world.bot_calls if '--content' in argv]
        self.assertEqual(sends[0],sends[1])
        self.assertNotIn('collapsible_panel',sends[1][sends[1].index('--content')+1])
        self.assertEqual(len(world.messages),1)

    def test_new_long_reply_uses_collapsed_card_and_acks(self):
        world,run,adapter=self.assembly()
        event=self.event(content='long reply '*100)
        world.events=[event]
        adapter.deliver(event)
        sends=[argv for argv,_ in world.bot_calls if '--content' in argv]
        payload=json.loads(sends[0][sends[0].index('--content')+1])
        self.assertFalse(next(e for e in payload['elements'] if e['tag']=='collapsible_panel')['expanded'])
        row=run.mapping_store.delivery_by_source('test',event['id'],'b2f',agent_id=base.AGENT2_PK)
        self.assertEqual(row['status'],'acked')

    def test_pending_precompact_edit_keeps_identical_patch_after_upgrade(self):
        from unittest import mock
        world,run,adapter=self.assembly()
        original=self.event(created=int(base.NOW.timestamp())-1000)
        edit=self.event(kind=40003,tags=[['e',original['id']]],content='long edit '*100)
        world.events=[original,edit];world.relay_events.append(edit)
        world.messages=[mapped.bot_message(original,APP,'om_outletold')]
        world.message_update_fail=['network']
        renderer=outlet.message_card
        def legacy(*args,**kwargs):
            kwargs.pop('compact',None)
            return renderer(*args,**kwargs)
        with mock.patch.object(outlet,'message_card',side_effect=legacy):
            with self.assertRaises(base.FGS.GroupSyncError):adapter.deliver(edit)
        adapter.deliver(edit)
        patches=[argv for argv,_ in world.bot_calls if argv[2:4]==['api','PATCH']]
        self.assertEqual(patches[0],patches[1])
        row=run.mapping_store.delivery_by_source('test',edit['id'],'e2f',agent_id=base.AGENT2_PK)
        self.assertEqual(row['status'],'acked')

    def test_pending_first_preview_layout_keeps_identical_request(self):
        from unittest import mock
        world,run,adapter=self.assembly()
        event=self.event(content='long reply '*100)
        world.events=[event];world.lark_send_fail=['timeout']
        renderer=outlet.message_card
        def previous(*args,**kwargs):
            kwargs['compact']='preview_v1'
            return renderer(*args,**kwargs)
        with mock.patch.object(outlet,'message_card',side_effect=previous):
            with self.assertRaises(base.FGS.GroupSyncError):adapter.deliver(event)
        adapter.deliver(event)
        sends=[argv for argv,_ in world.bot_calls if '--content' in argv]
        self.assertEqual(sends[0],sends[1])
        payload=json.loads(sends[0][sends[0].index('--content')+1])
        self.assertIn('header',payload)
        self.assertIn('collapsible_panel',json.dumps(payload))

    def test_pending_second_preview_layout_keeps_identical_request(self):
        from unittest import mock
        world, run, adapter = self.assembly()
        event = self.event(content='评论 ·\n[issue](https://example.test/' + 'long/' * 40 + ')\n' + 'body ' * 100)
        world.events = [event]; world.lark_send_fail = ['timeout']
        renderer = outlet.message_card
        def previous(*args, **kwargs):
            kwargs['compact'] = 'preview_v2'
            return renderer(*args, **kwargs)
        with mock.patch.object(outlet, 'message_card', side_effect=previous):
            with self.assertRaises(base.FGS.GroupSyncError): adapter.deliver(event)
        adapter.deliver(event)
        sends = [argv for argv, _ in world.bot_calls if '--content' in argv]
        self.assertEqual(len(sends), 2)
        self.assertEqual(sends[0], sends[1])
        self.assertNotIn('header', json.loads(sends[0][sends[0].index('--content') + 1]))

    def test_own_edit_recovers_mapping_and_updates_same_actual_bot_card(self):
        world, _, adapter = self.assembly()
        original = self.event(created=int(base.NOW.timestamp()) - 1000)
        world.events = [original]
        world.messages = [mapped.bot_message(original, APP, 'om_outletold')]
        edit = self.event(kind=40003, tags=[['e', original['id']]], content='edited own message')
        world.events.append(edit)
        adapter.deliver(edit)
        patches = [argv for argv, _ in world.bot_calls if argv[2:4] == ['api', 'PATCH']]
        self.assertEqual(len(patches), 1)
        self.assertEqual(patches[0][patches[0].index('--profile') + 1], APP)
        self.assertIn('om_outletold', ' '.join(patches[0]))
        self.assertIn(original['id'], ' '.join(patches[0]))
        self.assertFalse(any('--content' in argv for argv, _ in world.bot_calls))

    def test_late_older_edit_never_overwrites_newer_actual_card_and_is_not_delivery_ack(self):
        world, run, adapter = self.assembly()
        original = self.event(created=int(base.NOW.timestamp()) - 1000)
        older = self.event(kind=40003, tags=[['e', original['id']]], content='older', created=int(base.NOW.timestamp()) - 10)
        newer = self.event(kind=40003, tags=[['e', original['id']]], content='newer')
        world.events = [original, older, newer]
        world.relay_events.extend([older, newer])
        world.messages = [mapped.bot_message(original, APP, 'om_outletold')]
        adapter.deliver(newer)
        self.assertIsNone(adapter.deliver(older))
        patches = [argv for argv, _ in world.bot_calls if argv[2:4] == ['api', 'PATCH']]
        self.assertEqual(len(patches), 1)
        old = run.mapping_store.delivery_by_source(run.binding_id, older['id'], 'e2f', agent_id=base.AGENT2_PK)
        self.assertEqual(old['status'], 'skipped')
        self.assertIsNone(run.mapping_store.outlet_receipt(old['id']))
        adapter.deliver(older)
        self.assertEqual(len([argv for argv, _ in world.bot_calls if argv[2:4] == ['api', 'PATCH']]), 1)
        query = next(q['filters'][0] for q in world.relay_queries if '#e' in q['filters'][0])
        self.assertEqual(query, {'kinds': [40003], 'authors': [base.AGENT2_PK], '#h': [base.CHANNEL], '#e': [original['id']], 'limit': 1500})

    def test_same_timestamp_edits_use_signed_event_id_order(self):
        world, _, adapter = self.assembly()
        original = self.event(created=int(base.NOW.timestamp()) - 1000)
        pair = [self.event(kind=40003, tags=[['e', original['id']]], content=content) for content in ('A', 'B')]
        lower, higher = sorted(pair, key=lambda ev: ev['id'])
        world.events = [original, *pair]
        world.relay_events.extend(pair)
        world.messages = [mapped.bot_message(original, APP, 'om_outletold')]
        adapter.deliver(higher)
        self.assertIsNone(adapter.deliver(lower))
        self.assertEqual(len([argv for argv, _ in world.bot_calls if argv[2:4] == ['api', 'PATCH']]), 1)

    def test_saturated_latest_edit_query_holds_pending_without_patch(self):
        world, run, adapter = self.assembly()
        original = self.event(created=int(base.NOW.timestamp()) - 1000)
        edit = self.event(kind=40003, tags=[['e', original['id']]], content='edit')
        world.events = [original, edit]
        world.relay_events.extend([edit] * 1500)
        world.messages = [mapped.bot_message(original, APP, 'om_outletold')]
        with self.assertRaises(base.FGS.GroupSyncError): adapter.deliver(edit)
        self.assertFalse(any(argv[2:4] == ['api', 'PATCH'] for argv, _ in world.bot_calls))
        pending = run.mapping_store.delivery_by_source(run.binding_id, edit['id'], 'e2f', agent_id=base.AGENT2_PK)
        self.assertEqual(pending['status'], 'pending')

    def test_out_of_scope_latest_edit_response_is_held_without_patch(self):
        world, _, adapter = self.assembly()
        original = self.event(created=int(base.NOW.timestamp()) - 1000)
        edit = self.event(kind=40003, tags=[['e', original['id']]], content='edit')
        world.events = [original, edit]
        world.messages = [mapped.bot_message(original, APP, 'om_outletold')]
        baseline = list(world.relay_events)
        for bad_tags in ([['h', 'f' * 64], ['e', 'e' * 64]], [['e', 'e' * 64]]):
            with self.subTest(tags=bad_tags):
                bad = self.event(kind=40003, tags=bad_tags, content='foreign target')
                world.relay_events[:] = [*baseline, bad]
                with self.assertRaises(base.FGS.GroupSyncError): adapter.deliver(edit)
                self.assertFalse(any(argv[2:4] == ['api', 'PATCH'] for argv, _ in world.bot_calls))

    def test_catchup_uses_single_author_single_channel_and_durable_overlap(self):
        world, run, adapter = self.assembly()
        event = self.event()
        world.events = [event]
        world.relay_events.append(event)
        adapter.catch_up()
        requests = [q for q in world.relay_queries if any('#h' in f and 'authors' in f for f in q['filters'])]
        self.assertTrue(requests)
        self.assertTrue(all(q['signer'] == base.AGENT2_PK for q in requests))
        filter_ = requests[0]['filters'][0]
        self.assertEqual(filter_['authors'], [base.AGENT2_PK])
        self.assertEqual(filter_['#h'], [base.CHANNEL])
        self.assertEqual(filter_['since'], 0)
        adapter.catch_up()
        self.assertEqual(len([argv for argv, _ in world.bot_calls if '--content' in argv]), 1)
        last = next(q['filters'][0] for q in reversed(world.relay_queries) if '#h' in q['filters'][0] and 'authors' in q['filters'][0])
        self.assertEqual(last['since'], event['created_at'] - 900)

    def test_unmirrored_channel_less_withdrawal_does_not_block_message_catchup(self):
        world, run, adapter = self.assembly()
        deletion = base.FGS.sign_event(base.AGENT2_KEY, 5, [['e', 'a' * 64]], '', run.now_ts)
        with self.assertRaises(base.FGS.GroupSyncError):
            adapter.deliver(deletion)
        message = self.event()
        adapter.http = self.history_http(adapter, [deletion, message])
        adapter.catch_up()
        self.assertEqual(len([argv for argv, _ in world.bot_calls if '--content' in argv]), 1)
        self.assertFalse(any(argv[2:4] == ['api', 'DELETE'] for argv, _ in world.bot_calls))

    def test_malformed_signed_channel_less_withdrawal_is_not_skipped(self):
        world, run, adapter = self.assembly()
        original_http = adapter.http
        for tags in ([['e', 'a' * 64], ['x', 17]], [['e', 'bad']], [],
                     [['e', 'a' * 64], ['e', 'b' * 64]]):
            with self.subTest(tags=tags):
                deletion = base.FGS.sign_event(base.AGENT2_KEY, 5, tags, '', run.now_ts)
                adapter.http = original_http
                adapter.http = self.history_http(adapter, [deletion])
                with self.assertRaises(base.FGS.GroupSyncError): adapter.catch_up()
                self.assertFalse(any('--content' in argv or argv[2:4] == ['api', 'DELETE'] for argv, _ in world.bot_calls))

    def test_authenticated_feed_keeps_malformed_and_foreign_frames_on_same_subscription_and_closes_on_cancel(self):
        _, _, adapter = self.assembly()
        event = self.event()
        foreign = self.event(key=base.OWNER_KEY)
        socket = Socket([foreign, dict(event, content=17), event])
        received, statuses, urls = [], [], []
        def connect(url):
            urls.append(url)
            return socket
        async def on_event(ev):
            received.append(ev)
            raise asyncio.CancelledError
        async def reconnect(): pass
        async def run():
            with self.assertRaises(asyncio.CancelledError):
                await adapter.follow(on_event, statuses.append, connect=connect, on_reconnect=reconnect)
        asyncio.run(run())
        self.assertEqual(received, [event])
        self.assertEqual(urls, ['wss://relay.test'])
        requests = [frame for frame in socket.sent if frame[0] == 'REQ']
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0][2]['authors'], [base.AGENT2_PK])
        self.assertEqual(requests[0][2]['#h'], [base.CHANNEL])
        self.assertEqual(socket.sent[0][1]['pubkey'], base.AGENT2_PK)
        self.assertTrue(base.FGS._nip01_event_verified(socket.sent[0][1]))
        self.assertTrue(socket.closed)
        self.assertEqual(socket.sent[-1][:1], ['CLOSE'])

    def test_native_reaction_target_read_keeps_feed_loop_responsive(self):
        import threading
        from unittest import mock
        _,run,adapter=self.assembly()
        original=self.event()
        reaction=base.FGS.sign_event(base.AGENT2_KEY,7,[['e',original['id']]],'👍',run.now_ts)
        entered,release,expired=threading.Event(),threading.Event(),threading.Event()
        def lookup(eid):
            self.assertEqual(eid,original['id']);entered.set();release.wait(4)
            return original
        def timeout():expired.set();release.set()
        watchdog=threading.Timer(3,timeout);watchdog.start()
        socket=Socket([reaction]);received=[]
        async def on_event(event):received.append(event);raise asyncio.CancelledError
        async def reconnect():pass
        async def check():
            task=asyncio.create_task(adapter.follow(on_event,lambda _:None,
                connect=lambda _:socket,on_reconnect=reconnect))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait,3))
                self.assertFalse(expired.is_set(),'native target read blocked the event loop')
                release.set()
                with self.assertRaises(asyncio.CancelledError):await asyncio.wait_for(task,3)
            finally:
                release.set();task.cancel();await asyncio.gather(task,return_exceptions=True)
        try:
            with mock.patch.object(adapter,'_original',side_effect=lookup):asyncio.run(check())
        finally:release.set();watchdog.cancel();watchdog.join()
        self.assertEqual(received,[reaction]);self.assertTrue(socket.closed)

    def test_replay_of_acked_text_only_verifies_once_and_has_no_effect(self):
        from unittest import mock
        world,run,adapter=self.assembly();event=self.event();world.events=[event]
        adapter.deliver(event)
        world.relay_events.append(event)
        before='\n'.join(run.mapping_store.conn.iterdump())
        with mock.patch.object(adapter,'verify',wraps=adapter.verify) as verify, \
                mock.patch.object(adapter,'deliver',side_effect=AssertionError('settled text replay performed delivery')):
            self.assertEqual(adapter.catch_up(),1)
        self.assertEqual(verify.call_count,1)
        self.assertEqual(before,'\n'.join(run.mapping_store.conn.iterdump()))

    def test_new_effect_scan_verifies_once_before_actual_send(self):
        from unittest import mock
        world,run,adapter=self.assembly();event=self.event();world.events=[event];world.relay_events.append(event)
        with mock.patch.object(adapter,'verify',wraps=adapter.verify) as verify:
            self.assertEqual(adapter.catch_up(),1)
        self.assertEqual(verify.call_count,1)
        self.assertEqual(run.mapping_store.delivery_by_source('test',event['id'],'b2f',agent_id=base.AGENT2_PK)['status'],'acked')

    def revoked_scan(self,mode):
        world,run,adapter=self.assembly();event=self.event()
        if mode=='acked':
            world.events=[event];adapter.deliver(event);world.relay_events.append(event)
        elif mode=='baseline':
            old=self.event(created=run.now_ts-100);world.events=[old];adapter.deliver(old)
            world.relay_events.append(event);adapter.initial_since=run.now_ts+1
        elif mode=='new':world.events=[event];world.relay_events.append(event)
        world.relay_events.append(base.FGS.sign_event(base.OWNER_KEY,30177,[['d',base.AGENT2_PK]],json.dumps({'feishu':{'app_id':'cli_revoked'}}),run.now_ts))
        world.bot_calls.clear();before='\n'.join(run.mapping_store.conn.iterdump())
        with self.assertRaises(base.FGS.GroupSyncError):adapter.catch_up()
        self.assertFalse(any('--content' in argv for argv,_ in world.bot_calls))
        # New signed-source hints are not authority. Revocation leaves every
        # business table byte-for-byte unchanged; only scheduling metadata may persist.
        def business_dump(value):
            return '\n'.join(line for line in value.splitlines()
                if not line.startswith('INSERT INTO "outlet_work"')
                and not line.startswith('INSERT INTO "outlet_work_turn"'))
        self.assertEqual(business_dump(before),business_dump('\n'.join(run.mapping_store.conn.iterdump())))
    def test_empty_scan_still_requires_fresh_authority(self):self.revoked_scan('empty')
    def test_acked_only_scan_still_requires_fresh_authority(self):self.revoked_scan('acked')
    def test_prebaseline_only_scan_still_requires_fresh_authority(self):self.revoked_scan('baseline')
    def test_new_scan_revocation_never_sends(self):self.revoked_scan('new')

    def test_text_replay_shortcut_needs_exact_own_ack_and_never_skips_images(self):
        from unittest import mock
        world,run,adapter=self.assembly();event=self.event();world.events=[event]
        adapter.deliver(event);world.relay_events.append(event)
        db=run.mapping_store
        row=db.delivery_by_source('test',event['id'],'b2f',agent_id=base.AGENT2_PK)
        receipt=dict(db.outlet_receipt(row['id']))
        for value in (None,dict(receipt,sender_app_id='cli_foreign'),
                      dict(receipt,target_message_id='om_other'),
                      dict(receipt,reaction_id='opaque'),dict(receipt,emoji='DONE')):
            with self.subTest(receipt=value),mock.patch.object(db,'outlet_receipt',return_value=value), \
                    mock.patch.object(adapter,'deliver',return_value=None) as deliver:
                adapter.catch_up();deliver.assert_called_once_with(event)
        with mock.patch.object(db,'delivery_by_source',return_value=dict(row,status='unknown')), \
                mock.patch.object(adapter,'deliver',return_value=None) as deliver:
            with self.assertRaises(outlet.OutletDeferred):adapter.catch_up()
            deliver.assert_called_once_with(event)
        image=self.event(tags=[['imeta','url https://relay.test/media/image.png']])
        with mock.patch.object(db,'delivery_by_source',return_value=row):
            self.assertFalse(adapter._acked_text_replay(image))
            self.assertFalse(adapter._acked_text_replay(dict(event,sig='0'*128)))
            self.assertFalse(adapter._acked_text_replay(self.event(key=base.OWNER_KEY)))

    def test_pending_after_idempotency_window_never_resends(self):
        world, run, adapter = self.assembly()
        event = self.event()
        world.events = [event]
        world.lark_send_fail = ['timeout']
        with self.assertRaises(base.FGS.GroupSyncError): adapter.deliver(event)
        delivery = run.mapping_store.conn.execute('SELECT id FROM delivery WHERE agent_id=?', (base.AGENT2_PK,)).fetchone()
        run.mapping_store.conn.execute('UPDATE delivery SET created_at=? WHERE id=?',
                                      (run.now_ts - 2701, delivery['id']))
        with self.assertRaises(base.FGS.GroupSyncError): adapter.deliver(event)
        self.assertEqual(len([argv for argv, _ in world.bot_calls if '--content' in argv]), 1)

    def test_reaction_receipt_preserves_real_id_and_mixed_case_then_withdraws_after_restart(self):
        world, run, adapter = self.assembly()
        original = self.event(created=run.now_ts - 1000, key=base.OWNER_KEY)
        world.events = [original]
        world.messages = [mapped.bot_message(original, mid='om_reactiontarget')]
        reaction = self.event(kind=7, tags=[['e', original['id']]], content='💬')
        adapter.deliver(reaction)
        added = [argv for argv, _ in world.bot_calls if argv[2:5] == ['im', 'reactions', 'create']]
        self.assertEqual(len(added), 1)
        self.assertEqual(json.loads(added[0][added[0].index('--data') + 1])['reaction_type']['emoji_type'], 'Typing')
        row = run.mapping_store.delivery_by_source('test', reaction['id'], 'r2f', agent_id=base.AGENT2_PK)
        receipt = run.mapping_store.outlet_receipt(row['id'])
        self.assertEqual(receipt['emoji'], 'Typing')
        self.assertTrue(receipt['reaction_id'])
        self.assertEqual(len(receipt['reaction_id']), 88)
        self.assertTrue(receipt['reaction_id'].endswith('=='))
        self.assertEqual(receipt['sender_app_id'], APP)
        rebuilt = outlet.AgentOutlet(run, base.AGENT2_PK, self.tmp / 'outlet.env', bot_client=adapter.client,
                                      trusted_relays={'https://relay.test'}, http=world.http_get, clock=lambda: base.NOW)
        deleted = base.FGS.sign_event(base.AGENT2_KEY, 5, [['e', reaction['id']]], '', run.now_ts)
        rebuilt.deliver(deleted)
        deletes = [argv for argv, _ in world.bot_calls if argv[2:4] == ['api', 'DELETE']]
        self.assertEqual(len(deletes), 1)
        self.assertEqual(unquote(deletes[0][4].rsplit('/', 1)[-1]), receipt['reaction_id'])
        self.assertTrue(deletes[0][4].endswith('%3D%3D'))
        self.assertNotIn('==', deletes[0][4])
        rebuilt.deliver(deleted)
        self.assertEqual(len([argv for argv, _ in world.bot_calls if argv[2:4] == ['api', 'DELETE']]), 1)

    def test_settled_reaction_and_withdrawal_replays_never_repeat_effect_or_cursor(self):
        from unittest import mock
        world,run,adapter=self.assembly();original=self.event(created=run.now_ts-1000,key=base.OWNER_KEY)
        world.events=[original];world.messages=[mapped.bot_message(original,mid='om_replayreaction')]
        reaction=self.event(kind=7,tags=[['e',original['id']]],content='💬')
        adapter.deliver(reaction);policies=list(world.relay_events);world.relay_events.append(reaction)
        before='\n'.join(run.mapping_store.conn.iterdump())
        with mock.patch.object(adapter,'deliver',side_effect=AssertionError('settled effect replay')),mock.patch.object(adapter,'verify',wraps=adapter.verify) as verify:
            adapter.catch_up()
        self.assertEqual(verify.call_count,1);self.assertEqual(before,'\n'.join(run.mapping_store.conn.iterdump()))
        deleted=base.FGS.sign_event(base.AGENT2_KEY,5,[['e',reaction['id']]],'',run.now_ts)
        adapter.deliver(deleted);world.relay_events=[*policies,deleted];before='\n'.join(run.mapping_store.conn.iterdump())
        with mock.patch.object(adapter,'deliver',side_effect=AssertionError('settled delete replay')),mock.patch.object(adapter,'verify',wraps=adapter.verify) as verify:
            adapter.catch_up()
        self.assertEqual(verify.call_count,1);self.assertEqual(before,'\n'.join(run.mapping_store.conn.iterdump()))
        world.relay_events.append(base.FGS.sign_event(base.OWNER_KEY,30177,[['d',base.AGENT2_PK]],json.dumps({'feishu':{'app_id':'cli_revoked'}}),run.now_ts))
        with self.assertRaises(base.FGS.GroupSyncError):adapter.catch_up()
        self.assertEqual(before,'\n'.join(run.mapping_store.conn.iterdump()))

    def test_settled_reaction_shortcut_requires_exact_own_complete_receipt(self):
        from unittest import mock
        world,run,adapter=self.assembly();original=self.event(created=run.now_ts-1000,key=base.OWNER_KEY)
        world.events=[original];world.messages=[mapped.bot_message(original,mid='om_replayreaction')]
        reaction=self.event(kind=7,tags=[['e',original['id']]],content='💬');adapter.deliver(reaction)
        row=run.mapping_store.delivery_by_source('test',reaction['id'],'r2f',agent_id=base.AGENT2_PK);receipt=dict(run.mapping_store.outlet_receipt(row['id']))
        self.assertTrue(adapter._acked_reaction_replay(reaction))
        for field,value in [('sender_app_id','cli_foreign'),('target_message_id','om_other'),('reaction_id',''),('emoji','DONE')]:
            with mock.patch.object(run.mapping_store,'outlet_receipt',return_value={**receipt,field:value}):
                self.assertFalse(adapter._acked_reaction_replay(reaction))
        for field,value in [('status','unknown'),('root_id','0'*64),('content_hash','0'*64)]:
            with mock.patch.object(run.mapping_store,'delivery_by_source',return_value={**dict(row),field:value}):
                self.assertFalse(adapter._acked_reaction_replay(reaction))

    def test_reaction_against_foreign_channel_root_is_held_without_bot_write(self):
        world, run, adapter = self.assembly()
        original = base.FGS.sign_event(base.OWNER_KEY, 9, [['h', 'other-channel']], 'foreign', run.now_ts - 1000)
        world.events = [original]
        reaction = self.event(kind=7, tags=[['e', original['id']]], content='❌')
        with self.assertRaises(base.FGS.GroupSyncError): adapter.deliver(reaction)
        self.assertFalse(any(argv[2:5] == ['im', 'reactions', 'create'] for argv, _ in world.bot_calls))

    def test_old_proxy_copy_cannot_be_acknowledged_as_own_bot_delivery(self):
        world, run, adapter = self.assembly()
        event = self.event()
        world.events = [event]
        world.messages = [mapped.bot_message(event, mid='om_wrongbot')]
        run.state.b2f[event['id']] = 'om_wrongbot'
        with self.assertRaises(base.FGS.GroupSyncError): adapter.deliver(event)
        rows = list(run.mapping_store.conn.execute("SELECT status FROM delivery WHERE agent_id=? AND direction='b2f'", (base.AGENT2_PK,)))
        self.assertTrue(all(row['status'] != 'acked' for row in rows))

    def test_reply_reads_actual_root_through_own_app_and_keeps_thread_receipt(self):
        world, run, adapter = self.assembly()
        root = self.event(key=base.OWNER_KEY, created=run.now_ts - 1000)
        world.events = [root]
        world.messages = [mapped.bot_message(root, mid='om_replyroot')]
        reply = self.event(tags=[['e', root['id'], '', 'reply']])
        world.events.append(reply)
        adapter.deliver(reply)
        sends = [argv for argv, _ in world.bot_calls if '--content' in argv]
        self.assertEqual(sends[0][2:4], ['im', '+messages-reply'])
        self.assertEqual(sends[0][sends[0].index('--message-id') + 1], 'om_replyroot')
        reads = [argv for argv, _ in world.bot_calls if argv[2:4] == ['api', 'GET'] and '/messages/om_replyroot' in argv[4]]
        self.assertTrue(any(argv[argv.index('--profile') + 1] == APP for argv in reads))

    def test_own_image_caption_and_image_use_own_bot_and_separate_agent_delivery_rows(self):
        world, run, adapter = self.assembly()
        data = base.PNG_PLAIN
        digest = hashlib.sha256(data).hexdigest()
        event = self.event(tags=[['imeta', f'url https://relay.test/media/{digest}.png', 'm image/png',
                                  f'x {digest}', f'size {len(data)}']])
        world.events = [event]
        world.media[digest] = data
        adapter.deliver(event)
        self.assertEqual(len(world.image_sends), 1)
        self.assertEqual(world.image_sends[0]['app'], APP)
        image = run.mapping_store.delivery_by_source('test', event['id'] + ':0', 'image', agent_id=base.AGENT2_PK)
        self.assertEqual(image['status'], 'acked')
        adapter.deliver(event)
        self.assertEqual(len(world.image_sends), 1)

    def test_failed_own_image_remains_pending_and_blocks_later_agent_cursor_until_retry(self):
        world, run, adapter = self.assembly()
        digest = hashlib.sha256(base.PNG_PLAIN).hexdigest()
        event = self.event(tags=[['imeta', f'url https://relay.test/media/{digest}.png', 'm image/png', f'x {digest}']])
        world.events = [event]
        world.media[digest] = base.PNG_PLAIN
        world.media_fail = ['network']
        adapter.deliver(event)
        image = run.mapping_store.delivery_by_source('test', event['id'] + ':0', 'image', agent_id=base.AGENT2_PK)
        self.assertEqual(image['status'], 'pending')
        later = self.event(content='later', created=run.now_ts + 1)
        world.events.append(later)
        adapter.deliver(later)
        self.assertLess(run.mapping_store.cursor_position('test', 'relay', agent_id=base.AGENT2_PK), later['created_at'])
        adapter.deliver(event)
        self.assertEqual(len(world.image_sends), 1)
        self.assertEqual(run.mapping_store.delivery_by_source('test', event['id'] + ':0', 'image', agent_id=base.AGENT2_PK)['status'], 'acked')

    def test_mentions_use_fresh_bot_member_id_from_own_app(self):
        world, run, adapter = self.assembly()
        key = 'd7' * 32
        other = base.FGS._signer_pubkey(key)
        run.roles[other], run.names[other] = 'bot', 'other helper'
        world.relay_events += [profile(key), base.FGS.sign_event(base.OWNER_KEY, 30177, [['d', other]],
                                json.dumps({'feishu': {'app_id': base.AGENT_APP}}), run.now_ts - 30)]
        fresh = 'ou_outletviewagent0000000000000001'
        def runner(argv, **kwargs):
            answer = world(argv, **kwargs)
            if argv[2:4] == ['im', '+chat-members-list'] and argv[argv.index('--profile') + 1] == APP:
                body = json.loads(answer.stdout)
                for bot in body['data'].get('bots') or []:
                    if bot['app_id'] == base.AGENT_APP:
                        bot['member_id'] = fresh
                answer.stdout = json.dumps(body)
            return answer
        adapter.client.runner = runner
        event = self.event(tags=[['p', other]])
        world.events = [event]
        adapter.deliver(event)
        send = next(argv for argv, _ in world.bot_calls if '--content' in argv)
        card = send[send.index('--content') + 1]
        self.assertIn(fresh, card)
        self.assertNotIn(base.AGENT_BOT_MEMBER, card)

    def test_confirmed_public_receipt_recovers_unknown_send_after_retry_window_without_resending(self):
        world, run, adapter = self.assembly()
        event = self.event()
        world.events = [event]
        world.lark_send_fail = ['network_envelope']
        with self.assertRaises(base.FGS.GroupSyncError): adapter.deliver(event)
        row = run.mapping_store.delivery_by_source('test', event['id'], 'b2f', agent_id=base.AGENT2_PK)
        run.mapping_store.conn.execute('UPDATE delivery SET created_at=? WHERE id=?', (run.now_ts - 2701, row['id']))
        adapter.deliver(event)
        self.assertEqual(run.mapping_store.delivery_record(row['id'])['status'], 'acked')
        self.assertEqual(len([argv for argv, _ in world.bot_calls if '--content' in argv]), 1)

    def test_verified_earlier_same_chat_claim_on_other_channel_blocks_outlet(self):
        world, run, adapter = self.assembly()
        key = 'd8' * 32
        mirror = base.FGS._signer_pubkey(key)
        claim = {'mirror': True, 'bindings': [{'channel': 'e34c7e5b-d14a-4ea0-a9fc-7f51a64122c2',
                    'chat_ref': base.FGS.chat_ref(base.CHAT), 'claimed_at': run.now_ts - 100, 'heartbeat': run.now_ts}]}
        world.relay_events += [profile(key), base.FGS.sign_event(base.OWNER_KEY, 30177, [['d', mirror]],
                                             json.dumps({'feishu': claim}), run.now_ts - 100)]
        with self.assertRaises(base.FGS.GroupSyncError): adapter.deliver(self.event())
        self.assertFalse(any('--content' in argv for argv, _ in world.bot_calls))

    def test_new_namespace_uses_explicit_baseline_and_does_not_send_older_source(self):
        world, run, old = self.assembly()
        floor = run.now_ts - 100
        adapter = outlet.AgentOutlet(run, base.AGENT2_PK, self.tmp / 'outlet.env', bot_client=old.client,
            trusted_relays={'https://relay.test'}, http=world.http_get, clock=lambda: base.NOW, initial_since=floor)
        event = self.event(created=floor - 1)
        adapter.http = self.history_http(adapter, [event])
        world.events = [event]
        world.relay_events.append(event)
        adapter.catch_up()
        self.assertEqual(adapter.last_history_filter['since'], floor)
        self.assertFalse(any('--content' in argv for argv, _ in world.bot_calls))

    def test_existing_pending_below_new_baseline_keeps_authoritative_overlap_and_retries(self):
        world, run, adapter = self.assembly()
        newest = self.event(content='newest', created=run.now_ts)
        world.events = [newest]
        adapter.deliver(newest)
        older = self.event(content='pending earlier', created=run.now_ts - 100)
        world.events.append(older)
        world.lark_send_fail = ['timeout']
        with self.assertRaises(base.FGS.GroupSyncError): adapter.deliver(older)
        world.relay_events.append(older)
        rebuilt = outlet.AgentOutlet(run, base.AGENT2_PK, self.tmp / 'outlet.env', bot_client=adapter.client,
            trusted_relays={'https://relay.test'}, http=world.http_get, clock=lambda: base.NOW, initial_since=run.now_ts - 50)
        rebuilt.catch_up()
        request = next(q['filters'][0] for q in reversed(world.relay_queries) if '#h' in q['filters'][0] and 'authors' in q['filters'][0])
        self.assertEqual(request['since'], older['created_at'] - 900)
        self.assertEqual(run.mapping_store.delivery_by_source('test', older['id'], 'b2f', agent_id=base.AGENT2_PK)['status'], 'acked')


    def test_current_edit_materializes_only_exact_own_prebaseline_original(self):
        world,run,adapter=self.assembly();adapter.initial_since=run.now_ts-50
        original=self.event(created=run.now_ts-100,content='old original')
        edit=self.event(kind=40003,tags=[['e',original['id']]],content='current edit')
        unrelated=self.event(created=run.now_ts-200,content='unrelated history')
        world.events=[original,edit,unrelated]
        self.assertIsNone(adapter.deliver(unrelated))
        mid=adapter.deliver(edit)
        root=run.mapping_store.delivery_by_source('test',original['id'],'b2f',agent_id=base.AGENT2_PK)
        self.assertEqual(root['status'],'acked');self.assertEqual(root['target_id'],mid)
        self.assertEqual(adapter.deliver(edit),mid)
        self.assertEqual(len([a for a,_ in world.bot_calls if a[2:4]==['im','+messages-send']]),1)
        self.assertIsNone(run.mapping_store.delivery_by_source('test',unrelated['id'],'b2f',agent_id=base.AGENT2_PK))
        self.assertEqual(run.mapping_store.cursor_position('test','relay',agent_id=base.AGENT2_PK),edit['created_at'])

    def test_current_reply_materializes_own_old_root_and_uses_same_thread(self):
        world,run,adapter=self.assembly();adapter.initial_since=run.now_ts-50
        root=self.event(created=run.now_ts-100,content='old thread')
        reply=self.event(tags=[['e',root['id'],'','reply']],content='current reply')
        world.events=[root,reply];adapter.deliver(reply)
        root_receipt=run.mapping_store.delivery_by_source('test',root['id'],'b2f',agent_id=base.AGENT2_PK)
        calls=[a for a,_ in world.bot_calls if a[2:4]==['im','+messages-reply']]
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0][calls[0].index('--message-id')+1],root_receipt['target_id'])

    def test_current_reaction_materializes_only_own_old_target(self):
        world,run,adapter=self.assembly();adapter.initial_since=run.now_ts-50
        root=self.event(created=run.now_ts-100,content='old reaction target')
        reaction=self.event(kind=7,tags=[['e',root['id']]],content='👍')
        world.events=[root,reaction]
        adapter.deliver(reaction)
        receipt=run.mapping_store.delivery_by_source('test',root['id'],'b2f',agent_id=base.AGENT2_PK)
        self.assertEqual(receipt['status'],'acked')
        self.assertEqual(len([a for a,_ in world.bot_calls if a[2:4]==['im','+messages-send']]),1)
        self.assertEqual(len([a for a,_ in world.bot_calls if a[2:5]==['im','reactions','create']]),1)
        self.assertEqual(run.mapping_store.cursor_position('test','relay',agent_id=base.AGENT2_PK),reaction['created_at'])

    def test_reaction_does_not_materialize_foreign_unmapped_target(self):
        world,run,adapter=self.assembly()
        root=base.FGS.sign_event(base.OWNER_KEY,9,[['h',base.CHANNEL]],'foreign',run.now_ts-100)
        reaction=self.event(kind=7,tags=[['e',root['id']]],content='👍');world.events=[root,reaction]
        with self.assertRaises(base.FGS.GroupSyncError):adapter.deliver(reaction)
        self.assertFalse(any(a[2:4]==['im','+messages-send'] or a[2:5]==['im','reactions','create'] for a,_ in world.bot_calls))

    def test_reaction_does_not_retry_unknown_original_send(self):
        world,run,adapter=self.assembly()
        root=self.event(created=run.now_ts-100);world.events=[root];world.lark_send_fail=['timeout']
        with self.assertRaises(base.FGS.GroupSyncError):adapter.deliver(root)
        saved=run.mapping_store.delivery_by_source('test',root['id'],'b2f',agent_id=base.AGENT2_PK)
        run.mapping_store.fail_delivery(saved['id'],unknown=True,now=run.now_ts)
        sends=len([a for a,_ in world.bot_calls if '--content' in a])
        reaction=self.event(kind=7,tags=[['e',root['id']]],content='👍');world.events.append(reaction)
        with self.assertRaises(base.FGS.GroupSyncError):adapter.deliver(reaction)
        self.assertEqual(len([a for a,_ in world.bot_calls if '--content' in a]),sends)
        self.assertFalse(any(a[2:5]==['im','reactions','create'] for a,_ in world.bot_calls))

    def test_superseded_edit_does_not_materialize_unmapped_history(self):
        world,run,adapter=self.assembly();adapter.initial_since=run.now_ts-50
        root=self.event(created=run.now_ts-100)
        old=self.event(kind=40003,tags=[['e',root['id']]],created=run.now_ts-10,content='old edit')
        latest=self.event(kind=40003,tags=[['e',root['id']]],content='latest edit')
        world.events=[root,old,latest];world.relay_events.extend([old,latest])
        self.assertIsNone(adapter.deliver(old))
        self.assertFalse(any('--content' in a for a,_ in world.bot_calls))

    def test_dependency_never_materializes_foreign_author_or_invalid_known_mapping(self):
        from unittest import mock
        world,run,adapter=self.assembly();adapter.initial_since=run.now_ts-50
        foreign=self.event(created=run.now_ts-100,key=base.OWNER_KEY)
        reply=self.event(tags=[['e',foreign['id'],'','reply']])
        world.events=[foreign,reply]
        with self.assertRaises(base.FGS.GroupSyncError):adapter.deliver(reply)
        self.assertFalse(any('--content' in a for a,_ in world.bot_calls))
        original=self.event(created=run.now_ts-100)
        edit=self.event(kind=40003,tags=[['e',original['id']]],content='edit')
        world.events=[original,edit]
        with mock.patch.object(adapter,'_target',side_effect=base.FGS.GroupSyncError('unverified known mapping')):
            with self.assertRaises(base.FGS.GroupSyncError):adapter.deliver(edit)
        self.assertFalse(any('--content' in a for a,_ in world.bot_calls))

    def test_dependency_ack_does_not_commit_child_cursor_when_patch_fails(self):
        from unittest import mock
        world,run,adapter=self.assembly();adapter.initial_since=run.now_ts-50
        root=self.event(created=run.now_ts-100)
        edit=self.event(kind=40003,tags=[['e',root['id']]],content='edit')
        world.events=[root,edit]
        with mock.patch.object(adapter.client,'update_card',side_effect=base.FGS.GroupSyncError('uncertain patch')):
            with self.assertRaises(base.FGS.GroupSyncError):adapter.deliver(edit)
        saved=run.mapping_store.delivery_by_source('test',root['id'],'b2f',agent_id=base.AGENT2_PK)
        self.assertEqual(saved['status'],'acked')
        self.assertEqual(run.mapping_store.cursor_position('test','relay',agent_id=base.AGENT2_PK),0)

    def test_image_dependency_ack_and_reentry_do_not_create_replay_cursor(self):
        from unittest import mock
        world,run,adapter=self.assembly();adapter.initial_since=run.now_ts-50
        data=base.PNG_PLAIN;digest=hashlib.sha256(data).hexdigest();world.media[digest]=data
        root=self.event(created=run.now_ts-100,tags=[['imeta',f'url https://relay.test/media/{digest}.png',
            'm image/png',f'x {digest}',f'size {len(data)}']])
        edit=self.event(kind=40003,tags=[['e',root['id']]],content='edit');world.events=[root,edit]
        with mock.patch.object(adapter.client,'update_card',side_effect=base.FGS.GroupSyncError('uncertain patch')):
            with self.assertRaises(base.FGS.GroupSyncError):adapter.deliver(edit)
        image=run.mapping_store.delivery_by_source('test',root['id']+':0','image',agent_id=base.AGENT2_PK)
        self.assertEqual(image['status'],'acked');self.assertEqual(len(world.image_sends),1)
        adapter._deliver(root,frozenset({edit['id']}),dependency=True)
        self.assertEqual(len(world.image_sends),1)
        self.assertIsNone(run.mapping_store.conn.execute(
            "SELECT 1 FROM cursor WHERE binding_id='test' AND agent_id=? AND stream='relay'",
            (base.AGENT2_PK,)).fetchone())

    def test_unknown_dependency_cannot_be_reposted_for_new_edit(self):
        world,run,adapter=self.assembly()
        root=self.event(created=run.now_ts-100);world.events=[root]
        world.lark_send_fail=['timeout']
        with self.assertRaises(base.FGS.GroupSyncError):adapter.deliver(root)
        saved=run.mapping_store.delivery_by_source('test',root['id'],'b2f',agent_id=base.AGENT2_PK)
        run.mapping_store.fail_delivery(saved['id'],unknown=True,now=run.now_ts)
        sends=len([a for a,_ in world.bot_calls if '--content' in a])
        adapter.initial_since=run.now_ts-50
        edit=self.event(kind=40003,tags=[['e',root['id']]],content='edit');world.events.append(edit)
        with self.assertRaises(base.FGS.GroupSyncError):adapter.deliver(edit)
        self.assertEqual(len([a for a,_ in world.bot_calls if '--content' in a]),sends)

    def test_dependency_depth_is_bounded_before_any_post(self):
        world,run,adapter=self.assembly();adapter.initial_since=run.now_ts-50
        chain=[self.event(created=run.now_ts-200,content='ancestor')]
        for i in range(9):
            chain.append(self.event(created=run.now_ts-100+i,tags=[['e',chain[-1]['id'],'','reply']],content=str(i)))
        current=self.event(tags=[['e',chain[-1]['id'],'','reply']],content='current')
        world.events=chain+[current]
        with self.assertRaises(base.FGS.GroupSyncError):adapter.deliver(current)
        self.assertFalse(any('--content' in a for a,_ in world.bot_calls))


if __name__ == '__main__':
    unittest.main()
