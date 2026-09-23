"""GitLab git activity prefers Issue thread, then MR, then feature-branch thread.

2026-09-18 notification policy cut live push records, so these tests feed the ladder
synthetic digest records (the exact shape the pre-policy builder produced) through a
record_from_event patch. The ladder machinery stays for a possible policy revert.
"""

import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import urllib.parse

SKILL = Path(__file__).resolve().parents[1]
SCRIPT = SKILL / "scripts" / "gitlab_buzz_sync.py"


def load_module():
    spec = importlib.util.spec_from_file_location("gitlab_buzz_sync_activity_placement", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SYNC = load_module()

DESK = "d" * 64
CHANNEL = "00000000-0000-4000-8000-0000000000c1"
BOT_ID = 7
BOT = "buzz-sync-bot"
PID = 481
SINCE = "2026-09-13T00:00:00Z"
SERVER_TIME = "2026-09-13T04:00:00Z"
WEB = "http://127.0.0.1:8929/buzz-sync-test/pilot"
def OBJ_URL(tail, iid):
    return f"{WEB}/-/{tail}/{iid}"




def make_issue(iid, **overrides):
    base = {
        "iid": iid, "project_id": PID, "title": f"Issue {iid}", "description": "",
        "state": "opened", "labels": ["type::feature", "status::in-progress"], "milestone": None,
        "confidential": False, "assignees": [],
        "web_url": f"{WEB}/-/issues/{iid}",
        "created_at": "2026-09-13T01:00:00Z", "updated_at": "2026-09-13T01:00:00Z",
    }
    base.update(overrides)
    return base


def make_mr(iid=31, **overrides):
    base = {
        "iid": iid, "project_id": PID, "title": f"MR {iid}", "state": "merged", "draft": False,
        "sha": "a" * 40, "source_branch": "feat/birds-encyclopedia", "target_branch": "staging",
        "labels": [], "reviewers": [], "author": {"username": "alice"},
        "web_url": f"{WEB}/-/merge_requests/{iid}",
        "created_at": "2026-09-13T01:00:00Z", "updated_at": "2026-09-13T01:00:00Z",
    }
    base.update(overrides)
    return base


def push_event(event_id=1, ref="feat/birds-encyclopedia", sha="b" * 40, count=1):
    return {
        "id": event_id, "action_name": "pushed to", "target_type": None, "created_at": "2026-09-13T02:00:00.000Z",
        "author": {"username": "alice"},
        "push_data": {
            "ref_type": "branch", "ref": ref, "commit_count": count,
            "commit_title": "fix: order encyclopedia exporter secret sync",
            "commit_to": sha,
        },
    }


class FakeGitLab:
    def __init__(self):
        self.user = {"id": BOT_ID, "username": BOT}
        self.issue_list = []
        self.issue_note_list = {}
        self.mr_list = []
        self.mr_note_list = {}
        self.closes = {}
        self.related = {}
        self.mr_related = {}
        self.commit_mrs = {}
        self.protected = []
        self.event_list = []
        self.pipeline_list = []
        self.writes = []
        self.default_branch = "master"

    def current_user(self):
        return self.user

    def scan_time(self):
        return SERVER_TIME

    def project(self, project_id):
        return {"id": project_id, "visibility": "private", "web_url": WEB,
                "default_branch": self.default_branch}

    def issues(self, project_id, updated_after):
        return [dict(issue) for issue in self.issue_list]

    def issue(self, project_id, iid):
        try:
            return dict(next(issue for issue in self.issue_list if issue.get("iid") == iid))
        except StopIteration:
            raise SYNC.GitLabHTTPError("GET", f"projects/{project_id}/issues/{iid}", 404) from None

    def notes(self, project_id, iid):
        return list(self.issue_note_list.get(iid, []))

    def add_note(self, project_id, iid, body):
        note = {"id": 1000 + len(self.writes), "author": {"id": BOT_ID, "username": BOT},
                "body": body, "system": False, "created_at": SERVER_TIME}
        self.issue_note_list.setdefault(iid, []).append(note)
        self.writes.append(("issue_note", iid))
        return note["id"]

    def merge_requests(self, project_id, updated_after):
        return [dict(mr) for mr in self.mr_list]

    def merge_request(self, project_id, iid):
        return dict(next(mr for mr in self.mr_list if mr["iid"] == iid))

    def merge_requests_by_source_branch(self, project_id, source_branch):
        return [dict(mr) for mr in self.mr_list if mr.get("source_branch") == source_branch]

    def commit_merge_requests(self, project_id, sha):
        return [dict(next(mr for mr in self.mr_list if mr["iid"] == iid))
                for iid in self.commit_mrs.get(sha, [])]

    def mr_related_issues(self, project_id, iid):
        return [dict(issue) for issue in self.mr_related.get(iid, [])]

    def protected_branches(self, project_id):
        return list(self.protected)

    def members(self, project_id):
        return []

    def events(self, project_id, after_date):
        return list(self.event_list)

    def pipelines(self, project_id, updated_after):
        return list(self.pipeline_list)

    def deployments(self, project_id, updated_after):
        return []

    def releases(self, project_id):
        return []

    def feature_flags(self, project_id):
        return []

    def access_tokens(self, project_id):
        return []

    def mr_notes(self, project_id, iid):
        return list(self.mr_note_list.get(iid, []))

    def mr_closes_issues(self, project_id, iid):
        return [dict(issue) for issue in self.closes.get(iid, [])]

    def related_merge_requests(self, project_id, issue_iid):
        return [dict(next(mr for mr in self.mr_list if mr["iid"] == mr_iid))
                for mr_iid in self.related.get(issue_iid, [])]

    def add_mr_note(self, project_id, iid, body):
        note = {"id": 2000 + len(self.writes), "author": {"id": BOT_ID, "username": BOT},
                "body": body, "system": False, "created_at": SERVER_TIME}
        self.mr_note_list.setdefault(iid, []).append(note)
        self.writes.append(("mr_note", iid))
        return note["id"]


class FakeBuzz:
    def __init__(self):
        self.events = []
        self.writes = []

    def send(self, content, reply_to=None, mentions=()):
        event_id = hashlib.sha256(f"{len(self.events)}:{content}".encode()).hexdigest()
        tags = [["h", CHANNEL]]
        if reply_to:
            tags.append(["e", reply_to, "", "reply"])
        tags.extend(["p", pubkey] for pubkey in mentions)
        self.events.append({"id": event_id, "pubkey": DESK, "kind": 9,
                            "created_at": 1000 + len(self.events), "tags": tags, "content": content})
        self.writes.append((reply_to, content, tuple(mentions)))
        return event_id

    def channel_messages(self, since_unix):
        return [e for e in self.events if e["kind"] == 9]

    def thread(self, root_event_id):
        members, frontier = {root_event_id}, {root_event_id}
        while frontier:
            next_frontier = set()
            for event in self.events:
                for tag in event.get("tags") or []:
                    if tag[0] == "e" and tag[1] in frontier and event["id"] not in members:
                        members.add(event["id"])
                        next_frontier.add(event["id"])
            frontier = next_frontier
        return [e for e in self.events if e["id"] in members]

    def search_roots(self, query, object_kind, iid, since_unix=None):
        # issue #78: recovery searches by the canonical object URL (plaque or legacy root)
        return [e for e in self.events
                if query in str(e.get("content", ""))
                and not any(tag[0] == "e" for tag in e.get("tags") or [])]

    def channel_members(self):
        return {}


class ActivityPlacementTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.gitlab = FakeGitLab()
        self.buzz = FakeBuzz()
        self.config = {
            "channel_id": CHANNEL, "publisher_pubkey": DESK, "since": SINCE,
            "include_confidential": False, "exclude": [], "diff": {"enabled": False, "private": False},
            "gitlab": {"base_url": "http://127.0.0.1:8929", "token_env": "NH_DESK_GITLAB_TOKEN",
                       "bot_user_id": BOT_ID, "bot_username": BOT, "projects": [PID]},
            "buzz": {}, "people": {},
        }

    def run_sync(self):
        original = SYNC.record_from_event

        def synthetic_push_digest(event, project_id, web_url):
            push = event.get("push_data")
            if not isinstance(push, dict):
                return original(event, project_id, web_url)
            raw_ref = SYNC._single_line(push.get("ref") or "")
            quoted = urllib.parse.quote(raw_ref, safe="/")
            action = str(event.get("action_name") or "")
            kind = "branch_deleted" if action.startswith("deleted") else (
                "branch_created" if action.startswith("pushed new") else "pushed")
            url = f"{web_url}/-/branches" if kind == "branch_deleted" else f"{web_url}/-/commits/{quoted}"
            return SYNC._record(
                f"event-{event['id']}", "push", kind, "digest", project_id, url=url,
                created_at=event.get("created_at"),
                actor=SYNC.neutralize(SYNC._single_line((event.get("author") or {}).get("username") or "?")),
                ref=SYNC.neutralize(raw_ref), commits=push.get("commit_count") or 0,
                title=SYNC.neutralize(SYNC.sanitize_title(push.get("commit_title") or "")),
                sha=push.get("commit_to") if isinstance(push.get("commit_to"), str) else "",
            )

        with mock.patch.object(SYNC, "record_from_event", side_effect=synthetic_push_digest):
            return SYNC.Syncer(self.config, self.gitlab, self.buzz, state_dir=Path(self.tmp.name)).run()

    def test_branch_key_and_protected_ref(self):
        key = SYNC.branch_key(PID, "feat/birds-encyclopedia")
        self.assertRegex(key, r"^[a-f0-9]{12}$")
        self.assertEqual(key, SYNC.branch_key(PID, "refs/heads/feat/birds-encyclopedia"))
        self.assertTrue(SYNC.ref_is_protected("staging", "master", [{"name": "staging"}]))
        self.assertTrue(SYNC.ref_is_protected("release/KB2.26", "master", [{"name": "release/*"}]))
        self.assertFalse(SYNC.ref_is_protected("feat/birds-encyclopedia", "master", [{"name": "staging"}]))

    def test_push_on_related_feature_branch_goes_to_issue_thread(self):
        """Related MR on the source branch sends the push into that Issue thread, not the digest."""
        self.gitlab.issue_list = [make_issue(270)]
        self.gitlab.mr_list = [make_mr(936)]
        self.gitlab.mr_related = {936: [make_issue(270), make_issue(128)]}
        self.gitlab.related = {270: [936]}
        self.gitlab.event_list = [push_event()]
        summary = self.run_sync()
        self.assertEqual(summary["summary_requests"], [])
        issue_root = self.buzz.search_roots(OBJ_URL("issues", 270), "issue", 270)[0]["id"]
        replies = [e for e in self.buzz.thread(issue_root) if e["id"] != issue_root]
        self.assertTrue(any("[events:event-1]" in e["content"]
                            for e in replies))
        self.assertEqual(self.buzz.search_roots(OBJ_URL("issues", 128), "issue", 128), [])
        self.assertFalse(any((SYNC.parse_header(e["content"]) or {}).get("object") == "branch" for e in self.buzz.events))

    def test_push_without_issue_goes_to_mr_thread(self):
        self.gitlab.mr_list = [make_mr(965, state="merged")]
        self.gitlab.event_list = [push_event()]
        summary = self.run_sync()
        self.assertEqual(summary["summary_requests"], [])
        mr_root = self.buzz.search_roots(OBJ_URL("merge_requests", 965), "mr", 965)[0]["id"]
        self.assertTrue(any("[events:event-1]" in e["content"] for e in self.buzz.thread(mr_root)))

    def test_feature_branch_without_mr_opens_branch_thread_and_reuses_it(self):
        self.gitlab.event_list = [push_event(1), push_event(2, sha="c" * 40)]
        first = self.run_sync()
        self.assertEqual(first["summary_requests"], [])
        roots = [e for e in self.buzz.events
                 if (SYNC.plaque_identity(SYNC.plaque_url(e["content"]) or "") or {}).get("object") == "branch"
                 and not any(tag[0] == "e" for tag in e["tags"])]
        self.assertEqual(len(roots), 1)
        self.assertIn("🌿 **feat/birds-encyclopedia**", roots[0]["content"])
        root = roots[0]["id"]
        self.assertEqual(sum(1 for _reply, content, _ in self.buzz.writes if "[events:event-" in content), 2)
        self.gitlab.event_list = [push_event(3, sha="d" * 40)]
        self.run_sync()
        roots_after = [e for e in self.buzz.events
                       if (SYNC.plaque_identity(SYNC.plaque_url(e["content"]) or "") or {}).get("object") == "branch"
                       and not any(tag[0] == "e" for tag in e["tags"])]
        self.assertEqual([e["id"] for e in roots_after], [root])

    def test_protected_branch_without_mr_stays_in_summary(self):
        self.gitlab.protected = [{"name": "staging"}]
        self.gitlab.event_list = [push_event(ref="staging")]
        summary = self.run_sync()
        self.assertEqual(len(summary["summary_requests"]), 1)
        self.assertEqual(summary["summary_requests"][0]["facts"][0]["object"], "push")
        self.assertFalse(any((SYNC.parse_header(e["content"]) or {}).get("object") == "branch" for e in self.buzz.events))

    def test_unknown_protected_branches_fail_closed_to_digest(self):
        def forbidden(project_id):
            raise SYNC.GitLabHTTPError("GET", f"projects/{project_id}/protected_branches", 403)

        self.gitlab.protected_branches = forbidden
        self.gitlab.event_list = [push_event(ref="feat/unconfirmed")]
        summary = self.run_sync()
        self.assertEqual(len(summary["summary_requests"]), 1)
        self.assertFalse(any((SYNC.parse_header(e["content"]) or {}).get("object") == "branch"
                             for e in self.buzz.events))
        self.assertEqual(summary["status"], "degraded")
        self.assertIn(f"{PID}:protected_branches:HTTP 403", summary["degraded"])

    def test_related_query_http_error_degrades_to_digest(self):
        """A 5xx on the source-branch lookup degrades that record to digest; the round and digest survive."""

        def boom(project_id, source_branch):
            raise SYNC.GitLabHTTPError("GET", f"projects/{project_id}/merge_requests", 500)

        self.gitlab.merge_requests_by_source_branch = boom
        self.gitlab.event_list = [push_event()]
        summary = self.run_sync()
        self.assertEqual(summary["status"], "degraded")
        self.assertIn(f"{PID}:related_query:HTTP 500", summary["degraded"])
        self.assertEqual(len(summary["summary_requests"]), 1)
        self.assertEqual(summary["summary_requests"][0]["facts"][0]["object"], "push")
        self.assertFalse(any((SYNC.parse_header(e["content"]) or {}).get("object") == "branch"
                             for e in self.buzz.events))

    def test_related_query_transport_error_degrades_to_digest(self):
        def unreachable(project_id, source_branch):
            raise SYNC.SyncError("GitLab GET merge_requests failed: timed out")

        self.gitlab.merge_requests_by_source_branch = unreachable
        self.gitlab.event_list = [push_event()]
        summary = self.run_sync()
        self.assertEqual(summary["status"], "degraded")
        self.assertIn(f"{PID}:related_query:SyncError", summary["degraded"])
        self.assertEqual(summary["summary_requests"][0]["facts"][0]["object"], "push")
        self.assertFalse(any((SYNC.parse_header(e["content"]) or {}).get("object") == "branch"
                             for e in self.buzz.events))

    def test_closes_issues_error_degrades_to_digest_without_abort(self):
        # The MR exists only for the routing lookup (mr_list stays empty: the primary MR
        # scan must not see it, where a closes_issues failure still fails the whole round).
        def by_branch(project_id, source_branch):
            return [make_mr(965)] if source_branch == "feat/birds-encyclopedia" else []

        def boom(project_id, iid):
            raise SYNC.GitLabHTTPError("GET", f"projects/{project_id}/merge_requests/{iid}/closes_issues", 502)

        self.gitlab.merge_requests_by_source_branch = by_branch
        self.gitlab.mr_closes_issues = boom
        self.gitlab.event_list = [push_event()]
        summary = self.run_sync()
        self.assertEqual(summary["status"], "degraded")
        self.assertIn(f"{PID}:related_query:HTTP 502", summary["degraded"])
        self.assertEqual(summary["summary_requests"][0]["facts"][0]["object"], "push")
        self.assertEqual(self.buzz.search_roots(OBJ_URL("merge_requests", 965), "mr", 965), [])

    def test_protected_branches_server_error_degrades_to_digest(self):
        def boom(project_id):
            raise SYNC.GitLabHTTPError("GET", f"projects/{project_id}/protected_branches", 500)

        self.gitlab.protected_branches = boom
        self.gitlab.event_list = [push_event(ref="feat/unconfirmed")]
        summary = self.run_sync()
        self.assertEqual(summary["status"], "degraded")
        self.assertIn(f"{PID}:related_query:HTTP 500", summary["degraded"])
        self.assertEqual(len(summary["summary_requests"]), 1)
        self.assertFalse(any((SYNC.parse_header(e["content"]) or {}).get("object") == "branch"
                             for e in self.buzz.events))

    def test_related_issue_404_still_means_empty_not_degrade(self):
        """404 on the Issue snapshot keeps its per-endpoint empty-set meaning; nothing degrades."""

        self.gitlab.mr_list = [make_mr(965)]
        self.gitlab.closes = {965: [make_issue(270)]}  # issue 270 is not in issue_list → snapshot 404
        self.gitlab.event_list = [push_event()]
        summary = self.run_sync()
        self.assertEqual(summary["status"], "ok")
        self.assertEqual(summary["degraded"], [])
        mr_root = self.buzz.search_roots(OBJ_URL("merge_requests", 965), "mr", 965)[0]["id"]
        self.assertTrue(any("[events:event-1]" in e["content"] for e in self.buzz.thread(mr_root)))

    def test_degrade_signals_collapse_within_a_round(self):
        def boom(project_id, source_branch):
            raise SYNC.GitLabHTTPError("GET", f"projects/{project_id}/merge_requests", 500)

        self.gitlab.merge_requests_by_source_branch = boom
        self.gitlab.event_list = [push_event(1), push_event(2, sha="c" * 40)]
        summary = self.run_sync()
        self.assertEqual(summary["degraded"], [f"{PID}:related_query:HTTP 500"])
        self.assertEqual(len(summary["summary_requests"][0]["facts"]), 2)

    def test_protected_merge_commit_uses_commit_merge_requests(self):
        sha = "1" * 40
        self.gitlab.protected = [{"name": "staging"}]
        self.gitlab.issue_list = [make_issue(270)]
        self.gitlab.mr_list = [make_mr(965, merge_commit_sha=sha)]
        self.gitlab.commit_mrs = {sha: [965]}
        self.gitlab.closes = {965: [make_issue(270)]}
        self.gitlab.event_list = [push_event(ref="staging", sha=sha)]
        summary = self.run_sync()
        self.assertEqual(summary["summary_requests"], [])
        issue_root = self.buzz.search_roots(OBJ_URL("issues", 270), "issue", 270)[0]["id"]
        self.assertTrue(any("[events:event-1]" in e["content"] for e in self.buzz.thread(issue_root)))

    def test_canonical_mr_prefers_merged_over_open_draft(self):
        chosen = SYNC.canonical_mr([
            make_mr(10, state="opened", draft=True, updated_at="2026-09-18T04:00:00Z"),
            make_mr(11, state="merged", draft=False, updated_at="2026-09-17T04:00:00Z"),
        ])
        self.assertEqual(chosen["iid"], 11)

    def test_canonical_mr_ignores_closed_only_candidates(self):
        self.assertIsNone(SYNC.canonical_mr([make_mr(12, state="closed")]))

    def test_excluded_related_issues_do_not_consume_the_issue_cap(self):
        self.gitlab.issue_list = [
            make_issue(128, confidential=True),
            make_issue(129, confidential=True),
            make_issue(130, confidential=True),
            make_issue(270),
        ]
        self.gitlab.mr_list = [make_mr(936)]
        self.gitlab.mr_related = {936: [make_issue(128, confidential=True), make_issue(129, confidential=True),
                                       make_issue(130, confidential=True), make_issue(270)]}
        self.gitlab.related = {128: [936], 129: [936], 130: [936], 270: [936]}
        self.gitlab.event_list = [push_event()]
        self.run_sync()
        self.assertTrue(any("[events:event-1]" in e["content"]
                            and "[issue:270]" in (SYNC.header_line(e["content"]) or "")
                            for e in self.buzz.events))

    def test_branch_created_opens_feature_branch_thread(self):
        self.gitlab.event_list = [push_event(1, ref="feat/new-wiki")]
        self.gitlab.event_list[0]["action_name"] = "pushed new"
        summary = self.run_sync()
        self.assertEqual(summary["summary_requests"], [])
        roots = [e for e in self.buzz.events
                 if (SYNC.plaque_identity(SYNC.plaque_url(e["content"]) or "") or {}).get("object") == "branch"
                 and not any(tag[0] == "e" for tag in e["tags"])]
        self.assertEqual(len(roots), 1)
        self.assertTrue(any("[events:event-1]" in e["content"] for e in self.buzz.events))

    def test_branch_deleted_without_thread_stays_in_summary(self):
        event = push_event(1, ref="feat/gone")
        event["action_name"] = "deleted"
        event["push_data"]["commit_count"] = 0
        self.gitlab.event_list = [event]
        summary = self.run_sync()
        self.assertEqual(len(summary["summary_requests"]), 1)
        self.assertFalse(any((SYNC.parse_header(e["content"]) or {}).get("object") == "branch"
                             for e in self.buzz.events))

    def test_noisy_related_issues_are_dropped_without_issue_side_link(self):
        self.gitlab.issue_list = [make_issue(270), make_issue(128)]
        self.gitlab.mr_list = [make_mr(936)]
        self.gitlab.mr_related = {936: [make_issue(270), make_issue(128)]}
        self.gitlab.related = {270: [936]}
        self.gitlab.event_list = [push_event()]
        self.run_sync()
        self.assertEqual(len(self.buzz.search_roots(OBJ_URL("issues", 270), "issue", 270)), 1)
        noisy = self.buzz.search_roots(OBJ_URL("issues", 128), "issue", 128)
        self.assertTrue(noisy)
        self.assertFalse(any("[events:event-1]" in e["content"] for e in self.buzz.thread(noisy[0]["id"])))


if __name__ == "__main__":
    unittest.main()
