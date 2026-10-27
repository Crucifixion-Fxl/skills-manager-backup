"""Contract tests for Device Cloud run preflight."""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "device_cloud.py"
SPEC = importlib.util.spec_from_file_location("device_cloud_cli", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
DEVICE_CLOUD = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = DEVICE_CLOUD
SPEC.loader.exec_module(DEVICE_CLOUD)
TESTS_SNAPSHOT = DEVICE_CLOUD._tests_snapshot


@pytest.fixture(autouse=True)
def _stable_tests_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        DEVICE_CLOUD,
        "_tests_snapshot",
        lambda _root, requested_ref, _tests_repo=None: {
            "requestedRef": requested_ref,
            "resolvedCommit": "a" * 40,
            "catalogDigest": "b" * 64,
        },
    )
    monkeypatch.setattr(
        DEVICE_CLOUD,
        "_local_client_snapshot",
        lambda root: {
            "root": str(root),
            "commit": "c" * 40,
            "contentDigest": "d" * 64,
        },
    )


def test_configure_utf8_output_reconfigures_both_streams(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    class Stream:
        def reconfigure(self, *, encoding: str, errors: str) -> None:
            calls.append((encoding, errors))

    monkeypatch.setattr(DEVICE_CLOUD.sys, "stdout", Stream())
    monkeypatch.setattr(DEVICE_CLOUD.sys, "stderr", Stream())

    DEVICE_CLOUD._configure_utf8_output()

    assert calls == [("utf-8", "replace"), ("utf-8", "replace")]


def test_utf8_child_env_overrides_console_encoding(monkeypatch) -> None:
    monkeypatch.setenv("PYTHONIOENCODING", "gbk")

    child_env = DEVICE_CLOUD._utf8_child_env()

    assert child_env["PYTHONIOENCODING"] == "utf-8"


def _feature_id_args(resource_profile: str) -> argparse.Namespace:
    return argparse.Namespace(
        app_url=(
            "https://example.invalid/Kiwibit_Android/"
            "prod_abcdef0_2.24.0-12_20260820-120000.apk"
        ),
        feature_id=123,
        allow_multiple=True,
        resource_profile=resource_profile,
        resource_json=None,
        cases_root=None,
        tests_repo=DEVICE_CLOUD.TESTS_REPO,
        tests_module=DEVICE_CLOUD.TESTS_MODULE,
        tests_ref="main",
        refresh_cases=False,
        uid=None,
        module=None,
        submodule=None,
        tags=None,
        plan_name="contract-test",
        device_cloud_env="prod-cn",
        priority=5,
        poll_interval=5,
        timeout=1800,
        queue_timeout=7200,
        cleanup_timeout=60,
        country="US",
        no_wait=False,
        dry_run=True,
        confirm=False,
        max_concurrency=4,
    )


def _write_feature(root: Path, relative_path: str, content: str) -> None:
    target = root / "features" / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _embedded_args(root: Path) -> argparse.Namespace:
    _write_feature(root, "firmware.feature", """Feature: embedded firmware
  Background:
    | camera | BATTERYCAM |
  @uid=embedded-001
  Scenario: inspect debug firmware
""")
    args = _feature_id_args("auto")
    args.feature_id = None
    args.uid = "embedded-001"
    args.cases_root = str(root)
    args.tests_repo = "https://gitlab.addx.ai/DEVT/test-suite/firmware-tests.git"
    args.tests_module = None
    args.app_url = None
    args.firmware_version = "1.19.35-d"
    args.companion_firmware_version = None
    return args


@pytest.mark.parametrize("command", ["run", "run-local"])
def test_firmware_parser_exposes_only_implemented_inputs(command: str) -> None:
    args = DEVICE_CLOUD.parser().parse_args([
        command, "--uid", "embedded-001", "--automation-type", "embedded",
        "--firmware-version", "1.19.35-d",
        "--companion-firmware-version", "1.19.36-d",
    ])
    assert args.automation_type == "embedded"
    assert args.firmware_version == "1.19.35-d"
    assert args.companion_firmware_version == "1.19.36-d"


@pytest.mark.parametrize("runner", ["run_test", "run_local_client"])
@pytest.mark.parametrize("version", [None, "", "1.19.35", "-d", "1.19.35-d-extra"])
def test_embedded_firmware_rejects_missing_or_non_debug_before_auth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: str, version: str | None,
) -> None:
    args = _embedded_args(tmp_path)
    args.firmware_version = version
    monkeypatch.setenv("DEVIUM_FIRMWARE_VERSION", "1.19.34-d")
    monkeypatch.setattr(
        DEVICE_CLOUD, "_authenticate_and_fetch_available_resources",
        lambda *_a: pytest.fail("invalid firmware must fail before authentication"),
    )
    monkeypatch.setattr(
        DEVICE_CLOUD.subprocess, "Popen",
        lambda *_a, **_k: pytest.fail("invalid firmware must not create jobs"),
    )
    with pytest.raises(ValueError, match="--firmware-version.*-d"):
        getattr(DEVICE_CLOUD, runner)(args)


@pytest.mark.parametrize("hint", ["explicit", "module", "origin"])
def test_embedded_firmware_requirement_uses_type_module_or_local_origin_before_auth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hint: str,
) -> None:
    args = _embedded_args(tmp_path)
    args.firmware_version = None
    args.tests_repo = "https://example.invalid/generic-tests.git"
    if hint == "explicit":
        args.automation_type = "embedded"
    elif hint == "module":
        args.tests_module = "firmware-tests"
    else:
        args.tests_repo = None
        monkeypatch.setattr(
            DEVICE_CLOUD, "_origin_url",
            lambda _root: "https://example.invalid/firmware-tests.git",
        )
    monkeypatch.setattr(
        DEVICE_CLOUD, "_authenticate_and_fetch_available_resources",
        lambda *_a: pytest.fail("embedded detection must precede authentication"),
    )
    with pytest.raises(ValueError, match="--firmware-version"):
        DEVICE_CLOUD.run_test(args)


@pytest.mark.parametrize("companion", [None, "1.19.36-d"])
def test_firmware_preflight_cloud_env_and_job_metadata(
    tmp_path: Path, companion: str | None,
) -> None:
    args = _embedded_args(tmp_path)
    args.companion_firmware_version = companion
    command, context = DEVICE_CLOUD.build_trigger_command(args)
    firmware = context["preview"]["firmware"]
    assert context["preview"]["automationType"] == "embedded"
    assert context["preview"]["automationTypeSource"] == "repository"
    assert firmware["testedVersion"] == "1.19.35-d"
    assert firmware["testedVersionSource"] == "explicit"
    assert firmware["companionVersion"] == (companion or "1.19.35-d")
    assert firmware["companionVersionSource"] == ("explicit" if companion else "tested-version")
    assert firmware["companionVersionConsumer"] is None
    trigger_args = DEVICE_CLOUD.parse_trigger_args(command[2:])
    assert "DEVIUM_FIRMWARE_VERSION=1.19.35-d" in trigger_args.inject_env
    assert (
        f"DEVIUM_COMPANION_FIRMWARE_VERSION={companion or '1.19.35-d'}"
        in trigger_args.inject_env
    )
    metadata = json.loads(trigger_args.metadata)
    assert metadata["firmware"] == firmware
    assert metadata["automationType"] == "embedded"
    injected_env = dict(value.split("=", 1) for value in trigger_args.inject_env)
    job_metadata = DEVICE_CLOUD.batch_mode.__globals__["build_metadata"](
        "legacy", trigger_args.app_type, trigger_args.tests_repo,
        trigger_args.tests_ref, trigger_args.tests_module, "@uid=embedded-001",
        metadata, injected_env,
    )
    assert job_metadata["firmware"] == firmware
    assert job_metadata["env"]["DEVIUM_FIRMWARE_VERSION"] == firmware["testedVersion"]
    assert job_metadata["env"]["DEVIUM_COMPANION_FIRMWARE_VERSION"] == firmware["companionVersion"]


@pytest.mark.parametrize("version", [
    "", "1.19.36", "-d", "1.19.36-d\n", " 1.19.36-d", "1.19\x00.36-d",
    "1.19\x80.36-d", "1.19\u200b.36-d",
])
def test_companion_firmware_is_debug_and_fails_before_auth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: str,
) -> None:
    args = _embedded_args(tmp_path)
    args.companion_firmware_version = version
    monkeypatch.setattr(
        DEVICE_CLOUD, "_authenticate_and_fetch_available_resources",
        lambda *_a: pytest.fail("invalid companion must fail before authentication"),
    )
    with pytest.raises(ValueError, match="--companion-firmware-version.*-d"):
        DEVICE_CLOUD.run_test(args)


@pytest.mark.parametrize("changed_field", ["firmware_version", "companion_firmware_version"])
def test_changed_firmware_invalidates_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, changed_field: str,
) -> None:
    args = _embedded_args(tmp_path)
    args.dry_run = False
    resources = [{"type": "BATTERYCAM", "available": True}]
    _, original = DEVICE_CLOUD.build_trigger_command(args, resources)
    args.confirm = original["preview"]["confirmation"]["token"]
    setattr(args, changed_field, "1.19.36-d")
    monkeypatch.setattr(
        DEVICE_CLOUD, "_authenticate_and_fetch_available_resources",
        lambda _env: (resources, object(), "tester@example.com"),
    )
    monkeypatch.setattr(
        DEVICE_CLOUD, "_run_batch_with_reused_auth",
        lambda *_a, **_k: pytest.fail("changed firmware must not create jobs"),
    )
    with pytest.raises(ValueError, match="结果已变化"):
        DEVICE_CLOUD.run_test(args)


def test_explicit_automation_type_is_independent_of_app_artifact(tmp_path: Path) -> None:
    args = _embedded_args(tmp_path)
    args.automation_type = "hardware"
    args.tests_repo = "https://example.invalid/factory-tests.git"
    args.firmware_version = None
    args.app_url = _feature_id_args("phone").app_url
    command, context = DEVICE_CLOUD.build_trigger_command(args)
    assert context["preview"]["automationType"] == "hardware"
    assert context["preview"]["automationTypeSource"] == "explicit"
    assert context["preview"]["app"]["version"] == "2.24.0"
    assert command[command.index("--tests-repo") + 1] == args.tests_repo
    assert command[command.index("--tests-module") + 1] == "factory-tests"
    assert not any("FIRMWARE_VERSION=" in value for value in command)


def test_companion_requires_explicit_tested_firmware(tmp_path: Path) -> None:
    args = _embedded_args(tmp_path)
    args.automation_type = "hardware"
    args.tests_repo = "https://example.invalid/hardware-tests.git"
    args.firmware_version = None
    args.companion_firmware_version = "1.19.36-d"
    with pytest.raises(ValueError, match="--companion-firmware-version.*--firmware-version"):
        DEVICE_CLOUD.build_trigger_command(args)


def test_firmware_repository_is_detected_even_with_legacy_module(tmp_path: Path) -> None:
    args = _embedded_args(tmp_path)
    args.tests_module = "kb-tests"
    args.app_url = _feature_id_args("phone").app_url
    command, context = DEVICE_CLOUD.build_trigger_command(args)
    assert command[command.index("--app-type") + 1] == "kb-tests"
    assert command[command.index("--tests-module") + 1] == "kb-tests"
    assert context["preview"]["automationType"] == "embedded"
    assert context["preview"]["firmware"]["testedVersion"] == "1.19.35-d"
    assert context["preview"]["app"]["version"] == "2.24.0"


@pytest.mark.parametrize("automation_type", ["app", "hardware"])
def test_nonembedded_type_cannot_bypass_firmware_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, automation_type: str,
) -> None:
    args = _embedded_args(tmp_path)
    args.automation_type = automation_type
    args.firmware_version = None
    monkeypatch.setattr(
        DEVICE_CLOUD, "_authenticate_and_fetch_available_resources",
        lambda *_a: pytest.fail("type conflict must fail before authentication"),
    )
    with pytest.raises(ValueError, match="--automation-type.*firmware-tests"):
        DEVICE_CLOUD.run_test(args)


@pytest.mark.parametrize("version", ["1.2-d", "1.19.35.7-d", "release-candidate-d"])
def test_debug_firmware_does_not_impose_extra_version_grammar(
    tmp_path: Path, version: str,
) -> None:
    args = _embedded_args(tmp_path)
    args.firmware_version = version
    _, context = DEVICE_CLOUD.build_trigger_command(args)
    assert context["preview"]["firmware"]["testedVersion"] == version


def test_local_firmware_env_reaches_actual_client_resource_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = _embedded_args(tmp_path)
    args.client_root = str(tmp_path / "client")
    args.app_url = _feature_id_args("phone").app_url
    monkeypatch.setattr(DEVICE_CLOUD, "_resolve_local_client_root", lambda _root: tmp_path)
    monkeypatch.setattr(DEVICE_CLOUD, "_validate_local_runtime", lambda *_a: None)
    for key in DEVICE_CLOUD.MANAGED_CREDENTIAL_KEYS:
        monkeypatch.setenv(key, f"private-{key}")
    monkeypatch.setenv("DEVIUM_FIRMWARE_VERSION", "stale-release")
    monkeypatch.setenv("DEVIUM_COMPANION_FIRMWARE_VERSION", "stale-companion")
    _, context, child_env = DEVICE_CLOUD.build_local_client_command(args)
    assert child_env["DEVIUM_FIRMWARE_VERSION"] == "1.19.35-d"
    assert child_env["DEVIUM_COMPANION_FIRMWARE_VERSION"] == "1.19.35-d"
    assert not DEVICE_CLOUD.MANAGED_CREDENTIAL_KEYS.intersection(child_env)
    assert "private-" not in json.dumps(context["preview"])

    config_module = _actual_client_config_module(monkeypatch)
    monkeypatch.setenv("DEVIUM_FIRMWARE_VERSION", child_env["DEVIUM_FIRMWARE_VERSION"])
    monkeypatch.setenv("DEVIUM_VERSION", child_env["DEVIUM_VERSION"])
    config = config_module.ResourcesConfigLoader()._parse_config({
        "feishu": {}, "resources": [{
            "name": "camera", "type": "BATTERYCAM", "firmwareVersion": "0.0.1-d",
        }],
    })
    assert config.resources[0].firmware_version == "1.19.35-d"
    assert config.resources[0].version == "2.24.0"


def _actual_client_config_module(monkeypatch: pytest.MonkeyPatch):
    # Cross-repository tests use the real Client loader without executing jobs.
    candidates = [
        parent / "device-cloud-client" / "devium_clients" / "utils" / "config_reader.py"
        for parent in SCRIPT_PATH.parents
    ]
    if os.environ.get("DEVICE_CLOUD_CLIENT_ROOT"):
        candidates.insert(0, Path(os.environ["DEVICE_CLOUD_CLIENT_ROOT"]) /
                          "devium_clients" / "utils" / "config_reader.py")
    config_path = next((path for path in candidates if path.is_file()), None)
    if config_path is None:
        pytest.skip("actual Client checkout is required for the cross-repository contract")
    spec = importlib.util.spec_from_file_location("firmware_client_config_contract", config_path)
    assert spec is not None and spec.loader is not None
    config_module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, config_module)
    spec.loader.exec_module(config_module)
    return config_module


@pytest.mark.parametrize("companion", [None, "1.19.30-d"])
def test_firmware_job_payload_and_local_env_reach_client_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, companion: str | None,
) -> None:
    args = _embedded_args(tmp_path)
    args.companion_firmware_version = companion
    args.app_url = _feature_id_args("phone").app_url
    command, context = DEVICE_CLOUD.build_trigger_command(args)
    trigger_args = DEVICE_CLOUD.parse_trigger_args(command[2:])
    trigger = DEVICE_CLOUD.batch_mode.__globals__
    metadata = trigger["build_metadata"](
        trigger_args.client_mode, trigger_args.app_type, trigger_args.tests_repo,
        trigger_args.tests_ref, trigger_args.tests_module, "@uid=embedded-001",
        json.loads(trigger_args.metadata),
        dict(value.split("=", 1) for value in trigger_args.inject_env),
    )
    captured = {}

    def capture_create_job(_endpoint, _auth, _query, variables):
        captured.update(variables["in"])
        return {"createJob": {"success": True, "id": 34}}

    monkeypatch.setitem(trigger, "gql", capture_create_job)
    trigger["create_job_batch"](
        "https://example.invalid/graphql", object(), 12, metadata,
        trigger["build_resource_requirements"](context["job"][0]["resources"]),
        "manual", 5, "tester@example.com",
    )
    assert captured["testPlanId"] == 12
    assert captured["metadata"]["firmware"] == context["preview"]["firmware"]
    job_env = captured["metadata"]["env"]

    args.client_root = str(tmp_path / "client")
    monkeypatch.setattr(DEVICE_CLOUD, "_resolve_local_client_root", lambda _root: tmp_path)
    monkeypatch.setattr(DEVICE_CLOUD, "_validate_local_runtime", lambda *_a: None)
    _, _, child_env = DEVICE_CLOUD.build_local_client_command(args)
    config_module = _actual_client_config_module(monkeypatch)
    for environment in (job_env, child_env):
        for name in ("DEVIUM_FIRMWARE_VERSION", "DEVIUM_COMPANION_FIRMWARE_VERSION", "DEVIUM_VERSION"):
            monkeypatch.setenv(name, environment[name])
        config = config_module.ResourcesConfigLoader()._parse_config({
            "resources": [{"name": "camera", "type": "BATTERYCAM"}],
        })
        camera = config.resources[0]
        assert camera.firmware_version == "1.19.35-d"
        assert camera.companion_firmware_version == (companion or "1.19.35-d")
        assert camera.version == "2.24.0"


def test_managed_local_job_merges_firmware_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = _embedded_args(tmp_path)
    command, context = DEVICE_CLOUD.build_trigger_command(args)
    context["_triggerCommand"] = command
    captured = []
    process = SimpleNamespace(
        stdout=iter(['[RESULT_JSON] {"planId": 12, "jobIds": [34]}\n']), wait=lambda: 0,
    )

    def popen(command, **_kwargs):
        captured.extend(command)
        return process

    monkeypatch.setattr(DEVICE_CLOUD.subprocess, "Popen", popen)
    DEVICE_CLOUD._create_managed_local_job(args, context, {}, tmp_path / "job.json")
    parsed = DEVICE_CLOUD.parse_trigger_args(captured[2:])
    metadata = json.loads(parsed.metadata)
    assert captured.count("--metadata") == 1
    assert metadata["executionLocation"] == "LOCAL_CLIENT"
    assert metadata["firmware"] == context["preview"]["firmware"]


def test_tests_snapshot_pins_clean_git_commit_and_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_feature(tmp_path, "login.feature", "Feature: login\n  Scenario: ok\n")
    commit = "1" * 40

    def git_output(arguments: list[str], _cwd: Path) -> str:
        if arguments[:2] == ["rev-parse", "HEAD"]:
            return commit
        if arguments[0] == "ls-remote":
            return f"{commit}\trefs/heads/main"
        if arguments[0] == "status":
            return ""
        raise AssertionError(arguments)

    monkeypatch.setattr(DEVICE_CLOUD, "_git_output", git_output)

    snapshot = TESTS_SNAPSHOT(tmp_path, "main")

    assert snapshot["requestedRef"] == "main"
    assert len(snapshot["resolvedCommit"]) == 40
    assert len(snapshot["catalogDigest"]) == 64


def test_tests_snapshot_rejects_dirty_feature(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_feature(tmp_path, "login.feature", "Feature: login\n  Scenario: ok\n")
    commit = "1" * 40

    def git_output(arguments: list[str], _cwd: Path) -> str:
        if arguments[:2] == ["rev-parse", "HEAD"]:
            return commit
        if arguments[0] == "ls-remote":
            return f"{commit}\trefs/heads/main"
        if arguments[0] == "status":
            return " M features/login.feature"
        raise AssertionError(arguments)

    monkeypatch.setattr(DEVICE_CLOUD, "_git_output", git_output)
    _write_feature(tmp_path, "login.feature", "Feature: changed\n  Scenario: ok\n")

    with pytest.raises(ValueError, match="未提交改动"):
        TESTS_SNAPSHOT(tmp_path, "main")


def test_tests_snapshot_rejects_commit_not_at_remote_ref(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_feature(tmp_path, "login.feature", "Feature: login\n  Scenario: ok\n")

    def git_output(arguments: list[str], _cwd: Path) -> str:
        if arguments[:2] == ["rev-parse", "HEAD"]:
            return "1" * 40
        if arguments[0] == "ls-remote":
            return f"{'2' * 40}\trefs/heads/main"
        raise AssertionError(arguments)

    monkeypatch.setattr(DEVICE_CLOUD, "_git_output", git_output)
    with pytest.raises(ValueError, match="不是远端 main 的当前提交"):
        TESTS_SNAPSHOT(tmp_path, "main")


@pytest.fixture
def _local_tests_git_origin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, str]:
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    template = tmp_path / "git-template"
    template.mkdir()
    monkeypatch.setenv("GIT_TEMPLATE_DIR", str(template))
    checkout = tmp_path / "checkout"
    checkout.mkdir()

    def git(*arguments: str) -> str:
        return subprocess.run(
            ["git", *arguments], cwd=checkout, check=True,
            capture_output=True, text=True, encoding="utf-8",
        ).stdout.strip()

    git("init", "--initial-branch=main")
    git("config", "user.name", "Local ref contract")
    git("config", "user.email", "local-ref@example.invalid")
    _write_feature(checkout, "one.feature", "Feature: ref contract\n  Scenario: one\n")
    git("add", "features/one.feature")
    git("-c", "commit.gpgsign=false", "commit", "-m", "Local ref fixture")
    git("tag", "light")
    git("tag", "-a", "annotated", "-m", "Local annotated tag")
    origin = tmp_path / "origin.git"
    git("init", "--bare", "--initial-branch=main", str(origin))
    git("remote", "add", "origin", str(origin))
    git("push", "origin", "main", "--tags")
    assert git("cat-file", "-t", "refs/tags/light") == "commit"
    assert git("cat-file", "-t", "refs/tags/annotated") == "tag"
    return checkout, git("rev-parse", "HEAD")


@pytest.mark.parametrize(
    ("ref", "error"),
    [
        ("main", None),
        ("light", None),
        ("annotated", "不是远端 annotated 的当前提交"),
        ("raw-sha", "可追溯的 Git 提交"),
    ],
)
def test_tests_snapshot_real_git_ref_contract(
    _local_tests_git_origin: tuple[Path, str], ref: str, error: str | None,
) -> None:
    checkout, commit = _local_tests_git_origin
    requested_ref = commit if ref == "raw-sha" else ref
    if error is not None:
        with pytest.raises(ValueError, match=error):
            TESTS_SNAPSHOT(checkout, requested_ref)
        return

    snapshot = TESTS_SNAPSHOT(checkout, requested_ref)
    assert snapshot["requestedRef"] == requested_ref
    assert snapshot["resolvedCommit"] == commit
    assert len(snapshot["catalogDigest"]) == 64


@pytest.mark.parametrize(
    "repository",
    [None, "https://gitlab.addx.ai/DEVT/test-suite/firmware-tests.git"],
)
def test_feature_id_preview_pins_commit_from_selected_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, repository: str | None
) -> None:
    legacy_root = tmp_path / "kb-tests"
    firmware_root = tmp_path / "firmware-tests"
    commits = {legacy_root: "1" * 40, firmware_root: "2" * 40}
    for root in commits:
        _write_feature(root, "one.feature", "Feature: one\n  Scenario: one\n")

    def resolve_root(_explicit, _ref, _refresh, tests_repo=None):
        return firmware_root if tests_repo == repository and repository else legacy_root

    def git_output(arguments: list[str], root: Path) -> str:
        if arguments[:2] == ["rev-parse", "HEAD"]:
            return commits[root]
        if arguments[0] == "ls-remote":
            return f"{commits[root]}\trefs/heads/main"
        if arguments[0] == "status":
            return ""
        raise AssertionError(arguments)

    monkeypatch.setattr(DEVICE_CLOUD, "_tests_snapshot", TESTS_SNAPSHOT)
    monkeypatch.setattr(DEVICE_CLOUD, "resolve_cases_root", resolve_root)
    monkeypatch.setattr(DEVICE_CLOUD, "_git_output", git_output)
    args = _feature_id_args("phone")
    args.tests_repo = repository
    args.tests_module = None
    if repository:
        args.firmware_version = "1.19.35-d"

    command, context = DEVICE_CLOUD.build_trigger_command(args)

    expected_repository = repository or DEVICE_CLOUD.TESTS_REPO
    expected_commit = commits[firmware_root if repository else legacy_root]
    assert context["preview"]["tests"]["repository"] == expected_repository
    assert context["preview"]["tests"]["resolvedCommit"] == expected_commit
    assert command[command.index("--tests-repo") + 1] == expected_repository
    assert command[command.index("--tests-ref") + 1] == expected_commit


def test_catalog_ignores_resources_template_and_detects_phone_aliases(
    tmp_path: Path,
) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "resources.yml").write_text(
        "resources:\n- name: phone\n  type: PHONE\n- name: camera\n  type: PLUGINCAM\n",
        encoding="utf-8",
    )
    _write_feature(
        tmp_path,
        "share/two-phones.feature",
        """Feature: 双手机分享
  Background:
    Given 我有resources配置
    When 我卸载 admin 的当前应用
    And close app 'com.kb.kiwibit' on sharer

  @uid=share-001 @testCase_modules=设备分享
  Scenario: 双手机分享
    When sharer \"sharer\" 通过 API 向 admin \"admin\" 分享 camera
""",
    )

    case = DEVICE_CLOUD.load_case_catalog(tmp_path)[0]

    assert case.phone_aliases == ("admin", "sharer")
    assert case.camera_types == ()
    assert case.camera_referenced is True
    assert case.resource_source == "case device references"


def test_catalog_does_not_treat_words_inside_ui_descriptions_as_phone_aliases(
    tmp_path: Path,
) -> None:
    _write_feature(
        tmp_path,
        "share/descriptions.feature",
        """Feature: 描述文本
  Background:
    When close app 'com.kb.kiwibit' on phone

  @uid=share-002
  Scenario: 描述中含介词
    When tap 'button on the share popup' with element "id/button" on phone
    Then visually verify 'admin email on permission details' on phone
""",
    )

    case = DEVICE_CLOUD.load_case_catalog(tmp_path)[0]

    assert case.phone_aliases == ("phone",)


def test_catalog_does_not_treat_log_assertion_tables_as_resources(
    tmp_path: Path,
) -> None:
    _write_feature(
        tmp_path,
        "firmware/power.feature",
        """Feature: firmware power
  Background:
    Given 我有resources配置
    When close app 'com.kb.kiwibit' on phone

  @uid=power-001
  Scenario: power cycle
    Then 设备日志按顺序匹配以下内容
      | 日志类型 | 内容                                           | 匹配模式 | 超时时间 |
      | SOC      | addx_app_get_wakeup_type.*AX_MCU_WAKEUP_POWERON | REGEX    | 90       |
      | SOC      | broadcast_data                                 | CONTAINS | 90       |
    And 日志还应包含
      | 内容                   | 匹配模式 |
      | AX_MCU_WAKEUP_KEY_BELL | CONTAINS |
""",
    )

    case = DEVICE_CLOUD.load_case_catalog(tmp_path)[0]

    assert case.logical_resources == ()
    assert case.resource_source == "case device references"


def test_catalog_accepts_explicit_resource_table_with_header(
    tmp_path: Path,
) -> None:
    _write_feature(
        tmp_path,
        "factory.feature",
        """Feature: factory
  @uid=factory-001
  Scenario: calibrated motion
    Given these resources are configured
      | name    | type                   |
      | robot   | ROBOT_ARM              |
      | chamber | TEMPERATURE_CONTROLLER |
""",
    )

    case = DEVICE_CLOUD.load_case_catalog(tmp_path)[0]

    assert case.logical_resources == (
        ("chamber", "TEMPERATURE_CONTROLLER"),
        ("robot", "ROBOT_ARM"),
    )


def test_catalog_inherits_feature_and_rule_tags_without_replacing_scenario_uid(tmp_path: Path) -> None:
    _write_feature(
        tmp_path,
        "device-settings/video/quality.feature",
        """@uid=feature-001 @testCase_modules=设备设置 @testCase_submodule=video
Feature: 视频设置

  @rule-tag
  规则: 录像清晰度

    @uid=scenario-001 @ai.status=complete
    Scenario: 修改录像清晰度
      When close app 'com.kb.kiwibit' on phone
""",
    )

    case = DEVICE_CLOUD.load_case_catalog(tmp_path)[0]

    assert case.uid == "scenario-001"
    assert "@uid=feature-001" in case.tags
    assert "@testCase_modules=设备设置" in case.tags
    assert "@testCase_submodule=video" in case.tags
    assert "@rule-tag" in case.tags


def test_module_and_submodule_select_effective_feature_tags(tmp_path: Path) -> None:
    _write_feature(
        tmp_path,
        "device-settings/video/quality.feature",
        """@testCase_modules=设备设置 @testCase_submodule=video
Feature: 视频设置
  @uid=video-001
  Scenario: 视频
""",
    )
    _write_feature(
        tmp_path,
        "device-settings/audio/audio.feature",
        """@testCase_modules=设备设置 @testCase_submodule=audio
Feature: 音频设置
  @uid=audio-001
  Scenario: 音频
""",
    )
    args = argparse.Namespace(uid=None, module="设备设置", submodule="video", tags=None, text=None)

    matches = DEVICE_CLOUD.find_cases(DEVICE_CLOUD.load_case_catalog(tmp_path), args)

    assert [case.uid for case in matches] == ["video-001"]


def test_submodule_requires_module(tmp_path: Path) -> None:
    args = argparse.Namespace(uid=None, module=None, submodule="video", tags=None, text=None)

    with pytest.raises(ValueError, match="--submodule 必须与 --module 一起使用"):
        DEVICE_CLOUD.find_cases([], args)


def test_feature_id_rejects_unpaired_submodule_before_preflight() -> None:
    args = _feature_id_args("phone")
    args.submodule = "video"

    with pytest.raises(ValueError, match="--submodule 必须与 --module 一起使用"):
        DEVICE_CLOUD._preflight_cases(args)


def test_parser_accepts_module_submodule_for_search_run_and_run_local() -> None:
    for command in ("search-cases", "run", "run-local"):
        arguments = [command]
        if command != "search-cases":
            arguments.extend(["--app-url", "https://example.invalid/app.apk"])
        arguments.extend(["--module", "设备设置", "--submodule", "video"])

        parsed = DEVICE_CLOUD.parser().parse_args(arguments)

        assert parsed.module == "设备设置"
        assert parsed.submodule == "video"


def test_run_parser_does_not_require_app_url() -> None:
    parsed = DEVICE_CLOUD.parser().parse_args([
        "run",
        "--tests-repo", "https://gitlab.addx.ai/DEVT/test-suite/hardware-tests.git",
        "--module", "factory",
    ])

    assert parsed.app_url is None
    assert parsed.tests_repo.endswith("hardware-tests.git")


def test_no_app_generic_repository_uses_declared_hardware_resources(tmp_path: Path) -> None:
    _write_feature(
        tmp_path,
        "factory.feature",
        """@testCase_modules=factory
Feature: factory station
  Background:
    | robot | ROBOT_ARM |
    | chamber | TEMPERATURE_CONTROLLER |

  @uid=factory-001
  Scenario: first station check

  @uid=factory-002
  Scenario: second station check
""",
    )
    args = _feature_id_args("auto")
    args.app_url = None
    args.feature_id = None
    args.module = "factory"
    args.allow_multiple = True
    args.cases_root = str(tmp_path)
    args.tests_repo = "https://gitlab.addx.ai/DEVT/test-suite/hardware-tests.git"
    args.tests_module = None
    args.no_wait = False

    command, context = DEVICE_CLOUD.build_trigger_command(
        args,
        available_resources=[
            {"type": "ROBOT_ARM", "available": True},
            {"type": "TEMPERATURE_CONTROLLER", "available": True},
        ],
    )

    assert command[command.index("--tests-repo") + 1] == args.tests_repo
    assert command[command.index("--tests-module") + 1] == "hardware-tests"
    assert command[command.index("--app-type") + 1] == "hardware-tests"
    assert "--inject-env" not in command
    assert context["preview"]["app"] is None
    assert [case["uid"] for case in context["preview"]["selection"]["cases"]] == [
        "factory-001", "factory-002"
    ]
    assert {item["type"] for item in context["preview"]["resourceGroup"]} == {
        "ROBOT_ARM", "TEMPERATURE_CONTROLLER"
    }
    assert context["preview"]["execution"]["capacitySnapshot"]["byType"]["ROBOT_ARM"] == 1


def test_app_artifact_does_not_override_explicit_firmware_repository(tmp_path: Path) -> None:
    _write_feature(
        tmp_path,
        "upgrade.feature",
        """Feature: firmware upgrade
  @uid=firmware-001
  Scenario: upgrade with companion app
    When close app 'com.kb.kiwibit' on phone
""",
    )
    args = _feature_id_args("phone")
    args.feature_id = None
    args.uid = "firmware-001"
    args.cases_root = str(tmp_path)
    args.tests_repo = "https://gitlab.addx.ai/DEVT/test-suite/firmware-tests.git"
    args.tests_module = None
    args.firmware_version = "1.19.35-d"

    command, context = DEVICE_CLOUD.build_trigger_command(args)

    assert command[command.index("--tests-module") + 1] == "firmware-tests"
    assert command[command.index("--app-type") + 1] == "firmware-tests"
    assert context["preview"]["app"]["app_family"] == "Kiwibit_Android"


def test_module_batch_rejects_duplicate_scenario_uids(tmp_path: Path) -> None:
    for name in ("one", "two"):
        _write_feature(
            tmp_path,
            f"{name}.feature",
            f"""@testCase_modules=factory
Feature: {name}
  @uid=duplicate-001
  Scenario: {name}
    | fixture | FIXTURE |
""",
        )
    args = _feature_id_args("auto")
    args.app_url = None
    args.feature_id = None
    args.module = "factory"
    args.allow_multiple = True
    args.cases_root = str(tmp_path)
    args.tests_repo = "https://gitlab.addx.ai/DEVT/test-suite/hardware-tests.git"
    args.tests_module = None
    args.no_wait = False

    with pytest.raises(ValueError, match="@uid 全局唯一"):
        DEVICE_CLOUD.build_trigger_command(args)


def test_module_batch_rejects_scenario_without_uid(tmp_path: Path) -> None:
    _write_feature(
        tmp_path,
        "factory.feature",
        """@testCase_modules=factory
Feature: factory
  @uid=factory-001
  Scenario: identified
    | fixture | FIXTURE |

  Scenario: missing identity
    | fixture | FIXTURE |
""",
    )
    args = _feature_id_args("auto")
    args.app_url = None
    args.feature_id = None
    args.module = "factory"
    args.allow_multiple = True
    args.cases_root = str(tmp_path)
    args.tests_repo = "https://gitlab.addx.ai/DEVT/test-suite/hardware-tests.git"
    args.tests_module = None
    args.no_wait = False

    with pytest.raises(ValueError, match="每个 Scenario 都有 @uid"):
        DEVICE_CLOUD.build_trigger_command(args)


def test_no_app_auto_does_not_default_to_phone(tmp_path: Path) -> None:
    _write_feature(
        tmp_path,
        "firmware.feature",
        """Feature: firmware
  @uid=firmware-001
  Scenario: protocol check
""",
    )
    args = _feature_id_args("auto")
    args.app_url = None
    args.feature_id = None
    args.uid = "firmware-001"
    args.cases_root = str(tmp_path)
    args.tests_repo = "https://gitlab.addx.ai/DEVT/test-suite/firmware-tests.git"
    args.tests_module = None
    args.firmware_version = "1.19.35-d"

    with pytest.raises(ValueError, match="不能默认申请 PHONE"):
        DEVICE_CLOUD.build_trigger_command(args)


def test_module_submodule_plan_splits_features_and_preserves_logical_aliases(tmp_path: Path) -> None:
    _write_feature(
        tmp_path,
        "share/two-phones.feature",
        """@testCase_modules=设备设置 @testCase_submodule=video
Feature: 双手机分享
  Background:
    When close app 'com.kb.kiwibit' on admin
    And close app 'com.kb.kiwibit' on sharer

  @uid=share-001
  Scenario: 双手机分享
    When 使用 camera 分享设备
""",
    )
    _write_feature(
        tmp_path,
        "share/one-phone.feature",
        """@testCase_modules=设备设置 @testCase_submodule=video
Feature: 单手机分享
  Background:
    When close app 'com.kb.kiwibit' on phone

  @uid=share-002
  Scenario: 单手机分享
    When 打开分享入口
""",
    )
    args = _feature_id_args("phone+plugin-camera")
    args.feature_id = None
    args.module = "设备设置"
    args.submodule = "video"
    args.allow_multiple = True
    args.cases_root = str(tmp_path)
    args.no_wait = False

    _, context = DEVICE_CLOUD.build_trigger_command(
        args,
        available_resources=[
            {"type": "PHONE", "platform": "android", "available": True},
            {"type": "PHONE", "platform": "android", "available": True},
            {"type": "PHONE", "platform": "android", "available": True},
            {"type": "PLUGINCAM", "platform": None, "available": True},
            {"type": "PLUGINCAM", "platform": None, "available": True},
        ],
    )

    jobs = context["job"]
    assert len(jobs) == 2
    by_name = {job["name"]: job for job in jobs}
    assert [item["name"] for item in by_name["双手机分享"]["resources"]] == [
        "admin", "sharer", "camera"
    ]
    assert [item["name"] for item in by_name["单手机分享"]["resources"]] == [
        "phone"
    ]
    assert context["preview"]["willCreate"] == {
        "testPlans": 1,
        "jobs": 2,
        "scenarios": 2,
    }
    assert context["preview"]["execution"]["concurrency"] == 2
    assert context["preview"]["execution"]["capacitySnapshot"] == {
        "byType": {"PHONE": 3, "BATTERYCAM": 0, "PLUGINCAM": 2},
        "byModel": {},
    }
    assert context["preview"]["selection"]["requested"]["submodule"] == "video"
    assert context["preview"]["selection"]["effectiveTags"] == (
        "@testCase_modules=设备设置 and @testCase_submodule=video"
    )


def test_multiple_cases_require_explicit_permission(tmp_path: Path) -> None:
    _write_feature(
        tmp_path,
        "login.feature",
        """Feature: 登录
  @uid=login-001 @testCase_modules=登录
  Scenario: 用户一登录
    When close app 'com.kb.kiwibit' on phone

  @uid=login-002 @testCase_modules=登录
  Scenario: 用户二登录
    When close app 'com.kb.kiwibit' on phone
""",
    )
    args = _feature_id_args("phone")
    args.feature_id = None
    args.module = "登录"
    args.allow_multiple = False
    args.cases_root = str(tmp_path)

    with pytest.raises(ValueError, match="明确添加 --allow-multiple"):
        DEVICE_CLOUD.build_trigger_command(args)


def test_auto_camera_type_does_not_infer_requirement_from_availability(tmp_path: Path) -> None:
    _write_feature(
        tmp_path,
        "live.feature",
        """Feature: 直播
  @uid=live-001
  Scenario: 查看直播
    When close app 'com.kb.kiwibit' on phone
    And 使用 camera 查看直播
""",
    )
    args = _feature_id_args("auto")
    args.feature_id = None
    args.uid = "live-001"
    args.allow_multiple = False
    args.cases_root = str(tmp_path)

    with pytest.raises(ValueError, match="用例未声明相机类型"):
        DEVICE_CLOUD.build_trigger_command(
            args,
            available_resources=[
                {"type": "PHONE", "platform": "android", "available": True},
                {"type": "PLUGINCAM", "platform": None, "available": True},
            ],
        )


def test_auto_phone_only_case_does_not_allocate_camera(tmp_path: Path) -> None:
    _write_feature(
        tmp_path,
        "login.feature",
        """Feature: 登录
  @uid=login-001
  Scenario: 登录成功
    When close app 'com.kb.kiwibit' on phone
""",
    )
    args = _feature_id_args("auto")
    args.feature_id = None
    args.uid = "login-001"
    args.allow_multiple = False
    args.cases_root = str(tmp_path)

    _, context = DEVICE_CLOUD.build_trigger_command(
        args,
        [
            {"type": "PHONE", "platform": "android", "available": True},
            {"type": "BATTERYCAM", "platform": None, "available": True},
            {"type": "PLUGINCAM", "platform": None, "available": True},
        ],
    )

    assert [item["type"] for item in context["preview"]["resourceGroup"]] == ["PHONE"]


def test_preflight_rejects_job_that_cannot_fit_live_capacity(tmp_path: Path) -> None:
    _write_feature(
        tmp_path,
        "share.feature",
        """Feature: 双手机分享
  Background:
    When close app 'com.kb.kiwibit' on admin
    And close app 'com.kb.kiwibit' on sharer
  @uid=share-001
  Scenario: 分享成功
    When 使用 camera 分享设备
""",
    )
    args = _feature_id_args("phone+plugin-camera")
    args.feature_id = None
    args.uid = "share-001"
    args.allow_multiple = False
    args.cases_root = str(tmp_path)

    with pytest.raises(ValueError, match="没有满足全部条件的实时空闲资源"):
        DEVICE_CLOUD.build_trigger_command(
            args,
            available_resources=[
                {"type": "PHONE", "platform": "android", "available": True},
                {"type": "PLUGINCAM", "platform": None, "available": True},
            ],
        )


def test_auto_honors_multiple_explicit_camera_resources(tmp_path: Path) -> None:
    _write_feature(
        tmp_path,
        "live.feature",
        """Feature: 直播
  @uid=live-001
  Scenario: 查看直播
    | camera1 | BATTERYCAM |
    | camera2 | PLUGINCAM |
    When close app 'com.kb.kiwibit' on phone
    And 使用 camera 查看直播
""",
    )
    args = _feature_id_args("auto")
    args.feature_id = None
    args.uid = "live-001"
    args.allow_multiple = False
    args.cases_root = str(tmp_path)

    _, context = DEVICE_CLOUD.build_trigger_command(
        args,
        available_resources=[
            {"type": "PHONE", "platform": "android", "available": True},
            {"type": "BATTERYCAM", "platform": None, "available": True},
            {"type": "PLUGINCAM", "platform": None, "available": True},
        ],
    )

    assert {item["type"] for item in context["preview"]["resourceGroup"]} == {
        "PHONE", "BATTERYCAM", "PLUGINCAM"
    }


def test_auto_resolves_camera_type_independently_per_feature(tmp_path: Path) -> None:
    _write_feature(
        tmp_path,
        "battery.feature",
        """Feature: 低功耗设备
  @uid=battery-001 @testCase_modules=混合模块
  Scenario: 低功耗直播
    | camera | BATTERYCAM |
    When close app 'com.kb.kiwibit' on phone
""",
    )
    _write_feature(
        tmp_path,
        "plugin.feature",
        """Feature: 常电设备
  @uid=plugin-001 @testCase_modules=混合模块
  Scenario: 常电直播
    | camera | PLUGINCAM |
    When close app 'com.kb.kiwibit' on phone
""",
    )
    args = _feature_id_args("auto")
    args.feature_id = None
    args.module = "混合模块"
    args.allow_multiple = True
    args.cases_root = str(tmp_path)
    args.no_wait = False

    _, context = DEVICE_CLOUD.build_trigger_command(
        args,
        available_resources=[
            {"type": "PHONE", "platform": "android", "available": True},
            {"type": "PHONE", "platform": "android", "available": True},
            {"type": "BATTERYCAM", "deviceModel": "KF126", "available": True},
            {"type": "PLUGINCAM", "deviceModel": "EC1", "available": True},
        ],
    )

    assert [job["resources"][-1]["type"] for job in context["job"]] == [
        "BATTERYCAM", "PLUGINCAM"
    ]


def test_resource_json_capacity_honors_device_model(tmp_path: Path) -> None:
    _write_feature(
        tmp_path,
        "live.feature",
        """Feature: 指定型号
  @uid=model-001
  Scenario: 指定型号直播
    When close app 'com.kb.kiwibit' on phone
    And 使用 camera 查看直播
""",
    )
    args = _feature_id_args("auto")
    args.feature_id = None
    args.uid = "model-001"
    args.allow_multiple = False
    args.cases_root = str(tmp_path)
    args.resource_json = """[
      {"name":"phone","type":"PHONE","conditions":{"platform":"$eq:android"}},
      {"name":"camera","type":"BATTERYCAM","conditions":{"device_model":"$eq:KF126"}}
    ]"""

    with pytest.raises(ValueError, match="没有满足全部条件"):
        DEVICE_CLOUD.build_trigger_command(
            args,
            available_resources=[
                {"type": "PHONE", "platform": "android", "available": True},
                {"type": "BATTERYCAM", "deviceModel": "KF129", "available": True},
            ],
        )


def test_no_wait_is_rejected_until_terminal_cleanup_can_be_verified(tmp_path: Path) -> None:
    for index in (1, 2):
        _write_feature(
            tmp_path,
            f"feature-{index}.feature",
            f"""Feature: 功能 {index}
  @uid=case-{index} @testCase_modules=批量
  Scenario: 场景 {index}
    When close app 'com.kb.kiwibit' on phone
""",
        )
    args = _feature_id_args("phone")
    args.feature_id = None
    args.module = "批量"
    args.allow_multiple = True
    args.cases_root = str(tmp_path)
    args.no_wait = True

    with pytest.raises(ValueError, match="不支持 --no-wait.*Job 终态和资源释放"):
        DEVICE_CLOUD.build_trigger_command(
            args,
            available_resources=[
                {"type": "PHONE", "platform": "android", "available": True},
                {"type": "PHONE", "platform": "android", "available": True},
            ],
        )


def test_run_requires_confirmation_after_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_feature(
        tmp_path,
        "login.feature",
        """Feature: 登录
  @uid=login-001
  Scenario: 登录成功
    When close app 'com.kb.kiwibit' on phone
""",
    )
    args = _feature_id_args("phone")
    args.feature_id = None
    args.uid = "login-001"
    args.allow_multiple = False
    args.cases_root = str(tmp_path)
    args.dry_run = False
    monkeypatch.setattr(
        DEVICE_CLOUD,
        "_authenticate_and_fetch_available_resources",
        lambda _env: (
            [{"type": "PHONE", "platform": "android", "available": True}],
            object(),
            "tester@example.com",
        ),
    )
    monkeypatch.setattr(
        DEVICE_CLOUD,
        "_run_batch_with_reused_auth",
        lambda *_args, **_kwargs: pytest.fail("未确认时不应创建计划"),
    )

    with pytest.raises(ValueError, match="--confirm"):
        DEVICE_CLOUD.run_test(args)


@pytest.mark.parametrize(("initial_count", "live_count"), [(1, 2), (2, 1)])
@pytest.mark.parametrize("changed_type", ["PHONE", "BATTERYCAM"])
def test_run_accepts_capacity_changes_that_preserve_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    initial_count: int, live_count: int, changed_type: str,
) -> None:
    _write_feature(
        tmp_path,
        "login.feature",
        """Feature: 登录
  @uid=login-001
  Scenario: 登录成功
    When close app 'com.kb.kiwibit' on phone
""",
    )
    args = _feature_id_args("phone")
    args.feature_id = None
    args.uid = "login-001"
    args.allow_multiple = False
    args.cases_root = str(tmp_path)
    args.dry_run = False
    phone = {"type": "PHONE", "platform": "android", "available": True}
    changed = {"type": changed_type, "platform": "android", "available": True}
    initial_resources = [phone, *[dict(changed) for _ in range(initial_count)]]
    live_resources = [phone, *[dict(changed) for _ in range(live_count)]]
    _, initial = DEVICE_CLOUD.build_trigger_command(args, initial_resources)
    args.confirm = initial["preview"]["confirmation"]["token"]
    _, live = DEVICE_CLOUD.build_trigger_command(args, live_resources)
    assert (initial["preview"]["execution"]["capacitySnapshot"]
            != live["preview"]["execution"]["capacitySnapshot"])
    monkeypatch.setattr(
        DEVICE_CLOUD,
        "_authenticate_and_fetch_available_resources",
        lambda _env: (live_resources, object(), "tester@example.com"),
    )
    calls = []
    monkeypatch.setattr(
        DEVICE_CLOUD,
        "_run_batch_with_reused_auth",
        lambda *call: calls.append(call) or 0,
    )

    assert DEVICE_CLOUD.run_test(args) == 0
    assert len(calls) == 1
    assert calls[0][0].concurrency == 1


@pytest.mark.parametrize("live_resources", [[], [
    {"type": "PHONE", "platform": "android", "available": False},
]])
def test_run_rechecks_capacity_before_using_confirmed_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, live_resources: list[dict],
) -> None:
    args = _feature_id_args("phone")
    args.cases_root = str(tmp_path)
    args.dry_run = False
    _, initial = DEVICE_CLOUD.build_trigger_command(args, [
        {"type": "PHONE", "platform": "android", "available": True},
    ])
    args.confirm = initial["preview"]["confirmation"]["token"]
    monkeypatch.setattr(DEVICE_CLOUD, "_authenticate_and_fetch_available_resources",
                        lambda _env: (live_resources, object(), "tester@example.com"))
    monkeypatch.setattr(DEVICE_CLOUD, "_run_batch_with_reused_auth",
                        lambda *_: pytest.fail("实时容量不足时不得创建计划"))

    with pytest.raises(ValueError, match="实时空闲资源"):
        DEVICE_CLOUD.run_test(args)


def test_run_rejects_confirmation_for_changed_concurrency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    for index in range(2):
        _write_feature(tmp_path, f"login-{index}.feature", f"""Feature: 登录{index}
  @uid=login-{index} @testCase_modules=容量确认
  Scenario: 登录成功
    When close app 'com.kb.kiwibit' on phone
""")
    args = _feature_id_args("phone")
    args.feature_id = None
    args.module = "容量确认"
    args.allow_multiple = True
    args.cases_root = str(tmp_path)
    args.dry_run = False
    phone = {"type": "PHONE", "platform": "android", "available": True}
    _, initial = DEVICE_CLOUD.build_trigger_command(args, [phone, dict(phone)])
    _, live = DEVICE_CLOUD.build_trigger_command(args, [phone])
    assert initial["preview"]["execution"]["concurrency"] == 2
    assert live["preview"]["execution"]["concurrency"] == 1
    args.confirm = initial["preview"]["confirmation"]["token"]
    monkeypatch.setattr(DEVICE_CLOUD, "_authenticate_and_fetch_available_resources",
                        lambda _env: ([phone], object(), "tester@example.com"))
    monkeypatch.setattr(DEVICE_CLOUD, "_run_batch_with_reused_auth",
                        lambda *_: pytest.fail("并发变化后不得创建计划"))

    with pytest.raises(ValueError, match="结果已变化"):
        DEVICE_CLOUD.run_test(args)


def test_confirmation_binds_immutable_ref_and_execution_parameters(tmp_path: Path) -> None:
    _write_feature(
        tmp_path,
        "login.feature",
        """Feature: 登录
  @uid=login-001
  Scenario: 登录成功
    When close app 'com.kb.kiwibit' on phone
""",
    )
    args = _feature_id_args("phone")
    args.feature_id = None
    args.uid = "login-001"
    args.allow_multiple = False
    args.cases_root = str(tmp_path)
    args.plan_name = "confirmed-plan"
    resources = [{"type": "PHONE", "platform": "android", "available": True}]

    command, initial = DEVICE_CLOUD.build_trigger_command(args, resources)
    initial_token = initial["preview"]["confirmation"]["token"]
    ref_index = command.index("--tests-ref") + 1

    assert command[ref_index] == "a" * 40
    assert initial["preview"]["tests"]["resolvedCommit"] == "a" * 40
    changed_commit_preview = copy.deepcopy(initial["preview"])
    changed_commit_preview["tests"]["resolvedCommit"] = "e" * 40
    assert DEVICE_CLOUD._confirmation_token(changed_commit_preview) != initial_token
    changed_catalog_preview = copy.deepcopy(initial["preview"])
    changed_catalog_preview["tests"]["catalogDigest"] = "f" * 64
    assert DEVICE_CLOUD._confirmation_token(changed_catalog_preview) != initial_token
    args.country = "CN"
    _, changed_country = DEVICE_CLOUD.build_trigger_command(args, resources)
    assert changed_country["preview"]["confirmation"]["token"] != initial_token
    args.country = "US"
    args.timeout += 1
    _, changed_timeout = DEVICE_CLOUD.build_trigger_command(args, resources)
    assert changed_timeout["preview"]["confirmation"]["token"] != initial_token
    args.timeout -= 1
    args.plan_name = "another-plan"
    _, changed_name = DEVICE_CLOUD.build_trigger_command(args, resources)
    assert changed_name["preview"]["confirmation"]["token"] != initial_token


def test_run_accepts_matching_confirmation_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_feature(
        tmp_path,
        "login.feature",
        """Feature: 登录
  @uid=login-001
  Scenario: 登录成功
    When close app 'com.kb.kiwibit' on phone
""",
    )
    args = _feature_id_args("phone")
    args.feature_id = None
    args.uid = "login-001"
    args.allow_multiple = False
    args.cases_root = str(tmp_path)
    args.dry_run = False
    resources = [{"type": "PHONE", "platform": "android", "available": True}]
    _, initial = DEVICE_CLOUD.build_trigger_command(args, resources)
    args.confirm = initial["preview"]["confirmation"]["token"]
    auth_client = object()
    monkeypatch.setattr(
        DEVICE_CLOUD,
        "_authenticate_and_fetch_available_resources",
        lambda _env: (resources, auth_client, "tester@example.com"),
    )
    calls: list[tuple[argparse.Namespace, dict]] = []
    monkeypatch.setattr(
        DEVICE_CLOUD,
        "_run_batch_with_reused_auth",
        lambda trigger_args, reused_auth, launched_by: (
            calls.append((
                trigger_args,
                {"auth_client": reused_auth, "launched_by": launched_by},
            ))
            or 0
        ),
    )

    assert DEVICE_CLOUD.run_test(args) == 0
    assert calls[0][0].concurrency == 1
    assert calls[0][1] == {
        "auth_client": auth_client,
        "launched_by": "tester@example.com",
    }


def test_fetch_available_resources_returns_only_capacity_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(DEVICE_CLOUD, "server_base_url", lambda endpoint: endpoint)
    monkeypatch.setattr(
        DEVICE_CLOUD,
        "DeviceCloudAuthClient",
        lambda _url: SimpleNamespace(authenticate=lambda: None),
    )
    monkeypatch.setattr(
        DEVICE_CLOUD,
        "gql",
        lambda *_args, **_kwargs: {
            "phoneConnection": {
                "edges": [{"node": {
                    "resourceType": "PHONE", "online": True, "jobId": None,
                    "connection": {"platform": "ANDROID", "host": "hidden"},
                    "uuid": "hidden",
                }}]
            },
            "deviceConnection": {
                "edges": [
                    {"node": {
                        "resourceType": "BATTERYCAM", "online": True, "jobId": 9,
                        "deviceModel": "KF126", "sn": "hidden",
                    }},
                    {"node": {
                        "resourceType": "ROBOT_ARM", "online": True, "jobId": None,
                        "deviceModel": "RA1", "connection": {"ip": "hidden"},
                    }},
                ]
            },
        },
    )

    result = DEVICE_CLOUD.fetch_available_resources("prod-cn")

    assert result == [
        {"type": "PHONE", "platform": "android", "available": True},
        {"type": "BATTERYCAM", "platform": None, "deviceModel": "KF126", "available": False},
        {"type": "ROBOT_ARM", "platform": None, "deviceModel": "RA1", "available": True},
    ]


def test_authenticated_capacity_reuses_one_session(monkeypatch) -> None:
    calls: list[str] = []
    auth_client = SimpleNamespace(
        authenticate=lambda: calls.append("authenticate")
        or SimpleNamespace(email="tester@example.com")
    )
    monkeypatch.setattr(DEVICE_CLOUD, "server_base_url", lambda endpoint: endpoint)
    monkeypatch.setattr(DEVICE_CLOUD, "DeviceCloudAuthClient", lambda _url: auth_client)
    monkeypatch.setattr(
        DEVICE_CLOUD,
        "fetch_available_resources",
        lambda _env, reused_auth: calls.append("fetch") or (
            [{"type": "PHONE", "platform": "android", "available": True}]
            if reused_auth is auth_client
            else pytest.fail("capacity lookup did not reuse authentication")
        ),
    )

    resources, reused_auth, launched_by = (
        DEVICE_CLOUD._authenticate_and_fetch_available_resources("prod-cn")
    )

    assert calls == ["authenticate", "fetch"]
    assert reused_auth is auth_client
    assert launched_by == "tester@example.com"
    assert resources[0]["type"] == "PHONE"


def test_in_process_batch_preserves_string_system_exit_as_return_code(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        DEVICE_CLOUD,
        "batch_mode",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(SystemExit("batch failed")),
    )

    result = DEVICE_CLOUD._run_batch_with_reused_auth(
        argparse.Namespace(),
        object(),
        "tester@example.com",
    )

    assert result == 1
    assert capsys.readouterr().err == "batch failed\n"


@pytest.mark.parametrize(
    ("exit_code", "expected"),
    [(None, 0), (0, 0), (2, 2)],
)
def test_in_process_batch_preserves_numeric_system_exit_codes(
    monkeypatch: pytest.MonkeyPatch,
    exit_code: int | None,
    expected: int,
) -> None:
    monkeypatch.setattr(
        DEVICE_CLOUD,
        "batch_mode",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(SystemExit(exit_code)),
    )

    assert DEVICE_CLOUD._run_batch_with_reused_auth(
        argparse.Namespace(), object(), "tester@example.com"
    ) == expected


def test_confirmed_run_keeps_batch_metadata_and_schedule_in_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_feature(
        tmp_path,
        "login.feature",
        """Feature: 登录
  @uid=login-001
  Scenario: 登录成功
    When close app 'com.kb.kiwibit' on phone
""",
    )
    args = _feature_id_args("phone")
    args.feature_id = None
    args.uid = "login-001"
    args.allow_multiple = False
    args.cases_root = str(tmp_path)
    args.dry_run = False
    resources = [{"type": "PHONE", "platform": "android", "available": True}]
    _, initial = DEVICE_CLOUD.build_trigger_command(args, resources)
    args.confirm = initial["preview"]["confirmation"]["token"]
    auth_client = object()
    monkeypatch.setattr(
        DEVICE_CLOUD,
        "_authenticate_and_fetch_available_resources",
        lambda _env: (resources, auth_client, "tester@example.com"),
    )
    batch_globals = DEVICE_CLOUD.batch_mode.__globals__
    captured: dict = {}
    monkeypatch.setitem(
        batch_globals,
        "create_plan_once",
        lambda *_args, **_kwargs: ("123", True),
    )

    def capture_schedule(trigger_args, context, state):
        captured["args"] = trigger_args
        captured["context"] = context
        state["running"] = {456: {}}
        state["created_ids"] = [456]
        return state, 1

    monkeypatch.setitem(batch_globals, "_run_batch_schedule", capture_schedule)
    monkeypatch.setitem(
        batch_globals,
        "ensure_plan_jobs_released",
        lambda *_args, **_kwargs: {"verified": True, "jobs": []},
    )
    monkeypatch.setitem(batch_globals, "_print_batch_summary", lambda *_args: 0)

    assert DEVICE_CLOUD.run_test(args) == 0
    assert captured["args"].concurrency == 1
    assert captured["context"]["auth"] is auth_client
    assert captured["context"]["launched_by"] == "tester@example.com"
    assert captured["context"]["jobs"] == [{
        "name": "登录",
        "tags": "@uid=login-001",
        "resources": [{
            "name": "phone",
            "type": "PHONE",
            "conditions": {"platform": "$eq:android"},
        }],
    }]
    assert captured["context"]["injected_env"]["DEVIUM_COUNTRY"] == "US"
    assert captured["context"]["injected_env"]["RP_LAUNCH_NAME"] == "contract-test"


def test_feature_id_rejects_auto_resource_profile() -> None:
    with pytest.raises(ValueError, match="--feature-id 无法读取本地用例元数据"):
        DEVICE_CLOUD.build_trigger_command(_feature_id_args("auto"))


def test_feature_id_accepts_explicit_phone_profile() -> None:
    command, preview = DEVICE_CLOUD.build_trigger_command(
        _feature_id_args("phone")
    )

    assert command[command.index("--feature-id") + 1] == "123"
    assert preview["preview"]["resourceGroup"] == [
        {
            "name": "phone",
            "type": "PHONE",
            "conditions": {"platform": "$eq:android"},
        }
    ]


def test_default_plan_name_uses_app_specific_abbreviation() -> None:
    config = DEVICE_CLOUD.parse_app_url(
        "https://example.invalid/Kiwibit_Android/"
        "prod_abcdef0_2.24.0-12_20260820-120000.apk"
    )

    plan_name = DEVICE_CLOUD.safe_plan_name(config, "live_scr_01", None)

    assert plan_name.startswith("ai-kb-2.24.0-live_scr_01-")


@pytest.mark.parametrize("environment", ["prod", "staging", "pre"])
def test_parse_app_url_extracts_supported_business_environment(environment: str) -> None:
    config = DEVICE_CLOUD.parse_app_url(
        "https://dist.builder.addx.live/download/530/"
        f"kiwibit_android_2.25.0-184883_{environment}_4198be5_"
        "20260908-161328.apk"
    )

    assert config.app_environment == environment
    if environment == "staging":
        assert config.version == "2.25.0"
        assert config.build_number == "184883"
        assert config.commit == "4198be5"


VICOHOME_APK = (
    "https://dist.builder.addx.live/download/895/"
    "vicohome_android_4.7.0-290805_staging_d00ac3e_20261020-175915.apk"
)

VICONATURE_APK = "https://dist.builder.addx.live/download/913/viconature_android_1.5.0-294893_prod_0730a65_20261021-143301.apk"
GUARD_APK = "https://dist.builder.addx.live/download/811/guard_android_4.6.0-255395_staging_225a4a1_20261010-181939.apk"


@pytest.mark.parametrize("url, family, app_name, package, version, build, commit, built_at, environment", [
    (VICONATURE_APK, "VicoNature_Android", "VicoNature", "com.smartaddx.vicohome.nature", "1.5.0", "294893", "0730a65", "20261021T143301", "prod"),
    (GUARD_APK, "Guard_Android", "QuanCheng", "com.ai.addx.guard", "4.6.0", "255395", "225a4a1", "20261010T181939", "staging"),
])
def test_parse_user_supplied_android_artifacts(
    url, family, app_name, package, version, build, commit, built_at, environment,
) -> None:
    config = DEVICE_CLOUD.parse_app_url(url)
    assert (config.app_family, config.app_name, config.package_name, config.platform) == (
        family, app_name, package, "android",
    )
    assert (config.version, config.build_number, config.commit, config.built_at) == (
        version, build, commit, built_at,
    )
    assert config.app_environment == environment


@pytest.mark.parametrize("brand, runtime, package", [
    ("viconature", "VicoNature", "com.smartaddx.vicohome.nature"),
    ("guard", "QuanCheng", "com.ai.addx.guard"),
])
@pytest.mark.parametrize("environment", ["prod", "staging", "pre"])
@pytest.mark.parametrize("layout", ["filename", "directory"])
def test_parse_additional_brands_with_existing_layouts(brand, runtime, package, environment, layout) -> None:
    path = (
        f"{brand}_android_1.5.0-123_{environment}_abcdef0_20261021-123456.apk"
        if layout == "filename" else
        f"{brand}/android/{environment}_abcdef0_1.5.0-123_20261021-123456.apk"
    )
    config = DEVICE_CLOUD.parse_app_url("https://example.invalid/" + path)
    assert (config.app_name, config.package_name, config.app_environment) == (runtime, package, environment)
    assert (config.commit, config.build_number) == ("abcdef0", "123")


@pytest.mark.parametrize("url", [
    "https://dist.builder.addx.live/download/921/viconature_ios_1.5.0-4218_prod_a05725e_20261021-161552.ipa",
    "https://example.invalid/guard_android_4.6.0-1_prod_abcdef0_20261010-181939.aab",
])
def test_parse_rejects_ios_and_aab_without_android_fallback(url) -> None:
    with pytest.raises(ValueError, match="Android APK"):
        DEVICE_CLOUD.parse_app_url(url)


@pytest.mark.parametrize("url", [VICOHOME_APK, VICONATURE_APK, GUARD_APK])
def test_public_inspect_cli_supports_all_requested_android_apps(url) -> None:
    command = [sys.executable, str(SCRIPT_PATH), "inspect-app", "--app-url", url]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stderr
    parsed = json.loads(result.stdout)
    assert parsed["platform"] == "android"


@pytest.mark.parametrize("path", [
    "guard/android/viconature_android_1.5.0-1_prod_abcdef0.apk",
    "viconature/ios/viconature_android_1.5.0-1_prod_abcdef0.apk",
    "notguard_android_1.5.0-1_prod_abcdef0.apk",
    "guard_androidish_1.5.0-1_prod_abcdef0.apk",
])
def test_new_brands_reject_mixed_or_similar_identity(path) -> None:
    with pytest.raises(ValueError, match="品牌|无法识别"):
        DEVICE_CLOUD.parse_app_url("https://example.invalid/" + path)


def test_repeated_consistent_identity_in_directory_and_filename_is_not_ambiguous() -> None:
    config = DEVICE_CLOUD.parse_app_url("https://example.invalid/Guard_Android/guard_android_4.6.0-1_staging_abcdef0.apk")
    assert config.app_name == "QuanCheng"


@pytest.mark.parametrize("brand", ["kiwibit", "vicohome", "viconature", "guard"])
@pytest.mark.parametrize("other", ["safemo", "golf", "unknown"])
@pytest.mark.parametrize("layout", ["directory-first", "filename-first", "same-segment"])
def test_supported_brand_cannot_hide_another_explicit_brand(brand, other, layout) -> None:
    tail = "4.7.0-1_prod_abcdef0.apk"
    path = {
        "directory-first": f"{brand}/android/{other}_android_{tail}",
        "filename-first": f"{other}/android/{brand}_android_{tail}",
        "same-segment": f"{brand}_android_{other}_android_{tail}",
    }[layout]
    with pytest.raises(ValueError, match="品牌|无法识别"):
        DEVICE_CLOUD.parse_app_url("https://example.invalid/" + path)


@pytest.mark.parametrize("path", [
    "guard_android_vicohome_android_4.7.0-1_prod_abcdef0.apk",
    "guard/android/guard_ios_4.7.0-1_prod_abcdef0.apk",
    "guard/android/not_guard_android_4.7.0-1_prod_abcdef0.apk",
    "guard/android/360_android_4.7.0-1_prod_abcdef0.apk",
    "guard/android/自定义品牌_android_4.7.0-1_prod_abcdef0.apk",
    "guard%2Fandroid%2Fsafemo_android_4.7.0-1_prod_abcdef0.apk",
])
def test_all_explicit_identity_markers_must_agree(path) -> None:
    with pytest.raises(ValueError, match="品牌|无法识别"):
        DEVICE_CLOUD.parse_app_url("https://example.invalid/" + path)


@pytest.mark.parametrize("brand, runtime", [
    ("kiwibit", "BAPP"), ("vicohome", "VicoHome"),
    ("viconature", "VicoNature"), ("guard", "QuanCheng"),
])
def test_consistent_repeated_brand_and_generic_filename_remain_supported(brand, runtime) -> None:
    for path in [
        f"{brand}/android/{brand}_android_4.7.0-1_prod_abcdef0.apk",
        f"{brand}_android/{brand}_android_4.7.0-1_prod_abcdef0.apk",
        f"{brand}/android/prod_abcdef0_4.7.0-1.apk",
    ]:
        config = DEVICE_CLOUD.parse_app_url("https://example.invalid/" + path)
        assert (config.app_name, config.platform) == (runtime, "android")


def test_public_cli_rejects_supported_directory_with_unsupported_filename() -> None:
    result = subprocess.run([
        sys.executable, str(SCRIPT_PATH), "inspect-app", "--app-url",
        "https://example.invalid/guard/android/safemo_android_4.7.0-1_prod_abcdef0.apk",
    ], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode != 0
    assert "品牌" in result.stderr + result.stdout


@pytest.mark.parametrize("local", [False, True])
def test_both_command_builders_reject_mixed_brands_before_job_configuration(local, monkeypatch) -> None:
    def forbid_preflight(_args):
        pytest.fail("品牌冲突必须在读取用例或配置 Job 前失败")

    monkeypatch.setattr(DEVICE_CLOUD, "_preflight_cases", forbid_preflight)
    args = _feature_id_args("phone")
    args.feature_id = None
    args.uid = "offline-mixed-brand-contract"
    args.app_url = "https://example.invalid/guard/android/safemo_android_4.7.0-1_prod_abcdef0.apk"
    build = DEVICE_CLOUD.build_local_client_command if local else DEVICE_CLOUD.build_trigger_command
    with pytest.raises(ValueError, match="品牌"):
        build(args)


def test_parse_vicohome_apk_extracts_exact_identity_and_build_metadata() -> None:
    config = DEVICE_CLOUD.parse_app_url(VICOHOME_APK)

    assert DEVICE_CLOUD.asdict(config) == {
        "app_url": VICOHOME_APK,
        "app_family": "VicoHome_Android",
        "app_type": "vh-tests",
        "app_name": "VicoHome",
        "package_name": "com.smartaddx.vicohome",
        "platform": "android",
        "app_environment": "staging",
        "version": "4.7.0",
        "build_number": "290805",
        "commit": "d00ac3e",
        "built_at": "20261020T175915",
    }


@pytest.mark.parametrize("environment", ["prod", "staging", "pre"])
@pytest.mark.parametrize("family", ["kiwibit", "vicohome"])
@pytest.mark.parametrize("layout", ["filename", "directory"])
def test_parse_apk_preserves_supported_families_environments_and_layouts(
    family: str, environment: str, layout: str,
) -> None:
    path = (
        f"{family}_android_4.7.0-290805_{environment}_d00ac3e_20261020-175915.apk"
        if layout == "filename" else
        f"{family}/android/{environment}_d00ac3e_4.7.0-290805_20261020-175915.apk"
    )
    config = DEVICE_CLOUD.parse_app_url("https://example.invalid/" + path)

    assert config.app_environment == environment
    assert config.version == "4.7.0"
    assert config.build_number == "290805"
    assert config.commit == "d00ac3e"
    assert config.built_at == "20261020T175915"
    assert (config.app_name, config.package_name) == (
        ("BAPP", "com.kb.kiwibit") if family == "kiwibit" else
        ("VicoHome", "com.smartaddx.vicohome")
    )


def test_parse_existing_kiwibit_config_remains_compatible() -> None:
    url = "https://example.invalid/Kiwibit_Android/prod_abcdef0_2.24.0-12_20260820-120000.apk"
    config = DEVICE_CLOUD.parse_app_url(url)

    assert DEVICE_CLOUD.asdict(config) == {
        "app_url": url, "app_family": "Kiwibit_Android", "app_type": "kb-tests",
        "app_name": "BAPP", "package_name": "com.kb.kiwibit", "platform": "android",
        "app_environment": "prod", "version": "2.24.0", "build_number": "12",
        "commit": "abcdef0", "built_at": "20260820T120000",
    }


def test_parse_vicohome_accepts_case_and_url_encoding_without_losing_original_url() -> None:
    url = "https://example.invalid/VicoHome%2FAndroid/staging_d00ac3e_4.7.0-290805_20261020-175915.APK?download=1"
    config = DEVICE_CLOUD.parse_app_url(url)

    assert config.app_name == "VicoHome"
    assert config.app_url == url


@pytest.mark.parametrize("path", [
    "unknown_android_4.7.0-1_staging_abcdef0.apk",
    "safemo_android_2.12.0-354_0134509_20260915-001722.apk",
    "golf_android_1.0.0-1_staging_abcdef0.apk",
    "notvicohome_android_4.7.0-1_staging_abcdef0.apk",
    "kiwibit/android/vicohome_android_4.7.0-1_staging_abcdef0.apk",
    "vicohome/android/kiwibit_android_4.7.0-1_staging_abcdef0.apk",
])
def test_parse_apk_rejects_unknown_or_ambiguous_app_identity(path: str) -> None:
    with pytest.raises(ValueError, match="App|应用|品牌|识别"):
        DEVICE_CLOUD.parse_app_url("https://example.invalid/" + path)


@pytest.mark.parametrize("family", ["kiwibit", "vicohome"])
@pytest.mark.parametrize("extension", ["ipa", "zip"])
def test_parse_apk_rejects_non_apk_artifact(family: str, extension: str) -> None:
    with pytest.raises(ValueError, match="Android APK"):
        DEVICE_CLOUD.parse_app_url(
            f"https://example.invalid/{family}_android_4.7.0-1_staging_abcdef0.{extension}"
        )


@pytest.mark.parametrize("path, message", [
    ("vicohome_android_staging_abcdef0.apk", "唯一识别"),
    ("1.2.3/vicohome_android_4.7.0-1_staging_abcdef0.apk", "唯一识别"),
    ("vicohome_android_4.7.0-1_test_abcdef0.apk", "业务环境"),
    ("vicohome_android_4.7.0-1_qa_abcdef0.apk", "业务环境"),
])
def test_parse_vicohome_rejects_missing_or_unsupported_metadata(path: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        DEVICE_CLOUD.parse_app_url("https://example.invalid/" + path)


@pytest.mark.parametrize("family, expected", [("kiwibit", "BAPP"), ("vicohome", "VicoHome")])
def test_parse_app_public_cli_supports_both_brands_without_authentication(family: str, expected: str) -> None:
    url = f"https://example.invalid/{family}_android_4.7.0-1_staging_abcdef0.apk"
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "inspect-app", "--app-url", url],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert result.returncode == 0, result.stderr
    parsed = json.loads(result.stdout)
    assert parsed["app_name"] == expected
    assert parsed["app_environment"] == "staging"
    assert parsed["version"] == "4.7.0"


def test_parse_app_does_not_use_query_string_to_guess_identity() -> None:
    with pytest.raises(ValueError, match="无法识别 App"):
        DEVICE_CLOUD.parse_app_url(
            "https://example.invalid/unknown_4.7.0-1_staging_abcdef0.apk?app=vicohome_android"
        )


def test_vicohome_default_plan_name_uses_its_own_identity() -> None:
    config = DEVICE_CLOUD.parse_app_url(VICOHOME_APK)
    assert DEVICE_CLOUD.safe_plan_name(config, "parser-check", None).startswith(
        "ai-vicohome-4.7.0-parser-check-"
    )


@pytest.mark.parametrize("country", ["US", "EU"])
def test_vicohome_app_metadata_is_injected_without_changing_test_repository(
    tmp_path: Path, country: str,
) -> None:
    _write_feature(tmp_path, "firmware.feature", '''Feature: firmware
  @uid=vicohome-parser-contract
  Scenario: use requested companion App
    When launch app 'com.smartaddx.vicohome' on phone
''')
    args = _feature_id_args("phone")
    args.feature_id = None
    args.uid = "vicohome-parser-contract"
    args.cases_root = str(tmp_path)
    args.tests_repo = "https://gitlab.addx.ai/DEVT/test-suite/firmware-tests.git"
    args.tests_module = None
    args.firmware_version = "1.21.70-d"
    args.app_url = VICOHOME_APK
    args.country = country
    command, context = DEVICE_CLOUD.build_trigger_command(args)

    assert command[command.index("--tests-module") + 1] == "firmware-tests"
    assert command[command.index("--app-type") + 1] == "firmware-tests"
    assert context["preview"]["app"]["app_family"] == "VicoHome_Android"
    assert context["preview"]["automationType"] == "embedded"
    injected = [command[i + 1] for i, arg in enumerate(command[:-1]) if arg == "--inject-env"]
    assert f"DEVIUM_COUNTRY={country}" in injected
    assert "DEVIUM_APP_NAME=VicoHome" in injected
    assert "DEVIUM_PACKAGE_NAME=com.smartaddx.vicohome" in injected
    assert "DEVIUM_ENV=staging" in injected
    assert f"DEVIUM_APP_URL={VICOHOME_APK}" in injected


@pytest.mark.parametrize("environment", ["test", "qa"])
def test_parse_app_url_rejects_unsupported_business_environment(environment: str) -> None:
    with pytest.raises(
        ValueError,
        match=r"无法识别 _prod_、_staging_ 或 _pre_ 业务环境",
    ):
        DEVICE_CLOUD.parse_app_url(
            "https://dist.builder.addx.live/download/530/"
            f"kiwibit_android_2.25.0-184883_{environment}_4198be5_"
            "20260908-161328.apk"
        )


def test_staging_app_environment_is_bound_and_injected_into_job() -> None:
    args = _feature_id_args("phone")
    args.app_url = (
        "https://dist.builder.addx.live/download/530/"
        "kiwibit_android_2.25.0-184883_staging_4198be5_"
        "20260908-161328.apk"
    )

    command, context = DEVICE_CLOUD.build_trigger_command(args)

    preview = context["preview"]
    assert preview["app"]["app_environment"] == "staging"
    staging_token = preview["confirmation"]["token"]
    changed_environment = copy.deepcopy(preview)
    changed_environment["app"]["app_environment"] = "prod"
    assert DEVICE_CLOUD._confirmation_token(changed_environment) != staging_token

    trigger_args = DEVICE_CLOUD.parse_trigger_args(command[2:])
    assert "DEVIUM_ENV=staging" in trigger_args.inject_env
    injected_env = dict(value.split("=", 1) for value in trigger_args.inject_env)
    metadata = json.loads(trigger_args.metadata or "{}")
    job_metadata = DEVICE_CLOUD.batch_mode.__globals__["build_metadata"](
        "legacy", trigger_args.app_type, trigger_args.tests_repo,
        trigger_args.tests_ref, trigger_args.tests_module, "@uid=contract-test",
        metadata, injected_env,
    )
    assert job_metadata["env"]["DEVIUM_ENV"] == "staging"


def test_local_client_rejects_outdated_runtime(tmp_path: Path) -> None:
    client_root = tmp_path / "device-cloud-client"
    cases_root = tmp_path / "kb-tests"
    (client_root / "devium_clients" / "job").mkdir(parents=True)
    (cases_root / "features").mkdir(parents=True)
    (client_root / "run_tests.py").write_text("# old", encoding="utf-8")
    (client_root / "devium_clients" / "job" / "resource.py").write_text(
        "# old", encoding="utf-8"
    )
    (cases_root / "features" / "environment.py").write_text("# old", encoding="utf-8")

    with pytest.raises(ValueError, match="版本过旧"):
        DEVICE_CLOUD._validate_local_runtime(client_root, cases_root)


def test_local_client_rejects_business_environment_hook(tmp_path: Path) -> None:
    client_root = tmp_path / "device-cloud-client"
    cases_root = tmp_path / "hardware-tests"
    (client_root / "devium_clients" / "job").mkdir(parents=True)
    (client_root / "devium_clients" / "behave_runtime").mkdir(parents=True)
    (cases_root / "features").mkdir(parents=True)
    marker_files = {
        client_root / "run_tests.py": "ClientBehaveRunner",
        client_root / "devium_clients" / "behave_runtime" / "runner.py": "ClientBehaveRunner",
        client_root / "devium_clients" / "behave_runtime" / "environment.py": "ManagedLocalCloudPlanClient",
        client_root / "devium_clients" / "job" / "managed_local_plan.py": "ManagedLocalCloudPlanClient",
    }
    for path, marker in marker_files.items():
        path.write_text(marker, encoding="utf-8")
    (cases_root / "features" / "environment.py").write_text(
        "# business-owned hook", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="不能提供 features/environment.py"):
        DEVICE_CLOUD._validate_local_runtime(client_root, cases_root)


def test_local_client_command_uses_sso_runtime_and_uniform_plan_name(tmp_path: Path) -> None:
    client_root = tmp_path / "device-cloud-client"
    cases_root = tmp_path / "kb-tests"
    (client_root / "devium_clients" / "job").mkdir(parents=True)
    (client_root / "devium_clients" / "behave_runtime").mkdir(parents=True)
    (cases_root / "features").mkdir(parents=True)
    (client_root / "run_tests.py").write_text("# ClientBehaveRunner", encoding="utf-8")
    (client_root / "devium_clients" / "behave_runtime" / "runner.py").write_text(
        "# ClientBehaveRunner", encoding="utf-8"
    )
    (client_root / "devium_clients" / "behave_runtime" / "environment.py").write_text(
        "# ManagedLocalCloudPlanClient", encoding="utf-8"
    )
    (client_root / "devium_clients" / "job" / "managed_local_plan.py").write_text(
        "# ManagedLocalCloudPlanClient", encoding="utf-8"
    )
    (cases_root / "features" / "live.feature").write_text(
        """Feature: 直播
  @uid=case-001 @deviceType=PLUGINCAM
  Scenario: 快速直播
    Given 我有resources配置
    When 使用camera扫码
""",
        encoding="utf-8",
    )
    args = _feature_id_args("phone+plugin-camera")
    args.feature_id = None
    args.uid = "case-001"
    args.allow_multiple = False
    args.cases_root = str(cases_root)
    args.client_root = str(client_root)
    args.plan_name = "ai-local-kb-live"
    args.no_wait = False

    command, context, child_env = DEVICE_CLOUD.build_local_client_command(
        args,
        [
            {"type": "PHONE", "platform": "android", "available": True},
            {"type": "PLUGINCAM", "platform": None, "available": True},
        ],
    )

    assert command[command.index("--plan-name") + 1] == "ai-local-kb-live"
    assert command[command.index("--tags") + 1] == "@uid=case-001"
    assert command[command.index("--env") + 1] == "prod"
    assert command[command.index("--mode") + 1] == "legacy"
    assert context["preview"]["executionMode"] == "local-client-cloud-resources"
    assert context["preview"]["localClient"]["commit"] == "c" * 40
    without_local_client = copy.deepcopy(context["preview"])
    without_local_client.pop("localClient")
    assert (
        DEVICE_CLOUD._confirmation_token(without_local_client)
        != context["preview"]["confirmation"]["token"]
    )
    assert "GITLAB_TOKEN" not in child_env
    assert "launched_by" not in " ".join(command).lower()


def test_local_client_dependency_preflight_fails_before_sso(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        DEVICE_CLOUD.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout="ModuleNotFoundError: No module named 'PIL'\n",
        ),
    )

    with pytest.raises(ValueError, match="依赖预检失败.*PIL"):
        DEVICE_CLOUD._validate_local_python_dependencies(tmp_path)


def test_local_client_rejects_false_zero_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cleanup_calls: list[bool] = []
    args = argparse.Namespace(
        dry_run=False, confirm="confirmed", device_cloud_env="prod-cn",
        cleanup_timeout=60, poll_interval=1,
    )
    context = {
        "job": [{"name": "case", "tags": "@uid=case", "resources": []}],
        "preview": {
            "clientRoot": str(tmp_path),
            "resourceGroup": [],
            "selection": {"effectiveTags": "@uid=case"},
            "confirmation": {"token": "confirmed"},
        },
    }
    monkeypatch.setattr(
        DEVICE_CLOUD,
        "build_local_client_command",
        lambda *_args: (["python", "run_tests.py"], context, {}),
    )
    monkeypatch.setattr(
        DEVICE_CLOUD,
        "_authenticate_and_fetch_available_resources",
        lambda _env: ([], object(), "tester@example.com"),
    )
    monkeypatch.setattr(
        DEVICE_CLOUD,
        "ensure_plan_jobs_released",
        lambda *_args, **kwargs: (
            cleanup_calls.append(kwargs["cancel_immediately"])
            or {"verified": True, "jobs": []}
        ),
    )
    monkeypatch.setattr(
        DEVICE_CLOUD, "_validate_local_python_dependencies", lambda _root: None
    )
    monkeypatch.setattr(
        DEVICE_CLOUD,
        "_create_managed_local_job",
        lambda *_args: {"planId": 12, "jobIds": [34]},
    )
    process = SimpleNamespace(
        stdout=iter([
            "HOOK-ERROR in before_all: AuthenticationError: timed out\n",
            "测试成功完成!\n",
        ]),
        wait=lambda: 0,
    )
    monkeypatch.setattr(DEVICE_CLOUD.subprocess, "Popen", lambda *_a, **_k: process)

    assert DEVICE_CLOUD.run_local_client(args) == 1
    assert cleanup_calls == [True]


def test_local_client_allows_optional_ai_import_failure_after_scenario_started(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = argparse.Namespace(
        dry_run=False, confirm="confirmed", device_cloud_env="prod-cn",
        cleanup_timeout=60, poll_interval=1,
    )
    context = {
        "job": [{"name": "case", "tags": "@uid=case", "resources": []}],
        "preview": {
            "clientRoot": str(tmp_path),
            "resourceGroup": [],
            "selection": {"effectiveTags": "@uid=case"},
            "confirmation": {"token": "confirmed"},
        },
    }
    monkeypatch.setattr(
        DEVICE_CLOUD,
        "build_local_client_command",
        lambda *_args: (["python", "run_tests.py"], context, {}),
    )
    monkeypatch.setattr(
        DEVICE_CLOUD,
        "_authenticate_and_fetch_available_resources",
        lambda _env: ([], object(), "tester@example.com"),
    )
    monkeypatch.setattr(
        DEVICE_CLOUD,
        "ensure_plan_jobs_released",
        lambda *_args, **_kwargs: {"verified": True, "jobs": []},
    )
    monkeypatch.setattr(
        DEVICE_CLOUD, "_validate_local_python_dependencies", lambda _root: None
    )
    monkeypatch.setattr(
        DEVICE_CLOUD,
        "_create_managed_local_job",
        lambda *_args: {"planId": 12, "jobIds": [34]},
    )
    process = SimpleNamespace(
        stdout=iter([
            "  Scenario: one live case\n",
            "ModuleNotFoundError: No module named 'devium_ai'\n",
            "1 scenario passed, 0 failed\n",
            "测试成功完成!\n",
        ]),
        wait=lambda: 0,
    )
    monkeypatch.setattr(DEVICE_CLOUD.subprocess, "Popen", lambda *_a, **_k: process)

    assert DEVICE_CLOUD.run_local_client(args) == 0


def test_local_client_allows_skipped_legacy_step_module_before_scenario(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = argparse.Namespace(
        dry_run=False, confirm="confirmed", device_cloud_env="prod-cn",
        cleanup_timeout=60, poll_interval=1,
    )
    context = {
        "job": [{"name": "case", "tags": "@uid=case", "resources": []}],
        "preview": {
            "clientRoot": str(tmp_path),
            "resourceGroup": [],
            "selection": {"effectiveTags": "@uid=case"},
            "confirmation": {"token": "confirmed"},
        },
    }
    monkeypatch.setattr(
        DEVICE_CLOUD,
        "build_local_client_command",
        lambda *_args: (["python", "run_tests.py"], context, {}),
    )
    monkeypatch.setattr(
        DEVICE_CLOUD,
        "_authenticate_and_fetch_available_resources",
        lambda _env: ([], object(), "tester@example.com"),
    )
    monkeypatch.setattr(
        DEVICE_CLOUD,
        "ensure_plan_jobs_released",
        lambda *_args, **_kwargs: {"verified": True, "jobs": []},
    )
    monkeypatch.setattr(
        DEVICE_CLOUD, "_validate_local_python_dependencies", lambda _root: None
    )
    monkeypatch.setattr(
        DEVICE_CLOUD,
        "_create_managed_local_job",
        lambda *_args: {"planId": 12, "jobIds": [34]},
    )
    process = SimpleNamespace(
        stdout=iter([
            "load_steps: skipped legacy module: ModuleNotFoundError: old step\n",
            "  Scenario: one live case\n",
            "1 scenario passed, 0 failed\n",
            "测试成功完成!\n",
        ]),
        wait=lambda: 0,
    )
    monkeypatch.setattr(DEVICE_CLOUD.subprocess, "Popen", lambda *_a, **_k: process)

    assert DEVICE_CLOUD.run_local_client(args) == 0


def test_local_client_fails_when_resource_release_cannot_be_verified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    args = argparse.Namespace(
        dry_run=False, confirm="confirmed", device_cloud_env="prod-cn",
        cleanup_timeout=60, poll_interval=1,
    )
    context = {
        "job": [{"name": "case", "tags": "@uid=case", "resources": []}],
        "preview": {
            "clientRoot": str(tmp_path),
            "resourceGroup": [],
            "selection": {"effectiveTags": "@uid=case"},
            "confirmation": {"token": "confirmed"},
        },
    }
    monkeypatch.setattr(
        DEVICE_CLOUD,
        "build_local_client_command",
        lambda *_args: (["python", "run_tests.py"], context, {}),
    )
    monkeypatch.setattr(
        DEVICE_CLOUD,
        "_authenticate_and_fetch_available_resources",
        lambda _env: ([], object(), "tester@example.com"),
    )
    monkeypatch.setattr(
        DEVICE_CLOUD, "_validate_local_python_dependencies", lambda _root: None
    )
    monkeypatch.setattr(
        DEVICE_CLOUD,
        "_create_managed_local_job",
        lambda *_args: {"planId": 12, "jobIds": [34]},
    )
    monkeypatch.setattr(
        DEVICE_CLOUD,
        "ensure_plan_jobs_released",
        lambda *_args, **_kwargs: {
            "verified": False,
            "jobs": [{
                "jobId": 34, "cancelRequested": False,
                "verifiedTerminal": True, "resourcesReleased": False,
                "finalStatus": "COMPLETE", "error": "resource still bound",
            }],
        },
    )
    process = SimpleNamespace(
        stdout=iter(["  Scenario: case\n", "1 scenario passed, 0 failed\n"]),
        wait=lambda: 0,
    )
    monkeypatch.setattr(DEVICE_CLOUD.subprocess, "Popen", lambda *_a, **_k: process)

    assert DEVICE_CLOUD.run_local_client(args) == 1
    assert '"finalStatus": "FAILED"' in capsys.readouterr().out


def test_managed_local_job_requires_exactly_one_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = _feature_id_args("phone")
    context = {"preview": {}}
    monkeypatch.setattr(
        DEVICE_CLOUD,
        "build_trigger_command",
        lambda _args: (["python", "trigger.py"], context),
    )
    process = SimpleNamespace(
        stdout=iter([
            '[RESULT_JSON] {"planId": 12, "jobIds": [34, 35]}\n',
        ]),
        wait=lambda: 0,
    )
    monkeypatch.setattr(DEVICE_CLOUD.subprocess, "Popen", lambda *_a, **_k: process)

    with pytest.raises(RuntimeError, match="只能绑定一个 Plan 和一个 Job"):
        DEVICE_CLOUD._create_managed_local_job(
            args, context, {}, tmp_path / "job.json"
        )


def test_managed_local_job_records_existing_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = _feature_id_args("phone")
    context = {"preview": {}}
    monkeypatch.setattr(
        DEVICE_CLOUD,
        "build_trigger_command",
        lambda _args: (["python", "trigger.py"], context),
    )
    process = SimpleNamespace(
        stdout=iter([
            '[RESULT_JSON] {"planId": "12", "jobIds": [34]}\n',
        ]),
        wait=lambda: 0,
    )
    monkeypatch.setattr(DEVICE_CLOUD.subprocess, "Popen", lambda *_a, **_k: process)

    result = DEVICE_CLOUD._create_managed_local_job(
        args, context, {}, tmp_path / "job.json"
    )

    assert result["jobIds"] == [34]
    assert context["preview"]["created"] == {"planId": 12, "jobId": 34}


@pytest.mark.parametrize("cleanup_verified", [True, False])
def test_local_client_cleans_known_result_after_helper_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], cleanup_verified: bool,
) -> None:
    args = argparse.Namespace(
        dry_run=False, confirm="confirmed", device_cloud_env="prod-cn",
        cleanup_timeout=60, poll_interval=1,
    )
    context = {
        "_triggerCommand": ["python", "trigger.py"],
        "job": [{"name": "case", "tags": "@uid=case", "resources": []}],
        "preview": {
            "clientRoot": str(tmp_path), "resourceGroup": [],
            "selection": {"effectiveTags": "@uid=case"},
            "confirmation": {"token": "confirmed"},
        },
    }
    monkeypatch.setattr(DEVICE_CLOUD, "build_local_client_command",
                        lambda *_args: (["python", "run_tests.py"], context, {}))
    monkeypatch.setattr(DEVICE_CLOUD, "_authenticate_and_fetch_available_resources",
                        lambda _env: ([], object(), "tester@example.com"))
    monkeypatch.setattr(DEVICE_CLOUD, "_validate_local_python_dependencies", lambda _root: None)
    launches = []
    process = SimpleNamespace(
        stdout=iter(['[RESULT_JSON] {"planId": 12, "jobIds": [34]}\n']),
        wait=lambda: 3,
    )
    monkeypatch.setattr(DEVICE_CLOUD.subprocess, "Popen",
                        lambda command, **_kwargs: launches.append(command) or process)
    cleanup_calls = []
    cleanup_result = {"verified": cleanup_verified, "jobs": [{
        "jobId": 34, "cancelRequested": True,
        "verifiedTerminal": cleanup_verified, "resourcesReleased": cleanup_verified,
        "finalStatus": "ABORTED_BY_WEB" if cleanup_verified else "RUNNING",
    }]}
    monkeypatch.setattr(
        DEVICE_CLOUD, "ensure_plan_jobs_released",
        lambda *_args, **kwargs: cleanup_calls.append((_args[2:], kwargs)) or cleanup_result,
    )

    with pytest.raises(RuntimeError, match="exit=3"):
        DEVICE_CLOUD.run_local_client(args)

    assert len(launches) == 1  # No Client starts after the creation helper failed.
    assert cleanup_calls[0][0] == (12, [34])
    assert cleanup_calls[0][1]["cancel_immediately"] is True
    output = capsys.readouterr().out
    result_lines = [line for line in output.splitlines() if line.startswith("[RESULT_JSON] ")]
    final = json.loads(result_lines[-1][len("[RESULT_JSON] "):])
    assert final["planId"] == 12 and final["jobIds"] == [34]
    assert final["finalStatus"] == "FAILED"
    assert "exit=3" in final["firstFailureReason"]
    assert final["cleanup"] == cleanup_result


def _trigger_metadata(command: list[str]) -> dict | None:
    trigger_args = DEVICE_CLOUD.parse_trigger_args(command[2:])
    return json.loads(trigger_args.metadata) if trigger_args.metadata else None


def test_client_image_unset_keeps_trigger_command_and_token() -> None:
    args = _feature_id_args("phone+battery-camera")
    command, context = DEVICE_CLOUD.build_trigger_command(args)
    assert "--metadata" not in command
    assert "clientImage" not in context["preview"]
    args.client_image = None
    _, explicit_none = DEVICE_CLOUD.build_trigger_command(args)
    assert (
        explicit_none["preview"]["confirmation"]["token"]
        == context["preview"]["confirmation"]["token"]
    )


def test_client_image_goes_to_metadata_preview_and_confirmation() -> None:
    args = _feature_id_args("phone+battery-camera")
    _, unset = DEVICE_CLOUD.build_trigger_command(args)
    args.client_image = "test-f5b43852"
    command, context = DEVICE_CLOUD.build_trigger_command(args)
    assert _trigger_metadata(command) == {"image": "test-f5b43852"}
    assert context["preview"]["clientImage"] == "test-f5b43852"
    token = context["preview"]["confirmation"]["token"]
    assert token != unset["preview"]["confirmation"]["token"]
    args.client_image = "test-other"
    _, changed = DEVICE_CLOUD.build_trigger_command(args)
    assert changed["preview"]["confirmation"]["token"] != token


def test_client_image_merges_with_firmware_metadata(tmp_path: Path) -> None:
    args = _embedded_args(tmp_path)
    args.client_image = "test-f5b43852"
    command, context = DEVICE_CLOUD.build_trigger_command(args)
    metadata = _trigger_metadata(command)
    assert metadata["image"] == "test-f5b43852"
    assert metadata["firmware"] == context["preview"]["firmware"]
    assert metadata["automationType"] == "embedded"
    assert command.count("--metadata") == 1


@pytest.mark.parametrize("tag", [
    "", "a" * 129, ".tag", "-tag", ".-tag", "has space", "reg/repo:tag", "192.168.36.190:8880/p/c:t", "tag\n", "镜像",
])
def test_client_image_rejects_non_tag_values(tag: str) -> None:
    args = _feature_id_args("phone+battery-camera")
    args.client_image = tag
    with pytest.raises(ValueError, match="--client-image"):
        DEVICE_CLOUD.build_trigger_command(args)


def test_client_image_is_run_only() -> None:
    parsed = DEVICE_CLOUD.parser().parse_args(
        ["run", "--uid", "x", "--client-image", "test-1"]
    )
    assert parsed.client_image == "test-1"
    assert DEVICE_CLOUD.parser().parse_args(["run", "--uid", "x"]).client_image is None
    with pytest.raises(SystemExit):
        DEVICE_CLOUD.parser().parse_args(
            ["run-local", "--uid", "x", "--client-image", "test-1"]
        )
