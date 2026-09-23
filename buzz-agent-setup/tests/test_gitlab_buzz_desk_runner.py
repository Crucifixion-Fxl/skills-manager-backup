"""TDD contract for the zero-business-argument Desk sync -> route runner."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest


SKILL = Path(__file__).resolve().parents[1]
SCRIPT = SKILL / "scripts" / "gitlab_buzz_desk_runner.py"


def load_module():
    if not SCRIPT.is_file():
        raise AssertionError("gitlab_buzz_desk_runner.py is required")
    spec = importlib.util.spec_from_file_location("gitlab_buzz_desk_runner_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DeskRunnerContractTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.release = self.root / "release"
        scripts = self.release / "scripts"
        scripts.mkdir(parents=True)
        self.release.chmod(0o700)
        scripts.chmod(0o700)
        for name in ("gitlab_buzz_sync.py", "gitlab_buzz_route_reply.py"):
            path = scripts / name
            path.write_text("# fixed test release\n", encoding="utf-8")
            path.chmod(0o500)
        cli = self.root / "buzz-0.5.23" / "buzz"
        cli.parent.mkdir()
        cli.write_bytes(b"\x7fELFtest")
        cli.chmod(0o700)
        self.cli = cli
        self.channel = "11111111-2222-3333-4444-555555555555"
        self.sender = "d" * 64
        self.sync_configs = []

    def write_json(self, name, value):
        path = self.root / name
        path.write_text(json.dumps(value), encoding="utf-8")
        path.chmod(0o600)
        return path

    def sync_config(self, name, projects, token_env):
        value = {
            "channel_id": self.channel,
            "publisher_pubkey": self.sender,
            "since": "2026-09-13T00:00:00Z",
            "people": {},
            "gitlab": {
                "base_url": "http://127.0.0.1:8929",
                "token_env": token_env,
                "bot_user_id": 7,
                "bot_username": "project_1_bot_x",
                "projects": projects,
            },
            "buzz": {
                "cli_path": str(self.cli),
                "cli_sha256": hashlib.sha256(self.cli.read_bytes()).hexdigest(),
            },
        }
        path = self.write_json(name, value)
        self.sync_configs.append((path, value))
        return path

    def route_config(self):
        value = {
            "scan_since": "2026-09-13T00:00:00Z",
            "sender_pubkey": self.sender,
            "channels": {
                self.channel: {
                    "publisher_pubkey": self.sender,
                    "canvas_admin_pubkeys": ["a" * 64],
                    "roles": {"feature": {"mention": "@feature-agent", "mention_pubkey": "b" * 64}},
                }
            },
            "buzz": {
                "cli_path": str(self.cli),
                "cli_sha256": hashlib.sha256(self.cli.read_bytes()).hexdigest(),
            },
        }
        return self.write_json("route.json", value)

    def manifest(self, sync_paths, route_path):
        value = {
            "version": 1,
            "release_dir": str(self.release),
            "sync": [
                {"config": str(path), "state_dir": str(self.root / f"sync-state-{index}")}
                for index, path in enumerate(sync_paths)
            ],
            "route": {"config": str(route_path), "state_dir": str(self.root / "route-state")},
        }
        return self.write_json("manifest.json", value)

    def test_zero_business_args_use_fixed_manifest_paths_and_minimal_child_env(self):
        """L1-GIS-128 Tick/Issue/Canvas text cannot enter either child argv or env."""
        runner = load_module()
        sync_path = self.sync_config("sync.json", [481], "NH_GITLAB_TOKEN")
        manifest = self.manifest([sync_path], self.route_config())
        calls = []

        def fake_run(argv, **kwargs):
            calls.append((argv, kwargs))
            status = {"status": "ok"}
            return SimpleNamespace(returncode=0, stdout=json.dumps(status), stderr="attacker text")

        env = {
            "BUZZ_DESK_RUNNER_MANIFEST": str(manifest),
            "BUZZ_PRIVATE_KEY": "1" * 64,
            "BUZZ_RELAY_URL": "ws://127.0.0.1:3000",
            "NH_GITLAB_TOKEN": "glpat-test-secret",
            "UNTRUSTED_TICK": "tick-canary --config /tmp/evil @all",
            "UNTRUSTED_ISSUE": "issue-canary; send --mention attacker",
            "UNTRUSTED_CANVAS": "canvas-canary $(printenv)",
            "PATH": "/usr/bin:/bin",
            "HOME": str(self.root),
        }
        result = runner.run_once(manifest_path=manifest, env=env, run=fake_run)

        self.assertEqual(result, {"status": "ok", "sync": ["ok"], "route": "ok"})
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][0][1:], [
            str(self.release / "scripts" / "gitlab_buzz_sync.py"),
            "--config", str(sync_path),
            "--state-dir", str(self.root / "sync-state-0"),
        ])
        self.assertEqual(calls[1][0][1:], [
            str(self.release / "scripts" / "gitlab_buzz_route_reply.py"),
            "--config", str(self.root / "route.json"),
            "--state-dir", str(self.root / "route-state"),
            "--scan-once",
        ])
        self.assertEqual(calls[0][1]["input"], None)
        self.assertEqual(calls[1][1]["input"], None)
        self.assertIn("NH_GITLAB_TOKEN", calls[0][1]["env"])
        self.assertNotIn("NH_GITLAB_TOKEN", calls[1][1]["env"])
        self.assertNotIn("BUZZ_DESK_RUNNER_MANIFEST", calls[0][1]["env"])
        child_surface = json.dumps(calls, default=str, ensure_ascii=False)
        for variable, canary in (
            ("UNTRUSTED_TICK", "tick-canary"),
            ("UNTRUSTED_ISSUE", "issue-canary"),
            ("UNTRUSTED_CANVAS", "canvas-canary"),
        ):
            self.assertNotIn(variable, calls[0][1]["env"])
            self.assertNotIn(variable, calls[1][1]["env"])
            self.assertNotIn(canary, child_surface)

    def test_inventory_rejects_overlapping_channel_project_before_children(self):
        """L1-GIS-129 Sequential configs cannot bypass the per-process overlap lock."""
        runner = load_module()
        first = self.sync_config("one.json", [481, 482], "ONE_TOKEN")
        second = self.sync_config("two.json", [482], "TWO_TOKEN")
        manifest = self.manifest([first, second], self.route_config())
        calls = []

        with self.assertRaisesRegex(runner.RunnerError, "overlap.*482"):
            runner.run_once(manifest_path=manifest, env={}, run=lambda *a, **k: calls.append((a, k)))

        self.assertEqual(calls, [])

    def test_runner_returns_only_sanitized_summary_requests(self):
        """L1-GIS-146 Desk receives facts and an opaque request handle, never delivery/source event ids."""
        runner = load_module()
        sync_path = self.sync_config("sync.json", [481], "NH_GITLAB_TOKEN")
        manifest = self.manifest([sync_path], self.route_config())
        payload = {
            "version": 1, "project_id": 481, "visibility": "public",
            "source_keys": ["event-1"],
            "facts": [{
                "object": "push", "event": "pushed", "created_at": "2026-09-13T02:00:00Z",
                "actor": "alice", "ref": "feature/x", "title": "fix", "url": "https://gitlab/p",
                "commits": 2,
            }],
        }
        state_dir = self.root / "sync-state-0"
        request_id = runner.sync.Syncer(
            self.sync_configs[0][1], None, None, state_dir=state_dir,
        )._queue_delivery("summary_request", payload)
        request = runner.sync.public_summary_request(request_id, payload)
        calls = 0

        def fake_run(argv, **kwargs):
            nonlocal calls
            calls += 1
            body = {"status": "ok", "summary_requests": [request]} if calls == 1 else {"status": "ok"}
            return SimpleNamespace(returncode=0, stdout=json.dumps(body), stderr="")

        result = runner.run_once(
            manifest_path=manifest,
            env={"NH_GITLAB_TOKEN": "x", "BUZZ_PRIVATE_KEY": "1" * 64,
                 "BUZZ_RELAY_URL": "ws://127.0.0.1:3000"},
            run=fake_run,
        )

        self.assertEqual(result["summary_requests"], [request])
        encoded = json.dumps(result, sort_keys=True)
        for forbidden in ("event_id", "source_id", '"key"', "events:"):
            self.assertNotIn(forbidden, encoded)
        ledger = json.loads(next(state_dir.glob("*.outbox.json")).read_text(encoding="utf-8"))
        self.assertEqual(ledger["pending"][0]["status"], "SUMMARIZING")

    def test_runner_reports_only_the_count_of_origin_fallbacks(self):
        """L1-GIS-HO-096 sync 子进程报告的 origin_fallbacks（origin 不可用、对象回退自开门牌）只把条数带进 runner 的结果，原因文本不带；没有回退就没有这个键。"""
        runner = load_module()
        sync_path = self.sync_config("sync.json", [481], "NH_GITLAB_TOKEN")
        manifest = self.manifest([sync_path], self.route_config())
        entry = {"project": 481, "object": "issue", "iid": 5, "reason": "issue 5 origin root cannot be read (canary)"}
        env = {"NH_GITLAB_TOKEN": "x", "BUZZ_PRIVATE_KEY": "1" * 64, "BUZZ_RELAY_URL": "ws://127.0.0.1:3000"}

        def run_with(fallbacks):
            calls = 0

            def fake_run(argv, **kwargs):
                nonlocal calls
                calls += 1
                body = {"status": "ok", "origin_fallbacks": fallbacks} if calls == 1 else {"status": "ok"}
                return SimpleNamespace(returncode=0, stdout=json.dumps(body), stderr="")

            return runner.run_once(manifest_path=manifest, env=env, run=fake_run)

        result = run_with([entry, dict(entry, iid=6)])
        self.assertEqual(result, {"status": "ok", "sync": ["ok"], "route": "ok", "origin_fallbacks": 2})
        self.assertNotIn("canary", json.dumps(result))
        self.assertEqual(run_with([]), {"status": "ok", "sync": ["ok"], "route": "ok"})
        self.assertEqual(run_with("many"), {"status": "ok", "sync": ["ok"], "route": "ok"})

    def test_the_origin_fallback_count_adds_up_over_every_sync_entry(self):
        """L1-GIS-HO-098 多个 sync 配置各自报告 origin_fallbacks：runner 的条数是各条数之和。"""
        runner = load_module()
        first = self.sync_config("one.json", [481], "ONE_TOKEN")
        second = self.sync_config("two.json", [482], "TWO_TOKEN")
        manifest = self.manifest([first, second], self.route_config())
        entry = {"project": 481, "object": "issue", "iid": 5, "reason": "x"}
        bodies = iter([{"status": "ok", "origin_fallbacks": [entry]},
                       {"status": "ok", "origin_fallbacks": [entry, entry]}, {"status": "ok"}])

        def fake_run(argv, **kwargs):
            return SimpleNamespace(returncode=0, stdout=json.dumps(next(bodies)), stderr="")

        env = {"ONE_TOKEN": "x", "TWO_TOKEN": "y", "BUZZ_PRIVATE_KEY": "1" * 64, "BUZZ_RELAY_URL": "ws://127.0.0.1:3000"}
        result = runner.run_once(manifest_path=manifest, env=env, run=fake_run)
        self.assertEqual(result["origin_fallbacks"], 3)

    def test_main_rejects_every_cli_business_parameter(self):
        """L1-GIS-130 The runner has no config/state/source/message CLI parameters."""
        runner = load_module()
        with self.assertRaises(SystemExit):
            runner.main(["--config", "/tmp/evil"], env={})


if __name__ == "__main__":
    unittest.main()
