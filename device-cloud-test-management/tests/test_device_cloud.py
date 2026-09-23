"""Contract tests for Device Cloud run preflight."""

from __future__ import annotations

import argparse
import copy
import importlib.util
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
        lambda _root, requested_ref: {
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
        country="US",
        no_wait=True,
        dry_run=True,
        confirm=False,
        max_concurrency=4,
    )


def _write_feature(root: Path, relative_path: str, content: str) -> None:
    target = root / "features" / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


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


def test_auto_camera_type_rejects_ambiguous_case_types(tmp_path: Path) -> None:
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

    with pytest.raises(ValueError, match="无法唯一确定相机类型.*BATTERYCAM.*PLUGINCAM"):
        DEVICE_CLOUD.build_trigger_command(
            args,
            available_resources=[
                {"type": "PHONE", "platform": "android", "available": True},
                {"type": "BATTERYCAM", "platform": None, "available": True},
                {"type": "PLUGINCAM", "platform": None, "available": True},
            ],
        )


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


def test_no_wait_rejects_multiple_jobs(tmp_path: Path) -> None:
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

    with pytest.raises(ValueError, match="--no-wait 不支持多 Job"):
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


def test_run_rejects_confirmation_for_changed_capacity(
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
    initial_resources = [
        {"type": "PHONE", "platform": "android", "available": True},
    ]
    _, initial = DEVICE_CLOUD.build_trigger_command(args, initial_resources)
    args.confirm = initial["preview"]["confirmation"]["token"]
    monkeypatch.setattr(
        DEVICE_CLOUD,
        "_authenticate_and_fetch_available_resources",
        lambda _env: (
            initial_resources + [
                {"type": "PHONE", "platform": "android", "available": True},
            ],
            object(),
            "tester@example.com",
        ),
    )
    monkeypatch.setattr(
        DEVICE_CLOUD,
        "_run_batch_with_reused_auth",
        lambda *_args, **_kwargs: pytest.fail("快照变化后不应创建计划"),
    )

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
                "edges": [{"node": {
                    "resourceType": "BATTERYCAM", "online": True, "jobId": 9,
                    "deviceModel": "KF126", "sn": "hidden",
                }}]
            },
        },
    )

    result = DEVICE_CLOUD.fetch_available_resources("prod-cn")

    assert result == [
        {"type": "PHONE", "platform": "android", "available": True},
        {"type": "BATTERYCAM", "platform": None, "deviceModel": "KF126", "available": False},
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

    def capture_schedule(trigger_args, context):
        captured["args"] = trigger_args
        captured["context"] = context
        return {
            "completed": [],
            "failed": [],
            "create_failed": [],
            "running": {"456": {}},
        }, 1

    monkeypatch.setitem(batch_globals, "_run_batch_schedule", capture_schedule)
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


def test_local_client_command_uses_sso_runtime_and_uniform_plan_name(tmp_path: Path) -> None:
    client_root = tmp_path / "device-cloud-client"
    cases_root = tmp_path / "kb-tests"
    (client_root / "devium_clients" / "job").mkdir(parents=True)
    (cases_root / "features").mkdir(parents=True)
    (client_root / "run_tests.py").write_text("# --resources-config", encoding="utf-8")
    (client_root / "devium_clients" / "job" / "managed_local_plan.py").write_text(
        "# ManagedLocalCloudPlanClient", encoding="utf-8"
    )
    (cases_root / "features" / "environment.py").write_text(
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
    args = argparse.Namespace(dry_run=False, confirm="confirmed", device_cloud_env="prod-cn")
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
    monkeypatch.setattr(DEVICE_CLOUD, "fetch_available_resources", lambda _env: [])
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


def test_local_client_allows_optional_ai_import_failure_after_scenario_started(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = argparse.Namespace(dry_run=False, confirm="confirmed", device_cloud_env="prod-cn")
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
    monkeypatch.setattr(DEVICE_CLOUD, "fetch_available_resources", lambda _env: [])
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
    args = argparse.Namespace(dry_run=False, confirm="confirmed", device_cloud_env="prod-cn")
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
    monkeypatch.setattr(DEVICE_CLOUD, "fetch_available_resources", lambda _env: [])
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
