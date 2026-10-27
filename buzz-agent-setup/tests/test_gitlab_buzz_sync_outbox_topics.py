"""Strict standalone pipeline UNKNOWN retains its ledger while proven independent objects progress."""
import copy
import hashlib
import json
import subprocess
import unittest
import test_gitlab_buzz_sync_delivery as delivery
from test_gitlab_buzz_sync_delivery import FAKES, SYNC, PID, CHANNEL, make_issue


SIGNING_SECRET = bytes.fromhex('01' * 32)
SIGNING_PUBKEY = SYNC.nk.pubkey_xonly(SIGNING_SECRET).hex()


def sign_fixture_event(event):
    event['pubkey'] = SIGNING_PUBKEY
    encoded = json.dumps([0,event['pubkey'],event['created_at'],event['kind'],event['tags'],event['content']],
                         ensure_ascii=False,separators=(',',':')).encode()
    digest = hashlib.sha256(encoded).digest()
    event['id'] = digest.hex()
    event['sig'] = SYNC.nk.schnorr_sign(digest,SIGNING_SECRET,bytes(32)).hex()
    return event


def enable_signed_transport(case):
    # This is the fake external transport, not a replacement of a product
    # authorization/readback method. Native events are actually signed.
    case.config['publisher_pubkey'] = SIGNING_PUBKEY
    send = case.buzz.send
    def signed_send(*args,**kwargs):
        send(*args,**kwargs)
        return sign_fixture_event(case.buzz.events[-1])['id']
    case.buzz.send = signed_send


def set_up_signed(case):
    delivery.DeliveryStateTest.setUp(case)
    enable_signed_transport(case)


class OutboxTopicsTest(unittest.TestCase):
    setUp = set_up_signed
    syncer = delivery.DeliveryStateTest.syncer
    def pending_pipeline(self, **overrides):
        syncer = self.syncer()
        payload = {"content": f"Pipeline failed\n[gitlab-notify:v1][object:pipeline][event:failed][project:{PID}][events:pipeline-246366-failed]",
                   "mentions": [], "reply_to": None, "project_id": None}
        key = syncer._queue_delivery("buzz_message", payload)
        syncer._mark_delivery_attempted(key)
        ledger = syncer._read_outbox()
        ledger["pending"][0].update(overrides)
        syncer._write_outbox(ledger)
        return syncer, copy.deepcopy(ledger["pending"][0])

    def existing_issue(self):
        self.gitlab.issue_list[PID] = [make_issue(186)]
        self.syncer().run()
        root = self.buzz.events[0]["id"]
        self.buzz.writes.clear()
        self.gitlab.writes.clear()
        self.gitlab.add_user_note(186, 666491, "Independent progress comment")
        return root

    def test_existing_comment_progress_preserves_unknown_and_cursor(self):
        root = self.existing_issue()
        syncer, pending = self.pending_pipeline()
        cache = syncer.cache_path().read_bytes()
        summary = syncer.run()
        self.assertEqual(summary["status"], "degraded")
        self.assertEqual(len(self.buzz.writes), 1)
        self.assertEqual(self.buzz.writes[0][1], root)
        self.assertEqual(SYNC.parse_header(self.buzz.writes[0][2])["note"], 666491)
        self.assertEqual(syncer._read_outbox()["pending"], [pending])
        self.assertEqual(syncer.cache_path().read_bytes(), cache)
        self.assertEqual(self.gitlab.writes, [])
        syncer.run()
        self.assertEqual(len(self.buzz.writes), 1)

    def test_existing_attempted_direct_action_is_never_called(self):
        syncer, item = self.pending_pipeline()
        syncer._project_visibilities = {PID: "public"}
        called = []
        with self.assertRaises(SYNC.SyncError):
            syncer._deliver(item["kind"], item["payload"], lambda: called.append(True))
        self.assertEqual(called, [])
        self.assertEqual(syncer._read_outbox()["pending"], [item])

    def test_existing_attempted_queued_action_is_never_called(self):
        syncer, item = self.pending_pipeline()
        syncer._project_visibilities = {PID: "public"}
        with self.assertRaises(SYNC.SyncError):
            syncer._execute_queued_delivery(item)
        self.assertEqual(self.buzz.writes, [])
        self.assertEqual(syncer._read_outbox()["pending"], [item])

    def test_unattempted_queued_action_still_runs_once(self):
        syncer, item = self.pending_pipeline(attempted=False)
        syncer.run()
        self.assertEqual(len(self.buzz.writes), 1)
        self.assertEqual(syncer._read_outbox()["pending"], [])
        self.assertEqual(syncer._read_outbox()["acked"][0]["change_id"], item["change_id"])

    def test_new_root_and_independent_pipeline_progress_with_frozen_cursor(self):
        self.gitlab.issue_list[PID] = [make_issue(186)]
        self.gitlab.projects[PID]['default_branch'] = 'main'
        self.gitlab.pipelines = lambda *_: [
            {'id': n, 'status': 'failed', 'ref': 'main', 'updated_at': FAKES.SERVER_TIME,
             'web_url': FAKES.WEB + '/-/pipelines/' + str(n)} for n in (246366, 246367)]
        self.buzz.channel_messages = lambda _: list(self.buzz.events)
        syncer, item = self.pending_pipeline()
        summary = syncer.run()
        self.assertEqual(summary['status'], 'degraded')
        self.assertEqual(summary['created'], 1)
        self.assertEqual(summary['notified']['instant'], 1)
        self.assertEqual(len(self.gitlab.writes), 1)
        self.assertFalse(syncer.cache_path().exists())
        self.assertEqual(syncer._read_outbox()['pending'], [item])
        self.assertFalse(any('pipeline-246366-' in text for _,_,text in self.buzz.writes))
        writes = list(self.buzz.writes)
        self.syncer().run()
        self.assertEqual(self.buzz.writes, writes)

    def test_new_direct_action_cannot_bypass_unknown(self):
        syncer, item = self.pending_pipeline()
        syncer._project_visibilities = {PID: "public"}
        payload = dict(item["payload"], content="Changed presentation\n" + item["payload"]["content"])
        with self.assertRaises(SYNC.SyncError):
            syncer._deliver("buzz_message", payload, lambda: self.fail("must not dispatch"))
        self.assertEqual(syncer._read_outbox()["pending"], [item])

    def test_grouped_unknown_remains_global_barrier(self):
        self.existing_issue()
        syncer, item = self.pending_pipeline(group_id="a" * 64, group_index=0)
        with self.assertRaisesRegex(SYNC.SyncError, "still pending"):
            syncer.run()
        self.assertEqual(self.buzz.writes, [])
        self.assertEqual(syncer._read_outbox()["pending"], [item])

    def test_unknown_scope_remains_global_barrier(self):
        self.existing_issue()
        syncer, item = self.pending_pipeline()
        ledger = syncer._read_outbox()
        ledger["pending"][0]["payload"]["content"] = "unclassified prior notification"
        ledger["pending"][0]["change_id"] = SYNC.delivery_change_id(
            CHANNEL, "buzz_message", ledger["pending"][0]["payload"])
        syncer._write_outbox(ledger)
        with self.assertRaisesRegex(SYNC.SyncError, "still pending"):
            syncer.run()
        self.assertEqual(self.buzz.writes, [])
        self.assertEqual(syncer._read_outbox(), ledger)

    def test_mixed_queued_dependency_stays_blocked(self):
        self.existing_issue()
        syncer, item = self.pending_pipeline()
        syncer._queue_delivery("buzz_message", dict(item["payload"], reply_to="f" * 64))
        before = syncer._read_outbox()
        with self.assertRaisesRegex(SYNC.SyncError, "blocked"):
            syncer.run()
        self.assertEqual(self.buzz.writes, [])
        self.assertEqual(syncer._read_outbox(), before)

    def test_readback_of_unknown_uses_original_ack_then_normal_run(self):
        syncer, item = self.pending_pipeline()
        self.buzz.send(item["payload"]["content"])
        self.buzz.writes.clear()
        self.buzz.channel_messages = lambda _: self.buzz.events
        summary = syncer.run()
        self.assertEqual(summary["status"], "ok")
        self.assertEqual(syncer._read_outbox()["pending"], [])
        self.assertTrue(syncer._read_outbox()["acked"][0]["recovered"])
        self.assertEqual(self.buzz.writes, [])

    def test_state_change_must_precede_comment(self):
        self.existing_issue()
        self.gitlab.issue_list[PID][0]["state"] = "closed"
        syncer, item = self.pending_pipeline()
        syncer.run()
        headers = [SYNC.parse_header(text) for _, _, text in self.buzz.writes]
        self.assertEqual([h['change'] for h in headers], ['routing', 'activity'])
        self.assertEqual(headers[0]['state'], 'closed')
        self.assertEqual(syncer._read_outbox()["pending"], [item])

    def test_non_bot_binding_requires_native_recovery_and_real_binding(self):
        self.existing_issue()
        self.gitlab.note_list[(PID, 186)][0]["author"]["id"] = 99
        syncer, item = self.pending_pipeline()
        summary = syncer.run()
        self.assertEqual(summary['recovered'], 1)
        self.assertEqual(len(self.gitlab.writes), 1)
        self.assertEqual(len(self.buzz.writes), 1)
        self.assertEqual(syncer._read_outbox()['pending'], [item])

    def test_foreign_or_pipeline_root_cannot_authorize_comment(self):
        for variant in ("author", "channel", "pipeline", "reply"):
            with self.subTest(variant=variant):
                self.setUp()
                self.existing_issue()
                syncer, item = self.pending_pipeline()
                root = self.buzz.events[0]
                if variant == "author":
                    root["pubkey"] = "a" * 64
                elif variant == "channel":
                    root["tags"] = [["h", "00000000-0000-4000-8000-0000000000ff"]]
                elif variant == "reply":
                    root["tags"].append(["e", "f" * 64, "", "reply"])
                else:
                    root["content"] = item["payload"]["content"]
                try:
                    syncer.run()
                except SYNC.SyncError:
                    pass  # Unreadable/mismatched root is deliberately fail-closed.
                self.assertEqual(self.buzz.writes, [])

    def test_new_comment_unknown_preserved_and_never_retried(self):
        self.existing_issue()
        syncer, original = self.pending_pipeline()
        calls = []
        def lose_ack(*args, **kwargs):
            calls.append(True)
            raise SYNC.SyncError("simulated transport UNKNOWN")
        self.buzz.send = lose_ack
        with self.assertRaisesRegex(SYNC.SyncError, "transport UNKNOWN"):
            syncer.run()
        pending = syncer._read_outbox()["pending"]
        self.assertEqual(len(pending), 2)
        self.assertEqual(pending[0], original)
        self.assertTrue(pending[1]["attempted"])
        self.assertIsNone(syncer._independent_comment_id)
        with self.assertRaisesRegex(SYNC.SyncError, "still pending"):
            self.syncer().run()
        self.assertEqual(calls, [True])
        self.assertEqual(syncer._read_outbox()["pending"], pending)

    def test_strict_pipeline_scope_rejects_ambiguous_variants(self):
        syncer, item = self.pending_pipeline()
        self.assertEqual(syncer._isolatable_pipeline(item), PID)
        variants = []
        for key, value in [("attempted", None), ("status", "UNKNOWN"), ("kind", "buzz_edit"),
                           ("group_id", None), ("group_index", None), ("change_id", "f" * 64)]:
            variant = copy.deepcopy(item)
            variant[key] = value
            variants.append(variant)
        for key, value in [("reply_to", "a" * 64), ("project_id", 999), ("mentions", ["not-pubkey"]),
                           ("content", item["payload"]["content"].replace("event:failed", "event:success")),
                           ("content", item["payload"]["content"].replace("246366-failed", "0-failed")),
                           ("content", item["payload"]["content"].replace("246366-failed", "246366-failed,pipeline-2-failed"))]:
            variant = copy.deepcopy(item)
            variant["payload"][key] = value
            variant["change_id"] = SYNC.delivery_change_id(CHANNEL, "buzz_message", variant["payload"])
            variants.append(variant)
        for variant in variants:
            self.assertIsNone(syncer._isolatable_pipeline(variant))

    def test_all_direct_mutation_kinds_guard_existing_attempted_id(self):
        for kind in ("buzz_message", "buzz_edit", "buzz_reaction", "buzz_diff", "gitlab_note"):
            with self.subTest(kind=kind):
                self.setUp()
                syncer = self.syncer()
                payload = {"project_id": PID, "content": "test"}
                key = syncer._queue_delivery(kind, payload)
                syncer._mark_delivery_attempted(key)
                before = syncer._read_outbox()
                with self.assertRaisesRegex(SYNC.SyncError, "already attempted"):
                    syncer._deliver(kind, payload, lambda: self.fail("must not dispatch"))
                self.assertEqual(syncer._read_outbox(), before)

    def test_queued_item_with_forged_change_id_is_not_dispatched(self):
        syncer, item = self.pending_pipeline(attempted=False)
        forged = dict(item, change_id="f" * 64)
        with self.assertRaises(SYNC.SyncError):
            syncer._execute_queued_delivery(forged)
        self.assertEqual(self.buzz.writes, [])
        self.assertEqual(syncer._read_outbox()["pending"], [item])

    def test_scope_does_not_leak_after_pending_recovery(self):
        self.existing_issue()
        syncer, item = self.pending_pipeline()
        syncer.run()
        self.assertEqual(len(self.buzz.writes), 1)
        self.buzz.send(item["payload"]["content"])
        self.buzz.channel_messages = lambda _: self.buzz.events
        self.gitlab.issue_list[PID].append(make_issue(187))
        summary = syncer.run()
        self.assertEqual(summary["status"], "ok")
        self.assertEqual(summary["created"], 1)
        self.assertEqual(syncer._isolated_pending_projects, set())
        self.assertIsNone(syncer._independent_comment_id)

    def test_restricted_comment_failure_definite_zero_write_keeps_only_old_pending(self):
        self.existing_issue()
        syncer, original = self.pending_pipeline()
        def rejected(*args, **kwargs):
            raise SYNC.BuzzSendRejected("messages send", 1)
        self.buzz.send = rejected
        with self.assertRaises(SYNC.BuzzSendRejected):
            syncer.run()
        self.assertEqual(syncer._read_outbox()["pending"], [original])
        self.assertIsNone(syncer._independent_comment_id)

    def test_unattempted_before_unknown_cannot_cross_global_barrier(self):
        self.existing_issue()
        syncer, item = self.pending_pipeline()
        syncer._queue_delivery("buzz_message", dict(item["payload"], reply_to="f" * 64))
        ledger = syncer._read_outbox()
        ledger["pending"].reverse()
        syncer._write_outbox(ledger)
        with self.assertRaises(SYNC.SyncError):
            syncer.run()
        self.assertEqual(self.buzz.writes, [])
        self.assertEqual(syncer._read_outbox(), ledger)

    def test_foreign_origin_preserves_causal_barrier(self):
        self.existing_issue()
        syncer, item = self.pending_pipeline()
        syncer._resolve_origin_roots = lambda *args, **kwargs: ["f" * 64]
        with self.assertRaisesRegex(SYNC.SyncError, 'blocked|signature'):
            syncer.run()
        self.assertEqual(self.buzz.writes, [])
        self.assertEqual(syncer._read_outbox()["pending"], [item])

    def test_pipeline_header_cannot_disguise_root_as_issue_plaque(self):
        root = self.existing_issue()
        original_fact = self.buzz.events[0]["content"]
        syncer, item = self.pending_pipeline()
        self.buzz.events[0]["content"] = (
            item["payload"]["content"].splitlines()[-1] + "\nPipeline\n" + make_issue(186)["web_url"])
        self.buzz.send(original_fact, reply_to=root)
        self.buzz.writes.clear()
        with self.assertRaisesRegex(SYNC.SyncError, 'blocked|signature'):
            syncer.run()
        self.assertEqual(self.buzz.writes, [])


class PipelineIsolationRecovery(unittest.TestCase):
    setUp = set_up_signed
    syncer = delivery.DeliveryStateTest.syncer
    pending_pipeline = OutboxTopicsTest.pending_pipeline
    existing_issue = OutboxTopicsTest.existing_issue

    def initialized(self, syncer):
        syncer._projects = {PID: self.gitlab.project(PID)}
        syncer._project_visibilities = {PID: 'public'}

    def test_altered_same_pipeline_state_mentions_and_opaque_keys_cannot_dispatch(self):
        syncer, old = self.pending_pipeline()
        self.initialized(syncer)
        variants = [dict(old['payload'], content='new body\n'+old['payload']['content']),
                    dict(old['payload'], mentions=['a'*64]),
                    dict(old['payload'], content=old['payload']['content'].replace('failed','success')),
                    dict(old['payload'], content=old['payload']['content'].replace('pipeline-246366-failed','webhook-0123456789abcdef')),
                    {'content':'project alone is not a scope', 'project_id':PID,'reply_to':None,'mentions':[]}]
        for payload in variants:
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(SYNC.SyncError, 'blocked'):
                    syncer._deliver('buzz_message',payload,lambda:self.fail('unexpected dispatch'))
        self.assertEqual(syncer._read_outbox()['pending'],[old])

    def test_direct_target_mutations_cannot_use_project_or_forged_native_scope(self):
        root = self.existing_issue()
        syncer, old = self.pending_pipeline(); self.initialized(syncer)
        good = copy.deepcopy(self.buzz.events[0])
        for variant in ('absent','author','channel','pipeline','ancestor'):
            self.buzz.events[0] = copy.deepcopy(good)
            target=root
            if variant=='absent': target='f'*64
            elif variant=='author': self.buzz.events[0]['pubkey']='a'*64
            elif variant=='channel': self.buzz.events[0]['tags']=[['h','wrong']]
            elif variant=='pipeline': self.buzz.events[0]['content']=old['payload']['content']
            else: self.buzz.events[0]['tags'].append(['e','f'*64,'','reply'])
            payloads=[('buzz_edit',{'event_id':target,'project_id':PID,'content':'changed'}),
                      ('buzz_reaction',{'event_id':target,'project_id':PID,'emoji':'✅'}),
                      ('buzz_diff',{'reply_to':target,'project_id':PID,'diff':'x'}),
                      ('gitlab_note',{'project_id':PID,'object':'issue','iid':186,'body':SYNC.render_binding_note(
                          {'project_id':PID,'object':'issue','iid':186,'channel_id':CHANNEL,'root_event_id':target},'buzz://test')})]
            for kind,payload in payloads:
                with self.subTest(variant=variant,kind=kind):
                    with self.assertRaisesRegex(SYNC.SyncError,'blocked|signature'):
                        syncer._deliver(kind,payload,lambda:self.fail('unexpected dispatch'))
        self.assertEqual(syncer._read_outbox()['pending'],[old])

    def test_mixed_unattempted_atomic_group_recovers_in_order(self):
        root=self.existing_issue(); self.gitlab.note_list[(PID,186)]=self.gitlab.note_list[(PID,186)][:1]
        syncer,old=self.pending_pipeline()
        fact=SYNC.issue_fact(make_issue(186),PID)
        note1={'id':801,'body':'one','author':{'username':'alice'}}
        note2={'id':802,'body':'two','author':{'username':'alice'}}
        payloads=[{'content':SYNC.render_comment_message(fact,n),'reply_to':root,'mentions':[],'project_id':PID} for n in (note1,note2)]
        keys=syncer._queue_deliveries([('buzz_message',p) for p in payloads])
        # Old held operation may occur before OR after the queued independent chain.
        ledger=syncer._read_outbox(); ledger['pending']=ledger['pending'][1:]+ledger['pending'][:1]; syncer._write_outbox(ledger)
        summary=self.syncer().run()
        self.assertEqual(summary['status'],'degraded')
        self.assertEqual([SYNC.parse_header(t)['note'] for _,_,t in self.buzz.writes],[801,802])
        self.assertEqual(syncer._read_outbox()['pending'],[old])
        self.assertTrue(all(k in [a['change_id'] for a in syncer._read_outbox()['acked']] for k in keys))

    def test_new_group_unknown_holds_continuation_and_restart(self):
        root=self.existing_issue(); self.gitlab.note_list[(PID,186)]=self.gitlab.note_list[(PID,186)][:1]
        syncer,old=self.pending_pipeline(); self.initialized(syncer)
        fact=SYNC.issue_fact(make_issue(186),PID)
        payloads=[{'content':SYNC.render_comment_message(fact,{'id':n,'body':'group','author':{'username':'alice'}}),
                   'reply_to':root,'mentions':[],'project_id':PID} for n in (801,802)]
        syncer._queue_deliveries([('buzz_message',p) for p in payloads]); calls=[]
        def lost(*args,**kwargs): calls.append(True); raise SYNC.SyncError('new UNKNOWN')
        self.buzz.send=lost
        with self.assertRaisesRegex(SYNC.SyncError,'new UNKNOWN'): self.syncer().run()
        pending=syncer._read_outbox()['pending']
        self.assertEqual(pending[0],old); self.assertTrue(pending[1]['attempted']); self.assertFalse(pending[2]['attempted'])
        with self.assertRaisesRegex(SYNC.SyncError,'still pending'): self.syncer().run()
        self.assertEqual(calls,[True]); self.assertEqual(syncer._read_outbox()['pending'],pending)

    def test_new_root_lost_ack_recovers_before_binding_without_duplicate(self):
        self.gitlab.issue_list[PID]=[make_issue(186)]
        syncer,old=self.pending_pipeline(); send=self.buzz.send
        def lost(*args,**kwargs): send(*args,**kwargs); raise SYNC.SyncError('lost root ACK')
        self.buzz.send=lost
        with self.assertRaisesRegex(SYNC.SyncError,'lost root ACK'): syncer.run()
        self.assertEqual(self.gitlab.writes,[])
        self.buzz.send=send; self.syncer().run()
        self.assertEqual(len(self.buzz.events),1); self.assertEqual(len(self.gitlab.writes),1)
        self.assertEqual(syncer._read_outbox()['pending'],[old]); self.assertFalse(syncer.cache_path().exists())
        self.syncer().run(); self.assertEqual(len(self.buzz.events),1)

    def test_readback_survives_ack_cap_and_rejects_incomplete_thread(self):
        root=self.existing_issue(); syncer,old=self.pending_pipeline(); cache=syncer.cache_path().read_bytes()
        self.syncer().run(); sends=len(self.buzz.writes)
        ledger=syncer._read_outbox(); ledger['acked']=[{'status':'ACKED','change_id':f'{n:064x}'} for n in range(SYNC.OUTBOX_ACK_LIMIT)]
        syncer._write_outbox(ledger); self.syncer().run()
        self.assertEqual(len(self.buzz.writes),sends); self.assertEqual(syncer.cache_path().read_bytes(),cache)
        thread=self.buzz.thread
        self.buzz.thread=lambda target: thread(target)+[{'id':f'{n:064x}'} for n in range(SYNC.THREAD_REPLY_LIMIT)]
        self.initialized(syncer)
        with self.assertRaisesRegex(SYNC.SyncError,'incomplete'):
            syncer._deliver('buzz_reaction',{'project_id':PID,'event_id':root,'emoji':'✅'},lambda:self.fail('dispatch'))
        self.assertEqual(syncer._read_outbox()['pending'],[old])

    def test_held_poll_webhook_and_mr_ref_records_excluded_before_grouping(self):
        syncer,old=self.pending_pipeline()
        for key,source,status in [('pipeline-246366-failed',246366,'failed'),('pipeline-246366-success',246366,'success'),
                                  ('webhook-abc123',246366,'failed'),('pipeline-246366-canceled',None,'canceled'),('pipeline-246366-failed',246367,'failed')]:
            record={'object':'pipeline','key':key,'source_id':source,'project':PID,'event':status,'placement':'mr_thread','mr_iid':1152}
            self.assertTrue(syncer._held_pipeline_record(record,PID))
        self.assertFalse(syncer._held_pipeline_record({'object':'pipeline','key':'pipeline-246367-failed','source_id':246367,'project':PID,'event':'failed','placement':'instant'},PID))
        self.assertEqual(syncer._read_outbox()['pending'],[old])

    def test_dispatch_revalidates_held_classifier_instead_of_cached_project(self):
        root=self.existing_issue(); syncer,old=self.pending_pipeline(); self.initialized(syncer)
        ledger=syncer._read_outbox(); ledger['pending'][0]['group_id']='a'*64; ledger['pending'][0]['group_index']=0
        syncer._write_outbox(ledger); syncer._isolated_pending_projects={PID}
        with self.assertRaisesRegex(SYNC.SyncError,'blocked'):
            syncer._deliver('buzz_message',{'content':'safe-looking reply','reply_to':root,'project_id':PID,'mentions':[]},lambda:self.fail('dispatch'))
        self.assertEqual(syncer._read_outbox(),ledger)


class PipelineIsolationMR(unittest.TestCase):
    setUp = set_up_signed
    syncer = delivery.DeliveryStateTest.syncer
    pending_pipeline = OutboxTopicsTest.pending_pipeline

    def test_new_mr_root_binding_fact_comment_and_restart(self):
        import test_gitlab_buzz_sync_mr_issue_merge as mr
        self.gitlab,self.buzz=mr.FakeGitLab(),mr.FakeBuzz()
        enable_signed_transport(self)
        self.gitlab.mr_list=[mr.make_mr(iid=1152,source_branch='fix/standalone')]
        syncer,old=self.pending_pipeline()
        summary=syncer.run(); self.assertEqual(summary['mr_created'],1)
        roots=[e for e in self.buzz.events if not SYNC._tag_values(e,'e')]
        self.assertEqual(len(roots),1)
        self.assertEqual(len(self.gitlab.mr_note_list[1152]),1)
        self.gitlab.mr_note_list[1152].append({'id':987,'body':'independent MR progress','author':{'id':3,'username':'alice'},'system':False,'created_at':mr.SERVER_TIME})
        self.syncer().run(); count=len(self.buzz.writes); self.syncer().run()
        self.assertEqual(len(self.buzz.writes),count)
        self.assertEqual(sum(SYNC.parse_header(e.get('content')).get('note')==987 for e in self.buzz.events if SYNC.parse_header(e.get('content'))),1)
        self.assertEqual(syncer._read_outbox()['pending'],[old]); self.assertFalse(syncer.cache_path().exists())

    def test_mr_binding_crash_recovers_same_root_then_facts(self):
        import test_gitlab_buzz_sync_mr_issue_merge as mr
        self.gitlab,self.buzz=mr.FakeGitLab(),mr.FakeBuzz()
        enable_signed_transport(self)
        self.gitlab.mr_list=[mr.make_mr(iid=1152,source_branch='fix/standalone')]
        syncer,old=self.pending_pipeline(); add_note=self.gitlab.add_mr_note
        def lost(*args,**kwargs): raise SYNC.SyncError('binding UNKNOWN')
        self.gitlab.add_mr_note=lost
        with self.assertRaisesRegex(SYNC.SyncError,'binding UNKNOWN'): syncer.run()
        self.assertEqual(len(self.buzz.events),1)
        self.assertEqual(len(syncer._read_outbox()['pending']),2)
        self.gitlab.add_mr_note=add_note
        self.syncer().run(); count=len(self.buzz.writes); self.syncer().run()
        self.assertEqual(len(self.buzz.writes),count)
        self.assertEqual(len(self.gitlab.mr_note_list[1152]),1)
        self.assertEqual(len([e for e in self.buzz.events if not SYNC._tag_values(e,'e')]),1)
        self.assertEqual(syncer._read_outbox()['pending'],[old])

    def test_old_pipeline_cannot_reappear_as_mr_terminal_record(self):
        import test_gitlab_buzz_sync_mr_issue_merge as mr
        self.gitlab,self.buzz=mr.FakeGitLab(),mr.FakeBuzz()
        enable_signed_transport(self)
        self.gitlab.mr_list=[mr.make_mr(iid=1152,source_branch='fix/standalone')]
        self.gitlab.pipelines=lambda *_:[{'id':246366,'status':'success','ref':'refs/merge-requests/1152/head',
            'updated_at':mr.SERVER_TIME,'web_url':mr.WEB+'/-/pipelines/246366'}]
        syncer,old=self.pending_pipeline(); syncer.run()
        self.assertFalse(any('pipeline-246366-' in e['content'] for e in self.buzz.events))
        self.assertEqual(syncer._read_outbox()['pending'],[old])
        self.assertEqual(len(self.gitlab.mr_note_list[1152]),1)

    def test_filtered_retained_pipeline_group_cannot_materialize_missing_roots(self):
        import test_gitlab_buzz_sync_mr_issue_merge as mr
        self.gitlab,self.buzz=mr.FakeGitLab(),mr.FakeBuzz()
        enable_signed_transport(self)
        self.syncer().run()
        syncer,old=self.pending_pipeline()
        cache=json.loads(syncer.cache_path().read_text())
        record=SYNC.record_from_pipeline({'id':246366,'status':'failed','ref':'refs/merge-requests/1152/head',
            'updated_at':mr.SERVER_TIME,'web_url':mr.WEB+'/-/pipelines/246366'},PID,'main')
        cache['stalled_groups']=[{'project':PID,'object':'mr','iid':1152,'records':[record]}]
        SYNC.atomic_write_json(syncer.cache_path(),cache); before=syncer.cache_path().read_bytes()
        # It is absent from the ordinary updated-MR list; only the retained old
        # pipeline could discover this MR and its still-unbound closing Issue.
        self.gitlab.merge_request=lambda *_:mr.make_mr(iid=1152,source_branch='fix/standalone')
        self.gitlab.closes[1152]=[mr.make_issue(186)]
        self.gitlab.issue=lambda *_:mr.make_issue(186)
        summary=self.syncer().run()
        self.assertEqual(summary['status'],'degraded')
        self.assertEqual(self.buzz.writes,[]); self.assertEqual(self.gitlab.writes,[])
        self.assertEqual(syncer._read_outbox()['pending'],[old]); self.assertEqual(syncer.cache_path().read_bytes(),before)

    def _assert_retained_source_scope(self, project, *, allow_effects=False, expected_key="pipeline-246367-success", **overrides):
        import test_gitlab_buzz_sync_mr_issue_merge as mr
        self.gitlab,self.buzz=mr.FakeGitLab(),mr.FakeBuzz()
        enable_signed_transport(self)
        self.syncer().run()
        syncer,old=self.pending_pipeline()
        cache=json.loads(syncer.cache_path().read_text())
        record=SYNC.record_from_pipeline({'id':246366,'status':'failed','ref':'refs/merge-requests/1152/head',
            'updated_at':mr.SERVER_TIME,'web_url':mr.WEB+'/-/pipelines/246366'},PID,'main')
        if project is None:
            record.pop('project')
        else:
            record['project']=project
        record.update(overrides)
        cache['stalled_groups']=[{'project':PID,'object':'mr','iid':1152,'records':[record]}]
        SYNC.atomic_write_json(syncer.cache_path(),cache); before=syncer.cache_path().read_bytes()
        self.gitlab.merge_request=lambda *_:mr.make_mr(iid=1152,source_branch='fix/standalone')
        self.gitlab.closes[1152]=[mr.make_issue(186)]
        self.gitlab.issue=lambda *_:mr.make_issue(186)
        self.gitlab.pipeline_jobs=lambda *_:[]
        summary=self.syncer().run()
        self.assertEqual(summary['status'],'degraded')
        if allow_effects:
            self.assertTrue(self.buzz.writes); self.assertTrue(self.gitlab.writes)
            self.assertTrue(any(expected_key in event['content'] for event in self.buzz.events))
            self.assertFalse(any('pipeline-246366-' in event['content'] for event in self.buzz.events))
        else:
            self.assertEqual(self.buzz.writes,[]); self.assertEqual(self.gitlab.writes,[])
        self.assertEqual(syncer._read_outbox()['pending'],[old])
        self.assertEqual(syncer.cache_path().read_bytes(),before)

    def test_retained_pipeline_missing_project_cannot_materialize_roots(self):
        self._assert_retained_source_scope(None)

    def test_retained_pipeline_conflicting_project_cannot_materialize_roots(self):
        self._assert_retained_source_scope(PID+1)

    def test_retained_pipeline_string_project_cannot_materialize_roots(self):
        self._assert_retained_source_scope(str(PID))

    def test_retained_unknown_object_cannot_materialize_roots(self):
        self._assert_retained_source_scope(PID,object='unexpected')

    def test_retained_pipeline_key_disguised_as_mr_cannot_materialize_roots(self):
        self._assert_retained_source_scope(PID,object='mr',event='approved')

    def test_retained_pipeline_conflicting_event_cannot_materialize_roots(self):
        self._assert_retained_source_scope(PID,key='pipeline-246367-success',
                                                       source_id=246367,event='failed')

    def test_retained_pipeline_wrong_group_iid_cannot_materialize_roots(self):
        self._assert_retained_source_scope(PID,key='pipeline-246367-success',
                                                       source_id=246367,event='success',mr_iid=999)

    def test_proven_unrelated_retained_pipeline_still_materializes_own_mr(self):
        self._assert_retained_source_scope(PID,allow_effects=True,
            key='pipeline-246367-success',source_id=246367,event='success')

    def test_proven_retained_mr_approval_still_materializes_own_mr(self):
        self._assert_retained_source_scope(PID,allow_effects=True,expected_key='event-246367',
            key='event-246367',object='mr',source_id=None,event='approved',ref='')

    def test_actual_cli_plaque_scope_requires_valid_signature_even_for_same_pubkey(self):
        syncer,old=self.pending_pipeline()
        syncer._projects={PID:self.gitlab.project(PID)}; syncer._project_visibilities={PID:'public'}
        event=sign_fixture_event({'kind':9,'created_at':1000,'tags':[['h',CHANNEL]],
            'content':SYNC.render_mr_plaque(SYNC.mr_fact(__import__('test_gitlab_buzz_sync_mr_issue_merge').make_mr(iid=1152),PID),'buzz-sync-test/pilot')})
        cli=object.__new__(SYNC.BuzzCli)
        cli.cli='/synthetic/buzz'; cli.channel=CHANNEL; cli.publisher=SIGNING_PUBKEY; cli.env={}; cli.sleeper=lambda _:None
        replies=[event]
        cli.runner=lambda *args,**kwargs:subprocess.CompletedProcess(args,0,json.dumps(replies),'')
        syncer.buzz=cli
        self.assertEqual(syncer._independent_target(event['id'],syncer._held_pipeline_scopes(),PID),PID)
        for field,value in [('sig','00'*64),('content',event['content'].replace('!1152','edited !1152'))]:
            forged=copy.deepcopy(event); forged[field]=value; replies[:]=[forged]
            with self.subTest(field=field):
                with self.assertRaisesRegex(SYNC.SyncError,'signature'):
                    syncer._deliver('buzz_reaction',{'event_id':event['id'],'emoji':'✅','project_id':PID},lambda:self.fail('dispatch'))
        self.assertEqual(syncer._read_outbox()['pending'],[old])

    def test_signed_child_cannot_delegate_scope_to_unsigned_plaque_ancestor(self):
        syncer,old=self.pending_pipeline()
        syncer._projects={PID:self.gitlab.project(PID)}; syncer._project_visibilities={PID:'public'}
        root=sign_fixture_event({'kind':9,'created_at':1000,'tags':[['h',CHANNEL]],
            'content':SYNC.render_issue_plaque(SYNC.issue_fact(make_issue(186),PID),'buzz-sync-test/pilot')})
        child=sign_fixture_event({'kind':9,'created_at':1001,'tags':[['h',CHANNEL],['e',root['id'],'','reply']],
            'content':SYNC.render_message(SYNC.issue_fact(make_issue(186),PID),'routing',first=True)})
        root['sig']='00'*64
        cli=object.__new__(SYNC.BuzzCli)
        cli.cli='/synthetic/buzz'; cli.channel=CHANNEL; cli.publisher=SIGNING_PUBKEY; cli.env={}; cli.sleeper=lambda _:None
        def runner(argv,**kwargs):
            target=argv[argv.index('--event')+1]
            return subprocess.CompletedProcess(argv,0,json.dumps([child if target==child['id'] else root]),'')
        cli.runner=runner; syncer.buzz=cli
        with self.assertRaisesRegex(SYNC.SyncError,'signature'):
            syncer._deliver('buzz_reaction',{'event_id':child['id'],'emoji':'✅','project_id':PID},lambda:self.fail('dispatch'))
        self.assertEqual(syncer._read_outbox()['pending'],[old])


class ExplicitIssueOriginIsolationTest(unittest.TestCase):
    """Signed external transport; product scope/recovery methods are never mocked."""
    setUp = set_up_signed
    syncer = delivery.DeliveryStateTest.syncer
    pending_pipeline = OutboxTopicsTest.pending_pipeline
    existing_issue = OutboxTopicsTest.existing_issue

    def origin(self, *, channel=CHANNEL, parents=(), content='Existing participant topic'):
        secret = bytes.fromhex('02' * 32)
        event = {'kind':9,'created_at':900,'tags':[['h',channel],*parents],
                 'content':content,'pubkey':SYNC.nk.pubkey_xonly(secret).hex()}
        raw = json.dumps([0,event['pubkey'],900,9,event['tags'],content],
                         ensure_ascii=False,separators=(',',':')).encode()
        digest = hashlib.sha256(raw).digest()
        event.update(id=digest.hex(),sig=SYNC.nk.schnorr_sign(digest,secret,bytes(32)).hex())
        self.buzz.events.append(event)
        return event

    def marker(self,target):
        return '<!-- gitlab-buzz-origin:v1 '+json.dumps({'channel_id':CHANNEL,'root_event_id':target})+' -->'

    def prepared(self):
        root=self.existing_issue();event=self.origin()
        self.gitlab.issue_list[PID][0]['description']=self.marker(event['id'])
        syncer,pending=self.pending_pipeline()
        syncer._projects={PID:self.gitlab.project(PID)};syncer._project_visibilities={PID:'public'}
        return syncer,pending,root,event

    def payload(self,event):
        content=SYNC.render_message(SYNC.issue_fact(make_issue(186),PID),'routing',first=True)
        return {'content':content,'reply_to':event['id'],'mentions':[],'project_id':PID}

    def test_existing_issue_mirrors_and_restarts_without_duplicates(self):
        syncer,pending,root,event=self.prepared();cache=syncer.cache_path().read_bytes()
        result=syncer.run()
        self.assertEqual(result['status'],'degraded')
        self.assertEqual({w[1] for w in self.buzz.writes},{root,event['id']})
        self.assertEqual(syncer._read_outbox()['pending'],[pending])
        self.assertEqual(syncer.cache_path().read_bytes(),cache)
        writes=list(self.buzz.writes);self.syncer().run();self.assertEqual(self.buzz.writes,writes)

    def test_new_issue_binds_to_explicit_foreign_origin(self):
        event=self.origin();self.gitlab.issue_list[PID]=[make_issue(186,description=self.marker(event['id']))]
        syncer,pending=self.pending_pipeline();result=syncer.run()
        self.assertEqual(result['status'],'degraded');self.assertEqual(len(self.gitlab.writes),1)
        self.assertEqual({w[1] for w in self.buzz.writes},{event['id']})
        self.assertEqual(syncer._read_outbox()['pending'],[pending]);self.assertFalse(syncer.cache_path().exists())

    def test_queued_issue_send_reproves_origin_after_restart(self):
        syncer,pending,root,event=self.prepared();key=syncer._queue_delivery('buzz_message',self.payload(event))
        restarted=self.syncer();restarted._projects={PID:self.gitlab.project(PID)};restarted._project_visibilities={PID:'public'}
        restarted._reconcile_pending()
        self.assertEqual(len(self.buzz.writes),1);self.assertEqual(self.buzz.writes[0][1],event['id'])
        self.assertEqual(restarted._read_outbox()['pending'],[pending])
        self.assertTrue(any(x['change_id']==key for x in restarted._read_outbox()['acked']))

    def test_signed_child_reaction_proves_foreign_ancestor_but_bare_reaction_cannot(self):
        syncer,pending,root,event=self.prepared();child=self.buzz.send(self.payload(event)['content'],reply_to=event['id'])
        self.buzz.writes.clear();called=[]
        syncer._deliver('buzz_reaction',{'event_id':child,'emoji':'✅','project_id':PID},lambda:called.append(True))
        self.assertEqual(called,[True]);self.assertEqual(syncer._read_outbox()['pending'],[pending])
        with self.assertRaisesRegex(SYNC.SyncError,'scope'):
            syncer._deliver('buzz_reaction',{'event_id':event['id'],'emoji':'✅','project_id':PID},lambda:self.fail('write'))

    def test_revoked_marker_blocks_queued_send(self):
        syncer,pending,root,event=self.prepared();syncer._queue_delivery('buzz_message',self.payload(event))
        ledger=syncer._read_outbox();self.gitlab.issue_list[PID][0]['description']=''
        with self.assertRaisesRegex(SYNC.SyncError,'scope'):syncer._reconcile_pending()
        self.assertEqual(self.buzz.writes,[]);self.assertEqual(syncer._read_outbox(),ledger)

    def test_invalid_contexts_never_authorize_dispatch(self):
        for mutation in ['bare_link','missing_marker','other_issue','wrong_project','wrong_url',
                         'confidential','forged_signature','other_channel','parent','pipeline_header']:
            with self.subTest(mutation=mutation):
                case=ExplicitIssueOriginIsolationTest();case.setUp()
                try:
                    syncer,pending,root,event=case.prepared();issue=case.gitlab.issue_list[PID][0]
                    if mutation=='bare_link':issue['description']=f'buzz://message?channel={CHANNEL}&id={event["id"]}'
                    elif mutation=='missing_marker':issue['description']=''
                    elif mutation=='other_issue':
                        issue['iid']=187;case.gitlab.issue_details[(PID,186)]=issue
                    elif mutation=='wrong_project':issue['project_id']=PID+1
                    elif mutation=='wrong_url':issue['web_url']=FAKES.WEB+'/-/issues/999'
                    elif mutation=='confidential':issue['confidential']=True
                    elif mutation=='forged_signature':event['sig']='00'*64
                    else:
                        event=case.origin(channel='other' if mutation=='other_channel' else CHANNEL,
                            parents=[['e','a'*64,'','reply']] if mutation=='parent' else (),
                            content=pending['payload']['content'] if mutation=='pipeline_header' else 'Existing participant topic')
                        issue['description']=case.marker(event['id'])
                    with self.assertRaises(SYNC.SyncError):
                        syncer._deliver('buzz_message',case.payload(event),lambda:case.fail('dispatch'))
                    self.assertEqual(case.buzz.writes,[]);self.assertEqual(syncer._read_outbox()['pending'],[pending])
                finally:case.doCleanups()

    def test_human_note_marker_works_but_bot_note_cannot_grant_origin(self):
        syncer,pending,root,event=self.prepared()
        self.gitlab.issue_list[PID][0]['description']=''
        self.gitlab.add_user_note(186,9001,self.marker(event['id']))
        summary=syncer.run()
        self.assertEqual(summary['status'],'degraded')
        self.assertTrue(any(w[1]==event['id'] for w in self.buzz.writes))
        note=next(n for n in self.gitlab.note_list[(PID,186)] if n['id']==9001)
        note['author']['id']=FAKES.BOT_ID
        self.buzz.writes.clear()
        with self.assertRaisesRegex(SYNC.SyncError,'scope'):
            syncer._deliver('buzz_message',self.payload(event),lambda:self.fail('dispatch'))
        self.assertEqual(self.buzz.writes,[])

    def test_marker_for_other_channel_or_malformed_marker_does_not_authorize(self):
        syncer,pending,root,event=self.prepared()
        for description in [self.marker(event['id']).replace(CHANNEL,'00000000-0000-4000-8000-000000000099'),
                            '<!-- gitlab-buzz-origin:v1 {broken} -->']:
            self.gitlab.issue_list[PID][0]['description']=description
            with self.assertRaises(SYNC.SyncError):
                syncer._deliver('buzz_message',self.payload(event),lambda:self.fail('dispatch'))
        self.assertEqual(self.buzz.writes,[])
        self.assertEqual(syncer._read_outbox()['pending'],[pending])

    def test_unscoped_wrong_project_or_pipeline_payload_cannot_borrow_issue_origin(self):
        syncer,pending,root,event=self.prepared()
        for content in ['unscoped prose',pending['payload']['content'],
                        self.payload(event)['content'].replace(f'[project:{PID}]',f'[project:{PID+1}]'),
                        self.payload(event)['content'].replace('/-/issues/186','/-/issues/999')]:
            payload=dict(self.payload(event),content=content)
            with self.assertRaises(SYNC.SyncError):
                syncer._deliver('buzz_message',payload,lambda:self.fail('dispatch'))
        for kind,payload in [('buzz_edit',dict(self.payload(event),event_id=event['id'])),
                             ('buzz_diff',{'reply_to':event['id'],'project_id':PID})]:
            with self.assertRaises(SYNC.SyncError):
                syncer._deliver(kind,payload,lambda:self.fail('dispatch'))
        self.assertEqual(syncer._read_outbox()['pending'],[pending])

    def test_lost_origin_send_ack_uses_readback_without_resend(self):
        syncer,pending,root,event=self.prepared()
        send=self.buzz.send
        def lost_ack(*args,**kwargs):
            send(*args,**kwargs)
            raise SYNC.SyncError('transport response lost')
        self.buzz.send=lost_ack
        with self.assertRaisesRegex(SYNC.SyncError,'transport response lost'):
            syncer._send_message(self.payload(event)['content'],reply_to=event['id'],project_id=PID)
        self.assertEqual(len(self.buzz.writes),1)
        self.assertEqual(len(syncer._read_outbox()['pending']),2)
        self.buzz.send=send
        restarted=self.syncer();restarted._projects={PID:self.gitlab.project(PID)};restarted._project_visibilities={PID:'public'}
        restarted._reconcile_pending()
        self.assertEqual(len(self.buzz.writes),1)
        self.assertEqual(restarted._read_outbox()['pending'],[pending])

    def test_unknown_origin_send_without_readback_stays_pending(self):
        syncer,pending,root,event=self.prepared()
        key=syncer._queue_delivery('buzz_message',self.payload(event));syncer._mark_delivery_attempted(key)
        ledger=syncer._read_outbox()
        with self.assertRaisesRegex(SYNC.SyncError,'still pending'):syncer._reconcile_pending()
        self.assertEqual(self.buzz.writes,[]);self.assertEqual(syncer._read_outbox(),ledger)

    def test_selected_private_issue_still_obeys_delivery_permission_gate(self):
        syncer,pending,root,event=self.prepared();syncer._project_visibilities={PID:'private'}
        # No relay owner evidence: the new scope proof must not bypass visibility checks.
        with self.assertRaises(SYNC.SyncError):
            syncer._deliver('buzz_message',self.payload(event),lambda:self.fail('dispatch'))
        self.assertEqual(self.buzz.writes,[]);self.assertEqual(syncer._read_outbox()['pending'],[pending])

    def test_issue_origin_authority_cannot_be_borrowed_by_another_issue(self):
        syncer,pending,root,event=self.prepared()
        self.gitlab.issue_list[PID].append(make_issue(187))
        payload=dict(self.payload(event),content=SYNC.render_message(SYNC.issue_fact(make_issue(187),PID),'routing',first=True))
        with self.assertRaisesRegex(SYNC.SyncError,'scope'):
            syncer._deliver('buzz_message',payload,lambda:self.fail('dispatch'))
        self.assertEqual(self.buzz.writes,[])
        self.assertEqual(syncer._read_outbox()['pending'],[pending])


class LegacyWorkItemScopeTest(unittest.TestCase):
    """Issue 84 shape: signed legacy status child under a signed plaque root."""
    setUp = set_up_signed
    syncer = delivery.DeliveryStateTest.syncer
    pending_pipeline = OutboxTopicsTest.pending_pipeline

    def prepared(self, *, url=None, root_url=None, mutation=None):
        syncer, pending = self.pending_pipeline()
        syncer._projects = {PID: self.gitlab.project(PID)}
        syncer._project_visibilities = {PID: 'public'}
        canonical = syncer._object_url(PID, 'issue', 84)
        legacy = canonical.replace('/-/issues/', '/-/work_items/')
        fact = SYNC.issue_fact(make_issue(84), PID)
        fact['url'] = root_url or legacy
        root = sign_fixture_event({'kind': 9, 'created_at': 1000, 'tags': [['h', CHANNEL]],
            'content': SYNC.render_issue_plaque(fact, 'buzz-sync-test/pilot')})
        fact['url'] = legacy if url is None else url
        child = sign_fixture_event({'kind': 9, 'created_at': 1001,
            'tags': [['h', CHANNEL], ['e', root['id'], '', 'reply']],
            'content': SYNC.render_message(fact, 'routing', first=True)})
        if mutation:
            mutation(root, child)
        cli = object.__new__(SYNC.BuzzCli)
        cli.cli = '/synthetic/buzz'; cli.channel = CHANNEL; cli.publisher = SIGNING_PUBKEY
        cli.env = {}; cli.sleeper = lambda _: None
        def runner(argv, **kwargs):
            target = argv[argv.index('--event') + 1]
            return subprocess.CompletedProcess(argv, 0, json.dumps([
                child if target == child['id'] else root]), '')
        cli.runner = runner
        syncer.buzz = cli
        syncer.cache_path().write_text('{"cursor":"retained"}')
        return syncer, pending, child, canonical

    def test_legacy_signed_status_reaction_preserves_unknown_and_cursor(self):
        syncer, pending, child, _ = self.prepared()
        before = syncer.cache_path().read_bytes()
        called = []
        result = syncer._deliver('buzz_reaction',
            {'event_id': child['id'], 'emoji': '🟢', 'project_id': PID},
            lambda: called.append(child['id']) or 'reaction-readback')
        self.assertEqual(result, 'reaction-readback')
        self.assertEqual(called, [child['id']])
        self.assertEqual(syncer._read_outbox()['pending'], [pending])
        self.assertEqual(syncer.cache_path().read_bytes(), before)

    def test_canonical_and_legacy_url_forms_keep_exact_identity(self):
        syncer, _, child, canonical = self.prepared()
        legacy = canonical.replace('/-/issues/', '/-/work_items/')
        for url in (canonical, legacy, canonical + '#note_1', legacy + '#note_1'):
            for wrapper in ('{}', '[Issue]({})'):
                # Keep the actual signed fixture's machine header, vary only its URL presentation.
                header = child['content'][child['content'].index('[gitlab-notify:v1]'):]
                with self.subTest(url=url, wrapper=wrapper):
                    self.assertEqual(syncer._independent_object_content(
                        header + '\n' + wrapper.format(url), PID), PID)

    def test_wrong_or_incomplete_url_never_dispatches(self):
        syncer, _, _, canonical = self.prepared()
        legacy = canonical.replace('/-/issues/', '/-/work_items/')
        candidates = ['']
        for base in (canonical, legacy):
            candidates.extend((base.replace('://', '://evil.invalid/'),
                base.replace('/-/', '-other/-/'), base + '0', base + '/extra',
                base + '?query=1', 'prefix' + base, 'https://evil.invalid/?next=' + base,
                base.replace('/84', '/084'), base.replace('/84', '/85')))
        for url in candidates:
            with self.subTest(url=url):
                syncer, pending, child, _ = self.prepared(url=url)
                before = syncer.cache_path().read_bytes()
                with self.assertRaises(SYNC.SyncError):
                    syncer._deliver('buzz_reaction',
                        {'event_id': child['id'], 'emoji': '🟢', 'project_id': PID},
                        lambda: self.fail('unproven destination dispatched'))
                self.assertEqual(syncer._read_outbox()['pending'], [pending])
                self.assertEqual(syncer.cache_path().read_bytes(), before)

    def test_legacy_child_requires_signed_same_scope_ancestor(self):
        for target in ('root', 'child'):
            with self.subTest(target=target):
                def corrupt(root, child):
                    (root if target == 'root' else child)['sig'] = '00' * 64
                syncer, pending, child, _ = self.prepared(mutation=corrupt)
                with self.assertRaisesRegex(SYNC.SyncError, 'signature'):
                    syncer._deliver('buzz_reaction',
                        {'event_id': child['id'], 'emoji': '🟢', 'project_id': PID},
                        lambda: self.fail('forged ancestor dispatched'))
                self.assertEqual(syncer._read_outbox()['pending'], [pending])
        syncer, _, _, canonical = self.prepared()
        syncer, pending, child, _ = self.prepared(root_url=canonical.replace('://', '://evil.invalid/'))
        with self.assertRaises(SYNC.SyncError):
            syncer._deliver('buzz_reaction',
                {'event_id': child['id'], 'emoji': '🟢', 'project_id': PID},
                lambda: self.fail('wrong ancestor dispatched'))
        self.assertEqual(syncer._read_outbox()['pending'], [pending])
