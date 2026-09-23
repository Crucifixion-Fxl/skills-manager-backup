#!/usr/bin/env python3
"""Validate DB resource details that are easy to get subtly wrong.

The check is intentionally narrow: RDS/Aurora password and connection chain
resources must keep the expected ArgoCD sync-wave ordering; DocumentDB member
instances must avoid AWS-rejected fields; ArgoCD drift ignores that are needed
for DB/stateful resources must actually be respected during sync; and stateful
data stores (RDS/Aurora/DocumentDB/ElastiCache) must be orphan-safe so a CR
deletion or ArgoCD prune (e.g. when a manifest is moved between namespaces or
removed) cannot delete the physical store; and a kind:Database (shared-middleware
self-service) spec.app must be the underscore-form identifier it becomes. MySQL
RDS Instances that already declare the current 8.4 family must use the matching
parameter and option groups locked in references/cost-tiering/rds.yaml.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any

import yaml


EXPECTED_WAVES = {
    ("Password", "-rds-password-gen"): "-5",
    ("Password", "-aurora-password-gen"): "-5",
    ("ExternalSecret", "-rds-password"): "-4",
    ("ExternalSecret", "-aurora-password"): "-4",
    ("PushSecret", "-rds-password-push"): "-3",
    ("PushSecret", "-aurora-password-push"): "-3",
    ("PushSecret", "-rds-push"): "0",
    ("PushSecret", "-aurora-push"): "0",
}
DB_APPLICATION_KEY_MARKERS = ("/rds/application/", "/aurora/application/")
# Crossplane MRv2 provider groups — kept as constants because the literal recurs
# across several registries / checks below.
RDS_GROUP = "rds.aws.m.upbound.io"
ELASTICACHE_GROUP = "elasticache.aws.m.upbound.io"
RDS_CONTRACT_PATH = (
    Path(__file__).resolve().parents[1] / "references/cost-tiering/rds.yaml"
)


def _load_mysql_rds_contract() -> dict[str, str]:
    data = yaml.safe_load(RDS_CONTRACT_PATH.read_text(encoding="utf-8"))
    contract = (data.get("engine_contracts") or {}).get("mysql")
    required = {
        "default_engine_version",
        "allowed_engine_version_regex",
        "parameter_group_name",
        "option_group_name",
    }
    if not isinstance(contract, dict) or not required.issubset(contract):
        missing = sorted(required - set(contract or {}))
        raise RuntimeError(
            f"invalid mysql RDS engine contract in {RDS_CONTRACT_PATH}: "
            f"missing {missing}"
        )
    return {key: str(contract[key]) for key in required}


MYSQL_RDS_CONTRACT = _load_mysql_rds_contract()
MYSQL_RDS_VERSION_RE = re.compile(
    MYSQL_RDS_CONTRACT["allowed_engine_version_regex"]
)
# These two are a whole-list jsonPointer (/spec/volumeClaimTemplates) and a
# scalar jsonPointer (/spec/forProvider/publiclyAccessible) — both safe under
# RespectIgnoreDifferences. Do NOT add an array-element jqPath here (e.g.
# .spec.data[]?...): RespectIgnoreDifferences pre-patches the live atomic array
# over desired and deadlocks when its membership changes (see check below).
ARGOCD_IGNORE_REQUIRES_RESPECT = {
    ("apps", "StatefulSet", "/spec/volumeClaimTemplates"):
        "StatefulSet volumeClaimTemplates drift ignore",
    (RDS_GROUP, "ClusterInstance", "/spec/forProvider/publiclyAccessible"):
        "DocumentDB ClusterInstance publiclyAccessible late-init drift ignore",
}

# Matches a jqPath that indexes INTO an array element (e.g. .spec.data[],
# .spec.dataFrom[], .spec.rules[]). Combined with RespectIgnoreDifferences=true
# this deadlocks when the array's membership grows/shrinks.
ARRAY_ELEMENT_JQPATH = re.compile(r"\.[A-Za-z0-9_]+\[\]")

# --- Stateful orphan-safety (prevent accidental physical DB deletion) ---------
# MRv2 namespaced providers (*.m.upbound.io) have NO spec.deletionPolicy field;
# writing it is silently dropped, giving false orphan protection. Orphan
# semantics are expressed via managementPolicies (drop the "Delete" action).
# This check only targets the MRv2 namespaced family the skill's templates emit;
# legacy non-namespaced v1 (*.aws.upbound.io, where deletionPolicy is valid) is
# out of scope.
MUPBOUND_SUFFIX = ".m.upbound.io"
# (group, kind) of data stores whose CR deletion (or an ArgoCD prune of a moved /
# removed manifest) deletes the physical store when the Delete management action
# is in effect. Cluster MEMBERS (rds ClusterInstance) are compute, not the data
# store, so they are intentionally excluded. Extend this set for new engines.
STATEFUL_DATA_STORES = {
    (RDS_GROUP, "Instance"),                 # RDS
    (RDS_GROUP, "Cluster"),                  # Aurora / DocumentDB cluster
    (ELASTICACHE_GROUP, "ReplicationGroup"),
    (ELASTICACHE_GROUP, "CacheCluster"),
}
# Data stores that own the RDS/Aurora AWS-layer safety knobs (deletionProtection,
# skipFinalSnapshot) and the multiAz field. ElastiCache has neither.
RDS_AWS_LAYER_STORES = {
    (RDS_GROUP, "Instance"),
    (RDS_GROUP, "Cluster"),
}
MANAGED_DELETE_ACTIONS = {"Delete", "*"}
ORPHAN_SAFE_POLICIES = "[Observe, Create, Update, LateInitialize]"
# A resource is treated as prod when its env tag is a prod keyword (authoritative
# when present), else when its identifier / external-name / name carries a prod
# env segment (prod-us / production / ...). Prod data stores must be orphan-safe
# so any prune orphans the store instead of deleting it.
PROD_NAME_RE = re.compile(r"(^|-)prod(uction)?($|-)")

# --- kind:Database (shared-middleware self-service XRD) naming ----------------
# platform.addx.io/v1alpha1 Database. spec.app becomes the physical DB name +
# DB user + vault path segment, so it must be underscore-form (NO hyphens). The
# XRD itself pins this pattern; the validator catches it in CI before apply.
PLATFORM_DATABASE_GROUP = "platform.addx.io"
DATABASE_APP_RE = re.compile(r"^[a-z][a-z0-9_]{1,30}$")
DATABASE_APP_SLUG_ANNOTATION = "platform.addx.io/app-slug"
DATABASE_NAMESPACE_PHASES = ("dev", "staging", "pre", "prod", "canary", "test")
DATABASE_GENERIC_PHASE_REGION_NAMES = {
    f"{phase}-{region}"
    for phase in DATABASE_NAMESPACE_PHASES
    for region in ("us", "eu", "cn", "sg")
}
DATABASE_ADDITIONAL_USER_RE = re.compile(r"^[a-z][a-z0-9_]+$")
DATABASE_TABLE_RE = re.compile(r"^[a-z_][a-z0-9_]*$")
DATABASE_ADDITIONAL_PRIVILEGES = {"SELECT", "INSERT", "UPDATE", "DELETE"}
DATABASE_PURPOSE_RE = re.compile(r"^[a-z][a-z0-9_]*$")
DATABASE_FORBIDDEN_CONTROL_FIELDS = {
    "host",
    "admin",
    "administrator",
    "path",
    "vaultpath",
    "owner",
    "privilege",
    "privileges",
}
MAX_INPUT_BYTES = 16 * 1024 * 1024
MAX_INVENTORY_ITEMS = 10000


class UniqueKeyLoader(yaml.SafeLoader):
    """Do not silently change a producer identity by accepting duplicate keys."""

    def construct_mapping(self, node, deep=False):
        self.flatten_mapping(node)
        seen = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in seen
                seen.add(key)
            except TypeError as exc:
                raise yaml.constructor.ConstructorError(
                    None, None, "unhashable mapping key", key_node.start_mark
                ) from exc
            if duplicate:
                raise yaml.constructor.ConstructorError(
                    None, None, "duplicate mapping key", key_node.start_mark
                )
        return super().construct_mapping(node, deep=deep)


def _bounded_text(path: Path) -> str:
    # O_NONBLOCK avoids hanging on a FIFO before its type can be checked. Symlinks
    # may resolve to ordinary files, but devices/pipes/directories are rejected.
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NONBLOCK), "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("input must be a regular file")
        data = stream.read(MAX_INPUT_BYTES + 1)
    if len(data) > MAX_INPUT_BYTES:
        raise ValueError("input exceeds the 16 MiB limit")
    return data.decode("utf-8")


def iter_yaml_files(root: Path):
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix in {".yaml", ".yml"}:
            yield path


def load_docs(path: Path):
    try:
        return list(yaml.load_all(_bounded_text(path), Loader=UniqueKeyLoader))
    except (OSError, ValueError, yaml.YAMLError, RecursionError) as exc:
        # YAML exception strings can include source lines. Keep input payloads
        # out of logs while retaining the two actionable ambiguity diagnostics.
        problem = getattr(exc, "problem", None)
        reason = problem if problem in ("duplicate mapping key", "unhashable mapping key") else type(exc).__name__
        return [("YAML_ERROR", reason)]


def metadata(doc: dict[str, Any]) -> dict[str, Any]:
    value = doc.get("metadata")
    return value if isinstance(value, dict) else {}


def annotations(doc: dict[str, Any]) -> dict[str, Any]:
    value = metadata(doc).get("annotations")
    return value if isinstance(value, dict) else {}


def name_of(doc: dict[str, Any]) -> str:
    value = metadata(doc).get("name")
    return value if isinstance(value, str) else ""


def api_group(doc: dict[str, Any]) -> str:
    api_version = doc.get("apiVersion")
    if not isinstance(api_version, str) or "/" not in api_version:
        return ""
    return api_version.split("/", 1)[0]


def spec(doc: dict[str, Any]) -> dict[str, Any]:
    value = doc.get("spec")
    return value if isinstance(value, dict) else {}


def for_provider(doc: dict[str, Any]) -> dict[str, Any]:
    value = spec(doc).get("forProvider")
    return value if isinstance(value, dict) else {}


def management_policies(doc: dict[str, Any]) -> list | None:
    value = spec(doc).get("managementPolicies")
    return value if isinstance(value, list) else None


def env_tag(doc: dict[str, Any]) -> str:
    tags = for_provider(doc).get("tags")
    if isinstance(tags, dict):
        env = tags.get("env")
        if isinstance(env, str):
            return env
    return ""


def is_m_upbound(doc: dict[str, Any]) -> bool:
    return api_group(doc).endswith(MUPBOUND_SUFFIX)


def is_prod_stateful(doc: dict[str, Any]) -> bool:
    # The env tag is authoritative when present: a staging-tagged resource is not
    # prod even if its name happens to contain a "prod" segment.
    env = env_tag(doc).strip().lower()
    if env:
        return env.startswith("prod")
    # No env tag: fall back to identifier / external-name / name. Aurora &
    # DocumentDB Cluster have no forProvider.identifier — the cluster identifier
    # lives in the crossplane.io/external-name annotation.
    external_name = annotations(doc).get("crossplane.io/external-name")
    candidates = (for_provider(doc).get("identifier"), external_name, name_of(doc))
    return any(isinstance(c, str) and PROD_NAME_RE.search(c) for c in candidates)


def remote_ref_key(item: Any) -> str:
    if not isinstance(item, dict):
        return ""
    remote_ref = item.get("remoteRef")
    if not isinstance(remote_ref, dict):
        return ""
    key = remote_ref.get("key")
    return key if isinstance(key, str) else ""


def external_secret_reads_db_application(doc: dict[str, Any]) -> bool:
    data = doc.get("spec", {}).get("data", [])
    if not isinstance(data, list):
        return False
    return any(
        marker in remote_ref_key(item)
        for item in data
        for marker in DB_APPLICATION_KEY_MARKERS
    )


def required_wave(doc: dict[str, Any]) -> str | None:
    kind = doc.get("kind")
    name = name_of(doc)
    for (expected_kind, suffix), wave in EXPECTED_WAVES.items():
        if kind == expected_kind and name.endswith(suffix):
            return wave

    if kind == "ExternalSecret" and external_secret_reads_db_application(doc):
        return "1"
    return None


def is_docdb_rds_cluster_instance(doc: dict[str, Any]) -> bool:
    return (
        api_group(doc) == RDS_GROUP
        and doc.get("kind") == "ClusterInstance"
        and for_provider(doc).get("engine") == "docdb"
    )


def check_docdb_cluster_instance(path: Path, doc: dict[str, Any]) -> tuple[int, list[str]]:
    if not is_docdb_rds_cluster_instance(doc):
        return 0, []

    provider = for_provider(doc)
    if "publiclyAccessible" not in provider:
        return 1, []

    return (
        1,
        [
            f"FAIL: {path}: ClusterInstance/{name_of(doc)} with engine=docdb "
            "must not set spec.forProvider.publiclyAccessible; AWS rejects this "
            "flag on DocumentDB cluster member instances"
        ],
    )


def check_mysql_rds_engine_contract(
    path: Path, doc: dict[str, Any]
) -> tuple[int, list[str]]:
    if not (
        api_group(doc) == RDS_GROUP
        and doc.get("kind") == "Instance"
        and for_provider(doc).get("engine") == "mysql"
    ):
        return 0, []

    provider = for_provider(doc)
    version = provider.get("engineVersion")
    # validate.sh scans the full manifest directory, not only changed files.
    # Skip legacy version families here so an unrelated MR is not blocked by a
    # grandfathered 8.0 instance. The add-rds workflow, recipe, and offline E2E
    # own the new-instance version gate; this validator enforces 8.4 family
    # consistency once a manifest adopts 8.4.
    if not isinstance(version, str) or MYSQL_RDS_VERSION_RE.fullmatch(version) is None:
        return 0, []

    obj = f"Instance/{name_of(doc)}"
    failures: list[str] = []
    expected_fields = {
        "parameterGroupName": MYSQL_RDS_CONTRACT["parameter_group_name"],
        "optionGroupName": MYSQL_RDS_CONTRACT["option_group_name"],
    }
    for field, expected in expected_fields.items():
        actual = provider.get(field)
        if actual != expected:
            failures.append(
                f"FAIL: {path}: {obj} forProvider.{field}={actual!r}; "
                f"MySQL 8.4 requires {expected!r} from "
                "references/cost-tiering/rds.yaml -> engine_contracts.mysql"
            )

    return 1, failures


def sync_options(doc: dict[str, Any]) -> set[str]:
    options = ((spec(doc).get("syncPolicy") or {}).get("syncOptions") or [])
    return {item for item in options if isinstance(item, str)}


def has_respect_ignore_differences(doc: dict[str, Any]) -> bool:
    return "RespectIgnoreDifferences=true" in sync_options(doc)


def iter_ignore_items(doc: dict[str, Any]):
    items = spec(doc).get("ignoreDifferences") or []
    if not isinstance(items, list):
        return
    for item in items:
        if isinstance(item, dict):
            yield item


def ignore_pointers(item: dict[str, Any]) -> set[str]:
    pointers = item.get("jsonPointers") or []
    expressions = item.get("jqPathExpressions") or []
    values: set[str] = set()
    values.update(pointer for pointer in pointers if isinstance(pointer, str))
    values.update(expr for expr in expressions if isinstance(expr, str))
    return values


def check_application_drift_ignores(path: Path, doc: dict[str, Any]) -> tuple[int, list[str]]:
    if not (
        doc.get("kind") == "Application"
        and isinstance(doc.get("apiVersion"), str)
        and doc["apiVersion"].startswith("argoproj.io/")
    ):
        return 0, []

    failures: list[str] = []
    needs_respect: list[str] = []
    for item in iter_ignore_items(doc):
        group = item.get("group") or ""
        kind = item.get("kind")
        pointers = ignore_pointers(item)
        for (expected_group, expected_kind, pointer), reason in (
            ARGOCD_IGNORE_REQUIRES_RESPECT.items()
        ):
            if group == expected_group and kind == expected_kind and pointer in pointers:
                needs_respect.append(reason)

    if needs_respect and not has_respect_ignore_differences(doc):
        reasons = ", ".join(sorted(set(needs_respect)))
        failures.append(
            f"FAIL: {path}: Application/{name_of(doc)} uses {reasons} but "
            "must set spec.syncPolicy.syncOptions += RespectIgnoreDifferences=true"
        )

    checked = 1 if needs_respect else 0

    # Inverse guard: RespectIgnoreDifferences=true + a jqPath that indexes INTO
    # an atomic array element (.spec.data[]/.dataFrom[]/.rules[]) deadlocks when
    # the array's membership grows/shrinks. jsonPointers (whole-list/scalar) are
    # exempt — only jqPathExpressions can index into elements.
    if has_respect_ignore_differences(doc):
        array_jqpaths = sorted({
            expr
            for item in iter_ignore_items(doc)
            for expr in (item.get("jqPathExpressions") or [])
            if isinstance(expr, str) and ARRAY_ELEMENT_JQPATH.search(expr)
        })
        if array_jqpaths:
            checked = 1
            failures.append(
                f"FAIL: {path}: Application/{name_of(doc)} pairs "
                "RespectIgnoreDifferences=true with atomic-array-element "
                f"jqPath(s) {array_jqpaths}; drop RespectIgnoreDifferences for "
                "this app, or ignore only scalar/whole-object paths, or use "
                "ServerSideApply"
            )

    return checked, failures


def _has_delete_action(policies: list) -> bool:
    return any(isinstance(action, str) and action in MANAGED_DELETE_ACTIONS for action in policies)


def _prod_store_orphan_failures(
    path: Path, obj: str, key: tuple, doc: dict[str, Any], provider: dict[str, Any]
) -> list[str]:
    """Rule B: a prod data store must be orphan-safe (managementPolicies omits
    Delete) and, for RDS/Aurora, keep the AWS-layer safety net."""
    if key not in STATEFUL_DATA_STORES or not is_prod_stateful(doc):
        return []

    failures: list[str] = []
    policies = management_policies(doc)
    if policies is None:
        failures.append(
            f"FAIL: {path}: {obj} is prod but sets no managementPolicies "
            "(defaults to ['*'] including Delete -> CR deletion / ArgoCD prune "
            f"deletes the physical store). Set managementPolicies: {ORPHAN_SAFE_POLICIES}"
        )
    elif _has_delete_action(policies):
        failures.append(
            f"FAIL: {path}: {obj} is prod but managementPolicies {policies} includes "
            f"Delete/'*' -> CR deletion / ArgoCD prune deletes the physical store. "
            f"Drop 'Delete': {ORPHAN_SAFE_POLICIES}"
        )

    if key in RDS_AWS_LAYER_STORES and provider.get("deletionProtection") is not True:
        failures.append(
            f"FAIL: {path}: {obj} is prod but forProvider.deletionProtection is not "
            "true (AWS-layer safety net; required for prod)"
        )
    if key in RDS_AWS_LAYER_STORES and provider.get("skipFinalSnapshot") is True:
        failures.append(
            f"FAIL: {path}: {obj} is prod but forProvider.skipFinalSnapshot is true; "
            "prod must keep a final snapshot (set skipFinalSnapshot: false)"
        )
    return failures


def check_stateful_orphan_safety(path: Path, doc: dict[str, Any]) -> tuple[int, list[str]]:
    """Prevent accidental physical store deletion via CR delete / ArgoCD prune.

    Rule A (every *.m.upbound.io managed resource): spec.deletionPolicy does not
            exist on MRv2 and is silently ignored (false orphan protection).
    Rule B (prod data stores — RDS/Aurora/DocumentDB/ElastiCache): managementPolicies
            must omit the Delete action; and for RDS/Aurora the AWS-layer safety net
            (deletionProtection=true, skipFinalSnapshot=false) must hold, so a prune
            orphans the store, never deletes it. (see _prod_store_orphan_failures)
    Rule C (RDS/Aurora): forProvider.multiAZ is a silently-ignored mis-casing of
            forProvider.multiAz (leaves a single-AZ instance).
    """
    if not is_m_upbound(doc):
        return 0, []

    kind = doc.get("kind")
    key = (api_group(doc), kind)
    obj = f"{kind}/{name_of(doc)}"
    provider = for_provider(doc)
    failures: list[str] = []

    # Rule A — applies to every MRv2 managed resource.
    if "deletionPolicy" in spec(doc):
        failures.append(
            f"FAIL: {path}: {obj} sets spec.deletionPolicy, which does not exist on "
            "MRv2 (*.m.upbound.io) and is silently ignored (false orphan protection). "
            f"Express Orphan via managementPolicies omitting 'Delete': {ORPHAN_SAFE_POLICIES}"
        )

    # Rule C — mis-cased multiAZ silently leaves a single-AZ instance.
    if key in RDS_AWS_LAYER_STORES and "multiAZ" in provider:
        failures.append(
            f"FAIL: {path}: {obj} sets forProvider.multiAZ, but the CRD field is "
            "forProvider.multiAz (capital 'AZ' is silently ignored, leaving a "
            "single-AZ instance). Rename to multiAz"
        )

    # Rule B — prod data stores must be orphan-safe.
    failures.extend(_prod_store_orphan_failures(path, obj, key, doc, provider))

    return 1, failures


def _check_additional_mysql_grant(
    path: Path,
    prefix: str,
    grant: Any,
    grant_index: int,
    seen_tables: set[str],
) -> list[str]:
    grant_prefix = f"{prefix}.grants[{grant_index}]"
    if not isinstance(grant, dict):
        return [f"FAIL: {path}: {grant_prefix} must be an object"]

    failures: list[str] = []
    table = grant.get("table")
    if (
        not isinstance(table, str)
        or len(table) > 64
        or DATABASE_TABLE_RE.fullmatch(table) is None
    ):
        failures.append(
            f"FAIL: {path}: {grant_prefix}.table must be a lowercase literal MySQL identifier; "
            "wildcards and qualified database names are forbidden"
        )
    elif table in seen_tables:
        failures.append(f"FAIL: {path}: {prefix} repeats grant table {table!r}")
    else:
        seen_tables.add(table)

    privileges = grant.get("privileges")
    if not isinstance(privileges, list) or not privileges:
        failures.append(
            f"FAIL: {path}: {grant_prefix}.privileges must be a non-empty list"
        )
        return failures

    invalid = sorted(
        {
            privilege if isinstance(privilege, str) else repr(privilege)
            for privilege in privileges
            if not isinstance(privilege, str)
            or privilege not in DATABASE_ADDITIONAL_PRIVILEGES
        },
        key=str,
    )
    if invalid:
        failures.append(
            f"FAIL: {path}: {grant_prefix}.privileges contains {invalid}; only "
            "SELECT/INSERT/UPDATE/DELETE are allowed"
        )
    if len(privileges) != len(set(map(str, privileges))):
        failures.append(f"FAIL: {path}: {grant_prefix}.privileges contains duplicates")
    return failures


def _check_additional_mysql_user(
    path: Path,
    obj: str,
    app: str,
    user: Any,
    user_index: int,
    seen_users: set[str],
) -> list[str]:
    prefix = f"{obj} spec.additionalUsers[{user_index}]"
    if not isinstance(user, dict):
        return [f"FAIL: {path}: {prefix} must be an object"]

    failures: list[str] = []
    user_name = user.get("name")
    valid_user_name = (
        isinstance(user_name, str)
        and len(user_name) <= 20
        and DATABASE_ADDITIONAL_USER_RE.fullmatch(user_name) is not None
    )
    if not valid_user_name:
        failures.append(
            f"FAIL: {path}: {prefix}.name must match ^[a-z][a-z0-9_]+$ and be <=20 characters"
        )
    elif user_name in seen_users:
        failures.append(f"FAIL: {path}: {obj} repeats additional user name {user_name!r}")
    else:
        seen_users.add(user_name)
        if user_name in {"database", "postgres"} or user_name.startswith("postgres_"):
            failures.append(
                f"FAIL: {path}: {prefix}.name={user_name!r} is reserved: it can "
                "overwrite the primary MySQL or PostgreSQL credential Vault key"
            )
        if len(app) + 1 + len(user_name) > 32:
            failures.append(
                f"FAIL: {path}: {prefix}.name derives MySQL username {app}_{user_name}, "
                "which exceeds 32 characters"
            )

    max_connections = user.get("maxUserConnections", 50)
    if (
        not isinstance(max_connections, int)
        or isinstance(max_connections, bool)
        or not 1 <= max_connections <= 200
    ):
        failures.append(
            f"FAIL: {path}: {prefix}.maxUserConnections must be an integer from 1 to 200"
        )

    grants = user.get("grants")
    if not isinstance(grants, list) or not grants:
        failures.append(f"FAIL: {path}: {prefix}.grants must be a non-empty list")
        return failures

    seen_tables: set[str] = set()
    for grant_index, grant in enumerate(grants):
        failures.extend(
            _check_additional_mysql_grant(
                path, prefix, grant, grant_index, seen_tables
            )
        )
    return failures


def _is_database_app_slug(value: Any) -> bool:
    """Return whether value is the 2-31 character ASCII kebab-case app slug."""
    if not isinstance(value, str) or not 2 <= len(value) <= 31:
        return False
    if not value[0].isascii() or not value[0].islower():
        return False
    return all(
        segment
        and all(char.isascii() and (char.islower() or char.isdigit()) for char in segment)
        for segment in value.split("-")
    )


def _standard_database_namespace_app(namespace: str) -> str | None:
    """Extract the app suffix from a valid {phase}-{kebab-app} namespace."""
    for phase in DATABASE_NAMESPACE_PHASES:
        prefix = f"{phase}-"
        if namespace.startswith(prefix):
            app_slug = namespace.removeprefix(prefix)
            return app_slug if _is_database_app_slug(app_slug) else None
    return None


def _check_platform_database_app_slug(
    path: Path,
    obj: str,
    app: str,
    doc: dict[str, Any],
) -> list[str]:
    """Validate the explicit new-claim marker while grandfathering legacy claims."""
    app_slug = annotations(doc).get(DATABASE_APP_SLUG_ANNOTATION)
    if app_slug is None:
        return []
    if not _is_database_app_slug(app_slug):
        return [
            f"FAIL: {path}: {obj} annotation {DATABASE_APP_SLUG_ANNOTATION!r} "
            "must be a 2-31 character lowercase kebab-case application slug"
        ]

    failures: list[str] = []
    expected_app = app_slug.replace("-", "_")
    if app != expected_app:
        failures.append(
            f"FAIL: {path}: {obj} spec.app={app!r} must be derived from "
            f"{DATABASE_APP_SLUG_ANNOTATION}={app_slug!r} by replacing '-' "
            f"with '_' (expected {expected_app!r})"
        )

    namespace = metadata(doc).get("namespace")
    if not isinstance(namespace, str) or not namespace:
        failures.append(
            f"FAIL: {path}: {obj} must set metadata.namespace explicitly when "
            f"using {DATABASE_APP_SLUG_ANNOTATION}"
        )
        return failures

    namespace_app = _standard_database_namespace_app(namespace)
    if (
        namespace_app is not None
        and namespace not in DATABASE_GENERIC_PHASE_REGION_NAMES
        and app_slug != namespace_app
    ):
        failures.append(
            f"FAIL: {path}: {obj} {DATABASE_APP_SLUG_ANNOTATION}={app_slug!r} "
            f"must equal the standard namespace suffix {namespace_app!r} "
            f"from namespace {namespace!r}"
        )
    return failures


def check_platform_database_naming(path: Path, doc: dict[str, Any]) -> tuple[int, list[str]]:
    """Validate kind:Database identity and bounded optional extensions.

    spec.app becomes the physical DB name + DB user + vault path segment, so it
    must match ^[a-z][a-z0-9_]{1,30}$ (lowercase letters/digits/underscore, no
    hyphens, no uppercase) — the same pattern the XRD pins. The validator catches
    it in CI before apply (where it would otherwise surface as a late XRD rejection
    or a provider-sql / SecretSynced error).

    New claims carry platform.addx.io/app-slug. It is the canonical kebab-case
    source used to derive spec.app mechanically, and in a standard {phase}-{app}
    namespace it must equal the namespace suffix. Claims without the annotation
    are legacy and intentionally remain valid; the CREATE-only Kyverno admission
    policy requires the annotation for every newly created claim.

    metadata.name is intentionally NOT constrained against spec.app: it is only
    the Kubernetes resource name and may include a -db / -<engine> suffix.

    An optional purpose is the only supported way for an app to request a
    secondary PostgreSQL identity. The owner spec.app remains unchanged; the
    platform derives app_purpose and the postgres-<purpose-as-kebab> Vault key.
    Reject bottom-up control fields so callers cannot inject a host,
    administrator, Vault path, database owner, or arbitrary privilege contract.
    """
    if api_group(doc) != PLATFORM_DATABASE_GROUP or doc.get("kind") != "Database":
        return 0, []

    database_spec = spec(doc)
    app = database_spec.get("app")
    obj = f"Database/{name_of(doc)}"
    failures: list[str] = []

    forbidden_fields = sorted(
        field
        for field in database_spec
        if isinstance(field, str)
        and field.lower().replace("_", "") in DATABASE_FORBIDDEN_CONTROL_FIELDS
    )
    if forbidden_fields:
        failures.append(
            f"FAIL: {path}: {obj} has forbidden spec control fields {forbidden_fields}; "
            "host/admin/path/owner/privilege input is not part of the high-level "
            "Database contract"
        )

    if not isinstance(app, str) or not app:
        failures.append(
            f"FAIL: {path}: {obj} must set spec.app "
            "(the DB name / user / vault path segment)"
        )
        return 1, failures

    if not DATABASE_APP_RE.fullmatch(app):
        failures.append(
            f"FAIL: {path}: {obj} spec.app={app!r} must match ^[a-z][a-z0-9_]{{1,30}}$ "
            "— lowercase letters/digits/underscore, NO hyphens (it becomes the physical "
            "DB name + DB user + vault path segment; hyphens are rejected). Convert the "
            "kebab-case app slug to underscores, e.g. factory-service -> factory_service"
        )

    failures.extend(_check_platform_database_app_slug(path, obj, app, doc))

    if "purpose" in database_spec:
        purpose = database_spec.get("purpose")
        if (
            not isinstance(purpose, str)
            or not 2 <= len(purpose) <= 20
            or DATABASE_PURPOSE_RE.fullmatch(purpose) is None
        ):
            failures.append(
                f"FAIL: {path}: {obj} spec.purpose={purpose!r} must be a 2-20 "
                "character lower_snake identifier matching ^[a-z][a-z0-9_]*$"
            )
        if database_spec.get("engine", "mysql") != "postgres":
            failures.append(
                f"FAIL: {path}: {obj} spec.purpose is supported only for engine=postgres"
            )
        if isinstance(purpose, str) and isinstance(app, str):
            postgres_identity = f"{app}_{purpose}"
            if len(postgres_identity) > 63:
                failures.append(
                    f"FAIL: {path}: {obj} derived PostgreSQL identity "
                    f"{postgres_identity!r} exceeds 63 characters"
                )

    additional_users = database_spec.get("additionalUsers")
    if additional_users is None:
        return 1, failures
    if database_spec.get("engine", "mysql") != "mysql":
        failures.append(
            f"FAIL: {path}: {obj} spec.additionalUsers is supported only for engine=mysql"
        )
    if not isinstance(additional_users, list) or not additional_users:
        failures.append(
            f"FAIL: {path}: {obj} spec.additionalUsers must be a non-empty list"
        )
        return 1, failures

    seen_users: set[str] = set()
    for user_index, user in enumerate(additional_users):
        failures.extend(
            _check_additional_mysql_user(
                path, obj, app, user, user_index, seen_users
            )
        )

    return 1, failures


def check_doc(path: Path, doc: Any) -> tuple[int, list[str]]:
    if doc is None:
        return 0, []
    if isinstance(doc, tuple) and doc[0] == "YAML_ERROR":
        return 0, [f"FAIL: {path}: YAML parse error: {doc[1]}"]
    if not isinstance(doc, dict):
        return 0, []
    if "kind" in doc and not isinstance(doc["kind"], str):
        return 0, [f"FAIL: {path}: manifest kind must be a string"]

    checked = 0
    failures: list[str] = []

    wave = required_wave(doc)
    if wave is not None:
        checked += 1
        actual = annotations(doc).get("argocd.argoproj.io/sync-wave")
        if str(actual) != wave:
            failures.append(
                f"FAIL: {path}: {doc.get('kind')}/{name_of(doc)} "
                f"must set argocd.argoproj.io/sync-wave={wave!r}"
            )

    docdb_checked, docdb_failures = check_docdb_cluster_instance(path, doc)
    checked += docdb_checked
    failures.extend(docdb_failures)

    mysql_checked, mysql_failures = check_mysql_rds_engine_contract(path, doc)
    checked += mysql_checked
    failures.extend(mysql_failures)

    app_checked, app_failures = check_application_drift_ignores(path, doc)
    checked += app_checked
    failures.extend(app_failures)

    sos_checked, sos_failures = check_stateful_orphan_safety(path, doc)
    checked += sos_checked
    failures.extend(sos_failures)

    db_checked, db_failures = check_platform_database_naming(path, doc)
    checked += db_checked
    failures.extend(db_failures)

    return checked, failures


def database_producers(doc: dict[str, Any]) -> dict[str, set[tuple[str, str]]]:
    """Project current database.platform.addx.io producer identities, never secrets.

    The scope is one cluster. SQL identities are qualified by provider and kind;
    connection Secrets and PushSecrets share crossplane-system across engines.
    Redis reads shared-redis-conn, so that read-only source is not an owned key.
    """
    db = spec(doc)
    app, env = db["app"], db["env"]
    dns = app.replace("_", "-")
    engine = db.get("engine", "mysql")
    store = "vault-builder-backend" if env in {"dev", "staging"} else "vault-backend"

    def delivery(source: str | None, push: str, remote: str) -> set[tuple[str, str]]:
        keys = {("PushSecret", f"crossplane-system/{push}"),
                ("Vault", f"{store}/{remote}")}
        if source:
            keys.add(("Secret", f"crossplane-system/{source}"))
        return keys

    if engine == "redis":
        return {"primary": delivery(None, f"{dns}-redis-push",
                                    f"{env}/redis/application/{app}/connection")}
    if engine == "postgres":
        purpose = db.get("purpose", "")
        identity = f"{app}_{purpose}" if purpose else app
        identity_dns = identity.replace("_", "-")
        vault_key = f"postgres-{purpose.replace('_', '-')}" if purpose else "postgres"
        keys = delivery(f"{identity_dns}-postgres-db-cred",
                        f"{identity_dns}-postgres-push",
                        f"{env}/rds/application/{app}/{vault_key}")
        keys.update({("postgres.Role", f"shared-postgres/{identity}"),
                     ("postgres.Database", f"shared-postgres/{identity}")})
        if purpose:
            keys.add(("postgres.ProviderConfig", f"{identity_dns}-schema"))
        return {"primary": keys}
    keys = delivery(f"{dns}-db-cred", f"{dns}-db-push",
                    f"{env}/rds/application/{app}/database")
    keys.update({("mysql.User", f"shared-mysql/{app}"),
                 ("mysql.Database", f"shared-mysql/{app}")})
    producers = {"primary": keys}
    for user in db.get("additionalUsers", []):
        name = user["name"]
        user_dns = name.replace("_", "-")
        keys = delivery(f"{dns}-{user_dns}-db-cred", f"{dns}-{user_dns}-db-push",
                        f"{env}/rds/application/{app}/{user_dns}")
        keys.add(("mysql.User", f"shared-mysql/{app}_{name}"))
        producers[f"additionalUsers/{name}"] = keys
    return producers


def _identity_failures(path: Path, doc: dict[str, Any]) -> list[str]:
    """Fail closed where projection of an unknown/partial claim is impossible."""
    failures = check_platform_database_naming(path, doc)[1]
    if doc.get("apiVersion") != "platform.addx.io/v1alpha1":
        failures.append(f"FAIL: {path}: unsupported Database apiVersion")
    for field in ("name", "namespace"):
        value = metadata(doc).get(field)
        limit = 63 if field == "namespace" else 253
        pattern = r"[a-z0-9](?:[-a-z0-9]*[a-z0-9])?" if field == "namespace" else r"[a-z0-9](?:[-a-z0-9.]*[a-z0-9])?"
        if not isinstance(value, str) or len(value) > limit or re.fullmatch(pattern, value) is None:
            failures.append(f"FAIL: {path}: Database metadata.{field} must be a valid explicit Kubernetes name")
    db = spec(doc)
    if db.get("env") not in ("dev", "staging", "pre", "prod"):
        failures.append(f"FAIL: {path}: Database spec.env must be dev/staging/pre/prod")
    if db.get("engine", "mysql") not in ("mysql", "redis", "postgres"):
        failures.append(f"FAIL: {path}: unsupported Database spec.engine")
    composition = db.get("compositionRef")
    if composition is not None and composition != {"name": "database.platform.addx.io"}:
        failures.append(f"FAIL: {path}: unknown Database Composition cannot be projected")
    if "compositionSelector" in db:
        failures.append(f"FAIL: {path}: Database compositionSelector cannot be projected")
    if "compositionRevisionSelector" in db:
        failures.append(f"FAIL: {path}: Database compositionRevisionSelector cannot be projected")
    if db.get("compositionUpdatePolicy", "Automatic") != "Automatic":
        failures.append(f"FAIL: {path}: only Automatic Composition updates can use this identity projection; verify the effective revision")
    revision = db.get("compositionRevisionRef")
    if revision is not None and (
        not isinstance(revision, dict) or set(revision) != {"name"}
        or not isinstance(revision.get("name"), str)
        or re.fullmatch(r"database\.platform\.addx\.io-[a-z0-9][a-z0-9-]*", revision["name"]) is None
    ):
        failures.append(f"FAIL: {path}: unsupported Database compositionRevisionRef")
    return failures


def _unique_json_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def load_database_inventory(path: Path) -> list[dict[str, Any]]:
    """Read a bounded, unpaginated API list; no API, shell or credential access."""
    data = json.loads(_bounded_text(path), object_pairs_hook=_unique_json_pairs)
    if not isinstance(data, dict) or (
        data.get("apiVersion"), data.get("kind")
    ) not in (("v1", "List"), ("platform.addx.io/v1alpha1", "DatabaseList")):
        raise ValueError("inventory must be a Kubernetes List or DatabaseList JSON object")
    meta = data.get("metadata")
    if not isinstance(meta, dict) or not isinstance(meta.get("resourceVersion"), str) or not meta["resourceVersion"]:
        raise ValueError("inventory requires list metadata.resourceVersion from the API")
    if meta.get("continue", "") != "" or (
        "remainingItemCount" in meta and (
            type(meta["remainingItemCount"]) is not int or meta["remainingItemCount"] != 0
        )
    ):
        raise ValueError("inventory must be complete: pagination/remaining items are forbidden")
    items = data.get("items")
    if not isinstance(items, list) or len(items) > MAX_INVENTORY_ITEMS:
        raise ValueError("inventory.items must be a list of at most 10000 Database claims")
    for item in items:
        if not isinstance(item, dict) or item.get("kind") != "Database" or api_group(item) != PLATFORM_DATABASE_GROUP:
            raise ValueError("inventory contains an item that is not a platform Database claim")
    return items


def check_database_identities(
    candidates: list[tuple[Path, dict[str, Any]]],
    inventory: list[dict[str, Any]] | None = None,
    inventory_path: Path | None = None,
) -> list[str]:
    """Compare local producers per directory, or all candidates with one target.

    Without inventory the parent directory is the explicit comparison boundary:
    never combine separate overlays/clusters/examples into an invented target.
    Inventory mode instead treats the complete candidate render as one target.
    It does not attest the caller's selected context or snapshot freshness.
    """
    failures: list[str] = []
    # (comparison scope, namespace, claim name) -> (source, component identities)
    claims = {}

    def record(path, doc, scope, *, replacing=False):
        errors = _identity_failures(path, doc)
        failures.extend(errors)
        if errors:
            return
        key = (scope, metadata(doc)["namespace"], name_of(doc))
        producers = database_producers(doc)
        previous = claims.get(key)
        if previous and not replacing:
            failures.append(f"FAIL: {path}: duplicate Database claim {key[1]}/{key[2]} also in {previous[0]}")
            return
        if previous:
            for component, identities in previous[1].items():
                if not identities.issubset(producers.get(component, set())):
                    failures.append(
                        f"FAIL: {path}: Database {key[1]}/{key[2]} changes or removes existing "
                        f"producer identity {component}; requires an operator-reviewed migration"
                    )
                    # Retain the original producer too: a failed replacement must
                    # not hide collisions with other candidates in this run.
                    return
        claims[key] = (path, producers)

    if inventory is not None:
        for doc in inventory:
            record(inventory_path, doc, "target")
    seen_candidates = set()
    for path, doc in candidates:
        scope = "target" if inventory is not None else str(path.parent.resolve())
        if not isinstance(metadata(doc).get("namespace"), str):
            failures.extend(_identity_failures(path, doc))
            continue
        key = (scope, metadata(doc).get("namespace"), name_of(doc))
        # A repeated candidate must fail even when identical; it cannot be
        # mistaken for a second legitimate replacement of one live claim.
        if key in seen_candidates:
            failures.append(f"FAIL: {path}: duplicate candidate Database claim")
            continue
        seen_candidates.add(key)
        record(path, doc, scope, replacing=inventory is not None)

    owners = {}
    for (scope, namespace, name), (path, producers) in claims.items():
        for component, identities in producers.items():
            owner = f"Database/{namespace}/{name} {component}"
            for identity in sorted(identities):
                key = (scope, *identity)
                if key in owners:
                    other, other_path = owners[key]
                    failures.append(
                        f"FAIL: {path}: producer identity collision on {identity[0]} {identity[1]}: "
                        f"{owner} conflicts with {other} ({other_path}); do not rename live resources automatically"
                    )
                else:
                    owners[key] = (owner, path)
    return failures


def is_cloudsql_candidate(doc: dict[str, Any]) -> bool:
    """The GCP logical Database recipe shares the GVK, not the AWS producer contract.

    This exclusion is candidate-only. Inventory mode is explicitly one AWS shared
    target and must reject these shapes instead of silently omitting an owner.
    AWS's XRD requires env (without a default), so absence cannot select its
    producer successfully. Mixed env/instanceRef input must never use this exit.
    """
    db = spec(doc)
    ref = db.get("instanceRef")
    return ("env" not in db and "purpose" not in db and "additionalUsers" not in db
            and isinstance(ref, dict) and set(ref) == {"name"}
            and isinstance(ref["name"], str) and bool(ref["name"].strip())
            and db.get("engine") in {"mysql", "postgres"})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--inventory", type=Path, help="complete Database List JSON from the exact target; use a single-target render")
    args = parser.parse_args()
    root = args.directory
    if not root.is_dir():
        print(f"FAIL: directory not found: {root}", file=sys.stderr)
        return 2

    failures: list[str] = []
    checked_docs = 0
    candidates: list[tuple[Path, dict[str, Any]]] = []

    for path in iter_yaml_files(root):
        docs = []
        for doc in load_docs(path):
            if isinstance(doc, dict) and doc.get("kind") in ("List", "DatabaseList"):
                items = doc.get("items")
                if not isinstance(items, list) or len(items) > MAX_INVENTORY_ITEMS:
                    failures.append(f"FAIL: {path}: Kubernetes List.items is missing or exceeds 10000 objects")
                    continue
                if any(not isinstance(item, dict) or item.get("kind") in ("List", "DatabaseList") for item in items):
                    failures.append(f"FAIL: {path}: Kubernetes List.items contains an invalid/nested object")
                    continue
                docs.extend(items)
            else:
                docs.append(doc)
        for doc in docs:
            checked, doc_failures = check_doc(path, doc)
            checked_docs += checked
            failures.extend(doc_failures)
            if isinstance(doc, dict) and doc.get("kind") == "Database" and api_group(doc) == PLATFORM_DATABASE_GROUP:
                if args.inventory or not is_cloudsql_candidate(doc):
                    candidates.append((path, doc))

    inventory = None
    if args.inventory:
        try:
            inventory = load_database_inventory(args.inventory)
        except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
            print(f"FAIL: invalid Database inventory: {type(exc).__name__}")
            return 2
        except ValueError as exc:
            print(f"FAIL: invalid Database inventory: {exc}")
            return 2
    failures.extend(check_database_identities(candidates, inventory, args.inventory))

    if failures:
        for failure in failures:
            print(failure)
        print(f"FAIL: {len(failures)} DB resource contract violation(s)")
        return 1

    print(f"PASS: checked {checked_docs} YAML document(s), no DB contract violations")
    if inventory is None:
        print("Database identity scope: candidates per manifest directory only; cross-repository inventory not checked")
    else:
        print("Database identity scope: all candidates plus supplied inventory; context/freshness/effective Composition contract must be verified by the caller")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
