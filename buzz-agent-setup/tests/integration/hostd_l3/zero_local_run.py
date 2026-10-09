"""Reviewed private support for an independent normal zero-local Hostd run.

No daemon, authority or testing API is provided here. Admission is read-only;
normal production Hostd alone writes runtime Store rows after signed checks.
"""
from __future__ import annotations

from contextlib import closing
import asyncio
from dataclasses import dataclass, field
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import re
import signal
import sqlite3
import ssl
import stat
import subprocess
import sys
import time

NOTICE = ('独立本机运行尚未安全确认，请求未开始或仍待核验。怎么解决：检查冻结源码、独立 owner、relay、受保护应用配置、空绑定目录和本次运行的原进程；保留记录后重新核验。'
          '\n复制给 AI：帮我检查 hostd 独立零绑定运行的输入固定值、只读数据库与原进程会话清理；不要输出密钥、应用凭据、正文或任意原始错误。')
KEYS = frozenset('version run_id run_dir source_root revision source_hashes python home runtime_dir registry_root state_db status_file console_dir onboarding_config owner_env_file relay_url relay_pubkey trusted_relays template_config binding_dir legacy_join_path catalog_path trust_bundle node_binary lark_cli_entry selector channel_id source_owner_pubkey agent_pubkeys app_profiles input_sha256'.split())
PATHS = frozenset('home runtime_dir registry_root state_db status_file console_dir onboarding_config owner_env_file template_config binding_dir legacy_join_path catalog_path trust_bundle node_binary lark_cli_entry'.split())
HEX64 = re.compile(r'[0-9a-f]{64}\Z')
SCRIPTS_RELATIVE = Path('skills/agent-harness/buzz-agent-setup/scripts')


class ZeroLocalRunError(ValueError):
    def __init__(self):
        super().__init__(NOTICE)


def _require(condition):
    if not condition:
        raise ZeroLocalRunError()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result)
        result[key] = value
    return result


def _json(raw):
    return json.loads(raw, object_pairs_hook=_unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(ZeroLocalRunError()))


def _absolute(value):
    _require(isinstance(value, str) and value and Path(value).is_absolute())
    _require(not any(ord(c) < 32 or ord(c) == 127 for c in value))
    _require(all(part not in ('.', '..') for part in value.split('/')[1:]))
    return Path(value)


def _relative(value):
    _require(isinstance(value, str) and value and not Path(value).is_absolute())
    _require(all(part not in ('', '.', '..') for part in value.split('/')))
    _require(not any(ord(c) < 32 or ord(c) == 127 for c in value))
    return Path(value)


def _under(path, root):
    _require(path != root and path.is_relative_to(root))
    return path


def _directory(path, *, private=False):
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            os.close(fd)
            fd = child
        meta = os.fstat(fd)
        if private:
            _require(meta.st_uid == os.geteuid() and stat.S_IMODE(meta.st_mode) == 0o700)
        else:
            _require(meta.st_uid in (0, os.geteuid()))
        return (meta.st_dev, meta.st_ino, meta.st_uid, stat.S_IMODE(meta.st_mode))
    finally:
        os.close(fd)


def _read(path, *, mode=None, source=False, limit=32*1024*1024):
    """One nofollow FD; unchanged stat before/after bounded reads."""
    parent = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    leaf = None
    try:
        for part in path.parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
            os.close(parent)
            parent = child
        leaf = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent)
        meta = os.fstat(leaf)
        _require(stat.S_ISREG(meta.st_mode) and meta.st_size <= limit)
        _require(meta.st_uid in (0, os.geteuid()) if source else meta.st_uid == os.geteuid())
        if mode is not None:
            _require(stat.S_IMODE(meta.st_mode) == mode)
        chunks, length = [], 0
        while True:
            chunk = os.read(leaf, 65536)
            if not chunk:
                break
            chunks.append(chunk)
            length += len(chunk)
            _require(length <= limit)
        final = os.fstat(leaf)
        signature = lambda m: (m.st_dev, m.st_ino, m.st_uid, m.st_mode, m.st_size, m.st_mtime_ns, m.st_ctime_ns)
        _require(signature(meta) == signature(final))
        return b''.join(chunks), signature(meta)
    finally:
        if leaf is not None:
            os.close(leaf)
        os.close(parent)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _proc(pid):
    try:
        path = Path('/proc')/str(pid)
        text = (path/'stat').read_text()
        fields = text[text.rindex(')')+2:].split()
        return (pid, int(fields[1]), int(fields[3]), int(fields[19]), path.stat().st_uid)
    except (FileNotFoundError, ProcessLookupError):
        return None


def _session(sid):
    rows = {}
    for entry in Path('/proc').iterdir():
        if entry.name.isdigit():
            row = _proc(int(entry.name))
            if row is not None and row[2] == sid:
                rows[row[0]] = row
    return rows


def _identity(row):
    return (row[0], row[2], row[3], row[4])


def _signal_original(row, sig):
    """Linux pidfd prevents a reused PID from receiving an owned signal."""
    if row is None:
        return
    _require(hasattr(os, 'pidfd_open') and hasattr(signal, 'pidfd_send_signal'))
    try:
        fd = os.pidfd_open(row[0], 0)
    except ProcessLookupError:
        return
    try:
        current = _proc(row[0])
        if current is not None and _identity(current) == _identity(row):
            try:
                signal.pidfd_send_signal(fd, sig)
            except ProcessLookupError:
                pass
    finally:
        os.close(fd)


@dataclass(frozen=True)
class ZeroLocalRunPlan:
    _manifest_path: Path = field(repr=False)
    _manifest_bytes: bytes = field(repr=False)
    _manifest_metadata: tuple = field(repr=False)
    _reviewed_source: Path = field(repr=False)
    _revision: str
    _source_pins: tuple = field(repr=False)
    _input_metadata: tuple = field(repr=False)
    _directory_metadata: tuple = field(repr=False)
    _owner: str
    _source_owner: str
    _relay_pin: str
    _channel: str
    _agents: tuple
    _profiles_json: str = field(repr=False)
    _command_runner: object = field(repr=False, compare=False)
    _sdk_evidence: object = field(default=None,repr=False,compare=False)

    @classmethod
    def check(cls, manifest_path, *, reviewed_source, reviewed_revision,
              reviewed_source_hashes, expected_host_b_owner, expected_relay_pubkey,
              expected_channel_id, expected_source_owner_pubkey,
              expected_agent_pubkeys, expected_app_profiles, command_runner=subprocess.run,
              sdk_evidence_config=None,sdk_evidence_config_sha256=None):
        try:
            manifest_path = _absolute(str(manifest_path))
            raw, metadata = _read(manifest_path, mode=0o600)
            data = _json(raw)
            _require(isinstance(data, dict) and set(data) == KEYS)
            _require(type(data['version']) is int and data['version'] == 1)
            root = _absolute(data['run_dir'])
            _require(re.fullmatch('[0-9a-f]{32}', data['run_id']) is not None)
            _require(root.name == 'run-'+data['run_id'] and manifest_path == root/'manifest.json')
            _directory(root, private=True)
            source = _absolute(str(reviewed_source))
            _require(data['source_root'] == str(source))
            _require(isinstance(reviewed_revision, str) and re.fullmatch('[0-9a-f]{40}', reviewed_revision))
            _require(data['revision'] == reviewed_revision)
            _require(isinstance(reviewed_source_hashes, dict) and bool(reviewed_source_hashes))
            _require(data['source_hashes'] == reviewed_source_hashes)
            for name, pin in reviewed_source_hashes.items():
                relative = _relative(name)
                _require(relative.is_relative_to(SCRIPTS_RELATIVE) and relative.suffix == '.py')
                _require(isinstance(pin, str) and HEX64.fullmatch(pin))
            for pin in (expected_host_b_owner, expected_relay_pubkey, expected_source_owner_pubkey):
                _require(isinstance(pin, str) and HEX64.fullmatch(pin))
            _require(expected_source_owner_pubkey != expected_host_b_owner)
            _require(data['source_owner_pubkey'] == expected_source_owner_pubkey)
            _require(data['relay_pubkey'] == expected_relay_pubkey and data['channel_id'] == expected_channel_id)
            _require(isinstance(expected_channel_id, str) and re.fullmatch('[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}', expected_channel_id))
            _require(isinstance(expected_agent_pubkeys, (list, tuple)) and bool(expected_agent_pubkeys))
            agents = tuple(sorted(expected_agent_pubkeys))
            _require(len(set(agents)) == len(agents) and all(isinstance(v, str) and HEX64.fullmatch(v) for v in agents))
            _require(isinstance(data['agent_pubkeys'], list) and tuple(sorted(data['agent_pubkeys'])) == agents)
            _require(isinstance(expected_app_profiles, dict) and bool(expected_app_profiles))
            _require(data['app_profiles'] == expected_app_profiles)
            for app, profile in expected_app_profiles.items():
                _require(isinstance(app, str) and re.fullmatch('cli_[A-Za-z0-9]+', app))
                _require(isinstance(profile, dict) and set(profile) == {'profile', 'config_dir', 'data_dir'})
                _require(isinstance(profile['profile'], str) and bool(profile['profile']))
                _under(_absolute(profile['config_dir']), root)
                _under(_absolute(profile['data_dir']), root)
            for name in PATHS:
                _under(_absolute(data[name]), root)
            _require(data['python'] == str(Path(sys.executable).absolute()) and os.access(data['python'], os.X_OK))
            _require(data['selector'] == 'hostd-msg003-none-'+data['run_id'])
            _require(_absolute(data['registry_root']) == _absolute(data['home'])/'.config'/'buzz-feishu-sync')
            runtime = _absolute(data['runtime_dir'])
            _require(_absolute(data['state_db']) == runtime/'hostd.sqlite3')
            _require(_absolute(data['status_file']) == runtime/'status.json')
            _require(_absolute(data['console_dir']) == runtime/'console')
            _require(isinstance(data['trusted_relays'], list) and data['trusted_relays'] == [data['relay_url']])
            _require(isinstance(data['input_sha256'], dict) and bool(data['input_sha256']))
            input_metadata = []
            for name, pin in sorted(data['input_sha256'].items()):
                relative = _relative(name)
                _require(relative != Path('manifest.json') and isinstance(pin, str) and HEX64.fullmatch(pin))
                path = _under(root/relative, root)
                file_raw, file_meta = _read(path, mode=0o700 if path == Path(data['node_binary']) else 0o600,
                                        limit=128*1024*1024 if path == Path(data['node_binary']) else 32*1024*1024)
                _require(_sha(file_raw) == pin)
                input_metadata.append((name, file_meta))
            required = {data[name] for name in ('onboarding_config', 'owner_env_file', 'template_config', 'legacy_join_path', 'catalog_path', 'trust_bundle', 'node_binary', 'lark_cli_entry')}
            _require(required.issubset({str(root/Path(name)) for name in data['input_sha256']}))
            _require(os.access(data['node_binary'], os.X_OK))
            ssl.create_default_context(cadata=_read(Path(data['trust_bundle']), mode=0o600)[0].decode('ascii'))
            directories = [(str(root), _directory(root, private=True))]
            for path in sorted(root.rglob('*')):
                _require(not path.is_symlink())
                if path.is_dir():
                    directories.append((str(path), _directory(path, private=True)))
            evidence=None
            if (sdk_evidence_config is None)!=(sdk_evidence_config_sha256 is None):raise ValueError
            if sdk_evidence_config is not None:
                from hostd.sdk_evidence import EvidenceConfig
                evidence=EvidenceConfig.check(_under(_absolute(os.fspath(sdk_evidence_config)),root),sdk_evidence_config_sha256)
                _require(evidence.run_id==data['run_id'] and set(dict(evidence.apps).values()).issubset(expected_app_profiles))
                for source_path,source_pin,_ in evidence._sources:
                    _require(reviewed_source_hashes.get(str(SCRIPTS_RELATIVE/'hostd'/source_path.name))==source_pin)
                for output in (evidence.events_path,evidence.receipt_path):_under(output,runtime)
            plan = cls(manifest_path, raw, metadata, source, reviewed_revision,
                       tuple(sorted(reviewed_source_hashes.items())), tuple(input_metadata),
                       tuple(directories), expected_host_b_owner, expected_source_owner_pubkey,
                       expected_relay_pubkey, expected_channel_id, agents,
                       json.dumps(expected_app_profiles, sort_keys=True), command_runner,evidence)
            plan.revalidate(phase='prepared')
            return plan
        except Exception:
            raise ZeroLocalRunError() from None

    def _data(self):
        return _json(self._manifest_bytes)

    def _sources(self):
        _directory(self._reviewed_source)
        scripts = self._reviewed_source/SCRIPTS_RELATIVE
        actual = {str(p.relative_to(self._reviewed_source)) for p in scripts.rglob('*.py')}
        expected = dict(self._source_pins)
        _require(actual == set(expected))
        for name, pin in self._source_pins:
            raw, _ = _read(self._reviewed_source/_relative(name), source=True)
            _require(_sha(raw) == pin)
        result = self._command_runner(['git', '-C', str(self._reviewed_source), 'rev-parse', 'HEAD'],
            capture_output=True, text=True, check=False, timeout=5,
            env={'PATH': os.defpath, 'LANG': 'C.UTF-8'})
        _require(result.returncode == 0 and result.stdout.strip() == self._revision)

    def _runtime_path_allowed(self, path):
        data = self._data()
        runtime = Path(data['runtime_dir'])
        state = Path(data['home'])/'.local'/'state'/'buzz-hostd'
        if self._sdk_evidence is not None and path in (self._sdk_evidence.events_path,self._sdk_evidence.receipt_path):return 'file'
        if path == Path(data['home'])/'.local' or path == state.parent or path == state:
            return 'directory'
        if path == state/'app-locks':
            return 'directory'
        if path.parent == state/'app-locks' and re.fullmatch(r'[0-9a-f]{64}\.lock', path.name):
            return 'file'
        if path == runtime/'console':
            return 'directory'
        if path.parent == runtime/'console' and path.name in ('console.sock', 'console.token'):
            return 'socket' if path.name == 'console.sock' else 'file'
        if path.parent == runtime and (path.name in ('hostd.sqlite3', 'hostd.sqlite3-wal', 'hostd.sqlite3-shm', 'hostd.sqlite3-journal', 'status.json') or path.name.startswith('.hostd-status-')):
            return 'file'
        return None

    def _inputs(self, phase):
        data = self._data()
        root = Path(data['run_dir'])
        raw, meta = _read(self._manifest_path, mode=0o600)
        _require(raw == self._manifest_bytes and meta == self._manifest_metadata)
        for name, old_meta in self._input_metadata:
            path = root/_relative(name)
            raw, meta = _read(path, mode=0o700 if path == Path(data['node_binary']) else 0o600,
                                        limit=128*1024*1024 if path == Path(data['node_binary']) else 32*1024*1024)
            _require(_sha(raw) == data['input_sha256'][name] and meta == old_meta)
        known_dirs = dict(self._directory_metadata)
        for name, meta in known_dirs.items():
            _require(_directory(Path(name), private=True) == meta)
        known_files = {root/Path(name) for name, _ in self._input_metadata} | {self._manifest_path}
        if self._sdk_evidence is not None:
            self._sdk_evidence.revalidate(fresh=phase=='prepared')
            known_files.add(self._sdk_evidence.path)
        found_files = set()
        for path in root.rglob('*'):
            try:
                meta = path.lstat()
            except FileNotFoundError:
                # Only a declared transient runtime status file may disappear.
                _require(phase == 'running' and self._runtime_path_allowed(path) == 'file')
                continue
            _require(not stat.S_ISLNK(meta.st_mode))
            if path in known_files:
                found_files.add(path)
                continue
            if str(path) in known_dirs:
                continue
            _require(phase == 'running')
            kind = self._runtime_path_allowed(path)
            _require(kind is not None and meta.st_uid == os.geteuid())
            if kind == 'directory':
                _require(stat.S_ISDIR(meta.st_mode) and stat.S_IMODE(meta.st_mode) == 0o700)
                _directory(path, private=True)
            elif kind == 'socket':
                _require(stat.S_ISSOCK(meta.st_mode) and stat.S_IMODE(meta.st_mode) == 0o600)
            else:
                _require(stat.S_ISREG(meta.st_mode) and stat.S_IMODE(meta.st_mode) == 0o600)
        _require(found_files == known_files)
        if phase == 'prepared':
            for name in ('state_db', 'status_file', 'console_dir'):
                _require(not os.path.lexists(data[name]))
            _require(not any(Path(data['runtime_dir']).iterdir()))
            _require(not any(Path(data['binding_dir']).iterdir()))
            _require(not (Path(data['home'])/'.local').exists())

    def _catalog(self):
        """Actual reviewed production parsers/factories, with no external IO."""
        data = self._data()
        modules = {}
        for name in ('hostd.onboarding_runtime', 'hostd.agent_catalog', 'hostd.registry', 'hostd.join_effects', 'hostd.bot_clients'):
            module = importlib.import_module(name)
            expected = self._reviewed_source/SCRIPTS_RELATIVE/Path(*name.split('.')).with_suffix('.py')
            _require(Path(module.__file__).absolute() == expected)
            modules[name] = module
        config = modules['hostd.onboarding_runtime'].RuntimeConfig.load(data['onboarding_config'])
        for name in ('owner_env_file', 'relay_url', 'relay_pubkey', 'template_config', 'binding_dir', 'legacy_join_path', 'catalog_path'):
            _require(getattr(config, name) == data[name])
        _require(config.trusted_relays == tuple(data['trusted_relays']))
        relay = modules['hostd.join_effects'].Nip98Relay(config.relay_url, config.owner_env_file,
            config.relay_pubkey, trusted_relays=config.trusted_relays)
        _require(relay.owner == self._owner and relay.relay_pubkey == self._relay_pin)
        catalog = modules['hostd.agent_catalog'].load(config.catalog_path, legacy_join_path=config.legacy_join_path)
        records = catalog.records
        _require(tuple(sorted(record.pubkey for record in records)) == self._agents)
        _require(all(record.status == 'own_bot_verified' and record.owner_pubkey == self._owner for record in records))
        profiles = _json(self._profiles_json)
        _require(set(record.app_id for record in records) == set(profiles) and len(records) == len(profiles))
        for record in records:
            client = modules['hostd.bot_clients'].BotLarkCli(record.app_id, record.lark_config_dir,
                record.lark_data_dir, base_env=self.environment())
            _require(profiles[record.app_id] == {'profile': client._profile(),
                'config_dir': str(record.lark_config_dir), 'data_dir': str(record.lark_data_dir)})
        for only in (None, {data['selector']}):
            registry = modules['hostd.registry'].load(Path(data['registry_root']), only=only)
            _require(not registry.bindings and not registry.skipped)
        return len(records)

    def revalidate(self, *, phase):
        try:
            _require(phase in ('prepared', 'running'))
            self._sources()
            self._inputs(phase)
            self._catalog()
        except Exception:
            raise ZeroLocalRunError() from None

    def argv(self):
        data = self._data()
        argv=[data['python'], '-m', 'hostd', 'run', '--only', data['selector'],
                '--onboarding-config', data['onboarding_config'], '--state-db', data['state_db'],
                '--status-file', data['status_file'], '--console-dir', data['console_dir']]
        if self._sdk_evidence is not None:argv.extend(['--sdk-evidence-config',str(self._sdk_evidence.path),'--sdk-evidence-config-sha256',self._sdk_evidence.sha256])
        return argv

    def environment(self):
        data = self._data()
        return {'HOME': data['home'], 'XDG_RUNTIME_DIR': data['runtime_dir'],
                'PYTHONPATH': str(self._reviewed_source/SCRIPTS_RELATIVE), 'PATH': '/bin:/usr/bin',
                'LANG': 'C.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1', 'SSL_CERT_FILE': data['trust_bundle'],
                'HOSTD_NODE_BINARY': data['node_binary'], 'HOSTD_LARK_CLI_ENTRY': data['lark_cli_entry']}

    def _receipt(self, status, started=False, agents=(), apps=()):
        return {'status': status, 'runtime_started': started, 'registry_bindings': 0,
                'target_channel_bindings': 0, 'catalog_own_agents': len(self._agents),
                'registered_agent_pubkeys': list(agents), 'registered_agent_app_ids': list(apps),
                'live_verified': False}

    def readback(self):
        self.revalidate(phase='prepared')
        return self._receipt('prepared')


class OwnedZeroLocalRun:
    def __init__(self, plan, *, popen_factory=subprocess.Popen):
        _require(type(plan) is ZeroLocalRunPlan and callable(popen_factory))
        self._plan, self._popen = plan, popen_factory
        self._process = self._root_identity = None
        self._known_members = {}
        self._stopped = None
        self._started_once = False

    def _observe(self):
        _require(self._process is not None and self._root_identity is not None)
        root = _proc(self._process.pid)
        if root is not None:
            _require(_identity(root) == self._root_identity)
        rows = _session(self._process.pid)
        for pid, row in rows.items():
            _require(row[4] == os.geteuid())
            if pid in self._known_members:
                _require(_identity(row) == _identity(self._known_members[pid]))
            else:
                # New descendants must still have a real chain to original root.
                _require(root is not None)
                parent, seen = row[1], set()
                while parent != root[0]:
                    _require(parent in rows and parent not in seen)
                    seen.add(parent)
                    parent = rows[parent][1]
                self._known_members[pid] = row
        return rows

    def start(self):
        try:
            _require(not self._started_once and self._stopped is None)
            self._plan.revalidate(phase='prepared')
            self._started_once = True
            self._process = self._popen(self._plan.argv(), env=self._plan.environment(),
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                close_fds=True, start_new_session=True, umask=0o077)
            _require(isinstance(self._process, subprocess.Popen))
            row = _proc(self._process.pid)
            _require(row is not None and row[2] == self._process.pid and row[4] == os.geteuid())
            _require(row[1] == os.getpid())
            self._root_identity = _identity(row)
            self._known_members[row[0]] = row
            self._observe()
            return self._plan._receipt('running')
        except BaseException as error:
            if self._process is not None and self._root_identity is not None:
                self.stop(timeout=10)
            if isinstance(error, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)):
                raise
            raise ZeroLocalRunError() from None

    def readback(self):
        try:
            if self._stopped is not None:
                self._plan.revalidate(phase='running')
                _require(not self._observe())
                return dict(self._stopped)
            _require(self._process is not None and self._process.poll() is None)
            self._observe()
            self._plan.revalidate(phase='running')
            data = self._plan._data()
            db_path = Path(data['state_db'])
            _read(db_path, mode=0o600)  # Validate actual path before readonly SQLite.
            with closing(sqlite3.connect(db_path.as_uri()+'?mode=ro', uri=True, timeout=1)) as db:
                db.execute('PRAGMA query_only=ON')
                bindings = db.execute('SELECT binding_id,channel_id FROM binding').fetchall()
                agents = db.execute('SELECT pubkey,owner_pubkey,app_id,status FROM agent ORDER BY pubkey').fetchall()
            _require(not bindings)
            profiles = _json(self._plan._profiles_json)
            _require(tuple(row[0] for row in agents) == self._plan._agents)
            _require(all(row[1] == self._plan._owner and row[3] == 'active' for row in agents))
            _require(sorted(row[2] for row in agents) == sorted(profiles))
            raw, _ = _read(Path(data['status_file']), mode=0o600)
            status = _json(raw)
            _require(isinstance(status, dict) and status.get('bindings') == {})
            _require(set(status.get('apps', {})) == set(profiles))
            _require(self._process.poll() is None)
            self._observe()
            return self._plan._receipt('running', True, [row[0] for row in agents], sorted(profiles))
        except Exception:
            raise ZeroLocalRunError() from None

    def stop(self, *, timeout=10):
        try:
            _require(type(timeout) in (int, float) and math.isfinite(timeout) and 0 < timeout <= 30)
            if self._stopped is not None:
                _require(self._process is None or (self._process.poll() is not None and not self._observe()))
                return dict(self._stopped)
            if self._process is None:
                self._stopped = dict(self._plan._receipt('stopped'), child_reaped=True, session_reaped=True)
                return dict(self._stopped)
            deadline = time.monotonic()+timeout
            graceful = deadline-min(2.0, timeout/3)
            interrupted = None
            root_signalled = False
            while True:
                try:
                    rows = self._observe()
                    reaped = self._process.poll() is not None
                    if not root_signalled:
                        _signal_original(rows.get(self._process.pid), signal.SIGINT)
                        root_signalled = True
                    if not rows and reaped:
                        self._process.wait(timeout=max(.001, deadline-time.monotonic()))
                        break
                    now = time.monotonic()
                    if now >= deadline:
                        raise ZeroLocalRunError()
                    if now >= graceful:
                        # Descendants first; root remains able to reap them.
                        for pid, row in rows.items():
                            if pid != self._process.pid:
                                _signal_original(row, signal.SIGKILL)
                        if len(rows) == 1:
                            _signal_original(rows.get(self._process.pid), signal.SIGKILL)
                    time.sleep(min(.05, max(.001, deadline-now)))
                except (KeyboardInterrupt, SystemExit, asyncio.CancelledError) as error:
                    interrupted = error  # Retain cancellation until real join.
                    if time.monotonic() >= deadline:
                        raise ZeroLocalRunError() from None
            _require(not self._observe() and self._process.poll() is not None)
            self._stopped = dict(self._plan._receipt('stopped'), child_reaped=True, session_reaped=True)
            if interrupted is not None:
                raise interrupted
            return dict(self._stopped)
        except (KeyboardInterrupt, SystemExit, asyncio.CancelledError):
            raise
        except Exception:
            raise ZeroLocalRunError() from None
