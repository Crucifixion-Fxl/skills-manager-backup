"""Issue #162: deterministic admission help, never business-task authorization (L2)."""

import json
from unittest.mock import patch

from test_buzz_agent_join_requests import (
    AGENT, ADMIN, MEMBER, NEW_CH, OTHER_BOT, OWNER, JoinTestCase, join,
)
from test_buzz_agent_join_requests_feishu import (
    MIRROR, MIRROR_OWNER, MirrorAwareBuzz, mirror_events,
)


class PendingFeedback(JoinTestCase):
    def setUp(self):
        super().setUp()
        self.baseline()
        self.invite_by_admin()
        self.run_once()
        self.root = self.record()["request_event"]
        self.join_id = self.record()["join_id"]
        self.relay.sent.clear()

    def mention(self, *, author=MEMBER, root=None, content="请开始工作"):
        tags = [["h", NEW_CH], ["p", AGENT]]
        if root:
            tags.append(["e", root, "", "reply"])
        return self.relay._event(NEW_CH, 9, author, content, tags)

    def notice(self, *parts, reply_to):
        messages = [e for e in self.relay.sent if all(p in e["content"] for p in parts)]
        self.assertEqual(len(messages), 1, self.relay.sent)
        event = messages[0]
        self.assertEqual(event["channel"], NEW_CH)
        self.assertEqual(event["reply_to"], reply_to)
        self.assertNotIn("glpat-", event["content"])
        lines = event["content"].splitlines()
        self.assertIn(f"/approve {self.join_id}", lines)
        self.assertIn(f"/deny {self.join_id}", lines)
        self.assertIn("不需要 @", event["content"])
        self.assertIn("原入群申请", event["content"])
        self.assertIn(self.root, event["content"])
        self.assertEqual(self.record()["state"], "REQUESTED")
        self.assertNotIn(NEW_CH, self.env_channels())
        self.assertEqual(self.system.restarts, [])
        return event

    def test_L2_JOIN_162_008_pending_mention_answers_in_same_thread_once(self):
        other = self.relay.top(NEW_CH, MEMBER, "另一个任务")["id"]
        self.mention(root=other)
        self.run_once()
        self.notice("等待", "陈老板", "不能执行", reply_to=other)
        self.run_once()
        self.notice("等待", reply_to=other)

    def test_L2_JOIN_162_009_top_level_mention_becomes_its_own_reply_thread(self):
        source = self.mention()
        self.run_once()
        self.notice("等待", reply_to=source["id"])

    def test_L2_JOIN_162_010_wrong_thread_approval_gets_help_not_approval(self):
        source = self.relay.top(NEW_CH, OWNER, f"/approve {self.join_id}")
        self.run_once()
        self.notice("审批未生效", "话题", reply_to=source["id"])

    def test_L2_JOIN_162_011_bad_number_and_nonowner_get_precise_reasons(self):
        self.relay.reply(NEW_CH, OWNER, self.root, "/approve JOIN-deadbeef")
        self.run_once()
        self.notice("审批未生效", "编号", reply_to=self.root)
        self.relay.sent.clear()
        self.relay.react(NEW_CH, ADMIN, self.root, "✅")
        self.run_once()
        self.notice("审批未生效", "只有 owner 陈老板", reply_to=self.root)

    def test_L2_JOIN_162_012_unsafe_unrelated_and_bot_inputs_are_ignored(self):
        self.relay.reply(NEW_CH, MEMBER, self.root, "@nh-dev 请开始工作")
        self.relay.top(NEW_CH, MEMBER, "/approve JOIN-unrelated")
        self.mention(author=AGENT)
        self.mention(author=OTHER_BOT)
        self.mention(author="ab" * 32)
        self.mention()["sig"] = "forged"
        self.mention()["created_at"] = int(self.clock()) + 600
        self.mention()["created_at"] = self.record()["requested_at"] - 1
        self.mention()["tags"][0][1] = "another-channel"
        self.run_once()
        self.assertEqual(self.relay.sent, [])
        self.assertEqual(self.record()["state"], "REQUESTED")

    def mirror_input(self, text, *, tags=(), root=None):
        self.relay.profile_events = mirror_events()
        self.relay.channels[NEW_CH]["members"].update({MIRROR_OWNER: "admin", MIRROR: "bot"})
        return self.relay._event(NEW_CH, 9, MIRROR, text,
                                 [["h", NEW_CH], ["e", root or self.root, "", "reply"], *tags])

    def mirror_round(self):
        return join.run(self.config, state_dir=self.state_dir,
                        make_buzz=lambda agent: MirrorAwareBuzz(self.relay, agent.pubkey),
                        system=self.system, clock=self.clock, sleeper=self.clock.sleep)

    def test_L2_JOIN_162_013_feishu_real_mention_without_approval_author_gets_help(self):
        self.mirror_input("[飞书] 成员乙：＠nh-dev 请开始工作", tags=[["p", AGENT]])
        self.mirror_round()
        self.notice("等待", reply_to=self.root)
        self.mirror_round()
        self.notice("等待", reply_to=self.root)

    def test_L2_JOIN_162_014_feishu_approval_in_wrong_thread_gets_help(self):
        other = self.relay.top(NEW_CH, MEMBER, "另一个话题")["id"]
        # Ordinary mirrored text outside the request carries no approval-author tag.
        self.mirror_input(f"[飞书] 陈老板：＠buzz-desk /approve {self.join_id}", root=other)
        self.mirror_round()
        self.notice("审批未生效", "话题", reply_to=other)

    def test_L2_JOIN_162_025_malformed_mirror_command_explains_format_not_owner(self):
        # Real mirror routing intentionally omits approval-author tags for malformed input.
        self.mirror_input(f"[飞书] 陈老板：/approve {self.join_id} 多余文字")
        self.mirror_round()
        notice = self.notice("审批未生效", "格式", "不要", reply_to=self.root)
        self.assertNotIn("未识别为 owner", notice["content"])
        self.mirror_round()
        self.notice("审批未生效", "格式", reply_to=self.root)

    def test_L2_JOIN_162_026_wrong_mirrored_number_explains_number_not_owner(self):
        self.mirror_input("[飞书] 陈老板：/approve JOIN-deadbeef")
        self.mirror_round()
        notice = self.notice("审批未生效", "编号", reply_to=self.root)
        self.assertNotIn("未识别为 owner", notice["content"])

    def test_L2_JOIN_162_027_valid_nonowner_mirror_command_stays_rejected(self):
        self.mirror_input(f"[飞书] 成员乙：/approve {self.join_id}",
                          tags=[["feishu-author", MEMBER], ["join", self.join_id]])
        self.mirror_round()
        self.notice("审批未生效", "只有 owner 陈老板", reply_to=self.root)

    def test_L2_JOIN_162_028_valid_mirror_command_without_owner_proof_stays_rejected(self):
        self.mirror_input(f"[飞书] 陈老板：/approve {self.join_id}")
        self.mirror_round()
        self.notice("审批未生效", "owner", reply_to=self.root)

    def test_L2_JOIN_162_015_untrusted_mirror_or_explicit_disable_cannot_get_help(self):
        self.mirror_input("[飞书] 成员乙：请开始工作", tags=[["p", AGENT]])
        self.relay.profile_events = mirror_events(declared=False)
        self.mirror_round()
        self.assertEqual(self.relay.sent, [])
        self.relay.profile_events = mirror_events()
        self.config["accept_feishu_approvals"] = False
        self.mirror_round()
        self.assertEqual(self.relay.sent, [])

    def test_L2_JOIN_162_016_transport_failure_retries_without_advancing_admission(self):
        source = self.mention()
        self.relay.fail_send_contains = "等待"
        self.run_once()
        self.assertEqual(self.record()["state"], "REQUESTED")
        self.assertFalse(any("等待" in e["content"] for e in self.relay.sent))
        self.assertTrue(any("入群审批处理失败" in e["content"] for e in self.relay.sent))
        self.relay.fail_send_contains = None
        self.run_once()
        self.notice("等待", reply_to=source["id"])

    def crash_after_delivery(self, *, before_save):
        class Crash(BaseException):
            pass

        real_save = join.save_state

        def save(state_dir, state):
            delivered = any("等待" in e["content"] for e in self.relay.sent)
            if delivered and before_save:
                raise Crash()
            real_save(state_dir, state)
            if delivered:
                raise Crash()

        with patch.object(join, "save_state", save), self.assertRaises(Crash):
            self.run_once()

    def test_L2_JOIN_162_017_crash_before_receipt_save_adopts_delivered_notice(self):
        source = self.mention()
        self.crash_after_delivery(before_save=True)
        self.run_once()
        self.notice("等待", reply_to=source["id"])

    def test_L2_JOIN_162_018_crash_after_receipt_save_does_not_duplicate(self):
        source = self.mention()
        self.crash_after_delivery(before_save=False)
        self.run_once()
        self.notice("等待", reply_to=source["id"])

    def test_L2_JOIN_162_019_request_explains_feedback_is_not_business_execution(self):
        event = next(e for e in self.relay.channels[NEW_CH]["events"] if e["id"] == self.root)
        self.assertNotIn("我不会回应本群的 @", event["content"])
        self.assertIn("不能执行", event["content"])
        self.assertIn("审批提示", event["content"])

    def test_L2_JOIN_162_021_original_request_link_survives_feishu_card_renderer(self):
        """The wrong-thread hint must navigate to the request, not back to the wrong Thread."""
        source = self.mention()
        self.run_once()
        notice = self.notice("等待", reply_to=source["id"])
        event = next(e for e in self.relay.channels[NEW_CH]["events"] if e["id"] == notice["id"])
        card = join.fgs.CardContext(link_base="https://bridge.example.test", channel_id=NEW_CH)
        rendered = join.fgs._event_card(event, event["content"], "nh-dev", True, {}, card)
        expected = f"https://bridge.example.test/bind/open?e={self.root}&c={NEW_CH}"
        self.assertIn(f"[原入群申请]({expected})", json.dumps(json.loads(rendered), ensure_ascii=False))
        self.assertNotIn("buzz：//", rendered)

    def test_L2_JOIN_162_022_card_does_not_convert_unscoped_custom_links(self):
        source = self.mention()
        self.run_once()
        notice = self.notice("等待", reply_to=source["id"])
        event = next(e for e in self.relay.channels[NEW_CH]["events"] if e["id"] == notice["id"])
        card = join.fgs.CardContext(link_base="https://bridge.example.test", channel_id=NEW_CH)
        bodies = [
            event["content"].rsplit("\n", 1)[0],  # no exact feedback marker
            event["content"].replace(NEW_CH, "33333333-3333-4333-8333-333333333333"),
            event["content"].replace(f"&id={self.root}", f"&id={self.root}&redirect=https://untrusted.test"),
        ]
        for body in bodies:
            with self.subTest(body=body):
                rendered = join.fgs._event_card(event, body, "nh-dev", True, {}, card)
                self.assertNotIn("[原入群申请](https://", rendered)
                self.assertNotIn("buzz://", rendered)

    def feedback_for_routing(self):
        source = self.mention()
        self.run_once()
        notice = self.notice("等待", reply_to=source["id"])
        return next(e for e in self.relay.channels[NEW_CH]["events"] if e["id"] == notice["id"])

    def route_feedback(self, event):
        card = join.fgs.CardContext(link_base="https://bridge.example.test", channel_id=NEW_CH,
                                    open_ids={OWNER: "ou_owner"})
        return join.fgs.route_buzz_event(
            event, mirror_pubkey=MIRROR, agent_apps={}, human_pubkeys={OWNER},
            agent_pubkeys={AGENT}, names={AGENT: "nh-dev", OWNER: "陈老板"},
            mention_targets={}, card=card, unmanaged_agents="relay", managed_agents=set(),
            unmapped_senders="context")

    def test_L2_JOIN_162_029_desk_relay_keeps_request_link_without_extra_notifications(self):
        event = self.feedback_for_routing()
        event["tags"].append(["p", OWNER])
        outgoing = self.route_feedback(event)
        expected = f"https://bridge.example.test/bind/open?e={self.root}&c={NEW_CH}"
        self.assertTrue(outgoing.relayed)
        self.assertIsNone(outgoing.via_app_id)
        self.assertIn("nh-dev（Buzz·助手）", outgoing.text)
        self.assertIn(f"[原入群申请]({expected})", outgoing.card)
        self.assertNotIn("buzz：//", outgoing.card)
        self.assertNotIn("<at", outgoing.card)
        self.assertNotIn("ou_owner", outgoing.card)

    def test_L2_JOIN_162_030_desk_link_conversion_stays_scoped_to_agent_and_channel(self):
        event = self.feedback_for_routing()
        cases = [
            {**event, "pubkey": OWNER},
            {**event, "pubkey": "cd" * 32},
            {**event, "content": event["content"].rsplit("\n", 1)[0]},
            {**event, "content": event["content"].replace(NEW_CH, "33333333-3333-4333-8333-333333333333")},
            {**event, "content": event["content"].replace(f"&id={self.root}", f"&id={self.root}&redirect=https://untrusted.test")},
        ]
        for case in cases:
            with self.subTest(author=case["pubkey"], content=case["content"]):
                outgoing = self.route_feedback(case)
                self.assertNotIn("[原入群申请](https://", outgoing.card)
                self.assertNotIn("buzz://", outgoing.card)

    def test_L2_JOIN_162_023_service_fault_is_specific_and_saved_approval_recovers(self):
        """A valid Thread reply must not be reported as a formatting/owner mistake."""
        self.mirror_input(f"[飞书] 陈老板：/approve {self.join_id}",
                          tags=[["feishu-author", OWNER], ["join", self.join_id]])
        self.relay.mirror_directory_unavailable = True
        result = self.mirror_round()
        self.assertEqual(result["status"], "error")
        self.assertEqual(self.record()["state"], "REQUESTED")
        faults = [e for e in self.relay.sent if "服务端身份校验" in e["content"]]
        self.assertEqual(len(faults), 1, self.relay.sent)
        self.assertEqual(faults[0]["reply_to"], self.root)
        for text in ("飞书同步", "无需重复", "自动重试", "维护者", "尚未开通"):
            self.assertIn(text, faults[0]["content"])
        self.assertNotIn("本机配置或 Buzz 操作暂不可用", faults[0]["content"])
        self.mirror_round()
        self.assertEqual(len([e for e in self.relay.sent if "服务端身份校验" in e["content"]]), 1)
        self.relay.mirror_directory_unavailable = False
        self.mirror_round()  # same saved command, no user toggle or resend
        self.assertEqual((self.record()["state"], self.record()["outcome"]), ("ACTIVE", "approved"))
        self.assertEqual(len(self.system.restarts), 1)
        self.assertTrue(any("故障已恢复" in e["content"] for e in self.relay.sent))

    def test_L2_JOIN_162_024_old_generic_failure_is_replaced_by_specific_help_once(self):
        self.mirror_input(f"[飞书] 陈老板：/approve {self.join_id}",
                          tags=[["feishu-author", OWNER], ["join", self.join_id]])
        old = self.relay.reply(NEW_CH, AGENT, self.root, "旧版：本机配置或 Buzz 操作暂不可用")
        state = self.state()
        state["agents"][AGENT]["failure_notices"][NEW_CH] = {
            "code": "processing", "at": int(self.clock()), "event": old["id"],
            "incident_id": "ab" * 8, "pending_at": None,
        }
        join.save_state(self.state_dir, state)
        self.relay.mirror_directory_unavailable = True
        self.mirror_round()
        self.mirror_round()
        faults = [e for e in self.relay.sent if "服务端身份校验" in e["content"]]
        self.assertEqual(len(faults), 1, self.relay.sent)
        self.assertEqual(self.record()["state"], "REQUESTED")
        self.assertEqual(self.system.restarts, [])
