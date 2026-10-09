"""Remote own-bot metadata ledger: real SQLite, no relay/profile/transport."""
import concurrent.futures
from dataclasses import FrozenInstanceError, replace
import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "hostd"))
import store

AGENT, OWNER, MIRROR, MIRROR_OWNER = (c * 64 for c in "abcd")
CHANNEL = "12345678-1234-1234-1234-123456789abc"
CHAT, APP, REQUEST, DECISION = "oc_remote", "cli_agent", "JOIN-12345678", "e" * 64
SOURCE, CONTENT, RECEIPT = "1" * 64, "2" * 64, "3" * 64
CAPABILITIES = ("message", "edit", "reaction_add", "reaction_remove")
SCHEMA5 = store._SCHEMA_V4 + store._UPGRADE_V5_SCHEMA
FROZEN = {
    "_SCHEMA_V1": "a8ba09c605240e34be6e8b35a60ef67ac78fdf6a5847d9cbef18dccd60be98bd",
    "_JOIN_TRANSPORT_SCHEMA": "d6c406f73fa0d5b87ba00ef4d048fa80815f448915acdb767892a2fe4c82bba8",
    "_UPGRADE_V3_SCHEMA": "bdb66b1027022cb618aef3e831d224ee66b5ab005adc7d17f856d8b062f90f73",
    "_UPGRADE_V4_SCHEMA": "e2247bbf783c653f382d97e13f09d650fcd9e5a6532b42945b87f397732dbae6",
    "_UPGRADE_V5_SCHEMA": "fe442eaa149d3aa8661f4420b4d3a6230f0951b12672dccdfddeb39fdd3fadc5",
}


def digest(value):
    return hashlib.sha256(json.dumps(value, separators=(",", ":")).encode()).hexdigest()


class RemoteLedger(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "private" / "state.db"
        self.db = store.Store(self.path)
        self.addCleanup(self.db.close)
        self.db.register_agent(AGENT, owner_pubkey=OWNER, app_id=APP, now=10)
        self.db.create_join(REQUEST, AGENT, OWNER, APP, CHAT, kind="channel", now=10)
        self.db.rotate_card(REQUEST, "om_card", now=11)
        self.assertTrue(self.db.decide_join(REQUEST, DECISION, OWNER, APP, "om_card", 1,
                                           approved=True, now=12))

    def evidence(self, **changes):
        args = dict(agent_id=AGENT, owner_pubkey=OWNER, app_id=APP, channel_id=CHANNEL,
                    chat_id=CHAT, chat_ref=digest_chat(CHAT), relay_origin="https://relay.test",
                    mirror_pubkey=MIRROR, mirror_owner_pubkey=MIRROR_OWNER,
                    claim_event_id="4" * 64, agent_profile_event_id="5" * 64,
                    agent_policy_event_id="6" * 64, roster_event_id="7" * 64,
                    allowlist_hash="8" * 64, approval_kind="mirror_approval", approval_id="9" * 64,
                    approval_hash="0" * 64,
                    capabilities=CAPABILITIES, checked_at=20, valid_until=120, claimed_at=10)
        args.update(changes)
        return store.RemoteGrantEvidence(**args)

    def grant(self, db=None, *, expected=0, now=20, **changes):
        return (db or self.db).activate_remote_grant(self.evidence(**changes),
                                                    expected_revision=expected, now=now)

    def reserve(self, grant, db=None, *, source=SOURCE, action="message", at=1000,
                content=CONTENT, now=21, root=""):
        return (db or self.db).reserve_remote_delivery(grant.target_id, source, action,
            revision=grant.revision, scope_hash=grant.scope_hash, source_at=at,
            content_hash=content, root_id=root, now=now)

    def unknown(self, delivery, grant, db=None, *, now=22):
        return (db or self.db).mark_remote_unknown(delivery.id, revision=grant.revision,
                                                 scope_hash=grant.scope_hash, now=now)

    def ack(self, delivery, grant, db=None, **changes):
        args = dict(revision=grant.revision, scope_hash=grant.scope_hash, sender_app_id=APP,
                    message_id="om_sent", receipt_hash=RECEIPT, now=23)
        args.update(changes)
        return (db or self.db).ack_remote_delivery(delivery.id, **args)

    def commit(self, grant, position, *, complete=True, now=24):
        return self.db.commit_remote_cursor(grant.target_id, position, revision=grant.revision,
                                            scope_hash=grant.scope_hash, complete=complete, now=now)

    def local(self, **changes):
        args = dict(approval_kind="local_card", approval_id=REQUEST,
                    approval_hash=digest([REQUEST, OWNER, APP, CHAT, DECISION]))
        args.update(changes)
        return self.grant(**args)

    def local_plan(self):
        # Existing local scope only; the production remote flow must never
        # manufacture this graph to turn discovery into card approval.
        self.db.reconcile_bindings([store.BindingRecord("local", CHANNEL, CHAT, "cli_reader",
                                   "/local/cfg", "/local/config", "/local/data")], now=10)
        self.db.conn.execute("UPDATE join_request SET binding_id='local' WHERE request_id=?", (REQUEST,))
        self.db.ensure_effect_plan(REQUEST, CHANNEL, "local", "/own/reference.env", "/local/cfg", now=13)

    def test_schema_six_preserves_frozen_sql_bytes_and_private_wal(self):
        self.assertEqual(self.db.conn.execute("PRAGMA user_version").fetchone()[0], store.SCHEMA_VERSION)
        for name, expected in FROZEN.items():
            self.assertEqual(hashlib.sha256(getattr(store, name).encode()).hexdigest(), expected)
        names = {r[0] for r in self.db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"remote_target", "remote_grant", "remote_delivery", "remote_cursor"} <= names)
        self.assertEqual(self.db.conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertEqual(self.path.parent.stat().st_mode & 0o777, 0o700)
        for path in self.path.parent.iterdir():
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_owned_agent_foreign_target_does_not_create_local_binding_or_profile(self):
        grant = self.grant()
        self.assertEqual((grant.agent_id, grant.revision, grant.capabilities), (AGENT, 1, CAPABILITIES))
        with self.assertRaises(FrozenInstanceError):
            grant.revision = 2
        self.assertEqual(self.db.bindings(), [])
        for table in ("app_profile", "agent_chat", "connection"):
            self.assertEqual(self.db.conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 0)
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.conn.execute("DELETE FROM agent WHERE pubkey=?", (AGENT,))

    def test_discovery_and_unlinked_or_unapproved_card_are_not_approval(self):
        self.local_plan()
        for changes in ({"approval_kind": "discovery"}, {"approval_kind": "emoji"},
                        {"approval_id": "JOIN-87654321"}, {"approval_hash": "9" * 64}):
            with self.subTest(changes=changes), self.assertRaises(store.StoreError):
                self.local(**changes)
        self.db.conn.execute("DELETE FROM join_decision WHERE request_id=?", (REQUEST,))
        with self.assertRaises(store.StoreError):
            self.local()
        self.db.conn.execute("INSERT INTO join_decision VALUES(?,?,?,12)", (APP, DECISION, REQUEST))
        for status in ("requested", "denied", "expired"):
            self.db.conn.execute("UPDATE join_request SET status=? WHERE request_id=?", (status, REQUEST))
            with self.subTest(status=status), self.assertRaises(store.StoreError):
                self.local()

    def test_local_card_exact_owner_app_chat_decision_and_active_agent(self):
        self.local_plan()
        for column, wrong in (("owner_pubkey", "9" * 64), ("callback_app_id", "cli_other"),
                              ("chat_id", "oc_other")):
            old = self.db.join_request(REQUEST)[column]
            self.db.conn.execute(f"UPDATE join_request SET {column}=? WHERE request_id=?", (wrong, REQUEST))
            with self.subTest(column=column), self.assertRaises(store.StoreError):
                self.local()
            self.db.conn.execute(f"UPDATE join_request SET {column}=? WHERE request_id=?", (old, REQUEST))
        self.db.conn.execute("UPDATE join_decision SET app_id='cli_other' WHERE request_id=?", (REQUEST,))
        with self.assertRaises(store.StoreError):
            self.local()
        self.db.conn.execute("UPDATE join_decision SET app_id=? WHERE request_id=?", (APP, REQUEST))
        for column, wrong in (("owner_pubkey", "9" * 64), ("app_id", "cli_other")):
            old = self.db.conn.execute(f"SELECT {column} FROM agent WHERE pubkey=?", (AGENT,)).fetchone()[0]
            self.db.conn.execute(f"UPDATE agent SET {column}=? WHERE pubkey=?", (wrong, AGENT))
            with self.subTest(agent_column=column), self.assertRaises(store.StoreError):
                self.local()
            self.db.conn.execute(f"UPDATE agent SET {column}=? WHERE pubkey=?", (old, AGENT))
        for status in ("paused", "retired"):
            self.db.conn.execute("UPDATE agent SET status=? WHERE pubkey=?", (status, AGENT))
            with self.subTest(status=status), self.assertRaises(store.StoreError):
                self.local()

    def test_local_card_contradictory_effect_plan_or_dangling_claimed_binding_fails(self):
        with self.assertRaises(store.StoreError):
            self.local()  # no matching local channel evidence
        self.db.ensure_effect_plan(REQUEST, "87654321-4321-4321-4321-cba987654321", "not-local",
                                  "/own/reference.env", "/own/reference.json", now=13)
        with self.assertRaises(store.StoreError):
            self.local()
        self.db.conn.execute("UPDATE effect_plan SET channel_id=? WHERE request_id=?", (CHANNEL, REQUEST))
        # A matching string cannot invent a verified binding or channel proof.
        with self.assertRaises(store.StoreError):
            self.local()
        self.assertEqual(self.db.bindings(), [])

    def test_local_card_existing_actual_binding_plan_and_decision_are_sql_linked(self):
        self.local_plan()
        grant = self.local()
        self.assertEqual(grant.evidence.approval_kind, "local_card")
        self.assertEqual(grant.evidence.approval_id, REQUEST)
        self.assertEqual(self.db.bindings()[0]["binding_id"], "local")
        self.db.conn.execute("UPDATE binding SET chat_id='oc_other' WHERE binding_id='local'")
        with self.assertRaises(store.StoreError):
            self.local(expected=1, claim_event_id="f" * 64)

    def test_mirror_evidence_is_metadata_only_and_never_invents_approval_format(self):
        # Store cannot verify wire signatures from hashes. This records only the
        # trusted runtime's verified evidence; unresolved parser stays outside.
        grant = self.grant(approval_kind="mirror_approval", approval_id="9" * 64,
                           approval_hash="0" * 64)
        self.assertEqual(grant.evidence.approval_kind, "mirror_approval")
        for approval in ("unsigned text", {"body": "PRIVATE-REMOTE-CANARY"}, "JOIN-87654321"):
            with self.subTest(value=approval), self.assertRaises(store.StoreError):
                self.grant(expected=1, approval_kind="mirror_approval", approval_id=approval)

    def test_evidence_and_capability_validation_is_fixed_canonical_and_bounded(self):
        bad = ({"capabilities": ()}, {"capabilities": ("edit", "message")},
               {"capabilities": ("message", "message")}, {"capabilities": ("admin",)},
               {"capabilities": ["message"]}, {"valid_until": 20}, {"checked_at": True},
               {"checked_at": 21}, {"claim_event_id": "G" * 64}, {"allowlist_hash": "body"},
               {"channel_id": "foreign channel"}, {"chat_ref": "0" * 64},
               {"relay_origin": "https://relay.test/path"}, {"relay_origin": "http://evil.test"},
               {"relay_origin": "https://user:secret@relay.test"})
        for changes in bad:
            with self.subTest(changes=changes), self.assertRaises(store.StoreError):
                self.grant(**changes)
        grant = self.grant(capabilities=("message", "reaction_add"))
        with self.assertRaises(store.StoreError):
            self.reserve(grant, action="edit")

    def test_current_revision_cas_and_target_identity_are_immutable(self):
        first = self.grant()
        self.assertEqual(self.grant(expected=1), first)  # exact evidence is idempotent.
        for changes in ({"chat_id": "oc_other", "chat_ref": digest_chat("oc_other")},
                        {"app_id": "cli_other"}, {"owner_pubkey": "9" * 64}):
            with self.subTest(changes=changes), self.assertRaises(store.StoreError):
                self.grant(expected=1, **changes)
        with self.assertRaises(store.StoreError):
            self.grant(expected=0, claim_event_id="9" * 64)
        second = self.grant(expected=1, claim_event_id="9" * 64, claimed_at=11, checked_at=25, now=25)
        self.assertEqual((second.target_id, second.revision), (first.target_id, 2))
        self.assertNotEqual(second.scope_hash, first.scope_hash)
        self.assertEqual(self.db.remote_grant(first.target_id, revision=1), first)

    def test_pending_unknown_survives_reopen_and_changed_revision_cannot_repin_or_ack(self):
        grant = self.grant()
        created = self.reserve(grant)
        self.assertTrue(created.created)
        self.assertTrue(self.unknown(created.record, grant))
        self.db.close()
        with store.Store(self.path) as db:
            recovered = self.reserve(grant, db)
            self.assertFalse(recovered.created)
            self.assertEqual((recovered.record.id, recovered.record.status), (created.record.id, "unknown"))
            fresh = self.grant(db, expected=1, claim_event_id="9" * 64, claimed_at=11, checked_at=25, now=25)
            unchanged = self.reserve(fresh, db, now=26)
            self.assertFalse(unchanged.created)
            self.assertEqual((unchanged.record.id, unchanged.record.revision), (created.record.id, 1))
            self.assertFalse(self.unknown(created.record, fresh, db, now=26))
            self.assertFalse(self.ack(created.record, fresh, db, now=27))
            self.assertFalse(self.ack(created.record, grant, db, now=27))
            self.assertEqual(db.remote_delivery(created.record.id).status, "unknown")

    def test_ack_requires_own_app_current_scope_live_grant_and_immutable_receipt(self):
        grant = self.grant()
        op = self.reserve(grant).record
        self.unknown(op, grant)
        self.assertFalse(self.ack(op, grant, sender_app_id="cli_other"))
        self.assertFalse(self.ack(op, grant, scope_hash="9" * 64))
        self.assertFalse(self.ack(op, grant, revision=2))
        self.assertTrue(self.ack(op, grant))
        self.assertTrue(self.ack(op, grant))
        self.assertFalse(self.ack(op, grant, message_id="om_other"))
        self.assertFalse(self.ack(op, grant, receipt_hash="9" * 64))
        self.assertEqual(self.db.remote_delivery(op.id).message_id, "om_sent")
        self.assertEqual(self.db.remote_replay_since(grant.target_id, initial_since=77), 77)

    def test_revocation_expiry_and_agent_pause_block_new_dispatch_and_ack_keep_unknown(self):
        for index, mode in enumerate(("suspended", "expired", "paused")):
            with self.subTest(mode=mode):
                base = 20 + index * 20
                grant = self.grant(expected=0 if mode == "suspended" else self.db.remote_grant(target).revision,
                    checked_at=base, now=base, valid_until=base + 100, claimed_at=10 + index,
                    claim_event_id={"suspended": "4", "expired": "9", "paused": "0"}[mode] * 64)
                target = grant.target_id
                op = self.reserve(grant, source={"suspended": "1", "expired": "a", "paused": "b"}[mode] * 64,
                                  now=base + 1).record
                self.unknown(op, grant, now=base + 2)
                if mode == "suspended":
                    self.assertTrue(self.db.suspend_remote_grant(target, expected_revision=grant.revision, now=base + 3))
                if mode == "paused":
                    self.db.conn.execute("UPDATE agent SET status='paused' WHERE pubkey=?", (AGENT,))
                now = base + 101 if mode == "expired" else base + 4
                with self.assertRaises(store.StoreError):
                    self.reserve(grant, source="f" * 64, now=now)
                self.assertFalse(self.ack(op, grant, now=now))
                self.assertEqual(self.db.remote_delivery(op.id).status, "unknown")

    def test_reactions_preserve_exact_opaque_receipt_and_require_fixed_emoji_pair(self):
        grant = self.grant()
        op = self.reserve(grant, action="reaction_add").record
        self.unknown(op, grant)
        rid = "A" * 86 + "=="  # canonical URL-safe encoding of exactly 64 bytes.
        for args in ({"reaction_id": "rid"}, {"emoji": "Typing"},
                     {"reaction_id": "rid", "emoji": "body"}, {"reaction_id": "unsafe/id", "emoji": "Typing"}):
            with self.subTest(args=args), self.assertRaises(store.StoreError):
                self.ack(op, grant, **args)
        self.assertTrue(self.ack(op, grant, reaction_id=rid, emoji="Typing"))
        record = self.db.remote_delivery(op.id)
        self.assertEqual((record.reaction_id, record.emoji), (rid, "Typing"))

    def test_source_action_hash_timestamp_types_and_arbitrary_body_are_rejected(self):
        grant = self.grant()
        for args in ({"source": "om_foreign"}, {"action": "f2b"}, {"action": {"body": "PRIVATE-REMOTE-CANARY"}},
                     {"at": True}, {"at": -1}, {"content": "PRIVATE-REMOTE-CANARY"}, {"root": "not_event"}):
            with self.subTest(args=args), self.assertRaises(store.StoreError):
                self.reserve(grant, **args)
        op = self.reserve(grant).record
        for receipt in ("", None, {"body": "PRIVATE-REMOTE-CANARY"}, "PRIVATE-REMOTE-CANARY"):
            with self.subTest(receipt=receipt), self.assertRaises(store.StoreError):
                self.ack(op, grant, receipt_hash=receipt)
        self.assertEqual(self.db.remote_delivery_by_source(grant.target_id, SOURCE, "message"), op)
        with self.assertRaises(store.StoreError):
            self.reserve(grant, content="9" * 64)

    def test_cursor_is_complete_scan_only_monotonic_and_zero_pending_blocks(self):
        grant = self.grant()
        self.assertEqual(self.db.remote_replay_since(grant.target_id, initial_since=77), 77)
        zero = self.reserve(grant, at=0).record
        late = self.reserve(grant, source="9" * 64, at=2000).record
        self.unknown(late, grant)
        self.ack(late, grant)
        self.assertFalse(self.commit(grant, 2000))
        self.assertEqual(self.db.remote_replay_since(grant.target_id, initial_since=77), 0)
        self.unknown(zero, grant)
        self.ack(zero, grant)
        self.assertFalse(self.commit(grant, 2000, complete=False))
        with self.assertRaises(store.StoreError):
            self.commit(grant, 2000, complete=1)
        self.assertTrue(self.commit(grant, 2000))
        self.assertFalse(self.commit(grant, 1500))
        self.assertEqual(self.db.remote_replay_since(grant.target_id), 1100)
        next_op = self.reserve(grant, source="a" * 64, at=1500).record
        self.assertFalse(self.commit(grant, 3000))
        self.assertEqual(self.db.remote_replay_since(grant.target_id), 600)
        self.assertEqual(next_op.status, "reserved")

    def test_pending_keyset_is_bounded_stable_with_timestamp_ties_across_reopen(self):
        grant = self.grant()
        ids = {self.reserve(grant, source=f"{i:064x}").record.id for i in range(130)}
        first = self.db.pending_remote_deliveries(grant.target_id, limit=128)
        self.assertEqual(len(first), 128)
        last = first[-1]
        self.db.close()
        with store.Store(self.path) as db:
            second = db.pending_remote_deliveries(grant.target_id, after=(last.created_at, last.id), limit=128)
            self.assertEqual({r.id for r in first + second}, ids)
            for args in ({"limit": 129}, {"limit": True}, {"after": (True, last.id)},
                         {"after": (21, "not_hash")}, {"after": [21, last.id]}):
                with self.subTest(args=args), self.assertRaises(store.StoreError):
                    db.pending_remote_deliveries(grant.target_id, **args)

    def test_two_connections_reserve_one_operation_and_atomic_rollback_preserves_revision(self):
        grant = self.grant()
        def reserve(_):
            with store.Store(self.path) as db:
                return self.reserve(grant, db)
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(reserve, range(8)))
        self.assertEqual(sum(r.created for r in results), 1)
        self.assertEqual(len({r.record.id for r in results}), 1)
        with self.assertRaises(RuntimeError):
            with self.db.transaction():
                fresh = self.grant(expected=1, claim_event_id="9" * 64, claimed_at=11, checked_at=25, now=25)
                self.reserve(fresh, source="a" * 64, now=26)
                raise RuntimeError("crash before commit")
        self.assertEqual(self.db.remote_grant(grant.target_id), grant)
        self.assertIsNone(self.db.remote_delivery_by_source(grant.target_id, "a" * 64, "message"))

    def test_ack_and_cursor_nested_transaction_failure_keep_last_valid_records(self):
        grant = self.grant()
        op = self.reserve(grant).record
        self.unknown(op, grant)
        with self.assertRaises(RuntimeError):
            with self.db.transaction():
                self.ack(op, grant)
                self.commit(grant, 1000)
                raise RuntimeError("crash before commit")
        self.assertEqual(self.db.remote_delivery(op.id).status, "unknown")
        self.assertEqual(self.db.remote_replay_since(grant.target_id), 0)
        self.assertTrue(self.ack(op, grant))
        self.assertTrue(self.commit(grant, 1000))

    def test_no_canary_content_reaches_sql_dump_db_or_wal(self):
        grant = self.grant()
        op = self.reserve(grant).record
        self.unknown(op, grant)
        self.ack(op, grant)
        self.assertNotIn("PRIVATE-REMOTE-CANARY", "\n".join(self.db.conn.iterdump()))
        for path in self.path.parent.iterdir():
            self.assertNotIn(b"PRIVATE-REMOTE-CANARY", path.read_bytes())

    def test_wss_first_unknown_reopen_never_raises_uncommitted_history_floor(self):
        grant = self.grant()
        op = self.reserve(grant, at=1000).record
        self.unknown(op, grant)
        self.assertEqual(self.db.remote_replay_since(grant.target_id), 0)
        self.assertEqual(self.db.remote_replay_since(grant.target_id, initial_since=77), 77)
        self.db.close()
        with store.Store(self.path) as db:
            self.assertEqual(db.remote_replay_since(grant.target_id), 0)
            self.assertEqual(db.remote_replay_since(grant.target_id, initial_since=77), 77)
            recovered = self.reserve(grant, db)
            self.assertFalse(recovered.created)
            self.assertEqual(recovered.record.id, op.id)
            # The first WSS row isn't proof that older relay history was read.
            earlier = self.reserve(grant, db, source="a" * 64, at=10).record
            self.assertEqual(db.remote_replay_since(grant.target_id, initial_since=77), 0)
            self.assertEqual(earlier.status, "reserved")

    def test_same_authority_fresh_read_renews_original_unknown_without_redispatch_or_repin(self):
        original = self.grant()
        op = self.reserve(original).record
        self.unknown(op, original)
        self.assertFalse(self.ack(op, original, now=200))  # original proof expired.
        fresh = self.evidence(claim_event_id="9" * 64, agent_profile_event_id="a" * 64,
            agent_policy_event_id="b" * 64, roster_event_id="c" * 64, checked_at=200, valid_until=300)
        self.assertTrue(self.db.refresh_remote_proof(original.target_id, fresh,
                         revision=original.revision, scope_hash=original.scope_hash, now=200))
        self.assertEqual(self.db.remote_grant(original.target_id), original)
        current = self.db.remote_proof(original.target_id)
        self.assertEqual((current.revision, current.scope_hash, current.evidence.checked_at),
                         (original.revision, original.scope_hash, 200))
        self.assertEqual(current.evidence.claim_event_id, "9" * 64)
        self.assertEqual(original.evidence.checked_at, 20)
        self.db.close()
        with store.Store(self.path) as db:
            retry = self.reserve(original, db, now=201)
            self.assertFalse(retry.created)
            self.assertEqual((retry.record.id, retry.record.revision), (op.id, original.revision))
            self.assertTrue(self.ack(op, original, db, now=202))
            self.assertEqual(db.remote_delivery(op.id).revision, original.revision)
            self.assertEqual(db.remote_proof(original.target_id), current)

    def test_changed_authority_cannot_renew_original_proof_or_release_unknown(self):
        original = self.grant()
        op = self.reserve(original).record
        self.unknown(op, original)
        for changes in ({"claimed_at": 11}, {"approval_hash": "f" * 64},
                        {"capabilities": ("message",)}, {"allowlist_hash": "f" * 64},
                        {"mirror_pubkey": "f" * 64}, {"app_id": "cli_other"}):
            with self.subTest(changes=changes):
                self.assertFalse(self.db.refresh_remote_proof(original.target_id,
                    self.evidence(checked_at=200, valid_until=300, **changes),
                    revision=original.revision, scope_hash=original.scope_hash, now=200))
        self.assertFalse(self.ack(op, original, now=200))
        self.assertEqual(self.db.remote_delivery(op.id).status, "unknown")
        self.assertEqual(self.db.remote_grant(original.target_id), original)

    def test_proof_renewal_is_monotonic_scope_exact_and_transactional(self):
        original = self.grant()
        fresh = self.evidence(checked_at=200, valid_until=300, claim_event_id="9" * 64)
        with self.assertRaises(RuntimeError):
            with self.db.transaction():
                self.assertTrue(self.db.refresh_remote_proof(original.target_id, fresh,
                    revision=original.revision, scope_hash=original.scope_hash, now=200))
                raise RuntimeError("proof transaction rollback")
        self.assertEqual(self.db.remote_proof(original.target_id).evidence.checked_at, 20)
        self.assertFalse(self.db.refresh_remote_proof(original.target_id, fresh,
                    revision=2, scope_hash=original.scope_hash, now=200))
        self.assertFalse(self.db.refresh_remote_proof(original.target_id, fresh,
                    revision=original.revision, scope_hash="f" * 64, now=200))
        self.assertTrue(self.db.refresh_remote_proof(original.target_id, fresh,
                    revision=original.revision, scope_hash=original.scope_hash, now=200))
        self.assertFalse(self.db.refresh_remote_proof(original.target_id,
                    self.evidence(checked_at=199, valid_until=301),
                    revision=original.revision, scope_hash=original.scope_hash, now=201))
        self.assertEqual(self.db.remote_proof(original.target_id).evidence, fresh)

    def test_actual_schema_five_every_populated_table_migrates_without_data_loss(self):
        path = Path(self.tmp.name) / "v5" / "state.db"
        path.parent.mkdir(mode=0o700)
        conn = sqlite3.connect(path)
        path.chmod(0o600)
        conn.executescript(SCHEMA5)
        seed_schema5(conn)
        conn.execute("PRAGMA user_version=5")
        names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        before = {name: conn.execute(f'SELECT * FROM "{name}"').fetchall() for name in names}
        self.assertTrue(all(before.values()), "each historical table contains representative metadata")
        conn.commit()
        conn.close()
        with store.Store(path) as db:
            self.assertEqual(db.conn.execute("PRAGMA user_version").fetchone()[0], store.SCHEMA_VERSION)
            for name, rows in before.items():
                self.assertEqual([tuple(r) for r in db.conn.execute(f'SELECT * FROM "{name}"')], rows, name)
            self.assertEqual(db.conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_schema_five_drift_and_unknown_version_fail_closed_without_partial_migration(self):
        for version, drift in ((5, True), (99, False)):
            with self.subTest(version=version):
                path = Path(self.tmp.name) / f"drift{version}" / "state.db"
                path.parent.mkdir(mode=0o700)
                conn = sqlite3.connect(path)
                path.chmod(0o600)
                conn.executescript(SCHEMA5)
                conn.execute("INSERT INTO agent VALUES(?,?,?,NULL,'active',10)", (AGENT, OWNER, APP))
                if drift:
                    conn.execute("CREATE TABLE alien(metadata TEXT)")
                conn.execute(f"PRAGMA user_version={version}")
                conn.commit()
                conn.close()
                with self.assertRaises(store.StoreError):
                    store.Store(path)
                with sqlite3.connect(path) as conn:
                    self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], version)
                    self.assertEqual(conn.execute("SELECT pubkey FROM agent").fetchone()[0], AGENT)
                    self.assertIsNone(conn.execute("SELECT name FROM sqlite_master WHERE name='remote_target'").fetchone())


def digest_chat(chat):
    return hashlib.sha256(("buzz-feishu-chat:v1:" + chat).encode()).hexdigest()


def seed_schema5(c):
    """One valid metadata row in each exact v5 table, with real FK graph."""
    c.execute("INSERT INTO app_profile VALUES('cli_reader','/cfg','/data')")
    c.execute("INSERT INTO binding VALUES('local','channel','oc_local','cli_reader','/cfg','',?,'active',10,10)", ("0" * 64,))
    c.execute("INSERT INTO agent VALUES(?,?,?,NULL,'active',10)", (AGENT, OWNER, APP))
    c.execute("INSERT INTO delivery VALUES(?,'local',?,'1','b2f','relay','om_sent','','','acked',1000,1,10,10)", (SOURCE, AGENT))
    c.execute("INSERT INTO cursor VALUES('local',?,'relay',1000,10)", (AGENT,))
    c.execute("INSERT INTO join_request VALUES(?,?,?,?,'oc_local','local','channel','approved','om_card',1,10,12,604810)", (REQUEST, AGENT, OWNER, APP))
    c.execute("INSERT INTO join_decision VALUES(?,?,?,12)", (APP, DECISION, REQUEST))
    c.execute("INSERT INTO agent_chat VALUES(?,'oc_local',?,'local','active',12)", (AGENT, "0" * 64))
    c.execute("INSERT INTO connection VALUES('outlet',?,'local','connected',10,10,0,'',10)", (AGENT,))
    c.execute("INSERT INTO state_snapshot VALUES('local',7,?,'/legacy/state.json',?,'legacy:cli_reader:union_id',10)", (CONTENT, RECEIPT))
    c.execute("INSERT INTO state_migration VALUES('local','old','new',10)")
    c.execute("INSERT INTO state_scalar VALUES('local','last_feishu',NULL,1000)")
    c.execute("INSERT INTO state_map VALUES('local','b2f',?,'om_sent',NULL)", (SOURCE,))
    c.execute("INSERT INTO state_member_event VALUES('local','add',?,?,10,9000,'[]',?)", (CONTENT, AGENT, "0" * 128))
    c.execute("INSERT INTO join_transport VALUES(?,1,100,0,1,'sent',0,'om_card',12)", (REQUEST,))
    c.execute("INSERT INTO join_feedback VALUES(?,'feedback',?,'on_operator','owner','sent',12)", (APP, REQUEST))
    c.execute("INSERT INTO join_notice VALUES(?,'om_card','approved','pending',0,0,0,12)", (REQUEST,))
    c.execute("INSERT INTO event_target VALUES('local','cli_reader','om_target','','im.message.receive_v1',10)")
    c.execute("INSERT INTO outlet_receipt VALUES(?,?,'om_sent','','')", (SOURCE, APP))
    c.execute("INSERT INTO effect_plan VALUES(?,'channel','local',?,'/local/mirror.env','/cfg',10,12)", (REQUEST, MIRROR))
    c.execute("INSERT INTO effect_step VALUES(?,'runtime','unknown',?,'','',0,1,10,12)", (REQUEST, CONTENT))
    c.execute("INSERT INTO restart_operation VALUES('restart',?,?,?,1,10,?,NULL,NULL,NULL,'unknown',10,12)", (AGENT, CONTENT, RECEIPT, "a" * 32))
    c.execute("INSERT INTO console_operation VALUES(?,?,?,'agent',NULL,?,'restart',?,'unknown','recovered',10,12,'restart',NULL)", (SOURCE, CONTENT, RECEIPT, AGENT, "f" * 64))


if __name__ == "__main__":
    unittest.main()
