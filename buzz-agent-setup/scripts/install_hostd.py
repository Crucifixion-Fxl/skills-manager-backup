#!/usr/bin/env python3
"""Prepare fixed-commit hostd candidates. No service-manager writes or activation."""
from __future__ import annotations
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import signal
import sqlite3
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
from urllib.parse import urlsplit
sys.path.insert(0, str(Path(__file__).resolve().parent))
from hostd.cli_runtime import RuntimePaths, RuntimePathError

BASE = 'skills/agent-harness/buzz-agent-setup/scripts'
REFERENCE_SCRIPTS = 'skills/agent-harness/buzz-agent-setup/references/scripts'
LOCK = BASE + '/hostd/requirements.lock'
PROPERTIES = 'Id,LoadState,ActiveState,SubState,UnitFileState,MainPID,ControlPID,TasksCurrent,ControlGroup,FragmentPath,DropInPaths,NeedDaemonReload'

class InstallError(ValueError):
    def __init__(self, code='invalid'):
        self.code = code
        super().__init__(code)
    def public(self):
        return dict(status='failed', error=self.code, installed=False,
                    remediation='怎么解决：保留安装记录，核对固定提交、15 个目标、文件权限和服务状态后重试。复制给 AI：检查 hostd 安装记录并报告未通过的验证；不要重启忙碌 Agent。')

class Runner:
    """No shell, credential-free environment, process-group deadline and output cap."""
    def __call__(self, args, *, timeout=30, limit=64*1024*1024, cwd=None):
        env = dict(PATH='/usr/bin:/bin', HOME=str(Path.home()), LANG='C.UTF-8',
                   XDG_RUNTIME_DIR=f'/run/user/{os.getuid()}', PYTHONDONTWRITEBYTECODE='1',
                   PYTHONNOUSERSITE='1', PIP_CONFIG_FILE='/dev/null', GIT_CONFIG_NOSYSTEM='1',
                   GIT_CONFIG_GLOBAL='/dev/null', GIT_TERMINAL_PROMPT='0',
                   UV_NO_CONFIG='1', UV_NO_CACHE='1', UV_PYTHON_DOWNLOADS='never')
        if not isinstance(args,list) or not all(isinstance(a,str) and '\0' not in a for a in args):
            raise InstallError()
        try:
            with tempfile.TemporaryFile() as out:
                p=subprocess.Popen(args,stdout=out,stderr=subprocess.DEVNULL,env=env,cwd=cwd,start_new_session=True)
                deadline=time.monotonic()+timeout
                try:
                    while p.poll() is None:
                        if time.monotonic()>=deadline or os.fstat(out.fileno()).st_size>limit:
                            raise InstallError('command_bound')
                        time.sleep(.02)
                    if p.returncode or os.fstat(out.fileno()).st_size>limit:
                        raise InstallError('command_failed')
                    out.seek(0)
                    return out.read(limit+1)
                finally:
                    if p.poll() is None:
                        os.killpg(p.pid,signal.SIGKILL);p.wait()
        except (OSError,subprocess.SubprocessError):
            raise InstallError('command_failed') from None

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def path(value):
    p=Path(value)
    if not p.is_absolute() or '..' in p.parts or any(c in str(p) for c in '\n\r\0%$"\\'):
        raise InstallError('unsafe_path')
    if any(part in {'.worktree','.worktrees'} for part in p.parts):
        raise InstallError('worktree_destination')
    # Inspect every existing ancestor, never resolve through links.
    fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
    try:
        for index,part in enumerate(p.parts[1:]):
            try:
                meta=os.stat(part,dir_fd=fd,follow_symlinks=False)
            except FileNotFoundError:break
            if stat.S_ISLNK(meta.st_mode):raise InstallError('unsafe_path')
            if index < len(p.parts)-2:
                child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
                os.close(fd);fd=child
    except OSError:raise InstallError('unsafe_path') from None
    finally:os.close(fd)
    return p

def directory(value):
    p=path(value);fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
    try:
        for part in p.parts[1:]:
            try:child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            except FileNotFoundError:
                os.mkdir(part,0o700,dir_fd=fd)
                child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            os.close(fd);fd=child
        meta=os.fstat(fd)
        if meta.st_uid!=os.getuid() or stat.S_IMODE(meta.st_mode)!=0o700:raise InstallError('unsafe_path')
        return fd
    except Exception:
        os.close(fd);raise

def read(value,limit=32*1024*1024,private=False,missing=False):
    p=path(value)
    # Pin every ancestor across opening the file.
    fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
    try:
        for part in p.parent.parts[1:]:
            child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd);os.close(fd);fd=child
        file=os.open(p.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
        try:
            meta=os.fstat(file)
            if not stat.S_ISREG(meta.st_mode) or meta.st_nlink!=1 or meta.st_uid!=os.getuid() or meta.st_size>limit or private and stat.S_IMODE(meta.st_mode)!=0o600:raise InstallError('unsafe_file')
            data=b''
            while len(data)<=limit:
                block=os.read(file,min(65536,limit+1-len(data)))
                if not block:break
                data+=block
            after=os.fstat(file)
            if len(data)>limit or (meta.st_size,meta.st_mtime_ns,meta.st_ctime_ns)!=(after.st_size,after.st_mtime_ns,after.st_ctime_ns):raise InstallError('file_changed')
            return data
        finally:os.close(file)
    except FileNotFoundError:
        if missing:return None
        raise InstallError('unsafe_file') from None
    except OSError:raise InstallError('unsafe_file') from None
    finally:os.close(fd)

def write(parent,name,data):
    fd=directory(parent)
    try:
        file=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
        try:
            with os.fdopen(file,'wb') as out:out.write(data);out.flush();os.fsync(out.fileno())
            os.fsync(fd)
        except Exception:raise InstallError('write_failed') from None
    finally:os.close(fd)
    if read(Path(parent)/name,private=True)!=data:raise InstallError('readback_failed')

def legacy_hash(value):
    data=read(value,private=True,missing=True)
    return hashlib.sha256(data).hexdigest() if data is not None else None

def jsonwrite(parent,name,value):write(parent,name,json.dumps(value,indent=2,sort_keys=True).encode())

def states(targets,runner):
    rows={}
    for t in targets:
        for unit in (t['service'],t['timer']):
            properties=PROPERTIES if unit.endswith('.service') else ','.join(k for k in PROPERTIES.split(',') if k not in {'MainPID','ControlPID','TasksCurrent','ControlGroup'})
            raw=runner(['/usr/bin/systemctl','--user','show',unit,'--property='+properties],timeout=10,limit=32768).decode()
            fields={}
            for line in raw.splitlines():
                k,sep,v=line.partition('=')
                if not sep or k in fields:raise InstallError('manager_unverified')
                fields[k]=v
            if set(fields)!=set(properties.split(',')) or fields['Id']!=unit or fields['LoadState']!='loaded' or fields['DropInPaths'] or fields['NeedDaemonReload']!='no':raise InstallError('manager_unverified')
            if fields['ActiveState'] not in {'active','inactive','activating','deactivating','failed','reloading'} or fields['UnitFileState'] not in {'enabled','disabled','static','indirect','enabled-runtime'}:raise InstallError('manager_unverified')
            if unit.endswith('.service') and fields['TasksCurrent']=='[not set]' and not fields['ControlGroup'] and fields['ActiveState']=='inactive' and fields['MainPID']==fields['ControlPID']=='0':
                fields['TasksCurrent']='0'
            for k in (['MainPID','ControlPID','TasksCurrent'] if unit.endswith('.service') else []):
                if not re.fullmatch(r'[0-9]{1,12}',fields[k]):raise InstallError('manager_unverified')
            fragment=fields['FragmentPath']
            fields['fragment_sha256']=hashlib.sha256(read(fragment)).hexdigest() if fragment else None
            rows[unit]=fields
    return rows

def validate_targets(inventory):
    if not isinstance(inventory,list) or len(inventory)!=15:raise InstallError('target_count')
    seen=set();units=set();identities=set()
    targets=[]
    keys={'name','channel_id','chat_id_hash','app_id','service','timer','legacy_state'}
    for t in inventory:
        if not isinstance(t,dict) or set(t)!=keys:raise InstallError('target_invalid')
        if not all(isinstance(v,str) for v in t.values()):raise InstallError('target_invalid')
        if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,79}',t['name']) or t['name'] in seen:raise InstallError('target_invalid')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',t['channel_id']) or not re.fullmatch(r'[0-9a-f]{64}',t['chat_id_hash']) or not re.fullmatch(r'cli_[A-Za-z0-9_]{1,80}',t['app_id']):raise InstallError('target_invalid')
        identity=(t['channel_id'],t['chat_id_hash'])
        if identity in identities:raise InstallError('target_invalid')
        for kind in ['service','timer']:
            if not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}\.'+kind,t[kind]) or t[kind] in units:raise InstallError('target_invalid')
            units.add(t[kind])
        path(t['legacy_state']);read(t['legacy_state'],private=True,missing=True)
        seen.add(t['name']);identities.add(identity);targets.append(dict(t))
    return targets

def archive(repo,revision,runner):
    # Runtime imports (for example gitlab_buzz_sync -> nostrkit) use this
    # sibling tree. Both trees come from the same fixed commit, never WIP.
    return runner(['/usr/bin/git','-C',str(repo),'archive','--format=tar',revision,BASE,REFERENCE_SCRIPTS],timeout=30)

def archive_files(data):
    files={}
    try:
        with tarfile.open(fileobj=io.BytesIO(data)) as tar:
            members=tar.getmembers()
            if len(members)>20000:raise InstallError('archive_invalid')
            for member in members:
                p=Path(member.name)
                if p.is_absolute() or '..' in p.parts or member.name in files or not (member.isdir() or member.isfile()) or member.size>32*1024*1024:raise InstallError('archive_invalid')
                if member.isfile():files[member.name]=tar.extractfile(member).read()
    except (tarfile.TarError,OSError):raise InstallError('archive_invalid') from None
    if LOCK not in files or BASE+'/hostd/__main__.py' not in files:raise InstallError('release_missing_runtime')
    lock_versions(files[LOCK])
    return files

def lock_versions(data):
    try:entries=data.decode().replace('\\\n',' ').splitlines()
    except UnicodeError:raise InstallError('lock_invalid') from None
    versions={}
    for line in entries:
        if not line.strip() or line.startswith('#'):continue
        if not re.fullmatch(r'[A-Za-z0-9_.-]+==[0-9][A-Za-z0-9_.]*\s+(?:--hash=sha256:[0-9a-f]{64}\s*)+',line):raise InstallError('lock_invalid')
        name,version=line.split()[0].split('==')
        name=name.lower().replace('_','-')
        if name in versions:raise InstallError('lock_invalid')
        versions[name]=version
    for name,version in {'lark-oapi':'1.7.3','websockets':'15.0.1','cryptography':'46.0.2'}.items():
        if versions.get(name)!=version:raise InstallError('lock_invalid')
    return versions


def remote_link_origin(value):
    """Match RuntimeConfig's bounded HTTPS origin check without runtime imports."""
    if (not isinstance(value,str) or not value or len(value)>2048
            or '?' in value or '#' in value
            or any(ord(c)<=32 or ord(c)==127 for c in value)):
        raise ValueError
    try:
        parts=urlsplit(value);port=parts.port
    except ValueError:
        raise ValueError from None
    if (parts.scheme!='https' or not parts.hostname or parts.username is not None
            or parts.path or parts.query or parts.fragment
            or '%' in parts.netloc or '\\' in parts.netloc or parts.netloc.endswith(':')
            or (port is not None and not 1<=port<=65535)):
        raise ValueError
    return parts


def onboarding(config,pin,release):
    """Check explicit protected startup metadata; never read its credentials."""
    if config is None and pin is None:
        return dict(status='pending',source_path=None,candidate_path=None,sha256=None)
    if config is None or not isinstance(pin,str) or not re.fullmatch(r'[0-9a-f]{64}',pin):raise InstallError('startup_pin_required')
    config=path(config);raw=read(config,limit=128*1024,private=True)
    if hashlib.sha256(raw).hexdigest()!=pin:raise InstallError('startup_changed')
    try:
        def unique(pairs):
            result={}
            for key,value in pairs:
                if key in result:raise ValueError
                result[key]=value
            return result
        value=json.loads(raw,object_pairs_hook=unique)
        required={'version','owner_env_file','relay_url','relay_pubkey','template_config','binding_dir','legacy_join_path','catalog_path'}
        if not isinstance(value,dict) or set(value)-required-{'trusted_relays','remote_link_base'} or not required<=set(value):raise ValueError
        if type(value['version']) is not int or value['version']!=1:raise ValueError
        for key in ('owner_env_file','template_config','binding_dir','legacy_join_path','catalog_path'):
            if not isinstance(value[key],str) or not value[key]:raise ValueError
            path(value[key])
        if not isinstance(value['relay_pubkey'],str) or not re.fullmatch(r'[0-9a-f]{64}',value['relay_pubkey']):raise ValueError
        trusted=value.get('trusted_relays',[])
        if not isinstance(trusted,list):raise ValueError
        if value.get('remote_link_base','')!='':remote_link_origin(value['remote_link_base'])
        for uri in [value['relay_url'],*trusted]:
            if not isinstance(uri,str) or not uri.isascii() or any(c.isspace() or c in '?#\\' for c in uri):raise ValueError
            url=urlsplit(uri)
            if url.scheme not in ('http','https','ws','wss') or not url.hostname or url.username is not None or url.password is not None or url.path not in ('','/'):raise ValueError
            url.port
            # RuntimeConfig in the fixed release performs the authoritative relay
            # allowlist check during prepare. Check does no network or key reads.
    except (ValueError,TypeError,KeyError,OSError):raise InstallError('startup_invalid') from None
    return dict(status='pinned',source_path=str(config),candidate_path=str(release/'onboarding-config.json'),sha256=pin)


def public_plan(plan):
    """Expose review hashes and candidate paths, never protected source metadata."""
    result=dict(plan)
    if isinstance(result.get('onboarding'),dict):
        result['onboarding']={k:v for k,v in result['onboarding'].items() if k!='source_path'}
    return result


def verify_startup(release,plan,runner):
    startup=plan.get('onboarding') or {}
    if startup.get('status')!='pinned':return
    file=release/'onboarding-config.json'
    if hashlib.sha256(read(file,limit=128*1024,private=True)).hexdigest()!=startup['sha256']:raise InstallError('startup_changed')
    code='from hostd.onboarding_runtime import RuntimeConfig; import sys; RuntimeConfig.load(sys.argv[1]); print("validated")'
    result=runner([str(release/'venv/bin/python'),'-c',code,str(file)],cwd=str(release/'source'/BASE),timeout=30,limit=1024)
    if result.strip()!=b'validated':raise InstallError('startup_validation_failed')
    if hashlib.sha256(read(file,limit=128*1024,private=True)).hexdigest()!=startup['sha256']:raise InstallError('startup_changed')


def cli_runtime(node_binary,lark_cli_entry):
    if node_binary is None and lark_cli_entry is None:
        return dict(status='pending',node_binary=None,lark_cli_entry=None)
    if node_binary is None or lark_cli_entry is None:
        raise InstallError('cli_runtime_invalid')
    try:
        # Construct explicitly: installer never discovers inherited HOME/PATH
        # installations or accepts a personal credential-profile fallback.
        paths=RuntimePaths(str(path(node_binary)),str(path(lark_cli_entry))).validate()
        result=dict(status='configured',node_binary=paths.node_binary,lark_cli_entry=paths.lark_cli_entry)
        for key in ('node_binary','lark_cli_entry'):
            p=Path(result[key]);directory=leaf=None
            try:
                directory=os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
                meta=os.fstat(directory)
                if meta.st_uid not in (0,os.geteuid()) or meta.st_mode & 0o022:raise InstallError('cli_runtime_invalid')
                for part in p.parent.parts[1:]:
                    child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=directory)
                    os.close(directory);directory=child
                    meta=os.fstat(directory)
                    if meta.st_uid not in (0,os.geteuid()) or meta.st_mode & 0o022:raise InstallError('cli_runtime_invalid')
                leaf=os.open(p.name,getattr(os,'O_PATH',os.O_RDONLY)|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC,dir_fd=directory)
                meta=os.fstat(leaf)
                if not stat.S_ISREG(meta.st_mode) or meta.st_uid not in (0,os.geteuid()) or meta.st_mode & 0o022:
                    raise InstallError('cli_runtime_invalid')
                if key=='node_binary' and not meta.st_mode & 0o111:raise InstallError('cli_runtime_invalid')
                result[key+'_identity']={field:getattr(meta,'st_'+field) for field in ('dev','ino','mode','uid','gid','size','mtime_ns','ctime_ns')}
            finally:
                if leaf is not None:os.close(leaf)
                if directory is not None:os.close(directory)
        return result
    except (RuntimePathError,OSError,TypeError,ValueError):raise InstallError('cli_runtime_invalid') from None


def verify_cli_runtime(release,plan,runner):
    cli=plan.get('cli_runtime') or {}
    if cli.get('status')!='configured':return
    code='from hostd.cli_runtime import RuntimePaths; import sys; RuntimePaths(*sys.argv[1:]).validate(); print("validated")'
    result=runner([str(release/'venv/bin/python'),'-c',code,cli['node_binary'],cli['lark_cli_entry']],
                  cwd=str(release/'source'/BASE),timeout=30,limit=1024)
    if result.strip()!=b'validated':raise InstallError('cli_runtime_validation_failed')


def unit(release,targets,state_db,status_file,*,startup=None,migrate_bot_readers=False,cli=None):
    python=str(release/'venv/bin/python');working=str(release/'source'/BASE)
    command=[python,'-m','hostd','run','--only',','.join(t['name'] for t in targets),'--state-db',str(state_db),'--status-file',str(status_file)]
    pre=''
    if startup and startup['status']=='pinned':
        command+=['--onboarding-config',startup['candidate_path']]
        code='from hostd.safety import read_owned; import hashlib,sys; sys.exit(hashlib.sha256(read_owned(sys.argv[1])).hexdigest()!=sys.argv[2])'
        pre='ExecStartPre='+' '.join('"'+v+'"' for v in [python,'-c',code,startup['candidate_path'],startup['sha256']])+'\n'
    if migrate_bot_readers:command+=['--migrate-bot-readers']
    environment=''
    if cli and cli['status']=='configured':
        environment=''.join('Environment="'+key+'='+cli[value]+'"\n' for key,value in (('HOSTD_NODE_BINARY','node_binary'),('HOSTD_LARK_CLI_ENTRY','lark_cli_entry')))
        code='from hostd.cli_runtime import RuntimePaths; import sys; RuntimePaths(*sys.argv[1:]).validate()'
        pre+='ExecStartPre='+' '.join('"'+v+'"' for v in [python,'-c',code,cli['node_binary'],cli['lark_cli_entry']])+'\n'
    args=' '.join('"'+v+'"' for v in command)
    return '[Unit]\nDescription=Buzz hostd fixed revision\n[Service]\nType=simple\nWorkingDirectory='+working+'\n'+pre+'ExecStart='+args+'\nEnvironment=PYTHONDONTWRITEBYTECODE=1\n'+environment+'UMask=0077\nKillMode=control-group\nTimeoutStopSec=30\nRestart=on-failure\n[Install]\nWantedBy=default.target\n'

def check(repo,revision,inventory,release_root,state_db,status_file,*,runner=None,onboarding_config=None,onboarding_config_sha256=None,migrate_bot_readers=False,node_binary=None,lark_cli_entry=None):
    runner=runner or Runner();repo=Path(repo).absolute()
    if not re.fullmatch(r'[0-9a-f]{40}',revision):raise InstallError('revision_invalid')
    # Repository itself may be a shared dirty worktree; only git archive supplies source.
    actual=runner(['/usr/bin/git','-C',str(repo),'rev-parse','--verify',revision+'^{commit}'],timeout=10,limit=1024).decode().strip()
    if actual!=revision:raise InstallError('revision_invalid')
    roots=runner(['/usr/bin/git','-C',str(repo),'worktree','list','--porcelain'],timeout=10,limit=65536).decode()
    release_root=path(release_root);state_db=path(state_db);status_file=path(status_file)
    if state_db==status_file or state_db.is_relative_to(release_root) or status_file.is_relative_to(release_root):raise InstallError('runtime_path_conflict')
    for line in roots.splitlines():
        if line.startswith('worktree ') and release_root.is_relative_to(Path(line[9:])):raise InstallError('worktree_destination')
    targets=validate_targets(inventory)
    source=archive(repo,revision,runner);files=archive_files(source)
    before=states(targets,runner)
    busy=any(int(before[t['service']]['MainPID']) or int(before[t['service']]['ControlPID']) or int(before[t['service']]['TasksCurrent']) or before[t['service']]['ActiveState'] in {'active','activating','deactivating','reloading'} for t in targets)
    release=release_root/revision
    if type(migrate_bot_readers) is not bool:raise InstallError('startup_invalid')
    cli=cli_runtime(node_binary,lark_cli_entry)
    if cli['status']=='configured' and BASE+'/hostd/cli_runtime.py' not in files:raise InstallError('cli_runtime_missing')
    startup=onboarding(onboarding_config,onboarding_config_sha256,release)
    if startup['source_path'] is not None and (Path(startup['source_path']).is_relative_to(release_root) or startup['source_path'] in (str(state_db),str(status_file))):raise InstallError('runtime_path_conflict')
    if release.exists():raise InstallError('release_exists')
    plan=dict(version=1,status='busy' if busy else 'reviewable',installed=False,repo=str(repo),revision=revision,release=str(release),archive_sha256=hashlib.sha256(source).hexdigest(),lock_sha256=hashlib.sha256(files[LOCK]).hexdigest(),targets=targets,before=before,state_db=str(state_db),status_file=str(status_file),legacy_sha256={t['name']:legacy_hash(t['legacy_state']) for t in targets},onboarding=startup,migrate_bot_readers=migrate_bot_readers,cli_runtime=cli,desired_unit=unit(release,targets,state_db,status_file,startup=startup,migrate_bot_readers=migrate_bot_readers,cli=cli))
    plan['digest']=digest(plan)
    return plan

def prepare(plan,expected_digest,*,runner=None):
    runner=runner or Runner();body={k:v for k,v in plan.items() if k!='digest'}
    if plan.get('digest')!=expected_digest or digest(body)!=expected_digest:raise InstallError('plan_changed')
    if plan['status']!='reviewable':raise InstallError('busy')
    startup=plan.get('onboarding') or {}
    cli=plan.get('cli_runtime') or {}
    options=dict(onboarding_config=startup.get('source_path'),onboarding_config_sha256=startup.get('sha256'),migrate_bot_readers=plan.get('migrate_bot_readers',False),node_binary=cli.get('node_binary'),lark_cli_entry=cli.get('lark_cli_entry'))
    current=check(plan['repo'],plan['revision'],plan['targets'],Path(plan['release']).parent,plan['state_db'],plan['status_file'],runner=runner,**options)
    if current['digest']!=expected_digest:raise InstallError('plan_changed')
    root=Path(plan['release']).parent;fd=directory(root)
    staging_name='.prepare-'+uuid.uuid4().hex
    os.mkdir(staging_name,0o700,dir_fd=fd)
    staging=root/staging_name
    artifact=staging
    # Hold root FD and operate through it; publication is a no-clobber rename under lock.
    # Public path helpers reject proc symlinks, so verify root binding before each phase.
    def stable():
        meta=os.fstat(fd);linked=os.stat(root,follow_symlinks=False)
        if (meta.st_dev,meta.st_ino)!=(linked.st_dev,linked.st_ino):raise InstallError('path_changed')
    try:
        stable();data=archive(plan['repo'],plan['revision'],runner)
        if hashlib.sha256(data).hexdigest()!=plan['archive_sha256']:raise InstallError('archive_changed')
        files=archive_files(data)
        if startup.get('status')=='pinned':
            raw=read(startup['source_path'],limit=128*1024,private=True)
            if hashlib.sha256(raw).hexdigest()!=startup['sha256']:raise InstallError('startup_changed')
            write(staging,'onboarding-config.json',raw)
        source=staging/'source';source.mkdir(mode=0o700)
        # No tar extract: validated regular files only, exclusive nofollow publication.
        for name,content in files.items():
            dst=source/name
            parent_fd=directory(dst.parent);os.close(parent_fd)
            write(dst.parent,dst.name,content)
        rollback=staging/'rollback';rollback.mkdir(mode=0o700)
        for index,t in enumerate(plan['targets']):
            old=read(t['legacy_state'],private=True,missing=True)
            actual=hashlib.sha256(old).hexdigest() if old is not None else None
            if actual!=plan['legacy_sha256'][t['name']]:raise InstallError('state_changed')
            if old is not None:write(rollback,f'state{index}.json',old)
            for kind in ['service','timer']:
                fragment=plan['before'][t[kind]]['FragmentPath']
                if fragment:
                    oldunit=read(fragment)
                    if hashlib.sha256(oldunit).hexdigest()!=plan['before'][t[kind]]['fragment_sha256']:raise InstallError('unit_changed')
                    write(rollback,t[kind],oldunit)
        db=Path(plan['state_db'])
        if db.exists():
            snapshot_database(db,rollback/'hostd.sqlite3')
        jsonwrite(rollback,'manifest.json',plan)
        stable();runner(['/usr/bin/python3','-m','venv','--without-pip',str(staging/'venv')],timeout=60,limit=65536)
        python=str(staging/'venv/bin/python')
        runner([str(Path.home()/'.local/bin/uv'),'pip','install','--python',python,'--index-url','https://pypi.org/simple','--require-hashes','--only-binary',':all:','-r',str(source/LOCK)],timeout=300,limit=1024*1024)
        runner([str(Path.home()/'.local/bin/uv'),'pip','check','--python',python],timeout=30,limit=65536)
        versions=lock_versions(files[LOCK])
        verification='import importlib.metadata as m,json; print(json.dumps({n:m.version(n) for n in '+repr(list(versions))+'}))'
        observed=json.loads(runner([python,'-c',verification],timeout=30,limit=65536))
        if observed!=versions:raise InstallError('dependency_readback')
        runner([python,'-m','hostd','--help'],cwd=str(source/BASE),timeout=30,limit=65536)
        verify_startup(staging,plan,runner)
        verify_cli_runtime(staging,plan,runner)
        write(staging,'candidate.service',plan['desired_unit'].encode())
        # Venv scripts carry staging prefixes: creation must use final path. Relocate
        # only generated entrypoint text, and verify Python at final path afterward.
        for script in (staging/'venv/bin').iterdir():
            if script.is_file() and not script.is_symlink():
                raw=script.read_bytes()
                if b'\0' not in raw and str(staging).encode() in raw:
                    script.write_bytes(raw.replace(str(staging).encode(),plan['release'].encode()))
        # Freeze generated environment as well as source. Symlinks created by
        # stdlib venv are retained; arbitrary archive symlinks were rejected.
        for dst in (staging/'venv').rglob('*'):
            if dst.is_file() and not dst.is_symlink():
                dst.chmod(0o555 if dst.stat().st_mode & 0o111 else 0o444)
        for dst in sorted((staging/'venv').rglob('*'),key=lambda x:len(x.parts),reverse=True):
            if dst.is_dir() and not dst.is_symlink():dst.chmod(0o555)
        (staging/'venv').chmod(0o555)
        for dst in source.rglob('*'):
            if dst.is_file():dst.chmod(0o444)
        for dst in sorted(source.rglob('*'),key=lambda x:len(x.parts),reverse=True):
            if dst.is_dir():dst.chmod(0o555)
        source.chmod(0o555)
        receipt=dict(status='prepared',installed=False,revision=plan['revision'],release=plan['release'],plan_digest=expected_digest,onboarding_status=startup.get('status','pending'),onboarding_sha256=startup.get('sha256'),migrate_bot_readers=plan.get('migrate_bot_readers',False),cli_runtime_status=cli.get('status','pending'),verification=('dependency/startup schema check passed' if startup.get('status')=='pinned' else 'dependency check passed; startup configuration pending')+('; media CLI paths validated offline' if cli.get('status')=='configured' else '; media CLI paths pending')+'; owner authorization, rollback review, service activation and L3 pending')
        jsonwrite(staging,'source-manifest.json',{name:hashlib.sha256(content).hexdigest() for name,content in files.items()})
        stable()
        # Preparation can take minutes; timers or another operator may have
        # changed original state during dependency work. Recheck before publish.
        final_plan=check(plan['repo'],plan['revision'],plan['targets'],root,plan['state_db'],plan['status_file'],runner=runner,**options)
        if final_plan['digest']!=expected_digest:raise InstallError('plan_changed')
        if Path(plan['release']).exists():raise InstallError('release_exists')
        os.rename(staging_name,plan['revision'],src_dir_fd=fd,dst_dir_fd=fd);os.fsync(fd)
        artifact=Path(plan['release'])
        final_python=str(artifact/'venv/bin/python')
        observed=json.loads(runner([final_python,'-c',verification],timeout=30,limit=65536))
        if observed!=versions:raise InstallError('final_dependency_readback')
        runner([final_python,'-m','hostd','--help'],cwd=str(artifact/'source'/BASE),timeout=30,limit=65536)
        verify_startup(artifact,plan,runner)
        verify_cli_runtime(artifact,plan,runner)
        jsonwrite(artifact,'receipt.json',receipt)
        return receipt
    except Exception as error:
        try:jsonwrite(artifact,'receipt.json',dict(status='failed',installed=False,error=getattr(error,'code','preparation_failed')))
        except Exception:pass
        if isinstance(error,InstallError):raise
        raise InstallError('preparation_failed') from None
    finally:os.close(fd)

def snapshot_database(source,destination):
    source=path(source);read(source,private=True)
    deadline=time.monotonic()+10
    def progress(status,remaining,total):
        if time.monotonic()>deadline:raise InstallError('snapshot_timeout')
    fd=os.open(destination,os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600);os.close(fd)
    with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True,timeout=2) as original, sqlite3.connect(destination) as backup:
        original.backup(backup,pages=128,sleep=.01,progress=progress)
    destination.chmod(0o600)

def quiescent(inventory,runner):
    # Native cgroup task count, both manager PIDs and inactive state must agree.
    hostd=dict(service='buzz-hostd.service',timer='buzz-hostd.service')
    observed=states([hostd]+inventory,runner)
    host=observed['buzz-hostd.service']
    if host['ActiveState']!='inactive' or any(int(host[k]) for k in ['MainPID','ControlPID','TasksCurrent']):raise InstallError('hostd_not_reaped')
    for t in inventory:
        service=observed[t['service']];timer=observed[t['timer']]
        if service['ActiveState']!='inactive' or timer['ActiveState']!='inactive' or any(int(service[k]) for k in ['MainPID','ControlPID','TasksCurrent']):raise InstallError('old_writer_running')
    return observed

def export_rollback(release,inventory,state_db,destination,*,runner=None):
    """After externally verified stop, snapshot newest ledger and export 15 states.

    No timer restoration or runtime signal. Failure retains private evidence.
    """
    runner=runner or Runner();targets=validate_targets(inventory)
    before=quiescent(targets,runner)
    release=path(release);destination=path(destination)
    if destination.exists():raise InstallError('rollback_exists')
    fd=directory(destination);os.close(fd)
    snapshot=destination/'hostd.sqlite3';snapshot_database(state_db,snapshot)
    jsonwrite(destination,'inventory.json',targets)
    # Operate only on the private backup, using its own fixed-version adapter.
    code="""import json,hashlib,sys
from pathlib import Path
from hostd.store import Store
from hostd.state_store import StateAdapter
root=Path(sys.argv[1]);targets=json.loads((root/'inventory.json').read_text())
with Store(root/'hostd.sqlite3') as store:
 for t in targets:
  row=store.conn.execute('SELECT channel_id,chat_id,sync_app_id FROM binding WHERE binding_id=?',(t['name'],)).fetchone()
  if not (row and row['channel_id']==t['channel_id'] and hashlib.sha256(row['chat_id'].encode()).hexdigest()==t['chat_id_hash'] and row['sync_app_id']==t['app_id']):raise ValueError('target_identity_mismatch')
  StateAdapter(store,t['name'],Path(t['legacy_state']).parent).export_legacy(root/t['name'])
print('exported')
"""
    output=runner([str(release/'venv/bin/python'),'-c',code,str(destination)],cwd=str(release/'source'/BASE),timeout=30,limit=65536)
    if output.strip()!=b'exported':raise InstallError('rollback_export_unverified')
    hashes={}
    for t in targets:hashes[t['name']]=hashlib.sha256(read(destination/t['name']/'state.json',private=True)).hexdigest()
    if quiescent(targets,runner)!=before:raise InstallError('rollback_runtime_changed')
    result=dict(status='exported',installed=False,targets=targets,state_sha256=hashes,ledger_sha256=hashlib.sha256(read(snapshot,private=True)).hexdigest())
    jsonwrite(destination,'export-receipt.json',result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    modes=parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--export-rollback',action='store_true');modes.add_argument('--check',action='store_true');modes.add_argument('--dry-run',action='store_true');modes.add_argument('--apply',action='store_true')
    parser.add_argument('--repo');parser.add_argument('--revision');parser.add_argument('--inventory',required=True)
    parser.add_argument('--release-root');parser.add_argument('--state-db',required=True);parser.add_argument('--status-file');parser.add_argument('--release');parser.add_argument('--rollback-dir')
    parser.add_argument('--expect-plan-digest')
    parser.add_argument('--onboarding-config',help='explicit protected startup JSON; no default discovery')
    parser.add_argument('--onboarding-config-sha256',help='reviewed exact SHA256 of the protected startup JSON')
    parser.add_argument('--migrate-bot-readers',action='store_true',help='explicit candidate startup migration; authorization/rollback review still required')
    parser.add_argument('--node-binary',help='reviewed absolute executable Node path; pair with --lark-cli-entry')
    parser.add_argument('--lark-cli-entry',help='reviewed absolute readable CLI entry path; pair with --node-binary')
    args=parser.parse_args()
    try:
        inventory=json.loads(read(args.inventory,limit=128*1024,private=True))
        if args.export_rollback:
            if args.onboarding_config is not None or args.onboarding_config_sha256 is not None or args.migrate_bot_readers or args.node_binary is not None or args.lark_cli_entry is not None:raise InstallError('arguments_invalid')
            if not args.release or not args.rollback_dir:raise InstallError('arguments_invalid')
            print(json.dumps(export_rollback(args.release,inventory,args.state_db,args.rollback_dir),indent=2));return 0
        if not all([args.repo,args.revision,args.release_root,args.status_file]):raise InstallError('arguments_invalid')
        plan=check(args.repo,args.revision,inventory,args.release_root,args.state_db,args.status_file,onboarding_config=args.onboarding_config,onboarding_config_sha256=args.onboarding_config_sha256,migrate_bot_readers=args.migrate_bot_readers,node_binary=args.node_binary,lark_cli_entry=args.lark_cli_entry)
        if args.apply:
            if not args.expect_plan_digest:raise InstallError('review_required')
            result=prepare(plan,args.expect_plan_digest)
        else:result=public_plan(plan)
        print(json.dumps(result,indent=2));return 0
    except (InstallError,OSError,ValueError,KeyError,TypeError) as error:
        print(json.dumps((error if isinstance(error,InstallError) else InstallError()).public(),ensure_ascii=False));return 1
if __name__=='__main__':sys.exit(main())
