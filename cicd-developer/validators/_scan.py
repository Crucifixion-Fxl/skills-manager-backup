#!/usr/bin/env python3
"""Shared YAML-scanning helpers for the cicd-developer validators.

This is a helper module, not a standalone validator -- it is imported by the
check_*.py scripts and is not listed in validate.sh.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml


class ParseError:
    """Marker yielded by iter_docs() when a file cannot be parsed."""

    def __init__(self, message: str) -> None:
        self.message = message


def resolve_dir(argv: list[str], prog: str):
    """Return the target Path from argv[1], or None (after printing) on error."""
    if len(argv) < 2:
        print(f"usage: {prog} <directory>", file=sys.stderr)
        return None
    directory = Path(argv[1])
    if not directory.is_dir():
        print(f"FAIL: directory not found: {directory}", file=sys.stderr)
        return None
    return directory


def iter_files(root: Path):
    """Yield (path, documents) for every YAML file under root.

    `documents` is the list of parsed documents, or a ParseError instance when
    the file failed to parse. Callers that need per-file context (e.g. abort vs
    count on a parse error) use this; callers that want a flat document stream
    use iter_docs() below.
    """
    for path in sorted({*root.rglob("*.yaml"), *root.rglob("*.yml")}):
        try:
            yield path, list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
        except (yaml.YAMLError, UnicodeDecodeError) as exc:
            yield path, ParseError(f"invalid YAML: {exc}")


def iter_docs(root: Path):
    """Yield (path, document) for every YAML document under root.

    A document is a ParseError instance when the file failed to parse, so the
    caller decides how to report it.
    """
    for path, documents in iter_files(root):
        if isinstance(documents, ParseError):
            yield path, documents
        else:
            yield from ((path, document) for document in documents)


def is_application(doc) -> bool:
    return (
        isinstance(doc, dict)
        and doc.get("kind") == "Application"
        and (doc.get("apiVersion") or "").startswith("argoproj.io/")
    )


def is_kustomization(doc) -> bool:
    return (
        isinstance(doc, dict)
        and doc.get("kind") == "Kustomization"
        and (doc.get("apiVersion") or "").startswith("kustomize.config.k8s.io/")
    )
