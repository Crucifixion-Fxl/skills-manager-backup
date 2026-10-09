"""L1 error projection: human cause must not erase forward-fix/rollback fences."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from recovery_install_apply import ApplyError
from recovery_install_files import InstallationFileError
from recovery_install_runtime import RuntimeProofError


class InstallErrorTests(unittest.TestCase):
    def test_L1_REC_ERROR_001_safe_rollback_exposes_the_actual_readiness_reason(self):
        wrapper = InstallationFileError("installation_write_failed")
        wrapper.__cause__ = RuntimeProofError("test-dev", "recovery_provider_not_ready")
        result = ApplyError("runtime_before", wrapper).public()
        self.assertEqual(result["error"], "recovery_provider_not_ready")
        self.assertEqual(result["agent"], "test-dev")
        self.assertEqual(result["rollback"], "completed")
        self.assertIn("模型", result["remediation"])
        self.assertFalse(result["installed"])

    def test_L1_REC_ERROR_002_failed_rollback_or_possible_publication_keeps_primary_warning(self):
        for code in ("installation_rollback_conflict", "installation_rollback_unverified", "installation_runtime_unverified"):
            with self.subTest(code=code):
                wrapper = InstallationFileError(code)
                wrapper.__cause__ = RuntimeProofError("test-dev", "recovery_provider_not_ready")
                result = ApplyError("runtime_after", wrapper).public()
                self.assertEqual(result["error"], code)
                self.assertNotIn("rollback", result)
                self.assertEqual(result["cause"]["error"], "recovery_provider_not_ready")
                self.assertFalse(result["installed"])

    def test_L1_REC_ERROR_003_actual_file_error_remains_primary_and_never_leaks_exception(self):
        wrapper = InstallationFileError("installation_write_failed")
        wrapper.__cause__ = OSError("private-path private-key")
        result = ApplyError("publish", wrapper).public()
        self.assertEqual(result["error"], "installation_write_failed")
        self.assertNotIn("private", str(result))
        self.assertFalse(result["installed"])


if __name__ == "__main__":
    unittest.main()
