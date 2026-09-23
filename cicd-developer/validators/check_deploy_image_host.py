#!/usr/bin/env python3
"""check_deploy_image_host.py <directory>

The DEPLOY side of a business image -- kustomize overlay `images[].newName` -- MUST be
a LITERAL Harbor host. kustomize and ArgoCD Image Updater do NOT expand shell / CI
variables, so a `${IMAGE_BASE}` (or any `$VAR`) in the host is taken byte-for-byte and
the image pull 404s (hard-rules #25). The CI side may use Option B
(`${IMAGE_BASE}/<env>-<region>/<app>`, host injected by the runner); the deploy side
may not.

This scans every `kustomization.yaml` / `.yml` under <directory> and FAILs any
`images[].newName` on a `cicd/<env-region>/<app>` business path whose value contains a
shell variable. It is deliberately narrow and false-positive-free (verified: 0 of 154
live fleet newNames contain a variable).

NOT implemented here (deliberately): the broader "CI push Harbor == deploy pull Harbor"
cross-check that resolves each build job's runner tag through clusters.yaml. The fleet's
split-horizon Harbor DNS aliases (e.g. registry-harbor-cn == harbor-58989-cn-tech --
same physical registry, different DNS) and legacy / utility runner tags (kubernetes-sg,
sonar-scanner-sg, runner-sg-nat, ...) are not yet modelled; a naive tag->harbor gate
hard-fails dozens of currently-deployed repos. That cross-check stays human review until
the Harbor-alias + runner-tag model is curated (see hard-rules #25). The
Application image-list / recovery seed host remains an explicit cross-file
human review.

Exit codes: 0 pass, 1 fail, 2 dep/usage error.
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("FAIL: PyYAML not installed", file=sys.stderr)
    sys.exit(2)

from _scan import resolve_dir


def iter_new_names(doc):
    if not isinstance(doc, dict):
        return
    images = doc.get("images")
    if not isinstance(images, list):
        return
    for item in images:
        if isinstance(item, dict):
            new_name = item.get("newName")
            if isinstance(new_name, str) and "/cicd/" in new_name:
                yield new_name


def check_file(path: Path) -> tuple[list[str], int]:
    try:
        docs = list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
    except (yaml.YAMLError, UnicodeDecodeError):
        return [], 0  # parse errors are surfaced by other validators
    failures: list[str] = []
    checked = 0
    for doc in docs:
        for new_name in iter_new_names(doc):
            checked += 1
            if "$" in new_name:
                failures.append(
                    f"FAIL: {path}: images[].newName {new_name!r} contains a variable; "
                    f"kustomize / Image Updater do not expand it -> the pull 404s. Use a "
                    f"literal Harbor host (hard-rules #25)."
                )
    return failures, checked


def main(argv: list[str]) -> int:
    root = resolve_dir(argv, "check_deploy_image_host.py")
    if root is None:
        return 2

    failures: list[str] = []
    checked = 0
    for path in sorted({*root.rglob("kustomization.yaml"), *root.rglob("kustomization.yml")}):
        file_failures, file_checked = check_file(path)
        failures.extend(file_failures)
        checked += file_checked

    for failure in failures:
        print(failure)

    if failures:
        print(f"FAIL: {len(failures)} deploy-side image host violation(s)")
        return 1
    if checked == 0:
        print(f"PASS: no business kustomization images[].newName under {root} (nothing to check)")
        return 0
    print(f"PASS: {checked} deploy-side image newName(s) checked, all literal hosts")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
