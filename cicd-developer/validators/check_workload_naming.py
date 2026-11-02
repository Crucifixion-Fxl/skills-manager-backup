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

The only name-rule exception is the closed, time-bounded set of three legacy
DEV/buildbarn SG entry Services in
references/approved-exceptions/sg-buildbarn-entry-services.md. It skips only
the {app}/{app}-<suffix> name assertion for those exact bytes at their exact
tracked repository paths (or the skill's own fixtures); the selector-match rule
and every other check still apply. It is never a caller-supplied waiver.

Exit codes: 0 = ok, 1 = violation(s), 2 = usage / dependency error.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

try:
    import yaml  # noqa: F401  (imported so a missing dep fails like the other checks)
except ImportError:
    print("FAIL: PyYAML not installed. install: pip install pyyaml", file=sys.stderr)
    sys.exit(2)

from _namespace_legacy_exceptions import containing_worktree, repository_origin, same_repository
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


SCRIPT_DIR = Path(__file__).resolve().parent
FIXTURE_DIR = SCRIPT_DIR.parent / "references" / "approved-exceptions"
# DEV/buildbarn #4/#5, engineering/skills #396: three legacy SG SYS RBE entry
# Services (ALB backends and the frontend scrape target) predate rule #32.
# Renaming them is a separately reviewed entry migration; until then only these
# exact bytes at these exact tracked paths in DEV/buildbarn skip the name rule.
BUILDBARN_ENTRY_REPOSITORY = "https://gitlab.addx.ai/DEV/buildbarn.git"
BUILDBARN_ENTRY_EXPIRES_ON = date(2026, 12, 31)
BUILDBARN_ENTRY_SERVICES = {
    # Service name: (selector app, repository-relative path, complete-file SHA-256)
    "bb-cache": (
        "bb-frontend",
        ("k8s", "sg-devops", "rendered", "service-bb-cache.yaml"),
        "c32aeee80de1a546bed098a189c80aeb75da52c1d804cf534ca9c2c47b708b70",
    ),
    "bb-cache-metrics": (
        "bb-frontend",
        ("k8s", "sg-devops", "rendered", "service-bb-cache-metrics.yaml"),
        "bdbabd82c156361c29e9d9fef79f642943eaabecd6d48cddb1e24590997b7e7d",
    ),
    "bb-exec": (
        "scheduler",
        ("k8s", "sg-devops", "rendered", "service-bb-exec.yaml"),
        "8429c205deb21c0955f9a350a421f446b1e41b3ea3a85e2d1db68732c11c3057",
    ),
}
MAX_ENTRY_SERVICE_BYTES = 4096


def utc_today() -> date:
    return datetime.now(timezone.utc).date()


def has_entry_service_bytes(path: Path, digest: str) -> bool:
    try:
        return (
            not path.is_symlink()
            and path.is_file()
            and path.stat().st_size <= MAX_ENTRY_SERVICE_BYTES
            and hashlib.sha256(path.read_bytes()).hexdigest() == digest
        )
    except OSError:
        return False


def is_tracked(worktree_root: Path, relative: tuple[str, ...]) -> bool:
    try:
        result = subprocess.run(
            ["git", "-C", str(worktree_root), "ls-files", "--error-unmatch", "--", "/".join(relative)],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def is_approved_entry_service(path: Path, sname: str, selector: dict, today: date) -> bool:
    """Exact legacy DEV/buildbarn entry Service or its own skill fixture, until expiry."""
    approved = BUILDBARN_ENTRY_SERVICES.get(sname)
    if approved is None or today > BUILDBARN_ENTRY_EXPIRES_ON:
        return False
    selector_app, relative, digest = approved
    if selector != {"app": selector_app} or not has_entry_service_bytes(path, digest):
        return False
    fixture = FIXTURE_DIR / f"sg-buildbarn-service-{sname}.yaml"
    try:
        if path.resolve(strict=True) == fixture.resolve(strict=True):
            return True
    except (OSError, RuntimeError):
        return False
    worktree = containing_worktree(path)
    if worktree is None:
        return False
    resolved_file, worktree_root = worktree
    try:
        if resolved_file.relative_to(worktree_root).parts != relative:
            return False
    except ValueError:
        return False
    return (
        same_repository(repository_origin(worktree_root), BUILDBARN_ENTRY_REPOSITORY)
        and is_tracked(worktree_root, relative)
    )


def service_name_failure(path: Path, sname: str, selector: dict, today: date | None = None) -> str | None:
    """Service name must be {app} or {app}-<suffix>, where {app} = selector app."""
    sel_app = selector.get("app")
    if not (isinstance(sel_app, str) and sel_app):
        return None
    if sname == sel_app or sname.startswith(sel_app + "-"):
        return None
    if is_approved_entry_service(path, sname, selector, today or utc_today()):
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
