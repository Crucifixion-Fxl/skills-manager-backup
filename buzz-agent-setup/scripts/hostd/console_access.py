"""Standalone loopback transport for the authenticated Unix console.

It never authenticates a caller on their behalf. The owned CDP client supplies
its scoped request header; this proxy checks and forwards that caller header.
An explicitly reviewed SSH plan can supply the owned remote Unix transport.
"""
from __future__ import annotations
import asyncio
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
import socket
import struct

NOTICE = ('控制台访问工具尚未通过本机验证。怎么解决：核对本次私有目录、控制台连接与隔离浏览器；远程访问还需核对已审核 SSH 身份。'
          '\n复制给 AI：帮我检查 hostd 本机访问客户端的权限、请求来源和连接；不要输出访问令牌或个人信息。')
HEADER_LIMIT=16384
BODY_LIMIT=1024
FRAME_LIMIT=1024*1024
SSH_REFRESH_INTERVAL=20
ROUTE=re.compile(r'/(?:api/(?:graph|events|(?:bindings|agents|operations)/[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}(?:/(?:pause|resume|backfill|restart))?))?\Z')

class AccessError(ValueError):
    def __init__(self):super().__init__(NOTICE)


def _open(path, *, directory=False):
    path=Path(path)
    if not path.is_absolute() or '..' in path.parts:raise AccessError()
    fd=os.open(path.anchor,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        for i,part in enumerate(path.parts[1:]):
            flags=os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK
            if i<len(path.parts)-2 or directory:flags|=os.O_DIRECTORY
            child=os.open(part,flags,dir_fd=fd);os.close(fd);fd=child
        meta=os.fstat(fd)
        if (meta.st_uid!=os.geteuid() or stat.S_IMODE(meta.st_mode)!=(0o700 if directory else 0o600)
                or not (stat.S_ISDIR(meta.st_mode) if directory else stat.S_ISREG(meta.st_mode))
                or not directory and meta.st_nlink!=1):raise AccessError()
        return fd
    except BaseException:os.close(fd);raise


def _token(path):
    fd=_open(path)
    with os.fdopen(fd,'rb') as file:
        raw=file.read(129);meta=os.fstat(file.fileno())
    if not re.fullmatch(rb'[A-Za-z0-9_-]{43}\n?',raw):raise AccessError()
    return raw.strip().decode('ascii'),(meta.st_dev,meta.st_ino),hashlib.sha256(raw).digest()


def _socket(directory):
    fd=_open(directory,directory=True)
    try:meta=os.stat('console.sock',dir_fd=fd,follow_symlinks=False)
    finally:os.close(fd)
    if not stat.S_ISSOCK(meta.st_mode) or meta.st_uid!=os.geteuid() or stat.S_IMODE(meta.st_mode)!=0o600:raise AccessError()
    return meta.st_dev,meta.st_ino

class ConsoleAccess:
    def __repr__(self):return '<ConsoleAccess private local transport>'
    @classmethod
    def check(cls,runtime_dir,client_dir,*,request_timeout=2):
        try:
            if isinstance(request_timeout,bool) or not isinstance(request_timeout,(float,int)) or not 0<request_timeout<=30:raise AccessError()
            runtime,client=Path(runtime_dir),Path(client_dir)
            for path in (runtime,client):
                fd=_open(path,directory=True);os.close(fd)
            value,inode,digest=_token(runtime/'console.token')
            own=cls();own.runtime_dir=runtime;own.client_dir=client;own.request_timeout=request_timeout
            own._token=value;own._token_inode=inode;own._token_hash=digest;own._socket_inode=_socket(runtime)
            own._server=None;own._tasks=set();own._writers=set();own._closed=False;own.port=None;own.origin=None;own.listener_address=None
            own._client_inode=client.stat().st_dev,client.stat().st_ino
            own._runtime_inode=runtime.stat().st_dev,runtime.stat().st_ino
            return own
        except Exception:raise AccessError() from None
    @classmethod
    def from_forwarder(cls,tunnel,client_dir,*,request_timeout=2):
        """Internal owned-SSH capability seam; HTTP never supplies a token DTO."""
        from .console_ssh import OwnedSSHTunnel,MemoryEndpoint
        try:
            if type(tunnel) is not OwnedSSHTunnel or isinstance(request_timeout,bool) or not isinstance(request_timeout,(int,float)) or not 0<request_timeout<=30:raise AccessError()
            endpoint=tunnel.endpoint()
            if type(endpoint) is not MemoryEndpoint:raise AccessError()
            client=Path(client_dir);fd=_open(client,directory=True)
            try:meta=os.fstat(fd)
            finally:os.close(fd)
            own=cls();own.runtime_dir=endpoint.directory;own.client_dir=client;own.request_timeout=request_timeout
            own._forwarded_endpoint=endpoint;own._token=endpoint._memory_token();own._socket_inode=_socket(endpoint.directory)
            own._client_inode=(meta.st_dev,meta.st_ino)
            own._server=None;own._tasks=set();own._writers=set();own._closed=False;own.port=None;own.origin=None;own.listener_address=None
            own._validate();return own
        except Exception:raise AccessError() from None
    def _validate(self):
        if hasattr(self,'_forwarded_endpoint'):
            self._forwarded_endpoint.validate()
            fd=_open(self.client_dir,directory=True)
            try:meta=os.fstat(fd)
            finally:os.close(fd)
            if ((meta.st_dev,meta.st_ino)!=self._client_inode or _socket(self.runtime_dir)!=self._socket_inode
                or not hmac.compare_digest(self._forwarded_endpoint._memory_token(),self._token)):raise AccessError()
            return
        for path in (self.runtime_dir,self.client_dir):
            fd=_open(path,directory=True);meta=os.fstat(fd);os.close(fd)
            expected=self._client_inode if path==self.client_dir else self._runtime_inode
            if (meta.st_dev,meta.st_ino)!=expected:raise AccessError()
        value,inode,digest=_token(self.runtime_dir/'console.token')
        if inode!=self._token_inode or digest!=self._token_hash or not hmac.compare_digest(value,self._token) or _socket(self.runtime_dir)!=self._socket_inode:raise AccessError()
    def readback(self):return {'status':'connected' if self._server else 'closed' if self._closed else 'prepared','live_verified':False}
    async def start(self):
        try:
            if self._server is not None or self._closed:raise AccessError()
            self._validate()
            self._server=await asyncio.start_server(self._handle,'127.0.0.1',0,limit=HEADER_LIMIT)
            self.listener_address=self._server.sockets[0].getsockname();self.port=self.listener_address[1]
            self.origin='http://127.0.0.1:'+str(self.port)
            return self.readback()
        except Exception:raise AccessError() from None
    async def close(self):
        self._closed=True
        if self._server:self._server.close();await self._server.wait_closed();self._server=None
        tasks=tuple(task for task in self._tasks if task is not asyncio.current_task())
        for task in tasks:task.cancel()
        for writer in tuple(self._writers):writer.close()
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)
        self._writers.clear()
    async def _error(self,writer,status):
        body=json.dumps({'ok':False,'notice':NOTICE},ensure_ascii=False).encode()
        writer.write((f'HTTP/1.1 {status} Error\r\nContent-Type: application/json; charset=utf-8\r\nContent-Length: {len(body)}\r\nConnection: close\r\nCache-Control: no-store\r\n\r\n').encode()+body)
        await asyncio.wait_for(writer.drain(),self.request_timeout)
    async def _request(self,reader):
        header=await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'),self.request_timeout)
        if len(header)>HEADER_LIMIT:raise ValueError(431)
        lines=header[:-4].decode('ascii').split('\r\n')
        if len(lines)>33 or any(len(line)>2048 for line in lines):raise ValueError(431)
        method,target,version=lines[0].split(' ')
        if version!='HTTP/1.1' or method not in ('GET','POST') or not ROUTE.fullmatch(target):raise ValueError(400)
        fields={}
        for line in lines[1:]:
            name,colon,value=line.partition(':');key=name.lower()
            if not colon or not re.fullmatch(r'[A-Za-z0-9-]+',name) or key in fields or value.startswith('\t'):raise ValueError(400)
            value=value.strip()
            if any(ord(c)<32 or ord(c)==127 for c in value):raise ValueError(400)
            fields[key]=value
        if any(key in fields for key in ('transfer-encoding','upgrade','expect','proxy-authorization','proxy-connection')):raise ValueError(400)
        if fields.get('connection','').lower() not in ('','close','keep-alive'):raise ValueError(400)
        length=fields.get('content-length','0')
        if not re.fullmatch(r'[0-9]{1,4}',length) or int(length)>BODY_LIMIT:raise ValueError(413)
        if method=='GET' and int(length):raise ValueError(400)
        if (fields.get('host')!=self.origin[7:] or fields.get('origin') not in (None,self.origin)
                or fields.get('sec-fetch-site') not in (None,'none','same-origin') or fields.get('sec-fetch-dest') in ('iframe','frame')):raise ValueError(403)
        if not hmac.compare_digest(fields.get('authorization',''),'Bearer '+self._token):raise ValueError(401)
        if method=='POST' and (fields.get('origin')!=self.origin or fields.get('x-hostd-request')!='1'):raise ValueError(403)
        body=await asyncio.wait_for(reader.readexactly(int(length)),self.request_timeout)
        return method,target,fields,body
    async def _handle(self,reader,writer):
        task=asyncio.current_task();self._tasks.add(task);self._writers.add(writer);upstream=None;sent=False
        try:
            if len(self._tasks)>32:raise ValueError(503)
            method,target,fields,body=await self._request(reader)
            self._validate()
            # Connect through the pinned parent descriptor, then recheck CAS.
            parent=_open(self.runtime_dir,directory=True)
            try:incoming,upstream=await asyncio.wait_for(asyncio.open_unix_connection(f'/proc/self/fd/{parent}/console.sock',limit=FRAME_LIMIT),self.request_timeout)
            finally:os.close(parent)
            self._validate();self._writers.add(upstream)
            peer=upstream.get_extra_info('socket')
            if peer is None or struct.unpack('3i',peer.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))[1]!=os.geteuid():raise AccessError()
            fields['host']='hostd.local';fields['connection']='close'
            if 'origin' in fields:fields['origin']='http://hostd.local'
            upstream.write((method+' '+target+' HTTP/1.1\r\n'+''.join(k+': '+v+'\r\n' for k,v in fields.items())+'\r\n').encode()+body)
            await asyncio.wait_for(upstream.drain(),self.request_timeout)
            head=await asyncio.wait_for(incoming.readuntil(b'\r\n\r\n'),self.request_timeout)
            if len(head)>HEADER_LIMIT or self._token.encode() in head:raise AccessError()
            lines=head[:-4].decode('ascii').split('\r\n');status=int(lines[0].split(' ')[1]);response={}
            for line in lines[1:]:
                k,colon,v=line.partition(':');key=k.lower()
                if not colon or key in response:raise AccessError()
                response[key]=v.strip()
            if 'location' in response or status in (301,302,303,307,308) or any(k in response for k in ('set-cookie','access-control-allow-origin','transfer-encoding')):raise AccessError()
            if response.get('content-type')=='text/event-stream' and target=='/api/events' and method=='GET' and status==200:
                writer.write(head);await writer.drain();sent=True
                disconnect=asyncio.create_task(reader.read(1))
                try:
                    while True:
                        packet=asyncio.create_task(incoming.readuntil(b'\n\n'))
                        done,_=await asyncio.wait((packet,disconnect),return_when=asyncio.FIRST_COMPLETED)
                        if disconnect in done:
                            packet.cancel();await asyncio.gather(packet,return_exceptions=True);break
                        data=packet.result()
                        if len(data)>FRAME_LIMIT or self._token.encode() in data:raise AccessError()
                        writer.write(data);await asyncio.wait_for(writer.drain(),self.request_timeout)
                finally:
                    disconnect.cancel()
                    if 'packet' in locals() and not packet.done():packet.cancel()
                    await asyncio.gather(disconnect,*( [packet] if 'packet' in locals() else []),return_exceptions=True)
            else:
                length=response.get('content-length','')
                if not re.fullmatch(r'[0-9]{1,8}',length) or int(length)>FRAME_LIMIT:raise AccessError()
                data=await asyncio.wait_for(incoming.readexactly(int(length)),self.request_timeout)
                if self._token.encode() in data:raise AccessError()
                writer.write(head+data);sent=True;await asyncio.wait_for(writer.drain(),self.request_timeout)
        except asyncio.CancelledError:raise
        except Exception as error:
            if not sent:
                status=408 if isinstance(error,asyncio.TimeoutError) else error.args[0] if isinstance(error,ValueError) and error.args and type(error.args[0]) is int else 503
                try:await self._error(writer,status)
                except Exception:pass
        finally:
            for current in (upstream,writer):
                if current:
                    current.close();self._writers.discard(current)
                    try:await current.wait_closed()
                    except Exception:pass
            self._tasks.discard(task)

def load_ssh_plan(path):
    """Read only protected reviewed SSH metadata; never credentials or tokens."""
    from .console_ssh import SSHPlan
    fields={'version','ssh_binary','ssh_sha256','host','user','remote_uid','remote_python','remote_runtime',
            'known_hosts','known_hosts_sha256','identity_file','identity_sha256','forward_dir'}
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result:raise AccessError()
            result[key]=value
        return result
    try:
        fd=_open(Path(path))
        with os.fdopen(fd,'rb') as file:
            before=os.fstat(file.fileno());raw=file.read(16385);after=os.fstat(file.fileno())
        if len(raw)>16384 or (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns):raise AccessError()
        document=json.loads(raw,object_pairs_hook=unique)
        if not isinstance(document,dict) or set(document)!=fields or type(document['version']) is not int or document['version']!=1:raise AccessError()
        if type(document['remote_uid']) is not int or any(not isinstance(value,str) for key,value in document.items() if key not in ('version','remote_uid')):raise AccessError()
        return SSHPlan.check(**{key:value for key,value in document.items() if key!='version'})
    except Exception:raise AccessError() from None

async def _entry(args):
    from .console_browser import BrowserPlan,OwnedBrowser,_finish
    from .console_ssh import OwnedSSHTunnel
    access=browser=tunnel=None;signals=[];loop=asyncio.get_running_loop()
    try:
        if bool(args.runtime_dir)==bool(args.ssh_plan):raise AccessError()
        if args.ssh_plan:
            tunnel=OwnedSSHTunnel(load_ssh_plan(args.ssh_plan))
            await tunnel.start()
            access=ConsoleAccess.from_forwarder(tunnel,Path(args.client_dir))
        else:access=ConsoleAccess.check(Path(args.runtime_dir),Path(args.client_dir))
        await access.start()
        plan=BrowserPlan.check(access,chrome_binary=Path(args.chrome_binary),chrome_sha256=args.chrome_sha256,
            profile_dir=access.client_dir/'chrome-profile')
        environment={}
        if args.display:environment['DISPLAY']=args.display
        if args.xauthority:environment['XAUTHORITY']=args.xauthority
        browser=OwnedBrowser(plan,browser_environment=environment)
        await browser.start()
        # Metadata only; this is transport startup, never browser acceptance.
        print(json.dumps({'status':'connected','live_verified':False,'remote_ssh':'connected' if tunnel is not None else 'not_requested'}),flush=True)
        stopped=asyncio.Event()
        import signal
        for sig in (signal.SIGINT,signal.SIGTERM):
            loop.add_signal_handler(sig,stopped.set);signals.append(sig)
        if tunnel is None:await stopped.wait()
        else:
            while not stopped.is_set():
                try:await asyncio.wait_for(stopped.wait(),SSH_REFRESH_INTERVAL)
                except asyncio.TimeoutError:
                    if not stopped.is_set():await tunnel.refresh()
    finally:
        for sig in signals:loop.remove_signal_handler(sig)
        async def cleanup():
            try:
                if browser is not None:await browser.close()
            finally:
                try:
                    if access is not None:await access.close()
                finally:
                    if tunnel is not None:await tunnel.close()
        await _finish(cleanup())

def main(argv=None):
    import argparse
    import sys
    class Parser(argparse.ArgumentParser):
        def error(self,message):raise AccessError()
    parser=Parser(description='隔离浏览器访问 hostd Unix 控制台；可使用已审核 SSH Unix 转发。')
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--runtime-dir');mode.add_argument('--ssh-plan')
    for name in ('client-dir','chrome-binary','chrome-sha256'):parser.add_argument('--'+name,required=True)
    parser.add_argument('--display');parser.add_argument('--xauthority')
    try:
        args=parser.parse_args(argv);asyncio.run(_entry(args));return 0
    except KeyboardInterrupt:return 130
    except Exception:
        print(NOTICE,file=sys.stderr);return 1

if __name__=='__main__':raise SystemExit(main())
