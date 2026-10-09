"""Text/edit work cannot depend on an unrelated reaction in the same topic."""
import unittest
import test_hostd_worker_outlets as fixture

base=fixture.base
setUpModule=fixture.setUpModule
tearDownModule=fixture.tearDownModule
work=fixture.bw.ao.outlet_work
causal=fixture.bw.ao.reaction_inbox.causal_order


class BodyLane(base.TmpCase):
    assembly=fixture.WorkerOutlets.assembly
    event=fixture.WorkerOutlets.event
    put=fixture.WorkerOutlets.put
    own_record=fixture.WorkerOutlets.own_record
    sends=fixture.WorkerOutlets.sends

    def capture(self,run,events):
        work.capture(run.mapping_store,'test',base.CHANNEL,base.AGENT2_PK,events,
                     floor=0,now=run.now_ts,retained=set())

    def choose(self,run,events):
        return work.choose(run.mapping_store,'test',base.CHANNEL,base.AGENT2_PK,causal(events),now=run.now_ts)

    def test_failed_reaction_does_not_block_body_edit_order(self):
        self.scenario(False)

    def test_missing_reaction_does_not_block_body_edit_order(self):
        self.scenario(True)

    def scenario(self,missing):
        world,run,worker,_=self.assembly()
        # Legacy fake reply transport omits native root metadata/card body.
        # Supply those GET fields from the actual fake POST result; no proof
        # or production mapping/authority method is mocked.
        original_get=world._message_item
        def reply_card(mid,id_type):
            item=original_get(mid,id_type)
            for root_mid,replies in world.threads.items():
                message=next((m for m in replies if m['message_id']==mid),None)
                if item is not None and message is not None:
                    item.update(root_id=root_mid,parent_id=root_mid,
                                body={'content':message['content']})
            return item
        world._message_item=reply_card
        root=self.event(content='actual root',created=run.now_ts-40)
        self.put(world,root);worker.run({'buzz'})
        self.assertEqual(self.own_record(run,root)['status'],'acked')
        reaction=self.event(kind=7,tags=[['e',root['id']]],content='unsupported-fixture-reaction',created=run.now_ts-30)
        self.put(world,reaction);worker.run({'buzz'})
        queued=next(r for r in work.pending(run.mapping_store,'test',base.CHANNEL,base.AGENT2_PK) if r['source_id']==reaction['id'])
        self.assertEqual(queued['attempts'],1)
        first=self.event(tags=[['e',root['id'],'','root']],content='first body',created=run.now_ts-20)
        edit=self.event(kind=40003,tags=[['e',first['id']]],content='first body edited',created=run.now_ts-15)
        second=self.event(tags=[['e',root['id'],'','root']],content='second body',created=run.now_ts-10)
        removal=self.event(kind=5,tags=[['e',reaction['id']]],content='',created=run.now_ts-5)
        for event in (first,edit,second,removal):self.put(world,event)
        if missing:
            world.events[:]=[e for e in world.events if e['id']!=reaction['id']]
            world.relay_events[:]=[e for e in world.relay_events if e['id']!=reaction['id']]
        # Production Worker still admits one source per author visit.
        worker.run({'buzz'})
        first_record=self.own_record(run,first)
        self.assertIsNotNone(first_record,'unrelated reaction must not withhold the body source')
        self.assertEqual(first_record['status'],'acked')
        self.assertIsNone(self.own_record(run,edit,'e2f'))
        self.assertIsNone(self.own_record(run,second))
        report=worker.run({'buzz'})
        self.assertIsNotNone(self.own_record(run,edit,'e2f'), str(report))
        self.assertEqual(self.own_record(run,edit,'e2f')['status'],'acked')
        self.assertIsNone(self.own_record(run,second))
        worker.run({'buzz'})
        self.assertEqual(self.own_record(run,second)['status'],'acked')
        self.assertIsNone(self.own_record(run,removal,'r2f'))
        self.assertEqual(len(self.sends(world)),3)  # root + two replies, edit is PATCH.

    def test_fresh_body_precedes_newer_independent_reaction(self):
        _,run,_,_=self.assembly()
        body=self.event(content='body',created=run.now_ts-30)
        reaction=self.event(kind=7,tags=[['e','a'*64]],content='👍',created=run.now_ts)
        self.capture(run,[body,reaction])
        self.assertEqual(self.choose(run,[body,reaction])[0]['source_id'],body['id'])

    def test_eighth_all_head_turn_services_old_due_reaction_after_restart(self):
        _,run,_,_=self.assembly()
        reaction=self.event(kind=7,tags=[['e','a'*64]],content='👍',created=run.now_ts-200)
        bodies=[self.event(content='new body '+str(i),created=run.now_ts+i) for i in range(10)]
        events=[reaction,*bodies];self.capture(run,events)
        row=next(r for r in work.pending(run.mapping_store,'test',base.CHANNEL,base.AGENT2_PK) if r['source_id']==reaction['id'])
        work.start(run.mapping_store,'test',base.AGENT2_PK,row,0,now=run.now_ts-100)
        for _ in range(7):
            selected,turn=self.choose(run,events)
            self.assertEqual(selected['kind'],9)
            work.start(run.mapping_store,'test',base.AGENT2_PK,selected,turn,now=run.now_ts)
            work.forget(run.mapping_store,'test',base.AGENT2_PK,selected['source_id'])
        with fixture.Store(run.mapping_store.path) as reopened:
            chosen,turn=work.choose(reopened,'test',base.CHANNEL,base.AGENT2_PK,causal(events),now=run.now_ts)
            self.assertEqual(chosen['source_id'],reaction['id'])
            self.assertEqual(turn,0)

    def test_withdrawal_cannot_pass_own_create_even_if_timestamp_reversed(self):
        _,run,_,_=self.assembly()
        reaction=self.event(kind=7,tags=[['e','a'*64]],content='👍',created=run.now_ts)
        removal=self.event(kind=5,tags=[['e',reaction['id']]],content='',created=run.now_ts-1)
        self.capture(run,[reaction,removal])
        chosen,turn=self.choose(run,[removal,reaction])
        self.assertEqual(chosen['source_id'],reaction['id'])
        work.start(run.mapping_store,'test',base.AGENT2_PK,chosen,turn,now=run.now_ts)
        self.assertIsNone(self.choose(run,[removal,reaction]))
        self.assertIsNone(self.choose(run,[removal]))

    def test_missing_text_head_still_blocks_its_edit_and_reply(self):
        _,run,_,_=self.assembly()
        first=self.event(tags=[['e','a'*64,'','root']],content='earlier reply',created=run.now_ts-20)
        edit=self.event(kind=40003,tags=[['e',first['id']]],content='edit',created=run.now_ts-10)
        later=self.event(tags=[['e','a'*64,'','root']],content='later reply',created=run.now_ts)
        self.capture(run,[first,edit,later])
        self.assertIsNone(self.choose(run,[edit,later]))
