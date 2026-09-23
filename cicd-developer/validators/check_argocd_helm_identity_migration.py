#!/usr/bin/env python3
"""Validate an opt-in Argo CD identity-migration namespace contract.

Usage:
  check_argocd_helm_identity_migration.py <cluster-dir> \
    --source-application <legacy-source-application.yaml> \
    [--bootstrap-application <bootstrap-application.yaml> \
     --bootstrap-source-root <checked-out-bootstrap-git-root> \
     --bootstrap-render <exact-rendered-output.yaml-or-directory>] \
    [--target-project-namespace-owner \
     --target-application <target-application.yaml> \
     --target-render <exact-rendered-output.yaml-or-directory> \
     --target-source-root <checked-out-target-git-root>] \
    --verify-remote-host <approved-git-host>

The default, backwards-compatible mode requires the exact standalone bootstrap
Application, its immutable source checkout, and its rendered protected
Namespace. ``--target-project-namespace-owner`` is an explicit, mutually
exclusive alternative: it requires the source and target Applications plus the
target checkout/render, proves that the exact non-default target AppProject
permits core ``/Namespace``, and requires ``CreateNamespace=true`` on the target.
The mode is never inferred from omitted bootstrap inputs.

In both modes the target must be a separate direct raw-YAML Git source at an
immutable commit. Regular Helm chart repository sources are rejected because a
SemVer does not pin an immutable chart artifact; OCI digest sources require a
separate future contract. Fresh remote proof is performed only against
caller-approved ``--verify-remote-host`` values. This is deliberately separate
from ``check_argocd_namespace_creation.py``: it validates one parallel identity
migration rather than changing the incremental fleet-wide namespace gate.

Exit codes: 0 pass, 1 contract violation, 2 usage/dependency error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath
from urllib.parse import ParseResult, unquote, urlparse

try:
    import yaml
except ImportError:
    print("FAIL: PyYAML not installed", file=sys.stderr)
    sys.exit(2)

from _scan import ParseError, is_application, iter_docs


CREATE_NAMESPACE = "CreateNamespace"
SYNC_WAVE = "argocd.argoproj.io/sync-wave"
SYNC_OPTIONS = "argocd.argoproj.io/sync-options"
SKIP_FILE_RENDERING = "+argocd:skip-file-rendering"
ADMISSION_REGISTRATION_API_GROUP = "admissionregistration.k8s.io"
RBAC_AUTHORIZATION_API_GROUP = "rbac.authorization.k8s.io"
STORAGE_API_GROUP = "storage.k8s.io"
EXTERNAL_SECRETS_API_GROUP = "external-secrets.io"
ARGO_API_GROUP = "argoproj.io"
UNNAMED_RESOURCE_NAME = "<unnamed>"
YAML_FILE_SUFFIXES = frozenset({".yaml", ".yml"})
TEMPLATE_OPEN_DELIMITER = "{{"
TEMPLATE_CLOSE_DELIMITER = "}}"
RAW_TEMPLATE_PATH = "<raw>"
VECTOR_CONFIG_DATA_KEY = "vector.yaml"
VECTOR_TEMPLATE_ACTION = re.compile(
    r"\{\{\s*(?P<field>[A-Za-z_][A-Za-z0-9_.-]*)\s*\}\}"
)
ALLOWED_VECTOR_RUNTIME_TEMPLATE_FIELDS = frozenset({"topic"})
DIRECT_TOOL_MARKERS = {
    "Chart.yaml",
    "kustomization.yaml",
    "kustomization.yml",
    "Kustomization",
}
DIRECT_GIT_SOURCE_FIELDS = frozenset({"repoURL", "path", "targetRevision"})
SAFE_CREDENTIAL_HELPERS = frozenset(
    {
        "cache",
        "libsecret",
        "manager",
        "manager-core",
        "osxkeychain",
        "store",
        "wincred",
    }
)
MAX_GO_FILEPATH_PATTERN_LENGTH = 256
MAX_CLUSTER_RESOURCE_RULES_PER_FIELD = 128
MAX_CLUSTER_RESOURCE_PATTERN_CHARS_PER_FIELD = 4096
MAX_DECODED_YAML_VALUES = 10000


# The target-side render gate is deliberately fail-closed.  Kubernetes scope
# is a GVK property rather than a metadata property, so an omitted
# ``metadata.namespace`` is valid for a known namespaced workload (Argo CD
# supplies the destination namespace), while a cluster-scoped resource must
# never sneak through a namespace-only migration.  Keep this set focused on
# the ordinary workload resources this workflow is intended to migrate; add a
# verified GVK with a regression test instead of assuming an unknown CR is
# namespaced.
KNOWN_CLUSTER_SCOPED_RESOURCES = {
    ("", "Namespace"),
    ("", "Node"),
    ("", "PersistentVolume"),
    ("", "ComponentStatus"),
    (ADMISSION_REGISTRATION_API_GROUP, "MutatingWebhookConfiguration"),
    (ADMISSION_REGISTRATION_API_GROUP, "ValidatingWebhookConfiguration"),
    (ADMISSION_REGISTRATION_API_GROUP, "ValidatingAdmissionPolicy"),
    (ADMISSION_REGISTRATION_API_GROUP, "ValidatingAdmissionPolicyBinding"),
    ("apiextensions.k8s.io", "CustomResourceDefinition"),
    ("apiregistration.k8s.io", "APIService"),
    ("flowcontrol.apiserver.k8s.io", "FlowSchema"),
    ("flowcontrol.apiserver.k8s.io", "PriorityLevelConfiguration"),
    ("gateway.networking.k8s.io", "GatewayClass"),
    ("keda.sh", "ClusterTriggerAuthentication"),
    ("kyverno.io", "ClusterPolicy"),
    ("networking.k8s.io", "IngressClass"),
    ("node.k8s.io", "RuntimeClass"),
    ("policy", "PodSecurityPolicy"),
    (RBAC_AUTHORIZATION_API_GROUP, "ClusterRole"),
    (RBAC_AUTHORIZATION_API_GROUP, "ClusterRoleBinding"),
    ("scheduling.k8s.io", "PriorityClass"),
    (STORAGE_API_GROUP, "CSIDriver"),
    (STORAGE_API_GROUP, "CSINode"),
    (STORAGE_API_GROUP, "StorageClass"),
    (STORAGE_API_GROUP, "VolumeAttachment"),
    (EXTERNAL_SECRETS_API_GROUP, "ClusterExternalSecret"),
    (EXTERNAL_SECRETS_API_GROUP, "ClusterSecretStore"),
    ("cert-manager.io", "ClusterIssuer"),
    (ARGO_API_GROUP, "ClusterAnalysisTemplate"),
    (ARGO_API_GROUP, "ClusterWorkflowTemplate"),
    ("karpenter.sh", "NodePool"),
    ("karpenter.k8s.aws", "EC2NodeClass"),
}

KNOWN_NAMESPACED_RESOURCES = {
    "": {
        "Binding",
        "ConfigMap",
        "Endpoints",
        "Event",
        "LimitRange",
        "PersistentVolumeClaim",
        "Pod",
        "PodTemplate",
        "ReplicationController",
        "ResourceQuota",
        "Secret",
        "Service",
        "ServiceAccount",
    },
    "apps": {
        "ControllerRevision",
        "DaemonSet",
        "Deployment",
        "ReplicaSet",
        "StatefulSet",
    },
    ARGO_API_GROUP: {"AnalysisRun", "AnalysisTemplate", "Experiment", "Rollout"},
    "autoscaling": {"HorizontalPodAutoscaler", "VerticalPodAutoscaler"},
    "batch": {"CronJob", "Job"},
    "cert-manager.io": {"Certificate", "CertificateRequest", "Issuer"},
    EXTERNAL_SECRETS_API_GROUP: {"ExternalSecret", "PushSecret", "SecretStore"},
    "gateway.networking.k8s.io": {
        "BackendTLSPolicy",
        "GRPCRoute",
        "Gateway",
        "HTTPRoute",
        "ReferenceGrant",
        "TCPRoute",
        "TLSRoute",
    },
    "keda.sh": {"ScaledJob", "ScaledObject", "TriggerAuthentication"},
    "monitoring.coreos.com": {
        "Alertmanager",
        "PodMonitor",
        "Probe",
        "Prometheus",
        "PrometheusRule",
        "ScrapeConfig",
        "ServiceMonitor",
        "ThanosRuler",
    },
    "networking.k8s.io": {"Ingress", "NetworkPolicy"},
    "policy": {"PodDisruptionBudget"},
    RBAC_AUTHORIZATION_API_GROUP: {"Role", "RoleBinding"},
    "snapshot.storage.k8s.io": {"VolumeSnapshot", "VolumeSnapshotContentClass"},
}


@dataclass(frozen=True)
class Resource:
    file: Path
    doc: dict


@dataclass(frozen=True)
class GitSource:
    repo_url: str
    path: str
    revision: str


@dataclass(frozen=True)
class VerifiedGitCheckout:
    """A declared immutable Git source bound to its local commit object."""

    root: Path
    commit: str


class InputError(Exception):
    """Raised for an unreadable or ambiguous supplied input."""


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate one exact Argo CD / Helm identity migration using either "
            "the default standalone bootstrap or an explicit target-project "
            "Namespace owner"
        )
    )
    parser.add_argument("cluster_dir", type=Path)
    parser.add_argument(
        "--source-application",
        required=True,
        type=Path,
        help="legacy/source Application YAML whose namespace must remain isolated",
    )
    parser.add_argument("--bootstrap-application", type=Path)
    parser.add_argument(
        "--bootstrap-source-root",
        type=Path,
        help=(
            "checkout root for the bootstrap Application's exact Git "
            "source and targetRevision"
        ),
    )
    parser.add_argument(
        "--target-project-namespace-owner",
        action="store_true",
        help=(
            "explicitly use the target Application's exact AppProject as the "
            "Namespace owner; mutually exclusive with every bootstrap input"
        ),
    )
    parser.add_argument(
        "--target-application",
        type=Path,
        help="target Application YAML; required for the Add-MR pair gate",
    )
    parser.add_argument(
        "--target-render",
        type=Path,
        help="exact rendered target YAML file or YAML-only directory for the Add-MR pair gate",
    )
    parser.add_argument(
        "--target-source-root",
        type=Path,
        help=(
            "checkout root for a single direct-YAML target Git source; "
            "required for the supported strict target source form"
        ),
    )
    parser.add_argument(
        "--verify-remote-host",
        action="append",
        default=[],
        metavar="HOST",
        help=(
            "explicitly approved Git host for the temporary fresh remote proof; "
            "repeat for every approved host"
        ),
    )
    parser.add_argument(
        "--bootstrap-render",
        type=Path,
        help="exact rendered bootstrap YAML file or directory; never a raw source scan",
    )
    return parser.parse_args(argv[1:])


def normalized(path: Path) -> Path:
    return path.resolve(strict=False)


def is_below(path: Path, directory: Path) -> bool:
    try:
        normalized(path).relative_to(normalized(directory))
    except ValueError:
        return False
    return True


def resolve_candidate(root: Path, value: Path, option: str) -> Path:
    path = value if value.is_absolute() else root / value
    if not path.is_file():
        raise InputError(f"{option} file not found: {path}")
    if not is_below(path, root):
        raise InputError(f"{option} must be inside cluster directory: {path}")
    return normalized(path)


def load_yaml_file(path: Path) -> list[object]:
    try:
        return [doc for doc in yaml.safe_load_all(path.read_text(encoding="utf-8")) if doc is not None]
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise InputError(f"cannot parse YAML {path}: {exc}") from exc


def load_exact_application(path: Path, label: str) -> Resource:
    docs = load_yaml_file(path)
    if len(docs) != 1 or not is_application(docs[0]):
        raise InputError(
            f"{label} must contain exactly one Argo CD Application document: {path}"
        )
    return Resource(path, docs[0])


def is_appproject(doc: object) -> bool:
    return (
        isinstance(doc, dict)
        and doc.get("kind") == "AppProject"
        and str(doc.get("apiVersion") or "").startswith(f"{ARGO_API_GROUP}/")
    )


def resource_name(doc: dict) -> str:
    metadata = doc.get("metadata")
    if not isinstance(metadata, dict):
        return ""
    return str(metadata.get("name") or "")


def application_identity(app: dict) -> tuple[str, str] | None:
    metadata = app.get("metadata")
    if not isinstance(metadata, dict):
        return None
    name = metadata.get("name")
    namespace = metadata.get("namespace")
    if not isinstance(name, str) or not name.strip():
        return None
    if not isinstance(namespace, str) or not namespace.strip():
        return None
    return namespace.strip(), name.strip()


def spec_mapping(doc: dict) -> dict:
    value = doc.get("spec")
    return value if isinstance(value, dict) else {}


def collect_projects(root: Path) -> tuple[dict[tuple[str, str], list[Resource]], list[str]]:
    projects: dict[tuple[str, str], list[Resource]] = {}
    failures: list[str] = []
    for file, doc in iter_docs(root):
        if isinstance(doc, ParseError):
            failures.append(f"FAIL: {file}: {doc.message}")
            continue
        if is_appproject(doc):
            identity = application_identity(doc)
            if identity is not None:
                projects.setdefault(identity, []).append(Resource(file, doc))
    return projects, failures


def one_project(
    projects: dict[tuple[str, str], list[Resource]],
    name: str,
    control_namespace: str,
) -> tuple[Resource | None, str | None]:
    matches = projects.get((control_namespace, name)) or []
    if not matches:
        return None, (
            f"AppProject/{name or '<empty>'} is absent from control-plane namespace "
            f"{control_namespace!r} in the supplied cluster directory"
        )
    if len(matches) > 1:
        files = ", ".join(str(item.file) for item in matches)
        return None, f"AppProject/{name} has multiple definitions: {files}"
    return matches[0], None


def namespace_rule_matches(entry: object, namespace: str) -> bool:
    if not isinstance(entry, dict):
        return False
    if entry.get("group") not in {"", "*"}:
        return False
    if entry.get("kind") not in {"Namespace", "*"}:
        return False
    name_pattern = entry.get("name", "*")
    return isinstance(name_pattern, str) and fnmatchcase(namespace, name_pattern)


def project_allows_namespace(project: dict, namespace: str) -> bool:
    """Return whether the AppProject permits this exact core Namespace."""
    spec = spec_mapping(project)
    whitelist = spec.get("clusterResourceWhitelist") or []
    blacklist = spec.get("clusterResourceBlacklist") or []
    if not any(namespace_rule_matches(entry, namespace) for entry in whitelist):
        return False
    return not any(namespace_rule_matches(entry, namespace) for entry in blacklist)


def go_filepath_class_character(
    pattern: str,
    position: int,
) -> tuple[str, int] | None:
    """Read one literal or escaped character inside a Go filepath class."""
    if position >= len(pattern) or pattern[position] in {"-", "]"}:
        return None
    if pattern[position] != "\\":
        return pattern[position], position + 1
    if position + 1 >= len(pattern):
        return None
    return pattern[position + 1], position + 2


def go_filepath_class_range(
    pattern: str,
    position: int,
) -> tuple[tuple[str, str], int] | None:
    """Read one character or lo-hi range from a Go filepath class."""
    first = go_filepath_class_character(pattern, position)
    if first is None:
        return None
    lower, next_position = first
    if next_position >= len(pattern) or pattern[next_position] != "-":
        return (lower, lower), next_position
    second = go_filepath_class_character(pattern, next_position + 1)
    if second is None:
        return None
    upper, next_position = second
    return (lower, upper), next_position


def go_filepath_class(
    pattern: str,
    start: int,
) -> tuple[int, bool, list[tuple[str, str]]] | None:
    """Parse one Go ``path/filepath.Match`` character class."""
    index = start + 1
    negated = index < len(pattern) and pattern[index] == "^"
    index += int(negated)
    ranges: list[tuple[str, str]] = []
    while index < len(pattern) and pattern[index] != "]":
        parsed_range = go_filepath_class_range(pattern, index)
        if parsed_range is None:
            return None
        character_range, index = parsed_range
        ranges.append(character_range)
    if not ranges or index >= len(pattern):
        return None
    return index + 1, negated, ranges


def go_filepath_pattern_is_valid(pattern: str) -> bool:
    """Validate all pattern syntax even when an early literal cannot match."""
    if len(pattern) > MAX_GO_FILEPATH_PATTERN_LENGTH:
        return False
    index = 0
    while index < len(pattern):
        if pattern[index] == "\\":
            if index + 1 >= len(pattern):
                return False
            index += 2
        elif pattern[index] == "[":
            parsed = go_filepath_class(pattern, index)
            if parsed is None:
                return False
            index = parsed[0]
        else:
            index += 1
    return True


class GoFilepathMatcher:
    """Memoized matcher for one syntax-validated Linux Go filepath pattern."""

    def __init__(self, pattern: str, value: str) -> None:
        self.pattern = pattern
        self.value = value
        self.memo: dict[tuple[int, int], bool] = {}

    def match(self, pattern_index: int = 0, value_index: int = 0) -> bool:
        key = (pattern_index, value_index)
        if key not in self.memo:
            self.memo[key] = self._match_token(pattern_index, value_index)
        return self.memo[key]

    def _match_token(self, pattern_index: int, value_index: int) -> bool:
        if pattern_index == len(self.pattern):
            return value_index == len(self.value)
        token = self.pattern[pattern_index]
        if token == "*":
            return self._match_star(pattern_index, value_index)
        if token == "?":
            return self._match_single(pattern_index + 1, value_index)
        if token == "[":
            return self._match_class(pattern_index, value_index)
        if token == "\\":
            return self._match_escaped(pattern_index, value_index)
        return self._match_literal(token, pattern_index + 1, value_index)

    def _match_star(self, pattern_index: int, value_index: int) -> bool:
        cursor = value_index
        while True:
            if self.match(pattern_index + 1, cursor):
                return True
            if cursor >= len(self.value) or self.value[cursor] == "/":
                return False
            cursor += 1

    def _match_single(self, next_pattern_index: int, value_index: int) -> bool:
        return (
            value_index < len(self.value)
            and self.value[value_index] != "/"
            and self.match(next_pattern_index, value_index + 1)
        )

    def _match_class(self, pattern_index: int, value_index: int) -> bool:
        parsed = go_filepath_class(self.pattern, pattern_index)
        if parsed is None or value_index >= len(self.value):
            return False
        next_pattern_index, negated, ranges = parsed
        in_class = any(
            lower <= self.value[value_index] <= upper
            for lower, upper in ranges
        )
        return in_class != negated and self.match(
            next_pattern_index,
            value_index + 1,
        )

    def _match_escaped(self, pattern_index: int, value_index: int) -> bool:
        return self._match_literal(
            self.pattern[pattern_index + 1],
            pattern_index + 2,
            value_index,
        )

    def _match_literal(
        self,
        literal: str,
        next_pattern_index: int,
        value_index: int,
    ) -> bool:
        return (
            value_index < len(self.value)
            and self.value[value_index] == literal
            and self.match(next_pattern_index, value_index + 1)
        )


def go_filepath_match(pattern: str, value: str) -> bool | None:
    """Match safe bounded Linux Go ``filepath.Match`` syntax, else fail closed."""
    if len(pattern) > MAX_GO_FILEPATH_PATTERN_LENGTH:
        return None
    if not go_filepath_pattern_is_valid(pattern):
        return None
    return GoFilepathMatcher(pattern, value).match()


def go_filepath_pattern_failure(pattern: str) -> str | None:
    """Explain unsafe complexity separately from malformed Go pattern syntax."""
    if len(pattern) > MAX_GO_FILEPATH_PATTERN_LENGTH:
        return (
            "exceeds safe Go filepath.Match pattern length "
            f"{MAX_GO_FILEPATH_PATTERN_LENGTH}"
        )
    if not go_filepath_pattern_is_valid(pattern):
        return "is not valid Go filepath.Match syntax"
    return None


def cluster_resource_rule_list(
    project: dict,
    field: str,
) -> tuple[list[dict] | None, str | None]:
    """Load one bounded owner-mode AppProject rule list."""
    spec = spec_mapping(project)
    if field not in spec:
        return [], None
    entries = spec[field]
    if not isinstance(entries, list):
        return None, f"{field} must be a list"
    if len(entries) > MAX_CLUSTER_RESOURCE_RULES_PER_FIELD:
        return None, (
            f"{field} has {len(entries)} rules; safe owner-mode limit is "
            f"{MAX_CLUSTER_RESOURCE_RULES_PER_FIELD}"
        )
    return entries, None


def cluster_resource_entry_mapping(
    entry: object,
    field: str,
    index: int,
) -> tuple[dict | None, str | None]:
    """Require one rule to be a mapping with only supported fields."""
    if not isinstance(entry, dict):
        return None, f"{field}[{index}] must be a mapping"
    if set(entry) - {"group", "kind", "name"}:
        return None, f"{field}[{index}] contains unsupported fields"
    return entry, None


def cluster_resource_group_kind_patterns(
    entry: dict,
    field: str,
    index: int,
) -> tuple[list[tuple[int, str, str]] | None, str | None]:
    """Validate and return one rule's required GroupKind patterns."""
    group = entry.get("group")
    kind = entry.get("kind")
    if not isinstance(group, str) or not isinstance(kind, str):
        return None, f"{field}[{index}] must declare string group and kind"
    if not kind or group != group.strip() or kind != kind.strip():
        return None, f"{field}[{index}] has an invalid group or kind"
    return [(index, "group", group), (index, "kind", kind)], None


def cluster_resource_name_pattern(
    entry: dict,
    field: str,
    index: int,
) -> tuple[tuple[int, str, str] | None, str | None]:
    """Validate and return one rule's optional resource-name pattern."""
    if "name" not in entry:
        return None, None
    name = entry["name"]
    if not isinstance(name, str) or not name:
        return None, f"{field}[{index}].name must be a nonempty string"
    return (index, "name", name), None


def cluster_resource_entry_patterns(
    entry: object,
    field: str,
    index: int,
) -> tuple[list[tuple[int, str, str]] | None, str | None]:
    """Validate one rule schema and return its ordered pattern fields."""
    mapping, mapping_error = cluster_resource_entry_mapping(entry, field, index)
    if mapping_error is not None or mapping is None:
        return None, mapping_error
    patterns, group_kind_error = cluster_resource_group_kind_patterns(
        mapping,
        field,
        index,
    )
    if group_kind_error is not None or patterns is None:
        return None, group_kind_error
    name_pattern, name_error = cluster_resource_name_pattern(mapping, field, index)
    if name_error is not None:
        return None, name_error
    if name_pattern is not None:
        patterns.append(name_pattern)
    return patterns, None


def cluster_resource_pattern_inventory(
    entries: list[dict],
    field: str,
) -> tuple[list[tuple[int, str, str]] | None, str | None]:
    """Validate every rule schema, then enforce the cumulative pattern budget."""
    pattern_values: list[tuple[int, str, str]] = []
    for index, entry in enumerate(entries):
        entry_patterns, entry_error = cluster_resource_entry_patterns(
            entry,
            field,
            index,
        )
        if entry_error is not None or entry_patterns is None:
            return None, entry_error
        pattern_values.extend(entry_patterns)
    cumulative_pattern_chars = sum(len(pattern) for _, _, pattern in pattern_values)
    if cumulative_pattern_chars > MAX_CLUSTER_RESOURCE_PATTERN_CHARS_PER_FIELD:
        return None, (
            f"{field} has {cumulative_pattern_chars} cumulative pattern characters; "
            f"safe owner-mode limit is "
            f"{MAX_CLUSTER_RESOURCE_PATTERN_CHARS_PER_FIELD}"
        )
    return pattern_values, None


def cluster_resource_pattern_syntax_failure(
    pattern_values: list[tuple[int, str, str]],
    field: str,
) -> str | None:
    """Return the first single-pattern syntax/length failure in field order."""
    for index, key, pattern in pattern_values:
        pattern_failure = go_filepath_pattern_failure(pattern)
        if pattern_failure is not None:
            return f"{field}[{index}].{key} {pattern_failure}"
    return None


def strict_cluster_resource_rules(
    project: dict,
    field: str,
) -> tuple[list[dict] | None, str | None]:
    """Load owner rules in count, schema/budget, then syntax-check order."""
    entries, entries_error = cluster_resource_rule_list(project, field)
    if entries_error is not None or entries is None:
        return None, entries_error
    patterns, inventory_error = cluster_resource_pattern_inventory(entries, field)
    if inventory_error is not None or patterns is None:
        return None, inventory_error
    syntax_error = cluster_resource_pattern_syntax_failure(patterns, field)
    if syntax_error is not None:
        return None, syntax_error
    return entries, None


def exact_namespace_whitelist_failure(whitelist: list[dict]) -> str | None:
    """Require exactly one unconstrained core Namespace permission entry."""
    expected = {"group": "", "kind": "Namespace"}
    if sum(entry == expected for entry in whitelist) == 1:
        return None
    return (
        "must contain exactly one {group: '', kind: Namespace} whitelist "
        "entry with no name or additional constraints"
    )


def blacklist_rule_matches_namespace_kind(entry: dict) -> bool:
    """Return whether a validated blacklist GroupKind applies to Namespace."""
    group_matches = go_filepath_match(entry["group"], "")
    kind_matches = go_filepath_match(entry["kind"], "Namespace")
    assert group_matches is not None
    assert kind_matches is not None
    return group_matches and kind_matches


def namespace_blacklist_failure(
    blacklist: list[dict],
    namespace: str,
) -> str | None:
    """Return the first validated blacklist rule denying the target Namespace."""
    for index, entry in enumerate(blacklist):
        if not blacklist_rule_matches_namespace_kind(entry):
            continue
        pattern = entry.get("name", "*")
        assert isinstance(pattern, str)
        matches = go_filepath_match(pattern, namespace)
        if matches is None:
            return (
                f"clusterResourceBlacklist[{index}].name is not valid "
                "Go filepath.Match syntax"
            )
        if matches:
            return f"clusterResourceBlacklist[{index}] denies Namespace/{namespace}"
    return None


def target_owner_namespace_permission_failure(
    project: dict,
    namespace: str,
) -> str | None:
    """Prove exact owner authority from one entry and a strict deny list."""
    whitelist, whitelist_error = strict_cluster_resource_rules(
        project,
        "clusterResourceWhitelist",
    )
    if whitelist_error is not None or whitelist is None:
        return whitelist_error
    blacklist, blacklist_error = strict_cluster_resource_rules(
        project,
        "clusterResourceBlacklist",
    )
    if blacklist_error is not None or blacklist is None:
        return blacklist_error
    whitelist_error = exact_namespace_whitelist_failure(whitelist)
    if whitelist_error is not None:
        return whitelist_error
    return namespace_blacklist_failure(blacklist, namespace)


def source_repository_rule_match(
    raw_pattern: object,
    repo_url: str,
) -> tuple[bool, bool]:
    """Return whether one ``sourceRepos`` rule matches and denies a remote."""
    if not isinstance(raw_pattern, str):
        return False, False
    pattern = raw_pattern.strip()
    if not pattern:
        return False, False
    denied = pattern.startswith("!")
    pattern = pattern.removeprefix("!")
    if not pattern:
        return False, False
    return fnmatchcase(repo_url.rstrip("/"), pattern.rstrip("/")), denied


def project_allows_source(project: dict, repo_url: str) -> bool:
    """Return whether an AppProject permits the exact Application repo URL.

    Argo CD supports allow and deny patterns in ``sourceRepos``. A matching
    deny entry must win even if an earlier allow entry also matches.
    """
    source_repos = spec_mapping(project).get("sourceRepos") or []
    if not isinstance(source_repos, list):
        return False

    allowed = False
    for raw_pattern in source_repos:
        matches, denied = source_repository_rule_match(raw_pattern, repo_url)
        if not matches:
            continue
        if denied:
            return False
        allowed = True
    return allowed


def destination_rule_match(
    entry: object,
    destination_value: tuple[str, str],
) -> tuple[bool, bool]:
    """Return whether an AppProject destination rule matches and denies a pair."""
    if not isinstance(entry, dict):
        return False, False
    server_pattern = entry.get("server")
    namespace_pattern = entry.get("namespace")
    if not isinstance(server_pattern, str) or not isinstance(namespace_pattern, str):
        return False, False
    server_pattern = server_pattern.strip()
    namespace_pattern = namespace_pattern.strip()
    if not server_pattern or not namespace_pattern:
        return False, False
    denied = server_pattern.startswith("!") or namespace_pattern.startswith("!")
    server_pattern = server_pattern.removeprefix("!")
    namespace_pattern = namespace_pattern.removeprefix("!")
    if not server_pattern or not namespace_pattern:
        return False, False
    server, namespace = destination_value
    matches = (
        fnmatchcase(server.rstrip("/"), server_pattern.rstrip("/"))
        and fnmatchcase(namespace, namespace_pattern)
    )
    return matches, denied


def project_allows_destination(
    project: dict,
    value: tuple[str, str],
) -> bool:
    """Return whether an AppProject permits an exact server/namespace pair.

    Argo CD destination permissions use the same allow-and-no-deny contract as
    ``sourceRepos``. A matching negated server or namespace rule rejects the
    pair even when a positive rule also matches it.
    """
    destinations = spec_mapping(project).get("destinations") or []
    if not isinstance(destinations, list):
        return False

    allowed = False
    for entry in destinations:
        matches, denied = destination_rule_match(entry, value)
        if not matches:
            continue
        if denied:
            return False
        allowed = True
    return allowed


def application_sources(app: dict) -> tuple[list[dict] | None, str | None]:
    """Return declared Argo CD sources, rejecting ambiguous source shapes."""
    spec = spec_mapping(app)
    source = spec.get("source")
    sources = spec.get("sources")
    if source is not None and sources is not None:
        return None, "must use either spec.source or spec.sources, not both"
    if isinstance(source, dict):
        return [source], None
    if isinstance(sources, list) and all(isinstance(item, dict) for item in sources):
        return sources, None
    return None, "must declare spec.source or spec.sources as Application source mappings"


def source_hydrator_failure(app: dict, prefix: str) -> str | None:
    """Reject Argo CD source hydration from the strict migration path.

    ``spec.sourceHydrator`` can materialize a separate sync source from the
    declared dry source.  That breaks the invariant that this gate verifies
    the exact Git tree Argo CD will apply, so it is never permitted for either
    the bootstrap or the target Application.
    """
    if "sourceHydrator" in spec_mapping(app):
        return f"{prefix} must not declare spec.sourceHydrator in strict identity migration"
    return None


def operation_failure(app: dict, prefix: str) -> str | None:
    """Reject an Application operation that can override the declared source.

    Argo CD's top-level ``operation.sync`` accepts revision/source/sources and
    sync-option overrides.  Its presence would make the Git source and render
    proven below advisory rather than authoritative.
    """
    if "operation" in app:
        return f"{prefix} must not declare top-level operation in strict identity migration"
    return None


def record_application_source_guard_failures(
    app: dict,
    prefix: str,
    failures: list[str],
) -> None:
    """Record source-hydrator and operation guards in their required order."""
    hydrator_failure = source_hydrator_failure(app, prefix)
    if hydrator_failure is not None:
        failures.append(hydrator_failure)
    operation_error = operation_failure(app, prefix)
    if operation_error is not None:
        failures.append(operation_error)


def repository_url_has_embedded_credentials(value: str) -> bool:
    """Reject credential-bearing Git/chart source URLs in GitOps."""
    parsed = urlparse(value.strip())
    scheme = parsed.scheme.lower()
    if not scheme:
        # Scp-like Git remotes have no password grammar.  Their username is
        # still part of the URL, so admit only the conventional non-secret
        # ``git@host:path`` form.
        match = re.fullmatch(
            r"(?:(?P<user>[^@/\s]+)@)?(?P<host>[^:/\s]+):(?P<path>[^?#\s]+)",
            value.strip(),
        )
        return match is None or (match.group("user") not in {None, "git"})
    if parsed.password is not None:
        return True
    if scheme in {"http", "https"}:
        return parsed.username is not None or bool(parsed.query) or bool(parsed.fragment)
    if scheme == "git":
        return parsed.username is not None
    if scheme == "ssh":
        return parsed.username not in {None, "git"}
    return parsed.username is not None


def canonical_git_path(value: str) -> str | None:
    """Normalize a decoded Git repository path without accepting an empty one."""
    path = value.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    path = path.rstrip("/")
    return path or None


def canonical_url_git_repo(
    parsed: ParseResult,
) -> tuple[str, str, int | None, str] | None:
    """Canonicalize a standard URL-form Git remote."""
    scheme = parsed.scheme.lower()
    if scheme not in {"git", "http", "https", "ssh"}:
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    path = canonical_git_path(unquote(parsed.path))
    if not host or path is None or parsed.query or parsed.fragment:
        return None
    default_ports = {
        "git": 9418,
        "http": 80,
        "https": 443,
        "ssh": 22,
    }
    if port == default_ports[scheme]:
        port = None
    return scheme, host, port, path


def canonical_scp_git_repo(raw: str) -> tuple[str, str, int | None, str] | None:
    """Canonicalize a conventional ``git@host:path`` remote."""
    match = re.fullmatch(
        r"(?:[^@/\s]+@)?(?P<host>[^:/\s]+):(?P<path>[^?#\s]+)",
        raw,
    )
    if match is None:
        return None
    path = canonical_git_path(unquote(match.group("path")))
    if path is None:
        return None
    return "ssh", match.group("host").lower(), None, path


def canonical_git_repo(value: str) -> tuple[str, str, int | None, str] | None:
    """Canonicalize a Git remote URL while retaining its transport class."""
    raw = value.strip()
    if not raw:
        return None
    parsed = urlparse(raw)
    if parsed.scheme:
        return canonical_url_git_repo(parsed)
    return canonical_scp_git_repo(raw)


def safe_git_repository(value: str) -> tuple[str, str, int | None, str] | None:
    """Return a secure Git remote, rejecting insecure fetch transports."""
    canonical = canonical_git_repo(value)
    if canonical is None or canonical[0] not in {"https", "ssh"}:
        return None
    return canonical


def same_git_repository(left: str, right: str) -> bool:
    """Compare secure remotes, allowing only standard-port HTTPS/SSH parity."""
    left_canonical = safe_git_repository(left)
    right_canonical = safe_git_repository(right)
    if left_canonical is None or right_canonical is None:
        return False
    left_scheme, left_host, left_port, left_path = left_canonical
    right_scheme, right_host, right_port, right_path = right_canonical
    if (left_host, left_port, left_path) != (right_host, right_port, right_path):
        return False
    if left_scheme == right_scheme:
        return True
    return {left_scheme, right_scheme} == {"https", "ssh"} and left_port is None


def git_output(directory: Path, *args: str) -> str | None:
    """Run a read-only Git query without inheriting Git config overrides."""
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    environment["GIT_TERMINAL_PROMPT"] = "0"
    # A local refs/replace entry can make tree/blob reads resolve a different
    # object than the immutable SHA Argo CD will fetch.  Never honor those
    # developer-local substitutions in a provenance gate.
    environment["GIT_NO_REPLACE_OBJECTS"] = "1"
    # A fresh remote proof must not inherit user/system url.*.insteadOf rules
    # that could redirect the declared, explicitly approved Git host.
    environment["GIT_CONFIG_NOSYSTEM"] = "1"
    environment["GIT_CONFIG_GLOBAL"] = os.devnull
    # Do not allow a partial clone's locally configured promisor remote to
    # fetch a missing tree/blob outside the explicit proof-host boundary.
    environment["GIT_NO_LAZY_FETCH"] = "1"
    try:
        result = subprocess.run(
            ["git", "-C", str(directory), *args],
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def safe_global_credential_helpers() -> tuple[str, ...]:
    """Read only standard noninteractive credential helpers from global config.

    The proof fetch deliberately suppresses normal Git configuration so a
    local ``url.*.insteadOf`` cannot redirect an approved host.  Restoring an
    arbitrary helper would reintroduce shell-command execution, so retain only
    a small set of standard helper names.  Values are never logged.
    """
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    environment["GIT_TERMINAL_PROMPT"] = "0"
    environment["GIT_CONFIG_NOSYSTEM"] = "1"
    try:
        result = subprocess.run(
            ["git", "config", "--global", "--get-all", "credential.helper"],
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        return ()
    if result.returncode not in {0, 1}:
        return ()
    return tuple(
        value
        for value in (line.strip() for line in result.stdout.splitlines())
        if value in SAFE_CREDENTIAL_HELPERS
    )


def git_binary_output(
    directory: Path,
    *args: str,
    timeout: int | None = None,
    credential_helpers: tuple[str, ...] = (),
) -> bytes | None:
    """Read Git bytes without trusting caller-controlled Git environment state."""
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    environment["GIT_TERMINAL_PROMPT"] = "0"
    environment["GIT_NO_REPLACE_OBJECTS"] = "1"
    environment["GIT_CONFIG_NOSYSTEM"] = "1"
    environment["GIT_CONFIG_GLOBAL"] = os.devnull
    environment["GIT_NO_LAZY_FETCH"] = "1"
    command = ["git", "-C", str(directory)]
    for helper in credential_helpers:
        command.extend(["-c", f"credential.helper={helper}"])
    command.extend(args)
    try:
        result = subprocess.run(
            command,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode == 0 else None


def canonical_relative_git_path(value: str) -> str | None:
    """Normalize an Application-controlled Git-tree path without pathspec semantics."""
    raw = value.strip()
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or not path.parts:
        return None
    if any(part in {"", ".", ".."} for part in path.parts):
        return None
    return "/".join(path.parts)


def approved_remote_hosts(values: list[str]) -> set[str] | None:
    """Validate the caller-approved Git egress boundary for fresh proof fetches."""
    hosts: set[str] = set()
    for value in values:
        host = value.strip().lower()
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host):
            return None
        hosts.add(host)
    return hosts


def git_tree_entries(
    checkout: VerifiedGitCheckout,
) -> dict[str, tuple[str, str, str]] | None:
    """Return the exact committed tree without interpreting any user path as a pathspec."""
    raw = git_binary_output(
        checkout.root,
        "ls-tree",
        "-r",
        "-z",
        "--full-tree",
        checkout.commit,
    )
    if raw is None:
        return None
    entries: dict[str, tuple[str, str, str]] = {}
    for record in raw.split(b"\0"):
        if not record:
            continue
        try:
            header, encoded_path = record.split(b"\t", 1)
            mode, object_type, object_id = header.decode("ascii").split(" ", 2)
            relative_path = encoded_path.decode("utf-8")
        except (UnicodeDecodeError, ValueError):
            return None
        if canonical_relative_git_path(relative_path) != relative_path:
            return None
        entries[relative_path] = (mode, object_type, object_id)
    return entries


def committed_blob_text(
    checkout: VerifiedGitCheckout,
    object_id: str,
) -> str | None:
    """Read a verified Git blob, never the potentially modified worktree file."""
    raw = git_binary_output(checkout.root, "cat-file", "blob", object_id)
    if raw is None:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def direct_git_source_value(
    source: dict,
    field: str,
    prefix: str,
    argument_label: str,
) -> tuple[str | None, str | None]:
    """Read one required direct-source field while retaining its diagnostic."""
    value = source.get(field)
    if not isinstance(value, str) or not value.strip():
        return None, f"{prefix} {argument_label} must declare a nonempty {field}"
    return value.strip(), None


def direct_git_source_values(
    source: dict,
    prefix: str,
    argument_label: str,
    policy_label: str,
) -> tuple[GitSource | None, str | None]:
    """Read the three required direct-Git fields in their contract order."""
    unsupported = sorted(key for key in source if key not in DIRECT_GIT_SOURCE_FIELDS)
    if unsupported:
        return None, (
            f"{prefix} {policy_label} must be a direct raw-YAML Git path; "
            f"unsupported field(s): {', '.join(unsupported)}"
        )
    repo_url, repo_error = direct_git_source_value(source, "repoURL", prefix, argument_label)
    if repo_error is not None or repo_url is None:
        return None, repo_error
    source_path, path_error = direct_git_source_value(source, "path", prefix, argument_label)
    if path_error is not None or source_path is None:
        return None, path_error
    revision, revision_error = direct_git_source_value(
        source,
        "targetRevision",
        prefix,
        argument_label,
    )
    if revision_error is not None or revision is None:
        return None, revision_error
    return GitSource(repo_url, source_path, revision), None


def strict_direct_git_source(
    source: dict,
    prefix: str,
    argument_label: str,
    policy_label: str,
) -> tuple[GitSource | None, str | None]:
    """Validate the exact direct raw-YAML Git source form shared by both roles."""
    if "directory" in source:
        return None, (
            f"{prefix} {argument_label} must omit spec.source.directory for canonical "
            "root-only raw-YAML rendering; Argo CD normalizes zero-valued directory "
            "objects such as {} and recurse: false away, while non-zero directory "
            "options change rendering; plain YAML is auto-detected non-recursively by default"
        )
    git_source, field_error = direct_git_source_values(
        source,
        prefix,
        argument_label,
        policy_label,
    )
    if field_error is not None or git_source is None:
        return None, field_error
    if repository_url_has_embedded_credentials(git_source.repo_url):
        return None, f"{prefix} {policy_label} repoURL must not embed credentials"
    if safe_git_repository(git_source.repo_url) is None:
        return None, f"{prefix} {policy_label} repoURL must be a valid secure Git remote URL"
    return git_source, None


def bootstrap_git_source(
    app: dict,
    prefix: str,
) -> tuple[GitSource | None, str | None]:
    """Return a direct raw-YAML Git source for the Namespace bootstrap."""
    if "sources" in spec_mapping(app):
        return None, (
            f"{prefix} bootstrap must declare its one direct source through "
            "spec.source, not spec.sources"
        )
    sources, source_error = application_sources(app)
    if source_error is not None or sources is None:
        return None, f"{prefix} {source_error}"
    if len(sources) != 1:
        return None, f"{prefix} must declare exactly one Git source for the Namespace bootstrap"

    source = sources[0]
    if source.get("chart") is not None:
        return None, f"{prefix} bootstrap source must use a Git path, not a Helm chart"
    return strict_direct_git_source(
        source,
        prefix,
        "bootstrap Git source",
        "bootstrap source",
    )


def source_permission_failure(
    project: Resource,
    source: GitSource,
    prefix: str,
    role: str,
) -> str | None:
    if project_allows_source(project.doc, source.repo_url):
        return None
    project_name = resource_name(project.doc) or UNNAMED_RESOURCE_NAME
    return f"{prefix} {role} source repository is not allowed by AppProject/{project_name} sourceRepos"


def target_source_repository(
    source: dict,
    prefix: str,
) -> tuple[str | None, str | None]:
    """Validate the target repository and revision before source-shape checks."""
    repo_url = source.get("repoURL")
    if not isinstance(repo_url, str) or not repo_url.strip():
        return None, f"{prefix} target source must declare a nonempty repoURL"
    if repository_url_has_embedded_credentials(repo_url):
        return None, f"{prefix} target source repoURL must not embed credentials"
    revision = source.get("targetRevision")
    if not isinstance(revision, str) or not revision.strip():
        return None, f"{prefix} target source must declare a nonempty targetRevision"
    return repo_url.strip(), None


def target_source_renders_manifests(
    source: dict,
    prefix: str,
) -> tuple[bool | None, str | None]:
    """Classify a target source as a renderer or values-only reference."""
    source_path = source.get("path")
    chart = source.get("chart")
    has_path = isinstance(source_path, str) and bool(source_path.strip())
    has_chart = isinstance(chart, str) and bool(chart.strip())
    if has_path and has_chart:
        return None, f"{prefix} target source must declare either path or chart, not both"
    if has_path or has_chart:
        return True, None
    ref = source.get("ref")
    if not isinstance(ref, str) or not ref.strip():
        return (
            None,
            f"{prefix} target source must declare a nonempty path or chart, or a values-only ref",
        )
    return False, None


def target_source_details(
    source: dict,
    prefix: str,
) -> tuple[str | None, bool, str | None]:
    """Validate one target source and report whether it can render manifests."""
    repo_url, repository_error = target_source_repository(source, prefix)
    if repository_error is not None or repo_url is None:
        return None, False, repository_error
    produces_manifests, shape_error = target_source_renders_manifests(source, prefix)
    if shape_error is not None or produces_manifests is None:
        return None, False, shape_error
    return repo_url, produces_manifests, None


def target_source_permission_failure(
    project: Resource,
    repo_url: str,
    prefix: str,
) -> str | None:
    """Return the existing target-source AppProject diagnostic when denied."""
    if project_allows_source(project.doc, repo_url):
        return None
    project_name = resource_name(project.doc) or UNNAMED_RESOURCE_NAME
    return (
        f"{prefix} target source repository is not allowed by "
        f"AppProject/{project_name} sourceRepos"
    )


def target_source_contract(
    source: dict,
    project: Resource,
    prefix: str,
) -> tuple[bool | None, str | None]:
    """Validate one target source's shape and AppProject authorization."""
    repo_url, produces_manifests, source_error = target_source_details(source, prefix)
    if source_error is not None or repo_url is None:
        return None, source_error
    permission_error = target_source_permission_failure(project, repo_url, prefix)
    if permission_error is not None:
        return None, permission_error
    return produces_manifests, None


def target_source_failure(
    app: dict,
    project: Resource,
    prefix: str,
) -> str | None:
    """Require every target Application source to be AppProject-authorized."""
    sources, source_error = application_sources(app)
    if source_error is not None or sources is None:
        return f"{prefix} {source_error}"
    if not sources:
        return f"{prefix} must declare at least one Application source"
    produces_manifests = False
    for source in sources:
        produces, source_error = target_source_contract(source, project, prefix)
        if source_error is not None or produces is None:
            return source_error
        produces_manifests = produces_manifests or produces
    if not produces_manifests:
        return f"{prefix} target Application must have at least one path or chart source"
    return None


def multi_source_has_helm_chart(sources: object) -> bool:
    """Return whether an explicit ``spec.sources`` form includes a Helm chart."""
    return isinstance(sources, list) and any(
        isinstance(item, dict)
        and isinstance(item.get("chart"), str)
        and item["chart"].strip()
        for item in sources
    )


def direct_target_git_source(
    app: dict,
    prefix: str,
) -> tuple[GitSource | None, str | None]:
    """Return the supported one-source direct-YAML target form, if present."""
    spec = spec_mapping(app)
    if "sources" in spec:
        multi_sources = spec.get("sources")
        if multi_source_has_helm_chart(multi_sources):
            # Preserve the explicit mutable-Helm diagnostic below.
            return None, None
        return None, (
            f"{prefix} target must declare its one direct source through "
            "spec.source, not spec.sources"
        )
    sources, source_error = application_sources(app)
    if source_error is not None or sources is None:
        return None, f"{prefix} {source_error}"
    if len(sources) != 1:
        return None, None
    source = sources[0]
    if source.get("chart") is not None:
        return None, None
    return strict_direct_git_source(
        source,
        prefix,
        "direct target source",
        "direct target source",
    )


def helm_target_source_failure(app: dict, prefix: str) -> str | None:
    """Reject mutable Helm-repository sources from the strict provenance path.

    An HTTPS chart version does not bind an artifact digest, so the chart host
    can serve different bytes to Argo CD after this local render. OCI digest
    source support needs a separate parser/renderer contract; it is deliberately
    not inferred from an ordinary ``chart`` source here.
    """
    sources, source_error = application_sources(app)
    if source_error is not None or sources is None:
        return f"{prefix} {source_error}"
    if any(isinstance(source.get("chart"), str) and source["chart"].strip() for source in sources):
        return (
            f"{prefix} strict identity migration does not support mutable Helm chart "
            "repository sources; use a direct raw-YAML Git target or create an Ops Todo "
            "for separately verified OCI-digest support"
        )
    return None


def destination(app: dict) -> tuple[str, str] | None:
    spec = spec_mapping(app)
    value = spec.get("destination") or {}
    if not isinstance(value, dict):
        return None
    server = value.get("server")
    namespace = value.get("namespace")
    if not isinstance(server, str) or not server.strip():
        return None
    if not isinstance(namespace, str) or not namespace.strip():
        return None
    return server.strip(), namespace.strip()


FULL_GIT_SHA = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})")


def revision_commit(git_root: Path, revision: str) -> str | None:
    """Resolve one full immutable Git object ID, never a branch or expression."""
    value = revision.strip()
    if not FULL_GIT_SHA.fullmatch(value):
        return None
    commit = git_output(git_root, "rev-parse", "--verify", f"{value}^{{commit}}")
    if commit is None or commit.lower() != value.lower():
        return None
    return commit


def remote_has_reachable_commit(repo_url: str, commit: str) -> bool:
    """Freshly fetch declared remote refs and prove they contain the pinned commit.

    Remote-tracking refs in the caller's checkout are deliberately not used:
    they can be stale or locally manufactured. The temporary bare repository
    is disposable, so this proof never changes the supplied checkout. Fetch
    commit ancestry only: exact tree/blob provenance is checked separately in
    the caller's complete checkout with lazy promisor fetches disabled.
    """
    with tempfile.TemporaryDirectory(prefix="identity-migration-git-proof-") as temp_dir:
        remote_root = Path(temp_dir)
        if git_binary_output(remote_root, "init", "--bare", "-q") is None:
            return False
        if git_binary_output(remote_root, "remote", "add", "origin", repo_url) is None:
            return False
        fetched = git_binary_output(
            remote_root,
            "fetch",
            "--quiet",
            "--no-tags",
            "--filter=tree:0",
            "origin",
            "+refs/heads/*:refs/remotes/origin/*",
            "+refs/tags/*:refs/tags/*",
            timeout=90,
            credential_helpers=safe_global_credential_helpers(),
        )
        if fetched is None:
            return False
        remote_commit = git_output(
            remote_root,
            "rev-parse",
            "--verify",
            f"{commit}^{{commit}}",
        )
        if remote_commit is None or remote_commit.lower() != commit.lower():
            return False
        containing_ref = git_output(
            remote_root,
            "for-each-ref",
            "--contains",
            remote_commit,
            "--format=%(refname)",
            "refs/remotes/origin",
            "refs/tags",
        )
        return bool(containing_ref)


def verified_checkout_root(
    source_root_value: Path,
    prefix: str,
    option: str,
) -> tuple[Path | None, str | None]:
    """Require an exact Git checkout root before reading provenance data."""
    source_root = normalized(source_root_value)
    if not source_root.is_dir():
        return None, f"{prefix} {option} must be an existing directory"
    git_root_text = git_output(source_root, "rev-parse", "--show-toplevel")
    if not git_root_text:
        return None, f"{prefix} {option} must be a Git checkout root"
    git_root = normalized(Path(git_root_text))
    if source_root != git_root:
        return None, f"{prefix} {option} must be the Git checkout root"
    return git_root, None


def checkout_origin_failure(
    git_root: Path,
    repo_url: str,
    prefix: str,
    role: str,
) -> str | None:
    """Return the strict-origin mismatch diagnostic without changing its order."""
    origin = git_output(git_root, "config", "--local", "--get", "remote.origin.url")
    if (
        origin is None
        or repository_url_has_embedded_credentials(origin)
        or not same_git_repository(origin, repo_url)
    ):
        return (
            f"{prefix} {role} source checkout origin does not match declared "
            "spec.source.repoURL"
        )
    return None


def checkout_revision(
    git_root: Path,
    revision_value: str,
    prefix: str,
    role: str,
) -> tuple[str | None, str | None]:
    """Bind checkout HEAD to one declared immutable commit SHA."""
    head = git_output(git_root, "rev-parse", "--verify", "HEAD^{commit}")
    revision = revision_commit(git_root, revision_value)
    if revision is None:
        return None, (
            f"{prefix} {role} targetRevision must be one full immutable Git commit SHA "
            "present in the supplied checkout"
        )
    if head != revision:
        return None, (
            f"{prefix} {role} source checkout HEAD must equal the declared "
            "targetRevision"
        )
    return revision, None


def remote_host_approval_failure(
    repo_url: str,
    approved_remote_hosts: set[str],
    prefix: str,
    role: str,
) -> str | None:
    """Require a caller-approved host before an outbound remote proof."""
    canonical_repo = safe_git_repository(repo_url)
    remote_host = canonical_repo[1] if canonical_repo is not None else ""
    if not approved_remote_hosts:
        return (
            f"{prefix} --verify-remote-host is required before fresh Git remote "
            "proof; target render provenance is a STOP"
        )
    if remote_host not in approved_remote_hosts:
        return (
            f"{prefix} {role} source remote host {remote_host!r} is not explicitly "
            "approved by --verify-remote-host"
        )
    return None


def verified_git_checkout(
    source_root_value: Path,
    repo_url: str,
    revision_value: str,
    prefix: str,
    option: str,
    role: str,
    approved_remote_hosts: set[str],
) -> tuple[VerifiedGitCheckout | None, str | None]:
    """Bind a source checkout to an immutable, freshly verified remote commit."""
    git_root, root_error = verified_checkout_root(source_root_value, prefix, option)
    if root_error is not None or git_root is None:
        return None, root_error
    origin_error = checkout_origin_failure(git_root, repo_url, prefix, role)
    if origin_error is not None:
        return None, origin_error
    revision, revision_error = checkout_revision(git_root, revision_value, prefix, role)
    if revision_error is not None or revision is None:
        return None, revision_error
    host_error = remote_host_approval_failure(repo_url, approved_remote_hosts, prefix, role)
    if host_error is not None:
        return None, host_error
    if not remote_has_reachable_commit(repo_url, revision):
        return None, (
            f"{prefix} cannot freshly prove {role} targetRevision is reachable "
            "from the declared remote; target render provenance is a STOP"
        )
    return VerifiedGitCheckout(git_root, revision), None


def direct_source_paths(
    entries: dict[str, tuple[str, str, str]],
    source_path: str,
) -> tuple[str, list[str]]:
    """Return the direct descendants Argo sees with recursion disabled."""
    source_prefix = f"{source_path}/"
    return source_prefix, sorted(path for path in entries if path.startswith(source_prefix))


def direct_source_path_contract_failure(
    tracked_paths: list[str],
    source_prefix: str,
    prefix: str,
    role: str,
    *,
    require_yaml_only: bool,
) -> str | None:
    """Enforce direct-tree layout and tool-detection constraints in order."""
    if any("/" in path[len(source_prefix) :] for path in tracked_paths):
        return (
            f"{prefix} {role} source path must use only regular YAML files directly "
            "at its path root because Argo directory recursion is disabled"
        )
    if not tracked_paths:
        return f"{prefix} {role} source path has no tracked files in the supplied checkout"
    if any(Path(path).name in DIRECT_TOOL_MARKERS for path in tracked_paths):
        return (
            f"{prefix} {role} source path must not contain Helm/Kustomize "
            "tool-detection marker files"
        )
    if require_yaml_only and any(
        Path(path).suffix.lower() not in YAML_FILE_SUFFIXES for path in tracked_paths
    ):
        return (
            f"{prefix} {role} source path must contain only tracked raw YAML manifests"
        )
    return None


def direct_source_tree(
    checkout: VerifiedGitCheckout,
    source: GitSource,
    prefix: str,
    role: str,
    *,
    require_yaml_only: bool,
) -> tuple[dict[str, tuple[str, str, str]] | None, list[str] | None, str | None]:
    """List committed direct-source files after enforcing Argo directory semantics."""
    source_path = canonical_relative_git_path(source.path)
    if source_path is None:
        return None, None, f"{prefix} {role} source path must be a relative path inside its checkout"
    entries = git_tree_entries(checkout)
    if entries is None:
        return None, None, f"{prefix} cannot read committed {role} source tree"
    source_prefix, tracked_paths = direct_source_paths(entries, source_path)
    path_error = direct_source_path_contract_failure(
        tracked_paths,
        source_prefix,
        prefix,
        role,
        require_yaml_only=require_yaml_only,
    )
    if path_error is not None:
        return None, None, path_error
    return entries, tracked_paths, None


def committed_raw_yaml_documents(
    checkout: VerifiedGitCheckout,
    entry: tuple[str, str, str],
    prefix: str,
    role: str,
) -> tuple[list[object] | None, str | None]:
    """Load one regular committed raw-YAML file without consulting its worktree copy."""
    mode, object_type, object_id = entry
    if mode not in {"100644", "100755"} or object_type != "blob":
        return None, f"{prefix} {role} tracked source manifest must be a regular committed file"
    raw = committed_blob_text(checkout, object_id)
    if raw is None:
        return None, f"{prefix} {role} tracked source manifest cannot be read from its commit"
    if SKIP_FILE_RENDERING in raw:
        return None, (
            f"{prefix} {role} tracked source manifest must not use {SKIP_FILE_RENDERING}"
        )
    documents, template_path, yaml_error = exact_rendered_yaml_documents(raw)
    if template_path is not None:
        return None, (
            f"{prefix} {role} tracked source manifest contains unrendered "
            f"Helm template input at {template_path}"
        )
    if yaml_error is not None or documents is None:
        return None, f"{prefix} {role} tracked source manifest is not valid YAML"
    return documents, None


def bootstrap_source_path(
    tracked_paths: list[str],
    prefix: str,
) -> tuple[str | None, str | None]:
    """Require the bootstrap source path to contain exactly one YAML manifest."""
    if len(tracked_paths) != 1:
        return None, (
            f"{prefix} bootstrap source path must contain exactly one tracked YAML "
            "Namespace manifest"
        )
    source_path = tracked_paths[0]
    if Path(source_path).suffix.lower() not in YAML_FILE_SUFFIXES:
        return None, (
            f"{prefix} bootstrap source path must contain exactly one tracked YAML "
            "Namespace manifest"
        )
    return source_path, None


def bootstrap_namespace_manifest(
    source_documents: list[object],
    namespace: str,
    prefix: str,
) -> tuple[dict | None, str | None]:
    """Validate the one committed Namespace document used for bootstrap."""
    if len(source_documents) != 1 or not isinstance(source_documents[0], dict):
        return None, (
            f"{prefix} bootstrap tracked source manifest must contain exactly one "
            f"v1 Namespace/{namespace}"
        )
    source_document = source_documents[0]
    if (
        source_document.get("apiVersion") != "v1"
        or source_document.get("kind") != "Namespace"
        or resource_name(source_document) != namespace
    ):
        return None, (
            f"{prefix} bootstrap tracked source manifest must contain exactly one "
            f"v1 Namespace/{namespace}"
        )
    if not sync_options_protect_namespace(source_document):
        return None, (
            f"{prefix} bootstrap tracked Namespace/{namespace} must set "
            f"{SYNC_OPTIONS}: Prune=false,Delete=false"
        )
    return source_document, None


def bootstrap_tracked_manifest(
    checkout: VerifiedGitCheckout,
    source: GitSource,
    namespace: str,
    prefix: str,
) -> tuple[dict | None, str | None]:
    """Read the exact protected Namespace from committed direct-source YAML."""
    entries, tracked_paths, tree_error = direct_source_tree(
        checkout,
        source,
        prefix,
        "bootstrap",
        require_yaml_only=False,
    )
    if tree_error is not None:
        return None, tree_error
    assert entries is not None and tracked_paths is not None
    source_path, path_error = bootstrap_source_path(tracked_paths, prefix)
    if path_error is not None or source_path is None:
        return None, path_error
    source_documents, source_error = committed_raw_yaml_documents(
        checkout,
        entries[source_path],
        prefix,
        "bootstrap",
    )
    if source_error is not None or source_documents is None:
        return None, source_error
    return bootstrap_namespace_manifest(source_documents, namespace, prefix)


def source_checkout_failure(
    source_root_value: Path,
    render: Path,
    rendered: list[Resource],
    source: GitSource,
    namespace: str,
    approved_remote_hosts: set[str],
    prefix: str,
) -> str | None:
    """Prove the supplied render matches a committed direct Git source manifest."""
    checkout, checkout_error = verified_git_checkout(
        source_root_value,
        source.repo_url,
        source.revision,
        prefix,
        "--bootstrap-source-root",
        "bootstrap",
        approved_remote_hosts,
    )
    if checkout_error is not None or checkout is None:
        return checkout_error
    if is_below(normalized(render), checkout.root):
        return f"{prefix} --bootstrap-render must be outside the bootstrap source checkout"

    source_document, source_error = bootstrap_tracked_manifest(
        checkout,
        source,
        namespace,
        prefix,
    )
    if source_error is not None or source_document is None:
        return source_error
    if len(rendered) != 1 or rendered[0].doc != source_document:
        return (
            f"{prefix} --bootstrap-render must semantically match the single tracked "
            "bootstrap Namespace manifest"
        )
    return None


def sync_option_values(app: dict, key: str) -> list[str]:
    sync_policy = spec_mapping(app).get("syncPolicy") or {}
    options = sync_policy.get("syncOptions") if isinstance(sync_policy, dict) else None
    if not isinstance(options, list):
        return []

    values: list[str] = []
    for option in options:
        if not isinstance(option, str) or "=" not in option:
            continue
        option_key, value = option.split("=", 1)
        if option_key == key:
            values.append(value)
    return values


def has_exact_sync_option(app: dict, key: str, expected: str) -> bool:
    values = sync_option_values(app, key)
    return bool(values) and all(value == expected for value in values)


def application_sync_options(app: dict) -> list[str] | None:
    sync_policy = spec_mapping(app).get("syncPolicy") or {}
    options = sync_policy.get("syncOptions") if isinstance(sync_policy, dict) else None
    if options is None:
        return []
    if not isinstance(options, list) or not all(isinstance(option, str) for option in options):
        return None
    return options


def has_destructive_application_sync_option(app: dict) -> bool:
    options = application_sync_options(app)
    if options is None:
        return True
    for option in options:
        if "=" not in option:
            continue
        key, value = option.split("=", 1)
        if key.strip().lower() in {"replace", "force"} and value.strip().lower() == "true":
            return True
    return False


def bootstrap_application_sync_options_are_safe(app: dict) -> bool:
    """Keep the namespace-only bootstrap free of unrelated Argo sync behavior."""
    return application_sync_options(app) == [f"{CREATE_NAMESPACE}=true"]


def has_argocd_hook_annotation(document: dict) -> bool:
    metadata = document.get("metadata")
    annotations = metadata.get("annotations") if isinstance(metadata, dict) else None
    if not isinstance(annotations, dict):
        return False
    return any(
        isinstance(key, str)
        and key in {
            "argocd.argoproj.io/hook",
            "argocd.argoproj.io/hook-delete-policy",
        }
        for key in annotations
    )


def sync_wave(app: dict) -> int | None:
    metadata = app.get("metadata")
    annotations = metadata.get("annotations") if isinstance(metadata, dict) else None
    if not isinstance(annotations, dict) or SYNC_WAVE not in annotations:
        return None
    value = annotations[SYNC_WAVE]
    if not isinstance(value, str) or not re.fullmatch(r"-?(?:0|[1-9][0-9]*)", value):
        return None
    try:
        wave = int(value)
    except ValueError:
        return None
    # Argo parses into a Go int. The bound below avoids accepting a value that
    # would overflow and fall back to the default wave at runtime.
    if not -(2**63) <= wave <= 2**63 - 1:
        return None
    return wave


def sync_options_protect_namespace(namespace: dict) -> bool:
    metadata = namespace.get("metadata")
    annotations = metadata.get("annotations") if isinstance(metadata, dict) else None
    if not isinstance(annotations, dict):
        return False
    value = annotations.get(SYNC_OPTIONS)
    if not isinstance(value, str):
        return False
    options = [option.strip() for option in value.split(",") if option.strip()]
    if len(options) != 2 or set(options) != {"Prune=false", "Delete=false"}:
        return False
    # This narrow bootstrap may retain ordinary Kubernetes metadata, but must
    # not carry a hook, wave, replace, or any other Argo control annotation.
    return all(
        not isinstance(key, str)
        or not key.startswith("argocd.argoproj.io/")
        or key == SYNC_OPTIONS
        for key in annotations
    )


def expand_rendered_document(file: Path, document: dict, option: str) -> list[Resource]:
    """Expand Kubernetes List wrappers before any scope/permission check."""
    if document.get("kind") != "List":
        return [Resource(file, document)]
    items = document.get("items")
    if not isinstance(items, list):
        raise InputError(f"{option} List document has no list-valued items: {file}")
    expanded: list[Resource] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise InputError(
                f"{option} List document has a non-mapping item at index {index}: {file}"
            )
        expanded.extend(expand_rendered_document(file, item, option))
    return expanded


def rendered_yaml_files(render: Path, option: str) -> list[Path]:
    """Resolve one render input to the exact YAML files it is allowed to contain."""
    if render.is_file():
        return [render]
    if not render.is_dir():
        raise InputError(f"{option} path not found: {render}")
    files = sorted(file for file in render.rglob("*") if file.is_file())
    if not files:
        raise InputError(f"{option} contains no files: {render}")
    non_yaml_files = [file for file in files if file.suffix.lower() not in YAML_FILE_SUFFIXES]
    if non_yaml_files:
        paths = ", ".join(str(file) for file in non_yaml_files)
        raise InputError(
            f"{option} contains non-YAML file(s): {paths}; "
            "supply one exact YAML-only render output"
        )
    return files


def rendered_file_documents(file: Path, option: str) -> list[Resource]:
    """Read one exact rendered YAML file and expand its List documents."""
    try:
        raw = file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise InputError(f"cannot read rendered YAML {file}: {exc}") from exc
    loaded, template_path, yaml_error = exact_rendered_yaml_documents(raw)
    if template_path is not None:
        location = "" if template_path == RAW_TEMPLATE_PATH else f" at {template_path}"
        raise InputError(
            f"{option} contains unrendered Helm template input{location}: "
            f"{file}; render the exact source first"
        )
    if yaml_error is not None or loaded is None:
        raise InputError(f"cannot parse YAML {file}: {yaml_error}")

    documents: list[Resource] = []
    for document in loaded:
        if not isinstance(document, dict):
            raise InputError(f"{option} contains a non-mapping document: {file}")
        documents.extend(expand_rendered_document(file, document, option))
    return documents


def vector_runtime_templates_only(value: str) -> bool:
    """Accept only complete Vector field-template actions in vector.yaml."""
    matches = list(VECTOR_TEMPLATE_ACTION.finditer(value))
    remainder = VECTOR_TEMPLATE_ACTION.sub("", value)
    return (
        TEMPLATE_OPEN_DELIMITER not in remainder
        and TEMPLATE_CLOSE_DELIMITER not in remainder
        and all(
            match.group("field") in ALLOWED_VECTOR_RUNTIME_TEMPLATE_FIELDS
            for match in matches
        )
    )


def mapping_node_child(node: object, key: str) -> object | None:
    """Return one scalar-keyed YAML mapping child without constructing data."""
    if not isinstance(node, yaml.MappingNode):
        return None
    for key_node, value_node in node.value:
        if isinstance(key_node, yaml.ScalarNode) and key_node.value == key:
            return value_node
    return None


def vector_runtime_template_span(document: object, node: object) -> tuple[int, int] | None:
    """Locate the one explicitly supported runtime-template scalar in raw YAML."""
    if (
        not isinstance(document, dict)
        or document.get("apiVersion") != "v1"
        or document.get("kind") != "ConfigMap"
    ):
        return None
    data = document.get("data")
    vector_config = data.get(VECTOR_CONFIG_DATA_KEY) if isinstance(data, dict) else None
    if not isinstance(vector_config, str) or not vector_runtime_templates_only(vector_config):
        return None
    data_node = mapping_node_child(node, "data")
    vector_node = mapping_node_child(data_node, VECTOR_CONFIG_DATA_KEY)
    if not isinstance(vector_node, yaml.ScalarNode):
        return None
    return vector_node.start_mark.index, vector_node.end_mark.index


def decoded_yaml_children(
    value: object,
    path: tuple[str, ...],
) -> Iterator[tuple[object, tuple[str, ...]]]:
    """Yield mapping keys/values or sequence values with their decoded paths."""
    if isinstance(value, dict):
        for key, child in value.items():
            key_path = (*path, str(key))
            if isinstance(key, str):
                yield key, key_path
            yield child, key_path
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield child, (*path, f"[{index}]")


def nested_string_values(value: object) -> Iterator[tuple[tuple[str, ...], str]]:
    """Iterate decoded YAML strings, including mapping keys and alias expansions."""
    pending: list[tuple[object, tuple[str, ...], frozenset[int]]] = [
        (value, (), frozenset())
    ]
    visited = 0
    while pending:
        current, path, ancestors = pending.pop()
        visited += 1
        if visited > MAX_DECODED_YAML_VALUES:
            raise InputError("decoded YAML alias expansion exceeds the safe traversal limit")
        if isinstance(current, str):
            yield path, current
            continue
        if not isinstance(current, (dict, list)):
            continue
        identity = id(current)
        if identity in ancestors:
            alias_type = "mapping" if isinstance(current, dict) else "sequence"
            raise InputError(f"decoded YAML contains a recursive {alias_type} alias")
        child_ancestors = ancestors | {identity}
        pending.extend(
            (child, child_path, child_ancestors)
            for child, child_path in decoded_yaml_children(current, path)
        )


def invalid_decoded_template_path(document: object) -> str | None:
    """Reject decoded template text outside the exact Vector config field."""
    for path, value in nested_string_values(document):
        if (
            TEMPLATE_OPEN_DELIMITER not in value
            and TEMPLATE_CLOSE_DELIMITER not in value
        ):
            continue
        if (
            isinstance(document, dict)
            and document.get("apiVersion") == "v1"
            and document.get("kind") == "ConfigMap"
            and path == ("data", VECTOR_CONFIG_DATA_KEY)
            and vector_runtime_templates_only(value)
        ):
            continue
        return ".".join(path) or "<document>"
    return None


def exact_rendered_yaml_documents(
    raw: str,
) -> tuple[list[object] | None, str | None, str | None]:
    """Parse YAML while accounting for every raw template delimiter."""
    try:
        all_documents = list(yaml.safe_load_all(raw))
        all_nodes = list(yaml.compose_all(raw))
    except yaml.YAMLError as exc:
        template_path = (
            RAW_TEMPLATE_PATH
            if TEMPLATE_OPEN_DELIMITER in raw or TEMPLATE_CLOSE_DELIMITER in raw
            else None
        )
        return None, template_path, str(exc)

    allowed_spans = [
        span
        for document, node in zip(all_documents, all_nodes, strict=True)
        if (span := vector_runtime_template_span(document, node)) is not None
    ]
    for delimiter in re.finditer(r"\{\{|\}\}", raw):
        if not any(start <= delimiter.start() < end for start, end in allowed_spans):
            return None, RAW_TEMPLATE_PATH, None

    documents = [document for document in all_documents if document is not None]
    try:
        for document in documents:
            if (template_path := invalid_decoded_template_path(document)) is not None:
                return None, template_path, None
    except InputError as exc:
        return None, None, str(exc)
    return documents, None, None


def rendered_documents(render: Path, option: str) -> list[Resource]:
    """Read the exact YAML-only render supplied to the migration gate."""
    documents: list[Resource] = []
    for file in rendered_yaml_files(render, option):
        documents.extend(rendered_file_documents(file, option))
    return documents


def semantic_documents_match(left: list[Resource], right: list[Resource]) -> bool:
    """Compare rendered resource multisets independent of YAML document order."""
    def fingerprints(resources: list[Resource]) -> list[str]:
        return sorted(
            json.dumps(resource.doc, sort_keys=True, separators=(",", ":"), default=str)
            for resource in resources
        )

    return fingerprints(left) == fingerprints(right)


def tracked_yaml_file_documents(
    checkout: VerifiedGitCheckout,
    tracked_path: str,
    entry: tuple[str, str, str],
    prefix: str,
    role: str,
) -> tuple[list[Resource] | None, str | None]:
    """Read and expand one committed direct-source YAML blob."""
    source_documents, source_error = committed_raw_yaml_documents(
        checkout,
        entry,
        prefix,
        role,
    )
    if source_error is not None or source_documents is None:
        return None, source_error
    documents: list[Resource] = []
    source_file = Path(f"<git:{checkout.commit}:{tracked_path}>")
    for document in source_documents:
        if not isinstance(document, dict):
            return None, f"{prefix} {role} tracked source manifest contains a non-mapping document"
        try:
            documents.extend(expand_rendered_document(source_file, document, f"{role} source"))
        except InputError as exc:
            return None, f"{prefix} {exc}"
    return documents, None


def tracked_direct_yaml_documents(
    checkout: VerifiedGitCheckout,
    source: GitSource,
    prefix: str,
    role: str,
) -> tuple[list[Resource] | None, str | None]:
    """Read strict raw YAML from committed blobs, not a mutable worktree."""
    entries, tracked_paths, tree_error = direct_source_tree(
        checkout,
        source,
        prefix,
        role,
        require_yaml_only=True,
    )
    if tree_error is not None or entries is None or tracked_paths is None:
        return None, tree_error

    documents: list[Resource] = []
    for tracked_path in tracked_paths:
        file_documents, source_error = tracked_yaml_file_documents(
            checkout,
            tracked_path,
            entries[tracked_path],
            prefix,
            role,
        )
        if source_error is not None or file_documents is None:
            return None, source_error
        documents.extend(file_documents)
    if not documents:
        return None, f"{prefix} {role} source path contains no Kubernetes resources"
    return documents, None


def direct_target_render_provenance_failure(
    source_root_value: Path,
    render: Path,
    rendered: list[Resource],
    source: GitSource,
    approved_remote_hosts: set[str],
    prefix: str,
) -> str | None:
    """Bind target render to a committed exact direct-YAML Git source."""
    checkout, checkout_error = verified_git_checkout(
        source_root_value,
        source.repo_url,
        source.revision,
        prefix,
        "--target-source-root",
        "target",
        approved_remote_hosts,
    )
    if checkout_error is not None or checkout is None:
        return checkout_error
    if is_below(normalized(render), checkout.root):
        return f"{prefix} --target-render must be outside the target source checkout"
    source_documents, source_error = tracked_direct_yaml_documents(
        checkout,
        source,
        prefix,
        "direct target",
    )
    if source_error is not None or source_documents is None:
        return source_error
    if not semantic_documents_match(source_documents, rendered):
        return (
            f"{prefix} --target-render must semantically match the clean tracked "
            "direct target source"
        )
    return None


def target_render_provenance_failure(
    app: dict,
    target_source_root: Path | None,
    approved_remote_hosts: set[str],
    target_render: Path,
    rendered: list[Resource],
    prefix: str,
) -> str | None:
    """Require a reproducible target render before policy-checking it."""
    direct_source, direct_error = direct_target_git_source(app, prefix)
    if direct_error is not None:
        return direct_error
    if direct_source is not None:
        if target_source_root is None:
            return (
                f"{prefix} --target-source-root is required for a direct raw-YAML "
                "target source"
            )
        return direct_target_render_provenance_failure(
            target_source_root,
            target_render,
            rendered,
            direct_source,
            approved_remote_hosts,
            prefix,
        )

    helm_error = helm_target_source_failure(app, prefix)
    if helm_error is not None:
        return helm_error
    return (
        f"{prefix} cannot prove target render provenance for this Application source "
        "shape; create an Ops Todo or use a supported direct raw-YAML Git form"
    )


def exact_namespace_resources(documents: list[Resource], namespace: str) -> list[Resource]:
    return [
        resource
        for resource in documents
        if resource.doc.get("apiVersion") == "v1"
        and resource.doc.get("kind") == "Namespace"
        and resource_name(resource.doc) == namespace
    ]


def resource_group(document: dict) -> str | None:
    api_version = document.get("apiVersion")
    if not isinstance(api_version, str) or not api_version.strip():
        return None
    return api_version.split("/", 1)[0] if "/" in api_version else ""


def resource_scope(document: dict) -> str | None:
    """Return known Kubernetes scope for a rendered GVK, else fail closed."""
    group = resource_group(document)
    kind = document.get("kind")
    if group is None or not isinstance(kind, str) or not kind:
        return None
    identity = (group, kind)
    if identity in KNOWN_CLUSTER_SCOPED_RESOURCES:
        return "cluster"
    if kind in KNOWN_NAMESPACED_RESOURCES.get(group, set()):
        return "namespaced"
    return None


def namespaced_rule_matches(entry: object, document: dict) -> bool:
    """Match Argo's namespace resource GroupKind rule, not a fictional name rule."""
    if not isinstance(entry, dict):
        return False
    group = resource_group(document)
    kind = document.get("kind")
    if group is None or not isinstance(kind, str) or not kind:
        return False
    if entry.get("group") not in {group, "*"}:
        return False
    if entry.get("kind") not in {kind, "*"}:
        return False
    # AppProject namespaceResource{White,Black}list entries are GroupKind
    # items. A stray ``name`` is ignored by Argo's authorization semantics,
    # so treating it as a narrower rule could falsely permit a render.
    return True


def project_allows_namespaced_resource(project: dict, document: dict) -> bool:
    """Check AppProject allow-and-deny rules for one rendered namespaced GVK."""
    spec = spec_mapping(project)
    whitelist = spec.get("namespaceResourceWhitelist") or []
    blacklist = spec.get("namespaceResourceBlacklist") or []
    if not isinstance(whitelist, list) or not isinstance(blacklist, list):
        return False
    if whitelist and not any(namespaced_rule_matches(entry, document) for entry in whitelist):
        return False
    return not any(namespaced_rule_matches(entry, document) for entry in blacklist)


def target_render_scope(
    document: dict,
    prefix: str,
) -> tuple[tuple[str, str] | None, str | None]:
    """Reject Namespace, cluster-scoped, and unknown GVKs before metadata checks."""
    if document.get("apiVersion") == "v1" and document.get("kind") == "Namespace":
        return None, f"{prefix} --target-render must not contain any Namespace resource"
    group = resource_group(document)
    kind = document.get("kind")
    identity = f"{group or '<unknown>'}/{kind or '<unknown>'}"
    scope = resource_scope(document)
    if scope == "cluster":
        return None, f"{prefix} --target-render must not contain cluster-scoped resource {identity}"
    if scope != "namespaced":
        return None, (
            f"{prefix} cannot prove that rendered {identity} is namespaced; "
            "add verified scope support before this migration"
        )
    assert isinstance(kind, str)
    return (group or "", kind), None


def target_render_metadata_identity(
    document: dict,
    group: str,
    kind: str,
    namespace: str,
    prefix: str,
) -> tuple[tuple[str, str, str, str] | None, str | None]:
    """Resolve a known namespaced GVK to its destination Namespace and name."""
    identity = f"{group or '<unknown>'}/{kind}"
    metadata = document.get("metadata")
    if not isinstance(metadata, dict):
        return None, f"{prefix} --target-render resource {identity} must declare metadata.name"
    name = metadata.get("name")
    if not isinstance(name, str) or not name.strip():
        return None, f"{prefix} --target-render resource {identity} must declare a nonempty metadata.name"
    if "generateName" in metadata:
        return None, f"{prefix} --target-render resource {identity}/{name} must not declare generateName"
    rendered_namespace = metadata.get("namespace")
    if rendered_namespace is not None and (
        not isinstance(rendered_namespace, str) or rendered_namespace != namespace
    ):
        return None, (
            f"{prefix} --target-render resource {kind}/{name} "
            f"must resolve to destination namespace {namespace!r}"
        )
    return (group, kind, namespace, name), None


def rendered_target_identity(
    document: dict,
    namespace: str,
    prefix: str,
) -> tuple[tuple[str, str, str, str] | None, str | None]:
    """Resolve one rendered namespaced object to its Argo CD target identity."""
    scope_identity, scope_error = target_render_scope(document, prefix)
    if scope_error is not None or scope_identity is None:
        return None, scope_error
    group, kind = scope_identity
    return target_render_metadata_identity(document, group, kind, namespace, prefix)


def target_render_project_failure(
    document: dict,
    project: Resource,
    prefix: str,
    name: str,
) -> str | None:
    """Require the target AppProject to authorize a rendered namespaced GVK."""
    if project_allows_namespaced_resource(project.doc, document):
        return None
    return (
        f"{prefix} AppProject/{resource_name(project.doc) or UNNAMED_RESOURCE_NAME} "
        f"does not allow rendered "
        f"{resource_group(document) or '<unknown>'}/{document.get('kind') or '<unknown>'} "
        f"resource {name}"
    )


def target_render_failure(
    rendered: list[Resource],
    destination_value: tuple[str, str],
    project: Resource,
    prefix: str,
) -> str | None:
    """Require the target render to stay namespaced and AppProject-authorized.

    Helm commonly omits ``metadata.namespace``. That is valid only for a
    known namespaced GVK, where Argo CD supplies ``spec.destination.namespace``.
    Unknown and cluster-scoped GVKs are rejected rather than inferred from
    metadata.
    """
    if not rendered:
        return f"{prefix} --target-render must contain at least one rendered resource"
    namespace = destination_value[1]
    seen_identities: set[tuple[str, str, str, str]] = set()
    for resource in rendered:
        document = resource.doc
        resolved_identity, identity_error = rendered_target_identity(
            document,
            namespace,
            prefix,
        )
        if identity_error is not None or resolved_identity is None:
            return identity_error
        if resolved_identity in seen_identities:
            group, kind, _, name = resolved_identity
            return (
                f"{prefix} --target-render contains duplicate resolved resource identity "
                f"{group or '<unknown>'}/{kind}/{namespace}/{name}"
            )
        seen_identities.add(resolved_identity)
        project_error = target_render_project_failure(
            document,
            project,
            prefix,
            resolved_identity[3],
        )
        if project_error is not None:
            return project_error
    return None


@dataclass(frozen=True)
class MigrationInputs:
    """Parsed files and exact renders supplied to one identity-migration gate."""

    remote_hosts: set[str]
    target_project_namespace_owner: bool
    bootstrap_source_root: Path | None
    bootstrap_render: Path | None
    target_source_root: Path | None
    target_render: Path | None
    legacy_source: Resource
    bootstrap: Resource | None
    target: Resource | None
    bootstrap_rendered: list[Resource] | None
    target_rendered: list[Resource] | None


def load_migration_inputs(args: argparse.Namespace, root: Path) -> MigrationInputs:
    """Load the mutually dependent command inputs before validating policy."""
    remote_hosts = approved_remote_hosts(args.verify_remote_host)
    if remote_hosts is None:
        raise InputError("--verify-remote-host must be a valid DNS hostname")
    source_path = resolve_candidate(root, args.source_application, "--source-application")
    legacy_source = load_exact_application(source_path, "--source-application")
    target, target_rendered = load_target_inputs(args, root)
    bootstrap_values = (
        args.bootstrap_application,
        args.bootstrap_source_root,
        args.bootstrap_render,
    )
    if args.target_project_namespace_owner:
        if any(value is not None for value in bootstrap_values):
            raise InputError(
                "--target-project-namespace-owner is mutually exclusive with "
                "--bootstrap-application, --bootstrap-source-root, and --bootstrap-render"
            )
        if target is None or args.target_source_root is None:
            raise InputError(
                "--target-project-namespace-owner requires --target-application, "
                "--target-render, and --target-source-root"
            )
        bootstrap = None
        bootstrap_rendered = None
    else:
        if not all(value is not None for value in bootstrap_values):
            raise InputError(
                "standalone bootstrap mode requires --bootstrap-application, "
                "--bootstrap-source-root, and --bootstrap-render; omission does not "
                "select target-project Namespace ownership"
            )
        assert args.bootstrap_application is not None
        assert args.bootstrap_render is not None
        bootstrap_path = resolve_candidate(
            root,
            args.bootstrap_application,
            "--bootstrap-application",
        )
        bootstrap = load_exact_application(bootstrap_path, "--bootstrap-application")
        bootstrap_rendered = rendered_documents(
            args.bootstrap_render,
            "--bootstrap-render",
        )
    return MigrationInputs(
        remote_hosts,
        args.target_project_namespace_owner,
        args.bootstrap_source_root,
        args.bootstrap_render,
        args.target_source_root,
        args.target_render,
        legacy_source,
        bootstrap,
        target,
        bootstrap_rendered,
        target_rendered,
    )


def load_target_inputs(
    args: argparse.Namespace,
    root: Path,
) -> tuple[Resource | None, list[Resource] | None]:
    """Load the optional Add-MR target pair without accepting partial input."""
    if args.target_application is not None:
        if args.target_render is None:
            raise InputError("--target-render is required with --target-application")
        target_path = resolve_candidate(root, args.target_application, "--target-application")
        target = load_exact_application(target_path, "--target-application")
        return target, rendered_documents(args.target_render, "--target-render")
    if args.target_render is not None:
        raise InputError("--target-render requires --target-application")
    if args.target_source_root is not None:
        raise InputError("--target-source-root requires --target-application")
    return None, None


def application_failure_prefix(resource: Resource) -> str:
    """Format a stable failure prefix for a supplied Argo CD Application."""
    return f"FAIL: {resource.file}: Application/{resource_name(resource.doc) or UNNAMED_RESOURCE_NAME}"


def validate_identity_pair(
    legacy_source: Resource,
    bootstrap: Resource,
    legacy_prefix: str,
    bootstrap_prefix: str,
    failures: list[str],
) -> tuple[tuple[str, str] | None, tuple[str, str] | None]:
    """Require distinct legacy and namespace-bootstrap Application identities."""
    legacy_identity = application_identity(legacy_source.doc)
    bootstrap_identity = application_identity(bootstrap.doc)
    if legacy_identity is None:
        failures.append(
            f"{legacy_prefix} must declare nonempty metadata.namespace and metadata.name"
        )
    if bootstrap_identity is None:
        failures.append(
            f"{bootstrap_prefix} must declare nonempty metadata.namespace and metadata.name"
        )
    elif legacy_identity is not None and legacy_identity == bootstrap_identity:
        failures.append(
            f"{bootstrap_prefix} must use a distinct metadata.namespace/name identity "
            "from the legacy Application"
        )
    elif legacy_identity is not None and legacy_identity[0] != bootstrap_identity[0]:
        failures.append(
            f"{bootstrap_prefix} must use the same control-plane metadata.namespace "
            "as the legacy Application"
        )
    return legacy_identity, bootstrap_identity


def validate_bootstrap_destinations(
    legacy_source: Resource,
    bootstrap: Resource,
    legacy_prefix: str,
    bootstrap_prefix: str,
    failures: list[str],
) -> tuple[tuple[str, str] | None, tuple[str, str] | None]:
    """Enforce the legacy/namespace-bootstrap destination isolation contract."""
    legacy_destination = destination(legacy_source.doc)
    if legacy_destination is None:
        failures.append(
            f"{legacy_prefix} must declare nonempty spec.destination.server and spec.destination.namespace"
        )
    bootstrap_destination = destination(bootstrap.doc)
    if bootstrap_destination is None:
        failures.append(
            f"{bootstrap_prefix} must declare nonempty spec.destination.server and spec.destination.namespace"
        )
        return legacy_destination, None
    if bootstrap_destination[1] == "default":
        failures.append(f"{bootstrap_prefix} must not target the default namespace")
        return legacy_destination, bootstrap_destination
    if legacy_destination is not None:
        if legacy_destination[0] != bootstrap_destination[0]:
            failures.append(
                f"{legacy_prefix} destination server must exactly match bootstrap destination server"
            )
        if legacy_destination[1] == bootstrap_destination[1]:
            failures.append(
                f"{legacy_prefix} namespace must differ from bootstrap target namespace"
            )
    return legacy_destination, bootstrap_destination


def record_bootstrap_application_safety_failures(
    bootstrap: Resource,
    bootstrap_prefix: str,
    failures: list[str],
) -> int | None:
    """Record bootstrap-only sync, hook, hydration, and wave requirements."""
    if not has_exact_sync_option(bootstrap.doc, CREATE_NAMESPACE, "true"):
        failures.append(f"{bootstrap_prefix} must explicitly set CreateNamespace=true")
    if not bootstrap_application_sync_options_are_safe(bootstrap.doc):
        failures.append(
            f"{bootstrap_prefix} may use only exact CreateNamespace=true syncOptions"
        )
    if has_argocd_hook_annotation(bootstrap.doc):
        failures.append(f"{bootstrap_prefix} must not be an Argo CD hook")
    record_application_source_guard_failures(bootstrap.doc, bootstrap_prefix, failures)
    bootstrap_wave = sync_wave(bootstrap.doc)
    if bootstrap_wave is None:
        failures.append(f"{bootstrap_prefix} must declare an integer {SYNC_WAVE}")
    return bootstrap_wave


def project_for_application(
    projects: dict[tuple[str, str], list[Resource]],
    project_name: str,
    identity: tuple[str, str] | None,
) -> tuple[Resource | None, str | None]:
    """Resolve an Application's AppProject only from its control-plane namespace."""
    if identity is None:
        return None, "Application control-plane namespace is absent"
    return one_project(projects, project_name, identity[0])


def validate_bootstrap_project(
    bootstrap: Resource,
    bootstrap_identity: tuple[str, str] | None,
    bootstrap_destination: tuple[str, str] | None,
    projects: dict[tuple[str, str], list[Resource]],
    bootstrap_prefix: str,
    failures: list[str],
) -> Resource | None:
    """Require a non-default AppProject with core Namespace authority."""
    bootstrap_project = str(spec_mapping(bootstrap.doc).get("project") or "")
    if bootstrap_project == "default":
        failures.append(f"{bootstrap_prefix} must not use the default AppProject as a namespace bypass")
        return None
    project, project_error = project_for_application(
        projects,
        bootstrap_project,
        bootstrap_identity,
    )
    if project_error is not None or project is None:
        failures.append(f"{bootstrap_prefix} cannot validate Namespace authority: {project_error}")
        return None
    if bootstrap_destination is not None and not project_allows_namespace(
        project.doc,
        bootstrap_destination[1],
    ):
        failures.append(
            f"{bootstrap_prefix} AppProject/{bootstrap_project} does not allow core /Namespace"
        )
    if bootstrap_destination is not None and not project_allows_destination(
        project.doc,
        bootstrap_destination,
    ):
        failures.append(
            f"{bootstrap_prefix} AppProject/{bootstrap_project} does not permit bootstrap destination"
        )
    return project


def validate_bootstrap_source(
    inputs: MigrationInputs,
    bootstrap_project: Resource | None,
    bootstrap_destination: tuple[str, str] | None,
    bootstrap_prefix: str,
    failures: list[str],
) -> None:
    """Bind the protected Namespace render to its authorized committed Git source."""
    bootstrap_source, source_failure = bootstrap_git_source(
        inputs.bootstrap.doc,
        bootstrap_prefix,
    )
    if source_failure is not None:
        failures.append(source_failure)
        return
    if (
        bootstrap_source is None
        or bootstrap_project is None
        or bootstrap_destination is None
    ):
        return
    permission_failure = source_permission_failure(
        bootstrap_project,
        bootstrap_source,
        bootstrap_prefix,
        "bootstrap",
    )
    if permission_failure is not None:
        failures.append(permission_failure)
        return
    checkout_failure = source_checkout_failure(
        inputs.bootstrap_source_root,
        inputs.bootstrap_render,
        inputs.bootstrap_rendered,
        bootstrap_source,
        bootstrap_destination[1],
        inputs.remote_hosts,
        bootstrap_prefix,
    )
    if checkout_failure is not None:
        failures.append(checkout_failure)


def validate_bootstrap_render(
    rendered: list[Resource],
    bootstrap_destination: tuple[str, str] | None,
    bootstrap_render: Path,
    failures: list[str],
) -> None:
    """Require exactly one protected Namespace and no bootstrap side resources."""
    if bootstrap_destination is None:
        return
    namespace = bootstrap_destination[1]
    namespace_resources = exact_namespace_resources(rendered, namespace)
    all_namespace_resources = [
        resource
        for resource in rendered
        if resource.doc.get("apiVersion") == "v1" and resource.doc.get("kind") == "Namespace"
    ]
    unexpected_namespaces = [
        resource
        for resource in all_namespace_resources
        if resource_name(resource.doc) != namespace
    ]
    record_namespace_render_failure(
        namespace_resources,
        namespace,
        bootstrap_render,
        failures,
    )
    record_unexpected_bootstrap_resources(
        rendered,
        namespace_resources,
        unexpected_namespaces,
        bootstrap_render,
        failures,
    )


def record_namespace_render_failure(
    namespace_resources: list[Resource],
    namespace: str,
    bootstrap_render: Path,
    failures: list[str],
) -> None:
    """Record exact protection failures for the desired Namespace document."""
    if not namespace_resources:
        failures.append(
            f"FAIL: {bootstrap_render}: exact rendered output lacks v1 Namespace/{namespace}"
        )
    elif len(namespace_resources) != 1:
        files = ", ".join(str(resource.file) for resource in namespace_resources)
        failures.append(
            f"FAIL: {bootstrap_render}: rendered output has multiple v1 Namespace/{namespace} documents: {files}"
        )
    elif not sync_options_protect_namespace(namespace_resources[0].doc):
        failures.append(
            f"FAIL: {namespace_resources[0].file}: Namespace/{namespace} must set "
            f"only {SYNC_OPTIONS}: Prune=false,Delete=false and no other Argo controls"
        )


def record_unexpected_bootstrap_resources(
    rendered: list[Resource],
    namespace_resources: list[Resource],
    unexpected_namespaces: list[Resource],
    bootstrap_render: Path,
    failures: list[str],
) -> None:
    """Record extra Namespace and non-Namespace output from the bootstrap render."""
    if unexpected_namespaces:
        names = ", ".join(
            resource_name(resource.doc) or UNNAMED_RESOURCE_NAME
            for resource in unexpected_namespaces
        )
        failures.append(
            f"FAIL: {bootstrap_render}: rendered output contains unexpected v1 Namespace resource(s): {names}"
        )
    unexpected_non_namespaces = [
        resource
        for resource in rendered
        if resource not in namespace_resources
        and not (
            resource.doc.get("apiVersion") == "v1" and resource.doc.get("kind") == "Namespace"
        )
    ]
    if unexpected_non_namespaces:
        identities = ", ".join(
            f"{resource.doc.get('kind') or '<unknown>'}/"
            f"{resource_name(resource.doc) or UNNAMED_RESOURCE_NAME}"
            for resource in unexpected_non_namespaces
        )
        failures.append(
            f"FAIL: {bootstrap_render}: rendered bootstrap output contains unexpected resource(s): {identities}"
        )


def target_migration_destination(
    target: Resource,
    bootstrap_destination: tuple[str, str] | None,
    target_prefix: str,
    failures: list[str],
) -> tuple[str, str] | None:
    """Validate the target destination remains isolated behind the bootstrap."""
    target_destination = destination(target.doc)
    if target_destination is None:
        failures.append(
            f"{target_prefix} must declare nonempty spec.destination.server and spec.destination.namespace"
        )
    elif target_destination[1] == "default":
        failures.append(f"{target_prefix} must not target the default namespace")
    elif bootstrap_destination is not None and target_destination != bootstrap_destination:
        failures.append(
            f"{target_prefix} destination {target_destination!r} must exactly match bootstrap destination {bootstrap_destination!r}"
        )
    return target_destination


def target_migration_identity(
    target: Resource,
    legacy_identity: tuple[str, str] | None,
    bootstrap_identity: tuple[str, str] | None,
    target_prefix: str,
    failures: list[str],
) -> tuple[str, str] | None:
    """Validate the replacement Application identity stays distinct and colocated."""
    target_identity = application_identity(target.doc)
    if target_identity is None:
        failures.append(
            f"{target_prefix} must declare nonempty metadata.namespace and metadata.name"
        )
    elif bootstrap_identity is not None and target_identity == bootstrap_identity:
        failures.append(
            f"{target_prefix} must use a distinct metadata.namespace/name identity "
            "from the bootstrap Application"
        )
    elif legacy_identity is not None and target_identity == legacy_identity:
        failures.append(
            f"{target_prefix} must use a distinct metadata.namespace/name identity "
            "from the legacy Application"
        )
    elif bootstrap_identity is not None and target_identity[0] != bootstrap_identity[0]:
        failures.append(
            f"{target_prefix} must use the same control-plane metadata.namespace as the bootstrap Application"
        )
    return target_identity


def validate_target_destination_and_identity(
    target: Resource,
    legacy_identity: tuple[str, str] | None,
    bootstrap_identity: tuple[str, str] | None,
    bootstrap_destination: tuple[str, str] | None,
    target_prefix: str,
    failures: list[str],
) -> tuple[tuple[str, str] | None, tuple[str, str] | None]:
    """Validate target hydration, destination isolation, and unique identity."""
    record_application_source_guard_failures(target.doc, target_prefix, failures)
    target_destination = target_migration_destination(
        target,
        bootstrap_destination,
        target_prefix,
        failures,
    )
    target_identity = target_migration_identity(
        target,
        legacy_identity,
        bootstrap_identity,
        target_prefix,
        failures,
    )
    return target_destination, target_identity


def record_target_sync_policy_failures(
    target: Resource,
    bootstrap_wave: int | None,
    bootstrap_prefix: str,
    target_prefix: str,
    failures: list[str],
) -> None:
    """Record target sync-option, hook, and wave safety failures in order."""
    if not has_exact_sync_option(target.doc, CREATE_NAMESPACE, "false"):
        failures.append(f"{target_prefix} must explicitly set CreateNamespace=false")
    if has_destructive_application_sync_option(target.doc):
        failures.append(
            f"{target_prefix} must not set destructive Replace=true or Force=true syncOptions"
        )
    if has_argocd_hook_annotation(target.doc):
        failures.append(f"{target_prefix} must not be an Argo CD hook")
    target_wave = sync_wave(target.doc)
    if target_wave is None:
        failures.append(f"{target_prefix} must declare an integer {SYNC_WAVE}")
    elif bootstrap_wave is not None and bootstrap_wave >= target_wave:
        failures.append(
            f"{bootstrap_prefix} sync wave {bootstrap_wave} must be lower than target sync wave {target_wave}"
        )


def validate_target_project_and_policy(
    target: Resource,
    target_identity: tuple[str, str] | None,
    target_destination: tuple[str, str] | None,
    bootstrap_wave: int | None,
    bootstrap_prefix: str,
    projects: dict[tuple[str, str], list[Resource]],
    target_prefix: str,
    failures: list[str],
) -> Resource | None:
    """Apply target sync safety and authorize its AppProject destination."""
    record_target_sync_policy_failures(
        target,
        bootstrap_wave,
        bootstrap_prefix,
        target_prefix,
        failures,
    )
    target_project = str(spec_mapping(target.doc).get("project") or "")
    if target_project == "default":
        failures.append(f"{target_prefix} must not use the default AppProject")
        return None
    project, project_error = project_for_application(projects, target_project, target_identity)
    if project_error is not None or project is None:
        failures.append(f"{target_prefix} cannot validate its AppProject: {project_error}")
        return None
    if target_destination is not None and not project_allows_destination(
        project.doc,
        target_destination,
    ):
        failures.append(f"{target_prefix} AppProject/{target_project} does not permit target destination")
    return project


def validate_target_source_and_render(
    inputs: MigrationInputs,
    target_project: Resource,
    target_destination: tuple[str, str] | None,
    target_prefix: str,
    failures: list[str],
) -> None:
    """Bind and authorize the target source only after its AppProject is known."""
    assert inputs.target is not None
    target_source_error = target_source_failure(inputs.target.doc, target_project, target_prefix)
    if target_source_error is not None:
        failures.append(target_source_error)
        return
    if target_destination is None or inputs.target_rendered is None:
        return
    provenance_failure = target_render_provenance_failure(
        inputs.target.doc,
        inputs.target_source_root,
        inputs.remote_hosts,
        inputs.target_render,
        inputs.target_rendered,
        target_prefix,
    )
    if provenance_failure is not None:
        failures.append(provenance_failure)
        return
    render_failure = target_render_failure(
        inputs.target_rendered,
        target_destination,
        target_project,
        target_prefix,
    )
    if render_failure is not None:
        failures.append(render_failure)


def validate_target(
    inputs: MigrationInputs,
    legacy_identity: tuple[str, str] | None,
    bootstrap_identity: tuple[str, str] | None,
    bootstrap_destination: tuple[str, str] | None,
    bootstrap_wave: int | None,
    bootstrap_prefix: str,
    projects: dict[tuple[str, str], list[Resource]],
    failures: list[str],
) -> None:
    """Run the complete Add-MR target contract when the caller supplied one."""
    if inputs.target is None:
        return
    target_prefix = application_failure_prefix(inputs.target)
    target_destination, target_identity = validate_target_destination_and_identity(
        inputs.target,
        legacy_identity,
        bootstrap_identity,
        bootstrap_destination,
        target_prefix,
        failures,
    )
    target_project = validate_target_project_and_policy(
        inputs.target,
        target_identity,
        target_destination,
        bootstrap_wave,
        bootstrap_prefix,
        projects,
        target_prefix,
        failures,
    )
    if target_project is not None:
        validate_target_source_and_render(
            inputs,
            target_project,
            target_destination,
            target_prefix,
            failures,
        )


def validate_target_owner_identity_and_destination(
    legacy_source: Resource,
    target: Resource,
    legacy_prefix: str,
    target_prefix: str,
    failures: list[str],
) -> tuple[tuple[str, str] | None, tuple[str, str] | None]:
    """Require a distinct target identity and same-cluster namespace isolation."""
    legacy_identity = application_identity(legacy_source.doc)
    target_identity = application_identity(target.doc)
    if legacy_identity is None:
        failures.append(
            f"{legacy_prefix} must declare nonempty metadata.namespace and metadata.name"
        )
    if target_identity is None:
        failures.append(
            f"{target_prefix} must declare nonempty metadata.namespace and metadata.name"
        )
    elif legacy_identity is not None and target_identity == legacy_identity:
        failures.append(
            f"{target_prefix} must use a distinct metadata.namespace/name identity "
            "from the legacy Application"
        )
    elif legacy_identity is not None and target_identity[0] != legacy_identity[0]:
        failures.append(
            f"{target_prefix} must use the same control-plane metadata.namespace "
            "as the legacy Application"
        )

    legacy_destination = destination(legacy_source.doc)
    target_destination = destination(target.doc)
    if legacy_destination is None:
        failures.append(
            f"{legacy_prefix} must declare nonempty spec.destination.server and "
            "spec.destination.namespace"
        )
    if target_destination is None:
        failures.append(
            f"{target_prefix} must declare nonempty spec.destination.server and "
            "spec.destination.namespace"
        )
    elif target_destination[1] == "default":
        failures.append(f"{target_prefix} must not target the default namespace")
    elif legacy_destination is not None:
        if legacy_destination[0] != target_destination[0]:
            failures.append(
                f"{legacy_prefix} destination server must exactly match target destination server"
            )
        if legacy_destination[1] == target_destination[1]:
            failures.append(
                f"{legacy_prefix} namespace must differ from target namespace"
            )
    return target_identity, target_destination


def validate_target_owner_policy(
    target: Resource,
    target_identity: tuple[str, str] | None,
    target_destination: tuple[str, str] | None,
    projects: dict[tuple[str, str], list[Resource]],
    target_prefix: str,
    failures: list[str],
) -> Resource | None:
    """Require the exact target project to own Namespace creation explicitly."""
    record_application_source_guard_failures(target.doc, target_prefix, failures)
    if not has_exact_sync_option(target.doc, CREATE_NAMESPACE, "true"):
        failures.append(f"{target_prefix} must explicitly set CreateNamespace=true")
    if has_destructive_application_sync_option(target.doc):
        failures.append(
            f"{target_prefix} must not set destructive Replace=true or Force=true syncOptions"
        )
    if has_argocd_hook_annotation(target.doc):
        failures.append(f"{target_prefix} must not be an Argo CD hook")
    if sync_wave(target.doc) is None:
        failures.append(f"{target_prefix} must declare an integer {SYNC_WAVE}")

    target_project = str(spec_mapping(target.doc).get("project") or "")
    if not target_project:
        failures.append(f"{target_prefix} must declare a nonempty AppProject")
        return None
    if target_project == "default":
        failures.append(
            f"{target_prefix} must not use the default AppProject as a Namespace bypass"
        )
        return None
    project, project_error = project_for_application(
        projects,
        target_project,
        target_identity,
    )
    if project_error is not None or project is None:
        failures.append(
            f"{target_prefix} cannot validate target-project Namespace authority: "
            f"{project_error}"
        )
        return None
    namespace_permission_error = None
    if target_destination is not None:
        namespace_permission_error = target_owner_namespace_permission_failure(
            project.doc,
            target_destination[1],
        )
    if namespace_permission_error is not None:
        failures.append(
            f"{target_prefix} AppProject/{target_project} owner-mode Namespace "
            f"permission is invalid: {namespace_permission_error}"
        )
    if target_destination is not None and not project_allows_destination(
        project.doc,
        target_destination,
    ):
        failures.append(
            f"{target_prefix} AppProject/{target_project} does not permit target destination"
        )
    return project


def validate_target_project_namespace_owner(
    inputs: MigrationInputs,
    projects: dict[tuple[str, str], list[Resource]],
    failures: list[str],
) -> None:
    """Run the explicit bootstrap-free target-project Namespace-owner contract."""
    assert inputs.target is not None
    legacy_prefix = application_failure_prefix(inputs.legacy_source)
    target_prefix = application_failure_prefix(inputs.target)
    target_identity, target_destination = validate_target_owner_identity_and_destination(
        inputs.legacy_source,
        inputs.target,
        legacy_prefix,
        target_prefix,
        failures,
    )
    target_project = validate_target_owner_policy(
        inputs.target,
        target_identity,
        target_destination,
        projects,
        target_prefix,
        failures,
    )
    if target_project is not None:
        validate_target_source_and_render(
            inputs,
            target_project,
            target_destination,
            target_prefix,
            failures,
        )


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    root = normalized(args.cluster_dir)
    if not root.is_dir():
        print(f"FAIL: cluster directory not found: {root}", file=sys.stderr)
        return 2

    try:
        inputs = load_migration_inputs(args, root)
    except InputError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2

    projects, failures = collect_projects(root)
    if inputs.target_project_namespace_owner:
        validate_target_project_namespace_owner(inputs, projects, failures)
        if failures:
            for failure in failures:
                print(failure)
            print(
                f"FAIL: {len(failures)} Argo CD / Helm identity-migration "
                "contract violation(s)"
            )
            return 1
        print(
            "PASS: exact source/target Applications, target-project Namespace "
            "authority, and reproducible namespaced target render satisfy the "
            "identity-migration contract"
        )
        return 0

    assert inputs.bootstrap is not None
    assert inputs.bootstrap_source_root is not None
    assert inputs.bootstrap_render is not None
    assert inputs.bootstrap_rendered is not None
    legacy_prefix = application_failure_prefix(inputs.legacy_source)
    bootstrap_prefix = application_failure_prefix(inputs.bootstrap)
    legacy_identity, bootstrap_identity = validate_identity_pair(
        inputs.legacy_source,
        inputs.bootstrap,
        legacy_prefix,
        bootstrap_prefix,
        failures,
    )
    _, bootstrap_destination = validate_bootstrap_destinations(
        inputs.legacy_source,
        inputs.bootstrap,
        legacy_prefix,
        bootstrap_prefix,
        failures,
    )
    bootstrap_wave = record_bootstrap_application_safety_failures(
        inputs.bootstrap,
        bootstrap_prefix,
        failures,
    )
    bootstrap_project_resource = validate_bootstrap_project(
        inputs.bootstrap,
        bootstrap_identity,
        bootstrap_destination,
        projects,
        bootstrap_prefix,
        failures,
    )

    validate_bootstrap_source(
        inputs,
        bootstrap_project_resource,
        bootstrap_destination,
        bootstrap_prefix,
        failures,
    )

    validate_bootstrap_render(
        inputs.bootstrap_rendered,
        bootstrap_destination,
        inputs.bootstrap_render,
        failures,
    )

    validate_target(
        inputs,
        legacy_identity,
        bootstrap_identity,
        bootstrap_destination,
        bootstrap_wave,
        bootstrap_prefix,
        projects,
        failures,
    )

    if failures:
        for failure in failures:
            print(failure)
        print(f"FAIL: {len(failures)} Argo CD / Helm identity-migration contract violation(s)")
        return 1

    if inputs.target is not None:
        print(
            "PASS: exact bootstrap/target Applications, reproducible target render, and "
            "protected Namespace satisfy the identity-migration contract"
        )
    else:
        print("PASS: exact bootstrap Application and protected Namespace satisfy the identity-migration contract")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
