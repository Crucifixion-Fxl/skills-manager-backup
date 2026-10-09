"""Read-only source revalidation for immutable own-home channel admissions.

This module reads the original pinned approval objects and independently checks
the complete current signed/native authority set. It never mutates the journal,
activates a grant, or claims process/physical readiness.
"""
from __future__ import annotations

from asyncio import CancelledError
from dataclasses import dataclass, field
import hashlib
import json
import re
import time
from pathlib import Path

import buzz_agent_join_requests as join
import buzz_feishu_group_sync as gs
import recovery_authority as authority
from recovery_origin import canonical_origin

from . import agent_catalog, remote_approval
from .agent_signed_reads import OwnAgentReader
from .async_io import thread_call
from .bot_clients import BotLarkCli
from .remote_proofs import RemoteProofs
from .remote_target import RemoteTarget
from .safety import read_owned
from .store import OwnHomeAdmissionChannel, OwnHomeAdmissionRecord, Store


PROOF_SECONDS = 30
NOTICE = (
    '本机 own-home 来源证明尚未完整核验，保留待处理。怎么解决：检查完整的原始签名来源、当前 own bot 权限、'
    '完整频道集合、受保护配置与有效认领；不要重写 admission 或恢复发送。'
    '\n复制给 AI：帮我只读核查 hostd own-home 当前来源；不要输出凭据、签名正文、人员资料或本机路径。'
)
_HEX = re.compile(r'[0-9a-f]{64}')
_CHANNEL = re.compile(r'[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}')
_ORIGINAL_FIELDS = ('approval_id', 'policy_event_id', 'claim_event_id', 'roster_event_id')


class _Pending(ValueError):
    pass


@dataclass(frozen=True)
class CurrentSourcesReceipt:
    admission_id: str
    snapshot_hash: str
    proposed_channels_hash: str
    agent_pubkey: str
    owner_pubkey: str
    app_id: str
    channel_ids: tuple[str, ...]
    chat_refs: tuple[str, ...]
    approval_ids: tuple[str, ...]
    policy_ids: tuple[str, ...]
    claim_ids: tuple[str, ...]
    roster_ids: tuple[str, ...]
    current_policy_ids: tuple[str, ...]
    current_profile_ids: tuple[str, ...]
    current_roster_ids: tuple[str, ...]
    current_claim_ids: tuple[str, ...]
    heartbeats: tuple[int, ...]
    catalog_hash: str
    env_hash: str
    profile_hash: str
    legacy_join_hash: str
    checked_at: int
    expires_at: int


@dataclass(frozen=True)
class CurrentSourcesResult:
    status: str
    receipt: CurrentSourcesReceipt | None = field(default=None, repr=False)
    reason: str = ''
    notice: str = ''

    def readback(self):
        return {
            'status': self.status,
            'reason': self.reason,
            'notice': self.notice,
            'admission_id': self.receipt.admission_id if self.receipt else '',
            'checked_at': self.receipt.checked_at if self.receipt else 0,
            'expires_at': self.receipt.expires_at if self.receipt else 0,
            'live_verified': False,
            'grant_activated': False,
            'sending_ready': False,
            'physical_verified': False,
        }


def _need(condition):
    if not condition:
        raise _Pending()


def _digest(value):
    return hashlib.sha256(value).hexdigest()


def _remote_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _sql_snapshot(store: Store, admission_id: str):
    """Read exact journal rows and current SQL agent in one short loop-owned tx."""
    with store.transaction():
        record = store.own_home_admission(admission_id)
        if type(record) is not OwnHomeAdmissionRecord:
            raise _Pending()
        channels = store.own_home_admission_channels(admission_id)
        active = store.active_own_home_admission(record.agent_id)
        agent = store.conn.execute('SELECT * FROM agent WHERE pubkey=?', (record.agent_id,)).fetchone()
        if (active != record or type(channels) is not tuple or not 1 <= len(channels) <= 256
                or not agent or agent['status'] != 'active'):
            raise _Pending()
        if record.state not in ('reserved', 'unknown'):
            raise _Pending()
        if any(type(row) is not OwnHomeAdmissionChannel or row.admission_id != admission_id
               or row.ordinal != ordinal for ordinal, row in enumerate(channels)):
            raise _Pending()
        return record, channels, tuple(agent)


def _protected_files(catalog_path: Path, legacy_join_path: Path, agent_id: str):
    """Joined protected reads; deliberately called outside every SQL transaction."""
    first = agent_catalog.load(catalog_path, legacy_join_path=legacy_join_path)
    matches = [row for row in first.records if row.pubkey == agent_id]
    if len(matches) != 1 or matches[0].status != 'own_bot_verified':
        raise _Pending()
    env_file = matches[0].env_file
    env_first = read_owned(env_file, max_bytes=65536)
    second = agent_catalog.load(catalog_path, legacy_join_path=legacy_join_path)
    env_second = read_owned(env_file, max_bytes=65536)
    _need(first == second and env_first == env_second)
    return second, env_second


def _validate_admission(record, channels, agent_row, catalog, env_raw):
    _need(type(record) is OwnHomeAdmissionRecord and type(channels) is tuple
          and type(catalog) is agent_catalog.Catalog)
    _need(_HEX.fullmatch(record.admission_id) and _HEX.fullmatch(record.agent_id))
    _need(record.state in ('reserved', 'unknown') and record.receipt_hash == '')
    _need(record.prior_channels_hash == _remote_digest([]))
    _need(record.proposed_channels_hash == _digest(
        json.dumps(sorted(row.channel_id for row in channels), separators=(',', ':')).encode()))
    channel_ids = tuple(row.channel_id for row in channels)
    _need(len(channel_ids) == len(set(channel_ids)) and channel_ids == tuple(sorted(channel_ids)))
    _need(all(_CHANNEL.fullmatch(value) for value in channel_ids))
    _need(all(row.source_kind == 'own_approval' for row in channels))
    _need(record.approval_set_hash == _remote_digest([row.approval_id for row in channels]))
    original_rows = [{name: getattr(row, name) for name in (
        'channel_id', 'chat_id', 'chat_ref', 'source_kind', 'approval_id', 'approval_hash',
        'mirror_pubkey', 'mirror_owner_pubkey', 'claimed_at', 'claim_event_id',
        'policy_event_id', 'roster_event_id', 'authorization_hash')}
        for row in channels]
    _need(record.snapshot_hash == _remote_digest(original_rows))
    expected_agent = (record.agent_id,)
    _need(tuple(agent_row)[0:1] == expected_agent and agent_row[4] == 'active')
    catalog_rows = [row for row in catalog.records if row.pubkey == record.agent_id]
    _need(len(catalog_rows) == 1)
    selected = catalog_rows[0]
    _need(selected.status == 'own_bot_verified' and selected.local_bot_verified is True
          and selected.pubkey == record.agent_id and selected.owner_pubkey == agent_row[1]
          and selected.app_id == agent_row[2] and str(selected.env_file) == agent_row[3])
    _need((catalog.catalog_sha256, catalog.legacy_join_sha256, selected.profile_sha256,
           _digest(env_raw)) == (record.catalog_hash, record.legacy_join_hash,
                                  record.profile_hash, record.env_after_hash))
    env = join.parse_env(env_raw.decode('utf-8'))
    _need(tuple(selected.channels) == channel_ids
          and selected.channels == tuple(join._allowlist(env.get('BUZZ_ACP_CHANNELS', '')))
          and selected.channels == tuple(sorted(set(selected.channels))))
    _need(selected.app_id and selected.lark_config_dir and selected.lark_data_dir
          and selected.prompt_file and selected.responsible_config)
    _need(all(_HEX.fullmatch(getattr(row, name)) for row in channels for name in (
        'approval_id', 'approval_hash', 'mirror_pubkey', 'mirror_owner_pubkey',
        'claim_event_id', 'policy_event_id', 'roster_event_id', 'authorization_hash')))
    _need(all(gs.CHAT_ID_RE.fullmatch(row.chat_id)
              and row.chat_ref == gs.chat_ref(row.chat_id)
              and type(row.claimed_at) is int and row.claimed_at >= 0 for row in channels))
    for row in channels:
        _need(row.mirror_pubkey != row.mirror_owner_pubkey)
    return selected, env


def _validate_reader(reader, record, env):
    _need(isinstance(reader, OwnAgentReader)
          and (reader.agent, reader.owner) == (record.pubkey, record.owner_pubkey)
          and Path(reader.env_file) == record.env_file)
    origin = canonical_origin(env.get('BUZZ_RELAY_URL', ''))
    _need(origin == reader.origin and reader.origin.startswith('https://')
          and reader.origin in {canonical_origin(value) for value in reader.trusted_relays}
          and _HEX.fullmatch(reader.pin))


async def _original_events(reader, channels):
    expected = {}
    for row in channels:
        for name in _ORIGINAL_FIELDS:
            event_id = getattr(row, name)
            old = expected.get(event_id)
            if old is not None:
                _need(old == name)
            expected[event_id] = name
    ids = tuple(sorted(expected))
    _need(0 < len(ids) <= 1024)
    output = {}
    for offset in range(0, len(ids), 128):
        batch = ids[offset:offset + 128]
        rows = await reader.query([{'ids': list(batch), 'limit': 257}])
        _need(type(rows) is list and len(rows) == len(batch)
              and all(type(event) is dict and type(event.get('id')) is str for event in rows))
        _need(len({event['id'] for event in rows}) == len(rows)
              and {event['id'] for event in rows} == set(batch))
        output.update((event['id'], event) for event in rows)
    _need(set(output) == set(ids))
    return output


def _validate_originals(events, rows, agent, reader, now):
    for row in rows:
        approval = events[row.approval_id]
        decoded = remote_approval.decode(approval, now=now)
        expected = remote_approval.ApprovalScope(agent.pubkey, agent.owner_pubkey,
            agent.app_id, row.channel_id, row.chat_ref,
            row.mirror_pubkey, row.mirror_owner_pubkey, row.claimed_at)
        _need(decoded.scope == expected
              and approval['pubkey'] == row.mirror_pubkey and decoded.content_hash == row.approval_hash)

        policy = events[row.policy_event_id]
        policy_body = remote_approval._policy(policy, agent.pubkey, now)
        policy_feishu = policy_body.get('feishu')
        _need(policy['id'] == row.policy_event_id and policy['pubkey'] == agent.owner_pubkey
              and type(policy_feishu) is dict and policy_feishu.get('app_id') == agent.app_id
              and policy_feishu.get('mirror') is not True)

        claim = events[row.claim_event_id]
        claim_body = remote_approval._policy(claim, row.mirror_pubkey, now)
        _need(claim['id'] == row.claim_event_id and claim['pubkey'] == row.mirror_owner_pubkey)
        feishu = claim_body.get('feishu')
        _need(type(feishu) is dict and feishu.get('mirror') is True
              and type(feishu.get('bindings')) is list)
        bindings = feishu['bindings']
        claims = gs._claims_in(row.mirror_pubkey, row.mirror_owner_pubkey, '', claim['content'])
        _need(len(claims) == len(bindings)
              and len({(item.channel, item.chat_ref) for item in claims}) == len(claims))
        matching = [item for item in claims if (item.channel, item.chat_ref, item.claimed_at) ==
                    (row.channel_id, row.chat_ref, row.claimed_at)]
        _need(len(matching) == 1)

        roster = events[row.roster_event_id]
        remote_approval._event(roster, 39002, now)
        _need(roster['id'] == row.roster_event_id and roster['pubkey'] == reader.pin)
        roles = authority.membership(roster, row.channel_id)
        _need(roles.get(agent.pubkey) == 'bot' and roles.get(row.mirror_pubkey) == 'bot'
              and roles.get(row.mirror_owner_pubkey) in ('owner', 'admin'))


def _matches_evidence(result, row, admission, agent, reader, catalog):
    _need(result.status == 'verified' and result.authorization is not None)
    authorization = result.authorization
    evidence = authorization.evidence
    _need((evidence.agent_id, evidence.owner_pubkey, evidence.app_id, evidence.channel_id,
           evidence.chat_ref, evidence.mirror_pubkey, evidence.mirror_owner_pubkey,
           evidence.approval_id, evidence.approval_hash, evidence.claimed_at,
           evidence.agent_policy_event_id, evidence.relay_origin) ==
          (admission.agent_id, agent.owner_pubkey, agent.app_id, row.channel_id,
           row.chat_ref, row.mirror_pubkey, row.mirror_owner_pubkey,
           row.approval_id, row.approval_hash, row.claimed_at,
           row.policy_event_id, reader.origin))
    _need(evidence.capabilities == ('message',)
          and evidence.allowlist_hash == Store._remote_digest(sorted(agent.channels))
          and authorization.catalog_sha256 == catalog.catalog_sha256
          and authorization.legacy_join_sha256 == catalog.legacy_join_sha256)
    return evidence


def _validate_current_heartbeat(rows, evidence):
    """Pure signature and claim validation for one exact current event."""
    _need(type(rows) is list and len(rows) == 1 and type(rows[0]) is dict
          and rows[0].get('id') == evidence.claim_event_id)
    event = rows[0]
    body = remote_approval._policy(event, evidence.mirror_pubkey, evidence.checked_at)
    _need(event['pubkey'] == evidence.mirror_owner_pubkey)
    feishu = body.get('feishu')
    _need(type(feishu) is dict and feishu.get('mirror') is True
          and type(feishu.get('bindings')) is list)
    bindings = feishu['bindings']
    claims = gs._claims_in(evidence.mirror_pubkey, evidence.mirror_owner_pubkey, '', event['content'])
    _need(len(claims) == len(bindings)
          and len({(item.channel, item.chat_ref) for item in claims}) == len(claims))
    matching = [item for item in claims if
        (item.channel, item.chat_ref, item.claimed_at) ==
        (evidence.channel_id, evidence.chat_ref, evidence.claimed_at)]
    _need(len(matching) == 1 and gs.claim_valid(matching[0], evidence.checked_at))
    return matching[0].heartbeat


async def _current_heartbeat(reader, evidence):
    """Read over the native boundary, then join pure validation off-loop."""
    rows = await reader.query([{'ids': [evidence.claim_event_id], 'limit': 257}])
    return await thread_call(_validate_current_heartbeat, rows, evidence)


class OwnHomeCurrentSources:
    def __init__(self, store: Store, catalog_path, legacy_join_path, *, reader, bot,
                 clock=lambda: int(time.time())):
        self.store = store
        self.catalog_path = Path(catalog_path)
        self.legacy_join_path = Path(legacy_join_path)
        self.reader = reader
        self.bot = bot
        self.clock = clock

    async def _pass(self, record, channels, agent, catalog, clock):
        proofs = RemoteProofs(self.catalog_path, self.legacy_join_path, record=agent,
            reader=self.reader, bot=self.bot, clock=clock)
        evidence = []
        for row in channels:
            target = RemoteTarget(record.agent_id, agent.owner_pubkey, agent.app_id,
                row.channel_id, row.chat_id, row.chat_ref, self.reader.origin,
                row.mirror_pubkey, row.mirror_owner_pubkey, row.claim_event_id,
                row.claimed_at, row.claimed_at, clock())
            result = await proofs.verify(target, capabilities=('message',))
            current = _matches_evidence(result, row, record, agent, self.reader, catalog)
            heartbeat = await _current_heartbeat(self.reader, current)
            evidence.append((current, heartbeat))
        return tuple(evidence)

    async def verify(self, admission_id):
        try:
            _need(type(admission_id) is str and _HEX.fullmatch(admission_id))
            started = self.clock()
            _need(type(started) is int and started >= 0)
            clock_state = {'last': started, 'invalid': False}

            def guarded_clock():
                try:
                    value = self.clock()
                except Exception:
                    clock_state['invalid'] = True
                    raise
                if type(value) is not int:
                    clock_state['invalid'] = True
                else:
                    if (value < clock_state['last'] or value < started
                            or value > started + PROOF_SECONDS):
                        clock_state['invalid'] = True
                    clock_state['last'] = value
                return value

            record, channels, agent_row = _sql_snapshot(self.store, admission_id)
            catalog, env_raw = await thread_call(_protected_files,
                self.catalog_path, self.legacy_join_path, record.agent_id)
            agent, env = _validate_admission(record, channels, agent_row, catalog, env_raw)
            _need(isinstance(self.bot, BotLarkCli))
            _validate_reader(self.reader, agent, env)

            # A first complete current-proof pass precedes exact original
            # pin reads; the second pass catches revocation during any IO.
            first = await self._pass(record, channels, agent, catalog, guarded_clock)
            _need(not clock_state['invalid'])
            originals = await _original_events(self.reader, channels)
            now = guarded_clock()
            _need(not clock_state['invalid'] and type(now) is int
                  and started <= now <= started + PROOF_SECONDS
                  and now >= max(item.checked_at for item, _ in first))
            await thread_call(_validate_originals, originals, channels, agent, self.reader, now)
            # The subject policy is exact and immutable for this contract;
            # evidence from each current proof must retain the original ID.
            _need(all(item.agent_policy_event_id == row.policy_event_id
                      for (item, _), row in zip(first, channels)))

            second = await self._pass(record, channels, agent, catalog, guarded_clock)
            _need(not clock_state['invalid'])
            _need(len(first) == len(second) == len(channels))
            for (before, _), (after, heartbeat), row in zip(first, second, channels):
                _need((before.approval_id, before.approval_hash, before.agent_policy_event_id,
                       before.claimed_at, before.chat_ref, before.channel_id) ==
                      (after.approval_id, after.approval_hash, after.agent_policy_event_id,
                       after.claimed_at, after.chat_ref, after.channel_id))
                _need(after.agent_policy_event_id == row.policy_event_id)
                _need(type(heartbeat) is int and row.claimed_at <= heartbeat
                      and heartbeat <= after.checked_at + gs.RELAY_CLOCK_SKEW_SECONDS)

            # The original row IDs are immutable historical pins, even when
            # current proofs have advanced to a newer valid claim or roster.
            # Repeat the complete bounded GET only after all second-pass native
            # reads, and validate those exact original signed objects again.
            originals_after = await _original_events(self.reader, channels)
            _need(set(originals_after) == set(originals))
            originals_after_now = guarded_clock()
            second_evidence = tuple(item for item, _ in second)
            _need(not clock_state['invalid'] and type(originals_after_now) is int
                  and started <= originals_after_now <= started + PROOF_SECONDS
                  and originals_after_now >= max(item.checked_at for item in second_evidence))
            await thread_call(_validate_originals, originals_after, channels, agent, self.reader,
                              originals_after_now)
            _need(not clock_state['invalid'])

            # Finish joined protected file reads before SQL. No filesystem,
            # network, process, or awaited work occurs inside this short tx.
            final_catalog, final_env = await thread_call(_protected_files,
                self.catalog_path, self.legacy_join_path, record.agent_id)
            final_agent, _ = _validate_admission(record, channels, agent_row,
                final_catalog, final_env)
            _need(final_agent == agent and final_catalog == catalog and final_env == env_raw)
            checked = guarded_clock()
            all_evidence = tuple(item for item, _ in (*first, *second))
            _need(not clock_state['invalid'] and type(checked) is int
                  and started <= checked <= started + PROOF_SECONDS
                  and checked >= max(item.checked_at for item in all_evidence)
                  and all(type(item.checked_at) is int and type(item.valid_until) is int
                          and started <= item.checked_at <= checked
                          and checked < item.valid_until for item in all_evidence))
            expires = min(checked + PROOF_SECONDS,
                min(item.valid_until for item in all_evidence))
            _need(checked < expires)
            with self.store.transaction():
                current = self.store.own_home_admission(admission_id)
                current_rows = self.store.own_home_admission_channels(admission_id)
                active = self.store.active_own_home_admission(record.agent_id)
                current_agent = self.store.conn.execute(
                    'SELECT * FROM agent WHERE pubkey=?', (record.agent_id,)).fetchone()
                _need(current == record and current_rows == channels and active == record
                      and current_agent is not None and tuple(current_agent) == agent_row
                      and current.state in ('reserved', 'unknown'))

            receipt = CurrentSourcesReceipt(
                record.admission_id, record.snapshot_hash, record.proposed_channels_hash,
                record.agent_id, agent.owner_pubkey, agent.app_id,
                tuple(row.channel_id for row in channels),
                tuple(row.chat_ref for row in channels),
                tuple(row.approval_id for row in channels),
                tuple(row.policy_event_id for row in channels),
                tuple(row.claim_event_id for row in channels),
                tuple(row.roster_event_id for row in channels),
                tuple(item.agent_policy_event_id for item, _ in second),
                tuple(item.agent_profile_event_id for item, _ in second),
                tuple(item.roster_event_id for item, _ in second),
                tuple(item.claim_event_id for item, _ in second),
                tuple(heartbeat for _, heartbeat in second),
                final_catalog.catalog_sha256, _digest(final_env),
                agent.profile_sha256, final_catalog.legacy_join_sha256,
                checked, expires)
            return CurrentSourcesResult('verified', receipt)
        except CancelledError:
            raise
        except Exception:
            return CurrentSourcesResult('pending', reason='source_unverified', notice=NOTICE)
