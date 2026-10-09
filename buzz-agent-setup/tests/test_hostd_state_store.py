"""Legacy ledger import and durable metadata snapshots, never message bodies."""
import dataclasses
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

HOSTD = Path(__file__).resolve().parents[1] / "scripts" / "hostd"
sys.path.insert(0, str(HOSTD)); sys.path.insert(0, str(HOSTD.parent))
import store
import buzz_feishu_group_sync as gs
try:
    import state_store
except ImportError:
    state_store = None

CHANNEL = "11111111-1111-1111-1111-111111111111"
HEX = "a" * 64
OTHER = "b" * 64
CANARY = "SECRET BODY canary never persist"


class StateStoreTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(state_store, "hostd.state_store not implemented")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = store.Store(self.root / "db" / "hostd.db")
        self.addCleanup(self.db.close)
        self.db.reconcile_bindings([store.BindingRecord("alpha", CHANNEL, "oc_a", "cli_a", "/cfg/alpha", "/lark/a", "/data/a")], now=100)
        self.legacy = self.root / "legacy"
        self.legacy.mkdir(mode=0o700)
        self.adapter = state_store.StateAdapter(self.db, "alpha", self.legacy)

    def full_state(self):
        stream = "c" * 64
        op = f"9000|{OTHER}|member|u:on_u"
        event = gs.sign_event("0" * 63 + "1", 9000,
                              [["h", CHANNEL], ["p", OTHER], ["role", "member"],
                               [gs.MEMBER_EVENT_NONCE_TAG, "d" * 64], [gs.MEMBER_EVENT_STREAM_TAG, stream],
                               [gs.MEMBER_EVENT_SEQUENCE_TAG, "1"]], "", 100)
        return gs.State(binding=CHANNEL + "|oc_a", floor=1, buzz_since=50, feishu_since=50, react_since=50,
                        b2f={HEX: "om_target", OTHER: "pending:100:om_parent,card"},
                        b2f_modes={HEX: "card"}, b2f_senders={HEX: "cli_a"}, f2b={"om_source": HEX},
                        e2f={"e" * 64: f"pending:100:{HEX}|om_target|card"}, edit_unresolved={"e" * 64: 100},
                        attempts={"b2f:" + OTHER: 2}, unresolved={OTHER: 100}, f_unresolved={"om_retry": 100},
                        threads={"om_root": 100}, polled={"om_root": 90}, tried={"om_root": 80},
                        r2f={"f" * 64: f"{OTHER}|om_target|SMILE|reaction_a"},
                        idmap={"ou_a": "on_u"}, emailmap={HEX: "ou_a"},
                        images={HEX + ":0": "retry:100", HEX + ":thread": "om_parent"}, img_unresolved={HEX + ":0": 100},
                        members_synced=100, feishu_seen={"u:on_u": ""}, buzz_seen={OTHER: "u:on_u"},
                        member_notes={"unresolved:u:on_u": 100}, member_events={op: event},
                        member_event_stream=stream, member_event_seq=1, member_event_blocks={op: "retry_refused"},
                        member_notice_event=HEX, member_notice_feishu="om_notice", member_notice_sender="cli_a",
                        member_notice_content=CANARY, member_notice_active=True, people_seen={"u:on_u": OTHER + "|100"},
                        agent_intros_initialized=True, agent_intros={OTHER: "pending:100"}, agent_intro_senders={OTHER: "cli_a"},
                        rwatch={"om_target": HEX + "|1000"}, f2r={"om_target|on_u|SMILE": "pending:100"},
                        claim_notes={"feishu:" + "a" * 40: "retry:100"}, claim_takeovers={"a" * 40: 100})

    def legacy_write(self, state):
        path = self.legacy / "state.json"
        path.write_text(json.dumps(dataclasses.asdict(state)))
        path.chmod(0o600)
        return path

    def test_import_once_preserves_critical_fields_and_original_bytes(self):
        state = self.full_state()
        path = self.legacy_write(state)
        original = path.read_bytes()
        loaded = self.adapter.load()
        expected = dataclasses.asdict(state)
        expected["member_notice_content"] = ""
        self.assertEqual(dataclasses.asdict(loaded), expected)
        self.assertEqual(path.read_bytes(), original)
        path.write_text("corrupt after import")
        self.assertEqual(dataclasses.asdict(self.adapter.load()), expected)
        snapshot = self.db.conn.execute("SELECT imported_hash FROM state_snapshot").fetchone()
        self.assertEqual(snapshot[0], hashlib.sha256(original).hexdigest())

    def test_restart_exact_member_retry_and_no_body_in_db_wal_or_dump(self):
        state = self.full_state()
        self.adapter.load()
        self.adapter.save(state, now=101)
        other = store.Store(self.db.path)
        self.addCleanup(other.close)
        restarted = state_store.StateAdapter(other, "alpha", self.legacy).load()
        self.assertEqual(restarted.member_events, state.member_events)
        self.assertEqual(restarted.member_event_blocks, state.member_event_blocks)
        self.assertEqual(restarted.e2f, state.e2f)
        self.assertNotIn(CANARY, "\n".join(other.conn.iterdump()))
        for path in self.db.path.parent.iterdir():
            self.assertNotIn(CANARY.encode(), path.read_bytes())

    def old_snapshot(self):
        golden=json.loads((Path(__file__).parent/'fixtures/hostd/state-before-intro-cards.json').read_text())
        self.legacy_write(gs.state_from_data(golden['state']))
        self.adapter.load()
        self.db.conn.execute('UPDATE state_snapshot SET state_hash=?',(golden['state_hash'],))
        return golden

    def test_actual_old_release_snapshot_loads_without_writing_then_upgrades_on_save(self):
        golden=self.old_snapshot()
        before='\n'.join(self.db.conn.iterdump())
        loaded=self.adapter.load()
        self.assertEqual('\n'.join(self.db.conn.iterdump()),before)
        self.assertEqual(loaded.agent_intro_formats,{})
        self.assertEqual(loaded.agent_intros,golden['state']['agent_intros'])
        revision=self.adapter.revision
        self.adapter.save(loaded,now=101)
        self.assertEqual(self.adapter.revision,revision+1)
        self.assertNotEqual(self.db.conn.execute('SELECT state_hash FROM state_snapshot').fetchone()[0],golden['state_hash'])
        self.assertEqual(state_store.StateAdapter(self.db,'alpha',self.legacy).load(),loaded)

    def test_legacy_hash_never_ignores_new_format_or_modified_old_field(self):
        self.old_snapshot()
        self.db.conn.execute('INSERT INTO state_map VALUES(?,?,?,?,?)',('alpha','agent_intro_formats',OTHER,'card_v1',None))
        with self.assertRaises(ValueError):self.adapter.load()
        self.db.conn.execute("DELETE FROM state_map WHERE field='agent_intro_formats'")
        self.db.conn.execute("UPDATE state_scalar SET value_int=51 WHERE field='buzz_since'")
        with self.assertRaises(ValueError):self.adapter.load()

    def test_older_schema_uses_existing_migration_validation(self):
        data = {"binding": CHANNEL + "|oc_a", "floor": 1, "buzz_since": 50, "feishu_since": 60,
                "buzz_floor": 0, "feishu_floor": 0, "b2f": {HEX: "om_target"}, "f2b": {}, "attempts": {},
                "threads": {}, "polled": {}, "tried": {}, "unresolved": {}, "f_unresolved": {}}
        path = self.legacy / "state.json"
        path.write_text(json.dumps(data)); path.chmod(0o600)
        self.assertEqual(dataclasses.asdict(self.adapter.load()), dataclasses.asdict(gs.state_from_data(data)))

    def test_mixed_case_feishu_reaction_types_survive_import_and_restart(self):
        state = gs.State(binding=CHANNEL + '|oc_a',
            r2f={HEX: f'{OTHER}|om_target|Typing|reaction_a',
                 OTHER: f'{HEX}|om_other|CrossMark|reaction_b'},
            f2r={'om_target|on_user|Typing': HEX, 'om_other|on_user|CrossMark': OTHER})
        path = self.legacy_write(state)
        original = path.read_bytes()
        loaded = self.adapter.load()
        self.assertEqual(loaded.r2f, state.r2f)
        self.assertEqual(loaded.f2r, state.f2r)
        self.adapter.save(loaded, now=100)
        restarted = state_store.StateAdapter(self.db, 'alpha', self.legacy).load()
        self.assertEqual(restarted.r2f, state.r2f)
        self.assertEqual(restarted.f2r, state.f2r)
        self.assertEqual(path.read_bytes(), original)

    def test_urlsafe_padded_reaction_ack_survives_import_restart_and_export(self):
        import base64
        reaction_id=base64.urlsafe_b64encode(bytes([251])*64).decode()
        state=self.full_state()
        state.r2f={HEX:f'{OTHER}|om_target|CrossMark|{reaction_id}',
                   OTHER:f'{HEX}|om_other|Typing|{reaction_id}|desk:cli_a'}
        path=self.legacy_write(state);original=path.read_bytes()
        imported=self.adapter.load()
        self.assertEqual(imported.r2f,state.r2f)
        self.assertEqual(imported.b2f,state.b2f);self.assertEqual(imported.f2r,state.f2r)
        self.assertEqual(imported.e2f,state.e2f);self.assertEqual(imported.attempts,state.attempts)
        self.assertEqual(imported.react_since,state.react_since)
        self.adapter.save(imported,now=101)
        restarted=state_store.StateAdapter(self.db,'alpha',self.legacy).load()
        self.assertEqual(restarted.r2f,state.r2f)
        rollback=self.root/'rollback';self.adapter.export_legacy(rollback)
        exported=gs.load_state(rollback)
        self.assertEqual(exported.r2f,state.r2f);self.assertEqual(path.read_bytes(),original)
    def test_padded_reaction_ids_reject_wrong_size_padding_and_body(self):
        import base64
        valid=base64.urlsafe_b64encode(bytes([251])*64).decode()
        for value in [valid[:-3]+'B==',valid+'=',valid[:-1],base64.urlsafe_b64encode(bytes(65)).decode(),valid+' BODY', 'CANARY BODY==']:
            self.assertFalse(state_store._value('r2f',f'{OTHER}|om_target|CrossMark|{value}'))

    def test_corrupt_import_fails_closed_instead_of_empty_state(self):
        path = self.legacy / "state.json"
        path.write_text("{broken SECRET BODY canary"); path.chmod(0o600)
        with self.assertRaises(store.StoreError) as caught:
            self.adapter.load()
        self.assertNotIn("SECRET BODY", str(caught.exception))
        self.assertEqual(self.db.conn.execute("SELECT count(*) FROM state_snapshot").fetchone()[0], 0)

    def test_unsafe_symlink_import_fails_closed(self):
        path = self.legacy_write(gs.State())
        real = path.with_name("actual.json"); path.rename(real); path.symlink_to(real)
        with self.assertRaises(store.StoreError):
            self.adapter.load()

    def test_arbitrary_body_in_metadata_or_nonempty_member_event_is_rejected(self):
        self.adapter.load()
        state = gs.State(binding=CHANNEL + "|oc_a", b2f={HEX: CANARY})
        with self.assertRaises(store.StoreError):
            self.adapter.save(state, now=100)
        state = self.full_state()
        next(iter(state.member_events.values()))["content"] = CANARY
        with self.assertRaises(store.StoreError):
            self.adapter.save(state, now=100)
        self.assertNotIn(CANARY, "\n".join(self.db.conn.iterdump()))

    def test_two_snapshot_writers_use_revision_cas(self):
        self.adapter.load()
        stale = state_store.StateAdapter(self.db, "alpha", self.legacy)
        old = stale.load()
        current = self.adapter.load()
        current.f2b["om_new"] = HEX
        self.adapter.save(current, now=101)
        with self.assertRaises(store.StoreError):
            stale.save(old, now=102)
        self.assertEqual(self.adapter.load().f2b, {"om_new": HEX})

    def test_save_transaction_rollback_does_not_ack_or_advance_checkpoint(self):
        state = self.adapter.load()
        with self.assertRaises(RuntimeError):
            with self.db.transaction():
                state.f2b["om_new"] = HEX
                state.feishu_since = 2000
                self.adapter.save(state, now=2001)
                raise RuntimeError("crash")
        self.assertFalse(state_store.StateAdapter(self.db, "alpha", self.legacy).load().f2b)
        self.assertEqual(self.db.cursor_position("alpha", "feishu"), 0)

    def test_explicit_rollback_export_is_compatible_and_not_a_double_writer(self):
        self.legacy_write(self.full_state())
        self.adapter.load()
        output = self.root / "rollback"
        self.adapter.export_legacy(output)
        self.assertEqual(dataclasses.asdict(gs.load_state(output)), dataclasses.asdict(self.adapter.load()))
        self.assertEqual((output / "state.json").stat().st_mode & 0o777, 0o600)

    def test_identity_epoch_migration_preserves_pending_and_delivery_ledger(self):
        self.legacy_write(self.full_state())
        self.adapter.load()
        before = self.adapter.load()
        self.adapter.migrate_identity("cli_a", now=101)
        after = self.adapter.load()
        for field in ("b2f", "f2b", "e2f", "threads", "r2f", "f2r", "member_events", "member_event_blocks", "member_event_stream", "member_event_seq"):
            self.assertEqual(getattr(after, field), getattr(before, field))
        self.assertEqual((after.idmap, after.emailmap, after.feishu_seen, after.buzz_seen, after.members_synced), ({}, {}, {}, {}, 0))
        self.assertEqual(self.db.conn.execute("SELECT count(*) FROM state_migration").fetchone()[0], 1)
        self.adapter.migrate_identity("cli_a", now=102)
        self.assertEqual(self.db.conn.execute("SELECT count(*) FROM state_migration").fetchone()[0], 1)

    def test_snapshot_corruption_is_not_interpreted_as_missing_delivery(self):
        self.legacy_write(self.full_state())
        self.adapter.load()
        self.db.conn.execute("DELETE FROM state_map WHERE field='b2f'")
        with self.assertRaises(store.StoreError):
            self.adapter.load()

    def test_migration_candidate_cannot_discard_delivery_or_member_retry(self):
        self.legacy_write(self.full_state())
        state = self.adapter.load()
        state.b2f = {}
        with self.assertRaises(store.StoreError):
            self.adapter.migrate_identity("bot:cli_a:union_id:v1", candidate=state, now=101)
        self.assertEqual(self.adapter.identity_profile, "")
        self.assertTrue(self.adapter.load().b2f)

    def test_snapshot_metadata_reports_verified_reader_epoch_only(self):
        self.adapter.load()
        self.assertIsNone(self.adapter.metadata()["reader_app_id"])
        self.adapter.migrate_identity("bot:cli_a:union_id:v1", now=101)
        self.assertEqual(self.adapter.metadata()["reader_app_id"], "cli_a")

    def test_pending_delivery_holds_round_checkpoint_as_well_as_sql_cursor(self):
        state = self.adapter.load()
        state.binding = CHANNEL + "|oc_a"
        state.f2b = {"om_pending": "pending:1000"}
        state.f_unresolved = {"om_pending": 1000}
        state.feishu_since = 2000
        self.adapter.save(state, now=2001)
        self.assertLess(self.adapter.load().feishu_since, 1000)
        self.assertLess(self.db.cursor_position("alpha", "feishu"), 1000)

    def test_round_retry_transitions_sql_failed_back_to_same_pending_operation(self):
        state = self.adapter.load()
        state.binding = CHANNEL + "|oc_a"
        state.f2b = {"om_retry": "retry:100"}
        state.f_unresolved = {"om_retry": 100}
        self.adapter.save(state, now=101)
        before = self.db.conn.execute("SELECT * FROM delivery WHERE direction='f2b'").fetchone()
        self.assertEqual(before["status"], "failed")
        state = self.adapter.load()
        state.f2b["om_retry"] = "pending:100"
        self.adapter.save(state, now=102)
        after = self.db.conn.execute("SELECT * FROM delivery WHERE direction='f2b'").fetchone()
        self.assertEqual((after["id"], after["status"], after["attempts"]), (before["id"], "pending", 2))

    def test_removed_reaction_updates_ack_and_terminal_failures_do_not_block_cursor(self):
        state = self.adapter.load()
        state.binding = CHANNEL + "|oc_a"
        state.r2f = {HEX: f"{OTHER}|om_target|SMILE|reaction_a"}
        self.adapter.save(state, now=100)
        state = self.adapter.load()
        state.r2f[HEX] = "removed"
        state.f2b["om_failed"] = "failed"
        state.f_unresolved = {}
        state.feishu_since = 2000
        state.react_since = 2000
        self.adapter.save(state, now=2001)
        rows = {row["direction"]: dict(row) for row in self.db.conn.execute("SELECT * FROM delivery")}
        self.assertEqual((rows["r2f"]["status"], rows["r2f"]["target_id"]), ("removed", "om_target"))
        self.assertEqual(rows["f2b"]["status"], "abandoned")
        self.assertEqual(self.adapter.load().feishu_since, 2000)
        self.assertEqual(self.adapter.load().react_since, 2000)

    def test_outer_transaction_failure_restores_adapter_revision(self):
        state = self.adapter.load()
        revision = self.adapter.revision
        with self.assertRaises(RuntimeError):
            with self.db.transaction():
                self.adapter.save(state, now=100)
                raise RuntimeError("crash")
        self.assertEqual(self.adapter.revision, revision)
        self.adapter.save(state, now=101)

    def test_migration_cannot_collapse_two_author_acks_or_change_author_without_proof(self):
        state = self.adapter.load()
        state.binding = CHANNEL + "|oc_a"
        state.f2r = {"om_target|on_a|SMILE": HEX, "om_target|on_b|SMILE": HEX}
        self.adapter.save(state, now=100)
        candidate = self.adapter.load()
        candidate.f2r = {"om_target|on_c|SMILE": HEX}
        with self.assertRaises(store.StoreError):
            self.adapter.migrate_identity("bot:cli_a:union_id:v1", candidate=candidate, now=101)
        candidate.f2r = {"om_target|on_c|SMILE": HEX, "om_target|on_d|SMILE": HEX}
        with self.assertRaises(store.StoreError):
            self.adapter.migrate_identity("bot:cli_a:union_id:v1", candidate=candidate, now=101)


if __name__ == "__main__":
    unittest.main()
