"""Approval metadata journal: actual SQLite and signed canonical wire pins."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_buzz_feishu_group_sync as base
import test_hostd_remote_ledger as historical
from hostd import store

gs = base.FGS
setUpModule = base.setUpModule
tearDownModule = base.tearDownModule
REQUEST, BINDING = 'JOIN-aabbccdd', 'local'
CARD, DECISION = 'om_actual_card', 'actual-owner-callback'
CREATED, DECIDED, NOW = 10, 20, 30
AGENT, OWNER, MIRROR = base.AGENT2_PK, base.OWNER_PK, base.MIRROR_PK
PREFIX = 'hostd-card-approval-v1'
FROZEN = dict(historical.FROZEN)
FROZEN['_UPGRADE_V6_SCHEMA'] = 'bb3af4b1e1be8083ec11375e5e83ea3a6a9850a024935a3c109951107269a945'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def text_hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


def scope_hash(values):
    return digest({key: values[key] for key in ('binding_id', 'agent_pubkey', 'agent_owner_pubkey',
        'app_id', 'channel_id', 'chat_ref', 'mirror_pubkey', 'mirror_owner_pubkey', 'claimed_at')})


class ApprovalJournal(base.TmpCase):
    def setUp(self):
        super().setUp()
        self.path = self.tmp/'private'/'hostd.db'
        self.db = store.Store(self.path)
        self.addCleanup(self.db.close)
        self.db.register_agent(AGENT, owner_pubkey=OWNER, app_id=base.AGENT_APP,
                               config_path='/approved/agent.env',now=CREATED)
        self.db.reconcile_bindings([store.BindingRecord(BINDING, base.CHANNEL, base.CHAT,
            base.AGENT_APP, '/approved/config.json', '/approved/bot', '/approved/data', MIRROR)], now=CREATED)
        self.db.create_join(REQUEST, AGENT, OWNER, base.AGENT_APP, base.CHAT,
            kind='channel', binding_id=BINDING, now=CREATED)
        self.db.rotate_card(REQUEST, CARD, now=CREATED+1)
        self.assertTrue(self.db.decide_join(REQUEST, DECISION, OWNER, base.AGENT_APP, CARD, 1,
                                            approved=True, now=DECIDED))
        self.db.ensure_effect_plan(REQUEST, base.CHANNEL, BINDING, '/approved/agent.env',
                                  '/approved/config.json', now=DECIDED)

    def pin(self, **changes):
        body = dict(version=1, decision='approve', agent_pubkey=AGENT, agent_owner_pubkey=OWNER,
            app_id=base.AGENT_APP, channel_id=base.CHANNEL, chat_ref=gs.chat_ref(base.CHAT),
            mirror_pubkey=MIRROR, mirror_owner_pubkey=OWNER, claimed_at=15, request_id=REQUEST,
            request_created_at=CREATED, request_deadline=CREATED+7*86400, card_generation=1,
            card_message_sha256=text_hash(CARD), decision_event_sha256=text_hash(DECISION), decision_at=DECIDED)
        content = json.dumps(body, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
        content_hash = text_hash(content)
        event = gs.sign_event(base.MIRROR_KEY, 30078,
            [['t',PREFIX], ['h',base.CHANNEL], ['p',AGENT], ['d',PREFIX+':'+content_hash]], content, NOW)
        values = {key:value for key,value in body.items() if key not in ('version','decision')}
        values.update(binding_id=BINDING, event_created_at=NOW, content_hash=content_hash,
            event_id=event['id'], signature=event['sig'], protectedfiles_hash='1'*64,
            plan_hash=digest(self.db.effect_plan(REQUEST)))
        values['scope_hash'] = scope_hash(values)
        values.update(changes)
        return store.ApprovalPublicationPin(**values)

    def reserve(self, pin=None, db=None, now=NOW):
        return (db or self.db).reserve_approval_publication(pin or self.pin(), now=now)

    def unknown(self, pin, db=None, now=NOW):
        return (db or self.db).mark_approval_unknown(REQUEST, pin.event_id, now=now)

    def test_reads_first_actual_approved_current_card_decision_as_hashes(self):
        value = self.db.card_approval_decision(REQUEST)
        self.assertEqual(type(value), store.CardApprovalDecision)
        self.assertEqual((value.agent_pubkey,value.agent_owner_pubkey,value.app_id), (AGENT,OWNER,base.AGENT_APP))
        self.assertEqual((value.card_generation,value.card_message_sha256,value.decision_event_sha256,
            value.decision_at,value.request_created_at,value.request_deadline),
            (1,text_hash(CARD),text_hash(DECISION),DECIDED,CREATED,CREATED+7*86400))
        self.assertNotIn(CARD,repr(value));self.assertNotIn(DECISION,repr(value))
        for status in ('applied','done'):
            self.db.conn.execute('UPDATE join_request SET status=?',(status,))
            self.assertEqual(self.db.card_approval_decision(REQUEST),value)

    def test_sql_rejected_expired_unapproved_wrong_owner_card_and_duplicate_decision_fail_closed(self):
        original = self.db.join_request(REQUEST)
        for status in ('requested','denied','expired'):
            self.db.conn.execute('UPDATE join_request SET status=?',(status,))
            self.assertIsNone(self.db.card_approval_decision(REQUEST))
        self.db.conn.execute("UPDATE join_request SET status='approved'")
        self.db.conn.execute('UPDATE agent SET owner_pubkey=?',('f'*64,))
        self.assertIsNone(self.db.card_approval_decision(REQUEST))
        self.db.conn.execute('UPDATE agent SET owner_pubkey=?',(OWNER,))
        self.db.conn.execute('UPDATE join_request SET card_generation=0')
        self.assertIsNone(self.db.card_approval_decision(REQUEST))
        self.db.conn.execute('UPDATE join_request SET card_generation=?',(original['card_generation'],))
        self.db.conn.execute('INSERT INTO join_decision VALUES(?,?,?,?)',('cli_foreign','other',REQUEST,DECIDED))
        self.assertIsNone(self.db.card_approval_decision(REQUEST))

    def test_exact_pin_reserved_then_unknown_survives_actual_close_reopen(self):
        pin=self.pin();reservation=self.reserve(pin)
        self.assertTrue(reservation.created);self.assertEqual(reservation.record.pin,pin)
        self.assertEqual(reservation.record.state,'reserved')
        self.assertTrue(self.unknown(pin));self.db.close()
        with store.Store(self.path) as reopened:
            record=reopened.approval_publication(REQUEST)
            self.assertEqual(record.pin,pin);self.assertEqual(record.state,'unknown')
            retry=self.reserve(pin,reopened,now=NOW+100000)
            self.assertFalse(retry.created);self.assertEqual(retry.record.pin,pin)
            self.assertEqual(retry.record.state,'unknown')

    def test_unknown_never_leases_repins_resigns_or_adopts_new_timestamp(self):
        pin=self.pin();self.reserve(pin);self.unknown(pin)
        for key,value in (('event_created_at',NOW+1),('signature','a'*128),('claimed_at',16),
                          ('protectedfiles_hash','2'*64),('plan_hash','3'*64),('content_hash','4'*64)):
            with self.subTest(field=key),self.assertRaises(store.StoreError):
                self.reserve(replace(pin,**{key:value}),now=NOW+100000)
        self.assertEqual(self.db.approval_publication(REQUEST).pin,pin)
        self.assertFalse(self.unknown(pin,now=NOW+100000))

    def test_wire_metadata_or_sql_binding_plan_mismatch_cannot_reserve(self):
        pin=self.pin()
        for key,value in (('app_id','cli_foreign'),('agent_owner_pubkey','f'*64),
            ('card_generation',2),('decision_event_sha256','e'*64),('channel_id',historical.CHANNEL),
            ('mirror_pubkey','c'*64),('scope_hash','d'*64),('event_id','b'*64),
            ('signature',True),('event_created_at',False)):
            with self.subTest(field=key),self.assertRaises(store.StoreError):self.reserve(replace(pin,**{key:value}))
        self.assertIsNone(self.db.approval_publication(REQUEST))

    def test_authority_revoked_after_reservation_prevents_dispatch_mark(self):
        pin=self.pin();self.reserve(pin)
        self.db.conn.execute("UPDATE agent SET status='retired'")
        self.assertFalse(self.unknown(pin))
        self.assertEqual(self.db.approval_publication(REQUEST).state,'reserved')

    def test_ack_requires_exact_unknown_pin_and_still_current_sql_authority(self):
        pin=self.pin();self.reserve(pin)
        self.assertFalse(self.db.ack_approval_publication(REQUEST,pin.event_id,'5'*64,now=NOW))
        self.unknown(pin)
        self.assertFalse(self.db.ack_approval_publication(REQUEST,'f'*64,'5'*64,now=NOW))
        self.db.conn.execute("UPDATE binding SET status='retired'")
        self.assertFalse(self.db.ack_approval_publication(REQUEST,pin.event_id,'5'*64,now=NOW))
        self.assertEqual(self.db.approval_publication(REQUEST).state,'unknown')

    def test_exact_ack_is_terminal_without_deleting_original_operation(self):
        pin=self.pin();self.reserve(pin);self.unknown(pin)
        self.assertTrue(self.db.ack_approval_publication(REQUEST,pin.event_id,'5'*64,now=NOW+1))
        record=self.db.approval_publication(REQUEST)
        self.assertEqual((record.state,record.observed_hash,record.pin),('acked','5'*64,pin))
        self.assertFalse(self.reserve(pin,now=NOW+2).created)
        self.assertFalse(self.db.ack_approval_publication(REQUEST,pin.event_id,'6'*64,now=NOW+2))

    def test_two_real_sql_connections_reserve_one_exact_original_event(self):
        pin=self.pin()
        def reserve():
            with store.Store(self.path) as db:return self.reserve(pin,db).created
        with ThreadPoolExecutor(max_workers=2) as workers:
            flags=list(workers.map(lambda _:reserve(),range(2)))
        self.assertEqual(sorted(flags),[False,True]);self.assertEqual(self.db.approval_publication(REQUEST).pin,pin)

    def test_full_pin_roundtrip_is_metadata_only_and_sql_failure_rolls_back(self):
        pin=self.pin()
        with self.assertRaises(RuntimeError),self.db.transaction():
            self.reserve(pin);self.unknown(pin);raise RuntimeError('offline rollback')
        self.assertIsNone(self.db.approval_publication(REQUEST))
        self.reserve(pin)
        columns={r[1] for r in self.db.conn.execute('PRAGMA table_info(approval_publication)')}
        self.assertFalse(columns & {'body','content','secret','key','token','card_message_id','decision_event_id','open_id','union_id'})
        rows=self.db.conn.execute('SELECT * FROM approval_publication').fetchall()
        raw=repr([tuple(row) for row in rows])
        self.assertNotIn(CARD,raw);self.assertNotIn(DECISION,raw);self.assertNotIn(base.MIRROR_KEY,raw)

    def test_actual_populated_v6_every_old_table_and_sql_bytes_survive_upgrade(self):
        source_path=self.tmp/'seed'/'source.db'
        with store.Store(source_path) as seeded:
            historical.seed_schema5(seeded.conn)
            helper=historical.RemoteLedger()
            evidence=store.RemoteGrantEvidence(**vars(helper.evidence()))
            grant=seeded.activate_remote_grant(evidence,expected_revision=0,now=20)
            delivery=seeded.reserve_remote_delivery(grant.target_id,historical.SOURCE,'message',
                revision=grant.revision,scope_hash=grant.scope_hash,source_at=20,
                content_hash=historical.CONTENT,now=21)
            self.assertTrue(seeded.mark_remote_unknown(delivery.record.id,revision=grant.revision,
                scope_hash=grant.scope_hash,now=22))
            self.assertTrue(seeded.ack_remote_delivery(delivery.record.id,revision=grant.revision,
                scope_hash=grant.scope_hash,sender_app_id=historical.APP,message_id='om_sent',
                receipt_hash=historical.RECEIPT,now=23))
            self.assertTrue(seeded.commit_remote_cursor(grant.target_id,20,revision=grant.revision,
                scope_hash=grant.scope_hash,complete=True,now=24))
            with sqlite3.connect(':memory:') as v6_schema:
                v6_schema.executescript(store._SCHEMA_V5+store._UPGRADE_V6_SCHEMA)
                names=[row[0] for row in v6_schema.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            before={name:[tuple(r) for r in seeded.conn.execute(f'SELECT * FROM "{name}"')] for name in names}
        self.assertEqual(len(before),27);self.assertTrue(all(before.values()))
        path=self.tmp/'v6'/'state.db';path.parent.mkdir(mode=0o700)
        with sqlite3.connect(path) as conn:
            conn.executescript(store._SCHEMA_V5+store._UPGRADE_V6_SCHEMA)
            for name,rows in before.items():
                conn.executemany(f'INSERT INTO "{name}" VALUES({",".join("?" for _ in rows[0])})',rows)
            conn.execute('PRAGMA user_version=6')
        conn.close()
        path.chmod(0o600)
        with store.Store(path) as migrated:
            self.assertEqual(migrated.conn.execute('PRAGMA user_version').fetchone()[0],store.SCHEMA_VERSION)
            for name,rows in before.items():self.assertEqual([tuple(r) for r in migrated.conn.execute(f'SELECT * FROM "{name}"')],rows,name)
            self.assertEqual(migrated.conn.execute('PRAGMA foreign_key_check').fetchall(),[])
            self.assertEqual(migrated.conn.execute('SELECT count(*) FROM approval_publication').fetchone()[0],0)
        for name,sha in FROZEN.items():self.assertEqual(hashlib.sha256(getattr(store,name).encode()).hexdigest(),sha)

    def test_drifted_v6_fails_closed_without_partial_new_schema_or_lost_rows(self):
        path=self.tmp/'drift'/'state.db';path.parent.mkdir(mode=0o700)
        with sqlite3.connect(path) as conn:
            conn.executescript(store._SCHEMA_V5+store._UPGRADE_V6_SCHEMA)
            historical.seed_schema5(conn);conn.execute('CREATE TABLE alien(metadata TEXT)')
            conn.execute('PRAGMA user_version=6')
        conn.close()
        path.chmod(0o600)
        with self.assertRaises(store.StoreError):store.Store(path)
        with sqlite3.connect(path) as conn:
            self.assertEqual(conn.execute('PRAGMA user_version').fetchone()[0],6)
            self.assertIsNone(conn.execute("SELECT name FROM sqlite_master WHERE name='approval_publication'").fetchone())
            self.assertEqual(conn.execute('SELECT count(*) FROM agent').fetchone()[0],1)


if __name__=='__main__':unittest.main()
