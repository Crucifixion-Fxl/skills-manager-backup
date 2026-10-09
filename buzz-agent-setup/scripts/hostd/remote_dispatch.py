"""Root-owned remote lanes; socket events only wake independent signed drains.

The onboarding Store, scheduler and pool are borrowed. Each admitted own-agent
channel has distinct discovery/sending bot lanes and its own authenticated feed.
No foreign binding, worker, app profile or caller-supplied proof is accepted.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import time
from types import SimpleNamespace

from . import agent_catalog, relay_feed
from .agent_signed_reads import OwnAgentReader
from .async_io import thread_call
from .native_scopes import parse_bot_scope_envelope
from .bot_clients import BotLarkCli
from .onboarding_runtime import OnboardingRuntime
from .remote_manager import RemoteManager
from .remote_proofs import CAPABILITIES, WRITE_SCOPE_GROUPS, _capabilities
from .remote_mapping import RemoteMappingContext
from .remote_target import RemoteTargetResolver
from .store import Store

PERIOD_SECONDS = 30
MAX_LANES = 256
NOTICE = ('远端发送仍待核验。怎么解决：核对本机 agent、频道授权、自己的应用、'
          '受保护目录、当前本地绑定和签名批准。复制给 AI：检查 hostd 远端发送协调器，'
          '不要借用其他机器的身份、把 socket 消息当授权或换 grant 重发。')


def _need(value):
    if not value:
        raise ValueError(NOTICE)


def _native_write_capabilities(record, bot, chat_id):
    """Choose only a fresh lane's bounded, actual own-bot scope grants."""
    _need(type(bot) is BotLarkCli and bot.chat_id == chat_id
          and (bot.app_id, bot.config_dir, bot.data_dir)
              == (record.app_id, record.lark_config_dir, record.lark_data_dir))
    _need(bot.identity() == (record.app_id, ''))
    payload = bot.call('daemon remote capability scopes',
        ['api', 'GET', '/open-apis/application/v6/scopes', '--as', 'bot'], full=True)
    granted = parse_bot_scope_envelope(payload)
    capabilities = tuple(action for action in CAPABILITIES
                         if WRITE_SCOPE_GROUPS[action] & granted)
    _need(capabilities)
    _capabilities(capabilities)
    _need(bot.identity() == (record.app_id, ''))
    return capabilities


@dataclass
class _Lane:
    record: object
    channel: str
    manager: RemoteManager | None = None
    feed: asyncio.Task | None = None
    retry_at: float = 0
    retry_seconds: float = 1


class RemoteDispatch:
    def __init__(self, onboarding, *, scheduler, http_pool, base_env=None,
                 clock=lambda: int(time.time())):
        if (not isinstance(onboarding, OnboardingRuntime)
                or not isinstance(onboarding.store, Store)
                or not callable(getattr(scheduler, 'run', None))
                or not callable(clock) or onboarding._closed):
            raise ValueError(NOTICE)
        self.onboarding, self.store = onboarding, onboarding.store
        self.scheduler, self.http_pool = scheduler, http_pool
        self.base_env = {} if base_env is None else dict(base_env)
        self.clock = clock
        self._closed, self._close_task = False, None
        self._lanes, self._dirty, self._tasks = {}, set(), set()
        self._wake, self._refresh_lock = asyncio.Event(), asyncio.Lock()
        self.last_notice = ''

    def _spawn(self, coroutine):
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def _current(self, record, channel):
        """Main-loop SQL guard for every awaited discovery admission boundary."""
        if (self._closed or self.onboarding._closed or not self._public_base()
                or record.status != 'own_bot_verified'
                or not record.local_bot_verified
                or record.owner_pubkey != self.onboarding.relay.owner
                or channel not in record.channels):
            return False
        with self.store.transaction():
            order = getattr(self.store, '_own_home_order', None)
            if (self.store.active_own_home_admission(record.pubkey) is not None
                    or (order is not None and order._busy(record.pubkey))):
                return False
            row = self.store.conn.execute('SELECT * FROM agent WHERE pubkey=?',
                                          (record.pubkey,)).fetchone()
            local = self.store.conn.execute('SELECT 1 FROM binding WHERE channel_id=? LIMIT 1',
                                            (channel,)).fetchone()
            target = self.store.conn.execute('SELECT status FROM remote_target WHERE target_id=?',
                (Store._remote_digest([record.pubkey, channel]),)).fetchone()
            return (row is not None and row['status'] == 'active' and local is None
                and (row['owner_pubkey'], row['app_id'], row['config_path'])
                    == (record.owner_pubkey, record.app_id, str(record.env_file))
                and (target is None or target['status'] == 'active'))

    def _public_base(self):
        base = self.onboarding.config.remote_link_base
        try:
            RemoteMappingContext._link_origin(SimpleNamespace(link_base=base))
            return base
        except Exception:
            return ''

    def _revision(self, lane):
        if not self._current(lane.record, lane.channel):
            raise ValueError(NOTICE)
        row = self.store.conn.execute('SELECT current_revision FROM remote_target WHERE target_id=?',
            (Store._remote_digest([lane.record.pubkey, lane.channel]),)).fetchone()
        return row['current_revision'] if row is not None else 0

    def mark_dirty(self, agent_pubkey, channel):
        key = (agent_pubkey, channel)
        if not self._closed and key in self._lanes:
            self._dirty.add(key)
            self._wake.set()

    async def _feed(self, key, lane):
        async def hint(_identity, _event):
            # Discard every payload, including reconnect controls. Only the
            # manager's own signed query can authorize source/target delivery.
            self.mark_dirty(*key)

        def status(_identity, _kind, value):
            if value == 'connected':
                self.mark_dirty(*key)

        config = self.onboarding.config
        await relay_feed.follow('remote-' + Store._remote_digest(list(key))[:40],
            str(lane.record.env_file), lane.channel, hint, status,
            trusted_relays=config.trusted_relays, author=lane.record.pubkey,
            status_kind='outlet', replay_since=lambda: self.store.remote_replay_since(
                Store._remote_digest(list(key))))

    async def _work(self, key, lane):
        def retry():
            lane.retry_at = asyncio.get_running_loop().time() + lane.retry_seconds
            lane.retry_seconds = min(lane.retry_seconds * 2, PERIOD_SECONDS)
            self.last_notice = NOTICE

        order = getattr(self.store, '_own_home_order', None)
        entered = False
        try:
            if order is not None:
                entered = order._try_enter(lane.record.pubkey)
                if not entered:
                    retry()
                    return  # A busy pubkey never awaits another agent's gate.
            if (not self._current(lane.record, lane.channel)
                    or asyncio.get_running_loop().time() < lane.retry_at):
                return
            config = self.onboarding.config
            if lane.manager is None:
                discovery_bot = BotLarkCli(lane.record.app_id, lane.record.lark_config_dir,
                    lane.record.lark_data_dir, base_env=self.base_env,
                    scheduler=self.scheduler, http_pool=self.http_pool, chat_id=None)
                resolver = RemoteTargetResolver(config.catalog_path, config.legacy_join_path,
                    relay=self.onboarding.relay, clients={lane.record.app_id: discovery_bot},
                    authorize=lambda record, channel: record is lane.record
                        and channel == lane.channel and self._current(record, channel),
                    clock=self.clock)
                found = await resolver.resolve(lane.record, lane.channel)
                if not self._current(lane.record, lane.channel) or found.status != 'verified':
                    retry()
                    return
                reader = OwnAgentReader(lane.record, origin=self.onboarding.relay.origin,
                    relay_pubkey=config.relay_pubkey, trusted_relays=config.trusted_relays,
                    clock=self.clock, http=self.onboarding.http)
                bot = BotLarkCli(lane.record.app_id, lane.record.lark_config_dir,
                    lane.record.lark_data_dir, base_env=self.base_env,
                    scheduler=self.scheduler, http_pool=self.http_pool, chat_id=found.target.chat_id)
                # A prior grant is immutable authority state. Reconstruct its
                # exact tuple after restart; never rescope it from fresh permissions.
                target_id = Store._remote_digest([lane.record.pubkey, lane.channel])
                expected_revision = self._revision(lane)
                grant = self.store.remote_grant(target_id)
                if grant is not None:
                    if (grant.target_id != target_id or grant.agent_id != lane.record.pubkey
                            or grant.revision != expected_revision
                            or (grant.evidence.agent_id, grant.evidence.owner_pubkey,
                                grant.evidence.app_id, grant.evidence.channel_id)
                               != (lane.record.pubkey, lane.record.owner_pubkey,
                                   lane.record.app_id, lane.channel)
                            or grant.capabilities != grant.evidence.capabilities):
                        retry()
                        return
                    capabilities = _capabilities(grant.evidence.capabilities)
                else:
                    # Check the authoritative lane/source immediately before and
                    # after the awaited native read; a new grant racing this call
                    # must prevent creation of a manager with a fresh tuple.
                    if (expected_revision != 0 or not self._current(lane.record, lane.channel)):
                        retry()
                        return
                    capabilities = await thread_call(_native_write_capabilities,
                        lane.record, bot, found.target.chat_id)
                    if (not self._current(lane.record, lane.channel)
                            or self._revision(lane) != expected_revision
                            or self.store.remote_grant(target_id) is not None):
                        retry()
                        return
                lane.manager = RemoteManager(self.store, config.catalog_path, config.legacy_join_path,
                    record=lane.record, reader=reader, bot=bot,
                    discovery_relay=self.onboarding.relay, link_base=self._public_base(),
                    clock=self.clock, capabilities=capabilities)
            active = await lane.manager.reconcile(lane.channel, expected_revision=self._revision(lane))
            if not self._current(lane.record, lane.channel) or active.status != 'active':
                retry()
                return
            if lane.feed is None or lane.feed.done():
                lane.feed = self._spawn(self._feed(key, lane))
            result = await lane.manager.drain(lane.channel, expected_revision=self._revision(lane))
            if result.status != 'complete':
                retry()
            else:
                lane.retry_at, lane.retry_seconds = 0, 1
        except Exception:
            retry()
        finally:
            if entered:
                order._leave(lane.record.pubkey)  # Manager/runtime nested same task.

    async def _retire(self, key):
        lane = self._lanes[key]
        self._dirty.discard(key)
        try:
            if lane.feed is not None:
                lane.feed.cancel()
                await asyncio.gather(lane.feed, return_exceptions=True)
        finally:
            if lane.manager is not None:
                await lane.manager.close()
        self._lanes.pop(key, None)

    async def _refresh(self):
        async with self._refresh_lock:
            if self._closed:
                return
            config = self.onboarding.config
            try:
                catalog = await thread_call(agent_catalog.load, config.catalog_path,
                    legacy_join_path=config.legacy_join_path)
                # Include every registered status, so a paused/pending local
                # channel never obtains a second remote writer.
                local = {row['channel_id'] for row in self.store.bindings()}
                expected = {(record.pubkey, channel): record for record in catalog.records
                    if record.status == 'own_bot_verified' and record.local_bot_verified
                    and record.owner_pubkey == self.onboarding.relay.owner
                    for channel in record.channels if channel not in local
                    and self._current(record, channel)}
                if len(expected) > MAX_LANES:
                    raise ValueError(NOTICE)
            except Exception:
                expected = {}
                self.last_notice = NOTICE
            if self._closed:
                return
            for key, lane in list(self._lanes.items()):
                if (key not in expected or expected[key] != lane.record
                        or (lane.manager is not None and lane.manager.link_base != self._public_base())):
                    await self._retire(key)
            if self._closed:
                return
            for key, record in expected.items():
                self._lanes.setdefault(key, _Lane(record, key[1]))
            self._dirty.difference_update(expected)
            tasks = [self._spawn(self._work(key, self._lanes[key])) for key in expected]
            if tasks:
                await asyncio.gather(*tasks)

    async def refresh(self):
        if not self._closed:
            await self._spawn(self._refresh())

    async def run(self):
        backoff = 1
        while not self._closed:
            self._wake.clear()
            try:
                await self.refresh()
                backoff = 1
            except Exception:
                self.last_notice = NOTICE
                backoff = min(backoff * 2, PERIOD_SECONDS)
            if self._closed:
                return
            try:
                await asyncio.wait_for(self._wake.wait(), PERIOD_SECONDS if backoff == 1 else backoff)
            except asyncio.TimeoutError:
                pass  # A missed socket wake is recovered by the periodic signed drain.

    def stop(self):
        self._closed = True
        self._wake.set()
        for task in tuple(self._tasks):
            if not task.done() and not task.cancelling():
                task.cancel()

    async def _close(self):
        await asyncio.gather(*tuple(self._tasks), return_exceptions=True)
        for key in list(self._lanes):
            await self._retire(key)

    async def close(self):
        self.stop()
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
        cancelled = False
        while not self._close_task.done():
            try:
                await asyncio.shield(self._close_task)
            except asyncio.CancelledError:
                cancelled = True
        self._close_task.result()
        if cancelled:
            raise asyncio.CancelledError
