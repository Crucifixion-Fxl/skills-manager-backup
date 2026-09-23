"""`mute_events` config: a channel can silence chosen top-level notices (2026-09-21).

Deployment `blocked` is a manual job waiting for a click (pipeline stays `manual`), so it
is pure noise on a host-deploy project; `failed` on a manual job does not fail the pipeline
either. Whether to hear either is a per-channel choice, made in sync-<project>.json.
"""

import copy
import re
import tempfile
import unittest
from pathlib import Path

from test_gitlab_buzz_sync_deployment_placement import (
    BOT, BOT_ID, CHANNEL, DESK, PID, SINCE, SYNC, FakeBuzz, FakeGitLab, deployment, make_mr,
)


def pipeline(pipeline_id, status="failed", ref="master"):
    return {"id": pipeline_id, "status": status, "ref": ref, "sha": "c" * 40,
            "updated_at": "2026-09-13T02:00:00Z", "web_url": f"http://127.0.0.1:8929/p/-/pipelines/{pipeline_id}"}


def base_config():
    return {
        "channel_id": CHANNEL, "publisher_pubkey": DESK, "since": SINCE,
        "include_confidential": False, "exclude": [], "diff": {"enabled": False, "private": False},
        "gitlab": {"base_url": "http://127.0.0.1:8929", "token_env": "NH_DESK_GITLAB_TOKEN",
                   "bot_user_id": BOT_ID, "bot_username": BOT, "projects": [PID]},
        "buzz": {}, "people": {},
    }


class PipelineGitLab(FakeGitLab):
    def pipeline_jobs(self, project_id, pipeline_id):
        return []


class MuteEventsConfigTest(unittest.TestCase):
    def test_key_is_a_known_config_key(self):
        self.assertIn("mute_events", SYNC.CONFIG_KEYS)

    def test_accepts_object_event_pairs_and_object_wildcard(self):
        SYNC.validate_mute_events(["deployment:failed", "deployment:blocked", "release:*"])
        SYNC.validate_mute_events([])

    def test_absent_key_is_fine(self):
        SYNC.validate_mute_events(None)

    def test_validate_config_runs_the_check(self):
        """Pins the call in validate_config: a bad value must fail the whole config, every round."""
        for bad in (["deploy:failed"], "deployment:failed", ["deployment:blockd"]):
            with self.subTest(bad=bad):
                config = base_config()
                config["mute_events"] = bad
                with self.assertRaisesRegex(SYNC.SyncError, "mute_events"):
                    SYNC.validate_config(config)

    def test_event_names_are_checked_per_object(self):
        """`deployment:blockd` must fail loud, not silently mute nothing."""
        for bad in ("deployment:blockd", "deployment:success", "pipeline:sucess", "pipeline:success",
                    "tag:created", "release:tag_created", "release:deleted", "access_token:failed"):
            with self.subTest(bad=bad), self.assertRaises(SYNC.SyncError):
                SYNC.validate_mute_events([bad])
        SYNC.validate_mute_events(["pipeline:failed", "tag:tag_created", "tag:tag_deleted", "tag:pushed",
                                   "release:created", "access_token:expiring", "tag:*"])

    def test_thread_and_system_objects_cannot_be_muted(self):
        for bad in ("issue:*", "mr:*", "note:*", "milestone:*", "sync:*", "sync:stalled", "push:*"):
            with self.subTest(bad=bad), self.assertRaises(SYNC.SyncError):
                SYNC.validate_mute_events([bad])

    def test_is_muted_needs_a_list_and_exact_entries(self):
        record = {"object": "pipeline", "event": "failed"}
        self.assertFalse(SYNC.is_muted(record, {"mute_events": "xpipeline:failed"}))
        self.assertFalse(SYNC.is_muted(record, {"mute_events": ["xpipeline:failed"]}))
        self.assertTrue(SYNC.is_muted(record, {"mute_events": ["pipeline:failed"]}))

    def test_rejects_non_list_and_malformed_entries(self):
        for bad in ("deployment:failed", [1], [""], ["deployment"], ["Deployment:failed"],
                    ["deployment:failed extra"], [["deployment:failed"]], {"deployment": "failed"}):
            with self.subTest(bad=bad):
                with self.assertRaises(SYNC.SyncError):
                    SYNC.validate_mute_events(bad)

    def test_rejects_objects_the_script_never_publishes_at_top_level(self):
        """A typo such as `deploy:failed` must fail loud instead of silently muting nothing."""
        with self.assertRaises(SYNC.SyncError):
            SYNC.validate_mute_events(["deploy:failed"])


class MuteEventsDocsTest(unittest.TestCase):
    def test_config_table_matches_mutable_events_both_ways(self):
        """The doc row is the owner's only menu; neither side may list an entry the other lacks."""
        doc = (Path(__file__).resolve().parents[1] / "references" / "gitlab-buzz-sync.md").read_text(encoding="utf-8")
        row = next(line for line in doc.splitlines() if line.startswith("| `mute_events` |"))
        documented = set(re.findall(r"`((?:%s):[a-z_]+)`" % "|".join(SYNC.MUTABLE_EVENTS), row))
        real = {f"{name}:{event}" for name, events in SYNC.MUTABLE_EVENTS.items() for event in events}
        self.assertEqual(documented, real)


class MuteEventsBehaviourTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.gitlab = PipelineGitLab()
        self.buzz = FakeBuzz()
        self.gitlab.deployment_list = [
            deployment(deployment_id=4201, status="blocked", ref="master"),
            deployment(deployment_id=4202, status="failed", ref="master"),
        ]

    def run_sync(self, mute_events):
        config = copy.deepcopy(base_config())
        if mute_events is not None:
            config["mute_events"] = mute_events
        SYNC.validate_mute_events(config.get("mute_events"))
        summary = SYNC.Syncer(config, self.gitlab, self.buzz, state_dir=Path(self.tmp.name)).run()
        return summary, [SYNC.parse_header(e["content"]) for e in self.buzz.events
                         if SYNC.parse_header(e["content"])]

    def events(self, headers):
        return sorted((h["object"], h["event"]) for h in headers)

    def test_without_the_key_both_deployment_notices_are_sent(self):
        _, headers = self.run_sync(None)
        self.assertEqual(self.events(headers), [("deployment", "blocked"), ("deployment", "failed")])

    def test_muting_blocked_keeps_failed(self):
        summary, headers = self.run_sync(["deployment:blocked"])
        self.assertEqual(self.events(headers), [("deployment", "failed")])
        self.assertEqual(summary["notified"], {"instant": 1, "milestone": 0})

    def test_muting_both_sends_nothing(self):
        summary, headers = self.run_sync(["deployment:failed", "deployment:blocked"])
        self.assertEqual(headers, [])
        self.assertEqual(summary["notified"], {"instant": 0, "milestone": 0})

    def test_object_wildcard_mutes_every_event_of_that_object(self):
        _, headers = self.run_sync(["deployment:*"])
        self.assertEqual(headers, [])

    def test_muting_deployment_does_not_touch_same_named_events_of_other_objects(self):
        self.gitlab.pipeline_list = [pipeline(50, "failed")]
        _, headers = self.run_sync(["deployment:failed", "deployment:blocked"])
        self.assertEqual(self.events(headers), [("pipeline", "failed")])

    def keys_in_channel(self):
        return {key for e in self.buzz.events for key in ("pipeline-51-failed", "pipeline-50-failed")
                if f"events:{key}" in e["content"]}

    def test_muting_pipeline_failed_only_silences_the_top_level_notice(self):
        """MR pipeline results are thread facts, not notices: a channel muting the default-branch
        notice must still see them in the MR Thread."""
        self.gitlab.deployment_list = []
        self.gitlab.mr_list = [make_mr(965, state="opened")]
        self.gitlab.pipeline_list = [pipeline(50, "failed"), pipeline(51, "failed", ref="refs/merge-requests/965/head")]
        self.run_sync(["pipeline:failed"])
        self.assertEqual(self.keys_in_channel(), {"pipeline-51-failed"})

    def test_pipeline_wildcard_is_top_level_only_too(self):
        self.gitlab.deployment_list = []
        self.gitlab.mr_list = [make_mr(965, state="opened")]
        self.gitlab.pipeline_list = [pipeline(50, "failed"), pipeline(51, "failed", ref="refs/merge-requests/965/head")]
        self.run_sync(["pipeline:*"])
        self.assertEqual(self.keys_in_channel(), {"pipeline-51-failed"})

    def test_without_mute_both_pipelines_are_published(self):
        self.gitlab.deployment_list = []
        self.gitlab.mr_list = [make_mr(965, state="opened")]
        self.gitlab.pipeline_list = [pipeline(50, "failed"), pipeline(51, "failed", ref="refs/merge-requests/965/head")]
        self.run_sync(None)
        self.assertEqual(self.keys_in_channel(), {"pipeline-50-failed", "pipeline-51-failed"})

    def test_muting_another_object_leaves_deployments_alone(self):
        _, headers = self.run_sync(["release:*"])
        self.assertEqual(self.events(headers), [("deployment", "blocked"), ("deployment", "failed")])


if __name__ == "__main__":
    unittest.main()
