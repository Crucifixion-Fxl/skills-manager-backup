"""Bounded root history ingestion: complete pages and durable positive IDs."""
import json
import sqlite3
import unittest
from datetime import timedelta
from unittest import mock
import test_hostd_http_wiring as wire
import test_hostd_delivery_mapping as mapping

base=mapping.base
setUpModule=mapping.setUpModule
tearDownModule=mapping.tearDownModule

class PageContract(unittest.TestCase):
    profile=wire.Wiring.profile
    setUp=wire.Wiring.setUp
    no_cli=wire.Wiring.no_cli
    api_calls=wire.Wiring.api_calls
    def test_valid_continuation_is_not_partial_and_query_is_frozen(self):
        self.cli.active_phase='feishu'
        self.server.responses.append(wire.page([wire.message()],True,'opaque/next'))
        page=self.cli.message_page(wire.CHAT,1699999900,1700000100)
        self.assertEqual(page.next_token,'opaque/next')
        self.assertTrue(page.has_more)
        self.assertEqual(page.rows[0]['create_time'],'1700000000000')
        self.assertFalse(self.cli.partial_reads)
        from urllib.parse import parse_qs,urlsplit
        query=parse_qs(urlsplit(self.api_calls()[0]['path']).query)
        self.assertEqual(query['end_time'],['1700000100'])
        self.assertEqual(query['sort_type'],['ByCreateTimeAsc'])
    def test_malformed_page_is_not_positive_discovery(self):
        self.cli.active_phase='feishu'
        self.server.responses.append(wire.page([wire.message()],True,''))
        with self.assertRaises(base.FGS.GroupSyncError):self.cli.message_page(wire.CHAT,1699999900,1700000100)
        self.assertIn('feishu',self.cli.partial_reads)

    def test_malformed_foreign_duplicate_and_time_pages_never_pass(self):
        bad_rows=[dict(wire.message(),chat_id='oc_foreign'),dict(wire.message(),create_time='bad'),
                  dict(wire.message(),create_time='1')]
        for rows in [[r] for r in bad_rows]+[[wire.message(),wire.message()]]:
            with self.subTest(rows=len(rows)):
                self.server.responses.append(wire.page(rows,True,'next'))
                with self.assertRaises(base.FGS.GroupSyncError):
                    self.cli.message_page(wire.CHAT,1699999900,1700000100)

    def test_unsupported_and_malformed_body_are_only_positive_metadata(self):
        rows=[dict(wire.message(mid='om_attachment'),msg_type='file',body={'content':'{"file_key":"file_x"}'}),
              dict(wire.message(mid='om_malformed'),body={'content':'not JSON'})]
        self.server.responses.append(wire.page(rows,False,''))
        page=self.cli.message_page(wire.CHAT,1699999900,1700000100)
        self.assertEqual([r['message_id'] for r in page.rows],['om_attachment','om_malformed'])
        self.assertFalse(self.cli.partial_reads)

class DurablePages(base.TmpCase):
    assembly=mapping.MappedAssembly.assembly
    def test_schema18_and_atomic_capture_survive_reopen(self):
        import feishu_ingest as ingest
        _,_,_,run=self.assembly()
        db=run.mapping_store
        self.assertEqual(db.conn.execute('PRAGMA user_version').fetchone()[0],19)
        state=ingest.begin(run,100,200)
        row=dict(wire.message(mid='om_one'),chat_id=base.CHAT,create_time='150001')
        page=type('Page',(),dict(rows=(row,),has_more=True,next_token='next'))()
        ingest.capture(run,state,page)
        self.assertEqual(ingest.pending(run)[0]['message_id'],'om_one')
        with mapping.Store(db.path) as reopened:
            self.assertEqual(reopened.conn.execute('SELECT next_token FROM feishu_scan').fetchone()[0],'next')
            self.assertEqual(reopened.conn.execute('SELECT source_ms FROM feishu_ingest').fetchone()[0],150001)

    def test_schema17_migration_preserves_business_rows(self):
        import store as storage
        path=self.tmp/'old.db'
        conn=sqlite3.connect(path)
        conn.executescript(storage._SCHEMA_V17)
        conn.execute('PRAGMA user_version=17');conn.commit();conn.close();path.chmod(0o600)
        with storage.Store(path) as db:
            self.assertEqual(db.conn.execute('PRAGMA user_version').fetchone()[0],19)
            self.assertFalse(db.conn.execute('SELECT * FROM feishu_ingest').fetchall())
        with storage.Store(path) as db:self.assertEqual(db.conn.execute('PRAGMA user_version').fetchone()[0],19)

    def test_schema17_upgrade_preserves_existing_acked_delivery_and_binding(self):
        _,_,world,run=self.assembly()
        world.messages=[base.fmsg('om_migrated',base.ALICE_OPEN,'before upgrade')]
        run.feishu_to_buzz(only_threads=set());db=run.mapping_store
        before=dict(db.delivery_by_source('test','om_migrated','f2b'))
        binding=dict(db.conn.execute('SELECT * FROM binding').fetchone())
        for table in ['join_adoption','feishu_scan_token','feishu_scan','feishu_ingest']:db.conn.execute('DROP TABLE '+table)
        db.conn.execute('PRAGMA user_version=17')
        with mapping.Store(db.path) as upgraded:
            self.assertEqual(dict(upgraded.delivery_by_source('test','om_migrated','f2b')),before)
            self.assertEqual(dict(upgraded.conn.execute('SELECT * FROM binding').fetchone()),binding)
            self.assertEqual(upgraded.conn.execute('PRAGMA user_version').fetchone()[0],19)

    def test_more_than_legacy_two_thousand_limit_dense_ids_survive_paged_reopen(self):
        import feishu_ingest as ingest
        _,_,_,run=self.assembly();original=run.mapping_store
        scan=ingest.begin(run,100,200)
        count=base.FGS.FEISHU_PAGE_LIMIT*50+1
        for offset in range(0,count,50):
            rows=tuple(dict(wire.message(mid=f'om_many{i:04}'),chat_id=base.CHAT,create_time='150001')
                       for i in range(offset,min(count,offset+50)))
            page=type('Page',(),dict(rows=rows,has_more=offset+50<count,next_token=str(offset+50)))()
            with mapping.Store(original.path) as reopened:
                run.mapping_store=reopened
                scan=dict(reopened.conn.execute('SELECT * FROM feishu_scan').fetchone())
                ingest.capture(run,scan,page)
            run.mapping_store=original
        self.assertEqual(len(ingest.pending(run)),count)
        self.assertEqual(original.conn.execute('SELECT complete FROM feishu_scan').fetchone()[0],1)
        self.assertEqual(original.conn.execute('SELECT count(DISTINCT source_ms) FROM feishu_ingest').fetchone()[0],1)

    def test_capacity_failure_does_not_move_token_or_keep_partial_ids(self):
        import feishu_ingest as ingest
        _,_,_,run=self.assembly();db=run.mapping_store
        scan=ingest.begin(run,100,200)
        db.conn.execute("CREATE TEMP TRIGGER limit_ingest BEFORE INSERT ON feishu_ingest WHEN NEW.message_id='om_second' BEGIN SELECT RAISE(ABORT,'test capacity'); END")
        rows=tuple(dict(wire.message(mid=mid),chat_id=base.CHAT,create_time='150001') for mid in ['om_first','om_second'])
        page=type('Page',(),dict(rows=rows,has_more=True,next_token='next'))()
        from store import StoreError
        with self.assertRaises(StoreError):ingest.capture(run,scan,page)
        self.assertFalse(ingest.pending(run))
        self.assertEqual(db.conn.execute('SELECT next_token FROM feishu_scan').fetchone()[0],'')
        self.assertEqual(db.conn.execute('SELECT count(*) FROM feishu_scan_token').fetchone()[0],0)

    def test_scope_change_restarts_old_interval_without_discarding_work(self):
        import feishu_ingest as ingest
        _,_,_,run=self.assembly();scan=ingest.begin(run,100,200)
        row=dict(wire.message(mid='om_one'),chat_id=base.CHAT,create_time='150001')
        ingest.capture(run,scan,type('Page',(),dict(rows=(row,),has_more=True,next_token='next'))())
        run.cfg=dict(run.cfg,identity_test='new scope')
        changed=ingest.begin(run,180,250)
        self.assertEqual((changed['start_at'],changed['next_token']),(100,''))
        self.assertNotEqual(changed['scope_hash'],scan['scope_hash'])
        self.assertEqual(len(ingest.pending(run)),1)

    def test_repeated_token_rejects_cycle_and_keeps_prior_work(self):
        import feishu_ingest as ingest
        _,_,_,run=self.assembly();scan=ingest.begin(run,100,200)
        row=dict(wire.message(mid='om_one'),chat_id=base.CHAT,create_time='150001')
        page=type('Page',(),dict(rows=(row,),has_more=True,next_token='next'))()
        ingest.capture(run,scan,page)
        current=dict(run.mapping_store.conn.execute('SELECT * FROM feishu_scan').fetchone())
        with self.assertRaises(base.FGS.GroupSyncError):ingest.capture(run,current,page)
        self.assertEqual(len(ingest.pending(run)),1)

    def test_stale_failed_query_cannot_reset_a_new_scope_token(self):
        import feishu_ingest as ingest
        _,_,_,run=self.assembly();old=ingest.begin(run,100,200)
        row=dict(wire.message(mid='om_one'),chat_id=base.CHAT,create_time='150001')
        ingest.capture(run,old,type('Page',(),dict(rows=(row,),has_more=True,next_token='old'))())
        stale=dict(run.mapping_store.conn.execute('SELECT * FROM feishu_scan').fetchone())
        run.cfg=dict(run.cfg,scope_revision='new')
        changed=ingest.begin(run,100,200)
        ingest.capture(run,changed,type('Page',(),dict(rows=(row,),has_more=True,next_token='new'))())
        ingest.reset_token(run,stale)
        self.assertEqual(run.mapping_store.conn.execute('SELECT next_token FROM feishu_scan').fetchone()[0],'new')
        self.assertEqual(run.mapping_store.conn.execute('SELECT count(*) FROM feishu_scan_token').fetchone()[0],1)

class WorkerIngest(base.TmpCase):
    from test_hostd_worker_store import WorkerAssembly
    assembly=WorkerAssembly.assembly
    def snapshot(self,env,path):
        from state_store import StateAdapter
        with mapping.Store(path) as db:
            return StateAdapter(db,'test-binding',env.state_dir).load(),[dict(r) for r in db.conn.execute('SELECT * FROM feishu_ingest')]
    def test_dense_timestamps_backlog_drains_across_worker_restarts(self):
        import feishu_ingest as ingest
        env,_,world,path,factory=self.assembly()
        # More than one complete API page, all in the same millisecond/window.
        world.messages=[base.fmsg(f'om_dense{i:03}',base.ALICE_OPEN,f'message {i}') for i in range(61)]
        total=0
        with mock.patch.object(ingest,'DRAIN_SECONDS',100):
            for turn in range(12):
                report=factory().run({'feishu'},threads=set());total+=report['to_buzz']
                state,rows=self.snapshot(env,path)
                if total==61:break
        self.assertEqual(total,61)
        self.assertEqual(len([r for r in rows if not r['done']]),0)
        self.assertEqual(len([e for e in world.relay_writes if e['kind']==9]),61)
        self.assertEqual(factory().run({'feishu'},threads=set())['to_buzz'],0)
        self.assertTrue(all(base.FGS._settled(state.f2b.get(m['message_id'])) for m in world.messages))
        pages=[argv for argv,_ in world.bot_calls if argv[2:5]==['api','GET','/open-apis/im/v1/messages']]
        self.assertTrue(any(json.loads(a[a.index('--params')+1]).get('page_token')=='50' for a in pages))

    def test_missing_exact_read_is_retained_while_independent_source_succeeds(self):
        env,_,world,path,factory=self.assembly()
        world.messages=[base.fmsg('om_bad',base.ALICE_OPEN,'bad'),base.fmsg('om_good',base.ALICE_OPEN,'good')]
        def tamper(mid,space,item):
            if mid=='om_bad':return dict(item,chat_id='oc_foreign')
            return item
        world.message_get_tamper=tamper
        report=factory().run({'feishu'},threads=set())
        state,rows=self.snapshot(env,path)
        self.assertEqual(report['to_buzz'],1)
        self.assertNotIn('om_bad',state.f2b)
        self.assertTrue(base.FGS._settled(state.f2b['om_good']))
        self.assertEqual(next(r for r in rows if r['message_id']=='om_bad')['done'],0)
        self.assertGreater(report['hostd']['next_retry_at'],int(base.NOW.timestamp()))
        # Lease and ID survive a fresh Worker, no repeated writes.
        self.assertEqual(factory().run({'feishu'},threads=set())['to_buzz'],0)

    def test_unknown_is_not_reset_or_republished_by_queue(self):
        from state_store import StateAdapter
        env,_,world,path,factory=self.assembly();factory().run({'buzz'})
        world.messages=[base.fmsg('om_unknown',base.ALICE_OPEN,'unknown')]
        with mapping.Store(path) as db:
            adapter=StateAdapter(db,'test-binding',env.state_dir);state=adapter.load()
            state.f2b['om_unknown']=base.FGS.UNKNOWN;adapter.save(state)
        report=factory().run({'feishu'},threads=set())
        state,rows=self.snapshot(env,path)
        self.assertEqual(report['to_buzz'],0)
        self.assertEqual(state.f2b['om_unknown'],base.FGS.UNKNOWN)
        self.assertTrue(rows[0]['done'])
        self.assertEqual(world.relay_writes,[])

    def test_processing_budget_retains_unvisited_ids_without_advancing_to_now(self):
        import feishu_ingest as ingest
        env,_,world,path,factory=self.assembly()
        world.messages=[base.fmsg(f'om_budget{i}',base.ALICE_OPEN,'text') for i in range(3)]
        with mock.patch.object(ingest,'DRAIN_LIMIT',1):
            report=factory().run({'feishu'},threads=set())
        state,rows=self.snapshot(env,path)
        self.assertEqual(report['to_buzz'],1)
        self.assertEqual(sum(not r['done'] for r in rows),2)
        self.assertIn('feishu',report['hostd']['cooperative_phases'])
        self.assertEqual(factory().run({'feishu'},threads=set())['to_buzz'],2)

    def test_failed_continuation_restarts_same_interval_and_preserves_discovery(self):
        import feishu_ingest as ingest
        env,_,world,path,factory=self.assembly()
        world.messages=[base.fmsg(f'om_restart{i:03}',base.ALICE_OPEN,'text') for i in range(51)]
        with mock.patch.object(ingest,'DRAIN_LIMIT',1):factory().run({'feishu'},threads=set())
        worker=factory();original=worker.client_factory
        def clients(cfg,env):
            built=original(cfg,env)
            def fail(*args,**kwargs):raise base.FGS.GroupSyncError('expired page token')
            built.owner.message_page=fail;return built
        worker.client_factory=clients
        report=worker.run({'feishu'},threads=set())
        self.assertIn('feishu',report['hostd']['retry_phases'])
        self.assertGreater(report['to_buzz'],0)  # Retained work drains despite a broken continuation.
        with mapping.Store(path) as db:
            scan=dict(db.conn.execute('SELECT * FROM feishu_scan').fetchone())
            self.assertEqual(scan['next_token'],'')
            self.assertEqual(db.conn.execute('SELECT count(*) FROM feishu_ingest').fetchone()[0],50)
        with mock.patch.object(ingest,'DRAIN_SECONDS',100):
            for _ in range(9):factory().run({'feishu'},threads=set())
        state,rows=self.snapshot(env,path)
        self.assertEqual(sum(not r['done'] for r in rows),0)
        self.assertEqual(len([e for e in world.relay_writes if e['kind']==9]),51)

    def test_crash_after_capture_replays_retained_ids_after_restart(self):
        import feishu_ingest as ingest
        env,_,world,path,factory=self.assembly()
        world.messages=[base.fmsg('om_crash',base.ALICE_OPEN,'crash boundary')]
        with mock.patch.object(ingest,'process',side_effect=RuntimeError('simulated process exit')):
            with self.assertRaises(RuntimeError):factory().run({'feishu'},threads=set())
        _,rows=self.snapshot(env,path)
        self.assertEqual([(r['message_id'],r['done']) for r in rows],[('om_crash',0)])
        self.assertEqual(factory().run({'feishu'},threads=set())['to_buzz'],1)
        self.assertEqual(factory().run({'feishu'},threads=set())['to_buzz'],0)

    def test_crash_after_signed_write_does_not_publish_second_event(self):
        import delivery_mapping as dm
        env,_,world,path,factory=self.assembly()
        world.messages=[base.fmsg('om_after_write',base.ALICE_OPEN,'crash after effect')]
        with mock.patch.object(dm.MappedHostdRound,'_delivery_settled',side_effect=RuntimeError('exit before settlement')):
            with self.assertRaises(RuntimeError):factory().run({'feishu'},threads=set())
        self.assertEqual(len([e for e in world.relay_writes if e['kind']==9]),1)
        # Retry after the committed lease. The actual signed mapping is recovered.
        w=factory();w.clock=lambda:base.NOW+timedelta(seconds=31)
        w.run({'feishu'},threads=set())
        self.assertEqual(len([e for e in world.relay_writes if e['kind']==9]),1)
        state,_=self.snapshot(env,path)
        self.assertTrue(base.FGS._settled(state.f2b['om_after_write']))

    def test_no_processing_when_budget_already_expired(self):
        import feishu_ingest as ingest
        env,_,world,path,factory=self.assembly()
        world.messages=[base.fmsg('om_deadline',base.ALICE_OPEN,'text')]
        with mock.patch.object(ingest,'DRAIN_SECONDS',-1):
            report=factory().run({'feishu'},threads=set())
        _,rows=self.snapshot(env,path)
        self.assertEqual(report['to_buzz'],0)
        self.assertEqual(rows[0]['attempts'],0)
        self.assertIn('feishu',report['hostd']['cooperative_phases'])

    def test_more_than_six_hour_old_unattempted_ids_are_not_closed(self):
        import feishu_ingest as ingest
        from state_store import StateAdapter
        env,_,world,path,factory=self.assembly();factory().run({'buzz'})
        old=base.NOW-timedelta(hours=8)
        with mapping.Store(path) as db:
            adapter=StateAdapter(db,'test-binding',env.state_dir);state=adapter.load()
            # Retained positive discovery, independently of newer delivery cursor.
            db.conn.execute('INSERT INTO feishu_ingest(binding_id,chat_id,message_id,source_ms,captured_floor) VALUES(?,?,?,?,?)',
                ('test-binding',base.CHAT,'om_older',int(old.timestamp())*1000,int(old.timestamp())-1))
            state.floor=state.feishu_floor=int(old.timestamp())-10;adapter.save(state)
        world.messages=[base.fmsg('om_older',base.ALICE_OPEN,'old retained',when=old)]
        report=factory().run({'feishu'},threads=set())
        self.assertEqual(report['to_buzz'],1)
        state,rows=self.snapshot(env,path)
        self.assertTrue(base.FGS._settled(state.f2b['om_older']))

    def test_missing_root_holds_reply_but_independent_topic_then_restored_root_releases(self):
        env,_,world,path,factory=self.assembly()
        reply=base.fmsg('om_reply',base.ALICE_OPEN,'reply');reply['root_id']='om_root'
        world.messages=[reply,base.fmsg('om_independent',base.ALICE_OPEN,'independent')]
        self.assertEqual(factory().run({'feishu'},threads=set())['to_buzz'],1)
        state,rows=self.snapshot(env,path)
        self.assertNotIn('om_reply',state.f2b)
        self.assertEqual(next(r for r in rows if r['message_id']=='om_reply')['done'],0)
        world.messages.append(base.fmsg('om_root',base.ALICE_OPEN,'real root'))
        self.assertEqual(factory().run({'feishu'},threads=set())['to_buzz'],1)
        w=factory();w.clock=lambda:base.NOW+timedelta(seconds=31);world.clock=w.clock()
        self.assertEqual(w.run({'feishu'},threads=set())['to_buzz'],1)
        state,_=self.snapshot(env,path)
        event=next(e for e in world.relay_writes if e['id']==state.f2b['om_reply'])
        self.assertIn(['e',state.f2b['om_root'],'','reply'],event['tags'])
        self.assertIn(['feishu-root','om_root'],event['tags'])

    def test_context_wait_survives_batch_boundary_and_restart(self):
        import feishu_ingest as ingest
        from dataclasses import replace
        env,_,world,path,factory=self.assembly()
        world.messages=[base.fmsg('om_z_context',base.ALICE_OPEN,'context'),
                        base.fmsg('om_a_mention',base.ALICE_OPEN,'question'),
                        base.fmsg('om_2_independent',base.ALICE_OPEN,'independent')]
        original=base.FGS.route_feishu_message
        def route(msg,**kw):
            inbound=original(msg,**kw)
            if isinstance(inbound,base.FGS.Inbound):
                if msg['message_id']=='om_z_context':return replace(inbound,sender_pubkey='',context_only=True)
                if msg['message_id']=='om_a_mention':return replace(inbound,mentions=(base.AGENT_PK,))
            return inbound
        with mock.patch.object(base.FGS,'route_feishu_message',side_effect=route):
            with mock.patch.object(ingest,'DRAIN_LIMIT',1):self.assertEqual(factory().run({'feishu'},threads=set())['to_buzz'],0)
            self.assertEqual(factory().run({'feishu'},threads=set())['to_buzz'],1)
            state,rows=self.snapshot(env,path)
            self.assertNotIn('om_a_mention',state.f2b)
            self.assertTrue(next(r for r in rows if r['message_id']=='om_z_context')['waiting_context'])
            w=factory();w.clock=lambda:base.NOW+timedelta(seconds=180);world.clock=w.clock()
            w.run({'feishu'},threads=set())
            w=factory();w.clock=lambda:base.NOW+timedelta(seconds=241);world.clock=w.clock()
            w.run({'feishu'},threads=set())
        state,rows=self.snapshot(env,path)
        self.assertEqual(sum(not r['done'] for r in rows),0)
        sent=[next(t[1] for t in e['tags'] if t[0]=='feishu') for e in world.relay_writes if e['kind']==9]
        self.assertEqual(sent,['om_2_independent','om_z_context','om_a_mention'])

    def test_completed_scan_advances_independently_of_partial_thread_cursor(self):
        import feishu_ingest as ingest
        _,_,_,run=mapping.MappedAssembly.assembly(self)
        floor=run.state.floor;first=ingest.begin(run,floor,run.now_ts)
        ingest.capture(run,first,type('Page',(),dict(rows=(),has_more=False,next_token=''))())
        run.state.feishu_since=floor
        run.now+=timedelta(hours=1)
        next_scan=ingest.begin(run,floor,run.now_ts)
        self.assertEqual(next_scan['start_at'],max(floor,first['end_at']-900))
        self.assertEqual(next_scan['end_at'],run.now_ts)

    def test_edited_deleted_source_uses_fresh_exact_evidence(self):
        import feishu_ingest as ingest
        env,_,world,path,factory=self.assembly()
        world.messages=[base.fmsg('om_delete',base.ALICE_OPEN,'old body')]
        with mock.patch.object(ingest,'DRAIN_LIMIT',0):factory().run({'feishu'},threads=set())
        world.messages[0]['deleted']=True
        self.assertEqual(factory().run({'feishu'},threads=set())['to_buzz'],0)
        _,rows=self.snapshot(env,path)
        self.assertTrue(rows[0]['done']);self.assertEqual(world.relay_writes,[])

    def test_malformed_fresh_body_retained_without_blocking_independent_source(self):
        env,_,world,path,factory=self.assembly()
        world.messages=[base.fmsg('om_badbody',base.ALICE_OPEN,'bad'),base.fmsg('om_goodbody',base.ALICE_OPEN,'good')]
        world.message_get_tamper=lambda mid,kind,item:dict(item,body={'content':'not JSON'}) if mid=='om_badbody' else item
        self.assertEqual(factory().run({'feishu'},threads=set())['to_buzz'],1)
        state,rows=self.snapshot(env,path)
        self.assertNotIn('om_badbody',state.f2b)
        self.assertFalse(next(r for r in rows if r['message_id']=='om_badbody')['done'])

    def test_crash_after_final_page_before_watermark_retains_every_source(self):
        import feishu_ingest as ingest
        env,_,world,path,factory=self.assembly()
        world.messages=[base.fmsg('om_final',base.ALICE_OPEN,'final page')]
        with mock.patch.object(ingest,'finish',side_effect=RuntimeError('exit before cursor checkpoint')):
            with self.assertRaises(RuntimeError):factory().run({'feishu'},threads=set())
        with mapping.Store(path) as db:
            self.assertTrue(db.conn.execute('SELECT complete FROM feishu_scan').fetchone()[0])
            self.assertEqual(db.conn.execute('SELECT count(*) FROM feishu_ingest').fetchone()[0],1)
        self.assertEqual(factory().run({'feishu'},threads=set())['to_buzz'],0)
        self.assertEqual(len([e for e in world.relay_writes if e['kind']==9]),1)

    def test_old_retained_thread_root_keeps_original_reply_window_after_scan_completion(self):
        from state_store import StateAdapter
        env,_,world,path,factory=self.assembly();factory().run({'buzz'})
        old=base.NOW-timedelta(hours=8);captured=int(old.timestamp())-60
        with mapping.Store(path) as db:
            adapter=StateAdapter(db,'test-binding',env.state_dir);state=adapter.load()
            db.conn.execute('INSERT INTO feishu_ingest(binding_id,chat_id,message_id,source_ms,captured_floor) VALUES(?,?,?,?,?)',
                ('test-binding',base.CHAT,'om_oldthread',int(old.timestamp())*1000,captured))
            state.floor=state.feishu_floor=captured;adapter.save(state)
        root=base.fmsg('om_oldthread',base.ALICE_OPEN,'old root',when=old,thread_id='omt_oldthread')
        reply=dict(base.fmsg('om_oldreply',base.ALICE_OPEN,'old reply',when=old+timedelta(minutes=1)),root_id='om_oldthread')
        reply['sender']['id']=world.bot_open
        world.messages=[root];world.threads={'om_oldthread':[reply]}
        self.assertEqual(factory().run({'feishu'},threads=set())['to_buzz'],1)
        state,_=self.snapshot(env,path)
        self.assertLessEqual(state.polled['om_oldthread'],captured)
        self.assertGreater(state.feishu_since,captured)
        report=factory().run({'feishu'},threads={'om_oldthread'})
        self.assertEqual(report['to_buzz'],1,report)
        state,_=self.snapshot(env,path)
        event=next(e for e in world.relay_writes if e['id']==state.f2b['om_oldreply'])
        self.assertIn(['e',state.f2b['om_oldthread'],'','reply'],event['tags'])

    def test_unsupported_discovery_body_is_retained_without_blocking_other_sources(self):
        env,_,world,path,factory=self.assembly()
        attachment=base.fmsg('om_attachment',base.ALICE_OPEN,'file',msg_type='file')
        attachment['body']={'content':'{"file_key":"file_x"}'}
        attachment['create_time']=str(int((base.NOW-timedelta(minutes=1)).timestamp())*1000)
        world.messages=[attachment,base.fmsg('om_after_attachment',base.ALICE_OPEN,'independent')]
        self.assertEqual(factory().run({'feishu'},threads=set())['to_buzz'],1)
        state,rows=self.snapshot(env,path)
        self.assertNotIn('om_attachment',state.f2b)
        retained=next(r for r in rows if r['message_id']=='om_attachment')
        self.assertFalse(retained['done']);self.assertEqual(retained['attempts'],1)
        self.assertTrue(base.FGS._settled(state.f2b['om_after_attachment']))
        with mapping.Store(path) as db:
            self.assertIsNone(db.delivery_by_source('test-binding','om_attachment','f2b'))

    def test_legacy_stale_sealed_retry_preserves_display_time_and_exact_event(self):
        _,_,world,run=mapping.MappedAssembly.assembly(self)
        old=base.NOW-timedelta(minutes=11)
        message=base.fmsg('om_legacy_stale',base.ALICE_OPEN,'old signed source',when=old)
        world.messages=[message];world.relay_write_fail=['network']
        normalized=dict(message,sender=dict(message['sender'],id=world.bot_open))
        # Construct an actual sealed event through the previous root hooks.
        with mock.patch.object(run,'_feishu_root_rows',return_value=([normalized],0,0)), \
             mock.patch.object(run,'_feishu_process_root',side_effect=lambda mirror,msg,since:base.FGS.Round._feishu_process_root(run,mirror,msg,since)), \
             mock.patch.object(run,'_feishu_finish_roots',side_effect=lambda:base.FGS.Round._feishu_finish_roots(run)):
            run.feishu_to_buzz(only_threads=set())
        first=next(e for e in world.relay_write_requests if e['kind']==9)
        self.assertIn(message['create_time'],first['content'])
        run.now+=timedelta(seconds=31);world.clock=run.now;run.auth_clock=lambda:run.now
        run.feishu_to_buzz(only_threads=set())
        posted=[e for e in world.relay_write_requests if e['kind']==9]
        self.assertEqual(posted,[first,first])
        self.assertEqual(run.state.f2b['om_legacy_stale'],first['id'])
