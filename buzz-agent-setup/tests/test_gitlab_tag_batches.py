"""Signed cross-poll tag aggregation and crash recovery via ordinary outbox."""
import copy
import datetime
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock
import test_gitlab_buzz_sync_events as fixtures
import test_hostd_delivery_mapping as mapped
import gitlab_tag_batches as batches

sync=fixtures.SYNC
KEY=mapped.base.OWNER_KEY
PUB=mapped.base.FGS._signer_pubkey(KEY)
SHA='a'*40


class SignedBuzz:
    def __init__(self):self.events=[];self.writes=[];self.now=1790000000;self.fail=None;self.hidden=set();self.channel=fixtures.CHANNEL;self.key=KEY
    def send(self,content,reply_to=None,mentions=()):
        tags=[['h',self.channel]]
        if reply_to:tags.append(['e',reply_to,'','reply'])
        tags += [['p',pk] for pk in mentions]
        return self._publish(9,tags,content)
    def edit(self,target,content):return self._publish(40003,[['h',self.channel],['e',target]],content)
    def _publish(self,kind,tags,content):
        self.now+=1
        ev=mapped.base.FGS.sign_event(self.key,kind,tags,content,self.now);self.events.append(ev);self.writes.append(ev)
        mode=self.fail;self.fail=None
        if mode:
            if mode=='hidden':self.hidden.add(ev['id'])
            raise sync.SyncError('synthetic lost acknowledgement')
        return ev['id']
    def channel_messages(self,since):return [copy.deepcopy(e) for e in self.events if e['kind']==9 and e['id'] not in self.hidden and ['h',self.channel] in e['tags']]
    def compact_edits(self,roots):return [copy.deepcopy(e) for e in self.events if e['kind']==40003 and e['id'] not in self.hidden and sync.edit_target(e) in {r['id'] for r in roots}]
    def thread(self,target):return [copy.deepcopy(e) for e in self.events if e['id'] not in self.hidden and (e['id']==target or sync.edit_target(e)==target)]


class TagBatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name);self.gitlab=fixtures.FakeGitLab();self.buzz=SignedBuzz()
        self.cfg={'channel_id':fixtures.CHANNEL,'publisher_pubkey':PUB,'since':fixtures.SINCE,'include_confidential':False,
                  'exclude':[],'diff':{'enabled':False,'private':False},'gitlab':{'base_url':'http://127.0.0.1:8929','token_env':'TOKEN','bot_user_id':fixtures.BOT_ID,'bot_username':fixtures.BOT,'projects':[fixtures.PID]},'buzz':{},'people':{}}
    def tearDown(self):self.tmp.cleanup()
    def service(self):
        s=sync.Syncer(self.cfg,self.gitlab,self.buzz,state_dir=self.path);s._project_visibilities[fixtures.PID]='public';s._scan_date='2026-09-13';return s
    def event(self,key,ref,*,sha=SHA,action='pushed new'):
        ev=fixtures.push_event(key,action=action,ref_type='tag',ref=ref,title='same commit')
        ev['push_data']['commit_to']=sha;return ev
    def scan(self,*events):
        self.gitlab.event_list=list(events);s=self.service();s._reconcile_pending();summary={'notified':{'instant':0,'milestone':0},'summary_requests':[]}
        s._sync_top_level(fixtures.PID,self.gitlab.project(fixtures.PID),fixtures.SINCE,summary,False);return s,summary
    def latest(self):return batches.parse_snapshot(self.buzz.events[-1]['content'],sync.parse_header)

    def test_two_refs_one_root_then_cross_poll_union_edit_same_root(self):
        a,b,c=[self.event(i,ref) for i,ref in [(1,'one/v1'),(2,'two/v2'),(3,'three/v3')]]
        self.scan(a,b);root=self.buzz.events[0]['id'];self.assertEqual(len(self.buzz.writes),1)
        self.assertEqual(self.latest()['source_keys'],['event-1','event-2'])
        self.scan(c);self.assertEqual(len(self.buzz.writes),2);self.assertEqual(sync.edit_target(self.buzz.events[-1]),root)
        self.assertEqual([r['ref'] for r in self.latest()['refs']],['one/v1','three/v3','two/v2'])
        self.scan(a,b,c,c);self.assertEqual(len(self.buzz.writes),2)
        self.assertEqual(self.latest()['source_keys'],['event-1','event-2','event-3'])

    def test_cache_and_batch_ledger_loss_recovers_same_root_and_all_refs(self):
        a,b=self.event(1,'one/v1'),self.event(2,'two/v2')
        self.scan(a);root=self.buzz.events[0]['id']
        for path in self.path.glob('gitlab-tag-batch-*.json'):path.unlink()
        self.scan(b);self.assertEqual(sync.edit_target(self.buzz.events[-1]),root)
        self.assertEqual(len(self.latest()['refs']),2)
        for path in self.path.glob('*.json'):path.unlink()
        self.scan(a,b);self.assertEqual(len(self.buzz.writes),2)

    def test_unknown_root_stays_byte_frozen_then_reconciles_before_appending(self):
        a,b=self.event(1,'one/v1'),self.event(2,'two/v2');self.buzz.fail='hidden'
        with self.assertRaises(sync.SyncError):self.scan(a)
        path=self.service().outbox_path();pending=path.read_bytes()
        with self.assertRaises(sync.SyncError):self.scan(a,b)
        self.assertEqual(path.read_bytes(),pending);self.assertEqual(len(self.buzz.writes),1)
        self.buzz.hidden.clear();self.scan(a,b)
        self.assertEqual(len(self.buzz.writes),2);self.assertEqual(self.buzz.writes[-1]['kind'],40003)
        self.assertEqual(len(self.latest()['refs']),2)
        ledger=json.loads(path.read_text());self.assertFalse(ledger['pending']);self.assertTrue(all(x.get('result_id') for x in ledger['acked']))

    def test_unknown_edit_cannot_drop_or_append_refs_until_original_proven(self):
        a,b,c=self.event(1,'one/v1'),self.event(2,'two/v2'),self.event(3,'three/v3')
        self.scan(a);self.buzz.fail='hidden'
        with self.assertRaises(sync.SyncError):self.scan(b)
        path=self.service().outbox_path();pending=path.read_bytes()
        with self.assertRaises(sync.SyncError):self.scan(c)
        self.assertEqual(path.read_bytes(),pending);self.assertEqual(len(self.buzz.writes),2)
        self.buzz.hidden.clear();self.scan(c)
        self.assertEqual(len(self.buzz.writes),3);self.assertEqual(len(self.latest()['refs']),3)

    def test_state_ack_precedes_outbox_ack_crash_is_idempotent(self):
        a=self.event(1,'one/v1');s=self.service();self.gitlab.event_list=[a]
        with mock.patch.object(s,'_ack_delivery',side_effect=OSError('crash after state commit')):
            with self.assertRaises(OSError):s._sync_top_level(fixtures.PID,self.gitlab.project(fixtures.PID),fixtures.SINCE,{'notified':{'instant':0}},False)
        self.assertEqual(len(list(self.path.glob('gitlab-tag-batch-*.json'))),1)
        self.scan(a);self.assertEqual(len(self.buzz.writes),1)

    def test_prior_acked_individual_tags_not_migrated_or_replayed(self):
        a=self.event(1,'one/v1');record=sync.record_from_event(a,fixtures.PID,fixtures.WEB)
        self.buzz.send(sync.render_record(record));before=len(self.buzz.writes)
        self.scan(a);self.assertEqual(len(self.buzz.writes),before)
        self.assertFalse(list(self.path.glob('gitlab-tag-batch-*.json')))

    def test_different_commit_delete_move_and_missing_sha_never_merge(self):
        self.scan(self.event(1,'one/v1'),self.event(2,'two/v2',sha='b'*40),self.event(3,'gone/v1',action='deleted'),self.event(4,'move/v1',action='pushed to'),self.event(5,'short/v1',sha='aaaa'))
        self.assertEqual(len(self.buzz.writes),5)
        self.assertEqual(sum(batches.parse_snapshot(e['content'],sync.parse_header) is not None for e in self.buzz.events),2)

    def test_changed_publisher_does_not_adopt_another_publishers_batch(self):
        self.scan(self.event(1,'one/v1'))
        service=self.service();service.publisher=mapped.base.AGENT_PK
        helper=batches.TagBatches(service,vars(sync));scope=helper._scope(fixtures.PID,SHA)
        self.assertIsNone(helper._recover(scope,helper._history()))

    def test_lost_ledger_with_duplicate_roots_blocks_without_new_effect(self):
        self.scan(self.event(1,'one/v1'));first=copy.deepcopy(self.buzz.events[0]);self.buzz.send(first['content'])
        for path in self.path.glob('gitlab-tag-batch-*.json'):path.unlink()
        with self.assertRaises(sync.SyncError):self.scan(self.event(2,'two/v2'))
        self.assertEqual(len(self.buzz.writes),2)

    def test_invalid_signature_and_missing_history_fail_closed(self):
        self.scan(self.event(1,'one/v1'));self.buzz.events[0]['sig']='00'*64
        with self.assertRaises(sync.SyncError):self.scan(self.event(2,'two/v2'))
        self.assertEqual(len(self.buzz.writes),1)
        self.buzz.hidden.add(self.buzz.events[0]['id'])
        with self.assertRaises(sync.SyncError):self.scan(self.event(2,'two/v2'))
        self.assertEqual(len(self.buzz.writes),1)


    def test_actual_run_retains_commit_across_cursor_advance_and_restart(self):
        self.gitlab.event_list=[self.event(1,'one/v1')]
        self.service().run();root=self.buzz.events[0]['id']
        new=self.event(2,'two/v2');new['created_at']='2026-09-13T04:30:00Z'
        self.gitlab.event_list=[new];self.gitlab.scan_time=lambda:'2026-09-13T05:00:00Z'
        self.service().run()
        self.assertEqual(len(self.buzz.writes),2);self.assertEqual(sync.edit_target(self.buzz.events[-1]),root)
        self.assertEqual(self.latest()['source_keys'],['event-1','event-2'])
        self.service().run();self.assertEqual(len(self.buzz.writes),2)

    def test_project_channel_and_publisher_scope_each_get_their_own_root(self):
        a=self.event(1,'one/v1');self.scan(a)
        self.cfg['gitlab']['projects'].append(482)
        self.gitlab.event_list=[self.event(2,'two/v2')];service=self.service();service._project_visibilities[482]='public'
        service._sync_top_level(482,self.gitlab.project(482),fixtures.SINCE,{'notified':{'instant':0}},False)
        self.cfg['channel_id']='00000000-0000-4000-8000-0000000000c2';self.buzz.channel=self.cfg['channel_id']
        self.scan(a)
        self.buzz.key=mapped.base.AGENT2_KEY;self.cfg['publisher_pubkey']=mapped.base.FGS._signer_pubkey(self.buzz.key)
        self.scan(a)
        self.assertEqual(len(self.buzz.writes),4);self.assertTrue(all(e['kind']==9 for e in self.buzz.writes))
        self.assertEqual(len(list(self.path.glob('gitlab-tag-batch-*.json'))),4)

    def test_regressing_revision_and_incomplete_history_never_overwrite_facts(self):
        self.scan(self.event(1,'one/v1'));root=self.buzz.events[0]['id'];self.scan(self.event(2,'two/v2'))
        replacement=copy.deepcopy(self.latest());replacement['revision']+=1;replacement['refs']=replacement['refs'][:1]
        self.buzz.edit(root,batches.render_snapshot(replacement))
        with self.assertRaises(sync.SyncError):self.scan(self.event(3,'three/v3'))
        self.assertEqual(len(self.buzz.writes),3)
        with mock.patch.object(self.buzz,'channel_messages',side_effect=sync.SyncError('incomplete history')):
            with self.assertRaises(sync.SyncError):self.scan(self.event(4,'four/v4'))
        self.assertEqual(len(self.buzz.writes),3)

    def test_signed_snapshot_display_hides_only_exact_machine_metadata(self):
        self.scan(self.event(1,'one/v1'),self.event(2,'two/v2'))
        content=self.buzz.events[0]['content'];display=mapped.dm._gitlab_card_body(content)
        self.assertIn('one/v1',display);self.assertIn('two/v2',display)
        self.assertNotIn('[gitlab-',display);self.assertEqual(display.count('same commit'),1)
        bad=content.replace('[commit:'+SHA+']','[commit:short]')
        self.assertEqual(mapped.dm._gitlab_card_body(bad),bad)


    def test_unexpected_signed_mentions_cannot_satisfy_pending_snapshot(self):
        self.buzz.fail='hidden'
        with self.assertRaises(sync.SyncError):self.scan(self.event(1,'one/v1'))
        original=self.buzz.events[0]
        changed=mapped.base.FGS.sign_event(KEY,9,[*original['tags'],['p',mapped.base.AGENT2_PK]],original['content'],original['created_at'])
        self.buzz.events=[changed];self.buzz.hidden.clear()
        with self.assertRaises(sync.SyncError):self.scan(self.event(1,'one/v1'))
        self.assertEqual(len(self.buzz.writes),1)
        self.assertEqual(len(self.service()._read_outbox()['pending']),1)


    def test_unattempted_outbox_crash_starts_exact_snapshot_before_new_refs(self):
        service=self.service();self.gitlab.event_list=[self.event(1,'one/v1')]
        with mock.patch.object(service,'_mark_delivery_attempted',side_effect=OSError('crash before dispatch')):
            with self.assertRaises(OSError):service._sync_top_level(fixtures.PID,self.gitlab.project(fixtures.PID),fixtures.SINCE,{'notified':{'instant':0}},False)
        self.assertEqual(len(self.buzz.writes),0)
        self.assertIs(self.service()._read_outbox()['pending'][0]['attempted'],False)
        self.scan(self.event(2,'two/v2'))
        self.assertEqual([e['kind'] for e in self.buzz.writes],[9,40003])
        self.assertEqual(len(batches.parse_snapshot(self.buzz.writes[0]['content'],sync.parse_header)['refs']),1)
        self.assertEqual(len(self.latest()['refs']),2)

    def test_unattempted_definite_rejection_keeps_no_unknown_operation(self):
        service=self.service();self.gitlab.event_list=[self.event(1,'one/v1')]
        with mock.patch.object(service,'_mark_delivery_attempted',side_effect=OSError('crash before dispatch')):
            with self.assertRaises(OSError):service._sync_top_level(fixtures.PID,self.gitlab.project(fixtures.PID),fixtures.SINCE,{'notified':{'instant':0}},False)
        with mock.patch.object(self.buzz,'send',side_effect=sync.BuzzSendRejected('explicit zero write')):
            with self.assertRaises(sync.BuzzSendRejected):self.scan(self.event(1,'one/v1'))
        self.assertFalse(self.service()._read_outbox()['pending']);self.assertFalse(self.buzz.writes)
        self.scan(self.event(1,'one/v1'));self.assertEqual(len(self.buzz.writes),1)

    def test_foreign_scoped_pending_cannot_dispatch(self):
        service=self.service();self.gitlab.event_list=[self.event(1,'one/v1')]
        with mock.patch.object(service,'_mark_delivery_attempted',side_effect=OSError('crash')):
            with self.assertRaises(OSError):service._sync_top_level(fixtures.PID,self.gitlab.project(fixtures.PID),fixtures.SINCE,{'notified':{'instant':0}},False)
        ledger=service._read_outbox();item=ledger['pending'][0]
        item['payload']['tag_batch']['scope']['publisher']=mapped.base.AGENT2_PK
        item['change_id']=sync.delivery_change_id(service.channel,item['kind'],item['payload']);service._write_outbox(ledger)
        with self.assertRaises(sync.SyncError):self.scan(self.event(1,'one/v1'))
        self.assertFalse(self.buzz.writes)


    def test_complete_discovery_once_per_sync_known_groups_use_exact_readback(self):
        with mock.patch.object(self.buzz,'channel_messages',wraps=self.buzz.channel_messages) as scan:
            self.scan(self.event(1,'one/v1'),self.event(2,'two/v2',sha='b'*40))
        # One ordinary window scan plus one complete aggregation discovery,
        # independent of the number of groups and successful write readbacks.
        self.assertEqual(sum(call.args==(0,) for call in scan.call_args_list),1)
        with mock.patch.object(self.buzz,'channel_messages',wraps=self.buzz.channel_messages) as scan, mock.patch.object(self.buzz,'thread',wraps=self.buzz.thread) as exact:
            self.scan(self.event(3,'three/v3'))
        self.assertFalse(any(call.args==(0,) for call in scan.call_args_list))
        self.assertEqual(exact.call_count,2)

    def test_same_source_key_in_other_project_does_not_suppress_aggregate(self):
        self.scan(self.event(1,'one/v1'));self.cfg['gitlab']['projects'].append(482)
        self.gitlab.event_list=[self.event(1,'other/v2')];service=self.service();service._project_visibilities[482]='public'
        service._sync_top_level(482,self.gitlab.project(482),fixtures.SINCE,{'notified':{'instant':0}},False)
        self.assertEqual(len(self.buzz.writes),2)
        self.assertEqual(self.latest()['project'],482)

    def test_same_second_revision_waits_without_cursor_or_outbox_mutation(self):
        fixed=datetime.datetime.now(datetime.timezone.utc)
        self.buzz.now=int(fixed.timestamp())-1
        self.scan(self.event(1,'one/v1'));outbox=self.service().outbox_path().read_bytes()
        with mock.patch.object(sync.dt,'datetime',wraps=datetime.datetime) as clock:
            clock.now.return_value=fixed
            with self.assertRaises(sync.SyncError):self.scan(self.event(2,'two/v2'))
        self.assertEqual(len(self.buzz.writes),1);self.assertEqual(self.service().outbox_path().read_bytes(),outbox)

    def test_existing_legacy_ack_stays_excluded_after_group_anchor_is_saved(self):
        old=self.event(1,'old/v1')
        self.buzz.send(sync.render_record(sync.record_from_event(old,fixtures.PID,fixtures.WEB)))
        self.scan(old,self.event(2,'new/v2'))
        self.scan(old,self.event(3,'new/v3'))
        self.assertEqual(self.latest()['source_keys'],['event-2','event-3'])
        self.assertEqual(len(self.buzz.writes),3)

    def test_incomplete_discovery_is_not_absence_and_cannot_create_root(self):
        with mock.patch.object(self.buzz,'channel_messages',side_effect=sync.SyncError('page cap reached')):
            with self.assertRaises(sync.SyncError):self.scan(self.event(1,'one/v1'))
        self.assertFalse(self.buzz.writes)
        self.assertFalse(self.service()._read_outbox()['pending'])

    def test_unattempted_invalid_initial_revision_cannot_dispatch(self):
        service=self.service();self.gitlab.event_list=[self.event(1,'one/v1')]
        with mock.patch.object(service,'_mark_delivery_attempted',side_effect=OSError('crash')):
            with self.assertRaises(OSError):service._sync_top_level(fixtures.PID,self.gitlab.project(fixtures.PID),fixtures.SINCE,{'notified':{'instant':0}},False)
        ledger=service._read_outbox();item=ledger['pending'][0]
        item['payload']['tag_batch']['snapshot']['revision']=2
        item['payload']['content']=batches.render_snapshot(item['payload']['tag_batch']['snapshot'])
        item['change_id']=sync.delivery_change_id(service.channel,item['kind'],item['payload']);service._write_outbox(ledger)
        pending=service.outbox_path().read_bytes()
        with self.assertRaises(sync.SyncError):self.scan(self.event(1,'one/v1'))
        self.assertFalse(self.buzz.writes)
        self.assertEqual(service.outbox_path().read_bytes(),pending)

    def test_unknown_root_survives_project_list_change_before_new_refs(self):
        a,b=self.event(1,'one/v1'),self.event(2,'two/v2');self.buzz.fail='hidden'
        with self.assertRaises(sync.SyncError):self.scan(a)
        oldpath=self.service().outbox_path();pending=oldpath.read_bytes()
        self.cfg['gitlab']['projects'].append(482)
        with self.assertRaises(sync.SyncError):self.scan(b)
        self.assertEqual(len(self.buzz.writes),1);self.assertEqual(oldpath.read_bytes(),pending)
        self.buzz.hidden.clear();self.scan(b)
        self.assertEqual([e['kind'] for e in self.buzz.writes],[9,40003])
        self.assertEqual(self.latest()['source_keys'],['event-1','event-2'])
        self.assertFalse(json.loads(oldpath.read_text())['pending'])

    def test_unknown_edit_survives_project_list_shrink(self):
        self.cfg['gitlab']['projects'].append(482)
        self.scan(self.event(1,'one/v1'));self.buzz.fail='hidden'
        with self.assertRaises(sync.SyncError):self.scan(self.event(2,'two/v2'))
        oldpath=self.service().outbox_path();pending=oldpath.read_bytes();self.cfg['gitlab']['projects'].remove(482)
        with self.assertRaises(sync.SyncError):self.scan(self.event(3,'three/v3'))
        self.assertEqual(len(self.buzz.writes),2);self.assertEqual(oldpath.read_bytes(),pending)
        self.buzz.hidden.clear();self.scan(self.event(3,'three/v3'))
        self.assertEqual(len(self.latest()['refs']),3);self.assertEqual(len(self.buzz.writes),3)

    def test_prepared_outbox_scope_change_starts_old_snapshot_once(self):
        service=self.service();self.gitlab.event_list=[self.event(1,'one/v1')]
        with mock.patch.object(service,'_mark_delivery_attempted',side_effect=OSError('crash')):
            with self.assertRaises(OSError):service._sync_top_level(fixtures.PID,self.gitlab.project(fixtures.PID),fixtures.SINCE,{'notified':{'instant':0}},False)
        oldpath=service.outbox_path();self.cfg['gitlab']['projects'].append(482)
        self.scan(self.event(2,'two/v2'))
        self.assertEqual([e['kind'] for e in self.buzz.writes],[9,40003])
        self.assertEqual(batches.parse_snapshot(self.buzz.writes[0]['content'],sync.parse_header)['source_keys'],['event-1'])
        self.assertFalse(json.loads(oldpath.read_text())['pending'])

    def test_conflicting_origin_outboxes_fail_before_dispatch_and_keep_paths(self):
        service=self.service();self.gitlab.event_list=[self.event(1,'one/v1')]
        with mock.patch.object(service,'_mark_delivery_attempted',side_effect=OSError('crash')):
            with self.assertRaises(OSError):service._sync_top_level(fixtures.PID,self.gitlab.project(fixtures.PID),fixtures.SINCE,{'notified':{'instant':0}},False)
        oldpath=service.outbox_path();pending=oldpath.read_bytes();self.cfg['gitlab']['projects'].append(482)
        service=self.service();newpath=service.outbox_path();newpath.write_bytes(pending)
        with self.assertRaises(sync.SyncError):service._reconcile_pending()
        self.assertEqual(service.outbox_path(),newpath);self.assertFalse(self.buzz.writes)
        self.assertEqual(oldpath.read_bytes(),pending);self.assertEqual(newpath.read_bytes(),pending)

    def test_malformed_prior_scope_is_not_treated_as_absent(self):
        self.buzz.fail='hidden'
        with self.assertRaises(sync.SyncError):self.scan(self.event(1,'one/v1'))
        oldpath=self.service().outbox_path();ledger=json.loads(oldpath.read_text())
        ledger['pending'][0]['payload']['tag_batch']['scope']['unexpected']=True
        oldpath.write_text(json.dumps(ledger));pending=oldpath.read_bytes();self.cfg['gitlab']['projects'].append(482)
        with self.assertRaises(sync.SyncError):self.scan(self.event(2,'two/v2'))
        self.assertEqual(len(self.buzz.writes),1);self.assertEqual(oldpath.read_bytes(),pending)

    def test_foreign_publisher_prior_unknown_is_not_acknowledged(self):
        self.buzz.fail='hidden'
        with self.assertRaises(sync.SyncError):self.scan(self.event(1,'one/v1'))
        oldpath=self.service().outbox_path();pending=oldpath.read_bytes();self.cfg['gitlab']['projects'].append(482)
        self.buzz.key=mapped.base.AGENT2_KEY;self.cfg['publisher_pubkey']=mapped.base.FGS._signer_pubkey(self.buzz.key)
        self.scan(self.event(2,'two/v2'))
        self.assertEqual(oldpath.read_bytes(),pending);self.assertEqual(len(self.buzz.writes),2)
        self.assertEqual(self.buzz.writes[-1]['kind'],9)

    def test_failed_prior_recovery_restores_current_outbox_binding(self):
        self.buzz.fail='hidden'
        with self.assertRaises(sync.SyncError):self.scan(self.event(1,'one/v1'))
        self.cfg['gitlab']['projects'].append(482);service=self.service();path=service.outbox_path()
        self.gitlab.event_list=[self.event(2,'two/v2')]
        with self.assertRaises(sync.SyncError):service._sync_top_level(fixtures.PID,self.gitlab.project(fixtures.PID),fixtures.SINCE,{'notified':{'instant':0}},False)
        self.assertEqual(service.outbox_path(),path);self.assertNotIn('outbox_path',service.__dict__)
        self.assertEqual(len(self.buzz.writes),1)

if __name__=='__main__':unittest.main()
