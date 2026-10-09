"""UNKNOWN top-level sends never retry; only pre-existing independent comments may progress."""
import copy
import unittest
import test_gitlab_buzz_sync_delivery as delivery
from test_gitlab_buzz_sync_delivery import FAKES, SYNC, PID, CHANNEL, make_issue


class OutboxTopicsTest(unittest.TestCase):
    setUp = delivery.DeliveryStateTest.setUp
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

    def test_new_root_and_top_level_never_run(self):
        self.gitlab.issue_list[PID] = [make_issue(186)]
        syncer, item = self.pending_pipeline()
        self.gitlab.pipelines = lambda *_: self.fail("top-level scan must stay paused")
        summary = syncer.run()
        self.assertEqual(summary["status"], "degraded")
        self.assertEqual(self.buzz.writes, [])
        self.assertEqual(self.gitlab.writes, [])
        self.assertFalse(syncer.cache_path().exists())
        self.assertEqual(syncer._read_outbox()["pending"], [item])

    def test_new_direct_action_cannot_bypass_unknown(self):
        syncer, item = self.pending_pipeline()
        syncer._project_visibilities = {PID: "public"}
        payload = dict(item["payload"], content=item["payload"]["content"].replace("246366", "246367"))
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
        with self.assertRaisesRegex(SYNC.SyncError, "still pending"):
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
        self.assertEqual(self.buzz.writes, [])
        self.assertEqual(syncer._read_outbox()["pending"], [item])

    def test_non_bot_binding_does_not_authorize_independence(self):
        self.existing_issue()
        self.gitlab.note_list[(PID, 186)][0]["author"]["id"] = 99
        syncer, _ = self.pending_pipeline()
        syncer.run()
        self.assertEqual(self.buzz.writes, [])

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
        syncer.run()
        self.assertEqual(self.buzz.writes, [])
