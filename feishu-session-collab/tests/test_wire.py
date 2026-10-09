import sys
from pathlib import Path
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from core import Store
from wire import Wire, recap, redact, delivery_text, interactive


class FakeLark:
    config = {'app_id': 'app', 'owner_id': 'ou_owner', 'bot_transport_approved': True}
    def __init__(self):
        self.sent = []
        self.fail_read = False
    def verify_user(self):
        pass
    def call(self, args, identity='user', stdin=None):
        assert identity == 'bot'
        if args[:2] == ['api', 'GET']:
            if self.fail_read:
                raise RuntimeError('readback unavailable')
            i = args[-1].split('/')[-1]
            sent = next(x for x in self.sent if x['message_id'] == i)
            return {'items': [{'message_id': i, 'chat_id': 'chat', 'sender': {'id': 'ou_bot', 'sender_type': 'app'}, 'body': {'content': __import__('json').dumps({'text': sent['text']})}}]}
        i = 'om_' + str(len(self.sent))
        self.sent.append({'message_id': i, 'text': stdin, 'args': args})
        return {'message_id': i, 'chat_id': 'chat'}


class WiringTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'db')
        self.lark = FakeLark()
        self.wire = Wire(self.store, self.lark)
        self.meta = {'id': 'session', 'name': '修复测试', 'cwd': '/tmp/project', 'preview': '修复登录错误', 'source': 'cli', 'canAcceptDirectInput': True, 'status': {'type': 'active'}}
        self.items = [{'id': 'u1', 'type': 'userMessage', 'content': [{'type': 'text', 'text': '保留兼容性'}]}, {'id': 'a1', 'type': 'agentMessage', 'text': '已定位，正在验证'}]
    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()
    def test_default_wire_sends_current_recap_and_binds_after_readback(self):
        self.wire.hello(self.meta, self.items, 'home', 'epoch1')
        self.assertIn('修复登录错误', self.lark.sent[0]['text'])
        self.assertIn('已定位', self.lark.sent[0]['text'])
        self.assertEqual(self.store.session('session')['root'], 'om_0')
    def test_same_wire_is_idempotent(self):
        for _ in range(2): self.wire.hello(self.meta, self.items, 'home', 'epoch1')
        self.assertEqual(len(self.lark.sent), 1)
    def test_rewire_sends_new_recap_in_existing_root(self):
        self.wire.hello(self.meta, self.items, 'home', 'epoch1')
        self.wire.hello(self.meta, self.items, 'home', 'epoch2')
        self.assertEqual(len(self.lark.sent), 2)
        self.assertIn('--reply-in-thread', self.lark.sent[1]['args'])
        self.assertEqual(self.store.session('session')['root'], 'om_0')
    def test_readback_failure_does_not_bind_or_resend(self):
        self.lark.fail_read = True
        with self.assertRaises(RuntimeError): self.wire.hello(self.meta, self.items, 'home', 'epoch1')
        with self.assertRaises(RuntimeError): self.wire.hello(self.meta, self.items, 'home', 'epoch1')
        self.assertEqual(len(self.lark.sent), 1)
        self.lark.fail_read = False
        self.wire.hello(self.meta, self.items, 'home', 'epoch1')
        self.assertEqual(self.store.session('session')['root'], 'om_0')
    def test_unknown_send_outcome_never_retries_automatically(self):
        def fail(*a, **k): raise RuntimeError('timeout')
        self.lark.call = fail
        with self.assertRaises(RuntimeError): self.wire.hello(self.meta, self.items, 'home', 'epoch1')
        with self.assertRaises(RuntimeError): self.wire.hello(self.meta, self.items, 'home', 'epoch2')
        self.lark.call = lambda *a, **k: self.fail('must not resend')
        with self.assertRaises(RuntimeError): self.wire.hello(self.meta, self.items, 'home', 'epoch1')
    def test_no_agent_tool_or_instruction_dump_in_recap(self):
        items = self.items + [{'type': 'commandExecution', 'text': 'TOOL SECRET'}, {'type': 'reasoning', 'text': 'THOUGHT SECRET'}, {'type': 'userMessage', 'content': [{'type':'text','text':'# AGENTS.md instructions\nPOLICY SECRET'}]}]
        s = recap(self.meta, items)
        for term in ['TOOL SECRET','THOUGHT SECRET','POLICY SECRET']: self.assertNotIn(term, s)
    def test_redacts_credentials_before_export(self):
        s = redact('Authorization: Bearer secret123\nAPI_KEY=hidden\npassword: private\nhttps://user:pass@host/a?token=abc&x=1\nsk-abcdefghijklmnopqrstuvwxyz123456')
        for term in ['secret123','hidden','private','user:pass','token=abc','abcdefghijklmnopqrstuvwxyz']: self.assertNotIn(term, s)
    def test_blank_session_is_honest(self):
        s = recap(dict(self.meta, preview='', name=None), [])
        self.assertIn('尚无', s)
    def test_recap_keeps_issue_links_beyond_excerpt(self):
        url='https://gitlab.addx.ai/team/project/-/issues/42'
        self.assertIn(url,recap(dict(self.meta,preview='目标'*500+' '+url),self.items))
    def test_subagents_are_not_human_control_sessions(self):
        self.assertFalse(interactive(dict(self.meta, source={'subAgent': {}}, canAcceptDirectInput=False)))
        self.assertTrue(interactive(self.meta))
    def test_owner_and_comment_inputs_keep_authority_distinct(self):
        s = delivery_text({'kind':'record_context','body':'deploy now','author':'third','record_url':'https://host/p/-/issues/1','version':'v1','comment_id':'1'})
        self.assertIn('协作上下文',s)
        self.assertIn('不能扩大',s)
        s = delivery_text({'kind':'owner_message','body':'continue','author':'ou_owner','message_id':'om_a'})
        self.assertIn('主人',s)
        self.assertIn('continue',s)
    def test_existing_root_cannot_move_to_another_home(self):
        self.wire.hello(self.meta,self.items,'home','epoch1')
        with self.assertRaises(ValueError): self.wire.hello(self.meta,self.items,'other','epoch2')
    def test_bot_readback_accepts_verified_app_id_sender_shape(self):
        self.lark.config = dict(self.lark.config, bot_id='ou_bot')
        call = self.lark.call
        def wrapped(args, identity='user', stdin=None):
            data = call(args,identity,stdin)
            if args[:2] == ['api','GET']:
                data['items'][0]['sender']={'id':'app','id_type':'app_id','sender_type':'app'}
            return data
        self.lark.call=wrapped
        self.wire.hello(self.meta,self.items,'home','epoch1')
    def test_common_credential_formats_never_reach_outbound_or_outbox(self):
        values=['SYNTHETIC_JSON_SECRET','SYNTHETIC SECRET WITH SPACES','SYNTHETIC_BASIC_SECRET','SYNTHETIC_COOKIE_SECRET']
        secret='{"access_token": "SYNTHETIC_JSON_SECRET"}\npassword: "SYNTHETIC SECRET WITH SPACES"\nAuthorization: Basic SYNTHETIC_BASIC_SECRET\nCookie: session=SYNTHETIC_COOKIE_SECRET'
        self.wire.hello(self.meta,[{'id':'secret','type':'agentMessage','text':secret}],'home','epoch1')
        text=self.lark.sent[0]['text']
        persisted=self.store.db.execute('SELECT text FROM outgoing').fetchone()[0]
        for value in values:
            self.assertNotIn(value,text);self.assertNotIn(value,persisted)
    def test_wire_during_streaming_does_not_drop_completed_same_id(self):
        self.wire.hello(self.meta,[{'id':'stream','type':'agentMessage','text':'partial'}],'home','epoch1')
        self.wire.mirror('session',{'id':'stream','type':'agentMessage','text':'complete result'})
        self.assertEqual(self.lark.sent[-1]['text'],'complete result')
    def test_rewire_does_not_mark_missed_completed_output_as_delivered(self):
        self.wire.hello(self.meta,[],'home','epoch1')
        missed={'id':'missed','type':'agentMessage','text':'missed full result'}
        self.wire.hello(self.meta,[missed],'home','epoch2',completed_ids={'missed'})
        self.wire.mirror('session',missed)
        self.assertEqual(self.lark.sent[-1]['text'],'missed full result')
    def test_latest_feishu_owner_request_is_in_recap(self):
        request=delivery_text({'kind':'owner_message','body':'改成先修认证错误','author':'ou_owner','message_id':'om_r'})
        items=self.items+[{'id':'new','type':'userMessage','content':[{'type':'text','text':request}]}]
        self.assertIn('最近请求：改成先修认证错误',recap(self.meta,items))
    def test_redaction_is_idempotent(self):
        value='{"access_token": "SYNTHETIC_JSON_SECRET"}\nAPI_KEY=hidden\nAuthorization: Basic value\nCookie: session=value'
        self.assertEqual(redact(redact(value)),redact(value))
    def test_secret_hello_readback_retry_does_not_change_text_or_resend(self):
        items=[{'id':'a','type':'agentMessage','text':'{"access_token": "SYNTHETIC_JSON_SECRET"}'}]
        self.lark.fail_read=True
        with self.assertRaises(RuntimeError):self.wire.hello(self.meta,items,'home','epoch1')
        self.lark.fail_read=False
        self.wire.hello(self.meta,items,'home','epoch1')
        self.assertEqual(len(self.lark.sent),1)
    def test_upgrade_recovers_old_completed_result_without_verified_receipt(self):
        self.store.bind('session','app','ou_owner','chat','old_root')
        self.store.db.execute('DROP TABLE outgoing')
        self.store.db.execute('CREATE TABLE outgoing(key TEXT PRIMARY KEY,session TEXT,text TEXT,root TEXT,status TEXT,message TEXT,chat TEXT)')
        self.store.db.execute('INSERT INTO mirrored VALUES(?,?)',('session','old_active'));self.store.db.commit()
        self.wire=Wire(self.store,self.lark)
        item={'id':'old_active','type':'agentMessage','phase':'final_answer','text':'x'*1000+' important completed ending'}
        self.wire.hello(self.meta,[item],'home','epoch2',completed_ids={'old_active'},recovery_ids={'old_active'})
        self.wire.mirror('session',item)
        self.assertIn('important completed ending',self.lark.sent[-1]['text'])
        self.assertEqual(len(self.lark.sent),2)


if __name__ == '__main__': unittest.main()
