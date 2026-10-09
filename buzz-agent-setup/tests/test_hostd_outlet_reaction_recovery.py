"""Real own-bot reaction recovery; only API/subprocess transport is offline."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest
from urllib.parse import parse_qs, urlsplit, unquote

TESTS=Path(__file__).resolve().parent
sys.path[:0]=[str(TESTS),str(TESTS.parent/'scripts'),str(TESTS.parent/'scripts'/'hostd')]
import test_hostd_outlet as fixture
import test_hostd_http_wiring as http_fixture
from bot_clients import BotLarkCli
from outlet import AgentOutlet
from store import StoreError
base=fixture.base
setUpModule=fixture.setUpModule
tearDownModule=fixture.tearDownModule

class Recovery(base.TmpCase):
    def assembly(self):
        world,run,adapter=fixture.OwnOutlet.assembly(self)
        self.world,self.run,self.adapter=world,run,adapter
        self.root_event=fixture.OwnOutlet.event(self,created=int(base.NOW.timestamp())-5)
        world.events.append(self.root_event);world.relay_events.append(self.root_event)
        self.target=adapter.deliver(self.root_event)
        self.reaction=fixture.OwnOutlet.event(self,kind=7,tags=[['e',self.root_event['id']]],content='✅')
        world.events.append(self.reaction);world.relay_events.append(self.reaction)
        self.lose='';self.read_override=None;self.reads=[];self.mutations=[]
        runner=adapter.client.runner
        def transport(argv,**kwargs):
            path=argv[4] if argv[2:4]==['api','GET'] else ''
            if path.endswith('/reactions'):
                self.assertEqual(argv[argv.index('--profile')+1],fixture.APP)
                self.assertEqual(argv[argv.index('--as')+1],'bot')
                params=json.loads(argv[argv.index('--params')+1]);self.reads.append((path,params))
                rows=[{'reaction_id':r['id'],'reaction_type':{'emoji_type':r['emoji']},'operator':{'operator_type':'app','operator_id':r['app']}}
                      for r in world.reactions if r['message_id']==unquote(path.split('/')[-2]) and r['emoji']==params['reaction_type']]
                data=self.read_override(params,rows) if self.read_override else {'items':rows,'has_more':False}
                if isinstance(data,Exception):raise data
                return subprocess.CompletedProcess(argv,0,json.dumps({'ok':True,'identity':'bot','data':data}),'')
            operation='create' if argv[2:5]==['im','reactions','create'] else 'delete' if argv[2:4]==['api','DELETE'] and '/reactions/' in argv[4] else ''
            if operation:self.mutations.append(operation)
            result=runner(argv,**kwargs)
            if operation and self.lose==operation:
                self.lose=''
                return subprocess.CompletedProcess(argv,1,json.dumps({'ok':False,'error':{'type':'network'}}),'')
            return result
        adapter.client.runner=transport
        return world,run,adapter
    def row(self,event):return self.run.mapping_store.delivery_by_source('test',event['id'],'r2f',agent_id=base.AGENT2_PK)
    def lost_create(self):
        self.lose='create'
        with self.assertRaises(base.FGS.GroupSyncError):self.adapter.deliver(self.reaction)
    def deletion(self):
        event=fixture.OwnOutlet.event(self,kind=5,tags=[['e',self.reaction['id']]],content='')
        self.world.events.append(event);self.world.relay_events.append(event);return event
    def test_lost_create_restart_adopts_exact_own_receipt_without_second_post(self):
        world,run,adapter=self.assembly();self.lost_create()
        receipt=world.reactions[0]['id']
        restarted=AgentOutlet(run,base.AGENT2_PK,self.tmp/'outlet.env',bot_client=adapter.client,trusted_relays={'https://relay.test'},http=world.http_get,clock=lambda:base.NOW)
        self.assertEqual(restarted.deliver(self.reaction),self.target)
        self.assertEqual(self.mutations,['create']);self.assertTrue(self.reads)
        row=self.row(self.reaction);self.assertEqual(row['status'],'acked')
        self.assertEqual(run.mapping_store.outlet_receipt(row['id'])['reaction_id'],receipt)

    def test_native_reaction_without_h_requires_signed_target_in_bound_channel(self):
        world,run,adapter=self.assembly()
        event=base.FGS.sign_event(base.AGENT2_KEY,7,[['e',self.root_event['id']]],'👍',run.now_ts)
        self.assertEqual(adapter.deliver(event),self.target)
        self.assertEqual(self.row(event)['status'],'acked')
        self.assertEqual(self.mutations,['create'])
        foreign=base.FGS.sign_event(base.AGENT2_KEY,9,[['h','other-channel']],'foreign',run.now_ts)
        world.events.append(foreign);world.relay_events.append(foreign)
        for tags in ([['e',foreign['id']]], [['e','a'*64]],
                     [['e',self.root_event['id']],['h','other-channel']],
                     [['e',self.root_event['id']],['e']]):
            with self.subTest(tags=tags):
                bad=base.FGS.sign_event(base.AGENT2_KEY,7,tags,'👍',run.now_ts)
                with self.assertRaises(base.FGS.GroupSyncError):adapter.deliver(bad)
        self.assertEqual(self.mutations,['create'])
    def test_lost_create_complete_absence_stays_unknown_without_visibility_assumption(self):
        self.assembly();self.lost_create();self.world.reactions.clear()
        with self.assertRaises(base.FGS.GroupSyncError):self.adapter.deliver(self.reaction)
        self.assertEqual(self.mutations,['create']);self.assertTrue(self.reads)
        self.assertEqual(self.row(self.reaction)['status'],'unknown')
    def test_foreign_bot_or_human_receipt_cannot_ack_own_create(self):
        self.assembly();self.lost_create()
        ownrow=self.world.reactions[0]
        def foreign(params,rows):
            row=copy.deepcopy(rows[0]);row['operator']={'operator_type':'app','operator_id':'cli_foreign'}
            human=copy.deepcopy(row);human['reaction_id']='human-id';human['operator']={'operator_type':'user','operator_id':'ou_human'}
            return {'items':[row,human],'has_more':False}
        self.read_override=foreign
        with self.assertRaises(base.FGS.GroupSyncError):self.adapter.deliver(self.reaction)
        self.assertEqual(self.mutations,['create']);self.assertEqual(self.row(self.reaction)['status'],'unknown')
    def test_incomplete_duplicate_own_or_error_read_never_repeats_mutation(self):
        self.assembly();self.lost_create()
        for mode in ('partial','duplicate','error','malformed'):
            def response(params,rows,mode=mode):
                if mode=='error':return OSError('offline private response')
                if mode=='partial':return {'items':rows,'has_more':True,'page_token':'again'}
                if mode=='malformed':return {'items':rows,'has_more':1}
                second=copy.deepcopy(rows[0]);second['reaction_id']='second-own-id';return {'items':[rows[0],second],'has_more':False}
            self.read_override=response
            with self.subTest(mode=mode):
                with self.assertRaises(base.FGS.GroupSyncError) as caught:self.adapter.deliver(self.reaction)
                self.assertIn('怎么解决',str(caught.exception));self.assertIn('复制给 AI',str(caught.exception))
                self.assertNotIn('private',str(caught.exception));self.assertEqual(self.mutations,['create'])
                self.assertEqual(self.row(self.reaction)['status'],'unknown')
    def test_lost_delete_complete_absence_recovers_removal_without_second_delete(self):
        self.assembly();self.adapter.deliver(self.reaction);event=self.deletion();self.lose='delete'
        with self.assertRaises(base.FGS.GroupSyncError):self.adapter.deliver(event)
        self.assertFalse(self.world.reactions)
        self.assertEqual(self.adapter.deliver(event),self.target)
        self.assertEqual(self.mutations,['create','delete']);self.assertTrue(self.reads)
        self.assertEqual(self.row(event)['status'],'acked');self.assertEqual(self.row(self.reaction)['status'],'removed')
    def test_lost_delete_conflicting_or_incomplete_read_preserves_unknown(self):
        self.assembly();self.adapter.deliver(self.reaction);existing=copy.deepcopy(self.world.reactions[0]);event=self.deletion();self.lose='delete'
        with self.assertRaises(base.FGS.GroupSyncError):self.adapter.deliver(event)
        def response(params,rows):return {'items':[],'has_more':True,'page_token':'same'}
        self.read_override=response
        with self.assertRaises(base.FGS.GroupSyncError):self.adapter.deliver(event)
        self.assertEqual(self.mutations,['create','delete']);self.assertEqual(self.row(event)['status'],'unknown')
        self.world.reactions.append(existing);self.read_override=None
        with self.assertRaises(base.FGS.GroupSyncError):self.adapter.deliver(event)
        self.assertEqual(self.mutations,['create','delete']);self.assertEqual(self.row(event)['status'],'unknown')
    def test_foreign_reactor_claiming_known_delete_id_cannot_prove_absence(self):
        self.assembly();self.adapter.deliver(self.reaction);rid=self.world.reactions[0]['id'];event=self.deletion();self.lose='delete'
        with self.assertRaises(base.FGS.GroupSyncError):self.adapter.deliver(event)
        self.read_override=lambda params,rows:{'items':[{'reaction_id':rid,'operator':{'operator_type':'app','operator_id':'cli_foreign'},'reaction_type':{'emoji_type':'DONE'}}],'has_more':False}
        with self.assertRaises(base.FGS.GroupSyncError):self.adapter.deliver(event)
        self.assertEqual(self.row(event)['status'],'unknown');self.assertEqual(self.row(self.reaction)['status'],'acked')
        self.assertEqual(self.mutations,['create','delete'])
    def test_delete_receipt_transaction_failure_preserves_unknown_for_readback(self):
        self.assembly();self.adapter.deliver(self.reaction);event=self.deletion()
        self.run.mapping_store.conn.execute("CREATE TRIGGER stop_removal BEFORE UPDATE OF status ON delivery WHEN NEW.status='removed' BEGIN SELECT RAISE(ABORT,'offline rollback'); END")
        with self.assertRaises(StoreError):self.adapter.deliver(event)
        self.assertEqual(self.row(event)['status'],'unknown');self.assertEqual(self.row(self.reaction)['status'],'acked')
        self.assertEqual(self.mutations,['create','delete']);self.assertFalse(self.world.reactions)
        self.run.mapping_store.conn.execute('DROP TRIGGER stop_removal')
        self.assertEqual(self.adapter.deliver(event),self.target)
        self.assertEqual(self.mutations,['create','delete']);self.assertEqual(self.row(self.reaction)['status'],'removed')
    def test_preexisting_pending_reservation_cannot_be_treated_as_first_unsent_attempt(self):
        self.assembly();emoji='DONE';digest=__import__('hashlib').sha256(json.dumps([self.target,emoji,False],separators=(',',':')).encode()).hexdigest()
        self.run.mapping_store.reserve_delivery('test',self.reaction['id'],'r2f',agent_id=base.AGENT2_PK,source_at=self.reaction['created_at'],content_hash=digest,stream='relay',root_id=self.root_event['id'],now=int(base.NOW.timestamp()))
        with self.assertRaises(base.FGS.GroupSyncError):self.adapter.deliver(self.reaction)
        self.assertFalse(self.mutations);self.assertTrue(self.reads)
        self.assertEqual(self.row(self.reaction)['status'],'unknown')

@unittest.skipUnless(http_fixture.AESGCM, 'actual protected app-secret HTTP path requires cryptography')
class NativeReadback(unittest.TestCase):
    setUp=http_fixture.Wiring.setUp
    profile=http_fixture.Wiring.profile
    no_cli=http_fixture.Wiring.no_cli
    respond=http_fixture.Wiring.respond
    api_calls=http_fixture.Wiring.api_calls
    def item(self,rid='opaque',app='cli_one'):
        return {'reaction_id':rid,'operator':{'operator_type':'app','operator_id':app},'reaction_type':{'emoji_type':'Typing'}}
    def test_actual_pool_complete_pages_select_own_app_preserve_opaque_id(self):
        self.respond({'items':[self.item('foreign','cli_other')],'has_more':True,'page_token':'next'})
        rid=__import__('base64').urlsafe_b64encode(bytes(range(64))).decode()
        self.respond({'items':[self.item(rid)],'has_more':False})
        value=self.cli.own_reactions(http_fixture.MID,'Typing')
        self.assertTrue(value.complete);self.assertEqual(value.reaction_ids,(rid,));self.assertEqual(value.app_id,'cli_one')
        calls=self.api_calls();self.assertEqual([c['method'] for c in calls],['GET','GET'])
        self.assertEqual(parse_qs(urlsplit(calls[-1]['path']).query)['page_token'],['next'])
        self.assertTrue(all(c['path'].startswith('/open-apis/im/v1/messages/'+http_fixture.MID+'/reactions?') for c in calls))
    def test_actual_pool_repeated_token_malformed_operator_duplicate_id_not_complete(self):
        for mode in ('token','operator','id'):
            with self.subTest(mode=mode):
                if mode=='token':
                    self.respond({'items':[],'has_more':True,'page_token':'repeat'});self.respond({'items':[],'has_more':True,'page_token':'repeat'})
                elif mode=='operator':self.respond({'items':[dict(self.item(),operator={'operator_type':'app','operator_id':[]})],'has_more':False})
                else:self.respond({'items':[self.item(),self.item()],'has_more':False})
                self.assertFalse(self.cli.own_reactions(http_fixture.MID,'Typing').complete)

if __name__=='__main__':unittest.main()
