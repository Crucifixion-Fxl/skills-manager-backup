"""Internal root bridge from real fresh remote proof to an existing SQL grant.

Discovery never creates authority here. SQLite stays on its owning loop; only
protected file/client reads run in reaped IO threads. The accepted outlet owns
serialization, original delivery pins, uncertainty and physical receipt checks.
"""
from __future__ import annotations

from pathlib import Path
import time

from . import agent_catalog
from .agent_catalog import AgentRecord
from .agent_signed_reads import OwnAgentReader
from .async_io import thread_call
from .bot_clients import BotLarkCli
from .remote_mapping import RemoteMappingContext
from .remote_outlet import RemoteOutlet, OutletOutcome, DrainOutcome
from .remote_proofs import RemoteProofs, RemoteProofResult, VerifiedRemoteAuthorization
from .remote_target import RemoteTarget
from .store import Store, RemoteGrantEvidence, hexid, stamp

NOTICE = ('远端发送保持待核验，当前授权或原投递尚未确认。怎么解决：核对本机 active agent、自己的应用与受保护目录、旧任务排除证据、原批准及当前 grant；结果未知时只核验原回执，不要重复发送。'
          '\n复制给 AI：帮我核查 hostd 远端 runtime 的当前 SQL 授权、受保护目录和原投递回执；不要输出正文或凭据，不要把目标发现当作批准或换 grant 重发。')


class _Pending(ValueError):
    pass


def _need(value):
    if not value:
        raise _Pending(NOTICE)


class RemoteRuntime:
    def __init__(self, store, *, record, reader, bot, proofs, link_base,
                 clock=lambda: int(time.time())):
        # No constructor IO, discovered profile fallback or public callback/DTO
        # authorization interface. These are root's exact existing adapters.
        _need(isinstance(store, Store) and type(record) is AgentRecord
              and isinstance(reader, OwnAgentReader) and isinstance(bot, BotLarkCli)
              and type(proofs) is RemoteProofs and callable(clock))
        _need(proofs.record is record and proofs.reader is reader and proofs.bot is bot)
        self.store, self.record, self.reader, self.bot, self.proofs = store, record, reader, bot, proofs
        self.link_base, self.clock, self._closed = link_base, clock, False
        self._catalog_path = Path(proofs.catalog_path)
        self._legacy_path = Path(proofs.legacy_join_path)
        _need(self._catalog_path.is_absolute() and self._legacy_path.is_absolute())

    def close(self):
        """Reject further gates; root retains Store/pool until IO is reaped."""
        self._closed = True

    def _factory(self, target):
        r = self.record
        _need(not self._closed and type(target) is RemoteTarget
              and self.proofs.record is r and self.proofs.reader is self.reader
              and self.proofs.bot is self.bot
              and Path(self.proofs.catalog_path) == self._catalog_path
              and Path(self.proofs.legacy_join_path) == self._legacy_path
              and r.status == 'own_bot_verified' and r.local_bot_verified is True
              and target.channel_id in r.channels
              and (r.pubkey, r.owner_pubkey, r.app_id)
                  == (target.agent_pubkey, target.owner_pubkey, target.app_id)
              and (self.reader.agent, self.reader.owner, self.reader.origin)
                  == (r.pubkey, r.owner_pubkey, target.relay_url)
              and Path(self.reader.env_file) == r.env_file
              and (self.bot.app_id, self.bot.config_dir, self.bot.data_dir)
                  == (r.app_id, r.lark_config_dir, r.lark_data_dir)
              and self.bot.chat_id == target.chat_id)

    def _current(self, target, target_id, revision, scope_hash):
        """Pure current SQL gate, deliberately independent of proof expiry."""
        self._factory(target)
        hexid(target_id); hexid(scope_hash)
        _need(stamp(revision) > 0)
        _need(target_id == Store._remote_digest([self.record.pubkey, target.channel_id]))
        # No await/external transport in this frame. A short transaction gives
        # a coherent SQL view across agent, target and the immutable grant.
        with self.store.transaction():
            order = getattr(self.store, '_own_home_order', None)
            _need(self.store.active_own_home_admission(self.record.pubkey) is None
                  and (order is None or not order._busy(self.record.pubkey)))
            local = self.store.conn.execute('SELECT 1 FROM binding WHERE channel_id=? LIMIT 1',
                                            (target.channel_id,)).fetchone()
            _need(local is None)
            agent = self.store.conn.execute('SELECT * FROM agent WHERE pubkey=?',
                                            (self.record.pubkey,)).fetchone()
            target_row = self.store.conn.execute('SELECT status FROM remote_target WHERE target_id=?',
                                                 (target_id,)).fetchone()
            grant = self.store.remote_grant(target_id)
            _need(agent is not None and agent['status'] == 'active'
                  and (agent['owner_pubkey'], agent['app_id'], agent['config_path'])
                      == (self.record.owner_pubkey, self.record.app_id, str(self.record.env_file))
                  and target_row is not None and target_row['status'] == 'active'
                  and grant is not None and (grant.revision, grant.scope_hash) == (revision, scope_hash))
            e = grant.evidence
            _need(grant.agent_id == self.record.pubkey and Store._remote_scope(e) == scope_hash
                  and (e.agent_id, e.owner_pubkey, e.app_id, e.channel_id, e.chat_id, e.chat_ref,
                       e.relay_origin, e.mirror_pubkey, e.mirror_owner_pubkey, e.claimed_at)
                      == (target.agent_pubkey, target.owner_pubkey, target.app_id, target.channel_id,
                          target.chat_id, target.chat_ref, target.relay_url, target.mirror_pubkey,
                          target.mirror_owner_pubkey, target.claimed_at)
                  and self.store._remote_approval(e))
            return grant

    def _catalog(self, target, authorization):
        """Fresh protected-file CAS after the actual proof consumer returned."""
        self._factory(target)
        current = agent_catalog.load(self._catalog_path, legacy_join_path=self._legacy_path)
        selected = [row for row in current.records if row.name == self.record.name]
        _need(len(selected) == 1 and selected[0] == self.record
              and selected[0].status == 'own_bot_verified'
              and (current.catalog_sha256, current.legacy_join_sha256)
                  == (authorization.catalog_sha256, authorization.legacy_join_sha256)
              and self.bot.identity() == (self.record.app_id, ''))
        # Identity reading itself can race protected files; retain the actual
        # loader's record/hashes and check again, without reading prompt bodies.
        _need(agent_catalog.load(self._catalog_path, legacy_join_path=self._legacy_path) == current)
        self._factory(target)

    def _outlet(self, target, target_id, revision, scope_hash):
        self._current(target, target_id, revision, scope_hash)
        mappings = RemoteMappingContext(target, reader=self.reader, bot_client=self.bot,
                                       clock=self.clock, link_base=self.link_base)
        mappings._link_origin()  # Same accepted origin validation before IO.

        async def authorize(record, channel):
            _need(record is self.record and channel == target.channel_id)
            grant = self._current(target, target_id, revision, scope_hash)
            result = await self.proofs.verify(target, capabilities=grant.evidence.capabilities)
            self._current(target, target_id, revision, scope_hash)
            _need(type(result) is RemoteProofResult and result.status == 'verified'
                  and type(result.authorization) is VerifiedRemoteAuthorization)
            authorization = result.authorization
            _need(type(authorization.evidence) is RemoteGrantEvidence
                  and Store._remote_scope(authorization.evidence) == scope_hash)
            await thread_call(self._catalog, target, authorization)
            # After all IO, only pure SQL/scope gates remain before returning
            # actual evidence to Outlet's refresh/reserve/ACK transaction.
            self._current(target, target_id, revision, scope_hash)
            now = self.clock()
            _need(type(now) is int and authorization.evidence.checked_at <= now
                  < authorization.evidence.valid_until)
            return authorization.evidence

        return RemoteOutlet(self.store, self.record, self.reader, self.bot, mappings,
                            authorize=authorize, link_base=self.link_base, clock=self.clock)

    async def deliver(self, target, event, *, target_id, revision, scope_hash):
        order = getattr(self.store, '_own_home_order', None)
        entered = False
        try:
            if order is not None:
                entered = order._try_enter(self.record.pubkey)
                _need(entered)
            outlet = self._outlet(target, target_id, revision, scope_hash)
            return await outlet.deliver(event, target_id)
        except Exception:
            # CancelledError propagates after the actual accepted IO helper
            # reaps its transport. Never convert cancellation into a receipt.
            return OutletOutcome('pending', 'authorization', notice=NOTICE)
        finally:
            if entered:
                order._leave(self.record.pubkey)  # Joined outlet IO precedes release.

    async def drain(self, target, *, target_id, revision, scope_hash):
        order = getattr(self.store, '_own_home_order', None)
        entered = False
        try:
            if order is not None:
                entered = order._try_enter(self.record.pubkey)
                _need(entered)
            outlet = self._outlet(target, target_id, revision, scope_hash)
            return await outlet.drain(target.channel_id, target_id)
        except Exception:
            return DrainOutcome('pending', 'authorization', pending_count=1, notice=NOTICE)
        finally:
            if entered:
                order._leave(self.record.pubkey)  # Joined outlet IO precedes release.
