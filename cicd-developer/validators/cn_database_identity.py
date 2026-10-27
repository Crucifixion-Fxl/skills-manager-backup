"""Fixed CN PostgreSQL producer projection; input is inert native API evidence."""

from __future__ import annotations

import hashlib
import json
import re

COMPOSITION = "database.cn-staging.platform.addx.io"
# Canonical JSON of Composition.spec, excluding only Revision.spec.revision.
# DEV/k8s merged911798: SQL-only; merged71e85e0: default-off native publication.
APPROVED_SPECS = {
    "f478388ab73a7b7fc909186b2fd0e21470feab397945b62763e039a0c70d1c15": False,
    "022802afb1a8dc44f36f368b403eae8f9b67db12f9d51a41bfce596918723ffe": True,
}


def verified_revisions(data):
    """Verify a complete target-selected RevisionList; never execute its template."""
    if not isinstance(data, dict) or (data.get("apiVersion"), data.get("kind")) != (
        "apiextensions.crossplane.io/v1",
        "CompositionRevisionList",
    ):
        raise ValueError("expected native CompositionRevisionList")
    meta = data.get("metadata")
    if (
        not isinstance(meta, dict)
        or not isinstance(meta.get("resourceVersion"), str)
        or not meta["resourceVersion"]
        or meta.get("continue", "") != ""
        or (
            "remainingItemCount" in meta
            and (
                type(meta["remainingItemCount"]) is not int
                or meta["remainingItemCount"] != 0
            )
        )
    ):
        raise ValueError("incomplete CompositionRevision inventory")
    items = data.get("items")
    if not isinstance(items, list) or len(items) > 1000:
        raise ValueError("invalid CompositionRevision items")
    result = {}
    for item in items:
        if not isinstance(item, dict) or (item.get("apiVersion"), item.get("kind")) != (
            "apiextensions.crossplane.io/v1",
            "CompositionRevision",
        ):
            raise ValueError("unexpected CompositionRevision object")
        metadata = item.get("metadata", {})
        name = metadata.get("name") if isinstance(metadata, dict) else None
        labels = metadata.get("labels", {}) if isinstance(metadata, dict) else None
        if (
            not isinstance(metadata, dict)
            or not isinstance(name, str)
            or re.fullmatch(re.escape(COMPOSITION) + r"-[a-z0-9]+", name) is None
            or not isinstance(metadata.get("uid"), str)
            or not metadata["uid"]
            or not isinstance(labels, dict)
            or labels.get("crossplane.io/composition-name") != COMPOSITION
            or name in result
        ):
            raise ValueError("unbound or duplicate CompositionRevision identity")
        spec = item.get("spec")
        if (
            not isinstance(spec, dict)
            or type(spec.get("revision")) is not int
            or spec["revision"] < 1
        ):
            raise ValueError("invalid CompositionRevision spec")
        effective = {k: v for k, v in spec.items() if k != "revision"}
        digest = hashlib.sha256(
            json.dumps(effective, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if digest not in APPROVED_SPECS:
            raise ValueError(
                "unrecognized CompositionRevision spec; review the actual producer contract"
            )
        result[name] = APPROVED_SPECS[digest]
    return result


def claim_failures(doc, revisions):
    db = doc["spec"]
    failures = []
    if db.get("compositionUpdatePolicy") != "Manual":
        failures.append("CN PostgreSQL requires explicit Manual Composition updates")
    revision = db.get("compositionRevisionRef")
    revision_name = revision.get("name") if isinstance(revision, dict) else None
    if (
        not isinstance(revision, dict)
        or set(revision) != {"name"}
        or not isinstance(revision_name, str)
        or revisions is None
        or revision_name not in revisions
    ):
        failures.append(
            "CN PostgreSQL requires an explicit verified native CompositionRevision"
        )
    app = db.get("app")
    if (
        not isinstance(app, str)
        or not 2 <= len(app) <= 31
        or re.fullmatch(r"[a-z][a-z0-9]*(_[a-z0-9]+)*", app) is None
    ):
        failures.append(
            "CN PostgreSQL requires a canonical snake_case app of 2..31 characters"
        )
    if db.get("env") != "staging" or db.get("engine") != "postgres":
        failures.append("CN PostgreSQL supports only explicit staging/postgres")
    if "purpose" in db or "additionalUsers" in db or "instanceRef" in db:
        failures.append("CN PostgreSQL supports only the primary app identity")
    metadata = doc.get("metadata")
    namespace = metadata.get("namespace") if isinstance(metadata, dict) else None
    if isinstance(app, str) and namespace != "staging-" + app.replace("_", "-"):
        failures.append("CN PostgreSQL requires the exact staging app namespace")
    publish = db.get("publishCredentials", False)
    if type(publish) is not bool:
        failures.append("CN PostgreSQL publication must be a boolean")
    elif publish:
        if revisions is not None and (
            not isinstance(revision_name, str)
            or not revisions.get(revision_name, False)
        ):
            failures.append(
                "selected CN PostgreSQL revision does not support publication"
            )
        if (
            not isinstance(app, str)
            or re.fullmatch(r"[a-z][a-z0-9]{1,30}", app) is None
        ):
            failures.append("CN PostgreSQL publication requires an alphanumeric app")
    return failures


def producers(doc):
    """Reserve the exact SQL, credential and potential publication identities."""
    app = doc["spec"]["app"]
    dns = app.replace("_", "-")
    prefix = f"cn-staging-{dns}-postgres"
    return {
        "primary": {
            ("postgres.Role", f"shared-postgres/{app}"),
            ("postgres.Database", f"shared-postgres/{app}"),
            ("postgres.ProviderConfig", f"{prefix}-schema"),
            ("Secret", f"crossplane-system/{prefix}-db-cred"),
            ("PushSecret", f"crossplane-system/{prefix}"),
            ("ClusterSecretStore", f"{prefix}-reader"),
            ("ClusterSecretStore", f"{prefix}-writer"),
            ("ServiceAccount", f"shared-postgres-publishers/{app}"),
            ("ServiceAccount", f"shared-postgres-readers/{app}"),
            (
                "Vault",
                f"cn-staging-vault/shared-db/staging/rds/application/{app}/postgres",
            ),
        }
    }
