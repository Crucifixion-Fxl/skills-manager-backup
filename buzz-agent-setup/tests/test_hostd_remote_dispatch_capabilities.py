"""Tests-first daemon capability wiring proposal; product ownership is pending root review.

This suite reuses only the real dispatch harness setup and lower HTTP boundary.
RemoteDispatch, Manager, Proofs, Runtime, signed reads, mapping, and Store remain
real. No producer, proof DTO, dispatcher factory, or Store method is mocked.
"""
import asyncio
import sys
import unittest
from pathlib import Path
from urllib.parse import unquote
from unittest import mock

TESTS = Path(__file__).resolve().parent
SCRIPTS = TESTS.parent / 'scripts'
sys.path[:0] = [str(TESTS), str(SCRIPTS)]
import test_hostd_remote_dispatch as dispatch_fixture
import test_hostd_remote_mapping as signed_fixture
from hostd import store
from hostd.bot_clients import BotCliError
from hostd.remote_proofs import CAPABILITIES
import buzz_feishu_group_sync as gs

CHANNEL, CHAT, APP, ORIGIN, NOW = (dispatch_fixture.CHANNEL, dispatch_fixture.CHAT,
    dispatch_fixture.APP, dispatch_fixture.ORIGIN, dispatch_fixture.NOW)
WRITE = 'im:message.reactions:write_only'
SEND = 'im:message:send'
EDIT = 'im:message:update'
READ_SCOPES = {
    'im:chat.group_info:readonly',
    'im:message:readonly',
    'im:message.reactions:read',
}


class ReactionWorld(dispatch_fixture.World):
    """The dispatch test's real protected/relay setup plus actual native reaction endpoints."""

    def __init__(self, case):
        super().__init__(case)
        # Use read-only alternatives and narrow write names. In particular,
        # never retain broad `im:message`, which also grants reactions here.
        self.scopes = READ_SCOPES | {SEND, EDIT, WRITE}
        self.reactions = []
        self.reaction_mutations = []
        self.lose_next_add_response = False

    @staticmethod
    def reaction_row(reaction_id):
        return {'reaction_id': reaction_id,
                'reaction_type': {'emoji_type': 'DONE'},
                'operator': {'operator_type': 'app', 'operator_id': APP},
                'action_time': str(NOW * 1000)}

    def request(self, app, config, data_dir, method, path, **kwargs):
        if path.startswith('/open-apis/im/v1/messages/') and '/reactions' in path:
            self.assertEqual((app, Path(config), Path(data_dir)), (APP, self.cfg, self.data))
            self.assertEqual(kwargs.get('chat_id'), CHAT)
            self.api_calls.append((method, path, kwargs.get('params'), kwargs.get('data')))
            if path.endswith('/reactions') and method == 'GET':
                return {'ok': True, 'identity': 'bot', 'data': {
                    'items': list(self.reactions), 'has_more': False}}
            if path.endswith('/reactions') and method == 'POST':
                self.assertEqual(kwargs.get('data'), {'reaction_type': {'emoji_type': 'DONE'}})
                self.reaction_mutations.append((method, path))
                row = self.reaction_row('reaction-daemon-1')
                self.reactions = [row]
                if self.lose_next_add_response:
                    self.lose_next_add_response = False
                    raise BotCliError('synthetic unknown reaction response', 500, 'network', definite=False)
                return {'ok': True, 'identity': 'bot', 'data': row}
            if '/reactions/' in path and method == 'DELETE':
                reaction_id = unquote(path.rsplit('/', 1)[-1])
                self.assertEqual(reaction_id, 'reaction-daemon-1')
                self.assertIsNone(kwargs.get('data'))
                self.reaction_mutations.append((method, path))
                self.reactions = []
                return {'ok': True, 'identity': 'bot', 'data': self.reaction_row(reaction_id)}
            self.fail('unexpected native reaction request')
        return super().request(app, config, data_dir, method, path, **kwargs)

    def add_reaction_event(self, kind, root, *, text='✅'):
        tags = [['h', CHANNEL], ['e', root['id']]]
        if kind == 5:
            text = ''
        event = gs.sign_event(signed_fixture.KEY, kind, tags, text, NOW)
        self.events.append(event)
        return event


class DaemonCapabilityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Reuse the actual service configuration and socket lower boundary while
        # substituting only this fixture's native reaction HTTP endpoint.
        with mock.patch.object(dispatch_fixture, 'World', ReactionWorld):
            dispatch_fixture.RemoteDispatchTests.setUp(self)

    async def service(self):
        return await dispatch_fixture.RemoteDispatchTests.service(self)

    async def dispatcher(self, service=None):
        return await dispatch_fixture.RemoteDispatchTests.dispatcher(self, service)

    def assert_no_foreign(self):
        return dispatch_fixture.RemoteDispatchTests.assert_no_foreign(self)

    async def wait_until_retry_due(self, dispatch):
        lane = dispatch._lanes[(dispatch_fixture.PUB, CHANNEL)]
        remaining = lane.retry_at - asyncio.get_running_loop().time()
        if remaining > 0:
            await asyncio.sleep(remaining + .01)

    def assert_scope_identity_is_pinned(self):
        scope_lanes = [(path, chat) for path, chat in self.w.bot_lanes
            if path == '/open-apis/application/v6/scopes']
        self.assertTrue(scope_lanes, 'actual current own-bot scope envelope must be read')
        self.assertTrue(all(chat == CHAT for _, chat in scope_lanes),
            'capability selection must use the exact chat-bound own profile')

    async def test_fresh_daemon_lane_requests_granted_reaction_caps_and_delivers_add_remove(self):
        self.w.loop = asyncio.get_running_loop()
        root = self.w.event()
        dispatch = await self.dispatcher()
        await dispatch.refresh()
        grant = self.db.remote_grant(self.target_id)
        self.assertIsNotNone(grant)
        self.assertEqual(grant.evidence.capabilities, CAPABILITIES)
        self.assert_scope_identity_is_pinned()
        self.assertEqual(self.db.remote_delivery_by_source(self.target_id, root['id'], 'message').status, 'acked')

        add = self.w.add_reaction_event(7, root)
        await dispatch.refresh()
        add_row = self.db.remote_delivery_by_source(self.target_id, add['id'], 'reaction_add')
        self.assertEqual(add_row.status, 'acked')
        self.assertEqual((add_row.sender_app_id, add_row.message_id, add_row.reaction_id, add_row.emoji),
            (APP, 'om_sent1', 'reaction-daemon-1', 'DONE'))
        self.assertEqual((add_row.revision, add_row.scope_hash), (grant.revision, grant.scope_hash))

        remove = self.w.add_reaction_event(5, add)
        await dispatch.refresh()
        remove_row = self.db.remote_delivery_by_source(self.target_id, remove['id'], 'reaction_remove')
        self.assertEqual(remove_row.status, 'acked')
        self.assertEqual((remove_row.sender_app_id, remove_row.message_id, remove_row.reaction_id, remove_row.emoji),
            (APP, 'om_sent1', 'reaction-daemon-1', 'DONE'))
        self.assertEqual((remove_row.revision, remove_row.scope_hash), (grant.revision, grant.scope_hash))
        self.assertEqual([method for method, _ in self.w.reaction_mutations], ['POST', 'DELETE'])
        self.assertEqual(self.db.remote_grant(self.target_id), grant)
        self.assert_no_foreign()

    async def test_fresh_message_only_permissions_keep_message_behavior_and_do_not_request_reactions(self):
        self.w.scopes = READ_SCOPES | {SEND}
        root = self.w.event()
        dispatch = await self.dispatcher()
        await dispatch.refresh()
        grant = self.db.remote_grant(self.target_id)
        self.assertEqual(grant.evidence.capabilities, ('message',))
        self.assert_scope_identity_is_pinned()
        self.assertEqual(self.db.remote_delivery_by_source(self.target_id, root['id'], 'message').status, 'acked')
        add = self.w.add_reaction_event(7, root)
        await dispatch.refresh()
        self.assertEqual(self.db.remote_grant(self.target_id), grant)
        self.assertIsNone(self.db.remote_delivery_by_source(self.target_id, add['id'], 'reaction_add'))
        self.assertFalse(self.w.reaction_mutations)
        self.assert_no_foreign()

    async def test_existing_message_only_grant_is_not_expanded_when_reaction_permissions_appear(self):
        self.w.scopes = READ_SCOPES | {SEND}
        root = self.w.event()
        dispatch = await self.dispatcher()
        await dispatch.refresh()
        original = self.db.remote_grant(self.target_id)
        self.assertEqual(original.evidence.capabilities, ('message',))
        self.w.scopes.update((SEND, WRITE))
        add = self.w.add_reaction_event(7, root)
        await dispatch.refresh()
        current = self.db.remote_grant(self.target_id)
        self.assertEqual((current.revision, current.scope_hash, current.evidence.capabilities),
            (original.revision, original.scope_hash, ('message',)))
        self.assertIsNone(self.db.remote_delivery_by_source(self.target_id, add['id'], 'reaction_add'))
        self.assertFalse(self.w.reaction_mutations)
        self.assert_no_foreign()

    async def test_missing_scope_keeps_reaction_grant_pin_and_pending_add_never_replays(self):
        root = self.w.event()
        dispatch = await self.dispatcher()
        await dispatch.refresh()
        original = self.db.remote_grant(self.target_id)
        self.assertIsNotNone(original)
        self.assertEqual(original.evidence.capabilities, CAPABILITIES,
            'fresh daemon grant must pin the actual granted reaction tuple')
        add = self.w.add_reaction_event(7, root)
        self.w.lose_next_add_response = True
        await dispatch.refresh()
        unknown = self.db.remote_delivery_by_source(self.target_id, add['id'], 'reaction_add')
        self.assertEqual(unknown.status, 'unknown')
        self.assertEqual(len(self.w.reaction_mutations), 1)

        self.w.scopes = READ_SCOPES | {SEND}
        await self.wait_until_retry_due(dispatch)
        await dispatch.refresh()
        self.assert_scope_identity_is_pinned()
        current = self.db.remote_grant(self.target_id)
        still_unknown = self.db.remote_delivery_by_source(self.target_id, add['id'], 'reaction_add')
        self.assertEqual((current.revision, current.scope_hash, current.evidence.capabilities),
            (original.revision, original.scope_hash, CAPABILITIES))
        self.assertEqual((still_unknown.status, still_unknown.reaction_id), ('unknown', ''))
        self.assertEqual(len(self.w.reaction_mutations), 1)

        # Advance the actual asyncio monotonic retry deadline; keep the same
        # lane, manager, grant, and UNKNOWN reservation for the GET-only retry.
        await self.wait_until_retry_due(dispatch)
        self.w.scopes = READ_SCOPES | {SEND, EDIT, WRITE}
        await dispatch.refresh()
        self.assert_scope_identity_is_pinned()
        resumed = self.db.remote_grant(self.target_id)
        still_unknown = self.db.remote_delivery_by_source(self.target_id, add['id'], 'reaction_add')
        self.assertEqual((resumed.revision, resumed.scope_hash, resumed.evidence.capabilities),
            (original.revision, original.scope_hash, CAPABILITIES))
        self.assertEqual((still_unknown.status, still_unknown.reaction_id), ('unknown', ''))
        self.assertEqual(len(self.w.reaction_mutations), 1)
        self.assert_no_foreign()


if __name__ == '__main__':
    unittest.main()
