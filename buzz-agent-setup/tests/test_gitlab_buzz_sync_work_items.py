"""GitLab work-item URLs on issue plaques (engineering/skills#101).

Newer GitLab hands out `/-/work_items/N` as the web_url of some issues. The sync only knew
`/-/issues/N` on a plaque's URL line, so a bound issue root it had itself published was refused
("bound root is not a readable Desk root for this object") and the whole channel stopped.
"""

import unittest

try:
    from .test_gitlab_buzz_sync_hardening import (  # noqa: F401
        BOT_ID, CHANNEL, DESK, PID, SYNC, WEB, SyncCase, make_issue,
    )
except ImportError:  # imported as a top-level module
    from test_gitlab_buzz_sync_hardening import (  # noqa: F401
        BOT_ID, CHANNEL, DESK, PID, SYNC, WEB, SyncCase, make_issue,
    )


def legacy_plaque(iid, url_tail):
    """A plaque as an older run published it: the URL line is whatever GitLab's web_url was."""
    return f"📋 **#{iid} Issue {iid}**\nbuzz-sync-test/pilot\n{WEB}/-/{url_tail}/{iid}"


class WorkItemIdentityTest(unittest.TestCase):
    def test_plaque_identity_accepts_issue_and_work_item_urls(self):
        """L1-GIS-WI-001 issue 门牌的 URL 尾巴 /-/issues/N 与 /-/work_items/N 都认作 issue。"""
        for tail in ("issues", "work_items"):
            with self.subTest(tail=tail):
                self.assertEqual(
                    SYNC.plaque_identity(f"{WEB}/-/{tail}/16"), {"object": "issue", "iid": 16})

    def test_work_items_tail_does_not_leak_into_other_kinds(self):
        """L1-GIS-WI-002 MR / milestone / 分支 URL 的识别不变，work_items 也不能冒充它们。"""
        self.assertEqual(SYNC.plaque_identity(f"{WEB}/-/merge_requests/31"), {"object": "mr", "iid": 31})
        self.assertEqual(SYNC.plaque_identity(f"{WEB}/-/milestones/4"), {"object": "milestone", "iid": 4})
        self.assertEqual(SYNC.plaque_identity(f"{WEB}/-/tree/feat/x"), {"object": "branch"})
        self.assertIsNone(SYNC.plaque_identity(f"{WEB}/-/work_items/0"))
        self.assertIsNone(SYNC.plaque_identity(f"{WEB}/-/work_items/16/extra"))
        self.assertIsNone(SYNC.plaque_identity(f"{WEB}/-/merge_requests/../work_items"))

    def test_canonical_plaque_url_maps_work_items_to_issues_only(self):
        """L1-GIS-WI-003 规范形：/-/work_items/N → /-/issues/N；其余 URL 原样。"""
        self.assertEqual(SYNC.canonical_plaque_url(f"{WEB}/-/work_items/16"), f"{WEB}/-/issues/16")
        self.assertEqual(SYNC.canonical_plaque_url(f"{WEB}/-/issues/16"), f"{WEB}/-/issues/16")
        self.assertEqual(SYNC.canonical_plaque_url(f"{WEB}/-/merge_requests/31"), f"{WEB}/-/merge_requests/31")
        self.assertEqual(SYNC.canonical_plaque_url(f"{WEB}/-/tree/feat/x"), f"{WEB}/-/tree/feat/x")


class WorkItemPlaqueTest(SyncCase):
    def bind(self, iid, root):
        note = SYNC.render_binding_note(
            {"project_id": PID, "object": "issue", "iid": iid, "channel_id": CHANNEL, "root_event_id": root},
            SYNC.buzz_message_link(CHANNEL, root),
        )
        self.gitlab.issue_notes.setdefault(iid, []).append(
            {"id": 900 + iid, "author": {"id": BOT_ID}, "body": note, "system": False,
             "created_at": "2026-09-13T02:00:00Z"})

    def seed_legacy_root(self, iid, url_tail="work_items", bound=True):
        root = self.buzz.send(legacy_plaque(iid, url_tail))
        if bound:
            self.bind(iid, root)
        return root

    def test_an_already_published_work_items_plaque_stays_a_readable_root(self):
        """L1-GIS-WI-004 已发出的 /-/work_items/N 门牌仍是可读的 Desk root：更新回到同一 Thread，整轮不报错。"""
        self.gitlab.issue_list[PID] = [make_issue(16, web_url=f"{WEB}/-/work_items/16", title="Issue 16")]
        root = self.seed_legacy_root(16)
        result = self.run_sync()
        self.assertNotEqual(result.get("status"), "error")
        self.assertEqual(sum(1 for e in self.buzz.events if not any(t[0] == "e" for t in e["tags"])), 1)
        replies = [e for e in self.buzz.events if ["e", root, "", "reply"] in e["tags"]]
        self.assertGreaterEqual(len(replies), 1)

    def test_new_fact_roots_keep_the_work_items_url(self):
        """L1-GIS-WI-005 work_items 形式的新 Issue 用一条事实作根，仍能回读身份与 GitLab 链接。"""
        self.gitlab.issue_list[PID] = [make_issue(17, web_url=f"{WEB}/-/work_items/17")]
        self.run_sync()
        roots = [e for e in self.buzz.events if not any(t[0] == "e" for t in e["tags"])]
        self.assertEqual(len(roots), 1)
        self.assertEqual(SYNC.parse_header(roots[0]["content"])["issue"], 17)
        self.assertIn(f"{WEB}/-/work_items/17", roots[0]["content"])
        self.assertEqual(len(self.buzz.events), 1)

    def test_a_new_work_items_issue_syncs_again_without_error(self):
        """L1-GIS-WI-006 复现 #101：第一轮发门牌并绑定，issue 更新后第二轮读回该 root 不再报「bound root is not a readable Desk root」。"""
        self.gitlab.issue_list[PID] = [make_issue(18, web_url=f"{WEB}/-/work_items/18")]
        self.run_sync()
        self.gitlab.issue_list[PID] = [make_issue(
            18, web_url=f"{WEB}/-/work_items/18", title="Issue 18 renamed", updated_at="2026-09-13T03:00:00Z")]
        result = self.run_sync()
        self.assertNotEqual(result.get("status"), "error")
        self.assertEqual(sum(1 for e in self.buzz.events if not any(t[0] == "e" for t in e["tags"])), 1)

    def test_lost_binding_recovers_a_legacy_work_items_plaque(self):
        """L1-GIS-WI-007 binding note 丢了：按 URL 找回旧的 work_items 门牌，不重复开 root，并重写 binding。"""
        self.gitlab.issue_list[PID] = [make_issue(19, web_url=f"{WEB}/-/work_items/19")]
        root = self.seed_legacy_root(19, bound=False)
        self.run_sync()
        roots = [e for e in self.buzz.events if not any(t[0] == "e" for t in e["tags"])]
        self.assertEqual([e["id"] for e in roots], [root])
        self.assertTrue(any(root in note["body"] for note in self.gitlab.issue_notes.get(19, [])))

    def test_lost_binding_still_recovers_a_canonical_plaque(self):
        """L1-GIS-WI-008 对照：规范 /-/issues/N 门牌丢 binding 后照旧找回。"""
        self.gitlab.issue_list[PID] = [make_issue(20)]
        root = self.seed_legacy_root(20, url_tail="issues", bound=False)
        self.run_sync()
        roots = [e for e in self.buzz.events if not any(t[0] == "e" for t in e["tags"])]
        self.assertEqual([e["id"] for e in roots], [root])

    def test_a_root_without_a_plaque_url_is_still_not_a_readable_root(self):
        """L1-GIS-WI-009 安全对照：末行不是 issue URL 的无 header 根仍拒绝（该 issue stall，零回帖），放宽 URL 形态不等于放宽对根的要求。"""
        self.gitlab.issue_list[PID] = [make_issue(21, web_url=f"{WEB}/-/work_items/21")]
        root = self.buzz.send("📋 **#21 Issue 21**\nbuzz-sync-test/pilot\nnot a url")
        self.bind(21, root)
        writes = len(self.buzz.writes)
        result = self.run_sync()
        self.assertEqual([(s["object"], s["iid"]) for s in result["stalled"]], [("issue", 21)])
        self.assertFalse(any(reply_to == root for reply_to, _, _ in self.buzz.writes[writes:]))

    def test_a_non_desk_author_is_a_top_level_message_not_a_plaque(self):
        """L1-GIS-WI-010 非 Desk 作者的 work_items 形式「门牌」不是 Desk 门牌：按 ADR-0014 只当作一条人的顶层消息（结构合法，内容不读），既不 stall 也不被当成这个 issue 的门牌去找回。"""
        self.gitlab.issue_list[PID] = [make_issue(22, web_url=f"{WEB}/-/work_items/22")]
        root = self.seed_legacy_root(22, bound=False)
        for event in self.buzz.events:
            if event["id"] == root:
                event["pubkey"] = "a1" * 32
        result = self.run_sync()  # recovery only trusts Desk-authored roots, so the issue opens a plaque of its own
        self.assertEqual(result["stalled"], [])
        own = SYNC.parse_binding(self.gitlab.issue_notes[22], BOT_ID, PID, "issue", 22, CHANNEL)
        self.assertNotEqual(own, root)


class OriginErrorNamesTheObjectTest(SyncCase):
    """A malformed origin marker fails closed for that one object (it stalls, ADR-0009), and the stalled entry
    must name the object so an operator can find and fix the one description that caused it. A well-formed marker
    whose root cannot be used no longer stalls (ADR-0014)."""

    def test_issue_with_a_malformed_origin_fails_closed_and_names_the_issue(self):
        """L1-GIS-WI-011 origin 注释格式非法：该 issue fail closed（stalled，零 binding/零回帖），原因带 issue 编号（合法但指向人类顶层消息的见 ADR-0014）。"""
        self.gitlab.issue_list[PID] = [make_issue(81, description="请从这里接手\n<!-- gitlab-buzz-origin:v1 {not json} -->")]
        result = self.run_sync()
        reason = result["stalled"][0]["reason"]
        self.assertEqual([(s["object"], s["iid"]) for s in result["stalled"]], [("issue", 81)])
        self.assertIn("issue 81", reason)
        self.assertIn("not JSON", reason)
        self.assertEqual(self.gitlab.writes, [])
        self.assertFalse(any("#81" in content for _, content, _ in self.buzz.writes))

    def test_mr_with_a_malformed_origin_names_the_mr(self):
        """L1-GIS-WI-012 MR 的 origin 注释格式非法：该 MR stalled，原因带 mr 编号，零写入（根读不到的见 ADR-0014：回退自开门牌）。"""
        from_missing = "请从这里接手\n<!-- gitlab-buzz-origin:v1 {not json} -->"
        self.gitlab.mr_list[PID] = [{
            "iid": 897, "project_id": PID, "title": "MR 897", "state": "opened", "draft": False, "sha": "a" * 40,
            "source_branch": "feature/x", "target_branch": "main", "labels": [], "reviewers": [],
            "author": {"username": "dave"}, "web_url": f"{WEB}/-/merge_requests/897",
            "description": from_missing, "created_at": "2026-09-13T01:00:00Z",
            "updated_at": "2026-09-13T01:00:00Z"}]
        result = self.run_sync()
        self.assertEqual([(s["object"], s["iid"]) for s in result["stalled"]], [("mr", 897)])
        self.assertIn("mr 897", result["stalled"][0]["reason"])
        self.assertEqual(self.gitlab.writes, [])
        self.assertFalse(any("!897" in content for _, content, _ in self.buzz.writes))


if __name__ == "__main__":
    unittest.main()
