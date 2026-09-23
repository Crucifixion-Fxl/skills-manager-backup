import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
SCRIPT = SKILL / "scripts" / "gitlab_buzz_sync.py"


def load_module():
    spec = importlib.util.spec_from_file_location("gitlab_buzz_sync_mr_activity", SCRIPT)
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
REPO = WEB + ".git"
SINCE = "2026-09-13T00:00:00Z"
SERVER_TIME = "2026-09-13T04:00:00Z"
SHA_A, SHA_B = "a" * 40, "b" * 40


def make_mr(iid=31, **overrides):
    base = {
        "iid": iid, "project_id": PID, "title": "恢复最近工作区", "state": "opened", "draft": False,
        "sha": SHA_A, "source_branch": "feature/restore-workspace", "target_branch": "main", "labels": [],
        "reviewers": [], "author": {"username": "dave"}, "web_url": f"{WEB}/-/merge_requests/{iid}",
        "created_at": "2026-09-13T01:00:00Z", "updated_at": "2026-09-13T01:00:00Z",
    }
    base.update(overrides)
    return base


def make_issue(iid=182):
    return {
        "iid": iid, "project_id": PID, "title": f"Issue {iid}", "description": "", "state": "opened",
        "labels": ["type::feature", "status::ready"], "milestone": None, "confidential": False, "assignees": [],
        "web_url": f"{WEB}/-/issues/{iid}", "created_at": "2026-09-13T01:00:00Z",
        "updated_at": "2026-09-13T01:00:00Z",
    }


def pipeline(pipeline_id, status, ref):
    return {"id": pipeline_id, "iid": pipeline_id, "status": status, "ref": ref, "sha": SHA_A, "source": "push",
            "web_url": f"{WEB}/-/pipelines/{pipeline_id}", "updated_at": "2026-09-13T02:00:00.000Z"}


def file_diff(path, body="@@ -1 +1 @@\n-x\n+y\n"):
    return {"old_path": path, "new_path": path, "diff": body}


class MrActivityRenderTest(unittest.TestCase):
    def test_render_mr_activity(self):
        """L1-GIS-031 MR 流水线失败与批准渲染成 MR Thread activity，附失败 job 与 events 行。"""
        fact = SYNC.mr_fact(make_mr(), PID)
        record = SYNC.record_from_pipeline(pipeline(54, "failed", "refs/merge-requests/31/head"), PID, "main")
        rendered = SYNC.render_mr_activity(fact, record, jobs=["test:unit", "@lint"])
        lines = rendered.split("\n")
        header = SYNC.parse_header(rendered)
        self.assertEqual((header["object"], header["change"], header["mr"]), ("mr", "activity", 31))
        self.assertNotIn("event: pipeline failed", lines)
        self.assertIn("jobs: test:unit,＠lint", lines)
        self.assertEqual(header.get("events"), ["pipeline-54-failed"])
        self.assertNotIn("events: pipeline-54-failed", lines)
        self.assertTrue(lines[-1].startswith("[gitlab-notify:v1][object:mr]"))
        self.assertIn("[events:pipeline-54-failed]", lines[-1])
        self.assertTrue(rendered.startswith("❌ **流水线失败**"))

        approved = SYNC.record_from_event(
            {"id": 15, "action_name": "approved", "target_type": "MergeRequest", "target_iid": 31,
             "target_title": "恢复最近工作区", "created_at": "2026-09-13T02:00:00.000Z",
             "author": {"username": "alice"}}, PID, WEB)
        lines = SYNC.render_mr_activity(fact, approved).split("\n")
        self.assertNotIn("event: mr approved", lines)
        self.assertIn("by: alice", lines)
        self.assertEqual(SYNC.parse_header("\n".join(lines)).get("events"), ["event-15"])
        self.assertNotIn("events: event-15", lines)
        self.assertTrue(lines[0].startswith("✔ **已批准**"))

    def test_unified_diff_from_files(self):
        """L1-GIS-014 MR diffs 接口的逐文件 diff 拼成 unified diff，再按文件切分。"""
        text = SYNC.unified_diff_from_files([
            file_diff("src/a.ts"),
            {"old_path": "old.py", "new_path": "new.py", "diff": "@@ -1 +1 @@\n-1\n+2"},
        ])
        chunks, skipped = SYNC.split_diff_by_file(text)
        self.assertEqual([chunk["file"] for chunk in chunks], ["src/a.ts", "new.py"])
        self.assertTrue(chunks[0]["diff"].startswith("diff --git a/src/a.ts b/src/a.ts\n--- a/src/a.ts\n+++ b/src/a.ts\n"))
        self.assertTrue(chunks[1]["diff"].endswith("+2\n"))
        self.assertEqual(skipped, [])

    def test_build_diff_args(self):
        """L1-GIS-014 send-diff 参数；非法 commit 与 repo 拒绝。"""
        root = "f" * 64
        args = SYNC.build_diff_args("/x/buzz-0.5.23/buzz", CHANNEL, repo=REPO, commit=SHA_A, file_path="src/a.ts",
                                    reply_to=root, source_branch="feature/182-restore", target_branch="main", pr=31)
        self.assertEqual(args, [
            "/x/buzz-0.5.23/buzz", "messages", "send-diff", "--channel", CHANNEL, "--diff", "-",
            "--repo", REPO, "--commit", SHA_A, "--file=src/a.ts", "--reply-to", root,
            "--source-branch=feature/182-restore", "--target-branch=main", "--pr", "31",
        ])
        with self.assertRaises(SYNC.SyncError):
            SYNC.build_diff_args("/x/buzz", CHANNEL, repo=REPO, commit="zz", file_path="a", reply_to=root,
                                 source_branch="a", target_branch="b", pr=31)
        with self.assertRaises(SYNC.SyncError):
            SYNC.build_diff_args("/x/buzz", CHANNEL, repo="ftp://x", commit=SHA_A, file_path="a", reply_to=root,
                                 source_branch="a", target_branch="b", pr=31)


class FakeGitLab:
    def __init__(self):
        self.user = {"id": BOT_ID, "username": BOT}
        self.visibility = "private"
        self.issue_list = []
        self.issue_notes = {}
        self.mr_list = []
        self.mr_note_list = {}
        self.closes = {}
        self.diffs = {}
        self.pipeline_list = []
        self.jobs = {}
        self.event_list = []

    def current_user(self):
        return self.user

    def scan_time(self):
        return SERVER_TIME

    def project(self, project_id):
        return {"id": project_id, "visibility": self.visibility, "web_url": WEB, "http_url_to_repo": REPO,
                "default_branch": "main"}

    def issues(self, project_id, updated_after):
        return [dict(issue) for issue in self.issue_list]

    def issue(self, project_id, iid):
        return dict(next(issue for issue in self.issue_list if issue["iid"] == iid))

    def notes(self, project_id, iid):
        return list(self.issue_notes.get(iid, []))

    def add_note(self, project_id, iid, body):
        note = {"id": 3000 + len(self.issue_notes), "author": {"id": BOT_ID, "username": BOT}, "body": body,
                "system": False, "created_at": SERVER_TIME}
        self.issue_notes.setdefault(iid, []).append(note)
        return note["id"]

    def merge_requests(self, project_id, updated_after):
        return [dict(mr) for mr in self.mr_list]

    def merge_request(self, project_id, iid):
        try:
            return dict(next(mr for mr in self.mr_list if mr["iid"] == iid))
        except StopIteration:
            raise SYNC.GitLabHTTPError("GET", f"projects/{project_id}/merge_requests/{iid}", 404) from None

    def members(self, project_id):
        return []

    def mr_notes(self, project_id, iid):
        return list(self.mr_note_list.get(iid, []))

    def add_mr_note(self, project_id, iid, body):
        note = {"id": 4000 + len(self.mr_note_list), "author": {"id": BOT_ID, "username": BOT}, "body": body,
                "system": False, "created_at": SERVER_TIME}
        self.mr_note_list.setdefault(iid, []).append(note)
        return note["id"]

    def related_merge_requests(self, project_id, issue_iid):
        return []

    def mr_closes_issues(self, project_id, iid):
        return [{"iid": value} for value in self.closes.get(iid, [])]

    def mr_diffs(self, project_id, iid):
        mr = next(mr for mr in self.mr_list if mr["iid"] == iid)
        return list(self.diffs.get((iid, mr["sha"]), []))

    def events(self, project_id, after_date):
        return list(self.event_list)

    def pipelines(self, project_id, updated_after):
        return list(self.pipeline_list)

    def pipeline_jobs(self, project_id, pipeline_id):
        return list(self.jobs.get(pipeline_id, []))

    def deployments(self, project_id, updated_after):
        return []

    def releases(self, project_id):
        return []

    def feature_flags(self, project_id):
        return []

    def access_tokens(self, project_id):
        return []


class FakeBuzz:
    def __init__(self):
        self.events = []
        self.writes = []
        self.diffs = []

    def _add(self, kind, content, reply_to, extra_tags=()):
        event_id = hashlib.sha256(f"{len(self.events)}:{kind}:{content}".encode()).hexdigest()
        tags = [["h", CHANNEL]]
        if reply_to:
            tags.append(["e", reply_to, "", "reply"])
        tags.extend(list(tag) for tag in extra_tags)
        self.events.append({"id": event_id, "pubkey": DESK, "kind": kind, "created_at": 1000 + len(self.events),
                            "tags": tags, "content": content})
        return event_id

    def send(self, content, reply_to=None, mentions=()):
        self.writes.append((reply_to, content, tuple(mentions)))
        return self._add(9, content, reply_to, [("p", pubkey) for pubkey in mentions])

    def send_diff(self, diff, *, repo, commit, file_path, reply_to, source_branch, target_branch, pr):
        self.diffs.append((reply_to, commit, file_path, repo, pr))
        return self._add(40008, diff, reply_to, [("repo", repo), ("commit", commit), ("file", file_path)])

    def thread(self, root_event_id):
        return [e for e in self.events if e["id"] == root_event_id or ["e", root_event_id, "", "reply"] in e["tags"]]

    def search_roots(self, query, object_kind, iid, since_unix=None):
        # issue #78: recovery searches by the canonical object URL (plaque or legacy root)
        return [e for e in self.events
                if query in str(e.get("content", ""))
                and not any(tag[0] == "e" for tag in e.get("tags") or [])]

    def channel_messages(self, since_unix):
        return [e for e in self.events if e["kind"] == 9]

    def channel_members(self):
        return {DESK: "member"}


class MrActivitySyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.gitlab = FakeGitLab()
        self.buzz = FakeBuzz()
        self.config = {
            "channel_id": CHANNEL, "publisher_pubkey": DESK, "since": SINCE, "include_confidential": False,
            "audience": {"allowed_pubkeys": [DESK]},
            "exclude": [], "diff": {"enabled": False, "private": False},
            "gitlab": {"base_url": "http://127.0.0.1:8929", "token_env": "NH_DESK_GITLAB_TOKEN",
                       "bot_user_id": BOT_ID, "bot_username": BOT, "projects": [PID]},
            "buzz": {}, "people": {},
        }

    def tearDown(self):
        self.tmp.cleanup()

    def run_sync(self):
        return SYNC.Syncer(self.config, self.gitlab, self.buzz, state_dir=Path(self.tmp.name)).run()

    def mr_root(self, iid=31):
        url = f"{WEB}/-/merge_requests/{iid}"
        return next(e["id"] for e in self.buzz.events if url in e["content"] and e["kind"] == 9
                    and not any(tag[0] == "e" for tag in e["tags"]))

    def test_pipeline_and_approval_into_mr_thread(self):
        """L1-GIS-031 MR 流水线终态（附失败 job）与批准回到 MR Thread；未绑定 MR 不发；重跑不重复。"""
        self.gitlab.mr_list = [make_mr()]
        self.run_sync()
        root = self.mr_root()
        self.gitlab.pipeline_list = [pipeline(54, "failed", "refs/merge-requests/31/head"),
                                     pipeline(55, "running", "refs/merge-requests/31/head"),
                                     pipeline(56, "failed", "refs/merge-requests/99/head")]
        self.gitlab.jobs = {54: [{"name": "test:unit", "status": "failed"}]}
        self.gitlab.event_list = [{"id": 15, "action_name": "approved", "target_type": "MergeRequest",
                                   "target_iid": 31, "target_title": "恢复最近工作区", "author": {"username": "alice"},
                                   "created_at": "2026-09-13T04:00:30.000Z"}]
        before = len(self.buzz.writes)
        summary = self.run_sync()
        new = self.buzz.writes[before:]
        self.assertEqual(len(new), 2)
        self.assertTrue(all(reply_to == root for reply_to, _, _ in new))
        contents = "\n".join(content for _, content, _ in new)
        self.assertIn("[events:pipeline-54-failed]", contents)
        self.assertIn("jobs: test:unit", contents)
        self.assertIn("[events:event-15]", contents)
        self.assertGreaterEqual(summary["activity"], 2)

        count = len(self.buzz.writes)
        self.run_sync()
        self.assertEqual(len(self.buzz.writes), count)

    def test_diffs_into_mr_thread(self):
        """L1-GIS-014 / US-GIS-08 开启 diff 时按文件发进 MR Thread；同 sha+文件不重发；新 sha 再发；未开 private 不发。"""
        self.config["diff"] = {"enabled": True, "private": True}
        self.gitlab.mr_list = [make_mr()]
        self.gitlab.diffs = {(31, SHA_A): [file_diff("src/a.ts"), file_diff("src/b.ts")],
                             (31, SHA_B): [file_diff("src/a.ts", "@@ -1 +1 @@\n-y\n+z\n")]}
        self.run_sync()
        root = self.mr_root()
        self.assertEqual([(r, c, f) for r, c, f, _, _ in self.buzz.diffs],
                         [(root, SHA_A, "src/a.ts"), (root, SHA_A, "src/b.ts")])
        self.assertTrue(all(repo == REPO and pr == 31 for _, _, _, repo, pr in self.buzz.diffs))
        self.run_sync()
        self.assertEqual(len(self.buzz.diffs), 2)

        self.gitlab.mr_list = [make_mr(sha=SHA_B, updated_at="2026-09-13T02:00:00Z")]
        self.run_sync()
        self.assertEqual(self.buzz.diffs[-1][:3], (root, SHA_B, "src/a.ts"))
        self.assertEqual(len(self.buzz.diffs), 3)

        other = MrActivitySyncTest("test_diffs_into_mr_thread")
        other.setUp()
        try:
            other.gitlab.mr_list = [make_mr()]
            other.gitlab.diffs = {(31, SHA_A): [file_diff("src/a.ts")]}
            other.config["diff"] = {"enabled": True, "private": False}
            other.run_sync()
            self.assertEqual(other.buzz.diffs, [])
        finally:
            other.tearDown()

    def test_mr_facts_merge_into_linked_issue_thread(self):
        """US-GIS-09（2026-09-17 归并修订）closes_issues 关联的 MR 事实进入该 Issue thread，正文带 Issue 链接。"""
        self.gitlab.issue_list = [make_issue(182)]
        self.gitlab.mr_list = [make_mr()]
        self.gitlab.closes = {31: [182]}
        self.run_sync()
        issue_root = next(e["id"] for e in self.buzz.events if f"{WEB}/-/issues/182" in e["content"])
        mr_facts = [e for e in self.buzz.events
                    if "[mr:31]" in e["content"]
                    and any(tag[:2] == ["e", issue_root] for tag in e["tags"])]
        self.assertEqual(len(mr_facts), 1)
        self.assertIn(f"issues: buzz://message?channel={CHANNEL}&id={issue_root}&thread={issue_root}",
                      mr_facts[0]["content"].split("\n"))


if __name__ == "__main__":
    unittest.main()
