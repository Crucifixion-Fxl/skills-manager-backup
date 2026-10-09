"""Complete live-shaped pages must survive Feishu's senderless system entries."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from hostd.bot_clients import BotLarkCli


class SystemMessagePage(unittest.TestCase):
    def page(self, kind, sender):
        client = object.__new__(BotLarkCli)
        client.active_phase = 'feishu'
        client.partial_reads = set()
        row = {'message_id': 'om_system', 'create_time': '1791372000000',
               'msg_type': kind, 'sender': sender, 'body': {'content': '{"text":"message"}'}}
        client._http_request = lambda *args: {'data': {'items': [row], 'has_more': False}}
        return client, client._http_pages('/open-apis/im/v1/messages', {}, messages=True)

    def test_senderless_system_entry_keeps_complete_page_and_empty_content(self):
        client, result = self.page('system', {'id': '', 'id_type': '', 'sender_type': '', 'tenant_key': ''})
        self.assertTrue(result['meta']['pagination']['complete'])
        self.assertFalse(result['data']['has_more'])
        self.assertEqual(result['data']['messages'][0]['content'], '')
        self.assertEqual(client.partial_reads, set())

    def test_senderless_text_still_blocks_page(self):
        client, result = self.page('text', {'id': '', 'id_type': '', 'sender_type': ''})
        self.assertFalse(result['meta']['pagination']['complete'])
        self.assertTrue(result['data']['has_more'])
        self.assertIn('feishu', client.partial_reads)


if __name__ == '__main__':
    unittest.main()
