"""文档契约：agent 被拉进没有绑定 Buzz 频道的飞书群时的提示（ADR-0023，engineering/skills#162）。"""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
REPO = SKILL_DIR.parents[2]
JOIN_DOC = SKILL_DIR / "references" / "agent-channel-join.md"
RUNBOOK = SKILL_DIR / "references" / "local-upgrade-runbook.md"
SKILL = SKILL_DIR / "SKILL.md"
EXAMPLE = SKILL_DIR / "references" / "scripts" / "buzz-agent-join.example.json"
ADR_DIR = REPO / "docs" / "agent-harness" / "adr"
ADR = ADR_DIR / "0023-tell-an-unbound-feishu-group-why-an-invited-agent-cannot-work-there.md"

sys.path.insert(0, str(SKILL_DIR / "scripts"))
import buzz_agent_join_requests as join  # noqa: E402


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    start = text.index(heading)
    nxt = re.search(r"^## ", text[start + len(heading):], re.MULTILINE)
    return text[start:start + len(heading) + nxt.start()] if nxt else text[start:]


class UnboundInviteDocs(unittest.TestCase):
    def test_the_join_reference_explains_the_prompt(self) -> None:
        """L1-DOC-UI01: agent-channel-join.md「被拉进未绑定的飞书群」一节：入口、配置键、权威来源、两段原文、不刷屏与送达、边界。"""
        self.assertIn("## 被拉进未绑定的飞书群", _read(JOIN_DOC))
        text = _section(_read(JOIN_DOC), "## 被拉进未绑定的飞书群")
        for needle in ("ADR-0023", "ADR-0022", "`feishu`", "`lark_cli`", "`feishu_unbound_prompt`", "120 秒", "基线",
                       "暂时无法确认", "私聊", "回读", "45 分钟", "`feishu-invite-state.json`", "`im:chat:readonly`",
                       "`im:message:send_as_bot`", "`im:message:readonly`", "不自动", "owner 同意", "先升级"):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)
        for status in ("unbound", "unknown"):
            opening, reason, step = join.invite_message(status, "{name}", "unreadable").split("\n")
            with self.subTest(status=status):
                self.assertIn(reason, text)
                self.assertIn(step, text)

    def test_the_example_config_carries_the_feishu_block_and_validates(self) -> None:
        """L1-DOC-UI02: 示例配置写了 lark_cli 与 agent 的 feishu 块，并且脚本接受它。"""
        value = json.loads(_read(EXAMPLE))
        self.assertIn("lark_cli", value)
        self.assertEqual(set(value["agents"][0]["feishu"]), {"app_id", "lark_config_dir", "lark_data_dir"})
        join.validate_config(value)

    def test_the_runbook_lists_the_audit_codes_and_the_order(self) -> None:
        """L1-DOC-UI03: 升级清单列出审计失败码与整改办法，并写明先把所有机器的群同步升到认领版再开。"""
        text = _read(RUNBOOK)
        for needle in ("ADR-0023", "`feishu_invite_profile_missing`", "`feishu_invite_profile_invalid`",
                       "`feishu_invite_capability_gap`", "`feishu_invite_capability_unverified`",
                       "`feishu_invite_prompt_disabled`", "feishu_scope_apply_url.py", "认领"):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)

    def test_the_entry_points_link_the_decision(self) -> None:
        """L1-DOC-UI04: SKILL.md 的入群申请路由行提到未绑定飞书群提示与 ADR-0023；ADR 索引有 0023 且文件标题编号一致。"""
        row = next(line for line in _read(SKILL).splitlines() if line.startswith("| **把 agent 拉进新频道（邀请即申请）**"))
        self.assertIn("ADR-0023", row)
        self.assertIn("未绑定", row)
        self.assertIn("# ADR-0023:", _read(ADR))
        self.assertIn("| 0023 | [Tell an unbound Feishu group why an invited agent cannot work there]"
                      "(0023-tell-an-unbound-feishu-group-why-an-invited-agent-cannot-work-there.md)", _read(ADR_DIR / "README.md"))


if __name__ == "__main__":
    unittest.main()
