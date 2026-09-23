from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
SYNC_API_KEY = "awpk_v1_" + "a" * 26 + "_" + "A" * 43
AUDIENCE_ID = "aud_" + "a" * 26
OTHER_AUDIENCE_ID = "aud_" + "b" * 26
MATERIALIZATION_REQUEST_ID = "amrq_" + "c" * 26
SYNC_REQUEST_ID = "asrq_" + "d" * 26
FILTER_HASH = "e" * 64


class _JourneyPlatform:
    def __init__(self, scenario: str) -> None:
        self.scenario = scenario
        self.operations: list[str] = []
        self.envelopes: list[dict[str, Any]] = []
        self.materialization_reads = 0
        self.sync_reads = 0
        self.materialization_posts = 0

    def capabilities(self) -> dict[str, Any]:
        destinations = [
            {
                "destination_id": "brevo_primary",
                "kind": "brevo",
                "label": "Brevo staging",
            }
        ]
        if self.scenario == "multiple_destinations":
            destinations.append(
                {
                    "destination_id": "brevo_secondary",
                    "kind": "brevo",
                    "label": "Brevo secondary",
                }
            )
        return {
            "product_scope": "vicohome",
            "contract_revision": "audience-sync-v2",
            "create_audience_available": True,
            "definition_available": True,
            "materialization_available": True,
            "brevo_configured": True,
            "brevo_effect_ready": True,
            "mailchimp_configured": False,
            "mailchimp_effect_ready": False,
            "exact_materialization_run_required": True,
            "max_members": None,
            "destinations": destinations,
        }

    @staticmethod
    def audience() -> dict[str, Any]:
        return {
            "audience_id": AUDIENCE_ID,
            "name": "audience_sync_tdd_loop1",
            "product_scope": "vicohome",
            "audience_filter": {"tenants": ["vicohome"], "materialization_limit": 1},
            "audience_filter_hash": FILTER_HASH,
            "audience_detail_url": "https://audience.example.test/audiences/one",
        }


class _Handler(BaseHTTPRequestHandler):
    server: _JourneyServer

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        decoded = json.loads(raw)
        self.assert_authorization()
        return decoded

    def assert_authorization(self) -> None:
        if self.headers.get("Authorization") != "Bearer " + SYNC_API_KEY:
            raise AssertionError("restricted token was not used")

    def _write(self, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        self._write_raw(encoded, status=200)

    def _write_raw(self, encoded: bytes, *, status: int) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:  # noqa: N802
        platform = self.server.platform
        self.assert_authorization()
        path = self.path.split("?", 1)[0]
        if path.endswith("/capabilities"):
            platform.operations.append("get_sync_capabilities")
            if platform.scenario == "capabilities_http_failure":
                self._write_raw(
                    json.dumps({"detail": {"code": "platform_unavailable"}}).encode(),
                    status=503,
                )
                return
            if platform.scenario == "capabilities_malformed":
                self._write_raw(b"{", status=200)
                return
            if platform.scenario == "capabilities_timeout":
                time.sleep(0.1)
            self._write(platform.capabilities())
            return
        if "/materializations/" in path:
            platform.operations.append("get_materialization")
            platform.materialization_reads += 1
            status = "queued" if platform.materialization_reads == 1 else "succeeded"
            if platform.scenario == "materialization_failed":
                status = "failed"
            elif platform.scenario == "materialization_timeout":
                status = "queued"
            materialization: dict[str, Any] = {
                "status": status,
                "materialization_request_id": MATERIALIZATION_REQUEST_ID,
            }
            if status == "succeeded":
                materialization.update(
                    {
                        "audience_filter_hash": (
                            "f" * 64
                            if platform.scenario == "materialization_hash_mismatch"
                            else FILTER_HASH
                        ),
                        "materialization_run_id": "run:exact:1",
                        "member_count": 1,
                    }
                )
            self._write(
                {
                    "audience_id": AUDIENCE_ID,
                    "product_scope": "vicohome",
                    "materialization": materialization,
                    "idempotent": True,
                }
            )
            return
        if "/syncs/" in path:
            platform.operations.append("get_audience_sync")
            platform.sync_reads += 1
            status = "running" if platform.sync_reads == 1 else "succeeded"
            audience_id = (
                OTHER_AUDIENCE_ID if platform.scenario == "sync_binding_mismatch" else AUDIENCE_ID
            )
            payload: dict[str, Any] = {
                "sync_request_id": SYNC_REQUEST_ID,
                "status": status,
                "audience_id": audience_id,
                "product_scope": "vicohome",
                "provider_kind": "brevo",
                "destination_id": "brevo_primary",
                "materialization_run_id": "run:exact:1",
            }
            if status == "succeeded":
                payload.update(
                    {
                        "source_count": 1,
                        "resolved_count": 1,
                        "applied_count": 1,
                        "added_count": 1,
                        "removed_count": 0,
                        "skipped_count": 0,
                        "compliance_skipped_count": 0,
                        "provider_resource_url": "https://provider.example.test/resources/one",
                    }
                )
                if platform.scenario == "sync_count_mismatch":
                    payload["source_count"] = 2
            self._write(payload)
            return
        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        platform = self.server.platform
        path = self.path.split("?", 1)[0]
        body = self._body()
        platform.envelopes.append(body)
        if path.endswith("/audiences/search"):
            platform.operations.append("search_audiences")
            self._write({"items": []})
            return
        if path.endswith("/audiences"):
            platform.operations.append("create_audience")
            self._write({"outcome": "created", "audience": platform.audience()})
            return
        if path.endswith("/preview"):
            platform.operations.append("preview_audience")
            self._write(
                {
                    "audience_id": AUDIENCE_ID,
                    "product_scope": "vicohome",
                    "audience_filter_hash": FILTER_HASH,
                    "criteria_summary": "one bounded staging member",
                    "member_count": 1,
                    "partition_dt": "2026-09-03",
                }
            )
            return
        if path.endswith("/materializations"):
            platform.operations.append("materialize_audience")
            platform.materialization_posts += 1
            if (
                platform.scenario == "ambiguous_materialization"
                and platform.materialization_posts == 1
            ):
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            self._write(
                {
                    "audience_id": AUDIENCE_ID,
                    "product_scope": "vicohome",
                    "materialization": {
                        "status": "queued",
                        "materialization_request_id": MATERIALIZATION_REQUEST_ID,
                    },
                    "idempotent": platform.materialization_posts > 1,
                }
            )
            return
        if path.endswith("/syncs"):
            platform.operations.append("sync_audience")
            self._write({"sync_request_id": SYNC_REQUEST_ID, "status": "queued"})
            return
        self.send_error(404)


class _JourneyServer(ThreadingHTTPServer):
    def __init__(self, platform: _JourneyPlatform) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.platform = platform

    def handle_error(self, _request: Any, _client_address: Any) -> None:
        return


class TokenOnlyBootstrapTests(unittest.TestCase):
    def test_copied_skill_preflight_needs_only_restricted_token(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            installed = temporary / "skills" / "audience-sync"
            shutil.copytree(
                ROOT,
                installed,
                ignore=shutil.ignore_patterns(
                    ".git", ".pytest_cache", ".ruff_cache", "__pycache__", "build", "dist"
                ),
            )
            completed = subprocess.run(
                [sys.executable, str(installed / "scripts/preflight.py"), "--legacy"],
                cwd=temporary,
                env={"AUDIENCE_SYNC_API_KEY": SYNC_API_KEY},
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertEqual(completed.stdout.splitlines()[0], "preflight_ok:published_operations")
        self.assertNotIn(SYNC_API_KEY, completed.stdout + completed.stderr)


class JourneyDriverTests(unittest.TestCase):
    def _run(
        self,
        scenario: str,
        *,
        mode: str = "full",
        environment_overrides: Optional[dict[str, str]] = None,
    ) -> tuple[subprocess.CompletedProcess[str], _JourneyPlatform]:
        platform = _JourneyPlatform(scenario)
        server = _JourneyServer(platform)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                temporary = Path(directory)
                installed = temporary / "skills" / "audience-sync"
                shutil.copytree(
                    ROOT,
                    installed,
                    ignore=shutil.ignore_patterns(
                        ".git", ".pytest_cache", ".ruff_cache", "__pycache__", "build", "dist"
                    ),
                )
                environment = {
                    "AUDIENCE_ALLOW_LOCALHOST_HTTP_FOR_TESTS": "1",
                    "AUDIENCE_PLATFORM_BASE_URL": "http://127.0.0.1:" + str(server.server_port),
                    "AUDIENCE_SYNC_API_KEY": SYNC_API_KEY,
                    "AUDIENCE_SYNC_TDD_POLL_ATTEMPTS": "3",
                    "AUDIENCE_SYNC_TDD_POLL_INTERVAL_SECONDS": "0",
                    "AUDIENCE_SYNC_TDD_PRODUCT_SCOPE": "vicohome",
                    "AUDIENCE_SYNC_TDD_PROVIDER_KIND": "brevo",
                    "AUDIENCE_SYNC_TDD_RUN_TOKEN": "loop1",
                }
                environment.update(environment_overrides or {})
                completed = subprocess.run(
                    [sys.executable, str(installed / "scripts/tdd_audience_journey.py"), mode],
                    cwd=temporary,
                    env=environment,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        self.assertNotIn(SYNC_API_KEY, completed.stdout + completed.stderr)
        return completed, platform

    def test_full_journey_uses_only_fixed_cli_with_exact_bindings(self) -> None:
        completed, platform = self._run("happy")

        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["result"]["materialization_status"], "succeeded")
        self.assertEqual(payload["result"]["materialization_member_count"], 1)
        self.assertEqual(payload["result"]["sync_status"], "succeeded")
        self.assertEqual(payload["result"]["sync_applied_count"], 1)
        self.assertEqual(
            platform.operations,
            [
                "get_sync_capabilities",
                "search_audiences",
                "create_audience",
                "preview_audience",
                "materialize_audience",
                "get_materialization",
                "get_materialization",
                "get_sync_capabilities",
                "sync_audience",
                "get_audience_sync",
                "get_audience_sync",
            ],
        )
        materialize = next(
            body for body in platform.envelopes if "expected_audience_filter_hash" in body
        )
        synchronized = next(
            body for body in platform.envelopes if "materialization_run_id" in body
        )
        self.assertEqual(materialize["expected_audience_filter_hash"], FILTER_HASH)
        self.assertEqual(synchronized["materialization_run_id"], "run:exact:1")
        self.assertEqual(synchronized["expected_member_count"], 1)
        self.assertEqual(synchronized["destination_id"], "brevo_primary")

    def test_inspect_mode_runs_only_its_selected_operations(self) -> None:
        completed, platform = self._run("happy", mode="inspect")
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertEqual(platform.operations, ["get_sync_capabilities", "search_audiences"])

    def test_ambiguous_materialization_is_not_replayed_by_the_fixture(self) -> None:
        completed, platform = self._run("ambiguous_materialization")
        self.assertEqual(completed.returncode, 1, completed.stdout + completed.stderr)
        self.assertEqual(json.loads(completed.stdout), {"error": "transport_error", "ok": False})
        self.assertEqual(platform.materialization_posts, 1)

    def test_failed_timeout_binding_and_destination_cases_fail_closed(self) -> None:
        cases = (
            ("materialization_failed", "materialization_failed"),
            ("materialization_timeout", "materialization_outcome_unknown"),
            ("materialization_hash_mismatch", "materialization_binding_mismatch"),
            ("sync_binding_mismatch", "sync_binding_mismatch"),
            ("sync_count_mismatch", "sync_binding_mismatch"),
            ("multiple_destinations", "destination_ambiguous"),
        )
        for scenario, error in cases:
            completed, platform = self._run(scenario)
            with self.subTest(scenario=scenario):
                self.assertEqual(completed.returncode, 1, completed.stdout + completed.stderr)
                self.assertEqual(json.loads(completed.stdout), {"error": error, "ok": False})
                if scenario == "multiple_destinations":
                    self.assertNotIn("materialize_audience", platform.operations)

    def test_token_never_leaks_across_http_timeout_malformed_and_cli_errors(self) -> None:
        cases = (
            ("capabilities_http_failure", {}, "platform_unavailable"),
            ("capabilities_malformed", {}, "invalid_response"),
            (
                "capabilities_timeout",
                {"AUDIENCE_PLATFORM_TIMEOUT_SECONDS": "0.01"},
                "transport_error",
            ),
            (
                "happy",
                {"AUDIENCE_SYNC_API_KEY": "malformed-token"},
                "invalid_sync_api_key",
            ),
        )
        for scenario, overrides, error in cases:
            completed, _platform = self._run(
                scenario,
                mode="inspect",
                environment_overrides=overrides,
            )
            with self.subTest(scenario=scenario):
                self.assertEqual(completed.returncode, 1, completed.stdout + completed.stderr)
                self.assertEqual(json.loads(completed.stdout), {"error": error, "ok": False})


if __name__ == "__main__":
    unittest.main()
