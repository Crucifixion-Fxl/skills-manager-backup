"""Explicit metadata tap on the original SDK child; no SDK/network dependency.

Neither this artifact nor a connection marker is a business/scenario verdict.
Only the owning parent may attach its existing child's stdout to the tap.
"""
from __future__ import annotations
import asyncio
from dataclasses import dataclass,field
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import time
from .async_io import thread_call

TEST_APPS={'hostd-test-desk':'cli_aa48bbeeba38dbcf','hostd-test-a':'cli_aa48b41531785bfc','hostd-test-b':'cli_aa48b406abf8dbe7'}
SOURCE_NAMES=('sdk_evidence.py','feishu_feed.py','__main__.py')
HEX=re.compile(r'[0-9a-f]{64}')
IDENT=re.compile(r'[A-Za-z0-9_.:-]{1,128}')
EVENTS=frozenset(('im.message.receive_v1','im.message.reaction.created_v1','im.message.reaction.deleted_v1',
    'im.chat.member.bot.added_v1','im.chat.member.bot.deleted_v1','im.chat.member.user.added_v1',
    'im.chat.member.user.deleted_v1','im.chat.member.user.withdrawn_v1','card.action.trigger'))
NOTICE='SDK 证据未通过安全验证；怎么解决：检查独立配置摘要、私有路径、窗口和原子进程。复制给 AI：检查 hostd 单连接证据元数据，不要显示凭据、事件正文或操作人标识。'
class EvidenceError(ValueError):
    def __init__(self):super().__init__(NOTICE)
def require(value):
    if not value:raise EvidenceError()
def digest(raw):return hashlib.sha256(raw).hexdigest()
def number(value):return type(value) in (int,float) and math.isfinite(value)
def identifier(value):return isinstance(value,str) and IDENT.fullmatch(value) is not None
def unique(pairs):
    result={}
    for key,value in pairs:
        require(key not in result);result[key]=value
    return result

def absolute(value):
    path=Path(value);require(path.is_absolute() and '..' not in path.parts and bool(path.name));return path

def identity(meta):return (meta.st_dev,meta.st_ino,meta.st_uid,meta.st_mode,meta.st_nlink)
def directory_pin(meta):return identity(meta)[:4]
def file_pin(meta):return identity(meta)+(meta.st_size,meta.st_mtime_ns,meta.st_ctime_ns)
def parent(path):
    path=absolute(path);fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        for name in path.parts[1:-1]:
            child=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=fd)
            os.close(fd);fd=child
        return fd
    except BaseException:os.close(fd);raise

def read_file(path,*,private,limit):
    path=absolute(path);directory=parent(path);fd=None
    try:
        base=os.fstat(directory)
        if private:require(base.st_uid==os.geteuid() and stat.S_IMODE(base.st_mode)==0o700)
        fd=os.open(path.name,os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=directory)
        before=os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_nlink==1)
        if private:require(before.st_uid==os.geteuid() and stat.S_IMODE(before.st_mode)==0o600)
        raw=os.read(fd,limit+1);require(len(raw)<=limit)
        require(file_pin(before)==file_pin(os.fstat(fd))==file_pin(os.stat(path.name,dir_fd=directory,follow_symlinks=False)))
        return raw,file_pin(before),directory_pin(base)
    finally:
        if fd is not None:os.close(fd)
        os.close(directory)

def output_parent(path):
    fd=parent(path)
    try:
        meta=os.fstat(fd);require(meta.st_uid==os.geteuid() and stat.S_IMODE(meta.st_mode)==0o700)
        return directory_pin(meta)
    finally:os.close(fd)

REACTIONS=frozenset(('im.message.reaction.created_v1','im.message.reaction.deleted_v1'))
TARGET_FIELDS=frozenset(('app_id','message_id','root_message_id','channel_id','original_event_id','root_event_id',
    'target_sha256','native_target_sha256','actor_union_sha256'))

def reaction_declarations(ref,*,run_id,chat_id,apps,excluded):
    """Consume Root-pinned original-owner artifacts; never certify native/live authority.

    The finite Source owner must validate its signed/native/store/roster gates
    before issuing the receipt. Explicit protected config hashing authorizes this
    narrowly scoped declaration. No callback or cache may mint a declaration.
    """
    inputs=[];seen=set(excluded)
    def pinned(reference,limit):
        require(isinstance(reference,dict) and set(reference)=={'path','sha256'})
        require(isinstance(reference['sha256'],str) and HEX.fullmatch(reference['sha256']))
        path=absolute(reference['path']);require(path not in seen);seen.add(path)
        raw,pin,base=read_file(path,private=True,limit=limit);require(digest(raw)==reference['sha256'])
        inputs.append((path,reference['sha256'],pin,base))
        return raw
    doc=json.loads(pinned(ref,65536),object_pairs_hook=unique)
    require(isinstance(doc,dict) and set(doc)=={'schema_version','run_id','source_revision','chat_id','targets','authority_pins'})
    require(type(doc['schema_version']) is int and doc['schema_version']==1 and doc['run_id']==run_id and doc['chat_id']==chat_id)
    require(isinstance(doc['source_revision'],str) and re.fullmatch('[0-9a-f]{40}',doc['source_revision']))
    targets=doc['targets'];require(isinstance(targets,list) and 1<=len(targets)<=2)
    result=[];keys=set()
    for target in targets:
        require(isinstance(target,dict) and set(target)==TARGET_FIELDS|{'owner_receipt'})
        require(target['app_id'] in apps and identifier(target['channel_id']))
        require(all(isinstance(target[k],str) and re.fullmatch('om_[A-Za-z0-9_-]{1,128}',target[k]) for k in ('message_id','root_message_id')))
        require(all(isinstance(target[k],str) and HEX.fullmatch(target[k]) for k in TARGET_FIELDS-{'app_id','message_id','root_message_id','channel_id'}))
        key=(target['app_id'],target['message_id']);require(key not in keys);keys.add(key)
        receipt=json.loads(pinned(target['owner_receipt'],65536),object_pairs_hook=unique)
        require(isinstance(receipt,dict) and set(receipt)=={'schema_version','run_id','source_revision','chat_id','target'})
        require(type(receipt['schema_version']) is int and receipt['schema_version']==1)
        require(all(receipt[k]==doc[k] for k in ('run_id','source_revision','chat_id')))
        expected={k:target[k] for k in TARGET_FIELDS};require(receipt['target']==expected)
        result.append((key,json.dumps(expected,separators=(',',':')),ref['sha256']))
    authorities=doc['authority_pins'];require(isinstance(authorities,list) and 1<=len(authorities)<=16)
    for authority in authorities:pinned(authority,1048576)
    return tuple(result),tuple(inputs)

@dataclass(frozen=True)
class EvidenceConfig:
    path:Path=field(repr=False)
    sha256:str
    run_id:str
    start:float
    end:float
    chat_id:str=field(repr=False)
    apps:tuple
    events_path:Path=field(repr=False)
    receipt_path:Path=field(repr=False)
    max_rows:int
    max_bytes:int
    _config_pin:tuple=field(repr=False)
    _parent_pin:tuple=field(repr=False)
    _sources:tuple=field(repr=False)
    _output_parents:tuple=field(repr=False)
    _reaction_targets:tuple=field(default=(),repr=False)
    _reaction_inputs:tuple=field(default=(),repr=False)
    @classmethod
    def check(cls,path,sha256,*,clock=time.time):
        try:
            require(isinstance(sha256,str) and HEX.fullmatch(sha256));path=absolute(path)
            raw,pin,base=read_file(path,private=True,limit=65536);require(digest(raw)==sha256)
            data=json.loads(raw,object_pairs_hook=unique)
            required={'schema_version','run_id','window','target','events_path','receipt_path','max_rows','max_bytes','source_sha256'}
            require(isinstance(data,dict) and required<=set(data)<=required|{'reaction_targets'})
            require(type(data['schema_version']) is int and data['schema_version']==1 and identifier(data['run_id']))
            window=data['window'];target=data['target']
            require(isinstance(window,dict) and set(window)=={'start','end'} and all(number(v) for v in window.values()))
            start,end=window['start'],window['end'];now=clock()
            require(number(now) and 0<end-start<=90 and end>now and start<=now+5)
            require(isinstance(target,dict) and set(target)=={'chat_id','apps'} and isinstance(target['chat_id'],str)
                and re.fullmatch(r'oc_[A-Za-z0-9_-]{1,128}',target['chat_id']))
            apps=target['apps'];require(isinstance(apps,dict) and 1<=len(apps)<=3)
            require(all(TEST_APPS.get(bot)==app for bot,app in apps.items()))
            require(type(data['max_rows']) is int and 0<data['max_rows']<=1000)
            require(type(data['max_bytes']) is int and 0<data['max_bytes']<=1048576)
            sources=data['source_sha256'];require(isinstance(sources,dict) and set(sources)=={'hostd/'+n for n in SOURCE_NAMES})
            pins=[]
            for name in SOURCE_NAMES:
                expected=sources['hostd/'+name];require(isinstance(expected,str) and HEX.fullmatch(expected))
                source=Path(__file__).absolute().parent/name
                source_raw,source_pin,_=read_file(source,private=False,limit=1048576)
                require(digest(source_raw)==expected);pins.append((source,expected,source_pin))
            outputs=tuple(absolute(data[n]) for n in ('events_path','receipt_path'))
            require(len(set(outputs))==2 and path not in outputs)
            parents=tuple(output_parent(p) for p in outputs);require(all(not os.path.lexists(p) for p in outputs))
            declarations,inputs=(),()
            if 'reaction_targets' in data:
                declarations,inputs=reaction_declarations(data['reaction_targets'],run_id=data['run_id'],chat_id=target['chat_id'],
                    apps=set(apps.values()),excluded=(path,*outputs))
            return cls(path,sha256,data['run_id'],float(start),float(end),target['chat_id'],tuple(sorted(apps.items())),
                *outputs,data['max_rows'],data['max_bytes'],pin,base,tuple(pins),parents,declarations,inputs)
        except Exception:raise EvidenceError() from None
    def revalidate(self,*,fresh=False):
        try:
            raw,pin,base=read_file(self.path,private=True,limit=65536)
            require(digest(raw)==self.sha256 and pin==self._config_pin and base==self._parent_pin)
            for path,expected,pinned in self._sources:
                raw,found,_=read_file(path,private=False,limit=1048576)
                require(digest(raw)==expected and found==pinned)
            for path,expected,pinned,parent_pin in self._reaction_inputs:
                raw,found,base=read_file(path,private=True,limit=1048576)
                require(digest(raw)==expected and found==pinned and base==parent_pin)
            for path,pinned in zip((self.events_path,self.receipt_path),self._output_parents):
                require(output_parent(path)==pinned)
                if fresh:require(not os.path.lexists(path))
        except Exception:raise EvidenceError() from None

    def reaction_target(self,app,message):
        matches=[json.loads(raw) for key,raw,_ in self._reaction_targets if key==(app,message)]
        return matches[0] if len(matches)==1 else None
    def reaction_grant_sha256(self,app,message):
        matches=[pin for key,_,pin in self._reaction_targets if key==(app,message)]
        require(len(matches)==1);return matches[0]

class FeedEvidenceWire:
    """Only EventClient's completed SDK hook establishes the active wire session."""
    def __init__(self,app_id,run_id,nonce):
        require(app_id in TEST_APPS.values() and identifier(run_id) and isinstance(nonce,str) and HEX.fullmatch(nonce))
        self.app_id,self.run_id,self.nonce=app_id,run_id,nonce;self.session=None;self.failed=False
    def connected(self,client):
        try:
            require(client._conn is not None and client._app_id==self.app_id and identifier(client._conn_id))
            connection=digest(client._conn_id.encode())
            self.session={'version':1,'run_id':self.run_id,'child_nonce':self.nonce,
                'connection_id':connection,'session_id':digest((connection+'\0'+self.nonce+'\0'+secrets.token_hex(32)).encode())}
        except Exception:self.failed=True;self.session=None
    def attach(self,row,raw=None):
        if self.failed or self.session is None:return row
        if raw is not None:
            header=raw.get('header') if isinstance(raw,dict) else None
            if not isinstance(header,dict) or header.get('app_id')!=self.app_id:
                self.failed=True;self.session=None;return row
        result=dict(row);result['_sdk_evidence']=dict(self.session);return result
    def closed(self):self.session=None

@dataclass
class _Child:
    process:object=field(repr=False)
    app:str
    argv:tuple=field(repr=False)
    nonce:str=field(repr=False)
    proof:tuple|None=None
    session:dict|None=None
    sessions:list=field(default_factory=list)

def process_proof(process,argv):
    """Kernel facts for the exact original asyncio child, never a row's alive flag."""
    require(isinstance(process,asyncio.subprocess.Process))
    fd=os.open('/proc/'+str(process.pid),os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        require(os.fstat(fd).st_uid==os.geteuid())
        def read(name):
            child=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=fd)
            try:return os.read(child,65537)
            finally:os.close(child)
        before=read('stat').decode().rsplit(')',1)[1].split()
        require(before[0]!='Z' and int(before[1])==os.getpid())
        command=read('cmdline');require(command==b'\0'.join(os.fsencode(a) for a in argv)+b'\0')
        require(os.readlink('exe',dir_fd=fd)==str(Path(sys.executable).resolve()))
        executable=os.open('exe',os.O_RDONLY|os.O_CLOEXEC,dir_fd=fd)
        try:
            meta=os.fstat(executable);require(stat.S_ISREG(meta.st_mode) and meta.st_size<=512*1024*1024)
            with os.fdopen(os.dup(executable),'rb') as stream:binary=hashlib.file_digest(stream,'sha256').hexdigest()
        finally:os.close(executable)
        after=read('stat').decode().rsplit(')',1)[1].split();require(before[19]==after[19] and after[0]!='Z')
        return (process.pid,int(before[19]),int(before[1]),os.geteuid(),file_pin(meta),binary,digest(command))
    finally:os.close(fd)

class EvidenceTap:
    def __init__(self,config,*,clock=time.time):
        require(type(config) is EvidenceConfig and callable(clock));self.config=config;self.clock=clock
        self.queue=asyncio.Queue(maxsize=config.max_rows);self.children={};self.task=None
        self.state='prepared';self.rows=0;self.bytes=0;self.enqueued=0;self.queued_bytes=0
        self.events_hash=hashlib.sha256();self.receipt_bytes=b'';self.outputs=[];self._closing=False
    def disable(self):self.state='disabled'
    def readback(self):return {'state':self.state,'verdict':'pending','live_verified':False}
    async def start(self):
        if self.task is not None:return
        try:await thread_call(self._open)
        except Exception:self.disable()
        self.task=asyncio.create_task(self._worker())
    def _open(self):
        self.config.revalidate(fresh=True)
        for path,pinned in zip((self.config.events_path,self.config.receipt_path),self.config._output_parents):
            directory=parent(path)
            try:
                require(directory_pin(os.fstat(directory))==pinned)
                fd=os.open(path.name,os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=directory)
                self.outputs.append((path,directory,fd,identity(os.fstat(fd))))
            except BaseException:os.close(directory);raise
        self.state='recording';self._publish()
    def register_child(self,app,process,argv,nonce):
        if self.state!='recording' or app not in dict(self.config.apps).values():return False
        try:
            require(len(self.children)<32 and id(process) not in self.children)
            require(isinstance(nonce,str) and HEX.fullmatch(nonce))
            argv=tuple(argv);require(len(argv)==9 and argv[0]==sys.executable and argv[1]==str(self.config._sources[1][0]) and argv[2]==app
                and argv[-4:]==('--sdk-evidence-run-id',self.config.run_id,'--sdk-evidence-nonce',nonce))
            child=_Child(process,app,argv,nonce);self.children[id(process)]=child
            self.queue.put_nowait(('child',child,None));return True
        except Exception:self.disable();return False
    def child_ended(self,process):
        # EOF cannot invent an SDK disconnect event. Expired/unknown producer
        # invalidates evidence; never changes the business process lifecycle.
        child=self.children.get(id(process))
        if child is not None:child.session=None
    def enqueue(self,app,process,envelope,event):
        if self.state!='recording' or app not in dict(self.config.apps).values():return
        try:
            now=self.clock();require(number(now))
            if now>self.config.end:return
            if event.get('type')=='_connecting':return
            child=self.children.get(id(process));require(child is not None and child.process is process and child.app==app and event.get('app')==app)
            require(isinstance(envelope,dict) and set(envelope)=={'version','run_id','child_nonce','session_id','connection_id'})
            require(type(envelope['version']) is int and envelope['version']==1 and envelope['run_id']==self.config.run_id and envelope['child_nonce']==child.nonce)
            require(all(isinstance(envelope[k],str) and HEX.fullmatch(envelope[k]) for k in ('session_id','connection_id')))
            received=event.get('t');require(number(received) and self.config.start<=received<=now<=self.config.end)
            etype=event.get('type');require(etype in EVENTS|{'_connected','_disconnected'})
            if etype=='_connected':
                require(child.session is None and envelope['session_id'] not in child.sessions and len(child.sessions)<64)
                child.session=dict(envelope);child.sessions.append(envelope['session_id']);kind='_connection_started'
            else:
                require(child.session==envelope);kind='_connection_closed' if etype=='_disconnected' else etype
            row={'schema_version':2,'run_id':self.config.run_id,'t_recv':received,'bot':next(bot for bot,a in self.config.apps if a==app),
                'app_id':app,'session_id':envelope['session_id'],'connection_id':envelope['connection_id'],'type':kind}
            if etype in EVENTS:
                chat=event.get('chat_id');require(isinstance(chat,str))
                if etype in REACTIONS:
                    target=self.config.reaction_target(app,event.get('message_id'))
                    if target is None:
                        if not self.config._reaction_targets:return
                        require(False)
                    require(chat in ('',self.config.chat_id))
                    self._reaction(row,event,target,received)
                    row.update(callback_chat_id=chat,chat_resolution='declared-target',
                        reaction_target_sha256=self.config.reaction_grant_sha256(app,event.get('message_id')))
                    chat=self.config.chat_id
                if chat!=self.config.chat_id:return
                require(identifier(event.get('event_id')));row.update(chat_id=chat,event_id=event['event_id'])
                message=event.get('message_id')
                if etype=='card.action.trigger':
                    context=event.get('context');message=context.get('open_message_id') if isinstance(context,dict) else None
                if message:require(identifier(message));row['message_id']=message
                if etype=='im.message.receive_v1':require('message_id' in row)
                if etype=='im.chat.member.bot.added_v1':row['target_app_id']=app
            if etype=='_disconnected':child.session=None
            payload=(json.dumps(row,separators=(',',':'),allow_nan=False)+'\n').encode()
            require(self.enqueued<self.config.max_rows and self.queued_bytes+len(payload)<=self.config.max_bytes)
            self.queue.put_nowait(('event',child,payload));self.enqueued+=1;self.queued_bytes+=len(payload)
        except Exception:self.disable()
    def _reaction(self,row,event,target,received):
        require(isinstance(event.get('reaction_type'),str) and re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,63}',event['reaction_type']))
        action=event.get('action_time');require(isinstance(action,str) and re.fullmatch('[0-9]{1,16}',action))
        require(math.ceil(self.config.start*1000)<=int(action)<=math.floor(received*1000))
        operator=event.get('operator_type');require(operator in ('user','app'))
        actor=event.get('actor_id_hash');require(isinstance(actor,str) and HEX.fullmatch(actor))
        if operator=='user':
            require(event.get('actor_namespace')=='user-union' and actor==target['actor_union_sha256'])
            require(event.get('_operator_app_id') is None and event.get('_operator_user_present') is True and 'actor_app_id' not in event)
            require(event.get('operator_union_id_hash')==actor)
            for key,namespace in (('union_id','user-union'),('open_id','user-open'),('user_id','user-id')):
                name='operator_'+key+'_hash'
                if name in event:
                    require(isinstance(event[name],str) and HEX.fullmatch(event[name]))
                    row[name]=event[name]
                    row['operator_'+key+'_namespace']=namespace if key=='union_id' else namespace+':'+row['app_id']
        else:
            require(event.get('actor_namespace')=='bot-app' and event.get('actor_app_id')==target['app_id'])
            require(event.get('_operator_app_id')==target['app_id'] and event.get('_operator_user_present') is False)
            require(not any(k.startswith('operator_') and k.endswith('_hash') for k in event))
            require(actor==digest(('bot-app\0'+target['app_id']).encode()));row['actor_app_id']=target['app_id']
        row.update(reaction_type=event['reaction_type'],operator_type=operator,action_time=action,
            actor_namespace=event['actor_namespace'],actor_id_hash=actor)
    def _check_outputs(self):
        require(len(self.outputs)==2)
        for index,(path,directory,fd,pinned) in enumerate(self.outputs):
            require(directory_pin(os.fstat(directory))==self.config._output_parents[index])
            opened=parent(path)
            try:require(directory_pin(os.fstat(opened))==directory_pin(os.fstat(directory)))
            finally:os.close(opened)
            found=os.fstat(fd)
            require(stat.S_ISREG(found.st_mode) and found.st_uid==os.geteuid() and stat.S_IMODE(found.st_mode)==0o600 and found.st_nlink==1)
            require(identity(found)==pinned==identity(os.stat(path.name,dir_fd=directory,follow_symlinks=False)))
            raw=os.pread(fd,self.config.max_bytes+1,0)
            expected=self.events_hash.hexdigest() if index==0 else digest(self.receipt_bytes)
            require(len(raw)==(self.bytes if index==0 else len(self.receipt_bytes)) and digest(raw)==expected)
    def _receipt(self):
        children=[]
        for child in self.children.values():
            if child.proof is not None:
                pid,start,ppid,uid,exe,binary,argv=child.proof
                children.append({'app_id':child.app,'pid':pid,'start':start,'executable_sha256':binary,'argv_sha256':argv,'sessions':list(child.sessions)})
        return {'schema_version':1,'run_id':self.config.run_id,'config_sha256':self.config.sha256,
            'source_sha256':{'hostd/'+p.name:h for p,h,_ in self.config._sources},'state':self.state,'verdict':'pending',
            'events_sha256':self.events_hash.hexdigest(),'rows':self.rows,'bytes':self.bytes,'children':children,'live_verified':False}
    def _publish(self):
        self._check_outputs();payload=(json.dumps(self._receipt(),separators=(',',':'))+'\n').encode()
        require(len(payload)<=65536 and self.bytes+len(payload)<=self.config.max_bytes)
        fd=self.outputs[1][2];os.lseek(fd,0,os.SEEK_SET);require(os.write(fd,payload)==len(payload));os.ftruncate(fd,len(payload));os.fsync(fd)
        self.receipt_bytes=payload
    def _record(self,kind,child,payload):
        if self.state!='recording':return
        self.config.revalidate();self._check_outputs()
        current=process_proof(child.process,child.argv)
        if kind=='child':child.proof=current
        else:
            require(child.proof==current)
            require(self.bytes+len(payload)+len(self.receipt_bytes)<=self.config.max_bytes)
            fd=self.outputs[0][2];os.lseek(fd,0,os.SEEK_END);require(os.write(fd,payload)==len(payload));os.fsync(fd)
            self.events_hash.update(payload);self.bytes+=len(payload);self.rows+=1
        self._publish()
    async def _worker(self):
        while True:
            try:
                item=await asyncio.wait_for(self.queue.get(),max(.01,self.config.end-self.clock()))
            except asyncio.TimeoutError:
                if self.state=='recording':self.state='closed'
                break
            if item is None:break
            try:await thread_call(self._record,*item)
            except Exception:self.disable()
            finally:self.queue.task_done()
        try:
            if self.state=='recording':self.state='closed'
            await thread_call(self.config.revalidate)
            await thread_call(self._publish)
        except Exception:self.disable()
    async def close(self):
        if self._closing:return
        self._closing=True;cancelled=False
        if self.task is not None and not self.task.done():
            try:
                await asyncio.wait_for(self.queue.put(None),3)
                await asyncio.wait_for(asyncio.shield(self.task),5)
            except asyncio.CancelledError:
                cancelled=True;self.disable();self.task.cancel()
            except Exception:
                self.disable();self.task.cancel()
            # Never close/reuse FDs while an already dispatched write still
            # owns them. thread_call reaps that exact IO before task settles.
            while not self.task.done():
                try:await asyncio.shield(self.task)
                except asyncio.CancelledError:
                    if asyncio.current_task().cancelling():cancelled=True
                except Exception:self.disable();break
        if self.task is not None and self.task.done():
            try:self.task.result()
            except asyncio.CancelledError:pass
            except Exception:self.disable()
        for _,directory,fd,_ in self.outputs:os.close(fd);os.close(directory)
        self.outputs=[]
        if cancelled:raise asyncio.CancelledError
