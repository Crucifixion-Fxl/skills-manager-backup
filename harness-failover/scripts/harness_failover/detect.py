"""Per-harness health from local history. Pure functions over lines/events so they are unit-testable;
file readers live in cli.py."""
from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass, field

from .signatures import ANY, classify, parse_resets  # noqa: F401  (parse_resets re-exported for callers)

UTC = dt.timezone.utc
STALE_ERR = dt.timedelta(minutes=30)  # quota error without a reset time counts this long
GROK_402_WINDOW = dt.timedelta(hours=6)
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
TS_RE = re.compile(r'"timestamp"\s*:\s*"([^"]+)"')
ASSISTANT_RE = re.compile(r'"type"\s*:\s*"assistant"')
LOG_TS_RE = re.compile(r"^(\d{4}-\d\d-\d\dT[\d:.]+Z)")
# Status codes must stand alone: digits inside a timestamp (".741402Z") are not HTTP 402.
SUSPICIOUS_RE = re.compile(
    r"(?<![\d.])(?:402|429|529)(?![\d.])|status[=: ]+[45]\d\d|limit|quota|exhaust|billing|balance|credit|overloaded", re.I)


@dataclass
class Health:
    status: str  # ok | exhausted | unknown | unavailable
    reason: str
    until: object = None
    evidence: dict = field(default_factory=dict)


@dataclass
class LogScan:
    matches: list  # (ts|None, signature id, kind)
    unclassified: list  # suspicious error lines no signature explains


def strip_ansi(s: str) -> str:
    return ANSI_RE.sub("", s)


def _ts(s: str | None):
    try:
        d = dt.datetime.fromisoformat((s or "").replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=UTC)
    except Exception:
        return None


# ───────────────────────────── grok ─────────────────────────────
def _num(cfg: dict, key: str) -> float:
    return float((cfg.get(key) or {}).get("val", 0) or 0)


def grok_health(lines, now) -> Health:
    """billing snapshot + structured 402 failures. A 402 newer than the last snapshot wins: billing is only
    refreshed while a grok process runs, an explicit 402 is the harness itself saying no."""
    billing = None  # (ts, config, tier)
    last402 = None
    for line in lines:
        if "billing: fetched credits config" in line:
            try:
                o = json.loads(line)
                billing = (_ts(o["ts"]), o["ctx"]["config"], o["ctx"].get("subscriptionTier"))
            except (ValueError, KeyError, TypeError):
                continue
        elif "inference_failed" in line:
            try:
                o = json.loads(line)
            except ValueError:
                continue
            ctx = o.get("ctx") if isinstance(o.get("ctx"), dict) else {}
            msg = str(ctx.get("message", "")).lower()
            if ctx.get("status_code") == 402 or "balance exhausted" in msg or "402 payment required" in msg:
                last402 = _ts(o.get("ts"))
    fresh_402 = last402 is not None and now - last402 < GROK_402_WINDOW
    if fresh_402 and (billing is None or (billing[0] is not None and last402 > billing[0])):
        end = _ts(billing[1].get("billingPeriodEnd")) if billing else None
        until = end if (end and end > now) else None
        return Health("exhausted", "402 usage balance exhausted 晚于最近一次 billing 快照" if billing else
                      "近 6 小时出现 402 usage balance exhausted", until, {"last_402": last402.isoformat()})
    if billing:
        seen, cfg, tier = billing
        try:
            pct = float(cfg.get("creditUsagePercent") or 0)
            headroom = (_num(cfg, "onDemandCap") - _num(cfg, "onDemandUsed")) + _num(cfg, "prepaidBalance")
        except (ValueError, TypeError):
            return Health("unknown", "billing 记录格式异常")
        end = _ts(cfg.get("billingPeriodEnd"))
        ev = dict(credit_usage_percent=pct, tier=tier, headroom=headroom, period_end=end.isoformat() if end else None)
        if pct >= 100 and headroom <= 0 and end and end > now:
            return Health("exhausted", f"credits {pct:.0f}% 且无按需/预充余额（402 usage balance exhausted）", end, ev)
        return Health("ok", f"credits {pct:.0f}%", None, ev)
    return Health("unknown", "没有可用的 billing / 402 记录")


# ─────────────────────── claude family (claude, glm) ───────────────────────
def quota_events(lines, sigs, cutoff, provider=ANY):
    """会话 jsonl → [(ts, 'quota'|'ok', until|None, code)]，按时间排序。transient/auth 不算额度事件。"""
    events = []
    for line in lines:
        if "api_error" in line or "isApiErrorMessage" in line:
            try:
                o = json.loads(line)
            except Exception:
                continue
            ts = _ts(o.get("timestamp"))
            if not ts or ts < cutoff:
                continue
            if o.get("type") == "assistant" and not o.get("isApiErrorMessage"):
                # a working reply that merely *mentions* api_error must still count as working
                if ts and ts >= cutoff:
                    events.append((ts, "ok", None, ""))
                continue
            if o.get("subtype") == "api_error":
                text = json.dumps(o.get("error") or {}, ensure_ascii=False)
            elif o.get("isApiErrorMessage"):
                text = json.dumps(o.get("message") or {}, ensure_ascii=False)
            else:
                continue
            m = classify(text, sigs, harness="claude", after=ts, provider=provider)
            if m and m.kind == "quota":
                events.append((ts, "quota", m.until, m.id))
        elif ASSISTANT_RE.search(line):
            m = TS_RE.search(line)
            ts = _ts(m.group(1)) if m else None
            if ts and ts >= cutoff:
                events.append((ts, "ok", None, ""))
    events.sort(key=lambda e: e[0])
    return events


def family_health(events, now) -> Health:
    if not events:
        return Health("unknown", "没有近期会话记录")
    events = sorted(events, key=lambda e: e[0])
    ts, kind, until, code = events[-1]
    last_ok = max((e[0] for e in events if e[1] == "ok"), default=None)
    last_q = max((e[0] for e in events if e[1] == "quota"), default=None)
    ev = dict(last_ok=last_ok.isoformat() if last_ok else None, last_quota_error=last_q.isoformat() if last_q else None,
              code=code or None)
    if kind == "ok":
        return Health("ok", "最近一次事件是成功回复", None, ev)
    if until and until > now:
        return Health("exhausted", f"额度错误 {code}，恢复时刻已知", until, ev)
    if until is None and now - ts < STALE_ERR:
        return Health("exhausted", f"额度错误 {code}，{int((now - ts).total_seconds() // 60)} 分钟前，无恢复时刻", None, ev)
    return Health("unknown", "最近的额度错误已过期，之后没有成功回复", None, ev)


# ───────────────────────────── codex ─────────────────────────────
def codex_health(logged_in, now) -> Health:
    if logged_in is False:
        return Health("unavailable", "codex 未登录（需要人工 `CODEX_HOME=<home> codex login`）")
    if logged_in is None:
        return Health("unknown", "无法判断 codex 登录状态")
    return Health("unknown", "已登录，但没有可分析的额度历史")


# ───────────────────────── agent logs / learning ─────────────────────────
def scan_agent_log(lines, sigs) -> LogScan:
    """已知错误 → matches；看起来像额度/限流、但没有任何签名解释的行 → unclassified（喂给 learn）。"""
    matches, unknown = [], []
    for raw in lines:
        line = strip_ansi(raw).rstrip("\n")
        if "error" not in line.lower() and "ERROR" not in line and "WARN" not in line:
            continue
        m = classify(line, sigs)
        ts = LOG_TS_RE.match(line)
        if m:
            matches.append((_ts(ts.group(1)) if ts else None, m.id, m.kind))
        elif (SUSPICIOUS_RE.search(LOG_TS_RE.sub("", line, count=1)) and "-32603): Internal error" not in line
              and "requeueing failed batch" not in line):
            unknown.append(line)
    return LogScan(matches, unknown)
