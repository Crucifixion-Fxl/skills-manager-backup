"""频道↔飞书群绑定的跨机认领（ADR-0022，infra/buzz-deploy#97）。

本地 CLI 路线防重复的手段原来全在本机（state 锁、一个 state 目录一个绑定）。两台机器各自同步同一个频道、或把两个频道绑到同一个群，
谁也发现不了。ADR-0022：每套同步在自己镜像身份的 kind:30177 里写一条租约认领

    "feishu": {"mirror": true, "bindings": [{"channel", "chat_ref", "claimed_at", "heartbeat", "takeover_of"?, "policy"}]}

由镜像的 NIP-OA owner（people_api 的 signer key）签名；`chat_ref` = SHA-256("buzz-feishu-chat:v1:" + chat_id)，chat_id 不出现在公开事件里。
每轮读所有声明了镜像的 30177：

- 有效 = 心跳在 30 分钟租期内；心跳比 10 分钟旧就刷新；
- 同频道的别的认领只认「声明方是本频道 bot 成员、owner 是频道 owner/admin」的；同群（chat_ref 相同、频道不同）的不核对角色；
- claimed_at 早的赢，相同时镜像 pubkey 小的赢；带 takeover_of 指名接管的胜过被指名的一方；
- 输的一方本轮整个绑定不同步（消息、表情、成员），报告 claim_conflict，群里和频道里各提示一次；
- 对方过期：忽略，报告记一次接管；读不到认领：消息和表情照常、成员暂停一轮（claims_unreadable）；写不了：照常同步、下一轮重试；
- `round --take-over` 立即接管；`bind` / `create-chat` 预检遇到别的有效认领拒绝（claimed_by_other_mirror）；`binding_claim: false` 关掉。

沿用 test_buzz_feishu_group_sync.py 的 FakeWorld / Env / 常量。用例编号从 900 起。
"""

import hashlib
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_buzz_feishu_group_sync as base  # noqa: E402
from unittest import mock
from test_buzz_feishu_group_sync import (  # noqa: E402
    AGENT_PK, ALICE_OPEN, ALICE_PK, CHANNEL, CHAT, EXTRA_OPEN, FGS, MIRROR_KEY, MIRROR_PK, NOW, OWNER_APP, OWNER_KEY, OWNER_PK,
    T0, TEAM_BOT_APP, TmpCase, Env, text_of, write_owner_only,
)
from test_buzz_feishu_group_sync_two_way_members import TwoWay  # noqa: E402
from test_buzz_feishu_group_sync_agent_directory import (  # noqa: E402
    FOREIGN_OWNER_PK, MALLORY_PK, nostr_event, profile, signed_identity,
)


def setUpModule():
    base.setUpModule()


def tearDownModule():
    base.tearDownModule()


signed_identity(OWNER_KEY)
signed_identity(MIRROR_KEY)
MIRROR_B_PK = signed_identity("d1" * 32)  # 另一台机器的镜像身份
MIRROR_C_PK = signed_identity("d2" * 32)  # 第三台机器的镜像身份
OTHER_CHANNEL = "0b5c3a1e-2f4d-4e6a-8b9c-0d1e2f3a4b5c"
OTHER_CHAT = "oc_chat000000000000000000000000002"
NOW_TS = base.ts(NOW)


def ref_of(chat_id):
    return hashlib.sha256(("buzz-feishu-chat:v1:" + chat_id).encode()).hexdigest()


def entry(channel=CHANNEL, chat=CHAT, claimed_at=NOW_TS - 3600, heartbeat=NOW_TS - 60, takeover_of=None, policy=None):
    out = {"channel": channel, "chat_ref": ref_of(chat), "claimed_at": claimed_at, "heartbeat": heartbeat}
    if takeover_of:
        out["takeover_of"] = takeover_of
    out["policy"] = policy or {"remove_extras": True, "feishu_unmapped_senders": "context", "buzz_unmapped_senders": "skip",
                               "membership_sync": "two_way", "reaction_sync": "two_way"}
    return out


def mirror_claims(owner, mirror, bindings, *, created_at=T0 - 900, declared=True, extra=None):
    """镜像身份的 30177：由 owner 签名，feishu.mirror + bindings。"""
    feishu = {"mirror": True} if declared else {}
    feishu["bindings"] = list(bindings)
    content = {"name": "feishu-mirror", "parallelism": 1, "respond_to": "owner-only", "feishu": feishu}
    content.update(extra or {})
    return nostr_event(owner, 30177, [["d", mirror]], json.dumps(content, ensure_ascii=False), created_at)


def claim(mirror=MIRROR_B_PK, owner=FOREIGN_OWNER_PK, channel=CHANNEL, chat=CHAT, claimed_at=NOW_TS - 3600,
          heartbeat=NOW_TS - 60, takeover_of=""):
    return FGS.BindingClaim(mirror=mirror, owner=owner, channel=channel, chat_ref=ref_of(chat), claimed_at=claimed_at,
                            heartbeat=heartbeat, takeover_of=takeover_of)


def query_over(events):
    """扮演 relay 的 POST /query：按 kinds / authors / #d 过滤。"""
    calls = []

    def query(filters):
        calls.append(filters)

        def match(ev, f):
            if "kinds" in f and ev["kind"] not in f["kinds"]:
                return False
            if "authors" in f and ev["pubkey"] not in f["authors"]:
                return False
            if "#d" in f and not any(t[0] == "d" and t[1] in f["#d"] for t in ev["tags"]):
                return False
            return True
        return [ev for ev in events if any(match(ev, f) for f in filters)]
    query.calls = calls
    return query


# ================================ L1 : chat_ref、配置、策略 ================================


class ChatRefAndConfig(TmpCase):
    def load(self, **extra):
        raw = json.loads(Env(self.tmp).config.read_text())
        raw.pop("binding_claim", None)
        raw.update(extra)
        return FGS.load_config(write_owner_only(self.tmp / "c.json", json.dumps(raw)))

    def test_chat_ref_is_the_keyless_versioned_hash_of_the_chat_id(self):
        """L1-FGS-900: chat_ref = SHA-256("buzz-feishu-chat:v1:" + chat_id) 的小写 hex（64 位），不含 chat_id 本身；不同的群不同。"""
        self.assertEqual(FGS.chat_ref(CHAT), ref_of(CHAT))
        self.assertRegex(FGS.chat_ref(CHAT), r"^[0-9a-f]{64}$")
        self.assertNotIn(CHAT[3:], FGS.chat_ref(CHAT))
        self.assertNotEqual(FGS.chat_ref(CHAT), FGS.chat_ref(OTHER_CHAT))

    def test_binding_claim_is_optional_on_by_default_and_strictly_a_boolean(self):
        """L1-FGS-901: `binding_claim` 可选，不写就是开；`false` 关掉；别的值（字符串、数字、null）整份配置被拒，错误里不带值。"""
        self.assertIn("binding_claim", FGS.OPTIONAL_CONFIG_KEYS)
        self.assertTrue(FGS.binding_claim_enabled(self.load()))
        try:
            off = self.load(binding_claim=False)
        except FGS.GroupSyncError:
            self.fail("binding_claim: false must be accepted")
        self.assertFalse(FGS.binding_claim_enabled(off))
        for bad in ("no-secret", 0, None):
            with self.subTest(bad=bad):
                with self.assertRaises(FGS.GroupSyncError) as caught:
                    self.load(binding_claim=bad)
                self.assertNotIn("secret", str(caught.exception))

    def test_the_published_policy_is_booleans_and_enums_only(self):
        """L1-FGS-902: 认领里的 policy 恰好五个键（remove_extras、两个未映射发言人模式、成员与表情同步方向），值只有布尔和枚举，
        不含频道、群、人的任何标识。"""
        cfg = self.load(remove_extras=False, feishu_unmapped_senders="skip", membership_sync="buzz_to_feishu")
        policy = FGS.claim_policy(cfg)
        self.assertEqual(policy, {"remove_extras": False, "feishu_unmapped_senders": "skip", "buzz_unmapped_senders": "skip",
                                  "membership_sync": "buzz_to_feishu", "reaction_sync": "two_way"})
        blob = json.dumps(policy)
        for ident in (CHANNEL, CHAT, OWNER_PK, MIRROR_PK):
            self.assertNotIn(ident, blob)


# ================================ L1 : 读取认领 ================================


class ReadClaims(unittest.TestCase):
    def test_only_claims_in_an_owner_signed_mirror_policy_count(self):
        """L1-FGS-903: 只认声明了 feishu.mirror、且由该镜像最新 kind 0 的 NIP-OA owner 签名的最新一条 30177 里的 bindings：
        别人冒签的、没声明镜像的、更早一版的都不算；格式不对的条目（频道不是 UUID、chat_ref 不是 64 位 hex、时间是布尔或缺失、
        takeover_of 不是 pubkey）逐条跳过、不连累同一事件里的好条目。镜像名取自它的 kind 0。"""
        good = entry(channel=OTHER_CHANNEL)
        bad_entries = [dict(entry(), channel="not-a-uuid"), dict(entry(), chat_ref="OC_CHAT"), dict(entry(), claimed_at=True),
                       {k: v for k, v in entry().items() if k != "heartbeat"}, dict(entry(), takeover_of="xyz")]
        events = [
            profile(MIRROR_B_PK, FOREIGN_OWNER_PK, name="mirror-b"),
            mirror_claims(FOREIGN_OWNER_PK, MIRROR_B_PK, [entry()], created_at=T0 - 2000),  # older head: ignored
            mirror_claims(FOREIGN_OWNER_PK, MIRROR_B_PK, [good, *bad_entries], created_at=T0 - 900),
            mirror_claims(MALLORY_PK, MIRROR_B_PK, [entry(chat=OTHER_CHAT)], created_at=T0 - 100),  # forged: newer, wrong signer
            profile(MIRROR_C_PK, FOREIGN_OWNER_PK, name="mirror-c"),
            mirror_claims(FOREIGN_OWNER_PK, MIRROR_C_PK, [entry()], declared=False),  # not a declared mirror
        ]
        view = FGS.read_binding_claims(query_over(events))
        self.assertEqual([(c.mirror, c.owner, c.channel, c.chat_ref, c.name) for c in view.claims],
                         [(MIRROR_B_PK, FOREIGN_OWNER_PK, OTHER_CHANNEL, ref_of(CHAT), "mirror-b")])
        self.assertEqual(view.owners.get(MIRROR_B_PK), FOREIGN_OWNER_PK)

    def test_scope_keeps_same_channel_or_chat_rivals_and_complete_latest_policy(self):
        events=[profile(MIRROR_PK,OWNER_PK),mirror_claims(OWNER_PK,MIRROR_PK,[entry()]),
            profile(MIRROR_B_PK,FOREIGN_OWNER_PK),
            mirror_claims(FOREIGN_OWNER_PK,MIRROR_B_PK,[entry(channel=OTHER_CHANNEL)],created_at=T0-1000),
            mirror_claims(MALLORY_PK,MIRROR_B_PK,[entry(channel=OTHER_CHANNEL,chat=OTHER_CHAT)],created_at=T0),
            profile(MIRROR_C_PK,FOREIGN_OWNER_PK),mirror_claims(FOREIGN_OWNER_PK,MIRROR_C_PK,[entry(channel=OTHER_CHANNEL,chat=OTHER_CHAT)])]
        full=FGS.read_binding_claims(query_over(events))
        query=query_over(events)
        scoped=FGS.read_binding_claims(query,also={MIRROR_PK},scope=(CHANNEL,ref_of(CHAT)))
        relevant=lambda claims:[c for c in claims if c.channel==CHANNEL or c.chat_ref==ref_of(CHAT)]
        self.assertEqual(relevant(scoped.claims),relevant(full.claims))
        self.assertNotIn(MIRROR_C_PK,scoped.owners)
        self.assertIn(MIRROR_B_PK,scoped.owners)
        self.assertEqual(scoped.policies,full.policies)
        # Same-channel/different-chat is independently a conflict candidate.
        changed=mirror_claims(FOREIGN_OWNER_PK,MIRROR_C_PK,[entry(chat=OTHER_CHAT)],created_at=T0+1)
        updated=FGS.read_binding_claims(query_over([*events,changed]),scope=(CHANNEL,ref_of(CHAT)))
        self.assertIn(MIRROR_C_PK,updated.owners)
        # A genuine newer owner policy revokes the old conflicting entry.
        revoked=mirror_claims(FOREIGN_OWNER_PK,MIRROR_B_PK,[],created_at=T0+1)
        after=FGS.read_binding_claims(query_over([*events,revoked]),scope=(CHANNEL,ref_of(CHAT)))
        self.assertFalse(any(c.mirror==MIRROR_B_PK for c in after.claims))

    def test_the_own_mirror_is_looked_up_even_without_a_policy(self):
        """L1-FGS-903b: `also` 里的镜像（本机的）即使还没有任何 30177，也读它的 kind 0，得到它的 NIP-OA owner；所有 30177 原样留在
        view.policies 里供同一轮的 agent 反查复用（ADR-0020 的全量读取）。"""
        events = [profile(MIRROR_PK, OWNER_PK, name="our-mirror"),
                  mirror_claims(FOREIGN_OWNER_PK, MIRROR_B_PK, [entry()])]
        view = FGS.read_binding_claims(query_over(events), also={MIRROR_PK})
        self.assertEqual(view.owners.get(MIRROR_PK), OWNER_PK)
        self.assertEqual(view.names.get(MIRROR_PK), "our-mirror")
        self.assertEqual(len([e for e in view.policies if e["kind"] == 30177]), 1)


# ================================ L1 : 有效、胜负、相关 ================================


class Rules(unittest.TestCase):
    def test_a_claim_is_valid_while_its_heartbeat_is_inside_the_lease(self):
        """L1-FGS-904: 心跳在 30 分钟租期内有效（正好 30 分钟也算），多一秒就过期；远在未来的心跳（超过 relay 容忍的 15 分钟）不算。"""
        self.assertEqual((FGS.CLAIM_LEASE_SECONDS, FGS.CLAIM_HEARTBEAT_SECONDS), (1800, 600))
        self.assertTrue(FGS.claim_valid(claim(heartbeat=NOW_TS - 1800), NOW_TS))
        self.assertTrue(FGS.claim_valid(claim(heartbeat=NOW_TS), NOW_TS))
        self.assertFalse(FGS.claim_valid(claim(heartbeat=NOW_TS - 1801), NOW_TS))
        self.assertFalse(FGS.claim_valid(claim(heartbeat=NOW_TS + 3600), NOW_TS))

    def test_the_earlier_claim_wins_then_the_smaller_mirror_and_a_named_takeover_beats_its_target(self):
        """L1-FGS-905: claimed_at 早的赢；相同时镜像 pubkey 小的赢；指名接管（takeover_of = 对方镜像）的胜过被指名的一方，不看时间；
        互相指名时后接管的（claimed_at 大的）赢。"""
        small, big = sorted([MIRROR_B_PK, MIRROR_C_PK])
        early, late = claim(mirror=big, claimed_at=100), claim(mirror=small, claimed_at=200)
        self.assertTrue(FGS.claim_beats(early, late))
        self.assertFalse(FGS.claim_beats(late, early))
        a, b = claim(mirror=small, claimed_at=100), claim(mirror=big, claimed_at=100)
        self.assertTrue(FGS.claim_beats(a, b))
        self.assertFalse(FGS.claim_beats(b, a))
        taker = claim(mirror=small, claimed_at=900, takeover_of=big)
        self.assertTrue(FGS.claim_beats(taker, early))
        self.assertFalse(FGS.claim_beats(early, taker))
        first, second = claim(mirror=small, claimed_at=300, takeover_of=big), claim(mirror=big, claimed_at=400, takeover_of=small)
        self.assertTrue(FGS.claim_beats(second, first))
        self.assertFalse(FGS.claim_beats(first, second))

    def test_which_claims_are_rivals(self):
        """L1-FGS-906: 同频道的别的认领只算「声明方是本频道 bot 成员、它的 owner 是频道 owner/admin」的；同一个 chat_ref、别的频道的认领
        不核对角色（知道 chat_id 的只有群成员）；自己这一条（同镜像、同频道、同群）不算；本镜像给同频道别的群的认领算。"""
        roles = {MIRROR_B_PK: "bot", FOREIGN_OWNER_PK: "admin", MIRROR_C_PK: "bot", MALLORY_PK: "member", MIRROR_PK: "bot",
                 OWNER_PK: "owner"}
        claims = (
            claim(mirror=MIRROR_B_PK, owner=FOREIGN_OWNER_PK),  # same channel, qualified
            claim(mirror=MIRROR_C_PK, owner=MALLORY_PK),  # same channel, owner only a member
            claim(mirror=AGENT_PK, owner=FOREIGN_OWNER_PK),  # same channel, not a member of it
            claim(mirror=MIRROR_C_PK, owner=MALLORY_PK, channel=OTHER_CHANNEL),  # same group, other channel: no role check
            claim(mirror=MIRROR_B_PK, owner=FOREIGN_OWNER_PK, channel=OTHER_CHANNEL, chat=OTHER_CHAT),  # unrelated
            claim(mirror=MIRROR_PK, owner=OWNER_PK),  # this very binding
            claim(mirror=MIRROR_PK, owner=OWNER_PK, chat=OTHER_CHAT),  # this mirror, same channel, another group
        )
        view = FGS.ClaimView(claims, {}, {})
        rivals = FGS.rival_claims(view, mirror=MIRROR_PK, channel=CHANNEL, ref=ref_of(CHAT), roles=roles)
        self.assertEqual({(c.mirror, c.channel, c.chat_ref) for c in rivals},
                         {(MIRROR_B_PK, CHANNEL, ref_of(CHAT)), (MIRROR_C_PK, OTHER_CHANNEL, ref_of(CHAT)),
                          (MIRROR_PK, CHANNEL, ref_of(OTHER_CHAT))})
        # create-chat: no group yet, only the channel is checked
        no_group = FGS.rival_claims(view, mirror=MIRROR_PK, channel=CHANNEL, ref=None, roles=roles)
        self.assertEqual({(c.mirror, c.chat_ref) for c in no_group},
                         {(MIRROR_B_PK, ref_of(CHAT)), (MIRROR_PK, ref_of(CHAT)), (MIRROR_PK, ref_of(OTHER_CHAT))})


# ================================ L1 : 读改写 30177 ================================


class RewriteContent(unittest.TestCase):
    def head(self):
        other = entry(channel=OTHER_CHANNEL, chat=OTHER_CHAT)
        content = {"name": "镜像", "parallelism": 1, "respond_to": "owner-only", "custom": {"z": [1, 2.5, "é"]},
                   "feishu": {"mirror": True, "note": "keep", "bindings": [other, entry(heartbeat=1)]}}
        return json.dumps(content, ensure_ascii=False), other

    def test_only_this_bindings_entry_changes(self):
        """L1-FGS-907: 读改写只动本绑定（同频道 + 同 chat_ref）那一条（原位替换），其余字段和别的条目逐字保留（键序、非 ASCII 都不变）；
        entry=None 撤掉这一条、别的不动；没有这一条时追加在末尾。"""
        content, other = self.head()
        mine = entry(heartbeat=NOW_TS)
        out = FGS.with_binding_claim(content, name="x", channel=CHANNEL, ref=ref_of(CHAT), entry=mine)
        doc = json.loads(out)
        self.assertEqual(doc["feishu"]["bindings"], [other, mine])
        self.assertIn(json.dumps(other, ensure_ascii=False), out)
        self.assertIn('"custom": {"z": [1, 2.5, "é"]}', out)
        self.assertEqual(list(doc), ["name", "parallelism", "respond_to", "custom", "feishu"])
        self.assertEqual(list(doc["feishu"]), ["mirror", "note", "bindings"])
        gone = json.loads(FGS.with_binding_claim(content, name="x", channel=CHANNEL, ref=ref_of(CHAT), entry=None))
        self.assertEqual(gone["feishu"]["bindings"], [other])
        added = json.loads(FGS.with_binding_claim(content, name="x", channel=OTHER_CHANNEL, ref=ref_of(CHAT), entry=mine))
        self.assertEqual(len(added["feishu"]["bindings"]), 3)
        self.assertEqual(added["feishu"]["bindings"][-1], mine)

    def test_a_missing_policy_is_created_as_a_declared_mirror(self):
        """L1-FGS-907b: 镜像还没有 30177 时新建一条最保守的（名字、parallelism 1、respond_to owner-only）并声明 mirror；
        已有但没声明 mirror 的补上 "mirror": true。"""
        mine = entry()
        doc = json.loads(FGS.with_binding_claim(None, name="feishu-mirror", channel=CHANNEL, ref=ref_of(CHAT), entry=mine))
        self.assertEqual(doc, {"name": "feishu-mirror", "parallelism": 1, "respond_to": "owner-only",
                               "feishu": {"mirror": True, "bindings": [mine]}})
        plain = json.dumps({"name": "m", "parallelism": 1, "respond_to": "owner-only"})
        doc = json.loads(FGS.with_binding_claim(plain, name="x", channel=CHANNEL, ref=ref_of(CHAT), entry=mine))
        self.assertEqual(doc["feishu"], {"mirror": True, "bindings": [mine]})

    def test_it_refuses_to_turn_an_agent_into_a_mirror_or_to_merge_into_garbage(self):
        """L1-FGS-907c: 30177 里写着飞书 app_id（那是 agent，不是镜像）、content 不是 JSON 对象、bindings 不是列表、
        既有 feishu 字段是非对象（不是缺失，是格式错误）时都拒绝（GroupSyncError），什么都不改，不静默丢弃未知内容。"""
        mine = entry()
        for content in (json.dumps({"name": "a", "feishu": {"app_id": "cli_agent00000000001"}}), "[1]", "not json",
                        json.dumps({"name": "m", "feishu": {"mirror": True, "bindings": {"x": 1}}}),
                        json.dumps({"name": "m", "feishu": "not-an-object"}),
                        json.dumps({"name": "m", "feishu": [1, 2]}),
                        json.dumps({"name": "m", "feishu": None})):
            with self.subTest(content=content):
                refused = False
                try:
                    FGS.with_binding_claim(content, name="x", channel=CHANNEL, ref=ref_of(CHAT), entry=mine)
                except FGS.GroupSyncError:
                    refused = True
                self.assertTrue(refused)


# ================================ L2-1 : 一轮里的认领 ================================


class ClaimWorld(TwoWay):
    """认领开着的世界。本机镜像的 kind 0 由 owner（signer key 的主人）背书；对手镜像 MIRROR_B 也是本频道的 bot 成员，
    它的 owner 就是频道 owner（同一个人在另一台机器上跑了一套）。"""

    def env(self, **overrides):
        return super().env(**{"binding_claim": True, **overrides})

    def world(self, rival=None, *, rival_in_channel=True):
        w = super().world()
        w.relay_events = [profile(MIRROR_PK, OWNER_PK, name="feishu-mirror")]
        if rival is not None:
            if rival_in_channel:
                w.members.append({"pubkey": MIRROR_B_PK, "role": "bot"})
                w.display[MIRROR_B_PK] = "mirror-b"
            w.relay_events += [profile(MIRROR_B_PK, OWNER_PK, name="江领的镜像"), mirror_claims(OWNER_PK, MIRROR_B_PK, rival)]
        w.users |= {EXTRA_OPEN}  # 群里多余的人：remove_extras 的首轮会把他移出（成员同步在跑的证据）
        w.events = [base.event(base.eid(1), ALICE_PK, "Buzz 这边说的话")]
        w.messages = [base.fmsg("om_f1", ALICE_OPEN, "飞书那边说的话")]
        return w

    @staticmethod
    def claim_writes(w, mirror=MIRROR_PK):
        return [a for a in w.relay_write_attempts if a["event"]["kind"] == 30177
                and ["d", mirror] in a["event"]["tags"]]

    @staticmethod
    def our_bindings(w):
        heads = [e for e in w.relay_events if e["kind"] == 30177 and ["d", MIRROR_PK] in e["tags"]]
        if not heads:
            return None
        head = max(heads, key=lambda e: (e["created_at"], e["id"]))
        return json.loads(head["content"])["feishu"].get("bindings")

    @staticmethod
    def synced(w):
        """(Buzz → 飞书 有没有发, 飞书 → Buzz 有没有发, 有没有成员变动)"""
        return (any("Buzz 这边说的话" in text_of(c) for c in w.lark_sends()),
                any("飞书那边说的话" in (c["content"] or "") or any("飞书那边说的话" in str(a) for a in c["args"])
                    for c in w.buzz_sends()),
                bool(w.member_ops()))

    def stop_notices(self, w):
        feishu = [c for c in w.lark_sends() if c["app"] == OWNER_APP and "这边已停" in text_of(c)]
        buzz = [e for e in w.relay_writes if e["kind"] == 9 and e["pubkey"] == MIRROR_PK and "这边已停" in e["content"]]
        return feishu, buzz



class ClaimRound(ClaimWorld):
    # -- publishing and heartbeat

    def test_the_first_round_claims_the_binding_in_the_mirrors_policy(self):
        """L2-1-FGS-910: 首轮就在镜像的 30177 里写上本绑定的认领：由 signer key（镜像的 NIP-OA owner）签名、d=镜像；
        {channel, chat_ref, claimed_at=heartbeat=本轮时间, policy}，声明 mirror；chat_id 不出现在任何公开事件里；报告 claim_published=1；
        本轮照常同步。已有的 30177（别的绑定、未知字段、标签）原样保留。"""
        w = self.world()
        other = entry(channel=OTHER_CHANNEL, chat=OTHER_CHAT)
        head = mirror_claims(OWNER_PK, MIRROR_PK, [other], extra={"custom": {"keep": "é"}})
        head["tags"]  # the fixture's tags are [["d", mirror]]
        w.relay_events.append(head)
        report = self.env().round(w)
        writes = self.claim_writes(w)
        self.assertEqual(len(writes), 1)
        write = writes[0]
        ev = write["event"]
        self.assertEqual((write["signer"], ev["pubkey"]), (OWNER_PK, OWNER_PK))
        self.assertEqual(ev["tags"], head["tags"])
        doc = json.loads(ev["content"])
        self.assertIs(doc["feishu"]["mirror"], True)
        self.assertEqual(doc["custom"], {"keep": "é"})
        self.assertEqual(doc["feishu"]["bindings"][0], other)
        self.assertIn(json.dumps(other, ensure_ascii=False), ev["content"])
        mine = doc["feishu"]["bindings"][1]
        self.assertEqual(mine, {"channel": CHANNEL, "chat_ref": ref_of(CHAT), "claimed_at": NOW_TS, "heartbeat": NOW_TS,
                                "policy": {"remove_extras": True, "feishu_unmapped_senders": "context",
                                           "buzz_unmapped_senders": "skip", "membership_sync": "two_way",
                                           "reaction_sync": "two_way"}})
        for attempt in w.relay_write_attempts:
            self.assertNotIn(CHAT, json.dumps(attempt["event"]))
        self.assertEqual(report["claim_published"], 1)
        self.assertEqual(self.synced(w), (True, True, True))
        self.assertIsNone(report["claim_conflict"])

    def test_the_heartbeat_is_refreshed_once_it_is_ten_minutes_old(self):
        """L2-1-FGS-911: 心跳不到 10 分钟不再写；过了 10 分钟写一次，claimed_at 不变、heartbeat 是本轮时间；策略变了也立即重写。"""
        w = self.world()
        env = self.env()
        env.round(w)
        env.round(w, now=NOW + base.timedelta(minutes=5))
        self.assertEqual(len(self.claim_writes(w)), 1)
        later = NOW + base.timedelta(minutes=11)
        report = env.round(w, now=later)
        self.assertEqual(len(self.claim_writes(w)), 2)
        (mine,) = self.our_bindings(w)
        self.assertEqual((mine["claimed_at"], mine["heartbeat"]), (NOW_TS, base.ts(later)))
        self.assertEqual(report["claim_published"], 1)
        changed = self.env(remove_extras=False)
        changed.round(w, now=later + base.timedelta(minutes=1))
        self.assertEqual(len(self.claim_writes(w)), 3)
        self.assertIs(self.our_bindings(w)[0]["policy"]["remove_extras"], False)

    # -- losing, winning, expiry

    def test_the_later_claim_on_the_same_channel_stops_the_whole_binding(self):
        """L2-1-FGS-912: 同频道有一条更早的有效认领（对手是本频道 bot，owner 是频道 owner）：本轮什么都不同步（两个方向的消息、成员），
        绑定不开始、游标不动；报告 claim_conflict（原因 same_channel、对手镜像与名字），退出码 3；owner bot 在群里、镜像在频道里各提示一次，
        写明对手镜像名和「这边已停」，之后的轮次不再提示；本机仍写一条待命认领（claimed_at 更晚）。"""
        w = self.world([entry(claimed_at=NOW_TS - 7200, heartbeat=NOW_TS - 120)])
        env = self.env()
        with open(self.tmp / "out.json", "w") as fh:
            code = FGS.main(["round", "--config", str(env.config), "--state-dir", str(env.state_dir)], base_env=env.base_env,
                            runner=w, stdout=fh, now=NOW, http=w.http_get)
        report = json.loads((self.tmp / "out.json").read_text())
        self.assertEqual(code, FGS.EXIT_ATTENTION)
        self.assertEqual(self.synced(w), (False, False, False))
        self.assertEqual(report["claim_conflict"], {"reason": "same_channel", "mirror": MIRROR_B_PK[:12], "name": "江领的镜像"})
        state = env.state()
        self.assertEqual((state["binding"], state["buzz_since"], state["feishu_since"]), ("", 0, 0))
        feishu, buzz = self.stop_notices(w)
        self.assertEqual(len(feishu), 1)
        self.assertEqual(feishu[0]["as"], "bot")
        self.assertIn("江领的镜像", text_of(feishu[0]))
        self.assertEqual(len(buzz), 1)
        self.assertIn(["h", CHANNEL], buzz[0]["tags"])
        self.assertIn("江领的镜像", buzz[0]["content"])
        self.assertEqual(self.our_bindings(w)[0]["claimed_at"], NOW_TS)  # standby
        env.round(w, now=NOW + base.timedelta(minutes=1))
        self.assertEqual(tuple(map(len, self.stop_notices(w))), (1, 1))
        self.assertEqual(self.synced(w), (False, False, False))

    def test_an_expired_rival_is_ignored_and_the_takeover_counted_once(self):
        """L2-1-FGS-913: 对手的心跳已超过 30 分钟：忽略它，本轮照常同步，报告 claim_takeovers=1；下一轮还是它，不再计。"""
        w = self.world([entry(claimed_at=NOW_TS - 7200, heartbeat=NOW_TS - 1801)])
        env = self.env()
        report = env.round(w)
        self.assertEqual(self.synced(w), (True, True, True))
        self.assertIsNone(report["claim_conflict"])
        self.assertEqual(report["claim_takeovers"], 1)
        again = env.round(w, now=NOW + base.timedelta(minutes=1))
        self.assertEqual(again["claim_takeovers"], 0)
        self.assertEqual(self.stop_notices(w), ([], []))

    def test_the_loser_takes_over_by_itself_once_the_winner_stops_heartbeating(self):
        """L2-1-FGS-913b: 换机器：旧机器停了心跳，新机器（本机，待命认领）等它过期后自动接管，从那一轮开始同步。"""
        w = self.world([entry(claimed_at=NOW_TS - 7200, heartbeat=NOW_TS - 60)])
        env = self.env()
        env.round(w)
        self.assertEqual(env.state()["binding"], "")
        later = NOW + base.timedelta(minutes=31)
        w.messages = [base.fmsg("om_f2", ALICE_OPEN, "接管以后说的", when=later - base.timedelta(minutes=1))]
        report = env.round(w, now=later)
        self.assertIsNone(report["claim_conflict"])
        self.assertEqual(report["claim_takeovers"], 1)
        self.assertEqual(env.state()["binding"], f"{CHANNEL}|{CHAT}")
        self.assertTrue(any("接管以后说的" in str(c["args"]) or "接管以后说的" in (c["content"] or "") for c in w.buzz_sends()))

    def test_a_host_back_after_its_lease_ran_out_does_not_beat_the_one_that_took_over(self):
        """L2-1-FGS-926: 本机停了一个多小时（自己的认领早已过期），期间别的机器接管（它的 claimed_at 比本机当初的晚）：本机回来后自己的
        认领按「现在」重新认领，输给接管者，停下并写待命认领；不能拿过期的旧 claimed_at 把接管者挤掉。"""
        w = self.world([entry(claimed_at=NOW_TS - 1800, heartbeat=NOW_TS - 60)])
        w.relay_events.append(mirror_claims(OWNER_PK, MIRROR_PK, [entry(claimed_at=NOW_TS - 7200, heartbeat=NOW_TS - 3700)]))
        report = self.env().round(w)
        self.assertEqual((report["claim_conflict"] or {}).get("reason"), "same_channel")
        self.assertEqual(self.synced(w), (False, False, False))
        self.assertEqual([(b["claimed_at"], b["heartbeat"]) for b in self.our_bindings(w)], [(NOW_TS, NOW_TS)])

    def test_the_earlier_claim_wins_and_a_tie_goes_to_the_smaller_mirror(self):
        """L2-1-FGS-914: 对手的认领更晚：本机赢，照常同步，不提示。同一秒认领（首轮 claimed_at 都是本轮时间）：镜像 pubkey 小的赢
        （夹具里对手的更小，所以本机停下）。"""
        self.assertLess(MIRROR_B_PK, MIRROR_PK)  # the fixture's premise
        for rival_claimed, we_win in ((NOW_TS + 60, True), (NOW_TS, False)):
            with self.subTest(rival_claimed=rival_claimed):
                sub = self.tmp / str(rival_claimed)
                sub.mkdir()
                self.tmp, saved = sub, self.tmp
                try:
                    w = self.world([entry(claimed_at=rival_claimed, heartbeat=NOW_TS)])
                    report = self.env().round(w)
                finally:
                    self.tmp = saved
                self.assertEqual(report["claim_conflict"] is None, we_win)
                self.assertEqual(self.synced(w), (True, True, True) if we_win else (False, False, False))
                self.assertEqual(tuple(map(len, self.stop_notices(w))), (0, 0) if we_win else (1, 1))

    def test_the_same_group_from_another_channel_counts_without_a_role_check(self):
        """L2-1-FGS-915: 另一个频道把同一个群（chat_ref 相同）认领在先：对手不在本频道、owner 也不是本频道的人，照样算，本机停下，
        原因 same_chat；同一个频道、但声明方不是本频道 bot 成员的认领不算。"""
        w = self.world([entry(channel=OTHER_CHANNEL, claimed_at=NOW_TS - 7200)], rival_in_channel=False)
        report = self.env().round(w)
        self.assertEqual(self.synced(w), (False, False, False))
        self.assertEqual((report["claim_conflict"] or {}).get("reason"), "same_chat")
        (self.tmp / "b").mkdir()
        self.tmp = self.tmp / "b"
        w = self.world([entry(claimed_at=NOW_TS - 7200)], rival_in_channel=False)
        report = self.env().round(w)
        self.assertIsNone(report["claim_conflict"])
        self.assertEqual(self.synced(w), (True, True, True))

    # -- takeover

    def test_take_over_claims_the_binding_now(self):
        """L2-1-FGS-916: `round --take-over`：对手有效且在先，本机仍写一条 takeover_of=对手镜像 的认领，并在本轮照常同步。"""
        w = self.world([entry(claimed_at=NOW_TS - 7200, heartbeat=NOW_TS - 60)])
        env = self.env()
        with open(self.tmp / "out.json", "w") as fh:
            code = FGS.main(["round", "--config", str(env.config), "--state-dir", str(env.state_dir), "--take-over"],
                            base_env=env.base_env, runner=w, stdout=fh, now=NOW, http=w.http_get)
        report = json.loads((self.tmp / "out.json").read_text())
        self.assertIsNone(report["claim_conflict"])
        bindings = self.our_bindings(w) or []
        self.assertEqual(len(bindings), 1)
        self.assertEqual(bindings[0].get("takeover_of"), MIRROR_B_PK)
        self.assertEqual(self.synced(w), (True, True, True))
        self.assertNotEqual(code, FGS.EXIT_ERROR)

    def test_a_binding_taken_over_stops_and_withdraws_its_claim(self):
        """L2-1-FGS-917: 别的镜像写了 takeover_of=本机镜像 的有效认领：本机这一轮停下（原因 taken_over），撤掉自己那一条（别的条目不动），
        群里和频道里各提示一次。"""
        other = entry(channel=OTHER_CHANNEL, chat=OTHER_CHAT)
        w = self.world([entry(claimed_at=NOW_TS - 60, heartbeat=NOW_TS - 60, takeover_of=MIRROR_PK)])
        w.relay_events.append(mirror_claims(OWNER_PK, MIRROR_PK, [other, entry(claimed_at=NOW_TS - 7200)]))
        report = self.env().round(w)
        self.assertEqual((report["claim_conflict"] or {}).get("reason"), "taken_over")
        self.assertEqual(self.synced(w), (False, False, False))
        self.assertEqual(self.our_bindings(w), [other])
        self.assertEqual(tuple(map(len, self.stop_notices(w))), (1, 1))

    # -- failures

    def test_unreadable_claims_pause_membership_only(self):
        """L2-1-FGS-918: 读认领失败（relay 连不上）：消息照常两个方向同步，成员变动暂停这一轮（多余的人不移出），报告 claims_unreadable=1，
        不写认领、不算冲突。"""
        w = self.world()
        w.relay_fail = ["network"]
        report = self.env().round(w)
        self.assertEqual(report["claims_unreadable"], 1)
        self.assertEqual(self.synced(w), (True, True, False))
        self.assertIn(EXTRA_OPEN, w.users)
        self.assertEqual(self.claim_writes(w), [])
        self.assertIsNone(report["claim_conflict"])

    def test_a_claim_that_cannot_be_written_is_retried_next_round(self):
        """L2-1-FGS-919: 写认领失败（relay 500）：本轮照常同步，claim_publish_failed=1；下一轮（不到 10 分钟）重写成功。"""
        w = self.world()
        w.relay_write_fail = ["500"]
        env = self.env()
        report = env.round(w)
        self.assertEqual(report["claim_publish_failed"], 1)
        self.assertEqual(self.synced(w), (True, True, True))
        self.assertIsNone(self.our_bindings(w))
        again = env.round(w, now=NOW + base.timedelta(minutes=1))
        self.assertEqual(again["claim_published"], 1)
        self.assertEqual(len(self.our_bindings(w)), 1)

    def test_a_mirror_owned_by_someone_else_publishes_nothing(self):
        """L2-1-FGS-920: 镜像的 kind 0 背书的 owner 不是 signer key 的主人：不发认领，本轮报错说明（errors、skipped.claim_owner_mismatch），
        同步照常。"""
        w = self.world()
        w.relay_events = [profile(MIRROR_PK, FOREIGN_OWNER_PK, name="feishu-mirror")]
        report = self.env().round(w)
        self.assertEqual(self.claim_writes(w), [])
        self.assertEqual(report["skipped"].get("claim_owner_mismatch"), 1)
        self.assertGreaterEqual(report["errors"], 1)
        self.assertTrue(self.synced(w)[0])

    def test_the_policy_is_rewritten_under_the_owners_lock(self):
        """L2-1-FGS-921: 读改写在 owner 的锁下做（~/.local/state/buzz-agent-feishu-app/locks/<owner>.lock，与 buzz_agent_feishu_app.py
        同一把）：锁被别的进程占着就不写，claim_publish_failed=1，同步照常。"""
        import fcntl
        lock_dir = self.tmp / ".local" / "state" / "buzz-agent-feishu-app" / "locks"
        lock_dir.mkdir(parents=True, mode=0o700)
        w = self.world()
        with open(lock_dir / f"{OWNER_PK}.lock", "a") as held, \
                mock.patch.object(FGS, "CLAIM_LOCK_WAIT_SECONDS", 0, create=True):
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            report = self.env().round(w)
        self.assertEqual(self.claim_writes(w), [])
        self.assertEqual(report["claim_publish_failed"], 1)
        self.assertTrue(self.synced(w)[0])

    def test_a_failed_stop_notice_is_retried_and_never_repeated(self):
        """L2-1-FGS-922: 群里的停止提示发送失败（限流）：下一轮用同一个幂等键再发一次，成功后不再发；频道里的提示只发一次。"""
        w = self.world([entry(claimed_at=NOW_TS - 7200, heartbeat=NOW_TS - 60)])
        w.lark_send_fail = ["rate_limited"]
        env = self.env()
        env.round(w)
        tries = lambda: self.stop_notices(w)[0]  # noqa: E731 -- every attempt, the refused one included
        key = lambda call: call["args"][call["args"].index("--idempotency-key") + 1]  # noqa: E731
        self.assertEqual(len(tries()), 1)
        self.assertNotIn(key(tries()[0]), w.sent_keys)
        env.round(w, now=NOW + base.timedelta(minutes=1))
        env.round(w, now=NOW + base.timedelta(minutes=2))
        self.assertEqual((len(tries()), len({key(c) for c in tries()})), (2, 1))
        self.assertIn(key(tries()[0]), w.sent_keys)
        self.assertEqual(len(self.stop_notices(w)[1]), 1)

    # -- off, upgrade, one read

    def test_binding_claim_false_reads_and_writes_nothing(self):
        """L2-1-FGS-923: `binding_claim: false`：不读认领、不写 30177，在先的对手认领也不管，照常同步（回到只靠本机防重）。"""
        w = self.world([entry(claimed_at=NOW_TS - 7200)])
        report = self.env(binding_claim=False).round(w)
        self.assertEqual(self.claim_writes(w), [])
        self.assertFalse(any(q["filters"] == [{"kinds": [30177]}] for q in w.relay_queries))
        self.assertIsNone(report["claim_conflict"])
        self.assertEqual(self.synced(w), (True, True, True))

    def test_an_older_state_upgrades_with_an_empty_claim_ledger(self):
        """L2-1-FGS-924: 认领之前写的 state（没有 claim_notes / claim_takeovers）照常读取，两个账本从空开始。"""
        state_dir = self.tmp / "old-state"
        state_dir.mkdir(mode=0o700)
        old = FGS.asdict(FGS.State(binding=f"{CHANNEL}|{CHAT}", floor=1, buzz_since=1, feishu_since=1))
        old.pop("claim_notes", None)
        old.pop("claim_takeovers", None)
        write_owner_only(state_dir / FGS.STATE_FILE, json.dumps(old))
        try:
            loaded = FGS.load_state(state_dir)
        except FGS.GroupSyncError:
            self.fail("a pre-claim state must load")
        self.assertEqual((getattr(loaded, "claim_notes", None), getattr(loaded, "claim_takeovers", None)), ({}, {}))

    def test_the_agent_lookup_reuses_the_rounds_full_policy_read(self):
        """L2-1-FGS-925: 同一轮里认领和 ADR-0020 的 agent 反查都要全量读 30177：只读一次。"""
        w = self.world()
        env = self.env()
        env.round(w)
        env.round(w, now=NOW + base.timedelta(minutes=1))
        w.relay_queries.clear()
        w.bots[TEAM_BOT_APP] = "ou_teambot000000000000000000000001"
        env.round(w, now=NOW + base.timedelta(minutes=2))
        self.assertEqual(len([q for q in w.relay_queries if q["filters"] == [{"kinds": [30177]}]]), 1)


# ================================ L2-1 : bind / create-chat 预检 ================================


class ClaimPreflight(ClaimWorld):
    def unbound(self, **overrides):
        env = self.env(**overrides)
        raw = json.loads(env.config.read_text())
        raw["chat_id"] = None
        write_owner_only(env.config, json.dumps(raw))
        return env

    def test_bind_refuses_a_group_another_mirror_claims(self):
        """L2-1-FGS-930: `bind` 的预检先查认领：这个群（chat_ref）已被别的频道的有效认领占着 → 阻断项 claimed_by_other_mirror，
        输出里 claimed_by 说明是哪个镜像（名字和 pubkey 前 12 位），不含 chat_id；bind 拒绝、配置不改、群不动；退出码 2。"""
        w = self.world([entry(channel=OTHER_CHANNEL)], rival_in_channel=False)
        env = self.unbound()
        before = env.config.read_bytes()
        out = FGS.preflight_command(env.config, "existing", CHAT, base_env=env.base_env, runner=w, http=w.http_get, now=NOW)
        self.assertFalse(out["ok"])
        self.assertIn("claimed_by_other_mirror", out["problems"])
        self.assertEqual(out.get("claimed_by"), [{"mirror": MIRROR_B_PK[:12], "name": "江领的镜像", "reason": "same_chat"}])
        self.assertNotIn(CHAT, json.dumps(out))
        refused = None
        try:
            FGS.bind_command(env.config, CHAT, base_env=env.base_env, runner=w, http=w.http_get, now=NOW)
        except FGS.GroupSyncError as exc:
            refused = str(exc)
        self.assertIsNotNone(refused)
        self.assertIn("claimed_by_other_mirror", refused or "")
        self.assertIn("江领的镜像", refused or "")
        self.assertEqual(env.config.read_bytes(), before)
        self.assertEqual(w.member_ops(), [])
        with open(self.tmp / "out.json", "w") as fh:
            code = FGS.main(["preflight", "--config", str(env.config), "--mode", "existing", "--chat-id", CHAT],
                            base_env=env.base_env, runner=w, stdout=fh, http=w.http_get, now=NOW)
        self.assertEqual(code, FGS.EXIT_BLOCKED)

    def test_create_chat_refuses_a_channel_another_mirror_claims(self):
        """L2-1-FGS-931: `create-chat` 的预检查本频道：别的镜像（本频道 bot，owner 是频道 owner）的有效认领占着 → claimed_by_other_mirror，
        不建群、不留意图文件、配置不改。"""
        w = self.world([entry()])
        env = self.unbound()
        out = FGS.preflight_command(env.config, "new", None, base_env=env.base_env, runner=w, http=w.http_get, now=NOW)
        self.assertIn("claimed_by_other_mirror", out["problems"])
        self.assertEqual([c["reason"] for c in out.get("claimed_by", [])], ["same_channel"])
        refused = False
        try:
            FGS.create_chat_command(env.config, "测试群", base_env=env.base_env, runner=w, http=w.http_get, now=NOW)
        except FGS.GroupSyncError:
            refused = True
        self.assertTrue(refused)
        self.assertEqual(w.created, [])
        self.assertFalse(Path(str(env.config) + FGS.CREATE_INTENT_SUFFIX).exists())
        self.assertIsNone(json.loads(env.config.read_text())["chat_id"])

    def test_what_does_not_block_a_binding(self):
        """L2-1-FGS-932: 不阻断：本机镜像自己的认领（换绑时旧群那条还在租期里）、已过期的认领、不是本频道 bot 成员的同频道认领；
        读不到认领时只给警告 claims_unreadable；binding_claim=false 时根本不读。"""
        cases = {
            "own mirror": ([], [entry(chat=OTHER_CHAT, claimed_at=NOW_TS - 7200)], True),
            "expired": ([entry(heartbeat=NOW_TS - 1801)], [], True),
            "not a member": ([entry()], [], False),
        }
        for label, (rival, ours, rival_in_channel) in cases.items():
            with self.subTest(label=label):
                sub = self.tmp / label.replace(" ", "-")
                sub.mkdir()
                self.tmp, saved = sub, self.tmp
                try:
                    w = self.world(rival or None, rival_in_channel=rival_in_channel)
                    if ours:
                        w.relay_events.append(mirror_claims(OWNER_PK, MIRROR_PK, ours))
                    env = self.unbound()
                    out = FGS.preflight_command(env.config, "existing", CHAT, base_env=env.base_env, runner=w, http=w.http_get, now=NOW)
                finally:
                    self.tmp = saved
                self.assertNotIn("claimed_by_other_mirror", out["problems"])
        w = self.world()
        w.relay_fail = ["network"]
        (self.tmp / "u").mkdir()
        self.tmp = self.tmp / "u"
        env = self.unbound()
        out = FGS.preflight_command(env.config, "existing", CHAT, base_env=env.base_env, runner=w, http=w.http_get, now=NOW)
        self.assertTrue(out["ok"])
        self.assertIn("claims_unreadable", out["warnings"])
        w = self.world([entry()])
        w.relay_queries.clear()
        (self.tmp / "off").mkdir()
        self.tmp = self.tmp / "off"
        env = self.unbound(binding_claim=False)
        out = FGS.preflight_command(env.config, "existing", CHAT, base_env=env.base_env, runner=w, http=w.http_get, now=NOW)
        self.assertNotIn("claimed_by_other_mirror", out["problems"])
        self.assertEqual(w.relay_queries, [])


if __name__ == "__main__":
    unittest.main()
