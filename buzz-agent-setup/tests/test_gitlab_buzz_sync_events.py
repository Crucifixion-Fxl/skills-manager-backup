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
    spec = importlib.util.spec_from_file_location("gitlab_buzz_sync_events", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SYNC = load_module()

DESK = "d" * 64
OTHER = "e" * 64
CHANNEL = "00000000-0000-4000-8000-0000000000c1"
BOT_ID = 7
BOT = "buzz-sync-bot"
PID = 481
WEB = "http://127.0.0.1:8929/buzz-sync-test/pilot"
SINCE = "2026-09-13T00:00:00Z"
SERVER_TIME = "2026-09-13T04:00:00Z"


def push_event(event_id, action="pushed to", ref_type="branch", ref="feature/x", count=2,
               title="fix: restore", created="2026-09-13T02:00:00.000Z", username="alice"):
    return {
        "id": event_id, "action_name": action, "target_type": None, "target_iid": None,
        "target_title": None, "created_at": created, "author": {"username": username, "name": "Alice"},
        "push_data": {"ref_type": ref_type, "ref": ref, "commit_count": count, "commit_title": title},
    }


def target_event(event_id, target_type, action, iid=None, title="t", note=None,
                 created="2026-09-13T02:00:00.000Z", username="alice"):
    return {
        "id": event_id, "action_name": action, "target_type": target_type, "target_iid": iid,
        "target_title": title, "created_at": created, "author": {"username": username, "name": "Alice"},
        "note": note,
    }


def pipeline(pipeline_id, status, ref="main", updated="2026-09-13T02:00:00.000Z"):
    return {"id": pipeline_id, "iid": pipeline_id, "status": status, "ref": ref, "sha": "a" * 40,
            "source": "push", "web_url": f"{WEB}/-/pipelines/{pipeline_id}", "updated_at": updated}


class EventClassificationTest(unittest.TestCase):
    def record(self, event):
        return SYNC.record_from_event(event, PID, WEB)

    def test_push_branch_and_tag(self):
        """L1-GIS-032 政策 2026-09-18（13:16 修订）：分支 push 不产生记录；tag 建删/移动即时。"""
        for event in (push_event(1), push_event(2, action="pushed new"),
                      push_event(3, action="deleted", count=0)):
            with self.subTest(event=event["id"]):
                self.assertIsNone(self.record(event))
        tag = self.record(push_event(4, action="pushed new", ref_type="tag", ref="v1.2.0", count=0))
        self.assertEqual((tag["object"], tag["event"], tag["placement"], tag["ref"]),
                         ("tag", "tag_created", "instant", "v1.2.0"))
        removed = self.record(push_event(5, action="deleted", ref_type="tag", ref="v1.1.0", count=0))
        self.assertEqual((removed["event"], removed["placement"]), ("tag_deleted", "instant"))
        self.assertEqual(removed["url"], f"{WEB}/-/tags/v1.1.0")

    def test_issue_mr_and_comments(self):
        """L1-GIS-032 Issue/MR 与其评论交给 Thread 同步；MR 批准进 MR Thread；commit/snippet 评论不通知。"""
        for event in (
            target_event(10, "Issue", "opened", iid=182),
            target_event(11, "WorkItem", "closed", iid=182),
            target_event(12, "MergeRequest", "accepted", iid=31),
            target_event(13, "Note", "commented on", note={"noteable_type": "Issue", "noteable_iid": 182, "body": "x"}),
            target_event(14, "DiffNote", "commented on",
                         note={"noteable_type": "MergeRequest", "noteable_iid": 31, "body": "x"}),
        ):
            with self.subTest(event=event["id"]):
                self.assertEqual(self.record(event)["placement"], "thread")
        approved = self.record(target_event(15, "MergeRequest", "approved", iid=31))
        self.assertEqual((approved["placement"], approved["mr_iid"]), ("mr_thread", 31))
        commit_note = self.record(target_event(16, "Note", "commented on",
                                               note={"noteable_type": "Commit", "noteable_iid": None,
                                                     "body": "@nh-dev 这行有问题"}))
        self.assertIsNone(commit_note)
        snippet_note = self.record(target_event(17, "Note", "commented on",
                                                note={"noteable_type": "Snippet", "body": "x"}))
        self.assertIsNone(snippet_note)

    def test_other_targets_go_to_digest(self):
        """L1-GIS-032 2026-09-18 政策：milestone 进独立 Thread；wiki、成员与未知类型不产生记录。"""
        milestone = self.record(target_event(20, "Milestone", "created", title="2026-Q4", iid=9))
        self.assertEqual((milestone["object"], milestone["event"], milestone["placement"], milestone["source_id"]),
                         ("milestone", "created", "milestone_thread", 9))
        self.assertEqual(milestone["url"], f"{WEB}/-/milestones/9")
        title_only = self.record(target_event(24, "Milestone", "closed", title="2026-Q4"))
        self.assertEqual((title_only["placement"], title_only["source_id"]), ("milestone_thread", None))
        self.assertEqual(title_only["url"], f"{WEB}/-/milestones")
        for event in (target_event(21, "WikiPage::Meta", "updated", title="Runbook"),
                      target_event(22, None, "joined"),
                      target_event(23, "DesignManagement::Design", "created")):
            with self.subTest(object_kind=event["target_type"]):
                self.assertIsNone(self.record(event))

    def test_pipeline_deployment_release(self):
        """L1-GIS-032 流水线只取终态；默认分支失败即时，MR 流水线全部终态进 MR Thread，其余不通知；
        部署只有失败/受阻即时，其余不通知（2026-09-18 政策）。"""
        self.assertIsNone(SYNC.record_from_pipeline(pipeline(50, "running"), PID, "main"))
        failed = SYNC.record_from_pipeline(pipeline(51, "failed"), PID, "main")
        self.assertEqual((failed["object"], failed["event"], failed["placement"], failed["key"]),
                         ("pipeline", "failed", "instant", "pipeline-51-failed"))
        self.assertIsNone(SYNC.record_from_pipeline(pipeline(52, "success"), PID, "main"))
        self.assertIsNone(SYNC.record_from_pipeline(pipeline(53, "failed", ref="feature/x"), PID, "main"))
        for status in ("success", "failed", "canceled"):
            mr_pipeline = SYNC.record_from_pipeline(
                pipeline(54, status, ref="refs/merge-requests/31/head"), PID, "main")
            self.assertEqual((mr_pipeline["placement"], mr_pipeline["mr_iid"]), ("mr_thread", 31), status)

        self.assertIsNone(SYNC.record_from_deployment(
            {"id": 70, "iid": 7, "status": "success", "ref": "main", "sha": "a" * 40,
             "environment": {"name": "production"}, "updated_at": "2026-09-13T02:00:00Z"}, PID, WEB))
        for status in ("failed", "blocked"):
            record = SYNC.record_from_deployment(
                {"id": 72, "iid": 7, "status": status, "ref": "main", "sha": "a" * 40,
                 "environment": {"name": "production"}, "updated_at": "2026-09-13T02:00:00Z"}, PID, WEB)
            self.assertEqual((record["object"], record["event"], record["placement"], record["sha"]),
                             ("deployment", status, "instant", "a" * 40), status)
        for status in ("running", "canceled", "created"):
            self.assertIsNone(SYNC.record_from_deployment(
                {"id": 73, "iid": 7, "status": status, "ref": "main", "sha": "a" * 40,
                 "environment": {"name": "production"}, "updated_at": "2026-09-13T02:00:00Z"}, PID, WEB), status)

        release = {"tag_name": "v1.2.0", "name": "1.2.0", "created_at": "2026-09-13T01:00:00Z",
                   "_links": {"self": f"{WEB}/-/releases/v1.2.0"}}
        record = SYNC.record_from_release(release, PID, SINCE)
        self.assertEqual((record["object"], record["event"], record["placement"]), ("release", "created", "instant"))
        self.assertEqual(record["key"], "release-481-" + hashlib.sha256(b"v1.2.0").hexdigest()[:12])
        self.assertIsNone(SYNC.record_from_release({**release, "created_at": "2026-09-12T01:00:00Z"}, PID, SINCE))

    def test_polling_gaps_are_declared(self):
        """L1-GIS-032 轮询抓不到的类型以常量声明（2026-09-18 政策更新；Release 恢复后缺口条目回归）。"""
        self.assertEqual(SYNC.POLLING_GAPS,
                         ("emoji", "release_update_delete", "intermediate_states",
                          "mr_unapproval", "discussion_resolution", "auto_merge_setting"))


class RenderTest(unittest.TestCase):
    def test_render_instant_record(self):
        """L1-GIS-033 即时消息 header 与 events 行；@ 转义。"""
        record = SYNC._record(
            "event-9", "tag", "tag_created", "instant", PID, actor="＠root", ref="v1.2.0", url=f"{WEB}/-/tags/v1.2.0",
            title="v1.2.0")
        rendered = SYNC.render_record(record)
        lines = rendered.split("\n")
        self.assertEqual(
            lines[-1],
            "[gitlab-notify:v1][object:tag][event:tag_created][project:481][events:event-9]",
        )
        self.assertIn("ref: v1.2.0", lines)
        self.assertIn("by: ＠root", lines)
        self.assertNotIn("events: event-9", lines)
        self.assertEqual(
            SYNC.parse_header(rendered),
            {"object": "tag", "event": "tag_created", "project": 481, "events": ["event-9"]},
        )

    def test_render_digest(self):
        """L1-GIS-033 / L1-GIS-049 摘要按类型计数、列 ref 与 events；只有一条时也用 activity header。

        政策 2026-09-18 后 live 记录不再进 digest，渲染器留给合成输入与回滚。"""
        records = [SYNC._record("event-1", "push", "pushed", "digest", PID, ref="feature/x", url=f"{WEB}/-/commits/feature/x"),
                   SYNC._record("event-2", "push", "pushed", "digest", PID, ref="feature/x", url=f"{WEB}/-/commits/feature/x"),
                   SYNC._record("event-3", "wiki", "created", "digest", PID, url=f"{WEB}/-/wikis", title="Runbook")]
        rendered = SYNC.render_digest(records, PID)
        lines = rendered.split("\n")
        self.assertEqual(
            lines[-1],
            "[gitlab-notify:v1][object:activity][event:digest][project:481]"
            "[events:event-1,event-2,event-3]",
        )
        self.assertIn("summary: 2 push · 1 wiki", lines)
        self.assertIn("refs: feature/x", lines)
        self.assertNotIn("events: event-1,event-2,event-3", lines)
        self.assertNotEqual(SYNC.render_digest(records[:1], PID), SYNC.render_record(records[0]))


class DedupeTest(unittest.TestCase):
    def test_posted_keys(self):
        """L1-GIS-034 只从首行是同步 header 的 Desk 消息的 events 行解析已发 key。"""
        events = [
            {"id": "1" * 64, "pubkey": DESK, "content": "[gitlab-notify:v1][object:activity][event:digest][project:481]\n"
                                                        "summary: 1 push\nevents: event-1,pipeline-5-failed"},
            {"id": "2" * 64, "pubkey": OTHER, "content": "x\nevents: event-9"},
            {"id": "3" * 64, "pubkey": DESK,
             "content": "[gitlab-notify:v1][object:tag][event:tag_created][project:481]\nevents: bad key,event-2"},
            {"id": "4" * 64, "pubkey": DESK, "content": "no header\nevents: event-3"},
        ]
        self.assertEqual(SYNC.posted_keys(events, DESK), {"event-1", "pipeline-5-failed", "event-2"})


class EventsCursorTest(unittest.TestCase):
    def test_after_date_and_filter(self):
        """L1-GIS-035 after 取游标前一天的日期；本地按 created_at 过滤。"""
        self.assertEqual(SYNC.events_after_date("2026-09-13T00:30:00Z"), "2026-09-12")
        events = [push_event(1, created="2026-09-12T23:59:59.000Z"), push_event(2, created="2026-09-13T00:30:00.000Z")]
        self.assertEqual([e["id"] for e in SYNC.events_since(events, "2026-09-13T00:30:00Z")], [2])

    def test_gitlab_events_paginates(self):
        """L1-GIS-035 Events API 翻页直到没有下一页。"""
        client = object.__new__(SYNC.GitLabClient)
        calls = []

        def request(method, path, params=None, body=None):
            calls.append((path, dict(params)))
            page = int(params["page"])
            return [push_event(page)], {"x-next-page": "2" if page == 1 else ""}

        client.request = request
        events = client.events(PID, "2026-09-12")
        self.assertEqual([e["id"] for e in events], [1, 2])
        self.assertEqual(calls[0][0], f"projects/{PID}/events")
        self.assertEqual((calls[0][1]["after"], calls[0][1]["sort"]), ("2026-09-12", "asc"))


class FakeGitLab:
    def __init__(self):
        self.user = {"id": BOT_ID, "username": BOT}
        self.event_list = []
        self.pipeline_list = []
        self.deployment_list = []
        self.release_list = []

    def current_user(self):
        return self.user

    def scan_time(self):
        return SERVER_TIME

    def project(self, project_id):
        return {"id": project_id, "default_branch": "main", "web_url": WEB, "visibility": "public"}

    def issues(self, project_id, updated_after):
        return []

    def merge_requests(self, project_id, updated_after):
        return []

    def members(self, project_id):
        return []

    def events(self, project_id, after_date):
        return list(self.event_list)

    def pipelines(self, project_id, updated_after):
        return list(self.pipeline_list)

    def deployments(self, project_id, updated_after):
        return list(self.deployment_list)

    def releases(self, project_id):
        return list(self.release_list)

    def feature_flags(self, project_id):
        return []

    def access_tokens(self, project_id):
        return []


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
        self.events.append({"id": event_id, "pubkey": DESK, "kind": 9, "created_at": 1000 + len(self.events),
                            "tags": tags, "content": content})
        self.writes.append((reply_to, content, tuple(mentions)))
        return event_id

    def channel_messages(self, since_unix):
        return list(self.events)

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

class TopLevelSyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.gitlab = FakeGitLab()
        self.buzz = FakeBuzz()
        self.config = {
            "channel_id": CHANNEL, "publisher_pubkey": DESK, "since": SINCE, "include_confidential": False,
            "exclude": [], "diff": {"enabled": False, "private": False},
            "gitlab": {"base_url": "http://127.0.0.1:8929", "token_env": "NH_DESK_GITLAB_TOKEN",
                       "bot_user_id": BOT_ID, "bot_username": BOT, "projects": [PID]},
            "buzz": {}, "people": {},
        }
        self.gitlab.event_list = [
            push_event(1), push_event(2),
            push_event(3, action="pushed new", ref_type="tag", ref="v1.2.0", count=0),
            target_event(4, "WikiPage::Meta", "created", title="Runbook"),
            target_event(5, "Issue", "opened", iid=182),
            target_event(6, "Note", "commented on", note={"noteable_type": "Commit", "body": "nit"}),
            push_event(7, created="2026-09-12T20:00:00.000Z"),
        ]
        self.gitlab.pipeline_list = [pipeline(50, "failed"), pipeline(51, "success", ref="feature/x"),
                                     pipeline(52, "running")]
        self.gitlab.deployment_list = [{"id": 70, "iid": 7, "status": "success", "ref": "main", "sha": "a" * 40,
                                        "environment": {"name": "production"}, "updated_at": "2026-09-13T02:00:00Z"}]
        self.gitlab.release_list = [{"tag_name": "v1.2.0", "name": "1.2.0", "created_at": "2026-09-13T01:00:00Z",
                                     "_links": {"self": f"{WEB}/-/releases/v1.2.0"}}]

    def tearDown(self):
        self.tmp.cleanup()

    def run_sync(self):
        return SYNC.Syncer(self.config, self.gitlab, self.buzz, state_dir=Path(self.tmp.name)).run()

    def test_top_level_policy_notifies_failures_only(self):
        """L1-GIS-144 2026-09-18 政策（13:16 修订）：push/wiki/commit 评论/部署成功静默；
        失败类与发布类（tag/Release）即时。"""
        summary = self.run_sync()
        self.assertEqual(summary["notified"], {"instant": 3, "milestone": 0})
        self.assertEqual(summary["gaps"], list(SYNC.POLLING_GAPS))
        headers = [SYNC.parse_header(content) for _, content, _ in self.buzz.writes]
        instant = sorted((h["object"], h.get("event")) for h in headers
                         if h and h["object"] in {"pipeline", "tag", "release"})
        self.assertEqual(instant, [("pipeline", "failed"), ("release", "created"), ("tag", "tag_created")])
        self.assertFalse(any(h and h["object"] in {"branch", "activity"} for h in headers))
        self.assertEqual(summary["summary_requests"], [])
        self.assertEqual(summary["activity"], 0)

        writes_before = len(self.buzz.writes)
        second = self.run_sync()
        self.assertEqual(second["notified"], {"instant": 0, "milestone": 0})
        self.assertEqual(len(self.buzz.writes), writes_before)

    def test_activity_queues_sanitized_summary_request_without_direct_send(self):
        """L1-GIS-144 digest 记录仍走 durable 汇总请求、绝不直发。live 记录已不产生 digest
        （2026-09-18 政策），这里用合成 digest 记录保住队列与清洗契约。"""
        synthetic = {
            4: SYNC._record("event-4", "wiki", "created", "digest", PID, url=f"{WEB}/-/wikis", title="Runbook"),
            6: SYNC._record("event-6", "note", "commit_comment", "digest", PID, title="＠nh-dev 这行有问题"),
        }

        def fake_record(event, project_id, web_url):
            return synthetic.get(event["id"])

        writes = []
        original_write = SYNC.atomic_write_json

        def observed_write(path, value):
            writes.append((Path(path).name, json.loads(json.dumps(value))))
            return original_write(path, value)

        with mock.patch.object(SYNC, "record_from_event", side_effect=fake_record), \
                mock.patch.object(SYNC, "atomic_write_json", side_effect=observed_write):
            summary = self.run_sync()

        self.assertTrue(all(mentions == () for _, _, mentions in self.buzz.writes))
        self.assertEqual(len(summary["summary_requests"]), 1)
        request = summary["summary_requests"][0]
        self.assertEqual(set(request), {"request_id", "project_id", "facts", "facts_sha256"})
        self.assertEqual(request["project_id"], PID)
        self.assertEqual(
            request["facts_sha256"],
            hashlib.sha256(json.dumps(
                request["facts"], sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            ).encode("utf-8")).hexdigest(),
        )
        self.assertRegex(request["request_id"], r"^[0-9a-f]{64}$")
        self.assertEqual(len(request["facts"]), 2)
        public_json = json.dumps(request, sort_keys=True)
        for forbidden in ("source_id", "event_id", '"key"', "pipeline-51-success"):
            self.assertNotIn(forbidden, public_json)

        outbox = json.loads(next(Path(self.tmp.name).glob("*.outbox.json")).read_text(encoding="utf-8"))
        pending = outbox["pending"]
        self.assertEqual(len(pending), 1)
        self.assertEqual((pending[0]["status"], pending[0]["kind"]), ("PENDING", "summary_request"))
        self.assertEqual(set(pending[0]["payload"]["source_keys"]), {"event-4", "event-6"})
        outbox_indexes = [index for index, (name, _) in enumerate(writes) if name.endswith(".outbox.json")]
        cache_index = next(index for index, (name, _) in enumerate(writes) if name.endswith(".cache.json"))
        self.assertTrue(outbox_indexes)
        self.assertLess(outbox_indexes[-1], cache_index, "summary request must be durable before cursor")

        writes_before = len(self.buzz.writes)
        second = self.run_sync()
        self.assertEqual(second["summary_requests"], [request])
        self.assertEqual(len(self.buzz.writes), writes_before)


if __name__ == "__main__":
    unittest.main()
