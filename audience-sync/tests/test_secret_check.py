from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class SecretCheckTests(unittest.TestCase):
    def test_repository_passes_deterministic_secret_gate(self) -> None:
        completed = subprocess.run(
            [sys.executable, "scripts/check_secrets.py"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout)
        self.assertEqual(completed.stdout.strip(), "secret_check_ok")

    def test_both_personal_key_versions_report_only_path_and_rule(self) -> None:
        for version in (1, 2):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                secret = f"awpk_v{version}_" + "a" * 26 + "_" + "A" * 43
                (root / "unsafe.txt").write_text(secret, encoding="utf-8")
                completed = subprocess.run(
                    [sys.executable, str(ROOT / "scripts/check_secrets.py"), "--root", str(root)],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(completed.returncode, 1)
                self.assertEqual(
                    completed.stdout,
                    "secret_check_failed:unsafe.txt:audience_personal_key\n",
                )
                self.assertEqual(completed.stderr, "")
                self.assertNotIn(secret, completed.stdout + completed.stderr)

    def test_environment_only_personal_keys_are_outside_file_scan(self) -> None:
        for version in (1, 2):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                secret = f"awpk_v{version}_" + "a" * 26 + "_" + "A" * 43
                (root / "safe.txt").write_text("AUDIENCE_SYNC_API_KEY", encoding="utf-8")
                completed = subprocess.run(
                    [sys.executable, str(ROOT / "scripts/check_secrets.py"), "--root", str(root)],
                    cwd=ROOT,
                    env={**os.environ, "AUDIENCE_SYNC_API_KEY": secret},
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(completed.returncode, 0)
                self.assertEqual(completed.stdout, "secret_check_ok\n")
                self.assertEqual(completed.stderr, "")
                self.assertNotIn(secret, completed.stdout + completed.stderr)

    def test_finding_reports_only_path_and_rule_not_secret(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            secret = "gl" + "pat-" + "A" * 24
            (root / "unsafe.txt").write_text(secret, encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, str(ROOT / "scripts/check_secrets.py"), "--root", str(root)],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 1)
            self.assertEqual(
                completed.stdout.strip(),
                "secret_check_failed:unsafe.txt:gitlab_token",
            )
            self.assertNotIn(secret, completed.stdout + completed.stderr)


if __name__ == "__main__":
    unittest.main()
