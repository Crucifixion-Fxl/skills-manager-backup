#!/usr/bin/env python3
"""check_vault_paths.py <directory> [--platform-source <path>]

Scans every *.yaml / *.yml under <directory> for Vault path references in
ESO ExternalSecret data[*].remoteRef.key / dataFrom[*].{extract.key,find.path}
and PushSecret data[*].remoteRef.remoteKey.
Verifies each path matches one of the canonical regex patterns declared in
references/vault-paths/rules.yaml.

Both reader and writer references must be relative to the Vault mount configured
on the ClusterSecretStore. A literal ``secret/`` prefix would otherwise resolve
to ``secret/secret/...`` and is rejected before admission/reconcile.

The regex patterns and platform enum are NOT duplicated here; this script reads
the same yaml that references/vault-paths/resolver.md points at. Single source of truth means
adding a new platform in the yaml automatically extends both resolver behavior
and validator coverage.

``secret/cicd/*`` is reserved for platform-owned components. Source manifests
are authorized from their Git checkout. DEV/k8s components use the allowlisted
component roots below; DEV/crossplane-infra additionally permits only the exact
per-cluster ``ninedata-provider-config.yaml`` platform credential file. Rendered
DEV/k8s manifests, whose temporary output path no longer carries repository
provenance, must provide the original component path with ``--platform-source``.
Manifest metadata is never treated as provenance.

The caller is the trust boundary: ``--platform-source`` authorizes every file in
the rendered directory, which must therefore contain isolated output from one
component. Git checks fail closed on common misconfiguration; they do not
authenticate a local caller who controls the process and checkout.

Exit codes:
  0 = every reference matches one pattern
  1 = one or more references do not match any pattern
  2 = dependency missing (PyYAML) or invalid usage or vault-paths/rules.yaml unreadable
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

try:
    import yaml
except ImportError:
    print("FAIL: PyYAML not installed. install: pip install pyyaml", file=sys.stderr)
    sys.exit(2)

from _scan import ParseError, iter_files


SCRIPT_DIR = Path(__file__).resolve().parent
VAULT_RULES_YAML = SCRIPT_DIR.parent / "references" / "vault-paths" / "rules.yaml"
VAULT_MOUNT_PREFIX = "secret/"


def load_patterns() -> list[tuple[str, re.Pattern[str]]]:
    """Read rules from vault-paths/rules.yaml; return [(rule_name, compiled_regex), ...]"""
    if not VAULT_RULES_YAML.is_file():
        print(f"FAIL: cannot locate {VAULT_RULES_YAML}", file=sys.stderr)
        sys.exit(2)
    try:
        data = yaml.safe_load(VAULT_RULES_YAML.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        print(f"FAIL: {VAULT_RULES_YAML} is not valid YAML: {e}", file=sys.stderr)
        sys.exit(2)
    rules = data.get("rules") or []
    out: list[tuple[str, re.Pattern[str]]] = []
    for rule in rules:
        name = rule.get("name", "<unnamed>")
        pat = rule.get("regex")
        if not isinstance(pat, str):
            print(f"FAIL: rule '{name}' missing regex in vault-paths/rules.yaml", file=sys.stderr)
            sys.exit(2)
        try:
            out.append((name, re.compile(pat)))
        except re.error as e:
            print(f"FAIL: rule '{name}' regex invalid: {e}", file=sys.stderr)
            sys.exit(2)
    if not out:
        print(f"FAIL: no rules found in {VAULT_RULES_YAML}", file=sys.stderr)
        sys.exit(2)
    return out


def _normalize_vault_path(v: str) -> str | None:
    """Normalize a possible Vault path into `secret/<...>` form for regex matching.

    ESO reader/writer 推荐写法是**相对路径**（不带 `secret/` 前缀，因为 CSS 已挂 path=secret）。
    legacy app 路径也可能是 `app/env-region/key` 这种相对形式。validator 对所有
    remoteRef.key / extract.key 字符串都统一补 `secret/` 后匹配 rules.yaml；匹配不到
    就 FAIL，避免未知相对路径被静默跳过。
    """
    if not isinstance(v, str):
        return None
    value = v.strip()
    if not value:
        return None
    if value.startswith(VAULT_MOUNT_PREFIX):
        return value
    return VAULT_MOUNT_PREFIX + value


def _relative_reference_violation(
    kind: str | None,
    reference_name: str,
    raw: str,
    field: str,
) -> str | None:
    """Return an error when an ESO reader/writer key repeats the Vault mount."""
    if not raw.strip().startswith(VAULT_MOUNT_PREFIX):
        return None
    if kind == "ExternalSecret" and reference_name in {
        "remoteRef.key",
        "extract.key",
        "find.path",
    }:
        return (
            f"{field}: ExternalSecret {reference_name} must be relative "
            "(without leading secret/) because ClusterSecretStore already mounts path=secret"
        )
    if (
        kind in {"PushSecret", "ClusterPushSecret"}
        and reference_name == "remoteRef.remoteKey"
    ):
        return (
            f"{field}: {kind} remoteRef.remoteKey must be relative "
            "(without leading secret/) because ClusterSecretStore already "
            "mounts path=secret; a literal prefix resolves to secret/secret/..."
        )
    return None


PLATFORM_CICD_COMPONENTS = {
    "argocd",
    "argocd-image-updater",
    "casdoor",
    "cicd",
    "elasticsearch",
    "es-config",
    "es-config-cn",
    "es-config-eu",
    "es-config-us",
    "fluent-bit",
    "fluent-bit-universal",
    "gitlab-runner",
    "graylog",
    "harbor",
    "harbor-new",
    "log-frontend",
    "log-splitter",
    "logstash",
    "ninedata",
    "external-secrets-operator",
    "kyverno",
    "prometheus",
    "vector",
}

TRUSTED_GIT_HOST = "gitlab.addx.ai"
TRUSTED_K8S_PROJECT = "DEV/k8s"
TRUSTED_CROSSPLANE_INFRA_PROJECT = "DEV/crossplane-infra"
NINEDATA_PROVIDER_CONFIG_FILE = "ninedata-provider-config.yaml"


def _canonical_gitlab_project(remote_url: str) -> str | None:
    """Return ``group/project`` without ever logging the credential-bearing URL."""
    remote_url = remote_url.strip()
    if not remote_url:
        return None

    if "://" in remote_url:
        try:
            parsed = urlsplit(remote_url)
            scheme = parsed.scheme.lower()
            hostname = (parsed.hostname or "").lower()
        except ValueError:
            return None
        if scheme not in {"git", "http", "https", "ssh"}:
            return None
        if hostname != TRUSTED_GIT_HOST:
            return None
        project = unquote(parsed.path).strip("/")
    else:
        match = re.fullmatch(
            rf"(?:[^@/\s]+@)?{re.escape(TRUSTED_GIT_HOST)}:(?P<project>[^?#]+)",
            remote_url,
        )
        if match is None:
            return None
        project = match.group("project").strip("/")

    if project.endswith(".git"):
        project = project[:-4]
    return project or None


def _git_output(directory: Path, *args: str) -> str | None:
    """Run a read-only Git query and keep stderr/remote credentials out of output."""
    clean_env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GIT_")
    }
    try:
        result = subprocess.run(
            ["git", "-C", str(directory), *args],
            env=clean_env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _repo_path_context(relative_path: Path) -> tuple[str, Path] | None:
    """Return (component, component-root) for anchored DEV/k8s paths."""
    parts = relative_path.parts
    if len(parts) >= 3 and parts[0] == "clusters":
        return parts[2], Path(*parts[:3])
    if len(parts) >= 3 and parts[:2] == ("cicd", "apps"):
        return parts[2], Path(*parts[:3])
    if len(parts) >= 4 and parts[:2] == ("cicd", "base"):
        return parts[3], Path(*parts[:4])
    return None


def _component_has_tracked_files(git_root: Path, component_root: Path) -> bool:
    tracked = _git_output(git_root, "ls-files", "--", component_root.as_posix())
    return bool(tracked)


def _trusted_project_root(source: Path, expected_project: str) -> Path | None:
    """Return a verified checkout root for one exact trusted GitLab project."""
    if not source.exists():
        return None
    resolved_source = source.resolve()
    git_cwd = resolved_source if resolved_source.is_dir() else resolved_source.parent

    root_text = _git_output(git_cwd, "rev-parse", "--show-toplevel")
    if not root_text:
        return None
    git_root = Path(root_text).resolve()
    try:
        resolved_source.relative_to(git_root)
    except ValueError:
        return None

    remote_url = _git_output(
        git_root,
        "config",
        "--local",
        "--get",
        "remote.origin.url",
    )
    if remote_url is None or _canonical_gitlab_project(remote_url) != expected_project:
        return None
    if not _git_output(git_root, "rev-parse", "--verify", "HEAD"):
        return None

    return git_root


def _trusted_k8s_root(source: Path) -> Path | None:
    """Return the verified DEV/k8s checkout root containing ``source``."""
    return _trusted_project_root(source, TRUSTED_K8S_PROJECT)


def _trusted_crossplane_infra_root(source: Path) -> Path | None:
    """Return the verified DEV/crossplane-infra checkout containing ``source``."""
    return _trusted_project_root(source, TRUSTED_CROSSPLANE_INFRA_PROJECT)


def _platform_component_in_checkout(
    source: Path,
    git_root: Path | None,
    tracked_component_cache: dict[Path, bool] | None = None,
) -> str | None:
    if git_root is None:
        return None
    try:
        relative_path = source.resolve().relative_to(git_root)
    except ValueError:
        return None

    context = _repo_path_context(relative_path)
    if context is None:
        return None
    component, component_root = context
    if component not in PLATFORM_CICD_COMPONENTS:
        return None
    if tracked_component_cache is None:
        component_is_tracked = _component_has_tracked_files(git_root, component_root)
    elif component_root in tracked_component_cache:
        component_is_tracked = tracked_component_cache[component_root]
    else:
        component_is_tracked = _component_has_tracked_files(git_root, component_root)
        tracked_component_cache[component_root] = component_is_tracked
    if not component_is_tracked:
        return None
    return component


def _trusted_platform_component(source: Path) -> str | None:
    """Resolve an allowlisted component from a source inside a DEV/k8s checkout."""
    return _platform_component_in_checkout(source, _trusted_k8s_root(source))


def _crossplane_platform_component_in_checkout(
    source: Path,
    git_root: Path | None,
) -> str | None:
    """Authorize only the tracked per-cluster NineData provider credential file."""
    if git_root is None or not source.is_file():
        return None
    try:
        relative_path = source.resolve().relative_to(git_root)
    except ValueError:
        return None
    if len(relative_path.parts) != 2:
        return None
    cluster_dir, file_name = relative_path.parts
    if not cluster_dir.startswith(("aws-", "gcp-", "tencent-")):
        return None
    if file_name != NINEDATA_PROVIDER_CONFIG_FILE:
        return None
    if not _component_has_tracked_files(git_root, relative_path):
        return None
    return "ninedata"


def _is_forbidden_path(
    vault_path: str,
    *,
    platform_component: str | None = None,
) -> str | None:
    if vault_path.startswith("secret/cicd/"):
        if platform_component in PLATFORM_CICD_COMPONENTS:
            return None
        return "business app manifests must not read/write secret/cicd/*"
    if re.match(r"^secret/(dev|staging|pre|prod)/app/shared(/|$)", vault_path):
        return "app/shared is not an ownership boundary; use cross-app credential rules"
    if re.match(
        r"^secret/(dev|staging|pre|prod)-(us|eu|cn|sg|us-east-1|eu-central-1|"
        r"eu-west-1|cn-north-1|cn-northwest-1|cn-beijing|cn-shanghai|ap-southeast-1)(/|$)",
        vault_path,
    ):
        return (
            "env-region as the FIRST path segment is forbidden (e.g. secret/staging-us/...); "
            "the first segment must be a bare env (dev/staging/pre/prod). Region is implied by "
            "the per-region Vault instance (vault-{us,eu,cn}.builder.addx.live for staging), so "
            "putting it in the path is redundant. Middleware/DB creds use "
            "platform-resource-credential: secret/{env}/{platform}/application/{app}/{key}. "
            "(legacy secret/<app>/<env>-<region>/... with the app name first is still allowed)"
        )
    return None


def _collect_reference(
    raw,
    field: str,
    reference_name: str,
    kind: str | None,
    found: list[tuple[str, str]],
    violations: list[str],
    *,
    platform_component: str | None = None,
) -> None:
    if raw is None:
        return
    if not isinstance(raw, str):
        violations.append(f"{field}: Vault path must be a string")
        return
    relative_path_violation = _relative_reference_violation(
        kind,
        reference_name,
        raw,
        field,
    )
    if relative_path_violation:
        violations.append(relative_path_violation)
    norm = _normalize_vault_path(raw)
    if norm is None:
        return
    forbidden = _is_forbidden_path(
        norm,
        platform_component=platform_component,
    )
    if forbidden:
        violations.append(f"{field}: forbidden Vault path {norm}: {forbidden}")
    found.append((field, norm))


def _collect_remote_ref(
    obj: dict,
    kind: str | None,
    found: list[tuple[str, str]],
    violations: list[str],
    *,
    platform_component: str | None,
    breadcrumb: str,
) -> None:
    remote_ref = obj.get("remoteRef")
    if not isinstance(remote_ref, dict):
        return
    for key in ("key", "remoteKey"):
        _collect_reference(
            remote_ref.get(key),
            f"{breadcrumb}.remoteRef.{key}",
            f"remoteRef.{key}",
            kind,
            found,
            violations,
            platform_component=platform_component,
        )


def _collect_data_from_ref(
    obj: dict,
    reference_path: tuple[str, str],
    kind: str | None,
    found: list[tuple[str, str]],
    violations: list[str],
    *,
    platform_component: str | None,
    breadcrumb: str,
) -> None:
    node_name, value_name = reference_path
    node = obj.get(node_name)
    if not isinstance(node, dict):
        return
    _collect_reference(
        node.get(value_name),
        f"{breadcrumb}.{node_name}.{value_name}",
        f"{node_name}.{value_name}",
        kind,
        found,
        violations,
        platform_component=platform_component,
    )


def walk(
    obj,
    found: list[tuple[str, str]],
    violations: list[str],
    *,
    platform_component: str | None = None,
    breadcrumb: str = "",
    current_kind: str | None = None,
) -> None:
    """Walk the YAML tree, collecting (field-path, value) pairs that look like
    Vault path references inside remoteRef nodes."""
    if isinstance(obj, dict):
        kind = obj.get("kind") if isinstance(obj.get("kind"), str) else current_kind
        _collect_remote_ref(
            obj,
            kind,
            found,
            violations,
            platform_component=platform_component,
            breadcrumb=breadcrumb,
        )
        _collect_data_from_ref(
            obj,
            ("extract", "key"),
            kind,
            found,
            violations,
            platform_component=platform_component,
            breadcrumb=breadcrumb,
        )
        _collect_data_from_ref(
            obj,
            ("find", "path"),
            kind,
            found,
            violations,
            platform_component=platform_component,
            breadcrumb=breadcrumb,
        )
        for k, v in obj.items():
            walk(
                v,
                found,
                violations,
                platform_component=platform_component,
                breadcrumb=f"{breadcrumb}.{k}",
                current_kind=kind,
            )
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            walk(
                item,
                found,
                violations,
                platform_component=platform_component,
                breadcrumb=f"{breadcrumb}[{i}]",
                current_kind=current_kind,
            )


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="check_vault_paths.py",
        description="Validate Vault references in Kubernetes YAML manifests.",
    )
    parser.add_argument("directory", type=Path, help="manifest directory to scan")
    parser.add_argument(
        "--platform-source",
        type=Path,
        help=(
            "original DEV/k8s component path for rendered manifests; the Git origin "
            "and allowlisted repository-relative path are verified"
        ),
    )
    return parser.parse_args(argv[1:])


def _validate_file(
    path: Path,
    docs,
    platform_component: str | None,
    patterns: list[tuple[str, re.Pattern[str]]],
) -> tuple[int, int]:
    if isinstance(docs, ParseError):
        print(f"FAIL: {path}: {docs.message}")
        return 1, 0

    bad = 0
    total = 0
    for doc in docs:
        if doc is None:
            continue
        collected: list[tuple[str, str]] = []
        violations: list[str] = []
        walk(
            doc,
            collected,
            violations,
            platform_component=platform_component,
        )
        for violation in violations:
            print(f"FAIL: {path}: {violation}")
            bad += 1
        for field_path, vault_path in collected:
            total += 1
            if not any(pattern.match(vault_path) for _, pattern in patterns):
                print(
                    f"FAIL: {path}: {vault_path} (at {field_path}; "
                    f"no rule in vault-paths/rules.yaml matches)"
                )
                bad += 1
    return bad, total


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    root = args.directory
    if not root.is_dir():
        print(f"FAIL: directory not found: {root}", file=sys.stderr)
        return 2

    explicit_platform_component = None
    if args.platform_source is not None:
        explicit_platform_component = _trusted_platform_component(args.platform_source)
        if explicit_platform_component is None:
            print(
                "FAIL: --platform-source is not an allowlisted component path "
                "inside a DEV/k8s checkout",
                file=sys.stderr,
            )
            return 2

    scan_git_root = _trusted_k8s_root(root)
    scan_crossplane_git_root = _trusted_crossplane_infra_root(root)
    tracked_component_cache: dict[Path, bool] = {}

    patterns = load_patterns()

    bad = 0
    total = 0

    for f, docs in iter_files(root):
        platform_component = (
            explicit_platform_component
            or _platform_component_in_checkout(
                f,
                scan_git_root,
                tracked_component_cache,
            )
            or _crossplane_platform_component_in_checkout(
                f,
                scan_crossplane_git_root,
            )
        )
        file_bad, file_total = _validate_file(
            f,
            docs,
            platform_component,
            patterns,
        )
        bad += file_bad
        total += file_total

    rule_summary = ",".join(name for name, _ in patterns)
    if bad:
        print(f"FAIL: {bad} vault-path violation(s) out of {total} checked "
              f"(rules: {rule_summary})")
        return 1
    print(f"PASS: {total} vault path(s) checked, all canonical "
          f"(rules: {rule_summary})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
