"""TDD contract for the fixed-destination AI activity-summary publisher."""

from __future__ import annotations

import hashlib
import importlib.util
import inspect
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock


TESTS = Path(__file__).resolve().parent
SKILL = TESTS.parent
SCRIPT = SKILL / "scripts" / "gitlab_buzz_summary_publish.py"


def load(name: str, path: Path):
    if not path.is_file():
        raise AssertionError(f"{path.name} is required")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


EVENTS = load("gitlab_buzz_summary_publish_events", TESTS / "test_gitlab_buzz_sync_events.py")
SYNC = EVENTS.SYNC


class PublishBuzz(EVENTS.FakeBuzz):
    def __init__(self):
        super().__init__()
        self.fail_after_accept = False
        self.uncertain_before_store = False
        self.reject_send = False
        self.error_type = SYNC.SyncError
        self.reject_type = SYNC.SyncError

    def send(self, content, reply_to=None, mentions=()):
        if self.reject_send:
            self.reject_send = False
            raise self.reject_type("relay definitively rejected the send")
        if self.uncertain_before_store:
            self.uncertain_before_store = False
            raise self.error_type("send outcome unknown: timed out before acceptance")
        event_id = super().send(content, reply_to, mentions)
        if self.events:
            # Relay events page by unix-second created_at; keep the fake realistic.
            self.events[-1]["created_at"] = int(time.time())
        if self.fail_after_accept:
            self.fail_after_accept = False
            raise self.error_type("accepted but readback was unavailable")
        return event_id

    def channel_members(self):
        return {EVENTS.DESK: "member"}

    def thread(self, root_event_id):
        return [event for event in self.events if event.get("id") == root_event_id]


class SummaryPublisherTest(unittest.TestCase):
    def setUp(self):
        self.publisher = load("gitlab_buzz_summary_publish_test", SCRIPT)
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
        self.config = {
            "channel_id": EVENTS.CHANNEL,
            "publisher_pubkey": EVENTS.DESK,
            "since": EVENTS.SINCE,
            "include_confidential": False,
            "exclude": [],
            "diff": {"enabled": False, "private": False},
            "gitlab": {
                "base_url": "http://127.0.0.1:8929",
                "token_env": "NH_DESK_GITLAB_TOKEN",
                "bot_user_id": EVENTS.BOT_ID,
                "bot_username": EVENTS.BOT,
                "projects": [EVENTS.PID],
            },
            "buzz": {"cli_path": str(cli), "cli_sha256": hashlib.sha256(cli.read_bytes()).hexdigest()},
            "people": {},
        }
        self.config_path = self.write_json("sync.json", self.config)
        self.state = self.root / "sync-state"
        route = {
            "scan_since": EVENTS.SINCE,
            "sender_pubkey": EVENTS.DESK,
            "channels": {EVENTS.CHANNEL: {
                "publisher_pubkey": EVENTS.DESK,
                "canvas_admin_pubkeys": ["a" * 64],
                "roles": {"feature": {"mention": "@feature-agent", "mention_pubkey": "b" * 64}},
            }},
            "buzz": self.config["buzz"],
        }
        route_path = self.write_json("route.json", route)
        self.manifest = self.write_json("manifest.json", {
            "version": 1,
            "release_dir": str(self.release),
            "sync": [{"config": str(self.config_path), "state_dir": str(self.state)}],
            "route": {"config": str(route_path), "state_dir": str(self.root / "route-state")},
        })
        self.gitlab = EVENTS.FakeGitLab()
        # Live push records no longer exist (2026-09-18 notification policy), so the
        # pending summary request these publisher tests need is seeded by a synthetic
        # digest record — the pre-policy shape of a default-branch push.
        self.gitlab.event_list = [EVENTS.push_event(1, ref="main")]
        self.buzz = PublishBuzz()
        self.buzz.error_type = self.publisher.sync.SyncError
        self.buzz.reject_type = self.publisher.sync.BuzzSendRejected

        def synthetic_push_digest(event, project_id, web_url):
            push = event.get("push_data")
            if not isinstance(push, dict):
                return None
            raw_ref = SYNC._single_line(push.get("ref") or "")
            return SYNC._record(f"event-{event['id']}", "push", "pushed", "digest", project_id,
                                url=f"{web_url}/-/commits/{raw_ref}",
                                created_at=event.get("created_at"),
                                actor=(event.get("author") or {}).get("username") or "?",
                                ref=SYNC.neutralize(raw_ref), commits=push.get("commit_count") or 0)

        with mock.patch.object(SYNC, "record_from_event", side_effect=synthetic_push_digest):
            queued = SYNC.Syncer(self.config, self.gitlab, self.buzz, state_dir=self.state).run()
        scopes = [(self.config, self.state)]
        handles = self.publisher.sync.acquire_scope_locks(scopes, "test summary inventory")
        try:
            selected = self.publisher.sync.select_summary_request(scopes, claim=True)
        finally:
            for handle in reversed(handles):
                handle.close()
        self.assertIsNotNone(selected)
        self.request = selected["request"]
        self.assertEqual(self.request, queued["summary_requests"][0])
        self.buzz.writes.clear()
        self.buzz.events.clear()

    def write_json(self, name, value):
        path = self.root / name
        path.write_text(json.dumps(value), encoding="utf-8")
        path.chmod(0o600)
        return path

    def facts_digest_of(self, facts):
        canonical = json.dumps(facts, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def pending_facts_digest(self):
        pending = self.ledger()["pending"]
        if not pending:
            return "0" * 64
        return self.facts_digest_of(pending[0]["payload"]["facts"])

    def execute(self, summary="本次有分支更新。", facts=None):
        return self.publisher.execute(
            manifest_path=self.manifest,
            summary=summary,
            facts=self.pending_facts_digest() if facts is None else facts,
            env={"NH_DESK_GITLAB_TOKEN": "token", "BUZZ_PRIVATE_KEY": "1" * 64,
                 "BUZZ_RELAY_URL": "ws://127.0.0.1:3000"},
            adapter_factory=lambda config, env, dry_run: (self.gitlab, self.buzz),
        )

    def ledger(self):
        return json.loads(next(self.state.glob("*.outbox.json")).read_text(encoding="utf-8"))

    def test_publish_uses_fixed_destination_readbacks_then_acks_without_exposing_ids(self):
        """L1-GIS-147 AI supplies text only; destination is fixed and event ids stay in owner state."""
        result = self.execute()

        self.assertEqual(result, {"status": "sent"})
        self.assertEqual(len(self.buzz.writes), 1)
        reply_to, content, mentions = self.buzz.writes[0]
        self.assertIsNone(reply_to)
        self.assertEqual(mentions, ())
        self.assertEqual(content.split("\n")[0], "本次有分支更新。")
        self.assertEqual(content.split("\n")[1:], self.publisher.summary_link_lines(self.request["facts"]))
        self.assertNotIn("events:", content)
        self.assertNotIn("event-1", content)
        for forbidden in ("[gitlab-notify:", "object:", "project:"):
            self.assertNotIn(forbidden, content)
        ledger = self.ledger()
        self.assertEqual(ledger["pending"], [])
        self.assertEqual(ledger["acked"][-1]["kind"], "summary_request")
        self.assertRegex(ledger["acked"][-1]["result_id"], r"^[0-9a-f]{64}$")
        self.assertNotIn("event_id", json.dumps(result))

        self.assertEqual(self.execute(), {"status": "idle"})
        self.assertEqual(len(self.buzz.writes), 1)

    def test_accepted_but_unreadable_retry_is_read_only_and_never_resends(self):
        """L1-GIS-148 A PUBLISHING request is recovered by exact readback; it is never sent twice."""
        self.buzz.fail_after_accept = True
        with self.assertRaises(self.publisher.PublishError):
            self.execute()

        ledger = self.ledger()
        self.assertEqual(ledger["pending"][0]["status"], "PUBLISHING")
        self.assertEqual(len(self.buzz.writes), 1)

        self.assertEqual(self.execute("模型重试时生成了不同但有效的措辞。"), {"status": "duplicate"})
        self.assertEqual(len(self.buzz.writes), 1)
        self.assertEqual(self.ledger()["pending"], [])

    def test_cli_accepts_one_summary_and_no_selectors_or_trailing_argv(self):
        """L1-GIS-149 The allowlisted argv suffix is text only; target and request selectors do not exist."""
        parameters = inspect.signature(self.publisher.execute).parameters
        for forbidden in ("request_id", "input_path", "channel_id", "project_id", "mentions", "source"):
            self.assertNotIn(forbidden, parameters)
        for argv in (
            ["--input", "request.json"],
            ["--summary", "正常摘要。", "--channel", EVENTS.CHANNEL],
            ["--summary", "正常摘要。", "--project", str(EVENTS.PID)],
            ["--summary", "正常摘要。", "--request-id", "a" * 64],
            ["--summary", "正常摘要。", "--mention", "b" * 64],
            ["--summary", "正常摘要。", "--source", "event-1"],
            ["--summary", "第一条。", "--summary", "第二条。"],
            ["--summary", "正常摘要。", "trailing"],
        ):
            with self.subTest(argv=argv), mock.patch("sys.stderr", new=io.StringIO()), self.assertRaises(SystemExit):
                self.publisher.main(argv, env={})
        self.assertEqual(self.buzz.writes, [])

    def test_summary_rejects_ids_links_mentions_protocol_fields_and_multiline(self):
        """L1-GIS-150 AI prose cannot smuggle routing or transport metadata into the fixed message."""
        source_key = self.ledger()["pending"][0]["payload"]["source_keys"][0]
        bad = (
            "通知 @alice",
            "nostr:npub1secret",
            "详情 https://gitlab.example/1",
            "events: event-1",
            "[gitlab-notify:v1] 摘要",
            "event_id abc",
            "a" * 64,
            source_key,
            "第一行\n第二行",
            " 前后空格 ",
            "don't publish this",
            r"路径\内容",
            "过长" * 501,
        )
        for summary in bad:
            with self.subTest(summary=summary[:30]), self.assertRaises(self.publisher.PublishError):
                self.execute(summary)
        self.assertEqual(self.buzz.writes, [])

    def test_summary_rejects_internal_numeric_ids_line_separators_and_shell_metacharacters(self):
        """L1-GIS-156 Numeric GitLab ids, Unicode line breaks and shell metacharacters never cross to prose."""
        bad = (
            "议题 #268 有更新",
            "MR !1024 已可评审",
            "issue 12345 有新评论",
            "iid 999 变更",
            "编号 888 已更新",
            "流水 67890",
            "第一行 第二行",
            "第一行 第二行",
            "第一行第二行",
            "花费 $100",
            "命令 `ls` 执行",
            "分号; 连接",
            "a & b",
            "x | y",
            "<重定向>",
            "(括号)",
        )
        for summary in bad:
            with self.subTest(summary=summary[:20]), self.assertRaises(self.publisher.PublishError):
                self.execute(summary)
        self.assertEqual(self.buzz.writes, [])

    def test_zero_pending_is_idle_and_never_constructs_adapters(self):
        """L1-GIS-151 A heartbeat with no pending activity is silent and performs no Buzz send/read."""
        self.execute()
        adapter = mock.Mock(side_effect=AssertionError("idle publisher must not construct network adapters"))

        result = self.publisher.execute(
            manifest_path=self.manifest,
            summary="本轮没有需要公开的活动。",
            facts="0" * 64,
            env={},
            adapter_factory=adapter,
        )

        self.assertEqual(result, {"status": "idle"})
        adapter.assert_not_called()
        self.assertEqual(len(self.buzz.writes), 1)

    def test_stale_turn_facts_receipt_never_publishes_to_a_later_request(self):
        """L1-GIS-154 A delayed turn's prose stays bound to the request its facts came from."""
        digest_first = self.pending_facts_digest()
        self.assertEqual(self.execute(facts=digest_first), {"status": "sent"})
        self.assertEqual(len(self.buzz.writes), 1)

        newer_fact = {
            "object": "push", "event": "pushed", "created_at": "2026-09-13T05:00:00Z",
            "actor": "carol", "ref": "later/ref", "title": "later request", "url": "",
            "commits": 2,
        }
        newer_payload = {
            "version": 1, "project_id": EVENTS.PID, "visibility": "public",
            "source_keys": ["event-2"], "facts": [newer_fact],
        }
        SYNC.Syncer(self.config, None, None, state_dir=self.state)._queue_delivery(
            "summary_request", newer_payload,
        )
        scopes = [(self.config, self.state)]
        handles = self.publisher.sync.acquire_scope_locks(scopes, "test stale turn")
        try:
            selected = self.publisher.sync.select_summary_request(scopes, claim=True)
        finally:
            for handle in reversed(handles):
                handle.close()
        self.assertEqual(selected["request"]["facts"], [newer_fact])
        digest_newer = self.facts_digest_of(newer_payload["facts"])

        with self.assertRaises(self.publisher.PublishError):
            self.execute(summary="上一轮的旧措辞。", facts=digest_first)
        self.assertEqual(len(self.buzz.writes), 1)
        pending = self.ledger()["pending"]
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["status"], "SUMMARIZING")

        self.assertEqual(self.execute(summary="较新的分支有一次更新。", facts=digest_newer), {"status": "sent"})
        self.assertEqual(self.buzz.writes[-1][1], "较新的分支有一次更新。")

    def test_facts_receipt_is_required_exactly_once_and_checked_before_any_write(self):
        """L1-GIS-155 The publisher binds prose to the pending request via an opaque facts receipt."""
        parameters = inspect.signature(self.publisher.execute).parameters
        self.assertIn("facts", parameters)
        with self.assertRaises(self.publisher.PublishError):
            self.execute(facts="f" * 64)
        self.assertEqual(self.buzz.writes, [])
        self.assertEqual(self.ledger()["pending"][0]["status"], "SUMMARIZING")
        for argv in (
            ["--summary", "正常摘要。"],
            ["--summary", "正常摘要。", "--facts", "a" * 64, "--facts", "b" * 64],
        ):
            with self.subTest(argv=argv), mock.patch("sys.stderr", new=io.StringIO()), self.assertRaises(SystemExit):
                self.publisher.main(argv, env={})
        with mock.patch("sys.stdout", new=io.StringIO()) as out:
            code = self.publisher.main(
                ["--summary", "正常摘要。", "--facts", "not-hex"],
                env={self.publisher.desk_runner.MANIFEST_ENV: str(self.manifest)},
            )
        self.assertEqual(code, 1)
        self.assertIn("64-hex", json.loads(out.getvalue())["error"])
        self.assertEqual(self.buzz.writes, [])

    def test_summary_numbers_and_refs_must_match_the_bound_facts(self):
        """L1-GIS-157 Prose counts and ref names must be semantically consistent with the bound facts."""
        with self.assertRaises(self.publisher.PublishError):
            self.execute("今天共有 5 项更新。")
        with self.assertRaises(self.publisher.PublishError):
            self.execute("分支 feature/evil 有更新。")
        self.assertEqual(self.buzz.writes, [])
        self.assertEqual(self.ledger()["pending"][0]["status"], "SUMMARIZING")

        self.assertEqual(self.execute("分支 main 有更新。"), {"status": "sent"})

        payload = self.ledger()["acked"][-1]
        self.assertEqual(payload["status"], "ACKED")
        original_payload = json.loads(
            json.dumps(self.request["facts"])
        )
        SYNC.Syncer(self.config, None, None, state_dir=self.state)._queue_delivery(
            "summary_request", {
                "version": 1, "project_id": EVENTS.PID, "visibility": "public",
                "source_keys": ["event-1"], "facts": original_payload,
            },
        )
        scopes = [(self.config, self.state)]
        handles = self.publisher.sync.acquire_scope_locks(scopes, "test consistency")
        try:
            self.assertIsNotNone(
                self.publisher.sync.select_summary_request(scopes, claim=True),
            )
        finally:
            for handle in reversed(handles):
                handle.close()

        self.assertEqual(self.execute("1 次推送共带来 2 个提交。"), {"status": "sent"})
        self.assertEqual(self.buzz.writes[-1][1].split("\n")[0], "1 次推送共带来 2 个提交。")

    def queue_and_claim_second_request(self):
        newer_fact = {
            "object": "push", "event": "pushed", "created_at": "2026-09-13T05:00:00Z",
            "actor": "carol", "ref": "second/ref", "title": "second request", "url": "",
            "commits": 3,
        }
        payload = {
            "version": 1, "project_id": EVENTS.PID, "visibility": "public",
            "source_keys": ["event-9"], "facts": [newer_fact],
        }
        SYNC.Syncer(self.config, None, None, state_dir=self.state)._queue_delivery(
            "summary_request", payload,
        )
        scopes = [(self.config, self.state)]
        handles = self.publisher.sync.acquire_scope_locks(scopes, "test second request")
        try:
            return self.publisher.sync.select_summary_request(scopes, claim=True)
        finally:
            for handle in reversed(handles):
                handle.close()

    def test_identical_earlier_summary_is_never_acked_as_this_request(self):
        """L1-GIS-158 A retry proves only THIS request's event, never an identical older summary."""
        self.assertEqual(self.execute(), {"status": "sent"})
        earlier_event_id = self.buzz.events[-1]["id"]
        self.assertIsNotNone(self.queue_and_claim_second_request())

        self.buzz.uncertain_before_store = True
        with self.assertRaises(self.publisher.PublishError):
            self.execute(summary="本次有分支更新。")
        pending = self.ledger()["pending"]
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["status"], "PUBLISHING")

        with self.assertRaises(self.publisher.PublishError):
            self.execute(summary="重试措辞不影响绑定内容。")
        self.assertEqual(len(self.buzz.writes), 1)
        pending = self.ledger()["pending"]
        self.assertEqual(pending[0]["status"], "PUBLISHING")
        acked_ids = [item.get("result_id") for item in self.ledger()["acked"]]
        self.assertNotIn(earlier_event_id, acked_ids[1:])
        self.assertEqual(len(self.ledger()["acked"]), 1)

    def test_definitively_rejected_send_resends_only_the_bound_content(self):
        """L1-GIS-159 A definitively rejected send retries its bound prose; an unknown outcome never resends."""
        self.buzz.reject_send = True
        with self.assertRaises(self.publisher.PublishError):
            self.execute(summary="首次绑定的措辞。")
        pending = self.ledger()["pending"]
        self.assertEqual(pending[0]["status"], "PUBLISHING")
        self.assertEqual(pending[0]["publication"]["content"].split("\n")[0], "首次绑定的措辞。")
        self.assertEqual(self.buzz.writes, [])

        self.assertEqual(self.execute(summary="模型后来换了措辞。"), {"status": "sent"})
        self.assertEqual(len(self.buzz.writes), 1)
        self.assertEqual(self.buzz.writes[0][1].split("\n")[0], "首次绑定的措辞。")
        self.assertEqual(self.ledger()["pending"], [])
        self.assertRegex(self.ledger()["acked"][-1]["result_id"], r"^[0-9a-f]{64}$")

    def test_scope_change_and_ack_trim_never_republish_summaries(self):
        """L1-GIS-160 Splitting/merging configs or losing old ACK rows never re-queues published keys."""
        self.assertEqual(self.execute(), {"status": "sent"})
        self.assertEqual(len(self.buzz.writes), 1)

        merged_config = json.loads(json.dumps(self.config))
        merged_config["gitlab"]["projects"] = [EVENTS.PID, 482]
        second = SYNC.Syncer(merged_config, self.gitlab, self.buzz, state_dir=self.state).run()
        self.assertEqual(second.get("summary_requests"), [])
        self.assertEqual(len(self.buzz.writes), 1)

        for path in self.state.glob("*.outbox.json"):
            ledger = json.loads(path.read_text(encoding="utf-8"))
            ledger["acked"] = []
            self.publisher.sync.atomic_write_json(path, ledger)
        for path in self.state.glob("*.cache.json"):
            path.unlink()
        third = SYNC.Syncer(merged_config, self.gitlab, self.buzz, state_dir=self.state).run()
        self.assertEqual(third.get("summary_requests"), [])
        self.assertEqual(len(self.buzz.writes), 1)

    def test_embedded_identifier_digits_are_not_count_claims(self):
        """L1-GIS-171 h3/v8/utf8 and ref names carry digits that are not counts; free-standing counts still gate."""
        facts = [{
            "object": "push", "event": "pushed", "created_at": "2026-09-13T05:00:00Z",
            "actor": "dev1", "ref": "feature/h3-evidence-push", "title": "feat: h3 evidence push",
            "url": "", "commits": 1,
        }]
        for prose in (
            "buzz-sync-dev 新建分支 feature/h3-evidence-push 并推送了 1 个提交，提交信息为 feat: h3 evidence push。",
            "v8 迁移与 utf8 修正已完成。",
        ):
            with self.subTest(prose=prose[:24]):
                self.publisher.verify_summary_against_facts(prose, facts)
        with self.assertRaises(self.publisher.PublishError):
            self.publisher.verify_summary_against_facts("今天共有 5 项更新。", facts)

    def test_injection_shaped_summary_argv_is_rejected_with_zero_side_effects(self):
        """L1-GIS-161 Shell-metacharacter prose and trailing argv cannot smuggle a second command."""
        digest = self.pending_facts_digest()
        for argv in (
            ["--summary", "正常'; rm -rf /; echo '", "--facts", digest],
            ["--summary", "$(rm -rf /)", "--facts", digest],
            ["--summary", "`reboot`", "--facts", digest],
            ["--summary", "正常。", "--facts", digest, "; reboot"],
            ["--summary", "正常。", "--facts", digest, "&& /bin/sh -c x"],
        ):
            with self.subTest(argv=argv):
                if len(argv) > 4:
                    with mock.patch("sys.stderr", new=io.StringIO()), self.assertRaises(SystemExit):
                        self.publisher.main(argv, env={self.publisher.desk_runner.MANIFEST_ENV: str(self.manifest)})
                else:
                    with mock.patch("sys.stdout", new=io.StringIO()):
                        code = self.publisher.main(
                            argv, env={self.publisher.desk_runner.MANIFEST_ENV: str(self.manifest)},
                        )
                    self.assertEqual(code, 1)
        self.assertEqual(self.buzz.writes, [])
        self.assertEqual(self.ledger()["pending"][0]["status"], "SUMMARIZING")

    def test_reversed_multi_inventory_claim_binds_runner_facts_to_publisher_target(self):
        """L1-GIS-152 Manifest order cannot make prose for one request ACK another request."""
        base_path = next(self.state.glob("*.outbox.json"))
        base_ledger = json.loads(base_path.read_text(encoding="utf-8"))
        base_ledger["pending"][0]["status"] = "PENDING"
        base_ledger["pending"][0]["queued_at"] = "2026-09-13T03:00:00Z"
        self.publisher.sync.atomic_write_json(base_path, base_ledger)

        second_config = json.loads(json.dumps(self.config))
        second_config["gitlab"]["projects"] = [482]
        second_config_path = self.write_json("sync-second.json", second_config)
        second_state = self.root / "sync-state-second"
        older_fact = {
            "object": "push", "event": "pushed", "created_at": "2026-09-13T01:00:00Z",
            "actor": "bob", "ref": "older/ref", "title": "oldest request", "url": "",
            "commits": 1,
        }
        older_payload = {
            "version": 1, "project_id": 482, "visibility": "public",
            "source_keys": ["event-482"], "facts": [older_fact],
        }
        second_syncer = self.publisher.sync.Syncer(
            second_config, None, None, state_dir=second_state,
        )
        second_syncer._queue_delivery("summary_request", older_payload)
        second_path = next(second_state.glob("*.outbox.json"))
        second_ledger = json.loads(second_path.read_text(encoding="utf-8"))
        second_ledger["pending"][0]["queued_at"] = "2026-09-13T01:00:00Z"
        self.publisher.sync.atomic_write_json(second_path, second_ledger)

        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        # Deliberately newest-first: selection must use durable time, not manifest order.
        manifest["sync"] = [
            {"config": str(self.config_path), "state_dir": str(self.state)},
            {"config": str(second_config_path), "state_dir": str(second_state)},
        ]
        self.manifest.write_text(json.dumps(manifest), encoding="utf-8")

        def fake_child(argv, **kwargs):
            return type("Result", (), {"returncode": 0, "stdout": '{"status":"ok"}'})()

        runner_result = self.publisher.desk_runner.run_once(
            manifest_path=self.manifest,
            env={"NH_DESK_GITLAB_TOKEN": "token"},
            run=fake_child,
        )
        self.assertEqual(runner_result["summary_requests"][0]["facts"], [older_fact])

        publish_result = self.execute(
            "较早的分支有一次更新。", facts=self.facts_digest_of(older_payload["facts"]),
        )

        self.assertEqual(publish_result, {"status": "sent"})
        self.assertEqual(self.buzz.writes[-1][1], "较早的分支有一次更新。")
        self.assertEqual(json.loads(base_path.read_text(encoding="utf-8"))["pending"][0]["status"], "PENDING")
        published = json.loads(second_path.read_text(encoding="utf-8"))
        self.assertEqual(published["pending"], [])
        self.assertEqual(published["acked"][-1]["project_id"], 482)


class SummaryLinksTest(unittest.TestCase):
    def setUp(self):
        self.publisher = load("gitlab_buzz_summary_publish_links", SCRIPT)

    @staticmethod
    def fact(obj, url, ref="", title=""):
        return {"object": obj, "event": "pushed", "created_at": "2026-09-17T01:00:00Z", "actor": "alice",
                "ref": ref, "title": title, "url": url, "commits": 1}

    def test_rendered_summary_appends_trusted_fact_urls_after_the_prose(self):
        """L1-GIS-205 the summary keeps link-free prose, then lists each distinct GitLab URL from the bound facts."""
        facts = [
            self.fact("push", "https://gitlab.example/g/p/-/compare/a...b", ref="feature/x"),
            self.fact("push", "https://gitlab.example/g/p/-/compare/a...b", ref="feature/x"),
            self.fact("pipeline", "https://gitlab.example/g/p/-/pipelines/9", ref="main"),
            self.fact("note", "http://gitlab.example/g/p/-/issues/3#note_1", title="Issue (a|b)"),
            self.fact("wiki", "javascript:alert(1)"),
            self.fact("member", ""),
            self.fact("other", "https://gitlab.example/has space"),
        ]
        content = self.publisher._render({"facts": facts, "source_keys": []}, "本次有分支更新。")
        lines = content.split("\n")
        self.assertEqual(lines[0], "本次有分支更新。")
        self.assertEqual(lines[1:], [
            "推送 feature/x https://gitlab.example/g/p/-/compare/a...b",
            "流水线 main https://gitlab.example/g/p/-/pipelines/9",
            "评论 http://gitlab.example/g/p/-/issues/3#note_1",
        ])
        with self.assertRaises(self.publisher.PublishError):
            self.publisher._render({"facts": facts, "source_keys": []}, "看 https://evil.example")

    def test_rendered_summary_caps_the_link_list(self):
        """L1-GIS-205 at most 30 links are listed; the rest are counted, not dropped silently."""
        facts = [self.fact("push", f"https://gitlab.example/g/p/-/commit/{i}", ref="main") for i in range(35)]
        content = self.publisher._render({"facts": facts, "source_keys": []}, "推送 35 条。")
        lines = content.split("\n")
        self.assertEqual(len(lines), 1 + 30 + 1)
        self.assertEqual(lines[-1], "另有 5 个链接未列出")


if __name__ == "__main__":
    unittest.main()
