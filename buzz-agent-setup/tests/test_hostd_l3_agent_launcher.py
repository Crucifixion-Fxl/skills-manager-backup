"""Offline unit admission/lifecycle contracts, never native/L3 acceptance."""
import asyncio
import dataclasses
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys
import threading
import unittest
from unittest import mock

import test_hostd_l3_prepare as prepare_fixture

digest = prepare_fixture.digest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import buzz_feishu_group_sync as gs
import buzz_agent_join_requests as join
from hostd.join_effects import ProcessOps

CHANNEL = '00000000-0000-0000-0000-000000000001'
KEY = '1'.zfill(64)
OWNER_KEY = '2'.zfill(64)
PUB = gs._signer_pubkey(KEY)
OWNER = gs._signer_pubkey(OWNER_KEY)


class UnitBackend:
    """Fake only systemctl/systemd-run and kernel observation boundaries."""
    def __init__(self, test):
        self.test = test
        self.calls = []
        self.loaded = False
        self.active = False
        self.pid = 10001
        self.start = 17
        self.invocation = '2' * 32
        self.extra_arg = ''
        self.env_path = test.env
        self.working_directory = test.base.run / 'runtime/agent-local'
        self.group = '/user.slice/app.slice/' + test.unit
        self.fail_create = False
        self.fail_stop = False
        self.program_override = None
        self.argv_extra = ()
        self.entered = threading.Event()
        self.release = threading.Event()
        self.block = False
        self.log = 'SYNTHETIC_BODY_DO_NOT_EXPORT\n'
        self.environment = join.parse_env(test.env.read_text())
        self.environment.update(HOME=str(test.base.run / 'home'), SSL_CERT_FILE=str(test.base.bundle))
        self.environment['PATH'] = str(test.base.buzz.parent) + ':' + str(Path(test.base.m.NODE).parent) + ':/usr/bin:/bin'
        self.cgroot = test.base.run / 'runtime/cgroup-fixture'
        self.cgroot.mkdir(mode=0o700)
        self.sync_group()

    def sync_group(self):
        path = self.cgroot / self.group.lstrip('/')
        path.mkdir(parents=True, exist_ok=True)
        (path / 'cgroup.procs').write_text(str(self.pid) + '\n' if self.active else '')
        (path / 'cgroup.procs').chmod(0o644)

    def runner(self, argv, **kwargs):
        self.calls.append((tuple(argv), kwargs))
        if Path(argv[0]).name == 'systemd-run':
            self.test.assertIn('--property=WorkingDirectory=' + str(self.working_directory), argv)
            self.entered.set()
            if self.block:
                if not self.release.wait(5):
                    raise subprocess.TimeoutExpired(argv, 5)
            if self.fail_create:
                # Dispatch uncertainty can leave a matching live unit. A new
                # initial invocation proof cannot be invented after failure.
                self.loaded = self.active = True
                self.sync_group()
                return subprocess.CompletedProcess(argv, 1, '', 'SYNTHETIC_SECRET_FAILURE')
            self.loaded = self.active = True
            self.sync_group()
            return subprocess.CompletedProcess(argv, 0, '', '')
        self.test.assertEqual(Path(argv[0]).name, 'systemctl')
        self.test.assertIn('--user', argv)
        self.test.assertIn(self.test.unit, argv)
        if 'stop' in argv:
            if not self.fail_stop:
                self.active = False
                self.sync_group()
            return subprocess.CompletedProcess(argv, int(self.fail_stop), '', 'SYNTHETIC_SECRET_FAILURE' if self.fail_stop else '')
        values = {
            'LoadState': 'loaded' if self.loaded else 'not-found',
            'ActiveState': 'active' if self.active else 'inactive',
            'MainPID': str(self.pid if self.active else 0),
            'InvocationID': self.invocation,
            'Id': self.test.unit,
            'ControlGroup': self.group if self.loaded else '',
            'WorkingDirectory': str(self.working_directory) if self.loaded else '',
            'EnvironmentFiles': str(self.env_path) + ' (ignore_errors=no)' if self.loaded else '',
            'ExecStart': ('{ path=' + str(self.test.native) + ' ; argv[]=' + str(self.test.native)
                          + ' --relay-url wss://127.0.0.1:9443' + self.extra_arg
                          + ' ; ignore_errors=no ; start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }') if self.loaded else '',
        }
        props = [argv[i + 1] for i, v in enumerate(argv) if v in ('-p', '--property')]
        answer = '\n'.join(values[k] if '--value' in argv else k + '=' + values[k] for k in props) + '\n'
        return subprocess.CompletedProcess(argv, 0, answer, '')

    def ready(self):
        self.log = ('connected to relay at wss://127.0.0.1:9443\n'
                    'subscribed to channel ' + CHANNEL + '\nSYNTHETIC_BODY_DO_NOT_EXPORT\n')


class KernelFixture(ProcessOps):
    def __init__(self, backend):
        super().__init__(backend.runner, base_env={}, cgroup_root=backend.cgroot)
        self.backend = backend

    def process(self, unit):
        assert unit == self.backend.test.unit and self.backend.active
        return self.backend.pid, self.backend.start, dict(self.backend.environment)

    def journal_invocation(self, unit, pid, invocation):
        assert unit == self.backend.test.unit and pid == self.backend.pid and invocation == self.backend.invocation
        return self.backend.log

    def program(self, unit, pid, start):
        assert unit == self.backend.test.unit and pid == self.backend.pid and start == self.backend.start
        test = self.backend.test
        return {'executable': self.backend.program_override or str(test.native),
                'sha256': digest(test.native), 'argv': (str(test.native), '--relay-url', 'wss://127.0.0.1:9443') + self.backend.argv_extra,
                'cgroup': self.backend.group, 'uid': os.geteuid()}


class AgentLauncher(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.base = prepare_fixture.PrepareTests()
        self.base.setUp()
        self.addCleanup(self.base.doCleanups)
        # Use the canonical real PreparedRun class, not a duplicate dynamic DTO.
        self.base.m = importlib.import_module('integration.hostd_l3.prepare')
        self.native = self.base.run / 'opt/buzz-acp'
        shutil.copyfile(Path('/usr/bin/true').resolve(), self.native)
        self.native.chmod(0o700)
        self.provider = self.base.run / 'provider'
        self.provider.mkdir(mode=0o700)
        self.python = self.provider / 'python'
        shutil.copyfile(Path(sys.executable).resolve(), self.python)
        self.python.chmod(0o700)
        self.script = self.provider / 'double.py'
        self.base.write(self.script, '# root-reviewed external ACP double; not executed here\n')
        self.args = '-I ' + str(self.script)
        self.prompt = self.base.run / 'runtime/agent-prompt.md'
        self.responsible = self.base.run / 'runtime/responsible.json'
        self.base.write(self.prompt, join.PROMPT_BEGIN + '\n' + CHANNEL + '\n' + join.PROMPT_END + '\n')
        self.base.write(self.responsible, {'channels': [CHANNEL]})
        self.env = self.base.run / 'runtime/agent.env'
        signature = gs.sync.nk.schnorr_sign(hashlib.sha256(f'nostr:agent-auth:{PUB}:'.encode()).digest(), bytes.fromhex(OWNER_KEY), bytes(32)).hex()
        self.env_text = ('BUZZ_PRIVATE_KEY=' + KEY + '\nBUZZ_AUTH_TAG=\'' + json.dumps(['auth', OWNER, '', signature])
                         + '\'\nBUZZ_ACP_AGENT_OWNER=' + OWNER + '\nBUZZ_ACP_CHANNELS=' + CHANNEL
                         + '\nBUZZ_RELAY_URL=wss://127.0.0.1:9443\nBUZZ_ACP_SYSTEM_PROMPT_FILE=' + str(self.prompt)
                         + '\nBUZZ_RESPONSIBLE_CONFIG=' + str(self.responsible) + '\nBUZZ_ACP_AGENT_COMMAND=' + str(self.python)
                         + '\nBUZZ_ACP_AGENT_ARGS=' + ','.join(shlex.split(self.args)) + '\n')
        self.base.write(self.env, self.env_text)
        self.unit = 'buzz-l3-' + self.base.doc['run_id'] + '.service'
        catalog = json.loads(self.base.catalog.read_text())
        catalog['owner_pubkey'] = OWNER
        catalog['agents'][0]['env_file'] = str(self.env)
        catalog['agents'][0]['log_file'] = str(self.base.run / 'runtime/agent-metadata.log')
        self.base.write(self.base.catalog, catalog)
        self.base.write(self.base.legacy, dict(catalog, agents=[]))
        self.base.cfg['agents'][PUB] = self.base.cfg['agents'].pop('2' * 64)
        self.base.write(self.base.config, self.base.cfg)
        self.prepared = self.base.check()
        self.backend = UnitBackend(self)
        self.ops = KernelFixture(self.backend)

    def module(self):
        return importlib.import_module('integration.hostd_l3.agent_launcher')

    def check(self, **changes):
        values = dict(catalog_path=self.base.catalog, legacy_join_path=self.base.legacy, agent_name='local',
                      native_buzz_acp=self.native, native_sha256=digest(self.native),
                      provider_python=self.python, python_sha256=digest(self.python),
                      provider_script=self.script, script_sha256=digest(self.script), provider_args=self.args)
        values.update(changes)
        return self.module().AgentPlan.check(self.prepared, **values)

    def owned(self, plan=None):
        return self.module().OwnedAgent(plan or self.check(), runner=self.backend.runner, process_ops=self.ops)

    def safe_error(self, caught):
        text = str(caught.exception)
        self.assertIn('怎么解决', text)
        self.assertIn('复制给 AI', text)
        for value in (KEY, 'SYNTHETIC_SECRET_FAILURE', 'SYNTHETIC_BODY_DO_NOT_EXPORT'):
            self.assertNotIn(value, text)

    def witness_plan(self):
        self.witness_work = self.provider / 'work'; self.witness_work.mkdir(mode=0o700)
        manifest = self.provider / 'double.json'; config = self.provider / 'wake-config.json'
        self.base.write(manifest, {'version': 1, 'run_id': self.base.doc['run_id'],
            'work_dir': str(self.witness_work), 'receipt_file': str(self.provider/'double-receipt.json'),
            'reply_text': 'Synthetic reply', 'hold_marker': 'L3-HOLD-' + self.base.doc['run_id']})
        self.base.write(config, {'version': 1, 'run_id': self.base.doc['run_id'],
            'work_dir': str(self.witness_work), 'witness_file': str(self.provider/'wake.json')})
        self.args = shlex.join(['-I', str(self.script), '--manifest', str(manifest), '--sha256', digest(manifest),
            '--witness-manifest', str(config), '--witness-sha256', digest(config)])
        self.env_text = self.env_text.split('BUZZ_ACP_AGENT_ARGS=', 1)[0] + 'BUZZ_ACP_AGENT_ARGS=' + ','.join(shlex.split(self.args)) + '\n'
        self.base.write(self.env, self.env_text); self.prepared = self.base.check()
        self.backend.environment['BUZZ_ACP_AGENT_ARGS'] = ','.join(shlex.split(self.args))
        # Literal admitted fixture path, independent of the launcher's selection.
        self.backend.working_directory = self.witness_work
        return self.check()

    async def test_witness_native_dispatch_uses_pinned_acp_work_directory(self):
        plan = self.witness_plan(); agent = self.owned(plan)
        self.assertEqual(plan._witness_inputs[8], self.witness_work)
        self.assertEqual(plan.workdir, self.base.run/'runtime/agent-local')
        self.assertFalse(plan.workdir.exists())
        self.assertIn('--property=WorkingDirectory=' + str(self.witness_work), agent._start_argv())
        await agent.start(); self.backend.ready()
        self.assertTrue(plan.workdir.is_dir())
        self.assertTrue((await agent.refresh())['native_ready'])
        dispatched = [a for a, _ in self.backend.calls if Path(a[0]).name == 'systemd-run']
        self.assertEqual(len(dispatched), 1)
        self.assertIn('--property=WorkingDirectory=' + str(self.witness_work), dispatched[0])
        self.assertNotIn('--property=WorkingDirectory=' + str(plan.workdir), dispatched[0])
        await agent.stop()

    async def test_default_work_directory_remains_fresh_agent_namespace(self):
        plan = self.check(); agent = self.owned(plan)
        self.assertFalse(plan._witness_inputs)
        self.assertIn('--property=WorkingDirectory=' + str(self.base.run/'runtime/agent-local'), agent._start_argv())
        await agent.start(); self.backend.ready()
        self.assertTrue((await agent.refresh())['native_ready'])
        await agent.stop()

    async def reject_wrong_work_directory(self, plan):
        agent = self.owned(plan); await agent.start(); self.backend.ready()
        original = self.backend.working_directory
        for wrong in (self.base.run/'runtime/foreign', str(original) + '/',
                      self.base.run/'runtime/agent-local' if plan._witness_inputs else self.provider/'work'):
            self.backend.working_directory = wrong
            with self.assertRaises(self.module().LauncherError): await agent.refresh()
            self.assertFalse(agent.readback()['native_ready'])
            with self.assertRaises(self.module().LauncherError): await agent.stop()
            self.assertFalse(any('stop' in a for a, _ in self.backend.calls))
        self.backend.working_directory = original
        await agent.stop()

    async def test_wrong_default_unit_work_directory_denies_readiness_and_stop_effects(self):
        await self.reject_wrong_work_directory(self.check())

    async def test_wrong_witness_unit_work_directory_denies_readiness_and_stop_effects(self):
        await self.reject_wrong_work_directory(self.witness_plan())

    async def test_stale_exited_unit_with_wrong_work_directory_is_not_marked_reaped(self):
        plan = self.witness_plan(); agent = self.owned(plan); await agent.start()
        self.backend.active = False; self.backend.sync_group()
        self.backend.working_directory = self.base.run/'runtime/agent-local'
        with self.assertRaises(self.module().LauncherError): await agent.stop()
        self.assertFalse(agent.readback()['reaped'])
        self.assertFalse(any('stop' in a for a, _ in self.backend.calls))

    async def test_replaced_pinned_witness_work_directory_denies_before_dispatch(self):
        plan = self.witness_plan(); agent = self.owned(plan)
        self.witness_work.rename(self.provider/'original-work')
        self.witness_work.mkdir(mode=0o700)
        with self.assertRaises(self.module().LauncherError): await agent.start()
        self.assertFalse(any(Path(a[0]).name == 'systemd-run' for a, _ in self.backend.calls))

    async def test_check_readonly_immutable_actual_prepared_and_catalog(self):
        before = {str(p): p.read_bytes() for p in self.base.run.rglob('*') if p.is_file()}
        plan = self.check()
        self.assertEqual(plan.readback()['status'], 'prepared')
        self.assertFalse(plan.readback()['live_verified'])
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.base.run.rglob('*') if p.is_file()})
        with self.assertRaises(dataclasses.FrozenInstanceError):
            plan.unit = 'foreign.service'
        self.assertNotIn(KEY, repr(plan))

    async def test_native_env_is_comma_vector_while_plan_keeps_shell_arguments(self):
        self.args = shlex.join([*shlex.split(self.args), '--label', 'internal space'])
        self.env_text = self.env_text.split('BUZZ_ACP_AGENT_ARGS=', 1)[0] + 'BUZZ_ACP_AGENT_ARGS=' + ','.join(shlex.split(self.args)) + '\n'
        self.base.write(self.env, self.env_text)
        self.prepared = self.base.check()
        self.backend.environment['BUZZ_ACP_AGENT_ARGS'] = ','.join(shlex.split(self.args))
        plan = self.check()
        expected = shlex.split(self.args)
        self.assertEqual(plan.provider_args, self.args)
        self.assertEqual(self.backend.environment['BUZZ_ACP_AGENT_ARGS'].split(','), expected)
        agent = self.owned(plan)
        await agent.start(); self.backend.ready()
        self.assertTrue((await agent.refresh())['native_ready'])
        await agent.stop()

    async def test_ambiguous_or_malformed_provider_arguments_fail_before_dispatch(self):
        for args in (self.args + ' --label=a,b', self.args + " ''", self.args + " '",
                     self.args + ' \x00', self.args + ' \x1f', self.args + ' \x7f',
                     self.args + " ' leading'", self.args + " 'trailing '", self.args + ' \x85'):
            with self.subTest(args_hash=hashlib.sha256(args.encode()).hexdigest()):
                # The original prepared inputs stay valid; reject the caller's vector.
                with self.assertRaises(self.module().LauncherError): self.check(provider_args=args)
                self.assertEqual(self.backend.calls, [])
        self.base.write(self.env, self.env_text)

    async def test_malformed_native_env_vector_fails_admission_and_current_observation(self):
        correct = ','.join(shlex.split(self.args))
        for value in (self.args, correct + ',', ',' + correct, correct.replace(',', ',,'),
                      correct.replace(',', ',"', 1) + '"', correct.replace(',', ', ')):
            with self.subTest(env_hash=hashlib.sha256(value.encode()).hexdigest()):
                self.base.write(self.env, self.env_text.replace(correct, value))
                self.prepared = self.base.check()
                with self.assertRaises(self.module().LauncherError): self.check()
                self.assertEqual(self.backend.calls, [])
        self.base.write(self.env, self.env_text)
        self.prepared = self.base.check()
        agent = self.owned(); await agent.start(); self.backend.ready()
        self.backend.environment['BUZZ_ACP_AGENT_ARGS'] = self.args
        with self.assertRaises(self.module().LauncherError): await agent.refresh()
        self.backend.environment['BUZZ_ACP_AGENT_ARGS'] = correct
        await agent.stop()

    async def test_forged_run_unit_owner_oa_and_channel_fail_before_commands(self):
        for old, new in ((OWNER, 'a' * 64), (CHANNEL, '00000000-0000-0000-0000-000000000002'), ('"",', '"conditional",')):
            self.base.write(self.env, self.env_text.replace(old, new))
            with self.assertRaises(self.module().LauncherError) as caught:
                self.check()
            self.safe_error(caught)
        self.base.write(self.env, self.env_text)
        doc = json.loads(self.base.catalog.read_text()); doc['agents'][0]['unit'] = 'foreign.service'
        self.base.write(self.base.catalog, doc)
        with self.assertRaises(self.module().LauncherError): self.check()
        self.assertFalse(self.backend.calls)

    async def test_program_elf_symlink_permissions_hash_and_provider_pin_reject(self):
        # Execute the real native observer parser through only a low-level
        # /proc-open redirection to a synthetic protected kernel tree.
        proc = self.base.run / 'runtime/proc-fixture'; proc.mkdir(mode=0o700)
        tail = ['S'] + ['0'] * 20; tail[19] = str(self.backend.start)
        self.base.write(proc / 'stat', f'{self.backend.pid} (synthetic) ' + ' '.join(tail))
        self.base.write(proc / 'cmdline', str(self.native) + '\0--relay-url\0wss://127.0.0.1:9443\0')
        self.base.write(proc / 'cgroup', '0::' + self.backend.group + '\n')
        (proc / 'exe').symlink_to(self.native)
        original_open = os.open
        def kernel_open(path, *args, **kwargs):
            if path == '/proc/' + str(self.backend.pid): path = str(proc)
            return original_open(path, *args, **kwargs)
        with mock.patch.object(self.module().os, 'open', side_effect=kernel_open):
            native_ops = self.module().NativeProcessOps(self.backend.runner, base_env={})
            observed = native_ops.program(self.unit, self.backend.pid, self.backend.start)
        self.assertEqual(observed['sha256'], digest(self.native))
        self.assertEqual(observed['argv'], (str(self.native), '--relay-url', 'wss://127.0.0.1:9443'))
        self.assertEqual(observed['cgroup'], self.backend.group)
        for field in ('native_sha256', 'python_sha256', 'script_sha256'):
            with self.assertRaises(self.module().LauncherError): self.check(**{field: 'f' * 64})
        with self.assertRaises(self.module().LauncherError): self.check(provider_args='--production')
        original = self.native.read_bytes(); self.native.write_bytes(b'#!/bin/sh\n')
        with self.assertRaises(self.module().LauncherError): self.check()
        self.native.write_bytes(original); self.native.chmod(0o777)
        with self.assertRaises(self.module().LauncherError): self.check()
        self.native.chmod(0o700); target = self.native.with_name('real-acp'); self.native.rename(target); self.native.symlink_to(target)
        with self.assertRaises(self.module().LauncherError): self.check()

    async def test_start_revalidates_all_inputs_and_source_before_dispatch(self):
        for path in (self.env, self.prompt, self.responsible, self.script, self.base.bundle):
            plan = self.check(); original = path.read_bytes(); path.write_bytes(original + b'\n')
            with self.assertRaises(self.module().LauncherError): await self.owned(plan).start()
            path.write_bytes(original)
        self.base.status_result.stdout = ' M reviewed-source\n'
        with self.assertRaises(self.module().LauncherError): await self.owned().start()
        self.assertFalse(any(Path(a[0]).name == 'systemd-run' for a, _ in self.backend.calls))

    async def test_preexisting_loaded_or_unknown_unit_is_never_adopted(self):
        plan = self.check()
        with self.assertRaises(self.module().LauncherError):
            await self.owned(dataclasses.replace(plan, unit='foreign.service')).start()
        self.assertFalse(self.backend.calls)
        scope = [(CHANNEL,)]
        original_status = self.ops.unit_status
        def revoked_during_status(unit):
            value = original_status(unit); scope[0] = ()
            return value
        self.ops.unit_status = revoked_during_status
        guarded = self.module().OwnedAgent(plan, runner=self.backend.runner, process_ops=self.ops,
                                          approved_channels=lambda admitted: scope[0])
        with self.assertRaises(self.module().LauncherError): await guarded.start()
        self.assertFalse(any(Path(a[0]).name == 'systemd-run' for a, _ in self.backend.calls))
        self.ops.unit_status = original_status
        self.backend.loaded = self.backend.active = True
        with self.assertRaises(self.module().LauncherError): await self.owned().start()
        self.backend.loaded = False
        self.ops.unit_status = lambda unit: ('unknown', 'unknown')
        with self.assertRaises(self.module().LauncherError): await self.owned().start()
        self.assertFalse(any(Path(a[0]).name == 'systemd-run' for a, _ in self.backend.calls))

    async def test_explicit_start_exact_safe_user_unit_argv_and_private_receipt(self):
        descriptors = len(list(Path('/proc/self/fd').iterdir()))
        owned = self.owned(); result = await owned.start()
        self.assertEqual(len(list(Path('/proc/self/fd').iterdir())), descriptors)
        self.assertEqual(result['status'], 'running'); self.assertFalse(result['native_ready'])
        starts = [a for a, _ in self.backend.calls if Path(a[0]).name == 'systemd-run']
        self.assertEqual(len(starts), 1); argv = starts[0]
        self.assertIn('--user', argv); self.assertIn('--unit=' + self.unit, argv)
        self.assertIn('--property=EnvironmentFile=' + str(self.env), argv)
        self.assertEqual(argv[-3:], (str(self.native), '--relay-url', 'wss://127.0.0.1:9443'))
        self.assertIn('--property=StandardOutput=journal', argv)
        environment = next(arg for arg in argv if arg.startswith('--property=Environment='))
        self.assertIn(' PATH=' + self.backend.environment['PATH'], environment)
        self.assertNotIn(KEY, repr(argv)); self.assertNotIn('BUZZ_AUTH_TAG=', repr(argv))
        for path in self.base.run.rglob('*'):
            if path.is_file() and path.name.endswith(('.receipt.json', '.log')):
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertNotIn('SYNTHETIC_BODY_DO_NOT_EXPORT', path.read_text())

    async def test_registered_and_one_subscription_line_do_not_prove_native_ready(self):
        owned = self.owned(); await owned.start()
        for log in ('registered\n', 'connected to relay at wss://127.0.0.1:9443\n', 'subscribed to channel ' + CHANNEL + '\n'):
            self.backend.log = log
            result = await owned.refresh(); self.assertFalse(result['native_ready'])
            self.assertEqual(result['status'], 'running')

    async def test_current_invocation_connect_and_channel_are_native_ready_metadata_only(self):
        owned = self.owned(); await owned.start(); self.backend.ready()
        result = await owned.refresh()
        self.assertTrue(result['native_ready']); self.assertTrue(result['subscribed'])
        self.assertFalse(result['live_verified'])
        self.assertNotIn('SYNTHETIC_BODY', json.dumps(result)); self.assertNotIn(KEY, json.dumps(result))
        changes = (
            (self.base.catalog, lambda raw: json.dumps(dict(json.loads(raw), agents=[dict(json.loads(raw)['agents'][0],
                 feishu=dict(json.loads(raw)['agents'][0]['feishu'], lark_data_dir=str(self.base.run / 'foreign-data')))] )).encode()),
            (self.base.legacy, lambda raw: json.dumps(dict(json.loads(raw), state_dir=str(self.base.run / 'other-state'))).encode()),
            (self.base.onboarding, lambda raw: json.dumps(dict(json.loads(raw), relay_pubkey='b' * 64)).encode()),
            (self.base.config, lambda raw: json.dumps(dict(json.loads(raw), remove_extras=False)).encode()),
            (self.base.bundle, lambda raw: raw + b'\n'),
            (self.base.fixture, lambda raw: raw + b'\n'),
            (self.base.source / 'skills/agent-harness/buzz-agent-setup/scripts/hostd/__main__.py', lambda raw: raw + b'# changed\n'),
        )
        for path, change in changes:
            original = path.read_bytes(); mode = path.stat().st_mode & 0o777
            path.chmod(0o600); path.write_bytes(change(original)); path.chmod(mode)
            with self.subTest(immutable=path.name):
                with self.assertRaises(self.module().LauncherError): await owned.refresh()
            path.chmod(0o600); path.write_bytes(original); path.chmod(mode)
        self.backend.ready(); self.backend.log += 'disconnected from relay\n'
        self.assertFalse((await owned.refresh())['native_ready'])
        self.backend.log += 'connected to relay at wss://127.0.0.1:9443\n'
        self.assertFalse((await owned.refresh())['native_ready'])
        self.backend.log += 'subscribed to channel ' + CHANNEL + '\n'
        self.assertTrue((await owned.refresh())['native_ready'])
        self.backend.log += 'unknown connection state from native transport\n'
        self.assertFalse((await owned.refresh())['native_ready'])

    async def test_exact_official_reconnect_generation_and_fresh_debug_subscription_rearm(self):
        owned = self.owned(); await owned.start(); self.backend.ready()
        self.backend.log += ('WARN buzz_acp::relay: relay connection closed\n'
            'INFO buzz_acp::relay: attempting relay reconnect to wss://127.0.0.1:9443…\n'
            'INFO buzz_acp::relay: relay reconnected to wss://127.0.0.1:9443\n'
            'INFO buzz_acp::relay: resubscribing to 1 channel(s) after reconnect\n'
            'DEBUG buzz_acp::relay: subscribed to channel ' + CHANNEL + ' (with since filter)\n')
        value = await owned.refresh()
        self.assertTrue(value['connected']); self.assertTrue(value['subscribed'])
        self.assertTrue(value['native_ready']); self.assertFalse(value['live_verified'])
        await owned.stop()

    async def test_reconnect_info_count_and_old_subscription_stay_pending(self):
        owned = self.owned(); await owned.start(); self.backend.ready()
        self.backend.log += ('INFO buzz_acp::relay: relay reconnected to wss://127.0.0.1:9443\n'
            'INFO buzz_acp::relay: resubscribing to 1 channel(s) after reconnect\n')
        value = await owned.refresh()
        self.assertTrue(value['connected']); self.assertFalse(value['subscribed'])
        self.assertFalse(value['native_ready'])
        await owned.stop()

    async def test_reconnect_wrong_relay_channel_and_suffix_cannot_rearm(self):
        owned = self.owned(); await owned.start()
        correct = 'INFO buzz_acp::relay: relay reconnected to wss://127.0.0.1:9443\n'
        frames = (
            'INFO buzz_acp::relay: relay reconnected to wss://127.0.0.1:9444\n'
                + 'DEBUG buzz_acp::relay: subscribed to channel ' + CHANNEL + ' (with since filter)\n',
            correct + 'DEBUG buzz_acp::relay: subscribed to channel 00000000-0000-0000-0000-000000000002 (with since filter)\n',
            correct + 'DEBUG buzz_acp::relay: subscribed to channel ' + CHANNEL + ' (with since filter) extra\n',
            correct + 'DEBUG buzz_acp::relay: subscribed to channel ' + CHANNEL + ' (since=now )\n',
            correct + 'DEBUG buzz_acp::relay: subscribed to channel ' + CHANNEL + ' (with since filter) (since=now)\n',
            correct + 'INFO buzz_acp::relay: resubscribing to 1 channel(s) after reconnect extra\n'
                + 'DEBUG buzz_acp::relay: subscribed to channel ' + CHANNEL + ' (since=now)\n',
            correct + 'WARN disconnected: resubscribing to 1 channel(s) after reconnect\n'
                + 'DEBUG buzz_acp::relay: subscribed to channel ' + CHANNEL + ' (since=now)\n',
            correct + 'INFO buzz_acp::relay: autonomous reconnect succeeded (attempt 1)\n'
                + 'DEBUG buzz_acp::relay: subscribed to channel ' + CHANNEL + ' (with since filter)\n',
        )
        for frame in frames:
            with self.subTest(frame_hash=hashlib.sha256(frame.encode()).hexdigest()):
                self.backend.ready(); self.backend.log += frame
                self.assertFalse((await owned.refresh())['native_ready'])
        await owned.stop()

    async def test_reconnect_subscription_suffix_since_now_is_exact_supported_log(self):
        owned = self.owned(); await owned.start()
        self.backend.log = ('INFO buzz_acp::relay: relay reconnected to wss://127.0.0.1:9443\n'
            'DEBUG buzz_acp::relay: subscribed to channel ' + CHANNEL + ' (since=now)\n')
        self.assertTrue((await owned.refresh())['native_ready'])
        await owned.stop()

    async def test_reconnect_partial_approved_channels_remain_pending(self):
        second = '00000000-0000-0000-0000-000000000002'; scope = [(CHANNEL,)]
        owned = self.module().OwnedAgent(self.check(), runner=self.backend.runner, process_ops=self.ops,
            approved_channels=lambda plan: scope[0])
        await owned.start()
        self.base.write(self.env, self.env_text.replace('BUZZ_ACP_CHANNELS=' + CHANNEL,
            'BUZZ_ACP_CHANNELS=' + CHANNEL + ',' + second))
        self.base.write(self.responsible, {'channels': [CHANNEL, second]})
        self.backend.environment['BUZZ_ACP_CHANNELS'] = CHANNEL + ',' + second
        scope[0] = (CHANNEL, second)
        self.backend.log = ('INFO buzz_acp::relay: relay reconnected to wss://127.0.0.1:9443\n'
            'INFO buzz_acp::relay: resubscribing to 2 channel(s) after reconnect\n'
            'DEBUG buzz_acp::relay: subscribed to channel ' + CHANNEL + ' (with since filter)\n')
        value = await owned.refresh()
        self.assertTrue(value['connected']); self.assertFalse(value['subscribed'])
        self.assertFalse(value['native_ready'])
        self.backend.log += 'DEBUG buzz_acp::relay: subscribed to channel ' + second + ' (with since filter)\n'
        self.assertTrue((await owned.refresh())['native_ready'])
        # A later reconnect invalidates both channels from the older generation.
        self.backend.log += ('INFO buzz_acp::relay: relay reconnected to wss://127.0.0.1:9443\n'
            'DEBUG buzz_acp::relay: subscribed to channel ' + second + ' (with since filter)\n')
        self.assertFalse((await owned.refresh())['native_ready'])
        await owned.stop()

    async def test_wrong_process_env_invocation_or_group_never_ready(self):
        owned = self.owned(); await owned.start(); self.backend.ready()
        for key, value in (('HOME', '/foreign/home'), ('BUZZ_ACP_AGENT_OWNER', 'a' * 64), ('BUZZ_PRIVATE_KEY', '3'.zfill(64)), ('SSL_CERT_FILE', '/foreign/ca')):
            original = self.backend.environment[key]; self.backend.environment[key] = value
            with self.assertRaises(self.module().LauncherError): await owned.refresh()
            self.backend.environment[key] = original
        self.backend.program_override = '/foreign/buzz-acp'
        with self.assertRaises(self.module().LauncherError): await owned.refresh()
        self.backend.program_override = None; self.backend.argv_extra = ('--foreign',)
        with self.assertRaises(self.module().LauncherError): await owned.refresh()
        self.backend.argv_extra = ()
        self.backend.environment['BUZZ_ACP_CHANNELS'] = CHANNEL + ',00000000-0000-0000-0000-000000000002'
        with self.assertRaises(self.module().LauncherError): await owned.refresh()
        self.backend.environment['BUZZ_ACP_CHANNELS'] = CHANNEL
        self.backend.environment['UNRELATED_API_TOKEN'] = 'SYNTHETIC_SECRET_FAILURE'
        with self.assertRaises(self.module().LauncherError): await owned.refresh()
        self.backend.environment.pop('UNRELATED_API_TOKEN')
        original = self.backend.invocation; self.backend.invocation = '0' * 32
        with self.assertRaises(self.module().LauncherError): await owned.refresh()
        self.backend.invocation = original
        (self.backend.cgroot / self.backend.group.lstrip('/') / 'cgroup.procs').write_text('99999\n')
        with self.assertRaises(self.module().LauncherError): await owned.refresh()

    async def test_unknown_creation_never_retries_or_claims_ownership(self):
        self.backend.fail_create = True; owned = self.owned()
        with self.assertRaises(self.module().LauncherError) as caught: await owned.start()
        self.safe_error(caught)
        self.assertEqual(owned.readback()['status'], 'unknown')
        with self.assertRaises(self.module().LauncherError): await owned.start()
        with self.assertRaises(self.module().LauncherError): await owned.stop()
        self.assertEqual(sum(Path(a[0]).name == 'systemd-run' for a, _ in self.backend.calls), 1)

    async def test_cleanup_refuses_changed_unit_exec_envfile_or_cgroup(self):
        owned = self.owned(); await owned.start()
        for field, value in (('extra_arg', ' --foreign'), ('env_path', self.base.run / 'runtime/foreign.env'), ('group', '/user.slice/foreign.service')):
            original = getattr(self.backend, field); setattr(self.backend, field, value)
            with self.assertRaises(self.module().LauncherError): await owned.stop()
            setattr(self.backend, field, original)
        self.assertFalse(any('stop' in a for a, _ in self.backend.calls))

    async def test_successor_pid_same_owned_unit_refresh_then_cleanup(self):
        approved = [(CHANNEL,)]
        callback_threads = []
        def current_effect(plan):
            self.assertEqual(plan.pubkey, PUB); self.assertEqual(plan.unit, self.unit)
            callback_threads.append(threading.current_thread())
            return approved[0]
        owned = self.module().OwnedAgent(self.check(), runner=self.backend.runner, process_ops=self.ops,
                                        approved_channels=current_effect)
        await owned.start(); self.backend.ready(); await owned.refresh()
        new_channel = '00000000-0000-0000-0000-000000000002'
        self.base.write(self.env, self.env_text.replace('BUZZ_ACP_CHANNELS=' + CHANNEL, 'BUZZ_ACP_CHANNELS=' + CHANNEL + ',' + new_channel))
        self.base.write(self.responsible, {'channels': [CHANNEL, new_channel]})
        self.backend.environment['BUZZ_ACP_CHANNELS'] = CHANNEL + ',' + new_channel
        with self.assertRaises(self.module().LauncherError): await owned.refresh()
        approved[0] = (CHANNEL, new_channel)
        self.backend.log += 'subscribed to channel ' + new_channel + '\n'
        self.backend.pid += 1; self.backend.start += 1; self.backend.invocation = '3' * 32; self.backend.sync_group()
        self.assertTrue((await owned.refresh())['native_ready'])
        self.assertTrue(all(thread is threading.main_thread() for thread in callback_threads))
        original = self.ops.journal_invocation
        def changed_effect(unit, pid, invocation):
            text = original(unit, pid, invocation); approved[0] = (CHANNEL,)
            return text
        self.ops.journal_invocation = changed_effect
        with self.assertRaises(self.module().LauncherError): await owned.refresh()
        self.ops.journal_invocation = original; approved[0] = (CHANNEL, new_channel)
        result = await owned.stop(); self.assertEqual(result['status'], 'stopped')
        self.assertTrue(result['reaped']); self.assertFalse(self.backend.active)

    async def test_cancelled_creation_is_uncertain_not_blindly_replayed(self):
        owned = self.owned(); self.backend.block = True
        task = asyncio.create_task(owned.start())
        for _ in range(100):
            if self.backend.entered.is_set(): break
            await asyncio.sleep(.01)
        self.assertTrue(self.backend.entered.is_set())
        task.cancel(); self.backend.release.set()
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertEqual(owned.readback()['status'], 'unknown')
        with self.assertRaises(self.module().LauncherError): await owned.start()

    async def test_stop_failure_or_no_observed_exit_never_claims_reaped(self):
        owned = self.owned(); await owned.start(); self.backend.fail_stop = True
        with self.assertRaises(self.module().LauncherError) as caught: await owned.stop()
        self.safe_error(caught); self.assertFalse(owned.readback()['reaped'])
        self.backend.fail_stop = False; self.backend.active = False; self.backend.sync_group()
        result = await owned.stop()
        self.assertEqual(result['status'], 'stopped'); self.assertTrue(result['reaped'])


if __name__ == '__main__':
    unittest.main()
