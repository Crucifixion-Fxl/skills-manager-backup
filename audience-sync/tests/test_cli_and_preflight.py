from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUDIENCE_ID = "aud_" + "a" * 26
SYNC_API_KEY = "awpk_v1_" + "a" * 26 + "_" + "A" * 43


class CliAndPreflightTests(unittest.TestCase):
    def test_clone_direct_capabilities_needs_no_install_or_credentials(self) -> None:
        completed = subprocess.run(
            [sys.executable, "scripts/api.py", "capabilities"],
            cwd=ROOT,
            env={},
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["contract_revision"], "audience-project-v3")
        self.assertEqual(payload["capability_source"], "local_adapter")
        self.assertNotIn("effects_available", payload)
        self.assertNotIn("effects_require_confirmation", payload)
        self.assertEqual(len(payload["operations"]), 14)

    def test_public_discovery_excludes_compatibility_and_rejects_old_commands(self) -> None:
        from audience_sync.operations import OPERATION_SPECS
        from audience_sync.project_operations import (
            PERSONAL_KEY_OPERATION_SPECS,
            PROJECT_OPERATION_SPECS,
        )

        help_result = subprocess.run(
            [sys.executable, "scripts/api.py", "--help"], cwd=ROOT, env={},
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(help_result.returncode, 0)
        result = subprocess.run(
            [sys.executable, "scripts/api.py", "capabilities"], cwd=ROOT, env={},
            capture_output=True, text=True, check=False,
        )
        payload = json.loads(result.stdout)
        expected = {
            operation.value
            for operation in (*PROJECT_OPERATION_SPECS, *PERSONAL_KEY_OPERATION_SPECS)
        }
        self.assertEqual(set(payload["operations"]), expected)
        self.assertEqual(payload["local_operations"], ["summarize_project_keys"])
        for name in expected | {"summarize_project_keys"}:
            self.assertIn(name, help_result.stdout)
        for operation in OPERATION_SPECS:
            self.assertNotIn(operation.value, payload["operations"])
            # Match the complete command rather than substrings shared with Project names.
            choices = help_result.stdout.split("{", 1)[1].split("}", 1)[0].split(",")
            self.assertNotIn(operation.value, choices)
            rejected = subprocess.run(
                [sys.executable, "scripts/api.py", operation.value, "--request-stdin"],
                cwd=ROOT, env={}, input="{}", capture_output=True, text=True, check=False,
            )
            self.assertEqual(rejected.returncode, 2)
            self.assertEqual(json.loads(rejected.stdout)["error"], "invalid_arguments")

    def test_public_preflight_help_has_no_compatibility_choice(self) -> None:
        result = subprocess.run(
            [sys.executable, "scripts/preflight.py", "--help"], cwd=ROOT, env={},
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertNotIn("--legacy", result.stdout)

    def test_capabilities_do_not_publish_or_consume_a_legacy_host_gate(self) -> None:
        for configured in ("false", "true", "yes"):
            completed = subprocess.run(
                [sys.executable, "scripts/api.py", "capabilities"],
                cwd=ROOT,
                env={"AUDIENCE_SYNC_EFFECTS_ENABLED": configured},
                check=False,
                capture_output=True,
                text=True,
            )
            with self.subTest(configured=configured):
                self.assertEqual(completed.returncode, 0, completed.stderr)
                payload = json.loads(completed.stdout)
                self.assertNotIn("effects_available", payload)
                self.assertNotIn("effects_require_confirmation", payload)

    def test_processing_cli_has_no_extra_host_gate(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "scripts/legacy_api.py",
                "materialize_audience",
                "--request-stdin",
            ],
            cwd=ROOT,
            env={
                "AUDIENCE_PLATFORM_BASE_URL": "http://127.0.0.1:1",
                "AUDIENCE_SYNC_API_KEY": SYNC_API_KEY,
                "AUDIENCE_ALLOW_LOCALHOST_HTTP_FOR_TESTS": "1",
            },
            check=False,
            capture_output=True,
            text=True,
            input=json.dumps(
                {
                    "path": {"audience_id": AUDIENCE_ID},
                    "body": {
                        "product_scope": "vicohome",
                        "expected_audience_filter_hash": "a" * 64,
                        "idempotency_key": "materialize-1",
                    },
                }
            ),
        )
        self.assertEqual(completed.returncode, 1, completed.stderr)
        self.assertEqual(json.loads(completed.stdout), {"error": "transport_error", "ok": False})
        self.assertNotIn(SYNC_API_KEY, completed.stdout + completed.stderr)

    def test_copied_skill_runs_from_an_unrelated_working_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            installed = temporary / "skills" / "audience-sync"
            shutil.copytree(
                ROOT,
                installed,
                ignore=shutil.ignore_patterns(
                    ".git", ".pytest_cache", ".ruff_cache", "__pycache__"
                ),
            )
            completed = subprocess.run(
                [sys.executable, str(installed / "scripts/api.py"), "capabilities"],
                cwd=temporary,
                env={},
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["capability_source"], "local_adapter")
        self.assertEqual(len(payload["operations"]), 14)
        self.assertIn("get_query_capabilities", payload["operations"])

    def test_cli_accepts_request_values_only_from_stdin_not_argv(self) -> None:
        rejected = subprocess.run(
            [
                sys.executable,
                "scripts/api.py",
                "search_audiences",
                "--query",
                "product_scope=vicohome",
            ],
            cwd=ROOT,
            env={},
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertNotIn("vicohome", rejected.stdout + rejected.stderr)

    def test_cli_accepts_path_query_and_body_in_one_stdin_request(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "scripts/legacy_api.py",
                "materialize_audience",
                "--request-stdin",
            ],
            cwd=ROOT,
            env={
                "AUDIENCE_PLATFORM_BASE_URL": "http://127.0.0.1:1",
                "AUDIENCE_SYNC_API_KEY": SYNC_API_KEY,
                "AUDIENCE_ALLOW_LOCALHOST_HTTP_FOR_TESTS": "1",
            },
            check=False,
            capture_output=True,
            text=True,
            input=json.dumps(
                {
                    "path": {"audience_id": AUDIENCE_ID},
                    "query": {},
                    "body": {
                        "product_scope": "vicohome",
                        "expected_audience_filter_hash": "a" * 64,
                        "idempotency_key": "materialize-stdin-1",
                    },
                }
            ),
        )
        self.assertEqual(completed.returncode, 1, completed.stderr)
        self.assertEqual(json.loads(completed.stdout), {"error": "transport_error", "ok": False})
        self.assertNotIn(SYNC_API_KEY, completed.stdout + completed.stderr)

    def test_cli_rejects_unbounded_or_unexpected_stdin_requests(self) -> None:
        cases = (
            (json.dumps({"url": "https://untrusted.invalid"}), "invalid_arguments", 2),
            (json.dumps({"method": "GET"}), "invalid_arguments", 2),
            (json.dumps({"headers": {"Authorization": "Bearer injected"}}), "invalid_arguments", 2),
            (json.dumps({"credential": SYNC_API_KEY}), "invalid_arguments", 2),
            (
                json.dumps({"body": {"product_scope": "vicohome", "url": "https://evil.invalid"}}),
                "invalid_body_parameters",
                1,
            ),
            (json.dumps({"path": []}), "invalid_arguments", 2),
            ("{", "invalid_arguments", 2),
            ("x" * (32 * 1024 + 1), "request_too_large", 1),
        )
        for request, error, returncode in cases:
            completed = subprocess.run(
                [
                    sys.executable,
                    "scripts/legacy_api.py",
                    "search_audiences",
                    "--request-stdin",
                ],
                cwd=ROOT,
                env={
                    "AUDIENCE_ALLOW_LOCALHOST_HTTP_FOR_TESTS": "1",
                    "AUDIENCE_PLATFORM_BASE_URL": "http://127.0.0.1:1",
                    "AUDIENCE_SYNC_API_KEY": SYNC_API_KEY,
                },
                check=False,
                capture_output=True,
                text=True,
                input=request,
            )
            with self.subTest(error=error, size=len(request)):
                self.assertEqual(completed.returncode, returncode, completed.stderr)
                self.assertEqual(json.loads(completed.stdout), {"error": error, "ok": False})
                self.assertNotIn("untrusted.invalid", completed.stdout + completed.stderr)
                self.assertNotIn(SYNC_API_KEY, completed.stdout + completed.stderr)

    def test_explicit_compatibility_preflight_is_offline(self) -> None:
        completed = subprocess.run(
            [sys.executable, "scripts/preflight.py", "--legacy"],
            cwd=ROOT,
            env={
                "AUDIENCE_SYNC_API_KEY": SYNC_API_KEY,
            },
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.splitlines()[0], "preflight_ok:published_operations")
        self.assertEqual(json.loads(completed.stdout.splitlines()[1]), {
            "validation": "offline", "authentication": "not_attempted",
            "credential_family": "awpk_v1", "api_contract_revision": "audience-sync-v2",
        })

    def test_preflight_checks_selected_project_or_legacy_family_offline(self) -> None:
        project_key = SYNC_API_KEY.replace("awpk_v1_", "awpk_v2_")
        project_env = {
            "AUDIENCE_SYNC_API_KEY": project_key, "AUDIENCE_PROJECT_ID": "example-project"
        }
        cases = (
            ([], project_env, "preflight_ok:audience-project-v3", 0),
            (["--project"], project_env, "preflight_ok:audience-project-v3", 0),
            ([], {"AUDIENCE_SYNC_API_KEY": SYNC_API_KEY},
             "preflight_failed:invalid_sync_api_key", 1),
            (["--legacy"], {"AUDIENCE_SYNC_API_KEY": SYNC_API_KEY},
             "preflight_ok:published_operations", 0),
            (["--project"], {"AUDIENCE_SYNC_API_KEY": project_key},
             "preflight_failed:project_binding_missing", 1),
            ([], {"AUDIENCE_SYNC_API_KEY": project_key},
             "preflight_ok:audience-project-v3", 0),
            (["--project"], {**project_env, "AUDIENCE_SYNC_API_KEY": SYNC_API_KEY},
             "preflight_failed:invalid_sync_api_key", 1),
            ([], {**project_env, "AUDIENCE_SYNC_CONTRACT_REVISION": "audience-sync-v2"},
             "preflight_failed:contract_revision_mismatch", 1),
            (["--legacy"], project_env, "preflight_failed:contract_revision_mismatch", 1),
        )
        for flags, environment, expected, code in cases:
            with self.subTest(flags=flags, expected=expected):
                completed = subprocess.run(
                    [sys.executable, "scripts/preflight.py", *flags], cwd=ROOT,
                    env={**environment, "AUDIENCE_PLATFORM_BASE_URL": "https://offline.invalid"},
                    check=False, capture_output=True, text=True,
                )
                self.assertEqual(completed.returncode, code, completed.stderr)
                self.assertEqual(completed.stdout.splitlines()[0], expected)
                if code == 0:
                    is_project = expected == "preflight_ok:audience-project-v3"
                    self.assertEqual(json.loads(completed.stdout.splitlines()[1]), {
                        "validation": "offline",
                        "authentication": "not_attempted",
                        "credential_family": "awpk_v2" if is_project else "awpk_v1",
                        "api_contract_revision": (
                            "audience-project-v3" if is_project else "audience-sync-v2"
                        ),
                    })
                self.assertEqual(completed.stderr, "")
                self.assertNotIn(project_key, completed.stdout)

    def test_key_only_personal_preflight_does_not_require_project_or_network(self) -> None:
        completed = subprocess.run(
            [sys.executable, "scripts/preflight.py", "--personal-key"],
            cwd=ROOT,
            env={"AUDIENCE_SYNC_API_KEY": SYNC_API_KEY.replace("awpk_v1_", "awpk_v2_"),
                 "AUDIENCE_PLATFORM_BASE_URL": "https://offline.invalid"},
            check=False, capture_output=True, text=True,
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout.splitlines()[0], "preflight_ok:audience-project-v3")
        self.assertEqual(json.loads(completed.stdout.splitlines()[1]), {
            "validation": "offline", "authentication": "not_attempted",
            "credential_family": "awpk_v2", "api_contract_revision": "audience-project-v3",
        })
        self.assertEqual(completed.stderr, "")

    def test_preflight_rejects_wrong_revision(self) -> None:
        completed = subprocess.run(
            [sys.executable, "scripts/preflight.py"],
            cwd=ROOT,
            env={
                "AUDIENCE_SYNC_API_KEY": SYNC_API_KEY,
                "AUDIENCE_SYNC_CONTRACT_REVISION": "wrong",
            },
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(completed.stdout.strip(), "preflight_failed:contract_revision_mismatch")

    def test_dedicated_cli_does_not_accept_broad_audience_api_key(self) -> None:
        completed = subprocess.run(
            [sys.executable, "scripts/preflight.py"],
            cwd=ROOT,
            env={
                "AUDIENCE_API_KEY": "broad-key-must-not-be-used",
            },
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(completed.stdout.strip(), "preflight_failed:sync_api_key_missing")
        self.assertNotIn("broad-key-must-not-be-used", completed.stdout + completed.stderr)

    def test_clone_scripts_use_only_harness_process_configuration(self) -> None:
        self.assertFalse((ROOT / "src/audience_sync/environment.py").exists())
        for relative in ("scripts/api.py", "scripts/preflight.py", "src/audience_sync/cli.py"):
            content = (ROOT / relative).read_text(encoding="utf-8")
            self.assertNotIn("dotenv", content.lower())


if __name__ == "__main__":
    unittest.main()
