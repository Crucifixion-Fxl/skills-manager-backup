"""ADR-0015 and the documents that state the MR delivery rule cannot drift apart (skills#131).

ADR-0015 (2026-09-21) replaces the 2026-09-17 "merge-in" delivery (every fact of an MR into every associated Issue
thread and every origin thread) with: facts only in the binding thread, one cross-link (`change:xref`) in every other
related thread when the MR first appears. The 2026-09-17 decision was never an ADR; it lived in the reference and in
US-GIS-09. The only ADR sentence that states multi-thread delivery is ADR-0014 clause 6 (several origins all receive
the object's facts), so that is the clause ADR-0014 has to mark as amended (code-review !955 / !956: a partial
supersession must flag each old clause, not only add a pointer).
"""

import ast
import importlib.util
import json
import re
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
REPO = SKILL.parents[1]
ADR_DIR = REPO / "docs" / "05-adr"
ADR15_NAME = "0015-deliver-an-mr-to-one-thread-and-cross-link-the-others"
ADR14_NAME = "0014-allow-a-human-top-level-message-as-an-origin-root"
ADR15 = ADR_DIR / f"{ADR15_NAME}.md"
ADR14 = ADR_DIR / f"{ADR14_NAME}.md"
ADR_INDEX = ADR_DIR / "README.md"
REFERENCE = SKILL / "references" / "gitlab-buzz-sync.md"
SKILL_MD = SKILL / "SKILL.md"
FCHAC = SKILL / "references" / "fchac-model.md"
STORY = REPO / "docs" / "04-user-stories" / "buzz-agent-setup-gitlab-buzz-sync.md"
PLAN = REPO / "docs" / "plans" / "2026-09-13-buzz-agent-setup-gitlab-buzz-sync-test-plan.md"
SCENARIOS_HTML = REPO / "docs" / "testing" / "scenarios" / "tech-gitlab-buzz-bridge.html"
SCENARIOS_JSON = SKILL / "tests" / "fixtures" / "gitlab_buzz_product_demo" / "scenarios.json"
SCRIPT = SKILL / "scripts" / "gitlab_buzz_sync.py"

# Present-tense statements of the old rule. They may not survive in any document that describes what the sync does
# now (ADRs are history and are checked separately).
STALE = (
    "并入这些 Issue 的 Thread",
    "多 Issue 各投",
    "每个关联 Issue 的 Thread",
    "进入**每个**关联 Issue",
    "全部 MR 事实发进关联 Issue 的 Thread",
    "其它 Issue Thread 收到相同事实",
    "关联 MR 并入 Issue Thread",
    "关联 MR 并入其 Issue Thread",
    "关联 Issue 的 MR 并入",
    "MR 事实才会并入该 Issue 的 Thread",
    "所属的每个 Thread",
    "所有关联 Issue 的 Thread 收到",
    "MR 事实发进每个关联 Issue",
    "Diff 进入每个关联",
    "Diff 归并到每个关联",
    "每个关联 Issue Thread",
    "Diffs go to every associated",
    "其余有效 origin 每轮把本轮新事实各投一份",
    "后来评论里的 origin 仍会向那些 Thread 补一份本轮事实",
)
# Wording that presents a decision of the ADR as still open. ADR-0015 is Accepted; the two judgement points (the late
# Issue, several origins) are settled by it, and a different answer is a change to the ADR, not a pending item in the
# documents that describe what the sync does.
PENDING = ("待评审", "待 jchen", "reviewer decision", "asked to confirm", "approved by jchen on 2026-09-21")
# Test modules whose docstrings describe the delivery rule: none may state the 2026-09-17 rule as current.
RULE_TEST_MODULES = (
    "test_gitlab_buzz_sync_mr_issue_merge.py", "test_gitlab_buzz_sync_mr_single_thread.py",
    "test_gitlab_buzz_sync_human_origin.py", "test_gitlab_buzz_sync_mr_groups.py", "test_gitlab_buzz_sync_mr.py",
    "test_gitlab_buzz_sync_mr_activity.py", "test_gitlab_buzz_product_demo_contract.py",
)
STALE_TEST_DOCSTRING = ("land in the associated Issue's thread",)
SWEPT = {
    "reference": REFERENCE, "SKILL.md": SKILL_MD, "fchac-model": FCHAC, "user stories": STORY,
    "test plan": PLAN, "scenario page": SCENARIOS_HTML, "scenario manifest": SCENARIOS_JSON,
}


def read(path):
    return path.read_text(encoding="utf-8")


def front_matter(text):
    return re.match(r"(?s)^---\n(.*?)\n---\n", text).group(1)


def section(text, title):
    """Body of the markdown section whose heading line contains `title`, up to the next heading of the same or a
    higher level (its own sub-headings belong to it)."""
    match = re.search(rf"^(#+) [^\n]*{re.escape(title)}[^\n]*\n", text, re.M)
    if match is None:
        raise AssertionError(f"no heading containing {title!r}")
    rest = text[match.end():]
    end = re.search(rf"^#{{1,{len(match.group(1))}}} ", rest, re.M)
    return rest if end is None else rest[:end.start()]


def load_sync():
    spec = importlib.util.spec_from_file_location("gitlab_buzz_sync_mt_docs", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Adr0015Test(unittest.TestCase):
    def test_adr_0015_is_accepted_and_complete(self):
        """L1-GIS-MT-100 ADR-0015：Accepted、2026-09-21、deciders jchen、supersedes ADR-0014；至少 A/B/C 三个选项与取舍表；写明后果、回滚办法和确认它的测试。"""
        text = read(ADR15)
        front = front_matter(text)
        for needle in ("status: Accepted", 'date: "2026-09-21"', "deciders:\n  - jchen", f"supersedes:\n  - {ADR14_NAME}",
                       "superseded-by: []"):
            with self.subTest(front=needle):
                self.assertIn(needle, front)
        for heading in ("## Context and Problem Statement", "## Considered Options", "## Trade-off Analysis",
                        "## Decision Outcome", "## Consequences", "## Confirmation"):
            with self.subTest(heading=heading):
                self.assertIn(heading, text)
        options = re.findall(r"(?m)^- \*\*Option ([A-Z]):", section(text, "Considered Options"))
        self.assertGreaterEqual(len(options), 3)
        for needle in ("Rollback", "L1-GIS-MT-", "change:xref", "mr_xrefs", "skills#131"):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)

    def test_adr_0015_records_the_undocumented_decision_it_replaces_and_the_eight_points(self):
        """L1-GIS-MT-101 ADR 写明被取代的 2026-09-17 归并决定（当年没有 ADR，只在 reference 和 US-GIS-09）、实测数据，以及 jchen 批准的 8 点决定（含两个待评审的判断点）。"""
        text = read(ADR15)
        for needle in ("2026-09-17", "merge-in", "never an ADR", "US-GIS-09", "closes_issues", "related_merge_requests",
                       "94 of 285", "561", "binding", "cross-link", "one thread"):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)
        decision = section(text, "Decision Outcome")
        for number in range(1, 9):
            with self.subTest(point=number):
                self.assertRegex(decision, rf"(?m)^\*\*{number}\. ")
        for needle in ("Late Issue", "does not get one", "several origins", "judgement points",
                       "Points 1, 2, 3, 5, 7 and 8 are jchen's decision"):
            with self.subTest(judgement=needle):
                self.assertIn(needle, decision)
        for phrase in PENDING:
            with self.subTest(pending=phrase):
                self.assertNotIn(phrase, text)
        consequences = section(text, "Consequences")
        for needle in ("stale", "legacy", "best-effort", "dormant"):
            with self.subTest(consequence=needle):
                self.assertIn(needle, consequences)

    def test_adr_0014_flags_the_clause_that_is_now_historical(self):
        """L1-GIS-MT-102 ADR-0014 被部分修订：开头有修订横幅、front matter 的 superseded-by 指向 0015、状态仍 Accepted；第 6 条（多个 origin 都收事实）和 Confirmation 逐处标出「已修订」；其余条款不动（code-review !955 红线）。"""
        text = read(ADR14)
        front = front_matter(text)
        head = text.split("## Context and Problem Statement", 1)[0]
        self.assertIn("status: Accepted", front)
        self.assertIn(f"superseded-by:\n  - {ADR15_NAME}", front)
        self.assertRegex(head, r"Amended by ADR-0015")
        self.assertGreaterEqual(text.count("[Amended by ADR-0015"), 2)
        clause6 = re.search(r"\*\*6\. Everything else stays\.\*\*.*", text).group(0)
        self.assertIn("all receive the object's facts", clause6, "the old wording is kept, not rewritten")
        self.assertIn("[Amended by ADR-0015", clause6)
        confirmation = section(text, "Confirmation")
        for needle in ("ADR-0015", "historical", "L1-GIS-HO-054"):
            with self.subTest(needle=needle):
                self.assertIn(needle, confirmation)
        self.assertIn(ADR15_NAME, section(text, "Links"))

    def test_the_index_lists_0015_and_records_the_partial_supersession_of_0014(self):
        """L1-GIS-MT-103 ADR 索引：0015 一行（Accepted、2026-09-21、Supersedes 写 0014 的多 origin 条款）；0014 一行状态带＊、Superseded By 列有 0015。"""
        index = read(ADR_INDEX)
        row15 = next(line for line in index.splitlines() if line.startswith("| 0015 |"))
        self.assertIn(f"{ADR15_NAME}.md", row15)
        self.assertRegex(row15, r"Accepted[^\n]*2026-09-21")
        self.assertIn("0014", row15.rsplit("|", 3)[-3])
        row14 = next(line for line in index.splitlines() if line.startswith("| 0014 |"))
        self.assertIn("Accepted＊", row14)
        self.assertIn("0015", row14.rsplit("|", 2)[-2])


class DocumentsAgreeTest(unittest.TestCase):
    def test_no_document_states_the_old_delivery_rule_in_the_present_tense(self):
        """L1-GIS-MT-110 reference、SKILL.md、fchac-model、用户故事、测试方案、场景页与清单里不再有「每个关联 Issue 各一份」的现在时说法（ADR 是历史，不在此列）。"""
        for name, path in SWEPT.items():
            text = read(path)
            for phrase in STALE:
                with self.subTest(document=name, phrase=phrase):
                    self.assertNotIn(phrase, text)

    def test_no_document_presents_an_adr_decision_as_still_open(self):
        """L1-GIS-MT-116 ADR-0015 是 Accepted：状态与措辞一致（不写「全部由 jchen 批准」又写「待评审」）；reference、SKILL.md、fchac-model、用户故事、测试方案、场景页与清单里没有「待评审」类措辞，判断点的默认值就是现行规则（code-review !971 红线）。"""
        for name, path in SWEPT.items():
            text = read(path)
            for phrase in PENDING:
                with self.subTest(document=name, phrase=phrase):
                    self.assertNotIn(phrase, text)

    def test_test_module_docstrings_do_not_state_the_old_rule_as_current(self):
        """L1-GIS-MT-117 描述投递规则的测试模块的开头 docstring 不再以现在时写旧规则（code-review !971 红线：test_gitlab_buzz_sync_mr_issue_merge.py 开头仍写「关联 MR 的事实进入 associated Issue thread」）。"""
        for name in RULE_TEST_MODULES:
            path = SKILL / "tests" / name
            docstring = ast.get_docstring(ast.parse(read(path))) or ""
            for phrase in (*STALE, *STALE_TEST_DOCSTRING):
                with self.subTest(module=name, phrase=phrase):
                    self.assertNotIn(phrase, docstring)
        merge = ast.get_docstring(ast.parse(read(SKILL / "tests" / "test_gitlab_buzz_sync_mr_issue_merge.py"))) or ""
        for needle in ("ADR-0015", "ONE", "cross-link"):
            with self.subTest(needle=needle):
                self.assertIn(needle, merge)

    def test_reference_states_the_one_thread_rule(self):
        """L1-GIS-MT-111 reference：ADR-0015、唯一目的地、选择顺序、related 只出链接、后到 Issue 不补、Diff 只进 binding、多 origin 其余各收一条交叉链接、存量不搬迁、@ 与 reviewable 只在 binding。"""
        text = read(REFERENCE)
        for needle in ("ADR-0015", "只发进 binding 指向的一个 Thread", "closes_issues", "第一条 origin", "分支族",
                       "只用来生成链接", "不会为了一条链接", "后到的 Issue 不补交叉链接", "Diff", "其余各收一条交叉链接", "存量",
                       "transition:reviewable"):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)
        line9 = next(line for line in text.splitlines() if line.startswith("- 每个 Issue 对应唯一一个 Thread"))
        for needle in ("binding", "交叉链接"):
            self.assertIn(needle, line9)
        policy = next(line for line in text.splitlines() if line.startswith("- **通知政策"))
        self.assertIn("交叉链接", policy)

    def test_the_cross_link_message_is_documented_and_agrees_with_the_code(self):
        """L1-GIS-MT-112 消息协议里有交叉链接的 header 和正文格式；去重章节写明按 Thread 内已有的交叉链接去重；stdout 表有 mr_xrefs；文档里的 header 就是代码渲染出来的那一种。"""
        text = read(REFERENCE)
        documented = ("[gitlab-notify:v1][object:mr][state:<opened|merged|closed|locked>][draft:<yes|no>]"
                      "[change:xref][transition:none][project:<id>][mr:<iid>]")
        self.assertIn(documented, text)
        self.assertRegex(text, r"change:<lifecycle\|update\|activity>")
        self.assertIn("xref 不是事实变化", text)
        dedupe = section(text, "去重与恢复")
        for needle in ("`change:xref`", "交叉链接", "mr_xref_posted"):
            with self.subTest(dedupe=needle):
                self.assertIn(needle, dedupe)
        self.assertIn("| `mr_xrefs` |", text)
        sync = load_sync()
        fact = sync.mr_fact({
            "iid": 31, "title": "t", "state": "opened", "draft": False, "sha": "a" * 40, "source_branch": "a",
            "target_branch": "main", "labels": [], "reviewers": [], "author": {"username": "alice"},
            "web_url": "http://gitlab.test/p/-/merge_requests/31",
        }, 481)
        header = sync.render_mr_xref(fact, "buzz://message?channel=c&id=r&thread=r").splitlines()[-1]
        self.assertEqual(re.sub(r"\[state:[a-z]+\]\[draft:(yes|no)\]", "[state:<opened|merged|closed|locked>][draft:<yes|no>]",
                                header).replace("[project:481]", "[project:<id>]").replace("[mr:31]", "[mr:<iid>]"),
                         documented)

    def test_skill_md_index_and_rules_say_the_same_thing(self):
        """L1-GIS-MT-113 SKILL.md 的 description、同步索引行和「绑定」规则：MR 事实只进 binding 的一个 Thread，其余关联处一条交叉链接；不再说「关联 MR 并入 Issue Thread」。"""
        text = read(SKILL_MD)
        description = re.search(r"(?m)^description: (.*)$", text).group(1)
        self.assertIn("交叉链接", description)
        row = next(line for line in text.splitlines() if line.startswith("| **GitLab → Buzz 全量变更同步"))
        self.assertIn("交叉链接", row)
        binding = next(line for line in text.splitlines() if line.startswith("- **绑定**"))
        for needle in ("binding", "交叉链接", "ADR-0015"):
            with self.subTest(binding=needle):
                self.assertIn(needle, binding)
        self.assertIn("ADR-0015", read(FCHAC))

    def test_user_stories_describe_one_thread_and_the_cross_link(self):
        """L1-GIS-MT-114 用户故事 US-GIS-08／09：Diff 和事实只进 binding Thread，其他关联 Issue 首次收一条交叉链接，之后不再收；已绑定的 MR 不因后来出现的 Issue 扩散。"""
        text = read(STORY)
        us08 = section(text, "US-GIS-08")
        us09 = section(text, "US-GIS-09")
        for name, body in (("US-GIS-08", us08), ("US-GIS-09", us09)):
            with self.subTest(story=name):
                self.assertIn("binding", body)
        self.assertIn("交叉链接", us09)
        self.assertIn("后到", us09)
        self.assertIn("ADR-0015", us09)

    def test_test_plan_and_scenarios_carry_the_new_cases(self):
        """L1-GIS-MT-115 测试方案列出 L1-GIS-MT 系列并改写被取代的 L1-GIS-162..201 行；场景页与清单里的场景 13 是「Diff 只进 binding Thread」。"""
        plan = read(PLAN)
        self.assertIn("L1-GIS-MT-", plan)
        self.assertIn("ADR-0015", plan)
        scene = next(item for item in json.loads(read(SCENARIOS_JSON)) if item["title"].startswith("Diff"))
        self.assertEqual(scene["title"], "Diff 只进 MR 的 binding Thread")
        html = read(SCENARIOS_HTML)
        self.assertIn("Diff 只进 MR 的 binding Thread", html)
        self.assertNotIn("Diff 进入每个关联 Issue Thread", html)


if __name__ == "__main__":
    unittest.main()
