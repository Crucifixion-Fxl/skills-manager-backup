"""L2 install proof with real process birth/executable/env and private files.

Native is an external process/snapshot fixture, not a real native L3 claim.
Only the systemd MainPID lookup is supplied at the external manager boundary.
"""
import copy
import importlib
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import test_recovery_controller as fixtures


class RecoveryInstallRuntimeTests(unittest.TestCase):
    write = fixtures.ControllerTests.write
    write_snapshot = fixtures.ControllerTests.write_snapshot
    def setUp(self):
        fixtures.ControllerTests.setUp(self)
        self.addCleanup(fixtures.ControllerTests.tearDown, self)
        self.module = importlib.import_module("recovery_install_runtime")

    def verify(self, main_pid=None):
        return self.module.verify_inventory(self.config, main_pid=main_pid or (lambda unit: self.pid))

    def test_L2_REC_LIVE_001_exact_live_process_and_snapshot_are_proven_without_writes(self):
        before = {p: p.read_bytes() for p in self.base.rglob("*") if p.is_file()}
        result = self.verify()
        self.assertEqual([r["name"] for r in result], ["test-dev"])
        self.assertEqual(result[0]["generation"], self.current["generation"])
        self.assertEqual(result[0]["binary_sha256"], self.digest)
        self.assertEqual(before, {p: p.read_bytes() for p in self.base.rglob("*") if p.is_file()})

    def test_L2_REC_LIVE_002_new_files_do_not_prove_old_loaded_revision_or_other_binary(self):
        original = copy.deepcopy(self.config)
        old_env = self.agent_env.read_text()
        for key, value in (("revision", "b" * 40), ("binary_sha256", "0" * 64)):
            with self.subTest(key=key):
                self.config = copy.deepcopy(original)
                self.config["agents"][0][key] = value
                self.write(self.agent_env, old_env.replace(original["agents"][0][key], value))
                with self.assertRaises(self.module.RuntimeProofError):
                    self.verify()

    def test_L2_REC_LIVE_003_no_live_or_multiple_live_generations_are_not_ready(self):
        with self.assertRaises(self.module.RuntimeProofError):
            self.verify(main_pid=lambda unit: 0)
        duplicate = copy.deepcopy(self.current)
        duplicate["generation"] = "00000000-0000-0000-0000-000000000097"
        self.write_snapshot(duplicate)
        with self.assertRaises(self.module.RuntimeProofError):
            self.verify()

    def test_L2_REC_LIVE_004_any_schema_policy_identity_phase_or_history_gap_blocks(self):
        original = copy.deepcopy(self.current)
        for update in ({"version": 8}, {"phase": "starting"}, {"phase": "stopping"},
                       {"agent_pubkey": "0" * 64}, {"relay": "https://elsewhere.invalid"},
                       {"runtime_policy": {"owner": "0" * 64, "respond_to": "anyone", "allowlist": []}},
                       {"input_sources": {}}, {"process_start_ticks": "0"}):
            with self.subTest(keys=list(update)):
                self.current = {**original, **update}
                # Make input_sources={} genuinely invalid, not an empty-work control.
                if "input_sources" in update:
                    self.current["active"] = copy.deepcopy(self.old["active"])
                    self.current["triggers"] = copy.deepcopy(self.old["triggers"])
                self.write_snapshot(self.current)
                with self.assertRaises(self.module.RuntimeProofError):
                    self.verify()
        self.current = original
        self.write_snapshot(self.current)
        self.old["version"] = 8
        self.write_snapshot(self.old)
        with self.assertRaises(self.module.RuntimeProofError):
            self.verify()

    def test_L2_REC_LIVE_005_actual_subscriptions_must_match_fixed_approved_channels(self):
        original = self.agent_env.read_text()
        self.write(self.agent_env, original + "BUZZ_ACP_CHANNELS=" + ",".join(self.current["channels"]) + "\n")
        self.verify()
        self.current["channels"] = self.current["channels"][:1]
        self.write_snapshot(self.current)
        with self.assertRaises(self.module.RuntimeProofError):
            self.verify()

    def test_L2_REC_LIVE_006_changed_manager_pid_and_missing_second_agent_never_partially_pass(self):
        pids = iter([self.pid, 0])
        with self.assertRaises(self.module.RuntimeProofError):
            self.verify(main_pid=lambda unit: next(pids))
        another = {**self.config["agents"][0], "name": "other-dev", "pubkey": "3" * 64,
                   "unit": "buzz-other-dev.service", "journal_dir": str(self.base / "missing")}
        self.config["agents"].append(another)
        with self.assertRaises(self.module.RuntimeProofError) as caught:
            self.verify()
        self.assertEqual(caught.exception.agent, "other-dev")
        self.assertFalse(caught.exception.public()["installed"])
        self.assertNotIn(fixtures.KEY, str(caught.exception.public()))
