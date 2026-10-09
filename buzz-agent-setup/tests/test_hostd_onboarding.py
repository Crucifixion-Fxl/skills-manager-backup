"""Card-only approval contracts, with isolated SQL and fake bot effects."""
import asyncio
import json
import concurrent.futures
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

HOSTD = Path(__file__).resolve().parents[1] / "scripts" / "hostd"
sys.path.insert(0, str(HOSTD))
import store
try:
    import onboarding as ob
    import join_cards as jc
except ImportError:
    ob = jc = None

AGENT = "a" * 64
OWNER = "b" * 64
OTHER = "c" * 64


class Identity:
    def __init__(self):
        self.pubkey = OWNER
        self.calls = []

    async def resolve(self, app_id, operator, *, now):
        self.calls.append((app_id, operator, now))
        return ob.Person(app_id, "on_owner", self.pubkey, now)

    async def owner(self, agent_id, app_id, *, now):
        return OWNER

    async def owner_union(self, owner_pubkey, app_id, *, now):
        return "on_owner"

    async def inviter(self, app_id, operator, *, now):
        self.calls.append((app_id, operator, now))
        return "on_owner"


class Cards:
    def __init__(self):
        self.sent = []
        self.updated = []
        self.dms = []
        self.refusals = []
        self.failure = False
        self.recovery_verified = False
        self.recovered_message = ""

    async def send(self, row, generation):
        self.sent.append((row["request_id"], generation))
        if self.failure:
            raise RuntimeError("credential-canary-content")
        return "om_card" + str(generation)

    async def update(self, row, message_id, state):
        self.updated.append((message_id, state))

    async def dm(self, row, union_id):
        self.dms.append((row["request_id"], union_id))

    async def recover(self, row, generation):
        return ob.CardRecovery(generation, self.recovered_message, self.recovery_verified)

    async def refuse(self, row, union_id, event_id, reason):
        self.refusals.append((union_id, event_id, reason))


class Effects:
    def __init__(self):
        self.applies = 0
        self.verified = False
        self.cleaned = []

    async def apply(self, row):
        self.applies += 1

    async def readback(self, row):
        return ob.EffectReceipt(row["request_id"], row["agent_id"], row["chat_id"], self.verified and self.applies > 0)

    async def cleanup(self, row):
        self.cleaned.append(row["request_id"])


class OnboardingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.assertIsNotNone(ob, "card coordinator is missing")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = store.Store(Path(self.temp.name) / "private" / "hostd.db")
        self.addCleanup(self.db.close)
        self.db.register_agent(AGENT, owner_pubkey=OWNER, app_id="cli_agent", now=10)
        self.identity, self.cards, self.effects = Identity(), Cards(), Effects()
        self.co = ob.Coordinator(self.db, self.identity, self.cards, effects=self.effects, clock=lambda: 10)

    async def invite(self, event="invite1", now=10):
        invite = ob.Invite("cli_agent", AGENT, "oc_group", event, ob.Operator("ou_owner"), "new_binding")
        toast = self.co.enqueue_invite(invite, now=now)
        self.assertEqual(toast["toast"]["type"], "info")
        await self.co.drain()
        return self.db.conn.execute("SELECT request_id FROM join_request").fetchone()[0]

    async def click(self, request, *, owner=OWNER, app="cli_agent", message="om_card1", generation=1, event="click1", approved=True, now=20):
        self.identity.pubkey = owner
        decision = ob.CardDecision(app, "oc_group", message, event, request, generation, approved, ob.Operator("ou_owner"))
        self.co.enqueue_card(decision, now=now)
        await self.co.drain()

    async def test_invite_own_bot_verified_and_duplicates_one_request(self):
        request = await self.invite()
        await self.invite()
        self.assertEqual(len(self.cards.sent), 1)
        self.assertEqual(self.db.join_request(request)["status"], "requested")
        self.assertEqual(self.identity.calls[0][0], "cli_agent")

    async def test_nonowner_can_request_but_cannot_approve(self):
        self.identity.pubkey = OTHER
        request = await self.invite()
        await self.click(request, owner=OTHER)
        self.assertEqual(self.db.join_request(request)["status"], "requested")
        self.assertEqual(self.effects.applies, 0)
        self.assertEqual(self.cards.refusals, [("on_owner", "click1", "owner")])
        await self.click(request, owner=OTHER)
        self.assertEqual(len(self.cards.refusals), 1)

    async def test_inviter_without_buzz_binding_still_creates_request(self):
        async def unmapped(*args, **kwargs): raise ValueError("no people binding")
        self.identity.resolve = unmapped
        request = await self.invite()
        self.assertEqual(len(self.cards.sent), 1)
        self.assertEqual(self.db.join_request(request)["status"], "requested")
        await self.click(request)
        self.assertEqual(self.effects.applies, 0)
        self.assertEqual(self.db.join_request(request)["status"], "requested")

    async def test_failure_outputs_exact_remedy_and_ai_contract(self):
        self.assertIn("怎么解决", ob.NOTICE)
        self.assertIn("复制给 AI", ob.NOTICE)
        invalid = ob.CardDecision("cli_agent", "oc_group", "om_old", "click", "JOIN-12345678", 1, True, ob.Operator("ou_owner"))
        text = self.co.enqueue_card(invalid, now=20)["toast"]["content"]
        self.assertIn("怎么解决", text)
        self.assertIn("复制给 AI", text)
        row = dict(request_id="JOIN-12345678", kind="new_binding", status="requested", callback_app_id="cli_agent", chat_id="oc_group")
        for state in ("blocked", "denied", "expired", "superseded"):
            text = json.dumps(jc.card(row, 1, state), ensure_ascii=False)
            self.assertIn("怎么解决", text)
            self.assertIn("复制给 AI", text)

    async def test_foreign_app_invite_cannot_create_request(self):
        self.co.enqueue_invite(ob.Invite("cli_other", AGENT, "oc_group", "invite1", ob.Operator("ou_x"), "new_binding"), now=10)
        await self.co.drain()
        self.assertEqual(self.db.conn.execute("SELECT count(*) FROM join_request").fetchone()[0], 0)

    async def test_old_card_app_chat_and_generation_fail_closed(self):
        request = await self.invite()
        for args in ({"app": "cli_other"}, {"message": "om_old"}, {"generation": 2}):
            await self.click(request, **args)
        wrong = ob.CardDecision("cli_agent", "oc_other", "om_card1", "chatwrong", request, 1, True, ob.Operator("ou_owner"))
        self.co.enqueue_card(wrong, now=20)
        await self.co.drain()
        self.assertEqual(self.db.join_request(request)["status"], "requested")

    async def test_approval_without_verified_readback_stays_approved(self):
        request = await self.invite()
        await self.click(request)
        self.assertEqual(self.db.join_request(request)["status"], "approved")
        self.assertEqual(self.effects.applies, 1)
        self.assertTrue(any(state == "blocked" for _, state in self.cards.updated))

    async def test_verified_effect_completion_and_replay_no_duplicate_apply(self):
        request = await self.invite()
        self.effects.verified = True
        await self.click(request)
        await self.click(request)
        self.assertEqual(self.db.join_request(request)["status"], "done")
        self.assertEqual(self.effects.applies, 1)

    async def test_unset_effects_cannot_report_applied(self):
        self.co.effects = None
        request = await self.invite()
        await self.click(request)
        self.assertEqual(self.db.join_request(request)["status"], "approved")

    async def test_deny_first_valid_click_terminal(self):
        request = await self.invite()
        await self.click(request, approved=False)
        await self.click(request, event="later", approved=True)
        self.assertEqual(self.db.join_request(request)["status"], "denied")
        self.assertEqual(self.effects.applies, 0)

    async def test_retry_same_request_new_card_invalidates_old_once_dm(self):
        request = await self.invite()
        await self.co.tick(now=400)
        self.assertEqual(self.cards.sent, [(request, 1), (request, 2)])
        self.assertIn(("om_card1", "superseded"), self.cards.updated)
        self.assertEqual(len(self.cards.dms), 1)
        await self.click(request, message="om_card1", generation=1)
        self.assertEqual(self.db.join_request(request)["status"], "requested")
        await self.co.tick(now=1000)
        self.assertEqual(len(self.cards.dms), 1)

    async def test_send_unknown_retries_identical_generation_after_restart(self):
        self.cards.failure = True
        request = await self.invite()
        self.assertEqual(self.db.join_request(request)["card_generation"], 0)
        self.cards.failure = False
        self.cards.recovery_verified = True
        other = ob.Coordinator(self.db, self.identity, self.cards, clock=lambda: 10)
        await other.tick(now=400)
        self.assertEqual(self.cards.sent, [(request, 1), (request, 1)])
        self.assertEqual(self.db.join_request(request)["card_generation"], 1)

    async def test_expiry_and_independent_request_backoff(self):
        request = await self.invite()
        await self.co.tick(now=10 + 7 * 86400)
        self.assertEqual(self.db.join_request(request)["status"], "expired")
        self.assertEqual(self.effects.cleaned, [request])
        await self.click(request)
        self.assertEqual(self.effects.applies, 0)

    async def test_toast_immediate_even_slow_identity_and_queue_bounded(self):
        async def slow(*args, **kwargs):
            await asyncio.sleep(2)
            return ob.Person("cli_agent", "on_owner", OWNER, 10)
        self.identity.resolve = slow
        before = asyncio.get_running_loop().time()
        self.co.enqueue_invite(ob.Invite("cli_agent", AGENT, "oc_group", "event", ob.Operator("ou_owner"), "new_binding"), now=10)
        self.assertLess(asyncio.get_running_loop().time() - before, .05)
        self.assertFalse(self.identity.calls)

    async def test_raw_errors_not_persisted_and_old_authorizations_unaccepted(self):
        self.cards.failure = True
        await self.invite()
        dump = "\n".join(self.db.conn.iterdump())
        self.assertNotIn("credential-canary-content", dump)
        with self.assertRaises(ValueError):
            ob.CardDecision("cli_agent", "oc_group", "om_card1", "x", "JOIN-12345678", 1, "/approve", ob.Operator("ou_owner"))

    async def test_expired_request_new_invite_creates_new_request_duplicate_event_does_not(self):
        request = await self.invite()
        await self.co.tick(now=10 + 7 * 86400)
        await self.invite(event="invite2", now=20 + 7 * 86400)
        rows = self.db.join_requests()
        self.assertEqual(len(rows), 2)
        self.assertEqual(sum(row["status"] == "requested" for row in rows), 1)
        self.assertEqual(self.db.join_request(request)["status"], "expired")

    async def test_fresh_owner_change_and_stale_person_cannot_approve(self):
        request = await self.invite()
        async def changed(*args, **kwargs): return OTHER
        self.identity.owner = changed
        await self.click(request)
        self.assertEqual(self.db.join_request(request)["status"], "requested")
        async def original(*args, **kwargs): return OWNER
        async def stale(*args, **kwargs): return ob.Person("cli_agent", "on_owner", OWNER, 0)
        self.identity.owner, self.identity.resolve = original, stale
        await self.click(request, now=100)
        self.assertEqual(self.db.join_request(request)["status"], "requested")

    async def test_queue_limit_and_no_arbitrary_callback_dictionary(self):
        self.co.queue_limit = 1
        invite = ob.Invite("cli_agent", AGENT, "oc_group", "event", ob.Operator("ou_owner"), "new_binding")
        self.assertEqual(self.co.enqueue_invite(invite, now=10)["toast"]["type"], "info")
        self.assertEqual(self.co.enqueue_invite(invite, now=10)["toast"]["type"], "error")
        with self.assertRaises(ValueError): self.co.enqueue_card({"approved": True}, now=10)

    async def test_wrong_effect_receipt_cannot_complete(self):
        request = await self.invite()
        async def wrong(row): return ob.EffectReceipt(row["request_id"], OTHER, row["chat_id"], True)
        self.effects.readback = wrong
        await self.click(request)
        self.assertEqual(self.db.join_request(request)["status"], "approved")

    async def test_denial_cleanup_survives_card_update_failure_and_retries(self):
        request = await self.invite()
        async def failed(*args): raise RuntimeError("private-canary")
        self.cards.update = failed
        await self.click(request, approved=False)
        self.assertEqual(self.effects.cleaned, [request])
        await self.co.tick(now=100)
        self.assertEqual(self.effects.cleaned, [request, request])

    async def test_effect_exception_updates_human_blocked_card(self):
        request = await self.invite()
        async def failed(*args): raise RuntimeError("private-effect-canary")
        self.effects.apply = failed
        await self.click(request)
        self.assertEqual(self.db.join_request(request)["status"], "approved")
        self.assertIn(("om_card1", "blocked"), self.cards.updated)
        self.assertNotIn("private-effect-canary", self.co.last_notice)

    async def test_done_card_update_failure_retries_after_restart_without_reapply(self):
        request = await self.invite()
        self.effects.verified = True
        original = self.cards.update
        async def failed(*args): raise RuntimeError("private-card-canary")
        self.cards.update = failed
        await self.click(request)
        self.assertEqual(self.db.join_request(request)["status"], "done")
        self.cards.update = original
        other = ob.Coordinator(self.db, self.identity, self.cards, effects=self.effects, clock=lambda: 1000)
        await other.tick(now=1000)
        self.assertIn(("om_card1", "done"), self.cards.updated)
        self.assertNotIn(("om_card1", "blocked"), self.cards.updated)
        self.assertEqual(self.effects.applies, 1)

    async def test_private_unknown_delivery_held_on_replayed_callback(self):
        request = await self.invite()
        async def failed(*args): raise RuntimeError("private-refusal-canary")
        self.cards.refuse = failed
        await self.click(request, owner=OTHER)
        row = self.db.conn.execute("SELECT * FROM join_feedback").fetchone()
        self.assertEqual(row["status"], "unknown")
        self.assertNotIn("private-refusal-canary", "\n".join(self.db.conn.iterdump()))
        async def replayed(*args): self.fail("unknown refusal was blindly resent")
        self.cards.refuse = replayed
        await self.click(request, owner=OTHER)
        self.assertEqual(self.db.join_request(request)["status"], "requested")
        self.assertEqual(self.db.join_request(request)["card_message_id"], "om_card1")

    async def test_refused_event_cannot_become_approval_after_people_binding_changes(self):
        request = await self.invite()
        await self.click(request, owner=OTHER)
        await self.click(request, owner=OWNER)
        self.assertEqual(self.db.join_request(request)["status"], "requested")
        self.assertEqual(self.effects.applies, 0)
        await self.click(request, owner=OWNER, event="fresh-owner-click")
        self.assertEqual(self.db.join_request(request)["status"], "approved")

    async def test_callback_queued_before_deadline_cannot_approve_after_deadline(self):
        request = await self.invite()
        decision = ob.CardDecision("cli_agent", "oc_group", "om_card1", "late", request, 1, True, ob.Operator("ou_owner"))
        self.co.enqueue_card(decision, now=20)
        self.co.clock = lambda: 11 + 7 * 86400
        await self.co.drain()
        self.assertNotEqual(self.db.join_request(request)["status"], "approved")
        self.assertEqual(self.effects.applies, 0)

    async def test_unknown_retry_past_idempotency_window_holds_without_recovery(self):
        self.cards.failure = True
        request = await self.invite()
        self.cards.failure = False
        await self.co.tick(now=10000)
        self.assertEqual(self.cards.sent, [(request, 1)])
        self.assertEqual(self.db.join_request(request)["card_generation"], 0)

    async def test_unknown_verified_card_readback_activates_without_resending(self):
        self.cards.failure = True
        request = await self.invite()
        self.cards.failure = False
        self.cards.recovery_verified = True
        self.cards.recovered_message = "om_recovered"
        await self.co.tick(now=400)
        self.assertEqual(self.cards.sent, [(request, 1)])
        self.assertEqual(self.db.join_request(request)["card_message_id"], "om_recovered")

    def test_feed_decoder_uses_trusted_app_and_actual_context_not_action_claims(self):
        row = {"app": "cli_agent", "type": "card.action.trigger", "event_id": "event1",
               "operator": {"open_id": "ou_owner", "union_id": "on_owner"},
               "context": {"open_chat_id": "oc_group", "open_message_id": "om_card1"},
               "action": {"value": {"request_id": "JOIN-12345678", "generation": 1, "decision": "approve", "app_id": "cli_attacker", "message_id": "om_attacker"}}}
        decision = ob.card_from_feed(row)
        self.assertEqual((decision.app_id, decision.message_id), ("cli_agent", "om_card1"))
        row["type"] = "im.message.reaction.created_v1"
        with self.assertRaises(ValueError): ob.card_from_feed(row)


class ResolverTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_app_contact_and_union_mismatch_or_ambiguous_people_fail(self):
        self.assertIsNotNone(ob)
        class Bot:
            app_id = "cli_agent"
            def __init__(self): self.calls = []
            def call(self, what, args):
                self.calls.append(args)
                return {"user": {"open_id": "ou_owner", "union_id": "on_owner"}}
        bot = Bot()
        answer = ob.gs.PeopleAnswer({}, {OWNER: "on_owner"})
        loader_calls = []
        def people(app, now): loader_calls.append((app, now)); return answer
        resolver = ob.IdentityResolver({"cli_agent": bot}, people, lambda *args: None)
        person = await resolver.resolve("cli_agent", ob.Operator("ou_owner"), now=10)
        self.assertEqual(person.pubkey, OWNER)
        self.assertIn("bot", bot.calls[0])
        self.assertEqual(loader_calls, [("cli_agent", 10)])
        with self.assertRaises(ValueError): await resolver.resolve("cli_agent", ob.Operator("ou_owner", "on_other"), now=11)
        answer.union_ids[OTHER] = "on_owner"
        with self.assertRaises(ValueError): await resolver.resolve("cli_agent", ob.Operator("ou_owner"), now=12)

    async def test_signed_profile_owner_requires_valid_event_and_nip_oa_endorsement(self):
        self.assertIsNotNone(ob)
        nk = ob.gs.sync.nk
        owner_key, agent_key = bytes.fromhex("01".zfill(64)), bytes.fromhex("02".zfill(64))
        owner, agent = nk.pubkey_xonly(owner_key).hex(), nk.pubkey_xonly(agent_key).hex()
        import hashlib
        endorsement = nk.schnorr_sign(hashlib.sha256(f"nostr:agent-auth:{agent}:".encode()).digest(), owner_key, b"\0" * 32).hex()
        event = {"pubkey": agent, "created_at": 10, "kind": 0, "tags": [["auth", owner, "", endorsement]], "content": ""}
        event["id"] = hashlib.sha256(json.dumps([0, agent, 10, 0, event["tags"], ""], separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        event["sig"] = nk.schnorr_sign(bytes.fromhex(event["id"]), agent_key, b"\0" * 32).hex()
        class Bot: app_id = "cli_agent"
        resolver = ob.IdentityResolver({"cli_agent": Bot()}, lambda *args: None, lambda *args: event)
        self.assertEqual(await resolver.owner(agent, "cli_agent", now=10), owner)
        event["tags"][0][3] = "0" * 128
        with self.assertRaises(ValueError): await resolver.owner(agent, "cli_agent", now=10)


class CardTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_card_payload_only_card_buttons_and_failure_remedy(self):
        self.assertIsNotNone(jc)
        row = dict(request_id="JOIN-12345678", kind="new_binding", status="requested", callback_app_id="cli_agent", chat_id="oc_group")
        text = json.dumps(jc.card(row, 1, "blocked"), ensure_ascii=False)
        self.assertIn("AI", text)
        self.assertIn("下一步", text)
        self.assertNotIn("/approve", text)
        requested = jc.card(row, 1, "requested")
        buttons = requested["elements"][-1]["actions"]
        self.assertEqual({b["value"]["decision"] for b in buttons}, {"approve", "deny"})
        self.assertEqual(requested['header']['template'],'orange')
        self.assertIn('已获授的工具和系统权限',json.dumps(requested,ensure_ascii=False))
        self.assertIn('拉进群只是发起申请',json.dumps(requested,ensure_ascii=False))
        for state,color in [('approved','blue'),('blocked','red'),('done','green'),
                            ('denied','red'),('expired','grey'),('superseded','grey')]:
            rendered=jc.card(row,1,state)
            self.assertEqual(rendered['header']['template'],color)
            self.assertNotEqual(rendered['header']['title']['content'],'Agent 接入申请')
            self.assertFalse(any(e['tag']=='action' for e in rendered['elements']))

    async def test_transport_calls_actual_bot_methods_and_stable_send_key(self):
        self.assertIsNotNone(jc)
        class Bot:
            app_id = "cli_agent"
            def __init__(self): self.calls = []
            def send_card(self, chat, content, key): self.calls.append((chat, content, key)); self.content = content; return "om_card"
            def message_view(self, message, identity):
                return {"message_id": message, "chat_id": "oc_group", "sender": {"id": "cli_agent", "id_type": "app_id", "sender_type": "app"}, "body": {"content": self.content}}
            def update_card(self, message, content): self.calls.append((message, content))
            def call(self, what, args): self.calls.append((what, args)); return {"message_id": "om_dm"}
        bot = Bot()
        transport = jc.BotCards({"cli_agent": bot})
        row = dict(request_id="JOIN-12345678", kind="new_binding", status="requested", callback_app_id="cli_agent", chat_id="oc_group")
        self.assertEqual(await transport.send(row, 1), "om_card")
        await transport.send(row, 1)
        self.assertEqual(bot.calls[0][2], bot.calls[1][2])
        await transport.update(row, "om_card", "superseded")
        await transport.dm(row, "on_owner")
        self.assertIn("--as", bot.calls[-1][1])
        self.assertIn("bot", bot.calls[-1][1])
        args = bot.calls[-1][1]
        payload = json.loads(args[args.index('--data') + 1])
        text = json.loads(payload['content'])['text']
        self.assertIn('等待您审批', text)
        self.assertIn('https://applink.feishu.cn/client/chat/open?openChatId=oc_group', text)
        self.assertNotIn(jc.NOTICE, text)
        self.assertNotIn('暂时无法继续', text)
        for state in ('requested', 'approved', 'blocked', 'done'):
            self.assertIn('openChatId=oc_group', json.dumps(jc.card(row, 1, state)))

    async def test_ambiguous_own_bot_card_body_cannot_prove_absence(self):
        self.assertIsNotNone(jc)
        class Bot:
            app_id = "cli_agent"
            def messages(self, *args):
                return ([{"message_id": "om_card", "chat_id": "oc_group", "sender": {"id": "cli_agent", "id_type": "app_id", "sender_type": "app"}, "body": {"content": "unknown flattened card body"}}], False)
        row = dict(request_id="JOIN-12345678", created_at=10, callback_app_id="cli_agent", chat_id="oc_group")
        result = await jc.BotCards({"cli_agent": Bot()}).recover(row, 1)
        self.assertFalse(result.verified)

    async def test_private_refusal_real_bot_call_idempotent_and_readback_checked(self):
        class Bot:
            app_id = "cli_agent"
            def __init__(self): self.calls = []; self.bad = False
            def call(self, what, args):
                self.calls.append(args)
                self.data = json.loads(args[args.index("--data") + 1])
                return {"message_id": "om_private", "chat_id": "oc_private", "sender": {"id": "cli_agent", "id_type": "app_id", "sender_type": "app"}}
            def message_view(self, message, kind):
                return {"message_id": message, "chat_id": "oc_other" if self.bad else "oc_private", "sender": {"id": "cli_agent", "id_type": "app_id", "sender_type": "app"}, "body": {"content": self.data["content"]}}
        bot = Bot()
        row = dict(request_id="JOIN-12345678", callback_app_id="cli_agent", chat_id="oc_group")
        transport = jc.BotCards({"cli_agent": bot})
        await transport.refuse(row, "on_other", "event1", "owner")
        first = bot.data.copy()
        await transport.refuse(row, "on_other", "event1", "owner")
        self.assertEqual(first["uuid"], bot.data["uuid"])
        self.assertEqual(bot.data["receive_id"], "on_other")
        self.assertIn("怎么解决", bot.data["content"])
        self.assertIn("bot", bot.calls[0])
        bot.bad = True
        with self.assertRaises(ValueError): await transport.refuse(row, "on_other", "event1", "owner")


class TransportStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "private" / "hostd.db"
        self.db = store.Store(self.path)
        self.addCleanup(self.db.close)
        self.db.register_agent(AGENT, owner_pubkey=OWNER, app_id="cli_agent", now=10)
        self.db.create_join("JOIN-12345678", AGENT, OWNER, "cli_agent", "oc_group", kind="new_binding", now=10)

    def test_concurrent_card_reservation_one_writer_and_lease_recovery(self):
        def reserve():
            with store.Store(self.path) as db:
                return db.reserve_join_card("JOIN-12345678", now=10)
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            result = list(pool.map(lambda _: reserve(), range(2)))
        self.assertEqual(result.count(1), 1)
        self.assertEqual(result.count(None), 1)
        self.assertIsNone(self.db.reserve_join_card("JOIN-12345678", now=69))
        self.assertEqual(self.db.reserve_join_card("JOIN-12345678", now=310), 1)
        self.assertTrue(self.db.finish_join_card("JOIN-12345678", 1, "om_card1", now=310))
        self.assertFalse(self.db.finish_join_card("JOIN-12345678", 1, "om_other", now=311))

    def test_unknown_definite_failed_metadata_and_monotonic_due(self):
        self.assertEqual(self.db.reserve_join_card("JOIN-12345678", now=10), 1)
        self.db.fail_join_card("JOIN-12345678", 1, definite=False, now=10)
        self.assertEqual(self.db.join_transport("JOIN-12345678")["send_status"], "unknown")
        self.assertIsNone(self.db.reserve_join_card("JOIN-12345678", now=9))
        self.assertEqual(self.db.reserve_join_card("JOIN-12345678", now=310), 1)
        self.db.fail_join_card("JOIN-12345678", 1, definite=True, now=310)
        self.assertEqual(self.db.join_transport("JOIN-12345678")["send_status"], "failed")
        self.assertIsNone(self.db.reserve_join_card("JOIN-12345678", now=311))
        self.assertEqual(self.db.reserve_join_card("JOIN-12345678", now=910), 1)
        self.assertTrue(self.db.reserve_join_dm("JOIN-12345678", now=910))
        self.assertFalse(self.db.reserve_join_dm("JOIN-12345678", now=911))

    def test_version_one_exact_schema_migrates_without_metadata_loss(self):
        self.db.close()
        self.path.unlink()
        db = sqlite3.connect(self.path)
        db.executescript(store._SCHEMA_V1)
        db.execute("PRAGMA user_version=1")
        db.execute("INSERT INTO agent VALUES(?,?,?,NULL,'active',10)", (AGENT, OWNER, "cli_agent"))
        db.commit(); db.close()
        self.path.chmod(0o600)
        migrated = store.Store(self.path)
        self.addCleanup(migrated.close)
        self.assertEqual(migrated.conn.execute("PRAGMA user_version").fetchone()[0], store.SCHEMA_VERSION)
        self.assertEqual(migrated.conn.execute("SELECT owner_pubkey FROM agent").fetchone()[0], OWNER)

    def test_mutated_version_one_schema_is_rejected_without_upgrade(self):
        self.db.close()
        self.path.unlink()
        db = sqlite3.connect(self.path)
        db.executescript(store._SCHEMA_V1)
        db.execute("CREATE TABLE unexpected(body TEXT)")
        db.execute("PRAGMA user_version=1"); db.commit(); db.close()
        self.path.chmod(0o600)
        with self.assertRaises(store.StoreError): store.Store(self.path)
        db = sqlite3.connect(self.path)
        self.addCleanup(db.close)
        self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 1)

    def test_notice_private_feedback_and_dm_reservations_concurrent_cas(self):
        def reserve(which):
            with store.Store(self.path) as db:
                if which == "dm": return db.reserve_join_dm("JOIN-12345678", now=10)
                return db.reserve_join_feedback("JOIN-12345678", "cli_agent", "event1", "on_owner", reason="owner", now=10)
        for which in ("dm", "feedback"):
            with concurrent.futures.ThreadPoolExecutor(2) as pool:
                self.assertEqual(list(pool.map(reserve, [which, which])).count(True), 1)
        self.db.reserve_join_card("JOIN-12345678", now=10)
        self.db.finish_join_card("JOIN-12345678", 1, "om_card1", now=10)
        self.db.decide_join("JOIN-12345678", "yes", OWNER, "cli_agent", "om_card1", 1, approved=True, now=10)
        self.db.queue_join_notice("JOIN-12345678", "om_card1", "blocked", now=10)
        def notice(_):
            with store.Store(self.path) as db: return db.reserve_join_notice("JOIN-12345678", "om_card1", now=10)
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            self.assertEqual(list(pool.map(notice, range(2))).count("blocked"), 1)
        self.assertIsNone(self.db.reserve_join_notice("JOIN-12345678", "om_card1", now=69))
        self.assertEqual(self.db.reserve_join_notice("JOIN-12345678", "om_card1", now=310), "blocked")

    def test_version_one_migration_preserves_every_existing_table_row(self):
        self.db.close()
        self.path.unlink()
        raw = sqlite3.connect(self.path)
        raw.executescript(store._SCHEMA_V1)
        raw.execute("INSERT INTO agent VALUES(?,?,?,NULL,'active',10)", (AGENT, OWNER, "cli_agent"))
        raw.execute("INSERT INTO join_request VALUES('JOIN-12345678',?,?, 'cli_agent','oc_group',NULL,'new_binding','requested',NULL,0,10,10,604810)", (AGENT, OWNER))
        raw.execute("INSERT INTO app_profile VALUES('cli_agent','/private/config','/private/data')")
        raw.execute("""INSERT INTO binding VALUES('alpha','channel','oc_group','cli_agent','/cfg','', '', 'active',10,10)""")
        raw.execute("INSERT INTO cursor VALUES('alpha','','feishu',42,10)")
        raw.execute("""INSERT INTO delivery VALUES(?, 'alpha',NULL,'om_source','f2b','feishu',?,'','','acked',42,1,10,10)""", ("d" * 64, "e" * 64))
        raw.execute("INSERT INTO state_snapshot VALUES('alpha',7,?,'/legacy/state.json',?,'legacy:cli_agent:union_id',10)", ("f" * 64, "0" * 64))
        raw.execute("INSERT INTO state_map VALUES('alpha','b2f',?,'om_target',NULL)", ("e" * 64,))
        raw.execute("PRAGMA user_version=1"); raw.commit()
        names = [row[0] for row in raw.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        before = {name: raw.execute('SELECT * FROM ' + name).fetchall() for name in names}
        raw.close()
        self.path.chmod(0o600)
        with store.Store(self.path) as migrated:
            after = {name: [tuple(row) for row in migrated.conn.execute('SELECT * FROM ' + name)] for name in names}
            self.assertEqual(before, after)


if __name__ == "__main__": unittest.main()
