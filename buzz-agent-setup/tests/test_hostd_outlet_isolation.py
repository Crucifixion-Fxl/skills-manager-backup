"""Per-event isolation uses actual outlet, signed sources and native receipts."""
import unittest
import hashlib
import sqlite3
from unittest import mock
import test_hostd_outlet as fixture
base=fixture.base
from outlet import AgentOutlet
from outlet_pending import pending as legacy_pending, remember
from outlet_work import pending as work_pending
def pending(*args):return legacy_pending(*args)+work_pending(*args)
from store import Store, StoreError
setUpModule=fixture.setUpModule
tearDownModule=fixture.tearDownModule

class OutletIsolation(base.TmpCase):
    assembly=fixture.OwnOutlet.assembly
    event=fixture.OwnOutlet.event
    history_http=fixture.OwnOutlet.history_http

    def test_missing_old_root_does_not_starve_later_independent_thread(self):
        world,run,adapter=self.assembly()
        old=self.event(tags=[['e','a'*64,'','root']],created=run.now_ts-2000)
        fresh=self.event(content='independent later thread')
        world.events.extend([old,fresh]); world.relay_events.extend([old,fresh])
        adapter.catch_up(max_events=1)
        self.assertTrue(adapter.slice_pending)
        row=run.mapping_store.delivery_by_source('test',fresh['id'],'b2f',agent_id=base.AGENT2_PK)
        self.assertIsNotNone(row,'a missing root must not prevent a later independent delivery')
        self.assertEqual(row['status'],'acked')

    def blocked_pair(self):
        world,run,adapter=self.assembly()
        root=self.event(content='old root',created=run.now_ts-2200)
        old=self.event(tags=[['e',root['id'],'','root']],created=run.now_ts-2000)
        fresh=self.event(content='independent new thread')
        world.events.extend([old,fresh]);world.relay_events.extend([old,fresh])
        adapter.catch_up(max_events=1)
        self.assertTrue(adapter.slice_pending)
        return world,run,adapter,root,old,fresh

    def test_restart_and_advanced_floor_preserve_old_source_then_recover_exact_root(self):
        world,run,adapter,root,old,fresh=self.blocked_pair()
        rows=pending(run.mapping_store,'test',base.CHANNEL,base.AGENT2_PK)
        self.assertEqual([r['source_id'] for r in rows],[old['id']])
        self.assertGreater(run.mapping_store.cursor_position('test','relay',agent_id=base.AGENT2_PK),old['created_at']+900)
        with Store(run.mapping_store.path) as reopened:
            self.assertEqual(pending(reopened,'test',base.CHANNEL,base.AGENT2_PK),rows)
        restarted=AgentOutlet(run,base.AGENT2_PK,self.tmp/'outlet.env',bot_client=adapter.client,
            trusted_relays={'https://relay.test'},http=world.http_get,clock=lambda:base.NOW,initial_since=run.now_ts+100)
        self.assertEqual(restarted._since(),old['created_at'])
        world.events.append(root)
        restarted.catch_up()
        self.assertEqual(pending(run.mapping_store,'test',base.CHANNEL,base.AGENT2_PK),[])
        row=run.mapping_store.delivery_by_source('test',old['id'],'b2f',agent_id=base.AGENT2_PK)
        self.assertEqual(row['status'],'acked')
        # New independent message was not duplicated during root recovery.
        self.assertEqual(len([a for a,_ in world.bot_calls if '--content' in a]),3)

    def test_missing_relay_source_remains_durable_and_cannot_be_mistaken_for_success(self):
        world,run,adapter,root,old,fresh=self.blocked_pair()
        world.events=[e for e in world.events if e['id']!=old['id']]
        world.relay_events=[e for e in world.relay_events if e['id']!=old['id']]
        with self.assertRaises(base.FGS.GroupSyncError):adapter.catch_up()
        self.assertEqual(adapter._since(),old['created_at'])
        self.assertEqual(len(pending(run.mapping_store,'test',base.CHANNEL,base.AGENT2_PK)),1)
        self.assertEqual(len([a for a,_ in world.bot_calls if '--content' in a]),1)

    def test_unknown_native_write_is_not_retried_while_next_thread_succeeds(self):
        world,run,adapter=self.assembly();adapter.verify()
        old=self.event(created=run.now_ts-10);fresh=self.event(content='safe independent source')
        card=fixture.outlet.message_card(old,run.names.get(base.AGENT2_PK) or base.AGENT2_PK[:12],old['content'],
            run.cfg['people_api']['base_url'],base.CHANNEL,compact=True)
        reserved=run.mapping_store.reserve_delivery('test',old['id'],'b2f',agent_id=base.AGENT2_PK,
            source_at=old['created_at'],content_hash=hashlib.sha256(card.encode()).hexdigest(),now=run.now_ts)
        run.mapping_store.fail_delivery(reserved.id,unknown=True,now=run.now_ts)
        world.events.extend([old,fresh]);world.relay_events.extend([old,fresh])
        for _ in range(2):
            with self.assertRaises(base.FGS.GroupSyncError):adapter.catch_up()
        self.assertEqual(run.mapping_store.delivery_record(reserved.id)['status'],'unknown')
        self.assertEqual(len([a for a,_ in world.bot_calls if '--content' in a]),1)
        self.assertEqual(run.mapping_store.delivery_by_source('test',fresh['id'],'b2f',agent_id=base.AGENT2_PK)['status'],'acked')

    def test_metadata_failure_aborts_before_advancing_later_source(self):
        world,run,adapter=self.assembly()
        old=self.event(tags=[['e','a'*64,'','root']],created=run.now_ts-10);fresh=self.event(content='later')
        world.events.extend([old,fresh]);world.relay_events.extend([old,fresh])
        with mock.patch.object(fixture.outlet.outlet_work,'capture',side_effect=StoreError('fixed')):
            with self.assertRaises(StoreError):adapter.catch_up()
        self.assertIsNone(run.mapping_store.delivery_by_source('test',fresh['id'],'b2f',agent_id=base.AGENT2_PK))
        self.assertFalse(any('--content' in a for a,_ in world.bot_calls))

    def test_prebaseline_source_is_not_captured_or_newly_delivered(self):
        world,run,adapter=self.assembly();adapter.initial_since=run.now_ts-10
        run.mapping_store.conn.execute('INSERT INTO cursor VALUES(?,?,?,?,?)',
            ('test',base.AGENT2_PK,'relay',run.now_ts,run.now_ts))
        old=self.event(tags=[['e','a'*64,'','root']],created=run.now_ts-20)
        world.events.append(old);world.relay_events.append(old)
        adapter.http=self.history_http(adapter,[old]);adapter.catch_up()
        self.assertEqual(pending(run.mapping_store,'test',base.CHANNEL,base.AGENT2_PK),[])
        self.assertFalse(any('--content' in a for a,_ in world.bot_calls))

    def test_pending_metadata_never_bypasses_current_revoked_grant(self):
        world,run,adapter,root,old,fresh=self.blocked_pair()
        world.events.append(root)
        run.mapping_store.conn.execute("UPDATE agent_chat SET status='retired' WHERE agent_id=?",(base.AGENT2_PK,))
        with self.assertRaises(base.FGS.GroupSyncError):adapter.catch_up()
        self.assertIsNone(run.mapping_store.delivery_by_source('test',old['id'],'b2f',agent_id=base.AGENT2_PK))
        self.assertEqual(len(pending(run.mapping_store,'test',base.CHANNEL,base.AGENT2_PK)),1)
        self.assertEqual(len([a for a,_ in world.bot_calls if '--content' in a]),1)

    def test_changed_author_channel_or_signature_cannot_enter_metadata(self):
        world,run,adapter=self.assembly();adapter.verify()
        foreign=self.event(tags=[],key=base.MIRROR_KEY)
        channel=base.FGS.sign_event(base.AGENT2_KEY,9,[['h','foreign']], 'private content',run.now_ts)
        bad=dict(self.event());bad['content']='tampered secret'
        for event in (foreign,channel,bad):
            with self.assertRaises(StoreError):remember(run.mapping_store,'test',base.CHANNEL,base.AGENT2_PK,event,floor=0)
        self.assertEqual(pending(run.mapping_store,'test',base.CHANNEL,base.AGENT2_PK),[])

    def test_metadata_does_not_store_body_and_is_immutable_bounded(self):
        world,run,adapter=self.assembly();adapter.verify()
        event=self.event(content='private sentinel must not enter metadata')
        remember(run.mapping_store,'test',base.CHANNEL,base.AGENT2_PK,event,floor=0)
        rows=pending(run.mapping_store,'test',base.CHANNEL,base.AGENT2_PK)
        self.assertNotIn(event['content'],repr(rows))
        with self.assertRaises(sqlite3.IntegrityError):run.mapping_store.conn.execute('UPDATE outlet_pending SET source_at=source_at+1')
        for i in range(255):
            run.mapping_store.conn.execute('INSERT INTO outlet_pending VALUES(?,?,?,?,?,?,?)',
                ('test',base.AGENT2_PK,format(i,'064x'),base.CHANNEL,9,run.now_ts,0))
        with self.assertRaises(StoreError):remember(run.mapping_store,'test',base.CHANNEL,base.AGENT2_PK,self.event(content='overflow'),floor=0)
        self.assertEqual(len(pending(run.mapping_store,'test',base.CHANNEL,base.AGENT2_PK)),256)

    def test_successful_no_effect_ack_replay_prunes_stale_pending(self):
        world,run,adapter=self.assembly();event=self.event();world.events.append(event);world.relay_events.append(event)
        adapter.deliver(event);remember(run.mapping_store,'test',base.CHANNEL,base.AGENT2_PK,event,floor=0)
        adapter.catch_up()
        self.assertEqual(pending(run.mapping_store,'test',base.CHANNEL,base.AGENT2_PK),[])
        self.assertEqual(len([a for a,_ in world.bot_calls if '--content' in a]),1)

    def test_schema15_upgrade_preserves_pause_and_delivery_rows(self):
        world,run,adapter=self.assembly();adapter.verify()
        db=run.mapping_store
        op=db.reserve_console_operation('a'*64,'b'*64,'binding','test','pause',execution_epoch='c'*64,now=run.now_ts).record
        db.conn.execute("INSERT INTO console_pause VALUES(?,?,?,?,?,?,?,'stopping',NULL)",
            ('test',op.id,'d'*64,1,1,'e'*64,run.now_ts))
        pause=[tuple(row) for row in db.conn.execute('SELECT * FROM console_pause')]
        operations=[tuple(row) for row in db.conn.execute('SELECT * FROM console_operation')]
        db.conn.execute('DROP TABLE join_adoption');db.conn.execute('DROP TABLE feishu_scan_token');db.conn.execute('DROP TABLE feishu_scan');db.conn.execute('DROP TABLE feishu_ingest');db.conn.execute('DROP TABLE outlet_work_turn');db.conn.execute('DROP TABLE outlet_work');db.conn.execute('DROP TABLE outlet_pending');db.conn.execute('PRAGMA user_version=15')
        with Store(db.path) as upgraded:
            self.assertEqual(upgraded.conn.execute('PRAGMA user_version').fetchone()[0],19)
            self.assertEqual([tuple(row) for row in upgraded.conn.execute('SELECT * FROM console_pause')],pause)
            self.assertEqual([tuple(row) for row in upgraded.conn.execute('SELECT * FROM console_operation')],operations)
            self.assertEqual(pending(upgraded,'test',base.CHANNEL,base.AGENT2_PK),[])

    def test_skipped_edit_prunes_watermark_without_patch_or_fabricated_ack(self):
        world,run,adapter=self.assembly();adapter.verify()
        edit=self.event(kind=40003,tags=[['e','a'*64]],content='superseded')
        row=run.mapping_store.reserve_delivery('test',edit['id'],'e2f',agent_id=base.AGENT2_PK,source_at=edit['created_at'],now=run.now_ts)
        run.mapping_store.settle_delivery(row.id,outcome='skipped',now=run.now_ts)
        remember(run.mapping_store,'test',base.CHANNEL,base.AGENT2_PK,edit,floor=0)
        world.events.append(edit);world.relay_events.append(edit)
        adapter.catch_up()
        self.assertEqual(pending(run.mapping_store,'test',base.CHANNEL,base.AGENT2_PK),[])
        self.assertEqual(run.mapping_store.delivery_record(row.id)['status'],'skipped')
        self.assertFalse(any('--content' in a for a,_ in world.bot_calls))

    def test_removed_reaction_prunes_watermark_without_second_native_create(self):
        world,run,adapter=self.assembly();root=self.event();world.events.append(root)
        adapter.deliver(root)
        reaction=self.event(kind=7,tags=[['e',root['id']]],content='👍');adapter.deliver(reaction)
        deletion=self.event(kind=5,tags=[['e',reaction['id']]],content='');adapter.deliver(deletion)
        remember(run.mapping_store,'test',base.CHANNEL,base.AGENT2_PK,reaction,floor=0)
        world.events.extend([reaction,deletion]);world.relay_events.extend([reaction,deletion])
        before=len([a for a,_ in world.bot_calls if '--content' in a or 'create' in a or 'DELETE' in a])
        adapter.catch_up()
        self.assertEqual(pending(run.mapping_store,'test',base.CHANNEL,base.AGENT2_PK),[])
        self.assertEqual(len([a for a,_ in world.bot_calls if '--content' in a or 'create' in a or 'DELETE' in a]),before)

if __name__=='__main__':unittest.main()
