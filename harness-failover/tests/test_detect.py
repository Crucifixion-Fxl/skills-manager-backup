"""S2b: per-harness health from local history (pure functions over lines / events)."""
import datetime as dt
import json

import pytest

from harness_failover import detect as D
from harness_failover import signatures as S

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 9, 19, 5, 0, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def sigs():
    return S.load_signatures()


def iso(d):
    return d.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ") if d else None


# ───────── grok ─────────
def billing(pct, end="2026-09-25T01:03:18.434240+00:00", cap=0, used=0, prepaid=0, ts="2026-09-19T04:56:35.000Z"):
    return json.dumps({"ts": ts, "lvl": "info", "msg": "billing: fetched credits config", "ctx": {
        "config": {"creditUsagePercent": pct, "onDemandCap": {"val": cap}, "onDemandUsed": {"val": used},
                   "prepaidBalance": {"val": prepaid}, "billingPeriodEnd": end}, "subscriptionTier": "SuperGrok Plus"}})


def fail402(ts):
    return json.dumps({"ts": ts, "lvl": "error", "msg": "shell.turn.inference_failed", "ctx": {
        "status_code": 402, "message": "API error (status 402 Payment Required): Grok Build usage balance exhausted"}})


def test_grok_full_credits_without_headroom_is_exhausted_until_period_end():
    h = D.grok_health([billing(100.0)], NOW)
    assert h.status == "exhausted" and iso(h.until) == "2026-09-25T01:03:18Z"
    assert h.evidence["credit_usage_percent"] == 100.0


def test_grok_partial_usage_is_ok():
    assert D.grok_health([billing(40.0)], NOW).status == "ok"


def test_grok_full_but_with_on_demand_headroom_is_ok():
    assert D.grok_health([billing(100.0, cap=50, used=10)], NOW).status == "ok"
    assert D.grok_health([billing(100.0, prepaid=5)], NOW).status == "ok"


def test_grok_period_already_ended_is_not_exhausted():
    assert D.grok_health([billing(100.0, end="2026-09-18T00:00:00+00:00")], NOW).status == "ok"


def test_grok_latest_billing_line_wins():
    assert D.grok_health([billing(100.0, ts="2026-09-19T01:00:00Z"), billing(10.0, ts="2026-09-19T04:00:00Z")], NOW).status == "ok"


def test_grok_recent_402_without_billing_is_exhausted_without_until():
    h = D.grok_health([fail402("2026-09-19T03:00:00.000Z")], NOW)
    assert h.status == "exhausted" and h.until is None


def test_grok_old_402_without_billing_is_unknown():
    assert D.grok_health([fail402("2026-09-18T01:00:00.000Z")], NOW).status == "unknown"


def test_grok_no_data_is_unknown():
    assert D.grok_health([], NOW).status == "unknown"
    assert D.grok_health(["not json", "{}"], NOW).status == "unknown"


# ───────── claude family (claude / glm) ─────────
def api_error(ts, text, status=429):
    return json.dumps({"type": "system", "subtype": "api_error", "timestamp": ts, "error": {"status": status, "message": text}})


def assistant(ts, text, err=False):
    o = {"type": "assistant", "timestamp": ts, "message": {"content": [{"type": "text", "text": text}]}}
    if err:
        o["isApiErrorMessage"] = True
    return json.dumps(o)


GLM_1308 = "Request rejected (429) · [1308][已达到 5 小时使用上限，2026-09-18 18:36:35 后可继续使用。]"
CUTOFF = NOW - dt.timedelta(hours=48)


def test_quota_events_extracts_kinds_and_resets(sigs):
    lines = [
        api_error("2026-09-18T08:00:00.000Z", GLM_1308),
        assistant("2026-09-18T23:06:25.922Z", "You've hit your weekly limit · resets 12am (UTC)", err=True),
        assistant("2026-09-19T00:01:45.250Z", "hello"),
        api_error("2026-09-19T01:00:00.000Z", "[1302][请求过于频繁]"),  # transient: not a quota event
    ]
    ev = D.quota_events(lines, sigs, CUTOFF)
    kinds = [(iso(e[0]), e[1], iso(e[2])) for e in ev]
    assert kinds == [
        ("2026-09-18T08:00:00Z", "quota", "2026-09-18T10:36:35Z"),
        ("2026-09-18T23:06:25Z", "quota", "2026-09-19T00:00:00Z"),
        ("2026-09-19T00:01:45Z", "ok", None),
    ]


def test_quota_events_ignore_lines_older_than_cutoff(sigs):
    assert D.quota_events([assistant("2026-09-01T00:00:00.000Z", "hi")], sigs, CUTOFF) == []


def test_quota_events_ignore_garbage(sigs):
    assert D.quota_events(["", "{not json", '{"type":"user"}'], sigs, CUTOFF) == []


def ev(ts, kind, until=None):
    return (dt.datetime.fromisoformat(ts.replace("Z", "+00:00")), kind, until and dt.datetime.fromisoformat(until.replace("Z", "+00:00")), "")


def test_family_ok_when_latest_event_is_success():
    h = D.family_health([ev("2026-09-18T23:06:25Z", "quota"), ev("2026-09-19T00:01:45Z", "ok")], NOW)
    assert h.status == "ok"


def test_family_exhausted_when_latest_is_quota_with_future_reset():
    h = D.family_health([ev("2026-09-19T04:50:00Z", "quota", "2026-09-19T09:00:00Z")], NOW)
    assert h.status == "exhausted" and iso(h.until) == "2026-09-19T09:00:00Z"


def test_family_exhausted_when_recent_quota_without_reset():
    assert D.family_health([ev("2026-09-19T04:45:00Z", "quota")], NOW).status == "exhausted"


def test_family_unknown_when_quota_is_stale_and_no_reset():
    assert D.family_health([ev("2026-09-19T03:00:00Z", "quota")], NOW).status == "unknown"


def test_family_unknown_when_reset_passed_and_nothing_since():
    assert D.family_health([ev("2026-09-18T23:06:25Z", "quota", "2026-09-19T00:00:00Z")], NOW).status == "unknown"


def test_family_unknown_without_events():
    assert D.family_health([], NOW).status == "unknown"


# ───────── codex ─────────
def test_codex_not_logged_in_is_unavailable():
    h = D.codex_health(False, NOW)
    assert h.status == "unavailable" and "login" in h.reason.lower()


def test_codex_logged_in_without_history_is_unknown():
    assert D.codex_health(True, NOW).status == "unknown"


# ───────── agent logs (also the source of unknown errors) ─────────
ANSI_402 = ("\x1b[2m2026-09-19T04:24:34.395337Z\x1b[0m \x1b[31mERROR\x1b[0m responses API error status=402 Payment Required "
            "error_message=Grok Build usage balance exhausted")
GENERIC = "2026-09-19T04:44:36.785653Z  WARN buzz_acp: agent_returned (application error — pipe intact) outcome=\"error\" error=Agent reported error (code -32603): Internal error"
NEW_ERR = "2026-09-19T05:10:00.000000Z ERROR responses API error status=529 error_message=Daily quota window closed for this account"


def test_strip_ansi():
    assert D.strip_ansi("\x1b[31mERROR\x1b[0m x") == "ERROR x"


def test_scan_agent_log_classifies_known_errors_through_ansi(sigs):
    scan = D.scan_agent_log([ANSI_402], sigs)
    assert [(m[1], m[2]) for m in scan.matches] == [("grok-402-balance", "quota")]
    assert scan.unclassified == []


def test_scan_agent_log_ignores_generic_internal_error(sigs):
    scan = D.scan_agent_log([GENERIC], sigs)
    assert scan.matches == [] and scan.unclassified == []


def test_scan_agent_log_collects_suspicious_unknown_errors(sigs):
    scan = D.scan_agent_log([NEW_ERR, ANSI_402, "INFO all fine"], sigs)
    assert len(scan.unclassified) == 1 and "Daily quota window closed" in scan.unclassified[0]


def test_word_assistant_inside_a_user_line_is_not_a_success_event(sigs):
    """Regression: only records whose *type* is assistant count as a working harness.

    The tool-result payload carries an unescaped "assistant" token, which the old substring check took for a reply.
    """
    user_line = json.dumps({"type": "user", "timestamp": "2026-09-19T04:00:00.000Z",
                            "toolUseResult": {"role": "assistant"}})
    assert '"assistant"' in user_line  # the input really contains the token that fooled the old check
    assert D.quota_events([user_line], sigs, CUTOFF) == []


# Regression (found on the real machine): digits inside timestamps looked like HTTP status codes.
NOISE = [
    "2026-09-14T01:39:44.741402Z  WARN buzz_acp::relay: relay reconnect failed: WebSocket error: HTTP error: 503 Service Unavailable",
    "2026-09-19T04:07:55.742971Z ERROR Failed to spawn MCP server 'railway': No such file or directory (os error 2)",
]


@pytest.mark.parametrize("line", NOISE)
def test_timestamp_digits_are_not_mistaken_for_status_codes(sigs, line):
    assert D.scan_agent_log([line], sigs).unclassified == []


def test_a_real_status_code_is_still_flagged(sigs):
    line = "2026-09-19T05:10:00.000000Z ERROR responses API error status=429 error_message=Slow down please"
    assert len(D.scan_agent_log([line], sigs).unclassified) == 1


def test_codex_login_state_that_cannot_be_determined_is_not_reported_as_logged_in():
    h = D.codex_health(None, NOW)
    assert h.status == "unknown" and "已登录" not in h.reason


# ───────── reliability review fixes ─────────
def test_grok_402_is_read_from_the_status_code_not_from_digits_in_a_timestamp():
    line = json.dumps({"ts": "2026-09-19T04:05:05.402113Z", "lvl": "error", "msg": "shell.turn.inference_failed",
                       "ctx": {"status_code": 429, "message": "slow down"}})
    assert D.grok_health([line], NOW).status == "unknown"


def test_grok_402_by_message_still_counts_without_a_status_code():
    line = json.dumps({"ts": "2026-09-19T04:55:00.000Z", "msg": "shell.turn.inference_failed",
                       "ctx": {"message": "Grok Build usage balance exhausted"}})
    assert D.grok_health([line], NOW).status == "exhausted"


def test_grok_malformed_billing_does_not_crash_the_run():
    bad = json.dumps({"ts": "2026-09-19T04:56:35.000Z", "msg": "billing: fetched credits config",
                      "ctx": {"config": {"creditUsagePercent": "n/a"}}})
    assert D.grok_health([bad], NOW).status == "unknown"


def test_a_402_newer_than_the_last_billing_snapshot_wins():
    assert D.grok_health([billing(80.0, ts="2026-09-19T04:00:00.000Z"), fail402("2026-09-19T04:55:00.000Z")], NOW).status == "exhausted"
    assert D.grok_health([fail402("2026-09-19T03:00:00.000Z"), billing(80.0, ts="2026-09-19T04:00:00.000Z")], NOW).status == "ok"


def test_a_success_reply_that_merely_mentions_api_error_still_counts_as_working(sigs):
    lines = [api_error("2026-09-19T04:00:00.000Z", GLM_1308),
             json.dumps({"type": "assistant", "timestamp": "2026-09-19T04:59:00.000Z",
                         "message": {"content": [{"type": "text", "text": "I am fixing the api_error handling"}]}})]
    ev = D.quota_events(lines, sigs, CUTOFF)
    assert [e[1] for e in ev] == ["quota", "ok"]


def test_glm_signatures_only_apply_to_the_glm_profile(sigs):
    """`.claude-buzz` once ran on a GLM key; a stale GLM error there must not make claude-buzz look exhausted."""
    lines = [api_error("2026-09-19T04:30:00.000Z", GLM_1308.replace("2026-09-18 18:36:35", "2026-09-24 09:00:00"))]
    assert D.quota_events(lines, sigs, CUTOFF, provider=None) == []
    assert len(D.quota_events(lines, sigs, CUTOFF, provider="glm")) == 1
