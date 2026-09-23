#!/usr/bin/env python3
"""Validate the exact staging Argo CD Application for legacy-live migration.

This directed validator supplements the generic Application validator. It
binds one reviewed Application to the expected app repository, target overlay,
destination namespace, and protected ``staging`` application branch.
"""

from __future__ import annotations

import argparse
import re
import sys
from typing import Any
from urllib.parse import urlsplit

from _legacy_target_common import SafeInputError, load_single_yaml


MAX_APPLICATION_BYTES = 1024 * 1024
MAX_YAML_TOKENS = 65_536
K8S_NAME = re.compile(r"^[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?$")
SAFE_PATH_PART = re.compile(r"^[A-Za-z0-9._-]+$")


class ControlledArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        self.exit(2, "FAIL: invalid command-line arguments\n")


def _parser() -> argparse.ArgumentParser:
    parser = ControlledArgumentParser(
        description="Validate one exact legacy-live staging Application."
    )
    parser.add_argument("--app", required=True)
    parser.add_argument("--application-name", required=True)
    parser.add_argument("--expected-repo", required=True)
    parser.add_argument("--expected-path", required=True)
    parser.add_argument("--expected-namespace", required=True)
    parser.add_argument("application")
    return parser


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_repo(value: str) -> bool:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    parts = parsed.path.removeprefix("/").removesuffix(".git").split("/")
    return bool(
        parsed.scheme == "https"
        and parsed.hostname == "gitlab.addx.ai"
        and parsed.port is None
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
        and parsed.path.endswith(".git")
        and len(parts) >= 2
        and all(SAFE_PATH_PART.fullmatch(part) for part in parts)
    )


def _safe_path(value: str) -> bool:
    parts = value.split("/")
    return bool(
        value
        and not value.startswith("/")
        and not value.endswith("/")
        and all(part not in {"", ".", ".."} and SAFE_PATH_PART.fullmatch(part) for part in parts)
    )


def validate_application(
    document: object,
    *,
    app: str,
    application_name: str,
    expected_repo: str,
    expected_path: str,
    expected_namespace: str,
) -> list[str]:
    failures: list[str] = []
    if not isinstance(document, dict):
        return ["Application input must be one mapping"]
    if document.get("apiVersion") != "argoproj.io/v1alpha1" or document.get(
        "kind"
    ) != "Application":
        failures.append("resource must be argoproj.io/v1alpha1 Application")
    metadata = _mapping(document.get("metadata"))
    spec = _mapping(document.get("spec"))
    source = _mapping(spec.get("source"))
    destination = _mapping(spec.get("destination"))
    labels = _mapping(metadata.get("labels"))
    if metadata.get("name") != application_name:
        failures.append("Application metadata.name does not match the expected identity")
    if metadata.get("namespace") != "argo-cd":
        failures.append("Application metadata.namespace must be argo-cd")
    if labels.get("app") != app:
        failures.append("Application labels.app does not match the expected app")
    if "sources" in spec:
        failures.append("legacy-live Application must use exactly one spec.source")
    if source.get("repoURL") != expected_repo:
        failures.append("Application source repository does not match the expected repository")
    if source.get("targetRevision") != "staging":
        failures.append("Application spec.source.targetRevision must be exactly staging")
    if source.get("path") != expected_path:
        failures.append("Application source path does not match the expected target overlay")
    if destination.get("namespace") != expected_namespace:
        failures.append("Application destination namespace does not match the expected namespace")
    if destination.get("server") != "https://kubernetes.default.svc":
        failures.append("Application destination server must be the local Argo CD cluster")
    return failures


def main(argv: list[str]) -> int:
    try:
        args = _parser().parse_args(argv[1:])
    except SystemExit as exc:
        return int(exc.code)
    if (
        not K8S_NAME.fullmatch(args.app)
        or not K8S_NAME.fullmatch(args.application_name)
        or not K8S_NAME.fullmatch(args.expected_namespace)
        or not _safe_repo(args.expected_repo)
        or not _safe_path(args.expected_path)
    ):
        print("FAIL: expected Application identity contract is invalid")
        return 2
    try:
        document = load_single_yaml(
            args.application,
            max_bytes=MAX_APPLICATION_BYTES,
            max_tokens=MAX_YAML_TOKENS,
        )
        failures = validate_application(
            document,
            app=args.app,
            application_name=args.application_name,
            expected_repo=args.expected_repo,
            expected_path=args.expected_path,
            expected_namespace=args.expected_namespace,
        )
    except (SafeInputError, RecursionError, ValueError, OverflowError, MemoryError):
        print("FAIL: Application input cannot be validated safely")
        return 2
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        print(f"FAIL: {len(failures)} legacy target Application violation(s)")
        return 1
    print("PASS: legacy-live Application is bound to protected staging and the exact target")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
