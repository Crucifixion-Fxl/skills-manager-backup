"""Per-case evidence receipts for local-relay runs (2026-09-17 plan §1c).

Every integration case ends with a receipt JSON under
~/buzz-agent-work/l4-local-evidence/<run-id>/ capturing the verdict, the relay
events that appeared during the case (full readback: id/pubkey/created_at/
content/tags), and the error text when red.  index.md summarizes the run so the
owner can audit without reading raw JSON.  No secrets are ever written: the
channel key is used only in-process for readback, values never hit disk.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

STACK_STATE = Path(__file__).resolve().parent.parent / "localstack" / ".state" / "state.json"
EVIDENCE_ROOT = Path.home() / "buzz-agent-work" / "l4-local-evidence"
# AI reply content oracle: an agent confessing a capability gap is a config
# finding, not a pass.  Whitelist per case via `allow_gaps`.
GAP_MARKERS = (
    "无权限", "读不到", "没有权限", "无法读取", "凭据缺失", "未授权",
    "permission denied", "cannot read", "no access",
)


def _buzz_cli() -> Path:
    cli = Path.home() / ".local/opt/buzz-0.5.23/usr/bin/buzz"
    if not cli.is_file():
        raise RuntimeError("pinned Desktop buzz CLI not found")
    return cli


class EvidenceRun:
    def __init__(self, run_id: str | None = None):
        self.run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.dir = EVIDENCE_ROOT / self.run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        state = json.loads(STACK_STATE.read_text(encoding="utf-8"))
        relay = state["relay"]
        self.channel = relay["channel_id"]
        owner_key = Path(state["identities"]["owner"]["secret_file"]) if isinstance(
            state.get("identities", {}).get("owner", {}).get("secret_file"), str
        ) else Path(str((Path(STACK_STATE).parent / "secrets/owner.key")))
        self._env = {
            **os.environ,
            "BUZZ_PRIVATE_KEY": owner_key.read_text(encoding="utf-8").strip(),
            "BUZZ_RELAY_URL": relay["http_url"],
        }
        self.results: list[dict] = []

    def channel_events(self, since_unix: int = 0) -> list[dict]:
        proc = subprocess.run(
            [str(_buzz_cli()), "messages", "get", "--channel", self.channel,
             "--kinds", "9", "--since", str(int(since_unix)), "--limit", "200"],
            env=self._env, capture_output=True, text=True, timeout=60, check=False,
        )
        if proc.returncode != 0:
            return []
        try:
            events = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return []
        return events if isinstance(events, list) else events.get("events", [])

    @staticmethod
    def config_gaps(texts: list[str], allow: tuple[str, ...] = ()) -> list[str]:
        """Content-level oracle: capability confessions in AI replies are findings."""

        findings = []
        for text in texts:
            for marker in GAP_MARKERS:
                if marker in text and not any(allowed in text for allowed in allow):
                    findings.append(marker)
        return sorted(set(findings))

    def capture(self, case_id: str, status: str, started: float, error: str = "",
                extra: dict | None = None, allow_gaps: tuple[str, ...] = ()) -> None:
        events = self.channel_events(since_unix=int(started) - 1)
        contents = [str(event.get("content", "")) for event in events]
        receipt = {
            "case": case_id,
            "status": status,
            "started_at_utc": datetime.fromtimestamp(started, tz=timezone.utc).isoformat(),
            "captured_at_utc": datetime.now(timezone.utc).isoformat(),
            "channel_event_count": len(events),
            "config_gap_findings": self.config_gaps(contents, allow_gaps),
            "error": (error or "")[:2000],
            "events": [
                {
                    "id": event.get("id"),
                    "pubkey": str(event.get("pubkey", ""))[:16] + "…",
                    "created_at": event.get("created_at"),
                    "content": str(event.get("content", ""))[:1000],
                    "tags": event.get("tags"),
                }
                for event in events
            ],
            **(extra or {}),
        }
        (self.dir / f"{case_id}.json").write_text(
            json.dumps(receipt, ensure_ascii=False, indent=1), encoding="utf-8",
        )
        self.results.append(receipt)

    def finalize(self) -> Path:
        passed = sum(1 for r in self.results if r["status"] == "pass")
        failed = len(self.results) - passed
        gaps = sorted({g for r in self.results for g in r["config_gap_findings"]})
        lines = [
            f"# 本地 relay 证据 {self.run_id}",
            "",
            f"- 用例：{len(self.results)}（pass {passed} / fail {failed}）",
            f"- 内容级配置发现（AI 回复自白）：{gaps or '无'}",
            f"- 频道：{self.channel}",
            "",
            "| 用例 | 判定 | 新增频道消息 | 配置发现 | 错误摘要 |",
            "|---|---|---|---|---|",
        ]
        for r in self.results:
            error = (r.get("error") or "").split("\n")[0][:80]
            lines.append(
                f"| {r['case']} | {r['status']} | {r['channel_event_count']} "
                f"| {', '.join(r['config_gap_findings']) or '-'} | {error} |"
            )
        lines += ["", f"原始回读见本目录 `*.json`；栈保持运行可直接用 buzz CLI 查频道。"]
        index = self.dir / "index.md"
        index.write_text("\n".join(lines), encoding="utf-8")
        return index


class EvidenceResult(unittest.TextTestResult):
    """unittest result that writes one receipt per case."""

    run: EvidenceRun | None = None

    def startTest(self, test):
        super().startTest(test)
        self._test = test
        self._case_started = time.time()
        self._case_id = f"{test.__class__.__name__}.{test._testMethodName}"

    def _capture(self, status: str, error: str = ""):
        if self.run is None:
            return
        doc = ""
        try:
            method = getattr(self._test, self._test._testMethodName)
            doc = (method.__doc__ or "").strip().split("\n")[0]
        except Exception:
            pass
        self.run.capture(self._case_id, status, self._case_started, error,
                         extra={"doc": doc})

    def addSuccess(self, test):
        super().addSuccess(test)
        self._capture("pass")

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._capture("fail", self._exc_info_to_string(err, test))

    def addError(self, test, err):
        super().addError(test, err)
        self._capture("error", self._exc_info_to_string(err, test))
