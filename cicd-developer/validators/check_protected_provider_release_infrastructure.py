#!/usr/bin/env python3
"""Fail closed on protected Crossplane Provider release infrastructure config."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    print("FAIL: PyYAML not installed", file=sys.stderr)
    sys.exit(2)

KIND = "ProtectedProviderReleaseInfrastructureCandidate"
SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
REPOSITORY = re.compile(r"^base/provider-(family-gcp|gcp-managedkafka)$")
PROTECTED_TAG = "provider-release-protected"
ALLOWED_SOURCE = "DEV/provider-upjet-gcp"
ALLOWED_REF = "main"
ALLOWED_REGISTRY = "harbor-12571-sg-devops.addx.live"
ALLOWED_PROJECT = "base"
# Authority-commit pins are a dated, locally verified snapshot (2026-08-18) of
# the two protected parents; they match runner.gitops.authority_evidence in
# references/provider-release/protected-provider-release.yaml. When either
# protected ref advances, refresh these pins, that profile evidence, the two
# test fixtures, and the e2e slot values together from fresh read-only evidence.
OVERLAY = {
    "repository": "DEV/k8s",
    "protectedRef": "master",
    "commit": "7c5a7dc267f09ace198a66102bfbf4eae3cc783f",
    "parentPath": "clusters/aws-125710977284-sg-devops/cicd/gitlab-runner/",
    "desiredFile": "values-override-provider-release-protected-amd64.yaml",
}
APPLICATION = {
    "repository": "DEV/argocd-apps",
    "protectedRef": "main",
    "commit": "bebe645d7372a8c573589cd9442074682d9c981a",
    "parentPath": "aws-125710977284-sg-devops/",
    "desiredFile": "gitlab-runner-provider-release-protected-amd64.yaml",
}
EXPECTED_REPOSITORIES = {"base/provider-family-gcp", "base/provider-gcp-managedkafka"}
EXPECTED_ROBOT_ALLOW = {"pull", "push"}
EXPECTED_ROBOT_DENY = {"delete", "admin", "project-admin", "repository-create"}
EXPECTED_BINDINGS = {"signature", "provenance", "source-ref", "source-commit"}


def mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def list_of_strings(value: Any) -> list[str] | None:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return None
    return value


def exact_mapping(value: dict[str, Any], expected: dict[str, Any]) -> bool:
    return set(value) == set(expected) and all(value.get(key) == item for key, item in expected.items())


def validate_authority_record(record: dict[str, Any], expected: dict[str, str]) -> bool:
    authority = {key: expected[key] for key in ("repository", "protectedRef", "commit", "parentPath")}
    return set(record) == set(authority) and all(record.get(key) == value for key, value in authority.items())


def validate_planned_addition(
    addition: dict[str, Any], expected: dict[str, str], extra: dict[str, Any]
) -> bool:
    required = {
        "repository": expected["repository"],
        "parentPath": expected["parentPath"],
        "desiredFile": expected["desiredFile"],
        "runnerTag": PROTECTED_TAG,
        "childPrecondition": "absent-before-additive-creation",
        **extra,
    }
    return exact_mapping(addition, required)


def validate_runner(runner: dict[str, Any]) -> list[str]:
    result: list[str] = []
    if runner.get("protectedTag") != PROTECTED_TAG:
        result.append("runner.protectedTag must be provider-release-protected")
    if runner.get("dedicated") is not True or runner.get("sharedRunnerReuse") != "forbidden":
        result.append("runner must be dedicated and forbid shared reuse")
    result.extend(validate_registration(mapping(runner.get("registration"))))
    result.extend(validate_controls(mapping(runner.get("controls"))))
    result.extend(validate_gitops(mapping(runner.get("gitops"))))
    return result


def validate_registration(registration: dict[str, Any]) -> list[str]:
    expected = {
        "tokenSource": "unresolved-admin-prerequisite",
        "owner": "unresolved-admin-prerequisite",
        "tokenReadOrReuse": "forbidden",
    }
    if exact_mapping(registration, expected):
        return []
    return ["runner registration token source and owner must remain unresolved admin prerequisites"]


def validate_controls(controls: dict[str, Any]) -> list[str]:
    expected = {
        "protected": "required",
        "locked": "required",
        "runUntagged": False,
        "broadDockerConfig": "forbidden",
        "privileged": "forbidden-unless-exact-approved-recipe",
        "serviceAccountOverride": "forbidden-unless-exact-approved-recipe",
    }
    if exact_mapping(controls, expected):
        return []
    return ["runner controls must stay protected, locked, tagged-only, and least privilege"]


def validate_gitops(gitops: dict[str, Any]) -> list[str]:
    result: list[str] = []
    if set(gitops) != {"mode", "coordinatedPair", "authorityEvidence", "plannedAdditions"}:
        result.append("runner.gitops must contain only the coordinated overlay and application contract")
        return result
    if gitops.get("mode") != "gitops-only" or gitops.get("coordinatedPair") != "required":
        result.append("runner.gitops must require a gitops-only coordinated pair")
    authority = mapping(gitops.get("authorityEvidence"))
    if set(authority) != {"overlay", "application"} or not validate_authority_record(
        mapping(authority.get("overlay")), OVERLAY
    ) or not validate_authority_record(mapping(authority.get("application")), APPLICATION):
        result.append("runner.gitops requires exact master/main overlay and application commit-and-parent-path authority records")
    additions = mapping(gitops.get("plannedAdditions"))
    if set(additions) != {"overlay", "application"} or not validate_planned_addition(
        mapping(additions.get("overlay")), OVERLAY, {}
    ) or not validate_planned_addition(
        mapping(additions.get("application")), APPLICATION, {"chartPath": "cicd/apps/gitlab-runner"}
    ):
        result.append("runner.gitops requires both absent desired child additions; existing child or identity collision is a STOP")
    return result


def validate_registry(registry: dict[str, Any]) -> list[str]:
    result: list[str] = []
    if registry.get("host") != ALLOWED_REGISTRY or registry.get("project") != ALLOWED_PROJECT:
        result.append("registry must use the fixed internal Harbor base project")
    repositories = list_of_strings(registry.get("repositories"))
    if repositories is None or set(repositories) != EXPECTED_REPOSITORIES or len(repositories) != 2:
        result.append("registry.repositories must be the fixed family and managedkafka pair")
    elif any(not REPOSITORY.fullmatch(item) for item in repositories):
        result.append("registry.repositories contain an invalid provider release repository")
    robot = mapping(registry.get("robot"))
    allow = list_of_strings(robot.get("allow"))
    deny = list_of_strings(robot.get("deny"))
    if allow is None or set(allow) != EXPECTED_ROBOT_ALLOW:
        result.append("registry.robot.allow must contain only pull and push")
    if deny is None or set(deny) != EXPECTED_ROBOT_DENY:
        result.append("registry.robot.deny must explicitly deny delete and admin scopes")
    return result


def validate_verifier(verifier: dict[str, Any]) -> list[str]:
    result: list[str] = []
    checksum = mapping(verifier.get("checksum"))
    if verifier.get("binaryPath") != ".release-tools/verify-release-evidence":
        result.append("evidenceVerifier must use the fixed verifier path")
    if checksum.get("algorithm") != "sha256" or not SHA256.fullmatch(str(checksum.get("value", ""))):
        result.append("evidenceVerifier requires a concrete sha256 checksum")
    trust_root = verifier.get("trustRoot")
    if not isinstance(trust_root, str) or not trust_root.strip():
        result.append("evidenceVerifier requires a non-empty trust root")
    bindings = list_of_strings(verifier.get("requiredBindings"))
    if bindings is None or set(bindings) != EXPECTED_BINDINGS:
        result.append("evidenceVerifier must require signature, provenance, source-ref, and source-commit")
    return result


def failures(doc: dict[str, Any]) -> list[str]:
    result: list[str] = []
    if set(doc) != {"schemaVersion", "kind", "metadata", "spec"}:
        result.append("configuration must use only schemaVersion, kind, metadata, and spec")
    if doc.get("schemaVersion") != "v1" or doc.get("kind") != KIND:
        result.append("configuration must use the exact non-deployable ProtectedProviderReleaseInfrastructureCandidate schema")
        return result
    metadata = mapping(doc.get("metadata"))
    if set(metadata) != {"name"} or metadata.get("name") != "provider-upjet-gcp-release":
        result.append("metadata must contain only provider-upjet-gcp-release name")
    spec = mapping(doc.get("spec"))
    expected_spec_keys = {"providerSource", "runner", "registry", "credentials", "evidenceVerifier", "vulnerabilityPolicy", "release", "rollout"}
    if set(spec) != expected_spec_keys:
        result.append("spec must contain only the fixed protected-release fields")
    source = mapping(spec.get("providerSource"))
    if source != {"repository": ALLOWED_SOURCE, "protectedRef": ALLOWED_REF}:
        result.append("providerSource must fix DEV/provider-upjet-gcp at protected main")
    result.extend(validate_runner(mapping(spec.get("runner"))))
    result.extend(validate_registry(mapping(spec.get("registry"))))
    credentials = {
        "delivery": "vault-or-eso", "mountMode": "path-files-only", "profilePath": ".release-secrets/profile.json",
        "usernamePath": ".release-secrets/harbor-username", "passwordPath": ".release-secrets/harbor-password",
    }
    if mapping(spec.get("credentials")) != credentials:
        result.append("credentials must use only approved path-only Vault/ESO mounts")
    result.extend(validate_verifier(mapping(spec.get("evidenceVerifier"))))
    scanner = mapping(spec.get("vulnerabilityPolicy"))
    if scanner.get("maximumCritical") != 0 or scanner.get("maximumHigh") != 0:
        result.append("vulnerabilityPolicy must allow zero critical and zero high findings")
    if scanner.get("exceptionAuthority") != "security-release-owner":
        result.append("vulnerabilityPolicy requires the fixed security exception authority")
    release = {"immutableTagsOnly": True, "retention": "retain-immutable-release-evidence", "publish": "separately-approved"}
    if mapping(spec.get("release")) != release:
        result.append("release must remain immutable, retained, and separately approved")
    rollout = {"sync": "manual-only", "prune": False, "delete": False, "install": "separately-approved"}
    if mapping(spec.get("rollout")) != rollout:
        result.append("rollout must remain manual, no-prune, no-delete, and separately approved")
    return result


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: check_protected_provider_release_infrastructure.py <directory>", file=sys.stderr)
        return 2
    root = Path(argv[1])
    if not root.is_dir():
        print(f"FAIL: directory not found: {root}", file=sys.stderr)
        return 2
    if root.is_symlink():
        print(f"FAIL: candidate directory must not be a symlink: {root}", file=sys.stderr)
        return 2
    try:
        candidate_files = [path for path in root.iterdir() if path.is_file() and path.suffix in {".yaml", ".yml"}]
    except OSError as exc:
        print(f"FAIL: cannot inspect candidate directory: {exc}", file=sys.stderr)
        return 2
    if len(candidate_files) != 1 or candidate_files[0].name != "provider-release.yaml":
        print("FAIL: candidate directory must contain exactly provider-release.yaml and no other YAML files")
        return 1
    candidate = candidate_files[0]
    try:
        documents = list(yaml.safe_load_all(candidate.read_text(encoding="utf-8")))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        print("FAIL: provider-release.yaml: invalid YAML")
        return 2
    if len(documents) != 1 or not isinstance(documents[0], dict):
        print("FAIL: provider-release.yaml: candidate must contain exactly one YAML mapping")
        return 1
    doc = documents[0]
    if doc.get("kind") != KIND:
        print("FAIL: provider-release.yaml: candidate contains an unexpected YAML document")
        return 1
    name = mapping(doc.get("metadata")).get("name", "<unnamed>")
    errors = [f"FAIL: provider-release.yaml: {KIND}/{name}: {item}" for item in failures(doc)]
    if errors:
        print("\n".join(errors))
        print(f"FAIL: {len(errors)} protected provider release infrastructure violation(s)")
        return 1
    print("PASS: checked 1 protected provider release infrastructure configuration")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
