"""S5: unknown error messages are redacted, de-duplicated and turned into a ready-to-review proposal.

These samples end up in a merge request, so redaction must never leak credentials or identifiers."""
import datetime as dt
import json
import os
import stat

import pytest

from harness_failover import learn as L
from harness_failover import signatures as S

NOW = dt.datetime(2026, 9, 19, 5, 0, tzinfo=dt.timezone.utc)
UUID = "11d76795-1dd1-4d53-af1c-83576ea992cf"
HEX64 = "ab" * 32


@pytest.mark.parametrize("secret", [
    UUID, HEX64, "nsec" + "1" + "q" * 60,
    "npub" + "1" + "abcdefghijklmnopqrstuvwxyz0123456789abcdefghijklmnopqrs",
    "glpat" + "-" + "AbCdEfGhIjKlMnOpQrSt", "sk" + "-ant-" + "api03-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789",
    "someone@example.com", "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.abcDEF123",
    "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789AbCd",
])
def test_redact_removes_credentials_and_identifiers(secret):
    out = L.redact(f"error_message=quota window closed for {secret} please retry")
    assert secret not in out
    assert "quota window closed" in out and "please retry" in out


def test_redact_keeps_ordinary_error_text():
    t = "responses API error status=529 error_message=Daily quota window closed for this account"
    assert L.redact(t) == t


def test_redact_hides_key_value_secrets():
    assert "hunter2" not in L.redact("request failed token=hunter2 password: hunter2 api_key=hunter2")


def test_normalize_collapses_ids_and_numbers():
    a = L.normalize(f"2026-09-19T05:10:00Z ERROR status=529 quota closed {UUID} after 12 tries")
    b = L.normalize(f"2026-09-20T07:22:11Z ERROR status=529 quota closed {'22222222-2222-2222-2222-222222222222'} after 3 tries")
    assert a == b


def test_record_unknown_dedupes_and_counts(tmp_path):
    p = str(tmp_path / "unknown.jsonl")
    new = L.record_unknown([f"ERROR quota closed {UUID} after 12 tries", "ERROR quota closed 22222222-2222-2222-2222-222222222222 after 3 tries",
                            "ERROR something else about billing balance"], p, NOW)
    assert new == 2
    entries = L.load_unknown(p)
    assert sorted(e["count"] for e in entries) == [1, 2]
    assert all(UUID not in json.dumps(e) for e in entries)
    assert L.record_unknown([f"ERROR quota closed {UUID} after 9 tries"], p, NOW) == 0  # already known
    assert sorted(e["count"] for e in L.load_unknown(p)) == [1, 3]


def test_unknown_file_is_0600(tmp_path):
    p = str(tmp_path / "u.jsonl")
    L.record_unknown(["ERROR quota gone"], p, NOW)
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600


def test_load_unknown_of_missing_file_is_empty(tmp_path):
    assert L.load_unknown(str(tmp_path / "nope")) == []


def test_prune_known_drops_entries_a_signature_now_explains(tmp_path):
    p = str(tmp_path / "u.jsonl")
    L.record_unknown(["ERROR responses API error status=402 Payment Required usage balance exhausted",
                      "ERROR Daily quota window closed for this account"], p, NOW)
    removed = L.prune_known(p, S.load_signatures())
    assert removed == 1
    (left,) = L.load_unknown(p)
    assert "Daily quota window closed" in left["sample"]


def test_propose_outputs_a_reviewable_stub_with_the_redacted_sample():
    entries = [{"key": "k1", "count": 3, "first_seen": "2026-09-19T05:00:00Z", "last_seen": "2026-09-19T06:00:00Z",
                "sample": "ERROR responses API error status=529 error_message=Daily quota window closed for this account"}]
    out = L.propose(entries)
    assert "Daily quota window closed for this account" in out
    assert '"kind": "quota|transient|auth"' in out or '"kind":' in out
    assert "tests/fixtures/samples.json" in out and "assets/signatures.json" in out
    assert "<unknown-1>" in out  # placeholder id the human/agent must choose


def test_propose_with_no_entries_says_so():
    assert "no unclassified" in L.propose([]).lower()


@pytest.mark.parametrize("secret,line", [
    ("hunter2", '"password": "hunter2"'),
    ("abcd1234", '{"api_key":"abcd1234"}'),
    ("abc123def", "access_token=abc123def"),
    ("zzz999", "client_secret=zzz999"),
    ("dXNlcjpwYXNz", "Authorization: Basic dXNlcjpwYXNz"),
    ("user:pass", "GET https://user:pass@host/x failed"),
    ("session=abc123", "Cookie: session=abc123"),
    ("AKIA" + "IOSFODNN7EXAMPLE", "aws key " + "AKIA" + "IOSFODNN7EXAMPLE" + " rejected"),
    ("ghs" + "_abcdefghij1234", "token " + "ghs" + "_abcdefghij1234" + " expired"),
    ("sk" + "_live_abcdefgh1234", "stripe " + "sk" + "_live_abcdefgh1234" + " declined"),
])
def test_redact_covers_json_quoting_prefixed_names_basic_auth_url_credentials_and_cloud_keys(secret, line):
    """Review P3: each of these leaked through the first version of redact()."""
    assert secret not in L.redact(line)


def test_load_unknown_skips_corrupt_lines_instead_of_crashing(tmp_path):
    p = tmp_path / "u.jsonl"
    p.write_text('{"key": "a", "count": 1, "first_seen": "t", "last_seen": "t", "sample": "s"}\nnot json\n\n{broken\n')
    assert [e["key"] for e in L.load_unknown(str(p))] == ["a"]
