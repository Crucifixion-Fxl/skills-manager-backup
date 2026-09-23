from __future__ import annotations

import json
import unittest

from audience_sync.allowlist import (
    generate_operation_specs,
    load_bundled_document,
    require_operation_inventory,
)
from audience_sync.operations import OPERATION_SPECS, Operation


class AllowlistTests(unittest.TestCase):
    def test_bundled_openapi_generates_the_exact_fixed_inventory(self) -> None:
        generated = generate_operation_specs()

        self.assertEqual(set(generated), {operation.value for operation in Operation})
        self.assertEqual(len(generated), 9)
        self.assertTrue(all(not hasattr(spec, "effect") for spec in generated.values()))
        self.assertEqual(
            generated["create_audience"].required_body,
            frozenset({"name", "product_scope", "audience_filter", "idempotency_key"}),
        )
        self.assertEqual(
            generated["get_sync_capabilities"].query_parameters,
            ("product_scope",),
        )
        self.assertEqual(
            generated["get_sync_capabilities"].required_query_parameters,
            frozenset({"product_scope"}),
        )
        self.assertEqual(
            {operation.value: spec for operation, spec in OPERATION_SPECS.items()},
            generated,
        )

    def test_generator_surfaces_an_unpublished_operation_for_inventory_guard(self) -> None:
        document = load_bundled_document()
        mutated = {**document, "paths": dict(document["paths"])}
        mutated["paths"]["/api/platform/v2/audience-sync/arbitrary"] = {
            "post": {
                "operationId": "arbitrary_request",
                "responses": {"204": {"description": "not allowed"}},
            }
        }

        generated = generate_operation_specs(mutated)

        self.assertIn("arbitrary_request", generated)
        with self.assertRaisesRegex(ValueError, "unexpected_operation_inventory"):
            require_operation_inventory(
                generated, frozenset(operation.value for operation in Operation)
            )

    def test_generator_rejects_an_unsupported_http_operation(self) -> None:
        document = load_bundled_document()
        mutated = {**document, "paths": dict(document["paths"])}
        mutated["paths"]["/api/platform/v2/audience-sync/arbitrary"] = {
            "delete": {
                "operationId": "delete_arbitrary",
                "responses": {"204": {"description": "not allowed"}},
            }
        }

        with self.assertRaisesRegex(ValueError, "unsupported_operation_method"):
            generate_operation_specs(mutated)

    def test_generator_preserves_optional_query_semantics(self) -> None:
        document = load_bundled_document()
        mutated = json.loads(json.dumps(document))
        operation = mutated["paths"][
            "/api/platform/v2/audience-sync/capabilities"
        ]["get"]
        operation["parameters"][0]["required"] = False

        generated = generate_operation_specs(mutated)
        spec = generated["get_sync_capabilities"]

        self.assertEqual(spec.query_parameters, ("product_scope",))
        self.assertEqual(spec.required_query_parameters, frozenset())


if __name__ == "__main__":
    unittest.main()
