from __future__ import annotations

import json
import os
import stat
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from collect_claude_usage import (  # noqa: E402
    collect_claude_usage,
    collect_raw_transcripts,
    discover_ccusage,
)

SECRET_MARKER = "PROMPT-CONTENT-MUST-NOT-LEAK"


def _entry(ts, model="glm-5.3", req="r1", msg="m1", inp=10, out=2,
           cache_read=5, cache_create=0):
    return {
        "timestamp": ts,
        "requestId": req,
        "message": {
            "id": msg,
            "model": model,
            "usage": {
                "input_tokens": inp,
                "output_tokens": out,
                "cache_read_input_tokens": cache_read,
                "cache_creation_input_tokens": cache_create,
            },
        },
    }


def _write_session(projects_dir: Path, session: str, *entries: dict) -> None:
    target = projects_dir / f"-work-{session}" / f"{session}.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(json.dumps(entry) + "\n")
            fh.write(json.dumps({"timestamp": entry["timestamp"],
                                 "message": {"text": SECRET_MARKER}}) + "\n")


def _collect(tmp_path, since="2026-09-07", until="2026-09-13", **kwargs):
    return collect_claude_usage(tmp_path / "projects", since, until,
                                allow_ccusage=False, **kwargs)


def test_raw_transcripts_count_per_entry_with_ccusage_parity(tmp_path):
    projects = tmp_path / "projects"
    _write_session(
        projects, "sess-a",
        _entry("2026-09-06T23:59:00+08:00", req="r0", msg="m0"),   # before week
        _entry("2026-09-07T00:00:30+08:00", req="r1", msg="m1"),
        _entry("2026-09-07T01:00:00+08:00", req="r1", msg="m1"),   # streaming snapshot
        _entry("2026-09-13T23:59:59+08:00", req="r2", msg="m2", model="glm-5.3-flash"),
        _entry("2026-09-14T00:00:01+08:00", req="r3", msg="m3"),   # after week
    )
    result = _collect(tmp_path)
    usage = result["usage"]
    assert result["source"] == "raw_transcripts"
    assert result["available"] is True
    assert usage["sessions"] == 1
    assert usage["input_tokens"] == 30          # every in-week entry counts
    assert usage["output_tokens"] == 6
    assert usage["cache_read_tokens"] == 15
    assert [d["date"] for d in usage["daily"]] == ["2026-09-07", "2026-09-13"]
    assert usage["daily"][0]["input_tokens"] == 20
    assert {m["model"] for m in usage["model_breakdown"]} == {"glm-5.3", "glm-5.3-flash"}


def test_raw_transcripts_count_entries_without_message_ids(tmp_path):
    projects = tmp_path / "projects"
    entry = _entry("2026-09-08T10:00:00+08:00")
    del entry["message"]["id"]
    del entry["requestId"]
    _write_session(projects, "sess-a", entry, entry)
    result = _collect(tmp_path)
    assert result["usage"]["input_tokens"] == 20


def _touch_in_week(projects: Path, when="2026-09-08T12:00:00+08:00") -> None:
    ts = datetime.fromisoformat(when).timestamp()
    for path in projects.glob("**/*.jsonl"):
        os.utime(path, (ts, ts))


def test_evidence_guard_flags_week_activity_even_without_usage(tmp_path):
    projects = tmp_path / "projects"
    _write_session(projects, "sess-a",
                   _entry("2026-09-08T10:00:00+08:00", inp=0, out=0, cache_read=0))
    _touch_in_week(projects)
    result = _collect(tmp_path)
    assert result["evidence"]["week_activity_observed"] is True
    assert result["usage"]["input_tokens"] == 0


def test_evidence_guard_bounded_to_week_for_historical_reruns(tmp_path):
    projects = tmp_path / "projects"
    _write_session(projects, "sess-a",
                   _entry("2026-01-06T10:00:00+08:00", req="h0", msg="h0",
                          inp=0, out=0, cache_read=0))
    result = collect_claude_usage(projects, "2026-01-05", "2026-01-11",
                                  allow_ccusage=False)
    # Old transcripts touched after that week must not fake in-week evidence.
    assert result["evidence"]["week_activity_observed"] is False
    assert result["usage"]["input_tokens"] == 0


def test_output_never_contains_message_text_or_home_paths(tmp_path):
    projects = tmp_path / "projects"
    _write_session(projects, "sess-a",
                   _entry("2026-09-08T10:00:00+08:00"))
    rendered = json.dumps(_collect(tmp_path), ensure_ascii=False)
    assert SECRET_MARKER not in rendered
    assert str(Path.home()) not in rendered
    assert "/Users/" not in rendered


def test_discovery_falls_back_to_nvm_glob(tmp_path):
    nvm_bin = tmp_path / "nvm" / "versions" / "node" / "v25.7.0" / "bin"
    nvm_bin.mkdir(parents=True)
    ccusage = nvm_bin / "ccusage"
    ccusage.write_text("#!/bin/sh\nexit 0\n")
    ccusage.chmod(ccusage.stat().st_mode | stat.S_IEXEC)
    home = tmp_path / "home"
    home.mkdir()
    (home / ".nvm").symlink_to(tmp_path / "nvm", target_is_directory=True)

    import os
    from unittest.mock import patch
    # PATH is cleared so the glob branch is exercised even on machines that
    # have ccusage on PATH.
    with patch.dict(os.environ, {"HOME": str(home), "PATH": ""}), \
            patch("collect_claude_usage.CCUSAGE_EXTRA_GLOBS",
                  ("~/.nvm/versions/node/*/bin/ccusage",)):
        assert discover_ccusage() is not None
    # Explicit-but-missing binaries must not be returned as usable.
    assert discover_ccusage(str(tmp_path / "missing" / "ccusage")) is None


def test_ccusage_path_propagates_range_flags_and_parses(tmp_path):
    argv_log = tmp_path / "argv.json"
    stub = tmp_path / "ccusage-stub"
    stub.write_text(
        "#!/bin/sh\n"
        'printf \'%s\\n\' "$*" >> "' + str(argv_log) + '"\n'
        'if [ "$1" = "daily" ]; then\n'
        '  cat <<JSON\n'
        '{"totals":{"inputTokens":7,"outputTokens":3,"cacheReadTokens":11,'
        '"cacheCreationTokens":0},"daily":[{"date":"2026-09-07","inputTokens":7,'
        '"outputTokens":3,"cacheReadTokens":11,"cacheCreationTokens":0,'
        '"modelBreakdowns":[{"modelName":"glm-5.3","inputTokens":7,'
        '"outputTokens":3,"cacheReadTokens":11}]}]}\n'
        "JSON\n"
        "else\n"
        '  echo \'{"sessions":[{"sessionId":"s1"}]}\'\n'
        "fi\n",
        encoding="utf-8",
    )
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)

    result = collect_claude_usage(tmp_path / "projects", "2026-09-07", "2026-09-13",
                                  ccusage_bin=str(stub))
    assert result["source"] == "ccusage"
    assert result["usage"]["sessions"] == 1
    assert result["usage"]["input_tokens"] == 7
    assert result["usage"]["model_breakdown"][0]["model"] == "glm-5.3"

    calls = argv_log.read_text().splitlines()
    # Each call line is the argument string after the subcommand.
    for call in calls:
        assert "--since 20260907" in call
        assert "--until 20260913" in call
        assert "--timezone Asia/Singapore" in call
        assert "--offline" in call
    assert len(calls) == 2


def test_ccusage_empty_array_week_falls_back_to_raw(tmp_path):
    # ccusage 18.x prints a bare `[]` (exit 0) for weeks with no data; that
    # must fall back to raw transcripts instead of crashing.
    stub = tmp_path / "ccusage-empty"
    stub.write_text('#!/bin/sh\necho "[]"\n')
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    projects = tmp_path / "projects"
    _write_session(projects, "sess-a", _entry("2026-01-06T10:00:00+08:00", req="h0", msg="h0"))

    result = collect_claude_usage(projects, "2026-01-05", "2026-01-11",
                                  ccusage_bin=str(stub))
    assert result["source"] == "raw_transcripts"
    assert result["usage"]["input_tokens"] == 10
    assert "ccusage_discovered_but_failed" in result["notes"]


def test_ccusage_and_raw_paths_agree_on_same_fixture(tmp_path):
    projects = tmp_path / "projects"
    _write_session(projects, "sess-a",
                   _entry("2026-09-07T00:00:30+08:00", req="r1", msg="m1"),
                   _entry("2026-09-07T01:00:00+08:00", req="r1", msg="m1"),
                   _entry("2026-09-13T23:59:59+08:00", req="r2", msg="m2",
                          model="glm-5.3-flash"))
    stub = tmp_path / "ccusage-consistent"
    stub.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"daily\" ]; then\n"
        '  echo \'{"totals":{"inputTokens":30,"outputTokens":6,"cacheReadTokens":15,'
        '"cacheCreationTokens":0},"daily":[{"date":"2026-09-07","inputTokens":20,'
        '"outputTokens":4,"cacheReadTokens":10,"cacheCreationTokens":0,'
        '"modelBreakdowns":[{"modelName":"glm-5.3","inputTokens":20,'
        '"outputTokens":4,"cacheReadTokens":10}]}]}\'\n'
        "else\n"
        '  echo \'{"sessions":[{"sessionId":"sess-a"},{"sessionId":"subagents"}]}\'\n'
        "fi\n",
        encoding="utf-8",
    )
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)

    via_ccusage = collect_claude_usage(projects, "2026-09-07", "2026-09-13",
                                       ccusage_bin=str(stub))
    via_raw = collect_claude_usage(projects, "2026-09-07", "2026-09-13",
                                   allow_ccusage=False)
    for field in ("input_tokens", "output_tokens", "cache_read_tokens",
                  "cache_creation_tokens"):
        assert via_ccusage["usage"][field] == via_raw["usage"][field] == {
            "input_tokens": 30, "output_tokens": 6, "cache_read_tokens": 15,
            "cache_creation_tokens": 0}[field]
    assert via_ccusage["usage"]["subagent_sessions"] == 1


def test_failing_ccusage_falls_back_to_raw(tmp_path):
    broken = tmp_path / "ccusage-broken"
    broken.write_text("#!/bin/sh\nexit 3\n")
    broken.chmod(broken.stat().st_mode | stat.S_IEXEC)
    projects = tmp_path / "projects"
    _write_session(projects, "sess-a", _entry("2026-09-08T10:00:00+08:00"))

    result = collect_claude_usage(projects, "2026-09-07", "2026-09-13",
                                  ccusage_bin=str(broken))
    assert result["source"] == "raw_transcripts"
    assert result["usage"]["input_tokens"] == 10
    assert "ccusage_discovered_but_failed" in result["notes"]


def test_raw_collect_function_matches_combined_totals(tmp_path):
    projects = tmp_path / "projects"
    _write_session(projects, "s1", _entry("2026-09-09T09:00:00+08:00"))
    _write_session(projects, "s2",
                   _entry("2026-09-09T18:00:00+08:00", req="r9", msg="m9", inp=1))
    usage = collect_raw_transcripts(projects, "2026-09-07", "2026-09-13")
    assert usage["sessions"] == 2
    assert usage["input_tokens"] == 11
