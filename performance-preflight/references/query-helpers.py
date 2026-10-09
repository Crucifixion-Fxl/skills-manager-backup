#!/usr/bin/env python3
"""
query-helpers.py — Copy-paste-runnable Prometheus / Thanos / Grafana client.
Saves you 30 lines of boilerplate every time you need to run a PromQL query.

Usage / 用法:

  # 1. From shell — single instant query
  python3 query-helpers.py thanos us 'sum(rate(http_request_cost_time_histogram_count{job="prod-us-iot-service"}[5m]))'

  # 2. Refresh the magnitudes anchor table
  python3 query-helpers.py anchors

  # 3. Discover what jobs exist in a region
  python3 query-helpers.py jobs us       # via Thanos US
  python3 query-helpers.py jobs eu
  python3 query-helpers.py jobs cn       # CN prod only
  # Tech-service domain cutover is pending. Verify the actual API base, then
  # set PROMETHEUS_CN_TECH_SERVICE_URL (VMCluster includes /select/0/prometheus):
  python3 query-helpers.py jobs cn-tech-service
  # After discovering/forwarding the new staging vmsingle Service:
  # set PROMETHEUS_CN_STAGING_URL to its verified API base, then:
  python3 query-helpers.py jobs cn-staging

  # 4. Probe metric names available for a job
  python3 query-helpers.py metrics prod-us-iot-service
  python3 query-helpers.py metrics us-prod-kiss
  # CN staging job names can survive on cn-main; verify their scrape source first.
  python3 query-helpers.py metrics cn-staging-kiss cn
  python3 query-helpers.py metrics staging-cn-my-app cn-staging

  # 5. As a Python module — for scripts that need many queries
  from query_helpers import thanos_q, grafana_q, val
  print(val(thanos_q("us", 'sum(rate(...)[5m]))')))
"""

import os, sys, json, time, urllib.request, urllib.parse

# ============================================================================
# Endpoints (kept in sync with endpoints.md)
# ============================================================================
THANOS = {
    "us": "http://thanos-prod-us.addx.live",
    "eu": "http://thanos-prod-eu.addx.live",
    "cn": "http://thanos-cn.addx.live",  # Legacy CN prod only, not all CN clusters
    "cn-staging": None,  # Discover vmsingle and port-forward; never fall back to prod
    "cn-tech-service": None,  # Pending cutover; require an explicitly verified API base
}

TARGET_ALIASES = {"cn-prod": "cn", "prod-cn": "cn", "staging-cn": "cn-staging"}
TARGET_ENV = {
    "cn": "CN_PROD",
    "cn-staging": "CN_STAGING",
    "cn-tech-service": "CN_TECH_SERVICE",
}

GRAFANA_URL = "https://grafana.addx.live"
GRAFANA_DATASOURCE_UID = {
    "us": "000000016",
    "eu": "000000036",
    "cn": "000000003",
}

# ============================================================================
# Core query primitives
# ============================================================================

def thanos_q(region, expr, instant=True, start=None, end=None, step="60"):
    """POST to Thanos /api/v1/query (or /query_range) for the given region."""
    target = TARGET_ALIASES.get(region.lower(), region.lower())
    if target not in THANOS:
        return {"error": f"unknown target {region}; valid: {list(THANOS.keys())}"}
    env_name = f"PROMETHEUS_{TARGET_ENV[target]}_URL" if target in TARGET_ENV else None
    url = os.environ.get(env_name, "") if env_name else ""
    url = url or THANOS[target]
    if not url:
        return {"error": f"{env_name} must identify the verified {target} API base; no prod fallback"}
    url = url.rstrip("/")
    if instant:
        path, body = "/api/v1/query", urllib.parse.urlencode({"query": expr})
    else:
        path = "/api/v1/query_range"
        body = urllib.parse.urlencode({
            "query": expr, "start": start, "end": end, "step": step,
        })
    req = urllib.request.Request(
        f"{url}{path}", data=body.encode(), method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"error": str(e)}


def grafana_q(region, expr, instant=True, from_ms=None, to_ms=None):
    """POST to Grafana /api/ds/query for the given region."""
    token = os.environ.get("GRAFANA_TOKEN")
    if not token:
        return {"error": "GRAFANA_TOKEN env var not set"}
    target = TARGET_ALIASES.get(region.lower(), region.lower())
    env_name = f"GRAFANA_{TARGET_ENV[target]}_DATASOURCE_UID" if target in TARGET_ENV else None
    uid = (os.environ.get(env_name, "") if env_name else "") or GRAFANA_DATASOURCE_UID.get(target)
    if not uid:
        return {"error": f"No verified datasource for {target}; set {env_name}" if env_name else f"unknown target {region}"}
    now_ms = int(time.time() * 1000)
    if from_ms is None: from_ms = now_ms - 600_000  # default last 10 minutes
    if to_ms   is None: to_ms   = now_ms
    body = json.dumps({
        "queries": [{"refId": "A", "datasource": {"type": "prometheus", "uid": uid},
                     "expr": expr, "instant": instant}],
        "from": str(from_ms), "to": str(to_ms),
    }).encode()
    req = urllib.request.Request(
        f"{GRAFANA_URL}/api/ds/query", data=body, method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"error": str(e)}


def val(r, fmt="{:.2f}"):
    """Extract first scalar value from a Thanos/Grafana response."""
    if isinstance(r, dict) and "error" in r:
        return f"ERR({r['error'][:40]})"
    # Thanos shape
    try:
        result = r["data"]["result"]
        if result:
            return fmt.format(float(result[0]["value"][1]))
    except Exception: pass
    # Grafana shape
    try:
        v = r["results"]["A"]["frames"][0]["data"]["values"][1][0]
        return fmt.format(float(v))
    except Exception: pass
    return "n/a"


# ============================================================================
# CLI entry points
# ============================================================================

def cmd_query(argv):
    """thanos us 'expr' — run a single instant query"""
    if len(argv) < 3:
        print("usage: query-helpers.py thanos <region> '<promql>'"); sys.exit(2)
    region, expr = argv[1], " ".join(argv[2:])
    r = thanos_q(region, expr)
    print(json.dumps(r, indent=2))


def cmd_grafana(argv):
    """grafana us 'expr' — same but via Grafana datasource"""
    if len(argv) < 3:
        print("usage: query-helpers.py grafana <region> '<promql>'"); sys.exit(2)
    region, expr = argv[1], " ".join(argv[2:])
    r = grafana_q(region, expr)
    print(json.dumps(r, indent=2))


def cmd_jobs(argv):
    """jobs us — list all scrape jobs in the region"""
    if len(argv) < 2:
        print("usage: query-helpers.py jobs <region>"); sys.exit(2)
    region = argv[1]
    r = thanos_q(region, 'count by (job)({__name__="up"})')
    if "error" in r: print("ERR:", r["error"]); return
    rows = sorted([(it["metric"].get("job","?"), int(float(it["value"][1])))
                   for it in r.get("data",{}).get("result",[])], key=lambda x: -x[1])
    for j, c in rows:
        print(f"{c:5d}  {j}")


def cmd_metrics(argv):
    """metrics <job> — list all metric names exposed by a job (top 50 by series count)"""
    if len(argv) < 2:
        print("usage: query-helpers.py metrics <job-label> [target]"); sys.exit(2)
    job = argv[1]
    # Explicit target is required when a job name does not identify its cluster.
    region = argv[2] if len(argv) > 2 else None
    if region is None:
        if job.startswith(("cn-staging", "staging-cn")):
            print("ERR: ambiguous CN staging job; pass an explicit target (cn for verified cn-main metrics, cn-staging for the new staging cluster)"); return
        elif job.startswith("cn-tech-service"):
            region = "cn-tech-service"
        elif job.startswith(("prod-cn", "cn-prod")):
            region = "cn"
        elif job.startswith("cn-") or "-cn-" in job:
            print("ERR: ambiguous CN job; pass an explicit target after the job label"); return
        elif job.startswith("prod-eu") or job.startswith("eu-"):
            region = "eu"
        else:
            region = "us"
    r = thanos_q(region, f'count by (__name__)({{job="{job}"}})')
    if "error" in r: print("ERR:", r["error"]); return
    rows = sorted([(it["metric"].get("__name__","?"), int(float(it["value"][1])))
                   for it in r.get("data",{}).get("result",[])], key=lambda x: -x[1])
    print(f"# {len(rows)} unique metric names for job={job} (region={region})")
    for n, c in rows[:80]:
        print(f"{c:8d}  {n}")


def cmd_anchors(argv):
    """anchors — refresh the magnitudes.md anchor table"""
    print("# Magnitude anchors — re-run to refresh")
    print(f"# Captured: {time.strftime('%Y-%m-%d %H:%M %Z')}")
    print()
    # Pod counts
    print("## Pods")
    services = [
        ("iot-service",        "prod-{r}-iot-service"),
        ("iot-consumer",       "prod-{r}-iot-consumer"),
        ("state-machine",      "prod-{r}-statemachine"),
        ("a4x-log-report",     "prod-{r}-a4x-log-report"),
        ("kiss",               "{r}-prod-kiss"),
    ]
    print(f"  {'service':20s}  {'US':>6s}  {'EU':>6s}  {'CN':>6s}")
    for name, tpl in services:
        cells = []
        for r in ("us", "eu", "cn"):
            job = tpl.format(r=r)
            res = thanos_q(r, f'count(up{{job="{job}"}}==1)')
            cells.append(val(res, "{:.0f}"))
        print(f"  {name:20s}  {cells[0]:>6s}  {cells[1]:>6s}  {cells[2]:>6s}")

    print()
    print("## kiss online devices (TCP CurrEstab)")
    for r in ("us", "eu", "cn"):
        res = thanos_q(r, f'sum(node_netstat_Tcp_CurrEstab{{job="{r}-prod-kiss"}})')
        print(f"  {r:>5s}: {val(res, '{:.0f}')}")

    print()
    print("## iot-service-cloud /deviceMsg/setting QPS")
    for r in ("us", "eu", "cn"):
        res = thanos_q(r, f'sum(rate(http_request_cost_time_histogram_count{{job="prod-{r}-iot-service",uri="/deviceMsg/setting"}}[5m]))')
        print(f"  {r:>5s}: {val(res, '{:.1f}')} req/s")

    print()
    print("## iot-service-cloud total SQL QPS")
    for r in ("us", "eu", "cn"):
        res = thanos_q(r, f'sum(rate(server_sql_duration_milliseconds_count{{job="prod-{r}-iot-service"}}[5m]))')
        print(f"  {r:>5s}: {val(res, '{:.0f}')} ops/s")

    print()
    print("## state-machine device_state_change rate")
    for r in ("us", "eu", "cn"):
        res = thanos_q(r, f'sum(rate(device_state_change_counter{{job="prod-{r}-statemachine"}}[5m]))')
        print(f"  {r:>5s}: {val(res, '{:.1f}')} /s")


# ============================================================================
# Entry
# ============================================================================
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(0)
    cmd = sys.argv[1]
    rest = sys.argv[1:]  # keep the cmd in argv[0] for help printouts
    handlers = {
        "thanos":  cmd_query,
        "grafana": cmd_grafana,
        "jobs":    cmd_jobs,
        "metrics": cmd_metrics,
        "anchors": cmd_anchors,
    }
    fn = handlers.get(cmd)
    if not fn:
        print(__doc__); sys.exit(2)
    fn(rest)
