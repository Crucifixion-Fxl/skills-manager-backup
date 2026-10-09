"""Two finite contracts for independent host-B normal zero-local startup.

Collect at skills/agent-harness/buzz-agent-setup/tests/test_hostd_l3_zero_local_run.py,
with zero_local_fixture.py beside it. No product testing helper is used.
"""
from __future__ import annotations

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
SCRIPTS = TESTS.parent / 'scripts'
INTEGRATION = TESTS / 'integration' / 'hostd_l3'
MODULE = INTEGRATION / 'zero_local_run.py'
SOURCE = TESTS.parents[3]


class ZeroLocalRunTests(unittest.TestCase):
    def load_candidate(self):
        # Genuine missing support must fail before fixture imports/dependencies.
        self.assertTrue(MODULE.is_file(), 'missing distinct zero-local host-B run support')
        # Ordinary CI may lack real runtime dependencies. Check installed
        # distribution metadata only; never import, fake or relax proof code.
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
                'zero-local normal runtime requires actual dependencies: '+', '.join(missing)+
                '; ordinary CI skip is not proof: BOTH tests must pass in the mandatory '
                'actualdeps environment with zero failures, errors and skips')
        for path in (TESTS, SCRIPTS):
            if str(path) not in sys.path: sys.path.insert(0, str(path))
        spec = importlib.util.spec_from_file_location('hostd_l3_zero_local_run_candidate', MODULE)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module  # Dataclass module lookup is real import behavior.
        self.addCleanup(sys.modules.pop, spec.name, None)
        spec.loader.exec_module(module)
        for name in ('ZeroLocalRunPlan', 'OwnedZeroLocalRun', 'ZeroLocalRunError'):
            self.assertTrue(callable(getattr(module, name, None)), name)
        return module

    def setup_fixture(self):
        import zero_local_fixture as fixture
        self.fixture = fixture
        self.tmp = tempfile.TemporaryDirectory(prefix='hostd-zero-local-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.root.chmod(0o700)
        # Read actual reviewed revision, never inject a fictitious git result.
        result = subprocess.run(['git', '-C', str(SOURCE), 'rev-parse', 'HEAD'], check=True, capture_output=True, text=True, env={'PATH': os.defpath, 'LANG': 'C.UTF-8'}, timeout=5)
        self.revision = result.stdout.strip()
        self.assertRegex(self.revision, r'^[0-9a-f]{40}$')
        self.hashes = {str(p.relative_to(SOURCE)): fixture.digest(p) for p in sorted(SCRIPTS.rglob('*.py'))}

    def case(self):
        case = self.fixture.Fixture(self.root, SOURCE, self.revision, dict(self.hashes))
        self.addCleanup(case.close)
        return case

    def assert_no_outputs(self, case):
        self.assertTrue(all(not p.exists() for p in case.outputs))
        self.assertFalse((case.home/'.local'/'state'/'buzz-hostd').exists())

    def assert_redacted(self, value, case):
        text = json.dumps(value, sort_keys=True) if isinstance(value, dict) else str(value)
        for forbidden in (self.fixture.SECRET, case.agent_key, case.owner_key, 'BUZZ_PRIVATE_KEY', 'appSecret'):
            self.assertNotIn(forbidden, text)

    def padded_elf(self, case, size):
        # A real executable ELF with sparse trailing padding; no mocked size/read.
        shutil.copyfile('/bin/true', case.node)
        with case.node.open('r+b') as stream:
            stream.truncate(size)
        case.node.chmod(0o700)
        self.assertEqual(case.node.stat().st_size, size)
        with case.node.open('rb') as stream:
            self.assertEqual(stream.read(4), b'\x7fELF')
        case.repin()

    def test_declared_pinned_node_accepts_actual_node_size_and_bounded_maximum(self):
        module = self.load_candidate()
        self.setup_fixture()
        for size in (98927992, 128*1024*1024):
            with self.subTest(size=size):
                case = self.case()
                self.padded_elf(case, size)
                before = self.fixture.digest(case.node)
                try:
                    plan = module.ZeroLocalRunPlan.check(case.manifest, **case.check_args)
                    self.assertEqual(plan.readback()['status'], 'prepared')
                    plan.revalidate(phase='prepared')
                finally:
                    self.assertEqual(self.fixture.digest(case.node), before)
                    self.assert_no_outputs(case)

    def test_node_over_limit_and_ordinary_input_over_default_limit_reject_prestart(self):
        module = self.load_candidate()
        self.setup_fixture()
        for kind in ('node', 'cli_entry', 'config'):
            with self.subTest(kind=kind):
                case = self.case()
                if kind == 'node':
                    self.padded_elf(case, 128*1024*1024+1)
                    target = case.node
                else:
                    target = case.entry if kind == 'cli_entry' else case.onboarding
                    with target.open('r+b') as stream:
                        stream.truncate(32*1024*1024+1)
                    case.repin()
                before = self.fixture.digest(target)
                with self.assertRaises(module.ZeroLocalRunError):
                    module.ZeroLocalRunPlan.check(case.manifest, **case.check_args)
                self.assertEqual(self.fixture.digest(target), before)
                self.assert_no_outputs(case)

    def test_large_node_inode_and_content_drift_reject_prestart(self):
        module = self.load_candidate()
        self.setup_fixture()
        for mutation in ('inode', 'content'):
            with self.subTest(mutation=mutation):
                case = self.case()
                self.padded_elf(case, 98927992)
                plan = module.ZeroLocalRunPlan.check(case.manifest, **case.check_args)
                if mutation == 'inode':
                    replacement = case.node.with_name('node-replacement')
                    shutil.copyfile(case.node, replacement)
                    replacement.chmod(0o700)
                    replacement.replace(case.node)
                else:
                    with case.node.open('r+b') as stream:
                        stream.seek(-1, os.SEEK_END)
                        stream.write(b'X')
                before = self.fixture.digest(case.node)
                calls = []
                def forbidden_popen(*args, **kwargs):
                    calls.append((args, kwargs))
                    raise AssertionError('Node drift reached Popen')
                with self.assertRaises(module.ZeroLocalRunError):
                    plan.revalidate(phase='prepared')
                with self.assertRaises(module.ZeroLocalRunError):
                    plan.readback()
                owned = module.OwnedZeroLocalRun(plan, popen_factory=forbidden_popen)
                with self.assertRaises(module.ZeroLocalRunError):
                    owned.start()
                self.assertEqual(calls, [])
                self.assertEqual(self.fixture.digest(case.node), before)
                self.assert_no_outputs(case)

    def test_host_b_manifest_requires_independent_verified_zero_local_topology(self):
        module = self.load_candidate()
        self.setup_fixture()
        case = self.case()
        from hostd.onboarding_runtime import RuntimeConfig
        from hostd import agent_catalog, registry
        cfg = RuntimeConfig.load(case.onboarding)
        catalog = agent_catalog.load(case.catalog, legacy_join_path=case.legacy)
        self.assertEqual(cfg.owner_env_file, str(case.owner_env))
        self.assertNotEqual(case.owner, case.source_owner)
        self.assertEqual([(r.pubkey, r.owner_pubkey, r.app_id, r.status) for r in catalog.records], [(case.agent_pub, case.owner, 'cli_agent', 'own_bot_verified')])
        for only in (None, {case.doc['selector']}):
            loaded = registry.load(case.registry, only=only)
            self.assertEqual(loaded.bindings, {})
            self.assertEqual(loaded.skipped, {})
        before = case.snapshot()
        plan = module.ZeroLocalRunPlan.check(case.manifest, **case.check_args)
        prepared = plan.readback()
        self.assertEqual(prepared['status'], 'prepared')
        self.assertEqual(prepared['registry_bindings'], 0)
        self.assertEqual(prepared['target_channel_bindings'], 0)
        self.assertEqual(prepared['catalog_own_agents'], 1)
        self.assertEqual(prepared['registered_agent_pubkeys'], [])
        self.assertFalse(prepared['runtime_started'])
        self.assertFalse(prepared['live_verified'])
        self.assert_redacted(prepared, case)
        self.assertEqual(case.snapshot(), before)
        self.assert_no_outputs(case)
        self.assertEqual(plan.environment()['HOME'], str(case.home))
        self.assertEqual(plan.environment()['PYTHONPATH'], str(SCRIPTS))
        self.assertNotIn('ZERO_LOCAL_RELAY_PORT', plan.environment())
        self.assertNotIn(str(case.wire_dir), plan.environment().values())

        for mutation in ('selector_collision', 'local_target_binding', 'wrong_owner', 'wrong_source_owner', 'wrong_relay', 'wrong_profile', 'run_id_drift', 'source_revision_drift', 'catalog_drift', 'profile_drift', 'source_hash_pin_mismatch'):
            with self.subTest(mutation=mutation):
                bad = self.case()
                arguments = dict(bad.check_args)
                if mutation == 'source_hash_pin_mismatch':
                    arguments['reviewed_source_hashes'] = dict(arguments['reviewed_source_hashes'])
                    first = next(iter(arguments['reviewed_source_hashes']))
                    arguments['reviewed_source_hashes'][first] = '0'*64
                else:
                    bad.mutate(mutation)
                snapshot = bad.snapshot()
                with self.assertRaises(module.ZeroLocalRunError) as raised:
                    module.ZeroLocalRunPlan.check(bad.manifest, **arguments)
                self.assertIn('怎么解决', str(raised.exception))
                self.assertIn('复制给 AI', str(raised.exception))
                self.assert_redacted(raised.exception, bad)
                self.assertEqual(bad.snapshot(), snapshot)
                self.assert_no_outputs(bad)

        # Drift AFTER admission must be checked at prepared readback and spawn,
        # without writing canonical runtime source or creating a Store/database.
        for mutation in ('catalog_drift', 'profile_drift'):
            with self.subTest(post_check=mutation):
                drift = self.case()
                admitted = module.ZeroLocalRunPlan.check(drift.manifest, **drift.check_args)
                drift.mutate(mutation)
                snapshot = drift.snapshot()
                with self.assertRaises(module.ZeroLocalRunError): admitted.revalidate(phase='prepared')
                with self.assertRaises(module.ZeroLocalRunError): admitted.readback()
                calls = []
                def forbidden_popen(*args, **kwargs):
                    calls.append((args, kwargs))
                    raise AssertionError('drift reached Popen')
                owned = module.OwnedZeroLocalRun(admitted, popen_factory=forbidden_popen)
                with self.assertRaises(module.ZeroLocalRunError): owned.start()
                self.assertEqual(calls, [])
                self.assertEqual(drift.snapshot(), snapshot)
                self.assert_no_outputs(drift)

    def test_owned_normal_hostd_run_registers_own_agents_without_local_binding_and_reaps_only_its_child(self):
        module = self.load_candidate()
        self.setup_fixture()
        case = self.case()
        case.relay.start()
        plan = module.ZeroLocalRunPlan.check(case.manifest, **case.check_args)
        self.assertEqual(plan.argv(), [str(Path(sys.executable).absolute()), '-m', 'hostd', 'run', '--only', case.doc['selector'], '--onboarding-config', str(case.onboarding), '--state-db', str(case.outputs[0]), '--status-file', str(case.outputs[1]), '--console-dir', str(case.outputs[2])])
        sibling = self.fixture.sibling_canary()
        sibling_identity = self.fixture.process_identity(sibling.pid)
        self.addCleanup(self.fixture.reap_sibling, sibling)
        capture = self.fixture.CapturePopen(case, plan)
        self.addCleanup(capture.emergency_reap)
        owned = module.OwnedZeroLocalRun(plan, popen_factory=capture)
        original_inputs = case.snapshot()
        seen_members = set()
        try:
            started = owned.start()
            self.assertEqual(started['status'], 'running')
            self.assertFalse(started['live_verified'])
            self.assertIsNotNone(capture.process)
            deadline = time.monotonic()+20
            while time.monotonic() < deadline:
                self.assertIsNone(capture.process.poll(), 'normal Hostd exited before registration')
                self.assertEqual(self.fixture.process_identity(capture.process.pid), capture.identity)
                current_members = self.fixture.session_members(capture.process.pid)
                seen_members |= current_members
                capture.observed_members |= seen_members
                sdk_argv = [str(Path(sys.executable).absolute()), str(SCRIPTS/'hostd'/'feishu_feed.py'), 'cli_agent', str(case.cfg), str(case.data)]
                sdk_present = False
                for identity in current_members:
                    try:
                        argv = Path(f'/proc/{identity[0]}/cmdline').read_bytes().decode().strip('\0').split('\0')
                    except FileNotFoundError:
                        continue
                    if argv == sdk_argv:
                        sdk_present = True
                if case.outputs[0].is_file() and case.outputs[1].is_file():
                    rows, target_rows, agents = self.fixture.db_readback(case.outputs[0])
                    status = json.loads(case.outputs[1].read_text())
                    if agents == [(case.agent_pub, case.owner, 'cli_agent', 'active')] and 'cli_agent' in status.get('apps', {}) and sdk_present:
                        break
                time.sleep(.05)
            else:
                self.fail('bounded normal startup did not register exact own agents and start SDK child')
            self.assertEqual(rows, [])
            self.assertEqual(target_rows, [])
            self.assertEqual(status['bindings'], {})
            self.assertGreaterEqual(len(seen_members), 2)
            self.assertEqual(case.relay.failures, [])
            self.assertTrue(any(f.get('kinds') == [0] and f.get('authors') == [case.agent_pub] for packet in case.relay.received for f in packet))
            self.assertTrue(any(f.get('kinds') == [30177] and f.get('authors') == [case.owner] and f.get('#d') == [case.agent_pub] for packet in case.relay.received for f in packet))
            plan.revalidate(phase='running')  # Existing child outputs are now valid.
            observed = owned.readback()
            self.assertEqual(observed['status'], 'running')
            self.assertTrue(observed['runtime_started'])
            self.assertEqual(observed['registry_bindings'], 0)
            self.assertEqual(observed['target_channel_bindings'], 0)
            self.assertEqual(observed['registered_agent_pubkeys'], [case.agent_pub])
            self.assertEqual(observed['registered_agent_app_ids'], ['cli_agent'])
            self.assertFalse(observed['live_verified'])
            self.assert_redacted(observed, case)
            for relative, value in original_inputs.items():
                self.assertEqual(case.snapshot()[relative], value)
            self.assertEqual(plan.environment(), capture.original_env)
            self.assertIsNone(sibling.poll())
            self.assertEqual(self.fixture.process_identity(sibling.pid), sibling_identity)
        finally:
            stopped = owned.stop(timeout=10)
        self.assertEqual(stopped['status'], 'stopped')
        self.assertTrue(stopped['child_reaped'])
        self.assertTrue(stopped['session_reaped'])
        self.assertIsNotNone(capture.process.poll())
        self.assertEqual(self.fixture.session_members(capture.process.pid), set())
        for identity in seen_members:
            self.assertNotEqual(self.fixture.process_identity(identity[0]), identity)
        self.assertIsNone(sibling.poll())  # Preserve unrelated live sibling.
        self.assertEqual(self.fixture.process_identity(sibling.pid), sibling_identity)
        self.assertEqual(owned.stop(timeout=10), stopped)
