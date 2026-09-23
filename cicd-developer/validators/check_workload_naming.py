#!/usr/bin/env python3
"""check_workload_naming.py <directory>

Enforces the business-workload naming convention (the {app} base name) and the
label/selector wiring that actually routes traffic and logs.

  1. Every business workload (Rollout / StatefulSet) pod template must carry a
     non-empty metadata.labels.app. fluent-bit-base routes logs by this label
     (references/logging/README.md) and Services select pods by it.

  2. The workload metadata.name must equal that `app` label -- the workload is
     named {app} (no env suffix; the env lives in the namespace and the ArgoCD
     Application name, not the Rollout/StatefulSet name).

  3. Every Service whose spec.selector is non-empty:
     - its name must be {app} or {app}-<suffix> where {app} is the selector's
       `app` value (allows secondary services like {app}-headless / {app}-metrics);
     - the selector must match at least one workload pod-label set in the same
       scan (a selector matching nothing routes to dead air, with no apply-time
       error). Skipped when no workload is in scope (e.g. an overlay patch).

Deployment is treated only as a Service selector target (platform Deployments
are rollout-exempt and not authored by this skill), so the app-label / name
rules apply to Rollout and StatefulSet only.

Exit codes: 0 = ok, 1 = violation(s), 2 = usage / dependency error.
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    import yaml  # noqa: F401  (imported so a missing dep fails like the other checks)
except ImportError:
    print("FAIL: PyYAML not installed. install: pip install pyyaml", file=sys.stderr)
    sys.exit(2)

from _scan import ParseError, iter_docs, resolve_dir

# Workload kinds whose pods a Service can select.
SELECTABLE_KINDS = {"Rollout", "Deployment", "StatefulSet"}
# Business workload kinds this skill produces; they MUST carry an `app` pod
# label and be named {app}. Deployment is excluded -- platform Deployments are
# rollout-exempt and not authored by this skill.
APP_LABEL_REQUIRED_KINDS = {"Rollout", "StatefulSet"}


def pod_template_labels(doc: dict) -> dict:
    template = ((doc.get("spec") or {}).get("template") or {})
    meta = template.get("metadata") or {}
    labels = meta.get("labels")
    return labels if isinstance(labels, dict) else {}


def name_of(doc: dict) -> str:
    meta = doc.get("metadata") or {}
    name = meta.get("name")
    return name if isinstance(name, str) else "<unnamed>"


def workload_name_failures(path: Path, kind: str, doc: dict, labels: dict) -> list[str]:
    """app-label presence + name == app label, for Rollout / StatefulSet."""
    wname = name_of(doc)
    app = labels.get("app")
    if not isinstance(app, str) or not app:
        return [
            f"FAIL: {path}: {kind}/{wname}: pod template missing "
            "metadata.labels.app (fluent-bit log routing + Service selection "
            "both key on it)"
        ]
    if wname != app:
        return [
            f"FAIL: {path}: {kind}/{wname}: name must equal its pod 'app' label "
            f"{app!r} (workload is named {{app}}, no env suffix -- env lives in "
            "the namespace / Application name)"
        ]
    return []


def service_name_failure(path: Path, sname: str, selector: dict) -> str | None:
    """Service name must be {app} or {app}-<suffix>, where {app} = selector app."""
    sel_app = selector.get("app")
    if not (isinstance(sel_app, str) and sel_app):
        return None
    if sname == sel_app or sname.startswith(sel_app + "-"):
        return None
    return (
        f"FAIL: {path}: Service/{sname}: name must be {sel_app!r} or "
        f"'{sel_app}-<suffix>' (e.g. {sel_app}-headless), matching its selector app"
    )


def selector_matches_a_workload(selector: dict, workload_labels: list[dict]) -> bool:
    return any(
        all(labels.get(k) == v for k, v in selector.items())
        for labels in workload_labels
    )


def service_failures(
    path: Path, sname: str, selector: dict, workload_labels: list[dict]
) -> list[str]:
    out: list[str] = []
    name_problem = service_name_failure(path, sname, selector)
    if name_problem:
        out.append(name_problem)
    # Selector match is only verifiable with a workload in scope.
    if workload_labels and not selector_matches_a_workload(selector, workload_labels):
        out.append(
            f"FAIL: {path}: Service/{sname}: spec.selector {selector} matches no "
            "Rollout/Deployment/StatefulSet pod labels in scan (Service would "
            "route to nothing)"
        )
    return out


def classify(path: Path, doc) -> tuple[dict | None, tuple | None, list[str]]:
    """Process one document.

    Returns (workload_pod_labels_or_None, service_tuple_or_None, failures): the
    pod labels to record if it is a selectable workload, a (path, name, selector)
    tuple if it is a Service with a selector, and any workload-name failures.
    """
    if isinstance(doc, ParseError):
        return None, None, [f"FAIL: {path}: {doc.message}"]
    if not isinstance(doc, dict):
        return None, None, []
    kind = doc.get("kind")
    if kind in SELECTABLE_KINDS:
        labels = pod_template_labels(doc)
        fails = (
            workload_name_failures(path, kind, doc, labels)
            if kind in APP_LABEL_REQUIRED_KINDS
            else []
        )
        return labels, None, fails
    if kind == "Service":
        selector = (doc.get("spec") or {}).get("selector")
        if isinstance(selector, dict) and selector:
            return None, (path, name_of(doc), selector), []
    return None, None, []


def main(argv: list[str]) -> int:
    root = resolve_dir(argv, "check_workload_naming.py")
    if root is None:
        return 2

    failures: list[str] = []
    workload_labels: list[dict] = []
    services: list[tuple] = []

    for path, doc in iter_docs(root):
        labels, service, fails = classify(path, doc)
        if labels is not None:
            workload_labels.append(labels)
        if service is not None:
            services.append(service)
        failures.extend(fails)

    for path, sname, selector in services:
        failures.extend(service_failures(path, sname, selector, workload_labels))

    if failures:
        for failure in failures:
            print(failure)
        print(f"FAIL: {len(failures)} workload-naming violation(s)")
        return 1

    checked = len(workload_labels) + len(services)
    print(f"PASS: {checked} workload/service object(s) checked, naming consistent")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
