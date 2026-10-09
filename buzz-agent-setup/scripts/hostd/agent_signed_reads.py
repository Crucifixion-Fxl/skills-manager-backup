"""Own-agent bounded signed reads; no owner-key fallback or target authorization.

Constructor identity/path arguments are trusted root-runtime configuration, never
HTTP/business input. Native IO reuses the owner transport's sealed CA, private
stdin, hard child budget and reap discipline with a distinct fixed packet parser.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import buzz_feishu_group_sync as gs
import recovery_authority as authority
from recovery_origin import canonical_origin
from recovery_read_budget import _Budget
from hostd import signed_reads as shared
from hostd.async_io import thread_call
from hostd.safety import read_owned

NOTICE = ('agent 自己的签名读回暂未核实，目标保持待核验。怎么解决：检查这个 agent 自己的受保护 env、空条件 NIP-OA、明确批准的 Relay、签名固定值和私有 CA；非空条件暂不支持，403 不会借用 owner 或同步身份重试。'
          '\n复制给 AI：帮我核查 hostd own-agent 签名读取、完整响应、时限和权限；不要输出密钥、认证内容、消息正文或个人资料。')
HEX = re.compile(r'[0-9a-f]{64}')
CHANNEL = re.compile(r'[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}')


class AgentReadFailure(ValueError):
    status = 'pending'
    def __init__(self): super().__init__(NOTICE)


@dataclass(frozen=True)
class AgentReadRequest:
    origin: str
    key: str = field(repr=False)
    pin: str
    now: int
    agent: str
    owner: str
    auth_tag: tuple = field(repr=False)
    filters: tuple
    operation: str = 'query'

    @classmethod
    def from_data(cls, value):
        try:
            fields = {'version','origin','key','pin','now','operation','agent','owner','auth_tag'}
            if not isinstance(value, dict): raise ValueError
            if value.get('operation') != 'policy_snapshot': fields.add('filters')
            if set(value) != fields: raise ValueError
            if len(json.dumps(value,separators=(',',':')).encode()) > shared.MAX_INPUT: raise ValueError
            if type(value['version']) is not int or value['version'] != 1 or value['operation'] not in ('query','policy_snapshot'): raise ValueError
            origin = canonical_origin(value['origin'])
            if not origin.startswith('https://'): raise ValueError
            key = gs.secret_hex(value['key'], 'own agent')
            if any(not isinstance(value[k], str) or not HEX.fullmatch(value[k]) for k in ('pin','agent','owner')): raise ValueError
            if gs._signer_pubkey(key) != value['agent']: raise ValueError
            auth = value['auth_tag']
            if not isinstance(auth,list) or len(auth) != 4 or not all(isinstance(x,str) for x in auth) or auth[2]: raise ValueError
            if authority.attested_owner({'tags':[auth]},value['agent']) != value['owner']: raise ValueError
            if type(value['now']) is not int or not 0 <= value['now'] <= 2**63-1: raise ValueError
            if value['operation'] == 'policy_snapshot':
                return cls(origin,key,value['pin'],value['now'],value['agent'],value['owner'],tuple(auth),(), 'policy_snapshot')
            filters = value['filters']
            if not isinstance(filters,list) or not 1 <= len(filters) <= 256: raise ValueError
            for row in filters:
                if not isinstance(row,dict) or not row or set(row)-{'kinds','authors','ids','#d','#h','#feishu','#e','since','until','limit'}: raise ValueError
                if 'since' in row and 'until' in row and row['since'] > row['until']: raise ValueError
                for name,items in row.items():
                    if name == 'limit':
                        if type(items) is not int or not 1 <= items <= 257: raise ValueError
                        continue
                    if name in ('since','until'):
                        if type(items) is not int or not 0 <= items <= 2**63-1: raise ValueError
                        continue
                    if not isinstance(items,list) or not 1 <= len(items) <= 256: raise ValueError
                    for item in items:
                        if name == 'kinds':
                            if type(item) is not int or not 0 <= item <= 65535: raise ValueError
                        elif not isinstance(item,str): raise ValueError
                        elif name in ('authors','ids','#e'):
                            if not HEX.fullmatch(item): raise ValueError
                        elif name == '#h':
                            if not CHANNEL.fullmatch(item): raise ValueError
                        elif name == '#feishu':
                            if not gs.MESSAGE_ID_RE.fullmatch(item): raise ValueError
                        elif not 1 <= len(item) <= 256 or any(ord(c)<32 for c in item): raise ValueError
            return cls(origin,key,value['pin'],value['now'],value['agent'],value['owner'],tuple(auth),tuple(json.loads(json.dumps(filters))))
        except Exception: raise AgentReadFailure() from None

    def packet(self):
        value = dict(version=1,origin=self.origin,key=self.key,pin=self.pin,now=self.now,
                     operation=self.operation,agent=self.agent,owner=self.owner,auth_tag=list(self.auth_tag))
        if self.operation == 'query': value['filters'] = list(self.filters)
        return value


def _matches(event, fil):
    for name,values in fil.items():
        if name == 'limit': continue
        if name == 'since' and event['created_at'] < values: return False
        if name == 'until' and event['created_at'] > values: return False
        if name == 'kinds' and event['kind'] not in values: return False
        if name == 'authors' and event['pubkey'] not in values: return False
        if name == 'ids' and event['id'] not in values: return False
        if name.startswith('#') and not any(len(t)>=2 and t[0]==name[1:] and t[1] in values for t in event['tags']): return False
    return True


def _execute(request, http, budget):
    def authenticated(url, headers, timeout, *, body=None):
        # Only the shared fixed query URL/method gets this delegated credential.
        if url != request.origin+'/query' or body is None: raise AgentReadFailure()
        return http(url,dict(headers,**{'x-auth-tag':json.dumps(list(request.auth_tag),separators=(',',':'))}),timeout,body=body)
    rows = shared._execute(request,authenticated,budget)
    # The shared fixed snapshot parser checks the raw 1000 boundary, all
    # signatures, kind, future timestamps and duplicate IDs before returning.
    if request.operation == 'policy_snapshot': return rows
    if (not isinstance(rows,list) or len(rows)>=256
            or any(not any(_matches(e,f) for f in request.filters) for e in rows)
            or any(e['created_at']>request.now+gs.RELAY_CLOCK_SKEW_SECONDS for e in rows)
            or any(sum(_matches(e,f) for e in rows)>=f.get('limit',257) for f in request.filters)):
        raise AgentReadFailure()
    return rows


def child_main(*, http=gs._http_get):
    shared.child_main(http=http,parser=AgentReadRequest.from_data,executor=_execute)


async def child_read(payload, *, command=None, outer_seconds=12):
    """command is an explicit offline subprocess test seam, never runtime config."""
    argv=[sys.executable,str(Path(__file__).resolve())] if command is None else command
    try:
        return await shared.child_read(payload,command=argv,outer_seconds=outer_seconds,parser=AgentReadRequest.from_data)
    except Exception: raise AgentReadFailure() from None


class OwnAgentReader:
    def __init__(self, record, *, origin, relay_pubkey, trusted_relays, clock=lambda: int(time.time()), http=gs._http_get, media_http=None):
        try:
            self.agent,self.owner,self.env_file=record.pubkey,record.owner_pubkey,str(record.env_file)
            if any(not isinstance(pk,str) or not HEX.fullmatch(pk) for pk in (self.agent,self.owner,relay_pubkey)): raise ValueError
            if not Path(self.env_file).is_absolute(): raise ValueError
            if not isinstance(trusted_relays,(tuple,list,set,frozenset)): raise ValueError
            self.origin=canonical_origin(origin)
            if not self.origin.startswith('https://') or self.origin not in {canonical_origin(r) for r in trusted_relays}: raise ValueError
            self.pin,self.trusted_relays=relay_pubkey,tuple(trusted_relays)
            self.clock,self.http=clock,http
            self.transport_mode='native_child' if http is gs._http_get else 'injected_cooperative'
            # Media backend selection is independent of the signed-query HTTP
            # seam. The default always uses the separate native binary child.
            from hostd.agent_media import OwnAgentMediaReader
            self._media_reader = OwnAgentMediaReader(record, origin=self.origin,
                relay_pubkey=relay_pubkey, trusted_relays=self.trusted_relays,
                clock=clock, media_http=media_http)
        except Exception: raise AgentReadFailure() from None

    def _request(self, filters, operation='query'):
        try:
            env={}
            for line in read_owned(self.env_file,max_bytes=65536).decode().splitlines():
                name,sep,value=line.partition('=')
                if sep and name in ('BUZZ_PRIVATE_KEY','BUZZ_AUTH_TAG','BUZZ_RELAY_URL'):
                    if name in env: raise ValueError
                    env[name]=value.strip().strip('"').strip("'")
            if canonical_origin(env['BUZZ_RELAY_URL']) != self.origin: raise ValueError
            value = dict(version=1,origin=self.origin,key=env['BUZZ_PRIVATE_KEY'],pin=self.pin,
                now=self.clock(),operation=operation,agent=self.agent,owner=self.owner,auth_tag=json.loads(env['BUZZ_AUTH_TAG']))
            if operation == 'query': value['filters'] = filters
            return AgentReadRequest.from_data(value)
        except Exception: raise AgentReadFailure() from None

    async def query(self, filters):
        request=await thread_call(self._request,filters)
        return await self._read(request)

    async def policy_snapshot(self):
        request=await thread_call(self._request,None,'policy_snapshot')
        return await self._read(request)

    async def _read(self, request):
        if self.http is gs._http_get: return await child_read(request.packet())
        def injected():
            try:
                budget=_Budget();value=_execute(request,self.http,budget);budget.remaining();return value
            except Exception: raise AgentReadFailure() from None
        return await thread_call(injected)

    async def read_media(self, url, *, sha256, mime, size):
        try:
            return await self._media_reader.read_media(url, sha256=sha256, mime=mime, size=size)
        except Exception:
            raise AgentReadFailure() from None

    async def members(self, channel):
        try:
            if not isinstance(channel,str) or not CHANNEL.fullmatch(channel): raise ValueError
            rows=await self.query([{'kinds':[39002],'authors':[self.pin],'#d':[channel],'limit':257}])
            if not rows: raise ValueError
            for row in rows: authority.exact_tag(row,'d',channel)
            return authority.membership(authority.latest(rows),channel)
        except Exception: raise AgentReadFailure() from None


if __name__ == '__main__': child_main()
