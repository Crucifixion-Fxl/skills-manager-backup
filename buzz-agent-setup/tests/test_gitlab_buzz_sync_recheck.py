"""Stalled objects are re-read every run; per-object not-found isolates; mentions and dedupe edge cases."""
import datetime as dt
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FAKES = load("gitlab_buzz_sync_recheck_fakes", TESTS / "test_gitlab_buzz_sync_isolation.py")
SYNC = FAKES.SYNC
ALICE, BOB, CHANNEL, PID = FAKES.ALICE, FAKES.BOB, FAKES.CHANNEL, FAKES.PID
make_issue, make_mr, binding_note, config = FAKES.make_issue, FAKES.make_mr, FAKES.binding_note, FAKES.config


class CursorGitLab(FAKES.FakeGitLab):
    """Honors updated_after like GitLab, can move the server clock, and reads single objects."""

    def __init__(self):
        super().__init__()
        self.server_time = FAKES.SERVER_TIME
        self.missing_objects, self.object_reads = set(), []

    def scan_time(self):
        return self.server_time

    def issues(self, project_id, updated_after):
        boundary = SYNC.parse_timestamp(updated_after)
        return [dict(item) for item in self.issue_list.get(project_id, [])
                if SYNC.parse_timestamp(item["updated_at"]) >= boundary]

    def issue(self, project_id, iid):
        self.object_reads.append(("issue", iid))
        if iid in self.missing_objects:
            raise SYNC.GitLabHTTPError("GET", f"projects/{project_id}/issues/{iid}", 404)
        return dict(next(item for item in self.issue_list[project_id] if item["iid"] == iid))


class AllMessagesBuzz(FAKES.FakeBuzz):
    """Returns thread replies from the channel scan too, as `messages get` does."""

    def channel_messages(self, since_unix):
        self.channel_calls.append(since_unix)
        return list(self.events)


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.gitlab, self.buzz = CursorGitLab(), AllMessagesBuzz()

    def run_sync(self, cfg=None):
        return SYNC.Syncer(cfg or config(), self.gitlab, self.buzz, state_dir=Path(self.tmp.name)).run()

    def notices(self):
        return [content for reply_to, content, _ in self.buzz.writes
                if reply_to is None and SYNC.matches_trigger_prefix(
                    content, "[gitlab-notify:v1][object:sync][event:stalled]")]


class FailedCursorRecheckTest(Case):
    def test_failed_object_is_reread_every_round_and_recovers(self):
        """L1-GIS-041 (ADR-0009) 坏对象只 stall 自己：cache 写入并带 stalled，下轮仍被重读，修复后清出 stalled。"""
        self.gitlab.issue_list[PID] = [make_issue(183)]
        self.gitlab.issue_notes[183] = [binding_note(1, "issue", 183, "1" * 64), binding_note(2, "issue", 183, "2" * 64)]
        first = self.run_sync()
        self.assertEqual([(s["object"], s["iid"]) for s in first["stalled"]], [("issue", 183)])
        cache = json.loads(next(Path(self.tmp.name).glob("*.cache.json")).read_text(encoding="utf-8"))
        self.assertEqual(cache["stalled"], [{"project": PID, "object": "issue", "iid": 183}])
        self.assertEqual(len(self.notices()), 1)

        self.gitlab.server_time = "2026-09-13T05:00:00Z"
        second = self.run_sync()
        self.assertEqual([(s["object"], s["iid"]) for s in second["stalled"]], [("issue", 183)])
        self.assertEqual(len(self.notices()), 1)  # same object, same reason, same UTC day: no second notice

        self.gitlab.issue_notes[183] = []
        self.gitlab.server_time = "2026-09-14T02:00:00Z"
        recovered = self.run_sync()
        self.assertEqual((recovered["created"], recovered["status"]), (1, "ok"))
        cache = json.loads(next(Path(self.tmp.name).glob("*.cache.json")).read_text(encoding="utf-8"))
        self.assertEqual(cache["stalled"], [])

    def test_failed_object_deleted_before_retry_leaves_the_stalled_list(self):
        """L1-GIS-041 (ADR-0009) 坏对象在重试前已被删除（GitLab 404）：跳过它，stalled 清空，本轮 status ok。"""
        self.gitlab.issue_list[PID] = [make_issue(183)]
        self.gitlab.issue_notes[183] = [binding_note(1, "issue", 183, "1" * 64), binding_note(2, "issue", 183, "2" * 64)]
        self.run_sync()
        self.gitlab.issue_list[PID] = []
        self.gitlab.missing_objects = {183}
        self.gitlab.server_time = "2026-09-13T05:00:00Z"
        summary = self.run_sync()
        self.assertEqual(summary["status"], "ok")
        self.assertEqual(summary["stalled"], [])
        cache = json.loads(next(Path(self.tmp.name).glob("*.cache.json")).read_text(encoding="utf-8"))
        self.assertEqual(cache["stalled"], [])

    def test_not_found_root_stalls_only_that_issue(self):
        """L1-GIS-041 (ADR-0009) 绑定 root 不可读（CLI 退出码 1）只停该 issue，后续对象照常；修复后下轮补做。"""
        self.gitlab.issue_list[PID] = [make_issue(183), make_issue(184), make_issue(185)]
        self.gitlab.issue_notes[183] = [binding_note(1, "issue", 183, "3" * 64)]
        missing_root = "3" * 64
        original_thread = self.buzz.thread

        def thread(root):
            if root == missing_root:
                raise SYNC.BuzzCliError("messages thread", 1)
            return original_thread(root)

        self.buzz.thread = thread
        first = self.run_sync()
        self.assertEqual([(s["object"], s["iid"]) for s in first["stalled"]], [("issue", 183)])
        self.assertEqual(first["created"], 2)  # 184 and 185 were not held back by 183
        self.assertFalse(any(reply_to == missing_root for reply_to, _, _ in self.buzz.writes))
        self.gitlab.issue_notes[183] = []
        self.buzz.thread = original_thread
        self.gitlab.server_time = "2026-09-14T02:00:00Z"
        second = self.run_sync()
        self.assertEqual(second["created"], 1)
        self.assertEqual(second["stalled"], [])


class MentionEdgeTest(Case):
    def test_closed_or_merged_mr_mentions_nobody(self):
        """L1-GIS-040 第一次看到时已合并或已关闭的 MR：root 不 @ 任何人。"""
        self.assertEqual(SYNC.mr_mentions(None, SYNC.mr_fact(make_mr(31, state="merged"), PID), [ALICE], {}), [])
        self.gitlab.members_list = [{"username": "alice", "access_level": 40}]
        self.gitlab.mr_list[PID] = [make_mr(31, state="merged"), make_mr(32, state="closed")]
        self.run_sync(config(people={"alice": ALICE}))
        self.assertEqual([mentions for _, _, mentions in self.buzz.writes if mentions], [])

    def test_reopened_mr_mentions_the_full_set(self):
        """L1-GIS-040 第一次看到时已关闭、之后重开的 MR：重开即变为可评审，lifecycle 回帖 @ 全集。"""
        closed = SYNC.mr_fact(make_mr(31, state="closed"), PID)
        self.assertEqual(SYNC.mr_mentions(closed, SYNC.mr_fact(make_mr(31), PID), [ALICE], {}), [ALICE])
        self.gitlab.members_list = [{"username": "alice", "access_level": 40}]
        self.gitlab.mr_list[PID] = [make_mr(31, state="closed")]
        self.run_sync(config(people={"alice": ALICE}))
        self.gitlab.mr_list[PID] = [make_mr(31, updated_at="2026-09-13T02:00:00Z")]
        self.run_sync(config(people={"alice": ALICE}, since="2026-09-13T00:30:00Z"))
        reply_to, content, mentions = self.buzz.writes[-1]
        self.assertIsNotNone(reply_to)
        self.assertEqual(SYNC.parse_header(content)["change"], "lifecycle")
        self.assertEqual(mentions, (ALICE,))

    def test_bot_role_members_are_never_mentioned(self):
        """L1-GIS-046 频道里角色为 bot 的成员（Agent）即使被 people 映射也不 @，列进 unmapped。"""
        self.gitlab.members_list = [{"username": "alice", "access_level": 40}, {"username": "bob", "access_level": 40}]
        self.buzz.members = {ALICE: "member", BOB: "bot"}
        self.gitlab.mr_list[PID] = [make_mr(31)]
        self.run_sync(config(people={"alice": ALICE, "bob": BOB}))
        _, content, mentions = self.buzz.writes[1]  # first fact; the plaque carries no mentions
        self.assertEqual(mentions, (ALICE,))
        self.assertIn("unmapped: bob(not_human_member)", content.split("\n"))


class DedupeScopeTest(Case):
    def test_thread_replies_do_not_suppress_top_level_notices(self):
        """L1-GIS-043 顶层去重只看 Desk 的顶层消息；Thread 回帖里同名的 events 行不会压掉顶层通知。
        digest 输入用合成记录（live push 已不产生记录——2026-09-18 政策）。"""
        from unittest import mock

        self.buzz.add_event("[gitlab-notify:v1][object:mr][state:opened][draft:no][change:activity]"
                            "[transition:none][project:481][mr:31]\n"
                            "events: event-1", reply_to="f" * 64)
        self.gitlab.event_list = [FAKES.push_event(1)]

        def synthetic_push_digest(event, project_id, web_url):
            push = event.get("push_data")
            if not isinstance(push, dict):
                return None
            raw_ref = SYNC._single_line(push.get("ref") or "")
            return SYNC._record(f"event-{event['id']}", "push", "pushed", "digest", project_id,
                                url=f"{web_url}/-/commits/{raw_ref}",
                                created_at=event.get("created_at"),
                                actor=(event.get("author") or {}).get("username") or "?",
                                ref=SYNC.neutralize(raw_ref), commits=push.get("commit_count") or 0)

        with mock.patch.object(SYNC, "record_from_event", side_effect=synthetic_push_digest):
            summary = self.run_sync()
        self.assertEqual(summary["notified"]["milestone"], 0)
        self.assertEqual(len(summary["summary_requests"]), 1)
        self.assertEqual(summary["summary_requests"][0]["facts"][0]["object"], "push")


class ExcludedGroupTest(Case):
    def test_exclusion_checked_before_binding_and_counted_once(self):
        """L1-GIS-048 exclude 的 MR：先判 exclude 再读 binding/Thread（坏 binding 不会让它 stall），同一轮只计一次 excluded。"""
        rule = config(exclude=[{"label": "no-sync"}])
        self.gitlab.mr_note_list[31] = [binding_note(1, "mr", 31, "1" * 64), binding_note(2, "mr", 31, "2" * 64)]
        self.gitlab.fresh_mr[31] = make_mr(31, labels=["no-sync"])
        self.gitlab.pipeline_list = [FAKES.mr_pipeline()]
        summary = self.run_sync(rule)
        self.assertEqual((summary["stalled"], summary["skipped"]["excluded"], self.buzz.writes), ([], 1, []))

        self.gitlab.mr_list[PID] = [make_mr(31, labels=["no-sync"])]
        summary = SYNC.Syncer(config(exclude=[{"label": "no-sync"}], since="2026-09-13T00:30:00Z"), self.gitlab,
                              self.buzz, state_dir=Path(self.tmp.name)).run()
        self.assertEqual(summary["skipped"]["excluded"], 1)


class LabelDashTest(Case):
    def test_label_named_dash_does_not_repost(self):
        """L1-GIS-027 GitLab label 名就是「-」时渲染为「－」，重跑不会被当成 label 变化反复发 activity。"""
        issue = make_issue(183, labels=["-", "type::feature", "status::ready"])
        self.assertEqual(SYNC.issue_fact(issue, PID)["labels"], ["－"])
        self.gitlab.issue_list[PID] = [issue]
        self.run_sync()
        count = len(self.buzz.writes)
        self.run_sync(config(since="2026-09-13T00:30:00Z"))
        self.assertEqual(len(self.buzz.writes), count)


class LockScopeTest(unittest.TestCase):
    def test_lock_and_cache_keyed_by_channel_and_projects(self):
        """L1-GIS-016 锁与缓存按「频道 + 项目集合」区分：同频道一个 token 一份配置可以并存，项目顺序不影响。"""
        state = Path("/tmp/state")
        one, other, reordered = config(), config(), config()
        other["gitlab"]["projects"] = [482]
        one["gitlab"]["projects"], reordered["gitlab"]["projects"] = [481, 482], [482, 481]
        self.assertNotEqual(SYNC.lock_path(one, state), SYNC.lock_path(other, state))
        self.assertEqual(SYNC.lock_path(one, state), SYNC.lock_path(reordered, state))


if __name__ == "__main__":
    unittest.main()
