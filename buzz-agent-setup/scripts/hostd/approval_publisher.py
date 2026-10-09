"""Publish an actual local card decision as one immutable mirror-signed record.

This is a producer adapter, not a card handler or a remote grant. SQL stays on
its owning loop. The journal contains only public metadata; protected keys and
canonical event bodies exist only during bounded IO. Restored pins never POST.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import threading
import time

import buzz_feishu_group_sync as gs
import recovery_authority as authority
from . import remote_approval as approval
from .async_io import thread_call
from .join_effects import Nip98Relay, legacy
from .safety import read_owned
from .signed_reads import SignedReader
from .store import ApprovalPublicationPin, Store

NOTICE = ('卡片批准记录尚未完成签名读回，保留待核验。怎么解决：检查当前原始卡片决定、本机镜像文件、完整签名授权与原事件读回；未知发送只核验，不重复发布。'
          '\n复制给 AI：帮我核查 hostd 卡片批准发布的原事件与当前授权；不要输出密钥、卡片、回调或个人信息。')


class _Pending(ValueError):
    def __init__(self): super().__init__(NOTICE)


@dataclass(frozen=True)
class PublicationResult:
    status: str
    event_id: str = ''
    content_hash: str = ''
    notice: str = ''


@dataclass(frozen=True)
class _Local:
    files_hash: str
    mirror: str
    owner: str
    key: str = field(repr=False)


def _need(value):
    if not value: raise _Pending()


class ApprovalPublisher:
    def __init__(self, store, binding_id, relay, *, clock=lambda: int(time.time())):
        _need(isinstance(store, Store) and isinstance(relay, Nip98Relay))
        self.store, self.binding_id, self.relay, self.clock = store, binding_id, relay, clock
        self.reader = SignedReader(relay)
        self._locks = {}

    def _now(self):
        now = self.clock()
        _need(type(now) is int and 0 <= now < 2**63)
        return now

    def _sql(self, request_id):
        decision = self.store.card_approval_decision(request_id)
        request, plan = self.store.join_request(request_id), self.store.effect_plan(request_id)
        rows = [row for row in self.store.bindings() if row['binding_id'] == self.binding_id]
        _need(decision is not None and request and plan and len(rows) == 1)
        binding = rows[0]
        agent = self.store.conn.execute('SELECT config_path FROM agent WHERE pubkey=?',
                                        (decision.agent_pubkey,)).fetchone()
        _need(agent and isinstance(agent['config_path'], str))
        plan_scope = ((request['kind'] == 'channel' and plan['mirror_pubkey'] == ''
                       and plan['secret_ref'] == agent['config_path'] and request['binding_id'] == self.binding_id)
                      or (request['kind'] == 'new_binding' and plan['mirror_pubkey'] == binding['mirror_pubkey']
                          and plan['binding_id'] == request_id
                          and Path(plan['secret_ref']) == Path(plan['config_path']).parent / 'mirror.env'
                          and Path(plan['config_path']).name == 'config.json'
                          and Path(plan['config_path']).parent.name == request_id))
        _need(plan_scope and binding['status'] in ('active', 'pending')
              and (plan['binding_id'], plan['channel_id'], plan['config_path']) ==
                  (self.binding_id, binding['channel_id'], binding['config_path'])
              and request['chat_id'] == binding['chat_id']
              and (request['kind'] == 'new_binding' or request['binding_id'] == self.binding_id)
              and (not binding['chat_ref'] or binding['chat_ref'] == decision.chat_ref))
        # Heartbeat/process timestamps do not define the binding's authority.
        binding = {key: binding[key] for key in ('binding_id', 'channel_id', 'chat_id', 'sync_app_id',
            'config_path', 'mirror_pubkey', 'chat_ref', 'status', 'config_dir', 'data_dir')}
        binding.update(agent_env_file=agent['config_path'], request_kind=request['kind'])
        return decision, plan, binding

    def _guard(self, request_id, sql, pin=None):
        _need(self._sql(request_id) == sql)
        if pin is not None: _need(self.store._approval_current(pin))

    def _read_local(self, sql):
        """File/crypto IO only; this function never touches SQL."""
        decision, plan, binding = sql
        cfg_raw = read_owned(binding['config_path'])
        cfg = approval._json(cfg_raw.decode())
        _need((cfg.get('channel_id'), cfg.get('chat_id'), cfg.get('mirror_pubkey')) ==
              (binding['channel_id'], binding['chat_id'], binding['mirror_pubkey'])
              and gs.binding_claim_enabled(cfg))
        signer = cfg.get('people_api', {}).get('signer_env_file')
        _need(isinstance(signer, str) and Path(signer) == self.relay.owner_env_file)
        _need(binding['request_kind'] != 'new_binding' or cfg['mirror_env_file'] == plan['secret_ref'])
        agent_raw = read_owned(binding['agent_env_file'])
        agent_env = legacy.parse_env(agent_raw.decode())
        _need(gs._signer_pubkey(gs.secret_hex(agent_env.get('BUZZ_PRIVATE_KEY'), 'agent')) == decision.agent_pubkey
              and agent_env.get('BUZZ_ACP_AGENT_OWNER') == decision.agent_owner_pubkey
              and binding['channel_id'] in legacy._allowlist(agent_env.get('BUZZ_ACP_CHANNELS', '')))
        mirror_raw = read_owned(cfg['mirror_env_file'])
        owner_raw = read_owned(self.relay.owner_env_file)
        mirror_env, owner_env = legacy.parse_env(mirror_raw.decode()), legacy.parse_env(owner_raw.decode())
        _need(all(gs.relay_query_url(env.get('BUZZ_RELAY_URL')) == self.relay.origin + '/query'
                  for env in (mirror_env, owner_env)))
        key = gs.secret_hex(mirror_env.get('BUZZ_PRIVATE_KEY'), 'mirror')
        owner_key = gs.secret_hex(owner_env.get('BUZZ_PRIVATE_KEY'), 'owner')
        _need(gs._signer_pubkey(key) == binding['mirror_pubkey']
              and owner_key == self.relay.key and gs._signer_pubkey(owner_key) == self.relay.owner
              and gs._signer_pubkey(key) != self.relay.owner)
        fingerprint = self.store._approval_digest([(str(path), hashlib.sha256(raw).hexdigest())
            for path, raw in ((binding['config_path'], cfg_raw), (binding['agent_env_file'], agent_raw),
                              (cfg['mirror_env_file'], mirror_raw), (self.relay.owner_env_file, owner_raw))])
        return _Local(fingerprint, binding['mirror_pubkey'], self.relay.owner, key)

    async def _local(self, request_id, sql, previous=None, pin=None):
        local = await thread_call(self._read_local, sql)
        self._guard(request_id, sql, pin)
        if previous is not None: _need(local == previous)
        if pin is not None: _need(local.files_hash == pin.protectedfiles_hash)
        return local

    async def _query(self, filters):
        row = dict(filters, limit=257)
        events = await self.reader.read('query', filters=[row])
        _need(isinstance(events, list) and len(events) < 256
              and len({event['id'] for event in events}) == len(events))
        for event in events:
            _need(event['created_at'] <= self._now() + gs.RELAY_CLOCK_SKEW_SECONDS)
            for name, expected in filters.items():
                if name == 'kinds': _need(event['kind'] in expected)
                elif name == 'authors': _need(event['pubkey'] in expected)
                elif name == 'ids': _need(event['id'] in expected)
                elif name == '#d': _need(any(tag[1] in expected for tag in authority.tags(event, 'd') if len(tag) == 2))
                else: raise _Pending()
        return events

    async def _snapshot(self, request_id, sql, local, pin=None):
        decision, _, binding = sql
        profiles = await self._query({'kinds': [0], 'authors': [decision.agent_pubkey]})
        self._guard(request_id, sql, pin)
        policies = await self._query({'kinds': [30177]})
        self._guard(request_id, sql, pin)

        def candidates():
            mirrors = {local.mirror}
            for policy in policies:
                tag = authority.tags(policy, 'd')
                _need(len(tag) == 1 and len(tag[0]) == 2 and approval._hex(tag[0][1]))
                body = authority.policy_body(policy, tag[0][1])
                if isinstance(body.get('feishu'), dict) and body['feishu'].get('mirror') is True:
                    mirrors.add(tag[0][1])
            _need(len(mirrors) < 256)
            return sorted(mirrors)

        mirrors = await thread_call(candidates)
        self._guard(request_id, sql, pin)
        mirror_profiles = await self._query({'kinds': [0], 'authors': mirrors})
        self._guard(request_id, sql, pin)
        rosters = await self._query({'kinds': [39002], 'authors': [self.relay.relay_pubkey], '#d': [binding['channel_id']]})
        self._guard(request_id, sql, pin)
        now = self._now()

        def validate():
            mirror_policy = authority.latest([event for event in policies if event['pubkey'] == local.owner
                and authority.tags(event, 'd') == [['d', local.mirror]]])
            _need(mirror_policy is not None)
            body = authority.policy_body(mirror_policy, local.mirror)
            feishu = body.get('feishu')
            _need(isinstance(feishu, dict) and isinstance(feishu.get('bindings'), list))
            entries = [entry for entry in feishu['bindings'] if isinstance(entry, dict)
                and (entry.get('channel'), entry.get('chat_ref')) == (binding['channel_id'], decision.chat_ref)]
            _need(len(entries) == 1)
            scope = approval.ApprovalScope(decision.agent_pubkey, decision.agent_owner_pubkey, decision.app_id,
                binding['channel_id'], decision.chat_ref, local.mirror, local.owner, entries[0]['claimed_at'])
            own_policy = authority.latest([event for event in policies if event['pubkey'] == decision.agent_owner_pubkey
                and authority.tags(event, 'd') == [['d', decision.agent_pubkey]]])
            context = approval.ProofContext(scope, self.relay.relay_pubkey, authority.latest(profiles), own_policy,
                tuple(mirror_profiles), tuple(policies), authority.latest(rosters), now, complete=True)
            # Completeness is asserted ONLY here, after every bounded strict
            # actual query; callers cannot supply a hand-picked proof context.
            _need(approval.sync_app(context).app_id == binding['sync_app_id'])
            _need(scope.claimed_at <= decision.decision_at)
            if pin is not None:
                _need(all(getattr(scope, key) == getattr(pin, key) for key in approval.ApprovalScope.__dataclass_fields__))
            return context

        context = await thread_call(validate)
        self._guard(request_id, sql, pin)
        return context

    async def _new_pin(self, request_id, sql, local, context):
        decision, plan, _ = sql
        body = dict(version=1, decision='approve', **asdict(decision),
                    channel_id=context.scope.channel_id, mirror_pubkey=local.mirror,
                    mirror_owner_pubkey=local.owner, claimed_at=context.scope.claimed_at)
        content = json.dumps(body, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
        content_hash = hashlib.sha256(content.encode()).hexdigest()
        tags = [['t', approval.PREFIX], ['h', context.scope.channel_id], ['p', decision.agent_pubkey],
                ['d', approval.PREFIX + ':' + content_hash]]
        event = await thread_call(gs.sign_event, local.key, 30078, tags, content, self._now())
        self._guard(request_id, sql)
        await thread_call(approval.verify, event, context)
        self._guard(request_id, sql)
        values = {key: value for key, value in body.items() if key not in ('version', 'decision')}
        values.update(binding_id=self.binding_id, event_created_at=event['created_at'], event_id=event['id'],
                      signature=event['sig'], content_hash=content_hash, protectedfiles_hash=local.files_hash,
                      plan_hash=self.store._approval_digest(plan))
        values['scope_hash'] = self.store._approval_digest({key: values[key] for key in ('binding_id',
            'agent_pubkey', 'agent_owner_pubkey', 'app_id', 'channel_id', 'chat_ref', 'mirror_pubkey',
            'mirror_owner_pubkey', 'claimed_at')})
        return ApprovalPublicationPin(**values)

    def _admit(self, loop, task, request_id, sql, pin):
        """Bounded owning-loop SQL gate immediately before the actual HTTP call."""
        settled, expired = threading.Event(), threading.Event()
        accepted = False
        def check():
            nonlocal accepted
            if expired.is_set(): return
            try:
                _need(not task.done() and not task.cancelling())
                self._guard(request_id, sql, pin)
                accepted = True
            except Exception: pass
            finally: settled.set()
        loop.call_soon_threadsafe(check)
        if not settled.wait(5): expired.set(); raise _Pending()
        _need(accepted)

    async def _post(self, request_id, sql, local, pin, context):
        event = self.store._approval_event(pin)
        profile = authority.latest([row for row in context.mirror_profiles if row['pubkey'] == local.mirror])
        auth = json.dumps(authority.tags(profile, 'auth')[0], separators=(',', ':'))
        loop, task = asyncio.get_running_loop(), asyncio.current_task()
        def http(url, headers, timeout, body=None):
            # Own protected file/key proof after executor queuing, followed by
            # final SQL on the owner loop. No SQL is used by this IO thread.
            _need(self._read_local(sql) == local)
            self._admit(loop, task, request_id, sql, pin)
            return self.relay.http(url, headers, timeout, body=body)
        await thread_call(gs.publish_signed_event, self.relay.origin, local.key, event, http,
                          datetime.fromtimestamp(self._now(), timezone.utc), auth_tag=auth)
        self._guard(request_id, sql, pin)

    async def _execute(self, request_id):
        sql = self._sql(request_id)
        previous = self.store.approval_publication(request_id)
        pin = previous.pin if previous else None
        local = await self._local(request_id, sql, pin=pin)
        context = await self._snapshot(request_id, sql, local, pin)
        local = await self._local(request_id, sql, previous=local, pin=pin)
        if previous is None:
            pin = await self._new_pin(request_id, sql, local, context)
            local = await self._local(request_id, sql, previous=local, pin=pin)
            # Commit UNKNOWN before any physical POST. Crash here leaves one
            # immutable pin; future invocations are GET-only, even reserved.
            with self.store.transaction():
                self._guard(request_id, sql, pin)
                reservation = self.store.reserve_approval_publication(pin, now=self._now())
                created = reservation.created
                if created: _need(self.store.mark_approval_unknown(request_id, pin.event_id, now=self._now()))
            if created:
                context = await self._snapshot(request_id, sql, local, pin)
                local = await self._local(request_id, sql, previous=local, pin=pin)
                await self._post(request_id, sql, local, pin, context)
        event = self.store._approval_event(pin)
        records = await self._query({'kinds': [30078], 'authors': [pin.mirror_pubkey], 'ids': [pin.event_id]})
        self._guard(request_id, sql, pin)
        _need(len(records) == 1 and records[0] == event)
        # Finish with fresh current signed authority, not the pre-POST snapshot.
        context = await self._snapshot(request_id, sql, local, pin)
        await thread_call(approval.verify, records[0], context)
        self._guard(request_id, sql, pin)
        await self._local(request_id, sql, previous=local, pin=pin)
        self._guard(request_id, sql, pin)
        _need(self.store.ack_approval_publication(request_id, pin.event_id,
                  self.store._approval_digest(event), now=self._now()))
        # Final ACK and return are synchronous; no IO or awaits follow ACK.
        return PublicationResult('verified', pin.event_id, pin.content_hash)

    async def publish(self, request_id):
        lock = self._locks.setdefault(request_id, asyncio.Lock())
        await lock.acquire()
        try:
            return await self._execute(request_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            return PublicationResult('pending', notice=NOTICE)
        finally:
            lock.release()
