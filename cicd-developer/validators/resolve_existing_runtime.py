#!/usr/bin/env python3
"""Describe an existing Kustomize runtime; this does not waive deployment policy."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_deployment_target import UniqueKeyLoader, resolve_target
import yaml


def repository_identity(value: str) -> tuple[str, str]:
    if not isinstance(value, str) or not value or any(c.isspace() for c in value):
        raise ValueError("source repository must be a credential-free GitLab URL")
    if value.startswith("git@gitlab.addx.ai:"):
        host, path = "gitlab.addx.ai", value.split(":", 1)[1]
    else:
        url = urlsplit(value)
        if (url.scheme != "https" or url.hostname != "gitlab.addx.ai"
                or url.username or url.password or url.port or url.query or url.fragment):
            raise ValueError("source repository must be a credential-free GitLab URL")
        host, path = url.hostname, url.path.lstrip("/")
    path = path.removesuffix(".git")
    if len(path.split("/")) < 2 or any(p in ("", ".", "..") for p in path.split("/")) or any(c in path for c in "\\%?#"):
        raise ValueError("source repository identity is invalid")
    return host, path


def read_catalog():
    data = Path(__file__).resolve().parents[1] / "references/data"
    return tuple(yaml.load((data / name).read_text(), Loader=UniqueKeyLoader)
                 for name in ("clusters.yaml", "env-keywords.yaml"))


def resolve_existing_runtime(*, application_file: Path, argocd_root: Path,
                             app_root: Path, expected_repo_url: str, app: str,
                             env_keyword: str, rendered: list,
                             allow_label_repair: bool = False) -> dict:
    clusters, environments = read_catalog()
    target = resolve_target(clusters, environments, env_keyword=env_keyword)
    application_file, argocd_root, app_root = (Path(p).resolve() for p in
                                              (application_file, argocd_root, app_root))
    try:
        relative = application_file.relative_to(argocd_root)
    except ValueError as exc:
        raise ValueError("Application is outside the argocd-apps checkout") from exc
    if relative.parent.as_posix() != target["argocd_apps_dir"].rstrip("/"):
        raise ValueError("Application does not belong to the selected cluster directory")
    application = yaml.load(application_file.read_text(), Loader=UniqueKeyLoader)
    if not isinstance(application, dict) or application.get("kind") != "Application" or application.get("apiVersion") != "argoproj.io/v1alpha1":
        raise ValueError("expected one Argo CD Application")
    metadata = application.get("metadata") or {}
    if not isinstance(metadata.get("name"), str) or not metadata["name"]:
        raise ValueError("Application name is missing")
    if (metadata.get("labels") or {}).get("app") != app:
        raise ValueError("Application owner must match the explicit application app label")
    spec = application.get("spec") or {}
    source = spec.get("source") or {}
    if "sources" in spec or any(k in source for k in ("helm", "chart", "plugin", "directory")):
        raise ValueError("ambiguous or non-Kustomize source requires a separate target handoff")
    if repository_identity(source.get("repoURL")) != repository_identity(expected_repo_url):
        raise ValueError("Application source repository differs from the verified checkout origin")
    source_path = source.get("path")
    if (not isinstance(source_path, str) or source_path.startswith("/")
            or "\\" in source_path or any(p in ("", ".", "..") for p in source_path.split("/"))):
        raise ValueError("runtime source.path must be an exact safe repository-relative path")
    runtime = (app_root / source_path).resolve()
    entry = runtime / "kustomization.yaml"
    if entry.is_symlink():
        raise ValueError("linked Kustomize entry requires a separately verified maintenance handoff")
    kustomization = entry.resolve()
    if (not runtime.is_relative_to(app_root)
            or not kustomization.is_relative_to(app_root) or not kustomization.is_file()):
        raise ValueError("runtime source.path is missing or escapes the application checkout")
    destination = spec.get("destination") or {}
    if destination.get("server") != "https://kubernetes.default.svc" or "name" in destination:
        raise ValueError("remote destination requires a separately verified target handoff")
    namespace = destination.get("namespace")
    if not isinstance(namespace, str) or not namespace:
        raise ValueError("Application destination namespace is missing")
    workloads = [doc for doc in rendered if isinstance(doc, dict)
                 and doc.get("kind") in ("Rollout", "Deployment", "StatefulSet")]
    if not workloads:
        raise ValueError("exact runtime render has no supported business workload")
    expected_env = (metadata.get("labels") or {}).get("env")
    phase = environments["env_keywords"][env_keyword]["env"]
    if not isinstance(expected_env, str) or not expected_env.startswith(phase + "-"):
        raise ValueError("Application env identity is missing or differs from the selected phase")
    repair_required = False
    for workload in workloads:
        pod_labels = (((workload.get("spec") or {}).get("template") or {}).get("metadata") or {}).get("labels") or {}
        if (workload.get("metadata") or {}).get("namespace") != namespace:
            raise ValueError("rendered workload namespace differs from Application")
        if pod_labels.get("app") != app and not (allow_label_repair and not pod_labels.get("app")):
            raise ValueError("rendered workload namespace or owner differs from Application")
        env = pod_labels.get("env")
        if env != expected_env and not allow_label_repair:
            raise ValueError("runtime env identity is missing or differs from Application")
        repair_required |= pod_labels.get("app") != app or env != expected_env
    return dict(env_keyword=env_keyword, cluster=target["name"], app=app,
                application_name=metadata["name"], runtime_source_path=source_path,
                namespace=namespace, runtime_env_label=expected_env,
                label_repair_required=repair_required)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("application-file", "argocd-root", "app-root", "rendered"):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ("source-repo-url", "app", "env-keyword"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--allow-label-repair", action="store_true",
                        help="logging-only: report missing app / incorrect env for repair; strict recheck required")
    args = parser.parse_args()
    try:
        result = resolve_existing_runtime(
            application_file=args.application_file, argocd_root=args.argocd_root,
            app_root=args.app_root, expected_repo_url=args.source_repo_url,
            app=args.app, env_keyword=args.env_keyword,
            rendered=list(yaml.load_all(args.rendered.read_text(), Loader=UniqueKeyLoader)),
            allow_label_repair=args.allow_label_repair)
    except (OSError, ValueError, TypeError, AttributeError, yaml.YAMLError):
        print("FAIL: runtime identity cannot be resolved; verify the exact Application, source, render and catalog target", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
