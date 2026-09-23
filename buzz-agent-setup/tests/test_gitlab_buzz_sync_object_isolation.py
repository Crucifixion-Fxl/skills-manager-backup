"""Object-level isolation of the GitLab -> Buzz sync (engineering/skills#103, ADR-0009).

A data problem that belongs to exactly one Issue or MR (an unusable origin, a bound root that was deleted or is
not a Desk plaque, a broken binding, an unreadable object) must stall only that object: no write for it, a
`stalled` entry in the result and cache, one notice per object and reason per UTC day, and every other object
keeps syncing. Transport, authentication, identity, configuration and uncertain-delivery errors still stop the
whole run.
"""

import json
import unittest
from pathlib import Path

try:
    from .test_gitlab_buzz_sync_hardening import (  # noqa: F401
        BOT_ID, CHANNEL, DESK, PID, SYNC, WEB, FakeBuzz, SyncCase, make_issue, make_mr,
    )
except ImportError:  # imported as a top-level module
    from test_gitlab_buzz_sync_hardening import (  # noqa: F401
        BOT_ID, CHANNEL, DESK, PID, SYNC, WEB, FakeBuzz, SyncCase, make_issue, make_mr,
    )

HUMAN_ROOT = "b" * 64
# The one origin that still stalls its object: a marker that is not JSON. A well-formed marker to a human's
# top-level message binds (ADR-0014); one to an unusable root falls back to the object's own plaque.
BAD_ORIGIN = "请从这里接手\n<!-- gitlab-buzz-origin:v1 {not json} -->"


def origin_text(root=HUMAN_ROOT, channel=CHANNEL):
    return ('请从这里接手\n<!-- gitlab-buzz-origin:v1 '
            f'{{"channel_id":"{channel}","root_event_id":"{root}"}} -->')


class FailingThreadBuzz(FakeBuzz):
    """Reading one named root fails the way the Buzz CLI does: exit 1 is not-found, 2+ is relay/auth."""

    def __init__(self, failing_root, returncode):
        super().__init__()
        self.failing_root, self.returncode = failing_root, returncode

    def thread(self, root):
        if root == self.failing_root:
            raise SYNC.BuzzCliError("messages thread", self.returncode)
        return super().thread(root)


class IsolationCase(SyncCase):
    def seed_human_root(self):
        self.buzz.events.append({"id": HUMAN_ROOT, "pubkey": "a1" * 32, "kind": 9, "created_at": 5,
                                 "tags": [["h", CHANNEL]], "content": "有什么办法做 skill 瘦身？"})

    def bind(self, iid, root, kind="issue"):
        note = SYNC.render_binding_note(
            {"project_id": PID, "object": kind, "iid": iid, "channel_id": CHANNEL, "root_event_id": root},
            SYNC.buzz_message_link(CHANNEL, root),
        )
        target = self.gitlab.issue_notes if kind == "issue" else self.gitlab.mr_note_list
        target.setdefault(iid, []).append(
            {"id": 900 + iid, "author": {"id": BOT_ID}, "body": note, "system": False,
             "created_at": "2026-09-13T02:00:00Z"})

    def roots(self):
        return [e for e in self.buzz.events if not any(t[0] == "e" for t in e["tags"])]

    def stalled_notices(self):
        return [content for _, content, _ in self.buzz.writes if "[event:stalled]" in content]

    def cache(self):
        return json.loads(next(Path(self.tmp.name).glob("*.cache.json")).read_text(encoding="utf-8"))


class BadDataStallsOnlyItsObjectTest(IsolationCase):
    def test_a_human_origin_root_no_longer_stalls_that_issue(self):
        """L1-GIS-ISO-001 复现 skill 频道（#125）：origin 指向人类顶层消息的 issue 不再停摆（ADR-0014），绑到该话题；其余 issue 照常开门牌。"""
        self.seed_human_root()
        self.gitlab.issue_list[PID] = [make_issue(81, description=origin_text()), make_issue(82)]
        result = self.run_sync()
        self.assertEqual((result["status"], result["stalled"]), ("ok", []))
        plaques = [e["content"] for e in self.roots() if e["id"] != HUMAN_ROOT and "📋" in e["content"]]
        self.assertTrue(any("#82" in text for text in plaques))
        self.assertFalse(any("#81" in text for text in plaques))
        self.assertEqual(SYNC.parse_binding(self.gitlab.issue_notes[81], BOT_ID, PID, "issue", 81, CHANNEL), HUMAN_ROOT)

    def test_an_mr_with_an_unreadable_origin_root_falls_back_to_its_own_plaque(self):
        """L1-GIS-ISO-002 MR 的 origin 读不到：不停摆（ADR-0014），MR 自开门牌并记 origin_fallbacks，issue 照常。"""
        self.gitlab.mr_list[PID] = [make_mr(897, source_branch="feature/x", description=origin_text(root="c" * 64))]
        self.gitlab.issue_list[PID] = [make_issue(83)]
        result = self.run_sync()
        self.assertEqual(result["stalled"], [])
        self.assertEqual([(f["object"], f["iid"]) for f in result["origin_fallbacks"]], [("mr", 897)])
        self.assertIn("mr 897", result["origin_fallbacks"][0]["reason"])
        self.assertTrue(any("#83" in e["content"] for e in self.roots()))
        self.assertTrue(any("!897" in e["content"] for e in self.roots()))

    def test_a_deleted_bound_root_stalls_that_issue_only(self):
        """L1-GIS-ISO-003 复现 buzz-deploy #56：binding 指向已被删的根，只停这个 issue。"""
        self.bind(56, "d" * 64)
        self.gitlab.issue_list[PID] = [make_issue(56), make_issue(57)]
        result = self.run_sync()
        self.assertEqual([(s["object"], s["iid"]) for s in result["stalled"]], [("issue", 56)])
        self.assertTrue(any("#57" in e["content"] for e in self.roots()))
        self.assertFalse(any("#56" in e["content"] for e in self.buzz.events))

    def test_a_root_the_cli_reports_as_not_found_stalls_that_issue_only(self):
        """L1-GIS-ISO-004 Buzz CLI 对该根退出码 1（不存在/非本频道）按对象级数据问题处理。"""
        self.buzz = FailingThreadBuzz("e" * 64, returncode=1)
        self.bind(58, "e" * 64)
        self.gitlab.issue_list[PID] = [make_issue(58), make_issue(59)]
        result = self.run_sync()
        self.assertEqual([(s["object"], s["iid"]) for s in result["stalled"]], [("issue", 58)])
        self.assertTrue(any("#59" in e["content"] for e in self.roots()))

    def test_malformed_origin_json_stalls_that_issue_only(self):
        """L1-GIS-ISO-005 origin 注释不是合法 JSON：只停这个 issue。"""
        self.gitlab.issue_list[PID] = [
            make_issue(60, description="x\n<!-- gitlab-buzz-origin:v1 {not json} -->"), make_issue(61)]
        result = self.run_sync()
        self.assertEqual([(s["object"], s["iid"]) for s in result["stalled"]], [("issue", 60)])
        self.assertTrue(any("#61" in e["content"] for e in self.roots()))

    def test_an_origin_for_another_channel_falls_back_to_its_own_plaque(self):
        """L1-GIS-ISO-006 origin 的 channel_id 不是本频道：不停摆（ADR-0014），自开门牌并记 origin_fallbacks。"""
        self.gitlab.issue_list[PID] = [
            make_issue(62, description=origin_text(channel="00000000-0000-4000-8000-0000000000c9")),
            make_issue(63)]
        result = self.run_sync()
        self.assertEqual(result["stalled"], [])
        self.assertEqual([(f["object"], f["iid"]) for f in result["origin_fallbacks"]], [("issue", 62)])
        self.assertTrue(any("#62" in e["content"] for e in self.roots()))
        self.assertTrue(any("#63" in e["content"] for e in self.roots()))


class WholeRunFailuresStayWholeRunTest(IsolationCase):
    def test_a_relay_failure_reading_the_bound_root_still_stops_the_run(self):
        """L1-GIS-ISO-007 读绑定根时 CLI 退出码 2+（relay/认证失败）不是数据问题：整轮失败，不写缓存。"""
        self.buzz = FailingThreadBuzz("f" * 64, returncode=2)
        self.bind(64, "f" * 64)
        self.gitlab.issue_list[PID] = [make_issue(64), make_issue(65)]
        with self.assertRaises(SYNC.BuzzCliError):
            self.run_sync()
        self.assertEqual(list(Path(self.tmp.name).glob("*.cache.json")), [])

    def test_a_relay_failure_reading_an_origin_root_still_stops_the_run(self):
        """L1-GIS-ISO-008 读 origin 根时 CLI 退出码 2+：整轮失败，而不是被当成坏 origin 跳过。"""
        self.buzz = FailingThreadBuzz(HUMAN_ROOT, returncode=2)
        self.gitlab.issue_list[PID] = [make_issue(66, description=origin_text()), make_issue(67)]
        with self.assertRaises(SYNC.BuzzCliError):
            self.run_sync()
        self.assertEqual(list(Path(self.tmp.name).glob("*.cache.json")), [])

    def test_gitlab_permission_errors_still_stop_the_run(self):
        """L1-GIS-ISO-009 GitLab 读评论 403 仍整轮失败。"""
        self.gitlab.issue_list[PID] = [make_issue(68)]

        def forbidden(project_id, iid):
            raise SYNC.GitLabHTTPError("GET", f"projects/{project_id}/issues/{iid}/notes", 403)

        self.gitlab.notes = forbidden
        with self.assertRaises(SYNC.GitLabHTTPError):
            self.run_sync()

    def test_an_event_group_isolates_object_errors_but_not_other_failures(self):
        """L1-GIS-ISO-010 事件驱动分组（MR 分组、milestone）遇对象级错误按 ADR-0009 隔离（#105）；其它 SyncError 仍整轮停止。"""
        syncer = SYNC.Syncer(_hardening_config(), self.gitlab, self.buzz, state_dir=Path(self.tmp.name))
        syncer._scan_date = "2026-09-13"
        summary = {"stalled": []}

        def bad(*args):
            raise SYNC.ObjectError("boom")

        def transport(*args):
            raise SYNC.SyncError("relay down")

        syncer._run_group(summary, PID, "milestone", 9, [{"key": "event-1"}], bad, False)
        self.assertEqual([(s["object"], s["iid"]) for s in summary["stalled"]], [("milestone", 9)])
        with self.assertRaises(SYNC.SyncError):
            syncer._run_group(summary, PID, "milestone", 10, [{"key": "event-2"}], transport, False)


def _hardening_config():
    try:
        from .test_gitlab_buzz_sync_hardening import config
    except ImportError:
        from test_gitlab_buzz_sync_hardening import config
    return config()


class StalledStateTest(IsolationCase):
    def setUp(self):
        super().setUp()
        self.seed_human_root()
        self.gitlab.issue_list[PID] = [make_issue(81, description=BAD_ORIGIN), make_issue(82)]

    def test_the_cursor_advances_and_the_stalled_object_is_persisted_without_its_reason(self):
        """L1-GIS-ISO-011 cursor 照常推进；stalled 只落 project/object/iid，不落原因文本。"""
        self.run_sync()
        cache = self.cache()
        self.assertEqual(cache["stalled"], [{"project": PID, "object": "issue", "iid": 81}])
        self.assertIn("cursor", cache)

    def test_a_stalled_object_is_retried_next_round_even_if_gitlab_did_not_update_it(self):
        """L1-GIS-ISO-012 下一轮即使 issue 没有再更新也会重试：修好数据后自动补开门牌，stalled 清空。"""
        self.run_sync()
        fixed = make_issue(81, description="")
        self.gitlab.issue_list[PID] = [fixed, make_issue(82)]
        listed = self.gitlab.issues
        self.gitlab.issues = lambda project_id, updated_after: []  # nothing was updated since the cursor
        result = self.run_sync()
        self.gitlab.issues = listed
        self.assertEqual(result["stalled"], [])
        self.assertEqual(result["status"], "ok")
        self.assertTrue(any("#81" in e["content"] for e in self.roots()))
        self.assertEqual(self.cache()["stalled"], [])

    def test_a_still_broken_object_is_retried_and_stays_stalled(self):
        """L1-GIS-ISO-013 数据没修好则每轮重试、继续 stalled，且不产生任何写入。"""
        self.run_sync()
        writes = len(self.buzz.writes)
        self.gitlab.issues = lambda project_id, updated_after: []
        result = self.run_sync()
        self.assertEqual([(s["object"], s["iid"]) for s in result["stalled"]], [("issue", 81)])
        self.assertEqual(self.buzz.writes[writes:], [])

    def test_dry_run_reports_the_stall_but_writes_nothing(self):
        """L1-GIS-ISO-014 --dry-run：summary 里有 stalled（如绑定根已被删），不发通知、不写缓存。"""
        self.bind(56, "d" * 64)
        self.gitlab.issue_list[PID] = [make_issue(56), make_issue(57)]
        result = SYNC.Syncer(_hardening_config(), self.gitlab, self.buzz, state_dir=Path(self.tmp.name)).run(
            dry_run=True)
        self.assertEqual([(s["object"], s["iid"]) for s in result["stalled"]], [("issue", 56)])
        self.assertEqual(self.buzz.writes, [])
        self.assertEqual(self.gitlab.writes, [])
        self.assertEqual(list(Path(self.tmp.name).glob("*.cache.json")), [])

    def test_a_healthy_run_reports_no_stalled_objects(self):
        """L1-GIS-ISO-015 对照：没有坏对象时 status ok、stalled 为空、没有 stalled 通知。"""
        self.gitlab.issue_list[PID] = [make_issue(82)]
        result = self.run_sync()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["stalled"], [])
        self.assertEqual(self.stalled_notices(), [])


class DailyNoticeTest(IsolationCase):
    def setUp(self):
        super().setUp()
        self.seed_human_root()
        self.gitlab.issue_list[PID] = [make_issue(81, description=BAD_ORIGIN), make_issue(82)]

    def test_one_notice_per_object_and_reason_per_day(self):
        """L1-GIS-ISO-016 同一对象同一原因同一 UTC 日只发一条频道通知，重复轮次不刷屏。"""
        self.run_sync()
        self.gitlab.issues = lambda project_id, updated_after: []
        self.run_sync()
        self.run_sync()
        notices = self.stalled_notices()
        self.assertEqual(len(notices), 1)
        self.assertIn("issue 81", notices[0])
        self.assertIn("[object:sync][event:stalled]", notices[0])

    def test_the_notice_repeats_on_the_next_utc_day(self):
        """L1-GIS-ISO-017 次日仍未修好则再提醒一条。"""
        self.run_sync()
        self.gitlab.issues = lambda project_id, updated_after: []
        self.gitlab.scan_time = lambda: "2026-09-14T04:00:00Z"
        self.run_sync()
        self.assertEqual(len(self.stalled_notices()), 2)

    def test_the_notice_names_the_object_and_carries_no_mention_or_nostr_uri(self):
        """L1-GIS-ISO-018 通知带对象编号和原因；GitLab 来源文本里的 @ 和 nostr: 已被中和，不产生 p tag。"""
        self.gitlab.issue_list[PID] = [make_issue(
            81, title="hi @alice nostr:npub1abc", description=BAD_ORIGIN)]
        self.run_sync()
        notice = self.stalled_notices()[0]
        self.assertIn("issue 81", notice)
        self.assertNotIn("@", notice)
        self.assertNotIn("nostr:", notice)
        stalled_writes = [w for w in self.buzz.writes if "[event:stalled]" in w[1]]
        self.assertEqual(stalled_writes[0][2], ())
        self.assertIsNone(stalled_writes[0][0])

    def test_a_token_shaped_string_in_the_reason_never_reaches_the_channel_or_the_result(self):
        """L1-GIS-ISO-019 原因文本会发到频道：像密钥的长串（token、私钥、event id 之外的长 base64）一律打码，不进通知也不进结果。"""
        secret = "glpat-AbCdEfGhIjKlMnOpQrStUv"
        syncer = SYNC.Syncer(_hardening_config(), self.gitlab, self.buzz, state_dir=Path(self.tmp.name))

        def leaky(*args):
            raise SYNC.ObjectError(f"issue 90: upstream said {secret} and a path /home/x")

        syncer._sync_issue = leaky
        self.gitlab.issue_list[PID] = [make_issue(90)]
        result = syncer.run()
        self.assertNotIn(secret, json.dumps(result))
        self.assertTrue(self.stalled_notices())
        self.assertFalse(any(secret in content for _, content, _ in self.buzz.writes))
        self.assertIn("issue 90", result["stalled"][0]["reason"])


if __name__ == "__main__":
    unittest.main()
