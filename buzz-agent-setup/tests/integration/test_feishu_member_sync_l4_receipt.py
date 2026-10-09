"""L4-148: black-box oracle for the Feishu group <-> Buzz channel membership experience (engineering/skills#148).

Set BUZZ_MEMBER_SYNC_L4_RECEIPT to a receipt collected from the real test channel and Feishu group, following
references/feishu-member-sync-l4.md. The oracle re-verifies every Buzz event's NIP-01 id and signature, so a receipt
cannot be hand-written from memory; Feishu records and local observations are read back by the collector.

The synthetic receipt below is signed with throwaway keys. It only proves that the oracle accepts the intended shape
and rejects each known failure; it is not L4 evidence.
"""

import copy
import json
import os
import secrets
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "scripts"))

import buzz_feishu_group_sync as fgs  # noqa: E402
import member_sync_l4_oracle as oracle  # noqa: E402

CHANNEL = "613a9560-d423-4d14-ab3d-f4fc29aecb7a"
CHAT = "oc_" + "1" * 32
JOIN = "JOIN-0a1b2c3d"
T0 = 1790300000


def _key():
    return secrets.token_hex(32)


class Keys:
    def __init__(self):
        self.owner, self.agent, self.signer, self.mirror = _key(), _key(), _key(), _key()
        self.colleague = _key()

    def pub(self, key):
        return fgs._signer_pubkey(key)


def _sign(key, kind, tags, content, at):
    return fgs.sign_event(key, kind, tags, content, at)


def _feishu(message_id, text, at, *, sender="ou_owner", sender_type="user", root=None, mentions=()):
    return {"message_id": message_id, "chat_id": CHAT, "create_time": at, "sender_id": sender,
            "sender_type": sender_type, "root_id": root, "text": text, "mentions": list(mentions)}


def build_receipt():
    k = Keys()
    owner, agent, signer, mirror, colleague = (k.pub(k.owner), k.pub(k.agent), k.pub(k.signer), k.pub(k.mirror),
                                               k.pub(k.colleague))
    member_tags = [["feishu-member-op", "n1"], ["feishu-member-stream", "s1"], ["feishu-member-seq", "1"]]
    meta = {"suite": "L4-148", "skills_revision": "0" * 40, "channel": CHANNEL, "feishu_chat": CHAT,
            "owner": {"pubkey": owner, "feishu_open_id": "ou_owner", "name": "owner"},
            "agent": {"pubkey": agent, "name": "l4-member-agent", "app_id": "cli_agent"},
            "desk_app_id": "cli_desk", "signer_pubkeys": [signer], "mirror_pubkeys": [mirror],
            "colleague": {"pubkey": colleague, "feishu_open_id": "ou_colleague", "name": "colleague"}}

    add = _sign(k.signer, 9000, [["h", CHANNEL], ["p", agent], ["role", "bot"]] + member_tags, "", T0 + 5)
    request_text = "\n".join([
        "我是 l4-member-agent（owner：owner），收到了加入「L4」的邀请。",
        f"/approve {JOIN}", f"/deny {JOIN}", f"buzz-join:v1 {JOIN}"])
    request = _sign(k.agent, 9, [["h", CHANNEL], ["p", owner]], request_text, T0 + 20)
    request_feishu = _feishu("om_request", request_text, T0 + 25, sender="cli_desk", sender_type="app")

    def active(after):
        return _sign(k.agent, 9, [["h", CHANNEL], ["e", request["id"], "", "root"]],
                     f"已开通，现在可以在本群 @ 我（l4-member-agent）。\nbuzz-join:v1 {JOIN} active", after)

    def restart(after):
        return {"old_main_pid": 100, "new_main_pid": 200, "restarted_at": after, "subscribed_at": after + 3}

    reaction = _sign(k.mirror, 7, [["h", CHANNEL], ["e", request["id"]], ["feishu-author", owner], ["join", JOIN]],
                     "✅", T0 + 60)
    command = _sign(k.mirror, 9, [["h", CHANNEL], ["e", request["id"], "", "root"], ["feishu-author", owner],
                                  ["join", JOIN]], f"[飞书] owner：\n/approve {JOIN}", T0 + 60)
    other_reaction = _sign(k.mirror, 7, [["h", CHANNEL], ["e", request["id"]], ["feishu-author", colleague]], "✅",
                           T0 + 40)
    refusal = _sign(k.agent, 9, [["h", CHANNEL], ["e", request["id"], "", "root"]],
                    f"审批未生效：只有 owner owner 能决定是否让我加入本群。\nbuzz-join:v1 {JOIN} feedback-{other_reaction['id']}",
                    T0 + 45)
    stray = _sign(k.mirror, 9, [["h", CHANNEL], ["feishu-author", owner]], f"[飞书] owner：\n/approve {JOIN}", T0 + 41)
    stray_reply = _sign(k.agent, 9, [["h", CHANNEL], ["e", stray["id"], "", "root"]],
                        f"审批未生效：这条命令不在原入群申请的话题内。\nbuzz-join:v1 {JOIN} feedback-{stray['id']}", T0 + 46)
    early = _sign(k.mirror, 9, [["h", CHANNEL], ["p", agent], ["feishu-author", colleague]],
                  "[飞书] colleague：\n@l4-member-agent 你好", T0 + 42)
    early_reply = _sign(k.agent, 9, [["h", CHANNEL], ["e", early["id"], "", "root"]],
                        f"我是 l4-member-agent，已收到你的 @。本群仍在等待 owner owner 同意入群。\nbuzz-join:v1 {JOIN} "
                        f"feedback-{early['id']}", T0 + 47)

    mention = _sign(k.mirror, 9, [["h", CHANNEL], ["p", agent], ["feishu-author", colleague]],
                    "[飞书] colleague：\n@l4-member-agent 回复 PONG", T0 + 200)
    answer = _sign(k.agent, 9, [["h", CHANNEL], ["e", mention["id"], "", "root"]], "PONG", T0 + 230)

    task = _sign(k.owner, 9, [["h", "c" * 8 + CHANNEL[8:]], ["p", agent]], "@l4-member-agent 慢任务", T0 + 300)
    task_done = _sign(k.agent, 9, [["h", "c" * 8 + CHANNEL[8:]], ["e", task["id"], "", "root"]], "DONE", T0 + 420)

    remove = _sign(k.signer, 9001, [["h", CHANNEL], ["p", agent]] + member_tags, "", T0 + 510)
    buzz_add = _sign(k.owner, 9000, [["h", CHANNEL], ["p", agent], ["role", "bot"]], "", T0 + 600)
    buzz_remove = _sign(k.owner, 9001, [["h", CHANNEL], ["p", agent]], "", T0 + 700)

    person_add = _sign(k.signer, 9000, [["h", CHANNEL], ["p", colleague], ["role", "member"]] + member_tags, "",
                       T0 + 810)
    person_remove = _sign(k.signer, 9001, [["h", CHANNEL], ["p", colleague]] + member_tags, "", T0 + 910)

    target = _sign(k.owner, 9, [["h", CHANNEL]], "表情目标", T0 + 1000)
    thumbs = _sign(k.mirror, 7, [["h", CHANNEL], ["e", target["id"]], ["feishu-author", colleague]], "👍", T0 + 1020)
    withdraw = _sign(k.mirror, 5, [["h", CHANNEL], ["e", thumbs["id"]]], "", T0 + 1040)
    owner_like = _sign(k.owner, 7, [["h", CHANNEL], ["e", target["id"]]], "👍", T0 + 1060)

    cases = {
        "L4-148-01": {"status": "passed", "feishu_bots_before": [], "feishu_bots_after": ["cli_agent"],
                      "feishu_added_at": T0, "add_event": add, "request_event": request,
                      "request_feishu": request_feishu,
                      "intro_feishu": _feishu("om_intro", "我是 l4-member-agent，负责 L4", T0 + 30, sender="cli_agent",
                                              sender_type="app")},
        "L4-148-02": {"status": "passed", "request_event": request, "request_feishu": request_feishu,
                      "feishu_reaction": {"message_id": "om_request", "emoji_type": "CheckMark",
                                          "operator_id": "ou_owner", "at": T0 + 58},
                      "mirror_reaction": reaction, "restart": restart(T0 + 70), "active_event": active(T0 + 80),
                      "active_feishu": _feishu("om_active", "已开通，现在可以在本群 @ 我", T0 + 85,
                                               sender="cli_agent", sender_type="app", root="om_request")},
        "L4-148-03": {"status": "passed", "request_event": request, "request_feishu": request_feishu,
                      "feishu_command": _feishu("om_cmd", f"/approve {JOIN}", T0 + 58, root="om_request"),
                      "mirror_command": command, "restart": restart(T0 + 70), "active_event": active(T0 + 80)},
        "L4-148-04": {"status": "passed", "request_event": request,
                      "non_owner_reaction": other_reaction, "non_owner_feedback": refusal,
                      "stray_command": stray, "stray_feedback": stray_reply,
                      "pending_mention": early, "pending_feedback": early_reply,
                      "state_after": "REQUESTED", "rounds_observed": 2, "active_events_before_owner": []},
        "L4-148-05": {"status": "passed", "feishu_mention": _feishu("om_m", "@l4-member-agent 回复 PONG", T0 + 199,
                                                                    sender="ou_colleague", mentions=["cli_agent"]),
                      "mirror_mention": mention, "agent_reply": answer,
                      "reply_feishu": _feishu("om_pong", "PONG", T0 + 235, sender="cli_agent", sender_type="app",
                                              root="om_m")},
        "L4-148-06": {"status": "passed", "task_event": task, "task_done_event": task_done,
                      "approval_at": T0 + 330, "busy_at_approval": True, "restart": restart(T0 + 430)},
        "L4-148-07": {"status": "passed",
                      "feishu_remove": {"bots_before": ["cli_agent"], "bots_after": [], "at": T0 + 500,
                                        "event": remove},
                      "buzz_add": {"event": buzz_add, "bots_after": ["cli_agent"], "observed_at": T0 + 650},
                      "buzz_remove": {"event": buzz_remove, "bots_after": [], "observed_at": T0 + 750}},
        "L4-148-08": {"status": "passed",
                      "feishu_add_person": {"users_after": ["ou_owner", "ou_colleague"], "at": T0 + 800,
                                            "event": person_add},
                      "feishu_remove_person": {"users_after": ["ou_owner"], "at": T0 + 900, "event": person_remove}},
        "L4-148-09": {"status": "passed", "target_event": target,
                      "target_feishu": _feishu("om_target", "表情目标", T0 + 1005, sender="cli_desk",
                                               sender_type="app"),
                      "feishu_reaction": {"message_id": "om_target", "emoji_type": "THUMBSUP",
                                          "operator_id": "ou_colleague", "at": T0 + 1010},
                      "mirror_reaction": thumbs, "feishu_withdrawn_at": T0 + 1030, "mirror_withdraw": withdraw,
                      "buzz_reaction": owner_like,
                      "feishu_reaction_from_buzz": {"message_id": "om_target", "emoji_type": "THUMBSUP",
                                                    "operator_type": "app", "at": T0 + 1070}},
    }
    return {"meta": meta, "cases": cases}, k


_BASE = None


def fresh_receipt():
    """Signing is slow in pure Python: sign the synthetic receipt once, hand each test its own copy."""
    global _BASE
    if _BASE is None:
        _BASE = build_receipt()
    receipt, keys = _BASE
    return copy.deepcopy(receipt), keys


def _resign(event, key, **changes):
    return _sign(key, changes.get("kind", event["kind"]), changes.get("tags", event["tags"]),
                 changes.get("content", event["content"]), changes.get("created_at", event["created_at"]))


class ReceiptShapeTests(unittest.TestCase):
    def setUp(self):
        self.receipt, self.keys = fresh_receipt()

    def assertRejected(self, receipt, message):
        with self.assertRaisesRegex(AssertionError, message):
            oracle.assert_receipt(receipt)

    def test_synthetic_receipt_is_accepted(self):
        oracle.assert_receipt(self.receipt)

    def test_every_required_case_must_be_present(self):
        del self.receipt["cases"]["L4-148-08"]
        self.assertRejected(self.receipt, "L4-148-08.*missing")

    def test_not_run_needs_a_reason_and_never_counts_as_complete(self):
        self.receipt["cases"]["L4-148-08"] = {"status": "not_run"}
        self.assertRejected(self.receipt, "L4-148-08.*reason")
        self.receipt["cases"]["L4-148-08"] = {"status": "not_run", "reason": "no colleague"}
        oracle.assert_receipt(self.receipt, require_complete=False)
        self.assertRejected(self.receipt, "L4-148-08.*not_run")

    def test_forged_signature_is_rejected(self):
        event = self.receipt["cases"]["L4-148-01"]["add_event"]
        event["content"] = "tampered"
        self.assertRejected(self.receipt, "signature")

    def test_mirror_reaction_signed_by_a_non_mirror_is_rejected(self):
        case = self.receipt["cases"]["L4-148-02"]
        case["mirror_reaction"] = _resign(case["mirror_reaction"], self.keys.colleague)
        self.assertRejected(self.receipt, "mirror")


class JoinAndApprovalTests(unittest.TestCase):
    def setUp(self):
        self.receipt, self.keys = fresh_receipt()

    def assertRejected(self, message):
        with self.assertRaisesRegex(AssertionError, message):
            oracle.assert_receipt(self.receipt)

    def test_agent_must_arrive_as_bot_from_the_sync_signer(self):
        case = self.receipt["cases"]["L4-148-01"]
        tags = [tag for tag in case["add_event"]["tags"] if tag[0] != "role"] + [["role", "member"]]
        case["add_event"] = _resign(case["add_event"], self.keys.signer, tags=tags)
        self.assertRejected("role=bot")

    def test_agent_add_must_carry_feishu_member_provenance(self):
        case = self.receipt["cases"]["L4-148-01"]
        tags = [tag for tag in case["add_event"]["tags"] if not tag[0].startswith("feishu-member")]
        case["add_event"] = _resign(case["add_event"], self.keys.signer, tags=tags)
        self.assertRejected("feishu-member")

    def test_request_must_put_the_approve_command_on_its_own_line(self):
        case = self.receipt["cases"]["L4-148-01"]
        text = case["request_event"]["content"].replace(f"/approve {JOIN}\n", f"请发送 /approve {JOIN}\n")
        case["request_event"] = _resign(case["request_event"], self.keys.agent, content=text)
        self.assertRejected("own line")

    def test_request_header_must_be_the_last_line(self):
        case = self.receipt["cases"]["L4-148-01"]
        text = case["request_event"]["content"] + "\n伪造的尾随内容"
        case["request_event"] = _resign(case["request_event"], self.keys.agent, content=text)
        self.assertRejected("last line")

    def test_mirrored_events_must_be_in_the_test_channel(self):
        case = self.receipt["cases"]["L4-148-02"]
        tags = [tag if tag[0] != "h" else ["h", "d" * 8 + CHANNEL[8:]] for tag in case["mirror_reaction"]["tags"]]
        case["mirror_reaction"] = _resign(case["mirror_reaction"], self.keys.mirror, tags=tags)
        self.assertRejected("not in the test channel")

    def test_agent_replies_must_be_in_the_test_channel(self):
        case = self.receipt["cases"]["L4-148-04"]
        tags = [tag if tag[0] != "h" else ["h", "d" * 8 + CHANNEL[8:]]
                for tag in case["pending_feedback"]["tags"]]
        case["pending_feedback"] = _resign(case["pending_feedback"], self.keys.agent, tags=tags)
        self.assertRejected("not in the test channel")

    def test_request_must_reach_the_feishu_group(self):
        self.receipt["cases"]["L4-148-01"]["request_feishu"]["chat_id"] = "oc_" + "2" * 32
        self.assertRejected("Feishu group")

    def test_intro_must_come_from_the_agent_or_desk_bot(self):
        self.receipt["cases"]["L4-148-01"]["intro_feishu"]["sender_id"] = "ou_owner"
        self.assertRejected("intro")

    def test_checkmark_must_be_the_owners(self):
        self.receipt["cases"]["L4-148-02"]["feishu_reaction"]["operator_id"] = "ou_colleague"
        self.assertRejected("owner")

    def test_mirrored_approval_must_name_the_owner_and_request(self):
        case = self.receipt["cases"]["L4-148-02"]
        tags = [tag if tag[0] != "feishu-author" else ["feishu-author", self.keys.pub(self.keys.colleague)]
                for tag in case["mirror_reaction"]["tags"]]
        case["mirror_reaction"] = _resign(case["mirror_reaction"], self.keys.mirror, tags=tags)
        self.assertRejected("feishu-author")

    def test_activation_needs_a_real_restart_and_subscription_first(self):
        case = self.receipt["cases"]["L4-148-02"]
        case["restart"]["new_main_pid"] = case["restart"]["old_main_pid"]
        self.assertRejected("restart")
        case["restart"]["new_main_pid"] = 200
        case["restart"]["subscribed_at"] = case["active_event"]["created_at"] + 1
        self.assertRejected("subscribed")

    def test_activation_reply_belongs_in_the_request_thread(self):
        case = self.receipt["cases"]["L4-148-02"]
        tags = [["h", CHANNEL]]
        case["active_event"] = _resign(case["active_event"], self.keys.agent, tags=tags)
        self.assertRejected("request Thread")

    def test_command_must_be_a_thread_reply_without_mention(self):
        case = self.receipt["cases"]["L4-148-03"]
        case["feishu_command"]["root_id"] = None
        self.assertRejected("Thread reply")
        case["feishu_command"]["root_id"] = "om_request"
        case["feishu_command"]["mentions"] = ["cli_agent"]
        self.assertRejected("without @")

    def test_mirrored_command_is_verified_like_the_mirrored_reaction(self):
        """03 hands mirror_command to the same approval chain as 02: signer, channel, owner and JOIN all checked."""
        other = "d" * 8 + CHANNEL[8:]
        cases = {
            "trusted mirror": lambda e: _resign(e, self.keys.colleague),
            "not in the test channel": lambda e: _resign(
                e, self.keys.mirror, tags=[t if t[0] != "h" else ["h", other] for t in e["tags"]]),
            "feishu-author": lambda e: _resign(e, self.keys.mirror, tags=[
                t if t[0] != "feishu-author" else ["feishu-author", self.keys.pub(self.keys.colleague)]
                for t in e["tags"]]),
            "does not name": lambda e: _resign(
                e, self.keys.mirror, tags=[t if t[0] != "join" else ["join", "JOIN-ffffffff"] for t in e["tags"]]),
        }
        for message, mutate in cases.items():
            with self.subTest(message=message):
                receipt, _ = fresh_receipt()
                case = receipt["cases"]["L4-148-03"]
                case["mirror_command"] = mutate(case["mirror_command"])
                with self.assertRaisesRegex(AssertionError, message):
                    oracle.assert_receipt(receipt)

    def test_negatives_must_not_activate_and_must_explain(self):
        case = self.receipt["cases"]["L4-148-04"]
        case["active_events_before_owner"] = ["x"]
        self.assertRejected("activated")
        case["active_events_before_owner"] = []
        case["non_owner_feedback"] = _resign(case["non_owner_feedback"], self.keys.agent,
                                             content="好的\nbuzz-join:v1 " + JOIN)
        self.assertRejected("审批未生效")

    def test_pending_mention_feedback_is_in_the_same_thread(self):
        case = self.receipt["cases"]["L4-148-04"]
        case["pending_feedback"] = _resign(case["pending_feedback"], self.keys.agent, tags=[["h", CHANNEL]])
        self.assertRejected("same Thread")


class AfterActivationTests(unittest.TestCase):
    def setUp(self):
        self.receipt, self.keys = fresh_receipt()

    def assertRejected(self, message):
        with self.assertRaisesRegex(AssertionError, message):
            oracle.assert_receipt(self.receipt)

    def test_real_mention_gets_an_agent_reply_in_the_same_thread(self):
        case = self.receipt["cases"]["L4-148-05"]
        case["agent_reply"] = _resign(case["agent_reply"], self.keys.owner)
        self.assertRejected("agent")
        self.receipt, self.keys = fresh_receipt()
        case = self.receipt["cases"]["L4-148-05"]
        case["reply_feishu"]["root_id"] = None
        self.assertRejected("Feishu Thread")

    def test_busy_agent_is_not_restarted_before_its_task_finishes(self):
        case = self.receipt["cases"]["L4-148-06"]
        case["restart"]["restarted_at"] = case["task_done_event"]["created_at"] - 1
        self.assertRejected("interrupted")

    def test_busy_case_must_have_observed_the_agent_busy(self):
        self.receipt["cases"]["L4-148-06"]["busy_at_approval"] = False
        self.assertRejected("busy")

    def test_feishu_removal_of_agent_reaches_buzz(self):
        case = self.receipt["cases"]["L4-148-07"]["feishu_remove"]
        case["event"] = _resign(case["event"], self.keys.signer, kind=9000)
        self.assertRejected("9001")

    def test_buzz_changes_reach_feishu(self):
        self.receipt["cases"]["L4-148-07"]["buzz_add"]["bots_after"] = []
        self.assertRejected("Buzz add")
        self.receipt, self.keys = fresh_receipt()
        self.receipt["cases"]["L4-148-07"]["buzz_remove"]["bots_after"] = ["cli_agent"]
        self.assertRejected("Buzz remove")

    def test_person_changes_from_feishu_reach_buzz_as_member(self):
        case = self.receipt["cases"]["L4-148-08"]["feishu_add_person"]
        tags = [tag if tag[0] != "role" else ["role", "bot"] for tag in case["event"]["tags"]]
        case["event"] = _resign(case["event"], self.keys.signer, tags=tags)
        self.assertRejected("role=member")

    def test_person_removal_needs_the_person_gone_from_feishu(self):
        self.receipt["cases"]["L4-148-08"]["feishu_remove_person"]["users_after"].append("ou_colleague")
        self.assertRejected("still in the Feishu group")

    def test_reaction_mirror_names_the_human_and_withdraw_follows(self):
        case = self.receipt["cases"]["L4-148-09"]
        case["mirror_withdraw"] = _resign(case["mirror_withdraw"], self.keys.mirror, tags=[["h", CHANNEL]])
        self.assertRejected("withdraw")

    def test_buzz_reaction_reaches_feishu(self):
        self.receipt["cases"]["L4-148-09"]["feishu_reaction_from_buzz"]["message_id"] = "om_other"
        self.assertRejected("Buzz reaction")


class RealReceiptTests(unittest.TestCase):
    @unittest.skipUnless(os.getenv("BUZZ_MEMBER_SYNC_L4_RECEIPT"), "real L4 receipt not supplied")
    def test_real_member_sync_receipt(self):
        receipt = json.loads(Path(os.environ["BUZZ_MEMBER_SYNC_L4_RECEIPT"]).read_text())
        oracle.assert_receipt(receipt)


if __name__ == "__main__":
    unittest.main()
