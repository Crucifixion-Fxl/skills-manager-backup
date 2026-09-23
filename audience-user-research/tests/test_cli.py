from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.parse import quote

from user_research.cli import main
from user_research.operations import (
    HOST_ATTACHMENT_OPERATIONS,
    OPERATION_SPECS,
    SELF_CONTEXT_OPERATION,
)

KEY = "awpk_v2_" + "a" * 26 + "_" + "B" * 43
CONTEXT = {
    "project_id": "kiwibit",
    "binding_revision": "pbr_" + "a" * 64,
    "credential_profile": "user_research",
    "allowed_actions": ["research.results.read"],
}


class CliTests(unittest.TestCase):
    def test_clone_direct_script_needs_no_install_or_credentials_for_capabilities(self) -> None:
        result = subprocess.run(
            [sys.executable, "scripts/api.py", "capabilities"],
            cwd=os.path.dirname(os.path.dirname(__file__)),
            env={},
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["capability_source"], "bundled_contracts")
        self.assertIn("get_project_personal_key_context", payload["operations"])
        self.assertIn("personal_research_journey_campaign_draft", payload["operations"])

    def test_cli_accepts_only_one_bounded_stdin_envelope(self) -> None:
        client = MagicMock()
        client.verify_self_context.return_value = CONTEXT
        client.call.return_value = {"project_id": "kiwibit"}
        request = {
            "path": {"research_id": "research_" + "b" * 26},
            "query": {"limit": "10"},
            "body": {},
        }
        with (
            patch("user_research.cli.AudienceClient", return_value=client),
            patch("user_research.cli.AudienceClientConfig.from_environment"),
            patch("sys.stdin", SimpleNamespace(buffer=BytesIO(json.dumps(request).encode()))),
            patch("builtins.print") as output,
        ):
            self.assertEqual(main(["personal_research_journey_responses", "--request-stdin"]), 0)
        client.verify_self_context.assert_called_once_with()
        self.assertEqual(client.call.call_args.kwargs["path"]["project_id"], "kiwibit")
        self.assertEqual(client.call.call_args.kwargs["query"], {"limit": "10"})
        self.assertIsNone(client.call.call_args.kwargs["body"])
        self.assertEqual(json.loads(output.call_args.args[0])["ok"], True)

    def test_responses_json_omits_email_uid_per_user_url_and_provider_payload(self) -> None:
        email = "alex.lee@example.com"
        uid = "cafebabeface0123456789abcdef0123"
        per_user_url = (
            "https://form.typeform.com/to/Form123#uid="
            + uid
            + "&research_id=research_"
            + "b" * 26
            + "&batch=batch-1"
        )
        provider_secret = "landing-id-must-not-leak"
        client = MagicMock()
        client.verify_self_context.return_value = CONTEXT
        client.call.return_value = {
            "project_id": "kiwibit",
            "binding_revision": CONTEXT["binding_revision"],
            "total": 1,
            "watermark": "wm-1",
            "items": [
                {
                    "response_id": "resp_keep_me",
                    "submitted_at": "2026-09-21T00:00:00Z",
                    "association_status": "matched",
                    "batch": "batch-1",
                    "uid": uid,
                    "profile": {"email": email, "country": "US"},
                    "answers": [
                        {"field": {"ref": "q1"}, "choice": {"label": "Yes"}},
                        {"email": email},
                        {"user_email": email},
                        {"recipient_email": email},
                        {"contact_email": email},
                        {"url": per_user_url},
                        {"link": "https://form.typeform.com/to/Form123?uid=" + uid},
                        {"provider_payload": {"landing_id": provider_secret}},
                    ],
                }
            ],
        }
        request = {
            "path": {"research_id": "research_" + "b" * 26},
            "query": {"form_id": "Form123", "limit": "10"},
            "body": {},
        }
        with (
            patch("user_research.cli.AudienceClient", return_value=client),
            patch("user_research.cli.AudienceClientConfig.from_environment"),
            patch("sys.stdin", SimpleNamespace(buffer=BytesIO(json.dumps(request).encode()))),
            patch("builtins.print") as output,
        ):
            self.assertEqual(main(["personal_research_journey_responses", "--request-stdin"]), 0)
        rendered = output.call_args.args[0]
        payload = json.loads(rendered)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["result"]["total"], 1)
        self.assertEqual(payload["result"]["items"][0]["response_id"], "resp_keep_me")
        self.assertEqual(payload["result"]["items"][0]["association_status"], "matched")
        self.assertIn("Yes", rendered)
        for secret in (email, uid, per_user_url, provider_secret, "provider_payload"):
            self.assertNotIn(secret, rendered)

    def test_unknown_email_recipient_and_relay_values_are_removed(self) -> None:
        email = "alex.lee@example.com"
        relay = "abcd@privaterelay.appleid.com"
        client = MagicMock()
        client.verify_self_context.return_value = CONTEXT
        client.call.return_value = {
            "project_id": "kiwibit",
            "binding_revision": CONTEXT["binding_revision"],
            "kept": "visible-marker",
            "hidden_contact": email,
            "relay_inbox": relay,
            "recipient": {"address": email, "name": "Alex"},
            "note": "Please email " + email + " about the feeder",
            "answers": [{"choice": {"label": "Yes"}}],
        }
        request = {"path": {}, "query": {}, "body": {}}
        with (
            patch("user_research.cli.AudienceClient", return_value=client),
            patch("user_research.cli.AudienceClientConfig.from_environment"),
            patch("sys.stdin", SimpleNamespace(buffer=BytesIO(json.dumps(request).encode()))),
            patch("builtins.print") as output,
        ):
            self.assertEqual(main(["personal_research_journey_responses", "--request-stdin"]), 0)
        rendered = output.call_args.args[0]
        self.assertIn("visible-marker", rendered)
        self.assertIn("Yes", rendered)
        self.assertIn("feeder", rendered)
        self.assertNotIn("recipient", rendered)
        for secret in (email, relay, "Alex"):
            self.assertNotIn(secret, rendered)

    def test_non_response_json_keeps_binding_matched_form_url(self) -> None:
        form_url = "https://form.typeform.com/to/Form123"
        client = MagicMock()
        client.verify_self_context.return_value = CONTEXT
        client.call.return_value = {
            "project_id": "kiwibit",
            "binding_revision": CONTEXT["binding_revision"],
            "research_id": "research_" + "b" * 26,
            "form_url": form_url,
        }
        request = {
            "path": {"research_id": "research_" + "b" * 26},
            "query": {},
            "body": {},
        }
        with (
            patch("user_research.cli.AudienceClient", return_value=client),
            patch("user_research.cli.AudienceClientConfig.from_environment"),
            patch("sys.stdin", SimpleNamespace(buffer=BytesIO(json.dumps(request).encode()))),
            patch("builtins.print") as output,
        ):
            self.assertEqual(main(["personal_research_journey_status", "--request-stdin"]), 0)
        rendered = output.call_args.args[0]
        self.assertIn(form_url, rendered)
        self.assertNotIn("#uid=", rendered)

    def test_encoded_uid_links_and_encoded_emails_are_removed(self) -> None:
        uid = "cafebabeface0123456789abcdef0123"
        form_url = "https://form.typeform.com/to/Form123"
        path_uid = "https://form.typeform.com/to/uid/Form123"
        encoded = "https://form.typeform.com/to/Form123%3Fuid%3D" + uid
        double_encoded = "https://form.typeform.com/to/Form123%253Fuid%253D" + uid
        fragment = "https://form.typeform.com/to/Form123%23uid%3D" + uid
        encoded_email = "alex.lee%40example.com"
        dotted_email = "alex.lee%40example%2ecom"
        deep_uid = "https://form.typeform.com/to/Form123?uid=" + uid
        for _ in range(6):
            deep_uid = quote(deep_uid, safe="")
        letter_email = "".join(f"%{ord(char):02X}" for char in "alex.lee@example.com")
        for _ in range(4):
            letter_email = quote(letter_email, safe="")
        client = MagicMock()
        client.verify_self_context.return_value = CONTEXT
        client.call.return_value = {
            "project_id": "kiwibit",
            "binding_revision": CONTEXT["binding_revision"],
            "research_id": "research_" + "b" * 26,
            "form_url": form_url,
            "editor": path_uid,
            "note": "feeder still works",
            "aside": "feeder alex.lee%40example.com remains",
            "choice": "Yes",
            "links": [
                encoded,
                double_encoded,
                fragment,
                encoded_email,
                dotted_email,
                deep_uid,
                letter_email,
            ],
        }
        request = {"path": {"research_id": "research_" + "b" * 26}, "query": {}, "body": {}}
        with (
            patch("user_research.cli.AudienceClient", return_value=client),
            patch("user_research.cli.AudienceClientConfig.from_environment"),
            patch("sys.stdin", SimpleNamespace(buffer=BytesIO(json.dumps(request).encode()))),
            patch("builtins.print") as output,
        ):
            self.assertEqual(main(["personal_research_journey_status", "--request-stdin"]), 0)
        rendered = output.call_args.args[0]
        self.assertIn(form_url, rendered)
        self.assertIn(path_uid, rendered)
        self.assertIn("feeder still works", rendered)
        self.assertIn("remains", rendered)
        self.assertIn("Yes", rendered)
        for secret in (
            uid,
            encoded,
            double_encoded,
            fragment,
            encoded_email,
            dotted_email,
            deep_uid,
            letter_email,
            "alex.lee",
            "example.com",
        ):
            self.assertNotIn(secret, rendered)

    def test_every_json_operation_omits_sensitive_stdout_fields(self) -> None:
        email = "alex.lee@example.com"
        uid = "cafebabeface0123456789abcdef0123"
        per_user_url = "https://form.typeform.com/to/Form123#uid=" + uid
        provider_secret = "landing-id-must-not-leak"
        form_url = "https://form.typeform.com/to/Form123"
        sensitive = {
            "project_id": "kiwibit",
            "kept": "visible-marker",
            "form_url": form_url,
            "email": email,
            "user_email": email,
            "recipient_email": email,
            "contact_email": email,
            "uid": uid,
            "profile": {"email": email, "country": "US"},
            "provider_payload": {"landing_id": provider_secret},
            "survey_url": per_user_url,
            "items": [
                {
                    "uid": uid,
                    "url": per_user_url,
                    "response_id": "resp_keep_me",
                    "answers": [
                        {"choice": {"label": "Yes"}},
                        {"email": email},
                        {"user_email": email},
                        {"link": "https://form.typeform.com/to/Form123?uid=" + uid},
                        {"provider_payload": {"landing_id": provider_secret}},
                    ],
                }
            ],
        }
        request = {"path": {}, "query": {}, "body": {}}
        for operation in OPERATION_SPECS:
            if operation in HOST_ATTACHMENT_OPERATIONS:
                continue
            client = MagicMock()
            client.verify_self_context.return_value = (
                {**CONTEXT, **sensitive} if operation == SELF_CONTEXT_OPERATION else CONTEXT
            )
            client.call.return_value = sensitive
            with (
                self.subTest(operation=operation.value),
                patch("user_research.cli.AudienceClient", return_value=client),
                patch("user_research.cli.AudienceClientConfig.from_environment"),
                patch("sys.stdin", SimpleNamespace(buffer=BytesIO(json.dumps(request).encode()))),
                patch("builtins.print") as output,
            ):
                self.assertEqual(main([operation.value, "--request-stdin"]), 0)
            rendered = output.call_args.args[0]
            self.assertIn("visible-marker", rendered)
            self.assertIn(form_url, rendered)
            self.assertNotIn("#uid=", rendered)
            for secret in (email, uid, provider_secret, "provider_payload", '"uid"'):
                self.assertNotIn(secret, rendered)

    def test_self_context_is_a_fixed_operation_and_not_bootstrapped_twice(self) -> None:
        client = MagicMock()
        client.verify_self_context.return_value = CONTEXT
        with (
            patch("user_research.cli.AudienceClient", return_value=client),
            patch("user_research.cli.AudienceClientConfig.from_environment"),
            patch("sys.stdin", SimpleNamespace(buffer=BytesIO(b"{}"))),
            patch("builtins.print") as output,
        ):
            self.assertEqual(main(["get_project_personal_key_context", "--request-stdin"]), 0)
        client.verify_self_context.assert_called_once_with()
        client.call.assert_not_called()
        self.assertEqual(json.loads(output.call_args.args[0])["result"], CONTEXT)

    def test_argv_cannot_carry_request_values(self) -> None:
        secret_value = "do-not-echo-this-project"
        result = subprocess.run(
            [sys.executable, "scripts/api.py", "personal_idea_list", "--path", secret_value],
            cwd=os.path.dirname(os.path.dirname(__file__)),
            env={},
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout), {"error": "invalid_arguments", "ok": False})
        self.assertNotIn(secret_value, result.stdout + result.stderr)

    def test_unbounded_or_injected_stdin_requests_fail_without_echo(self) -> None:
        cases = (
            ({"url": "https://evil.invalid"}, "invalid_arguments", 2),
            ({"headers": {"Authorization": KEY}}, "invalid_arguments", 2),
            ({"path": []}, "invalid_arguments", 2),
            ({"query": {"limit": 10}}, "invalid_query_parameters", 1),
        )
        for request, error, code in cases:
            with (
                self.subTest(error=error),
                patch("sys.stdin", SimpleNamespace(buffer=BytesIO(json.dumps(request).encode()))),
                patch("builtins.print") as output,
            ):
                self.assertEqual(main(["personal_research_journey_list", "--request-stdin"]), code)
                rendered = output.call_args.args[0]
                self.assertEqual(json.loads(rendered), {"error": error, "ok": False})
                self.assertNotIn(KEY, rendered)
                self.assertNotIn("evil.invalid", rendered)

    def test_stdin_size_is_bounded_before_configuration_or_network(self) -> None:
        with (
            patch("sys.stdin", SimpleNamespace(buffer=BytesIO(b"x" * (32 * 1024 + 1)))),
            patch("user_research.cli.AudienceClient") as client,
            patch("builtins.print") as output,
        ):
            self.assertEqual(main(["personal_idea_list", "--request-stdin"]), 1)
        client.assert_not_called()
        self.assertEqual(
            json.loads(output.call_args.args[0]), {"error": "request_too_large", "ok": False}
        )

    def test_attachment_operation_requires_output_and_prints_metadata_only(self) -> None:
        client = MagicMock()
        client.verify_self_context.return_value = CONTEXT
        client.download.return_value = {
            "content_type": "text/csv",
            "bytes": 42,
            "sha256": "a" * 64,
        }
        request = {
            "path": {"research_id": "research_" + "b" * 26},
            "query": {"form_id": "Form123"},
            "body": {},
        }
        with (
            patch("user_research.cli.AudienceClient", return_value=client),
            patch("user_research.cli.AudienceClientConfig.from_environment"),
            patch("sys.stdin", SimpleNamespace(buffer=BytesIO(json.dumps(request).encode()))),
            patch("builtins.print") as output,
        ):
            self.assertEqual(
                main(
                    [
                        "personal_research_journey_responses_csv",
                        "--request-stdin",
                        "--output",
                        "responses.csv",
                    ]
                ),
                0,
            )
        client.download.assert_called_once()
        self.assertEqual(client.download.call_args.args[1], "responses.csv")
        rendered = output.call_args.args[0]
        self.assertNotIn("response_id", rendered)
        self.assertEqual(json.loads(rendered)["result"]["bytes"], 42)

    def test_attachment_output_rejects_absolute_and_traversal(self) -> None:
        client = MagicMock()
        client.verify_self_context.return_value = CONTEXT
        request = {
            "path": {"research_id": "research_" + "b" * 26},
            "query": {"form_id": "Form123"},
            "body": {},
        }
        cases = (
            "/tmp/responses.csv",
            "../responses.csv",
            "nested/responses.csv",
            "..",
        )
        for name in cases:
            with (
                self.subTest(name=name),
                patch("user_research.cli.AudienceClient", return_value=client),
                patch("user_research.cli.AudienceClientConfig.from_environment"),
                patch("sys.stdin", SimpleNamespace(buffer=BytesIO(json.dumps(request).encode()))),
                patch("builtins.print") as output,
            ):
                self.assertEqual(
                    main(
                        [
                            "personal_research_journey_responses_csv",
                            "--request-stdin",
                            "--output",
                            name,
                        ]
                    ),
                    1,
                )
                self.assertEqual(
                    json.loads(output.call_args.args[0]),
                    {"error": "invalid_output_path", "ok": False},
                )
        client.download.assert_not_called()


if __name__ == "__main__":
    unittest.main()
