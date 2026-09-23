"""Exercise the documented source repair with real Kustomize ordering."""

from __future__ import annotations

import copy
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml


SKILL_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = SKILL_ROOT / "workflows/add-logging.md"
KINDS = ("Deployment", "StatefulSet", "Rollout")


def example(marker: str, kind: str) -> dict:
    text = WORKFLOW.read_text(encoding="utf-8")
    match = re.search(r"```yaml\n\s*# " + re.escape(marker) + r"\n(.*?)```", text, re.S)
    assert match, f"missing executable workflow example: {marker}"
    source = match.group(1)
    for before, after in {
        "<workload-kind>": kind,
        "<workload-name>": "orders",
        "<env-keyword>": "staging-us",
    }.items():
        source = source.replace(before, after)
    return yaml.safe_load(source)


def write_yaml(path: Path, value: dict) -> None:
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def render(path: Path) -> dict[tuple[str, str], dict]:
    binary = shutil.which("kustomize")
    assert binary, "real kustomize is required for logging source repair tests"
    result = subprocess.run(
        [binary, "build", str(path)], text=True, capture_output=True, check=True
    )
    return {
        (doc["kind"], doc["metadata"]["name"]): doc
        for doc in yaml.safe_load_all(result.stdout)
        if isinstance(doc, dict)
    }


def fixture(path: Path, kind: str) -> dict:
    workload = {
        "apiVersion": "argoproj.io/v1alpha1" if kind == "Rollout" else "apps/v1",
        "kind": kind,
        "metadata": {"name": "orders", "labels": {"owner": "checkout"}},
        "spec": {
            "replicas": 1,
            "selector": {"matchLabels": {"app": "orders"}},
            "template": {
                "metadata": {
                    "labels": {"app": "orders", "env": "staging", "track": "stable"}
                },
                "spec": {"containers": [{"name": "app", "image": "orders:abcdef0"}]},
            },
        },
    }
    if kind == "StatefulSet":
        workload["spec"]["serviceName"] = "orders"
    elif kind == "Rollout":
        workload["spec"]["strategy"] = {"canary": {"steps": []}}
    write_yaml(path / "workload.yaml", workload)
    write_yaml(
        path / "service.yaml",
        {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {"name": "orders", "labels": {"owner": "checkout"}},
            "spec": {
                "selector": {"app": "orders"},
                "ports": [{"port": 80, "targetPort": 8080}],
            },
        },
    )
    return {
        "apiVersion": "kustomize.config.k8s.io/v1beta1",
        "kind": "Kustomization",
        "resources": ["workload.yaml", "service.yaml"],
    }


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("source", ("patch-only", "labels", "LabelTransformer"))
def test_logging_source_repair_survives_transformer_order(
    tmp_path: Path, kind: str, source: str
) -> None:
    config = fixture(tmp_path, kind)
    transformer = None
    if source == "labels":
        config["labels"] = [
            {
                "pairs": {"env": "staging", "team": "backend"},
                "includeTemplates": True,
                "includeSelectors": False,
            }
        ]
    elif source == "LabelTransformer":
        transformer = {
            "apiVersion": "builtin",
            "kind": "LabelTransformer",
            "metadata": {"name": "pod-logging"},
            "labels": {"env": "staging", "team": "backend"},
            "fieldSpecs": [
                {"kind": kind, "path": "spec/template/metadata/labels", "create": True}
            ],
        }
        config["transformers"] = ["labels.yaml"]
        write_yaml(tmp_path / "labels.yaml", transformer)
    write_yaml(tmp_path / "kustomization.yaml", config)
    before = render(tmp_path)

    # Applying the old instruction alone reproduces the bug for built-in
    # workloads and for a custom transformer that explicitly handles Rollout.
    config.update(example("logging-env-pod-patch", kind))
    write_yaml(tmp_path / "kustomization.yaml", config)
    patch_only = render(tmp_path)[(kind, "orders")]
    overwritten = source == "LabelTransformer" or (source == "labels" and kind != "Rollout")
    assert patch_only["spec"]["template"]["metadata"]["labels"]["env"] == (
        "staging" if overwritten else "staging-us"
    )

    # Execute the source split example, or repair the explicit pod-only writer.
    if source == "labels":
        config.update(example("logging-env-source-repair", kind))
    elif source == "LabelTransformer":
        assert transformer is not None
        transformer["labels"]["env"] = "staging-us"
        write_yaml(tmp_path / "labels.yaml", transformer)
    write_yaml(tmp_path / "kustomization.yaml", config)
    after = render(tmp_path)
    expected = copy.deepcopy(before)
    expected[(kind, "orders")]["spec"]["template"]["metadata"]["labels"]["env"] = "staging-us"
    # Full render equality preserves selectors, metadata, unrelated labels,
    # Service selectors and all non-label workload configuration.
    assert after == expected


def test_commonlabels_env_requires_selector_migration(tmp_path: Path) -> None:
    config = fixture(tmp_path, "Deployment")
    config["commonLabels"] = {"env": "staging"}
    write_yaml(tmp_path / "kustomization.yaml", config)
    workload = render(tmp_path)[("Deployment", "orders")]
    assert workload["spec"]["selector"]["matchLabels"]["env"] == "staging"
    # Correcting the pod env while preserving this selector cannot be a valid
    # logging-only repair. Keep the workflow's explicit migration STOP.
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "同时参与 workload 的 selector 且值与目标值冲突，STOP" in workflow


def test_control_plane_namespace_policy_uses_exact_project() -> None:
    references = SKILL_ROOT / "references/data/permission-boundaries.yaml"
    projects = yaml.safe_load(references.read_text(encoding="utf-8"))["argocd_app_projects"]
    policy = projects["dedicated_projects"]["platform-control-plane"]["note"]
    assert "职责摘要，不是所有集群的完整或统一权限集合" in policy
    assert "不能据本摘要推断 core /Namespace" in policy
    assert "准确目标 project\n已获准且实际允许 core /Namespace 可直接使用" in policy
    assert "否则由有权 project" in policy
    assert "Git 已批准不证明 live 已同步" in policy
    assert "新增权限走独立平台审批/权限 MR" in policy
