"""Card1 readback shape observed with an isolated real Feishu bot, 2026-10-04."""
import copy
import json
import unittest
import test_hostd_delivery_mapping as fixture
import join_cards

base, dm = fixture.base, fixture.dm
setUpModule = base.setUpModule
tearDownModule = base.tearDownModule


class RenderedReadback(base.TmpCase):
    def test_native_card1_note_preserves_exact_request_and_generation(self):
        row = {'request_id': 'JOIN-1860cafe', 'callback_app_id': base.AGENT_APP, 'chat_id': base.CHAT}
        body = join_cards.card(row, 1)
        body['config'] = {}
        note=next(e for e in body['elements'] if e['tag']=='note')
        note['elements'][0]['tag'] = 'lark_md'
        view = {'message_id': 'om_card', 'chat_id': base.CHAT,
                'sender': {'id': base.AGENT_APP, 'id_type': 'app_id', 'sender_type': 'app'},
                'body': {'content': json.dumps(body)}}
        self.assertTrue(join_cards.BotCards._matches(view, row, 1))
        self.assertFalse(join_cards.BotCards._matches(view, row, 2))
        for text in ('申请 JOIN-1860cafe · 卡片 1 extra', '申请 JOIN-1860cabf · 卡片 1',
                     '**申请 JOIN-1860cafe · 卡片 1**'):
            note['elements'][0]['content'] = text
            view['body']['content'] = json.dumps(body)
            self.assertFalse(join_cards.BotCards._matches(view, row, 1))

    def view(self):
        event = base.FGS.sign_event(base.OWNER_KEY, 9, [['h', base.CHANNEL]], 'hello', int(base.NOW.timestamp()))
        link = base.FGS.open_link(base.API_ORIGIN, event['id'], base.CHANNEL, None)
        body = {'title': 'Readback probe', 'elements': [
            [{'tag': 'text', 'text': 'Only a readback probe'}],
            [{'tag': 'note', 'elements': [{'tag': 'text', 'text': '申请 JOIN-1860cafe · 卡片 1'}]}],
            [{'tag': 'note', 'elements': [{'tag': 'a', 'href': link, 'text': '在 Buzz 中打开'}]}]]}
        view = fixture.bot_message(event)
        view['body']['content'] = json.dumps(body, ensure_ascii=False)
        return event, link, body, view

    def test_owner_card_recovers_exact_marker_from_rendered_note(self):
        _, _, body, view = self.view()
        row = {'request_id': 'JOIN-1860cafe', 'callback_app_id': base.AGENT_APP, 'chat_id': base.CHAT}
        self.assertTrue(join_cards.BotCards._matches(view, row, 1))
        self.assertFalse(join_cards.BotCards._matches(view, row, 2))
        wrong = copy.deepcopy(view)
        wrong['sender']['id'] = base.OWNER_APP
        self.assertFalse(join_cards.BotCards._matches(wrong, row, 1))
        body['elements'].pop(1)
        body['elements'][0][0]['text'] = '申请 JOIN-1860cafe · 卡片 1'
        view['body']['content'] = json.dumps(body)
        self.assertFalse(join_cards.BotCards._matches(view, row, 1))

    def test_bottom_rendered_anchor_recovers_only_exact_signed_mapping(self):
        event, link, body, view = self.view()
        self.assertEqual(dm._receipt_link(view), link)
        args = (event, base.CHANNEL, base.CHAT, base.API_ORIGIN, {base.OWNER_PK}, {}, base.AGENT_APP)
        self.assertIsNotNone(dm.feishu_mapping(view, *args))
        altered = copy.deepcopy(body)
        altered['elements'][-1][0]['elements'][0]['href'] += '&c=other'
        wrong = dict(view, body={'content': json.dumps(altered)})
        self.assertIsNone(dm.feishu_mapping(wrong, *args))
        body['elements'].append([{'tag': 'text', 'text': 'untrusted trailing row'}])
        view['body']['content'] = json.dumps(body)
        self.assertIsNone(dm._receipt_link(view))


if __name__ == '__main__': unittest.main()
