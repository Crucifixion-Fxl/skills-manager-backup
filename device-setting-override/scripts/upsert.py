#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量调用 /inner-api/setting-override/upsert 写入 device_setting_override。
Bulk-call /inner-api/setting-override/upsert to write rows into device_setting_override.

鉴权两种模式 / Two auth modes (默认 ldap; 除非显式指定 --auth=aksk):
  --auth=ldap (默认/default) — 通过 revenue-sharing 的 /skill-api/invoke 代理转发,
                                LDAP token 通过环境变量(默认 SKILL_LDAP_TOKEN)注入。
                                服务端用 PaasConfig AK/SK 帮忙签名后转发到目标节点。
                                运维用自己 LDAP 账号操作, 无需 IOT_ADMIN_AK/SK 共享凭证。
  --auth=aksk                — HmacSHA1 + base64(web-safe), 直连 iot-service-cloud
                                公网入口, 等价于 Java 端 OpenApiUtil.sign + buildUnsignedUrl。
                                AK/SK 通过环境变量 IOT_ADMIN_AK / IOT_ADMIN_SK 注入。
                                仅在用户明确要求或需绕过 revenue-sharing 代理时使用。

AK/SK / LDAP token 一律通过 env vars 注入, 绝不写入文件、绝不打印。
All secrets MUST be injected via env vars; never written to file or printed.

安全:  默认 dry-run; 只有 --confirm 才会真正发起请求。
Safe:  Dry-run by default; --confirm is required to actually POST.
"""

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import sys
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional

# ---------------------------------------------------------------------------
# 第三方依赖检查 / Third-party deps preflight
# ---------------------------------------------------------------------------
try:
    import openpyxl
except ImportError:
    sys.stderr.write("ERROR: 需要 openpyxl,请 pip install openpyxl / openpyxl required\n")
    sys.exit(2)

try:
    import requests
except ImportError:
    sys.stderr.write("ERROR: 需要 requests,请 pip install requests / requests required\n")
    sys.exit(2)


# ---------------------------------------------------------------------------
# 常量 / Constants
# ---------------------------------------------------------------------------

# 环境 → 公网入口域名的映射。aksk 模式下直接调用 iot-service-cloud 的对应区域入口。
# Env name → public ingress host mapping (used by aksk auth mode).
ENV_HOSTS = {
    "staging-us": "https://api-staging-us.vicohome.io",
    "staging-eu": "https://api-staging-eu.vicohome.io",
    "staging-cn": "https://api-stage.addx.live",
    "prod-us": "https://api-us.addx.live",
    "prod-eu": "https://api-eu.vicohome.io",
    "prod-cn": "https://api.addx.live",
}

# revenue-sharing 后端 host (ldap 模式下走 /skill-api/invoke 代理)。
# staging: console-test 前端, nginx 把 /api/* 转后端 → 需要 /api 前缀
# prod: 专用后端 host, 无 /api 前缀; **当前 skill-api 仅在 staging, prod 待部署**
# revenue-sharing backend hosts (ldap auth mode goes through /skill-api/invoke proxy).
RS_HOSTS = {
    "staging": "https://console-test.addx.live/api",
    "prod": "https://revenus-sharing-backend.addx.live",
}

# 后端 controller 的 path,与 SettingOverrideAdminController.java 中的 @PostMapping 完全一致。
# Backend path; must match @PostMapping in SettingOverrideAdminController.java.
PATH = "/inner-api/setting-override/upsert"

# overrideJson 顶层 key 白名单。来源: SettingOverrideValidator.ALLOWED_TOP_LEVEL_KEYS。
# Top-level key whitelist for overrideJson, mirrors SettingOverrideValidator.
ALLOWED_OVERRIDE_KEYS = {"name", "id", "time", "value"}

# overrideJson 序列化后字节数硬上限。来源: SettingOverrideValidator.MAX_OVERRIDE_JSON_BYTES。
# Hard upper bound on serialized overrideJson size.
MAX_OVERRIDE_JSON_BYTES = 16 * 1024

# 单次 upsert 批量上限。来源: SettingRepushService.MAX_BATCH_SIZE。
# Per-batch upsert cap.
MAX_BATCH_SIZE = 1000

# experiment 标签正则。来源: SettingOverrideValidator.EXPERIMENT_PATTERN。
# Experiment tag regex.
EXPERIMENT_RE = re.compile(r"^[a-z0-9_-]{1,64}$")


# ---------------------------------------------------------------------------
# 数据结构 / Data structures
# ---------------------------------------------------------------------------

@dataclass
class GroupSpec:
    """一个待写入分组的全部信息(分组名、SN 列表、对应的 overrideJson、对应的 experiment tag)。
    Carrier for a single rollout group: sheet name, SNs, override JSON string, experiment tag."""
    sheet_name: str             # Excel sheet 名,例如 "灵敏组"
    label: str                  # 分组英文标签,用于日志和文件名 (sensitive / insensitive)
    override_json_str: str      # 已序列化的 overrideJson 字符串 (一定是合法 JSON)
    experiment: str             # 该分组的 experiment 标签 (prefix + 分隔符 + 各组 suffix)
    sns: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 输入校验工具 / Input helpers
# ---------------------------------------------------------------------------

def env_var_required(name: str) -> str:
    """从环境变量读取必填值,缺失时立即退出。绝不打印 secret 内容。
    Read a required env var; exit immediately if missing. Never prints the value."""
    v = os.environ.get(name)
    if not v:
        sys.stderr.write(f"ERROR: 环境变量 {name} 未设置 / env var {name} not set\n")
        sys.exit(2)
    return v


def load_override_json(value: str) -> str:
    """加载并校验 overrideJson。

    入参支持两种形式:
      - 内联 JSON 字符串
      - 以 @ 开头的文件路径,例如 "@scripts/setting_override/sensitive.json"

    校验项与 SettingOverrideValidator 保持一致:
      - 必须是 JSON object
      - 不允许空对象 {} (AC-2.9b)
      - 顶层 key ⊂ {name, id, time, value} (AC-2.9c)
      - 'value' 字段 (若存在) 必须是 JSON object (AC-2.9d)
      - 序列化后字节数 ≤ 16KB (AC-2.9f)

    Returns:
      序列化后的紧凑 JSON 字符串(去掉多余空白),可直接作为 overrideJson 字段写入。
    """
    if value.startswith("@"):
        # @path 形式,从文件读
        path = Path(value[1:]).expanduser()
        text = path.read_text(encoding="utf-8")
    else:
        # 内联 JSON
        text = value

    try:
        obj = json.loads(text)
    except json.JSONDecodeError as e:
        raise SystemExit(f"override JSON 不是合法 JSON / invalid JSON: {e}")

    if not isinstance(obj, dict):
        raise SystemExit("override JSON 必须是 JSON object / must be a JSON object")
    if not obj:
        raise SystemExit("override JSON 不能为空 {} / must not be empty (AC-2.9b)")
    bad = set(obj.keys()) - ALLOWED_OVERRIDE_KEYS
    if bad:
        raise SystemExit(
            f"override JSON 顶层 key 不在白名单 {ALLOWED_OVERRIDE_KEYS}: {bad} / "
            f"top-level keys outside whitelist"
        )
    if "value" in obj and not isinstance(obj["value"], dict):
        raise SystemExit("override JSON 中的 'value' 必须是 object / 'value' must be JSON object")

    # 紧凑序列化(无空格),让字节数检查更接近真实落库内容。
    serialized = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    size = len(serialized.encode("utf-8"))
    if size > MAX_OVERRIDE_JSON_BYTES:
        raise SystemExit(
            f"override JSON 序列化后 {size} 字节,超过 16KB 上限 / exceeds 16KB"
        )
    return serialized


def load_sns_from_xlsx(path: Path, sheet_names: List[str]) -> dict:
    """从 xlsx 读取 sheet → SN 列表的映射。

    约定:
      - 第 1 行视为表头,跳过。
      - 只取第 1 列。
      - 跳过空单元格 / 仅空白的格。
      - 单 sheet 内去重(保留首次出现的顺序)。
    """
    # read_only=True 大幅减少内存占用;data_only=True 把公式结果而非公式本身读出来。
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)

    # 提前校验所有需要的 sheet 都存在,避免读到一半才报错。
    for required in sheet_names:
        if required not in wb.sheetnames:
            raise SystemExit(
                f"sheet '{required}' 不存在于 {path} / not found "
                f"(实际有: {wb.sheetnames})"
            )

    out = {}
    for name in sheet_names:
        ws = wb[name]
        sns = []
        seen = set()
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i == 0:
                # 跳过表头行
                continue
            v = row[0]
            if v is None:
                continue
            sn = str(v).strip()
            if not sn:
                continue
            if sn in seen:
                # 同 sheet 内的重复 SN 直接丢弃,数据库 ON DUPLICATE KEY UPDATE 也会兜底。
                continue
            seen.add(sn)
            sns.append(sn)
        out[name] = sns
    return out


# ---------------------------------------------------------------------------
# 签名 / Signing
# ---------------------------------------------------------------------------

def create_signed_url(row_url: str, ak: str, sk: str) -> str:
    """构造带签名的最终 URL,语义与 Java 端 OpenApiAuthService.createSignedUrl 完全一致。

    步骤:
      1. 拼出 unsignedUrl = "<row_url>?accessKey=<ak>&timestamp=<ts>"  (ts 取当前 UTC 秒)
      2. base64-decode AK/SK 中的 SK,得到 HmacSHA1 的 raw key
      3. signature = base64( HmacSHA1(unsignedUrl_bytes, raw_key) )
      4. URL-safe 替换: '+' → '-', '/' → '_'  (= 不替换)
      5. 返回 "<unsignedUrl>&signature=<signature>"

    服务端会反向去掉末尾 "&signature=...",对剩余串再算一次,然后 byte-for-byte 比对。
    """
    ts = str(int(time.time()))
    sep = "&" if "?" in row_url else "?"
    unsigned = f"{row_url}{sep}accessKey={ak}&timestamp={ts}"

    # SK 在配置中是 base64 字符串,解码后才是真正的 16 字节随机 key。
    try:
        key = base64.b64decode(sk)
    except Exception as e:
        raise SystemExit(f"IOT_ADMIN_SK 不是合法 base64 / not valid base64: {e}")

    sig = hmac.new(key, unsigned.encode("utf-8"), hashlib.sha1).digest()
    sig_b64 = base64.b64encode(sig).decode("ascii").replace("+", "-").replace("/", "_")
    return f"{unsigned}&signature={sig_b64}"


# ---------------------------------------------------------------------------
# 请求构造与发送 / Build & send
# ---------------------------------------------------------------------------

def build_items(
    sns: Iterable[str],
    override_json: str,
    experiment: Optional[str],
    expire_at: int,
) -> list:
    """把一组 SN 拼成 admin upsert API 的 items 数组。

    每个 item 形状对齐 SettingOverrideUpsertItem:
      { serialNumber, overrideJson, experiment?, expireAt }
    其中 overrideJson 是字符串(不是嵌套 JSON);experiment 为空时不放该字段。

    注意:同批 items 共用同一个 experiment 值,这样 admin 审计指标能把整批归到同一
    experiment 标签下(SettingOverrideAdminController.commonExperimentTagOrNull)。
    """
    items = []
    for sn in sns:
        item = {
            "serialNumber": sn,
            "overrideJson": override_json,
            "expireAt": int(expire_at),
        }
        if experiment:
            item["experiment"] = experiment
        items.append(item)
    return items


def compose_experiment(prefix: str, separator: str, suffix: str) -> str:
    """把 prefix / separator / suffix 拼成最终 experiment 标签并校验。

    校验规则:拼好的整串必须满足 ^[a-z0-9_-]{1,64}$ (与 SettingOverrideValidator
    一致);prefix 和 suffix 单独校验空白以给出更清晰的错误。
    """
    if not prefix:
        raise SystemExit("--experiment-prefix 不能为空 / must not be empty")
    if not suffix:
        raise SystemExit("--*-suffix 不能为空 / suffix must not be empty")
    if separator not in {"_", "-"}:
        raise SystemExit("--experiment-separator 只能是 '_' 或 '-' / only '_' or '-' allowed")
    composed = f"{prefix}{separator}{suffix}"
    if not EXPERIMENT_RE.match(composed):
        raise SystemExit(
            f"组合后的 experiment 不合法: '{composed}' "
            f"(必须满足 ^[a-z0-9_-]{{1,64}}$) / composed tag invalid"
        )
    return composed


def chunked(lst: list, size: int):
    """把 list 切成 size 大小的批次,yield (起始 offset, 子 list)。
    用 offset 主要是为了在日志里追踪某一批对应原 list 的哪一段。"""
    for i in range(0, len(lst), size):
        yield i, lst[i : i + size]


def enforce_repush_choice(repush: bool, no_repush: bool, confirm: bool) -> bool:
    """非交互式的 repush 决策强制函数。

    设计理由:
      - skills 仓库的安全 validator 禁止脚本里使用交互式 stdin 读取 (agent-friendly 要求)
      - 但又不能让运维"忘了选 repush"导致静默错配
      - 折中: `--confirm` 触发时, `--repush` 与 `--no-repush` 必须二选一显式给出,
        否则脚本打印两种选择的后果并立即退出(让用户重新运行时显式选)

    --repush  与 --no-repush 互斥(argparse 已强制),不能同时设。
    返回最终生效的 repush 布尔值。
    """
    # dry-run 阶段不强制选择, 默认 False 即可。
    if not confirm:
        return repush

    if not repush and not no_repush:
        # 把两种选择的后果同时打出来, 让用户基于完整信息做决定。
        print("=" * 70, file=sys.stderr)
        print("ERROR: --confirm 要求显式选择 --repush 或 --no-repush", file=sys.stderr)
        print("       (must explicitly choose one when --confirm is set)", file=sys.stderr)
        print("-" * 70, file=sys.stderr)
        print("[选项 A] --no-repush  仅写 DB, 设备需等下次主动拉 /deviceMsg/setting 才能看到新值", file=sys.stderr)
        print("                       延迟几分钟到几小时, 整批耗时 ≈ HTTP 往返 (~50-200 ms/批)", file=sys.stderr)
        print("                       适用: 慢实验铺开, 启动时间不敏感", file=sys.stderr)
        print("[选项 B] --repush     写库后服务端串行重发 setting 给每个 SN", file=sys.stderr)
        print("                       单批耗时显著拉长 (~5-10 分钟/批, 视在线情况)", file=sys.stderr)
        print("                       离线 SN 进入 repushFailedSerialNumbers 落到 failed/", file=sys.stderr)
        print("                       适用: 紧急 hot-fix / 实验需立即开始", file=sys.stderr)
        print("=" * 70, file=sys.stderr)
        sys.exit(2)

    # argparse mutually exclusive 已保证不会同时为 True, 这里仅返回最终值。
    return bool(repush)


# ---------------------------------------------------------------------------
# 鉴权上下文 / Auth context — 抽象 aksk / ldap 两种模式
# ---------------------------------------------------------------------------

@dataclass
class AuthCtx:
    """承载本次运行的鉴权配置。

    aksk 模式: 直接调 iot-service-cloud, 用 AK/SK HmacSHA1 签名 URL。
      - target_host: ENV_HOSTS[env]
      - ak, sk: IOT_ADMIN_AK / IOT_ADMIN_SK
      - rs_host, ldap_token, node_name: 未使用

    ldap 模式: 经 revenue-sharing /skill-api/invoke 代理转发, 用 LDAP token 鉴权。
      - rs_host: RS_HOSTS[rs_env]
      - ldap_token: 从 --skill-token-env 指定的环境变量读取
      - node_name: ConsoleServiceRegistry 里登记的 paas nodeName(用来路由到目标 iot-service-cloud 节点)
      - ak, sk, target_host: 未使用(服务端用 PaasConfig 帮忙签名)
    """
    mode: str                               # "aksk" or "ldap"
    target_host: Optional[str] = None       # aksk only
    ak: Optional[str] = None                # aksk only
    sk: Optional[str] = None                # aksk only
    rs_host: Optional[str] = None           # ldap only
    ldap_token: Optional[str] = None        # ldap only
    node_name: Optional[str] = None         # ldap only


def send_request(ctx: AuthCtx, method: str, path: str,
                 query: Optional[str] = None,
                 body: Optional[dict] = None,
                 timeout: int = 30) -> requests.Response:
    """根据 ctx.mode 选 aksk 或 ldap 路径发起请求, 透传 method/path/query/body。

    aksk 路径: 拼出 <target_host><path>?<query>, HmacSHA1 签名后直接发到 iot-service-cloud。
    ldap 路径: 拼出 <rs_host>/skill-api/invoke?nodeName=...&path=<urlencoded path+query>&method=...,
              header 带 Authorization: <ldap_token>, body 透传。

    body 处理:
      - aksk: GET 不带 body; POST 走 JSON。
      - ldap: GET 也强制带 data='{}' (revenue-sharing 端 HttpUtils 历史 NPE 兼容; 修复后保留也无害)。
    """
    method = method.upper()
    if ctx.mode == "aksk":
        return _send_aksk(ctx, method, path, query, body, timeout)
    if ctx.mode == "ldap":
        return _send_ldap(ctx, method, path, query, body, timeout)
    raise SystemExit(f"unknown auth mode: {ctx.mode}")


def _send_aksk(ctx: AuthCtx, method: str, path: str,
               query: Optional[str], body: Optional[dict],
               timeout: int) -> requests.Response:
    row_url = f"{ctx.target_host}{path}"
    if query:
        row_url = f"{row_url}?{query}"
    signed_url = create_signed_url(row_url, ctx.ak, ctx.sk)
    if method == "GET":
        return requests.get(signed_url, timeout=timeout)
    if method == "POST":
        return requests.post(
            signed_url,
            data=json.dumps(body or {}, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            timeout=timeout,
        )
    raise SystemExit(f"aksk: unsupported method {method}")


def _send_ldap(ctx: AuthCtx, method: str, path: str,
               query: Optional[str], body: Optional[dict],
               timeout: int) -> requests.Response:
    # 把目标 path + query 编码到 invoke 的 path 参数里, 让 revenue-sharing 完整透传给目标节点。
    target_path = path if not query else f"{path}?{query}"
    invoke_query = (
        f"nodeName={urllib.parse.quote(ctx.node_name or '', safe='')}"
        f"&path={urllib.parse.quote(target_path, safe='')}"
        f"&method={urllib.parse.quote(method, safe='')}"
    )
    url = f"{ctx.rs_host}/skill-api/invoke?{invoke_query}"
    headers = {
        "Content-Type": "application/json",
        # revenue-sharing 端 SkillApiController 读 Authorization header 做 token 校验。
        "Authorization": ctx.ldap_token,
    }
    # GET 也带空 body 兼容 revenue-sharing HttpUtils 历史 null-body NPE; 修复后兼容仍无害。
    payload_bytes = json.dumps(body if body is not None else {}, ensure_ascii=False).encode("utf-8")
    if method == "GET":
        return requests.get(url, data=payload_bytes, headers=headers, timeout=timeout)
    if method == "POST":
        return requests.post(url, data=payload_bytes, headers=headers, timeout=timeout)
    raise SystemExit(f"ldap: unsupported method {method}")


def post_batch(ctx: AuthCtx, payload: dict, timeout: int = 60):
    """对 /inner-api/setting-override/upsert 发一次 POST, 走 aksk 或 ldap 路径(由 ctx 决定)。

    timeout 默认 60s 仅适用于 repushSetting=false 的快速路径;
    repushSetting=true 时服务端要做 1000 SN 串行 setting 重发, 单批耗时 5-10 分钟,
    必须把 timeout 拉到 ≥600s, 否则 client 会在 server 还没回包前就超时,
    把成功批误记为 network-error。调用方负责按 repush 状态选合适 timeout。
    """
    return send_request(ctx, "POST", PATH, query=None, body=payload, timeout=timeout)


# ---------------------------------------------------------------------------
# CLI / 命令行
# ---------------------------------------------------------------------------

def parse_args():
    """命令行参数定义。所有"高危"参数(experiment / expire-at / repush)都强制显式给出,
    避免拷贝命令时漏掉某项导致写错数据。"""
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("--env", required=True, choices=sorted(ENV_HOSTS.keys()),
                   help="目标环境名,会映射到对应 iot-service-cloud 公网域名(aksk 模式直连;"
                        "ldap 模式仅作为日志标签, 真实路由由 --node-name 决定)/ target env")
    # ---- 鉴权模式(aksk = AK/SK 直连; ldap = 经 revenue-sharing /skill-api/invoke 代理)----
    p.add_argument("--auth", choices=["aksk", "ldap"], default="ldap",
                   help="鉴权模式: ldap(默认, 经 revenue-sharing /skill-api/invoke 代理, 需 LDAP token) "
                        "或 aksk(直连 iot-service-cloud, 需 IOT_ADMIN_AK/SK, 仅在用户显式要求时用)")
    p.add_argument("--rs-env", choices=sorted(RS_HOSTS.keys()), default=None,
                   help="ldap 模式必填: revenue-sharing 控制台环境, "
                        "staging→console-test.addx.live/api, "
                        "prod→revenus-sharing-backend.addx.live (待部署, 当前仅 staging 可用)")
    p.add_argument("--node-name", default=None,
                   help="ldap 模式必填: ConsoleServiceRegistry 登记的 paas nodeName, "
                        "决定 revenue-sharing 把请求转发到哪个 iot-service-cloud 节点 "
                        "(如 staging-us / staging-eu / prod-us / prod-eu, 具体由服务端配置决定)")
    p.add_argument("--skill-token-env", default="SKILL_LDAP_TOKEN",
                   help="ldap 模式: 存放 LDAP token 的环境变量名, 默认 SKILL_LDAP_TOKEN "
                        "(token 由 POST <rs-host>/skill-api/login 获得, 3 天有效)")
    p.add_argument("--input", required=True, help="xlsx 文件路径 / path to xlsx")
    p.add_argument("--sensitive-sheet", default="灵敏组",
                   help="灵敏组 sheet 名 / sensitive sheet name")
    p.add_argument("--insensitive-sheet", default="不灵敏组",
                   help="不灵敏组 sheet 名 / insensitive sheet name")
    p.add_argument("--sensitive-json", required=True,
                   help="灵敏组 overrideJson, 内联 JSON 或 @路径 / inline JSON or @path")
    p.add_argument("--insensitive-json", required=True,
                   help="不灵敏组 overrideJson, 内联 JSON 或 @路径")
    p.add_argument("--experiment-prefix", required=True,
                   help="experiment 标签前缀,两组共用 / experiment tag prefix shared by both groups")
    p.add_argument("--experiment-separator", default="_", choices=["_", "-"],
                   help="prefix 与 suffix 间的分隔符, 只允许 '_' 或 '-', 默认 '_'")
    p.add_argument("--sensitive-suffix", default="sensitive",
                   help="灵敏组 suffix, 默认 'sensitive' (最终 tag = <prefix><sep><suffix>)")
    p.add_argument("--insensitive-suffix", default="insensitive",
                   help="不灵敏组 suffix, 默认 'insensitive'")
    p.add_argument("--expire-at", type=int, required=True,
                   help="过期时间(UTC 秒);0 = 永不过期;否则必须 > 当前时间")
    # repush 决策互斥组: --confirm 触发时必须二选一显式给出。
    # 设计避免交互式 stdin 读取 (skills 安全 validator 不允许), 改用强制 flag 方式让选择显式且不可遗漏。
    repush_group = p.add_mutually_exclusive_group()
    repush_group.add_argument("--repush", action="store_true",
                              help="repushSetting=true: 写库后服务端串行重发 setting 给每个 SN(慢但即时)")
    repush_group.add_argument("--no-repush", action="store_true",
                              help="repushSetting=false: 仅写 DB, 设备等下次主动拉 setting 才看到新值(快但有延迟)")
    p.add_argument("--batch-size", type=int, default=MAX_BATCH_SIZE,
                   help=f"单批 SN 数,默认 {MAX_BATCH_SIZE} / batch size")
    p.add_argument("--start-batch", type=int, default=0,
                   help="0-索引;从第 N 批开始(用于断点续传)")
    p.add_argument("--limit-batches", type=int, default=0,
                   help=">0 时只跑前 N 批(烟雾测试用);0 = 全量")
    p.add_argument("--qps-sleep", type=float, default=0.5,
                   help="批间 sleep 秒数,默认 0.5 / inter-batch sleep")
    p.add_argument("--http-timeout", type=int, default=0,
                   help="单次 POST 的 timeout 秒数; 默认 0=按 --repush 自动选(no-repush=60s, "
                        "repush=900s)。一定不能比 server 端 repush 单批耗时短, 否则会把成功批误记为 network-error")
    p.add_argument("--confirm", action="store_true",
                   help="真正发起请求;不加该 flag 时为 dry-run")
    p.add_argument("--out-dir", default="./out",
                   help="日志/失败批/汇总输出目录 / run output dir")
    p.add_argument("--only-group", choices=["sensitive", "insensitive"], default=None,
                   help="只跑其中一组(测试用) / run only one group")
    return p.parse_args()


# ---------------------------------------------------------------------------
# AuthCtx 构造 / Build auth ctx from CLI args
# ---------------------------------------------------------------------------

def build_auth_ctx(args, target_host: str) -> AuthCtx:
    """根据 --auth 选项构造 AuthCtx 并校验必填项。

    aksk 模式必填: env 环境变量 IOT_ADMIN_AK / IOT_ADMIN_SK
    ldap 模式必填: --rs-env, --node-name, env 环境变量 <--skill-token-env>(默认 SKILL_LDAP_TOKEN)
    """
    if args.auth == "aksk":
        # AK/SK 强制走 env 注入, 绝不写入磁盘日志。
        return AuthCtx(
            mode="aksk",
            target_host=target_host,
            ak=env_var_required("IOT_ADMIN_AK"),
            sk=env_var_required("IOT_ADMIN_SK"),
        )
    # ldap 模式
    if not args.rs_env:
        sys.exit("--auth=ldap 必须给 --rs-env (staging 或 prod) / required for ldap auth")
    if not args.node_name:
        sys.exit("--auth=ldap 必须给 --node-name (paas nodeName) / required for ldap auth")
    return AuthCtx(
        mode="ldap",
        rs_host=RS_HOSTS[args.rs_env],
        ldap_token=env_var_required(args.skill_token_env),
        node_name=args.node_name,
    )


# ---------------------------------------------------------------------------
# 主流程 / main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()

    # ---- 入参合法性检查 ----
    if args.batch_size < 1 or args.batch_size > MAX_BATCH_SIZE:
        sys.exit(f"--batch-size 必须在 [1, {MAX_BATCH_SIZE}] / out of range")
    # 两组的最终 experiment tag 在 compose_experiment 内校验,失败立即退出。
    sensitive_experiment = compose_experiment(
        args.experiment_prefix, args.experiment_separator, args.sensitive_suffix)
    insensitive_experiment = compose_experiment(
        args.experiment_prefix, args.experiment_separator, args.insensitive_suffix)
    if sensitive_experiment == insensitive_experiment:
        sys.exit("两组 suffix 不能相同 / sensitive and insensitive suffixes must differ")
    now = int(time.time())
    if args.expire_at != 0 and args.expire_at <= now:
        sys.exit("--expire-at 必须 = 0 或 > 当前时间 / must be 0 or > now")

    host = ENV_HOSTS[args.env]
    # ---- 根据 --auth 构造鉴权上下文 ----
    # aksk: 直连 iot-service-cloud, 用 IOT_ADMIN_AK/SK HmacSHA1 签名。
    # ldap: 经 revenue-sharing /skill-api/invoke 代理, 用 LDAP token 鉴权,
    #       由服务端用 PaasConfig 帮忙签名后转发到 --node-name 指定的目标节点。
    ctx = build_auth_ctx(args, host)

    # ---- 加载并校验两份 overrideJson ----
    sensitive_json = load_override_json(args.sensitive_json)
    insensitive_json = load_override_json(args.insensitive_json)

    # ---- 加载 xlsx 中两个 sheet 的 SN 列表 ----
    xlsx_path = Path(args.input).expanduser()
    if not xlsx_path.exists():
        sys.exit(f"输入文件不存在 / input not found: {xlsx_path}")
    sheet_data = load_sns_from_xlsx(
        xlsx_path,
        [args.sensitive_sheet, args.insensitive_sheet],
    )

    # ---- 组装待执行的分组列表(可被 --only-group 过滤) ----
    # 每个 GroupSpec 携带自己的 experiment tag, 让 build_items 直接读取分组级别的值。
    groups = []
    if args.only_group in (None, "sensitive"):
        groups.append(GroupSpec(
            sheet_name=args.sensitive_sheet,
            label="sensitive",
            override_json_str=sensitive_json,
            experiment=sensitive_experiment,
            sns=sheet_data[args.sensitive_sheet],
        ))
    if args.only_group in (None, "insensitive"):
        groups.append(GroupSpec(
            sheet_name=args.insensitive_sheet,
            label="insensitive",
            override_json_str=insensitive_json,
            experiment=insensitive_experiment,
            sns=sheet_data[args.insensitive_sheet],
        ))

    # ---- 准备本次 run 的输出目录 ----
    out_dir = Path(args.out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    run_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = out_dir / f"run-{run_ts}-{args.env}"
    run_dir.mkdir(parents=True, exist_ok=True)
    failed_dir = run_dir / "failed"          # 失败批的完整 payload + response 落到这里,便于 replay
    failed_dir.mkdir(exist_ok=True)
    progress_path = run_dir / "progress.jsonl"   # 每批一行 JSONL,实时刷盘
    summary_path = run_dir / "summary.json"      # 结束时写一次

    # ---- 预演计划 ----
    print(f"[plan] env={args.env} host={host}")
    if ctx.mode == "aksk":
        print(f"[plan] auth=aksk (direct to iot-service-cloud)")
    else:
        # 不打印 token 值, 只打印来源 env var 名 + 长度区段, 便于排查 "是不是没读到 token"
        token_len = len(ctx.ldap_token or "")
        print(f"[plan] auth=ldap rs-env={args.rs_env} rs-host={ctx.rs_host} "
              f"node-name={ctx.node_name} token-env={args.skill_token_env} "
              f"token-len={token_len}")
    print(f"[plan] xlsx={xlsx_path}")
    print(f"[plan] experiment-prefix={args.experiment_prefix} "
          f"separator='{args.experiment_separator}' "
          f"expireAt={args.expire_at} repush={args.repush}")
    print(f"[plan] batch-size={args.batch_size} start-batch={args.start_batch} "
          f"limit-batches={args.limit_batches or 'all'}")
    for g in groups:
        nb = (len(g.sns) + args.batch_size - 1) // args.batch_size
        # 把每组最终的 experiment tag 显式打印, 避免靠脑补猜
        print(f"[plan] group={g.label} sheet={g.sheet_name} "
              f"experiment={g.experiment} sns={len(g.sns)} batches={nb}")
    # repush 决策强制: --confirm 时必须显式给 --repush 或 --no-repush, 否则函数打印后果并 sys.exit
    effective_repush = enforce_repush_choice(args.repush, args.no_repush, args.confirm)

    # 自动选 http timeout: repush 路径必须给足时间让 server 完成 1000 SN 的 setting fanout
    # (实测单批 5-10 分钟); no-repush 路径只是写库, 60s 充裕。--http-timeout 显式给值时以用户为准。
    effective_http_timeout = args.http_timeout if args.http_timeout > 0 \
        else (900 if effective_repush else 60)

    print(f"[plan] dry-run={'NO (will POST)' if args.confirm else 'YES (no POST)'}")
    print(f"[plan] repush={effective_repush} http-timeout={effective_http_timeout}s")
    print(f"[plan] run_dir={run_dir}")
    print()

    # ---- 汇总结构,逐批累加 ----
    # 顶层不再有单一 experiment 字段;改为在 groups[<label>].experiment 记录各自 tag。
    summary = {
        "env": args.env,
        "host": host,
        "auth": ctx.mode,
        "rsEnv": args.rs_env,
        "nodeName": args.node_name,
        "experimentPrefix": args.experiment_prefix,
        "experimentSeparator": args.experiment_separator,
        "expireAt": args.expire_at,
        "repush": bool(effective_repush),
        "repushOriginalArg": bool(args.repush),    # 命令行原始值, 与最终生效值可能不同(交互切换)
        "batchSize": args.batch_size,
        "dryRun": not args.confirm,
        "startedAt": run_ts,
        "groups": {},
        "totals": {"requested": 0, "succeededCount": 0, "repushFailed": 0, "failedBatches": 0},
    }

    global_batch_idx = 0     # 跨分组的全局批次号(用在文件名 / progress 里,便于关联)
    progress_fh = open(progress_path, "a", encoding="utf-8")

    try:
        for g in groups:
            g_summary = {
                "experiment": g.experiment,
                "sns": len(g.sns),
                "succeededCount": 0,
                "repushFailed": 0,
                "failedBatches": 0,
                "skippedBatches": 0,
            }
            for offset, chunk in chunked(g.sns, args.batch_size):
                # 1) 只跑 N 批(烟雾测试)
                if args.limit_batches and global_batch_idx >= args.limit_batches:
                    break
                # 2) 断点续传:跳过指定批次之前的批
                if global_batch_idx < args.start_batch:
                    g_summary["skippedBatches"] += 1
                    global_batch_idx += 1
                    continue

                # 用分组级别的 experiment tag, 让灵敏组与不灵敏组各自归到不同 tag。
                items = build_items(chunk, g.override_json_str, g.experiment, args.expire_at)
                payload = {"items": items, "repushSetting": bool(effective_repush)}

                t0 = time.time()
                progress_record = {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "globalBatchIdx": global_batch_idx,
                    "group": g.label,
                    "groupOffset": offset,
                    "size": len(chunk),
                }

                if not args.confirm:
                    # ---- dry-run 分支:只打印计划,不发请求 ----
                    progress_record.update({"action": "dry-run"})
                    print(f"[dry] batch#{global_batch_idx:04d} group={g.label} "
                          f"size={len(chunk)} (no POST)")
                else:
                    # ---- 真正调用 admin upsert ----
                    try:
                        resp = post_batch(ctx, payload, timeout=effective_http_timeout)
                    except requests.RequestException as e:
                        # 网络错误不重试,直接落盘失败,人工 --start-batch 继续。
                        progress_record.update({"action": "network-error", "error": str(e)})
                        g_summary["failedBatches"] += 1
                        with open(
                            failed_dir / f"{g.label}-batch{global_batch_idx:04d}.json",
                            "w", encoding="utf-8",
                        ) as f:
                            json.dump({"payload": payload, "error": str(e)},
                                      f, ensure_ascii=False)
                        print(f"[err] batch#{global_batch_idx:04d} group={g.label} "
                              f"size={len(chunk)} network: {e}")
                        progress_fh.write(json.dumps(progress_record, ensure_ascii=False) + "\n")
                        progress_fh.flush()
                        global_batch_idx += 1
                        time.sleep(args.qps_sleep)
                        continue

                    latency_ms = int((time.time() - t0) * 1000)
                    body_text = resp.text
                    parsed = None
                    try:
                        parsed = resp.json()
                    except ValueError:
                        # 后端意外返回非 JSON(例如 nginx 错误页),按失败处理。
                        pass

                    progress_record.update({
                        "action": "post",
                        "httpStatus": resp.status_code,
                        "latencyMs": latency_ms,
                    })

                    # 与 org.addx.iot.common.vo.Result 约定: result == 0 才算成功。
                    ok = (
                        resp.status_code == 200
                        and isinstance(parsed, dict)
                        and parsed.get("result") == 0
                    )
                    if ok:
                        data = parsed.get("data") or {}
                        succeeded = int(data.get("succeededCount") or 0)
                        repush_failed = list(data.get("repushFailedSerialNumbers") or [])
                        g_summary["succeededCount"] += succeeded
                        g_summary["repushFailed"] += len(repush_failed)
                        progress_record["succeededCount"] = succeeded
                        progress_record["repushFailedCount"] = len(repush_failed)
                        # 重发失败 SN 单独落盘,便于事后单独再 push。
                        if repush_failed:
                            with open(
                                failed_dir / f"{g.label}-batch{global_batch_idx:04d}-repushFailed.json",
                                "w", encoding="utf-8",
                            ) as f:
                                json.dump(repush_failed, f, ensure_ascii=False)
                        print(f"[ok ] batch#{global_batch_idx:04d} group={g.label} "
                              f"size={len(chunk)} succeeded={succeeded} "
                              f"repushFailed={len(repush_failed)} {latency_ms}ms")
                    else:
                        # http != 200 或 result != 0:落完整上下文便于 replay。
                        g_summary["failedBatches"] += 1
                        progress_record["responseBody"] = body_text[:2000]
                        with open(
                            failed_dir / f"{g.label}-batch{global_batch_idx:04d}.json",
                            "w", encoding="utf-8",
                        ) as f:
                            json.dump({
                                "payload": payload,
                                "httpStatus": resp.status_code,
                                "responseBody": body_text,
                            }, f, ensure_ascii=False)
                        print(f"[err] batch#{global_batch_idx:04d} group={g.label} "
                              f"size={len(chunk)} http={resp.status_code} "
                              f"body={body_text[:200]}")

                # progress 立即 flush,避免脚本被 kill 时丢失。
                progress_fh.write(json.dumps(progress_record, ensure_ascii=False) + "\n")
                progress_fh.flush()
                global_batch_idx += 1
                if args.qps_sleep > 0:
                    time.sleep(args.qps_sleep)

            summary["groups"][g.label] = g_summary
            summary["totals"]["requested"] += g_summary["sns"]
            summary["totals"]["succeededCount"] += g_summary["succeededCount"]
            summary["totals"]["repushFailed"] += g_summary["repushFailed"]
            summary["totals"]["failedBatches"] += g_summary["failedBatches"]

            if args.limit_batches and global_batch_idx >= args.limit_batches:
                break
    finally:
        # 不论成功/异常都把 summary 落盘,便于事后审计。
        progress_fh.close()
        summary["finishedAt"] = datetime.now(timezone.utc).isoformat()
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)

    print()
    print(f"[done] summary 已写入 / written to {summary_path}")
    print(json.dumps(summary["totals"], ensure_ascii=False))


if __name__ == "__main__":
    main()
