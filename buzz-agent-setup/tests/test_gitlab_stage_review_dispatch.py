"""Opt-in shared Syncer stage-review dispatch contract (#359)."""
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
import tempfile
from unittest.mock import patch

SCRIPTS=Path(__file__).resolve().parents[1]/'scripts'
sys.path.insert(0,str(SCRIPTS))
import gitlab_stage_review_dispatch as d
import gitlab_buzz_sync as sync
import gitlab_buzz_route_reply as route

class DispatchTest(unittest.TestCase):
    def setUp(self):
        self.content=b'Brief v3: approved sources only'
        self.policy=dict(project_id=2048,scope='brief',request_value='review-ready',
            policy_revision='p3',reviewer_user_id=7,submitter_user_ids=[8],
            route_id='brief-review',source_prefix='packaging/')
        self.issue=dict(project_id=2048,iid=22,state='opened',confidential=False,labels=['brief::review-ready'],
                       title='Brief',description='',web_url='https://gitlab.example.test/marketing/repo/-/issues/22')
        self.source=dict(revision='v3',source_commit='b'*40,source_path='packaging/brief.md',
                         content_digest=hashlib.sha256(self.content).hexdigest())
        self.note=dict(id=31,author={'id':8},system=False,created_at='2026-10-24T00:00:00Z',
                       body='<!-- stage-review-submission:v1 '+json.dumps(self.source)+' -->')
        self.events=[dict(id=41,action='add',label={'name':'brief::review-ready'},user={'id':8},
                          created_at='2026-10-24T00:01:00Z')]
        class Git:
            def issue(_,project,iid): return self.issue
            def notes(_,project,iid): return [self.note]
            def paged(_,path,params): return self.events
            def request(_,method,path,params=None):
                return dict(encoding='base64',content=base64.b64encode(self.content).decode(),
                            file_path=self.source['source_path'],ref=self.source['source_commit']),{}
        self.git=Git()
    def read(self): return d.read_request(self.git,self.policy,'existing-channel',22)
    def test_structured_transition_and_exact_source_generate_request(self):
        request,event=self.read()
        self.assertEqual(event,41)
        self.assertEqual(request['submission_note_id'],31)
        self.assertEqual(request['content_digest'],self.source['content_digest'])
    def test_config_rejects_foreign_project_and_duplicate_stage(self):
        cfg={'gitlab':{'projects':[2048]},'stage_review':{'route_config':'/owner/route.json','policies':[self.policy]}}
        d.validate_config(cfg)
        cfg['stage_review']['policies']=[self.policy,self.policy]
        with self.assertRaises(d.StageError): d.validate_config(cfg)
        cfg['stage_review']['policies']=[{**self.policy,'project_id':99}]
        with self.assertRaises(d.StageError): d.validate_config(cfg)
    def test_removed_or_other_scope_does_not_request(self):
        self.events[-1]['action']='remove'
        with self.assertRaises(d.StageError): self.read()
        self.events[-1]['label']['name']='status::ready'
        with self.assertRaises(d.StageError): self.read()
    def test_untrusted_label_actor_rejected_even_with_trusted_note(self):
        self.events[-1]['user']['id']=99
        with self.assertRaises(d.StageError): self.read()
    def test_submission_after_request_requires_resubmission(self):
        self.note['created_at']='2026-10-24T00:02:00Z'
        with self.assertRaises(d.StageError): self.read()
    def test_repository_digest_and_path_escape_fail_closed(self):
        self.content=b'tampered'
        with self.assertRaises(d.StageError): self.read()
        self.source['source_path']='packaging/../private.txt'
        self.note['body']='<!-- stage-review-submission:v1 '+json.dumps(self.source)+' -->'
        with self.assertRaises(d.StageError): self.read()
    def test_new_label_toggle_same_source_keeps_task_identity(self):
        first,_=self.read()
        self.events.extend([{**self.events[0],'id':42,'action':'remove'}, {**self.events[0],'id':43}])
        second,event=self.read()
        self.assertEqual(first,second)
        self.assertEqual(event,43)
    def test_new_review_prefix_is_canvas_policy_only_not_legacy_fact_route(self):
        prefix=d.trigger_prefix(self.policy)
        roles={'reviewer':dict(mention='@content-reviewer',mention_pubkey='c'*64)}
        canvas='<!-- gitlab-buzz-routing:v1 -->\n| route_id | trigger_prefix | role | reason |\n| --- | --- | --- | --- |\n| brief-review | `'+prefix+'` | reviewer | brief-review |\n<!-- /gitlab-buzz-routing -->'
        parsed=route.parse_canvas_routes(canvas,roles)
        self.assertIn('brief-review',parsed)
        self.assertFalse(sync.matches_trigger_prefix(sync.render_message(sync.issue_fact(self.issue,2048),'activity'),prefix))
    def test_note_cannot_forge_dispatch_marker(self):
        line=d.MARKER+'{} -->'
        self.assertNotIn(d.MARKER,sync._sanitize_comment_line(line))
    def test_scope_replacement_removal_after_ready_still_queues(self):
        self.events.insert(0,{**self.events[0],'id':39,'label':{'name':'brief::draft'}})
        self.events.append({**self.events[0],'id':42,'action':'remove','label':{'name':'brief::draft'}})
        self.assertEqual(self.read()[1],41)
    def test_shared_sync_hook_is_opt_in_and_retains_existing_content_sync(self):
        obj=object.__new__(sync.Syncer)
        obj.config={}
        with patch.object(obj,'_sync_issue_content') as original, patch.object(d,'dispatch') as dispatch:
            obj._sync_issue(2048,self.issue,{},False)
            original.assert_called_once()
            dispatch.assert_not_called()
        obj.config={'stage_review':{'policies':[self.policy]}}
        with patch.object(obj,'_sync_issue_content') as original, patch.object(d,'dispatch') as dispatch:
            obj._sync_issue(2048,self.issue,{},False)
            original.assert_called_once()
            dispatch.assert_called_once()
    def make_syncer(self):
        channel='d19bcdb9-60c4-4b62-bb78-10a306045690'
        root='d'*64
        binding=dict(project_id=2048,object='issue',iid=22,channel_id=channel,root_event_id=root)
        binding_note=dict(id=32,author={'id':9},body=sync.render_binding_note(binding,'buzz://thread'))
        self.git.notes=lambda project,iid:[self.note,binding_note]
        messages=[]
        obj=SimpleNamespace(config={'stage_review':{'policies':[self.policy]},'exclude':[]},
            gitlab=self.git,channel=channel,publisher='a'*64,bot_user_id=9,
            _thread=lambda *args:messages)
        def send(content,reply_to,mentions,project_id):
            payload=dict(content=content,reply_to=reply_to,mentions=mentions,project_id=project_id)
            d.recheck_delivery(obj,payload)
            messages.append(dict(id='e'*64,pubkey=obj.publisher,content=content,
                tags=[['h',channel],['e',root,'','reply'],['p',mentions[0]]]))
            return 'e'*64
        obj._send_message=send
        return obj,messages
    def test_dispatch_uses_existing_thread_and_deduplicates_after_readback(self):
        obj,messages=self.make_syncer()
        with patch.object(d,'load_route',return_value={'mention_pubkey':'c'*64}),patch.object(sync,'verify_nostr_event_signature') as verify:
            summary={}
            d.dispatch(obj,2048,22,summary,False)
            d.dispatch(obj,2048,22,summary,False)
            self.assertEqual(len(messages),1)
            self.assertEqual([x['status'] for x in summary['stage_review']],['sent','already_sent'])
            self.assertEqual(sync.parse_header(messages[0]['content'])['issue'],22)
            verify.assert_called_once()
    def test_replay_rejects_replaced_source_and_revoked_recipient(self):
        obj,messages=self.make_syncer()
        with patch.object(d,'load_route',return_value={'mention_pubkey':'c'*64}):
            d.dispatch(obj,2048,22,{},False)
            payload=dict(content=messages[0]['content'],reply_to='d'*64,mentions=['c'*64],project_id=2048)
            self.source['revision']='v4'
            self.note['body']='<!-- stage-review-submission:v1 '+json.dumps(self.source)+' -->'
            with self.assertRaises(d.StageError): d.recheck_delivery(obj,payload)
            self.source['revision']='v3'
            self.note['body']='<!-- stage-review-submission:v1 '+json.dumps(self.source)+' -->'
        with patch.object(d,'load_route',return_value={'mention_pubkey':'f'*64}):
            with self.assertRaises(d.StageError): d.recheck_delivery(obj,payload)

    def test_source_mentions_are_neutralized_but_marker_roundtrips_and_dedups(self):
        self.source['source_path']='packaging/@content-creator nostr:npub1example NOSTR:npub1other.md'
        self.note['body']='<!-- stage-review-submission:v1 '+json.dumps(self.source)+' -->'
        obj,messages=self.make_syncer()
        with patch.object(d,'load_route',return_value={'mention_pubkey':'c'*64}),patch.object(sync,'verify_nostr_event_signature'):
            d.dispatch(obj,2048,22,{},False)
            content=messages[0]['content']
            self.assertNotIn('@content-creator',content)
            self.assertNotIn('nostr:npub',content.lower())
            request=json.loads(d.REQUEST.findall(content)[0])
            self.assertEqual(request['source_path'],self.source['source_path'])
            d.dispatch(obj,2048,22,{},False)
            self.assertEqual(len(messages),1)
        for control in ('\n','\r','\t','\x00'):
            self.source['source_path']='packaging/a'+control+'b.md'
            self.note['body']='<!-- stage-review-submission:v1 '+json.dumps(self.source)+' -->'
            with self.assertRaises(d.StageError): self.read()

    def test_unattempted_stale_or_disabled_request_cancels_durably_and_unblocks(self):
        for change in ('revision','disabled','recipient','attempted','transport'):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as folder:
                self.setUp()
                obj,messages=self.make_syncer()
                with patch.object(d,'load_route',return_value={'mention_pubkey':'c'*64}):
                    d.dispatch(obj,2048,22,{},False)
                payload=dict(content=messages[0]['content'],reply_to='d'*64,mentions=['c'*64],project_id=2048)
                worker=object.__new__(sync.Syncer)
                worker.__dict__.update(obj.__dict__)
                worker.outbox_path=lambda:Path(folder)/'outbox.json'
                worker._check_run_budget=lambda:None
                worker._check_outbox_start=lambda *args:None
                worker._project_visibilities={2048:'private'}
                worker.gitlab.project=lambda pid:dict(id=pid,visibility='private')
                sent=[]
                worker.buzz=SimpleNamespace(send=lambda content,**kwargs:sent.append(content) or 'f'*64)
                old=worker._queue_delivery('buzz_message',payload)
                if change=='attempted': worker._mark_delivery_attempted(old)
                worker._queue_delivery('buzz_message',dict(content='unrelated Issue or MR',project_id=2048))
                if change in ('revision','attempted'):
                    self.source['revision']='v4'
                    self.note['body']='<!-- stage-review-submission:v1 '+json.dumps(self.source)+' -->'
                elif change=='disabled': worker.config.pop('stage_review')
                if change=='transport':
                    worker.gitlab.issue=lambda *args: (_ for _ in ()).throw(sync.SyncError('temporary transport failure'))
                role='f'*64 if change=='recipient' else 'c'*64
                with patch.object(d,'load_route',return_value={'mention_pubkey':role}):
                    if change in ('attempted','transport'):
                        with patch.object(worker,'_pending_satisfied',return_value=False),patch.object(worker,'_isolatable_pipeline',return_value=None):
                            with self.assertRaises(sync.SyncError): worker._reconcile_pending()
                        self.assertEqual(len(worker._read_outbox()['pending']),2)
                        self.assertFalse(worker._read_outbox().get('cancelled'))
                        self.assertEqual(sent,[])
                        continue
                    worker._reconcile_pending()
                    # Reload persistent state, as a restart would, and ensure no repeated blockage.
                    worker._reconcile_pending()
                self.assertEqual(sent,['unrelated Issue or MR'])
                ledger=json.loads(worker.outbox_path().read_text())
                self.assertFalse(ledger['pending'])
                self.assertEqual(ledger['cancelled'][0]['change_id'],old)
                self.assertEqual(ledger['cancelled'][0]['status'],'CANCELLED')
                self.assertFalse(ledger['cancelled'][0]['attempted'])
                self.assertEqual(ledger['cancelled'][0]['payload'],payload)
                if change=='revision':
                    fresh,_=d.read_request(worker.gitlab,self.policy,worker.channel,22)
                    new_payload={**payload,'content':d.request_marker(fresh)}
                    worker._queue_delivery('buzz_message',new_payload)
                    with patch.object(d,'load_route',return_value={'mention_pubkey':'c'*64}):
                        worker._reconcile_pending()
                    self.assertEqual(sent[-1],new_payload['content'])
                    self.assertFalse(worker._read_outbox()['pending'])

    def test_replay_gate_rechecks_stage_payload(self):
        obj=object.__new__(sync.Syncer)
        obj.config={}
        obj._project_visibilities={2048:'private'}
        obj.gitlab=type('G',(),{'project':lambda _,pid:{}})()
        with patch.object(obj,'_project_visibility',return_value='private'),patch.object(obj,'_check_run_budget'),patch.object(d,'recheck_delivery') as gate:
            obj._check_delivery_gates('buzz_message',{'content':d.MARKER+'{} -->','project_id':2048})
            gate.assert_called_once()

if __name__=='__main__': unittest.main()
