"""Durable retry pacing and public-message budget at the controller entry point."""
import copy
import hashlib
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import subprocess
import unittest
import uuid
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from recovery_relay import RouteBlocked
import recovery_controller
from recovery_read_budget import SourceReadFailure
import test_recovery_fairness as fixture


class RetryBudgetTests(unittest.TestCase):
    write = fixture.FairnessTests.write
    write_snapshot = fixture.FairnessTests.write_snapshot
    validate_route = fixture.FairnessTests.validate_route
    inventory = fixture.FairnessTests.inventory
    tick = fixture.FairnessTests.tick

    def setUp(self):
        fixture.FairnessTests.setUp(self)
        self.clock = mock.patch("time.time", return_value=1000).start()
        self.jitter = mock.patch("random.uniform", return_value=1.0).start()
        self.addCleanup(mock.patch.stopall)

    def tearDown(self):
        fixture.FairnessTests.tearDown(self)

    def unavailable(self, channel, root, agent):
        self.visits.append((agent, channel, root))
        raise TimeoutError("private endpoint must never reach public status")

    def test_backoff_survives_reopen_and_preserves_original_pending_work(self):
        self.inventory(1, 1)
        self.relay.validate_route = self.unavailable
        old = Path(self.config["agents"][0]["journal_dir"]) / (str(uuid.UUID(int=98)) + ".json")
        original = old.read_bytes()
        first = self.tick()["agents"]["fair-0"]
        self.assertEqual(first["errors"], ["recovery_delivery_unverified"])
        self.assertEqual(len(self.visits), 1)
        self.clock.return_value = 1004
        waiting = self.tick()["agents"]["fair-0"]
        self.assertEqual(self.visits, [], "reopened controller ignored retry deadline")
        self.assertEqual(waiting["retry_pending"], 1)
        self.assertEqual(waiting["retry_reasons"], ["recovery_delivery_unverified"])
        self.assertEqual(waiting["next_retry_at"], 1005)
        self.assertNotIn("private", json.dumps(waiting))
        self.assertEqual(old.read_bytes(), original)
        self.clock.return_value = 1005
        self.tick()
        self.assertEqual(len(self.visits), 1)

    def test_delay_doubles_to_five_minutes_and_does_not_grow_unbounded(self):
        self.inventory(1, 1)
        self.relay.validate_route = self.unavailable
        for delay in (5, 10, 20, 40, 80, 160, 300, 300, 300):
            now = self.clock.return_value
            row = self.tick()["agents"]["fair-0"]
            self.assertEqual(len(self.visits), 1)
            self.assertEqual(row.get("next_retry_at"), now + delay)
            self.clock.return_value = now + delay - 0.01
            self.tick()
            self.assertEqual(self.visits, [], "network retried before backoff elapsed")
            self.clock.return_value = now + delay

    def test_jitter_is_bounded_but_not_deterministically_synchronized(self):
        self.inventory(1, 1)
        self.relay.validate_route = self.unavailable
        deadlines = []
        for index, factor in enumerate((0.8, 1.2)):
            directory = self.base / f"jitter-{index}"
            directory.mkdir(mode=0o700)
            self.config["state_dir"] = str(directory)
            self.jitter.return_value = factor
            row = self.tick()["agents"]["fair-0"]
            deadlines.append(row.get("next_retry_at"))
        self.assertEqual(deadlines, [1005, 1006])
        self.jitter.assert_called_with(0.8, 1.2)

    def test_success_clears_backoff_and_lost_ack_reuses_exact_event(self):
        self.inventory(1, 1)
        original_publish = self.relay.publish
        lose_once = [True]
        def publish(event):
            if event["content"].endswith(" continue") and lose_once[0]:
                self.relay.lose_ack = True
                lose_once[0] = False
            original_publish(event)
        self.relay.publish = publish
        first = self.tick()["agents"]["fair-0"]
        self.assertEqual(first["errors"], ["recovery_delivery_unverified"])
        deliveries = [e["id"] for e in self.relay.events.values() if e["content"].endswith(" continue")]
        self.assertEqual(len(deliveries), 1, "failed ACK fixture never reached the Relay")
        posts = list(self.relay.published)
        self.clock.return_value = 1001
        self.tick()
        self.assertEqual(self.visits, [])
        self.clock.return_value = 1005
        recovered = self.tick()["agents"]["fair-0"]
        self.assertEqual(recovered["continued"], 1)
        self.assertEqual(recovered["retry_pending"], 0)
        self.assertEqual(recovered["retry_reasons"], [])
        self.assertEqual(self.relay.published, posts, "reconciliation re-signed or reposted accepted delivery")
        self.assertEqual([e["id"] for e in self.relay.events.values() if e["content"].endswith(" continue")], deliveries)

    def test_one_failed_route_does_not_throttle_other_routes_or_agents(self):
        self.inventory(2, 4)
        denied = self.config["agents"][0]["pubkey"]
        def validate(channel, root, agent):
            self.visits.append((agent, channel, root))
            if agent == denied:
                raise TimeoutError("offline")
            return "owner-only"
        self.relay.validate_route = validate
        first = self.tick()
        self.assertEqual(first["agents"]["fair-1"]["continued"], 2)
        second = self.tick()
        self.assertEqual(second["agents"]["fair-1"]["continued"], 2)
        third = self.tick()
        self.assertFalse(any(visit[0] == denied for visit in self.visits))
        self.assertEqual(third["agents"]["fair-0"]["retry_pending"], 4)

    def test_known_policy_denial_is_not_mistaken_for_network_failure(self):
        self.inventory(1, 1)
        def denied(*args):
            self.visits.append(args)
            raise RouteBlocked("agent_not_bot", "请 owner 恢复机器人角色。", notice_allowed=True)
        self.relay.validate_route = denied
        self.tick()
        self.relay.validate_route = self.validate_route
        row = self.tick()["agents"]["fair-0"]
        self.assertEqual(row["continued"], 1)
        self.assertEqual(row["errors"], [])

    def test_complete_read_timeout_has_durable_backoff_and_one_visible_notice(self):
        self.inventory(1, 1)
        original_source = self.relay.validate_source
        def timeout(*_args):
            raise SourceReadFailure("source_read_timeout")
        self.relay.validate_source = timeout
        first = self.tick()["agents"]["fair-0"]
        self.assertEqual(first["continued"], 0)
        self.assertEqual(first["retry_reasons"], ["source_read_timeout"])
        self.assertEqual(first["next_retry_at"], 1005)
        notices = list(self.relay.events.values())
        self.assertEqual(len(notices), 1)
        self.assertIn("核验", notices[0]["content"])
        self.assertFalse(any(t[0] == "p" for t in notices[0]["tags"]))
        self.clock.return_value = 1001
        waiting = self.tick()["agents"]["fair-0"]
        self.assertEqual(self.visits, [])
        self.assertEqual(waiting["retry_pending"], 1)
        self.clock.return_value = 1005
        self.tick()
        self.assertEqual(len(self.relay.events), 1, "retry flooded the Thread with timeout notices")
        self.relay.validate_source = original_source
        self.clock.return_value = 1015
        recovered = self.tick()["agents"]["fair-0"]
        self.assertEqual(recovered["continued"], 1)
        self.assertEqual(recovered["retry_pending"], 0)

    def test_cli_reports_waiting_retry_as_nonzero_with_reason_and_deadline(self):
        self.inventory(1, 1)
        self.relay.validate_route = self.unavailable
        self.tick()
        config_path = self.base / "controller.json"
        self.write(config_path, json.dumps(self.config))
        self.clock.return_value = 1001
        output = io.StringIO()
        process = subprocess.CompletedProcess([], 0, str(self.children[0].pid), "")
        # main() goes through the real systemd_main_pid, which needs XDG_RUNTIME_DIR
        # (a plain container has no /run/user/<euid> logind session).
        xdg_runtime = self.base / "xdg-runtime"
        xdg_runtime.mkdir(mode=0o700)
        with mock.patch.object(recovery_controller, "RecoveryRelay", return_value=self.relay), \
             mock.patch.object(recovery_controller.subprocess, "run", return_value=process), \
             mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(xdg_runtime)}), \
             contextlib.redirect_stdout(output):
            self.assertEqual(recovery_controller.main(["--config", str(config_path)]), 1)
        row = json.loads(output.getvalue())["agents"]["fair-0"]
        self.assertEqual(row["scheduled"], 0)
        self.assertEqual(row["retry_reasons"], ["recovery_delivery_unverified"])
        self.assertEqual(row["next_retry_at"], 1005)

    def test_notice_budget_eventually_reports_every_denial_without_losing_allowed_work(self):
        self.inventory(1, 1)
        path = Path(self.config["agents"][0]["journal_dir"]) / (str(uuid.UUID(int=98)) + ".json")
        old = json.loads(path.read_text())
        turn = next(iter(old["active"]))
        prototype = copy.deepcopy(next(iter(old["input_sources"].values()))[0])
        denied = {hashlib.sha256(f"denied-source-{n}".encode()).hexdigest() for n in range(17)}
        for work in denied:
            old["receipts"][work] = "active"
            old["triggers"][turn].append(work)
            old["input_sources"][work] = [dict(prototype, event_id=work)]
        self.write(path, json.dumps(old))
        def source(value, agent, policy):
            if value["event_id"] in denied:
                raise RouteBlocked("source_not_member", "请 owner 核对原请求人的群权限。", notice_allowed=True)
            return value["signed_author"]
        self.relay.validate_source = source
        for index in range(3):
            before = set(self.relay.events)
            row = self.tick()["agents"]["fair-0"]
            notices = [self.relay.events[key] for key in set(self.relay.events) - before
                       if not self.relay.events[key]["content"].endswith(" continue")]
            self.assertLessEqual(len(notices), 8, "one route flooded the Channel")
            self.assertTrue(all(not any(t[0] == "p" for t in e["tags"]) for e in notices))
            if index < 2:
                self.assertEqual(row["continued"], 0, "work started before its startup notice could be sent")
                self.assertIn("recovery_notice_budget_exhausted", row["errors"])
            else:
                self.assertEqual(row["continued"], 1)
        events = list(self.relay.events.values())
        self.assertEqual(sum("原请求" in e["content"] for e in events), 17)
        self.assertEqual(sum(e["content"].endswith(" continue") for e in events), 1)
        before = list(self.relay.published)
        self.tick()
        self.assertEqual(self.relay.published, before)


if __name__ == "__main__":
    unittest.main()
