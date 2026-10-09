import sys
from pathlib import Path
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from core import Store
from test_wire import FakeLark
from test_bridge import FakeCodex
from wire import Wire
from daemon import sync_bound, disconnect


class DaemonLifecycle(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(Path(self.tmp.name)/'db');self.lark=FakeLark();self.wire=Wire(self.store,self.lark);self.codex=FakeCodex()
        self.meta={'id':'s','status':{'type':'idle'}}
        self.store.bind('s','app','ou_owner','chat','root')
        self.store.enqueue('s','im:owner',{'kind':'owner_message','body':'continue','author':'ou_owner','message_id':'om_owner'})
    def tearDown(self):self.store.close();self.tmp.cleanup()
    def test_unknown_output_never_blocks_owner_input(self):
        original=self.lark.call
        def fail(args,identity='user',stdin=None):
            if args[:2]==['im','+messages-reply']:raise RuntimeError('send timeout')
            return original(args,identity,stdin)
        self.lark.call=fail
        turns=[{'status':'completed','items':[{'id':'output','type':'agentMessage','text':'progress'}]}]
        for _ in range(2):sync_bound(self.store,self.wire,self.codex,self.meta,turns,[],self.lark,{})
        self.assertEqual(self.store.inbox('s'),[])
        self.assertEqual(len(self.codex.entries),1)
        self.assertTrue(any(m=='thread/queue/start' for m,p in self.codex.calls))
        self.assertEqual(self.store.db.execute('SELECT status FROM outgoing').fetchone()[0],'attempting')
    def test_disconnect_requires_new_wire_for_same_loaded_sessions(self):
        class Client:
            closed=False
            def close(self):self.closed=True
        client=Client();clients={'home':client};present={'home':{'s'}}
        disconnect('home',clients,present)
        self.assertNotIn('home',clients);self.assertEqual(present.get('home',set()),set());self.assertTrue(client.closed)


if __name__=='__main__':unittest.main()
