#!/usr/bin/env python3
"""Validate passive adoption of an existing kubectl-managed workload.

This is a directed workflow gate, not a fleet manifest validator. It compares a
closed non-secret live snapshot with an exact rendered cohort and a reviewed
adoption contract. ``pre-normalization`` permits only contract-declared image
reference changes, including separately declared non-Image-Updater
tag-to-digest pins; ``post-normalization`` requires live and render to be
identical before an Application exists, and ``passive`` additionally requires
and validates the fixed-SHA Argo CD Application.

Exit codes: 0 pass, 1 contract violation, 2 usage/dependency/input error.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import os
import re
import resource
import signal
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    print("FAIL: PyYAML not installed", file=sys.stderr)
    sys.exit(2)

from _namespace_legacy_exceptions import UniqueKeySafeLoader
from check_argocd_helm_identity_migration import (
    approved_remote_hosts,
    canonical_relative_git_path,
    repository_url_has_embedded_credentials,
    safe_git_repository,
    same_git_repository,
)


API_VERSION = "cicd.addx.io/v1alpha1"
CONTRACT_KIND = "ArgoCDAdoptionContract"
SHA = re.compile(r"^[a-f0-9]{40}$")
DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")
GIT_BRANCH = re.compile(
    r"^(?!-)(?!.*(?:\.\.|//|@\{|\\|[ ~^:?*\[]))[A-Za-z0-9._/-]+(?<![./])$"
)
MUTABLE_TAGS = {"latest", "main", "master", "dev", "staging", "prod"}
SUPPORTED_KINDS = {
    "ConfigMap": {"v1"},
    "Deployment": {"apps/v1"},
    "Ingress": {"networking.k8s.io/v1"},
    "NetworkPolicy": {"networking.k8s.io/v1"},
    "PodDisruptionBudget": {"policy/v1"},
    "Service": {"v1"},
    "ServiceAccount": {"v1"},
}
APPROVAL_STAGES = [
    "source-publish",
    "project-permission",
    "artifact-copy",
    "image-normalization",
    "passive-sync",
    "source-branch-activation",
    "automatic-promotion",
    "writer-retirement",
]
ARGO_TRACKING_ANNOTATIONS = {
    "argocd.argoproj.io/tracking-id",
}
ARGO_TRACKING_LABELS = {"argocd.argoproj.io/instance"}
LIVE_ONLY_LABELS = {"k8slens-edit-resource-version"}
HELM_RELEASE_ANNOTATIONS = {"meta.helm.sh/release-name", "meta.helm.sh/release-namespace"}
LIVE_ONLY_ANNOTATIONS = {
    "deployment.kubernetes.io/revision",
    "kubectl.kubernetes.io/last-applied-configuration",
    "k8slens-edit-resource-version",
    "argocd.argoproj.io/tracking-id",
}
SERVER_ALLOCATED_SERVICE_FIELDS = {
    "spec.clusterIP",
    "spec.clusterIPs",
    "spec.healthCheckNodePort",
    "spec.ipFamilies",
    "spec.ipFamilyPolicy",
}
AI_PREFIX = "argocd-image-updater.argoproj.io/"
IMAGE_REPOSITORY = re.compile(
    r"^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?(?::[0-9]+)?"
    r"(?:/[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?)+$"
)
IMAGE_TAG = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
IMAGE_ALIAS = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
CONTAINER_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")
IMAGE_NAME = re.compile(
    r"^[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?"
    r"(?:/[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?)*$"
)
PLATFORMS = {"linux/amd64", "linux/arm64"}
MAX_YAML_FILES = 256
MAX_YAML_FILE_BYTES = 2 * 1024 * 1024
MAX_YAML_TOTAL_BYTES = 16 * 1024 * 1024
MAX_YAML_DOCUMENTS = 2048
MAX_YAML_NODES = 100_000
MAX_YAML_DEPTH = 100
MAX_DIRECTORY_ENTRIES = 4096
MAX_CREDENTIAL_HELPER_OUTPUT_BYTES = 4096
MAX_CREDENTIAL_HELPER_LINES = 32
MAX_CREDENTIAL_HELPERS = 8
MAX_REMOTE_GIT_OUTPUT_BYTES = 65536
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
CONTRACT_FIELDS = {"apiVersion", "kind", "metadata", "spec"}
METADATA_FIELDS = {"name"}
SPEC_FIELDS = {
    "target",
    "sourceRevision",
    "passiveRevision",
    "inventory",
    "imageProvenance",
    "pinnedImageNormalizations",
    "approvalStages",
    "legacyWriters",
}
INVENTORY_FIELDS = {"included", "excluded"}
RESOURCE_REF_FIELDS = {"apiVersion", "kind", "namespace", "name"}
INCLUDED_FIELDS = RESOURCE_REF_FIELDS | {"uid", "immutableFields", "legacyWriter"}
EXCLUDED_FIELDS = RESOURCE_REF_FIELDS | {"uid", "reason"}
PROVENANCE_FIELDS = {
    "alias",
    "resource",
    "container",
    "sourceImage",
    "normalizedImage",
    "digest",
    "commit",
    "evidence",
    "kustomizeImageName",
    "platforms",
}
PINNED_NORMALIZATION_FIELDS = {
    "resource",
    "container",
    "sourceImage",
    "normalizedImage",
    "digest",
    "evidence",
    "platforms",
}
LEGACY_WRITER_FIELDS = {
    "name",
    "type",
    "state",
    "retirementAfter",
    "disableMethod",
    "verification",
    "writeSet",
}
APPLICATION_FIELDS = {"apiVersion", "kind", "metadata", "spec"}
APPLICATION_METADATA_FIELDS = {"name", "namespace", "finalizers", "labels", "annotations"}
APPLICATION_SPEC_FIELDS = {"project", "source", "destination", "syncPolicy"}
APPLICATION_SOURCE_FIELDS = {"repoURL", "targetRevision", "path", "kustomize"}
APPLICATION_DESTINATION_FIELDS = {"server", "namespace"}
APPLICATION_SYNC_FIELDS = {"automated", "syncOptions"}
APPLICATION_AUTOMATED_FIELDS = {"prune", "selfHeal"}
APPLICATION_KUSTOMIZE_FIELDS = {"images"}
SECRET_SNAPSHOT_FIELDS = {"apiVersion", "kind", "metadata"}
SECRET_SNAPSHOT_METADATA_FIELDS = {"name", "namespace", "uid"}


@dataclass(frozen=True, order=True)
class ResourceRef:
    api_version: str
    kind: str
    namespace: str
    name: str

    def label(self) -> str:
        return f"{self.api_version} {self.kind}/{self.namespace}/{self.name}"


def git_output(directory: Path, *args: str) -> str | None:
    """Run a local read-only query without executing checkout-local hooks."""
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    environment["GIT_TERMINAL_PROMPT"] = "0"
    environment["GIT_CONFIG_NOSYSTEM"] = "1"
    environment["GIT_CONFIG_GLOBAL"] = os.devnull
    environment["GIT_NO_REPLACE_OBJECTS"] = "1"
    environment["GIT_NO_LAZY_FETCH"] = "1"
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(directory),
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.hooksPath=/dev/null",
                *args,
            ],
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", required=True, type=Path)
    parser.add_argument("--live", required=True, type=Path)
    parser.add_argument("--render", required=True, type=Path)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument(
        "--verify-remote-host",
        action="append",
        default=[],
        metavar="HOST",
        help=(
            "explicitly approve the Git host used for the fresh target-branch "
            "proof; repeat for every approved host"
        ),
    )
    parser.add_argument("--application", type=Path)
    parser.add_argument(
        "--phase",
        required=True,
        choices=("pre-normalization", "post-normalization", "passive"),
    )
    return parser.parse_args(argv[1:])


def mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def validate_exact_fields(
    value: object,
    required: set[str],
    field: str,
    failures: list[str],
    *,
    optional: set[str] | None = None,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        failures.append(f"{field} must be a mapping")
        return {}
    non_string = [repr(key) for key in value if not isinstance(key, str)]
    if non_string:
        failures.append(f"{field} field names must be strings: {', '.join(non_string)}")
    actual = {key for key in value if isinstance(key, str)}
    missing = sorted(required - actual)
    if missing:
        failures.append(f"{field} is missing required fields: {missing}")
    unexpected = sorted(actual - required - (optional or set()))
    if unexpected:
        failures.append(f"{field} has unexpected fields: {unexpected}")
    return value


def yaml_complexity_error(documents: list[object]) -> str | None:
    stack = [(document, 0) for document in documents]
    visited: set[int] = set()
    nodes = 0
    while stack:
        value, depth = stack.pop()
        nodes += 1
        if nodes > MAX_YAML_NODES:
            return f"YAML exceeds {MAX_YAML_NODES} decoded values"
        if depth > MAX_YAML_DEPTH:
            return f"YAML exceeds maximum nesting depth {MAX_YAML_DEPTH}"
        if not isinstance(value, (dict, list)):
            continue
        identity = id(value)
        if identity in visited:
            continue
        visited.add(identity)
        if isinstance(value, dict):
            for key, child in value.items():
                stack.append((key, depth + 1))
                stack.append((child, depth + 1))
        else:
            stack.extend((child, depth + 1) for child in value)
    return None


def sanitized_yaml_error(exc: BaseException) -> str:
    if isinstance(exc, RecursionError):
        return "YAML nesting exceeds parser limit"
    if isinstance(exc, yaml.YAMLError):
        duplicate = re.search(r"found duplicate key ([^\n]+)", str(exc))
        if duplicate is not None:
            return f"found duplicate key {duplicate.group(1)}"
        return f"invalid YAML ({type(exc).__name__})"
    return type(exc).__name__


def load_yaml_documents(path: Path) -> tuple[list[object], str | None]:
    try:
        if path.is_symlink():
            return [], "symbolic links are not accepted"
        if not path.is_file():
            return [], "path is not a regular file"
        if path.stat().st_size > MAX_YAML_FILE_BYTES:
            return [], f"file exceeds {MAX_YAML_FILE_BYTES} bytes"
        documents = list(
            yaml.load_all(
                path.read_text(encoding="utf-8"),
                Loader=UniqueKeySafeLoader,
            )
        )
        if len(documents) > MAX_YAML_DOCUMENTS:
            return [], f"file exceeds {MAX_YAML_DOCUMENTS} YAML documents"
        complexity_error = yaml_complexity_error(documents)
        return documents, complexity_error
    except (OSError, UnicodeDecodeError, yaml.YAMLError, RecursionError) as exc:
        return [], sanitized_yaml_error(exc)


def load_contract(path: Path) -> tuple[dict[str, Any] | None, list[str], bool]:
    if not path.is_file():
        return None, [f"contract file not found: {path}"], True
    docs, error = load_yaml_documents(path)
    if error is not None:
        return None, [f"cannot load contract {path}: {error}"], True
    docs = [doc for doc in docs if doc is not None]
    if len(docs) != 1 or not isinstance(docs[0], dict):
        return None, ["contract must contain exactly one YAML mapping document"], True
    return docs[0], [], False


def ref_from(value: object, *, default_namespace: str = "") -> ResourceRef | None:
    item = mapping(value)
    values = (
        item.get("apiVersion"),
        item.get("kind"),
        item.get("namespace", default_namespace),
        item.get("name"),
    )
    if not all(isinstance(part, str) and part for part in values):
        return None
    return ResourceRef(*values)  # type: ignore[arg-type]


def resource_ref(doc: dict[str, Any], default_namespace: str) -> ResourceRef | None:
    metadata = mapping(doc.get("metadata"))
    return ref_from(
        {
            "apiVersion": doc.get("apiVersion"),
            "kind": doc.get("kind"),
            "namespace": metadata.get("namespace", default_namespace),
            "name": metadata.get("name"),
        }
    )


def resource_directory_paths(
    root: Path, label: str
) -> tuple[list[Path], list[str], bool]:
    if not root.is_dir() or root.is_symlink():
        return [], [f"{label} directory not found: {root}"], True
    paths: list[Path] = []
    directories = [root]
    entry_count = 0
    total_bytes = 0
    try:
        while directories:
            directory = directories.pop()
            with os.scandir(directory) as entries:
                for entry in entries:
                    entry_count += 1
                    if entry_count > MAX_DIRECTORY_ENTRIES:
                        return [], [f"{label} directory exceeds {MAX_DIRECTORY_ENTRIES} entries"], True
                    path = Path(entry.path)
                    if entry.is_symlink():
                        return [], [f"{label} directory must not contain symbolic links"], True
                    if entry.is_dir(follow_symlinks=False):
                        directories.append(path)
                        continue
                    if not entry.is_file(follow_symlinks=False) or path.suffix not in {
                        ".yaml",
                        ".yml",
                    }:
                        continue
                    paths.append(path)
                    if len(paths) > MAX_YAML_FILES:
                        return [], [f"{label} directory exceeds {MAX_YAML_FILES} YAML files"], True
                    total_bytes += entry.stat(follow_symlinks=False).st_size
                    if total_bytes > MAX_YAML_TOTAL_BYTES:
                        return [], [f"{label} directory exceeds {MAX_YAML_TOTAL_BYTES} YAML bytes"], True
    except OSError:
        return [], [f"{label} directory metadata cannot be read"], True
    return sorted(paths), [], False


def add_resource_document(
    doc: object,
    path: Path,
    default_namespace: str,
    resources: dict[ResourceRef, dict[str, Any]],
    failures: list[str],
) -> bool:
    if doc is None:
        return False
    if not isinstance(doc, dict):
        failures.append(f"{path}: YAML document must be a mapping")
        return True
    if doc.get("kind") == "List":
        failures.append(f"{path}: kind List is unsupported; provide individual objects")
        return False
    ref = resource_ref(doc, default_namespace)
    if ref is None:
        failures.append(f"{path}: resource identity is incomplete")
        return False
    if ref in resources:
        failures.append(f"{path}: duplicate resource identity {ref.label()}")
        return False
    resources[ref] = doc
    return False


def load_resources(
    root: Path, default_namespace: str, label: str
) -> tuple[dict[ResourceRef, dict[str, Any]], list[str], bool]:
    resources: dict[ResourceRef, dict[str, Any]] = {}
    paths, failures, unavailable = resource_directory_paths(root, label)
    if unavailable:
        return resources, failures, unavailable
    document_count = 0
    for path in paths:
        docs, error = load_yaml_documents(path)
        if error is not None:
            failures.append(f"{path}: invalid YAML: {error}")
            unavailable = True
            continue
        document_count += len(docs)
        if document_count > MAX_YAML_DOCUMENTS:
            failures.append(f"{label} directory exceeds {MAX_YAML_DOCUMENTS} YAML documents")
            unavailable = True
            break
        for doc in docs:
            unavailable = (
                add_resource_document(doc, path, default_namespace, resources, failures)
                or unavailable
            )
    if not resources:
        failures.append(f"{label} directory contains no resource YAML")
        unavailable = True
    return resources, failures, unavailable


def load_yaml_text(text: str) -> tuple[list[object], str | None]:
    if len(text.encode("utf-8")) > MAX_YAML_TOTAL_BYTES:
        return [], f"render exceeds {MAX_YAML_TOTAL_BYTES} YAML bytes"
    try:
        documents = list(yaml.load_all(text, Loader=UniqueKeySafeLoader))
    except (yaml.YAMLError, RecursionError) as exc:
        return [], sanitized_yaml_error(exc)
    if len(documents) > MAX_YAML_DOCUMENTS:
        return [], f"render exceeds {MAX_YAML_DOCUMENTS} YAML documents"
    return documents, yaml_complexity_error(documents)


def resources_from_documents(
    documents: list[object], default_namespace: str, label: str
) -> tuple[dict[ResourceRef, dict[str, Any]], list[str]]:
    resources: dict[ResourceRef, dict[str, Any]] = {}
    failures: list[str] = []
    for doc in documents:
        if doc is None:
            continue
        if not isinstance(doc, dict):
            failures.append(f"{label}: YAML document must be a mapping")
            continue
        if doc.get("kind") == "List":
            failures.append(f"{label}: kind List is unsupported; provide individual objects")
            continue
        ref = resource_ref(doc, default_namespace)
        if ref is None:
            failures.append(f"{label}: resource identity is incomplete")
            continue
        if ref in resources:
            failures.append(f"{label}: duplicate resource identity {ref.label()}")
            continue
        resources[ref] = doc
    if not resources:
        failures.append(f"{label} contains no resource YAML")
    return resources, failures


KUSTOMIZATION_NAMES = ("kustomization.yaml", "kustomization.yml", "Kustomization")
KUSTOMIZATION_PATH_LIST_FIELDS = (
    "bases",
    "components",
    "configurations",
    "crds",
    "patchesStrategicMerge",
    "resources",
)
UNSUPPORTED_KUSTOMIZATION_PLUGIN_FIELDS = ("generators", "transformers")
KUSTOMIZATION_METADATA_FIELDS = {"labels"}
KUSTOMIZATION_FIELDS = {
    "apiVersion",
    "bases",
    "buildMetadata",
    "commonAnnotations",
    "commonLabels",
    "components",
    "configMapGenerator",
    "configurations",
    "crds",
    "generatorOptions",
    "generators",
    "helmCharts",
    "images",
    "kind",
    "labels",
    "metadata",
    "namePrefix",
    "nameSuffix",
    "namespace",
    "openapi",
    "patches",
    "patchesJson6902",
    "patchesStrategicMerge",
    "replacements",
    "replicas",
    "resources",
    "sortOptions",
    "transformers",
    "vars",
}


def kustomization_file(directory: Path) -> Path | None:
    matches = [directory / name for name in KUSTOMIZATION_NAMES if (directory / name).is_file()]
    return matches[0] if len(matches) == 1 else None


def path_has_symlink(root: Path, raw_path: str) -> bool:
    current = root
    for part in Path(raw_path).parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def local_kustomize_dependency(
    raw_value: object,
    directory: Path,
    source_root: Path,
    label: str,
    *,
    allow_key: bool = False,
) -> tuple[Path | None, str | None]:
    if not isinstance(raw_value, str) or not raw_value:
        return None, f"{label} must be a non-empty local path"
    raw_path = raw_value.split("=", 1)[1] if allow_key and "=" in raw_value else raw_value
    if (
        not raw_path
        or len(raw_path) > 1024
        or any(ord(character) < 32 for character in raw_path)
        or "\\" in raw_path
        or "\x00" in raw_path
        or "://" in raw_path
        or "::" in raw_path
        or "?" in raw_path
        or "#" in raw_path
        or raw_path.startswith(("git@", "github.com/", "gitlab.com/"))
        or ".git/" in raw_path
        or ".git//" in raw_path
    ):
        return None, f"{label} must not use a remote or non-canonical dependency"
    if path_has_symlink(directory, raw_path):
        return None, f"{label} must not traverse a symbolic link"
    try:
        resolved = (directory / raw_path).resolve(strict=True)
        resolved.relative_to(source_root)
    except (OSError, RuntimeError, ValueError):
        return None, f"{label} must resolve inside the committed source checkout"
    return resolved, None


KustomizeDependency = tuple[object, str, bool]


def kustomization_metadata_failures(
    document: dict[str, Any], label: str
) -> list[str]:
    if "metadata" not in document:
        return []
    metadata = document.get("metadata")
    if not isinstance(metadata, dict):
        return [f"{label}.metadata must be a mapping"]
    if any(field not in KUSTOMIZATION_METADATA_FIELDS for field in metadata):
        return [f"{label}.metadata has unsupported fields"]
    labels = metadata.get("labels")
    if labels is None:
        return []
    if not isinstance(labels, dict):
        return [f"{label}.metadata.labels must be a mapping"]
    if any(
        not isinstance(key, str)
        or not key
        or not isinstance(value, str)
        for key, value in labels.items()
    ):
        return [f"{label}.metadata.labels must contain string label entries"]
    return []


def dependency_list_field(
    document: dict[str, Any], label: str, field: str
) -> tuple[list[KustomizeDependency], list[str]]:
    values = document.get(field, [])
    if not isinstance(values, list):
        return [], [f"{label}.{field} must be a list"]
    return [
        (value, f"{label}.{field}[{index}]", False)
        for index, value in enumerate(values)
    ], []


def patch_dependency_values(
    document: dict[str, Any], label: str, field: str = "patches"
) -> tuple[list[KustomizeDependency], list[str]]:
    patches = document.get(field, [])
    if not isinstance(patches, list):
        return [], [f"{label}.{field} must be a list"]
    return [
        (patch["path"], f"{label}.{field}[{index}].path", False)
        for index, patch in enumerate(patches)
        if isinstance(patch, dict) and "path" in patch
    ], []


def generator_dependency_values(
    document: dict[str, Any], label: str, generator_field: str
) -> tuple[list[KustomizeDependency], list[str]]:
    generators = document.get(generator_field, [])
    if not isinstance(generators, list):
        return [], [f"{label}.{generator_field} must be a list"]
    dependencies: list[KustomizeDependency] = []
    failures: list[str] = []
    for index, generator in enumerate(generators):
        if not isinstance(generator, dict):
            failures.append(f"{label}.{generator_field}[{index}] must be a mapping")
            continue
        for file_field in ("envs", "files"):
            values = generator.get(file_field, [])
            if not isinstance(values, list):
                failures.append(
                    f"{label}.{generator_field}[{index}].{file_field} must be a list"
                )
                continue
            dependencies.extend(
                (
                    value,
                    f"{label}.{generator_field}[{index}].{file_field}[{value_index}]",
                    file_field == "files",
                )
                for value_index, value in enumerate(values)
            )
    return dependencies, failures


def kustomization_dependency_values(
    document: dict[str, Any], label: str
) -> tuple[list[KustomizeDependency], list[str]]:
    dependencies: list[KustomizeDependency] = []
    failures: list[str] = []
    unexpected_fields = sorted(set(document) - KUSTOMIZATION_FIELDS)
    if unexpected_fields:
        failures.append(f"{label} has unsupported field(s): {unexpected_fields}")
    failures.extend(kustomization_metadata_failures(document, label))
    if document.get("helmCharts") is not None:
        failures.append(f"{label}.helmCharts is unsupported in the offline source render")
    for field in UNSUPPORTED_KUSTOMIZATION_PLUGIN_FIELDS:
        if document.get(field) not in (None, []):
            failures.append(
                f"{label}.{field} plugin configs are unsupported in the offline source render"
            )
    for field in KUSTOMIZATION_PATH_LIST_FIELDS:
        field_dependencies, field_failures = dependency_list_field(document, label, field)
        dependencies.extend(field_dependencies)
        failures.extend(field_failures)
    patch_dependencies, patch_failures = patch_dependency_values(document, label)
    dependencies.extend(patch_dependencies)
    failures.extend(patch_failures)
    json_patch_dependencies, json_patch_failures = patch_dependency_values(
        document, label, "patchesJson6902"
    )
    dependencies.extend(json_patch_dependencies)
    failures.extend(json_patch_failures)
    openapi = document.get("openapi")
    if isinstance(openapi, dict) and "path" in openapi:
        dependencies.append((openapi["path"], f"{label}.openapi.path", False))
    for generator_field in ("configMapGenerator", "secretGenerator"):
        generator_dependencies, generator_failures = generator_dependency_values(
            document, label, generator_field
        )
        dependencies.extend(generator_dependencies)
        failures.extend(generator_failures)
    return dependencies, failures


def load_local_kustomization(
    directory: Path,
) -> tuple[Path | None, list[KustomizeDependency], list[str]]:
    manifest = kustomization_file(directory)
    if manifest is None or manifest.is_symlink():
        return None, [], [
            f"{directory}: must contain exactly one regular Kustomization file"
        ]
    documents, error = load_yaml_documents(manifest)
    if error is not None or len(documents) != 1 or not isinstance(documents[0], dict):
        return manifest, [], [f"{manifest}: cannot load one Kustomization mapping"]
    document = documents[0]
    if document.get("kind") != "Kustomization":
        return manifest, [], [f"{manifest}: kind must be Kustomization"]
    dependencies, failures = kustomization_dependency_values(document, str(manifest))
    return manifest, dependencies, failures


def add_local_dependency(
    raw_value: object,
    label: str,
    allow_key: bool,
    directory: Path,
    source_root: Path,
    queue: list[Path],
    seen_files: set[Path],
) -> str | None:
    dependency, error = local_kustomize_dependency(
        raw_value,
        directory,
        source_root,
        label,
        allow_key=allow_key,
    )
    if error is not None:
        return error
    if dependency is not None and dependency.is_dir():
        queue.append(dependency)
        return None
    if dependency is not None and dependency.is_file():
        seen_files.add(dependency)
        return None
    return f"{label} must resolve to a regular file or directory"


def dependency_closure_budget_failures(seen_files: set[Path]) -> list[str]:
    failures: list[str] = []
    if len(seen_files) > MAX_YAML_FILES:
        failures.append(f"source dependency closure exceeds {MAX_YAML_FILES} files")
    try:
        total_bytes = sum(path.stat().st_size for path in seen_files)
    except OSError:
        failures.append("source dependency closure metadata cannot be read")
        total_bytes = 0
    if total_bytes > MAX_YAML_TOTAL_BYTES:
        failures.append(
            f"source dependency closure exceeds {MAX_YAML_TOTAL_BYTES} input bytes"
        )
    return failures


def local_git_binary_output(directory: Path, *args: str) -> bytes | None:
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    environment["GIT_TERMINAL_PROMPT"] = "0"
    environment["GIT_CONFIG_NOSYSTEM"] = "1"
    environment["GIT_CONFIG_GLOBAL"] = os.devnull
    environment["GIT_NO_REPLACE_OBJECTS"] = "1"
    environment["GIT_NO_LAZY_FETCH"] = "1"
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(directory),
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.hooksPath=/dev/null",
                *args,
            ],
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode == 0 else None


def committed_tree_entries(
    source_root: Path, source_revision: str, relative_paths: list[str]
) -> dict[str, tuple[str, str]] | None:
    raw = local_git_binary_output(
        source_root,
        "--literal-pathspecs",
        "ls-tree",
        "-r",
        "-z",
        "--full-tree",
        source_revision,
        "--",
        *relative_paths,
    )
    if raw is None:
        return None
    entries: dict[str, tuple[str, str]] = {}
    for record in raw.split(b"\0"):
        if not record:
            continue
        try:
            header, raw_path = record.split(b"\t", 1)
            mode, object_type, object_id = header.decode("ascii").split(" ", 2)
            relative_path = raw_path.decode("utf-8")
        except (UnicodeDecodeError, ValueError):
            return None
        if object_type != "blob" or mode not in {"100644", "100755"}:
            return None
        entries[relative_path] = (mode, object_id)
    return entries


def worktree_blob_oid(path: Path, algorithm: str) -> str | None:
    try:
        content = path.read_bytes()
        digest = hashlib.new(algorithm)
    except (OSError, ValueError):
        return None
    digest.update(f"blob {len(content)}\0".encode("ascii"))
    digest.update(content)
    return digest.hexdigest()


def committed_dependency_failures(
    source_root: Path, source_revision: str, seen_files: set[Path]
) -> list[str]:
    relative_paths = sorted(path.relative_to(source_root).as_posix() for path in seen_files)
    object_format = git_output(source_root, "rev-parse", "--show-object-format")
    entries = committed_tree_entries(source_root, source_revision, relative_paths)
    if object_format not in {"sha1", "sha256"} or entries is None:
        return ["source dependency commit blobs cannot be verified"]
    if set(entries) != set(relative_paths):
        return ["every source dependency must be present in spec.sourceRevision"]
    for path, relative_path in zip(sorted(seen_files), relative_paths, strict=True):
        if worktree_blob_oid(path, object_format) != entries[relative_path][1]:
            return [
                "every source dependency byte must equal its spec.sourceRevision blob"
            ]
    return []


def local_kustomize_closure(
    source_path: Path, source_root: Path, source_revision: str
) -> list[str]:
    queue = [source_path]
    seen_directories: set[Path] = set()
    seen_files: set[Path] = set()
    failures: list[str] = []
    while queue and not failures:
        if len(seen_directories) + len(seen_files) + len(queue) > MAX_DIRECTORY_ENTRIES:
            failures.append(
                f"source dependency closure exceeds {MAX_DIRECTORY_ENTRIES} entries"
            )
            break
        directory = queue.pop()
        if directory in seen_directories:
            continue
        seen_directories.add(directory)
        manifest, dependencies, dependency_failures = load_local_kustomization(directory)
        failures.extend(dependency_failures)
        if manifest is not None:
            seen_files.add(manifest)
        for raw_value, label, allow_key in dependencies:
            dependency_error = add_local_dependency(
                raw_value,
                label,
                allow_key,
                directory,
                source_root,
                queue,
                seen_files,
            )
            if dependency_error is not None:
                failures.append(dependency_error)
    failures.extend(dependency_closure_budget_failures(seen_files))
    if not failures:
        failures.extend(
            committed_dependency_failures(source_root, source_revision, seen_files)
        )
    return failures


def limit_kustomize_output() -> None:
    resource.setrlimit(
        resource.RLIMIT_FSIZE,
        (MAX_YAML_TOTAL_BYTES + 1, MAX_YAML_TOTAL_BYTES + 1),
    )


def verified_child_process_group(process: subprocess.Popen[bytes]) -> int | None:
    """Return the process group only for our live, independent session leader."""
    child_pid = process.pid
    if child_pid <= 0 or child_pid == os.getpid() or process.poll() is not None:
        return None
    try:
        child_pgid = os.getpgid(child_pid)
        child_sid = os.getsid(child_pid)
        caller_pgid = os.getpgrp()
    except OSError:
        return None
    if child_pgid != child_pid or child_sid != child_pid:
        return None
    if child_pgid == caller_pgid:
        return None
    return child_pgid


def terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    try:
        child_pgid = verified_child_process_group(process)
        if child_pgid is None:
            if process.returncode is None:
                process.kill()
            return
        try:
            # Both callers create this child with start_new_session=True. The
            # verifier proves that the still-unreaped child remains its own
            # session/group leader and excludes our process group. Killing the
            # group is required to prevent timed-out descendants from escaping.
            os.killpg(child_pgid, signal.SIGKILL)  # NOSONAR
        except ProcessLookupError:
            pass
    finally:
        process.wait()


def limit_file_output(max_bytes: int) -> None:
    resource.setrlimit(resource.RLIMIT_FSIZE, (max_bytes + 1, max_bytes + 1))


def bounded_process_output(
    command: list[str],
    environment: dict[str, str],
    *,
    timeout: int,
    max_bytes: int,
) -> tuple[int, bytes] | None:
    with tempfile.TemporaryFile() as output:
        try:
            process = subprocess.Popen(
                command,
                env=environment,
                stdout=output,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                preexec_fn=partial(limit_file_output, max_bytes),
            )
            try:
                return_code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                terminate_process_group(process)
                return None
        except (OSError, subprocess.SubprocessError):
            return None
        try:
            output_size = os.fstat(output.fileno()).st_size
        except OSError:
            return None
        if output_size > max_bytes:
            return None
        try:
            output.seek(0)
            stdout = output.read(max_bytes + 1)
        except OSError:
            return None
        return return_code, stdout


def run_source_kustomize(source_path: Path) -> tuple[list[object], str | None]:
    kubectl = shutil.which("kubectl")
    if kubectl is None:
        return [], "kubectl dependency is unavailable"
    with tempfile.TemporaryDirectory(prefix="adoption-kustomize-home-") as home:
        environment = {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_NO_LAZY_FETCH": "1",
            "HOME": home,
            "KUBECONFIG": os.devnull,
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PATH": "",
        }
        with tempfile.TemporaryFile() as output:
            try:
                process = subprocess.Popen(
                    [
                        kubectl,
                        "kustomize",
                        str(source_path),
                        "--load-restrictor=LoadRestrictionsRootOnly",
                    ],
                    env=environment,
                    stdout=output,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                    preexec_fn=limit_kustomize_output,
                )
                try:
                    return_code = process.wait(timeout=60)
                except subprocess.TimeoutExpired:
                    terminate_process_group(process)
                    return [], "kubectl kustomize dependency timed out"
            except OSError:
                return [], "kubectl kustomize dependency failed"
            output_size = os.fstat(output.fileno()).st_size
            if output_size > MAX_YAML_TOTAL_BYTES:
                return [], f"kubectl kustomize output exceeds {MAX_YAML_TOTAL_BYTES} bytes"
            if return_code != 0:
                return [], f"kubectl kustomize failed with exit {return_code}"
            output.seek(0)
            try:
                rendered_text = output.read().decode("utf-8")
            except UnicodeDecodeError:
                return [], "kubectl kustomize returned non-UTF-8 output"
    return load_yaml_text(rendered_text)


def bounded_global_credential_helpers() -> tuple[str, ...]:
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    environment["GIT_TERMINAL_PROMPT"] = "0"
    environment["GIT_CONFIG_NOSYSTEM"] = "1"
    result = bounded_process_output(
        ["git", "config", "--global", "--get-all", "credential.helper"],
        environment,
        timeout=5,
        max_bytes=MAX_CREDENTIAL_HELPER_OUTPUT_BYTES,
    )
    if result is None:
        return ()
    return_code, raw_output = result
    if return_code not in {0, 1}:
        return ()
    try:
        lines = raw_output.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        return ()
    if len(lines) > MAX_CREDENTIAL_HELPER_LINES:
        return ()
    helpers: list[str] = []
    for value in (line.strip() for line in lines):
        if value not in SAFE_CREDENTIAL_HELPERS or value in helpers:
            continue
        helpers.append(value)
        if len(helpers) > MAX_CREDENTIAL_HELPERS:
            return ()
    return tuple(helpers)


def remote_git_binary_output(*args: str, timeout: int) -> bytes | None:
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    environment["GIT_TERMINAL_PROMPT"] = "0"
    environment["GIT_CONFIG_NOSYSTEM"] = "1"
    environment["GIT_CONFIG_GLOBAL"] = os.devnull
    command = ["git"]
    for helper in bounded_global_credential_helpers():
        command.extend(["-c", f"credential.helper={helper}"])
    command.extend(args)
    result = bounded_process_output(
        command,
        environment,
        timeout=timeout,
        max_bytes=MAX_REMOTE_GIT_OUTPUT_BYTES,
    )
    if result is None:
        return None
    return_code, stdout = result
    return stdout if return_code == 0 else None


def fresh_remote_branch_tip(
    repository: str,
    revision: str,
    remote_hosts: set[str],
    failures: list[str],
) -> tuple[str | None, bool]:
    canonical_repository = safe_git_repository(repository)
    remote_host = canonical_repository[1] if canonical_repository is not None else ""
    if not remote_hosts:
        failures.append(
            "--verify-remote-host is required before the fresh target-branch proof"
        )
        return None, True
    if remote_host not in remote_hosts:
        failures.append(
            f"target Git host {remote_host!r} is not approved by --verify-remote-host"
        )
        return None, True
    raw = remote_git_binary_output(
        "ls-remote",
        "--heads",
        "--exit-code",
        repository,
        f"refs/heads/{revision}",
        timeout=90,
    )
    if raw is None:
        failures.append("fresh target-branch proof is unavailable")
        return None, True
    try:
        rows = raw.decode("ascii").splitlines()
    except UnicodeDecodeError:
        failures.append("fresh target-branch proof returned invalid output")
        return None, True
    expected_ref = f"refs/heads/{revision}"
    parsed = [row.split("\t", 1) for row in rows]
    if (
        len(parsed) != 1
        or len(parsed[0]) != 2
        or not SHA.fullmatch(parsed[0][0])
        or parsed[0][1] != expected_ref
    ):
        failures.append("fresh target-branch proof returned an unexpected exact ref")
        return None, True
    return parsed[0][0], False


def source_checkout(
    source_root_value: Path,
    spec: dict[str, Any],
    target: dict[str, str],
    phase: str,
    render_root: Path,
    remote_hosts: set[str],
    failures: list[str],
) -> tuple[Path | None, bool]:
    source_revision = spec.get("sourceRevision")
    if not isinstance(source_revision, str) or not SHA.fullmatch(source_revision):
        failures.append("spec.sourceRevision must be one lowercase full 40-hex Git SHA")
        return None, False
    if source_root_value.is_symlink():
        failures.append("--source-root must not be a symbolic link")
        return None, True
    try:
        source_root = source_root_value.resolve(strict=True)
        render_resolved = render_root.resolve(strict=True)
    except OSError:
        failures.append("--source-root and --render must resolve to existing paths")
        return None, True
    git_root = git_output(source_root, "rev-parse", "--show-toplevel")
    if git_root is None or Path(git_root).resolve() != source_root:
        failures.append("--source-root must be the exact Git checkout root")
        return None, True
    if source_root == render_resolved or source_root in render_resolved.parents:
        failures.append("--render must remain outside the source checkout")
    origin = git_output(source_root, "config", "--local", "--get", "remote.origin.url")
    repository = target.get("repository", "")
    if (
        origin is None
        or repository_url_has_embedded_credentials(origin)
        or not same_git_repository(origin, repository)
    ):
        failures.append("source checkout origin differs from spec.target.repository")
    head = git_output(source_root, "rev-parse", "--verify", "HEAD^{commit}")
    revision = git_output(
        source_root, "rev-parse", "--verify", f"{source_revision}^{{commit}}"
    )
    if revision != source_revision or head != source_revision:
        failures.append("source checkout HEAD must equal spec.sourceRevision")
    status = git_output(source_root, "status", "--porcelain", "--untracked-files=all")
    if status is None:
        failures.append("source checkout status cannot be verified")
        return None, True
    if status:
        failures.append("source checkout must be clean, including untracked files")
    if phase != "pre-normalization":
        target_commit, remote_unavailable = fresh_remote_branch_tip(
            repository,
            target.get("revision", ""),
            remote_hosts,
            failures,
        )
        if remote_unavailable:
            return None, True
        if target_commit != source_revision:
            failures.append(
                "post-normalization/passive sourceRevision must equal the fresh remote "
                "target-branch tip"
            )
    target_path = target.get("path", "")
    if canonical_relative_git_path(target_path) != target_path:
        failures.append("spec.target.path must be a canonical repository-relative path")
        return None, False
    if path_has_symlink(source_root, target_path):
        failures.append("spec.target.path must not traverse a symbolic link")
        return None, False
    try:
        source_path = (source_root / target_path).resolve(strict=True)
    except OSError:
        failures.append("spec.target.path does not exist in the source checkout")
        return None, False
    if source_root not in source_path.parents or not source_path.is_dir():
        failures.append("spec.target.path must be a directory inside the source checkout")
        return None, False
    return source_path, False


def validate_source_render(
    args: argparse.Namespace,
    spec: dict[str, Any],
    target: dict[str, str],
    namespace: str,
    render: dict[ResourceRef, dict[str, Any]],
    failures: list[str],
) -> bool:
    remote_hosts = approved_remote_hosts(args.verify_remote_host)
    if remote_hosts is None:
        failures.append("--verify-remote-host values must be literal DNS hostnames")
        return True
    source_path, unavailable = source_checkout(
        args.source_root,
        spec,
        target,
        args.phase,
        args.render,
        remote_hosts,
        failures,
    )
    if source_path is None:
        return unavailable
    source_root = args.source_root.resolve(strict=True)
    closure_failures = local_kustomize_closure(
        source_path,
        source_root,
        str(spec.get("sourceRevision") or ""),
    )
    if closure_failures:
        failures.extend(closure_failures)
        return False
    documents, render_error = run_source_kustomize(source_path)
    if render_error is not None:
        failures.append(f"source render unavailable: {render_error}")
        return True
    source_render, source_failures = resources_from_documents(
        documents, namespace, "source render"
    )
    failures.extend(source_failures)
    if set(source_render) != set(render):
        missing = sorted(ref.label() for ref in set(render) - set(source_render))
        extra = sorted(ref.label() for ref in set(source_render) - set(render))
        failures.append(
            f"source render inventory differs from supplied render; missing={missing}, extra={extra}"
        )
        return False
    for ref, document in source_render.items():
        if document != render[ref]:
            failures.append(f"{ref.label()}: source render differs from supplied render")
    return False


def validate_target(spec: dict[str, Any], failures: list[str]) -> dict[str, str]:
    required = {
        "clusterContext",
        "namespace",
        "application",
        "project",
        "repository",
        "revision",
        "path",
    }
    target = validate_exact_fields(spec.get("target"), required, "spec.target", failures)
    result: dict[str, str] = {}
    for key in sorted(required):
        value = target.get(key)
        if not isinstance(value, str) or not value.strip():
            failures.append(f"spec.target.{key} must be a non-empty string")
        else:
            result[key] = value.strip()
    repository = result.get("repository")
    if repository is not None and (
        repository_url_has_embedded_credentials(repository)
        or safe_git_repository(repository) is None
    ):
        failures.append(
            "spec.target.repository must be a credential-free HTTPS or SSH Git URL"
        )
    revision = result.get("revision")
    if revision is not None and not GIT_BRANCH.fullmatch(revision):
        failures.append("spec.target.revision must be a literal safe Git branch name")
    return result


def validate_included_inventory_entry(
    item: dict[str, Any], ref: ResourceRef, field: str, failures: list[str]
) -> None:
    if ref.kind not in SUPPORTED_KINDS:
        failures.append(f"{field}: {ref.kind} is not a supported stateless adoption kind")
    elif ref.api_version not in SUPPORTED_KINDS[ref.kind]:
        failures.append(
            f"{field}: {ref.kind} must use one of {sorted(SUPPORTED_KINDS[ref.kind])}"
        )
    immutable = item.get("immutableFields")
    if not isinstance(immutable, dict):
        failures.append(f"{field}.immutableFields must be a mapping")
    legacy_writer = item.get("legacyWriter")
    if legacy_writer is not None and (
        not isinstance(legacy_writer, str) or not legacy_writer.strip()
    ):
        failures.append(f"{field}.legacyWriter must be null or a non-empty writer name")
    if ref.kind == "Deployment":
        if not isinstance(legacy_writer, str) or not legacy_writer.strip():
            failures.append(f"{field}: Deployment must declare its legacyWriter")
        if item.get("grandfatheredExistingDeployment") is not True:
            failures.append(
                f"{field}: Deployment requires grandfatheredExistingDeployment: true"
            )
        if not isinstance(immutable, dict) or "spec.selector" not in immutable:
            failures.append(f"{field}: Deployment must freeze spec.selector")
    elif "grandfatheredExistingDeployment" in item:
        failures.append(
            f"{field}: grandfatheredExistingDeployment is valid only for Deployment"
        )
    if ref.kind == "Service" and (
        not isinstance(immutable, dict) or "spec.clusterIP" not in immutable
    ):
        failures.append(f"{field}: Service must freeze spec.clusterIP")


def validate_excluded_inventory_entry(
    item: dict[str, Any], field: str, failures: list[str]
) -> None:
    reason = item.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        failures.append(f"{field}.reason must be non-empty")


def inventory_entry(
    raw: object,
    section: str,
    index: int,
    target_namespace: str,
    failures: list[str],
) -> tuple[ResourceRef | None, dict[str, Any]]:
    field = f"spec.inventory.{section}[{index}]"
    expected_fields = INCLUDED_FIELDS if section == "included" else EXCLUDED_FIELDS
    optional_fields = (
        {"grandfatheredExistingDeployment"} if section == "included" else set()
    )
    item = validate_exact_fields(
        raw,
        expected_fields,
        field,
        failures,
        optional=optional_fields,
    )
    ref = ref_from(item, default_namespace=target_namespace)
    if ref is None:
        failures.append(f"{field} has an incomplete resource identity")
        return None, item
    if ref.namespace != target_namespace:
        failures.append(f"{field} must remain in target namespace {target_namespace!r}")
    uid = item.get("uid")
    if not isinstance(uid, str) or not uid:
        failures.append(f"{field}.uid must be the non-empty live UID")
    if section == "included":
        validate_included_inventory_entry(item, ref, field, failures)
    else:
        validate_excluded_inventory_entry(item, field, failures)
    return ref, item


def validate_inventory(
    spec: dict[str, Any], target_namespace: str, failures: list[str]
) -> tuple[dict[ResourceRef, dict[str, Any]], dict[ResourceRef, dict[str, Any]]]:
    inventory = validate_exact_fields(
        spec.get("inventory"), INVENTORY_FIELDS, "spec.inventory", failures
    )
    included_raw = inventory.get("included")
    excluded_raw = inventory.get("excluded")
    if not isinstance(included_raw, list) or not included_raw:
        failures.append("spec.inventory.included must be a non-empty list")
        included_raw = []
    if not isinstance(excluded_raw, list):
        failures.append("spec.inventory.excluded must be a closed list (empty is explicit)")
        excluded_raw = []
    included: dict[ResourceRef, dict[str, Any]] = {}
    excluded: dict[ResourceRef, dict[str, Any]] = {}
    seen_uids: dict[str, ResourceRef] = {}
    for section, values, destination in (
        ("included", included_raw, included),
        ("excluded", excluded_raw, excluded),
    ):
        for index, raw in enumerate(values):
            ref, item = inventory_entry(
                raw, section, index, target_namespace, failures
            )
            if ref is None:
                continue
            if ref in included or ref in excluded:
                failures.append(f"inventory classifies {ref.label()} more than once")
                continue
            uid = item.get("uid")
            if isinstance(uid, str) and uid:
                previous = seen_uids.get(uid)
                if previous is not None:
                    failures.append(
                        f"inventory UID {uid!r} is shared by {previous.label()} and {ref.label()}"
                    )
                else:
                    seen_uids[uid] = ref
            destination[ref] = item
    if not any(ref.kind == "Deployment" for ref in included):
        failures.append("included inventory must contain an existing Deployment")
    return included, excluded


def image_repository_and_tag(image: str) -> tuple[str, str] | None:
    if "@" in image:
        return None
    slash = image.rfind("/")
    colon = image.rfind(":")
    if colon <= slash or colon == len(image) - 1:
        return None
    repository = image[:colon]
    tag = image[colon + 1 :]
    if not IMAGE_REPOSITORY.fullmatch(repository) or not IMAGE_TAG.fullmatch(tag):
        return None
    return repository, tag


def pinned_source_image_and_tag(image: str) -> tuple[str, str] | None:
    parsed = image_repository_and_tag(image)
    if parsed is not None:
        return parsed
    slash = image.rfind("/")
    colon = image.rfind(":")
    if colon <= slash or colon == len(image) - 1:
        return None
    repository = image[:colon]
    tag = image[colon + 1 :]
    if not IMAGE_NAME.fullmatch(repository) or not IMAGE_TAG.fullmatch(tag):
        return None
    return repository, tag


def immutable_image_reference(image: object) -> bool:
    if not isinstance(image, str) or not image:
        return False
    if "@" in image:
        repository, digest = image.rsplit("@", 1)
        return bool(
            IMAGE_REPOSITORY.fullmatch(repository)
            and DIGEST.fullmatch(digest)
        )
    parsed = image_repository_and_tag(image)
    return parsed is not None and SHA.fullmatch(parsed[1]) is not None


def passive_revision(spec: dict[str, Any], failures: list[str]) -> str:
    value = spec.get("passiveRevision")
    if not isinstance(value, str) or not SHA.fullmatch(value):
        failures.append("spec.passiveRevision must be one lowercase full 40-hex Git SHA")
        return ""
    return value


def provenance_identity(
    item: dict[str, Any],
    field: str,
    included: dict[ResourceRef, dict[str, Any]],
    existing: dict[tuple[ResourceRef, str], dict[str, Any]],
    failures: list[str],
) -> tuple[ResourceRef, str] | None:
    resource = validate_exact_fields(
        item.get("resource"),
        RESOURCE_REF_FIELDS,
        f"{field}.resource",
        failures,
    )
    ref = ref_from(resource)
    container = item.get("container")
    if ref is None or ref not in included or ref.kind != "Deployment":
        failures.append(f"{field}.resource must reference one included Deployment")
        return None
    if not isinstance(container, str) or not CONTAINER_NAME.fullmatch(container):
        failures.append(f"{field}.container must be a literal Kubernetes container name")
        return None
    key = (ref, container)
    if key in existing:
        failures.append(
            f"{field} duplicates provenance for {ref.label()} container {container}"
        )
        return None
    return key


def validate_provenance_alias(
    item: dict[str, Any], field: str, aliases: set[str], failures: list[str]
) -> bool:
    alias = item.get("alias")
    if not isinstance(alias, str) or not IMAGE_ALIAS.fullmatch(alias) or alias in aliases:
        failures.append(f"{field}.alias must be a unique literal Image Updater alias")
        return False
    aliases.add(alias)
    return True


def validate_provenance_strings(
    item: dict[str, Any], field: str, failures: list[str]
) -> bool:
    valid = True
    for name in ("sourceImage", "normalizedImage", "evidence", "kustomizeImageName"):
        if not isinstance(item.get(name), str) or not item[name].strip():
            failures.append(f"{field}.{name} must be a non-empty string")
            valid = False
    return valid


def validate_provenance_images(
    item: dict[str, Any], field: str, passive: str, failures: list[str]
) -> bool:
    valid = True
    source = image_repository_and_tag(str(item.get("sourceImage") or ""))
    normalized = image_repository_and_tag(str(item.get("normalizedImage") or ""))
    if source is None or source[1].lower() in MUTABLE_TAGS:
        failures.append(
            f"{field}.sourceImage must be a credential-free literal image with a non-floating tag"
        )
        valid = False
    if normalized is None or normalized[1] != passive:
        failures.append(
            f"{field}.normalizedImage must be a credential-free literal image tagged with passiveRevision"
        )
        valid = False
    if item.get("commit") != passive:
        failures.append(f"{field}.commit must equal passiveRevision")
        valid = False
    if not isinstance(item.get("digest"), str) or not DIGEST.fullmatch(item["digest"]):
        failures.append(f"{field}.digest must be sha256:<64 lowercase hex>")
        valid = False
    return valid


def validate_kustomize_image_name(
    item: dict[str, Any],
    field: str,
    names: set[str],
    failures: list[str],
) -> bool:
    name = item.get("kustomizeImageName")
    if not isinstance(name, str) or not IMAGE_NAME.fullmatch(name) or name in names:
        failures.append(f"{field}.kustomizeImageName must be a unique literal image name")
        return False
    names.add(name)
    return True


def validate_provenance_platforms(
    item: dict[str, Any], field: str, failures: list[str]
) -> bool:
    platforms = item.get("platforms")
    if (
        not isinstance(platforms, list)
        or not platforms
        or any(not isinstance(value, str) or not value for value in platforms)
        or len(set(platforms)) != len(platforms)
    ):
        failures.append(f"{field}.platforms must be a non-empty unique string list")
        return False
    if not set(platforms) <= PLATFORMS:
        failures.append(
            f"{field}.platforms contains unsupported values; allowed={sorted(PLATFORMS)}"
        )
        return False
    return True


def provenance_entries(
    spec: dict[str, Any], included: dict[ResourceRef, dict[str, Any]], failures: list[str]
) -> dict[tuple[ResourceRef, str], dict[str, Any]]:
    passive = passive_revision(spec, failures)
    raw_entries = spec.get("imageProvenance")
    if not isinstance(raw_entries, list) or not raw_entries:
        failures.append("spec.imageProvenance must be a non-empty list")
        raw_entries = []
    result: dict[tuple[ResourceRef, str], dict[str, Any]] = {}
    aliases: set[str] = set()
    kustomize_names: set[str] = set()
    for index, raw in enumerate(raw_entries):
        field = f"spec.imageProvenance[{index}]"
        item = validate_exact_fields(raw, PROVENANCE_FIELDS, field, failures)
        key = provenance_identity(item, field, included, result, failures)
        if key is None:
            continue
        valid = validate_provenance_alias(item, field, aliases, failures)
        valid = validate_provenance_strings(item, field, failures) and valid
        valid = validate_provenance_images(item, field, passive, failures) and valid
        valid = (
            validate_kustomize_image_name(item, field, kustomize_names, failures)
            and valid
        )
        valid = validate_provenance_platforms(item, field, failures) and valid
        if valid:
            result[key] = item
    for ref in included:
        if ref.kind == "Deployment" and not any(key[0] == ref for key in result):
            failures.append(f"{ref.label()} has no image provenance entry")
    return result


def validate_pinned_normalization_images(
    item: dict[str, Any], field: str, failures: list[str]
) -> bool:
    valid = True
    source = pinned_source_image_and_tag(str(item.get("sourceImage") or ""))
    if source is None or source[1].lower() in MUTABLE_TAGS:
        failures.append(
            f"{field}.sourceImage must be a credential-free literal image with a non-mutable tag"
        )
        valid = False
    normalized = str(item.get("normalizedImage") or "")
    repository, separator, digest = normalized.rpartition("@")
    if (
        separator != "@"
        or not IMAGE_REPOSITORY.fullmatch(repository)
        or not DIGEST.fullmatch(digest)
    ):
        failures.append(
            f"{field}.normalizedImage must be a fully qualified image pinned by sha256 digest"
        )
        valid = False
    if item.get("digest") != digest:
        failures.append(f"{field}.digest must equal normalizedImage digest")
        valid = False
    return valid


def pinned_normalization_entries(
    spec: dict[str, Any],
    included: dict[ResourceRef, dict[str, Any]],
    provenance: dict[tuple[ResourceRef, str], dict[str, Any]],
    failures: list[str],
) -> dict[tuple[ResourceRef, str], dict[str, Any]]:
    raw_entries = spec.get("pinnedImageNormalizations", [])
    if not isinstance(raw_entries, list):
        failures.append("spec.pinnedImageNormalizations must be a list")
        return {}
    occupied = dict(provenance)
    result: dict[tuple[ResourceRef, str], dict[str, Any]] = {}
    for index, raw in enumerate(raw_entries):
        field = f"spec.pinnedImageNormalizations[{index}]"
        item = validate_exact_fields(
            raw, PINNED_NORMALIZATION_FIELDS, field, failures
        )
        key = provenance_identity(item, field, included, occupied, failures)
        if key is None:
            continue
        valid = validate_pinned_normalization_images(item, field, failures)
        if not isinstance(item.get("evidence"), str) or not item["evidence"].strip():
            failures.append(f"{field}.evidence must be a non-empty string")
            valid = False
        valid = validate_provenance_platforms(item, field, failures) and valid
        if valid:
            result[key] = item
            occupied[key] = item
    return result


def validate_writer_fields(
    item: dict[str, Any], field: str, names: set[str], failures: list[str]
) -> None:
    name = item.get("name")
    if not isinstance(name, str) or not name or name in names:
        failures.append(f"{field}.name must be unique and non-empty")
    else:
        names.add(name)
    if item.get("state") != "enabled-idle":
        failures.append(f"{field}.state must remain enabled-idle before retirement")
    if item.get("retirementAfter") != "automatic-promotion-accepted":
        failures.append(f"{field}.retirementAfter must be automatic-promotion-accepted")
    for key in ("type", "disableMethod", "verification"):
        if not isinstance(item.get(key), str) or not item[key]:
            failures.append(f"{field}.{key} must be a non-empty string")


def validate_writer_ref(
    raw_ref: object,
    write_field: str,
    included: dict[ResourceRef, dict[str, Any]],
    writer_refs: set[ResourceRef],
    failures: list[str],
) -> ResourceRef | None:
    ref_data = validate_exact_fields(
        raw_ref,
        RESOURCE_REF_FIELDS,
        write_field,
        failures,
    )
    ref = ref_from(ref_data)
    if ref is None:
        failures.append(f"{write_field} has an incomplete resource identity")
        return None
    if ref in writer_refs:
        failures.append(f"{write_field} duplicates {ref.label()}")
        return None
    writer_refs.add(ref)
    if ref not in included:
        failures.append(f"{write_field} must reference one included resource")
        return None
    return ref


def validate_writer_write_set(
    item: dict[str, Any],
    field: str,
    included: dict[ResourceRef, dict[str, Any]],
    failures: list[str],
) -> set[ResourceRef]:
    write_set = item.get("writeSet")
    if not isinstance(write_set, list) or not write_set:
        failures.append(f"{field}.writeSet must be a non-empty resource list")
        return set()
    writer_refs: set[ResourceRef] = set()
    covered: set[ResourceRef] = set()
    for write_index, raw_ref in enumerate(write_set):
        ref = validate_writer_ref(
            raw_ref,
            f"{field}.writeSet[{write_index}]",
            included,
            writer_refs,
            failures,
        )
        if ref is not None:
            covered.add(ref)
    return covered


def validate_governance(
    spec: dict[str, Any],
    included: dict[ResourceRef, dict[str, Any]],
    failures: list[str],
) -> None:
    if spec.get("approvalStages") != APPROVAL_STAGES:
        failures.append(
            "spec.approvalStages must list the eight fresh approvals in exact workflow order"
        )
    writers = spec.get("legacyWriters")
    if not isinstance(writers, list) or not writers:
        failures.append("spec.legacyWriters must be a non-empty list")
        return
    names: set[str] = set()
    writer_by_resource: dict[ResourceRef, str] = {}
    for index, raw in enumerate(writers):
        field = f"spec.legacyWriters[{index}]"
        item = validate_exact_fields(raw, LEGACY_WRITER_FIELDS, field, failures)
        validate_writer_fields(item, field, names, failures)
        writer_coverage = validate_writer_write_set(item, field, included, failures)
        writer_name = item.get("name")
        writer_label = writer_name if isinstance(writer_name, str) and writer_name else field
        for ref in writer_coverage:
            previous = writer_by_resource.get(ref)
            if previous is not None:
                failures.append(
                    f"{field}.writeSet overlaps {previous} for {ref.label()}"
                )
            else:
                writer_by_resource[ref] = writer_label
    for ref, contract_entry in included.items():
        declared_writer = contract_entry.get("legacyWriter")
        actual_writer = writer_by_resource.get(ref)
        if declared_writer != actual_writer:
            failures.append(
                f"{ref.label()}: declared legacyWriter {declared_writer!r} "
                f"differs from writeSet owner {actual_writer!r}"
            )


def path_value(doc: object, path: str) -> tuple[bool, object]:
    current = doc
    for segment in path.split("."):
        if not isinstance(current, dict) or segment not in current:
            return False, None
        current = current[segment]
    return True, current


def tracking_or_owner_failures(
    ref: ResourceRef,
    doc: dict[str, Any],
    application_name: str,
    source: str,
) -> list[str]:
    failures: list[str] = []
    metadata = mapping(doc.get("metadata"))
    annotations = mapping(metadata.get("annotations"))
    labels = mapping(metadata.get("labels"))
    owner_references = metadata.get("ownerReferences") or []
    if owner_references:
        failures.append(
            f"{ref.label()}: {source} object has ownerReferences and is not an unmanaged adoption candidate"
        )
    if any(
        key in ARGO_TRACKING_ANNOTATIONS
        or str(key).startswith("argocd.argoproj.io/tracking")
        for key in annotations
    ):
        failures.append(f"{ref.label()}: {source} object already has Argo CD tracking")
    if any(key in HELM_RELEASE_ANNOTATIONS for key in annotations):
        failures.append(f"{ref.label()}: {source} object already has Helm release identity")
    if any(key in ARGO_TRACKING_LABELS for key in labels) or labels.get(
        "app.kubernetes.io/instance"
    ) == application_name:
        failures.append(f"{ref.label()}: {source} object already has Argo CD instance identity")
    managed_by = str(labels.get("app.kubernetes.io/managed-by") or "").lower()
    if managed_by in {"argocd", "helm"}:
        failures.append(
            f"{ref.label()}: {source} object is already managed by {managed_by}"
        )
    managed_fields = metadata.get("managedFields") or []
    if isinstance(managed_fields, list):
        managers = {
            str(item.get("manager") or "").lower()
            for item in managed_fields
            if isinstance(item, dict)
        }
        if any("argocd" in manager or manager == "helm" for manager in managers):
            failures.append(
                f"{ref.label()}: {source} managedFields show an existing Argo CD/Helm writer"
            )
    return failures


def validate_snapshot_inventory(
    included: dict[ResourceRef, dict[str, Any]],
    excluded: dict[ResourceRef, dict[str, Any]],
    live: dict[ResourceRef, dict[str, Any]],
    render: dict[ResourceRef, dict[str, Any]],
    failures: list[str],
) -> None:
    expected_live = set(included) | set(excluded)
    if set(live) != expected_live:
        missing = sorted(ref.label() for ref in expected_live - set(live))
        unknown = sorted(ref.label() for ref in set(live) - expected_live)
        failures.append(f"live snapshot is not closed; missing={missing}, unclassified={unknown}")
    if set(render) != set(included):
        missing = sorted(ref.label() for ref in set(included) - set(render))
        extra = sorted(ref.label() for ref in set(render) - set(included))
        failures.append(
            f"render inventory differs from included cohort; missing={missing}, extra={extra}"
        )


def validate_live_snapshot_metadata(
    included: dict[ResourceRef, dict[str, Any]],
    excluded: dict[ResourceRef, dict[str, Any]],
    live: dict[ResourceRef, dict[str, Any]],
    failures: list[str],
) -> None:
    for ref, contract_entry in {**included, **excluded}.items():
        doc = live.get(ref)
        if doc is None:
            continue
        metadata = mapping(doc.get("metadata"))
        if metadata.get("uid") != contract_entry.get("uid"):
            failures.append(f"{ref.label()}: live UID differs from the adoption contract")
        if ref.kind != "Secret":
            continue
        unexpected_fields = sorted(set(doc) - SECRET_SNAPSHOT_FIELDS)
        unexpected_metadata = sorted(set(metadata) - SECRET_SNAPSHOT_METADATA_FIELDS)
        if unexpected_fields or unexpected_metadata:
            failures.append(
                f"{ref.label()}: live Secret snapshot must contain identity fields only; "
                f"top-level={unexpected_fields}, metadata={unexpected_metadata}"
            )


def validate_service_immutable_evidence(
    ref: ResourceRef,
    live_doc: dict[str, Any],
    immutable_fields: dict[str, Any],
    failures: list[str],
) -> None:
    if ref.kind != "Service":
        return
    for field in sorted(SERVER_ALLOCATED_SERVICE_FIELDS):
        live_found, live_value = path_value(live_doc, field)
        if live_found and immutable_fields.get(field) != live_value:
            failures.append(f"{ref.label()}: contract must freeze live Service field {field}")


def validate_immutable_evidence(
    ref: ResourceRef,
    live_doc: dict[str, Any],
    render_doc: dict[str, Any],
    immutable_fields: dict[str, Any],
    failures: list[str],
) -> None:
    validate_service_immutable_evidence(ref, live_doc, immutable_fields, failures)
    for field, expected in immutable_fields.items():
        if not isinstance(field, str) or not field:
            failures.append(f"{ref.label()}: immutable field names must be non-empty strings")
            continue
        live_found, live_value = path_value(live_doc, field)
        render_found, render_value = path_value(render_doc, field)
        if not live_found or live_value != expected:
            failures.append(f"{ref.label()}: live immutable field {field} differs from contract")
        if render_found and render_value != expected:
            failures.append(f"{ref.label()}: render immutable field {field} differs from contract")
        if not render_found and field not in SERVER_ALLOCATED_SERVICE_FIELDS:
            failures.append(f"{ref.label()}: render omits immutable field {field}")


def validate_included_live_identity(
    included: dict[ResourceRef, dict[str, Any]],
    live: dict[ResourceRef, dict[str, Any]],
    render: dict[ResourceRef, dict[str, Any]],
    application_name: str,
    failures: list[str],
) -> None:
    for ref, contract_entry in included.items():
        live_doc = live.get(ref)
        render_doc = render.get(ref)
        if live_doc is None or render_doc is None:
            continue
        failures.extend(
            tracking_or_owner_failures(ref, live_doc, application_name, "live")
        )
        failures.extend(
            tracking_or_owner_failures(ref, render_doc, application_name, "rendered")
        )
        render_metadata = mapping(render_doc.get("metadata"))
        if "uid" in render_metadata:
            failures.append(f"{ref.label()}: render must not copy metadata.uid")
        validate_immutable_evidence(
            ref,
            live_doc,
            render_doc,
            mapping(contract_entry.get("immutableFields")),
            failures,
        )


def validate_live_identity(
    included: dict[ResourceRef, dict[str, Any]],
    excluded: dict[ResourceRef, dict[str, Any]],
    live: dict[ResourceRef, dict[str, Any]],
    render: dict[ResourceRef, dict[str, Any]],
    application_name: str,
    failures: list[str],
) -> None:
    validate_snapshot_inventory(included, excluded, live, render, failures)
    validate_live_snapshot_metadata(included, excluded, live, failures)
    validate_included_live_identity(
        included, live, render, application_name, failures
    )


def remove_if(mapping_value: dict[str, Any], key: str, expected: object) -> None:
    if mapping_value.get(key) == expected:
        mapping_value.pop(key, None)


def normalize_probe(probe: object) -> None:
    if not isinstance(probe, dict):
        return
    remove_if(probe, "failureThreshold", 3)
    remove_if(probe, "successThreshold", 1)


def normalize_container(container: dict[str, Any]) -> None:
    remove_if(container, "imagePullPolicy", "IfNotPresent")
    remove_if(container, "terminationMessagePath", "/dev/termination-log")
    remove_if(container, "terminationMessagePolicy", "File")
    remove_if(container, "resources", {})
    remove_if(container, "securityContext", {})
    for field in ("livenessProbe", "readinessProbe", "startupProbe"):
        normalize_probe(container.get(field))
    ports = container.get("ports") or []
    if not isinstance(ports, list):
        return
    for port in ports:
        if isinstance(port, dict):
            remove_if(port, "protocol", "TCP")


def canonical_metadata(
    value: dict[str, Any], namespace: str, *, live_snapshot: bool
) -> None:
    metadata = mapping(value.get("metadata"))
    for key in ("creationTimestamp", "generation", "managedFields", "resourceVersion", "uid"):
        metadata.pop(key, None)
    metadata.setdefault("namespace", namespace)
    labels = mapping(metadata.get("labels"))
    if live_snapshot:
        for key in LIVE_ONLY_LABELS:
            labels.pop(key, None)
        for key in ARGO_TRACKING_LABELS:
            labels.pop(key, None)
    if labels:
        metadata["labels"] = labels
    else:
        metadata.pop("labels", None)
    annotations = mapping(metadata.get("annotations"))
    if live_snapshot:
        for key in LIVE_ONLY_ANNOTATIONS:
            annotations.pop(key, None)
    if annotations:
        metadata["annotations"] = annotations
    else:
        metadata.pop("annotations", None)
    value["metadata"] = metadata


def canonical_deployment_spec(spec: dict[str, Any]) -> None:
    remove_if(spec, "replicas", 1)
    remove_if(spec, "revisionHistoryLimit", 10)
    remove_if(spec, "progressDeadlineSeconds", 600)
    strategy = mapping(spec.get("strategy"))
    rolling = mapping(strategy.get("rollingUpdate"))
    remove_if(rolling, "maxSurge", "25%")
    remove_if(rolling, "maxUnavailable", "25%")
    if rolling:
        strategy["rollingUpdate"] = rolling
    else:
        strategy.pop("rollingUpdate", None)
    remove_if(strategy, "type", "RollingUpdate")
    if strategy:
        spec["strategy"] = strategy
    else:
        spec.pop("strategy", None)
    template = mapping(spec.get("template"))
    template_metadata = mapping(template.get("metadata"))
    remove_if(template_metadata, "creationTimestamp", None)
    if template_metadata:
        template["metadata"] = template_metadata
    else:
        template.pop("metadata", None)
    pod_spec = mapping(template.get("spec"))
    for key, expected in (
        ("dnsPolicy", "ClusterFirst"),
        ("enableServiceLinks", True),
        ("restartPolicy", "Always"),
        ("schedulerName", "default-scheduler"),
        ("terminationGracePeriodSeconds", 30),
        ("serviceAccount", "default"),
        ("serviceAccountName", "default"),
        ("securityContext", {}),
    ):
        remove_if(pod_spec, key, expected)
    for field in ("containers", "initContainers"):
        containers = pod_spec.get(field) or []
        if not isinstance(containers, list):
            continue
        for container in containers:
            if isinstance(container, dict):
                normalize_container(container)
    template["spec"] = pod_spec
    spec["template"] = template


def canonical_service_spec(spec: dict[str, Any]) -> None:
    for key in (
        "clusterIP",
        "clusterIPs",
        "healthCheckNodePort",
        "ipFamilies",
        "ipFamilyPolicy",
    ):
        spec.pop(key, None)
    remove_if(spec, "internalTrafficPolicy", "Cluster")
    remove_if(spec, "sessionAffinity", "None")
    remove_if(spec, "type", "ClusterIP")
    ports = spec.get("ports") or []
    if not isinstance(ports, list):
        return
    for port in ports:
        if not isinstance(port, dict):
            continue
        remove_if(port, "protocol", "TCP")
        if port.get("targetPort") == port.get("port"):
            port.pop("targetPort", None)


def canonical_ingress_metadata(value: dict[str, Any], *, live_snapshot: bool) -> None:
    if not live_snapshot:
        return
    metadata = mapping(value.get("metadata"))
    finalizers = metadata.get("finalizers")
    if not isinstance(finalizers, list):
        return
    annotations = mapping(metadata.get("annotations"))
    group_name = annotations.get("alb.ingress.kubernetes.io/group.name")
    controller_finalizer = (
        f"group.ingress.k8s.aws/{group_name}"
        if isinstance(group_name, str) and group_name
        else None
    )
    retained = [
        item
        for item in finalizers
        if controller_finalizer is None or item != controller_finalizer
    ]
    if retained:
        metadata["finalizers"] = retained
    else:
        metadata.pop("finalizers", None)
    value["metadata"] = metadata


def canonical_resource(
    doc: dict[str, Any], namespace: str, *, live_snapshot: bool
) -> dict[str, Any]:
    value = copy.deepcopy(doc)
    value.pop("status", None)
    canonical_metadata(value, namespace, live_snapshot=live_snapshot)
    kind = value.get("kind")
    spec = mapping(value.get("spec"))
    if kind == "Deployment":
        canonical_deployment_spec(spec)
    elif kind == "Ingress":
        canonical_ingress_metadata(value, live_snapshot=live_snapshot)
    elif kind == "Service":
        canonical_service_spec(spec)
    if spec:
        value["spec"] = spec
    elif "spec" in value:
        value.pop("spec", None)
    return value


def workload_container(doc: dict[str, Any], name: str) -> dict[str, Any] | None:
    pod_spec = mapping(mapping(mapping(doc.get("spec")).get("template")).get("spec"))
    for field in ("containers", "initContainers"):
        containers = pod_spec.get(field) or []
        if not isinstance(containers, list):
            continue
        for item in containers:
            if isinstance(item, dict) and item.get("name") == name:
                return item
    return None


def validate_rendered_container_section(
    ref: ResourceRef,
    section: str,
    containers: object,
    seen_names: set[str],
    failures: list[str],
) -> None:
    if not isinstance(containers, list):
        failures.append(f"{ref.label()}: render {section} must be a list")
        return
    for index, raw_container in enumerate(containers):
        if not isinstance(raw_container, dict):
            failures.append(f"{ref.label()}: render {section}[{index}] must be a mapping")
            continue
        name = raw_container.get("name")
        if not isinstance(name, str) or not name or name in seen_names:
            failures.append(
                f"{ref.label()}: rendered container names must be non-empty and unique"
            )
        else:
            seen_names.add(name)
        if not immutable_image_reference(raw_container.get("image")):
            failures.append(
                f"{ref.label()} {section}[{index}]: rendered image must use a full 40-hex SHA tag or digest"
            )


def validate_rendered_deployment_images(
    included: dict[ResourceRef, dict[str, Any]],
    render: dict[ResourceRef, dict[str, Any]],
    failures: list[str],
) -> None:
    for ref in included:
        if ref.kind != "Deployment" or ref not in render:
            continue
        pod_spec = mapping(mapping(mapping(render[ref].get("spec")).get("template")).get("spec"))
        seen_names: set[str] = set()
        for section in ("initContainers", "containers"):
            validate_rendered_container_section(
                ref, section, pod_spec.get(section) or [], seen_names, failures
            )


def validate_provenance_container_comparison(
    phase: str,
    ref: ResourceRef,
    container_name: str,
    evidence: dict[str, Any],
    live_doc: dict[str, Any],
    render_doc: dict[str, Any],
    canonical_render: dict[ResourceRef, dict[str, Any]],
    failures: list[str],
) -> None:
    live_container = workload_container(live_doc, container_name)
    render_container = workload_container(render_doc, container_name)
    if live_container is None:
        failures.append(f"{ref.label()}: live container {container_name!r} is absent")
        return
    if render_container is None:
        failures.append(f"{ref.label()}: rendered container {container_name!r} is absent")
        return
    expected_live = (
        evidence["sourceImage"]
        if phase == "pre-normalization"
        else evidence["normalizedImage"]
    )
    if live_container.get("image") != expected_live:
        failures.append(
            f"{ref.label()} container {container_name}: live image differs from {phase} contract"
        )
    if render_container.get("image") != evidence["normalizedImage"]:
        failures.append(
            f"{ref.label()} container {container_name}: render image differs from normalized image"
        )
    if phase != "pre-normalization":
        return
    canonical = canonical_render.get(ref)
    if canonical is None:
        return
    target = workload_container(canonical, container_name)
    if target is not None:
        target["image"] = evidence["sourceImage"]


def validate_images_and_compare(
    phase: str,
    namespace: str,
    image_transitions: dict[tuple[ResourceRef, str], dict[str, Any]],
    included: dict[ResourceRef, dict[str, Any]],
    live: dict[ResourceRef, dict[str, Any]],
    render: dict[ResourceRef, dict[str, Any]],
    failures: list[str],
) -> None:
    validate_rendered_deployment_images(included, render, failures)
    canonical_live = {
        ref: canonical_resource(doc, namespace, live_snapshot=True)
        for ref, doc in live.items()
        if ref in included
    }
    canonical_render = {
        ref: canonical_resource(doc, namespace, live_snapshot=False)
        for ref, doc in render.items()
        if ref in included
    }
    for (ref, container_name), evidence in image_transitions.items():
        live_doc = live.get(ref)
        render_doc = render.get(ref)
        if live_doc is None or render_doc is None:
            continue
        validate_provenance_container_comparison(
            phase,
            ref,
            container_name,
            evidence,
            live_doc,
            render_doc,
            canonical_render,
            failures,
        )
    for ref in included:
        if ref not in canonical_live or ref not in canonical_render:
            continue
        if canonical_live[ref] != canonical_render[ref]:
            failures.append(f"{ref.label()}: normalized live/render semantics differ")


def image_list(value: object) -> dict[str, str] | None:
    if not isinstance(value, str) or not value:
        return None
    result: dict[str, str] = {}
    for part in value.split(","):
        if "=" not in part:
            return None
        alias, image = (item.strip() for item in part.split("=", 1))
        if not alias or not image or alias in result:
            return None
        result[alias] = image
    return result


def load_application(path: Path) -> tuple[dict[str, Any] | None, list[str], bool]:
    if not path.is_file():
        return None, [f"Application file not found: {path}"], True
    docs, error = load_yaml_documents(path)
    if error is not None:
        return None, [f"cannot load Application {path}: {error}"], True
    docs = [doc for doc in docs if doc is not None]
    if len(docs) != 1 or not isinstance(docs[0], dict):
        return None, ["Application file must contain exactly one mapping document"], True
    if docs[0].get("apiVersion") != "argoproj.io/v1alpha1" or docs[0].get("kind") != "Application":
        return None, ["Application file must contain one argoproj.io/v1alpha1 Application"], False
    return docs[0], [], False


def application_shape(
    app: dict[str, Any], failures: list[str]
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    validate_exact_fields(app, APPLICATION_FIELDS, "Application", failures)
    metadata = validate_exact_fields(
        app.get("metadata"),
        {"name", "namespace", "finalizers", "annotations"},
        "Application metadata",
        failures,
        optional={"labels"},
    )
    spec = validate_exact_fields(
        app.get("spec"), APPLICATION_SPEC_FIELDS, "Application spec", failures
    )
    source = validate_exact_fields(
        spec.get("source"),
        APPLICATION_SOURCE_FIELDS,
        "Application spec.source",
        failures,
    )
    destination = validate_exact_fields(
        spec.get("destination"),
        APPLICATION_DESTINATION_FIELDS,
        "Application spec.destination",
        failures,
    )
    return metadata, spec, source, destination


def validate_application_identity(
    metadata: dict[str, Any],
    spec: dict[str, Any],
    source: dict[str, Any],
    destination: dict[str, Any],
    target: dict[str, str],
    source_revision: str,
    failures: list[str],
) -> None:
    if metadata.get("name") != target.get("application"):
        failures.append("Application metadata.name differs from contract target.application")
    if metadata.get("namespace") != "argo-cd":
        failures.append("Application metadata.namespace must be argo-cd")
    if spec.get("project") != target.get("project"):
        failures.append("Application spec.project differs from contract target.project")
    for key, contract_key in (("repoURL", "repository"), ("path", "path")):
        if source.get(key) != target.get(contract_key):
            failures.append(f"Application spec.source.{key} differs from contract")
    if source.get("targetRevision") != source_revision:
        failures.append(
            "passive Application spec.source.targetRevision must equal "
            "spec.sourceRevision"
        )
    if "sources" in spec:
        failures.append("Application spec.sources is unsupported for passive adoption")
    if destination.get("namespace") != target.get("namespace"):
        failures.append("Application destination.namespace differs from contract")
    if destination.get("server") != "https://kubernetes.default.svc":
        failures.append(
            "Application destination.server must be https://kubernetes.default.svc for in-cluster adoption"
        )


def validate_application_sync_policy(
    spec: dict[str, Any], failures: list[str]
) -> None:
    sync_policy = validate_exact_fields(
        spec.get("syncPolicy"),
        APPLICATION_SYNC_FIELDS,
        "Application spec.syncPolicy",
        failures,
    )
    automated = validate_exact_fields(
        sync_policy.get("automated"),
        APPLICATION_AUTOMATED_FIELDS,
        "Application spec.syncPolicy.automated",
        failures,
    )
    if automated.get("prune") is not True or automated.get("selfHeal") is not True:
        failures.append("Application passive sync requires automated prune=true and selfHeal=true")
    options = sync_policy.get("syncOptions")
    if (
        not isinstance(options, list)
        or any(not isinstance(option, str) or not option for option in options)
        or len(options) != len(set(options))
    ):
        failures.append("Application syncOptions must be a unique non-empty string list")
        options = []
    if set(options) != {"PruneLast=true"}:
        failures.append(
            "existing-namespace passive adoption requires exactly syncOptions: [PruneLast=true]"
        )


def application_annotations(
    metadata: dict[str, Any], failures: list[str]
) -> dict[str, Any]:
    finalizers = metadata.get("finalizers")
    if (
        not isinstance(finalizers, list)
        or any(not isinstance(value, str) or not value for value in finalizers)
        or len(finalizers) != len(set(finalizers))
    ):
        failures.append("Application finalizers must be a unique non-empty string list")
        finalizers = []
    if "resources-finalizer.argocd.argoproj.io" not in finalizers:
        failures.append("Application is missing the resources finalizer")
    annotations = metadata.get("annotations")
    if not isinstance(annotations, dict):
        failures.append("Application metadata.annotations must be a mapping")
        return {}
    return annotations


def validate_application_notifications(
    annotations: dict[str, Any], failures: list[str]
) -> None:
    for event in ("deployed", "health-degraded", "sync-failed"):
        key = f"notifications.argoproj.io/subscribe.on-{event}.feishu-ops"
        if annotations.get(key) != "":
            failures.append(f"Application notification {key} must be present and empty")
    if annotations.get(f"{AI_PREFIX}write-back-method") != "argocd":
        failures.append("Application Image Updater write-back-method must be argocd")
    if any(
        str(key).startswith("image-writeback.addx.io/")
        or str(key) == f"{AI_PREFIX}git-branch"
        for key in annotations
    ):
        failures.append("Application contains retired image write-back metadata")


def expected_updater_images(entries: list[dict[str, Any]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in entries:
        parsed = image_repository_and_tag(str(item.get("normalizedImage")))
        if parsed is not None:
            result[str(item.get("alias"))] = parsed[0]
    return result


def validate_updater_entry(
    item: dict[str, Any],
    annotations: dict[str, Any],
    passive: str,
    failures: list[str],
) -> None:
    alias = str(item.get("alias"))
    if annotations.get(f"{AI_PREFIX}{alias}.update-strategy") != "newest-build":
        failures.append(f"Application alias {alias} must use newest-build")
    expected_allow = f"regexp:^{passive}$"
    if annotations.get(f"{AI_PREFIX}{alias}.allow-tags") != expected_allow:
        failures.append(f"Application alias {alias} must remain frozen to {expected_allow}")
    if annotations.get(f"{AI_PREFIX}{alias}.kustomize.image-name") != item.get(
        "kustomizeImageName"
    ):
        failures.append(f"Application alias {alias} kustomize image name differs from contract")
    platforms = annotations.get(f"{AI_PREFIX}{alias}.platforms")
    platform_parts = [part.strip() for part in str(platforms or "").split(",")]
    if (
        not platform_parts
        or any(not part for part in platform_parts)
        or len(platform_parts) != len(set(platform_parts))
        or set(platform_parts) != set(item.get("platforms") or [])
    ):
        failures.append(f"Application alias {alias} platforms differ from contract")


def validate_image_updater_annotations(
    annotations: dict[str, Any],
    passive: str,
    entries: list[dict[str, Any]],
    failures: list[str],
) -> None:
    declared = image_list(annotations.get(f"{AI_PREFIX}image-list"))
    if declared != expected_updater_images(entries):
        failures.append("Application image-list differs from the provenance alias/image set")
    for item in entries:
        validate_updater_entry(item, annotations, passive, failures)


def validate_application_recovery_seed(
    source: dict[str, Any], entries: list[dict[str, Any]], failures: list[str]
) -> None:
    kustomize = validate_exact_fields(
        source.get("kustomize"),
        APPLICATION_KUSTOMIZE_FIELDS,
        "Application spec.source.kustomize",
        failures,
    )
    recovery = kustomize.get("images")
    expected_recovery = {
        f"{item['kustomizeImageName']}={item['normalizedImage']}" for item in entries
    }
    if (
        not isinstance(recovery, list)
        or any(not isinstance(value, str) for value in recovery)
        or len(recovery) != len(set(recovery))
        or set(recovery) != expected_recovery
    ):
        failures.append("Application kustomize.images is not the exact fixed-SHA recovery seed")


def validate_application(
    app: dict[str, Any],
    target: dict[str, str],
    source_revision: str,
    passive: str,
    provenance: dict[tuple[ResourceRef, str], dict[str, Any]],
    failures: list[str],
) -> None:
    entries = list(provenance.values())
    metadata, spec, source, destination = application_shape(app, failures)
    validate_application_identity(
        metadata, spec, source, destination, target, source_revision, failures
    )
    validate_application_sync_policy(spec, failures)
    annotations = application_annotations(metadata, failures)
    validate_application_notifications(annotations, failures)
    validate_image_updater_annotations(annotations, passive, entries, failures)
    validate_application_recovery_seed(source, entries, failures)


def validate_application_phase(
    args: argparse.Namespace,
    target: dict[str, str],
    source_revision: str,
    passive: str,
    provenance: dict[tuple[ResourceRef, str], dict[str, Any]],
    failures: list[str],
) -> bool:
    if args.phase == "passive" and args.application is None:
        failures.append("--application is required for phase passive")
    if args.phase != "passive" and args.application is not None:
        failures.append("--application is valid only for phase passive")
    if args.application is None:
        return False
    app, app_failures, unavailable = load_application(args.application)
    failures.extend(app_failures)
    if app is not None:
        validate_application(app, target, source_revision, passive, provenance, failures)
    return unavailable


def report_failures(failures: list[str], unavailable: bool) -> int:
    for failure in failures:
        print(f"FAIL: {failure}")
    return 2 if unavailable else 1


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    contract, failures, unavailable = load_contract(args.contract)
    if contract is None:
        return report_failures(failures, unavailable)
    validate_exact_fields(contract, CONTRACT_FIELDS, "contract", failures)
    if contract.get("apiVersion") != API_VERSION or contract.get("kind") != CONTRACT_KIND:
        failures.append(f"contract must be {API_VERSION} {CONTRACT_KIND}")
    metadata = validate_exact_fields(
        contract.get("metadata"), METADATA_FIELDS, "contract metadata", failures
    )
    if not isinstance(metadata.get("name"), str) or not metadata.get("name"):
        failures.append("contract metadata.name must be non-empty")
    spec = validate_exact_fields(
        contract.get("spec"), SPEC_FIELDS, "contract spec", failures
    )
    target = validate_target(spec, failures)
    if metadata.get("name") != target.get("application"):
        failures.append("contract metadata.name must equal spec.target.application")
    namespace = target.get("namespace", "")
    included, excluded = validate_inventory(spec, namespace, failures)
    provenance = provenance_entries(spec, included, failures)
    pinned_normalizations = pinned_normalization_entries(
        spec, included, provenance, failures
    )
    validate_governance(spec, included, failures)
    live, load_failures, load_unavailable = load_resources(args.live, namespace, "live")
    failures.extend(load_failures)
    unavailable = unavailable or load_unavailable
    render, load_failures, load_unavailable = load_resources(args.render, namespace, "render")
    failures.extend(load_failures)
    unavailable = unavailable or load_unavailable
    unavailable = unavailable or validate_source_render(
        args, spec, target, namespace, render, failures
    )
    validate_live_identity(
        included,
        excluded,
        live,
        render,
        target.get("application", ""),
        failures,
    )
    validate_images_and_compare(
        args.phase,
        namespace,
        {**provenance, **pinned_normalizations},
        included,
        live,
        render,
        failures,
    )
    unavailable = unavailable or validate_application_phase(
        args,
        target,
        str(spec.get("sourceRevision") or ""),
        str(spec.get("passiveRevision") or ""),
        provenance,
        failures,
    )
    if failures:
        return report_failures(failures, unavailable)
    print(
        "PASS: closed stateless adoption cohort matches normalized live state "
        f"for phase {args.phase}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
