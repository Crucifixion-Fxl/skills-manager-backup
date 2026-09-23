#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Setting Override Admin API 的 4 个非-upsert 端点的 CLI 封装。
CLI for the 4 non-upsert endpoints of the Setting Override Admin API.

子命令 / Subcommands:
  - delete  批量删除 (按 SN 列表 或 按 experiment) / batch delete (by SN list or experiment)
  - get     单 SN 查询 / single-SN query
  - list    按 experiment 分页列表(支持 --all 全量导出) / list by experiment with pagination
  - stats   按 experiment 聚合活跃覆盖数 / aggregate counts by experiment

鉴权两种模式 / Two auth modes (同 upsert.py; 默认 ldap; 除非显式指定 --auth=aksk):
  --auth=ldap (默认) — 经 revenue-sharing 的 /skill-api/invoke 代理, LDAP token 通过
                       环境变量(默认 SKILL_LDAP_TOKEN)注入, 服务端用 PaasConfig 帮忙签名。
  --auth=aksk        — HmacSHA1 + base64(web-safe), 直连 iot-service-cloud, 用
                       IOT_ADMIN_AK / IOT_ADMIN_SK 签名。仅在用户明确要求时用。

所有 secret 一律通过 env vars 注入, 绝不写入文件、绝不打印。
All secrets MUST be injected via env vars; never written to file or printed.

安全:delete 默认 dry-run,只有 --confirm 才会真正发起请求;get / list / stats
是只读,直接执行(只读没有破坏性,且不变量保护让响应不可能影响线上)。
Safety: delete is dry-run by default and requires --confirm; get/list/stats are
read-only and run immediately.
"""

import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

# ---------------------------------------------------------------------------
# 第三方依赖检查 / Third-party deps preflight
# ---------------------------------------------------------------------------
try:
    import requests
except ImportError:
    sys.stderr.write("ERROR: 需要 requests,请 pip install requests / requests required\n")
    sys.exit(2)


# ---------------------------------------------------------------------------
# 常量 / Constants  (与 upsert.py 保持一致, 故意不抽公共模块以保留两个脚本各自独立可运行)
# (Intentionally duplicated from upsert.py so each script remains standalone.)
# ---------------------------------------------------------------------------

# 环境 → 公网入口域名的映射(aksk 模式直连)。
# Env name → public ingress host mapping (used by aksk auth mode).
ENV_HOSTS = {
    "staging-us": "https://api-staging-us.vicohome.io",
    "staging-eu": "https://api-staging-eu.vicohome.io",
    "staging-cn": "https://api-stage.addx.live",
    "prod-us": "https://api-us.addx.live",
    "prod-eu": "https://api-eu.vicohome.io",
    "prod-cn": "https://api.addx.live",
}

# revenue-sharing 控制台域名(ldap 模式下走 /skill-api/invoke 代理)。
# revenue-sharing console hosts (ldap auth mode goes through /skill-api/invoke proxy).
RS_HOSTS = {
    "staging": "https://console-test.addx.live/api",
    "prod": "https://revenus-sharing-backend.addx.live",
}

# 后端 controller 的 path 前缀。
# Backend controller path prefix.
PATH_BASE = "/inner-api/setting-override"

# 单批 SN 上限 (delete-by-sn 沿用此值)。来源:SettingRepushService.MAX_BATCH_SIZE。
# Per-batch SN cap for delete-by-sn; mirrors backend constant.
MAX_BATCH_SIZE = 1000

# list 接口 pageSize 默认值与硬上限。来源:SettingOverrideAdminController.DEFAULT_PAGE_SIZE / MAX_PAGE_SIZE。
# Defaults & hard cap for list endpoint pageSize.
DEFAULT_PAGE_SIZE = 200
MAX_PAGE_SIZE = 1000


# ---------------------------------------------------------------------------
# 鉴权 / Auth
# ---------------------------------------------------------------------------

def env_var_required(name: str) -> str:
    """从环境变量读取必填值,缺失时立即退出。绝不打印 secret 内容。
    Read a required env var; exit immediately if missing. Never prints the value."""
    v = os.environ.get(name)
    if not v:
        sys.stderr.write(f"ERROR: 环境变量 {name} 未设置 / env var {name} not set\n")
        sys.exit(2)
    return v


def create_signed_url(row_url: str, ak: str, sk: str) -> str:
    """构造带签名的最终 URL,语义与 Java OpenApiAuthService.createSignedUrl 完全一致。

    步骤:
      1. unsignedUrl = "<row_url>?accessKey=<ak>&timestamp=<ts>"  (ts = 当前 UTC 秒)
      2. base64-decode SK 得到 raw key (16 字节)
      3. signature = base64( HmacSHA1(unsignedUrl_bytes, raw_key) )
      4. URL-safe 替换:'+' → '-', '/' → '_'  ('=' 保留)
      5. 返回 "<unsignedUrl>&signature=<signature>"

    Server 会反向去掉末尾 "&signature=...", 对剩余串再算一次, byte-for-byte 比对。
    """
    ts = str(int(time.time()))
    sep = "&" if "?" in row_url else "?"
    unsigned = f"{row_url}{sep}accessKey={ak}&timestamp={ts}"

    try:
        key = base64.b64decode(sk)
    except Exception as e:
        raise SystemExit(f"IOT_ADMIN_SK 不是合法 base64 / not valid base64: {e}")

    sig = hmac.new(key, unsigned.encode("utf-8"), hashlib.sha1).digest()
    sig_b64 = base64.b64encode(sig).decode("ascii").replace("+", "-").replace("/", "_")
    return f"{unsigned}&signature={sig_b64}"


# ---------------------------------------------------------------------------
# 鉴权上下文 / Auth context — 抽象 aksk / ldap 两种模式 (与 upsert.py 等价)
# ---------------------------------------------------------------------------

@dataclass
class AuthCtx:
    """承载本次运行的鉴权配置。结构与 upsert.py AuthCtx 一致, 故意复制以保留脚本独立。

    aksk 模式: 直接调 iot-service-cloud, 用 AK/SK HmacSHA1 签名 URL。
      - target_host: ENV_HOSTS[env]
      - ak, sk: IOT_ADMIN_AK / IOT_ADMIN_SK

    ldap 模式: 经 revenue-sharing /skill-api/invoke 代理转发, 用 LDAP token 鉴权。
      - rs_host: RS_HOSTS[rs_env]
      - ldap_token: 从 --skill-token-env 指定的环境变量读取
      - node_name: ConsoleServiceRegistry 里登记的 paas nodeName
    """
    mode: str                               # "aksk" or "ldap"
    target_host: Optional[str] = None       # aksk only
    ak: Optional[str] = None                # aksk only
    sk: Optional[str] = None                # aksk only
    rs_host: Optional[str] = None           # ldap only
    ldap_token: Optional[str] = None        # ldap only
    node_name: Optional[str] = None         # ldap only


# ---------------------------------------------------------------------------
# HTTP helpers — 支持 aksk / ldap 两种模式, 由 AuthCtx 决定路径
# ---------------------------------------------------------------------------

def sign_and_get(ctx: AuthCtx, path: str,
                 query: Optional[str] = None, timeout: int = 30):
    """对 GET 请求按 ctx.mode 选 aksk 或 ldap 路径发起。query 为不带前导 '?' 的查询串(可为 None)。"""
    if ctx.mode == "aksk":
        row_url = f"{ctx.target_host}{path}"
        if query:
            row_url = f"{row_url}?{query}"
        signed_url = create_signed_url(row_url, ctx.ak, ctx.sk)
        return requests.get(signed_url, timeout=timeout)
    # ldap: 走 /skill-api/invoke 代理, target path+query 整体编码到 invoke 的 path 参数
    return _ldap_invoke(ctx, "GET", path, query, body=None, timeout=timeout)


def sign_and_post(ctx: AuthCtx, path: str, payload: dict, timeout: int = 30):
    """对 POST 请求按 ctx.mode 选 aksk 或 ldap 路径发起。"""
    if ctx.mode == "aksk":
        row_url = f"{ctx.target_host}{path}"
        signed_url = create_signed_url(row_url, ctx.ak, ctx.sk)
        return requests.post(
            signed_url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            timeout=timeout,
        )
    return _ldap_invoke(ctx, "POST", path, query=None, body=payload, timeout=timeout)


def _ldap_invoke(ctx: AuthCtx, method: str, path: str,
                 query: Optional[str], body: Optional[dict],
                 timeout: int) -> requests.Response:
    """构造 <rs_host>/skill-api/invoke?nodeName=..&path=..&method=.. 并发起请求。

    - target path + query 整体编码到 invoke 的 path 参数, 让 revenue-sharing 透传给目标节点。
    - GET 也强制带 data='{}' 兼容 revenue-sharing HttpUtils 历史 null-body NPE; 修复后保留无害。
    - Authorization header 带 LDAP token。
    """
    target_path = path if not query else f"{path}?{query}"
    invoke_query = (
        f"nodeName={urllib.parse.quote(ctx.node_name or '', safe='')}"
        f"&path={urllib.parse.quote(target_path, safe='')}"
        f"&method={urllib.parse.quote(method, safe='')}"
    )
    url = f"{ctx.rs_host}/skill-api/invoke?{invoke_query}"
    headers = {
        "Content-Type": "application/json",
        "Authorization": ctx.ldap_token,
    }
    payload_bytes = json.dumps(body if body is not None else {}, ensure_ascii=False).encode("utf-8")
    if method == "GET":
        return requests.get(url, data=payload_bytes, headers=headers, timeout=timeout)
    if method == "POST":
        return requests.post(url, data=payload_bytes, headers=headers, timeout=timeout)
    raise SystemExit(f"ldap: unsupported method {method}")


def parse_result(resp: requests.Response) -> dict:
    """解析 Result 信封:返回 {ok, httpStatus, result, message, data, raw}。
    与 org.addx.iot.common.vo.Result 约定:result == 0 才算成功。"""
    out = {"httpStatus": resp.status_code, "raw": resp.text, "ok": False,
           "result": None, "message": None, "data": None}
    try:
        parsed = resp.json()
    except ValueError:
        return out
    if not isinstance(parsed, dict):
        return out
    out["result"] = parsed.get("result")
    out["message"] = parsed.get("message") or parsed.get("msg")
    out["data"] = parsed.get("data")
    out["ok"] = (resp.status_code == 200 and parsed.get("result") == 0)
    return out


# ---------------------------------------------------------------------------
# 子命令实现 / Subcommand handlers
# ---------------------------------------------------------------------------

def cmd_get(args, ctx: AuthCtx) -> int:
    """GET /inner-api/setting-override?sn=<sn>  单 SN 查询。"""
    # 这里的 SN 不做严格 hex 校验:验证器允许长度 1-64 的任意非空串,以后扩展更友好。
    query = f"sn={args.sn}"
    resp = sign_and_get(ctx, PATH_BASE, query=query)
    parsed = parse_result(resp)

    if parsed["ok"]:
        # data 即 SettingOverrideRowView, 直接 pretty print。
        print(json.dumps(parsed["data"], ensure_ascii=False, indent=2))
        return 0

    if parsed["result"] == 404:
        # 与 controller 约定:未命中 result=404, 不算"错误"。
        sys.stderr.write(f"NOT FOUND: sn={args.sn}\n")
        return 4

    sys.stderr.write(
        f"ERROR: http={parsed['httpStatus']} result={parsed['result']} "
        f"message={parsed['message']} body={parsed['raw'][:500]}\n"
    )
    return 1


def cmd_stats(args, ctx: AuthCtx) -> int:
    """GET /inner-api/setting-override/stats  按 experiment 聚合的活跃覆盖数。"""
    resp = sign_and_get(ctx, f"{PATH_BASE}/stats")
    parsed = parse_result(resp)

    if not parsed["ok"]:
        sys.stderr.write(
            f"ERROR: http={parsed['httpStatus']} result={parsed['result']} "
            f"message={parsed['message']} body={parsed['raw'][:500]}\n"
        )
        return 1

    rows = parsed["data"] or []
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        # 给人看的表格:experiment | deviceCount。
        # SQL 层已只统计 active 行;空 experiment 桶在 controller 改写为 (unspecified)。
        if not rows:
            print("(no active rows)")
        else:
            width = max(len("experiment"),
                        max(len(str(r.get("experiment", ""))) for r in rows))
            print(f"{'experiment'.ljust(width)}  deviceCount")
            print(f"{'-' * width}  -----------")
            total = 0
            for r in sorted(rows, key=lambda x: -int(x.get("deviceCount") or 0)):
                exp = str(r.get("experiment", ""))
                cnt = int(r.get("deviceCount") or 0)
                print(f"{exp.ljust(width)}  {cnt}")
                total += cnt
            print(f"{'-' * width}  -----------")
            print(f"{'(total)'.ljust(width)}  {total}")
    return 0


def cmd_list(args, ctx: AuthCtx) -> int:
    """GET /inner-api/setting-override?experiment=<tag>&pageNum=<n>&pageSize=<k>。

    --all 时自动翻页拉全量,逐页输出 JSONL 到 --out (或 stdout)。
    单页模式 (--all 不开) 直接 pretty print 当页数据。
    """
    page_size = args.page_size
    if page_size <= 0 or page_size > MAX_PAGE_SIZE:
        sys.exit(f"--page-size 必须在 [1, {MAX_PAGE_SIZE}] / out of range")

    # 单页模式:仅拉一页,适合人工抽查。
    if not args.all:
        query = (f"experiment={args.experiment}&pageNum={args.page_num}"
                 f"&pageSize={page_size}")
        resp = sign_and_get(ctx, PATH_BASE, query=query)
        parsed = parse_result(resp)
        if not parsed["ok"]:
            sys.stderr.write(
                f"ERROR: http={parsed['httpStatus']} result={parsed['result']} "
                f"message={parsed['message']} body={parsed['raw'][:500]}\n"
            )
            return 1
        print(json.dumps(parsed["data"], ensure_ascii=False, indent=2))
        return 0

    # --all 模式:翻页直到取尽。一行一条 row,落到 --out 或 stdout。
    out_fh = open(args.out, "w", encoding="utf-8") if args.out else sys.stdout
    try:
        page_num = 1
        total_returned = 0
        total_known = None
        while True:
            query = (f"experiment={args.experiment}&pageNum={page_num}"
                     f"&pageSize={page_size}")
            resp = sign_and_get(ctx, PATH_BASE, query=query)
            parsed = parse_result(resp)
            if not parsed["ok"]:
                sys.stderr.write(
                    f"ERROR page#{page_num}: http={parsed['httpStatus']} "
                    f"result={parsed['result']} message={parsed['message']}\n"
                )
                return 1

            data = parsed["data"] or {}
            rows = data.get("rows") or []
            total_known = data.get("total")
            for row in rows:
                out_fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            out_fh.flush()
            total_returned += len(rows)

            sys.stderr.write(
                f"[page {page_num}] returned={len(rows)} cumulative={total_returned} "
                f"total={total_known}\n"
            )

            # 终止条件:本页不满 page_size 或 累计 ≥ total。
            if len(rows) < page_size:
                break
            if total_known is not None and total_returned >= int(total_known):
                break
            page_num += 1
            # 翻页间小睡, 防 RPS 抖动。
            time.sleep(args.qps_sleep)
    finally:
        if out_fh is not sys.stdout:
            out_fh.close()
    sys.stderr.write(f"[done] total_returned={total_returned} total_known={total_known}\n")
    return 0


def _load_sns_for_delete(args) -> List[str]:
    """从 --sn / --sn-file 解析 SN 列表。
    --sn-file 一行一个 SN, 跳过空行;同 list 内自动去重。"""
    sns: List[str] = []
    seen = set()
    if args.sns:
        for s in args.sns.split(","):
            s = s.strip()
            if s and s not in seen:
                seen.add(s)
                sns.append(s)
    if args.sn_file:
        for line in Path(args.sn_file).expanduser().read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if s in seen:
                continue
            seen.add(s)
            sns.append(s)
    return sns


def cmd_delete(args, ctx: AuthCtx) -> int:
    """POST /inner-api/setting-override/delete  按 SN 列表 或 按 experiment 删除。

    两种模式互斥;dry-run 默认,只有 --confirm 才真正发请求。
    """
    by_experiment = args.experiment is not None
    sns = _load_sns_for_delete(args)
    by_sn = bool(sns)
    # 必须二选一,与 controller 端 CODE_DELETE_AMBIGUOUS 行为一致。
    if by_experiment == by_sn:
        sys.exit("必须二选一:--experiment <tag>  或  --sn / --sn-file / "
                 "must specify exactly one delete mode")

    # 输出目录(每次执行新建,失败批落盘到这里方便 replay)。
    out_dir = Path(args.out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    run_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = out_dir / f"delete-{run_ts}-{args.env}"
    run_dir.mkdir(parents=True, exist_ok=True)
    failed_dir = run_dir / "failed"
    failed_dir.mkdir(exist_ok=True)
    progress_path = run_dir / "progress.jsonl"
    summary_path = run_dir / "summary.json"

    # 计划打印。
    print(f"[plan] env={args.env}")
    if ctx.mode == "aksk":
        print(f"[plan] auth=aksk host={ctx.target_host}")
    else:
        print(f"[plan] auth=ldap rs-host={ctx.rs_host} node-name={ctx.node_name}")
    print(f"[plan] mode={'experiment' if by_experiment else 'sn-list'}")
    if by_experiment:
        print(f"[plan] experiment={args.experiment}")
        print(f"[plan] note: 删除全量行, 但 repush 名单只覆盖前 1000 个 SN "
              f"(MR 2063 已知 P0)")
    else:
        print(f"[plan] sn-count={len(sns)} batches="
              f"{(len(sns) + args.batch_size - 1) // args.batch_size}")
    print(f"[plan] repush={args.repush}")
    print(f"[plan] dry-run={'NO (will POST)' if args.confirm else 'YES (no POST)'}")
    print(f"[plan] run_dir={run_dir}")
    print()

    summary = {
        "env": args.env,
        "auth": ctx.mode,
        "host": ctx.target_host if ctx.mode == "aksk" else ctx.rs_host,
        "nodeName": ctx.node_name,
        "mode": "experiment" if by_experiment else "sn-list",
        "experiment": args.experiment,
        "snCount": len(sns),
        "repush": bool(args.repush),
        "dryRun": not args.confirm,
        "startedAt": run_ts,
        "totals": {"deleted": 0, "repushFailed": 0, "failedBatches": 0},
    }

    progress_fh = open(progress_path, "a", encoding="utf-8")

    def _exec_one(payload: dict, batch_label: str) -> bool:
        """发一次 delete 请求, 把进度/失败上下文落盘。返回 True 表示该批成功。"""
        progress_record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "batch": batch_label,
            "payloadSummary": {
                "snCount": len(payload.get("serialNumbers") or []),
                "experiment": payload.get("experiment"),
                "repushSetting": payload.get("repushSetting"),
            },
        }
        if not args.confirm:
            progress_record["action"] = "dry-run"
            print(f"[dry] batch={batch_label} (no POST)")
            progress_fh.write(json.dumps(progress_record, ensure_ascii=False) + "\n")
            progress_fh.flush()
            return True

        try:
            resp = sign_and_post(ctx, f"{PATH_BASE}/delete", payload)
        except requests.RequestException as e:
            progress_record.update({"action": "network-error", "error": str(e)})
            with open(failed_dir / f"{batch_label}.json", "w", encoding="utf-8") as f:
                json.dump({"payload": payload, "error": str(e)}, f, ensure_ascii=False)
            print(f"[err] batch={batch_label} network: {e}")
            progress_fh.write(json.dumps(progress_record, ensure_ascii=False) + "\n")
            progress_fh.flush()
            summary["totals"]["failedBatches"] += 1
            return False

        parsed = parse_result(resp)
        progress_record.update({
            "action": "post",
            "httpStatus": parsed["httpStatus"],
            "result": parsed["result"],
        })
        if parsed["ok"]:
            data = parsed["data"] or {}
            deleted = int(data.get("deletedCount") or 0)
            repush_failed = list(data.get("repushFailedSerialNumbers") or [])
            summary["totals"]["deleted"] += deleted
            summary["totals"]["repushFailed"] += len(repush_failed)
            progress_record["deletedCount"] = deleted
            progress_record["repushFailedCount"] = len(repush_failed)
            if repush_failed:
                with open(failed_dir / f"{batch_label}-repushFailed.json",
                          "w", encoding="utf-8") as f:
                    json.dump(repush_failed, f, ensure_ascii=False)
            print(f"[ok ] batch={batch_label} deleted={deleted} "
                  f"repushFailed={len(repush_failed)}")
            progress_fh.write(json.dumps(progress_record, ensure_ascii=False) + "\n")
            progress_fh.flush()
            return True

        summary["totals"]["failedBatches"] += 1
        progress_record["responseBody"] = parsed["raw"][:2000]
        with open(failed_dir / f"{batch_label}.json", "w", encoding="utf-8") as f:
            json.dump({
                "payload": payload,
                "httpStatus": parsed["httpStatus"],
                "responseBody": parsed["raw"],
            }, f, ensure_ascii=False)
        print(f"[err] batch={batch_label} http={parsed['httpStatus']} "
              f"result={parsed['result']} body={(parsed['raw'] or '')[:200]}")
        progress_fh.write(json.dumps(progress_record, ensure_ascii=False) + "\n")
        progress_fh.flush()
        return False

    try:
        if by_experiment:
            # 单次调用即可; controller 内部分页拉所有 SN 再删, 客户端无需切片。
            payload = {"experiment": args.experiment, "repushSetting": bool(args.repush)}
            _exec_one(payload, batch_label="experiment-0000")
        else:
            # SN 列表模式:按 batch_size 切片, 多批次串行。
            for i in range(0, len(sns), args.batch_size):
                chunk = sns[i : i + args.batch_size]
                payload = {"serialNumbers": chunk, "repushSetting": bool(args.repush)}
                ok = _exec_one(payload, batch_label=f"sn-{i // args.batch_size:04d}")
                # 失败不立即停, 让后续批继续, 全量结束后看 summary 统一处理。
                if args.qps_sleep > 0:
                    time.sleep(args.qps_sleep)
                if not ok and args.stop_on_error:
                    print("[abort] --stop-on-error 触发, 中断后续批 / aborting")
                    break
    finally:
        progress_fh.close()
        summary["finishedAt"] = datetime.now(timezone.utc).isoformat()
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)

    print()
    print(f"[done] summary 已写入 / written to {summary_path}")
    print(json.dumps(summary["totals"], ensure_ascii=False))
    return 0 if summary["totals"]["failedBatches"] == 0 else 1


# ---------------------------------------------------------------------------
# CLI / 命令行
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("--env", required=True, choices=sorted(ENV_HOSTS.keys()),
                   help="目标环境名(aksk 模式直连; ldap 模式仅作日志标签, 真实路由由 --node-name 决定)/ target env")
    # ---- 鉴权模式 ----
    p.add_argument("--auth", choices=["aksk", "ldap"], default="ldap",
                   help="鉴权模式: ldap(默认, 经 revenue-sharing /skill-api/invoke 代理, 需 LDAP token) "
                        "或 aksk(直连 iot-service-cloud, 需 IOT_ADMIN_AK/SK, 仅在用户显式要求时用)")
    p.add_argument("--rs-env", choices=sorted(RS_HOSTS.keys()), default=None,
                   help="ldap 模式必填: revenue-sharing 控制台环境, "
                        "staging→console-test.addx.live/api, "
                        "prod→revenus-sharing-backend.addx.live (待部署, 当前仅 staging 可用)")
    p.add_argument("--node-name", default=None,
                   help="ldap 模式必填: ConsoleServiceRegistry 登记的 paas nodeName")
    p.add_argument("--skill-token-env", default="SKILL_LDAP_TOKEN",
                   help="ldap 模式: 存放 LDAP token 的环境变量名, 默认 SKILL_LDAP_TOKEN "
                        "(token 由 POST <rs-host>/skill-api/login 获得, 3 天有效)")
    sub = p.add_subparsers(dest="cmd", required=True, help="子命令 / subcommand")

    # ---- get ----
    p_get = sub.add_parser("get", help="单 SN 查询 / single-SN query")
    p_get.add_argument("--sn", required=True, help="设备 SN")

    # ---- stats ----
    p_stats = sub.add_parser("stats", help="按 experiment 聚合活跃覆盖数 / aggregate counts")
    p_stats.add_argument("--json", action="store_true",
                         help="原样输出 JSON 数组(默认渲染表格) / raw JSON")

    # ---- list ----
    p_list = sub.add_parser("list", help="按 experiment 分页列表 / list by experiment")
    p_list.add_argument("--experiment", required=True, help="experiment 标签")
    p_list.add_argument("--page-num", type=int, default=1,
                        help="单页模式时取第几页, 默认 1")
    p_list.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE,
                        help=f"每页大小, 默认 {DEFAULT_PAGE_SIZE}, 上限 {MAX_PAGE_SIZE}")
    p_list.add_argument("--all", action="store_true",
                        help="自动翻页拉全量, 输出 JSONL")
    p_list.add_argument("--out", default=None,
                        help="--all 模式下 JSONL 输出文件路径; 缺省时写 stdout")
    p_list.add_argument("--qps-sleep", type=float, default=0.2,
                        help="--all 模式下翻页间 sleep 秒数, 默认 0.2")

    # ---- delete ----
    p_del = sub.add_parser("delete", help="批量删除 / batch delete")
    p_del.add_argument("--experiment", default=None,
                       help="按 experiment 删除(整批); 与 --sn / --sn-file 互斥")
    p_del.add_argument("--sns", default=None,
                       help="按 SN 列表删除, 逗号分隔; 与 --experiment 互斥")
    p_del.add_argument("--sn-file", default=None,
                       help="按 SN 列表删除, 文件路径(一行一个); 与 --experiment 互斥")
    p_del.add_argument("--repush", action="store_true",
                       help="设置 repushSetting=true, 让设备拿回默认")
    p_del.add_argument("--batch-size", type=int, default=MAX_BATCH_SIZE,
                       help=f"SN 列表模式下单批 SN 数, 默认 {MAX_BATCH_SIZE}")
    p_del.add_argument("--qps-sleep", type=float, default=0.5,
                       help="批间 sleep 秒数, 默认 0.5")
    p_del.add_argument("--stop-on-error", action="store_true",
                       help="某批失败立即中断, 默认是继续后续批")
    p_del.add_argument("--confirm", action="store_true",
                       help="真正发请求, 不加为 dry-run")
    p_del.add_argument("--out-dir", default="./out",
                       help="输出根目录 / run output dir")

    return p.parse_args()


def build_auth_ctx(args) -> AuthCtx:
    """根据 --auth 选项构造 AuthCtx 并校验必填项。

    aksk 模式必填: env 环境变量 IOT_ADMIN_AK / IOT_ADMIN_SK
    ldap 模式必填: --rs-env, --node-name, env 环境变量 <--skill-token-env>(默认 SKILL_LDAP_TOKEN)
    """
    if args.auth == "aksk":
        return AuthCtx(
            mode="aksk",
            target_host=ENV_HOSTS[args.env],
            ak=env_var_required("IOT_ADMIN_AK"),
            sk=env_var_required("IOT_ADMIN_SK"),
        )
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


def main() -> int:
    args = parse_args()
    ctx = build_auth_ctx(args)

    if args.cmd == "get":
        return cmd_get(args, ctx)
    if args.cmd == "stats":
        return cmd_stats(args, ctx)
    if args.cmd == "list":
        return cmd_list(args, ctx)
    if args.cmd == "delete":
        return cmd_delete(args, ctx)

    sys.exit(f"unknown subcommand: {args.cmd}")


if __name__ == "__main__":
    sys.exit(main())
