"""Tag refs distinguish independent notifications without changing source identity."""
import copy
import hashlib
import json
import unittest
from unittest import mock
from urllib.parse import quote
import test_hostd_delivery_mapping as mapped
import test_hostd_outlet as own
import test_gitlab_buzz_sync_events as producer

base, dm, sync = mapped.base, mapped.dm, producer.SYNC
setUpModule, tearDownModule = mapped.setUpModule, mapped.tearDownModule


def notice(ref='component/v1.2+g123', title='same commit title', event='tag_created', *, key='event-9'):
    record = sync._record(key, 'tag', event, 'instant', producer.PID, actor='alice', ref=ref,
                         url='https://gitlab.example.test/group/project/-/tags/' + quote(ref, safe='/'), title=title)
    return record


def legacy(record):
    phrase = {'tag_created': '🏷 **新建 tag**', 'tag_deleted': '🏷 **删除 tag**', 'pushed': '🔔 **通知**'}[record['event']]
    return phrase + ' · ' + record['title'] + '\n' + record['url'] + '\nref: ' + record['ref'] + '\nby: alice\n' + \
        f"[gitlab-notify:v1][object:tag][event:{record['event']}][project:{record['project']}][events:{record['key']}]"


class TagDisplay(unittest.TestCase):
    def test_new_producer_distinguishes_refs_same_commit_and_preserves_keys(self):
        first = sync.render_record(notice('one/v1', key='event-1'))
        second = sync.render_record(notice('two/v1', key='event-2'))
        self.assertNotEqual(first.splitlines()[0], second.splitlines()[0])
        for ref, text, key in [('one/v1', first, 'event-1'), ('two/v1', second, 'event-2')]:
            self.assertTrue(text.splitlines()[0].startswith(ref))
            self.assertNotIn('same commit title', text.splitlines()[0])
            self.assertEqual(text.count('same commit title'), 1)
            self.assertEqual(sync.parse_header(text)['events'], [key])

    def test_new_and_old_tag_layouts_ref_first_once_and_no_extra_header(self):
        for event, phrase in [('tag_created','新建 tag'),('tag_deleted','删除 tag'),('pushed','移动 tag')]:
            record=notice(event=event)
            for text in (legacy(record), sync.render_record(record)):
                with self.subTest(event=event,old=text==legacy(record)):
                    signed=mapped.signed(); original=copy.deepcopy(signed)
                    card=json.loads(dm.message_card(signed,'Agent',text,base.API_ORIGIN,base.CHANNEL,compact=True))
                    display=dm._gitlab_card_body(base.FGS.card_markdown(text))
                    self.assertTrue(display.startswith('['+record['ref']+']('))
                    self.assertIn(phrase, display)
                    self.assertEqual(display.count('same commit title'),1)
                    self.assertNotIn('ref:',display)
                    self.assertNotIn('[gitlab-notify:',display)
                    self.assertEqual(display.count(record['url']),1)
                    self.assertNotIn('header',card)
                    self.assertEqual(signed,original)

    def test_preview_120_visible_characters_remainder_only(self):
        record=notice(title='X'*160)
        text=legacy(record)
        card=json.loads(dm.message_card(mapped.signed(),'Agent',text,base.API_ORIGIN,base.CHANNEL,compact=True))
        left=card['elements'][0]['text']['content']
        panel=card['elements'][1]
        self.assertEqual(panel['tag'],'collapsible_panel')
        self.assertFalse(panel['expanded'])
        right=panel['elements'][0]['text']['content']
        self.assertEqual(base.FGS._plain_markdown(left)[0].count('X')+base.FGS._plain_markdown(right)[0].count('X'),160)
        self.assertEqual(len(base.FGS._plain_markdown(left)[0]),120)
        self.assertEqual((left+right).count('by: alice'),1)

    def test_strict_tag_contract_leaves_ordinary_or_malformed_unchanged(self):
        text=legacy(notice())
        invalid=[text.replace('ref: component/v1.2+g123','ref: other/v1'),
                 text.replace('\nby: alice','\nref: duplicate/v1\nby: alice'),
                 text.replace('🏷 **新建 tag**','ordinary tag discussion'),
                 text.replace('[events:event-9]','[invalid]'),
                 'quoted\n'+text+'\nordinary',
                 text.replace('component/v1.2+g123','../bad')]
        for value in invalid:
            with self.subTest(value=value[:30]):self.assertEqual(dm._gitlab_card_body(value),value)

    def test_special_ref_is_literal_and_url_percent_encoding_preserved(self):
        ref='components/a_`b`(c)%+@name'
        record=notice(ref=ref)
        text=legacy(record)
        display=dm._gitlab_card_body(text)
        self.assertTrue(display.startswith('['))
        self.assertIn(record['url'],display)
        self.assertIn('\\_',display)
        self.assertIn('\\`',display)
        card=json.loads(dm.message_card(mapped.signed(),'Agent',text,base.API_ORIGIN,base.CHANNEL,compact=True))
        self.assertNotIn('<at',json.dumps(card))

    def test_missing_ref_keeps_producer_compatibility(self):
        record=notice(); record['ref']=''
        self.assertTrue(sync.render_record(record).startswith('🏷 **新建 tag** · same commit title'))

    def test_preview_v3_freezes_previous_tag_and_issue_bytes(self):
        goldens=['9a8ed77c43ac4f8a4969089ca62e20ff32a4b10d2d58ef6e8774c3970ae04fca', 'd8b811e2285d4f5e01858e043d190c1493797b07fd62300ed343ba9fd9d7f6c3']
        for text, expected in zip([legacy(notice(title='long title '*40)), 'plain '*60],goldens):
            previous=dm.message_card(mapped.signed(),'Agent',text,base.API_ORIGIN,base.CHANNEL,compact='preview_v3')
            self.assertNotIn('header',json.loads(previous))
            self.assertEqual(hashlib.sha256(previous.encode()).hexdigest(),expected)
        self.assertIn('[gitlab-notify:',dm.message_card(mapped.signed(),'Agent',legacy(notice()),base.API_ORIGIN,base.CHANNEL,compact='preview_v3'))
        issue='🟢 **已打开** · [#42 title](https://gitlab.example.test/g/p/-/issues/42)\n[gitlab-notify:v1][object:issue][type:bug][status:unknown][state:opened][change:activity][project:481][issue:42]'
        self.assertEqual(dm.message_card(mapped.signed(),'Agent',issue,base.API_ORIGIN,base.CHANNEL,compact='preview_v3'),dm.message_card(mapped.signed(),'Agent',issue,base.API_ORIGIN,base.CHANNEL,compact=True))


class TagPending(base.TmpCase):
    assembly=own.OwnOutlet.assembly
    event=own.OwnOutlet.event

    def test_cross_round_tag_summary_edit_updates_same_native_message(self):
        from gitlab_tag_batches import render_snapshot
        world,run,adapter=self.assembly()
        snapshot={'sha':'a'*40,'project':481,'revision':1,'source_keys':['event-1'],
                  'refs':[{'ref':'one/v1','url':'https://gitlab.example.test/g/p/-/tags/one/v1'}],
                  'titles':['same commit'],'actors':['alice']}
        original=self.event(content=render_snapshot(snapshot));world.events=[original]
        adapter.deliver(original)
        self.assertEqual(len(world.messages),1)
        original_mid=world.messages[0]['message_id']
        snapshot['revision']=2;snapshot['source_keys'].append('event-2')
        snapshot['refs'].append({'ref':'two/v2','url':'https://gitlab.example.test/g/p/-/tags/two/v2'})
        edit=self.event(kind=40003,tags=[['e',original['id']]],content=render_snapshot(snapshot))
        world.events.append(edit);world.relay_events.append(edit)
        adapter.deliver(edit)
        self.assertEqual(len(world.messages),1);self.assertEqual(world.messages[0]['message_id'],original_mid)
        patches=[argv for argv,_ in world.bot_calls if argv[2:4]==['api','PATCH']]
        self.assertEqual(len(patches),1)
        payload=json.loads(patches[0][patches[0].index('--data')+1])
        self.assertIn('one/v1',payload['content']);self.assertIn('two/v2',payload['content'])
        self.assertNotIn('[gitlab-tag-batch:',payload['content'])

    def test_preupgrade_timeout_uses_byte_identical_request_and_key(self):
        world,run,adapter=self.assembly()
        event=self.event(content=legacy(notice(title='commit '*60)));world.events=[event];world.lark_send_fail=['timeout']
        renderer=own.outlet.message_card
        def previous(*args,**kwargs):kwargs['compact']='preview_v3';return renderer(*args,**kwargs)
        with mock.patch.object(own.outlet,'message_card',side_effect=previous):
            with self.assertRaises(base.FGS.GroupSyncError):adapter.deliver(event)
        before=dict(run.mapping_store.delivery_by_source('test',event['id'],'b2f',agent_id=base.AGENT2_PK))
        adapter.deliver(event)
        sends=[argv for argv,_ in world.bot_calls if '--content' in argv]
        self.assertEqual(len(sends),2)
        self.assertEqual(sends[0],sends[1])
        after=dict(run.mapping_store.delivery_by_source('test',event['id'],'b2f',agent_id=base.AGENT2_PK))
        self.assertEqual(before['id'],after['id']);self.assertEqual(before['content_hash'],after['content_hash'])
        self.assertEqual(after['status'],'acked');self.assertEqual(len(world.messages),1)

if __name__=='__main__':unittest.main()
