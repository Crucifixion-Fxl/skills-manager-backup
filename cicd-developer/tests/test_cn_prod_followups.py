"""Exercise retained runtime identity and the known CN production log blocker."""

import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RUNTIME = load("runtime_target", "validators/resolve_existing_runtime.py")
LOGGING = load("logging_target", "validators/check_logging_target.py")


@pytest.fixture
def retained(tmp_path):
    apps = tmp_path / "argocd-apps"
    app = tmp_path / "engagement"
    manifest = apps / "tencent-100052802231-cn-prod/engagement-service-prod-cn.yaml"
    source = app / "backend/k8s/overlays/prod-cn-tke"
    manifest.parent.mkdir(parents=True)
    source.mkdir(parents=True)
    (source / "kustomization.yaml").write_text("kind: Kustomization\n")
    application = {
        "apiVersion": "argoproj.io/v1alpha1", "kind": "Application",
        "metadata": {"name": "engagement-service-prod-cn", "labels": {"app": "engagement-service", "env": "prod-cn"}},
        "spec": {
            "source": {"repoURL": "https://gitlab.addx.ai/services/value-added/engagement.git", "path": "backend/k8s/overlays/prod-cn-tke"},
            "destination": {"server": "https://kubernetes.default.svc", "namespace": "prod-cn"},
        },
    }
    workload = {
        "kind": "Rollout", "metadata": {"name": "engagement-service", "namespace": "prod-cn"},
        "spec": {"template": {"metadata": {"labels": {"app": "engagement-service", "env": "prod-cn"}}}},
    }
    return dict(application=application, application_file=manifest, argocd_root=apps,
                app_root=app, expected_repo_url="git@gitlab.addx.ai:services/value-added/engagement.git",
                app="engagement-service", env_keyword="prod-cn-tke-2231", rendered=[workload])


def resolve(values):
    values = copy.deepcopy(values)
    values["application_file"].write_text(yaml.safe_dump(values.pop("application")))
    return RUNTIME.resolve_existing_runtime(**values)


def test_retained_source_path_is_the_only_maintenance_destination(retained):
    result = resolve(retained)
    assert result["runtime_source_path"] == "backend/k8s/overlays/prod-cn-tke"
    assert result["runtime_env_label"] == "prod-cn"
    assert result["application_name"] == "engagement-service-prod-cn"
    assert result["namespace"] == "prod-cn"
    assert result["cluster"] == "cn-tke-prod"
    assert result["env_keyword"] == "prod-cn-tke-2231"
    hook = retained["app_root"] / result["runtime_source_path"] / "migration-job.yaml"
    hook.write_text("kind: Job\n")
    assert hook.parent == retained["app_root"] / retained["application"]["spec"]["source"]["path"]
    assert not (retained["app_root"] / "k8s/overlays/prod-cn-tke-2231").exists()


@pytest.mark.parametrize("field,value", [
    ("env_keyword", "prod-cn"),
    ("env_keyword", "prod-cn-restricted-admin"),
    ("expected_repo_url", "https://gitlab.addx.ai/services/other.git"),
    ("app", "other-service"),
])
def test_wrong_target_repository_or_owner_is_rejected(retained, field, value):
    retained[field] = value
    with pytest.raises(ValueError):
        resolve(retained)


@pytest.mark.parametrize("source_path", ["../outside", "/tmp/other", "backend/../k8s", "backend\\k8s", "missing/overlay"])
def test_unsafe_or_missing_runtime_path_is_rejected(retained, source_path):
    retained["application"]["spec"]["source"]["path"] = source_path
    with pytest.raises(ValueError):
        resolve(retained)


def test_symlink_cannot_escape_application_checkout(retained, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "kustomization.yaml").write_text("kind: Kustomization\n")
    (retained["app_root"] / "escaped").symlink_to(outside, target_is_directory=True)
    retained["application"]["spec"]["source"]["path"] = "escaped"
    with pytest.raises(ValueError):
        resolve(retained)


@pytest.mark.parametrize("outside_checkout", [False, True])
def test_leaf_kustomization_file_symlink_requires_dedicated_handoff(retained, tmp_path, outside_checkout):
    target_root = tmp_path if outside_checkout else retained["app_root"]
    target = target_root / "shared-kustomization.yaml"
    target.write_text("kind: Kustomization\n")
    entry = retained["app_root"] / retained["application"]["spec"]["source"]["path"] / "kustomization.yaml"
    entry.unlink()
    entry.symlink_to(target)
    with pytest.raises(ValueError):
        resolve(retained)
    assert target.read_text() == "kind: Kustomization\n"


def test_leaf_kustomization_file_must_be_regular(retained):
    entry = retained["app_root"] / retained["application"]["spec"]["source"]["path"] / "kustomization.yaml"
    entry.unlink()
    entry.mkdir()
    with pytest.raises(ValueError):
        resolve(retained)


@pytest.mark.parametrize("change", ["multiple-sources", "wrong-namespace", "different-env", "empty-render", "remote-destination"])
def test_ambiguous_or_mismatched_runtime_is_rejected(retained, change):
    if change == "multiple-sources":
        retained["application"]["spec"]["sources"] = [retained["application"]["spec"]["source"]]
    elif change == "wrong-namespace":
        retained["rendered"][0]["metadata"]["namespace"] = "prod-other"
    elif change == "different-env":
        retained["rendered"][0]["spec"]["template"]["metadata"]["labels"]["env"] = "staging-cn"
    elif change == "empty-render":
        retained["rendered"] = []
    else:
        retained["application"]["spec"]["destination"]["server"] = "https://another-cluster"
    with pytest.raises(ValueError):
        resolve(retained)


@pytest.mark.parametrize("renderer", ["helm", "chart", "plugin", "directory"])
def test_explicit_non_kustomize_renderer_cannot_inherit_runtime_resolution(retained, renderer):
    retained["application"]["spec"]["source"][renderer] = "demo" if renderer == "chart" else {}
    with pytest.raises(ValueError, match="non-Kustomize"):
        resolve(retained)


def test_resolved_identity_does_not_waive_namespace_policy(retained):
    result = resolve(retained)
    assert result["namespace"] != "prod-engagement-service"
    registry = (ROOT / "references/data/namespace-legacy-exceptions.yaml").read_text()
    assert "tencent-100052802231-cn-prod" not in registry


def test_describing_an_uncatalogued_runtime_label_does_not_admit_its_logging(retained):
    retained["application"]["metadata"]["labels"]["env"] = "prod-unknown"
    retained["rendered"][0]["spec"]["template"]["metadata"]["labels"]["env"] = "prod-unknown"
    result = resolve(retained)
    assert result["runtime_env_label"] == "prod-unknown"
    assert result["env_keyword"] == "prod-cn-tke-2231"
    with pytest.raises(ValueError, match="no verified target-specific logging mapping"):
        LOGGING.check_logging_target(env_keyword=result["env_keyword"],
                                     runtime_env=result["runtime_env_label"])


def test_same_phase_label_cannot_replace_the_bound_application_identity(retained):
    retained["rendered"][0]["spec"]["template"]["metadata"]["labels"]["env"] = "prod-unknown"
    with pytest.raises(ValueError, match="runtime env identity"):
        resolve(retained)


def test_matching_render_cannot_move_the_application_to_another_phase(retained):
    retained["application"]["metadata"]["labels"]["env"] = "staging-cn"
    retained["rendered"][0]["spec"]["template"]["metadata"]["labels"]["env"] = "staging-cn"
    with pytest.raises(ValueError, match="selected phase"):
        resolve(retained)


@pytest.mark.parametrize("keyword", ["prod-cn", "prod-cn-tke-2231"])
@pytest.mark.parametrize("broken", ["app", "env", "wrong-env"])
def test_logging_repair_remains_reachable_and_requires_strict_recheck(retained, keyword, broken):
    if keyword == "prod-cn":
        retained["application_file"] = retained["argocd_root"] / "tencent-100014919455-cn-main/engagement-service-prod-cn.yaml"
        retained["application_file"].parent.mkdir()
        retained["env_keyword"] = keyword
    labels = retained["rendered"][0]["spec"]["template"]["metadata"]["labels"]
    if broken == "wrong-env":
        labels["env"] = "staging-cn"
    else:
        del labels[broken]
    with pytest.raises(ValueError):
        resolve(retained)
    retained["allow_label_repair"] = True
    result = resolve(retained)
    assert result["runtime_env_label"] == "prod-cn"
    assert result["label_repair_required"] is True
    labels.update(app="engagement-service", env=result["runtime_env_label"])
    retained["allow_label_repair"] = False
    assert resolve(retained)["label_repair_required"] is False


def test_logging_repair_does_not_accept_another_owner(retained):
    retained["allow_label_repair"] = True
    retained["rendered"][0]["spec"]["template"]["metadata"]["labels"]["app"] = "other-service"
    with pytest.raises(ValueError):
        resolve(retained)


def test_new_keyword_logging_is_blocked_before_label_generation():
    with pytest.raises(ValueError, match="blocked"):
        LOGGING.check_logging_target(env_keyword="prod-cn-tke-2231")


def test_retained_label_uses_verified_configuration_mapping_without_e2e_claim():
    result = LOGGING.check_logging_target(env_keyword="prod-cn-tke-2231", runtime_env="prod-cn")
    assert result == "compatible-config; live log delivery remains unverified"


@pytest.mark.parametrize("label", ["prod-cn-tke", "prod-cn-tke-9999", "staging-cn", ""])
def test_other_cn_prod_labels_cannot_inherit_mapping(label):
    with pytest.raises(ValueError):
        LOGGING.check_logging_target(env_keyword="prod-cn-tke-2231", runtime_env=label)


def test_old_route_does_not_become_new_cluster_or_claim_logging_readiness():
    assert LOGGING.check_logging_target(env_keyword="prod-cn") == "no recorded blocker; target topology and live delivery remain unverified"


@pytest.mark.parametrize("keyword", ["missing", "dev-cn"])
def test_logging_guard_keeps_catalog_admission(keyword):
    with pytest.raises(ValueError):
        LOGGING.check_logging_target(env_keyword=keyword)


def test_admitted_tech_route_does_not_claim_logging_delivery():
    assert LOGGING.check_logging_target(env_keyword="prod-cn-restricted-admin") == (
        "no recorded blocker; target topology and live delivery remain unverified"
    )


@pytest.mark.parametrize("state,error", [
    ("blocked", "is blocked"),
    ("pending-cutover", "endpoint cutover is pending"),
])
def test_logging_guard_rejects_unadmitted_tech_catalog(monkeypatch, state, error):
    original_load = LOGGING.yaml.load

    def load_with_unadmitted_target(stream, Loader):
        data = original_load(stream, Loader=Loader)
        if isinstance(data, dict) and isinstance(data.get("clusters"), list):
            target = next(c for c in data["clusters"] if c["name"] == "cn-tke-tech-service")
            if state == "blocked":
                target["deployment_status"] = "blocked"
            else:
                target["deployment_status"] = "allowed"
                target["endpoint_status"] = "pending-cutover"
        return data

    monkeypatch.setattr(LOGGING.yaml, "load", load_with_unadmitted_target)
    with pytest.raises(ValueError, match=error):
        LOGGING.check_logging_target(env_keyword="prod-cn-restricted-admin")


@pytest.mark.parametrize("runtime_env,exit_code", [("prod-cn-tke-2231", 1), ("prod-cn", 0)])
def test_workflow_logging_cli_preserves_gate_exit_status(runtime_env, exit_code):
    result = subprocess.run([sys.executable, str(ROOT / "validators/check_logging_target.py"),
                             "--env-keyword", "prod-cn-tke-2231", "--runtime-env", runtime_env],
                            capture_output=True, text=True)
    assert result.returncode == exit_code
    assert ("compatible-config" in result.stdout) == (exit_code == 0)


def test_workflow_runtime_cli_returns_the_bound_path_and_fails_closed(retained, tmp_path):
    expected = resolve(retained)
    rendered = tmp_path / "rendered.yaml"
    rendered.write_text(yaml.safe_dump_all(retained["rendered"]))
    args = [sys.executable, str(ROOT / "validators/resolve_existing_runtime.py"),
            "--application-file", str(retained["application_file"]),
            "--argocd-root", str(retained["argocd_root"]), "--app-root", str(retained["app_root"]),
            "--source-repo-url", retained["expected_repo_url"], "--app", retained["app"],
            "--env-keyword", retained["env_keyword"], "--rendered", str(rendered)]
    result = subprocess.run(args, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == expected
    retained["rendered"][0]["metadata"]["namespace"] = "prod-other"
    rendered.write_text(yaml.safe_dump_all(retained["rendered"]))
    result = subprocess.run(args, capture_output=True, text=True)
    assert result.returncode == 1
    assert result.stdout == ""
