"""Buzz → 飞书：非成员/非 agent 作者的消息以「仅上下文」镜像（engineering/skills#142，与 #135 对称，
references/feishu-group-sync.md「非成员的发言」，ADR-0016）。

配置 `buzz_unmapped_senders`（可选）：`"skip"`（缺省，和以前逐字节一致：作者既不是频道人类成员也不是配置的 agent 就
`not_channel_human`，不镜像）或 `"context"`。设成 `"context"` 时，这样的作者由 owner 应用 bot 发进飞书，署名插入
「·非成员」区分（`名字（Buzz·非成员）：正文`，卡片模式下 speaker 是「名字（非成员）」），只有 @ 到频道自己的、当下有
飞书 bot 的 agent 才生成真的 `<at>`（能唤醒它）；@ 到任何人一律静默丢弃（不生成、也不留纯文字），与 #135 里飞书那边
陌生人的规则对称。与飞书 → Buzz 方向不同：Buzz 的事件都经过 relay 的签名校验，没有「平台认不认这个账号」的问题，所以
门槛只是「不是验证过的人类成员、也不是配置的 agent」——不要求能解出 profile 显示名（解不出退回公钥前 12 位，与既有
人类分支一致；这正是修复对象：Workflow 的签名身份没有 kind:0 profile）。

沿用 test_buzz_feishu_group_sync.py 的 FakeWorld / Env / 常量，以及 test_buzz_feishu_group_sync_cards.py 的
CardRouting 卡片测试方式。用例编号从 600 起，避开同一个脚本上其他改动用的编号。
"""

import hashlib
import json
import sys
import unittest
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_buzz_feishu_group_sync as base  # noqa: E402
from test_buzz_feishu_group_sync import (  # noqa: E402
    AGENT2_PK, AGENT_APP, AGENT_BOT_MEMBER, AGENT_PK, ALICE_OPEN, ALICE_PK, BOB_PK, CAROL_PK, FGS, MIRROR_PK, NOW,
    OUTSIDER_PK, OWNER_PK, T0, TmpCase, Env, FakeWorld, event, eid, ts, write_owner_only,
)


def setUpModule():
    base.setUpModule()


def tearDownModule():
    base.tearDownModule()


LABEL = "非成员"


# ================================ L1 : config ================================


class BuzzContextConfig(TmpCase):
    def raw(self):
        return json.loads(Env(self.tmp).config.read_text())

    def load(self, **extra):
        return FGS.load_config(write_owner_only(self.tmp / "c.json", json.dumps(dict(self.raw(), **extra))))

    def test_the_key_is_optional_and_absent_means_skip(self):
        """L1-FGS-600: `buzz_unmapped_senders` 可以不写：配置照常加载、`buzz_unmapped_sender_mode(cfg)` 是 "skip"
        （和以前一样不镜像，走 not_channel_human）；它是可选键，不是必填键。"""
        raw = self.raw()
        self.assertNotIn("buzz_unmapped_senders", raw)
        cfg = FGS.load_config(write_owner_only(self.tmp / "none.json", json.dumps(raw)))
        self.assertEqual(FGS.buzz_unmapped_sender_mode(cfg), "skip")
        self.assertIn("buzz_unmapped_senders", FGS.OPTIONAL_CONFIG_KEYS)
        self.assertNotIn("buzz_unmapped_senders", FGS.CONFIG_KEYS)
        self.assertEqual(FGS.BUZZ_UNMAPPED_SENDER_MODES, ("skip", "context"))
        self.assertEqual(FGS.DEFAULT_BUZZ_UNMAPPED_SENDERS, "skip")
        self.assertEqual(FGS.BUZZ_CONTEXT_LABEL, LABEL)

    def test_both_values_are_accepted(self):
        """L1-FGS-601: "skip" 和 "context" 都能加载，`buzz_unmapped_sender_mode` 原样给出。"""
        self.assertEqual(FGS.buzz_unmapped_sender_mode(self.load(buzz_unmapped_senders="skip")), "skip")
        self.assertEqual(FGS.buzz_unmapped_sender_mode(self.load(buzz_unmapped_senders="context")), "context")

    def test_anything_else_is_refused_and_the_error_does_not_echo_the_value(self):
        """L1-FGS-602: 不是这两个字符串的整份配置被拒，不会悄悄退回「不镜像」；错误信息是固定的一句话，不含配置里的值。"""
        for bad in ("Context", "CONTEXT", " context", "context ", "all", "", None, True, False, 1, 0,
                    ["context"], {"mode": "context"}):
            with self.subTest(bad=repr(bad)):
                with self.assertRaises(FGS.GroupSyncError) as ctx:
                    self.load(buzz_unmapped_senders=bad)
                self.assertEqual(str(ctx.exception), 'config buzz_unmapped_senders must be "skip" (the default) or "context"')

    def test_it_is_independent_of_the_feishu_side_key(self):
        """L1-FGS-603: 两个方向的键互不影响，可以只开一个、也可以同时开；都不是彼此的前提条件。"""
        cfg = self.load(buzz_unmapped_senders="context")
        self.assertEqual((FGS.buzz_unmapped_sender_mode(cfg), FGS.unmapped_sender_mode(cfg)), ("context", "context"))
        cfg = self.load(buzz_unmapped_senders="context", feishu_unmapped_senders="context")
        self.assertEqual((FGS.buzz_unmapped_sender_mode(cfg), FGS.unmapped_sender_mode(cfg)), ("context", "context"))
        cfg = self.load(feishu_unmapped_senders="context")
        self.assertEqual(FGS.buzz_unmapped_sender_mode(cfg), "skip")

    def test_the_other_optional_keys_keep_working_next_to_it(self):
        """L1-FGS-604: 和别的可选键并存：`identity`、`message_format`、`feishu_sender_allowlist` 照旧严格校验；写错键名
        （少个 s、多个 s）仍被拒。"""
        cfg = self.load(buzz_unmapped_senders="context", identity="email", message_format="card",
                        feishu_sender_allowlist=[ALICE_PK])
        self.assertEqual(FGS.buzz_unmapped_sender_mode(cfg), "context")
        for extra in ({"identity": "Email"}, {"message_format": "html"},
                      {"buzz_unmapped_sender": "context"}, {"buzz_unmapped_senderss": "context"}):
            with self.assertRaises(FGS.GroupSyncError, msg=repr(extra)):
                self.load(**extra)


# ================================ L1 : route_buzz_event / _event_card (pure) ================================


class BuzzContextRouting(unittest.TestCase):
    def route(self, ev, **kw):
        args = dict(mirror_pubkey=MIRROR_PK, agent_apps={AGENT_PK: AGENT_APP},
                    human_pubkeys={OWNER_PK, ALICE_PK, BOB_PK}, agent_pubkeys={AGENT_PK, AGENT2_PK},
                    names={ALICE_PK: "Alice", AGENT_PK: "helper-agent"},
                    mention_targets={ALICE_PK: (ALICE_OPEN, "Alice"), AGENT_PK: (AGENT_BOT_MEMBER, "helper-agent")},
                    agent_mention_targets={AGENT_PK: (AGENT_BOT_MEMBER, "helper-agent")})
        args.update(kw)
        return FGS.route_buzz_event(ev, **args)

    def test_default_mode_is_unchanged_skip(self):
        """L1-FGS-610: 不写 `unmapped_senders`（缺省 "skip"）时行为和以前逐字节一样：既不是人类成员也不是 agent 的作者
        仍然 `not_channel_human`。"""
        self.assertEqual(self.route(event(eid(1), OUTSIDER_PK, "hi")), "not_channel_human")
        self.assertEqual(self.route(event(eid(2), OUTSIDER_PK, "hi"), unmapped_senders="skip"), "not_channel_human")

    def test_agent_bot_unavailable_is_not_affected(self):
        """L1-FGS-611: 配置了但没有飞书 bot 的 agent（agent_bot_unavailable）不受这个开关影响：开了 "context" 也一样不镜像
        ——它是一个更具体、已知的原因，不是「陌生人」。"""
        self.assertEqual(self.route(event(eid(3), AGENT2_PK, "hi"), unmapped_senders="context"), "agent_bot_unavailable")

    def test_context_mode_mirrors_through_the_owner_bot_with_a_distinct_signature(self):
        """L1-FGS-612: "context" 时，非成员/非 agent 作者的话由 owner bot 发（via_app_id=None），署名在既有的
        「名字（Buzz）：」里插入「·非成员」：`名字（Buzz·非成员）：正文`。"""
        out = self.route(event(eid(4), OUTSIDER_PK, "hello"), unmapped_senders="context", names={OUTSIDER_PK: "Mallory"})
        self.assertIsNone(out.via_app_id)
        self.assertEqual(out.text, "Mallory（Buzz·非成员）：hello")

    def test_no_profile_falls_back_to_the_pubkey_prefix(self):
        """L1-FGS-613: 没有 profile 显示名时（比如 Workflow 的签名身份）退回公钥前 12 位，跟人类分支一致——这正是要修的
        那个根消息不镜像的场景。"""
        out = self.route(event(eid(5), OUTSIDER_PK, "hello"), unmapped_senders="context", names={})
        self.assertEqual(out.text, f"{OUTSIDER_PK[:12]}（Buzz·非成员）：hello")

    def test_a_mention_of_the_channels_own_agent_still_wakes_it(self):
        """L1-FGS-614: 正文 @ 到频道自己的、当下有飞书 bot 的 agent：生成真的 `<at user_id=…>`（能唤醒），跟成员发言一致。"""
        ev = event(eid(6), OUTSIDER_PK, "@helper-agent 看下", tags=[("p", AGENT_PK)])
        out = self.route(ev, unmapped_senders="context", names={OUTSIDER_PK: "Mallory"})
        self.assertEqual(out.text, f'Mallory（Buzz·非成员）：@helper-agent 看下 <at user_id="{AGENT_BOT_MEMBER}">helper-agent</at>')

    def test_a_mention_of_a_human_or_an_unavailable_agent_is_silently_dropped(self):
        """L1-FGS-615: 正文 @ 到人类成员、或没有飞书 bot 的 agent：不生成 `<at>`，也不留可读的纯文字 @——它们的名字在
        正文里也被中和（全角＠），跟成员发言的规则不同（成员发言里映射不到的才不生成 `<at>`，正文原样保留；这里连
        「映射得到但不是 agent」的目标，连同它在正文里字面的 @名字，都一并处理掉），与飞书侧陌生人的中和对称。"""
        ev = event(eid(7), OUTSIDER_PK, "@Alice @agent2 看下", tags=[("p", ALICE_PK), ("p", AGENT2_PK)])
        out = self.route(ev, unmapped_senders="context",
                         names={OUTSIDER_PK: "Mallory", ALICE_PK: "Alice", AGENT2_PK: "agent2"})
        self.assertEqual(out.text, "Mallory（Buzz·非成员）：＠Alice ＠agent2 看下")
        self.assertNotIn("<at", out.text)
        self.assertNotIn("@Alice", out.text)
        self.assertNotIn("@agent2", out.text)

    def test_an_unresolvable_disallowed_target_leaves_the_text_untouched(self):
        """L1-FGS-621: 目标是人类成员/不可用 agent，但解不出它的显示名（没有 profile）：没法在正文里找它的字面 @，
        正文原样保留——不猜、不误伤别的文字，`<at>` 仍然不生成。"""
        ev = event(eid(7), OUTSIDER_PK, "@某人 看下", tags=[("p", ALICE_PK)])
        out = self.route(ev, unmapped_senders="context", names={OUTSIDER_PK: "Mallory"})
        self.assertEqual(out.text, "Mallory（Buzz·非成员）：@某人 看下")

    def test_echo_kind_and_empty_are_still_skipped_first(self):
        """L1-FGS-616: 回声、非消息 kind、空正文的判断在「谁能说话」之前，"context" 打开也不例外。"""
        self.assertEqual(self.route(event(eid(8), MIRROR_PK, "x"), unmapped_senders="context"), "echo")
        self.assertEqual(self.route(event(eid(9), OUTSIDER_PK, "x", kind=40008), unmapped_senders="context"), "kind")
        self.assertEqual(self.route(event(eid(10), OUTSIDER_PK, "  "), unmapped_senders="context"), "empty")

    def test_context_speech_still_carries_its_thread_parent(self):
        """L1-FGS-617: 话题父事件（parent_event_id）照常从 e tag 取，不受这个分支影响——补发根、挂话题的逻辑不用改。"""
        ev = event(eid(11), OUTSIDER_PK, "x", tags=[("e", eid(1), "", "reply")])
        out = self.route(ev, unmapped_senders="context")
        self.assertEqual(out.parent_event_id, eid(1))


class BuzzContextCard(unittest.TestCase):
    def ctx(self, **kw):
        args = dict(link_base=base.API_ORIGIN, channel_id=base.CHANNEL, channel_name="naturehood", emails={},
                    open_ids={AGENT_PK: AGENT_BOT_MEMBER, ALICE_PK: ALICE_OPEN})
        args.update(kw)
        return FGS.CardContext(**args)

    def route(self, ev, **kw):
        args = dict(mirror_pubkey=MIRROR_PK, agent_apps={AGENT_PK: AGENT_APP},
                    human_pubkeys={OWNER_PK, ALICE_PK}, agent_pubkeys={AGENT_PK, AGENT2_PK},
                    names={ALICE_PK: "Alice", AGENT_PK: "helper-agent"},
                    mention_targets={ALICE_PK: (ALICE_OPEN, "Alice"), AGENT_PK: (AGENT_BOT_MEMBER, "helper-agent")},
                    agent_mention_targets={AGENT_PK: (AGENT_BOT_MEMBER, "helper-agent")}, card=self.ctx())
        args.update(kw)
        return FGS.route_buzz_event(ev, **args)

    def card(self, out):
        self.assertIsNotNone(out.card)
        return json.loads(out.card)

    def test_the_card_speaker_is_labelled_and_blue(self):
        """L1-FGS-618: 卡片模式下 speaker 显示为「名字（非成员）」，跟人类一样是 blue 模板（不是绿色的 agent 模板）；
        署名行（原来的副标题）和摘要开头是「名字（非成员） · #频道名」。"""
        out = self.route(event(eid(12), OUTSIDER_PK, "hi"), unmapped_senders="context", names={OUTSIDER_PK: "Mallory"})
        card = self.card(out)
        self.assertEqual(card["header"]["template"], "blue")
        self.assertNotIn("subtitle", card["header"])
        self.assertIn('"content": "Mallory（非成员） · #naturehood"', json.dumps(card["body"]["elements"][0], ensure_ascii=False))
        self.assertTrue(card["config"]["summary"]["content"].startswith("Mallory（非成员） · #naturehood："))

    def test_only_the_agent_mention_survives_in_the_card(self):
        """L1-FGS-619: 卡片里的 @ 行只留下频道自己的 agent（真的 <at id=…>，能唤醒）；@ 到人类成员的目标完全不出现在
        @ 行里——不是退化成纯文字，是压根不写。"""
        ev = event(eid(13), OUTSIDER_PK, "看下", tags=[("p", ALICE_PK), ("p", AGENT_PK)])
        out = self.route(ev, unmapped_senders="context", names={OUTSIDER_PK: "Mallory"})
        card = self.card(out)
        body_text = json.dumps(card["body"]["elements"], ensure_ascii=False)
        self.assertIn(f'<at id={AGENT_BOT_MEMBER}>', body_text)
        self.assertNotIn(ALICE_OPEN, body_text)
        self.assertNotIn("Alice", body_text)

    def test_without_any_agent_target_the_card_has_no_mention_line(self):
        """L1-FGS-620: 正文只 @ 了人类成员、没 @ 任何 agent：卡片里完全没有 @ 行（跟没写 p tag 一样）。"""
        ev = event(eid(14), OUTSIDER_PK, "hi", tags=[("p", ALICE_PK)])
        out = self.route(ev, unmapped_senders="context", names={OUTSIDER_PK: "Mallory"})
        card = self.card(out)
        tags = [e.get("tag") for e in card["body"]["elements"]]
        self.assertNotIn("collapsible_panel", tags[:-1])  # 唯一允许的额外行是折叠面板（超长时），这里正文很短不会触发
        body_text = json.dumps(card["body"]["elements"], ensure_ascii=False)
        self.assertNotIn("Alice", body_text)

    def test_the_preview_text_neutralises_a_disallowed_targets_literal_mention_too(self):
        """L1-FGS-622: 正文里字面写的 @Alice（不只是没写出来的 p tag）在卡片里也被中和成全角＠（这条短消息整句话就是
        标题，没有单独的正文行）——跟文字模式一样，不止 @ 行不写，正文本身也不能让读的人以为这条陌生人的话真的在
        称呼 Alice。"""
        ev = event(eid(15), OUTSIDER_PK, "@Alice 麻烦看下", tags=[("p", ALICE_PK)])
        out = self.route(ev, unmapped_senders="context", names={OUTSIDER_PK: "Mallory", ALICE_PK: "Alice"})
        self.assertNotIn("@Alice", out.card)
        self.assertIn("＠Alice", out.card)
        self.assertNotIn("@Alice", out.text)
        self.assertIn("＠Alice", out.text)


# ================================ L2-1 : a full round ================================


class RoundBuzzUnmappedSenders(TmpCase):
    """buzz_unmapped_senders "context" 打开时，非成员/非 agent 作者的根消息现在会镜像，之后同一话题的回复能挂在它下面
    ——这正是 skills#142 要修的场景（Workflow 的签名身份没有 profile、发了话题根）。只借用
    test_buzz_feishu_group_sync.RoundThreadRoots 的几个纯辅助方法，不继承它的用例（避免重复跑一遍不相关的话题根测试）。"""

    OLD = ts(NOW) - 3600  # 比绑定起点（首轮时间 - 120 秒）早

    def world(self):
        w = FakeWorld(self.tmp)
        w.members = [m for m in w.members if m["pubkey"] != CAROL_PK]
        return w

    @staticmethod
    def key_of(event_id):
        return "b2f-" + hashlib.sha256(event_id.encode()).hexdigest()[:40]

    def root(self, n, pk, content="根", created_at=None, **kw):
        return event(eid(n), pk, content, created_at=self.OLD if created_at is None else created_at, **kw)

    def reply(self, n, pk, content, parent, *, root=None, at=T0 + 100):
        tags = [("e", parent, "", "reply")]
        if root:
            tags.insert(0, ("e", root, "", "root"))
        return event(eid(n), pk, content, created_at=at, tags=tags)

    def sent(self, w):
        out = []
        for c in w.lark_sends():
            a = c["args"]
            out.append({"verb": a[1], "app": c["app"], "text": base.text_of(c), "key": a[a.index("--idempotency-key") + 1],
                        "parent": a[a.index("--message-id") + 1] if "--message-id" in a else None})
        return out

    def start(self, w, env=None, **overrides):
        env = env or Env(self.tmp, **overrides)
        env.round(w)  # the binding starts here
        return env

    def go(self, env, w, minutes=2):
        return env.round(w, now=NOW + timedelta(minutes=minutes))

    def test_default_mode_regresses_to_the_old_top_level_behaviour(self):
        """L2-1-FGS-630: 回归：不写 `buzz_unmapped_senders`（缺省 "skip"）时和 L2-1-FGS-306 一样——非成员作者的根不镜像，
        回复退回顶层发出，不丢、不算错误。"""
        w = self.world()
        env = self.start(w)
        w.events = [self.root(1, OUTSIDER_PK, "根"), self.reply(2, ALICE_PK, "回复", eid(1))]
        report = self.go(env, w)
        sent = self.sent(w)
        self.assertEqual([(s["verb"], s["text"], s["parent"]) for s in sent], [("+messages-send", "Alice（Buzz）：回复", None)])
        self.assertEqual((report["thread_root_unavailable"], report["skipped"].get("not_channel_human")), (1, 1))

    def test_context_mode_mirrors_the_root_and_the_reply_threads_under_it(self):
        """L2-1-FGS-631: `buzz_unmapped_senders` "context" 时，同一个场景：根现在真的镜像出去（签名 …非成员…），回复直接
        挂在它下面（不再各自顶层发出）；`thread_root_unavailable` 不再计数，`not_channel_human` 也不再计数（context_to_buzz
        方向没有对应计数，飞书 → Buzz 才有 context_to_buzz——这里数的是 to_feishu）。"""
        w = self.world()
        env = self.start(w, buzz_unmapped_senders="context")
        w.events = [self.root(1, OUTSIDER_PK, "根"), self.reply(2, ALICE_PK, "回复", eid(1))]
        report = self.go(env, w)
        sent = self.sent(w)
        self.assertEqual([s["text"] for s in sent], [f"{OUTSIDER_PK[:12]}（Buzz·非成员）：根", "Alice（Buzz）：回复"])
        self.assertEqual(sent[1]["parent"], w.sent_keys[self.key_of(eid(1))])
        self.assertEqual((report["thread_root_unavailable"], report["skipped"].get("not_channel_human"), report["to_feishu"]),
                         (0, None, 2))
        self.assertFalse(FGS.needs_attention(report))


# ================================ docs ================================


class BuzzContextDocs(unittest.TestCase):
    SKILL = base.TESTS.parent
    REPO = SKILL.parent.parent
    GROUP_DOC = (SKILL / "references" / "feishu-group-sync.md").read_text(encoding="utf-8")
    DOC = (SKILL / "references" / "feishu-routing-policy.md").read_text(encoding="utf-8")
    HEADING = "## 非成员/非 agent 的 Buzz 消息（仅上下文镜像）"
    ADR = REPO / "docs" / "05-adr" / "0017-mirror-unmapped-buzz-authors-as-context-only.md"

    def section(self):
        start = self.DOC.find(self.HEADING)
        self.assertGreaterEqual(start, 0, msg="the reference has no section for non-member/non-agent Buzz authors")
        end = self.DOC.find("\n## ", start + 1)
        return self.DOC[start:end if end > 0 else len(self.DOC)]

    def test_the_section_says_what_it_does_what_it_does_not_and_what_it_risks(self):
        """L1-FGS-640: 「非成员/非 agent 的 Buzz 消息（仅上下文镜像）」一节写明：用途（Workflow 签名身份没有 profile、根消息丢了导致
        Thread 断链）、配置（`buzz_unmapped_senders`、"skip" 缺省、"context"）、署名「·非成员」、能 @ 到 agent 不能 @ 到人、
        `agent_bot_unavailable` 不受影响、没有新增报告字段、与 `feishu_sender_allowlist` 无关、风险、ADR-0017。"""
        section = self.section()
        for term in ("`buzz_unmapped_senders`", '"skip"', '"context"', "·非成员", "`not_channel_human`", "`agent_bot_unavailable`",
                     "能 @ 到 agent", "不能 @ 到人", "`<at>`", "唤醒不等于授权", "`to_feishu`", "`context_to_buzz`",
                     "`feishu_sender_allowlist`", "整份配置被拒", "ADR-0017", "缺省", "签名"):
            self.assertIn(term, section, msg=term)

    def test_the_config_key_list_and_the_feature_bullet_mention_it(self):
        """L1-FGS-641: 配置一节的可选键清单、「配置这次同步」编号列表都提到新键。"""
        start = self.GROUP_DOC.find("## 配置（0600")
        config = self.GROUP_DOC[start:self.GROUP_DOC.find("\n## ", start + 1)]
        self.assertIn("`buzz_unmapped_senders`", config)
        self.assertRegex(config, r"只有[^。]*`buzz_unmapped_senders`[^。]*可以不写")
        setup = self.GROUP_DOC[self.GROUP_DOC.find("8. 群里不是频道成员的人"):self.GROUP_DOC.find("\n\n### 2. 预检")]
        self.assertIn("buzz_unmapped_senders", setup)

    def test_the_adr_exists_is_indexed_and_records_the_decision(self):
        """L1-FGS-642: ADR-0017 存在，是 Accepted，写明决定、备选、与 ADR-0016 的关系。"""
        self.assertTrue(self.ADR.exists(), msg="docs/05-adr/0017-... is missing")
        text = self.ADR.read_text(encoding="utf-8")
        self.assertRegex(text, r"(?m)^status: Accepted$")
        for term in ("PO", "ADR-0016", "buzz_unmapped_senders", "Option A", "Option B", "Option C", "唤醒",
                     "not_channel_human", "能 @ 到 agent", "不能 @ 到人", "engineering/skills#142", "#135"):
            self.assertIn(term, text, msg=term)


if __name__ == "__main__":
    unittest.main()
