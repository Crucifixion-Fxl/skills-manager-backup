#!/usr/bin/env python3
"""
触发云测平台测试任务（创建 TestPlan + Job，可选轮询到结束）。

能力边界：本入口只负责触发云端 `device-cloud-client` 的 Behave 执行，Host
最终调用 `run_tests.py --mode server-opt`。它不支持 `devium-ai --mode implement`
的步骤生成/真机填充；implement 必须由 scenario-lifecycle 的 Scenario Runner
在安装了 `devium-ai` 的工作区执行，生成完成后再用本入口做云端严格回放。

认证与凭据边界：
    1. 脚本通过浏览器完成 Casdoor/飞书登录；访问令牌仅保存在当前进程内，
       刷新令牌进入操作系统安全凭据存储。
    2. Trigger 只传普通用例参数，不接受任何运行密钥。
    3. GitLab/PIR 凭据由 Host 托管；AI、ReportPortal、Redis 等执行凭据由
       Server 授权后交付。它们不会进入 Trigger 参数或本地 .env。

支持两种模式：

1. 单任务模式（默认）：创建 1 个 TestPlan + 1 个 Job
    ./scripts/trigger_cloud_job.py \\
      --app-type kb-tests \\
      --device-id R5CNA04E7QH \\
      --resource-name phone \\
      --tests-repo <kb-tests-repo-url> \\
      --tests-ref main \\
      --tags '@testCase_modules=用户登录 and @testCase_priority=P0' \\
      --inject-env DEVIUM_VERSION=2.17.0

2. 批量模式（--batch）：创建 1 个 TestPlan + N 个 Job，滑动窗口并发控制
    ./scripts/trigger_cloud_job.py \\
      --batch scripts/kb-tests-regression.json \\
      --concurrency 3 \\
      --app-type kb-tests \\
      --tests-repo <kb-tests-repo-url> \\
      --tests-ref main \\
      --baseline-env-file .vscode/.env \\
      --env-file kb-tests/.env \\
      --inject-env DEVIUM_VERSION=2.17.0 \\
      --inject-env DEVIUM_PACKAGE_NAME=com.kb.kiwibit \\
      --plan-name "regression-$(date +%%H%%M)"

环境变量注入（三层合并,优先级低→高,透传给容器 → client env-first override）：
    --baseline-env-file .vscode/.env               跨模块普通配置（不得包含密钥）
    --env-file kb-tests/.env                       模块特有 K=V（覆盖 baseline 同名项）
    --inject-env DEVIUM_APP_URL=http://...app.apk  单条注入（可重复，优先级最高）
    支持的 DEVIUM_* 变量（对应 config/resources.yml 字段，容器内 env 优先于 yml）：
      DEVIUM_APP_NAME      appName（如 KiwiBit / VicoHome）
      DEVIUM_APP_URL       appUrl（APK 下载地址）
      DEVIUM_VERSION       version（App 版本号）
      DEVIUM_COUNTRY       country（地区代码）
      DEVIUM_ENV           env（环境 staging/prod）
      DEVIUM_PACKAGE_NAME  packageName（如 com.kb.kiwibit）
      DEVIUM_FIRMWARE_VERSION  firmwareVersion（设备固件版本）

jobs.json 格式（--batch 参数）：
    JSON 数组，每个元素一个 job：
    [
      {
        "name": "用户登录",                           # job 名称，用于日志和汇总
        "tags": "@testCase_modules=用户登录 and ...",  # behave tag 过滤表达式
        "resources": [                                # 该 job 的资源需求列表
          {
            "name": "phone",                          # 资源名称（对应 resourceRequirements.name）
            "type": "PHONE",                          # 资源类型（PHONE/BATTERYCAM/PLUGINCAM/BX）
            "conditions": {                           # 资源筛选条件（对应 resource tags）
              "platform": "$eq:android",              # 格式：key: "$operator:value"
              "belong_to_room": "$eq:115"             # 限定深圳机房（排除开发者电脑）
            }
          }
        ]
      }
    ]
    conditions 常用 key（来自 resource tags）：
      platform          手机平台（android/ios）
      manufacturer      手机厂商（samsung/xiaomi）
      device_id         设备唯一标识（如 R5CNA04E7QH）
      device_model      设备型号（如 SM-A7160、CG625A1）
      belong_to_room    所属机房 ID（115=深圳机房，排除开发者电脑）
      belong_to_cabinet 所属机柜 ID（111-113,117,118=shenzhen-host-001~005）
    conditions 支持的 operator：$eq $ne $gt $lt $ge $le $in $regex $exists
    注意：含 # 的 tag key（如 cabinet#name）不能直接用，MySQL JSON path 不支持

    示例文件：scripts/kb-tests-regression.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

if os.name == "nt":
    # The server and Client return Chinese diagnostics and may include symbols
    # that are not representable by the legacy Windows GBK console encoding.
    # Keep monitoring alive even when those diagnostics contain such text.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Keep sibling imports working when callers load this script via importlib.
_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
from device_cloud_auth import DeviceCloudAuthClient, server_base_url  # noqa: E402

ENV_ENDPOINTS = {
    "prod-cn": "https://device-cloud-server.builder.addx.live/graphql",
    "staging-cn": "https://device-cloud-server-staging.builder.addx.live/graphql",
}

ENV_RUNTIME_DEFAULTS = {
    "staging-cn": {
        "BEHAVE_ENVIRONMENT": "staging",
        "DEVICE_CLOUD_SERVER_BASE_URL":
            "https://device-cloud-server-staging.builder.addx.live",
        "RP_ENDPOINT": "https://reportportal-staging.builder.addx.live",
        "RP_PROJECT": "builder_staging_cn",
        "REDIS_HOST": "host.docker.internal",
        "REDIS_PORT": "6379",
        "REDIS_DB": "0",
        "LITELLM_BASE_URL": "https://litellm.addx.live/",
        "LITELLM_MODEL": "dashscope/qwen3-vl-plus",
        "LITELLM_MULTIMODAL_EMBEDDING_MODEL":
            "gcp-a4xcloud-t/multimodalembedding@001",
        "LANGCHAIN_TRACING_V2": "true",
        "LANGCHAIN_ENDPOINT": "https://api.smith.langchain.com",
        "LANGCHAIN_PROJECT": "ai-testing",
    },
    "prod-cn": {
        "BEHAVE_ENVIRONMENT": "prod",
        "DEVICE_CLOUD_SERVER_BASE_URL":
            "https://device-cloud-server.builder.addx.live",
        "RP_ENDPOINT": "https://reportportal.builder.addx.live",
        "RP_PROJECT": "builder_prod_cn",
        "REDIS_HOST": "host.docker.internal",
        "REDIS_PORT": "6379",
        "REDIS_DB": "0",
        "LITELLM_BASE_URL": "https://litellm.addx.live/",
        "LITELLM_MODEL": "dashscope/qwen3-vl-plus",
        "LITELLM_MULTIMODAL_EMBEDDING_MODEL":
            "gcp-a4xcloud-t/multimodalembedding@001",
        "LANGCHAIN_TRACING_V2": "true",
        "LANGCHAIN_ENDPOINT": "https://api.smith.langchain.com",
        "LANGCHAIN_PROJECT": "ai-testing",
    },
}

RESULT_JSON_PREFIX = "[RESULT_JSON] "


class GraphQLRequestError(RuntimeError):
    """A recoverable Device Cloud GraphQL transport or response failure."""

TERMINAL_STATUSES = {
    "COMPLETE",
    "ABORTED_BY_WEB",
    "ABORTED_BY_CLIENT",
    "ABORTED_BY_HOST",
    "ABORTED_BY_SERVER",
    "FAILED_RESOURCE_NOT_AVAILABLE",
    "FAILED_RESOURCE_DISCONNECT",
    "FAILED_RESOURCE_ALLOCATION",
    "FAILED_JOB_WS_DISCONNECT",
    "FAILED_CLIENT_EXIT",
}
SUCCESS_STATUSES = {"COMPLETE"}


def job_fully_passed(job: dict) -> bool:
    """job 真正"通过" = 状态 COMPLETE 且所有 scenario 都通过。

    注意：job 状态 COMPLETE 仅表示 job 执行完毕，不代表用例通过。一个
    COMPLETE 但 passedScenarios < totalScenarios（如 0/2、1/2）的 job 是
    测试失败，必须计入 failed（见 device-cloud#15：批量成功判定只看
    COMPLETE 会把失败误报为 PASS）。
    """
    if job.get("status") not in SUCCESS_STATUSES:
        return False
    ps = job.get("passedScenarios")
    tot = job.get("totalScenarios")
    return ps is not None and tot is not None and tot > 0 and ps == tot


def _access_token(auth) -> str:
    return auth if isinstance(auth, str) else auth.access_token


def gql(endpoint: str, auth, query: str, variables: dict) -> dict:
    """Call GraphQL and refresh the in-memory user token once on HTTP 401."""
    body = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    for attempt in range(2):
        req = urllib.request.Request(
            endpoint, data=body, method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {_access_token(auth)}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as e:
            if e.code == 401 and attempt == 0 and hasattr(auth, "refresh"):
                auth.refresh()
                continue
            raise GraphQLRequestError(f"HTTP {e.code} from {endpoint}") from e
        except urllib.error.URLError as e:
            raise GraphQLRequestError(f"网络异常 {e.reason}") from e
    if payload.get("errors"):
        raise GraphQLRequestError(
            "GraphQL errors: " + json.dumps(payload["errors"], ensure_ascii=False)
        )
    return payload["data"]


CREATE_PLAN = """
mutation($in: TestPlanInput!) {
  createTestPlan(input: $in) { success message planId status }
}
"""

CREATE_JOB = """
mutation($in: CreateJobInput!) {
  createJob(input: $in) { id success message }
}
"""

QUERY_JOB = """
query($id: ID!) {
  job(id: $id) {
    id status startedAt completedAt errorMessage
    completedScenarios passedScenarios totalScenarios
    allocatedResources { success message }
  }
}
"""

FIND_PLANS = """
query($filter: TestPlanFilter) {
  testPlanConnection(first: 20, filter: $filter) {
    edges { node { id name status launchedBy } }
  }
}
"""


def find_existing_plan(endpoint, auth, name: str, launched_by: str) -> dict | None:
    """Return an exact same-user plan so retries do not create duplicate jobs."""
    data = gql(endpoint, auth, FIND_PLANS, {"filter": {
        "name": name,
        "launchedBy": launched_by,
    }})
    connection = data.get("testPlanConnection") or {}
    for edge in connection.get("edges") or []:
        plan = (edge or {}).get("node") or {}
        if plan.get("name") == name and plan.get("launchedBy") == launched_by:
            return plan
    return None


def create_plan_once(endpoint, auth, name, plan_type, launched_by,
                     *, allow_duplicate=False) -> tuple[str, bool]:
    """Create a plan once, or reuse the exact same user's existing plan."""
    if not allow_duplicate:
        existing = find_existing_plan(endpoint, auth, name, launched_by)
        if existing:
            return str(existing["id"]), False
    return create_plan(endpoint, auth, name, plan_type, launched_by), True


def create_plan(endpoint, token, name, plan_type, launched_by) -> str:
    data = gql(endpoint, token, CREATE_PLAN, {"in": {
        "name": name,
        "planType": plan_type,
        "launchedBy": launched_by,
        "status": "UNTESTED",
    }})
    res = data["createTestPlan"]
    if not res["success"]:
        sys.exit(f"error: createTestPlan failed: {res.get('message')}")
    return str(res["planId"])


def build_metadata(client_mode, app_type, tests_repo, tests_ref, tests_module,
                   extra_tags, extra_metadata, injected_env=None):
    """组装发给 createJob 的 metadata，env 字段透传给 host docker run -e。"""
    metadata = {"clientMode": client_mode, "appType": app_type}
    if extra_tags:
        metadata["tags"] = extra_tags
    env = {}
    if tests_repo:
        env["TESTS_REPO"] = tests_repo
    if tests_ref:
        env["TESTS_REF"] = tests_ref
    if tests_module:
        env["TESTS_MODULE"] = tests_module
    if injected_env:
        env.update(injected_env)
    if env:
        metadata["env"] = env
    if extra_metadata:
        # 用户的 --metadata 优先级最高，能 override 上面任何字段
        for k, v in extra_metadata.items():
            if k == "env" and isinstance(v, dict):
                metadata.setdefault("env", {}).update(v)
            else:
                metadata[k] = v
    return metadata


def resolve_tests_ref(tests_repo: str | None, tests_ref: str | None) -> str | None:
    """返回显式 ref，或从远端 HEAD 读取测试仓库的默认分支。

    Trigger 不再猜测 ``master``。未传 ``--tests-ref`` 时，把当前远端默认
    分支显式写入 metadata，使 Job 在仓库默认分支切换后仍可复现。
    """
    if not tests_repo or tests_ref:
        return tests_ref

    try:
        result = subprocess.run(
            ["git", "ls-remote", "--symref", tests_repo, "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        sys.exit(f"error: 无法读取测试仓库默认分支: {exc.__class__.__name__}")

    if result.returncode != 0:
        sys.exit("error: 无法读取测试仓库默认分支；请检查仓库访问权限或显式传入 --tests-ref")

    for line in result.stdout.splitlines():
        if line.startswith("ref: refs/heads/") and line.endswith("\tHEAD"):
            return line[len("ref: refs/heads/"):-len("\tHEAD")]

    sys.exit("error: 测试仓库未公开默认分支；请显式传入 --tests-ref")


def create_job(endpoint, token, plan_id, feature_id, metadata,
               job_type, priority, resource_type, count, launched_by,
               device_id=None, resource_name=None) -> int:
    conditions = []
    if device_id:
        conditions.append({"key": "device_id", "operator": "$eq", "value": device_id})
    job_input = {
        "testPlanId": int(plan_id),
        "jobType": job_type,
        "priority": int(priority),
        "resourceRequirements": [{
            "name": resource_name or f"{resource_type.lower()}-1",
            "resourceType": resource_type,
            "count": int(count),
            "conditions": conditions,
        }],
        "launchedBy": launched_by,
        "metadata": metadata,
    }
    if feature_id is not None:
        job_input["featureId"] = int(feature_id)
    data = gql(endpoint, token, CREATE_JOB, {"in": job_input})
    res = data["createJob"]
    if not res["success"]:
        sys.exit(f"error: createJob failed: {res.get('message')}")
    return int(res["id"])


def create_job_batch(endpoint, token, plan_id, metadata,
                     resource_requirements, job_type, priority,
                     launched_by, feature_id=None, name=None) -> int:
    """批量模式下创建 job，接受完整的 resourceRequirements 列表。"""
    job_input = {
        "testPlanId": int(plan_id),
        "jobType": job_type,
        "priority": int(priority),
        "resourceRequirements": resource_requirements,
        "launchedBy": launched_by,
        "metadata": metadata,
    }
    if name:
        job_input["name"] = name
    if feature_id is not None:
        job_input["featureId"] = int(feature_id)
    data = gql(endpoint, token, CREATE_JOB, {"in": job_input})
    res = data["createJob"]
    if not res["success"]:
        return -1  # 批量模式下不 sys.exit，返回 -1 表示失败
    return int(res["id"])


def build_resource_requirements(resources: list[dict]) -> list[dict]:
    """将 jobs.json 的 resources 转为 createJob 的 resourceRequirements。

    输入格式: {"name": "phone", "type": "PHONE", "conditions": {"platform": "$eq:android"}}
    输出格式: {"name": "phone", "resourceType": "PHONE", "conditions": [
                  {"key": "platform", "operator": "$eq", "value": "android"}
              ]}
    """
    requirements = []
    for res in resources:
        conditions = []
        for key, raw_value in res.get("conditions", {}).items():
            raw_value = str(raw_value)
            if ":" in raw_value:
                operator, _, value = raw_value.partition(":")
            else:
                operator, value = "$eq", raw_value
            conditions.append({"key": key, "operator": operator, "value": value})
        requirements.append({
            "name": res["name"],
            "resourceType": res["type"],
            "conditions": conditions,
        })
    return requirements


def poll_job(endpoint, token, job_id, interval, timeout) -> dict:
    deadline = time.monotonic() + timeout
    last_status = None
    while time.monotonic() < deadline:
        job = gql(endpoint, token, QUERY_JOB, {"id": str(job_id)})["job"]
        status = job.get("status")
        if status != last_status:
            ts = dt.datetime.now().strftime("%H:%M:%S")
            print(f"[{ts}] job#{job_id} status={status} "
                  f"scenarios={job.get('passedScenarios')}/{job.get('totalScenarios')}")
            last_status = status
        if status in TERMINAL_STATUSES:
            return job
        time.sleep(interval)
    sys.exit(f"error: 轮询超时 ({timeout}s)，job#{job_id} 仍处于 {last_status}")


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--env", choices=list(ENV_ENDPOINTS), default="prod-cn")
    p.add_argument("--endpoint", help="覆盖 --env 选定的 GraphQL endpoint")
    p.add_argument("--feature-id", type=int, default=None,
                   help="云端 Feature ID。可选 — 不传时 createJob 不带 featureId 字段，"
                        "适用于 server-opt 模式让容器内 client 启动后自动 sync features")
    p.add_argument("--device-id", default=None,
                   help="锁定具体设备的 device_id（resourceRequirements.conditions[device_id $eq value]），"
                        "如 R5CN70TEH0X")
    p.add_argument("--resource-name", default="BigPhone",
                   help="resourceRequirements.name —— vh-tests 等仓库 feature step 中"
                        "device 名字必须与此一致（默认 BigPhone，对应 vh-tests/user_login.feature 等 ）")
    p.add_argument("--app-type", required=True,
                   help="必填：kiwibit / birdtab / VicoHome 等，决定 host 加载哪个 features 子目录")
    p.add_argument("--client-mode", default="server_opt",
                   help="云端 client 协议模式，默认 server_opt。此参数不是 DeviumAI 的"
                        " implement/execution 模式；本脚本不支持 --mode implement")
    p.add_argument("--job-type", default="manual",
                   help="manual / smoke / regression 等，对应 TestJobType 枚举")
    p.add_argument("--priority", type=int, default=5, help="作业优先级（数字越大越优先），默认 5")
    p.add_argument("--resource-type", default="PHONE",
                   help="资源类型，默认 PHONE，可选 CAMERA/PHONE/SWITCH/OTHER")
    p.add_argument("--count", type=int, default=1, help="资源数量，默认 1")
    p.add_argument("--plan-name", default=None,
                   help="测试计划名称；默认由脚本生成")
    p.add_argument(
        "--allow-duplicate-plan",
        action="store_true",
        help="allow creating another plan with the same name; disabled by default",
    )
    p.add_argument("--plan-type", default="manual",
                   help="planType 字段，默认 manual")
    p.add_argument("--metadata", default=None,
                   help="附加 metadata，JSON 字符串，会与 clientMode/appType/env 合并；"
                        "支持 env 子字典，会与 --tests-* 自动合并")
    # ===== 方案 2：运行时 git clone features =====
    p.add_argument("--tests-repo", default=None,
                   help="目标 *-tests 仓库 git URL。"
                        "设了之后容器启动时会 git clone 到 /app/<module>")
    p.add_argument("--tests-ref", default=None,
                   help="git 分支/tag/commit；未传时读取测试仓库远端 HEAD 的默认分支")
    p.add_argument("--tests-module", default=None,
                   help="目标模块名，默认从 --tests-repo basename 推导（kb-tests.git → kb-tests）")
    p.add_argument("--tags", default=None,
                   help="behave tag 表达式，会进 metadata.tags（如 @uid=265af594）")
    # ===== 批量模式 =====
    p.add_argument("--batch", default=None,
                   help="批量模式：JSON 文件路径，包含多个 job 定义。"
                        "每个 job 有独立的 tags 和 resources。"
                        "启用时创建 1 个 TestPlan + N 个 Job。")
    p.add_argument("--concurrency", type=int, default=3,
                   help="批量模式下的最大并发 job 数，默认 3")
    # ===== 环境变量注入（三层合并，优先级低→高）=====
    p.add_argument("--baseline-env-file", default=None,
                   help="baseline .env 文件路径，仅注入团队共通的非敏感配置。"
                        "运行密钥由平台管理；普通配置可被 --env-file 和 "
                        "--inject-env 覆盖。")
    p.add_argument("--env-file", default=None,
                   help=".env 文件路径，注入模块特定 K=V 到 metadata.env（如 kb-tests/.env），"
                        "覆盖 --baseline-env-file 同名项。")
    p.add_argument("--inject-env", action="append", default=None,
                   help="单条 K=V 注入 metadata.env（可重复），优先级最高。"
                        "如 --inject-env DEVIUM_VERSION=2.17.0")
    # ===== 调试 =====
    p.add_argument("--dry-run", action="store_true",
                   help="不发请求，打印将要发送的 plan/job payload 后退出")
    p.add_argument("--no-wait", action="store_true", help="创建完即退出，不轮询状态")
    p.add_argument("--poll-interval", type=int, default=5, help="轮询间隔秒数，默认 5")
    p.add_argument("--timeout", type=int, default=1800,
                   help="单 job 执行超时秒数（从 startedAt 起算，不含农场排队），默认 1800（30min）")
    p.add_argument("--queue-timeout", type=int, default=7200,
                   help="单 job 排队超时秒数（从建单到 startedAt 出现），默认 7200（2h）。"
                        "排队不计入执行超时，避免农场繁忙时误报 timeout（见 device-cloud#15）")
    return p.parse_args(argv)


def load_env_file(path: str) -> dict[str, str]:
    """解析 .env 文件为 dict。忽略空行、# 注释、无 = 的行。"""
    result = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            # 去掉引号包裹
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            if key:
                result[key] = value
    return result


def merge_injected_env(
    env_file: str | None,
    inject_env: list[str] | None,
    baseline_env_file: str | None = None,
) -> dict[str, str]:
    """三层合并环境变量,优先级低→高:

      1. baseline_env_file (如 .vscode/.env, 跨模块通用项)
      2. env_file          (如 kb-tests/.env, 模块特有项)
      3. inject_env        (--inject-env, 命令行单条覆盖)
    """
    merged: dict[str, str] = {}
    if baseline_env_file:
        merged.update(load_env_file(baseline_env_file))
    if env_file:
        merged.update(load_env_file(env_file))
    if inject_env:
        for item in inject_env:
            if "=" not in item:
                sys.exit(f"error: --inject-env 格式错误（缺少 =）: {item}")
            key, _, value = item.partition("=")
            merged[key.strip()] = value.strip()
    return merged


def add_runtime_defaults(
    environment: str, injected_env: dict[str, str], plan_name: str
) -> dict[str, str]:
    """Add environment-specific, non-secret Client bootstrap configuration."""
    merged = dict(ENV_RUNTIME_DEFAULTS.get(environment, {}))
    merged.update(injected_env)
    merged["RP_LAUNCH_NAME"] = plan_name
    return merged


def reject_managed_credential_inputs(
    injected_env: dict[str, str],
    extra_metadata: dict | None,
) -> None:
    metadata_env = (
        extra_metadata.get("env")
        if isinstance(extra_metadata, dict)
        else None
    )
    found = sorted(
        key for key in MANAGED_CREDENTIAL_KEYS
        if key in injected_env or (
            isinstance(metadata_env, dict) and key in metadata_env
        )
    )
    if found:
        sys.exit(
            "error: managed credentials are not accepted from Trigger input: "
            + ", ".join(found)
        )


def load_batch_jobs(path: str) -> list[dict]:
    """读取并校验 jobs.json。

    每个 job 必须有 name(str)、tags(str)、resources(list)。
    resources 每项必须有 name(str)、type(str)。conditions(dict) 可选。
    """
    with open(path, encoding="utf-8") as f:
        jobs = json.load(f)
    if not isinstance(jobs, list) or not jobs:
        sys.exit(f"error: {path} 必须是非空 JSON 数组")
    for i, job in enumerate(jobs):
        label = f"jobs[{i}]"
        if not isinstance(job, dict):
            sys.exit(f"error: {label} 不是 JSON 对象")
        for required in ("name", "tags", "resources"):
            if required not in job:
                sys.exit(f"error: {label} 缺少 '{required}' 字段")
        if not isinstance(job["resources"], list) or not job["resources"]:
            sys.exit(f"error: {label}.resources 必须是非空数组")
        for j, res in enumerate(job["resources"]):
            res_label = f"{label}.resources[{j}]"
            if not isinstance(res, dict):
                sys.exit(f"error: {res_label} 不是 JSON 对象")
            for required in ("name", "type"):
                if required not in res:
                    sys.exit(f"error: {res_label} 缺少 '{required}' 字段")
    return jobs


# Trigger must never accept these credentials from argv, .env, metadata, or
# the ambient process. The target platform supplies them after authorization.
MANAGED_CREDENTIAL_KEYS = (
    "GITLAB_TOKEN",
    "CLIENT_GITLAB_TOKEN",
    "PIR_SIGN_SECRET",
    "LITELLM_API_KEY",
    "RP_API_KEY",
    "REDIS_PASSWORD",
    "LANGCHAIN_API_KEY",
    "CLOUD_AUTH_TOKEN",
    "GOOGLE_API_KEY",
    "OPENAI_API_KEY",
    "PACKAGE_API_TOKEN",
)
_SENSITIVE_ENV_KEYS = MANAGED_CREDENTIAL_KEYS


def _redact(metadata: dict) -> dict:
    """打印时把敏感 env 屏蔽,避免日志/CI log 泄漏。"""
    out = json.loads(json.dumps(metadata))
    if isinstance(out.get("env"), dict):
        for k in _SENSITIVE_ENV_KEYS:
            if k in out["env"]:
                v = out["env"][k]
                out["env"][k] = (v[:6] + "***" + v[-3:]) if v and len(v) > 12 else "***"
    return out


def _emit_result(payload: dict) -> None:
    print(RESULT_JSON_PREFIX + json.dumps(payload, ensure_ascii=False))


def _batch_dry_run(jobs_def: list[dict]) -> int:
    print("\n=== DRY-RUN: batch jobs ===")
    for index, job in enumerate(jobs_def):
        requirements = build_resource_requirements(job["resources"])
        print(f"\n--- job[{index}] {job['name']} ---")
        print(f"  tags: {job['tags']}")
        print(f"  resourceRequirements: {json.dumps(requirements, ensure_ascii=False)}")
    _emit_result({
        "planId": None, "jobIds": [],
        "resources": [item.get("resources", []) for item in jobs_def],
        "scenarioCount": len(jobs_def), "finalStatus": "DRY_RUN",
        "firstFailureReason": None,
    })
    return 0


def _fill_batch_window(args, context: dict, state: dict) -> None:
    limit = len(context["jobs"]) if args.no_wait else args.concurrency
    while state["pending"] and len(state["running"]) < limit:
        index = state["pending"].pop(0)
        job = context["jobs"][index]
        metadata = build_metadata(
            args.client_mode, args.app_type, args.tests_repo, args.tests_ref,
            args.tests_module, job["tags"], context["extra_metadata"],
            context["injected_env"],
        )
        timestamp = dt.datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] 创建 job [{index + 1}/{len(context['jobs'])}] {job['name']}")
        job_id = create_job_batch(
            context["endpoint"], context["auth"], context["plan_id"], metadata,
            build_resource_requirements(job["resources"]), args.job_type,
            args.priority, context["launched_by"], feature_id=args.feature_id,
            name=job["name"],
        )
        if job_id < 0:
            print(f"[{timestamp}] [FAILED] job {job['name']} 创建失败")
            state["create_failed"].append({"name": job["name"], "error": "createJob failed"})
            continue
        print(f"[{timestamp}] [OK] job#{job_id} {job['name']}")
        state["running"][job_id] = {"index": index, "name": job["name"]}
        state["created_mono"][job_id] = time.monotonic()


def _record_terminal_job(job_id: int, job: dict, state: dict) -> None:
    info = state["running"].pop(job_id)
    status = job.get("status")
    timestamp = dt.datetime.now().strftime("%H:%M:%S")
    scenarios = f"{job.get('passedScenarios', '?')}/{job.get('totalScenarios', '?')}"
    record = {"name": info["name"], "job_id": job_id, "status": status,
              "scenarios": scenarios}
    if job_fully_passed(job):
        print(f"[{timestamp}] [PASS] job#{job_id} {info['name']} -> {status} (scenario {scenarios})")
        state["completed"].append(record)
        return
    error = (
        f"scenario 未全通过 ({scenarios})"
        if status in SUCCESS_STATUSES
        else job.get("errorMessage", "") or status
    )
    print(f"[{timestamp}] [FAILED] job#{job_id} {info['name']} -> {status} "
          f"(scenario {scenarios}) {error}")
    state["failed"].append({**record, "error": error})


def _record_timeout(job_id: int, status: str | None, args, state: dict, now: float) -> None:
    timeout_kind = None
    timeout_seconds = None
    if job_id in state["started_mono"] and now - state["started_mono"][job_id] > args.timeout:
        timeout_kind, timeout_seconds = "EXEC_TIMEOUT", args.timeout
    elif job_id not in state["started_mono"] and (
        now - state["created_mono"].get(job_id, now) > args.queue_timeout
    ):
        timeout_kind, timeout_seconds = "QUEUE_TIMEOUT", args.queue_timeout
    if timeout_kind is None:
        return
    info = state["running"].pop(job_id)
    label = "执行" if timeout_kind == "EXEC_TIMEOUT" else "排队"
    timestamp = dt.datetime.now().strftime("%H:%M:%S")
    error = f"{label}超时 >{timeout_seconds}s (status={status})"
    print(f"[{timestamp}] ⏰ job#{job_id} {info['name']} {error}")
    state["failed"].append({"name": info["name"], "job_id": job_id,
                            "status": timeout_kind, "scenarios": "?/?", "error": error})


def _poll_batch_once(args, context: dict, state: dict) -> None:
    time.sleep(args.poll_interval)
    for job_id in list(state["running"]):
        try:
            job = gql(
                context["endpoint"], context["auth"], QUERY_JOB, {"id": str(job_id)}
            )["job"]
        except GraphQLRequestError as error:
            timestamp = dt.datetime.now().strftime("%H:%M:%S")
            print(f"[{timestamp}] [WARN] job#{job_id} 查询失败: {error}")
            continue
        now = time.monotonic()
        if job.get("startedAt"):
            state["started_mono"].setdefault(job_id, now)
        if job.get("status") in TERMINAL_STATUSES:
            _record_terminal_job(job_id, job, state)
        else:
            _record_timeout(job_id, job.get("status"), args, state, now)


def _run_batch_schedule(args, context: dict) -> tuple[dict, int]:
    total = len(context["jobs"])
    state = {
        "pending": list(range(total)), "running": {}, "created_mono": {},
        "started_mono": {}, "completed": [], "failed": [], "create_failed": [],
    }
    deadline = time.monotonic() + 6 * 3600
    while state["pending"] or state["running"]:
        if time.monotonic() > deadline:
            for job_id, info in state["running"].items():
                state["failed"].append({"name": info["name"], "job_id": job_id,
                                        "status": "TIMEOUT", "scenarios": "?/?",
                                        "error": "batch hard cap (6h)"})
            break
        _fill_batch_window(args, context, state)
        if args.no_wait or not state["running"]:
            break
        _poll_batch_once(args, context, state)
    return state, total


def _print_batch_summary(plan_id: str, jobs_def: list[dict], state: dict,
                         elapsed: float) -> int:
    total = len(jobs_def)
    completed, failed = state["completed"], state["failed"]
    create_failed = state["create_failed"]
    print(f"\n{'=' * 60}")
    print(f"[SUMMARY] batch 汇总 | plan_id={plan_id} | 耗时 "
          f"{int(elapsed // 60)}m{int(elapsed % 60)}s")
    print(f"   [PASS] 通过: {len(completed)}/{total}")
    for item in completed:
        print(f"      - {item['name']} (job#{item['job_id']}) scenario {item['scenarios']}")
    print(f"   [FAILED] 失败: {len(failed)}/{total}")
    for item in failed:
        print(f"      - {item['name']} (job#{item['job_id']}) -> {item['status']} "
              f"scenario {item['scenarios']}: {item['error']}")
    for item in create_failed:
        print(f"      - {item['name']}: {item['error']}")
    print(f"{'=' * 60}")
    failures = [*failed, *create_failed]
    _emit_result({
        "planId": plan_id,
        "jobIds": [item["job_id"] for item in [*completed, *failed] if item.get("job_id")],
        "resources": [item.get("resources", []) for item in jobs_def],
        "scenarioCount": total,
        "finalStatus": "FAILED" if failures else "PASSED",
        "firstFailureReason": failures[0].get("error") if failures else None,
    })
    return 1 if failures else 0


def batch_mode(args, *, auth_client=None, launched_by: str | None = None):
    """批量模式：1 plan + N jobs，滑动窗口并发控制。"""
    endpoint = args.endpoint or ENV_ENDPOINTS[args.env]
    plan_name = args.plan_name or f"batch-{dt.datetime.now():%H%M%S}"
    extra_metadata = json.loads(args.metadata) if args.metadata else None
    injected_env = add_runtime_defaults(
        args.env,
        merge_injected_env(args.env_file, args.inject_env, args.baseline_env_file),
        plan_name,
    )
    reject_managed_credential_inputs(injected_env, extra_metadata)
    jobs_def = load_batch_jobs(args.batch)
    print(f"[+] batch mode : {len(jobs_def)} jobs, concurrency={args.concurrency}")
    print(f"[+] endpoint   : {endpoint}")
    if args.dry_run:
        return _batch_dry_run(jobs_def)

    if auth_client is None:
        auth_client = DeviceCloudAuthClient(server_base_url(endpoint))
        launched_by = auth_client.authenticate().email
    elif not launched_by:
        raise ValueError("reused authentication requires launched_by")
    plan_name = args.plan_name or f"batch-{launched_by}-{dt.datetime.now():%H%M%S}"
    injected_env = add_runtime_defaults(args.env, injected_env, plan_name)
    plan_id, created = create_plan_once(
        endpoint, auth_client, plan_name, args.plan_type, launched_by,
        allow_duplicate=args.allow_duplicate_plan,
    )
    if not created:
        _emit_result({"planId": plan_id, "jobIds": [],
                      "scenarioCount": len(jobs_def), "finalStatus": "IDEMPOTENT",
                      "firstFailureReason": None})
        return 0
    print(f"[+] testPlanId : {plan_id}  ({plan_name})")
    context = {"endpoint": endpoint, "auth": auth_client, "plan_id": plan_id,
               "launched_by": launched_by, "jobs": jobs_def,
               "extra_metadata": extra_metadata, "injected_env": injected_env}
    started = time.monotonic()
    state, total = _run_batch_schedule(args, context)
    if args.no_wait:
        _emit_result({"planId": plan_id, "jobIds": sorted(state["running"]),
                      "resources": [item.get("resources", []) for item in jobs_def],
                      "scenarioCount": total, "finalStatus": "CREATED",
                      "firstFailureReason": (state["create_failed"][0]["error"]
                                             if state["create_failed"] else None)})
        return 1 if state["create_failed"] or len(state["running"]) != total else 0
    return _print_batch_summary(plan_id, jobs_def, state, time.monotonic() - started)


def main():
    args = parse_args()
    args.tests_ref = resolve_tests_ref(args.tests_repo, args.tests_ref)

    # batch 模式走独立流程
    if args.batch:
        return batch_mode(args)

    endpoint = args.endpoint or ENV_ENDPOINTS[args.env]
    launched_by = "<authenticated-user>"
    plan_name = args.plan_name or f"script-{dt.datetime.now():%H%M%S}"
    extra_metadata = json.loads(args.metadata) if args.metadata else None
    injected_env = merge_injected_env(args.env_file, args.inject_env, args.baseline_env_file)
    injected_env = add_runtime_defaults(args.env, injected_env, plan_name)
    reject_managed_credential_inputs(injected_env, extra_metadata)

    metadata = build_metadata(
        client_mode=args.client_mode,
        app_type=args.app_type,
        tests_repo=args.tests_repo,
        tests_ref=args.tests_ref,
        tests_module=args.tests_module,
        extra_tags=args.tags,
        extra_metadata=extra_metadata,
        injected_env=injected_env,
    )

    print(f"[+] endpoint   : {endpoint}")
    print(f"[+] launchedBy : {launched_by}")
    print(f"[+] feature    : {args.feature_id} / appType={args.app_type} / clientMode={args.client_mode}")
    if args.tests_repo:
        print(f"[+] tests      : {args.tests_repo}@{args.tests_ref}")
    print(f"[+] metadata   : {json.dumps(_redact(metadata), ensure_ascii=False)}")

    if args.dry_run:
        plan_payload = {"name": plan_name, "planType": args.plan_type,
                        "launchedBy": launched_by, "status": "UNTESTED"}
        conditions = ([{"key": "device_id", "operator": "$eq", "value": args.device_id}]
                      if args.device_id else [])
        job_payload = {
            "testPlanId": "<plan_id>",
            "jobType": args.job_type, "priority": args.priority,
            "resourceRequirements": [{
                "name": f"{args.resource_type.lower()}-1",
                "resourceType": args.resource_type, "count": args.count,
                "conditions": conditions,
            }],
            "launchedBy": launched_by, "metadata": _redact(metadata),
        }
        if args.feature_id is not None:
            job_payload["featureId"] = args.feature_id
        print("\n=== DRY-RUN: createTestPlan input ===")
        print(json.dumps(plan_payload, indent=2, ensure_ascii=False))
        print("\n=== DRY-RUN: createJob input ===")
        print(json.dumps(job_payload, indent=2, ensure_ascii=False))
        return 0

    auth_client = DeviceCloudAuthClient(server_base_url(endpoint))
    launched_by = auth_client.authenticate().email
    plan_name = args.plan_name or f"script-{launched_by}-{dt.datetime.now():%H%M%S}"
    metadata.setdefault("env", {})["RP_LAUNCH_NAME"] = plan_name
    plan_id, created = create_plan_once(
        endpoint,
        auth_client,
        plan_name,
        args.plan_type,
        launched_by,
        allow_duplicate=args.allow_duplicate_plan,
    )
    if not created:
        print(
            f"[IDEMPOTENT] testPlanId={plan_id} already exists for "
            f"{launched_by}; no duplicate Job was created"
        )
        return 0
    print(f"[+] testPlanId : {plan_id}  ({plan_name})")

    job_id = create_job(
        endpoint, auth_client, plan_id, args.feature_id, metadata,
        args.job_type, args.priority, args.resource_type, args.count, launched_by,
        device_id=args.device_id,
        resource_name=args.resource_name,
    )
    print(f"[+] jobId      : {job_id}")

    if args.no_wait:
        print(json.dumps({"planId": plan_id, "jobId": job_id}))
        return 0

    print(f"[+] polling every {args.poll_interval}s (timeout {args.timeout}s)...")
    job = poll_job(endpoint, auth_client, job_id, args.poll_interval, args.timeout)
    print(json.dumps(job, indent=2, ensure_ascii=False, default=str))
    scenarios = f"{job.get('passedScenarios', '?')}/{job.get('totalScenarios', '?')}"
    if job_fully_passed(job):
        print(f"[+] [PASS] 通过: status={job.get('status')} scenario {scenarios}")
        return 0
    print(f"[+] [FAILED] 失败: status={job.get('status')} scenario {scenarios} "
          f"(COMPLETE 不代表通过，需 scenario 全过)")
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except GraphQLRequestError as error:
        sys.exit(f"error: {error}")
