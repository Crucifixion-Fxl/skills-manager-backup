"""Tests-first contract for own-agent remote edit/image actions.

This private draft reuses the signed-reader, Manager, proof, mapping, Store,
HttpPool, and native read fixtures from test_hostd_remote_reactions. It is not
collected or executed until root reviews and freezes it into the test tree.
Only HTTPSConnection's native boundary is extended for a real PATCH/readback.
"""
import copy
import asyncio
import json
import threading
import sys
from pathlib import Path
from unittest import mock

TESTS = Path(__file__).resolve().parent
sys.path[:0] = [str(TESTS), str(TESTS.parent / 'scripts')]
import test_hostd_remote_reactions as reactions


class EditWorld(reactions.World):
    def __init__(self, case):
        super().__init__(case)
        self.patch_calls = []
        self.after_patch = None
        self.lose_patch_response = False
        self.incomplete_message_view = False
        self.block_patch = False
        self.patch_entered = threading.Event()
        self.patch_release = threading.Event()
        self.duplicate_edit_response = 0

    def http(self, url, headers, timeout, *, body=None):
        response = super().http(url, headers, timeout, body=body)
        filters = json.loads(body) if body else []
        if self.duplicate_edit_response and any(
                row.get('kinds') == [40003] and '#e' in row for row in filters):
            status, payload = response
            rows = json.loads(payload)
            if rows:
                return status, json.dumps([rows[0]] * self.duplicate_edit_response).encode()
        return response

    def native(self, method, raw_path, body):
        from urllib.parse import urlsplit

        path = urlsplit(raw_path).path
        if method == "POST" and path == "/open-apis/im/v1/messages":
            data = json.loads(body)
            self.assertEqual((data['msg_type'], data['receive_id']), ('interactive', reactions.CHAT))
            intent = self.on_loop(lambda: self.db.remote_delivery_by_source(
                self.target_id, self.current_event['id'], 'message'))
            self.assertEqual(intent.status, 'unknown')
            self.api_calls.append((method, path, {}, copy.deepcopy(data)))
            self.messages['om_root'] = {
                'message_id': 'om_root', 'chat_id': reactions.CHAT,
                'root_id': 'om_root', 'thread_id': 'omt_om_root',
                'create_time': str(self.now * 1000), 'msg_type': 'interactive',
                'sender': {'sender_type': 'app', 'id_type': 'app_id', 'id': reactions.APP},
                'body': {'content': data['content']},
            }
            return reactions.Response(200, {'code': 0, 'data': {'message_id': 'om_root'}})
        if method == "PATCH" and path.startswith("/open-apis/im/v1/messages/"):
            message_id = path.rsplit("/", 1)[1]
            self.assertEqual(message_id, "om_root")
            data = json.loads(body)
            self.assertEqual(set(data), {"content"})
            self.assertTrue(isinstance(data["content"], str))
            observed = self.on_loop(lambda: (
                self.db.remote_delivery_by_source(
                    self.target_id, self.current_event["id"], "edit"),
                self.db.conn.in_transaction,
            ))
            row, in_transaction = observed
            self.assertIsNotNone(row, "durable edit intent must exist before PATCH")
            self.assertEqual(row.status, "unknown")
            self.assertFalse(in_transaction)
            self.patch_calls.append((message_id, copy.deepcopy(data)))
            self.messages[message_id]["body"]["content"] = data["content"]
            self.patch_entered.set()
            if self.block_patch:
                self.assertTrue(self.patch_release.wait(10))
            if self.after_patch:
                self.after_patch()
            if self.lose_patch_response:
                return OSError("SYNTHETIC_PRIVATE_ERROR")
            return reactions.Response(200, {"code": 0, "data": {"message_id": message_id}})
        if method == "GET" and path == "/open-apis/im/v1/messages/om_root" and self.incomplete_message_view:
            return reactions.Response(200, {"code": 0, "data": {"items": []}})
        return super().native(method, raw_path, body)


class Actions(reactions.Base):
    async def asyncSetUp(self):
        self._world_patch = mock.patch.object(reactions, "World", EditWorld)
        self._world_patch.start()
        self.addCleanup(self._world_patch.stop)
        await super().asyncSetUp()
        self.addCleanup(self.w.patch_release.set)

    async def edit_grant(self):
        return await self.grant(("message", "edit"))

    def edit(self, *, text="revised card", key=reactions.signed_fixture.KEY,
             tags=None):
        return self.w.event(kind=40003, key=key,
            tags=tags if tags is not None else [["e", self.root["id"]]], text=text)

    async def test_signed_edit_updates_original_mapped_card_and_never_creates_mapping(self):
        grant = await self.edit_grant()
        event = self.edit()
        original = copy.deepcopy(self.w.messages["om_root"])
        result = await self.deliver(event, grant)
        self.assertEqual(result.status, "acked")
        self.assertEqual(len(self.w.patch_calls), 1)
        self.assertEqual(self.w.patch_calls[0][0], "om_root")
        self.assertEqual(self.w.messages["om_root"]["message_id"], original["message_id"])
        self.assertEqual(self.w.messages["om_root"]["root_id"], original["root_id"])
        self.assertEqual(len([call for call in self.w.api_calls
            if call[0] == "POST" and call[1] == "/open-apis/im/v1/messages"]), 0)
        self.assertEqual(self.row(event, "edit").status, "acked")
        self.assertEqual(self.row(event, "edit").message_id, "om_root")
        self.assertEqual(self.w.messages["om_root"]["sender"]["id"], reactions.APP)

    async def test_invalid_signed_edit_source_author_channel_or_target_has_no_intent_or_patch(self):
        grant = await self.edit_grant()
        cases = []
        bad_signature = self.edit()
        bad_signature["sig"] = "0" * 128
        cases.append(("signature", bad_signature))
        cases.append(("foreign-author", self.edit(key=reactions.signed_fixture.HUMAN_KEY)))
        cases.append(("wrong-channel", self.edit(tags=[["e", self.root["id"]], ["h", "f" * 64]])))
        cases.append(("wrong-target", self.edit(tags=[["e", "f" * 64]])))
        for label, event in cases:
            with self.subTest(label=label):
                result = await self.deliver(event, grant)
                self.assertEqual(result.status, "pending")
                self.assertFalse(self.w.patch_calls)
                self.assertIsNone(self.row(event, "edit"))

    async def test_lost_patch_response_recovers_by_exact_get_without_a_second_patch(self):
        grant = await self.edit_grant()
        event = self.edit()
        self.w.lose_patch_response = True
        first = await self.deliver(event, grant)
        self.assertEqual(first.status, "pending")
        row = self.row(event, "edit")
        self.assertEqual(row.status, "unknown")
        self.reopen()
        self.w.lose_patch_response = False
        second = await self.deliver(event, grant)
        self.assertEqual(second.status, "acked")
        self.assertEqual(len(self.w.patch_calls), 1)
        self.assertEqual(self.row(event, "edit").status, "acked")

    async def test_changed_original_body_or_grant_after_patch_stays_unknown(self):
        grant = await self.edit_grant()
        event = self.edit()

        def alter_original_after_physical_patch():
            self.w.messages["om_root"]["root_id"] = "om_foreign_root"

        self.w.after_patch = alter_original_after_physical_patch
        result = await self.deliver(event, grant)
        self.assertEqual(result.status, "pending")
        self.assertEqual(self.row(event, "edit").status, "unknown")
        self.assertEqual(len(self.w.patch_calls), 1)

    async def test_body_drift_after_patch_stays_unknown(self):
        grant = await self.edit_grant()
        event = self.edit()

        def change_body_after_patch():
            self.w.messages["om_root"]["body"]["content"] = "{}"

        self.w.after_patch = change_body_after_patch
        result = await self.deliver(event, grant)
        self.assertEqual(result.status, "pending")
        self.assertEqual(self.row(event, "edit").status, "unknown")
        self.assertEqual(len(self.w.patch_calls), 1)

    async def test_grant_suspension_after_patch_stays_unknown(self):
        grant = await self.edit_grant()
        event = self.edit()

        def suspend_after_patch():
            self.w.on_loop(lambda: self.db.suspend_remote_grant(
                grant.target_id, expected_revision=grant.revision, now=self.w.now))

        self.w.after_patch = suspend_after_patch
        result = await self.deliver(event, grant)
        self.assertEqual(result.status, "pending")
        self.assertEqual(self.row(event, "edit").status, "unknown")
        self.assertEqual(len(self.w.patch_calls), 1)

    async def test_incomplete_exact_get_after_patch_stays_unknown(self):
        grant = await self.edit_grant()
        event = self.edit()
        self.w.after_patch = lambda: setattr(self.w, "incomplete_message_view", True)
        result = await self.deliver(event, grant)
        self.assertEqual(result.status, "pending")
        self.assertEqual(self.row(event, "edit").status, "unknown")
        self.assertEqual(len(self.w.patch_calls), 1)

    async def test_foreign_or_changed_original_mapping_has_no_intent_or_patch(self):
        grant = await self.edit_grant()
        event = self.edit()
        self.w.messages["om_root"]["sender"]["id"] = "cli_foreign"
        result = await self.deliver(event, grant)
        self.assertEqual(result.status, "pending")
        self.assertIsNone(self.row(event, "edit"))
        self.assertFalse(self.w.patch_calls)

    async def test_cancellation_joins_patch_and_keeps_unknown_without_retry(self):
        grant = await self.edit_grant()
        event = self.edit()
        self.w.block_patch = True
        task = asyncio.create_task(self.deliver(event, grant))
        try:
            self.assertTrue(await asyncio.to_thread(self.w.patch_entered.wait, 240),
                "actual signed/native gates must finish and reach PATCH")
            task.cancel()
            await asyncio.sleep(0)
            self.assertFalse(task.done(), "cancellation must reap the in-flight native PATCH")
            self.w.patch_release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        finally:
            self.w.patch_release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(self.row(event, "edit").status, "unknown")
        self.assertEqual(len(self.w.patch_calls), 1)

    async def test_authenticated_imeta_remains_pending_without_partial_text_or_native_write(self):
        grant = await self.grant(("message",))
        image = self.w.event(kind=9, text="IMAGE_BODY_CANARY", tags=[[
            "imeta", "m image/png", "x " + "a" * 64,
            "url https://relay.example/media/" + "a" * 64 + ".png",
        ]])
        result = await self.deliver(image, grant)
        self.assertEqual(result.status, "pending")
        self.assertIsNone(self.row(image, "message"))
        self.assertFalse(self.w.patch_calls)
        self.assertFalse([call for call in self.w.api_calls if call[0] == "POST"])
        self.assertNotIn("IMAGE_BODY_CANARY", json.dumps(result.readback()))

    async def test_message_only_grant_never_gains_edit_by_ambient_scope(self):
        grant = await self.grant(("message",))
        event = self.edit()
        self.pending(await self.deliver(event, grant))
        self.assertIsNone(self.row(event, "edit"))
        self.assertFalse(self.w.patch_calls)
        self.assertEqual(self.db.remote_grant(grant.target_id), grant)

    async def test_official_patch_scope_alternatives_are_proved_independently(self):
        for scope in ("im:message:update", "im:message", reactions.SEND):
            self.w.scopes = set(reactions.READ_SCOPE_GROUPS) | {scope}
            proof = await self.request(("edit",))
            self.assertEqual(proof.status, "verified")
            self.assertEqual(proof.authorization.evidence.capabilities, ("edit",))
        self.w.scopes = set(reactions.READ_SCOPE_GROUPS) | {"im:message:send"}
        self.pending(await self.request(("edit",)))

    async def test_complete_latest_overlay_ignores_unreserved_older_without_fake_ack(self):
        grant = await self.edit_grant()
        older, latest = sorted([self.edit(text="first revision"), self.edit(text="second revision")],
            key=lambda event: (event["created_at"], event["id"]))
        stale = await self.deliver(older, grant)
        self.assertEqual((stale.status, stale.reason), ("ignored", "superseded"))
        self.assertIsNone(self.row(older, "edit"))
        self.assertFalse(self.w.patch_calls)
        self.assertEqual((await self.deliver(latest, grant)).status, "acked")
        self.assertEqual(len(self.w.patch_calls), 1)
        expected = reactions.message_card(self.root, self.w.actual_record.name,
            latest["content"], reactions.ORIGIN, reactions.CHANNEL)
        self.assertEqual(json.loads(self.w.messages["om_root"]["body"]["content"]), json.loads(expected))

    async def test_older_unknown_never_repatches_and_newest_can_progress_without_cursor_ack(self):
        grant = await self.edit_grant()
        older = self.edit(text="uncertain old revision")
        self.w.lose_patch_response = True
        self.pending(await self.deliver(older, grant))
        old_row = self.row(older, "edit")
        self.assertEqual(old_row.status, "unknown")
        latest = self.w.event(kind=40003, tags=[["e", self.root["id"]]],
            text="certain new revision", at=reactions.NOW + 1)
        self.w.now += 2
        self.w.lose_patch_response = False
        self.pending(await self.deliver(older, grant))
        self.assertEqual(self.row(older, "edit"), old_row)
        self.assertEqual((await self.deliver(latest, grant)).status, "acked")
        self.assertEqual(len(self.w.patch_calls), 2)
        self.assertEqual(self.row(older, "edit"), old_row)
        self.assertFalse(self.db.commit_remote_cursor(grant.target_id, self.w.now,
            revision=grant.revision, scope_hash=grant.scope_hash, complete=True, now=self.w.now))

    async def test_new_overlay_after_physical_patch_prevents_stale_ack(self):
        grant = await self.edit_grant()
        event = self.edit(text="was latest before PATCH")
        latest = self.w.event(kind=40003, tags=[["e", self.root["id"]]],
            text="arrived during PATCH", at=reactions.NOW)
        # Same-second tie must deterministically put the arrival last. Choose
        # from already signed events; never mutate a signed timestamp or id.
        event, latest = sorted((event, latest), key=lambda row: (row["created_at"], row["id"]))
        self.w.events.remove(latest)
        self.w.after_patch = lambda: self.w.events.append(latest)
        self.pending(await self.deliver(event, grant))
        self.assertEqual(self.row(event, "edit").status, "unknown")
        self.assertEqual(len(self.w.patch_calls), 1)

    async def test_reaction_after_canonical_edit_uses_same_original_mapping(self):
        grant = await self.grant(("message", "edit", "reaction_add", "reaction_remove"))
        event = self.edit()
        self.assertEqual((await self.deliver(event, grant)).status, "acked")
        reaction = self.w.reaction(self.root)
        self.assertEqual((await self.deliver(reaction, grant)).status, "acked")
        self.assertEqual(self.row(reaction, "reaction_add").message_id, "om_root")
        self.assertEqual(len(self.w.patch_calls), 1)
        self.assertEqual(len(self.w.mutations), 1)

    async def test_duplicate_or_saturated_signed_overlay_history_cannot_authorize_patch(self):
        grant = await self.edit_grant()
        event = self.edit()
        for count in (2, 257):
            with self.subTest(returned=count):
                self.w.duplicate_edit_response = count
                self.pending(await self.deliver(event, grant))
                self.assertIsNone(self.row(event, "edit"))
                self.assertFalse(self.w.patch_calls)

    async def test_original_message_redrain_after_edit_validates_overlay_without_duplicate_post(self):
        grant = await self.edit_grant()
        self.w.messages.pop('om_root')
        self.assertEqual((await self.deliver(self.root, grant)).status, 'acked')
        original_receipt = self.row(self.root, 'message')
        event = self.edit()
        self.assertEqual((await self.deliver(event, grant)).status, 'acked')
        self.assertEqual((await self.deliver(self.root, grant)).status, 'acked')
        self.assertEqual(self.row(self.root, 'message'), original_receipt)
        self.assertEqual(len([call for call in self.w.api_calls
            if call[0] == 'POST' and call[1] == '/open-apis/im/v1/messages']), 1)
