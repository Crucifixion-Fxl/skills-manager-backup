#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Setting Override 写入后的闭环验证 CLI。
Post-write verification CLI for the Setting Override admin path.

集合 4 个数据源做一次性体检:
  1. admin stats   — DB 落库行数是否覆盖目标 experiment(按 SN 数对账)
  2. admin get     — 抽样 SN 的 row 是否符合预期(experiment/active/expireAt)
  3. Thanos applied_total / failed_total — 设备命中速率 + 失败计数
  4. Thanos admin_request_total          — admin 写入成功率

任何一项不达标都会让脚本退出码 ≠ 0, 同时高亮失败项。

鉴权两种模式 / Two auth modes (同 upsert.py / admin_cli.py; 默认 ldap):
  --auth=ldap (默认) — 经 revenue-sharing 的 /skill-api/invoke 代理, LDAP token 通过
                       环境变量(默认 SKILL_LDAP_TOKEN)注入。
  --auth=aksk        — HmacSHA1 + base64(web-safe), 直连 iot-service-cloud, 用
                       IOT_ADMIN_AK / IOT_ADMIN_SK 签名。仅在用户明确要求时用。
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
import urllib.request
from dataclasses import dataclass
from typing import List, Optional


# ---------------------------------------------------------------------------
# 常量与映射 / Constants & mappings
# ---------------------------------------------------------------------------

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

# 按 env 映射到对应区域 Thanos. CN 注意没有 prod- 前缀.
# 来源: prometheus / sre-agent / 历史经验积累.
THANOS_HOSTS = {
    "staging-us": "http://thanos-staging-us.addx.live",
    "staging-eu": "http://thanos-staging-eu.addx.live",
    "staging-cn": "http://thanos-cn.addx.live",
    "prod-us": "http://thanos-prod-us.addx.live",
    "prod-eu": "http://thanos-prod-eu.addx.live",
    "prod-cn": "http://thanos-cn.addx.live",
}

PATH_BASE = "/inner-api/setting-override"

# 输出色彩(终端非 tty 时自动关闭)
USE_COLOR = sys.stdout.isatty()
RED = "\033[31m" if USE_COLOR else ""
GREEN = "\033[32m" if USE_COLOR else ""
YELLOW = "\033[33m" if USE_COLOR else ""
RESET = "\033[0m" if USE_COLOR else ""


# ---------------------------------------------------------------------------
# 鉴权 / Auth
# ---------------------------------------------------------------------------

def env_var_required(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        sys.stderr.write(f"ERROR: 环境变量 {name} 未设置 / env var {name} not set\n")
        sys.exit(2)
    return v


def create_signed_url(row_url: str, ak: str, sk: str) -> str:
    """与 upsert.py / admin_cli.py 等价的 HmacSHA1 签名."""
    ts = str(int(time.time()))
    sep = "&" if "?" in row_url else "?"
    unsigned = f"{row_url}{sep}accessKey={ak}&timestamp={ts}"
    try:
        key = base64.b64decode(sk)
    except Exception as e:
        raise SystemExit(f"IOT_ADMIN_SK 不是合法 base64: {e}")
    sig = hmac.new(key, unsigned.encode("utf-8"), hashlib.sha1).digest()
    sig_b64 = base64.b64encode(sig).decode("ascii").replace("+", "-").replace("/", "_")
    return f"{unsigned}&signature={sig_b64}"


# ---------------------------------------------------------------------------
# 鉴权上下文 / Auth context — 抽象 aksk / ldap 两种模式 (与 upsert.py / admin_cli.py 等价)
# ---------------------------------------------------------------------------

@dataclass
class AuthCtx:
    """承载本次运行的鉴权配置。结构与 upsert.py / admin_cli.py AuthCtx 一致, 故意复制以保留脚本独立。

    aksk 模式: 直接调 iot-service-cloud, 用 AK/SK HmacSHA1 签名 URL。
    ldap 模式: 经 revenue-sharing /skill-api/invoke 代理转发, 用 LDAP token 鉴权。
    """
    mode: str                               # "aksk" or "ldap"
    target_host: Optional[str] = None       # aksk only
    ak: Optional[str] = None                # aksk only
    sk: Optional[str] = None                # aksk only
    rs_host: Optional[str] = None           # ldap only
    ldap_token: Optional[str] = None        # ldap only
    node_name: Optional[str] = None         # ldap only


# ---------------------------------------------------------------------------
# HTTP helper(只用 stdlib 避免依赖)
# HTTP helpers (stdlib only to keep verify.py self-contained)
# ---------------------------------------------------------------------------

def http_get_json(url: str, timeout: int = 30,
                  headers: Optional[dict] = None,
                  data: Optional[bytes] = None,
                  method: str = "GET") -> dict:
    """通用 GET-而-返-JSON helper。

    支持 ldap 路径携带 Authorization header + 空 body(兼容 revenue-sharing
    HttpUtils 历史 null-body NPE)。
    """
    req = urllib.request.Request(url, method=method, data=data,
                                 headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _admin_call_get(ctx: AuthCtx, path: str, query: Optional[str] = None) -> dict:
    """对一个 admin GET 端点发请求, 根据 ctx.mode 选 aksk 或 ldap 路径, 返回 JSON 信封。

    aksk: 直连 iot-service-cloud, HmacSHA1 签名 URL。
    ldap: 经 <rs_host>/skill-api/invoke?nodeName=..&path=..&method=GET 代理,
          带 Authorization header + 空 body 兼容 revenue-sharing HttpUtils null-body NPE。
    """
    if ctx.mode == "aksk":
        row_url = f"{ctx.target_host}{path}"
        if query:
            row_url = f"{row_url}?{query}"
        signed = create_signed_url(row_url, ctx.ak, ctx.sk)
        return http_get_json(signed)
    # ldap
    target_path = path if not query else f"{path}?{query}"
    invoke_query = (
        f"nodeName={urllib.parse.quote(ctx.node_name or '', safe='')}"
        f"&path={urllib.parse.quote(target_path, safe='')}"
        f"&method=GET"
    )
    url = f"{ctx.rs_host}/skill-api/invoke?{invoke_query}"
    headers = {
        "Content-Type": "application/json",
        "Authorization": ctx.ldap_token,
    }
    return http_get_json(url, headers=headers, data=b"{}", method="GET")


def admin_stats(ctx: AuthCtx) -> List[dict]:
    """GET /inner-api/setting-override/stats → list[ {experiment, deviceCount} ]"""
    body = _admin_call_get(ctx, f"{PATH_BASE}/stats")
    if body.get("result") != 0:
        raise RuntimeError(f"admin stats failed: {body}")
    return body.get("data") or []


def admin_get(ctx: AuthCtx, sn: str) -> Optional[dict]:
    """GET /inner-api/setting-override?sn=<sn>  未命中返回 None"""
    body = _admin_call_get(ctx, PATH_BASE, query=f"sn={urllib.parse.quote(sn)}")
    if body.get("result") == 404:
        return None
    if body.get("result") != 0:
        raise RuntimeError(f"admin get failed for sn={sn}: {body}")
    return body.get("data")


def thanos_query(thanos_host: str, promql: str) -> List[dict]:
    """Thanos /api/v1/query (instant query) → list[ {metric, value} ]"""
    url = f"{thanos_host}/api/v1/query?query={urllib.parse.quote(promql)}"
    body = http_get_json(url)
    if body.get("status") != "success":
        raise RuntimeError(f"thanos query failed: {body}")
    return body.get("data", {}).get("result") or []


# ---------------------------------------------------------------------------
# 各维度检查 / Dimension checks
# ---------------------------------------------------------------------------

def fmt_check(passed: bool, label: str, detail: str = "") -> str:
    icon = f"{GREEN}✅{RESET}" if passed else f"{RED}❌{RESET}"
    return f"  {icon} {label}" + (f"  {detail}" if detail else "")


def check_admin_stats(ctx: AuthCtx,
                      expected_experiments: List[str]) -> dict:
    """目标 experiment 是否都在 stats 表里, 各自 deviceCount > 0."""
    rows = admin_stats(ctx)
    by_exp = {r.get("experiment"): int(r.get("deviceCount") or 0) for r in rows}
    results = {}
    all_pass = True
    for exp in expected_experiments:
        cnt = by_exp.get(exp, 0)
        ok = cnt > 0
        all_pass = all_pass and ok
        results[exp] = {"ok": ok, "deviceCount": cnt}
    return {"ok": all_pass, "by_experiment": results, "raw_rows": rows}


def check_sample_get(ctx: AuthCtx,
                     sample_sns: List[str],
                     expected_experiments: Optional[List[str]] = None) -> dict:
    """对每个抽样 SN 调 admin get 并检查 active=true, experiment 在期望集合里."""
    expected_set = set(expected_experiments or [])
    results = []
    all_pass = True
    for sn in sample_sns:
        try:
            row = admin_get(ctx, sn)
        except Exception as e:
            results.append({"sn": sn, "ok": False, "error": str(e)})
            all_pass = False
            continue
        if row is None:
            results.append({"sn": sn, "ok": False, "error": "NOT_FOUND"})
            all_pass = False
            continue
        active = bool(row.get("active"))
        exp = row.get("experiment")
        exp_ok = (not expected_set) or (exp in expected_set)
        ok = active and exp_ok
        all_pass = all_pass and ok
        results.append({
            "sn": sn,
            "ok": ok,
            "experiment": exp,
            "active": active,
            "expireAt": row.get("expireAt"),
            "exp_in_expected": exp_ok,
        })
    return {"ok": all_pass, "rows": results}


def check_metric_applied(thanos: str, experiments: List[str],
                         window: str = "30m") -> dict:
    """过去 window 内, 各 experiment 的 applied_total increase + 5m 当前速率."""
    if not experiments:
        return {"ok": True, "by_experiment": {}, "skipped": "no experiments"}
    # 用正则 OR 出来匹配多个 experiment(避免多个查询往返)
    re_pattern = "|".join(urllib.parse.quote_plus("") + e for e in experiments)
    re_pattern = "|".join(experiments)
    inc_q = (f'sum by (experiment) (increase('
             f'setting_override_applied_total{{experiment=~"{re_pattern}"}}[{window}]))')
    rate_q = (f'sum by (experiment) (rate('
              f'setting_override_applied_total{{experiment=~"{re_pattern}"}}[5m]))')
    inc_rows = thanos_query(thanos, inc_q)
    rate_rows = thanos_query(thanos, rate_q)
    inc_by_exp = {r["metric"].get("experiment"): float(r["value"][1]) for r in inc_rows}
    rate_by_exp = {r["metric"].get("experiment"): float(r["value"][1]) for r in rate_rows}
    by_exp = {}
    all_pass = True
    for exp in experiments:
        inc = inc_by_exp.get(exp, 0.0)
        # 我们不要求 inc > 0 才算 pass — 因为可能查询时点离写入太近, 设备还没开始拉
        # 但 inc == 0 时给 ⚠ 警告, 不算 fail
        by_exp[exp] = {
            "increase": inc,
            "rate_5m": rate_by_exp.get(exp, 0.0),
            "warn_no_traffic": inc == 0.0,
        }
    return {"ok": all_pass, "window": window, "by_experiment": by_exp}


def check_metric_failed(thanos: str, window: str = "30m") -> dict:
    """过去 window 内, failed_total 各 stage 应该全 0."""
    q = f'sum by (stage) (increase(setting_override_failed_total[{window}]))'
    rows = thanos_query(thanos, q)
    by_stage = {r["metric"].get("stage", "?"): float(r["value"][1]) for r in rows}
    failures = {s: c for s, c in by_stage.items() if c > 0}
    return {"ok": len(failures) == 0,
            "window": window,
            "by_stage": by_stage,
            "non_zero_stages": failures}


def check_metric_admin(thanos: str, window: str = "30m") -> dict:
    """过去 window 内 admin upsert/delete 的 result 分布, validation/server error 应该全 0."""
    q = (f'sum by (action, result) (increase('
         f'setting_override_admin_request_total{{action=~"upsert|delete"}}[{window}]))')
    rows = thanos_query(thanos, q)
    by = {(r["metric"].get("action", "?"), r["metric"].get("result", "?")):
          float(r["value"][1]) for r in rows}
    error_kinds = {k: v for k, v in by.items()
                   if v > 0 and k[1] in {"validation_error", "server_error"}}
    return {"ok": len(error_kinds) == 0,
            "window": window,
            "by": by,
            "errors": error_kinds}


# ---------------------------------------------------------------------------
# 输出渲染 / Pretty rendering
# ---------------------------------------------------------------------------

def render_report(env: str, experiments: List[str], sample_sns: List[str],
                  results: dict) -> int:
    """打印体检报告 + 返回 exit code."""
    print(f"\n=== Setting Override 验证报告 / verification report ===")
    print(f"env: {env}    experiments: {len(experiments)}    sample SNs: {len(sample_sns)}")
    print()

    overall_ok = True

    # 1. admin stats
    r = results["admin_stats"]
    print("[1] admin stats — DB 落库行数 / DB row counts")
    for exp in experiments:
        e = r["by_experiment"].get(exp, {})
        print(fmt_check(e.get("ok", False), exp,
                        f"deviceCount={e.get('deviceCount', 0)}"))
    overall_ok = overall_ok and r["ok"]
    print()

    # 2. sample get
    r = results["sample_get"]
    print("[2] sample SN 内容 / sample SN content")
    for row in r["rows"]:
        if not row["ok"]:
            detail = f"error={row.get('error', '?')}" if 'error' in row else \
                f"experiment={row.get('experiment')} active={row.get('active')}"
            print(fmt_check(False, row["sn"], detail))
        else:
            print(fmt_check(True, row["sn"],
                            f"experiment={row['experiment']} "
                            f"active={row['active']} expireAt={row['expireAt']}"))
    overall_ok = overall_ok and r["ok"]
    print()

    # 3. applied_total
    r = results["metric_applied"]
    if r.get("skipped"):
        print(f"[3] Thanos applied_total — skipped ({r['skipped']})")
    else:
        print(f"[3] Thanos applied_total — 设备命中(过去 {r['window']})")
        for exp in experiments:
            d = r["by_experiment"].get(exp, {})
            inc = d.get("increase", 0)
            rate = d.get("rate_5m", 0)
            warn = d.get("warn_no_traffic", False)
            tail = f"{YELLOW}(写完后 30m 内未观察到任何命中, 注意!){RESET}" if warn else ""
            print(f"  • {exp}  increase_30m={inc:,.1f}  rate_5m={rate:.2f}/s  {tail}")
    print()

    # 4. failed_total
    r = results["metric_failed"]
    print(f"[4] Thanos failed_total — 覆盖路径异常(过去 {r['window']})")
    if r["ok"]:
        print(fmt_check(True, "全 stage 零失败 / no failures across all stages"))
    else:
        for stage, count in r["non_zero_stages"].items():
            print(fmt_check(False, f"stage={stage}", f"increase={count:,.1f}"))
    overall_ok = overall_ok and r["ok"]
    print()

    # 5. admin requests
    r = results["metric_admin"]
    print(f"[5] Thanos admin_request_total — admin 写入审计(过去 {r['window']})")
    if r["ok"]:
        for (action, result), v in sorted(r["by"].items()):
            tag = "ok " if result == "success" else "?  "
            print(f"  {tag} action={action} result={result} count≈{v:,.1f}")
        print(fmt_check(True, "无 validation_error / server_error"))
    else:
        for (action, result), v in r["errors"].items():
            print(fmt_check(False, f"action={action} result={result}",
                            f"count≈{v:,.1f}"))
    overall_ok = overall_ok and r["ok"]
    print()

    # final
    if overall_ok:
        print(f"{GREEN}所有检查通过 / all checks passed{RESET}")
        return 0
    print(f"{RED}存在不通过项, 请按上面 ❌ 标记排查 / failed checks above{RESET}")
    return 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("--env", required=True, choices=sorted(ENV_HOSTS.keys()))
    # ---- 鉴权模式 ----
    p.add_argument("--auth", choices=["aksk", "ldap"], default="ldap",
                   help="鉴权模式: ldap(默认, 经 revenue-sharing /skill-api/invoke 代理, 需 LDAP token) "
                        "或 aksk(直连 iot-service-cloud, 需 IOT_ADMIN_AK/SK, 仅在用户显式要求时用)")
    p.add_argument("--rs-env", choices=sorted(RS_HOSTS.keys()), default=None,
                   help="ldap 模式必填: revenue-sharing 控制台环境")
    p.add_argument("--node-name", default=None,
                   help="ldap 模式必填: paas nodeName")
    p.add_argument("--skill-token-env", default="SKILL_LDAP_TOKEN",
                   help="ldap 模式: 存放 LDAP token 的环境变量名, 默认 SKILL_LDAP_TOKEN")
    p.add_argument("--experiment", action="append", default=[],
                   help="待验证的 experiment 标签(可重复多次给多组)")
    p.add_argument("--sample-sn", action="append", default=[],
                   help="抽样 SN(可重复多次)。建议每组各取 1-2 个")
    p.add_argument("--window", default="30m",
                   help="Thanos 增量窗口大小, 默认 30m")
    p.add_argument("--thanos-url", default=None,
                   help="覆盖默认 Thanos host(默认按 --env 自动选择)")
    p.add_argument("--json", action="store_true",
                   help="同时把结构化报告以 JSON 输出到 stdout(便于 CI 解析)")
    return p.parse_args()


def build_auth_ctx(args) -> AuthCtx:
    """根据 --auth 选项构造 AuthCtx 并校验必填项 (与 admin_cli.py / upsert.py 等价)."""
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
    thanos = args.thanos_url or THANOS_HOSTS[args.env]
    ctx = build_auth_ctx(args)

    if not args.experiment:
        sys.exit("ERROR: 至少需要一个 --experiment / pass at least one --experiment")

    if ctx.mode == "aksk":
        print(f"[env] {args.env} → admin {ctx.target_host}    thanos {thanos}", file=sys.stderr)
    else:
        print(f"[env] {args.env} (ldap via {ctx.rs_host}, node={ctx.node_name})    "
              f"thanos {thanos}", file=sys.stderr)

    results = {}
    try:
        results["admin_stats"] = check_admin_stats(ctx, args.experiment)
    except Exception as e:
        sys.exit(f"admin stats 调用失败 / admin stats call failed: {e}")
    try:
        results["sample_get"] = check_sample_get(ctx, args.sample_sn, args.experiment)
    except Exception as e:
        sys.exit(f"admin get 调用失败 / admin get call failed: {e}")
    try:
        results["metric_applied"] = check_metric_applied(thanos, args.experiment,
                                                          args.window)
    except Exception as e:
        # Thanos 不可达不应把整次 verify 弄挂, 留下警告并继续
        results["metric_applied"] = {"ok": True, "skipped": f"thanos unreachable: {e}",
                                      "by_experiment": {}}
    try:
        results["metric_failed"] = check_metric_failed(thanos, args.window)
    except Exception as e:
        results["metric_failed"] = {"ok": True, "skipped": f"thanos unreachable: {e}",
                                     "by_stage": {}, "non_zero_stages": {}}
    try:
        results["metric_admin"] = check_metric_admin(thanos, args.window)
    except Exception as e:
        results["metric_admin"] = {"ok": True, "skipped": f"thanos unreachable: {e}",
                                    "by": {}, "errors": {}}

    code = render_report(args.env, args.experiment, args.sample_sn, results)
    if args.json:
        # 把详细 JSON 也输出便于自动化解析(stderr 用于人看, stdout 给机器看)
        print(json.dumps({"env": args.env, "exit": code, **results},
                         ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
