#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6.0"]
# ///
"""Check catalog admission for a Build target, without contacting a cluster."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("FAIL: PyYAML not installed", file=sys.stderr)
    raise SystemExit(2)


class UniqueKeyLoader(yaml.SafeLoader):
    """Reject catalog ambiguity instead of silently taking the last value."""

    def construct_mapping(self, node, deep=False):
        if not isinstance(node, yaml.MappingNode):
            return super().construct_mapping(node, deep=deep)
        self.flatten_mapping(node)
        seen = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in seen
                seen.add(key)
            except TypeError as exc:
                raise yaml.constructor.ConstructorError(
                    None, None, "unhashable catalog key", key_node.start_mark
                ) from exc
            if duplicate:
                raise yaml.constructor.ConstructorError(
                    None, None, "duplicate catalog key", key_node.start_mark
                )
        return super().construct_mapping(node, deep=deep)


def resolve_target(
    cluster_data: object,
    env_data: object,
    *,
    env_keyword: str | None = None,
    cluster_name: str | None = None,
) -> dict:
    """Resolve one exact target and reject retired or ambiguous catalog entries."""
    if (env_keyword is None) == (cluster_name is None):
        raise ValueError("select exactly one environment keyword or cluster name")
    entries = cluster_data.get("clusters") if isinstance(cluster_data, dict) else None
    if not isinstance(entries, list) or not entries:
        raise ValueError("clusters.yaml must contain a non-empty clusters list")
    clusters = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("cluster entry must be a mapping")
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip() or name in clusters:
            raise ValueError("cluster names must be non-empty and unique")
        if entry.get("deployment_status") not in ("allowed", "retired"):
            raise ValueError(f"cluster {name}: missing or invalid deployment_status")
        evidence = entry.get("lifecycle_evidence")
        if entry["deployment_status"] == "retired" and (
            not isinstance(evidence, str) or not evidence.strip()
        ):
            raise ValueError(f"cluster {name}: retired entry requires lifecycle_evidence")
        clusters[name] = entry
    if env_keyword is not None:
        envs = env_data.get("env_keywords") if isinstance(env_data, dict) else None
        if not isinstance(envs, dict) or env_keyword not in envs:
            raise ValueError(f"unknown environment keyword: {env_keyword}")
        target = envs[env_keyword]
        if not isinstance(target, dict) or not isinstance(target.get("cluster"), str):
            raise ValueError(f"environment {env_keyword}: missing cluster mapping")
        cluster_name = target["cluster"]
    if cluster_name not in clusters:
        raise ValueError(f"unknown cluster: {cluster_name}")
    target = clusters[cluster_name]
    if target["deployment_status"] != "allowed":
        raise ValueError(f"cluster {cluster_name} is retired; no new or extended deployment")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    selector = parser.add_mutually_exclusive_group(required=True)
    selector.add_argument("--env-keyword")
    selector.add_argument("--cluster")
    args = parser.parse_args()
    data = Path(__file__).resolve().parents[1] / "references/data"
    try:
        clusters = yaml.load(
            (data / "clusters.yaml").read_text(encoding="utf-8"), Loader=UniqueKeyLoader
        )
        envs = yaml.load(
            (data / "env-keywords.yaml").read_text(encoding="utf-8"), Loader=UniqueKeyLoader
        )
        target = resolve_target(
            clusters, envs, env_keyword=args.env_keyword, cluster_name=args.cluster
        )
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        print(f"FAIL: cannot read deployment catalog: {exc}")
        return 2
    except ValueError as exc:
        print(f"FAIL: {exc}")
        return 1
    print(f"PASS: catalog admits {target['name']}; live readiness is not verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
