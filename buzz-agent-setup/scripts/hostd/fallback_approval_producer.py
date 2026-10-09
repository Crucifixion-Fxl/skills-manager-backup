"""Publish one foreign-subject card decision through owned local relay identities.

No foreign files, effects, grants or runtime are used. Membership and approval
have separate immutable metadata pins. Existing pins are always GET-only,
including reserved/unknown/acked pins and policy refusals. SQL stays on its loop.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import time

import buzz_feishu_group_sync as gs
import recovery_authority as authority
from . import agent_catalog, remote_approval as approval
from .async_io import thread_call
from .fallback_discovery import FallbackDiscoverer
from .fallback_onboarding import FallbackOnboarding
from .join_cards import BotCards
from .join_effects import Nip98Relay, legacy
from .safety import read_owned
from .signed_reads import SignedReader
from .store import Store, ident

NOTICE = ('兜底批准的成员与签名记录尚未完成核验，保留待处理。怎么解决：检查原始 owner 卡片决定、本机同步应用和镜像、当前签名策略与完整成员；未知发送只读回原事件，不重复发布。'
          '\n复制给 AI：帮我核查 hostd 兜底批准的原事件与当前授权；不要输出凭据或人员资料，也不要把记录已核验当作其他机器已开通。')


class _Pending(ValueError):
    def __init__(self): super().__init__(NOTICE)


def _need(value):
    if not value: raise _Pending()


@dataclass(frozen=True)
class PublicationResult:
    status: str
    event_id: str = ''
    content_hash: str = ''
    notice: str = ''


@dataclass(frozen=True)
class _Keys:
    files_hash: str
    mirror_key: str = field(repr=False)


@dataclass(frozen=True)
class _Snapshot:
    context: approval.ProofContext = field(repr=False)
    mirror_profile: dict = field(repr=False)
    member: bool
    add_allowed: bool


class FallbackApprovalProducer:
    def __init__(self, store, discoverer, issuer_clients, relay, *, catalog_path, legacy_join_path,
                 clock=lambda: int(time.time())):
        _need(isinstance(store,Store) and isinstance(discoverer,FallbackDiscoverer)
              and discoverer.store is store and discoverer.clients is issuer_clients
              and isinstance(relay,Nip98Relay) and discoverer.relay is relay)
        self.store,self.discoverer,self.clients,self.relay,self.clock = store,discoverer,issuer_clients,relay,clock
        self.catalog_path,self.legacy_join_path = catalog_path,legacy_join_path
        self.cards = FallbackOnboarding(store,discoverer,issuer_clients,relay,
            catalog_path=catalog_path,legacy_join_path=legacy_join_path,clock=clock)
        self.reader,self._locks = SignedReader(relay),{}

    def _now(self):
        now = self.clock()
        _need(type(now) is int and 0 <= now < 2**63)
        return now

    def _sql(self, request_id):
        facts = self.store._fallback_outward_facts(request_id)
        row = self.store.join_request(request_id)
        binding = self.discoverer._sql(facts['binding_id'])
        return facts,row,binding

    def _guard(self, request_id, sql, pin=None):
        _need(self._sql(request_id)==sql)
        if pin is not None: _need(self.store._fallback_outward_current(pin))

    def _read_keys(self, binding, files_hash, catalog_hash):
        """Only actual local mirror and owner files; never a subject path."""
        cfg = approval._json(read_owned(binding['config_path']).decode())
        _need(Path(cfg['people_api']['signer_env_file'])==self.relay.owner_env_file)
        owner = legacy.parse_env(read_owned(self.relay.owner_env_file).decode())
        mirror = legacy.parse_env(read_owned(cfg['mirror_env_file']).decode())
        key = gs.secret_hex(mirror.get('BUZZ_PRIVATE_KEY'),'mirror')
        _need(gs.secret_hex(owner.get('BUZZ_PRIVATE_KEY'),'owner')==self.relay.key
              and gs._signer_pubkey(key)==binding['mirror_pubkey'] and key!=self.relay.key
              and all(gs.relay_query_url(env.get('BUZZ_RELAY_URL'))==self.relay.origin+'/query'
                      for env in (owner,mirror)))
        return _Keys(self.store._approval_digest([files_hash,catalog_hash]),key)

    async def _query(self, request_id, sql, filters, pin=None):
        events = await self.reader.read('query',filters=[dict(filters,limit=257)])
        self._guard(request_id,sql,pin)
        _need(isinstance(events,list) and len(events)<256 and len({e['id'] for e in events})==len(events))
        for event in events:
            _need(event['created_at']<=self._now()+gs.RELAY_CLOCK_SKEW_SECONDS)
            for name,expected in filters.items():
                if name=='kinds': _need(event['kind'] in expected)
                elif name=='authors': _need(event['pubkey'] in expected)
                elif name=='ids': _need(event['id'] in expected)
                elif name=='#d': _need(authority.tags(event,'d') in [[['d',v]] for v in expected])
                else: raise _Pending()
        return events

    async def _snapshot(self, request_id, sql, local, pin=None):
        facts,_,binding = sql
        policies = await self._query(request_id,sql,{'kinds':[30177]},pin)
        def subjects():
            pubs = {facts['subject_pubkey'],binding['mirror_pubkey'],local.issuer}
            for event in policies:
                tags = authority.tags(event,'d')
                _need(len(tags)==1 and len(tags[0])==2 and approval._hex(tags[0][1]))
                approval._policy(event,tags[0][1],self._now())
                pubs.add(tags[0][1])
            _need(len(pubs)<=256)
            return sorted(pubs)
        pubs = await thread_call(subjects)
        self._guard(request_id,sql,pin)
        profiles = await self._query(request_id,sql,{'kinds':[0],'authors':pubs},pin)
        rosters = await self._query(request_id,sql,{'kinds':[39002],'authors':[self.relay.relay_pubkey],
            '#d':[binding['channel_id']]},pin)
        add_policies = await self._query(request_id,sql,{'kinds':[10100],'authors':[facts['subject_pubkey']]},pin)
        now = self._now()
        def validate():
            groups = {}
            for profile in profiles:
                approval._owner(profile,profile['pubkey'],now)
                groups.setdefault(profile['pubkey'],[]).append(profile)
            heads = {pub:authority.latest(rows) for pub,rows in groups.items()}
            _need(all(pub in heads for pub in (local.issuer,facts['subject_pubkey'],facts['mirror_pubkey'])))
            subject = heads[facts['subject_pubkey']]
            _need(approval._owner(subject,facts['subject_pubkey'],now)==facts['subject_owner_pubkey'])
            subject_policy = authority.latest([p for p in policies if p['pubkey']==facts['subject_owner_pubkey']
                and authority.tags(p,'d')==[['d',facts['subject_pubkey']]]])
            _need(subject_policy is not None)
            feishu = approval._policy(subject_policy,facts['subject_pubkey'],now).get('feishu')
            _need(isinstance(feishu,dict) and feishu.get('app_id')==facts['subject_app_id']
                  and feishu.get('mirror') is not True)
            issuer_profile = heads[local.issuer]
            issuer_owner = approval._owner(issuer_profile,local.issuer,now)
            issuer_policy = authority.latest([p for p in policies if p['pubkey']==issuer_owner
                and authority.tags(p,'d')==[['d',local.issuer]]])
            _need(issuer_policy is not None and rosters)
            for roster in rosters:
                approval._event(roster,39002,now); authority.membership(roster,binding['channel_id'])
            roster = authority.latest(rosters)
            _need(now-gs.CLAIM_LEASE_SECONDS<=roster['created_at']<=now+gs.RELAY_CLOCK_SKEW_SECONDS)
            roles = authority.membership(roster,binding['channel_id'])
            _need(roles.get(self.relay.owner) in ('owner','admin'))
            scope = approval.ApprovalScope(facts['subject_pubkey'],facts['subject_owner_pubkey'],facts['subject_app_id'],
                facts['channel_id'],facts['chat_ref'],facts['mirror_pubkey'],facts['mirror_owner_pubkey'],facts['claimed_at'])
            issuer_scope = approval.ApprovalScope(local.issuer,issuer_owner,facts['issuer_app_id'],
                scope.channel_id,scope.chat_ref,scope.mirror_pubkey,scope.mirror_owner_pubkey,scope.claimed_at)
            issuer_context = approval.ProofContext(issuer_scope,self.relay.relay_pubkey,issuer_profile,issuer_policy,
                tuple(profiles),tuple(policies),roster,now,complete=True)
            # Completeness only follows the actual bounded strict queries.
            _need(approval.sync_app(issuer_context).app_id==facts['issuer_app_id'])
            _need(scope.claimed_at<=facts['decision_at'])
            context = approval.ProofContext(scope,self.relay.relay_pubkey,subject,subject_policy,
                tuple(profiles),tuple(policies),roster,now,complete=True)
            member = roles.get(scope.agent_pubkey)=='bot'
            if member: _need(approval.sync_app(context).app_id==facts['issuer_app_id'])
            # Complete absence is not a signed DB policy proof. Hold rather
            # than infer the server's permissive default from missing events.
            _need(add_policies)
            for event in add_policies:
                approval._event(event,10100,now)
                policy = approval._json(event['content'])
                _need(event['pubkey']==scope.agent_pubkey and event['tags']==[]
                      and set(policy)=={'channel_add_policy'}
                      and policy['channel_add_policy'] in ('anyone','owner_only','nobody'))
            head = authority.latest(add_policies)
            policy = approval._json(head['content'])['channel_add_policy']
            allowed = policy=='anyone' or (policy=='owner_only' and self.relay.owner==scope.agent_owner_pubkey)
            mirror_profile = heads[scope.mirror_pubkey]
            _need(approval._owner(mirror_profile,scope.mirror_pubkey,now)==self.relay.owner)
            return _Snapshot(context,mirror_profile,member,allowed)
        value = await thread_call(validate)
        self._guard(request_id,sql,pin)
        return value

    def _native_card(self, sql):
        facts,row,binding = sql
        client = self.clients[facts['issuer_app_id']]
        _need(client.identity()==(facts['issuer_app_id'],''))
        view = client.message_view(row['card_message_id'],'union_id')
        _need(BotCards._matches(view,row,row['card_generation']))
        listing = client.member_listing(binding['chat_id'],'union_id')
        _need(isinstance(listing,gs.MemberListing) and listing.complete is True
              and facts['issuer_app_id'] in listing.bots and facts['subject_app_id'] in listing.bots)
        return frozenset(listing.users)

    async def _fresh(self, request_id, sql, previous=None, pin=None):
        started = self._now()
        facts,row,binding = sql
        marker = self.store.fallback_provenance(request_id)
        evidence,own = await self.cards._fresh(facts['binding_id'],facts['subject_pubkey'],marker)
        self._guard(request_id,sql,pin)
        _need(not own)
        local = await self.discoverer._local(binding)
        self._guard(request_id,sql,pin)
        keys = await thread_call(self._read_keys,binding,local.files_hash,evidence.catalog_hash)
        self._guard(request_id,sql,pin)
        if previous is not None: _need(keys==previous)
        if pin is not None: _need(keys.files_hash==pin.protectedfiles_hash)
        users = await thread_call(self._native_card,sql)
        self._guard(request_id,sql,pin)
        identity = await self.cards._identity(row)
        owner_union = await identity.owner_union(facts['subject_owner_pubkey'],facts['issuer_app_id'],now=self._now())
        self._guard(request_id,sql,pin)
        _need(owner_union in users)
        snapshot = await self._snapshot(request_id,sql,local,pin)
        catalog = await thread_call(agent_catalog.load,self.catalog_path,legacy_join_path=self.legacy_join_path)
        self._guard(request_id,sql,pin)
        catalog_hash = hashlib.sha256((catalog.catalog_sha256+':'+catalog.legacy_join_sha256).encode()).hexdigest()
        _need(catalog_hash==evidence.catalog_hash)
        await self.discoverer._local(binding,local)
        self._guard(request_id,sql,pin)
        now = self._now()
        _need(started<=now<=started+30 and evidence.checked_at<=now<evidence.valid_until)
        return keys,snapshot

    async def _event(self, request_id, stage, sql, keys):
        facts,_,_ = sql
        if stage=='member':
            event = await thread_call(self.relay.event,9000,
                [['h',facts['channel_id']],['p',facts['subject_pubkey']],['role','bot']],'',self._now())
        else:
            body = dict(version=1,decision='approve',request_id=request_id,agent_pubkey=facts['subject_pubkey'],
                agent_owner_pubkey=facts['subject_owner_pubkey'],app_id=facts['subject_app_id'],
                **{name:facts[name] for name in ('channel_id','chat_ref','mirror_pubkey','mirror_owner_pubkey','claimed_at',
                   'request_created_at','request_deadline','card_generation','card_message_sha256','decision_event_sha256','decision_at')})
            content = json.dumps(body,sort_keys=True,separators=(',',':'),ensure_ascii=False)
            content_hash = hashlib.sha256(content.encode()).hexdigest()
            event = await thread_call(gs.sign_event,keys.mirror_key,30078,
                [['t',approval.PREFIX],['h',facts['channel_id']],['p',facts['subject_pubkey']],
                 ['d',approval.PREFIX+':'+content_hash]],content,self._now())
        self._guard(request_id,sql)
        return event

    async def _stage(self, request_id, stage, sql, keys, snapshot):
        record = self.store.fallback_outward(request_id,stage)
        if record is None:
            _need(snapshot.member if stage=='approval' else snapshot.add_allowed)
            event = await self._event(request_id,stage,sql,keys)
            keys,snapshot = await self._fresh(request_id,sql,keys)
            _need(snapshot.member if stage=='approval' else snapshot.add_allowed)
            with self.store.transaction():
                self._guard(request_id,sql)
                reserved = self.store.reserve_fallback_outward(request_id,stage,event,sql[0]['scope_hash'],keys.files_hash,now=self._now())
                record = reserved.record
                if reserved.created:
                    _need(self.store.mark_fallback_outward_unknown(request_id,stage,event['id'],now=self._now()))
            if reserved.created:
                # Renewal after committed UNKNOWN, before any external POST.
                keys,snapshot = await self._fresh(request_id,sql,keys,record.pin)
                _need(snapshot.member if stage=='approval' else snapshot.add_allowed)
                if stage=='member': await thread_call(self.relay.publish,event)
                else:
                    auth = json.dumps(authority.tags(snapshot.mirror_profile,'auth')[0],separators=(',',':'))
                    await thread_call(self.relay.publish_as,event,keys.mirror_key,auth)
                self._guard(request_id,sql,record.pin)
        event = self.store._fallback_outward_event(record.pin)
        _need(keys.files_hash==record.pin.protectedfiles_hash)
        rows = await self._query(request_id,sql,{'ids':[record.pin.event_id],
            'authors':[record.pin.signer_pubkey],'kinds':[event['kind']]},record.pin)
        _need(len(rows)==1 and rows[0]==event)
        keys,snapshot = await self._fresh(request_id,sql,keys,record.pin)
        _need(snapshot.member)
        if stage=='approval':
            await thread_call(approval.verify,event,snapshot.context)
            self._guard(request_id,sql,record.pin)
            # Reap and check files after verification too; no post-ACK await.
            keys,snapshot = await self._fresh(request_id,sql,keys,record.pin)
            _need(snapshot.member)
        self._guard(request_id,sql,record.pin)
        _need(self.store.ack_fallback_outward(request_id,stage,record.pin.event_id,
            self.store._approval_digest(event),now=self._now()))
        return record.pin,keys,snapshot

    async def _execute(self, request_id):
        sql = self._sql(request_id)
        keys,snapshot = await self._fresh(request_id,sql)
        if self.store.fallback_outward(request_id,'member') is not None or not snapshot.member:
            _,keys,snapshot = await self._stage(request_id,'member',sql,keys,snapshot)
        _need(snapshot.member)
        pin,_,_ = await self._stage(request_id,'approval',sql,keys,snapshot)
        return PublicationResult('record_verified',pin.event_id,pin.content_hash)

    async def run(self, request_id):
        lock = None
        try:
            ident(request_id)
            lock = self._locks.setdefault(request_id,asyncio.Lock())
            await lock.acquire()
        except asyncio.CancelledError: raise
        except Exception: return PublicationResult('pending',notice=NOTICE)
        try:
            return await self._execute(request_id)
        except asyncio.CancelledError: raise
        except Exception: return PublicationResult('pending',notice=NOTICE)
        finally: lock.release()
