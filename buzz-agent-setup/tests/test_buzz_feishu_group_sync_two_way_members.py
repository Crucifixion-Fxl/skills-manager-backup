"""飞书群 ↔ 频道成员双向同步（ADR-0020，engineering/skills#148）。

以前的成员对账只有一个方向：以 Buzz 频道为准改飞书群。ADR-0020 把它改成三方对比——每轮拿 Buzz 成员、飞书成员和上一轮
结束时的快照（state 里的 `feishu_seen` / `buzz_seen`）比，哪边变了就把变化带到另一边：

- 飞书里新拉进来的人 / agent 的 bot：认得出是谁，就由 signer key（people 接口那把，频道 owner/admin）在进程内签 kind 9000、
  `POST /events` 加进频道（人是 member，agent 是 bot）；
- 飞书里移出的：认得出、而且飞书成员列表完整读到，才签 kind 9001 移出频道；
- Buzz 里移出的：按快照里记下的飞书身份把他移出群，不看 `remove_extras`（它只剩首轮基线时清理多余成员这一个用处）。

兜底：首轮只记基线；同一轮两边冲突以 Buzz 为准；一轮删除超过 BULK_REMOVAL_LIMIT 全部暂停；频道 owner、同步签名身份、镜像身份
不会被移出频道（只在群里提示一次）；agent 的 `channel_add_policy` 拒绝时群里提示一次、不每轮重试；认不出的人留在群里、提示一次去绑定。
从飞书来的变动由镜像身份在频道里发一行说明，每轮最多一条。

认人：人按 bridge 的绑定（本频道现存成员）、本 state 记住的人、可选的全机共享缓存 `people_cache_file`；agent 按 app_id：
本机配置、ADR-0019 的目录，以及出现未知 bot 时全量读一次 kind:30177 建的反查表。

沿用 test_buzz_feishu_group_sync.py 的 FakeWorld / Env / 常量。用例编号从 800 起。
"""

import base64
import hashlib
import json
import os
import sys
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_buzz_feishu_group_sync as base  # noqa: E402
from test_buzz_feishu_group_sync import (  # noqa: E402
    AGENT2_KEY, AGENT2_PK, AGENT_APP, AGENT_BOT_MEMBER, AGENT_PK, ALICE_OPEN, ALICE_PK, API_ORIGIN, BOB_OPEN, BOB_PK,
    CHANNEL, EXTRA_OPEN,
    FGS, MEDIA_ORIGIN, MIRROR_KEY, MIRROR_PK, NOW, OUTSIDER_OPEN, OUTSIDER_PK, OWNER_APP, OWNER_KEY, OWNER_OPEN, OWNER_PK,
    T0, TEAM_BOT_APP, TmpCase, Env, FakeWorld, text_of, union_of, write_owner_only,
)
from test_buzz_feishu_group_sync_agent_directory import (  # noqa: E402
    AGENT2_APP, AGENT2_BOT_MEMBER, FOREIGN_OWNER_PK, nostr_event, profile, policy, public_profile, published,
    signed_identity,
)


def setUpModule():
    base.setUpModule()


def tearDownModule():
    base.tearDownModule()


EVENTS_URL = f"{MEDIA_ORIGIN}/events"
AUTH_TAG = '["auth","' + OWNER_PK + '","","' + "ab" * 64 + '"]'
NOTICE_PREFIX = "飞书群同步："
DESK_PK = "dd" * 32
DESK_APP = "cli_desk0000000001"
DESK_BOT_MEMBER = "ou_deskbot0000000000000000000001"


def user_key(open_id):
    """union 模式（Env 缺省）下飞书用户在快照里的键。"""
    return "u:" + union_of(open_id)


class TwoWay(TmpCase):
    """一个频道 + 一个群，按轮推进。镜像 env 带 BUZZ_AUTH_TAG（本机实际的镜像 env 都有）。"""

    def env(self, **overrides):
        mirror_env = write_owner_only(self.tmp / "mirror-auth.env", "\n".join([
            f"BUZZ_PRIVATE_KEY={MIRROR_KEY}", "BUZZ_RELAY_URL=https://relay.test", f"BUZZ_AUTH_TAG='{AUTH_TAG}'", ""]))
        for directory in ("desk-cfg", "desk-data"):
            (self.tmp / directory).mkdir(mode=0o700, exist_ok=True)
        agents = {AGENT_PK: {"app_id": AGENT_APP, "lark_config_dir": str(self.tmp / "agent-cfg"),
                             "lark_data_dir": str(self.tmp / "agent-data")},
                  DESK_PK: {"app_id": DESK_APP, "lark_config_dir": str(self.tmp / "desk-cfg"),
                            "lark_data_dir": str(self.tmp / "desk-data")}}
        agents.update(overrides.pop("agents", {}))
        return Env(self.tmp, mirror_env_file=str(mirror_env), desk_pubkey=DESK_PK, agents=agents, **overrides)

    def world(self):
        world = FakeWorld(self.tmp)
        world.members.append({"pubkey": DESK_PK, "role": "bot"})
        world.display[DESK_PK] = "test-desk"
        world.agent_dirs[str(self.tmp / "desk-cfg")] = DESK_APP
        world.bot_members[DESK_APP] = DESK_BOT_MEMBER
        world.bots[DESK_APP] = DESK_BOT_MEMBER
        return world

    def settled(self, w, env=None):
        """首轮（记基线、按现状单向对账）再加一轮（吸收自己上一轮对飞书的改动）之后的世界。"""
        env = env or self.env()
        env.round(w)
        env.round(w)
        w.relay_write_attempts.clear()
        return env

    def notices(self, w):
        return [e for e in w.events if e["pubkey"] == MIRROR_PK and e["kind"] == 9 and e["content"].startswith(NOTICE_PREFIX)]

    def feishu_notes(self, w, needle):
        return [c for c in w.lark_sends() if c["app"] == DESK_APP and needle in text_of(c)]


# ================================ L1 : 配置 ================================


class Config(TmpCase):
    def raw(self):
        return json.loads(Env(self.tmp).config.read_text())

    def load(self, **extra):
        return FGS.load_config(write_owner_only(self.tmp / "c.json", json.dumps(dict(self.raw(), **extra))))

    def test_the_new_keys_are_optional_and_default_to_two_way(self):
        """L1-FGS-800: `membership_sync` / `reaction_sync` / `people_cache_file` 都是可选键；不写时成员与表情都是 "two_way"。"""
        cfg = self.load()
        self.assertEqual(FGS.membership_sync_mode(cfg), "two_way")
        self.assertEqual(FGS.reaction_sync_mode(cfg), "two_way")
        for key in ("membership_sync", "reaction_sync", "people_cache_file"):
            self.assertIn(key, FGS.OPTIONAL_CONFIG_KEYS)
            self.assertNotIn(key, FGS.CONFIG_KEYS)
        self.assertEqual(FGS.membership_sync_mode(self.load(membership_sync="buzz_to_feishu")), "buzz_to_feishu")
        self.assertEqual(FGS.reaction_sync_mode(self.load(reaction_sync="agents_only")), "agents_only")

    def test_bad_values_are_refused_without_echoing_them(self):
        """L1-FGS-801: 只认列出的值；people_cache_file 必须是绝对路径。错误信息不回显写了什么。"""
        for key, bad in (("membership_sync", "both-ways-secret"), ("membership_sync", 1), ("reaction_sync", "all-secret"),
                         ("reaction_sync", None), ("people_cache_file", "relative/cache-secret.json"), ("people_cache_file", 7)):
            with self.subTest(key=key, bad=bad):
                with self.assertRaises(FGS.GroupSyncError) as caught:
                    self.load(**{key: bad})
                self.assertNotIn("secret", str(caught.exception))


# ================================ L1 : state ================================


class StateFields(TmpCase):
    @staticmethod
    def pending(kind=9000, target=AGENT_PK, role="bot", nonce="ab" * 32, stream="ef" * 32, sequence=1,
                created_at=base.ts(NOW)):
        tags = [["h", CHANNEL], ["p", target]] + ([["role", role]] if role else [])
        tags += [["feishu-member-op", nonce], ["feishu-member-stream", stream],
                 ["feishu-member-seq", str(sequence)]]
        return FGS.sign_event(OWNER_KEY, kind, tags, "", created_at)

    def test_the_member_event_ledger_is_added_empty_to_an_existing_two_way_state(self):
        """L1-FGS-802b: 已经有双向成员快照、但还没有 member_events 的旧 state 能升级；未决成员事件从空开始。"""
        state_dir = self.tmp / "state"
        state_dir.mkdir(mode=0o700)
        old = FGS.asdict(FGS.State(binding=f"{CHANNEL}|oc_x", floor=1, buzz_since=1, feishu_since=1,
                                   members_synced=1))
        old.pop("member_events", None)
        old.pop("member_event_stream", None)
        old.pop("member_event_seq", None)
        old.pop("member_event_blocks", None)
        write_owner_only(state_dir / FGS.STATE_FILE, json.dumps(old))
        loaded = FGS.load_state(state_dir)
        self.assertEqual((loaded.member_events, loaded.member_event_stream, loaded.member_event_seq,
                          loaded.member_event_blocks), ({}, "", 0, {}))

    def test_losing_only_the_member_event_ledger_fails_closed(self):
        """L1-FGS-802g: 新 schema 只丢 member_events 可能丢掉结果未知的写入；不能误认成旧 schema 后补空账本。"""
        state_dir = self.tmp / "state"
        state_dir.mkdir(mode=0o700)
        damaged = FGS.asdict(FGS.State(binding=f"{CHANNEL}|oc_x", member_event_stream="ef" * 32,
                                        member_event_seq=1))
        damaged.pop("member_events")
        write_owner_only(state_dir / FGS.STATE_FILE, json.dumps(damaged))

        with self.assertRaises(FGS.GroupSyncError):
            FGS.load_state(state_dir)

    def test_the_stream_is_lazily_added_to_a_settled_sequence_ledger(self):
        """L1-FGS-802e: 上一版已经分配过序号、但没有未决事件时可以升级；保留高水位，下一次写入再生成 stream。"""
        state_dir = self.tmp / "state"
        state_dir.mkdir(mode=0o700)
        old = FGS.asdict(FGS.State(binding=f"{CHANNEL}|oc_x", member_event_seq=7))
        old.pop("member_event_stream", None)
        write_owner_only(state_dir / FGS.STATE_FILE, json.dumps(old))

        loaded = FGS.load_state(state_dir)

        self.assertEqual((loaded.member_event_stream, loaded.member_event_seq, loaded.member_events), ("", 7, {}))

    def test_an_unsettled_pre_stream_event_fails_closed(self):
        """L1-FGS-802f: 上一版未决事件没有 stream，不能在升级时改签或猜测；整份 state 拒绝，等待人工核对。"""
        state_dir = self.tmp / "state"
        state_dir.mkdir(mode=0o700)
        operation = f"9000|{AGENT_PK}|bot|b:{AGENT_APP}"
        tags = [["h", CHANNEL], ["p", AGENT_PK], ["role", "bot"],
                ["feishu-member-op", "ab" * 32], ["feishu-member-seq", "1"]]
        old = FGS.asdict(FGS.State(binding=f"{CHANNEL}|oc_x", member_events={
            operation: FGS.sign_event(OWNER_KEY, 9000, tags, "", base.ts(NOW)),
        }, member_event_seq=1))
        old.pop("member_event_stream", None)
        write_owner_only(state_dir / FGS.STATE_FILE, json.dumps(old))

        with self.assertRaises(FGS.GroupSyncError):
            FGS.load_state(state_dir)

    def test_an_old_state_reads_as_never_synced_both_ways(self):
        """L1-FGS-802: 旧版本写的 state（没有成员快照等字段）照常读入：新字段取空值，`members_synced` 是 0（下一轮只记基线）。"""
        state_dir = self.tmp / "state"
        state_dir.mkdir(mode=0o700)
        old = FGS.asdict(FGS.State(binding=f"{CHANNEL}|oc_x", floor=1, buzz_since=1, feishu_since=1))
        for field in ("members_synced", "feishu_seen", "buzz_seen", "member_notes", "member_events",
                      "member_event_stream", "member_event_seq", "member_event_blocks", "people_seen", "rwatch", "f2r",
                      "agent_intros_initialized", "agent_intros"):
            old.pop(field, None)
        write_owner_only(state_dir / FGS.STATE_FILE, json.dumps(old))
        state = FGS.load_state(state_dir)
        self.assertEqual((state.members_synced, state.feishu_seen, state.buzz_seen, state.member_notes, state.people_seen,
                          state.member_events, state.member_event_stream, state.member_event_seq, state.member_event_blocks,
                          state.rwatch, state.f2r, state.agent_intros_initialized, state.agent_intros),
                         (0, {}, {}, {}, {}, {}, "", 0, {}, {}, {}, False, {}))

    def test_malformed_new_fields_are_refused(self):
        """L1-FGS-803: 新字段类型不对（快照的值不是字符串、提示记录的值不是整数、members_synced 不是整数）整份 state 拒绝，不当作空的。"""
        state_dir = self.tmp / "state"
        state_dir.mkdir(mode=0o700)
        for field, bad in (("feishu_seen", {"u:x": 1}), ("buzz_seen", []), ("member_notes", {"a": "b"}),
                           ("member_events", {"9000|not-a-pubkey|bot|b:cli_x": 1}), ("members_synced", "1"),
                           ("member_event_stream", "not-a-stream"),
                           ("member_event_seq", -1), ("member_event_blocks", {"not-an-operation": "retry_refused"}),
                           ("people_seen", {"k": None}), ("rwatch", {"om": 3}), ("f2r", {"k": 1}),
                           ("agent_intros_initialized", 1), ("agent_intros", {"not-a-pubkey": "baseline"}),
                           ("agent_intros", {AGENT2_PK: "made-up-terminal"})):
            with self.subTest(field=field):
                data = dict(FGS.asdict(FGS.State()), **{field: bad})
                write_owner_only(state_dir / FGS.STATE_FILE, json.dumps(data))
                with self.assertRaises(FGS.GroupSyncError):
                    FGS.load_state(state_dir)

    def test_member_event_ledger_round_trips_the_exact_signed_event_and_has_a_hard_limit(self):
        """L1-FGS-802c: 未决成员账本保存完整、已验签的事件（不是只有秒级时间）；读取时核对 operation、频道、
        target、role、nonce、stream、序号、id 与签名，并拒绝超过硬上限的 state，而不是无界增长或淘汰未决项。"""
        state_dir = self.tmp / "state"
        state_dir.mkdir(mode=0o700)
        operation = f"9000|{AGENT_PK}|bot|b:{AGENT_APP}"
        pending = self.pending()
        state = FGS.State(binding=f"{CHANNEL}|oc_x", member_events={operation: pending},
                          member_event_stream="ef" * 32, member_event_seq=1)
        FGS.save_state(state_dir, state)
        self.assertEqual(FGS.load_state(state_dir).member_events, {operation: pending})

        second = f"9001|{AGENT_PK}||b:{AGENT_APP}"
        with mock.patch.object(FGS, "MEMBER_EVENTS_MAX", 1):
            over_limit = FGS.State(binding=f"{CHANNEL}|oc_x", member_events={
                operation: pending, second: self.pending(kind=9001, role=None, nonce="cd" * 32, sequence=2),
            }, member_event_stream="ef" * 32, member_event_seq=2)
            with self.assertRaises(FGS.GroupSyncError):
                FGS.prune_state(over_limit)
            FGS.save_state(state_dir, over_limit)
            with self.assertRaises(FGS.GroupSyncError):
                FGS.load_state(state_dir)

    def test_member_event_ledger_rejects_a_tampered_exact_event(self):
        """L1-FGS-802d: 未决事件的 id、签名、标签或与 operation 的对应关系被改过，整份 state 失败关闭。"""
        state_dir = self.tmp / "state"
        state_dir.mkdir(mode=0o700)
        operation = f"9000|{AGENT_PK}|bot|b:{AGENT_APP}"
        good = self.pending()
        bad_events = [dict(good, id="00" * 32), dict(good, sig="00" * 64),
                      dict(good, tags=good["tags"][:-1]), dict(good, kind=9001)]
        for bad in bad_events:
            with self.subTest(field=next(k for k in bad if bad[k] != good.get(k))):
                FGS.save_state(state_dir, FGS.State(binding=f"{CHANNEL}|oc_x", member_events={operation: bad},
                                                    member_event_stream="ef" * 32, member_event_seq=1))
                with self.assertRaises(FGS.GroupSyncError):
                    FGS.load_state(state_dir)


# ================================ L1 : 发事件（POST /events） ================================


class PublishEvent(unittest.TestCase):
    def call(self, answer, auth_tag=None, kind=9000, tags=None):
        seen = []

        def http(url, headers, timeout, body=None):
            seen.append({"url": url, "headers": dict(headers), "body": body})
            if isinstance(answer, Exception):
                raise answer
            return answer(json.loads(body)) if callable(answer) else answer
        tags = tags if tags is not None else [["h", CHANNEL], ["p", ALICE_PK], ["role", "member"]]
        out = FGS.publish_event("https://relay.test", OWNER_KEY, kind, tags, "", http, NOW, auth_tag=auth_tag)
        return out, seen

    def test_it_signs_the_event_and_the_request_with_one_key(self):
        """L1-FGS-804: 事件按 NIP-01 在进程内签名（id、sig 都对，作者是这把 key）；POST 到 {relay}/events，签名头的 payload 就是这条事件的
        sha256；relay 接受后返回事件 id。"""
        accepted = lambda ev: (200, json.dumps({"event_id": ev["id"], "accepted": True, "message": ""}).encode())
        event_id, seen = self.call(accepted)
        (req,) = seen
        self.assertEqual(req["url"], "https://relay.test/events")
        ev = json.loads(req["body"])
        self.assertEqual(ev["id"], event_id)
        self.assertEqual(ev["pubkey"], OWNER_PK)
        ser = json.dumps([0, ev["pubkey"], ev["created_at"], ev["kind"], ev["tags"], ev["content"]], separators=(",", ":"),
                         ensure_ascii=False)
        self.assertEqual(hashlib.sha256(ser.encode()).hexdigest(), ev["id"])
        self.assertTrue(FGS.sync.nk.schnorr_verify(bytes.fromhex(ev["id"]), bytes.fromhex(OWNER_PK), bytes.fromhex(ev["sig"])))
        head = json.loads(base64.b64decode(req["headers"]["Authorization"][len("Nostr "):]))
        self.assertIn(["payload", hashlib.sha256(req["body"]).hexdigest()], head["tags"])
        self.assertNotIn("x-auth-tag", req["headers"])

    def test_an_agent_identity_sends_its_auth_tag(self):
        """L1-FGS-805: NIP-OA 身份（镜像）要带 `x-auth-tag` 头，relay 才认它是成员（与 Buzz CLI 的做法一致）。"""
        accepted = lambda ev: (200, json.dumps({"event_id": ev["id"], "accepted": True, "message": ""}).encode())
        _, seen = self.call(accepted, auth_tag=AUTH_TAG)
        self.assertEqual(seen[0]["headers"]["x-auth-tag"], AUTH_TAG)

    def test_a_refusal_carries_the_relays_reason(self):
        """L1-FGS-806: relay 说不（400 {"error"} 或 200 accepted=false）是 RelayRefused，带着 relay 给的理由（例如 channel_add_policy 的
        `policy:owner_only …`）；`refused_by_policy` 认得出这两种策略拒绝。"""
        for answer in ((400, json.dumps({"error": "policy:owner_only — only the agent owner can add this agent"}).encode()),
                       (200, json.dumps({"event_id": "x", "accepted": False, "message": "policy:nobody — disabled"}).encode())):
            with self.subTest(answer=answer[0]):
                with self.assertRaises(FGS.RelayRefused) as caught:
                    self.call(answer)
                self.assertTrue(FGS.refused_by_policy(caught.exception))
        with self.assertRaises(FGS.RelayRefused) as caught:
            self.call((400, b'{"error":"restricted: only an owner or admin can change members"}'))
        self.assertFalse(FGS.refused_by_policy(caught.exception))

    def test_an_unknown_outcome_is_not_a_refusal(self):
        """L1-FGS-807: 连不上、5xx、应答不是 JSON：GroupSyncError（不是 RelayRefused），错误里不带应答内容。"""
        for answer in (OSError("down"), (500, b"boom-secret"), (200, b"<html>boom-secret")):
            with self.subTest(answer=repr(answer)[:20]):
                with self.assertRaises(FGS.GroupSyncError) as caught:
                    self.call(answer)
                self.assertNotIsInstance(caught.exception, FGS.RelayRefused)
                self.assertNotIn("boom-secret", str(caught.exception))


# ================================ L2-1 : 基线 ================================


class Baseline(TwoWay):
    def test_the_first_round_only_records(self):
        """L2-1-FGS-820: 第一次开启（state 里没有快照）的那一轮完全按现状单向对账，只记基线：群里原有的绑定过的人（OUTSIDER 不在频道里）
        和别人的 bot 都不会被加进频道；第二轮什么也没变，也不写 relay。"""
        w = self.world()
        w.users |= {OUTSIDER_OPEN}
        w.bots[TEAM_BOT_APP] = "ou_teambot000000000000000000000001"
        env = self.env(remove_extras=False)
        env.round(w)
        self.assertEqual(w.relay_write_attempts, [])
        self.assertGreater(env.state()["members_synced"], 0)
        env.round(w)
        self.assertEqual(w.relay_write_attempts, [])
        self.assertNotIn(OUTSIDER_PK, {m["pubkey"] for m in w.members})

    def test_our_own_changes_to_the_group_come_back_as_nothing(self):
        """L2-1-FGS-821: 本轮按 Buzz 往群里加的人（ALICE、BOB）和 agent 的 bot，下一轮在飞书里「新出现」：他们本来就在频道里，什么也不写。"""
        w = self.world()
        env = self.env()
        env.round(w)
        self.assertIn(user_key(ALICE_OPEN), env.state()["buzz_seen"].values())
        env.round(w)
        env.round(w)
        self.assertEqual(w.relay_write_attempts, [])
        self.assertEqual(self.notices(w), [])


# ================================ L2-1 : 飞书 → Buzz 加 ================================


class FeishuAdds(TwoWay):
    def test_an_agent_pulled_into_the_group_joins_the_channel_as_a_bot(self):
        """L2-1-FGS-822: 飞书里有人把本机配置的 agent 的 bot 拉进群（它不在频道里）：signer key 签 kind 9000（h=本频道、p=它、role=bot），
        它成为频道的 bot 成员；镜像在频道里发一行说明；remove_extras=true 也不会在同一轮把它的 bot 移出群。"""
        w = self.world()
        w.members = [m for m in w.members if m["pubkey"] != AGENT_PK]
        env = self.settled(w)
        w.bots[AGENT_APP] = AGENT_BOT_MEMBER
        report = env.round(w)
        self.assertEqual(w.member_writes(), [(9000, AGENT_PK, "bot")])
        (attempt,) = w.relay_write_attempts
        self.assertEqual(attempt["signer"], OWNER_PK)
        self.assertIn(["h", CHANNEL], attempt["event"]["tags"])
        self.assertIn({"pubkey": AGENT_PK, "role": "bot"}, w.members)
        self.assertEqual(report["members_to_buzz"], 1)
        self.assertIn(AGENT_APP, w.bots)
        (notice,) = self.notices(w)
        self.assertIn("helper-agent", notice["content"])

    def test_a_locally_configured_agent_introduces_itself_once_after_joining(self):
        """L2-1-FGS-869: Agent 从 Buzz 新进群后，本机有它独立的飞书 profile 时由自己的 bot 发一次介绍；介绍取公开 about，
        用用户语言说明群里任何成员都能 @，后续轮次和离群重入都不重复。"""
        (self.tmp / "agent2-cfg").mkdir(mode=0o700)
        (self.tmp / "agent2-data").mkdir(mode=0o700)
        agents = {AGENT2_PK: {"app_id": AGENT2_APP, "lark_config_dir": str(self.tmp / "agent2-cfg"),
                              "lark_data_dir": str(self.tmp / "agent2-data")}}
        w = self.world()
        w.agent_dirs[str(self.tmp / "agent2-cfg")] = AGENT2_APP
        w.profiles[AGENT2_APP] = "ou_agent2owner0000000000000000001"
        w.bot_members[AGENT2_APP] = AGENT2_BOT_MEMBER
        w.display[AGENT2_PK] = "skill-dev"
        w.members = [m for m in w.members if m["pubkey"] != AGENT2_PK]
        about = "维护公开 Skill；@我时请给原始命令、报错和验收标准。"
        w.relay_events = [public_profile(AGENT2_PK, FOREIGN_OWNER_PK, name="skill-dev", about=about),
                          policy(FOREIGN_OWNER_PK, AGENT2_PK, AGENT2_APP)]
        env = self.settled(w, self.env(agents=agents))
        w.members.append({"pubkey": AGENT2_PK, "role": "bot"})

        report = env.round(w)
        intros = [c for c in w.lark_sends() if about in text_of(c)]

        self.assertEqual(report["agent_intros_sent"], 1)
        self.assertEqual([(c["app"], c["as"]) for c in intros], [(AGENT2_APP, "bot")])
        self.assertIn("群里的任何成员", text_of(intros[0]))
        env.round(w)
        w.members = [m for m in w.members if m["pubkey"] != AGENT2_PK]
        env.round(w)
        w.members.append({"pubkey": AGENT2_PK, "role": "bot"})
        env.round(w)
        self.assertEqual(len([c for c in w.lark_sends() if about in text_of(c)]), 1)

    def test_a_directory_agent_gets_an_honest_proxy_intro_with_its_public_policy(self):
        """L2-1-FGS-870: 远端目录 Agent 没有本机飞书凭据，群助手明确按其签名公开资料代发，不冒充；allowlist 用用户语言说明
        只回应获授权成员，以及未获授权者的申请办法，30177 里的 instruction 不泄露。"""
        w = self.world()
        w.members = [m for m in w.members if m["pubkey"] != AGENT2_PK]
        w.bot_members[AGENT2_APP] = AGENT2_BOT_MEMBER
        w.display[AGENT2_PK] = "review-agent"
        about = "负责合并前代码审查。"
        w.relay_events = [
            public_profile(AGENT2_PK, FOREIGN_OWNER_PK, name="review-agent", about=about),
            policy(FOREIGN_OWNER_PK, AGENT2_PK, AGENT2_APP,
                   extra={"respond_to": "allowlist", "instruction": "PRIVATE-INSTRUCTION"}),
        ]
        env = self.settled(w)
        w.members.append({"pubkey": AGENT2_PK, "role": "bot"})

        report = env.round(w)
        (intro,) = [c for c in w.lark_sends() if about in text_of(c)]
        text = text_of(intro)

        self.assertEqual((report["agent_intros_sent"], report["agent_intros_relayed"]), (1, 1))
        self.assertEqual(intro["app"], DESK_APP)
        self.assertIn("群助手根据 Agent 的公开资料代发", text)
        self.assertIn("只回应已授权成员", text)
        self.assertIn("联系 Agent 所有者申请授权", text)
        self.assertNotIn("PRIVATE-INSTRUCTION", text)

    def test_a_failed_intro_is_visible_and_retried_with_the_same_idempotency_key(self):
        """L2-1-FGS-871: 自我介绍被飞书明确拒绝时报告失败、成员状态给出原因和自动重试办法；下一轮用同一个幂等键成功，
        不因介绍失败回滚已经完成的成员同步。"""
        w = self.world()
        w.members = [m for m in w.members if m["pubkey"] != AGENT2_PK]
        w.bot_members[AGENT2_APP] = AGENT2_BOT_MEMBER
        about = "负责合并前代码审查。"
        w.relay_events = [public_profile(AGENT2_PK, FOREIGN_OWNER_PK, name="review-agent", about=about),
                          policy(FOREIGN_OWNER_PK, AGENT2_PK, AGENT2_APP)]
        env = self.settled(w)
        w.members.append({"pubkey": AGENT2_PK, "role": "bot"})
        w.lark_send_fail = ["rate_limited"]

        report = env.round(w)
        first = [c for c in w.lark_sends() if any("agent-intro-" in arg for arg in c["args"])][0]

        self.assertEqual(report["agent_intro_failures"], 1)
        self.assertTrue(FGS.needs_attention(report))
        self.assertIn(AGENT2_APP, w.bots)
        status = [e for e in w.events if ["feishu-sync-status", "membership"] in e["tags"]][-1]
        self.assertIn("Agent 自我介绍发送失败", status["content"])
        self.assertIn("下一轮自动重试", status["content"])

        recovered = env.round(w, now=NOW + base.timedelta(minutes=1))
        intro_calls = [c for c in w.lark_sends() if about in text_of(c)]
        second = intro_calls[-1]
        self.assertEqual(first["args"][first["args"].index("--idempotency-key") + 1],
                         second["args"][second["args"].index("--idempotency-key") + 1])
        self.assertEqual(recovered["agent_intros_sent"], 1)

    def test_someone_elses_agent_is_found_through_the_published_app_ids(self):
        """L2-1-FGS-823: 拉进群的是别人的 agent（本机没配置、也不在频道里）：全量读一次 kind:30177 建 app_id → pubkey 的反查表，找到它就加进频道。"""
        w = self.world()
        w.members = [m for m in w.members if m["pubkey"] != AGENT2_PK]
        w.bot_members[AGENT2_APP] = AGENT2_BOT_MEMBER
        w.relay_events = published()
        env = self.settled(w)
        w.bots[AGENT2_APP] = AGENT2_BOT_MEMBER
        env.round(w)
        self.assertEqual(w.member_writes(), [(9000, AGENT2_PK, "bot")])
        self.assertTrue(any(f.get("kinds") == [30177] and "#d" not in f and "authors" not in f
                            for q in w.relay_queries for f in q["filters"]))

    def test_a_bot_that_is_no_agent_is_left_alone_and_looked_up_once(self):
        """L2-1-FGS-824: 拉进群的 bot 不是任何已知 agent（反查表里没有）：不写 relay、不提示；之后的轮次不再为它全量读 30177。"""
        w = self.world()
        env = self.settled(w)
        w.bots[TEAM_BOT_APP] = "ou_teambot000000000000000000000001"
        full_scans = lambda: [q for q in w.relay_queries if q["filters"] == [{"kinds": [30177]}]]  # noqa: E731
        env.round(w)
        self.assertEqual(len(full_scans()), 1)
        env.round(w)
        env.round(w)
        self.assertEqual(w.relay_write_attempts, [])
        self.assertEqual(len(full_scans()), 1)

    def test_a_person_this_channel_has_seen_is_added_back(self):
        """L2-1-FGS-825: 频道管理员在 Buzz 里移出 BOB → 下一轮他被移出群（remove_extras=false 也会）；之后有人在飞书里把 BOB 拉回群：本 state
        记得他的飞书身份对应哪个 pubkey，签 9000（role=member）把他加回频道。"""
        w = self.world()
        env = self.settled(w, self.env(remove_extras=False))
        w.members = [m for m in w.members if m["pubkey"] != BOB_PK]
        env.round(w)
        self.assertNotIn(BOB_OPEN, w.users)
        w.users.add(BOB_OPEN)
        env.round(w)
        self.assertEqual(w.member_writes(), [(9000, BOB_PK, "member")])
        self.assertIn({"pubkey": BOB_PK, "role": "member"}, w.members)

    def test_a_person_nobody_can_place_stays_and_is_told_once(self):
        """L2-1-FGS-826: 拉进群的人认不出（没绑定，或本机没见过）：不写 relay，他留在群里；owner 的 bot 在群里提示一次去绑定（链接是 bridge 的
        /bind/），报告 members_unresolved；之后的轮次不再提示。"""
        w = self.world()
        env = self.settled(w)
        w.users.add(EXTRA_OPEN)
        report = env.round(w)
        env.round(w)
        self.assertEqual(w.relay_write_attempts, [])
        self.assertIn(EXTRA_OPEN, w.users)
        self.assertEqual(report["members_unresolved"], 1)
        self.assertEqual(len(self.feishu_notes(w, f"{API_ORIGIN}/bind/")), 1)

    def test_the_shared_cache_places_people_from_other_channels(self):
        """L2-1-FGS-827: 配了 people_cache_file：每轮把本频道认得的人写进这份全机共享的缓存（0600）；另一个频道同步写进去的人（OUTSIDER）在这里
        被拉进群时也认得出，签 9000 加进频道。"""
        cache = self.tmp / "people-cache.json"
        write_owner_only(cache, json.dumps({"version": 1, "people": {
            user_key(OUTSIDER_OPEN): {"pubkey": OUTSIDER_PK, "seen": int(NOW.timestamp())}}}))
        w = self.world()
        env = self.settled(w, self.env(people_cache_file=str(cache)))
        stored = json.loads(cache.read_text())["people"]
        self.assertEqual(stored[user_key(ALICE_OPEN)]["pubkey"], ALICE_PK)
        self.assertEqual(os.stat(cache).st_mode & 0o777, 0o600)
        w.users.add(OUTSIDER_OPEN)
        env.round(w)
        self.assertEqual(w.member_writes(), [(9000, OUTSIDER_PK, "member")])

    def test_an_agent_that_refuses_is_told_once_and_not_retried(self):
        """L2-1-FGS-828: agent 的 channel_add_policy 是 owner_only（签名者不是它的 owner）：relay 拒绝；群里提示一次、报告 members_refused，
        之后的轮次不再重试；它的 bot 离开群、再被拉进来一次才再试。"""
        w = self.world()
        w.members = [m for m in w.members if m["pubkey"] != AGENT2_PK]
        w.bot_members[AGENT2_APP] = AGENT2_BOT_MEMBER
        w.relay_events = published()
        w.add_policy[AGENT2_PK] = "owner_only"
        w.agent_owners[AGENT2_PK] = FOREIGN_OWNER_PK
        env = self.settled(w)
        w.bots[AGENT2_APP] = AGENT2_BOT_MEMBER
        report = env.round(w)
        env.round(w)
        self.assertEqual(len(w.relay_write_attempts), 1)
        self.assertEqual(report["members_refused"], 1)
        self.assertEqual(len(self.feishu_notes(w, "owner")), 1)
        w.bots.pop(AGENT2_APP)
        env.round(w)
        w.bots[AGENT2_APP] = AGENT2_BOT_MEMBER
        env.round(w)
        self.assertEqual(len(w.relay_write_attempts), 2)

    def test_a_lost_answer_does_not_add_twice(self):
        """L2-1-FGS-829b: 签 9000 时 relay 收下了、应答丢了：这一轮记失败；下一轮它已是频道成员，不再发 9000（只有一条）。"""
        w = self.world()
        w.members = [m for m in w.members if m["pubkey"] != AGENT_PK]
        env = self.settled(w)
        w.bots[AGENT_APP] = AGENT_BOT_MEMBER
        w.relay_write_fail = ["lost"]
        env.round(w)
        self.assertTrue(env.state()["member_events"])  # accepted but the answer was lost: exact event remains pending
        env.round(w)
        self.assertEqual(len([e for e in w.relay_writes if e["kind"] == 9000]), 1)
        self.assertEqual(env.state()["member_events"], {})

    def test_an_acknowledged_add_stays_in_the_ledger_until_the_snapshot_is_saved(self):
        """L2-1-FGS-866: relay 接受 9000 后、调用者更新成员快照前进程崩溃，state 仍保留原事件；下一轮从
        Buzz 成员列表看到效果后清账，不会因读面延迟或缺少快照而生成带新 nonce 的第二条。"""
        w = self.world()
        w.members = [m for m in w.members if m["pubkey"] != AGENT_PK]
        env = self.settled(w)
        w.bots[AGENT_APP] = AGENT_BOT_MEMBER
        original = FGS.Round._member_event

        def crash_after_ack(round_, kind, pubkey, source_key, role=None):
            original(round_, kind, pubkey, source_key, role)
            raise RuntimeError("crash after relay ack")

        with mock.patch.object(FGS.Round, "_member_event", crash_after_ack):
            with self.assertRaisesRegex(RuntimeError, "after relay ack"):
                env.round(w, now=NOW)
        self.assertTrue(env.state()["member_events"])

        env.round(w, now=NOW + base.timedelta(minutes=1))

        self.assertEqual(len([e for e in w.relay_writes if e["kind"] == 9000]), 1)
        self.assertEqual(env.state()["member_events"], {})

    def test_a_failed_lookup_is_not_repeated_in_the_same_round(self):
        """L2-1-FGS-853: 同一轮有两个陌生 bot 进群、反查第一次就失败：这一轮不再为第二个再全量查一次 30177。"""
        w = self.world()
        env = self.settled(w)
        w.bots[TEAM_BOT_APP] = "ou_teambot000000000000000000000001"
        w.bots["cli_team000000000002"] = "ou_teambot000000000000000000000002"
        w.relay_fail = ["network", "network", "network"]
        before = len(w.relay_fail)
        env.round(w)
        self.assertEqual(before - len(w.relay_fail), 1)

    def test_a_failed_write_is_retried(self):
        """L2-1-FGS-829: relay 连不上：计入 errors 与 member_failures（需要关注），下一轮重试成功。"""
        w = self.world()
        w.members = [m for m in w.members if m["pubkey"] != AGENT_PK]
        env = self.settled(w)
        w.bots[AGENT_APP] = AGENT_BOT_MEMBER
        w.relay_write_fail = ["network"]
        report = env.round(w)
        self.assertGreaterEqual(report["member_failures"], 1)
        self.assertTrue(FGS.needs_attention(report))
        env.round(w)
        self.assertEqual(w.member_writes(), [(9000, AGENT_PK, "bot")])

    def test_an_unknown_add_retries_the_exact_same_event(self):
        """L2-1-FGS-857: 9000 的 HTTP 结果未知、下一轮成员列表仍未显示成功时，重试必须复用首次
        created_at，因而 event id 完全相同；不能在结果未知时再造一条可能重复执行的成员事件。"""
        w = self.world()
        w.members = [m for m in w.members if m["pubkey"] != AGENT_PK]
        env = self.settled(w)
        w.bots[AGENT_APP] = AGENT_BOT_MEMBER
        w.relay_write_fail = ["network"]

        env.round(w, now=NOW + base.timedelta(minutes=1))
        (stored,) = env.state()["member_events"].values()
        self.assertEqual(stored, [event for event in w.relay_write_requests if event["kind"] == 9000][0])
        env.round(w, now=NOW + base.timedelta(minutes=2))

        attempts = [event for event in w.relay_write_requests if event["kind"] == 9000]
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0]["id"], attempts[1]["id"])
        self.assertEqual(attempts[0], attempts[1])  # exact body too: the persisted signature is reused
        self.assertEqual(env.state()["member_events"], {})

    def test_a_refused_retry_stays_paused_until_membership_or_source_intent_settles_it(self):
        """L2-1-FGS-867: 首次 9000 结果未知、原事件的重试又被 Relay 明确拒绝后，把 retry_refused 与原事件一起
        持久化；后续轮次不再 POST。只有 Buzz 成员态证明已生效，或飞书侧撤回加人意图，才清掉暂停项。"""
        w = self.world()
        w.members = [m for m in w.members if m["pubkey"] != AGENT_PK]
        env = self.settled(w)
        w.bots[AGENT_APP] = AGENT_BOT_MEMBER

        w.relay_write_fail = ["network"]
        env.round(w, now=NOW)
        w.relay_write_fail = ["reject"]
        report = env.round(w, now=NOW + base.timedelta(minutes=1))
        state = env.state()
        (operation,) = state["member_events"]
        self.assertEqual(state["member_event_blocks"], {operation: "retry_refused"})
        self.assertEqual(report["member_events_blocked"], {"retry_refused": 1})

        report = env.round(w, now=NOW + base.timedelta(minutes=2))

        attempts = [event for event in w.relay_write_requests if event["kind"] == 9000]
        self.assertEqual(len(attempts), 2)
        self.assertEqual(report["member_events_blocked"], {"retry_refused": 1})
        status = [e for e in w.events if ["feishu-sync-status", "membership"] in e["tags"]][-1]
        self.assertIn("已暂停", status["content"])
        self.assertIn("人工核对 Buzz 频道成员", status["content"])

        w.bots.pop(AGENT_APP)
        env.round(w, now=NOW + base.timedelta(minutes=3))
        self.assertEqual(env.state()["member_events"], {})
        self.assertEqual(env.state()["member_event_blocks"], {})
        self.assertEqual(len([event for event in w.relay_write_requests if event["kind"] == 9000]), 2)

    def test_a_pending_add_is_closed_when_the_feishu_intent_is_withdrawn(self):
        """L2-1-FGS-863: 9000 结果未知后 bot 又离开飞书群；完整成员列表证明加人意图已撤回，清掉原事件且不重发。"""
        w = self.world()
        w.members = [m for m in w.members if m["pubkey"] != AGENT_PK]
        env = self.settled(w)
        w.bots[AGENT_APP] = AGENT_BOT_MEMBER
        w.relay_write_fail = ["network"]
        env.round(w, now=NOW)
        w.bots.pop(AGENT_APP)

        env.round(w, now=NOW + base.timedelta(minutes=1))

        self.assertEqual(len([e for e in w.relay_write_requests if e["kind"] == 9000]), 1)
        self.assertEqual(env.state()["member_events"], {})

    def test_separate_adds_in_the_same_second_have_distinct_event_ids(self):
        """L2-1-FGS-859: add → remove → add 即使三轮时钟落在同一秒，也是三个逻辑操作；第二个 9000 带新的
        持久化 nonce，不能被 relay 当成第一个 9000 的 duplicate 而漏掉重新加入。"""
        w = self.world()
        w.members = [m for m in w.members if m["pubkey"] != AGENT_PK]
        env = self.settled(w)

        with mock.patch.object(FGS, "_new_member_event_nonce", side_effect=["01" * 32, "02" * 32, "03" * 32]):
            w.bots[AGENT_APP] = AGENT_BOT_MEMBER
            env.round(w, now=NOW)
            w.bots.pop(AGENT_APP)
            env.round(w, now=NOW)
            w.bots[AGENT_APP] = AGENT_BOT_MEMBER
            env.round(w, now=NOW)

        adds = [event for event in w.relay_write_requests if event["kind"] == 9000]
        member_events = [event for event in w.relay_write_requests if event["kind"] in (9000, 9001)]
        self.assertEqual(len(adds), 2)
        self.assertNotEqual(adds[0]["id"], adds[1]["id"])
        self.assertEqual(len({FGS.member_event_stream(event) for event in member_events}), 1)
        self.assertEqual([FGS.member_event_sequence(event) for event in member_events], [1, 2, 3])
        self.assertEqual(env.state()["member_event_seq"], 3)
        self.assertIn({"pubkey": AGENT_PK, "role": "bot"}, w.members)

    def test_a_pending_add_fails_closed_if_the_signer_changes(self):
        """L2-1-FGS-860: signer A 的 9000 结果未知后改成另一个合法 admin signer B：不生成第二个 event id；
        报告与群状态明说为避免重复已暂停，并要求恢复原 signer 或人工核对。"""
        w = self.world()
        w.members = [dict(m, role="admin") if m["pubkey"] == AGENT2_PK else m for m in w.members
                     if m["pubkey"] != AGENT_PK]
        w.key_to_pubkey[AGENT2_KEY] = AGENT2_PK
        env = self.settled(w)
        w.bots[AGENT_APP] = AGENT_BOT_MEMBER
        w.relay_write_fail = ["network"]
        env.round(w, now=NOW)
        write_owner_only(env.signer_env, f"BUZZ_PRIVATE_KEY={AGENT2_KEY}\nBUZZ_RELAY_URL=https://relay.test\n")

        report = env.round(w, now=NOW + base.timedelta(minutes=1))

        attempts = [event for event in w.relay_write_requests if event["kind"] == 9000]
        self.assertEqual(len(attempts), 1)
        self.assertEqual(report["member_events_blocked"], {"signer_changed": 1})
        status = [e for e in w.events if ["feishu-sync-status", "membership"] in e["tags"]][-1]
        self.assertIn("签名身份已变化", status["content"])
        self.assertIn("恢复原签名身份", status["content"])

    def test_an_expired_unknown_add_stays_pending_and_says_why(self):
        """L2-1-FGS-861: 9000 结果未知超过 relay 的 900 秒接收窗口，不能丢账后用新时间造第二条；不再写 relay，
        未决事件保留，状态消息要求人工核对频道成员。"""
        w = self.world()
        w.members = [m for m in w.members if m["pubkey"] != AGENT_PK]
        env = self.settled(w)
        w.bots[AGENT_APP] = AGENT_BOT_MEMBER
        w.relay_write_fail = ["network"]
        env.round(w, now=NOW)

        report = env.round(w, now=NOW + base.timedelta(seconds=FGS.RELAY_CLOCK_SKEW_SECONDS + 1))

        attempts = [event for event in w.relay_write_requests if event["kind"] == 9000]
        self.assertEqual(len(attempts), 1)
        self.assertEqual(report["member_events_blocked"], {"expired": 1})
        self.assertTrue(env.state()["member_events"])
        status = [e for e in w.events if ["feishu-sync-status", "membership"] in e["tags"]][-1]
        self.assertIn("超过 Relay 接收窗口", status["content"])
        self.assertIn("人工核对 Buzz 频道成员", status["content"])

    def test_a_full_pending_ledger_applies_backpressure_and_says_how_to_recover(self):
        """L2-1-FGS-862: 未决成员事件达到硬上限时不淘汰旧项、也不发送未记账的新操作；失败状态说明先恢复
        Relay 并让现有操作收敛。"""
        w = self.world()
        w.members = [m for m in w.members if m["pubkey"] != AGENT_PK]
        env = self.settled(w)
        w.bots[AGENT_APP] = AGENT_BOT_MEMBER

        with mock.patch.object(FGS, "MEMBER_EVENTS_MAX", 0):
            report = env.round(w, now=NOW)

        self.assertEqual([e for e in w.relay_write_requests if e["kind"] == 9000], [])
        self.assertEqual(report["member_events_blocked"], {"ledger_full": 1})
        status = [e for e in w.events if ["feishu-sync-status", "membership"] in e["tags"]][-1]
        self.assertIn("未决成员变更达到安全上限", status["content"])
        self.assertIn("恢复 Relay", status["content"])

    def test_an_exhausted_operation_sequence_fails_visibly_without_posting(self):
        """L2-1-FGS-868: 持久序号达到上限时不回绕、不发一个可能排到旧事件前面的操作；状态明说原因并要求维护者迁移。"""
        w = self.world()
        w.members = [m for m in w.members if m["pubkey"] != AGENT_PK]
        env = self.settled(w)
        w.bots[AGENT_APP] = AGENT_BOT_MEMBER

        with mock.patch.object(FGS, "MEMBER_EVENT_SEQUENCE_MAX", 0):
            report = env.round(w, now=NOW)

        self.assertEqual([e for e in w.relay_write_requests if e["kind"] == 9000], [])
        self.assertEqual(report["member_events_blocked"], {"sequence_exhausted": 1})
        status = [e for e in w.events if ["feishu-sync-status", "membership"] in e["tags"]][-1]
        self.assertIn("成员变更序号已耗尽", status["content"])
        self.assertIn("联系维护者", status["content"])


class AgentLookupAtScale(TwoWay):
    """L4（2026-09-23，生产 relay）发现：kind:30177 有 415 条，有的 agent 的 kind 0 内嵌了约 190 KB 的头像，200 个作者一批的 kind 0 应答有
    1.44 MB，超过 transport 的 1 MiB 上限，整次反查失败——而失败后这个 bot 被当成「谁的 agent 都不是」记下，再也不查。"""

    def test_only_the_claimants_profiles_are_read(self):
        """L1-FGS-850: 反查某个 app_id 时，先读全部 30177，再只读**声称了这个 app_id 的 agent** 的 kind 0（通常就一个），不读几百个 agent 的。"""
        events = [*published()]
        for n in range(30):  # 别的 agent：有 30177（其中一半也公开了 app_id），不该被读 profile
            other = signed_identity(f"{n + 1:064x}")
            events += [profile(other, FOREIGN_OWNER_PK), policy(FOREIGN_OWNER_PK, other, f"cli_other{n:011d}" if n % 2 else None)]
        seen = []

        def http(url, headers, timeout, body=None):
            filters = json.loads(body)
            seen.append(filters)
            wanted = [e for e in events if any(
                e["kind"] in f.get("kinds", [e["kind"]]) and ("authors" not in f or e["pubkey"] in f["authors"])
                and ("#d" not in f or any(t[0] == "d" and t[1] in f["#d"] for t in e["tags"])) for f in filters)]
            return 200, json.dumps(wanted).encode()
        cfg = FGS.load_config(self.env().config)
        index = FGS.fetch_agent_index(cfg, "https://relay.test", {AGENT_APP, OWNER_APP}, http, NOW, wanted=[AGENT2_APP])
        self.assertEqual(index, {AGENT2_APP: AGENT2_PK})
        profile_queries = [f for q in seen for f in q if f.get("kinds") == [0]]
        self.assertEqual([f["authors"] for f in profile_queries], [[AGENT2_PK]])

    def test_many_foreign_agents_read_profiles_in_small_batches(self):
        """L1-FGS-851: 目录查询（ADR-0019）：频道里外来 agent 多于 5 个时，kind 0 分批读，每批至多 5 个作者（一批 5 个带大头像的 profile 也在
        1 MiB 以内）；不多于 5 个时仍是一次查询。"""
        agents = [f"{n:02x}" * 32 for n in range(0x60, 0x6c)]
        seen = []

        def http(url, headers, timeout, body=None):
            seen.append(json.loads(body))
            return 200, b"[]"
        cfg = FGS.load_config(self.env().config)
        FGS.fetch_agent_directory(cfg, "https://relay.test", set(agents), {OWNER_APP}, http, NOW)
        authors = [f["authors"] for q in seen for f in q if f.get("kinds") == [0]]
        self.assertEqual(sorted(a for batch in authors for a in batch), sorted(agents))
        self.assertTrue(all(len(batch) <= 5 for batch in authors))
        seen.clear()
        FGS.fetch_agent_directory(cfg, "https://relay.test", set(agents[:3]), {OWNER_APP}, http, NOW)
        self.assertEqual(seen, [FGS.directory_filters(set(agents[:3]))])

    def test_a_failed_lookup_is_tried_again_next_round(self):
        """L2-1-FGS-852: 反查失败的那一轮，新进群的 bot 不被记成「谁的 agent 都不是」；下一轮反查成功就把它加进频道。"""
        w = self.world()
        w.members = [m for m in w.members if m["pubkey"] != AGENT2_PK]
        w.bot_members[AGENT2_APP] = AGENT2_BOT_MEMBER
        w.relay_events = published()
        env = self.settled(w)
        w.bots[AGENT2_APP] = AGENT2_BOT_MEMBER
        w.relay_fail = ["network"]
        report = env.round(w)
        self.assertEqual(report["directory_failed"], 1)
        self.assertEqual(w.member_writes(), [])
        env.round(w)
        self.assertEqual(w.member_writes(), [(9000, AGENT2_PK, "bot")])


# ================================ L2-1 : 飞书 → Buzz 移出 ================================


class FeishuRemovals(TwoWay):
    def test_an_agent_removed_from_the_group_leaves_the_channel(self):
        """L2-1-FGS-830: 飞书里把 agent 的 bot 移出群：签 9001 把它移出频道；这一轮不会再把它的 bot 加回群。"""
        w = self.world()
        env = self.settled(w)
        w.bots.pop(AGENT_APP)
        report = env.round(w)
        self.assertEqual(w.member_writes(), [(9001, AGENT_PK, None)])
        self.assertNotIn(AGENT_PK, {m["pubkey"] for m in w.members})
        self.assertNotIn(AGENT_APP, w.bots)
        self.assertEqual(report["members_removed_from_buzz"], 1)
        (notice,) = self.notices(w)
        self.assertIn("移出", notice["content"])

    def test_a_person_removed_from_the_group_leaves_the_channel_admins_too(self):
        """L2-1-FGS-831: 飞书里把人移出群：签 9001 移出频道，频道 admin 也一样（PO 决定）；这一轮不会把他加回群。"""
        w = self.world()
        w.members = [dict(m, role="admin") if m["pubkey"] == ALICE_PK else m for m in w.members]
        env = self.settled(w)
        w.users.discard(ALICE_OPEN)
        env.round(w)
        self.assertEqual(w.member_writes(), [(9001, ALICE_PK, None)])
        self.assertNotIn(ALICE_OPEN, w.users)

    def test_an_unknown_removal_retries_the_exact_same_event(self):
        """L2-1-FGS-858: 9001 的 HTTP 结果未知、下一轮频道成员列表仍未显示移出时，重试复用同一条
        NIP-01 事件；成功后清掉持久化的未决操作。"""
        w = self.world()
        env = self.settled(w)
        w.bots.pop(AGENT_APP)
        w.relay_write_fail = ["network"]

        env.round(w, now=NOW + base.timedelta(minutes=1))
        (stored,) = env.state()["member_events"].values()
        self.assertEqual(stored, [event for event in w.relay_write_requests if event["kind"] == 9001][0])
        env.round(w, now=NOW + base.timedelta(minutes=2))

        attempts = [event for event in w.relay_write_requests if event["kind"] == 9001]
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0]["id"], attempts[1]["id"])
        self.assertEqual(attempts[0], attempts[1])
        self.assertEqual(env.state()["member_events"], {})

    def test_a_lost_remove_answer_is_settled_without_a_duplicate(self):
        """L2-1-FGS-864: 9001 被接受但应答丢失时，下一轮从成员列表看到已移出就清账，不发第二个 9001。"""
        w = self.world()
        env = self.settled(w)
        w.bots.pop(AGENT_APP)
        w.relay_write_fail = ["lost"]
        env.round(w, now=NOW)
        self.assertTrue(env.state()["member_events"])

        env.round(w, now=NOW + base.timedelta(minutes=1))

        self.assertEqual(len([a for a in w.relay_write_attempts if a["event"]["kind"] == 9001]), 1)
        self.assertEqual(env.state()["member_events"], {})

    def test_a_pending_remove_is_closed_when_the_feishu_intent_is_withdrawn(self):
        """L2-1-FGS-865: 9001 结果未知后 bot 回到飞书群；完整成员列表证明移出意图已撤回，清账且不重发。"""
        w = self.world()
        env = self.settled(w)
        w.bots.pop(AGENT_APP)
        w.relay_write_fail = ["network"]
        env.round(w, now=NOW)
        w.bots[AGENT_APP] = AGENT_BOT_MEMBER

        env.round(w, now=NOW + base.timedelta(minutes=1))

        self.assertEqual(len([e for e in w.relay_write_requests if e["kind"] == 9001]), 1)
        self.assertEqual(env.state()["member_events"], {})

    def test_the_channel_owner_and_the_signer_stay(self):
        """L2-1-FGS-832: 飞书里把频道 owner（也是同步的签名身份）移出群：频道里不移出，群里提示一次，报告 members_protected；他照旧被加回群。"""
        w = self.world()
        env = self.settled(w)
        w.users.discard(OWNER_OPEN)
        report = env.round(w)
        env.round(w)
        self.assertEqual(w.relay_write_attempts, [])
        self.assertEqual(report["members_protected"], 1)
        self.assertEqual(len(self.feishu_notes(w, "不会")), 1)
        self.assertIn(OWNER_OPEN, w.users)

    def test_an_incomplete_member_list_removes_nobody(self):
        """L2-1-FGS-833: 飞书成员列表没读全（has_more）：不判定任何人被移出；读全了的下一轮才移出。"""
        w = self.world()
        env = self.settled(w)
        w.users.discard(ALICE_OPEN)
        w.members_incomplete = True
        env.round(w)
        self.assertEqual(w.member_writes(), [])
        w.members_incomplete = False
        env.round(w)
        self.assertEqual(w.member_writes(), [(9001, ALICE_PK, None)])

    def test_malformed_completeness_metadata_removes_nobody_and_names_the_reason(self):
        """L2-1-FGS-833b: 只有 has_more 严格 false、truncations 是空列表、对应 *_total 是非 bool 整数且等于
        成员列表长度才能判定“飞书里删了人”。元数据缺失、错类型或成员行不是列表时都不写 9001，报告原因。"""
        faults = ("missing_has_more", "missing_truncations", "truncations_object", "missing_total", "bool_total", "rows_object")
        for fault in faults:
            with self.subTest(fault=fault):
                sub = self.tmp / fault
                sub.mkdir()
                saved, self.tmp = self.tmp, sub
                try:
                    w = self.world()
                    env = self.settled(w)
                    w.users.discard(ALICE_OPEN)
                    w.member_listing_fault = fault
                    if fault == "rows_object":
                        with self.assertRaisesRegex(FGS.GroupSyncError, "Desk bot"):
                            env.round(w)
                        continue
                    report = env.round(w)
                finally:
                    self.tmp = saved
                self.assertEqual(w.member_writes(), [])
                self.assertIn({"pubkey": ALICE_PK, "role": "member"}, w.members)
                self.assertEqual(report["removals_withheld"], "member_list_incomplete")

    def test_many_removals_at_once_wait_for_a_person(self):
        """L2-1-FGS-834: 一轮要移出的人数（两个方向合计）超过 BULK_REMOVAL_LIMIT：全部暂停，报告 removals_withheld=bulk_removal；
        带 --allow-bulk-removal 的那一轮才执行。"""
        w = self.world()
        env = self.settled(w)
        w.users -= {ALICE_OPEN, BOB_OPEN}
        with mock.patch.object(FGS, "BULK_REMOVAL_LIMIT", 1):
            report = env.round(w)
            self.assertEqual(report["removals_withheld"], "bulk_removal")
            self.assertEqual(w.member_writes(), [])
            env.round(w, allow_bulk_removal=True)
        self.assertEqual(sorted(w.member_writes()), sorted([(9001, ALICE_PK, None), (9001, BOB_PK, None)]))


# ================================ L2-1 : Buzz → 飞书移出、冲突 ================================


class BuzzRemovalsAndConflicts(TwoWay):
    def test_a_buzz_removal_reaches_the_group_even_without_remove_extras(self):
        """L2-1-FGS-835: Buzz 里移出 ALICE：下一轮按快照里她的飞书身份把她移出群（remove_extras=false 也会），不写 relay。"""
        w = self.world()
        env = self.settled(w, self.env(remove_extras=False))
        w.members = [m for m in w.members if m["pubkey"] != ALICE_PK]
        env.round(w)
        self.assertNotIn(ALICE_OPEN, w.users)
        self.assertEqual(w.relay_write_attempts, [])

    def test_a_directory_agent_that_leaves_the_channel_leaves_the_group(self):
        """L2-1-FGS-836: ADR-0019 的目录 agent（别人的）离开频道：它的 bot 也被移出群（ADR-0019 留下的已知限制由这里补上）。"""
        w = self.world()
        w.bot_members[AGENT2_APP] = AGENT2_BOT_MEMBER
        w.relay_events = published()
        env = self.settled(w, self.env(remove_extras=False))
        self.assertIn(AGENT2_APP, w.bots)
        w.members = [m for m in w.members if m["pubkey"] != AGENT2_PK]
        env.round(w)
        self.assertNotIn(AGENT2_APP, w.bots)

    def test_buzz_wins_when_both_sides_change_the_same_member(self):
        """L2-1-FGS-837: 冲突以 Buzz 为准。① ALICE 在 Buzz 里、却没进群（飞书拒绝加她）；这一轮她在 Buzz 被移出、同时有人在飞书把她拉进群：
        不加回频道，她反被移出群。② BOB 只在群里（不在频道），这一轮他在 Buzz 被加进频道、同时在飞书被移出群：频道里保留他，并把他加回群。"""
        w = self.world()
        w.reject_ids = {union_of(ALICE_OPEN), ALICE_OPEN}
        w.members = [m for m in w.members if m["pubkey"] != BOB_PK]
        w.users.add(BOB_OPEN)
        env = self.settled(w, self.env(remove_extras=False))
        self.assertNotIn(ALICE_OPEN, w.users)
        w.reject_ids = set()
        w.members = [m for m in w.members if m["pubkey"] != ALICE_PK] + [{"pubkey": BOB_PK, "role": "member"}]
        w.users.add(ALICE_OPEN)
        w.users.discard(BOB_OPEN)
        env.round(w)
        self.assertEqual(w.member_writes(), [])
        self.assertNotIn(ALICE_OPEN, w.users)
        self.assertIn(BOB_OPEN, w.users)
        self.assertIn(BOB_PK, {m["pubkey"] for m in w.members})


# ================================ L2-1 : 单向模式与留痕 ================================


class OneWayAndNotices(TwoWay):
    def test_buzz_to_feishu_keeps_the_old_behaviour(self):
        """L2-1-FGS-838: `membership_sync: "buzz_to_feishu"`：飞书里的增减不写 relay（被移出群的 ALICE 照旧被加回群），和以前一样。"""
        w = self.world()
        env = self.settled(w, self.env(membership_sync="buzz_to_feishu"))
        w.users.discard(ALICE_OPEN)
        w.bots[TEAM_BOT_APP] = "ou_teambot000000000000000000000001"
        env.round(w)
        self.assertEqual(w.relay_write_attempts, [])
        self.assertIn(ALICE_OPEN, w.users)

    def test_one_notice_per_round_names_everyone(self):
        """L2-1-FGS-839: 同一轮既有加入也有移出：频道里只有一条说明，写明新加入的和移出的；什么都没变的轮次不发。"""
        w = self.world()
        w.members = [m for m in w.members if m["pubkey"] != AGENT_PK]
        env = self.settled(w)
        w.bots[AGENT_APP] = AGENT_BOT_MEMBER
        w.users.discard(ALICE_OPEN)
        env.round(w)
        (notice,) = self.notices(w)
        self.assertIn("helper-agent", notice["content"])
        self.assertIn("Alice", notice["content"])
        env.round(w)
        self.assertEqual(len(self.notices(w)), 1)


class FailureNotices(TwoWay):
    """成员主链路失败有一条 Buzz 规范状态，同步到飞书后原地更新；Buzz 暂时不可写时先在飞书兜底。"""

    @staticmethod
    def status_events(w, kind):
        return [e for e in w.events if e["kind"] == kind and ["feishu-sync-status", "membership"] in e["tags"]]

    @staticmethod
    def status_sends(w):
        return [c for c in w.lark_sends() if text_of(c).startswith("飞书群成员同步")]

    def test_every_incomplete_round_edits_one_canonical_notice_in_buzz_and_feishu(self):
        """L2-1-FGS-854: 成员列表不完整时，Buzz 有一条安全、可操作的原因说明，本群同轮收到；下一失败轮发 40003
        编辑同一 root，飞书也只原地更新同一条，不刷屏。"""
        w = self.world()
        env = self.settled(w)
        w.members_incomplete = True

        first = env.round(w, now=NOW + base.timedelta(minutes=1))
        (root,) = self.status_events(w, 9)
        (sent,) = self.status_sends(w)
        self.assertEqual(first["removals_withheld"], "member_list_incomplete")
        self.assertIn("成员列表未确认完整", root["content"])
        self.assertIn("下一轮自动重试", root["content"])
        self.assertEqual(text_of(sent), root["content"])

        env.round(w, now=NOW + base.timedelta(minutes=2))

        self.assertEqual(len(self.status_sends(w)), 1)
        (edit,) = self.status_events(w, 40003)
        self.assertIn(["e", root["id"]], edit["tags"])
        self.assertNotEqual(edit["content"], root["content"])
        updates = [c for c in w.message_updates if c["message_id"] == w.sent_keys[
            sent["args"][sent["args"].index("--idempotency-key") + 1]]]
        self.assertEqual(len(updates), 1)

    def test_an_agent_directory_failure_says_why_the_bot_was_not_pulled(self):
        """L2-1-FGS-855: 读不到远端 agent 目录时，状态明说“本轮未拉取远端 agent bot”与自动重试，不暴露 relay 应答。"""
        w = self.world()
        env = self.settled(w)
        w.relay_fail = ["network"]

        report = env.round(w, now=NOW + base.timedelta(minutes=1))

        self.assertEqual(report["directory_failed"], 1)
        (root,) = self.status_events(w, 9)
        self.assertIn("Agent 目录读取失败", root["content"])
        self.assertIn("本轮未拉取远端 agent bot", root["content"])
        self.assertNotIn("connection", root["content"].lower())

    def test_buzz_failure_falls_back_to_feishu_then_backfills_without_a_duplicate(self):
        """L2-1-FGS-856: Buzz 状态事件发不出时，owner bot 直接在飞书提示；下轮 Buzz 恢复后回填规范 root，
        把它绑到已有飞书消息，只原地更新而不发第二条。"""
        w = self.world()
        env = self.settled(w)
        w.members_incomplete = True
        w.relay_write_fail = ["network"]

        env.round(w, now=NOW + base.timedelta(minutes=1))
        self.assertEqual(self.status_events(w, 9), [])
        (fallback,) = self.status_sends(w)
        fallback_id = w.sent_keys[fallback["args"][fallback["args"].index("--idempotency-key") + 1]]

        env.round(w, now=NOW + base.timedelta(minutes=2))

        (root,) = self.status_events(w, 9)
        self.assertEqual(len(self.status_sends(w)), 1)
        self.assertEqual(env.state()["b2f"][root["id"]], fallback_id)
        self.assertTrue(any(c["message_id"] == fallback_id for c in w.message_updates))


# ================================ L2-1 : 别的机器的镜像身份 ================================


OTHER_MIRROR_PK = signed_identity("c8" * 32)  # 另一台机器同步同一频道时的镜像身份：role=bot


def mirror_policy(owner, mirror, created_at=T0 - 900, extra=None):
    content = {"name": "feishu-mirror", "parallelism": 1, "respond_to": "owner-only", "feishu": {"mirror": True}}
    content.update(extra or {})
    return nostr_event(owner, 30177, [["d", mirror]], json.dumps(content), created_at)


class OtherMirrors(TwoWay):
    """同一个频道被两台机器同步时，别人的镜像在本机看来是「本机没配置的 bot 成员」。它在 30177 里声明了 feishu.mirror（由它的 NIP-OA
    owner 签名）就按镜像处理：不是 agent，它转进 Buzz 的飞书消息不再被本机代发一份。判定只要这一条，不要求它的 owner 是频道 admin。"""

    def world(self, declared=True):
        w = super().world()
        w.members.append({"pubkey": OTHER_MIRROR_PK, "role": "bot"})
        w.display[OTHER_MIRROR_PK] = "feishu-mirror-2"
        w.relay_events = [profile(OTHER_MIRROR_PK, FOREIGN_OWNER_PK)]
        if declared:
            w.relay_events.append(mirror_policy(FOREIGN_OWNER_PK, OTHER_MIRROR_PK, extra={"feishu": {"mirror": True, "app_id": AGENT2_APP}}))
        return w

    def test_another_hosts_mirror_is_not_relayed(self):
        """L2-1-FGS-840: 别人镜像转进 Buzz 的消息不代发进本机的群（缺省 relay 下以前会多出一份），也不按非成员上下文镜像；跳过原因 other_mirror。"""
        for overrides in ({}, {"buzz_unmapped_senders": "context"}):
            with self.subTest(overrides=overrides):
                sub = self.tmp / str(len(overrides))
                sub.mkdir()
                self.tmp, saved = sub, self.tmp
                try:
                    w = self.world()
                    w.events = [base.event(base.eid(1), OTHER_MIRROR_PK, "[飞书] 张三：别的群里说的")]
                    report = self.env(**overrides).round(w)
                finally:
                    self.tmp = saved
                self.assertFalse(any("别的群里说的" in text_of(c) for c in w.lark_sends()))
                self.assertEqual(report["skipped"].get("other_mirror"), 1)
                self.assertEqual(report["relayed_agents"], 0)

    def test_it_is_no_agent_to_look_up_mention_or_sync(self):
        """L2-1-FGS-841: 它不进目录（即使 30177 里也写了 app_id）、人在 Buzz 里提到它不渲染成 <at>、它离开频道时群里什么也不动。"""
        w = self.world()
        w.bot_members[AGENT2_APP] = AGENT2_BOT_MEMBER
        w.events = [base.event(base.eid(1), BOB_PK, "@feishu-mirror-2 看下", tags=[("p", OTHER_MIRROR_PK)])]
        env = self.env()
        report = env.round(w)
        self.assertEqual(report["directory_agents"], 0)
        self.assertNotIn(AGENT2_APP, w.bots)
        (send,) = [c for c in w.lark_sends() if "看下" in text_of(c)]
        self.assertNotIn("<at", text_of(send))
        env.round(w)
        w.members = [m for m in w.members if m["pubkey"] != OTHER_MIRROR_PK]
        before = sorted(w.bots)
        env.round(w)
        self.assertEqual(sorted(w.bots), before)
        self.assertEqual(w.relay_write_attempts, [])

    def test_an_undeclared_bot_is_still_an_agent(self):
        """L2-1-FGS-842: 没有声明 feishu.mirror 的 bot 成员照旧是「本机没配置的 agent」：缺省代发（ADR-0019 的行为不变）。"""
        w = self.world(declared=False)
        w.events = [base.event(base.eid(1), OTHER_MIRROR_PK, "照旧代发")]
        report = self.env().round(w)
        self.assertTrue(any("照旧代发" in text_of(c) for c in w.lark_sends()))
        self.assertEqual(report["relayed_agents"], 1)


if __name__ == "__main__":
    unittest.main()
