"""Bounded read-only signed transport; production uses an isolated child main.

The injected HTTP seam is explicitly cooperative and offline: it runs the SAME
signed parser/capacity budget in a reaped thread, without claiming SIGALRM safety.
Production always uses a fresh child with the unchanged hard read_budget. Neither
backend can access SQL or publish events. Credentials only cross private stdin.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import fcntl
import os
from pathlib import Path
import re
import selectors
import ssl
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import buzz_feishu_group_sync as gs
from recovery_relay import RecoveryRelay, canonical_origin
from recovery_read_budget import read_budget, _Budget
try:
    from .async_io import thread_call
    from .safety import read_owned
except ImportError:
    from async_io import thread_call
    from safety import read_owned

MAX_INPUT = 65536
MAX_OUTPUT = 1024 * 1024
NOTICE = ('接入签名读回暂时无法安全完成，申请保持待核验。怎么解决：检查明确配置的 Relay、签名固定值、完整响应、读取子进程及明确配置的 0600 CA 信任文件；恢复后重新核验。'
          '\n复制给 AI：帮我核查 hostd 签名读回时限、SSL_CERT_FILE 信任文件、子进程及授权频道证据；不要输出密钥、凭据、消息正文或个人资料。')


class ReadFailure(ValueError):
    def __init__(self): super().__init__(NOTICE)


@dataclass(frozen=True)
class ReadRequest:
    origin: str
    key: str = field(repr=False)
    pin: str
    now: int
    operation: str
    filters: tuple = ()
    channel: str = ''

    @classmethod
    def from_data(cls, value):
        try:
            if not isinstance(value, dict): raise ValueError
            fields = {'version','origin','key','pin','now','operation'}
            if value.get('operation') != 'policy_snapshot':
                fields.add('filters' if value.get('operation') == 'query' else 'channel')
            if set(value) != fields: raise ValueError
            if type(value['version']) is not int or value['version'] != 1: raise ValueError
            origin = canonical_origin(value['origin'])
            key = gs.secret_hex(value['key'], 'owner')
            if not isinstance(value['pin'], str) or not re.fullmatch('[0-9a-f]{64}', value['pin']): raise ValueError
            if type(value['now']) is not int or not 0 <= value['now'] <= 2**63-1: raise ValueError
            operation = value['operation']
            if operation == 'policy_snapshot':
                return cls(origin,key,value['pin'],value['now'],operation)
            if operation == 'query':
                filters = value['filters']
                if not isinstance(filters, list) or not 1 <= len(filters) <= 256: raise ValueError
                for row in filters:
                    if not isinstance(row, dict) or not row or set(row) - {'kinds','authors','ids','#d','#e','#h','limit'}: raise ValueError
                    for k,v in row.items():
                        if k == 'limit':
                            if type(v) is not int or not 1 <= v <= 257: raise ValueError
                        elif not isinstance(v, list) or not 1 <= len(v) <= 256: raise ValueError
                        elif k == 'kinds':
                            if any(type(n) is not int or not 0 <= n <= 65535 for n in v): raise ValueError
                        elif k in ('authors','ids','#e'):
                            if any(not isinstance(s,str) or not re.fullmatch('[0-9a-f]{64}',s) for s in v): raise ValueError
                        elif k == '#h':
                            if any(not isinstance(s,str) or not re.fullmatch(r'[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}',s) for s in v): raise ValueError
                        elif any(not isinstance(s,str) or not 1 <= len(s) <= 256 or any(ord(c)<32 for c in s) for s in v): raise ValueError
                return cls(origin,key,value['pin'],value['now'],operation,tuple(json.loads(json.dumps(filters))))
            if operation != 'people' or not isinstance(value['channel'],str) or not re.fullmatch(r'[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}',value['channel']): raise ValueError
            return cls(origin,key,value['pin'],value['now'],operation,channel=value['channel'])
        except Exception: raise ReadFailure() from None

    def packet(self):
        value = dict(version=1,origin=self.origin,key=self.key,pin=self.pin,now=self.now,operation=self.operation)
        if self.operation == 'query': value.update(filters=list(self.filters))
        elif self.operation != 'policy_snapshot': value.update(channel=self.channel)
        return value


def _event_result_shape(event):
    """A signature alone does not establish a complete NIP-01 event shape.

    The existing relay parser verifies canonical IDs/signatures. Keep this
    stricter boundary local to these readers, including the delegated reader.
    Whole-response bytes remain bounded by the shared read budget.
    """
    return (isinstance(event,dict)
        and all(isinstance(event.get(name),str) and re.fullmatch(pattern,event[name])
                for name,pattern in (('id','[0-9a-f]{64}'),('pubkey','[0-9a-f]{64}'),('sig','[0-9a-f]{128}')))
        and type(event.get('kind')) is int and 0 <= event['kind'] <= 65535
        and type(event.get('created_at')) is int and 0 <= event['created_at'] <= 2**63-1
        and isinstance(event.get('content'),str)
        and isinstance(event.get('tags'),list)
        and all(isinstance(tag,list) and tag and all(isinstance(value,str) for value in tag)
                for tag in event['tags']))


def _execute(request, http, budget):
    now = lambda: datetime.fromtimestamp(request.now, timezone.utc)
    relay = RecoveryRelay(request.origin,request.key,request.pin,http=http,now=now)
    if request.operation in ('query', 'policy_snapshot'):
        rows=(relay._policy_snapshot(budget) if request.operation == 'policy_snapshot'
              else relay._query(list(request.filters),budget))
        if not isinstance(rows,list) or any(not _event_result_shape(event) for event in rows):
            raise ReadFailure()
        if request.operation == 'policy_snapshot' and (
                len({e['id'] for e in rows}) != len(rows)
                or any(e['kind'] != 30177 or e['created_at'] > request.now + gs.RELAY_CLOCK_SKEW_SECONDS for e in rows)):
            raise ReadFailure()
        return rows
    url = gs.people_url(request.origin,request.channel)
    headers = {'Authorization':gs.nip98_header(request.key,'GET',url,now()),'Accept':'application/json'}
    status,body = http(url,headers,budget.request()); budget.response(len(body))
    if status != 200: raise ReadFailure()
    answer = gs.parse_people_response(body,request.channel)
    if not isinstance(answer.union_ids,dict): raise ReadFailure()
    for pk,union in answer.union_ids.items():
        if not isinstance(pk,str) or not re.fullmatch('[0-9a-f]{64}',pk) or not isinstance(union,str) or not re.fullmatch('on_[A-Za-z0-9]+',union): raise ReadFailure()
    if len(set(answer.union_ids.values())) != len(answer.union_ids): raise ReadFailure()
    return answer.union_ids


def child_main(*, http=gs._http_get, parser=ReadRequest.from_data, executor=_execute):
    """Fixed process entry; only the explicit test script injects offline HTTP."""
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT+1)
        if len(raw)>MAX_INPUT: raise ReadFailure()
        request = parser(json.loads(raw))
        with read_budget() as budget: value = executor(request,http,budget)
        result = json.dumps({'ok':True,'value':value},separators=(',',':')).encode()
        if len(result)>MAX_OUTPUT: raise ReadFailure()
    except BaseException:
        result = b'{"ok":false,"value":null}'
    sys.stdout.buffer.write(result); sys.stdout.buffer.flush()


def _trust_bundle():
    path=os.environ.get('SSL_CERT_FILE')
    if path is None:return None
    if not path or not Path(path).is_absolute():raise ReadFailure()
    value=read_owned(path)
    # Validate before child dispatch, without disabling hostname or chain checks.
    ssl.create_default_context(cadata=value.decode('ascii'))
    fd=os.memfd_create('hostd-trust',os.MFD_CLOEXEC|os.MFD_ALLOW_SEALING)
    try:
        os.fchmod(fd,0o600);offset=0
        while offset<len(value):offset+=os.write(fd,value[offset:offset+65536])
        os.lseek(fd,0,os.SEEK_SET)
        fcntl.fcntl(fd,fcntl.F_ADD_SEALS,fcntl.F_SEAL_WRITE|fcntl.F_SEAL_GROW|fcntl.F_SEAL_SHRINK|fcntl.F_SEAL_SEAL)
        return fd
    except BaseException:
        os.close(fd);raise


def _child_read(payload, command, outer_seconds, parser=ReadRequest.from_data):
    process = None;trust_fd = None
    try:
        request = parser(payload)
        raw = json.dumps(request.packet(),separators=(',',':')).encode()
        if len(raw)>MAX_INPUT or not 0 < outer_seconds <= 12: raise ReadFailure()
        argv = [sys.executable,str(Path(__file__).resolve())] if command is None else list(command)
        trust_fd=_trust_bundle()
        environment={'PATH':os.defpath,'LANG':'C.UTF-8'}
        if trust_fd is not None:environment['SSL_CERT_FILE']='/proc/self/fd/'+str(trust_fd)
        process = subprocess.Popen(argv,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
            env=environment,pass_fds=() if trust_fd is None else (trust_fd,),close_fds=True)
        deadline = time.monotonic()+outer_seconds; output = bytearray(); offset = 0
        os.set_blocking(process.stdin.fileno(),False); os.set_blocking(process.stdout.fileno(),False)
        with selectors.DefaultSelector() as select:
            select.register(process.stdin,selectors.EVENT_WRITE); select.register(process.stdout,selectors.EVENT_READ)
            while select.get_map():
                remaining = deadline-time.monotonic()
                if remaining<=0: raise ReadFailure()
                for key,mask in select.select(min(remaining,.2)):
                    if key.fileobj is process.stdin:
                        written = os.write(process.stdin.fileno(),raw[offset:offset+4096]); offset += written
                        if offset == len(raw): select.unregister(process.stdin); process.stdin.close()
                    else:
                        chunk = os.read(process.stdout.fileno(),65536)
                        if not chunk: select.unregister(process.stdout); process.stdout.close()
                        else:
                            output.extend(chunk)
                            if len(output)>MAX_OUTPUT: raise ReadFailure()
            if process.wait(timeout=max(.001,deadline-time.monotonic())) != 0: raise ReadFailure()
        result = json.loads(output)
        if (not isinstance(result,dict) or set(result)!={'ok','value'} or result['ok'] is not True
                or not isinstance(result['value'],(list,dict))): raise ReadFailure()
        return result['value']
    except BaseException: raise ReadFailure() from None
    finally:
        try:
            if process is not None:
                try:
                    if process.poll() is None: process.kill()
                    process.wait()
                finally:
                    for stream in (process.stdin,process.stdout):
                        if stream and not stream.closed: stream.close()
        finally:
            if trust_fd is not None:os.close(trust_fd)


async def child_read(payload, *, command=None, outer_seconds=12, parser=ReadRequest.from_data):
    """command/outer_seconds are low-level offline subprocess test seams only."""
    return await thread_call(_child_read,payload,command,outer_seconds,parser)


class SignedReader:
    def __init__(self, relay):
        self.relay = relay
        self.transport_mode = 'native_child' if relay.http is gs._http_get else 'injected_cooperative'

    async def read(self, operation, **kwargs):
        request = ReadRequest.from_data(dict(version=1,origin=self.relay.origin,key=self.relay.key,
            pin=self.relay.relay_pubkey,now=int(self.relay.now().timestamp()),operation=operation,**kwargs))
        if self.relay.http is gs._http_get: return await child_read(request.packet())
        # Explicit injected transport only. No signal emulation, daemon fork or
        # copied Store. _Budget enforces elapsed/capacity before/after each fake I/O;
        # thread_call reaps it even after repeated caller cancellation.
        def injected():
            try:
                budget=_Budget(); value=_execute(request,self.relay.http,budget); budget.remaining(); return value
            except Exception: raise ReadFailure() from None
        return await thread_call(injected)


if __name__ == '__main__': child_main()
