"""PRIVATE unexecuted tests-first proposal for physical own-home admission.

The signed discovery and protected files use the actual fixture parsers. Relay
and native HTTPS and the systemctl/journal subprocess are lower-I/O boundaries.
A temporary child is a real owned process read via actual /proc by
ScopedProcessOps; all children are terminated and reaped during cleanup.
"""
import asyncio
import concurrent.futures
from dataclasses import replace as dataclass_replace
import fcntl
import hashlib
import re
import importlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(TESTS))
import test_hostd_own_admission_discovery as source_fixture
import test_hostd_remote_mapping as signed_fixture
from hostd import agent_catalog, remote_approval, remote_proofs, store
from hostd.agent_operations import ScopedProcessOps
from hostd.join_effects import AgentSpec, ProtectedFiles
import buzz_agent_join_requests as legacy
import buzz_feishu_group_sync as gs


async def wait_thread_event(event, timeout):
    deadline = asyncio.get_running_loop().time() + timeout
    while not event.is_set() and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.01)
    return event.is_set()


class OwnedChildSystemdBoundary:
    """Only systemctl/journal output is simulated; the child process is real."""
    def __init__(self, case, env_reader, unit):
        self.case = case
        self.env_reader = env_reader
        self.unit = unit
        self.lose_restart_response = False
        self.omit_journal_channel = None
        self.child = None
        self.invocation = 'a' * 32
        self.journal = set()
        self.journal_reads = []
        self.restart_calls = 0
        self.on_restart = None
        self.restart_callback_error = None
        self.block_restart = False
        self.restart_entered = threading.Event()
        self.restart_release = threading.Event()
        self.stdin_fd = None
        self.busy_children = []
        case.addCleanup(self.close_all)
        self.spawn()

    def raise_restart_callback_error(self):
        # The coordinator correctly keeps a failed transport UNKNOWN. Surface
        # a fixture callback failure on the owner thread before status assertion
        # masks its original traceback; never synthesize a successful restart.
        if self.restart_callback_error is not None:
            exc, traceback = self.restart_callback_error
            raise exc.with_traceback(traceback)

    def spawn(self):
        self.invocation = ('%032x' % (int(self.invocation, 16) + 1))
        env = {**self.env_reader(), 'PATH': '/usr/bin:/bin', 'INVOCATION_ID': self.invocation}
        read_fd, write_fd = os.pipe()
        try:
            self.child = subprocess.Popen([sys.executable, '-c', 'import sys; sys.stdin.buffer.read()'],
                stdin=read_fd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env, close_fds=True)
        except BaseException:
            os.close(read_fd); os.close(write_fd)
            raise
        else:
            os.close(read_fd)
            self.stdin_fd = write_fd
        try:
            self._write_cgroup_procs()
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.stdin_fd is not None:
            os.close(self.stdin_fd)
            self.stdin_fd = None
        child = self.child
        if child is not None:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
        self.child = None

    def _write_cgroup_procs(self):
        if hasattr(self, 'cgroup_procs_path'):
            pids = ([self.child.pid] if self.child is not None and self.child.poll() is None else [])
            pids.extend(proc.pid for proc, _fd in self.busy_children if proc.poll() is None)
            self.cgroup_procs_path.write_text(''.join(str(pid) + '\n' for pid in pids))

    def add_busy_process(self):
        read_fd, write_fd = os.pipe()
        try:
            proc = subprocess.Popen([sys.executable, '-c', 'import sys; sys.stdin.buffer.read()'],
                stdin=read_fd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                env={'PATH': '/usr/bin:/bin'}, close_fds=True)
        except BaseException:
            os.close(read_fd); os.close(write_fd)
            raise
        os.close(read_fd)
        self.busy_children.append((proc, write_fd))
        self._write_cgroup_procs()
        return proc.pid

    def remove_busy_process(self, pid):
        kept = []
        for proc, fd in self.busy_children:
            if proc.pid != pid:
                kept.append((proc, fd))
                continue
            os.close(fd)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill(); proc.wait(timeout=5)
        self.busy_children = kept
        self._write_cgroup_procs()

    def close_all(self):
        self.close()
        for proc, fd in self.busy_children:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill(); proc.wait(timeout=5)
        self.busy_children = []
        self._write_cgroup_procs()

    def run(self, argv, **kwargs):
        self.case.assertIn(argv[0], (legacy.SYSTEMCTL, legacy.JOURNALCTL))
        self.case.assertIs(kwargs.get('capture_output'), True)
        self.case.assertIs(kwargs.get('text'), True)
        self.case.assertIs(kwargs.get('check'), False)
        self.case.assertIs(type(kwargs.get('timeout')), int)
        self.case.assertGreater(kwargs['timeout'], 0)
        self.case.assertLessEqual(kwargs['timeout'], 10)
        if argv[0] == legacy.SYSTEMCTL:
            prefix = [legacy.SYSTEMCTL, '--user']
            load = prefix + ['show', self.unit, '-p', 'LoadState', '-p', 'ActiveState']
            pid = prefix + ['show', self.unit, '-p', 'MainPID', '--value']
            invocation = prefix + ['show', self.unit, '-p', 'InvocationID', '--value']
            cgroup = prefix + ['show', self.unit, '-p', 'ControlGroup', '--value']
            restart = prefix + ['restart', self.unit]
            if argv == load:
                return subprocess.CompletedProcess(argv, 0, 'LoadState=loaded\nActiveState=active\n', '')
            if argv == pid:
                return subprocess.CompletedProcess(argv, 0, str(self.child.pid) + '\n', '')
            if argv == invocation:
                return subprocess.CompletedProcess(argv, 0, self.invocation + '\n', '')
            if argv == cgroup:
                return subprocess.CompletedProcess(argv, 0, '/hostd-test-own-home\n', '')
            if argv == restart:
                self.restart_calls += 1
                self.restart_entered.set()
                if self.on_restart:
                    try:
                        self.on_restart()
                    except BaseException as exc:
                        # Preserve the actual fixture assertion/exception while
                        # retaining the original failed restart boundary behavior.
                        self.restart_callback_error = (exc, exc.__traceback__)
                        raise
                if self.block_restart and not self.restart_release.wait(kwargs['timeout'] - 0.25):
                    raise subprocess.TimeoutExpired(argv, kwargs['timeout'])
                self.close()
                self.spawn()
                channels = self.env_reader().get('BUZZ_ACP_CHANNELS', '')
                self.journal = set(filter(None, channels.split(',')))
                if self.omit_journal_channel is not None:
                    self.journal.discard(self.omit_journal_channel)
                if self.lose_restart_response:
                    self.journal.clear()
                    raise subprocess.TimeoutExpired(argv, kwargs['timeout'])
                return subprocess.CompletedProcess(argv, 0, '', '')
        if argv[0] == legacy.JOURNALCTL:
            self.journal_reads.append(tuple(argv))
            expected = [legacy.JOURNALCTL, '--user', '-u', self.unit,
                '_PID=' + str(self.child.pid), '_SYSTEMD_INVOCATION_ID=' + self.invocation,
                '-o', 'cat', '--no-pager', '--lines=1000']
            self.case.assertEqual(argv, expected)
            return subprocess.CompletedProcess(argv, 0,
                '\n'.join('subscribed to channel ' + value for value in sorted(self.journal)), '')
        raise AssertionError('unapproved process command/argv')


class PhysicalWorld(source_fixture.AdmissionWorld):
    def __init__(self, case):
        super().__init__(case)
        # Readonly discovery intentionally had no physical channel-file state.
        # Retain its canaries while adding the legitimate empty initial fields.
        original_prompt = self.actual_record.prompt_file.read_text()
        case.assertIn('SYNTHETIC_PRIVATE_PROMPT_DO_NOT_READ', original_prompt)
        prompt = original_prompt + '\n' + legacy.PROMPT_BEGIN + '\n' + legacy.PROMPT_END + '\n'
        self.owned(self.actual_record.prompt_file, prompt)
        responsible = json.loads(self.actual_record.responsible_config.read_text())
        case.assertNotIn('channels', responsible)
        original_people_file = responsible['people_file']
        self.owned(self.actual_record.responsible_config,
                   json.dumps(dict(responsible, channels=[]), separators=(',', ':')))
        case.assertEqual(prompt.count(legacy.PROMPT_BEGIN), 1)
        case.assertEqual(prompt.count(legacy.PROMPT_END), 1)
        case.assertEqual(prompt.split(legacy.PROMPT_BEGIN)[1].split(legacy.PROMPT_END)[0].strip(), '')
        parsed = json.loads(self.actual_record.responsible_config.read_text())
        case.assertEqual(parsed['channels'], [])
        case.assertEqual(parsed['people_file'], original_people_file)
        case.assertEqual(agent_catalog.load(self.catalog_path,
            legacy_join_path=self.legacy_path).records[0], self.actual_record)
        self.db = store.Store(self.root / 'hostd.sqlite3')
        case.addCleanup(self.db.close)
        self.db.register_agent(self.actual_record.pubkey,
            owner_pubkey=self.actual_record.owner_pubkey, app_id=self.actual_record.app_id,
            config_path=str(self.actual_record.env_file), now=self.now)
        self.timer = self.root / 'timer.json'
        self.owned(self.timer, json.dumps({'version': 1, 'owner_pubkey': self.actual_record.owner_pubkey,
                                           'agents': []}))
        self.state_dir = self.root / 'timer-state'
        self.state_dir.mkdir(mode=0o700)
        self.spec = AgentSpec(self.actual_record.pubkey, self.actual_record.owner_pubkey,
            self.actual_record.app_id, str(self.actual_record.env_file), str(self.actual_record.prompt_file),
            str(self.actual_record.responsible_config), self.actual_record.unit, str(self.timer),
            str(self.state_dir), reader_app_id=self.actual_record.app_id,
            reader_config_dir=str(self.actual_record.lark_config_dir),
            reader_data_dir=str(self.actual_record.lark_data_dir))
        self.loop = None
        self.block_final_member = False
        self.final_member_entered = threading.Event()
        self.final_member_release = threading.Event()
        self.replace_process_at_final_member = False
        self.system = OwnedChildSystemdBoundary(case, self._process_env, self.actual_record.unit)
        self.ops = ScopedProcessOps(runner=self.system.run, timeout=10,
                                    base_env={'PATH': '/usr/bin:/bin'}, cgroup_root=self._test_cgroup())

    def request(self, *args, **kwargs):
        path = args[4]
        if path == '/open-apis/application/v6/scopes':
            from hostd.bot_clients import READ_SCOPE_GROUPS
            self.api_calls.append((args[3], path, kwargs.get('params'), kwargs.get('data')))
            names = set(READ_SCOPE_GROUPS) | {'im:message:send'}
            return {'ok': True, 'identity': 'bot', 'data': {'scopes': [
                {'scope_name': name, 'scope_type': 'tenant', 'grant_status': 1}
                for name in sorted(names)]}}
        if path == '/open-apis/im/v1/chats':
            self.api_calls.append((args[3], path, kwargs.get('params'), kwargs.get('data')))
            self.assertIn(kwargs.get('chat_id'), (None, getattr(self, 'second_chat', None)))
            return {'ok': True, 'identity': 'bot', 'data': {'items': self.chat_rows,
                'has_more': False, 'page_token': ''}}
        if path.endswith('/members/list') and self.system.restart_calls:
            if self.replace_process_at_final_member:
                self.replace_process_at_final_member = False
                self.system.close()
                self.system.spawn()
            if self.block_final_member:
                self.final_member_entered.set()
                self.assertTrue(self.final_member_release.wait(600),
                                'final source HTTP boundary must be released and joined')
        if getattr(self, 'second_chat', None) and path == (
                '/open-apis/im/v1/chats/' + self.second_chat + '/members/list'):
            self.api_calls.append((args[3], path, kwargs.get('params'), kwargs.get('data')))
            return {'ok': True, 'identity': 'bot', 'data': {'users': [],
                'bots': [{'member_id': 'ou_own', 'app_id': self.actual_record.app_id}],
                'user_total': 0, 'bot_total': 1, 'truncations': [],
                'has_more': False, 'page_token': ''}}
        return super().request(*args, **kwargs)

    def _process_env(self):
        from buzz_agent_join_requests import parse_env
        return parse_env(self.actual_record.env_file.read_text())

    def _test_cgroup(self):
        # The owned child PID is observed from /proc; only its test cgroup listing
        # is synthetic. It makes the inherited idle check fail closed by default
        # on hosts without a disposable real user systemd unit.
        root = self.root / 'cgroup'
        group = root / 'hostd-test-own-home'
        group.mkdir(parents=True, mode=0o700)
        self.system.cgroup_procs_path = group / 'cgroup.procs'
        self.system.cgroup_procs_path.write_text(str(self.system.child.pid) + '\n')
        return root

    def on_loop(self, action):
        self.case.assertIsNotNone(self.loop, 'test must pin the owning event loop before subprocess IO')
        result = concurrent.futures.Future()
        def invoke():
            try:
                result.set_result(action())
            except BaseException as exc:
                result.set_exception(exc)
        self.loop.call_soon_threadsafe(invoke)
        return result.result(timeout=8)

    def coordinator_for(self, db):
        self.case.assertIsNotNone(importlib.util.find_spec('hostd.own_admission'),
            'missing physical own-home coordinator; do not fabricate binding/agent_chat authority')
        module = importlib.import_module('hostd.own_admission')
        return module.OwnHomeAdmissionCoordinator(
            self.catalog_path, self.legacy_path, db,
            reader_factory=lambda record: self.reader, bot_factory=lambda record: self.bot,
            spec_getter=lambda pubkey: self.spec if pubkey == self.actual_record.pubkey else None,
            operations=self.ops, trusted_relays=(signed_fixture.ORIGIN,), clock=lambda: self.now)

    def coordinator(self):
        return self.coordinator_for(self.db)

    def add_second_signed_channel(self):
        channel, chat = '33333333-3333-4333-8333-333333333333', 'oc_second'
        self.second_channel, self.second_chat = channel, chat
        self.claims.append({'channel': channel, 'chat_ref': gs.chat_ref(chat),
                            'claimed_at': self.now - 100, 'heartbeat': self.now})
        self.chat_rows.append({'chat_id': chat, 'name': 'SYNTHETIC_SECOND_CHAT'})
        self.directory = self.metadata()
        body = json.loads(self.approval['content'])
        body.update(channel_id=channel, chat_ref=gs.chat_ref(chat), request_id='JOIN-8765dcba',
                    request_created_at=self.now - 20, request_deadline=self.now - 20 + 7 * 86400,
                    card_generation=2, decision_at=self.now - 1)
        body['card_message_sha256'] = hashlib.sha256(b'SYNTHETIC_SECOND_CARD').hexdigest()
        body['decision_event_sha256'] = hashlib.sha256(b'SYNTHETIC_SECOND_DECISION').hexdigest()
        raw = json.dumps(body, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
        digest = hashlib.sha256(raw.encode()).hexdigest()
        event = gs.sign_event(signed_fixture.MIRROR_KEY, 30078,
            [['t', remote_approval.PREFIX], ['h', channel], ['p', body['agent_pubkey']],
             ['d', remote_approval.PREFIX + ':' + digest]], raw, self.now)
        self.events.append(event)
        return channel, chat

    def source_hashes(self):
        paths = (self.actual_record.env_file, self.actual_record.prompt_file,
                 self.actual_record.responsible_config, self.catalog_path, self.legacy_path,
                 self.cfg / 'config.json')
        return {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}

    def no_authority_rows(self):
        for table in ('binding', 'agent_chat', 'remote_grant'):
            self.case.assertEqual(self.db.conn.execute('SELECT count(*) FROM ' + table).fetchone()[0], 0)


class OwnHomeAdmissionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.world = PhysicalWorld(self)

    async def test_control_zero_channel_catalog_is_real_but_not_a_remote_grant(self):
        w = self.world
        current = agent_catalog.load(w.catalog_path, legacy_join_path=w.legacy_path).records[0]
        self.assertEqual(current.channels, ())
        observed = w.ops.process(w.spec.unit)
        invocation = w.ops.invocation_id(w.spec.unit)
        self.assertEqual(observed[2].get('INVOCATION_ID'), invocation)
        self.assertEqual(observed[0], w.system.child.pid)
        self.assertGreater(observed[1], 0)
        result = await remote_proofs.RemoteProofs(w.catalog_path, w.legacy_path,
            record=current, reader=w.reader, bot=w.bot, clock=lambda: w.now).verify(w.target)
        self.assertEqual(result.status, 'pending')
        self.assertIsNone(result.authorization)
        self.assertIsNone(w.db.active_own_home_admission(current.pubkey))
        w.no_authority_rows()

    async def test_control_actual_signed_discovery_stays_metadata_only(self):
        w = self.world
        result = await w.adapter().discover()
        self.assertEqual(result.status, 'discovered')
        self.assertEqual(len(result.candidates), 1)
        self.assertFalse(result.readback()['grant_activated'])
        self.assertFalse(result.readback()['sending_ready'])
        self.assertIsNone(w.db.active_own_home_admission(w.actual_record.pubkey))
        w.no_authority_rows()

    async def test_control_second_actual_chat_and_signed_approval_are_discovered(self):
        w = self.world
        second_channel, second_chat = w.add_second_signed_channel()
        result = await w.adapter().discover()
        self.assertEqual(result.status, 'discovered')
        self.assertEqual({item.channel_id for item in result.candidates},
                         {signed_fixture.CHANNEL, second_channel})
        self.assertEqual({item.chat_id for item in result.candidates},
                         {signed_fixture.CHAT, second_chat})
        self.assertFalse(result.readback()['sending_ready'])
        w.no_authority_rows()

    async def test_missing_physical_coordinator_is_the_explicit_feature_red(self):
        self.assertIsNotNone(importlib.util.find_spec('hostd.own_admission'),
            'missing physical coordinator after readonly discovery and schema-10 journal')

    async def test_complete_signed_candidate_set_uses_one_real_process_restart_then_ack(self):
        w = self.world
        w.add_second_signed_channel()
        spec_before = w.spec
        before_pid = w.system.child.pid
        before_proc = w.ops.process(w.spec.unit)
        before_invocation = w.ops.invocation_id(w.spec.unit)
        result = await w.coordinator().admit(w.actual_record.pubkey)
        self.assertEqual(result.status, 'admitted')
        record = w.db.own_home_admission(result.admission_id)
        channels = w.db.own_home_admission_channels(result.admission_id)
        self.assertEqual(tuple(row.channel_id for row in channels), result.proposed_channels)
        self.assertEqual(len(channels), 2, 'all actual signed candidates are one batch')
        self.assertEqual(record.state, 'acked')
        self.assertEqual(w.system.restart_calls, 1)
        self.assertNotEqual(w.system.child.pid, before_pid)
        after_proc = w.ops.process(w.spec.unit)
        after_invocation = w.ops.invocation_id(w.spec.unit)
        self.assertNotEqual(before_proc[:2], after_proc[:2])
        self.assertNotEqual(before_invocation, after_invocation)
        self.assertEqual(w.spec, spec_before, 'the real AgentSpec remains unchanged')
        self.assertEqual(set(w._process_env()['BUZZ_ACP_CHANNELS'].split(',')),
                         {row.channel_id for row in channels})
        actual = agent_catalog.load(w.catalog_path, legacy_join_path=w.legacy_path).records[0]
        self.assertEqual(set(actual.channels), {row.channel_id for row in channels})
        w.no_authority_rows()

    async def test_unknown_and_original_restart_link_are_committed_before_restart_boundary(self):
        w = self.world
        observed = []
        def at_restart():
            def read_committed_state():
                record = w.db.active_own_home_admission(w.actual_record.pubkey)
                if record is None:
                    return None
                link = w.db.own_home_admission_restart(record.admission_id)
                restart = w.db.restart_record(link.restart_operation_id) if link else None
                channels = (w.db.own_home_admission_channels(record.admission_id)
                            if record is not None else ())
                return record, link, restart, channels, w.db.conn.in_transaction
            snapshot = w.on_loop(read_committed_state)
            self.assertIsNotNone(snapshot)
            record, link, restart, channels, in_transaction = snapshot
            self.assertEqual(record.state, 'unknown')
            self.assertIsNotNone(link)
            self.assertIsNotNone(restart)
            self.assertEqual(restart.state, 'unknown')
            self.assertEqual(len(channels), 1)
            self.assertFalse(in_transaction)
            observed.append(snapshot)
        w.loop = asyncio.get_running_loop()
        w.system.on_restart = at_restart
        result = await w.coordinator().admit(w.actual_record.pubkey)
        w.system.raise_restart_callback_error()
        self.assertEqual(result.status, 'admitted')
        self.assertEqual(len(observed), 1)

    async def test_changed_signed_native_or_catalog_snapshot_has_no_physical_effect(self):
        w = self.world
        before = w.source_hashes(); pid = w.system.child.pid
        w.on_member = lambda: w.mutate_approval(chat_ref='f' * 64)
        result = await w.coordinator().admit(w.actual_record.pubkey)
        self.assertEqual(result.status, 'pending')
        self.assertEqual(w.source_hashes(), before)
        self.assertEqual(w.system.child.pid, pid)
        self.assertEqual(w.system.restart_calls, 0)
        w.no_authority_rows()

    async def test_external_owned_file_writer_after_unknown_before_real_cas_wins(self):
        from unittest import mock
        from hostd import join_effects

        w = self.world
        w.loop = asyncio.get_running_loop()
        entered = threading.Event()
        release = threading.Event()
        paused = {'done': False, 'path': None}
        original = join_effects.ProtectedFiles.replace

        def barrier(files, path, expected, updated):
            if Path(path) in {w.actual_record.env_file, w.actual_record.prompt_file,
                              w.actual_record.responsible_config} and not paused['done']:
                paused['done'] = True
                paused['path'] = Path(path)
                entered.set()
                if not release.wait(600):
                    raise TimeoutError('test CAS barrier was not released')
            return original(files, path, expected, updated)

        task = None
        try:
            with mock.patch.object(join_effects.ProtectedFiles, 'replace', barrier):
                task = asyncio.create_task(w.coordinator().admit(w.actual_record.pubkey))
                self.assertTrue(await wait_thread_event(entered, 600),
                                'real CAS must be reached after UNKNOWN reservation')
                state = w.db.active_own_home_admission(w.actual_record.pubkey)
                self.assertIsNotNone(state)
                self.assertEqual(state.state, 'unknown')
                path = paused['path']
                before = path.read_bytes()
                if path == w.actual_record.responsible_config:
                    after = before + b' \n'
                elif path == w.actual_record.env_file:
                    after = before + b'\n# external writer\n'
                else:
                    after = before + b'\n<!-- external writer -->\n'
                await asyncio.to_thread(original, join_effects.ProtectedFiles(), path, before, after)
                release.set()
                result = await task
            self.assertEqual(result.status, 'unknown')
            self.assertEqual(path.read_bytes(), after,
                             'the external protected-file write must never be overwritten')
            self.assertEqual(w.system.restart_calls, 0)
            active = w.db.active_own_home_admission(w.actual_record.pubkey)
            self.assertIsNotNone(active)
            self.assertEqual(active.state, 'unknown')
            w.no_authority_rows()
        finally:
            release.set()
            if task is not None and not task.done():
                await asyncio.gather(task, return_exceptions=True)

    async def test_real_cgroup_busy_blocks_before_reservation_and_after_unknown_cas(self):
        from unittest import mock
        from hostd import join_effects

        w = self.world
        busy_pid = w.system.add_busy_process()
        self.assertTrue(w.ops.is_busy(w.spec.unit), 'real owned second child makes observed cgroup busy')
        before = await w.coordinator().admit(w.actual_record.pubkey)
        self.assertEqual(before.status, 'pending')
        self.assertIsNone(w.db.active_own_home_admission(w.actual_record.pubkey))
        self.assertEqual(w.system.restart_calls, 0)
        w.system.remove_busy_process(busy_pid)

        original = join_effects.ProtectedFiles.replace
        added = {'pid': None}
        def add_busy_after_real_cas(files, path, expected, updated):
            result = original(files, path, expected, updated)
            if added['pid'] is None and Path(path) in {
                    w.actual_record.env_file, w.actual_record.prompt_file,
                    w.actual_record.responsible_config}:
                added['pid'] = w.system.add_busy_process()
            return result

        with mock.patch.object(join_effects.ProtectedFiles, 'replace', add_busy_after_real_cas):
            after = await w.coordinator().admit(w.actual_record.pubkey)
        self.assertEqual(after.status, 'unknown')
        self.assertIsNotNone(added['pid'])
        active = w.db.active_own_home_admission(w.actual_record.pubkey)
        self.assertIsNotNone(active)
        self.assertEqual(active.state, 'unknown')
        self.assertTrue(w.ops.is_busy(w.spec.unit))
        self.assertEqual(w.system.restart_calls, 0)
        w.no_authority_rows()

    async def test_postrestart_source_change_blocks_ack_using_reloaded_actual_catalog(self):
        w = self.world
        w.system.on_restart = lambda: setattr(w, 'events', [])
        result = await w.coordinator().admit(w.actual_record.pubkey)
        self.assertEqual(result.status, 'unknown')
        actual = agent_catalog.load(w.catalog_path, legacy_join_path=w.legacy_path).records[0]
        self.assertEqual(set(actual.channels), set(w._process_env()['BUZZ_ACP_CHANNELS'].split(',')))
        self.assertNotEqual(result.status, 'admitted')
        record = w.db.own_home_admission(result.admission_id)
        self.assertEqual(record.state, 'unknown')
        w.no_authority_rows()

    async def test_missing_second_channel_journal_keeps_two_channel_admission_unknown(self):
        w = self.world
        _channel, second_chat = w.add_second_signed_channel()
        del second_chat
        w.system.omit_journal_channel = w.second_channel
        result = await w.coordinator().admit(w.actual_record.pubkey)
        self.assertEqual(result.status, 'unknown')
        self.assertEqual(w.system.restart_calls, 1)
        record = w.db.own_home_admission(result.admission_id)
        rows = w.db.own_home_admission_channels(result.admission_id)
        self.assertEqual(len(rows), 2)
        self.assertEqual(record.state, 'unknown')
        process = w.ops.process(w.spec.unit)
        invocation = w.ops.invocation_id(w.spec.unit)
        journal = w.ops.journal_invocation(w.spec.unit, process[0], invocation)
        self.assertIn('subscribed to channel ' + signed_fixture.CHANNEL, journal)
        self.assertNotIn('subscribed to channel ' + w.second_channel, journal)
        active = w.db.active_own_home_admission(w.actual_record.pubkey)
        self.assertIsNotNone(active)
        self.assertEqual(active.state, 'unknown')
        w.no_authority_rows()

    async def test_final_protected_file_drift_after_restart_keeps_admission_unknown(self):
        from hostd import join_effects

        w = self.world
        path = w.actual_record.responsible_config
        changed = []
        def external_file_change_at_restart():
            before = path.read_bytes()
            after = before + b' \n'
            join_effects.ProtectedFiles().replace(path, before, after)
            changed.append(after)
        w.system.on_restart = external_file_change_at_restart
        result = await w.coordinator().admit(w.actual_record.pubkey)
        self.assertEqual(result.status, 'unknown')
        self.assertEqual(w.system.restart_calls, 1)
        self.assertTrue(changed)
        self.assertEqual(path.read_bytes(), changed[0])
        record = w.db.own_home_admission(result.admission_id)
        self.assertEqual(record.state, 'unknown')
        active = w.db.active_own_home_admission(w.actual_record.pubkey)
        self.assertIsNotNone(active)
        self.assertEqual(active.state, 'unknown')
        w.no_authority_rows()

    async def test_unknown_reopen_is_readback_only_and_never_adopts_later_process(self):
        w = self.world
        w.system.lose_restart_response = True
        first = await w.coordinator().admit(w.actual_record.pubkey)
        self.assertEqual(first.status, 'unknown')
        pinned = w.db.own_home_admission_restart(first.admission_id)
        self.assertIsNotNone(pinned)
        restarts = w.system.restart_calls
        before = w.source_hashes()
        restart_before = w.db.restart_record(pinned.restart_operation_id).new_process
        channels_before = w.db.own_home_admission_channels(first.admission_id)
        w.system.close(); w.system.spawn()  # unrelated later invocation, outside coordinator dispatch
        later_pid = w.system.child.pid
        w.db.close()
        reopened = store.Store(w.root / 'hostd.sqlite3')
        w.case.addCleanup(reopened.close)
        w.db = reopened
        recovery = w.coordinator_for(reopened).readback(first.admission_id)
        observed = await recovery
        self.assertEqual(observed.status, 'unknown')
        self.assertEqual(w.system.restart_calls, restarts)
        self.assertEqual(w.source_hashes(), before)
        self.assertEqual(w.system.child.pid, later_pid)
        persisted = reopened.own_home_admission(first.admission_id)
        self.assertEqual(persisted.state, 'unknown')
        persisted_link = reopened.own_home_admission_restart(first.admission_id)
        self.assertIsNotNone(persisted_link, 'original UNKNOWN restart link must remain readable')
        self.assertEqual(persisted_link, pinned)
        self.assertEqual(reopened.own_home_admission_channels(first.admission_id), channels_before)
        self.assertEqual(reopened.restart_record(pinned.restart_operation_id).new_process, restart_before)

    async def test_pinned_unknown_reopen_with_current_sources_and_journal_is_readback_only(self):
        w = self.world
        w.add_second_signed_channel()
        current_events = list(w.events)
        w.system.on_restart = lambda: setattr(w, 'events', [])
        first = await w.coordinator().admit(w.actual_record.pubkey)
        self.assertEqual(first.status, 'unknown')
        w.events = current_events
        link = w.db.own_home_admission_restart(first.admission_id)
        self.assertIsNotNone(link)
        pinned = w.db.restart_record(link.restart_operation_id)
        self.assertIsNotNone(pinned.new_process,
                             'the changed process tuple must be durably pinned before uncertainty')
        expected_tuple = pinned.new_process
        observed = w.ops.process(w.spec.unit)
        actual_tuple = (observed[0], observed[1], w.ops.invocation_id(w.spec.unit))
        self.assertEqual(actual_tuple, (expected_tuple.pid, expected_tuple.start,
                                      expected_tuple.invocation))
        expected_channels = {signed_fixture.CHANNEL, w.second_channel}
        self.assertEqual(set(w._process_env()['BUZZ_ACP_CHANNELS'].split(',')), expected_channels)
        current = agent_catalog.load(w.catalog_path, legacy_join_path=w.legacy_path).records[0]
        self.assertEqual(set(current.channels), expected_channels)
        journal = w.ops.journal_invocation(w.spec.unit, observed[0], actual_tuple[2])
        self.assertIn('subscribed to channel ' + signed_fixture.CHANNEL, journal)
        self.assertIn('subscribed to channel ' + w.second_channel, journal)
        self.assertEqual(w.system.restart_calls, 1)
        before = w.source_hashes()
        admission_before = w.db.own_home_admission(first.admission_id)
        channels_before = w.db.own_home_admission_channels(first.admission_id)
        restart_before = w.db.restart_record(link.restart_operation_id)
        w.db.close()
        reopened = store.Store(w.root / 'hostd.sqlite3')
        w.case.addCleanup(reopened.close)
        w.db = reopened
        w.queries.clear()
        w.api_calls.clear()
        w.system.journal_reads.clear()
        recovery = w.coordinator_for(reopened).readback(first.admission_id)
        result = await recovery
        self.assertEqual(result.status, 'unknown')
        for key in ('grant_activated', 'sending_ready', 'physical_verified', 'live_verified'):
            self.assertFalse(result.readback()[key])
        self.assertEqual(w.system.restart_calls, 1)
        self.assertEqual(w.source_hashes(), before)
        self.assertEqual(w.db.own_home_admission(first.admission_id), admission_before)
        self.assertEqual(w.db.own_home_admission_channels(first.admission_id), channels_before)
        self.assertEqual(w.db.restart_record(link.restart_operation_id), restart_before)
        queried_ids = {value for query in w.queries for value in query.get('ids', ())}
        expected_ids = {value for row in channels_before for value in
                        (row.approval_id, row.policy_event_id, row.claim_event_id, row.roster_event_id)}
        self.assertTrue(expected_ids.issubset(queried_ids),
                        'readback must GET every original pinned signed source ID')
        paths = [call[1] for call in w.api_calls]
        self.assertIn('/open-apis/application/v6/scopes', paths,
                      'readback must GET current native own-bot scopes')
        self.assertIn('/open-apis/im/v1/chats/' + signed_fixture.CHAT + '/members/list', paths)
        self.assertIn('/open-apis/im/v1/chats/' + w.second_chat + '/members/list', paths)
        self.assertTrue(w.system.journal_reads, 'readback must GET the pinned invocation journal')
        self.assertEqual(w.system.journal_reads[-1], tuple([
            legacy.JOURNALCTL, '--user', '-u', w.spec.unit,
            '_PID=' + str(expected_tuple.pid), '_SYSTEMD_INVOCATION_ID=' + expected_tuple.invocation,
            '-o', 'cat', '--no-pager', '--lines=1000']))
        self.assertEqual(w.db.own_home_admission(first.admission_id).state, 'unknown')

    async def test_repeated_cancellation_during_restart_joins_io_and_retains_unknown(self):
        w = self.world
        w.loop = asyncio.get_running_loop()
        w.system.block_restart = True
        observed = []

        def at_restart():
            def committed_unknown():
                row = w.db.active_own_home_admission(w.actual_record.pubkey)
                link = w.db.own_home_admission_restart(row.admission_id) if row else None
                restart = w.db.restart_record(link.restart_operation_id) if link else None
                return (row.state if row else None, restart.state if restart else None,
                        w.db.conn.in_transaction)
            observed.append(w.on_loop(committed_unknown))

        w.system.on_restart = at_restart
        task = asyncio.create_task(w.coordinator().admit(w.actual_record.pubkey))
        try:
            self.assertTrue(await wait_thread_event(w.system.restart_entered, 600),
                            'dispatched restart must reach the real child boundary')
            task.cancel()
            await asyncio.sleep(0.03)
            task.cancel()
            await asyncio.sleep(0.03)
            self.assertFalse(task.done(), 'cancellation must wait for dispatched IO to join')
        finally:
            w.system.restart_release.set()
            if not task.done():
                await asyncio.gather(task, return_exceptions=True)
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(observed, [('unknown', 'unknown', False)])
        self.assertEqual(w.system.restart_calls, 1)
        active = w.db.active_own_home_admission(w.actual_record.pubkey)
        self.assertIsNotNone(active)
        self.assertEqual(active.state, 'unknown')
        lock_fd = os.open(w.state_dir / 'join.lock', os.O_RDWR | os.O_NOFOLLOW)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)
        w.no_authority_rows()

    async def test_concurrent_admissions_cannot_both_reserve_or_restart(self):
        async def event_set(event, timeout):
            end = asyncio.get_running_loop().time() + timeout
            while not event.is_set() and asyncio.get_running_loop().time() < end:
                await asyncio.sleep(0.02)
            return event.is_set()

        w = self.world
        w.system.block_restart = True
        first = asyncio.create_task(w.coordinator().admit(w.actual_record.pubkey))
        second = None
        try:
            entered = await event_set(w.system.restart_entered, 600)
            self.assertTrue(entered, 'first original operation must reach its bounded restart boundary')
            second = asyncio.create_task(w.coordinator().admit(w.actual_record.pubkey))
            done, _ = await asyncio.wait((second,), timeout=3)
            self.assertTrue(done, 'second attempt must finish while first holds UNKNOWN reservation')
            self.assertEqual(second.result().status, 'pending',
                             'the active UNKNOWN admission blocks another source/native traversal')
        finally:
            w.system.restart_release.set()
            tasks = [first] + ([second] if second is not None else [])
            results = await asyncio.gather(*tasks, return_exceptions=True)
        self.assertIsNotNone(second, 'second admission must start while original reservation is held')
        self.assertFalse(any(isinstance(item, BaseException) for item in results),
                         'both actual adapter paths must return a bounded result')
        admitted = [item for item in results if item.status == 'admitted']
        self.assertTrue(admitted, 'the authentic winning admission must complete')
        self.assertEqual(len({item.admission_id for item in admitted}), 1)
        self.assertEqual(w.system.restart_calls, 1)
        rows = w.db.conn.execute('SELECT admission_id,state FROM own_home_admission WHERE agent_id=?',
                                 (w.actual_record.pubkey,)).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['state'], 'acked')
        self.assertIsNone(w.db.active_own_home_admission(w.actual_record.pubkey))
        link = w.db.own_home_admission_restart(rows[0]['admission_id'])
        self.assertIsNotNone(link)
        self.assertEqual(w.db.restart_record(link.restart_operation_id).state, 'acked')
        w.no_authority_rows()

    async def test_sql_retirement_after_real_discovery_before_reservation_blocks_intent(self):
        from unittest import mock
        from hostd import own_admission_discovery

        w = self.world
        original = own_admission_discovery.OwnAdmissionDiscovery.discover
        observed = []

        async def retire_after_actual_discovery(adapter):
            result = await original(adapter)
            self.assertEqual(result.status, 'discovered')
            self.assertEqual(len(result.candidates), 1)
            w.db.conn.execute("UPDATE agent SET status='retired',updated_at=? WHERE pubkey=?",
                              (w.now + 1, w.actual_record.pubkey))
            observed.append(result)
            return result

        before = w.source_hashes()
        with mock.patch.object(own_admission_discovery.OwnAdmissionDiscovery,
                               'discover', retire_after_actual_discovery):
            result = await w.coordinator().admit(w.actual_record.pubkey)
        self.assertEqual(len(observed), 1, 'the actual signed/native source pass must complete first')
        self.assertEqual(result.status, 'pending')
        self.assertEqual(w.db.conn.execute('SELECT status FROM agent WHERE pubkey=?',
                         (w.actual_record.pubkey,)).fetchone()[0], 'retired')
        self.assertIsNone(w.db.active_own_home_admission(w.actual_record.pubkey))
        self.assertEqual(w.source_hashes(), before)
        self.assertEqual(w.system.restart_calls, 0)
        w.no_authority_rows()

    async def test_original_process_tuple_drift_after_real_file_cas_blocks_restart(self):
        from unittest import mock
        from hostd import join_effects

        w = self.world
        original = join_effects.ProtectedFiles.replace
        changed = []

        def replace_then_external_process_change(files, path, expected, updated):
            result = original(files, path, expected, updated)
            if not changed and Path(path) in {w.actual_record.env_file, w.actual_record.prompt_file,
                                               w.actual_record.responsible_config}:
                old_pid = w.system.child.pid
                w.system.close()
                w.system.spawn()
                changed.append((old_pid, w.system.child.pid, w.system.invocation))
            return result

        with mock.patch.object(join_effects.ProtectedFiles, 'replace', replace_then_external_process_change):
            result = await w.coordinator().admit(w.actual_record.pubkey)
        self.assertEqual(result.status, 'unknown')
        self.assertTrue(changed, 'actual owned process must drift after CAS and before coordinator restart')
        self.assertNotEqual(changed[0][0], changed[0][1])
        self.assertEqual(w.system.restart_calls, 0, 'coordinator must not restart a replaced original process')
        active = w.db.active_own_home_admission(w.actual_record.pubkey)
        self.assertIsNotNone(active)
        self.assertEqual(active.state, 'unknown')
        w.no_authority_rows()

    async def test_agent_spec_drift_after_real_file_cas_blocks_restart(self):
        from unittest import mock
        from hostd import join_effects

        w = self.world
        original = join_effects.ProtectedFiles.replace
        replaced_paths = set()
        alternate = w.root / 'changed-prompt.md'
        w.owned(alternate, w.actual_record.prompt_file.read_bytes())

        def replace_then_spec_change(files, path, expected, updated):
            result = original(files, path, expected, updated)
            if Path(path) in {w.actual_record.env_file, w.actual_record.prompt_file,
                              w.actual_record.responsible_config}:
                replaced_paths.add(Path(path))
                if len(replaced_paths) == 3:
                    w.spec = dataclass_replace(w.spec, prompt_file=str(alternate))
            return result

        with mock.patch.object(join_effects.ProtectedFiles, 'replace', replace_then_spec_change):
            result = await w.coordinator().admit(w.actual_record.pubkey)
        self.assertEqual(result.status, 'unknown')
        self.assertEqual(len(replaced_paths), 3, 'all protected CAS operations must precede spec drift')
        self.assertEqual(w.spec.prompt_file, str(alternate))
        self.assertEqual(w.system.restart_calls, 0, 'changed runtime AgentSpec must block dispatch')
        active = w.db.active_own_home_admission(w.actual_record.pubkey)
        self.assertIsNotNone(active)
        self.assertEqual(active.state, 'unknown')
        w.no_authority_rows()

    async def test_repeated_cancellation_during_protected_file_cas_joins_worker(self):
        from unittest import mock
        from hostd import join_effects

        w = self.world
        entered = threading.Event()
        release = threading.Event()
        original = join_effects.ProtectedFiles.replace
        paused = {'done': False}

        def bounded_cas_barrier(files, path, expected, updated):
            if Path(path) in {w.actual_record.env_file, w.actual_record.prompt_file,
                              w.actual_record.responsible_config} and not paused['done']:
                paused['done'] = True
                entered.set()
                if not release.wait(600):
                    raise TimeoutError('file CAS cancellation barrier was not released')
            return original(files, path, expected, updated)

        task = None
        try:
            with mock.patch.object(join_effects.ProtectedFiles, 'replace', bounded_cas_barrier):
                task = asyncio.create_task(w.coordinator().admit(w.actual_record.pubkey))
                self.assertTrue(await wait_thread_event(entered, 600),
                                'actual file CAS must start only after UNKNOWN is committed')
                active = w.db.active_own_home_admission(w.actual_record.pubkey)
                self.assertIsNotNone(active)
                self.assertEqual(active.state, 'unknown')
                task.cancel(); await asyncio.sleep(0.03); task.cancel(); await asyncio.sleep(0.03)
                self.assertFalse(task.done(), 'repeated cancellation must join dispatched file IO')
        finally:
            release.set()
            if task is not None and not task.done():
                await asyncio.gather(task, return_exceptions=True)
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(w.system.restart_calls, 0)
        active = w.db.active_own_home_admission(w.actual_record.pubkey)
        self.assertIsNotNone(active)
        self.assertEqual(active.state, 'unknown')
        w.no_authority_rows()

    async def test_repeated_cancellation_during_final_native_source_read_joins_io(self):
        w = self.world
        w.block_final_member = True
        task = asyncio.create_task(w.coordinator().admit(w.actual_record.pubkey))
        try:
            self.assertTrue(await wait_thread_event(w.final_member_entered, 600),
                            'final current-source verifier must reach actual native member GET')
            self.assertEqual(w.system.restart_calls, 1)
            task.cancel(); await asyncio.sleep(0.03); task.cancel(); await asyncio.sleep(0.03)
            self.assertFalse(task.done(), 'cancel must join the actual in-flight native HTTP operation')
        finally:
            w.final_member_release.set()
            if not task.done():
                await asyncio.gather(task, return_exceptions=True)
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(w.system.restart_calls, 1)
        active = w.db.active_own_home_admission(w.actual_record.pubkey)
        self.assertIsNotNone(active)
        self.assertEqual(active.state, 'unknown')
        w.no_authority_rows()

    async def test_process_drift_after_final_sources_before_ack_keeps_unknown(self):
        w = self.world
        w.replace_process_at_final_member = True
        result = await w.coordinator().admit(w.actual_record.pubkey)
        self.assertEqual(result.status, 'unknown')
        self.assertEqual(w.system.restart_calls, 1)
        self.assertFalse(w.replace_process_at_final_member,
                         'actual post-restart native read must precede out-of-band process drift')
        admission = w.db.own_home_admission(result.admission_id)
        link = w.db.own_home_admission_restart(result.admission_id)
        pinned = w.db.restart_record(link.restart_operation_id).new_process
        self.assertEqual(admission.state, 'unknown')
        self.assertIsNotNone(pinned)
        self.assertNotEqual(pinned.pid, w.system.child.pid,
                            'ACK must compare the pinned restarted process to current /proc tuple')
        w.no_authority_rows()

    async def test_retry_with_changed_source_or_spec_cannot_rewrite_sealed_channel_set(self):
        w = self.world
        first = await w.coordinator().admit(w.actual_record.pubkey)
        self.assertEqual(first.status, 'admitted')
        original = w.db.own_home_admission_channels(first.admission_id)
        w.mutate_approval(chat_ref='f' * 64)
        retry = await w.coordinator().admit(w.actual_record.pubkey)
        self.assertIn(retry.status, ('pending', 'unknown'))
        self.assertEqual(w.db.own_home_admission_channels(first.admission_id), original)
        self.assertEqual(w.system.restart_calls, 1)
        w.no_authority_rows()


if __name__ == '__main__':
    unittest.main()
