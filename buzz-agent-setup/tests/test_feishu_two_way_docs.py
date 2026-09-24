"""文档契约：飞书群与频道的成员、表情双向同步，飞书里同意 agent 入群（ADR-0020，engineering/skills#148）。"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
REPO = SKILL_DIR.parents[1]
TWO_WAY = SKILL_DIR / "references" / "feishu-two-way-sync.md"
GROUP_SYNC = SKILL_DIR / "references" / "feishu-group-sync.md"
JOIN = SKILL_DIR / "references" / "agent-channel-join.md"
README = SKILL_DIR / "references" / "scripts" / "README.md"
SKILL = SKILL_DIR / "SKILL.md"
ADR = REPO / "docs" / "05-adr" / "0020-sync-feishu-group-membership-and-reactions-both-ways.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    start = text.index(heading)
    nxt = re.search(r"^## ", text[start + len(heading):], re.MULTILINE)
    return text[start:start + len(heading) + nxt.start()] if nxt else text[start:]


class TwoWayReference(unittest.TestCase):
    def test_the_reference_stays_under_the_length_limit(self):
        """L1-DOC-TW01: 新参考文档不超过 600 行（主文档已超，新内容不再往里堆）。"""
        self.assertLessEqual(len(_read(TWO_WAY).splitlines()), 600)

    def test_members_section_says_how_it_works_and_what_it_will_not_do(self):
        """L1-DOC-TW02: 成员一节写明开关、基线、冲突、兜底、拒绝与认不出、认人来源、别的机器的镜像、remove_extras 的新含义、身份空间变化，
        以及「不限频道、anyone 的 agent 从飞书拉进来会直接回复」的提醒与防线。"""
        text = _section(_read(TWO_WAY), "## 成员双向同步")
        for needle in ("`membership_sync`", '`"two_way"`', '`"buzz_to_feishu"`', "基线", "以 Buzz 为准", "BULK_REMOVAL_LIMIT",
                       "`--allow-bulk-removal`", "`channel_add_policy`", "/bind/", "`people_cache_file`", "`other_mirror`",
                       "`remove_extras`", "`identity`", "kind 9000", "kind 9001", "`BUZZ_ACP_CHANNELS`", "`owner_only`", "`nobody`",
                       "ADR-0018", "频道 owner", "签名身份"):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)

    def test_the_human_placement_limit_is_stated_honestly(self):
        """L1-DOC-TW03: 写明认人的限制：bridge 只回答本频道现存成员，频道外的人只能靠本频道见过、或本机共享缓存里有；否则只能提示去绑定、
        由管理员在 Buzz 里加。"""
        text = _section(_read(TWO_WAY), "## 成员双向同步")
        for needle in ("只回答本频道现存成员", "30 天", "管理员在 Buzz 里"):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)

    def test_reactions_section(self):
        """L1-DOC-TW04: 表情一节写明开关、两个方向谁来打、feishu-author、batch_query、监看窗口、防回声、CrossMark。"""
        text = _section(_read(TWO_WAY), "## 表情双向同步")
        for needle in ("`reaction_sync`", '`"agents_only"`', "Desk bot", "`feishu-author`", "batch_query", "24 小时", "7 天",
                       "防回声", "CrossMark", "x-auth-tag"):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)

    def test_approvals_section_and_the_setup_step(self):
        """L1-DOC-TW05: 飞书里同意入群一节写明两种做法、join 标签、可信镜像的三个条件、accept_feishu_approvals，以及上线时用 helper 的
        --mirror 声明镜像（没有这一步，飞书里的同意不算、别的机器也会把它当 agent 代发）；写明已接受的风险。"""
        text = _section(_read(TWO_WAY), "## 飞书里同意 agent 入群")
        for needle in ("`/approve JOIN-<id>`", "✅", "`join`", "可信镜像", "`accept_feishu_approvals`", "buzz_agent_feishu_app.py",
                       "`--mirror`", "已接受的风险", "`buzz messages send`"):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)

    def test_state_contract_change_is_stated(self):
        """L1-DOC-TW06: 写明 state 的变化：双向时为本频道成员记 pubkey ↔ 飞书 id（buzz_seen、people_seen），群快照只有飞书 id；单向时照旧不落。"""
        text = _read(TWO_WAY)
        for needle in ("`members_synced`", "`feishu_seen`", "`buzz_seen`", "`member_notes`", "`member_events`", "`people_seen`", "`rwatch`", "`f2r`",
                       "谁是谁"):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)


class Pointers(unittest.TestCase):
    def test_unmanaged_agent_sender_and_reaction_use_channel_desk(self):
        text = _section(_read(GROUP_SYNC), "## 别人的 agent 的飞书应用")
        self.assertIn("Buzz 消息由本频道 Desk bot 发到飞书群", text)
        self.assertIn("Buzz reaction", text)
        self.assertIn("同一个 Desk bot", text)
        self.assertIn("owner bot 不代发消息或 reaction", text)
        self.assertNotIn("消息仍由镜像代发", text)

    def test_usage_guide_is_sent_by_channel_desk(self):
        text = _read(GROUP_SYNC)
        self.assertIn("发送者是本频道 Desk bot", text)
        self.assertIn("owner 应用不代发这份说明", text)
        self.assertNotIn("发送者是 owner 应用的 bot", text)

    def test_the_group_sync_reference_is_an_entrypoint_not_a_monolith(self):
        """L1-DOC-TW14: 主参考只保留 setup 与契约入口，不超过 600 行；消息运行细节与成员/表情双向细节各自链接到专门参考。"""
        text = _read(GROUP_SYNC)
        self.assertLessEqual(len(text.splitlines()), 600)
        self.assertIn("(feishu-message-sync.md)", text)
        self.assertIn("(feishu-two-way-sync.md)", text)
        self.assertNotIn("### 5. **Buzz reaction → 飞书表情**", text)

    def test_the_group_sync_reference_lists_the_keys_and_fields_and_points_here(self):
        """L1-DOC-TW07: 主参考文档的配置表列出三个新键、state 一节列出新字段，并链到新文档；state 标题不再说「没有谁是谁」。"""
        text = _read(GROUP_SYNC)
        for needle in ("`membership_sync`", "`reaction_sync`", "`people_cache_file`", "(feishu-two-way-sync.md)", "`feishu_seen`",
                       "`buzz_seen`", "`member_events`", "`people_seen`", "`rwatch`", "`f2r`"):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)
        self.assertNotIn("## state（0600，不含正文和 secret，没有明文邮箱，也没有「谁是谁」的对应关系）", text)

    def test_the_group_sync_reference_warns_about_unrestricted_agents(self):
        """L1-DOC-TW11: 主参考文档也写明提醒（协调方要求）：没设 BUZZ_ACP_CHANNELS、channel_add_policy 还是 anyone 的 agent 从飞书被拉进来会
        直接开始回复，防线是平台类 owner_only、executor nobody。"""
        text = _read(GROUP_SYNC)
        for needle in ("直接开始回复", "`BUZZ_ACP_CHANNELS`", "`anyone`", "`owner_only`", "`nobody`"):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)

    def test_the_join_runbook_explains_feishu_approvals(self):
        """L1-DOC-TW08: 入群申请运行手册写明 accept_feishu_approvals、可信镜像与 feishu-author，并链到 ADR-0020。"""
        text = _read(JOIN)
        for needle in ("`accept_feishu_approvals`", "可信镜像", "`feishu-author`", "ADR-0020"):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)

    def test_the_join_runbook_explains_a_feishu_member_invite(self):
        """L1-DOC-TW15: 飞书侧加 Agent 由可信镜像写 kind 9000；它只代表管理员通道发起，仍须 owner 审批，
        不能误写成 owner 直接邀请或静默开通；不可信镜像仍按普通邀请者拒绝并说明原因。"""
        text = _read(JOIN)
        for needle in ("飞书群里加", "kind 9000", "仍须 owner 审批", "不可信镜像", "说明原因"):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)

    def test_readme_and_skill_point_to_it(self):
        """L1-DOC-TW09: scripts/README 写明 helper 的 --mirror 和群同步的双向；SKILL.md 的任务表链到新文档。"""
        readme = _read(README)
        for needle in ("--mirror", "ADR-0020", "feishu-two-way-sync.md"):
            with self.subTest(document="README", needle=needle):
                self.assertIn(needle, readme)
        self.assertIn("(references/feishu-two-way-sync.md)", _read(SKILL))


class Adr(unittest.TestCase):
    def test_the_adr_follows_what_was_built(self):
        """L1-DOC-TW10: ADR-0020 与实现一致：同意走镜像的表情（buzz messages send 加不了标签），别的机器的镜像按 other_mirror 跳过，
        state 的变化，Buzz 侧移出不看 remove_extras，认人的限制与共享缓存，以及不限频道 agent 的提醒。"""
        text = _read(ADR)
        for needle in ("`buzz messages send`", "`other_mirror`", "谁是谁", "`people_cache_file`", "`remove_extras`", "`BUZZ_ACP_CHANNELS`",
                       "只回答本频道现存成员", "`owner_only`"):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)
        self.assertNotIn("镜像转进 Buzz 的已绑定成员的消息，也带", text)


class ReviewFixes(unittest.TestCase):
    """!1003 CI code-review：文档与实现一致（ADR-0019 不再说目录 agent 的 bot 不移出、reaction 跳过；README 不再说只同步 agent 的 reaction、
    只认 Buzz 里 owner 的签名）；同意走带标签的回复、同表情共用一个镜像 reaction、重试不重发、helper 一次一个。"""

    ADR19 = REPO / "docs" / "05-adr" / "0019-publish-agent-feishu-app-ids-in-kind-30177-and-relay-by-default.md"

    def test_stale_statements_are_gone(self):
        """L1-DOC-TW12: 过时的说法都改掉，并指向 ADR-0020。"""
        adr19 = _read(self.ADR19)
        for stale in ("它的 reaction 仍然跳过", "它的 bot 不会被自动移出群"):
            with self.subTest(stale=stale):
                self.assertNotIn(stale, adr19)
        self.assertIn("ADR-0020", adr19)
        self.assertNotIn("要么等群成员双向同步（另行讨论）处理", adr19)
        self.assertNotIn("Negative（已由 ADR-0020 解决）", adr19)
        readme = _read(README)
        for stale in ("只同步 agent 的 reaction，由它自己的 bot 打（人的不同步）", "只认 owner 签名的 ✅／`/approve JOIN-<id>`"):
            with self.subTest(stale=stale):
                self.assertNotIn(stale, readme)

    def test_the_approval_reply_and_the_shared_reaction_are_described(self):
        """L1-DOC-TW13: 两份参考文档与 ADR-0020 写明：飞书里的 `/approve` 由镜像签成带 feishu-author 与 join 标签的话题回复（不是表情，relay
        对同一身份、同一目标、同一表情只留一个 reaction）；多人同一表情共用一个镜像 reaction；重试用首次的时间、同一个事件；helper 一次一个运行。"""
        for path in (TWO_WAY, JOIN, ADR):
            text = _read(path)
            for needle in ("同一表情只留一个", "`join`", "`feishu-author`"):
                with self.subTest(document=path.name, needle=needle):
                    self.assertIn(needle, text)
        two_way = _read(TWO_WAY)
        for needle in ("首次的时间", "`.lock`"):
            with self.subTest(needle=needle):
                self.assertIn(needle, two_way)
        self.assertNotIn("镜像再在申请消息上打 ✅/❌", two_way)


if __name__ == "__main__":
    unittest.main()
