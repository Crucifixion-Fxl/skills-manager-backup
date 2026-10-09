"""First-time own-home admission with durable UNKNOWN-before-effects ordering.

This private draft grants no outbound-message authority. SQLite records metadata,
while the protected files and actual process remain the sources of effect truth.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import Future
from dataclasses import asdict, dataclass, field, replace
import hashlib
import json
import re
import secrets
import time
import uuid
from pathlib import Path

import buzz_agent_join_requests as legacy
import buzz_feishu_group_sync as gs
from recovery_origin import canonical_origin
from . import agent_catalog, own_admission_discovery
from .agent_operations import _OwnedLock, OperationError
from .agent_signed_reads import OwnAgentReader
from .bot_clients import BotLarkCli
from .async_io import thread_call
from .join_effects import AgentSpec, JoinEffects, ProtectedFiles, read_owned
from .store import OwnHomeAdmissionRecord, RestartProcess

NOTICE = ('本机 own-home admission 尚未完成实际读回核验；保留 UNKNOWN，不重复派发。'
          '怎么解决：检查原始审批、完整频道来源、受保护配置与实际进程；仅继续只读核验。'
          '\n复制给 AI：帮我只读核查 hostd own-home admission；不要输出凭据、正文或本机路径。')
# The actual Discovery first slice is capped at 32. Store/Source metadata may
# represent 256, but this coordinator never truncates or expands Discovery.
_MAX_CANDIDATES = 32
_HEX = re.compile(r'[0-9a-f]{64}')
_INVOCATION = re.compile(r'[0-9a-f]{32}')


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _json_hash(value) -> str:
    return _sha(json.dumps(value, sort_keys=True, separators=(',', ':')).encode())


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def _bad_constant(value):
    raise ValueError()


def _channels_digest(channels):
    return _json_hash(sorted(channels))


def _safe_result(status, admission_id='', channels=()):
    return AdmissionResult(status, admission_id, tuple(channels), NOTICE if status != 'admitted' else '')


@dataclass(frozen=True)
class AdmissionResult:
    status: str
    admission_id: str = ''
    proposed_channels: tuple[str, ...] = field(default_factory=tuple)
    notice: str = ''

    def readback(self):
        return {'status': self.status, 'admission_id': self.admission_id,
                'proposed_channels': self.proposed_channels, 'notice': self.notice,
                'grant_activated': False, 'sending_ready': False,
                'physical_verified': False, 'live_verified': False}


class OwnHomeRestartAdapter:
    """Narrow old-zero/new-complete process adapter; it never builds grant rows."""

    def __init__(self, operations, spec):
        self.ops, self.spec = operations, spec

    def capture(self, expected_channels):
        spec = self.spec
        if self.ops.unit_status(spec.unit) != ('loaded', 'active'):
            raise OperationError()
        invocation = self.ops.invocation_id(spec.unit)
        if type(invocation) is not str or not _INVOCATION.fullmatch(invocation) or invocation == '0' * 32:
            raise OperationError()
        pid, start, env = self.ops.process(spec.unit)
        if type(pid) is not int or pid <= 0 or type(start) is not int or start <= 0 or type(env) is not dict:
            raise OperationError()
        again = self.ops.process(spec.unit)
        if again[:2] != (pid, start) or self.ops.invocation_id(spec.unit) != invocation:
            raise OperationError()
        channels = tuple(legacy._allowlist(env.get('BUZZ_ACP_CHANNELS', '')))
        if (channels != tuple(sorted(set(channels))) or set(channels) != set(expected_channels)
                or env.get('BUZZ_ACP_AGENT_OWNER') != spec.owner_pubkey
                or gs._signer_pubkey(gs.secret_hex(env.get('BUZZ_PRIVATE_KEY'), 'agent')) != spec.pubkey
                or env.get('BUZZ_ACP_SYSTEM_PROMPT_FILE') != spec.prompt_file
                or env.get('BUZZ_RESPONSIBLE_CONFIG') != spec.responsible_file
                or env.get('INVOCATION_ID') != invocation):
            raise OperationError()
        return RestartProcess(pid, start, invocation), env

    def idle(self):
        return self.ops.is_busy(self.spec.unit) is False

    def journal_has(self, process, channels):
        body = self.ops.journal_invocation(self.spec.unit, process.pid, process.invocation)
        if type(body) is not str or len(body) > legacy.LOG_READ_MAX:
            return False
        return all(re.search(r'(?<![A-Za-z0-9_])subscribed to channel ' + re.escape(channel)
                             + r'(?![A-Za-z0-9_-])', body) for channel in channels)


class OwnHomeAdmissionCoordinator:
    """Pubkey-only first admission; untrusted call arguments never carry authority."""

    def __init__(self, catalog_path, legacy_join_path, store, *, reader_factory,
                 bot_factory, spec_getter, operations, trusted_relays, clock=lambda: int(time.time())):
        self.catalog_path = Path(catalog_path)
        self.legacy_join_path = Path(legacy_join_path)
        self.store = store
        self.reader_factory, self.bot_factory = reader_factory, bot_factory
        self.spec_getter, self.operations = spec_getter, operations
        self.trusted_relays, self.clock = tuple(trusted_relays), clock
        self._loop = None
        self._active = set()

    def _now(self):
        value = self.clock()
        if type(value) is not int or value < 0:
            raise ValueError()
        return value

    @staticmethod
    def _record_from_catalog(catalog, pubkey, expected_channels):
        rows = [row for row in catalog.records if row.pubkey == pubkey]
        if len(rows) != 1:
            raise ValueError()
        record = rows[0]
        if (record.status != 'own_bot_verified' or record.local_bot_verified is not True
                or record.channels != tuple(sorted(expected_channels))):
            raise ValueError()
        return record

    @staticmethod
    def _match_spec(record, spec):
        if (type(spec) is not AgentSpec or spec.pubkey != record.pubkey
                or spec.owner_pubkey != record.owner_pubkey or spec.app_id != record.app_id
                or Path(spec.env_file) != record.env_file or spec.unit != record.unit
                or Path(spec.prompt_file) != record.prompt_file
                or Path(spec.responsible_file) != record.responsible_config):
            raise ValueError()

    async def _select(self, pubkey, expected_channels=()):
        catalog = await thread_call(agent_catalog.load, self.catalog_path,
                                    legacy_join_path=self.legacy_join_path)
        record = self._record_from_catalog(catalog, pubkey, expected_channels)
        # Runtime-owned map, never a filesystem loader: only call on SQL loop.
        spec = self.spec_getter(pubkey)
        self._match_spec(record, spec)
        reader = await thread_call(self.reader_factory, record)
        bot = await thread_call(self.bot_factory, record)
        if (self.spec_getter(pubkey) != spec or type(reader) is not OwnAgentReader
                or type(bot) is not BotLarkCli or (reader.agent, reader.owner) !=
                    (record.pubkey, record.owner_pubkey) or Path(reader.env_file) != record.env_file
                or reader.origin not in {canonical_origin(value) for value in self.trusted_relays}
                or (bot.app_id, bot.config_dir, bot.data_dir, bot.chat_id) !=
                    (record.app_id, record.lark_config_dir, record.lark_data_dir, None)):
            raise ValueError()
        return catalog, record, spec, reader, bot

    def _sql_identity(self, record, spec):
        row = self.store.conn.execute('SELECT * FROM agent WHERE pubkey=?',
                                      (record.pubkey,)).fetchone()
        if (row is None or row['status'] != 'active'
                or row['owner_pubkey'] != record.owner_pubkey or row['app_id'] != record.app_id
                or row['config_path'] != str(record.env_file)
                or self.spec_getter(record.pubkey) != spec):
            raise ValueError()
        self._match_spec(record, spec)

    def _physical(self, catalog, record, spec, expected_files, protected_channels,
                  process_channels, expected_process, *, journal=False):
        """Joined worker: actual protected/key/profile/timer/process/idle IO only."""
        first = agent_catalog.load(self.catalog_path, legacy_join_path=self.legacy_join_path)
        selected = self._record_from_catalog(first, record.pubkey, protected_channels)
        wanted = replace(record, channels=tuple(protected_channels),
                         env_sha256=_sha(expected_files[spec.env_file]))
        if (first.catalog_sha256 != catalog.catalog_sha256
                or first.legacy_join_sha256 != catalog.legacy_join_sha256 or selected != wanted):
            raise ValueError()
        self._match_spec(selected, spec)
        JoinEffects._timer_excluded(spec)
        if self._read_files(spec) != expected_files:
            raise ValueError()
        responsible = json.loads(expected_files[spec.responsible_file],
                                 object_pairs_hook=_unique_object, parse_constant=_bad_constant)
        prompt = expected_files[spec.prompt_file].decode('utf-8')
        begin, end = legacy.PROMPT_BEGIN, legacy.PROMPT_END
        if (type(responsible) is not dict or responsible.get('channels') != list(protected_channels)
                or prompt.count(begin) != 1 or prompt.count(end) != 1
                or prompt.index(begin) >= prompt.index(end)
                or any(channel not in prompt[prompt.index(begin):prompt.index(end)]
                       for channel in protected_channels)):
            raise ValueError()
        ops = OwnHomeRestartAdapter(self.operations, spec)
        process, _ = ops.capture(process_channels)
        if expected_process is not None and process != expected_process:
            raise ValueError()
        if not ops.idle() or not ops.idle():
            raise ValueError()
        if journal and not ops.journal_has(process, process_channels):
            raise ValueError()
        # Catch mutation during process/journal/idle observation as well.
        if (ops.capture(process_channels)[0] != process or self._read_files(spec) != expected_files
                or agent_catalog.load(self.catalog_path,
                    legacy_join_path=self.legacy_join_path) != first):
            raise ValueError()
        JoinEffects._timer_excluded(spec)
        return first, selected, process

    @staticmethod
    def _read_files(spec):
        return {path: read_owned(path) for path in
                (spec.env_file, spec.prompt_file, spec.responsible_file)}

    @staticmethod
    def _file_digest(raw_files):
        return _sha(b''.join(len(raw_files[path]).to_bytes(8, 'big') + raw_files[path]
                             for path in sorted(raw_files)))

    @staticmethod
    def _responsible_update(raw, channels):
        """Replace only the unique top-level channels JSON value, not its peers."""
        text = raw.decode('utf-8')
        decoder = json.JSONDecoder(object_pairs_hook=_unique_object, parse_constant=_bad_constant)
        document = decoder.decode(text)
        if (type(document) is not dict or type(document.get('channels')) is not list
                or document['channels']):
            raise ValueError()
        def skip(index):
            while index < len(text) and text[index] in ' \t\r\n':
                index += 1
            return index
        index = skip(0)
        if text[index] != '{':
            raise ValueError()
        index = skip(index + 1)
        span = None
        while text[index] != '}':
            key, index = decoder.raw_decode(text, index)
            if type(key) is not str:
                raise ValueError()
            index = skip(index)
            if text[index] != ':':
                raise ValueError()
            start = skip(index + 1)
            value, end = decoder.raw_decode(text, start)
            if key == 'channels':
                if span is not None or type(value) is not list or value:
                    raise ValueError()
                span = start, end
            index = skip(end)
            if text[index] == '}':
                break
            if text[index] != ',':
                raise ValueError()
            index = skip(index + 1)
        if span is None:
            raise ValueError()
        start, end = span
        updated = text[:start] + json.dumps(list(channels), separators=(',', ':')) + text[end:]
        if decoder.decode(updated) != dict(document, channels=list(channels)):
            raise ValueError()
        return updated.encode('utf-8')

    @staticmethod
    def _prepare_files(spec, raw_files, candidates, now):
        channels = tuple(sorted(candidate.channel_id for candidate in candidates))
        if not 1 <= len(channels) <= _MAX_CANDIDATES or len(set(channels)) != len(channels):
            raise ValueError()
        env_raw = raw_files[spec.env_file]
        env_text = env_raw.decode('utf-8')
        env = legacy.parse_env(env_text)
        if legacy._allowlist(env.get('BUZZ_ACP_CHANNELS', '')):
            raise ValueError()
        lines = env_text.splitlines(keepends=True)
        indexes = [i for i, line in enumerate(lines) if legacy.CHANNELS_LINE.fullmatch(line.rstrip('\r\n'))]
        if len(indexes) != 1:
            raise ValueError()
        idx, line = indexes[0], lines[indexes[0]]
        ending = line[len(line.rstrip('\r\n')):]
        match = legacy.CHANNELS_LINE.fullmatch(line.rstrip('\r\n'))
        raw_value = match.group(2).strip()
        quote = raw_value[0] if len(raw_value) >= 2 and raw_value[0] == raw_value[-1] and raw_value[0] in "'\"" else ''
        lines[idx] = f"{match.group(1)}BUZZ_ACP_CHANNELS={quote}{','.join(channels)}{quote}{ending}"
        env_after = ''.join(lines).encode()
        prompt = raw_files[spec.prompt_file].decode('utf-8')
        begin, end = legacy.PROMPT_BEGIN, legacy.PROMPT_END
        if prompt.count(begin) != 1 or prompt.count(end) != 1 or prompt.index(begin) >= prompt.index(end):
            raise ValueError()
        body = prompt[prompt.index(begin) + len(begin):prompt.index(end)]
        if any(channel in body for channel in channels):
            raise ValueError()
        additions = '\n'.join(legacy.prompt_row(channel, {'has_list': False}, str(uuid.uuid4()), now)
                              for channel in channels)
        pos = prompt.index(end)
        prefix = prompt[:pos]
        prompt_after = (prefix + ('' if prefix.endswith('\n') else '\n')
                        + additions + '\n' + prompt[pos:]).encode()
        responsible_after = OwnHomeAdmissionCoordinator._responsible_update(
            raw_files[spec.responsible_file], channels)
        if (tuple(legacy._allowlist(legacy.parse_env(env_after.decode()).get('BUZZ_ACP_CHANNELS', '')))
                != channels or json.loads(responsible_after).get('channels') != list(channels)
                or prompt_after.decode().count(begin) != 1 or prompt_after.decode().count(end) != 1):
            raise ValueError()
        return channels, {spec.env_file: env_after, spec.prompt_file: prompt_after,
                          spec.responsible_file: responsible_after}

    @staticmethod
    def _channel_rows(candidates):
        rows = []
        for row in sorted(candidates, key=lambda item: item.channel_id):
            rows.append({
                'channel_id': row.channel_id, 'chat_id': row.chat_id, 'chat_ref': row.chat_ref,
                'source_kind': 'own_approval', 'approval_id': row.approval_id,
                'approval_hash': row.approval_hash, 'mirror_pubkey': row.mirror_pubkey,
                'mirror_owner_pubkey': row.mirror_owner_pubkey, 'claimed_at': row.claimed_at,
                'claim_event_id': row.claim_event_id, 'policy_event_id': row.agent_policy_id,
                'roster_event_id': row.roster_event_id,
                'authorization_hash': _json_hash((row.approval_id, row.approval_hash,
                    row.claim_event_id, row.agent_policy_id, row.roster_event_id,
                    row.channel_id, row.chat_ref, row.claimed_at)),
            })
        return tuple(rows)

    def _receipt(self, result, admission, rows, record):
        from .own_home_current_sources import CurrentSourcesReceipt, CurrentSourcesResult
        if type(result) is not CurrentSourcesResult or result.status != 'verified':
            raise ValueError()
        receipt = result.receipt
        now = self._now()
        if (type(receipt) is not CurrentSourcesReceipt
                or type(receipt.checked_at) is not int or type(receipt.expires_at) is not int
                or not 0 <= receipt.checked_at <= now < receipt.expires_at
                or receipt.expires_at > receipt.checked_at + 30):
            raise ValueError()
        expected = (admission.admission_id, admission.snapshot_hash, admission.proposed_channels_hash,
                    record.pubkey, record.owner_pubkey, record.app_id,
                    tuple(row['channel_id'] for row in rows), tuple(row['chat_ref'] for row in rows),
                    tuple(row['approval_id'] for row in rows), tuple(row['policy_event_id'] for row in rows),
                    tuple(row['claim_event_id'] for row in rows), tuple(row['roster_event_id'] for row in rows),
                    admission.catalog_hash, admission.env_after_hash,
                    admission.profile_hash, admission.legacy_join_hash)
        observed = (receipt.admission_id, receipt.snapshot_hash, receipt.proposed_channels_hash,
                    receipt.agent_pubkey, receipt.owner_pubkey, receipt.app_id,
                    receipt.channel_ids, receipt.chat_refs, receipt.approval_ids, receipt.policy_ids,
                    receipt.claim_ids, receipt.roster_ids, receipt.catalog_hash, receipt.env_hash,
                    receipt.profile_hash, receipt.legacy_join_hash)
        if observed != expected or receipt.current_policy_ids != receipt.policy_ids:
            raise ValueError()
        for values in (receipt.current_policy_ids, receipt.current_profile_ids,
                       receipt.current_roster_ids, receipt.current_claim_ids):
            if (type(values) is not tuple or len(values) != len(rows)
                    or any(type(value) is not str or not _HEX.fullmatch(value) for value in values)):
                raise ValueError()
        if (type(receipt.heartbeats) is not tuple or len(receipt.heartbeats) != len(rows)
                or any(type(heartbeat) is not int or heartbeat < row['claimed_at']
                       or heartbeat > now + gs.RELAY_CLOCK_SKEW_SECONDS
                       for heartbeat, row in zip(receipt.heartbeats, rows))):
            raise ValueError()
        return receipt

    async def _current_sources(self, admission, rows, record, reader, bot):
        from .own_home_current_sources import OwnHomeCurrentSources
        verifier = OwnHomeCurrentSources(self.store, self.catalog_path, self.legacy_join_path,
            reader=reader, bot=bot, clock=self.clock)
        result = await verifier.verify(admission.admission_id)
        self._receipt(result, admission, rows, record)
        return result

    def _sql_guard(self, admission, rows, record, spec, restart_id, *, pinned=None, source=None):
        """Pure owning-loop authorization; no file/native/process IO or await."""
        self._sql_identity(record, spec)
        current = self.store.own_home_admission(admission.admission_id)
        saved_rows = self.store.own_home_admission_channels(admission.admission_id)
        link = self.store.own_home_admission_restart(admission.admission_id)
        restart = self.store.restart_record(restart_id)
        if (current != admission or current.state != 'unknown'
                or self.store.active_own_home_admission(record.pubkey) != current
                or _json_hash(asdict(spec)) != admission.agent_spec_hash
                or admission.scope_hash != _json_hash({'spec': asdict(spec),
                    'channels': tuple(row['channel_id'] for row in rows)})
                or len(saved_rows) != len(rows)
                or tuple({key: getattr(row, key) for key in expected}
                         for row, expected in zip(saved_rows, rows)) != rows
                or link is None or link.restart_operation_id != restart_id
                or link.scope_hash != admission.scope_hash
                or restart is None or restart.state != 'unknown'
                or (restart.agent_id, restart.scope_hash, restart.protectedfiles_hash,
                    restart.old_process, restart.new_process) !=
                   (record.pubkey, admission.scope_hash, admission.protectedfiles_hash,
                    admission.old_process, pinned)):
            raise ValueError()
        if source is not None:
            self._receipt(source, admission, rows, record)

    def _handoff(self, call, operation=None):
        """Bounded worker→loop authorization; late callbacks never run SQL."""
        answer = Future()
        deadline = time.monotonic() + 5
        def on_loop():
            if answer.done():
                return
            accepted = False
            try:
                if (time.monotonic() < deadline and
                        (operation is None or not operation.done() and not operation.cancelling())):
                    accepted = call() is True
            except Exception:
                pass
            try:
                if not answer.done():
                    answer.set_result(accepted)
            except Exception:
                pass
        try:
            self._loop.call_soon_threadsafe(on_loop)
            return answer.result(timeout=5) is True
        except Exception:
            answer.cancel()
            return False

    def _replace(self, admission, rows, catalog, record, spec, staged, path, updated,
                 restart_id, candidates, operation):
        """One original CAS; staged bytes are not a current-source verdict."""
        staged_channels = tuple(legacy._allowlist(legacy.parse_env(
            staged[spec.env_file].decode()).get('BUZZ_ACP_CHANNELS', '')))
        first = agent_catalog.load(self.catalog_path, legacy_join_path=self.legacy_join_path)
        selected = self._record_from_catalog(first, record.pubkey, staged_channels)
        if (first.catalog_sha256 != catalog.catalog_sha256
                or first.legacy_join_sha256 != catalog.legacy_join_sha256
                or selected != replace(record, channels=staged_channels,
                                       env_sha256=_sha(staged[spec.env_file]))):
            raise ValueError()
        self._match_spec(selected, spec)
        JoinEffects._timer_excluded(spec)
        ops = OwnHomeRestartAdapter(self.operations, spec)
        if (self._read_files(spec) != staged or ops.capture(())[0] != admission.old_process
                or not ops.idle() or not ops.idle() or self._read_files(spec) != staged
                or agent_catalog.load(self.catalog_path,
                    legacy_join_path=self.legacy_join_path) != first):
            raise ValueError()
        JoinEffects._timer_excluded(spec)
        def authorize():
            self._sql_guard(admission, rows, record, spec, restart_id)
            now = self._now()
            if any(not candidate.checked_at <= now < candidate.expires_at for candidate in candidates):
                raise ValueError()
            return True
        if not self._handoff(authorize, operation):
            raise ValueError()
        ProtectedFiles().replace(path, staged[path], updated)

    def _dispatch(self, admission, rows, catalog, record, spec, updates, channels,
                  restart_id, source, operation):
        # The final worker sees actual files, old process and both idle checks.
        # Only the same live original reservation can reach this method; reopen
        # uses readback and can never dispatch, adopt or pin a new invocation.
        self._physical(catalog, record, spec, updates, channels, (), admission.old_process)
        def authorize():
            self._sql_guard(admission, rows, record, spec, restart_id, source=source)
            return True
        if not self._handoff(authorize, operation):
            raise ValueError()
        transport_ack = False
        try:
            transport_ack = self.operations.restart(spec.unit) is None
        except Exception:
            pass  # Lost/failed dispatch remains UNKNOWN even with a later tuple.
        replacement, _ = OwnHomeRestartAdapter(self.operations, spec).capture(channels)
        if (replacement.invocation == admission.old_process.invocation or
                (replacement.pid, replacement.start) ==
                (admission.old_process.pid, admission.old_process.start)):
            raise ValueError()
        def pin():
            self._sql_guard(admission, rows, record, spec, restart_id)
            return self.store.pin_restart(restart_id, record.pubkey, admission.scope_hash,
                admission.protectedfiles_hash, replacement, now=self._now())
        # Preserve the first observed live tuple even when the caller cancels
        # dispatched IO. Pinning records metadata only, never readiness/ACK.
        if not self._handoff(pin):
            raise ValueError()
        return replacement, transport_ack

    def _bind_loop(self):
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
        return self._loop is loop

    async def admit(self, agent_pubkey):
        if (type(agent_pubkey) is not str or not _HEX.fullmatch(agent_pubkey)
                or not self._bind_loop() or agent_pubkey in self._active):
            return _safe_result('pending')
        self._active.add(agent_pubkey)
        lock = _OwnedLock()
        closed = False
        admission_id, channels, committed = '', (), False
        try:
            catalog, record, spec, reader, bot = await self._select(agent_pubkey)
            self._sql_identity(record, spec)
            await thread_call(lock.acquire, spec)
            ops = OwnHomeRestartAdapter(self.operations, spec)
            if not await thread_call(ops.idle):
                return _safe_result('pending')
            old_process, _ = await thread_call(ops.capture, ())
            initial_files = await thread_call(self._read_files, spec)
            await thread_call(JoinEffects._timer_excluded, spec)
            discovery = own_admission_discovery.OwnAdmissionDiscovery(
                self.catalog_path, self.legacy_join_path, record=record, reader=reader,
                bot=bot, clock=self.clock)
            found = await discovery.discover()
            if found.status != 'discovered' or not found.candidates:
                return _safe_result('pending')
            channels, updates = await thread_call(self._prepare_files, spec, initial_files,
                                                  found.candidates, self._now())
            rows = await thread_call(self._channel_rows, found.candidates)
            await thread_call(self._physical, catalog, record, spec, initial_files, (), (), old_process)
            self._sql_identity(record, spec)
            now = self._now()
            if any(type(candidate.checked_at) is not int or type(candidate.expires_at) is not int
                   or not 0 <= candidate.checked_at <= now < candidate.expires_at
                   or candidate.expires_at > candidate.checked_at + 30
                   for candidate in found.candidates):
                return _safe_result('pending')
            admission_id = secrets.token_hex(32)
            restart_id = uuid.uuid4().hex  # Store restart ident permits UUID32.
            scope_hash = _json_hash({'spec': asdict(spec), 'channels': channels})
            snapshot_hash = _json_hash(list(rows))
            protected_hash = self._file_digest(initial_files)
            with self.store.transaction():
                self._sql_identity(record, spec)
                reserved = self.store.reserve_own_home_admission(
                    admission_id=admission_id, agent_id=agent_pubkey, snapshot_hash=snapshot_hash,
                    scope_hash=scope_hash, protectedfiles_hash=protected_hash,
                    catalog_hash=catalog.catalog_sha256, legacy_join_hash=catalog.legacy_join_sha256,
                    profile_hash=record.profile_sha256, env_before_hash=_sha(initial_files[spec.env_file]),
                    env_after_hash=_sha(updates[spec.env_file]), agent_spec_hash=_json_hash(asdict(spec)),
                    prior_channels_hash=_channels_digest(()), proposed_channels_hash=_channels_digest(channels),
                    approval_set_hash=_json_hash([row['approval_id'] for row in rows]),
                    old_process=old_process, channels=rows, now=now)
                if not reserved.created or not self.store.mark_own_home_admission_unknown(
                        admission_id, snapshot_hash, now=now):
                    raise ValueError()
                restart = self.store.reserve_restart(
                    restart_id, agent_pubkey, scope_hash, protected_hash, old_process, now=now)
                if (not restart.created or not self.store.mark_restart_unknown(
                        restart_id, agent_pubkey, scope_hash, protected_hash, now=now)
                        or not self.store.link_own_home_admission_restart(
                            admission_id, restart_id, scope_hash=scope_hash, now=now)):
                    raise ValueError()
                admission = self.store.own_home_admission(admission_id)
                self._sql_guard(admission, rows, record, spec, restart_id)
            committed = True
            # UNKNOWN committed before each original CAS, never replayed by readback.
            staged = dict(initial_files)
            for path in sorted(updates):
                await thread_call(self._replace, admission, rows, catalog, record, spec, staged,
                    path, updates[path], restart_id, found.candidates, asyncio.current_task())
                staged[path] = updates[path]
            source = await self._current_sources(admission, rows, record, reader, bot)
            self._sql_guard(admission, rows, record, spec, restart_id, source=source)
            replacement, transport_ack = await thread_call(self._dispatch, admission, rows, catalog,
                record, spec, updates, channels, restart_id, source, asyncio.current_task())
            self._sql_guard(admission, rows, record, spec, restart_id, pinned=replacement)
            if not transport_ack:
                return _safe_result('unknown', admission_id, channels)
            # Reap owned lock cleanup BEFORE final physical/source observation,
            # as required by actual Store.ack_restart. Active stays held throughout.
            await thread_call(lock.close)
            closed = True
            source = await self._current_sources(admission, rows, record, reader, bot)
            final_catalog, _, observed = await thread_call(self._physical, catalog, record, spec,
                updates, channels, channels, replacement, journal=True)
            if observed != replacement:
                raise ValueError()
            stamp = self._now()
            receipt_hash = _json_hash((admission_id, restart_id,
                (replacement.pid, replacement.start, replacement.invocation), channels,
                self._file_digest(updates), final_catalog.catalog_sha256))
            with self.store.transaction():
                self._sql_guard(admission, rows, record, spec, restart_id,
                                pinned=replacement, source=source)
                if not self.store.ack_restart(restart_id, agent_pubkey, scope_hash,
                        protected_hash, replacement, now=stamp):
                    raise ValueError()
                if not self.store.ack_own_home_admission(admission_id,
                        snapshot_hash=snapshot_hash, restart_operation_id=restart_id,
                        receipt_hash=receipt_hash, now=stamp):
                    raise ValueError()
            return _safe_result('admitted', admission_id, channels)  # No await after ACK.
        except asyncio.CancelledError:
            raise
        except Exception:
            return _safe_result('unknown' if committed else 'pending',
                                admission_id if committed else '', channels if committed else ())
        finally:
            try:
                if not closed:
                    try:
                        await thread_call(lock.close)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        pass
            finally:
                self._active.discard(agent_pubkey)

    async def readback(self, admission_id):
        """Observe only the original/pinned tuple; never dispatch, pin, write or ACK."""
        if (type(admission_id) is not str or not _HEX.fullmatch(admission_id)
                or not self._bind_loop()):
            return _safe_result('pending')
        try:
            admission = self.store.own_home_admission(admission_id)
            link = self.store.own_home_admission_restart(admission_id)
            if type(admission) is not OwnHomeAdmissionRecord or link is None:
                return _safe_result('pending', admission_id)
            saved = self.store.own_home_admission_channels(admission_id)
            rows = tuple({key: getattr(row, key) for key in (
                'channel_id', 'chat_id', 'chat_ref', 'source_kind', 'approval_id', 'approval_hash',
                'mirror_pubkey', 'mirror_owner_pubkey', 'claimed_at', 'claim_event_id',
                'policy_event_id', 'roster_event_id', 'authorization_hash')} for row in saved)
            channels = tuple(row['channel_id'] for row in rows)
            if not 1 <= len(channels) <= _MAX_CANDIDATES:
                raise ValueError()
            catalog, current, spec, reader, bot = await self._select(admission.agent_id, channels)
            if (catalog.catalog_sha256 != admission.catalog_hash
                    or catalog.legacy_join_sha256 != admission.legacy_join_hash
                    or current.profile_sha256 != admission.profile_hash
                    or current.env_sha256 != admission.env_after_hash
                    or _json_hash(asdict(spec)) != admission.agent_spec_hash
                or admission.scope_hash != _json_hash({'spec': asdict(spec),
                    'channels': tuple(row['channel_id'] for row in rows)})
                    or _json_hash(list(rows)) != admission.snapshot_hash):
                raise ValueError()
            self._sql_identity(current, spec)
            restart = self.store.restart_record(link.restart_operation_id)
            if (restart is None or link.scope_hash != admission.scope_hash
                    or (restart.agent_id, restart.scope_hash, restart.protectedfiles_hash,
                        restart.old_process) != (admission.agent_id, admission.scope_hash,
                                                 admission.protectedfiles_hash, admission.old_process)):
                raise ValueError()
            if admission.state == 'acked':
                # A prior ACK is historical metadata, not current source readiness.
                if restart.state != 'acked' or restart.new_process is None:
                    raise ValueError()
                return AdmissionResult('admitted', admission_id, channels,
                    '这是原始已完成记录的历史读回，不代表当前来源或进程就绪。' + NOTICE)
            if admission.state != 'unknown':
                raise ValueError()
            source = await self._current_sources(admission, rows, current, reader, bot)
            before = await thread_call(self._read_files, spec)
            pinned = restart.new_process
            await thread_call(self._physical, catalog, current, spec, before, channels,
                channels if pinned is not None else (), pinned or admission.old_process,
                journal=pinned is not None)
            # Readback still never upgrades UNKNOWN even with full observed truth.
            self._sql_guard(admission, rows, current, spec, link.restart_operation_id,
                            pinned=pinned, source=source)
            return _safe_result('unknown', admission_id, channels)
        except asyncio.CancelledError:
            raise
        except Exception:
            return _safe_result('unknown', admission_id)
