"""Four finite real assembly/lifecycle contracts; no remote grant/delivery proof.

Collect beside unchanged zero_local_fixture.py and the separate reviewed low
normal_remote_assembly_fixture.py in a FULL private canonical-layout snapshot.
"""
from __future__ import annotations

import asyncio
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

TESTS = Path(__file__).resolve().parent
SCRIPTS = TESTS.parent/'scripts'
SOURCE = TESTS.parents[3]
MODULE = TESTS/'integration'/'hostd_l3'/'zero_local_run.py'


class NormalRemoteAssemblyTests(unittest.TestCase):
    def setUp(self):
        # Missing support is genuine RED even in ordinary CI lacking deps.
        self.assertTrue(MODULE.is_file(), 'missing distinct zero-local host-B run support')
        missing = []
        for name in ('cryptography', 'lark-oapi', 'websockets'):
            try:
                importlib.metadata.distribution(name)
            except importlib.metadata.PackageNotFoundError:
                missing.append(name)
        if shutil.which('git', path=os.defpath) is None:
            missing.append('git executable on fixed PATH')
        if missing:
            raise unittest.SkipTest(
                'normal remote assembly requires actual dependencies: '+', '.join(missing)+
                '; ordinary CI skip is not proof: mandatory actualdeps must pass '
                'ALL FOUR methods with zero failures, errors and skips')
        previous_bytecode = sys.dont_write_bytecode
        sys.dont_write_bytecode = True
        self.addCleanup(setattr, sys, 'dont_write_bytecode', previous_bytecode)
        for path in (TESTS, SCRIPTS):
            if str(path) not in sys.path:
                sys.path.insert(0, str(path))
        spec = importlib.util.spec_from_file_location('hostd_l3_remote_assembly_candidate', MODULE)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        self.module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = self.module
        self.addCleanup(sys.modules.pop, spec.name, None)
        spec.loader.exec_module(self.module)
        for name in ('ZeroLocalRunPlan', 'OwnedZeroLocalRun', 'ZeroLocalRunError'):
            self.assertTrue(callable(getattr(self.module, name, None)))
        import zero_local_fixture as fixture
        import normal_remote_assembly_fixture as native
        self.fixture, self.native = fixture, native
        temp = tempfile.TemporaryDirectory(prefix='hostd-normal-remote-assembly-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.root.chmod(0o700)
        result = subprocess.run(
            ['git', '-C', str(SOURCE), 'rev-parse', 'HEAD'], check=True,
            capture_output=True, text=True, timeout=5,
            env={'PATH': os.defpath, 'LANG': 'C.UTF-8'})
        self.revision = result.stdout.strip()
        self.assertRegex(self.revision, r'^[0-9a-f]{40}$')
        self.hashes = {str(p.relative_to(SOURCE)): fixture.digest(p)
                       for p in sorted(SCRIPTS.rglob('*.py'))}

    def case(self, *, remote=True, early_exit=False):
        case = self.fixture.Fixture(self.root, SOURCE, self.revision, dict(self.hashes))
        self.addCleanup(case.close)
        if remote:
            config = json.loads(case.onboarding.read_text())
            config['remote_link_base'] = case.relay.origin
            self.fixture.write(case.onboarding, json.dumps(config))
        if early_exit:
            self.fixture.write(case.wire_file,
                               self.fixture.STARTUPWIRE+self.native.EARLY_CONSOLE_BIND_FAILURE)
        case.repin()  # Real bytes/metadata, prior to Plan.check only.
        return case

    def admit(self, case):
        plan = self.module.ZeroLocalRunPlan.check(case.manifest, **case.check_args)
        self.assertIs(type(plan), self.module.ZeroLocalRunPlan)
        observed = plan.readback()
        self.assertEqual(observed['status'], 'prepared')
        self.assertEqual(observed['registry_bindings'], 0)
        self.assertEqual(observed['target_channel_bindings'], 0)
        self.assertEqual(observed['registered_agent_pubkeys'], [])
        self.assertFalse(observed['runtime_started'])
        self.assertFalse(observed['live_verified'])
        self.assert_no_outputs(case)
        return plan

    def assert_no_outputs(self, case):
        self.assertTrue(all(not path.exists() for path in case.outputs))
        self.assertFalse((case.home/'.local').exists())

    def assert_inputs(self, case, original):
        observed = case.snapshot()
        for relative, value in original.items():
            self.assertEqual(observed[relative], value, relative)
        self.assertEqual(self.hashes, {str(p.relative_to(SOURCE)): self.fixture.digest(p)
                                      for p in sorted(SCRIPTS.rglob('*.py'))})

    def sibling(self):
        proc = self.fixture.sibling_canary()
        identity = self.fixture.process_identity(proc.pid)
        self.assertIsNotNone(identity)
        self.addCleanup(self.fixture.reap_sibling, proc)
        return proc, identity

    def assert_sibling(self, proc, identity):
        self.assertIsNone(proc.poll())
        self.assertEqual(self.fixture.process_identity(proc.pid), identity)

    def assert_stopped(self, owned, capture, stopped):
        self.assertEqual(stopped['status'], 'stopped')
        self.assertTrue(stopped['child_reaped'])
        self.assertTrue(stopped['session_reaped'])
        self.assertFalse(stopped['live_verified'])
        self.assertIsNotNone(capture.process.poll())
        self.assertEqual(self.fixture.session_members(capture.process.pid), set())
        self.assertEqual(owned.stop(timeout=10), stopped)

    def test_real_start_onboarding_constructs_remote_dispatch_only_for_protected_nonempty_link(self):
        for remote in (False, True):
            with self.subTest(remote=remote):
                case = self.case(remote=remote)
                plan = self.admit(case)
                before = case.snapshot()
                case.relay.start()
                # Real parent adapters use this private environment and native
                # TLS sockets. Native signed readers keep their own minimal env.
                with self.native.parent_transport(plan.environment(), case.relay.server.server_port):
                    from hostd.__main__ import Hostd
                    from hostd import registry
                    from hostd.onboarding_runtime import RuntimeConfig, OnboardingRuntime
                    from hostd.remote_dispatch import RemoteDispatch
                    from hostd.store import Store
                    config = RuntimeConfig.load(case.onboarding)
                    self.assertEqual(config.remote_link_base, case.relay.origin if remote else '')
                    actual_registry = registry.load(case.registry, only={case.doc['selector']})
                    self.assertEqual(actual_registry.bindings, {})
                    self.assertEqual(actual_registry.skipped, {})

                    async def assemble():
                        root = Hostd(actual_registry, case.outputs[1], state_db=case.outputs[0],
                                     onboarding_config=config)
                        try:
                            await root.start_onboarding()
                            self.assertIs(type(root.onboarding), OnboardingRuntime)
                            self.assertIs(type(root.runtime_store), Store)
                            self.assertIs(root.onboarding.store, root.runtime_store)
                            self.assertIs(root.onboarding.config, config)
                            self.assertEqual(root.onboarding.relay.owner, case.owner)
                            self.assertNotEqual(case.owner, case.source_owner)
                            self.assertEqual(root.app_profiles,
                                             {'cli_agent': (str(case.cfg), str(case.data))})
                            self.assertEqual([(r.pubkey, r.owner_pubkey, r.app_id, r.status)
                                              for r in root.onboarding.catalog.records],
                                             [(case.agent_pub, case.owner, 'cli_agent', 'own_bot_verified')])
                            if remote:
                                dispatch = root.remote_dispatch
                                self.assertIs(type(dispatch), RemoteDispatch)
                                self.assertIs(dispatch.onboarding, root.onboarding)
                                self.assertIs(dispatch.store, root.runtime_store)
                                self.assertIs(dispatch.scheduler, root.scheduler)
                                self.assertIs(dispatch.http_pool, root.http_pool)
                                self.assertEqual(dispatch._public_base(), case.relay.origin)
                                self.assertEqual(dispatch.base_env, plan.environment())
                            else:
                                self.assertIsNone(root.remote_dispatch)
                            rows, target_rows, agents = self.fixture.db_readback(case.outputs[0])
                            self.assertEqual((rows, target_rows), ([], []))
                            self.assertEqual(agents, [(case.agent_pub, case.owner, 'cli_agent', 'active')])
                        finally:
                            await root._shutdown()
                        self.assertIsNone(root.remote_dispatch)
                        self.assertIsNone(root.onboarding)
                        self.assertIsNone(root.runtime_store)

                    async def bounded_assembly():
                        await asyncio.wait_for(assemble(), timeout=40)
                    asyncio.run(bounded_assembly())
                self.assertEqual(case.relay.failures, [])
                self.assertTrue(any(f.get('kinds') == [0] and f.get('authors') == [case.agent_pub]
                                    for packet in case.relay.received for f in packet))
                self.assertTrue(any(f.get('kinds') == [30177] and f.get('authors') == [case.owner]
                                    and f.get('#d') == [case.agent_pub]
                                    for packet in case.relay.received for f in packet))
                self.assert_inputs(case, before)
                self.assertFalse(case.outputs[1].exists())
                self.assertFalse(case.outputs[2].exists())

    def test_normal_remote_enabled_cli_registers_own_sdk_zero_bindings_and_reaps_original_session(self):
        case = self.case()
        case.relay.start()
        plan = self.admit(case)
        self.assertEqual(plan.argv(), [str(Path(sys.executable).absolute()), '-m', 'hostd', 'run',
                         '--only', case.doc['selector'], '--onboarding-config', str(case.onboarding),
                         '--state-db', str(case.outputs[0]), '--status-file', str(case.outputs[1]),
                         '--console-dir', str(case.outputs[2])])
        original_inputs = case.snapshot()
        sibling, sibling_identity = self.sibling()
        capture = self.fixture.CapturePopen(case, plan)
        self.addCleanup(capture.emergency_reap)
        owned = self.module.OwnedZeroLocalRun(plan, popen_factory=capture)
        seen_members = set()
        try:
            self.assertEqual(owned.start()['status'], 'running')
            deadline = time.monotonic()+20
            while time.monotonic() < deadline:
                self.assertIsNone(capture.process.poll(), 'normal Hostd exited before registration')
                self.assertEqual(self.fixture.process_identity(capture.process.pid), capture.identity)
                members = self.fixture.session_members(capture.process.pid)
                seen_members |= members
                capture.observed_members |= members
                sdk_argv = [str(Path(sys.executable).absolute()), str(SCRIPTS/'hostd'/'feishu_feed.py'),
                            'cli_agent', str(case.cfg), str(case.data)]
                sdk_present = False
                for identity in members:
                    try:
                        argv = Path(f'/proc/{identity[0]}/cmdline').read_bytes().decode().strip('\0').split('\0')
                    except FileNotFoundError:
                        continue
                    if argv == sdk_argv:
                        sdk_present = True
                if case.outputs[0].is_file() and case.outputs[1].is_file():
                    rows, target_rows, agents = self.fixture.db_readback(case.outputs[0])
                    status = json.loads(case.outputs[1].read_text())
                    if agents == [(case.agent_pub, case.owner, 'cli_agent', 'active')] and sdk_present and 'cli_agent' in status.get('apps', {}):
                        break
                time.sleep(.05)
            else:
                self.fail('bounded normal remote-enabled CLI did not register own agent and actual SDK child')
            self.assertEqual((rows, target_rows), ([], []))
            self.assertEqual(status['bindings'], {})
            self.assertGreaterEqual(len(seen_members), 2)
            self.assertEqual(case.relay.failures, [])
            plan.revalidate(phase='running')
            observed = owned.readback()
            self.assertEqual(observed['status'], 'running')
            self.assertTrue(observed['runtime_started'])
            self.assertEqual(observed['registered_agent_pubkeys'], [case.agent_pub])
            self.assertEqual(observed['registered_agent_app_ids'], ['cli_agent'])
            self.assertEqual((observed['registry_bindings'], observed['target_channel_bindings']), (0, 0))
            self.assertFalse(observed['live_verified'])
            self.assert_inputs(case, original_inputs)
            self.assertEqual(plan.environment(), capture.original_env)
            self.assert_sibling(sibling, sibling_identity)
        finally:
            stopped = owned.stop(timeout=10)  # Original ROOT SIGINT, actual join.
        self.assert_stopped(owned, capture, stopped)
        for identity in seen_members:
            self.assertNotEqual(self.fixture.process_identity(identity[0]), identity)
        rows, target_rows, agents = self.fixture.db_readback(case.outputs[0])
        self.assertEqual((rows, target_rows), ([], []))
        self.assertEqual(agents, [(case.agent_pub, case.owner, 'cli_agent', 'active')])
        self.assert_inputs(case, original_inputs)
        self.assert_sibling(sibling, sibling_identity)

    def test_protected_link_drift_is_rejected_before_popen_without_outputs_or_input_changes(self):
        from hostd.onboarding_runtime import RuntimeConfig
        for link in ('https://localhost:443', 'http://127.0.0.1:443'):
            with self.subTest(link=link):
                case = self.case()
                plan = self.admit(case)
                manifest = case.manifest.read_bytes()
                changed = json.loads(case.onboarding.read_text())
                changed['remote_link_base'] = link
                self.fixture.write(case.onboarding, json.dumps(changed))
                if link.startswith('https:'):
                    self.assertEqual(RuntimeConfig.load(case.onboarding).remote_link_base, link)
                else:
                    from hostd.onboarding_runtime import RuntimeErrorNotice
                    with self.assertRaises(RuntimeErrorNotice):
                        RuntimeConfig.load(case.onboarding)
                before = case.snapshot()
                calls = []
                def forbidden_popen(*args, **kwargs):
                    calls.append((args, kwargs))
                    raise AssertionError('changed protected link reached Popen')
                owned = self.module.OwnedZeroLocalRun(plan, popen_factory=forbidden_popen)
                with self.assertRaises(self.module.ZeroLocalRunError):
                    plan.revalidate(phase='prepared')
                with self.assertRaises(self.module.ZeroLocalRunError):
                    plan.readback()
                with self.assertRaises(self.module.ZeroLocalRunError):
                    owned.start()
                self.assertEqual(calls, [])
                self.assertEqual(case.manifest.read_bytes(), manifest)
                self.assertEqual(case.snapshot(), before)
                self.assert_no_outputs(case)
                self.assertIsNone(case.relay.thread)
                self.assertEqual(case.relay.received, [])

    def test_failed_spawn_and_real_early_exit_reap_only_original_owned_session(self):
        sibling, sibling_identity = self.sibling()
        with self.subTest(failure='before_actual_spawn'):
            case = self.case()
            plan = self.admit(case)
            before = case.snapshot()
            calls = []
            def failed_spawn(argv, **kwargs):
                self.assertEqual(list(argv), plan.argv())
                self.assertEqual(kwargs['env'], plan.environment())
                self.assertTrue(kwargs['start_new_session'])
                self.assertEqual(kwargs['umask'], 0o077)
                calls.append(list(argv))
                raise OSError('test native spawn unavailable')
            owned = self.module.OwnedZeroLocalRun(plan, popen_factory=failed_spawn)
            with self.assertRaises(self.module.ZeroLocalRunError):
                owned.start()
            self.assertEqual(calls, [plan.argv()])
            stopped = owned.stop(timeout=10)
            self.assertEqual(stopped['status'], 'stopped')
            self.assertTrue(stopped['child_reaped'])
            self.assertTrue(stopped['session_reaped'])
            self.assertEqual(owned.stop(timeout=10), stopped)
            self.assertEqual(case.snapshot(), before)
            self.assert_no_outputs(case)
            self.assert_sibling(sibling, sibling_identity)
        with self.subTest(failure='actual_normal_cli_unix_bind_exit'):
            case = self.case(early_exit=True)
            plan = self.admit(case)
            before = case.snapshot()
            capture = self.fixture.CapturePopen(case, plan)
            self.addCleanup(capture.emergency_reap)
            owned = self.module.OwnedZeroLocalRun(plan, popen_factory=capture)
            try:
                self.assertEqual(owned.start()['status'], 'running')
                original = capture.identity
                deadline = time.monotonic()+10
                # Do not poll()/wait(): leave original child for Owned.stop to reap.
                while time.monotonic() < deadline:
                    self.assertEqual(self.fixture.process_identity(capture.process.pid), original)
                    fields = Path(f'/proc/{capture.process.pid}/stat').read_text().rsplit(')', 1)[1].split()
                    if fields[0] == 'Z':
                        break
                    time.sleep(.05)
                else:
                    self.fail('bounded native Unix bind failure did not cause normal CLI early exit')
                self.assertEqual(self.fixture.session_members(capture.process.pid), {original})
                self.assertIsNone(capture.process.returncode)
                self.assert_sibling(sibling, sibling_identity)
            finally:
                stopped = owned.stop(timeout=10)
            self.assert_stopped(owned, capture, stopped)
            self.assertNotEqual(capture.process.returncode, 0)
            self.assertNotEqual(self.fixture.process_identity(original[0]), original)
            rows, target_rows, agents = self.fixture.db_readback(case.outputs[0])
            self.assertEqual((rows, target_rows, agents), ([], [], []))
            self.assertFalse(case.outputs[1].exists())
            self.assertEqual(case.relay.received, [])
            self.assertIsNone(case.relay.thread)
            self.assert_inputs(case, before)
            self.assert_sibling(sibling, sibling_identity)
