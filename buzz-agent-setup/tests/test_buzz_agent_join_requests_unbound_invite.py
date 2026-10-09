"""agent 被拉进没有绑定 Buzz 频道的飞书群时主动说明（ADR-0023，engineering/skills#162 需求修订「未绑定飞书群入群即提示」）。

入群申请 timer（每 120 秒）另外为配置了 `feishu` 块（agent 自己的 lark-cli profile）的 agent 轮询它的 bot 所在的群：首轮只记基线；之后新出现的
群按 ADR-0022 的绑定认领判断——有有效认领 = 已绑定（不说话，沿用 ADR-0018 审批）；只有过期认领或读不到 = 暂时无法确认；没有 = 未绑定——
由 agent 自己的 bot 在群里说一次原因和群管理员下一步。每次入群最多两条（先「暂时无法确认」、后确认「未绑定」），p2p 不发，
pending → 幂等键发送 → 回读才算发出，结果不确定按同一个键在窗口内重试，确定被拒最多 3 次。

沿用 test_buzz_agent_join_requests.py 的 JoinTestCase / FakeBuzz。用例编号 L1-JOIN-U / L2-JOIN-U。
"""

from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_buzz_agent_join_requests as base  # noqa: E402
from test_buzz_agent_join_requests import NOW, JoinTestCase, join  # noqa: E402

FGS = join.fgs
CHAT = "oc_unbound000000000000000000000001"
BOUND_CHAT = "oc_bound00000000000000000000000001"
CHANNEL = "7d10832a-0d23-4486-82db-7aa51b97e5f4"
MIRROR = "e1" * 32


def ref_of(chat_id):
    return hashlib.sha256(("buzz-feishu-chat:v1:" + chat_id).encode()).hexdigest()


def claim(chat_id, heartbeat):
    return FGS.BindingClaim(mirror=MIRROR, owner="e2" * 32, channel=CHANNEL, chat_ref=ref_of(chat_id),
                            claimed_at=int(NOW) - 7200, heartbeat=int(heartbeat), name="江领的镜像")


def view(*claims):
    return FGS.ClaimView(tuple(claims), {}, {})


# ================================ L1 : 配置 ================================


class InviteConfig(JoinTestCase):
    def feishu(self, **overrides):
        block = {"app_id": "cli_nhdev000000000001", "lark_config_dir": str(self.tmp / "lark-cfg"),
                 "lark_data_dir": str(self.tmp / "lark-data")}
        block.update(overrides)
        return block

    def test_the_feishu_block_and_its_switches_are_accepted(self) -> None:
        """L1-JOIN-U01: agent 可选的 `feishu` 块恰好是 {app_id, lark_config_dir, lark_data_dir}；顶层 `lark_cli`（绝对路径、文件名 lark-cli）
        与 `feishu_unbound_prompt`（布尔，缺省开）。"""
        self.config["agents"][0]["feishu"] = self.feishu()
        self.config["lark_cli"] = "/opt/lark/bin/lark-cli"
        try:
            join.validate_config(self.config)
            join.validate_config(dict(self.config, feishu_unbound_prompt=False))
        except join.sync.SyncError as exc:
            self.fail(f"the documented shape must be accepted: {exc}")

    def test_bad_shapes_are_refused(self) -> None:
        """L1-JOIN-U02: 缺键 / 多键、app_id 不是 cli_…、相对路径、没有 lark_cli、lark_cli 不叫 lark-cli、两个 agent 共用一个 app 或 profile、
        开关不是布尔：整份配置拒绝。"""
        good = self.feishu()
        cases = {
            "missing key": ({k: v for k, v in good.items() if k != "lark_data_dir"}, {}),
            "extra key": (dict(good, token="x"), {}),
            "bad app": (dict(good, app_id="nhdev"), {}),
            "relative": (dict(good, lark_config_dir="lark-cfg"), {}),
            "no lark_cli": (good, {"lark_cli": None}),
            "wrong binary": (good, {"lark_cli": "/usr/bin/lark"}),
            "bad switch": (good, {"feishu_unbound_prompt": "yes"}),
        }
        for label, (block, top) in cases.items():
            with self.subTest(label=label):
                config = json.loads(json.dumps(self.config))
                config["agents"][0]["feishu"] = block
                config["lark_cli"] = "/opt/lark/bin/lark-cli"
                config.update(top)
                if config.get("lark_cli") is None:
                    config.pop("lark_cli")
                refused = False
                try:
                    join.validate_config(config)
                except join.sync.SyncError:
                    refused = True
                self.assertTrue(refused)
        config = json.loads(json.dumps(self.config))
        second = dict(config["agents"][0], name="bi-dev", env_file=str(self.tmp / "bi-dev.env"), unit="buzz-local-bi-dev.service")
        config["agents"][0]["feishu"] = good
        second["feishu"] = dict(good)
        config["agents"].append(second)
        config["lark_cli"] = "/opt/lark/bin/lark-cli"
        refused = False
        try:
            join.validate_config(config)
        except join.sync.SyncError:
            refused = True
        self.assertTrue(refused)


# ================================ L1 : 判断与文案 ================================


class InviteRules(unittest.TestCase):
    def test_the_claims_are_the_authority(self) -> None:
        """L1-JOIN-U03: 有这个群的有效认领 = bound；只有过期的 = unknown（同步停了心跳，不能断言未绑定）；别的群的认领不算，
        没有 = unbound；读不到（view 为 None）= unknown。"""
        now = NOW
        self.assertEqual(join.invite_binding_status(view(claim(CHAT, now - 60)), CHAT, now)[0], "bound")
        self.assertEqual(join.invite_binding_status(view(claim(CHAT, now - 1801), claim(CHAT, now - 10)), CHAT, now)[0], "bound")
        status, detail = join.invite_binding_status(view(claim(CHAT, now - 1801)), CHAT, now)
        self.assertEqual((status, detail), ("unknown", "stale"))
        self.assertEqual(join.invite_binding_status(view(claim(BOUND_CHAT, now - 60)), CHAT, now), ("unbound", ""))
        self.assertEqual(join.invite_binding_status(view(), CHAT, now), ("unbound", ""))
        self.assertEqual(join.invite_binding_status(None, CHAT, now), ("unknown", "unreadable"))

    def test_at_most_one_message_per_state_change(self) -> None:
        """L1-JOIN-U04: 还没说过：unbound → 说 unbound，unknown → 说 unknown，bound → 不说；说过 unknown：再 unknown 不说、确认 unbound
        说一次、bound 不说；说过 unbound 之后什么都不说（读不到认领也不改口）。"""
        table = {("", "unbound"): "unbound", ("", "unknown"): "unknown", ("", "bound"): None,
                 ("unknown", "unknown"): None, ("unknown", "unbound"): "unbound", ("unknown", "bound"): None,
                 ("unbound", "unbound"): None, ("unbound", "unknown"): None, ("unbound", "bound"): None}
        for (told, status), expected in table.items():
            with self.subTest(told=told, status=status):
                self.assertEqual(join.invite_next(told, status), expected)

    def test_the_messages_say_what_happened_why_and_what_to_do(self) -> None:
        """L1-JOIN-U05: 未绑定：收到邀请、暂不能处理群内任务、原因（没有和任何 Buzz 频道绑定）、群管理员下一步（请频道 owner/管理员绑定，
        绑定后在频道里发入群申请、owner 同意后才工作）；不含 @、不含 chat_id。暂时无法确认：说「暂时无法确认」和原因细节，绝不说
        「还没有绑定」或「已开通」。话题群 / 外部群的下一步是改用内部普通群。"""
        unbound = join.invite_message("unbound", "nh-dev")
        for needle in ("我是 nh-dev", "收到了进群邀请", "暂时还不能处理这个群里的任务", "原因：", "还没有和任何 Buzz 频道绑定",
                       "群管理员下一步：", "owner 或管理员", "入群申请", "owner 同意后"):
            with self.subTest(needle=needle):
                self.assertIn(needle, unbound)
        self.assertNotIn("<at", unbound)
        self.assertNotIn("oc_", unbound)
        for detail, needle in (("unreadable", "读取绑定信息失败"), ("stale", "超过 30 分钟没有更新")):
            unknown = join.invite_message("unknown", "nh-dev", detail)
            with self.subTest(detail=detail):
                self.assertIn("暂时无法确认", unknown)
                self.assertIn(needle, unknown)
                self.assertNotIn("还没有和任何 Buzz 频道绑定", unknown)
                self.assertNotIn("已开通", unknown)
                self.assertIn("群管理员下一步：", unknown)
        for kwargs, needle in (({"chat_mode": "topic"}, "话题群"), ({"external": True}, "外部群")):
            text = join.invite_message("unbound", "nh-dev", **kwargs)
            with self.subTest(kwargs=kwargs):
                self.assertIn(needle, text)
                self.assertIn("内部普通群", text)

    def test_missing_scopes_are_named_by_capability(self) -> None:
        """L1-JOIN-U06: 列群（im:chat:readonly 或 im:chat）、发消息（im:message:send_as_bot 或 im:message）、回读（im:message:readonly
        或 im:message）任一组缺失就按能力名报出。"""
        self.assertEqual(join.missing_invite_scopes({"im:chat:readonly", "im:message:send_as_bot", "im:message:readonly"}), [])
        self.assertEqual(join.missing_invite_scopes({"im:chat", "im:message"}), [])
        self.assertEqual(join.missing_invite_scopes({"im:message:send_as_bot"}), ["list", "read"])
        self.assertEqual(join.missing_invite_scopes(set()), ["list", "send", "read"])


# ================================ L2 : 入群申请 timer 里的一轮 ================================


APP = "cli_nhdev000000000001"
LARK = "/opt/lark/bin/lark-cli"


class FakeLark:
    """扮演 agent 自己的 lark-cli profile（--as bot）：auth status、scope、bot 所在的群（分页）、群详情、发消息（幂等键一小时去重）、
    按 id 读消息。每次调用记下 argv 与 env。"""

    def __init__(self, clock):
        self.clock = clock
        self.app = APP
        self.scopes = {"im:chat:readonly", "im:message:send_as_bot", "im:message:readonly"}
        self.chats = {}  # chat_id -> {"name", "chat_mode", "chat_status", "external"}
        self.page_size = 2
        self.list_fail = []  # modes popped per list call: permission | network
        self.detail_fail = []
        self.send_fail = []  # modes popped per send: refused | timeout | lost (sent, answer lost)
        self.read_fail = []  # modes popped per message read: network
        self.read_tamper = None
        self.sent = {}  # idempotency key -> message
        self.messages = {}  # message_id -> message
        self.calls = []
        self.n = 0

    def delivered(self, chat_id=None):
        return [m for m in self.messages.values() if chat_id is None or m["chat_id"] == chat_id]

    def __call__(self, argv, input=None, capture_output=True, text=True, timeout=None, check=False, env=None, cwd=None):
        import subprocess
        assert argv[0] == LARK, argv
        args = list(argv[1:])
        self.calls.append({"args": args, "env": dict(env or {})})

        def ok(data):
            return subprocess.CompletedProcess(argv, 0, json.dumps({"ok": True, "identity": "bot", "data": data}), "")

        def fail(kind, code=99991672):
            body = {"ok": False, "error": {"type": kind, "code": code, "subtype": kind}}
            return subprocess.CompletedProcess(argv, 1, json.dumps(body), "")

        if args[:2] == ["auth", "status"]:
            return subprocess.CompletedProcess(argv, 0, json.dumps({"appId": self.app, "identities": {}}), "")
        opt = lambda name: args[args.index(name) + 1] if name in args else None  # noqa: E731
        assert opt("--as") == "bot", args
        if args[:3] == ["api", "GET", "/open-apis/application/v6/scopes"]:
            return ok({"scopes": [{"scope_name": s, "scope_type": "tenant", "grant_status": 1} for s in sorted(self.scopes)]})
        if args[:3] == ["api", "GET", "/open-apis/im/v1/chats"]:
            mode = self.list_fail.pop(0) if self.list_fail else None
            if mode == "permission":
                return fail("permission")
            if mode == "network":
                raise subprocess.TimeoutExpired(argv, 90)
            params = json.loads(opt("--params") or "{}")
            ids = sorted(self.chats)
            start = int(params.get("page_token") or 0)
            page = ids[start:start + self.page_size]
            more = start + self.page_size < len(ids)
            items = [{"chat_id": c, "name": self.chats[c]["name"]} for c in page]
            if getattr(self, "duplicate_rows", False) and items:
                items.append(dict(items[0]))
            return ok({"items": items, "has_more": more, "page_token": str(start + self.page_size) if more else ""})
        if args[:2] == ["api", "GET"] and args[2].startswith("/open-apis/im/v1/chats/"):
            if self.detail_fail:
                self.detail_fail.pop(0)
                return fail("network", -1)
            chat = self.chats.get(args[2].rsplit("/", 1)[1])
            if chat is None:
                return fail("api", 232011)
            return ok({k: v for k, v in chat.items()})
        if args[:2] == ["im", "+messages-send"]:
            key, chat_id, body = opt("--idempotency-key"), opt("--chat-id"), opt("--text")
            mode = self.send_fail.pop(0) if self.send_fail else None
            if mode == "refused":
                return fail("api", 230002)
            if mode == "timeout":
                raise subprocess.TimeoutExpired(argv, 90)
            if key in self.sent and self.clock() - self.sent[key]["at"] < 3600:
                message = self.sent[key]
            else:
                self.n += 1
                message = {"message_id": f"om_invite{self.n:06d}", "chat_id": chat_id, "text": body, "at": self.clock(),
                           "app": self.app}
                self.sent[key] = message
                self.messages[message["message_id"]] = message
            if mode == "lost":
                raise subprocess.TimeoutExpired(argv, 90)
            return ok({"message_id": message["message_id"], "chat_id": chat_id})
        if args[:2] == ["api", "GET"] and args[2].startswith("/open-apis/im/v1/messages/"):
            if self.read_fail:
                self.read_fail.pop(0)
                return fail("network", -1)
            message = self.messages.get(args[2].rsplit("/", 1)[1])
            if message is None:
                return fail("api", 230011)
            item = {"message_id": message["message_id"], "chat_id": message["chat_id"], "msg_type": "text",
                    "sender": {"id": message["app"], "id_type": "app_id", "sender_type": "app"},
                    "body": {"content": json.dumps({"text": message["text"]}, ensure_ascii=False)}}
            if self.read_tamper:
                item = self.read_tamper(item)
            return ok({"items": [item]})
        raise AssertionError(f"unexpected lark-cli call {args}")


class ClaimsBuzz(base.FakeBuzz):
    """FakeBuzz 加上 ADR-0022 的认领读取：relay 上的认领，或读不到。"""

    def for_channel(self, ch):
        scoped = ClaimsBuzz(self.relay, self.me, ch)
        return scoped

    def binding_claims(self):
        self.relay.claim_reads = getattr(self.relay, "claim_reads", 0) + 1
        if getattr(self.relay, "claims_unreadable", False):
            raise join.sync.SyncError("relay query failed")
        return view(*getattr(self.relay, "claims", ()))


class InviteRun(JoinTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.lark = FakeLark(self.clock)
        for d in ("lark-cfg", "lark-data"):
            (self.tmp / d).mkdir(mode=0o700)
        self.config["lark_cli"] = LARK
        self.config["agents"][0]["feishu"] = {"app_id": APP, "lark_config_dir": str(self.tmp / "lark-cfg"),
                                              "lark_data_dir": str(self.tmp / "lark-data")}
        self.relay.claims = ()
        self.lark.chats["oc_old0000000000000000000000000001"] = {"name": "早就在的群", "chat_mode": "group",
                                                                "chat_status": "normal", "external": False}

    def go(self, minutes=2):
        self.clock.sleep(minutes * 60)
        return join.run(self.config, state_dir=self.state_dir, make_buzz=lambda agent: ClaimsBuzz(self.relay, agent.pubkey),
                        system=self.system, clock=self.clock, sleeper=self.clock.sleep,
                        make_lark=lambda block: join.FeishuInviteClient(LARK, block, base_env={"HOME": str(self.tmp),
                                                                                              "PATH": "/usr/bin"},
                                                                         runner=self.lark))

    def invite(self, chat_id=CHAT, **detail):
        self.lark.chats[chat_id] = {"name": "外面的群", "chat_mode": "group", "chat_status": "normal", "external": False,
                                    **detail}

    def feishu(self, result):
        return (result.get("agents", {}).get("nh-dev") or {}).get("feishu") or {}

    def invite_state(self):
        path = self.state_dir / join.FEISHU_STATE_FILE
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def capability(self):
        return ((self.invite_state().get("agents") or {}).get("nh-dev") or {}).get("capability")

    def one(self, chat_id):
        messages = self.lark.delivered(chat_id)
        self.assertEqual(len(messages), 1, messages)
        return messages[0]

    def settled(self):
        """首轮：只记基线。"""
        self.go()
        self.assertEqual(self.lark.delivered(), [])

    # -- discovery

    def test_a_new_unbound_group_is_told_once_without_a_mention(self) -> None:
        """L2-JOIN-U01: 首轮记基线（已经在的群不说话）；之后被拉进一个没有任何认领的群：agent 自己的 bot（--as bot，自己的 profile 目录）
        在群里发一次「未绑定」说明，不 @ 人；回读确认后才计 told_unbound；之后的轮次不再发。报告和输出里没有 chat_id。"""
        self.settled()
        self.invite()
        result = self.go()
        message = self.one(CHAT)
        self.assertEqual(message["text"], join.invite_message("unbound", "nh-dev"))
        self.assertEqual(self.feishu(result).get("told_unbound"), 1)
        sends = [c for c in self.lark.calls if c["args"][:2] == ["im", "+messages-send"]]
        self.assertEqual({c["env"].get("LARKSUITE_CLI_CONFIG_DIR") for c in sends}, {str(self.tmp / "lark-cfg")})
        self.assertTrue(any(c["args"][:2] == ["api", "GET"] and c["args"][2].startswith("/open-apis/im/v1/messages/")
                            for c in self.lark.calls))
        self.assertNotIn(CHAT, json.dumps(result))
        self.go()
        self.go()
        self.assertEqual(len(self.lark.delivered(CHAT)), 1)
        self.assertEqual(self.lark.delivered("oc_old0000000000000000000000000001"), [])

    def test_a_bound_group_is_left_to_the_channel_approval(self) -> None:
        """L2-JOIN-U02: 这个群有有效认领（别的机器在同步）：什么都不说（之后由频道里的入群申请走 ADR-0018），不再为它读认领。"""
        self.settled()
        self.relay.claims = (claim(CHAT, NOW),)
        self.invite()
        result = self.go()
        self.assertEqual(self.lark.delivered(), [])
        self.assertEqual(self.feishu(result).get("bound"), 1)
        reads = self.relay.claim_reads
        self.go()
        self.assertEqual(self.relay.claim_reads, reads)

    def test_unreadable_claims_say_cannot_confirm_then_the_verdict_once(self) -> None:
        """L2-JOIN-U03: 读不到认领：说一次「暂时无法确认」（绝不说未绑定）；下一轮还读不到：不再说；读到了且没有认领：再说一次「未绑定」；
        之后什么都不说。一次入群一共两条。"""
        self.settled()
        self.relay.claims_unreadable = True
        self.invite()
        self.go()
        self.go()
        first = self.one(CHAT)
        self.assertIn("暂时无法确认", first["text"])
        self.assertNotIn("还没有和任何 Buzz 频道绑定", first["text"])
        self.relay.claims_unreadable = False
        self.go()
        self.go()
        texts = [m["text"] for m in self.lark.delivered(CHAT)]
        self.assertEqual(len(texts), 2)
        self.assertIn("还没有和任何 Buzz 频道绑定", texts[1])

    def test_a_stale_claim_is_not_called_unbound(self) -> None:
        """L2-JOIN-U04: 只有过期的认领（那边的同步停了心跳）：说「暂时无法确认」并写明同步超过 30 分钟没有更新。"""
        self.settled()
        self.relay.claims = (claim(CHAT, NOW - 7200),)
        self.invite()
        self.go()
        message = self.one(CHAT)
        self.assertIn("暂时无法确认", message["text"])
        self.assertIn("超过 30 分钟没有更新", message["text"])

    def test_private_chats_and_dissolved_groups_are_never_told(self) -> None:
        """L2-JOIN-U05: 列表里出现 p2p（私聊）或状态不是 normal 的群：永不发送；话题群按话题群的下一步说明。"""
        self.settled()
        self.invite("oc_p2p00000000000000000000000000001", chat_mode="p2p")
        self.invite("oc_gone0000000000000000000000000001", chat_status="dissolved")
        self.invite("oc_topic000000000000000000000000001", chat_mode="topic")
        result = self.go()
        self.go()
        self.assertEqual(self.lark.delivered("oc_p2p00000000000000000000000000001"), [])
        self.assertEqual(self.lark.delivered("oc_gone0000000000000000000000000001"), [])
        topic = self.one("oc_topic000000000000000000000000001")
        self.assertIn("话题群", topic["text"])
        self.assertEqual(self.feishu(result).get("skipped_p2p"), 1)

    def test_the_first_run_is_a_baseline(self) -> None:
        """L2-JOIN-U06: 升级后第一轮：bot 已经在的群（包括未绑定的）全部记为基线，一条都不发；不读认领。"""
        self.invite()
        result = self.go()
        self.assertEqual(self.lark.delivered(), [])
        self.assertEqual(self.feishu(result).get("baseline"), 2)
        self.assertEqual(getattr(self.relay, "claim_reads", 0), 0)
        self.go()
        self.assertEqual(self.lark.delivered(), [])

    # -- identity and capability

    def test_the_profile_must_be_the_configured_app(self) -> None:
        """L2-JOIN-U07: profile 的 appId 不是配置的 app_id：不列群、不发送，能力记为 profile_mismatch，报告 error（退出码非零）。"""
        self.settled()
        self.lark.app = "cli_someoneelse0000001"
        self.invite()
        before = len(self.lark.calls)
        result = self.go()
        self.assertEqual(self.lark.delivered(), [])
        self.assertEqual(self.capability(), "profile_mismatch")
        self.assertEqual(result["status"], "error")
        self.assertFalse(any(c["args"][:3] == ["api", "GET", "/open-apis/im/v1/chats"] for c in self.lark.calls[before:]))

    def test_a_missing_scope_is_recorded_as_a_capability_gap(self) -> None:
        """L2-JOIN-U08: 应用没开通发消息的 scope：记为 scope_missing:send，本轮不发，报告 error；开通后（24 小时内重查）恢复为 ok。"""
        self.lark.scopes = {"im:chat:readonly", "im:message:readonly"}
        result = self.go()
        self.assertEqual(self.capability(), "scope_missing:send")
        self.assertEqual(result["status"], "error")
        self.lark.scopes.add("im:message:send_as_bot")
        self.go()
        self.assertEqual(self.capability(), "ok")

    def test_an_invite_waits_for_a_missing_scope_instead_of_failing(self) -> None:
        """L2-JOIN-U08b: 基线之后才发现缺发消息的 scope：不尝试发送、不把这次入群记成失败；scope 开通后（下一次重查）照常说一次。"""
        self.settled()
        self.lark.scopes = {"im:chat:readonly", "im:message:readonly"}
        state = self.invite_state()
        state["agents"]["nh-dev"]["scopes_checked_at"] = 0
        base.write_private(self.state_dir / join.FEISHU_STATE_FILE, json.dumps(state))
        self.invite()
        self.go()
        self.assertEqual([c for c in self.lark.calls if c["args"][:2] == ["im", "+messages-send"]], [])
        self.lark.scopes.add("im:message:send_as_bot")
        self.go()
        self.assertEqual(len(self.lark.delivered(CHAT)), 1)

    def test_a_refused_chat_list_is_a_capability_gap(self) -> None:
        """L2-JOIN-U09: 列群被拒（permission）：能力记为 list_refused，报告 error；网络失败只是本轮失败，能力不变。"""
        self.settled()
        self.lark.list_fail = ["network"]
        result = self.go()
        self.assertEqual(result["status"], "error")
        self.assertEqual(self.capability(), "ok")
        self.lark.list_fail = ["permission"]
        self.go()
        self.assertEqual(self.capability(), "list_refused")

    # -- delivery

    def test_an_uncertain_send_is_retried_with_the_same_key_after_a_restart(self) -> None:
        """L2-JOIN-U10: 发送结果不确定（请求到了、应答丢了）：pending 已落盘；下一轮（新进程）用同一个幂等键再发，飞书去重，群里只有一条；
        回读后才计为已说。"""
        self.settled()
        self.invite()
        self.lark.send_fail = ["lost"]
        first = self.go()
        self.assertNotEqual(self.feishu(first).get("told_unbound"), 1)
        pending = (((self.invite_state().get("agents") or {}).get("nh-dev") or {}).get("chats") or {}).get(CHAT, {}).get("pending") or {}
        self.assertTrue(pending.get("key"))
        second = self.go()
        self.assertEqual(len(self.lark.delivered(CHAT)), 1)
        self.assertEqual(self.feishu(second).get("told_unbound"), 1)
        keys = {c["args"][c["args"].index("--idempotency-key") + 1] for c in self.lark.calls
                if c["args"][:2] == ["im", "+messages-send"]}
        self.assertEqual(keys, {pending["key"]})

    def test_a_refused_send_is_retried_then_given_up_visibly(self) -> None:
        """L2-JOIN-U11: 确定被拒：下一轮重试；连续 3 次被拒记 failed（报告 error），之后不再发。"""
        self.settled()
        self.invite()
        self.lark.send_fail = ["refused", "refused", "refused", None]
        results = [self.go() for _ in range(4)]
        self.assertEqual(self.lark.delivered(CHAT), [])
        self.assertEqual(self.feishu(results[2]).get("failed"), 1)
        self.assertEqual(results[2]["status"], "error")
        attempts = [c for c in self.lark.calls if c["args"][:2] == ["im", "+messages-send"]]
        self.assertEqual(len(attempts), 3)

    def test_an_uncertain_send_that_never_resolves_becomes_unknown(self) -> None:
        """L2-JOIN-U12: 结果一直不确定超过 45 分钟：记 unknown（可能已送达），报告 error，永不再发。"""
        self.settled()
        self.invite()
        self.lark.send_fail = ["timeout"] * 30
        results = [self.go(minutes=10) for _ in range(6)]
        self.assertTrue(any(self.feishu(r).get("unknown") == 1 for r in results))
        before = len([c for c in self.lark.calls if c["args"][:2] == ["im", "+messages-send"]])
        self.go()
        after = len([c for c in self.lark.calls if c["args"][:2] == ["im", "+messages-send"]])
        self.assertEqual(before, after)

    def test_a_failed_readback_reads_again_and_never_resends(self) -> None:
        """L2-JOIN-U13: 发出后回读失败：下一轮只回读、不重发；回读到的不是这个群或不是本应用发的：记 failed，不计为已说。"""
        self.settled()
        self.invite()
        self.lark.read_fail = ["network"]
        self.go()
        result = self.go()
        self.assertEqual(len(self.lark.delivered(CHAT)), 1)
        self.assertEqual(len([c for c in self.lark.calls if c["args"][:2] == ["im", "+messages-send"]]), 1)
        self.assertEqual(self.feishu(result).get("told_unbound"), 1)
        other = "oc_other00000000000000000000000001"
        self.invite(other)
        self.lark.read_tamper = lambda item: dict(item, chat_id=CHAT)
        result = self.go()
        self.assertEqual(self.feishu(result).get("failed"), 1)
        self.assertNotEqual(self.feishu(result).get("told_unbound"), 1)

    # -- membership over time

    def test_a_group_missing_from_one_listing_is_not_a_new_invite(self) -> None:
        """L2-JOIN-U14: 列表偶尔漏掉一个群（不满 10 分钟又出现）：不算新的入群，不再说；真的离开满 10 分钟后再被拉进来：算新的一次，再说一次。
        分页里重复出现的同一个群只算一次。"""
        self.settled()
        self.invite()
        self.lark.duplicate_rows = True
        self.go()
        saved = self.lark.chats.pop(CHAT)
        self.go(minutes=2)
        self.lark.chats[CHAT] = saved
        self.go(minutes=2)
        self.assertEqual(len(self.lark.delivered(CHAT)), 1)
        self.lark.chats.pop(CHAT)
        self.go(minutes=6)
        self.go(minutes=11)
        self.lark.chats[CHAT] = saved
        self.go()
        self.assertEqual(len(self.lark.delivered(CHAT)), 2)

    def test_the_switch_turns_it_off(self) -> None:
        """L2-JOIN-U15: `feishu_unbound_prompt: false`：不调用 lark-cli、不发送；入群申请本身照常。"""
        self.config["feishu_unbound_prompt"] = False
        self.go()
        self.invite()
        result = self.go()
        self.assertEqual(self.lark.calls, [])
        self.assertEqual(result["status"], "ok")



class ClaimsAdapter(unittest.TestCase):
    def test_the_agent_reads_the_claims_itself(self) -> None:
        """L2-JOIN-U16: AgentBuzz.binding_claims 用 agent 自己的 key 签 relay 的 POST /query（带 x-auth-tag），读全部 kind:30177 和声明镜像的
        kind 0，按 ADR-0022 的规则解析；id / 签名被改过的事件丢掉；relay 失败抛 SyncError（调用方据此说「暂时无法确认」，不当成没有认领）。"""
        import base64
        import test_buzz_agent_join_requests_feishu as jf
        entry = {"channel": CHANNEL, "chat_ref": ref_of(CHAT), "claimed_at": NOW - 60, "heartbeat": NOW - 60}
        profile = jf.nostr_event(jf.MIRROR, 0, [["auth", jf.MIRROR_OWNER, "", "ab" * 64]], json.dumps({"name": "m"}), NOW - 999)
        policy = jf.nostr_event(jf.MIRROR_OWNER, 30177, [["d", jf.MIRROR]],
                                json.dumps({"name": "m", "feishu": {"mirror": True, "bindings": [entry]}}), NOW - 99)
        seen = []

        def http(url, headers, timeout, body=None, events=(profile, policy)):
            seen.append({"url": url, "headers": dict(headers), "filters": json.loads(body)})

            def match(ev, f):
                return (ev["kind"] in f.get("kinds", [ev["kind"]]) and ev["pubkey"] in f.get("authors", [ev["pubkey"]]))
            return 200, json.dumps([e for e in events if any(match(e, f) for f in json.loads(body))]).encode()
        env = {"BUZZ_RELAY_URL": "wss://relay.example.test", "BUZZ_PRIVATE_KEY": base.AGENT_KEY,
               "BUZZ_AUTH_TAG": '["auth","x","","y"]'}
        got = join.AgentBuzz("/opt/buzz-0.5.23/usr/bin/buzz", env, http=http).binding_claims()
        self.assertEqual([(c.mirror, c.chat_ref) for c in got.claims], [(jf.MIRROR, ref_of(CHAT))])
        self.assertTrue(seen)
        for request in seen:
            self.assertEqual(request["url"], "https://relay.example.test/query")
            self.assertEqual(request["headers"].get("x-auth-tag"), '["auth","x","","y"]')
            head = json.loads(base64.b64decode(request["headers"]["Authorization"][len("Nostr "):]))
            self.assertEqual(head["pubkey"], base.AGENT)
        forged = dict(policy, sig="00" * 64)
        got = join.AgentBuzz("/opt/buzz-0.5.23/usr/bin/buzz", env,
                             http=lambda *a, **k: http(*a, **k, events=(profile, forged))).binding_claims()
        self.assertEqual(got.claims, ())
        raised = False
        try:
            join.AgentBuzz("/opt/buzz-0.5.23/usr/bin/buzz", env, http=lambda *a, **k: (500, b"x")).binding_claims()
        except join.sync.SyncError:
            raised = True
        self.assertTrue(raised)


if __name__ == "__main__":
    unittest.main()
