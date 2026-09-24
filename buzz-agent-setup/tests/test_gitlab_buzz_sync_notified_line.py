"""Every sync message that carries `p` tags also shows, in its body, who was notified.

The `p` tag (`--mention <pubkey>`) is what notifies; the body used to say nothing about it. Such a message now
carries one visible line `🔔 通知 @周旭兆 @刘海旗` just above the machine header: a real `@<display name>` for each
notified person (the client renders it as a highlighted mention). A display name is only used when it is a plain
token that no other channel member's name contains, so the CLI can never resolve it to somebody else; otherwise
the line falls back to the plain GitLab username with no `@`. Messages with no `p` tag get no line, the header,
facts parsing, dedupe and size guard keep working, and GitLab-authored text stays neutralized.
"""

import importlib.util
import unittest
from pathlib import Path

try:
    from .test_gitlab_buzz_sync_mentions import (
        ALICE, BOB, CAROL, DAVE, PEOPLE, FakeBuzz, SYNC as RUNTIME, SyncCase, make_issue, make_mr, note, users,
    )
except ImportError:  # imported as a top-level module
    from test_gitlab_buzz_sync_mentions import (
        ALICE, BOB, CAROL, DAVE, PEOPLE, FakeBuzz, SYNC as RUNTIME, SyncCase, make_issue, make_mr, note, users,
    )

SKILL = Path(__file__).resolve().parents[1]
SCRIPT = SKILL / "scripts" / "gitlab_buzz_sync.py"


def load_module():
    spec = importlib.util.spec_from_file_location("gitlab_buzz_sync_notified_line", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SYNC = load_module()

PREFIX = "🔔 通知 "
NAMES = {ALICE: "艾丽丝", BOB: "鲍勃", CAROL: "卡罗尔", DAVE: "戴夫"}


def notified_lines(content):
    return [line for line in content.split("\n") if line.startswith(PREFIX)]


def facts_with_mentions(writes):
    return [(reply, content, mentions) for reply, content, mentions in writes if mentions]


class NotifiedTokensTest(unittest.TestCase):
    def test_unique_plain_display_names_become_real_at_mentions_in_mention_order(self):
        """L1-GIS-NL-001 每个被通知的人写成 @显示名，按 @ 的顺序，同一个人只出现一次。"""
        self.assertEqual(SYNC.notified_tokens([BOB, ALICE, BOB], PEOPLE, NAMES), ["@鲍勃", "@艾丽丝"])

    def test_duplicate_display_name_falls_back_to_the_plain_username(self):
        """L1-GIS-NL-002 两个成员同名（CLI 会判为有歧义）时不写 @，退回纯文字用户名。"""
        names = {ALICE: "阿明", BOB: "阿明", CAROL: "卡罗尔"}
        self.assertEqual(SYNC.notified_tokens([ALICE, CAROL], PEOPLE, names), ["alice", "@卡罗尔"])

    def test_a_name_contained_in_another_members_name_is_not_used(self):
        """L1-GIS-NL-003 显示名是别的成员名字的子串（CLI 可能按子串解析）就不写 @；较长的那个仍可以。"""
        names = {ALICE: "chen", BOB: "chen2", CAROL: "卡罗尔"}
        self.assertEqual(SYNC.notified_tokens([ALICE, BOB], PEOPLE, names), ["alice", "@chen2"])

    def test_unsafe_or_missing_display_names_fall_back_without_an_at(self):
        """L1-GIS-NL-004 含空格/@/冒号/引号、空、保留词（all/everyone）或没有档案的，一律退回纯文字，绝不出现 ASCII @ 之外的注入。"""
        names = {ALICE: "陈 敬敏", BOB: "a@b", CAROL: "all", DAVE: ""}
        out = SYNC.notified_tokens([ALICE, BOB, CAROL, DAVE], PEOPLE, names)
        self.assertEqual(out, ["alice", "bob", "carol", "dave"])
        self.assertEqual(SYNC.notified_tokens([ALICE], PEOPLE, {}), ["alice"])
        self.assertEqual(SYNC.notified_tokens([ALICE], PEOPLE, None), ["alice"])

    def test_unmapped_pubkey_without_a_profile_shows_a_short_pubkey(self):
        """L1-GIS-NL-005 不在 people 里又取不到档案的人（例如 owner）用 pubkey 前 8 位缩写，且没有 @。"""
        owner = "0a" * 32
        self.assertEqual(SYNC.notified_tokens([owner, CAROL], PEOPLE, {}), ["0a0a0a0a…", "carol"])

    def test_fallback_username_is_neutralized_like_other_gitlab_text(self):
        """L1-GIS-NL-006 退回的用户名走与其他 GitLab 文本相同的中和：没有 ASCII @ 与 nostr:。"""
        weird = {"a@b": ALICE, "nostr:npub1x": BOB}
        out = SYNC.notified_tokens([ALICE, BOB], weird, {})
        for token in out:
            self.assertNotIn("@", token)
            self.assertNotIn("nostr:", token)


class NotifiedLineHelperTest(unittest.TestCase):
    CONTENT = "\n".join([
        "📋 **已打开** · [#5 Issue 5](http://h/-/issues/5)",
        "assignees carol",
        "[gitlab-notify:v1][object:issue][type:feature][status:ready][state:opened][change:routing]"
        "[project:481][issue:5]",
    ])

    def test_line_goes_just_above_the_header_which_stays_last(self):
        """L1-GIS-NL-007 提示行在机器头部行之上，头部行仍是最后一行且能解析；原有正文行原样保留。"""
        out = SYNC.with_notified_line(self.CONTENT, ["@卡罗尔", "@鲍勃"])
        lines = out.split("\n")
        self.assertEqual(lines[-1], self.CONTENT.split("\n")[-1])
        self.assertEqual(SYNC.parse_header(out)["issue"], 5)
        self.assertEqual(lines[-2], PREFIX + "@卡罗尔 @鲍勃")
        self.assertEqual(lines[:2], self.CONTENT.split("\n")[:2])

    def test_no_tokens_leaves_the_content_untouched(self):
        """L1-GIS-NL-008 没有被通知的人就不加提示行，内容原样返回。"""
        self.assertEqual(SYNC.with_notified_line(self.CONTENT, []), self.CONTENT)

    def test_legacy_header_first_content_keeps_its_header_first(self):
        """L1-GIS-NL-009 旧格式（头部在第一行）的消息，提示行加在正文末尾，头部仍在第一行。"""
        header = self.CONTENT.split("\n")[-1]
        legacy = "\n".join([header, "title: Issue 5", "assignees: carol"])
        out = SYNC.with_notified_line(legacy, ["carol"])
        self.assertEqual(out.split("\n")[0], header)
        self.assertEqual(out.split("\n")[-1], PREFIX + "carol")

    def test_a_trailing_machine_line_stays_the_last_body_line(self):
        """L1-GIS-NL-010 正文末尾如果是 note:/events: 机器行，提示行放在它前面，按末行扫描的读取不受影响。"""
        header = self.CONTENT.split("\n")[-1]
        body = ["💬 **评论** · x", "by: alice", "", "hello", "note: 91"]
        out = SYNC.with_notified_line("\n".join([*body, header]), ["@鲍勃"])
        lines = out.split("\n")
        self.assertEqual(lines[-1], header)
        self.assertEqual(lines[-2], "note: 91")
        self.assertIn(PREFIX + "@鲍勃", lines)

    def test_line_is_dropped_rather_than_pushing_content_past_the_byte_limit(self):
        """L1-GIS-NL-011 内容已经贴近 CLI 上限时宁可不加提示行，也不让整条消息被拒。"""
        pad = "x" * (SYNC.CONTENT_BYTE_LIMIT - len(self.CONTENT.encode("utf-8")) - 3)
        big = "\n".join([*self.CONTENT.split("\n")[:-1], pad, self.CONTENT.split("\n")[-1]])
        self.assertLessEqual(len(big.encode("utf-8")), SYNC.CONTENT_BYTE_LIMIT)
        self.assertEqual(SYNC.with_notified_line(big, ["@卡罗尔"]), big)


class NamedBuzz(FakeBuzz):
    """A fake relay that also serves member profiles (`buzz users get --pubkey …` display names)."""

    def __init__(self):
        super().__init__()
        self.name_reads = 0
        self.fail_names = False

    def member_names(self, pubkeys):
        self.name_reads += 1
        if self.fail_names:
            raise RUNTIME.SyncError("Buzz profile lookup failed")  # the class the Syncer under test catches
        return {key: NAMES[key] for key in pubkeys if key in NAMES}


class NotifiedLineInSyncTest(SyncCase):
    def setUp(self):
        super().setUp()
        self.buzz = NamedBuzz()
        self.buzz.roles = {key: "member" for key in PEOPLE.values()}

    def test_issue_first_fact_shows_a_real_mention_and_the_plaque_shows_nobody(self):
        """L1-GIS-NL-101 新 Issue 首个事实 @ assignee 并写出 @显示名；门牌根和没有 @ 的消息不加提示行。"""
        self.gitlab.issue_list = [make_issue(5, assignees=users("carol", "erin"))]
        self.run_sync()
        tagged = facts_with_mentions(self.buzz.writes)
        self.assertEqual(len(tagged), 1)
        _, content, mentions = tagged[0]
        self.assertEqual(mentions, (CAROL,))
        self.assertEqual(notified_lines(content), [PREFIX + "@卡罗尔"])
        for _, other, other_mentions in self.buzz.writes:
            if not other_mentions:
                self.assertEqual(notified_lines(other), [])

    def test_explicit_p_tags_are_unchanged_by_the_line(self):
        """L1-GIS-NL-102 提示行不改变显式 p tag：仍恰为 mentions，显示的每个名字都对应一个 mentions。"""
        self.gitlab.issue_list = [make_issue(5, assignees=users("carol", "bob"))]
        self.run_sync()
        tagged = facts_with_mentions(self.buzz.writes)
        self.assertEqual(len(tagged), 1)
        _, content, mentions = tagged[0]
        shown = notified_lines(content)[0][len(PREFIX):].split(" ")
        self.assertEqual(sorted(shown), sorted("@" + NAMES[key] for key in mentions))
        for event in self.buzz.events:
            if notified_lines(event["content"]):
                self.assertEqual({t[1] for t in event["tags"] if t[0] == "p"}, set(mentions))

    def test_comment_message_lists_the_notified_people(self):
        """L1-GIS-NL-103 评论消息：@ 了谁就显示谁（名字、顺序与 p tag 一致），头部行仍在最后。"""
        self.gitlab.issue_list = [make_issue(5, assignees=users("bob", "alice"))]
        self.run_sync()
        self.gitlab.issue_notes[5] = [note(91, "alice", "ping @carol please")]
        self.gitlab.issue_list = [make_issue(5, assignees=users("bob", "alice"),
                                             updated_at="2026-09-13T02:30:00Z")]
        self.run_sync()
        reply, content, mentions = self.last()
        self.assertEqual(notified_lines(content), [PREFIX + " ".join("@" + NAMES[key] for key in mentions)])
        self.assertEqual(SYNC.parse_header(content)["issue"], 5)
        self.assertEqual(content.split("\n")[-1], SYNC.header_line(content))

    def test_rerun_neither_resends_nor_treats_the_line_as_a_content_change(self):
        """L1-GIS-NL-104 带提示行的消息被读回做差异/去重时不当成内容变化：再跑两轮零新写入，评论不重复发。"""
        self.gitlab.issue_list = [make_issue(5, assignees=users("carol"))]
        self.run_sync()
        self.gitlab.issue_notes[5] = [note(91, "bob", "hello")]
        self.gitlab.issue_list = [make_issue(5, assignees=users("carol"), updated_at="2026-09-13T02:30:00Z")]
        self.run_sync()
        count = len(self.buzz.writes)
        self.run_sync()
        self.run_sync()
        self.assertEqual(len(self.buzz.writes), count)

    def test_mr_reviewable_message_lists_the_notified_reviewers(self):
        """L1-GIS-NL-105 MR 变可评审的消息显示被 @ 的 reviewer。"""
        self.gitlab.mr_list = [make_mr(31, reviewers=users("alice", "bob"))]
        self.run_sync()
        tagged = facts_with_mentions(self.buzz.writes)
        self.assertTrue(tagged)
        _, content, mentions = tagged[0]
        shown = notified_lines(content)
        self.assertEqual(len(shown), 1)
        for key in mentions:
            self.assertIn("@" + NAMES[key], shown[0])

    def test_profile_lookup_failure_falls_back_to_plain_names_and_does_not_fail_the_round(self):
        """L1-GIS-NL-106 读档案失败不让整轮失败：mentions 不变，提示行退回纯文字用户名（没有 @）。"""
        self.buzz.fail_names = True
        self.gitlab.issue_list = [make_issue(5, assignees=users("carol"))]
        summary = self.run_sync()
        self.assertEqual(summary["status"], "ok")
        _, content, mentions = facts_with_mentions(self.buzz.writes)[0]
        self.assertEqual(mentions, (CAROL,))
        self.assertEqual(notified_lines(content), [PREFIX + "carol"])

    def test_profiles_are_read_once_per_round_not_once_per_message(self):
        """L1-GIS-NL-107 同一轮内档案只读一次（缓存），不是每条消息重读。"""
        self.gitlab.issue_list = [make_issue(5, assignees=users("carol")),
                                  make_issue(6, assignees=users("bob"))]
        self.run_sync()
        self.assertEqual(len(facts_with_mentions(self.buzz.writes)), 2)
        self.assertEqual(self.buzz.name_reads, 1)

    def test_a_relay_without_profile_reads_still_sends_with_plain_names(self):
        """L1-GIS-NL-108 buzz 客户端没有 member_names（旧适配器）时同样退回纯文字，不报错。"""
        self.buzz = FakeBuzz()
        self.gitlab.issue_list = [make_issue(5, assignees=users("carol"))]
        self.run_sync()
        _, content, mentions = facts_with_mentions(self.buzz.writes)[0]
        self.assertEqual(mentions, (CAROL,))
        self.assertEqual(notified_lines(content), [PREFIX + "carol"])


class NotifiedLineDocsTest(unittest.TestCase):
    def test_reference_and_adr_describe_the_visible_line(self):
        """L1-GIS-NL-201 参考文档写清可见提示行（格式、@显示名的条件、纯文字兜底），ADR-0012 存在并列在索引里。"""
        doc = (SKILL / "references" / "gitlab-buzz-sync.md").read_text(encoding="utf-8")
        self.assertIn("可见提示行", doc)
        self.assertIn("🔔 通知 @", doc)
        self.assertIn("name_fallbacks", doc)
        adr_dir = SKILL.parents[1] / "docs" / "05-adr"
        adr = adr_dir / "0012-show-who-was-notified-with-a-visible-mention-line.md"
        self.assertTrue(adr.is_file())
        self.assertIn("status: Accepted", adr.read_text(encoding="utf-8"))
        self.assertIn("0012-show-who-was-notified-with-a-visible-mention-line.md",
                      (adr_dir / "README.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
