"""飞书里也能同意 agent 入群（ADR-0020，engineering/skills#148）。

ADR-0018 只认 agent owner 本人在 Buzz 里签名的 ✅/❌ 或 `/approve|/deny JOIN-<id>`。ADR-0020 让飞书里的同意也算数：群同步把 owner 在飞书里
对申请打的 ✅/❌、或在申请话题里回复的 `/approve|/deny JOIN-<id>`，由**镜像身份**在 Buzz 的申请消息上打成 ✅/❌（kind 7），带
`["feishu-author", <这个人的 pubkey>]`，由回复转来的还带 `["join", "JOIN-<id>"]`。

入群申请脚本（配置 `accept_feishu_approvals`，缺省 true）另外承认这样的表情，条件是：
- 作者是本频道的**可信镜像**：是频道的 bot 成员；它的 kind:30177 由它的 NIP-OA owner 签名、content 里 `"feishu": {"mirror": true}`；这个 owner 是
  本频道的 owner 或 admin（跑同步的那个人）——`parse_trusted_mirrors`；
- `feishu-author` 等于 agent 的 owner；打在申请消息上；带 join 标签时必须等于这个申请；期限与验签同 owner 自己签的信号。

已接受的风险（ADR-0020）：跑同步的那台机器（频道 owner/admin 的）技术上能替 agent owner 伪造一条同意。

沿用 test_buzz_agent_join_requests.py 的 FakeRelay / JoinTestCase。
"""

from __future__ import annotations

import base64
import hashlib
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_buzz_agent_join_requests as base  # noqa: E402
from test_buzz_agent_join_requests import (  # noqa: E402
    ADMIN, AGENT, AGENT_KEY, DAY, MEMBER, NEW_CH, OWNER, FakeBuzz, JoinTestCase, join, sync,
)

FGS = join.fgs
MIRROR_KEY = "0e" * 32
MIRROR = sync.publisher_pubkey_from_private_key(MIRROR_KEY)  # 跑群同步的那台机器的镜像身份（频道里 role=bot）
MIRROR_OWNER_KEY = "0f" * 32
MIRROR_OWNER = sync.publisher_pubkey_from_private_key(MIRROR_OWNER_KEY)
UNTRUSTED_OWNER_KEY = "10" * 32
UNTRUSTED_OWNER = sync.publisher_pubkey_from_private_key(UNTRUSTED_OWNER_KEY)
OUTSIDER_OWNER_KEY = "11" * 32
OUTSIDER_OWNER = sync.publisher_pubkey_from_private_key(OUTSIDER_OWNER_KEY)
ROGUE_MIRROR_KEY = "12" * 32
ROGUE_MIRROR = sync.publisher_pubkey_from_private_key(ROGUE_MIRROR_KEY)
EVENT_KEYS = {
    MIRROR: MIRROR_KEY,
    MIRROR_OWNER: MIRROR_OWNER_KEY,
    UNTRUSTED_OWNER: UNTRUSTED_OWNER_KEY,
    OUTSIDER_OWNER: OUTSIDER_OWNER_KEY,
    ROGUE_MIRROR: ROGUE_MIRROR_KEY,
}


def nostr_event(pubkey, kind, tags, content, created_at):
    ser = json.dumps([0, pubkey, created_at, kind, tags, content], separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.sha256(ser.encode()).digest()
    sig = sync.nk.schnorr_sign(digest, bytes.fromhex(EVENT_KEYS[pubkey]), bytes(32)).hex()
    return {"id": digest.hex(), "pubkey": pubkey, "created_at": created_at, "kind": kind,
            "tags": tags, "content": content, "sig": sig}


def mirror_events(mirror=MIRROR, owner=MIRROR_OWNER, declared=True, at=base.NOW - DAY):
    profile = nostr_event(mirror, 0, [["auth", owner, "", "ab" * 64]], json.dumps({"name": "feishu-mirror"}), at)
    content = {"name": "feishu-mirror", "parallelism": 1, "respond_to": "owner-only"}
    if declared:
        content["feishu"] = {"mirror": True}
    return [profile, nostr_event(owner, 30177, [["d", mirror]], json.dumps(content), at + 1)]


class MirrorAwareBuzz(FakeBuzz):
    """FakeBuzz plus the relay's kind 0 / 30177 events, read through the real trust rule."""

    def for_channel(self, ch):
        return MirrorAwareBuzz(self.relay, self.me, ch)

    def trusted_mirrors(self, candidates, roles):
        self.relay.mirror_queries = getattr(self.relay, "mirror_queries", 0) + 1
        if getattr(self.relay, "mirror_directory_unavailable", False):
            return None
        return FGS.parse_trusted_mirrors(getattr(self.relay, "profile_events", []), candidates, roles)


# ================================ L1 : 可信镜像 ================================


class TrustedMirrors(unittest.TestCase):
    ROLES = {OWNER: "member", ADMIN: "owner", MEMBER: "member", MIRROR_OWNER: "owner", MIRROR: "bot", AGENT: "bot"}

    def test_a_bot_member_declared_a_mirror_by_a_channel_admin_is_trusted(self):
        """L1-JOIN-F01: 频道 bot 成员 + 30177 由它的 NIP-OA owner 签、声明 feishu.mirror + 这个 owner 是频道 owner/admin → 可信。"""
        self.assertEqual(FGS.parse_trusted_mirrors(mirror_events(), {MIRROR}, self.ROLES), {MIRROR})
        self.assertEqual(FGS.parse_trusted_mirrors(mirror_events(), {MIRROR},
                                                   dict(self.ROLES, **{MIRROR_OWNER: "admin"})), {MIRROR})

    def test_anything_less_is_not(self):
        """L1-JOIN-F02: owner 只是普通成员 / 不在频道；没声明 mirror；不是 bot 成员；30177 不是它的 NIP-OA owner 签的——都不可信。"""
        cases = {
            "owner is a member": (mirror_events(owner=UNTRUSTED_OWNER),
                                  dict(self.ROLES, **{UNTRUSTED_OWNER: "member"})),
            "owner not in channel": (mirror_events(owner=OUTSIDER_OWNER), self.ROLES),
            "not declared": (mirror_events(declared=False), self.ROLES),
            "not a bot member": (mirror_events(), dict(self.ROLES, **{MIRROR: "member"})),
            "someone else signed": ([mirror_events()[0], mirror_events(owner=UNTRUSTED_OWNER)[1]], self.ROLES),
        }
        for name, (events, roles) in cases.items():
            with self.subTest(name):
                self.assertEqual(FGS.parse_trusted_mirrors(events, {MIRROR}, roles), set())


# ================================ L1 : 成员事件顺序 ================================


class OrderedMembershipEvents(JoinTestCase):
    def test_same_second_group_sync_events_reopen_from_the_persisted_sequence(self):
        """L1-JOIN-F03: 群同步在同一秒发 add -> remove -> add 时，真实签名事件里的持久序号而不是随机 event id
        决定先后；已经把该秒记作 invite_at 的 NO_INVITE 也必须看见同秒的后续 add，并发起新的申请。"""
        self.baseline()
        self.relay.channel(NEW_CH, "群", {MIRROR_OWNER: "admin", OWNER: "member", AGENT: "bot"})
        at = int(self.clock.now)

        def membership(kind, sequence, nonce):
            tags = [["h", NEW_CH], ["p", AGENT]] + ([["role", "bot"]] if kind == 9000 else [])
            tags += [["feishu-member-op", nonce * 32], ["feishu-member-stream", "02" * 32],
                     ["feishu-member-seq", str(sequence)]]
            return FGS.sign_event(MIRROR_OWNER_KEY, kind, tags, "", at)

        add, remove, readd = (membership(9000, 1, "01"), membership(9001, 2, "02"),
                              membership(9000, 3, "03"))
        self.assertEqual(max((add, remove, readd), key=lambda event: event["id"]), remove)  # old id ordering is wrong
        real_authentic = join.authentic
        join.authentic = FGS._nip01_event_verified
        self.addCleanup(lambda: setattr(join, "authentic", real_authentic))

        self.relay.channels[NEW_CH]["events"] += [add, remove]
        self.run_once()
        self.assertEqual(self.record()["state"], "NO_INVITE")
        self.relay.channels[NEW_CH]["events"].append(readd)

        self.run_once()

        self.assertEqual(self.record()["state"], "REQUESTED")
        self.assertEqual(self.record()["invite_event"], readd["id"])

    def test_same_stream_sequence_survives_a_legitimate_signer_rotation(self):
        """L1-JOIN-F04: 成员序号属于同步 state，不属于 signer；owner/admin key 合法轮换后，同一 stream 的
        同秒较大序号仍能越过旧 signer 的 cursor，独立同步进程则不能只靠碰巧相同的序号串线。"""
        self.baseline()
        self.relay.channel(NEW_CH, "群", {MIRROR_OWNER: "admin", ROGUE_MIRROR: "admin",
                                           OWNER: "member", AGENT: "bot"})
        at = int(self.clock.now)
        stream = "cd" * 32
        remove = FGS.sign_event(MIRROR_OWNER_KEY, 9001,
                                [["h", NEW_CH], ["p", AGENT], ["feishu-member-op", "01" * 32],
                                 ["feishu-member-stream", stream], ["feishu-member-seq", "2"]], "", at)
        readd = FGS.sign_event(ROGUE_MIRROR_KEY, 9000,
                               [["h", NEW_CH], ["p", AGENT], ["role", "bot"],
                                ["feishu-member-op", "02" * 32], ["feishu-member-stream", stream],
                                ["feishu-member-seq", "3"]], "", at)
        real_authentic = join.authentic
        join.authentic = FGS._nip01_event_verified
        self.addCleanup(lambda: setattr(join, "authentic", real_authentic))

        self.relay.channels[NEW_CH]["events"].append(remove)
        self.run_once()
        self.assertEqual(self.record()["state"], "NO_INVITE")
        self.relay.channels[NEW_CH]["events"].append(readd)

        self.run_once()

        self.assertEqual(self.record()["state"], "REQUESTED")
        self.assertEqual(self.record()["invite_event"], readd["id"])

    def test_a_lower_seen_sequence_is_not_replayed_when_the_client_clock_moves_back(self):
        """L1-JOIN-F05: 同一 stream 里 seq=2 的 remove 即使 created_at 比 seq=1 的 add 小一秒也更新；下一轮
        不能因旧 add 的时间更大就把它当成未见事件，错误地从 NO_INVITE 重开申请。"""
        self.baseline()
        self.relay.channel(NEW_CH, "群", {MIRROR_OWNER: "admin", OWNER: "member", AGENT: "bot"})
        at = int(self.clock.now)
        stream = "de" * 32
        add = FGS.sign_event(MIRROR_OWNER_KEY, 9000,
                             [["h", NEW_CH], ["p", AGENT], ["role", "bot"],
                              ["feishu-member-op", "01" * 32], ["feishu-member-stream", stream],
                              ["feishu-member-seq", "1"]], "", at + 2)
        remove = FGS.sign_event(MIRROR_OWNER_KEY, 9001,
                                [["h", NEW_CH], ["p", AGENT], ["feishu-member-op", "02" * 32],
                                 ["feishu-member-stream", stream], ["feishu-member-seq", "2"]], "", at + 1)
        real_authentic = join.authentic
        join.authentic = FGS._nip01_event_verified
        self.addCleanup(lambda: setattr(join, "authentic", real_authentic))
        self.relay.channels[NEW_CH]["events"] += [add, remove]

        self.run_once()
        self.assertEqual(self.record()["state"], "NO_INVITE")
        self.run_once()

        self.assertEqual(self.record()["state"], "NO_INVITE")
        self.assertEqual(len([event for event in self.relay.sent
                              if "无法确认这次入群是谁发起的" in event["content"]]), 1)


# ================================ L2 : 入群申请 ================================


class FeishuApproval(JoinTestCase):
    def run_once(self, **kwargs):
        return join.run(self.config, state_dir=self.state_dir,
                        make_buzz=lambda agent: MirrorAwareBuzz(self.relay, agent.pubkey),
                        system=self.system, clock=self.clock, sleeper=self.clock.sleep, **kwargs)

    def requested(self, **kwargs):
        self.relay.profile_events = mirror_events()
        self.baseline()
        self.invite_by_admin(**kwargs)
        self.relay.channels[NEW_CH]["members"][MIRROR_OWNER] = "admin"
        self.relay.channels[NEW_CH]["members"][MIRROR] = "bot"
        self.run_once()
        self.clock.sleep(30)
        return self.request_event()

    def test_a_trusted_group_mirror_invite_becomes_an_owner_approval_request(self):
        """L2-JOIN-F06: 飞书群把 Agent 加进 Buzz 时，9000 由可信镜像签名；镜像 owner 是频道 owner/admin，
        所以这次邀请等价于管理员发起申请，必须给 Agent owner 发待审批消息，不能当普通 bot 邀请静默退群。"""
        self.relay.profile_events = mirror_events()
        self.baseline()
        self.relay.channel(NEW_CH, "飞书测试群",
                           {MIRROR_OWNER: "admin", MIRROR: "bot", OWNER: "member"})
        self.clock.sleep(10)
        self.relay.invite(NEW_CH, MIRROR, AGENT)

        result = self.run_once()

        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(self.record()["state"], "REQUESTED")
        request = self.request_event()
        self.assertIn("需要 owner 同意", request["content"])
        self.assertIn(OWNER, [tag[1] for tag in request["tags"] if tag[:1] == ["p"]])

    def test_an_untrusted_bot_invite_is_still_refused_with_a_reason(self):
        """L2-JOIN-F07: 未声明 mirror，或其 owner 不是频道 owner/admin 的 bot 不能借飞书路径扩大权限；
        它的邀请仍明确说明只有管理员邀请才会送审，然后退出。"""
        self.relay.profile_events = mirror_events(owner=UNTRUSTED_OWNER)
        self.baseline()
        self.relay.channel(NEW_CH, "飞书测试群",
                           {ADMIN: "owner", UNTRUSTED_OWNER: "member", MIRROR: "bot", OWNER: "member"})
        self.clock.sleep(10)
        self.relay.invite(NEW_CH, MIRROR, AGENT)

        result = self.run_once()

        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(self.record()["outcome"], "declined_inviter")
        self.assertTrue(any("只有本群的管理员邀请我" in event["content"] for event in self.relay.sent))

    def test_a_temporarily_unavailable_mirror_directory_keeps_the_agent_and_explains_retry(self):
        """L2-JOIN-F08: 无法验证镜像与已验证不可信是两回事；临时故障不能谎报策略拒绝或退群。"""
        self.relay.profile_events = mirror_events()
        self.baseline()
        self.relay.channel(NEW_CH, "飞书测试群",
                           {MIRROR_OWNER: "admin", MIRROR: "bot", OWNER: "member"})
        self.clock.sleep(10)
        self.relay.invite(NEW_CH, MIRROR, AGENT)
        self.relay.mirror_directory_unavailable = True

        result = self.run_once()

        self.assertEqual(result["status"], "error", result)
        self.assertIn(AGENT, self.relay.channels[NEW_CH]["members"])
        self.assertEqual(self.record()["state"], "NO_INVITE")
        notice = next(event for event in self.relay.sent if event["channel"] == NEW_CH)
        self.assertIn("暂时无法验证飞书镜像身份", notice["content"])
        self.assertIn("下一轮自动重试", notice["content"])

        self.relay.mirror_directory_unavailable = False
        self.assertEqual(self.run_once()["status"], "ok")
        self.assertEqual(self.record()["state"], "REQUESTED")

    def mirror_react(self, request, emoji, author=OWNER, join_id=None, signer=MIRROR, sig="fake-ok"):
        tags = [["e", request["id"]], ["feishu-author", author]] + ([["join", join_id]] if join_id else [])
        event = self.relay._event(NEW_CH, 7, signer, emoji, tags)
        event["sig"] = sig
        return event

    def test_the_owners_check_in_feishu_approves_and_activates(self):
        """L2-JOIN-F10: 可信镜像在申请上打 ✅、feishu-author 是 agent 的 owner → 同意并开通（和 owner 自己在 Buzz 里点一样）。"""
        request = self.requested()
        self.mirror_react(request, "✅")
        result = self.run_once()
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual((self.record()["outcome"], self.record()["state"]), ("approved", "ACTIVE"))
        self.assertEqual(self.env_channels()[-1], NEW_CH)

    def mirror_reply(self, request, text, author=OWNER, join_id=None, signer=MIRROR):
        tags = [["h", NEW_CH], ["e", request["id"], "", "reply"], ["feishu-author", author]] + ([["join", join_id]] if join_id else [])
        return self.relay._event(NEW_CH, 9, signer, text, tags)

    def test_an_approve_reply_in_feishu_comes_as_a_tagged_mirror_reply(self):
        """L2-JOIN-F11: 飞书里回复 `/approve JOIN-<id>`，群同步由可信镜像发成带 feishu-author 与 join 标签的话题回复（「[飞书] 名字：」署名）：
        feishu-author 是 owner、join 与首行命令都等于这个申请才算；别的 id、不是 owner、没有 join 标签都不算。"""
        request = self.requested()
        join_id = self.record()["join_id"]
        self.mirror_reply(request, "[飞书] 陈老板：/approve JOIN-deadbeef", join_id="JOIN-deadbeef")
        self.mirror_reply(request, f"[飞书] 成员乙：/approve {join_id}", author=MEMBER, join_id=join_id)
        self.mirror_reply(request, f"[飞书] 陈老板：/approve {join_id}")
        self.run_once()
        self.assertEqual(self.record()["state"], "REQUESTED")
        self.mirror_reply(request, f"[飞书] 陈老板：/approve {join_id}", join_id=join_id)
        self.run_once()
        self.assertEqual(self.record()["outcome"], "approved")

    def test_a_cross_denies(self):
        """L2-JOIN-F12: ❌ → 不同意，agent 退群。"""
        request = self.requested()
        self.mirror_react(request, "❌")
        self.run_once()
        self.assertEqual(self.record()["outcome"], "denied")

    def test_only_the_owners_word_through_a_trusted_mirror_counts(self):
        """L2-JOIN-F13: feishu-author 不是 owner；签名的不是可信镜像（普通成员、声明了 mirror 但 owner 不是频道 admin 的 bot）；签名伪造；
        打在别的消息上——都不算。"""
        request = self.requested()
        rogue = ROGUE_MIRROR
        self.relay.channels[NEW_CH]["members"][rogue] = "bot"
        self.relay.channels[NEW_CH]["members"][UNTRUSTED_OWNER] = "member"
        self.relay.profile_events += mirror_events(mirror=rogue, owner=UNTRUSTED_OWNER)
        other = self.relay.top(NEW_CH, MEMBER, "hello")
        self.mirror_react(request, "✅", author=MEMBER)
        self.mirror_react(request, "✅", signer=MEMBER)
        self.mirror_react(request, "✅", signer=rogue)
        self.mirror_react(request, "✅", sig="forged")
        self.relay._event(NEW_CH, 7, MIRROR, "✅", [["e", other["id"]], ["feishu-author", OWNER]])
        self.run_once()
        self.assertEqual(self.record()["state"], "REQUESTED")
        self.assertEqual(self.system.restarts, [])

    def test_it_can_be_turned_off(self):
        """L2-JOIN-F14: `accept_feishu_approvals: false`：只认 owner 在 Buzz 里自己签的，镜像转来的一律不算（也不去查镜像）。"""
        self.config["accept_feishu_approvals"] = False
        request = self.requested()
        self.mirror_react(request, "✅")
        self.run_once()
        self.assertEqual(self.record()["state"], "REQUESTED")
        self.assertEqual(getattr(self.relay, "mirror_queries", 0), 0)

    def test_the_deadline_applies(self):
        """L2-JOIN-F15: 过了期限才打的 ✅ 不算（期限与 owner 自己的信号相同）。"""
        request = self.requested()
        self.clock.sleep(8 * DAY)
        self.mirror_react(request, "✅")
        self.run_once()
        self.assertNotEqual(self.record()["outcome"], "approved")

    def test_the_key_must_be_a_boolean(self):
        """L2-JOIN-F16: accept_feishu_approvals 只能是布尔值。"""
        for bad in ("yes", 1, None):
            with self.subTest(bad=bad):
                with self.assertRaises(sync.SyncError):
                    join.validate_config(dict(self.config, accept_feishu_approvals=bad))


# ================================ adapter ================================


class TrustedMirrorsAdapter(unittest.TestCase):
    def test_it_asks_the_relay_as_the_agent_with_its_auth_tag(self):
        """L2-JOIN-F20: AgentBuzz.trusted_mirrors 用 agent 自己的 key 签一次 relay 的 POST /query（带 x-auth-tag），问候选者的 kind 0 与
        kind 30177，按 parse_trusted_mirrors 判定；查询失败就当没有可信镜像（不影响 owner 自己签的信号）；候选里没有频道 bot 成员就不查。"""
        seen = []

        def http(url, headers, timeout, body=None):
            seen.append({"url": url, "headers": dict(headers), "body": json.loads(body)})
            return 200, json.dumps(mirror_events()).encode()
        env = {"BUZZ_RELAY_URL": "https://relay.example.test", "BUZZ_PRIVATE_KEY": AGENT_KEY,
               "BUZZ_AUTH_TAG": '["auth","x","","y"]'}
        buzz = join.AgentBuzz("/opt/buzz-0.5.23/usr/bin/buzz", env, http=http)
        roles = {MIRROR_OWNER: "owner", MIRROR: "bot"}
        self.assertEqual(buzz.trusted_mirrors({MIRROR}, roles), {MIRROR})
        (req,) = seen
        self.assertEqual(req["url"], "https://relay.example.test/query")
        self.assertEqual(req["headers"]["x-auth-tag"], '["auth","x","","y"]')
        head = json.loads(base64.b64decode(req["headers"]["Authorization"][len("Nostr "):]))
        self.assertEqual(head["pubkey"], AGENT)
        self.assertEqual(req["body"], FGS.directory_filters({MIRROR}))
        failing = join.AgentBuzz("/opt/buzz-0.5.23/usr/bin/buzz", env, http=lambda *a, **k: (500, b"x"))
        self.assertIsNone(failing.trusted_mirrors({MIRROR}, roles))
        seen.clear()
        self.assertEqual(buzz.trusted_mirrors({MIRROR, ADMIN}, {ADMIN: "owner", MIRROR: "member"}), set())
        self.assertEqual(seen, [])  # nobody is a bot member: nothing to ask the relay

    def test_it_rejects_any_tampering_in_a_profile_or_policy_event(self):
        """L2-JOIN-F21: relay HTTP 200 不是信任边界；kind 0/30177 的 id、sig、tags 或 content 被改后，
        AgentBuzz 必须在 parse_trusted_mirrors 之前丢掉它，不能让伪造的镜像代 owner 批准入群。"""
        env = {"BUZZ_RELAY_URL": "https://relay.example.test", "BUZZ_PRIVATE_KEY": AGENT_KEY}
        roles = {MIRROR_OWNER: "owner", MIRROR: "bot"}
        good = mirror_events()
        for index, event_name in enumerate(("profile", "policy")):
            for field, value in (
                    ("id", "00" * 32),
                    ("sig", "00" * 64),
                    ("tags", [*good[index]["tags"], ["x", "tampered"]]),
                    ("content", json.dumps({**json.loads(good[index]["content"]), "tampered": True}))):
                with self.subTest(event=event_name, field=field):
                    events = [dict(row) for row in good]
                    events[index][field] = value
                    buzz = join.AgentBuzz("/opt/buzz-0.5.23/usr/bin/buzz", env,
                                          http=lambda *a, answer=events, **k: (200, json.dumps(answer).encode()))
                    self.assertEqual(buzz.trusted_mirrors({MIRROR}, roles), set())


if __name__ == "__main__":
    unittest.main()
