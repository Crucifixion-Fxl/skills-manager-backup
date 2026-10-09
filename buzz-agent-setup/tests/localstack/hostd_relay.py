"""Explicit, run-scoped three-container L3 launcher. No implicit Docker action.

prepare/check are read-only. start/stop require an explicit runner and return
observed process metadata, never live/E2E readiness. No image pull or global
HOME/stack state, credential discovery, GitLab or provider provisioning.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import socket
import stat
import sys
import time
from types import MappingProxyType
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from hostd.safety import read_owned
import buzz_feishu_group_sync as gs

DOCKER = '/usr/bin/docker'
RUN_LABEL = 'ai.addx.hostd-l3-run'
PATH_LABEL = 'ai.addx.hostd-l3-path'
ROLE_LABEL = 'ai.addx.hostd-l3-role'
IMAGE_FORMAT = '{"Id":{{json .Id}},"RepoDigests":{{json .RepoDigests}}}'
CONTAINER_FORMAT = '{"Id":{{json .Id}},"Name":{{json .Name}},"Labels":{{json .Config.Labels}},"Image":{{json .Image}},"State":{{json .State.Status}}}'
NETWORK_FORMAT = '{"Id":{{json .Id}},"Name":{{json .Name}},"Labels":{{json .Labels}}}'
NOTICE = ('本机 L3 relay 未完成准备或操作，保留当前状态。怎么解决：检查固定运行目录、缓存镜像、端口和资源身份后重试。'
          '\n复制给 AI：帮我检查本次 hostd L3 relay 的私有回执与固定 Docker 资源；不要输出密钥或清理其他运行。')
ID_RE = re.compile(r'[0-9a-f]{64}')
IMAGE_RE = re.compile(r'[a-z0-9][a-z0-9./_-]*@sha256:[0-9a-f]{64}')


def _directory(path):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts or any(ord(c) < 32 or ord(c) == 127 for c in str(path)):
        raise ValueError(NOTICE)
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            os.close(fd);fd = child
        meta = os.fstat(fd)
        if meta.st_uid != os.geteuid() or stat.S_IMODE(meta.st_mode) != 0o700:
            raise ValueError(NOTICE)
        return fd, (meta.st_dev, meta.st_ino)
    except BaseException:
        os.close(fd)
        raise


def port_available(port):
    """Read-only ephemeral loopback reservation check; caller may inject offline."""
    with socket.socket() as probe:
        try:
            probe.bind(('127.0.0.1', port))
            return True
        except OSError:
            return False


@dataclass(frozen=True)
class RelayPlan:
    run_dir: Path
    run_id: str
    images: object
    backend_port: int
    health_port: int
    public_url: str
    owner_pubkey: str
    relay_key_file: Path
    names: object
    directory_identity: tuple
    key_sha256: str

    @classmethod
    def prepare(cls, *, run_dir, run_id, images, backend_port, health_port,
                public_url, owner_pubkey, relay_key_file):
        fd = None
        try:
            directory, key = Path(run_dir), Path(relay_key_file)
            fd, identity = _directory(directory)
            if (not isinstance(run_id, str) or not re.fullmatch(r'[0-9a-f]{32}', run_id)
                    or not isinstance(images, dict) or set(images) != {'postgres', 'redis', 'relay'}
                    or any(not isinstance(image, str) or not IMAGE_RE.fullmatch(image) for image in images.values())
                    or len(set(images.values())) != 3
                    or not isinstance(owner_pubkey, str) or not ID_RE.fullmatch(owner_pubkey)):
                raise ValueError
            public = urlsplit(public_url)
            if (public.scheme != 'wss' or public.hostname != '127.0.0.1' or not public.port
                    or public_url != f'wss://127.0.0.1:{public.port}'):
                raise ValueError
            for port in (backend_port, health_port, public.port):
                if type(port) is not int or not 1024 <= port <= 65535:
                    raise ValueError
            if len({backend_port, health_port, public.port}) != 3:
                raise ValueError
            if key.parent != directory or key.name.startswith('.') or not key.is_absolute():
                raise ValueError
            if set(os.listdir(fd)) != {key.name}:
                raise ValueError
            raw = read_owned(key, max_bytes=256)
            gs._signer_pubkey(gs.secret_hex(raw.decode().strip(), 'L3 relay'))
            names = {role:f'hostd-l3-{run_id}-{role}' for role in ('postgres', 'redis', 'relay', 'network')}
            return cls(directory, run_id, MappingProxyType(dict(images)), backend_port, health_port,
                       public_url, owner_pubkey, key, MappingProxyType(names), identity,
                       hashlib.sha256(raw).hexdigest())
        except (Exception, SystemExit):
            raise ValueError(NOTICE) from None
        finally:
            if fd is not None:
                os.close(fd)


class Launcher:
    def __init__(self, plan, *, runner, port_available=port_available, sleep=time.sleep,
                 readiness_attempts=20):
        if not isinstance(plan, RelayPlan) or not callable(runner) or not callable(port_available):
            raise ValueError(NOTICE)
        if type(readiness_attempts) is not int or not 1 <= readiness_attempts <= 40 or not callable(sleep):
            raise ValueError(NOTICE)
        self.plan, self.runner, self.port_available = plan, runner, port_available
        self.sleep, self.readiness_attempts = sleep, readiness_attempts
        self.labels = {RUN_LABEL:plan.run_id, PATH_LABEL:hashlib.sha256(str(plan.run_dir).encode()).hexdigest()}
        self.images = {}
        self.receipt = None

    def _env(self, extra=None):
        env = {'PATH':'/usr/bin:/bin', 'DOCKER_CONFIG':str(self.plan.run_dir / 'docker-config')}
        env.update(extra or {})
        return env

    def _docker(self, args, *, extra=None, timeout=30):
        try:
            result = self.runner([DOCKER, *args], env=self._env(extra), timeout=timeout,
                                 capture_output=True, text=True)
            if result.returncode:
                raise ValueError
            return result.stdout
        except (Exception, SystemExit):
            # stderr may contain injected secrets; no raw diagnostic escapes.
            raise ValueError(NOTICE) from None

    def _pinned_dir(self):
        fd, identity = _directory(self.plan.run_dir)
        if identity != self.plan.directory_identity:
            os.close(fd)
            raise ValueError(NOTICE)
        return fd

    def _absent(self, role):
        kind = 'network' if role == 'network' else 'container'
        pattern = '^'+('/' if kind == 'container' else '')+self.plan.names[role]+'$'
        return not self._docker([kind, 'ls', *(['-a'] if kind=='container' else []), '--no-trunc', '--filter', 'name='+pattern, '--format', '{{json .}}']).strip()

    def _id_absent(self, role, identity):
        if not isinstance(identity,str) or not ID_RE.fullmatch(identity):
            raise ValueError(NOTICE)
        kind = 'network' if role == 'network' else 'container'
        return not self._docker([kind,'ls', *(['-a'] if kind=='container' else []), '--no-trunc','--filter','id='+identity,'--format','{{json .}}']).strip()

    def check(self):
        fd = None
        try:
            fd = self._pinned_dir()
            if set(os.listdir(fd)) != {self.plan.relay_key_file.name}:
                raise ValueError
            if hashlib.sha256(read_owned(self.plan.relay_key_file, max_bytes=256)).hexdigest() != self.plan.key_sha256:
                raise ValueError
            if not all(self.port_available(port) for port in (self.plan.backend_port, self.plan.health_port)):
                raise ValueError
            self.images = {}
            for role, image in self.plan.images.items():
                answer = json.loads(self._docker(['image', 'inspect', '--format', IMAGE_FORMAT, image]))
                if (not re.fullmatch(r'sha256:[0-9a-f]{64}', answer.get('Id',''))
                        or image not in answer.get('RepoDigests',[])):
                    raise ValueError
                self.images[role] = answer['Id']
            if not all(self._absent(role) for role in self.plan.names):
                raise ValueError
            return {'status':'prepared', 'run_id':self.plan.run_id, 'public_url':self.plan.public_url,
                    'images':dict(self.images)}
        except (Exception, SystemExit):
            raise ValueError(NOTICE) from None
        finally:
            if fd is not None:
                os.close(fd)

    def _save(self):
        fd = self._pinned_dir()
        temporary = '.receipt-'+secrets.token_hex(16)+'.tmp'
        try:
            data = json.dumps(self.receipt, separators=(',', ':')).encode()
            out = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                          0o600, dir_fd=fd)
            try:
                with os.fdopen(out, 'wb') as stream:
                    stream.write(data);stream.flush();os.fsync(stream.fileno())
                if 'receipt.json' in os.listdir(fd):
                    read_owned(self.plan.run_dir / 'receipt.json')
                os.replace(temporary, 'receipt.json', src_dir_fd=fd, dst_dir_fd=fd)
                os.fsync(fd)
            finally:
                try:os.unlink(temporary, dir_fd=fd)
                except FileNotFoundError:pass
        finally:
            os.close(fd)

    def _inspect(self, role, identity):
        kind = 'network' if role == 'network' else 'container'
        form = NETWORK_FORMAT if kind == 'network' else CONTAINER_FORMAT
        try:
            return json.loads(self._docker([kind, 'inspect', '--format', form, identity]))
        except (Exception, SystemExit):
            raise ValueError(NOTICE) from None

    def _verify(self, role, identity, *, running=False, stopped=False):
        if not isinstance(identity, str) or not ID_RE.fullmatch(identity):
            raise ValueError(NOTICE)
        row = self._inspect(role, identity)
        expected = dict(self.labels, **{ROLE_LABEL:role})
        if (row.get('Id') != identity or row.get('Name','').lstrip('/') != self.plan.names[role]
                or not isinstance(row.get('Labels'), dict)
                or any(row['Labels'].get(key) != value for key,value in expected.items())
                or role != 'network' and (row.get('Image') != self.images[role]
                                         or running and row.get('State') != 'running'
                                         or stopped and row.get('State') not in ('exited','created'))):
            raise ValueError(NOTICE)
        return {'id':identity, 'name':self.plan.names[role], 'labels':expected,
                **({'image':self.images[role]} if role != 'network' else {})}

    def _label_args(self, role):
        result = []
        for key, value in dict(self.labels, **{ROLE_LABEL:role}).items():
            result.extend(['--label', key+'='+value])
        return result

    def _ready(self, role, command):
        identity = self.receipt['containers'][role]['id']
        for attempt in range(self.readiness_attempts):
            self._verify(role, identity, running=True)
            try:
                self._docker(['exec', identity, *command], timeout=5)
                return
            except ValueError:
                if attempt+1 < self.readiness_attempts:
                    self.sleep(.5)
        raise ValueError(NOTICE)

    def start(self):
        self.check()  # All admission guards precede creating even local files.
        self.receipt = {'status':'starting', 'run_id':self.plan.run_id, 'public_url':self.plan.public_url,
                        'images':dict(self.images), 'containers':{}}
        fd = self._pinned_dir()
        try:
            if set(os.listdir(fd)) != {self.plan.relay_key_file.name}:
                raise ValueError(NOTICE)
            os.mkdir('docker-config', 0o700, dir_fd=fd)
        except (Exception, SystemExit):
            raise ValueError(NOTICE) from None
        finally:
            os.close(fd)
        self._save()
        try:
            network_id = self._docker(['network', 'create', *self._label_args('network'), self.plan.names['network']]).strip()
            self.receipt['network'] = self._verify('network', network_id)
            self._save()
            for role in ('postgres', 'redis', 'relay'):
                # Recheck exact absence immediately before the create operation.
                if not self._absent(role):
                    raise ValueError
                args = ['run', '-d', '--pull=never', '--name', self.plan.names[role],
                        *self._label_args(role), '--network', network_id]
                extra = None
                if role == 'postgres':
                    args += ['--network-alias','postgres','-e','POSTGRES_USER=buzz','-e','POSTGRES_PASSWORD=hostd_l3_private','-e','POSTGRES_DB=buzz']
                elif role == 'redis':
                    args += ['--network-alias','redis']
                else:
                    raw = read_owned(self.plan.relay_key_file, max_bytes=256)
                    if hashlib.sha256(raw).hexdigest() != self.plan.key_sha256:
                        raise ValueError
                    key = gs.secret_hex(raw.decode().strip(), 'L3 relay')
                    extra = {'BUZZ_RELAY_PRIVATE_KEY':key}
                    settings = {'DATABASE_URL':'postgres://buzz:hostd_l3_private@postgres:5432/buzz',
                        'REDIS_URL':'redis://redis:6379','RELAY_URL':self.plan.public_url,
                        'BUZZ_BIND_ADDR':'0.0.0.0:3000','BUZZ_HEALTH_PORT':'8080','BUZZ_AUTO_MIGRATE':'true',
                        'BUZZ_REQUIRE_AUTH_TOKEN':'true','BUZZ_REQUIRE_RELAY_MEMBERSHIP':'true',
                        'BUZZ_REQUIRE_MEDIA_GET_AUTH':'true','BUZZ_ALLOW_NIP_OA_AUTH':'true',
                        'BUZZ_PUBKEY_ALLOWLIST':'false','BUZZ_HUDDLE_AUDIO_AVAILABLE':'false',
                        'RELAY_OWNER_PUBKEY':self.plan.owner_pubkey,'BUZZ_S3_ACCESS_KEY':'localstack-dummy',
                        'BUZZ_S3_SECRET_KEY':'localstack-dummy','BUZZ_GIT_CONFORMANCE_PROBE':'false','BUZZ_GIT_REPO_PATH':'/tmp/git'}
                    args += ['-p',f'127.0.0.1:{self.plan.backend_port}:3000', '-p',f'127.0.0.1:{self.plan.health_port}:8080']
                    for name, value in settings.items():args += ['-e',name+'='+value]
                    args += ['-e','BUZZ_RELAY_PRIVATE_KEY']
                args.append(self.plan.images[role])
                identity = self._docker(args, extra=extra).strip()
                # Retain the validated created ID even if startup already
                # exited. Explicit stop must still have durable identity.
                self.receipt['containers'][role] = self._verify(role, identity)
                self._save()
                self._verify(role, identity, running=True)
                if role == 'postgres':self._ready(role, ['pg_isready','-h','127.0.0.1','-U','buzz','-d','buzz'])
                elif role == 'redis':self._ready(role, ['redis-cli','ping'])
            self.receipt['status'] = 'started'
            self._save()
            return copy.deepcopy(self.receipt)
        except (Exception, SystemExit):
            self.receipt['status'] = 'failed'
            try:
                self._save()
            except (Exception, SystemExit):
                pass  # Storage failure is still fail-loud; never claim clean.
            raise ValueError(NOTICE) from None

    def stop(self):
        try:
            fd = self._pinned_dir()
            os.close(fd)
            saved = json.loads(read_owned(self.plan.run_dir / 'receipt.json'))
            if (saved.get('run_id') != self.plan.run_id or saved.get('public_url') != self.plan.public_url
                    or saved.get('images') != self.images or set(saved.get('containers',{})) - {'relay','redis','postgres'}
                    or saved.get('status') not in ('starting','started','failed','stopped')):
                raise ValueError
            self.receipt = saved
            resources = [('network', saved['network'])] if saved.get('network') else []
            resources += list(saved['containers'].items())
            recorded = {role for role,record in resources}
            if any(not self._absent(role) for role in set(self.plan.names)-recorded):
                raise ValueError  # A created target without an ID isn't absent.
            present = {}
            # Read ALL identities before ANY destructive command.
            for role, record in resources:
                if self._absent(role):
                    if not self._id_absent(role, record['id']):
                        raise ValueError
                    continue
                if record != self._verify(role, record['id']):
                    raise ValueError
                present[role] = record
            for role in ('relay','redis','postgres'):
                if role in present:
                    identity = present[role]['id']
                    self._verify(role, identity)
                    self._docker(['stop','--time','5',identity], timeout=15)
                    self._verify(role, identity, stopped=True)
                    self._docker(['rm','-v',identity], timeout=15)
                    if not self._absent(role) or not self._id_absent(role, identity):raise ValueError
            if 'network' in present:
                identity = present['network']['id']
                self._verify('network', identity)
                self._docker(['network','rm',identity], timeout=15)
                if not self._absent('network') or not self._id_absent('network', identity):raise ValueError
            self.receipt['status'] = 'stopped'
            self._save()
            return copy.deepcopy(self.receipt)
        except (Exception, SystemExit):
            raise ValueError(NOTICE) from None
