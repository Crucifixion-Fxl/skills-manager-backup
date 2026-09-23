"""L2-3 owner timer: the timer entrypoint syncs under the Desk identity with no LLM in the path (ADR-0008).

Skipped unless BUZZ_SYNC_L3=1. Needs the localstack up with agents on the current prompts:
  python3 skills/buzz-agent-setup/tests/localstack/stack.py agents --restart desk,role
  BUZZ_SYNC_L3=1 python3 -m unittest skills/buzz-agent-setup/tests/integration/test_desk_e2e.py -v

`stack.py timer-run` plays the systemd --user service: it starts the immutable release's
gitlab_buzz_sync_timer.py once, with no argv and an env -i whitelist of the Desk identity
(relay, Desk key + NIP-OA tag, Desk GitLab token, BUZZ_DESK_RUNNER_MANIFEST). The Desk Agent
stays an ordinary Agent: a Channel message addressed to it can never run the sync.
Do not run concurrently with L2-2 or L3.
"""
from __future__ import annotations

import json
import time
import unittest

try:
    from .e2e_support import (AGENT_TIMEOUT, ENABLED, QUIET, E2ECase, is_route_event,
                              load_sync_module, stack_cli)
    from .test_sync_local import first_line, p_tags, tag_values
except ImportError:  # imported as a top-level module
    from e2e_support import (AGENT_TIMEOUT, ENABLED, QUIET, E2ECase, is_route_event,
                             load_sync_module, stack_cli)
    from test_sync_local import first_line, p_tags, tag_values


@unittest.skipUnless(ENABLED, "L2-3 runs only with BUZZ_SYNC_L3=1, the localstack up and its agents running")
class OwnerTimerE2ETest(E2ECase):
    def desk_chatter(self, events: list[dict]) -> list[dict]:
        """Non-protocol messages signed by the Desk identity (the timer shares it)."""

        return [event for event in self.by(events, self.stack.desk["pubkey"])
                if not first_line(event).startswith("[gitlab-notify:v1]") and not is_route_event(event)]

    def timer_run(self, *, expect_exit: int = 0) -> dict:
        result = stack_cli("timer-run")
        self.assertEqual(result["exit_code"], expect_exit, json.dumps(result)[:400])
        return result["result"]

    def assert_only_protocol_routes(self, events: list[dict]) -> None:
        by_id = {event["id"]: event for event in events}
        for route in [event for event in self.by(events, self.stack.desk["pubkey"]) if is_route_event(event)]:
            parents = [tag[1] for tag in tag_values(route, "e") if len(tag) > 1 and tag[-1] == "reply"]
            self.assertEqual(len(parents), 1, route)
            parent = by_id.get(parents[0])
            if parent is None:
                parent = next((event for event in self.stack.thread(parents[0])
                               if event.get("id") == parents[0]), None)
            self.assertEqual((parent or {}).get("pubkey"), self.stack.desk["pubkey"], route)
            self.assertEqual(p_tags(route), {self.stack.role_pubkey}, route)

    def test_001_timer_run_syncs_under_desk_identity_without_chatter(self):
        """L2-3-GIS-001 timer 入口一次运行：Desk 身份建出 root + binding，频道没有 tick、没有 Desk 非协议消息。"""
        issue = self.stack.create_issue(f"l2-3 timer {self.stamp}")
        result = self.timer_run()
        self.assertIn(result["status"], {"ok", "degraded"})
        root = self.wait_root("issue", issue["iid"])
        self.assert_single_binding("issue", issue["iid"], root)

        time.sleep(QUIET)
        events = self.channel_events()
        self.assertEqual([event for event in events if "gitlab sync" in first_line(event)], [])
        self.assertEqual(self.desk_chatter(events), [])
        self.assert_only_protocol_routes(events)
        self.assertEqual(len(self.stack.roots("issue", issue["iid"], self.started_unix)), 1)

    def test_002_empty_timer_run_writes_zero_channel_messages(self):
        """L2-3-GIS-002 无新事件的一轮 timer 运行：exit 0，频道新增消息为 0。"""
        self.timer_run()
        time.sleep(5)
        before = {event["id"] for event in self.channel_events()}
        result = self.timer_run()
        self.assertEqual(result.get("published"), 0)
        time.sleep(QUIET / 2)
        self.assertEqual([event for event in self.channel_events() if event["id"] not in before], [])

    def test_003_channel_mention_cannot_start_sync(self):
        """L2-3-GIS-003 人工发「@Desk gitlab sync」不会运行同步：普通 Desk 不执行 runner，Issue 没有 root。"""
        issue = self.stack.create_issue(f"l2-3 mention {self.stamp}")
        self.mention_desk()
        time.sleep(AGENT_TIMEOUT / 4)
        self.assertEqual(self.stack.roots("issue", issue["iid"], self.started_unix), [])

    def test_004_failed_run_is_silent_and_redacted(self):
        """L2-3-GIS-004 配置错误时 timer 非零退出；业务频道零消息，stdout 只有脱敏 error。"""
        broken = json.loads(self.sync_config_file.read_text(encoding="utf-8"))
        broken["since"] = "not-a-timestamp"
        self.sync_config_file.write_text(json.dumps(broken), encoding="utf-8")
        self.sync_config_file.chmod(0o600)
        self.addCleanup(stack_cli, "sync-config", "--since", self.since)
        before = {event["id"] for event in self.channel_events()}
        result = self.timer_run(expect_exit=1)
        self.assertEqual(result["status"], "error")
        serialized = json.dumps(result)
        for secret in (self.stack.desk["secret"], *self.stack.tokens.values()):
            self.assertNotIn(secret, serialized)
        time.sleep(QUIET / 2)
        self.assertEqual([event for event in self.channel_events() if event["id"] not in before], [])

    def test_005_first_delivery_failure_stops_without_stalled_facts(self):
        """L2-3-GIS-005 binding 冲突使同步首错停止：timer 非零退出，不发 stalled 事实，Desk 身份无非协议消息。"""
        sync = load_sync_module()
        issue = self.stack.create_issue(f"l2-3 stalled {self.stamp}")
        for root_id in ("1" * 64, "2" * 64):
            binding = {"project_id": self.stack.project_id, "object": "issue", "iid": issue["iid"],
                       "channel_id": self.stack.channel, "root_event_id": root_id}
            self.stack.ok("bot", "POST", f"{self.stack.base}/issues/{issue['iid']}/notes",
                          body={"body": sync.render_binding_note(binding, "buzz://conflict")})
        self.timer_run(expect_exit=1)
        time.sleep(QUIET / 2)
        events = self.channel_events()
        self.assertEqual([event for event in self.by(events, self.stack.desk["pubkey"])
                          if first_line(event).startswith("[gitlab-notify:v1][object:sync][event:stalled]")], [])
        self.assertEqual(self.desk_chatter(events), [])

    def test_006_locked_run_stays_silent(self):
        """L2-3-GIS-006 已有同步持锁时 timer 返回 locked 且 exit 0；频道零新增消息。"""
        self.hold_sync_lock()
        before = {event["id"] for event in self.channel_events()}
        result = self.timer_run()
        self.assertEqual(result["sync"], ["locked"])
        time.sleep(QUIET / 2)
        self.assertEqual([event for event in self.channel_events() if event["id"] not in before], [])


if __name__ == "__main__":
    unittest.main()
