from __future__ import annotations

import importlib.util
import json
import os
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
from urllib.parse import parse_qs, unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "awpk_v2_" + "a" * 26 + "_" + "B" * 43
PROJECT = "kiwibit"
BINDING = "pbr_" + "a" * 64
IDEA = "idea_" + "b" * 26
ACTOR = "oAuCIx3ItNrs2okjQ"
ACTOR_REF = "publisher/amazon-reviews"
REQUEST = "pnvoc_" + "c" * 26
VOC = "ivoc_" + "d" * 26
RUN = "provider-run-001"
DATASET = "dataset-001"
SOURCE_REVISION = "native-source-001"
SOURCE_FINGERPRINT = "e" * 64
REPORT_REVISION = "arr_" + "f" * 26
EVIDENCE = "ev_" + "1" * 64
REPORT = f"# Findings\n\nObserved activation friction [{EVIDENCE}].\n\nSampled evidence only."
RAW_REPORT = "# Findings\n\nThe sampled raw reviewText reports activation confusion.\n"
DATASET_BYTES = b'{"reviewText":"Activation was confusing"}\n'
DEFAULT_VOICES = [
    {
        "evidence_id": EVIDENCE,
        "text": "Activation was confusing",
        "theme": "activation friction",
        "translation": {"locale": "zh-CN", "text": "激活过程让人困惑"},
    }
]


class _Platform:
    def __init__(self, scenario: str) -> None:
        self.scenario = scenario
        self.operations: list[str] = []
        self.bodies: list[dict[str, Any]] = []
        self.run_reads = 0
        self.start_requests = 0
        self.provider_starts = 0
        self.exports = 0
        self.source_reads = 0
        self.report_content = REPORT
        self.claimed = False
        self.published = False
        self.voices: list[dict[str, Any]] = []

    @staticmethod
    def envelope(resource: Any) -> dict[str, Any]:
        return {"project_id": PROJECT, "binding_revision": BINDING, "resource": resource}

    def native_run(self, *, succeeded: bool) -> dict[str, Any]:
        return {
            "project_id": PROJECT,
            "binding_revision": BINDING,
            "request_id": REQUEST,
            "idea_id": IDEA,
            "actor_id": ACTOR,
            "build": "1.2.3",
            "input": {"productUrls": ["https://www.amazon.com/dp/example"]},
            "voc_id": VOC,
            "run": {
                "voc_id": VOC,
                "memory_binding_warning": None,
                "run_id": RUN,
                "dataset_id": DATASET if succeeded else None,
                "status": "succeeded" if succeeded else "running",
                "terminal": succeeded,
                "run_url": "https://console.apify.com/actors/runs/provider-run-001",
                "dataset_url": (
                    "https://console.apify.com/storage/datasets/dataset-001" if succeeded else None
                ),
                "result_fingerprint": "2" * 64 if succeeded else None,
                "changed": succeeded,
                "item_count": 1 if succeeded else None,
            },
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
        size = int(self.headers.get("Content-Length", "0"))
        value = json.loads(self.rfile.read(size) if size else b"{}")
        self.server.platform.bodies.append(value)
        return value

    def _json(self, value: Any, status: int = 200) -> None:
        raw = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _attachment(self, raw: bytes, content_type: str, filename: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        self._authorize()
        platform = self.server.platform
        parsed = urlsplit(self.path)
        path = unquote(parsed.path)
        query = parse_qs(parsed.query)
        if path == "/api/platform/v3/personal-key":
            platform.operations.append("self_context")
            self._json(
                {
                    "project_id": PROJECT,
                    "binding_revision": BINDING,
                    "credential_profile": "user_research",
                    "allowed_actions": [
                        "idea.read",
                        "idea.write",
                        "report.publish",
                        "voc.collect",
                        "voc.results.read",
                    ],
                }
            )
            return
        if path.endswith("/providers/apify/actors"):
            platform.operations.append("actor_search")
            if query != {"search": ["amazon reviews"], "offset": ["0"], "limit": ["50"]}:
                self.send_error(422)
                return
            self._json(
                platform.envelope(
                    {
                        "total": 1,
                        "offset": 0,
                        "limit": 50,
                        "count": 1,
                        "next_offset": None,
                        "has_more": False,
                        "items": [
                            {
                                "actor_id": ACTOR,
                                "actor_ref": ACTOR_REF,
                                "name": "amazon-reviews",
                                "username": "publisher",
                                "title": "Amazon Reviews",
                                "description": "Collects public product reviews",
                                "notice": None,
                                "badge": None,
                                "categories": ["ECOMMERCE"],
                                "stats": {},
                            }
                        ],
                    }
                )
            )
            return
        if path.endswith("/providers/apify/actors/" + ACTOR):
            platform.operations.append("actor_detail")
            self._json(
                platform.envelope(
                    {
                        "actor_id": ACTOR,
                        "actor_ref": ACTOR_REF,
                        "name": "amazon-reviews",
                        "username": "publisher",
                        "title": "Amazon Reviews",
                        "description": "Collects public product reviews",
                        "notice": None,
                        "badge": None,
                        "categories": ["ECOMMERCE"],
                        "stats": {},
                        "is_public": True,
                        "is_deprecated": False,
                        "actor_permission_level": "READ",
                        "default_run_options": {"build": "1.2.3"},
                    }
                )
            )
            return
        if path.endswith("/providers/apify/actors/" + ACTOR + "/input-schema"):
            platform.operations.append("actor_schema")
            self._json(
                platform.envelope(
                    {
                        "actor_id": ACTOR,
                        "build": "1.2.3",
                        "input_schema": {
                            "type": "object",
                            "required": ["productUrls"],
                            "properties": {"productUrls": {"type": "array"}},
                        },
                        "example_input": {"productUrls": ["https://www.amazon.com/dp/example"]},
                    }
                )
            )
            return
        if path.endswith(f"/ideas/{IDEA}/voc"):
            platform.operations.append("voc_discovery")
            self._json(
                {
                    "project_id": PROJECT,
                    "binding_revision": BINDING,
                    "idea_id": IDEA,
                    "kind": "native",
                    "offset": 0,
                    "limit": 50,
                    "has_more": False,
                    "items": [
                        {
                            "idea_id": IDEA,
                            "resource_id": VOC,
                            "kind": "native",
                            "voc_id": VOC,
                            "status": "running",
                            "title": None,
                            "created_at": None,
                            "dataset_ids": [],
                            "voc_detail_url": None,
                        }
                    ],
                }
            )
            return
        if "/voc/runs/" in path:
            platform.operations.append("run_read")
            platform.run_reads += 1
            if platform.scenario == "read_unavailable":
                self.send_error(500)
                return
            if platform.scenario == "read_rejected":
                self.send_error(400)
                return
            self._json(platform.native_run(succeeded=platform.run_reads > 1))
            return
        if path.endswith(f"/voc/{VOC}/datasets"):
            platform.operations.append("dataset_list")
            dataset = self._dataset_metadata()
            if platform.scenario == "foreign_dataset":
                dataset["dataset_id"] = "different-dataset"
            self._json(platform.envelope([dataset]))
            return
        if path.endswith(f"/voc/{VOC}/datasets/{DATASET}"):
            platform.operations.append("dataset_metadata")
            self._json(platform.envelope(self._dataset_metadata()))
            return
        if path.endswith(f"/voc/{VOC}/datasets/{DATASET}/items"):
            platform.operations.append("dataset_items")
            offset = int(query.get("offset", ["0"])[0])
            if platform.scenario == "paged_items" and offset == 0:
                page = {
                    "offset": 0,
                    "count": 1,
                    "total": 2,
                    "next_offset": 1,
                    "has_more": True,
                    "items": [{"reviewText": "page-one-review"}],
                }
            elif platform.scenario == "paged_items":
                page = {
                    "offset": offset,
                    "count": 1,
                    "total": 2,
                    "next_offset": None,
                    "has_more": False,
                    "items": [{"reviewText": "page-two-review"}],
                }
            elif platform.scenario == "no_excerpts":
                page = {
                    "offset": 0,
                    "count": 1,
                    "total": 1,
                    "next_offset": None,
                    "has_more": False,
                    "items": [{"profileUrl": "https://example.com/user", "rating": 5}],
                }
            else:
                page = {
                    "offset": 0,
                    "count": 1,
                    "total": 1,
                    "next_offset": None,
                    "has_more": False,
                    "items": [{"reviewText": "Activation was confusing"}],
                }
            self._json(
                platform.envelope(
                    {
                        "dataset_id": DATASET,
                        "dataset_url": "https://console.apify.com/storage/datasets/dataset-001",
                        "limit": 20,
                        **page,
                    }
                )
            )
            return
        if path.endswith(f"/voc/{VOC}/datasets/{DATASET}/export"):
            platform.operations.append("dataset_export")
            platform.exports += 1
            if query != {"format": ["jsonl"]}:
                self.send_error(422)
                return
            content = DATASET_BYTES
            if platform.scenario == "changed_export" and platform.exports > 1:
                content = b'{"reviewText":"Different snapshot"}\n'
            self._attachment(content, "application/x-ndjson", "dataset.jsonl")
            return
        if path.endswith("/report-sources/current"):
            platform.operations.append("report_source")
            platform.source_reads += 1
            self._require_report_query(query)
            source = self._source()
            if platform.scenario == "dynamic_source" and platform.source_reads > 1:
                source["context"]["sampling"] = "same-snapshot-second-read"
            if platform.scenario in {"unknown_shape", "no_excerpts"}:
                source["context"]["evidence"] = []
                source["context"]["sources"][0]["sample_content_hash"] = "4" * 64
            self._json(source)
            return
        if path.endswith("/reports/current/download"):
            platform.operations.append("report_download")
            self._require_report_query(query)
            content = platform.report_content.encode()
            if platform.scenario == "bad_report_download":
                content = b"# Different report\n"
            self._attachment(content, "text/markdown", "report.md")
            return
        if path.endswith("/reports/current"):
            platform.operations.append("report_current")
            self._require_report_query(query)
            if not platform.published:
                self.send_error(404)
                return
            self._json(self._publication(platform.report_content, platform.voices))
            return
        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        platform = self.server.platform
        path = urlsplit(self.path).path
        body = self._body()
        if path.endswith("/ideas"):
            platform.operations.append("idea_create")
            self._json(
                platform.envelope(
                    {"idea_id": IDEA, "title": body["title"], "description": body["description"]}
                )
            )
            return
        if path.endswith(f"/ideas/{IDEA}/voc/runs"):
            platform.operations.append("run_start")
            platform.start_requests += 1
            expected = {
                "idempotency_key": "native-voc-native-loop-0001",
                "actor_id": ACTOR,
                "build": "1.2.3",
                "input": {"productUrls": ["https://www.amazon.com/dp/example"]},
            }
            if body != expected:
                self.send_error(422)
                return
            if not platform.claimed:
                platform.claimed = True
                platform.provider_starts += 1
                if platform.scenario == "ambiguous_start":
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
            self._json(platform.native_run(succeeded=False))
            return
        if path.endswith("/reports"):
            platform.operations.append("report_publish")
            source = self._source()
            if (
                body.get("idea_id") != IDEA
                or body.get("parent_id") != VOC
                or body.get("source_revision_id") != source["source_revision_id"]
                or body.get("source_fingerprint") != source["source_fingerprint"]
                or not body.get("report", {}).get("content", "").strip()
            ):
                self.send_error(409)
                return
            voices = body.get("representative_voices", [])
            if not isinstance(voices, list):
                self.send_error(409)
                return
            platform.published = True
            platform.report_content = body["report"]["content"]
            platform.voices = voices
            if platform.scenario == "publish_dropped_voices":
                platform.voices = []
                self.send_error(500)
                return
            if platform.scenario == "uncertain_publish":
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            if platform.scenario == "publish_lost_response":
                self.send_error(500)
                return
            self._json(self._publication(platform.report_content, platform.voices))
            return
        self.send_error(404)

    @staticmethod
    def _dataset_metadata() -> dict[str, Any]:
        return {
            "dataset_id": DATASET,
            "dataset_url": "https://console.apify.com/storage/datasets/dataset-001",
            "item_count": 1,
            "created_at": "2026-09-16T00:00:00Z",
            "modified_at": "2026-09-16T00:01:00Z",
        }

    @staticmethod
    def _source() -> dict[str, Any]:
        return {
            "project_id": PROJECT,
            "binding_revision": BINDING,
            "idea_id": IDEA,
            "parent_kind": "voc",
            "parent_id": VOC,
            "source_revision_id": SOURCE_REVISION,
            "source_fingerprint": SOURCE_FINGERPRINT,
            "source_mode": "native_dataset",
            "context": {
                "schema_version": "native-voc-report-context.v1",
                "coverage_status": "sampled",
                "sampling": "first_page_per_source",
                "sources": [
                    {
                        "provider_dataset_id": DATASET,
                        "total_item_count": 1,
                        "sampled_item_count": 1,
                        "readable_item_count": 1,
                        "has_more": False,
                        "modified_at": "2026-09-16T00:01:00Z",
                    }
                ],
                "omitted_source_count": 0,
                "omitted_evidence_count": 0,
                "evidence": [
                    {
                        "evidence_id": EVIDENCE,
                        "source_strategy": None,
                        "provider_dataset_id": DATASET,
                        "item_index": 0,
                        "excerpt": "Activation was confusing",
                    }
                ],
            },
        }

    @staticmethod
    def _publication(
        report: str = REPORT, voices: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        stored = voices or []
        return {
            "project_id": PROJECT,
            "binding_revision": BINDING,
            "idea_id": IDEA,
            "parent_kind": "voc",
            "parent_id": VOC,
            "source_mode": "native_dataset",
            "report_revision_id": REPORT_REVISION,
            "source_revision_id": SOURCE_REVISION,
            "source_fingerprint": SOURCE_FINGERPRINT,
            "content_hash": "3" * 64,
            "generated_at": "2026-09-16T00:02:00Z",
            "report": {
                "contract_version": "analysis-report.v1",
                "format": "markdown",
                "content": report,
            },
            "representative_voices": stored,
            "voices_status": "available" if stored else "not_provided",
            "freshness_basis": "provider_snapshot",
        }

    @staticmethod
    def _require_report_query(query: dict[str, list[str]]) -> None:
        if query != {
            "idea_id": [IDEA],
            "parent_kind": ["voc"],
            "parent_id": [VOC],
            "source_mode": ["native_dataset"],
        }:
            raise AssertionError("report query is not exact")


class _Server(ThreadingHTTPServer):
    def __init__(self, platform: _Platform) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.platform = platform

    def handle_error(self, _request: Any, _client_address: Any) -> None:
        return


class NativeVocJourneyTests(unittest.TestCase):
    @staticmethod
    def _runner_module():
        spec = importlib.util.spec_from_file_location(
            "native_voc_test_runner", ROOT / "scripts/tdd_native_voc_journey.py"
        )
        if spec is None or spec.loader is None:
            raise AssertionError("runner module unavailable")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _invoke(
        self,
        installed: Path,
        temporary: Path,
        server: _Server,
        *,
        phase: str,
        report: str = REPORT,
        voices: list[dict[str, Any]] | None = None,
        include_voices: bool = True,
    ):
        output_dir = temporary / "artifacts"
        output_dir.mkdir(exist_ok=True)
        report_path = output_dir / "authored-report.md"
        report_path.write_text(report)
        env = {
            "AUDIENCE_ALLOW_LOCALHOST_HTTP_FOR_TESTS": "1",
            "AUDIENCE_PLATFORM_BASE_URL": f"http://127.0.0.1:{server.server_port}",
            "AUDIENCE_API_KEY": TOKEN,
            "USER_RESEARCH_TDD_RUN_TOKEN": "native-loop",
            "USER_RESEARCH_TDD_POLL_ATTEMPTS": "3",
            "USER_RESEARCH_TDD_POLL_INTERVAL_SECONDS": "0",
            "USER_RESEARCH_NATIVE_VOC_SEARCH": "amazon reviews",
            "USER_RESEARCH_NATIVE_VOC_ACTOR_ID": ACTOR,
            "USER_RESEARCH_NATIVE_VOC_INPUT_JSON": json.dumps(
                {"productUrls": ["https://www.amazon.com/dp/example"]}
            ),
            "USER_RESEARCH_NATIVE_VOC_REPORT_PATH": str(report_path),
            "USER_RESEARCH_NATIVE_VOC_EXPORT_FORMAT": "jsonl",
            "USER_RESEARCH_NATIVE_VOC_OUTPUT_DIR": str(output_dir),
            "AUDIENCE_ATTACHMENT_DIR": str(output_dir),
        }
        if include_voices:
            voices_path = output_dir / "authored-voices.json"
            voices_path.write_text(
                json.dumps(DEFAULT_VOICES if voices is None else voices), encoding="utf-8"
            )
            env["USER_RESEARCH_NATIVE_VOC_VOICES_PATH"] = str(voices_path)
        completed = subprocess.run(
            [
                sys.executable,
                str(installed / "scripts/tdd_native_voc_journey.py"),
                "--phase",
                phase,
            ],
            cwd=temporary,
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertNotIn(TOKEN, completed.stdout + completed.stderr)
        self.assertNotIn("Activation was confusing", completed.stdout)
        return completed, output_dir

    def _run(
        self,
        scenario: str,
        *,
        report: str = REPORT,
        collect_repeats: int = 1,
        publish: bool = False,
        publish_repeats: int = 1,
        voices: list[dict[str, Any]] | None = None,
        include_voices: bool = True,
    ):
        platform = _Platform(scenario)
        server = _Server(platform)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        results = []
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
                for _ in range(collect_repeats):
                    results.append(
                        self._invoke(
                            installed,
                            temporary,
                            server,
                            phase="collect",
                            report=report,
                            voices=voices,
                            include_voices=include_voices,
                        )
                    )
                if publish and results[-1][0].returncode == 0:
                    for _ in range(publish_repeats):
                        results.append(
                            self._invoke(
                                installed,
                                temporary,
                                server,
                                phase="publish",
                                report=report,
                                voices=voices,
                                include_voices=include_voices,
                            )
                        )
                        if results[-1][0].returncode != 0:
                            break
                artifact_dir = temporary / "artifacts"
                snapshot = {
                    item.name: item.read_bytes()
                    for item in artifact_dir.iterdir()
                    if item.is_file()
                }
                results = [(completed, snapshot) for completed, _ in results]
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        return results, platform

    def test_cold_installed_cli_completes_native_dataset_report_loop(self) -> None:
        results, platform = self._run("happy", publish=True)
        completed, output_dir = results[-1]
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        result = json.loads(completed.stdout)["result"]
        self.assertEqual(result["analysis_status"], "published_and_downloaded")
        self.assertEqual(result["dataset_id"], DATASET)
        self.assertNotIn("provider-run-001", json.dumps(result["report_attachment"]))
        self.assertEqual(output_dir[f"native-voc-native-loop-{DATASET}.jsonl"], DATASET_BYTES)
        self.assertEqual(
            output_dir[f"native-voc-report-native-loop-{REPORT_REVISION}.md"].decode(), REPORT
        )
        self.assertTrue(platform.published)
        self.assertEqual(result["voices_status"], "available")
        self.assertEqual(result["voice_count"], 1)
        published = [body for body in platform.bodies if "report" in body]
        self.assertEqual(len(published), 1)
        self.assertEqual(published[0]["representative_voices"], DEFAULT_VOICES)
        self.assertNotIn("source_coverage", published[0])
        self.assertNotIn("Activation was confusing", completed.stdout)
        self.assertLess(
            platform.operations.index("dataset_list"), platform.operations.index("dataset_metadata")
        )
        self.assertLess(
            platform.operations.index("dataset_items"), platform.operations.index("report_source")
        )
        self.assertLess(
            platform.operations.index("report_source"), platform.operations.index("report_publish")
        )
        self.assertLess(
            platform.operations.index("report_current"),
            platform.operations.index("report_download"),
        )

    def test_ambiguous_start_reuses_same_claim_without_second_provider_start(self) -> None:
        results, platform = self._run("ambiguous_start", collect_repeats=2)
        self.assertEqual(results[0][0].returncode, 1)
        self.assertEqual(
            json.loads(results[0][0].stdout), {"error": "transport_error", "ok": False}
        )
        self.assertEqual(
            results[-1][0].returncode, 0, results[-1][0].stdout + results[-1][0].stderr
        )
        self.assertEqual(platform.start_requests, 2)
        self.assertEqual(platform.provider_starts, 1)
        starts = [body for body in platform.bodies if "actor_id" in body]
        self.assertEqual(
            {body["idempotency_key"] for body in starts}, {"native-voc-native-loop-0001"}
        )

    def test_collect_pages_dataset_until_has_more_is_false(self) -> None:
        results, platform = self._run("paged_items")
        completed, snapshot = results[-1]
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        result = json.loads(completed.stdout)["result"]
        self.assertFalse(result["sample_has_more"])
        self.assertEqual(result["sampled_item_count"], 2)
        self.assertEqual(platform.operations.count("dataset_items"), 2)
        saved = snapshot["native-voc-items-native-loop.json"]
        self.assertIn(b"page-one-review", saved)
        self.assertIn(b"page-two-review", saved)
        self.assertNotIn("page-one-review", completed.stdout)
        self.assertNotIn("page-two-review", completed.stdout)

    def test_read_5xx_uses_discovery_and_does_not_restart(self) -> None:
        results, platform = self._run("read_unavailable")
        completed, _snapshot = results[-1]
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        result = json.loads(completed.stdout)["result"]
        self.assertEqual(result["request_id"], REQUEST)
        self.assertEqual(result["voc_id"], VOC)
        self.assertEqual(result["collection_status"], "read_unavailable")
        self.assertEqual(result["discovery_status"], "running")
        self.assertEqual(result["next_action"], "poll_same_request_do_not_restart")
        self.assertEqual(platform.start_requests, 1)
        self.assertEqual(platform.provider_starts, 1)
        self.assertIn("voc_discovery", platform.operations)
        self.assertNotIn("dataset_items", platform.operations)
        self.assertNotIn("report_publish", platform.operations)

    def test_read_4xx_stops_without_discovery(self) -> None:
        results, platform = self._run("read_rejected")
        completed, _snapshot = results[-1]
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(
            json.loads(completed.stdout),
            {"error": "http_error", "http_status": 400, "ok": False},
        )
        self.assertEqual(platform.run_reads, 1)
        self.assertEqual(platform.start_requests, 1)
        self.assertNotIn("voc_discovery", platform.operations)
        self.assertNotIn("report_publish", platform.operations)

    def test_foreign_dataset_stops_before_provider_reads_or_report(self) -> None:
        results, platform = self._run("foreign_dataset")
        completed, _ = results[-1]
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(
            json.loads(completed.stdout),
            {"error": "native_voc_dataset_binding_mismatch", "ok": False},
        )
        self.assertNotIn("dataset_metadata", platform.operations)
        self.assertNotIn("dataset_export", platform.operations)
        self.assertNotIn("report_publish", platform.operations)

    def test_collect_returns_source_before_agent_authors_report(self) -> None:
        results, platform = self._run("happy")
        completed, output_dir = results[-1]
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        result = json.loads(completed.stdout)["result"]
        self.assertEqual(result["analysis_status"], "awaiting_agent_authored_report")
        source = json.loads(output_dir[Path(result["report_source_path"]).name])
        self.assertEqual(source["context"]["evidence"][0]["excerpt"], "Activation was confusing")
        self.assertIn("native-voc-state-native-loop.json", output_dir)
        self.assertNotIn("report_publish", platform.operations)

    def test_collect_rerun_verifies_existing_dataset_without_overwrite(self) -> None:
        results, platform = self._run("changed_export", collect_repeats=2)
        first, output_dir = results[0]
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        dataset_name = f"native-voc-native-loop-{DATASET}.jsonl"
        self.assertEqual(results[1][0].returncode, 1, results[1][0].stdout)
        self.assertEqual(
            json.loads(results[1][0].stdout),
            {"error": "existing_artifact_mismatch", "ok": False},
        )
        self.assertEqual(platform.operations.count("dataset_export"), 2)
        self.assertEqual(output_dir[dataset_name], DATASET_BYTES)

    def test_collect_rerun_keeps_saved_source_when_same_fingerprint_has_display_drift(self) -> None:
        results, _ = self._run("dynamic_source", collect_repeats=2)
        self.assertEqual(results[0][0].returncode, 0, results[0][0].stdout)
        self.assertEqual(results[1][0].returncode, 0, results[1][0].stdout)
        snapshot = results[1][1]
        source = json.loads(snapshot["native-voc-source-native-loop.json"])
        self.assertEqual(source["context"]["sampling"], "first_page_per_source")

    def test_publish_rejects_download_that_differs_from_readback(self) -> None:
        results, platform = self._run("bad_report_download", publish=True)
        completed, snapshot = results[-1]
        self.assertEqual(completed.returncode, 1, completed.stdout)
        self.assertEqual(
            json.loads(completed.stdout),
            {"error": "native_voc_report_download_mismatch", "ok": False},
        )
        self.assertNotIn(f"native-voc-report-native-loop-{REPORT_REVISION}.md", snapshot)
        self.assertTrue(platform.published)

    def test_unknown_actor_shape_uses_raw_sample_hash_without_invented_text_field(self) -> None:
        results, platform = self._run("unknown_shape", report=RAW_REPORT, publish=True)
        completed, snapshot = results[-1]
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        result = json.loads(completed.stdout)["result"]
        self.assertEqual(result["analysis_status"], "published_and_downloaded")
        source = json.loads(snapshot["native-voc-source-native-loop.json"])
        self.assertEqual(source["context"]["evidence"], [])
        self.assertEqual(source["context"]["sources"][0]["sample_content_hash"], "4" * 64)
        self.assertEqual(
            snapshot[f"native-voc-report-native-loop-{REPORT_REVISION}.md"].decode(),
            RAW_REPORT,
        )
        self.assertTrue(platform.published)

    def test_cli_timeout_is_code_only_and_state_budget_covers_large_native_input(self) -> None:
        runner = self._runner_module()
        with (
            patch.object(
                runner.subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired(["api.py"], timeout=70),
            ),
            self.assertRaisesRegex(runner.JourneyError, "^cli_timeout$"),
        ):
            runner.cli("project_voc_native_start")
        with (
            patch.object(
                runner.subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired(["api.py"], timeout=20),
            ),
            self.assertRaisesRegex(runner.JourneyError, "^cli_timeout$"),
        ):
            runner.capabilities()
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state.json"
            state.write_bytes(b"x" * (70 * 1024))
            self.assertEqual(len(runner.regular_bytes(state, runner.STATE_MAX_BYTES)), 70 * 1024)

    def test_publish_rerun_uses_saved_revision_without_a_second_post(self) -> None:
        results, platform = self._run("happy", publish=True, publish_repeats=2)
        completed, _snapshot = results[-1]
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertEqual(platform.operations.count("report_publish"), 1)
        self.assertTrue(platform.published)

    def test_publish_transport_loss_adopts_the_saved_report(self) -> None:
        results, platform = self._run("uncertain_publish", publish=True)
        completed, _snapshot = results[-1]
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertEqual(
            json.loads(completed.stdout)["result"]["analysis_status"],
            "published_and_downloaded",
        )
        self.assertEqual(platform.operations.count("report_publish"), 1)
        self.assertTrue(platform.published)

    def test_publish_5xx_adopts_the_saved_report_without_a_second_post(self) -> None:
        results, platform = self._run("publish_lost_response", publish=True)
        completed, _snapshot = results[-1]
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        result = json.loads(completed.stdout)["result"]
        self.assertEqual(result["analysis_status"], "published_and_downloaded")
        self.assertEqual(platform.operations.count("report_publish"), 1)
        self.assertIn("report_current", platform.operations)
        self.assertTrue(platform.published)
        self.assertEqual(result["voices_status"], "available")
        self.assertEqual(result["voice_count"], 1)

    def test_publish_refuses_markdown_only_when_excerpts_exist(self) -> None:
        results, platform = self._run("happy", publish=True, include_voices=False)
        completed = results[-1][0]
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(
            json.loads(completed.stdout),
            {"error": "native_voc_voices_required", "ok": False},
        )
        self.assertNotIn("report_publish", platform.operations)

    def test_publish_rejects_voice_text_that_was_not_read(self) -> None:
        voices = [
            {
                "text": "This sentence was never in the dataset",
                "theme": "invented",
                "translation": {"locale": "zh-CN", "text": "这条不在已读数据集里"},
            }
        ]
        results, platform = self._run("happy", publish=True, voices=voices)
        completed = results[-1][0]
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(
            json.loads(completed.stdout),
            {"error": "invalid_native_voc_voices", "ok": False},
        )
        self.assertNotIn("report_publish", platform.operations)

    def test_publish_without_excerpts_omits_voices(self) -> None:
        results, platform = self._run("no_excerpts", publish=True, include_voices=False)
        completed = results[-1][0]
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        result = json.loads(completed.stdout)["result"]
        self.assertEqual(result["voices_status"], "not_provided")
        self.assertEqual(result["voice_count"], 0)
        published = [body for body in platform.bodies if "report" in body]
        self.assertEqual(len(published), 1)
        self.assertNotIn("representative_voices", published[0])
        self.assertEqual(platform.operations.count("report_publish"), 1)

    def test_publish_5xx_does_not_adopt_a_report_that_dropped_voices(self) -> None:
        results, platform = self._run("publish_dropped_voices", publish=True)
        completed = results[-1][0]
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(
            json.loads(completed.stdout),
            {"error": "http_error", "http_status": 500, "ok": False},
        )
        self.assertEqual(platform.operations.count("report_publish"), 1)
        self.assertNotIn("report_download", platform.operations)

    def test_publish_reads_only_a_regular_file_inside_the_output_dir(self) -> None:
        runner = self._runner_module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output_dir = root / "artifacts"
            output_dir.mkdir()
            secret = root / "secret.md"
            secret.write_text("# secret\n")
            approved = output_dir / "authored-report.md"
            approved.write_text(REPORT)
            previous = os.environ.get("USER_RESEARCH_NATIVE_VOC_REPORT_PATH")
            try:
                os.environ["USER_RESEARCH_NATIVE_VOC_REPORT_PATH"] = str(secret)
                with self.assertRaisesRegex(runner.JourneyError, "^invalid_native_voc_report$"):
                    runner.approved_report_text(output_dir)
                escaped = output_dir / ".." / secret.name
                os.environ["USER_RESEARCH_NATIVE_VOC_REPORT_PATH"] = str(escaped)
                with self.assertRaisesRegex(runner.JourneyError, "^invalid_native_voc_report$"):
                    runner.approved_report_text(output_dir)
                link = output_dir / "linked-report.md"
                link.symlink_to(secret)
                os.environ["USER_RESEARCH_NATIVE_VOC_REPORT_PATH"] = str(link)
                with self.assertRaisesRegex(runner.JourneyError, "^invalid_native_voc_report$"):
                    runner.approved_report_text(output_dir)
                fifo = output_dir / "report.fifo"
                os.mkfifo(fifo)
                os.environ["USER_RESEARCH_NATIVE_VOC_REPORT_PATH"] = str(fifo)
                with self.assertRaisesRegex(runner.JourneyError, "^invalid_native_voc_report$"):
                    runner.approved_report_text(output_dir)
                os.environ["USER_RESEARCH_NATIVE_VOC_REPORT_PATH"] = str(output_dir)
                with self.assertRaisesRegex(runner.JourneyError, "^invalid_native_voc_report$"):
                    runner.approved_report_text(output_dir)
                os.environ["USER_RESEARCH_NATIVE_VOC_REPORT_PATH"] = str(approved)
                self.assertEqual(runner.approved_report_text(output_dir), REPORT)
            finally:
                if previous is None:
                    os.environ.pop("USER_RESEARCH_NATIVE_VOC_REPORT_PATH", None)
                else:
                    os.environ["USER_RESEARCH_NATIVE_VOC_REPORT_PATH"] = previous

    def test_voice_loader_uses_item_text_and_ignores_urls(self) -> None:
        runner = self._runner_module()
        leaves = runner.citable_leaves(
            [
                {
                    "reviewText": "Activation was confusing",
                    "profileUrl": "https://example.com/user",
                },
                {"contact": "alex@example.com"},
            ]
        )
        self.assertEqual(leaves, ["Activation was confusing"])
        self.assertEqual(runner.citable_leaves([{"rating": 5}]), [])
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            previous = os.environ.get("USER_RESEARCH_NATIVE_VOC_VOICES_PATH")
            try:
                os.environ.pop("USER_RESEARCH_NATIVE_VOC_VOICES_PATH", None)
                with self.assertRaisesRegex(runner.JourneyError, "^native_voc_voices_required$"):
                    runner.load_representative_voices(output_dir, leaves)
                self.assertEqual(runner.load_representative_voices(output_dir, []), [])
                empty = output_dir / "empty-voices.json"
                empty.write_text("[]", encoding="utf-8")
                os.environ["USER_RESEARCH_NATIVE_VOC_VOICES_PATH"] = str(empty)
                with self.assertRaisesRegex(runner.JourneyError, "^native_voc_voices_required$"):
                    runner.load_representative_voices(output_dir, leaves)
                invented = output_dir / "invented-voices.json"
                invented.write_text(
                    json.dumps(
                        [
                            {
                                "text": "not in the items",
                                "theme": "invented",
                                "translation": {"locale": "zh-CN", "text": "不在原文里"},
                            }
                        ]
                    ),
                    encoding="utf-8",
                )
                os.environ["USER_RESEARCH_NATIVE_VOC_VOICES_PATH"] = str(invented)
                with self.assertRaisesRegex(runner.JourneyError, "^invalid_native_voc_voices$"):
                    runner.load_representative_voices(output_dir, [])
            finally:
                if previous is None:
                    os.environ.pop("USER_RESEARCH_NATIVE_VOC_VOICES_PATH", None)
                else:
                    os.environ["USER_RESEARCH_NATIVE_VOC_VOICES_PATH"] = previous


if __name__ == "__main__":
    unittest.main()
