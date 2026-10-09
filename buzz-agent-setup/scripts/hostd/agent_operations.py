"""Authorized local console restart; dispatched uncertainty is never replayed.

SQLite remains on the main loop. Only local file and process observations enter
reaped executor IO. SQL preserves unresolved restart intent and its exact observed invocation. A
fresh driver only observes persisted pins; unpinned uncertainty requires operator
review. Console queues and client operation IDs are still not durable.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import Future
from dataclasses import asdict, dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import time

from .async_io import thread_call
from .console import ActionResult
from .console_operations import ConsoleIntent, current_intent
from .store import RestartProcess
from . import join_effects as effects

NOTICE = ('Agent 重启尚未通过当前授权、空闲状态和实际进程读回核验。\n'
          '怎么解决：核对本机 agent 配置、已授权频道、旧接入任务排除和当前进程；未知重启结果只继续核验，不要重复派发。\n'
          '复制给 AI：帮我检查 hostd 的 Agent 重启授权、空闲状态、新进程身份与全部频道订阅证明；不要输出密钥、凭据、日志正文或个人信息。')
INVOCATION = re.compile(r'[0-9a-f]{32}\Z')


class OperationError(ValueError):
    def __init__(self): super().__init__(NOTICE)


class ScopedProcessOps(effects.ProcessOps):
    """Real ProcessOps with short deadlines and invocation-scoped journal reads."""
    def __init__(self, *args, timeout=5, **kwargs):
        if type(timeout) not in (int,float) or not 0 < timeout <= 10: raise OperationError()
        super().__init__(*args,**kwargs)
        self.timeout=timeout

    def _run(self,argv):
        try:
            result=self.runner(argv,capture_output=True,text=True,timeout=self.timeout,check=False,env=self.env)
            if not isinstance(result.stdout,str) or len(result.stdout)>effects.legacy.LOG_READ_MAX:raise OperationError()
            return result
        except (OSError,subprocess.SubprocessError):raise OperationError() from None

    def unit_status(self,unit):
        result=self._systemctl('show',unit,'-p','LoadState','-p','ActiveState')
        if result.returncode:return 'unknown','unknown'
        values={}
        for line in result.stdout.splitlines():
            key,separator,value=line.partition('=')
            if not separator or key in values:return 'unknown','unknown'
            values[key]=value
        if set(values)!={'LoadState','ActiveState'}:return 'unknown','unknown'
        return values['LoadState'],values['ActiveState']

    def invocation_id(self,unit):
        result=self._systemctl('show',unit,'-p','InvocationID','--value')
        value=result.stdout.strip()
        if result.returncode or not INVOCATION.fullmatch(value) or value=='0'*32:raise OperationError()
        return value

    def journal_invocation(self,unit,pid,invocation):
        if (not effects.legacy.UNIT_RE.fullmatch(unit) or type(pid) is not int or pid<=0
            or not isinstance(invocation,str) or not INVOCATION.fullmatch(invocation)):
            raise OperationError()
        result=self._run([effects.legacy.JOURNALCTL,'--user','-u',unit,f'_PID={pid}',
                          '_SYSTEMD_INVOCATION_ID='+invocation,'-o','cat','--no-pager','--lines=1000'])
        if result.returncode:raise OperationError()
        return result.stdout


@dataclass(frozen=True)
class _Scope:
    spec: effects.AgentSpec
    grants: tuple  # binding/channel/chat/config/sync-app tuples, no message bodies

    @property
    def channels(self):return tuple(sorted(row[1] for row in self.grants))


@dataclass
class _Pending:
    scope: _Scope
    files: str
    old: tuple
    new: tuple | None = None
    operation_id: str = ''
    console_intent: ConsoleIntent | None = None


class _OwnedLock:
    def __init__(self):self.fd=None
    def acquire(self,spec):
        directory=None
        try:
            directory,_=effects._parent(Path(spec.timer_state_dir)/'join.lock')
            if stat.S_IMODE(os.fstat(directory).st_mode)!=0o700:raise OperationError()
            self.fd=os.open('join.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC,0o600,dir_fd=directory)
            meta=os.fstat(self.fd)
            if not stat.S_ISREG(meta.st_mode) or meta.st_uid!=os.geteuid() or stat.S_IMODE(meta.st_mode)!=0o600:raise OperationError()
            fcntl.flock(self.fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except Exception:raise OperationError() from None
        finally:
            if directory is not None:os.close(directory)
    def close(self):
        if self.fd is not None:os.close(self.fd);self.fd=None


class AgentRestartDriver:
    """Async pubkey-only driver; exact local spec and active grants authorize IO.

    spec_getter is a synchronous main-loop getter for the current verified local
    AgentSpec, not a filesystem loader. Bare ProcessOps without the invocation
    methods fails closed. No caller-supplied unit, name or arbitrary argv exists.
    """
    def __init__(self,store,spec_getter,*,operations=None,observations=3):
        if not callable(spec_getter) or type(observations) is not int or not 1<=observations<=5:raise OperationError()
        self.store,self.spec_getter=store,spec_getter
        self.ops=operations if operations is not None else ScopedProcessOps()
        self.observations=observations
        self.last_notice=''
        self._active=set();self._pending={}
        self._loop=None
        self.authorization_timeout=5

    def _scope(self,pubkey):
        effects.hexid(pubkey)
        agent=self.store.conn.execute('SELECT * FROM agent WHERE pubkey=?',(pubkey,)).fetchone()
        if not agent or agent['status']!='active':raise OperationError()
        spec=self.spec_getter(pubkey)
        if (not isinstance(spec,effects.AgentSpec) or spec.pubkey!=pubkey or spec.owner_pubkey!=agent['owner_pubkey']
            or spec.app_id!=agent['app_id'] or spec.env_file!=agent['config_path']):raise OperationError()
        rows=self.store.conn.execute("""SELECT b.binding_id,b.channel_id,b.chat_id,b.config_path,b.sync_app_id,
          b.chat_ref AS binding_ref,b.status AS binding_status,ac.chat_ref FROM agent_chat ac
          JOIN binding b ON b.binding_id=ac.binding_id
          WHERE ac.agent_id=? AND ac.status='active' ORDER BY b.binding_id""",(pubkey,)).fetchall()
        if not rows or len(rows)>64:raise OperationError()
        grants=[]
        for row in rows:
            channel=effects._channel(row['channel_id']);ref=effects.gs.chat_ref(row['chat_id'])
            if row['binding_status']!='active' or row['chat_ref']!=ref or row['binding_ref'] not in ('',ref):raise OperationError()
            grants.append((row['binding_id'],channel,row['chat_id'],row['config_path'],row['sync_app_id']))
        if len({row[1] for row in grants})!=len(grants):raise OperationError()
        return _Scope(spec,tuple(grants))

    def _console_record(self,pubkey,intent,*,linked=None):
        # A correctly shaped DTO alone is not a dispatch capability. It must
        # be the current fixed dispatcher's context and exact claimed SQL row.
        if type(intent) is not ConsoleIntent or current_intent()!=intent:return None
        row=self.store.console_operation(intent.operation_id,intent.principal)
        if (row is None or row.status!='dispatched' or ConsoleIntent.from_record(row)!=intent
            or intent.kind!='agent' or intent.action!='restart' or intent.target!=pubkey):return None
        if linked is not None and row.restart_operation_id!=linked:return None
        return row

    async def restart_for_console(self,pubkey,intent):
        loop=asyncio.get_running_loop()
        if self._loop is None:self._loop=loop
        if loop is not self._loop:return self._result('failed')
        try:
            row=self._console_record(pubkey,intent)
            if row is None:return self._result('failed')
            self._scope(pubkey)
            if row.restart_operation_id is not None:
                linked=self.store.restart_record(row.restart_operation_id)
                if linked is not None and linked.agent_id==pubkey and linked.state=='acked':
                    return self._result('restarted')  # Exact associated ACK, no fresh process action.
        except Exception:return self._result('failed')
        return await self._run(pubkey,intent)

    @staticmethod
    def _scope_hash(scope):
        value={'spec':asdict(scope.spec),'grants':scope.grants}
        return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()

    @staticmethod
    def _files(scope):
        spec=scope.spec
        paths=(spec.env_file,spec.prompt_file,spec.responsible_file,spec.timer_config)
        raw=[effects.read_owned(path) for path in paths]
        env=effects.legacy.parse_env(raw[0].decode())
        channels=effects.legacy._allowlist(env.get('BUZZ_ACP_CHANNELS',''))
        if (len(channels)!=len(set(channels)) or set(channels)!=set(scope.channels)
            or effects.gs._signer_pubkey(effects.gs.secret_hex(env.get('BUZZ_PRIVATE_KEY'),'agent'))!=spec.pubkey
            or env.get('BUZZ_ACP_AGENT_OWNER')!=spec.owner_pubkey
            or env.get('BUZZ_ACP_SYSTEM_PROMPT_FILE')!=spec.prompt_file
            or env.get('BUZZ_RESPONSIBLE_CONFIG')!=spec.responsible_file):raise OperationError()
        responsible=json.loads(raw[2]);prompt=raw[1].decode()
        if (not isinstance(responsible,dict) or responsible.get('channels')!=list(dict.fromkeys(responsible.get('channels',[])))
            or set(responsible.get('channels',[]))!=set(scope.channels)
            or prompt.count(effects.legacy.PROMPT_BEGIN)!=1 or prompt.count(effects.legacy.PROMPT_END)!=1):raise OperationError()
        begin,end=prompt.index(effects.legacy.PROMPT_BEGIN),prompt.index(effects.legacy.PROMPT_END)
        if begin>=end or any(channel not in prompt[begin:end] for channel in scope.channels):raise OperationError()
        effects.JoinEffects._timer_excluded(spec)
        for _,channel,chat,path,app in scope.grants:
            value=effects.read_owned(path);config=json.loads(value)
            sync_app=config.get('sync_app_id') or (config.get('agents',{}).get(config.get('desk_pubkey'),{}) or {}).get('app_id')
            if config.get('channel_id')!=channel or config.get('chat_id')!=chat or sync_app!=app:raise OperationError()
            raw.append(value)
        # Transient local CAS only; no raw file/environment is retained in an
        # operation record or added to Store, notices, logs or ActionResult.
        return hashlib.sha256(b''.join(len(value).to_bytes(8,'big')+value for value in raw)).hexdigest()

    def _capture(self,scope):
        unit=scope.spec.unit
        if self.ops.unit_status(unit)!=('loaded','active'):raise OperationError()
        invocation=self.ops.invocation_id(unit)
        if not isinstance(invocation,str) or not INVOCATION.fullmatch(invocation) or invocation=='0'*32:raise OperationError()
        pid,start,env=self.ops.process(unit)
        if type(pid) is not int or pid<=0 or type(start) is not int or start<=0 or not isinstance(env,dict):raise OperationError()
        again=self.ops.process(unit)
        if again[:2]!=(pid,start) or self.ops.invocation_id(unit)!=invocation:raise OperationError()
        identity=(pid,start,invocation)
        good=(env.get('BUZZ_ACP_AGENT_OWNER')==scope.spec.owner_pubkey
              and effects.gs._signer_pubkey(effects.gs.secret_hex(env.get('BUZZ_PRIVATE_KEY'),'agent'))==scope.spec.pubkey
              and env.get('BUZZ_ACP_SYSTEM_PROMPT_FILE')==scope.spec.prompt_file
              and env.get('BUZZ_RESPONSIBLE_CONFIG')==scope.spec.responsible_file
              and set(effects.legacy._allowlist(env.get('BUZZ_ACP_CHANNELS','')))==set(scope.channels))
        return identity,good

    def _prepare(self,scope,lock):
        lock.acquire(scope.spec)
        files=self._files(scope);old,good=self._capture(scope)
        if not good:raise OperationError()
        return files,old

    def _idle(self,scope,files,old):
        if self._files(scope)!=files or self._capture(scope)!=(old,True):raise OperationError()
        return self.ops.is_busy(scope.spec.unit) is False

    def _observe(self,pending,*,pin=False):
        for _ in range(self.observations):
            try:
                if self._files(pending.scope)!=pending.files:return False
                identity,good=self._capture(pending.scope)
                if pending.new is None:
                    if not pin:return False  # Unknown cannot adopt a later unrelated restart.
                    if identity[:2]==pending.old[:2] or identity[2]==pending.old[2]:continue
                    if not self._pin_observed(pending,identity):return False
                    pending.new=identity
                if identity!=pending.new or not good:return False
                pid,_,invocation=identity
                log=self.ops.journal_invocation(pending.scope.spec.unit,pid,invocation)
                if not isinstance(log,str) or len(log)>effects.legacy.LOG_READ_MAX:return False
                complete=all(re.search(r'(?:^|\s)subscribed to channel '+re.escape(channel)+r'(?=$|\s)',log)
                             for channel in pending.scope.channels)
                if complete and self._capture(pending.scope)==(identity,True) and self._files(pending.scope)==pending.files:return True
            except Exception:
                continue
        return False

    def _pin_observed(self,pending,identity):
        # Dispatched IO is reaped even after cancellation; preserve its first
        # observed tuple on the owning loop before returning any proof.
        answer=Future();deadline=time.monotonic()+5
        def _pin_current():
            if answer.done():return
            result=False
            try:
                if time.monotonic()<deadline:
                    result=self.store.pin_restart(pending.operation_id,pending.scope.spec.pubkey,
                         self._scope_hash(pending.scope),pending.files,RestartProcess(*identity),now=int(time.time()))
                if not answer.done():answer.set_result(result)
            except Exception:
                try:
                    if not answer.done():answer.set_result(False)
                except Exception:pass
        try:
            self._loop.call_soon_threadsafe(_pin_current)
            return answer.result(timeout=5) is True
        except Exception:
            answer.cancel()
            return False

    def _authorize_dispatch(self,pubkey,scope,files,old,operation,console_intent=None):
        """Final SQL authorization and durable reservation, before restart.

        Failed or late handoffs never dispatch. If the reservation committed
        before its waiter expired, the intent remains held for operator review.
        Only this newly created live reservation may ever dispatch or first-pin.
        """
        timeout=self.authorization_timeout
        if type(timeout) not in (int,float) or not 0 < timeout <= 5:return None
        answer=Future();deadline=time.monotonic()+timeout
        def _admit():
            if answer.done():return
            record=None
            try:
                if (time.monotonic()<deadline and not operation.done() and not operation.cancelling()
                    and self._scope(pubkey)==scope and time.monotonic()<deadline):
                    digest=self._scope_hash(scope)
                    # Store enforces fresh reserve/mark/link provenance in this
                    # exact outer transaction; any failed link rolls back all.
                    with self.store.transaction():
                        if console_intent is not None:
                            queued=self._console_record(pubkey,console_intent)
                            if queued is None or queued.restart_operation_id is not None:raise OperationError()
                        reservation=self.store.reserve_restart(secrets.token_hex(32),pubkey,digest,files,
                                                               RestartProcess(*old),now=int(time.time()))
                        if (reservation.created and time.monotonic()<deadline
                            and not operation.cancelling() and self._scope(pubkey)==scope
                            and self.store.mark_restart_unknown(reservation.record.operation_id,pubkey,digest,files,
                                                                now=int(time.time()))):
                            if console_intent is not None:
                                if not self.store.link_console_restart(console_intent.operation_id,reservation.record.operation_id,
                                                scope_hash=digest,protectedfiles_hash=files,now=int(time.time())):
                                    raise OperationError()
                            if time.monotonic()<deadline:
                                record=self.store.restart_record(reservation.record.operation_id)
                if not answer.done():answer.set_result(record)
            except Exception:
                try:
                    if not answer.done():answer.set_result(None)
                except Exception:pass
        try:
            self._loop.call_soon_threadsafe(_admit)
            return answer.result(timeout=timeout)
        except Exception:
            answer.cancel()
            return None

    def _authorize_live_dispatch(self,pending,operation):
        """Pure SQL guard for this freshly reserved live operation only."""
        timeout=self.authorization_timeout
        if type(timeout) not in (int,float) or not 0 < timeout <= 5:return False
        answer=Future();deadline=time.monotonic()+timeout
        def _admit_current():
            if answer.done():return
            allowed=False
            try:
                if (time.monotonic()<deadline and not operation.done() and not operation.cancelling()
                    and self._scope(pending.scope.spec.pubkey)==pending.scope):
                    record=self.store.restart_record(pending.operation_id)
                    allowed=(record is not None and record.state=='unknown' and record.new_process is None
                             and record.agent_id==pending.scope.spec.pubkey
                             and record.scope_hash==self._scope_hash(pending.scope)
                             and record.protectedfiles_hash==pending.files
                             and record.old_process==RestartProcess(*pending.old)
                             and (pending.console_intent is None or self._console_record(pending.scope.spec.pubkey,
                                                   pending.console_intent,linked=pending.operation_id) is not None)
                             and time.monotonic()<deadline)
                if not answer.done():answer.set_result(allowed)
            except Exception:
                try:
                    if not answer.done():answer.set_result(False)
                except Exception:pass
        try:
            self._loop.call_soon_threadsafe(_admit_current)
            return answer.result(timeout=timeout) is True
        except Exception:
            answer.cancel()
            return False

    def _dispatch(self,pubkey,scope,files,old,operation,console_intent=None):
        # Revalidate the original file/process identity after awaited preflight
        # and SQL, then perform both idle checks immediately before restart.
        if self._files(scope)!=files or self._capture(scope)!=(old,True):raise OperationError()
        if self.ops.is_busy(scope.spec.unit) is not False:return 'waiting_idle'
        if self.ops.is_busy(scope.spec.unit) is not False:return 'waiting_idle'
        record=self._authorize_dispatch(pubkey,scope,files,old,operation,console_intent)
        if record is None:return 'failed'
        pending=_Pending(scope,files,old,operation_id=record.operation_id,console_intent=console_intent)
        self._pending[pubkey]=pending
        # The durable SQL handoff may have waited. Refresh physical evidence
        # afterwards, then authorize the same live reservation with pure SQL.
        # A changed/busy reservation remains held; it is never erased/replayed.
        if self._files(scope)!=files or self._capture(scope)!=(old,True):return 'waiting_verify'
        if self.ops.is_busy(scope.spec.unit) is not False:return 'waiting_verify'
        if self.ops.is_busy(scope.spec.unit) is not False:return 'waiting_verify'
        if not self._authorize_live_dispatch(pending,operation):return 'waiting_verify'
        try:self.ops.restart(scope.spec.unit)
        except Exception:pass  # Unknown outcome is independently observed; never automatically replayed.
        return 'restarted' if self._observe(pending,pin=True) else 'waiting_verify'

    def _result(self,observed):
        self.last_notice='' if observed=='restarted' else NOTICE
        return ActionResult(observed=='restarted',observed)

    async def _operate(self,pubkey,scope,lock,console_intent=None):
        record=self.store.active_restart(pubkey)
        if record is not None:
            if (console_intent is not None and self._console_record(pubkey,console_intent,
                                                     linked=record.operation_id) is None):return 'waiting_verify'
            if record.scope_hash!=self._scope_hash(scope):return 'waiting_verify'
            old=(record.old_process.pid,record.old_process.start,record.old_process.invocation)
            new=(None if record.new_process is None else
                 (record.new_process.pid,record.new_process.start,record.new_process.invocation))
            pending=_Pending(scope,record.protectedfiles_hash,old,new,record.operation_id,console_intent)
            self._pending[pubkey]=pending
            if record.state!='unknown' or new is None:return 'waiting_verify'
            await thread_call(lock.acquire,scope.spec)
            observed=await thread_call(self._observe,pending)
            if self._scope(pubkey)!=scope:return 'waiting_verify'
            return 'restarted' if observed else 'waiting_verify'
        files,old=await thread_call(self._prepare,scope,lock)
        if self._scope(pubkey)!=scope:return 'failed'
        if not await thread_call(self._idle,scope,files,old):return 'waiting_idle'
        if self._scope(pubkey)!=scope:return 'failed'
        observed=await thread_call(self._dispatch,pubkey,scope,files,old,asyncio.current_task(),console_intent)
        if self._scope(pubkey)!=scope:return 'waiting_verify'
        return observed

    async def __call__(self,pubkey):
        return await self._run(pubkey)

    async def _run(self,pubkey,console_intent=None):
        loop=asyncio.get_running_loop()
        if self._loop is None:self._loop=loop
        if loop is not self._loop:return self._result('failed')
        try:scope=self._scope(pubkey)
        except Exception:return self._result('failed')
        if pubkey in self._active:return self._result('waiting_verify')
        self._active.add(pubkey);lock=_OwnedLock()
        try:
            try:
                observed=await self._operate(pubkey,scope,lock,console_intent)
            except asyncio.CancelledError:
                self.last_notice=NOTICE
                raise
            except Exception:
                observed='waiting_verify' if pubkey in self._pending else 'failed'
            finally:
                try:
                    await thread_call(lock.close)
                except asyncio.CancelledError:
                    self.last_notice=NOTICE
                    raise
            if observed=='restarted':
                try:
                    pending=self._pending[pubkey]
                    # Cleanup was an await boundary. Refresh bounded physical
                    # files/process/invocation/journal evidence off-loop before
                    # the final synchronous SQL scope check and exact ACK.
                    if (not await thread_call(self._observe,pending)
                        or self._scope(pubkey)!=pending.scope or pending.new is None
                        or (pending.console_intent is not None and self._console_record(pubkey,pending.console_intent,
                                                                     linked=pending.operation_id) is None)
                        or not self.store.ack_restart(pending.operation_id,pubkey,self._scope_hash(pending.scope),
                                                      pending.files,RestartProcess(*pending.new),now=int(time.time()))):
                        observed='waiting_verify'
                    else:self._pending.pop(pubkey,None)
                except asyncio.CancelledError:
                    self.last_notice=NOTICE
                    raise
                except Exception:observed='waiting_verify'
            # No await after ACK. Cancellation during final physical IO is
            # reaped and preserves the durable pin for readback, never restart.
            return self._result(observed)
        finally:self._active.discard(pubkey)
