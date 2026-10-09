"""Explicit protected factory and card-only discovery; no guessed live configuration.

Root supplies the shared Scheduler and real dynamic registrar. Feed input is trusted
SDK provenance from root; this module still checks app and typed metadata. Directory
reads are restricted to locally registered, owner-authorized binding channels. An
absent directory or ambiguous claim is pending, never an authorization substitute.
"""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import stat
import threading
import time

import buzz_feishu_group_sync as gs
import buzz_agent_join_requests as legacy
import recovery_authority as authority
from . import agent_catalog
from .async_io import thread_call
from .bot_clients import BotLarkCli, _trusted_relay
from .join_cards import BotCards
from .join_effects import AgentSpec, AgentRuntime, JoinEffects, Nip98Relay, BotMembership, MemberPreparation
from .onboarding import Coordinator, IdentityResolver, Invite, Operator, PeopleBindings, card_from_feed, QUEUED
from .fallback_discovery import FallbackDiscoverer
from .fallback_onboarding import FallbackOnboarding
from .fallback_approval_producer import FallbackApprovalProducer
from .safety import read_owned
from .store import hexid, ident, localpath, stamp

NOTICE = ('接入发现暂时无法安全确认，申请保持待核验。怎么解决：检查明确配置的 owner 身份、relay 签名固定值、旧任务排除、完整 bot 群列表和频道认领；人员绑定不足时先修复已授权频道的 union_id 绑定。'
          '\n复制给 AI：帮我核查 hostd 接入发现、卡片当前应用身份、完整分页和已授权频道人员证据；不要输出凭据、消息正文、个人资料或卡片 token。')

class RuntimeErrorNotice(ValueError):
    pass


def _same_binding_authority(current, captured):
    # Worker liveness updates are not authorization changes. Every other SQL
    # field remains part of the post-I/O authority comparison, including status.
    if not isinstance(current,dict) or not isinstance(captured,dict):return False
    return ({k:v for k,v in current.items() if k!='heartbeat_at'} ==
            {k:v for k,v in captured.items() if k!='heartbeat_at'})


def _same_people_authority(current, captured):
    # People evidence needs the existing grant, not a successful worker round.
    # This exception is deliberately not shared with discovery/issuer checks.
    if (not isinstance(current,dict) or not isinstance(captured,dict)
            or current.get('status') not in ('active','degraded')
            or captured.get('status') not in ('active','degraded')):return False
    return _same_binding_authority(dict(current,status='active'),dict(captured,status='active'))


@dataclass(frozen=True)
class RuntimeConfig:
    version: int
    owner_env_file: str
    relay_url: str
    relay_pubkey: str
    template_config: str
    binding_dir: str
    legacy_join_path: str
    catalog_path: str
    trusted_relays: tuple[str, ...] = ()
    remote_link_base: str = ''

    def __post_init__(self):
        try:
            if type(self.version) is not int or self.version != 1 or not isinstance(self.trusted_relays, tuple): raise ValueError
            for value in (self.owner_env_file,self.template_config,self.binding_dir,self.legacy_join_path,self.catalog_path):
                localpath(value)
                if any(ord(c)<32 or ord(c)==127 for c in str(value)): raise ValueError
            hexid(self.relay_pubkey)
            _trusted_relay(self.relay_url,self.trusted_relays)
            if any(not isinstance(value,str) for value in self.trusted_relays): raise ValueError
            if self.remote_link_base != '':
                from types import SimpleNamespace
                from .remote_mapping import RemoteMappingContext
                RemoteMappingContext._link_origin(SimpleNamespace(link_base=self.remote_link_base))
        except Exception:
            raise RuntimeErrorNotice(NOTICE) from None

    @classmethod
    def load(cls,path):
        """Optional protected startup file; every field remains explicit."""
        try:
            def unique(pairs):
                result={}
                for key,value in pairs:
                    if key in result: raise ValueError
                    result[key]=value
                return result
            value=json.loads(read_owned(path),object_pairs_hook=unique)
            if not isinstance(value,dict):raise ValueError
            if 'trusted_relays' in value:
                if not isinstance(value['trusted_relays'],list):raise ValueError
                value['trusted_relays']=tuple(value['trusted_relays'])
            return cls(**value)
        except Exception:
            raise RuntimeErrorNotice(NOTICE) from None


@dataclass(frozen=True)
class Discovery:
    status: str
    app_id: str
    agent_id: str
    chat_id: str
    binding_id: str | None = None

    def __post_init__(self):
        if self.status not in ('bound','unbound','uncertain'):raise RuntimeErrorNotice(NOTICE)
        ident(self.app_id,'cli_');hexid(self.agent_id);ident(self.chat_id,'oc_')
        if self.binding_id is not None:ident(self.binding_id)
        if (self.status=='bound') != (self.binding_id is not None):raise RuntimeErrorNotice(NOTICE)


class _LaneClient(BotLarkCli):
    """Per-call lanes, without mutating the shared app client from worker threads."""
    def __init__(self,*args,store,http_pool=None,**kwargs):
        if http_pool is not None:kwargs['http_pool']=http_pool
        super().__init__(*args,**kwargs);self.http_pool=http_pool;self.store=store
        self.message_chats={};self.message_lock=threading.Lock()

    def remember(self,message_id,chat_id):
        ident(message_id,'om_');ident(chat_id,'oc_')
        with self.message_lock:
            self.message_chats[message_id]=chat_id
            if len(self.message_chats)>2048:self.message_chats.pop(next(iter(self.message_chats)))

    def send_card(self,chat_id,card,key):
        message_id=super().send_card(chat_id,card,key)
        self.remember(message_id,chat_id)
        return message_id

    def call(self,what,args,*,full=False,cwd=None):
        chat=None
        if '--chat-id' in args:
            index=args.index('--chat-id')
            if index+1<len(args):chat=args[index+1]
        for value in args:
            if isinstance(value,str):
                matched=re.fullmatch(r'/open-apis/im/v1/chats/(oc_[A-Za-z0-9]+)(?:/members)?',value)
                if matched:chat=matched[1]
                matched=re.fullmatch(r'/open-apis/im/v1/messages/(om_[A-Za-z0-9]+)',value)
                if matched:
                    with self.message_lock:chat=self.message_chats.get(matched[1])
        if chat is not None:ident(chat,'oc_')
        extra={} if self.http_pool is None else {'http_pool':self.http_pool}
        lane=BotLarkCli(self.app_id,self.config_dir,self.data_dir,base_env=self.env,
                       runner=self.runner,scheduler=self.scheduler,chat_id=chat,priority=self.priority,**extra)
        return lane.call(what,args,full=full,cwd=cwd)


class OnboardingRuntime:
    def diagnostics(self):
        # Fixed metadata only: never expose chat/person IDs, event bodies,
        # callback values, tokens or exception text.
        return {'queued': len(self._queue), 'active': dict(getattr(self, '_active', {})),
                'agents': len(self.records), 'issuers': len(self._issuer_bindings),
                'last_identity_failure': dict(getattr(self, 'last_identity_failure', {}))}

    @classmethod
    async def create(cls,config,store,*,registrar,scheduler,http_pool=None,http=gs._http_get,
                     runner=subprocess.run,base_env=None,runtime_operations=None,clock=time.time,defer_issuers=False):
        """Build real adapters; only HTTP/CLI/process I/O may be replaced offline."""
        try:
            if not isinstance(config,RuntimeConfig) or scheduler is None or not callable(getattr(scheduler,'run',None)):raise ValueError
            if threading.current_thread() is not threading.main_thread():raise ValueError
            service=cls();service.config=config;service.store=store;service.clock=clock
            service.loop=asyncio.get_running_loop();service._closed=False;service._task=None
            service._queue=deque();service._wake=asyncio.Event();service._lock=asyncio.Lock();service.last_notice=''
            service.last_identity_failure={}
            service.http=http;service._issuer_bindings={};service._issuer_cursor='';service._fallback_cursor=''
            service._candidate_cursors={};service._recovery_binding_cursor=''
            service._approval_retry_at=0.;service._approval_retry_cursor='';service._approval_retry_due={}
            service._notice_retry_at=0.
            service._pending_own={};service._own_retry_at={};service._own_retry_cursor=''
            service._own_restores={};service._own_restore_cursor=''
            service._retry_clock=time.monotonic
            service._scheduler=scheduler;service._http_pool=http_pool
            service._base_env={} if base_env is None else base_env;service._runner=runner
            service._directory(config.binding_dir)
            service.catalog=await thread_call(agent_catalog.load,config.catalog_path,legacy_join_path=config.legacy_join_path)
            legacy_doc=json.loads(read_owned(config.legacy_join_path));template=json.loads(read_owned(config.template_config))
            if not isinstance(template,dict):raise ValueError
            state_dir=localpath(legacy_doc['state_dir'])
            service._state_dir=state_dir
            service.relay=Nip98Relay(config.relay_url,config.owner_env_file,config.relay_pubkey,http=http,trusted_relays=config.trusted_relays,clock=clock)
            if service.relay.owner != legacy_doc['owner_pubkey']:raise ValueError
            service.records={};service.clients={};specs={}
            candidates=[record for record in service.catalog.records
                if record.status=='own_bot_verified' and record.owner_pubkey==service.relay.owner]
            slots=asyncio.Semaphore(4)
            async def verify_record(record):
                async with slots:
                    try:await service._fresh(record);return True
                    except Exception:return False
            # Independent read-only proofs may overlap. Registration still
            # occurs deterministically after each candidate's complete proof.
            verified=await asyncio.gather(*(verify_record(record) for record in candidates))
            for record,valid in zip(candidates,verified):
                if not valid:
                    service._pending_own[record.name]=record
                    service.last_notice=NOTICE;continue
                service.records[record.app_id]=record
                service.clients[record.app_id]=_LaneClient(record.app_id,record.lark_config_dir,record.lark_data_dir,
                    store=store,http_pool=http_pool,base_env={} if base_env is None else base_env,runner=runner,scheduler=scheduler)
                specs[record.pubkey]=AgentSpec(record.pubkey,record.owner_pubkey,record.app_id,str(record.env_file),str(record.prompt_file),
                    str(record.responsible_config),record.unit,config.legacy_join_path,state_dir,config.binding_dir,record.app_id,
                    str(record.lark_config_dir),str(record.lark_data_dir),config.template_config)
                store.register_agent(record.pubkey,owner_pubkey=record.owner_pubkey,app_id=record.app_id,config_path=str(record.env_file),now=int(clock()))
            service.identity=IdentityResolver(service.clients,service._people_loader,service._profile_loader)
            service.cards=BotCards(service.clients)
            service.effects=JoinEffects(store,specs,service.relay,AgentRuntime(runtime_operations),registrar,clients=service.clients,clock=clock)
            service.coordinator=Coordinator(store,service.identity,service.cards,effects=service.effects,clock=clock)
            now=lambda:int(service.clock())
            service.fallback_discoverer=FallbackDiscoverer(store,service.clients,service.relay,clock=now)
            service.fallback_onboarding=FallbackOnboarding(store,service.fallback_discoverer,service.clients,service.relay,
                catalog_path=config.catalog_path,legacy_join_path=config.legacy_join_path,clock=now)
            service.fallback_producer=FallbackApprovalProducer(store,service.fallback_discoverer,service.clients,service.relay,
                catalog_path=config.catalog_path,legacy_join_path=config.legacy_join_path,clock=now)
            if not defer_issuers:await service._refresh_issuers()
            for row in store.join_requests():
                if row['callback_app_id'] in service.clients and row['card_message_id']:
                    service.clients[row['callback_app_id']].remember(row['card_message_id'],row['chat_id'])
            return service
        except Exception:
            raise RuntimeErrorNotice(NOTICE) from None

    async def _recover_own(self):
        """One original protected own candidate per pass; no failed proof is eligible."""
        if self._closed:return
        now=self._retry_clock()
        names=sorted(name for name in self._pending_own if self._own_retry_at.get(name,0)<=now)
        if not names:return
        name=next((name for name in names if name>self._own_retry_cursor),names[0])
        self._own_retry_cursor=name;self._own_retry_at[name]=now+60
        record=self._pending_own[name]
        try:
            await self._fresh(record)
            if self._closed:return
            registrar=self.effects.registrar
            if registrar is not None:registrar.check_own_profile(record)
            client=self.clients.get(record.app_id)
            profile=(str(record.lark_config_dir),str(record.lark_data_dir))
            if client is not None and (str(client.config_dir),str(client.data_dir))!=profile:raise ValueError
            if any(r.pubkey==record.pubkey and r.app_id!=record.app_id for r in self.records.values()):raise ValueError
            current=self.records.get(record.app_id)
            if current is not None and current!=record:raise ValueError
            client=client or _LaneClient(record.app_id,*profile,store=self.store,http_pool=self._http_pool,
                base_env=self._base_env,runner=self._runner,scheduler=self._scheduler)
            for row in self.store.join_requests():
                if row['callback_app_id']==record.app_id and row['card_message_id']:
                    client.remember(row['card_message_id'],row['chat_id'])
            spec=AgentSpec(record.pubkey,record.owner_pubkey,record.app_id,str(record.env_file),str(record.prompt_file),
                str(record.responsible_config),record.unit,self.config.legacy_join_path,self._state_dir,
                self.config.binding_dir,record.app_id,*profile,self.config.template_config)
            # No await between durable identity validation and all adapter maps.
            # JoinEffects owns copies; update them as well as shared card/identity maps.
            prior=self.store.conn.execute('SELECT status FROM agent WHERE pubkey=?',(record.pubkey,)).fetchone()
            if prior is not None and prior['status']!='active':raise ValueError
            self.store.register_agent(record.pubkey,owner_pubkey=record.owner_pubkey,app_id=record.app_id,
                config_path=str(record.env_file),now=int(self.clock()))
            self.records[record.app_id]=record;self.clients[record.app_id]=client
            self.effects.specs[record.pubkey]=spec;self.effects.clients[record.app_id]=client
            if registrar is not None:
                await registrar.own_admitted(record)
                if self._closed:return
                for row in self.store.join_requests():
                    if (row['agent_id'],row['callback_app_id'])==(record.pubkey,record.app_id) and row['status'] in {'approved','applied','done'}:
                        self._own_restores.setdefault(row['request_id'],0)
            self._pending_own.pop(name,None);self._own_retry_at.pop(name,None)
        except Exception:self.last_notice=NOTICE
        if (any(self._own_retry_at.get(name,0)<=self._retry_clock() for name in self._pending_own)
                or self._own_restores):self._wake.set()

    async def _restore_own_request(self):
        """One existing request dispatch only; never approval/effects replay."""
        if self._closed or self.effects.registrar is None:return
        now=self._retry_clock()
        names=sorted(name for name,due in self._own_restores.items() if due<=now)
        if not names:return
        name=next((name for name in names if name>self._own_restore_cursor),names[0])
        self._own_restore_cursor=name;self._own_restores[name]=now+60
        row=self.store.join_request(name)
        if row is None or row['status'] not in {'approved','applied','done'}:
            self._own_restores.pop(name,None);return
        try:
            result=await self.effects.registrar.restore(request_id=name)
            if result['registered']==1:self._own_restores.pop(name,None)
            else:self.last_notice=NOTICE
        except Exception:self.last_notice=NOTICE
        if any(due<=self._retry_clock() for due in self._own_restores.values()):self._wake.set()

    @staticmethod
    def _directory(value):
        path=Path(value);fd=os.open(path.anchor,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
        try:
            for part in path.parts[1:]:
                child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=fd)
                os.close(fd);fd=child
            info=os.fstat(fd)
            if info.st_uid!=os.geteuid() or stat.S_IMODE(info.st_mode)!=0o700:raise ValueError
        finally:os.close(fd)

    async def prepare_members(self,row,plan):
        """Root registrar provider: fresh directory + complete actual own-app roster.

        Only metadata IDs enter this typed receipt. Unknown ordinary users remain
        explicit pending; the approving owner's authenticated binding is required.
        """
        try:
            if not isinstance(row,dict) or not isinstance(plan,dict):raise ValueError
            current=self.store.join_request(row['request_id']);actual_plan=self.store.effect_plan(row['request_id'])
            if not current or current['status'] not in ('approved','applied') or plan!=actual_plan:raise ValueError
            if any(row.get(k)!=current[k] for k in ('request_id','agent_id','owner_pubkey','callback_app_id','chat_id','kind','binding_id','status')):raise ValueError
            app=current['callback_app_id'];record=self.records.get(app)
            if record is None or (record.pubkey,record.owner_pubkey)!=(current['agent_id'],current['owner_pubkey']):raise ValueError
            answer=await self._people_loader(app,int(self.clock()))
            listing=await thread_call(BotMembership(self.clients).listing,app,current['chat_id'])
            await self._current(record)
            if app not in listing.bots or self._closed or self.store.join_request(current['request_id'])!=current or self.store.effect_plan(current['request_id'])!=actual_plan:raise ValueError
            reverse={union:pk for pk,union in answer.union_ids.items()}
            people=tuple(sorted((reverse[union],union) for union in listing.users if union in reverse))
            pending=tuple(sorted(union for union in listing.users if union not in reverse))
            if record.owner_pubkey not in {pk for pk,_ in people}:raise ValueError
            checked=int(self.clock())
            digest=hashlib.sha256(json.dumps([app,current['chat_id'],checked,people,pending],separators=(',',':')).encode()).hexdigest()
            return MemberPreparation(app,current['chat_id'],checked,True,people,pending,digest)
        except Exception:raise RuntimeErrorNotice(NOTICE) from None

    @property
    def eligible_apps(self):
        active={b['binding_id']:b for b in self.store.bindings() if b['status']=='active'}
        issuers={app for binding,(app,profile) in self._issuer_bindings.items()
            if binding in active and (active[binding]['sync_app_id'],active[binding]['config_dir'],active[binding]['data_dir'])==(app,*profile)}
        return tuple(sorted(set(self.records)|issuers))

    @property
    def app_profiles(self):
        return {app:(str(self.clients[app].config_dir),str(self.clients[app].data_dir))
                for app in self.eligible_apps if app in self.clients}

    def _binding_profile(self,binding):
        # Protected local config only. Never inspect a public subject's files.
        cfg=json.loads(read_owned(binding['config_path']))
        if (cfg.get('channel_id'),cfg.get('chat_id'),cfg.get('mirror_pubkey')) != (
                binding['channel_id'],binding['chat_id'],binding['mirror_pubkey']):raise ValueError
        selected=cfg.get('agents',{}).get(cfg.get('desk_pubkey'))
        if not isinstance(selected,dict) or selected.get('app_id')!=binding['sync_app_id']:raise ValueError
        profile=(selected.get('lark_config_dir'),selected.get('lark_data_dir'))
        if profile!=(binding['config_dir'],binding['data_dir']):raise ValueError
        for path in profile:localpath(path)
        return profile

    async def _refresh_issuers(self, *, limit=128):
        bindings=[b for b in self.store.bindings() if b['status']=='active']
        active={b['binding_id'] for b in bindings}
        self._issuer_bindings={key:value for key,value in self._issuer_bindings.items() if key in active}
        self._candidate_cursors={key:value for key,value in self._candidate_cursors.items() if key in active}
        # Bounded, rotating admission; complete snapshots remain mandatory for
        # each binding. Cached entries never authorize an adapter operation.
        ordered=[b for b in bindings if b['binding_id']>self._issuer_cursor]
        if not ordered:ordered=bindings;self._issuer_cursor=''
        for binding in ordered[:limit]:
            await self._foreground()
            if self._closed:return
            name,app=binding['binding_id'],binding['sync_app_id']
            self._issuer_cursor=name
            try:
                profile=await thread_call(self._binding_profile,binding)
                if self._closed:raise ValueError
                candidate=self.clients.get(app)
                if candidate is not None:
                    if (str(candidate.config_dir),str(candidate.data_dir))!=profile:raise ValueError
                else:
                    candidate=_LaneClient(app,*profile,store=self.store,http_pool=self._http_pool,
                        base_env=self._base_env,runner=self._runner,scheduler=self._scheduler)
                trial=dict(self.clients);trial[app]=candidate
                verified=FallbackDiscoverer(self.store,trial,self.relay,clock=lambda:int(self.clock()))
                result=await verified.discover(name)
                if self._closed or result.status!='complete':raise ValueError
                current={b['binding_id']:b for b in self.store.bindings()}.get(name)
                if not _same_binding_authority(current,binding) or await thread_call(self._binding_profile,binding)!=profile:raise ValueError
                if self._closed or not _same_binding_authority({b['binding_id']:b for b in self.store.bindings()}.get(name),binding):raise ValueError
                self.clients[app]=candidate;self._issuer_bindings[name]=(app,profile)
            except Exception:
                self._issuer_bindings.pop(name,None);self.last_notice=NOTICE
        eligible=set(self.records)|{app for app,_ in self._issuer_bindings.values()}
        for app in set(self.clients)-eligible:self.clients.pop(app,None)

    async def _fallback_binding(self,binding_id,event_id):
        if self._closed:return
        result=await self.fallback_discoverer.discover(binding_id)
        if self._closed:return
        if result.status!='complete':self.last_notice=NOTICE;return
        # Reader caps are strict. A pass never turns a partial candidate set into
        # permission; all candidates use their own fresh card preflight again.
        cursor=self._candidate_cursors.get(binding_id,'')
        candidates=[c for c in result.candidates if c.subject_pubkey>cursor]
        if not candidates:candidates=result.candidates
        for candidate in candidates[:1]:
            if self._closed:return
            self._candidate_cursors[binding_id]=candidate.subject_pubkey
            answer=await self.fallback_onboarding.request(binding_id,candidate.subject_pubkey,event_id)
            if answer.status=='pending':self.last_notice=NOTICE

    async def _fallback_card(self,decision):
        if self._closed:return
        row=self.store.join_request(decision.request_id)
        if row is None or row['status']!='requested':return
        answer=await self.fallback_onboarding.decide(decision)
        if self._closed:return
        if answer.status=='approved':
            published=await self.fallback_producer.run(decision.request_id)
            if published.status!='record_verified':self.last_notice=NOTICE
        else:self.last_notice=NOTICE

    async def _recover_fallback(self):
        if self._closed:return
        await self._refresh_issuers(limit=1)
        if self._closed:return
        # A SQL keyset cursor bounds each pass without starving old unresolved
        # records behind terminal history. These are metadata IDs, never bodies.
        rows=self.store.conn.execute("""SELECT j.request_id FROM join_request j
            JOIN fallback_request f ON f.request_id=j.request_id
            WHERE j.request_id>? AND j.status IN ('requested','approved')
            ORDER BY j.request_id LIMIT 1""",(self._fallback_cursor,)).fetchall()
        if not rows:
            self._fallback_cursor=''
            rows=self.store.conn.execute("""SELECT j.request_id FROM join_request j
                JOIN fallback_request f ON f.request_id=j.request_id
                WHERE j.status IN ('requested','approved') ORDER BY j.request_id LIMIT 1""").fetchall()
        for item in rows:
            await self._foreground()
            if self._closed:return
            request_id=item[0];self._fallback_cursor=request_id
            row=self.store.join_request(request_id);marker=self.store.fallback_provenance(request_id)
            if row is None or marker is None:continue
            if marker.binding_id not in self._issuer_bindings:self.last_notice=NOTICE;continue
            if int(self.clock())>=row['deadline'] and row['status']=='requested':self.last_notice=NOTICE;continue
            if row['status']=='approved':
                answer=await self.fallback_producer.run(request_id)
                if answer.status!='record_verified':self.last_notice=NOTICE
            elif not row['card_message_id']:
                answer=await self.fallback_onboarding.request(marker.binding_id,marker.subject_pubkey,'hostd-fallback-recovery')
                if answer.status=='pending':self.last_notice=NOTICE
        names=sorted(self._issuer_bindings)
        remaining=[name for name in names if name>self._recovery_binding_cursor]
        if not remaining:remaining=names
        for name in remaining[:1]:
            await self._foreground()
            if self._closed:return
            self._recovery_binding_cursor=name
            await self._fallback_binding(name,'hostd-fallback-periodic')

    async def recover_fallback(self):
        async with self._lock:await self._recover_fallback()

    async def _query(self,filters):
        checked=[dict(f,limit=min(f.get('limit',257),257)) for f in filters]
        rows=await self.relay.read('query',checked)
        def matches(event,fil):
            if 'kinds' in fil and event['kind'] not in fil['kinds']:return False
            if 'authors' in fil and event['pubkey'] not in fil['authors']:return False
            if '#d' in fil and not any(t and t[0]=='d' and len(t)>1 and t[1] in fil['#d'] for t in event['tags']):return False
            return True
        if any(not any(matches(event,fil) for fil in checked) for event in rows):raise RuntimeErrorNotice(NOTICE)
        # A full cap is ambiguous truncation, never authoritative absence.
        if len(rows)>=256:raise RuntimeErrorNotice(NOTICE)
        if any(e['created_at']>int(self.clock())+gs.RELAY_CLOCK_SKEW_SECONDS for e in rows):raise RuntimeErrorNotice(NOTICE)
        return rows

    async def _current(self,record):
        current=await thread_call(agent_catalog.load,self.config.catalog_path,legacy_join_path=self.config.legacy_join_path)
        matches=[r for r in current.records if r.name==record.name]
        if len(matches)!=1 or matches[0].status!='own_bot_verified':raise RuntimeErrorNotice(NOTICE)
        actual=matches[0]
        # Expected channel updates are permitted; identity and protected targets are not.
        if any(getattr(actual,k)!=getattr(record,k) for k in ('pubkey','owner_pubkey','app_id','env_file','unit','prompt_file','responsible_config','lark_config_dir','lark_data_dir')):raise RuntimeErrorNotice(NOTICE)
        return actual

    async def _fresh(self,record):
        await self._current(record)
        profile=authority.latest(await self._query([{'kinds':[0],'authors':[record.pubkey],'limit':2}]))
        if profile['kind']!=0 or profile['pubkey']!=record.pubkey or authority.attested_owner(profile,record.pubkey)!=record.owner_pubkey:raise RuntimeErrorNotice(NOTICE)
        auth=authority.tags(profile,'auth')
        if len(auth)!=1 or auth[0][2]:raise RuntimeErrorNotice(NOTICE)
        policies=await self._query([{'kinds':[30177],'authors':[record.owner_pubkey],'#d':[record.pubkey],'limit':2}])
        if not policies or any(e['kind']!=30177 or e['pubkey']!=record.owner_pubkey for e in policies):raise RuntimeErrorNotice(NOTICE)
        policy=authority.policy_body(authority.latest(policies),record.pubkey)
        if not isinstance(policy.get('feishu'),dict) or policy['feishu'].get('app_id')!=record.app_id:raise RuntimeErrorNotice(NOTICE)
        await self._current(record)
        return profile

    async def _profile_loader(self,agent_id,now):
        if self._closed:raise RuntimeErrorNotice(NOTICE)
        records=[r for r in self.records.values() if r.pubkey==agent_id]
        if len(records)!=1:raise RuntimeErrorNotice(NOTICE)
        return await self._fresh(records[0])

    async def _people_loader(self,app_id,now):
        stage='runtime_scope'
        try:
            if self._closed or app_id not in self.records:raise RuntimeErrorNotice(NOTICE)
            stage='agent_profile'
            await self._fresh(self.records[app_id]);merged={};snapshots=[]
            # Capture protected source/config and SQL on the event-loop thread.
            stage='source_capture'
            for binding in self.store.bindings():
                if binding['status'] not in ('active','degraded'):continue
                stage='source_pause'
                # Already stopping sources contribute no evidence, just like
                # already paused sources. A new fence after capture still
                # invalidates the entire read below, never a partial merge.
                if self.store.conn.execute('SELECT 1 FROM console_pause WHERE binding_id=?',(binding['binding_id'],)).fetchone():continue
                stage='source_config'
                raw=read_owned(binding['config_path']);cfg=json.loads(raw)
                if cfg.get('channel_id')!=binding['channel_id'] or cfg.get('chat_id')!=binding['chat_id']:raise RuntimeErrorNotice(NOTICE)
                api=cfg.get('people_api')
                if not isinstance(api,dict) or api.get('signer_env_file')!=self.config.owner_env_file or api.get('base_url')!=self.relay.origin:raise RuntimeErrorNotice(NOTICE)
                snapshots.append((binding,raw))
            stage='source_missing'
            if not snapshots:raise RuntimeErrorNotice(NOTICE)
            for binding,raw in snapshots:
                stage='source_roles'
                roles=await self.relay.read('members',binding['channel_id'])
                if roles.get(self.relay.owner) not in ('owner','admin'):raise RuntimeErrorNotice(NOTICE)
                stage='source_people'
                answer=await self.relay.read('people',binding['channel_id'])
                if not isinstance(answer.union_ids,dict):raise RuntimeErrorNotice(NOTICE)
                stage='source_union_conflict'
                for pk,union in answer.union_ids.items():
                    if (pk in merged and merged[pk]!=union) or any(k!=pk and u==union for k,u in merged.items()):raise RuntimeErrorNotice(NOTICE)
                    merged[pk]=union
            stage='runtime_scope'
            if self._closed:raise RuntimeErrorNotice(NOTICE)
            stage='agent_current'
            await self._current(self.records[app_id])
            stage='runtime_scope'
            if self._closed:raise RuntimeErrorNotice(NOTICE)
            stage='source_authority'
            current={b['binding_id']:b for b in self.store.bindings()}
            for binding,raw in snapshots:
                stage='source_authority'
                if not _same_people_authority(current.get(binding['binding_id']),binding):raise RuntimeErrorNotice(NOTICE)
                stage='source_pause'
                if self.store.conn.execute('SELECT 1 FROM console_pause WHERE binding_id=?',(binding['binding_id'],)).fetchone():raise RuntimeErrorNotice(NOTICE)
                stage='source_config_changed'
                if read_owned(binding['config_path'])!=raw:raise RuntimeErrorNotice(NOTICE)
            stage='source_people'
            IdentityResolver._validate_people(PeopleBindings(merged))
            return PeopleBindings(merged)
        except Exception:
            # Fixed local stages only, never exception text or identity data.
            # A later successful attempt does not rewrite this failure receipt.
            self.last_identity_failure={'stage':stage}
            raise RuntimeErrorNotice(NOTICE) from None

    async def _claims(self):
        policies=await self.relay.read('policy_snapshot')
        candidates=sorted({d[1] for e in policies if (d:=gs._first_tag(e,'d')) is not None
                           and isinstance(d[1],str) and gs.HEX64_RE.fullmatch(d[1]) and gs.declares_mirror(e['content'])})
        profiles=[]
        for group in gs.batches(candidates,gs.PROFILE_QUERY_BATCH):
            profiles.extend(await self._query([{'kinds':[0],'authors':group}]))
        def captured(filters):
            fil=filters[0]
            if fil=={'kinds':[gs.KIND_MANAGED_AGENT]}:return policies
            if set(fil)=={'kinds','authors'} and fil['kinds']==[0]:
                return [e for e in profiles if e['pubkey'] in fil['authors']]
            raise RuntimeErrorNotice(NOTICE)
        return gs.read_binding_claims(captured)

    async def chats(self,app_id):
        """Read every actual same-app page before admitting any discovered chat."""
        if app_id not in self.clients:raise RuntimeErrorNotice(NOTICE)
        client=self.clients[app_id];token='';tokens=set();chats=set()
        try:
            for _ in range(100):
                params={'page_size':100}
                if token:params['page_token']=token
                data=await thread_call(client.call,'onboarding bot chats',['api','GET','/open-apis/im/v1/chats','--params',json.dumps(params),'--as','bot'])
                if not isinstance(data,dict) or not isinstance(data.get('items'),list) or type(data.get('has_more')) is not bool:raise ValueError
                for item in data['items']:
                    if not isinstance(item,dict):raise ValueError
                    chat=item.get('chat_id');ident(chat,'oc_')
                    if chat in chats:raise ValueError
                    chats.add(chat)
                if not data['has_more']:return tuple(sorted(chats))
                token=data.get('page_token');ident(token)
                if token in tokens:raise ValueError
                tokens.add(token)
            raise ValueError
        except Exception:raise RuntimeErrorNotice(NOTICE) from None

    async def discover(self,app_id,chat_id):
        record=self.records.get(app_id)
        if record is None:raise RuntimeErrorNotice(NOTICE)
        try:
            ident(chat_id,'oc_');await self._fresh(record)
            roster=await thread_call(BotMembership(self.clients).listing,app_id,chat_id)
            if app_id not in roster.bots:raise ValueError
            view=await self._claims()
            # Existing helper skips malformed claim entries; discovery must not
            # interpret a skipped malformed mirror claim as an unbound group.
            for event in view.policies:
                if gs.declares_mirror(event['content']):
                    body=authority.policy_body(event,authority.tags(event,'d')[0][1]);block=body.get('feishu',{})
                    raw=block.get('bindings',[])
                    if not isinstance(raw,list) or len(gs._claims_in(authority.tags(event,'d')[0][1],event['pubkey'],'',event['content']))!=len(raw):raise ValueError
            matching=[c for c in view.claims if c.chat_ref==gs.chat_ref(chat_id)]
            await self._current(record)
            local=[b for b in self.store.bindings() if b['chat_id']==chat_id and b['status']!='retired']
            if self._closed:raise ValueError
            if not matching:
                if local:raise ValueError
                return Discovery('unbound',app_id,record.pubkey,chat_id)
            if len(matching)!=1 or not gs.claim_valid(matching[0],int(self.clock())):raise ValueError
            claim=matching[0]
            accepted=[b for b in local if b['status']=='active' and b['channel_id']==claim.channel and b['mirror_pubkey']==claim.mirror and claim.owner==self.relay.owner]
            if len(accepted)!=1:raise ValueError
            cfg=json.loads(read_owned(accepted[0]['config_path']))
            if cfg.get('channel_id')!=claim.channel or cfg.get('chat_id')!=chat_id:raise ValueError
            if (await self.relay.read('members',claim.channel)).get(self.relay.owner) not in ('owner','admin'):raise ValueError
            await self._current(record)
            if self._closed or not _same_binding_authority(next((b for b in self.store.bindings() if b['binding_id']==accepted[0]['binding_id']),None),accepted[0]) or json.loads(read_owned(accepted[0]['config_path']))!=cfg:raise ValueError
            return Discovery('bound',app_id,record.pubkey,chat_id,accepted[0]['binding_id'])
        except Exception:
            self.last_notice=NOTICE
            return Discovery('uncertain',app_id,record.pubkey,chat_id)

    def enqueue_feed(self,app_id,row):
        """Trusted SDK metadata only; one bounded queue, no awaited IO."""
        try:
            if self._closed or app_id not in self.eligible_apps or not isinstance(row,dict) or row.get('app')!=app_id:raise ValueError
            now=int(self.clock());stamp(now)
            if len(self._queue)>=128:raise ValueError
            if row.get('type')=='card.action.trigger':
                decision=card_from_feed(row)
                actual=self.store.join_request(decision.request_id)
                if not actual or (actual['callback_app_id'],actual['chat_id'],actual['card_message_id'],actual['card_generation'])!=(
                        decision.app_id,decision.chat_id,decision.message_id,decision.generation):raise ValueError
                marker=self.store.fallback_provenance(decision.request_id)
                if marker is not None and marker.issuer_app_id!=app_id:raise ValueError
                item=('card',app_id,decision,None,None)
            elif row.get('type')=='_connected':item=('connected',app_id,None,None,None)
            elif row.get('type')=='im.chat.member.bot.added_v1':
                ident(row.get('chat_id'),'oc_');ident(row.get('event_id'))
                operator=row.get('operator_id')
                if not isinstance(operator,dict):raise ValueError
                op=Operator(operator.get('open_id'),operator.get('union_id') or '')
                item=('invite',app_id,row['chat_id'],row['event_id'],op)
            else:raise ValueError
            if item not in self._queue:self._queue.append(item)
            self._wake.set();return {'toast':dict(QUEUED['toast'])}
        except Exception:
            self.last_notice=NOTICE;return {'toast':{'type':'error','content':NOTICE}}

    async def _discovered(self,app_id,chat_id,event_id=None,operator=None):
        if self._closed or app_id not in self.records:return
        answer=await self.discover(app_id,chat_id)
        if answer.status=='uncertain':return
        now=int(self.clock());kind='channel' if answer.status=='bound' else 'new_binding'
        if operator is not None:
            self.coordinator.enqueue_invite(Invite(app_id,answer.agent_id,chat_id,event_id,operator,kind,answer.binding_id),now=now)
            return
        # No inviter is inferred on reconnect. Requested-only metadata and real
        # cards use the same durable coordinator; approval has a separate actor.
        with self.store.transaction():
            previous=[r for r in self.store.join_requests() if r['agent_id']==answer.agent_id and r['chat_id']==chat_id]
            if previous:request=previous[0]['request_id']
            else:
                request='JOIN-'+hashlib.sha256(f'hostd-discovery:v1:{app_id}:{answer.agent_id}:{chat_id}'.encode()).hexdigest()[:8]
                self.store.create_join(request,answer.agent_id,self.records[app_id].owner_pubkey,app_id,chat_id,kind=kind,binding_id=answer.binding_id,now=now)
        # Reconnect is discovery, not another approval retry slot. Existing
        # approved/applied requests retain the dedicated due scheduler above.
        current=self.store.join_request(request)
        if current and current['status'] in ('approved','applied'):return
        await self.coordinator.tick(now=now,request_id=request)

    async def _foreground(self):
        """Yield between complete background checks, under the owned root lock.

        Keep live invitations/callbacks FIFO and finish their coordinator work
        before resuming reconnect scans. Metadata remains only a hint; discovery
        and card/effect adapters still perform their current authority checks.
        """
        if getattr(self,'_foreground_active',False):return
        self._foreground_active=True
        try:
            for _ in range(128):
                if self._closed:return
                item=next((item for item in self._queue if item[0] in ('invite','card')),None)
                if item is None:break
                self._queue.remove(item)
                kind,app,chat,event,operator=item
                previous=getattr(self,'_active',{})
                self._active={'kind':kind,'app_id':app}
                try:
                    if kind=='card':
                        if self.store.fallback_provenance(chat.request_id) is not None:await self._fallback_card(chat)
                        else:self.coordinator.enqueue_card(chat,now=int(self.clock()))
                    else:
                        await self._discovered(app,chat,event,operator)
                    await self.coordinator.drain()
                    if kind=='invite':
                        for name,(issuer,profile) in tuple(self._issuer_bindings.items())[:128]:
                            if self._closed:return
                            binding=self.fallback_discoverer._sql(name)
                            if issuer==app and binding['chat_id']==chat:
                                await self._fallback_binding(name,event)
                except Exception:self.last_notice=NOTICE
                finally:self._active=previous
            # A transient effect failure must not wait for every application's
            # reconnect scan. Keep existing proof/unknown-send rules and spend
            # one globally spaced, per-request cooled retry after live FIFO work.
            if not self._closed and not any(v[0] in ('invite','card') for v in self._queue):
                await self._retry_approved()
                await self._retry_notices()
        finally:self._foreground_active=False

    async def _retry_notices(self):
        # Terminal requests leave effect recovery, but their original card may
        # still need a persisted PATCH after failure, cancellation or restart.
        now=self._retry_clock()
        if (self._closed or now<self._notice_retry_at
                or any(v[0] in ('invite','card') for v in self._queue)):return
        self._notice_retry_at=now+60
        previous=getattr(self,'_active',{})
        self._active={'kind':'notice_retry'}
        try:await self.coordinator.retry_notices(now=int(self.clock()))
        except Exception:self.last_notice=NOTICE
        finally:self._active=previous

    def _approval_rows(self):
        rows=sorted((r for r in self.store.join_requests()
                     if r['status'] in ('approved','applied')
                     and self.store.fallback_provenance(r['request_id']) is None),
                    key=lambda r:r['request_id'])
        pending={r['request_id'] for r in rows}
        self._approval_retry_due={key:due for key,due in self._approval_retry_due.items() if key in pending}
        return rows

    def _approval_delay(self):
        """Monotonic wake deadline; no effects and no permission cache."""
        rows=self._approval_rows()
        if not rows:return 60.
        due=min(self._approval_retry_due.get(r['request_id'],0.) for r in rows)
        return max(0.,max(self._approval_retry_at,due)-self._retry_clock())

    async def _retry_approved(self):
        now=self._retry_clock()
        if self._closed or now<self._approval_retry_at:return
        rows=[r for r in self._approval_rows() if self._approval_retry_due.get(r['request_id'],0.)<=now]
        if not rows:return
        row=next((r for r in rows if r['request_id']>self._approval_retry_cursor),rows[0])
        self._approval_retry_cursor=row['request_id']
        current=self.store.join_request(row['request_id'])
        if self._closed or not current or current['status'] not in ('approved','applied'):return
        # Reserve before awaiting: one serial start per 5s, one request per 60s.
        # This schedules readback, never bypasses durable UNKNOWN effect guards.
        self._approval_retry_at=now+5
        self._approval_retry_due[row['request_id']]=now+60
        previous=getattr(self,'_active',{})
        self._active={'kind':'approval_retry','app_id':current['callback_app_id']}
        try:await self.coordinator.tick(now=int(self.clock()),request_id=row['request_id'])
        except Exception:self.last_notice=NOTICE
        finally:
            self._approval_retry_due[row['request_id']]=self._retry_clock()+60
            self._active=previous

    async def drain(self):
        async with self._lock:
            self._wake.clear()
            processed=0
            while self._queue and not self._closed and processed<1:
                await self._foreground()
                if not self._queue or self._closed:break
                kind,app,chat,event,operator=self._queue.popleft();processed+=1
                self._active={'kind':kind,'app_id':app}
                try:
                    if kind=='card':
                        if self.store.fallback_provenance(chat.request_id) is not None:await self._fallback_card(chat)
                        else:self.coordinator.enqueue_card(chat,now=int(self.clock()))
                    else:
                        # Issuer admission already ran at startup and runs in
                        # periodic recovery below, not once per app reconnect.
                        if kind=='connected':
                            # A complete chat listing is required, but each
                            # pass checks only one chat. Continuations go to
                            # the tail so one app cannot hold every other app
                            # and issuer recovery behind all of its groups.
                            cursors=getattr(self,'_connected_chat_cursors',{})
                            self._connected_chat_cursors=cursors
                            chats=sorted(await self.chats(app))
                            pending=[value for value in chats if value>cursors.get(app,'')]
                            try:
                                for value in pending[:1]:
                                    await self._foreground()
                                    if self._closed:break
                                    cursors[app]=value
                                    await self._discovered(app,value)
                            finally:
                                if len(pending)>1 and not self._closed:
                                    continuation=('connected',app,None,None,None)
                                    if continuation not in self._queue:self._queue.append(continuation)
                                else:cursors.pop(app,None)
                        else:await self._discovered(app,chat,event,operator)
                        for name,(issuer,profile) in tuple(self._issuer_bindings.items())[:128]:
                            await self._foreground()
                            if self._closed:break
                            binding=self.fallback_discoverer._sql(name)
                            if issuer==app and (chat is None or binding['chat_id']==chat):
                                await self._fallback_binding(name,event or 'hostd-fallback-reconnect')
                except Exception:self.last_notice=NOTICE
                finally:self._active={}
            if self._queue:self._wake.set()
            if self._closed:return
            await self.coordinator.drain()
            if self._closed:return
            await self._foreground()
            await self._recover_own()
            await self._foreground()
            await self._restore_own_request()
            if self._closed:return
            # Approved/applied recovery has one rate-limited owner above.
            pending=sorted((r for r in self.store.join_requests() if r['status']=='requested'
                            and self.store.fallback_provenance(r['request_id']) is None),
                           key=lambda r:r['request_id'])
            cursor=getattr(self,'_join_recovery_cursor','')
            remaining=[r for r in pending if r['request_id']>cursor] or pending
            for row in remaining[:1]:
                await self._foreground()
                if self._closed:return
                self._join_recovery_cursor=row['request_id']
                current=self.store.join_request(row['request_id'])
                if not current or current['status']!='requested':continue
                await self.coordinator.tick(now=int(self.clock()),request_id=row['request_id'])
            if self.coordinator.last_notice:self.last_notice=NOTICE
            await self._recover_fallback()

    def start(self):
        if self._task is None:self._task=asyncio.create_task(self.run())
        return self._task

    async def run(self):
        recovery_at=0.
        while not self._closed:
            if self._wake.is_set() or self._retry_clock()>=recovery_at:
                await self.drain()
                recovery_at=self._retry_clock()+60
            else:
                # Approval-only timer wakes must not speed up issuer/fallback
                # recovery. All effects still run under the same writer lock.
                async with self._lock:await self._foreground()
            if self._closed:return
            delay=min(max(0.,recovery_at-self._retry_clock()),self._approval_delay())
            try:await asyncio.wait_for(self._wake.wait(),timeout=delay)
            except asyncio.TimeoutError:pass

    def close(self):
        self._closed=True;self._queue.clear();self.coordinator.close();self._wake.set()
        # Store, feeds and shared Scheduler belong to root and remain usable.
