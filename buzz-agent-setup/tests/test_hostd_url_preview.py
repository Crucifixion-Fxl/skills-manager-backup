"""URL-safe visible previews and byte-identical recovery of old send intents."""
import hashlib
import json
import unittest
from unittest import mock
import test_hostd_delivery_mapping as mapped
import test_hostd_outlet as own

base, dm = mapped.base, mapped.dm
setUpModule, tearDownModule = mapped.setUpModule, mapped.tearDownModule
URL = 'https://gitlab.addx.ai/SYS/launch_monitor/-/issues/494'
TEXT = '标题与正文。' * 16 + URL + '\n' + '后续说明。' * 50


def parts(text):
    card = json.loads(dm.message_card(mapped.signed(), 'Agent', text, base.API_ORIGIN, base.CHANNEL, compact=True))
    panel = next((e for e in card['elements'] if e['tag'] == 'collapsible_panel'), None)
    if panel is None:return card, card['elements'][0]['text']['content'], ''
    return card, card['elements'][0]['text']['content'], panel['elements'][0]['text']['content']


class URLPreview(unittest.TestCase):
    def test_user_launch_monitor_bare_url_is_whole_in_remainder(self):
        card,left,right = parts(TEXT)
        self.assertNotIn('header',card)
        self.assertNotIn('https://',left)
        self.assertTrue(right.startswith(URL))
        self.assertEqual(left+right,base.FGS.card_markdown(TEXT))
        self.assertEqual((left+right).count(URL),1)

    def test_markdown_and_autolink_are_indivisible_at_boundary(self):
        for link in ('[issue 494 full title]('+URL+')','<'+URL+'>'):
            text='字'*115+link+'\n'+'后'*200
            _,left,right=parts(text)
            self.assertEqual(left,'字'*115)
            self.assertEqual(left+right,base.FGS.card_markdown(text))
            self.assertEqual((left+right).count(URL),1)

    def test_first_long_url_or_link_uses_soft_budget_not_all_folded(self):
        for link in ('https://gitlab.addx.ai/'+'longpath/'*25,'['+'长标题'*60+']('+URL+')'):
            _,left,right=parts(link+'\n'+'后续正文'*100)
            self.assertIn(link,left)
            self.assertTrue(right.startswith('\n'))
            self.assertNotIn(link,right)


class URLSizeAndLegacy(unittest.TestCase):
    def test_long_bare_url_has_short_label_exact_href_and_no_repeated_body(self):
        url='https://gitlab.addx.ai/'+('path/'*150)+'?ref=complete'
        for text in (url,url+'\n'+'body '*100):
            card,left,right=parts(text)
            self.assertTrue(left.startswith('[链接（gitlab.addx.ai）]('))
            self.assertIn(']('+url+')',left)
            self.assertEqual((left+right).count(url),1)
            self.assertNotIn('header',card)
            if text==url:self.assertEqual(right,'')

    def test_balanced_parentheses_escaped_labels_and_trailing_punctuation(self):
        for token in ('[label](https://gitlab.addx.ai/a_(b)?q=(c))',
                      r'[label \[literal\]](https://gitlab.addx.ai/a_(b))',
                      'https://gitlab.addx.ai/a_(b)?q=(c)',
                      '<https://gitlab.addx.ai/a_(b)>'):
            text='字'*116+token+'。\n'+'rest '*60
            _,left,right=parts(text)
            self.assertEqual(left,'字'*116)
            self.assertEqual(left+right,base.FGS.card_markdown(text))

    def test_exact_visible_budget_keeps_whole_link(self):
        token='[label]('+URL+')'
        _,left,right=parts('字'*115+token+'\n'+'rest '*60)
        self.assertEqual(left,'字'*115+token)
        self.assertTrue(right.startswith('\n'))

    def test_byte_cap_never_leaves_partial_raw_or_markdown_destination(self):
        giant='https://gitlab.addx.ai/'+'p'*40000
        for token in (giant,'[complete label]('+giant+')','<'+giant+'>'):
            for prefix in ('','前文。'*500):
                text=prefix+token+'\n'+'后文'*100
                raw=dm.message_card(mapped.signed(),'Agent',text,base.API_ORIGIN,base.CHANNEL,compact=True)
                self.assertLess(len(raw.encode()),base.FGS.MAX_CARD_BYTES)
                self.assertIn(base.FGS.CARD_TRUNCATED_NOTE,raw)
                self.assertNotIn('https://gitlab.addx.ai',raw)
                self.assertIn(base.FGS.CARD_OPEN_TEXT,raw)

    def test_preview_v4_keeps_preupgrade_golden_bytes(self):
        import test_hostd_tag_preview as tags
        docs=[TEXT,tags.legacy(tags.notice(title='commit '*60)),'plain '*60]
        goldens=['65ee4fc5d063d08c33479e5480dd0dcc2d8135d084806b6061d6b1d09831d0f9',
                 'ba6d1e984f5751f3b522dd8edb0132ab3eff19a4052db0b611af68a023605437',
                 'd8b811e2285d4f5e01858e043d190c1493797b07fd62300ed343ba9fd9d7f6c3']
        for text,expected in zip(docs,goldens):
            raw=dm.message_card(mapped.signed(),'Agent',text,base.API_ORIGIN,base.CHANNEL,compact='preview_v4')
            self.assertEqual(hashlib.sha256(raw.encode()).hexdigest(),expected)


class URLIndependentRegressions(unittest.TestCase):
    def test_unpaired_parentheses_do_not_create_truncated_short_href(self):
        for middle in ('a)?q=', 'a(?q=', 'a)?q=(b'):
            url='https://gitlab.addx.ai/'+middle+'x'*600
            _,left,right=parts(url+'\n'+'正文'*150)
            # Keep the entire original URL when a short Markdown destination
            # cannot represent it safely; do not edit encoded query semantics.
            self.assertEqual(left,url)
            self.assertEqual(left+right,url+'\n'+'正文'*150)
            self.assertFalse(left.startswith('[链接'))

    def test_nested_markdown_label_moves_as_whole_token(self):
        for label in ('outer [inner] '+'x'*20, r'outer [inner [deep]] \[literal\] '+'x'*20):
            token='['+label+'](https://example.com/target)'
            text='字'*115+token+'\n'+'正文'*150
            _,left,right=parts(text)
            self.assertEqual(left,'字'*115)
            self.assertTrue(right.startswith(token))
            self.assertEqual(left+right,text)

    def test_formatted_long_url_retains_short_label_and_visible_count(self):
        url='https://gitlab.addx.ai/'+'x'*700
        short='[链接（gitlab.addx.ai）]('+url+')'
        for marker in ('**','__','*','_','~~'):
            text=marker+url+marker+'\n'+'正文'*150
            left,right,count=dm._preview_parts(text,atomic_urls=True)
            self.assertTrue(left.startswith(marker+short+marker))
            self.assertEqual(count,120)
            self.assertEqual(len(base.FGS._plain_markdown(left)[0]),120)
            self.assertEqual((left+right).count(url),1)
            self.assertEqual((left+right).count('正文'),150)


class PendingURL(base.TmpCase):
    assembly=own.OwnOutlet.assembly
    event=own.OwnOutlet.event

    def test_unknown_before_upgrade_keeps_exact_payload_id_and_send_key(self):
        world,run,adapter=self.assembly()
        event=self.event(content=TEXT);world.events=[event];world.lark_send_fail=['timeout']
        renderer=own.outlet.message_card
        def previous(*args,**kwargs):kwargs['compact']='preview_v4';return renderer(*args,**kwargs)
        with mock.patch.object(own.outlet,'message_card',side_effect=previous):
            with self.assertRaises(base.FGS.GroupSyncError):adapter.deliver(event)
        before=dict(run.mapping_store.delivery_by_source('test',event['id'],'b2f',agent_id=base.AGENT2_PK))
        adapter.deliver(event)
        sends=[argv for argv,_ in world.bot_calls if '--content' in argv]
        self.assertEqual(len(sends),2);self.assertEqual(sends[0],sends[1])
        after=dict(run.mapping_store.delivery_by_source('test',event['id'],'b2f',agent_id=base.AGENT2_PK))
        self.assertEqual(before['id'],after['id']);self.assertEqual(before['content_hash'],after['content_hash'])
        self.assertEqual(after['status'],'acked');self.assertEqual(len(world.messages),1)

    def test_new_url_preview_unknown_reuses_exact_native_request(self):
        world,run,adapter=self.assembly()
        event=self.event(content=TEXT);world.events=[event];world.lark_send_fail=['timeout']
        with self.assertRaises(base.FGS.GroupSyncError):adapter.deliver(event)
        adapter.deliver(event)
        sends=[argv for argv,_ in world.bot_calls if '--content' in argv]
        self.assertEqual(len(sends),2);self.assertEqual(sends[0],sends[1]);self.assertEqual(len(world.messages),1)


if __name__=='__main__':unittest.main()
