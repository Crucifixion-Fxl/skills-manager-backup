"""Bounded readonly discovery of a zero-channel own agent's approved chat.

This produces metadata for a later, separate admission review. It does not
change the protected catalog, grant a channel, authorize delivery, or create a
Store record. Existing RemoteProofs remains the allowlist gate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from pathlib import Path
import time

import buzz_feishu_group_sync as gs
import recovery_authority as authority
from . import agent_catalog, remote_approval
from .agent_signed_reads import OwnAgentReader
from recovery_origin import canonical_origin
from .async_io import thread_call
from .native_scopes import parse_bot_scope_envelope
from .bot_clients import BotLarkCli, READ_SCOPE_GROUPS

NOTICE = (
    '自己的 bot 批准与群成员读取尚未完整核验，保留待处理。'
    '怎么解决：检查受保护的本机目录、完整签名记录、当前认领、应用读取权限和原生群成员。'
    '\n复制给 AI：帮我核验自己的 bot 群发现结果；不要输出密钥、应用 secret、群成员或提示词正文。'
)
MAX_EVENTS = 255
MAX_CHANNELS = 32
MAX_NATIVE_PAGES = 10
PROOF_SECONDS = 30


def _need(value):
    if not value:
        raise ValueError


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _int(value) -> bool:
    return type(value) is int and 0 <= value <= remote_approval.MAX_TIME


def _hex(value) -> bool:
    return isinstance(value, str) and gs.HEX64_RE.fullmatch(value) is not None


def _chat_id(value) -> bool:
    return isinstance(value, str) and gs.CHAT_ID_RE.fullmatch(value) is not None


@dataclass(frozen=True)
class OwnAdmissionCandidate:
    agent_pubkey: str
    owner_pubkey: str
    app_id: str
    channel_id: str
    chat_id: str = field(repr=False)
    chat_ref: str = ''
    relay_url: str = ''
    mirror_pubkey: str = ''
    mirror_owner_pubkey: str = ''
    claimed_at: int = 0
    heartbeat: int = 0
    claim_event_id: str = ''
    agent_profile_id: str = ''
    agent_policy_id: str = ''
    mirror_profile_id: str = ''
    mirror_policy_id: str = ''
    roster_event_id: str = ''
    approval_id: str = ''
    approval_hash: str = ''
    catalog_sha256: str = ''
    legacy_join_sha256: str = ''
    env_sha256: str = ''
    profile_sha256: str = ''
    checked_at: int = 0
    expires_at: int = 0


@dataclass(frozen=True)
class OwnAdmissionResult:
    status: str
    candidates: tuple[OwnAdmissionCandidate, ...] = field(default_factory=tuple, repr=False)
    reason: str = ''
    notice: str = ''

    def readback(self):
        return {
            'status': self.status,
            'reason': self.reason,
            'notice': self.notice,
            'candidate_count': len(self.candidates),
            'approval_ids': tuple(candidate.approval_id for candidate in self.candidates),
            'checked_at': tuple(candidate.checked_at for candidate in self.candidates),
            'expires_at': tuple(candidate.expires_at for candidate in self.candidates),
            'live_verified': False,
            'grant_activated': False,
            'sending_ready': False,
        }


@dataclass(frozen=True)
class _Verified:
    event: dict = field(repr=False)
    approval: remote_approval.ApprovalProof
    context: remote_approval.ProofContext = field(repr=False)
    claim: object = field(repr=False)
    policy_id: str
    mirror_profile: dict = field(repr=False)
    roster: dict = field(repr=False)

    @property
    def stable_scope(self):
        s = self.approval.scope
        return (s.agent_pubkey, s.agent_owner_pubkey, s.app_id, s.channel_id,
                s.chat_ref, s.mirror_pubkey, s.mirror_owner_pubkey, s.claimed_at)

    @property
    def approval_identity(self):
        return (self.approval.record_id, self.approval.content_hash, self.stable_scope)


class OwnAdmissionDiscovery:
    """Verify current signed approval and actual own-bot readonly membership."""

    def __init__(self, catalog_path, legacy_join_path, *, record, reader, bot,
                 clock=lambda: int(time.time())):
        self.catalog_path = Path(catalog_path)
        self.legacy_join_path = Path(legacy_join_path)
        self.record, self.reader, self.bot, self.clock = record, reader, bot, clock

    def _identity(self):
        record, reader, bot = self.record, self.reader, self.bot
        _need(type(record) is agent_catalog.AgentRecord)
        _need(type(reader) is OwnAgentReader and type(bot) is BotLarkCli)
        _need(record.status == 'own_bot_verified' and record.local_bot_verified is True)
        _need(type(record.channels) is tuple and record.channels == ())
        _need(record.pubkey and record.owner_pubkey and record.app_id)
        _need(reader.agent == record.pubkey and reader.owner == record.owner_pubkey)
        _need(Path(reader.env_file) == record.env_file)
        _need(reader.origin.startswith('https://') and reader.origin in
              {canonical_origin(x) for x in reader.trusted_relays})
        _need(_hex(reader.pin))
        _need((bot.app_id, bot.config_dir, bot.data_dir, bot.chat_id) ==
              (record.app_id, record.lark_config_dir, record.lark_data_dir, None))
        _need(Path(self.catalog_path).is_absolute() and Path(self.legacy_join_path).is_absolute())

    def _catalog(self, original=None):
        self._identity()
        catalog = agent_catalog.load(self.catalog_path, legacy_join_path=self.legacy_join_path)
        matches = [row for row in catalog.records if row.name == self.record.name]
        _need(len(matches) == 1 and matches[0] == self.record)
        _need(matches[0].status == 'own_bot_verified' and matches[0].local_bot_verified is True
              and matches[0].channels == ())
        if original is not None:
            _need((catalog.catalog_sha256, catalog.legacy_join_sha256) ==
                  (original.catalog_sha256, original.legacy_join_sha256))
        _need(self.bot.identity() == (self.record.app_id, ''))
        again = agent_catalog.load(self.catalog_path, legacy_join_path=self.legacy_join_path)
        _need(again == catalog)
        self._identity()
        return catalog

    def _now(self):
        value = self.clock()
        _need(_int(value))
        return value

    async def _query(self, filters):
        # The signed-read protocol accepts #h/#d, but not #p. Keep reads complete
        # and let the strict approval parser filter the returned signed events.
        rows = await self.reader.query([dict(filters, limit=257)])
        _need(type(rows) is list and len(rows) < 256)
        ids = []
        for row in rows:
            _need(type(row) is dict and _hex(row.get('id')))
            ids.append(row['id'])
        _need(len(ids) == len(set(ids)))
        return rows

    def _policies(self, rows):
        decoded = []
        mirror_keys = set()
        agent_policies = []
        for event in rows:
            tags = authority.tags(event, 'd')
            _need(len(tags) == 1 and len(tags[0]) == 2 and _hex(tags[0][1]))
            subject = tags[0][1]
            body = authority.policy_body(event, subject)
            decoded.append((subject, event, body))
            mirror_keys.add(subject)
            if event['pubkey'] == self.record.owner_pubkey and subject == self.record.pubkey:
                agent_policies.append(event)
        _need(bool(agent_policies))
        return decoded, tuple(sorted(mirror_keys)), authority.latest(agent_policies)

    async def _signed_snapshot(self):
        now = self._now()
        # Query sequentially so cancellation cannot outlive another dispatched
        # own-key request; each reader call joins its actual IO before returning.
        approvals_raw = await self._query({'kinds': [30078]})
        own_profiles = await self._query({'kinds': [0], 'authors': [self.record.pubkey]})
        policy_rows = await self._query({'kinds': [30177]})
        _need(bool(own_profiles) and bool(policy_rows))
        policies, policy_subjects, own_policy = self._policies(policy_rows)
        own_profile = authority.latest(own_profiles)
        _need(own_profile is not None and own_policy is not None)

        decoded = []
        for event in approvals_raw:
            # A malformed or forged signed row invalidates the complete packet.
            approval = remote_approval.decode(event, now=now)
            scope = approval.scope
            if scope.agent_pubkey != self.record.pubkey:
                continue
            _need(scope.agent_owner_pubkey == self.record.owner_pubkey
                  and scope.app_id == self.record.app_id)
            decoded.append((event, approval))
        _need(bool(decoded))
        channels = {approval.scope.channel_id for _, approval in decoded}
        _need(len(channels) <= MAX_CHANNELS)

        # Every policy subject may be a current or historical mirror. Read all
        # of their current signed owner profiles so a rival winner cannot hide.
        mirror_keys = set(policy_subjects)
        mirror_keys.update(approval.scope.mirror_pubkey for _, approval in decoded)
        _need(mirror_keys and len(mirror_keys) <= MAX_EVENTS)
        mirror_profiles = await self._query({'kinds': [0], 'authors': sorted(mirror_keys)})
        _need(bool(mirror_profiles))

        rosters = {}
        for channel in sorted(channels):
            rows = await self._query({'kinds': [39002], 'authors': [self.reader.pin], '#d': [channel]})
            rosters[channel] = authority.latest(rows)
            _need(rosters[channel] is not None)

        verified = []
        for event, approval in decoded:
            scope = approval.scope
            profiles = tuple(row for row in mirror_profiles if row.get('pubkey') in mirror_keys)
            context = remote_approval.ProofContext(
                scope, self.reader.pin, own_profile, own_policy, profiles,
                tuple(policy_rows), rosters[scope.channel_id], now, complete=True)
            # A matching own-agent approval which fails current authority makes
            # the full pass pending; it is never filtered into success.
            proof = remote_approval.verify(event, context)
            claim, _, policy_id = remote_approval._current(context)
            mirror_profile = authority.latest([row for row in profiles
                                               if row.get('pubkey') == scope.mirror_pubkey])
            _need(mirror_profile is not None)
            verified.append(_Verified(event, proof, context, claim, policy_id,
                                      mirror_profile, context.roster))
        _need(bool(verified))
        # Any two distinct valid approvals for one own channel are ambiguous.
        per_channel = {}
        for row in verified:
            per_channel.setdefault(row.approval.scope.channel_id, []).append(row)
        _need(all(len(rows) == 1 for rows in per_channel.values()))
        return tuple(sorted(verified, key=lambda row: row.approval.scope.channel_id))

    def _scope_read(self):
        self._identity()
        payload = self.bot.call('own admission scopes',
            ['api', 'GET', '/open-apis/application/v6/scopes', '--as', 'bot'], full=True)
        granted = parse_bot_scope_envelope(payload)
        _need(all(alternatives & granted for alternatives in READ_SCOPE_GROUPS.values()))
        self._identity()

    def _chat_listing(self):
        self._identity()
        payload = self.bot.call('own admission chats',
            ['im', 'chats', 'list', '--page-all', '--page-limit', str(MAX_NATIVE_PAGES)], full=True)
        _need(type(payload) is dict and payload.get('ok') is True and payload.get('identity') == 'bot')
        pagination = payload.get('meta', {}).get('pagination') if type(payload.get('meta')) is dict else None
        data = payload.get('data')
        _need(type(pagination) is dict and pagination.get('complete') is True)
        _need(type(data) is dict and data.get('has_more') is False and data.get('page_token') == '')
        rows = data.get('items')
        _need(type(rows) is list and len(rows) < 256)
        ids, refs = set(), {}
        for row in rows:
            _need(type(row) is dict and _chat_id(row.get('chat_id')))
            chat_id = row['chat_id']
            _need(chat_id not in ids)
            ids.add(chat_id)
            ref = gs.chat_ref(chat_id)
            refs.setdefault(ref, []).append(chat_id)
        self._identity()
        return refs

    def _member_listing(self, chat_id):
        self._identity()
        _need(_chat_id(chat_id))
        payload = self.bot.call('own admission members',
            ['im', '+chat-members-list', '--chat-id', chat_id, '--member-id-type', 'open_id',
             '--member-types', 'user,bot', '--page-all', '--page-limit', str(MAX_NATIVE_PAGES)], full=True)
        _need(type(payload) is dict and payload.get('ok') is True and payload.get('identity') == 'bot')
        pagination = payload.get('meta', {}).get('pagination') if type(payload.get('meta')) is dict else None
        data = payload.get('data')
        _need(type(data) is dict and type(pagination) is dict and pagination.get('complete') is True)
        _need(BotLarkCli._listing_complete(data, ('user', 'bot')))
        users, bots = data.get('users'), data.get('bots')
        _need(type(users) is list and type(bots) is list and len(users) <= 4096 and len(bots) <= 4096)
        user_ids, bot_ids, app_ids = set(), set(), set()
        for row in users:
            _need(type(row) is dict and isinstance(row.get('member_id'), str)
                  and gs.OPEN_ID_RE.fullmatch(row['member_id']))
            _need(row['member_id'] not in user_ids)
            user_ids.add(row['member_id'])
        for row in bots:
            _need(type(row) is dict and isinstance(row.get('member_id'), str)
                  and gs.OPEN_ID_RE.fullmatch(row['member_id'])
                  and isinstance(row.get('app_id'), str) and gs.APP_ID_RE.fullmatch(row['app_id']))
            _need(row['member_id'] not in bot_ids and row['app_id'] not in app_ids)
            bot_ids.add(row['member_id']); app_ids.add(row['app_id'])
        _need(data.get('user_total') == len(users) and data.get('bot_total') == len(bots))
        _need(self.record.app_id in app_ids)
        self._identity()

    async def _native_read(self, verified):
        await thread_call(self._scope_read)
        refs = await thread_call(self._chat_listing)
        chats = {}
        for row in verified:
            ref = row.approval.scope.chat_ref
            matches = refs.get(ref, ())
            _need(len(matches) == 1)
            chats[row.approval.record_id] = matches[0]
        seen_chats = set()
        for chat_id in chats.values():
            if chat_id not in seen_chats:
                await thread_call(self._member_listing, chat_id)
                seen_chats.add(chat_id)
        return chats

    def _candidate(self, row, chat_id, catalog, checked_at, expires_at):
        scope = row.approval.scope
        claim = row.claim
        _need(claim.channel == scope.channel_id and claim.chat_ref == scope.chat_ref
              and claim.mirror == scope.mirror_pubkey and claim.owner == scope.mirror_owner_pubkey
              and claim.claimed_at == scope.claimed_at)
        return OwnAdmissionCandidate(
            agent_pubkey=scope.agent_pubkey,
            owner_pubkey=scope.agent_owner_pubkey,
            app_id=scope.app_id,
            channel_id=scope.channel_id,
            chat_id=chat_id,
            chat_ref=scope.chat_ref,
            relay_url=self.reader.origin,
            mirror_pubkey=scope.mirror_pubkey,
            mirror_owner_pubkey=scope.mirror_owner_pubkey,
            claimed_at=claim.claimed_at,
            heartbeat=claim.heartbeat,
            claim_event_id=row.policy_id,
            agent_profile_id=row.context.agent_profile['id'],
            agent_policy_id=row.context.agent_policy['id'],
            mirror_profile_id=row.mirror_profile['id'],
            mirror_policy_id=row.policy_id,
            roster_event_id=row.roster['id'],
            approval_id=row.approval.record_id,
            approval_hash=row.approval.content_hash,
            catalog_sha256=catalog.catalog_sha256,
            legacy_join_sha256=catalog.legacy_join_sha256,
            env_sha256=self.record.env_sha256 or '',
            profile_sha256=self.record.profile_sha256 or '',
            checked_at=checked_at,
            expires_at=expires_at,
        )

    async def discover(self):
        try:
            self._identity()
            started = self._now()
            original = await thread_call(self._catalog)
            first = await self._signed_snapshot()
            _need(self._now() - started <= PROOF_SECONDS)
            chats = await self._native_read(first)
            current = await self._signed_snapshot()
            _need(len(first) == len(current))
            snapshot_now = self._now()
            _need(snapshot_now >= current[0].context.now and snapshot_now - started <= PROOF_SECONDS)
            _need(tuple(row.approval_identity for row in first) ==
                  tuple(row.approval_identity for row in current))
            for before, after in zip(first, current):
                _need(before.stable_scope == after.stable_scope)
                _need(after.claim.heartbeat >= before.claim.heartbeat)
                _need(after.claim.claimed_at == before.claim.claimed_at)
            catalog = await thread_call(self._catalog, original)
            self._identity()
            # Time validity includes the final protected catalog read and identity
            # check, not only completion of signed/native network evidence.
            checked_at = self._now()
            _need(checked_at >= started and checked_at >= current[0].context.now
                  and checked_at - started <= PROOF_SECONDS)
            valid = []
            for row in current:
                _need(row.approval.record_id in chats)
                expiry = min(started + PROOF_SECONDS,
                             row.claim.heartbeat + gs.CLAIM_LEASE_SECONDS + 1)
                _need(checked_at < expiry)
                valid.append(self._candidate(row, chats[row.approval.record_id], catalog, checked_at, expiry))
            _need(bool(valid))
            return OwnAdmissionResult('discovered', tuple(valid), '', '')
        except Exception:
            return OwnAdmissionResult('pending', (), 'evidence_unverified', NOTICE)
