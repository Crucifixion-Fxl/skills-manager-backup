#!/usr/bin/env python3
"""Device Cloud 测试计划、用例查询、取消和失败诊断的统一入口。

用户只需要提供 App 链接和用例范围。脚本会先解析 kb-tests 中真实匹配的
Scenario，完成资源预检和单用例保护，然后才允许创建 TestPlan/Job。
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import itertools
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.parse
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from device_cloud_auth import DeviceCloudAuthClient, server_base_url  # noqa: E402
from trigger_cloud_job import (  # noqa: E402
    ENV_ENDPOINTS,
    batch_mode,
    gql,
    parse_args as parse_trigger_args,
)

TRIGGER_SCRIPT = SCRIPTS_DIR / "trigger_cloud_job.py"
DIAGNOSTICS_SCRIPT = SCRIPTS_DIR / "device_cloud_diagnostics.py"
RUNTIME_DEFAULTS_FILE = SCRIPTS_DIR.parent / "references" / "runtime-defaults.json"
RUN_TESTS_SCRIPT = "run_tests.py"


def _configure_utf8_output() -> None:
    """Keep streamed Client output printable on Windows GBK consoles."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def _utf8_child_env() -> dict[str, str]:
    """Force helper CLIs to emit Unicode safely when PowerShell redirects IO."""
    child_env = os.environ.copy()
    child_env["PYTHONIOENCODING"] = "utf-8"
    return child_env


def _load_tests_repo() -> str:
    configured = os.environ.get("KB_TESTS_REPO")
    if configured:
        return configured
    try:
        defaults = json.loads(RUNTIME_DEFAULTS_FILE.read_text(encoding="utf-8"))
        return str(defaults["kbTestsRepository"])
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(
            f"无法读取测试仓库默认配置：{RUNTIME_DEFAULTS_FILE}"
        ) from exc


TESTS_REPO = _load_tests_repo()
TESTS_REF = "main"
TESTS_MODULE = "kb-tests"
APP_TYPE = "kb-tests"
MANAGED_CREDENTIAL_KEYS = {
    "GITLAB_TOKEN", "CLIENT_GITLAB_TOKEN", "PIR_SIGN_SECRET", "RP_API_KEY",
    "LITELLM_API_KEY", "LANGCHAIN_API_KEY", "REDIS_PASSWORD", "CLOUD_AUTH_TOKEN",
    "GOOGLE_API_KEY", "OPENAI_API_KEY", "PACKAGE_API_TOKEN",
}

_VERSION_RE = re.compile(r"\d{1,5}\.\d{1,5}\.\d{1,5}")
_ENV_RE = re.compile(r"(?:^|[_/-])(prod|test|pre)(?=[_.\-/]|$)", re.IGNORECASE)
_COMMIT_RE = re.compile(r"(?:prod|test|pre)_([0-9a-f]{7,40})[_.]", re.IGNORECASE)
_BUILT_AT_RE = re.compile(r"(20\d{6})[-_](\d{6})")
_CAMERA_REFERENCE_RE = re.compile(r"(?<![a-z])camera(?![a-z])|摄像机|相机", re.I)
_PHONE_ALIAS_PATTERNS = (
    re.compile(
        r"\bon\s+([A-Za-z][A-Za-z0-9_-]*)"
        r"(?:\s+within\s+\d+(?:\.\d+)?\s+seconds?)?\s*$",
        re.I | re.M,
    ),
    re.compile(
        r"(?:我卸载|我远程重新安装|我处理|我关闭|我点击|我等待)\s+"
        r"([A-Za-z][A-Za-z0-9_-]*)\b"
    ),
)
_RESOURCE_TYPES = {"PHONE", "BATTERYCAM", "PLUGINCAM"}

RESOURCE_CAPACITY_QUERY = """
query DeviceCloudResourceCapacity($first: Int!) {
  phoneConnection(first: $first, filter: {online: true}) {
    edges { node { resourceType online jobId connection { platform } } }
  }
  deviceConnection(first: $first, filter: {online: true}) {
    edges { node { resourceType online jobId deviceModel } }
  }
}
"""


def _scenario_name(line: str) -> str | None:
    stripped = line.strip()
    for prefix in ("Scenario Outline", "Scenario", "场景大纲", "场景"):
        marker = prefix + ":"
        if stripped.lower().startswith(marker.lower()):
            name = stripped[len(marker):].strip()
            return name or None
    return None


def _resource_row(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not (stripped.startswith("|") and stripped.endswith("|")):
        return None
    cells = [cell.strip() for cell in stripped[1:-1].split("|")]
    if len(cells) < 2:
        return None
    name = cells[0]
    resource_type = cells[1].upper()
    if not name or name.lower() == "name" or resource_type not in _RESOURCE_TYPES:
        return None
    return name, resource_type


def _resource_rows(lines: Iterable[str]) -> dict[str, str]:
    return dict(row for line in lines if (row := _resource_row(line)))


def _phone_aliases(text: str) -> tuple[str, ...]:
    aliases = {
        match.group(1)
        for pattern in _PHONE_ALIAS_PATTERNS
        for match in pattern.finditer(text)
        if match.group(1).lower() != "camera"
    }
    return tuple(sorted(aliases))


def _feature_name(lines: list[str], fallback: str) -> str:
    for line in lines:
        stripped = line.strip()
        for prefix in ("Feature:", "功能:"):
            if stripped.lower().startswith(prefix.lower()):
                return stripped[len(prefix):].strip() or fallback
    return fallback


def _scenario_tags(lines: list[str], start: int) -> tuple[str, ...]:
    tag_lines: list[str] = []
    cursor = start - 1
    while cursor >= 0 and (
        not lines[cursor].strip() or lines[cursor].lstrip().startswith("@")
    ):
        if lines[cursor].lstrip().startswith("@"):
            tag_lines.append(lines[cursor].strip())
        cursor -= 1
    return tuple(
        token
        for line in reversed(tag_lines)
        for token in line.split()
        if token.startswith("@")
    )


def _effective_scenario_tags(lines: list[str], start: int) -> tuple[str, ...]:
    """Apply Gherkin Feature/Rule tag inheritance while preserving tag order."""
    feature_index = next(
        (
            index
            for index in range(start - 1, -1, -1)
            if lines[index].strip().lower().startswith(("feature:", "功能:"))
        ),
        -1,
    )
    feature_tags = _scenario_tags(lines, feature_index) if feature_index >= 0 else ()
    rule_index = next(
        (
            index
            for index in range(start - 1, feature_index, -1)
            if lines[index].strip().lower().startswith(("rule:", "规则:"))
        ),
        -1,
    )
    rule_tags = _scenario_tags(lines, rule_index) if rule_index >= 0 else ()
    inherited = (
        *feature_tags,
        *rule_tags,
        *_scenario_tags(lines, start),
    )
    return tuple(dict.fromkeys(inherited))


@dataclass(frozen=True)
class AppConfig:
    app_url: str
    app_family: str
    app_type: str
    app_name: str
    package_name: str
    platform: str
    app_environment: str
    version: str
    build_number: str | None = None
    commit: str | None = None
    built_at: str | None = None

    def runtime_env(self, country: str) -> dict[str, str]:
        return {
            "DEVIUM_APP_URL": self.app_url,
            "DEVIUM_APP_NAME": self.app_name,
            "DEVIUM_PACKAGE_NAME": self.package_name,
            "DEVIUM_VERSION": self.version,
            "DEVIUM_ENV": self.app_environment,
            "DEVIUM_COUNTRY": country,
        }


@dataclass(frozen=True)
class CaseInfo:
    uid: str | None
    name: str
    feature: str
    file: str
    line: int
    tags: tuple[str, ...]
    phone_aliases: tuple[str, ...]
    camera_referenced: bool
    camera_types: tuple[str, ...]
    resource_source: str

    def public(self) -> dict:
        return {
            "uid": self.uid,
            "name": self.name,
            "feature": self.feature,
            "file": self.file,
            "line": self.line,
            "phoneAliases": list(self.phone_aliases),
            "cameraReferenced": self.camera_referenced,
            "cameraTypes": list(self.camera_types),
            "resourceSource": self.resource_source,
        }


def parse_app_url(raw_url: str) -> AppConfig:
    """从 Kiwibit 安装包链接提取确定性运行参数。"""
    parsed = urllib.parse.urlparse(raw_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("App 链接必须是完整的 http/https URL")

    decoded_path = urllib.parse.unquote(parsed.path)
    filename = Path(decoded_path).name
    identity_text = f"{decoded_path}/{filename}".lower()
    if "kiwibit_android" not in identity_text and "kiwibit/android" not in identity_text:
        raise ValueError("当前 Skill 只支持可识别的 Kiwibit Android 安装包链接")
    if not (filename.lower().endswith(".apk") or "android" in identity_text):
        raise ValueError("当前仅支持 Android APK")

    versions = list(dict.fromkeys(_VERSION_RE.findall(decoded_path)))
    if len(versions) != 1:
        raise ValueError("App 链接中必须能唯一识别 x.y.z 版本号")
    env_match = _ENV_RE.search(decoded_path)
    if env_match is None:
        raise ValueError("App 链接中无法识别 _prod_、_test_ 或 _pre_ 业务环境")

    version_marker = versions[0] + "-"
    build_tail = decoded_path.partition(version_marker)[2]
    build_number = build_tail.split("_", 1)[0] if build_tail else ""
    if not build_number.isdigit():
        build_number = None
    commit_match = _COMMIT_RE.search(decoded_path)
    built_at_match = _BUILT_AT_RE.search(decoded_path)
    built_at = None
    if built_at_match:
        built_at = f"{built_at_match.group(1)}T{built_at_match.group(2)}"
    return AppConfig(
        app_url=raw_url,
        app_family="Kiwibit_Android",
        app_type=APP_TYPE,
        app_name="BAPP",
        package_name="com.kb.kiwibit",
        platform="android",
        app_environment=env_match.group(1).lower(),
        version=versions[0],
        build_number=build_number,
        commit=commit_match.group(1) if commit_match else None,
        built_at=built_at,
    )


def _run_git(arguments: list[str], cwd: Path | None = None) -> None:
    result = subprocess.run(
        ["git", *arguments], cwd=cwd, text=True, capture_output=True, check=False,
        encoding="utf-8", errors="replace",
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise ValueError(
            "无法读取 kb-tests；请确认 GitLab 权限，或使用 --cases-root 指向本地仓库"
            + (f"（{detail[-1]}）" if detail else "")
        )


def _git_output(arguments: list[str], cwd: Path) -> str:
    result = subprocess.run(
        ["git", *arguments], cwd=cwd, text=True, capture_output=True, check=False,
        encoding="utf-8", errors="replace",
    )
    if result.returncode:
        raise ValueError(
            "用例预检必须来自可追溯的 kb-tests Git 提交；"
            "请使用仓库目录，或移除 --cases-root 让 Skill 获取指定分支"
        )
    return result.stdout.strip()


def _tests_snapshot(root: Path, requested_ref: str) -> dict[str, str]:
    """Pin the catalog and cloud execution to the same clean Git commit."""
    if root == Path("<server-feature-id>"):
        root = resolve_cases_root(None, requested_ref, False)
    commit = _git_output(["rev-parse", "HEAD"], root)
    remote = _git_output(["ls-remote", "--exit-code", "origin", requested_ref], root)
    remote_commits = {line.split()[0] for line in remote.splitlines() if line.split()}
    if commit not in remote_commits:
        raise ValueError(
            f"本地 kb-tests 提交 {commit[:12]} 不是远端 {requested_ref} 的当前提交；"
            "请更新、提交并推送后重新预检"
        )
    dirty = _git_output(["status", "--porcelain", "--untracked-files=all", "--", "features"], root)
    if dirty:
        raise ValueError(
            "kb-tests/features 存在未提交改动，无法保证预检与云端执行一致；"
            "请提交并推送后重新预检"
        )
    digest = hashlib.sha256()
    for path in sorted((root / "features").rglob("*.feature")):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return {
        "requestedRef": requested_ref,
        "resolvedCommit": commit,
        "catalogDigest": digest.hexdigest(),
    }


def _local_client_snapshot(root: Path) -> dict[str, str]:
    commit = _git_output(["rev-parse", "HEAD"], root)
    dirty = _git_output(["status", "--porcelain", "--untracked-files=all"], root)
    if dirty:
        raise ValueError("device-cloud-client 存在未提交改动；请提交后重新预检")
    digest = hashlib.sha256()
    candidates = [root / RUN_TESTS_SCRIPT]
    candidates.extend(sorted((root / "devium_clients").rglob("*.py")))
    for path in candidates:
        if path.is_file():
            digest.update(path.relative_to(root).as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    return {
        "root": str(root),
        "commit": commit,
        "contentDigest": digest.hexdigest(),
    }


def resolve_cases_root(explicit: str | None, tests_ref: str, refresh: bool) -> Path:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    if os.environ.get("KB_TESTS_ROOT"):
        candidates.append(Path(os.environ["KB_TESTS_ROOT"]).expanduser())
    current = Path.cwd().resolve()
    candidates.extend(parent / "kb-tests" for parent in (current, *current.parents))
    for candidate in candidates:
        if (candidate / "features").is_dir():
            return candidate.resolve()
    if explicit:
        raise ValueError(f"--cases-root 不是有效的 kb-tests 目录：{explicit}")

    cache_base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".cache")
    cache_root = cache_base / "device-cloud-test-management" / "kb-tests"
    if not (cache_root / ".git").is_dir():
        cache_root.parent.mkdir(parents=True, exist_ok=True)
        _run_git([
            "clone", "--depth", "1", "--branch", tests_ref, TESTS_REPO, str(cache_root)
        ])
    elif refresh:
        _run_git(["fetch", "--depth", "1", "origin", tests_ref], cache_root)
        _run_git(["checkout", "--detach", "FETCH_HEAD"], cache_root)
    return cache_root.resolve()


def load_case_catalog(root: Path) -> list[CaseInfo]:
    cases: list[CaseInfo] = []
    for feature_path in sorted((root / "features").rglob("*.feature")):
        text = feature_path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        scenario_indexes = [i for i, line in enumerate(lines) if _scenario_name(line)]
        if not scenario_indexes:
            continue
        feature_name = _feature_name(lines, feature_path.stem)
        background_lines = lines[:scenario_indexes[0]]
        background_text = "\n".join(background_lines)
        background_resources = _resource_rows(background_lines)

        for position, start in enumerate(scenario_indexes):
            end = scenario_indexes[position + 1] if position + 1 < len(scenario_indexes) else len(lines)
            scenario_name = _scenario_name(lines[start])
            assert scenario_name is not None
            scenario_tags = _scenario_tags(lines, start)
            tags = _effective_scenario_tags(lines, start)
            uid_tag = next((tag for tag in scenario_tags if tag.startswith("@uid=")), None)
            uid = uid_tag.partition("=")[2] if uid_tag else None
            scenario_lines = lines[start:end]
            body = "\n".join(scenario_lines)
            resources = {**background_resources, **_resource_rows(scenario_lines)}
            combined_text = background_text + "\n" + body
            explicit_phone_aliases = {
                name for name, resource_type in resources.items() if resource_type == "PHONE"
            }
            phone_aliases = tuple(sorted(explicit_phone_aliases | set(_phone_aliases(combined_text))))
            camera_types = tuple(sorted({
                resource_type
                for name, resource_type in resources.items()
                if name.lower() == "camera" or resource_type in {"BATTERYCAM", "PLUGINCAM"}
            } - {"PHONE"}))
            camera_referenced = bool(camera_types or _CAMERA_REFERENCE_RE.search(combined_text))
            cases.append(CaseInfo(
                uid=uid,
                name=scenario_name,
                feature=feature_name,
                file=feature_path.relative_to(root).as_posix(),
                line=start + 1,
                tags=tags,
                phone_aliases=phone_aliases,
                camera_referenced=camera_referenced,
                camera_types=camera_types,
                resource_source=(
                    "feature/scenario resource table" if resources else "case device references"
                ),
            ))
    return cases


class _TagExpression:
    def __init__(self, expression: str, tags: Iterable[str]) -> None:
        self.tokens = re.findall(r"\(|\)|\band\b|\bor\b|\bnot\b|@[^\s()]+", expression, re.I)
        self.index = 0
        self.tags = set(tags)

    def evaluate(self) -> bool:
        if not self.tokens:
            return False
        value = self._or()
        if self.index != len(self.tokens):
            raise ValueError("无法解析 --tags 表达式")
        return value

    def _or(self) -> bool:
        value = self._and()
        while self._peek("or"):
            self.index += 1
            right = self._and()
            value = value or right
        return value

    def _and(self) -> bool:
        value = self._factor()
        while self._peek("and"):
            self.index += 1
            right = self._factor()
            value = value and right
        return value

    def _factor(self) -> bool:
        if self._peek("not"):
            self.index += 1
            return not self._factor()
        if self.index < len(self.tokens) and self.tokens[self.index] == "(":
            self.index += 1
            value = self._or()
            if self.index >= len(self.tokens) or self.tokens[self.index] != ")":
                raise ValueError("--tags 表达式括号不匹配")
            self.index += 1
            return value
        if self.index >= len(self.tokens) or not self.tokens[self.index].startswith("@"):
            raise ValueError("无法解析 --tags 表达式")
        tag = self.tokens[self.index]
        self.index += 1
        return tag in self.tags

    def _peek(self, value: str) -> bool:
        return self.index < len(self.tokens) and self.tokens[self.index].lower() == value


def find_cases(catalog: list[CaseInfo], args: argparse.Namespace) -> list[CaseInfo]:
    submodule = getattr(args, "submodule", None)
    if submodule and not getattr(args, "module", None):
        raise ValueError("--submodule 必须与 --module 一起使用")
    if getattr(args, "uid", None):
        expected = args.uid.lstrip("@").removeprefix("uid=")
        matches = [case for case in catalog if case.uid == expected]
    elif getattr(args, "module", None):
        tag = f"@testCase_modules={args.module}"
        matches = [case for case in catalog if tag in case.tags]
        if submodule:
            submodule_tag = f"@testCase_submodule={submodule}"
            matches = [case for case in matches if submodule_tag in case.tags]
    elif getattr(args, "tags", None):
        matches = [case for case in catalog if _TagExpression(args.tags, case.tags).evaluate()]
    else:
        matches = list(catalog)
    text = getattr(args, "text", None)
    if text:
        needle = text.casefold()
        matches = [
            case for case in matches
            if needle in case.name.casefold() or needle in case.feature.casefold()
            or needle in (case.uid or "").casefold()
        ]
    return matches


def _selector_description(args: argparse.Namespace) -> dict:
    return {
        "uid": getattr(args, "uid", None),
        "module": getattr(args, "module", None),
        "submodule": getattr(args, "submodule", None),
        "tags": getattr(args, "tags", None),
        "featureId": getattr(args, "feature_id", None),
    }


def _preflight_cases(args: argparse.Namespace) -> tuple[Path, list[CaseInfo]]:
    if getattr(args, "submodule", None) and not getattr(args, "module", None):
        raise ValueError("--submodule 必须与 --module 一起使用")
    if getattr(args, "feature_id", None) is not None:
        if not getattr(args, "allow_multiple", False):
            raise ValueError("--feature-id 无法在创建前确认场景数；必须明确添加 --allow-multiple")
        return Path("<server-feature-id>"), []
    root = resolve_cases_root(args.cases_root, args.tests_ref, args.refresh_cases)
    matches = find_cases(load_case_catalog(root), args)
    if not matches:
        raise ValueError("未找到匹配用例；不会创建 TestPlan")
    if getattr(args, "uid", None) and len(matches) != 1:
        raise ValueError(f"UID 必须唯一，实际匹配 {len(matches)} 条；不会创建 TestPlan")
    if len(matches) != 1 and not getattr(args, "allow_multiple", False):
        names = "；".join(f"{case.uid or '-'} {case.name}" for case in matches[:10])
        suffix = "……" if len(matches) > 10 else ""
        raise ValueError(
            f"当前选择器匹配 {len(matches)} 条用例：{names}{suffix}。"
            "请改用 --uid，或明确添加 --allow-multiple"
        )
    return root, matches


def _validate_resource_list(resources: list[dict], camera_required: bool) -> list[dict]:
    if not resources:
        raise ValueError("资源组不能为空")
    for item in resources:
        if not isinstance(item, dict) or not item.get("name") or not item.get("type"):
            raise ValueError("每项资源必须包含 name 和 type")
    resource_types = {str(item["type"]).upper() for item in resources}
    if "PHONE" not in resource_types:
        raise ValueError("资源组必须包含 PHONE")
    if camera_required and not resource_types.intersection({"BATTERYCAM", "PLUGINCAM"}):
        raise ValueError("用例引用 camera，但最终资源组没有相机；不会创建 TestPlan")
    return resources


def fetch_available_resources(
    device_cloud_env: str, auth_client: DeviceCloudAuthClient | None = None
) -> list[dict]:
    """读取实时资源容量，但不返回资源标识或连接信息。"""
    endpoint = ENV_ENDPOINTS[device_cloud_env]
    if auth_client is None:
        auth = DeviceCloudAuthClient(server_base_url(endpoint))
        auth.authenticate()
    else:
        auth = auth_client
    data = gql(endpoint, auth, RESOURCE_CAPACITY_QUERY, {"first": 1000})
    resources: list[dict] = []
    for edge in (data.get("phoneConnection") or {}).get("edges") or []:
        node = edge.get("node") or {}
        resources.append({
            "type": "PHONE",
            "platform": ((node.get("connection") or {}).get("platform") or "").lower() or None,
            "available": bool(node.get("online") and node.get("jobId") is None),
        })
    for edge in (data.get("deviceConnection") or {}).get("edges") or []:
        node = edge.get("node") or {}
        resource_type = str(node.get("resourceType") or "").upper()
        if resource_type in {"BATTERYCAM", "PLUGINCAM"}:
            resources.append({
                "type": resource_type,
                "platform": None,
                "deviceModel": node.get("deviceModel"),
                "available": bool(node.get("online") and node.get("jobId") is None),
            })
    return resources


def _authenticate_and_fetch_available_resources(
    device_cloud_env: str,
) -> tuple[list[dict], DeviceCloudAuthClient, str]:
    endpoint = ENV_ENDPOINTS[device_cloud_env]
    auth_client = DeviceCloudAuthClient(server_base_url(endpoint))
    session = auth_client.authenticate()
    resources = fetch_available_resources(device_cloud_env, auth_client)
    return resources, auth_client, session.email


def _run_batch_with_reused_auth(
    trigger_args: argparse.Namespace,
    auth_client: DeviceCloudAuthClient,
    launched_by: str,
) -> int:
    """Preserve the former subprocess exit-code contract for in-process runs."""
    try:
        return batch_mode(
            trigger_args,
            auth_client=auth_client,
            launched_by=launched_by,
        )
    except SystemExit as error:
        if error.code in (None, 0):
            return 0
        if not isinstance(error.code, int):
            print(error.code, file=sys.stderr)
            return 1
        return error.code


def resource_requirements(
    profile: str,
    resource_json: str | None,
    cases: list[CaseInfo],
) -> list[dict]:
    camera_required = any(case.camera_referenced for case in cases)
    if resource_json:
        value = json.loads(resource_json)
        if not isinstance(value, list):
            raise ValueError("--resource-json 必须是 JSON 数组")
        return _validate_resource_list(value, camera_required)

    phone = {"name": "phone", "type": "PHONE", "conditions": {"platform": "$eq:android"}}
    if profile == "phone":
        return _validate_resource_list([phone], camera_required)
    if profile == "auto" and not camera_required:
        return [phone]
    if profile == "phone+battery-camera":
        camera_type = "BATTERYCAM"
    elif profile == "phone+plugin-camera":
        camera_type = "PLUGINCAM"
    else:
        inferred = {kind for case in cases for kind in case.camera_types}
        if len(inferred) != 1:
            detail = ", ".join(sorted(inferred)) or "用例未声明相机类型"
            raise ValueError(
                f"无法唯一确定相机类型（{detail}）；请明确指定 --resource-profile"
            )
        camera_type = next(iter(inferred))
    return _validate_resource_list(
        [phone, {"name": "camera", "type": camera_type, "conditions": {}}],
        camera_required,
    )


def _logical_resources(
    cases: list[CaseInfo], base_resources: list[dict], resource_json: str | None
) -> list[dict]:
    if resource_json:
        required_names = {alias for case in cases for alias in case.phone_aliases}
        supplied_names = {str(item["name"]) for item in base_resources}
        missing = sorted(required_names - supplied_names)
        if missing:
            raise ValueError("--resource-json 缺少用例引用的手机别名：" + ", ".join(missing))
        return base_resources
    aliases = sorted({alias for case in cases for alias in case.phone_aliases}) or ["phone"]
    phones = [
        {"name": alias, "type": "PHONE", "conditions": {"platform": "$eq:android"}}
        for alias in aliases
    ]
    camera_required = any(case.camera_referenced for case in cases)
    cameras = (
        [item for item in base_resources if item["type"] != "PHONE"]
        if camera_required else []
    )
    return _validate_resource_list(
        phones + cameras, camera_required
    )


def _capacity_snapshot(available_resources: list[dict] | None) -> dict | None:
    if available_resources is None:
        return None
    by_type = {resource_type: 0 for resource_type in _RESOURCE_TYPES}
    by_model: dict[str, int] = {}
    for item in available_resources:
        resource_type = str(item.get("type") or "").upper()
        if not item.get("available") or resource_type not in by_type:
            continue
        if resource_type == "PHONE" and str(item.get("platform") or "").lower() != "android":
            continue
        by_type[resource_type] += 1
        model = str(item.get("deviceModel") or "").strip()
        if model:
            by_model[f"{resource_type}:{model}"] = by_model.get(f"{resource_type}:{model}", 0) + 1
    return {"byType": by_type, "byModel": by_model}


def _condition_value(expression: object, operator: str) -> str | None:
    prefix = f"${operator}:"
    text = str(expression)
    return text[len(prefix):] if text.startswith(prefix) else None


def _matches_requirement(resource: dict, requirement: dict) -> bool:
    if str(resource.get("type") or "").upper() != str(requirement["type"]).upper():
        return False
    for key, expression in (requirement.get("conditions") or {}).items():
        field = {"platform": "platform", "device_model": "deviceModel"}.get(key)
        if field is None:
            raise ValueError(f"实时资源预检暂不支持条件 {key}；不会创建 TestPlan")
        actual = str(resource.get(field) or "").casefold()
        expected = _condition_value(expression, "eq")
        if expected is not None and actual != expected.casefold():
            return False
        choices = _condition_value(expression, "in")
        if choices is not None and actual not in {
            choice.strip().casefold() for choice in choices.split(",") if choice.strip()
        }:
            return False
        if expected is None and choices is None:
            raise ValueError(
                f"实时资源预检暂不支持条件表达式 {key}={expression}；不会创建 TestPlan"
            )
    return True


def _allocate_job(job: dict, resources: list[dict]) -> list[dict] | None:
    remaining = [item for item in resources if item.get("available")]
    requirements = sorted(
        job["resources"], key=lambda item: len(item.get("conditions") or {}), reverse=True
    )
    for requirement in requirements:
        index = next(
            (i for i, item in enumerate(remaining) if _matches_requirement(item, requirement)),
            None,
        )
        if index is None:
            return None
        remaining.pop(index)
    return remaining


def _planned_concurrency(
    jobs: list[dict], available_resources: list[dict] | None, maximum: int
) -> tuple[int, dict | None]:
    snapshot = _capacity_snapshot(available_resources)
    if available_resources is None:
        return min(maximum, len(jobs)), None
    remaining = list(available_resources)
    admitted = 0
    for demand in sorted(
        jobs, key=lambda item: len(item["resources"]),
    ):
        if admitted >= maximum:
            break
        allocated = _allocate_job(demand, remaining)
        if allocated is not None:
            admitted += 1
            remaining = allocated
    return admitted, snapshot


def _validate_live_capacity(
    jobs: list[dict], available_resources: list[dict] | None
) -> None:
    if available_resources is None:
        return
    for job in jobs:
        if _allocate_job(job, available_resources) is None:
            raise ValueError(
                f"Job“{job['name']}”没有满足全部条件的实时空闲资源；未创建 TestPlan"
            )


def _feature_jobs(
    matches: list[CaseInfo], profile: str, resource_json: str | None
) -> list[dict]:
    jobs: list[dict] = []
    for _, group in itertools.groupby(matches, key=lambda case: case.file):
        feature_cases = list(group)
        missing_uid = [case.name for case in feature_cases if not case.uid]
        if missing_uid:
            raise ValueError("多用例计划要求每个 Scenario 都有 @uid：" + "；".join(missing_uid))
        base_resources = resource_requirements(profile, resource_json, feature_cases)
        jobs.append({
            "name": feature_cases[0].feature,
            "tags": " or ".join(f"@uid={case.uid}" for case in feature_cases),
            "resources": _logical_resources(feature_cases, base_resources, resource_json),
        })
    return jobs


def _confirmation_token(preview: dict) -> str:
    """绑定用户可见且会影响执行的预检内容。"""
    confirmed = {
        "deviceCloudEnvironment": preview["deviceCloudEnvironment"],
        "app": preview["app"],
        "tests": {
            "repository": preview["tests"]["repository"],
            "ref": preview["tests"]["ref"],
            "resolvedCommit": preview["tests"]["resolvedCommit"],
            "catalogDigest": preview["tests"]["catalogDigest"],
            "module": preview["tests"]["module"],
        },
        "selection": preview["selection"],
        "resourceGroups": preview["resourceGroups"],
        "execution": preview["execution"],
        "willCreate": preview["willCreate"],
        "planName": preview["planName"],
        "localClient": preview.get("localClient"),
    }
    canonical = json.dumps(
        confirmed, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def safe_plan_name(config: AppConfig, selector_label: str, explicit: str | None) -> str:
    if explicit:
        return explicit
    normalized = re.sub(r"[^0-9A-Za-z._-]+", "-", selector_label).strip("-") or "case"
    app_identity = re.sub(r"_(?:android|ios)$", "", config.app_family, flags=re.I)
    normalized_app = re.sub(r"[^0-9A-Za-z]+", "-", app_identity).strip("-").lower()
    app_slug = {"kiwibit": "kb"}.get(normalized_app, normalized_app)
    if not app_slug:
        app_slug = re.sub(r"[^0-9A-Za-z]+", "-", config.app_name).strip("-").lower()
    return f"ai-{app_slug or 'app'}-{config.version}-{normalized}-{dt.datetime.now():%Y%m%d-%H%M%S}"


def build_trigger_command(
    args: argparse.Namespace, available_resources: list[dict] | None = None
) -> tuple[list[str], dict]:
    config = parse_app_url(args.app_url)
    cases_root, matches = _preflight_cases(args)
    if (
        args.feature_id is not None
        and args.resource_profile == "auto"
        and not args.resource_json
    ):
        raise ValueError(
            "--feature-id 无法读取本地用例元数据来推断资源；"
            "请明确指定 --resource-profile phone、phone+battery-camera、"
            "phone+plugin-camera，或提供 --resource-json"
        )
    if args.feature_id is not None:
        tags, feature_id, selector_label = "", args.feature_id, f"feature-{args.feature_id}"
    elif len(matches) == 1 and matches[0].uid:
        # 即使用户用 module/text 找到唯一用例，执行时也收窄为 UID，避免仓库变化后误跑多条。
        tags, feature_id = f"@uid={matches[0].uid}", None
        selector_label = f"uid-{matches[0].uid[-8:]}"
    elif args.uid:
        clean_uid = args.uid.lstrip("@").removeprefix("uid=")
        tags, feature_id, selector_label = f"@uid={clean_uid}", None, f"uid-{clean_uid[-8:]}"
    elif args.module:
        submodule = getattr(args, "submodule", None)
        tags = f"@testCase_modules={args.module}"
        selector_label = args.module
        if submodule:
            tags += f" and @testCase_submodule={submodule}"
            selector_label += f"-{submodule}"
        feature_id = None
    else:
        tags, feature_id, selector_label = args.tags, None, "tags"

    plan_name = safe_plan_name(config, selector_label, args.plan_name)
    tests_snapshot = _tests_snapshot(cases_root, args.tests_ref)
    if args.feature_id is not None:
        resources = resource_requirements(
            args.resource_profile, args.resource_json, matches
        )
        job = [{"name": selector_label, "tags": tags, "resources": resources}]
    else:
        job = _feature_jobs(matches, args.resource_profile, args.resource_json)
    concurrency, capacity = _planned_concurrency(
        job, available_resources, args.max_concurrency
    )
    _validate_live_capacity(job, available_resources)
    if args.no_wait and len(job) > 1:
        raise ValueError("--no-wait 不支持多 Job；否则会绕过预检并发上限")
    command = [
        sys.executable, str(TRIGGER_SCRIPT), "--env", args.device_cloud_env,
        "--app-type", APP_TYPE, "--tests-repo", TESTS_REPO,
        "--tests-ref", tests_snapshot["resolvedCommit"], "--tests-module", TESTS_MODULE,
        "--plan-name", plan_name, "--priority", str(args.priority),
        "--poll-interval", str(args.poll_interval), "--timeout", str(args.timeout),
        "--queue-timeout", str(args.queue_timeout),
    ]
    if feature_id is not None:
        command += ["--feature-id", str(feature_id)]
    for key, value in config.runtime_env(args.country).items():
        command += ["--inject-env", f"{key}={value}"]
    if args.no_wait:
        command.append("--no-wait")
    if args.dry_run:
        command.append("--dry-run")

    preview = {
        "deviceCloudEnvironment": args.device_cloud_env,
        "app": asdict(config),
        "tests": {
            "repository": TESTS_REPO,
            "ref": args.tests_ref,
            **tests_snapshot,
            "module": TESTS_MODULE, "caseSource": str(cases_root),
        },
        "selection": {
            "requested": _selector_description(args),
            "effectiveTags": tags or None,
            "matchCount": len(matches) if matches else None,
            "cases": [case.public() for case in matches],
        },
        "resourceGroup": job[0]["resources"] if len(job) == 1 else None,
        "resourceGroups": [
            {"job": item["name"], "tags": item["tags"], "resources": item["resources"]}
            for item in job
        ],
        "execution": {
            "maxConcurrency": args.max_concurrency,
            "concurrency": concurrency,
            "capacitySnapshot": capacity,
            "country": args.country,
            "priority": args.priority,
            "pollIntervalSeconds": args.poll_interval,
            "timeoutSeconds": args.timeout,
            "queueTimeoutSeconds": args.queue_timeout,
            "noWait": args.no_wait,
        },
        "willCreate": {
            "testPlans": 1, "jobs": len(job),
            "scenarios": len(matches) if matches else None,
        },
        "planName": plan_name,
    }
    preview["confirmation"] = {
        "token": _confirmation_token(preview),
        "instruction": "确认当前预检后，将此 token 作为 --confirm 的值",
    }
    return command, {"job": job, "preview": preview}


def run_test(args: argparse.Namespace) -> int:
    available_resources, auth_client, launched_by = (
        _authenticate_and_fetch_available_resources(
            args.device_cloud_env
        )
    )
    command, context = build_trigger_command(args, available_resources)
    print(json.dumps({"preflight": context["preview"]}, ensure_ascii=False, indent=2), flush=True)
    if args.dry_run:
        return 0
    expected_confirmation = context["preview"]["confirmation"]["token"]
    if args.confirm != expected_confirmation:
        raise ValueError(
            "预检未确认或结果已变化；请重新展示当前预检，并将其中 token 作为 --confirm 的值"
        )
    concurrency = context["preview"]["execution"]["concurrency"]
    if concurrency < 1:
        raise ValueError("当前空闲资源不足，未创建 TestPlan；请稍后重新预检")
    with tempfile.TemporaryDirectory(prefix="device-cloud-test-") as temp_dir:
        batch_file = Path(temp_dir) / "job.json"
        batch_file.write_text(json.dumps(context["job"], ensure_ascii=False), encoding="utf-8")
        command += ["--batch", str(batch_file), "--concurrency", str(concurrency)]
        trigger_args = parse_trigger_args(command[2:])
        return _run_batch_with_reused_auth(
            trigger_args,
            auth_client,
            launched_by,
        )


def _resolve_local_client_root(explicit: str | None) -> Path:
    """Find a compatible device-cloud-client checkout and fail before creating a plan."""
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    configured = os.environ.get("DEVICE_CLOUD_CLIENT_ROOT")
    if configured:
        candidates.append(Path(configured).expanduser())
    for base in (Path.cwd(), *Path.cwd().parents):
        candidates.extend((base / "device-cloud-client", base / "cicd_build" / "device-cloud-client"))

    checked: set[Path] = set()
    for candidate in candidates:
        root = candidate.resolve()
        if root in checked:
            continue
        checked.add(root)
        if (root / RUN_TESTS_SCRIPT).is_file() and (root / "devium_clients").is_dir():
            return root
    raise ValueError(
        "未找到 device-cloud-client；请通过 --client-root 或 DEVICE_CLOUD_CLIENT_ROOT "
        "指定已更新到最新 main 的仓库目录"
    )


def _validate_local_runtime(client_root: Path, cases_root: Path) -> None:
    required_markers = {
        client_root / RUN_TESTS_SCRIPT: "--resources-config",
        client_root / "devium_clients" / "job" / "managed_local_plan.py": "ManagedLocalCloudPlanClient",
        cases_root / "features" / "environment.py": "ManagedLocalCloudPlanClient",
    }
    missing = [
        f"{path}（缺少 {marker}）"
        for path, marker in required_markers.items()
        if not path.is_file() or marker not in path.read_text(encoding="utf-8")
    ]
    if missing:
        raise ValueError(
            "本地 Client 运行链路版本过旧，不会创建 TestPlan；请更新 "
            "DEVT/device-cloud-client 和 DEVT/kb-tests 的 main。缺失："
            + "；".join(missing)
        )


def _validate_local_python_dependencies(client_root: Path) -> None:
    """Fail before SSO when the selected Python cannot load Client essentials."""
    probe = (
        "import behave; import PIL; "
        "from gql.transport.requests import RequestsHTTPTransport"
    )
    env = os.environ.copy()
    env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=client_root,
        env=env,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if result.returncode:
        detail = (result.stdout or "").strip().splitlines()
        reason = detail[-1] if detail else "unknown import failure"
        raise ValueError(
            "当前 Python 环境不能运行本地 Client；请使用 device-cloud-client "
            f"已同步依赖的虚拟环境后重试。依赖预检失败：{reason}"
        )


def build_local_client_command(
    args: argparse.Namespace,
    available_resources: list[dict] | None = None,
) -> tuple[list[str], dict, dict[str, str]]:
    if args.feature_id is not None:
        raise ValueError("本地 Client 不支持 --feature-id；请先查询并使用 UID")
    trigger_command, context = build_trigger_command(args, available_resources)
    context["_triggerCommand"] = trigger_command
    if len(context["job"]) != 1:
        raise ValueError("run-local 仅支持单 Job；多 Feature 请使用云端 run")
    preview = context["preview"]
    cases_root = Path(preview["tests"]["caseSource"]).resolve()
    client_root = _resolve_local_client_root(args.client_root)
    _validate_local_runtime(client_root, cases_root)
    preview["localClient"] = _local_client_snapshot(client_root)

    cloud_environment = "prod" if args.device_cloud_env == "prod-cn" else "staging"
    command = [
        sys.executable,
        str(client_root / RUN_TESTS_SCRIPT),
        "--project-root",
        str(cases_root),
        "--env",
        cloud_environment,
        "--mode",
        "legacy",
        "--plan-name",
        preview["planName"],
        "--tags",
        preview["selection"]["effectiveTags"],
    ]
    child_env = os.environ.copy()
    for key in MANAGED_CREDENTIAL_KEYS:
        child_env.pop(key, None)
    child_env.update(AppConfig(**preview["app"]).runtime_env(args.country))
    if args.device_cloud_env == "prod-cn":
        child_env.update({
            "RP_ENDPOINT": "https://reportportal.builder.addx.live",
            "RP_PROJECT": "builder_prod_cn",
        })
    else:
        child_env.update({
            "RP_ENDPOINT": "https://reportportal-staging.builder.addx.live",
            "RP_PROJECT": "builder_staging_cn",
        })
    child_env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
    preview["executionMode"] = "local-client-cloud-resources"
    preview["clientRoot"] = str(client_root)
    preview["confirmation"]["token"] = _confirmation_token(preview)
    return command, context, child_env


def _create_managed_local_job(
    args: argparse.Namespace, context: dict, child_env: dict[str, str], batch_file: Path
) -> dict:
    trigger_command = list(context.get("_triggerCommand") or build_trigger_command(args)[0])
    trigger_command.extend([
        "--client-mode", "legacy",
        "--metadata", json.dumps({"executionLocation": "LOCAL_CLIENT"}),
        "--allow-duplicate-plan",
        "--no-wait",
        "--batch", str(batch_file),
        "--concurrency", "1",
    ])
    process = subprocess.Popen(
        trigger_command,
        env=child_env,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    result = None
    assert process.stdout is not None
    for line in process.stdout:
        print(line, end="", flush=True)
        if line.startswith("[RESULT_JSON] "):
            result = json.loads(line[len("[RESULT_JSON] "):])
    return_code = process.wait()
    if return_code != 0:
        raise RuntimeError(f"Device Cloud 创建本地执行 Job 失败（exit={return_code}）")
    if not isinstance(result, dict):
        raise RuntimeError("Device Cloud 未返回 Plan/Job 结构化结果")
    job_ids = result.get("jobIds") or []
    if not result.get("planId") or len(job_ids) != 1:
        raise RuntimeError(
            "本地执行必须且只能绑定一个 Plan 和一个 Job；"
            f"实际结果 planId={result.get('planId')}, jobIds={job_ids}"
        )
    context["preview"]["created"] = {
        "planId": int(result["planId"]), "jobId": int(job_ids[0])
    }
    return result


def run_local_client(args: argparse.Namespace) -> int:
    available_resources = fetch_available_resources(args.device_cloud_env)
    command, context, child_env = build_local_client_command(args, available_resources)
    print(json.dumps({"preflight": context["preview"]}, ensure_ascii=False, indent=2), flush=True)
    if args.dry_run:
        return 0
    expected_confirmation = context["preview"]["confirmation"]["token"]
    if args.confirm != expected_confirmation:
        raise ValueError(
            "预检未确认或结果已变化；请重新展示当前预检，并将其中 token 作为 --confirm 的值"
        )
    _validate_local_python_dependencies(Path(context["preview"]["clientRoot"]))
    with tempfile.TemporaryDirectory(prefix="device-cloud-local-client-") as temp_dir:
        temp_root = Path(temp_dir)
        batch_file = temp_root / "job.json"
        batch_file.write_text(
            json.dumps(context["job"], ensure_ascii=False), encoding="utf-8"
        )
        result = _create_managed_local_job(args, context, child_env, batch_file)
        execution_file = temp_root / "execution.json"
        execution_file.write_text(json.dumps({
            "feishu": {},
            "resources": context["preview"]["resourceGroup"],
            "testDetail": {
                "testPlanId": int(result["planId"]),
                "jobId": int(result["jobIds"][0]),
                "clientMode": "legacy",
                "tags": context["preview"]["selection"]["effectiveTags"],
                "appType": APP_TYPE,
            },
        }, ensure_ascii=False), encoding="utf-8")
        command.extend(["--resources-config", str(execution_file)])
        process = subprocess.Popen(
            command,
            env=child_env,
            cwd=context["preview"]["clientRoot"],
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        fatal_markers = (
            "HOOK-ERROR in before_all:",
            "AuthenticationError:",
            "Exception ValueError: list.remove(x): x not in list",
            "Behave ValueError caught",
        )
        fatal_output = False
        scenario_started = False
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="", flush=True)
            stripped = line.lstrip()
            if "SCENARIO START:" in line or stripped.startswith("Scenario:"):
                scenario_started = True
            if not scenario_started:
                fatal_output = fatal_output or any(
                    marker in line for marker in fatal_markers
                )
        return_code = process.wait()
    if return_code == 0 and fatal_output:
        print(
            "本地 Client 在创建/执行计划前发生致命初始化错误；拒绝把其零退出码视为成功。",
            file=sys.stderr,
        )
        return 1
    return return_code


def search_cases(args: argparse.Namespace) -> int:
    root = resolve_cases_root(args.cases_root, args.tests_ref, args.refresh_cases)
    matches = find_cases(load_case_catalog(root), args)
    shown = matches[:args.limit]
    print(json.dumps({
        "caseSource": str(root),
        "testsRef": args.tests_ref,
        "matchCount": len(matches),
        "truncated": len(matches) > len(shown),
        "cases": [case.public() for case in shown],
    }, ensure_ascii=False, indent=2))
    return 0


def run_diagnostics(args: argparse.Namespace) -> int:
    forwarded = [sys.executable, str(DIAGNOSTICS_SCRIPT), "--env", args.device_cloud_env]
    forwarded.extend(args.diagnostic_args)
    return subprocess.run(forwarded, check=False, env=_utf8_child_env()).returncode


def run_cancel(args: argparse.Namespace) -> int:
    command = "cancel-job" if args.cancel_target == "job" else "cancel-plan"
    identifier = "--job-id" if args.cancel_target == "job" else "--plan-id"
    value = args.job_id if args.cancel_target == "job" else args.plan_id
    if value is None:
        raise ValueError(f"cancel {args.cancel_target} 必须提供 {identifier}")
    if args.cancel_target == "job" and args.plan_id is not None:
        raise ValueError("cancel job 不接受 --plan-id")
    if args.cancel_target == "plan" and args.job_id is not None:
        raise ValueError("cancel plan 不接受 --job-id")
    forwarded = [
        sys.executable, str(DIAGNOSTICS_SCRIPT), "--env", args.device_cloud_env,
        command, identifier, str(value), "--timeout", str(args.timeout),
    ]
    return subprocess.run(forwarded, check=False, env=_utf8_child_env()).returncode


def _add_case_source_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument("--tests-ref", default=TESTS_REF)
    command.add_argument("--cases-root", help="本地 kb-tests 根目录；缺省时自动查找或缓存克隆")
    command.add_argument("--refresh-cases", action="store_true", help="刷新缓存的 kb-tests")


def _add_run_arguments(command: argparse.ArgumentParser, *, local_client: bool = False) -> None:
    command.add_argument("--app-url", required=True)
    selectors = command.add_mutually_exclusive_group(required=True)
    selectors.add_argument("--uid", help="单条场景的 @uid 值（推荐）")
    selectors.add_argument("--module", help="模块标签；匹配多条时默认拒绝执行")
    selectors.add_argument("--tags", help="完整 Behave tag 表达式；匹配多条时默认拒绝执行")
    selectors.add_argument(
        "--feature-id", type=int,
        help="高级入口；必须同时明确 --allow-multiple 和非 auto 的资源配置",
    )
    command.add_argument("--submodule", help="二级模块标签；必须与 --module 一起使用")
    command.add_argument("--allow-multiple", action="store_true", help="明确允许匹配并执行多条场景")
    command.add_argument("--device-cloud-env", choices=("prod-cn", "staging-cn"), default="prod-cn")
    command.add_argument("--resource-profile", choices=(
        "auto", "phone", "phone+battery-camera", "phone+plugin-camera"
    ), default="auto")
    command.add_argument("--resource-json", help="特殊资源需求 JSON；仍会执行 camera 防呆校验")
    command.add_argument("--country", default="US")
    _add_case_source_arguments(command)
    command.add_argument("--plan-name")
    command.add_argument("--priority", type=int, default=5)
    command.add_argument("--poll-interval", type=int, default=5)
    command.add_argument("--timeout", type=int, default=1800)
    command.add_argument("--queue-timeout", type=int, default=7200)
    if local_client:
        command.set_defaults(no_wait=False)
    else:
        command.add_argument("--no-wait", action="store_true")
    command.add_argument("--dry-run", action="store_true")
    command.add_argument(
        "--confirm", metavar="PREFLIGHT_SHA256",
        help="携带已展示预检的 token，确认该快照并创建计划",
    )
    command.add_argument("--max-concurrency", type=int, default=4, help="最大并发 Job 数，默认 4")
    if local_client:
        command.add_argument("--client-root", help="device-cloud-client 仓库根目录")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)

    inspect = commands.add_parser("inspect-app", help="只解析 App 链接，不调用平台")
    inspect.add_argument("--app-url", required=True)

    search = commands.add_parser("search-cases", aliases=["list-cases"], help="查询 kb-tests 用例")
    search_selectors = search.add_mutually_exclusive_group()
    search_selectors.add_argument("--uid")
    search_selectors.add_argument("--module")
    search_selectors.add_argument("--tags")
    search.add_argument("--submodule", help="二级模块标签；必须与 --module 一起使用")
    search.add_argument("--text", help="按 UID、Feature 或 Scenario 名称模糊搜索")
    search.add_argument("--limit", type=int, default=50)
    _add_case_source_arguments(search)

    run = commands.add_parser("run", help="预检后由云端 Host 创建一个 TestPlan 和一个或多个 Job")
    _add_run_arguments(run)

    run_local = commands.add_parser("run-local", help="本地 Client 使用云端资源执行")
    _add_run_arguments(run_local, local_client=True)

    diagnose = commands.add_parser("diagnose", help="按强制 SOP 查询日志、时间线和附件")
    diagnose.add_argument("--device-cloud-env", choices=("prod-cn", "staging-cn"), default="prod-cn")
    diagnose.add_argument("diagnostic_args", nargs=argparse.REMAINDER)

    cancel = commands.add_parser("cancel", help="取消 Job 或 TestPlan 中所有未结束 Job")
    cancel.add_argument("--device-cloud-env", choices=("prod-cn", "staging-cn"), default="prod-cn")
    cancel.add_argument("cancel_target", choices=("job", "plan"))
    cancel.add_argument("--job-id", type=int)
    cancel.add_argument("--plan-id", type=int)
    cancel.add_argument("--timeout", type=int, default=60)
    return root


def main(argv: list[str] | None = None) -> int:
    _configure_utf8_output()
    args = parser().parse_args(argv)
    try:
        if args.command == "inspect-app":
            print(json.dumps(asdict(parse_app_url(args.app_url)), ensure_ascii=False, indent=2))
            return 0
        if args.command in {"search-cases", "list-cases"}:
            if args.limit < 1:
                raise ValueError("--limit 必须大于 0")
            return search_cases(args)
        if args.command == "run":
            if args.max_concurrency < 1:
                raise ValueError("--max-concurrency 必须大于 0")
            return run_test(args)
        if args.command == "run-local":
            if args.max_concurrency < 1:
                raise ValueError("--max-concurrency 必须大于 0")
            return run_local_client(args)
        if args.command == "cancel":
            return run_cancel(args)
        if not args.diagnostic_args:
            raise ValueError(
                "diagnose 后必须提供 full、job-report、timeline、logs、evidence 或 evidence-content"
            )
        return run_diagnostics(args)
    except ValueError as error:
        raise SystemExit(f"error: {error}") from None


if __name__ == "__main__":
    raise SystemExit(main())
