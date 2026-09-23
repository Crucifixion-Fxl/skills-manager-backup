"""Offline source-lock verification for the distribution seed."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .openapi_lineage import OpenApiLineageError, verify_upstream_lineage

_OPENAPI_LOCK_PATH = "contracts/audience-sync-v2.openapi.json"
_PROJECT_UPSTREAM = {
    "repository": "services/audiences",
    "revision": "d40108fe69eb5eeaa6879ca4e9aee7f513aa6e2e",
    "path": "audience-workflow/api/project-control-plane.openapi.json",
    "sha256": "7ea8ccad8e98135279e2979d6fe5b2c4ad0021be126053a87fc287d11e69d675",
}
_EXPECTED_OPENAPI_UPSTREAM = {
    "repository": "services/audiences",
    "revision": "53097e58faf98280e08560ef7361a5e1af3b140c",
    "path": "audience-workflow/api/audience-sync-v2.openapi.json",
    "sha256": "a1720465b316b19158bcffb4e4525af5e522762868a98db79c5c062d168c0f33",
}
_EXPECTED_OPENAPI_BUNDLE = {
    "sha256": "3382b67e825563282aec68bf9a20db7f4e21667e7e50ee3f8fd7d0475c26e4fe",
    "derived_from_upstream_sha256": _EXPECTED_OPENAPI_UPSTREAM["sha256"],
    "transformation": "remove_local_effect_policy_metadata",
    "removed_policy_markers": [
        "info.description default-dark qualifier",
        "x-audience-sync-effects-default",
    ],
}


class ProvenanceError(ValueError):
    pass


def load_and_verify(root: Path) -> dict[str, Any]:
    source_path = root / "contracts" / "source.json"
    try:
        source = json.loads(source_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ProvenanceError("invalid_source_lock") from None
    files = source.get("files")
    if not isinstance(files, dict) or not files:
        raise ProvenanceError("invalid_source_lock")
    if (
        source.get("project_openapi_upstream") != _PROJECT_UPSTREAM
        or files.get("contracts/project-control-plane.openapi.json") != _PROJECT_UPSTREAM["sha256"]
    ):
        raise ProvenanceError("invalid_source_lock")
    if source.get("openapi_upstream") != _EXPECTED_OPENAPI_UPSTREAM:
        raise ProvenanceError("invalid_source_lock")
    bundle_metadata = source.get("openapi_bundle")
    if not isinstance(bundle_metadata, dict):
        raise ProvenanceError("invalid_source_lock")
    for name, expected in _EXPECTED_OPENAPI_BUNDLE.items():
        if name != "sha256" and bundle_metadata.get(name) != expected:
            raise ProvenanceError("invalid_source_lock")
    bundle_sha256 = bundle_metadata.get("sha256")
    if not isinstance(bundle_sha256, str) or files.get(_OPENAPI_LOCK_PATH) != bundle_sha256:
        raise ProvenanceError("invalid_source_lock")
    try:
        bundle = (root / _OPENAPI_LOCK_PATH).read_bytes()
    except OSError:
        raise ProvenanceError("source_file_missing") from None
    if hashlib.sha256(bundle).hexdigest() != bundle_sha256:
        raise ProvenanceError("source_drift")
    try:
        verified_bundle_sha256 = verify_upstream_lineage(
            bundle, _EXPECTED_OPENAPI_UPSTREAM["sha256"]
        )
    except OpenApiLineageError:
        raise ProvenanceError("invalid_openapi_lineage") from None
    if verified_bundle_sha256 != _EXPECTED_OPENAPI_BUNDLE["sha256"]:
        raise ProvenanceError("invalid_source_lock")
    for relative, expected in files.items():
        if not isinstance(relative, str) or not isinstance(expected, str):
            raise ProvenanceError("invalid_source_lock")
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError:
            raise ProvenanceError("invalid_source_lock") from None
        try:
            actual = hashlib.sha256(candidate.read_bytes()).hexdigest()
        except OSError:
            raise ProvenanceError("source_file_missing") from None
        if actual != expected:
            raise ProvenanceError("source_drift")
    return source
