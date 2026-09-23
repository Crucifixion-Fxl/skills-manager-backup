from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from collect_zcode_usage import collect_zcode_usage  # noqa: E402

PAYLOAD_MARKER = "REQUEST-PAYLOAD-MUST-NOT-LEAK"


def _rollout(ts, model=None, req="r1", turn="t1", attempt=0, session="s1",
             inp=100, out=20, cache_read=50, cache_write=5):
    entry = {
        "startedAt": ts,
        "completedAt": ts,
        "requestId": req,
        "turnId": turn,
        "attempt": attempt,
        "sessionId": session,
        "type": "model.io",
        "request": {"text": PAYLOAD_MARKER},
        "response": {
            "usage": {
                "inputTokens": inp,
                "outputTokens": out,
                "cacheReadTokens": cache_read,
                "cacheWriteTokens": cache_write,
            },
        },
    }
    if model is not None:
        entry["model"] = model
    return entry


def _write(rollout_dir: Path, name: str, *entries: dict) -> None:
    rollout_dir.mkdir(parents=True, exist_ok=True)
    with open(rollout_dir / name, "w", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(json.dumps(entry) + "\n")


def test_rollout_counters_dedup_and_model_id(tmp_path):
    rollout = tmp_path / "rollout"
    _write(
        rollout, "model-io-sess_a.jsonl",
        _rollout("2026-09-07T09:00:00+08:00", model={"modelId": "GLM-5.3", "role": "main"}),
        _rollout("2026-09-07T09:00:00+08:00", model={"modelId": "GLM-5.3", "role": "main"}),  # dup
        _rollout("2026-09-08T12:30:00+08:00", model="GLM-5.3", req="r2", turn="t2", session="s2"),
        _rollout("2026-09-06T23:00:00+08:00"),  # before week
        _rollout("2026-09-14T00:30:00+08:00"),  # after week
    )
    result = collect_zcode_usage(rollout, "2026-09-07", "2026-09-13")
    usage = result["usage"]
    assert result["available"] is True
    assert usage["calls"] == 2
    assert usage["sessions"] == 2
    assert usage["input_tokens"] == 200
    assert usage["output_tokens"] == 40
    assert usage["cache_read_tokens"] == 100
    assert usage["cache_write_tokens"] == 10
    assert [d["date"] for d in usage["daily"]] == ["2026-09-07", "2026-09-08"]
    assert {m["model"] for m in usage["model_breakdown"]} == {"GLM-5.3"}
    assert usage["model_breakdown"][0]["calls"] == 2


def test_missing_rollout_dir_is_unavailable_not_zero(tmp_path):
    result = collect_zcode_usage(tmp_path / "nope", "2026-09-07", "2026-09-13")
    assert result["available"] is False
    assert result["reason"] == "rollout_dir_missing"
    assert result["usage"]["input_tokens"] == 0


def test_output_never_contains_payloads(tmp_path):
    rollout = tmp_path / "rollout"
    _write(rollout, "model-io-sess_a.jsonl",
           _rollout("2026-09-09T09:00:00+08:00", model="GLM-5.3"))
    rendered = json.dumps(collect_zcode_usage(rollout, "2026-09-07", "2026-09-13"))
    assert PAYLOAD_MARKER not in rendered
