"""Own-agent remote message delivery: real domain adapters, fake wire IO only."""
import asyncio
import base64
import concurrent.futures
import dataclasses
import hashlib
import importlib
import json
import re
from pathlib import Path
import sys
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_hostd_remote_mapping as fixture
import buzz_feishu_group_sync as gs
import recovery_authority as authority
from hostd import remote_approval, store
from hostd.agent_catalog import AgentRecord
from hostd.agent_signed_reads import OwnAgentReader
from hostd.bot_clients import BotLarkCli, BotCliError
from hostd.delivery_mapping import message_card
from hostd.remote_mapping import RemoteMappingContext
from hostd.remote_target import RemoteTarget

CHANNEL, CHAT, APP, ORIGIN, NOW = fixture.CHANNEL, fixture.CHAT, fixture.APP, fixture.ORIGIN, fixture.NOW
PUB, OWNER, MIRROR, MOWNER, PIN = fixture.PUB, fixture.OWNER, fixture.MIRROR, fixture.FOREIGN_OWNER, fixture.PIN


class WireWorld:
    """Synthetic signed relay and native BotLarkCli HTTP request boundary."""
    runner = fixture.RemoteMapping.runner

    def metadata(self):
        rows = fixture.RemoteMapping.metadata(self)
        for claim in self.claims[1:]:
            rows.append(gs.sign_event(fixture.RELAY_KEY, 39002,
                [["d", claim["channel"]]]+[["p", pk, "", role] for pk, role in self.roles.items()], "", NOW))
        return rows

    def __init__(self, case):
        self.case = case
        fixture.RemoteMapping.setUp(self)
        self.now = NOW
        self.posts = []
        self.api_calls = []
        self.response_only = self.lose_response = self.revoke_after_post = False
        self.send_entered = threading.Event()
        self.send_release = threading.Event()
        self.block_send = False
        self.db = None
        self.reader = OwnAgentReader(self.record(), origin=ORIGIN, relay_pubkey=PIN,
            trusted_relays=(ORIGIN,), clock=lambda: self.now, http=self.http)
        self.bot = BotLarkCli(APP, self.cfg, self.data, base_env={"HOME": str(self.root), "PATH": "/usr/bin"},
                             http_pool=self, chat_id=CHAT)
        self.env.write_text(self.env.read_text() + "BUZZ_ACP_CHANNELS=" + CHANNEL + "\n")
        self.approval = self.approval_event()
        self.events.append(self.approval)
        self.targets = {CHANNEL: self.target}

    def __getattr__(self, name):
        return getattr(self.case, name)

    def record(self):
        return AgentRecord("Synthetic", self.env, "synthetic.service", OWNER, PUB, tuple(c["channel"] for c in self.claims),
            None, None, None, "synthetic", (), APP, self.cfg, self.data,
            hashlib.sha256(self.env.read_bytes()).hexdigest(),
            hashlib.sha256((self.cfg / "config.json").read_bytes()).hexdigest(), True, "own_bot_verified", ())

    def approval_event(self, channel=CHANNEL, chat=CHAT):
        body = dict(version=1, decision="approve", agent_pubkey=PUB, agent_owner_pubkey=OWNER,
            app_id=APP, channel_id=channel, chat_ref=gs.chat_ref(chat), mirror_pubkey=MIRROR,
            mirror_owner_pubkey=MOWNER, claimed_at=NOW-100, request_id="JOIN-1234abcd",
            request_created_at=NOW-10, request_deadline=NOW-10+7*86400, card_generation=1,
            card_message_sha256=hashlib.sha256(b"om_card").hexdigest(),
            decision_event_sha256=hashlib.sha256(b"evt_decision").hexdigest(), decision_at=NOW-1)
        raw = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        digest = hashlib.sha256(raw.encode()).hexdigest()
        return gs.sign_event(fixture.MIRROR_KEY, 30078, [["t", "hostd-card-approval-v1"],
            ["h", channel], ["p", PUB], ["d", "hostd-card-approval-v1:"+digest]], raw, NOW)

    def second_target(self):
        channel = "00000000-0000-0000-0000-000000000002"; chat = "oc_second"
        self.claims.append(dict(channel=channel, chat_ref=gs.chat_ref(chat), claimed_at=NOW-100, heartbeat=NOW))
        self.directory = self.metadata()
        policy = next(e for e in self.directory if e["kind"] == 30177 and e["pubkey"] == MOWNER)
        self.target = dataclasses.replace(self.target, claim_event_id=policy["id"])
        target = RemoteTarget(PUB, OWNER, APP, channel, chat, gs.chat_ref(chat), ORIGIN,
                              MIRROR, MOWNER, policy["id"], NOW-100, NOW, NOW)
        self.targets = {CHANNEL: self.target, channel: target}
        self.events.append(self.approval_event(channel, chat))
        self.env.write_text(self.env.read_text().replace("BUZZ_ACP_CHANNELS="+CHANNEL,
                                                       "BUZZ_ACP_CHANNELS="+CHANNEL+","+channel))
        return target

    def http(self, url, headers, timeout, *, body=None):
        self.wire_calls.append((url, headers, body))
        self.assertEqual(url, ORIGIN+"/query")
        signed = json.loads(base64.b64decode(headers["Authorization"].split(" ", 1)[1]))
        self.assertTrue(gs._nip01_event_verified(signed))
        self.assertEqual(signed["pubkey"], PUB)
        authority.exact_tag(signed, "u", url)
        authority.exact_tag(signed, "method", "POST")
        authority.exact_tag(signed, "payload", hashlib.sha256(body).hexdigest())
        self.assertEqual(json.loads(headers["x-auth-tag"]), fixture.auth(fixture.KEY, fixture.OWNER_KEY))
        output = []
        for filt in json.loads(body):
            rows = []
            for event in self.directory + self.events:
                if "kinds" in filt and event["kind"] not in filt["kinds"]: continue
                if "authors" in filt and event["pubkey"] not in filt["authors"]: continue
                if "ids" in filt and event["id"] not in filt["ids"]: continue
                if event["created_at"] < filt.get("since", 0) or event["created_at"] > filt.get("until", 2**63-1): continue
                if any(k.startswith("#") and not any(len(t)>1 and t[0]==k[1:] and t[1] in values for t in event["tags"])
                       for k, values in filt.items()): continue
                rows.append(event)
            output.extend(rows[:filt.get("limit", 256)])
        output = json.loads(json.dumps(output))
        if self.bad_sig and output: output[0]["sig"] = "0"*128
        return self.status, json.dumps(output).encode()

    def request(self, app, config, data_dir, method, path, *, params=None, data=None, chat_id=None, priority=None):
        self.assertEqual((app, Path(config), Path(data_dir)), (APP, self.cfg, self.data))
        self.assertIn(chat_id, {target.chat_id for target in self.targets.values()})
        self.api_calls.append((method, path, params, data))
        if method == "POST":
            self.assertIsNotNone(self.db)
            observed = concurrent.futures.Future()
            source = re.search(r"[?&]e=([0-9a-f]{64})", data["content"]).group(1)
            def sql_observation():
                try:
                    row = self.db.conn.execute("SELECT status FROM remote_delivery WHERE source_id=?", (source,)).fetchone()
                    observed.set_result((row[0] if row else None, self.db.conn.in_transaction))
                except Exception as exc: observed.set_exception(exc)
            self.loop.call_soon_threadsafe(sql_observation)
            status, in_transaction = observed.result(timeout=2)
            self.assertEqual(status, "unknown", "durable uncertainty must precede external dispatch")
            self.assertFalse(in_transaction, "SQL must commit on owning loop before HTTP dispatch")
            self.posts.append((path, params, dict(data)))
            self.assertEqual(data["msg_type"], "interactive")
            self.assertRegex(data["uuid"], r"^[A-Za-z0-9_-]{1,64}$")
            parent = None
            if path.endswith("/reply"):
                parent = path.split("/")[-2]
                self.assertTrue(data["reply_in_thread"])
                self.assertIn(parent, self.messages)
            else:
                self.assertEqual(path, "/open-apis/im/v1/messages")
                self.assertEqual(params, {"receive_id_type": "chat_id"})
                self.assertEqual(data["receive_id"], chat_id)
            self.send_entered.set()
            if self.block_send and not self.send_release.wait(3):
                raise BotCliError("synthetic blocked", 500, "network", definite=False)
            mid = "om_sent"+str(len(self.posts))
            if not self.response_only:
                self.messages[mid] = dict(message_id=mid, chat_id=chat_id, root_id=parent or mid,
                    thread_id="omt_"+(parent or mid), create_time=str(self.now*1000), msg_type="interactive",
                    sender={"sender_type": "app", "id_type": "app_id", "id": APP}, body={"content": data["content"]})
            if self.revoke_after_post:
                self.policy_app = "cli_revoked"
                self.directory = self.metadata()
            if self.lose_response:
                raise BotCliError("synthetic lost response", 500, "network", definite=False)
            result = {"message_id": mid}
        elif path.endswith("/members/list"):
            bots = [{"member_id": "ou_own", "app_id": APP}]
            result = {"users": [], "bots": bots, "truncations": [], "user_total": 0, "bot_total": 1, "has_more": False}
        elif path == "/open-apis/im/v1/messages":
            params = params or {}
            rows = list(self.messages.values())
            if params.get("container_id_type") == "thread":
                rows = [row for row in rows if row.get("thread_id") == params.get("container_id")
                        or row.get("root_id") == params.get("container_id", "").removeprefix("omt_")]
            elif params.get("only_thread_root_messages"):
                rows = [row for row in rows if row["chat_id"] == params.get("container_id")
                        and (not row.get("root_id") or row["root_id"] == row["message_id"])]
            result = {"items": rows+self.history_extra, "has_more": self.incomplete, "page_token": "same"}
        else:
            mid = path.rsplit("/", 1)[-1]
            result = {"items": [self.messages[mid]] if mid in self.messages else []}
        return {"ok": True, "identity": "bot", "data": result}

    def event(self, *, kind=9, key=fixture.KEY, tags=(), text="SYNTHETIC_BODY_DO_NOT_CACHE", at=NOW):
        event = gs.sign_event(key, kind, [["h", CHANNEL], *tags], text, at)
        self.events.append(event)
        return event

    def card(self, event, mid="om_root", *, root=None, app=APP):
        row = fixture.RemoteMapping.card(self, event, mid, root=root, app=app)
        row.update(create_time=str(self.now*1000), thread_id="omt_"+(root or mid))
        return row

    def mirrored(self):
        event = fixture.RemoteMapping.mirrored(self)
        self.messages["om_human"].update(create_time=str(self.now*1000), thread_id="omt_om_human")
        return event

    async def evidence(self, record, channel):
        self.assertEqual((record.pubkey, record.app_id), (PUB, APP))
        target = self.targets[channel]
        try:
            rows = await self.reader.query([{"kinds": [0, 30177, 39002, 30078], "limit": 257}])
            selected = lambda kind, pub: authority.latest([e for e in rows if e["kind"] == kind and e["pubkey"] == pub])
            profile, policy = selected(0, PUB), selected(30177, OWNER)
            roster = authority.latest([e for e in rows if e["kind"] == 39002 and ["d", channel] in e["tags"]])
            mirror_profile, mirror_policy = selected(0, MIRROR), selected(30177, MOWNER)
            scope = remote_approval.ApprovalScope(PUB, OWNER, APP, channel, target.chat_ref, MIRROR, MOWNER, NOW-100)
            context = remote_approval.ProofContext(scope, PIN, profile, policy, (mirror_profile,), (mirror_policy,),
                                                   roster, self.now, complete=True)
            approval_event = authority.latest([e for e in rows if e["kind"] == 30078 and ["h", channel] in e["tags"]])
            approval = remote_approval.verify(approval_event, context)
            listing = await asyncio.to_thread(self.bot.member_listing, target.chat_id, "union_id")
            if not listing.complete or APP not in listing.bots: return None
            return store.RemoteGrantEvidence(PUB, OWNER, APP, channel, target.chat_id, target.chat_ref, ORIGIN,
                MIRROR, MOWNER, mirror_policy["id"], profile["id"], policy["id"], roster["id"],
                hashlib.sha256(self.env.read_bytes()).hexdigest(), "mirror_approval", approval.record_id,
                approval.content_hash, ("message", "edit", "reaction_add", "reaction_remove"),
                self.now, self.now+30, NOW-100)
        except (remote_approval.ApprovalPending, ValueError):
            return None


class RemoteOutletTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.world = WireWorld(self)
        self.db = store.Store(self.world.root/"metadata"/"state.db")
        self.addCleanup(self.db.close)
        self.world.db = self.db
        self.record = self.world.record()
        self.db.register_agent(PUB, owner_pubkey=OWNER, app_id=APP,
                               config_path=str(self.world.env), now=NOW)

    async def grant(self):
        self.world.loop = asyncio.get_running_loop()
        evidence = await self.world.evidence(self.record, CHANNEL)
        self.assertIsNotNone(evidence, "actual synthetic signed proof must validate")
        return self.db.activate_remote_grant(evidence, expected_revision=0, now=self.world.now)

    def outlet(self, *, db=None, record=None, reader=None, bot=None, authorize=None, target=None):
        try: module = importlib.import_module("hostd.remote_outlet")
        except ImportError: module = None
        self.assertIsNotNone(module, "remote message/reply adapter absent: genuine tests-first RED")
        context = RemoteMappingContext(target or self.world.target, reader=self.world.reader, bot_client=bot or self.world.bot,
                                       clock=lambda: self.world.now)
        return module.RemoteOutlet(db or self.db, record or self.record, reader or self.world.reader,
            bot or self.world.bot, context, authorize=authorize or self.world.evidence,
            link_base=ORIGIN, clock=lambda: self.world.now)

    def pending(self, result):
        self.assertEqual(result.status, "pending")
        self.assertIn("怎么解决", result.notice)
        self.assertIn("复制给 AI", result.notice)
        self.assertNotIn("SYNTHETIC_BODY", repr(result))
        self.assertNotIn(fixture.KEY, repr(result))

    def row(self, grant, event):
        return self.db.remote_delivery_by_source(grant.target_id, event["id"], "message")

    def reserve(self, grant, event):
        card = message_card(event, self.record.name, event["content"], ORIGIN, CHANNEL)
        return self.db.reserve_remote_delivery(grant.target_id, event["id"], "message", revision=grant.revision,
            scope_hash=grant.scope_hash, source_at=event["created_at"], content_hash=hashlib.sha256(card.encode()).hexdigest(),
            root_id=gs.buzz_thread_root(event) or "", now=self.world.now).record

    async def test_top_level_own_native_post_and_actual_get_ack_without_foreign_graph(self):
        grant = await self.grant(); event = self.world.event()
        result = await self.outlet().deliver(event, grant.target_id)
        self.assertEqual(result.status, "acked")
        self.assertEqual(self.row(grant, event).message_id, "om_sent1")
        self.assertEqual(len(self.world.posts), 1)
        self.assertTrue(any(method == "GET" and path.endswith("/om_sent1") for method, path, _, _ in self.world.api_calls))
        self.assertEqual(self.db.bindings(), [])
        self.assertEqual(self.db.conn.execute("SELECT count(*) FROM app_profile").fetchone()[0], 0)
        self.assertFalse(result.readback()["live_verified"])
        self.assertNotIn("SYNTHETIC_BODY", "\n".join(self.db.conn.iterdump()))

    async def test_reply_uses_verified_mirror_root_actual_reply_endpoint_and_root_receipt(self):
        grant = await self.grant(); root = self.world.mirrored()
        event = self.world.event(tags=[["e", root["id"], "", "root"], ["e", root["id"], "", "reply"]])
        result = await self.outlet().deliver(event, grant.target_id)
        self.assertEqual(result.status, "acked")
        self.assertEqual(self.world.posts[0][0], "/open-apis/im/v1/messages/om_human/reply")
        self.assertEqual(self.world.messages["om_sent1"]["root_id"], "om_human")
        self.assertEqual(self.row(grant, event).root_id, root["id"])

    async def test_unverified_reply_root_never_falls_back_to_top_level(self):
        grant = await self.grant(); event = self.world.event(tags=[["e", "a"*64, "", "root"]])
        self.pending(await self.outlet().deliver(event, grant.target_id))
        self.assertFalse(self.world.posts)

    async def test_resolver_discovery_without_existing_sql_grant_never_dispatches(self):
        event = self.world.event()
        self.pending(await self.outlet().deliver(event, "a"*64))
        self.assertFalse(self.world.posts)
        self.assertEqual(self.db.conn.execute("SELECT count(*) FROM remote_target").fetchone()[0], 0)

    async def test_wrong_author_channel_or_signature_rejected_without_write(self):
        grant = await self.grant(); outlet = self.outlet()
        foreign = self.world.event(key=fixture.HUMAN_KEY)
        wrong = gs.sign_event(fixture.KEY, 9, [["h", "00000000-0000-0000-0000-000000000002"]], "synthetic", NOW)
        bad = self.world.event(); bad["sig"] = "0"*128
        for event in (foreign, wrong, bad):
            with self.subTest(event_id=event["id"]): self.pending(await outlet.deliver(event, grant.target_id))
        self.assertFalse(self.world.posts)

    async def test_wrong_catalog_app_or_reader_identity_is_not_own_bot_authority(self):
        grant = await self.grant(); event = self.world.event()
        for record in (dataclasses.replace(self.record, app_id="cli_other"),
                       dataclasses.replace(self.record, owner_pubkey=fixture.HUMAN),
                       dataclasses.replace(self.record, lark_config_dir=self.world.root/"foreign-config"),
                       dataclasses.replace(self.record, lark_data_dir=self.world.root/"foreign-data"),
                       dataclasses.replace(self.record, env_file=self.world.root/"foreign.env"),
                       dataclasses.replace(self.record, channels=()),
                       dataclasses.replace(self.record, local_bot_verified=False)):
            with self.subTest(record=record.app_id):
                self.pending(await self.outlet(record=record).deliver(event, grant.target_id))
        self.world.policy_app = "cli_revoked"; self.world.directory = self.world.metadata()
        self.pending(await self.outlet().deliver(event, grant.target_id))
        self.assertFalse(self.world.posts)

    async def test_repeat_source_has_one_dispatch_and_immutable_ack(self):
        grant = await self.grant(); event = self.world.event(); outlet = self.outlet()
        first = await outlet.deliver(event, grant.target_id)
        second = await outlet.deliver(event, grant.target_id)
        self.assertEqual((first.status, second.status), ("acked", "acked"))
        self.assertEqual(len(self.world.posts), 1)
        self.assertEqual(self.db.conn.execute("SELECT count(*) FROM remote_delivery").fetchone()[0], 1)

    async def test_transport_uuid_bounded_stable_for_create_reply_and_unknown_restart(self):
        grant = await self.grant()
        for reply in (False, True):
            with self.subTest(reply=reply):
                root = self.world.mirrored() if reply else None
                tags = [["e", root["id"], "", "root"], ["e", root["id"], "", "reply"]] if root else []
                event = self.world.event(tags=tags); self.world.lose_response = True
                self.pending(await self.outlet().deliver(event, grant.target_id))
                original = self.row(grant, event); post = self.world.posts[-1]
                self.assertLessEqual(len(post[2]["uuid"]), 50)
                self.assertEqual(post[2]["uuid"], "hr-"+original.id[:40])
                count = len(self.world.posts)
                self.db.close(); self.db = store.Store(self.world.root/"metadata"/"state.db")
                self.addCleanup(self.db.close); self.world.db = self.db; self.world.lose_response = False
                self.world.now += 60
                self.assertEqual((await self.outlet().deliver(event, grant.target_id)).status, "acked")
                self.assertEqual((await self.outlet().deliver(event, grant.target_id)).status, "acked")
                self.assertEqual((len(self.world.posts), self.world.posts[-1][2]["uuid"]), (count, post[2]["uuid"]))
                self.assertEqual((self.row(grant, event).id, self.row(grant, event).revision),
                                 (original.id, original.revision))
                self.assertEqual(self.db.remote_grant(grant.target_id), grant)

    async def test_awaited_verifier_cannot_dispatch_after_protected_files_change(self):
        grant = await self.grant()
        for file in (self.world.env, self.world.cfg/"config.json"):
            with self.subTest(file=file.name):
                event = self.world.event(text="SYNTHETIC_BODY_DO_NOT_CACHE "+file.name)
                self.world.posts.clear()
                original = file.read_bytes()
                async def changed(record, channel):
                    evidence = await self.world.evidence(record, channel)
                    file.write_bytes(original+b"\n")
                    return evidence
                try:
                    self.pending(await self.outlet(authorize=changed).deliver(event, grant.target_id))
                    self.assertFalse(self.world.posts, "awaited proof cannot authorize changed protected files")
                finally:file.write_bytes(original)

    async def test_awaited_verifier_cannot_ack_after_protected_files_change(self):
        grant = await self.grant(); event = self.world.event(); file = self.world.env
        original = file.read_bytes(); calls = 0
        async def changed(record, channel):
            nonlocal calls
            evidence = await self.world.evidence(record, channel); calls += 1
            if calls == 2:file.write_bytes(original+b"\n")
            return evidence
        try:
            self.pending(await self.outlet(authorize=changed).deliver(event, grant.target_id))
            self.assertEqual(len(self.world.posts), 1)
            self.assertEqual(self.row(grant, event).status, "unknown")
        finally:file.write_bytes(original)

    async def test_mismatched_or_malformed_public_origin_rejected_before_transport(self):
        grant = await self.grant()
        for origin in ("https://wrong.test", ORIGIN+"/extra", "https://127.0.0.1:bad",
                       "https://127.0.0.1:65536", "https://127.0.0.1%2e:9443"):
            with self.subTest(origin=origin):
                event = self.world.event(text="SYNTHETIC_BODY_DO_NOT_CACHE "+origin)
                outlet = self.outlet(); outlet.link_base = origin
                if origin != "https://wrong.test":
                    # Matching pairs still require the mapping context's strict
                    # origin validation; equality alone is insufficient.
                    outlet.mappings.link_base = origin
                self.world.posts.clear()
                self.world.wire_calls.clear(); self.world.api_calls.clear()
                self.pending(await outlet.deliver(event, grant.target_id))
                self.assertFalse(self.world.posts)
                self.assertEqual((self.world.wire_calls, self.world.api_calls), ([], []))

    async def test_handler_success_without_actual_readable_message_keeps_unknown(self):
        grant = await self.grant(); event = self.world.event(); self.world.response_only = True
        self.pending(await self.outlet().deliver(event, grant.target_id))
        self.assertEqual(self.row(grant, event).status, "unknown")
        self.assertEqual(len(self.world.posts), 1)

    async def test_lost_create_response_reopens_and_adopts_unique_actual_receipt_without_post(self):
        grant = await self.grant(); event = self.world.event(); self.world.lose_response = True
        self.pending(await self.outlet().deliver(event, grant.target_id))
        original = self.row(grant, event); self.assertEqual(original.status, "unknown")
        self.db.close(); self.db = store.Store(self.world.root/"metadata"/"state.db")
        self.addCleanup(self.db.close); self.world.db = self.db; self.world.lose_response = False
        result = await self.outlet().deliver(event, grant.target_id)
        self.assertEqual(result.status, "acked")
        self.assertEqual((self.row(grant, event).id, len(self.world.posts)), (original.id, 1))

    async def test_lost_reply_response_recovers_complete_thread_history_not_root_only_list(self):
        grant = await self.grant(); root = self.world.mirrored()
        event = self.world.event(tags=[["e", root["id"], "", "root"], ["e", root["id"], "", "reply"]])
        self.world.lose_response = True
        self.pending(await self.outlet().deliver(event, grant.target_id)); self.world.lose_response = False
        result = await self.outlet().deliver(event, grant.target_id)
        self.assertEqual(result.status, "acked"); self.assertEqual(len(self.world.posts), 1)
        self.assertTrue(any(params and params.get("container_id_type") == "thread" for _, _, params, _ in self.world.api_calls))

    async def test_restored_reserved_crash_never_dispatches_even_with_complete_absence(self):
        grant = await self.grant(); event = self.world.event(); original = self.reserve(grant, event)
        self.db.close(); self.db = store.Store(self.world.root/"metadata"/"state.db")
        self.addCleanup(self.db.close); self.world.db = self.db
        self.pending(await self.outlet().deliver(event, grant.target_id))
        self.assertFalse(self.world.posts)
        self.assertEqual(self.row(grant, event).id, original.id)
        self.assertIn(self.row(grant, event).status, ("reserved", "unknown"))

    async def test_unknown_ambiguous_incomplete_wrong_sender_or_card_digest_never_reposts(self):
        grant = await self.grant(); event = self.world.event(); op = self.reserve(grant, event)
        self.db.mark_remote_unknown(op.id, revision=grant.revision, scope_hash=grant.scope_hash, now=NOW)
        for mode in ("absent", "incomplete", "duplicate", "wrong_sender", "wrong_body", "wrong_chat"):
            with self.subTest(mode=mode):
                self.world.messages.clear(); self.world.incomplete = mode == "incomplete"
                if mode not in ("absent", "incomplete"):
                    row = self.world.card(event)
                    if mode == "duplicate": self.world.card(event, "om_other")
                    if mode == "wrong_sender": row["sender"]["id"] = "cli_other"
                    if mode == "wrong_chat": row["chat_id"] = "oc_other"
                    if mode == "wrong_body":
                        doc = json.loads(row["body"]["content"]); doc["elements"][0]["text"]["content"] = "tampered"
                        row["body"]["content"] = json.dumps(doc)
                self.pending(await self.outlet().deliver(event, grant.target_id))
                self.assertEqual(self.row(grant, event).status, "unknown")
        self.assertFalse(self.world.posts)

    async def test_fresh_same_authority_after_original_expiry_acks_unknown_without_repin(self):
        grant = await self.grant(); event = self.world.event(); op = self.reserve(grant, event)
        self.db.mark_remote_unknown(op.id, revision=grant.revision, scope_hash=grant.scope_hash, now=NOW)
        self.world.card(event); self.world.now += 60
        result = await self.outlet().deliver(event, grant.target_id)
        self.assertEqual(result.status, "acked"); self.assertFalse(self.world.posts)
        self.assertEqual(self.row(grant, event).revision, grant.revision)
        self.assertEqual(self.db.remote_grant(grant.target_id), grant)
        self.assertEqual(self.db.remote_proof(grant.target_id).evidence.checked_at, NOW+60)

    async def test_revocation_during_dispatched_io_keeps_unknown_and_no_ack(self):
        grant = await self.grant(); event = self.world.event(); self.world.revoke_after_post = True
        self.pending(await self.outlet().deliver(event, grant.target_id))
        self.assertEqual(self.row(grant, event).status, "unknown")
        self.assertEqual(len(self.world.posts), 1)

    async def test_cancel_reaps_actual_dispatched_http_and_keeps_unknown_until_recovery(self):
        grant = await self.grant(); event = self.world.event(); self.world.block_send = True
        task = asyncio.create_task(self.outlet().deliver(event, grant.target_id))
        self.addCleanup(self.world.send_release.set)
        try:
            # Arrival rendezvous only: pure-Python signed fixtures can take
            # longer than 5s. Actual blocked IO/reap assertions remain below.
            self.assertTrue(await asyncio.to_thread(self.world.send_entered.wait, 30))
            task.cancel(); await asyncio.sleep(0.01); task.cancel(); await asyncio.sleep(0.01)
            self.assertFalse(task.done(), "cancel cannot release resources before real IO returns")
        finally: self.world.send_release.set()
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertEqual(self.row(grant, event).status, "unknown")
        self.world.block_send = False
        self.assertEqual((await self.outlet().deliver(event, grant.target_id)).status, "acked")
        self.assertEqual(len(self.world.posts), 1)

    async def test_separate_targets_for_same_agent_serialize_and_keep_loop_responsive(self):
        target = self.world.second_target(); self.record = self.world.record()
        grant = await self.grant()
        second_evidence = await self.world.evidence(self.record, target.channel_id)
        self.assertIsNotNone(second_evidence)
        second_grant = self.db.activate_remote_grant(second_evidence, expected_revision=0, now=NOW)
        first = self.world.event(text="first synthetic")
        second = gs.sign_event(fixture.KEY, 9, [["h", target.channel_id]], "second synthetic", NOW)
        self.world.events.append(second); self.world.block_send = True
        second_bot = BotLarkCli(APP, self.world.cfg, self.world.data,
            base_env={"HOME": str(self.world.root), "PATH": "/usr/bin"}, http_pool=self.world, chat_id=target.chat_id)
        tasks = [asyncio.create_task(self.outlet().deliver(first, grant.target_id))]
        self.addCleanup(self.world.send_release.set)
        try:
            self.assertTrue(await asyncio.to_thread(self.world.send_entered.wait, 30))
            tasks.append(asyncio.create_task(self.outlet(bot=second_bot, target=target).deliver(second, second_grant.target_id)))
            for _ in range(5): await asyncio.sleep(0.005)
            self.assertEqual(len(self.world.posts), 1)
            self.assertFalse(tasks[1].done())
        finally: self.world.send_release.set()
        results = await asyncio.gather(*tasks)
        self.assertEqual([r.status for r in results], ["acked", "acked"])
        self.assertEqual(len(self.world.posts), 2)

    async def test_complete_signed_scan_commits_until_only_after_actual_ack(self):
        grant = await self.grant(); self.world.event()
        result = await self.outlet().drain(CHANNEL, grant.target_id)
        self.assertEqual(result.status, "complete")
        self.assertEqual(self.db.remote_replay_since(grant.target_id), NOW-900)
        scans = [f for _, _, body in self.world.wire_calls for f in json.loads(body) if "since" in f and "until" in f]
        self.assertTrue(scans)
        self.assertEqual((scans[0]["authors"], scans[0]["#h"], scans[0]["until"], scans[0]["limit"]),
                         ([PUB], [CHANNEL], NOW, 257))

    async def test_saturated_or_unsupported_scan_preserves_cursor_and_never_claims_ready(self):
        grant = await self.grant(); outlet = self.outlet()
        for mode in ("saturated", "image", "edit", "reaction", "query_error"):
            with self.subTest(mode=mode):
                self.world.events = [self.world.approval]; self.world.status = 200
                if mode == "saturated":
                    event = self.world.event(); self.world.events += [event]*256
                elif mode == "image": self.world.event(tags=[["imeta", "url https://images.test/synthetic.png", "m image/png"]])
                elif mode == "edit": self.world.event(kind=40003, tags=[["e", "a"*64]])
                elif mode == "reaction": self.world.event(kind=7, tags=[["e", "a"*64]], text="+")
                else: self.world.status = 403
                self.pending(await outlet.drain(CHANNEL, grant.target_id))
                self.assertEqual(self.db.remote_replay_since(grant.target_id), 0)
                self.assertFalse(self.world.posts)


if __name__ == "__main__": unittest.main()
