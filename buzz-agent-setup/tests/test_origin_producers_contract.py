"""Producers of GitLab origin markers must write only ones the sync uses (engineering/skills#103, ADR-0014).

On 2026-09-19 a batch of skill issues (81-99) carried an origin pointing at a *human* Buzz message.
At that time the sync only accepted an origin that named a Desk-published top-level plaque or fact, and
one bad origin stopped the whole channel. The producer skills therefore wrote the origin only after the
root event had been checked, and omitted it for a person's message. ADR-0014 (2026-09-20) lets the HTML
marker name any top-level kind-9 message of the channel, a person's included, so a producer asked to open
an object in the *current thread* writes both lines when the root passes the check, and omits them when
it cannot be checked. A bare `buzz://message?...` link is still parsed as a hint that only leads to a
Desk plaque or fact, so it must not be pasted to quote a person's message.
"""

import importlib.util
import re
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
REPO = SKILL.parents[1]
SCRIPT = SKILL / "scripts" / "gitlab_buzz_sync.py"
REFERENCE = SKILL / "references" / "gitlab-buzz-sync.md"
PRODUCERS = {
    "gitlab-mr": REPO / "skills" / "gitlab-mr" / "SKILL.md",
    "gitlab-issue-sop": REPO / "skills" / "gitlab-issue-sop" / "SKILL.md",
    "milestone-governance": REPO / "docs" / "standards" / "gitlab-milestone-governance.md",
}
CHANNEL = "11111111-2222-4333-8444-555555555555"
DESK = "a" * 64
HUMAN = "b" * 64
ROOT = "c" * 64


def load_sync():
    spec = importlib.util.spec_from_file_location("gitlab_buzz_sync_op", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def event(pubkey, content, *, kind=9, e_tags=()):
    tags = [["h", CHANNEL], *[["e", *tag] for tag in e_tags]]
    return {"id": ROOT, "pubkey": pubkey, "kind": kind, "tags": tags, "content": content}


def section(text, title):
    """Body of the markdown section whose heading line contains `title`, up to the next heading."""
    match = re.search(rf"^#+ [^\n]*{re.escape(title)}[^\n]*\n", text, re.M)
    if match is None:
        raise AssertionError(f"no heading containing {title!r}")
    rest = text[match.end():]
    end = re.search(r"^#+ ", rest, re.M)
    return rest if end is None else rest[:end.start()]


class ProducerRuleAgreesWithTheSyncTest(unittest.TestCase):
    """The documented check is exactly what the sync enforces, so prose and code cannot drift."""

    @classmethod
    def setUpClass(cls):
        cls.sync = load_sync()

    def test_a_human_message_root_is_not_a_desk_plaque_or_fact(self):
        """L1-GIS-OP-001 人类消息根（作者不是 Desk）不是 Desk 门牌／事实：裸深链和 Desk 自己的消息仍用这条老规则。"""
        root = event(HUMAN, "法做 skill 瘦身，可以帮我分析一下吗？")
        self.assertIsNone(self.sync.origin_canonical_root(root, DESK, CHANNEL))

    def test_a_desk_plaque_is_a_valid_origin_root(self):
        """L1-GIS-OP-002 Desk 发布、本频道、顶层、末行是 issue URL 的门牌可以当 origin 根。"""
        plaque = event(DESK, "📋 **#7 x**\ng/p\nhttps://gitlab.example.test/g/p/-/issues/7")
        self.assertEqual(self.sync.origin_canonical_root(plaque, DESK, CHANNEL), ROOT)

    def test_a_desk_reply_is_not_a_top_level_root(self):
        """L1-GIS-OP-003 Desk 的回帖（带 e 标签）不是顶层根，不能直接当 origin。"""
        reply = event(DESK, "📋 **#7 x**\ng/p\nhttps://gitlab.example.test/g/p/-/issues/7", e_tags=[(HUMAN,)])
        self.assertIsNone(self.sync.origin_canonical_root(reply, DESK, CHANNEL))

    def test_a_desk_message_without_plaque_or_header_is_refused(self):
        """L1-GIS-OP-004 Desk 发的普通文字（没有门牌 URL 也没有同步 header）不是门牌／事实。"""
        chat = event(DESK, "收到，我来看看。")
        self.assertIsNone(self.sync.origin_canonical_root(chat, DESK, CHANNEL))

    def test_a_bare_buzz_link_is_parsed_as_an_origin(self):
        """L1-GIS-OP-005 没有 HTML 注释的裸 buzz://message 链接同样被当作 origin（文档必须这么说）。"""
        text = f"背景见 buzz://message?channel={CHANNEL}&id={ROOT}"
        origins = self.sync.parse_origins(text)
        self.assertEqual([(o["channel_id"], o["root_event_id"]) for o in origins], [(CHANNEL, ROOT)])

    def test_a_human_top_level_message_is_a_valid_marker_root(self):
        """L1-GIS-OP-007 ADR-0014：标记可以指向人的顶层消息（kind 9、恰好一个本频道 h、没有 e）；回帖、别的 kind、别的频道不行。"""
        top = event(HUMAN, "法做 skill 瘦身，可以帮我分析一下吗？")
        self.assertEqual(self.sync.origin_top_level_root(top, CHANNEL), ROOT)
        self.assertIsNone(self.sync.origin_top_level_root(event(HUMAN, "回帖", e_tags=[(DESK,)]), CHANNEL))
        self.assertIsNone(self.sync.origin_top_level_root(event(HUMAN, "点赞", kind=7), CHANNEL))
        self.assertIsNone(self.sync.origin_top_level_root(top, "00000000-0000-4000-8000-0000000000c9"))

    def test_a_link_inside_backticks_is_still_an_origin(self):
        """L1-GIS-OP-006 把链接放进反引号也躲不掉：同样被解析成 origin。"""
        origins = self.sync.parse_origins(f"`buzz://message?channel={CHANNEL}&id={ROOT}`")
        self.assertEqual(len(origins), 1)


class ReferenceStatesThePreWriteCheckTest(unittest.TestCase):
    def test_reference_has_the_pre_write_check_with_every_condition(self):
        """L1-GIS-OP-101 参考文档有「写入前检查」，逐条列出 kind 9、本频道、顶层（没有 e）、读得到，以及 Desk 自己的消息只认门牌／header。"""
        text = REFERENCE.read_text(encoding="utf-8")
        self.assertIn("origin 写入前检查", text)
        block = section(text, "origin 写入前检查")
        for needle in ("publisher_pubkey", "pubkey", "kind 9", "`h`", "`e`", "顶层", "[gitlab-notify:v1]",
                       "messages thread", "省略", "ADR-0014", "当前话题"):
            with self.subTest(needle=needle):
                self.assertIn(needle, block)

    def test_reference_says_bare_links_count_as_origins(self):
        """L1-GIS-OP-102 参考文档明确写出：裸 buzz://message 链接同样被当作 origin，不能贴指向人类消息的链接。"""
        block = section(REFERENCE.read_text(encoding="utf-8"), "origin 写入前检查")
        self.assertIn("裸", block)
        self.assertIn("buzz://message", block)
        self.assertIn("反引号", block)
        self.assertIn("忽略", block)  # a bare link to a person's message stays a plain reference (ADR-0011)

    def test_the_reference_no_longer_says_a_human_root_means_omit(self):
        """L1-GIS-OP-104 旧规则「根是人类消息就两行都省略」已删：人的顶层消息现在通过检查就写两行。"""
        text = REFERENCE.read_text(encoding="utf-8")
        self.assertNotRegex(text, r"根是人类消息[^\n]{0,60}两行都省略")

    def test_reference_documents_the_fallback_report_and_the_failure_modes(self):
        """L1-GIS-OP-105 不可用的合法标记自开 root 并记 origin_fallbacks，格式非法仍停摆。"""
        text = REFERENCE.read_text(encoding="utf-8")
        for needle in ("origin_fallbacks", "自开 root", "顶层", "ADR-0014", "回帖", "格式非法", "退出码 2+"):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)
        self.assertRegex(text, r"\| `origin_fallbacks` \|")
        self.assertIn("不停摆", text)
        self.assertIn("Desk runner", text)  # the runner passes the count up

    def test_the_issue_first_flow_and_the_scripts_readme_follow_the_new_rule(self):
        """L1-GIS-OP-106 fchac-model「Issue 先行」第 3 步和 scripts/README 跟上新规则：ADR-0014、人的顶层消息也写两行；省略只在核对不了时。"""
        model = (SKILL / "references" / "fchac-model.md").read_text(encoding="utf-8")
        block = model.split("## Issue 先行", 1)[1].split("\n## ", 1)[0]
        for needle in ("ADR-0014", "两行都写", "顶层", "省略 origin", "Thread 回链"):
            with self.subTest(needle=needle):
                self.assertIn(needle, block)
        self.assertNotIn("只在根是本频道 Desk 门牌／事实时", block)
        readme = (SKILL / "references" / "scripts" / "README.md").read_text(encoding="utf-8")
        self.assertIn("ADR-0014", readme)

    def test_reference_no_longer_says_agents_always_add_the_two_lines(self):
        """L1-GIS-OP-103 旧句「由 gitlab-issue-sop / gitlab-mr 自动带上这两行」（无条件）已删除。"""
        self.assertNotIn("自动带上这两行", REFERENCE.read_text(encoding="utf-8"))


class ProducersOnlyWriteAVerifiedOriginTest(unittest.TestCase):
    def test_no_producer_makes_the_origin_block_unconditional(self):
        """L1-GIS-OP-201 三处生产方都不再无条件要求带 origin（旧措辞已删）。"""
        old = ("必须在**描述末尾**带上 origin 块", "在描述末尾追加两行（Channel UUID", "在 **description 末尾**写 origin 块")
        for name, path in PRODUCERS.items():
            text = path.read_text(encoding="utf-8")
            for phrase in old:
                with self.subTest(producer=name, phrase=phrase):
                    self.assertNotIn(phrase, text)

    def test_every_producer_says_check_the_root_then_write_or_omit(self):
        """L1-GIS-OP-202 每处生产方都写清：在当前话题里开对象、根是本频道顶层消息（人的也行）且核对通过就写两行；核对不了就省略。"""
        for name, path in PRODUCERS.items():
            text = path.read_text(encoding="utf-8")
            with self.subTest(producer=name):
                for needle in ("origin 写入前检查", "省略", "Desk", "当前话题", "顶层", "两行都写", "messages thread",
                               "ADR-0014"):
                    self.assertIn(needle, text)

    def test_no_producer_still_says_a_human_root_means_omit(self):
        """L1-GIS-OP-205 三处生产方都不再写「根是人类消息 → 两行都省略」（旧规则，已被 ADR-0014 取代）。"""
        for name, path in PRODUCERS.items():
            with self.subTest(producer=name):
                self.assertNotRegex(path.read_text(encoding="utf-8"), r"根是人类消息[^\n]{0,60}两行都省略")

    def test_every_producer_warns_about_the_bare_link(self):
        """L1-GIS-OP-203 每处生产方都提醒：裸 buzz://message 链接同样会被当作 origin。"""
        for name, path in PRODUCERS.items():
            text = path.read_text(encoding="utf-8")
            with self.subTest(producer=name):
                self.assertIn("裸", text)
                self.assertIn("buzz://message", text)

    def test_every_producer_names_the_human_root_case_and_what_still_stalls(self):
        """L1-GIS-OP-204 每处生产方都点明人类消息根的处理，以及仍会让对象停摆的写法（格式非法的标记）。"""
        for name, path in PRODUCERS.items():
            text = path.read_text(encoding="utf-8")
            with self.subTest(producer=name):
                self.assertIn("人类消息", text)
                self.assertIn("停摆", text)
                self.assertIn("格式", text)


class AdrHumanRootTest(unittest.TestCase):
    ADR = REPO / "docs" / "05-adr" / "0014-allow-a-human-top-level-message-as-an-origin-root.md"
    ADR_DIR = REPO / "docs" / "05-adr"

    def test_adr_0014_is_accepted_and_complete(self):
        """L1-GIS-OP-301 ADR-0014：Accepted、2026-09-20、deciders jchen；至少 A/B/C 三个选项与取舍表；写明谁能影响往哪个话题发消息、回退与关闭办法。"""
        text = self.ADR.read_text(encoding="utf-8")
        front = re.match(r"(?s)^---\n(.*?)\n---\n", text).group(1)
        for needle in ("status: Accepted", 'date: "2026-09-20"', "deciders:\n  - jchen"):
            self.assertIn(needle, front)
        for heading in ("## Context and Problem Statement", "## Considered Options", "## Trade-off Analysis",
                        "## Decision Outcome", "## Consequences"):
            self.assertIn(heading, text)
        options = re.findall(r"(?m)^- \*\*Option ([A-Z]):", section(text, "Considered Options"))
        self.assertGreaterEqual(len(options), 3)
        for needle in ("ADR-0009", "ADR-0011", "origin_fallbacks", "Rollback", "Who can steer", "#125",
                       "HTML marker", "bare"):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)

    def test_the_two_revised_adrs_point_to_it_without_changing_their_status(self):
        """L1-GIS-OP-302 ADR-0009 和 ADR-0011 只加了指向 ADR-0014 的链接，状态不变；索引有 0014 一行。"""
        for name in ("0009-isolate-object-level-data-errors-in-gitlab-sync.md",
                     "0011-treat-a-bare-buzz-deep-link-as-an-origin-hint.md"):
            text = (self.ADR_DIR / name).read_text(encoding="utf-8")
            with self.subTest(adr=name):
                self.assertIn("0014-allow-a-human-top-level-message-as-an-origin-root.md", text)
                self.assertIn("status: Accepted", text.split("---")[1])
        index = (self.ADR_DIR / "README.md").read_text(encoding="utf-8")
        self.assertRegex(index, r"\| 0014 \|[^\n]*Accepted[^\n]*2026-09-20")

    def test_the_revised_adrs_flag_the_clauses_that_are_now_historical(self):
        """L1-GIS-OP-303 被 ADR-0014 部分修订的 ADR-0009／0011：开头有修订横幅、front matter 的 superseded-by 指向 0014；旧条款（对象级错误清单、HTML 标记不变、Confirmation、Follow-up）逐处标出「已修订／历史」，不留与现行为相反的现在时描述（code-review !955 红线）。"""
        for name in ("0009-isolate-object-level-data-errors-in-gitlab-sync.md",
                     "0011-treat-a-bare-buzz-deep-link-as-an-origin-hint.md"):
            text = (self.ADR_DIR / name).read_text(encoding="utf-8")
            front = re.match(r"(?s)^---\n(.*?)\n---\n", text).group(1)
            head = text.split("## Context and Problem Statement", 1)[0]
            with self.subTest(adr=name):
                self.assertIn("0014-allow-a-human-top-level-message-as-an-origin-root", front)
                self.assertIn("status: Accepted", front)
                self.assertRegex(head, r"Amended by ADR-0014")
        adr9 = (self.ADR_DIR / "0009-isolate-object-level-data-errors-in-gitlab-sync.md").read_text(encoding="utf-8")
        self.assertGreaterEqual(adr9.count("[Amended by ADR-0014"), 2)  # the object-level list and the producers follow-up
        adr11 = (self.ADR_DIR / "0011-treat-a-bare-buzz-deep-link-as-an-origin-hint.md").read_text(encoding="utf-8")
        self.assertGreaterEqual(adr11.count("[Amended by ADR-0014"), 2)  # the marker bullet and the producers bullet
        confirmation = section(adr11, "Confirmation")
        for needle in ("ADR-0014", "historical", "L1-GIS-HO-001", "origin_fallbacks"):
            with self.subTest(needle=needle):
                self.assertIn(needle, confirmation)

    def test_the_index_and_the_new_adr_record_the_partial_supersession(self):
        """L1-GIS-OP-304 索引里 0009、0011 两行的 Superseded By 列有 0014、状态带＊（部分修订）；ADR-0014 的 front matter supersedes 0009、0011（与 ADR-0010 对 0009 的写法一致）。"""
        index = (self.ADR_DIR / "README.md").read_text(encoding="utf-8")
        for number in ("0009", "0011"):
            row = next(line for line in index.splitlines() if line.startswith(f"| {number} |"))
            with self.subTest(row=number):
                self.assertIn("Accepted＊", row)
                self.assertRegex(row.rsplit("|", 2)[-2], r"0014")
        front = re.match(r"(?s)^---\n(.*?)\n---\n", self.ADR.read_text(encoding="utf-8")).group(1)
        for target in ("0009-isolate-object-level-data-errors-in-gitlab-sync",
                       "0011-treat-a-bare-buzz-deep-link-as-an-origin-hint"):
            self.assertIn(f"supersedes:", front)
            self.assertIn(f"  - {target}", front)


if __name__ == "__main__":
    unittest.main()
