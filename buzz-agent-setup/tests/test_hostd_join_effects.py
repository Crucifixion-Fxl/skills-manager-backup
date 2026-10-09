"""Resumable onboarding effects and trusted target metadata, offline only."""
import concurrent.futures
from dataclasses import replace
import asyncio
import base64
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

HOSTD = Path(__file__).resolve().parents[1] / "scripts" / "hostd"
sys.path.insert(0, str(HOSTD))
import store


class TargetQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "private" / "hostd.db"
        self.db = store.Store(self.path)
        self.addCleanup(self.db.close)
        self.db.reconcile_bindings([store.BindingRecord("alpha", "channel", "oc_group", "cli_reader", "/cfg", "/lark", "/data")], now=10)

    def queue(self, db=None, *, message="om_message", root="om_root", app="cli_reader", event="im.message.receive_v1", now=10):
        return (db or self.db).enqueue_target("alpha", app, message, root, event, now=now)

    def test_duplicate_original_time_restart_and_explicit_ack(self):
        self.queue(); self.queue(now=20)
        with store.Store(self.path) as db:
            rows = db.pending_targets("alpha")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["first_seen_at"], 10)
            self.assertEqual(rows[0]["root_id"], "om_root")
            self.assertTrue(db.ack_target("alpha", "cli_reader", "om_message", "im.message.receive_v1"))
            self.assertFalse(db.ack_target("alpha", "cli_reader", "om_message", "im.message.receive_v1"))
        self.assertEqual(self.db.pending_targets("alpha"), [])

    def test_delivery_record_read_projection_includes_original_retry_clock_only(self):
        reserved = self.db.reserve_delivery("alpha", "om_source", "f2b", source_at=42, now=10)
        self.db.fail_delivery(reserved.id, unknown=True, now=20)
        row = self.db.delivery_record(reserved.id)
        self.assertEqual(set(row), {"id", "status", "target_id", "source_at", "created_at", "updated_at", "attempts", "root_id", "content_hash"})
        self.assertEqual((row["created_at"], row["updated_at"], row["status"]), (10, 20, "unknown"))
        self.assertIsNone(self.db.delivery_record("f" * 64))
        with self.assertRaises(store.StoreError): self.db.delivery_record("bad")

    def test_replay_presence_empty_baseline_does_not_erase_durable_pending_or_cursor(self):
        self.assertFalse(self.db.has_replay_state("alpha", "relay"))
        self.db.reserve_delivery("alpha", "a" * 64, "b2f", source_at=0, now=10)
        self.assertTrue(self.db.has_replay_state("alpha", "relay"))
        self.assertEqual(self.db.replay_since("alpha", "relay"), 0)
        self.assertFalse(self.db.has_replay_state("alpha", "feishu"))
        with self.assertRaises(store.StoreError): self.db.has_replay_state("alpha", "body-canary")

    def test_phase_ack_preserves_checkpoint_until_complete_phase_commits(self):
        delivery = self.db.reserve_delivery('alpha','om_source','f2b',source_at=5000,now=10)
        for invalid in (0,1,None,'false'):
            with self.assertRaises(store.StoreError): self.db.ack_delivery(delivery.id,'a'*64,now=20,advance_cursor=invalid)
        self.db.ack_delivery(delivery.id,'a'*64,now=20,advance_cursor=False)
        self.assertEqual(self.db.delivery_record(delivery.id)['status'],'acked')
        self.assertEqual(self.db.cursor_position('alpha','feishu'),0)
        self.db.ack_delivery(delivery.id,'a'*64,now=21)
        self.assertEqual(self.db.cursor_position('alpha','feishu'),5000)

    def test_atomic_outlet_receipt_requires_active_agent_chat_and_own_app(self):
        agent = "a" * 64
        self.db.register_agent(agent, owner_pubkey="b" * 64, app_id="cli_outlet", now=10)
        delivery = self.db.reserve_delivery("alpha", "c" * 64, "r2f", agent_id=agent, source_at=10, now=10)
        with self.assertRaises(store.StoreError):
            self.db.ack_outlet_delivery(delivery.id, "om_target", "cli_outlet", reaction_id="reaction-1", emoji="Typing", now=20)
        self.db.record_agent_chat(agent, "oc_group", "d" * 64, binding_id="alpha", status="active", now=10)
        with self.assertRaises(store.StoreError):
            self.db.ack_outlet_delivery(delivery.id, "om_target", "cli_other", reaction_id="reaction-1", emoji="Typing", now=20)
        self.assertEqual(self.db.delivery_record(delivery.id)["status"], "pending")
        self.db.ack_outlet_delivery(delivery.id, "om_target", "cli_outlet", reaction_id="reaction-1", emoji="Typing", now=20)
        row = self.db.outlet_receipt(delivery.id)
        self.assertEqual((row["target_message_id"], row["reaction_id"], row["emoji"]), ("om_target", "reaction-1", "Typing"))
        self.assertEqual(self.db.delivery_record(delivery.id)["target_id"], "om_target")
        with self.assertRaises(store.StoreError):
            self.db.ack_outlet_delivery(delivery.id, "om_target", "cli_outlet", reaction_id="reaction-2", emoji="Typing", now=21)
        self.assertEqual(self.db.outlet_receipt(delivery.id)["reaction_id"], "reaction-1")
        with self.assertRaises(RuntimeError):
            with self.db.transaction():
                message = self.db.reserve_delivery("alpha", "e" * 64, "b2f", agent_id=agent, source_at=10, now=10)
                self.db.ack_outlet_delivery(message.id, "om_copy", "cli_outlet", now=20)
                raise RuntimeError("simulated crash before receipt commit")
        self.assertIsNone(self.db.outlet_receipt(message.id))

    def test_outlet_canonical_padded_reaction_id_preserved_and_bad_pad_bits_rejected(self):
        agent = "a" * 64
        self.db.register_agent(agent, owner_pubkey="b" * 64, app_id="cli_outlet", now=10)
        self.db.record_agent_chat(agent, "oc_group", "d" * 64, binding_id="alpha", status="active", now=10)
        delivery = self.db.reserve_delivery("alpha", "c" * 64, "r2f", agent_id=agent, now=10)
        reaction = base64.urlsafe_b64encode(bytes(range(64))).decode()
        for bad in (reaction[:-3] + "B==", reaction[:-1], reaction + "=", "=" * 88):
            with self.assertRaises(store.StoreError): self.db.ack_outlet_delivery(delivery.id, "om_target", "cli_outlet", reaction_id=bad, emoji="CrossMark", now=20)
        self.db.ack_outlet_delivery(delivery.id, "om_target", "cli_outlet", reaction_id=reaction, emoji="CrossMark", now=20)
        self.assertEqual(self.db.outlet_receipt(delivery.id)["reaction_id"], reaction)

    def test_delivery_lookup_scopes_source_direction_agent_without_reserving(self):
        self.db.register_agent("a" * 64, owner_pubkey="b" * 64, app_id="cli_outlet", now=10)
        first = self.db.reserve_delivery("alpha", "c" * 64, "r2f", agent_id="a" * 64, now=10)
        self.db.reserve_delivery("alpha", "c" * 64, "b2f", agent_id="a" * 64, now=10)
        self.assertEqual(self.db.delivery_by_source("alpha", "c" * 64, "r2f", agent_id="a" * 64), self.db.delivery_record(first.id))
        self.assertIsNone(self.db.delivery_by_source("alpha", "c" * 64, "r2f", agent_id="b" * 64))
        self.assertIsNone(self.db.delivery_by_source("alpha", "d" * 64, "r2f", agent_id="a" * 64))
        self.assertEqual(self.db.conn.execute("SELECT count(*) FROM delivery").fetchone()[0], 2)
        for source, direction in (("message body", "r2f"), ("c" * 64, "arbitrary")):
            with self.assertRaises(store.StoreError): self.db.delivery_by_source("alpha", source, direction)

    def test_f2r_mixed_case_actual_emoji_preserved_in_source_identity(self):
        for emoji in ("Typing", "CrossMark"):
            source = "om_source|on_person|" + emoji
            delivery = self.db.reserve_delivery("alpha", source, "f2r", now=10)
            self.assertEqual(self.db.conn.execute("SELECT source_id FROM delivery WHERE id=?", (delivery.id,)).fetchone()[0], source)

    def test_exact_reader_event_and_id_allowlists_no_arbitrary_payload(self):
        for arguments in ({"app": "cli_other"}, {"event": "im.message.updated_v1"}, {"message": "body-canary"}, {"root": "not-an-id"}):
            with self.assertRaises(store.StoreError): self.queue(**arguments)
        with self.assertRaises(TypeError): self.db.enqueue_target("alpha", "cli_reader", "om_message", "", "im.message.receive_v1", now=10, body="BODY-CANARY")
        self.assertNotIn("BODY-CANARY", "\n".join(self.db.conn.iterdump()))
        for event in ("im.message.reaction.created_v1", "im.message.reaction.deleted_v1"):
            self.queue(event=event, root="")
        self.assertEqual(len(self.db.pending_targets("alpha")), 2)

    def test_conflicting_root_rollback_and_recovery_failure_preserves_target(self):
        self.queue()
        with self.assertRaises(store.StoreError): self.queue(root="om_wrong", now=20)
        try:
            with self.db.transaction():
                self.db.ack_target("alpha", "cli_reader", "om_message", "im.message.receive_v1")
                raise RuntimeError("simulated crash before recovery commit")
        except RuntimeError:
            pass
        self.assertEqual(self.db.pending_targets("alpha")[0]["root_id"], "om_root")

    def test_concurrent_duplicate_writers_and_bounded_ordered_batches(self):
        def queue(_):
            with store.Store(self.path) as db: self.queue(db)
        with concurrent.futures.ThreadPoolExecutor(2) as pool: list(pool.map(queue, range(2)))
        self.assertEqual(len(self.db.pending_targets("alpha")), 1)
        self.queue(message="om_newer", now=20)
        self.assertEqual(self.db.pending_targets("alpha", limit=1)[0]["message_id"], "om_message")
        for limit in (0, 257, True):
            with self.assertRaises(store.StoreError): self.db.pending_targets("alpha", limit=limit)

    def test_exact_schema_two_upgrade_preserves_join_metadata_and_rejects_drift(self):
        self.db.close(); self.path.unlink()
        db = sqlite3.connect(self.path)
        db.executescript(store._SCHEMA_V2)
        db.execute("PRAGMA user_version=2")
        db.execute("INSERT INTO agent VALUES(?,?,?,NULL,'active',10)", ("a" * 64, "b" * 64, "cli_agent"))
        db.execute("INSERT INTO join_request VALUES('JOIN-12345678',?,?, 'cli_agent','oc_group',NULL,'new_binding','done','om_card',1,10,20,604810)", ("a" * 64, "b" * 64))
        db.execute("INSERT INTO join_transport VALUES('JOIN-12345678',2,100,1,1,'sent',0,'om_card',20)")
        db.commit(); db.close(); self.path.chmod(0o600)
        with store.Store(self.path) as upgraded:
            self.assertEqual(upgraded.conn.execute("PRAGMA user_version").fetchone()[0], store.SCHEMA_VERSION)
            self.assertEqual(upgraded.join_request("JOIN-12345678")["status"], "done")
            self.assertEqual(upgraded.join_transport("JOIN-12345678")["attempts"], 2)
        raw = sqlite3.connect(self.path)
        raw.execute("DROP TABLE event_target"); raw.execute("CREATE TABLE event_target(body TEXT)")
        raw.execute("PRAGMA user_version=2"); raw.commit(); raw.close()
        with self.assertRaises(store.StoreError): store.Store(self.path)
        raw = sqlite3.connect(self.path)
        self.addCleanup(raw.close)
        self.assertEqual(raw.execute("PRAGMA user_version").fetchone()[0], 2)

    def test_effect_metadata_restart_lease_exact_intent_and_transaction(self):
        self.db.register_agent("a" * 64, owner_pubkey="b" * 64, app_id="cli_agent", now=10)
        self.db.create_join("JOIN-12345678", "a" * 64, "b" * 64, "cli_agent", "oc_group", kind="new_binding", now=10)
        channel = "11111111-1111-4111-8111-111111111111"
        self.db.ensure_effect_plan("JOIN-12345678", channel, "new", "/protected/key.env", "/protected/config.json", now=10)
        self.assertTrue(self.db.reserve_effect_step("JOIN-12345678", "channel", "c" * 64, now=10))
        with store.Store(self.path) as reopened:
            self.assertFalse(reopened.reserve_effect_step("JOIN-12345678", "channel", "c" * 64, now=11))
            with self.assertRaises(store.StoreError): reopened.reserve_effect_step("JOIN-12345678", "channel", "d" * 64, now=1000)
            self.assertTrue(reopened.reserve_effect_step("JOIN-12345678", "channel", "c" * 64, now=71))
            reopened.finish_effect_step("JOIN-12345678", "channel", "c" * 64, "e" * 64, channel, now=72)
        self.assertEqual(self.db.effect_plan("JOIN-12345678")["channel_id"], channel)
        self.assertFalse(self.db.reserve_effect_step("JOIN-12345678", "channel", "c" * 64, now=1000))
        with self.assertRaises(TypeError): self.db.reserve_effect_step("JOIN-12345678", "channel", "c" * 64, now=1000, body="BODY-CANARY")
        self.assertNotIn("BODY-CANARY", "\n".join(self.db.conn.iterdump()))


class ActualEffectTests(unittest.TestCase):
    def test_real_bound_adapter_protocol_files_runtime_restart_and_independent_readback(self):
        import join_effects as effects
        import buzz_feishu_group_sync as gs
        owner_key, agent_key, relay_key = "2" * 64, "3" * 64, "4" * 64
        owner, agent, relay_pk = map(gs._signer_pubkey, (owner_key, agent_key, relay_key))
        channel = "11111111-1111-4111-8111-111111111111"
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory); base.chmod(0o700)
            def file(name, value):
                path = base / name; path.write_text(value); path.chmod(0o600); return str(path)
            prompt = file("prompt", legacy_prompt())
            responsible = file("responsible", '{"channels":[]}')
            env = file("agent.env", f"BUZZ_PRIVATE_KEY={agent_key}\nBUZZ_ACP_AGENT_OWNER={owner}\nBUZZ_ACP_CHANNELS=22222222-2222-4222-8222-222222222222\nBUZZ_ACP_SYSTEM_PROMPT_FILE={prompt}\nBUZZ_RESPONSIBLE_CONFIG={responsible}\n")
            timer = file("timer", json.dumps({"version": 1, "owner_pubkey": owner, "agents": []}))
            owner_env = file("owner.env", f"BUZZ_PRIVATE_KEY={owner_key}\n")
            config = file("config", json.dumps({"channel_id": channel, "chat_id": "oc_group", "sync_app_id": "cli_reader"}))
            rows = {0: [], 39002: []}; writes = []
            auth = ["auth", owner, "", gs.sync.nk.schnorr_sign(hashlib.sha256(f"nostr:agent-auth:{agent}:".encode()).digest(), bytes.fromhex(owner_key), b"\0" * 32).hex()]
            rows[0] = [gs.sign_event(agent_key, 0, [auth], "{}", 10)]
            def roster(): return gs.sign_event(relay_key, 39002, [["d", channel], ["p", owner, "", "owner"]] + ([["p", agent, "", "bot"]] if writes else []), "", 20 + len(writes))
            def http(url, headers, timeout, body=None):
                credential = json.loads(base64.b64decode(headers["Authorization"].split(" ", 1)[1]))
                self.assertTrue(gs._nip01_event_verified(credential)); self.assertEqual(credential["pubkey"], owner)
                if url.endswith("/query"):
                    kind = json.loads(body)[0]["kinds"][0]
                    return 200, json.dumps([roster()] if kind == 39002 else rows.get(kind, [])).encode()
                event = json.loads(body); self.assertTrue(gs._nip01_event_verified(event)); writes.append(event)
                return 200, json.dumps({"accepted": True, "event_id": event["id"]}).encode()
            class Ops:
                busy = True; restarts = 0; running_env = effects.legacy.parse_env(Path(env).read_text()); log = ""
                def unit_status(self, unit): return "loaded", "active"
                def is_busy(self, unit): return self.busy
                def process(self, unit): return 123, self.restarts + 1, self.running_env
                def invocation_id(self, unit): return f"{self.restarts + 1:032x}"
                def journal_invocation(self, unit, pid, invocation):
                    assert pid == 123 and invocation == self.invocation_id(unit)
                    return self.log
                def restart(self, unit):
                    self.restarts += 1; self.running_env = effects.legacy.parse_env(Path(env).read_text()); self.log = "subscribed to channel " + channel
            ops = Ops()
            class Client:
                app_id = 'cli_agent'; present = True
                def member_listing(self,chat,user_id_type): return gs.MemberListing({}, {'cli_agent':'ou_bot'} if self.present else {}, True)
            client = Client()
            spec = effects.AgentSpec(agent, owner, "cli_agent", env, prompt, responsible, "agent.service", timer, str(base))
            with store.Store(base / "sql" / "hostd.db") as db:
                db.reconcile_bindings([store.BindingRecord("alpha", channel, "oc_group", "cli_reader", config, "/reader", "/readerdata")], now=10)
                db.register_agent(agent, owner_pubkey=owner, app_id="cli_agent", now=10)
                db.create_join("JOIN-12345678", agent, owner, "cli_agent", "oc_group", kind="channel", binding_id="alpha", now=10)
                db.conn.execute("UPDATE join_request SET status='approved'")
                transport = effects.Nip98Relay("https://relay.test", owner_env, relay_pk, http=http, trusted_relays=("https://relay.test",), clock=lambda:100)
                adapter = effects.JoinEffects(db, {agent:spec}, transport, effects.AgentRuntime(ops), clients={'cli_agent':client},clock=lambda:100)
                request = db.join_request("JOIN-12345678")
                def grant():
                    found = db.conn.execute('SELECT * FROM agent_chat WHERE agent_id=? AND chat_id=?',
                                            (agent, 'oc_group')).fetchone()
                    return dict(found) if found else None
                asyncio.run(adapter.apply(request))
                self.assertFalse(asyncio.run(adapter.readback(request)).verified); self.assertEqual(ops.restarts, 0)
                self.assertIn(channel, Path(env).read_text()); self.assertIn(channel, Path(prompt).read_text())
                self.assertIn(channel, json.loads(Path(responsible).read_text())["channels"])
                ops.busy = False; asyncio.run(adapter.apply(request))
                with self.subTest('all old gates true but no root registrar stays pending'):
                    self.assertFalse(asyncio.run(adapter.readback(request)).verified)
                    self.assertIsNone(grant())
                self.assertEqual(ops.restarts, 1)
                class Registrar:
                    worker=True; reader=True; relay=True; outlet=False; checked=100; changes={}; mutate=None
                    def __init__(inner): inner.calls=[]
                    async def register(inner,row,plan):
                        self.assertTrue(effects.AgentRuntime(ops).verify(spec,channel))
                        self.assertEqual(row['kind'],'channel');self.assertEqual(plan['binding_id'],'alpha')
                        self.assertEqual(plan['config_path'],config);self.assertEqual(plan['secret_ref'],env)
                        inner.calls.append((dict(row),dict(plan)))
                    async def readback(inner,row,plan):
                        proof=effects.RegistrationProof(row['request_id'],plan['binding_id'],plan['channel_id'],row['chat_id'],row['callback_app_id'],
                                                        inner.worker,inner.reader,inner.relay,inner.outlet,inner.checked,'e'*64)
                        if inner.mutate: inner.mutate()
                        return replace(proof,**inner.changes)
                registrar=Registrar()
                adapter=effects.JoinEffects(db,{agent:spec},transport,effects.AgentRuntime(ops),registrar,
                                            clients={'cli_agent':client},clock=lambda:100)
                asyncio.run(adapter.apply(request))
                with self.subTest('typed connecting outlet is not a done receipt'):
                    self.assertFalse(asyncio.run(adapter.readback(request)).verified)
                    self.assertIsNone(grant())
                with self.subTest('existing bound plan adopted after real runtime active'):
                    self.assertEqual(len(registrar.calls),1)
                registrar.outlet=True
                for status in ('denied', 'expired', 'done', 'applied'):
                    with self.subTest('current SQL approval and caller snapshot must agree', status=status):
                        db.conn.execute('UPDATE join_request SET status=?', (status,))
                        self.assertFalse(asyncio.run(adapter.readback(request)).verified)
                        if status != 'applied':
                            self.assertFalse(asyncio.run(adapter.readback(db.join_request(request['request_id']))).verified)
                        self.assertIsNone(grant())
                db.conn.execute("UPDATE join_request SET status='approved'")
                with self.subTest('revocation during async proof cannot mint durable grant'):
                    registrar.mutate=lambda: db.conn.execute("UPDATE join_request SET status='denied'")
                    self.assertFalse(asyncio.run(adapter.readback(request)).verified)
                    self.assertIsNone(grant())
                registrar.mutate=None
                db.conn.execute("UPDATE join_request SET status='approved'")
                before_files={path:Path(path).read_bytes() for path in (env,prompt,responsible,timer,config)}
                with self.subTest('fresh root proof persists exact durable bound grant before done'):
                    self.assertTrue(asyncio.run(adapter.readback(request)).verified)
                    self.assertEqual(grant(), {'agent_id':agent,'chat_id':'oc_group','chat_ref':gs.chat_ref('oc_group'),
                                               'binding_id':'alpha','status':'active','updated_at':100})
                    self.assertEqual(db.join_request(request['request_id'])['status'],'approved')
                    self.assertEqual(before_files,{path:Path(path).read_bytes() for path in before_files})
                    self.assertEqual((len(writes),ops.restarts),(1,1))
                with self.subTest('fresh applied snapshot can complete without replaying side effects'):
                    db.conn.execute("UPDATE join_request SET status='applied'")
                    applied=db.join_request(request['request_id'])
                    self.assertTrue(asyncio.run(adapter.readback(applied)).verified)
                    self.assertIsNotNone(grant())
                db.conn.execute("UPDATE join_request SET status='approved'")
                for changes in ({'worker_active':False},{'reader_connected':False},{'relay_connected':False},{'outlet_active':False},
                                {'request_id':'OTHER-12345678'},{'binding_id':'other'},{'channel_id':'33333333-3333-4333-8333-333333333333'},
                                {'chat_id':'oc_wrong'},{'app_id':'cli_reader'},{'checked_at':101},{'checked_at':0}):
                    with self.subTest('root proof identity/freshness gate',changes=changes):
                        # Age301 is stale; age0 is fresh. Use clock401 for zero timestamp case.
                        registrar.changes=changes
                        check_adapter=adapter if changes!={'checked_at':0} else effects.JoinEffects(db,{agent:spec},transport,effects.AgentRuntime(ops),registrar,clients={'cli_agent':client},clock=lambda:401)
                        self.assertFalse(asyncio.run(check_adapter.readback(request)).verified)
                registrar.changes={}
                typed=registrar.readback
                async def untyped(*args): return {'outlet_active':True,'worker_active':True,'reader_connected':True,'relay_connected':True}
                registrar.readback=untyped
                with self.subTest('untyped truthy proof is refused'):
                    self.assertFalse(asyncio.run(adapter.readback(request)).verified)
                registrar.readback=typed
                restarted = effects.JoinEffects(db, {agent:spec}, transport, effects.AgentRuntime(ops), registrar,clients={'cli_agent':client},clock=lambda:200)
                asyncio.run(restarted.apply(request)); self.assertTrue(asyncio.run(restarted.readback(request)).verified)
                self.assertEqual(len(registrar.calls),2)
                registrar.outlet=False
                with self.subTest('saved verified registrar step cannot replace current disconnected outlet'):
                    saved_grant=grant()
                    self.assertFalse(asyncio.run(restarted.readback(request)).verified)
                    self.assertEqual(grant(),saved_grant)
                    self.assertIsNotNone(grant())
                asyncio.run(restarted.apply(request))
                self.assertEqual(len(registrar.calls),3)
                registrar.outlet=True
                self.assertEqual(len(writes), 1); self.assertEqual(ops.restarts, 1)
                client.present = False
                self.assertFalse(asyncio.run(restarted.readback(request)).verified)
                client.present = True
                Path(timer).write_text(json.dumps({'version':1,'owner_pubkey':owner,'agents':[{'env_file':env}]})); Path(timer).chmod(0o600)
                self.assertFalse(asyncio.run(restarted.readback(request)).verified)
                Path(timer).write_text(json.dumps({'version':1,'owner_pubkey':owner,'agents':[]})); Path(timer).chmod(0o600)
                Path(prompt).write_text("broken externally"); Path(prompt).chmod(0o600)
                self.assertFalse(asyncio.run(restarted.readback(request)).verified)

    def test_secure_compare_exchange_rejects_symlinks_and_preserves_concurrent_edit(self):
        import join_effects as effects
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "file"; path.write_bytes(b"old"); path.chmod(0o600)
            files = effects.ProtectedFiles()
            files.replace(path, b"old", b"new")
            self.assertEqual(path.read_bytes(), b"new")
            with self.assertRaises(effects.EffectError): files.replace(path, b"old", b"lost edit")
            link = Path(directory) / "link"; link.symlink_to(path)
            with self.assertRaises(effects.EffectError): files.replace(link, b"new", b"bad")
            self.assertEqual(path.read_bytes(), b"new")

    def test_runtime_busy_never_restarts_and_active_alone_never_verifies(self):
        import join_effects as effects
        class Operations:
            def __init__(self): self.busy = True; self.restarts = 0
            def unit_status(self, unit): return "loaded", "active"
            def is_busy(self, unit): return self.busy
            def restart(self, unit): self.restarts += 1
            def journal_cursor(self, unit): return "s=before"
            def journal_after(self, unit, cursor): return ""
            def process(self, unit): return 123, 456, {
                "BUZZ_PRIVATE_KEY": "3" * 64, "BUZZ_ACP_AGENT_OWNER": effects.gs._signer_pubkey("2" * 64),
                "BUZZ_ACP_SYSTEM_PROMPT_FILE": "/prompt", "BUZZ_RESPONSIBLE_CONFIG": "/responsible",
                "BUZZ_ACP_CHANNELS": "11111111-1111-4111-8111-111111111111"}
            def invocation_id(self, unit): return "a" * 32
            def journal_invocation(self, unit, pid, invocation): return ""
        ops = Operations(); runtime = effects.AgentRuntime(ops)
        spec = effects.AgentSpec(effects.gs._signer_pubkey("3" * 64), effects.gs._signer_pubkey("2" * 64), "cli_agent", "/env", "/prompt", "/responsible", "agent.service", "/timer.json", "/state")
        channel = "11111111-1111-4111-8111-111111111111"
        self.assertFalse(runtime.activate(spec, channel)); self.assertEqual(ops.restarts, 0)
        ops.busy = False
        self.assertFalse(runtime.activate(spec, channel)); self.assertEqual(ops.restarts, 1)
        self.assertFalse(runtime.verify(spec, channel))

    def test_denied_cleanup_bot_only_observed_absence_required(self):
        import join_effects as effects
        self.assertTrue(hasattr(effects.JoinEffects, 'cleanup'))
        class Client:
            app_id = 'cli_agent'
            complete = False
            present = True
            calls = 0
            def member_listing(self, chat, user_id_type):
                return effects.gs.MemberListing({}, {'cli_agent':'ou_bot'} if self.present else {}, self.complete)
            def change_members(self, method, chat, ids, id_type):
                self.calls += 1; self.present = False; return 0
        client = Client()
        # The concrete transport must fail closed on incomplete lists; then verify
        # bot disappearance after a real DELETE, never count intent as cleanup.
        transport = effects.BotMembership({'cli_agent':client})
        with self.assertRaises(effects.EffectError): transport.leave('cli_agent','oc_group')
        self.assertEqual(client.calls, 0)
        client.complete = True
        self.assertTrue(transport.leave('cli_agent','oc_group')); self.assertEqual(client.calls, 1)
        self.assertTrue(transport.leave('cli_agent','oc_group')); self.assertEqual(client.calls, 1)

    def test_new_binding_actual_private_channel_mirror_claim_bot_members_and_registrar(self):
        import join_effects as effects
        self.assertTrue(hasattr(effects, 'MemberPreparation'))
        self.assertTrue(hasattr(effects, 'RegistrationProof'))
        # Detailed protocol scenario below uses the real adapter against offline HTTP.
        asyncio.run(self._new_binding_scenario(effects))

    def test_new_binding_existing_group_conflict_blocks_before_any_publication(self):
        import join_effects as effects
        asyncio.run(self._new_binding_scenario(effects, conflict=True))

    def test_new_binding_group_race_during_preflight_blocks_before_publication(self):
        import join_effects as effects
        asyncio.run(self._new_binding_scenario(effects, conflict='during_preflight'))

    def test_first_binding_with_valid_empty_channel_allowlist_uses_actual_new_channel(self):
        import join_effects as effects
        asyncio.run(self._new_binding_scenario(effects, empty_channels=True))

    async def _new_binding_scenario(self, effects, *, conflict=False, empty_channels=False):
        gs = effects.gs
        owner_key, agent_key, relay_key = "2" * 64, "3" * 64, "4" * 64
        owner, agent, relay_pk = map(gs._signer_pubkey, (owner_key, agent_key, relay_key))
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory); base.chmod(0o700)
            def file(name,value):
                path = base/name; path.write_text(value); path.chmod(0o600); return str(path)
            prompt = file('prompt', legacy_prompt()); responsible = file('responsible','{"channels":[]}')
            env = file('agent.env',f'BUZZ_PRIVATE_KEY={agent_key}\nBUZZ_ACP_AGENT_OWNER={owner}\nBUZZ_ACP_CHANNELS=22222222-2222-4222-8222-222222222222\nBUZZ_ACP_SYSTEM_PROMPT_FILE={prompt}\nBUZZ_RESPONSIBLE_CONFIG={responsible}\n')
            if empty_channels:
                path=Path(env); path.write_text(path.read_text().replace('BUZZ_ACP_CHANNELS=22222222-2222-4222-8222-222222222222','BUZZ_ACP_CHANNELS=')); path.chmod(0o600)
            timer = file('timer',json.dumps({'version':1,'owner_pubkey':owner,'agents':[]})); owner_env=file('owner.env',f'BUZZ_PRIVATE_KEY={owner_key}\n')
            binary = base/'opt'/'buzz-0.5.23'/'usr'/'bin'/'buzz'; binary.parent.mkdir(parents=True); binary.write_bytes(b'\x7fELF-offline'); binary.chmod(0o755)
            profiles = base/'profile'; profiles.mkdir(mode=0o700); data_dir=base/'data'; data_dir.mkdir(mode=0o700); bindings=base/'bindings'; bindings.mkdir(mode=0o700)
            template = file('template',json.dumps({'channel_id':'22222222-2222-4222-8222-222222222222','chat_id':'oc_old','owner_open_id':'ou_owner','owner_app_id':'cli_legacy','mirror_pubkey':'a'*64,'mirror_env_file':env,'people_api':{'base_url':'https://people.test','signer_env_file':owner_env},'remove_extras':False,'agents':{agent:{'app_id':'cli_agent','lark_config_dir':str(profiles),'lark_data_dir':str(data_dir)}},'desk_pubkey':agent,'buzz_cli':str(binary),'buzz_cli_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),'lark_cli':'/opt/lark-cli'}))
            config=profiles/'config.json'; config.write_text(json.dumps({'apps':[{'appId':'cli_agent'}]})); config.chmod(0o600)
            auth=['auth',owner,'',gs.sync.nk.schnorr_sign(hashlib.sha256(f'nostr:agent-auth:{agent}:'.encode()).digest(),bytes.fromhex(owner_key),b'\0'*32).hex()]
            profiles_events={agent:gs.sign_event(agent_key,0,[auth],'{}',10)}; policies={agent:gs.sign_event(owner_key,30177,[['d',agent]],json.dumps({'feishu':{'app_id':'cli_agent'}}),10)}; roster={}; channel=''; publications=[]; lost_first_ack=True
            def http(url,headers,timeout,body=None):
                nonlocal channel,lost_first_ack
                payload=json.loads(body)
                if url.endswith('/query'):
                    kind=payload[0]['kinds'][0]
                    if kind==0: rows=[profiles_events[a] for a in payload[0].get('authors',[]) if a in profiles_events]
                    elif kind==30177: rows=[p for p in policies.values() if p['tags'][0][1] in payload[0]['#d']]
                    elif kind==39000: rows=[gs.sign_event(relay_key,39000,[['d',channel],['name','hostd JOIN-12345678'],['private']],'',20)] if channel else []
                    elif kind==39002: rows=[gs.sign_event(relay_key,39002,[['d',channel]]+[['p',p,'',role] for p,role in roster.items()],'',20+len(publications))] if channel else []
                    else: rows=[]
                    return 200,json.dumps(rows).encode()
                event=payload; self.assertTrue(gs._nip01_event_verified(event)); publications.append(event)
                if event['kind']==9007:
                    channel=event['tags'][0][1]; roster[owner]='owner'
                    if lost_first_ack: lost_first_ack=False; raise OSError('BODY-CANARY server error')
                elif event['kind']==9000: roster[event['tags'][1][1]]=event['tags'][2][1]
                elif event['kind']==0: profiles_events[event['pubkey']]=event
                elif event['kind']==30177: policies[event['tags'][0][1]]=event
                return 200,json.dumps({'accepted':True,'event_id':event['id']}).encode()
            def bot_cli(argv,**kwargs):
                import subprocess
                self.assertEqual(argv[argv.index('--as')+1],'bot')
                self.assertEqual(kwargs['env']['LARKSUITE_CLI_CONFIG_DIR'],str(profiles))
                self.assertEqual(kwargs['env']['LARKSUITE_CLI_DATA_DIR'],str(data_dir))
                if '/open-apis/application/v6/scopes' in argv:
                    data={'scopes':[{'scope_name':scope,'scope_type':'tenant','grant_status':1} for scope in effects.sync_app_claim.READ_SCOPE_GROUPS]}
                else:
                    kind=argv[argv.index('--member-types')+1]
                    data={'has_more':False,'truncations':[]}
                    if kind=='user':data.update(users=[{'member_id':'on_owner','name':'ignored display text'}],user_total=1)
                    else:data.update(bots=[{'app_id':'cli_agent','member_id':'ou_bot'}],bot_total=1)
                return subprocess.CompletedProcess(argv,0,json.dumps({'ok':True,'identity':'bot','data':data}),'')
            client=effects.sync_app_claim.BotLarkCli('cli_agent',profiles,data_dir,base_env={'HOME':str(base)},runner=bot_cli)
            class Ops:
                restarts=0; running=effects.legacy.parse_env(Path(env).read_text()); log=''
                def unit_status(self,unit): return 'loaded','active'
                def is_busy(self,unit): return False
                def process(self,unit): return 123,self.restarts+1,self.running
                def invocation_id(self,unit): return f"{self.restarts + 1:032x}"
                def journal_invocation(self,unit,pid,invocation):
                    assert pid == 123 and invocation == self.invocation_id(unit)
                    return self.log
                def restart(self,unit): self.restarts+=1; self.running=effects.legacy.parse_env(Path(env).read_text()); self.log='subscribed to channel '+channel
            class Registrar:
                active=False
                async def prepare_members(self,row,plan):
                    if conflict == 'during_preflight':
                        db.reconcile_bindings([store.BindingRecord('other','22222222-2222-4222-8222-222222222222','oc_group','cli_agent',template,str(profiles),str(data_dir))],now=10)
                    return effects.MemberPreparation('cli_agent','oc_group',100,True,((owner,'on_owner'),),(), 'f'*64)
                async def register(self,row,plan): self.active=True
                async def readback(self,row,plan): return effects.RegistrationProof(row['request_id'],plan['binding_id'],plan['channel_id'],'oc_group','cli_agent',self.active,self.active,self.active,self.active,100,'e'*64)
            ops=Ops(); registrar=Registrar()
            spec=effects.AgentSpec(agent,owner,'cli_agent',env,prompt,responsible,'agent.service',timer,str(base),str(bindings),'cli_agent',str(profiles),str(data_dir),template)
            with store.Store(base/'sql'/'hostd.db') as db:
                db.register_agent(agent,owner_pubkey=owner,app_id='cli_agent',now=10)
                db.create_join('JOIN-12345678',agent,owner,'cli_agent','oc_group',kind='new_binding',now=10); db.conn.execute("UPDATE join_request SET status='approved'")
                relay=effects.Nip98Relay('https://relay.test',owner_env,relay_pk,http=http,trusted_relays=('https://relay.test',),clock=lambda:100)
                adapter=effects.JoinEffects(db,{agent:spec},relay,effects.AgentRuntime(ops),registrar,clients={'cli_agent':client},clock=lambda:100)
                row=db.join_request('JOIN-12345678')
                if conflict:
                    if conflict is True:
                        db.reconcile_bindings([store.BindingRecord('other','22222222-2222-4222-8222-222222222222','oc_group','cli_agent',template,str(profiles),str(data_dir))],now=10)
                    with self.assertRaises(effects.EffectError): await adapter.apply(row)
                    self.assertEqual(publications,[], 'existing group binding must block before outward channel creation')
                    return
                with self.assertRaises(effects.EffectError): await adapter.apply(row)
                plan=db.effect_plan(row['request_id']); self.assertEqual(plan['channel_id'],channel)
                self.assertFalse((await adapter.readback(row)).verified)
                adapter=effects.JoinEffects(db,{agent:spec},relay,effects.AgentRuntime(ops),registrar,clients={'cli_agent':client},clock=lambda:200)
                await adapter.apply(row)
                self.assertTrue((await adapter.readback(row)).verified)
                self.assertEqual(sum(event['kind']==9007 for event in publications),1)
                with self.subTest('new unpublished steps use resumed execution clock'):
                    self.assertEqual(next(event['created_at'] for event in publications if event['kind']==0),200)
                with self.subTest('fresh approval execution clock'):
                    self.assertEqual(next(event['created_at'] for event in publications if event['kind']==9007),100)
                self.assertEqual(roster[plan['mirror_pubkey']] if plan['mirror_pubkey'] else roster[db.effect_plan(row['request_id'])['mirror_pubkey']], 'bot')
                cfg=json.loads(Path(plan['config_path']).read_text()); self.assertEqual(cfg['desk_pubkey'],agent); self.assertEqual(cfg['agents'][agent]['app_id'],'cli_agent')
                with self.subTest('registration activation atomic metadata'):
                    self.assertEqual(db.bindings()[0]['status'],'active')
                dump='\n'.join(db.conn.iterdump()); self.assertNotIn(agent_key,dump); self.assertNotIn(owner_key,dump); self.assertNotIn('BODY-CANARY',dump)
                secret_path=Path(plan['secret_ref']); secret=secret_path.read_bytes(); secret_path.unlink()
                self.assertFalse((await adapter.readback(row)).verified)
                with self.subTest('readonly missing credential readback'):
                    self.assertFalse(secret_path.exists(), 'readback must not recreate or rotate missing mirror credentials')
                secret_path.write_bytes(secret); secret_path.chmod(0o600)
                mirror=db.effect_plan(row['request_id'])['mirror_pubkey']; old_policy=policies[mirror]
                current_plan=db.effect_plan(row['request_id']); adapter.clock=lambda:200
                before_count=len(publications)
                for claimed_at in (json.loads(old_policy['content'])['feishu']['bindings'][0]['claimed_at'],200):
                    renewed=json.loads(old_policy['content'])
                    renewed['feishu']['bindings'][0].update(claimed_at=claimed_at,heartbeat=200)
                    policies[mirror]=gs.sign_event(owner_key,30177,[['d',mirror]],json.dumps(renewed),200)
                    self.assertTrue(await adapter._claim(row,current_plan,cfg))
                    self.assertEqual(len(publications),before_count,'verified renewal must never republish initial claim')
                for change in ({'channel':'different'}, {'heartbeat':True}, {'heartbeat':20000},
                               {'claimed_at':0}, {'sync_app':{'version':1,'app_id':'cli_wrong'}}):
                    wrong=json.loads(old_policy['content']);wrong['feishu']['bindings'][0].update(change)
                    policies[mirror]=gs.sign_event(owner_key,30177,[['d',mirror]],json.dumps(wrong),200)
                    with self.assertRaises(effects.EffectError):await adapter._claim(row,current_plan,cfg)
                    self.assertEqual(len(publications),before_count)
                for field in ('mirror','parallelism','app_version'):
                    wrong=json.loads(old_policy['content'])
                    if field=='mirror':wrong['feishu']['mirror']=1
                    elif field=='parallelism':wrong['parallelism']=True
                    else:wrong['feishu']['bindings'][0]['sync_app']['version']=True
                    policies[mirror]=gs.sign_event(owner_key,30177,[['d',mirror]],json.dumps(wrong),200)
                    with self.assertRaises(effects.EffectError):await adapter._claim(row,current_plan,cfg)
                    self.assertEqual(len(publications),before_count)
                policies[mirror]=old_policy
                forged=json.loads(old_policy['content']); forged['feishu']['bindings'][0]['heartbeat']=1
                policies[mirror]=gs.sign_event(owner_key,30177,[['d',mirror]],json.dumps(forged),200)
                with self.subTest('future or expired claim readback'):
                    # Signed metadata is necessary but does not prove a live lease.
                    adapter.clock=lambda:2000
                    registrar.prepare_members=lambda *args: async_value(effects.MemberPreparation('cli_agent','oc_group',2000,True,((owner,'on_owner'),),(),'f'*64))
                    registrar.readback=lambda *args: async_value(effects.RegistrationProof(row['request_id'],plan['binding_id'],plan['channel_id'],'oc_group','cli_agent',True,True,True,True,2000,'e'*64))
                    self.assertFalse((await adapter.readback(row)).verified)
                policies[mirror]=old_policy; adapter.clock=lambda:200
                registrar.active=False; self.assertFalse((await adapter.readback(row)).verified)


def legacy_prompt():
    return "System prompt\n<!-- buzz-agent-channels:v1 -->\n<!-- /buzz-agent-channels:v1 -->\n"


async def async_value(value): return value


if __name__ == "__main__": unittest.main()
