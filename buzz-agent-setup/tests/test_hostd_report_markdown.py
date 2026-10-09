"""Report rendering through the signed-message/card/outlet public boundary."""
import hashlib
import json
import unittest
from unittest import mock
import test_hostd_delivery_mapping as mapped
import test_hostd_outlet as own

base, dm = mapped.base, mapped.dm
setUpModule, tearDownModule = mapped.setUpModule, mapped.tearDownModule
URL = 'https://gitlab.addx.ai/engineering/skills/-/issues/244'
REPORT = ('## 交付进展\n\n摘要：实现推进，验收待完成。\n\n'
          '| 事项 | GitLab 责任人 | 通知状态 |\n'
          '| --- | :--- | ---: |\n'
          f'| [Issue #244]({URL}) | jchen | 未通知 |\n'
          '| 空值 | | 保留 |\n\n' + '边界说明。' * 60)


def body(raw):
    doc = json.loads(raw)
    return '\n'.join(item['content'] for item in walk(doc['elements'])
                     if item.get('tag') == 'lark_md' and 'content' in item)


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


class ReportMarkdown(unittest.TestCase):
    def test_report_headings_tables_and_named_links_render_in_native_card(self):
        raw = dm.message_card(mapped.signed(), 'Agent', REPORT, base.API_ORIGIN, base.CHANNEL, compact=True)
        text = body(raw)
        self.assertNotIn('| ---', text)
        self.assertNotIn('## 交付进展', text)
        self.assertIn('**交付进展**', text)
        self.assertIn(f'[Issue #244]({URL})', text)
        self.assertIn('GitLab 责任人：jchen', text)
        self.assertIn('GitLab 责任人：；通知状态：保留', text)
        self.assertIn(mapped.signed()['id'], raw)
        self.assertLess(len(raw.encode()), base.FGS.MAX_CARD_BYTES)

    def test_fenced_code_escaped_pipes_and_invalid_tables_keep_content(self):
        text = ('```md\n## 原文\n| a | b |\n| --- | --- |\n| x | y |\n```\n'
                '~~~\n# 原文二\n~~~\n'
                '| 条件 | 值 |\n| --- | --- |\n| `a|b` | x\\|y |\n\n'
                '| 不是 | 表格 |\n| --- |\n正文')
        actual = body(dm.message_card(mapped.signed(), 'Agent', text, base.API_ORIGIN, base.CHANNEL, compact=True))
        self.assertIn('## 原文', actual)
        self.assertIn('# 原文二', actual)
        self.assertIn('条件：`a|b`；值：x\\|y', actual)
        self.assertIn('| 不是 | 表格 |\n| --- |', actual)

    def test_table_scan_does_not_consume_fences_or_indented_code(self):
        text = '| a | b |\n| --- | --- |\n| x | y |\n```text|csv\n# fenced heading\n```'
        actual = dm.card1_report_blocks(text)
        self.assertEqual(actual, '• a：x；b：y\n```text|csv\n# fenced heading\n```')
        indented = '    | a | b |\n    | --- | --- |\n    | x | y |'
        for prefix in ('    ', '\t', ' \t', '  \t', '   \t'):
            code = '\n'.join(prefix + line.lstrip() for line in indented.splitlines())
            self.assertEqual(dm.card1_report_blocks(code), code)
        malformed = '| a | b |\n| --- | --- |\n| x | y |\n| one |'
        self.assertEqual(dm.card1_report_blocks(malformed), malformed)

    def test_one_column_table_retains_its_header_and_data(self):
        self.assertEqual(dm.card1_report_blocks('| 项目 |\n| --- |\n| #244 |'), '• 项目：#244')

    def test_previous_format_hash_matches_merged_baseline(self):
        raw = dm.message_card(mapped.signed(), 'Agent', REPORT, base.API_ORIGIN, base.CHANNEL, compact='preview_v5')
        self.assertEqual(hashlib.sha256(raw.encode()).hexdigest(),
                         '004b832da29ec28fa36beaa02e355b2c780c36c2bac429975ee9da011c5864f1')

    def test_untrusted_tags_stay_neutralized_after_conversion(self):
        text = '# <at id=all>伪造</at>\n| a | b |\n| --- | --- |\n| <font color=red>x</font> | y |'
        raw = dm.message_card(mapped.signed(), 'Agent', text, base.API_ORIGIN, base.CHANNEL, compact=True)
        self.assertNotIn('<at', raw)
        self.assertNotIn('<font', raw)
        self.assertIn('＜at', raw)


class OutletReport(base.TmpCase):
    assembly = own.OwnOutlet.assembly
    event = own.OwnOutlet.event

    def test_unknown_pre_upgrade_report_reuses_original_payload_and_key(self):
        world, run, adapter = self.assembly()
        event = self.event(content=REPORT)
        world.events = [event]
        world.lark_send_fail = ['timeout']
        renderer = own.outlet.message_card
        def previous(*args, **kwargs):
            kwargs['compact'] = 'preview_v5'
            return renderer(*args, **kwargs)
        with mock.patch.object(own.outlet, 'message_card', side_effect=previous):
            with self.assertRaises(base.FGS.GroupSyncError):
                adapter.deliver(event)
        before = dict(run.mapping_store.delivery_by_source('test', event['id'], 'b2f', agent_id=base.AGENT2_PK))
        adapter.deliver(event)
        sends = [argv for argv, _ in world.bot_calls if '--content' in argv]
        self.assertEqual(sends[0], sends[1])
        after = dict(run.mapping_store.delivery_by_source('test', event['id'], 'b2f', agent_id=base.AGENT2_PK))
        self.assertEqual(before['id'], after['id'])
        self.assertEqual(before['content_hash'], after['content_hash'])
        self.assertEqual(after['status'], 'acked')
        self.assertEqual(len(world.messages), 1)

    def test_new_report_recovers_and_proves_its_signed_mapping(self):
        world, run, adapter = self.assembly()
        event = self.event(content=REPORT)
        world.events = [event]
        target = adapter.deliver(event)
        self.assertIn('GitLab 责任人：jchen', body(next(row for row in world.messages if row['message_id'] == target)['content']))
        self.assertEqual(adapter.deliver(event), target)
        self.assertEqual(len(world.messages), 1)


if __name__ == '__main__':
    unittest.main()
