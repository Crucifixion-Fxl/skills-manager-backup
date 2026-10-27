"""Real SQLite + fake GitLab transport: reviewer publication and crash recovery."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import gitlab_label_review as kernel
import gitlab_stage_review_worker as worker


class WorkerTest(unittest.TestCase):
    def setUp(self):
        self.request=dict(task_key='a'*64, reviewer_user_id=7, project_id=2048, issue_iid=22,
                          revision='v3',source_commit='b'*40,content_digest='c'*64)
        self.result=dict(task_key='a'*64,verdict='changes-requested',summary='缺少已核验的刀模尺寸',findings=[
            dict(severity='blocking',location='Brief / 尺寸',rule='尺寸应有来源',evidence='未提供刀模版本',action='请需求人补充已确认刀模链接')])
        self.notes=[]
        self.writes=0
        outer=self
        class Git:
            def notes(self,*args): return outer.notes
            def add_note(self,project,iid,body):
                outer.writes+=1
                outer.notes.append(dict(id=81,body=body,author={'id':7}))
                return 81
        self.git=Git()
    def test_publish_rejection_and_restart_dedup(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(worker,'current_request',return_value=self.request):
            path=Path(tmp)/'ledger'
            with kernel.Ledger(path) as ledger:
                result=worker.publish({},self.git,None,{},self.result,ledger)
                self.assertEqual(result['status'],'RECORDED')
            with kernel.Ledger(path) as ledger:
                self.assertEqual(worker.publish({},self.git,None,{},self.result,ledger)['status'],'ALREADY_RECORDED')
            self.assertEqual(self.writes,1)
            self.assertIn('建议修改',self.notes[0]['body'])
    def test_unknown_attempt_never_reposts_but_exact_owned_receipt_recovers(self):
        with tempfile.TemporaryDirectory() as tmp,kernel.Ledger(Path(tmp)/'ledger') as ledger,patch.object(worker,'current_request',return_value=self.request):
            ledger.claim(self.request)
            with self.assertRaises(worker.WorkerError): worker.publish({},self.git,None,{},self.result,ledger)
            self.assertEqual(self.writes,0)
            body=worker.result_body(self.request,self.result)
            self.notes.append(dict(id=81,body=body,author={'id':99}))
            with self.assertRaises(worker.WorkerError): worker.publish({},self.git,None,{},self.result,ledger)
            self.notes[0]['author']['id']=7
            self.assertEqual(worker.publish({},self.git,None,{},self.result,ledger)['status'],'RECORDED')
            self.assertEqual(self.writes,0)
    def test_stale_before_post_and_missing_readback_do_not_record(self):
        with tempfile.TemporaryDirectory() as tmp,kernel.Ledger(Path(tmp)/'ledger') as ledger:
            with patch.object(worker,'current_request',side_effect=[self.request,worker.WorkerError('stale')]):
                with self.assertRaises(worker.WorkerError): worker.publish({},self.git,None,{},self.result,ledger)
            self.assertEqual(self.writes,0)
            self.assertEqual(ledger.claim(self.request),'RECONCILE_PENDING')
    def test_post_race_records_superseded_without_changing_labels(self):
        with tempfile.TemporaryDirectory() as tmp,kernel.Ledger(Path(tmp)/'ledger') as ledger:
            changed={**self.request,'task_key':'d'*64,'revision':'v4'}
            with patch.object(worker,'current_request',side_effect=[self.request,self.request,worker.SupersededRequest(changed)]):
                self.assertEqual(worker.publish({},self.git,None,{},self.result,ledger)['status'],'SUPERSEDED')
            self.assertEqual(self.writes,1)
            self.assertIn('v3',self.notes[0]['body'])
            self.assertEqual(ledger.claim(self.request),'SUPERSEDED')

    def test_signed_envelope_current_route_gitlab_identity_and_canonical_binding(self):
        channel='d19bcdb9-60c4-4b62-bb78-10a306045690'
        root='d'*64
        request={**self.request,'scope':'brief'}
        policy={'reviewer_user_id':7}
        adapter={'publisher_pubkey':'c'*64,'channel_id':channel,'gitlab':{'bot_user_id':7,'bot_username':'reviewer'},'exclude':[]}
        config={'adapter':adapter,'desk_pubkey':'a'*64,'desk_user_id':9}
        event=dict(kind=9,pubkey='a'*64,tags=[['h',channel],['p','c'*64],['e',root,'','reply']],
                   content=worker.dispatch.request_marker(request))
        binding=dict(project_id=2048,object='issue',iid=22,channel_id=channel,root_event_id=root)
        self.notes.append(dict(id=4,author={'id':9},body=worker.sync.render_binding_note(binding,'buzz://thread')))
        self.git.request=lambda *args:({'id':7,'username':'reviewer'}, {})
        self.git.issue=lambda *args:dict(iid=22,labels=[],assignees=[],confidential=False)
        with patch.object(worker.sync,'verify_nostr_event_signature'),patch.object(worker.dispatch,'_policy',return_value=policy),patch.object(worker.dispatch,'load_route',return_value={'mention_pubkey':'c'*64}),patch.object(worker.dispatch,'read_request',return_value=(request,41)):
            self.assertEqual(worker.current_request(config,self.git,None,event),request)
            self.git.request=lambda *args:({'id':99,'username':'other'}, {})
            with self.assertRaises(worker.WorkerError): worker.current_request(config,self.git,None,event)
            self.git.request=lambda *args:({'id':7,'username':'reviewer'}, {})
            self.notes[0]['author']['id']=99
            with self.assertRaises(worker.WorkerError): worker.current_request(config,self.git,None,event)

    def test_pass_cannot_contain_blocker_or_impersonate_human_approval(self):
        for verdict in ('approved','print-ready','review-passed'):
            with self.subTest(verdict=verdict):
                with self.assertRaises(worker.WorkerError): worker.result_body(self.request,{**self.result,'verdict':verdict})
        good={**self.result,'verdict':'review-passed','findings':[]}
        self.assertIn('不代表人工批准',worker.result_body(self.request,good))
        forged={**self.result,'summary':'<!-- stage-review-result:v1 {} -->'}
        with self.assertRaises(worker.WorkerError): worker.result_body(self.request,forged)
    def test_published_comment_is_readable_and_keeps_machine_result(self):
        with tempfile.TemporaryDirectory() as tmp,kernel.Ledger(Path(tmp)/'ledger') as ledger,patch.object(worker,'current_request',return_value=self.request):
            worker.publish({},self.git,None,{},self.result,ledger)
        body=self.notes[0]['body']
        self.assertTrue(body.startswith('## 设计阶段审查：需要修改\n'))
        self.assertIn('### 需要修改 · Brief / 尺寸',body)
        self.assertIn('<summary>版本与检查范围</summary>',body)
        visible,details=body.split('<details>',1)
        self.assertNotIn(self.request['source_commit'],visible)
        self.assertIn(self.request['source_commit'],details)
        raw=body.split('<!-- stage-review-result:v1 ',1)[1].split(' -->',1)[0]
        self.assertEqual(json.loads(raw),{'task_key':self.request['task_key'],'verdict':'changes-requested'})

    def test_identity_binding_is_checked_before_any_claim(self):
        config={'adapter':{'publisher_pubkey':'c'*64,'channel_id':'channel'},'desk_pubkey':'a'*64}
        event=dict(kind=9,pubkey='a'*64,tags=[['h','channel'],['p','WRONG']],content='')
        with patch.object(worker.sync,'verify_nostr_event_signature'):
            with self.assertRaises(worker.WorkerError): worker.current_request(config,self.git,None,event)


if __name__=='__main__': unittest.main()
