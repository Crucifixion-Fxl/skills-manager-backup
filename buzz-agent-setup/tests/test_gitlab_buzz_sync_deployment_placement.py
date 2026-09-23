"""Deployment events ride the attribution ladder (issue #77, 2026-09-18).

The 2026-09-18 notification policy keeps only failed/blocked deployments instant; these
tests feed the ladder synthetic digest records for happy-path deployments (the exact
pre-policy shape) through a record_from_deployment patch. The ladder machinery stays
for a possible policy revert.
"""

import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SKILL = Path(__file__).resolve().parents[1]
SCRIPT = SKILL / "scripts" / "gitlab_buzz_sync.py"


def load_module():
    spec = importlib.util.spec_from_file_location("gitlab_buzz_sync_deployment_placement", SCRIPT)
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


def deployment(deployment_id=3836, status="success", ref="feat/birds-encyclopedia", sha="b" * 40):
    return {"id": deployment_id, "iid": 38, "status": status, "ref": ref, "sha": sha,
            "environment": {"name": "pages-publisher"}, "updated_at": "2026-09-13T02:00:00Z"}


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
        self.deployment_list = []
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
        return list(self.deployment_list)

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


class DeploymentPlacementTest(unittest.TestCase):
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
        original = SYNC.record_from_deployment

        def synthetic_deployment_digest(payload, project_id, web_url):
            record = original(payload, project_id, web_url)
            if record is not None:
                return record  # failed/blocked stay instant under the policy
            if payload.get("status") not in SYNC.DEPLOYMENT_STATUSES:
                return None
            deployment_id = payload["id"]
            environment = SYNC.neutralize(SYNC._single_line((payload.get("environment") or {}).get("name") or "-"))
            return SYNC._record(
                f"deployment-{deployment_id}-{payload['status']}", "deployment", payload["status"], "digest",
                project_id, created_at=payload.get("updated_at"),
                ref=SYNC.neutralize(SYNC._single_line(payload.get("ref") or "")),
                title=environment, url=f"{web_url}/-/environments",
                sha=payload.get("sha") if isinstance(payload.get("sha"), str) else "",
            )

        with mock.patch.object(SYNC, "record_from_deployment", side_effect=synthetic_deployment_digest):
            return SYNC.Syncer(self.config, self.gitlab, self.buzz, state_dir=Path(self.tmp.name)).run()

    def test_deployment_on_mr_branch_lands_in_mr_thread(self):
        self.gitlab.mr_list = [make_mr(965, state="opened")]
        self.gitlab.deployment_list = [deployment()]
        summary = self.run_sync()
        self.assertEqual(summary["summary_requests"], [])
        mr_root = self.buzz.search_roots(OBJ_URL("merge_requests", 965), "mr", 965)[0]["id"]
        replies = self.buzz.thread(mr_root)
        self.assertTrue(any("[events:deployment-3836-success]" in e["content"]
                            for e in replies))
        top = [e for e in self.buzz.events if not any(tag[0] == "e" for tag in e["tags"])]
        self.assertFalse(any("deployment" in (SYNC.header_line(e["content"]) or "") for e in top))

    def test_deployment_without_sha_attributes_via_ref(self):
        """A payload with no usable sha degrades to source-branch attribution instead of
        stranding at digest (code-review P2 on !895: sha-missing contract)."""
        self.gitlab.mr_list = [make_mr(966, state="opened")]
        self.gitlab.deployment_list = [deployment(deployment_id=3837, sha="")]
        summary = self.run_sync()
        self.assertEqual(summary["summary_requests"], [])
        mr_root = self.buzz.search_roots(OBJ_URL("merge_requests", 966), "mr", 966)[0]["id"]
        self.assertTrue(any("[events:deployment-" in e["content"]
                            for e in self.buzz.thread(mr_root)))

    def test_deployment_on_issue_linked_mr_lands_in_issue_thread(self):
        self.gitlab.issue_list = [make_issue(270)]
        self.gitlab.mr_list = [make_mr(936, state="opened")]
        self.gitlab.mr_related = {936: [make_issue(270)]}
        self.gitlab.related = {270: [936]}
        self.gitlab.deployment_list = [deployment()]
        summary = self.run_sync()
        self.assertEqual(summary["summary_requests"], [])
        issue_root = self.buzz.search_roots(OBJ_URL("issues", 270), "issue", 270)[0]["id"]
        self.assertTrue(any("[events:deployment-" in e["content"]
                            for e in self.buzz.thread(issue_root)))

    def test_deployment_on_default_branch_goes_to_digest(self):
        """The #77 report: recurring docs-pages style deploys leave the channel top level."""
        self.gitlab.deployment_list = [deployment(ref="master")]
        summary = self.run_sync()
        self.assertEqual(len(summary["summary_requests"]), 1)
        self.assertEqual(summary["summary_requests"][0]["facts"][0]["object"], "deployment")
        self.assertEqual(summary["notified"], {"instant": 0, "milestone": 0})
        self.assertFalse(any("deployment" in (SYNC.header_line(e["content"]) or "") for e in self.buzz.events))

    def test_failed_deployment_stays_instant_top_level(self):
        self.gitlab.deployment_list = [deployment(status="failed", ref="master")]
        summary = self.run_sync()
        self.assertEqual(summary["summary_requests"], [])
        self.assertEqual(summary["notified"], {"instant": 1, "milestone": 0})
        top = [e for e in self.buzz.events if not any(tag[0] == "e" for tag in e["tags"])]
        header = SYNC.parse_header(top[0]["content"])
        self.assertEqual((header["object"], header["event"]), ("deployment", "failed"))
        self.assertIn("🔴 **部署失败** · pages-publisher", top[0]["content"])

    def test_deployment_on_feature_branch_without_mr_opens_branch_thread(self):
        self.gitlab.deployment_list = [deployment(ref="feat/release-scripts")]
        summary = self.run_sync()
        self.assertEqual(summary["summary_requests"], [])
        roots = [e for e in self.buzz.events
                 if (SYNC.plaque_identity(SYNC.plaque_url(e["content"]) or "") or {}).get("object") == "branch"
                 and not any(tag[0] == "e" for tag in e["tags"])]
        self.assertEqual(len(roots), 1)
        self.assertTrue(any("[events:deployment-" in e["content"]
                            for e in self.buzz.thread(roots[0]["id"])))


if __name__ == "__main__":
    unittest.main()
