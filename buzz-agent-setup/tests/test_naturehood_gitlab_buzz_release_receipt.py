"""Schema and opt-in live gate for the final GitLab -> Buzz L4 receipt.

Pre-merge CI validates the stable schema only.  A post-merge release run sets
``NATUREHOOD_GITLAB_BUZZ_RELEASE_RECEIPT`` to an external artifact path; live
commit/event identifiers and machine-specific paths are never checked in.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
import json
import os
from pathlib import Path
import re
import unittest


FIXTURES = Path(__file__).resolve().parent / "fixtures"
SCHEMA = FIXTURES / "naturehood-gitlab-buzz-release-receipt.schema.json"
LIVE_RECEIPT = os.environ.get("NATUREHOOD_GITLAB_BUZZ_RELEASE_RECEIPT")
AGENTS = {
    "nh-desk",
    "nh-feature",
    "nh-bug",
    "nh-debt",
    "nh-dev",
    "nh-bi",
    "nh-sre",
    "nh-qa",
}
SHA256 = re.compile(r"^[0-9a-f]{64}$")
GIT_REVISION = re.compile(r"^[0-9a-f]{40}$")
EVENT_ID = SHA256
RELEASE_DIGESTS = {
    "gitlab_buzz_sync_timer.py",
    "gitlab_buzz_desk_runner.py",
    "gitlab_buzz_summary_publish.py",
    "gitlab_buzz_sync.py",
    "gitlab_buzz_route_reply.py",
    "buzz_responsible_mentions.py",
    "buzz_send_with_responsible_mentions.py",
    "runner_manifest",
    "timer_unit",
    "service_unit",
    "launcher",
    "buzz_cli",
}
# Non-secret env names only; the GitLab credential variable is counted, never named, in the receipt.
TIMER_ENV_WHITELIST = {
    "HOME", "USER", "LOGNAME", "PATH", "LANG",
    "BUZZ_RELAY_URL", "BUZZ_PRIVATE_KEY", "BUZZ_AUTH_TAG", "BUZZ_DESK_RUNNER_MANIFEST",
}


class NaturehoodGitLabBuzzReleaseReceiptSchemaTest(unittest.TestCase):
    def test_premerge_schema_keeps_revision_runtime_cutover_and_protocol_evidence_required(self) -> None:
        """L1-GIS-191 Receipt v3 gates release on the owner timer (ADR-0008), not a heartbeat/restricted runtime."""
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertEqual(schema["title"], "Naturehood GitLab Buzz timer-gated L4 receipt")
        self.assertEqual(schema["properties"]["schema_version"]["const"], 3)
        self.assertEqual(
            set(schema["required"]),
            {
                "schema_version",
                "receipt_kind",
                "snapshot_only",
                "contains_secret",
                "recorded_at_utc",
                "release",
                "runtime",
                "naturehood_eval",
                "cutover",
                "verification",
                "protocol_evidence",
            },
        )
        definitions = schema["$defs"]
        self.assertEqual(set(definitions["release"]["required"]), {"skills_revision", "buzz_deploy_revision", "installed_plugin_revision", "release_dir", "sha256"})
        self.assertEqual(set(definitions["release"]["properties"]["sha256"]["required"]), RELEASE_DIGESTS)
        self.assertEqual(set(definitions["runtime"]["properties"]["agents"]["required"]), AGENTS)
        self.assertEqual(set(definitions["runtime"]["properties"]["desk"]["required"]),
                         {"process_started_after_prompt", "prompt_sync_command_count"})
        eval_contract = definitions["naturehood_eval"]
        self.assertEqual(
            set(eval_contract["required"]),
            {
                "started_at_utc",
                "completed_at_utc",
                "timer",
                "runner",
                "output_oracle",
                "failure_oracle",
                "trigger_gate",
            },
        )
        properties = eval_contract["properties"]
        for retired in ("runtime_profile", "heartbeat_trigger", "permission_probe"):
            with self.subTest(retired=retired):
                self.assertNotIn(retired, properties)
        timer = properties["timer"]["properties"]
        self.assertEqual(set(properties["timer"]["required"]), set(timer))
        self.assertEqual(timer["systemd_scope"]["const"], "user")
        self.assertEqual(timer["timer_unit"]["pattern"], "^gitlab-buzz-sync-[a-z0-9][a-z0-9-]*\\.timer$")
        self.assertEqual(timer["service_unit"]["pattern"], "^gitlab-buzz-sync-[a-z0-9][a-z0-9-]*\\.service$")
        self.assertEqual(timer["on_unit_active_sec"]["const"], 300)
        self.assertTrue(timer["persistent"]["const"])
        self.assertFalse(timer["automatic"]["const"])
        self.assertEqual(timer["launcher"]["const"], "env-i-whitelist")
        self.assertEqual(timer["env_whitelist"]["items"]["enum"], sorted(TIMER_ENV_WHITELIST))
        self.assertEqual(timer["gitlab_credential_env_count"]["const"], 1)
        self.assertEqual(timer["inherited_non_whitelisted_env_count"]["const"], 0)
        self.assertEqual(timer["manifest_env"]["const"], "BUZZ_DESK_RUNNER_MANIFEST")
        self.assertTrue(timer["manifest_path_pinned"]["const"])
        self.assertEqual(timer["exec_start"]["pattern"],
                         "^/usr/bin/python3 /.+/scripts/gitlab_buzz_sync_timer\\.py$")
        self.assertEqual(timer["entrypoint_argument_count"]["const"], 0)
        self.assertFalse(timer["llm_in_sync_path"]["const"])
        self.assertEqual(timer["acp_session_count"]["const"], 0)
        self.assertEqual(timer["public_tick_message_count"]["const"], 0)
        runner = properties["runner"]["properties"]
        self.assertTrue(runner["started"]["const"])
        self.assertEqual(runner["exit_code"]["const"], 0)
        self.assertEqual(runner["business_argument_count"]["const"], 0)
        self.assertNotIn("sandbox_setup_error_count", runner)
        output = properties["output_oracle"]["properties"]
        self.assertEqual(output["machine_digest_count"]["const"], 0)
        self.assertEqual(output["readable_summary_count"]["const"], 1)
        self.assertEqual(output["summary_source"]["const"], "template")
        self.assertEqual(output["llm_generated_summary_count"]["const"], 0)
        self.assertTrue(output["summary_publisher_gates_passed"]["const"])
        self.assertEqual(output["public_summary_internal_id_count"]["const"], 0)
        self.assertTrue(output["summary_fact_consistency_verified"]["const"])
        self.assertEqual(output["duplicate_count"]["const"], 0)
        self.assertEqual(output["no_new_event_channel_message_count"]["const"], 0)
        self.assertEqual(output["wrong_thread_reply_count"]["const"], 0)
        self.assertEqual(output["unbound_issue_root_count"]["const"], 1)
        self.assertEqual(output["unbound_mr_root_count"]["const"], 1)
        self.assertEqual(output["top_level_push_aggregate_count"]["const"], 1)
        failure = properties["failure_oracle"]["properties"]
        self.assertEqual(failure["business_channel_message_count"]["const"], 0)
        self.assertTrue(failure["timer_exit_nonzero"]["const"])
        self.assertEqual(failure["journal_failure_record_count"]["const"], 1)
        self.assertTrue(failure["journal_failure_record_redacted"]["const"])
        trigger_gate = properties["trigger_gate"]["properties"]
        self.assertTrue(trigger_gate["public_workflow_schedule_decommissioned"]["const"])
        self.assertFalse(trigger_gate["public_workflow_schedule_enabled"]["const"])
        self.assertFalse(trigger_gate["desk_heartbeat_enabled"]["const"])
        self.assertTrue(trigger_gate["automatic_timer_enabled_after_eval"]["const"])
        self.assertIn("automatic_timer_enabled_at_utc", trigger_gate)
        self.assertEqual(definitions["l4_scenarios"]["minItems"], 27)
        self.assertEqual(definitions["l4_scenarios"]["maxItems"], 27)
        self.assertFalse(definitions["verification"]["properties"]["l4"]["properties"]["live_test_skipped"]["const"])
        self.assertIn("mode_lease_loser_write_count", definitions["protocol_evidence"]["required"])
        self.assertIn("injection_rejections", definitions["protocol_evidence"]["required"])
        self.assertNotIn("old_notifier_timer_enabled", definitions["cutover"]["required"])
        self.assertIn("old_notifier_naturehood_source_enabled", definitions["cutover"]["required"])
        self.assertIn("old_notifier_drain", definitions["cutover"]["required"])
        self.assertTrue(definitions["cutover"]["properties"]["old_notifier_timer_active"]["const"])
        cutover = definitions["cutover"]["properties"]
        self.assertTrue(cutover["sync_timer_enabled"]["const"])
        self.assertEqual(cutover["sync_timer_interval_seconds"]["const"], 300)
        self.assertFalse(cutover["desk_heartbeat_enabled"]["const"])
        serialized = json.dumps(schema)
        for retired in ("heartbeat_prompt", "reject_once", "buzz_acp", "codex_", "permission_requests", "read-only"):
            with self.subTest(retired_anywhere=retired):
                self.assertNotIn(retired, serialized)


@unittest.skipUnless(
    LIVE_RECEIPT,
    "post-merge L4 only: set NATUREHOOD_GITLAB_BUZZ_RELEASE_RECEIPT to an external artifact",
)
class NaturehoodGitLabBuzzReleaseReceiptTest(unittest.TestCase):
    def load_receipt(self) -> dict:
        receipt_path = Path(str(LIVE_RECEIPT))
        self.assertTrue(receipt_path.is_absolute(), "live receipt must use an explicit absolute artifact path")
        self.assertFalse(
            receipt_path.is_relative_to(FIXTURES),
            "live identifiers and runtime paths must remain outside the repository fixtures",
        )
        self.assertTrue(receipt_path.is_file(), "post-merge final L4 receipt artifact is required")
        value = json.loads(receipt_path.read_text(encoding="utf-8"))
        self.assertIsInstance(value, dict)
        return value

    def test_final_receipt_binds_artifacts_runtime_cutover_and_all_27_scenes(self) -> None:
        receipt = self.load_receipt()
        self.assertEqual(receipt.get("schema_version"), 3)
        self.assertEqual(receipt.get("receipt_kind"), "naturehood_gitlab_buzz_release_l4")
        self.assertFalse(receipt.get("snapshot_only"))
        self.assertFalse(receipt.get("contains_secret"))
        self.assertRegex(receipt.get("recorded_at_utc", ""), r"^20\d\d-\d\d-\d\dT\d\d:\d\d:\d\dZ$")

        release = receipt["release"]
        for field in ("skills_revision", "buzz_deploy_revision", "installed_plugin_revision"):
            self.assertRegex(release[field], GIT_REVISION)
        self.assertEqual(release["installed_plugin_revision"], release["skills_revision"])
        self.assertIn(release["skills_revision"], release["release_dir"])
        expected_digests = RELEASE_DIGESTS
        self.assertTrue(expected_digests <= set(release["sha256"]))
        for name in expected_digests:
            self.assertRegex(release["sha256"][name], SHA256)

        runtime = receipt["runtime"]
        self.assertRegex(runtime["channel_id"], r"^[0-9a-f-]{36}$")
        self.assertEqual(set(runtime["agents"]), AGENTS)
        for name, agent in runtime["agents"].items():
            with self.subTest(agent=name):
                self.assertTrue(agent["active"])
                self.assertGreater(agent["main_pid"], 0)
                self.assertGreater(agent["workers"], 0)
                self.assertRegex(agent["started_at_utc"], r"^20\d\d-\d\d-\d\dT")
                self.assertRegex(agent["prompt_sha256"], SHA256)
                self.assertEqual(agent["plugin_revision"], release["skills_revision"])
        self.assertTrue(runtime["desk"]["process_started_after_prompt"])
        self.assertEqual(runtime["desk"]["prompt_sync_command_count"], 0)

        naturehood_eval = receipt["naturehood_eval"]
        started = datetime.fromisoformat(naturehood_eval["started_at_utc"].replace("Z", "+00:00"))
        completed = datetime.fromisoformat(naturehood_eval["completed_at_utc"].replace("Z", "+00:00"))
        timer = naturehood_eval["timer"]
        observed = datetime.fromisoformat(timer["observed_at_utc"].replace("Z", "+00:00"))
        self.assertLess(started, completed)
        self.assertGreaterEqual(observed, started)
        self.assertLessEqual(observed, completed)
        self.assertEqual(timer["systemd_scope"], "user")
        self.assertRegex(timer["timer_unit"], r"^gitlab-buzz-sync-[a-z0-9][a-z0-9-]*\.timer$")
        self.assertEqual(timer["service_unit"], timer["timer_unit"][: -len(".timer")] + ".service")
        self.assertEqual(timer["on_unit_active_sec"], 300)
        self.assertTrue(timer["persistent"])
        self.assertFalse(timer["automatic"])
        self.assertEqual(timer["launcher"], "env-i-whitelist")
        self.assertEqual(set(timer["env_whitelist"]), TIMER_ENV_WHITELIST)
        self.assertIn("BUZZ_DESK_RUNNER_MANIFEST", timer["env_whitelist"])
        self.assertEqual(timer["gitlab_credential_env_count"], 1)
        self.assertEqual(timer["inherited_non_whitelisted_env_count"], 0)
        self.assertEqual(timer["manifest_env"], "BUZZ_DESK_RUNNER_MANIFEST")
        self.assertTrue(timer["manifest_path_pinned"])
        self.assertEqual(timer["manifest_sha256"], release["sha256"]["runner_manifest"])
        self.assertEqual(timer["exec_start"],
                         f"/usr/bin/python3 {release['release_dir']}/scripts/gitlab_buzz_sync_timer.py")
        self.assertEqual(timer["entrypoint_argument_count"], 0)
        self.assertFalse(timer["llm_in_sync_path"])
        self.assertEqual(timer["acp_session_count"], 0)
        self.assertEqual(timer["public_tick_message_count"], 0)
        self.assertTrue(naturehood_eval["runner"]["started"])
        self.assertEqual(naturehood_eval["runner"]["exit_code"], 0)
        self.assertEqual(naturehood_eval["runner"]["business_argument_count"], 0)
        output = naturehood_eval["output_oracle"]
        self.assertEqual(output["machine_digest_count"], 0)
        self.assertEqual(output["readable_summary_count"], 1)
        self.assertRegex(output["readable_summary_event_id"], EVENT_ID)
        self.assertEqual(output["summary_source"], "template")
        self.assertEqual(output["llm_generated_summary_count"], 0)
        self.assertTrue(output["summary_publisher_gates_passed"])
        self.assertEqual(output["public_summary_internal_id_count"], 0)
        self.assertEqual(output["duplicate_count"], 0)
        self.assertTrue(output["summary_fact_consistency_verified"])
        self.assertEqual(output["no_new_event_channel_message_count"], 0)
        self.assertGreaterEqual(output["existing_issue_thread_reply_count"], 1)
        self.assertGreaterEqual(output["existing_mr_thread_reply_count"], 1)
        self.assertEqual(output["wrong_thread_reply_count"], 0)
        self.assertEqual(output["unbound_issue_root_count"], 1)
        self.assertEqual(output["unbound_issue_binding_count"], 1)
        self.assertEqual(output["unbound_mr_root_count"], 1)
        self.assertEqual(output["unbound_mr_binding_count"], 1)
        self.assertEqual(output["top_level_push_aggregate_count"], 1)
        failure = naturehood_eval["failure_oracle"]
        self.assertEqual(failure["injected_failure_count"], 1)
        self.assertEqual(failure["business_channel_message_count"], 0)
        self.assertTrue(failure["timer_exit_nonzero"])
        self.assertEqual(failure["journal_failure_record_count"], 1)
        self.assertTrue(failure["journal_failure_record_redacted"])
        trigger_gate = naturehood_eval["trigger_gate"]
        self.assertTrue(trigger_gate["public_workflow_schedule_decommissioned"])
        self.assertFalse(trigger_gate["public_workflow_schedule_enabled"])
        self.assertTrue(trigger_gate["evaluation_passed"])
        self.assertFalse(trigger_gate["desk_heartbeat_enabled"])
        self.assertTrue(trigger_gate["automatic_timer_enabled_after_eval"])
        timer_enabled_at = datetime.fromisoformat(
            trigger_gate["automatic_timer_enabled_at_utc"].replace("Z", "+00:00")
        )
        self.assertGreaterEqual(timer_enabled_at, completed)

        cutover = receipt["cutover"]
        self.assertTrue(cutover["old_notifier_timer_active"])
        self.assertFalse(cutover["old_notifier_naturehood_source_enabled"])
        self.assertTrue(cutover["old_notifier_drain"]["full_cycle_waited"])
        self.assertRegex(
            cutover["old_notifier_drain"]["last_naturehood_write_at_utc"],
            r"^20\d\d-\d\d-\d\dT",
        )
        self.assertTrue(cutover["public_sync_workflow_decommissioned"])
        self.assertFalse(cutover["public_sync_workflow_enabled"])
        self.assertEqual(cutover["public_visible_tick_message_count"], 0)
        self.assertTrue(cutover["sync_timer_enabled"])
        self.assertEqual(cutover["sync_timer_interval_seconds"], 300)
        self.assertEqual(cutover["sync_timer_unit_sha256"], release["sha256"]["timer_unit"])
        self.assertFalse(cutover["desk_heartbeat_enabled"])
        self.assertEqual(cutover["route_mode"], "desk-local")
        self.assertFalse(cutover["http_fallback_enabled"])

        verification = receipt["verification"]
        for layer in ("l1", "l2", "l3"):
            self.assertEqual(verification[layer]["revision"], release["skills_revision"])
            self.assertEqual(verification[layer]["failed"], 0)
            self.assertGreater(verification[layer]["passed"], 0)
        self.assertEqual(verification["source_audit"]["status"], "passed")
        self.assertEqual(verification["source_audit"]["scene_count"], 27)
        self.assertRegex(verification["source_audit"]["artifact_sha256"], SHA256)
        self.assertFalse(verification["l4"]["live_test_skipped"])

        scenes = verification["l4"]["scenarios"]
        self.assertEqual([scene["id"] for scene in scenes], [f"{number:02d}" for number in range(1, 29) if number != 14])
        self.assertEqual(Counter(scene["outcome"] for scene in scenes), Counter({"passed": 22, "blocked": 5}))
        for scene in scenes:
            with self.subTest(scene=scene["id"]):
                for field in ("title", "status", "destination", "agent", "oracle"):
                    self.assertTrue(scene[field])
                if scene["outcome"] == "passed":
                    self.assertTrue(scene["assertions"])
                    self.assertTrue(scene["event_ids"])
                    for event_id in scene["event_ids"]:
                        self.assertRegex(event_id, EVENT_ID)
                    self.assertNotIn("blocker", scene)
                else:
                    self.assertTrue(scene["blocker"])
                    self.assertEqual(scene["event_ids"], [])

    def test_final_receipt_has_fail_loud_protocol_and_recovery_evidence(self) -> None:
        receipt = self.load_receipt()
        protocol = receipt["protocol_evidence"]
        for field in (
            "root_event_id",
            "binding_event_id",
            "route_event_id",
            "role_reply_event_id",
            "responsible_human_event_id",
        ):
            self.assertRegex(protocol[field], EVENT_ID)
        self.assertTrue(protocol["pending_acked_readback"])
        self.assertTrue(protocol["cursor_retry_same_boundary"])
        self.assertTrue(protocol["overlap_lock_single_writer"])
        self.assertTrue(protocol["mode_lease_one_winner"])
        self.assertEqual(protocol["mode_lease_loser_write_count"], 0)
        self.assertEqual(protocol["injection_rejections"]["tick_write_count"], 0)
        self.assertEqual(protocol["injection_rejections"]["issue_write_count"], 0)
        self.assertEqual(protocol["injection_rejections"]["canvas_write_count"], 0)
        self.assertEqual(protocol["responsible_rejections"]["bot_write_count"], 0)
        self.assertEqual(protocol["responsible_rejections"]["free_text_write_count"], 0)

        serialized = json.dumps(receipt, ensure_ascii=False).lower()
        for forbidden in ("private-token", "nsec1", "gitlab_token", "route_secret", "private_key"):
            self.assertNotIn(forbidden, serialized)

        def assert_no_secret_keys(value: object, path: str = "receipt") -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    if key != "contains_secret":
                        self.assertIsNone(
                            re.search(r"(?:^|_)(?:token|password|secret|private_key)(?:$|_)", key.lower()),
                            f"secret-shaped key in receipt: {path}.{key}",
                        )
                    assert_no_secret_keys(child, f"{path}.{key}")
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    assert_no_secret_keys(child, f"{path}[{index}]")

        assert_no_secret_keys(receipt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
