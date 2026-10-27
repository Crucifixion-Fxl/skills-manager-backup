#!/usr/bin/env python3
"""Reject recorded logging incompatibilities before generating runtime labels."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_deployment_target import UniqueKeyLoader, resolve_target
import yaml


def check_logging_target(*, env_keyword: str, runtime_env: str | None = None) -> str:
    root = Path(__file__).resolve().parents[1] / "references"
    clusters = yaml.load((root / "data/clusters.yaml").read_text(), Loader=UniqueKeyLoader)
    environments = yaml.load((root / "data/env-keywords.yaml").read_text(), Loader=UniqueKeyLoader)
    target = resolve_target(clusters, environments, env_keyword=env_keyword)
    policy = yaml.load((root / "logging/target-compatibility.yaml").read_text(), Loader=UniqueKeyLoader)
    targets = policy.get("targets") if isinstance(policy, dict) else None
    if not isinstance(targets, dict):
        raise ValueError("logging compatibility catalog is invalid")
    if target["name"] not in targets:
        return "no recorded blocker; target topology and live delivery remain unverified"
    entry = targets[target["name"]]
    if not isinstance(entry, dict) or not isinstance(entry.get("runtime_env_labels"), dict):
        raise ValueError("logging target catalog is invalid")
    label = env_keyword if runtime_env is None else runtime_env
    fact = entry["runtime_env_labels"].get(label)
    if not isinstance(fact, dict) or fact.get("status") not in ("blocked", "compatible-config"):
        raise ValueError("runtime env has no verified target-specific logging mapping")
    if fact["status"] == "blocked":
        raise ValueError("logging mapping is blocked; platform GitOps and delivery verification are required before generating or rewriting this label")
    return "compatible-config; live log delivery remains unverified"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-keyword", required=True)
    parser.add_argument("--runtime-env")
    args = parser.parse_args()
    try:
        message = check_logging_target(env_keyword=args.env_keyword, runtime_env=args.runtime_env)
    except (OSError, ValueError, TypeError, yaml.YAMLError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(f"PASS: {message}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
