"""One admission regression for protected remote-enabled zero-local inputs.

No daemon, SDK, remote grant, dispatch or native MSG003 action is exercised.
Collect beside the existing zero_local_fixture.py in the reviewed FULL layout.
"""
from __future__ import annotations

import copy
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

TESTS = Path(__file__).resolve().parent
SCRIPTS = TESTS.parent/'scripts'
SOURCE = TESTS.parents[3]
MODULE = TESTS/'integration'/'hostd_l3'/'zero_local_run.py'


class ZeroLocalRemoteLinkAdmissionTests(unittest.TestCase):
    def _load_candidate(self):
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
                'protected remote-link admission requires actual dependencies: '+', '.join(missing)+
                '; ordinary CI skip is not proof: this ONE regression must pass in the '
                'mandatory actualdeps environment with zero failures, errors and skips')
        for path in (TESTS, SCRIPTS):
            if str(path) not in sys.path:
                sys.path.insert(0, str(path))
        spec = importlib.util.spec_from_file_location('hostd_l3_zero_remote_link_candidate', MODULE)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        self.addCleanup(sys.modules.pop, spec.name, None)
        spec.loader.exec_module(module)
        self.assertTrue(callable(getattr(module, 'ZeroLocalRunPlan', None)))
        self.assertTrue(callable(getattr(module, 'ZeroLocalRunError', None)))
        return module

    def _assert_prepared(self, plan, case):
        observed = plan.readback()
        self.assertEqual(observed['status'], 'prepared')
        self.assertEqual(observed['registry_bindings'], 0)
        self.assertEqual(observed['target_channel_bindings'], 0)
        self.assertEqual(observed['catalog_own_agents'], 1)
        self.assertEqual(observed['registered_agent_pubkeys'], [])
        self.assertEqual(observed['registered_agent_app_ids'], [])
        self.assertFalse(observed['runtime_started'])
        self.assertFalse(observed['live_verified'])
        self.assertTrue(all(not p.exists() for p in case.outputs))
        self.assertFalse((case.home/'.local').exists())
        return observed

    def test_protected_https_remote_link_admission_preserves_zero_local_pins_and_rejects_drift(self):
        module = self._load_candidate()
        import zero_local_fixture as fixture
        from hostd import agent_catalog, registry
        from hostd.onboarding_runtime import RuntimeConfig

        temp = tempfile.TemporaryDirectory(prefix='hostd-remote-link-admission-')
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        root.chmod(0o700)
        revision_result = subprocess.run(
            ['git', '-C', str(SOURCE), 'rev-parse', 'HEAD'], check=True,
            capture_output=True, text=True, timeout=5,
            env={'PATH': os.defpath, 'LANG': 'C.UTF-8'})
        revision = revision_result.stdout.strip()
        self.assertRegex(revision, r'^[0-9a-f]{40}$')
        hashes = {str(p.relative_to(SOURCE)): fixture.digest(p) for p in sorted(SCRIPTS.rglob('*.py'))}
        case = fixture.Fixture(root, SOURCE, revision, hashes)
        self.addCleanup(case.close)
        reviewer_arguments = copy.deepcopy(case.check_args)
        initial_document = copy.deepcopy(case.doc)
        initial_inputs = case.snapshot()
        catalog = agent_catalog.load(case.catalog, legacy_join_path=case.legacy)
        self.assertEqual([(r.pubkey, r.owner_pubkey, r.app_id, r.status) for r in catalog.records],
                         [(case.agent_pub, case.owner, 'cli_agent', 'own_bot_verified')])
        self.assertNotEqual(case.owner, case.source_owner)
        for only in (None, {case.doc['selector']}):
            actual = registry.load(case.registry, only=only)
            self.assertEqual(actual.bindings, {})
            self.assertEqual(actual.skipped, {})
        self.assertEqual(RuntimeConfig.load(case.onboarding).remote_link_base, '')
        empty_plan = module.ZeroLocalRunPlan.check(case.manifest, **reviewer_arguments)
        self.assertIs(type(empty_plan), module.ZeroLocalRunPlan)
        self._assert_prepared(empty_plan, case)
        self.assertEqual(case.snapshot(), initial_inputs)

        # Select the actual private HTTPS origin BEFORE reading/modifying JSON.
        # Do not add a manifest key or caller override, and do not start the relay.
        expected_link = case.relay.origin
        self.assertTrue(expected_link.startswith('https://127.0.0.1:'))
        protected = json.loads(case.onboarding.read_text())
        protected['remote_link_base'] = expected_link
        fixture.write(case.onboarding, json.dumps(protected))
        case.repin()  # Genuine hashes of the complete protected input bytes.
        self.assertEqual(set(case.doc), set(initial_document))
        for key in initial_document:
            if key != 'input_sha256':
                self.assertEqual(case.doc[key], initial_document[key])
        self.assertEqual(case.check_args, reviewer_arguments)
        relative_config = str(case.onboarding.relative_to(case.root))
        self.assertEqual(case.doc['input_sha256'][relative_config], fixture.digest(case.onboarding))
        for name, pin in initial_document['input_sha256'].items():
            if name != relative_config:
                self.assertEqual(case.doc['input_sha256'][name], pin)
        protected_before_admission = case.snapshot()
        config = RuntimeConfig.load(case.onboarding)  # Actual typed HTTPS policy.
        self.assertEqual(config.remote_link_base, expected_link)
        self.assertEqual(config.relay_pubkey, case.relay_pub)
        self.assertEqual(config.owner_env_file, str(case.owner_env))
        self.assertEqual(config.catalog_path, str(case.catalog))
        self.assertEqual(config.legacy_join_path, str(case.legacy))
        try:
            plan = module.ZeroLocalRunPlan.check(case.manifest, **reviewer_arguments)
        except module.ZeroLocalRunError:
            # Targeted genuine RED: existing admission's blank-only restriction,
            # AFTER original real topology and typed protected config succeeded.
            self.fail('zero-local admission rejects valid protected HTTPS remote_link_base')
        self.assertIs(type(plan), module.ZeroLocalRunPlan)
        self.assertEqual(plan.argv(), empty_plan.argv())
        self.assertEqual(plan.environment(), empty_plan.environment())
        plan.revalidate(phase='prepared')
        self._assert_prepared(plan, case)
        self.assertEqual(case.snapshot(), protected_before_admission)
        self.assertEqual(case.relay.received, [])
        self.assertEqual(case.relay.failures, [])
        self.assertIsNone(case.relay.thread)
        for relative, value in initial_inputs.items():
            if relative not in ('manifest.json', relative_config):
                self.assertEqual(case.snapshot()[relative], value)
        self.assertEqual(hashes, {str(p.relative_to(SOURCE)): fixture.digest(p) for p in sorted(SCRIPTS.rglob('*.py'))})

        # Another VALID typed HTTPS URI at the SAME protected path, unsealed.
        # Actual config validation still passes; frozen input revalidation must fail.
        original_manifest = case.manifest.read_bytes()
        drifted = dict(protected, remote_link_base='https://localhost:'+str(case.relay.server.server_port))
        fixture.write(case.onboarding, json.dumps(drifted))
        self.assertNotEqual(drifted['remote_link_base'], expected_link)
        self.assertEqual(RuntimeConfig.load(case.onboarding).remote_link_base, drifted['remote_link_base'])
        self.assertEqual(case.manifest.read_bytes(), original_manifest)
        drifted_inputs = case.snapshot()
        with self.assertRaises(module.ZeroLocalRunError):
            plan.revalidate(phase='prepared')
        with self.assertRaises(module.ZeroLocalRunError):
            plan.readback()
        self.assertEqual(case.snapshot(), drifted_inputs)
        self.assertTrue(all(not p.exists() for p in case.outputs))
        self.assertFalse((case.home/'.local').exists())
        self.assertEqual(case.relay.received, [])
        self.assertIsNone(case.relay.thread)
