"""Explicit pinned SSH Unix forwarding; authenticated token packet stays in memory.

No actual SSH/browser acceptance is inferred from preparing or testing this code.
The remote helper is fixed source with bounded stdin/stdout, not a caller command.
"""
from __future__ import annotations
import asyncio
from dataclasses import dataclass,field
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import select
import shlex
import stat
import subprocess
import threading
import time
from .console_access import _open,_socket,_token,FRAME_LIMIT
from .console_browser import _program,NativeBrowserOps,_Process,_finish
from .async_io import thread_call

NOTICE=('远程控制台尚未通过安全连接验证。怎么解决：核对已审核 SSH 主机与密钥、私有运行目录和当前连接；身份或结果未知时不要重试或停止其他进程。'
        '\n复制给 AI：帮我检查 hostd 远程 Unix 控制台的 SSH 身份、文件权限、连接和回收证明；不要输出令牌、密钥或响应正文。')
class SSHError(ValueError):
    def __init__(self):super().__init__(NOTICE)

# This constant is the ONLY executable remote program body. Request data arrives
# on stdin. stdout is a bounded authenticated channel packet, never a user log.
REMOTE_HELPER=r'''
import hashlib,json,os,re,stat,sys

def unique(pairs):
 out={}
 for key,value in pairs:
  if key in out:raise ValueError()
  out[key]=value
 return out

def parent(path):
 if not isinstance(path,str) or not re.fullmatch(r'/[A-Za-z0-9_+./-]+',path) or '..' in path.split('/'):raise ValueError()
 fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
 try:
  for part in path.split('/')[1:]:
   if not part:raise ValueError()
   child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=fd)
   os.close(fd);fd=child
  meta=os.fstat(fd)
  if meta.st_uid!=os.geteuid() or stat.S_IMODE(meta.st_mode)!=0o700:raise ValueError()
  return fd,meta
 except BaseException:os.close(fd);raise

def socket_meta(fd):
 meta=os.stat('console.sock',dir_fd=fd,follow_symlinks=False)
 if not stat.S_ISSOCK(meta.st_mode) or meta.st_uid!=os.geteuid() or stat.S_IMODE(meta.st_mode)!=0o600:raise ValueError()
 return meta

def identity(meta):return (meta.st_dev,meta.st_ino,meta.st_uid,meta.st_mode,meta.st_size,meta.st_mtime_ns,meta.st_ctime_ns)

try:
 raw=sys.stdin.buffer.read(2049)
 if len(raw)>2048:raise ValueError()
 req=json.loads(raw,object_pairs_hook=unique)
 if (not isinstance(req,dict) or set(req)!={'version','nonce','expected_uid','runtime_dir'} or type(req['version']) is not int or req['version']!=1
   or type(req['expected_uid']) is not int or req['expected_uid']!=os.geteuid()
   or not isinstance(req['nonce'],str) or not re.fullmatch('[0-9a-f]{64}',req['nonce'])):raise ValueError()
 fd,base=parent(req['runtime_dir'])
 try:
  sock=socket_meta(fd)
  tokenfd=os.open('console.token',os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK,dir_fd=fd)
  try:
   tokenmeta=os.fstat(tokenfd)
   if not stat.S_ISREG(tokenmeta.st_mode) or tokenmeta.st_uid!=os.geteuid() or stat.S_IMODE(tokenmeta.st_mode)!=0o600 or tokenmeta.st_nlink!=1:raise ValueError()
   token=os.read(tokenfd,129)
   if not re.fullmatch(rb'[A-Za-z0-9_-]{43}\n?',token):raise ValueError()
   if identity(os.fstat(tokenfd))!=identity(tokenmeta):raise ValueError()
   if identity(os.stat('console.token',dir_fd=fd,follow_symlinks=False))!=identity(tokenmeta):raise ValueError()
  finally:os.close(tokenfd)
  if identity(socket_meta(fd))!=identity(sock) or identity(os.fstat(fd))!=identity(base):raise ValueError()
  again,againmeta=parent(req['runtime_dir'])
  try:
   if identity(againmeta)!=identity(base):raise ValueError()
  finally:os.close(again)
  reply={'version':1,'nonce':req['nonce'],'uid':os.geteuid(),'runtime_ref':hashlib.sha256(req['runtime_dir'].encode()).hexdigest(),
   'runtime_dev':base.st_dev,'runtime_ino':base.st_ino,'socket_dev':sock.st_dev,'socket_ino':sock.st_ino,
   'token_sha256':hashlib.sha256(token).hexdigest(),'token':token.decode()}
  wire=json.dumps(reply,separators=(',',':')).encode()
  if len(wire)>2048:raise ValueError()
  sys.stdout.buffer.write(wire);sys.stdout.buffer.flush()
 finally:os.close(fd)
except BaseException:
 sys.exit(1)
'''

def _unique(pairs):
 result={}
 for key,value in pairs:
  if key in result:raise SSHError()
  result[key]=value
 return result

@dataclass(frozen=True)
class _Packet:
    uid:int
    stable:tuple=field(repr=False)
    token:str=field(repr=False)
    def readback(self):return {'remote_uid':self.uid,'live_verified':False}

def parse_helper_packet(raw,request):
    """Parse captured authenticated SSH output; parsing alone grants nothing."""
    try:
        if not isinstance(raw,bytes) or len(raw)>2048:raise SSHError()
        value=json.loads(raw,object_pairs_hook=_unique)
        fields={'version','nonce','uid','runtime_ref','runtime_dev','runtime_ino','socket_dev','socket_ino','token_sha256','token'}
        if not isinstance(value,dict) or set(value)!=fields or type(value['version']) is not int or value['version']!=1 or value['nonce']!=request['nonce']:raise SSHError()
        if type(value['uid']) is not int or value['uid']!=request['expected_uid']:raise SSHError()
        if value['runtime_ref']!=hashlib.sha256(request['runtime_dir'].encode()).hexdigest():raise SSHError()
        for key in ('runtime_dev','runtime_ino','socket_dev','socket_ino'):
            if type(value[key]) is not int or value[key]<0:raise SSHError()
        raw_token=value['token']
        if not isinstance(raw_token,str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}\n?',raw_token):raise SSHError()
        if value['token_sha256']!=hashlib.sha256(raw_token.encode()).hexdigest():raise SSHError()
        stable=(value['uid'],value['runtime_ref'],value['runtime_dev'],value['runtime_ino'],value['socket_dev'],value['socket_ino'],value['token_sha256'])
        return _Packet(value['uid'],stable,raw_token.strip())
    except Exception:raise SSHError() from None

def _path(value):
    path=Path(value)
    if not path.is_absolute() or not re.fullmatch(r'/[A-Za-z0-9_+./-]+',str(path)) or '..' in path.parts:raise SSHError()
    return path

def _file_pin(path,digest):
    if not re.fullmatch(r'[0-9a-f]{64}',digest):raise SSHError()
    fd=_open(path)
    with os.fdopen(fd,'rb') as file:
        raw=file.read(1024*1024+1);meta=os.fstat(file.fileno())
    if len(raw)>1024*1024 or hashlib.sha256(raw).hexdigest()!=digest:raise SSHError()
    return meta.st_dev,meta.st_ino

@dataclass(frozen=True)
class SSHPlan:
    ssh_binary:Path
    ssh_sha256:str
    host:str
    user:str
    remote_uid:int
    remote_python:Path
    remote_runtime:Path
    known_hosts:Path=field(repr=False)
    known_hosts_sha256:str
    identity_file:Path=field(repr=False)
    identity_sha256:str
    forward_dir:Path
    _program_inode:tuple=field(repr=False)
    _known_inode:tuple=field(repr=False)
    _identity_inode:tuple=field(repr=False)
    _dir_inode:tuple=field(repr=False)
    @classmethod
    def check(cls,*,ssh_binary,ssh_sha256,host,user,remote_uid,remote_python,remote_runtime,known_hosts,known_hosts_sha256,identity_file,identity_sha256,forward_dir):
        try:
            if (not isinstance(host,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,252}',host)
                or not isinstance(user,str) or not re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}',user)
                or type(remote_uid) is not int or remote_uid<0):raise SSHError()
            paths=[_path(value) for value in (ssh_binary,remote_python,remote_runtime,known_hosts,identity_file,forward_dir)]
            ssh,python,runtime,known,identity,directory=paths
            if len(os.fsencode(directory/'console.sock'))>=108:raise SSHError()
            fd=_open(directory,directory=True)
            try:meta=os.fstat(fd);inode=(meta.st_dev,meta.st_ino)
            finally:os.close(fd)
            if os.path.lexists(directory/'console.sock'):raise SSHError()
            return cls(ssh,ssh_sha256,host,user,remote_uid,python,runtime,known,known_hosts_sha256,identity,identity_sha256,directory,
                _program(ssh,ssh_sha256),_file_pin(known,known_hosts_sha256),_file_pin(identity,identity_sha256),inode)
        except Exception:raise SSHError() from None
    def access_pins(self):
        self.directory_pin()
        if (_program(self.ssh_binary,self.ssh_sha256)!=self._program_inode or _file_pin(self.known_hosts,self.known_hosts_sha256)!=self._known_inode
            or _file_pin(self.identity_file,self.identity_sha256)!=self._identity_inode):raise SSHError()
    def directory_pin(self):
        fd=_open(self.forward_dir,directory=True)
        try:meta=os.fstat(fd)
        finally:os.close(fd)
        if (meta.st_dev,meta.st_ino)!=self._dir_inode:raise SSHError()
    def argv(self,*,helper=False):
        options=('StrictHostKeyChecking=yes','IdentitiesOnly=yes','BatchMode=yes','IdentityAgent=none','ForwardAgent=no',
            'ControlMaster=no','ControlPath=none','ControlPersist=no','ProxyCommand=none','ProxyJump=none',
            'PasswordAuthentication=no','KbdInteractiveAuthentication=no','GSSAPIAuthentication=no','PreferredAuthentications=publickey',
            'CertificateFile=none','AddKeysToAgent=no','UserKnownHostsFile='+str(self.known_hosts),'GlobalKnownHostsFile=/dev/null',
            'UpdateHostKeys=no','ConnectionAttempts=1','ConnectTimeout=5','ServerAliveInterval=5','ServerAliveCountMax=1',
            'StreamLocalBindMask=0177','StreamLocalBindUnlink=no','ExitOnForwardFailure=yes')
        args=[str(self.ssh_binary),'-F','/dev/null','-T',*sum((['-o',option] for option in options),[]),'-i',str(self.identity_file),'-l',self.user]
        if helper:
            # Only a constant reviewed script and constrained program path enter
            # the remote shell command. All per-run data uses bounded stdin.
            args.extend([self.host,'exec '+str(self.remote_python)+' -I -c '+shlex.quote(REMOTE_HELPER)])
        else:args.extend(['-N','-L',str(self.forward_dir/'console.sock')+':'+str(self.remote_runtime/'console.sock'),self.host])
        return args
    def readback(self):return {'status':'prepared','live_verified':False}

class _SSHProcess(_Process):
    """An exact native child keeps its kernel wait proof outside public cache."""
    def __init__(self,pid):
        super().__init__(pid);self.__pid=pid;self.__status=None;self.__lock=threading.Lock()
    def poll(self):
        with self.__lock:
            if self.pid!=self.__pid:raise SSHError()
            if self.__status is not None:
                result=os.waitstatus_to_exitcode(self.__status)
                if type(self.returncode) is not int or self.returncode!=result:raise SSHError()
                return result
            if self.returncode is not None:raise SSHError()
            try:pid,status=os.waitpid(self.__pid,os.WNOHANG)
            except ChildProcessError:raise SSHError() from None
            if pid:
                self.__status=status;self.returncode=os.waitstatus_to_exitcode(status)
            return self.returncode

class NativeSSHProcessOps(NativeBrowserOps):
    def __init__(self):self._children={}
    def spawn(self,argv,*,env):
        null=os.open('/dev/null',os.O_RDWR|os.O_CLOEXEC)
        try:
            process=_SSHProcess(os.posix_spawn(argv[0],argv,env,file_actions=[(os.POSIX_SPAWN_DUP2,null,fd) for fd in (0,1,2)],setsid=True))
            self._children[id(process)]=(process,process.pid,tuple(argv))
            return process
        finally:os.close(null)
    def exited_owned(self,process,argv):
        # Only this spawn's exact direct-child handle can prove an early exit.
        # A cached identity/returncode or a replacement PID is not authority.
        child=self._children.get(id(process))
        if (child is None or child[0] is not process or type(process) is not _SSHProcess
            or child[1]!=process.pid or child[2]!=tuple(argv)):raise SSHError()
        status=_SSHProcess.poll(process)
        if status is None:return None
        if type(status) is not int or os.path.lexists('/proc/'+str(child[1])):raise SSHError()
        result=self.wait(process,3)
        if type(result) is not int or result!=status or os.path.lexists('/proc/'+str(child[1])):raise SSHError()
        try:os.killpg(child[1],0)
        except ProcessLookupError:return status
        raise SSHError()
    def helper(self,argv,*,stdin,env,timeout,limit):
        process=None;proof=None;out=b'';err=b''
        try:
            process=subprocess.Popen(argv,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env,start_new_session=True,close_fds=True)
            proof=self.observe(process);process._ownership=proof
            if proof['executable']!=argv[0] or proof['argv']!=tuple(argv) or not proof['alive']:raise SSHError()
            for file in (process.stdin,process.stdout,process.stderr):os.set_blocking(file.fileno(),False)
            pending=stdin;deadline=time.monotonic()+timeout;reads={process.stdout.fileno():'out',process.stderr.fileno():'err'}
            while reads or pending:
                remaining=deadline-time.monotonic()
                if remaining<=0:raise SSHError()
                ready,write,_=select.select(list(reads),[process.stdin.fileno()] if pending else [],[],min(.1,remaining))
                if write:
                    try:pending=pending[os.write(process.stdin.fileno(),pending):]
                    except BlockingIOError:pass
                    if not pending:process.stdin.close()
                for fd in ready:
                    try:data=os.read(fd,limit+1)
                    except BlockingIOError:continue
                    if not data:reads.pop(fd);continue
                    if reads[fd]=='out':out+=data
                    else:err+=data
                    if len(out)>limit or len(err)>limit:raise SSHError()
            result=process.wait(timeout=max(.001,deadline-time.monotonic()))
            return SimpleResult(result,out,err)
        except Exception:
            if process is not None and proof is not None and process.poll() is None:
                try:self.terminate(process);self.wait(process,3)
                except Exception:pass
            raise SSHError() from None
        finally:
            if process is not None:
                for file in (process.stdin,process.stdout,process.stderr):
                    if file is not None:file.close()

# Retain the native class identity when callers inject a process-ops factory.
_NATIVE_SSH_OPS=NativeSSHProcessOps

@dataclass(frozen=True)
class SimpleResult:
    returncode:int
    stdout:bytes=field(repr=False)
    stderr:bytes=field(repr=False)

_CAP=object()
class MemoryEndpoint:
    def __init__(self,tunnel,cap):
        if cap is not _CAP:raise SSHError()
        self._tunnel=tunnel;self._generation=tunnel._packet.stable
    def __repr__(self):return '<MemoryEndpoint owned authenticated SSH transport>'
    def validate(self):
        self._tunnel.validate()
        if self._generation!=self._tunnel._packet.stable:raise SSHError()
    @property
    def directory(self):return self._tunnel.plan.forward_dir
    def _memory_token(self):self.validate();return self._tunnel._packet.token

class OwnedSSHTunnel:
    def __init__(self,plan,*,process_ops=None,clock=time.monotonic,metadata_ttl=60):
        if (not isinstance(plan,SSHPlan) or not callable(clock) or isinstance(metadata_ttl,bool)
            or not isinstance(metadata_ttl,(float,int)) or not 0<metadata_ttl<=300):raise SSHError()
        self.plan=plan;self.ops=process_ops or NativeSSHProcessOps();self.clock=clock;self.ttl=metadata_ttl
        self.process=None;self._spawned_process=None;self._identity=None;self._socket=None;self._packet=None;self._checked_at=None
        self._started=False;self._reaped=False;self._status='prepared';self._lock=asyncio.Lock()
        self._env={'HOME':str(plan.forward_dir),'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'}
    def readback(self):
        if self._status=='connected':
            try:self.validate()
            except Exception:self._status='pending'
        return {'status':self._status,'reaped':self._reaped,'live_verified':False}
    def _time(self):
        value=self.clock()
        if type(value) not in (int,float) or not 0<=value<float('inf'):raise SSHError()
        return value
    def _process(self,*,cleanup=False):
        self.plan.directory_pin()
        if not cleanup:self.plan.access_pins()
        value=self.ops.observe(self.process)
        if (value.get('pid')!=self.process.pid or type(value.get('start')) is not int or value['start']<=0 or value.get('uid')!=os.geteuid()
            or value.get('executable')!=str(self.plan.ssh_binary) or value.get('sha256')!=self.plan.ssh_sha256
            or value.get('argv')!=tuple(self.plan.argv()) or not value.get('alive')):raise SSHError()
        if self._identity is not None and value!=self._identity:raise SSHError()
        return value
    def _socket_check(self):
        value=_socket(self.plan.forward_dir)
        if self._socket is not None and value!=self._socket:raise SSHError()
        return value
    def validate(self):
        try:
            if self._identity is None or self._reaped or self._checked_at is None or self._time()-self._checked_at>self.ttl or self._time()<self._checked_at:raise SSHError()
            self._process();self._socket_check()
        except Exception:raise SSHError() from None
    def endpoint(self):self.validate();return MemoryEndpoint(self,_CAP)
    async def _capture(self):
        self.plan.access_pins();request={'version':1,'nonce':secrets.token_hex(32),'expected_uid':self.plan.remote_uid,'runtime_dir':str(self.plan.remote_runtime)}
        answer=await thread_call(self.ops.helper,self.plan.argv(helper=True),stdin=json.dumps(request,separators=(',',':')).encode(),env=self._env,timeout=5,limit=2048)
        self.plan.access_pins()
        if answer.returncode!=0 or not isinstance(answer.stdout,bytes) or not isinstance(answer.stderr,bytes) or answer.stderr:raise SSHError()
        return parse_helper_packet(answer.stdout,request)
    async def _graph(self,packet):
        self.plan.access_pins();self._process();self._socket_check();writer=None
        fd=_open(self.plan.forward_dir,directory=True)
        try:reader,writer=await asyncio.wait_for(asyncio.open_unix_connection(f'/proc/self/fd/{fd}/console.sock',limit=FRAME_LIMIT),2)
        finally:os.close(fd)
        try:
            import socket,struct
            peer=writer.get_extra_info('socket')
            if peer is None or struct.unpack('3i',peer.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))[1]!=os.geteuid():raise SSHError()
            writer.write(('GET /api/graph HTTP/1.1\r\nHost: hostd.local\r\nAuthorization: Bearer '+packet.token+'\r\nConnection: close\r\n\r\n').encode());await asyncio.wait_for(writer.drain(),2)
            head=await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'),2)
            if len(head)>16384 or not head.startswith(b'HTTP/1.1 200 '):raise SSHError()
            fields={}
            for line in head[:-4].decode('ascii').split('\r\n')[1:]:
                name,colon,value=line.partition(':');key=name.lower()
                if not colon or key in fields:raise SSHError()
                fields[key]=value.strip()
            length=fields.get('content-length','')
            if not re.fullmatch('[0-9]{1,8}',length) or int(length)>FRAME_LIMIT or not fields.get('content-type','').startswith('application/json'):raise SSHError()
            body=await asyncio.wait_for(reader.readexactly(int(length)),2)
            if packet.token.encode() in head+body:raise SSHError()
            parsed=json.loads(body,object_pairs_hook=_unique)
            if not isinstance(parsed,dict) or not isinstance(parsed.get('nodes'),list):raise SSHError()
            self.plan.access_pins();self._process();self._socket_check()
        finally:
            writer.close()
            try:await writer.wait_closed()
            except (ConnectionResetError,BrokenPipeError):pass
    async def start(self):
        async with self._lock:
            if self._started:raise SSHError()
            self._started=True
            try:
                self.plan.access_pins()
                if os.path.lexists(self.plan.forward_dir/'console.sock'):raise SSHError()
                packet=await self._capture();self.plan.access_pins()
                def spawn():
                    self.process=self.ops.spawn(self.plan.argv(),env=self._env);self._spawned_process=self.process
                    self._identity=self._process();self.process._ownership=dict(self._identity)
                self._status='unknown';await thread_call(spawn)
                deadline=asyncio.get_running_loop().time()+5
                while True:
                    try:self._socket=self._socket_check();break
                    except FileNotFoundError:
                        if asyncio.get_running_loop().time()>=deadline:raise SSHError()
                        await asyncio.sleep(.01)
                await self._graph(packet);self._packet=packet;self._checked_at=self._time();self._status='connected'
                return self.readback()
            except asyncio.CancelledError:
                try:
                    if self.process is not None:await _finish(self._reap())
                except BaseException:pass
                raise
            except Exception:
                self._status='pending'
                try:
                    if self.process is not None:await _finish(self._reap())
                except Exception:pass
                raise SSHError() from None
    async def refresh(self):
        async with self._lock:
            try:
                if self._identity is None or self._reaped:raise SSHError()
                self._process();self._socket_check();packet=await self._capture()
                if packet.stable!=self._packet.stable:raise SSHError()
                await self._graph(packet);self._checked_at=self._time();self._status='connected';return self.readback()
            except asyncio.CancelledError:raise
            except Exception:self._status='pending';self._checked_at=None;raise SSHError() from None
    async def _reap(self):
        if self.process is None or self.process is not self._spawned_process:raise SSHError()
        self.plan.directory_pin()
        exited=None
        if isinstance(self.ops,_NATIVE_SSH_OPS):
            exited=await thread_call(self.ops.exited_owned,self.process,self.plan.argv())
        if exited is None:
            if self._identity is None:raise SSHError()
            await thread_call(self._process,cleanup=True)
            if self._socket is not None:self._socket_check()
            await thread_call(self.ops.terminate,self.process)
            if await thread_call(self.ops.wait,self.process,3) is None:raise SSHError()
        if self._socket is not None:
            self.plan.directory_pin();self._socket_check()
            fd=_open(self.plan.forward_dir,directory=True)
            try:os.unlink('console.sock',dir_fd=fd)
            finally:os.close(fd)
        self._reaped=True;self._status='closed';self._packet=None;self._checked_at=None
    async def close(self):
        async with self._lock:
            if self._reaped:return self.readback()
            try:await _finish(self._reap());return self.readback()
            except asyncio.CancelledError:raise
            except Exception:raise SSHError() from None
