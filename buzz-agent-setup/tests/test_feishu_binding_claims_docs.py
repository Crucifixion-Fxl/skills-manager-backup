"""文档契约：频道↔飞书群绑定的跨机认领（ADR-0022，infra/buzz-deploy#97）。"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
REPO = SKILL_DIR.parents[2]
TWO_WAY = SKILL_DIR / "references" / "feishu-two-way-sync.md"
GROUP_SYNC = SKILL_DIR / "references" / "feishu-group-sync.md"
RUNBOOK = SKILL_DIR / "references" / "local-upgrade-runbook.md"
SKILL = SKILL_DIR / "SKILL.md"
SCRIPTS_README = SKILL_DIR / "references" / "scripts" / "README.md"
ADR_DIR = REPO / "docs" / "agent-harness" / "adr"
ADR = ADR_DIR / "0022-claim-a-channel-to-group-binding-with-a-lease-in-the-mirrors-kind-30177.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    start = text.index(heading)
    nxt = re.search(r"^## ", text[start + len(heading):], re.MULTILINE)
    return text[start:start + len(heading) + nxt.start()] if nxt else text[start:]


class ClaimReference(unittest.TestCase):
    def test_the_two_way_reference_explains_the_claims(self):
        """L1-DOC-BC01: feishu-two-way-sync.md 有「跨机绑定认领」一节：写在哪（镜像 30177、owner 签名、chat_ref 的算法）、租期与心跳、
        谁的认领算、胜负、输了怎样、读不到 / 写不了、接管、预检、开关、锁、公开可读的代价。"""
        self.assertIn("## 跨机绑定认领", _read(TWO_WAY))
        text = _section(_read(TWO_WAY), "## 跨机绑定认领")
        for needle in ("ADR-0022", "`feishu.bindings`", "`chat_ref`", "buzz-feishu-chat:v1:", "`signer_env_file`", "NIP-OA",
                       "30 分钟", "10 分钟", "`claimed_at`", "`takeover_of`", "owner/admin", "`claim_conflict`", "这边已停",
                       "`claims_unreadable`", "`claim_publish_failed`", "`claim_takeovers`", "`round --take-over`",
                       "`claimed_by_other_mirror`", "`binding_claim`", "锁", "`policy`", "全员可读"):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)
        self.assertLessEqual(len(_read(TWO_WAY).splitlines()), 600)

    def test_the_group_sync_reference_lists_the_new_surface(self):
        """L1-DOC-BC02: feishu-group-sync.md：round 的 `--take-over`、预检阻断项 claimed_by_other_mirror、配置键 binding_claim、
        state 字段 claim_notes / claim_takeovers，换绑一节说明旧认领与 --take-over。"""
        text = _read(GROUP_SYNC)
        for needle in ("[--take-over]", "`claimed_by_other_mirror`", "`binding_claim`", "`claim_notes`", "`claim_takeovers`"):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)
        rebind = _section(text, "## 换绑到另一个飞书群")
        self.assertIn("--take-over", rebind)

    def test_the_runbook_says_how_to_move_a_binding_to_another_host(self):
        """L1-DOC-BC03: 本机升级清单写明换机器交接的两种做法：拷 state 目录（0600）就不会重复；不拷则新机器首轮只回看重叠窗口、
        可能重发一次；以及没升级的机器不发认领、发现不了。"""
        text = _read(RUNBOOK)
        for needle in ("ADR-0022", "state 目录", "0600", "重叠窗口", "重发", "没升级", "`--take-over`"):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)

    def test_the_scripts_readme_does_not_conflate_claims_with_the_bridge_identity_fetch(self):
        """L1-DOC-BC06: code review note (5f0d2ae5 round) — the buzz_feishu_group_sync.py entry opens by describing
        the bridge-fetched person-identity binding aborting the whole round on failure (#77, unrelated to ADR-0022).
        Its own 跨机绑定认领 clause must say claim reads are independent of bridge and do not abort the round; only
        an unreadable claim pauses membership (claims_unreadable), so a reader cannot conflate the two "binding"s."""
        text = _read(SCRIPTS_README)
        row = next(line for line in text.splitlines() if "buzz_feishu_group_sync.py" in line and "跨机绑定认领" in line)
        for needle in ("不依赖 bridge", "不中止整轮", "只暂停成员", "`claims_unreadable`"):
            with self.subTest(needle=needle):
                self.assertIn(needle, row)

    def test_the_entry_points_link_the_decision(self):
        """L1-DOC-BC04: SKILL.md 的飞书群路由行提到跨机认领与 ADR-0022；ADR 索引有 0022 一行、文件存在且标题编号一致。"""
        skill = _read(SKILL)
        row = next(line for line in skill.splitlines() if line.startswith("| **Buzz Channel ↔ 飞书群（本地 CLI 路线）**"))
        self.assertIn("ADR-0022", row)
        self.assertIn("认领", row)
        self.assertIn("# ADR-0022:", _read(ADR))
        index = _read(ADR_DIR / "README.md")
        self.assertIn("| 0022 | [Claim a channel-to-group binding with a lease in the mirror's kind:30177]"
                      "(0022-claim-a-channel-to-group-binding-with-a-lease-in-the-mirrors-kind-30177.md)", index)


    def test_the_adr_trade_off_table_matches_the_real_chat_ref_shape(self):
        """L1-DOC-BC05: code review note_644684-line drift — the trade-off table must describe chat_ref (64-hex
        SHA-256, no oc_ prefix) rather than reading like it describes the raw Feishu chat_id's own oc_+32-hex shape."""
        text = _read(ADR)
        row = next(line for line in text.splitlines() if line.startswith("| chat_id 是否外露"))
        self.assertIn("`chat_ref`", row)
        self.assertIn("64 位 hex", row)
        self.assertIn("不含 `oc_` 前缀", row)


if __name__ == "__main__":
    unittest.main()
