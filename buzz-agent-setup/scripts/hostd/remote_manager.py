"""Root-internal remote grant lifecycle using actual protected/signed adapters.

Discovery is a lookup only. Independent own-agent approval evidence is consumed
before an owning-loop SQL CAS. No foreign binding, worker, profile or credential
is created. Root owns the Store/pool and schedules these bounded operations.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, asdict
from pathlib import Path
import time

import buzz_feishu_group_sync as gs
from . import agent_catalog
from .agent_catalog import AgentRecord
from .agent_signed_reads import OwnAgentReader
from .async_io import thread_call
from .bot_clients import BotLarkCli
from .join_effects import Nip98Relay
from .remote_mapping import RemoteMappingContext
from .remote_outlet import OutletOutcome, DrainOutcome
from .remote_proofs import RemoteProofs, RemoteProofResult, VerifiedRemoteAuthorization, _capabilities
from .remote_runtime import RemoteRuntime
from .remote_target import RemoteTargetResolver, RemoteResolution, RemoteTarget
from .store import Store, RemoteGrantEvidence, stamp

NOTICE = ('远端授权或投递仍待核验。怎么解决：检查本机 active agent、自己的应用与群、受保护目录及旧任务排除证据、实际签名批准和当前 grant；暂停目标不能自动恢复，未知投递只核验原回执。'
          '\n复制给 AI：帮我核查 hostd remote manager 的签名批准、当前 SQL revision 和原投递回执；不要借用其他机器的配置、输出凭据或正文，也不要把发现当批准或换 grant 重发。')


class _Pending(ValueError):
    def __init__(self):
        super().__init__(NOTICE)


def _need(value):
    if not value:
        raise _Pending()


@dataclass(frozen=True)
class ManagerResult:
    status: str
    target_id: str = ''
    revision: int = 0
    scope_hash: str = ''
    reason: str = ''
    notice: str = ''

    def readback(self):
        return {**asdict(self), 'live_verified': False}


class RemoteManager:
    def __init__(self, store, catalog_path, legacy_join_path, *, record, reader,
                 bot, discovery_relay, link_base, clock=lambda: int(time.time()),
                 capabilities=('message',)):
        _need(isinstance(store, Store) and type(record) is AgentRecord
              and isinstance(reader, OwnAgentReader) and isinstance(bot, BotLarkCli)
              and isinstance(discovery_relay, Nip98Relay) and callable(clock))
        self.store, self.record, self.reader, self.bot = store, record, reader, bot
        self.discovery_relay, self.link_base, self.clock = discovery_relay, link_base, clock
        try:
            self.capabilities = _capabilities(capabilities)
        except Exception:
            raise _Pending() from None
        self.catalog_path, self.legacy_join_path = Path(catalog_path), Path(legacy_join_path)
        self._closed, self._inflight = False, set()
        self._factory()
        self._proofs = RemoteProofs(self.catalog_path, self.legacy_join_path,
            record=record, reader=reader, bot=bot, clock=clock)
        self._runtime = RemoteRuntime(store, record=record, reader=reader, bot=bot,
            proofs=self._proofs, link_base=link_base, clock=clock)

    def _factory(self):
        r, reader, bot, relay = self.record, self.reader, self.bot, self.discovery_relay
        _need(not self._closed and self.catalog_path.is_absolute() and self.legacy_join_path.is_absolute()
              and r.status == 'own_bot_verified' and r.local_bot_verified is True
              and (reader.agent, reader.owner, Path(reader.env_file))
                  == (r.pubkey, r.owner_pubkey, r.env_file)
              and (relay.owner, relay.origin, relay.relay_pubkey)
                  == (r.owner_pubkey, reader.origin, reader.pin)
              and (bot.app_id, bot.config_dir, bot.data_dir)
                  == (r.app_id, r.lark_config_dir, r.lark_data_dir))

    def _current(self, channel, expected_revision, original=None):
        """Short pure SQL frame; historical proof expiry must not block renewal."""
        self._factory()
        stamp(expected_revision)
        _need(isinstance(channel, str) and gs.UUID_RE.fullmatch(channel)
              and channel in self.record.channels)
        target_id = Store._remote_digest([self.record.pubkey, channel])
        with self.store.transaction():
            order = getattr(self.store, '_own_home_order', None)
            _need(self.store.active_own_home_admission(self.record.pubkey) is None
                  and (order is None or not order._busy(self.record.pubkey)))
            local = self.store.conn.execute('SELECT 1 FROM binding WHERE channel_id=? LIMIT 1',
                                            (channel,)).fetchone()
            _need(local is None)
            agent = self.store.conn.execute('SELECT * FROM agent WHERE pubkey=?',
                                            (self.record.pubkey,)).fetchone()
            row = self.store.conn.execute('SELECT status,current_revision FROM remote_target WHERE target_id=?',
                                          (target_id,)).fetchone()
            grant = self.store.remote_grant(target_id)
            _need(agent is not None and agent['status'] == 'active'
                  and (agent['owner_pubkey'], agent['app_id'], agent['config_path'])
                      == (self.record.owner_pubkey, self.record.app_id, str(self.record.env_file)))
            if row is None:
                _need(grant is None and expected_revision == 0)
                snapshot = (target_id, 0, '')
            else:
                _need(row['status'] == 'active' and grant is not None
                      and row['current_revision'] == grant.revision == expected_revision
                      and grant.agent_id == self.record.pubkey
                      and (grant.evidence.owner_pubkey, grant.evidence.app_id, grant.evidence.channel_id)
                          == (self.record.owner_pubkey, self.record.app_id, channel)
                      and Store._remote_scope(grant.evidence) == grant.scope_hash)
                snapshot = (target_id, grant.revision, grant.scope_hash)
            _need(original is None or snapshot == original)
            return snapshot

    def _catalog(self, original=None, authorization=None):
        """IO-only protected snapshot; no SQLite in the executor."""
        self._factory()
        catalog = agent_catalog.load(self.catalog_path, legacy_join_path=self.legacy_join_path)
        records = [r for r in catalog.records if r.name == self.record.name]
        _need(len(records) == 1 and records[0] == self.record
              and (original is None or catalog == original)
              and self.bot.identity() == (self.record.app_id, ''))
        if authorization is not None:
            _need((catalog.catalog_sha256, catalog.legacy_join_sha256)
                  == (authorization.catalog_sha256, authorization.legacy_join_sha256))
        # Reading own identity may race protected profiles. Re-read rather than
        # accepting a cached configured APP as physical current evidence.
        _need(agent_catalog.load(self.catalog_path, legacy_join_path=self.legacy_join_path) == catalog)
        self._factory()
        return catalog

    def _target(self, target, channel):
        self._factory()
        _need(type(target) is RemoteTarget
              and (target.agent_pubkey, target.owner_pubkey, target.app_id, target.channel_id,
                   target.relay_url, target.chat_ref)
                  == (self.record.pubkey, self.record.owner_pubkey, self.record.app_id,
                      channel, self.reader.origin, gs.chat_ref(target.chat_id))
              and self.bot.chat_id == target.chat_id)
        # Validate the reviewed public origin using the accepted mapping parser.
        RemoteMappingContext(target, reader=self.reader, bot_client=self.bot,
                             clock=self.clock, link_base=self.link_base)._link_origin()

    async def _prepare(self, channel, expected_revision):
        original = self._current(channel, expected_revision)
        catalog = await thread_call(self._catalog)
        self._current(channel, expected_revision, original)

        def authorize(record, requested_channel):
            try:
                _need(record is self.record and requested_channel == channel)
                self._current(channel, expected_revision, original)
                return True
            except Exception:
                return False

        # Only this internal SQL guard authorizes read-only discovery. It never
        # authorizes a grant. Approval is independently read through own OA.
        resolver = RemoteTargetResolver(self.catalog_path, self.legacy_join_path,
            relay=self.discovery_relay, clients={self.record.app_id: self.bot},
            authorize=authorize, clock=self.clock)
        discovery = await resolver.resolve(self.record, channel)
        self._current(channel, expected_revision, original)
        _need(type(discovery) is RemoteResolution and discovery.status == 'verified')
        target = discovery.target
        self._target(target, channel)
        await thread_call(self._catalog, catalog)
        self._current(channel, expected_revision, original)

        proof = await self._proofs.verify(target, capabilities=self.capabilities)
        self._current(channel, expected_revision, original)
        _need(type(proof) is RemoteProofResult and proof.status == 'verified'
              and type(proof.authorization) is VerifiedRemoteAuthorization)
        authorization = proof.authorization
        evidence = authorization.evidence
        _need(type(evidence) is RemoteGrantEvidence
              and (evidence.agent_id, evidence.owner_pubkey, evidence.app_id, evidence.channel_id,
                   evidence.chat_id, evidence.chat_ref, evidence.relay_origin, evidence.mirror_pubkey,
                   evidence.mirror_owner_pubkey, evidence.claimed_at)
                  == (self.record.pubkey, self.record.owner_pubkey, self.record.app_id, channel,
                      target.chat_id, target.chat_ref, self.reader.origin, target.mirror_pubkey,
                      target.mirror_owner_pubkey, target.claimed_at)
              and evidence.allowlist_hash == Store._remote_digest(sorted(self.record.channels)))
        await thread_call(self._catalog, catalog, authorization)
        self._current(channel, expected_revision, original)
        self._target(target, channel)
        now = self.clock()
        _need(type(now) is int and evidence.checked_at <= now < evidence.valid_until)
        # No await or external IO in this final frame. Current scope and closure
        # checks plus Store's expected-revision CAS precede recording metadata.
        with self.store.transaction():
            self._current(channel, expected_revision, original)
            grant = self.store.activate_remote_grant(evidence, expected_revision=expected_revision, now=now)
            _need(self.store.refresh_remote_proof(grant.target_id, evidence,
                  revision=grant.revision, scope_hash=grant.scope_hash, now=now))
        return target, grant

    async def _run(self, operation, pending_result):
        # Track a scoped completion, not the caller's whole persistent task.
        # Execute inline: there is no extra task-completion scheduling gap after
        # the final SQL CAS. Existing thread/child helpers reap cancellation
        # before this completion releases root's borrowed Store/pool lifetime.
        order = getattr(self.store, '_own_home_order', None)
        entered = order is not None and order._try_enter(self.record.pubkey)
        if order is not None and not entered:
            operation.close()  # No unawaited coroutine and no same-pubkey IO.
            return pending_result
        completed = asyncio.get_running_loop().create_future()
        self._inflight.add(completed)
        try:
            return await operation
        finally:
            completed.set_result(None)
            self._inflight.discard(completed)
            if entered:
                order._leave(self.record.pubkey)

    async def _reconcile(self, channel, expected_revision):
        try:
            _, grant = await self._prepare(channel, expected_revision)
            return ManagerResult('active', grant.target_id, grant.revision, grant.scope_hash)
        except Exception:
            return ManagerResult('pending', reason='authorization', notice=NOTICE)

    async def reconcile(self, channel, *, expected_revision):
        return await self._run(self._reconcile(channel, expected_revision),
            ManagerResult('pending', reason='authorization', notice=NOTICE))

    async def _deliver(self, channel, event, expected_revision):
        try:
            target, grant = await self._prepare(channel, expected_revision)
            original = (grant.target_id, grant.revision, grant.scope_hash)
            self._current(channel, grant.revision, original)
            result = await self._runtime.deliver(target, event, target_id=grant.target_id,
                                                revision=grant.revision, scope_hash=grant.scope_hash)
            self._current(channel, grant.revision, original)
            return result
        except Exception:
            return OutletOutcome('pending', 'authorization', notice=NOTICE)

    async def deliver(self, channel, event, *, expected_revision):
        return await self._run(self._deliver(channel, event, expected_revision),
            OutletOutcome('pending', 'authorization', notice=NOTICE))

    async def _drain(self, channel, expected_revision):
        try:
            target, grant = await self._prepare(channel, expected_revision)
            original = (grant.target_id, grant.revision, grant.scope_hash)
            self._current(channel, grant.revision, original)
            result = await self._runtime.drain(target, target_id=grant.target_id,
                                              revision=grant.revision, scope_hash=grant.scope_hash)
            self._current(channel, grant.revision, original)
            return result
        except Exception:
            return DrainOutcome('pending', 'authorization', pending_count=1, notice=NOTICE)

    async def drain(self, channel, *, expected_revision):
        return await self._run(self._drain(channel, expected_revision),
            DrainOutcome('pending', 'authorization', pending_count=1, notice=NOTICE))

    async def close(self):
        """Reject gates first; reap owned calls even if this waiter is cancelled."""
        self._closed = True
        self._runtime.close()
        completions = tuple(self._inflight)
        pending = asyncio.gather(*completions, return_exceptions=True)
        cancelled = False
        while not pending.done():
            try:
                await asyncio.shield(pending)
            except asyncio.CancelledError:
                cancelled = True
        pending.result()
        if cancelled:
            raise asyncio.CancelledError
