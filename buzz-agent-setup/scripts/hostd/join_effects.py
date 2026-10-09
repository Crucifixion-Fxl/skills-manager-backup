"""Resumable card-approved effects. SQL records intent; independent reads prove effects.

No credentials, bodies or arbitrary API payloads enter the metadata ledger. External
calls use bot identities and NIP98; the old join timer must exclude this agent.
"""
from __future__ import annotations

import contextlib
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import inspect
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import buzz_feishu_group_sync as gs
import buzz_agent_join_requests as legacy
import recovery_authority as authority
from recovery_relay import RecoveryRelay
try:
    from .safety import read_owned
    from .async_io import thread_call
    from .signed_reads import SignedReader
    from . import claim_sync_app as sync_app_claim
    from .store import hexid, ident, localpath, BindingRecord, _directory
    from .onboarding import EffectReceipt
except ImportError:
    from safety import read_owned
    from async_io import thread_call
    from signed_reads import SignedReader
    import claim_sync_app as sync_app_claim
    from store import hexid, ident, localpath, BindingRecord, _directory
    from onboarding import EffectReceipt

NOTICE = ("接入开通尚未通过实际读回核验。怎么解决：请检查 agent 归属、旧接入任务排除、文件权限、空闲运行状态与群频道授权，保留投递账本后重试。"
          "\n复制给 AI：帮我核查 hostd 接入步骤、配置读回、实际进程订阅与 bot 群成员权限；不要输出密钥、凭据、消息正文或个人信息。")
UUID = re.compile(r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}")


class EffectError(ValueError):
    pass


def _channel(value):
    if not isinstance(value, str) or not UUID.fullmatch(value):
        raise EffectError(NOTICE)
    return value


def _hash(value):
    return hashlib.sha256(value if isinstance(value, bytes) else json.dumps(value, separators=(",", ":"), sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class AgentSpec:
    pubkey: str
    owner_pubkey: str
    app_id: str
    env_file: str
    prompt_file: str
    responsible_file: str
    unit: str
    timer_config: str
    timer_state_dir: str
    binding_dir: str = ""
    reader_app_id: str = ""
    reader_config_dir: str = ""
    reader_data_dir: str = ""
    template_config: str = ""

    def __post_init__(self):
        hexid(self.pubkey); hexid(self.owner_pubkey); ident(self.app_id, 'cli_')
        for path in (self.env_file, self.prompt_file, self.responsible_file, self.timer_config, self.timer_state_dir):
            localpath(path)
        if not legacy.UNIT_RE.fullmatch(self.unit):
            raise EffectError(NOTICE)
        if self.binding_dir:
            localpath(self.binding_dir); ident(self.reader_app_id, 'cli_')
            localpath(self.reader_config_dir); localpath(self.reader_data_dir)
            localpath(self.template_config)
            if self.reader_app_id != self.app_id: raise EffectError(NOTICE)


@dataclass(frozen=True)
class MemberPreparation:
    """Root provider: fresh authenticated directory and complete same-app roster.

    Unmapped union IDs are an explicit remaining membership projection; they do
    not justify removing Feishu users or inventing a Buzz identity.
    """
    app_id: str
    chat_id: str
    checked_at: int
    complete: bool
    people: tuple[tuple[str, str], ...]
    pending_union_ids: tuple[str, ...]
    evidence_hash: str

    def __post_init__(self):
        ident(self.app_id, 'cli_'); ident(self.chat_id, 'oc_'); hexid(self.evidence_hash)
        if type(self.checked_at) is not int or self.checked_at < 0 or type(self.complete) is not bool or not isinstance(self.people, tuple) or not isinstance(self.pending_union_ids, tuple): raise EffectError(NOTICE)
        for pair in self.people:
            if not isinstance(pair, tuple) or len(pair) != 2: raise EffectError(NOTICE)
            hexid(pair[0]); ident(pair[1], 'on_')
        for value in self.pending_union_ids: ident(value, 'on_')
        if (len({p for p, _ in self.people}) != len(self.people) or len({u for _, u in self.people}) != len(self.people)
            or len(set(self.pending_union_ids)) != len(self.pending_union_ids)
            or set(self.pending_union_ids) & {u for _, u in self.people}): raise EffectError(NOTICE)


@dataclass(frozen=True)
class RegistrationProof:
    request_id: str
    binding_id: str
    channel_id: str
    chat_id: str
    app_id: str
    worker_active: bool
    reader_connected: bool
    relay_connected: bool
    outlet_active: bool
    checked_at: int
    evidence_hash: str

    def __post_init__(self):
        ident(self.request_id); ident(self.binding_id); _channel(self.channel_id)
        ident(self.chat_id, 'oc_'); ident(self.app_id, 'cli_'); hexid(self.evidence_hash)
        if any(type(value) is not bool for value in (self.worker_active,self.reader_connected,self.relay_connected,self.outlet_active)) or type(self.checked_at) is not int or self.checked_at < 0: raise EffectError(NOTICE)


class BotMembership:
    def __init__(self, clients): self.clients = dict(clients)

    def listing(self, app_id, chat_id):
        ident(app_id, 'cli_'); ident(chat_id, 'oc_')
        client = self.clients.get(app_id)
        if client is None or client.app_id != app_id: raise EffectError(NOTICE)
        answer = client.member_listing(chat_id, 'union_id')
        if not isinstance(answer, gs.MemberListing) or answer.complete is not True: raise EffectError(NOTICE)
        for union in answer.users: ident(union, 'on_')
        return answer

    def leave(self, app_id, chat_id):
        before = self.listing(app_id, chat_id)
        if app_id not in before.bots: return True
        client = self.clients[app_id]
        if client.change_members('DELETE', chat_id, [app_id], 'app_id') != 0: raise EffectError(NOTICE)
        return app_id not in self.listing(app_id, chat_id).bots


def _parent(path):
    path = Path(localpath(path))
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        for part in path.parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            os.close(fd); fd = child
        if os.fstat(fd).st_uid != os.geteuid():
            raise EffectError(NOTICE)
        return fd, path.name
    except Exception:
        os.close(fd)
        raise EffectError(NOTICE) from None


class ProtectedFiles:
    """CAS writes under a pinned nofollow parent and an owner-only shared lock."""
    @contextlib.contextmanager
    def lock(self, path):
        directory = lock = None
        try:
            directory, name = _parent(path)
            lock = os.open('.hostd-effects.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, 0o600, dir_fd=directory)
            meta = os.fstat(lock)
            if not stat.S_ISREG(meta.st_mode) or meta.st_uid != os.geteuid() or stat.S_IMODE(meta.st_mode) != 0o600:
                raise EffectError(NOTICE)
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield directory, name
        except Exception:
            raise EffectError(NOTICE) from None
        finally:
            if lock is not None: os.close(lock)
            if directory is not None: os.close(directory)

    @staticmethod
    def _read(directory, name):
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=directory)
        try:
            meta = os.fstat(fd)
            if not stat.S_ISREG(meta.st_mode) or meta.st_uid != os.geteuid() or stat.S_IMODE(meta.st_mode) != 0o600 or meta.st_size > 4 * 1024 * 1024:
                raise EffectError(NOTICE)
            chunks = []
            while True:
                block = os.read(fd, 65536)
                if not block: break
                chunks.append(block)
                if sum(map(len, chunks)) > 4 * 1024 * 1024: raise EffectError(NOTICE)
            end = os.fstat(fd)
            if (end.st_mtime_ns, end.st_size, end.st_mode, end.st_uid) != (meta.st_mtime_ns, meta.st_size, meta.st_mode, meta.st_uid):
                raise EffectError(NOTICE)
            return b''.join(chunks), meta
        finally:
            os.close(fd)

    def replace(self, path, expected, updated):
        if not isinstance(expected, bytes) or not isinstance(updated, bytes) or len(updated) > 4 * 1024 * 1024:
            raise EffectError(NOTICE)
        with self.lock(path) as (directory, name):
            actual, before = self._read(directory, name)
            if actual != expected: raise EffectError(NOTICE)
            if actual == updated: return
            temporary = '.hostd-' + secrets.token_hex(16)
            fd = None
            try:
                fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=directory)
                view = memoryview(updated)
                while view:
                    view = view[os.write(fd, view):]
                os.fsync(fd); os.close(fd); fd = None
                again, current = self._read(directory, name)
                if again != expected or (current.st_ino, current.st_dev, current.st_mtime_ns) != (before.st_ino, before.st_dev, before.st_mtime_ns):
                    raise EffectError(NOTICE)
                os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
                os.fsync(directory)
            finally:
                if fd is not None: os.close(fd)
                with contextlib.suppress(FileNotFoundError): os.unlink(temporary, dir_fd=directory)

    def create(self, path, value):
        if not isinstance(value, bytes) or len(value) > 4 * 1024 * 1024: raise EffectError(NOTICE)
        with self.lock(path) as (directory, name):
            try:
                actual, _ = self._read(directory, name)
            except FileNotFoundError:
                actual = None
            if actual is not None:
                if actual != value: raise EffectError(NOTICE)
                return
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=directory)
            try:
                view = memoryview(value)
                while view: view = view[os.write(fd, view):]
                os.fsync(fd)
            finally: os.close(fd)
            os.fsync(directory)


class ProcessOps(legacy.SystemOps):
    """Observe a bounded user-unit command and its exact current invocation."""
    def __init__(self, runner=legacy.subprocess.run, *, base_env=None,
                 cgroup_root=Path('/sys/fs/cgroup'), timeout=5):
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 10:
            raise EffectError(NOTICE)
        super().__init__(runner, base_env=base_env, cgroup_root=cgroup_root)
        self.timeout = timeout

    def _run(self, argv):
        try:
            answer = self.runner(argv, capture_output=True, text=True, timeout=self.timeout,
                                 check=False, env=self.env)
            if (not isinstance(answer.stdout, str) or not isinstance(answer.stderr, str)
                or len(answer.stdout.encode()) > legacy.LOG_READ_MAX
                or len(answer.stderr.encode()) > legacy.LOG_READ_MAX):
                raise EffectError(NOTICE)
            return answer
        except Exception:
            raise EffectError(NOTICE) from None

    def _systemctl(self, verb, unit, *extra):
        if not isinstance(unit, str) or not legacy.UNIT_RE.fullmatch(unit):
            raise EffectError(NOTICE)
        return self._run([legacy.SYSTEMCTL, '--user', verb, unit, *extra])

    def unit_status(self, unit):
        answer = self._systemctl('show', unit, '-p', 'LoadState', '-p', 'ActiveState')
        lines = answer.stdout.splitlines()
        if answer.returncode or len(lines) != 2:
            return 'unknown', 'unknown'
        values = {}
        for line in lines:
            if '=' not in line: return 'unknown', 'unknown'
            key, value = line.split('=', 1)
            if key not in ('LoadState', 'ActiveState') or key in values or not re.fullmatch(r'[a-z-]+', value):
                return 'unknown', 'unknown'
            values[key] = value
        if set(values) != {'LoadState', 'ActiveState'}: return 'unknown', 'unknown'
        return values['LoadState'], values['ActiveState']

    def restart(self, unit):
        if self._systemctl('restart', unit).returncode:
            raise EffectError(NOTICE)

    def process(self, unit):
        try:
            answer = self._systemctl('show', unit, '-p', 'MainPID', '--value')
            if answer.returncode or not re.fullmatch(r'[1-9][0-9]*', answer.stdout.strip()):
                raise EffectError(NOTICE)
            pid = int(answer.stdout.strip())
            proc = Path('/proc') / str(pid)
            if proc.stat().st_uid != os.geteuid(): raise EffectError(NOTICE)
            start = int((proc / 'stat').read_text().rsplit(')', 1)[1].split()[19])
            with (proc / 'environ').open('rb') as handle:
                raw = handle.read(legacy.LOG_READ_MAX + 1)
            if len(raw) > legacy.LOG_READ_MAX or start <= 0: raise EffectError(NOTICE)
            entries = [item.decode().split('=', 1) for item in raw.split(b'\0') if b'=' in item]
            environment = dict(entries)
            if len(entries) != len(environment): raise EffectError(NOTICE)
            again = self._systemctl('show', unit, '-p', 'MainPID', '--value')
            current_start = int((proc / 'stat').read_text().rsplit(')', 1)[1].split()[19])
            if again.returncode or again.stdout.strip() != str(pid) or current_start != start:
                raise EffectError(NOTICE)
            return pid, start, environment
        except Exception:
            raise EffectError(NOTICE) from None

    def invocation_id(self, unit):
        answer = self._systemctl('show', unit, '-p', 'InvocationID', '--value')
        invocation = answer.stdout.strip()
        if answer.returncode or not re.fullmatch(r'[0-9a-f]{32}', invocation) or invocation == '0' * 32:
            raise EffectError(NOTICE)
        return invocation

    def journal_invocation(self, unit, pid, invocation):
        if (not isinstance(unit, str) or not legacy.UNIT_RE.fullmatch(unit)
            or type(pid) is not int or pid <= 0 or not isinstance(invocation, str)
            or not re.fullmatch(r'[0-9a-f]{32}', invocation) or invocation == '0' * 32):
            raise EffectError(NOTICE)
        answer = self._run([legacy.JOURNALCTL, '--user', '-u', unit, f'_PID={pid}',
                            f'_SYSTEMD_INVOCATION_ID={invocation}', '-o', 'cat',
                            '--no-pager', '--lines=1000'])
        if answer.returncode: raise EffectError(NOTICE)
        return answer.stdout

    def native_subscription(self, spec, channel, identity):
        from .native_runtime import subscribed
        return subscribed(spec, channel, identity)

    def prewarm(self, spec, identity):
        """One activation action after a known restart, never a readback action."""
        from .native_runtime import inspect
        from recovery_runtime import request_prewarm
        if 'BUZZ_ACP_RECOVERY_REVISION' not in identity[3]:return False
        snapshot, proof = inspect(spec, identity, phases={'starting'})
        if (self.process(spec.unit) != (identity[0],identity[1],identity[3])
                or self.invocation_id(spec.unit) != identity[2]):return False
        return request_prewarm(snapshot=snapshot,main_pid=lambda:self.process(spec.unit)[0],
                              binary_sha256=proof['binary_sha256'],revision=proof['revision'])


def _subscription_mode(environment, channel):
    # Shared harness contract: absent CHANNELS + explicit SUBSCRIBE is all-member;
    # an explicit empty CHANNELS remains an empty finite scope.
    try:
        from .bot_admission import channel_mode
    except ImportError:
        from bot_admission import channel_mode
    return channel_mode(environment, channel)


class AgentRuntime:
    def __init__(self, operations=None):
        self.ops = operations or ProcessOps()

    def _identity(self, spec):
        if self.ops.unit_status(spec.unit) != ('loaded', 'active'): return None
        # Read MainPID first; no synthetic journal or invocation substitutes for
        # an actual owned process. A newly approved channel may be absent here.
        pid, start, environment = self.ops.process(spec.unit)
        if (type(pid) is not int or pid <= 0 or type(start) is not int or start <= 0
            or not isinstance(environment, dict)
            or environment.get('BUZZ_ACP_AGENT_OWNER') != spec.owner_pubkey
            or gs._signer_pubkey(gs.secret_hex(environment.get('BUZZ_PRIVATE_KEY'), 'agent')) != spec.pubkey
            or environment.get('BUZZ_ACP_SYSTEM_PROMPT_FILE') != spec.prompt_file
            or environment.get('BUZZ_RESPONSIBLE_CONFIG') != spec.responsible_file): return None
        invocation = self.ops.invocation_id(spec.unit)
        if (not isinstance(invocation, str) or not re.fullmatch(r'[0-9a-f]{32}', invocation)
            or invocation == '0' * 32): return None
        return pid, start, invocation, environment

    def verify(self, spec, channel):
        try:
            _channel(channel)
            identity = self._identity(spec)
            if identity is None: return False
            pid, start, invocation, environment = identity
            mode = _subscription_mode(environment, channel)
            if mode is None: return False
            native = getattr(self.ops, 'native_subscription', None)
            if callable(native):
                ready = native(spec, channel, identity)
                if ready is not None:
                    return ready is True and self._identity(spec) == identity
            # All-member scope is dynamic: a historical log cannot prove the
            # target is in this actual generation's current subscriptions.
            if mode == 'all_member': return False
            log = self.ops.journal_invocation(spec.unit, pid, invocation)
            if (not isinstance(log, str) or len(log.encode()) > legacy.LOG_READ_MAX
                or not re.search(r'subscribed to channel ' + re.escape(channel) + r'(?![0-9a-f-])', log)): return False
            return self._identity(spec) == identity
        except Exception:
            return False

    def activate(self, spec, channel):
        return self.activate_receipt(spec, channel) is True

    def activate_receipt(self, spec, channel):
        if self.verify(spec, channel): return True
        # None is a positive defer BEFORE dispatch. False is an unknown restart
        # outcome; the durable caller must observe it, never blindly replay it.
        try:
            _channel(channel)
            identity = self._identity(spec)
            if identity is None or self._identity(spec) != identity: return None
            if self.ops.is_busy(spec.unit) is not False: return None
            if self.ops.is_busy(spec.unit) is not False: return None
        except Exception:
            return None
        try:
            self.ops.restart(spec.unit)
            if self.verify(spec, channel):return True
            # The caller persisted the runtime dispatch before entering this
            # action. UNKNOWN recovery only calls verify, so a later readback
            # cannot resend this signal or restart the process again.
            identity = self._identity(spec)
            prewarm = getattr(self.ops, 'prewarm', None)
            if identity is not None and callable(prewarm):prewarm(spec, identity)
        except Exception:
            return False
        return self.verify(spec, channel)


class Nip98Relay(RecoveryRelay):
    """Actual same-process signed HTTP, pinned relay roster signer and no redirects."""
    def __init__(self, origin, owner_env_file, relay_pubkey, *, http=gs._http_get, trusted_relays=(), clock=time.time):
        try:
            from .bot_clients import _trusted_relay
        except ImportError:
            from bot_clients import _trusted_relay
        try:
            self.origin = _trusted_relay(origin, trusted_relays)
            self.owner_env_file = Path(localpath(owner_env_file))
            self.key = gs.secret_hex(legacy.parse_env(read_owned(owner_env_file).decode()).get('BUZZ_PRIVATE_KEY'), 'owner')
            self.owner = gs._signer_pubkey(self.key)
            hexid(relay_pubkey)
            self.relay_pubkey, self.http = relay_pubkey, http
            self.now = lambda: datetime.fromtimestamp(int(clock()), timezone.utc)
        except Exception:
            raise EffectError(NOTICE) from None

    def owner_of(self, agent):
        hexid(agent)
        rows = self.query([{'kinds': [0], 'authors': [agent], 'limit': 2}])
        if not rows or any(row['kind'] != 0 or row['pubkey'] != agent for row in rows): raise EffectError(NOTICE)
        return authority.attested_owner(authority.latest(rows), agent)

    def members(self, channel):
        return self._members(_channel(channel))

    def event(self, kind, tags, content, created_at):
        return gs.sign_event(self.key, kind, tags, content, created_at)

    def private_channel(self, channel, request_id):
        rows = self.query([{'kinds':[39000], 'authors':[self.relay_pubkey], '#d':[channel], 'limit':2}])
        if not rows: return None
        if any(row['pubkey'] != self.relay_pubkey or row['kind'] != 39000 or authority.tags(row, 'd') != [['d', channel]] for row in rows): raise EffectError(NOTICE)
        latest = authority.latest(rows)
        if authority.tags(latest, 'name') != [['name', 'hostd ' + request_id]] or authority.tags(latest, 'private') != [['private']] or authority.tags(latest, 'public'):
            raise EffectError(NOTICE)
        if self.members(channel).get(self.owner) not in ('owner','admin'): raise EffectError(NOTICE)
        return latest['id']

    def profile(self, pubkey):
        rows = self.query([{'kinds':[0], 'authors':[pubkey], 'limit':2}])
        if any(row['pubkey'] != pubkey or row['kind'] != 0 for row in rows): raise EffectError(NOTICE)
        return authority.latest(rows)

    def policy(self, mirror):
        rows = self.query([{'kinds':[30177], 'authors':[self.owner], '#d':[mirror], 'limit':2}])
        if any(row['pubkey'] != self.owner or row['kind'] != 30177 or authority.tags(row, 'd') != [['d',mirror]] for row in rows): raise EffectError(NOTICE)
        return authority.latest(rows)

    def publish_as(self, event, key, auth_tag):
        return gs.publish_signed_event(self.origin, key, event, self.http, self.now(), auth_tag=auth_tag)

    async def read(self, operation, *args):
        """Read-only async facade; all signal-budget work belongs to child main."""
        reader = SignedReader(self)
        if operation == 'policy_snapshot':
            if args: raise EffectError(NOTICE)
            return await reader.read('policy_snapshot')
        if operation == 'query': return await reader.read('query',filters=args[0])
        if operation == 'people':
            return gs.PeopleAnswer({},await reader.read('people',channel=_channel(args[0])))
        if operation == 'owner_of':
            agent=args[0];hexid(agent)
            rows=await self.read('query',[{'kinds':[0],'authors':[agent],'limit':2}])
            if not rows or any(e['kind']!=0 or e['pubkey']!=agent for e in rows): raise EffectError(NOTICE)
            return authority.attested_owner(authority.latest(rows),agent)
        if operation == 'members':
            channel=_channel(args[0])
            rows=await self.read('query',[{'kinds':[39002],'authors':[self.relay_pubkey],'#d':[channel],'limit':2}])
            if not rows or any(e['kind']!=39002 or e['pubkey']!=self.relay_pubkey or authority.tags(e,'d')!=[['d',channel]] for e in rows): raise EffectError(NOTICE)
            return authority.membership(authority.latest(rows),channel)
        if operation in ('profile','policy'):
            pub=args[0];hexid(pub);kind=0 if operation=='profile' else 30177
            filters=[{'kinds':[kind],'authors':[pub if kind==0 else self.owner],'limit':2}]
            if kind==30177: filters[0]['#d']=[pub]
            rows=await self.read('query',filters)
            if any(e['kind']!=kind or e['pubkey']!=(pub if kind==0 else self.owner) or (kind==30177 and authority.tags(e,'d')!=[['d',pub]]) for e in rows): raise EffectError(NOTICE)
            return authority.latest(rows)
        if operation == 'private_channel':
            channel,request_id=args;_channel(channel)
            rows=await self.read('query',[{'kinds':[39000],'authors':[self.relay_pubkey],'#d':[channel],'limit':2}])
            if not rows:return None
            if any(e['kind']!=39000 or e['pubkey']!=self.relay_pubkey or authority.tags(e,'d')!=[['d',channel]] for e in rows):raise EffectError(NOTICE)
            latest=authority.latest(rows)
            if authority.tags(latest,'name')!=[['name','hostd '+request_id]] or authority.tags(latest,'private')!=[['private']] or authority.tags(latest,'public'):raise EffectError(NOTICE)
            if (await self.read('members',channel)).get(self.owner) not in ('owner','admin'):raise EffectError(NOTICE)
            return latest['id']
        raise EffectError(NOTICE)


class JoinEffects:
    def __init__(self, store, specs, relay, runtime, registrar=None, *, clients=None, files=None, clock=time.time):
        self.store, self.specs, self.relay, self.runtime = store, dict(specs), relay, runtime
        self.registrar, self.clients = registrar, dict(clients or {})
        self.files, self.clock = files or ProtectedFiles(), clock

    def _local_spec(self, row):
        current = self.store.join_request(row['request_id'])
        if not current or any(current[key] != row[key] for key in ('agent_id','owner_pubkey','callback_app_id','chat_id','kind','binding_id','status')):
            raise EffectError(NOTICE)
        spec = self.specs.get(row['agent_id'])
        agent = self.store.conn.execute('SELECT * FROM agent WHERE pubkey=?', (row['agent_id'],)).fetchone()
        if (not spec or not agent or agent['status'] != 'active' or agent['owner_pubkey'] != spec.owner_pubkey
            or row['owner_pubkey'] != spec.owner_pubkey or agent['app_id'] != spec.app_id
            or row['callback_app_id'] != spec.app_id or self.relay.owner != spec.owner_pubkey
            ): raise EffectError(NOTICE)
        env = legacy.parse_env(read_owned(spec.env_file).decode())
        # Empty is a valid explicit first-binding allowlist. Validate every actual
        # entry; ownership/card approval and the live roster still gate additions.
        legacy._allowlist(env.get('BUZZ_ACP_CHANNELS', ''))
        if (gs._signer_pubkey(gs.secret_hex(env.get('BUZZ_PRIVATE_KEY'), 'agent')) != spec.pubkey
            or env.get('BUZZ_ACP_AGENT_OWNER') != spec.owner_pubkey
            or env.get('BUZZ_ACP_SYSTEM_PROMPT_FILE') != spec.prompt_file
            or env.get('BUZZ_RESPONSIBLE_CONFIG') != spec.responsible_file): raise EffectError(NOTICE)
        self._timer_excluded(spec)
        return spec

    async def _read(self, operation, *args):
        if isinstance(self.relay,Nip98Relay): return await self.relay.read(operation,*args)
        return await thread_call(getattr(self.relay,operation),*args)

    async def _spec(self, row):
        spec=self._local_spec(row)
        if await self._read('owner_of',spec.pubkey)!=spec.owner_pubkey:raise EffectError(NOTICE)
        if self._local_spec(row)!=spec:raise EffectError(NOTICE)
        return spec

    def _guard(self,row,plan=None):
        spec=self._local_spec(row)
        if plan is not None and self.store.effect_plan(row['request_id'])!=plan:raise EffectError(NOTICE)
        if row['kind']=='channel' and plan is not None:self._bound_plan(row,spec,plan)
        return spec

    def _effective_row(self, row):
        """Only the exact audited pre-adoption snapshot can follow the new route.

        Onboarding retains its initial snapshot across apply/readback. Do not turn
        this into a generic refresh that accepts changed approvals or identities.
        """
        current = self.store.join_request(row['request_id'])
        if current == row: return row
        audit = self.store.join_adoption(row['request_id'])
        if not audit or json.loads(audit['original_request']) != row: raise EffectError(NOTICE)
        target = json.loads(audit['target_binding'])
        expected = dict(row, kind='channel', binding_id=target['binding_id'], updated_at=audit['created_at'])
        if current != expected: raise EffectError(NOTICE)
        return current

    @staticmethod
    def _absent(path):
        # Reject symlinks even when their targets do not exist. Missing owned
        # parent directories are normal for a never-dispatched reserved plan.
        path = Path(localpath(path))
        fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            for part in path.parts[1:-1]:
                try: child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
                except FileNotFoundError: return True
                os.close(fd); fd = child
            try: os.stat(path.name, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError: return True
            return False
        finally: os.close(fd)

    def _adoption_local(self, row, spec, plan, target, decision, files=None):
        self._guard(row, plan)
        actual = [b for b in self.store.bindings() if b['chat_id'] == row['chat_id'] and b['status'] != 'retired']
        base = Path(spec.binding_dir) / row['request_id'] if spec.binding_dir else None
        if (row['status'] != 'approved' or row['kind'] != 'new_binding' or row['binding_id'] is not None
            or not decision or self.store.card_approval_decision(row['request_id']) != decision
            or self.store.effect_steps(row['request_id']) or plan['mirror_pubkey']
            or not base or plan['binding_id'] != row['request_id']
            or plan['secret_ref'] != str(base / 'mirror.env') or plan['config_path'] != str(base / 'config.json')
            or not self._absent(plan['secret_ref']) or not self._absent(plan['config_path'])
            or any(b['binding_id'] == plan['binding_id'] for b in self.store.bindings())
            or len(actual) != 1 or actual[0]['status'] != 'active'
            or self.store.adoption_binding(actual[0]) != self.store.adoption_binding(target)
            or self.store.conn.execute('SELECT 1 FROM console_pause WHERE binding_id=?', (target['binding_id'],)).fetchone()
            or target['chat_ref'] not in ('', gs.chat_ref(row['chat_id']))):
            raise EffectError(NOTICE)
        view = dict(row, kind='channel', binding_id=target['binding_id'])
        self._bound(view)
        cfg = json.loads(read_owned(target['config_path']))
        # _bound already checks explicit sync_app_id or the existing Desk entry;
        # newly-created bindings legitimately use the latter config shape.
        if cfg.get('mirror_pubkey') != target['mirror_pubkey'] or not target['mirror_pubkey']:
            raise EffectError(NOTICE)
        paths = (spec.env_file, spec.prompt_file, spec.responsible_file, spec.timer_config,
                 target['config_path'], cfg.get('mirror_env_file'), str(self.relay.owner_env_file))
        captured = tuple((path, _hash(read_owned(path))) for path in dict.fromkeys(paths))
        if files is not None and captured != files: raise EffectError(NOTICE)
        return cfg, captured

    async def _adopt_existing(self, row, spec, plan):
        targets = [b for b in self.store.bindings() if b['chat_id'] == row['chat_id']
                   and b['status'] != 'retired' and b['binding_id'] != plan['binding_id']]
        if not targets:
            if any(b['chat_id'] == row['chat_id'] and b['binding_id'] != plan['binding_id'] for b in self.store.bindings()):
                raise EffectError(NOTICE)
            return row, plan
        if len(targets) != 1: raise EffectError(NOTICE)
        target = targets[0]
        decision = self.store.card_approval_decision(row['request_id'])
        cfg, files = self._adoption_local(row, spec, plan, target, decision)
        started = int(self.clock())
        check = lambda: self._adoption_local(row, spec, plan, target, decision, files)
        prepared = await self._preparation(row, spec, plan); check()
        client = self.clients.get(spec.app_id)
        if client is None or await thread_call(client.identity) != (spec.app_id, ''): raise EffectError(NOTICE)
        check()
        policies = await self._read('policy_snapshot'); check()
        mirrors = sorted({d[1] for e in policies if (d := gs._first_tag(e, 'd'))
                          and isinstance(d[1], str) and gs.HEX64_RE.fullmatch(d[1]) and gs.declares_mirror(e['content'])})
        profiles = []
        for batch in gs.batches(sorted(set(mirrors) | {spec.pubkey}), gs.PROFILE_QUERY_BATCH):
            profiles.extend(await self._read('query', [{'kinds':[0], 'authors':batch, 'limit':257}])); check()
        for event in profiles: sync_app_claim.event(event, 0, int(self.clock()))
        for event in policies: sync_app_claim.event(event, 30177, int(self.clock()))
        view = gs.read_binding_claims(lambda filters: policies if filters == [{'kinds':[30177]}]
                                    else [e for e in profiles if e['pubkey'] in filters[0]['authors']])
        for event in view.policies:
            if gs.declares_mirror(event['content']):
                mirror = authority.tags(event, 'd')[0][1]
                entries = authority.policy_body(event, mirror).get('feishu', {}).get('bindings', [])
                if not isinstance(entries, list) or len(gs._claims_in(mirror, event['pubkey'], '', event['content'])) != len(entries):
                    raise EffectError(NOTICE)
        claims = [claim for claim in view.claims if claim.chat_ref == gs.chat_ref(row['chat_id'])]
        if len(claims) != 1: raise EffectError(NOTICE)
        claim = claims[0]
        # binding.claimed_at is local first-reconcile time; the signed claim uses
        # its own policy timeline. Pin both independently, never equate them.
        if (not gs.claim_valid(claim, int(self.clock())) or claim.owner != spec.owner_pubkey
            or (claim.channel, claim.mirror) != (target['channel_id'], target['mirror_pubkey'])
            or claim.heartbeat < claim.claimed_at): raise EffectError(NOTICE)
        entry = sync_app_claim.binding_entry(policies, claim.mirror, claim.owner, claim.channel, claim.chat_ref, int(self.clock()))
        if not entry or not sync_app_claim.matches(entry.get('sync_app'), target['sync_app_id']): raise EffectError(NOTICE)
        own_policies = [e for e in policies if e['pubkey'] == spec.owner_pubkey and authority.tags(e, 'd') == [['d',spec.pubkey]]]
        sync_app_claim.profiles_and_app([e for e in profiles if e['pubkey'] in (spec.pubkey,claim.mirror)],
            own_policies, spec.pubkey, claim.mirror, spec.owner_pubkey, spec.app_id, int(self.clock()))
        native = []
        for kind in (39000, 39002):
            rows = await self._read('query', [{'kinds':[kind], 'authors':[self.relay.relay_pubkey], '#d':[claim.channel], 'limit':257}]); check()
            if not rows or len(rows) > 256: raise EffectError(NOTICE)
            for event in rows:
                sync_app_claim.event(event, kind, int(self.clock()))
                if event['pubkey'] != self.relay.relay_pubkey or authority.tags(event, 'd') != [['d',claim.channel]]: raise EffectError(NOTICE)
            latest = authority.latest(rows); native.append(latest)
            if kind == 39000:
                if authority.tags(latest, 'private') != [['private']] or authority.tags(latest, 'public'): raise EffectError(NOTICE)
            else:
                roles = authority.membership(latest, claim.channel)
                # NIP-29 state changes only when membership changes. A fresh
                # query may legitimately return a roster older than a renewed
                # binding claim; the current roles, signer and channel govern.
                if roles.get(spec.owner_pubkey) not in ('owner','admin') or roles.get(claim.mirror) != 'bot':
                    raise EffectError(NOTICE)
        # Fresh bot listing and identity again after the signed reads; no cached
        # roster grants access. The existing reader identity never substitutes for
        # this separately approved joining application.
        prepared = await self._preparation(row, spec, plan); check()
        if await thread_call(client.identity) != (spec.app_id, ''): raise EffectError(NOTICE)
        check()
        now = int(self.clock())
        if not 0 <= now - started <= 300 or not gs.claim_valid(claim, now): raise EffectError(NOTICE)
        proofs = {'checked_at':started, 'events':sorted({e['id'] for e in profiles + policies + native}),
                  'files_hash':_hash(files), 'config_hash':_hash(read_owned(target['config_path'])),
                  'preparation_hash':prepared.evidence_hash}
        return self.store.adopt_join_binding(row, plan, target, decision, secret_ref=spec.env_file, proof_refs=proofs, now=now)

    async def _io(self,call):
        if inspect.iscoroutinefunction(call):return await call()
        return await thread_call(call)

    async def _async_step(self,row,name,intent,observe,execute,*,output='',operation_at=None,waiting_idle=False):
        request=row['request_id'];digest=_hash(intent);now=int(self.clock())
        plan=self.store.effect_plan(request);self._guard(row,plan)
        previous=next((s for s in self.store.effect_steps(request) if s['step']==name),None)
        # Unknown dispatch gets observation only, even after an expired lease.
        # A crash between durable unknown and actual dispatch also stays pending.
        unknown=previous is not None and previous['status']=='unknown'
        reservation=False if unknown else self.store.reserve_effect_step(request,name,digest,now=now,operation_at=operation_at)
        if previous and (previous['intent_hash']!=digest or (operation_at is not None and previous['operation_at']!=operation_at)):raise EffectError(NOTICE)
        proof=await self._io(observe);self._guard(row,plan)
        if proof:
            if reservation or previous:self.store.finish_effect_step(request,name,digest,_hash(proof),output,now=int(self.clock()))
            return True
        if not reservation:return False
        self._guard(row,plan)
        self.store.defer_effect_step(request,name,status='unknown',now=int(self.clock()))
        # SQLite is committed before dispatch. thread_call holds the caller's
        # lock/resources through cancellation and consumes no cancelled receipt.
        result=await self._io(execute);self._guard(row,plan)
        if waiting_idle and result is None:
            self.store.defer_effect_step(request,name,status='waiting_idle',now=int(self.clock()));return False
        proof=await self._io(observe);self._guard(row,plan)
        if not proof:return False
        self.store.finish_effect_step(request,name,digest,_hash(proof),output,now=int(self.clock()))
        return True

    async def _runtime_step(self,row,spec,channel):
        async def observe():return channel if await thread_call(self.runtime.verify,spec,channel) else None
        activate=getattr(self.runtime,'activate_receipt',self.runtime.activate)
        return await self._async_step(row,'runtime',[spec.pubkey,spec.unit,channel],observe,
             lambda:activate(spec,channel),output=channel,waiting_idle=True)

    @staticmethod
    def _timer_excluded(spec):
        config = json.loads(read_owned(spec.timer_config))
        agents = config.get('agents') if isinstance(config, dict) else None
        if not isinstance(config,dict) or config.get('version') != 1 or config.get('owner_pubkey') != spec.owner_pubkey or not isinstance(agents,list): raise EffectError(NOTICE)
        for item in agents:
            if (not isinstance(item,dict) or item.get('env_file') == spec.env_file or item.get('unit') == spec.unit
                or (item.get('feishu') or {}).get('app_id') == spec.app_id): raise EffectError(NOTICE)

    @contextlib.contextmanager
    def _exclusive(self, spec):
        directory = fd = None
        try:
            directory, _ = _parent(Path(spec.timer_state_dir) / 'join.lock')
            meta = os.fstat(directory)
            if stat.S_IMODE(meta.st_mode) != 0o700: raise EffectError(NOTICE)
            fd = os.open('join.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, 0o600, dir_fd=directory)
            meta = os.fstat(fd)
            if not stat.S_ISREG(meta.st_mode) or meta.st_uid != os.geteuid() or stat.S_IMODE(meta.st_mode) != 0o600: raise EffectError(NOTICE)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._timer_excluded(spec)
            yield
        except Exception:
            raise EffectError(NOTICE) from None
        finally:
            if fd is not None: os.close(fd)
            if directory is not None: os.close(directory)

    def _bound(self, row):
        found = next((binding for binding in self.store.bindings() if binding['binding_id'] == row['binding_id']), None)
        if not found or found['status'] == 'retired' or found['chat_id'] != row['chat_id']: raise EffectError(NOTICE)
        _channel(found['channel_id'])
        config = json.loads(read_owned(found['config_path']))
        app = config.get('sync_app_id') or (config.get('agents', {}).get(config.get('desk_pubkey'), {}) or {}).get('app_id')
        if any(config.get(key) != found[key] for key in ('channel_id','chat_id')) or app != found['sync_app_id']: raise EffectError(NOTICE)
        return found

    def _plan(self, row, spec):
        plan = self.store.effect_plan(row['request_id'])
        if plan: return plan
        if row['kind'] == 'channel':
            binding = self._bound(row)
            return self.store.ensure_effect_plan(row['request_id'], binding['channel_id'], binding['binding_id'], spec.env_file, binding['config_path'], now=int(self.clock()))
        if not spec.binding_dir: raise EffectError(NOTICE)
        # Stable request-derived directory and reserved UUID; never glob live files.
        base = Path(spec.binding_dir) / row['request_id']
        return self.store.ensure_effect_plan(row['request_id'], str(uuid.uuid4()), row['request_id'], str(base / 'mirror.env'), str(base / 'config.json'), now=int(self.clock()))

    def _operation_time(self,row,step):
        saved = next((value for value in self.store.effect_steps(row['request_id']) if value['step'] == step),None)
        return saved['operation_at'] if saved else int(self.clock())

    def _step(self, row, name, intent, observe, execute, *, output='', operation_at=None):
        self._guard(row,self.store.effect_plan(row['request_id']))
        request, now = row['request_id'], int(self.clock())
        digest = _hash(intent)
        reservation = self.store.reserve_effect_step(request, name, digest, now=now, operation_at=operation_at)
        proof = observe()
        if proof:
            if reservation or any(step['step'] == name and step['intent_hash'] == digest for step in self.store.effect_steps(request)):
                self.store.finish_effect_step(request, name, digest, _hash(proof), output, now=now)
            return True
        if not reservation: return False
        try:
            execute()
            proof = observe()
            if not proof:
                self.store.defer_effect_step(request, name, status='unknown', now=now)
                return False
            self.store.finish_effect_step(request, name, digest, _hash(proof), output, now=now)
            return True
        except Exception:
            self.store.defer_effect_step(request, name, status='unknown', now=now)
            raise EffectError(NOTICE) from None

    def _config_effects(self, row, spec, channel):
        timestamp = self.store.effect_plan(row['request_id'])['created_at']
        prompt_row = legacy.prompt_row(channel, {'has_list': False}, row['request_id'], timestamp)
        def prompt_contains_channel(value):
            text = value.decode()
            if legacy.PROMPT_BEGIN not in text and legacy.PROMPT_END not in text:
                return False  # An approved first JOIN may initialize this block.
            if text.count(legacy.PROMPT_BEGIN) != 1 or text.count(legacy.PROMPT_END) != 1 or text.index(legacy.PROMPT_BEGIN) >= text.index(legacy.PROMPT_END): raise EffectError(NOTICE)
            return channel in text[text.index(legacy.PROMPT_BEGIN):text.index(legacy.PROMPT_END)]
        def prompt_write():
            old = read_owned(spec.prompt_file); prompt_contains_channel(old)
            text = old.decode()
            if legacy.PROMPT_BEGIN not in text and legacy.PROMPT_END not in text:
                # Preserve every existing byte. Only the managed section for
                # this approved plan is appended through the same secure CAS.
                separator = '' if not text or text.endswith('\n') else '\n'
                updated = text + separator + legacy.PROMPT_BEGIN + '\n' + prompt_row + '\n' + legacy.PROMPT_END + '\n'
            else:
                end = text.index(legacy.PROMPT_END)
                updated = text[:end].rstrip('\n') + '\n' + prompt_row + '\n' + text[end:]
            self.files.replace(spec.prompt_file, old, updated.encode())
        def responsible(value):
            data = json.loads(value); channels = data.get('channels') if isinstance(data, dict) else None
            if not isinstance(channels, list) or any(not isinstance(c, str) or not UUID.fullmatch(c) for c in channels): raise EffectError(NOTICE)
            return channel in channels
        def responsible_write():
            old = read_owned(spec.responsible_file); responsible(old); data = json.loads(old); data['channels'].append(channel)
            self.files.replace(spec.responsible_file, old, (json.dumps(data, ensure_ascii=False, indent=2) + '\n').encode())
        def env(value):
            return _subscription_mode(legacy.parse_env(value.decode()), channel) is not None
        def env_write():
            old = read_owned(spec.env_file); lines = old.decode().splitlines(keepends=True)
            indexes = [i for i, line in enumerate(lines) if legacy.CHANNELS_LINE.fullmatch(line.rstrip('\r\n'))]
            if len(indexes) != 1: raise EffectError(NOTICE)
            index = indexes[0]; line = lines[index]; ending = line[len(line.rstrip('\r\n')):]
            match = legacy.CHANNELS_LINE.fullmatch(line.rstrip('\r\n')); prefix, raw = match.group(1), match.group(2).strip()
            quote = raw[0] if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "'\"" else ''
            channels = legacy._allowlist(raw[1:-1] if quote else raw)
            lines[index] = f"{prefix}BUZZ_ACP_CHANNELS={quote}{','.join(channels + [channel])}{quote}{ending}"
            self.files.replace(spec.env_file, old, ''.join(lines).encode())
        for step, path, check, action in (('agent_prompt', spec.prompt_file, prompt_contains_channel, prompt_write), ('agent_responsible', spec.responsible_file, responsible, responsible_write), ('agent_env', spec.env_file, env, env_write)):
            if not self._step(row, step, [spec.pubkey, channel, path], lambda p=path, c=check: channel if c(read_owned(p)) else None, action, output=channel): return False
        return True

    async def apply(self, row):
        try:
            row = self._effective_row(row)
            if self.store.join_request(row['request_id'])['status'] != 'approved': raise EffectError(NOTICE)
            spec = await self._spec(row)
            with self._exclusive(spec):
                plan = self._plan(row, spec)
                if row['kind'] == 'new_binding':
                    row, plan = await self._adopt_existing(row, spec, plan)
                channel = plan['channel_id']
                if row['kind'] == 'new_binding':
                    await self._new_apply(row, spec, plan)
                    return
                self._bound_plan(row,spec,plan)
                listing=await thread_call(BotMembership(self.clients).listing,spec.app_id,row['chat_id'])
                self._guard(row,plan)
                if spec.app_id not in listing.bots:
                    raise EffectError(NOTICE)
                roles = await self._read('members',channel)
                self._guard(row,plan)
                if roles.get(spec.owner_pubkey) not in ('owner','admin'): raise EffectError(NOTICE)
                event = self.relay.event(9000, [['h', channel], ['p', spec.pubkey], ['role', 'bot']], '', self._operation_time(row,'members'))
                async def members_observe():return spec.pubkey if (await self._read('members',channel)).get(spec.pubkey)=='bot' else None
                if not await self._async_step(row,'members',[event['id']],members_observe,
                    lambda:self.relay.publish(event),output=spec.pubkey,operation_at=event['created_at']):return
                if not self._config_effects(row,spec,channel):return
                if not await self._runtime_step(row,spec,channel):return
                await self._bound_register(row,spec,plan)
        except Exception:
            raise EffectError(NOTICE) from None

    def _bound_plan(self,row,spec,plan):
        binding=self._bound(row)
        if (plan['binding_id'] != binding['binding_id'] or plan['channel_id'] != binding['channel_id']
            or plan['config_path'] != binding['config_path'] or plan['secret_ref'] != spec.env_file
            or plan.get('mirror_pubkey')):
            raise EffectError(NOTICE)
        audit = self.store.join_adoption(row['request_id'])
        if audit:
            decision = self.store.card_approval_decision(row['request_id'])
            if (not decision or self.store._approval_digest(asdict(decision)) != audit['decision_hash']
                or self.store.adoption_binding(binding) != json.loads(audit['target_binding'])
                or self.store.conn.execute('SELECT 1 FROM console_pause WHERE binding_id=?', (binding['binding_id'],)).fetchone()
                or _hash(read_owned(binding['config_path'])) != json.loads(audit['proof_refs'])['config_hash']):
                raise EffectError(NOTICE)
        return binding

    async def _bound_register(self,row,spec,plan):
        # Existing-bound adoption is idempotent root task assembly. Persist its
        # intent, then ask root on each apply (including daemon restart). A saved
        # registrar step cannot replace a fresh live proof or recreate a NEW
        # binding. The existing sync reader and config remain authoritative.
        if self.registrar is None: return
        digest=_hash([plan['binding_id'],plan['channel_id'],row['chat_id'],spec.app_id])
        reserved=self.store.reserve_effect_step(row['request_id'],'registrar',digest,now=int(self.clock()))
        await self.registrar.register(row,plan)
        self._guard(row,plan)
        if await self._registration(row,spec,plan):
            self.store.finish_effect_step(row['request_id'],'registrar',digest,_hash([plan['channel_id'],spec.app_id]),
                                          plan['binding_id'],now=int(self.clock()))
        elif reserved:
            self.store.defer_effect_step(row['request_id'],'registrar',status='unknown',now=int(self.clock()))

    def _bound_authorization(self,row,spec,plan):
        current=self.store.join_request(row['request_id'])
        saved=self.store.effect_plan(row['request_id'])
        keys=('request_id','agent_id','owner_pubkey','callback_app_id','chat_id','kind','binding_id','status')
        if (not current or current['status'] not in ('approved','applied') or current['kind'] != 'channel'
            or any(current[key] != row[key] for key in keys) or saved != plan
            or current['agent_id'] != spec.pubkey or current['owner_pubkey'] != spec.owner_pubkey
            or current['callback_app_id'] != spec.app_id):
            raise EffectError(NOTICE)
        agent=self.store.conn.execute('SELECT * FROM agent WHERE pubkey=?',(spec.pubkey,)).fetchone()
        binding=self._bound_plan(current,spec,plan)
        if (not agent or agent['status'] != 'active' or agent['owner_pubkey'] != spec.owner_pubkey
            or agent['app_id'] != spec.app_id or binding['status'] != 'active'
            or binding['chat_ref'] not in ('',gs.chat_ref(current['chat_id']))):
            raise EffectError(NOTICE)

    def _record_bound_grant(self,row,spec,plan):
        # The live proof was awaited outside SQL. Recheck approval and its exact
        # agent/binding tuple while holding the write transaction before granting
        # startup restoration authority. A later disconnect never revokes it.
        with self.store.transaction():
            self._bound_authorization(row,spec,plan)
            self.store.record_agent_chat(spec.pubkey,row['chat_id'],gs.chat_ref(row['chat_id']),
                                         binding_id=plan['binding_id'],status='active',now=int(self.clock()))

    def _file_proof(self,spec,plan):
        paths=[spec.env_file,spec.prompt_file,spec.responsible_file,spec.timer_config,
               plan['config_path'],plan['secret_ref']]
        owner_env=getattr(self.relay,'owner_env_file',None)
        if owner_env is not None:paths.append(owner_env)
        # Hashes stay in memory. Bind a proof to exact captured protected files;
        # no secret/prompt bytes enter SQL or the external read transport.
        return tuple((path,_hash(read_owned(path))) for path in dict.fromkeys(paths))

    async def readback(self, row):
        verified = False
        try:
            row = self._effective_row(row)
            spec = await self._spec(row); plan = self.store.effect_plan(row['request_id'])
            if not plan: return EffectReceipt(row['request_id'], row['agent_id'], row['chat_id'], False)
            files=self._file_proof(spec,plan)
            if row['kind'] == 'new_binding':
                verified = await self._new_readback(row, spec, plan)
                self._guard(row,plan)
                if self._file_proof(spec,plan)!=files:verified=False
                return EffectReceipt(row['request_id'], row['agent_id'], row['chat_id'], bool(verified))
            self._bound_authorization(row,spec,plan)
            channel = plan['channel_id']
            roles = await self._read('members',channel)
            self._guard(row,plan)
            listing=await thread_call(BotMembership(self.clients).listing,spec.app_id,row['chat_id'])
            self._guard(row,plan)
            running=await thread_call(self.runtime.verify,spec,channel)
            self._guard(row,plan)
            prompt = read_owned(spec.prompt_file).decode(); responsible = json.loads(read_owned(spec.responsible_file))
            env = legacy.parse_env(read_owned(spec.env_file).decode())
            verified = (spec.app_id in listing.bots
                        and roles.get(spec.pubkey) == 'bot' and roles.get(spec.owner_pubkey) in ('owner','admin')
                        and prompt.count(legacy.PROMPT_BEGIN) == prompt.count(legacy.PROMPT_END) == 1
                        and channel in prompt[prompt.index(legacy.PROMPT_BEGIN):prompt.index(legacy.PROMPT_END)]
                        and channel in responsible['channels'] and _subscription_mode(env, channel) is not None
                        and running)
            if verified: verified = await self._registration(row,spec,plan)
            self._guard(row,plan)
            if self._file_proof(spec,plan)!=files:verified=False
            if verified: self._record_bound_grant(row,spec,plan)
        except Exception:
            verified = False
        return EffectReceipt(row['request_id'], row['agent_id'], row['chat_id'], bool(verified))

    async def cleanup(self, row):
        try:
            current = self.store.join_request(row['request_id'])
            if not current or current['status'] not in ('denied','expired'): raise EffectError(NOTICE)
            spec = await self._spec(row)
            with self._exclusive(spec):
                # Never tear down a separately active outlet because a later invite was denied.
                active = self.store.conn.execute("SELECT 1 FROM agent_chat WHERE agent_id=? AND chat_id=? AND status='active'", (spec.pubkey,row['chat_id'])).fetchone()
                if active: raise EffectError(NOTICE)
                self._plan(row,spec)
                bots=BotMembership(self.clients)
                async def observe():return spec.app_id if spec.app_id not in (await thread_call(bots.listing,spec.app_id,row['chat_id'])).bots else None
                if not await self._async_step(row,'cleanup',[row['request_id'],spec.app_id,row['chat_id']],observe,
                    lambda:bots.leave(spec.app_id,row['chat_id']),output=spec.app_id):raise EffectError(NOTICE)
                # Approval effects never ran for a denied request, so no channel/config
                # mutation is authorized here. Actual bot absence is the cleanup receipt.
                return EffectReceipt(row['request_id'], row['agent_id'], row['chat_id'], True)
        except Exception:
            raise EffectError(NOTICE) from None

    def _mirror(self, row, spec, plan, *, create=True):
        if create:
            directory = _directory(Path(plan['secret_ref']).parent)
            os.close(directory)
        try:
            env = legacy.parse_env(read_owned(plan['secret_ref']).decode())
        except FileNotFoundError:
            env = None
        except Exception:
            # read_owned deliberately hides FileNotFoundError; inspect via pinned FD.
            directory, name = _parent(plan['secret_ref'])
            try:
                try: os.stat(name, dir_fd=directory, follow_symlinks=False)
                except FileNotFoundError: env = None
                else: raise EffectError(NOTICE)
            finally: os.close(directory)
        if env is None:
            if not create or plan['mirror_pubkey']: raise EffectError(NOTICE)
            while True:
                key = secrets.token_hex(32)
                try: mirror = gs._signer_pubkey(key); break
                except ValueError: continue
            digest = hashlib.sha256(f'nostr:agent-auth:{mirror}:'.encode()).digest()
            signature = gs.sync.nk.schnorr_sign(digest, bytes.fromhex(self.relay.key), secrets.token_bytes(32)).hex()
            auth = ['auth',spec.owner_pubkey,'',signature]
            value = f'BUZZ_PRIVATE_KEY={key}\nBUZZ_RELAY_URL={self.relay.origin}\nBUZZ_AUTH_TAG={json.dumps(auth,separators=(",",":"))}\n'.encode()
            self.files.create(plan['secret_ref'], value)
            env = legacy.parse_env(read_owned(plan['secret_ref']).decode())
        key = gs.secret_hex(env.get('BUZZ_PRIVATE_KEY'), 'mirror'); mirror = gs._signer_pubkey(key)
        auth = json.loads(env.get('BUZZ_AUTH_TAG', 'null'))
        profile = gs.sign_event(key, 0, [auth], '{"name":"hostd-mirror"}', self._operation_time(row,'mirror'))
        if env.get('BUZZ_RELAY_URL') != self.relay.origin or authority.attested_owner(profile, mirror) != spec.owner_pubkey: raise EffectError(NOTICE)
        if create:
            self.store.set_effect_mirror(row['request_id'], mirror, now=int(self.clock()))
        return key, mirror, auth, profile

    def _candidate_config(self, row, spec, plan):
        cfg = json.loads(read_owned(spec.template_config))
        if not isinstance(cfg, dict): raise EffectError(NOTICE)
        cfg.update(channel_id=plan['channel_id'], chat_id=row['chat_id'], mirror_pubkey=plan['mirror_pubkey'], mirror_env_file=plan['secret_ref'],
                   desk_pubkey=spec.pubkey, agents={spec.pubkey:{'app_id':spec.app_id,'lark_config_dir':spec.reader_config_dir,'lark_data_dir':spec.reader_data_dir}},
                   remove_extras=False, identity='union_id', binding_claim=True)
        cfg['people_api'] = dict(cfg['people_api'])
        cfg['people_api']['signer_env_file'] = str(self.relay.owner_env_file)
        return gs._validated_config(cfg)

    async def _claim_attestation(self, row, plan, cfg):
        # This attests a local sync responsibility, not a card decision/grant.
        # Read each proof only AFTER the actual channel member step succeeded.
        try:
            spec = self._guard(row,plan)
            if (row['status'] != 'approved' or row['kind'] != 'new_binding'
                or spec.reader_app_id != spec.app_id or cfg != self._candidate_config(row,spec,plan)):
                raise EffectError(NOTICE)
            client = self.clients.get(spec.app_id)
            await thread_call(sync_app_claim.bot,client,spec.app_id,spec.reader_config_dir,spec.reader_data_dir,row['chat_id'])
            self._guard(row,plan)
            await self._preparation(row,spec,plan)
            profiles = await self._read('query',[{'kinds':[0],'authors':sorted({spec.pubkey,plan['mirror_pubkey']}),'limit':257}])
            self._guard(row,plan)
            policies = await self._read('query',[{'kinds':[30177],'authors':[spec.owner_pubkey],'#d':[spec.pubkey],'limit':257}])
            self._guard(row,plan)
            sync_app_claim.profiles_and_app(profiles,policies,spec.pubkey,plan['mirror_pubkey'],spec.owner_pubkey,spec.app_id,int(self.clock()))
            rows = await self._read('query',[{'kinds':[39002],'authors':[self.relay.relay_pubkey],'#d':[plan['channel_id']],'limit':257}])
            self._guard(row,plan)
            sync_app_claim.roster(rows,self.relay.relay_pubkey,plan['channel_id'],spec.pubkey,plan['mirror_pubkey'],spec.owner_pubkey,int(self.clock()))
            # Re-read the protected profile after asynchronous proof IO.
            await thread_call(client.identity)
            self._guard(row,plan)
            return sync_app_claim.field(spec.app_id)
        except Exception:
            raise EffectError(NOTICE) from None

    async def _claim(self, row, plan, cfg):
        app = await self._claim_attestation(row,plan,cfg)
        operation_at = self._operation_time(row,'claim')
        entry = {'channel':plan['channel_id'],'chat_ref':gs.chat_ref(row['chat_id']),'claimed_at':plan['created_at'],'heartbeat':operation_at,'policy':gs.claim_policy(cfg),'sync_app':app}
        content = gs.with_binding_claim(None, name='hostd-mirror', channel=plan['channel_id'], ref=entry['chat_ref'], entry=entry)
        event = self.relay.event(30177, [['d',plan['mirror_pubkey']]], content, operation_at)
        async def observe():
            policy = await self._read('policy',plan['mirror_pubkey'])
            if not policy: return None
            if policy['content'] != content:
                # A registered worker renews/reacquires its lease while this
                # approved workflow may still await native readiness. A prior
                # verified creation is immutable evidence; only the lease
                # timestamps may move forward under the same signed policy.
                prior = next((s for s in self.store.effect_steps(row['request_id']) if s['step']=='claim'), None)
                if (not prior or prior['status'] != 'verified'
                    or policy['pubkey'] != row['owner_pubkey'] or policy['created_at'] < operation_at):
                    raise EffectError(NOTICE)
                actual = authority.policy_body(policy,plan['mirror_pubkey'])
                expected = json.loads(content)
                bindings = actual.get('feishu',{}).get('bindings',[])
                if not isinstance(bindings,list) or len(bindings)!=1 or not isinstance(bindings[0],dict):
                    raise EffectError(NOTICE)
                current = bindings[0]
                claimed, heartbeat = current.get('claimed_at'), current.get('heartbeat')
                now = int(self.clock())
                if (type(claimed) is not int or type(heartbeat) is not int
                    or not entry['claimed_at'] <= claimed <= heartbeat
                    or heartbeat < operation_at
                    or not now-gs.CLAIM_LEASE_SECONDS <= heartbeat <= now+gs.RELAY_CLOCK_SKEW_SECONDS):
                    raise EffectError(NOTICE)
                current.update(claimed_at=entry['claimed_at'],heartbeat=operation_at)
                if gs._canonical(actual) != gs._canonical(expected):raise EffectError(NOTICE)
            return policy['id']
        async def publish():
            # The observation itself awaits IO. Recheck the same local authority
            # before external dispatch; never upgrade a previously pinned intent.
            if await self._claim_attestation(row,plan,cfg) != app: raise EffectError(NOTICE)
            await thread_call(self.relay.publish,event)
            self._guard(row,plan)
        return await self._async_step(row,'claim',[event['id']],observe,publish,output=event['id'],operation_at=operation_at)

    async def _preparation(self, row, spec, plan):
        if self.registrar is None or not hasattr(self.registrar, 'prepare_members'): raise EffectError(NOTICE)
        answer = await self.registrar.prepare_members(row, plan)
        self._guard(row,plan)
        listing = await thread_call(BotMembership(self.clients).listing,spec.app_id,row['chat_id'])
        self._guard(row,plan)
        now = int(self.clock())
        if (not isinstance(answer, MemberPreparation) or answer.app_id != spec.app_id or answer.chat_id != row['chat_id']
            or answer.complete is not True or not 0 <= now - answer.checked_at <= 300
            or spec.app_id not in listing.bots or {u for _,u in answer.people} | set(answer.pending_union_ids) != set(listing.users)
            or spec.owner_pubkey not in {p for p,_ in answer.people}): raise EffectError(NOTICE)
        return answer

    async def _new_apply(self, row, spec, plan):
        channel = plan['channel_id']
        if any(binding['chat_id'] == row['chat_id'] and binding['status'] != 'retired' and binding['binding_id'] != plan['binding_id'] for binding in self.store.bindings()):
            raise EffectError(NOTICE)
        # Validate fresh same-app roster/mapping before creating a channel or identity.
        prepared = await self._preparation(row,spec,plan)
        # The unique chat/channel constraints reserve this target before any outward
        # write. A competing request discovered during async preflight loses here.
        self.store.reconcile_bindings([BindingRecord(plan['binding_id'],channel,row['chat_id'],spec.app_id,plan['config_path'],spec.reader_config_dir,spec.reader_data_dir,
                                                    plan['mirror_pubkey'],gs.chat_ref(row['chat_id']),'pending')],now=int(self.clock()))
        event = self.relay.event(9007, [['h',channel],['name','hostd '+row['request_id']],['visibility','private'],['channel_type','stream']], '', self._operation_time(row,'channel'))
        async def channel_observe():return await self._read('private_channel',channel,row['request_id'])
        if not await self._async_step(row,'channel',[event['id']],channel_observe,lambda:self.relay.publish(event),output=channel,operation_at=event['created_at']):return
        key, mirror, auth, profile = self._mirror(row,spec,plan)
        plan = self.store.effect_plan(row['request_id'])
        async def profile_observe():
            current = await self._read('profile',mirror)
            if not current: return None
            if authority.attested_owner(current,mirror) != spec.owner_pubkey: raise EffectError(NOTICE)
            return current['id']
        if not await self._async_step(row,'mirror',[profile['id']],profile_observe,lambda:self.relay.publish_as(profile,key,json.dumps(auth,separators=(',',':'))),output=mirror,operation_at=profile['created_at']): return
        cfg = self._candidate_config(row,spec,plan)
        wanted = {spec.owner_pubkey:'owner',spec.pubkey:'bot',mirror:'bot'}
        member_time = self._operation_time(row,'members')
        wanted.update({pub:'member' for pub,_ in prepared.people if pub not in wanted})
        async def members_observe():
            roles = await self._read('members',channel)
            return sorted(wanted.items()) if all(roles.get(pub) == role for pub,role in wanted.items()) else None
        async def members_execute():
            for pub,role in sorted(wanted.items()):
                roles=await self._read('members',channel);self._guard(row,plan)
                if roles.get(pub)==role:continue
                member_event=self.relay.event(9000,[['h',channel],['p',pub],['role',role]],'',member_time)
                await thread_call(self.relay.publish,member_event);self._guard(row,plan)
        if not await self._async_step(row,'members',[channel,spec.pubkey,mirror],members_observe,members_execute,output=channel,operation_at=member_time): return
        if not await self._claim(row,plan,cfg): return
        value = (json.dumps(cfg,ensure_ascii=False,indent=2)+'\n').encode()
        def config_observe():
            try: actual = read_owned(plan['config_path'])
            except Exception:
                directory,name = _parent(plan['config_path'])
                try:
                    try: os.stat(name,dir_fd=directory,follow_symlinks=False)
                    except FileNotFoundError: return None
                    raise EffectError(NOTICE)
                finally: os.close(directory)
            if actual != value: raise EffectError(NOTICE)
            return _hash(actual)
        if not self._step(row,'config',[_hash(value)],config_observe,lambda:self.files.create(plan['config_path'],value),output=mirror): return
        self.store.reconcile_bindings([BindingRecord(plan['binding_id'],channel,row['chat_id'],spec.app_id,plan['config_path'],spec.reader_config_dir,spec.reader_data_dir,mirror,gs.chat_ref(row['chat_id']), 'pending')],now=int(self.clock()))
        if not self._config_effects(row,spec,channel): return
        if not await self._runtime_step(row,spec,channel):return
        digest = _hash([plan['binding_id'],channel,row['chat_id'],spec.app_id])
        reserved = self.store.reserve_effect_step(row['request_id'],'registrar',digest,now=int(self.clock()))
        if reserved:
            await self.registrar.register(row,plan)
            self._guard(row,plan)
        if await self._registration(row,spec,plan):
            self.store.finish_effect_step(row['request_id'],'registrar',digest,_hash([channel,spec.app_id]),plan['binding_id'],now=int(self.clock()))
            self.store.activate_effect_binding(row['request_id'],now=int(self.clock()))
        elif reserved:
            self.store.defer_effect_step(row['request_id'],'registrar',status='unknown',now=int(self.clock()))

    async def _registration(self,row,spec,plan):
        if self.registrar is None: return False
        answer = await self.registrar.readback(row,plan)
        self._guard(row,plan)
        return (isinstance(answer,RegistrationProof) and answer.request_id == row['request_id'] and answer.binding_id == plan['binding_id']
                and answer.channel_id == plan['channel_id'] and answer.chat_id == row['chat_id'] and answer.app_id == spec.app_id
                and 0 <= int(self.clock()) - answer.checked_at <= 300
                and answer.worker_active and answer.reader_connected and answer.relay_connected and answer.outlet_active)

    async def _new_readback(self,row,spec,plan):
        if not plan['mirror_pubkey'] or not await self._read('private_channel',plan['channel_id'],row['request_id']):return False
        self._guard(row,plan)
        prepared = await self._preparation(row,spec,plan)
        key,mirror,auth,profile = self._mirror(row,spec,plan,create=False)
        if mirror != plan['mirror_pubkey']: return False
        actual_profile = await self._read('profile',mirror)
        self._guard(row,plan)
        if not actual_profile or authority.attested_owner(actual_profile,mirror) != spec.owner_pubkey: return False
        cfg = self._candidate_config(row,spec,plan)
        if json.loads(read_owned(plan['config_path'])) != cfg: return False
        policy = await self._read('policy',mirror)
        self._guard(row,plan)
        if not policy: return False
        claims = gs._claims_in(mirror,spec.owner_pubkey,'hostd-mirror',policy['content'])
        if not any(c.channel == plan['channel_id'] and c.chat_ref == gs.chat_ref(row['chat_id']) and gs.claim_valid(c,int(self.clock()))
                   and c.policy == gs._canonical(gs.claim_policy(cfg)) for c in claims): return False
        roles = await self._read('members',plan['channel_id'])
        self._guard(row,plan)
        if (roles.get(spec.owner_pubkey) not in ('owner','admin') or roles.get(spec.pubkey) != 'bot' or roles.get(mirror) != 'bot'
            or any(roles.get(pub) not in ('owner','admin','member') for pub,_ in prepared.people)): return False
        env=legacy.parse_env(read_owned(spec.env_file).decode()); prompt=read_owned(spec.prompt_file).decode(); responsible=json.loads(read_owned(spec.responsible_file))
        channel=plan['channel_id']
        running=await thread_call(self.runtime.verify,spec,channel)
        self._guard(row,plan)
        if (_subscription_mode(env, channel) is None or prompt.count(legacy.PROMPT_BEGIN) != 1 or prompt.count(legacy.PROMPT_END) != 1
            or channel not in prompt[prompt.index(legacy.PROMPT_BEGIN):prompt.index(legacy.PROMPT_END)] or channel not in responsible['channels']
            or not running): return False
        binding = next((b for b in self.store.bindings() if b['binding_id'] == plan['binding_id']),None)
        if (not binding or binding['status'] != 'active' or binding['channel_id'] != channel or binding['chat_id'] != row['chat_id']
            or binding['sync_app_id'] != spec.app_id or binding['mirror_pubkey'] != mirror or binding['config_path'] != plan['config_path']): return False
        return await self._registration(row,spec,plan)
