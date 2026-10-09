"""ADR0027 real messages carry trusted delivery facts, never additional events."""
import copy
import base64
import hashlib
import json
from pathlib import Path
import sys
import unittest

TESTS = Path(__file__).resolve().parent
for p in (TESTS, TESTS.parent / 'scripts', TESTS.parent / 'scripts' / 'hostd'):
    sys.path.insert(0, str(p))
import test_hostd_bot_clients as bots
base = bots.base
import delivery_mapping as dm
from store import Store, BindingRecord
from state_store import StateAdapter

setUpModule = base.setUpModule
tearDownModule = base.tearDownModule


def signed(key=base.MIRROR_KEY, mid='om_root', root='om_root', tags=(), kind=9):
    return base.FGS.sign_event(key, kind, [['h', base.CHANNEL], ['feishu', mid], ['feishu-root', root], *tags], 'message', int(base.NOW.timestamp()))


def bot_message(event, app=base.AGENT_APP, mid='om_target', root=None, card=None):
    content = card or dm.message_card(event, 'Speaker', 'hello', base.API_ORIGIN, base.CHANNEL)
    return {'message_id': mid, 'chat_id': base.CHAT, 'root_id': root or '', 'sender': {'sender_type': 'app', 'id_type': 'app_id', 'id': app},
            'msg_type': 'interactive', 'content': content, 'body': {'content': content}}


class MappingContracts(base.TmpCase):
    def test_compact_agent_card_keeps_full_body_receipt_and_visible_mentions(self):
        event=signed()
        text='长回复\n'+'详细说明。'*100
        card=json.loads(dm.message_card(event,'Agent',text,base.API_ORIGIN,base.CHANNEL,
            compact=True,mentions=(base.FGS.CardMention('Alice',None,base.ALICE_OPEN),)))
        preview=card['elements'][0]['text']
        self.assertNotIn('header',card)
        self.assertEqual(preview['tag'],'lark_md')
        self.assertLessEqual(len(preview['content']),120)
        panel=next(e for e in card['elements'] if e['tag']=='collapsible_panel')
        self.assertFalse(panel['expanded'])
        self.assertEqual(preview['content']+panel['elements'][0]['text']['content'],base.FGS.card_markdown(text))
        self.assertIn(base.ALICE_OPEN,card['elements'][1]['text']['content'])
        self.assertIn(event['id'],card['elements'][-1]['elements'][0]['content'])

    def test_compact_gitlab_long_link_counts_visible_label_not_destination(self):
        title = '#11870 【KX1】录像开始数秒严重过曝，自动曝光调整后才恢复'
        text = '💬 评论 ·\n\n[' + title + '](https://gitlab.example.test/' + 'path/' * 40 + ')\nlabels ' + '用户反馈,' * 100
        card = json.loads(dm.message_card(signed(), 'Agent', text, base.API_ORIGIN, base.CHANNEL, compact=True))
        preview = card['elements'][0]['text']['content']
        remainder = card['elements'][1]['elements'][0]['text']['content']
        self.assertIn(title, preview)
        self.assertIn('labels', preview)
        self.assertEqual(preview + remainder, base.FGS.card_markdown(text))
        self.assertEqual(len(base.FGS._plain_markdown(preview)[0]), 120)
        self.assertNotIn('header', card)

    def test_preview_spanning_markup_does_not_hide_or_repeat_visible_text(self):
        cases = [('**' + '字' * 300 + '**', '**', '**'),
                 ('[' + '字' * 300 + '](https://example.test/long)', '[', '](https://example.test/long)'),
                 ('`' + '字' * 300 + '`', '`', '`')]
        for text, opening, closing in cases:
            with self.subTest(opening=opening):
                left, right, count = dm._preview_parts(text)
                self.assertEqual(count, 120)
                self.assertEqual(left, opening + '字' * 120 + closing)
                self.assertEqual(right, opening + '字' * 180 + closing)
        left, right, count = dm._preview_parts('```python\n' + '字' * 300 + '\n```')
        self.assertEqual((left, right, count), ('```python\n' + '字' * 120 + '\n```',
                         '```python\n' + '字' * 180 + '\n```', 120))

    def test_blank_lines_do_not_apply_four_raw_line_limit(self):
        text = '评论\n\n\n\n问题标题\n' + '详情' * 100
        left, right, count = dm._preview_parts(text)
        self.assertIn('问题标题', left)
        self.assertEqual(count, 120)
        self.assertEqual(left + right, text)

    def test_gitlab_notification_uses_object_title_before_opened_or_comment(self):
        header = '[gitlab-notify:v1][object:issue][type:bug][status:unknown][state:opened][change:activity][project:1504][issue:11870][desc:ed7f9dc9073f][note:664643]'
        title = '[#11870 【KX1】录像开始数秒严重过曝](https://gitlab.example.test/group/project/-/issues/11870#note_664643)'
        for phrase in ('💬 **评论**', '🟢 **已打开**'):
            text = phrase + ' · ' + title + '\nlabels priority::P1\nby: xjiao\n\n评论正文' + '详情' * 200 + '\n' + header
            card = json.loads(dm.message_card(signed(), 'Agent', text, base.API_ORIGIN, base.CHANNEL, compact=True))
            left = card['elements'][0]['text']['content']
            right = card['elements'][1]['elements'][0]['text']['content']
            self.assertTrue(left.startswith(title + '\n' + phrase))
            self.assertEqual((left + right).count('评论正文'), 1)
            self.assertNotIn('[gitlab-notify:', left + right)
            self.assertIn('labels priority::P1', left + right)
            self.assertNotIn('header', card)
            old = dm.message_card(signed(), 'Agent', text, base.API_ORIGIN, base.CHANNEL, compact='preview_v2')
            self.assertIn('[gitlab-notify:', old)
        ordinary = '讨论\n' + header + '\n保留原文'
        self.assertEqual(dm._gitlab_card_body(ordinary), ordinary)
        malformed = '评论\n[gitlab-notify:v1][invalid]'
        self.assertEqual(dm._gitlab_card_body(malformed), malformed)

    def test_compact_preview_bounds_link_bytes_and_emphasis_whitespace(self):
        text = '[label](https://example.test/' + 'x' * 32000 + ')\n' + 'body ' * 100
        raw = dm.message_card(signed(), 'Agent', text, base.API_ORIGIN, base.CHANNEL, compact=True)
        self.assertLess(len(raw.encode()), base.FGS.MAX_CARD_BYTES)
        self.assertIn(base.FGS.CARD_TRUNCATED_NOTE, raw)
        preview = json.loads(raw)['elements'][0]['text']
        self.assertEqual(preview['tag'], 'plain_text')
        self.assertTrue(preview['content'].startswith('label'))
        for inner in ('a' * 119 + ' ' + 'b' * 180, 'a' * 120 + ' ' + 'b' * 180):
            left, right, count = dm._preview_parts('**' + inner + '**')
            self.assertEqual(count, 120)
            self.assertEqual(base.FGS._plain_markdown(left)[0] + base.FGS._plain_markdown(right)[0], inner)

    def test_gitlab_mr_title_keeps_author_status_and_no_extra_header(self):
        text = '🔀 **新建 · 已打开** · [!42 Fix camera](https://gitlab.example.test/group/project/-/merge_requests/42) · author\n评论\n[gitlab-notify:v1][object:mr][state:opened][draft:no][change:lifecycle][transition:reviewable][project:1504][mr:42]'
        from gitlab_buzz_sync import parse_header
        self.assertIsNotNone(parse_header(text))
        display = dm._gitlab_card_body(text)
        self.assertTrue(display.startswith('[!42 Fix camera]'))
        self.assertIn('🔀 **新建 · 已打开** · author', display)
        self.assertIn('评论', display)
        self.assertNotIn('[gitlab-notify:', display)

    def test_gitlab_keeps_malformed_opposite_edge_and_giant_fence_is_bounded(self):
        valid = '[gitlab-notify:v1][object:issue][type:bug][status:unknown][state:opened][change:activity][project:1504][issue:11870]'
        malformed = '[gitlab-notify:v1][invalid-comment]'
        for text in (valid + '\n正文\n' + malformed, malformed + '\n正文\n' + valid):
            self.assertIn(malformed, dm._gitlab_card_body(text))
        text = '```' + 'lang' * 8000 + '\n' + 'body ' * 100 + '\n```'
        raw = dm.message_card(signed(), 'Agent', text, base.API_ORIGIN, base.CHANNEL, compact=True)
        self.assertLess(len(raw.encode()), base.FGS.MAX_CARD_BYTES)
        self.assertIn(base.FGS.CARD_TRUNCATED_NOTE, raw)
        self.assertTrue(json.loads(raw)['elements'][0]['text']['content'].startswith('body '))

    def test_native_card_projection_preserves_body_links_and_nonempty_i18n(self):
        expected = json.loads(dm.message_card(signed(), 'Speaker', 'hello', base.API_ORIGIN, base.CHANNEL))
        native = copy.deepcopy(expected)
        native['config'] = {}
        native['header']['title']['i18n'] = {}
        self.assertEqual(dm.card_document(json.dumps(expected)), dm.card_document(json.dumps(native, sort_keys=True)))
        for path, value in [('body', 'changed'), ('link', 'https://wrong.test'), ('i18n', {'en_us': 'changed'})]:
            wrong = copy.deepcopy(native)
            if path == 'body': wrong['elements'][0]['text']['content'] = value
            elif path == 'link': wrong['elements'][-1]['elements'][0]['content'] = value
            else: wrong['header']['title']['i18n'] = value
            self.assertNotEqual(dm.card_document(expected), dm.card_document(wrong))
        with self.assertRaises(ValueError): dm.card_document('{"config":{},"config":{}}')
        with self.assertRaises(ValueError): dm.card_document({'config': {'update_multi': 'true'}})

    def test_multibyte_card_truncates_within_feishu_limit_and_keeps_receipt(self):
        event = base.FGS.sign_event(base.OWNER_KEY, 9, [['h', base.CHANNEL]], '😀' * 20000, int(base.NOW.timestamp()))
        raw = dm.message_card(event, 'Speaker', event['content'], base.API_ORIGIN, base.CHANNEL)
        self.assertLess(len(raw.encode()), base.FGS.MAX_CARD_BYTES)
        self.assertIn(event['id'], json.loads(raw)['elements'][-1]['elements'][0]['content'])
        self.assertIn(base.FGS.CARD_TRUNCATED_NOTE, raw)

    def test_signed_kind9_unique_channel_and_author_boundary(self):
        ev = signed()
        mapping = dm.buzz_mapping(ev, base.CHANNEL, {base.OWNER_PK}, {base.MIRROR_PK})
        self.assertEqual((mapping.event_id, mapping.message_id, mapping.feishu_root), (ev['id'], 'om_root', 'om_root'))
        bad = [signed(key='03' * 32), signed(kind=7), signed(tags=[['h', base.CHANNEL]]), signed(mid='private@example.test'),
               signed(tags=[['feishu', 'om_other']]), dict(ev, content='forged')]
        for value in bad:
            with self.subTest(id=value['id']):
                self.assertIsNone(dm.buzz_mapping(value, base.CHANNEL, {base.OWNER_PK}, {base.MIRROR_PK}))
        self.assertIsNone(dm.buzz_mapping(ev, base.CHANNEL, {base.OWNER_PK}, set()))
        authored = signed(key=base.OWNER_KEY)
        self.assertIsNotNone(dm.buzz_mapping(authored, base.CHANNEL, {base.OWNER_PK}, set()))

    def test_card1_bottom_link_readback_checks_original_author_actual_bot_and_exact_origin(self):
        ev = base.FGS.sign_event(base.OWNER_KEY, 9, [['h', base.CHANNEL]], 'human says', int(base.NOW.timestamp()))
        msg = bot_message(ev)
        card = json.loads(msg['body']['content'])
        self.assertNotEqual(card.get('schema'), '2.0')
        self.assertEqual(card['elements'][-1]['tag'], 'note')
        self.assertIsNotNone(dm.feishu_mapping(msg, ev, base.CHANNEL, base.CHAT, base.API_ORIGIN, {base.OWNER_PK}, {}, base.AGENT_APP))
        for changes in [dict(chat_id='oc_wrong'), dict(sender={'sender_type': 'user', 'id_type': 'app_id', 'id': base.AGENT_APP}),
                        dict(sender={'sender_type': 'app', 'id_type': 'app_id', 'id': base.OWNER_APP})]:
            self.assertIsNone(dm.feishu_mapping(dict(msg, **changes), ev, base.CHANNEL, base.CHAT, base.API_ORIGIN, {base.OWNER_PK}, {}, base.AGENT_APP))
        forged = copy.deepcopy(msg)
        forged['body']['content'] = forged['body']['content'].replace(base.API_ORIGIN, 'https://evil.test')
        self.assertIsNone(dm.feishu_mapping(forged, ev, base.CHANNEL, base.CHAT, base.API_ORIGIN, {base.OWNER_PK}, {}, base.AGENT_APP))

    def test_agent_receipt_requires_verified_directory_application(self):
        ev = base.FGS.sign_event(base.AGENT2_KEY, 9, [['h', base.CHANNEL]], 'agent says', int(base.NOW.timestamp()))
        msg = bot_message(ev)
        self.assertIsNone(dm.feishu_mapping(msg, ev, base.CHANNEL, base.CHAT, base.API_ORIGIN, set(), {}, base.AGENT_APP))
        self.assertIsNotNone(dm.feishu_mapping(msg, ev, base.CHANNEL, base.CHAT, base.API_ORIGIN, set(), {base.AGENT2_PK: base.AGENT_APP}, base.OWNER_APP))

    def test_reply_receipt_requires_matching_root_proof(self):
        root = base.FGS.sign_event(base.OWNER_KEY, 9, [['h', base.CHANNEL]], 'root', int(base.NOW.timestamp()))
        ev = base.FGS.sign_event(base.OWNER_KEY, 9, [['h', base.CHANNEL], ['e', root['id'], '', 'root'], ['e', root['id'], '', 'reply']], 'reply', int(base.NOW.timestamp()))
        msg = bot_message(ev, root='om_root')
        args = (msg, ev, base.CHANNEL, base.CHAT, base.API_ORIGIN, {base.OWNER_PK}, {}, base.AGENT_APP)
        self.assertIsNone(dm.feishu_mapping(*args))
        self.assertIsNotNone(dm.feishu_mapping(*args, root_mapping=dm.DeliveryMapping(root['id'], 'om_root', 'om_root', None, 'b2f', base.AGENT_APP)))
        contradictory = dm.DeliveryMapping('ab' * 32, 'om_root', 'om_root', None, 'b2f', base.AGENT_APP)
        self.assertIsNone(dm.feishu_mapping(*args, root_mapping=contradictory))

    def test_reply_only_receipt_requires_matching_original_thread(self):
        root = base.FGS.sign_event(base.OWNER_KEY, 9, [['h', base.CHANNEL]], 'root', int(base.NOW.timestamp()))
        ev = base.FGS.sign_event(base.OWNER_KEY, 9, [['h', base.CHANNEL], ['e', root['id'], '', 'reply']], 'reply', int(base.NOW.timestamp()))
        proof = dm.DeliveryMapping(root['id'], 'om_root', 'om_root', None, 'b2f', base.AGENT_APP)
        msg = bot_message(ev, root='om_root')
        args = (msg, ev, base.CHANNEL, base.CHAT, base.API_ORIGIN, {base.OWNER_PK}, {}, base.AGENT_APP)
        self.assertIsNone(dm.feishu_mapping(*args))
        self.assertIsNotNone(dm.feishu_mapping(*args, root_mapping=proof))
        self.assertIsNone(dm.feishu_mapping(dict(msg, root_id=''), *args[1:], root_mapping=proof))
        self.assertIsNone(dm.feishu_mapping(*args, root_mapping=dm.DeliveryMapping('a'*64, 'om_root', 'om_root', None, 'b2f', base.AGENT_APP)))

    def test_post_readback_only_uses_last_exact_navigation_link(self):
        ev = base.FGS.sign_event(base.OWNER_KEY, 9, [['h', base.CHANNEL]], 'post', int(base.NOW.timestamp()))
        url = base.FGS.open_link(base.API_ORIGIN, ev['id'], base.CHANNEL, None)
        content = {'zh_cn': {'title': '', 'content': [[{'tag': 'text', 'text': 'hello'}],
                    [{'tag': 'a', 'text': '在 Buzz 中打开', 'href': url}]]}}
        msg = bot_message(ev)
        msg.update(msg_type='post', body={'content': json.dumps(content)})
        self.assertIsNotNone(dm.feishu_mapping(msg, ev, base.CHANNEL, base.CHAT, base.API_ORIGIN, {base.OWNER_PK}, {}, base.AGENT_APP))
        content['zh_cn']['content'].append([{'tag': 'text', 'text': 'forged trailing row'}])
        msg['body']['content'] = json.dumps(content)
        self.assertIsNone(dm.feishu_mapping(msg, ev, base.CHANNEL, base.CHAT, base.API_ORIGIN, {base.OWNER_PK}, {}, base.AGENT_APP))

    def test_signed_malformed_tags_and_duplicate_reply_proofs_are_rejected(self):
        for tags in [[{'bad': 'signed malformed tag'}], [['e', 'ab' * 32, '', 'root'], ['e', 'cd' * 32, '', 'root']]]:
            ev = signed(tags=tags)
            self.assertIsNone(dm.buzz_mapping(ev, base.CHANNEL, {base.OWNER_PK}, {base.MIRROR_PK}))

    def test_upload_descriptor_cannot_redirect_hash_size_or_type(self):
        file = self.tmp / 'image.png'
        file.write_bytes(base.PNG_PLAIN)
        digest = hashlib.sha256(base.PNG_PLAIN).hexdigest()
        descriptor = {'url': f'https://relay.test/media/{digest}.png', 'sha256': digest, 'size': len(base.PNG_PLAIN), 'type': 'image/png'}
        for change in ({'url': f'https://evil.test/media/{digest}.png'}, {'sha256': 'ab' * 32}, {'size': 1}, {'type': 'image/jpeg'}):
            def answer(*args, **kwargs): return 200, json.dumps(dict(descriptor, **change)).encode()
            with self.assertRaises(base.FGS.GroupSyncError):
                dm.upload_image(file, 'https://relay.test', base.MIRROR_KEY, base.NOW, answer)


class MappingWorld(bots.BotWorld):
    def media_upload(self, url, headers, timeout, *, body):
        if not hasattr(self, 'uploads'):
            self.uploads = []
        auth = json.loads(base64.urlsafe_b64decode(headers['Authorization'][6:] + '==='))
        digest = hashlib.sha256(body).hexdigest()
        assert base.FGS._nip01_event_verified(auth)
        assert auth['kind'] == 24242 and auth['pubkey'] == base.MIRROR_PK
        assert ['t', 'upload'] in auth['tags'] and ['x', digest] in auth['tags']
        assert ['server', 'relay.test'] in auth['tags']
        assert headers['X-SHA-256'] == digest and url == 'https://relay.test/upload'
        self.uploads.append((url, dict(headers), bytes(body)))
        return 200, json.dumps({'url': f'https://relay.test/media/{digest}.png', 'sha256': digest,
                              'size': len(body), 'type': 'image/png'}).encode()
    def __call__(self, argv, **kwargs):
        if argv[0] == bots.bc.NODE and argv[2:4] == ['im', '+messages-resources-download']:
            mid, key = argv[argv.index('--message-id') + 1], argv[argv.index('--file-key') + 1]
            self.bot_calls.append((argv, kwargs))
            (Path(kwargs['cwd']) / 'download.png').write_bytes(self.resources[(mid, key)])
            return bots.ok({'saved_path': 'download.png'})
        if argv[0] == bots.bc.NODE and argv[2:4] == ['api', 'GET'] and argv[4].startswith('/open-apis/im/v1/messages/'):
            mid = argv[4].rsplit('/', 1)[1]
            raw = next((m for m in self.messages if m['message_id'] == mid and 'body' in m), None)
            if raw:
                self.bot_calls.append((argv, kwargs))
                return bots.ok({'items': [copy.deepcopy(raw)]})
        return super().__call__(argv, **kwargs)

    def _card_refusal(self, args):
        content = self._opt(args, '--content')
        if content and json.loads(content).get('elements'):
            return None  # Actual card1.0 request supported by Feishu; old fixture only checks card2.0.
        return super()._card_refusal(args)
    def _serve_relay_query(self, url, headers, body):
        status, raw = super()._serve_relay_query(url, headers, body)
        filters = json.loads(body)
        if any('#feishu' in row or 'ids' in row or (row.get('kinds') == [9] and '#h' in row) for row in filters):
            self.assert_signed_query(headers, url, body)
            found = [ev for ev in self.events if any(
                (not f.get('ids') or ev['id'] in f['ids'])
                and (not f.get('authors') or ev['pubkey'] in f['authors']) and ev['kind'] in f.get('kinds', [9])
                and (not f.get('#h') or any(t[:1] == ['h'] and t[1] in f['#h'] for t in ev['tags'])) for f in filters)]
            # Actual relay indexes single-letter tags, not #feishu.
            found = found[:max(f.get('limit', len(found)) for f in filters)]
            return 200, json.dumps(found).encode()
        return status, raw

    def assert_signed_query(self, headers, url, body):
        if self._verify_relay_nip98(headers['Authorization'], url, body) is None:
            raise AssertionError('query not signed')


class MappedAssembly(base.TmpCase):
    def assembly(self, **overrides):
        env, cfg, _, old = bots.RoundAssembly.assembly(self, **overrides)
        world = MappingWorld(self.tmp)
        world.members = [m for m in world.members if m['pubkey'] != base.CAROL_PK]
        world.needs_auth_tag.clear()
        clients = bots.bc.build_clients(cfg, env.base_env, runner=world, http=world.http_get, trusted_relays={'https://relay.test'})
        db = Store(self.tmp / 'hostd.db')
        self.addCleanup(db.close)
        db.reconcile_bindings([BindingRecord('test', cfg['channel_id'], cfg['chat_id'], base.AGENT_APP, str(env.config), str(self.tmp / 'agent-cfg'), str(self.tmp / 'agent-data'), cfg['mirror_pubkey'])], now=int(base.NOW.timestamp()))
        adapter = StateAdapter(db, 'test', env.state_dir, profile_id=f'bot:{base.AGENT_APP}:union_id:v1')
        state = adapter.load()
        holder = {}
        def persist(): adapter.save(holder['run'].state)
        run = dm.MappedHostdRound(cfg, clients, state, base.FGS._new_report(), base.NOW, persist,
                                  reader_namespace=base.AGENT_APP, store=db, binding_id='test', auth_clock=lambda: base.NOW,
                                  media_http=world.media_upload)
        holder['run'] = run
        run.verify_identities(); run.load_people(); run.load_directory(); run.verify_desk()
        return env, cfg, world, run

    def test_feishu_kind9_self_carries_ids_and_sql_ack_without_extra_event(self):
        _, _, world, run = self.assembly()
        world.messages = [base.fmsg('om_human', base.ALICE_OPEN, 'hello')]
        run.feishu_to_buzz(only_threads=set())
        messages = [ev for ev in world.relay_writes if ev['kind'] == 9]
        self.assertEqual(len(messages), 1)
        ev = messages[0]
        self.assertTrue(all(isinstance(value, str) for tag in ev['tags'] for value in tag))
        self.assertFalse(any(tag[:1] == ['e'] for tag in ev['tags']))
        self.assertIn(['feishu', 'om_human'], ev['tags'])
        self.assertIn(['feishu-root', 'om_human'], ev['tags'])
        self.assertEqual(ev['pubkey'], base.MIRROR_PK)
        self.assertEqual(run.state.f2b['om_human'], ev['id'])
        row = run.mapping_store.conn.execute("SELECT * FROM delivery WHERE direction='f2b' AND source_id='om_human'").fetchone()
        self.assertEqual((row['status'], row['target_id']), ('acked', ev['id']))

    def test_buzz_human_sent_actual_card1_with_recoverable_footer(self):
        _, _, world, run = self.assembly()
        ev = base.FGS.sign_event(base.OWNER_KEY, 9, [['h', base.CHANNEL]], 'human hello', int(base.NOW.timestamp()))
        world.events = [ev]
        run.buzz_to_feishu()
        sent = next(argv for argv, _ in world.bot_calls if '--content' in argv)
        card = json.loads(sent[sent.index('--content') + 1])
        self.assertNotEqual(card.get('schema'), '2.0')
        self.assertIn(ev['id'], card['elements'][-1]['elements'][0]['content'])
        self.assertEqual(run.report['to_feishu'], 1)

    def test_lost_answer_retries_identical_signed_message_not_extra_event(self):
        _, _, world, run = self.assembly()
        world.messages = [base.fmsg('om_human', base.ALICE_OPEN, 'hello')]
        world.relay_write_fail = ['lost']
        run.feishu_to_buzz(only_threads=set())
        first = [ev for ev in world.relay_write_requests if ev['kind'] == 9][0]
        from datetime import timedelta
        run.now += timedelta(seconds=31); world.clock = run.now; run.auth_clock = lambda: run.now
        run.feishu_to_buzz(only_threads=set())
        posted = [ev for ev in world.relay_write_requests if ev['kind'] == 9]
        self.assertTrue(all(ev == first for ev in posted))
        self.assertEqual(len([ev for ev in world.events if ev['kind'] == 9]), 1)
        self.assertEqual(run.state.f2b['om_human'], first['id'])

    def test_old_root_lookup_does_not_depend_on_unindexed_multiletter_tag(self):
        from unittest import mock
        for cached in (True, False):
            with self.subTest(cached=cached):
                _, _, world, run = self.assembly()
                root = base.fmsg('om_old', base.ALICE_OPEN, 'old root')
                event = signed(mid='om_old', root='om_old')
                unrelated = [signed(mid='om_new' + str(i), root='om_new' + str(i)) for i in range(3)]
                world.messages = [root]
                if cached: run.state.f2b['om_old'] = event['id']
                world.events = [*unrelated, event]
                queries = []
                original_http = run.clients.http
                def query(url, headers, timeout, *, body=None):
                    if url.endswith('/query') and body:
                        queries.extend(f for f in json.loads(body) if f.get('kinds') == [9])
                    return original_http(url, headers, timeout, body=body)
                with mock.patch.object(run.clients, 'http', side_effect=query):
                    mapping = run.resolve_feishu('om_old')
                self.assertIsNotNone(mapping)
                self.assertEqual(mapping.event_id, event['id'])
                self.assertTrue(all('#feishu' not in f for f in queries))
                if cached:self.assertEqual(queries[0]['ids'], [event['id']])
                # assembly creates the same DB fixture path; next iteration uses it.
                run.mapping_store.close()
                for path in self.tmp.glob('hostd.db*'):path.unlink()

    def test_event_recovery_reuses_exact_direct_snapshot_but_not_negative_lookup(self):
        from unittest import mock
        _, _, world, run = self.assembly()
        world.messages = [base.fmsg('om_observed', base.ALICE_OPEN, 'new root')]
        observed = run.clients.owner.message_view('om_observed', 'open_id')
        with mock.patch.object(run.clients.owner, 'message_view', wraps=run.clients.owner.message_view) as read:
            run.recover_feishu_event({'message_id': 'om_observed'}, observed_message=observed)
        # Reuse the supplied open_id view, but verify the actual human's
        # union_id before deriving the supported authors filter.
        self.assertEqual(read.call_args_list, [mock.call('om_observed', 'union_id')])
        self.assertNotIn('om_observed', run.state.f2b)
        # Another sender/host may have completed the write after that miss.
        # The next resolve must actually query again, including same-round UNKNOWN recovery.
        ev = signed(mid='om_observed', root='om_observed')
        world.events.append(ev)
        with mock.patch.object(run.clients.owner, 'message_view', wraps=run.clients.owner.message_view) as read:
            recovered = run.resolve_feishu('om_observed')
        self.assertEqual(recovered.event_id, ev['id'])
        self.assertGreater(read.call_count, 0)

    def test_inbound_bot_history_does_not_lookup_every_card_mapping(self):
        from unittest import mock
        _, _, world, run = self.assembly()
        world.messages = [base.fmsg('om_bot' + str(i), base.AGENT_APP, 'old bot card', sender_type='app')
                          for i in range(100)]
        with mock.patch.object(run, 'resolve_feishu', side_effect=AssertionError('irrelevant bot mapping read')):
            run.feishu_to_buzz(only_threads=set())
        self.assertEqual(run.report['to_buzz'], 0)
        self.assertFalse(run.state.f2b)

    def test_deleted_retry_still_closes_and_unknown_is_not_resent(self):
        _, _, world, run = self.assembly()
        world.messages = [base.fmsg('om_retry', base.ALICE_OPEN, 'deleted', deleted=True),
                          base.fmsg('om_unknown', base.ALICE_OPEN, 'deleted', deleted=True)]
        run.state.f2b['om_retry'] = 'retry:' + str(run.now_ts)
        run.state.f_unresolved['om_retry'] = run.now_ts
        run.state.f2b['om_unknown'] = base.FGS.UNKNOWN
        run.feishu_to_buzz(only_threads=set())
        self.assertNotIn('om_retry', run.state.f_unresolved)
        self.assertFalse(base.FGS._is_retry(run.state.f2b.get('om_retry')))
        self.assertEqual(run.state.f2b['om_unknown'], base.FGS.UNKNOWN)
        self.assertEqual(run.report['to_buzz'], 0)

    def test_observed_snapshot_never_overrides_target_or_chat(self):
        import copy
        _, _, world, run = self.assembly()
        world.messages = [base.fmsg('om_observed', base.ALICE_OPEN, 'root')]
        observed = run.clients.owner.message_view('om_observed', 'open_id')
        for change in ({'message_id': 'om_other'}, {'chat_id': 'oc_other'}, {'root_id': 'om_other'}):
            value = dict(copy.deepcopy(observed), **change)
            with self.subTest(change=change), self.assertRaises(base.FGS.GroupSyncError):
                run.recover_feishu_event({'message_id': 'om_observed', 'root_id': 'om_observed'}, observed_message=value)
        self.assertFalse(run.state.f2b)

    def test_missing_old_root_recovers_signed_tag_and_direct_get_then_attaches_reply(self):
        _, _, world, run = self.assembly()
        root = base.fmsg('om_old', base.ALICE_OPEN, 'old root')
        reply = base.fmsg('om_reply', base.ALICE_OPEN, 'reply', when=base.NOW)
        reply['root_id'] = 'om_old'
        ev = signed(mid='om_old', root='om_old')
        world.events = [ev]
        world.messages = [root]
        world.threads = {'om_old': [reply]}
        run.state.floor = int(base.NOW.timestamp()) - 10
        root['create_time'] = str((run.state.floor - 5000) * 1000)
        run.recover_feishu_event({'message_id': 'om_reply'})
        self.assertEqual(run.state.f2b['om_old'], ev['id'])
        self.assertEqual(run.state.b2f[ev['id']], 'om_old')
        self.assertTrue(run.state.rwatch['om_old'].startswith(ev['id']))
        run.feishu_to_buzz(only_threads={'om_old'})
        published = [ev for ev in world.relay_writes if ev['kind'] == 9]
        self.assertEqual(len(published), 1)
        self.assertIn(['e', ev['id'], '', 'reply'], published[0]['tags'])

    def test_mapping_hint_is_only_a_query_hint_not_proof(self):
        from unittest import mock
        _, _, world, run = self.assembly()
        world.messages = [base.fmsg('om_old', base.ALICE_OPEN, 'root')]
        actual = signed(mid='om_old', root='om_old')
        unrelated = signed(mid='om_other', root='om_other')
        for hint in ('not-an-id', 42, unrelated['id']):
            with self.subTest(hint=hint):
                run.state.f2b['om_old'] = hint
                # Even if the server ignores ids and returns the wanted root,
                # it must not override the settled ledger's different target.
                with mock.patch.object(run, '_relay_read', return_value=[actual]):
                    self.assertIsNone(run.resolve_feishu('om_old'))
                self.assertEqual(run.state.f2b['om_old'], hint)
        run.state.f2b['om_old'] = unrelated['id']
        with mock.patch.object(run, '_relay_read', return_value=[unrelated]):
            self.assertIsNone(run.resolve_feishu('om_old'))

    def test_mapping_scan_full_raw_page_is_incomplete_even_with_invalid_signature(self):
        from unittest import mock
        _, _, world, run = self.assembly()
        world.messages = [base.fmsg('om_old', base.ALICE_OPEN, 'root')]
        event = signed(mid='om_old', root='om_old')
        invalid = dict(event, sig='0' * 128)
        for rows in ([event, event], [event, invalid]):
            with self.subTest(invalid=rows[-1] is invalid):
                with mock.patch.object(dm, 'RELAY_LOOKUP_LIMIT', 2), mock.patch.object(
                        world, '_serve_relay_query', return_value=(200, json.dumps(rows).encode())):
                    with self.assertRaises(base.FGS.GroupSyncError):
                        run.resolve_feishu('om_old')
                self.assertNotIn('om_old', run.state.f2b)

    def test_mapping_scan_conflicting_exact_tags_is_unresolved(self):
        from unittest import mock
        _, _, world, run = self.assembly()
        world.messages = [base.fmsg('om_old', base.ALICE_OPEN, 'root')]
        events = [signed(mid='om_old', root='om_old'),
                  signed(mid='om_old', root='om_old', tags=[['extra', 'duplicate']])]
        world.events = events
        self.assertIsNone(run.resolve_feishu('om_old'))
        self.assertNotIn('om_old', run.state.f2b)

    def test_recovery_authors_derive_from_actual_human_and_verified_mirrors(self):
        from unittest import mock
        _, _, world, run = self.assembly()
        world.messages = [base.fmsg('om_actual', base.ALICE_OPEN, 'actual')]
        wanted = signed(mid='om_actual', root='om_actual')
        forged = signed(key=base.OWNER_KEY, mid='om_actual', root='om_actual')
        world.events = [forged, wanted]
        queries = []
        original = run.clients.http
        def http(url, headers, timeout, *, body=None):
            if body and url.endswith('/query'):
                queries.extend(f for f in json.loads(body) if f.get('kinds') == [9])
            return original(url, headers, timeout, body=body)
        with mock.patch.object(run.clients, 'http', side_effect=http):
            found = run.resolve_feishu('om_actual')
        self.assertEqual(found.event_id, wanted['id'])
        self.assertEqual(set(queries[0]['authors']), {base.ALICE_PK, base.MIRROR_PK} | run.other_mirrors)
        self.assertNotIn(base.OWNER_PK, queries[0]['authors'])

    def test_missing_mirror_directory_cannot_be_used_as_absence(self):
        from unittest import mock
        _, _, world, run = self.assembly()
        world.messages = [base.fmsg('om_actual', base.ALICE_OPEN, 'actual')]
        run.agent_lookup_failed = True
        with mock.patch.object(dm, 'signed_history', side_effect=AssertionError('must not query narrowed authors')):
            with self.assertRaises(base.FGS.GroupSyncError):
                run.resolve_feishu('om_actual')
        self.assertNotIn('om_actual', run.state.f2b)

    def test_original_author_cannot_forge_mapping_to_another_human_message(self):
        _, _, world, run = self.assembly()
        world.messages = [base.fmsg('om_victim', base.ALICE_OPEN, 'victim')]
        world.events = [signed(key=base.OWNER_KEY, mid='om_victim', root='om_victim')]
        self.assertIsNone(run.resolve_feishu('om_victim'))
        self.assertNotIn('om_victim', run.state.f2b)

    def test_changed_source_after_unknown_publish_is_held_without_second_publish(self):
        _, _, world, run = self.assembly()
        world.messages = [base.fmsg('om_changed', base.ALICE_OPEN, 'original')]
        world.relay_write_fail = ['network']
        run.feishu_to_buzz(only_threads=set())
        world.messages[0]['content'] = 'edited before outcome known'
        sealed=run.mapping_store.delivery_by_source(run.binding_id,'om_changed','f2b')['content_hash']
        world.messages.append(base.fmsg('om_later',base.ALICE_OPEN,'later independent message'))
        from datetime import timedelta
        run.now += timedelta(seconds=31); world.clock = run.now; run.auth_clock = lambda: run.now
        run.feishu_to_buzz(only_threads=set())
        posts=[ev for ev in world.relay_write_requests if ev['kind']==9]
        self.assertEqual(len(posts),2)
        self.assertEqual(run.state.f2b['om_changed'],base.FGS.UNKNOWN)
        self.assertEqual(run.state.f2b['om_later'],posts[-1]['id'])
        row=run.mapping_store.delivery_by_source(run.binding_id,'om_changed','f2b')
        self.assertEqual((row['status'],row['content_hash']),('unknown',sealed))
        run.feishu_to_buzz(only_threads=set())
        self.assertEqual(len([ev for ev in world.relay_write_requests if ev['kind']==9]),2)

    def test_delayed_retry_does_not_add_new_stale_suffix_to_sealed_event(self):
        from datetime import timedelta
        _,_,world,run=self.assembly()
        world.messages=[base.fmsg('om_delayed',base.ALICE_OPEN,'original')]
        world.relay_write_fail=['network']
        run.feishu_to_buzz(only_threads=set())
        first=next(ev for ev in world.relay_write_requests if ev['kind']==9)
        run.now+=timedelta(seconds=base.FGS.STALE_AFTER_SECONDS+60)
        world.clock=run.now
        run.auth_clock=lambda:run.now
        run.feishu_to_buzz(only_threads=set())
        posts=[ev for ev in world.relay_write_requests if ev['kind']==9]
        self.assertEqual(len(posts),2)
        self.assertEqual(posts[0],posts[1])
        self.assertEqual(run.state.f2b['om_delayed'],first['id'])

    def test_slow_round_publish_uses_fresh_auth_but_fixed_event_time(self):
        from datetime import timedelta
        _,_,world,run=self.assembly()
        world.messages=[base.fmsg('om_slow_round',base.ALICE_OPEN,'original')]
        world.clock=run.now+timedelta(seconds=120)
        run.auth_clock=lambda:world.clock
        run.feishu_to_buzz(only_threads=set())
        posted=next(ev for ev in world.relay_write_requests if ev['kind']==9)
        self.assertEqual(posted['created_at'],int(run.now.timestamp()))
        self.assertEqual(run.state.f2b['om_slow_round'],posted['id'])

    def test_edit_keeps_mapping_footer_and_replaces_visible_content(self):
        _, _, world, run = self.assembly()
        original = base.FGS.sign_event(base.OWNER_KEY, 9, [['h', base.CHANNEL]], 'before edit', int(base.NOW.timestamp()))
        world.events = [original]
        run.buzz_to_feishu()
        edit = base.FGS.sign_event(base.OWNER_KEY, 40003, [['h', base.CHANNEL], ['e', original['id']]], 'after edit', int(base.NOW.timestamp()) + 1)
        world.events.append(edit)
        run.buzz_to_feishu()
        updates = [argv for argv, _ in world.bot_calls if argv[2:4] == ['api', 'PATCH']]
        self.assertEqual(len(updates), 1)
        card = json.loads(json.loads(updates[0][updates[0].index('--data') + 1])['content'])
        self.assertIn('after edit', json.dumps(card, ensure_ascii=False))
        self.assertIn(original['id'], card['elements'][-1]['elements'][0]['content'])

    def test_pending_after_restart_reconstructs_exact_signature_and_first_time(self):
        _, cfg, world, run = self.assembly()
        world.messages = [base.fmsg('om_retry', base.ALICE_OPEN, 'same source')]
        world.relay_write_fail = ['network']
        run.feishu_to_buzz(only_threads=set())
        first = next(ev for ev in world.relay_write_requests if ev['kind'] == 9)
        run.persist()
        adapter = StateAdapter(run.mapping_store, 'test', self.tmp / 'state')
        state = adapter.load()
        holder = {}
        from datetime import timedelta
        retry_now = base.NOW + timedelta(seconds=31); world.clock = retry_now
        rebuilt = dm.MappedHostdRound(cfg, run.clients, state, base.FGS._new_report(), retry_now,
                    lambda: adapter.save(holder['run'].state), store=run.mapping_store, binding_id='test',
                    reader_namespace=base.AGENT_APP, auth_clock=lambda: retry_now)
        holder['run'] = rebuilt
        rebuilt.verify_identities(); rebuilt.load_people(); rebuilt.load_directory(); rebuilt.verify_desk()
        rebuilt.feishu_to_buzz(only_threads=set())
        posted = [ev for ev in world.relay_write_requests if ev['kind'] == 9]
        self.assertEqual(posted, [first, first])
        self.assertEqual(rebuilt.state.f2b['om_retry'], first['id'])

    def test_cross_machine_card_root_read_restores_both_directions_and_reaction_watch(self):
        _, _, world, run = self.assembly()
        ev = base.FGS.sign_event(base.OWNER_KEY, 9, [['h', base.CHANNEL]], 'root', int(base.NOW.timestamp()))
        msg = bot_message(ev, mid='om_oldbot')
        msg['create_time'] = str((run.now_ts - 10000) * 1000)
        world.events = [ev]
        world.messages = [msg]
        mapping = run.resolve_feishu('om_oldbot')
        self.assertEqual(mapping.event_id, ev['id'])
        self.assertEqual(run.state.b2f[ev['id']], 'om_oldbot')
        self.assertEqual(run.state.f2b['om_oldbot'], ev['id'])
        self.assertTrue(run.state.rwatch['om_oldbot'].startswith(ev['id']))
        self.assertEqual(world.relay_writes, [])

    def test_recovery_refuses_conflicting_local_ack(self):
        _, _, world, run = self.assembly()
        ev = base.FGS.sign_event(base.OWNER_KEY, 9, [['h', base.CHANNEL]], 'root', int(base.NOW.timestamp()))
        world.events = [ev]
        world.messages = [bot_message(ev, mid='om_target')]
        run.state.b2f[ev['id']] = 'om_already'
        with self.assertRaises(base.FGS.GroupSyncError):
            run.resolve_feishu('om_target')
        self.assertEqual(run.state.b2f[ev['id']], 'om_already')

    def test_real_image_upload_signed_hash_and_imeta_share_actual_kind9_mapping(self):
        _, _, world, run = self.assembly()
        file = self.tmp / 'image.png'
        file.write_bytes(b'\x89PNG\r\n\x1a\n' + b'image fixture')
        inbound = base.FGS.Inbound('om_image', base.ALICE_PK, 'picture', (), ('img_key',))
        tags = run._feishu_tags({'message_id': 'om_image'}, None, inbound, None, [str(file)])
        self.assertTrue(any(tag[:1] == ['imeta'] for tag in tags))
        all_tags = [['h', base.CHANNEL], *tags]
        event_id = run._mirror_publish(9, all_tags, inbound.text, created_at=run.now_ts)
        accepted = next(ev for ev in world.events if ev['id'] == event_id)
        self.assertIn(['feishu', 'om_image'], accepted['tags'])
        self.assertEqual(len(world.uploads), 1)
        self.assertEqual(len([ev for ev in world.events if ev['kind'] == 9]), 1)

    def test_full_bot_image_read_strip_upload_and_mapped_message_pipeline(self):
        _, _, world, run = self.assembly()
        world.resources[('om_picture', 'img_v3_test')] = base.PNG_META
        world.messages = [base.fmsg('om_picture', base.ALICE_OPEN, '[Image: img_v3_test]', msg_type='image')]
        run.feishu_to_buzz(only_threads=set())
        message = next(ev for ev in world.relay_writes if ev['kind'] == 9)
        self.assertIn(['feishu', 'om_picture'], message['tags'])
        self.assertEqual(world.uploads[0][2], base.PNG_PLAIN)
        self.assertEqual(run.report['images_to_buzz'], 1)
        self.assertEqual(run.report['to_buzz'], 1)

    def test_upload_failure_holds_pending_message_and_retry_preserves_attachment(self):
        _, _, world, run = self.assembly()
        world.resources[('om_picture', 'img_v3_test')] = base.PNG_META
        world.messages = [base.fmsg('om_picture', base.ALICE_OPEN, '[Image: img_v3_test]', msg_type='image')]
        def unavailable(*args, **kwargs):
            raise OSError('PRIVATE_BODY_CANARY')
        run.media_http = unavailable
        run.feishu_to_buzz(only_threads=set())
        self.assertGreater(run.report['errors'], 0)
        self.assertNotIn('PRIVATE_BODY_CANARY', str(run.report))
        self.assertTrue(base.FGS._is_pending(run.state.f2b['om_picture']))
        self.assertFalse(world.relay_writes)
        run.media_http = world.media_upload
        from datetime import timedelta
        run.now += timedelta(seconds=31); world.clock = run.now; run.auth_clock = lambda: run.now
        run.feishu_to_buzz(only_threads=set())
        message = next(ev for ev in world.relay_writes if ev['kind'] == 9)
        self.assertTrue(any(tag[:1] == ['imeta'] for tag in message['tags']))
        self.assertEqual(run.report['images_to_buzz'], 1)

    def test_history_lookup_starts_near_source_event_not_entire_group_history(self):
        _, _, world, run = self.assembly()
        ev = base.FGS.sign_event(base.OWNER_KEY, 9, [['h', base.CHANNEL]], 'new message', run.now_ts)
        run.resolve_buzz(ev)
        argv = next(argv for argv, _ in world.bot_calls if argv[2:4] == ['im', '+chat-messages-list'])
        start = base.datetime.fromisoformat(argv[argv.index('--start') + 1])
        self.assertEqual(int(start.timestamp()), run.now_ts - 900)

    def test_mapping_card_retains_verified_mention_tokens(self):
        _, _, world, run = self.assembly()
        ev = base.FGS.sign_event(base.OWNER_KEY, 9, [['h', base.CHANNEL], ['p', base.AGENT_PK]], 'hello helper', run.now_ts)
        world.events = [ev]
        run.buzz_to_feishu()
        argv = next(argv for argv, _ in world.bot_calls if '--content' in argv)
        card = argv[argv.index('--content') + 1]
        self.assertIn(base.AGENT_BOT_MEMBER, card)
        self.assertIn('<at ', card)

    def test_mapping_path_holds_proxy_agent_message_until_own_outlet(self):
        _, _, world, run = self.assembly()
        ev = base.FGS.sign_event(base.AGENT2_KEY, 9, [['h', base.CHANNEL]], 'own agent content', run.now_ts)
        out = base.FGS.Outbound(ev['id'], None, 'relayed agent', None, relayed=True)
        with self.assertRaises(base.FGS.GroupSyncError):
            run._prepare_outbound(ev, out)
        self.assertFalse(any('--content' in argv for argv, _ in world.bot_calls))

    def test_reaction_recovers_old_target_before_using_actual_bot_reaction_api(self):
        _, _, world, run = self.assembly(reaction_sync='two_way')
        original = base.FGS.sign_event(base.OWNER_KEY, 9, [['h', base.CHANNEL]], 'old root', run.now_ts - 1000)
        reaction = base.FGS.sign_event(base.OWNER_KEY, 7, [['h', base.CHANNEL], ['e', original['id']]], '+', run.now_ts)
        world.events = [original, reaction]
        world.messages = [bot_message(original, mid='om_oldreaction')]
        run.buzz_reactions_to_feishu()
        self.assertEqual(run.state.b2f.get(original['id']), 'om_oldreaction')
        self.assertIn('om_oldreaction', run.state.rwatch)
        self.assertEqual(run.report['reactions_added'], 1)
        self.assertTrue(any(argv[2:5] == ['im', 'reactions', 'create'] and
                            json.loads(argv[argv.index('--params') + 1])['message_id'] == 'om_oldreaction'
                            for argv, _ in world.bot_calls))
        self.assertEqual(world.relay_writes, [])

    def test_edit_recovers_old_target_and_updates_same_card_without_resending(self):
        _, _, world, run = self.assembly()
        original = base.FGS.sign_event(base.OWNER_KEY, 9, [['h', base.CHANNEL]], 'old root', run.now_ts - 1000)
        edit = base.FGS.sign_event(base.OWNER_KEY, 40003, [['h', base.CHANNEL], ['e', original['id']]], 'updated old root', run.now_ts)
        world.events = [original, edit]
        world.messages = [bot_message(original, mid='om_oldedit')]
        run.state.buzz_since = run.now_ts
        run.buzz_to_feishu()
        patches = [argv for argv, _ in world.bot_calls if argv[2:4] == ['api', 'PATCH']]
        self.assertEqual(len(patches), 1)
        self.assertIn('om_oldedit', ' '.join(patches[0]))
        self.assertIn('updated old root', ' '.join(patches[0]))
        self.assertIn(original['id'], ' '.join(patches[0]))
        self.assertFalse(any(argv[2:4] == ['im', '+message-send'] for argv, _ in world.bot_calls))


if __name__ == '__main__':
    unittest.main()
