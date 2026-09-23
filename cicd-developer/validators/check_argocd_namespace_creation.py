#!/usr/bin/env python3
"""Validate CreateNamespace against the target AppProject and bootstrap order.

Usage:
  check_argocd_namespace_creation.py <cluster-dir> \
    --application <changed-application.yaml> [--application ...]

The explicit Application list keeps this an incremental gate: it evaluates only
new or changed Applications while loading AppProject and bootstrap context from
the complete argocd-apps cluster directory. It is intentionally not part of
``validate.sh`` because a fleet-wide run would turn pre-existing namespace debt
into unrelated MR failures.

Exit codes: 0 pass, 1 contract violation, 2 usage/dependency error.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

try:
    import yaml  # noqa: F401  (keeps the dependency error explicit)
except ImportError:
    print("FAIL: PyYAML not installed", file=sys.stderr)
    sys.exit(2)

from _scan import ParseError, is_application, iter_docs


CREATE_NAMESPACE = "createnamespace=true"
SYNC_WAVE = "argocd.argoproj.io/sync-wave"


@dataclass(frozen=True)
class Resource:
    file: Path
    doc: dict


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Check changed ArgoCD Applications against AppProject Namespace "
            "permission and an earlier authorized bootstrap"
        )
    )
    parser.add_argument("cluster_dir", type=Path)
    parser.add_argument(
        "--application",
        action="append",
        required=True,
        type=Path,
        help="changed Application YAML, absolute or relative to cluster-dir",
    )
    return parser.parse_args(argv[1:])


def normalized_path(path: Path) -> Path:
    return path.resolve(strict=False)


def target_paths(root: Path, values: list[Path]) -> set[Path]:
    targets: set[Path] = set()
    for value in values:
        path = value if value.is_absolute() else root / value
        targets.add(normalized_path(path))
    return targets


def is_appproject(doc: object) -> bool:
    return (
        isinstance(doc, dict)
        and doc.get("kind") == "AppProject"
        and str(doc.get("apiVersion") or "").startswith("argoproj.io/")
    )


def resource_name(doc: dict) -> str:
    metadata = doc.get("metadata") or {}
    return str(metadata.get("name") or "<unnamed>")


def project_allows_namespace(project: dict) -> bool:
    whitelist = (project.get("spec") or {}).get("clusterResourceWhitelist") or []
    for entry in whitelist:
        if not isinstance(entry, dict):
            continue
        if entry.get("group") in {"", "*"} and entry.get("kind") in {
            "Namespace",
            "*",
        }:
            return True
    return False


def creates_namespace(app: dict) -> bool:
    sync_policy = (app.get("spec") or {}).get("syncPolicy") or {}
    options = sync_policy.get("syncOptions") or []
    return any(
        isinstance(option, str) and option.strip().lower() == CREATE_NAMESPACE
        for option in options
    )


def sync_wave(app: dict) -> int | None:
    annotations = ((app.get("metadata") or {}).get("annotations") or {})
    value = annotations.get(SYNC_WAVE, "0")
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def destination(app: dict) -> tuple[str, str]:
    value = (app.get("spec") or {}).get("destination") or {}
    cluster = value.get("server") or value.get("name") or ""
    namespace = value.get("namespace") or ""
    return str(cluster), str(namespace)


def is_namespace(doc: object) -> bool:
    return (
        isinstance(doc, dict)
        and doc.get("apiVersion") == "v1"
        and doc.get("kind") == "Namespace"
    )


def load_context(
    root: Path,
) -> tuple[list[Resource], dict[str, list[Resource]], list[Resource], list[str]]:
    applications: list[Resource] = []
    projects: dict[str, list[Resource]] = {}
    namespaces: list[Resource] = []
    failures: list[str] = []

    for file, doc in iter_docs(root):
        if isinstance(doc, ParseError):
            failures.append(f"FAIL: {file}: {doc.message}")
            continue
        if is_application(doc):
            applications.append(Resource(file, doc))
        elif is_appproject(doc):
            projects.setdefault(resource_name(doc), []).append(Resource(file, doc))
        elif is_namespace(doc):
            namespaces.append(Resource(file, doc))
    return applications, projects, namespaces, failures


def one_project(
    projects: dict[str, list[Resource]],
    name: str,
) -> tuple[Resource | None, str | None]:
    matches = projects.get(name) or []
    if not matches:
        return None, f"AppProject/{name} is absent from the supplied cluster directory"
    if len(matches) > 1:
        files = ", ".join(str(item.file) for item in matches)
        return None, f"AppProject/{name} has multiple definitions: {files}"
    return matches[0], None


def is_below(path: Path, directory: Path) -> bool:
    try:
        normalized_path(path).relative_to(normalized_path(directory))
    except ValueError:
        return False
    return True


def local_source_directories(app: dict, root: Path) -> list[Path]:
    spec = app.get("spec") or {}
    sources: list[dict] = []
    if isinstance(spec.get("source"), dict):
        sources.append(spec["source"])
    sources.extend(source for source in spec.get("sources") or [] if isinstance(source, dict))

    result: list[Path] = []
    for source in sources:
        source_path = source.get("path")
        if not isinstance(source_path, str) or not source_path:
            continue
        for candidate in (root.parent / source_path, root / source_path):
            if candidate.is_dir() and is_below(candidate, root):
                result.append(candidate)
                break
    return result


def namespace_is_delete_safe(namespace: dict) -> bool:
    annotations = ((namespace.get("metadata") or {}).get("annotations") or {})
    value = annotations.get("argocd.argoproj.io/sync-options", "")
    options = {
        option.strip().lower()
        for option in str(value).split(",")
        if option.strip()
    }
    return {"prune=false", "delete=false"} <= options


def renders_protected_namespace(
    app: dict,
    namespace: str,
    root: Path,
    namespaces: list[Resource],
) -> bool:
    source_dirs = local_source_directories(app, root)
    return any(
        resource_name(resource.doc) == namespace
        and namespace_is_delete_safe(resource.doc)
        and any(is_below(resource.file, source_dir) for source_dir in source_dirs)
        for resource in namespaces
    )


def authorized_bootstrap_name(
    candidate: Resource,
    target: Resource,
    target_identity: tuple[str, str, int],
    projects: dict[str, list[Resource]],
    namespaces: list[Resource],
    root: Path,
) -> str | None:
    target_cluster, target_namespace, target_wave = target_identity
    if candidate == target:
        return None

    candidate_cluster, candidate_namespace = destination(candidate.doc)
    if candidate_cluster != target_cluster:
        return None

    candidate_wave = sync_wave(candidate.doc)
    if candidate_wave is None or candidate_wave >= target_wave:
        return None

    project_name = str((candidate.doc.get("spec") or {}).get("project") or "")
    project, error = one_project(projects, project_name)
    if error is not None or project is None:
        return None
    if not project_allows_namespace(project.doc):
        return None

    creates_exact_destination = (
        creates_namespace(candidate.doc) and candidate_namespace == target_namespace
    )
    renders_exact_namespace = renders_protected_namespace(
        candidate.doc,
        target_namespace,
        root,
        namespaces,
    )
    if not (creates_exact_destination or renders_exact_namespace):
        return None
    return resource_name(candidate.doc)


def authorized_bootstraps(
    target: Resource,
    applications: list[Resource],
    projects: dict[str, list[Resource]],
    namespaces: list[Resource],
    root: Path,
) -> list[str]:
    target_cluster, target_namespace = destination(target.doc)
    target_wave = sync_wave(target.doc)
    if target_wave is None:
        return []
    target_identity = (target_cluster, target_namespace, target_wave)

    names = (
        authorized_bootstrap_name(
            candidate,
            target,
            target_identity,
            projects,
            namespaces,
            root,
        )
        for candidate in applications
    )
    return [name for name in names if name is not None]


def application_failure(
    target: Resource,
    applications: list[Resource],
    projects: dict[str, list[Resource]],
    namespaces: list[Resource],
    root: Path,
) -> str | None:
    if not creates_namespace(target.doc):
        return None

    name = resource_name(target.doc)
    spec = target.doc.get("spec") or {}
    project_name = str(spec.get("project") or "")
    _, namespace = destination(target.doc)
    prefix = f"FAIL: {target.file}: Application/{name}"

    if not project_name:
        return f"{prefix} uses CreateNamespace=true but spec.project is empty"
    if not namespace:
        return f"{prefix} uses CreateNamespace=true but destination.namespace is empty"
    if sync_wave(target.doc) is None:
        return f"{prefix} has a non-integer {SYNC_WAVE}"

    project, error = one_project(projects, project_name)
    if error is not None or project is None:
        return f"{prefix} cannot validate namespace creation: {error}"
    if project_allows_namespace(project.doc):
        return None
    if authorized_bootstraps(target, applications, projects, namespaces, root):
        return None

    return (
        f"{prefix} uses CreateNamespace=true for {namespace!r}, but "
        f"AppProject/{project_name} does not allow core /Namespace and no lower-wave "
        "Application in the same cluster directory creates that exact destination "
        "namespace or renders a protected exact Namespace through an AppProject that "
        "does; add an authorized exact-namespace bootstrap "
        "and health-gate it, or choose the correct owning project--do not broaden the "
        "project or move the workload to default only to clear SyncFailed"
    )


def main(argv: list[str]) -> int:
    args = parse_args(argv)

    root = args.cluster_dir
    if not root.is_dir():
        print(f"FAIL: cluster directory not found: {root}", file=sys.stderr)
        return 2

    requested = target_paths(root, args.application)
    missing = sorted(path for path in requested if not path.is_file())
    if missing:
        for path in missing:
            print(f"FAIL: Application file not found: {path}")
        return 2

    applications, projects, namespaces, failures = load_context(root)
    targets = [
        resource
        for resource in applications
        if normalized_path(resource.file) in requested
    ]
    found_files = {normalized_path(resource.file) for resource in targets}
    for path in sorted(requested - found_files):
        failures.append(f"FAIL: {path}: no ArgoCD Application document found")

    for target in targets:
        failure = application_failure(target, applications, projects, namespaces, root)
        if failure is not None:
            failures.append(failure)

    if failures:
        for failure in failures:
            print(failure)
        print(f"FAIL: {len(failures)} namespace creation contract violation(s)")
        return 1

    print(
        f"PASS: {len(targets)} changed Application(s) match AppProject/namespace "
        "bootstrap contracts"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
