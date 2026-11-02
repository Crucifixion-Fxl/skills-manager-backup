"""Offline regressions for explicit affinity and Server-owned resource conditions."""

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


@pytest.mark.parametrize("value", [{"id": "same"}, ["same"], True])
def test_identical_invalid_topology_values_cannot_prove_affinity(value):
    resources = pool()
    for resource in resources:
        resource["belongToHost"] = value
    with pytest.raises(ValueError, match="无法核验"):
        from resource_affinity import validate_assignment
        validate_assignment(job()["constraints"], {
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
        CLI.validate_affinity_counts(request["constraints"], request["resources"])


def test_unconstrained_alias_multicount_in_affinity_group_is_rejected():
    request = job()
    request["resources"].append({"name": "fixture", "type": "FIXTURE", "count": 2})
    with pytest.raises(ValueError, match="count=1"):
        CLI.validate_affinity_counts(request["constraints"], request["resources"])


def test_run_local_with_feature_affinity_retained_after_client_setup(tmp_path, monkeypatch):
    args = args_for(tmp_path, monkeypatch, "# @firmware.affinity=belong_to_cabinet")
    args.client_root = str(tmp_path)
    monkeypatch.setattr(CLI, "_resolve_local_client_root", lambda *_: tmp_path)
    monkeypatch.setattr(CLI, "_validate_local_runtime", lambda *_: None)
    monkeypatch.setattr(CLI, "_local_client_snapshot", lambda *_: {"commit": "c" * 40})
    _, context, _ = CLI.build_local_client_command(args, pool())
    assert context["job"][0]["constraints"] == [constraint("belong_to_cabinet")]


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
def test_truncated_inventory_is_omitted_without_rejecting_execution(monkeypatch, capsys, connection):
    from types import SimpleNamespace
    auth = SimpleNamespace(authenticate=lambda: SimpleNamespace(email="tester@example.com"))
    monkeypatch.setattr(CLI, "DeviceCloudAuthClient", lambda _: auth)
    monkeypatch.setattr(CLI, "gql", lambda *_: {"phoneConnection": connection, "deviceConnection": {"edges": []}})
    resources, returned_auth, email = CLI._authenticate_and_fetch_available_resources("prod-cn")
    assert resources is None
    assert returned_auth is auth
    assert email == "tester@example.com"
    assert "截断" in capsys.readouterr().err


@pytest.mark.parametrize("phone_kind", ["physical", "virtual"])
def test_native_conditions_survive_snapshot_token_batch_and_server_payload(tmp_path, monkeypatch, phone_kind):
    from trigger_cloud_job import build_resource_requirements, create_job_batch, load_batch_jobs
    args = args_for(tmp_path, monkeypatch, extra=[
        "--resource-constraints", json.dumps([constraint()]),
    ])
    resources = job()["resources"]
    resources[0]["conditions"] = {
        "phone_kind": "$eq:" + phone_kind,
        "manufacturer": "$nin:unknown,other",
        "sdk_version": "$ge:31",
        "platform": "$in:Android, iOS",
    }
    args.resource_json = json.dumps(resources)
    _, context = CLI.build_trigger_command(args, [])
    assert context["job"][0]["resources"] == resources
    assert context["preview"]["resourceGroup"] == resources
    token = context["preview"]["confirmation"]["token"]
    changed = copy.deepcopy(context["preview"])
    changed["resourceGroups"][0]["resources"][0]["conditions"]["sdk_version"] = "$ge:32"
    assert CLI._confirmation_token(changed) != token
    batch_path = tmp_path / "batch.json"
    batch_path.write_text(json.dumps(context["job"]), encoding="utf-8")
    loaded = load_batch_jobs(str(batch_path))[0]
    captured = {}
    def create(_endpoint, _auth, _query, variables):
        captured.update(variables["in"])
        return {"createJob": {"success": True, "id": 34}}
    monkeypatch.setitem(create_job_batch.__globals__, "gql", create)
    assert create_job_batch("endpoint", object(), 12, {},
                            build_resource_requirements(loaded["resources"]),
                            "manual", 5, "tester@example.com",
                            constraints=loaded["constraints"]) == 34
    assert captured["constraints"] == [constraint()]
    assert captured["resourceRequirements"][0]["conditions"] == [
        {"key": "phone_kind", "operator": "$eq", "value": phone_kind},
        {"key": "manufacturer", "operator": "$nin", "value": "unknown,other"},
        {"key": "sdk_version", "operator": "$ge", "value": "31"},
        {"key": "platform", "operator": "$in", "value": "Android, iOS"},
    ]


def test_default_phone_kind_is_left_to_server(tmp_path, monkeypatch):
    args = args_for(tmp_path, monkeypatch)
    _, context = CLI.build_trigger_command(args, [])
    assert "phone_kind" not in context["job"][0]["resources"][0]["conditions"]


@pytest.mark.parametrize("value", [[], "platform=android", 1])
def test_malformed_resource_conditions_rejected(value):
    with pytest.raises(ValueError, match="conditions"):
        CLI._validate_resource_list([{"name": "phone", "type": "PHONE", "conditions": value}], False)


@pytest.mark.parametrize("resource_json", [
    "{broken", "{}", '[{"name":"","type":"PHONE"}]',
    '[{"name":"phone","type":"invalid type"}]',
    '[{"name":"phone","type":"PHONE"},{"name":"phone","type":"PHONE"}]',
])
def test_resource_structure_validation_remains_local(tmp_path, monkeypatch, resource_json):
    args = args_for(tmp_path, monkeypatch)
    args.resource_json = resource_json
    with pytest.raises(ValueError):
        CLI.build_trigger_command(args, [])


def test_resource_json_cannot_drop_scenario_phone_alias(tmp_path, monkeypatch):
    args = args_for(tmp_path, monkeypatch)
    args.resource_json = json.dumps([{"name": "camera", "type": "BATTERYCAM"}])
    with pytest.raises(ValueError, match="手机别名"):
        CLI.build_trigger_command(args, [])
