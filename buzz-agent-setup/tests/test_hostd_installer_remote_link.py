"""Tests-first proposal: startup remote-link validation matches runtime."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'install_hostd.py'
sys.path.insert(0, str(SCRIPT.parent))
_spec = importlib.util.spec_from_file_location('install_hostd_remote_link_test', SCRIPT)
_installer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_installer)
from hostd.onboarding_runtime import RuntimeConfig, RuntimeErrorNotice


class InstallerRemoteLinkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.startup_path = self.root / 'startup.json'
        self.release = self.root / 'release'
        self.release.mkdir(mode=0o700)

    def startup_value(self, **extra):
        value = dict(
            version=1,
            owner_env_file=str(self.root / 'owner.env'),
            relay_url='wss://relay.example.test',
            relay_pubkey='a' * 64,
            template_config=str(self.root / 'template.json'),
            binding_dir=str(self.root / 'bindings'),
            legacy_join_path=str(self.root / 'legacy.json'),
            catalog_path=str(self.root / 'catalog.json'),
            trusted_relays=['wss://relay.example.test'],
        )
        value.update(extra)
        return value

    def write_startup(self, value):
        raw = json.dumps(value, sort_keys=True, separators=(',', ':')).encode()
        self.startup_path.write_bytes(raw)
        self.startup_path.chmod(0o600)
        return raw, hashlib.sha256(raw).hexdigest()

    def assert_installer_accepts(self, pin):
        try:
            return _installer.onboarding(self.startup_path, pin, self.release)
        except _installer.InstallError as error:
            self.fail(f'installer rejected runtime-valid startup ({error.code})')

    def assert_install_invalid(self, pin):
        with self.assertRaises(_installer.InstallError) as caught:
            _installer.onboarding(self.startup_path, pin, self.release)
        self.assertEqual(caught.exception.code, 'startup_invalid')
        self.assertNotIn('remote_link_base', str(caught.exception))

    def test_valid_https_origin_with_explicit_port_matches_runtime(self):
        raw, pin = self.write_startup(self.startup_value(
            remote_link_base='https://buzz.example.test:8443'))
        loaded = RuntimeConfig.load(str(self.startup_path))
        result = self.assert_installer_accepts(pin)
        self.assertEqual(loaded.remote_link_base, 'https://buzz.example.test:8443')
        self.assertEqual(result, dict(
            status='pinned', source_path=str(self.startup_path),
            candidate_path=str(self.release / 'onboarding-config.json'),
            sha256=hashlib.sha256(raw).hexdigest()))
        self.assertEqual(self.startup_path.read_bytes(), raw)
        self.assertEqual(self.startup_path.stat().st_mode & 0o777, 0o600)

    def test_omitted_and_explicit_empty_origin_keep_existing_default(self):
        omitted, pin = self.write_startup(self.startup_value())
        self.assertEqual(RuntimeConfig.load(str(self.startup_path)).remote_link_base, '')
        self.assertEqual(self.assert_installer_accepts(pin)['status'], 'pinned')
        self.assertEqual(self.startup_path.read_bytes(), omitted)

        empty, pin = self.write_startup(self.startup_value(remote_link_base=''))
        self.assertEqual(RuntimeConfig.load(str(self.startup_path)).remote_link_base, '')
        self.assertEqual(self.assert_installer_accepts(pin)['status'], 'pinned')
        self.assertEqual(self.startup_path.read_bytes(), empty)

    def test_invalid_types_and_origins_match_runtime_rejection(self):
        invalid = (
            None, 7, [],
            'http://buzz.example.test',
            'https://user@buzz.example.test',
            'https://buzz.example.test/path',
            'https://buzz.example.test?query',
            'https://buzz.example.test#fragment',
            'https://buzz.example.test/with-space',
            'https://buzz.example.test\n',
            'https://buzz%2eexample.test',
            'https://buzz\\example.test',
            'https://buzz.example.test:',
            'https://buzz.example.test:invalid',
            'https://buzz.example.test:65536',
        )
        for value in invalid:
            with self.subTest(value_type=type(value).__name__):
                _, pin = self.write_startup(self.startup_value(remote_link_base=value))
                with self.assertRaises(RuntimeErrorNotice):
                    RuntimeConfig.load(str(self.startup_path))
                self.assert_install_invalid(pin)

    def test_pin_change_or_removal_stays_private_and_has_no_publish_side_effects(self):
        original, pin = self.write_startup(self.startup_value(
            remote_link_base='https://buzz.example.test:8443'))
        receipt = self.assert_installer_accepts(pin)
        public = _installer.public_plan({'onboarding': receipt})
        self.assertNotIn('source_path', public['onboarding'])
        self.assertEqual(public['onboarding']['candidate_path'],
                         str(self.release / 'onboarding-config.json'))
        self.assertEqual(public['onboarding']['sha256'], pin)

        changed, _ = self.write_startup(self.startup_value(
            remote_link_base='https://changed.example.test'))
        with self.assertRaises(_installer.InstallError) as caught:
            _installer.onboarding(self.startup_path, pin, self.release)
        self.assertEqual(caught.exception.code, 'startup_changed')
        self.assertNotIn(str(self.startup_path), str(caught.exception))
        self.assertNotIn('changed.example.test', str(caught.exception))

        self.startup_path.unlink()
        with self.assertRaises(_installer.InstallError) as missing:
            _installer.onboarding(self.startup_path, pin, self.release)
        self.assertEqual(missing.exception.code, 'unsafe_file')
        self.assertNotIn(str(self.startup_path), str(missing.exception))
        self.assertEqual(list(self.release.iterdir()), [])
        self.assertEqual(sorted(path.name for path in self.root.iterdir()), ['release'])
        self.assertEqual(hashlib.sha256(original).hexdigest(), pin)
        self.assertNotEqual(changed, original)
