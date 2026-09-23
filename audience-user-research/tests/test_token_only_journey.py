from __future__ import annotations

import importlib.util
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((ROOT / "tests/fixtures/coldstart_journey_cases.json").read_text())
TOKEN = "awpk_v2_" + "a" * 26 + "_" + "B" * 43
PROJECT = "kiwibit"
BINDING = "pbr_" + "a" * 64
IDEA = "idea_" + "c" * 26
RESEARCH = "research_" + "d" * 26
FORM = "Form123"
FINGERPRINT = "e" * 64
SELECTION = "rsel_fixture"
RUN_ID = "run_fixture_001"
MATERIALIZE_REQUEST = "request_materialize_001"
SYNC_REQUEST = "request_sync_001"
VOC_RUN = "prun_" + "v" * 24
VOC_HASH = "9" * 64
VOC_MINIMAL_INPUT = "Understand recent activation blockers"
VOC_CONFIGURATION = {
    "decision_goal": "Choose the next activation improvement",
    "assumptions": ["Recent public discussions may reveal user language"],
    "plan": None,
    "voc_brief": {
        "schema_version": 1,
        "question": "What blocks users from reaching initial value?",
        "markets": [{"country": "US", "language": "en"}],
        "source_strategies": ["community_discussions"],
        "keywords": ["activation", "onboarding"],
        "time_range": {"from": "2026-01-01", "to": "2026-09-15"},
        "collection_bound": "focused",
    },
}


class _Platform:
    def __init__(self, scenario: str) -> None:
        self.scenario = scenario
        self.operations: list[str] = []
        self.bodies: list[dict[str, Any]] = []
        self.form_posts = 0
        self.materialize_reads = 0
        self.sync_reads = 0
        self.campaign_created = False
        self.voc_configured = False
        self.voc_configuration: dict[str, Any] | None = None
        self.voc_minimal_input: str | None = None
        self.voc_reads = 0
        self.voc_read_run_ids: list[str] = []
        self.voc_starts = 0
        self.request_kind: str | None = None

    @staticmethod
    def binding(*, campaign: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {
            "research_id": RESEARCH,
            "project_id": PROJECT,
            "revision": 1,
            "binding_revision": BINDING,
            "idea_id": IDEA,
            "form_id": FORM,
            "form_url": "https://form.typeform.com/to/" + FORM,
            "definition_fingerprint": FINGERPRINT,
            "source_materialization_run_id": RUN_ID if campaign else None,
        }
        if campaign:
            result.update(
                campaign_id="campaign-001",
                campaign_url="https://app.brevo.com/campaign/001",
                list_id="20",
            )
        return result

    def status(self) -> dict[str, Any]:
        status_binding = self.binding(campaign=self.campaign_created)
        status_binding.pop("binding_revision")
        bindings = [status_binding]
        if self.request_kind == "materialize":
            self.materialize_reads += 1
            succeeded = self.materialize_reads > 1
            request_id = MATERIALIZE_REQUEST
        elif self.request_kind == "sync_brevo":
            self.sync_reads += 1
            succeeded = self.sync_reads > 1
            request_id = SYNC_REQUEST
        else:
            return self._status(bindings, [], [])
        state = "succeeded" if succeeded else "running"
        request = {
            "request_id": request_id,
            "operation": self.request_kind,
            "revision": 1,
            "state": state,
            "expected_count": 1,
            "source_materialization_run_id": (
                RUN_ID if self.request_kind == "sync_brevo" else None
            ),
            "error_code": None,
        }
        receipts: list[dict[str, Any]] = []
        if succeeded:
            receipt = {
                **request,
                "data_run_id": RUN_ID,
                "eligible_count": (
                    6
                    if self.scenario == "sampled_selection"
                    else 3
                    if self.scenario == "small_sampled_selection"
                    else 1
                ),
                "selected_count": 1,
                "skipped_count": 0,
                "form_id": FORM,
                "list_id": "20" if self.request_kind == "sync_brevo" else None,
            }
            if (
                self.scenario == "materialize_binding_mismatch"
                and self.request_kind == "materialize"
            ):
                receipt["selected_count"] = 2
            if self.scenario == "sync_binding_mismatch" and self.request_kind == "sync_brevo":
                receipt["source_materialization_run_id"] = "wrong_run"
            receipts.append(receipt)
        return self._status(bindings, [request], receipts)

    @staticmethod
    def _status(
        bindings: list[dict[str, Any]],
        requests: list[dict[str, Any]],
        receipts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "project_id": PROJECT,
            "binding_revision": BINDING,
            "enabled": True,
            "research_id": RESEARCH,
            "revision": 1,
            "requests": requests,
            "receipts": receipts,
            "bindings": bindings,
        }


class _Handler(BaseHTTPRequestHandler):
    server: _Server

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def _authorize(self) -> None:
        if self.headers.get("Authorization") != "Bearer " + TOKEN:
            raise AssertionError("restricted token was not used")

    def _body(self) -> dict[str, Any]:
        self._authorize()
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        value = json.loads(raw)
        self.server.platform.bodies.append(value)
        return value

    def _write(self, value: dict[str, Any], status: int = 200) -> None:
        encoded = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:  # noqa: N802
        self._authorize()
        platform = self.server.platform
        parsed = urlsplit(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        if path == "/api/platform/v3/personal-key":
            platform.operations.append("self_context")
            self._write(
                {
                    "project_id": PROJECT,
                    "binding_revision": BINDING,
                    "credential_profile": "user_research",
                    "allowed_actions": sorted(
                        [
                            "idea.read",
                            "idea.write",
                            "voc.collect",
                            "voc.results.read",
                            "research.prepare",
                            "research.materialize",
                            "research.results.read",
                            "forms.create",
                            "forms.read",
                            "audience_sync.capabilities.read",
                            "audience_sync.request",
                            "audience_sync.read",
                        ]
                    ),
                }
            )
            return
        if path.endswith("/actions/voc/capabilities"):
            platform.operations.append("voc_capabilities")
            capability = {
                "markets": [{"country": "US", "language": "en"}],
                "source_strategies": ["community_discussions"],
                "collection_bound": "focused",
            }
            self._write(
                {
                    "project_id": PROJECT,
                    "binding_revision": BINDING,
                    "configurations": [capability],
                    "default": capability,
                }
            )
            return
        if path.endswith("/actions/voc/configuration"):
            platform.operations.append("get_voc_configuration")
            resource = {
                "idea_id": IDEA,
                "kind": "voc",
                "configuration_revision": 1 if platform.voc_configured else 0,
                "content_hash": VOC_HASH if platform.voc_configured else None,
                "minimal_input": platform.voc_minimal_input,
                "configuration": platform.voc_configuration,
            }
            self._write(
                {
                    "project_id": PROJECT,
                    "binding_revision": BINDING,
                    "resource": resource,
                }
            )
            return
        if "/actions/voc/executions/" in path:
            platform.operations.append("get_voc_execution")
            platform.voc_reads += 1
            requested_run_id = path.rsplit("/", 1)[-1]
            platform.voc_read_run_ids.append(requested_run_id)
            if requested_run_id != VOC_RUN:
                self.send_error(404)
                return
            complete_after = 9 if platform.scenario == "slow_voc" else 2
            status = "succeeded" if platform.voc_reads >= complete_after else "running"
            idea_id = "idea_" + "x" * 26 if platform.scenario == "voc_binding_mismatch" else IDEA
            self._write(
                {
                    "project_id": PROJECT,
                    "binding_revision": BINDING,
                    "idea_id": idea_id,
                    "platform_run_id": VOC_RUN,
                    "result": {
                        "schema_version": 3,
                        "status": status,
                        "updated_at": "2026-09-15T00:00:00Z",
                    },
                }
            )
            return
        if path.endswith("/research/readiness"):
            platform.operations.append("research_readiness")
            self._write(
                {
                    "project_id": PROJECT,
                    "binding_revision": BINDING,
                    "mapping": "configured",
                    "warehouse": "available",
                    "error_code": None,
                }
            )
            return
        if path.endswith("/research/query-capabilities"):
            platform.operations.append("research_capabilities")
            self._write(
                {
                    "project_id": PROJECT,
                    "binding_revision": BINDING,
                    "enabled": True,
                    "resource": {
                        "schema_version": "audience-query-capability-v1",
                        "registry_version": "fixture-v1",
                        "registry_digest": "f" * 64,
                        "structured_criteria_available": True,
                        "preview_available": True,
                        "materialization_available": True,
                        "brevo_sync_available": True,
                        "relations": [
                            {
                                "relation_id": "profile_snapshot",
                                "datahub_urn": (
                                    "urn:li:dataset:(urn:li:dataPlatform:athena,"
                                    "profile_snapshot,PROD)"
                                ),
                                "fields": [
                                    {
                                        "field_id": "country",
                                        "value_type": "string",
                                        "operators": ["eq"],
                                    }
                                ],
                            }
                        ],
                        "edges": [],
                    },
                }
            )
            return
        if path.endswith("/journey/links"):
            platform.operations.append("links")
            batch = query["batch"][0]
            self._write(
                {
                    "project_id": PROJECT,
                    "binding_revision": BINDING,
                    "total": 1,
                    "watermark": "fixture",
                    "items": [
                        {
                            "uid": "0" * 32,
                            "url": (
                                "https://form.typeform.com/to/"
                                + FORM
                                + "#uid="
                                + "0" * 32
                                + "&research_id="
                                + RESEARCH
                                + "&batch="
                                + batch
                            ),
                        }
                    ],
                }
            )
            return
        if path.endswith("/journey"):
            platform.operations.append("journey_status")
            self._write(platform.status())
            return
        self.send_error(404)

    def do_PUT(self) -> None:  # noqa: N802
        platform = self.server.platform
        path = urlsplit(self.path).path
        body = self._body()
        if path.endswith("/actions/voc/configuration"):
            platform.operations.append("put_voc_configuration")
            if (
                body.get("expected_configuration_revision") != 0
                or body.get("expected_content_hash") is not None
                or body.get("minimal_input") != VOC_MINIMAL_INPUT
                or body.get("configuration") != VOC_CONFIGURATION
            ):
                self.send_error(422)
                return
            platform.voc_configured = True
            platform.voc_minimal_input = body["minimal_input"]
            platform.voc_configuration = json.loads(json.dumps(body["configuration"]))
            time_range = platform.voc_configuration["voc_brief"]["time_range"]
            time_range["from_date"] = time_range.pop("from")
            time_range["to_date"] = time_range.pop("to")
            response_configuration = json.loads(json.dumps(body["configuration"]))
            if platform.scenario == "voc_put_readback_mismatch":
                response_configuration["decision_goal"] = "A different decision"
            self._write(
                {
                    "project_id": PROJECT,
                    "binding_revision": BINDING,
                    "resource": {
                        "idea_id": IDEA,
                        "kind": "voc",
                        "configuration_revision": 1,
                        "content_hash": VOC_HASH,
                        "minimal_input": body["minimal_input"],
                        "configuration": response_configuration,
                    },
                }
            )
            return
        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        platform = self.server.platform
        path = urlsplit(self.path).path
        body = self._body()
        if path.endswith("/ideas"):
            platform.operations.append("create_idea")
            self._write(
                {
                    "project_id": PROJECT,
                    "binding_revision": BINDING,
                    "resource": {"idea_id": IDEA, "title": body["title"]},
                }
            )
            return
        if path.endswith("/actions/voc/executions"):
            platform.operations.append("start_voc_execution")
            platform.voc_starts += 1
            if (
                body.get("expected_configuration_revision") != 1
                or body.get("expected_content_hash") != VOC_HASH
                or body.get("idempotency_key") != "voc-coldstart-0001"
            ):
                self.send_error(409)
                return
            self._write(
                {
                    "project_id": PROJECT,
                    "binding_revision": BINDING,
                    "idea_id": IDEA,
                    "execution": {
                        "platform_run_id": VOC_RUN,
                        "status": "running" if platform.voc_starts > 1 else "queued",
                        "idempotent": platform.voc_starts > 1,
                    },
                },
                status=202,
            )
            return
        if path.endswith("/research"):
            platform.operations.append("create_research")
            self._write(
                {
                    "project_id": PROJECT,
                    "binding_revision": BINDING,
                    "research_id": RESEARCH,
                    "idea_id": IDEA,
                    "revision": 1,
                }
            )
            return
        if path.endswith("/journey/form"):
            platform.operations.append("create_form")
            platform.form_posts += 1
            if platform.scenario == "ambiguous_form":
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            self._write(_Platform.binding())
            return
        if path.endswith("/selections"):
            platform.operations.append("prepare_selection")
            where = body.get("criteria", {}).get("where", {})
            if where.get("relation_id") != "profile_snapshot":
                self.send_error(422)
                return
            count = (
                6
                if platform.scenario == "selection_count_mismatch"
                else 6
                if platform.scenario == "sampled_selection"
                else 3
                if platform.scenario == "small_sampled_selection"
                else 1
            )
            self._write(
                {
                    "project_id": PROJECT,
                    "binding_revision": BINDING,
                    "research_id": RESEARCH,
                    "approved_selection_id": SELECTION,
                    "expected_count": count,
                    "partition_dt": "2026-09-15",
                    "criteria_hash": "1" * 64,
                    "sql_hash": "2" * 64,
                    "registry_version": "fixture-v1",
                },
                status=201,
            )
            return
        if path.endswith("/journey/operations"):
            operation = body["operation"]
            platform.operations.append(operation)
            platform.request_kind = operation
            request_id = MATERIALIZE_REQUEST if operation == "materialize" else SYNC_REQUEST
            self._write(
                {
                    "project_id": PROJECT,
                    "binding_revision": BINDING,
                    "request_id": request_id,
                    "operation": operation,
                    "revision": 1,
                    "state": "queued",
                    "expected_count": body.get("expected_count"),
                    "source_materialization_run_id": (
                        RUN_ID
                        if platform.scenario == "materialize_replay_source"
                        and operation == "materialize"
                        else "wrong_run"
                        if platform.scenario == "materialize_replay_wrong_source"
                        and operation == "materialize"
                        else 7
                        if platform.scenario == "materialize_replay_invalid_source"
                        and operation == "materialize"
                        else body.get("source_materialization_run_id")
                    ),
                    "error_code": None,
                }
            )
            return
        if path.endswith("/journey/campaign-draft"):
            platform.operations.append("campaign_draft")
            if body.get("source_materialization_run_id") != RUN_ID:
                self.send_error(422)
                return
            if "{{ contact.SURVEY_URL }}" not in body.get("body", {}).get("htmlContent", ""):
                self.send_error(422)
                return
            platform.campaign_created = True
            self._write(_Platform.binding(campaign=True))
            return
        self.send_error(404)


class _Server(ThreadingHTTPServer):
    def __init__(self, platform: _Platform) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.platform = platform

    def handle_error(self, _request: Any, _client_address: Any) -> None:
        return


class TokenOnlyJourneyTests(unittest.TestCase):
    def test_publishing_scaffold_is_deterministic_and_covers_required_evals(self) -> None:
        owner = json.loads((ROOT / "contracts/semantic-owner.json").read_text())
        self.assertEqual(owner["repository"], "lli/user-research-skill")
        evaluations = json.loads((ROOT / "evals/evals.json").read_text())
        self.assertEqual(evaluations["contract"]["case_count"], 19)
        self.assertEqual(
            {case["slug"] for case in evaluations["evals"]},
            {
                "coldstart-full-authorized-journey",
                "voc-skipped-full-research",
                "questionnaire-only-no-warehouse",
                "resume-and-ambiguous-recovery",
                "response-profile-csv",
                "native-voc-dataset-report-loop",
                "historical-project-inventory",
                "monitor-existing-research",
                "historical-voc-local-dataset-analysis",
                "historical-research-results-local-csv",
                "permission-and-partial-history",
                "voc-focus-and-channel-choice",
                "questionnaire-and-campaign-copy-brief",
                "uj-nl-research-voc-dataset-report",
                "uj-questionnaire-without-warehouse",
                "uj-small-cohort-invite-draft",
                "uj-resume-history-results-matrix",
                "uj-recoverable-exceptions",
                "project-shared-research-reuse",
            },
        )
        for script in ("update_operation_registry.py", "update_source_lock.py"):
            completed = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / script), "--check"],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def _invoke_journey(
        self,
        server: _Server,
        installed: Path,
        temporary: Path,
        mode: str,
        *,
        poll_attempts: str | None = "3",
        sample_size: int | None = None,
    ) -> subprocess.CompletedProcess[str]:
        environment = {
            "AUDIENCE_ALLOW_LOCALHOST_HTTP_FOR_TESTS": "1",
            "AUDIENCE_PLATFORM_BASE_URL": f"http://127.0.0.1:{server.server_port}",
            "AUDIENCE_API_KEY": TOKEN,
            "USER_RESEARCH_TDD_POLL_INTERVAL_SECONDS": "0",
        }
        if poll_attempts is not None:
            environment["USER_RESEARCH_TDD_POLL_ATTEMPTS"] = poll_attempts
        command = [sys.executable, str(installed / "scripts/tdd_user_research_journey.py"), mode]
        if sample_size is not None:
            command.extend(("--sample-size", str(sample_size)))
        completed = subprocess.run(
            command,
            cwd=temporary,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        output = completed.stdout + completed.stderr
        self.assertNotIn(TOKEN, output)
        for marker in FIXTURE["forbidden_output_markers"]:
            self.assertNotIn(marker, output)
        return completed

    def _run(
        self,
        scenario: str,
        mode: str = "full",
        *,
        poll_attempts: str | None = "3",
        sample_size: int | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], _Platform]:
        platform = _Platform(scenario)
        server = _Server(platform)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                temporary = Path(directory)
                installed = temporary / "skills" / "user-research"
                shutil.copytree(
                    ROOT,
                    installed,
                    ignore=shutil.ignore_patterns(
                        ".git", ".pytest_cache", ".ruff_cache", "__pycache__", "build", "dist"
                    ),
                )
                completed = self._invoke_journey(
                    server,
                    installed,
                    temporary,
                    mode,
                    poll_attempts=poll_attempts,
                    sample_size=sample_size,
                )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        return completed, platform

    def _run_sequence(
        self,
        platform: _Platform,
        poll_attempts: tuple[str, ...],
        *,
        mutate_after_first: Any = None,
    ) -> list[subprocess.CompletedProcess[str]]:
        server = _Server(platform)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        completed: list[subprocess.CompletedProcess[str]] = []
        try:
            with tempfile.TemporaryDirectory() as directory:
                temporary = Path(directory)
                installed = temporary / "skills" / "user-research"
                shutil.copytree(
                    ROOT,
                    installed,
                    ignore=shutil.ignore_patterns(
                        ".git", ".pytest_cache", ".ruff_cache", "__pycache__", "build", "dist"
                    ),
                )
                for index, attempts in enumerate(poll_attempts):
                    completed.append(
                        self._invoke_journey(
                            server,
                            installed,
                            temporary,
                            "full",
                            poll_attempts=attempts,
                        )
                    )
                    if index == 0 and mutate_after_first is not None:
                        mutate_after_first(platform)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        return completed

    def test_full_cold_start_uses_server_discovery_and_exact_readback(self) -> None:
        completed, platform = self._run("happy")
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertTrue(payload["result"]["campaign_draft_created"])
        self.assertFalse(payload["result"]["campaign_sent"])
        self.assertEqual(payload["result"]["materialization_member_count"], 1)
        self.assertEqual(platform.operations, FIXTURE["happy_operations"])
        operations = [body for body in platform.bodies if "operation" in body]
        self.assertEqual(operations[0]["approved_selection_id"], SELECTION)
        self.assertNotIn("sample_size", operations[0])
        self.assertEqual(operations[1]["source_materialization_run_id"], RUN_ID)
        self.assertEqual(operations[1]["expected_count"], 1)
        selection = next(body for body in platform.bodies if "criteria" in body)
        self.assertEqual(selection["criteria"]["where"]["relation_id"], "profile_snapshot")
        campaign = next(body for body in platform.bodies if "htmlContent" in body.get("body", {}))
        self.assertNotIn("destination_id", operations[1])
        self.assertNotIn("destination_id", campaign)
        self.assertEqual(campaign["source_materialization_run_id"], RUN_ID)
        self.assertNotIn("sender", campaign["body"])
        self.assertEqual(json.loads(completed.stdout)["result"]["voc_status"], "succeeded")
        self.assertEqual(json.loads(completed.stdout)["result"]["voc_platform_run_id"], VOC_RUN)
        voc_write = next(body for body in platform.bodies if "configuration" in body)
        self.assertEqual(voc_write["expected_configuration_revision"], 0)
        self.assertEqual(voc_write["configuration"]["voc_brief"]["collection_bound"], "focused")

    def test_materialize_sample_uses_sample_count_for_downstream_binding(self) -> None:
        completed, platform = self._run("sampled_selection", mode="full-no-voc", sample_size=1)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        result = json.loads(completed.stdout)["result"]
        self.assertEqual(result["materialization_member_count"], 1)
        self.assertEqual(result["links_count"], 1)
        operations = [body for body in platform.bodies if "operation" in body]
        self.assertEqual(operations[0]["sample_size"], 1)
        self.assertEqual(operations[0]["expected_count"], 1)
        self.assertEqual(operations[1]["expected_count"], 1)
        self.assertTrue(platform.campaign_created)

    def test_sample_larger_than_approved_count_stops_before_materialization(self) -> None:
        completed, platform = self._run(
            "small_sampled_selection", mode="full-no-voc", sample_size=4
        )
        self.assertEqual(completed.returncode, 1, completed.stdout + completed.stderr)
        self.assertEqual(
            json.loads(completed.stdout),
            {"error": "sample_size_exceeds_selection", "ok": False},
        )
        self.assertNotIn("materialize", platform.operations)

    def test_tdd_driver_rejects_sample_above_five_before_provider_calls(self) -> None:
        completed, platform = self._run("sampled_selection", mode="full-no-voc", sample_size=6)
        self.assertEqual(completed.returncode, 1, completed.stdout + completed.stderr)
        self.assertEqual(
            json.loads(completed.stdout), {"error": "invalid_sample_size", "ok": False}
        )
        self.assertEqual(platform.operations, [])

    def test_materialize_exact_replay_may_return_the_created_run_id(self) -> None:
        completed, platform = self._run("materialize_replay_source")

        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertTrue(json.loads(completed.stdout)["result"]["campaign_draft_created"])
        self.assertTrue(platform.campaign_created)

    def test_materialize_exact_replay_rejects_a_different_run_id(self) -> None:
        completed, platform = self._run("materialize_replay_wrong_source")

        self.assertEqual(completed.returncode, 1, completed.stdout + completed.stderr)
        self.assertEqual(
            json.loads(completed.stdout), {"error": "request_binding_mismatch", "ok": False}
        )
        self.assertFalse(platform.campaign_created)

    def test_materialize_exact_replay_rejects_an_invalid_run_id_type(self) -> None:
        completed, platform = self._run("materialize_replay_invalid_source")

        self.assertEqual(completed.returncode, 1, completed.stdout + completed.stderr)
        self.assertEqual(
            json.loads(completed.stdout), {"error": "invalid_response_schema", "ok": False}
        )
        self.assertFalse(platform.campaign_created)

    def test_default_poll_window_handles_voc_longer_than_eight_reads(self) -> None:
        completed, platform = self._run("slow_voc", poll_attempts=None)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertEqual(platform.voc_reads, 9)

    def test_same_token_resumes_exact_voc_configuration_and_execution(self) -> None:
        platform = _Platform("happy")
        first, resumed = self._run_sequence(platform, ("1", "3"))

        self.assertEqual(first.returncode, 1, first.stdout + first.stderr)
        self.assertEqual(json.loads(first.stdout), {"error": "voc_execution_timeout", "ok": False})
        self.assertEqual(resumed.returncode, 0, resumed.stdout + resumed.stderr)
        self.assertEqual(json.loads(resumed.stdout)["result"]["voc_platform_run_id"], VOC_RUN)
        self.assertEqual(set(platform.voc_read_run_ids), {VOC_RUN})
        self.assertEqual(platform.operations.count("put_voc_configuration"), 1)
        self.assertEqual(platform.operations.count("start_voc_execution"), 2)
        starts = [body for body in platform.bodies if "expected_configuration_revision" in body]
        execution_starts = [body for body in starts if "idempotency_key" in body]
        self.assertEqual(
            [body["idempotency_key"] for body in execution_starts],
            ["voc-coldstart-0001", "voc-coldstart-0001"],
        )

    def test_resume_rejects_changed_voc_configuration_before_execution(self) -> None:
        def change_configuration(platform: _Platform) -> None:
            assert platform.voc_configuration is not None
            platform.voc_configuration["decision_goal"] = "A different decision"

        platform = _Platform("happy")
        first, rejected = self._run_sequence(
            platform,
            ("1", "3"),
            mutate_after_first=change_configuration,
        )

        self.assertEqual(first.returncode, 1, first.stdout + first.stderr)
        self.assertEqual(rejected.returncode, 1, rejected.stdout + rejected.stderr)
        self.assertEqual(
            json.loads(rejected.stdout),
            {"error": "voc_configuration_requires_review", "ok": False},
        )
        self.assertEqual(platform.operations.count("put_voc_configuration"), 1)
        self.assertEqual(platform.operations.count("start_voc_execution"), 1)

    def test_voc_configuration_put_requires_exact_readback_before_execution(self) -> None:
        completed, platform = self._run("voc_put_readback_mismatch")

        self.assertEqual(completed.returncode, 1, completed.stdout + completed.stderr)
        self.assertEqual(
            json.loads(completed.stdout),
            {"error": "voc_configuration_binding_mismatch", "ok": False},
        )
        self.assertEqual(platform.operations.count("put_voc_configuration"), 1)
        self.assertNotIn("start_voc_execution", platform.operations)

    def test_questionnaire_only_stops_before_warehouse_and_brevo(self) -> None:
        completed, platform = self._run("happy", "questionnaire-only")
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertEqual(
            platform.operations,
            [
                "self_context",
                "self_context",
                "research_readiness",
                "self_context",
                "create_idea",
                "self_context",
                "create_research",
                "self_context",
                "create_form",
            ],
        )
        self.assertTrue(json.loads(completed.stdout)["result"]["questionnaire_created"])

    def test_optional_voc_can_be_skipped_without_legacy_scope_or_provider_calls(self) -> None:
        completed, platform = self._run("happy", "full-no-voc")
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        result = json.loads(completed.stdout)["result"]
        self.assertEqual(result["voc_status"], "skipped_optional")
        self.assertIsNone(result["voc_platform_run_id"])
        self.assertFalse(any("voc" in operation for operation in platform.operations))
        selection = next(body for body in platform.bodies if "criteria" in body)
        self.assertEqual(selection["criteria"]["where"]["relation_id"], "profile_snapshot")

    def test_ambiguous_form_is_never_replayed_and_stops_downstream(self) -> None:
        completed, platform = self._run("ambiguous_form")
        self.assertEqual(completed.returncode, 1, completed.stdout + completed.stderr)
        self.assertEqual(json.loads(completed.stdout), {"error": "transport_error", "ok": False})
        self.assertEqual(platform.form_posts, 1)
        self.assertNotIn("prepare_selection", platform.operations)

    def test_binding_count_and_destination_failures_stop_downstream(self) -> None:
        for scenario, error in FIXTURE["terminal_errors"].items():
            completed, platform = self._run(scenario)
            with self.subTest(scenario=scenario):
                self.assertEqual(completed.returncode, 1, completed.stdout + completed.stderr)
                self.assertEqual(json.loads(completed.stdout), {"error": error, "ok": False})
                if scenario == "voc_binding_mismatch":
                    self.assertNotIn("create_research", platform.operations)
                if scenario == "selection_count_mismatch":
                    self.assertNotIn("materialize", platform.operations)
                if scenario == "materialize_binding_mismatch":
                    self.assertNotIn("sync_brevo", platform.operations)
                if scenario == "sync_binding_mismatch":
                    self.assertNotIn("campaign_draft", platform.operations)

    def test_capabilities_timeout_is_code_only(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "cold_start_runner", ROOT / "scripts/tdd_user_research_journey.py"
        )
        if spec is None or spec.loader is None:
            raise AssertionError("runner module unavailable")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with (
            patch.object(
                module.subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired(["api.py"], timeout=20),
            ),
            self.assertRaisesRegex(module.JourneyError, "^cli_timeout$"),
        ):
            module._capability_operations()


if __name__ == "__main__":
    unittest.main()
