"""The Project Research bridge must not widen the general Project API surface."""

from __future__ import annotations

import io
import json
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from user_research import AudienceClient, AudienceClientConfig, Operation, SafeApiError
from user_research.allowlist import generate_operation_specs
from user_research.contract_validation import ContractViolation, _contract_version

ROOT = "/api/platform/v3/projects/{project_id}/research"
PERSONAL_KEY = "awpk_v2_" + "a" * 26 + "_" + "B" * 43
JOURNEY = ROOT + "/{research_id}/journey"


def document(path=JOURNEY, method="get", label="personal_research_journey_status"):
    return {
        "paths": {
            path: {
                method: {
                    "operationId": label,
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object"},
                                }
                            }
                        }
                    },
                }
            }
        }
    }


class ResearchJourneyTests(unittest.TestCase):
    def test_exact_research_route_is_allowed_but_scope_and_effect_injection_are_not(self):
        approved = document()
        spec = generate_operation_specs((approved,))["personal_research_journey_status"]
        assert (spec.method, spec.path, spec.response_mode) == ("GET", JOURNEY, "json")
        for path, method, label in (
            (JOURNEY, "post", "personal_research_journey_status"),
            (JOURNEY, "get", "send_campaign"),
            (JOURNEY + "/send", "post", "personal_research_journey_status"),
            (
                "/api/platform/v3/projects/{project_id}/audience-sync/queries",
                "post",
                "personal_research_journey_status",
            ),
            (
                "/api/platform/v3/projects/{project_id}/providers/typeform/forms",
                "post",
                "personal_research_journey_form",
            ),
        ):
            assert generate_operation_specs((document(path, method, label),)) == {}

    def test_new_operations_in_bundled_contract_are_not_automatically_granted(self):
        changed = deepcopy(document())
        changed["paths"][JOURNEY]["patch"] = changed["paths"][JOURNEY]["get"]
        specs = generate_operation_specs((changed,))
        assert len(specs) == 1
        assert next(iter(specs.values())).method == "GET"

    def test_contract_version_dispatch_is_closed(self):
        assert _contract_version(JOURNEY) == "v3"
        assert _contract_version("/api/platform/v2/products/{product_scope}/ideas") == "v2"
        with self.assertRaises(ContractViolation):
            _contract_version("/api/admin/v3/research")


class ResearchTransportTests(unittest.TestCase):
    def test_actual_list_dto_through_full_client_transport_and_contract(self):
        fixture = json.loads(
            (Path(__file__).parent / "fixtures" / "research_journey_http.json").read_text()
        )
        client = AudienceClient(
            AudienceClientConfig(
                base_url="https://audience-workflow-api-prod-us.addx.live",
                personal_api_key=PERSONAL_KEY,
            )
        )
        client._verified_context = {
            "project_id": "kiwibit",
            "binding_revision": fixture["binding_revision"],
            "allowed_actions": ["research.results.read"],
        }
        with patch.object(
            client._opener, "open", return_value=io.BytesIO(json.dumps(fixture).encode())
        ) as opened:
            result = client.call(
                Operation("personal_research_journey_list"), path={"project_id": "kiwibit"}
            )
        self.assertEqual(result, fixture)
        request = opened.call_args.args[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(
            request.full_url,
            "https://audience-workflow-api-prod-us.addx.live/api/platform/v3/projects/kiwibit/research",
        )
        with patch.object(
            client._opener, "open", return_value=io.BytesIO(json.dumps(fixture).encode())
        ) as opened:
            client.call(
                Operation("personal_research_journey_list"),
                path={"project_id": "kiwibit"},
                query={"limit": "1", "offset": "100"},
            )
        self.assertEqual(
            opened.call_args.args[0].full_url,
            "https://audience-workflow-api-prod-us.addx.live/api/platform/v3/projects/kiwibit/research?limit=1&offset=100",
        )
        with patch.object(client._opener, "open", return_value=io.BytesIO(b'{"items":[]}')):
            with self.assertRaisesRegex(SafeApiError, "invalid_response_schema"):
                client.call(
                    Operation("personal_research_journey_list"), path={"project_id": "kiwibit"}
                )

    def test_selection_rejects_sql_injection_before_transport(self):
        client = AudienceClient(
            AudienceClientConfig(
                base_url="https://audience-workflow-api-prod-us.addx.live",
                personal_api_key=PERSONAL_KEY,
            )
        )
        client._verified_context = {
            "project_id": "kiwibit",
            "binding_revision": "pbr_fixture",
            "allowed_actions": ["research.prepare"],
        }
        with patch.object(client._opener, "open") as opened:
            with self.assertRaisesRegex(SafeApiError, "invalid_request_schema"):
                client.call(
                    Operation("personal_research_prepare_selection"),
                    path={"project_id": "kiwibit", "research_id": "research_" + "a" * 26},
                    body={
                        "sql": "SELECT user_id FROM anything",
                        "idempotency_key": "selection-fixture",
                    },
                )
        opened.assert_not_called()

    def test_source_table_uses_same_fixed_selection_transport(self):
        source = {
            "table": "audience_staging.preselected_audience",
            "user_id_column": "user_id",
            "profile_columns": ["country"],
        }
        research = "research_" + "a" * 26
        response = {
            "project_id": "kiwibit",
            "binding_revision": "pbr_fixture",
            "research_id": research,
            "approved_selection_id": "rsel_fixture",
            "expected_count": 2,
            "partition_dt": "unpartitioned",
            "criteria_hash": "a" * 64,
            "sql_hash": "b" * 64,
            "registry_version": "source-table",
        }
        client = AudienceClient(
            AudienceClientConfig(
                base_url="https://audience-workflow-api-prod-us.addx.live",
                personal_api_key=PERSONAL_KEY,
            )
        )
        client._verified_context = {
            "project_id": "kiwibit",
            "binding_revision": "pbr_fixture",
            "allowed_actions": ["research.prepare"],
        }
        with patch.object(
            client._opener, "open", return_value=io.BytesIO(json.dumps(response).encode())
        ) as opened:
            result = client.call(
                Operation("personal_research_prepare_selection"),
                path={"project_id": "kiwibit", "research_id": research},
                body={"source": source, "idempotency_key": "source-fixture"},
            )
        self.assertEqual(result, response)
        request = opened.call_args.args[0]
        self.assertEqual(json.loads(request.data)["source"], source)
        self.assertEqual(request.get_method(), "POST")
        self.assertTrue(request.full_url.endswith("/research/" + research + "/selections"))

    def test_all_fifteen_fixed_operations_are_packaged(self):
        specs = generate_operation_specs()
        research = {name for name in specs if name.startswith("personal_research_")}
        self.assertEqual(len(research), 18)
        self.assertIn("personal_research_query_capabilities", research)
        self.assertIn("personal_research_prepare_selection", research)
        self.assertIn("personal_research_journey_form_publish", research)
        self.assertIn("personal_research_journey_idea_summary", research)
        self.assertIn("personal_research_journey_idea_summary_csv", research)
