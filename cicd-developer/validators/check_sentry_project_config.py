#!/usr/bin/env python3
"""Validate optional PROJECT_SLUG/DSN_HOST on sentry-onboard ConfigMaps.

Missing/blank fields retain legacy defaults. This checks field shape only, not
image capabilities, project ownership, Vault credentials or live reachability.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

from _scan import ParseError, iter_files


SLUG = re.compile(r"[a-z0-9][a-z0-9-]{1,62}[a-z0-9]")
HOST = re.compile(r"([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?")


def check_doc(doc: dict[str, Any]) -> list[str]:
    if doc.get("kind") != "ConfigMap":
        return []
    data = doc.get("data")
    if not isinstance(data, dict):
        return []
    metadata = doc.get("metadata")
    name = metadata.get("name", "") if isinstance(metadata, dict) else ""
    if not (
        data.get("VAULT_K8S_ROLE") == "sentry-onboard"
        or name == "sentry-onboard-config"
        or isinstance(name, str) and name.endswith("-sentry-onboard-config")
    ):
        return []

    failures = []
    for field in ("PROJECT_SLUG", "DSN_HOST"):
        if field not in data:
            continue
        value = data[field]
        if not isinstance(value, str):
            failures.append(f"ConfigMap.data.{field} must be a string")
            continue
        value = value.strip()
        if not value:
            continue
        if field == "PROJECT_SLUG" and not SLUG.fullmatch(value):
            failures.append("ConfigMap.data.PROJECT_SLUG must be a 3-64 character lowercase slug")
        if field == "DSN_HOST" and (len(value) > 253 or not HOST.fullmatch(value.lower())):
            failures.append("ConfigMap.data.DSN_HOST must be a DNS hostname without scheme, credentials, port or path")
    return failures


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: check_sentry_project_config.py <directory>", file=sys.stderr)
        return 2
    root = Path(argv[1])
    if not root.is_dir():
        print("FAIL: manifest directory not found", file=sys.stderr)
        return 2
    failures = []
    for path, docs in iter_files(root):
        if isinstance(docs, ParseError):
            print(f"FAIL: {path}: invalid YAML")
            return 2
        for doc in docs:
            if isinstance(doc, dict):
                failures.extend(f"FAIL: {path}: {item}" for item in check_doc(doc))
    if failures:
        print("\n".join(failures))
        return 1
    print("PASS: Sentry optional project/ingest fields have valid shapes")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv))
