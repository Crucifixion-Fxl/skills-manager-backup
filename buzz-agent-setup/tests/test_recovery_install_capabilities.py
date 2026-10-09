"""L2 recovery installer capability process boundary; no business service runs.

Hash/path validation uses the real ELF /usr/bin/true. A child Python process
replaces only the native capability response; it retains the production file
descriptors, environment, output limits and process-group handling.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import install_agent_recovery as install


class RecoveryCapabilityTests(unittest.TestCase):
    def setUp(self):
        self.binary = Path("/usr/bin/true")
        self.digest = hashlib.sha256(self.binary.read_bytes()).hexdigest()
        self.popen = subprocess.Popen

    def response(self, output, *, status=0):
        def spawn(arguments, **kwargs):
            self.assertEqual(arguments[1:], ["recovery-schema"])
            self.assertTrue(arguments[0].startswith("/proc/self/fd/"))
            code = ("import os,sys; "
                    "assert 'BUZZ_PRIVATE_KEY' not in os.environ; "
                    "assert 'BUZZ_ACP_AGENT_OWNER' not in os.environ; "
                    f"sys.stdout.buffer.write({output!r}); sys.exit({status})")
            return self.popen([sys.executable, "-I", "-c", code], **kwargs)
        return mock.patch.object(install.subprocess, "Popen", side_effect=spawn)

    def reject(self, output, **kwargs):
        with self.response(output, **kwargs):
            with self.assertRaises(install.PlanError) as caught:
                install.probe_binary(self.binary, self.digest)
        self.assertEqual(caught.exception.code, "binary_recovery_capability_unverified")
        self.assertFalse(caught.exception.public()["installed"])

    def test_L2_REC_CAP_001_exact_current_contract_without_credentials(self):
        with mock.patch.dict(os.environ, {"BUZZ_PRIVATE_KEY": "fixture-do-not-forward", "BUZZ_ACP_AGENT_OWNER": "owner"}):
            with self.response(json.dumps(install.CAPABILITIES).encode()):
                self.assertIsNone(install.probe_binary(self.binary, self.digest))

    def test_L2_REC_CAP_002_duplicate_version_is_not_a_capability_proof(self):
        value = json.dumps(install.CAPABILITIES)
        self.reject(b'{"version":1,' + value[1:].encode())

    def test_L2_REC_CAP_003_oversized_whitespace_is_not_accepted_by_truncation(self):
        self.reject(json.dumps(install.CAPABILITIES).encode() + b" " * 5000)

    def test_L2_REC_CAP_004_old_unknown_and_wrong_typed_fields_fail_closed(self):
        for key, value in (("version", 8), ("version", 9.0), ("durable_admission", 1),
                           ("durable_admission", False), ("unknown_capability", True)):
            with self.subTest(field=key, value=value):
                contract = dict(install.CAPABILITIES, **{key: value})
                self.reject(json.dumps(contract).encode())

    def test_L2_REC_CAP_006_v10_contract_requires_storage_feedback_and_bounded_retention(self):
        """Native schema 10 (skills#157): durable capacity/storage feedback and bounded terminal retention."""
        v10 = {"bounded_terminal_retention": True, "cancellation_tombstones": True, "deferred_same_attempt": True,
               "delivery_receipts": True, "durable_admission": True, "durable_denial_notices": True,
               "durable_shutdown_notices": True, "durable_storage_feedback": True, "exact_attempt_binding": True,
               "generation_fence": True, "original_source_bindings": True, "prewarm_signal": "SIGUSR1",
               "runtime_policy_snapshot": True, "startup_prewarm": True, "version": 10}
        with self.response(json.dumps(v10).encode()):
            self.assertIsNone(install.probe_binary(self.binary, self.digest))
        v9 = {key: value for key, value in v10.items()
              if key not in ("durable_storage_feedback", "bounded_terminal_retention")} | {"version": 9}
        self.reject(json.dumps(v9).encode())

    def test_L2_REC_CAP_005_invalid_non_object_and_failed_child_are_visible(self):
        for output in (b"[]", b"null", b"\xff", b"{bad", b""):
            with self.subTest(output=output):
                self.reject(output)
        self.reject(json.dumps(install.CAPABILITIES).encode(), status=1)

    def test_L2_REC_CAP_006_digest_and_symlink_rejection_precede_execution(self):
        with self.response(json.dumps(install.CAPABILITIES).encode()) as process:
            with self.assertRaises(install.PlanError):
                install.probe_binary(self.binary, "f" * 64)
            with tempfile.TemporaryDirectory() as directory:
                link = Path(directory) / "native-link"
                link.symlink_to(self.binary)
                with self.assertRaises(install.PlanError):
                    install.probe_binary(link, self.digest)
            process.assert_not_called()
