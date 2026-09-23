"""Milestone threads (2026-09-18 notification policy): a 🎯 plaque per milestone, event
facts reply below, identity from target_iid or a title match, recovery by plaque URL."""

import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
SCRIPT = SKILL / "scripts" / "gitlab_buzz_sync.py"


def load_module():
    spec = importlib.util.spec_from_file_location("gitlab_buzz_sync_milestone", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SYNC = load_module()

PID = 481
CHANNEL = "00000000-0000-4000-8000-0000000000c1"
DESK = "d" * 64
BOT_ID = 7
BOT = "buzz-sync-bot"
WEB = "http://127.0.0.1:8929/buzz-sync-test/pilot"
SINCE = "2026-09-13T00:00:00Z"
SERVER_TIME = "2026-09-13T04:00:00Z"


def milestone_event(event_id, action, title="2026-Q4", iid=None, created="2026-09-13T02:00:00.000Z"):
    return {
        "id": event_id, "action_name": action, "target_type": "Milestone", "target_iid": iid,
        "target_title": title, "created_at": created,
        "author": {"username": "alice"},
    }


class MilestonePlaqueTest(unittest.TestCase):
    def test_plaque_renders_and_identifies(self):
        """🎯 门牌：图标+标题+项目路径+末行裸 URL；plaque_identity 认出 milestone 与 iid。"""
        url = f"{WEB}/-/milestones/9"
        plaque = SYNC.render_milestone_plaque("2026-Q4 鸟类百科", url, "buzz-sync-test/pilot")
        lines = plaque.split("\n")
        self.assertIsNone(SYNC.parse_header(plaque))
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines[0], "🎯 **2026-Q4 鸟类百科**")
        self.assertEqual(SYNC.plaque_url(plaque), url)
        self.assertEqual(SYNC.plaque_identity(url), {"object": "milestone", "iid": 9})

    def test_plaque_title_cannot_forge_markdown(self):
        """标题里的 [] \ 被转义，门牌不能伪造链接语法。"""
        plaque = SYNC.render_milestone_plaque("evil [x](https://evil.example)", f"{WEB}/-/milestones/9", "p")
        self.assertNotIn("[x](https://evil.example)", plaque)
        self.assertIn("\\[x\\]", plaque)

    def test_record_carries_identity_or_title(self):
        """events 路径：带 target_iid 的记录带 per-iid URL；缺失时留给标题归位（source_id None）。"""
        with_iid = SYNC.record_from_event(milestone_event(5, "created", iid=9), PID, WEB)
        self.assertEqual((with_iid["object"], with_iid["placement"], with_iid["source_id"]),
                         ("milestone", "milestone_thread", 9))
        self.assertEqual(with_iid["url"], f"{WEB}/-/milestones/9")
        title_only = SYNC.record_from_event(milestone_event(6, "closed"), PID, WEB)
        self.assertEqual((title_only["placement"], title_only["source_id"], title_only["title"]),
                         ("milestone_thread", None, "2026-Q4"))
        self.assertEqual(title_only["url"], f"{WEB}/-/milestones")

    def test_render_record_styles_milestone_facts(self):
        """milestone 事实正文：固定短语表（无 LLM），header 可回解析，events 行可去重。"""
        record = SYNC.record_from_event(milestone_event(5, "created", iid=9), PID, WEB)
        rendered = SYNC.render_record(record)
        self.assertEqual(
            rendered.split("\n")[-1],
            f"[gitlab-notify:v1][object:milestone][event:created][project:{PID}][events:event-5]",
        )
        self.assertIn("🎯 **里程碑创建** · 2026-Q4", rendered)
        self.assertEqual(
            SYNC.parse_header(rendered),
            {"object": "milestone", "event": "created", "project": PID, "events": ["event-5"]},
        )
        self.assertEqual(SYNC.posted_keys([{"pubkey": DESK, "content": rendered}], DESK), {"event-5"})


class FakeGitLab:
    def __init__(self):
        self.user = {"id": BOT_ID, "username": BOT}
        self.event_list = []
        self.pipeline_list = []
        self.deployment_list = []
        self.milestone_list = []

    def current_user(self):
        return self.user

    def scan_time(self):
        return SERVER_TIME

    def project(self, project_id):
        return {"id": project_id, "default_branch": "main", "web_url": WEB,
                "path_with_namespace": "buzz-sync-test/pilot", "visibility": "public"}

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

    def milestones(self, project_id):
        return list(self.milestone_list)

    def releases(self, project_id):
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
        self.events.append({"id": event_id, "pubkey": DESK, "kind": 9, "created_at": 1000 + len(self.events),
                            "tags": tags, "content": content})
        self.writes.append((reply_to, content))
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
        return [e for e in self.events
                if query in str(e.get("content", ""))
                and not any(tag[0] == "e" for tag in e.get("tags") or [])]


class MilestoneSyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.gitlab = FakeGitLab()
        self.buzz = FakeBuzz()
        self.config = {
            "channel_id": CHANNEL, "publisher_pubkey": DESK, "since": SINCE, "include_confidential": False,
            "exclude": [], "diff": {"enabled": False, "private": False},
            "gitlab": {"base_url": "http://127.0.0.1:8929", "token_env": "NH_DESK_GITLAB_TOKEN",
                       "bot_user_id": BOT_ID, "bot_username": BOT, "projects": [PID]},
            "buzz": {}, "people": {},
        }

    def run_sync(self):
        return SYNC.Syncer(self.config, self.gitlab, self.buzz, state_dir=Path(self.tmp.name)).run()

    def test_milestone_events_open_one_thread_and_reply(self):
        """同一 milestone 的事件只开一个 🎯 门牌 Thread，事实逐条回复；重跑不重复。"""
        self.gitlab.event_list = [milestone_event(5, "created", iid=9),
                                  milestone_event(6, "closed", iid=9)]
        summary = self.run_sync()
        self.assertEqual(summary["notified"]["milestone"], 2)
        roots = [e for e in self.buzz.events if not any(tag[0] == "e" for tag in e["tags"])]
        self.assertEqual(len(roots), 1)
        self.assertEqual(roots[0]["content"].split("\n")[0], "🎯 **2026-Q4**")
        replies = [e["content"] for e in self.buzz.thread(roots[0]["id"]) if e["id"] != roots[0]["id"]]
        self.assertEqual(len(replies), 2)
        self.assertIn("🎯 **里程碑创建** · 2026-Q4", replies[0])
        self.assertIn("🎯 **里程碑关闭** · 2026-Q4", replies[1])
        self.assertTrue(all(f"[events:event-{key}]" in reply for key, reply in zip((5, 6), replies)))
        # the reply carries the canonical per-milestone URL, even for title-resolved records
        self.assertTrue(all(f"{WEB}/-/milestones/9" in reply for reply in replies))

        writes = len(self.buzz.writes)
        second = self.run_sync()
        self.assertEqual((second["notified"]["milestone"], len(self.buzz.writes)), (0, writes))

    def test_origin_binds_milestone_into_named_desk_thread(self):
        plaque = "📋 **#14 weekly**\nbuzz-sync-test/pilot\n" + f"{WEB}/-/issues/14"
        origin = self.buzz.send(plaque)
        self.gitlab.milestone_list = [{
            "iid": 9, "title": "2026-Q4",
            "description": SYNC.render_origin_block(CHANNEL, origin),
        }]
        self.gitlab.event_list = [milestone_event(5, "created", iid=9)]
        summary = self.run_sync()
        self.assertEqual(summary["notified"]["milestone"], 1)
        roots = [e for e in self.buzz.events if not any(tag[0] == "e" for tag in e["tags"])]
        self.assertEqual([e["id"] for e in roots], [origin], "milestone must not open its own plaque")
        replies = [e for e in self.buzz.thread(origin) if e["id"] != origin]
        self.assertEqual(len(replies), 1)
        self.assertIn("🎯 **里程碑创建**", replies[0]["content"])

    def test_milestone_multiple_origin_links_fan_out(self):
        first = self.buzz.send("📋 **#14 weekly**\nbuzz-sync-test/pilot\n" + f"{WEB}/-/issues/14")
        second = self.buzz.send("📋 **#15 other**\nbuzz-sync-test/pilot\n" + f"{WEB}/-/issues/15")
        self.gitlab.milestone_list = [{
            "iid": 9, "title": "2026-Q4",
            "description": (
                SYNC.buzz_message_link(CHANNEL, first) + "\n"
                + SYNC.buzz_message_link(CHANNEL, second)
            ),
        }]
        self.gitlab.event_list = [milestone_event(5, "created", iid=9)]
        self.run_sync()
        self.assertEqual(
            [e["id"] for e in self.buzz.events if not any(tag[0] == "e" for tag in e["tags"])],
            [first, second],
        )
        for root in (first, second):
            replies = [e for e in self.buzz.thread(root) if e["id"] != root]
            self.assertEqual(len(replies), 1)
            self.assertIn("🎯 **里程碑创建**", replies[0]["content"])

    def test_title_resolved_against_milestone_list(self):
        """缺 target_iid 的事件按标题精确匹配 milestones 列表归位；匹配不到记 degrade 不静默丢。"""
        self.gitlab.milestone_list = [{"iid": 9, "title": "2026-Q4"}, {"iid": 10, "title": "2027-H1"}]
        self.gitlab.event_list = [milestone_event(5, "created", iid=None, title="2026-Q4"),
                                  milestone_event(6, "created", iid=None, title="gone")]
        summary = self.run_sync()
        self.assertEqual(summary["notified"]["milestone"], 1)
        self.assertEqual(summary["skipped"]["milestone_identity"], 1)
        self.assertIn(f"{PID}:milestone_identity:1 unresolved", summary["degraded"])
        self.assertIn(f"{WEB}/-/milestones/9", self.buzz.writes[0][1])

    def test_state_loss_recovers_the_thread_by_plaque_url(self):
        """状态文件丢了：按门牌 URL 全频道找回原 Thread 并重新锚定，不开重复 Thread。"""
        self.gitlab.event_list = [milestone_event(5, "created", iid=9)]
        self.run_sync()
        original_roots = [e for e in self.buzz.events if not any(tag[0] == "e" for tag in e["tags"])]
        self.assertEqual(len(original_roots), 1)
        for binding in Path(self.tmp.name).glob("milestone-bindings-*.json"):
            binding.unlink()
        self.gitlab.event_list = [milestone_event(7, "opened", iid=9, created="2026-09-13T05:00:00.000Z")]
        summary = self.run_sync()
        self.assertEqual(summary["created"], 0)  # recovered, not re-created
        roots = [e for e in self.buzz.events if not any(tag[0] == "e" for tag in e["tags"])]
        self.assertEqual(roots, original_roots)
        self.assertEqual(summary["notified"]["milestone"], 1)
        state = json.loads(next(Path(self.tmp.name).glob("milestone-bindings-*.json")).read_text(encoding="utf-8"))
        self.assertEqual(state, {"9": original_roots[0]["id"]})

    def test_corrupt_binding_file_fails_closed(self):
        """milestone 绑定文件损坏（坏 JSON / 字段离谱）：整轮失败关闭，不静默开新 Thread。"""
        path = Path(self.tmp.name) / f"milestone-bindings-{PID}.json"
        path.write_text("{not json", encoding="utf-8")
        self.gitlab.event_list = [milestone_event(5, "created", iid=9)]
        with self.assertRaises(SYNC.SyncError):
            self.run_sync()
        path.write_text(json.dumps({"9": "not-hex"}), encoding="utf-8")
        with self.assertRaises(SYNC.SyncError):
            self.run_sync()

    def test_webhook_milestone_goes_to_thread(self):
        """webhook milestone → milestone_thread，iid 取 attrs.iid，URL 带 iid。"""
        payload = {
            "object_kind": "milestone", "event_type": "milestone", "project": {
                "id": PID, "web_url": WEB, "default_branch": "main", "path_with_namespace": "buzz-sync-test/pilot",
            },
            "object_attributes": {"id": 61, "iid": 10, "title": "2026-Q4", "description": "",
                                  "state": "active", "created_at": "2026-09-01 00:00:00 UTC",
                                  "updated_at": "2026-09-13 02:00:00 UTC"},
            "action": "create",
        }
        result = SYNC.normalize_webhook(payload)
        record = result["records"][0]
        self.assertEqual((record["object"], record["event"], record["placement"], record["source_id"]),
                         ("milestone", "created", "milestone_thread", 10))
        self.assertEqual(record["url"], f"{WEB}/-/milestones/10")


if __name__ == "__main__":
    unittest.main()
