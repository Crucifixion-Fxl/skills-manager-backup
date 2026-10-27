"""Regression tests for the packaged Device Cloud trigger."""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch
import urllib.error

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "trigger_cloud_job.py"
SPEC = importlib.util.spec_from_file_location("device_cloud_trigger", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
TRIGGER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = TRIGGER
SPEC.loader.exec_module(TRIGGER)


def test_missing_tests_ref_resolves_remote_default_branch() -> None:
    completed = subprocess.CompletedProcess(
        args=["git", "ls-remote"],
        returncode=0,
        stdout="ref: refs/heads/main\tHEAD\n",
        stderr="",
    )

    with patch.object(TRIGGER.subprocess, "run", return_value=completed) as run:
        resolved = TRIGGER.resolve_tests_ref("https://example.invalid/tests.git", None)

    assert resolved == "main"
    run.assert_called_once_with(
        [
            "git",
            "ls-remote",
            "--symref",
            "https://example.invalid/tests.git",
            "HEAD",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )


def test_explicit_tests_ref_does_not_query_remote() -> None:
    with patch.object(TRIGGER.subprocess, "run") as run:
        resolved = TRIGGER.resolve_tests_ref(
            "https://example.invalid/tests.git",
            "release-1",
        )

    assert resolved == "release-1"
    run.assert_not_called()


def test_batch_mode_reuses_authenticated_client_without_keychain_read(
    tmp_path: Path,
) -> None:
    batch_file = tmp_path / "batch.json"
    batch_file.write_text(
        json.dumps(
            [{
                "name": "login",
                "tags": "@uid=login",
                "resources": [{"name": "phone", "type": "PHONE"}],
            }]
        ),
        encoding="utf-8",
    )
    args = argparse.Namespace(
        endpoint=None,
        env="prod-cn",
        plan_name="reused-auth-plan",
        metadata=None,
        env_file=None,
        inject_env=None,
        baseline_env_file=None,
        batch=str(batch_file),
        concurrency=1,
        dry_run=False,
        plan_type="manual",
        allow_duplicate_plan=False,
        no_wait=False,
        cleanup_timeout=60,
        poll_interval=1,
    )
    auth_client = object()

    with (
        patch.object(
            TRIGGER,
            "DeviceCloudAuthClient",
            side_effect=AssertionError("must not authenticate twice"),
        ),
        patch.object(TRIGGER, "create_plan_once", return_value=("123", True)) as create,
        patch.object(
            TRIGGER,
            "_run_batch_schedule",
            side_effect=lambda _args, _context, state: (state, 1),
        ),
        patch.object(
            TRIGGER,
            "ensure_plan_jobs_released",
            return_value={"verified": True, "jobs": []},
        ),
        patch.object(TRIGGER, "_print_batch_summary", return_value=0),
    ):
        result = TRIGGER.batch_mode(
            args,
            auth_client=auth_client,
            launched_by="tester@example.com",
        )

    assert result == 0
    assert create.call_args.args[1] is auth_client
    assert create.call_args.args[4] == "tester@example.com"


def test_cleanup_cancels_nonterminal_job_and_verifies_resource_release() -> None:
    job_queries = iter([
        {"job": {"id": "34", "status": "RUNNING"}},
        {"job": {"id": "34", "status": "ABORTED_BY_WEB"}},
    ])
    calls: list[str] = []

    def fake_gql(_endpoint, _auth, query, _variables):
        if query == TRIGGER.QUERY_JOB:
            calls.append("job")
            return next(job_queries)
        if query == TRIGGER.CANCEL_JOB:
            calls.append("cancel")
            return {"cancelJob": True}
        if query == TRIGGER.RESOURCE_BINDINGS_QUERY:
            calls.append("resources")
            return {
                "phoneConnection": {"edges": []},
                "deviceConnection": {"edges": []},
            }
        raise AssertionError(query)

    with (
        patch.object(TRIGGER, "gql", side_effect=fake_gql),
        patch.object(TRIGGER.time, "sleep"),
    ):
        result = TRIGGER.ensure_job_released(
            "https://example.invalid/graphql",
            object(),
            34,
            cancel_immediately=True,
            timeout_seconds=60,
            poll_interval=0.01,
        )

    assert result == {
        "jobId": 34,
        "cancelRequested": True,
        "verifiedTerminal": True,
        "resourcesReleased": True,
        "finalStatus": "ABORTED_BY_WEB",
        "error": None,
    }
    assert calls == ["cancel", "job", "job", "resources"]


def test_cleanup_fails_truthfully_when_terminal_job_keeps_resource_binding() -> None:
    def fake_gql(_endpoint, _auth, query, _variables):
        if query == TRIGGER.QUERY_JOB:
            return {"job": {"id": "34", "status": "COMPLETE"}}
        if query == TRIGGER.RESOURCE_BINDINGS_QUERY:
            return {
                "phoneConnection": {"edges": [{"node": {"jobId": "34"}}]},
                "deviceConnection": {"edges": []},
            }
        raise AssertionError(query)

    with patch.object(TRIGGER, "gql", side_effect=fake_gql):
        result = TRIGGER.ensure_job_released(
            "https://example.invalid/graphql",
            object(),
            34,
            cancel_immediately=False,
            timeout_seconds=0,
            poll_interval=0.01,
        )

    assert result["verifiedTerminal"] is True
    assert result["resourcesReleased"] is False
    assert result["error"] == "job#34 终态后资源仍未释放"


def test_batch_exception_cancels_created_jobs_before_reraising(tmp_path: Path) -> None:
    batch_file = tmp_path / "batch.json"
    batch_file.write_text(json.dumps([{
        "name": "case",
        "tags": "@uid=case",
        "resources": [{"name": "fixture", "type": "FIXTURE"}],
    }]), encoding="utf-8")
    args = argparse.Namespace(
        endpoint=None, env="prod-cn", plan_name="cleanup-test", metadata=None,
        env_file=None, inject_env=None, baseline_env_file=None,
        batch=str(batch_file), concurrency=1, dry_run=False,
        plan_type="manual", allow_duplicate_plan=False, no_wait=False,
        cleanup_timeout=60, poll_interval=1,
    )
    cleanup_calls: list[tuple[list[int], bool]] = []

    def fail_schedule(_args, _context, state):
        state["created_ids"].append(77)
        raise RuntimeError("client disconnected")

    def cleanup(_endpoint, _auth, _plan_id, job_ids, **kwargs):
        cleanup_calls.append((job_ids, kwargs["cancel_immediately"]))
        return {"verified": True, "jobs": []}

    with (
        patch.object(TRIGGER, "create_plan_once", return_value=("123", True)),
        patch.object(TRIGGER, "_run_batch_schedule", side_effect=fail_schedule),
        patch.object(TRIGGER, "ensure_plan_jobs_released", side_effect=cleanup),
    ):
        with pytest.raises(RuntimeError, match="client disconnected"):
            TRIGGER.batch_mode(
                args, auth_client=object(), launched_by="tester@example.com"
            )

    assert cleanup_calls == [([77], True)]


def test_plan_cleanup_discovers_jobs_missing_from_local_create_state() -> None:
    captured: list[int] = []

    with (
        patch.object(
            TRIGGER,
            "gql",
            return_value={"testPlan": {"id": "12", "jobs": [{"id": "88"}]}},
        ),
        patch.object(
            TRIGGER,
            "ensure_jobs_released",
            side_effect=lambda _endpoint, _auth, job_ids, **_kwargs: (
                captured.extend(job_ids)
                or {"verified": True, "jobs": []}
            ),
        ),
    ):
        result = TRIGGER.ensure_plan_jobs_released(
            "https://example.invalid/graphql",
            object(),
            12,
            [77],
            cancel_immediately=True,
        )

    assert captured == [77, 88]
    assert result["verified"] is True
    assert result["planId"] == 12
    assert result["discoveryError"] is None


def test_batch_summary_fails_when_cleanup_discovery_is_unverified(capsys) -> None:
    state = TRIGGER._new_batch_state(1)
    state["completed"] = [{
        "name": "case", "job_id": 34, "status": "COMPLETE", "scenarios": "1/1"
    }]
    state["created_ids"] = [34]
    state["cleanup"] = {
        "verified": False,
        "jobs": [],
        "discoveryError": "plan job discovery failed",
    }

    result = TRIGGER._print_batch_summary(
        "12", [{"name": "case", "resources": []}], state, 1.0
    )

    assert result == 1
    assert '"finalStatus": "FAILED"' in capsys.readouterr().out


@pytest.mark.parametrize("error", [TimeoutError("socket timed out"), OSError("connection reset")])
def test_gql_wraps_direct_transport_errors(error: OSError) -> None:
    with patch.object(TRIGGER.urllib.request, "urlopen", side_effect=error) as request:
        with pytest.raises(TRIGGER.GraphQLRequestError) as raised:
            TRIGGER.gql("https://example.invalid/graphql", "test-token", "query { job }", {})

    assert raised.value.__cause__ is error
    assert str(error) in str(raised.value)
    assert request.call_count == 1


def test_cleanup_retains_known_job_evidence_when_socket_requests_time_out() -> None:
    with patch.object(
        TRIGGER.urllib.request, "urlopen", side_effect=TimeoutError("socket timed out")
    ):
        cleanup = TRIGGER.ensure_plan_jobs_released(
            "https://example.invalid/graphql", "test-token", 12, [77],
            cancel_immediately=True, timeout_seconds=0, poll_interval=0.01,
        )

    assert cleanup["verified"] is False
    assert cleanup["planId"] == 12
    assert "socket timed out" in cleanup["discoveryError"]
    assert cleanup["jobs"] == [{
        "jobId": 77,
        "cancelRequested": False,
        "verifiedTerminal": False,
        "resourcesReleased": False,
        "finalStatus": None,
        "error": cleanup["jobs"][0]["error"],
    }]
    assert "socket timed out" in cleanup["jobs"][0]["error"]


@pytest.mark.parametrize("connection_name", ["phoneConnection", "deviceConnection"])
@pytest.mark.parametrize("resource_count", [999, 1000, 1001])
def test_cleanup_does_not_confirm_release_when_resource_window_is_full(
    connection_name: str, resource_count: int
) -> None:
    def fake_gql(_endpoint, _auth, query, _variables):
        if query == TRIGGER.QUERY_JOB:
            return {"job": {"id": "77", "status": "COMPLETE"}}
        if query == TRIGGER.RESOURCE_BINDINGS_QUERY:
            result = {"phoneConnection": {"edges": []}, "deviceConnection": {"edges": []}}
            result[connection_name]["edges"] = [
                {"node": {"jobId": None}} for _ in range(resource_count)
            ]
            return result
        raise AssertionError(query)

    with patch.object(TRIGGER, "gql", side_effect=fake_gql):
        result = TRIGGER.ensure_job_released(
            "https://example.invalid/graphql", object(), 77,
            cancel_immediately=False, timeout_seconds=0, poll_interval=0.01,
        )

    assert result["verifiedTerminal"] is True
    assert result["resourcesReleased"] is (resource_count < 1000)
    if resource_count >= 1000:
        assert connection_name in result["error"]
    else:
        assert result["error"] is None


def test_gql_refreshes_auth_once_on_401() -> None:
    class Auth:
        access_token = "test-token-before-refresh"
        refresh_count = 0

        def refresh(self) -> None:
            self.refresh_count += 1
            self.access_token = "test-token-after-refresh"

    auth = Auth()
    endpoint = "https://example.invalid/graphql"
    response = io.BytesIO(b'{"data":{"job":{"status":"COMPLETE"}}}')
    with patch.object(TRIGGER.urllib.request, "urlopen", side_effect=[
        urllib.error.HTTPError(endpoint, 401, "unauthorized", {}, None), response,
    ]) as request:
        result = TRIGGER.gql(endpoint, auth, "query { job }", {})

    assert result == {"job": {"status": "COMPLETE"}}
    assert auth.refresh_count == 1
    assert [call.args[0].get_header("Authorization") for call in request.call_args_list] == [
        "Bearer test-token-before-refresh", "Bearer test-token-after-refresh",
    ]


def test_gql_does_not_refresh_or_retry_forbidden_response() -> None:
    class Auth:
        access_token = "test-token"

        def refresh(self) -> None:
            raise AssertionError("403 must not refresh")

    endpoint = "https://example.invalid/graphql"
    with patch.object(TRIGGER.urllib.request, "urlopen", side_effect=
        urllib.error.HTTPError(endpoint, 403, "forbidden", {}, None)
    ) as request:
        with pytest.raises(TRIGGER.GraphQLRequestError, match="HTTP 403"):
            TRIGGER.gql(endpoint, Auth(), "query { job }", {})

    assert request.call_count == 1


def test_gql_reports_refresh_failure_as_request_error() -> None:
    from device_cloud_auth import AuthenticationError

    class Auth:
        access_token = "expired-test-token"
        refresh_count = 0

        def refresh(self) -> None:
            self.refresh_count += 1
            raise AuthenticationError("refresh endpoint unavailable")

    auth = Auth()
    endpoint = "https://example.invalid/graphql"
    with patch.object(TRIGGER.urllib.request, "urlopen", side_effect=
        urllib.error.HTTPError(endpoint, 401, "unauthorized", {}, None)
    ) as request:
        with pytest.raises(TRIGGER.GraphQLRequestError, match="authentication refresh failed"):
            TRIGGER.gql(endpoint, auth, "query { job }", {})

    assert auth.refresh_count == 1
    assert request.call_count == 1
    assert auth.access_token == "expired-test-token"


def test_cleanup_retains_job_and_attempts_cancel_when_auth_refresh_fails() -> None:
    from device_cloud_auth import AuthenticationError

    class Auth:
        access_token = "expired-test-token"

        def refresh(self) -> None:
            raise AuthenticationError("refresh endpoint unavailable")

    endpoint = "https://example.invalid/graphql"
    with patch.object(TRIGGER.urllib.request, "urlopen", side_effect=
        urllib.error.HTTPError(endpoint, 401, "unauthorized", {}, None)
    ) as request:
        cleanup = TRIGGER.ensure_plan_jobs_released(
            endpoint, Auth(), 12, [77],
            cancel_immediately=True, timeout_seconds=0, poll_interval=0.01,
        )

    assert cleanup["verified"] is False
    assert cleanup["planId"] == 12
    assert "authentication refresh failed" in cleanup["discoveryError"]
    assert len(cleanup["jobs"]) == 1
    job = cleanup["jobs"][0]
    assert job["jobId"] == 77
    assert job["verifiedTerminal"] is False
    assert job["resourcesReleased"] is False
    assert "authentication refresh failed" in job["error"]
    queries = [json.loads(call.args[0].data)["query"] for call in request.call_args_list]
    assert TRIGGER.CANCEL_JOB in queries
    assert TRIGGER.QUERY_JOB in queries
