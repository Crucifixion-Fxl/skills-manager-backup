"""L3 product-demo sync cases that cross real GitLab and Buzz public boundaries.

These cases complement routing_e2e.py.  They run the shipped script as a subprocess
against local GitLab CE and a real relay; they never invoke Syncer internals or fake
an adapter.  The real agents remain running so the shared Channel/runtime topology is
the same as routing L3, although these sync-only scenes do not require an LLM turn.
"""
from __future__ import annotations

import secrets
import unittest

try:
    from .e2e_support import ENABLED, E2ECase, wait_for
    from .test_sync_local import first_line, mr_header, tag_values
except ImportError:  # imported as a top-level module
    from e2e_support import ENABLED, E2ECase, wait_for
    from test_sync_local import first_line, mr_header, tag_values


@unittest.skipUnless(ENABLED, "L3 runs only with BUZZ_SYNC_L3=1 and the localstack up")
class ProductDemoSyncE2ETest(E2ECase):
    def run_config(self, config: dict, *, workdir=None, token: str | None = None):
        workdir = workdir or self.tmp
        run = self.stack.run_sync(config, workdir, token)
        self.assertEqual((run.returncode, run.status), (0, "ok"), run.describe())
        if run.result.get("summary_requests"):
            # ack durable summary requests through the real claim + publisher,
            # then redo the run so callers see unblocked counters
            for request in run.result["summary_requests"]:
                self.stack.claim_oldest_summary(config, workdir)
                facts = request.get("facts") or []
                prose = f"本轮共有 {len(facts)} 条分支活动。"
                outcome = self.stack.run_publisher(
                    config, workdir, prose, request.get("facts_sha256", ""))
                assert outcome.get("status") in {"sent", "duplicate"}, outcome
            run = self.stack.run_sync(config, workdir, token)
            self.assertEqual((run.returncode, run.status), (0, "ok"), run.describe())
        return run

    def test_001_mr_lifecycle_is_message_and_patch_is_stream_diff(self):
        """L3-GIS-011 / L3-GIS-DEMO-11 Real MR open/update/merge facts are kind 9; only real per-file patches are kind 40008 in the MR Thread."""
        path = f"l3/{self.stamp}.txt"
        branch = f"feature/demo11-{self.stamp}"
        sha = self.stack.branch_with_files(branch, {path: "one\n"}, f"feat: demo11 {self.stamp}")
        mr = self.stack.create_mr(branch, f"l3 demo11 {self.stamp}")
        iid, pid = mr["iid"], self.stack.project_id
        wait_for("MR head sha", lambda: self.stack.mr(iid).get("sha") == sha, 60)
        config = self.stack.config(self.since, people={}, diff={"enabled": True, "private": True})

        self.run_config(config)
        root = self.only_root("mr", iid)
        self.assertEqual((root["kind"], first_line(root)),
                         (9, mr_header(pid, iid, "opened", "no", "lifecycle", "reviewable")))
        initial_diffs = self.stack.publisher_thread(root["id"], kind=40008)
        self.assertEqual([tag[1] for event in initial_diffs for tag in tag_values(event, "file")], [path])

        next_sha = self.stack.commit(branch, {path: "two\n"}, f"fix: demo11 {self.stamp}")
        wait_for("updated MR head sha", lambda: self.stack.mr(iid).get("sha") == next_sha, 60)
        self.run_config(config)
        updates = [event for event in self.stack.publisher_thread(root["id"])
                   if first_line(event) == mr_header(pid, iid, "opened", "no", "update")]
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0]["kind"], 9)
        self.assertTrue(all(event["kind"] == 40008 for event in self.stack.publisher_thread(root["id"], kind=40008)))

        wait_for(
            "MR mergeable",
            lambda: self.stack.ok("maintainer", "GET", f"{self.stack.base}/merge_requests/{iid}",
                                  {"with_merge_status_recheck": "true"}).get("detailed_merge_status") == "mergeable",
            90,
        )
        self.stack.ok("maintainer", "PUT", f"{self.stack.base}/merge_requests/{iid}/merge")
        wait_for("MR merged", lambda: self.stack.mr(iid).get("state") == "merged", 90)
        self.run_config(config)
        merged = [event for event in self.stack.publisher_thread(root["id"])
                  if first_line(event) == mr_header(pid, iid, "merged", "no", "lifecycle")]
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["kind"], 9)

    def test_002_oversize_file_is_skipped_without_hiding_small_diff(self):
        """L3-GIS-012 / L3-GIS-DEMO-15 A real MR file over the 60 KiB event budget is counted as skipped and emits no kind 40008; a small sibling file still posts exactly."""
        big = f"l3/{self.stamp}-big.txt"
        small = f"l3/{self.stamp}-small.txt"
        branch = f"feature/demo15-{self.stamp}"
        sha = self.stack.branch_with_files(
            branch,
            {big: "".join(f"line-{index:05d}-xxxxxxxxxxxxxxxx\n" for index in range(4000)), small: "small\n"},
            f"feat: demo15 {self.stamp}",
        )
        mr = self.stack.create_mr(branch, f"l3 demo15 {self.stamp}")
        wait_for("MR head sha", lambda: self.stack.mr(mr["iid"]).get("sha") == sha, 60)
        config = self.stack.config(self.since, people={}, diff={"enabled": True, "private": True})

        run = self.run_config(config)
        self.assertGreaterEqual(run.result["diff_skipped"], 1, run.describe())
        root = self.only_root("mr", mr["iid"])
        files = [tag[1] for event in self.stack.publisher_thread(root["id"], kind=40008)
                 for tag in tag_values(event, "file")]
        self.assertEqual(files, [small])
        self.assertNotIn(big, files)

    def test_003_deleted_binding_is_rebuilt_from_real_gitlab_and_buzz_facts(self):
        """L3-GIS-013 / L3-GIS-DEMO-19 Deleting the real GitLab binding then rerunning the public script reuses the one Buzz root and recreates one binding."""
        issue = self.stack.create_issue(f"l3 demo19 {self.stamp}")
        config = self.stack.config(self.since, people={})
        self.run_config(config)
        root = self.only_root("issue", issue["iid"])
        bindings = self.stack.bindings("issue", issue["iid"])
        self.assertEqual(len(bindings), 1)
        self.stack.ok("bot", "DELETE",
                      f"{self.stack.base}/issues/{issue['iid']}/notes/{bindings[0]['note_id']}")
        self.assertEqual(self.stack.bindings("issue", issue["iid"]), [])

        run = self.run_config(config)
        self.assertGreaterEqual(run.result["recovered"], 1, run.describe())
        self.assertEqual(self.only_root("issue", issue["iid"])["id"], root["id"])
        self.assert_single_binding("issue", issue["iid"], root)

    def test_004_bad_token_project_and_channel_fail_before_any_write(self):
        """L3-GIS-014 / L3-GIS-DEMO-21 Bad credential, project and Channel mappings fail from the real process boundary with zero Buzz/GitLab writes and redacted output."""
        issue = self.stack.create_issue(f"l3 demo21 {self.stamp}")
        invalid = "glpat-" + secrets.token_hex(12)
        wrong_project = self.stack.config(self.since, people={},
                                          gitlab={"projects": [self.stack.project_id + 999999]})
        wrong_channel = self.stack.config(self.since, people={},
                                          channel_id="00000000-0000-4000-8000-000000000001")
        cases = {
            "token": self.stack.run_sync(self.stack.config(self.since, people={}), self.tmp / "token", invalid),
            "project": self.stack.run_sync(wrong_project, self.tmp / "project"),
            "channel": self.stack.run_sync(wrong_channel, self.tmp / "channel"),
        }
        for name, run in cases.items():
            with self.subTest(name=name):
                self.assertEqual((run.returncode, run.status), (1, "error"), run.describe())
                self.assertNotIn(invalid, run.stdout + run.stderr)
                self.assertNotIn("glpat-", run.stdout + run.stderr)
        self.assertEqual(self.stack.roots("issue", issue["iid"], self.started_unix), [])
        self.assertEqual(self.stack.notes("issue", issue["iid"]), [])


if __name__ == "__main__":
    unittest.main()
