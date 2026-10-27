"""Native history metadata stays complete independently of body support."""
import unittest
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit
import test_hostd_http_wiring as wire


@unittest.skipUnless(wire.AESGCM, 'protected bot transport requires cryptography')
class MappingHistory(unittest.TestCase):
    profile = wire.Wiring.profile
    setUp = wire.Wiring.setUp
    no_cli = wire.Wiring.no_cli
    api_calls = wire.Wiring.api_calls

    def history(self, **kwargs):
        self.cli.active_phase = 'buzz'
        return self.cli.mapping_messages(wire.CHAT,
            datetime.fromtimestamp(1699999900, timezone.utc),
            end=datetime.fromtimestamp(1700000100, timezone.utc), **kwargs)

    def test_unsupported_bodies_do_not_hide_native_mapping_candidates(self):
        rows = [wire.message(kind=kind, body={'file_key': 'opaque'})
                for kind in ('file', 'todo', 'merge_forward', 'interactive')]
        for i, row in enumerate(rows):
            row['message_id'] = 'om_' + str(i) * 32
        self.server.responses.append(wire.page(rows))
        found, partial = self.history()
        self.assertFalse(partial)
        self.assertEqual(found, rows)
        self.assertFalse(self.cli.partial_reads)

    def test_frozen_interval_and_all_native_pages(self):
        first, second = wire.message(), wire.message(mid=wire.ROOT)
        self.server.responses += [wire.page([first], True, 'opaque-next'), wire.page([second])]
        rows, partial = self.history()
        self.assertFalse(partial)
        self.assertEqual(rows, [first, second])
        queries = [parse_qs(urlsplit(c['path']).query) for c in self.api_calls()]
        self.assertEqual(queries[0]['start_time'], ['1699999900'])
        self.assertEqual(queries[0]['end_time'], ['1700000100'])
        self.assertEqual({k:v for k,v in queries[1].items() if k!='page_token'}, queries[0])

    def test_truncation_remains_partial(self):
        self.server.responses.append(wire.page([wire.message()], True, 'next'))
        _, partial = self.history(page_limit=1)
        self.assertTrue(partial)
        self.assertIn('buzz', self.cli.partial_reads)

    def test_duplicate_cross_chat_and_bad_timestamp_never_prove_absence(self):
        from hostd.bot_clients import gs
        for rows in ([wire.message(), wire.message()],
                     [dict(wire.message(), chat_id='oc_other')],
                     [dict(wire.message(), create_time='bad')]):
            with self.subTest(rows=rows):
                self.server.responses.append(wire.page(rows))
                with self.assertRaises(gs.GroupSyncError): self.history()


if __name__ == '__main__':
    unittest.main()
