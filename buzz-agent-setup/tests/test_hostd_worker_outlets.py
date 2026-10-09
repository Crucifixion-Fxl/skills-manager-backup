"""Real Worker/MappedRound/AgentOutlet; only temp files and low-level transports fake."""
import copy
import json
from datetime import timedelta
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

TESTS=Path(__file__).resolve().parent
sys.path[:0]=[str(TESTS),str(TESTS.parent/'scripts'),str(TESTS.parent/'scripts'/'hostd')]
import test_hostd_outlet as own
import binding_worker as bw
from state_store import StateAdapter
from store import Store
base=own.base
setUpModule=base.setUpModule
tearDownModule=base.tearDownModule

class WorkerOutlets(base.TmpCase):
    def test_cooperative_outlet_slice_keeps_cursor_and_schedules_without_error(self):
        world,run,w,_=self.assembly()
        events=[self.event(content='slice '+str(n)) for n in range(2)]
        for event in events:self.put(world,event)
        rep=w.run({'buzz'})
        self.assertEqual(rep['errors'],0)
        self.assertEqual(rep['hostd']['cooperative_phases'],['buzz'])
        self.assertIn('buzz',rep['hostd']['retry_phases'])
        self.assertEqual(len(self.sends(world)),1)
        rep=w.run({'buzz'})
        self.assertEqual(rep['errors'],0)
        self.assertEqual(rep['hostd']['cooperative_phases'],[])
        self.assertFalse(w.outlet_sweep_pending)
        self.assertEqual(len(self.sends(world)),2)

    def test_invalid_first_agent_does_not_starve_later_own_outlet(self):
        world,run,w,spec=self.assembly()
        bad='0'*64
        w.outlet_specs[bad]=SimpleNamespace(pubkey=bad,owner_pubkey=base.OWNER_PK,app_id='cli_invalid',status='blocked')
        event=self.event();self.put(world,event)
        first=w.run({'buzz'})
        self.assertFalse(self.sends(world))
        self.assertEqual(first['hostd']['outlet_results'][bad]['reason'],'spec_identity_invalid')
        second=w.run({'buzz'})
        self.assertEqual(self.own_record(run,event)['status'],'acked')
        self.assertEqual(len(self.sends(world)),1)

    def test_rescan_delivers_new_message_after_peer_keeps_failing(self):
        world,run,w,spec=self.assembly()
        bad='0'*64
        w.outlet_specs[bad]=SimpleNamespace(pubkey=bad,owner_pubkey=base.OWNER_PK,app_id='cli_invalid',status='blocked')
        first=self.event(content='first');self.put(world,first)
        w.run({'buzz'});w.run({'buzz'})
        self.assertNotIn(base.AGENT2_PK,w.outlet_sweep_pending)
        self.assertIn(bad,w.outlet_sweep_pending)
        second=self.event(content='new own feed');self.put(world,second)
        w.outlet_rescan=True
        w.run({'buzz'});w.run({'buzz'})
        self.assertEqual(self.own_record(run,second)['status'],'acked')
        self.assertEqual(len(self.sends(world)),2)

    def assembly(self,specs=True):
        world,run,adapter=own.OwnOutlet.assembly(self)
        path=self.tmp/'config.json';cfg=json.loads(path.read_text());cfg['agents'][base.AGENT2_PK]=dict(run.cfg['agents'][base.AGENT2_PK])
        base.write_owner_only(path,json.dumps(cfg))
        credential=self.tmp/'outlet.env'
        base.write_owner_only(credential,credential.read_text()+f'BUZZ_ACP_AGENT_OWNER={base.OWNER_PK}\nBUZZ_ACP_CHANNELS={base.CHANNEL}\n')
        spec=SimpleNamespace(pubkey=base.AGENT2_PK,owner_pubkey=base.OWNER_PK,app_id=own.APP,
            env_file=credential,lark_config_dir=run.clients.agents[own.APP].config_dir,
            lark_data_dir=run.clients.agents[own.APP].data_dir,channels=(base.CHANNEL,),status='own_bot_verified')
        run.state.binding=f'{base.CHANNEL}|{base.CHAT}'
        run.state.floor=int(base.NOW.timestamp())-1200;run.state.buzz_floor=int(base.NOW.timestamp())-1100
        run.state.buzz_since=run.state.feishu_since=run.state.react_since=run.state.floor
        run.persist()
        w=bw.Worker(path,self.tmp/'state',base_env={'HOME':str(self.tmp),'PATH':'/usr/bin'},
            store_path=self.tmp/'hostd.db',binding_id='test',client_factory=lambda cfg,env:run.clients,clock=lambda:base.NOW)
        # Set future API attributes after construction so baseline RED exercises
        # the actual old Worker rather than failing on an unsupported keyword.
        w.outlet_specs={base.AGENT2_PK:spec} if specs else {}
        w.trusted_relays={'https://relay.test'};w.http_pool=None
        world.bot_calls.clear();world.relay_queries.clear()
        return world,run,w,spec
    def event(self,*args,**kwargs):return own.OwnOutlet.event(self,*args,**kwargs)
    def put(self,world,event):world.events.append(event);world.relay_events.append(event)
    def sends(self,world):return [argv for argv,_ in world.bot_calls if '--content' in argv]
    def own_record(self,run,event,direction='b2f'):
        return run.mapping_store.delivery_by_source('test',event['id'],direction,agent_id=base.AGENT2_PK)
    def test_actual_worker_constructs_outlet_same_sql_and_own_signed_single_author_scan(self):
        world,run,w,_=self.assembly();event=self.event();self.put(world,event)
        rep=w.run({'buzz'})
        row=self.own_record(run,event);self.assertIsNotNone(row)
        self.assertEqual(row['status'],'acked');self.assertEqual(len(self.sends(world)),1)
        self.assertEqual(self.sends(world)[0][self.sends(world)[0].index('--profile')+1],own.APP)
        scans=[q for q in world.relay_queries if q['signer']==base.AGENT2_PK and any(f.get('kinds')==[9,7,5,40003] for f in q['filters'])]
        self.assertTrue(scans)
        f=scans[-1]['filters'][0];self.assertEqual(f['authors'],[base.AGENT2_PK]);self.assertEqual(f['#h'],[base.CHANNEL])
        self.assertEqual(f['since'],max(run.state.floor,run.state.buzz_floor))
        self.assertGreater(run.mapping_store.cursor_position('test','relay',agent_id=base.AGENT2_PK),0)
        self.assertEqual(rep['hostd']['outlet_results'][base.AGENT2_PK]['status'],'acked')
    def test_missing_ownspec_never_relays_bot_and_is_pending(self):
        world,run,w,_=self.assembly(specs=False);event=self.event();self.put(world,event)
        rep=w.run({'buzz'})
        self.assertFalse(self.sends(world));self.assertIsNone(self.own_record(run,event))
        self.assertGreater(rep['hostd']['outlet_pending'],0);self.assertGreater(rep['errors'],0)
        self.assertIn('buzz',rep['hostd']['retry_phases'])
    def test_human_still_sent_by_sync_bot_while_agent_requires_ownproof(self):
        world,run,w,_=self.assembly(specs=False)
        human=self.event(key=base.OWNER_KEY);agent=self.event(content='agent must wait');self.put(world,human);self.put(world,agent)
        rep=w.run({'buzz'});sends=self.sends(world);self.assertEqual(len(sends),1)
        self.assertEqual(sends[0][sends[0].index('--profile')+1],base.AGENT_APP)
        self.assertIn(human['id'],sends[0][sends[0].index('--content')+1]);self.assertNotIn(agent['id'],sends[0][sends[0].index('--content')+1])
        self.assertGreater(rep['hostd']['outlet_pending'],0)
    def test_human_reply_cannot_backfill_agent_root_through_shared_round(self):
        world,run,w,_=self.assembly(specs=False)
        root=self.event(created=int(base.NOW.timestamp())-5);self.put(world,root)
        reply=self.event(key=base.OWNER_KEY,tags=[['e',root['id'],'','root'],['e',root['id'],'','reply']]);self.put(world,reply)
        rep=w.run({'buzz'})
        self.assertFalse(any(root['id'] in argv[argv.index('--content')+1] for argv in self.sends(world)))
        self.assertIsNone(self.own_record(run,root));self.assertGreater(rep['errors'],0)
        self.assertIn('buzz',rep['hostd']['retry_phases'])
    def test_agent_root_is_caught_up_before_human_reply_can_hold_buzz_phase(self):
        world,run,w,_=self.assembly()
        root=self.event(created=int(base.NOW.timestamp())-5);self.put(world,root)
        reply=self.event(key=base.OWNER_KEY,tags=[['e',root['id'],'','root'],['e',root['id'],'','reply']]);self.put(world,reply)
        rep=w.run({'buzz'});row=self.own_record(run,root)
        self.assertIsNotNone(row);self.assertEqual(row['status'],'acked')
        self.assertEqual(rep['hostd']['outlet_results'][base.AGENT2_PK]['status'],'acked')
        self.assertEqual(len(self.sends(world)),2)
    def test_human_root_is_sent_before_bounded_second_pass_agent_reply(self):
        world,run,w,_=self.assembly()
        root=self.event(key=base.OWNER_KEY,created=int(base.NOW.timestamp())-5);self.put(world,root)
        reply=self.event(tags=[['e',root['id'],'','root'],['e',root['id'],'','reply']]);self.put(world,reply)
        rep=w.run({'buzz'});self.assertIsNone(self.own_record(run,reply))
        # The human root is now published, but the failed own candidate has
        # consumed this round's budget. Recover on its next durable retry turn.
        world.clock=base.NOW+timedelta(seconds=31);w.clock=lambda:world.clock
        rep=w.run({'buzz'});row=self.own_record(run,reply)
        self.assertIsNotNone(row);self.assertEqual(row['status'],'acked')
        self.assertEqual(rep['hostd']['outlet_results'][base.AGENT2_PK]['status'],'acked')
        self.assertEqual(len(self.sends(world)),2)
    def test_owner_env_or_live_allowlist_mismatch_fail_without_send_cursor(self):
        parent=self.tmp
        for kind in ['owner','allowlist','app','pubkey','blocked']:
            with self.subTest(kind=kind):
                # One fixture per subcase; no real HOME or external files.
                self.tmp=parent/kind;self.tmp.mkdir(mode=0o700)
                world,run,w,spec=self.assembly();event=self.event();self.put(world,event)
                if kind=='owner':spec.owner_pubkey='a'*64
                elif kind=='allowlist':base.write_owner_only(spec.env_file,spec.env_file.read_text().replace(base.CHANNEL,'00000000-0000-0000-0000-000000000099'))
                elif kind=='app':spec.app_id=base.AGENT_APP
                elif kind=='pubkey':spec.pubkey=base.AGENT_PK
                else:spec.status='blocked'
                rep=w.run({'buzz'});self.assertFalse(self.sends(world));self.assertIsNone(self.own_record(run,event))
                self.assertEqual(run.mapping_store.cursor_position('test','relay',agent_id=base.AGENT2_PK),0)
                self.assertGreater(rep['hostd']['outlet_pending'],0)

    def test_unknown_send_stays_unacked_on_restart_and_does_not_advance_agent_cursor(self):
        world,run,w,_=self.assembly();event=self.event();self.put(world,event);world.lark_send_fail=['timeout']
        rep=w.run({'buzz'});row=self.own_record(run,event)
        self.assertIsNotNone(row);self.assertEqual(row['status'],'pending')
        self.assertEqual(run.mapping_store.cursor_position('test','relay',agent_id=base.AGENT2_PK),0)
        self.assertGreater(rep['hostd']['outlet_pending'],0)
        # An unresolved operation is explicit and retried with its original
        # idempotency request, never a sync-bot fallback or fabricated ACK.
        before=len(self.sends(world));w.cached.clear()
        world.clock=base.NOW+timedelta(seconds=31);w.clock=lambda:world.clock
        second=w.run({'buzz'})
        row=self.own_record(run,event);self.assertEqual(row['status'],'acked');self.assertEqual(len(self.sends(world)),before+1)
        profiles=[argv[argv.index('--profile')+1] for argv in self.sends(world)];self.assertEqual(set(profiles),{own.APP})
        w.run({'buzz'});self.assertEqual(len(self.sends(world)),before+1)
    def test_fresh_policy_revocation_makes_outlet_pending_without_agent_cursor(self):
        world,run,w,_=self.assembly();event=self.event();self.put(world,event)
        world.relay_events.append(base.FGS.sign_event(base.OWNER_KEY,30177,[['d',base.AGENT2_PK]],json.dumps({'feishu':{'app_id':'cli_revoked'}}),int(base.NOW.timestamp())))
        rep=w.run({'buzz'});self.assertFalse(self.sends(world));self.assertIsNone(self.own_record(run,event))
        self.assertGreater(rep['hostd']['outlet_pending'],0);self.assertIn('buzz',rep['hostd']['retry_phases'])
    def test_agent_edit_is_own_outlet_delivery_not_shared_round_edit(self):
        world,run,w,_=self.assembly();event=self.event(created=int(base.NOW.timestamp())-5);self.put(world,event);w.run({'buzz'})
        edit=self.event(kind=40003,tags=[['e',event['id']]],content='edited own body');self.put(world,edit);w.run({'buzz'})
        row=self.own_record(run,edit,'e2f');self.assertIsNotNone(row);self.assertEqual(row['status'],'acked')
        self.assertTrue(world.message_updates);self.assertTrue(all(x['app']==own.APP for x in world.message_updates))
    def test_agent_reaction_is_own_outlet_receipt_and_legacy_table_not_modified(self):
        world,run,w,_=self.assembly();message=self.event(created=int(base.NOW.timestamp())-5);self.put(world,message);w.run({'buzz'})
        reaction=self.event(kind=7,tags=[['e',message['id']]],content='✅');self.put(world,reaction);rep=w.run({'buzz'})
        row=self.own_record(run,reaction,'r2f');self.assertIsNotNone(row);self.assertEqual(row['status'],'acked')
        receipt=run.mapping_store.outlet_receipt(row['id']);self.assertEqual(receipt['sender_app_id'],own.APP)
        with Store(self.tmp/'hostd.db') as store:
            state=StateAdapter(store,'test',self.tmp/'state').load();self.assertNotIn(reaction['id'],state.r2f)
    def authorize_dynamic(self, run, worker):
        db=run.mapping_store;now=int(base.NOW.timestamp());pub=base.AGENT2_PK
        db.register_agent(pub,owner_pubkey=base.OWNER_PK,app_id=own.APP,now=now)
        db.create_join('JOIN-aabbccdd',pub,base.OWNER_PK,own.APP,base.CHAT,kind='channel',binding_id='test',now=now)
        generation=db.rotate_card('JOIN-aabbccdd','om_approved',now=now)
        self.assertTrue(db.decide_join('JOIN-aabbccdd','evt_approved',base.OWNER_PK,own.APP,'om_approved',generation,approved=True,now=now))
        db.ensure_effect_plan('JOIN-aabbccdd',base.CHANNEL,'test',worker.outlet_specs[pub].env_file,worker.config,now=now)
        db.record_agent_chat(pub,base.CHAT,base.FGS.chat_ref(base.CHAT),binding_id='test',now=now)
        db.advance_join('JOIN-aabbccdd',expected='approved',target='applied',now=now)
        db.advance_join('JOIN-aabbccdd',expected='applied',target='done',now=now)

    def test_new_approved_spec_absent_binding_cfg_gets_private_own_app_projection(self):
        world,run,w,spec=self.assembly();cfg=json.loads(w.config.read_text());cfg['agents'].pop(base.AGENT2_PK)
        base.write_owner_only(w.config,json.dumps(cfg));before=w.config.read_bytes()
        self.authorize_dynamic(run,w)
        event=self.event();self.put(world,event);seen=[];actual=bw.dm.MappedHostdRound
        def observe(*args,**kwargs):
            instance=actual(*args,**kwargs);seen.append(instance);return instance
        with mock.patch.object(bw.dm,'MappedHostdRound',side_effect=observe):rep=w.run({'buzz'})
        row=self.own_record(run,event);self.assertIsNotNone(row);self.assertEqual(row['status'],'acked')
        self.assertEqual(seen[-1].cfg['agents'][base.AGENT2_PK]['app_id'],own.APP)
        self.assertEqual(w.config.read_bytes(),before)
        self.assertEqual(rep['hostd']['outlet_results'][base.AGENT2_PK]['status'],'acked')
    def test_dynamic_own_bot_projection_does_not_poison_later_cached_feishu_round(self):
        world,run,w,spec=self.assembly()
        cfg=json.loads(w.config.read_text());cfg['agents'].pop(base.AGENT2_PK)
        base.write_owner_only(w.config,json.dumps(cfg))
        self.authorize_dynamic(run,w)
        event=self.event();self.put(world,event)
        first=w.run({'buzz'})
        self.assertEqual(first['hostd']['outlet_results'][base.AGENT2_PK]['status'],'acked')
        # Each round reloads the protected config. Only the buzz round
        # projected this newly approved own-app sender into its private cfg.
        second=w.run({'feishu'},threads=set())
        self.assertEqual(second['hostd']['setup'],'cached')
        self.assertNotIn(base.AGENT2_PK,w.cached['verified_agents'])
        third=w.run({'buzz'})
        self.assertEqual(third['hostd']['outlet_results'][base.AGENT2_PK]['status'],'acked')
        fourth=w.run({'feishu'},threads=set())
        self.assertEqual(fourth['hostd']['setup'],'cached')
        self.assertNotIn(base.AGENT2_PK,w.cached['verified_agents'])

    def test_stale_catalog_channels_do_not_override_actual_newly_approved_allowlist(self):
        world,run,w,spec=self.assembly();spec.channels=();event=self.event();self.put(world,event)
        w.run({'buzz'});self.assertEqual(self.own_record(run,event)['status'],'acked')
    def test_duplicate_explicit_app_cannot_send_before_identity_conflict_is_reported(self):
        world,run,w,spec=self.assembly();duplicate=copy.copy(spec);duplicate.pubkey=base.AGENT_PK
        w.outlet_specs[base.AGENT_PK]=duplicate;event=self.event();self.put(world,event)
        rep=w.run({'buzz'});self.assertFalse(self.sends(world));self.assertIsNone(self.own_record(run,event))
        self.assertEqual(rep['hostd']['outlet_results'][base.AGENT2_PK]['reason'],'binding_app_conflict')
    def test_shared_feishu_pool_is_client_injection_not_relay_transport(self):
        world,run,w,_=self.assembly()
        class Pool:
            def __init__(self):self.calls=[]
            def request(self,app,config_dir,data_dir,method,path,params=None,data=None,**kwargs):
                self.calls.append((app,method,path));params=params or {};data=data or {}
                if path.endswith('/members/list'):
                    args=['im','+chat-members-list','--chat-id',base.CHAT,'--member-id-type',params['member_id_type'],'--member-types',params['member_types']]
                elif method=='POST' and path=='/open-apis/im/v1/messages':
                    args=['im','+messages-send','--chat-id',data['receive_id'],'--msg-type',data['msg_type'],'--content',data['content'],'--idempotency-key',data['uuid']]
                elif method=='GET' and path=='/open-apis/im/v1/messages':
                    return {'ok':True,'identity':'bot','data':{'items':[world._message_item(m['message_id'],'open_id') for m in world.messages],'has_more':False}}
                else:
                    args=['api',method,path,'--params',json.dumps(params)]
                    if data:args+=['--data',json.dumps(data)]
                args+=['--as','bot','--profile',app]
                result=world([bw.bc.NODE,bw.bc.CLI_ENTRY,*args],env={'LARKSUITE_CLI_CONFIG_DIR':str(config_dir)},capture_output=True,text=True)
                return json.loads(result.stdout)
        pool=Pool();w.http_pool=pool;event=self.event();self.put(world,event)
        rep=w.run({'buzz'});self.assertEqual(self.own_record(run,event)['status'],'acked')
        self.assertIs(run.clients.agents[own.APP].http_pool,pool)
        self.assertTrue(any(app==own.APP and method=='POST' for app,method,path in pool.calls))
    def test_explicit_m3_none_retains_legacy_round_path(self):
        world,run,w,_=self.assembly();w.outlet_specs=None;event=self.event();self.put(world,event);w.run({'buzz'})
        self.assertEqual(len(self.sends(world)),1);self.assertIsNone(self.own_record(run,event))

if __name__=='__main__':unittest.main()
