"""Owned Chrome/CDP pipe: interception precedes navigation, secrets stay in memory."""
from __future__ import annotations
import asyncio
from dataclasses import dataclass,field
import hashlib
import fcntl
import json
import os
from pathlib import Path
import re
import select
import signal
import stat
import time
from urllib.parse import urlsplit
from .console_access import ConsoleAccess,AccessError,_open,NOTICE,FRAME_LIMIT
from .async_io import thread_call

class BrowserError(ValueError):
    def __init__(self):super().__init__(NOTICE)


def _program(path,digest):
    path=Path(path)
    if not path.is_absolute() or '..' in path.parts or not re.fullmatch(r'[0-9a-f]{64}',digest):raise BrowserError()
    # Program may be root-owned/executable; ancestors must never be symlinks.
    fd=os.open(path.anchor,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        for i,part in enumerate(path.parts[1:]):
            flags=os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC
            if i<len(path.parts)-2:flags|=os.O_DIRECTORY
            child=os.open(part,flags,dir_fd=fd);os.close(fd);fd=child
        meta=os.fstat(fd)
        if not stat.S_ISREG(meta.st_mode) or meta.st_uid not in (0,os.geteuid()) or meta.st_mode&0o022 or not os.access(path,os.X_OK) or meta.st_size>512*1024*1024:raise BrowserError()
        with os.fdopen(os.dup(fd),'rb') as stream:
            prefix=stream.read(4);stream.seek(0);actual=hashlib.file_digest(stream,'sha256').hexdigest()
        if prefix!=b'\x7fELF' or actual!=digest:raise BrowserError()
        return meta.st_dev,meta.st_ino
    finally:os.close(fd)

@dataclass(frozen=True)
class BrowserPlan:
    access:ConsoleAccess=field(repr=False,compare=False)
    chrome_binary:Path
    chrome_sha256:str
    profile_dir:Path
    _inode:tuple=field(repr=False)
    @classmethod
    def check(cls,access,*,chrome_binary,chrome_sha256,profile_dir):
        try:
            if not isinstance(access,ConsoleAccess) or access._server is None:raise BrowserError()
            access._validate();profile=Path(profile_dir)
            if profile.parent!=access.client_dir or not re.fullmatch(r'[a-z][a-z0-9-]{0,63}',profile.name) or os.path.lexists(profile):raise BrowserError()
            fd=_open(profile.parent,directory=True);os.close(fd)
            inode=_program(chrome_binary,chrome_sha256)
            return cls(access,Path(chrome_binary),chrome_sha256,profile,inode)
        except Exception:raise BrowserError() from None
    def validate(self,*,created=False):
        self.access._validate()
        if _program(self.chrome_binary,self.chrome_sha256)!=self._inode:raise BrowserError()
        if created:
            fd=_open(self.profile_dir,directory=True);os.close(fd)
        elif os.path.lexists(self.profile_dir):raise BrowserError()
    def readback(self):return {'status':'prepared','live_verified':False,'chrome_sha256':self.chrome_sha256,'remote_ssh':'pending'}

class FetchGuard:
    def __init__(self,access,send):
        if not isinstance(access,ConsoleAccess) or not callable(send):raise BrowserError()
        self.access=access;self.send=send;self._sessions=set()
    async def attach(self,session,*,target_type):
        if not isinstance(session,str) or not 1<=len(session)<=256 or target_type not in ('page','iframe','worker','service_worker','shared_worker','background_page','browser_ui','other'):raise BrowserError()
        await self.send(session,'Fetch.enable',{'patterns':[{'urlPattern':'*','requestStage':'Request'}]})
        self._sessions.add(session)
        await self.send(session,'Runtime.runIfWaitingForDebugger',{})
    async def handle(self,session,event):
        try:
            if session not in self._sessions or event.get('method')!='Fetch.requestPaused':raise BrowserError()
            params=event['params'];request=params['request'];url=request['url'];headers=request['headers']
            if not isinstance(url,str) or len(url)>8192 or not isinstance(headers,dict) or len(headers)>64 or not isinstance(params['requestId'],str):raise BrowserError()
            parsed=urlsplit(url)
            exact=(parsed.scheme=='http' and parsed.netloc==self.access.origin[7:] and not parsed.username and not parsed.password)
            output=[]
            for name,value in headers.items():
                if not isinstance(name,str) or not isinstance(value,str) or any(ord(c)<32 or ord(c)==127 for c in name+value):raise BrowserError()
                if name.lower()!='authorization':output.append({'name':name,'value':value})
            if exact:
                self.access._validate()
                output.append({'name':'Authorization','value':'Bearer '+self.access._token})
            await self.send(session,'Fetch.continueRequest',{'requestId':params['requestId'],'headers':output})
        except asyncio.CancelledError:raise
        except Exception:raise BrowserError() from None

class _Process:
    def __init__(self,pid):self.pid=pid;self.returncode=None
    def poll(self):
        if self.returncode is None:
            try:pid,status=os.waitpid(self.pid,os.WNOHANG)
            except ChildProcessError:raise BrowserError() from None
            if pid:self.returncode=os.waitstatus_to_exitcode(status)
        return self.returncode

class NativeBrowserOps:
    def spawn(self,argv,*,env,read_fd,write_fd):
        # Chrome uses child descriptors 3(read) and 4(write). posix_spawn avoids
        # running a Python preexec callback in a multithreaded process.
        null=os.open('/dev/null',os.O_RDWR|os.O_CLOEXEC)
        read_copy=write_copy=None
        try:
            read_copy=fcntl.fcntl(read_fd,fcntl.F_DUPFD_CLOEXEC,5)
            write_copy=fcntl.fcntl(write_fd,fcntl.F_DUPFD_CLOEXEC,5)
            actions=[(os.POSIX_SPAWN_DUP2,null,0),(os.POSIX_SPAWN_DUP2,null,1),(os.POSIX_SPAWN_DUP2,null,2),
                     (os.POSIX_SPAWN_DUP2,read_copy,3),(os.POSIX_SPAWN_DUP2,write_copy,4)]
            return _Process(os.posix_spawn(argv[0],argv,env,file_actions=actions,setsid=True))
        finally:
            os.close(null)
            if read_copy is not None:os.close(read_copy)
            if write_copy is not None:os.close(write_copy)
    def observe(self,process):
        fd=exe=None
        try:
            fd=os.open('/proc/'+str(process.pid),os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
            uid=os.fstat(fd).st_uid
            if uid!=os.geteuid():raise BrowserError()
            def read(name):
                child=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=fd)
                with os.fdopen(child,'rb') as stream:raw=stream.read(65537)
                if len(raw)>65536:raise BrowserError()
                return raw
            ticks=int(read('stat').decode().rsplit(')',1)[1].split()[19])
            executable=os.readlink('exe',dir_fd=fd)
            exe=os.open('exe',os.O_RDONLY|os.O_CLOEXEC,dir_fd=fd)
            meta=os.fstat(exe)
            if not stat.S_ISREG(meta.st_mode) or meta.st_size>512*1024*1024:raise BrowserError()
            with os.fdopen(os.dup(exe),'rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
            raw=read('cmdline')
            if not raw.endswith(b'\0'):raise BrowserError()
            argv=tuple(part.decode() for part in raw[:-1].split(b'\0'))
            if int(read('stat').decode().rsplit(')',1)[1].split()[19])!=ticks:raise BrowserError()
            return {'pid':process.pid,'start':ticks,'uid':uid,'executable':executable,'sha256':digest,'argv':argv,'alive':process.poll() is None}
        except Exception:raise BrowserError() from None
        finally:
            if exe is not None:os.close(exe)
            if fd is not None:os.close(fd)
    def terminate(self,process):
        if self.observe(process)!=getattr(process,'_ownership',None):raise BrowserError()
        if os.getpgid(process.pid)!=process.pid:raise BrowserError()
        os.killpg(process.pid,signal.SIGTERM)
    def wait(self,process,timeout):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            status=process.poll()
            if status is not None:
                try:os.killpg(process.pid,0)
                except ProcessLookupError:return status
            time.sleep(.02)
        return None

class _Pipe:
    def __init__(self,read,write,event):
        # Only these parent endpoints change mode. Chrome's child fd3/4 keep
        # their independent blocking pipe semantics.
        os.set_blocking(read,False);os.set_blocking(write,False)
        self.read=read;self.write=write;self.event=event;self._serial=0;self._lock=asyncio.Lock();self._buffer=b'';self._pending={};self._task=None;self._closed=False;self._events=set()
    async def start(self):self._task=asyncio.create_task(self._receive())
    def _frame(self):
        while b'\0' not in self._buffer:
            ready,_,_=select.select([self.read],[],[],.25)
            if self._closed:raise BrowserError()
            if not ready:return None
            try:data=os.read(self.read,65536)
            except BlockingIOError:return None
            if not data:raise BrowserError()
            self._buffer+=data
            if len(self._buffer)>FRAME_LIMIT:raise BrowserError()
        packet,self._buffer=self._buffer.split(b'\0',1)
        return json.loads(packet)
    def _write(self,data):
        deadline=time.monotonic()+2
        while data:
            remaining=deadline-time.monotonic()
            if self._closed or remaining<=0:raise BrowserError()
            _,ready,_=select.select([],[self.write],[],min(.1,remaining))
            if ready:
                try:count=os.write(self.write,data)
                except BlockingIOError:continue
                if count<=0:raise BrowserError()
                data=data[count:]
    async def command(self,method,params,*,session=None):
        if self._closed:raise BrowserError()
        ident=None
        try:
            # Serialize frame registration/write only. Responses may depend on
            # another command sent by a paused-request event handler.
            async with self._lock:
                if self._closed:raise BrowserError()
                self._serial+=1;ident=self._serial;future=asyncio.get_running_loop().create_future();self._pending[ident]=future
                packet={'id':ident,'method':method,'params':params}
                if session:packet['sessionId']=session
                data=json.dumps(packet,separators=(',',':')).encode()+b'\0'
                if len(data)>FRAME_LIMIT:raise BrowserError()
                await thread_call(self._write,data)
            return await asyncio.wait_for(future,3)
        finally:
            if ident is not None:self._pending.pop(ident,None)
    async def _receive(self):
        try:
            while not self._closed:
                packet=await thread_call(self._frame)
                if packet is None:continue
                if not isinstance(packet,dict):raise BrowserError()
                if 'id' in packet:
                    future=self._pending.get(packet['id'])
                    if future is not None and not future.done():
                        if 'error' in packet or not isinstance(packet.get('result'),dict):future.set_exception(BrowserError())
                        else:future.set_result(packet['result'])
                elif 'method' in packet:
                    # Event handlers issue commands; do not block the receiver
                    # which must consume those commands' responses.
                    if len(self._events)>=64:raise BrowserError()
                    task=asyncio.create_task(self.event(packet));self._events.add(task)
                    task.add_done_callback(self._event_done)
                else:raise BrowserError()
        except asyncio.CancelledError:raise
        except Exception:
            self._closed=True
            for future in self._pending.values():
                if not future.done():future.set_exception(BrowserError())
    def _event_done(self,task):
        self._events.discard(task)
        if not task.cancelled():
            try:task.exception()
            except Exception:pass
    async def close(self):
        self._closed=True
        if self._task:self._task.cancel();await asyncio.gather(self._task,return_exceptions=True)
        tasks=tuple(self._events)
        for task in tasks:task.cancel()
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)
        for future in self._pending.values():
            if not future.done():future.set_exception(BrowserError())

async def _finish(awaitable):
    """Join owned cleanup across repeated cancellation, then propagate it."""
    task=asyncio.create_task(awaitable);cancelled=False
    while not task.done():
        try:await asyncio.shield(task)
        except asyncio.CancelledError:cancelled=True
        except BaseException:break
    try:result=task.result()
    except BaseException:
        if cancelled:raise asyncio.CancelledError from None
        raise
    if cancelled:raise asyncio.CancelledError
    return result

class OwnedBrowser:
    def __init__(self,plan,*,process_ops=None,browser_environment=None):
        if not isinstance(plan,BrowserPlan):raise BrowserError()
        self.plan=plan;self.ops=process_ops or NativeBrowserOps();self.process=None;self._identity=None
        self._started=False;self._reaped=False;self._fds=[];self._pipe=None;self._guard=None;self._events=set();self._status='prepared';self._event_failed=False;self._targets={};self._lock=asyncio.Lock()
        self.env={'HOME':str(plan.access.client_dir),'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'}
        supplied=browser_environment or {}
        if not isinstance(supplied,dict) or any(key not in ('DISPLAY','XAUTHORITY','WAYLAND_DISPLAY','XDG_RUNTIME_DIR') for key in supplied):raise BrowserError()
        if any(not isinstance(value,str) or len(value)>4096 or any(ord(c)<32 for c in value) for value in supplied.values()):raise BrowserError()
        # Caller supplies reviewed GUI transport explicitly; no HOME discovery.
        for key in ('XAUTHORITY',):
            if key in supplied:
                fd=_open(Path(supplied[key]));os.close(fd)
        if 'XDG_RUNTIME_DIR' in supplied:
            fd=_open(Path(supplied['XDG_RUNTIME_DIR']),directory=True);os.close(fd)
        self.env.update(supplied)
    def readback(self):return {'status':self._status,'reaped':self._reaped,'live_verified':False,'remote_ssh':'pending'}
    def _attest(self, *, cleanup=False):
        if cleanup:
            # Socket/token rotation must not prevent reaping the originally
            # owned browser. Cleanup needs program/profile/process proof only.
            if _program(self.plan.chrome_binary,self.plan.chrome_sha256)!=self.plan._inode:raise BrowserError()
            fd=_open(self.plan.access.client_dir,directory=True)
            try:meta=os.fstat(fd)
            finally:os.close(fd)
            if (meta.st_dev,meta.st_ino)!=self.plan.access._client_inode:raise BrowserError()
        else:self.plan.validate(created=True)
        fd=_open(self.plan.profile_dir,directory=True)
        try:meta=os.fstat(fd)
        finally:os.close(fd)
        if (meta.st_dev,meta.st_ino)!=self._profile_identity:raise BrowserError()
        observed=self.ops.observe(self.process)
        if (observed.get('pid')!=self.process.pid or type(observed.get('start')) is not int or observed['start']<=0
                or observed.get('uid')!=os.geteuid() or observed.get('executable')!=str(self.plan.chrome_binary)
                or observed.get('sha256')!=self.plan.chrome_sha256 or observed.get('argv')!=self._argv or not observed.get('alive')):raise BrowserError()
        if self._identity is not None and observed!=self._identity:raise BrowserError()
        return observed
    async def _send(self,session,method,params):
        if self._pipe is not None:return await self._pipe.command(method,params,session=session)
        return await self.ops.command(method,params,session=session)
    def _target(self,target_id):
        if not isinstance(target_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,256}',target_id):raise BrowserError()
        if target_id not in self._targets:
            if len(self._targets)>=64:raise BrowserError()
            self._targets[target_id]={'session':None,'ready':asyncio.Event(),'failed':False}
        return self._targets[target_id]
    async def _event(self,event):
        state=None
        try:
            if event['method']=='Target.attachedToTarget':
                params=event['params'];session=params['sessionId'];info=params['targetInfo'];kind=info['type']
                state=self._target(info['targetId'])
                if state['session'] is not None:
                    if state['session']!=session:raise BrowserError()
                    return
                state['session']=session
                # Pause descendants before resuming this target. Fetch must be
                # enabled on the actual autoattached session, never a second
                # manual attachment to the same target.
                await self._send(session,'Target.setAutoAttach',{'autoAttach':True,'waitForDebuggerOnStart':True,'flatten':True})
                await self._guard.attach(session,target_type=kind)
                state['ready'].set()
            elif event['method']=='Fetch.requestPaused':await self._guard.handle(event.get('sessionId'),event)
            elif event['method']=='Target.detachedFromTarget':
                session=event['params']['sessionId'];self._guard._sessions.discard(session)
                for state in self._targets.values():
                    if state['session']==session:state['failed']=True;state['ready'].set()
        except Exception:
            if state is not None:state['failed']=True;state['ready'].set()
            self._event_failed=True;self._status='pending'
            # Failed targets stay paused. A failure never supplies readiness.
    async def _ready_target(self,target_id):
        state=self._target(target_id)
        await asyncio.wait_for(state['ready'].wait(),3)
        if state['failed'] or self._event_failed or state['session'] not in self._guard._sessions:raise BrowserError()
        return state['session']
    async def start(self):
        async with self._lock:
            if self._started:raise BrowserError()
            self._started=True
            try:
                self.plan.validate();parent=_open(self.plan.profile_dir.parent,directory=True)
                try:os.mkdir(self.plan.profile_dir.name,0o700,dir_fd=parent)
                finally:os.close(parent)
                fd=_open(self.plan.profile_dir,directory=True)
                try:meta=os.fstat(fd);self._profile_identity=(meta.st_dev,meta.st_ino)
                finally:os.close(fd)
                child_read,parent_write=os.pipe2(os.O_CLOEXEC);parent_read,child_write=os.pipe2(os.O_CLOEXEC)
                self._fds=[child_read,parent_write,parent_read,child_write]
                self._argv=(str(self.plan.chrome_binary),'--user-data-dir='+str(self.plan.profile_dir),'--remote-debugging-pipe','--no-first-run','--no-default-browser-check','about:blank')
                self._status='unknown'
                def spawn():
                    self.process=self.ops.spawn(self._argv,env=self.env,read_fd=child_read,write_fd=child_write)
                    self._identity=self._attest()
                    self.process._ownership=dict(self._identity)
                await thread_call(spawn)
                self._identity=await thread_call(self._attest)
                if isinstance(self.ops,NativeBrowserOps):
                    os.close(child_read);os.close(child_write);self._fds.remove(child_read);self._fds.remove(child_write)
                    self._pipe=_Pipe(parent_read,parent_write,self._event);await self._pipe.start()
                self._guard=FetchGuard(self.plan.access,self._send)
                if self._pipe is None:
                    # Low-level internal test transport only; real Chrome uses
                    # the owned pipe's event receiver above.
                    self.ops.bind_events(self._event)
                await self._send(None,'Target.setAutoAttach',{'autoAttach':True,'waitForDebuggerOnStart':True,'flatten':True})
                target=await self._send(None,'Target.createTarget',{'url':'about:blank'})
                session=await self._ready_target(target['targetId'])
                await self._send(session,'Page.navigate',{'url':self.plan.access.origin+'/'})
                await thread_call(self._attest)
                if self._event_failed:raise BrowserError()
                self._status='connected'
                return self.readback()
            except asyncio.CancelledError:
                self._status='unknown'
                try:
                    if self._identity is not None:await self._reap()
                except BaseException:pass
                finally:await self._close_pipe()
                raise
            except Exception:
                self._status='pending'
                try:
                    if self._identity is not None:await self._reap()
                except Exception:pass
                finally:await self._close_pipe()
                raise BrowserError() from None
    async def _close_pipe(self):
        if self._pipe:await self._pipe.close();self._pipe=None
        for fd in self._fds:
            try:os.close(fd)
            except OSError:pass
        self._fds=[]
    async def _reap(self):
        await thread_call(self._attest,cleanup=True)
        await thread_call(self.ops.terminate,self.process)
        result=await thread_call(self.ops.wait,self.process,3)
        if result is None:raise BrowserError()
        self._reaped=True;self._status='closed'
    async def close(self):
        async with self._lock:
            if self._reaped:return self.readback()
            try:
                if self._identity is None:raise BrowserError()
                await _finish(self._reap())
                return self.readback()
            except asyncio.CancelledError:raise
            except Exception:raise BrowserError() from None
            finally:await _finish(self._close_pipe())
