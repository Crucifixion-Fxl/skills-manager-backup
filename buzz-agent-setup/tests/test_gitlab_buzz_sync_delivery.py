"""Delivery-state contracts: persist before write, stop at first failure, and never cross a failed cursor."""

import fcntl
import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


TESTS = Path(__file__).resolve().parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FAKES = load("gitlab_buzz_sync_delivery_fakes", TESTS / "test_gitlab_buzz_sync_run.py")
SYNC = FAKES.SYNC
PID, CHANNEL = FAKES.PID, FAKES.CHANNEL
make_issue, sync_config = FAKES.make_issue, FAKES.sync_config


class DeliveryStateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / "state"
        self.state.mkdir(mode=0o700)
        self.gitlab, self.buzz = FAKES.FakeGitLab(), FAKES.FakeBuzz()
        self.config = sync_config()
        self.buzz.channel_members = lambda: {FAKES.DESK: "member"}

    def syncer(self, config=None):
        return SYNC.Syncer(config or self.config, self.gitlab, self.buzz, state_dir=self.state)

    def test_first_failed_object_stops_later_delivery_and_does_not_advance_cursor(self):
        """L1-GIS-079 A transport failure stops the run at the first failing object and leaves the cursor unchanged.

        (A bad-data object, e.g. a bound root that no longer exists, only stalls itself: ADR-0009.)"""
        self.gitlab.issue_list[PID] = [make_issue(181), make_issue(182), make_issue(183)]
        self.gitlab.note_list[(PID, 182)] = [
            {"id": 1, "author": {"id": FAKES.BOT_ID, "username": FAKES.BOT}, "system": False,
             "created_at": FAKES.SERVER_TIME,
             "body": SYNC.render_binding_note(
                 {"project_id": PID, "object": "issue", "iid": 182, "channel_id": CHANNEL,
                  "root_event_id": "f" * 64}, "buzz://missing")}
        ]

        original_thread = self.buzz.thread

        def relay_down(root):
            if root == "f" * 64:
                raise SYNC.BuzzCliError("messages thread", 2)
            return original_thread(root)

        self.buzz.thread = relay_down
        with self.assertRaises(SYNC.SyncError):
            self.syncer().run()
        roots = [content for _, reply_to, content in self.buzz.writes if reply_to is None]
        self.assertTrue(any("/issues/181" in content for content in roots))
        self.assertFalse(any("/issues/183" in content for content in roots))
        self.assertEqual(list(self.state.glob("*.cache.json")), [])

        self.buzz.thread = original_thread
        self.gitlab.note_list[(PID, 182)] = []
        summary = self.syncer().run()
        self.assertEqual(summary["created"], 2)
        self.assertEqual(sum("/issues/181" in content for _, _, content in self.buzz.writes), 2)

    def test_publish_before_ack_leaves_durable_pending_and_retry_recovers_without_duplicate(self):
        """L1-GIS-080 A lost send ACK leaves PENDING on disk; retry finds the signed root and records recovery."""
        self.gitlab.issue_list[PID] = [make_issue(182)]
        original_send = self.buzz.send
        failed_once = False

        def lose_ack(content, reply_to=None, mentions=()):
            nonlocal failed_once
            event_id = original_send(content, reply_to, mentions)
            if not failed_once:
                failed_once = True
                raise SYNC.SyncError("simulated lost ACK")
            return event_id

        self.buzz.send = lose_ack
        syncer = self.syncer()
        with self.assertRaises(SYNC.SyncError):
            syncer.run()
        ledger = json.loads(syncer.outbox_path().read_text(encoding="utf-8"))
        self.assertEqual(len(ledger["pending"]), 1)
        self.assertEqual(ledger["pending"][0]["status"], "PENDING")
        self.assertRegex(ledger["pending"][0]["change_id"], r"^[0-9a-f]{64}$")

        self.buzz.send = original_send
        summary = self.syncer().run()
        self.assertEqual((summary["recovered"], len(self.buzz.events)), (1, 2))
        ledger = json.loads(self.syncer().outbox_path().read_text(encoding="utf-8"))
        self.assertEqual(ledger["pending"], [])
        self.assertTrue(any(item.get("recovered") is True for item in ledger["acked"]))

    def test_overlapping_project_sets_share_a_project_scope_lock(self):
        """L1-GIS-081 Configs [481,482] and [482,483] cannot write the same channel concurrently."""
        first, second = sync_config(), sync_config()
        first["gitlab"]["projects"] = [481, 482]
        second["gitlab"]["projects"] = [482, 483]
        shared = set(SYNC.lock_paths(first, self.state)) & set(SYNC.lock_paths(second, self.state))
        self.assertEqual(len(shared), 1)
        path = shared.pop()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            summary = self.syncer(second).run()
        self.assertEqual(summary["status"], "locked")
        self.assertEqual(self.gitlab.issue_queries, [])

    def test_restart_reconciles_old_pending_before_any_new_delivery(self):
        """L1-GIS-086 An unresolved old PENDING stops the run before a newer GitLab object is published."""
        syncer = self.syncer()
        syncer._queue_delivery("buzz_message", {
            "content": "[gitlab-notify:v1][type:push][project:481] unresolved prior fact",
            "reply_to": None,
            "mentions": [],
        })
        self.gitlab.issue_list[PID] = [make_issue(186)]

        with self.assertRaisesRegex(SYNC.SyncError, "still pending"):
            self.syncer().run()

        self.assertEqual(self.buzz.writes, [])
        ledger = json.loads(syncer.outbox_path().read_text(encoding="utf-8"))
        self.assertEqual(len(ledger["pending"]), 1)


class PrivateAudienceTest(unittest.TestCase):
    """ADR-0006: channel membership itself is the audience consent; drift never gates the sync."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.gitlab = FAKES.FakeGitLab()
        self.gitlab.projects[PID]["visibility"] = "private"
        self.gitlab.issue_list[PID] = [make_issue(182)]

    def run_sync(self, actual, gitlab=None):
        config = sync_config()
        buzz = FAKES.FakeBuzz()
        buzz.channel_members = lambda: dict(actual)
        state = Path(self.tmp.name) / f"state-{len(actual)}"
        state.mkdir(mode=0o700, parents=True, exist_ok=True)
        state.chmod(0o700)
        return SYNC.Syncer(config, gitlab or self.gitlab, buzz, state_dir=state).run(), buzz

    def test_private_project_publishes_without_any_membership_reconciliation(self):
        """L1-GIS-082 (ADR-0006 修订) Membership joins/leaves are the channel admin's consent, never a sync gate."""
        for actual in (
            {FAKES.DESK: "member"},
            {FAKES.DESK: "member", "a" * 64: "member"},
            {FAKES.DESK: "member", "b" * 64: "member", "c" * 64: "admin"},
        ):
            with self.subTest(members=len(actual)):
                # Fresh GitLab per subtest: earlier subtests write binding notes.
                gitlab = FAKES.FakeGitLab()
                gitlab.projects[PID]["visibility"] = "private"
                gitlab.issue_list[PID] = [make_issue(182)]
                summary, buzz = self.run_sync(actual, gitlab=gitlab)
                self.assertEqual((summary["status"], len(buzz.writes)), ("ok", 2))

    def test_retired_audience_block_is_rejected_loudly(self):
        """L1-GIS-083 (ADR-0006 修订) The retired audience block must be deleted, not silently ignored."""
        config = sync_config()
        config["audience"] = {"allowed_pubkeys": [FAKES.DESK]}
        with self.assertRaisesRegex(SYNC.SyncError, "ADR-0006"):
            SYNC.validate_config(config)

    def test_membership_drift_mid_run_never_blocks_the_first_private_write(self):
        """L1-GIS-088 (ADR-0006 修订) A member joining mid-run no longer stops the first private Buzz write."""
        buzz = FAKES.FakeBuzz()
        audiences = iter((
            {FAKES.DESK: "member"},
            {FAKES.DESK: "member", "a" * 64: "member"},
        ))
        buzz.channel_members = lambda: next(audiences)

        summary = SYNC.Syncer(
            sync_config(), self.gitlab, buzz, state_dir=Path(self.tmp.name),
        ).run()
        self.assertEqual(summary["status"], "ok")
        self.assertEqual(len(buzz.writes), 2)  # plaque + first fact (issue #78)

    def test_project_visibility_drift_fails_closed_at_the_write_boundary(self):
        """L1-GIS-096 public→private 或 private→public 在本轮写入前发生时，不使用预检缓存继续投递。"""
        for before, after in (
            ("public", "private"), ("private", "public"),
            ("public", "internal"), ("internal", "public"),
        ):
            with self.subTest(before=before, after=after):
                gitlab = FAKES.FakeGitLab()
                gitlab.issue_list[PID] = [make_issue(182)]
                visibilities = iter((before, after))
                gitlab.project = lambda project_id: {"id": project_id, "visibility": next(visibilities),
                                  "web_url": FAKES.WEB,
                                  "path_with_namespace": "buzz-sync-test/pilot"}
                config = sync_config()
                buzz = FAKES.FakeBuzz()
                buzz.channel_members = lambda: {FAKES.DESK: "member"}

                with self.assertRaisesRegex(SYNC.SyncError, "visibility"):
                    SYNC.Syncer(config, gitlab, buzz, state_dir=Path(self.tmp.name) / before).run()

                self.assertEqual((buzz.writes, gitlab.writes), ([], []))

    def test_project_visibility_drift_stops_the_binding_note_before_pending(self):
        """L1-GIS-101 Recheck visibility after the Buzz root and before the GitLab binding note."""
        gitlab = FAKES.FakeGitLab()
        gitlab.issue_list[PID] = [make_issue(182)]
        visibilities = iter(("public", "public", "private"))
        gitlab.project = lambda project_id: {"id": project_id, "visibility": next(visibilities),
                                  "web_url": FAKES.WEB,
                                  "path_with_namespace": "buzz-sync-test/pilot"}
        config = sync_config()
        buzz = FAKES.FakeBuzz()
        buzz.channel_members = lambda: {FAKES.DESK: "member"}
        syncer = SYNC.Syncer(config, gitlab, buzz, state_dir=Path(self.tmp.name) / "binding-boundary")

        with self.assertRaisesRegex(SYNC.SyncError, "visibility"):
            syncer.run()

        self.assertEqual((len(buzz.writes), gitlab.writes), (1, []))
        self.assertEqual(list((Path(self.tmp.name) / "binding-boundary").glob("*.cache.json")), [])
        ledger = json.loads(syncer.outbox_path().read_text(encoding="utf-8"))
        self.assertEqual(ledger["pending"], [])
        self.assertEqual([item["kind"] for item in ledger["acked"]], ["buzz_message"])

    def test_project_visibility_drift_stops_pending_binding_retry(self):
        """L1-GIS-102 Recheck visibility before retrying a crash-surviving GitLab binding note."""
        gitlab = FAKES.FakeGitLab()
        root = "f" * 64
        body = SYNC.render_binding_note(
            {"project_id": PID, "object": "issue", "iid": 182,
             "channel_id": CHANNEL, "root_event_id": root},
            SYNC.buzz_message_link(CHANNEL, root),
        )
        config = sync_config()
        buzz = FAKES.FakeBuzz()
        buzz.channel_members = lambda: {FAKES.DESK: "member"}
        syncer = SYNC.Syncer(config, gitlab, buzz, state_dir=Path(self.tmp.name) / "pending-binding")
        syncer._queue_delivery("gitlab_note", {
            "project_id": PID, "object": "issue", "iid": 182, "body": body,
        })
        visibilities = iter(("public", "private"))
        gitlab.project = lambda project_id: {"id": project_id, "visibility": next(visibilities),
                                  "web_url": FAKES.WEB,
                                  "path_with_namespace": "buzz-sync-test/pilot"}

        with self.assertRaisesRegex(SYNC.SyncError, "visibility"):
            syncer.run()

        self.assertEqual((buzz.writes, gitlab.writes), ([], []))
        ledger = json.loads(syncer.outbox_path().read_text(encoding="utf-8"))
        self.assertEqual(len(ledger["pending"]), 1)
        self.assertEqual(ledger["acked"], [])

    def test_issue_is_re_read_if_it_becomes_confidential_before_delivery(self):
        """L1-GIS-089 A fresh confidential Issue is skipped even when the listing snapshot was public."""
        self.gitlab.issue_details[(PID, 182)] = make_issue(182, confidential=True)

        summary, buzz = self.run_sync({FAKES.DESK: "member"})

        self.assertEqual(summary["skipped"]["confidential"], 1)
        self.assertEqual((buzz.writes, self.gitlab.writes), ([], []))


if __name__ == "__main__":
    unittest.main()
