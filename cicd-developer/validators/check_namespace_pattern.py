#!/usr/bin/env python3
"""check_namespace_pattern.py <directory> [--objectbucket-target <target>]

Verifies two independent business-app namespace contracts:
  - The phase prefix must pass the fluent-bit business-namespace gate.
  - New workloads must use the exact ownership form {phase}-{app}.

An exact legacy identity may retain a shared namespace through either an
explicit built-in grandfather or the controlled, time-bounded exception
registry. Registry-managed exceptions must remain phase-prefixed. Registry
errors are configuration failures and do not widen runtime matching.

The cluster fluent-bit Lua gate admits only namespace == "default" or names
starting with one of: staging-, pre-, prod-, canary-, test-. Anything else is
dropped at the entry (return -1) -> every pod log in that namespace is silently
discarded, never reaches ES, and raises no alert (the app still runs and
`kubectl logs` still works, so the loss is easy to miss). The naming
convention is {phase}-{app} (prefix form). The suffix form {app}-{phase}
(e.g. my-app-staging), {app}-prod, {app}-pre and bare {app} are forbidden.

Scope -- only business apps are checked:
  - ArgoCD Application carrying the
    argocd-image-updater.argoproj.io/image-list annotation
    -> spec.destination.namespace must be conformant.
  - kustomize overlay whose images[].newName points at a {harbor}/cicd/... path
    (business overlay) -> top-level `namespace:` must be conformant.
Platform / Helm-managed Applications land in infrastructure namespaces
(argo-cd, kyverno, logging, crossplane-system, external-secrets, kube-system,
registry, ...) and are NOT checked.

Business ownership:
  - One cicd image keeps the legacy contract: its final path segment is the app.
  - Multiple cicd component images require authoritative metadata.labels.app.
  - The owner must be kebab-case. Every component image must be either exactly
    the owner or use the <owner>-<component> kebab-case form.
  - The namespace is always {phase}-{owner}, never {phase}-{component}.

Prefix-admissible = namespace is "default" or starts with one of:
  staging-, pre-, prod-, canary-, test-, dev-
(dev- is the dev convention prefix; it is not in the fluent-bit gate, so dev
logs are not shipped to ES -- accepted for dev.) Except for an exact active
legacy identity, the namespace must also equal {phase}-{app}.

SSOT for the per-cluster pattern:
  references/data/env-keywords.yaml -> env_keywords[<keyword>].namespace_pattern
Hard rule #31.

Exit codes: 0 pass, 1 workload violation, 2 dependency/usage/registry error.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import yaml
from _namespace_legacy_exceptions import (
    FLUENT_BIT_PREFIXES,
    KEBAB_CASE_IDENTITY_RE,
    KUSTOMIZATION_FILE,
    LEGACY_EXCEPTIONS_YAML,
    ApplicationException,
    KustomizationException,
    application_matches_exception,
    kustomization_matches_exception,
    load_namespace_legacy_exceptions,
)
from _scan import ParseError, is_application, is_kustomization, iter_docs, resolve_dir

ALLOWED_PREFIXES = (*FLUENT_BIT_PREFIXES, "dev-")
IMAGE_LIST_ANNOTATION = "argocd-image-updater.argoproj.io/image-list"
APP_OWNER_LABEL = "app"
CICD_MARKER = "/cicd/"  # business image path segment: {harbor}/cicd/{env}-{region}/{app}
APP_OWNER_RE = KEBAB_CASE_IDENTITY_RE
OBJECTBUCKET_READINESS_YAML = (
    Path(__file__).resolve().parent.parent
    / "references"
    / "object-storage"
    / "objectbucket-readiness.yaml"
)
OBJECTBUCKET_API_VERSION = "platform.addx.io/v1alpha1"
OBJECTBUCKET_KIND = "ObjectBucket"
OBJECTBUCKET_APP_LABEL = "platform.addx.io/app"

# Exact legacy workloads that cannot move namespaces without a coordinated
# runtime, identity, Vault and Argo CD migration. Keep every identity dimension
# in the key so this cannot become a general bypass for a cluster, app, or
# namespace.
LEGACY_APPLICATION_NAMESPACES = {
    (
        "aws-002497567426-us-tech-service",
        "flink-tech-service-us",
        "staging-us",
        "flink",
    ),
    (
        "aws-302571458622-us-prod",
        "flink-prod-us",
        "prod-us",
        "flink",
    ),
    (
        "aws-740315635167-eu-prod",
        "flink-prod-eu",
        "prod-eu",
        "flink",
    ),
    (
        "aws-741924744516-cn-prod",
        "flink-prod-cn",
        "prod-cn",
        "flink",
    ),
    (
        "gcp-a4xcloud-tech-service-us-us-tech-service",
        "dvc-remote-staging-us-gcp",
        "staging-us-gcp",
        "dvc-remote",
    ),
    (
        "aws-002497567426-us-tech-service",
        "superset-prod-us-restricted-admin",
        "superset",
        "superset",
    ),
}

# The application-repository side of the same legacy deployments. Match the
# complete Git-worktree-relative path plus namespace and image app so changing
# the scan root cannot authorize a sibling overlay or reject an exact identity.
LEGACY_KUSTOMIZATION_NAMESPACES = {
    (
        ("k8s", "flink", "overlays", "tech-service-us", KUSTOMIZATION_FILE),
        "staging-us",
        "flink",
    ),
    (
        ("k8s", "flink", "overlays", "prod-us", KUSTOMIZATION_FILE),
        "prod-us",
        "flink",
    ),
    (
        ("k8s", "flink", "overlays", "prod-eu", KUSTOMIZATION_FILE),
        "prod-eu",
        "flink",
    ),
    (
        ("k8s", "flink", "overlays", "prod-cn", KUSTOMIZATION_FILE),
        "prod-cn",
        "flink",
    ),
    (
        ("k8s", "overlays", "staging-us-gcp", KUSTOMIZATION_FILE),
        "staging-us-gcp",
        "dvc-remote",
    ),
    (
        (
            "k8s",
            "superset",
            "overlays",
            "prod-us-restricted-admin",
            KUSTOMIZATION_FILE,
        ),
        "superset",
        "superset",
    ),
}


def load_additional_legacy_namespaces(
    path: Path = LEGACY_EXCEPTIONS_YAML,
    *,
    today: date | None = None,
) -> tuple[set[ApplicationException], set[KustomizationException]]:
    """Compatibility surface for existing namespace-validator callers."""
    return load_namespace_legacy_exceptions(path, today=today)


additional_applications, additional_kustomizations = (
    load_additional_legacy_namespaces()
)
LEGACY_APPLICATION_NAMESPACES.update(additional_applications)
LEGACY_KUSTOMIZATION_NAMESPACES.update(additional_kustomizations)


def load_scoped_ga_targets() -> dict[str, set[str]]:
    try:
        data = (
            yaml.safe_load(OBJECTBUCKET_READINESS_YAML.read_text(encoding="utf-8"))
            or {}
        )
    except (OSError, yaml.YAMLError) as exc:
        print(
            f"FAIL: cannot load {OBJECTBUCKET_READINESS_YAML}: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(2) from exc

    targets = data.get("targets") or {}
    if not isinstance(targets, dict):
        print(
            f"FAIL: {OBJECTBUCKET_READINESS_YAML}: targets must be a mapping",
            file=sys.stderr,
        )
        raise SystemExit(2)

    scoped_ga_targets: dict[str, set[str]] = {}
    for target_name, target in targets.items():
        if not isinstance(target, dict):
            continue
        prerequisites = target.get("prerequisites") or {}
        if (
            target.get("cloud") == "gcp"
            and target.get("phase") == "scoped_ga"
            and target.get("developer_enabled") is True
            and prerequisites
            and all(value in {"ready", "passed"} for value in prerequisites.values())
        ):
            scoped_ga_targets[target_name] = set(
                target.get("approved_namespaces") or []
            )
    return scoped_ga_targets


SCOPED_GA_OBJECTBUCKET_TARGETS = load_scoped_ga_targets()


def is_conformant(ns: str) -> bool:
    return ns == "default" or ns.startswith(ALLOWED_PREFIXES)


def application_is_business(doc) -> bool:
    metadata = doc.get("metadata") or {}
    annotations = metadata.get("annotations") or {}
    return IMAGE_LIST_ANNOTATION in annotations


def kustomization_is_business(doc) -> bool:
    for image in doc.get("images") or []:
        if isinstance(image, dict) and CICD_MARKER in (image.get("newName") or ""):
            return True
    return False


def is_business_object(doc) -> bool:
    return (is_application(doc) and application_is_business(doc)) or (
        is_kustomization(doc) and kustomization_is_business(doc)
    )


def business_namespace(doc) -> str | None:
    if is_application(doc):
        return ((doc.get("spec") or {}).get("destination") or {}).get("namespace")
    return doc.get("namespace")  # kustomization


def image_component_apps(doc) -> list[str]:
    """Return every app segment declared by a business cicd image."""
    if is_application(doc):
        metadata = doc.get("metadata") or {}
        annotations = metadata.get("annotations") or {}
        image_list = annotations.get(IMAGE_LIST_ANNOTATION) or ""
        if not isinstance(image_list, str):
            return []
        paths = [
            entry.partition("=")[2].strip()
            for entry in image_list.split(",")
            if "=" in entry
        ]
    else:
        paths = [
            image.get("newName") or ""
            for image in doc.get("images") or []
            if isinstance(image, dict)
        ]

    apps = []
    for path in paths:
        if not isinstance(path, str) or CICD_MARKER not in path:
            continue
        app = path.rstrip("/").rsplit("/", 1)[-1]
        app = app.split("@", 1)[0].split(":", 1)[0]
        if app:
            apps.append(app)
    return apps


def declared_app_owner(doc) -> tuple[bool, object]:
    metadata = doc.get("metadata") or {}
    labels = metadata.get("labels") or {}
    if not isinstance(labels, dict) or APP_OWNER_LABEL not in labels:
        return False, None
    return True, labels.get(APP_OWNER_LABEL)


def component_belongs_to_owner(component: str, owner: str) -> bool:
    if component == owner:
        return True
    if not component.startswith(f"{owner}-"):
        return False
    suffix = component[len(owner) + 1 :]
    return bool(APP_OWNER_RE.fullmatch(suffix))


ARTIFACT_VARIANT_SUFFIXES = frozenset(
    {"active", "passive", "inactive", "dormant", "gray", "canary"}
)


def _artifact_variant_suffix(component: str, owner: str) -> bool:
    """True when component is an explicit artifact-variant path of owner.

    A variant is a different build mode of the SAME application (e.g.
    ``safepush-active``), not a business component (``my-app-server``), so
    only the declared artifact suffixes are accepted here.
    """
    if not component.startswith(f"{owner}-"):
        return False
    suffix = component[len(owner) + 1 :]
    return suffix in ARTIFACT_VARIANT_SUFFIXES


def business_app(doc) -> tuple[str | None, str | None]:
    """Resolve the authoritative app owner and any fail-closed identity error.

    Single-image objects keep the legacy behavior of deriving the owner from
    the image path. Multi-image objects must declare metadata.labels.app, and
    every business component image must equal that owner or use its
    ``<owner>-<component>`` prefix. A declared labels.app is authoritative: a
    single image path may use an explicit artifact-variant suffix
    (``<app>-active`` / ``-passive`` / ``-gray`` etc.) of the same app without
    rewriting the app identity; business components are not variants.
    """
    components = image_component_apps(doc)
    has_declared_owner, raw_owner = declared_app_owner(doc)
    if has_declared_owner:
        if not isinstance(raw_owner, str) or not APP_OWNER_RE.fullmatch(raw_owner):
            return None, (
                "metadata.labels.app must declare one non-empty kebab-case app owner"
            )
    if len(components) == 1:
        owner = components[0]
        if has_declared_owner and raw_owner != owner:
            # A declared metadata.labels.app is authoritative. A single image
            # may target an explicit artifact-variant path (<app>-<variant>,
            # e.g. safepush-active for app safepush); the owner then stays the
            # declared app and the component path is validated below. Business
            # component images (e.g. my-app-server) are NOT variants and keep
            # the exact-match rule.
            if (
                not component_belongs_to_owner(owner, raw_owner)
                or not _artifact_variant_suffix(owner, raw_owner)
            ):
                return None, (
                    f"single business image app {owner!r} must exactly match "
                    f"metadata.labels.app {raw_owner!r}"
                )
            owner = raw_owner
    elif len(components) > 1:
        if not has_declared_owner:
            return None, (
                "multiple business component images require authoritative "
                "metadata.labels.app"
            )
        owner = raw_owner
    else:
        return None, "business object has no usable cicd image app identity"

    if not APP_OWNER_RE.fullmatch(owner):
        return None, f"app owner {owner!r} must be kebab-case"

    unrelated = [
        component
        for component in components
        if not component_belongs_to_owner(component, owner)
    ]
    if unrelated:
        return None, (
            f"business image component(s) {', '.join(repr(item) for item in unrelated)} "
            f"do not belong to authoritative app owner {owner!r}"
        )
    return owner, None


def application_env_prefix(doc) -> str | None:
    """Return a declared environment phase prefix when metadata.labels.env has one."""
    if not is_application(doc):
        return None
    env = (((doc.get("metadata") or {}).get("labels") or {}).get("env") or "")
    if not isinstance(env, str):
        return None
    for prefix in ALLOWED_PREFIXES:
        phase = prefix[:-1]
        if env == phase or env.startswith(f"{phase}-"):
            return prefix
    return None


def object_label(file: Path, doc) -> tuple[str, str]:
    if is_application(doc):
        return "Application", (doc.get("metadata") or {}).get("name", "?")
    return "Kustomization", file.parent.name


def fail_prefix(file: Path, kind: str, name: str, ns: str) -> str:
    return (
        f"FAIL: {file}: {kind}/{name}: namespace '{ns}' is not {{phase}}-{{app}} "
        f"prefix form (must be 'default' or start with one of "
        f"{', '.join(ALLOWED_PREFIXES)}); a non-prefixed namespace is dropped by "
        f"the fluent-bit business-namespace gate -> all pod logs silently lost"
    )


def fail_app(file: Path, kind: str, name: str, ns: str, prefix: str, app: str) -> str:
    return (
        f"FAIL: {file}: {kind}/{name}: namespace '{ns}' must be "
        f"'{prefix}{app}' ({{phase}}-{{app}}, app from authoritative ownership); "
        f"the part after '{prefix}' is '{ns[len(prefix):]}', expected '{app}'"
    )


def fail_identity(file: Path, kind: str, name: str, problem: str) -> str:
    return f"FAIL: {file}: {kind}/{name}: {problem}"


def is_legacy_namespace(
    root: Path,
    file: Path,
    doc,
    name: str,
    ns: str,
    app: str | None,
) -> bool:
    if is_application(doc):
        return application_matches_exception(
            file,
            root,
            name,
            ns,
            app,
            LEGACY_APPLICATION_NAMESPACES,
            doc,
        )
    if is_kustomization(doc):
        return kustomization_matches_exception(
            file,
            root,
            ns,
            app,
            LEGACY_KUSTOMIZATION_NAMESPACES,
        )
    return False


def expected_namespace_prefix(doc, ns: str) -> str | None:
    declared_prefix = application_env_prefix(doc)
    if declared_prefix is not None:
        return declared_prefix
    return next((prefix for prefix in ALLOWED_PREFIXES if ns.startswith(prefix)), None)


def parse_inputs(argv: list[str]) -> tuple[Path | None, str | None]:
    usage = (
        "usage: check_namespace_pattern.py <directory> "
        "[--objectbucket-target <target>]"
    )
    if len(argv) not in {2, 4}:
        print(usage, file=sys.stderr)
        return None, None
    if len(argv) == 4 and argv[2] != "--objectbucket-target":
        print(usage, file=sys.stderr)
        return None, None

    root = resolve_dir(argv[:2], "check_namespace_pattern.py")
    target = argv[3] if len(argv) == 4 else None
    if target and target not in SCOPED_GA_OBJECTBUCKET_TARGETS:
        print(
            f"FAIL: ObjectBucket target {target!r} is not scoped GA in "
            f"{OBJECTBUCKET_READINESS_YAML}",
            file=sys.stderr,
        )
        return None, None
    return root, target


def scoped_ga_overlay_key(
    file: Path,
    doc,
    target: str | None,
) -> tuple[Path, str, str] | None:
    if not target or not isinstance(doc, dict):
        return None
    if (
        doc.get("apiVersion") != OBJECTBUCKET_API_VERSION
        or doc.get("kind") != OBJECTBUCKET_KIND
    ):
        return None

    metadata = doc.get("metadata") or {}
    namespace = metadata.get("namespace")
    app = (metadata.get("labels") or {}).get(OBJECTBUCKET_APP_LABEL)
    if namespace not in SCOPED_GA_OBJECTBUCKET_TARGETS[target] or not app:
        return None
    return file.parent, namespace, app


def check_object(
    root: Path,
    file: Path,
    doc,
    scoped_ga_overlays: set[tuple[Path, str, str]],
) -> str | None:
    ns = business_namespace(doc)
    if not (isinstance(ns, str) and ns):
        return None
    kind, name = object_label(file, doc)
    app, identity_problem = business_app(doc)
    if identity_problem:
        return fail_identity(file, kind, name, identity_problem)
    if is_legacy_namespace(root, file, doc, name, ns, app):
        return None
    if is_kustomization(doc) and app and (file.parent, ns, app) in scoped_ga_overlays:
        return None
    if not is_conformant(ns):
        return fail_prefix(file, kind, name, ns)
    prefix = expected_namespace_prefix(doc, ns)
    if app and prefix and ns != f"{prefix}{app}":
        return fail_app(file, kind, name, ns, prefix, app)
    return None


def main(argv: list[str]) -> int:
    root, objectbucket_target = parse_inputs(argv)
    if root is None:
        return 2
    root = root.resolve()

    documents = list(iter_docs(root))
    scoped_ga_overlays = {
        key
        for file, doc in documents
        if not isinstance(doc, ParseError)
        and (key := scoped_ga_overlay_key(file, doc, objectbucket_target))
        is not None
    }

    bad = 0
    total = 0
    for file, doc in documents:
        if isinstance(doc, ParseError):
            print(f"FAIL: {file}: {doc.message}")
            bad += 1
            continue
        if not is_business_object(doc):
            continue
        total += 1
        problem = check_object(root, file, doc, scoped_ga_overlays)
        if problem:
            print(problem)
            bad += 1

    if bad:
        print(f"FAIL: {bad} namespace violation(s) across {total} business object(s)")
        return 1
    if total == 0:
        print(f"PASS: no business app namespace found in {root} (nothing to check)")
        return 0
    print(f"PASS: {total} business app namespace(s) checked, all conform")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
