"""Buzz → 飞书：本机没有凭据的频道 agent，由镜像（owner 应用）bot 代发（engineering/skills#143）。

背景：一个 agent 可以同时属于多个 Channel（M:N），而每个 Channel 的群同步由**不同的人**在自己机器上跑。agent 的飞书
应用凭据只能留在它自己 owner 的本机（SKILL.md Rule 11），所以别的操作者永远拿不到它的发送能力。今天这种消息直接被
丢掉（`agent_bot_unavailable`），群里看不到这个 agent 的任何回复。

配置 `buzz_unmanaged_agents`（可选）：`"relay"`（缺省，ADR-0019 起）或 `"skip"`（以前的行为，显式写才生效）。`"relay"` 时，
**本机配置里根本没有的**频道 agent（`agents_in_channel()` 给的是频道里所有 role=bot 成员，不看本地配置）由 owner 应用 bot 代发，
署名插入「·助手」区分（`名字（Buzz·助手）：正文`，卡片模式下 speaker 是「名字（助手）」）。

两条边界必须守住，它们正是这个改动最容易破坏的地方：

1. **代发不放宽通知**：p tag 一律不生成 `<at>`，卡片也不点名任何人。这与 agent 用自己 bot 发言那条路径一致（那条路
   径从来不渲染 `<at>`，因为 open_id 属于另一个应用），所以代发不会让一个 agent 的消息比它自己发时更吵。
2. **只覆盖「本机没配」的 agent**：配置里有、只是这一轮没验证过或 bot 还没进群的 agent（`agent_bot_unavailable`）
   不代发——那是暂时且会自愈的状态，代发会造成同一个 agent 一会儿自己说话、一会儿被转述。

reaction 不在范围内：表情仍然只能由 agent 自己的 bot 打，没有凭据就跳过（`route_buzz_reaction` 压根不接这个开关）。

沿用 test_buzz_feishu_group_sync.py 的 FakeWorld / Env / 常量。用例编号从 700 起。
"""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_buzz_feishu_group_sync as base  # noqa: E402
from test_buzz_feishu_group_sync import (  # noqa: E402
    AGENT2_PK, AGENT_APP, AGENT_BOT_MEMBER, AGENT_PK, ALICE_OPEN, ALICE_PK, BOB_PK, FGS, MIRROR_PK,
    OUTSIDER_PK, OWNER_PK, TmpCase, Env, event, eid, write_owner_only,
)


def setUpModule():
    base.setUpModule()


def tearDownModule():
    base.tearDownModule()


LABEL = "助手"
FOREIGN_AGENT_PK = "ac" * 32  # 频道里的 agent（role=bot），但本机配置里没有它 —— 别人的 agent


# ================================ L1 : config ================================


class UnmanagedAgentConfig(TmpCase):
    def raw(self):
        return json.loads(Env(self.tmp).config.read_text())

    def load(self, **extra):
        return FGS.load_config(write_owner_only(self.tmp / "c.json", json.dumps(dict(self.raw(), **extra))))

    def test_the_key_is_optional_and_absent_means_relay(self):
        """L1-FGS-700: `buzz_unmanaged_agents` 可以不写：配置照常加载、`buzz_unmanaged_agent_mode(cfg)` 是 "relay"
        （ADR-0019：缺省代发）；它是可选键，不是必填键。写 "skip" 仍然可以退回以前的丢弃行为。"""
        raw = self.raw()
        self.assertNotIn("buzz_unmanaged_agents", raw)
        cfg = FGS.load_config(write_owner_only(self.tmp / "none.json", json.dumps(raw)))
        self.assertEqual(FGS.buzz_unmanaged_agent_mode(cfg), "relay")
        self.assertEqual(FGS.buzz_unmanaged_agent_mode(self.load(buzz_unmanaged_agents="skip")), "skip")
        self.assertIn("buzz_unmanaged_agents", FGS.OPTIONAL_CONFIG_KEYS)
        self.assertNotIn("buzz_unmanaged_agents", FGS.CONFIG_KEYS)

    def test_relay_is_accepted(self):
        """L1-FGS-701: 写 `"relay"` 时配置加载通过，mode 就是 "relay"。"""
        self.assertEqual(FGS.buzz_unmanaged_agent_mode(self.load(buzz_unmanaged_agents="relay")), "relay")

    def test_an_unknown_value_is_rejected_without_echoing_it(self):
        """L1-FGS-702: 只认 "skip" / "relay"；其它值（含大小写变体、非字符串）整份配置拒绝，且错误信息里不回显
        用户写了什么（与其它配置校验一致）。"""
        for bad in ("Relay", "RELAY", "context", "", 1, None, ["relay"]):
            with self.subTest(bad=bad):
                with self.assertRaises(FGS.GroupSyncError) as caught:
                    self.load(buzz_unmanaged_agents=bad)
                if str(bad):  # "" is a substring of everything; there is nothing to leak about it either
                    self.assertNotIn(str(bad), str(caught.exception))


# ================================ L1 : route_buzz_event（纯函数） ================================


class UnmanagedAgentRouting(unittest.TestCase):
    def route(self, ev, **kw):
        args = dict(mirror_pubkey=MIRROR_PK, agent_apps={AGENT_PK: AGENT_APP},
                    human_pubkeys={OWNER_PK, ALICE_PK, BOB_PK},
                    agent_pubkeys={AGENT_PK, AGENT2_PK, FOREIGN_AGENT_PK},
                    managed_agents={AGENT_PK, AGENT2_PK},
                    names={ALICE_PK: "Alice", AGENT_PK: "helper-agent", FOREIGN_AGENT_PK: "nh-dev"},
                    mention_targets={ALICE_PK: (ALICE_OPEN, "Alice"), AGENT_PK: (AGENT_BOT_MEMBER, "helper-agent")},
                    agent_mention_targets={AGENT_PK: (AGENT_BOT_MEMBER, "helper-agent")})
        args.update(kw)
        return FGS.route_buzz_event(ev, **args)

    def test_default_mode_relays_and_skip_still_drops(self):
        """L1-FGS-710: 不写 `unmanaged_agents`（缺省 "relay"，ADR-0019）时本机没配的频道 agent 由 owner bot 代发；显式 "skip"
        时仍然 `agent_bot_unavailable`，不镜像。"""
        out = self.route(event(eid(1), FOREIGN_AGENT_PK, "hi"))
        self.assertIsInstance(out, FGS.Outbound)
        self.assertEqual(out.text, f"nh-dev（Buzz·{LABEL}）：hi")
        self.assertEqual(self.route(event(eid(2), FOREIGN_AGENT_PK, "hi"), unmanaged_agents="skip"),
                         "agent_bot_unavailable")

    def test_an_unmanaged_agent_is_relayed_by_the_owner_bot(self):
        """L1-FGS-711: 开了 "relay" 之后，本机没配的频道 agent 由 owner 应用 bot 代发（via_app_id 是 None，不是它
        自己的 app），署名是「名字（Buzz·助手）：正文」。"""
        out = self.route(event(eid(3), FOREIGN_AGENT_PK, "已按 Issue #96 核对完毕"), unmanaged_agents="relay")
        self.assertIsInstance(out, FGS.Outbound)
        self.assertIsNone(out.via_app_id)
        self.assertEqual(out.text, f"nh-dev（Buzz·{LABEL}）：已按 Issue #96 核对完毕")

    def test_a_managed_agent_without_a_live_bot_is_still_not_relayed(self):
        """L1-FGS-712: 配置里有、只是这一轮没有可用飞书 bot 的 agent（没验证过 / bot 还没进群）**不代发**：那是暂时
        且会自愈的状态，仍然 `agent_bot_unavailable`。开关只覆盖「本机根本没配」的 agent。"""
        self.assertEqual(self.route(event(eid(4), AGENT2_PK, "hi"), unmanaged_agents="relay"),
                         "agent_bot_unavailable")

    def test_an_agent_with_its_own_live_bot_is_untouched(self):
        """L1-FGS-713: 有自己飞书 bot 的 agent 照旧用自己的应用发（via_app_id 是它的 app_id，正文不带署名前缀），
        开关不影响这条路径。"""
        out = self.route(event(eid(5), AGENT_PK, "hi"), unmanaged_agents="relay")
        self.assertEqual(out.via_app_id, AGENT_APP)
        self.assertEqual(out.text, "hi")

    def test_relaying_never_notifies_a_person(self):
        """L1-FGS-714: 代发的消息一律不生成 `<at>`——即使 p tag 指向的是能解析的频道人类成员（Alice）或频道自己的
        agent。代发只搬运内容，不放宽通知：这与 agent 用自己 bot 发言那条路径一致。"""
        out = self.route(event(eid(6), FOREIGN_AGENT_PK, "请 Alice 看一下",
                               tags=[["p", ALICE_PK], ["p", AGENT_PK]]), unmanaged_agents="relay")
        self.assertNotIn("<at", out.text)
        self.assertNotIn(ALICE_OPEN, out.text)
        self.assertNotIn(AGENT_BOT_MEMBER, out.text)

    def test_an_outsider_is_still_not_an_agent(self):
        """L1-FGS-715: 这个开关只认「频道里 role=bot 的成员」。既不是人类成员也不是频道 agent 的作者不受影响，仍然
        按 `buzz_unmapped_senders` 处理（缺省 `not_channel_human`）。"""
        self.assertEqual(self.route(event(eid(7), OUTSIDER_PK, "hi"), unmanaged_agents="relay"), "not_channel_human")


# ================================ L1 : 卡片 ================================


class UnmanagedAgentCard(unittest.TestCase):
    def ctx(self, **kw):
        args = dict(link_base=base.API_ORIGIN, channel_id=base.CHANNEL, channel_name="naturehood", emails={},
                    open_ids={AGENT_PK: AGENT_BOT_MEMBER, ALICE_PK: ALICE_OPEN})
        args.update(kw)
        return FGS.CardContext(**args)

    def route(self, ev, **kw):
        args = dict(mirror_pubkey=MIRROR_PK, agent_apps={AGENT_PK: AGENT_APP},
                    human_pubkeys={OWNER_PK, ALICE_PK, BOB_PK},
                    agent_pubkeys={AGENT_PK, FOREIGN_AGENT_PK}, managed_agents={AGENT_PK},
                    names={ALICE_PK: "Alice", FOREIGN_AGENT_PK: "nh-dev"},
                    mention_targets={ALICE_PK: (ALICE_OPEN, "Alice")},
                    agent_mention_targets={}, card=self.ctx())
        args.update(kw)
        return FGS.route_buzz_event(ev, **args)

    def test_the_card_speaker_is_labelled(self):
        """L1-FGS-720: 卡片模式下 speaker 显示为「名字（助手）」，让人一眼看出这是被转述的 agent，不是它自己的 bot
        在说话。"""
        out = self.route(event(eid(8), FOREIGN_AGENT_PK, "hi"), unmanaged_agents="relay")
        card = json.loads(out.card)
        self.assertTrue(card["config"]["summary"]["content"].startswith(f"nh-dev（{LABEL}）"))  # the byline, as the summary starts with it
        self.assertIn(f"nh-dev（{LABEL}）", json.dumps(card["body"]["elements"][0], ensure_ascii=False))

    def test_the_card_names_nobody(self):
        """L1-FGS-721: 代发的卡片不点名任何人——与正文不生成 `<at>` 对称，否则卡片会成为绕过「不放宽通知」的后门。"""
        out = self.route(event(eid(9), FOREIGN_AGENT_PK, "请 Alice 看一下", tags=[["p", ALICE_PK]]),
                         unmanaged_agents="relay")
        self.assertNotIn(ALICE_OPEN, out.card)


# ================================ L1 : reaction 不受影响 ================================


class UnmanagedAgentReaction(unittest.TestCase):
    def test_an_unmanaged_agents_reaction_is_still_skipped(self):
        """L1-FGS-730: 表情仍然只能由 agent 自己的 bot 打。本机没有凭据的 agent 的 reaction 照旧跳过，不由 owner bot
        代打——代发只覆盖消息，不覆盖表情（`route_buzz_reaction` 不接这个开关）。"""
        from test_buzz_feishu_group_sync import reaction_event
        routed = FGS.route_buzz_reaction(reaction_event(1, FOREIGN_AGENT_PK, eid(3), "👀"),
                                         agent_apps={AGENT_PK: AGENT_APP}, human_pubkeys={OWNER_PK},
                                         agent_pubkeys={AGENT_PK, FOREIGN_AGENT_PK},
                                         reaction_map=FGS.DEFAULT_REACTION_MAP)
        self.assertEqual(routed, "agent_bot_unavailable")


if __name__ == "__main__":
    unittest.main()
