"""Private run-scoped MSG003 trigger and read-only evidence, never L3 GO.

The normal daemon owns discovery, authority, dispatch and all native writes.
SourceRun pins an installed B-owned actor; its credentials remain in B's sealed
catalog. The foreign mirror's ScenarioPlan supplies image/phase metadata only.
"""
from __future__ import annotations
import asyncio
from contextlib import closing
from dataclasses import dataclass, field
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import sqlite3
import stat
import subprocess
import time
import math
from datetime import datetime, timezone

from . import prepare, zero_local_run as zero, scenario_driver as scenario
from hostd import agent_catalog, delivery_mapping
from hostd.agent_signed_reads import OwnAgentReader
from hostd.bot_clients import BotLarkCli, OwnImage
from hostd.http_pool import HttpPool
from recovery_origin import canonical_origin
import buzz_feishu_group_sync as gs

NOTICE = 'MSG003 remains pending; check this run’s protected inputs and complete original readback. Unknown source publication must never be repeated.'
SOURCE_KEYS = frozenset('version run_id host_b_manifest_sha256 agent_name buzz_cli buzz_cli_sha256 phase_dir scenario_manifest_sha256 sdk'.split())

class Msg003Error(ValueError):
    def __init__(self): super().__init__(NOTICE)

def _require(value):
    if not value: raise Msg003Error()

def _digest(value):
    if not isinstance(value, bytes): value = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
    return hashlib.sha256(value).hexdigest()

def _read(path): return zero._read(Path(path), mode=0o600)[0]
def _phase(b): return 'running' if Path(b._data()['state_db']).exists() else 'prepared'
def _catalog(b):
    d=b._data()
    return agent_catalog.load(d['catalog_path'], legacy_join_path=d['legacy_join_path'])

def _write_new(path, value):
    """Durable O_EXCL UNKNOWN intent; no replacement or uncertain retry."""
    parent=prepare._open(path.parent, directory=True, private=True)
    try:
        fd=os.open(path.name, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC, 0o600, dir_fd=parent)
        with os.fdopen(fd, 'wb') as out:
            out.write(json.dumps(value, sort_keys=True, separators=(',', ':')).encode());out.flush();os.fsync(out.fileno())
        os.fsync(parent)
    finally: os.close(parent)

def _run_source_cli(argv, *, input, capture_output, text, check, timeout, env):
    """Own this CLI's new session and discard its nonauthoritative output.

    The root pidfd is acquired before admission, so a failed post-spawn check
    still joins the actual child. Descendants stop before the root, retaining
    its opportunity to reap them. No installed actor service is stopped.
    """
    _require(capture_output is True and text is True and check is False and 0<timeout<=30)
    process=subprocess.Popen(argv,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,text=True,env=env,close_fds=True,start_new_session=True,umask=0o077)
    root=None;root_fd=None
    try:
        root_fd=os.pidfd_open(process.pid,0)
        root=zero._proc(process.pid)
        _require(root is not None and root[2]==process.pid and root[4]==os.getuid())
        process.communicate(input=input,timeout=timeout)
        return subprocess.CompletedProcess(argv,process.returncode,'','')
    finally:
        try:
            # Read a previously unavailable identity before any poll/wait can
            # reap the root. The Popen child is still our unreaped direct child.
            if root is None:
                try:root=zero._proc(process.pid)
                except Exception:root=None
            if root is None or root[2]!=process.pid or root[4]!=os.getuid():
                if root_fd is not None:
                    try:signal.pidfd_send_signal(root_fd,signal.SIGTERM)
                    except ProcessLookupError:pass
                try:process.wait(timeout=.3)
                except subprocess.TimeoutExpired:
                    _require(root_fd is not None)
                    signal.pidfd_send_signal(root_fd,signal.SIGKILL);process.wait(timeout=2)
            else:
                deadline=time.monotonic()+4;started=time.monotonic();seen={process.pid:root}
                while True:
                    process.poll()
                    current_root=zero._proc(process.pid)
                    _require(current_root is None or zero._identity(current_root)==zero._identity(root))
                    rows=zero._session(process.pid)
                    for pid,row in rows.items():
                        _require(row[4]==root[4] and row[3]>=root[3])
                        previous=seen.setdefault(pid,row);_require(zero._identity(previous)==zero._identity(row))
                    if not rows and process.returncode is not None:break
                    _require(time.monotonic()<deadline)
                    sig=signal.SIGTERM if time.monotonic()-started<.3 else signal.SIGKILL
                    descendants=[row for pid,row in rows.items() if pid!=process.pid]
                    for row in descendants:zero._signal_original(row,sig)
                    if not descendants:zero._signal_original(rows.get(process.pid),sig)
                    time.sleep(.02)
                process.wait(timeout=.1)
        except BaseException:
            # Even an unavailable identity/session observation cannot skip
            # joining this known direct child through its original pidfd.
            if root_fd is not None and process.returncode is None:
                try:signal.pidfd_send_signal(root_fd,signal.SIGKILL)
                except ProcessLookupError:pass
                process.wait(timeout=2)
            raise
        finally:
            if root_fd is not None:os.close(root_fd)
            if process.stdin is not None:process.stdin.close()

@dataclass(frozen=True)
class Msg003SourceRun:
    """Strict root-pinned source descriptor, not authority supplied by a caller.

    Root supplies SHA256 of this owner-only manifest. Paths reference the
    installed Buzz CLI and existing B catalog, never copied agent credentials.
    sdk is null or exactly {manifest_path, manifest_sha256, checker_sha256,
    session_sha256, event_type, started, deadline}.
    """
    host_b_run: zero.ZeroLocalRunPlan = field(repr=False)
    _path: Path = field(repr=False)
    _raw: bytes = field(repr=False)
    _meta: tuple = field(repr=False)
    _binary_meta: tuple = field(repr=False)
    _binary_sha: str
    _phase_meta: tuple = field(repr=False)

    @classmethod
    def check(cls, host_b_run, manifest_path, manifest_sha256):
        try:
            _require(type(host_b_run) is zero.ZeroLocalRunPlan)
            host_b_run.revalidate(phase=_phase(host_b_run))
            path=zero._absolute(str(manifest_path));raw,meta=zero._read(path,mode=0o600)
            _require(_digest(raw)==manifest_sha256)
            d=zero._json(raw)
            _require(type(d.get('version')) is int and d['version'] in (1,2))
            _require(set(d)==(SOURCE_KEYS if d['version']==1 else SOURCE_KEYS|{'root'}))
            if d['version']==2:
                root=d['root']
                _require(isinstance(root,dict) and set(root)=={'event_id','event_sha256','native_message_id'})
                _require(all(isinstance(root[k],str) and zero.HEX64.fullmatch(root[k]) for k in ('event_id','event_sha256')))
                _require(isinstance(root['native_message_id'],str) and gs.MESSAGE_ID_RE.fullmatch(root['native_message_id']))
            b=host_b_run._data()
            _require(d['run_id']==b['run_id'] and d['host_b_manifest_sha256']==_digest(host_b_run._manifest_bytes))
            _require(isinstance(d['agent_name'],str) and bool(d['agent_name']))
            phase=zero._absolute(d['phase_dir']);phase_meta=zero._directory(phase, private=True)
            _require(path.parent==phase and not phase.is_relative_to(Path(b['run_dir'])))
            binary=zero._absolute(d['buzz_cli']);binary_raw,binary_meta=zero._read(binary,source=True)
            _require(os.access(binary,os.X_OK) and not stat.S_IMODE(binary_meta[3])&0o022 and _digest(binary_raw)==d['buzz_cli_sha256'])
            installed=zero._json(_read(b['catalog_path']))['buzz']
            _require(binary==zero._absolute(installed['cli_path']) and d['buzz_cli_sha256']==installed['cli_sha256'])
            _require(zero.HEX64.fullmatch(d['scenario_manifest_sha256']) is not None)
            sdk=d['sdk']
            if sdk is not None:
                _require(isinstance(sdk,dict) and set(sdk)=={'manifest_path','manifest_sha256','checker_sha256','session_sha256','event_type','started','deadline'})
                _require(zero.HEX64.fullmatch(sdk['session_sha256']) is not None and sdk['event_type']=='im.message.receive_v1')
                _require(zero.HEX64.fullmatch(sdk['manifest_sha256']) is not None and zero.HEX64.fullmatch(sdk['checker_sha256']) is not None)
                recorder=zero._absolute(sdk['manifest_path']);_require(recorder.parent==phase and _digest(_read(recorder))==sdk['manifest_sha256'])
                checker=host_b_run._reviewed_source/'skills/agent-harness/buzz-agent-setup/tests/hostd_probes/check_feishu_events.py'
                _require(_digest(zero._read(checker,source=True)[0])==sdk['checker_sha256'])
                _require(type(sdk['started']) is int and type(sdk['deadline']) is int and 0<=sdk['started']<sdk['deadline']<=sdk['started']+90)
            _require(len([r for r in _catalog(host_b_run).records if r.name==d['agent_name']])==1)
            return cls(host_b_run,path,raw,meta,binary_meta,d['buzz_cli_sha256'],phase_meta)
        except Exception: raise Msg003Error() from None

    def _data(self): return zero._json(self._raw)
    def revalidate(self):
        self.host_b_run.revalidate(phase=_phase(self.host_b_run))
        raw,meta=zero._read(self._path,mode=0o600)
        _require(raw==self._raw and meta==self._meta)
        d=self._data();raw,meta=zero._read(Path(d['buzz_cli']),source=True)
        _require(meta==self._binary_meta and _digest(raw)==self._binary_sha)
        _require(zero._directory(Path(d['phase_dir']), private=True)==self._phase_meta)
        if d['sdk'] is not None:
            sdk=d['sdk'];_require(_digest(_read(sdk['manifest_path']))==sdk['manifest_sha256'])
            checker=self.host_b_run._reviewed_source/'skills/agent-harness/buzz-agent-setup/tests/hostd_probes/check_feishu_events.py'
            _require(_digest(zero._read(checker,source=True)[0])==sdk['checker_sha256'])

@dataclass(frozen=True)
class Msg003RunPlan:
    host_b_run: zero.ZeroLocalRunPlan = field(repr=False)
    source_run: Msg003SourceRun = field(repr=False)
    scenario_plan: scenario.ScenarioPlan = field(repr=False)
    source_agent_name: str
    _record_digest: str
    _scenario_doc: str = field(repr=False)
    _image_mime: str
    _image_size: int
    _link_base: str

    @classmethod
    def check(cls, host_b_run, source_run, scenario_plan, *, source_agent_name):
        try:
            _require(type(host_b_run) is zero.ZeroLocalRunPlan and type(source_run) is Msg003SourceRun)
            _require(source_run.host_b_run is host_b_run and type(scenario_plan) is scenario.ScenarioPlan)
            source_run.revalidate();scenario_plan.revalidate()
            sd=source_run._data();doc=json.loads(scenario_plan._doc);b=host_b_run._data()
            _require(source_agent_name==sd['agent_name'] and scenario_plan.run_id==b['run_id'])
            _require(scenario_plan.prepared.run_id==b['run_id'] and scenario_plan.prepared.revision==b['revision'])
            _require(doc['run_id']==b['run_id'] and doc['channel_id']==b['channel_id'])
            _require(doc['phase_dir']==sd['phase_dir'] and scenario_plan._pins[0][1]==sd['scenario_manifest_sha256'])
            _require(Path(scenario_plan.prepared.run_dir)!=Path(b['run_dir']))
            records=[r for r in _catalog(host_b_run).records if r.name==source_agent_name]
            _require(len(records)==1);r=records[0]
            _require(r.status=='own_bot_verified' and r.local_bot_verified and r.owner_pubkey==host_b_run._owner and b['channel_id'] in r.channels)
            raw=_read(doc['image_file']);_require(_digest(raw)==doc['image_sha256'])
            unused,kind=gs.read_image(Path(doc['image_file']),allowed=frozenset({'png','jpeg'}))
            _require(0<len(raw)<=10_000_000)
            runtime=zero._json(_read(b['onboarding_config']));link=runtime.get('remote_link_base')
            _require(isinstance(link,str) and canonical_origin(link)==link and link.startswith('https://'))
            # All selected identity material is reread from the original catalog.
            return cls(host_b_run,source_run,scenario_plan,source_agent_name,_digest(r.__dict__ | {'env_file':str(r.env_file),'lark_config_dir':str(r.lark_config_dir),'lark_data_dir':str(r.lark_data_dir),'prompt_file':str(r.prompt_file),'responsible_config':str(r.responsible_config),'log_file':str(r.log_file)}),scenario_plan._doc,'image/'+kind,len(raw),link)
        except Exception: raise Msg003Error() from None

    def revalidate(self):
        current=type(self).check(self.host_b_run,self.source_run,self.scenario_plan,source_agent_name=self.source_agent_name)
        _require(current==self)

@dataclass(frozen=True)
class Msg003Receipt:
    _value: str = field(repr=False)
    def readback(self): return json.loads(self._value)

class Msg003Driver:
    def __init__(self,plan,*,hostd,runner=_run_source_cli,clock=time.time):
        _require(type(plan) is Msg003RunPlan and type(hostd) is zero.OwnedZeroLocalRun and hostd._plan is plan.host_b_run)
        _require(callable(runner) and callable(clock))
        self.plan,self.hostd,self.runner,self.clock=plan,hostd,runner,clock
        self._lock=asyncio.Lock();self._event=None;self._grant=None;self._message=None
        self._pending()

    def _pending(self):
        # A new verification owns a fresh verdict. Previously observed IDs and
        # digests must never survive a failed current ownership/native check.
        self._last={'version':1,'schema':'hostd-msg003-v1','run_id':self.plan.host_b_run._data()['run_id'],'status':'pending','source_readback':False,'native_readback':False,'sdk_callback':False,'live_verified':False}
        if self.plan.source_run._data()['version']==2:
            self._last.update(version=2,schema='hostd-msg003-v2',human_return=False)

    async def _io(self,fn,*args):
        task=asyncio.create_task(asyncio.to_thread(fn,*args))
        try:return await asyncio.shield(task)
        except asyncio.CancelledError as cancelled:
            while not task.done():
                try:await asyncio.shield(task)
                except asyncio.CancelledError:continue
                except Exception:break
            raise cancelled

    def _record(self):
        return next(r for r in _catalog(self.plan.host_b_run).records if r.name==self.plan.source_agent_name)
    def _reader(self):
        b=self.plan.host_b_run._data()
        return OwnAgentReader(self._record(),origin=b['relay_url'],relay_pubkey=b['relay_pubkey'],trusted_relays=b['trusted_relays'],clock=lambda:int(self.clock()))
    def _bot(self,target):
        r=self._record()
        return BotLarkCli(r.app_id,r.lark_config_dir,r.lark_data_dir,base_env=self.plan.host_b_run.environment(),http_pool=HttpPool(timeout=5),chat_id=target['chat_id'])
    def _owners(self):
        self.plan.revalidate();rb=self.hostd.readback()
        _require(rb['status']=='running' and rb['runtime_started'] and rb['target_channel_bindings']==0)

    def _root_pin(self):
        return self.plan.source_run._data().get('root')

    def _a_config(self):
        p=self.plan.scenario_plan.prepared
        rt=zero._json(_read(p.onboarding_config))
        return zero._json(_read(Path(rt['binding_dir'])/p.initial_binding/'config.json'))

    def _a_bot(self):
        # Original A read-only identity lane; never supplied to B dispatch.
        cfg=self._a_config();block=cfg['agents'][cfg['desk_pubkey']]
        return BotLarkCli(block['app_id'],block['lark_config_dir'],block['lark_data_dir'],
            base_env=self.plan.scenario_plan.prepared.environment(),http_pool=HttpPool(timeout=5),chat_id=cfg['chat_id'])

    async def _human_identity(self,bot,native,human):
        cfg=self._a_config();doc=json.loads(self.plan._scenario_doc)
        _require(bot.app_id==cfg['agents'][cfg['desk_pubkey']]['app_id'])
        _require((await self._io(bot.identity))==(bot.app_id,''))
        roles=await self._reader().members(doc['channel_id'])
        _require(roles.get(human) in gs.HUMAN_ROLES)
        people=await self._io(gs.fetch_people,cfg,roles,gs._http_get,datetime.fromtimestamp(self.clock(),timezone.utc))
        _require(people.union_ids is not None)
        paired,duplicates=gs.one_pubkey_per_id(people.union_ids)
        projected=await self._io(bot.message_view,native['message_id'],'union_id')
        _require(isinstance(projected,dict) and projected.get('chat_id')==doc['chat_id'] and not projected.get('deleted'))
        for key in ('message_id','root_id','parent_id','msg_type','body','create_time'):
            _require(projected.get(key)==native.get(key))
        sender=projected.get('sender') or {}
        _require(sender.get('sender_type')=='user' and sender.get('id_type')=='union_id'
            and paired.get(sender.get('id'))==human)
        profiles=await self._reader().query([{'kinds':[0],'authors':[human],'limit':257}])
        body=scenario.projected_human_body(native,projected,human_pubkey=human,roles=roles,
            profiles=profiles,union_ids=people.union_ids,now=self.clock())
        _require((await self._io(bot.identity))==(bot.app_id,''))
        return body

    async def _root_readback(self):
        """Pins select reads; original signature/native/intent establish truth."""
        pin=self._root_pin();_require(pin is not None);self._owners()
        doc=json.loads(self.plan._scenario_doc);cfg=self._a_config()
        expected={'version':1,'status':'unknown','phase':'msg001','run_id':doc['run_id'],
            'revision':doc['revision'],'binding':doc['initial_binding'],'chat':doc['chat_id'],
            'channel':doc['channel_id'],'bodyhash':_digest(_read(doc['msg001_content_file'])),
            'idempotency':'msg001-'+doc['run_id'],'root':None}
        intent=zero._json(_read(Path(doc['phase_dir'])/'msg001.json'))
        _require(set(intent)==set(expected)|{'started'} and all(intent[k]==v for k,v in expected.items()))
        _require(type(intent['started']) in (int,float) and math.isfinite(intent['started']) and 0<=intent['started']<=self.clock())
        events=await self._reader().query([{'kinds':[9],'ids':[pin['event_id']],'#h':[doc['channel_id']],'limit':2}])
        _require(len(events)==1);event=events[0]
        _require(event.get('id')==pin['event_id'] and _digest(event)==pin['event_sha256'])
        mapping=delivery_mapping.buzz_mapping(event,doc['channel_id'],set(),{cfg['mirror_pubkey']})
        _require(mapping is not None and mapping.message_id==pin['native_message_id']
            and mapping.feishu_root==mapping.message_id and mapping.buzz_root is None)
        _require(not gs._buzz_parent(event) and intent['started']<=event['created_at']<=self.clock())
        _require(len([t for t in event['tags'] if t[:2]==['p',doc['agent_pubkey']]])==1)
        metas=[t for t in event['tags'] if t[:1]==['imeta']];_require(len(metas)==1)
        fields={}
        for entry in metas[0][1:]:
            key,sep,value=entry.partition(' ');_require(sep and key not in fields);fields[key]=value
        suffix=Path(doc['image_file']).suffix
        _require(fields.get('url')==doc['link_base']+'/media/'+doc['image_sha256']+suffix
            and fields.get('x')==doc['image_sha256'] and fields.get('m')==self.plan._image_mime)
        bot=self._a_bot()
        try:
            native=await self._io(bot.message_view,pin['native_message_id'],'open_id')
            _require(isinstance(native,dict) and native.get('chat_id')==doc['chat_id'] and not native.get('deleted')
                and native.get('msg_type')=='post' and (native.get('root_id') or native['message_id'])==native['message_id'])
            _require((native.get('sender') or {}).get('sender_type')=='user')
            _require(scenario._canonical(native['body']['content'])==scenario._canonical(_read(doc['msg001_content_file'])))
            _require(intent['started']*1000<=int(native['create_time'])<=self.clock()*1000)
            _require(event['content']==await self._human_identity(bot,native,doc['human_pubkey']))
        finally:bot.http_pool.close()
        self._owners();return event,mapping

    def _snapshot(self,event_id=None):
        """SQLite read-only transaction; never open production Store for writes."""
        b=self.plan.host_b_run._data();path=Path(b['state_db']);zero._read(path,mode=0o600)
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=1)) as db:
            db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
            _require(not db.execute('SELECT 1 FROM binding WHERE channel_id=?',(b['channel_id'],)).fetchall())
            rows=db.execute('SELECT * FROM remote_target WHERE agent_id=? AND channel_id=?',(self._record().pubkey,b['channel_id'])).fetchall()
            if len(rows)!=1:return None
            t=dict(rows[0]);g=db.execute('SELECT * FROM remote_grant WHERE target_id=? AND revision=?',(t['target_id'],t['current_revision'])).fetchall()
            _require(len(g)==1);g=dict(g[0])
            _require(t['status']=='active' and t['owner_pubkey']==self.plan.host_b_run._owner and t['app_id']==self._record().app_id)
            _require(t['channel_id']==b['channel_id'] and t['chat_id']==json.loads(self.plan._scenario_doc)['chat_id'])
            _require(t['target_id']==_digest([self._record().pubkey,b['channel_id']]) and t['chat_ref']==gs.chat_ref(t['chat_id']) and t['relay_origin']==canonical_origin(b['relay_url']))
            now=int(self.clock());_require(t['proof_checked_at']<=now<t['proof_valid_until'] and g['checked_at']<g['valid_until'])
            _require(g['capabilities']&1 and g['approval_id'] and g['approval_hash'])
            immutable={k:t[k] for k in ('target_id','agent_id','owner_pubkey','app_id','channel_id','chat_id','chat_ref','relay_origin','current_revision')}
            pin=_digest({'target':immutable,'grant':g})
            if self._grant is not None:_require(pin==self._grant)
            phase=Path(self.plan.source_run._data()['phase_dir']);pin_path=phase/'msg003-grant-pin.json'
            try:_write_new(pin_path,{'version':1,'grant_sha256':pin})
            except FileExistsError:_require(zero._json(_read(pin_path))=={'version':1,'grant_sha256':pin})
            self._grant=pin
            delivery=db.execute('SELECT * FROM remote_delivery WHERE target_id=? AND source_id=? AND action=?',(t['target_id'],event_id,'message')).fetchall() if event_id else []
            images=db.execute('SELECT * FROM remote_image_upload WHERE target_id=? AND source_id=? ORDER BY ordinal',(t['target_id'],event_id)).fetchall() if event_id else []
            return t,g,[dict(r) for r in delivery],[dict(r) for r in images],pin

    def _intent(self):
        d=self.plan.source_run._data();path=Path(d['phase_dir'])/'msg003-intent.json'
        expected={'version':1,'state':'unknown','run_id':d['run_id'],'source_manifest_sha256':_digest(self.plan.source_run._raw),'image_sha256':json.loads(self.plan._scenario_doc)['image_sha256'],'source_agent_sha256':_digest(self._record().pubkey.encode())}
        if d['version']==2:expected.update(version=2,root=d['root'])
        try:
            _write_new(path,{**expected,'started':int(self.clock())})
            return zero._json(_read(path)),True
        except FileExistsError:
            value=zero._json(_read(path));_require(set(value)==set(expected)|{'started'} and all(value[k]==v for k,v in expected.items()))
            _require(type(value['started']) is int and 0<=value['started']<=int(self.clock()))
            return value,False

    def _publish(self,marker):
        self._owners();d=self.plan.source_run._data();r=self._record()
        env=self.plan.host_b_run.environment();env.update(prepare.join.parse_env(_read(r.env_file).decode()))
        doc=json.loads(self.plan._scenario_doc)
        argv=[d['buzz_cli'],'messages','send','--channel',doc['channel_id'],'--content','-','--file',doc['image_file']]
        if d['version']==2:argv.extend(['--reply-to',d['root']['event_id']])
        return self.runner(argv,input=marker,capture_output=True,text=True,check=False,timeout=30,env=env)

    def _source_valid(self,event,intent):
        doc=json.loads(self.plan._scenario_doc);b=self.plan.host_b_run._data();r=self._record()
        _require(isinstance(event,dict) and event.get('pubkey')==r.pubkey and event.get('kind')==9 and delivery_mapping._verified(event,doc['channel_id']))
        root=self._root_pin()
        if root is None:_require(not gs.buzz_thread_root(event) and not gs._buzz_parent(event))
        else:
            _require(gs.buzz_thread_root(event)==root['event_id'] and gs._buzz_parent(event)==root['event_id'])
            _require([t for t in event['tags'] if t[:1]==['e']]==[
                ['e',root['event_id'],'','root'],['e',root['event_id'],'','reply']])
        _require(event['content']=='MSG003-'+b['run_id'] and intent['started']<=event['created_at']<=int(self.clock()))
        tags=[t for t in event['tags'] if t[:1]==['imeta']];_require(len(tags)==1 and len(tags[0])==5)
        fields={}
        for entry in tags[0][1:]:
            key,sep,value=entry.partition(' ');_require(sep and key not in fields);fields[key]=value
        suffix='.png' if self.plan._image_mime=='image/png' else '.jpg'
        _require(fields=={'url':canonical_origin(b['relay_url'])+'/media/'+doc['image_sha256']+suffix,'x':doc['image_sha256'],'m':self.plan._image_mime,'size':str(self.plan._image_size)})
        if self._event is not None:_require(event==self._event)

    async def _query(self,intent,event_id=None):
        b=self.plan.host_b_run._data();filters={'kinds':[9],'authors':[self._record().pubkey],'#h':[b['channel_id']],'since':intent['started'],'until':int(self.clock()),'limit':257}
        if event_id:filters['ids']=[event_id]
        rows=await self._reader().query([filters]);found=[]
        for e in rows:
            try:self._source_valid(e,intent);found.append(e)
            except Msg003Error:pass
        _require(len(found)==1);self._event=found[0];self._last['source_readback']=True
        self._last['source_event_sha256']=_digest(self._event['id'].encode())

    async def publish_once(self):
        async with self._lock:
            self._pending()
            try:
                self._owners();self._snapshot()
                if self._root_pin() is not None:await self._root_readback()
                intent,fresh=self._intent()
                # Thread's bounded subprocess is joined before releasing the lock.
                if fresh:
                    try:await self._io(self._publish,'MSG003-'+intent['run_id'])
                    except asyncio.CancelledError:raise
                    except Exception:pass
                    await self._query(intent)
                elif self._event is not None:await self._query(intent,self._event['id'])
                else:await self._query(intent)  # Original UNKNOWN recovers by query only.
                self._owners()
            except asyncio.CancelledError:raise
            except Exception:self._pending()
            return self._receipt()

    async def _native(self,event,snapshot):
        t,g,delivery,images,pin=snapshot;doc=json.loads(self.plan._scenario_doc)
        _require(len(delivery)==1 and len(images)==1);row=delivery[0];image=images[0]
        root=self._root_pin()
        if root is not None:await self._root_readback()
        _require((row['status'],row['revision'],row['scope_hash'],row['source_at'],row['root_id'],row['sender_app_id'])==('acked',g['revision'],g['scope_hash'],event['created_at'],root['event_id'] if root else '',t['app_id']))
        _require((image['ordinal'],image['state'],image['revision'],image['scope_hash'],image['source_at'],image['content_hash'],image['mime_type'],image['byte_size'])==(0,'acked',g['revision'],g['scope_hash'],event['created_at'],doc['image_sha256'],self.plan._image_mime,self.plan._image_size))
        _require(BotLarkCli._image_key(image['image_key']))
        bot=self._bot(t)
        try:
            _require((await self._io(bot.identity))[0]==t['app_id'])
            native=await self._io(bot.message_view,row['message_id'],'open_id')
            _require(native.get('message_id')==row['message_id'] and native.get('chat_id')==t['chat_id'] and not native.get('deleted') and native.get('msg_type')=='interactive')
            _require(native.get('sender')=={'sender_type':'app','id_type':'app_id','id':t['app_id']} and (native.get('root_id') or row['message_id'])==(root['native_message_id'] if root else row['message_id']))
            card=json.loads(delivery_mapping.message_card(event,self._record().name,event['content'],self.plan._link_base,t['channel_id']))
            card['elements'][-1:-1]=[{'tag':'img','img_key':image['image_key'],'alt':{'tag':'plain_text','content':''}}]
            _require(row['content_hash']==_digest(json.dumps(card,ensure_ascii=False,separators=(',',':')).encode()))
            _require(json.loads(native['body']['content'])==card)
            blob=await self._io(bot.own_image,image['image_key'])
            _require(type(blob) is OwnImage and (blob.image_key,blob.mime_type,blob.byte_size,blob.content_hash)==(image['image_key'],self.plan._image_mime,self.plan._image_size,doc['image_sha256']))
            _require(len(blob.body)==self.plan._image_size and _digest(blob.body)==doc['image_sha256'] and blob.body==_read(doc['image_file']))
        finally:bot.http_pool.close()
        self._owners();current=self._snapshot(event['id']);_require(current==snapshot)
        self._message=row['message_id']
        self._last.update(status='observed',native_readback=True,native_message_sha256=_digest(row['message_id'].encode()),target_sha256=_digest(t['target_id'].encode()),channel_sha256=_digest(t['channel_id'].encode()),source_agent_sha256=_digest(self._record().pubkey.encode()),grant_revision=g['revision'],scope_sha256=_digest(g['scope_hash'].encode()),capabilities_sha256=_digest(g['capabilities']),image_count=1,images=[{'sha256':doc['image_sha256'],'mime':self.plan._image_mime,'size':self.plan._image_size}],native_readback_sha256=_digest({'message':row['message_id'],'source':event['id'],'image':doc['image_sha256'],'grant':pin}))

    async def observe(self,source_event_id=None):
        async with self._lock:
            self._pending()
            try:
                self._owners();_require((Path(self.plan.source_run._data()['phase_dir'])/'msg003-intent.json').exists())
                intent,fresh=self._intent();_require(not fresh)
                if source_event_id is not None:_require(gs.HEX64_RE.fullmatch(source_event_id) is not None and (self._event is None or self._event['id']==source_event_id))
                _require(self._event is not None or source_event_id is not None)
                await self._query(intent,source_event_id or self._event['id'])
                snapshot=self._snapshot(self._event['id']);_require(snapshot is not None)
                await self._native(self._event,snapshot)
            except asyncio.CancelledError:raise
            except Exception:self._last.update(status='pending',native_readback=False)
            return self._receipt()

    def _human_valid(self,event,native,expected_body):
        doc=json.loads(self.plan._scenario_doc);cfg=self._a_config();root=self._root_pin()
        stamp=int(native['create_time']);native_message_id=native['message_id']
        _require(event['pubkey']==cfg['mirror_pubkey'] and delivery_mapping._verified(event,doc['channel_id'])
            and event['content']==expected_body and stamp//1000<=event['created_at']<=self.clock())
        _require([t for t in event['tags'] if t[:1]==[gs.FEISHU_AUTHOR_TAG]]==[[gs.FEISHU_AUTHOR_TAG,doc['human_pubkey']]])
        _require(gs.buzz_thread_root(event)==root['event_id'] and gs._buzz_parent(event)==root['event_id'])
        _require([t for t in event['tags'] if t[:1]==['e']]==[
            ['e',root['event_id'],'','reply'],['e',root['event_id'],'','root']])
        mapping=delivery_mapping.buzz_mapping(event,doc['channel_id'],set(),{cfg['mirror_pubkey']})
        _require(mapping is not None and mapping.message_id==native_message_id
            and mapping.feishu_root==root['native_message_id'] and mapping.buzz_root==root['event_id'])

    async def observe_human_return(self,native_message_id):
        """Original native user reply + ordinary A mirror; no human mutation.

        A thread root alone cannot identify which card received a reply. Missing
        native parent_id stays pending, even if a signed mirror event exists.
        """
        async with self._lock:
            self._pending()
            try:
                root=self._root_pin();_require(root is not None)
                _require(isinstance(native_message_id,str) and gs.MESSAGE_ID_RE.fullmatch(native_message_id))
                self._owners();await self._root_readback()
                _require((Path(self.plan.source_run._data()['phase_dir'])/'msg003-intent.json').exists())
                intent,fresh=self._intent();_require(not fresh)
                await self._query(intent,self._event['id'] if self._event else None)
                _require(self._event is not None)
                snapshot=self._snapshot(self._event['id']);_require(snapshot is not None)
                await self._native(self._event,snapshot)
                doc=json.loads(self.plan._scenario_doc);cfg=self._a_config();bot=self._a_bot()
                try:
                    native=await self._io(bot.message_view,native_message_id,'open_id')
                    _require(isinstance(native,dict) and native.get('message_id')==native_message_id
                        and native.get('chat_id')==doc['chat_id'] and not native.get('deleted')
                        and native.get('root_id')==root['native_message_id']
                        and native.get('parent_id')==self._message and native.get('msg_type')=='text')
                    _require((native.get('sender') or {}).get('sender_type')=='user')
                    body=json.loads(native['body']['content']);_require(set(body)=={'text'} and isinstance(body['text'],str) and body['text'].strip())
                    stamp=int(native['create_time']);_require(self._event['created_at']*1000<=stamp<=self.clock()*1000)
                    expected_body=await self._human_identity(bot,native,doc['human_pubkey'])
                    rows=await self._reader().query([{'kinds':[9],'#h':[doc['channel_id']],
                        '#feishu':[native_message_id],'limit':2}])
                    _require(len(rows)==1);event=rows[0]
                    self._human_valid(event,native,expected_body)
                    # Read the native parent independently: no guessed card ID
                    # or thread root can confer a parent relation.
                    parent=await self._io(bot.message_view,self._message,'open_id')
                    _require(isinstance(parent,dict) and parent.get('message_id')==self._message
                        and parent.get('chat_id')==doc['chat_id'] and not parent.get('deleted')
                        and parent.get('root_id')==root['native_message_id']
                        and parent.get('sender')=={'sender_type':'app','id_type':'app_id','id':snapshot[0]['app_id']})
                    _require(await self._io(bot.message_view,native_message_id,'open_id')==native)
                finally:bot.http_pool.close()
                self._owners();await self._root_readback()
                _require(self._snapshot(self._event['id'])==snapshot)
                self._last.update(human_return=True,human_native_sha256=_digest(native_message_id.encode()),
                    human_event_sha256=_digest(event['id'].encode()),human_readback_sha256=_digest({'native':native,'event':event}))
            except asyncio.CancelledError:raise
            except Exception:self._pending()
            return self._receipt()

    async def join_sdk_evidence(self,callback_receipt):
        """Join (JSONL path, artifact SHA, source SHA, native SHA, SDK event SHA).

        The caller pins the original recorder artifact and expected SDK event,
        while this method rechecks source, Store and physical native evidence.
        A failed attempt returns only a fresh pending verdict.
        """
        async with self._lock:
            previous=dict(self._last);self._pending()
            try:
                self._owners();declared=self.plan.source_run._data()['sdk'];_require(declared is not None and previous['native_readback'] and self._event is not None)
                # Input is the recorder's sanitized JSONL artifact plus exact
                # source/native receipt hashes, never a callback verdict DTO.
                _require(isinstance(callback_receipt,tuple) and len(callback_receipt)==5)
                path,expected,source_sha,native_sha,event_sha=callback_receipt
                _require(source_sha==previous['source_event_sha256'] and native_sha==previous['native_readback_sha256'])
                path=zero._absolute(str(path));_require(path.parent==Path(self.plan.source_run._data()['phase_dir']))
                raw=_read(path);_require(_digest(raw)==expected)
                checker_path=self.plan.host_b_run._reviewed_source/'skills/agent-harness/buzz-agent-setup/tests/hostd_probes/check_feishu_events.py'
                spec=importlib.util.spec_from_file_location('msg003_event_checker',checker_path)
                checker=importlib.util.module_from_spec(spec);spec.loader.exec_module(checker)
                manifest=zero._json(_read(declared['manifest_path']))
                run,chat,apps=checker.validate_recorder_manifest(manifest)
                _require(run==self._last['run_id'] and manifest['window']=={'start':declared['started'],'end':declared['deadline']})
                snapshot=self._snapshot(self._event['id']);_require(snapshot is not None);t=snapshot[0]
                _require(chat==t['chat_id'] and t['app_id'] in apps.values())
                rows=[zero._json(line) for line in raw.splitlines() if line]
                _require(0<len(rows)<=1000)
                allowed={'schema_version','run_id','t_recv','bot','app_id','session_id','connection_id','type','event_id','chat_id','message_id','create_time','mentions'}
                for row in rows:
                    _require(isinstance(row,dict) and set(row)<=allowed);checker.validate_row(row)
                scoped=[r for r in rows if checker._in_scope(r,run,declared['started'],declared['deadline']) and r.get('app_id')==t['app_id'] and apps.get(r.get('bot'))==t['app_id'] and _digest(str(r.get('session_id','')).encode())==declared['session_sha256']]
                starts=[r for r in scoped if r.get('type')=='_connection_started'];_require(len(starts)==1)
                end=min([r['t_recv'] for r in scoped if r.get('type')=='_connection_closed']+[declared['deadline']])
                matched=[r for r in scoped if r.get('type')==declared['event_type'] and r.get('chat_id')==t['chat_id'] and r.get('message_id')==self._message and starts[0]['t_recv']<=r['t_recv']<end and isinstance(r.get('event_id'),str) and r['event_id']]
                _require(len(matched)==1 and zero.HEX64.fullmatch(event_sha) is not None and _digest(matched[0]['event_id'].encode())==event_sha)
                # Refresh actual source + Store/message/image GET evidence;
                # callback arrival cannot preserve an obsolete native verdict.
                intent,fresh=self._intent();_require(not fresh)
                self._last.update(status='pending',source_readback=False,native_readback=False)
                await self._query(intent,self._event['id']);await self._native(self._event,snapshot)
                self._owners();self._last.update(sdk_callback=True,callback_event_sha256=_digest(matched[0]['event_id'].encode()),callback_session_sha256=declared['session_sha256'],callback_artifact_sha256=expected)
            except asyncio.CancelledError:raise
            except Exception:self._pending()
            return self._receipt()

    def _receipt(self):
        # Versioned immutable owner-only artifacts keep every pending/failure.
        raw=json.dumps(self._last,sort_keys=True,separators=(',',':'));digest=_digest(raw.encode())
        phase=Path(self.plan.source_run._data()['phase_dir'])
        path=phase/('msg003-receipt-'+digest+'.json')
        try:_write_new(path,self._last)
        except FileExistsError:_require(_read(path)==raw.encode())
        manifest=phase/('msg003-receipt-manifest-'+digest+'.json')
        binding={'version':1,'run_id':self._last['run_id'],'source_manifest_sha256':_digest(self.plan.source_run._raw),'receipt_sha256':digest,'receipt_file':path.name}
        try:_write_new(manifest,binding)
        except FileExistsError:_require(zero._json(_read(manifest))==binding)
        return Msg003Receipt(raw)
    def readback(self): return json.loads(json.dumps(self._last))
