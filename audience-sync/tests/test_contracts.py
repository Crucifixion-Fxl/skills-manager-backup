from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from audience_sync.operations import CONTRACT_REVISION, CONTRACT_STATE, OPERATION_SPECS
from audience_sync.provenance import ProvenanceError, load_and_verify

ROOT = Path(__file__).resolve().parents[1]
GUIDE_ZH = json.loads((ROOT / "tests/fixtures/guide-zh-CN.json").read_text())


def _request_schema(openapi: dict, operation: dict) -> dict:
    reference = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    return openapi["components"]["schemas"][reference.rsplit("/", 1)[-1]]


def _request_schema_graph(openapi: dict, operation: dict) -> str:
    pending = [_request_schema(openapi, operation)]
    visited: set[str] = set()
    serialized: list[str] = []
    while pending:
        schema = pending.pop()
        encoded = json.dumps(schema, sort_keys=True)
        serialized.append(encoded)
        for reference in schema.get("properties", {}).values():
            for candidate in (reference, *reference.get("anyOf", [])):
                name = candidate.get("$ref", "").rsplit("/", 1)[-1]
                if name and name not in visited:
                    visited.add(name)
                    pending.append(openapi["components"]["schemas"][name])
    return "\n".join(serialized)


class ContractTests(unittest.TestCase):
    def test_machine_contract_has_no_local_effect_policy_metadata(self) -> None:
        openapi = json.loads((ROOT / "contracts/audience-sync-v2.openapi.json").read_text())
        registry = json.loads((ROOT / "contracts/operation-registry.json").read_text())
        ci = (ROOT / ".gitlab-ci.yml").read_text(encoding="utf-8")

        self.assertNotIn("x-audience-sync-effects-default", openapi)
        self.assertTrue(all("effect" not in operation for operation in registry["operations"]))
        self.assertNotIn("published_effects_default_dark", ci)
        self.assertIn('assert "effects_available" not in local', ci)
        self.assertIn('assert "effects_require_confirmation" not in local', ci)

    def test_project_materialization_prompt_matches_required_evidence(self) -> None:
        prompt = (ROOT / "agents/openai.yaml").read_text()
        registry = json.loads((ROOT / "contracts/project-operation-registry.json").read_text())
        operation = next(
            o for o in registry["operations"] if o["name"] == "materialize_audience_query"
        )
        for field in ("preview_attestation_id", "expected_member_count"):
            self.assertIn(field, operation["required_body"])
            self.assertIn(field, prompt)

    def test_readme_distinguishes_semantic_host_from_full_client(self) -> None:
        readme = (ROOT / "README.md").read_text()
        for marker in ("semantic-only", "native tools", "does not", "Python 3.9+", "source.json"):
            self.assertIn(marker, readme)

    def test_source_lock_hashes_every_declared_file(self) -> None:
        source = load_and_verify(ROOT)
        self.assertEqual(source["semantic_owner"]["repository"], "lli/audience-sync-skill")
        self.assertEqual(source["contract_source"]["repository"], "lli/audience-sync-skill")
        self.assertEqual(source["contract_source"]["release"], "0.8.1")
        self.assertNotIn("revision", source["contract_source"])
        self.assertEqual(source["contract_source"]["contract_revision"], CONTRACT_REVISION)
        self.assertEqual(source["contract_source"]["availability"], CONTRACT_STATE)
        self.assertEqual(
            source["openapi_upstream"],
            {
                "repository": "services/audiences",
                "revision": "53097e58faf98280e08560ef7361a5e1af3b140c",
                "path": "audience-workflow/api/audience-sync-v2.openapi.json",
                "sha256": ("a1720465b316b19158bcffb4e4525af5e522762868a98db79c5c062d168c0f33"),
            },
        )
        self.assertEqual(
            source["openapi_bundle"],
            {
                "sha256": ("3382b67e825563282aec68bf9a20db7f4e21667e7e50ee3f8fd7d0475c26e4fe"),
                "derived_from_upstream_sha256": source["openapi_upstream"]["sha256"],
                "transformation": "remove_local_effect_policy_metadata",
                "removed_policy_markers": [
                    "info.description default-dark qualifier",
                    "x-audience-sync-effects-default",
                ],
            },
        )
        self.assertEqual(
            source["openapi_bundle"]["sha256"],
            source["files"]["contracts/audience-sync-v2.openapi.json"],
        )
        self.assertNotIn("contracts/source.json", source["files"])
        self.assertEqual(
            subprocess.run(
                [sys.executable, "scripts/update_source_lock.py", "--check"],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            ).returncode,
            0,
        )
        self.assertEqual(
            subprocess.run(
                [sys.executable, "scripts/update_operation_registry.py", "--check"],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            ).returncode,
            0,
        )

    def test_registry_matches_published_openapi_exactly(self) -> None:
        registry = json.loads((ROOT / "contracts/operation-registry.json").read_text())
        openapi = json.loads((ROOT / "contracts/audience-sync-v2.openapi.json").read_text())
        openapi_operations = {}
        openapi_shapes = {}
        for path, path_item in openapi["paths"].items():
            for method, operation in path_item.items():
                name = operation["operationId"]
                path_parameters = sorted(
                    item["name"] for item in operation.get("parameters", []) if item["in"] == "path"
                )
                query_parameters = sorted(
                    item["name"]
                    for item in operation.get("parameters", [])
                    if item["in"] == "query"
                )
                required_body: list[str] = []
                optional_body: list[str] = []
                if "requestBody" in operation:
                    schema = _request_schema(openapi, operation)
                    required_body = sorted(schema.get("required", []))
                    optional_body = sorted(set(schema.get("properties", {})) - set(required_body))
                openapi_operations[name] = (method.upper(), path)
                openapi_shapes[name] = (
                    path_parameters,
                    query_parameters,
                    sorted(
                        item["name"]
                        for item in operation.get("parameters", [])
                        if item["in"] == "query" and item.get("required") is True
                    ),
                    required_body,
                    optional_body,
                )

        source_operations = {
            operation.value: (spec.method, spec.path) for operation, spec in OPERATION_SPECS.items()
        }
        registry_operations = {
            item["name"]: (item["method"], item["path"]) for item in registry["operations"]
        }
        registry_shapes = {
            item["name"]: (
                sorted(item["path_parameters"]),
                sorted(item["query_parameters"]),
                sorted(item["required_query_parameters"]),
                sorted(item["required_body"]),
                sorted(item["optional_body"]),
            )
            for item in registry["operations"]
        }
        source_shapes = {
            operation.value: (
                sorted(spec.path_parameters),
                sorted(spec.query_parameters),
                sorted(spec.required_query_parameters),
                sorted(spec.required_body),
                sorted(spec.optional_body),
            )
            for operation, spec in OPERATION_SPECS.items()
        }
        self.assertEqual(openapi_operations, source_operations)
        self.assertEqual(registry_operations, source_operations)
        self.assertEqual(openapi_shapes, registry_shapes)
        self.assertEqual(registry_shapes, source_shapes)
        self.assertEqual(registry["contract_revision"], CONTRACT_REVISION)
        self.assertEqual(registry["contract_state"], CONTRACT_STATE)
        self.assertEqual(len(source_operations), 9)

    def test_project_reference_covers_every_registry_shape(self) -> None:
        reference = (ROOT / "references/platform-api.md").read_text()
        registry = json.loads((ROOT / "contracts/project-operation-registry.json").read_text())
        for operation in registry["operations"]:
            with self.subTest(operation=operation["name"]):
                line = next(
                    line
                    for line in reference.splitlines()
                    if line.startswith("| `" + operation["name"] + "`")
                )
                self.assertIn(f"`{operation['method']} {operation['path']}`", line)
                for field in operation["required_body"]:
                    self.assertIn(f"`{field}`", line)

    def test_publishing_entry_has_required_sections_and_trigger(self) -> None:
        skill = (ROOT / "SKILL.md").read_text()
        for section in ("## Description", "## Rules", "## Examples", "### Good", "### Bad"):
            self.assertIn(section, skill)
        description = next(line for line in skill.splitlines() if line.startswith("description:"))
        self.assertIn(GUIDE_ZH["description_trigger"], description)
        self.assertIn(GUIDE_ZH["description_permissions"], description)

    def test_semantic_guide_uses_host_schema_without_private_contract_download(self) -> None:
        for name in ("platform-api.md", "project-query.md", "audience-filter.md"):
            guide = (ROOT / "references" / name).read_text()
            with self.subTest(reference=name):
                self.assertIn(GUIDE_ZH["native_host"], guide)
                self.assertIn("schema", guide)
                self.assertIn(GUIDE_ZH["full_clone"], guide)
                self.assertNotIn("https://gitlab.addx.ai/", guide)

    def test_source_owner_metadata_preserves_canonical_identity(self) -> None:
        metadata_path = ROOT / "contracts/semantic-owner.json"
        metadata = json.loads(metadata_path.read_text())
        lock = json.loads((ROOT / "contracts/source.json").read_text())
        self.assertEqual(lock["semantic_owner"], metadata)
        self.assertEqual(metadata["repository"], "lli/audience-sync-skill")
        self.assertEqual(metadata["availability"], "canonical_git")
        self.assertEqual(set(metadata), {"repository", "origin", "availability"})
        self.assertIn("contracts/semantic-owner.json", lock["files"])

    def test_source_owner_drift_and_invalid_generation_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lock_bytes = (ROOT / "contracts/source.json").read_bytes()
            lock = json.loads(lock_bytes)
            for relative in (*lock["files"], "contracts/source.json"):
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / relative, target)
            metadata_path = root / "contracts/semantic-owner.json"
            owner = json.loads(metadata_path.read_text())
            metadata_path.write_text(json.dumps({**owner, "availability": "changed"}))
            with self.assertRaisesRegex(ProvenanceError, "source_drift"):
                load_and_verify(root)
            for payload in (None, "{invalid", "[]", '{"repository": "incomplete"}'):
                with self.subTest(payload=payload):
                    if payload is None:
                        metadata_path.unlink()
                    else:
                        metadata_path.write_text(payload)
                    result = subprocess.run(
                        [sys.executable, str(root / "scripts/update_source_lock.py")],
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    self.assertEqual(result.returncode, 1)
                    self.assertEqual(result.stdout.strip(), "source_lock_generation_failed")
                    self.assertEqual(result.stderr, "")
                    self.assertEqual((root / "contracts/source.json").read_bytes(), lock_bytes)

    def test_eval_cases_separate_readonly_comparison_from_single_effect(self) -> None:
        data = json.loads((ROOT / "evals/evals.json").read_text())
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(data["skill_name"], "audience-sync")
        cases = data["evals"]
        self.assertEqual(len({case["id"] for case in cases}), len(cases))
        self.assertEqual(data["contract"], {
            "version": 1,
            "id_start": 1,
            "case_count": 7,
            "canonical_inputs": True,
            "require_assertions": True,
            "hide_tool_tokens": True,
        })
        self.assertEqual([case["id"] for case in cases], list(range(1, 8)))
        self.assertEqual([case["slug"] for case in cases], [
            "mixed-key-bootstrap",
            "native-host-no-python",
            "missing-transport",
            "authorized-production-full-chain",
            "business-preview-reply",
            "business-sync-reply",
            "business-pending-and-diagnostics",
        ])
        self.assertEqual(sum(c["mode"] == "read_only_comparison" for c in cases), 6)
        effects = [c for c in cases if c["mode"] == "single_authorized_effect"]
        self.assertEqual(len(effects), 1)
        for case in cases:
            self.assertTrue(case["prompt"])
            self.assertTrue(case["setup"])
            self.assertTrue(case["expected_output"])
            self.assertTrue(case["assertions"])
            self.assertNotIn("expected_outcomes", case)
            for assertion in case["assertions"]:
                self.assertTrue(assertion["check"].strip())
            self.assertNotIn("passed", case)
        self.assertEqual(effects[0]["slug"], "authorized-production-full-chain")
        effect_checks = " ".join(a["check"] for a in effects[0]["assertions"])
        for safety_requirement in (
            "if count differs or exceeds5",
            "without materializing or syncing",
            "confirmed=true",
            "reuse existing user authorization",
            "exact sync request",
            "duplicated effects for baseline evaluation",
        ):
            self.assertIn(safety_requirement, effect_checks)
        by_slug = {case["slug"]: case for case in cases}
        sync_checks = " ".join(
            a["check"] for a in by_slug["business-sync-reply"]["assertions"]
        )
        self.assertIn("IDs inside returned URLs may remain intact", sync_checks)
        self.assertIn("Show each supplied validated Audience/List URL independently", sync_checks)
        self.assertIn("destination.target.display_name", sync_checks)
        for case in cases:
            if case["mode"] == "read_only_comparison":
                self.assertIn("no effects are authorized", case["expected_output"])
        effect = json.dumps(effects[0], ensure_ascii=False)
        self.assertIn("only the Skill/full clone", effects[0]["setup"])
        self.assertIn("No supplied Project, origin, API route", effects[0]["setup"])
        self.assertIn("No host capability preflight", effects[0]["setup"])
        self.assertIn("advertised unambiguous ready Brevo Folder", effects[0]["setup"]
                      + " ".join(a["check"] for a in effects[0]["assertions"]))
        self.assertNotIn("trusted target Project", json.dumps(data))
        for marker in ("2026-09-07", "UTC", "5", "confirmed=true", "exact", "No campaign"):
            self.assertIn(marker, effect)
        for prefix in ("awpk_v2_", "aqp_", "amrq_", "asrq_"):
            self.assertNotIn(prefix, json.dumps(data))

    def test_active_guidance_is_production_only(self) -> None:
        for path in (
            ROOT / "SKILL.md",
            ROOT / "agents/openai.yaml",
            *sorted((ROOT / "references").glob("*.md")),
        ):
            with self.subTest(path=path.name):
                self.assertNotIn("staging", path.read_text().lower())

    def test_runtime_reference_supplies_production_and_live_catalog(self) -> None:
        skill = (ROOT / "SKILL.md").read_text()
        host = (ROOT / "references/host-configuration.md").read_text()
        profile = (ROOT / "references/audience-profile-selection.md").read_text()
        self.assertIn(GUIDE_ZH["host_reference"], skill)
        self.assertIn("https://audience-workflow-api-prod-us.addx.live", host)
        self.assertIn("Authorization: Bearer <AUDIENCE_SYNC_API_KEY>", host)
        self.assertIn("get_query_capabilities", profile)
        self.assertIn(GUIDE_ZH["advertised_value_type"], profile)

    def test_project_guide_uses_advertised_folder_semantics(self) -> None:
        guide = (ROOT / "references/project-query.md").read_text()
        for marker in (
            "kind=brevo",
            "target_type=folder",
            "destination_revision",
            "brevo_sync_available",
        ):
            self.assertIn(marker, guide)

    def test_active_semantics_do_not_publish_retired_operations(self) -> None:
        registry = json.loads((ROOT / "contracts/operation-registry.json").read_text())
        semantic = "\n".join(
            path.read_text()
            for path in (
                ROOT / "SKILL.md",
                ROOT / "agents/openai.yaml",
                ROOT / "README.md",
                *sorted((ROOT / "references").glob("*.md")),
            )
        )
        for operation in registry["operations"]:
            if operation["name"] == "get_sync_capabilities":
                # This name is also the installed native alias for the Project endpoint.
                self.assertEqual(semantic.count("`get_sync_capabilities`"), 1)
                continue
            self.assertNotIn("`" + operation["name"] + "`", semantic)
        for marker in ("audience-sync-v2", "awpk_v1", "Product Scope", "/api/platform/v2/"):
            self.assertNotIn(marker, semantic)

    def test_runtime_bootstrap_uses_bundled_or_native_transport(self) -> None:
        host = (ROOT / "references/host-configuration.md").read_text()
        for marker in (
            GUIDE_ZH["native_tools"],
            GUIDE_ZH["semantic_only"],
            "Python 3.9+",
            GUIDE_ZH["stdlib_runtime"],
            GUIDE_ZH["absolute_script_paths"],
            GUIDE_ZH["reject_redirects"],
        ):
            self.assertIn(marker, host)
        self.assertIn("get_project_sync_capabilities", host)
        self.assertIn("get_sync_capabilities", host)
        self.assertIn("/api/platform/v3/projects/{project_id}/audience-sync/capabilities", host)
        self.assertIn(GUIDE_ZH["native_endpoint_schema"], host)
        self.assertIn("AUDIENCE_SYNC_API_KEY", host)
        self.assertIn(GUIDE_ZH["no_runtime_install"], host)
        self.assertNotIn("pip install", host)

    def test_project_validation_example_uses_live_registry_placeholder(self) -> None:
        guide = (ROOT / "references/project-query.md").read_text()
        self.assertIn('"registry_version":"<live registry_version>"', guide)
        self.assertIn(GUIDE_ZH["live_registry_value"], guide)
        self.assertNotIn('"registry_version":"audience-query-v1-profile"', guide)

    def test_project_guide_preserves_evidence_without_new_workflow_gates(self) -> None:
        skill = (ROOT / "SKILL.md").read_text()
        self.assertIn(GUIDE_ZH["existing_evidence"], skill)
        self.assertIn(GUIDE_ZH["schema_and_live_gates"], skill)
        for marker in ("current_stage", "next_stage", "transition_id", "continuation_token"):
            self.assertNotIn(marker, skill)

    def test_preview_is_optional_and_materialization_uses_hash_cas_without_filter(self) -> None:
        registry = json.loads((ROOT / "contracts/operation-registry.json").read_text())
        materialize = next(
            item for item in registry["operations"] if item["name"] == "materialize_audience"
        )
        self.assertNotIn("audience_filter", materialize["required_body"])
        self.assertEqual(
            set(materialize["required_body"]),
            {"expected_audience_filter_hash", "idempotency_key", "product_scope"},
        )

    def test_readonly_checks_reuse_authorized_scope(self) -> None:
        confirmation = (ROOT / "references/confirmations.md").read_text()
        self.assertIn(GUIDE_ZH["existing_authorization"], confirmation)
        self.assertIn(GUIDE_ZH["changed_inputs"], confirmation)
        self.assertIn(
            GUIDE_ZH["readonly_no_extra_approval"],
            confirmation,
        )
        self.assertIn("confirmed=true", confirmation)

    def test_create_is_server_owned_and_existing_edits_are_safe(self) -> None:
        registry = json.loads((ROOT / "contracts/operation-registry.json").read_text())
        operation_names = [item["name"] for item in registry["operations"]]
        self.assertEqual(len(operation_names), 9)
        self.assertEqual(operation_names.count("create_audience"), 1)
        self.assertEqual(operation_names.count("save_audience"), 1)

        create = next(item for item in registry["operations"] if item["name"] == "create_audience")
        save = next(item for item in registry["operations"] if item["name"] == "save_audience")
        self.assertEqual(create["method"], "POST")
        self.assertEqual(create["path_parameters"], [])
        self.assertNotIn("audience_id", create["required_body"])
        self.assertEqual(save["path_parameters"], ["audience_id"])
        self.assertIn("expected_audience_filter_hash", save["required_body"])
        openapi = json.loads((ROOT / "contracts/audience-sync-v2.openapi.json").read_text())
        expected_hash = openapi["components"]["schemas"]["AudienceSyncSaveRequest"]["properties"][
            "expected_audience_filter_hash"
        ]
        self.assertEqual(expected_hash["type"], "string")
        self.assertNotIn("anyOf", expected_hash)

    def test_authoritative_filter_response_is_closed_and_typed(self) -> None:
        openapi = json.loads((ROOT / "contracts/audience-sync-v2.openapi.json").read_text())
        summary = openapi["components"]["schemas"]["AudienceSyncAudienceSummary"]
        authoritative = openapi["components"]["schemas"]["AudienceSyncAuthoritativeAudience"]
        audience_filter = summary["properties"]["audience_filter"]
        self.assertIn(
            {"$ref": "#/components/schemas/AudienceSyncFilter"},
            audience_filter["anyOf"],
        )
        self.assertFalse(summary["additionalProperties"])
        self.assertIn("audience_detail_url", summary["properties"])
        self.assertNotIn("audience_detail_url", summary["required"])
        detail_property = summary["properties"]["audience_detail_url"]
        detail_url = detail_property["anyOf"][0]
        self.assertEqual(detail_property["format"], "uri")
        self.assertEqual(detail_url["pattern"], r"^https://[^\s?#]+$")
        self.assertFalse(authoritative["additionalProperties"])
        self.assertEqual(
            set(authoritative["required"]),
            {
                "audience_id",
                "name",
                "product_scope",
                "audience_filter",
                "audience_filter_hash",
            },
        )

    def test_no_obsolete_or_direct_provider_contract_is_published(self) -> None:
        names = {path.name for path in (ROOT / "contracts").iterdir()}
        self.assertNotIn("audience-sync-v1-draft.openapi.json", names)
        governed = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (
                ROOT / "SKILL.md",
                ROOT / "references/platform-api.md",
                ROOT / "src/audience_sync/operations.py",
                ROOT / "contracts/operation-registry.json",
            )
        )
        self.assertNotIn("/api/platform/v1/", governed)
        self.assertNotIn("sync_audience_to_brevo", governed)
        self.assertNotIn("api.brevo.com", governed)
        self.assertNotIn("audience-sync-v1-draft", governed)

    def test_agent_facing_contract_exposes_only_typed_provider_navigation(self) -> None:
        openapi = json.loads((ROOT / "contracts/audience-sync-v2.openapi.json").read_text())
        serialized = json.dumps(openapi).lower()
        self.assertNotIn('"list_id"', serialized)
        self.assertNotIn('"list_name"', serialized)
        self.assertNotIn('"folder_id"', serialized)
        self.assertNotIn('"external_url"', serialized)
        status = openapi["components"]["schemas"]["AudienceSyncStatus"]
        self.assertIn("provider_resource_url", status["properties"])
        self.assertNotIn("provider_resource_url", status["required"])
        resource_property = status["properties"]["provider_resource_url"]
        resource_url = resource_property["anyOf"][0]
        self.assertEqual(resource_property["format"], "uri")
        self.assertEqual(resource_url["maxLength"], 2048)
        self.assertEqual(
            resource_url["pattern"],
            r"^https://[^\s@/?#]+(?:[/?#][^\s]*)?$",
        )

        for path_item in openapi["paths"].values():
            path_parameters = path_item.get("parameters", [])
            for parameter in path_parameters:
                self.assertNotEqual(parameter.get("name"), "provider_resource_url")
                self.assertNotIn("provider_resource_url", json.dumps(parameter))
            for method, operation in path_item.items():
                if method == "parameters":
                    continue
                for parameter in operation.get("parameters", []):
                    self.assertIn(parameter.get("in"), {"path", "query", "header"})
                    self.assertNotEqual(parameter.get("name"), "provider_resource_url")
                    self.assertNotIn("provider_resource_url", json.dumps(parameter))
                if "requestBody" not in operation:
                    continue
                request_schema = _request_schema(openapi, operation)
                self.assertNotIn("provider_resource_url", request_schema.get("properties", {}))
                self.assertNotIn("provider_resource_url", _request_schema_graph(openapi, operation))

    def test_legacy_success_link_schema_remains_compatible(self) -> None:
        openapi = json.loads((ROOT / "contracts/audience-sync-v2.openapi.json").read_text())
        schemas = openapi["components"]["schemas"]
        self.assertIn(
            "audience_detail_url",
            schemas["AudienceSyncAuthoritativeAudience"]["properties"],
        )
        self.assertNotIn(
            "audience_detail_url",
            schemas["AudienceSyncMaterialization"]["properties"],
        )
        self.assertNotIn(
            "audience_detail_url",
            schemas["AudienceSyncStatus"]["properties"],
        )
        self.assertIn(
            "provider_resource_url",
            schemas["AudienceSyncStatus"]["properties"],
        )
        materialization_required = set(schemas["AudienceSyncMaterializationResponse"]["required"])
        self.assertTrue({"audience_id", "product_scope"}.issubset(materialization_required))
        sync_required = set(schemas["AudienceSyncStatus"]["required"])
        self.assertTrue({"audience_id", "product_scope"}.isdisjoint(sync_required))

    def test_project_result_links_are_returned_not_constructed(self) -> None:
        guide = (ROOT / "references/platform-api.md").read_text()
        self.assertIn(GUIDE_ZH["successful_response_bindings"], guide)
        self.assertIn(GUIDE_ZH["no_constructed_urls"], guide)
        for route in ("app.brevo.com/contact/list/id/", "admin.mailchimp.com/lists/"):
            self.assertNotIn(route, guide)

    def test_audience_filter_is_closed(self) -> None:
        openapi = json.loads((ROOT / "contracts/audience-sync-v2.openapi.json").read_text())
        schema = openapi["components"]["schemas"]["AudienceSyncFilter"]
        self.assertFalse(schema["additionalProperties"])
        self.assertNotIn("sql", schema["properties"])
        self.assertEqual(
            set(schema["properties"]),
            {
                "active_within_days",
                "app_score_max",
                "app_score_min",
                "cities",
                "countries",
                "cuids",
                "device_quantity_max",
                "device_quantity_min",
                "device_share_types",
                "device_types",
                "email_domains",
                "feeder_device_user",
                "first_bind_serial_numbers",
                "first_bound_at_from",
                "first_bound_at_to",
                "first_purchase_at_from",
                "first_purchase_at_to",
                "free_trial",
                "languages",
                "last_purchase_at_from",
                "last_purchase_at_to",
                "materialization_limit",
                "paid_amount_max",
                "paid_amount_min",
                "paid_count_max",
                "paid_count_min",
                "paid_user",
                "platforms",
                "refund_amount_max",
                "refund_amount_min",
                "refund_count_max",
                "refund_count_min",
                "registered_at_from",
                "registered_at_to",
                "registered_only",
                "serial_numbers",
                "sku_ids",
                "sku_names",
                "subscription_statuses",
                "tenants",
                "tier_service_types",
                "timezones",
            },
        )

    def test_structured_criteria_guide_matches_project_schema(self) -> None:
        guide = (ROOT / "references/audience-filter.md").read_text()
        schema = json.loads((ROOT / "contracts/project-control-plane.openapi.json").read_text())
        serialized = json.dumps(schema)
        for field in (
            "schema_version",
            "registry_version",
            "where",
            "relation_id",
            "field_id",
            "operator",
        ):
            self.assertIn(field, guide)
            self.assertIn('"' + field + '"', serialized)
        self.assertIn(GUIDE_ZH["unsupported_condition"], guide)
        self.assertIn(GUIDE_ZH["no_silent_predicate_drop"], guide)

    def test_project_profile_reference_is_locked_and_logical(self) -> None:
        source = load_and_verify(ROOT)
        self.assertIn("references/audience-profile-selection.md", source["files"])
        guide = (ROOT / "references/audience-profile-selection.md").read_text()
        for marker in ("get_query_capabilities", GUIDE_ZH["advertised_value_type"],
                       GUIDE_ZH["onboarding_proposal"]):
            self.assertIn(marker, guide)
        for marker in ("SELECT ", "FROM ", "_min`", "_max`", "dws_user_"):
            self.assertNotIn(marker, guide)

    def test_profile_guide_distinguishes_meaning_types_and_live_edges(self) -> None:
        guide = (ROOT / "references/audience-profile-selection.md").read_text()
        for marker in (
            GUIDE_ZH["cancellation_semantics"],
            GUIDE_ZH["no_semantic_substitution"],
            GUIDE_ZH["preserve_string_type"],
            GUIDE_ZH["timezone"],
            GUIDE_ZH["edge_and_quantifier"],
            GUIDE_ZH["no_discovery_authority"],
        ):
            self.assertIn(marker, guide)

    def test_source_lock_is_not_self_referential(self) -> None:
        source = json.loads((ROOT / "contracts/source.json").read_text())
        for relative, expected in source["files"].items():
            actual = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
            self.assertEqual(actual, expected, relative)


if __name__ == "__main__":
    unittest.main()
