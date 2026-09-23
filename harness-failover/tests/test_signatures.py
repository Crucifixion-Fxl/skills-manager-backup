"""S2a: data-driven error signatures + reset-time parsing."""
import datetime as dt
import json
import os
import re

import pytest

from harness_failover import signatures as S

UTC = dt.timezone.utc
HERE = os.path.dirname(os.path.abspath(__file__))
SIGS = None


@pytest.fixture(scope="module")
def sigs():
    return S.load_signatures()


def iso(d):
    return d.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ") if d else None


def sample(sig_id):
    for s in json.load(open(os.path.join(HERE, "fixtures", "samples.json")))["samples"]:
        if s["sig"] == sig_id:
            return s["text"]
    raise KeyError(sig_id)


AFTER = dt.datetime(2026, 9, 18, 23, 6, 25, tzinfo=UTC)


def test_grok_402_is_quota(sigs):
    m = S.classify(sample("grok-402-balance"), sigs)
    assert (m.id, m.kind, m.until) == ("grok-402-balance", "quota", None)


def test_glm_1308_is_quota_with_plus8_reset(sigs):
    m = S.classify(sample("glm-1308"), sigs)
    assert (m.id, m.kind) == ("glm-1308", "quota")
    assert iso(m.until) == "2026-09-18T10:36:35Z"  # 18:36:35 +08


def test_glm_1310_reset_is_parsed(sigs):
    m = S.classify(sample("glm-1310"), sigs)
    assert (m.id, m.kind) == ("glm-1310", "quota")
    assert iso(m.until) == "2026-09-24T01:00:00Z"


def test_glm_1302_is_transient_not_quota(sigs):
    m = S.classify(sample("glm-1302"), sigs)
    assert (m.id, m.kind) == ("glm-1302", "transient")


def test_claude_weekly_limit_resolves_next_reset_after_event(sigs):
    m = S.classify(sample("claude-usage-limit"), sigs, after=AFTER)
    assert (m.id, m.kind) == ("claude-usage-limit", "quota")
    assert iso(m.until) == "2026-09-19T00:00:00Z"


def test_auth_failure_is_its_own_kind(sigs):
    assert S.classify(sample("claude-auth"), sigs).kind == "auth"


def test_unrelated_text_is_not_classified(sigs):
    assert S.classify("all good, tests passed", sigs) is None
    assert S.classify("Agent reported error (code -32603): Internal error", sigs) is None


def test_harness_filter_scopes_signatures(sigs):
    assert S.classify(sample("glm-1308"), sigs, harness="grok") is None
    assert S.classify(sample("glm-1308"), sigs, harness="claude").id == "glm-1308"


@pytest.mark.parametrize("text,want", [
    ("You've hit your weekly limit · resets 12am (UTC)", "2026-09-19T00:00:00Z"),
    ("hit your limit · resets 3:30pm (Asia/Singapore)", "2026-09-19T07:30:00Z"),
    ("hit your weekly limit · resets Sep 25, 12am (UTC)", "2026-09-25T00:00:00Z"),
    ("no reset info", None),
    ("resets 5pm (Not/AZone)", None),
])
def test_parse_resets(text, want):
    assert iso(S.parse_resets(text, AFTER)) == want


def test_parse_cn_reset_is_plus8():
    assert iso(S.parse_cn_reset("2026-09-18 18:36:35 后可继续使用")) == "2026-09-18T10:36:35Z"
    assert S.parse_cn_reset("nothing") is None


def test_signature_file_is_well_formed(sigs):
    ids = [s.id for s in sigs]
    assert len(ids) == len(set(ids))
    for s in sigs:
        assert s.kind in ("quota", "transient", "auth")
        re.compile(s.pattern)


def test_every_signature_has_a_matching_sample_fixture(sigs):
    """Update protocol: a new signature is only accepted together with a real sample."""
    samples = json.load(open(os.path.join(HERE, "fixtures", "samples.json")))["samples"]
    for s in sigs:
        mine = [x["text"] for x in samples if x["sig"] == s.id]
        assert mine, f"signature {s.id} has no sample in fixtures/samples.json"
        assert all(S.classify(t, sigs).id == s.id for t in mine), f"{s.id}: sample classified as something else"


def test_classify_can_scope_provider_specific_signatures(sigs):
    text = sample("glm-1308")
    assert S.classify(text, sigs, harness="claude", provider=None) is None  # a plain Claude account is not GLM
    assert S.classify(text, sigs, harness="claude", provider="glm").id == "glm-1308"
    assert S.classify(text, sigs, harness="claude").id == "glm-1308"  # default: no provider scoping
