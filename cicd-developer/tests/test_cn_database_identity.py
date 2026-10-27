"""CN Manual revisions require fixed native spec evidence, never a name-only escape."""

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

SKILL = Path(__file__).resolve().parents[1]
VALIDATOR = SKILL / "validators/check_db_resource_contracts.py"
COMPOSITION = "database.cn-staging.platform.addx.io"
FIXTURES = SKILL / "tests/fixtures/cn-postgres-identity"


def revision(publication=False):
    filename = "publication.json" if publication else "sql-only.json"
    spec = json.loads((FIXTURES / filename).read_text())["spec"]
    return {
        "apiVersion": "apiextensions.crossplane.io/v1",
        "kind": "CompositionRevision",
        "metadata": {
            "name": COMPOSITION + ("-test2" if publication else "-test1"),
            "uid": "synthetic-revision-for-parser-tests",
            "labels": {"crossplane.io/composition-name": COMPOSITION},
        },
        "spec": {**spec, "revision": 2 if publication else 1},
    }


def revisions(*items):
    return {
        "apiVersion": "apiextensions.crossplane.io/v1",
        "kind": "CompositionRevisionList",
        "metadata": {"resourceVersion": "synthetic-test-snapshot", "continue": ""},
        "items": list(items),
    }


def claim(publication=False):
    return {
        "apiVersion": "platform.addx.io/v1alpha1",
        "kind": "Database",
        "metadata": {
            "name": "mlflow-postgres",
            "namespace": "staging-mlflow",
            "annotations": {"platform.addx.io/app-slug": "mlflow"},
        },
        "spec": {
            "app": "mlflow",
            "env": "staging",
            "engine": "postgres",
            "compositionRef": {"name": COMPOSITION},
            "compositionUpdatePolicy": "Manual",
            "compositionRevisionRef": {
                "name": revision(publication)["metadata"]["name"]
            },
        },
    }


def run(tmp_path, candidate, *, evidence=None, live=(), inventory=True):
    manifests = tmp_path / "manifests"
    manifests.mkdir()
    (manifests / "candidate.yaml").write_text(yaml.safe_dump_all(candidate))
    args = [sys.executable, str(VALIDATOR), str(manifests)]
    if inventory:
        path = tmp_path / "databases.json"
        path.write_text(
            json.dumps(
                {
                    "apiVersion": "platform.addx.io/v1alpha1",
                    "kind": "DatabaseList",
                    "metadata": {"resourceVersion": "synthetic"},
                    "items": list(live),
                }
            )
        )
        args.extend(["--inventory", str(path)])
    if evidence is not None:
        path = tmp_path / "revisions.json"
        path.write_text(json.dumps(evidence))
        args.extend(["--composition-revisions", str(path)])
    return subprocess.run(args, capture_output=True, text=True, check=False)


@pytest.mark.parametrize("publication", [False, True])
def test_fixed_native_spec_supports_manual_and_exact_selected_revision(
    tmp_path, publication
):
    value = claim(publication)
    if publication:
        value["spec"]["publishCredentials"] = True
    result = run(tmp_path, [value], evidence=revisions(revision(publication)))
    assert result.returncode == 0, result.stdout + result.stderr


def test_no_revision_inventory_never_enables_manual_by_name(tmp_path):
    result = run(tmp_path, [claim()])
    assert result.returncode == 1
    assert "explicit verified native CompositionRevision" in result.stdout


def test_revision_inventory_requires_database_inventory(tmp_path):
    result = run(tmp_path, [claim()], evidence=revisions(revision()), inventory=False)
    assert result.returncode == 2
    assert "require the exact target Database inventory" in result.stdout


@pytest.mark.parametrize(
    "field,value",
    [
        ("env", "prod"),
        ("engine", "mysql"),
        ("purpose", "audit"),
        ("additionalUsers", []),
        ("instanceRef", {"name": "other"}),
        ("compositionUpdatePolicy", "Automatic"),
        ("compositionRevisionRef", {"name": COMPOSITION + "-unobserved"}),
        ("compositionRevisionRef", {"name": {"invalid": "mapping"}}),
        ("compositionRevisionSelector", {"matchLabels": {"x": "y"}}),
        ("compositionSelector", {"matchLabels": {"x": "y"}}),
        ("publishCredentials", "true"),
    ],
)
def test_invalid_claims_cannot_enter_native_projection(tmp_path, field, value):
    candidate = claim()
    candidate["spec"][field] = value
    result = run(tmp_path, [candidate], evidence=revisions(revision()))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Traceback" not in result.stderr


def test_sql_only_revision_cannot_claim_publication_support(tmp_path):
    candidate = claim()
    candidate["spec"]["publishCredentials"] = True
    result = run(tmp_path, [candidate], evidence=revisions(revision()))
    assert result.returncode == 1
    assert "does not support publication" in result.stdout


@pytest.mark.parametrize(
    "change", ["template", "function", "type", "mode", "extra_spec"]
)
def test_any_effective_spec_change_is_rejected_before_projection(tmp_path, change):
    observed = revision()
    if change == "template":
        observed["spec"]["pipeline"][0]["input"]["inline"]["template"] += (
            "\n# changed\n"
        )
    elif change == "function":
        observed["spec"]["pipeline"][0]["functionRef"]["name"] = "unreviewed"
    elif change == "type":
        observed["spec"]["compositeTypeRef"]["kind"] = "Other"
    elif change == "mode":
        observed["spec"]["mode"] = "Resources"
    else:
        observed["spec"]["writeConnectionSecretsToNamespace"] = "foreign"
    result = run(tmp_path, [claim()], evidence=revisions(observed))
    assert result.returncode == 2
    assert "unrecognized CompositionRevision spec" in result.stdout


@pytest.mark.parametrize(
    "change",
    [
        "pagination",
        "missing_rv",
        "wrong_kind",
        "wrong_uid",
        "wrong_label",
        "duplicate",
        "bad_revision",
    ],
)
def test_incomplete_or_unbound_native_evidence_is_invalid_input(tmp_path, change):
    observed = revisions(revision())
    item = observed["items"][0]
    if change == "pagination":
        observed["metadata"]["continue"] = "next"
    elif change == "missing_rv":
        del observed["metadata"]["resourceVersion"]
    elif change == "wrong_kind":
        observed["kind"] = "List"
    elif change == "wrong_uid":
        item["metadata"]["uid"] = ""
    elif change == "wrong_label":
        item["metadata"]["labels"]["crossplane.io/composition-name"] = "foreign"
    elif change == "duplicate":
        observed["items"].append(copy.deepcopy(item))
    else:
        item["spec"]["revision"] = True
    result = run(tmp_path, [claim()], evidence=observed)
    assert result.returncode == 2
    assert "Traceback" not in result.stderr


def test_same_sql_owner_conflicts_even_across_legacy_and_cn_credential_names(tmp_path):
    live = claim()
    live["metadata"]["name"] = "legacy-mlflow-postgres"
    for field in [
        "compositionRef",
        "compositionRevisionRef",
        "compositionUpdatePolicy",
    ]:
        del live["spec"][field]
    result = run(tmp_path, [claim()], evidence=revisions(revision()), live=[live])
    assert result.returncode == 1
    assert "postgres.Role shared-postgres/mlflow" in result.stdout
    assert "postgres.Database shared-postgres/mlflow" in result.stdout


def test_two_cn_claims_collide_on_real_prefixed_secret_and_store_identities(tmp_path):
    duplicate = claim()
    duplicate["metadata"]["name"] = "other-claim"
    result = run(tmp_path, [claim(), duplicate], evidence=revisions(revision()))
    assert result.returncode == 1
    for expected in [
        "crossplane-system/cn-staging-mlflow-postgres-db-cred",
        "cn-staging-mlflow-postgres-reader",
        "shared-postgres-readers/mlflow",
    ]:
        assert expected in result.stdout


def test_manual_revision_adoption_keeps_identity_but_app_rename_requires_migration(
    tmp_path,
):
    live = claim()
    candidate = claim(True)
    candidate["spec"]["app"] = "renamed"
    candidate["metadata"].pop("annotations")
    result = run(
        tmp_path,
        [candidate],
        evidence=revisions(revision(), revision(True)),
        live=[live],
    )
    assert result.returncode == 1


def test_fixed_revision_adoption_preserves_existing_producers(tmp_path):
    result = run(
        tmp_path,
        [claim(True)],
        evidence=revisions(revision(), revision(True)),
        live=[claim()],
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("publication_revision", [False, True])
@pytest.mark.parametrize("app", ["a__b", "ab_", "a_b_", "a_"])
def test_cn_rejects_noncanonical_app_even_without_annotation(
    tmp_path, publication_revision, app
):
    value = claim(publication_revision)
    value["metadata"].pop("annotations")
    value["metadata"]["namespace"] = "staging-" + app.replace("_", "-")
    value["spec"]["app"] = app
    result = run(tmp_path, [value], evidence=revisions(revision(publication_revision)))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "canonical snake_case app" in result.stdout


@pytest.mark.parametrize("publication_revision", [False, True])
def test_cn_accepts_canonical_snake_app_without_publication(
    tmp_path, publication_revision
):
    value = claim(publication_revision)
    value["metadata"].pop("annotations")
    value["metadata"]["namespace"] = "staging-billing-api"
    value["spec"]["app"] = "billing_api"
    result = run(tmp_path, [value], evidence=revisions(revision(publication_revision)))
    assert result.returncode == 0, result.stdout + result.stderr
