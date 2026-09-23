"""TDD contract for the owner timer that runs GitLab sync without an LLM (ADR-0008)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


SKILL = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def load(name: str):
    path = SCRIPTS / f"{name}.py"
    if not path.is_file():
        raise AssertionError(f"{name}.py is required")
    spec = importlib.util.spec_from_file_location(f"{name}_timer_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


import gitlab_buzz_summary_publish as publish  # noqa: E402
import gitlab_buzz_sync as sync  # noqa: E402


def fact(obj: str, event: str = "pushed", *, ref: str = "", title: str = "", commits: int = 0) -> dict:
    return {
        "object": obj, "event": event, "created_at": "2026-09-17T01:00:00Z", "actor": "alice",
        "ref": ref, "title": title, "url": "https://gitlab.example/x", "commits": commits,
    }


def request(facts: list[dict], request_id: str = "a" * 64) -> dict:
    return {"request_id": request_id, "project_id": 1175, "facts": facts,
            "facts_sha256": sync.facts_digest(facts)}


class TemplateSummaryTest(unittest.TestCase):
    def test_template_summary_passes_publisher_gates(self):
        """L1-GIS-182 模板摘要不经 LLM 生成，满足 publisher 的字符、计数与 ref 校验。"""
        timer = load("gitlab_buzz_sync_timer")
        facts = [
            fact("push", ref="feature/workspace-restore", commits=3),
            fact("push", ref="main", commits=1),
            fact("pipeline", "failed", ref="main"),
            fact("note", "commented", title="Issue title"),
        ]
        prose = timer.template_summary(facts)
        self.assertEqual(prose, publish._validated_summary(prose))
        publish.verify_summary_against_facts(prose, facts)
        self.assertIn("4", prose)
        self.assertIn("feature/workspace-restore", prose)
        self.assertNotIn("\n", prose)

    def test_unsafe_ref_names_fall_back_to_counts_only(self):
        """L1-GIS-182 ref 含 publisher 禁用字符时不写 ref，只写计数，仍可发布。"""
        timer = load("gitlab_buzz_sync_timer")
        facts = [fact("push", ref="feat/a(b)|c", commits=2), fact("push", ref="x" * 900, commits=1)]
        prose = timer.template_summary(facts)
        self.assertEqual(prose, publish._validated_summary(prose))
        publish.verify_summary_against_facts(prose, facts)
        self.assertNotIn("feat/a", prose)


class TimerRunTest(unittest.TestCase):
    def test_timer_syncs_once_then_publishes_every_claimed_request(self):
        """L1-GIS-183 timer 先跑一次 runner，再逐个发布已 claim 的摘要请求，回执原样绑定 facts。"""
        timer = load("gitlab_buzz_sync_timer")
        first = request([fact("push", ref="main", commits=1)], "a" * 64)
        second = request([fact("note", "commented")], "b" * 64)
        calls: list[tuple] = []
        queue = [second]

        def run_once(*, manifest_path, env):
            calls.append(("run_once", manifest_path))
            return {"status": "ok", "sync": ["ok"], "route": "ok", "summary_requests": [first]}

        def claim_next(*, manifest_path):
            calls.append(("claim",))
            return queue.pop(0) if queue else None

        def execute(*, manifest_path, summary, facts, env):
            calls.append(("publish", facts, summary))
            return {"status": "sent"}

        result = timer.run_timer(manifest_path=Path("/fixed/manifest.json"), env={},
                                 run_once=run_once, claim_next=claim_next, execute=execute)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["published"], 2)
        self.assertEqual([c[0] for c in calls], ["run_once", "publish", "claim", "publish", "claim"])
        self.assertEqual([c[1] for c in calls if c[0] == "publish"],
                         [first["facts_sha256"], second["facts_sha256"]])

    def test_timer_stops_on_runner_error_and_on_publish_error(self):
        """L1-GIS-184 runner 失败时不发布；发布失败时停止本轮并返回错误，不继续 claim 下一条。"""
        timer = load("gitlab_buzz_sync_timer")
        published: list[str] = []

        def execute(*, manifest_path, summary, facts, env):
            published.append(facts)
            raise publish.PublishError("readback mismatch")

        failed = timer.run_timer(
            manifest_path=Path("/m"), env={},
            run_once=lambda **_: {"status": "error", "stage": "sync", "error": "boom"},
            claim_next=lambda **_: self.fail("must not claim after runner error"),
            execute=execute,
        )
        self.assertEqual(failed["status"], "error")
        self.assertEqual(published, [])

        req = request([fact("push", ref="main", commits=1)])
        stopped = timer.run_timer(
            manifest_path=Path("/m"), env={},
            run_once=lambda **_: {"status": "ok", "sync": ["ok"], "route": "ok", "summary_requests": [req]},
            claim_next=lambda **_: self.fail("must not claim after publish error"),
            execute=execute,
        )
        self.assertEqual(stopped["status"], "error")
        self.assertEqual(stopped["stage"], "summary")
        self.assertEqual(published, [req["facts_sha256"]])

    def test_timer_caps_summaries_per_run_and_skips_publishing_when_locked(self):
        """L1-GIS-203 one run publishes at most 20 summaries and claims nothing past the cap; a locked runner result publishes nothing."""
        timer = load("gitlab_buzz_sync_timer")
        req = request([fact("push", ref="main", commits=1)])
        published = []
        claims = []
        result = timer.run_timer(
            manifest_path=Path("/m"), env={},
            run_once=lambda **_: {"status": "ok", "sync": ["ok"], "route": "ok", "summary_requests": [req]},
            claim_next=lambda **_: claims.append(1) or req,
            execute=lambda **kw: published.append(kw["facts"]) or {"status": "sent"},
        )
        self.assertEqual(result["published"], timer.MAX_SUMMARIES_PER_RUN)
        self.assertEqual(len(published), 20)
        self.assertEqual(len(claims), 19, "the 21st request must stay PENDING, not be claimed by a run that will not publish it")
        locked = timer.run_timer(
            manifest_path=Path("/m"), env={},
            run_once=lambda **_: {"status": "locked", "sync": ["locked"], "route": "ok"},
            claim_next=lambda **_: self.fail("must not claim while locked"),
            execute=lambda **_: self.fail("must not publish while locked"),
        )
        self.assertEqual(locked["published"], 0)

    def test_timer_entrypoint_takes_no_arguments_and_needs_manifest(self):
        """L1-GIS-185 入口不接受任何参数；缺 BUZZ_DESK_RUNNER_MANIFEST 时失败且不启动任何子进程。"""
        timer = load("gitlab_buzz_sync_timer")
        with self.assertRaises(SystemExit):
            timer.main(["--config", "/tmp/x"], env={"BUZZ_DESK_RUNNER_MANIFEST": "/m"})
        self.assertEqual(timer.main([], env={}), 2)


if __name__ == "__main__":
    unittest.main()
