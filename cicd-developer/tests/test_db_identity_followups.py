"""Regression cases for shared Database producer isolation and inventory gates."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


SKILL_ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = SKILL_ROOT / "validators/check_db_resource_contracts.py"


def claim(app="billing_api", engine="postgres", *, purpose=None, name=None):
    slug = app.replace("_", "-")
    suffix = f"-{purpose.replace('_', '-')}" if purpose else ""
    value = {
        "apiVersion": "platform.addx.io/v1alpha1",
        "kind": "Database",
        "metadata": {"name": name or f"{slug}-{engine}{suffix}",
                     "namespace": f"staging-{slug}"},
        "spec": {"app": app, "engine": engine, "env": "staging"},
    }
    if purpose:
        value["spec"]["purpose"] = purpose
    return value


def additional(name):
    return {"name": name, "grants": [{"table": "events", "privileges": ["SELECT"]}]}


def write_claims(directory, *documents, filename="database.yaml"):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / filename).write_text(yaml.safe_dump_all(documents), encoding="utf-8")


def snapshot(*documents):
    return {"apiVersion": "v1", "kind": "List", "metadata": {"resourceVersion": "12345"},
            "items": list(documents)}


def run_check(directory, *, inventory=None, inventory_text=None):
    args = [sys.executable, str(VALIDATOR), str(directory)]
    if inventory is not None or inventory_text is not None:
        path = directory.parent / "inventory.json"
        path.write_text(inventory_text if inventory_text is not None else json.dumps(inventory), encoding="utf-8")
        args.extend(["--inventory", str(path)])
    return subprocess.run(args, capture_output=True, text=True, check=False)


def test_cross_owner_postgres_purpose_collides_in_same_target(tmp_path):
    write_claims(tmp_path, claim(purpose="audit"), filename="secondary.yaml")
    write_claims(tmp_path, claim("billing_api_audit"), filename="primary.yaml")
    result = run_check(tmp_path)
    assert result.returncode == 1, result.stdout + result.stderr
    for expected in ["producer identity collision", "postgres.Role", "postgres.Database",
                     "crossplane-system/billing-api-audit-postgres-db-cred",
                     "crossplane-system/billing-api-audit-postgres-push"]:
        assert expected in result.stdout


@pytest.mark.parametrize("reserved", ["database", "postgres", "postgres_audit"])
def test_additional_mysql_reserved_credential_keys_fail_without_inventory(tmp_path, reserved):
    mysql = claim(engine="mysql")
    mysql["spec"]["additionalUsers"] = [additional(reserved)]
    write_claims(tmp_path, mysql)
    result = run_check(tmp_path)
    assert result.returncode == 1
    assert "is reserved" in result.stdout


def test_cross_owner_additional_mysql_user_collides_with_primary(tmp_path):
    mysql = claim(engine="mysql")
    mysql["spec"]["additionalUsers"] = [additional("audit")]
    write_claims(tmp_path, mysql, claim("billing_api_audit", "mysql"))
    result = run_check(tmp_path)
    assert result.returncode == 1
    assert "mysql.User shared-mysql/billing_api_audit" in result.stdout
    assert "Secret crossplane-system/billing-api-audit-db-cred" in result.stdout


def test_additional_mysql_user_can_collide_with_postgres_purpose_source_secret(tmp_path):
    mysql = claim(engine="mysql")
    mysql["spec"]["additionalUsers"] = [additional("audit_postgres")]
    write_claims(tmp_path, mysql, claim(purpose="audit"))
    result = run_check(tmp_path)
    assert result.returncode == 1
    assert "Secret crossplane-system/billing-api-audit-postgres-db-cred" in result.stdout


def test_compatible_engines_purposes_additional_users_and_legacy_identity(tmp_path):
    mysql = claim(engine="mysql")
    mysql["spec"]["additionalUsers"] = [additional("flink_cdc")]
    write_claims(tmp_path, mysql, claim(), claim(purpose="audit"),
                 claim(purpose="events"), claim(engine="redis"),
                 claim("legacybilling", "mysql", name="old-billing-database"))
    result = run_check(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "cross-repository inventory not checked" in result.stdout


def test_same_owner_in_separate_overlays_does_not_imply_same_cluster(tmp_path):
    write_claims(tmp_path / "deploy/overlays/prod-us", claim())
    write_claims(tmp_path / "deploy/overlays/prod-eu", claim())
    result = run_check(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


def test_inventory_mode_rejects_multiple_candidate_copies_even_if_identical(tmp_path):
    write_claims(tmp_path / "a", claim())
    write_claims(tmp_path / "b", claim())
    result = run_check(tmp_path, inventory=snapshot(claim()))
    assert result.returncode == 1
    assert "duplicate candidate" in result.stdout


def test_existing_other_repository_claim_blocks_candidate_collision(tmp_path):
    write_claims(tmp_path, claim(purpose="audit"))
    result = run_check(tmp_path, inventory=snapshot(claim("billing_api_audit")))
    assert result.returncode == 1
    assert "producer identity collision" in result.stdout


def test_inventory_comparison_spans_candidate_subdirectories(tmp_path):
    write_claims(tmp_path / "one", claim(purpose="audit"))
    write_claims(tmp_path / "two", claim("billing_api_audit"))
    result = run_check(tmp_path, inventory=snapshot())
    assert result.returncode == 1
    assert "producer identity collision" in result.stdout


def test_inventory_allows_same_claim_non_identity_update_and_additional_user(tmp_path):
    live = claim(engine="mysql")
    live["spec"]["additionalUsers"] = [additional("flink_cdc")]
    candidate = copy.deepcopy(live)
    candidate["metadata"]["labels"] = {"team": "payments"}
    candidate["spec"]["additionalUsers"][0]["grants"][0]["privileges"] = ["SELECT", "INSERT"]
    candidate["spec"]["additionalUsers"].append(additional("auditor"))
    write_claims(tmp_path, candidate)
    result = run_check(tmp_path, inventory=snapshot(live))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "supplied inventory" in result.stdout


@pytest.mark.parametrize("change", ["app", "env", "engine", "purpose", "remove_user", "rename_user"])
def test_inventory_rejects_identity_changing_replacement(tmp_path, change):
    live = claim(engine="mysql" if change.endswith("user") else "postgres")
    if change.endswith("user"):
        live["spec"]["additionalUsers"] = [additional("flink_cdc")]
    candidate = copy.deepcopy(live)
    if change == "app":
        candidate["spec"]["app"] = "renamed_app"
    elif change == "env":
        candidate["spec"]["env"] = "prod"
    elif change == "engine":
        candidate["spec"]["engine"] = "mysql"
    elif change == "purpose":
        candidate["spec"]["purpose"] = "audit"
    elif change == "remove_user":
        del candidate["spec"]["additionalUsers"]
    else:
        candidate["spec"]["additionalUsers"][0]["name"] = "auditor"
    write_claims(tmp_path, candidate)
    result = run_check(tmp_path, inventory=snapshot(live))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "operator-reviewed migration" in result.stdout


@pytest.mark.parametrize("mutation", ["missing_items", "missing_metadata", "missing_version",
    "paginated", "remaining", "null_continue", "wrong_kind", "wrong_api", "wrong_item", "wrong_items_type"])
def test_inventory_rejects_incomplete_or_malformed_list(tmp_path, mutation):
    write_claims(tmp_path, claim())
    data = snapshot()
    if mutation == "missing_items":
        del data["items"]
    elif mutation == "missing_metadata":
        del data["metadata"]
    elif mutation == "missing_version":
        data["metadata"] = {}
    elif mutation == "paginated":
        data["metadata"]["continue"] = "more"
    elif mutation == "remaining":
        data["metadata"]["remainingItemCount"] = 1
    elif mutation == "null_continue":
        data["metadata"]["continue"] = None
    elif mutation == "wrong_kind":
        data["kind"] = "SecretList"
    elif mutation == "wrong_api":
        data["apiVersion"] = "unknown/v1"
    elif mutation == "wrong_item":
        data["items"] = [{"kind": "Secret", "apiVersion": "v1"}]
    else:
        data["items"] = {}
    result = run_check(tmp_path, inventory=data)
    assert result.returncode == 2
    assert "invalid Database inventory" in result.stdout
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("mutation", ["missing_namespace", "mapping_namespace", "bad_env", "bad_engine", "unknown_composition"])
def test_inventory_rejects_unprojectable_claim(tmp_path, mutation):
    live = claim("existing_app")
    if mutation == "missing_namespace":
        del live["metadata"]["namespace"]
    elif mutation == "mapping_namespace":
        live["metadata"]["namespace"] = {"invalid": True}
    elif mutation == "bad_env":
        live["spec"]["env"] = "unknown"
    elif mutation == "bad_engine":
        live["spec"]["engine"] = "oracle"
    else:
        live["spec"]["compositionRef"] = {"name": "unrecognized"}
    write_claims(tmp_path, claim())
    result = run_check(tmp_path, inventory=snapshot(live))
    assert result.returncode == 1
    assert "Traceback" not in result.stderr


def test_inventory_rejects_duplicate_claims(tmp_path):
    write_claims(tmp_path, claim())
    result = run_check(tmp_path, inventory=snapshot(claim(), claim()))
    assert result.returncode == 1
    assert "duplicate Database claim" in result.stdout


@pytest.mark.parametrize("where", ["candidate", "inventory"])
@pytest.mark.parametrize("selection", ["manual", "selector", "unknown_policy", "wrong_ref"])
def test_unverified_composition_selection_cannot_use_current_identity_projection(tmp_path, where, selection):
    primary = claim()
    secondary = claim(purpose="audit")
    if selection == "manual":
        secondary['spec'].update(compositionRevisionRef={'name': 'database.platform.addx.io-legacy'},
                                 compositionUpdatePolicy='Manual')
    elif selection == "selector":
        secondary['spec']['compositionRevisionSelector'] = {'matchLabels': {'legacy': 'true'}}
    elif selection == "unknown_policy":
        secondary['spec']['compositionUpdatePolicy'] = 'unknown'
    else:
        secondary['spec']['compositionRevisionRef'] = {'name': 'custom-composition-old'}
    write_claims(tmp_path, secondary if where == 'candidate' else primary)
    result = run_check(tmp_path, inventory=snapshot(primary if where == 'candidate' else secondary))
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'Composition' in result.stdout or 'composition' in result.stdout


def test_automatic_controller_revision_ref_preserves_compatible_claim(tmp_path):
    existing = claim()
    existing['spec'].update(compositionRevisionRef={'name': 'database.platform.addx.io-abc123'},
                            compositionUpdatePolicy='Automatic')
    write_claims(tmp_path, claim(purpose='audit'))
    result = run_check(tmp_path, inventory=snapshot(existing))
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'effective Composition contract' in result.stdout


def test_gcp_logical_database_recipe_is_not_an_aws_shared_producer(tmp_path):
    gcp = claim(engine='postgres')
    del gcp['spec']['env']
    gcp['spec']['instanceRef'] = {'name': 'gcp-instance'}
    gcp['spec']['databaseName'] = 'billing_api'
    write_claims(tmp_path, gcp)
    assert run_check(tmp_path).returncode == 0
    # A caller explicitly claiming an AWS target inventory must not skip it.
    assert run_check(tmp_path, inventory=snapshot()).returncode == 1


def test_instance_ref_cannot_hide_an_aws_shared_identity_collision(tmp_path):
    candidate = claim(purpose='audit')
    candidate['spec']['instanceRef'] = {'name': 'gcp-instance'}
    write_claims(tmp_path, candidate, claim('billing_api_audit'))
    assert run_check(tmp_path).returncode == 1


def test_inventory_rejects_duplicate_json_keys(tmp_path):
    write_claims(tmp_path, claim())
    result = run_check(tmp_path, inventory_text='{"items": [], "items": []}')
    assert result.returncode == 2
    assert "duplicate JSON key" in result.stdout


def test_invalid_inventory_never_echoes_payload(tmp_path):
    write_claims(tmp_path, claim())
    result = run_check(tmp_path, inventory_text='{"unexpected": "DO_NOT_ECHO_PAYLOAD"')
    assert result.returncode == 2
    assert "DO_NOT_ECHO_PAYLOAD" not in result.stdout + result.stderr


def test_inventory_fifo_is_rejected_without_waiting_for_writer(tmp_path):
    write_claims(tmp_path, claim())
    fifo = tmp_path / "inventory.fifo"
    os.mkfifo(fifo)
    result = subprocess.run([sys.executable, str(VALIDATOR), str(tmp_path),
                             "--inventory", str(fifo)], capture_output=True,
                            text=True, timeout=3, check=False)
    assert result.returncode == 2
    assert "regular file" in result.stdout


def test_wrapper_explicit_empty_inventory_cannot_fall_back_to_candidate_scan(tmp_path):
    write_claims(tmp_path, claim())
    command = ["bash", str(SKILL_ROOT / "validators/validate.sh"), "--repo-context", "app"]
    candidate = subprocess.run(command + [str(tmp_path)], capture_output=True, text=True, check=False)
    assert candidate.returncode == 0, candidate.stdout + candidate.stderr
    assert "cross-repository inventory not checked" in candidate.stdout

    inventory_gate = subprocess.run(
        command + ["--database-inventory", "", str(tmp_path)],
        capture_output=True, text=True, check=False,
    )
    assert inventory_gate.returncode == 2, inventory_gate.stdout + inventory_gate.stderr
    assert "--database-inventory requires a non-empty path" in inventory_gate.stderr
    assert "PASS: all validators succeeded" not in inventory_gate.stdout


def test_candidate_rejects_duplicate_yaml_keys(tmp_path):
    write_claims(tmp_path, claim())
    path = tmp_path / "database.yaml"
    path.write_text(path.read_text() + "  app: shadow_app\n", encoding="utf-8")
    result = run_check(tmp_path)
    assert result.returncode == 1
    assert "duplicate mapping key" in result.stdout


def test_rendered_kubernetes_list_cannot_hide_collisions(tmp_path):
    write_claims(tmp_path, snapshot(claim(purpose="audit"), claim("billing_api_audit")))
    result = run_check(tmp_path)
    assert result.returncode == 1
    assert "producer identity collision" in result.stdout


def test_same_app_in_different_namespaces_is_still_same_producer(tmp_path):
    first, second = claim(), claim()
    second["metadata"]["namespace"] = "other-namespace"
    write_claims(tmp_path, first, second)
    result = run_check(tmp_path)
    assert result.returncode == 1
    assert "producer identity collision" in result.stdout


def test_shared_redis_read_only_connection_secret_is_not_a_collision(tmp_path):
    write_claims(tmp_path, claim("billing_api", "redis"), claim("order_api", "redis"))
    result = run_check(tmp_path, inventory=snapshot())
    assert result.returncode == 0, result.stdout + result.stderr
