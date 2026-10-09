import sys
from pathlib import Path
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from core import Store
from wire import Wire
from test_wire import FakeLark
from bridge import deliver, start_queued, public_items, recent_turns


class FakeCodex:
    def __init__(self): self.calls = []; self.entries = []; self.fail = False
    def call(self, method, params):
        self.calls.append((method, params))
        if method == 'thread/queue/list': return {'data':self.entries}
        if method == 'thread/queue/add':
            if self.fail: raise RuntimeError('timeout')
            queued = {'id':'q1', 'input':params['input'], 'clientUserMessageId':params['clientUserMessageId']}
            self.entries.append(queued)
            return {'queuedSubmission':queued}
        if method == 'thread/queue/start': return {'turn':{'id':'turn1'}}
        raise AssertionError(method)


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(Path(self.tmp.name)/'db')
        Wire(self.store, FakeLark())
        self.store.bind('s','app','ou_owner','chat','root')
        self.store.enqueue('s','im:test',{'kind':'owner_message','body':'continue','author':'ou_owner','message_id':'om_x'})
        self.entry=self.store.inbox('s')[0];self.codex=FakeCodex()
    def tearDown(self): self.store.close();self.tmp.cleanup()
    def test_ack_only_after_exact_queue_receipt(self):
        deliver(self.store,self.codex,'s',self.entry)
        self.assertEqual(self.store.inbox('s'),[])
        self.assertEqual(self.codex.entries[0]['clientUserMessageId'],'feishu-inbox-1')
    def test_delivery_retry_does_not_duplicate(self):
        deliver(self.store,self.codex,'s',self.entry)
        deliver(self.store,self.codex,'s',self.entry)
        self.assertEqual(len(self.codex.entries),1)
    def test_uncertain_queue_result_keeps_inbox_and_never_blindly_retries(self):
        self.codex.fail=True
        with self.assertRaises(RuntimeError): deliver(self.store,self.codex,'s',self.entry)
        self.codex.fail=False
        with self.assertRaises(RuntimeError): deliver(self.store,self.codex,'s',self.entry)
        self.assertEqual(len(self.store.inbox('s')),1)
        self.assertEqual(sum(m=='thread/queue/add' for m,p in self.codex.calls),1)
    def test_recovers_accepted_queue_from_readback(self):
        self.store.db.execute('INSERT INTO deliveries VALUES(?,?,?,?,?,?)',(1,'s','feishu-inbox-1','attempting','',0));self.store.db.commit()
        self.codex.entries=[{'id':'q1','clientUserMessageId':'feishu-inbox-1','input':[{'type':'text','text':'[飞书主人回复]\n来源消息：om_x\n主人身份已由本机路由器核验。按当前任务与既有授权处理：\ncontinue','text_elements':[]}]}]
        deliver(self.store,self.codex,'s',self.entry)
        self.assertEqual(self.store.inbox('s'),[])
    def test_mismatching_queue_receipt_never_ack(self):
        self.codex.call=lambda m,p:{'queuedSubmission':{'id':'q','clientUserMessageId':'different','input':[]}}
        with self.assertRaises(ValueError): deliver(self.store,self.codex,'s',self.entry)
        self.assertEqual(len(self.store.inbox('s')),1)
    def test_starts_idle_queue_without_new_session(self):
        deliver(self.store,self.codex,'s',self.entry)
        start_queued(self.store,self.codex,{'id':'s','status':{'type':'idle'}})
        self.assertEqual(self.codex.calls[-1][0],'thread/queue/start')
        self.assertEqual(self.codex.calls[-1][1]['threadId'],'s')
    def test_active_turn_not_interrupted(self):
        deliver(self.store,self.codex,'s',self.entry)
        start_queued(self.store,self.codex,{'id':'s','status':{'type':'active'}})
        self.assertFalse(any(m=='thread/queue/start' for m,p in self.codex.calls))
    def test_filters_non_public_items_and_orders_turns(self):
        turns=[{'items':[{'id':'b','type':'agentMessage','text':'new'},{'type':'reasoning','text':'private'}]}, {'items':[{'id':'a','type':'userMessage','content':[]}]}]
        self.assertEqual([i['id'] for i in public_items(turns)],['a','b'])
    def test_unmaterialized_blank_session_gets_empty_recap(self):
        def fail(*a,**k): raise RuntimeError('Codex API: thread s is not materialized yet; thread/turns/list is unavailable before first user message')
        self.codex.call=fail
        self.assertEqual(recent_turns(self.codex,{'id':'s','preview':''}),[])


if __name__=='__main__': unittest.main()
