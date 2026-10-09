"""Read-only L3 admission and explicitly owned daemon process lifecycle.

Root supplies frozen source/fixture pins and private run inputs. This helper does
not bootstrap credentials, Docker, groups or TLS. `prepared` and `running` are
local observations, never evidence that any L3 scenario or connection passed.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import ssl
import stat
import subprocess
import sys
import threading
import time

SCRIPTS = Path(__file__).resolve().parents[3] / 'scripts'
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
from hostd.config import validate_config
import buzz_agent_join_requests as join

NODE = '/home/jchen/.nvm/versions/node/v20.20.1/bin/node'
CLI_ENTRY = '/home/jchen/.npm-global/lib/node_modules/@larksuite/cli/scripts/run.js'
APPS = frozenset(('cli_aa48bbeeba38dbcf', 'cli_aa48b41531785bfc', 'cli_aa48b406abf8dbe7'))
TWO_LOCAL = ('cli_aa48bbeeba38dbcf', 'cli_aa48b41531785bfc')
NOTICE = ('本机 L3 准备尚未通过。怎么解决：核对已审核源码与测试群固定值、独立运行目录、完整配置和 TLS 信任文件；只启动并回收本次持有的进程。'
          '\n复制给 AI：帮我检查 hostd 本机 L3 准备与进程读回；不要读取真实 HOME 凭据、输出密钥或把 prepared/running 当作业务验收完成。')

class PreparationError(ValueError):
    pass


def _path(value):
    if (not isinstance(value, (str, Path)) or not str(value) or not Path(value).is_absolute()
            or '..' in Path(value).parts or any(ord(c) < 32 or ord(c) == 127 for c in str(value))):
        raise ValueError
    return Path(value)


def _open(path, directory=False, private=False):
    """Open through nofollow ancestors; private run directories must be 0700."""
    path = _path(path)
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        for index, part in enumerate(path.parts[1:]):
            last = index == len(path.parts) - 2
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
            if not last or directory:
                flags |= os.O_DIRECTORY
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd); fd = child
        meta = os.fstat(fd)
        if directory:
            if not stat.S_ISDIR(meta.st_mode): raise ValueError
        elif not stat.S_ISREG(meta.st_mode): raise ValueError
        if private and (meta.st_uid != os.geteuid() or stat.S_IMODE(meta.st_mode) != (0o700 if directory else 0o600)):
            raise ValueError
        if not private and (meta.st_uid not in (0,os.geteuid()) or meta.st_mode & 0o022): raise ValueError
        return fd
    except BaseException:
        os.close(fd)
        raise


def _bytes(path, *, private=True, limit=8 * 1024 * 1024):
    fd = _open(path, private=private)
    with os.fdopen(fd, 'rb') as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit: raise ValueError
    return raw


def _hash(raw): return hashlib.sha256(raw).hexdigest()


def _file_digest(path):
    fd = _open(path)
    digest = hashlib.sha256(); size = 0
    with os.fdopen(fd,'rb') as stream:
        while True:
            chunk=stream.read(1024*1024)
            if not chunk:break
            size+=len(chunk)
            if size>512*1024*1024:raise ValueError
            digest.update(chunk)
    return digest.hexdigest()


def _runtime_files(source):
    scripts=source/'skills/agent-harness/buzz-agent-setup/scripts'
    result=set()
    for path in scripts.rglob('*'):
        if path.is_symlink():raise ValueError
        if path.is_file() and '__pycache__' not in path.parts and path.suffix!='.pyc':
            result.add(path)
    return result


def _git_check(source,revision,runner):
    # An archive or linked worktree must never borrow an enclosing repository.
    # Prove this candidate owns a private real Git directory before any command.
    _private_directory(source / '.git')
    env={'PATH':'/usr/bin:/bin','LC_ALL':'C','GIT_CONFIG_GLOBAL':'/dev/null','GIT_CONFIG_NOSYSTEM':'1','GIT_OPTIONAL_LOCKS':'0'}
    for args,expected in [(['rev-parse','--verify','HEAD'],revision),(['status','--porcelain','--untracked-files=all'],'')]:
        result=runner(['git','-C',str(source),*args],env=env,capture_output=True,text=True,timeout=5,check=False)
        if result.returncode!=0 or result.stdout.strip()!=expected:raise ValueError



def _json(raw):
    def unique(pairs):
        result = {}
        for k, v in pairs:
            if k in result: raise ValueError
            result[k] = v
        return result
    value = json.loads(raw, object_pairs_hook=unique)
    if not isinstance(value, dict): raise ValueError
    return value


def _private_directory(path):
    fd = _open(path, directory=True, private=True)
    os.close(fd)


def _inside(path, root):
    path = _path(path)
    if not path.is_relative_to(root) or path == root: raise ValueError
    current = root
    _private_directory(current)
    for part in path.relative_to(root).parts[:-1]:
        current /= part
        _private_directory(current)
    return path


def _loopback_url(value, scheme):
    from urllib.parse import urlsplit
    if not isinstance(value, str): raise ValueError
    parsed = urlsplit(value)
    if (parsed.scheme != scheme or parsed.hostname != '127.0.0.1' or parsed.username is not None
            or parsed.password is not None or parsed.path or parsed.query or parsed.fragment
            or parsed.port is None or not 1024 <= parsed.port <= 65535
            or value != f'{scheme}://127.0.0.1:{parsed.port}'):
        raise ValueError
    return parsed.port


def _local_mode(doc, expected_local_app_ids):
    """An explicit v2 scope needs a separate reviewer pin, never a inferred subset."""
    if type(doc['version']) is not int: raise ValueError
    if doc['version'] == 1:
        return 'THREE_LOCAL', tuple(sorted(APPS)), None
    if (doc['version'] != 2 or doc.get('mode') != 'TWO_LOCAL'
            or doc.get('local_app_ids') != list(TWO_LOCAL)
            or not isinstance(expected_local_app_ids, (list, tuple))
            or tuple(expected_local_app_ids) != TWO_LOCAL):
        raise ValueError
    return 'TWO_LOCAL', TWO_LOCAL, tuple(expected_local_app_ids)


def _inventory(root, *, executable):
    """Inspect one protected tree without following symlinks."""
    result = []
    for path in sorted(root.rglob('*')):
        if path.is_symlink(): raise ValueError
        if path.is_dir():
            _private_directory(path); result.append((path, 'directory'))
        else:
            fd = _open(path, private=path != executable)
            os.close(fd); result.append((path, 'file'))
    return tuple(result)


def _local_material_inventory(run, cfg):
    """Local native profiles have an exact closure; auxiliary plans pin their own files.

    Scenario/provider/user files may be added after packaging. They cannot become
    local native actors without changing the already pinned cfg/catalog/envs.
    """
    result = []
    for agent in cfg['agents'].values():
        config = _inside(agent['lark_config_dir'], run)
        data = _inside(agent['lark_data_dir'], run)
        for root, files, dirs in (
                (config, {config / 'config.json'}, set()),
                (data, {data / 'lark-cli/master.key', data / f"lark-cli/appsecret_{agent['app_id']}.enc"}, {data / 'lark-cli'})):
            _private_directory(root)
            inventory = _inventory(root, executable=None)
            if ({p for p, kind in inventory if kind == 'file'} != files
                    or {p for p, kind in inventory if kind == 'directory'} != dirs): raise ValueError
            result.append((root, 'directory')); result.extend(inventory)
    return tuple(sorted(result))


def _manifest_snapshot(run, snapshots):
    """Find the original hash-pinned admission document, regardless of basename.

    Derived mode/path fields cannot decide whether the original v2 contract is
    rechecked. Other root inputs (groups, CLI) are not admission documents.
    """
    candidates = []
    identity_keys = {'run_id', 'run_dir', 'source_root', 'source_hashes',
                     'fixture_sha256', 'initial_binding', 'onboarding_config'}
    for path, expected in snapshots:
        if path.parent != run: continue
        raw = _bytes(path)
        if _hash(raw) != expected: raise ValueError
        try:
            doc = _json(raw)
        except ValueError:
            continue
        if identity_keys.issubset(doc) and doc['run_dir'] == str(run):
            candidates.append((path, doc))
    if len(candidates) != 1: raise ValueError
    return candidates[0]


@dataclass(frozen=True)
class PreparedRun:
    run_id: str
    run_dir: Path
    source_root: Path
    revision: str
    python: str
    onboarding_config: Path
    trust_bundle: Path
    initial_binding: str
    _snapshots: tuple[tuple[Path, str], ...] = field(repr=False)
    _directories: tuple[Path, ...] = field(repr=False)
    _source_hashes: tuple[tuple[Path, str], ...] = field(repr=False)
    _outputs: tuple[Path, ...] = field(repr=False)
    _command_runner: object = field(repr=False,compare=False)
    mode: str = 'THREE_LOCAL'
    local_app_ids: tuple[str, ...] = tuple(sorted(APPS))
    _expected_local_app_ids: tuple[str, ...] | None = field(default=None, repr=False)
    _inventory: tuple = field(default=(), repr=False)
    _manifest_path: Path | None = field(default=None, repr=False)
    _sdk_evidence: object = field(default=None,repr=False,compare=False)

    @classmethod
    def check(cls, manifest_path, *, reviewed_source, reviewed_revision, fixture_sha256,
              command_runner=subprocess.run, expected_local_app_ids=None,
              sdk_evidence_config=None,sdk_evidence_config_sha256=None):
        """Pure offline admission. Pins supplied by the reviewer cannot come from this manifest."""
        try:
            path = _path(manifest_path)
            raw = _bytes(path); doc = _json(raw)
            expected = {'version','run_id','run_dir','source_root','revision','source_hashes','fixture_path',
                        'fixture_sha256','initial_binding','onboarding_config','buzz_cli','buzz_sha256','python',
                        'relay_url','people_url','trust_bundle'}
            mode, local_apps, local_pin = _local_mode(doc, expected_local_app_ids)
            if mode == 'TWO_LOCAL': expected |= {'mode', 'local_app_ids'}
            if set(doc) != expected: raise ValueError
            if not re.fullmatch(r'[a-f0-9]{32}', doc['run_id']): raise ValueError
            run = _path(doc['run_dir']); _private_directory(run)
            if run.name != 'run-' + doc['run_id'] or path.parent != run: raise ValueError
            source = _path(doc['source_root'])
            if source != _path(reviewed_source) or source.is_relative_to(run) or run.is_relative_to(source): raise ValueError
            fd = _open(source, directory=True); os.close(fd)
            if not re.fullmatch(r'[a-f0-9]{40}', reviewed_revision) or doc['revision'] != reviewed_revision: raise ValueError
            # Only repository metadata; no inherited HOME config, credential helpers or network.
            _git_check(source,reviewed_revision,command_runner)
            if (source / '.git').is_file(): raise ValueError  # linked worktree is not an installed candidate
            hashes = doc['source_hashes']
            if not isinstance(hashes, dict) or not hashes: raise ValueError
            source_hashes = []
            for rel, expected_hash in hashes.items():
                if (not isinstance(rel, str) or Path(rel).is_absolute() or '..' in Path(rel).parts
                        or not re.fullmatch(r'[a-f0-9]{64}', expected_hash)): raise ValueError
                file = source / rel
                if _hash(_bytes(file, private=False)) != expected_hash: raise ValueError
                source_hashes.append((file, expected_hash))
            entry = source / 'skills/agent-harness/buzz-agent-setup/scripts/hostd/__main__.py'
            if entry not in dict(source_hashes) or set(dict(source_hashes)) != _runtime_files(source): raise ValueError
            snapshots = [(path, _hash(raw))]
            directories = {run}
            def read(file):
                file = _inside(file, run)
                value = _bytes(file)
                snapshots.append((file, _hash(value)))
                directories.update(file.parents[:len(file.relative_to(run).parts)-1])
                return value
            def directory(file):
                file = _inside(file, run); _private_directory(file); directories.add(file)
                directories.update(file.parents[:len(file.relative_to(run).parts)-1])
                return file
            def optional_path(value):
                file=_inside(value,run)
                if os.path.lexists(file):read(file)
                return file
            def env_file(value):
                env=join.parse_env(read(value).decode())
                if env.get('BUZZ_RELAY_URL')!=doc['relay_url']:raise ValueError
                for key in ('BUZZ_ACP_SYSTEM_PROMPT_FILE','BUZZ_RESPONSIBLE_CONFIG','BUZZ_ACP_WORK_DIR',
                            'LARKSUITE_CLI_CONFIG_DIR','LARKSUITE_CLI_DATA_DIR'):
                    if key in env:
                        if key.endswith('_DIR'):directory(env[key])
                        else:read(env[key])
                if env.get('BUZZ_RESPONSIBLE_CONFIG'):
                    responsible=_json(read(env['BUZZ_RESPONSIBLE_CONFIG']))
                    if 'people_file' in responsible:optional_path(responsible['people_file'])
                return env
            fixture_path = _inside(doc['fixture_path'], run); fixture_raw = read(fixture_path)
            if (not re.fullmatch(r'[a-f0-9]{64}', fixture_sha256) or doc['fixture_sha256'] != fixture_sha256
                    or _hash(fixture_raw) != fixture_sha256): raise ValueError
            groups = _json(fixture_raw)
            if set(groups) != {'hostd-test-群X','hostd-test-群W-未绑定'}: raise ValueError
            if len(set(groups.values())) != 2 or any(not isinstance(v,str) or not re.fullmatch(r'oc_[A-Za-z0-9]+',v) for v in groups.values()): raise ValueError
            if doc['initial_binding'] != 'hostd-local-l3': raise ValueError
            relay_port = _loopback_url(doc['relay_url'], 'wss')
            if _loopback_url(doc['people_url'], 'https') != relay_port: raise ValueError
            home = directory(run / 'home'); runtime = directory(run / 'runtime')
            registry = directory(home / '.config/buzz-feishu-sync')
            binding = directory(registry / doc['initial_binding'])
            cfg = validate_config(_json(read(binding / 'config.json')))
            if (cfg['chat_id'] != groups['hostd-test-群X'] or cfg['people_api']['base_url'] != doc['people_url']
                    or cfg.get('identity','union_id') != 'union_id' or cfg.get('reaction_sync','two_way') != 'two_way'
                    or {a['app_id'] for a in cfg['agents'].values()} != set(local_apps)
                    or cfg['agents'][cfg['desk_pubkey']]['app_id'] != 'cli_aa48bbeeba38dbcf'
                    or cfg['owner_app_id'] != 'cli_a940faa4ec381bc4'
                    or cfg['buzz_cli'] != doc['buzz_cli'] or cfg['buzz_cli_sha256'] != doc['buzz_sha256']): raise ValueError
            env_file(cfg['mirror_env_file'])
            env_file(cfg['people_api']['signer_env_file'])
            if 'people_cache_file' in cfg:optional_path(cfg['people_cache_file'])
            for agent in cfg['agents'].values():
                config_dir = directory(agent['lark_config_dir']); directory(agent['lark_data_dir'])
                profile = _json(read(config_dir / 'config.json'))
                if not isinstance(profile.get('apps'), list) or len(profile['apps']) != 1 or profile['apps'][0].get('appId') != agent['app_id']: raise ValueError
                if mode == 'TWO_LOCAL':
                    data = Path(agent['lark_data_dir']) / 'lark-cli'
                    directory(data)
                    read(data / 'master.key'); read(data / f"appsecret_{agent['app_id']}.enc")
            buzz = _inside(doc['buzz_cli'], run)
            # Native config validation already proves raw ELF, exact release path and SHA.
            fd = _open(buzz); os.close(fd)
            source_hashes.append((buzz,doc['buzz_sha256']))
            onboarding = _inside(doc['onboarding_config'], run)
            rt = _json(read(onboarding))
            keys = {'version','owner_env_file','relay_url','relay_pubkey','template_config','binding_dir','legacy_join_path','catalog_path','trusted_relays'}
            if (set(rt) != keys or type(rt['version']) is not int or rt['version'] != 1
                    or rt['relay_url'] != doc['relay_url'] or rt['binding_dir'] != str(registry)
                    or rt['trusted_relays'] != [doc['relay_url']] or not re.fullmatch(r'[a-f0-9]{64}', rt['relay_pubkey'])): raise ValueError
            env_file(rt['owner_env_file'])
            template = validate_config(_json(read(rt['template_config'])))
            if template != cfg: raise ValueError  # fixed initial scope; root may propose a separate reviewed template later
            catalog = _json(read(rt['catalog_path'])); legacy = _json(read(rt['legacy_join_path']))
            join.validate_config(catalog)
            if legacy.get('agents') != []: raise ValueError
            join.validate_config(dict(legacy,agents=catalog['agents']))
            if catalog['owner_pubkey'] != legacy['owner_pubkey']: raise ValueError
            for listing in (catalog,legacy):
                if listing['buzz'] != {'cli_path':str(buzz),'cli_sha256':doc['buzz_sha256']}: raise ValueError
                directory(listing['state_dir'])
                if listing['lark_cli'] != cfg['lark_cli']: raise ValueError
                _inside(listing['lark_cli'],run)
            for agent in catalog['agents']:
                if doc['run_id'] not in agent['unit']: raise ValueError
                env_file(agent['env_file'])
                if 'log_file' in agent:optional_path(agent['log_file'])
                block = agent.get('feishu')
                if not isinstance(block,dict) or block['app_id'] not in set(local_apps): raise ValueError
                for key in ('lark_config_dir','lark_data_dir'): directory(block[key])
                profile = _json(read(Path(block['lark_config_dir'])/'config.json'))
                if profile.get('apps') != [{'appId':block['app_id']}]:
                    # Real profile metadata may include additional fields, never require a secret value.
                    apps=profile.get('apps')
                    if not isinstance(apps,list) or len(apps)!=1 or not isinstance(apps[0],dict) or apps[0].get('appId')!=block['app_id']: raise ValueError
            if mode == 'TWO_LOCAL':
                import buzz_feishu_group_sync as gs
                import recovery_authority as authority
                owner = catalog['owner_pubkey']
                owner_env = env_file(rt['owner_env_file'])
                if (gs._signer_pubkey(gs.secret_hex(owner_env.get('BUZZ_PRIVATE_KEY'), 'fixture owner')) != owner
                        or rt['owner_env_file'] != cfg['people_api']['signer_env_file']
                        or len(cfg['agents']) != 2 or len(catalog['agents']) != 2): raise ValueError
                profiles = [a[key] for a in cfg['agents'].values() for key in ('lark_config_dir', 'lark_data_dir')]
                if len(set(profiles)) != len(profiles): raise ValueError
                seen = set()
                for row in catalog['agents']:
                    environment = env_file(row['env_file'])
                    pub = gs._signer_pubkey(gs.secret_hex(environment.get('BUZZ_PRIVATE_KEY'), 'fixture agent'))
                    auth = json.loads(environment.get('BUZZ_AUTH_TAG', 'null'))
                    if (pub in seen or cfg['agents'].get(pub) != row['feishu']
                            or environment.get('BUZZ_ACP_AGENT_OWNER') != owner
                            or join._allowlist(environment.get('BUZZ_ACP_CHANNELS', '')) != [cfg['channel_id']]
                            or not isinstance(auth, list) or len(auth) != 4 or auth[2]
                            or authority.attested_owner({'tags': [auth]}, pub) != owner
                            or any(key not in environment for key in ('BUZZ_ACP_SYSTEM_PROMPT_FILE', 'BUZZ_RESPONSIBLE_CONFIG'))):
                        raise ValueError
                    seen.add(pub)
                if seen != set(cfg['agents']): raise ValueError
                mirror = env_file(cfg['mirror_env_file'])
                auth = json.loads(mirror.get('BUZZ_AUTH_TAG', 'null'))
                if (gs._signer_pubkey(gs.secret_hex(mirror.get('BUZZ_PRIVATE_KEY'), 'fixture mirror')) != cfg['mirror_pubkey']
                        or not isinstance(auth, list) or len(auth) != 4 or auth[2]
                        or authority.attested_owner({'tags': [auth]}, cfg['mirror_pubkey']) != owner): raise ValueError
                read(cfg['lark_cli'])
            trust = _inside(doc['trust_bundle'],run); read(trust)
            context = ssl.create_default_context(cafile=str(trust))
            if not set(ssl.create_default_context().get_ca_certs(binary_form=True)).issubset(set(context.get_ca_certs(binary_form=True))): raise ValueError
            python = _path(doc['python'])
            fd = _open(python); meta = os.fstat(fd); os.close(fd)
            if not os.access(python,os.X_OK) or meta.st_uid not in (0,os.geteuid()) or meta.st_mode & 0o022: raise ValueError
            evidence=None
            if (sdk_evidence_config is None)!=(sdk_evidence_config_sha256 is None):raise ValueError
            if sdk_evidence_config is not None:
                from hostd.sdk_evidence import EvidenceConfig
                evidence=EvidenceConfig.check(_inside(sdk_evidence_config,run),sdk_evidence_config_sha256)
                if evidence.run_id!=doc['run_id'] or not set(dict(evidence.apps).values()).issubset(local_apps):raise ValueError
                for source_path,source_pin,_ in evidence._sources:
                    if dict(source_hashes).get(source/'skills/agent-harness/buzz-agent-setup/scripts/hostd'/source_path.name)!=source_pin:raise ValueError
                read(evidence.path)
                for output in (evidence.events_path,evidence.receipt_path):
                    _inside(output,runtime);directory(output.parent)
            outputs = (runtime/'hostd.sqlite3',runtime/'status.json',runtime/'console',runtime/'hostd-process.json',binding/'state')
            if evidence is not None:outputs+=(evidence.events_path,evidence.receipt_path)
            if any(os.path.lexists(p) for p in outputs): raise ValueError
            inventory = ()
            if mode == 'TWO_LOCAL':
                inventory = _local_material_inventory(run, cfg)
            return cls(doc['run_id'],run,source,reviewed_revision,str(python),onboarding,trust,doc['initial_binding'],
                       tuple(sorted(set(snapshots))),tuple(sorted(directories)),tuple(sorted(set(source_hashes))),outputs,command_runner,
                       mode,local_apps,local_pin,inventory,path if mode == 'TWO_LOCAL' else None,evidence)
        except Exception:
            raise PreparationError(NOTICE) from None

    def revalidate(self):
        try:
            manifest, doc = _manifest_snapshot(self.run_dir, self._snapshots)
            mode, apps, pin = _local_mode(doc, self._expected_local_app_ids)
            if (mode, apps, pin) != (self.mode, self.local_app_ids, self._expected_local_app_ids): raise ValueError
            if mode == 'TWO_LOCAL':
                if self._manifest_path != manifest: raise ValueError
                runtime = _json(_bytes(self.onboarding_config))
                cfg = _json(_bytes(_inside(runtime['template_config'], self.run_dir)))
                if not self._inventory or _local_material_inventory(self.run_dir, cfg) != self._inventory: raise ValueError
            _git_check(self.source_root,self.revision,self._command_runner)
            if _runtime_files(self.source_root) != {path for path,_ in self._source_hashes if path.is_relative_to(self.source_root)}:raise ValueError
            if self._sdk_evidence is not None:self._sdk_evidence.revalidate(fresh=True)
            for path in self._directories: _private_directory(path)
            for path,expected in self._snapshots:
                if _hash(_bytes(path)) != expected: raise ValueError
            for path,expected in self._source_hashes:
                if _file_digest(path) != expected: raise ValueError
            if any(os.path.lexists(path) for path in self._outputs): raise ValueError
        except Exception:
            raise PreparationError(NOTICE) from None

    def readback(self):
        return {'status':'prepared','live_verified':False,'app_count':len(self.local_app_ids),'chat_count':2}

    def argv(self):
        runtime = self.run_dir / 'runtime'
        argv=[self.python,'-m','hostd','run','--only',self.initial_binding,
                '--onboarding-config',str(self.onboarding_config),'--state-db',str(runtime/'hostd.sqlite3'),
                '--status-file',str(runtime/'status.json'),'--console-dir',str(runtime/'console')]
        if self._sdk_evidence is not None:argv.extend(['--sdk-evidence-config',str(self._sdk_evidence.path),'--sdk-evidence-config-sha256',self._sdk_evidence.sha256])
        return argv

    def environment(self):
        return {'HOME':str(self.run_dir/'home'),'PATH':f'{Path(NODE).parent}:/usr/bin:/bin',
                'PYTHONPATH':str(self.source_root/'skills/agent-harness/buzz-agent-setup/scripts'),
                'SSL_CERT_FILE':str(self.trust_bundle),'HOSTD_NODE_BINARY':NODE,'HOSTD_LARK_CLI_ENTRY':CLI_ENTRY,
                'LC_ALL':'C.UTF-8','PYTHONUNBUFFERED':'1','PYTHONDONTWRITEBYTECODE':'1'}


def _group_members(pgid):
    """Kernel process metadata only; inspect this session's members, never their environment."""
    members=[]
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():continue
        pid=int(entry.name)
        try:
            if os.getpgid(pid)==pgid and os.getsid(pid)==pgid:members.append(pid)
        except ProcessLookupError:pass
    return members


class OwnedHostd:
    """Own the exact Popen handle/session; no attaching to arbitrary saved PIDs."""
    def __init__(self,plan):
        self.plan=plan;self.process=None;self._reaped=False;self._children_reaped=False;self._lock=threading.RLock()

    def start(self):
        with self._lock:
            if self.process is not None: raise PreparationError(NOTICE)
            self.plan.revalidate()
            try:
                self.process=subprocess.Popen(self.plan.argv(),env=self.plan.environment(),cwd=self.plan.source_root,
                    stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True,
                    close_fds=True)
            except Exception:
                raise PreparationError(NOTICE) from None
            return self.readback()

    def readback(self):
        with self._lock:
            if self.process is None:return {'status':'not_started','live_verified':False,'reaped':False,'children_reaped':False}
            exited=self.process.poll() is not None
            return {'status':('stopped' if self._reaped and self._children_reaped else 'exited') if exited else 'running',
                    'live_verified':False,'reaped':self._reaped,'children_reaped':self._children_reaped}

    def stop(self,*,timeout=5):
        with self._lock:
            if type(timeout) not in (int,float) or not 0 < timeout <= 30: raise PreparationError(NOTICE)
            if self.process is None:return self.readback()
            try:
                if self.process.poll() is None:
                    # Session ID == leader PID is guaranteed by our own start_new_session.
                    if os.getpgid(self.process.pid) != self.process.pid: raise ValueError
                    os.killpg(self.process.pid,signal.SIGTERM)
                try:self.process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    if os.getpgid(self.process.pid) != self.process.pid: raise ValueError
                    os.killpg(self.process.pid,signal.SIGKILL);self.process.wait(timeout=timeout)
                self._reaped=True
                # The real daemon's SDK children share our new session. A reaped
                # leader alone cannot prove those children exited.
                remaining=_group_members(self.process.pid)
                if remaining:
                    try:os.killpg(self.process.pid,signal.SIGKILL)
                    except ProcessLookupError:pass
                deadline=time.monotonic()+timeout
                while _group_members(self.process.pid):
                    if time.monotonic()>=deadline:raise ValueError
                    time.sleep(.01)
                self._children_reaped=True
            except Exception:
                raise PreparationError(NOTICE) from None
            return self.readback()


class _Parser(argparse.ArgumentParser):
    def error(self,message):raise PreparationError(NOTICE)


def main(argv=None):
    parser=_Parser(description=__doc__)
    parser.add_argument('--check',action='store_true',required=True)
    parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--reviewed-source',type=Path,required=True)
    parser.add_argument('--reviewed-revision',required=True)
    parser.add_argument('--fixture-sha256',required=True)
    try:
        args=parser.parse_args(argv)
        plan=PreparedRun.check(args.manifest,reviewed_source=args.reviewed_source,
            reviewed_revision=args.reviewed_revision,fixture_sha256=args.fixture_sha256)
        print(json.dumps(plan.readback(),sort_keys=True));return 0
    except PreparationError:
        print(NOTICE,file=sys.stderr);return 1

if __name__=='__main__':sys.exit(main())
