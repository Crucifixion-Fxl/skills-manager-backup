"""Read-only ADR27 mapping recovery without a local binding or authorization.

A discovered target is a lookup constraint, never a grant. Proof and bot identity
are freshly checked on each resolution; bodies live only in temporary variables.
The caller must separately check the own catalog/credential-profile path pair:
app identity and the reader's public identity do not prove those catalog paths.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import re
import time
from types import SimpleNamespace
from urllib.parse import parse_qsl, urlsplit

import buzz_feishu_group_sync as gs
import recovery_authority as authority
from .agent_signed_reads import OwnAgentReader
from .async_io import thread_call
from .bot_clients import BotLarkCli
from .delivery_mapping import DeliveryMapping, buzz_mapping, feishu_mapping, _receipt_link, _verified
from .remote_target import RemoteTarget, RemoteTargetResolver

NOTICE = ('公开消息对应关系尚未核实，保持待核验。怎么解决：检查当前签名目录、有效赢家 claim、自己的 bot 身份、完整消息读取和原话题；缺少可信同步应用证明时不能猜测人类消息投递。'
          '\n复制给 AI：帮我核查 hostd 远端公开映射的签名、频道、原群、作者应用与话题根；不要输出消息正文、凭据或把映射当作发送授权。')
HEX = re.compile('[0-9a-f]{64}')
MAX_DEPTH = 8
MAX_HISTORY = 150


class _Pending(ValueError):
    def __init__(self,reason='mapping_unverified'):self.reason=reason


@dataclass(frozen=True)
class MappingResolution:
    status: str
    mapping: DeliveryMapping | None = None
    reason: str = ''
    notice: str = ''

    def readback(self):return {'status':self.status,'reason':self.reason,'live_verified':False}


@dataclass
class _Proof:
    roles: dict
    heartbeat: int
    apps: dict = field(default_factory=dict)  # Only verified app metadata; never message bodies.


class _DirectoryView:
    """Reuse the actual discovery authority parsers with the own-agent transport."""
    def __init__(self,context):self.context=context;self.clock=context.clock

    async def _query(self,record,channel,filters):
        return await self.context.reader.query([dict(f,limit=f.get('limit',256)) for f in filters])

    async def _policy_snapshot(self,record,channel):
        return await self.context.reader.policy_snapshot()


class RemoteMappingContext:
    def __init__(self,target,*,reader,bot_client,clock=lambda:int(time.time()),link_base=None):
        self.target,self.reader,self.bot,self.clock=target,reader,bot_client,clock
        self.link_base=link_base if link_base is not None else getattr(target,'relay_url','')
        self._heartbeat=None  # Signed lease metadata only, not a cached proof/body.

    def _link_origin(self):
        base=self.link_base
        if (not isinstance(base,str) or not base or len(base)>2048
                or '?' in base or '#' in base
                or any(ord(c)<=32 or ord(c)==127 for c in base)):
            raise _Pending('proof_unverified')
        try:
            parts=urlsplit(base);port=parts.port
        except ValueError:raise _Pending('proof_unverified') from None
        if (parts.scheme!='https' or not parts.hostname or parts.username is not None
                or parts.path or parts.query or parts.fragment
                or '%' in parts.netloc or '\\' in parts.netloc or parts.netloc.endswith(':')
                or (port is not None and not 1<=port<=65535)):
            raise _Pending('proof_unverified')
        return parts

    async def _identity(self):
        self._link_origin()  # Reject unreviewable link origins before any adapter IO.
        t=self.target
        if (not isinstance(t,RemoteTarget) or not isinstance(self.reader,OwnAgentReader)
                or not isinstance(self.bot,BotLarkCli)
                or (self.reader.agent,self.reader.owner,self.reader.origin)!=(t.agent_pubkey,t.owner_pubkey,t.relay_url)
                or self.bot.app_id!=t.app_id or gs.chat_ref(t.chat_id)!=t.chat_ref
                or type(t.heartbeat) is not int or t.heartbeat<0):raise _Pending('proof_unverified')
        if (await thread_call(self.bot.identity))[0]!=t.app_id:raise _Pending('proof_unverified')

    async def _proof(self):
        await self._identity()
        t=self.target;roles=await self.reader.members(t.channel_id)
        if roles.get(t.agent_pubkey)!='bot':raise _Pending('proof_unverified')
        record=SimpleNamespace(pubkey=t.agent_pubkey,owner_pubkey=t.owner_pubkey,app_id=t.app_id)
        view=_DirectoryView(self)
        await RemoteTargetResolver._own_identity(view,record,t.channel_id)
        claim,_=await RemoteTargetResolver._claims(view,record,t.channel_id,roles)
        # Current winner/lease was freshly verified above. Heartbeat-only policy
        # replacement preserves authority; a reclaim or different signer does not.
        if ((claim.mirror,claim.owner,claim.channel,claim.chat_ref,claim.claimed_at)
                !=(t.mirror_pubkey,t.mirror_owner_pubkey,t.channel_id,t.chat_ref,t.claimed_at)):
            raise _Pending('proof_unverified')
        minimum=max(t.heartbeat,self._heartbeat if self._heartbeat is not None else t.heartbeat)
        if claim.heartbeat<minimum:raise _Pending('proof_unverified')
        self._heartbeat=claim.heartbeat
        return _Proof(roles,claim.heartbeat)

    async def _app(self,pub,proof):
        if pub in proof.apps:return proof.apps[pub][1]
        if proof.roles.get(pub)!='bot' or pub==self.target.mirror_pubkey:raise _Pending('mapping_unverified')
        rows=await self.reader.query([{'kinds':[0],'authors':[pub],'limit':2}])
        profile=authority.latest(rows)
        if profile is None:raise _Pending('proof_unverified')
        owner=authority.attested_owner(profile,pub)
        if authority.tags(profile,'auth')[0][2]:raise _Pending('proof_unverified')
        rows=await self.reader.query([{'kinds':[30177],'authors':[owner],'#d':[pub],'limit':2}])
        policy=authority.latest(rows)
        if policy is None:raise _Pending('proof_unverified')
        body=authority.policy_body(policy,pub)
        app=gs._claimed_app_id(json.dumps(body))
        if not app or gs.declares_mirror(policy['content']):raise _Pending('proof_unverified')
        if pub==self.target.agent_pubkey and (owner,app)!=(self.target.owner_pubkey,self.target.app_id):raise _Pending('proof_unverified')
        proof.apps[pub]=(owner,app,profile['id'],policy['id'])
        return app

    async def _event(self,eid):
        if not isinstance(eid,str) or not HEX.fullmatch(eid):raise _Pending('input_invalid')
        rows=await self.reader.query([{'kinds':[9],'ids':[eid],'#h':[self.target.channel_id],'limit':2}])
        if len(rows)!=1 or rows[0]['id']!=eid or not _verified(rows[0],self.target.channel_id):raise _Pending()
        return rows[0]

    async def _message(self,mid):
        if not isinstance(mid,str) or not gs.MESSAGE_ID_RE.fullmatch(mid):raise _Pending('input_invalid')
        row=await thread_call(self.bot.message_view,mid,'open_id')
        if (not isinstance(row,dict) or row.get('message_id')!=mid or row.get('chat_id')!=self.target.chat_id
                or row.get('deleted')):raise _Pending()
        root=row.get('root_id') or mid
        if not isinstance(root,str) or not gs.MESSAGE_ID_RE.fullmatch(root):raise _Pending()
        sender=row.get('sender')
        if not isinstance(sender,dict):raise _Pending()
        if sender.get('sender_type')=='app':
            if sender.get('id_type')!='app_id' or not isinstance(sender.get('id'),str) or not gs.APP_ID_RE.fullmatch(sender['id']):raise _Pending()
        elif sender.get('sender_type')=='user':
            prefix={'open_id':'ou_','union_id':'on_'}.get(sender.get('id_type'))
            if not prefix or not isinstance(sender.get('id'),str) or not re.fullmatch(prefix+r'[A-Za-z0-9]+',sender['id']):raise _Pending()
        else:raise _Pending()
        return row,root

    async def _feishu(self,mid,proof,stack,*,expected_event=None):
        if mid in stack or len(stack)>=MAX_DEPTH:raise _Pending('mapping_unverified')
        stack.add(mid)
        try:
            row,root=await self._message(mid)
            root_mapping=await self._feishu(root,proof,stack) if root!=mid else None
            link=_receipt_link(row)
            if link:
                parts=urlsplit(link);origin=self._link_origin()
                pairs=parse_qsl(parts.query,strict_parsing=True);query=dict(pairs)
                if ('#' in link or any(ord(c)<=32 or ord(c)==127 for c in link)
                        or parts.scheme!='https' or parts.netloc!=origin.netloc or parts.path!=gs.CARD_OPEN_PATH
                        or parts.username is not None or parts.fragment or len(pairs)!=len(query)
                        or set(query) not in ({'e','c'},{'e','c','t'}) or query.get('c')!=self.target.channel_id):raise _Pending()
                event=await self._event(query.get('e'))
                if expected_event and event['id']!=expected_event:raise _Pending()
                if proof.roles.get(event['pubkey']) in gs.HUMAN_ROLES:raise _Pending('sync_app_unavailable')
                app=await self._app(event['pubkey'],proof)
                mapping=feishu_mapping(row,event,self.target.channel_id,self.target.chat_id,self.link_base,
                    set(),{event['pubkey']:app},'',root_mapping=root_mapping)
            else:
                # Multi-letter tags are not relay indexes. Exact IDs are only
                # query hints; otherwise OwnAgentReader requires a complete,
                # signed bounded page before local mapping selection.
                fil={'kinds':[9],'#h':[self.target.channel_id],'limit':256}
                if expected_event:fil.update(ids=[expected_event],limit=2)
                rows=await self.reader.query([fil])
                candidates=[]
                for event in rows:
                    mapping=buzz_mapping(event,self.target.channel_id,
                        {pk for pk,role in proof.roles.items() if role=='bot' and pk!=self.target.mirror_pubkey},
                        {self.target.mirror_pubkey})
                    if not mapping or mapping.message_id!=mid or mapping.feishu_root!=root:continue
                    if expected_event and mapping.event_id!=expected_event:continue
                    if event['pubkey']!=self.target.mirror_pubkey:
                        app=await self._app(event['pubkey'],proof)
                        if any(row['sender'].get(key)!=value for key,value in {'sender_type':'app','id_type':'app_id','id':app}.items()):continue
                    if root!=mid and (root_mapping is None or mapping.buzz_root!=root_mapping.event_id):continue
                    candidates.append(mapping)
                if len(candidates)!=1:raise _Pending()
                mapping=candidates[0]
            if mapping is None:raise _Pending()
            return mapping
        finally:stack.remove(mid)

    async def _buzz(self,eid,proof):
        event=await self._event(eid)
        embedded=buzz_mapping(event,self.target.channel_id,
            {pk for pk,role in proof.roles.items() if role=='bot' and pk!=self.target.mirror_pubkey},
            {self.target.mirror_pubkey})
        if embedded:
            return await self._feishu(embedded.message_id,proof,set(),expected_event=eid)
        if proof.roles.get(event['pubkey']) in gs.HUMAN_ROLES:raise _Pending('sync_app_unavailable')
        await self._app(event['pubkey'],proof)
        since=datetime.fromtimestamp(max(0,event['created_at']-900),timezone.utc)
        rows,more=await thread_call(self.bot.messages,self.target.chat_id,since,order='desc',page_limit=2)
        if more or len(rows)>MAX_HISTORY:raise _Pending('read_incomplete')
        mappings=[];seen=set()
        for row in rows:
            mid=row.get('message_id')
            if not isinstance(mid,str) or not gs.MESSAGE_ID_RE.fullmatch(mid) or mid in seen:raise _Pending('read_incomplete')
            seen.add(mid)
            actual,_=await self._message(mid)
            link=_receipt_link(actual)
            if link and dict(parse_qsl(urlsplit(link).query)).get('e')==eid:
                mappings.append(await self._feishu(mid,proof,set(),expected_event=eid))
        if len(mappings)!=1:raise _Pending()
        return mappings[0]

    async def _resolve(self,value,*,buzz):
        try:
            pattern=HEX if buzz else gs.MESSAGE_ID_RE
            if not isinstance(value,str) or not pattern.fullmatch(value):raise _Pending('input_invalid')
            proof=await self._proof()
            mapping=await self._buzz(value,proof) if buzz else await self._feishu(value,proof,set())
            fresh=await self._proof()
            for pub,metadata in proof.apps.items():
                await self._app(pub,fresh)
                if fresh.apps[pub]!=metadata:raise _Pending('proof_unverified')
            return MappingResolution('verified',mapping)
        except _Pending as exc:return MappingResolution('pending',reason=exc.reason,notice=NOTICE)
        except Exception:return MappingResolution('pending',reason='read_unverified',notice=NOTICE)

    async def resolve_buzz(self,event_id):return await self._resolve(event_id,buzz=True)
    async def resolve_feishu(self,message_id):return await self._resolve(message_id,buzz=False)
