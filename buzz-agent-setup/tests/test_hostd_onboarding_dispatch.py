"""Daemon dispatch must consume callbacks and unbound invites, without logging them."""
import json
from types import SimpleNamespace
import unittest
from unittest import mock
import test_hostd_wiring_lifecycle as fixture


class OnboardingDispatch(unittest.IsolatedAsyncioTestCase):
    setUp = fixture.Wiring.setUp

    async def test_card_callback_reaches_actual_runtime_queue_without_status_payload(self):
        runtime = SimpleNamespace(enqueue_feed=mock.Mock())
        self.h.onboarding = runtime
        event = {'app': 'cli_alpha', 'type': 'card.action.trigger', 'chat_id': 'oc_alpha',
                 'token': 'private-callback-token', 'operator': {'open_id': 'private-person'},
                 'action': {'value': {'request_id': 'JOIN-1234', 'choice': 'allow'}}}
        await self.h.on_feishu('cli_alpha', event, {'oc_alpha': 'alpha'})
        runtime.enqueue_feed.assert_called_once_with('cli_alpha', event)
        serialized = json.dumps(self.h.status)
        self.assertNotIn('private-callback-token', serialized)
        self.assertNotIn('private-person', serialized)

    async def test_unbound_bot_invite_is_dispatched_without_inventing_a_binding(self):
        runtime = SimpleNamespace(enqueue_feed=mock.Mock())
        self.h.onboarding = runtime
        event = {'app': 'cli_alpha', 'type': 'im.chat.member.bot.added_v1', 'chat_id': 'oc_unbound',
                 'event_id': 'invite1', 'operator_id': {'open_id': 'ou_inviter'}}
        await self.h.on_feishu('cli_alpha', event, {'oc_alpha': 'alpha'})
        runtime.enqueue_feed.assert_called_once_with('cli_alpha', event)
        self.assertEqual(self.h.dirty['alpha'], set())
        self.assertNotIn('oc_unbound', self.h.reg.by_chat())

    async def test_actual_connected_event_both_queues_discovery_and_catches_up_binding(self):
        runtime = SimpleNamespace(enqueue_feed=mock.Mock())
        self.h.onboarding = runtime
        event = {'app': 'cli_alpha', 'type': '_connected'}
        await self.h.on_feishu('cli_alpha', event, {'oc_alpha': 'alpha'})
        runtime.enqueue_feed.assert_called_once_with('cli_alpha', event)
        self.assertEqual(self.h.dirty['alpha'], {'members', 'feishu', 'notice'})

    async def test_other_app_and_malformed_events_never_enter_runtime_queue(self):
        runtime = SimpleNamespace(enqueue_feed=mock.Mock())
        self.h.onboarding = runtime
        for event in ({'app': 'cli_beta', 'type': 'card.action.trigger'},
                      {'app': 'cli_alpha', 'type': 'im.chat.member.bot.added_v1', 'chat_id': []}):
            await self.h.on_feishu('cli_alpha', event, {'oc_alpha': 'alpha'})
        runtime.enqueue_feed.assert_not_called()


if __name__ == '__main__':
    unittest.main()
