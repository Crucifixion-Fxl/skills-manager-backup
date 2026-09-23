#!/usr/bin/env python3
"""check_repo_boundary.py <directory> [--repo-context <context>]

Validate repository ownership for application and DEV/k8s manifests.

Application repositories may own workloads and app-owned data-plane Crossplane
resources (high-level ObjectBucket, S3/RDS/Aurora/ElastiCache/CloudFront, etc.).
They must not declare
centralized permission or platform boundary resources such as IAM/IRSA Roles,
ProviderConfigs, WAF resources, or shared SecurityGroupIngressRule objects.

DEV/k8s owns reusable platform capabilities, not static desired state for one
business application. Review callers must pass ``--repo-context k8s`` when
they copy changed files to a temporary directory, because the temporary path
cannot identify the source repository.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

try:
    import yaml  # noqa: F401  (kept so the dep-missing message stays consistent)
except ImportError:
    print("FAIL: PyYAML not installed", file=sys.stderr)
    sys.exit(2)

from _scan import ParseError, iter_docs


GROUP_PREFIX_IAM = "iam.aws."
GROUP_PREFIX_WAFV2 = "wafv2.aws."
GROUP_ARGOCD = "argoproj.io"
GROUP_AWS_CLUSTER_PROVIDER = "aws.m.upbound.io"
GROUP_AWS_PROVIDER = "aws.upbound.io"
GROUP_CAM = "cam.tencentcloud.crossplane.io"
GROUP_EC2 = "ec2.aws.m.upbound.io"
GROUP_EC2_LEGACY = "ec2.aws.upbound.io"
GROUP_PREFIX_EC2 = "ec2.aws."
GROUP_NINEDATA = "ninedata.crossplane.io"
GROUP_TENCENT_PROVIDER = "tencentcloud.crossplane.io"

KIND_APPLICATION = "Application"
KIND_CLUSTER_PROVIDER_CONFIG = "ClusterProviderConfig"
KIND_PROVIDER_CONFIG = "ProviderConfig"
KIND_ROLE = "Role"
KIND_POLICY = "Policy"
KIND_ROLE_POLICY = "RolePolicy"
KIND_ROLE_POLICY_ATTACHMENT = "RolePolicyAttachment"
KIND_SG_EGRESS_RULE = "SecurityGroupEgressRule"
KIND_SG_INGRESS_RULE = "SecurityGroupIngressRule"
UNNAMED = "<unnamed>"
FAIL_PREFIX = "FAIL: "

REPO_CONTEXT_APP = "app"
REPO_CONTEXT_ARGOCD = "argocd-apps"
REPO_CONTEXT_AUTO = "auto"
REPO_CONTEXT_CROSSPLANE = "crossplane-infra"
REPO_CONTEXT_K8S = "k8s"
REPO_CONTEXTS = (
    REPO_CONTEXT_AUTO,
    REPO_CONTEXT_APP,
    REPO_CONTEXT_K8S,
    REPO_CONTEXT_ARGOCD,
    REPO_CONTEXT_CROSSPLANE,
)

GROUP_EXTERNAL_SECRETS = "external-secrets.io"
GROUP_PLATFORM = "platform.addx.io"
KIND_DATABASE_CLAIM = "Database"
KIND_EXTERNAL_SECRET = "ExternalSecret"
KIND_KAFKA_CREDENTIAL_CLAIM = "KafkaScramCredential"
KIND_PUSH_SECRET = "PushSecret"
KIND_SECRET_STORE = "SecretStore"
PROVIDER_SQL_BUSINESS_KINDS = {"Database", "Grant", "Role", "User"}
PLATFORM_APP_CLAIM_KINDS = {
    KIND_DATABASE_CLAIM,
    KIND_KAFKA_CREDENTIAL_CLAIM,
}

PROVIDER_CONFIG_KINDS = {
    KIND_PROVIDER_CONFIG,
    KIND_CLUSTER_PROVIDER_CONFIG,
}
SECURITY_GROUP_RULE_KINDS = {
    KIND_SG_EGRESS_RULE,
    KIND_SG_INGRESS_RULE,
}

REASON_APPLICATION = "ArgoCD Application belongs in argocd-apps, not an app k8s overlay"
REASON_PROVIDER_CONFIG = "ProviderConfig is a centralized identity boundary and belongs in crossplane-infra"
REASON_IAM = "IAM/IRSA/CAM resources belong in crossplane-infra"
REASON_WAFV2 = "WAFv2 resources belong in crossplane-infra; app manifests should consume reviewed WAF ARNs"
REASON_SECURITY_GROUP = "shared security group rules belong in crossplane-infra"
REASON_DEFAULT = "centralized platform boundary resource belongs outside the app repository"
REASON_K8S_APP_CLAIM = (
    "business application claims belong in the application repository; "
    "DEV/k8s owns only the reusable platform capability"
)
REASON_K8S_ESO_IDENTITY = (
    "static per-app ESO identity fan-out is forbidden in DEV/k8s; derive it from "
    "an app-owned declaration through a reusable platform capability"
)
REASON_K8S_PROVIDER_SQL = (
    "raw per-app provider-sql Database/User/Role/Grant resources are forbidden in DEV/k8s; "
    "declare the requirement in the application claim"
)
REASON_K8S_PUSH_SECRET = (
    "literal per-app Vault delivery is forbidden in DEV/k8s; a reusable "
    "Composition/controller must derive it from the application claim"
)
REASON_K8S_EXTERNAL_SECRET = (
    "an ExternalSecret consuming a literal per-app Vault path belongs with the "
    "application workload and lifecycle, not in DEV/k8s"
)
REASON_K8S_SECRET_STORE = (
    "a namespace-local SecretStore for a business namespace belongs in the "
    "application repository; its runtime namespace does not transfer source ownership"
)
REASON_ARGOCD_SOURCE = (
    "active .argocd-source-* files are retired; Argo CD manifest generation "
    "can override the Application source. Keep history in Git commits/MRs and "
    "use the argocd-apps Application recovery seed"
)

CENTRALIZED_GROUP_PREFIXES = (
    GROUP_PREFIX_IAM,
    GROUP_PREFIX_WAFV2,
)
CENTRAL_REPO_PATH_PREFIXES = (
    "crossplane-infra/",
    "argocd-apps/",
    "crossplane/",
    "argocd/",
)
CENTRAL_REPO_NAMES = {"crossplane-infra", "argocd-apps"}


CENTRALIZED_API_KINDS = {
    (GROUP_AWS_CLUSTER_PROVIDER, KIND_CLUSTER_PROVIDER_CONFIG),
    (GROUP_AWS_PROVIDER, KIND_PROVIDER_CONFIG),
    (GROUP_NINEDATA, KIND_CLUSTER_PROVIDER_CONFIG),
    (GROUP_TENCENT_PROVIDER, KIND_PROVIDER_CONFIG),
    (GROUP_CAM, KIND_ROLE),
    (GROUP_CAM, KIND_POLICY),
    (GROUP_CAM, KIND_ROLE_POLICY),
    (GROUP_CAM, KIND_ROLE_POLICY_ATTACHMENT),
    (GROUP_EC2, KIND_SG_INGRESS_RULE),
    (GROUP_EC2, KIND_SG_EGRESS_RULE),
    (GROUP_EC2_LEGACY, KIND_SG_INGRESS_RULE),
    (GROUP_EC2_LEGACY, KIND_SG_EGRESS_RULE),
    (GROUP_ARGOCD, KIND_APPLICATION),
}


def api_group(doc: dict[str, Any]) -> str:
    api_version = doc.get("apiVersion")
    if not isinstance(api_version, str):
        return ""
    return api_version.split("/", 1)[0]


def metadata_name(doc: dict[str, Any]) -> str:
    metadata = doc.get("metadata")
    if not isinstance(metadata, dict):
        return UNNAMED
    value = metadata.get("name")
    return value if isinstance(value, str) else UNNAMED


def metadata_namespace(doc: dict[str, Any]) -> str:
    metadata = doc.get("metadata")
    if not isinstance(metadata, dict):
        return ""
    value = metadata.get("namespace")
    return value if isinstance(value, str) else ""


def relpath(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def first_segment(path: Path, root: Path) -> str:
    rel = relpath(path, root)
    return rel.split("/", 1)[0]


def has_account_and_suffix(segment: str, prefix: str) -> bool:
    value = segment.removeprefix(prefix)
    account, separator, suffix = value.partition("-")
    return bool(separator) and account.isdigit() and bool(suffix)


def is_cluster_dir(segment: str) -> bool:
    if segment.startswith("aws-"):
        return has_account_and_suffix(segment, "aws-")
    if segment.startswith("tencent-"):
        return has_account_and_suffix(segment, "tencent-")
    return segment.startswith("gcp-") and len(segment) > len("gcp-")


def is_central_repo_path(path: Path, root: Path) -> bool:
    """Return True for paths owned by crossplane-infra / argocd-apps context."""
    rel = relpath(path, root)
    first = first_segment(path, root)
    return (
        root.name in CENTRAL_REPO_NAMES
        or is_cluster_dir(root.name)
        or rel.startswith(CENTRAL_REPO_PATH_PREFIXES)
        or is_cluster_dir(first)
    )


def is_app_manifest_path(path: Path, root: Path) -> bool:
    rel = relpath(path, root)
    parts = rel.split("/")
    if parts[0] in {"base", "overlays"}:
        return True
    for index, part in enumerate(parts[:-1]):
        if part == "k8s" and parts[index + 1] in {"base", "overlays"}:
            return True
    return False


def is_per_app_eso_identity_path(path: Path) -> bool:
    parts = path.parts
    for index in range(len(parts) - 2):
        if parts[index : index + 2] != ("cicd", "eso-app-identities"):
            continue
        # A child directory between eso-app-identities/ and the YAML filename
        # is a static per-app fan-out tree.
        return index + 3 < len(parts)
    return False


def nested_string(mapping: dict[str, Any], path: tuple[str, ...]) -> str | None:
    current: Any = mapping
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current if isinstance(current, str) else None


def nested_strings(value: Any, paths: tuple[tuple[str, ...], ...]) -> list[str]:
    if not isinstance(value, list):
        return []
    strings: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        for path in paths:
            found = nested_string(item, path)
            if found is not None:
                strings.append(found)
    return strings


def push_secret_remote_keys(doc: dict[str, Any]) -> list[str]:
    spec = doc.get("spec")
    if not isinstance(spec, dict):
        return []
    return nested_strings(
        spec.get("data"),
        (
            ("remoteRef", "remoteKey"),
            ("match", "remoteRef", "remoteKey"),
        ),
    )


def external_secret_remote_keys(doc: dict[str, Any]) -> list[str]:
    spec = doc.get("spec")
    if not isinstance(spec, dict):
        return []
    data_keys = nested_strings(spec.get("data"), (("remoteRef", "key"),))
    data_from_keys = nested_strings(
        spec.get("dataFrom"),
        (
            ("extract", "key"),
            ("find", "path"),
        ),
    )
    return data_keys + data_from_keys


def is_literal_segment(value: str) -> bool:
    return bool(value) and not any(marker in value for marker in ("$", "{", "}", "*"))


def is_literal_business_vault_path(value: str) -> bool:
    parts = value.strip("/").split("/")
    if parts and parts[0] == "secret":
        parts = parts[1:]
    if len(parts) >= 4 and parts[1] == "app":
        return is_literal_segment(parts[2])
    if len(parts) >= 5 and parts[2] == "application":
        return is_literal_segment(parts[3])
    return False


def is_business_namespace(value: str) -> bool:
    phase, separator, app = value.partition("-")
    return bool(separator and app) and phase in {"dev", "staging", "pre", "prod"}


def is_business_delivery_namespace(value: str) -> bool:
    # crossplane-system is where the historical anti-pattern placed helper
    # ExternalSecrets for one application's hand-written provisioning job.
    return value == "crossplane-system" or is_business_namespace(value)


def iter_boundary_objects(doc: dict[str, Any]):
    """Expand Kubernetes List wrappers so ownership checks cannot be bypassed."""
    if doc.get("kind") == "List" and isinstance(doc.get("items"), list):
        for item in doc["items"]:
            if isinstance(item, dict):
                yield from iter_boundary_objects(item)
        return
    yield doc


def is_provider_sql_business_resource(group: str, kind: Any) -> bool:
    return group.endswith(".sql.crossplane.io") and kind in PROVIDER_SQL_BUSINESS_KINDS


def is_business_push_secret(doc: dict[str, Any], group: str, kind: Any) -> bool:
    if group != GROUP_EXTERNAL_SECRETS or kind != KIND_PUSH_SECRET:
        return False
    return any(
        is_literal_business_vault_path(key)
        for key in push_secret_remote_keys(doc)
    )


def is_business_external_secret(doc: dict[str, Any], group: str, kind: Any) -> bool:
    if group != GROUP_EXTERNAL_SECRETS or kind != KIND_EXTERNAL_SECRET:
        return False
    if not is_business_delivery_namespace(metadata_namespace(doc)):
        return False
    return any(
        is_literal_business_vault_path(key)
        for key in external_secret_remote_keys(doc)
    )


def is_business_secret_store(doc: dict[str, Any], group: str, kind: Any) -> bool:
    return (
        group == GROUP_EXTERNAL_SECRETS
        and kind == KIND_SECRET_STORE
        and is_business_namespace(metadata_namespace(doc))
    )


def k8s_boundary_reason(path: Path, doc: dict[str, Any]) -> str | None:
    group = api_group(doc)
    kind = doc.get("kind")

    if is_per_app_eso_identity_path(path):
        return REASON_K8S_ESO_IDENTITY
    if group == GROUP_PLATFORM and kind in PLATFORM_APP_CLAIM_KINDS:
        return REASON_K8S_APP_CLAIM
    if is_provider_sql_business_resource(group, kind):
        return REASON_K8S_PROVIDER_SQL
    if is_business_push_secret(doc, group, kind):
        return REASON_K8S_PUSH_SECRET
    if is_business_external_secret(doc, group, kind):
        return REASON_K8S_EXTERNAL_SECRET
    if is_business_secret_store(doc, group, kind):
        return REASON_K8S_SECRET_STORE
    return None


def is_centralized_resource(doc: dict[str, Any]) -> bool:
    group = api_group(doc)
    kind = doc.get("kind")
    if not isinstance(kind, str):
        return False
    if kind in SECURITY_GROUP_RULE_KINDS and group.startswith(GROUP_PREFIX_EC2):
        return True
    if any(group.startswith(prefix) for prefix in CENTRALIZED_GROUP_PREFIXES):
        return True
    return (group, kind) in CENTRALIZED_API_KINDS


def reason(doc: dict[str, Any]) -> str:
    group = api_group(doc)
    kind = doc.get("kind")
    if (group, kind) == (GROUP_ARGOCD, KIND_APPLICATION):
        return REASON_APPLICATION
    if kind in PROVIDER_CONFIG_KINDS:
        return REASON_PROVIDER_CONFIG
    if group.startswith(GROUP_PREFIX_IAM) or group == GROUP_CAM:
        return REASON_IAM
    if group.startswith(GROUP_PREFIX_WAFV2):
        return REASON_WAFV2
    if kind in SECURITY_GROUP_RULE_KINDS:
        return REASON_SECURITY_GROUP
    return REASON_DEFAULT


def boundary_failure(
    path: Path,
    root: Path,
    doc: dict[str, Any],
    repo_context: str = REPO_CONTEXT_AUTO,
) -> str | None:
    if repo_context == REPO_CONTEXT_K8S:
        violation_reason = k8s_boundary_reason(path, doc)
        if violation_reason is None:
            return None
        return (
            f"{FAIL_PREFIX}{path}: {doc.get('kind')}/{metadata_name(doc)} "
            "violates repository boundary: "
            f"{violation_reason}"
        )
    if repo_context in {REPO_CONTEXT_ARGOCD, REPO_CONTEXT_CROSSPLANE}:
        return None
    if repo_context == REPO_CONTEXT_AUTO and is_central_repo_path(path, root):
        return None
    if (
        api_group(doc) == GROUP_ARGOCD
        and doc.get("kind") == KIND_APPLICATION
        and not is_app_manifest_path(path, root)
    ):
        return None
    if not is_centralized_resource(doc):
        return None

    return (
        f"{FAIL_PREFIX}{path}: {doc.get('kind')}/{metadata_name(doc)} "
        "violates repository boundary: "
        f"{reason(doc)}"
    )


def parse_args(argv: list[str]) -> argparse.Namespace | None:
    parser = argparse.ArgumentParser(prog="check_repo_boundary.py")
    parser.add_argument("directory", type=Path)
    parser.add_argument(
        "--repo-context",
        choices=REPO_CONTEXTS,
        default=REPO_CONTEXT_AUTO,
    )
    args = parser.parse_args(argv[1:])
    if not args.directory.is_dir():
        print(f"FAIL: directory not found: {args.directory}", file=sys.stderr)
        return None
    return args


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    if args is None:
        return 2
    root = args.directory

    failures: list[str] = []
    checked_docs = 0

    if args.repo_context in {REPO_CONTEXT_APP, REPO_CONTEXT_AUTO}:
        for path in root.rglob(".argocd-source-*.yaml"):
            if args.repo_context == REPO_CONTEXT_AUTO and is_central_repo_path(path, root):
                continue
            failures.append(
                f"{FAIL_PREFIX}{path}: retired image parameter file violates "
                f"repository boundary: {REASON_ARGOCD_SOURCE}"
            )

    for path, doc in iter_docs(root):
        if isinstance(doc, ParseError):
            # Parse errors are surfaced by other validators; keep this check
            # focused on repository-boundary semantics.
            continue
        if not isinstance(doc, dict):
            continue
        for boundary_object in iter_boundary_objects(doc):
            checked_docs += 1
            failure = boundary_failure(
                path,
                root,
                boundary_object,
                args.repo_context,
            )
            if failure is not None:
                failures.append(failure)

    for failure in failures:
        print(failure)

    if failures:
        print(f"{FAIL_PREFIX}{len(failures)} repository boundary violation(s)")
        return 1

    if checked_docs == 0:
        print(f"PASS: no app-repo centralized boundary resources under {root} (nothing to check)")
        return 0

    print(
        f"PASS: checked {checked_docs} YAML document(s) for "
        f"{args.repo_context} repository boundary resources"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
