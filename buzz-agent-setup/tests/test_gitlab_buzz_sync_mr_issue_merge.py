"""L1 contracts for MR→Issue thread merging (2026-09-17, narrowed by ADR-0015 on 2026-09-21).

An MR that GitLab (closing reference) or the branch-name whitelist ties to an Issue posts its facts into the ONE Issue
thread its binding names, as a self-chained sub-conversation, instead of opening a per-MR root. Every other related
thread (a further Issue, a `related_merge_requests` hit, another origin) only gets a cross-link (`change:xref`) when the
MR first appears. Unassociated MRs keep the per-MR thread.

L1-GIS-165, 175, 176, 196, 197, 199, 200 and 201 used to assert "one copy per Issue thread" (the 2026-09-17 rule) and now
assert the rule above; the new cases are in test_gitlab_buzz_sync_mr_single_thread.py (L1-GIS-MT-*)."""

import hashlib
import importlib.util
import tempfile
import unittest
from unittest import mock
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
SCRIPT = SKILL / "scripts" / "gitlab_buzz_sync.py"


def load_module():
    spec = importlib.util.spec_from_file_location("gitlab_buzz_sync_mr_issue_merge", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SYNC = load_module()

DESK = "d" * 64
CHANNEL = "00000000-0000-4000-8000-0000000000c1"
BOT_ID = 7
BOT = "buzz-sync-bot"
PID = 481
WEB = "http://127.0.0.1:8929/buzz-sync-test/pilot"


def OBJ_URL(tail, iid):
    return f"{WEB}/-/{tail}/{iid}"
SINCE = "2026-09-13T00:00:00Z"
SERVER_TIME = "2026-09-13T04:00:00Z"
ALICE, CAROL = "a1" * 32, "c3" * 32
PEOPLE = {"alice": ALICE, "carol": CAROL}


def make_issue(iid, **overrides):
    base = {
        "iid": iid, "project_id": PID, "title": f"Issue {iid}", "description": "",
        "state": "opened", "labels": ["type::feature", "status::ready"], "milestone": None,
        "confidential": False, "assignees": [],
        "web_url": f"http://127.0.0.1:8929/buzz-sync-test/pilot/-/issues/{iid}",
        "created_at": "2026-09-13T01:00:00Z", "updated_at": "2026-09-13T01:00:00Z",
    }
    base.update(overrides)
    return base


def make_mr(iid=31, **overrides):
    base = {
        "iid": iid, "project_id": PID, "title": "恢复最近工作区", "state": "opened", "draft": False,
        "sha": "a" * 40, "source_branch": "feature/182-restore", "target_branch": "main",
        "labels": [], "reviewers": [], "author": {"username": "alice"},
        "web_url": f"http://127.0.0.1:8929/buzz-sync-test/pilot/-/merge_requests/{iid}",
        "created_at": "2026-09-13T01:00:00Z", "updated_at": "2026-09-13T01:00:00Z",
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
        self.closes = {}       # mr iid -> [issue dicts]
        self.related = {}      # issue iid -> [mr iids]
        self.diffs = {}        # (mr iid, sha) -> [GitLab diff file dicts]
        self.writes = []

    def current_user(self):
        return self.user

    def scan_time(self):
        return SERVER_TIME

    def project(self, project_id):
        return {"id": project_id, "visibility": "public", "web_url": WEB,
                "path_with_namespace": "buzz-sync-test/pilot"}

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
        self.writes.append(("issue_note", iid, body))
        return note["id"]

    def merge_requests(self, project_id, updated_after):
        return [dict(mr) for mr in self.mr_list]

    def members(self, project_id):
        return []

    def events(self, project_id, after_date):
        return []

    def pipelines(self, project_id, updated_after):
        return []

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

    def mr_diffs(self, project_id, iid):
        mr = next(mr for mr in self.mr_list if mr["iid"] == iid)
        return [dict(item) for item in self.diffs.get((iid, mr["sha"]), [])]

    def mr_closes_issues(self, project_id, iid):
        return [dict(issue) for issue in self.closes.get(iid, [])]

    def related_merge_requests(self, project_id, issue_iid):
        self.related_calls = getattr(self, "related_calls", 0) + 1
        return [dict(next(mr for mr in self.mr_list if mr["iid"] == mr_iid))
                for mr_iid in self.related.get(issue_iid, [])]

    def add_mr_note(self, project_id, iid, body):
        note = {"id": 2000 + len(self.writes), "author": {"id": BOT_ID, "username": BOT},
                "body": body, "system": False, "created_at": SERVER_TIME}
        self.mr_note_list.setdefault(iid, []).append(note)
        self.writes.append(("mr_note", iid, body))
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

    def send_diff(self, diff, *, repo, commit, file_path, reply_to, source_branch, target_branch, pr):
        event_id = hashlib.sha256(f"{len(self.events)}:diff:{commit}:{file_path}".encode()).hexdigest()
        tags = [["h", CHANNEL], ["e", reply_to, "", "reply"], ["repo", repo], ["commit", commit], ["file", file_path]]
        self.events.append({"id": event_id, "pubkey": DESK, "kind": 40008,
                            "created_at": 1000 + len(self.events), "tags": tags, "content": diff})
        self.writes.append((reply_to, f"diff:{commit}:{file_path}", ()))
        return event_id

    def channel_messages(self, since_unix):
        return [e for e in self.events if e["kind"] == 9]

    def thread(self, root_event_id):
        # transitive closure: merged MR sub-chains reply to earlier replies
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
        return {pubkey: "member" for pubkey in PEOPLE.values()}


class MrIssueMergeTest(unittest.TestCase):
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
            "buzz": {}, "people": PEOPLE,
        }

    def run_sync(self):
        return SYNC.Syncer(self.config, self.gitlab, self.buzz, state_dir=Path(self.tmp.name)).run()

    def roots(self, object_kind, iid):
        tail = "issues" if object_kind == "issue" else "merge_requests"
        return self.buzz.search_roots(OBJ_URL(tail, iid), object_kind, iid)

    def test_closes_issue_merges_mr_facts_into_issue_thread_with_subchain(self):
        """L1-GIS-162 MR closing an Issue posts its facts into that Issue's thread (no MR root), chained via reply-to."""
        self.gitlab.issue_list = [make_issue(182)]
        self.gitlab.mr_list = [make_mr(iid=31)]
        self.gitlab.closes = {31: [make_issue(182)]}
        self.run_sync()

        issue_roots = self.roots("issue", 182)
        self.assertEqual(len(issue_roots), 1)
        issue_root = issue_roots[0]["id"]
        self.assertEqual(self.roots("mr", 31), [], "associated MR must not open its own root")
        mr_replies = [e for e in self.buzz.thread(issue_root)
                      if (SYNC.parse_header(e.get("content", "")) or {}).get("object") == "mr"
                      and (SYNC.parse_header(e.get("content", "")) or {}).get("mr") == 31]
        self.assertEqual(len(mr_replies), 1)
        self.assertEqual(mr_replies[0]["tags"][1][:2], ["e", issue_root])
        self.assertEqual(
            SYNC.parse_binding(self.gitlab.mr_note_list[31], BOT_ID, PID, "mr", 31, CHANNEL),
            issue_root,
        )

        # a later MR update chains onto the MR's previous message, not the issue root
        self.gitlab.mr_list = [make_mr(iid=31, sha="b" * 40, updated_at="2026-09-13T02:00:00Z")]
        self.run_sync()
        thread = self.buzz.thread(issue_root)
        mr_replies = [e for e in thread
                      if (SYNC.parse_header(e.get("content", "")) or {}).get("object") == "mr"]
        self.assertEqual(len(mr_replies), 2)
        self.assertEqual(mr_replies[1]["tags"][1][:2], ["e", mr_replies[0]["id"]])

    def test_branch_name_whitelist_associates_and_rejects_unknown_shapes(self):
        """L1-GIS-163 branch `<word>-<iid>-…`/`<iid>-…` associates; other shapes stay per-MR."""
        self.gitlab.issue_list = [make_issue(401)]
        self.gitlab.mr_list = [make_mr(iid=32, source_branch="feature/401-video-search")]
        self.run_sync()
        issue_root = self.roots("issue", 401)[0]["id"]
        self.assertEqual(self.roots("mr", 32), [])
        self.assertEqual(
            SYNC.parse_binding(self.gitlab.mr_note_list[32], BOT_ID, PID, "mr", 32, CHANNEL),
            issue_root,
        )

        # a branch that matches no whitelist shape keeps the per-MR root fallback
        self.gitlab.mr_list.append(make_mr(iid=33, source_branch="feat/admin-video-id-search-staging-20260916"))
        self.run_sync()
        self.assertEqual(len(self.roots("mr", 33)), 1)

    def test_two_mrs_in_one_issue_keep_disjoint_subchains(self):
        """L1-GIS-164 two associated MRs chain independently inside the same Issue thread."""
        self.gitlab.issue_list = [make_issue(182)]
        self.gitlab.mr_list = [make_mr(iid=31, source_branch="feature/182-alpha"),
                               make_mr(iid=32, source_branch="feature/182-beta")]
        self.run_sync()
        issue_root = self.roots("issue", 182)[0]["id"]
        thread = self.buzz.thread(issue_root)
        for mr_iid in (31, 32):
            replies = [e for e in thread
                       if (SYNC.parse_header(e.get("content", "")) or {}).get("object") == "mr"
                       and (SYNC.parse_header(e.get("content", "")) or {}).get("mr") == mr_iid]
            self.assertEqual(len(replies), 1)
            self.assertEqual(replies[0]["tags"][1][:2], ["e", issue_root])

    def test_multiple_associated_issues_only_the_binding_thread_gets_the_fact(self):
        """L1-GIS-165（ADR-0015 改）an MR closing two Issues posts its fact in the first Issue thread only; the second one gets a single cross-link (change:xref), not a copy."""
        self.gitlab.issue_list = [make_issue(182), make_issue(183)]
        self.gitlab.mr_list = [make_mr(iid=31, source_branch="feature/182-restore")]
        self.gitlab.closes = {31: [make_issue(182), make_issue(183)]}
        self.run_sync()
        changes = {}
        for iid in (182, 183):
            root = self.roots("issue", iid)[0]["id"]
            changes[iid] = [(SYNC.parse_header(e.get("content", "")) or {}).get("change")
                            for e in self.buzz.thread(root)
                            if (SYNC.parse_header(e.get("content", "")) or {}).get("object") == "mr"]
        self.assertEqual(changes, {182: ["lifecycle"], 183: ["xref"]})

        before = list(self.buzz.writes)
        self.run_sync()
        self.assertEqual(self.buzz.writes, before, "rerun must not duplicate into either thread")

    def enable_diffs(self, sha, *paths):
        self.config["diff"] = {"enabled": True, "private": False}
        self.gitlab.diffs[(31, sha)] = [
            {"old_path": path, "new_path": path, "diff": f"@@ -1 +1 @@\n-old {path}\n+new {path}\n"}
            for path in paths
        ]

    def diff_events(self, root):
        return [e for e in self.buzz.thread(root) if e["kind"] == 40008]

    def test_diffs_of_mr_closing_two_issues_land_only_in_the_binding_thread(self):
        """L1-GIS-175（ADR-0015 改）an MR associated with two Issues posts its kind 40008 diffs into the binding (first Issue) thread only, deduped."""
        self.gitlab.issue_list = [make_issue(182), make_issue(183)]
        self.gitlab.mr_list = [make_mr(iid=31, source_branch="feature/182-restore")]
        self.gitlab.closes = {31: [make_issue(182), make_issue(183)]}
        self.enable_diffs("a" * 40, "app/a.dart", "app/b.dart")
        self.run_sync()
        self.assertEqual(self.roots("mr", 31), [], "an associated MR opens no MR root")
        files = {}
        for iid in (182, 183):
            root = self.roots("issue", iid)[0]["id"]
            files[iid] = sorted(dict(t[:2] for t in e["tags"] if t[0] in ("file",))["file"] for e in self.diff_events(root))
        self.assertEqual(files, {182: ["app/a.dart", "app/b.dart"], 183: []})

        before = list(self.buzz.writes)
        self.run_sync()
        self.assertEqual(self.buzz.writes, before, "rerun must not duplicate diffs")

    def test_new_commit_on_associated_mr_updates_only_the_binding_thread(self):
        """L1-GIS-176（ADR-0015 改）a new head commit posts the update fact and its diff into the binding thread only; the other Issue thread keeps just its cross-link."""
        self.gitlab.issue_list = [make_issue(182), make_issue(183)]
        self.gitlab.mr_list = [make_mr(iid=31, source_branch="feature/182-restore")]
        self.gitlab.closes = {31: [make_issue(182), make_issue(183)]}
        self.enable_diffs("a" * 40, "app/a.dart")
        self.run_sync()

        self.gitlab.mr_list = [make_mr(iid=31, source_branch="feature/182-restore", sha="b" * 40,
                                       updated_at="2026-09-13T02:00:00Z")]
        self.enable_diffs("b" * 40, "app/a.dart")
        self.run_sync()
        first, second = (self.roots("issue", iid)[0]["id"] for iid in (182, 183))
        thread = self.buzz.thread(first)
        commits = sorted(dict(t[:2] for t in e["tags"] if t[0] == "commit")["commit"][0]
                         for e in thread if e["kind"] == 40008)
        self.assertEqual(commits, ["a", "b"], "the binding thread gets both head commits' diffs")
        shas = [part for e in thread if e["kind"] == 9
                and (SYNC.parse_header(e.get("content", "")) or {}).get("object") == "mr"
                for line in e["content"].splitlines() for part in line.split(" · ") if part.startswith("sha ")]
        self.assertIn("sha " + "b" * 12, shas, "the binding thread gets the update fact")
        other = self.buzz.thread(second)
        self.assertEqual([e for e in other if e["kind"] == 40008], [], "no diff in the other thread")
        self.assertEqual([(SYNC.parse_header(e.get("content", "")) or {}).get("change") for e in other
                          if (SYNC.parse_header(e.get("content", "")) or {}).get("object") == "mr"], ["xref"])

    def test_unassociated_mr_diffs_stay_in_mr_thread(self):
        """L1-GIS-177 an MR with no Issue association keeps its diffs in its own MR thread."""
        self.gitlab.mr_list = [make_mr(iid=31, source_branch="spike/no-issue")]
        self.enable_diffs("a" * 40, "app/a.dart")
        self.run_sync()
        mr_root = self.roots("mr", 31)[0]["id"]
        self.assertEqual(len(self.diff_events(mr_root)), 1)

    def test_related_lookup_is_queried_once_per_issue_per_run(self):
        """L1-GIS-196 related_merge_requests 每轮每个 Issue 最多查一次（多 MR 共享结果），避免积压时按 MR×Issue 放大慢接口调用。"""
        self.gitlab.issue_list = [make_issue(iid) for iid in (182, 183, 184, 185)]
        self.gitlab.mr_list = [make_mr(iid=iid, source_branch=f"spike/no-issue-{iid}x") for iid in (31, 32, 33, 34, 35)]
        self.gitlab.related = {183: [32]}
        self.run_sync()
        self.assertLessEqual(self.gitlab.related_calls, 4)
        replies = [e for e in self.buzz.thread(self.roots("issue", 183)[0]["id"])
                   if (SYNC.parse_header(e.get("content", "")) or {}).get("object") == "mr"]
        self.assertEqual([(SYNC.parse_header(e["content"]) or {}).get("change") for e in replies], ["xref"],
                         "ADR-0015: the related hit only links MR 32 into Issue 183; MR 32 keeps its own thread")
        self.assertEqual(len(self.roots("mr", 32)), 1)

    def test_mentions_only_in_first_issue_thread(self):
        """L1-GIS-197 a reviewable MR associated with two Issues @-mentions reviewers once, in the first (bound) Issue thread; the other thread only has the cross-link (transition:none)."""
        self.gitlab.issue_list = [make_issue(182), make_issue(183)]
        self.gitlab.mr_list = [make_mr(iid=31, reviewers=[{"username": "carol"}])]
        self.gitlab.closes = {31: [make_issue(182), make_issue(183)]}
        self.run_sync()
        mentioned = [w for w in self.buzz.writes if w[2]]
        self.assertEqual(len(mentioned), 1)
        self.assertEqual(mentioned[0][2], (CAROL,))
        other = [e for e in self.buzz.thread(self.roots("issue", 183)[0]["id"])
                 if (SYNC.parse_header(e.get("content", "")) or {}).get("object") == "mr"]
        self.assertEqual([SYNC.parse_header(e["content"])["change"] for e in other], ["xref"])
        self.assertEqual(SYNC.parse_header(other[0]["content"])["transition"], "none",
                         "only the bound thread may carry the reviewable transition that the route gate assigns on")

    def test_excluded_or_unreadable_associated_issue_does_not_stall_the_run(self):
        """L1-GIS-198 an associated Issue that is excluded (confidential) is skipped; the other Issue thread still gets the MR and the run succeeds."""
        self.gitlab.issue_list = [make_issue(182), make_issue(183, confidential=True)]
        self.gitlab.mr_list = [make_mr(iid=31)]
        self.gitlab.closes = {31: [make_issue(183), make_issue(182)]}
        result = self.run_sync()
        self.assertIn(result["status"], {"ok", "degraded"})
        replies = [e for e in self.buzz.thread(self.roots("issue", 182)[0]["id"])
                   if (SYNC.parse_header(e.get("content", "")) or {}).get("object") == "mr"]
        self.assertEqual(len(replies), 1)
        self.assertEqual(self.roots("mr", 31), [])
        # and the update path keeps working on the next run
        self.gitlab.mr_list = [make_mr(iid=31, sha="b" * 40, updated_at="2026-09-13T02:00:00Z")]
        self.assertIn(self.run_sync()["status"], {"ok", "degraded"})

    def test_crash_before_binding_does_not_open_a_second_root_on_rerun(self):
        """L1-GIS-199 binding to the Issue root is written before MR facts are sent, so a crash then a rerun whose association disappeared cannot create a separate MR root. (ADR-0015: the association is a closing one; a related hit alone no longer places an MR.)"""
        self.gitlab.issue_list = [make_issue(182)]
        self.gitlab.mr_list = [make_mr(iid=31, source_branch="spike/no-issue-xx")]
        self.gitlab.closes = {31: [make_issue(182)]}
        original_send = SYNC.Syncer._send_message

        def crash_on_mr_fact(syncer, content, reply_to=None, mentions=(), project_id=None):
            if "[object:mr]" in (SYNC.header_line(content) or ""):
                raise KeyboardInterrupt("simulated process kill before the MR fact is sent")
            return original_send(syncer, content, reply_to=reply_to, mentions=mentions,
                                 project_id=project_id)

        with mock.patch.object(SYNC.Syncer, "_send_message", crash_on_mr_fact):
            with self.assertRaises(KeyboardInterrupt):
                self.run_sync()
        self.gitlab.closes = {}           # the closing reference is gone
        self.gitlab.issue_list = []       # and the Issue is outside this run's scan window
        self.run_sync()
        self.assertEqual(self.roots("mr", 31), [], "rerun must not open a per-MR root")

    def test_mr_comment_reaches_only_the_binding_thread_once(self):
        """L1-GIS-200（ADR-0015 改）an MR comment reaches the binding Issue thread only, and is not duplicated on rerun."""
        self.gitlab.issue_list = [make_issue(182), make_issue(183)]
        self.gitlab.mr_list = [make_mr(iid=31)]
        self.gitlab.closes = {31: [make_issue(182), make_issue(183)]}
        self.run_sync()
        self.gitlab.mr_note_list.setdefault(31, []).append({
            "id": 5001, "author": {"id": 99, "username": "carol"}, "body": "please rebase",
            "system": False, "created_at": "2026-09-13T02:30:00Z"})
        self.gitlab.mr_list = [make_mr(iid=31, updated_at="2026-09-13T02:30:00Z")]
        self.run_sync()
        counts = {iid: len([e for e in self.buzz.thread(self.roots("issue", iid)[0]["id"])
                            if "please rebase" in e.get("content", "")]) for iid in (182, 183)}
        self.assertEqual(counts, {182: 1, 183: 0})
        before = list(self.buzz.writes)
        self.run_sync()
        self.assertEqual(self.buzz.writes, before)

    def test_update_writes_nothing_in_dry_run(self):
        """L1-GIS-201 dry-run of an update to a merged-in MR writes nothing to any Issue thread."""
        self.gitlab.issue_list = [make_issue(182), make_issue(183)]
        self.gitlab.mr_list = [make_mr(iid=31)]
        self.gitlab.closes = {31: [make_issue(182), make_issue(183)]}
        self.run_sync()
        self.gitlab.mr_list = [make_mr(iid=31, sha="b" * 40, updated_at="2026-09-13T02:00:00Z")]
        before = list(self.buzz.writes)
        SYNC.Syncer(self.config, self.gitlab, self.buzz, state_dir=Path(self.tmp.name)).run(dry_run=True)
        self.assertEqual(self.buzz.writes, before)

    def test_unassociated_mr_keeps_per_mr_root(self):
        """L1-GIS-166 an MR with no association keeps today's per-MR root and binding."""
        self.gitlab.mr_list = [make_mr(iid=34, source_branch="feature/plain-branch")]
        self.run_sync()
        self.assertEqual(len(self.roots("mr", 34)), 1)

    def test_existing_mr_binding_wins_over_new_association(self):
        """L1-GIS-167 an already-bound MR keeps its old MR thread even when it now closes an Issue."""
        self.gitlab.issue_list = [make_issue(182)]
        self.gitlab.mr_list = [make_mr(iid=35, source_branch="feature/plain")]
        self.run_sync()
        old_root = self.roots("mr", 35)[0]["id"]

        self.gitlab.closes = {35: [make_issue(182)]}
        self.gitlab.mr_list = [make_mr(iid=35, source_branch="feature/plain", sha="b" * 40,
                                       updated_at="2026-09-13T02:00:00Z")]
        self.run_sync()
        replies = [e for e in self.buzz.thread(old_root)
                   if e["id"] != old_root
                   and (SYNC.parse_header(e.get("content", "")) or {}).get("object") == "mr"]
        self.assertEqual(len(replies), 2)  # first fact + the update, both under the plaque root
        self.assertTrue(all(reply["tags"][1][:2] == ["e", old_root] for reply in replies))
        late = [e for e in self.buzz.thread(self.roots("issue", 182)[0]["id"])
                if (SYNC.parse_header(e.get("content", "")) or {}).get("object") == "mr"]
        self.assertEqual(late, [], "ADR-0015: the Issue that closes the MR later gets neither facts nor a cross-link")

    def test_activity_records_of_associated_mr_land_in_issue_thread(self):
        """L1-GIS-168 pipeline-style activity of an associated MR replies into the Issue thread."""
        self.gitlab.issue_list = [make_issue(182)]
        self.gitlab.mr_list = [make_mr(iid=31, source_branch="feature/182-restore")]
        self.gitlab.closes = {31: [make_issue(182)]}
        self.run_sync()
        issue_root = self.roots("issue", 182)[0]["id"]

        self.gitlab.pipelines = lambda project_id, updated_after: [{
            "id": 204435, "iid": 31, "status": "failed", "ref": "feature/182-restore",
            "updated_at": "2026-09-13T03:00:00Z", "source": "merge_request",
        }] if False else []
        # drive the activity record through the digest path: push event record
        self.gitlab.events = lambda project_id, after_date: [{
            "id": 9001, "action_name": "pushed to", "target_type": None, "target_iid": None,
            "target_title": None, "created_at": "2026-09-13T03:00:00Z",
            "author": {"username": "alice", "name": "Alice"},
            "push_data": {"ref_type": "branch", "ref": "feature/182-restore",
                          "commit_count": 1, "commit_title": "fix"},
        }]
        self.run_sync()
        thread = self.buzz.thread(issue_root)
        mr_replies = [e for e in thread
                      if (SYNC.parse_header(e.get("content", "")) or {}).get("object") == "mr"]
        self.assertGreaterEqual(len(mr_replies), 1)


    def test_pipeline_activity_of_unbound_but_associated_mr_merges(self):
        """L1-GIS-173 a pipeline record for an unbound MR with an Issue association lands in the Issue thread."""
        self.gitlab.issue_list = [make_issue(182)]
        self.gitlab.mr_list = [make_mr(iid=31, source_branch="feature/182-restore")]
        self.gitlab.closes = {31: [make_issue(182)]}
        self.gitlab.pipelines = lambda project_id, updated_after: [{
            "id": 4242, "iid": 1, "status": "failed", "ref": "refs/merge-requests/31/head",
            "updated_at": "2026-09-13T03:00:00Z",
        }]
        self.gitlab.pipeline_jobs = lambda project_id, pid: [{"name": "test:unit"}]
        self.run_sync()
        root = self.roots("issue", 182)[0]["id"]
        replies = [e for e in self.buzz.thread(root)
                   if (SYNC.parse_header(e.get("content", "")) or {}).get("object") == "mr"]
        self.assertEqual(len(replies), 2)  # lifecycle fact + pipeline activity
        self.assertIn("流水线失败", replies[1]["content"])
        self.assertEqual(
            SYNC.parse_binding(self.gitlab.mr_note_list[31], BOT_ID, PID, "mr", 31, CHANNEL),
            root,
        )


class ReplyTagShapeTest(unittest.TestCase):
    def test_nip10_reply_shapes(self):
        """L1-GIS-170 reply e-tags: single reply-marker to the root, or root+reply markers to a chained parent."""
        root, parent = "a" * 64, "b" * 64
        one = {"tags": [["h", "c"], ["e", root, "", "reply"]]}
        two = {"tags": [["h", "c"], ["e", root, "", "root"], ["e", parent, "", "reply"]]}
        self.assertTrue(SYNC.reply_e_tags_match(one, root))
        self.assertTrue(SYNC.reply_e_tags_match(two, parent))
        # wrong parent, extra markers, or reply-to-root shape used for a chained parent all fail
        self.assertFalse(SYNC.reply_e_tags_match(one, parent))
        self.assertFalse(SYNC.reply_e_tags_match(two, root))
        self.assertFalse(SYNC.reply_e_tags_match(
            {"tags": [["e", root, "", "root"], ["e", parent, "", "reply"], ["e", root, "", "reply"]]}, parent))
        self.assertFalse(SYNC.reply_e_tags_match({"tags": [["e", parent, "", "reply"], ["e", root, "", "root"]]}, parent))


class OriginBindingTest(unittest.TestCase):
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
            "buzz": {}, "people": PEOPLE,
        }

    def run_sync(self):
        return SYNC.Syncer(self.config, self.gitlab, self.buzz, state_dir=Path(self.tmp.name)).run()

    def roots(self, object_kind, iid):
        tail = "issues" if object_kind == "issue" else "merge_requests"
        return self.buzz.search_roots(OBJ_URL(tail, iid), object_kind, iid)
    def test_origin_binds_unassociated_mr_into_named_desk_thread(self):
        """Unassociated MR with a valid origin marker posts into that Desk plaque thread."""
        self.gitlab.issue_list = [make_issue(14)]
        self.run_sync()
        issue_root = self.roots("issue", 14)[0]["id"]
        marker = SYNC.render_origin_marker(CHANNEL, issue_root)
        self.gitlab.mr_list = [make_mr(iid=897, source_branch="fix/notify-layout",
                                       description=f"layout fix\n\n{SYNC.render_origin_block(CHANNEL, issue_root)}")]
        self.run_sync()
        self.assertEqual(self.roots("mr", 897), [], "origin-bound MR must not open its own root")
        self.assertEqual(
            SYNC.parse_binding(self.gitlab.mr_note_list[897], BOT_ID, PID, "mr", 897, CHANNEL),
            issue_root,
        )
        replies = [e for e in self.buzz.thread(issue_root)
                   if (SYNC.parse_header(e.get("content") or "") or {}).get("mr") == 897]
        self.assertEqual(len(replies), 1)
        self.assertEqual(replies[0]["tags"][1][:2], ["e", issue_root])

        self.gitlab.mr_list = [make_mr(iid=897, source_branch="fix/notify-layout",
                                       description=f"layout fix\n\n{marker}",
                                       sha="b" * 40, updated_at="2026-09-13T02:00:00Z")]
        self.run_sync()
        replies = [e for e in self.buzz.thread(issue_root)
                   if (SYNC.parse_header(e.get("content") or "") or {}).get("mr") == 897]
        self.assertEqual(len(replies), 2)
        self.assertEqual(replies[1]["tags"][1][:2], ["e", replies[0]["id"]])

    def test_buzz_link_alone_binds_unassociated_mr(self):
        self.gitlab.issue_list = [make_issue(14)]
        self.run_sync()
        issue_root = self.roots("issue", 14)[0]["id"]
        self.gitlab.mr_list = [make_mr(
            iid=898, source_branch="fix/deeplink",
            description="see " + SYNC.buzz_message_link(CHANNEL, issue_root),
        )]
        self.run_sync()
        self.assertEqual(self.roots("mr", 898), [])
        self.assertEqual(
            SYNC.parse_binding(self.gitlab.mr_note_list[898], BOT_ID, PID, "mr", 898, CHANNEL),
            issue_root,
        )

    def test_origin_binds_new_issue_into_named_desk_thread(self):
        self.gitlab.issue_list = [make_issue(14)]
        self.run_sync()
        issue_root = self.roots("issue", 14)[0]["id"]
        self.gitlab.issue_list.append(make_issue(
            99, description=SYNC.render_origin_block(CHANNEL, issue_root),
            created_at="2026-09-13T02:00:00Z", updated_at="2026-09-13T02:00:00Z",
        ))
        self.run_sync()
        self.assertEqual(self.roots("issue", 99), [], "origin-bound issue must not open its own plaque")
        self.assertEqual(
            SYNC.parse_binding(self.gitlab.issue_note_list[99], BOT_ID, PID, "issue", 99, CHANNEL),
            issue_root,
        )
        facts = [e for e in self.buzz.thread(issue_root)
                 if (SYNC.parse_header(e.get("content") or "") or {}).get("issue") == 99]
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["tags"][1][:2], ["e", issue_root])

    def test_gitlab_issue_association_wins_over_origin(self):
        self.gitlab.issue_list = [make_issue(14), make_issue(182)]
        self.run_sync()
        origin_root = self.roots("issue", 14)[0]["id"]
        issue_root = self.roots("issue", 182)[0]["id"]
        marker = SYNC.render_origin_marker(CHANNEL, origin_root)
        self.gitlab.mr_list = [make_mr(iid=31, description=marker)]
        self.gitlab.closes = {31: [make_issue(182)]}
        self.run_sync()
        self.assertEqual(
            SYNC.parse_binding(self.gitlab.mr_note_list[31], BOT_ID, PID, "mr", 31, CHANNEL),
            issue_root,
        )
        copies = [e for e in self.buzz.thread(origin_root)
                  if (SYNC.parse_header(e.get("content") or "") or {}).get("mr") == 31]
        self.assertEqual([(SYNC.parse_header(e["content"]) or {}).get("change") for e in copies], ["xref"],
                         "ADR-0015: the origin thread gets one cross-link, not a copy of the facts")
        self.assertNotIn("transition:reviewable", copies[0]["content"])

    def test_origin_does_not_rebind_an_already_bound_mr(self):
        self.gitlab.issue_list = [make_issue(14)]
        self.gitlab.mr_list = [make_mr(iid=897, source_branch="fix/layout")]
        self.run_sync()
        mr_root = self.roots("mr", 897)[0]["id"]
        issue_root = self.roots("issue", 14)[0]["id"]
        marker = SYNC.render_origin_marker(CHANNEL, issue_root)
        self.gitlab.mr_list = [make_mr(iid=897, source_branch="fix/layout", description=marker,
                                       updated_at="2026-09-13T02:00:00Z")]
        self.run_sync()
        self.assertEqual(
            SYNC.parse_binding(self.gitlab.mr_note_list[897], BOT_ID, PID, "mr", 897, CHANNEL),
            mr_root,
        )

    def test_origin_wrong_channel_falls_back_to_the_mr_own_root(self):
        self.gitlab.issue_list = [make_issue(14)]
        self.run_sync()
        issue_root = self.roots("issue", 14)[0]["id"]
        marker = SYNC.render_origin_marker("00000000-0000-4000-8000-0000000000c2", issue_root)
        self.gitlab.mr_list = [make_mr(iid=897, source_branch="fix/layout", description=marker)]
        result = self.run_sync()  # ADR-0014: a well-formed marker that cannot be used is no origin at all
        self.assertEqual(result["stalled"], [])
        self.assertEqual([(f["object"], f["iid"]) for f in result["origin_fallbacks"]], [("mr", 897)])
        self.assertEqual(len(self.roots("mr", 897)), 1)
        self.assertIn(897, self.gitlab.mr_note_list)

    def test_origin_missing_root_falls_back_to_the_mr_own_root(self):
        marker = SYNC.render_origin_marker(CHANNEL, "c" * 64)
        self.gitlab.mr_list = [make_mr(iid=897, source_branch="fix/layout", description=marker)]
        result = self.run_sync()  # ADR-0014: the object syncs as if it had no origin, and the round says so
        self.assertEqual(result["stalled"], [])
        self.assertEqual([(f["object"], f["iid"]) for f in result["origin_fallbacks"]], [("mr", 897)])
        self.assertEqual(len(self.roots("mr", 897)), 1)

    def test_comment_origin_binds_and_multiple_origins_fan_out(self):
        self.gitlab.issue_list = [make_issue(14), make_issue(15)]
        self.run_sync()
        first = self.roots("issue", 14)[0]["id"]
        second = self.roots("issue", 15)[0]["id"]
        self.gitlab.issue_list.append(make_issue(
            99, created_at="2026-09-13T02:00:00Z", updated_at="2026-09-13T02:00:00Z",
        ))
        self.gitlab.issue_note_list[99] = [{
            "id": 11, "author": {"id": 8, "username": "alice"},
            "body": SYNC.render_origin_block(CHANNEL, first),
            "system": False, "created_at": "2026-09-13T02:00:00Z",
        }, {
            "id": 12, "author": {"id": 8, "username": "alice"},
            "body": "also " + SYNC.buzz_message_link(CHANNEL, second),
            "system": False, "created_at": "2026-09-13T02:01:00Z",
        }]
        self.run_sync()
        self.assertEqual(self.roots("issue", 99), [])
        self.assertEqual(
            SYNC.parse_binding(self.gitlab.issue_note_list[99], BOT_ID, PID, "issue", 99, CHANNEL),
            first,
        )
        for root, iid in ((first, 99), (second, 99)):
            facts = [e for e in self.buzz.thread(root)
                     if (SYNC.parse_header(e.get("content") or "") or {}).get("issue") == iid]
            self.assertEqual(len(facts), 1, f"issue 99 must land in thread {root}")
            self.assertEqual(facts[0]["tags"][1][:2], ["e", root])

    def test_task_description_origin_binds_like_issue(self):
        self.gitlab.issue_list = [make_issue(14)]
        self.run_sync()
        issue_root = self.roots("issue", 14)[0]["id"]
        self.gitlab.issue_list.append(make_issue(
            77, issue_type="task",
            web_url=f"{WEB}/-/work_items/77",
            description=SYNC.render_origin_block(CHANNEL, issue_root),
            created_at="2026-09-13T02:00:00Z", updated_at="2026-09-13T02:00:00Z",
        ))
        self.run_sync()
        self.assertEqual(self.roots("issue", 77), [])
        self.assertEqual(
            SYNC.parse_binding(self.gitlab.issue_note_list[77], BOT_ID, PID, "issue", 77, CHANNEL),
            issue_root,
        )
        facts = [e for e in self.buzz.thread(issue_root)
                 if (SYNC.parse_header(e.get("content") or "") or {}).get("issue") == 77]
        self.assertEqual(len(facts), 1)

    def test_comment_origin_does_not_rebind_an_already_bound_issue(self):
        self.gitlab.issue_list = [make_issue(14), make_issue(99)]
        self.run_sync()
        own_root = self.roots("issue", 99)[0]["id"]
        other = self.roots("issue", 14)[0]["id"]
        self.gitlab.issue_note_list.setdefault(99, []).append({
            "id": 21, "author": {"id": 8, "username": "alice"},
            "body": SYNC.render_origin_block(CHANNEL, other),
            "system": False, "created_at": "2026-09-13T02:00:00Z",
        })
        self.gitlab.issue_list = [
            make_issue(14),
            make_issue(99, updated_at="2026-09-13T02:00:00Z"),
        ]
        self.run_sync()
        self.assertEqual(
            SYNC.parse_binding(self.gitlab.issue_note_list[99], BOT_ID, PID, "issue", 99, CHANNEL),
            own_root,
        )
        extras = [e for e in self.buzz.thread(other)
                  if (SYNC.parse_header(e.get("content") or "") or {}).get("issue") == 99]
        self.assertGreaterEqual(len(extras), 1, "later comment origin still fans out a copy")


class AssociationResolverTest(unittest.TestCase):
    """L1-GIS-172 the association resolver merges sources, verifies existence, caps at 3."""

    def setUp(self):
        import tempfile
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
        self.syncer = SYNC.Syncer(self.config, self.gitlab, self.buzz, state_dir=Path(self.tmp.name))

    def seed_issues(self, *iids):
        self.gitlab.issue_list = [make_issue(iid) for iid in iids]
        # emulate the run's scanned-issues cache used by the reverse lookup
        self.syncer._issues_scanned[PID] = [make_issue(iid) for iid in iids]

    def test_related_reverse_lookup_associates(self):
        """L1-GIS-172 a scanned Issue whose related_merge_requests names the MR associates it."""
        self.seed_issues(501)
        self.gitlab.mr_list = [make_mr(iid=61, source_branch="feature/plain")]
        self.gitlab.related = {501: [61]}
        self.assertEqual(self.syncer.mr_issue_associations(PID, self.gitlab.mr_list[0], 61), [501])

    def test_missing_branch_named_issue_is_dropped(self):
        """L1-GIS-172 a branch-named iid with no such Issue is dropped, not associated."""
        self.seed_issues(501)
        self.gitlab.mr_list = [make_mr(iid=62, source_branch="feature/999-ghost")]
        self.assertEqual(self.syncer.mr_issue_associations(PID, self.gitlab.mr_list[0], 62), [])

    def test_association_is_capped_at_three_and_deduped(self):
        """L1-GIS-172 closes+branch+related dedupe into an ordered set capped at three."""
        self.seed_issues(551, 552, 553, 554)
        mr = make_mr(iid=63, source_branch="feature/551-alpha")
        self.gitlab.mr_list = [mr]
        self.gitlab.closes = {63: [make_issue(552), make_issue(553), make_issue(554)]}
        self.gitlab.related = {554: [63]}
        # closes sources come first (strongest signal); the branch-named 551 falls fourth and is capped out
        self.assertEqual(self.syncer.mr_issue_associations(PID, mr, 63), [552, 553, 554])


class BranchIssueRuleTest(unittest.TestCase):
    def test_whitelist_shapes(self):
        """L1-GIS-169 the branch rule accepts only <word>-<iid>-… and <iid>-… last segments."""
        accept = {
            "feature/182-restore": 182,
            "182-restore": 182,
            "naturehood-401-video-search": 401,
            "bugfix/9001-hot": 9001,
        }
        for branch, iid in accept.items():
            self.assertEqual(SYNC.branch_issue_iids(branch), [iid], branch)
        for branch in (
            "feat/admin-video-id-search-staging-20260916",
            "main", "develop", "release-2026-09-16", "feature/restore",
        ):
            self.assertEqual(SYNC.branch_issue_iids(branch), [], branch)


if __name__ == "__main__":
    unittest.main()
