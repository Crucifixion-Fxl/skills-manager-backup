from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import patch

from user_research import AudienceClient, AudienceClientConfig, Operation, SafeApiError
from user_research.client import DEFAULT_AUDIENCE_PLATFORM_BASE_URL

KEY = "awpk_v2_" + "a" * 26 + "_" + "B" * 43
CONTEXT = {
    "project_id": "kiwibit",
    "binding_revision": "pbr_" + "a" * 64,
    "credential_profile": "user_research",
    "allowed_actions": sorted(
        {
            "audience_sync.capabilities.read",
            "campaign.review.start",
            "forms.create",
            "idea.read",
            "idea.write",
            "research.materialize",
            "research.prepare",
            "research.results.read",
            "voc.collect",
            "voc.results.read",
        }
    ),
}


class ClientTests(unittest.TestCase):
    def test_default_origin_is_prod_us_personal_api(self) -> None:
        self.assertEqual(
            AudienceClientConfig().base_url,
            "https://audience-workflow-api-prod-us.addx.live",
        )

    def client(self, attachment_dir: str = "") -> AudienceClient:
        return AudienceClient(
            AudienceClientConfig(
                base_url="http://127.0.0.1:1",
                personal_api_key=KEY,
                allow_localhost_http=True,
                attachment_dir=attachment_dir,
            )
        )

    def test_fixed_origin_key_shape_and_secret_free_repr(self) -> None:
        with self.assertRaisesRegex(SafeApiError, "https_required"):
            AudienceClient(
                AudienceClientConfig(base_url="http://example.test", personal_api_key=KEY)
            )
        with self.assertRaisesRegex(SafeApiError, "invalid_personal_api_key"):
            AudienceClient(AudienceClientConfig(personal_api_key="not-a-personal-key"))
        self.assertNotIn(KEY, repr(AudienceClientConfig(personal_api_key=KEY)))

    def test_untrusted_https_origin_is_rejected_before_authorization(self) -> None:
        with patch("user_research.client.build_opener") as opener:
            with self.assertRaisesRegex(SafeApiError, "origin_not_allowed"):
                AudienceClient(
                    AudienceClientConfig(base_url="https://evil.example", personal_api_key=KEY)
                )
        opener.assert_not_called()
        with self.assertRaisesRegex(SafeApiError, "origin_not_allowed"):
            AudienceClient(
                AudienceClientConfig(
                    base_url="https://audience-workflow-api-prod-us.addx.live:8443",
                    personal_api_key=KEY,
                )
            )
        with self.assertRaisesRegex(SafeApiError, "invalid_origin"):
            AudienceClient(
                AudienceClientConfig(
                    base_url="https://audience-workflow-api-prod-us.addx.live:99999",
                    personal_api_key=KEY,
                )
            )

    def test_known_audience_https_origins_are_allowed(self) -> None:
        for origin in (
            DEFAULT_AUDIENCE_PLATFORM_BASE_URL,
            "https://audience-workflow-api-staging-us.addx.live",
        ):
            with self.subTest(origin=origin):
                client = AudienceClient(AudienceClientConfig(base_url=origin, personal_api_key=KEY))
                self.assertEqual(client._origin, origin)

    def test_self_context_uses_fixed_route_and_records_exact_binding(self) -> None:
        client = self.client()
        with patch.object(
            client._opener, "open", return_value=io.BytesIO(json.dumps(CONTEXT).encode())
        ) as opened:
            self.assertEqual(client.verify_self_context(), CONTEXT)
        request = opened.call_args.args[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.full_url, "http://127.0.0.1:1/api/platform/v3/personal-key")
        self.assertEqual(request.get_header("Authorization"), "Bearer " + KEY)
        self.assertEqual(client.verified_context, CONTEXT)

    def test_project_route_is_injected_from_context_and_binding_is_checked(self) -> None:
        client = self.client()
        operation = Operation("personal_research_journey_status")
        response = {
            "project_id": "kiwibit",
            "binding_revision": CONTEXT["binding_revision"],
            "research_id": "research_" + "b" * 26,
        }
        with (
            patch(
                "user_research.client.validate_operation_response", side_effect=[CONTEXT, response]
            ),
            patch.object(
                AudienceClient, "_call_with_read_retry", side_effect=[CONTEXT, response]
            ) as called,
        ):
            client.verify_self_context()
            self.assertEqual(
                client.call(operation, path={"research_id": "research_" + "b" * 26}), response
            )
        self.assertIn("/projects/kiwibit/research/", called.call_args.args[2])

        with self.assertRaisesRegex(SafeApiError, "project_binding_mismatch"):
            client.call(
                operation,
                path={"project_id": "other", "research_id": "research_" + "b" * 26},
            )

    def test_response_with_wrong_binding_or_resource_fails_closed(self) -> None:
        client = self.client()
        client._verified_context = CONTEXT
        operation = Operation("personal_research_journey_status")
        path = {"project_id": "kiwibit", "research_id": "research_" + "b" * 26}
        for response in (
            {
                "project_id": "kiwibit",
                "binding_revision": "pbr_" + "c" * 64,
                "research_id": path["research_id"],
            },
            {
                "project_id": "kiwibit",
                "binding_revision": CONTEXT["binding_revision"],
                "research_id": "research_" + "d" * 26,
            },
        ):
            with (
                patch.object(AudienceClient, "_call_with_read_retry", return_value=response),
                patch("user_research.client.validate_operation_response", return_value=response),
                self.assertRaisesRegex(SafeApiError, "invalid_response_schema"),
            ):
                client.call(operation, path=path)

    def test_voc_responses_require_exact_configuration_and_run_binding(self) -> None:
        client = self.client()
        client._verified_context = CONTEXT
        cases = (
            (
                Operation("personal_action_configuration_get"),
                {"project_id": "kiwibit", "idea_id": "idea_" + "a" * 26, "action_kind": "voc"},
                None,
                {
                    "project_id": "kiwibit",
                    "binding_revision": CONTEXT["binding_revision"],
                    "resource": {
                        "idea_id": "idea_" + "a" * 26,
                        "kind": "research",
                    },
                },
            ),
            (
                Operation("personal_voc_execution_get"),
                {
                    "project_id": "kiwibit",
                    "idea_id": "idea_" + "a" * 26,
                    "platform_run_id": "prun_" + "b" * 24,
                },
                None,
                {
                    "project_id": "kiwibit",
                    "binding_revision": CONTEXT["binding_revision"],
                    "idea_id": "idea_" + "a" * 26,
                    "platform_run_id": "prun_" + "c" * 24,
                },
            ),
        )
        for operation, path, body, response in cases:
            with (
                self.subTest(operation=operation.value),
                patch.object(AudienceClient, "_call_with_read_retry", return_value=response),
                patch("user_research.client.validate_operation_response", return_value=response),
                self.assertRaisesRegex(SafeApiError, "invalid_response_schema"),
            ):
                client.call(operation, path=path, body=body)

    def test_campaign_response_requires_requested_materialization_run(self) -> None:
        client = self.client()
        client._verified_context = CONTEXT
        operation = Operation("personal_research_journey_campaign_draft")
        body = {
            "idempotency_key": "campaign-draft-0001",
            "source_materialization_run_id": "run-0001",
            "destination_id": "brevo-primary",
            "body": {},
        }
        response = {
            "project_id": "kiwibit",
            "binding_revision": CONTEXT["binding_revision"],
            "research_id": "research_" + "b" * 26,
            "source_materialization_run_id": "run-0002",
        }
        with (
            patch.object(AudienceClient, "_call_with_read_retry", return_value=response),
            patch("user_research.client.validate_operation_response", return_value=response),
            self.assertRaisesRegex(SafeApiError, "invalid_response_schema"),
        ):
            client.call(
                operation,
                path={"project_id": "kiwibit", "research_id": "research_" + "b" * 26},
                body=body,
            )

    def test_research_effect_responses_require_requested_binding_fields(self) -> None:
        client = self.client()
        client._verified_context = {
            **CONTEXT,
            "allowed_actions": [*CONTEXT["allowed_actions"], "audience_sync.request"],
        }
        research_id = "research_" + "b" * 26
        cases = (
            (
                Operation("personal_research_journey_create"),
                {"project_id": "kiwibit"},
                {"idea_id": "idea_" + "a" * 26, "idempotency_key": "research-create-0001"},
                {
                    "project_id": "kiwibit",
                    "binding_revision": CONTEXT["binding_revision"],
                    "research_id": research_id,
                    "revision": 1,
                },
            ),
            (
                Operation("personal_research_journey_operation"),
                {"project_id": "kiwibit", "research_id": research_id},
                {
                    "operation": "sync_brevo",
                    "revision": 1,
                    "idempotency_key": "sync-brevo-0001",
                    "source_materialization_run_id": "run-0001",
                    "expected_count": 1,
                    "destination_id": "brevo-primary",
                },
                {
                    "request_id": "request-1",
                    "operation": "sync_brevo",
                    "revision": 1,
                    "state": "queued",
                    "project_id": "kiwibit",
                    "binding_revision": CONTEXT["binding_revision"],
                },
            ),
        )
        for operation, path, body, response in cases:
            with (
                self.subTest(operation=operation.value),
                patch.object(AudienceClient, "_call_with_read_retry", return_value=response),
                self.assertRaisesRegex(SafeApiError, "invalid_response_schema"),
            ):
                client.call(operation, path=path, body=body)

    def test_missing_declared_binding_fails_and_undeclared_identity_stays_legal(self) -> None:
        client = self.client()
        client._verified_context = CONTEXT
        idea_id = "idea_" + "a" * 26
        research_id = "research_" + "b" * 26
        omitted = {
            "project_id": "kiwibit",
            "binding_revision": CONTEXT["binding_revision"],
            "research_id": research_id,
            "revision": 1,
        }
        with (
            patch.object(AudienceClient, "_call_with_read_retry", return_value=omitted),
            self.assertRaisesRegex(SafeApiError, "invalid_response_schema"),
        ):
            client.call(
                Operation("personal_research_journey_form"),
                path={"project_id": "kiwibit", "research_id": research_id},
                body={
                    "idempotency_key": "questionnaire-v1",
                    "source_materialization_run_id": "run-0001",
                    "body": {"title": "Feeder"},
                },
            )
        datasets = {
            "project_id": "kiwibit",
            "binding_revision": CONTEXT["binding_revision"],
            "resource": [
                {
                    "dataset_id": "dataset-1",
                    "dataset_url": "https://api.apify.com/v2/datasets/dataset-1",
                }
            ],
        }
        with patch.object(AudienceClient, "_call_with_read_retry", return_value=datasets):
            listed = client.call(
                Operation("project_voc_dataset_list"),
                path={
                    "project_id": "kiwibit",
                    "idea_id": idea_id,
                    "voc_id": "ivoc_" + "c" * 26,
                },
            )
        self.assertEqual(listed["resource"][0]["dataset_id"], "dataset-1")
        mismatched = {**datasets, "idea_id": "idea_" + "d" * 26}
        with (
            patch.object(AudienceClient, "_call_with_read_retry", return_value=mismatched),
            self.assertRaisesRegex(SafeApiError, "invalid_response_schema"),
        ):
            client.call(
                Operation("project_voc_dataset_list"),
                path={
                    "project_id": "kiwibit",
                    "idea_id": idea_id,
                    "voc_id": "ivoc_" + "c" * 26,
                },
            )

    def test_action_grant_is_required_after_self_context(self) -> None:
        client = self.client()
        client._verified_context = {**CONTEXT, "allowed_actions": ["idea.read"]}
        with (
            patch.object(AudienceClient, "_call_with_read_retry") as request,
            self.assertRaisesRegex(SafeApiError, "operation_not_granted"),
        ):
            client.call(
                Operation("personal_research_journey_form"),
                path={"research_id": "research_" + "b" * 26},
                body={"idempotency_key": "questionnaire-v1", "body": {}},
            )
        request.assert_not_called()

    def test_brevo_sync_requires_sync_grant_before_transport(self) -> None:
        client = self.client()
        client._verified_context = {
            **CONTEXT,
            "allowed_actions": ["research.materialize"],
        }
        with (
            patch.object(AudienceClient, "_call_with_read_retry") as request,
            self.assertRaisesRegex(SafeApiError, "operation_not_granted"),
        ):
            client.call(
                Operation("personal_research_journey_operation"),
                path={"research_id": "research_" + "b" * 26},
                body={
                    "operation": "sync_brevo",
                    "revision": 1,
                    "idempotency_key": "sync-brevo-0001",
                    "source_materialization_run_id": "run-0001",
                    "expected_count": 1,
                    "destination_id": "brevo-primary",
                },
            )
        request.assert_not_called()

    def test_legacy_product_scope_operations_are_not_exposed(self) -> None:
        values = {operation.value for operation in Operation}
        self.assertIn("project_apify_actor_search", values)
        self.assertFalse(any("provider_token" in value for value in values))
        self.assertFalse(
            any(
                "{product_scope}" in spec.path
                for spec in __import__(
                    "user_research.operations", fromlist=["OPERATION_SPECS"]
                ).OPERATION_SPECS.values()
            )
        )

    def test_only_get_reads_retry(self) -> None:
        client = self.client()
        client._verified_context = CONTEXT
        retry = SafeApiError("transport_error")
        response = {"project_id": "kiwibit", "binding_revision": CONTEXT["binding_revision"]}
        with (
            patch.object(client, "_request", side_effect=[retry, response]) as request,
            patch("user_research.client.validate_operation_response", return_value=response),
        ):
            self.assertEqual(
                client.call(
                    Operation("personal_voc_capabilities"),
                    path={"project_id": "kiwibit"},
                ),
                response,
            )
        self.assertEqual(request.call_count, 2)
        with (
            patch.object(client, "_request", side_effect=retry) as request,
            patch("user_research.client.validate_operation_response", return_value={}),
        ):
            with self.assertRaisesRegex(SafeApiError, "transport_error"):
                client.call(
                    Operation("personal_voc_execution_start"),
                    path={"project_id": "kiwibit", "idea_id": "idea_" + "a" * 26},
                    body={
                        "expected_configuration_revision": 1,
                        "expected_content_hash": "b" * 64,
                        "idempotency_key": "voc-start-0001",
                    },
                )
        self.assertEqual(request.call_count, 1)

    def test_self_context_retries_stale_binding_revision(self) -> None:
        client = self.client()
        stale = SafeApiError("project_binding_revision_stale", 409)
        response = CONTEXT
        with (
            patch.object(client, "_request", side_effect=[stale, response]) as request,
            patch("user_research.client.validate_operation_response", return_value=response),
        ):
            self.assertEqual(
                client.call(Operation("get_project_personal_key_context")),
                response,
            )
        self.assertEqual(request.call_count, 2)

    def test_http_errors_expose_only_safe_code_and_status(self) -> None:
        from urllib.error import HTTPError

        error = HTTPError(
            "https://example.test",
            409,
            "secret provider message",
            {},
            io.BytesIO(b'{"detail":{"code":"binding_revision_stale","token":"leak"}}'),
        )
        client = self.client()
        with patch.object(client._opener, "open", side_effect=error):
            with self.assertRaises(SafeApiError) as raised:
                client.call(Operation("get_project_personal_key_context"))
        self.assertEqual(raised.exception.code, "binding_revision_stale")
        self.assertEqual(raised.exception.status, 409)
        self.assertNotIn("secret", str(raised.exception))
        self.assertNotIn("token", str(raised.exception))

    def test_csv_attachment_is_bounded_private_and_not_returned_as_content(self) -> None:
        class Response(io.BytesIO):
            def __init__(self, value: bytes, content_type: str = "text/csv") -> None:
                super().__init__(value)
                self.headers = Message()
                self.headers["Content-Type"] = content_type

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                self.close()

        operation = Operation("personal_research_journey_responses_csv")
        content = b"response_id,answer.question\nresponse_1,true\n"
        with tempfile.TemporaryDirectory() as directory:
            client = self.client(attachment_dir=directory)
            client._verified_context = CONTEXT
            target = Path(directory) / "responses.csv"
            with patch.object(client._opener, "open", return_value=Response(content)) as opened:
                result = client.download(
                    operation,
                    "responses.csv",
                    path={"research_id": "research_" + "b" * 26},
                    query={"form_id": "Form123"},
                )
            self.assertEqual(opened.call_args.kwargs["timeout"], 30.0)
            self.assertEqual(target.read_bytes(), content)
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            self.assertEqual(result["bytes"], len(content))
            self.assertNotIn("response_1", json.dumps(result))
            with (
                patch.object(client._opener, "open", return_value=Response(content)),
                self.assertRaisesRegex(SafeApiError, "output_exists"),
            ):
                client.download(
                    operation,
                    "responses.csv",
                    path={"research_id": "research_" + "b" * 26},
                    query={"form_id": "Form123"},
                )

        with self.assertRaisesRegex(SafeApiError, "attachment_output_required"):
            client.call(
                operation,
                path={"research_id": "research_" + "b" * 26},
                query={"form_id": "Form123"},
            )

    def test_attachment_get_retries_5xx_and_failed_write_leaves_no_file(self) -> None:
        from urllib.error import HTTPError

        class Response(io.BytesIO):
            def __init__(self, value: bytes) -> None:
                super().__init__(value)
                self.headers = Message()
                self.headers["Content-Type"] = "text/csv"

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                self.close()

        operation = Operation("personal_research_journey_responses_csv")
        content = b"response_id,answer\nresponse_1,true\n"
        path = {"research_id": "research_" + "b" * 26}
        query = {"form_id": "Form123"}
        unavailable = HTTPError(
            "https://example.test",
            503,
            "unavailable",
            Message(),
            io.BytesIO(b""),
        )
        rejected = HTTPError(
            "https://example.test",
            400,
            "rejected",
            Message(),
            io.BytesIO(b""),
        )
        with tempfile.TemporaryDirectory() as directory:
            client = self.client(attachment_dir=directory)
            client._verified_context = CONTEXT
            with patch.object(
                client._opener, "open", side_effect=[unavailable, Response(content)]
            ) as opened:
                client.download(operation, "responses.csv", path=path, query=query)
            self.assertEqual(opened.call_count, 2)
            self.assertEqual((Path(directory) / "responses.csv").read_bytes(), content)
            with (
                patch.object(client._opener, "open", side_effect=rejected) as opened,
                self.assertRaisesRegex(SafeApiError, "http_error"),
            ):
                client.download(operation, "rejected.csv", path=path, query=query)
            self.assertEqual(opened.call_count, 1)

            real_fdopen = os.fdopen

            def failing_fdopen(*args, **kwargs):
                stream = real_fdopen(*args, **kwargs)

                def fail(_data: bytes) -> None:
                    raise OSError("disk")

                stream.write = fail
                return stream

            with (
                patch.object(client._opener, "open", return_value=Response(content)),
                patch("user_research.client.os.fdopen", side_effect=failing_fdopen),
                self.assertRaisesRegex(SafeApiError, "output_write_failed"),
            ):
                client.download(operation, "partial.csv", path=path, query=query)
            self.assertFalse((Path(directory) / "partial.csv").exists())
            self.assertEqual(list(Path(directory).glob("*.partial")), [])

    def test_native_export_accepts_declared_jsonl_and_rejects_json_error_body(self) -> None:
        class Response(io.BytesIO):
            def __init__(self, value: bytes, content_type: str) -> None:
                super().__init__(value)
                self.headers = Message()
                self.headers["Content-Type"] = content_type

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                self.close()

        operation = Operation("project_voc_dataset_export")
        path = {
            "idea_id": "idea_" + "a" * 26,
            "voc_id": "ivoc_" + "b" * 26,
            "dataset_id": "dataset-001",
        }
        with tempfile.TemporaryDirectory() as directory:
            client = self.client(attachment_dir=directory)
            client._verified_context = CONTEXT
            target = Path(directory) / "dataset.jsonl"
            with patch.object(
                client._opener,
                "open",
                return_value=Response(b'{"review":"clear"}\n', "application/x-ndjson"),
            ):
                result = client.download(
                    operation, "dataset.jsonl", path=path, query={"format": "jsonl"}
                )
            self.assertEqual(result["content_type"], "application/x-ndjson")
            self.assertEqual(target.read_bytes(), b'{"review":"clear"}\n')
            rejected = Path(directory) / "error.jsonl"
            with (
                patch.object(
                    client._opener,
                    "open",
                    return_value=Response(b'{"detail":"provider error"}', "application/json"),
                ),
                self.assertRaisesRegex(SafeApiError, "invalid_attachment_content_type"),
            ):
                client.download(operation, "error.jsonl", path=path, query={"format": "jsonl"})
            self.assertFalse(rejected.exists())

    def test_attachment_output_stays_inside_host_sink(self) -> None:
        operation = Operation("personal_research_journey_responses_csv")
        path = {"research_id": "research_" + "b" * 26}
        query = {"form_id": "Form123"}
        outside = Path(tempfile.gettempdir()) / "audience-research-should-not-write.csv"
        try:
            outside.unlink(missing_ok=True)
        except OSError:
            pass
        with tempfile.TemporaryDirectory() as directory:
            sink = Path(directory) / "sink"
            sink.mkdir()
            linked = Path(directory) / "linked-sink"
            linked.symlink_to(sink)
            client = self.client(attachment_dir=str(sink))
            client._verified_context = CONTEXT
            missing = self.client()
            missing._verified_context = CONTEXT
            linked_client = self.client(attachment_dir=str(linked))
            linked_client._verified_context = CONTEXT
            with patch.object(missing._opener, "open") as opened:
                with self.assertRaisesRegex(SafeApiError, "invalid_attachment_dir"):
                    missing.download(operation, "responses.csv", path=path, query=query)
                opened.assert_not_called()
            with patch.object(linked_client._opener, "open") as opened:
                with self.assertRaisesRegex(SafeApiError, "invalid_attachment_dir"):
                    linked_client.download(operation, "responses.csv", path=path, query=query)
                opened.assert_not_called()
            cases = (
                str(outside),
                "../escape.csv",
                "nested/path.csv",
                "..",
                ".",
                "",
                "responses.csv\x00.txt",
            )
            for name in cases:
                with (
                    self.subTest(name=name),
                    patch.object(client._opener, "open") as opened,
                    self.assertRaisesRegex(SafeApiError, "invalid_output_path"),
                ):
                    client.download(operation, name, path=path, query=query)
                opened.assert_not_called()
                self.assertFalse(outside.exists())
            fifo = sink / "pipe.csv"
            os.mkfifo(fifo)

            class Response(io.BytesIO):
                def __init__(self, value: bytes) -> None:
                    super().__init__(value)
                    self.headers = Message()
                    self.headers["Content-Type"] = "text/csv"

                def __enter__(self):
                    return self

                def __exit__(self, *_args):
                    self.close()

            with patch.object(client._opener, "open", return_value=Response(b"a,b\n")) as opened:
                with self.assertRaisesRegex(SafeApiError, "output_exists"):
                    client.download(operation, "pipe.csv", path=path, query=query)
            opened.assert_called_once()


if __name__ == "__main__":
    unittest.main()
