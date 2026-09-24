"""agent 的飞书应用公开在 kind:30177，群同步从 relay 读（ADR-0019，engineering/skills#147）。

背景：每个 Channel ↔ 飞书群的同步跑在某一个操作者的机器上，而别人 owner 的 agent 也会在这个频道里。操作者没有它的凭据
（SKILL.md Rule 11），以前也不知道它的飞书 `app_id`：它的 bot 进不了群，飞书里 @ 它只是文字，Buzz 侧没有 p tag，唤醒不了它。

ADR-0019：agent 的 owner 把 `"feishu": {"app_id": "cli_…"}` 写进该 agent 的 kind:30177（relay 上全员可读、owner 签名）。群同步每轮只在
频道里有「本机没配置的 bot 成员」时，用 signer key 签一次 relay 的 `POST /query`，取这些 agent 的 kind 0 与 kind 30177：

- 完整性与 owner 校验：每条事件的 NIP-01 id / sig 必须通过；30177 的作者必须等于该 agent 最新 kind 0 里 `auth` 标签声明的 owner；
  取最新一条（同一时间取 id 最小的），
  最新那条没有 app_id 就是没有，不回退到更早的；app_id 格式不合法、两个 agent 声称同一个、与本机配置的 agent 或 owner 应用撞了，都不用。
- 读不到（网络、非 200、不是 JSON 数组）：本轮不用目录，`directory_failed` 加一，不影响退出码，整轮照常。
- 目录里的 agent：bot 按 app_id 拉进群；飞书里 @ 它变成 p tag；人在 Buzz 里 @ 它渲染成 `<at>`；它自己的消息仍由镜像代发（缺省 relay）；
  只有 `reaction_sync="agents_only"` 时 reaction 仍按旧策略跳过。

同时 `buzz_unmanaged_agents` 缺省值改成 "relay"。

沿用 test_buzz_feishu_group_sync.py 的 FakeWorld / Env / 常量。用例编号从 740 起。
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
    AGENT2_KEY, AGENT2_PK, AGENT_APP, AGENT_BOT_MEMBER, AGENT_PK, ALICE_OPEN, ALICE_PK, BOB_PK, FGS, MEDIA_ORIGIN, MIRROR_PK,
    NOW, OWNER_APP, OWNER_KEY, OWNER_PK, T0, TmpCase, Env, FakeWorld, event, eid, fmsg, text_of, ts,
)


def setUpModule():
    base.setUpModule()


def tearDownModule():
    base.tearDownModule()


FOREIGN_OWNER_KEY = "c3" * 32
FOREIGN_OWNER_PK = FGS.sync.nk.pubkey_xonly(bytes.fromhex(FOREIGN_OWNER_KEY)).hex()
MALLORY_KEY = "c6" * 32
MALLORY_PK = FGS.sync.nk.pubkey_xonly(bytes.fromhex(MALLORY_KEY)).hex()  # 另一个 owner：想冒认别人的 app_id
AGENT3_KEY = "c7" * 32
AGENT3_PK = FGS.sync.nk.pubkey_xonly(bytes.fromhex(AGENT3_KEY)).hex()  # 频道里的第三个外来 agent
AGENT2_APP = "cli_agent00000000002"
AGENT2_BOT_MEMBER = "ou_agent2bot00000000000000000001"
AGENT3_APP = "cli_agent00000000003"
QUERY_URL = f"{MEDIA_ORIGIN}/query"


EVENT_KEYS = {FOREIGN_OWNER_PK: FOREIGN_OWNER_KEY, AGENT2_PK: AGENT2_KEY, MALLORY_PK: MALLORY_KEY,
              AGENT3_PK: AGENT3_KEY}


def signed_identity(secret_key):
    """注册一个测试身份，让目录夹具能生成真实签名。"""
    pubkey = FGS.sync.nk.pubkey_xonly(bytes.fromhex(secret_key)).hex()
    EVENT_KEYS[pubkey] = secret_key
    return pubkey


def nostr_event(pubkey, kind, tags, content, created_at):
    """一条真实 NIP-01 事件：消费者不能把 HTTP 200 当成对 id / sig 的信任边界。"""
    ser = json.dumps([0, pubkey, created_at, kind, tags, content], separators=(",", ":"), ensure_ascii=False)
    event_id = hashlib.sha256(ser.encode()).hexdigest()
    sig = FGS.sync.nk.schnorr_sign(bytes.fromhex(event_id), bytes.fromhex(EVENT_KEYS[pubkey]), bytes(32)).hex()
    return {"id": event_id, "pubkey": pubkey, "created_at": created_at, "kind": kind,
            "tags": tags, "content": content, "sig": sig}


def profile(agent, owner, created_at=T0 - 1000, name="agent"):
    return nostr_event(agent, 0, [["auth", owner, "", "ab" * 64]], json.dumps({"name": name}), created_at)


def public_profile(agent, owner, *, name="agent", about="公开介绍", created_at=T0 - 1000):
    return nostr_event(agent, 0, [["auth", owner, "", "ab" * 64]],
                       json.dumps({"display_name": name, "about": about}, ensure_ascii=False), created_at)


def policy(owner, agent, app_id=None, created_at=T0 - 900, extra=None):
    content = {"name": "agent", "parallelism": 1, "respond_to": "anyone"}
    if app_id is not None:
        content["feishu"] = {"app_id": app_id}
    content.update(extra or {})
    return nostr_event(owner, 30177, [["d", agent]], json.dumps(content), created_at)


def published(agent=AGENT2_PK, owner=FOREIGN_OWNER_PK, app_id=AGENT2_APP):
    return [profile(agent, owner), policy(owner, agent, app_id)]


# ================================ L1 : 缺省 relay ================================


class DefaultIsRelay(TmpCase):
    def test_the_default_is_relay_and_skip_is_kept_when_written(self):
        """L1-FGS-740: `buzz_unmanaged_agents` 缺省是 "relay"（没有这个键就代发）；显式写 "skip" 的配置照旧是 "skip"。"""
        self.assertEqual(FGS.DEFAULT_BUZZ_UNMANAGED_AGENTS, "relay")
        raw = json.loads(Env(self.tmp).config.read_text())
        self.assertNotIn("buzz_unmanaged_agents", raw)
        cfg = FGS.load_config(base.write_owner_only(self.tmp / "none.json", json.dumps(raw)))
        self.assertEqual(FGS.buzz_unmanaged_agent_mode(cfg), "relay")
        skip = FGS.load_config(base.write_owner_only(self.tmp / "skip.json", json.dumps(dict(raw, buzz_unmanaged_agents="skip"))))
        self.assertEqual(FGS.buzz_unmanaged_agent_mode(skip), "skip")


# ================================ L1 : 签名与请求 ================================


class RelayRequest(unittest.TestCase):
    def decode(self, header):
        return json.loads(base64.b64decode(header[len("Nostr "):]))

    def test_a_post_signature_names_the_body_and_carries_a_nonce(self):
        """L1-FGS-741: 带 body 的签名头多两个标签：`payload`（body 的 sha256，relay 会核对）和 `nonce`（同一秒、同一 body 的两次请求
        事件 id 也不同，relay 的防重放不会拒掉第二次）。method 是 POST。"""
        body = b'[{"kinds":[0]}]'
        with mock.patch.object(FGS.secrets, "token_bytes", os.urandom):  # setUpModule pins it to zeros for the signing memo
            ev1 = self.decode(FGS.nip98_header(OWNER_KEY, "POST", QUERY_URL, NOW, body=body))
            ev2 = self.decode(FGS.nip98_header(OWNER_KEY, "POST", QUERY_URL, NOW, body=body))
        tags = {t[0]: t[1] for t in ev1["tags"]}
        self.assertEqual(tags["u"], QUERY_URL)
        self.assertEqual(tags["method"], "POST")
        self.assertEqual(tags["payload"], hashlib.sha256(body).hexdigest())
        self.assertRegex(tags["nonce"], r"^[0-9a-f]{32}$")
        self.assertNotEqual(ev1["id"], ev2["id"])
        self.assertEqual(ev1["pubkey"], OWNER_PK)

    def test_a_get_signature_is_unchanged(self):
        """L1-FGS-742: 不带 body 的签名头（people 接口的 GET）与以前完全一样：只有 u 和 method，bridge 的严格验签器不认别的标签。"""
        ev = self.decode(FGS.nip98_header(OWNER_KEY, "GET", "https://bridge.example.test/x", NOW, aux=b"\x00" * 32))
        self.assertEqual([t[0] for t in ev["tags"]], ["u", "method"])

    def test_the_query_url_comes_from_the_relay_url(self):
        """L1-FGS-743: /query 的地址来自镜像 env 的 BUZZ_RELAY_URL（结尾斜杠去掉）；不是 http(s) 的地址拒绝（wss://、空串、别的协议）。"""
        self.assertEqual(FGS.relay_query_url("https://buzz-sg.addx.live"), "https://buzz-sg.addx.live/query")
        self.assertEqual(FGS.relay_query_url("https://buzz-sg.addx.live/"), "https://buzz-sg.addx.live/query")
        for bad in ("wss://buzz-sg.addx.live", "", "ftp://x", "buzz-sg.addx.live"):
            with self.subTest(bad=bad):
                with self.assertRaises(FGS.GroupSyncError):
                    FGS.relay_query_url(bad)

    def test_the_filters_ask_for_profiles_and_policies_of_exactly_these_agents(self):
        """L1-FGS-744: 一次请求两个 filter：这些 agent 的 kind 0（按作者）与 kind 30177（按 d 标签），pubkey 排序、去重。"""
        self.assertEqual(FGS.directory_filters({AGENT3_PK, AGENT2_PK}),
                         [{"kinds": [0], "authors": sorted([AGENT2_PK, AGENT3_PK])},
                          {"kinds": [30177], "#d": sorted([AGENT2_PK, AGENT3_PK])}])


# ================================ L1 : 解析与 owner 校验（纯函数） ================================


class ParseDirectory(unittest.TestCase):
    def parse(self, events, candidates=frozenset({AGENT2_PK}), reserved=frozenset({AGENT_APP, OWNER_APP})):
        return FGS.parse_agent_directory(events, candidates, reserved)

    def test_an_owner_signed_policy_gives_the_app_id(self):
        """L1-FGS-745: 30177 的作者等于该 agent 的 kind 0 里 auth 标签声明的 owner，content 的 feishu.app_id 就是它的飞书应用。"""
        self.assertEqual(self.parse(published()), FGS.DirectoryAnswer(apps={AGENT2_PK: AGENT2_APP}, conflicts=0))

    def test_only_the_declared_owner_counts(self):
        """L1-FGS-746: 别人签的 30177（d 同样指向这个 agent）不算；没有 kind 0、kind 0 没有 auth 标签或 owner 不是 64 位 hex，都查不到。"""
        forged = [profile(AGENT2_PK, FOREIGN_OWNER_PK), policy(MALLORY_PK, AGENT2_PK, AGENT2_APP, created_at=T0)]
        self.assertEqual(self.parse(forged).apps, {})
        self.assertEqual(self.parse([policy(FOREIGN_OWNER_PK, AGENT2_PK, AGENT2_APP)]).apps, {})
        no_auth = nostr_event(AGENT2_PK, 0, [], "{}", T0 - 1000)
        self.assertEqual(self.parse([no_auth, policy(FOREIGN_OWNER_PK, AGENT2_PK, AGENT2_APP)]).apps, {})
        bad_owner = nostr_event(AGENT2_PK, 0, [["auth", "not-hex"]], "{}", T0 - 1000)
        self.assertEqual(self.parse([bad_owner, policy(FOREIGN_OWNER_PK, AGENT2_PK, AGENT2_APP)]).apps, {})

    def test_the_latest_profile_decides_the_owner(self):
        """L1-FGS-747: owner 看该 agent **最新**的 kind 0：换过 owner 以后，旧 owner 签的 30177 不再算数。"""
        events = [profile(AGENT2_PK, MALLORY_PK, created_at=T0 - 2000), profile(AGENT2_PK, FOREIGN_OWNER_PK, created_at=T0 - 100),
                  policy(MALLORY_PK, AGENT2_PK, AGENT3_APP, created_at=T0)]
        self.assertEqual(self.parse(events).apps, {})

    def test_the_latest_policy_wins_without_falling_back(self):
        """L1-FGS-748: owner 签的 30177 取最新一条；最新那条没有 feishu.app_id 就是没有（不回退到更早那条），同一时间取 id 最小的。"""
        old = policy(FOREIGN_OWNER_PK, AGENT2_PK, AGENT2_APP, created_at=T0 - 900)
        new_without = policy(FOREIGN_OWNER_PK, AGENT2_PK, None, created_at=T0 - 10)
        self.assertEqual(self.parse([profile(AGENT2_PK, FOREIGN_OWNER_PK), old, new_without]).apps, {})
        a = policy(FOREIGN_OWNER_PK, AGENT2_PK, AGENT2_APP, created_at=T0 - 5)
        b = policy(FOREIGN_OWNER_PK, AGENT2_PK, AGENT3_APP, created_at=T0 - 5)
        lowest = min((a, b), key=lambda e: e["id"])
        got = self.parse([profile(AGENT2_PK, FOREIGN_OWNER_PK), a, b]).apps
        self.assertEqual(got, {AGENT2_PK: json.loads(lowest["content"])["feishu"]["app_id"]})

    def test_a_malformed_claim_is_ignored(self):
        """L1-FGS-749: content 不是 JSON 对象、feishu 不是对象、app_id 不是字符串或不像 app_id，都当作没有（不让整轮失败）。"""
        owner_profile = profile(AGENT2_PK, FOREIGN_OWNER_PK)
        for content in ("not json", "[]", json.dumps({"feishu": "cli_agent00000000002"}),
                        json.dumps({"feishu": {"app_id": 7}}), json.dumps({"feishu": {"app_id": "ou_x"}}),
                        json.dumps({"feishu": {"app_id": "cli_bad id"}})):
            with self.subTest(content=content):
                bad = nostr_event(FOREIGN_OWNER_PK, 30177, [["d", AGENT2_PK]], content, T0 - 900)
                self.assertEqual(self.parse([owner_profile, bad]).apps, {})
        self.assertEqual(self.parse(["garbage", 7, {"kind": 30177}, *published()]).apps, {AGENT2_PK: AGENT2_APP})

    def test_one_app_claimed_twice_is_used_by_nobody(self):
        """L1-FGS-750: 两个 agent 声称同一个 app_id：谁都不用（不猜谁是真的），计一次冲突。"""
        events = [*published(), profile(AGENT3_PK, MALLORY_PK), policy(MALLORY_PK, AGENT3_PK, AGENT2_APP)]
        self.assertEqual(self.parse(events, candidates=frozenset({AGENT2_PK, AGENT3_PK})),
                         FGS.DirectoryAnswer(apps={}, conflicts=1))

    def test_a_claim_on_a_configured_app_is_ignored(self):
        """L1-FGS-751: 声称的 app_id 是本机配置的 agent 的、或操作者自己的 owner 应用：不用，计一次冲突（本机配置永远优先）。"""
        for app in (AGENT_APP, OWNER_APP):
            with self.subTest(app=app):
                self.assertEqual(self.parse(published(app_id=app)), FGS.DirectoryAnswer(apps={}, conflicts=1))

    def test_events_about_other_agents_are_ignored(self):
        """L1-FGS-752: 只看待查的 agent：d 指向别的 pubkey 的 30177、别的作者的 kind 0，一律不看。"""
        self.assertEqual(self.parse(published(agent=AGENT3_PK)).apps, {})

    def test_public_intro_uses_profile_about_and_owner_signed_response_policy_only(self):
        """L1-FGS-775: 入群介绍只取 agent 自己公开的 kind 0 名称/about 与 owner 签名的 30177 respond_to；
        即使 30177 里混有 instruction，也绝不能把内部提示词当公开介绍。"""
        events = [
            public_profile(AGENT2_PK, FOREIGN_OWNER_PK, name="skill-dev", about="维护公开 Skill；@我时请给报错。"),
            policy(FOREIGN_OWNER_PK, AGENT2_PK, AGENT2_APP,
                   extra={"respond_to": "allowlist", "instruction": "INTERNAL-PROMPT-MUST-NOT-LEAK"}),
        ]

        answer = self.parse(events)

        self.assertEqual(answer.introductions[AGENT2_PK], FGS.AgentIntroduction(
            name="skill-dev", description="维护公开 Skill；@我时请给报错。", respond_to="allowlist"))
        self.assertNotIn("INTERNAL-PROMPT-MUST-NOT-LEAK", repr(answer))

    def test_intro_response_policy_is_explained_in_user_language(self):
        """L1-FGS-776: 自我介绍不用 respond_to 内部枚举砸给用户；anyone/allowlist/owner-only/nobody 都说明谁能 @、
        受限时说明会收到原因，并告诉用户联系 owner 申请或让已授权成员代发。"""
        cases = {
            "anyone": ("群里的任何成员", "在群里 @我"),
            "allowlist": ("只回应已授权成员", "联系 Agent 所有者申请授权"),
            "owner-only": ("只回应我的所有者", "联系 Agent 所有者申请授权"),
            "nobody": ("不接受群内 @", "联系 Agent 所有者"),
        }
        for mode, needles in cases.items():
            with self.subTest(mode=mode):
                text = FGS.render_agent_introduction(FGS.AgentIntroduction("agent", "公开职责", mode))
                self.assertNotIn(mode, text)
                for needle in needles:
                    self.assertIn(needle, text)


# ================================ L1 : fetch_agent_directory ================================


class FetchDirectory(TmpCase):
    def cfg(self):
        return FGS.load_config(Env(self.tmp).config)

    def call(self, answer, candidates=frozenset({AGENT2_PK})):
        seen = []

        def http(url, headers, timeout, body=None):
            seen.append({"url": url, "headers": headers, "body": body})
            if isinstance(answer, Exception):
                raise answer
            return answer
        cfg = self.cfg()
        out = FGS.fetch_agent_directory(cfg, "https://relay.test", candidates, frozenset({AGENT_APP, OWNER_APP}), http, NOW)
        return out, seen

    def test_it_signs_one_post_with_the_signer_key(self):
        """L1-FGS-753: 一次 POST 到 {relay}/query，body 是 directory_filters，签名者是 signer key（people 接口那把，频道 owner/admin），
        不是镜像身份；应答解析成 DirectoryAnswer。"""
        out, seen = self.call((200, json.dumps(published()).encode()))
        self.assertEqual(out.apps, {AGENT2_PK: AGENT2_APP})
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0]["url"], "https://relay.test/query")
        self.assertEqual(json.loads(seen[0]["body"]), FGS.directory_filters({AGENT2_PK}))
        ev = json.loads(base64.b64decode(seen[0]["headers"]["Authorization"][len("Nostr "):]))
        self.assertEqual(ev["pubkey"], OWNER_PK)
        self.assertEqual(seen[0]["headers"].get("Content-Type"), "application/json")

    def test_a_failure_is_an_error_the_round_can_catch(self):
        """L1-FGS-754: 连不上、非 200、不是 JSON、不是数组：都抛 GroupSyncError（由 round 接住后降级），错误里不带应答内容。"""
        for answer in (OSError("down"), (500, b"boom-secret"), (403, b"[]"), (200, b"<html>"), (200, b'{"events":[]}')):
            with self.subTest(answer=repr(answer)[:30]):
                with self.assertRaises(FGS.GroupSyncError) as caught:
                    self.call(answer)
                self.assertNotIn("boom-secret", str(caught.exception))

    def test_only_nip01_verified_profiles_and_policies_can_choose_an_app(self):
        """L1-FGS-755: `/query` 是 HTTP 200 也不是信任边界；id/content/tags/sig 被改过的事件必须丢弃。
        伪造的新 head 也不能遮住旧的有效 head。"""
        good_profile, good_policy = published()
        corruptions = []
        for field, value in (("id", "00" * 32), ("sig", "00" * 64), ("content", json.dumps({"name": "tampered"})),
                             ("tags", [*good_profile["tags"], ["x", "tampered"]])):
            bad = dict(good_profile)
            bad[field] = value
            corruptions.append((field, bad))
        for field, value in (("id", "00" * 32), ("sig", "00" * 64),
                             ("content", json.dumps({"feishu": {"app_id": AGENT3_APP}})),
                             ("tags", [*good_policy["tags"], ["x", "tampered"]])):
            bad = dict(good_policy)
            bad[field] = value
            corruptions.append((field, bad))
        for field, bad in corruptions:
            with self.subTest(field=field, kind=bad["kind"]):
                events = [bad, good_policy] if bad["kind"] == 0 else [good_profile, bad]
                out, _ = self.call((200, json.dumps(events).encode()))
                self.assertEqual(out.apps, {})

        invalid_new = policy(FOREIGN_OWNER_PK, AGENT2_PK, None, created_at=T0 - 1)
        invalid_new["sig"] = "00" * 64
        out, _ = self.call((200, json.dumps([good_profile, good_policy, invalid_new]).encode()))
        self.assertEqual(out.apps, {AGENT2_PK: AGENT2_APP})


# ================================ L2-1 : 一整轮 ================================


class DirectoryRound(TmpCase):
    """频道里 AGENT2 是 bot 成员、本机没配置（别人的 agent）；它的 owner 在 30177 里公开了飞书 app_id。"""

    def world(self, publish=True):
        w = FakeWorld(self.tmp)
        w.bot_members[AGENT2_APP] = AGENT2_BOT_MEMBER
        if publish:
            w.relay_events = published()
        return w

    def test_the_agents_bot_is_pulled_into_the_group(self):
        """L2-1-FGS-760: 查到的 agent 的 bot 按 app_id 由 owner 的 user 身份拉进群；报告 directory_agents=1、directory_failed=0；
        relay 查询由 signer key 签名，只问本机没配置的那个 bot 成员。"""
        w = self.world()
        report = Env(self.tmp).round(w)
        self.assertIn(AGENT2_APP, w.bots)
        self.assertEqual(report["directory_agents"], 1)
        self.assertEqual(report["directory_failed"], 0)
        self.assertEqual(report["directory_conflicts"], 0)
        self.assertEqual(len(w.relay_queries), 1)
        self.assertEqual(w.relay_queries[0]["signer"], OWNER_PK)
        self.assertEqual(w.relay_queries[0]["filters"], FGS.directory_filters({AGENT2_PK}))

    def test_its_messages_are_relayed_by_default(self):
        """L2-1-FGS-761: 缺省（配置里没有 buzz_unmanaged_agents）它的发言由 owner 应用 bot 代发，署名「名字（Buzz·助手）」；它不在
        本机配置里，所以即使它的 bot 已在群里也不能用它的 bot 发。"""
        w = self.world()
        w.display[AGENT2_PK] = "nh-dev"
        w.events = [event(eid(1), AGENT2_PK, "已按 Issue 核对完毕")]
        report = Env(self.tmp).round(w)
        sends = [c for c in w.lark_sends() if "已按 Issue 核对完毕" in text_of(c)]
        self.assertEqual(len(sends), 1)
        self.assertEqual((sends[0]["app"], sends[0]["as"]), (AGENT_APP, "bot"))
        self.assertEqual(text_of(sends[0]), "nh-dev（Buzz·助手）：已按 Issue 核对完毕")
        self.assertEqual(report["relayed_agents"], 1)
        self.assertNotIn("agent_bot_unavailable", report["skipped"])

    def test_a_feishu_mention_of_its_bot_wakes_it(self):
        """L2-1-FGS-762: 飞书里 @ 它的 bot（群里真 mention）变成 Buzz 里对它 pubkey 的 p tag——这是以前做不到的（只能当文字）。"""
        w = self.world()
        w.bots[AGENT2_APP] = AGENT2_BOT_MEMBER
        w.messages = [fmsg("om_at2", ALICE_OPEN, "@nh-dev 帮我看下", mentions=[
            {"id": AGENT2_BOT_MEMBER, "key": "@_user_1", "name": "nh-dev"}])]
        Env(self.tmp).round(w)
        (mirrored,) = w.mirrored()
        self.assertEqual([t[1] for t in mirrored["tags"] if t[0] == "p"], [AGENT2_PK])

    def test_a_buzz_mention_of_it_becomes_an_at(self):
        """L2-1-FGS-763: 人在 Buzz 里 @ 它（p tag），飞书里渲染成对它 bot 的 `<at>`，和本机配置的 agent 一样。"""
        w = self.world()
        w.bots[AGENT2_APP] = AGENT2_BOT_MEMBER
        w.display[AGENT2_PK] = "nh-dev"
        w.events = [event(eid(1), BOB_PK, "@nh-dev 看下", tags=[("p", AGENT2_PK)])]
        Env(self.tmp).round(w)
        (send,) = [c for c in w.lark_sends() if "看下" in text_of(c)]
        self.assertEqual(text_of(send), "Bob（Buzz）：@nh-dev 看下")

    def test_a_card_names_its_bot(self):
        """L2-1-FGS-769: 卡片模式下人在 Buzz 里 @ 它，卡片点名它的 bot（`<at id=…>`，和本机配置的 agent 同一种写法）。"""
        w = self.world()
        w.bots[AGENT2_APP] = AGENT2_BOT_MEMBER
        w.display[AGENT2_PK] = "nh-dev"
        w.events = [event(eid(1), BOB_PK, "@nh-dev 看下", tags=[("p", AGENT2_PK)])]
        Env(self.tmp, message_format="card").round(w)
        (card,) = w.card_sends()
        self.assertNotIn("<at id=", json.dumps(card["card"], ensure_ascii=False))

    def test_a_failed_lookup_degrades_to_relay_and_needs_no_attention(self):
        """L2-1-FGS-764: relay 查询失败（连不上 / 500 / 429 / 不是 JSON / 不是数组 / 验签不过）：本轮不用目录，directory_failed=1，
        不影响退出码（needs_attention 为假）；它的 bot 不拉，它的发言照样代发，其余同步照常。"""
        for mode in ("network", "500", "429", "malformed", "not_list", "bad_signature"):
            with self.subTest(mode=mode):
                sub = self.tmp / mode
                sub.mkdir()
                w = FakeWorld(sub)
                w.bot_members[AGENT2_APP] = AGENT2_BOT_MEMBER
                w.relay_events = published()
                w.relay_fail = [mode]
                w.events = [event(eid(1), AGENT2_PK, "照样代发"), event(eid(2), ALICE_PK, "人的也照常")]
                report = Env(sub).round(w)
                self.assertEqual(report["directory_failed"], 1)
                self.assertEqual(report["directory_agents"], 0)
                self.assertNotIn(AGENT2_APP, w.bots)
                self.assertFalse(FGS.needs_attention(report))
                texts = [text_of(c) for c in w.lark_sends()]
                self.assertTrue(any("照样代发" in t for t in texts))
                self.assertTrue(any("人的也照常" in t for t in texts))

    def test_no_foreign_agent_no_query(self):
        """L2-1-FGS-765: 频道里的 bot 成员都在本机配置里（或只有镜像）时不查 relay：没有外来 agent 就没有这次请求。"""
        w = self.world()
        w.members = [m for m in w.members if m["pubkey"] != AGENT2_PK]
        report = Env(self.tmp).round(w)
        self.assertEqual(w.relay_queries, [])
        self.assertEqual((report["directory_agents"], report["directory_failed"]), (0, 0))

    def test_explicit_skip_still_skips_its_messages(self):
        """L2-1-FGS-766: 显式写 `buzz_unmanaged_agents: "skip"` 的配置：它的发言照旧丢弃（agent_bot_unavailable），目录只影响 bot 进群与 @。"""
        w = self.world()
        w.events = [event(eid(1), AGENT2_PK, "不代发")]
        report = Env(self.tmp, buzz_unmanaged_agents="skip").round(w)
        self.assertEqual(report["skipped"].get("agent_bot_unavailable"), 1)
        self.assertFalse(any("不代发" in text_of(c) for c in w.lark_sends()))
        self.assertIn(AGENT2_APP, w.bots)

    def test_its_reactions_are_still_skipped(self):
        """L2-1-FGS-767: `reaction_sync: "agents_only"` 时它的 reaction 仍然跳过（表情只能由 agent 自己的 bot 打，本机没有它的凭据）。
        缺省的双向（ADR-0020）由 owner bot 代打，见 test_buzz_feishu_group_sync_two_way_reactions.py。"""
        from test_buzz_feishu_group_sync import reaction_event
        w = self.world()
        w.events = [event(eid(1), ALICE_PK, "目标"), reaction_event(1, AGENT2_PK, eid(1), "👀", created_at=T0 + 5)]
        report = Env(self.tmp, reaction_sync="agents_only").round(w)
        self.assertEqual(report["reactions_added"], 0)
        self.assertEqual(w.reactions, [])

    def test_a_claim_on_the_configured_agents_app_changes_nothing(self):
        """L2-1-FGS-768: 外来 agent 声称本机配置的 agent 的 app_id：不用、计 directory_conflicts；本机 agent 的 bot 与 @ 不受影响。"""
        w = self.world(publish=False)
        w.relay_events = published(app_id=AGENT_APP)
        w.messages = [fmsg("om_at", ALICE_OPEN, "@helper-agent 看下", mentions=[
            {"id": AGENT_BOT_MEMBER, "key": "@_user_1", "name": "helper-agent"}])]
        report = Env(self.tmp).round(w)
        self.assertEqual(report["directory_conflicts"], 1)
        self.assertEqual(report["directory_agents"], 0)
        (mirrored,) = w.mirrored()
        self.assertEqual([t[1] for t in mirrored["tags"] if t[0] == "p"], [AGENT_PK])


# ================================ L1 : 文档契约 ================================


SKILL_DIR = Path(__file__).resolve().parent.parent
REPO = SKILL_DIR.parents[1]


def _read(path):
    return path.read_text(encoding="utf-8")


def _section(text, start, end):
    begin = text.index(start)
    return text[begin:text.index(end, begin + len(start))]


class DirectoryDocs(unittest.TestCase):
    DOC = SKILL_DIR / "references" / "feishu-group-sync.md"

    def test_the_directory_section_says_how_it_works_and_what_it_does_not_do(self):
        """L1-FGS-770: 「别人的 agent 的飞书应用（kind:30177 目录）」一节：谁公开、怎么公开（helper）、怎么读、验签与 owner 校验、失败降级、三个报告字段、
        已接受的风险与已知限制（离开频道后 bot 不会被移出）。"""
        section = _section(_read(self.DOC), "## 别人的 agent 的飞书应用（kind:30177 目录）", "## 换绑到另一个飞书群")
        for needle in ("ADR-0019", "buzz_agent_feishu_app.py", '"feishu": {"app_id": "cli_…"}', "POST {BUZZ_RELAY_URL}/query",
                       "`auth`", "`directory_agents`", "`directory_failed`", "`directory_conflicts`", "离开频道", "已接受的风险",
                       "`buzz_unmanaged_agents`"):
            with self.subTest(needle=needle):
                self.assertIn(needle, section)

    def test_publishing_is_a_step_after_the_app_is_made(self):
        """L1-FGS-771: 「agent 的飞书身份」里有一步：建好应用后由 owner 用 helper 公开 app_id（先 --dry-run），relay 上没有 30177 的不新建。"""
        section = _section(_read(self.DOC), "## agent 的飞书身份", "## 边界")
        for needle in ("buzz_agent_feishu_app.py", "--dry-run", "--owner-env", "kind:30177"):
            with self.subTest(needle=needle):
                self.assertIn(needle, section)

    def test_the_old_default_is_gone(self):
        """L1-FGS-772: 文档里不再说 `buzz_unmanaged_agents` 缺省是 "skip"。"""
        doc = _read(self.DOC)
        for stale in ('`"skip"`（缺省，与以前逐字节一致）或 `"relay"`', "存量绑定是否打开是各频道自己的风险决定"):
            with self.subTest(stale=stale):
                self.assertNotIn(stale, doc)
        self.assertIn('`"relay"`（缺省）或 `"skip"`', doc)

    def test_the_readme_and_the_skill_point_to_it(self):
        """L1-FGS-773: scripts/README.md 有 helper 一行（ADR-0019）；SKILL.md 任务表的飞书群一行提到 kind:30177 目录；ADR 索引有 0019。"""
        readme = _read(SKILL_DIR / "references" / "scripts" / "README.md")
        self.assertIn("buzz_agent_feishu_app.py", readme)
        self.assertIn("ADR-0019", readme)
        self.assertIn("kind:30177", _read(SKILL_DIR / "SKILL.md"))
        index = _read(REPO / "docs" / "05-adr" / "README.md")
        self.assertIn("(0019-publish-agent-feishu-app-ids-in-kind-30177-and-relay-by-default.md)", index)

    def test_the_two_way_guide_defines_the_public_one_time_intro_and_failure_ux(self):
        """L1-FGS-777: 双向指南把介绍的数据边界、响应范围人话、远端代发、一次性与失败恢复写成契约。"""
        doc = _read(SKILL_DIR / "references" / "feishu-two-way-sync.md")
        section = _section(doc, "### Agent 入群后的自我介绍", "### 兜底")
        for needle in ("kind:0", "`about`", "kind:30177", "`respond_to`", "instruction", "群里的任何成员",
                       "只回应已授权成员", "只回应我的所有者", "不接受群内 @", "公开资料代发", "只介绍一次",
                       "下一轮自动重试", "幂等键"):
            with self.subTest(needle=needle):
                self.assertIn(needle, section)


if __name__ == "__main__":
    unittest.main()
