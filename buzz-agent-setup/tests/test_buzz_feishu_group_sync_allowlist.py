"""飞书 → Buzz 的发言人白名单（engineering/skills#110，references/feishu-group-sync.md「只让特定的人的发言进 Buzz」）。

配置 `feishu_sender_allowlist`（可选）：非空的 Buzz pubkey 列表。设置了以后，飞书 → Buzz 方向只镜像白名单里的人的发言；不在名单里的
（哪怕已映射、在频道里）不镜像、不发送、不写账本，计 `skipped["sender_not_allowed"]`。缺省 = 不限制。用途：个人频道里的 agent 只认 owner，
群里万一多出别人，他们的发言也不能借镜像身份进入 Buzz。

沿用 test_buzz_feishu_group_sync.py 的 FakeWorld / Env / 常量。L1 覆盖配置和路由的纯函数；L2-1 走完整的 round（两种 identity 模式、话题、
混合、账本、重试、不泄露）。用例编号从 400 起，避开同一个脚本上其他改动用的编号。
"""

import contextlib
import json
import sys
import unittest
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_buzz_feishu_group_sync as base  # noqa: E402
from test_buzz_feishu_group_sync import (  # noqa: E402
    ALICE_OPEN, ALICE_PK, BOB_OPEN, BOB_PK, CAROL_PK, EXTRA_OPEN, FGS, GUEST_OPEN, GUEST_PK, NOW, OUTSIDER_OPEN,
    OUTSIDER_PK, OWNER_KEY, OWNER_OPEN, OWNER_PK, T0, Env, FakeWorld, TmpCase, bridge_open_of, eid, event, fmsg, text_of,
    union_of, write_owner_only,
)

IDENTITIES = ("default", "email")  # "default" 就是 union_id 模式（缺省）


def setUpModule():
    base.setUpModule()


def tearDownModule():
    base.tearDownModule()


def env_for(tmp, identity, allowlist=None, **extra):
    """identity 是 "default"（不写 identity，即 union_id）或 "email"；allowlist 为 None 时配置里没有这个键。"""
    overrides = dict(extra)
    if identity != "default":
        overrides["identity"] = identity
    if allowlist is not None:
        overrides["feishu_sender_allowlist"] = allowlist
    return Env(tmp, **overrides)


def quiet_world(tmp):
    """频道里的人都映射得上（没有 Carol：她没有绑定，会让 removals_withheld 触发「需要关注」），群里没有别的消息。"""
    w = FakeWorld(tmp)
    w.members = [m for m in w.members if m["pubkey"] != CAROL_PK]
    w.messages = []
    return w


def said_in_buzz(w):
    return [e["content"] for e in w.mirrored()]


# ================================ L1 ================================


class AllowlistConfig(TmpCase):
    def raw(self):
        return json.loads(Env(self.tmp).config.read_text())

    def load(self, **extra):
        return FGS.load_config(write_owner_only(self.tmp / "c.json", json.dumps(dict(self.raw(), **extra))))

    def test_the_key_is_optional_and_absent_means_no_restriction(self):
        """L1-FGS-400: `feishu_sender_allowlist` 可以不写：配置照常加载、`sender_allowlist(cfg)` 是 None（不限制）；它是可选键，不是必填键。"""
        raw = self.raw()
        self.assertNotIn("feishu_sender_allowlist", raw)
        cfg = FGS.load_config(write_owner_only(self.tmp / "none.json", json.dumps(raw)))
        self.assertIsNone(FGS.sender_allowlist(cfg))
        self.assertIn("feishu_sender_allowlist", FGS.OPTIONAL_CONFIG_KEYS)
        self.assertNotIn("feishu_sender_allowlist", FGS.CONFIG_KEYS)

    def test_a_list_of_lowercase_hex_pubkeys_is_accepted_and_deduplicated(self):
        """L1-FGS-401: 非空列表、每项 64 位小写 hex：加载成功，`sender_allowlist` 给出去重后的集合；重复的项合并（一个 pubkey 写两遍不会让
        名单变宽）；一项也行。"""
        cfg = self.load(feishu_sender_allowlist=[ALICE_PK])
        self.assertEqual(FGS.sender_allowlist(cfg), frozenset({ALICE_PK}))
        cfg = self.load(feishu_sender_allowlist=[ALICE_PK, OWNER_PK, ALICE_PK, OWNER_PK, ALICE_PK])
        self.assertEqual(FGS.sender_allowlist(cfg), frozenset({ALICE_PK, OWNER_PK}))
        self.assertIsInstance(FGS.sender_allowlist(cfg), frozenset)

    def test_the_cap_counts_distinct_pubkeys(self):
        """L1-FGS-402: 去重后最多 50 个（`MAX_SENDER_ALLOWLIST`）：正好 50 个可以，51 个整份配置被拒；重复项不占名额，50 个不同的加上
        一堆重复仍然可以。"""
        self.assertEqual(FGS.MAX_SENDER_ALLOWLIST, 50)
        fifty = [f"{i:064x}" for i in range(1, 51)]
        self.assertEqual(FGS.sender_allowlist(self.load(feishu_sender_allowlist=fifty)), frozenset(fifty))
        self.assertEqual(FGS.sender_allowlist(self.load(feishu_sender_allowlist=fifty + fifty[:10] + fifty)), frozenset(fifty))
        with self.assertRaises(FGS.GroupSyncError) as ctx:
            self.load(feishu_sender_allowlist=fifty + [f"{51:064x}"])
        self.assertIn("feishu_sender_allowlist", str(ctx.exception))

    def test_anything_else_is_refused_and_the_error_does_not_echo_the_value(self):
        """L1-FGS-403: 不是列表（字符串、对象、null、数字、布尔）、空列表、项不是 64 位小写 hex（大写、63 或 65 位、非 hex 字符、npub、
        首尾空白、数字（含 64 位的整数）、null、嵌套列表、对象）：整份配置被拒，不会悄悄退回「不限制」；错误信息说明是哪个键，但不带出配置里的值。"""
        secret = "AB" * 32  # 大写的 pubkey：格式不对，值也不该出现在错误里
        bad_values = [
            ALICE_PK, {ALICE_PK: True}, None, 1, True, "", [], [None], [1], [True], [secret], [base.AGENT_PK.upper()], [ALICE_PK[:-1]],
            [ALICE_PK + "1"], ["g" * 64], [f"npub1{'q' * 58}"], [" " + ALICE_PK], [ALICE_PK + "\n"], [[ALICE_PK]], [{"pubkey": ALICE_PK}],
            [ALICE_PK, "nope"], [ALICE_PK, None], [ALICE_PK, secret],
            [int("11" * 32)],  # 64 位的十进制数字：转成字符串看着像 hex，但不是字符串
        ]
        for bad in bad_values:
            with self.subTest(bad=repr(bad)):
                with self.assertRaises(FGS.GroupSyncError) as ctx:
                    self.load(feishu_sender_allowlist=bad)
                self.assertIn("feishu_sender_allowlist", str(ctx.exception))
                self.assertNotIn(secret, str(ctx.exception))
                self.assertNotIn(ALICE_PK, str(ctx.exception))

    def test_the_other_optional_keys_keep_working_next_to_it(self):
        """L1-FGS-404: 和别的可选键并存：`identity`、`message_format`、`reaction_map` 照旧严格校验，互不影响；未知的键仍被拒。"""
        cfg = self.load(feishu_sender_allowlist=[ALICE_PK], identity="email", message_format="card", reaction_map={"🎉": "Party"})
        self.assertEqual(FGS.sender_allowlist(cfg), frozenset({ALICE_PK}))
        for extra in ({"identity": "Email"}, {"message_format": "html"}, {"reaction_map": {"🎉": "not valid!"}},
                      {"feishu_sender_allowlists": [ALICE_PK]}):
            with self.assertRaises(FGS.GroupSyncError, msg=repr(extra)):
                self.load(feishu_sender_allowlist=[ALICE_PK], **extra)


class AllowlistRouting(unittest.TestCase):
    """`route_feishu_message` 的纯函数：先判身份（映射得上、在频道里），再判白名单；@ 的对象不受白名单限制。"""

    def route(self, msg, *, allowed=None, **kw):
        args = dict(open_id_to_pubkey={ALICE_OPEN: ALICE_PK, BOB_OPEN: BOB_PK, OWNER_OPEN: OWNER_PK, OUTSIDER_OPEN: OUTSIDER_PK},
                    bot_member_to_pubkey={}, channel_members={ALICE_PK, BOB_PK, OWNER_PK},
                    names={ALICE_PK: "Alice", BOB_PK: "Bob", OWNER_PK: "Owner"}, now=NOW)
        args.update(kw)
        if allowed is not None:
            args["allowed_senders"] = allowed
        return FGS.route_feishu_message(msg, **args)

    def test_no_allowlist_mirrors_everyone_who_is_in_the_channel(self):
        """L1-FGS-410: 不传白名单（None）就是现有行为：频道里已映射的人都镜像。"""
        for who, name in ((ALICE_OPEN, "Alice"), (BOB_OPEN, "Bob"), (OWNER_OPEN, "Owner")):
            self.assertEqual(self.route(fmsg("om_1", who, "hi")).text, f"[飞书] {name}：hi")
        self.assertIsNone(FGS.route_feishu_message.__kwdefaults__["allowed_senders"])

    def test_a_sender_on_the_list_is_mirrored_and_one_off_it_is_skipped(self):
        """L1-FGS-411: 白名单里的人照常镜像（署名格式不变）；已映射、在频道里但不在名单里的人：`sender_not_allowed`。"""
        allowed = frozenset({ALICE_PK})
        inbound = self.route(fmsg("om_1", ALICE_OPEN, "hi"), allowed=allowed)
        self.assertEqual((inbound.text, inbound.sender_pubkey), ("[飞书] Alice：hi", ALICE_PK))
        self.assertEqual(self.route(fmsg("om_2", BOB_OPEN, "hi"), allowed=allowed), "sender_not_allowed")
        self.assertEqual(self.route(fmsg("om_3", OWNER_OPEN, "hi"), allowed=frozenset({OWNER_PK, ALICE_PK})).sender_pubkey, OWNER_PK)
        # 空名单（配置不会给出，但函数本身要失败即关闭）是「谁都不让进」，不是「不限制」
        self.assertEqual(self.route(fmsg("om_4", ALICE_OPEN, "hi"), allowed=frozenset()), "sender_not_allowed")

    def test_identity_is_judged_before_the_list_and_both_must_hold(self):
        """L1-FGS-412: 先判身份，再判白名单，两者都要满足：映射不到的人（哪怕名单很宽）仍是 `unmapped_sender`；映射得上但已不在频道里的人
        （哪怕在名单里）仍是 `sender_not_in_channel`；不在名单里的、又不在频道里的，报的是身份这一关的原因；名单只能收窄，不能放宽。"""
        allowed = frozenset({ALICE_PK, OUTSIDER_PK})
        self.assertEqual(self.route(fmsg("om_1", EXTRA_OPEN, "x"), allowed=allowed), "unmapped_sender")
        self.assertEqual(self.route(fmsg("om_2", OUTSIDER_OPEN, "x"), allowed=allowed), "sender_not_in_channel")
        self.assertEqual(self.route(fmsg("om_3", OUTSIDER_OPEN, "x"), allowed=frozenset({ALICE_PK})), "sender_not_in_channel")
        self.assertEqual(self.route(fmsg("om_4", EXTRA_OPEN, "x"), allowed=frozenset({ALICE_PK})), "unmapped_sender")
        self.assertEqual(self.route(fmsg("om_5", BOB_OPEN, "x"), allowed=frozenset({ALICE_PK, OUTSIDER_PK})), "sender_not_allowed")

    def test_earlier_skip_reasons_are_unchanged(self):
        """L1-FGS-413: 已删除、系统消息、app 发的、空内容，跳过原因照旧，不因为有白名单而变成 `sender_not_allowed`。"""
        allowed = frozenset({ALICE_PK})
        self.assertEqual(self.route(fmsg("om_1", BOB_OPEN, "x", deleted=True), allowed=allowed), "deleted")
        self.assertEqual(self.route(fmsg("om_2", BOB_OPEN, "x", msg_type="system"), allowed=allowed), "system")
        self.assertEqual(self.route(fmsg("om_3", BOB_OPEN, "x", sender_type="app"), allowed=allowed), "bot")
        self.assertEqual(self.route(fmsg("om_4", BOB_OPEN, "  "), allowed=allowed), "empty")

    def test_mentioned_people_are_not_restricted_by_the_list(self):
        """L1-FGS-414: 白名单只管「谁的发言能进 Buzz」，不管「谁能被 @」：名单里的 Alice @ 了不在名单里的 Bob，p tag 照常产生。"""
        msg = fmsg("om_1", ALICE_OPEN, "@Bob 看下", mentions=[{"id": BOB_OPEN, "key": "@_user_1", "name": "Bob"}])
        self.assertEqual(self.route(msg, allowed=frozenset({ALICE_PK})).mentions, (BOB_PK,))

    def test_a_sender_that_is_skipped_costs_no_mention_lookup(self):
        """L1-FGS-415: 被白名单挡下的消息不为它取被 @ 的人的身份（union 模式里每取一次是一次飞书调用）：只解析了发信人。"""
        looked_up = []

        def resolve(open_id):
            looked_up.append(open_id)
            return open_id
        msg = fmsg("om_1", BOB_OPEN, "@Alice", mentions=[{"id": ALICE_OPEN, "key": "@_user_1", "name": "Alice"}])
        self.assertEqual(self.route(msg, allowed=frozenset({ALICE_PK}), resolve_id=resolve), "sender_not_allowed")
        self.assertEqual(looked_up, [BOB_OPEN])


# ================================ L2-1 ================================


class AllowlistRound(TmpCase):
    def go(self, w, identity="default", allowlist=None, **kw):
        env = env_for(self.tmp, identity, allowlist)
        return env, env.round(w, **kw)

    def two_people(self, w):
        w.messages = [fmsg("om_alice", ALICE_OPEN, "alice says hi"), fmsg("om_bob", BOB_OPEN, "bob says hi")]

    def test_without_the_key_everyone_in_the_channel_is_mirrored_as_before(self):
        """L2-1-FGS-400: 回归：配置里没有 `feishu_sender_allowlist` 时行为逐字节不变——Alice 和 Bob 的发言都镜像、署名格式不变，报告里没有
        `sender_not_allowed`；两种 identity 模式一样。"""
        for identity in IDENTITIES:
            with self.subTest(identity=identity):
                w = quiet_world(self.tmp)
                self.two_people(w)
                env, report = self.go(w, identity)
                self.assertEqual(said_in_buzz(w), ["[飞书] Alice：alice says hi", "[飞书] Bob：bob says hi"])
                self.assertEqual(report["to_buzz"], 2)
                self.assertNotIn("sender_not_allowed", report["skipped"])
                (env.state_dir / FGS.STATE_FILE).unlink()

    def test_an_allowed_sender_is_mirrored_and_a_mapped_one_off_the_list_is_not(self):
        """L2-1-FGS-401: 白名单只有 Alice：Alice 的发言正常镜像；Bob 已映射、在频道里，但不在名单里——不发送、不写账本、计一次
        `sender_not_allowed`；这不算需要关注（退出码 0）。两种 identity 模式一样。"""
        for identity in IDENTITIES:
            with self.subTest(identity=identity):
                w = quiet_world(self.tmp)
                self.two_people(w)
                env = env_for(self.tmp, identity, [ALICE_PK])
                (env.state_dir / FGS.STATE_FILE).unlink(missing_ok=True)
                report = env.round(w)
                self.assertEqual(said_in_buzz(w), ["[飞书] Alice：alice says hi"])
                self.assertEqual((report["to_buzz"], report["skipped"].get("sender_not_allowed")), (1, 1))
                self.assertFalse(FGS.needs_attention(report))
                state = env.state()
                self.assertEqual(list(state["f2b"]), ["om_alice"])  # 账本里只有被镜像的那一条
                self.assertNotIn("om_bob", json.dumps(state))
                self.assertEqual(state["f_unresolved"], {})

    def test_the_skipped_message_is_never_sent_later_either(self):
        """L2-1-FGS-402: 被挡下的消息只在重读窗口里被再读到，每一轮都再挡一次，一次也不发；名单没变、过了很久也一样。"""
        w = quiet_world(self.tmp)
        self.two_people(w)
        env = env_for(self.tmp, "default", [ALICE_PK])
        for minutes in (0, 1, 2, 30, 180):
            env.round(w, now=NOW + timedelta(minutes=minutes))
        self.assertEqual(said_in_buzz(w), ["[飞书] Alice：alice says hi"])
        self.assertFalse([c for c in w.buzz_sends() if "bob" in c["content"]])
        self.assertNotIn("om_bob", json.dumps(env.state()))

    def test_thread_replies_go_through_the_same_check(self):
        """L2-1-FGS-403: 话题里的回复走同一条路径：Alice（在名单里）的话题回复带 --reply-to 镜像；Bob（不在名单里）在同一个话题里的回复被挡下、
        计数、不写账本。两种 identity 模式一样。"""
        for identity in IDENTITIES:
            with self.subTest(identity=identity):
                w = quiet_world(self.tmp)
                w.messages = [fmsg("om_root", ALICE_OPEN, "question", thread_id="omt_om_root")]
                w.threads["om_root"] = [
                    fmsg("om_r_alice", ALICE_OPEN, "alice follows up", thread_id="omt_om_root", when=NOW - timedelta(seconds=40)),
                    fmsg("om_r_bob", BOB_OPEN, "bob chimes in", thread_id="omt_om_root", when=NOW - timedelta(seconds=30))]
                env = env_for(self.tmp, identity, [ALICE_PK])
                (env.state_dir / FGS.STATE_FILE).unlink(missing_ok=True)
                report = env.round(w)
                self.assertEqual(said_in_buzz(w), ["[飞书] Alice：question", "[飞书] Alice：alice follows up"])
                root_event = w.mirrored()[0]["id"]
                follow = next(c for c in w.buzz_sends() if c["content"] == "[飞书] Alice：alice follows up")
                self.assertEqual(follow["args"][follow["args"].index("--reply-to") + 1], root_event)
                self.assertEqual(report["skipped"].get("sender_not_allowed"), 1)
                self.assertNotIn("om_r_bob", json.dumps(env.state()["f2b"]))
                self.assertNotIn("bob", json.dumps(w.buzz_calls))

    def test_a_round_with_one_allowed_and_one_refused_sender_mixes_correctly(self):
        """L2-1-FGS-404: 同一轮里既有允许的又有不允许的：交错排列（Bob、Alice、Bob、Alice、Guest）时只有 Alice 的两条按原顺序镜像，
        其余三条各计一次；名单里有两个人（Alice、Bob）而 Guest 不在时也一样只挡 Guest。"""
        w = quiet_world(self.tmp)
        w.members.append({"pubkey": GUEST_PK, "role": "guest"})
        w.bindings[GUEST_PK] = GUEST_OPEN
        w.messages = [fmsg(f"om_{i}", who, text, when=NOW - timedelta(seconds=50 - i)) for i, (who, text) in enumerate([
            (BOB_OPEN, "b1"), (ALICE_OPEN, "a1"), (BOB_OPEN, "b2"), (ALICE_OPEN, "a2"), (GUEST_OPEN, "g1")])]
        env, report = self.go(w, "default", [ALICE_PK])
        self.assertEqual(said_in_buzz(w), ["[飞书] Alice：a1", "[飞书] Alice：a2"])
        self.assertEqual((report["to_buzz"], report["skipped"]["sender_not_allowed"]), (2, 3))
        w2 = quiet_world(self.tmp)
        w2.members.append({"pubkey": GUEST_PK, "role": "guest"})
        w2.bindings[GUEST_PK] = GUEST_OPEN
        w2.messages = list(w.messages)
        env2 = env_for(self.tmp, "default", [ALICE_PK, BOB_PK])
        (env2.state_dir / FGS.STATE_FILE).unlink()
        report2 = env2.round(w2)
        self.assertEqual(said_in_buzz(w2), ["[飞书] Bob：b1", "[飞书] Alice：a1", "[飞书] Bob：b2", "[飞书] Alice：a2"])
        self.assertEqual((report2["to_buzz"], report2["skipped"]["sender_not_allowed"]), (4, 1))

    def test_people_mentioned_by_an_allowed_sender_need_not_be_on_the_list(self):
        """L2-1-FGS-405: @ 提及的对象不受白名单限制：Alice（在名单里）在飞书里 @Bob 和 @agent，镜像出去的消息带 Bob 和 agent 的 p tag，
        尽管 Bob 不在名单里。两种 identity 模式一样。"""
        for identity in IDENTITIES:
            with self.subTest(identity=identity):
                w = quiet_world(self.tmp)
                w.messages = [fmsg("om_1", ALICE_OPEN, "@Bob @helper-agent 看下", mentions=[
                    {"id": BOB_OPEN, "key": "@_user_1", "name": "Bob"},
                    {"id": base.AGENT_BOT_MEMBER, "key": "@_user_2", "name": "helper-agent"}])]
                env = env_for(self.tmp, identity, [ALICE_PK])
                (env.state_dir / FGS.STATE_FILE).unlink(missing_ok=True)
                env.round(w)
                (mirrored,) = w.mirrored()
                self.assertEqual(sorted(t[1] for t in mirrored["tags"] if t[0] == "p"), sorted([BOB_PK, base.AGENT_PK]))

    def test_identity_comes_first_an_unmapped_person_stays_unmapped_whatever_the_list_says(self):
        """L2-1-FGS-406: 先判身份再判白名单：映射不到的人（不在频道的 EXTRA、没有绑定的 Carol）报 `unmapped_sender`，不是
        `sender_not_allowed`；已绑定但不在频道里的 OUTSIDER（映射只给频道里的人），哪怕写进了名单，也同样是 `unmapped_sender`，不会被镜像。"""
        for identity in IDENTITIES:
            with self.subTest(identity=identity):
                w = FakeWorld(self.tmp)
                w.bindings[OUTSIDER_PK] = OUTSIDER_OPEN
                w.messages = [fmsg("om_extra", EXTRA_OPEN, "not in the channel"),
                              fmsg("om_carol", base.CAROL_OPEN, "no binding"),
                              fmsg("om_out", OUTSIDER_OPEN, "bound but left the channel"),
                              fmsg("om_bob", BOB_OPEN, "mapped, not on the list")]
                env = env_for(self.tmp, identity, [ALICE_PK, OUTSIDER_PK])
                (env.state_dir / FGS.STATE_FILE).unlink(missing_ok=True)
                report = env.round(w)
                self.assertEqual(said_in_buzz(w), [])
                self.assertEqual(report["skipped"], {"unmapped_sender": 3, "sender_not_allowed": 1})

    def test_an_allowed_pubkey_whose_account_is_shared_is_still_unmapped(self):
        """L2-1-FGS-407: 名单里的 Alice 和别的 pubkey 绑到了同一个飞书账号（union_id 相同）：身份这一关就过不了（两把 key 都算映射不到），
        名单不能把它救回来——她在飞书里的发言不镜像，报 `unmapped_sender`，`identity_conflicts` 照记。"""
        w = quiet_world(self.tmp)
        twin = "66" * 32
        w.members.append({"pubkey": twin, "role": "member"})
        w.display[twin] = "Alice2"
        w.bindings[twin] = ALICE_OPEN
        w.messages = [fmsg("om_dup", ALICE_OPEN, "who said this?")]
        env, report = self.go(w, "default", [ALICE_PK, twin])
        self.assertEqual(said_in_buzz(w), [])
        self.assertEqual((report["skipped"], report["identity_conflicts"]), ({"unmapped_sender": 1}, 1))

    def test_a_pairing_that_cannot_be_vouched_for_is_skipped_for_the_identity_reason(self):
        """L2-1-FGS-408: union_id 模式里飞书答不出发信人的 union_id（配对不上）：仍是 `sender_unpaired`，不是 `sender_not_allowed`，
        也不进白名单判断；这一条留在重试里的处理不变（不是错误）。"""
        w = quiet_world(self.tmp)
        w.messages = [fmsg("om_1", ALICE_OPEN, "hello")]
        w.message_get_tamper = lambda mid, id_type, item: dict(item, sender=dict(item["sender"], id_type="open_id"))
        env, report = self.go(w, "default", [ALICE_PK])
        self.assertEqual(said_in_buzz(w), [])
        self.assertEqual(report["skipped"], {"sender_unpaired": 1})

    def test_the_buzz_to_feishu_direction_is_untouched(self):
        """L2-1-FGS-409: 白名单只管飞书 → Buzz：Buzz 上 Bob（不在名单里）和 Alice 的发言照常发到飞书，和没有白名单时发出的一模一样
        （文字、发送顺序、发送者身份）。"""
        def run(allowlist):
            w = quiet_world(self.tmp)
            w.events = [event(eid(1), ALICE_PK, "from alice", created_at=T0 + 1), event(eid(2), BOB_PK, "from bob", created_at=T0 + 2),
                        event(eid(3), base.AGENT_PK, "from the agent", created_at=T0 + 3)]
            env = env_for(self.tmp, "default", allowlist)
            (env.state_dir / FGS.STATE_FILE).unlink(missing_ok=True)
            report = env.round(w)
            return [(text_of(c), c["app"]) for c in w.lark_sends()], report["to_feishu"]
        without, with_list = run(None), run([ALICE_PK])
        self.assertEqual(with_list, without)
        self.assertEqual(with_list[1], 3)
        self.assertIn(("Bob（Buzz）：from bob", base.AGENT_APP), with_list[0])

    def test_a_refused_send_stops_retrying_once_its_sender_is_taken_off_the_list(self):
        """L2-1-FGS-410: 名单是每一轮从配置里重新读的：Bob 的消息先前被 Buzz 确定拒绝、留在重试里（`retry`），之后 owner 把名单收窄成
        只有 Alice——下一轮这条消息按 `sender_not_allowed` 关闭，不再重发，也不留在未决项里。"""
        w = quiet_world(self.tmp)
        w.messages = [fmsg("om_bob", BOB_OPEN, "bob says hi")]
        w.buzz_send_fail = ["rejected"]
        env = env_for(self.tmp, "default", [ALICE_PK, BOB_PK])
        env.round(w)
        self.assertTrue(FGS._is_retry(env.state()["f2b"]["om_bob"]))
        sends_before = len(w.buzz_sends())
        env2 = env_for(self.tmp, "default", [ALICE_PK])  # 同一个 state 目录，名单收窄了
        report = env2.round(w, now=NOW + timedelta(minutes=1))
        self.assertEqual(len(w.buzz_sends()), sends_before)
        self.assertEqual(report["skipped"].get("sender_not_allowed"), 1)
        self.assertEqual(env2.state()["f_unresolved"], {})
        self.assertEqual(said_in_buzz(w), [])
        env2.round(w, now=NOW + timedelta(minutes=2))
        self.assertEqual(len(w.buzz_sends()), sends_before)

    def test_state_written_without_a_list_keeps_working_and_gets_no_new_fields(self):
        """L2-1-FGS-411: 旧 state 兼容、也不动 state 结构：先不带名单跑两轮（Alice、Bob 都镜像），再在同一个 state 目录里加上名单
        （只有 Alice）继续跑——已镜像的不重发，Bob 的新消息被挡下；state 的键集合与不带名单时一模一样，没有新字段。"""
        w = quiet_world(self.tmp)
        w.messages = [fmsg("om_alice", ALICE_OPEN, "alice one"), fmsg("om_bob", BOB_OPEN, "bob one")]
        env_plain = env_for(self.tmp, "default")
        env_plain.round(w)
        env_plain.round(w, now=NOW + timedelta(minutes=1))
        keys_before = set(env_plain.state())
        self.assertEqual(said_in_buzz(w), ["[飞书] Alice：alice one", "[飞书] Bob：bob one"])
        w.messages += [fmsg("om_alice2", ALICE_OPEN, "alice two", when=NOW + timedelta(minutes=1, seconds=10)),
                       fmsg("om_bob2", BOB_OPEN, "bob two", when=NOW + timedelta(minutes=1, seconds=20))]
        env = env_for(self.tmp, "default", [ALICE_PK])
        report = env.round(w, now=NOW + timedelta(minutes=2))
        self.assertEqual(said_in_buzz(w), ["[飞书] Alice：alice one", "[飞书] Bob：bob one", "[飞书] Alice：alice two"])
        self.assertEqual(report["skipped"].get("sender_not_allowed"), 1)
        self.assertEqual(set(env.state()), keys_before)
        self.assertFalse(FGS.needs_attention(report))
        self.assertNotIn("allow", json.dumps(env.state()).lower())


class AllowlistLeaks(TmpCase):
    """报告、stderr、state 里没有 pubkey 的全值，也没有被挡下那条消息的正文；owner 的 key 不进任何子进程。"""

    def run_main(self, w, env, now=NOW):
        out, err = self.tmp / "out.json", self.tmp / "err.txt"
        args = ["round", "--config", str(env.config), "--state-dir", str(env.state_dir)]
        w.clock = now
        with open(out, "w") as fh, open(err, "w") as eh, contextlib.redirect_stderr(eh):
            code = FGS.main(args, base_env=env.base_env, runner=w, stdout=fh, now=now, http=w.http_get)
        return code, out.read_text(), err.read_text()

    def world(self):
        w = quiet_world(self.tmp)
        w.messages = [fmsg("om_alice", ALICE_OPEN, "alice body text"), fmsg("om_bob", BOB_OPEN, "bob secret body text")]
        return w

    def test_neither_the_report_nor_stderr_carries_a_pubkey_or_the_refused_body(self):
        """L2-1-FGS-412: 两种 identity 模式下，stdout 的报告和 stderr 里没有名单里、名单外任何人的 pubkey 全值，没有 open_id / union_id 的全值，
        没有被挡下那条消息的正文；跳过原因只有名字和计数；退出码 0。"""
        secrets = [ALICE_PK, BOB_PK, OWNER_PK, base.MIRROR_PK, "bob secret body text", "alice body text",
                   *(v for o in (ALICE_OPEN, BOB_OPEN, OWNER_OPEN) for v in (o, union_of(o), bridge_open_of(o)))]
        for identity in IDENTITIES:
            with self.subTest(identity=identity):
                w = self.world()
                env = env_for(self.tmp, identity, [ALICE_PK])
                (env.state_dir / FGS.STATE_FILE).unlink(missing_ok=True)
                code, out, err = self.run_main(w, env)
                self.assertEqual(code, FGS.EXIT_OK)
                self.assertEqual(json.loads(out)["skipped"], {"sender_not_allowed": 1})
                for secret in secrets:
                    self.assertNotIn(secret, out, secret)
                    self.assertNotIn(secret, err, secret)
                # Bob 是频道成员：双向成员同步（ADR-0020）的成员快照里本来就有他，与名单无关；名单拒掉的话在 state 的其余部分一字不留
                state = json.loads((env.state_dir / FGS.STATE_FILE).read_text())
                rest = json.dumps({k: v for k, v in state.items() if k not in ("buzz_seen", "people_seen")})
                for secret in (BOB_PK, "bob secret body text"):
                    self.assertNotIn(secret, rest)
                self.assertNotIn("bob secret body text", json.dumps(state))

    def test_a_refused_config_does_not_leak_either(self):
        """L2-1-FGS-413: 名单写错、整份配置被拒时，命令行的错误（stderr）说明是哪个键，不带出名单里的任何值；不发消息、不动 state。"""
        w = self.world()
        env = env_for(self.tmp, "default", [ALICE_PK, base.AGENT_PK.upper()])
        out, err = self.tmp / "o.json", self.tmp / "e.txt"
        with open(out, "w") as fh, open(err, "w") as eh, contextlib.redirect_stderr(eh):
            code = FGS.main(["round", "--config", str(env.config), "--state-dir", str(env.state_dir)], base_env=env.base_env,
                            runner=w, stdout=fh, now=NOW, http=w.http_get)
        self.assertEqual(code, FGS.EXIT_ERROR)
        self.assertIn("feishu_sender_allowlist", err.read_text())
        for secret in (ALICE_PK, base.AGENT_PK, base.AGENT_PK.upper()):
            self.assertNotIn(secret, err.read_text())
            self.assertNotIn(secret, out.read_text())
        self.assertEqual((w.lark_calls, w.buzz_calls), ([], []))
        self.assertFalse((env.state_dir / FGS.STATE_FILE).exists())

    def test_the_owner_key_never_reaches_a_child_process_with_a_list_set(self):
        """L2-1-FGS-414: 设了名单以后，owner 的 key 仍然只用来签 bridge 那一个 GET：不进 buzz 或 lark 子进程的环境和参数，不进报告、stderr、
        state；每次飞书 → Buzz 的发送都用镜像身份的 key；子进程环境仍是白名单。两种 identity 模式一样。"""
        for identity in IDENTITIES:
            with self.subTest(identity=identity):
                w = self.world()
                env = env_for(self.tmp, identity, [ALICE_PK])
                (env.state_dir / FGS.STATE_FILE).unlink(missing_ok=True)
                code, out, err = self.run_main(w, env)
                self.assertTrue(w.buzz_sends())
                for call in w.buzz_sends():
                    self.assertEqual(call["key"], base.MIRROR_KEY)
                for call in w.buzz_calls + w.lark_calls:
                    self.assertNotIn(OWNER_KEY, json.dumps(call.get("env", {})))
                    self.assertNotIn(OWNER_KEY, json.dumps(call.get("args", [])))
                    self.assertNotIn("GITLAB_TOKEN", call["env"])
                    self.assertNotIn("DBUS_SESSION_BUS_ADDRESS", call["env"])
                for text in (out, err, (env.state_dir / FGS.STATE_FILE).read_text()):
                    self.assertNotIn(OWNER_KEY, text)


class AllowlistDocs(unittest.TestCase):
    """参考文档和脚本 README 要写明白名单做什么、不做什么：文档和脚本对不上是这类改动最常见的出错方式。"""

    SKILL = base.TESTS.parent
    GROUP_DOC = (SKILL / "references" / "feishu-group-sync.md").read_text(encoding="utf-8")
    MESSAGE_DOC = (SKILL / "references" / "feishu-message-sync.md").read_text(encoding="utf-8")
    DOC = (SKILL / "references" / "feishu-routing-policy.md").read_text(encoding="utf-8")
    HEADING = "## 只让特定的人的发言进 Buzz（个人频道 / 只认 owner 的 agent）"

    def section(self):
        start = self.DOC.find(self.HEADING)
        self.assertGreaterEqual(start, 0, msg="the reference has no section for the sender allowlist")
        end = self.DOC.find("\n## ", start + 1)
        return self.DOC[start:end if end > 0 else len(self.DOC)]

    def test_the_section_says_what_it_is_for_how_to_set_it_and_what_it_does_not_do(self):
        """L1-FGS-416: 「只让特定的人的发言进 Buzz」一节写明：用途（个人频道、只认 owner 的 agent）、配置示例（`feishu_sender_allowlist`、64 位
        小写 hex、上限 50、去重、缺省不限制、写错整份配置被拒）、语义（先判身份再判白名单、`sender_not_allowed`、话题回复同样、@ 的对象
        不受限制、只管飞书 → Buzz、不影响 Buzz → 飞书）、与「不要把镜像身份加进只认 owner 的 agent 的名单」的关系（风险缩小到允许的
        那几个人的飞书账号，不是消除）、建议把「谁可以添加群成员」设成仅群主。"""
        section = self.section()
        for term in ("`feishu_sender_allowlist`", "64 位小写 hex", "50", "去重", "缺省", "不限制", "整份配置被拒", "`sender_not_allowed`",
                     "先判身份", "`unmapped_sender`", "话题", "@", "Buzz → 飞书", "只认 owner", "镜像身份", "缩小", "不是消除",
                     "谁可以添加群成员", "仅群主", "个人"):
            self.assertIn(term, section, msg=term)

    def test_the_config_and_the_skip_list_and_the_scripts_readme_mention_it(self):
        """L1-FGS-417: 配置一节的可选键清单、飞书 → Buzz 的「跳过的情况」、脚本 README 该脚本一行都提到 `feishu_sender_allowlist` /
        `sender_not_allowed`；配置示例之外没有第二种写法（不写就是不限制）。"""
        start = self.GROUP_DOC.find("## 配置（0600")
        config = self.GROUP_DOC[start:self.GROUP_DOC.find("\n## ", start + 1)]
        self.assertIn("`feishu_sender_allowlist`", config)
        self.assertRegex(config, r"只有[^。]*`feishu_sender_allowlist`[^。]*可以不写")
        run = self.MESSAGE_DOC[self.MESSAGE_DOC.find("4. **飞书 → Buzz**"):self.MESSAGE_DOC.find("5. **表情双向同步")]
        self.assertIn("`sender_not_allowed`", run)
        readme = (self.SKILL / "references" / "scripts" / "README.md").read_text(encoding="utf-8")
        row = next(line for line in readme.splitlines() if "buzz_feishu_group_sync.py" in line)
        self.assertIn("feishu_sender_allowlist", row)


if __name__ == "__main__":
    unittest.main()
