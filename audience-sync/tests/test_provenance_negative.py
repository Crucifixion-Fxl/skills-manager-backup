from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from audience_sync.openapi_lineage import OpenApiLineageError, verify_upstream_lineage
from audience_sync.provenance import ProvenanceError, load_and_verify

ROOT = Path(__file__).resolve().parents[1]
OPENAPI_RELATIVE = "contracts/audience-sync-v2.openapi.json"
OPENAPI_UPSTREAM = {
    "repository": "services/audiences",
    "revision": "53097e58faf98280e08560ef7361a5e1af3b140c",
    "path": "audience-workflow/api/audience-sync-v2.openapi.json",
    "sha256": "a1720465b316b19158bcffb4e4525af5e522762868a98db79c5c062d168c0f33",
}
OPENAPI_BUNDLE = {
    "sha256": "3382b67e825563282aec68bf9a20db7f4e21667e7e50ee3f8fd7d0475c26e4fe",
    "derived_from_upstream_sha256": OPENAPI_UPSTREAM["sha256"],
    "transformation": "remove_local_effect_policy_metadata",
    "removed_policy_markers": [
        "info.description default-dark qualifier",
        "x-audience-sync-effects-default",
    ],
}


def _source(root: Path, files: dict[str, str]) -> dict[str, object]:
    openapi = root / OPENAPI_RELATIVE
    openapi.write_bytes((ROOT / OPENAPI_RELATIVE).read_bytes())
    project_path = "contracts/project-control-plane.openapi.json"
    (root / project_path).write_bytes((ROOT / project_path).read_bytes())
    project_upstream = json.loads((ROOT / "contracts/source.json").read_text())[
        "project_openapi_upstream"
    ]
    return {
        "openapi_upstream": OPENAPI_UPSTREAM,
        "openapi_bundle": OPENAPI_BUNDLE,
        "project_openapi_upstream": project_upstream,
        "files": {
            OPENAPI_RELATIVE: OPENAPI_BUNDLE["sha256"],
            project_path: project_upstream["sha256"],
            **files,
        },
    }


class ProvenanceNegativeTests(unittest.TestCase):
    def test_project_contract_rehash_cannot_change_published_origin(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "contracts").mkdir()
            source = _source(root, {})
            relative = "contracts/project-control-plane.openapi.json"
            (root / relative).write_text("{}\n")
            altered = hashlib.sha256((root / relative).read_bytes()).hexdigest()
            source["files"][relative] = altered
            source["project_openapi_upstream"]["sha256"] = altered
            (root / "contracts/source.json").write_text(json.dumps(source))
            with self.assertRaisesRegex(ProvenanceError, "invalid_source_lock"):
                load_and_verify(root)

    def test_policy_neutralized_bundle_mechanically_reconstructs_upstream(self) -> None:
        bundle = (ROOT / OPENAPI_RELATIVE).read_bytes()
        self.assertEqual(
            verify_upstream_lineage(bundle, OPENAPI_UPSTREAM["sha256"]),
            OPENAPI_BUNDLE["sha256"],
        )

    def test_reconstruction_rejects_additional_route_or_schema_drift(self) -> None:
        bundle = json.loads((ROOT / OPENAPI_RELATIVE).read_text(encoding="utf-8"))
        mutations = []
        route_drift = json.loads(json.dumps(bundle))
        route_drift["paths"]["/unexpected"] = {}
        mutations.append(route_drift)
        schema_drift = json.loads(json.dumps(bundle))
        schema_drift["components"]["schemas"]["ProductScope"]["enum"].append("other")
        mutations.append(schema_drift)

        for mutated in mutations:
            payload = (json.dumps(mutated, indent=2) + "\n").encode()
            with self.assertRaisesRegex(OpenApiLineageError, "upstream_lineage_mismatch"):
                verify_upstream_lineage(payload, OPENAPI_UPSTREAM["sha256"])

    def test_runtime_verifier_rejects_rehashed_schema_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "contracts").mkdir()
            source = _source(root, {})
            openapi = root / OPENAPI_RELATIVE
            document = json.loads(openapi.read_text(encoding="utf-8"))
            document["components"]["schemas"]["ProductScope"]["enum"].append("other")
            openapi.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
            mutated_sha256 = hashlib.sha256(openapi.read_bytes()).hexdigest()
            source["openapi_bundle"] = {**OPENAPI_BUNDLE, "sha256": mutated_sha256}
            source["files"][OPENAPI_RELATIVE] = mutated_sha256
            (root / "contracts/source.json").write_text(json.dumps(source), encoding="utf-8")

            with self.assertRaisesRegex(ProvenanceError, "invalid_openapi_lineage"):
                load_and_verify(root)

    def test_mutated_declared_file_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "contracts").mkdir()
            payload = root / "payload.txt"
            payload.write_text("reviewed\n", encoding="utf-8")
            source = _source(
                root,
                {"payload.txt": hashlib.sha256(payload.read_bytes()).hexdigest()},
            )
            (root / "contracts/source.json").write_text(json.dumps(source), encoding="utf-8")
            load_and_verify(root)
            payload.write_text("mutated\n", encoding="utf-8")
            with self.assertRaisesRegex(ProvenanceError, "source_drift"):
                load_and_verify(root)

    def test_missing_declared_file_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "contracts").mkdir()
            source = _source(root, {"missing.txt": "0" * 64})
            (root / "contracts/source.json").write_text(json.dumps(source), encoding="utf-8")
            with self.assertRaisesRegex(ProvenanceError, "source_file_missing"):
                load_and_verify(root)

    def test_mismatched_openapi_lineage_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "contracts").mkdir()
            source = _source(root, {})
            source["openapi_upstream"] = {
                **OPENAPI_UPSTREAM,
                "revision": "0" * 40,
            }
            (root / "contracts/source.json").write_text(json.dumps(source), encoding="utf-8")

            with self.assertRaisesRegex(ProvenanceError, "invalid_source_lock"):
                load_and_verify(root)


if __name__ == "__main__":
    unittest.main()
