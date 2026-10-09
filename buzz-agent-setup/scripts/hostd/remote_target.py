"""Pure, owner-authorized remote outlet target discovery.

No local binding/Worker, foreign credential, Store write or activation is created.
Root's synchronous authorize callback is an internal main-loop authority seam;
HTTP/business input must never provide it. A verified target is discovery evidence,
not a grant, connection, delivery or completed onboarding result.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
from pathlib import Path
import time

import buzz_agent_join_requests as legacy
import buzz_feishu_group_sync as gs
import recovery_authority as authority
from . import agent_catalog
from .async_io import thread_call
from .bot_clients import BotLarkCli, _trusted_relay
from .join_effects import Nip98Relay
from .safety import read_owned

NOTICE = ('远端发送目标尚未核验，保留待处理。怎么解决：检查本机 agent 的明确授权、自己的应用与签名身份、完整群列表和成员、有效频道认领及 Relay 访问。'
          '\n复制给 AI：帮我核查 hostd 远端 agent 发送目标；不要借用同步 bot、外国机器的配置或私钥，也不要把发现结果当作授权或已接入。')


class _Pending(ValueError):
    pass


@dataclass(frozen=True)
class RemoteTarget:
    agent_pubkey: str
    owner_pubkey: str
    app_id: str
    channel_id: str
    chat_id: str = field(repr=False)
    chat_ref: str
    relay_url: str
    mirror_pubkey: str
    mirror_owner_pubkey: str
    claim_event_id: str
    claimed_at: int
    heartbeat: int
    checked_at: int


@dataclass(frozen=True)
class RemoteResolution:
    status: str
    target: RemoteTarget | None = field(default=None,repr=False)
    reason: str = ''
    notice: str = ''

    def readback(self):
        return {'status':self.status,'reason':self.reason,'live_verified':False}


class RemoteTargetResolver:
    def __init__(self,catalog_path,legacy_join_path,*,relay,clients,authorize=None,clock=time.time):
        self.catalog_path,self.legacy_join_path=Path(catalog_path),Path(legacy_join_path)
        self.relay,self.clients,self.authorize,self.clock=relay,clients,authorize,clock

    def _authorized(self,record,channel):
        try:return callable(self.authorize) and self.authorize(record,channel) is True
        except Exception:return False

    def _guard(self,record,channel):
        if not self._authorized(record,channel):raise _Pending('not_authorized')

    async def _io(self,record,channel,operation):
        self._guard(record,channel)
        cancelled=False
        try:return await operation()
        except asyncio.CancelledError:
            cancelled=True
            raise
        finally:
            # The trusted callback always runs on the main loop, never in the
            # catalog, signed wire or bot transport thread.
            if cancelled:self._authorized(record,channel)
            else:self._guard(record,channel)

    async def _current(self,record,channel):
        catalog=await self._io(record,channel,lambda:thread_call(agent_catalog.load,self.catalog_path,
                                                   legacy_join_path=self.legacy_join_path))
        actual=[candidate for candidate in catalog.records if candidate.name==record.name]
        if len(actual)!=1 or actual[0].status!='own_bot_verified':raise _Pending('catalog_unverified')
        current=actual[0]
        # Approved allowlist edits are permitted; credentials, identity and
        # target paths cannot be silently substituted behind an old record.
        fields=('name','pubkey','owner_pubkey','app_id','env_file','unit','prompt_file','responsible_config',
                'log_file','lark_config_dir','lark_data_dir','profile_sha256')
        if any(getattr(current,key)!=getattr(record,key) for key in fields):raise _Pending('catalog_changed')
        if channel not in current.channels:raise _Pending('channel_not_allowed')
        def own_origin():
            env=legacy.parse_env(read_owned(current.env_file).decode())
            return _trusted_relay(env.get('BUZZ_RELAY_URL',''),(self.relay.origin,))
        origin=await self._io(record,channel,lambda:thread_call(own_origin))
        if origin!=self.relay.origin:raise _Pending('relay_mismatch')
        return current

    async def _query(self,record,channel,filters):
        bounded=[dict(f,limit=f.get('limit',256)) for f in filters]
        rows=await self._io(record,channel,lambda:self.relay.read('query',bounded))
        def matches(event,fil):
            if 'kinds' in fil and event['kind'] not in fil['kinds']:return False
            if 'authors' in fil and event['pubkey'] not in fil['authors']:return False
            if '#d' in fil and not any(len(t)>1 and t[0]=='d' and t[1] in fil['#d'] for t in event['tags']):return False
            return True
        if (not isinstance(rows,list) or len(rows)>=256
                # Nip98Relay's actual SignedReader/RecoveryRelay verifies all
                # signatures off the main loop before returning these rows.
                or any(event['created_at']>int(self.clock())+gs.RELAY_CLOCK_SKEW_SECONDS
                       or not any(matches(event,fil) for fil in bounded) for event in rows)):
            raise _Pending('relay_incomplete')
        return rows

    async def _own_identity(self,record,channel):
        profiles=await self._query(record,channel,[{'kinds':[0],'authors':[record.pubkey],'limit':2}])
        profile=authority.latest(profiles)
        if (profile is None or authority.attested_owner(profile,record.pubkey)!=record.owner_pubkey
                or authority.tags(profile,'auth')[0][2]):raise _Pending('agent_owner_unverified')
        policies=await self._query(record,channel,[{'kinds':[30177],'authors':[record.owner_pubkey],'#d':[record.pubkey],'limit':2}])
        policy=authority.latest(policies)
        body=authority.policy_body(policy,record.pubkey) if policy else {}
        feishu=body.get('feishu')
        if not isinstance(feishu,dict) or feishu.get('app_id')!=record.app_id or feishu.get('mirror') is True:
            raise _Pending('agent_policy_unverified')

    async def _policy_snapshot(self,record,channel):
        return await self._io(record,channel,lambda:self.relay.read('policy_snapshot'))

    async def _claims(self,record,channel,roles):
        policies=await self._policy_snapshot(record,channel)
        parsed=[];candidates=set()
        for policy in policies:
            tags=authority.tags(policy,'d')
            if len(tags)!=1 or len(tags[0])!=2 or not gs.HEX64_RE.fullmatch(tags[0][1]):raise _Pending('claim_malformed')
            mirror=tags[0][1];body=authority.policy_body(policy,mirror)
            # Even policies we ignore already passed complete signature, tag and
            # JSON shape validation. Their display/content is never authority.
            if gs.declares_mirror(policy['content']):candidates.add(mirror)
            parsed.append((mirror,policy,body))
        if not candidates:raise _Pending('claim_absent')
        owners={}
        for mirror in sorted(candidates):
            profiles=await self._query(record,channel,[{'kinds':[0],'authors':[mirror],'limit':2}])
            profile=authority.latest(profiles)
            if profile is None:raise _Pending('mirror_owner_unverified')
            owner=authority.attested_owner(profile,mirror)
            if authority.tags(profile,'auth')[0][2]:raise _Pending('mirror_owner_unverified')
            owners[mirror]=owner
        claims=[]
        for mirror,owner in owners.items():
            latest=authority.latest([policy for identity,policy,_ in parsed if identity==mirror and policy['pubkey']==owner])
            if latest is None or not gs.declares_mirror(latest['content']):continue
            body=authority.policy_body(latest,mirror);bindings=body['feishu'].get('bindings')
            decoded=gs._claims_in(mirror,owner,'',latest['content'])
            if not isinstance(bindings,list) or len(decoded)!=len(bindings):raise _Pending('claim_malformed')
            for claim in decoded:
                if gs.claim_valid(claim,int(self.clock())):claims.append((claim,latest['id']))
        # Full local-channel roster proves the foreign mirror and its owner;
        # local agent owner need not be the foreign channel's owner.
        eligible=[pair for pair in claims if pair[0].channel==channel and roles.get(pair[0].mirror)=='bot'
                  and roles.get(pair[0].owner) in ('owner','admin')]
        if not eligible:raise _Pending('claim_unverified')
        winner=eligible[0]
        for pair in eligible[1:]:
            if gs.claim_beats(pair[0],winner[0]):winner=pair
        chosen=winner[0]
        # Another channel's winning claim for this same chat is a real conflict;
        # don't read absent local metadata as permission to create a binding.
        if any(claim.channel!=channel and claim.chat_ref==chosen.chat_ref and gs.claim_beats(claim,chosen)
               for claim,_ in claims):raise _Pending('claim_conflict')
        return winner

    async def _chats(self,record,channel,client):
        token='';tokens=set();chats=set()
        for _ in range(100):
            params={'page_size':100}
            if token:params['page_token']=token
            data=await self._io(record,channel,lambda:thread_call(client.call,'remote outlet chats',
                                ['api','GET','/open-apis/im/v1/chats','--params',json.dumps(params),'--as','bot']))
            if not isinstance(data,dict) or not isinstance(data.get('items'),list) or type(data.get('has_more')) is not bool:
                raise _Pending('chats_incomplete')
            for item in data['items']:
                chat=item.get('chat_id') if isinstance(item,dict) else None
                if not isinstance(chat,str) or not gs.CHAT_ID_RE.fullmatch(chat) or chat in chats:raise _Pending('chats_incomplete')
                chats.add(chat)
            if not data['has_more']:return chats
            token=data.get('page_token')
            if not isinstance(token,str) or not token or len(token)>1024 or any(ord(c)<32 for c in token) or token in tokens:
                raise _Pending('chats_incomplete')
            tokens.add(token)
        raise _Pending('chats_incomplete')

    async def resolve(self,record,channel_id):
        try:
            self._guard(record,channel_id)
            if (not isinstance(record,agent_catalog.AgentRecord) or not isinstance(channel_id,str)
                    or not gs.UUID_RE.fullmatch(channel_id) or not isinstance(self.relay,Nip98Relay)
                    or self.relay.owner!=record.owner_pubkey):raise _Pending('reader_identity_unverified')
            current=await self._current(record,channel_id)
            client=self.clients.get(current.app_id)
            if (not isinstance(client,BotLarkCli) or client.app_id!=current.app_id
                    or client.config_dir!=current.lark_config_dir or client.data_dir!=current.lark_data_dir):
                raise _Pending('bot_identity_unverified')
            identity=await self._io(record,channel_id,lambda:thread_call(client.identity))
            if identity!=(current.app_id,''):raise _Pending('bot_identity_unverified')
            await self._own_identity(record,channel_id)
            roster_events=await self._query(record,channel_id,[{'kinds':[39002],'authors':[self.relay.relay_pubkey],
                                                               '#d':[channel_id],'limit':2}])
            if not roster_events:raise _Pending('roster_unavailable')
            for event in roster_events:authority.exact_tag(event,'d',channel_id)
            roles=authority.membership(authority.latest(roster_events),channel_id)
            if roles.get(record.pubkey)!='bot':raise _Pending('agent_not_bot')
            claim,event_id=await self._claims(record,channel_id,roles)
            chats=await self._chats(record,channel_id,client)
            matching=[chat for chat in chats if gs.chat_ref(chat)==claim.chat_ref]
            if len(matching)!=1:raise _Pending('chat_unverified')
            chat=matching[0]
            roster=await self._io(record,channel_id,lambda:thread_call(client.member_listing,chat,'union_id'))
            if not roster.complete or record.app_id not in roster.bots:raise _Pending('members_incomplete')
            await self._current(record,channel_id)
            identity=await self._io(record,channel_id,lambda:thread_call(client.identity))
            if identity!=(record.app_id,''):raise _Pending('bot_identity_unverified')
            self._guard(record,channel_id)
            if not gs.claim_valid(claim,int(self.clock())):raise _Pending('claim_expired')
            return RemoteResolution('verified',RemoteTarget(record.pubkey,record.owner_pubkey,record.app_id,channel_id,
                chat,claim.chat_ref,self.relay.origin,claim.mirror,claim.owner,event_id,claim.claimed_at,claim.heartbeat,int(self.clock())))
        except _Pending as exc:return RemoteResolution('pending',reason=str(exc),notice=NOTICE)
        except Exception:return RemoteResolution('pending',reason='evidence_unverified',notice=NOTICE)
