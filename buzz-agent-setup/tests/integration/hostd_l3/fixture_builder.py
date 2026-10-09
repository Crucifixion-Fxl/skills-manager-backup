"""Static private L3 packaging. No activation, authority generation or processes.

Only explicitly referenced root-supplied material is copied. App credentials are
opaque pinned bytes; successful packaging still requires runtime verification.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import ssl
import stat
import subprocess
import sys

# The test harness entry is also importable from a pinned candidate checkout.
TESTS = Path(__file__).resolve().parents[2]
if str(TESTS) not in sys.path: sys.path.insert(0, str(TESTS))
from integration.hostd_l3 import prepare
from hostd import agent_catalog
from hostd.onboarding_runtime import RuntimeConfig
import buzz_feishu_group_sync as gs
import recovery_authority as authority

NOTICE = ('本机 L3 fixture 尚未安全准备完成。怎么解决：检查已审核源码、命名分支、固定测试群与三套独立应用材料、私有目录和文件固定值；失败目录保持待核验，不要当作已启动或验收通过。'
          '\n复制给 AI：帮我核查 hostd L3 静态 fixture、引用文件闭包、文件变更和运行时凭据待核验状态；不要输出密钥、凭据、消息正文或个人资料。')
BUNDLE_KEYS = {'version','binding_config','catalog_path','legacy_join_path','onboarding_config',
               'fixture_path','fixture_sha256','trust_bundle','buzz_cli','files'}
ENV_PATHS = {'BUZZ_ACP_SYSTEM_PROMPT_FILE','BUZZ_RESPONSIBLE_CONFIG','BUZZ_ACP_WORK_DIR',
             'LARKSUITE_CLI_CONFIG_DIR','LARKSUITE_CLI_DATA_DIR'}
GIT_ENV = {'PATH':'/usr/bin:/bin','LC_ALL':'C','GIT_CONFIG_GLOBAL':'/dev/null',
           'GIT_CONFIG_NOSYSTEM':'1','GIT_OPTIONAL_LOCKS':'0'}


class BuildError(ValueError):
    def __init__(self): super().__init__(NOTICE)


def _json(raw): return prepare._json(raw)
def _digest(raw): return hashlib.sha256(raw).hexdigest()
def _encoded(value): return (json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode()


def _source(source, revision, branch, runner):
    if (not isinstance(branch,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./-]{0,127}',branch)
            or '..' in branch or branch.endswith('/') or not re.fullmatch('[a-f0-9]{40}',revision)):
        raise ValueError
    prepare._private_directory(source)
    prepare._git_check(source,revision,runner)
    result=runner(['git','-C',str(source),'symbolic-ref','--quiet','--short','HEAD'],env=GIT_ENV,
                  capture_output=True,text=True,timeout=5,check=False)
    if result.returncode or result.stdout.strip()!=branch: raise ValueError
    files=prepare._runtime_files(source)
    entry=source/'skills/agent-harness/buzz-agent-setup/scripts/hostd/__main__.py'
    if not files or entry not in files: raise ValueError
    return tuple(sorted((p,prepare._file_digest(p)) for p in files))


def _directory(path):
    prepare._private_directory(path)
    fd=prepare._open(path,directory=True,private=True)
    try:
        st=os.fstat(fd);return st.st_dev,st.st_ino
    finally:os.close(fd)


def _bytes(path, *, executable=False):
    if not executable:return prepare._bytes(path)
    fd=prepare._open(path)
    try:
        meta=os.fstat(fd)
        if meta.st_uid!=os.geteuid() or stat.S_IMODE(meta.st_mode)!=0o700 or meta.st_size>256*1024*1024:raise ValueError
        parts=[]
        while chunk:=os.read(fd,65536):parts.append(chunk)
        final=os.fstat(fd)
        if (meta.st_size,meta.st_mtime_ns,meta.st_ino)!=(final.st_size,final.st_mtime_ns,final.st_ino):raise ValueError
        return b''.join(parts)
    finally:os.close(fd)


@dataclass(frozen=True)
class _Plan:
    run: Path
    source: Path
    revision: str
    branch: str
    fixture_hash: str
    python: str
    bundle_path: Path
    _bundle_hash: str = field(repr=False)
    _input_files: tuple = field(repr=False)
    _source_files: tuple = field(repr=False)
    _directories: tuple = field(repr=False)
    _output_files: tuple = field(repr=False)
    _output_dirs: tuple = field(repr=False)
    _manifest: bytes = field(repr=False)
    _runner: object = field(repr=False,compare=False)
    mode: str = 'THREE_LOCAL'
    local_app_ids: tuple = tuple(sorted(prepare.APPS))
    _expected_local_app_ids: tuple | None = field(default=None, repr=False)
    _seed_inventory: tuple = field(default=(), repr=False)

    def readback(self):
        return {'status':'planned','live_verified':False,'app_count':len(self.local_app_ids),'chat_count':2,
                'credentials_pinned':True,'runtime_verification':'pending'}

    def _revalidate(self, *, created=False):
        raw = prepare._bytes(self.bundle_path)
        if _digest(raw) != self._bundle_hash: raise ValueError
        doc = _json(raw)
        mode, apps, pin = prepare._local_mode(doc, self._expected_local_app_ids)
        if (mode, apps, pin) != (self.mode, self.local_app_ids, self._expected_local_app_ids): raise ValueError
        if mode == 'TWO_LOCAL':
            manifest_mode = prepare._local_mode(_json(self._manifest), self._expected_local_app_ids)
            buzz = prepare._path(doc['buzz_cli'])
            if (manifest_mode != (mode, apps, pin) or not self._seed_inventory
                    or prepare._inventory(self.bundle_path.parent, executable=buzz) != self._seed_inventory): raise ValueError
        if not created and os.path.lexists(self.run):raise ValueError
        if _source(self.source,self.revision,self.branch,self._runner)!=self._source_files:raise ValueError
        for path,identity in self._directories:
            if _directory(path)!=identity:raise ValueError
        for path,digest,executable in self._input_files:
            if _digest(_bytes(path,executable=executable))!=digest:raise ValueError

    def build(self):
        try:
            self._revalidate()  # All admission/CAS checks precede any write.
            parent_fd=prepare._open(self.run.parent,directory=True,private=True)
            root_fd=None
            try:
                expected=dict(self._directories)[self.run.parent]
                parent=os.fstat(parent_fd)
                if (parent.st_dev,parent.st_ino)!=expected:raise ValueError
                os.mkdir(self.run.name,0o700,dir_fd=parent_fd)
                root_fd=os.open(self.run.name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=parent_fd)
                for rel in self._output_dirs:self._mkdir(root_fd,Path(rel))
                for rel,raw,mode in self._output_files:self._write(root_fd,Path(rel),raw,mode)
                # Admission reads a private staging manifest. It is not the
                # successful manifest and survives failures only as pending data.
                self._write(root_fd,Path('pending-manifest.json'),self._manifest,0o600)
                prepared=prepare.PreparedRun.check(self.run/'pending-manifest.json',reviewed_source=self.source,
                    reviewed_revision=self.revision,fixture_sha256=self.fixture_hash,command_runner=self._runner,
                    expected_local_app_ids=self._expected_local_app_ids)
                RuntimeConfig.load(prepared.onboarding_config)
                # No ready/eligible/grant claims; the runtime will decrypt its own
                # app credentials and verify relay access after explicit start.
                self._write(root_fd,Path('builder-receipt.json'),_encoded(dict(status='materialized',
                    live_verified=False,credentials_pinned=True,runtime_verification='pending')),0o600)
                stage=self.run/'admitted-manifest.json'
                self._write(root_fd,Path(stage.name),self._manifest,0o600)
                prepared=prepare.PreparedRun.check(stage,reviewed_source=self.source,
                    reviewed_revision=self.revision,fixture_sha256=self.fixture_hash,command_runner=self._runner,
                    expected_local_app_ids=self._expected_local_app_ids)
                self._revalidate(created=True)
                current=prepare._open(self.run,directory=True,private=True)
                try:
                    a,b=os.fstat(current),os.fstat(root_fd)
                    if (a.st_dev,a.st_ino)!=(b.st_dev,b.st_ino):raise ValueError
                finally:os.close(current)
                published=False
                staged=os.stat(stage.name,dir_fd=root_fd,follow_symlinks=False)
                try:
                    # Exclusive publication: an existing filename is never
                    # overwritten. Both names refer only to our private stage.
                    os.link(stage.name,'manifest.json',src_dir_fd=root_fd,dst_dir_fd=root_fd,follow_symlinks=False)
                    published=True
                    os.fsync(root_fd)
                    if self.mode == 'TWO_LOCAL':
                        # ScenarioPlan requires each protected input to have a
                        # single name. Retire only this builder's staging alias.
                        os.unlink(stage.name,dir_fd=root_fd)
                        os.fsync(root_fd)
                        prepared=prepare.PreparedRun.check(self.run/'manifest.json',reviewed_source=self.source,
                            reviewed_revision=self.revision,fixture_sha256=self.fixture_hash,command_runner=self._runner,
                            expected_local_app_ids=self._expected_local_app_ids)
                    else:
                        prepared=replace(prepared,_snapshots=tuple((self.run/'manifest.json' if p==stage else p,h)
                            for p,h in prepared._snapshots))
                except Exception:
                    if published:
                        observed=os.stat('manifest.json',dir_fd=root_fd,follow_symlinks=False)
                        if (observed.st_dev,observed.st_ino)==(staged.st_dev,staged.st_ino):
                            os.unlink('manifest.json',dir_fd=root_fd)
                    raise
            finally:
                if root_fd is not None:os.close(root_fd)
                os.close(parent_fd)
            return prepared
        except Exception: raise BuildError() from None

    @staticmethod
    def _mkdir(root_fd,rel):
        fd=os.dup(root_fd)
        try:
            for name in rel.parts:
                try:os.mkdir(name,0o700,dir_fd=fd)
                except FileExistsError:pass
                child=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=fd)
                meta=os.fstat(child)
                if meta.st_uid!=os.geteuid() or stat.S_IMODE(meta.st_mode)!=0o700:
                    os.close(child);raise ValueError
                os.close(fd);fd=child
        finally:os.close(fd)

    @staticmethod
    def _write(root_fd,rel,raw,mode):
        fd=os.dup(root_fd);output=None
        try:
            for name in rel.parts[:-1]:
                child=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=fd)
                os.close(fd);fd=child
            output=os.open(rel.name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,mode,dir_fd=fd)
            os.fchmod(output,mode);offset=0
            while offset<len(raw):
                size=os.write(output,raw[offset:offset+65536])
                if size<=0:raise ValueError
                offset+=size
            os.fsync(output)
        finally:
            if output is not None:os.close(output)
            os.close(fd)


class FixtureBuilder:
    @classmethod
    def check(cls,bundle_path,*,run_parent,run_id,reviewed_source,reviewed_revision,reviewed_branch,
              fixture_sha256,python,command_runner=subprocess.run,expected_local_app_ids=None):
        try:
            bundle=prepare._path(bundle_path);seed=bundle.parent
            parent=prepare._path(run_parent);source=prepare._path(reviewed_source)
            if not re.fullmatch('[a-f0-9]{32}',run_id):raise ValueError
            if parent.is_relative_to(seed) or seed.is_relative_to(parent) or parent.is_relative_to(source):raise ValueError
            directories={seed:_directory(seed),parent:_directory(parent)}
            run=parent/('run-'+run_id)
            if os.path.lexists(run):raise ValueError
            raw=prepare._bytes(bundle);doc=_json(raw)
            mode, local_apps, local_pin = prepare._local_mode(doc, expected_local_app_ids)
            keys = BUNDLE_KEYS | {'mode', 'local_app_ids'} if mode == 'TWO_LOCAL' else BUNDLE_KEYS
            if set(doc)!=keys:raise ValueError
            files=doc['files']
            if not isinstance(files,dict) or not 1<=len(files)<=64:raise ValueError
            material={};inputs=[];total=0
            buzz=prepare._inside(doc['buzz_cli'],seed)
            for rel,digest in files.items():
                if (not isinstance(rel,str) or not rel or Path(rel).is_absolute() or '..' in Path(rel).parts
                        or str(Path(rel))!=rel or not re.fullmatch('[a-f0-9]{64}',digest)):raise ValueError
                path=prepare._inside(seed/rel,seed)
                if path==bundle:raise ValueError
                value=_bytes(path,executable=path==buzz);total+=len(value)
                if total>256*1024*1024 or _digest(value)!=digest:raise ValueError
                material[path]=value;inputs.append((path,digest,path==buzz))
                directory=path.parent
                while directory!=seed:
                    directories[directory]=_directory(directory);directory=directory.parent
            used=set();output={};outdirs={'home','runtime','home/.config/buzz-feishu-sync','runtime/join-state'}
            def path(value):return prepare._inside(value,seed)
            def relocated(value):return str(run/path(value).relative_to(seed))
            def read(value):
                p=path(value)
                if p not in material:raise ValueError
                used.add(p);return material[p]
            def directory(value):
                p=path(value);directories[p]=_directory(p)
                outdirs.add(str(p.relative_to(seed)))
                return relocated(value)
            def optional(value):
                p=path(value)
                if os.path.lexists(p):read(p)
                outdirs.add(str(p.parent.relative_to(seed)))
                return relocated(value)
            def env(value,*,agent=None,owner=None):
                p=path(value);body=prepare.join.parse_env(read(p).decode())
                if body.get('BUZZ_RELAY_URL')!=relay:raise ValueError
                if agent:
                    actual=gs._signer_pubkey(gs.secret_hex(body['BUZZ_PRIVATE_KEY'],'fixture agent'))
                    auth=json.loads(body['BUZZ_AUTH_TAG'])
                    if actual!=agent or auth[2] or authority.attested_owner({'tags':[auth]},actual)!=owner:raise ValueError
                for name in ENV_PATHS:
                    if name in body:
                        body[name]=directory(body[name]) if name.endswith('_DIR') else relocated_file(body[name])
                output[p]=(''.join(k+'='+shlex.quote(v)+'\n' for k,v in sorted(body.items()))).encode()
                return body
            def relocated_file(value):read(value);return relocated(value)
            if doc['fixture_sha256']!=fixture_sha256 or not re.fullmatch('[a-f0-9]{64}',fixture_sha256):raise ValueError
            fixture=read(doc['fixture_path'])
            if _digest(fixture)!=fixture_sha256:raise ValueError
            groups=_json(fixture)
            if (set(groups)!={'hostd-test-群X','hostd-test-群W-未绑定'} or len(set(groups.values()))!=2
                    or any(not isinstance(x,str) or not re.fullmatch('oc_[A-Za-z0-9]+',x) for x in groups.values())):raise ValueError
            cfg=prepare.validate_config(_json(read(doc['binding_config'])))
            relay=_json(read(doc['onboarding_config']))['relay_url']
            prepare._loopback_url(relay,'wss')
            people=cfg['people_api']['base_url']
            if prepare._loopback_url(people,'https')!=prepare._loopback_url(relay,'wss'):raise ValueError
            if (cfg['chat_id']!=groups['hostd-test-群X'] or {a['app_id'] for a in cfg['agents'].values()}!=set(local_apps)
                    or len(cfg['agents'])!=len(local_apps) or cfg['agents'][cfg['desk_pubkey']]['app_id']!='cli_aa48bbeeba38dbcf'
                    or cfg['owner_app_id']!='cli_a940faa4ec381bc4' or cfg.get('identity','union_id')!='union_id'
                    or cfg.get('reaction_sync','two_way')!='two_way' or path(cfg['buzz_cli'])!=buzz):raise ValueError
            read(buzz)
            if _digest(material[buzz])!=cfg['buzz_cli_sha256']:raise ValueError
            catalogue=agent_catalog._document(read(doc['catalog_path']))
            legacy=agent_catalog._document(read(doc['legacy_join_path']),allow_empty=True)
            if legacy['agents']!=[] or catalogue['owner_pubkey']!=legacy['owner_pubkey'] or len(catalogue['agents'])!=len(local_apps):raise ValueError
            if mode == 'TWO_LOCAL':
                profiles = [a[key] for a in cfg['agents'].values() for key in ('lark_config_dir', 'lark_data_dir')]
                if len(set(profiles)) != len(profiles): raise ValueError
            owner=catalogue['owner_pubkey'];seen=set()
            for a in catalogue['agents']:
                block=a['feishu'];app=block['app_id']
                if app not in set(local_apps) or app in seen or run_id not in a['unit']:raise ValueError
                seen.add(app)
                source_env=prepare.join.parse_env(read(a['env_file']).decode())
                pub=gs._signer_pubkey(gs.secret_hex(source_env['BUZZ_PRIVATE_KEY'],'fixture agent'))
                if (cfg['agents'].get(pub)!=block or source_env.get('BUZZ_ACP_AGENT_OWNER')!=owner
                        or prepare.join._allowlist(source_env.get('BUZZ_ACP_CHANNELS',''))!=[cfg['channel_id']]):raise ValueError
                for name in ('BUZZ_ACP_SYSTEM_PROMPT_FILE','BUZZ_RESPONSIBLE_CONFIG'):
                    if name not in source_env:raise ValueError
                responsible=path(source_env['BUZZ_RESPONSIBLE_CONFIG'])
                contents=_json(read(responsible))
                if 'people_file' in contents:
                    contents['people_file']=optional(contents['people_file']);output[responsible]=_encoded(contents)
                env(a['env_file'],agent=pub,owner=owner)
                a['env_file']=relocated(a['env_file'])
                if 'log_file' in a:
                    if os.path.lexists(path(a['log_file'])):raise ValueError
                    a['log_file']=optional(a['log_file'])
                for name in ('lark_config_dir','lark_data_dir'):block[name]=directory(block[name])
            # Profile identities and normal opaque credential dependencies are
            # validated against exactly the app assigned in the original schema.
            for agent in cfg['agents'].values():
                config=path(agent['lark_config_dir']);data=path(agent['lark_data_dir'])
                profile=_json(read(config/'config.json'))
                if not isinstance(profile.get('apps'),list) or len(profile['apps'])!=1 or profile['apps'][0].get('appId')!=agent['app_id']:raise ValueError
                read(data/'lark-cli/master.key');read(data/f"lark-cli/appsecret_{agent['app_id']}.enc")
                directory(data/'lark-cli')
                for name in ('lark_config_dir','lark_data_dir'):agent[name]=directory(agent[name])
            for listing in (catalogue,legacy):
                if listing['buzz']!={'cli_path':str(buzz),'cli_sha256':cfg['buzz_cli_sha256']}:raise ValueError
                listing['buzz']['cli_path']=relocated(buzz)
                listing['state_dir']=directory(listing['state_dir'])
                read(listing['lark_cli']);listing['lark_cli']=relocated(listing['lark_cli'])
            mirror=cfg['mirror_env_file'];env(mirror,agent=cfg['mirror_pubkey'],owner=owner);cfg['mirror_env_file']=relocated(mirror)
            signer=cfg['people_api']['signer_env_file'];signer_body=env(signer)
            if gs._signer_pubkey(gs.secret_hex(signer_body['BUZZ_PRIVATE_KEY'],'fixture owner'))!=owner:raise ValueError
            cfg['people_api']['signer_env_file']=relocated(signer)
            cfg['buzz_cli']=relocated(buzz);read(cfg['lark_cli']);cfg['lark_cli']=relocated(cfg['lark_cli'])
            if 'people_cache_file' in cfg:cfg['people_cache_file']=optional(cfg['people_cache_file'])
            rt=_json(read(doc['onboarding_config']))
            # Actual runtime schema before rewriting, then again on the written
            # protected output. No new authority fields are synthesized.
            RuntimeConfig.load(path(doc['onboarding_config']))
            expected={'template_config':doc['binding_config'],'catalog_path':doc['catalog_path'],'legacy_join_path':doc['legacy_join_path'],
                      'binding_dir':str(path(doc['binding_config']).parent.parent),'owner_env_file':str(path(signer))}
            if any(rt.get(k)!=v for k,v in expected.items()) or rt.get('trusted_relays')!=[relay]:raise ValueError
            for name in expected:rt[name]=directory(rt[name]) if name=='binding_dir' else relocated_file(rt[name])
            fixed_config=Path('home/.config/buzz-feishu-sync/hostd-local-l3/config.json')
            if path(doc['binding_config']).relative_to(seed)!=fixed_config:raise ValueError
            output[path(doc['binding_config'])]=_encoded(cfg)
            output[path(doc['catalog_path'])]=_encoded(catalogue);output[path(doc['legacy_join_path'])]=_encoded(legacy)
            output[path(doc['onboarding_config'])]=_encoded(rt)
            trust=read(doc['trust_bundle']);context=ssl.create_default_context(cadata=trust.decode('ascii'))
            if not set(ssl.create_default_context().get_ca_certs(binary_form=True)).issubset(set(context.get_ca_certs(binary_form=True))):raise ValueError
            if used!=set(material):raise ValueError
            inventory = ()
            if mode == 'TWO_LOCAL':
                inventory = prepare._inventory(seed, executable=buzz)
                if {p for p, kind in inventory if kind == 'file'} != set(material) | {bundle}: raise ValueError
            py=prepare._path(python);fd=prepare._open(py);os.close(fd)
            if not os.access(py,os.X_OK):raise ValueError
            source_files=_source(source,reviewed_revision,reviewed_branch,command_runner)
            files_out=tuple(sorted((str(p.relative_to(seed)),output.get(p,raw),0o700 if p==buzz else 0o600) for p,raw in material.items()))
            for rel,_,_ in files_out:
                for d in Path(rel).parents:
                    if str(d)!='.':outdirs.add(str(d))
            manifest=dict(version=doc['version'],run_id=run_id,run_dir=str(run),source_root=str(source),revision=reviewed_revision,
                source_hashes={str(p.relative_to(source)):digest for p,digest in source_files},fixture_path=relocated(doc['fixture_path']),
                fixture_sha256=fixture_sha256,initial_binding='hostd-local-l3',onboarding_config=relocated(doc['onboarding_config']),
                buzz_cli=relocated(buzz),buzz_sha256=cfg['buzz_cli_sha256'],python=str(py),relay_url=relay,people_url=people,trust_bundle=relocated(doc['trust_bundle']))
            if mode == 'TWO_LOCAL': manifest.update(mode=mode, local_app_ids=list(local_apps))
            return _Plan(run,source,reviewed_revision,reviewed_branch,fixture_sha256,str(py),bundle,_digest(raw),
                tuple(inputs),source_files,tuple(sorted(directories.items())),files_out,
                tuple(sorted(outdirs,key=lambda r:(len(Path(r).parts),r))),_encoded(manifest),command_runner,
                mode,local_apps,local_pin,inventory)
        except Exception:raise BuildError() from None
