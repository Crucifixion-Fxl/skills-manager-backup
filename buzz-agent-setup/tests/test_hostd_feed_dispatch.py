"""Trusted SDK IPC retains invitation evidence; malformed routing cannot kill feeds."""
import json
import unittest
from unittest import mock

import test_hostd_wiring_lifecycle as fixture


class Routing(unittest.IsolatedAsyncioTestCase):
    setUp = fixture.Wiring.setUp

    async def test_nonstring_chat_does_not_stop_binding_feed(self):
        for chat in ({'private': 'never-persist'}, ['oc_alpha'], 42):
            await self.h.on_feishu('cli_alpha', {
                'type': 'im.message.receive_v1', 'chat_id': chat,
                'message_id': 'om_target'}, {'oc_alpha': 'alpha'})
        self.assertEqual(self.h.dirty['alpha'], set())
        await self.h.on_feishu('cli_alpha', {
            'type': 'im.message.receive_v1', 'chat_id': 'oc_alpha',
            'message_id': 'om_target'}, {'oc_alpha': 'alpha'})
        self.assertIn('feishu', self.h.dirty['alpha'])
        self.assertNotIn('never-persist', self.h.status_file.read_text())


@unittest.skipUnless(fixture.HAS_DEPS, 'requires actual lark-oapi SDK')
class InvitationEnvelope(unittest.TestCase):
    def test_bot_added_retains_actual_operator_id_and_event_id_only(self):
        from lark_oapi.api.im.v1.model.p2_im_chat_member_bot_added_v1 import P2ImChatMemberBotAddedV1
        raw = {'schema': '2.0', 'header': {'event_id': 'evt_invite'}, 'event': {
            'chat_id': 'oc_scope', 'operator_id': {'open_id': 'ou_inviter', 'union_id': 'on_inviter'},
            'private_body': 'never-forward'}}
        data = P2ImChatMemberBotAddedV1(raw)
        ff = fixture.ff
        builder = mock.Mock()
        for etype in ff.EVENTS:
            getattr(builder, 'register_p2_' + etype.replace('.', '_')).return_value = builder
        builder.register_p2_card_action_trigger.return_value = builder
        rows = []
        with mock.patch.object(ff.lark.EventDispatcherHandler, 'builder', return_value=builder), \
                mock.patch.object(ff, 'emit', rows.append):
            ff.handler('cli_scope')
            callback = builder.register_p2_im_chat_member_bot_added_v1.call_args.args[0]
            callback(data)
        row = rows[0]
        self.assertEqual(row['event_id'], 'evt_invite')
        self.assertEqual(row['operator_id'], {'open_id': 'ou_inviter', 'union_id': 'on_inviter'})
        self.assertEqual(row['app'], 'cli_scope')
        self.assertEqual(row['chat_id'], 'oc_scope')
        self.assertNotIn('never-forward', json.dumps(row))


if __name__ == '__main__':
    unittest.main()
