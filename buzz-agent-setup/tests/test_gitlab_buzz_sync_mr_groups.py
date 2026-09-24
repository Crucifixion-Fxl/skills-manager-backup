"""Unassociated MRs share one thread per branch family (issue #77, 2026-09-18)."""

import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
SCRIPT = SKILL / "scripts" / "gitlab_buzz_sync.py"


def load_module():
    spec = importlib.util.spec_from_file_location("gitlab_buzz_sync_mr_groups", SCRIPT)
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




def make_mr(iid, source, target, **overrides):
    base = {
        "iid": iid, "project_id": PID, "title": f"MR {iid}", "state": "opened", "draft": True,
        "sha": "a" * 40, "source_branch": source, "target_branch": target,
        "labels": [], "reviewers": [], "author": {"username": "sshao"},
        "web_url": f"{WEB}/-/merge_requests/{iid}",
        "created_at": f"2026-09-13T0{iid % 10}:30:00Z", "updated_at": f"2026-09-13T0{iid % 10}:31:00Z",
    }
    base.update(overrides)
    return base


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
        self.reactions = {}

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

    def set_status_reaction(self, event_id, emoji):
        self.reactions[event_id] = emoji

    def status_reaction_matches(self, event_id, emoji):
        return self.reactions.get(event_id) == emoji


def root_mr_iid(content):
    header = SYNC.parse_header(content)
    if header is not None and header.get("object") == "mr":
        return header["mr"]
    ident = SYNC.plaque_identity(SYNC.plaque_url(content) or "")
    return ident["iid"] if ident and ident.get("object") == "mr" else None


def top_level_mr_roots(buzz):
    return [e for e in buzz.events
            if root_mr_iid(e["content"]) is not None
            and not any(tag[0] == "e" for tag in e["tags"])]


class MRGroupTest(unittest.TestCase):
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
        return SYNC.Syncer(self.config, self.gitlab, self.buzz, state_dir=Path(self.tmp.name)).run()

    def test_strip_target_suffix_edges(self):
        self.assertEqual(SYNC.strip_target_suffix("fix/x-staging", "staging"), "fix/x")
        self.assertEqual(SYNC.strip_target_suffix("fix/x-staging", "master"), "")
        self.assertEqual(SYNC.strip_target_suffix("staging", "staging"), "")
        self.assertEqual(SYNC.strip_target_suffix("-", "staging"), "")
        self.assertEqual(SYNC.strip_target_suffix("fix/x", "staging"), "")
        fact = SYNC.mr_fact(make_mr(1, "fix/x-staging", "staging"), PID)
        self.assertEqual(SYNC.mr_group_keys(fact), ("fix/x-staging", "fix/x"))

    def test_staging_master_fanout_shares_one_thread(self):
        """The #77 case: fix/<base>-staging and fix/<base>-master by one author land in one thread."""
        self.config["compact_status_updates"] = True
        self.gitlab.mr_list = [
            make_mr(968, "fix/postcard-alertgroup-staging", "staging"),
            make_mr(969, "fix/postcard-alertgroup-master", "master"),
        ]
        summary = self.run_sync()

        roots = top_level_mr_roots(self.buzz)
        self.assertEqual(len(roots), 1, [r["content"].split("\n")[0] for r in roots])
        anchor = roots[0]
        self.assertIn("!968", anchor["content"])  # earliest created MR is the group anchor

        thread_events = self.buzz.thread(anchor["id"])
        joined = [e for e in thread_events
                  if "[mr:969]" in (SYNC.header_line(e["content"]) or "")
                  and "[change:lifecycle]" in (SYNC.header_line(e["content"]) or "")]
        self.assertEqual(len(joined), 1)
        self.assertEqual(joined[0]["tags"][1][1], anchor["id"])  # first fact replies to the group root
        self.assertEqual(self.buzz.reactions[joined[0]["id"]], "📝")

        for iid in (968, 969):  # both binding notes point into the one thread
            note = self.gitlab.mr_note_list[iid][0]["body"]
            self.assertIn(anchor["id"], note)
        state = json.loads((Path(self.tmp.name) / f"mr-group-bindings-{PID}.json").read_text())
        self.assertEqual(state["fix/postcard-alertgroup-staging"]["root"], anchor["id"])
        self.assertEqual(state["fix/postcard-alertgroup"]["root"], anchor["id"])
        self.assertEqual(summary["summary_requests"], [])

    def test_same_source_branch_multiple_targets_share_thread(self):
        self.gitlab.mr_list = [
            make_mr(101, "fix/shared", "staging"),
            make_mr(102, "fix/shared", "master"),
        ]
        self.run_sync()
        roots = top_level_mr_roots(self.buzz)
        self.assertEqual(len(roots), 1)
        self.assertIn("!101", roots[0]["content"])
        self.assertTrue(any("[mr:102]" in e["content"] for e in self.buzz.thread(roots[0]["id"])))

    def test_different_author_same_prefix_not_merged(self):
        self.gitlab.mr_list = [
            make_mr(201, "fix/y-staging", "staging", author={"username": "alice"}),
            make_mr(202, "fix/y-master", "master", author={"username": "bob"}),
        ]
        self.run_sync()
        roots = top_level_mr_roots(self.buzz)
        self.assertEqual(len(roots), 2)
        self.assertEqual({root_mr_iid(r["content"]) for r in roots}, {201, 202})
        for root in roots:  # no foreign facts in either solo thread
            iids = {root_mr_iid(e["content"]) for e in self.buzz.thread(root["id"])}
            self.assertEqual(len(iids), 1)

    def test_reviewable_fires_once_per_group(self):
        self.gitlab.mr_list = [
            make_mr(301, "fix/z-staging", "staging", draft=True),
            make_mr(302, "fix/z-master", "master", draft=False),
        ]
        self.run_sync()
        anchor = top_level_mr_roots(self.buzz)[0]
        headers = [h for h in (SYNC.parse_header(e["content"]) for e in self.buzz.thread(anchor["id"])) if h]
        reviewable = [h for h in headers if h.get("transition") == "reviewable"]
        self.assertEqual(len(reviewable), 1)  # the non-draft join fired it; the draft anchor did not

        # the anchor becomes ready later: suppressed, the group thread already assigned review
        self.gitlab.mr_list[0] = {**self.gitlab.mr_list[0], "draft": False,
                                  "updated_at": "2026-09-13T05:31:00Z"}
        self.run_sync()
        headers = [h for h in (SYNC.parse_header(e["content"]) for e in self.buzz.thread(anchor["id"])) if h]
        self.assertEqual(len([h for h in headers if h.get("transition") == "reviewable"]), 1)

    def test_solo_mr_reviewable_is_not_suppressed(self):
        """Solo per-MR threads keep today's behaviour: reopen→review still fires."""
        mr = make_mr(401, "fix/solo", "staging", draft=False, state="opened")
        self.gitlab.mr_list = [mr]
        self.run_sync()
        anchor = top_level_mr_roots(self.buzz)[0]
        self.assertEqual(len([h for h in (SYNC.parse_header(e["content"]) for e in self.buzz.thread(anchor["id"]))
                              if h and h.get("transition") == "reviewable"]), 1)
        closed = {**mr, "state": "closed", "updated_at": "2026-09-13T06:31:00Z"}
        reopened = {**mr, "updated_at": "2026-09-13T07:31:00Z"}
        self.gitlab.mr_list[0] = closed
        self.run_sync()
        self.gitlab.mr_list[0] = reopened
        self.run_sync()
        headers = [h for h in (SYNC.parse_header(e["content"]) for e in self.buzz.thread(anchor["id"])) if h]
        self.assertEqual(len([h for h in headers if h.get("transition") == "reviewable"]), 2)

    def test_issue_associated_mr_still_merges_into_issue_thread(self):
        issue = {"iid": 77, "project_id": PID, "title": "Alert groups", "description": "",
                 "state": "opened", "labels": [], "milestone": None, "confidential": False,
                 "assignees": [], "web_url": f"{WEB}/-/issues/77",
                 "created_at": "2026-09-13T01:00:00Z", "updated_at": "2026-09-13T01:00:00Z"}
        self.gitlab.issue_list = [issue]
        self.gitlab.mr_list = [
            make_mr(501, "fix/w-staging", "staging"),
            make_mr(502, "fix/w-master", "master"),
        ]
        self.gitlab.closes = {501: [issue]}
        self.run_sync()
        # 501 merged into the Issue thread; 502 has no association and opens the family root
        self.assertTrue(self.buzz.search_roots(OBJ_URL("issues", 77), "issue", 77))
        self.assertFalse(self.buzz.search_roots(OBJ_URL("merge_requests", 501), "mr", 501))
        roots = top_level_mr_roots(self.buzz)
        self.assertEqual(len(roots), 1)
        self.assertIn("!502", roots[0]["content"])

    def test_rerun_posts_nothing_new(self):
        self.gitlab.mr_list = [
            make_mr(968, "fix/postcard-alertgroup-staging", "staging"),
            make_mr(969, "fix/postcard-alertgroup-master", "master"),
        ]
        self.run_sync()
        writes = len(self.buzz.writes)
        summary = self.run_sync()
        self.assertEqual(len(self.buzz.writes), writes)
        self.assertEqual(summary["mr_updated"], 0)

    def test_corrupt_group_state_fails_closed(self):
        self.gitlab.mr_list = [make_mr(601, "fix/q-staging", "staging")]
        (Path(self.tmp.name) / f"mr-group-bindings-{PID}.json").write_text("{not json", encoding="utf-8")
        with self.assertRaises(SYNC.SyncError):
            self.run_sync()

    def test_group_state_invalid_entry_fails_closed(self):
        """One off-schema anchor fails the round like file-level corruption, even beside
        valid entries: silently dropping it would fork that family's later siblings into
        a new thread (code-review P1 on !895; reference promises fail-closed)."""
        self.gitlab.mr_list = [make_mr(611, "fix/s-staging", "staging")]
        path = Path(self.tmp.name) / f"mr-group-bindings-{PID}.json"
        good = {"root": "a" * 64, "author": "alice"}
        for bad in (
            {"root": "nothex", "author": "alice"},   # root not event-id shaped
            {"author": "alice"},                     # root missing
            {"root": "a" * 64},                      # author missing
            {"root": "a" * 64, "author": ""},        # author empty
            {"root": "a" * 64, "author": 7},         # author not a string
            "flat",                                  # entry not an object
        ):
            with self.subTest(bad=bad):
                path.write_text(json.dumps({"fix/s": good, "fix/s-staging": bad}), encoding="utf-8")
                with self.assertRaises(SYNC.SyncError):
                    self.run_sync()

    def test_lost_group_state_does_not_duplicate_thread(self):
        """Bindings still hold after state loss; only future siblings may fork a new thread (#71 model)."""
        self.gitlab.mr_list = [
            make_mr(701, "fix/r-staging", "staging"),
            make_mr(702, "fix/r-master", "master"),
        ]
        self.run_sync()
        anchor = top_level_mr_roots(self.buzz)[0]["id"]
        (Path(self.tmp.name) / f"mr-group-bindings-{PID}.json").unlink()
        writes = len(self.buzz.writes)
        self.run_sync()  # both MRs are bound to the anchor; nothing reopens or duplicates
        self.assertEqual(len(self.buzz.writes), writes)
        self.assertEqual(len(top_level_mr_roots(self.buzz)), 1)
        self.assertIn(anchor, self.gitlab.mr_note_list[702][0]["body"])


if __name__ == "__main__":
    unittest.main()
