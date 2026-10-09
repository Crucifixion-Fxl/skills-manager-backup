"""Real Worker: failing old roots must not monopolize a one-event slice."""
import unittest
from unittest import mock
import test_hostd_worker_outlets as fixture

base=fixture.base
setUpModule=fixture.setUpModule
tearDownModule=fixture.tearDownModule

class OutletBudget(base.TmpCase):
    assembly=fixture.WorkerOutlets.assembly
    event=fixture.WorkerOutlets.event

    def test_many_old_broken_roots_yield_to_new_independent_topic_with_one_attempt(self):
        world,run,worker,spec=self.assembly()
        old=[self.event(tags=[['e',format(i+1,'064x'),'','root']],created=run.now_ts-100+i) for i in range(14)]
        fresh=self.event(content='new independent topic')
        world.events.extend([*old,fresh]);world.relay_events.extend([*old,fresh])
        calls=[];original=fixture.bw.ao.AgentOutlet._deliver
        def attempt(adapter,event,ancestors,**options):
            if not ancestors:calls.append(event['id'])
            return original(adapter,event,ancestors,**options)
        with mock.patch.object(fixture.bw.ao.AgentOutlet,'_deliver',attempt):
            worker.run({'buzz'})
        self.assertLessEqual(len(calls),1,'failed sources count against the production one-event budget')
        row=run.mapping_store.delivery_by_source('test',fresh['id'],'b2f',agent_id=base.AGENT2_PK)
        self.assertIsNotNone(row,'new independent topic must not wait for every older broken root')
        self.assertEqual(row['status'],'acked')

class Scheduling(base.TmpCase):
    assembly=fixture.WorkerOutlets.assembly
    event=fixture.WorkerOutlets.event
    def adapter(self,run,spec,world,*,floor=None):
        return fixture.bw.ao.AgentOutlet(run,base.AGENT2_PK,spec.env_file,
            bot_client=run.clients.agents[spec.app_id],trusted_relays={'https://relay.test'},
            http=world.http_get,clock=lambda:base.NOW,
            initial_since=max(run.state.floor,run.state.buzz_floor) if floor is None else floor)

    def test_all_skipped_ids_survive_cursor_advance_reopen_and_baseline_change(self):
        from store import Store
        world,run,w,spec=self.assembly()
        old=[self.event(tags=[['e',format(i+1,'064x'),'','root']],created=run.now_ts-100+i) for i in range(3)]
        fresh=self.event(content='new topic')
        world.events.extend([*old,fresh]);world.relay_events.extend([*old,fresh]);w.run({'buzz'})
        module=fixture.bw.ao.outlet_work
        rows=module.pending(run.mapping_store,'test',base.CHANNEL,base.AGENT2_PK)
        self.assertEqual({r['source_id'] for r in rows},{e['id'] for e in old})
        self.assertGreater(run.mapping_store.cursor_position('test','relay',agent_id=base.AGENT2_PK),old[0]['created_at'])
        with Store(run.mapping_store.path) as reopened:self.assertEqual(module.pending(reopened,'test',base.CHANNEL,base.AGENT2_PK),rows)
        adapter=self.adapter(run,spec,world,floor=run.now_ts+3600)
        self.assertLessEqual(adapter._since(),old[0]['created_at'])
        with self.assertRaises(base.FGS.GroupSyncError):adapter.catch_up(max_events=1)
        self.assertEqual(len(module.pending(run.mapping_store,'test',base.CHANNEL,base.AGENT2_PK)),3)

    def test_failed_candidate_budget_is_not_retried_by_worker_second_pass(self):
        world,run,w,spec=self.assembly()
        bad=[self.event(tags=[['e',format(i+1,'064x'),'','root']],created=run.now_ts-i) for i in range(3)]
        world.events.extend(bad);world.relay_events.extend(bad)
        original=fixture.bw.ao.AgentOutlet._deliver;calls=[]
        def deliver(adapter,event,ancestors,**kw):
            if not ancestors:calls.append(event['id'])
            return original(adapter,event,ancestors,**kw)
        with mock.patch.object(fixture.bw.ao.AgentOutlet,'_deliver',deliver):
            for _ in range(3):w.run({'buzz'})
            self.assertEqual(len(calls),3);self.assertEqual(len(set(calls)),3)
            w.run({'buzz'});self.assertEqual(len(calls),3,'not-due failures must not spin')
        self.assertEqual(w.last['hostd']['outlet_results'][base.AGENT2_PK]['reason'],'own_outlet_deferred')

    def test_same_topic_blocks_newer_reply_but_not_an_independent_topic(self):
        world,run,w,spec=self.assembly()
        old=self.event(tags=[['e','a'*64,'','root']],created=run.now_ts-10)
        related=self.event(tags=[['e','a'*64,'','root']],content='later same thread')
        fresh=self.event(content='independent',created=run.now_ts-1)
        world.events.extend([old,related,fresh]);world.relay_events.extend([old,related,fresh])
        w.run({'buzz'});w.run({'buzz'})
        self.assertIsNone(run.mapping_store.delivery_by_source('test',related['id'],'b2f',agent_id=base.AGENT2_PK))
        self.assertEqual(run.mapping_store.delivery_by_source('test',fresh['id'],'b2f',agent_id=base.AGENT2_PK)['status'],'acked')
        rows=fixture.bw.ao.outlet_work.pending(run.mapping_store,'test',base.CHANNEL,base.AGENT2_PK)
        self.assertEqual({r['source_id'] for r in rows},{old['id'],related['id']})

    def test_continuous_fresh_topics_cannot_starve_older_unattempted_topic(self):
        from datetime import timedelta
        world,run,w,spec=self.assembly()
        old=self.event(tags=[['e','a'*64,'','root']],created=run.now_ts-100)
        world.events.append(old);world.relay_events.append(old)
        for i in range(8):
            world.clock=base.NOW+timedelta(seconds=i);w.clock=lambda:world.clock
            event=self.event(content='fresh'+str(i),created=run.now_ts+i)
            world.events.append(event);world.relay_events.append(event);w.outlet_rescan=True;w.run({'buzz'})
        row=next(r for r in fixture.bw.ao.outlet_work.pending(run.mapping_store,'test',base.CHANNEL,base.AGENT2_PK) if r['source_id']==old['id'])
        self.assertEqual(row['attempts'],1)
        self.assertEqual(len([a for a,_ in world.bot_calls if '--content' in a]),7)

    def test_due_retry_gets_eighth_selection_with_twenty_fresh_topics(self):
        self._continuous_fairness(False)

    def test_old_unattempted_topic_ages_ahead_of_due_retry_and_continuous_topics(self):
        self._continuous_fairness(True)

    def _continuous_fairness(self,include_old_fresh):
        from datetime import timedelta
        world,run,w,spec=self.assembly()
        bad=self.event(tags=[['e','a'*64,'','root']],created=run.now_ts-200)
        world.events.append(bad);world.relay_events.append(bad)
        calls=[];original=fixture.bw.ao.AgentOutlet._deliver
        def attempt(adapter,event,ancestors,**kw):
            if not ancestors:calls.append(event['id'])
            return original(adapter,event,ancestors,**kw)
        with mock.patch.object(fixture.bw.ao.AgentOutlet,'_deliver',attempt):
            w.run({'buzz'})
            old=self.event(content='old independent unattempted',created=run.now_ts-100)
            if include_old_fresh:
                world.events.append(old);world.relay_events.append(old)
            for i in range(20):
                event=self.event(content='backlog'+str(i),created=run.now_ts+31)
                world.events.append(event);world.relay_events.append(event)
            for i in range(15 if include_old_fresh else 7):
                world.clock=base.NOW+timedelta(seconds=31+i);w.clock=lambda:world.clock
                event=self.event(content='continuous'+str(i),created=int(world.clock.timestamp()))
                world.events.append(event);world.relay_events.append(event)
                w.outlet_rescan=True;w.run({'buzz'})
        self.assertEqual(calls[0],bad['id'])
        self.assertEqual(calls[7],old['id'] if include_old_fresh else bad['id'])
        if include_old_fresh:
            self.assertEqual(calls[15],bad['id'])
            self.assertEqual(run.mapping_store.delivery_by_source('test',old['id'],'b2f',agent_id=base.AGENT2_PK)['status'],'acked')
        rows=fixture.bw.ao.outlet_work.pending(run.mapping_store,'test',base.CHANNEL,base.AGENT2_PK)
        self.assertEqual(next(r for r in rows if r['source_id']==bad['id'])['attempts'],2)
        self.assertGreaterEqual(sum(r['attempts']==0 for r in rows),20)


    def test_processing_deadline_yields_before_second_candidate(self):
        from types import SimpleNamespace
        world,run,w,spec=self.assembly();adapter=self.adapter(run,spec,world)
        events=[self.event(content=str(i),created=run.now_ts-i) for i in range(3)]
        world.events.extend(events);world.relay_events.extend(events)
        ticks=iter([0.0,0.0,6.0])
        with mock.patch.object(fixture.bw.ao,'time',SimpleNamespace(monotonic=lambda:next(ticks))):adapter.catch_up()
        self.assertTrue(adapter.slice_pending)
        self.assertEqual(len([a for a,_ in world.bot_calls if '--content' in a]),1)
        self.assertEqual(len(fixture.bw.ao.outlet_work.pending(run.mapping_store,'test',base.CHANNEL,base.AGENT2_PK)),2)

    def test_schema16_upgrade_preserves_pending_and_all_business_rows(self):
        from store import Store
        world,run,w,spec=self.assembly();db=run.mapping_store
        old=self.event(tags=[['e','a'*64,'','root']])
        # A real frozen16 unresolved record, retained without inventing a receipt.
        self.adapter(run,spec,world).verify()
        fixture.bw.ao.outlet_pending.remember(db,'test',base.CHANNEL,base.AGENT2_PK,old,floor=0)
        before=[tuple(r) for r in db.conn.execute('SELECT * FROM outlet_pending')]
        db.conn.execute('DROP TABLE join_adoption');db.conn.execute('DROP TABLE feishu_scan_token');db.conn.execute('DROP TABLE feishu_scan');db.conn.execute('DROP TABLE feishu_ingest');db.conn.execute('DROP TABLE outlet_work_turn');db.conn.execute('DROP TABLE outlet_work');db.conn.execute('PRAGMA user_version=16')
        with Store(db.path) as upgraded:
            self.assertEqual(upgraded.conn.execute('PRAGMA user_version').fetchone()[0],19)
            self.assertEqual([tuple(r) for r in upgraded.conn.execute('SELECT * FROM outlet_pending')],before)
            self.assertEqual(upgraded.conn.execute('SELECT count(*) FROM outlet_work').fetchone()[0],0)

    def test_capture_capacity_is_atomic_and_never_advances_native_or_cursor(self):
        import sqlite3
        from store import StoreError
        world,run,w,spec=self.assembly();db=run.mapping_store;mod=fixture.bw.ao.outlet_work
        for i in range(9999):
            db.conn.execute('INSERT INTO outlet_work VALUES(?,?,?,?,?,?,?,?,?,?)',('test',base.AGENT2_PK,format(i,'064x'),base.CHANNEL,9,run.now_ts-100,0,'',0,0))
        events=[self.event(content='overflow'+str(i)) for i in range(2)]
        with self.assertRaises(StoreError):mod.capture(db,'test',base.CHANNEL,base.AGENT2_PK,events,floor=0,now=run.now_ts,retained=set())
        self.assertEqual(db.conn.execute('SELECT count(*) FROM outlet_work').fetchone()[0],9999)
        self.assertFalse(any('--content' in a for a,_ in world.bot_calls))
        with self.assertRaises(sqlite3.IntegrityError):db.conn.execute("UPDATE outlet_work SET parent_id=? WHERE source_id=?",('a'*64,'0'*64))

if __name__=='__main__':unittest.main()
