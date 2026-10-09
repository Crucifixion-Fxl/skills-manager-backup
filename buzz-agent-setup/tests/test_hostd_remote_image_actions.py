"""Private tests-first image-preserving own-agent actions; not collected yet."""
import asyncio
import copy
import hashlib
import json
from pathlib import Path
import sys
import threading
import unittest
from urllib.parse import parse_qs, unquote, urlsplit

TESTS = Path(__file__).resolve().parent
sys.path[:0] = [str(TESTS), str(TESTS.parent / 'scripts')]
import test_hostd_remote_images as images_fixture
from hostd.async_io import thread_call
from hostd import store
from hostd.remote_runtime import RemoteRuntime
import buzz_feishu_group_sync as gs

R = images_fixture.reactions_fixture
APP, CHAT, CHANNEL, NOW = images_fixture.APP, images_fixture.CHAT, images_fixture.CHANNEL, images_fixture.NOW
CAPS = ('message', 'edit', 'reaction_add', 'reaction_remove')


class ImageActionWorld(images_fixture.ImageWorld):
    """Synthetic native HTTPS/subprocess endpoints; real wrappers and authority."""
    def __init__(self, case):
        super().__init__(case)
        self.scopes.add('im:message:update')
        self.original_image_event = None
        self.on_binary = None
        self.patch_calls = []
        self.after_patch = None
        self.lose_patch = False
        self.block_patch = False
        self.patch_entered = threading.Event()
        self.patch_release = threading.Event()
        case.addCleanup(self.patch_release.set)

    def bot_media_runner(self, argv, **kwargs):
        is_get = argv[2:4] == ['api', 'GET'] and '/open-apis/im/v1/images/' in argv[4]
        if not is_get:
            return super().bot_media_runner(argv, **kwargs)
        # Original IMAGE19 lower runner indexes image ledger via current_event.
        # An edit/reaction's binary GET belongs to original source, not action ID.
        # This only corrects low endpoint ledger observations; production signed
        # queries/authority never read this fixture-only current_event attribute.
        current = self.current_event
        self.current_event = self.original_image_event
        try:
            result = super().bot_media_runner(argv, **kwargs)
        finally:
            self.current_event = current
        if self.on_binary is not None:
            action, self.on_binary = self.on_binary, None
            self.on_loop(action)
        return result

    def native(self, method, raw_path, body):
        path = urlsplit(raw_path).path
        if method == 'PATCH' and path.startswith('/open-apis/im/v1/messages/'):
            mid = path.rsplit('/', 1)[1]
            self.assertEqual(mid, 'om_image_1')
            data = json.loads(body)
            self.assertEqual(set(data), {'content'})
            row, in_tx = self.on_loop(lambda: (self.db.remote_delivery_by_source(
                self.target_id, self.current_event['id'], 'edit'), self.db.conn.in_transaction))
            self.assertIsNotNone(row); self.assertEqual((row.status, in_tx), ('unknown', False))
            self.patch_calls.append((mid, copy.deepcopy(data)))
            self.messages[mid]['body']['content'] = data['content']
            self.patch_entered.set()
            if self.block_patch:
                self.assertTrue(self.patch_release.wait(3))
            if self.after_patch:
                self.after_patch()
            return (OSError('SYNTHETIC_LOST_PATCH') if self.lose_patch else
                    R.Response(200, {'code': 0, 'data': {'message_id': mid}}))
        if path.endswith('/reactions') or '/reactions/' in path:
            self.assertEqual(unquote(path.split('/')[5]), 'om_image_1')
            if method == 'GET':
                return R.Response(200, {'code': 0, 'data': {'items': copy.deepcopy(self.reactions), 'has_more': False}})
            action = 'reaction_add' if method == 'POST' else 'reaction_remove'
            row, in_tx = self.on_loop(lambda: (self.db.remote_delivery_by_source(
                self.target_id, self.current_event['id'], action), self.db.conn.in_transaction))
            self.assertIsNotNone(row); self.assertEqual((row.status, in_tx), ('unknown', False))
            data = json.loads(body) if body else None
            self.mutations.append((method, path, copy.deepcopy(data)))
            if method == 'POST':
                self.assertEqual(data, {'reaction_type': {'emoji_type': R.EMOJI}})
                self.reactions = [self.reaction_row(self.reaction_id)]
                result = self.reactions[0]
            else:
                self.assertEqual(method, 'DELETE'); self.assertIsNone(data)
                self.assertEqual(unquote(path.rsplit('/', 1)[1]), self.reaction_id)
                self.reactions = []
                result = self.reaction_row(self.reaction_id)
            if self.after_mutation:
                self.after_mutation()
            return R.Response(200, {'code': 0, 'data': result})
        result = super().native(method, raw_path, body)
        if method == 'POST' and path == '/open-apis/im/v1/messages':
            # Native PATCH window evidence, supplied at the real HTTP endpoint.
            self.messages['om_image_1']['create_time'] = str(self.now * 1000)
        return result


class ImageActionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = ImageActionWorld(self); self.w.loop = asyncio.get_running_loop()
        self.path = self.w.root / 'metadata' / 'image-actions.db'
        self.db = store.Store(self.path); self.w.db = self.db
        self.addCleanup(lambda: self.db.close())
        self.db.register_agent(R.PUB, owner_pubkey=R.OWNER, app_id=APP,
            config_path=str(self.w.env), now=NOW)
        self.w.target_id = store.Store._remote_digest([R.PUB, CHANNEL])
        self.consumer = self.w.consumer()
        self.keys = None

    async def grant(self, caps=CAPS):
        result = await self.consumer.verify(self.w.target, capabilities=caps)
        self.assertEqual(result.status, 'verified')
        self.assertEqual(result.authorization.evidence.capabilities, caps)
        grant = self.db.activate_remote_grant(result.authorization.evidence, expected_revision=0, now=self.w.now)
        self.assertTrue(self.db.refresh_remote_proof(grant.target_id, result.authorization.evidence,
            revision=grant.revision, scope_hash=grant.scope_hash, now=self.w.now))
        return grant

    def runtime(self):
        return RemoteRuntime(self.db, record=self.w.actual_record, reader=self.w.reader,
            bot=self.w.bot, proofs=self.consumer, link_base=images_fixture.ORIGIN, clock=lambda: self.w.now)

    async def deliver(self, event, grant):
        self.w.current_event = event
        return await self.runtime().deliver(self.w.target, event, target_id=grant.target_id,
            revision=grant.revision, scope_hash=grant.scope_hash)

    async def original(self, *, caps=CAPS, unknown=False):
        self.g = await self.grant(caps)
        self.images = [self.w.image_bytes('png', b'A'), self.w.image_bytes('jpeg', b'B')]
        self.w.last_images = self.images
        self.source = self.w.signed_image_event(self.images)
        self.w.original_image_event = self.source
        self.w.lose_message_response = unknown
        result = await self.deliver(self.source, self.g)
        self.assertEqual(result.status, 'pending' if unknown else 'acked',
                         'actual original IMAGE delivery must reach required receipt state')
        self.w.lose_message_response = False
        self.assertEqual(self.row(self.source, 'message').status, 'unknown' if unknown else 'acked')
        rows = self.image_rows()
        self.assertTrue(all(r.state == 'acked' for r in rows))
        self.keys = tuple(r.image_key for r in rows)
        self.image_pins = tuple(rows)
        self.original_card = json.loads(self.w.messages['om_image_1']['body']['content'])
        self.assertEqual(self.original_card['elements'][-1], json.loads(R.message_card(
            self.source, self.w.actual_record.name, self.source['content'], images_fixture.ORIGIN, CHANNEL))['elements'][-1])
        return self.g

    def image_rows(self):
        return tuple(self.db.remote_image_upload_by_source(self.w.target_id, self.source['id'], i) for i in range(2))
    def row(self, event, action):
        return self.db.remote_delivery_by_source(self.w.target_id, event['id'], action)
    def edit(self, text='changed text', *, at=None, imeta=False):
        tags = [['e', self.source['id']]]
        if imeta:
            i = self.images[0]
            tags += [['imeta', 'url ' + i['url'], 'm ' + i['mime'], 'x ' + i['sha256'], 'size ' + str(i['size'])]]
        return self.w.event(kind=40003, tags=tags, text=text, at=self.w.now if at is None else at)
    def reopen(self):
        self.db.close(); self.db = store.Store(self.path); self.w.db = self.db
    def pending(self, result):
        self.assertEqual(result.status, 'pending')
        self.assertIn('怎么解决', result.notice)
        raw = json.dumps(result.readback(), ensure_ascii=False)
        for value in (images_fixture.BODY_CANARY, R.signed_fixture.KEY, 'offline-reaction-token', 'img_own_'):
            self.assertNotIn(value, raw)
    def unchanged_images(self):
        self.assertEqual(self.image_rows(), self.image_pins)
        self.assertEqual(len(self.w.image_posts), 2)
        self.assertEqual(self.w.card_post_count, 1)
        self.assertEqual(self.db.remote_grant(self.g.target_id).revision, self.g.revision)
        self.assertEqual(self.db.remote_grant(self.g.target_id).evidence.capabilities, self.g.evidence.capabilities)
    def card_preserved(self):
        card = json.loads(self.w.messages['om_image_1']['body']['content'])
        self.assertEqual([e['img_key'] for e in card['elements'] if e.get('tag') == 'img'], list(self.keys))
        self.assertEqual(card['elements'][-1], self.original_card['elements'][-1])
        self.assertEqual(self.w.messages['om_image_1']['sender']['id'], APP)
        self.assertEqual(self.w.messages['om_image_1']['root_id'], 'om_image_1')
        return card
    async def patched(self):
        await self.original(); event = self.edit()
        self.assertEqual((await self.deliver(event, self.g)).status, 'acked',
                         'genuine missing image-preserving text edit')
        return event

    async def test_original_image_ack_control_retains_exact_general_capability_tuple(self):
        await self.original(); self.unchanged_images(); self.card_preserved()
        self.assertEqual(self.g.evidence.capabilities, CAPS)

    async def test_text_only_patch_preserves_all_original_images_footer_and_native_message(self):
        event = await self.patched()
        card = self.card_preserved()
        self.assertIn(event['content'], json.dumps(card, ensure_ascii=False))
        self.assertEqual(len(self.w.patch_calls), 1)
        self.assertEqual(self.row(event, 'edit').message_id, 'om_image_1')
        self.unchanged_images()

    async def test_unknown_edit_reopen_get_only_preserves_original_key_set(self):
        await self.original(); event = self.edit(); self.w.lose_patch = True
        self.pending(await self.deliver(event, self.g))
        actual_edit = self.row(event, 'edit')
        self.assertIsNotNone(actual_edit, 'missing actual image-preserving UNKNOWN edit intent')
        self.assertEqual(actual_edit.status, 'unknown')
        self.assertEqual(len(self.w.patch_calls), 1)
        self.reopen(); self.w.lose_patch = False
        self.assertEqual((await self.deliver(event, self.g)).status, 'acked')
        self.assertEqual(len(self.w.patch_calls), 1)
        self.unchanged_images(); self.card_preserved()

    async def test_original_ack_replay_after_text_edit_never_resends_or_changes_original_hash(self):
        event = await self.patched(); original = self.row(self.source, 'message')
        self.assertEqual((await self.deliver(self.source, self.g)).status, 'acked')
        self.assertEqual(self.row(self.source, 'message').content_hash, original.content_hash)
        self.assertEqual(len(self.w.patch_calls), 1)
        self.unchanged_images(); self.card_preserved()

    async def test_latest_signed_edit_skips_unreserved_older_without_fake_ack(self):
        await self.original(); older = self.edit('older', at=NOW); latest = self.edit('latest', at=NOW+1)
        self.w.now = NOW+2
        self.assertEqual((await self.deliver(latest, self.g)).status, 'acked')
        result = await self.deliver(older, self.g)
        self.assertEqual((result.status, result.reason), ('ignored', 'superseded'))
        self.assertIsNone(self.row(older, 'edit')); self.assertEqual(len(self.w.patch_calls), 1)
        self.unchanged_images(); self.card_preserved()

    async def test_old_unknown_edit_keeps_cursor_pending_after_latest_progress_without_repatch(self):
        await self.original(); older = self.edit('old uncertainty'); self.w.lose_patch = True
        self.pending(await self.deliver(older, self.g)); old = self.row(older, 'edit')
        self.assertIsNotNone(old, 'missing actual image-preserving UNKNOWN edit intent')
        self.assertEqual(old.status, 'unknown'); self.assertEqual(len(self.w.patch_calls), 1)
        self.w.lose_patch = False; latest = self.edit('latest', at=NOW+1); self.w.now = NOW+2
        self.w.current_event = latest
        result = await self.runtime().drain(self.w.target, target_id=self.g.target_id,
            revision=self.g.revision, scope_hash=self.g.scope_hash)
        self.pending(result); latest_edit = self.row(latest, 'edit')
        self.assertIsNotNone(latest_edit, 'missing actual latest image-preserving edit intent')
        self.assertEqual(latest_edit.status, 'acked')
        self.assertEqual(self.row(older, 'edit'), old); self.assertEqual(len(self.w.patch_calls), 2)
        self.assertIsNone(self.db.conn.execute('SELECT position FROM remote_cursor WHERE target_id=?', (self.g.target_id,)).fetchone())
        self.unchanged_images()

    async def test_reaction_add_uses_actual_original_image_body_and_key_gets(self):
        await self.original(); before = len(self.w.image_gets)
        event = self.w.reaction(self.source)
        self.assertEqual((await self.deliver(event, self.g)).status, 'acked', 'genuine missing image reaction')
        self.assertGreaterEqual(len(self.w.image_gets)-before, 2)
        self.assertEqual(self.row(event, 'reaction_add').message_id, 'om_image_1')
        self.assertEqual([r[0] for r in self.w.mutations], ['POST'])
        self.unchanged_images(); self.card_preserved()

    async def test_reaction_delete_requires_exact_original_own_receipt_and_preserves_images(self):
        await self.original(); added = self.w.reaction(self.source)
        self.assertEqual((await self.deliver(added, self.g)).status, 'acked')
        removed = self.w.reaction(added, kind=5)
        self.assertEqual((await self.deliver(removed, self.g)).status, 'acked')
        self.assertEqual([r[0] for r in self.w.mutations], ['POST', 'DELETE'])
        self.assertEqual(self.row(removed, 'reaction_remove').reaction_id, self.w.reaction_id)
        self.unchanged_images(); self.card_preserved()

    async def test_reaction_after_text_edit_requires_latest_pinned_full_image_card(self):
        await self.patched(); event = self.w.reaction(self.source)
        self.assertEqual((await self.deliver(event, self.g)).status, 'acked')
        self.unchanged_images(); self.card_preserved()

    async def test_original_image_hash_mismatch_prevents_new_edit_intent(self):
        await self.original(); event = self.edit(); self.w.image_get_override = lambda key: {'body': b'wrong'}
        before = len(self.w.image_gets); self.pending(await self.deliver(event, self.g))
        self.assertGreater(len(self.w.image_gets), before, 'real image binary GET must be reached')
        self.assertIsNone(self.row(event, 'edit')); self.assertFalse(self.w.patch_calls); self.unchanged_images()

    async def test_original_image_hash_mismatch_prevents_reaction_write(self):
        await self.original(); event = self.w.reaction(self.source); self.w.image_get_override = lambda key: {'body': b'wrong'}
        before = len(self.w.image_gets); self.pending(await self.deliver(event, self.g))
        self.assertGreater(len(self.w.image_gets), before)
        self.assertIsNone(self.row(event, 'reaction_add')); self.assertFalse(self.w.mutations); self.unchanged_images()

    async def test_native_membership_loss_during_original_image_get_prevents_patch(self):
        await self.original(); event = self.edit(); self.w.on_binary = lambda: setattr(self.w, 'bot_absent', True)
        before = len(self.w.image_gets); self.pending(await self.deliver(event, self.g))
        self.assertGreater(len(self.w.image_gets), before); self.assertIsNone(self.row(event, 'edit'))
        self.assertFalse(self.w.patch_calls); self.unchanged_images()

    async def test_sql_revocation_during_original_image_get_prevents_patch(self):
        await self.original(); event = self.edit()
        self.w.on_binary = lambda: self.db.suspend_remote_grant(self.g.target_id, expected_revision=self.g.revision, now=self.w.now)
        before = len(self.w.image_gets); self.pending(await self.deliver(event, self.g))
        self.assertGreater(len(self.w.image_gets), before); self.assertIsNone(self.row(event, 'edit'))
        self.assertFalse(self.w.patch_calls); self.assertEqual(self.image_rows(), self.image_pins)

    async def test_signed_original_disappearance_during_image_get_prevents_patch(self):
        await self.original(); event = self.edit(); self.w.on_binary = lambda: self.w.events.remove(self.source)
        before = len(self.w.image_gets); self.pending(await self.deliver(event, self.g))
        self.assertGreater(len(self.w.image_gets), before); self.assertIsNone(self.row(event, 'edit'))
        self.assertFalse(self.w.patch_calls); self.unchanged_images()

    async def test_native_image_key_drift_after_patch_retains_unknown_edit(self):
        await self.original(); event = self.edit()
        def drift():
            card = json.loads(self.w.messages['om_image_1']['body']['content'])
            next(e for e in card['elements'] if e.get('tag') == 'img')['img_key'] = 'img_foreign'
            self.w.messages['om_image_1']['body']['content'] = json.dumps(card)
        self.w.after_patch = drift
        self.pending(await self.deliver(event, self.g)); self.assertEqual(len(self.w.patch_calls), 1)
        actual_edit = self.row(event, 'edit')
        self.assertIsNotNone(actual_edit, 'missing actual image-preserving UNKNOWN edit intent')
        self.assertEqual(actual_edit.status, 'unknown'); self.unchanged_images()

    async def test_native_root_drift_after_patch_retains_unknown_edit(self):
        await self.original(); event = self.edit()
        self.w.after_patch = lambda: self.w.messages['om_image_1'].update(root_id='om_foreign')
        self.pending(await self.deliver(event, self.g)); self.assertEqual(len(self.w.patch_calls), 1)
        actual_edit = self.row(event, 'edit')
        self.assertIsNotNone(actual_edit, 'missing actual image-preserving UNKNOWN edit intent')
        self.assertEqual(actual_edit.status, 'unknown'); self.unchanged_images()

    async def test_cancel_original_key_get_joins_without_new_edit_or_image_mutation(self):
        await self.original(); event = self.edit(); self.w.block_image_get = True
        task = asyncio.create_task(self.deliver(event, self.g))
        try:
            self.assertTrue(await asyncio.to_thread(self.w.image_get_entered.wait, 240))
            task.cancel(); await asyncio.sleep(0); self.assertFalse(task.done())
            task.cancel(); self.w.image_get_release.set()
            with self.assertRaises(asyncio.CancelledError): await task
        finally:
            self.w.image_get_release.set()
            if not task.done(): task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.assertIsNone(self.row(event, 'edit')); self.assertFalse(self.w.patch_calls); self.unchanged_images()

    async def test_cancel_patch_retains_unknown_then_exact_get_only_recovery(self):
        await self.original(); event = self.edit(); self.w.block_patch = True
        task = asyncio.create_task(self.deliver(event, self.g))
        try:
            self.assertTrue(await asyncio.to_thread(self.w.patch_entered.wait, 240))
            task.cancel(); await asyncio.sleep(0); self.assertFalse(task.done())
            task.cancel(); self.w.patch_release.set()
            with self.assertRaises(asyncio.CancelledError): await task
        finally:
            self.w.patch_release.set()
            if not task.done(): task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        actual_edit = self.row(event, 'edit')
        self.assertIsNotNone(actual_edit, 'missing actual image-preserving UNKNOWN edit intent')
        self.assertEqual(actual_edit.status, 'unknown'); self.assertEqual(len(self.w.patch_calls), 1)
        self.reopen(); self.w.block_patch = False
        self.assertEqual((await self.deliver(event, self.g)).status, 'acked')
        self.assertEqual(len(self.w.patch_calls), 1); self.unchanged_images(); self.card_preserved()

    async def test_unsupported_latest_imeta_overlay_blocks_older_text_without_partial_patch(self):
        await self.original(); older = self.edit('older', at=NOW); self.edit('image-changing', at=NOW+1, imeta=True)
        self.w.now = NOW+2; self.pending(await self.deliver(older, self.g))
        self.assertIsNone(self.row(older, 'edit')); self.assertFalse(self.w.patch_calls); self.unchanged_images()

    async def test_original_unknown_overwritten_by_signed_edit_remains_pending_without_resend(self):
        await self.original(unknown=True); self.edit('later text')
        # Simulate later native body change only at the lower fake endpoint data.
        card = copy.deepcopy(self.original_card)
        card['elements'][0] = {'tag': 'div', 'text': {'tag': 'lark_md', 'content': 'later text'}}
        self.w.messages['om_image_1']['body']['content'] = json.dumps(card)
        self.pending(await self.deliver(self.source, self.g))
        self.assertEqual(self.row(self.source, 'message').status, 'unknown')
        self.unchanged_images(); self.assertFalse(self.w.patch_calls)

    async def test_missing_legacy_image_journal_does_not_adopt_native_card_keys(self):
        await self.original()
        self.db.close(); self.path = self.w.root / 'metadata' / 'fresh-no-image-ledger.db'
        self.db = store.Store(self.path); self.w.db = self.db
        self.db.register_agent(R.PUB, owner_pubkey=R.OWNER, app_id=APP, config_path=str(self.w.env), now=NOW)
        self.g = await self.grant(); event = self.edit()
        self.pending(await self.deliver(event, self.g))
        self.assertTrue(all(r is None for r in self.image_rows()))
        self.assertIsNone(self.row(event, 'edit')); self.assertFalse(self.w.patch_calls)
        self.assertEqual(len(self.w.image_posts), 2); self.assertEqual(self.w.card_post_count, 1)

    async def test_message_only_original_grant_never_gains_edit_from_ambient_scope(self):
        await self.original(caps=('message',)); event = self.edit()
        self.pending(await self.deliver(event, self.g))
        self.assertIsNone(self.row(event, 'edit')); self.assertFalse(self.w.patch_calls)
        self.assertEqual(self.g.evidence.capabilities, ('message',)); self.unchanged_images()


if __name__ == '__main__':
    unittest.main()
