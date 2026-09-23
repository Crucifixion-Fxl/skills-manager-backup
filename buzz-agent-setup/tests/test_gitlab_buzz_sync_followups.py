"""Follow-ups to object-level isolation and root recovery (engineering/skills#105 and #106; ADR-0010, ADR-0011).

A. Relay 0.2.1 full-text search finds nothing for a full URL, but finds its `/-/…` path tail: the adapter must
   send the tail and keep only events whose content holds the exact URL (#106).
B. A bare `buzz://message?…` link is a hint, not a machine marker: placeholder prose and links that do not lead
   to a Desk plaque are ignored instead of stalling the object; the HTML marker stays strict (ADR-0011).
C. MR-group and milestone groups are derived from events the cursor moves past, so a group that stalls keeps its
   records in the cache and is retried until the data is fixed (ADR-0009 follow-up, #105).
D. `--dry-run` validates a new object's origin too, so a preview shows what would stall (#105).
E. A stalled notice tags the Channel owner, once per object and reason per UTC day (ADR-0010, #105).
"""

import json
import unittest
from pathlib import Path
from unittest import mock

try:
    from .test_gitlab_buzz_sync_hardening import (  # noqa: F401
        ALICE, AGENT, BOT_ID, CHANNEL, DESK, PID, SYNC, WEB, FakeBuzz, FakeGitLab, SyncCase, config, make_issue,
        make_mr,
    )
except ImportError:  # imported as a top-level module
    from test_gitlab_buzz_sync_hardening import (  # noqa: F401
        ALICE, AGENT, BOT_ID, CHANNEL, DESK, PID, SYNC, WEB, FakeBuzz, FakeGitLab, SyncCase, config, make_issue,
        make_mr,
    )

HUMAN_ROOT = "b" * 64
OWNER = "0a" * 32


def marker(root=HUMAN_ROOT, channel=CHANNEL):
    return ('<!-- gitlab-buzz-origin:v1 '
            f'{{"channel_id":"{channel}","root_event_id":"{root}"}} -->')


# A marker that is not JSON is the one origin that still stalls its object (ADR-0009); a well-formed marker to a
# human's top-level message binds (ADR-0014), and one to an unusable root falls back.
BAD_MARKER = "<!-- gitlab-buzz-origin:v1 {not json} -->"


def bare_link(root=HUMAN_ROOT, channel=CHANNEL):
    return f"buzz://message?channel={channel}&id={root}&thread={root}"


class FailingThreadBuzz(FakeBuzz):
    def __init__(self, failing_root, returncode):
        super().__init__()
        self.failing_root, self.returncode = failing_root, returncode

    def thread(self, root):
        if root == self.failing_root:
            raise SYNC.BuzzCliError("messages thread", self.returncode)
        return super().thread(root)


class OwnerBuzz(FakeBuzz):
    def __init__(self, roles):
        super().__init__()
        self.roles = roles

    def channel_members(self):
        return dict(self.roles)


class MilestoneGitLab(FakeGitLab):
    def __init__(self):
        super().__init__()
        self.milestone_list = []

    def milestones(self, project_id):
        return [dict(item) for item in self.milestone_list]


def milestone_event(event_id, action, iid, title="2026-Q4"):
    return {"id": event_id, "action_name": action, "target_type": "Milestone", "target_iid": iid,
            "target_title": title, "created_at": "2026-09-13T02:00:00.000Z", "author": {"username": "alice"}}


class FollowupCase(SyncCase):
    def seed_human_root(self):
        self.buzz.events.append({"id": HUMAN_ROOT, "pubkey": "a1" * 32, "kind": 9, "created_at": 5,
                                 "tags": [["h", CHANNEL]], "content": "有什么办法做 skill 瘦身？"})

    def roots(self):
        return [e for e in self.buzz.events if not any(t[0] == "e" for t in e["tags"])]

    def plaque_texts(self):
        return [e["content"] for e in self.roots() if e["id"] != HUMAN_ROOT and "📋" in e["content"]]

    def stalled_notices(self):
        return [(mentions, content) for _, content, mentions in self.buzz.writes if "[event:stalled]" in content]

    def cache(self):
        return json.loads(next(Path(self.tmp.name).glob("*.cache.json")).read_text(encoding="utf-8"))


# ── A. relay search uses the path tail ───────────────────────────────────────


class RelayTailSearchTest(unittest.TestCase):
    def cli(self, hits):
        buzz = object.__new__(SYNC.BuzzCli)
        buzz.publisher, buzz.channel = DESK, CHANNEL
        buzz.calls = []

        def command(args, content=None):
            buzz.calls.append(args)
            return hits

        buzz.command = command
        return buzz

    @staticmethod
    def root(event_id, url):
        return {"id": event_id, "pubkey": DESK, "kind": 9, "tags": [["h", CHANNEL]],
                "content": f"📋 **#818 x**\nbuzz-sync-test/pilot\n{url}"}

    def query(self, buzz):
        return buzz.calls[-1][buzz.calls[-1].index("--query") + 1]

    def test_the_relay_query_is_the_path_tail_and_results_keep_the_exact_url_only(self):
        """L1-GIS-FU-001 relay 0.2.1 搜不到完整 URL：发路径尾 `/-/issues/N`，只留内容含精确 URL 的事件（#106）。"""
        mine = self.root("1" * 64, f"{WEB}/-/issues/818")
        other = self.root("2" * 64, "http://127.0.0.1:8929/other/proj/-/issues/818")
        buzz = self.cli([mine, other])
        found = buzz.search_roots(f"{WEB}/-/issues/818", "issue", 818, 5000)
        self.assertEqual(self.query(buzz), "/-/issues/818")
        self.assertEqual([event["id"] for event in found], ["1" * 64])

    def test_merge_request_milestone_and_work_item_urls_use_their_own_tails(self):
        """L1-GIS-FU-002 MR、milestone 与旧 work_items 门牌 URL 同样按路径尾搜索。"""
        for path in ("/-/merge_requests/9", "/-/milestones/4", "/-/work_items/818"):
            buzz = self.cli([])
            with self.subTest(path=path):
                buzz.search_roots(f"{WEB}{path}", "issue", 1, 0)
                self.assertEqual(self.query(buzz), path)

    def test_a_query_that_is_not_a_url_is_sent_unchanged(self):
        """L1-GIS-FU-003 没有 `/-/` 路径尾的查询原样发给 relay，不做过滤。"""
        event = self.root("3" * 64, f"{WEB}/-/issues/1")
        buzz = self.cli([event])
        found = buzz.search_roots("plain words", "issue", 0, 0)
        self.assertEqual(self.query(buzz), "plain words")
        self.assertEqual(found, [event])

    def test_the_result_cap_still_refuses_an_ambiguous_recovery(self):
        """L1-GIS-FU-004 relay 原始命中数到上限就拒绝恢复（先于精确过滤），保持首错停对象。"""
        hits = [self.root(f"{i:064x}", "http://127.0.0.1:8929/other/proj/-/issues/818")
                for i in range(SYNC.SEARCH_LIMIT)]
        with self.assertRaises(SYNC.ObjectError):
            self.cli(hits).search_roots(f"{WEB}/-/issues/818", "issue", 818, 5000)


# ── B. bare deep links are hints ─────────────────────────────────────────────


class BareLinkHintTest(FollowupCase):
    def stalled(self, result):
        return [(s["object"], s["iid"]) for s in result["stalled"]]

    def test_prose_that_shows_the_link_syntax_is_not_an_origin(self):
        """L1-GIS-FU-010 正文里讲 buzz://message?… 语法（省略号、占位符）不是 origin：不停摆，issue 照常开门牌（#105 第 4 项）。"""
        text = ("裸 `buzz://message?…` 链接也会被解析\n"
                "格式 buzz://message?channel=<UUID>&id=<root>&thread=<root>")
        self.gitlab.issue_list[PID] = [make_issue(101, description=text)]
        result = self.run_sync()
        self.assertEqual(result["stalled"], [])
        self.assertEqual(result["status"], "ok")
        self.assertTrue(any("#101" in text for text in self.plaque_texts()))

    def test_a_well_formed_link_to_a_human_message_is_ignored(self):
        """L1-GIS-FU-011 人类随手贴的 buzz 深链指向人类消息：忽略，不停摆，issue 自开门牌。"""
        self.seed_human_root()
        self.gitlab.issue_list[PID] = [make_issue(81, description=f"讨论见 {bare_link()}")]
        result = self.run_sync()
        self.assertEqual(result["stalled"], [])
        self.assertTrue(any("#81" in text for text in self.plaque_texts()))

    def test_a_bare_link_the_cli_cannot_find_is_ignored(self):
        """L1-GIS-FU-012 深链指向的根 CLI 退出码 1（不存在）：当作普通引用忽略。"""
        self.buzz = FailingThreadBuzz("c" * 64, returncode=1)
        self.gitlab.issue_list[PID] = [make_issue(82, description=bare_link(root="c" * 64))]
        result = self.run_sync()
        self.assertEqual(result["stalled"], [])
        self.assertTrue(any("#82" in text for text in self.plaque_texts()))

    def test_a_bare_link_to_a_desk_plaque_still_binds_the_issue(self):
        """L1-GIS-FU-013 兼容：深链指向本频道 Desk 门牌仍是 origin，issue 绑到该 Thread、不另开门牌。"""
        plaque = self.buzz.send(f"📋 **#14 weekly**\nbuzz-sync-test/pilot\n{WEB}/-/issues/14")
        self.gitlab.issue_list[PID] = [make_issue(83, description=f"接上 {bare_link(root=plaque)}")]
        result = self.run_sync()
        self.assertEqual(result["stalled"], [])
        self.assertEqual(len(self.plaque_texts()), 1)  # only the pre-existing plaque; no new root for #83
        replies = [e for e in self.buzz.thread(plaque) if e["id"] != plaque]
        self.assertTrue(any("Issue 83" in e["content"] for e in replies))

    def test_the_machine_marker_stays_strict(self):
        """L1-GIS-FU-014 HTML 机器注释的格式仍严格：不是 JSON 的注释让该 issue 停摆（指向人类顶层消息的合法注释见 ADR-0014）。"""
        self.seed_human_root()
        self.gitlab.issue_list[PID] = [make_issue(84, description=BAD_MARKER), make_issue(85)]
        result = self.run_sync()
        self.assertEqual(self.stalled(result), [("issue", 84)])
        self.assertTrue(any("#85" in text for text in self.plaque_texts()))

    def test_a_relay_failure_reading_a_bare_link_root_still_stops_the_run(self):
        """L1-GIS-FU-015 读深链根时 CLI 退出码 2+（relay/认证）不是数据问题：整轮失败。"""
        self.buzz = FailingThreadBuzz("d" * 64, returncode=2)
        self.gitlab.issue_list[PID] = [make_issue(86, description=bare_link(root="d" * 64))]
        with self.assertRaises(SYNC.BuzzCliError):
            self.run_sync()


# ── C. event-driven groups are isolated ──────────────────────────────────────


class EventGroupIsolationTest(FollowupCase):
    def setUp(self):
        super().setUp()
        self.gitlab = MilestoneGitLab()

    def bad_milestone(self, description):
        self.gitlab.milestone_list = [{"iid": 9, "title": "2026-Q4", "description": description}]
        self.gitlab.event_list = [milestone_event(5, "created", 9)]

    def test_a_milestone_with_a_bad_origin_stalls_only_that_milestone_and_keeps_its_events(self):
        """L1-GIS-FU-020 milestone 的坏 origin 只停该 milestone（不再整轮失败），其余照常；事件记录留在缓存等修复。"""
        self.seed_human_root()
        self.bad_milestone(BAD_MARKER)
        self.gitlab.issue_list[PID] = [make_issue(90)]
        result = self.run_sync()
        self.assertEqual([(s["object"], s["iid"]) for s in result["stalled"]], [("milestone", 9)])
        self.assertIn("milestone 9", result["stalled"][0]["reason"])
        self.assertEqual(result["status"], "degraded")
        self.assertTrue(any("#90" in text for text in self.plaque_texts()))
        self.assertFalse(any("2026-Q4" in e["content"] for e in self.buzz.events))  # zero writes for it
        groups = self.cache()["stalled_groups"]
        self.assertEqual([(g["project"], g["object"], g["iid"]) for g in groups], [(PID, "milestone", 9)])
        self.assertEqual(len(groups[0]["records"]), 1)

    def test_a_stalled_milestone_group_is_retried_from_the_cache_after_the_data_is_fixed(self):
        """L1-GIS-FU-021 修好 origin 后下一轮从缓存里的记录补发（GitLab 已不再返回该事件）。"""
        self.seed_human_root()
        self.bad_milestone(BAD_MARKER)
        self.run_sync()
        self.gitlab.milestone_list[0]["description"] = ""
        self.gitlab.event_list = []  # the cursor moved past the event; only the cache still has it
        result = self.run_sync()
        self.assertEqual(result["stalled"], [])
        self.assertEqual(result["notified"]["milestone"], 1)
        self.assertTrue(any("里程碑创建" in e["content"] for e in self.buzz.events))
        self.assertEqual(self.cache().get("stalled_groups", []), [])

    def test_a_mr_group_that_raises_an_object_error_is_stalled_and_retained(self):
        """L1-GIS-FU-022 MR 分组抛对象级错误：该 MR stalled、记录保留，其余 MR 分组继续。"""
        records = [{"key": "event-1", "mr_iid": 31, "placement": "mr_thread", "object": "mr"},
                   {"key": "event-2", "mr_iid": 32, "placement": "mr_thread", "object": "mr"}]
        syncer = SYNC.Syncer(config(), self.gitlab, self.buzz, state_dir=Path(self.tmp.name))
        syncer._stalled_objects, syncer._stalled_records, syncer._retained_groups = set(), {}, {}
        syncer._previous_groups = {}
        syncer._scan_date = "2026-09-13"
        syncer._projects = {PID: self.gitlab.projects[PID]}
        seen = []

        def group(project_id, mr_iid, group_records, summary, dry_run):
            seen.append(mr_iid)
            if mr_iid == 31:
                raise SYNC.ObjectError("mr 31 origin root cannot be read")

        summary = {"stalled": [], "links": []}
        with mock.patch.object(syncer, "_sync_mr_group", side_effect=group), \
                mock.patch.object(syncer, "_signal_degrade"), mock.patch.object(syncer, "_check_run_budget"):
            syncer._sync_mr_records(PID, records, summary, False)
        self.assertEqual(seen, [31, 32])
        self.assertEqual([(s["object"], s["iid"]) for s in summary["stalled"]], [("mr", 31)])
        self.assertEqual(list(syncer._retained_groups), [(PID, "mr", 31)])

    def test_a_malformed_stalled_groups_entry_is_dropped_and_the_cursor_survives(self):
        """L1-GIS-FU-024 缓存里坏的 stalled_groups 条目被丢弃，不信任也不让 cursor 失效。"""
        self.run_sync()
        path = next(Path(self.tmp.name).glob("*.cache.json"))
        cache = json.loads(path.read_text(encoding="utf-8"))
        cache["stalled_groups"] = [
            {"project": PID, "object": "milestone", "iid": 9, "records": [{"key": "event-1"}, "junk", {"nokey": 1}]},
            {"project": 999999, "object": "mr", "iid": 3, "records": [{"key": "x"}]},
            {"project": PID, "object": "issue", "iid": 4, "records": [{"key": "y"}]},
            {"project": PID, "object": "mr", "iid": 0, "records": [{"key": "z"}]},
            "junk",
        ]
        path.write_text(json.dumps(cache), encoding="utf-8")
        syncer = SYNC.Syncer(config(), self.gitlab, self.buzz, state_dir=Path(self.tmp.name))
        read = syncer.read_cache()
        self.assertEqual(read["cursor"], cache["cursor"])
        self.assertEqual(read["stalled_groups"], {(PID, "milestone", 9): [{"key": "event-1"}]})

    def test_a_stalled_group_keeps_only_its_newest_records(self):
        """L1-GIS-FU-025 每个分组最多留最新 SYNC.STALLED_GROUP_RECORD_LIMIT 条记录，缓存大小有界。"""
        limit = SYNC.STALLED_GROUP_RECORD_LIMIT
        records = [{"key": f"event-{i}"} for i in range(limit + 5)]
        syncer = SYNC.Syncer(config(), self.gitlab, self.buzz, state_dir=Path(self.tmp.name))
        syncer._stalled_objects, syncer._stalled_records, syncer._retained_groups = set(), {}, {}
        syncer._scan_date, syncer._projects = "2026-09-13", {PID: self.gitlab.projects[PID]}

        def bad(*args):
            raise SYNC.ObjectError("milestone 9 origin root cannot be read")

        with mock.patch.object(syncer, "_signal_degrade"), mock.patch.object(syncer, "_check_run_budget"):
            syncer._run_group({"stalled": []}, PID, "milestone", 9, records, bad, False)
        kept = syncer._retained_groups[(PID, "milestone", 9)]
        self.assertEqual(len(kept), limit)
        self.assertEqual(kept[-1]["key"], f"event-{limit + 4}")
        self.assertEqual(kept[0]["key"], "event-5")

    def test_a_relay_failure_in_an_event_group_still_stops_the_run(self):
        """L1-GIS-FU-023 事件驱动分组里的 relay 故障（退出码 2+）仍整轮失败，不当作对象级错误。"""
        self.buzz = FailingThreadBuzz(HUMAN_ROOT, returncode=2)
        self.seed_human_root()
        self.bad_milestone(bare_link())
        with self.assertRaises(SYNC.BuzzCliError):
            self.run_sync()


# ── D. dry-run validates a new object's origin ───────────────────────────────


class DryRunOriginTest(FollowupCase):
    def test_dry_run_reports_a_new_issue_whose_origin_would_stall(self):
        """L1-GIS-FU-030 dry-run 对新 issue 也校验 origin：预演里能看到会 stalled 的对象，且零写入。"""
        self.seed_human_root()
        self.gitlab.issue_list[PID] = [make_issue(81, description=BAD_MARKER), make_issue(82)]
        result = SYNC.Syncer(config(), self.gitlab, self.buzz, state_dir=Path(self.tmp.name)).run(dry_run=True)
        self.assertEqual([(s["object"], s["iid"]) for s in result["stalled"]], [("issue", 81)])
        self.assertEqual(self.buzz.writes, [])
        self.assertEqual(self.gitlab.writes, [])

    def test_dry_run_reports_a_new_mr_whose_origin_would_stall(self):
        """L1-GIS-FU-031 新 MR 同样：dry-run 校验 origin，坏的进 stalled。"""
        self.seed_human_root()
        self.gitlab.mr_list[PID] = [make_mr(897, source_branch="feature/x", description=BAD_MARKER)]
        result = SYNC.Syncer(config(), self.gitlab, self.buzz, state_dir=Path(self.tmp.name)).run(dry_run=True)
        self.assertEqual([(s["object"], s["iid"]) for s in result["stalled"]], [("mr", 897)])
        self.assertEqual(self.buzz.writes, [])

    def test_dry_run_reports_a_new_milestone_whose_origin_would_stall(self):
        """L1-GIS-FU-032 新 milestone 同样：dry-run 校验 origin。"""
        self.gitlab = MilestoneGitLab()
        self.seed_human_root()
        self.gitlab.milestone_list = [{"iid": 9, "title": "2026-Q4", "description": BAD_MARKER}]
        self.gitlab.event_list = [milestone_event(5, "created", 9)]
        result = SYNC.Syncer(config(), self.gitlab, self.buzz, state_dir=Path(self.tmp.name)).run(dry_run=True)
        self.assertEqual([(s["object"], s["iid"]) for s in result["stalled"]], [("milestone", 9)])
        self.assertEqual(self.buzz.writes, [])


# ── E. the stalled notice tags the Channel owner ─────────────────────────────


class StalledNoticeOwnerTest(FollowupCase):
    def setUp(self):
        super().setUp()
        self.buzz = OwnerBuzz({OWNER: "owner", ALICE: "member", AGENT: "bot", DESK: "bot"})

    def stall_one(self, **overrides):
        self.seed_human_root()
        self.gitlab.issue_list[PID] = [make_issue(81, description=BAD_MARKER)]
        return self.run_sync(config(**overrides))

    def test_the_stalled_notice_tags_the_channel_owner_once(self):
        """L1-GIS-FU-040 stalled 频道通知 @ 频道 owner（直接用其 pubkey，不要求在 people 里映射）；同一天再跑不重复。"""
        self.stall_one()
        notices = self.stalled_notices()
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0][0], (OWNER,))
        self.run_sync(config())
        self.assertEqual(len(self.stalled_notices()), 1)

    def test_no_owner_role_means_no_tag(self):
        """L1-GIS-FU-041 频道里没有 owner 角色：通知照发，但不 @ 任何人。"""
        self.buzz.roles.pop(OWNER)
        self.stall_one()
        self.assertEqual([mentions for mentions, _ in self.stalled_notices()], [()])

    def test_an_agent_owner_is_never_tagged(self):
        """L1-GIS-FU-042 owner 是 agent（agent_pubkeys）或 Desk 自己：不 @。"""
        self.stall_one(agent_pubkeys=[OWNER])
        self.assertEqual([mentions for mentions, _ in self.stalled_notices()], [()])

    def test_other_top_level_notices_do_not_tag_the_owner(self):
        """L1-GIS-FU-043 只有 stalled 通知 @ owner：其它顶层通知（例如 token 即将过期）不带 owner。"""
        self.gitlab.token_list = [{"id": 5, "name": "ci", "expires_at": "2026-09-14", "active": True,
                                   "revoked": False}]
        self.run_sync(config())
        notices = [(m, c) for _, c, m in self.buzz.writes if "[object:access_token]" in c]
        self.assertTrue(notices)
        self.assertTrue(all(OWNER not in mentions for mentions, _ in notices))


if __name__ == "__main__":
    unittest.main()
