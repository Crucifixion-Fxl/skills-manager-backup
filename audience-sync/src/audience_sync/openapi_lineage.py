"""Mechanical lineage proof for the policy-neutralized OpenAPI bundle."""

from __future__ import annotations

import hashlib
import json
from typing import Any

_NEUTRAL_DESCRIPTION = (
    "Fixed aggregate-only Audience definition, materialization, and provider "
    "handoff facade."
)
_UPSTREAM_DESCRIPTION = (
    "Fixed aggregate-only Audience definition, materialization, and default-dark "
    "provider handoff facade."
)
_REMOVED_EXTENSION = "x-audience-sync-effects-default"


class OpenApiLineageError(ValueError):
    """The bundled document cannot prove its pinned upstream lineage."""


def verify_upstream_lineage(bundle: bytes, expected_upstream_sha256: str) -> str:
    """Reinsert only the two removed markers and verify the upstream bytes."""
    try:
        document: Any = json.loads(bundle.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise OpenApiLineageError("invalid_policy_neutral_bundle") from None
    if not isinstance(document, dict) or _REMOVED_EXTENSION in document:
        raise OpenApiLineageError("invalid_policy_neutral_bundle")
    info = document.get("info")
    if not isinstance(info, dict) or info.get("description") != _NEUTRAL_DESCRIPTION:
        raise OpenApiLineageError("invalid_policy_neutral_bundle")

    info["description"] = _UPSTREAM_DESCRIPTION
    document[_REMOVED_EXTENSION] = "disabled"
    reconstructed = (json.dumps(document, indent=2) + "\n").encode("utf-8")
    if hashlib.sha256(reconstructed).hexdigest() != expected_upstream_sha256:
        raise OpenApiLineageError("upstream_lineage_mismatch")
    return hashlib.sha256(bundle).hexdigest()
