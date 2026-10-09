"""L2 installer readiness: real process/signal, private journal and files.

The child is the external native-runtime boundary fixture; it publishes a ready
journal only when it actually receives SIGUSR1. No business process is used.
Systemd CLI and native capability are the same declared L2 doubles as apply.
"""
import hashlib
import importlib
import json
from pathlib import Path
import select
import subprocess
import sys
import time
import unittest

from test_recovery_install_apply import RecoveryInstallApplyFixture
from test_install_agent_recovery import EXPECTED


CHILD = r'''
import json, os, pathlib, signal, sys
journal, marker, mode = map(pathlib.Path, sys.argv[1:])
def awake(*_):
    with marker.open('a') as output:
        output.write('prewarmed\n')
    behavior = mode.read_text()
    if behavior == 'hang':
        return
    value = json.loads(journal.read_text())
    value['phase'] = 'ready'
    if behavior == 'wrong-channel':
        value['channels'] = []
    if behavior == 'new-generation':
        value['generation'] = '00000000-0000-4000-8000-000000000199'
    # load_snapshots rejects every visible extra file. Only a hidden
    # ".*.tmp" is an uncommitted atomic write, so the scan can land here.
    temporary = journal.with_name('.' + journal.name + '.tmp')
    with temporary.open('w') as output:
        os.fchmod(output.fileno(), 0o600)
        json.dump(value, output)
        output.flush()
        os.fsync(output.fileno())
    target = journal.with_name(value['generation'] + '.json')
    os.replace(temporary, target)
    if target != journal:
        journal.unlink()
signal.signal(signal.SIGUSR1, awake)
print('signal-ready', flush=True)
while True:
    signal.pause()
'''


class RecoveryReadinessTests(RecoveryInstallApplyFixture):
    def setUp(self):
        super().setUp()
        self.child.terminate()
        self.child.wait(timeout=5)
        self.marker = self.home / 'signal-observed'
        self.mode = self.home / 'fixture-mode'
        self.write(self.mode, 'ready')
        binary = Path(sys.executable).resolve(strict=True)
        self.child = subprocess.Popen([str(binary), '-I', '-c', CHILD, str(self.snapshot_path),
                                       str(self.marker), str(self.mode)],
                                      env={'BUZZ_ACP_RECOVERY_REVISION': EXPECTED}, stdout=subprocess.PIPE)
        self.addCleanup(self.child.stdout.close)
        self.assertTrue(select.select([self.child.stdout], [], [], 5)[0])
        self.assertEqual(self.child.stdout.readline(), b'signal-ready\n')
        env = self.agents / 'demo-dev.env'
        old = hashlib.sha256(Path('/usr/bin/sleep').read_bytes()).hexdigest()
        digest = hashlib.sha256(binary.read_bytes()).hexdigest()
        self.write(env, env.read_text().replace('/usr/bin/sleep', str(binary)).replace(old, digest))
        cli_link = self.home / '.local/bin/buzz-acp'
        if cli_link.is_symlink():
            cli_link.unlink()
            cli_link.symlink_to(binary)
        self.current.update(pid=self.child.pid, phase='starting', process_start_ticks=
                            Path(f'/proc/{self.child.pid}/stat').read_text().rsplit(') ', 1)[1].split()[19])
        self.write(self.snapshot_path, json.dumps(self.current))
        self.journal_dir.chmod(0o700)

    def ready(self, config=None, **kwargs):
        module = importlib.import_module('recovery_install_runtime')
        return module.ensure_ready(config or self.plan()['config'], main_pid=lambda unit: self.child.pid, **kwargs)

    def test_L2_REC_READY_001_idle_agent_is_actually_woken_and_ready_before_install_succeeds(self):
        before = (self.agents / 'demo-dev.env').read_bytes()
        self.assertFalse(self.marker.exists())
        try:
            result = self.apply()
        except self.apply_module.ApplyError as error:
            self.fail(f'verified idle Agent must prewarm, then prove readiness: {error.public()}')
        self.assertTrue(result['installed'])
        self.assertEqual(self.marker.read_text(), 'prewarmed\n')
        actual = json.loads(self.snapshot_path.read_text())
        self.assertEqual(actual['phase'], 'ready')
        self.assertEqual(actual['active'], self.current['active'])
        self.assertEqual(actual['generation'], self.current['generation'])
        self.assertEqual((self.agents / 'demo-dev.env').read_bytes(), before)

    def test_L2_REC_READY_002_invalid_runtime_owner_is_never_signalled(self):
        self.current['runtime_policy']['owner'] = 'c' * 64
        self.write(self.snapshot_path, json.dumps(self.current))
        self.journal_dir.chmod(0o700)
        with self.assertRaises(self.apply_module.ApplyError):
            self.apply()
        self.assertFalse(self.marker.exists())
        self.assertEqual(self.verbs(), [])
        self.assertIsNone(self.child.poll())

    def test_L2_REC_READY_004_actual_signal_without_readiness_times_out_and_preserves_work(self):
        self.write(self.mode, 'hang')
        original = self.snapshot_path.read_bytes()
        config = self.plan()['config']
        module = importlib.import_module('recovery_install_runtime')
        start = time.monotonic()
        with self.assertRaises(module.RuntimeProofError) as caught:
            self.ready(config, timeout=0.15)
        elapsed = time.monotonic() - start
        self.assertGreaterEqual(elapsed, 0.15)
        self.assertLess(elapsed, 2)
        self.assertEqual(caught.exception.code, 'recovery_provider_not_ready')
        self.assertIn('模型', caught.exception.public()['remediation'])
        self.assertTrue(self.marker.exists(), 'timeout must follow a delivered prewarm signal')
        self.assertEqual(self.marker.read_text(), 'prewarmed\n', 'do not flood one idle generation with signals')
        self.assertEqual(self.snapshot_path.read_bytes(), original)
        self.assertIsNone(self.child.poll())

    def test_L2_REC_READY_005_invalid_second_agent_blocks_every_prewarm(self):
        config = self.plan()['config']
        second = {**config['agents'][0], 'name': 'other-dev', 'pubkey': 'c' * 64,
                  'unit': 'buzz-other-dev.service', 'journal_dir': str(self.home / 'missing-journal')}
        config['agents'].append(second)
        with self.assertRaises(ValueError):
            self.ready(config)
        self.assertFalse(self.marker.exists())

    def test_L2_REC_READY_006_generation_change_is_not_accepted_as_original_ready_proof(self):
        self.write(self.mode, 'new-generation')
        with self.assertRaises(ValueError):
            self.ready()
        self.assertEqual(self.marker.read_text(), 'prewarmed\n')

    def test_L2_REC_READY_007_invalid_wait_bounds_do_not_signal(self):
        for timeout in (True, 0, -1, 301, float('nan'), float('inf')):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                self.ready(timeout=timeout)
        self.assertFalse(self.marker.exists())
        self.assertIsNone(self.child.poll())

    def test_L2_REC_READY_003_signal_receipt_does_not_prove_actual_channel_readiness(self):
        self.write(self.mode, 'wrong-channel')
        with self.assertRaises(self.apply_module.ApplyError) as caught:
            self.apply()
        self.assertTrue(self.marker.exists(), 'must reach real signal path before testing changed ready channels')
        self.assertEqual(caught.exception.public()['stage'], 'runtime_before')
        self.assertFalse(self.config_path.exists())
        self.assertEqual(self.verbs(), [])


if __name__ == '__main__':
    unittest.main()
