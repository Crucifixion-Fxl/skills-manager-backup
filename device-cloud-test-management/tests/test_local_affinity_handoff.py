"""Managed local affinity keeps the canonical Job and verifies live allocations."""
from __future__ import annotations

import copy
import json
import threading
from pathlib import Path
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from test_device_affinity import CLI, args_for, constraint, pool
from test_trigger_affinity import FakeCloud, JOB_ID, HOST_CONSTRAINT, batch_args, bindings, job_definition

TRIGGER = __import__("trigger_cloud_job")


def local_args(tmp_path, monkeypatch, metadata="", extra=()):
    args = args_for(tmp_path, monkeypatch, metadata, extra)
    args.command = "run-local"
    args.client_root = str(tmp_path)
    monkeypatch.setattr(CLI, "_resolve_local_client_root", lambda _: tmp_path)
    monkeypatch.setattr(CLI, "_validate_local_runtime", lambda *_: None)
    monkeypatch.setattr(CLI, "_local_client_snapshot", lambda _: {"commit": "c" * 40})
    return args


def test_local_preflight_retains_cli_and_feature_constraints(tmp_path, monkeypatch):
    args = local_args(tmp_path, monkeypatch, "# @firmware.affinity=belong_to_cabinet", [
        "--resource-constraints", json.dumps([constraint()]),
    ])
    command, context, _ = CLI.build_local_client_command(args, pool())
    assert command[command.index("--mode") + 1] == "legacy"
    expected = [constraint("belong_to_cabinet"), constraint()]
    assert context["job"][0]["constraints"] == expected
    assert context["preview"]["resourceGroups"][0]["constraints"] == expected
    assert context["job"][0]["resources"][1]["conditions"] == {"device_model": "$eq:KF451"}
    changed = copy.deepcopy(context["preview"])
    changed["resourceGroups"][0]["constraints"].pop()
    assert CLI._confirmation_token(changed) != context["preview"]["confirmation"]["token"]


def test_native_background_affinity_is_part_of_canonical_job(tmp_path, monkeypatch):
    args = local_args(tmp_path, monkeypatch)
    path = tmp_path / "features" / "binding.feature"
    text = path.read_text().replace("  @uid=affinity-case", """    Given 约束条件如下
      | 类型 | 拓扑键 | 设备 |
      | AFFINITY | belong_to_host | {phone},{camera} |
  @uid=affinity-case""")
    path.write_text(text)
    _, context, _ = CLI.build_local_client_command(args, pool())
    assert context["job"][0]["constraints"] == [constraint()]


@pytest.mark.parametrize("row", [
    "ANTI_AFFINITY | belong_to_host | {phone},{camera}",
    "AFFINITY | belong_to_room | {phone},{camera}",
    "AFFINITY | belong_to_host | {phone},{missing}",
])
def test_native_invalid_affinity_cannot_be_silently_dropped(tmp_path, monkeypatch, row):
    args = local_args(tmp_path, monkeypatch)
    path = tmp_path / "features" / "binding.feature"
    path.write_text(path.read_text().replace("  @uid=affinity-case", f"""    Given 约束条件如下
      | 类型 | 拓扑键 | 设备 |
      | {row} |
  @uid=affinity-case"""))
    with pytest.raises(ValueError, match="AFFINITY|亲和拓扑|已声明"):
        CLI.build_local_client_command(args, pool())


def test_canonical_handoff_reads_persistence_without_waiting_for_client(tmp_path, capsys):
    args = batch_args(tmp_path, None, "--no-wait", "--client-mode", "legacy",
                      "--concurrency", "1", "--metadata", '{"executionLocation":"LOCAL_CLIENT"}')
    cloud = FakeCloud()
    with patch.object(TRIGGER, "gql", side_effect=cloud.gql), patch.object(TRIGGER, "create_plan_once", return_value=("123", True)):
        assert TRIGGER.batch_mode(args, auth_client=object(), launched_by="test@example.invalid") == 0
    assert cloud.events == ["create", "constraints"]
    assert cloud.created_input["metadata"]["executionLocation"] == "LOCAL_CLIENT"
    assert cloud.created_input["constraints"] == [{**HOST_CONSTRAINT, "resources": ["camera", "phone"]}]
    result = json.loads(capsys.readouterr().out.split("[RESULT_JSON] ")[-1])
    assert result["jobIds"] == [JOB_ID]
    assert result["affinityVerification"] == [{"jobId": JOB_ID, "verified": False}]


@pytest.mark.parametrize("change", ["server_opt", "metadata_override", "multi"])
def test_handoff_marker_does_not_bypass_other_constrained_no_wait(tmp_path, change):
    args = batch_args(tmp_path, None, "--no-wait", "--client-mode", "legacy", "--concurrency", "1",
                      "--metadata", '{"executionLocation":"LOCAL_CLIENT"}')
    if change == "server_opt":
        args.client_mode = "server_opt"
    elif change == "metadata_override":
        args.metadata = '{"executionLocation":"LOCAL_CLIENT","clientMode":"server_opt"}'
    else:
        Path(args.batch).write_text(json.dumps([job_definition(), job_definition()]))
    with patch.object(TRIGGER, "create_plan_once", side_effect=AssertionError("must not create")):
        with pytest.raises(ValueError):
            TRIGGER.batch_mode(args, auth_client=object(), launched_by="test@example.invalid")


@pytest.mark.parametrize("persisted", [[], None])
def test_handoff_lost_persistence_reports_owned_ids_and_release(tmp_path, capsys, persisted):
    args = batch_args(tmp_path, None, "--no-wait", "--client-mode", "legacy", "--concurrency", "1",
                      "--metadata", '{"executionLocation":"LOCAL_CLIENT"}')
    cloud = FakeCloud()
    cloud.constraints = persisted
    with patch.object(TRIGGER, "gql", side_effect=cloud.gql), patch.object(TRIGGER, "create_plan_once", return_value=("123", True)):
        with pytest.raises(RuntimeError, match="亲和约束"):
            TRIGGER.batch_mode(args, auth_client=object(), launched_by="test@example.invalid")
    result = json.loads(capsys.readouterr().out.split("[RESULT_JSON] ")[-1])
    assert result["jobIds"] == [JOB_ID]
    assert result["cleanup"]["verified"] is True
    assert "affinity" not in cloud.events


class QuietClient:
    def __init__(self):
        self.finished = threading.Event()
        self.returncode = 0
        self.terminated = False
        self.stdout = self

    def __iter__(self):
        assert self.finished.wait(3), "Client output hung: affinity monitor did not finish/terminate"
        return iter(())

    def poll(self):
        return self.returncode if self.finished.is_set() else None

    def wait(self, timeout=None):
        assert self.finished.wait(timeout or 3)
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15
        self.finished.set()

    def kill(self):
        self.terminate()


@pytest.mark.parametrize("outcome", ["valid", "wrong", "missing", "partial", "never", "race", "timeout", "query_error", "changed", "lingering", "server_failed", "scenario_failed", "interrupt", "late_failure", "late_success", "client_failure", "client_init"])
def test_quiet_local_client_live_affinity_and_cleanup(tmp_path, monkeypatch, capsys, outcome):
    process = QuietClient()
    args = CLI.parser().parse_args(["run-local", "--uid", "affinity-case", "--confirm", "confirmed",
                                   "--poll-interval", "0", "--queue-timeout", "0" if outcome == "timeout" else "10"])
    if outcome == "lingering":
        args.cleanup_timeout = 0.03
    observed = threading.Event()
    if outcome == "client_failure":
        process.returncode = 3
    if outcome == "client_init":
        class InitializationOutput:
            def __iter__(self):
                assert observed.wait(3)
                return iter(["HOOK-ERROR in before_all: AuthenticationError: offline\n"])
        process.stdout = InitializationOutput()
    if outcome == "interrupt":
        args.poll_interval = 1

        class InterruptedOutput:
            def __iter__(self):
                assert observed.wait(3)
                raise KeyboardInterrupt()
                yield "unreachable"

        process.stdout = InterruptedOutput()
        original_tempdir = CLI.tempfile.TemporaryDirectory

        @contextmanager
        def checked_tempdir(*call_args, **kwargs):
            with original_tempdir(*call_args, **kwargs) as root:
                try:
                    yield root
                finally:
                    assert process.poll() is not None, "Client must stop before config removal"
                    assert not any(t.name == "device-cloud-local-affinity" for t in threading.enumerate()), "Monitor must join before config removal"

        monkeypatch.setattr(CLI.tempfile, "TemporaryDirectory", checked_tempdir)
    definition = job_definition()
    context = {"job": [definition], "preview": {
        "confirmation": {"token": "confirmed"}, "clientRoot": str(tmp_path),
        "resourceGroup": definition["resources"], "selection": {"effectiveTags": "@uid=affinity-case"},
    }}
    monkeypatch.setattr(CLI, "_authenticate_and_fetch_available_resources", lambda _: ([], object(), "test@example.invalid"))
    monkeypatch.setattr(CLI, "build_local_client_command", lambda *_: (["client"], context, {}))
    monkeypatch.setattr(CLI, "_validate_local_python_dependencies", lambda _: None)
    monkeypatch.setattr(CLI, "_create_managed_local_job", lambda *_: {"planId": 123, "jobIds": [JOB_ID]})
    monkeypatch.setattr(CLI.subprocess, "Popen", lambda *_a, **_kw: process)
    empty = bindings(phone=False, camera=False)
    bad = bindings(camera_host="private-host-b")
    missing = bindings()
    del missing["deviceConnection"]["edges"][0]["node"]["belongToHost"]
    terminal = {"status": "COMPLETE", "passedScenarios": 1, "totalScenarios": 1}
    jobs = ([terminal] if outcome == "never" else [{"status": "RUNNING"}, terminal])
    allocations = [bindings(), empty]
    if outcome in {"wrong", "missing", "partial"}:
        jobs = [{"status": "RUNNING"}]
        allocations = [{"wrong": bad, "missing": missing, "partial": bindings(camera=False)}[outcome]]
    elif outcome == "never":
        allocations = [empty]
    elif outcome == "race":
        jobs = [{"status": "RUNNING"}, {"status": "RUNNING"}, terminal]
    elif outcome == "server_failed":
        jobs = [{"status": "RUNNING"}, {"status": "FAILED_CLIENT_EXIT"}]
    elif outcome == "late_failure":
        jobs = [{"status": "RUNNING"}, {"status": "FAILED_CLIENT_EXIT"}]
    elif outcome == "scenario_failed":
        jobs = [{"status": "RUNNING"}, {"status": "COMPLETE", "passedScenarios": 0, "totalScenarios": 1}]
    elif outcome == "interrupt":
        jobs, allocations = [{"status": "RUNNING"}], [bindings()]
    elif outcome == "changed":
        jobs, allocations = [{"status": "RUNNING"}], [bindings(), bad]
    elif outcome == "timeout":
        jobs, allocations = [{"status": "QUEUED"}], [empty]
    cloud = FakeCloud(jobs=jobs, allocations=allocations)

    def gql(*call):
        if outcome == "query_error" and call[2] == TRIGGER.QUERY_JOB and not cloud.cancelled:
            raise TRIGGER.GraphQLRequestError("offline query failure")
        data = cloud.gql(*call)
        if call[2] == TRIGGER.AFFINITY_BINDINGS_QUERY:
            observed.set()
            if outcome in {"late_failure", "late_success", "client_failure", "client_init"}:
                process.finished.set()
        if call[2] == TRIGGER.QUERY_JOB and data["job"].get("status") == "COMPLETE" and outcome != "lingering":
            process.finished.set()
        return data

    monkeypatch.setattr(TRIGGER, "gql", gql)
    before = {thread.ident for thread in threading.enumerate()}
    if outcome in {"valid", "race", "late_success"}:
        assert CLI.run_local_client(args) == 0
        assert process.terminated is False
    elif outcome in {"client_failure", "client_init"}:
        assert CLI.run_local_client(args) == 1
    else:
        with pytest.raises(KeyboardInterrupt if outcome == "interrupt" else (RuntimeError, TimeoutError), match=None if outcome == "interrupt" else "亲和|超时|offline query|终态未通过"):
            CLI.run_local_client(args)
        if outcome not in {"never", "scenario_failed", "late_failure"}:
            assert process.terminated is True
    result = json.loads(capsys.readouterr().out.split("[RESULT_JSON] ")[-1])
    assert result["cleanup"]["verified"] is True
    assert result["affinityVerification"] == [{"jobId": JOB_ID, "verified": outcome in {"valid", "race", "changed", "lingering", "server_failed", "scenario_failed", "interrupt", "late_failure", "late_success", "client_failure", "client_init"}}]
    assert set(thread.ident for thread in threading.enumerate()) == before
    assert "private-host" not in json.dumps(result)


def test_helper_interrupt_terminates_owned_helper_and_keeps_known_ids(tmp_path, monkeypatch):
    process = QuietClient()

    class InterruptedOutput:
        def __iter__(self):
            yield '[RESULT_JSON] {"planId":123,"jobIds":[77]}\n'
            raise KeyboardInterrupt()

    process.stdout = InterruptedOutput()
    context = {"_triggerCommand": ["helper"], "preview": {}}
    monkeypatch.setattr(CLI.subprocess, "Popen", lambda *_a, **_kw: process)
    with pytest.raises(KeyboardInterrupt):
        CLI._create_managed_local_job(None, context, {}, tmp_path / "job.json")
    assert process.terminated is True
    assert context["_createdResult"] == {"planId": 123, "jobIds": [77]}
