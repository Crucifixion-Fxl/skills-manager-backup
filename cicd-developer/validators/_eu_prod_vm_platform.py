"""Closed, opt-in source validation for the EU-prod VM stack.

This validates a committed configuration, not live transport or authorization.
It never reads credentials, performs network requests, or changes a checkout.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit

import yaml

PROFILE = "eu-prod-vm-stack-v1"
CLUSTER = "aws-740315635167-eu-prod"
REPOSITORY = "DEV/observability-platform"
PROJECT_ID = "1433"
SOURCE_DIRECTORY = "clusters/aws-740315635167-eu-prod/vm-stack-observability"
POLICY = Path(__file__).parent / "profiles" / (PROFILE + ".json")


class SourceError(Exception):
    """A source-context proof is missing or inconsistent."""


class UniqueLoader(yaml.SafeLoader):
    """Reject duplicate YAML keys rather than interpret an ambiguous source."""


def _mapping(loader, node, deep=False):
    loader.flatten_mapping(node)
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise ValueError("duplicate mapping key")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _git(root, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({"GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1"})
    try:
        result = subprocess.run(
            ["git", "--no-pager", "-c", "core.fsmonitor=false", "-C", str(root), *args],
            capture_output=True, env=env, timeout=15, check=True,
        )
        return result.stdout
    except (OSError, subprocess.SubprocessError):
        raise SourceError("Git source context is unavailable or inconsistent") from None


def _repository(remote):
    if remote.startswith("git@gitlab.addx.ai:"):
        remote = "ssh://git@gitlab.addx.ai/" + remote.split(":", 1)[1]
    try:
        value = urlsplit(remote)
        if value.scheme not in {"https", "ssh"} or value.hostname != "gitlab.addx.ai":
            return None
        if value.password or value.username not in {None, "git"} or value.query or value.fragment:
            return None
        if value.port not in {None, 443 if value.scheme == "https" else 22}:
            return None
        return value.path.strip("/").removesuffix(".git").lower()
    except ValueError:
        return None


def _source(directory, revision, cluster):
    if cluster != CLUSTER or not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise SourceError("exact EU-prod cluster and full source revision are required")
    if directory.is_symlink():
        raise SourceError("source directory must not be a symlink")
    root = Path(_git(directory, "rev-parse", "--show-toplevel").decode().strip()).resolve()
    expected = root / SOURCE_DIRECTORY
    if expected.resolve() != expected or directory.resolve() != expected:
        raise SourceError("source path is outside the exact platform profile")
    if _repository(_git(root, "remote", "get-url", "origin").decode().strip()) != REPOSITORY.lower():
        raise SourceError("origin does not match the platform repository")
    if _git(root, "rev-parse", "HEAD").decode().strip() != revision:
        raise SourceError("source revision must equal the checked-out HEAD")
    identity_keys = ("CI_PROJECT_PATH", "CI_PROJECT_ID", "CI_COMMIT_SHA")
    if os.environ.get("CI") or any(os.environ.get(k) for k in identity_keys):
        if (os.environ.get("CI_PROJECT_PATH"), os.environ.get("CI_PROJECT_ID"), os.environ.get("CI_COMMIT_SHA")) != (REPOSITORY, PROJECT_ID, revision):
            raise SourceError("CI repository or revision does not match the source checkout")
        if os.environ.get("CI_SERVER_HOST", "gitlab.addx.ai") != "gitlab.addx.ai":
            raise SourceError("CI instance does not match the source repository")
    manifest = expected / "manifests.yaml"
    if manifest.is_symlink() or set(expected.iterdir()) != {manifest} or not manifest.is_file():
        raise SourceError("profile source must contain only the committed manifests.yaml")
    committed = _git(root, "show", revision + ":" + SOURCE_DIRECTORY + "/manifests.yaml")
    if manifest.read_bytes() != committed:
        raise SourceError("working source differs from the fixed Git revision")
    return list(yaml.load_all(committed.decode("utf-8"), Loader=UniqueLoader))


def check(directory, documents, revision, cluster, repo_context, require_target_match):
    """Return an exit code and safe, value-free findings. No generic fallback."""
    if repo_context != "auto" or require_target_match:
        return 2, ["EUVM-CONTEXT-001: platform profile requires auto context without generic target matching"]
    try:
        policy = json.loads(POLICY.read_text(encoding="utf-8"))
        if (policy["schema_version"], policy["profile"], policy["cluster"], policy["repository"], policy["gitlab_project_id"], policy["source_directory"]) != ("eu-prod-vm-platform-profile/1", PROFILE, CLUSTER, REPOSITORY, PROJECT_ID, SOURCE_DIRECTORY):
            raise ValueError("invalid policy")
        signatures = (policy["baseline_content_sha256"], policy["credential_free_candidate_content_sha256"])
        if any(not re.fullmatch(r"[0-9a-f]{64}", s) for s in signatures):
            raise ValueError("invalid policy signature")
    except (OSError, ValueError, KeyError, TypeError):
        return 2, ["EUVM-POLICY-001: installed platform policy is invalid or unavailable"]
    try:
        source = _source(directory, revision, cluster)
    except SourceError as error:
        return 2, ["EUVM-SOURCE-001: " + str(error)]
    except (OSError, UnicodeError, ValueError, TypeError, yaml.YAMLError):
        return 2, ["EUVM-SOURCE-001: source cannot be interpreted unambiguously"]
    try:
        if source != documents or len(source) != 15 or sum(d.get("kind") == "VMServiceScrape" for d in source) != 4:
            return 1, ["EUVM-INVENTORY-001: platform inventory is incomplete or unexpected"]
        signature = _digest(source)
    except (AttributeError, ValueError, TypeError):
        return 1, ["EUVM-INVENTORY-001: platform inventory contains invalid objects"]
    if signature not in signatures:
        return 1, ["EUVM-CONFIG-001: platform objects differ from the closed reviewed contract"]
    if signature == policy["baseline_content_sha256"]:
        return 1, ["EUVM-AUTH-001: CoreDNS HTTP-default bearer-token configuration remains blocked; source remediation and independent security verification are required"]
    return 0, []
