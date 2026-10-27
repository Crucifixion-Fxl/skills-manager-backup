"""Offline affinity creation, binding verification, and cleanup regressions."""

from __future__ import annotations

import importlib.util
import json
import sys
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "trigger_cloud_job.py"
SPEC = importlib.util.spec_from_file_location("device_cloud_trigger_affinity", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
TRIGGER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = TRIGGER
SPEC.loader.exec_module(TRIGGER)

JOB_ID = 77
HOST_CONSTRAINT = {
    "type": "AFFINITY", "topologyKey": "belong_to_host",
    "resources": ["phone", "camera"],
}
CABINET_CONSTRAINT = {
    "type": "AFFINITY", "topologyKey": "belong_to_cabinet",
    "resources": ["camera", "phone"],
}


@pytest.fixture(autouse=True)
def no_network():
    with (
        patch.object(
            TRIGGER.urllib.request, "urlopen",
            side_effect=AssertionError("all network must be mocked"),
        ),
        patch.object(
            TRIGGER, "DeviceCloudAuthClient",
            side_effect=AssertionError("tests must not authenticate"),
        ),
    ):
        yield


def job_definition(constraints=None):
    return {
        "name": "affinity-case", "tags": "@uid=affinity-case",
        "resources": [
            {"name": "phone", "type": "PHONE"},
            {"name": "camera", "type": "BATTERYCAM"},
        ],
        "constraints": deepcopy(constraints if constraints is not None else [HOST_CONSTRAINT]),
    }


def batch_args(tmp_path, job=None, *extra):
    batch_path = tmp_path / "batch.json"
    batch_path.write_text(json.dumps([job or job_definition()]), encoding="utf-8")
    return TRIGGER.parse_args([
        "--app-type", "kb-tests", "--batch", str(batch_path),
        "--plan-name", "affinity-plan", "--poll-interval", "0",
        "--queue-timeout", "10", "--cleanup-timeout", "0", *extra,
    ])


def bindings(*, phone=True, camera=True, camera_host="private-host-a",
             camera_cabinet="private-cabinet-a"):
    nodes = []
    if phone:
        nodes.append({
            "jobId": str(JOB_ID), "jobRequirementName": "phone",
            "belongToHost": "private-host-a", "belongToCabinet": "private-cabinet-a",
        })
    device_nodes = []
    if camera:
        device_nodes.append({
            "jobId": str(JOB_ID), "jobRequirementName": "camera",
            "belongToHost": camera_host, "belongToCabinet": camera_cabinet,
        })
    # Unrelated Jobs must not affect this Job's affinity decision.
    nodes.append({
        "jobId": "999", "jobRequirementName": "phone",
        "belongToHost": "private-other-host", "belongToCabinet": "private-other-cabinet",
    })
    return {
        "phoneConnection": {
            "edges": [{"node": node} for node in nodes],
            "pageInfo": {"hasNextPage": False},
        },
        "deviceConnection": {
            "edges": [{"node": node} for node in device_nodes],
            "pageInfo": {"hasNextPage": False},
        },
    }


class FakeCloud:
    def __init__(self, constraints=None, jobs=None, allocations=None):
        self.constraints = deepcopy(constraints if constraints is not None else [HOST_CONSTRAINT])
        self.jobs = list(jobs or [{"status": "QUEUED"}])
        self.allocations = list(allocations or [bindings()])
        self.cancelled = False
        self.events = []
        self.created_input = None
        self.affinity_queries = 0

    def gql(self, _endpoint, _auth, query, variables):
        if query == TRIGGER.CREATE_JOB:
            self.events.append("create")
            self.created_input = variables["in"]
            return {"createJob": {"id": str(JOB_ID), "success": True}}
        if query == TRIGGER.QUERY_JOB_CONSTRAINTS:
            self.events.append("constraints")
            assert variables == {"id": str(JOB_ID)}
            return {"job": {"constraints": self.constraints}}
        if query == TRIGGER.QUERY_JOB:
            self.events.append("job")
            if self.cancelled:
                return {"job": {"status": "ABORTED_BY_WEB"}}
            result = self.jobs[0]
            if len(self.jobs) > 1:
                self.jobs.pop(0)
            return {"job": deepcopy(result)}
        if query == TRIGGER.AFFINITY_BINDINGS_QUERY:
            self.events.append("affinity")
            self.affinity_queries += 1
            assert variables == {"first": 1000}
            result = self.allocations[0]
            if len(self.allocations) > 1:
                self.allocations.pop(0)
            return deepcopy(result)
        if query == TRIGGER.PLAN_JOBS_QUERY:
            self.events.append("discover")
            return {"testPlan": {"id": "123", "jobs": [{"id": str(JOB_ID)}]}}
        if query == TRIGGER.CANCEL_JOB:
            self.events.append("cancel")
            self.cancelled = True
            return {"cancelJob": True}
        if query == TRIGGER.RESOURCE_BINDINGS_QUERY:
            self.events.append("release")
            return {"phoneConnection": {"edges": []}, "deviceConnection": {"edges": []}}
        raise AssertionError(query)


def run_batch(args, cloud):
    with (
        patch.object(TRIGGER, "gql", side_effect=cloud.gql),
        patch.object(TRIGGER, "create_plan_once", return_value=("123", True)),
        patch.object(TRIGGER.time, "sleep") as sleep,
    ):
        result = TRIGGER.batch_mode(
            args, auth_client=object(), launched_by="tester@example.invalid"
        )
    return result, sleep


def result_json(capsys):
    output = capsys.readouterr().out
    results = [
        json.loads(line.removeprefix(TRIGGER.RESULT_JSON_PREFIX))
        for line in output.splitlines() if line.startswith(TRIGGER.RESULT_JSON_PREFIX)
    ]
    assert results
    assert "private-host" not in output
    assert "private-cabinet" not in output
    assert "private-other" not in output
    return results[-1]


def test_create_payload_transmits_normalized_top_level_constraints():
    resources = TRIGGER.build_resource_requirements(job_definition()["resources"])
    with patch.object(
        TRIGGER, "gql", return_value={"createJob": {"success": True, "id": "77"}}
    ) as gql:
        assert TRIGGER.create_job_batch(
            "https://example.invalid/graphql", object(), "123", {}, resources,
            "manual", 5, "tester@example.invalid", constraints=[HOST_CONSTRAINT],
        ) == JOB_ID
    payload = gql.call_args.args[3]["in"]
    assert payload["constraints"] == [{
        **HOST_CONSTRAINT, "resources": ["camera", "phone"],
    }]
    assert "constraints" not in payload["metadata"]


def test_create_without_constraints_preserves_old_payload():
    with patch.object(
        TRIGGER, "gql", return_value={"createJob": {"success": True, "id": "77"}}
    ) as gql:
        TRIGGER.create_job_batch(
            "https://example.invalid/graphql", object(), "123", {},
            [{"name": "phone", "resourceType": "PHONE"}],
            "manual", 5, "tester@example.invalid",
        )
    assert "constraints" not in gql.call_args.args[3]["in"]


@pytest.mark.parametrize("constraint", [
    "AFFINITY", {}, {**HOST_CONSTRAINT, "type": "ANTI_AFFINITY"},
    {**HOST_CONSTRAINT, "topologyKey": "room"},
    {**HOST_CONSTRAINT, "resources": ["phone", "missing"]},
    {**HOST_CONSTRAINT, "resources": ["phone", "phone"]},
    {**HOST_CONSTRAINT, "resources": ["phone"]},
    {**HOST_CONSTRAINT, "unknown": True},
])
def test_invalid_constraints_fail_before_plan_creation(tmp_path, constraint):
    args = batch_args(tmp_path, job_definition([constraint]))
    with patch.object(TRIGGER, "create_plan_once") as create:
        with pytest.raises(SystemExit, match="constraints 无效"):
            TRIGGER.batch_mode(args, auth_client=object(), launched_by="tester@example.invalid")
    create.assert_not_called()


@pytest.mark.parametrize("count", [0, -1, 2, 1.0, "1", True, None])
def test_affinity_count_must_be_exactly_one_before_plan_creation(tmp_path, count):
    job = job_definition()
    job["resources"][0]["count"] = count
    args = batch_args(tmp_path, job)
    with patch.object(TRIGGER, "create_plan_once") as create:
        with pytest.raises(SystemExit, match="count=1"):
            TRIGGER.batch_mode(args, auth_client=object(), launched_by="tester@example.invalid")
    create.assert_not_called()


def test_duplicate_requested_alias_rejected_before_plan_creation(tmp_path):
    job = job_definition()
    job["resources"].append(deepcopy(job["resources"][0]))
    args = batch_args(tmp_path, job)
    with patch.object(TRIGGER, "create_plan_once") as create:
        with pytest.raises(SystemExit, match="逻辑名必须是非空、唯一字符串"):
            TRIGGER.batch_mode(args, auth_client=object(), launched_by="tester@example.invalid")
    create.assert_not_called()


def test_dry_run_exposes_constraints_without_authentication(tmp_path, capsys):
    args = batch_args(tmp_path, None, "--dry-run")
    assert TRIGGER.batch_mode(args) == 0
    result = result_json(capsys)
    assert result["constraints"] == [[{
        **HOST_CONSTRAINT, "resources": ["camera", "phone"],
    }]]


@pytest.mark.parametrize("persisted", [
    [], None, [{**HOST_CONSTRAINT, "topologyKey": "belong_to_cabinet"}],
    [{**HOST_CONSTRAINT, "topologyKey": "unsupported"}],
])
def test_lost_or_changed_constraints_cancel_and_verify_release(tmp_path, capsys, persisted):
    args = batch_args(tmp_path)
    cloud = FakeCloud()
    cloud.constraints = persisted
    with pytest.raises(RuntimeError, match="亲和约束"):
        run_batch(args, cloud)
    result = result_json(capsys)
    assert result["jobIds"] == [JOB_ID]
    assert result["finalStatus"] == "FAILED"
    assert result["affinityVerification"] == [{"jobId": JOB_ID, "verified": False}]
    assert result["cleanup"]["verified"] is True
    assert result["cleanup"]["jobs"][0]["cancelRequested"] is True
    assert cloud.events == ["create", "constraints", "discover", "cancel", "job", "release"]


@pytest.mark.parametrize("constraint", [HOST_CONSTRAINT, CABINET_CONSTRAINT])
def test_same_domain_binding_passes_then_release_is_accepted(tmp_path, capsys, constraint):
    args = batch_args(tmp_path, job_definition([constraint]))
    cloud = FakeCloud(
        constraints=[constraint],
        jobs=[
            {"status": "QUEUED"},
            {"status": "RUNNING", "startedAt": "2026-10-20T00:00:00Z"},
            {"status": "COMPLETE", "passedScenarios": 1, "totalScenarios": 1},
        ],
        allocations=[bindings(phone=False, camera=False), bindings(), bindings(phone=False, camera=False)],
    )
    result, _sleep = run_batch(args, cloud)
    assert result == 0
    output = result_json(capsys)
    assert output["finalStatus"] == "PASSED"
    assert output["affinityVerification"] == [{"jobId": JOB_ID, "verified": True}]
    assert output["cleanup"]["verified"] is True
    assert cloud.affinity_queries == 3
    assert "cancel" not in cloud.events


@pytest.mark.parametrize(("constraint", "bad_binding"), [
    (HOST_CONSTRAINT, bindings(camera_host="private-host-b")),
    (CABINET_CONSTRAINT, bindings(camera_cabinet="private-cabinet-b")),
])
def test_cross_domain_after_verified_allocation_is_cancelled(
    tmp_path, capsys, constraint, bad_binding
):
    args = batch_args(tmp_path, job_definition([constraint]))
    cloud = FakeCloud(
        constraints=[constraint], jobs=[{"status": "QUEUED"}, {"status": "RUNNING"}],
        allocations=[bindings(), bad_binding],
    )
    with pytest.raises(RuntimeError, match="拓扑缺失或不匹配"):
        run_batch(args, cloud)
    result = result_json(capsys)
    assert result["finalStatus"] == "FAILED"
    assert result["cleanup"]["verified"] is True
    assert cloud.events[-3:] == ["cancel", "job", "release"]


@pytest.mark.parametrize("missing", ["belongToHost", "jobRequirementName"])
def test_bound_resource_missing_required_field_fails_and_releases(tmp_path, capsys, missing):
    snapshot = bindings()
    del snapshot["deviceConnection"]["edges"][0]["node"][missing]
    cloud = FakeCloud(allocations=[snapshot])
    with pytest.raises(RuntimeError, match="拓扑缺失或不匹配|缺少逻辑名"):
        run_batch(batch_args(tmp_path), cloud)
    assert result_json(capsys)["cleanup"]["verified"] is True


@pytest.mark.parametrize("damage", ["next_page", "missing_page_info", "full_window"])
def test_incomplete_binding_inventory_cannot_verify(tmp_path, capsys, damage):
    snapshot = bindings()
    connection = snapshot["phoneConnection"]
    if damage == "next_page":
        connection["pageInfo"]["hasNextPage"] = True
    elif damage == "missing_page_info":
        del connection["pageInfo"]
    else:
        connection["edges"] = [{"node": {"jobId": None}} for _ in range(1000)]
    cloud = FakeCloud(allocations=[snapshot])
    with pytest.raises(RuntimeError, match="资源列表不完整"):
        run_batch(batch_args(tmp_path), cloud)
    result = result_json(capsys)
    assert result["affinityVerification"] == [{"jobId": JOB_ID, "verified": False}]
    assert result["cleanup"]["verified"] is True


def test_duplicate_alias_is_rejected_instead_of_choosing_one(tmp_path, capsys):
    snapshot = bindings()
    snapshot["phoneConnection"]["edges"].append(
        deepcopy(snapshot["phoneConnection"]["edges"][0])
    )
    cloud = FakeCloud(allocations=[snapshot])
    with pytest.raises(RuntimeError, match="同一逻辑名绑定多个资源"):
        run_batch(batch_args(tmp_path), cloud)
    assert result_json(capsys)["cleanup"]["verified"] is True


@pytest.mark.parametrize("job", [
    {"status": "RUNNING"}, {"status": "QUEUED", "startedAt": "2026-10-20T00:00:00Z"},
])
def test_started_job_with_partial_allocation_is_cancelled(tmp_path, capsys, job):
    cloud = FakeCloud(jobs=[job], allocations=[bindings(camera=False)])
    with pytest.raises(RuntimeError, match="已启动但亲和资源未完整绑定"):
        run_batch(batch_args(tmp_path), cloud)
    assert result_json(capsys)["cleanup"]["verified"] is True


def test_terminal_success_without_prior_allocation_evidence_cannot_pass(tmp_path, capsys):
    cloud = FakeCloud(jobs=[{
        "status": "COMPLETE", "passedScenarios": 1, "totalScenarios": 1,
    }])
    with pytest.raises(RuntimeError, match="终态成功前未核验完整亲和分配"):
        run_batch(batch_args(tmp_path), cloud)
    result = result_json(capsys)
    assert result["finalStatus"] == "FAILED"
    assert result["cleanup"]["verified"] is True


def test_constrained_no_wait_rejected_before_plan_creation(tmp_path):
    args = batch_args(tmp_path, None, "--no-wait", "--client-mode", "legacy")
    with patch.object(TRIGGER, "create_plan_once") as create:
        with pytest.raises(ValueError, match="亲和约束.*云端 run"):
            TRIGGER.batch_mode(args, auth_client=object(), launched_by="tester@example.invalid")
    create.assert_not_called()


def test_terminal_release_between_status_and_bindings_is_not_failure(tmp_path, capsys):
    cloud = FakeCloud(
        jobs=[{"status": "QUEUED"}, {"status": "RUNNING"},
              {"status": "COMPLETE", "passedScenarios": 1, "totalScenarios": 1}],
        allocations=[bindings(), bindings(phone=False, camera=False)],
    )
    result, _ = run_batch(batch_args(tmp_path), cloud)
    assert result == 0
    output = result_json(capsys)
    assert output["affinityVerification"] == [{"jobId": JOB_ID, "verified": True}]
    assert output["cleanup"]["verified"] is True
    assert "cancel" not in cloud.events


def test_unconstrained_no_wait_handoff_does_not_query_affinity(tmp_path, capsys):
    job = job_definition()
    del job["constraints"]
    cloud = FakeCloud()
    result, _sleep = run_batch(batch_args(tmp_path, job, "--no-wait"), cloud)
    assert result == 0
    assert cloud.events == ["create"]
    assert "constraints" not in cloud.created_input
    assert result_json(capsys)["affinityVerification"] == []
