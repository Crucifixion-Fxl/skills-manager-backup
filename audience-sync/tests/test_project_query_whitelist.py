"""The reference is public documentation, never a second authorization engine."""

import importlib.util
import json
import unittest
from pathlib import Path

from audience_sync.project_operations import ProjectOperation

ROOT = Path(__file__).resolve().parents[1]
GUIDE_ZH = json.loads((ROOT / "tests/fixtures/guide-zh-CN.json").read_text())
SPEC = importlib.util.spec_from_file_location(
    "whitelist_check", ROOT / "scripts/check_project_query_whitelist.py"
)
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)


def registry():
    return {
        "registry_version": "test-public-v1",
        "relations": {
            "audience_profile": {
                "physical_by_environment": {"staging": "private_table"},
                "datahub_urn": "private_urn",
                "tenant_column": "private_tenant",
                "fields": {
                    "country": {
                        "column": "private_column",
                        "value_type": "string",
                        "operators": ["eq", "in"],
                    }
                },
            }
        },
        "edges": {},
    }


class ProjectQueryWhitelistTests(unittest.TestCase):
    def test_projection_drops_physical_metadata_and_detects_operator_drift(self):
        source = registry()
        rendered = checker.render_inventory(source)
        self.assertNotIn("private", rendered)
        self.assertNotIn("physical_by_environment", rendered)
        self.assertIn("audience_profile.country", rendered)
        document = f"{checker.START}\n{rendered}\n{checker.END}"
        checker.check(document, source)
        source["relations"]["audience_profile"]["fields"]["country"]["operators"].append("gte")
        with self.assertRaisesRegex(ValueError, "differs"):
            checker.check(document, source)

    def test_join_expansion_requires_review_instead_of_silent_projection(self):
        source = registry()
        source["edges"] = {"new_edge": {"private_key": "private_value"}}
        with self.assertRaisesRegex(ValueError, "structure changed"):
            checker.render_inventory(source)

    def test_reference_has_complete_public_scalar_inventory_and_runtime_boundary(self):
        document = checker.REFERENCE.read_text(encoding="utf-8")
        table = checker.snapshot(document)
        rows = [line for line in table.splitlines() if line.startswith("| `audience_profile.")]
        self.assertEqual(len(rows), 26)
        self.assertEqual(len(set(rows)), 26)
        self.assertIn("Approved join edges: 0.", table)
        for forbidden in ("physical_by_environment", "datahub_urn", "tenant_id", "bundle_id"):
            self.assertNotIn(forbidden, table)
        self.assertIn(ProjectOperation.GET_QUERY_CAPABILITIES.value, document)
        self.assertIn(GUIDE_ZH["datahub_not_authorization"], document)
        self.assertIn(GUIDE_ZH["baseline_not_live_capability"], document)
