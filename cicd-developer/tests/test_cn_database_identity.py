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


KINDS = ("sql-only", "publication", "purpose", "purpose-publication")


def revision(publication=False, kind=None):
    kind = kind or ("publication" if publication else "sql-only")
    number = KINDS.index(kind) + 1
    spec = json.loads((FIXTURES / f"{kind}.json").read_text())["spec"]
    return {
        "apiVersion": "apiextensions.crossplane.io/v1",
        "kind": "CompositionRevision",
        "metadata": {
            "name": COMPOSITION + f"-test{number}",
            "uid": "synthetic-revision-for-parser-tests",
            "labels": {"crossplane.io/composition-name": COMPOSITION},
        },
        "spec": {**spec, "revision": number},
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


def purpose_claim(kind, purpose="spicedb", app="micro_app_platform", publish=False):
    value = claim()
    value["metadata"] = {
        "name": f"{app.replace('_', '-')}-postgres-{purpose.replace('_', '-')}",
        "namespace": "staging-" + app.replace("_", "-"),
    }
    value["spec"]["app"] = app
    value["spec"]["purpose"] = purpose
    value["spec"]["compositionRevisionRef"] = {"name": revision(kind=kind)["metadata"]["name"]}
    if publish:
        value["spec"]["publishCredentials"] = True
    return value


def all_revisions():
    return revisions(*(revision(kind=kind) for kind in KINDS))


@pytest.mark.parametrize("kind", ["sql-only", "publication"])
def test_pre_purpose_revisions_reject_purpose(tmp_path, kind):
    result = run(tmp_path, [purpose_claim(kind)], evidence=all_revisions())
    assert result.returncode == 1
    assert "does not support purpose" in result.stdout


@pytest.mark.parametrize("kind", ["purpose", "purpose-publication"])
def test_purpose_revisions_accept_purpose_identity(tmp_path, kind):
    result = run(tmp_path, [purpose_claim(kind)], evidence=all_revisions())
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("kind,ok", [("purpose", False), ("purpose-publication", True)])
def test_purpose_publication_needs_the_later_revision(tmp_path, kind, ok):
    result = run(tmp_path, [purpose_claim(kind, publish=True)], evidence=all_revisions())
    assert (result.returncode == 0) is ok, result.stdout + result.stderr
    if not ok:
        assert "publication for a purpose identity" in result.stdout


@pytest.mark.parametrize(
    "kind,ok", [("publication", False), ("purpose", False), ("purpose-publication", True)]
)
def test_underscore_app_publication_only_on_later_revision(tmp_path, kind, ok):
    value = claim()
    value["metadata"].pop("annotations")
    value["metadata"]["namespace"] = "staging-billing-api"
    value["spec"]["app"] = "billing_api"
    value["spec"]["publishCredentials"] = True
    value["spec"]["compositionRevisionRef"] = {"name": revision(kind=kind)["metadata"]["name"]}
    result = run(tmp_path, [value], evidence=all_revisions())
    assert (result.returncode == 0) is ok, result.stdout + result.stderr
    if not ok:
        assert "alphanumeric app" in result.stdout


@pytest.mark.parametrize("purpose", ["a", "a__b", "ab_", "Audit", "1st", "x" * 21, 7])
def test_noncanonical_purpose_is_rejected(tmp_path, purpose):
    value = purpose_claim("purpose-publication")
    value["spec"]["purpose"] = purpose
    value["metadata"]["name"] = "micro-app-platform-postgres-x"
    result = run(tmp_path, [value], evidence=all_revisions())
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Traceback" not in result.stderr


def test_longest_app_and_purpose_stay_within_identity_bound(tmp_path):
    # app <= 31 and purpose <= 20 keep app__purpose at <= 53 characters.
    value = purpose_claim("purpose-publication", purpose="p" * 20, app="a" * 31)
    value["metadata"]["name"] = "long-postgres"
    result = run(tmp_path, [value], evidence=all_revisions())
    assert result.returncode == 0, result.stdout + result.stderr


def test_purpose_and_primary_coexist_but_duplicate_purpose_collides(tmp_path):
    primary = claim()
    primary["metadata"] = {"name": "micro-app-platform-postgres", "namespace": "staging-micro-app-platform"}
    primary["spec"]["app"] = "micro_app_platform"
    primary["spec"]["compositionRevisionRef"] = {"name": revision(kind="purpose-publication")["metadata"]["name"]}
    result = run(tmp_path, [primary, purpose_claim("purpose-publication")], evidence=all_revisions())
    assert result.returncode == 0, result.stdout + result.stderr
    duplicate = purpose_claim("purpose-publication")
    duplicate["metadata"]["name"] = "other-claim"
    (tmp_path / "dup").mkdir()
    result = run(tmp_path / "dup", [purpose_claim("purpose-publication"), duplicate], evidence=all_revisions())
    assert result.returncode == 1
    for expected in [
        "postgres.Role shared-postgres/micro_app_platform__spicedb",
        "crossplane-system/cn-staging-micro-app-platform--spicedb-postgres-db-cred",
        "shared-postgres-readers/micro-app-platform--spicedb",
        "staging/rds/application/micro-app-platform--spicedb/postgres",
    ]:
        assert expected in result.stdout


def test_adopting_purpose_publication_revision_keeps_producers(tmp_path):
    result = run(
        tmp_path,
        [purpose_claim("purpose-publication", publish=True)],
        evidence=all_revisions(),
        live=[purpose_claim("purpose")],
    )
    assert result.returncode == 0, result.stdout + result.stderr
