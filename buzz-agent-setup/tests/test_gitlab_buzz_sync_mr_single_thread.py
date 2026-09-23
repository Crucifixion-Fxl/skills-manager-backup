"""An MR's facts go to exactly one thread; every other related thread gets one cross-link (ADR-0015, skills#131).

2026-09-17 "merge-in" posted every fact of an MR (first fact, updates, comments, Diffs) into each Issue thread the
MR was associated with (`closes_issues`, the branch-name whitelist, the Issue-side `related_merge_requests` reverse
lookup) and into every origin thread. Measured on five channels in 2026-09-16..21: 94 of 285 MRs were posted into
several threads, about 561 extra messages. ADR-0015 (jchen, 2026-09-21) replaces it:

1. the binding thread is the only destination of an MR's facts and Diffs (order: closes_issues, branch whitelist, first
   origin, branch family, own plaque);
2. every other related thread gets **one** cross-link, when the MR first appears, and nothing after that;
3. `related_merge_requests` only produces links (the `issues:` line and cross-links), it neither places nor binds;
4. an MR that is already bound is not spread to an Issue that appears later, and gets no late cross-link;
5. Diffs (kind 40008) follow the binding thread;
6. several origins: the first is the binding and gets the facts, the others get a cross-link;
7. nothing that was already sent is moved or removed;
8. mentions and `transition:reviewable` stay in the binding thread; an excluded or unreadable related thread is skipped
   and counted.

The cross-link is a Desk message in the other thread with the header
`[gitlab-notify:v1][object:mr][state:…][draft:…][change:xref][transition:none][project:N][mr:M]`.
"""

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:
    from .test_gitlab_buzz_sync_mr_issue_merge import (  # noqa: F401
        BOT, BOT_ID, CHANNEL, DESK, PEOPLE, PID, SINCE, SYNC, WEB, CAROL, FakeBuzz, FakeGitLab, OBJ_URL,
        make_issue, make_mr,
    )
except ImportError:  # imported as a top-level module
    from test_gitlab_buzz_sync_mr_issue_merge import (  # noqa: F401
        BOT, BOT_ID, CHANNEL, DESK, PEOPLE, PID, SINCE, SYNC, WEB, CAROL, FakeBuzz, FakeGitLab, OBJ_URL,
        make_issue, make_mr,
    )

PERSON = "1a" * 32
FACT_CHANGES = ("lifecycle", "update", "activity")


def marker(root):
    return SYNC.render_origin_marker(CHANNEL, root)


class SingleThreadCase(unittest.TestCase):
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

    # -- driving the sync ------------------------------------------------------------------------------------------

    def run_sync(self, dry_run=False):
        return SYNC.Syncer(self.config, self.gitlab, self.buzz, state_dir=Path(self.tmp.name)).run(dry_run=dry_run)

    def touch(self, iid, **overrides):
        """The MR was updated again: a later `updated_at` makes the next round read it."""
        self._ticks = getattr(self, "_ticks", 1) + 1
        self.gitlab.mr_list = [make_mr(iid=iid, updated_at=f"2026-09-13T{self._ticks:02d}:00:00Z", **overrides)]

    def human_root(self, text="能不能把 MR 的通知收成一个话题？"):
        event_id = hashlib.sha256(f"human:{len(self.buzz.events)}:{text}".encode()).hexdigest()
        self.buzz.events.append({"id": event_id, "pubkey": PERSON, "kind": 9, "created_at": 900 + len(self.buzz.events),
                                 "tags": [["h", CHANNEL]], "content": text})
        return event_id

    # -- reading the channel ---------------------------------------------------------------------------------------

    def issue_root(self, iid):
        return self.buzz.search_roots(OBJ_URL("issues", iid), "issue", iid)[0]["id"]

    def own_roots(self, mr_iid):
        return self.buzz.search_roots(OBJ_URL("merge_requests", mr_iid), "mr", mr_iid)

    def binding(self, mr_iid):
        return SYNC.parse_binding(self.gitlab.mr_note_list.get(mr_iid, []), BOT_ID, PID, "mr", mr_iid, CHANNEL)

    @staticmethod
    def mr_header(event, mr_iid):
        header = SYNC.parse_header(event.get("content") or "")
        if event.get("kind") == 9 and header and header["object"] == "mr" and header.get("mr") == mr_iid:
            return header
        return None

    def mr_messages(self, root, mr_iid):
        return [e for e in self.buzz.thread(root) if self.mr_header(e, mr_iid)]

    def facts(self, root, mr_iid):
        return [e for e in self.mr_messages(root, mr_iid) if self.mr_header(e, mr_iid)["change"] in FACT_CHANGES]

    def xrefs(self, root, mr_iid):
        return [e for e in self.mr_messages(root, mr_iid) if self.mr_header(e, mr_iid)["change"] == "xref"]

    def everywhere(self, mr_iid):
        return [e for e in self.buzz.events if self.mr_header(e, mr_iid)]

    def enable_diffs(self, sha, *paths, mr_iid=31):
        self.config["diff"] = {"enabled": True, "private": False}
        self.gitlab.diffs[(mr_iid, sha)] = [
            {"old_path": path, "new_path": path, "diff": f"@@ -1 +1 @@\n-old {path}\n+new {path}\n"} for path in paths
        ]

    def diff_events(self, root):
        return [e for e in self.buzz.thread(root) if e["kind"] == 40008]

    def diff_files(self, root):
        return sorted(dict(tag[:2] for tag in e["tags"] if tag[0] == "file")["file"] for e in self.diff_events(root))

    def two_issues_one_mr(self, **mr_overrides):
        """MR 31 closes Issues 182 and 183, both already in the channel."""
        self.gitlab.issue_list = [make_issue(182), make_issue(183)]
        self.gitlab.mr_list = [make_mr(iid=31, **mr_overrides)]
        self.gitlab.closes = {31: [make_issue(182), make_issue(183)]}



# ── 1. one destination, one cross-link per other thread ─────────────────────────────────────────────────────────────


class OneThreadTest(SingleThreadCase):
    def test_an_mr_closing_two_issues_posts_its_facts_only_in_the_binding_thread(self):
        """L1-GIS-MT-001 关闭两个 Issue 的 MR：事实只发进 binding 指向的第一个 Issue Thread，另一个 Thread 里没有 MR 事实，也没有 MR 自己的门牌。"""
        self.two_issues_one_mr()
        self.run_sync()
        first, second = self.issue_root(182), self.issue_root(183)
        self.assertEqual(self.binding(31), first)
        self.assertEqual(len(self.facts(first, 31)), 1)
        self.assertEqual(self.facts(second, 31), [], "the other Issue thread must not carry any MR fact")
        self.assertEqual(self.own_roots(31), [])

    def test_the_other_thread_gets_exactly_one_cross_link_to_the_binding_thread(self):
        """L1-GIS-MT-002 交叉链接：reply 在另一个 Issue 的根下，header 是 change:xref、transition:none，指向 binding Thread 和 MR 链接，不带 @。"""
        self.two_issues_one_mr(title="恢复最近工作区")
        self.run_sync()
        first, second = self.issue_root(182), self.issue_root(183)
        cross = self.xrefs(second, 31)
        self.assertEqual(len(cross), 1)
        self.assertEqual(self.xrefs(first, 31), [], "the binding thread needs no cross-link to itself")
        message = cross[0]
        header = SYNC.parse_header(message["content"])
        self.assertEqual((header["object"], header["change"], header["transition"], header["project"], header["mr"]),
                         ("mr", "xref", "none", PID, 31))
        self.assertEqual(message["content"].splitlines()[-1], SYNC.header_line(message["content"]))
        self.assertIn(SYNC.buzz_message_link(CHANNEL, first), message["content"])
        self.assertIn(f"[!31 恢复最近工作区]({WEB}/-/merge_requests/31)", message["content"])
        self.assertEqual(message["tags"][1][:2], ["e", second])
        self.assertEqual([tag for tag in message["tags"] if tag[0] == "p"], [])

    def test_n_related_threads_get_n_minus_one_cross_links_and_the_mr_speaks_once(self):
        """L1-GIS-MT-003 三个关联（closes、分支名白名单、related 反查）：一个 Thread 收事实，其余两个各一条交叉链接；全频道里这个 MR 的事实只有一条。"""
        self.gitlab.issue_list = [make_issue(182), make_issue(183), make_issue(184)]
        self.gitlab.mr_list = [make_mr(iid=31, source_branch="feature/183-restore")]
        self.gitlab.closes = {31: [make_issue(182)]}
        self.gitlab.related = {184: [31]}
        self.run_sync()
        roots = {iid: self.issue_root(iid) for iid in (182, 183, 184)}
        self.assertEqual(self.binding(31), roots[182], "closes_issues comes first")
        self.assertEqual(len(self.facts(roots[182], 31)), 1)
        for other in (183, 184):
            with self.subTest(issue=other):
                self.assertEqual(self.facts(roots[other], 31), [])
                self.assertEqual(len(self.xrefs(roots[other], 31)), 1)
        changes = sorted(self.mr_header(e, 31)["change"] for e in self.everywhere(31))
        self.assertEqual(changes, ["lifecycle", "xref", "xref"])

    def test_the_round_counts_its_cross_links(self):
        """L1-GIS-MT-004 同步报告里有 mr_xrefs：本轮发出的交叉链接条数；重跑为 0。"""
        self.two_issues_one_mr()
        self.assertEqual(self.run_sync()["mr_xrefs"], 1)
        self.assertEqual(self.run_sync()["mr_xrefs"], 0)

    def test_a_rerun_writes_nothing(self):
        """L1-GIS-MT-005 整轮重跑不再写任何消息（事实、交叉链接、binding 都不重复）。"""
        self.two_issues_one_mr()
        self.run_sync()
        before = (list(self.buzz.writes), list(self.gitlab.writes))
        self.run_sync()
        self.assertEqual((self.buzz.writes, self.gitlab.writes), before)

    def test_reviewers_are_mentioned_once_and_only_the_binding_thread_may_fire_the_route_gate(self):
        """L1-GIS-MT-006 可评审 MR 关联两个 Issue：@ 只发一次、在 binding Thread；全频道只有一条 transition:reviewable；交叉链接不带 @ 也不触发路由。"""
        self.two_issues_one_mr(reviewers=[{"username": "carol"}])
        self.run_sync()
        mentioned = [write for write in self.buzz.writes if write[2]]
        self.assertEqual([write[2] for write in mentioned], [(CAROL,)])
        transitions = [self.mr_header(e, 31)["transition"] for e in self.everywhere(31)]
        self.assertEqual(sorted(transitions), ["none", "reviewable"])
        cross = self.xrefs(self.issue_root(183), 31)
        self.assertEqual([SYNC.parse_header(e["content"])["transition"] for e in cross], ["none"])


# ── 2. the first appearance is the only appearance ──────────────────────────────────────────────────────────────────


class NothingFollowsTheCrossLinkTest(SingleThreadCase):
    def test_updates_comments_and_the_merge_reach_only_the_binding_thread(self):
        """L1-GIS-MT-010 新提交、评论、已合并都只进 binding Thread；另一个 Issue Thread 里始终只有那一条交叉链接。"""
        self.two_issues_one_mr()
        self.run_sync()
        first, second = self.issue_root(182), self.issue_root(183)

        self.touch(31, sha="b" * 40)
        self.run_sync()
        self.gitlab.mr_note_list.setdefault(31, []).append({
            "id": 5001, "author": {"id": 99, "username": "carol"}, "body": "please rebase",
            "system": False, "created_at": "2026-09-13T03:30:00Z"})
        self.touch(31, sha="b" * 40)
        self.run_sync()
        self.touch(31, sha="b" * 40, state="merged")
        self.run_sync()

        self.assertEqual([self.mr_header(e, 31)["change"] for e in self.mr_messages(second, 31)], ["xref"])
        kinds = [self.mr_header(e, 31)["change"] for e in self.mr_messages(first, 31)]
        self.assertEqual(sorted(kinds), ["activity", "lifecycle", "lifecycle", "update"])
        self.assertEqual(len([e for e in self.buzz.thread(second) if "please rebase" in e.get("content", "")]), 0)

    def test_a_pipeline_failure_reaches_only_the_binding_thread(self):
        """L1-GIS-MT-011 MR 流水线失败的活动回帖只进 binding Thread。"""
        self.two_issues_one_mr()
        self.run_sync()
        self.gitlab.pipelines = lambda project_id, updated_after: [{
            "id": 4242, "iid": 1, "status": "failed", "ref": "refs/merge-requests/31/head",
            "updated_at": "2026-09-13T03:00:00Z"}]
        self.gitlab.pipeline_jobs = lambda project_id, pid: [{"name": "test:unit"}]
        self.run_sync()
        first, second = self.issue_root(182), self.issue_root(183)
        self.assertEqual(len([e for e in self.buzz.thread(first) if "流水线失败" in e["content"]]), 1)
        self.assertEqual([e for e in self.buzz.thread(second) if "流水线失败" in e["content"]], [])

    def test_an_approval_reaches_only_the_binding_thread(self):
        """L1-GIS-MT-014 MR 被批准的活动回帖只进 binding Thread。"""
        self.two_issues_one_mr()
        self.run_sync()
        self.gitlab.events = lambda project_id, after_date: [{
            "id": 9002, "action_name": "approved", "target_type": "MergeRequest", "target_iid": 31,
            "target_title": "恢复最近工作区", "created_at": "2026-09-13T04:00:00Z", "author": {"username": "carol"}}]
        self.run_sync()
        first, second = self.issue_root(182), self.issue_root(183)
        self.assertEqual(len([e for e in self.buzz.thread(first) if "已批准" in e["content"]]), 1)
        self.assertEqual([e for e in self.buzz.thread(second) if "已批准" in e["content"]], [])
        self.assertEqual([self.mr_header(e, 31)["change"] for e in self.mr_messages(second, 31)], ["xref"])

    def test_diffs_go_only_to_the_binding_thread(self):
        """L1-GIS-MT-012 Diff（kind 40008）只进 binding Thread，新 commit 的 Diff 同样；重跑不重复。"""
        self.two_issues_one_mr()
        self.enable_diffs("a" * 40, "app/a.dart", "app/b.dart")
        self.run_sync()
        first, second = self.issue_root(182), self.issue_root(183)
        self.assertEqual(self.diff_files(first), ["app/a.dart", "app/b.dart"])
        self.assertEqual(self.diff_events(second), [])

        self.touch(31, sha="b" * 40)
        self.enable_diffs("b" * 40, "app/a.dart")
        self.run_sync()
        self.assertEqual(len(self.diff_events(first)), 3)
        self.assertEqual(self.diff_events(second), [])
        before = list(self.buzz.writes)
        self.run_sync()
        self.assertEqual(self.buzz.writes, before)

    def test_an_unassociated_mr_keeps_its_diffs_in_its_own_thread(self):
        """L1-GIS-MT-013 没有关联的 MR：Diff 留在自己的 MR Thread（不变）。"""
        self.gitlab.mr_list = [make_mr(iid=31, source_branch="spike/no-issue")]
        self.enable_diffs("a" * 40, "app/a.dart")
        self.run_sync()
        self.assertEqual(len(self.diff_events(self.own_roots(31)[0]["id"])), 1)


# ── 3. related_merge_requests only makes links ──────────────────────────────────────────────────────────────────────


class RelatedOnlyMakesLinksTest(SingleThreadCase):
    def test_a_related_hit_without_closes_or_branch_opens_the_mr_own_thread(self):
        """L1-GIS-MT-020 只有 related 反查命中（closes_issues 为空、分支名不符）：MR 自开 🔀 门牌并绑定在自己名下，事实不进 Issue Thread；Issue Thread 只收一条交叉链接；MR 首条事实的 issues: 行指向该 Issue Thread。"""
        self.gitlab.issue_list = [make_issue(182)]
        self.gitlab.mr_list = [make_mr(iid=31, source_branch="spike/no-issue-xx")]
        self.gitlab.related = {182: [31]}
        self.run_sync()
        own = self.own_roots(31)
        self.assertEqual(len(own), 1)
        issue = self.issue_root(182)
        self.assertEqual(self.binding(31), own[0]["id"])
        self.assertEqual(self.facts(issue, 31), [])
        self.assertEqual(len(self.xrefs(issue, 31)), 1)
        first_fact = self.facts(own[0]["id"], 31)[0]["content"].splitlines()
        self.assertIn(f"issues: {SYNC.buzz_message_link(CHANNEL, issue)}", first_fact)
        self.assertIn(SYNC.buzz_message_link(CHANNEL, own[0]["id"]), self.xrefs(issue, 31)[0]["content"])

    def test_a_branch_whitelist_issue_beats_a_related_one(self):
        """L1-GIS-MT-021 分支名白名单先于 related：白名单 Issue 是 binding，related 命中的 Issue 只收交叉链接。"""
        self.gitlab.issue_list = [make_issue(182), make_issue(401)]
        self.gitlab.mr_list = [make_mr(iid=32, source_branch="feature/401-video-search")]
        self.gitlab.related = {182: [32]}
        self.run_sync()
        self.assertEqual(self.binding(32), self.issue_root(401))
        self.assertEqual(len(self.facts(self.issue_root(401), 32)), 1)
        self.assertEqual(self.facts(self.issue_root(182), 32), [])
        self.assertEqual(len(self.xrefs(self.issue_root(182), 32)), 1)
        self.assertEqual(self.own_roots(32), [])

    def test_an_origin_beats_a_related_issue(self):
        """L1-GIS-MT-022 origin 先于 related：origin 的话题是 binding 收事实，related 命中的 Issue 只收交叉链接。"""
        self.gitlab.issue_list = [make_issue(182)]
        discussion = self.human_root()
        self.gitlab.mr_list = [make_mr(iid=33, source_branch="spike/x", description=marker(discussion))]
        self.gitlab.related = {182: [33]}
        self.run_sync()
        self.assertEqual(self.binding(33), discussion)
        self.assertEqual(len(self.facts(discussion, 33)), 1)
        self.assertEqual(self.facts(self.issue_root(182), 33), [])
        self.assertEqual(len(self.xrefs(self.issue_root(182), 33)), 1)
        self.assertEqual(self.own_roots(33), [])

    def test_a_related_hit_joins_a_branch_family_thread_only_by_link(self):
        """L1-GIS-MT-023 related 命中的 MR 仍按分支族分组：进组 Thread 收事实，Issue Thread 只收交叉链接。"""
        author = {"username": "sshao"}
        self.gitlab.issue_list = [make_issue(182)]
        self.gitlab.mr_list = [make_mr(iid=34, source_branch="fix/x-staging", target_branch="staging", author=author)]
        self.run_sync()
        anchor = self.own_roots(34)[0]["id"]
        self.gitlab.mr_list.append(make_mr(iid=35, source_branch="fix/x-master", target_branch="master", author=author,
                                           created_at="2026-09-13T02:30:00Z", updated_at="2026-09-13T02:30:00Z"))
        self.gitlab.related = {182: [35]}
        self.run_sync()
        self.assertEqual(self.binding(35), anchor)
        self.assertEqual(len(self.facts(anchor, 35)), 1)
        self.assertEqual(self.facts(self.issue_root(182), 35), [])
        self.assertEqual(len(self.xrefs(self.issue_root(182), 35)), 1)
        self.assertEqual(self.own_roots(35), [])

    def test_the_reverse_lookup_is_still_queried_once_per_issue_per_run(self):
        """L1-GIS-MT-024 related_merge_requests 仍然每轮每个 Issue 最多查一次（多 MR 共享结果）；命中的 Issue 只收该 MR 的一条交叉链接，MR 自开门牌。"""
        self.gitlab.issue_list = [make_issue(iid) for iid in (182, 183, 184, 185)]
        self.gitlab.mr_list = [make_mr(iid=iid, source_branch=f"spike/no-issue-{iid}x") for iid in (31, 32, 33, 34, 35)]
        self.gitlab.related = {183: [32]}
        self.run_sync()
        self.assertLessEqual(self.gitlab.related_calls, 4)
        issue = self.issue_root(183)
        self.assertEqual(self.facts(issue, 32), [])
        self.assertEqual(len(self.xrefs(issue, 32)), 1)
        self.assertEqual(len(self.own_roots(32)), 1)

    def test_activity_of_an_unbound_mr_is_not_placed_by_a_related_hit(self):
        """L1-GIS-MT-025 还没有 binding 的 MR（早于 since 的存量）出现流水线活动：related 命中不再决定落点也不写 binding，活动计入 unbound。"""
        self.gitlab.issue_list = [make_issue(182)]
        self.gitlab.mr_list = [make_mr(iid=31, source_branch="spike/no-issue-xx",
                                       created_at="2026-09-12T01:00:00Z", updated_at="2026-09-13T01:00:00Z")]
        self.gitlab.related = {182: [31]}
        self.gitlab.pipelines = lambda project_id, updated_after: [{
            "id": 4242, "iid": 1, "status": "failed", "ref": "refs/merge-requests/31/head",
            "updated_at": "2026-09-13T03:00:00Z"}]
        self.gitlab.pipeline_jobs = lambda project_id, pid: [{"name": "test:unit"}]
        result = self.run_sync()
        self.assertEqual(result["unbound"], 1)
        self.assertEqual(self.everywhere(31), [])
        self.assertEqual(self.gitlab.mr_note_list.get(31, []), [])

    def test_a_related_hit_on_an_issue_without_a_thread_does_not_create_one(self):
        """L1-GIS-MT-027 反查命中的 Issue 还没有 Thread（早于 since 的存量）：不为了一条链接给它建门牌；MR 自开门牌，没有交叉链接，首条事实的 issues: 行里也没有它。"""
        self.gitlab.issue_list = [make_issue(182, created_at="2026-09-12T01:00:00Z", updated_at="2026-09-13T01:00:00Z")]
        self.gitlab.mr_list = [make_mr(iid=31, source_branch="spike/no-issue-xx")]
        self.gitlab.related = {182: [31]}
        self.run_sync()
        self.assertEqual(self.buzz.search_roots(OBJ_URL("issues", 182), "issue", 182), [])
        own = self.own_roots(31)
        self.assertEqual(len(own), 1)
        self.assertEqual(len(self.everywhere(31)), 1)
        self.assertFalse([line for line in self.facts(own[0]["id"], 31)[0]["content"].splitlines()
                          if line.startswith("issues:")])

    def test_a_related_hit_on_an_excluded_issue_is_skipped_and_counted(self):
        """L1-GIS-MT-028 反查命中的 Issue 被排除（confidential）：不收交叉链接，计入 skipped.excluded；MR 照常自开门牌。"""
        self.gitlab.issue_list = [make_issue(182, confidential=True)]
        self.gitlab.mr_list = [make_mr(iid=31, source_branch="spike/no-issue-xx")]
        self.gitlab.related = {182: [31]}
        result = self.run_sync()
        self.assertEqual(result["skipped"]["excluded"], 1)
        self.assertEqual(len(self.own_roots(31)), 1)
        self.assertEqual(len(self.everywhere(31)), 1)

    def test_the_association_resolver_still_returns_every_source_in_order(self):
        """L1-GIS-MT-026 关联解析仍合并 closes、分支白名单、related 三个来源、去重、最多 3 个；新增的拆分把「能决定落点」的（closes、分支）和「只出链接」的（related）分开。"""
        self.gitlab.issue_list = [make_issue(iid) for iid in (551, 552, 553, 554)]
        mr = make_mr(iid=63, source_branch="feature/551-alpha")
        self.gitlab.mr_list = [mr]
        self.gitlab.closes = {63: [make_issue(553)]}
        self.gitlab.related = {554: [63], 552: [63]}
        syncer = SYNC.Syncer(self.config, self.gitlab, self.buzz, state_dir=Path(self.tmp.name))
        syncer._issues_scanned[PID] = [make_issue(iid) for iid in (551, 552, 553, 554)]
        self.assertEqual(syncer.mr_issue_associations(PID, mr, 63), [553, 551, 552])
        self.assertEqual(syncer.mr_issue_plan(PID, mr, 63), ([553, 551], [552]))
        self.assertEqual(syncer.mr_issue_plan(PID, mr, 63, with_related=False), ([553, 551], []))


# ── 4. an MR that is already bound is not spread ────────────────────────────────────────────────────────────────────


class BoundMrIsNotSpreadTest(SingleThreadCase):
    def test_an_issue_that_shows_up_after_the_mr_got_its_own_thread_never_receives_it(self):
        """L1-GIS-MT-030 类别 A（!952／#124）：MR 先有自己的 Thread，Issue 后出现并关联它；之后的更新、评论、合并都不进该 Issue Thread，也不补交叉链接。"""
        self.gitlab.mr_list = [make_mr(iid=952, source_branch="fix/x")]
        self.run_sync()
        own = self.own_roots(952)[0]["id"]
        self.gitlab.issue_list = [make_issue(124, created_at="2026-09-13T02:00:00Z", updated_at="2026-09-13T02:00:00Z")]
        self.gitlab.closes = {952: [make_issue(124)]}
        self.gitlab.related = {124: [952]}
        self.touch(952, source_branch="fix/x", sha="b" * 40)
        self.run_sync()
        self.gitlab.mr_note_list.setdefault(952, []).append({
            "id": 5002, "author": {"id": 99, "username": "carol"}, "body": "looks good",
            "system": False, "created_at": "2026-09-13T04:00:00Z"})
        self.touch(952, source_branch="fix/x", sha="b" * 40, state="merged")
        self.run_sync()
        issue = self.issue_root(124)
        self.assertEqual(self.mr_messages(issue, 952), [], "no fact and no cross-link in the late Issue thread")
        self.assertEqual(self.binding(952), own)
        self.assertEqual(len(self.facts(own, 952)), 4)  # first fact, update, comment, merged

    def test_a_marker_added_to_a_bound_mr_later_gets_nothing(self):
        """L1-GIS-MT-031 已绑定的 MR 之后评论里才出现 origin：那个话题既不收事实也不收交叉链接。"""
        self.two_issues_one_mr()
        self.run_sync()
        discussion = self.human_root()
        self.gitlab.mr_note_list.setdefault(31, []).append({
            "id": 5003, "author": {"id": 99, "username": "carol"}, "body": f"讨论在这里\n{marker(discussion)}",
            "system": False, "created_at": "2026-09-13T03:00:00Z"})
        self.touch(31, sha="b" * 40)
        self.gitlab.closes = {31: [make_issue(182), make_issue(183)]}
        self.run_sync()
        self.assertEqual(self.mr_messages(discussion, 31), [])
        self.assertEqual(self.binding(31), self.issue_root(182))

    def test_legacy_multi_thread_bindings_keep_updating_only_the_binding_thread(self):
        """L1-GIS-MT-032 存量（旧规则已把事实发进两个 Issue Thread，binding 指向第一个）不搬迁不清理，但之后的更新只进 binding Thread。"""
        self.two_issues_one_mr()
        self.run_sync()
        first, second = self.issue_root(182), self.issue_root(183)
        legacy = SYNC.render_mr_message(SYNC.mr_fact(make_mr(iid=31), PID), "lifecycle", (), (), first=True)
        self.buzz.send(legacy, reply_to=second)
        stale = list(self.mr_messages(second, 31))
        self.touch(31, sha="b" * 40)
        self.gitlab.closes = {31: [make_issue(182), make_issue(183)]}
        self.run_sync()
        self.assertEqual(self.mr_messages(second, 31), stale, "the old thread is left as it was")
        self.assertEqual(len(self.facts(first, 31)), 2)


# ── 5. origins ──────────────────────────────────────────────────────────────────────────────────────────────────────


class OriginThreadsTest(SingleThreadCase):
    def test_several_origins_bind_the_first_and_cross_link_the_others(self):
        """L1-GIS-MT-040 MR 描述里有两个 origin：第一条是 binding 收事实，第二条只收一条交叉链接；同一个 origin 写两次只算一条。"""
        self.gitlab.issue_list = [make_issue(14)]
        self.run_sync()
        plaque = self.issue_root(14)
        discussion = self.human_root()
        self.gitlab.mr_list = [make_mr(iid=897, source_branch="fix/x",
                                       description=f"{marker(discussion)}\n{marker(discussion)}\n{marker(plaque)}")]
        self.gitlab.mr_note_list[897] = [{"id": 61, "author": {"id": 8, "username": "alice"}, "body": marker(plaque),
                                          "system": False, "created_at": "2026-09-13T02:00:00Z"}]
        self.run_sync()
        self.assertEqual(self.binding(897), discussion)
        self.assertEqual(len(self.facts(discussion, 897)), 1)
        self.assertEqual(self.facts(plaque, 897), [])
        self.assertEqual(len(self.xrefs(plaque, 897)), 1)
        self.assertEqual(self.xrefs(discussion, 897), [])
        self.assertEqual(self.own_roots(897), [])

    def test_a_closing_issue_is_the_binding_and_the_origin_thread_gets_a_cross_link(self):
        """L1-GIS-MT-041 GitLab 关联 Issue 先于 origin（优先级不变）：Issue 是 binding 收事实，origin 的话题只收一条交叉链接（旧规则是再收一份事实）。"""
        self.gitlab.issue_list = [make_issue(14), make_issue(182)]
        self.run_sync()
        origin, issue = self.issue_root(14), self.issue_root(182)
        self.gitlab.mr_list = [make_mr(iid=31, description=marker(origin))]
        self.gitlab.closes = {31: [make_issue(182)]}
        self.run_sync()
        self.assertEqual(self.binding(31), issue)
        self.assertEqual(len(self.facts(issue, 31)), 1)
        self.assertEqual(self.facts(origin, 31), [])
        self.assertEqual(len(self.xrefs(origin, 31)), 1)
        self.assertNotIn("transition:reviewable", self.xrefs(origin, 31)[0]["content"])

    def test_an_unreadable_origin_thread_is_skipped_not_stalled(self):
        """L1-GIS-MT-042 只该收交叉链接的 origin 话题读不到（已满）：跳过并计数，MR 照常发到 binding Thread，本轮不停摆。"""
        self.gitlab.issue_list = [make_issue(14), make_issue(182)]
        self.run_sync()
        full = self.issue_root(14)
        for index in range(SYNC.THREAD_REPLY_LIMIT):
            self.buzz.send(f"chatter {index}", reply_to=full)
        self.gitlab.issue_list = [make_issue(182)]  # Issue 14 is not read again: its own full thread is not this test
        self.gitlab.mr_list = [make_mr(iid=31, description=marker(full))]
        self.gitlab.closes = {31: [make_issue(182)]}
        result = self.run_sync()
        self.assertEqual(result["stalled"], [])
        self.assertEqual(len(self.facts(self.issue_root(182), 31)), 1)
        self.assertEqual(result["skipped"]["excluded"], 1)
        self.assertEqual(self.xrefs(full, 31), [])


    def test_an_origin_that_names_the_binding_thread_itself_adds_nothing(self):
        """L1-GIS-MT-043 origin 指向的就是 binding Thread（关闭的 Issue 自己的门牌）：不重复投递，也不给自己发交叉链接。"""
        self.gitlab.issue_list = [make_issue(182)]
        self.run_sync()
        issue = self.issue_root(182)
        self.gitlab.mr_list = [make_mr(iid=31, description=marker(issue))]
        self.gitlab.closes = {31: [make_issue(182)]}
        self.run_sync()
        self.assertEqual(self.binding(31), issue)
        self.assertEqual([self.mr_header(e, 31)["change"] for e in self.mr_messages(issue, 31)], ["lifecycle"])
        self.assertEqual(len(self.everywhere(31)), 1)

    def test_when_every_closing_issue_is_excluded_the_first_origin_still_takes_the_mr(self):
        """L1-GIS-MT-044 关闭的 Issue 全被排除但描述里有 origin：第一条 origin 是 binding 收事实（沿用旧行为），排除的 Issue 什么也收不到。"""
        self.gitlab.issue_list = [make_issue(183, confidential=True)]
        discussion = self.human_root()
        self.gitlab.mr_list = [make_mr(iid=31, description=marker(discussion))]
        self.gitlab.closes = {31: [make_issue(183)]}
        result = self.run_sync()
        self.assertEqual(self.binding(31), discussion)
        self.assertEqual(len(self.facts(discussion, 31)), 1)
        self.assertEqual(len(self.everywhere(31)), 1)
        self.assertEqual(result["skipped"]["excluded"], 1)


    def test_a_discussion_thread_takes_one_review_assignment(self):
        """L1-GIS-MT-045 两个 MR 绑在同一个讨论话题：第一个可评审 MR 带 transition:reviewable 并 @ reviewer，第二个不再触发（ADR-0014：一个话题只指派一次评审）。"""
        discussion = self.human_root()
        self.gitlab.mr_list = [
            make_mr(iid=31, source_branch="fix/a", description=marker(discussion), reviewers=[{"username": "carol"}]),
            make_mr(iid=32, source_branch="fix/b", description=marker(discussion), reviewers=[{"username": "carol"}],
                    created_at="2026-09-13T01:30:00Z"),
        ]
        self.run_sync()
        transitions = {iid: self.mr_header(self.facts(discussion, iid)[0], iid)["transition"] for iid in (31, 32)}
        self.assertEqual(transitions, {31: "reviewable", 32: "none"})
        self.assertEqual([write[2] for write in self.buzz.writes if write[2]], [(CAROL,)])


    def test_a_full_binding_thread_stalls_the_mr_without_writing_a_binding(self):
        """L1-GIS-MT-046 落点是 origin 而它的 Thread 已满（500 回帖）：该 MR 按对象级隔离停摆，不写 binding，事实和交叉链接都不发。"""
        self.gitlab.issue_list = [make_issue(14)]
        self.run_sync()
        full = self.issue_root(14)
        for index in range(SYNC.THREAD_REPLY_LIMIT):
            self.buzz.send(f"chatter {index}", reply_to=full)
        self.gitlab.issue_list = []  # Issue 14 is not read again: its own full thread is not this test
        self.gitlab.mr_list = [make_mr(iid=31, source_branch="fix/x", description=marker(full))]
        result = self.run_sync()
        self.assertEqual([(item["object"], item["iid"]) for item in result["stalled"]], [("mr", 31)])
        self.assertEqual(self.gitlab.mr_note_list.get(31, []), [])
        self.assertEqual(self.everywhere(31), [])


# ── 6. compatibility: excluded Issues, dry-run ──────────────────────────────────────────────────────────────────────


class CompatibilityTest(SingleThreadCase):
    def test_an_excluded_related_issue_is_skipped_and_counted(self):
        """L1-GIS-MT-050 被 exclude（confidential）的关联 Issue 不收交叉链接，计入 skipped.excluded；MR 事实照常进 binding Thread，本轮成功。"""
        self.gitlab.issue_list = [make_issue(182), make_issue(183, confidential=True)]
        self.gitlab.mr_list = [make_mr(iid=31)]
        self.gitlab.closes = {31: [make_issue(182), make_issue(183)]}
        result = self.run_sync()
        self.assertIn(result["status"], {"ok", "degraded"})
        self.assertEqual(result["skipped"]["excluded"], 1)
        self.assertEqual(len(self.facts(self.issue_root(182), 31)), 1)
        self.assertEqual(self.own_roots(31), [])
        self.assertEqual(len(self.everywhere(31)), 1)

    def test_an_excluded_first_issue_hands_the_binding_to_the_next_one(self):
        """L1-GIS-MT-051 第一个关联 Issue 被排除：下一个可读的是 binding；排除的那个不收任何东西。"""
        self.gitlab.issue_list = [make_issue(182), make_issue(183, confidential=True)]
        self.gitlab.mr_list = [make_mr(iid=31)]
        self.gitlab.closes = {31: [make_issue(183), make_issue(182)]}
        self.run_sync()
        self.assertEqual(self.binding(31), self.issue_root(182))
        self.assertEqual(len(self.everywhere(31)), 1)
        self.touch(31, sha="b" * 40)
        self.assertIn(self.run_sync()["status"], {"ok", "degraded"})

    def test_an_mr_whose_every_closing_issue_is_excluded_is_not_published(self):
        """L1-GIS-MT-052 MR 关联的 Issue 全被排除：MR 不发布（也不自开门牌），不留 binding——沿用旧行为。"""
        self.gitlab.issue_list = [make_issue(183, confidential=True)]
        self.gitlab.mr_list = [make_mr(iid=31)]
        self.gitlab.closes = {31: [make_issue(183)]}
        result = self.run_sync()
        self.assertEqual(result["skipped"]["excluded"], 1)
        self.assertEqual(self.everywhere(31), [])
        self.assertEqual(self.own_roots(31), [])
        self.assertEqual(self.gitlab.mr_note_list.get(31, []), [])

    def test_a_dry_run_of_a_new_mr_writes_nothing(self):
        """L1-GIS-MT-053 dry-run 对还没同步过的 MR 不写任何 Thread 或 binding。"""
        self.two_issues_one_mr()
        self.run_sync(dry_run=True)
        self.assertEqual(self.everywhere(31), [])
        self.assertEqual(self.gitlab.mr_note_list.get(31, []), [])

    def test_a_dry_run_of_an_update_writes_nothing(self):
        """L1-GIS-MT-054 dry-run 对已有 MR 的更新不写任何 Thread。"""
        self.two_issues_one_mr()
        self.run_sync()
        self.touch(31, sha="b" * 40)
        before = (list(self.buzz.writes), list(self.gitlab.writes))
        self.run_sync(dry_run=True)
        self.assertEqual((self.buzz.writes, self.gitlab.writes), before)

    def test_a_dry_run_with_an_origin_writes_nothing(self):
        """L1-GIS-MT-055 dry-run：新 MR 带 origin 时只读校验 origin，什么也不写，也没有停摆。"""
        discussion = self.human_root()
        self.gitlab.issue_list = [make_issue(182)]
        self.gitlab.mr_list = [make_mr(iid=31, description=marker(discussion))]
        self.gitlab.closes = {31: [make_issue(182)]}
        before = (list(self.buzz.writes), list(self.gitlab.writes))
        result = self.run_sync(dry_run=True)
        self.assertEqual((self.buzz.writes, self.gitlab.writes), before)
        self.assertEqual(result["stalled"], [])


# ── 7. crashes ──────────────────────────────────────────────────────────────────────────────────────────────────────


class CrashSafetyTest(SingleThreadCase):
    def test_a_crash_before_the_first_fact_does_not_open_a_second_root_or_lose_the_cross_link(self):
        """L1-GIS-MT-060 binding 先于 MR 事实写入：发首条事实前崩溃，重跑时关联已消失也走 update 路径——不另开 MR 门牌，事实一条，交叉链接不丢不重。"""
        self.two_issues_one_mr(source_branch="spike/no-issue-xx")
        original = SYNC.Syncer._send_message

        def crash_on_the_fact(syncer, content, reply_to=None, mentions=(), project_id=None):
            header = SYNC.header_line(content) or ""
            if "[object:mr]" in header and "[change:lifecycle]" in header:
                raise KeyboardInterrupt("simulated process kill before the MR fact is sent")
            return original(syncer, content, reply_to=reply_to, mentions=mentions, project_id=project_id)

        with mock.patch.object(SYNC.Syncer, "_send_message", crash_on_the_fact):
            with self.assertRaises(KeyboardInterrupt):
                self.run_sync()
        self.gitlab.closes = {}
        self.gitlab.issue_list = []
        self.run_sync()
        self.assertEqual(self.own_roots(31), [], "the rerun must not open a per-MR root")
        first, second = self.issue_root(182), self.issue_root(183)
        self.assertEqual(self.binding(31), first)
        self.assertEqual(len(self.facts(first, 31)), 1)
        self.assertEqual(len(self.xrefs(second, 31)), 1)

    def test_a_crash_around_the_binding_write_does_not_repeat_the_cross_link(self):
        """L1-GIS-MT-061 交叉链接幂等：binding 写入时崩溃，重跑按目标 Thread 里已有的交叉链接去重，不重发；事实只发一条。"""
        self.two_issues_one_mr()
        original = SYNC.Syncer._write_binding

        def crash_on_the_mr_binding(syncer, project_id, object_kind, iid, root):
            if object_kind == "mr":
                raise KeyboardInterrupt("simulated process kill while writing the MR binding")
            return original(syncer, project_id, object_kind, iid, root)

        with mock.patch.object(SYNC.Syncer, "_write_binding", crash_on_the_mr_binding):
            with self.assertRaises(KeyboardInterrupt):
                self.run_sync()
        self.run_sync()
        first, second = self.issue_root(182), self.issue_root(183)
        self.assertEqual(len(self.xrefs(second, 31)), 1)
        self.assertEqual(len(self.facts(first, 31)), 1)
        self.assertEqual(self.binding(31), first)
        self.assertEqual(len(self.everywhere(31)), 2)

    def test_the_cross_link_is_not_sent_again_when_the_thread_already_has_one(self):
        """L1-GIS-MT-062 目标 Thread 里已有该 MR 的交叉链接（例如 binding 备注丢失后重新走「新 MR」路径）：不重发。"""
        self.two_issues_one_mr()
        self.run_sync()
        second = self.issue_root(183)
        self.assertEqual(len(self.xrefs(second, 31)), 1)
        self.gitlab.mr_note_list.clear()  # the binding note is gone: the MR is new again
        self.run_sync()
        self.assertEqual(len(self.xrefs(second, 31)), 1)
        self.assertEqual(len(self.facts(self.issue_root(182), 31)), 1)

    def test_a_crash_between_two_cross_links_sends_only_the_missing_one(self):
        """L1-GIS-MT-063 关联三个 Issue、发第二条交叉链接时崩溃：此时还没有 binding（交叉链接先于 binding）；重跑补上缺的那条，已发的不重发，binding 与事实各一次。"""
        self.gitlab.issue_list = [make_issue(182), make_issue(183), make_issue(184)]
        self.gitlab.mr_list = [make_mr(iid=31)]
        self.gitlab.closes = {31: [make_issue(182), make_issue(183), make_issue(184)]}
        original = SYNC.Syncer._send_message
        crossed = []

        def crash_on_the_second_cross_link(syncer, content, reply_to=None, mentions=(), project_id=None):
            if "[change:xref]" in (SYNC.header_line(content) or ""):
                crossed.append(reply_to)
                if len(crossed) == 2:
                    raise KeyboardInterrupt("simulated process kill between two cross-links")
            return original(syncer, content, reply_to=reply_to, mentions=mentions, project_id=project_id)

        with mock.patch.object(SYNC.Syncer, "_send_message", crash_on_the_second_cross_link):
            with self.assertRaises(KeyboardInterrupt):
                self.run_sync()
        self.assertEqual(self.gitlab.mr_note_list.get(31, []), [], "cross-links come first: no binding yet")
        self.assertEqual(len(self.everywhere(31)), 1)
        self.run_sync()
        first = self.issue_root(182)
        self.assertEqual(self.binding(31), first)
        self.assertEqual(len(self.facts(first, 31)), 1)
        for iid in (183, 184):
            with self.subTest(issue=iid):
                self.assertEqual(len(self.xrefs(self.issue_root(iid), 31)), 1)
        self.assertEqual(len(self.everywhere(31)), 3)

    def test_a_crash_between_two_diff_files_sends_only_the_missing_one_to_the_binding_thread(self):
        """L1-GIS-MT-064 首个提交有两个文件的 Diff，发第二个文件前崩溃：重跑只补缺的文件（按 commit＋file 去重），仍只进 binding Thread。"""
        self.two_issues_one_mr()
        self.enable_diffs("a" * 40, "app/a.dart", "app/b.dart")
        original = SYNC.Syncer._send_diff
        files = []

        def crash_on_the_second_file(syncer, diff, **kwargs):
            files.append(kwargs["file_path"])
            if len(files) == 2:
                raise KeyboardInterrupt("simulated process kill between two Diff files")
            return original(syncer, diff, **kwargs)

        with mock.patch.object(SYNC.Syncer, "_send_diff", crash_on_the_second_file):
            with self.assertRaises(KeyboardInterrupt):
                self.run_sync()
        self.run_sync()
        first, second = self.issue_root(182), self.issue_root(183)
        self.assertEqual(self.diff_files(first), ["app/a.dart", "app/b.dart"])
        self.assertEqual(self.diff_events(second), [])
        self.assertEqual(len(self.facts(first, 31)), 1)


# ── 8. the cross-link message itself ────────────────────────────────────────────────────────────────────────────────


class CrossLinkMessageTest(unittest.TestCase):
    ROOT = "e" * 64

    def fact(self, **overrides):
        return SYNC.mr_fact(make_mr(iid=31, **overrides), PID)

    def render(self, **overrides):
        return SYNC.render_mr_xref(self.fact(**overrides), SYNC.buzz_message_link(CHANNEL, self.ROOT))

    def test_the_header_is_the_documented_one_and_reads_back(self):
        """L1-GIS-MT-070 交叉链接末行是整行匹配的 header：object:mr、change:xref、transition:none，state／draft 取 MR 当时的值；读回得到 project 和 mr。"""
        for state, draft, expected in (("opened", False, ("opened", "no")), ("merged", False, ("merged", "no")),
                                       ("opened", True, ("opened", "yes"))):
            text = self.render(state=state, draft=draft)
            header = SYNC.parse_header(text)
            with self.subTest(state=state, draft=draft):
                self.assertIsNotNone(header)
                self.assertEqual((header["state"], header["draft"]), expected)
                self.assertEqual((header["object"], header["change"], header["transition"], header["project"],
                                  header["mr"]), ("mr", "xref", "none", PID, 31))
                self.assertEqual(text.splitlines()[-1], SYNC.header_line(text))
                self.assertIsNotNone(SYNC.MR_HEADER_RE.fullmatch(text.splitlines()[-1]))

    def test_the_body_names_the_mr_and_points_at_the_binding_thread(self):
        """L1-GIS-MT-071 正文：第一行是「标题 · 作者」样式的标题链接，第二行是 binding Thread 的 Buzz 链接；没有 @、没有 nostr:，标题里的 markdown 和 @ 被中和，不能多出行。"""
        text = self.render(title="a · b [x](http://evil) @carol nostr:npub1x\nfake: line")
        lines = text.splitlines()
        self.assertEqual(len(lines), 3)
        self.assertIn("MR 关联", lines[0])
        self.assertIn(f"]({WEB}/-/merge_requests/31)", lines[0])
        self.assertIn(SYNC.buzz_message_link(CHANNEL, self.ROOT), lines[1])
        self.assertNotIn("@", text)
        self.assertNotIn("nostr:", text)
        self.assertIn("\\[x\\]", lines[0], "the title's brackets are escaped, so it cannot forge a link")

    def test_posted_means_a_desk_xref_for_this_project_and_mr(self):
        """L1-GIS-MT-072 去重只认 Desk 发的、同项目同 MR 的交叉链接；别的 MR、别的项目、别人伪造的、同 MR 的普通事实都不算。"""
        xref = {"pubkey": DESK, "kind": 9, "content": self.render()}
        fact = {"pubkey": DESK, "kind": 9, "content": SYNC.render_mr_message(self.fact(), "lifecycle", first=True)}
        forged = {"pubkey": PERSON, "kind": 9, "content": self.render()}
        posted = lambda events, project=PID, iid=31: SYNC.mr_xref_posted(events, DESK, project, iid)  # noqa: E731
        self.assertTrue(posted([xref]))
        self.assertFalse(posted([]))
        self.assertFalse(posted([fact]))
        self.assertFalse(posted([forged]))
        self.assertFalse(posted([xref], iid=32))
        self.assertFalse(posted([xref], project=PID + 1))

    def test_a_cross_link_is_not_an_mr_fact_for_any_reader_of_the_thread(self):
        """L1-GIS-MT-073 交叉链接不是 MR 事实：上一版事实、事实链的接续点、「组 Thread」判断和 reviewable 判断都不把它算进去。"""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        syncer = SYNC.Syncer({
            "channel_id": CHANNEL, "publisher_pubkey": DESK, "since": SINCE, "include_confidential": False,
            "exclude": [], "diff": {"enabled": False, "private": False},
            "gitlab": {"base_url": "http://127.0.0.1:8929", "token_env": "NH_DESK_GITLAB_TOKEN",
                       "bot_user_id": BOT_ID, "bot_username": BOT, "projects": [PID]},
            "buzz": {}, "people": {}}, FakeGitLab(), FakeBuzz(), state_dir=Path(tmp.name))
        fact_text = SYNC.render_mr_message(self.fact(title="真事实"), "lifecycle", first=True)
        events = [
            {"id": "1" * 64, "pubkey": DESK, "kind": 9, "created_at": 10, "tags": [["h", CHANNEL]], "content": fact_text},
            {"id": "2" * 64, "pubkey": DESK, "kind": 9, "created_at": 20, "tags": [["h", CHANNEL]],
             "content": self.render(title="交叉链接")},
        ]
        previous = SYNC.previous_mr_fact_from_thread(events, DESK, PID, 31)
        self.assertEqual(previous["title"], "真事实")
        self.assertEqual(syncer._mr_chain_parent(events, "0" * 64, PID, 31), "1" * 64)
        other = SYNC.render_mr_message(SYNC.mr_fact(make_mr(iid=32), PID), "lifecycle", first=True)
        events.append({"id": "3" * 64, "pubkey": DESK, "kind": 9, "created_at": 30, "tags": [["h", CHANNEL]],
                       "content": other})
        self.assertTrue(syncer._thread_is_mr_group(events), "two MRs' facts still are a group thread")
        xref_other = {"id": "4" * 64, "pubkey": DESK, "kind": 9, "created_at": 40, "tags": [["h", CHANNEL]],
                      "content": SYNC.render_mr_xref(SYNC.mr_fact(make_mr(iid=33), PID), SYNC.buzz_message_link(CHANNEL, "5" * 64))}
        self.assertFalse(syncer._thread_is_mr_group([events[0], xref_other]),
                         "one MR's fact plus another MR's cross-link is not a group thread")
        self.assertFalse(syncer._thread_fired_reviewable([events[1], xref_other]))


if __name__ == "__main__":
    unittest.main()
