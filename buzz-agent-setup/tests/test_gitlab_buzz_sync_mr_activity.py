import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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
        self.assertTrue(rendered.startswith("❌ **#54 流水线失败**"))

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

    def test_compact_card_revision_orders_same_second_edits_without_hash_order(self):
        """L2-1-GIS-141 同秒 edit 由单调 rev 决定因果顺序，不拿内容哈希冒充发布时间。"""
        original_id = "a" * 64
        base = SYNC.render_mr_message(SYNC.mr_fact(make_mr(draft=True), PID), "lifecycle", [], first=True)
        first = SYNC.render_compact_status(
            {"content": base, "created_at": 100},
            SYNC.render_mr_message(SYNC.mr_fact(make_mr(), PID), "lifecycle", [], first=False),
            101,
        )
        second = SYNC.render_compact_status(
            {"content": first, "created_at": 101},
            SYNC.render_mr_message(
                SYNC.mr_fact(make_mr(title="later"), PID), "update", [], first=False,
            ),
            101,
        )
        events = [
            {"id": original_id, "pubkey": DESK, "kind": 9, "created_at": 100,
             "tags": [["h", CHANNEL]], "content": base},
            {"id": "f" * 64, "pubkey": DESK, "kind": 40003, "created_at": 101,
             "tags": [["h", CHANNEL], ["e", original_id]], "content": first},
            {"id": "0" * 64, "pubkey": DESK, "kind": 40003, "created_at": 101,
             "tags": [["h", CHANNEL], ["e", original_id]], "content": second},
        ]

        target, effective = SYNC.status_card_from_thread(events, DESK, PID, "mr", 31)

        self.assertEqual(target, original_id)
        self.assertEqual(effective["id"], "0" * 64)
        self.assertEqual(SYNC.parse_header(effective["content"])["rev"], 2)

    def test_compact_card_prefers_current_overlay_over_same_revision_history_overlay(self):
        """R4：旧发布器同 rev 时，route:skip 历史层不能覆盖可路由的合并终态。"""
        original_id = "a" * 64
        base = (
            "👀 **可评审**\n"
            "[gitlab-notify:v1][object:mr][state:opened][draft:no][change:lifecycle]"
            "[transition:reviewable][project:481][mr:31]"
        )
        merged = (
            "✅ **已合并**\n"
            "[gitlab-notify:v1][object:mr][state:merged][draft:no][change:lifecycle]"
            "[transition:none][project:481][mr:31][rev:1]"
        )
        historical = (
            "👀 **可评审**\n"
            "[gitlab-notify:v1][object:mr][state:opened][draft:no][change:lifecycle]"
            "[transition:reviewable][project:481][mr:31][rev:1][route:skip]"
        )
        events = [
            {"id": original_id, "pubkey": DESK, "kind": 9, "created_at": 100,
             "tags": [["h", CHANNEL]], "content": base},
            {"id": "b" * 64, "pubkey": DESK, "kind": 40003, "created_at": 101,
             "tags": [["h", CHANNEL], ["e", original_id]], "content": merged},
            {"id": "c" * 64, "pubkey": DESK, "kind": 40003, "created_at": 102,
             "tags": [["h", CHANNEL], ["e", original_id]], "content": historical},
        ]

        target, effective = SYNC.status_card_from_thread(events, DESK, PID, "mr", 31)

        self.assertEqual(target, original_id)
        self.assertEqual(effective["id"], "b" * 64)

        events.append({
            "id": "d" * 64, "pubkey": DESK, "kind": 40003, "created_at": 103,
            "tags": [["h", CHANNEL], ["e", original_id]], "content": merged,
        })

        target, effective = SYNC.status_card_from_thread(events, DESK, PID, "mr", 31)

        self.assertEqual(target, original_id)
        self.assertEqual(effective["id"], "d" * 64)

        events.append({
            "id": "e" * 64, "pubkey": DESK, "kind": 40003, "created_at": 104,
            "tags": [["h", CHANNEL], ["e", original_id]],
            "content": merged.replace("✅ **已合并**", "❌ **状态冲突**"),
        })
        with self.assertRaisesRegex(SYNC.SyncError, "conflicting overlay revisions"):
            SYNC.status_card_from_thread(events, DESK, PID, "mr", 31)

    def test_compact_card_migrates_legacy_unstyled_headline_into_history(self):
        """R4：旧消息没有粗体状态标题时仍能迁移，不拖停整个同步器。"""
        legacy = (
            "旧格式 Git 状态\n"
            "[gitlab-notify:v1][object:mr][state:opened][draft:no][change:lifecycle]"
            "[transition:none][project:481][mr:31]"
        )
        current = (
            "✅ **已合并**\n"
            "[gitlab-notify:v1][object:mr][state:merged][draft:no][change:lifecycle]"
            "[transition:none][project:481][mr:31]"
        )

        rendered = SYNC.render_compact_status(
            {"content": legacy, "created_at": 100}, current, 101,
        )

        self.assertIn("- 1970-01-01 00:01:40 UTC 旧格式 Git 状态", rendered)
        self.assertIn("- 1970-01-01 00:01:41 UTC ✅ 已合并", rendered)

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
        self.edits = []
        self.reactions = {}
        self.fail_reaction_once = False

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

    def edit(self, event_id, content):
        self.edits.append((event_id, content))
        return self._add(40003, content, None, [("e", event_id)])

    def set_status_reaction(self, event_id, emoji):
        if self.fail_reaction_once:
            self.fail_reaction_once = False
            raise SYNC.SyncError("simulated reaction transport failure")
        self.reactions[event_id] = emoji

    def status_reaction_matches(self, event_id, emoji):
        return self.reactions.get(event_id) == emoji

    def thread(self, root_event_id):
        member_ids = {root_event_id}
        while True:
            expanded = member_ids | {
                e["id"] for e in self.events
                if any(len(tag) > 1 and tag[0] == "e" and tag[1] in member_ids for tag in e["tags"])
            }
            if expanded == member_ids:
                return [e for e in self.events if e["id"] in member_ids]
            member_ids = expanded

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

    def test_compact_status_edits_one_card_but_keeps_comments_as_messages(self):
        """L2-1-GIS-034 状态、字段和流水线原位编辑同一张卡并追加时间线；comment 仍是独立消息。"""
        self.config["compact_status_updates"] = True
        self.gitlab.mr_list = [make_mr(draft=True)]
        self.run_sync()
        root = self.mr_root()
        first_fact = next(
            event for event in self.buzz.events
            if event["kind"] == 9 and "[mr:31]" in event["content"]
            and any(tag[:2] == ["e", root] for tag in event["tags"])
        )
        self.assertEqual(self.buzz.reactions[first_fact["id"]], "📝")
        writes_after_create = len(self.buzz.writes)

        self.gitlab.mr_list = [make_mr(
            draft=False, title="恢复最近工作区（已更新）", updated_at="2026-09-13T02:00:00Z",
        )]
        self.run_sync()
        self.assertEqual(len(self.buzz.writes), writes_after_create)
        self.assertEqual(self.buzz.edits[-1][0], first_fact["id"])
        self.assertIn("状态记录\n- ", self.buzz.edits[-1][1])
        self.assertIn("👀 可评审", self.buzz.edits[-1][1])
        self.assertEqual(self.buzz.reactions[first_fact["id"]], "👀")

        self.gitlab.pipeline_list = [pipeline(54, "success", "refs/merge-requests/31/head")]
        self.run_sync()
        self.assertEqual(len(self.buzz.writes), writes_after_create)
        self.assertEqual(self.buzz.edits[-1][0], first_fact["id"])
        self.assertIn("✅ #54 流水线通过", self.buzz.edits[-1][1])
        self.assertIn("👀 可评审", self.buzz.edits[-1][1])
        self.assertEqual(self.buzz.reactions[first_fact["id"]], "✅")

        self.gitlab.mr_list = [make_mr(
            draft=False, title="恢复最近工作区（字段更新）",
            updated_at="2026-09-13T02:15:00Z",
        )]
        self.run_sync()
        self.assertEqual(len(self.buzz.writes), writes_after_create)
        self.assertIn("恢复最近工作区（字段更新）", self.buzz.edits[-1][1])
        self.assertEqual(self.buzz.reactions[first_fact["id"]], "✅")

        self.gitlab.mr_list = [make_mr(
            draft=False, title="恢复最近工作区（字段更新）", sha=SHA_B,
            updated_at="2026-09-13T02:30:00Z",
        )]
        self.run_sync()
        self.assertEqual(len(self.buzz.writes), writes_after_create)
        self.assertIn("📦 新提交", self.buzz.edits[-1][1])
        self.assertEqual(self.buzz.reactions[first_fact["id"]], "👀")
        history = self.buzz.edits[-1][1]
        self.assertIn("- 2026-09-13 02:00:00 UTC 👀 可评审", history)
        self.assertIn("- 2026-09-13 02:00:00 UTC ✅ #54 流水线通过", history)
        self.assertIn("- 2026-09-13 02:15:00 UTC ✏️ 字段更新", history)
        self.assertIn("- 2026-09-13 02:30:00 UTC 📦 新提交", history)

        self.gitlab.mr_note_list[31] = [{
            "id": 81, "author": {"id": 8, "username": "alice"}, "body": "请补一个回归测试",
            "system": False, "created_at": "2026-09-13T03:00:00Z",
        }]
        self.run_sync()
        self.assertEqual(len(self.buzz.writes), writes_after_create + 1)
        self.assertIn("💬 **评论**", self.buzz.writes[-1][1])
        self.assertIn("请补一个回归测试", self.buzz.writes[-1][1])

    def test_compact_sync_explicitly_merges_overlay_scan_into_thread(self):
        """R4：真实 CLI 的 thread 不返回 root 的 edit，主路径必须显式合入 overlay scan。"""
        self.config["compact_status_updates"] = True
        self.gitlab.mr_list = [make_mr(draft=True)]
        self.run_sync()
        root = self.mr_root()
        overlay = {
            "id": "9" * 64, "pubkey": DESK, "kind": 40003, "created_at": 2000,
            "tags": [["h", CHANNEL], ["e", next(
                event["id"] for event in self.buzz.events
                if event["kind"] == 9 and "[mr:31]" in event["content"]
            )]],
            "content": "overlay",
        }
        self.buzz.compact_edits = mock.Mock(return_value=[overlay])
        syncer = SYNC.Syncer(self.config, self.gitlab, self.buzz, state_dir=Path(self.tmp.name))

        events = syncer._thread(root, PID, "mr", 31)

        self.buzz.compact_edits.assert_called_once()
        self.assertIn(overlay, events)

    def test_compact_restart_recovers_reaction_after_edit_without_a_second_edit(self):
        """L2-1-GIS-131 edit 已 ACK、reaction 失败时，重启补 reaction 且不重复 edit。"""
        self.config["compact_status_updates"] = True
        self.gitlab.mr_list = [make_mr(draft=True)]
        self.run_sync()
        first_fact = next(
            event for event in self.buzz.events if event["kind"] == 9 and "[mr:31]" in event["content"]
        )
        self.gitlab.mr_list = [make_mr(draft=False, updated_at="2026-09-13T02:00:00Z")]
        self.buzz.fail_reaction_once = True

        with self.assertRaisesRegex(SYNC.SyncError, "reaction transport"):
            self.run_sync()
        edit_count = len(self.buzz.edits)
        ledger = json.loads(next(Path(self.tmp.name).glob("*.outbox.json")).read_text(encoding="utf-8"))
        self.assertEqual([item["kind"] for item in ledger["pending"]], ["buzz_reaction"])

        self.run_sync()

        self.assertEqual(len(self.buzz.edits), edit_count)
        self.assertEqual(self.buzz.reactions[first_fact["id"]], "👀")

    def test_compact_restart_recovers_continuation_after_accepted_edit_lost_ack(self):
        """L2-1-GIS-138 edit 已落 relay 但 ACK 丢失时，预落盘的 reaction 在重启后仍补齐。"""
        self.config["compact_status_updates"] = True
        self.gitlab.mr_list = [make_mr(draft=True)]
        self.run_sync()
        first_fact = next(
            event for event in self.buzz.events if event["kind"] == 9 and "[mr:31]" in event["content"]
        )
        self.gitlab.mr_list = [make_mr(draft=False, updated_at="2026-09-13T02:00:00Z")]
        original_edit = self.buzz.edit
        failed_once = False

        def lose_edit_ack(event_id, content):
            nonlocal failed_once
            result = original_edit(event_id, content)
            if not failed_once:
                failed_once = True
                raise SYNC.SyncError("simulated accepted edit without readback")
            return result

        self.buzz.edit = lose_edit_ack
        with self.assertRaisesRegex(SYNC.SyncError, "accepted edit without readback"):
            self.run_sync()
        ledger = json.loads(next(Path(self.tmp.name).glob("*.outbox.json")).read_text(encoding="utf-8"))
        self.assertEqual([item["kind"] for item in ledger["pending"]], ["buzz_edit", "buzz_reaction"])
        self.assertEqual(self.buzz.reactions[first_fact["id"]], "📝")
        edit_count = len(self.buzz.edits)

        self.buzz.edit = original_edit
        self.run_sync()

        self.assertEqual(len(self.buzz.edits), edit_count)
        self.assertEqual(self.buzz.reactions[first_fact["id"]], "👀")

    def test_compact_restart_sends_a_prequeued_attention_only_once_after_lost_edit_ack(self):
        """L2-1-GIS-139 edit ACK 丢失前未尝试的 p-tag 提醒可安全续跑，且不会重复。"""
        person = "a" * 64
        self.config["compact_status_updates"] = True
        self.config["people"] = {"erin": person}
        self.buzz.channel_members = lambda: {DESK: "member", person: "member"}
        reviewers = [{"username": "erin"}]
        self.gitlab.mr_list = [make_mr(draft=True, reviewers=reviewers)]
        self.run_sync()
        self.gitlab.mr_list = [make_mr(
            draft=False, reviewers=reviewers, updated_at="2026-09-13T02:00:00Z",
        )]
        original_edit = self.buzz.edit
        failed_once = False

        def lose_edit_ack(event_id, content):
            nonlocal failed_once
            result = original_edit(event_id, content)
            if not failed_once:
                failed_once = True
                raise SYNC.SyncError("simulated accepted edit without readback")
            return result

        self.buzz.edit = lose_edit_ack
        with self.assertRaisesRegex(SYNC.SyncError, "accepted edit without readback"):
            self.run_sync()
        writes_before_restart = len(self.buzz.writes)
        self.buzz.edit = original_edit

        self.run_sync()
        self.run_sync()

        reminders = [
            write for write in self.buzz.writes[writes_before_restart:]
            if "Git 状态需要你关注" in write[1]
        ]
        self.assertEqual(len(reminders), 1)
        self.assertEqual(reminders[0][2], (person,))

    def test_compact_restart_self_heals_initial_card_reaction_after_lost_send_ack(self):
        """L2-1-GIS-140 首条卡已写入但 send ACK 丢失，重启从可见卡补初始 reaction。"""
        self.config["compact_status_updates"] = True
        self.gitlab.mr_list = [make_mr(draft=True)]
        original_send = self.buzz.send
        failed_once = False

        def lose_fact_ack(content, reply_to=None, mentions=()):
            nonlocal failed_once
            result = original_send(content, reply_to, mentions)
            if "[gitlab-notify:v1][object:mr]" in content and not failed_once:
                failed_once = True
                raise SYNC.SyncError("simulated accepted fact without readback")
            return result

        self.buzz.send = lose_fact_ack
        with self.assertRaisesRegex(SYNC.SyncError, "accepted fact without readback"):
            self.run_sync()
        fact = next(
            event for event in self.buzz.events if event["kind"] == 9 and "[mr:31]" in event["content"]
        )
        self.assertNotIn(fact["id"], self.buzz.reactions)
        writes = len(self.buzz.writes)
        self.buzz.send = original_send

        self.run_sync()

        self.assertEqual(len(self.buzz.writes), writes)
        self.assertEqual(self.buzz.reactions[fact["id"]], "📝")

    def test_compact_edit_keeps_real_attention_as_a_minimal_p_tag_reply(self):
        """L2-1-GIS-135 edit 不能加 p tag；需要通知的人收到独立最小提醒而非重复状态消息。"""
        person = "a" * 64
        self.config["compact_status_updates"] = True
        self.config["people"] = {"erin": person}
        self.buzz.channel_members = lambda: {DESK: "member", person: "member"}
        reviewers = [{"username": "erin"}]
        self.gitlab.mr_list = [make_mr(draft=True, reviewers=reviewers)]
        self.run_sync()
        writes_before = len(self.buzz.writes)
        self.gitlab.mr_list = [make_mr(
            draft=False, reviewers=reviewers, updated_at="2026-09-13T02:00:00Z",
        )]

        self.run_sync()

        self.assertEqual(len(self.buzz.edits), 1)
        self.assertEqual(len(self.buzz.writes), writes_before + 1)
        reply_to, content, mentions = self.buzz.writes[-1]
        self.assertEqual(reply_to, self.buzz.edits[-1][0])
        self.assertIn("Git 状态需要你关注", content)
        self.assertIn("2026-09-13 02:00:00 UTC", content)
        self.assertRegex(content, r"变更标识 `[0-9a-f]{12}`")
        self.assertEqual(mentions, (person,))
        self.assertNotIn("[gitlab-notify:v1]", content)

    def test_compact_upgrade_edits_latest_legacy_status_not_the_later_comment(self):
        """L2-1-GIS-132 存量多回复迁移后编辑最新非评论状态事实，comment 保持独立。"""
        self.gitlab.mr_list = [make_mr(draft=True)]
        self.run_sync()
        self.gitlab.mr_list = [make_mr(draft=False, updated_at="2026-09-13T02:00:00Z")]
        self.run_sync()
        legacy_status = next(
            event for event in reversed(self.buzz.events)
            if event["kind"] == 9 and "[mr:31]" in event["content"] and "[note:" not in event["content"]
        )
        self.gitlab.mr_note_list[31] = [{
            "id": 91, "author": {"id": 8, "username": "alice"}, "body": "legacy comment",
            "system": False, "created_at": "2026-09-13T02:05:00Z",
        }]
        self.run_sync()
        legacy_comment = self.buzz.events[-1]
        writes_before = len(self.buzz.writes)

        self.config["compact_status_updates"] = True
        self.gitlab.mr_list = [make_mr(
            draft=False, title="迁移后的字段更新", updated_at="2026-09-13T02:10:00Z",
        )]
        self.run_sync()

        self.assertEqual(len(self.buzz.writes), writes_before)
        self.assertEqual(self.buzz.edits[-1][0], legacy_status["id"])
        self.assertNotEqual(self.buzz.edits[-1][0], legacy_comment["id"])

    def test_compact_backfill_pipeline_creates_the_first_mr_card_then_reacts(self):
        """L2-1-GIS-133 老 MR 首次由 pipeline 建 binding 时先建主卡，不因缺卡停摆。"""
        self.config["compact_status_updates"] = True
        self.gitlab.issue_list = [make_issue(182)]
        self.gitlab.mr_list = [make_mr(
            created_at="2026-09-12T01:00:00Z", updated_at="2026-09-12T01:00:00Z",
        )]
        self.gitlab.closes[31] = [182]
        self.gitlab.pipeline_list = [pipeline(54, "success", "refs/merge-requests/31/head")]

        summary = self.run_sync()

        self.assertEqual((summary["status"], summary["stalled"]), ("ok", []))
        card = next(
            event for event in self.buzz.events
            if event["kind"] == 9 and "[mr:31]" in event["content"] and "[events:pipeline-54-success]" in event["content"]
        )
        self.assertEqual(self.buzz.reactions[card["id"]], "✅")

    def test_old_or_wrong_sha_activity_never_overwrites_the_current_mr_status(self):
        """L2-1-GIS-134 旧时间/旧 SHA activity 只进历史，不覆盖 merged 或新 commit 当前态。"""
        self.config["compact_status_updates"] = True
        self.gitlab.mr_list = [make_mr()]
        self.run_sync()
        first_fact = next(
            event for event in self.buzz.events if event["kind"] == 9 and "[mr:31]" in event["content"]
        )

        self.gitlab.mr_list = [make_mr(
            state="merged", updated_at="2026-09-13T04:00:00Z",
        )]
        old = pipeline(54, "canceled", "refs/merge-requests/31/head")
        old["updated_at"] = "2026-09-13T03:00:00Z"
        self.gitlab.pipeline_list = [old]
        self.run_sync()
        current = self.buzz.edits[-1][1]
        self.assertIn("状态流转 → 已合并", current)
        self.assertLess(current.index("03:00:00 UTC ⚪ #54 流水线取消"), current.index("04:00:00 UTC 🔄 状态流转 → 已合并"))
        self.assertEqual(self.buzz.reactions[first_fact["id"]], "✅")

        # A later-observed green pipeline for SHA_A must not green-light current SHA_B.
        self.gitlab.mr_list = [make_mr(updated_at="2026-09-13T04:30:00Z")]
        self.run_sync()
        self.gitlab.mr_list = [make_mr(sha=SHA_B, updated_at="2026-09-13T05:00:00Z")]
        self.run_sync()
        wrong_sha = pipeline(55, "success", "refs/merge-requests/31/head")
        wrong_sha["updated_at"] = "2026-09-13T06:00:00Z"
        self.gitlab.pipeline_list = [old, wrong_sha]
        self.run_sync()
        self.assertIn("📦 新提交", self.buzz.edits[-1][1])
        self.assertEqual(self.buzz.reactions[first_fact["id"]], "👀")

    def test_delayed_current_sha_pipeline_reaction_survives_next_round_self_heal(self):
        """L2-1-GIS-142 旧时间但属当前 SHA 的 pipeline 可更新 reaction，下一轮不会被 headline 回滚。"""
        self.config["compact_status_updates"] = True
        self.gitlab.mr_list = [make_mr(draft=True)]
        self.run_sync()
        card = next(event for event in self.buzz.events if event["kind"] == 9 and "[mr:31]" in event["content"])
        self.gitlab.mr_list = [make_mr(draft=False, updated_at="2026-09-13T05:00:00Z")]
        self.run_sync()
        delayed = pipeline(54, "success", "refs/merge-requests/31/head")
        delayed["updated_at"] = "2026-09-13T04:00:00Z"
        self.gitlab.pipeline_list = [delayed]

        self.run_sync()

        self.assertEqual(self.buzz.reactions[card["id"]], "✅")
        self.assertEqual(SYNC.parse_header(self.buzz.edits[-1][1])["reaction"], "success")
        self.assertEqual(SYNC.parse_header(self.buzz.edits[-1][1])["route"], "skip")

        self.run_sync()

        self.assertEqual(self.buzz.reactions[card["id"]], "✅")

    def test_compact_issue_lifecycle_edits_the_original_fact(self):
        """L2-1-GIS-035 Issue reopen/close 是原卡时间线和 Git 状态 reaction，不再新增状态回复。"""
        self.config["compact_status_updates"] = True
        self.gitlab.issue_list = [make_issue(182)]
        self.run_sync()
        root = next(
            event["id"] for event in self.buzz.events
            if event["kind"] == 9 and f"{WEB}/-/issues/182" in event["content"]
            and not any(tag[0] == "e" for tag in event["tags"])
        )
        first_fact = next(event for event in self.buzz.events if event["id"] == root)
        writes_after_create = len(self.buzz.writes)

        closed = make_issue(182)
        closed.update(state="closed", updated_at="2026-09-13T02:30:00Z")
        self.gitlab.issue_list = [closed]
        self.run_sync()

        self.assertEqual(len(self.buzz.writes), writes_after_create)
        self.assertEqual(self.buzz.edits[-1][0], first_fact["id"])
        self.assertIn("🔄 状态流转 → 已关闭", self.buzz.edits[-1][1])
        self.assertIn("状态记录\n- ", self.buzz.edits[-1][1])
        self.assertEqual(self.buzz.reactions[first_fact["id"]], "✅")

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
        self.config["compact_status_updates"] = True
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
        self.assertEqual(self.buzz.reactions[mr_facts[0]["id"]], "👀")


if __name__ == "__main__":
    unittest.main()
