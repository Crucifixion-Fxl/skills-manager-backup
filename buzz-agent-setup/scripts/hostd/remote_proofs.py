"""Read-only remote card approval consumption through the agent's own identity.

Discovery is a lookup constraint, never authorization. Complete signed reads,
protected local scope and actual own-bot permission/membership reads establish
metadata for root's trusted runtime. No SQL grant, foreign writer, credential,
allowlist edit or public approval producer is created here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import time

import buzz_feishu_group_sync as gs
import recovery_authority as authority
from . import agent_catalog, remote_approval
from .agent_signed_reads import OwnAgentReader
from .async_io import thread_call
from .native_scopes import parse_bot_scope_envelope
from .bot_clients import BotLarkCli, READ_SCOPE_GROUPS
from .remote_target import RemoteTarget
from .store import RemoteGrantEvidence, Store

NOTICE = ('远端批准与自己的 bot 读取权限尚未核验，保留待处理。怎么解决：检查受保护的本机目录和旧任务排除证据、自己的应用权限、完整成员与签名目录、有效认领以及实际卡片批准记录。'
          '\n复制给 AI：帮我核查 hostd 远端批准消费证明；不要猜同步应用、借用其他机器凭据或把目标发现当作批准，不要输出凭据、正文或人员资料。')
PROOF_SECONDS = 30
CAPABILITIES = ('message', 'edit', 'reaction_add', 'reaction_remove')
WRITE_SCOPE_GROUPS = {
    'message': frozenset({'im:message', 'im:message:send_as_bot', 'im:message:send'}),
    'edit': frozenset({'im:message:update', 'im:message', 'im:message:send_as_bot'}),
    'reaction_add': frozenset({'im:message', 'im:message.reactions:write_only'}),
    'reaction_remove': frozenset({'im:message', 'im:message.reactions:write_only'}),
}


class _Pending(ValueError):
    pass


@dataclass(frozen=True)
class VerifiedRemoteAuthorization:
    evidence: RemoteGrantEvidence
    sync_app: remote_approval.SyncAppProof | None
    catalog_sha256: str
    legacy_join_sha256: str


@dataclass(frozen=True)
class RemoteProofResult:
    status: str
    authorization: VerifiedRemoteAuthorization | None = field(default=None, repr=False)
    reason: str = ''
    notice: str = ''

    def readback(self):
        # Bodies, people IDs, protected paths and secrets never enter readback.
        proof = self.authorization
        return {'status': self.status, 'reason': self.reason, 'notice': self.notice,
                'sync_app_status': 'verified' if proof and proof.sync_app else 'pending',
                'approval_id': proof.evidence.approval_id if proof else '',
                'checked_at': proof.evidence.checked_at if proof else 0,
                'valid_until': proof.evidence.valid_until if proof else 0,
                'live_verified': False, 'grant_activated': False}


@dataclass(frozen=True)
class _Snapshot:
    context: remote_approval.ProofContext = field(repr=False)
    approval: remote_approval.ApprovalProof
    sync_app: remote_approval.SyncAppProof | None
    claim_event_id: str
    heartbeat: int


def _need(value):
    if not value:
        raise _Pending()


def _capabilities(value):
    _need(type(value) is tuple and bool(value)
          and all(type(item) is str for item in value)
          and value == tuple(item for item in CAPABILITIES if item in value))
    return value


class RemoteProofs:
    def __init__(self, catalog_path, legacy_join_path, *, record, reader, bot,
                 clock=lambda: int(time.time())):
        self.catalog_path, self.legacy_join_path = Path(catalog_path), Path(legacy_join_path)
        self.record, self.reader, self.bot, self.clock = record, reader, bot, clock

    def _identity(self, target):
        r, reader, bot = self.record, self.reader, self.bot
        _need(type(r) is agent_catalog.AgentRecord and type(target) is RemoteTarget
              and isinstance(reader, OwnAgentReader) and isinstance(bot, BotLarkCli))
        _need(r.status == 'own_bot_verified' and r.local_bot_verified is True
              and target.channel_id in r.channels
              and (target.agent_pubkey, target.owner_pubkey, target.app_id) == (r.pubkey, r.owner_pubkey, r.app_id)
              and (reader.agent, reader.owner, reader.origin) == (r.pubkey, r.owner_pubkey, target.relay_url)
              and Path(reader.env_file) == r.env_file
              and (bot.app_id, bot.config_dir, bot.data_dir) == (r.app_id, r.lark_config_dir, r.lark_data_dir)
              and bot.chat_id in (None, target.chat_id)
              and isinstance(target.chat_id, str) and gs.CHAT_ID_RE.fullmatch(target.chat_id)
              and target.chat_ref == gs.chat_ref(target.chat_id))
        # Scope fields receive the same exact type/range checks as the parser.
        remote_approval._scope(self._scope(target))

    @staticmethod
    def _scope(target):
        return remote_approval.ApprovalScope(target.agent_pubkey, target.owner_pubkey, target.app_id,
            target.channel_id, target.chat_ref, target.mirror_pubkey, target.mirror_owner_pubkey, target.claimed_at)

    def _catalog(self, target, original=None):
        self._identity(target)
        current = agent_catalog.load(self.catalog_path, legacy_join_path=self.legacy_join_path)
        matches = [row for row in current.records if row.name == self.record.name]
        _need(len(matches) == 1 and matches[0] == self.record
              and matches[0].status == 'own_bot_verified')
        if original is not None:
            _need((current.catalog_sha256, current.legacy_join_sha256) ==
                  (original.catalog_sha256, original.legacy_join_sha256))
        _need(self.bot.identity() == (self.record.app_id, ''))
        # identity() is itself a protected read; detect changes across that read.
        again = agent_catalog.load(self.catalog_path, legacy_join_path=self.legacy_join_path)
        _need(again == current)
        self._identity(target)
        return current

    async def _query(self, filt):
        # No caller limit/completeness argument or hand-picked proof DTO exists.
        rows = await self.reader.query([dict(filt, limit=257)])
        _need(isinstance(rows, list) and len(rows) < 256
              and len({row['id'] for row in rows}) == len(rows))
        return rows

    async def _snapshot(self, target):
        profile_rows = await self._query({'kinds': [0], 'authors': [self.record.pubkey]})
        # All policies are necessary: a mirror omitted by discovery can win,
        # and a newer revocation must supersede an old valid-looking claim.
        policies = await self._query({'kinds': [30177]})
        roster_rows = await self._query({'kinds': [39002], 'authors': [self.reader.pin], '#d': [target.channel_id]})

        def candidates():
            result = {target.mirror_pubkey}
            for policy in policies:
                tag = authority.tags(policy, 'd')
                _need(len(tag) == 1 and len(tag[0]) == 2 and gs.HEX64_RE.fullmatch(tag[0][1]))
                body = authority.policy_body(policy, tag[0][1])
                feishu = body.get('feishu')
                if isinstance(feishu, dict) and feishu.get('mirror') is True:
                    result.add(tag[0][1])
            return sorted(result)

        mirror_keys = await thread_call(candidates)
        _need(len(mirror_keys) < 256)
        profiles = await self._query({'kinds': [0], 'authors': mirror_keys})
        # The actual own-reader protocol permits #h, not a caller invented #p
        # filter. Enumerate this mirror/channel's full bounded approval query;
        # the pure parser enforces the exact agent p tag and body crossrefs.
        approvals = await self._query({'kinds': [30078], 'authors': [target.mirror_pubkey],
                                      '#h': [target.channel_id]})
        now = self.clock()
        _need(type(now) is int and now >= 0)

        def validate():
            own_policy = authority.latest([row for row in policies if row['pubkey'] == self.record.owner_pubkey
                and authority.tags(row, 'd') == [['d', self.record.pubkey]]])
            context = remote_approval.ProofContext(self._scope(target), self.reader.pin,
                authority.latest(profile_rows), own_policy, tuple(profiles), tuple(policies),
                authority.latest(roster_rows), now, complete=True)
            # Invalid or differently scoped records cannot create a proof; two
            # distinct current valid approvals are ambiguous rather than a new
            # approval chosen by arbitrary arrival/order/timestamp.
            valid = []
            for event in approvals:
                try:
                    valid.append(remote_approval.verify(event, context))
                except remote_approval.ApprovalPending:
                    continue
            _need(len(valid) == 1)
            claim, _, claim_id = remote_approval._current(context)
            try:
                sync = remote_approval.sync_app(context)
            except remote_approval.ApprovalPending:
                sync = None  # Own-agent approval is distinct from human mapping.
            return _Snapshot(context, valid[0], sync, claim_id, claim.heartbeat)

        return await thread_call(validate)

    def _bot_read(self, target, capabilities):
        self._identity(target)
        _need(self.bot.identity() == (self.record.app_id, ''))
        # bot_scopes() exposes only a set, discarding the full native envelope.
        # Retain and validate that identity here using the same real bot-token
        # endpoint and granted alternatives; never accept a caller scope flag.
        payload = self.bot.call('remote approval scopes', ['api', 'GET',
            '/open-apis/application/v6/scopes', '--as', 'bot'], full=True)
        scopes = parse_bot_scope_envelope(payload)
        _need(all(choices & scopes for choices in READ_SCOPE_GROUPS.values())
              and all(WRITE_SCOPE_GROUPS[action] & scopes for action in capabilities))
        listing = self.bot.member_listing(target.chat_id, 'union_id')
        _need(listing.complete is True and self.record.app_id in listing.bots)
        _need(self.bot.identity() == (self.record.app_id, ''))

    async def verify(self, target, *, capabilities=('message',)):
        try:
            capabilities = _capabilities(capabilities)
            self._identity(target)
            original = await thread_call(self._catalog, target)
            first = await self._snapshot(target)
            await thread_call(self._bot_read, target, capabilities)
            # Current remote authority may have changed while the bot API was
            # awaited. Never return the pre-membership signed snapshot as fresh.
            current = await self._snapshot(target)
            _need(first.approval == current.approval)
            catalog = await thread_call(self._catalog, target, original)
            self._identity(target)
            now = self.clock()
            _need(type(now) is int and now >= current.context.now)
            valid_until = min(current.context.now + PROOF_SECONDS,
                              current.heartbeat + gs.CLAIM_LEASE_SECONDS + 1)
            _need(now < valid_until)
            c = current.context
            e = RemoteGrantEvidence(self.record.pubkey, self.record.owner_pubkey, self.record.app_id,
                target.channel_id, target.chat_id, target.chat_ref, self.reader.origin,
                target.mirror_pubkey, target.mirror_owner_pubkey, current.claim_event_id,
                c.agent_profile['id'], c.agent_policy['id'], c.roster['id'],
                Store._remote_digest(sorted(self.record.channels)), 'mirror_approval',
                current.approval.record_id, current.approval.content_hash, capabilities,
                current.context.now, valid_until, target.claimed_at)
            Store._remote_evidence(e)  # Pure metadata/type validation; no SQL.
            return RemoteProofResult('verified', VerifiedRemoteAuthorization(e, current.sync_app,
                catalog.catalog_sha256, catalog.legacy_join_sha256))
        except Exception:
            # Cancellation (BaseException) propagates only after thread_call
            # reaps its actual IO; arbitrary transport exceptions never escape.
            return RemoteProofResult('pending', reason='evidence_unverified', notice=NOTICE)
