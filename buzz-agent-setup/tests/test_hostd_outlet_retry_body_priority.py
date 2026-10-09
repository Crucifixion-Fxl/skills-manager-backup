"""Persisted body retries share the text preference and its bounded aging turn."""
import unittest
import test_hostd_outlet_body_lane as fixture

base=fixture.base
work=fixture.work
causal=fixture.causal
setUpModule=fixture.setUpModule
tearDownModule=fixture.tearDownModule


class RetryBodyPriority(base.TmpCase):
    assembly=fixture.BodyLane.assembly
    event=fixture.BodyLane.event
    put=fixture.BodyLane.put
    own_record=fixture.BodyLane.own_record
    sends=fixture.BodyLane.sends
    capture=fixture.BodyLane.capture
    choose=fixture.BodyLane.choose

    def attempted(self,run,events,ago):
        self.capture(run,events)
        ids={e['id'] for e in events}
        for row in work.pending(run.mapping_store,'test',base.CHANNEL,base.AGENT2_PK):
            if row['source_id'] in ids:
                work.start(run.mapping_store,'test',base.AGENT2_PK,row,0,now=run.now_ts-ago)

    def test_attempted_reply_is_delivered_ahead_of_many_receiptless_withdrawals(self):
        world,run,worker,_=self.assembly()
        root=self.event(content='existing mapped root',created=run.now_ts-40)
        self.put(world,root);worker.run({'buzz'})
        self.assertEqual(self.own_record(run,root)['status'],'acked')
        withdrawals=[self.event(kind=5,tags=[['e',f'{i+1:064x}']],content='',created=run.now_ts-30+i)
                     for i in range(12)]
        body=self.event(tags=[['e',root['id'],'','root']],content='previously attempted reply',created=run.now_ts-10)
        for event in [*withdrawals,body]:self.put(world,event)
        self.attempted(run,withdrawals,1000);self.attempted(run,[body],500)
        worker.run({'buzz'})
        record=self.own_record(run,body)
        self.assertIsNotNone(record,'already-attempted text must receive the preferred slice')
        self.assertEqual(record['status'],'acked')
        self.assertEqual(record['root_id'],self.own_record(run,root)['target_id'])
        self.assertEqual(len(self.sends(world)),2)  # Existing root plus one reply; max_events stays one.
        for event in withdrawals:self.assertIsNone(self.own_record(run,event,'r2f'))

    def test_attempted_text_turns_persist_and_eighth_services_old_withdrawal(self):
        _,run,_,_=self.assembly()
        withdrawal=self.event(kind=5,tags=[['e','a'*64]],content='',created=run.now_ts-200)
        bodies=[self.event(content='retry '+str(i),created=run.now_ts-100+i) for i in range(9)]
        self.attempted(run,[withdrawal],1000);self.attempted(run,bodies,500)
        events=causal([withdrawal,*bodies])
        for expected_turn in range(1,8):
            row,turn=self.choose(run,events)
            self.assertEqual(row['kind'],9)
            self.assertEqual(turn,expected_turn)
            work.start(run.mapping_store,'test',base.AGENT2_PK,row,turn,now=run.now_ts)
        with fixture.fixture.Store(run.mapping_store.path) as reopened:
            row,turn=work.choose(reopened,'test',base.CHANNEL,base.AGENT2_PK,events,now=run.now_ts)
            self.assertEqual(row['source_id'],withdrawal['id'])
            self.assertEqual(turn,0)

    def test_mixed_fresh_text_and_retry_text_has_bounded_oldest_service(self):
        _,run,_,_=self.assembly()
        retry=self.event(content='old attempted body',created=run.now_ts-200)
        withdrawal=self.event(kind=5,tags=[['e','a'*64]],content='',created=run.now_ts-100)
        fresh=[self.event(content='fresh '+str(i),created=run.now_ts+i) for i in range(9)]
        self.attempted(run,[retry],1000);self.attempted(run,[withdrawal],500);self.capture(run,fresh)
        events=causal([retry,withdrawal,*fresh])
        for _ in range(7):
            row,turn=self.choose(run,events)
            self.assertEqual(row['attempts'],0);self.assertEqual(row['kind'],9)
            work.start(run.mapping_store,'test',base.AGENT2_PK,row,turn,now=run.now_ts)
            work.forget(run.mapping_store,'test',base.AGENT2_PK,row['source_id'])
        row,turn=self.choose(run,events)
        self.assertEqual(row['source_id'],retry['id']);self.assertEqual(turn,0)

    def test_attempted_root_blocks_its_fresh_edit_until_due_and_settled(self):
        _,run,_,_=self.assembly()
        root=self.event(content='root',created=run.now_ts-30)
        edit=self.event(kind=40003,tags=[['e',root['id']]],content='edit',created=run.now_ts-20)
        reaction=self.event(kind=7,tags=[['e','a'*64]],content='👍',created=run.now_ts-10)
        self.attempted(run,[root],100);self.capture(run,[edit,reaction])
        events=causal([root,edit,reaction])
        row,turn=self.choose(run,events)
        self.assertEqual(row['source_id'],root['id'])  # Fresh reaction does not preempt due body retry.
        work.start(run.mapping_store,'test',base.AGENT2_PK,row,turn,now=run.now_ts)
        self.assertEqual(self.choose(run,events)[0]['source_id'],reaction['id'])
        work.forget(run.mapping_store,'test',base.AGENT2_PK,reaction['id'])
        self.assertIsNone(self.choose(run,events))  # Backoff is never bypassed by the fresh edit.
        self.assertIsNone(self.choose(run,[edit]))  # Absent captured root is not settled.
        work.forget(run.mapping_store,'test',base.AGENT2_PK,root['id'])
        self.assertEqual(self.choose(run,events)[0]['source_id'],edit['id'])

    def test_reaction_only_retries_keep_due_order_and_create_dependency(self):
        _,run,_,_=self.assembly()
        reaction=self.event(kind=7,tags=[['e','a'*64]],content='👍',created=run.now_ts-10)
        removal=self.event(kind=5,tags=[['e',reaction['id']]],content='',created=run.now_ts-20)
        other=self.event(kind=5,tags=[['e','b'*64]],content='',created=run.now_ts-30)
        self.attempted(run,[reaction,removal],500);self.attempted(run,[other],1000)
        events=causal([removal,reaction,other])
        row,turn=self.choose(run,events);self.assertEqual(row['source_id'],other['id']);self.assertEqual(turn,0)
        work.start(run.mapping_store,'test',base.AGENT2_PK,row,turn,now=run.now_ts)
        row,turn=self.choose(run,events);self.assertEqual(row['source_id'],reaction['id'])
        work.start(run.mapping_store,'test',base.AGENT2_PK,row,turn,now=run.now_ts)
        self.assertIsNone(self.choose(run,events))


if __name__=='__main__':unittest.main()
