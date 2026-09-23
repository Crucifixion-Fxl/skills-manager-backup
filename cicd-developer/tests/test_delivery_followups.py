"""Render the two first-deployment paths reviewed in F4/F5 without live services.

These exercise the real recipes and validators. Runtime readiness, authorization
and registry digest/platform evidence remain operator gates, not simulated facts.
"""

from pathlib import Path
import copy
import subprocess

import pytest
import yaml

from test_offline_e2e import (
    RECIPE_ROOT,
    load_validator_module,
    render_recipe_template,
    run_validator,
)


PROD = {
    "env": "prod-us",
    "env_keyword": "prod-us",
    "namespace": "prod-my-app",
    "destination_namespace": "prod-my-app",
    "app_with_target_suffix": "my-app-prod-us",
    "source_path": "k8s/overlays/prod-us",
    "harbor_image_path": "harbor-30257-us-prod.addx.live/cicd/prod-us/my-app",
    "image_tag": "abcdef123456",
    "image_seed": "abcdef123456",
    "project": "app-runtime",
}


def recipe(name: str, **overrides: str) -> dict:
    return yaml.safe_load(render_recipe_template(
        RECIPE_ROOT / name, strip_full_line_comments=True,
        overrides=PROD | overrides,
    ))


def write(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")


def build(path: Path) -> list[dict]:
    result = subprocess.run(
        ["kustomize", "build", str(path)], text=True, capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    return list(yaml.safe_load_all(result.stdout))


@pytest.mark.parametrize("first_migration", [False, True])
@pytest.mark.parametrize("workload_wave", [2, 7])
def test_new_prod_rds_candidate_can_render_before_connection_secret_exists(
    tmp_path: Path, first_migration: bool, workload_wave: int,
) -> None:
    """First sync owns the password/DB chain; later consumers aren't prerequisites."""
    base = tmp_path / "k8s/base"
    overlay = tmp_path / "k8s/overlays/prod-us"
    for name in ("rollout", "service"):
        doc = recipe(f"k8s/{name}.yaml.tmpl")
        if name == "rollout":
            doc["metadata"].setdefault("annotations", {})["argocd.argoproj.io/sync-wave"] = str(workload_wave)
            doc["spec"]["template"]["spec"]["containers"][0]["envFrom"] = [
                {"secretRef": {"name": "my-app-db-secret"}}
            ]
        write(base / f"{name}.yaml", doc)
    write(base / "kustomization.yaml", recipe("k8s/kustomization-base.yaml.tmpl"))
    resources = {
        "rds-instance": recipe(
            "crossplane/rds-instance.yaml.tmpl", engine="postgres", engine_version="16.9",
            multi_az="true", deletion_protection="true", skip_final_snapshot="false",
            management_policies="[Observe, Create, Update, LateInitialize]",
            final_snapshot_identifier_line="    finalSnapshotIdentifier: my-app-prod-us-final",
        ),
        "rds-password-generator": recipe("crossplane/rds-password-generator.yaml.tmpl"),
        "rds-password-external-secret": recipe("crossplane/rds-password-external-secret.yaml.tmpl"),
        "rds-password-push-secret": recipe("crossplane/rds-password-push-secret.yaml.tmpl", env="prod"),
        "rds-conn-push-secret": recipe("crossplane/rds-conn-push-secret.yaml.tmpl", env="prod"),
        "db-external-secret": recipe(
            "k8s/db-external-secret.yaml.tmpl",
            vault_remote_key="prod/rds/application/my-app/database",
        ),
    }
    if first_migration:
        # The bootstrap revision already delivers the complete migration contract.
        resources["db-external-secret"]["spec"]["target"]["template"] = {
            "engineVersion": "v2",
            "data": {
                key: "{{ ." + key + " }}" for key in (
                    "DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD"
                )
            } | {"DB_NAME": "events"},
        }
    kustomization = recipe("k8s/kustomization-overlay.yaml.tmpl")
    for name, doc in resources.items():
        write(overlay / f"{name}.yaml", doc)
        kustomization["resources"].append(f"{name}.yaml")
    write(overlay / "kustomization.yaml", kustomization)
    rendered = build(overlay)
    assert not any(d["kind"] in {"Secret", "DataSource", "Job"} for d in rendered)
    by_name = {d["metadata"]["name"]: d for d in rendered}
    password = by_name["my-app-rds-password"]
    instance = by_name["my-app-prod-us-db"]
    connection = by_name["my-app-rds-push"]
    consumer = by_name["my-app-db-secret"]
    wave = lambda d: int(d["metadata"].get("annotations", {}).get("argocd.argoproj.io/sync-wave", 0))
    assert wave(by_name["my-app-rds-password-gen"]) < wave(password) < wave(instance)
    assert wave(connection) < wave(consumer)
    assert wave(consumer) < wave(by_name["my-app"])
    assert wave(by_name["my-app"]) == workload_wave
    assert instance["spec"]["forProvider"]["passwordSecretRef"]["name"] == password["spec"]["target"]["name"]
    assert instance["spec"]["writeConnectionSecretToRef"]["name"] == connection["spec"]["selector"]["secret"]["name"]
    assert {entry["match"]["remoteRef"]["remoteKey"] for entry in connection["spec"]["data"]} == {
        entry["remoteRef"]["key"] for entry in consumer["spec"]["data"]
    }
    render_dir = tmp_path / "rendered"
    for index, doc in enumerate(rendered):
        write(render_dir / f"{index}.yaml", doc)
    result = run_validator("check_db_resource_contracts.py", render_dir)
    assert result.returncode == 0, result.stdout
    consumption = load_validator_module("check_workload_secret.py")
    assert consumption.check(
        rendered, "prod-my-app", "Rollout", "my-app", "my-app", "my-app-db-secret",
        ["DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD"],
    ) == []
    old_order = copy.deepcopy(rendered)
    old_rollout = next(d for d in old_order if d["kind"] == "Rollout")
    old_rollout["metadata"]["annotations"]["argocd.argoproj.io/sync-wave"] = "0"
    assert any("sync-wave" in error for error in consumption.check(
        old_order, "prod-my-app", "Rollout", "my-app", "my-app", "my-app-db-secret",
        ["DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD"],
    ))
    disconnected = copy.deepcopy(rendered)
    rollout = next(d for d in disconnected if d["kind"] == "Rollout")
    rollout["spec"]["template"]["spec"]["containers"][0].pop("envFrom")
    assert consumption.check(
        disconnected, "prod-my-app", "Rollout", "my-app", "my-app", "my-app-db-secret",
        ["DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD"],
    )
    application = recipe("argocd/application.yaml.tmpl")
    validator = load_validator_module("check_argocd_application.py")
    assert validator.application_failures(tmp_path / "app.yaml", tmp_path, application) == []
    if first_migration:
        # First sync uses the SAME overlay/Application but cannot start the app
        # that requires the absent schema, nor a PreSync that needs its new Secret.
        bootstrap = copy.deepcopy(kustomization)
        bootstrap["resources"].remove("../../base")
        bootstrap.pop("patches", None)
        write(overlay / "kustomization.yaml", bootstrap)
        initial = build(overlay)
        assert not any(d["kind"] in {
            "Rollout", "Deployment", "StatefulSet", "Service", "Ingress", "Job"
        } for d in initial)
        initial_consumer = next(d for d in initial if d["metadata"]["name"] == "my-app-db-secret")
        assert set(initial_consumer["spec"]["target"]["template"]["data"]) == {
            "DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD", "DB_NAME"
        }
        assert initial_consumer["spec"]["target"]["template"]["data"]["DB_NAME"] == "events"
        # After operation Succeeded and those keys are Ready (a live gate), the
        # second MR adds the hook and runtime resources; the Secret source is identical.
        migration = recipe(
            "db-migration/presync-hook.yaml.tmpl", service_account_name="default",
            envfrom_block="            - secretRef:\n                name: my-app-db-secret",
        )
        write(overlay / "migration-job.yaml", migration)
        activation = copy.deepcopy(kustomization)
        activation["resources"].append("migration-job.yaml")
        write(overlay / "kustomization.yaml", activation)
        active = build(overlay)
        assert next(d for d in active if d["metadata"]["name"] == "my-app-db-secret") == initial_consumer
        assert next(d for d in active if d["kind"] == "Job")["metadata"]["annotations"]["argocd.argoproj.io/hook"] == "PreSync"
        assert {d["kind"] for d in active} >= {"Rollout", "Service", "Job"}
        assert consumption.check(
            active, "prod-my-app", "Rollout", "my-app", "my-app", "my-app-db-secret",
            ["DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD", "DB_NAME"],
        ) == []


def test_runtime_followup_consumers_use_the_bootstrapped_db_secret() -> None:
    """A separate revision can add PreSync and NineData without regenerating credentials."""
    migration = recipe(
        "db-migration/presync-hook.yaml.tmpl",
        service_account_name="default",
        envfrom_block="            - secretRef:\n                name: my-app-db-secret",
    )
    assert migration["metadata"]["annotations"]["argocd.argoproj.io/hook"] == "PreSync"
    assert migration["spec"]["template"]["spec"]["containers"][0]["envFrom"] == [
        {"secretRef": {"name": "my-app-db-secret"}}
    ]
    datasource = recipe(
        "crossplane/ninedata-datasource.yaml.tmpl", env="prod", env_id="env-product",
        db_conn_secret="my-app-rds-conn", datasource_type="PostgreSQL",
        management_policies_block="  managementPolicies: [Observe, Create, Update, LateInitialize]",
    )
    assert "Delete" not in datasource["spec"]["managementPolicies"]
    refs = datasource["spec"]["forProvider"]
    assert {refs[key]["key"] for key in (
        "hostFromSecretRef", "portFromSecretRef", "usernameFromSecretRef", "passwordSecretRef"
    )} == {"host", "port", "username", "password"}
    for key in ("hostFromSecretRef", "portFromSecretRef", "usernameFromSecretRef", "passwordSecretRef"):
        assert refs[key]["name"] == "my-app-rds-conn"
        assert refs[key]["namespace"] == "prod-my-app"


def test_approved_pinned_clickhouse_application_uses_overlay_version(
    tmp_path: Path,
) -> None:
    base = tmp_path / "k8s/base"
    overlay = tmp_path / "k8s/overlays/prod-us"
    image = "harbor-30257-us-prod.addx.live/base/clickhouse/clickhouse-server"
    for name in ("statefulset", "kustomization"):
        write(base / f"{name}.yaml", recipe(f"clickhouse/{name}.yaml.tmpl"))
    # ClickHouse emits both headless and client Services in one recipe.
    (base / "service.yaml").write_text(render_recipe_template(
        RECIPE_ROOT / "clickhouse/service.yaml.tmpl",
        strip_full_line_comments=True, overrides=PROD,
    ), encoding="utf-8")
    for name in ("password-generator", "password-external-secret"):
        write(overlay / f"clickhouse-{name}.yaml", recipe(f"clickhouse/{name}.yaml.tmpl"))
    write(overlay / "kustomization.yaml", recipe(
        "clickhouse/kustomization-overlay.yaml.tmpl", harbor_image_path=image, image_tag="25.8.23.13",
    ))
    statefulset = next(d for d in build(overlay) if d["kind"] == "StatefulSet")
    assert statefulset["spec"]["template"]["spec"]["containers"][0]["image"] == image + ":25.8.23.13"
    application = recipe("argocd/application.yaml.tmpl")
    application["metadata"]["annotations"] = {
        key: value for key, value in application["metadata"]["annotations"].items()
        if not key.startswith("argocd-image-updater.argoproj.io/")
    }
    application["spec"]["source"].pop("kustomize")
    validator = load_validator_module("check_argocd_application.py")
    assert validator.application_failures(tmp_path / "app.yaml", tmp_path, application) == []
    # The pinned exception must not bypass the core notification/finalizer contract.
    invalid = copy.deepcopy(application)
    invalid["metadata"]["finalizers"] = []
    assert validator.application_failures(tmp_path / "app.yaml", tmp_path, invalid)


@pytest.mark.parametrize("seed", ["25.8.23.13", "latest"])
def test_image_updater_branch_still_rejects_non_sha_seeds(seed: str, tmp_path: Path) -> None:
    application = recipe("argocd/application.yaml.tmpl", image_seed=seed)
    validator = load_validator_module("check_argocd_application.py")
    assert validator.application_failures(tmp_path / "app.yaml", tmp_path, application)
