"""Offline regressions for topology-aware preflight and confirmation."""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "device_cloud.py"
SPEC = importlib.util.spec_from_file_location("affinity_cli_under_test", SCRIPT)
CLI = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = CLI
SPEC.loader.exec_module(CLI)


def constraint(key="belong_to_host", names=None):
    return {"type": "AFFINITY", "topologyKey": key, "resources": names or ["camera", "phone"]}


def pool(phone_host="A", camera_host="A", phone_cabinet="X", camera_cabinet="X"):
    return [
        {"type": "PHONE", "platform": "android", "available": True,
         "belongToHost": phone_host, "belongToCabinet": phone_cabinet},
        {"type": "BATTERYCAM", "deviceModel": "KF451", "available": True,
         "belongToHost": camera_host, "belongToCabinet": camera_cabinet},
    ]


def job(constraints=None):
    return {"name": "affinity", "resources": [
        {"name": "phone", "type": "PHONE", "conditions": {"platform": "$eq:android"}},
        {"name": "camera", "type": "BATTERYCAM", "conditions": {"device_model": "$eq:KF451"}},
    ], "constraints": constraints if constraints is not None else [constraint(), constraint("belong_to_cabinet")]}


def args_for(tmp_path, monkeypatch, metadata="", extra=()):
    path = tmp_path / "features" / "binding.feature"
    path.parent.mkdir(parents=True)
    path.write_text(f"""{metadata}
Feature: binding
  Background:
    Given 我有resources配置
      | phone | PHONE |
      | camera | BATTERYCAM |
  @uid=affinity-case
  Scenario: binding
    Then inspect camera
""", encoding="utf-8")
    monkeypatch.setattr(CLI, "_tests_snapshot", lambda *_: {
        "requestedRef": "main", "resolvedCommit": "a" * 40, "catalogDigest": "b" * 64,
    })
    return CLI.parser().parse_args([
        "run", "--uid", "affinity-case", "--cases-root", str(tmp_path),
        "--tests-repo", "https://example.invalid/firmware-tests.git",
        "--firmware-version", "1.21.40-d", "--plan-name", "offline-affinity",
        "--resource-json", json.dumps(job()["resources"]), "--dry-run", *extra,
    ])


def test_feature_affinity_and_explicit_constraints_reach_jobs_and_confirmation(tmp_path, monkeypatch):
    args = args_for(tmp_path, monkeypatch, "# @firmware.affinity=belong_to_cabinet", [
        "--resource-constraints", json.dumps([constraint()]),
    ])
    _, context = CLI.build_trigger_command(args, pool())
    constraints = context["job"][0]["constraints"]
    assert {c["topologyKey"] for c in constraints} == {"belong_to_host", "belong_to_cabinet"}
    assert context["preview"]["resourceGroups"][0]["constraints"] == constraints
    assert context["preview"]["execution"]["concurrency"] == 1
    before = context["preview"]["confirmation"]["token"]
    modified = copy.deepcopy(context["preview"])
    modified["resourceGroups"][0]["constraints"].pop()
    assert CLI._confirmation_token(modified) != before
    assert "belongToHost" not in json.dumps(context["preview"])
    assert "belongToCabinet" not in json.dumps(context["preview"])


@pytest.mark.parametrize("resources", [pool(camera_host="B"), pool(camera_cabinet="Y"), pool(camera_host=None)])
def test_cross_domain_and_missing_topology_rejected_before_creation(tmp_path, monkeypatch, resources):
    args = args_for(tmp_path, monkeypatch, extra=[
        "--resource-constraints", json.dumps(job()["constraints"]),
    ])
    monkeypatch.setattr(CLI, "_run_batch_with_reused_auth", lambda *_: pytest.fail("must not create"))
    with pytest.raises(ValueError, match="实时空闲资源"):
        CLI.build_trigger_command(args, resources)


def test_backtracks_to_complete_group_instead_of_keeping_first_wrong_host():
    wrong = pool(phone_host="wrong")[0]
    assert CLI._allocate_job(job(), [wrong, *pool()]) == [wrong]


def test_unrelated_model_capacity_does_not_invalidate_affinity_confirmation(tmp_path, monkeypatch):
    args = args_for(tmp_path, monkeypatch, extra=[
        "--resource-constraints", json.dumps(job()["constraints"]),
    ])
    _, initial = CLI.build_trigger_command(args, pool())
    other = {"type": "BATTERYCAM", "deviceModel": "unrelated-model", "available": True}
    _, live = CLI.build_trigger_command(args, [other, *reversed(pool())])
    assert (live["preview"]["execution"]["capacitySnapshot"]
            != initial["preview"]["execution"]["capacitySnapshot"])
    assert live["preview"]["confirmation"]["token"] == initial["preview"]["confirmation"]["token"]


@pytest.mark.parametrize("resources", [pool(camera_host="B"), pool(camera_cabinet="Y"), pool(camera_host=None)])
def test_confirmed_token_does_not_bypass_live_affinity_checks(tmp_path, monkeypatch, resources):
    args = args_for(tmp_path, monkeypatch, extra=[
        "--resource-constraints", json.dumps(job()["constraints"]),
    ])
    _, initial = CLI.build_trigger_command(args, pool())
    args.confirm = initial["preview"]["confirmation"]["token"]
    args.dry_run = False
    monkeypatch.setattr(CLI, "_authenticate_and_fetch_available_resources",
                        lambda _env: (resources, object(), "tester@example.com"))
    monkeypatch.setattr(CLI, "_run_batch_with_reused_auth",
                        lambda *_: pytest.fail("must not create a cross-domain Job"))
    with pytest.raises(ValueError, match="实时空闲资源"):
        CLI.run_test(args)


def test_multiple_constraints_must_hold_for_the_same_pair():
    first = pool(phone_host="A", camera_host="B")
    second = pool(phone_host="B", camera_host="A", phone_cabinet="Y", camera_cabinet="Y")
    assert CLI._allocate_job(job(), [*first, *second]) is None


def test_matching_type_and_model_still_required_with_affinity():
    resources = pool()
    resources[1]["deviceModel"] = "other-model"
    assert CLI._allocate_job(job(), resources) is None


@pytest.mark.parametrize(("expression", "expected"), [
    ("$eq:Android", "$eq:android"), ("$eq:ANDROID", "$eq:android"),
    ("$in:Android, iOS", "$in:android,ios"),
])
def test_platform_canonicalized_before_preview_and_create_payload(expression, expected):
    original = [{"name": "phone", "type": "PHONE", "conditions": {"platform": expression}}]
    normalized = CLI._validate_resource_list(original, False)
    assert normalized[0]["conditions"]["platform"] == expected
    assert original[0]["conditions"]["platform"] == expression
    from trigger_cloud_job import build_resource_requirements
    payload = build_resource_requirements(normalized)
    assert payload[0]["conditions"][0]["value"] == expected.partition(":")[2]
    assert CLI._matches_requirement({"type": "PHONE", "platform": "android"}, normalized[0])


def test_noncanonical_platform_is_not_falsely_accepted_by_capacity_matching():
    requirement = {"type": "PHONE", "conditions": {"platform": "$eq:Android"}}
    assert not CLI._matches_requirement({"type": "PHONE", "platform": "android"}, requirement)


def test_model_and_topology_matching_preserves_case_like_server_tags():
    requirement = {"type": "BATTERYCAM", "conditions": {
        "device_model": "$eq:kf451", "belong_to_host": "$eq:host-a",
    }}
    assert not CLI._matches_requirement({
        "type": "BATTERYCAM", "deviceModel": "KF451", "belongToHost": "host-a",
    }, requirement)
    requirement["conditions"]["device_model"] = "$eq:KF451"
    assert not CLI._matches_requirement({
        "type": "BATTERYCAM", "deviceModel": "KF451", "belongToHost": "HOST-A",
    }, requirement)


def test_concurrency_counts_disjoint_complete_groups():
    groups = [*pool(), *pool(phone_host="B", camera_host="B", phone_cabinet="Y", camera_cabinet="Y")]
    count, _ = CLI._planned_concurrency([job(), job(), job()], groups, 4)
    assert count == 2


@pytest.mark.parametrize("value", [None, "", {}, [], "unknown"])
def test_missing_or_invalid_topology_never_proves_assignment(value):
    resources = pool()
    resources[0]["belongToHost"] = value
    if value == "unknown":
        assert CLI._allocate_job(job(), resources) is None
    elif isinstance(value, (dict, list)):
        # Invalid values must not become topology IDs by stringification.
        with pytest.raises(ValueError):
            CLI.validate_assignment(job()["constraints"], {"phone": resources[0], "camera": resources[1]})
    else:
        assert CLI._allocate_job(job(), resources) is None


@pytest.mark.parametrize("value", [{"id": "same"}, ["same"], True])
def test_identical_invalid_topology_values_cannot_prove_affinity(value):
    resources = pool()
    for resource in resources:
        resource["belongToHost"] = value
    with pytest.raises(ValueError, match="无法核验"):
        CLI.validate_assignment(job()["constraints"], {
            "phone": resources[0], "camera": resources[1],
        })


@pytest.mark.parametrize("value", [
    {}, [constraint("unknown")], [{**constraint(), "type": "ANTI_AFFINITY"}],
    [constraint(names=["phone", "missing"])], [constraint(names=["phone", "phone"])],
    [{**constraint(), "ignored": True}],
])
def test_malformed_constraints_fail_closed(value):
    with pytest.raises(ValueError):
        CLI.normalize_constraints(value, ["phone", "camera"])


@pytest.mark.parametrize("metadata", [
    "# @firmware.affinity=unknown", "# @firmware.affinity=", "# @firmware.affinity = belong_to_host",
])
def test_invalid_feature_declaration_cannot_be_ignored(tmp_path, monkeypatch, metadata):
    args = args_for(tmp_path, monkeypatch, metadata)
    with pytest.raises(ValueError, match="affinity"):
        CLI.build_trigger_command(args, pool())


@pytest.mark.parametrize("count", [0, 2, -1, "1", True])
def test_unsupported_affinity_count_rejected(count):
    request = job()
    request["resources"][0]["count"] = count
    with pytest.raises(ValueError, match="count=1"):
        CLI._allocate_job(request, pool())


def test_unconstrained_alias_multicount_in_affinity_group_is_rejected():
    request = job()
    request["resources"].append({"name": "fixture", "type": "FIXTURE", "count": 2})
    with pytest.raises(ValueError, match="count=1"):
        CLI._allocate_job(request, pool())


def test_run_local_with_feature_affinity_rejected_before_client_setup(tmp_path, monkeypatch):
    args = args_for(tmp_path, monkeypatch, "# @firmware.affinity=belong_to_cabinet")
    monkeypatch.setattr(CLI, "_resolve_local_client_root", lambda *_: pytest.fail("must reject before client setup"))
    with pytest.raises(ValueError, match="亲和约束.*云端 run"):
        CLI.build_local_client_command(args, pool())


def test_fetch_keeps_topology_internal_and_does_not_fetch_connection_secrets(monkeypatch):
    monkeypatch.setattr(CLI, "gql", lambda *_: {
        "phoneConnection": {"edges": [{"node": {
            "resourceType": "PHONE", "online": True, "jobId": None,
            "deviceModel": "physical-model", "belongToHost": "A", "belongToCabinet": "X",
            "connection": {"platform": "ANDROID", "ip": "private"},
        }}], "pageInfo": {"hasNextPage": False}},
        "deviceConnection": {"edges": [], "pageInfo": {"hasNextPage": False}},
    })
    resources = CLI.fetch_available_resources("prod-cn", object())
    assert resources[0]["belongToHost"] == "A"
    assert resources[0]["belongToCabinet"] == "X"
    assert resources[0]["deviceModel"] == "physical-model"
    assert "private" not in json.dumps(resources)
    assert "ip" not in CLI.RESOURCE_CAPACITY_QUERY
    assert "port" not in CLI.RESOURCE_CAPACITY_QUERY


@pytest.mark.parametrize("connection", [{"edges": [], "pageInfo": {"hasNextPage": True}}, {"edges": [{}] * 1000}])
def test_truncated_capacity_refuses_preflight(monkeypatch, connection):
    monkeypatch.setattr(CLI, "gql", lambda *_: {"phoneConnection": connection, "deviceConnection": {"edges": []}})
    with pytest.raises(ValueError, match="截断"):
        CLI.fetch_available_resources("prod-cn", object())
