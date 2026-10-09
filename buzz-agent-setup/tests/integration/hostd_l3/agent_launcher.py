"""Explicit, run-owned transient user units for the native L3 agent harness.

Admission and native connection observations do not accept an ACP provider or an
L3 scenario. Only the caller holding this object may observe or stop its unit.
No credentials, keys, channels or provider programs are generated here.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import time

from . import prepare, acp_double
from hostd import agent_catalog, delivery_mapping
from hostd.async_io import thread_call
from hostd.join_effects import ProcessOps
import buzz_agent_join_requests as join
import buzz_feishu_group_sync as gs
import recovery_authority as authority

NOTICE = ('本次 Agent 启动或进程读回尚未核实，保留待处理。怎么解决：检查本次私有目录、已审核程序摘要、自己的身份和原 user unit；未知创建结果不要重复启动，也不要接管或停止别人的 unit。'
          '\n复制给 AI：帮我核查本次 L3 Agent 的程序、私有配置、unit 原始归属、当前进程与订阅；不要输出密钥、环境、消息正文或日志，不要把 running/nativeReady 当作业务验收通过。')
SYSTEMD_RUN = '/usr/bin/systemd-run'
SYSTEMCTL = '/usr/bin/systemctl'
MAX_OUTPUT = join.LOG_READ_MAX
HEX = re.compile(r'[0-9a-f]{64}')
INVOCATION = re.compile(r'[0-9a-f]{32}')
SAFE_PATH = re.compile(r'/[A-Za-z0-9_+./-]+')
ANSI = re.compile(r'\x1b\[[0-9;]*m')
UNSET = ('LD_PRELOAD LD_AUDIT LD_LIBRARY_PATH PYTHONHOME PYTHONPATH PYTHONINSPECT PYTHONSTARTUP '
         'BASH_ENV ENV NODE_OPTIONS PERL5OPT RUBYOPT GLIBC_TUNABLES GCONV_PATH LOCPATH NLSPATH '
         'MALLOC_TRACE RES_OPTIONS HOSTALIASES TZDIR GITLAB_TOKEN GH_TOKEN GITHUB_TOKEN '
         'LARK_APP_SECRET FEISHU_APP_SECRET AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN '
         'GRAFANA_TOKEN AUDIENCE_SYNC_API_KEY AUDIENCE_SYNC_API_KEYS AUDIENCE_API_KEY SENTRY_AUTH_TOKEN '
         'PROMETHEUS_PASSWORD ARGOCD_AUTH_TOKEN GROWTHBOOK_API_KEY ZENDESK_OAUTH_CLIENT_SECRET '
         'DAGSTER_TOKEN GRAYLOG_TOKEN DITTOFEED_API_KEY SUPERSET_PASSWORD DAPP_TOKEN NOCODB_TOKEN '
         'NEXUS_PASSWD NEXUS_PASS NINEDATA_SECRET_KEY CROWDIN_API_TOKEN CROWDIN_TOKEN LITELLM_API_KEY '
         'PAPRIKA_API_KEY PAPRIKA_PASSWD SSH_AUTH_SOCK SSH_AGENT_PID')
EXTRA_CREDENTIAL = re.compile(r'(?:TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|ACCESS_KEY|PRIVATE_KEY|AUTH_TAG)', re.I)
ENV_KEYS = frozenset(('BUZZ_PRIVATE_KEY', 'BUZZ_AUTH_TAG', 'BUZZ_RELAY_URL', 'BUZZ_ACP_AGENT_OWNER',
    'BUZZ_ACP_AGENT_COMMAND', 'BUZZ_ACP_AGENT_ARGS', 'BUZZ_ACP_CHANNELS', 'BUZZ_ACP_SYSTEM_PROMPT_FILE',
    'BUZZ_RESPONSIBLE_CONFIG', 'BUZZ_ACP_RESPOND_TO', 'BUZZ_ACP_ALLOWED_RESPOND_TO', 'BUZZ_ACP_SUBSCRIBE',
    'BUZZ_ACP_SESSION_POLICY', 'BUZZ_ACP_AGENTS', 'BUZZ_ACP_SESSION_TITLE', 'BUZZ_ACP_MULTIPLE_EVENT_HANDLING',
    'BUZZ_ACP_LAZY_POOL', 'BUZZ_ACP_BINARY', 'BUZZ_ACP_BINARY_SHA256', 'BUZZ_ACP_RECOVERY_REVISION'))


class LauncherError(ValueError):
    def __init__(self):
        super().__init__(NOTICE)


def _path(value):
    path = prepare._path(value)
    # systemd's property/ExecStart textual grammar is intentionally narrow. We
    # cannot attest quoting, escaping or specifier expansion by substring.
    if not SAFE_PATH.fullmatch(str(path)) or '%' in str(path):
        raise ValueError
    return path


def _env(raw):
    text = raw.decode()
    names = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            # EnvironmentFile is not a shell script.
            raise ValueError
        name, separator, _ = line.partition('=')
        if not separator or name not in ENV_KEYS or name in names:
            raise ValueError
        names.append(name)
    result = join.parse_env(text)
    if set(result) != set(names):
        raise ValueError
    return result


def _identity(environment, owner, *, expected_pub=None):
    pub = gs._signer_pubkey(gs.secret_hex(environment.get('BUZZ_PRIVATE_KEY'), 'agent'))
    auth = json.loads(environment.get('BUZZ_AUTH_TAG', 'null'))
    if (environment.get('BUZZ_ACP_AGENT_OWNER') != owner or not isinstance(auth, list)
            or len(auth) != 4 or not all(isinstance(item, str) for item in auth) or auth[2]
            or authority.attested_owner({'tags': [auth]}, pub) != owner
            or expected_pub is not None and pub != expected_pub):
        raise ValueError
    return pub


def _program(path, expected, *, elf=False):
    path = _path(path)
    if not isinstance(expected, str) or not HEX.fullmatch(expected):
        raise ValueError
    fd = prepare._open(path)
    try:
        meta = os.fstat(fd)
        if elf and (not meta.st_mode & 0o111 or os.read(fd, 4) != b'\x7fELF'):
            raise ValueError
    finally:
        os.close(fd)
    if prepare._file_digest(path) != expected:
        raise ValueError
    return path

def _provider_env_args(provider_args):
    """Clap Vec<String> uses comma delimiters, never shell splitting."""
    if (not isinstance(provider_args, str) or not provider_args or len(provider_args) > 4096
            or any(ord(char) < 32 or 127 <= ord(char) < 160 for char in provider_args)):
        raise LauncherError()
    try:
        tokens = shlex.split(provider_args)
    except ValueError:
        raise LauncherError() from None
    if not tokens or any(not token or ',' in token or token != token.strip() for token in tokens):
        raise LauncherError()
    return ','.join(tokens)


def _witness_admission(prepared, script, provider_args):
    """Derive optional destinations only from the pinned protected argv."""
    tokens = shlex.split(provider_args)
    optional = any(token.startswith('--witness-') for token in tokens)
    if not optional:
        return (), ()
    if (len(tokens) != 10 or tokens[0:2] != ['-I', str(script)]
            or tokens[2] != '--manifest' or tokens[4] != '--sha256'
            or tokens[6] != '--witness-manifest' or tokens[8] != '--witness-sha256'):
        raise LauncherError()
    first = prepare._inside(_path(tokens[3]), prepared.run_dir)
    second = prepare._inside(_path(tokens[7]), prepared.run_dir)
    if first.parent != script.parent or second.parent != script.parent:
        raise LauncherError()
    manifest = acp_double.Manifest(str(first), tokens[5])
    config = acp_double.WitnessConfig(manifest, str(second), tokens[9])
    if manifest.doc['run_id'] != prepared.run_id or os.path.lexists(config.witness):
        raise LauncherError()
    inputs = (first, tokens[5], manifest.file_identity, second, tokens[9], config.file_identity,
              config.witness, manifest.root_identity, manifest.work, manifest.work_identity)
    return inputs, ((first, tokens[5]), (second, tokens[9]))


def _witness_current_inputs(plan):
    value = plan._witness_inputs
    if not value:
        raise LauncherError()
    first, first_pin, first_inode, second, second_pin, second_inode, destination, root_inode, work, work_inode = value
    for path, pin, identity in ((first, first_pin, first_inode), (second, second_pin, second_inode)):
        fd = prepare._open(path, private=True)
        try:
            meta = os.fstat(fd)
            raw = os.read(fd, 16385)
            if (meta.st_nlink != 1 or meta.st_size > 16384 or len(raw) > 16384
                    or prepare._hash(raw) != pin or (meta.st_dev, meta.st_ino) != identity):
                raise LauncherError()
        finally:
            os.close(fd)
    for directory, identity in ((first.parent, root_inode), (work, work_inode)):
        fd = prepare._open(directory, directory=True, private=True)
        try:
            meta = os.fstat(fd)
            if (meta.st_dev, meta.st_ino) != identity:
                raise LauncherError()
        finally:
            os.close(fd)
    return destination


def _provider_holds(pid, identity, run_id):
    """Read one sealed snapshot while proving the provider holds the old FD."""
    try:
        return acp_double.provider_snapshot(pid, run_id, identity)
    except Exception:
        raise LauncherError() from None


@dataclass(frozen=True)
class AgentPlan:
    prepared: prepare.PreparedRun = field(repr=False, compare=False)
    agent_name: str
    unit: str
    env_file: Path = field(repr=False)
    pubkey: str
    owner_pubkey: str
    app_id: str
    channel: str
    relay_url: str
    prompt: Path = field(repr=False)
    responsible: Path = field(repr=False)
    native: Path = field(repr=False)
    native_sha256: str
    provider_python: Path = field(repr=False)
    python_sha256: str
    provider_script: Path = field(repr=False)
    script_sha256: str
    provider_args: str = field(repr=False)
    workdir: Path = field(repr=False)
    receipt: Path = field(repr=False)
    _snapshots: tuple = field(repr=False)
    _path_value: str = field(repr=False)
    _witness_inputs: tuple = field(default=(), repr=False)

    @classmethod
    def check(cls, prepared, *, catalog_path, legacy_join_path, agent_name,
              native_buzz_acp, native_sha256, provider_python, python_sha256,
              provider_script, script_sha256, provider_args):
        try:
            if not isinstance(prepared, prepare.PreparedRun):
                raise ValueError
            prepared.revalidate()
            runtime = prepare._json(prepare._bytes(prepared.onboarding_config))
            if (_path(catalog_path) != _path(runtime['catalog_path'])
                    or _path(legacy_join_path) != _path(runtime['legacy_join_path'])):
                raise ValueError
            catalog = agent_catalog._document(prepare._bytes(catalog_path), allow_empty=True)
            legacy = agent_catalog._document(prepare._bytes(legacy_join_path), allow_empty=True)
            if legacy['agents'] or catalog['owner_pubkey'] != legacy['owner_pubkey']:
                raise ValueError
            selected = [row for row in catalog['agents'] if row['name'] == agent_name]
            if len(selected) != 1 or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', agent_name):
                raise ValueError
            row = selected[0]
            unit = row['unit']
            if not re.fullmatch(r'buzz-l3-' + prepared.run_id + r'(?:-[a-z0-9][a-z0-9_-]{0,63})?\.service', unit):
                raise ValueError
            owner = catalog['owner_pubkey']
            env_file = prepare._inside(_path(row['env_file']), prepared.run_dir)
            environment = _env(prepare._bytes(env_file))
            pub = _identity(environment, owner)
            channel = prepare._json(prepare._bytes(runtime['template_config']))['channel_id']
            if tuple(join._allowlist(environment.get('BUZZ_ACP_CHANNELS', ''))) != (channel,):
                raise ValueError
            relay_url = environment.get('BUZZ_RELAY_URL')
            prepare._loopback_url(relay_url, 'wss')
            if relay_url != runtime['relay_url']:
                raise ValueError
            prompt = prepare._inside(_path(environment['BUZZ_ACP_SYSTEM_PROMPT_FILE']), prepared.run_dir)
            responsible = prepare._inside(_path(environment['BUZZ_RESPONSIBLE_CONFIG']), prepared.run_dir)
            responsible_doc = prepare._json(prepare._bytes(responsible))
            if responsible_doc.get('channels') != [channel]:
                raise ValueError
            block = row.get('feishu')
            if not isinstance(block, dict) or block['app_id'] not in prepare.APPS:
                raise ValueError
            cfg = prepare._inside(_path(block['lark_config_dir']), prepared.run_dir)
            data = prepare._inside(_path(block['lark_data_dir']), prepared.run_dir)
            prepare._private_directory(cfg)
            prepare._private_directory(data)
            profile = prepare._json(prepare._bytes(cfg / 'config.json'))
            apps = profile.get('apps')
            if not isinstance(apps, list) or len(apps) != 1 or apps[0].get('appId') != block['app_id']:
                raise ValueError
            binding = prepare._json(prepare._bytes(runtime['template_config']))
            if binding.get('agents', {}).get(pub) != block:
                raise ValueError
            native = _program(native_buzz_acp, native_sha256, elf=True)
            python = _program(provider_python, python_sha256, elf=True)
            script = _program(provider_script, script_sha256)
            if (not isinstance(provider_args, str) or len(provider_args) > 4096
                    or any(ord(char) < 32 or ord(char) == 127 for char in provider_args)
                    or environment.get('BUZZ_ACP_AGENT_COMMAND') != str(python)
                    or environment.get('BUZZ_ACP_AGENT_ARGS') != _provider_env_args(provider_args)
                    or native == Path(binding['buzz_cli'])):
                raise ValueError
            for key, expected in (('BUZZ_ACP_BINARY', str(native)), ('BUZZ_ACP_BINARY_SHA256', native_sha256),
                                  ('BUZZ_ACP_RECOVERY_REVISION', prepared.revision)):
                if key in environment and environment[key] != expected:
                    raise ValueError
            if row.get('log_file'):
                log = prepare._inside(_path(row['log_file']), prepared.run_dir)
                if os.path.lexists(log):
                    prepare._bytes(log)
            workdir = prepared.run_dir / 'runtime' / ('agent-' + agent_name)
            receipt = prepared.run_dir / 'runtime' / (agent_name + '.receipt.json')
            if os.path.lexists(workdir) or os.path.lexists(receipt):
                raise ValueError
            paths = (Path(catalog_path), Path(legacy_join_path), env_file, prompt, responsible, cfg / 'config.json', prepared.trust_bundle)
            witness_inputs, witness_pins = _witness_admission(prepared, script, provider_args)
            snapshots = tuple((path, prepare._hash(prepare._bytes(path))) for path in paths) + witness_pins
            # Re-read all source/config material, not just a caller's typed row.
            prepared.revalidate()
            for path, expected in snapshots:
                if prepare._hash(prepare._bytes(path)) != expected:
                    raise ValueError
            return cls(prepared, agent_name, unit, env_file, pub, owner, block['app_id'], channel, relay_url,
                       prompt, responsible, native, native_sha256, python, python_sha256, script, script_sha256,
                       provider_args, workdir, receipt, snapshots,
                       str(Path(binding['buzz_cli']).parent) + ':' + str(Path(prepare.NODE).parent) + ':/usr/bin:/bin', witness_inputs)
        except Exception:
            raise LauncherError() from None

    def revalidate(self):
        try:
            self.prepared.revalidate()
            for path, expected in self._snapshots:
                if prepare._hash(prepare._bytes(path)) != expected:
                    raise ValueError
            _program(self.native, self.native_sha256, elf=True)
            _program(self.provider_python, self.python_sha256, elf=True)
            _program(self.provider_script, self.script_sha256)
            if os.path.lexists(self.workdir) or os.path.lexists(self.receipt):
                raise ValueError
            # A frozen dataclass can still be replaced by its trusted caller;
            # canonical protected catalog fields must be admitted again before
            # even querying the named unit.
            current = type(self).check(self.prepared, catalog_path=self._snapshots[0][0],
                legacy_join_path=self._snapshots[1][0], agent_name=self.agent_name,
                native_buzz_acp=self.native, native_sha256=self.native_sha256,
                provider_python=self.provider_python, python_sha256=self.python_sha256,
                provider_script=self.provider_script, script_sha256=self.script_sha256,
                provider_args=self.provider_args)
            if current != self:
                raise ValueError
        except Exception:
            raise LauncherError() from None

    def readback(self):
        return {'status': 'prepared', 'live_verified': False, 'native_sha256': self.native_sha256,
                'provider_python_sha256': self.python_sha256, 'provider_script_sha256': self.script_sha256,
                'provider_args_sha256': prepare._hash(self.provider_args.encode()),
                'credentials_verified': False}


class NativeProcessOps(ProcessOps):
    """Attest the exact executable, argv and unified cgroup of an owned PID.

The only symlink opened is the kernel's fixed /proc/PID/exe interface, after
opening the owned PID directory. No user-provided symlink/path is interpreted.
"""
    def program(self, unit, pid, start):
        if not join.UNIT_RE.fullmatch(unit) or type(pid) is not int or pid <= 0:
            raise LauncherError()
        directory = executable_fd = None
        try:
            directory = os.open('/proc/' + str(pid), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
            if os.fstat(directory).st_uid != os.geteuid():
                raise LauncherError()
            def read(name):
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory)
                with os.fdopen(fd, 'rb') as stream:
                    raw = stream.read(65537)
                if len(raw) > 65536:
                    raise LauncherError()
                return raw
            ticks = int(read('stat').decode().rsplit(')', 1)[1].split()[19])
            if ticks != start:
                raise LauncherError()
            executable = os.readlink('exe', dir_fd=directory)
            executable_fd = os.open('exe', os.O_RDONLY | os.O_CLOEXEC, dir_fd=directory)
            meta = os.fstat(executable_fd)
            if not stat.S_ISREG(meta.st_mode) or meta.st_size > 512 * 1024 * 1024:
                raise LauncherError()
            digest = hashlib.sha256()
            length = 0
            while True:
                chunk = os.read(executable_fd, 1024 * 1024)
                if not chunk:
                    break
                length += len(chunk)
                if length > 512 * 1024 * 1024:
                    raise LauncherError()
                digest.update(chunk)
            raw = read('cmdline')
            if not raw.endswith(b'\0'):
                raise LauncherError()
            argv = tuple(item.decode() for item in raw[:-1].split(b'\0'))
            groups = read('cgroup').decode().splitlines()
            if len(groups) != 1 or not groups[0].startswith('0::/'):
                raise LauncherError()  # This slice requires unified cgroup v2.
            if int(read('stat').decode().rsplit(')', 1)[1].split()[19]) != start:
                raise LauncherError()
            return {'executable': executable, 'sha256': digest.hexdigest(), 'argv': argv,
                    'cgroup': groups[0][3:], 'uid': os.fstat(directory).st_uid}
        except Exception:
            raise LauncherError() from None
        finally:
            if executable_fd is not None:
                os.close(executable_fd)
            if directory is not None:
                os.close(directory)


class OwnedAgent:
    def __init__(self, plan, *, runner=subprocess.run, process_ops=None, approved_channels=None):
        if not isinstance(plan, AgentPlan):
            raise LauncherError()
        self.plan = plan
        self.runner = runner
        uid = os.geteuid()
        self._command_env = {'PATH': '/usr/bin:/bin', 'HOME': str(plan.prepared.run_dir / 'home'), 'LANG': 'C.UTF-8',
            'XDG_RUNTIME_DIR': f'/run/user/{uid}', 'DBUS_SESSION_BUS_ADDRESS': f'unix:path=/run/user/{uid}/bus'}
        self.ops = process_ops or NativeProcessOps(runner, base_env=self._command_env)
        if not isinstance(self.ops, ProcessOps):
            raise LauncherError()
        # Internal root/main-loop seam backed by the CURRENT approved effects.
        # Never accept this callable from HTTP, CLI business input or discovery.
        if approved_channels is not None and not callable(approved_channels):
            raise LauncherError()
        self._approved_channels = approved_channels
        self._lock = asyncio.Lock()
        self._dispatched = False
        self._status = 'not_started'
        self._initial = None
        self._current = None
        self._group = None
        self._native_ready = False
        self._connected = False
        self._subscribed = False
        self._reaped = False

    def readback(self):
        return {'status': self._status, 'live_verified': False, 'connected': self._connected,
                'subscribed': self._subscribed, 'native_ready': self._native_ready, 'reaped': self._reaped}

    def _run(self, argv):
        answer = self.runner(argv, capture_output=True, text=True, timeout=5, check=False, env=self._command_env)
        if (not isinstance(answer.stdout, str) or not isinstance(answer.stderr, str)
                or len(answer.stdout.encode()) > MAX_OUTPUT or len(answer.stderr.encode()) > MAX_OUTPUT):
            raise LauncherError()
        return answer

    def _show(self, properties):
        answer = self._run([SYSTEMCTL, '--user', 'show', self.plan.unit, *sum((['-p', prop] for prop in properties), [])])
        if answer.returncode:
            raise LauncherError()
        values = {}
        for line in answer.stdout.splitlines():
            key, separator, value = line.partition('=')
            if not separator or key not in properties or key in values:
                raise LauncherError()
            values[key] = value
        if set(values) != set(properties):
            raise LauncherError()
        return values

    def _fingerprint(self, *, exited=False):
        values = self._show(('ExecStart', 'EnvironmentFiles', 'ControlGroup', 'WorkingDirectory'))
        command = str(self.plan.native) + ' --relay-url ' + self.plan.relay_url
        pattern = (r'\{ path=' + re.escape(str(self.plan.native)) + r' ; argv\[\]=' + re.escape(command)
                   + r' ; ignore_errors=no ; start_time=\[[^\]\r\n]*\] ; stop_time=\[[^\]\r\n]*\] ; pid=[0-9]+ ; code=[A-Za-z()]+ ; status=[0-9]+/[0-9]+ \}')
        group = values['ControlGroup']
        if exited and group == '' and self._group is not None:
            group = self._group
        if (not re.fullmatch(pattern, values['ExecStart'])
                or values['EnvironmentFiles'] != str(self.plan.env_file) + ' (ignore_errors=no)'
                or values['WorkingDirectory'] != str(self._working_directory())
                or not group.startswith('/user.slice/') or '..' in Path(group).parts
                or Path(group).name != self.plan.unit or self._group is not None and group != self._group):
            raise LauncherError()
        return group

    def _approved(self):
        value = (self.plan.channel,) if self._approved_channels is None else self._approved_channels(self.plan)
        if (not isinstance(value, tuple) or not 1 <= len(value) <= 32
                or any(not isinstance(channel, str) or not gs.UUID_RE.fullmatch(channel) for channel in value)
                or value != tuple(sorted(set(value))) or self.plan.channel not in value):
            raise LauncherError()
        return value

    def _pins(self):
        prepared = self.plan.prepared
        prepare._git_check(prepared.source_root, prepared.revision, prepared._command_runner)
        if prepare._runtime_files(prepared.source_root) != {path for path, _ in prepared._source_hashes if path.is_relative_to(prepared.source_root)}:
            raise LauncherError()
        for path in prepared._directories:
            prepare._private_directory(path)
        mutable = {self.plan.env_file, self.plan.prompt, self.plan.responsible} if self._approved_channels is not None else set()
        # Runtime outputs can now exist, but every admitted input keeps its pin.
        # Only the explicit current-effect seam can permit these three files.
        for path, expected in (*prepared._snapshots, *self.plan._snapshots):
            if path not in mutable and prepare._hash(prepare._bytes(path)) != expected:
                raise LauncherError()
        for path, expected in prepared._source_hashes:
            if prepare._file_digest(path) != expected:
                raise LauncherError()
        _program(self.plan.native, self.plan.native_sha256, elf=True)
        _program(self.plan.provider_python, self.plan.python_sha256, elf=True)
        _program(self.plan.provider_script, self.plan.script_sha256)
        if self.plan._witness_inputs:
            _witness_current_inputs(self.plan)

    async def _io(self, approved, function, *args, **kwargs):
        if self._approved() != approved:
            raise LauncherError()
        result = await thread_call(function, *args, **kwargs)
        if self._approved() != approved:
            raise LauncherError()
        return result

    def _configuration(self, approved):
        self._pins()
        catalog = agent_catalog._document(prepare._bytes(self.plan._snapshots[0][0]), allow_empty=True)
        legacy = agent_catalog._document(prepare._bytes(self.plan._snapshots[1][0]), allow_empty=True)
        rows = [row for row in catalog['agents'] if row['name'] == self.plan.agent_name]
        if (legacy['agents'] or catalog['owner_pubkey'] != self.plan.owner_pubkey
                or legacy['owner_pubkey'] != self.plan.owner_pubkey or len(rows) != 1
                or rows[0]['unit'] != self.plan.unit or rows[0]['env_file'] != str(self.plan.env_file)
                or rows[0].get('feishu', {}).get('app_id') != self.plan.app_id):
            raise LauncherError()
        environment = _env(prepare._bytes(self.plan.env_file))
        _identity(environment, self.plan.owner_pubkey, expected_pub=self.plan.pubkey)
        for key, expected in (('BUZZ_RELAY_URL', self.plan.relay_url),
                ('BUZZ_ACP_SYSTEM_PROMPT_FILE', str(self.plan.prompt)), ('BUZZ_RESPONSIBLE_CONFIG', str(self.plan.responsible)),
                ('BUZZ_ACP_AGENT_COMMAND', str(self.plan.provider_python)), ('BUZZ_ACP_AGENT_ARGS', _provider_env_args(self.plan.provider_args))):
            if environment.get(key) != expected:
                raise LauncherError()
        channels = tuple(join._allowlist(environment.get('BUZZ_ACP_CHANNELS', '')))
        if len(set(channels)) != len(channels) or tuple(sorted(channels)) != approved:
            raise LauncherError()
        # Authorized effects may edit these same protected files. They never
        # permit a different path, key/owner, app or provider to be substituted.
        prepare._bytes(self.plan.prompt)
        responsible = prepare._json(prepare._bytes(self.plan.responsible)).get('channels')
        if not isinstance(responsible, list) or tuple(sorted(responsible)) != approved or len(set(responsible)) != len(responsible):
            raise LauncherError()
        profile = self.plan._snapshots[5]
        if prepare._hash(prepare._bytes(profile[0])) != profile[1]:
            raise LauncherError()
        _program(self.plan.provider_python, self.plan.python_sha256, elf=True)
        _program(self.plan.provider_script, self.plan.script_sha256)
        return channels

    def _path_environment(self):
        return self.plan._path_value

    def _observe(self, approved, *, journal=False):
        self._configuration(approved)
        if self.ops.unit_status(self.plan.unit) != ('loaded', 'active'):
            raise LauncherError()
        group = self._fingerprint()
        pid, start, environment = self.ops.process(self.plan.unit)
        invocation = self.ops.invocation_id(self.plan.unit)
        if (type(pid) is not int or pid <= 0 or type(start) is not int or start <= 0
                or not isinstance(invocation, str) or not INVOCATION.fullmatch(invocation) or invocation == '0' * 32
                or not isinstance(environment, dict)):
            raise LauncherError()
        _identity(environment, self.plan.owner_pubkey, expected_pub=self.plan.pubkey)
        expected = {'HOME': str(self.plan.prepared.run_dir / 'home'), 'SSL_CERT_FILE': str(self.plan.prepared.trust_bundle),
            'PATH': self._path_environment(),
            'BUZZ_RELAY_URL': self.plan.relay_url, 'BUZZ_ACP_SYSTEM_PROMPT_FILE': str(self.plan.prompt),
            'BUZZ_RESPONSIBLE_CONFIG': str(self.plan.responsible), 'BUZZ_ACP_AGENT_COMMAND': str(self.plan.provider_python),
            'BUZZ_ACP_AGENT_ARGS': _provider_env_args(self.plan.provider_args)}
        if any(environment.get(key) != value for key, value in expected.items()):
            raise LauncherError()
        if any(EXTRA_CREDENTIAL.search(key) and key not in ENV_KEYS for key in environment):
            raise LauncherError()
        channels = join._allowlist(environment.get('BUZZ_ACP_CHANNELS', ''))
        if tuple(sorted(channels)) != approved or len(set(channels)) != len(channels):
            raise LauncherError()
        program = self.ops.program(self.plan.unit, pid, start)
        if (program != {'executable': str(self.plan.native), 'sha256': self.plan.native_sha256,
                        'argv': (str(self.plan.native), '--relay-url', self.plan.relay_url),
                        'cgroup': group, 'uid': os.geteuid()}):
            raise LauncherError()
        # ProcessOps pins owned /proc PID+start+environment before returning.
        # Also prove that current main PID is in this exact unit's cgroup.
        cg = self.ops.cgroup_root / group.lstrip('/') / 'cgroup.procs'
        raw = prepare._bytes(cg, private=False, limit=65536).decode()
        members = raw.splitlines()
        if not members or any(not re.fullmatch('[1-9][0-9]*', item) for item in members) or str(pid) not in members:
            raise LauncherError()
        connected = subscribed = False
        if journal:
            text = self.ops.journal_invocation(self.plan.unit, pid, invocation)
            if not isinstance(text, str) or len(text.encode()) > MAX_OUTPUT:
                raise LauncherError()
            lines = ANSI.sub('', text).splitlines()
            subscriptions = set()
            for line in lines:
                line = line.rstrip()
                if (line.endswith('connected to relay at ' + self.plan.relay_url)
                        or line.endswith('relay reconnected to ' + self.plan.relay_url)):
                    connected = True
                    subscriptions.clear()
                elif re.fullmatch(r'(?:(?:[0-9T:Z.+-]+ +)?INFO +buzz_acp::relay: +)?'
                        r'resubscribing to [1-9][0-9]* channel\(s\) after reconnect', line):
                    # Official INFO progress precedes REQ sends; it proves no channel.
                    continue
                elif 'subscribed to channel ' in line:
                    channel = line.rsplit('subscribed to channel ', 1)[1]
                    # Exact successful send_subscribe DEBUG messages, no broad suffix trim.
                    for suffix in (' (with since filter)', ' (since=now)'):
                        if channel.endswith(suffix):
                            channel = channel[:-len(suffix)]
                            break
                    if connected and channel in channels:
                        subscriptions.add(channel)
                    else:
                        connected = False
                        subscriptions.clear()
                elif re.search(r'disconnect|reconnect|connection.*(?:closed|lost|unknown|error|failed|state)|unknown.*connection|relay.*(?:closed|lost|failed)', line, re.I):
                    connected = False
                    subscriptions.clear()
                elif 'connected to relay' in line:
                    connected = False
                    subscriptions.clear()
            subscribed = connected and subscriptions == set(channels)

        again = self.ops.process(self.plan.unit)
        if (again != (pid, start, environment) or self.ops.invocation_id(self.plan.unit) != invocation
                or self._fingerprint() != group or self.ops.unit_status(self.plan.unit) != ('loaded', 'active')):
            raise LauncherError()
        self._configuration(approved)
        return (pid, start, invocation), group, connected, subscribed

    def _receipt(self):
        parent = prepare._open(self.plan.receipt.parent, directory=True, private=True)
        fd = None
        try:
            if not hasattr(self, '_receipt_inode'):
                fd = os.open(self.plan.receipt.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=parent)
                self._receipt_inode = (os.fstat(fd).st_dev, os.fstat(fd).st_ino)
            else:
                fd = os.open(self.plan.receipt.name, os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=parent)
            meta = os.fstat(fd)
            current = os.stat(self.plan.receipt.name, dir_fd=parent, follow_symlinks=False)
            if (not stat.S_ISREG(current.st_mode) or current.st_uid != os.geteuid() or stat.S_IMODE(current.st_mode) != 0o600
                    or (current.st_dev, current.st_ino) != self._receipt_inode
                    or (meta.st_dev, meta.st_ino) != self._receipt_inode):
                raise LauncherError()
            raw = json.dumps(self.readback(), sort_keys=True).encode() + b'\n'
            os.lseek(fd, 0, os.SEEK_SET)
            os.ftruncate(fd, 0)
            while raw:
                written = os.write(fd, raw)
                if written <= 0:
                    raise LauncherError()
                raw = raw[written:]
            os.fsync(fd)
            after = os.stat(self.plan.receipt.name, dir_fd=parent, follow_symlinks=False)
            if (after.st_dev, after.st_ino) != self._receipt_inode:
                raise LauncherError()
        finally:
            if fd is not None:
                os.close(fd)
            os.close(parent)

    def _working_directory(self):
        if self.plan._witness_inputs:
            _witness_current_inputs(self.plan)
            return self.plan._witness_inputs[8]
        return self.plan.workdir

    def _start_argv(self):
        properties = ('Type=exec', 'UMask=0077', 'NoNewPrivileges=yes', 'Restart=no',
            'TimeoutStopSec=2s', 'EnvironmentFile=' + str(self.plan.env_file), 'WorkingDirectory=' + str(self._working_directory()),
            'Environment=HOME=' + str(self.plan.prepared.run_dir / 'home') + ' SSL_CERT_FILE=' + str(self.plan.prepared.trust_bundle)
                + ' PATH=' + self._path_environment(),
            'UnsetEnvironment=' + UNSET, 'StandardOutput=journal', 'StandardError=journal', 'KillMode=control-group')
        return [SYSTEMD_RUN, '--user', '--unit=' + self.plan.unit,
                *('--property=' + prop for prop in properties), '--', str(self.plan.native), '--relay-url', self.plan.relay_url]

    async def start(self):
        async with self._lock:
            if self._dispatched:
                raise LauncherError()
            try:
                approved = self._approved()
                await self._io(approved, self.plan.revalidate)
                if approved != (self.plan.channel,):
                    raise LauncherError()
                if await self._io(approved, self.ops.unit_status, self.plan.unit) != ('not-found', 'inactive'):
                    raise LauncherError()
                absent = await self._io(approved, self._show, ('MainPID',))
                if absent != {'MainPID': '0'}:
                    raise LauncherError()
                # Revalidate CAS after the awaited unit checks and before writes.
                await self._io(approved, self.plan.revalidate)
                parent = prepare._open(self.plan.workdir.parent, directory=True, private=True)
                try:
                    os.mkdir(self.plan.workdir.name, mode=0o700, dir_fd=parent)
                finally:
                    os.close(parent)
                self._dispatched = True
                self._status = 'unknown'
                answer = await self._io(approved, self._run, self._start_argv())
                if answer.returncode:
                    raise LauncherError()
                identity, group, _, _ = await self._io(approved, self._observe, approved)
                if self._approved() != approved:
                    raise LauncherError()
                self._initial = self._current = identity
                self._group = group
                self._status = 'running'
                self._receipt()
                return self.readback()
            except asyncio.CancelledError:
                if self._dispatched:
                    self._status = 'unknown'
                raise
            except Exception:
                raise LauncherError() from None

    async def refresh(self):
        async with self._lock:
            try:
                if self._initial is None or self._reaped:
                    raise LauncherError()
                approved = self._approved()
                identity, group, connected, subscribed = await self._io(approved, self._observe, approved, journal=True)
                if self._approved() != approved:
                    raise LauncherError()
                self._current = identity
                self._group = group
                self._connected, self._subscribed = connected, subscribed
                self._native_ready = connected and subscribed
                self._status = 'running'
                self._receipt()
                return self.readback()
            except asyncio.CancelledError:
                self._native_ready = False
                raise
            except Exception:
                self._native_ready = False
                self._connected = self._subscribed = False
                raise LauncherError() from None

    async def stop(self):
        async with self._lock:
            try:
                if self._initial is None or self._reaped:
                    raise LauncherError()
                approved = self._approved()
                if await self._io(approved, self._exited):
                    if self._approved() != approved:
                        raise LauncherError()
                    self._mark_stopped()
                    return self.readback()
                identity, group, _, _ = await self._io(approved, self._observe, approved)
                if self._approved() != approved:
                    raise LauncherError()
                # A legitimate restart may change PID/InvocationID. Ownership
                # requires the original unit fingerprint, exact own identity
                # and current cgroup, never a remembered numeric PID alone.
                self._current = identity
                if group != self._group:
                    raise LauncherError()
                answer = await self._io(approved, self._run, [SYSTEMCTL, '--user', 'stop', self.plan.unit])
                if answer.returncode:
                    raise LauncherError()
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    values = await self._io(approved, self._show, ('LoadState', 'ActiveState', 'MainPID'))
                    if (values['LoadState'] in ('loaded', 'not-found') and values['ActiveState'] == 'inactive'
                            and values['MainPID'] == '0'):
                        cg = self.ops.cgroup_root / self._group.lstrip('/') / 'cgroup.procs'
                        if os.path.lexists(cg) and prepare._bytes(cg, private=False, limit=65536).strip():
                            raise LauncherError()
                        self._mark_stopped()
                        return self.readback()
                    if self._approved() != approved: raise LauncherError()
                    await asyncio.sleep(.05)
                    if self._approved() != approved: raise LauncherError()
                raise LauncherError()
            except asyncio.CancelledError:
                self._native_ready = False
                raise
            except Exception:
                self._native_ready = self._connected = self._subscribed = False
                raise LauncherError() from None

    def _exited(self):
        self._pins()
        values = self._show(('LoadState', 'ActiveState', 'MainPID'))
        if (values['LoadState'] not in ('loaded', 'not-found') or values['ActiveState'] not in ('inactive', 'failed')
                or values['MainPID'] != '0'):
            return False
        if values['LoadState'] == 'loaded':
            self._fingerprint(exited=True)
            if self.ops.invocation_id(self.plan.unit) != self._current[2]:
                raise LauncherError()  # Cannot adopt an unseen, already-exited successor.
        cg = self.ops.cgroup_root / self._group.lstrip('/') / 'cgroup.procs'
        if os.path.lexists(cg) and prepare._bytes(cg, private=False, limit=65536).strip():
            raise LauncherError()
        if self._show(('LoadState', 'ActiveState', 'MainPID')) != values:
            raise LauncherError()
        return True

    def _mark_stopped(self):
        self._reaped = True
        self._status = 'stopped'
        self._native_ready = self._connected = self._subscribed = False
        self._receipt()

    def _wake_current(self, approved):
        if (self._initial is None or self._reaped or self._status != 'running'
                or self._current != self._initial or not self.plan._witness_inputs
                or self._approved() != approved):
            raise LauncherError()
        identity, group, connected, subscribed = self._observe(approved, journal=True)
        if (identity != self._initial or group != self._group or not connected or not subscribed
                or self._approved() != approved):
            raise LauncherError()
        _witness_current_inputs(self.plan)
        return identity

    def _wake_expected(self, event, selected, image):
        if (selected != self.plan.pubkey or not isinstance(image, str) or not HEX.fullmatch(image)
                or not isinstance(event, dict) or event.get('kind') != 9):
            raise LauncherError()
        rt = prepare._json(prepare._bytes(self.plan.prepared.onboarding_config))
        binding = Path(rt['binding_dir']) / self.plan.prepared.initial_binding / 'config.json'
        config = prepare._json(prepare._bytes(binding))
        mirror = config['mirror_pubkey']
        mapped = delivery_mapping.buzz_mapping(event, self.plan.channel, set(), {mirror})
        if (mapped is None or config['channel_id'] != self.plan.channel
                or event.get('pubkey') != mirror
                or len([t for t in event['tags'] if t[:2] == ['p', selected]]) != 1):
            raise LauncherError()
        metas = [t for t in event['tags'] if t[:1] == ['imeta']]
        if len(metas) != 1:
            raise LauncherError()
        fields = {}
        for item in metas[0][1:]:
            name, separator, value = item.partition(' ')
            if not separator or not value or name in fields:
                raise LauncherError()
            fields[name] = value
        if fields.get('x') != image:
            raise LauncherError()
        return {'event_id': event['id'], 'channel_id': self.plan.channel, 'kind': 9,
                'author_pubkey': mirror, 'created_at': event['created_at'],
                'body_sha256': prepare._hash(event['content'].encode()),
                'tags_sha256': acp_double._hash_json(event['tags']), 'image_sha256': image}

    def _wake_read(self, expected):
        destination = _witness_current_inputs(self.plan)
        fd = prepare._open(destination, private=True)
        try:
            meta = os.fstat(fd)
            inode = (meta.st_dev, meta.st_ino)
            if meta.st_nlink != 1 or meta.st_size > 256 * 1024:
                raise LauncherError()
            raw = os.read(fd, 256 * 1024 + 1)
            if len(raw) != meta.st_size or len(raw) > 256 * 1024:
                raise LauncherError()
            value = prepare._json(raw)
            if (not isinstance(value, dict) or set(value) != {'version', 'run_id', 'records'}
                    or type(value['version']) is not int or value['version'] != 1
                    or value['run_id'] != self.plan.prepared.run_id
                    or not isinstance(value['records'], list) or not 1 <= len(value['records']) <= 64):
                raise LauncherError()
            sequence, matching = 0, []
            record_keys = {'session_id', 'prompt_sequence', 'request_id_sha256', 'prompt_sha256',
                           'provider_pid', 'provider_start_ticks', 'ancestors', 'cgroup_sha256', 'event'}
            for row in value['records']:
                if (not isinstance(row, dict) or set(row) != record_keys
                        or type(row['prompt_sequence']) is not int or not sequence < row['prompt_sequence'] <= 64
                        or not isinstance(row['session_id'], str)
                        or not re.fullmatch('l3-' + self.plan.prepared.run_id + r'-(?:[1-9]|1[0-6])', row['session_id'])
                        or any(not isinstance(row[key], str) or not HEX.fullmatch(row[key])
                            for key in ('request_id_sha256', 'prompt_sha256', 'cgroup_sha256'))):
                    raise LauncherError()
                sequence = row['prompt_sequence']
                if row['event'] == expected:
                    matching.append(row)
            if len(matching) != 1:
                raise LauncherError()
            row = matching[0]
            pid, start = row['provider_pid'], row['provider_start_ticks']
            if type(pid) is not int or type(start) is not int or start <= 0:
                raise LauncherError()
            own, ancestors = acp_double.process_ancestry(pid)
            original = [self._initial[0], self._initial[1]]
            native = acp_double.process_metadata(self._initial[0])
            if (own['start_ticks'] != start or row['ancestors'] != ancestors or original not in ancestors
                    or own['cgroup_sha256'] != native['cgroup_sha256']
                    or own['cgroup_sha256'] != row['cgroup_sha256']):
                raise LauncherError()
            # Actual kernel executable and argv, independent of unit metadata
            # seams and the metadata asserted by the witness file itself.
            program = NativeProcessOps(self.runner, base_env=self._command_env).program(self.plan.unit, pid, start)
            if (program['executable'] != str(self.plan.provider_python)
                    or program['sha256'] != self.plan.python_sha256
                    or program['argv'] != (str(self.plan.provider_python), *shlex.split(self.plan.provider_args))
                    or program['uid'] != os.geteuid()):
                raise LauncherError()
            sealed = _provider_holds(pid, inode, self.plan.prepared.run_id)
            if prepare._hash(sealed) != prepare._hash(raw):
                raise LauncherError()
            if acp_double.process_ancestry(pid) != (own, ancestors):
                raise LauncherError()
            current = os.stat(destination, follow_symlinks=False)
            if ((current.st_dev, current.st_ino) != inode or current.st_nlink != 1
                    or current.st_size != meta.st_size or stat.S_IMODE(current.st_mode) != 0o600):
                raise LauncherError()
            _witness_current_inputs(self.plan)
            return prepare._hash(raw)
        finally:
            os.close(fd)

    async def correlate_wake(self, event, *, selected_pubkey, image_sha256):
        """Observe one exact original native->ACP wake; never publish or restart."""
        async with self._lock:
            try:
                approved = self._approved()
                expected = self._wake_expected(event, selected_pubkey, image_sha256)
                await self._io(approved, self._wake_current, approved)
                witness = await self._io(approved, self._wake_read, expected)
                await self._io(approved, self._wake_current, approved)
                # Close the final await gap before exposing an observation.
                self._wake_current(approved)
                return {'status': 'observed', 'live_verified': False, 'witness_sha256': witness}
            except asyncio.CancelledError:
                raise  # Existing thread_call joins original IO, even on repeated cancellation.
            except Exception:
                return {'status': 'pending', 'live_verified': False, 'reason': 'wake_witness_pending'}
