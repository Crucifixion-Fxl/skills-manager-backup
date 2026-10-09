#!/usr/bin/env python3
"""Pinned native ACP -> real Codex exec child adapter, component evidence only.

Root MUST render both placeholders and pin final bytes. Never uses reply_text.
Adjacent acp_double.py and model_evidence.py are explicit deployment sources.
"""
import argparse
import ctypes
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import select
import signal
import stat
import subprocess
import sys
import time
import types

DOUBLE_SHA256 = '4a5fda0bdca07d626f003a15f28ec9a5ed56133617df88ecb2668934797642a3'
BACKEND_CONFIG_SHA256 = '__RENDER_BACKEND_CONFIG_SHA256__'
MODEL_EVIDENCE_SHA256 = '__RENDER_MODEL_EVIDENCE_SHA256__'
LIMIT = 256 * 1024
NOTICE = '真实模型图片输入或子进程证据仍待确认。怎么解决：核对受保护部署和原生图片输入。复制给 AI：ACP_REAL_MODEL_PENDING。'

class AdapterError(Exception):pass
class ScanChanged(AdapterError):pass

def pinned(path,pin):
    # Bootstrap source closure without importing a mutable neighboring file.
    if not re.fullmatch('[0-9a-f]{64}',pin):raise AdapterError()
    path=Path(path)
    if not path.is_absolute() or '..' in path.parts:raise AdapterError()
    fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC)
    try:
        for i,name in enumerate(path.parts[1:]):
            flags=os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC
            if i<len(path.parts)-2:flags|=os.O_DIRECTORY
            child=os.open(name,flags,dir_fd=fd);os.close(fd);fd=child
        meta=os.fstat(fd)
        if meta.st_uid!=os.geteuid() or stat.S_IMODE(meta.st_mode)!=0o600 or not stat.S_ISREG(meta.st_mode) or meta.st_nlink!=1 or meta.st_size>LIMIT:raise AdapterError()
        raw=os.read(fd,LIMIT+1)
        if len(raw)!=meta.st_size or hashlib.sha256(raw).hexdigest()!=pin:raise AdapterError()
        mod=types.ModuleType('hostd_pinned_'+pin);mod.__file__=str(path);exec(compile(raw,str(path),'exec'),mod.__dict__);return mod
    finally:os.close(fd)

D = pinned(Path(__file__).absolute().parent/'acp_double.py',DOUBLE_SHA256)
E = pinned(Path(__file__).absolute().parent/'model_evidence.py',MODEL_EVIDENCE_SHA256)

class Config:
    def __init__(self,manifest):
        self.manifest=manifest;self.path=manifest.root/'backend.json';self.raw,self.inode=E.read_private(self.path,BACKEND_CONFIG_SHA256,16384);self.doc=D._json(self.raw)
        c=self.doc
        keys={'version','run_id','backend_path','backend_sha256','backend_args','model','timeout_seconds','env_allowlist','selected_pubkey','channel_id','mirror_pubkey','native_path','native_sha256'}
        if set(c) not in (keys,keys|{'native_image_transport'}) or type(c['version']) is not int or c['version']!=1 or c['run_id']!=manifest.doc['run_id']:raise AdapterError()
        for key in ('backend_sha256','native_sha256','selected_pubkey','mirror_pubkey'):
            if not isinstance(c[key],str) or not re.fullmatch('[0-9a-f]{64}',c[key]):raise AdapterError()
        D._canonical(c['backend_path']);D._canonical(c['native_path'])
        if not re.fullmatch('[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',c['channel_id']):raise AdapterError()
        a=c['backend_args']
        if not isinstance(a,list) or not 8<=len(a)<=64 or any(not isinstance(x,str) or not x or len(x.encode())>4096 or '\0' in x for x in a) or a[0]!='exec' or a[-1]!='-' or a.count('{image}')!=1 or '--image' not in a or a[a.index('--image')+1]!='{image}':raise AdapterError()
        if any(a.count(x)!=1 for x in ('--json','--ephemeral','--ignore-user-config','--ignore-rules','--model')) or a[a.index('--model')+1]!=c['model']:raise AdapterError()
        if not isinstance(c['model'],str) or not 1<=len(c['model'])<=128 or any(ord(x)<32 for x in c['model']):raise AdapterError()
        if type(c['timeout_seconds']) not in (int,float) or not .1<=c['timeout_seconds']<=120:raise AdapterError()
        names=c['env_allowlist']
        if not isinstance(names,list) or len(names)>16 or len(set(names))!=len(names) or any(not isinstance(x,str) or not re.fullmatch('[A-Z][A-Z0-9_]{0,63}',x) or x in ('HOME','PATH','LANG','LD_PRELOAD','LD_LIBRARY_PATH','PYTHONPATH','PYTHONHOME','BASH_ENV','ENV') for x in names):raise AdapterError()
        if 'native_image_transport' in c:
            if any(name in ('BUZZ_PRIVATE_KEY','BUZZ_AUTH_TAG') for name in names):raise AdapterError()
            E.transport_config(c['native_image_transport'])
        self.adapter_raw,self.adapter_inode=E.read_private(Path(__file__).absolute());self.adapter_sha=E.sha(self.adapter_raw)
        self.check()
    def check(self):
        self.manifest.check()
        if E.read_private(self.path,BACKEND_CONFIG_SHA256,16384)!=(self.raw,self.inode) or E.read_private(Path(__file__).absolute())!=(self.adapter_raw,self.adapter_inode):raise AdapterError()
        if 'native_image_transport' in self.doc:E.transport_config(self.doc['native_image_transport'])
        E.read_private(self.manifest.root/'acp_double.py',DOUBLE_SHA256);E.read_private(self.manifest.root/'model_evidence.py',MODEL_EVIDENCE_SHA256)
    def native(self):
        own,ancestors=D.process_ancestry(os.getpid());found=[]
        for pid,start in ancestors:
            try:
                # Reject unrelated executables before reading their full bytes.
                # This fresh path is only a filter; matching native ancestors
                # retain the original complete program digest/identity check.
                before=D.process_metadata(pid)
                if before['start_ticks']!=start:raise E.EvidenceError()
                path=os.readlink(Path('/proc')/str(pid)/'exe')
                if D.process_metadata(pid)!=before:raise E.EvidenceError()
                if path!=self.doc['native_path']:continue
                p=E.program(pid,start,D)
                if D.process_metadata(pid)!=before or os.readlink(Path('/proc')/str(pid)/'exe')!=path:raise E.EvidenceError()
                if p['path']==self.doc['native_path'] and p['sha256']==self.doc['native_sha256']:found.append([pid,start])
            except (OSError,D.DoubleError,E.EvidenceError):continue
        if not found:raise AdapterError()
        # Root later chooses exact original unit PID/start independently.
        return own,ancestors


class OwnedProcess:
    """Retain original Popen leader until bounded owned-lineage cleanup/reap.

    A private adapter subreaper has exactly one backend lineage. No signal uses
    a reaped leader's numeric group. Descendant signals use validated pidfds.
    """
    def __init__(self,proc):
        self.proc=proc;self.original=E.kernel_identity(proc.pid);self.pidfd=os.pidfd_open(proc.pid);self.known={};self.waited=[];self.escaped=False
        try:self.anchor()
        except BaseException:os.close(self.pidfd);raise
    def anchor(self):
        if self.proc.returncode is not None:raise AdapterError()
        now=E.kernel_identity(self.proc.pid);original=self.original
        if any(now[k]!=original[k] for k in original if k!='state') or now['parent_pid']!=os.getpid() or now['pgid']!=self.proc.pid or now['sid']!=self.proc.pid or now['uid']!=E.kernel_identity(os.getpid())['uid']:raise AdapterError()
        return now
    def scan(self,deadline):
        anchor=self.anchor();table=E.process_table(deadline);own=E.kernel_identity(os.getpid());found={}
        if self.proc.pid not in table or table[self.proc.pid]['start_ticks']!=anchor['start_ticks']:raise AdapterError()
        for pid,basic in table.items():
            if pid in (os.getpid(),self.proc.pid):continue
            path=[];node=basic;visited=set()
            while node['pid'] not in visited and len(path)<256:
                visited.add(node['pid']);path.append([node['pid'],node['start_ticks']])
                if node['parent_pid'] in (self.proc.pid,os.getpid()):
                    ancestor=anchor if node['parent_pid']==self.proc.pid else own
                    path.append([ancestor['pid'],ancestor['start_ticks']]);break
                node=table.get(node['parent_pid'])
                if node is None:path=[];break
            else:path=[]
            descended=bool(path) and path[-1] in ([self.proc.pid,anchor['start_ticks']],[os.getpid(),own['start_ticks']])
            if basic['pgid']==anchor['pgid'] and not descended:raise ScanChanged()
            if not descended:continue
            if len(found)>=256:raise AdapterError()
            try:
                identity=E.kernel_identity(pid)
                if identity['start_ticks']!=basic['start_ticks']:raise AdapterError()
                if any(identity[k]!=basic[k] for k in ('parent_pid','pgid','sid')):raise ScanChanged()
                if identity['start_ticks']<anchor['start_ticks'] or identity['uid']!=anchor['uid'] or identity['cgroup_sha256']!=anchor['cgroup_sha256']:raise AdapterError()
                # Validate the actual ancestry from this snapshot, including each start.
                for offset,(parent,start) in enumerate(path[1:],1):
                    current=E.kernel_identity(parent)
                    if current['start_ticks']!=start or current['uid']!=anchor['uid'] or current['cgroup_sha256']!=anchor['cgroup_sha256']:raise AdapterError()
                    if offset<len(path)-1 and current['parent_pid']!=path[offset+1][0]:raise ScanChanged()
                previous=self.known.get(pid)
                if previous is not None and previous['identity']['start_ticks']!=identity['start_ticks']:raise AdapterError()
                if previous is None:
                    fd=os.pidfd_open(pid)
                    try:
                        check=E.kernel_identity(pid)
                        if check['start_ticks']!=identity['start_ticks'] or check['uid']!=identity['uid'] or check['cgroup_sha256']!=identity['cgroup_sha256']:raise AdapterError()
                        if any(check[k]!=identity[k] for k in ('parent_pid','pgid','sid')):raise ScanChanged()
                    except BaseException:os.close(fd);raise
                    self.known[pid]={'identity':identity,'ancestry':path,'fd':fd}
                if identity['pgid']!=anchor['pgid'] or identity['sid']!=anchor['sid']:self.escaped=True
                found[pid]=identity
            except FileNotFoundError:continue
        self.anchor();return found
    def cleanup(self,terminate):
        if getattr(self.proc,'_hostd_lifecycle',None) is not None:return self.proc.returncode,self.proc._hostd_lifecycle
        if self.proc.returncode is not None:raise AdapterError()
        started=time.monotonic_ns();deadline=time.monotonic()+2
        # Observe terminal leader without reaping; its PID/PGID cannot be reused.
        if not terminate:
            while os.waitid(os.P_PID,self.proc.pid,os.WEXITED|os.WNOHANG|os.WNOWAIT) is None:
                self.anchor()
                if time.monotonic()>=deadline:raise AdapterError()
                time.sleep(.005)
        while True:
            if time.monotonic()>=deadline:raise AdapterError()
            try:found=self.scan(deadline)
            except (ScanChanged,E.ProcessChanged):
                # Reparent/group transitions restart a complete bounded scan.
                # No identity failure is converted into permission to signal.
                time.sleep(.005);continue
            if terminate:
                self.anchor();os.killpg(self.proc.pid,signal.SIGKILL)
            # Same original group is anchored. Changed groups use exact owned pidfd.
            if found:
                self.anchor();os.killpg(self.proc.pid,signal.SIGKILL)
            for pid,identity in found.items():
                tracked=self.known[pid]
                try:
                    current=E.kernel_identity(pid)
                    if current['start_ticks']!=identity['start_ticks'] or current['uid']!=identity['uid'] or current['cgroup_sha256']!=identity['cgroup_sha256']:raise AdapterError()
                    if current['pgid']!=self.original['pgid'] or current['sid']!=self.original['sid']:self.escaped=True
                    signal.pidfd_send_signal(tracked['fd'],signal.SIGKILL)
                    if current['parent_pid']==os.getpid():
                        waited,status=os.waitpid(pid,os.WNOHANG)
                        if waited:
                            self.waited.append({'pid':pid,'start_ticks':identity['start_ticks'],'identity':tracked['identity'],'ancestry':tracked['ancestry'],'returncode':os.waitstatus_to_exitcode(status),'wait_observed':True})
                            os.close(tracked['fd']);del self.known[pid]
                except E.ProcessChanged:continue
                except FileNotFoundError:
                    # It was reaped by its actual parent; not an asserted own wait.
                    os.close(tracked['fd']);del self.known[pid]
            leader=os.waitid(os.P_PID,self.proc.pid,os.WEXITED|os.WNOHANG|os.WNOWAIT)
            if not found and leader is not None:
                # No live lineage can fork after this terminal leader snapshot.
                self.anchor();rc=self.proc.wait(timeout=max(.001,deadline-time.monotonic()));self.proc._hostd_original_wait_observed=True
                E.require_group_absent(self.original['pgid'])
                E.require_children_absent(os.getpid(),E.kernel_identity(os.getpid())['start_ticks'])
                if time.monotonic()>=deadline:raise AdapterError()
                lifecycle={'historical':True,'scope':'bounded-owned-descendants-and-original-group','original':{k:v for k,v in self.original.items() if k!='state'},'started_ns':started,'finished_ns':time.monotonic_ns(),'absence_observed':True,'descendant_waits':self.waited,'escaped_observed':self.escaped}
                self.proc._hostd_lifecycle=lifecycle;os.close(self.pidfd)
                for tracked in self.known.values():os.close(tracked['fd'])
                self.known.clear();return rc,lifecycle
            time.sleep(.005)

class ACPReal(D.ACPDouble):
    def __init__(self,manifest,witness,config):
        # Only this private adapter process adopts its backend descendants.
        libc=ctypes.CDLL(None,use_errno=True);flag=ctypes.c_int()
        if libc.prctl(36,1,0,0,0)!=0 or libc.prctl(37,ctypes.byref(flag),0,0,0)!=0 or flag.value!=1:raise AdapterError()
        super().__init__(manifest,witness);self.config=config;self.job=None;self.ledger=E.ModelLedger(manifest,config.check,D);self.audit_fd=None;self.audit_inode=None;self.waits=[]
        if os.path.lexists(manifest.root/'model-input.png'):raise AdapterError()
    def emit(self,value):
        if isinstance(value.get('result'),dict) and 'agentInfo' in value['result']:
            value['result']['agentInfo']={'name':'hostd-l3-real-exec-adapter','version':'1'}
        super().emit(value)
    def error(self,rid,code):
        self.counts['refused']+=1;self.emit({'jsonrpc':'2.0','id':rid,'error':{'code':code,'message':NOTICE}})
    def dispatch(self,req):
        self.config.check()
        if isinstance(req,dict) and req.get('jsonrpc')=='2.0' and req.get('method')=='session/cancel' and 'id' not in req:
            sid=req.get('params',{}).get('sessionId') if isinstance(req.get('params',{}),dict) else None
            if self.job is not None and sid==self.job['sid']:
                self.abort(self.job);self.complete(sid,'cancelled')
            return
        if not isinstance(req,dict) or req.get('method')!='session/prompt':
            # This adapter supports protocol 1; the native client advertises 2.
            # Negotiate that exact integer offer down to 1 without mutating it.
            # Base dispatch retains all envelope, ID, duplicate and state checks.
            if (isinstance(req,dict) and req.get('method')=='initialize'
                    and isinstance(req.get('params'),dict)
                    and type(req['params'].get('protocolVersion')) is int
                    and req['params']['protocolVersion']==2):
                req=dict(req,params=dict(req['params'],protocolVersion=1))
            return super().dispatch(req)
        rid=None
        try:
            if req.get('jsonrpc')!='2.0' or 'id' not in req:raise AdapterError()
            rid=req['id']
            if type(rid) not in (int,str) or isinstance(rid,str) and len(rid)>128:rid=None;raise AdapterError()
            marker=(type(rid).__name__,rid)
            if marker in self.ids or len(self.ids)>=256:self.error(rid,-32600);return
            self.ids.add(marker)
            params=req.get('params');sid=params.get('sessionId') if isinstance(params,dict) else None
            if not self.initialized or not isinstance(sid,str) or sid not in self.sessions or self.counts['prompts']>=D.PROMPT_LIMIT:raise AdapterError()
            if self.job is not None:self.error(rid,-32000);return
            blocks=params.get('prompt');texts,images=self.content(blocks)
            self.counts['prompts']+=1;self.counts['images']+=images
            self.config.native();self.witness.record(sid,self.counts['prompts'],rid,blocks)
            event,raw,image=E.semantic_input(D,blocks,self.config.doc)
            # The input row is unchanged; only exact supported native metadata invokes.
            if not self.witness.records or self.witness.records[-1]['prompt_sha256']!=D._hash_json(blocks):raise AdapterError()
            self.launch(sid,rid,blocks,texts,raw,image,self.witness.records[-1])
        except (AdapterError,D.DoubleError,E.EvidenceError,ValueError,TypeError,KeyError,UnicodeError):self.error(rid,-32602)
    def audit_image(self,raw):
        # Mutable private audit copy; NEVER used as actual child-input proof.
        path=self.manifest.root/'model-input.png';directory=E.open_safe(self.manifest.root,True)
        try:
            if (os.fstat(directory).st_dev,os.fstat(directory).st_ino)!=self.manifest.root_identity:raise AdapterError()
            if self.audit_fd is None:
                self.audit_fd=os.open(path.name,os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=directory);self.audit_inode=(os.fstat(self.audit_fd).st_dev,os.fstat(self.audit_fd).st_ino)
            meta=os.stat(path.name,dir_fd=directory,follow_symlinks=False);E.private(meta)
            if (meta.st_dev,meta.st_ino)!=self.audit_inode:raise AdapterError()
            os.lseek(self.audit_fd,0,0);os.ftruncate(self.audit_fd,0);E.write_all(self.audit_fd,raw);os.fsync(self.audit_fd)
        finally:os.close(directory)
    def launch(self,sid,rid,blocks,texts,raw,image,inp):
        self.config.check();self.audit_image(raw);c=self.config.doc
        image_fd=None;backend_fd=None;proc=None
        try:
            image_fd=os.memfd_create('hostd-acp-image-'+self.manifest.doc['run_id'],os.MFD_CLOEXEC|os.MFD_ALLOW_SEALING);os.fchmod(image_fd,0o600)
            E.write_all(image_fd,raw);os.fsync(image_fd)
            mask=fcntl.F_SEAL_WRITE|fcntl.F_SEAL_GROW|fcntl.F_SEAL_SHRINK|fcntl.F_SEAL_SEAL
            fcntl.fcntl(image_fd,fcntl.F_ADD_SEALS,mask)
            if fcntl.fcntl(image_fd,fcntl.F_GET_SEALS)&mask!=mask:raise AdapterError()
            image_path='/proc/self/fd/'+str(image_fd)
            # FD execution prevents pathname replacement selecting other bytes.
            backend_fd=E.open_safe(c['backend_path']);meta=os.fstat(backend_fd)
            if meta.st_size>512*1024*1024 or not stat.S_ISREG(meta.st_mode) or not meta.st_mode&0o111 or meta.st_mode&0o022 or meta.st_uid not in (0,os.geteuid()):raise AdapterError()
            if os.read(backend_fd,4)!=b'\x7fELF':raise AdapterError()
            os.lseek(backend_fd,0,0);h=hashlib.sha256()
            while True:
                piece=os.read(backend_fd,65536)
                if not piece:break
                h.update(piece)
            if h.hexdigest()!=c['backend_sha256']:raise AdapterError()
            argv=[c['backend_path'],*[image_path if x=='{image}' else x for x in c['backend_args']]]
            env={'HOME':str(self.manifest.root),'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'}
            for name in c['env_allowlist']:
                if name in os.environ:env[name]=os.environ[name]
            started=time.monotonic_ns()
            proc=subprocess.Popen(argv,executable='/proc/self/fd/'+str(backend_fd),pass_fds=(backend_fd,image_fd),stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,cwd=self.manifest.work,env=env,start_new_session=True)
            proc._hostd_owned=OwnedProcess(proc)
            os.close(backend_fd);backend_fd=None
            child=D.process_metadata(proc.pid)
            if child['parent_pid']!=os.getpid() or child['cgroup_sha256']!=inp['cgroup_sha256'] or os.getpgid(proc.pid)!=proc.pid:raise AdapterError()
            child['image_fd']=image_fd
            inherited=os.open('/proc/'+str(proc.pid)+'/fd/'+str(image_fd),os.O_RDONLY|os.O_CLOEXEC)
            try:
                im=os.fstat(inherited);mine=os.fstat(image_fd)
                if (im.st_dev,im.st_ino)!=(mine.st_dev,mine.st_ino) or fcntl.fcntl(inherited,fcntl.F_GET_SEALS)&mask!=mask:raise AdapterError()
            finally:os.close(inherited)
            program_deadline=time.monotonic()+.1
            while True:
                try:
                    child['program']=E.program(proc.pid,child['start_ticks'],D);break
                except (OSError,D.DoubleError,E.EvidenceError):
                    # A transient proc snapshot is retried against the retained
                    # original child; every successful sample still proves the
                    # exact predeath ELF/argv. Never substitute claimed metadata.
                    proc._hostd_owned.anchor()
                    if time.monotonic()>=program_deadline:raise AdapterError()
                    time.sleep(.005)
            if child['program']!={'path':c['backend_path'],'sha256':c['backend_sha256'],'argv':argv}:raise AdapterError()
            for stream in (proc.stdin,proc.stdout,proc.stderr):os.set_blocking(stream.fileno(),False)
            payload='\n'.join(texts).encode()
            self.job={'sid':sid,'rid':rid,'proc':proc,'child':child,'argv':argv,'input':inp,'prompt':blocks,'image':image,'payload':payload,'sent':0,'out':bytearray(),'err_bytes':0,'stdout_eof':False,'stderr_eof':False,'started_ns':started,'deadline':time.monotonic()+c['timeout_seconds'],'image_fd':image_fd}
            self.active[sid]=(rid,self.job['deadline']);proc=None;image_fd=None
        finally:
            if image_fd is not None:os.close(image_fd)
            if backend_fd is not None:os.close(backend_fd)
            if proc is not None:self.reap_proc(proc,None,True)
    def reap_proc(self,proc,start,terminate):
        try:
            owned=getattr(proc,'_hostd_owned',None)
            if owned is None:raise AdapterError()
            if start is not None and owned.original['start_ticks']!=start:raise AdapterError()
            rc,lifecycle=owned.cleanup(terminate)
            if not getattr(proc,'_hostd_wait_recorded',False):
                self.waits.append({'pid':proc.pid,'start_ticks':owned.original['start_ticks'],'returncode':rc,'wait_observed':True,'terminated':terminate,'group_lifecycle':lifecycle});proc._hostd_wait_recorded=True
            return rc
        except (AdapterError,OSError,E.EvidenceError,subprocess.TimeoutExpired):
            if not getattr(proc,'_hostd_wait_recorded',False):
                original=getattr(getattr(proc,'_hostd_owned',None),'original',{})
                self.waits.append({'pid':proc.pid,'start_ticks':original.get('start_ticks'),'returncode':proc.returncode,'wait_observed':getattr(proc,'_hostd_original_wait_observed',False),'terminated':terminate,'group_lifecycle':{'historical':True,'absence_observed':False,'status':'pending','original':{k:v for k,v in original.items() if k!='state'}}});proc._hostd_wait_recorded=True
            raise AdapterError() from None
        finally:
            for stream in (proc.stdin,proc.stdout,proc.stderr):
                if stream is not None and not stream.closed:stream.close()
    def abort(self,job):
        self.reap_proc(job['proc'],job['child']['start_ticks'],True);self.remove_image(job);self.job=None
    def remove_image(self,job):
        os.close(job['image_fd'])
    def tick(self):
        job=self.job
        if job is None:return
        if time.monotonic()>=job['deadline']:
            self.abort(job);self.active.pop(job['sid']);self.error(job['rid'],-32001);return
        proc=job['proc']
        if not proc.stdin.closed:
            if job['sent']==len(job['payload']):proc.stdin.close()
            elif select.select([],[proc.stdin],[],0)[1]:
                try:job['sent']+=os.write(proc.stdin.fileno(),job['payload'][job['sent']:])
                except (BrokenPipeError,OSError):self.abort(job);self.active.pop(job['sid']);self.error(job['rid'],-32001);return
        for name,eof in (('stdout','stdout_eof'),('stderr','stderr_eof')):
            stream=getattr(proc,name)
            if not job[eof] and select.select([stream],[],[],0)[0]:
                piece=os.read(stream.fileno(),65536)
                if not piece:job[eof]=True
                elif name=='stdout':job['out'].extend(piece)
                else:job['err_bytes']+=len(piece)
        if len(job['out'])>LIMIT or job['err_bytes']>LIMIT:
            self.abort(job);self.active.pop(job['sid']);self.error(job['rid'],-32001);return
        if not job['stdout_eof'] or not job['stderr_eof']:return
        try:
            # wait is the actual Popen wait, not an asserted exit status.
            rc=self.reap_proc(proc,job['child']['start_ticks'],False)
            if rc!=0 or proc._hostd_lifecycle['escaped_observed']:raise AdapterError()
            events=[D._json(line) for line in bytes(job['out']).splitlines() if line]
            if not events or len(events)>256 or any(not isinstance(e,dict) for e in events) or events[-1].get('type')!='turn.completed' or sum(e.get('type')=='turn.completed' for e in events)!=1 or any(e.get('type') in ('turn.failed','error') for e in events):raise AdapterError()
            finals=[e['item'] for e in events if e.get('type')=='item.completed' and isinstance(e.get('item'),dict) and e['item'].get('type')=='agent_message']
            if len(finals)!=1 or not isinstance(finals[0].get('text'),str) or not finals[0]['text'].strip() or len(finals[0]['text'].encode())>16384:raise AdapterError()
            text=finals[0]['text']
            completion_id=finals[0].get('id');backend_sessions=[e.get('thread_id') for e in events if e.get('type')=='thread.started']
            backend_session=backend_sessions[0] if len(backend_sessions)==1 else None
            if len(backend_sessions)>1 or any(v is not None and (not isinstance(v,str) or len(v)>128) for v in (completion_id,backend_session)):raise AdapterError()
            if re.search(r'-----BEGIN .*PRIVATE KEY|\bsk-[A-Za-z0-9_-]{16,}|\bAKIA[A-Z0-9]{16}|(?i:authorization[ \t]*:[ \t]*bearer)',text):raise AdapterError()
            self.config.check()
            # Record before publishing; failed evidence persistence produces no success.
            row={'input':job['input'],'prompt':job['prompt'],'image':job['image'],'selected_pubkey':self.config.doc['selected_pubkey'],'adapter':{'path':str(Path(__file__).absolute()),'sha256':self.config.adapter_sha},'backend':{'path':self.config.doc['backend_path'],'sha256':self.config.doc['backend_sha256'],'argv':job['argv'],'model':self.config.doc['model']},'child':job['child'],'completion':text,'completion_id':completion_id,'backend_session_id':backend_session,'completion_sha256':E.sha(text.encode()),'stream_sha256':E.sha(bytes(job['out'])),'started_ns':job['started_ns'],'finished_ns':time.monotonic_ns(),'wait_returncode':rc,'wait_observed':True,'backend_config_sha256':BACKEND_CONFIG_SHA256,'group_lifecycle':proc._hostd_lifecycle}
            self.ledger.record(row);self.remove_image(job);self.job=None
            self.emit({'jsonrpc':'2.0','method':'session/update','params':{'sessionId':job['sid'],'update':{'sessionUpdate':'agent_message_chunk','content':{'type':'text','text':text}}}});self.complete(job['sid'],'end_turn')
        except (AdapterError,D.DoubleError,E.EvidenceError,ValueError,TypeError,KeyError,UnicodeError):
            if self.job is not None:self.abort(job)
            self.active.pop(job['sid'],None);self.error(job['rid'],-32001)
    def run(self):
        os.set_blocking(0,False);os.set_blocking(1,False)
        try:
            while True:
                self.config.check();self.tick();now=time.monotonic()
                if now-self.last_input>D.IDLE_BUDGET or self.input and now-self.last_input>D.WRITE_BUDGET:raise AdapterError()
                if not select.select([0],[],[],.01)[0]:continue
                piece=os.read(0,8192)
                if not piece:
                    if self.input:raise AdapterError()
                    if self.job is not None:
                        sid=self.job['sid'];self.abort(self.job);self.complete(sid,'cancelled')
                    return
                self.last_input=time.monotonic();self.input+=piece
                while b'\n' in self.input:
                    line,self.input=self.input.split(b'\n',1)
                    if len(line)>D.FRAME_LIMIT:raise AdapterError()
                    try:req=D._json(line)
                    except (D.DoubleError,ValueError,UnicodeError):self.error(None,-32700);continue
                    self.dispatch(req)
                if len(self.input)>D.FRAME_LIMIT:raise AdapterError()
        finally:
            if self.job is not None:self.abort(self.job)

def main(argv=None):
    manifest=witness=backend=None;failed=False
    try:
        p=D.SafeParser(add_help=False,exit_on_error=False)
        for key in ('--manifest','--sha256','--witness-manifest','--witness-sha256'):p.add_argument(key,required=True)
        args=p.parse_args(argv);manifest=D.Manifest(args.manifest,args.sha256)
        if Path(__file__).absolute().parent!=manifest.root:raise AdapterError()
        witness=D.PromptWitness(D.WitnessConfig(manifest,args.witness_manifest,args.witness_sha256));backend=ACPReal(manifest,witness,Config(manifest));backend.run()
    except BaseException:failed=True
    finally:
        if witness is not None:witness.close()
        if backend is not None:
            backend.ledger.close()
            if backend.audit_fd is not None:os.close(backend.audit_fd)
    if backend is not None:
        try:
            manifest.check();fd=E.open_safe(manifest.root,True)
            try:
                if (os.fstat(fd).st_dev,os.fstat(fd).st_ino)!=manifest.root_identity:raise AdapterError()
                out=os.open(manifest.receipt.name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=fd)
                try:E.write_all(out,json.dumps({'backend':'real_exec_adapter','live_verified':False,'status':'failed' if failed else 'closed','backend_waits':backend.waits,**backend.counts}).encode()+b'\n');os.fsync(out)
                finally:os.close(out)
            finally:os.close(fd)
        except BaseException:failed=True
    if failed:
        try:os.write(2,(NOTICE+'\n').encode())
        except OSError:pass
    return int(failed)

if __name__=='__main__':sys.exit(main())
