from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "hostd_probes"))
import check_feishu_events as checker


START = 1_800_000_000.0
END = START + 40
RUN = "run-186-s0d-001"
CHAT = "oc-test-chat"
BOTS = {
    "hostd-test-desk": "cli-desk",
    "hostd-test-a": "cli-a",
    "hostd-test-b": "cli-b",
}
REACTION_ACTOR_HASH = hashlib.sha256(b"user-union\0on-actor").hexdigest()
SDK_AVAILABLE = importlib.util.find_spec("lark_oapi") is not None


def manifest():
    return {
        "schema_version": 2,
        "run_id": RUN,
        "window": {"start": START, "end": END},
        "target": {"chat_id": CHAT, "apps": BOTS},
        "required_events": [
            {"scenario": "P0-1", "bot": bot, "app_id": app, "type": "im.message.receive_v1",
             "event_id": f"event-{bot}", "chat_id": CHAT,
             "message_id": "om-probe-message"}
            for bot, app in BOTS.items()
        ] + [{
            "scenario": "P0-3", "bot": "hostd-test-a", "app_id": BOTS["hostd-test-a"],
            "type": "card.action.trigger", "event_id": "event-card",
            "chat_id": CHAT, "message_id": "om-card-message",
            "card_id": "card-186", "action_id": "action-approve-186", "probe_marker": "p0-3-roundtrip", "operator_has_union_id": True,
        }, {
            "scenario": "P0-4", "bot": "hostd-test-b", "app_id": BOTS["hostd-test-b"],
            "type": "im.chat.member.bot.added_v1", "event_id": "event-added",
            "chat_id": CHAT, "target_app_id": BOTS["hostd-test-b"],
        }] + [{
            "scenario": "P0-5", "bot": bot, "app_id": app,
            "type": "im.message.reaction.created_v1", "event_id": f"event-reaction-{bot}",
            "chat_id": CHAT, "reaction_operation_id": "reaction-186", "message_id": "om-reaction-message",
            "reaction_type": "THUMBSUP", "actor_id_hash": REACTION_ACTOR_HASH, "actor_namespace": "user-union", "operator_type": "user", "action_time": "2026-10-04T16:00:00Z",
        } for bot, app in BOTS.items()] + [{
            "scenario": "P0-6", "bot": "hostd-test-desk", "app_id": BOTS["hostd-test-desk"],
            "type": "im.message.receive_v1", "event_id": "event-mention",
            "chat_id": CHAT, "message_id": "om-mention-message", "mentioned_type": "bot", "mention_id": "ou-test-b",
        }],
        "isolation": {
            "driver": {"driver_id": "isolated-test-driver", "readback_id": "readback-b-001",
                       "receipt_path": "/tmp/hostd-test-driver-receipt.json"},
            "fault_id": "fault-hostd-test-b-001",
            "fault_bot": "hostd-test-b",
            "survivor_bots": ["hostd-test-desk", "hostd-test-a"],
            "post_fault_message_id": "om-after-fault",
        },
    }


def valid_rows():
    driver_hash = with_driver_hash(manifest())["isolation"]["driver"]["receipt_sha256"]
    rows = []
    for index, (bot, app) in enumerate(BOTS.items()):
        rows.append({"schema_version": 2, "run_id": RUN, "t_recv": START + 1 + index,
                     "bot": bot, "app_id": app, "type": "_connection_started",
                     "connection_id": f"conn-{bot}", "session_id": f"session-{bot}"})
        rows.append({"schema_version": 2, "run_id": RUN, "t_recv": START + 10 + index,
                     "bot": bot, "app_id": app, "type": "im.message.receive_v1",
                     "event_id": f"event-{bot}", "chat_id": CHAT,
                     "message_id": "om-probe-message", "create_time": int((START + 10 + index) * 1000)})
    rows.extend([{ "schema_version": 2, "run_id": RUN, "t_recv": START + 16.5,
                   "bot": bot, "app_id": app, "type": "im.message.receive_v1",
                   "event_id": f"association-{bot}", "chat_id": CHAT, "message_id": "om-reaction-message"}
                 for bot, app in BOTS.items()])
    rows.extend([
        {"schema_version": 2, "run_id": RUN, "t_recv": START + 15,
         "bot": "hostd-test-a", "app_id": BOTS["hostd-test-a"],
         "type": "card.action.trigger", "event_id": "event-card", "chat_id": CHAT,
         "message_id": "om-card-message", "card_id": "card-186", "action_id": "action-approve-186", "probe_marker": "p0-3-roundtrip", "operator_has_union_id": True},
        {"schema_version": 2, "run_id": RUN, "t_recv": START + 16,
         "bot": "hostd-test-b", "app_id": BOTS["hostd-test-b"], "type": "im.chat.member.bot.added_v1",
         "event_id": "event-added", "chat_id": CHAT, "target_app_id": BOTS["hostd-test-b"]},
        *[{"schema_version": 2, "run_id": RUN, "t_recv": START + 17 + i,
           "bot": bot, "app_id": app, "type": "im.message.reaction.created_v1",
           "event_id": f"event-reaction-{bot}", "chat_id": CHAT, "reaction_type": "THUMBSUP",
           "actor_id_hash": REACTION_ACTOR_HASH, "actor_namespace": "user-union", "operator_type": "user", "action_time": "2026-10-04T16:00:00Z",
           "message_id": "om-reaction-message"} for i, (bot, app) in enumerate(BOTS.items())],
        {"schema_version": 2, "run_id": RUN, "t_recv": START + 19,
         "bot": "hostd-test-desk", "app_id": BOTS["hostd-test-desk"], "type": "im.message.receive_v1",
         "event_id": "event-mention", "chat_id": CHAT, "message_id": "om-mention-message",
         "mentions": [{"id": "ou-test-b", "mentioned_type": "bot"}]},
        {"schema_version": 2, "run_id": RUN, "t_recv": START + 20,
         "bot": "hostd-test-b", "app_id": BOTS["hostd-test-b"],
         "type": "_fault_injected", "fault_id": "fault-hostd-test-b-001", "driver_id": "isolated-test-driver",
         "receipt_sha256": driver_hash},
        {"schema_version": 2, "run_id": RUN, "t_recv": START + 20.5,
         "bot": "hostd-test-b", "app_id": BOTS["hostd-test-b"],
         "type": "_fault_driver_readback", "fault_id": "fault-hostd-test-b-001", "driver_id": "isolated-test-driver",
         "readback_id": "readback-b-001", "receipt_sha256": driver_hash, "result": "failed"},
        {"schema_version": 2, "run_id": RUN, "t_recv": START + 21,
         "bot": "hostd-test-b", "app_id": BOTS["hostd-test-b"],
         "type": "_app_failed", "fault_id": "fault-hostd-test-b-001", "driver_id": "isolated-test-driver",
         "readback_id": "readback-b-001", "receipt_sha256": driver_hash},
    ])
    for bot in ("hostd-test-desk", "hostd-test-a"):
        rows.append({"schema_version": 2, "run_id": RUN, "t_recv": START + 22,
                     "bot": bot, "app_id": BOTS[bot], "type": "im.message.receive_v1",
                     "event_id": f"event-after-{bot}", "chat_id": CHAT,
                     "message_id": "om-after-fault"})
    for row in rows:
        if row.get("type") not in ("_connection_started", "_connection_closed") and row.get("bot") in BOTS:
            row["session_id"] = f"session-{row['bot']}"
    return rows


def driver_receipt_bytes(doc):
    driver = doc["isolation"]["driver"]
    receipt = {"schema_version": 1, "run_id": RUN, "fault_id": doc["isolation"]["fault_id"],
               "driver_id": driver["driver_id"], "readback_id": driver["readback_id"], "result": "failed"}
    return json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode()


def with_driver_hash(doc):
    doc = json.loads(json.dumps(doc))
    doc["isolation"]["driver"]["receipt_sha256"] = hashlib.sha256(driver_receipt_bytes(doc)).hexdigest()
    return doc


def verdict(rows=None, doc=None, driver_bytes=None):
    doc = with_driver_hash(doc or manifest())
    return checker.evaluate(doc, rows if rows is not None else valid_rows(),
                            driver_bytes if driver_bytes is not None else driver_receipt_bytes(doc))


class ProbeEvidenceTests(unittest.TestCase):
    def test_exact_run_window_and_targeted_correlations_pass(self):
        receipt = verdict()
        self.assertIs(receipt["ok"], True)
        self.assertEqual(receipt["run_id"], RUN)
        self.assertEqual(receipt["target_chat_id"], CHAT)
        card_evidence = next(item for item in receipt["evidence"] if item.get("event_id") == "event-card")
        self.assertEqual(card_evidence["probe_marker"], "p0-3-roundtrip")
        self.assertIs(receipt["checks"]["simultaneous_app_connections"], True)
        self.assertIs(receipt["checks"]["fault_isolated_to_one_app"], True)
        for check in ("P0-1_every_declared_app_received_exact_message", "P0-1_receive_lag_under_2s",
                      "P0-3_exact_card_action_callback", "P0-4_only_added_bot_received_event",
                      "P0-5_same_reaction_seen_by_every_app", "P0-6_message_names_bot_mention"):
            self.assertIs(receipt["checks"][check], True, check)


    def test_missing_or_legacy_manifest_fails_clearly(self):
        for doc in ({}, {"probe": "hostd-feishu-events", "ok": True}):
            try:
                checker.evaluate(doc, valid_rows())
            except checker.EvidenceError as exc:
                assert "schema_version 2" in str(exc)
            else:
                raise AssertionError("legacy or missing manifest must fail closed")


    def test_stale_global_rows_cannot_satisfy_current_run(self):
        rows = valid_rows()
        for row in rows:
            row["run_id"] = "previous-run"
        assert verdict(rows)["ok"] is False


    def test_wrong_chat_does_not_satisfy_required_event(self):
        rows = valid_rows()
        next(r for r in rows if r.get("event_id") == "event-hostd-test-a")["chat_id"] = "oc-other"
        receipt = verdict(rows)
        self.assertIs(receipt["ok"], False)
        self.assertIn("remedy", receipt["guidance"])
        self.assertIn("copy_to_ai", receipt["guidance"])

    def test_wrong_message_does_not_satisfy_required_event(self):
        rows = valid_rows()
        next(r for r in rows if r.get("event_id") == "event-hostd-test-a")["message_id"] = "om-similar"
        assert verdict(rows)["ok"] is False


    def test_wrong_card_or_action_does_not_satisfy_card_callback(self):
        for key, value in (("card_id", "card-other"), ("action_id", "action-other")):
            rows = valid_rows()
            next(r for r in rows if r.get("event_id") == "event-card")[key] = value
            assert verdict(rows)["ok"] is False

    def test_reaction_actor_namespace_must_match(self):
        rows = valid_rows()
        next(r for r in rows if r.get("event_id") == "event-reaction-hostd-test-a")["actor_namespace"] = "bot-app"
        with self.assertRaises(checker.EvidenceError):
            verdict(rows)


    def test_wrong_app_id_does_not_satisfy_required_event(self):
        rows = valid_rows()
        next(r for r in rows if r.get("event_id") == "event-hostd-test-a")["app_id"] = "cli-other"
        assert verdict(rows)["ok"] is False


    def test_event_outside_manifest_window_does_not_satisfy_required_event(self):
        rows = valid_rows()
        next(r for r in rows if r.get("event_id") == "event-hostd-test-a")["t_recv"] = END + 1
        assert verdict(rows)["ok"] is False


    def test_app_connections_must_overlap_in_time(self):
        rows = valid_rows()
        for row in rows:
            if row.get("type") == "_connection_started" and row.get("bot") == "hostd-test-b":
                row["t_recv"] = END - 1
            if row.get("type") == "_connection_started" and row.get("bot") in ("hostd-test-desk", "hostd-test-a"):
                    rows.append({"schema_version": 2, "run_id": RUN, "t_recv": START + 30,
                             "bot": row["bot"], "app_id": row["app_id"], "type": "_connection_closed",
                             "connection_id": row["connection_id"], "session_id": row["session_id"]})
        assert verdict(rows)["ok"] is False


    def test_fault_isolation_requires_named_fault_and_survivor_delivery_afterward(self):
        rows = valid_rows()
        next(r for r in rows if r.get("type") == "_app_failed")["fault_id"] = "other-fault"
        assert verdict(rows)["ok"] is False
        rows = valid_rows()
        rows = [r for r in rows if r.get("message_id") != "om-after-fault"]
        assert verdict(rows)["ok"] is False


    def test_survivor_event_from_another_connection_session_does_not_prove_isolation(self):
        rows = valid_rows()
        next(r for r in rows if r.get("message_id") == "om-after-fault" and r.get("bot") == "hostd-test-a")["session_id"] = "unrelated-session"
        self.assertIs(verdict(rows)["checks"]["fault_isolated_to_one_app"], False)

    def test_connection_closed_before_required_event_fails_simultaneous_evidence(self):
        rows = valid_rows()
        start = next(r for r in rows if r.get("type") == "_connection_started" and r.get("bot") == "hostd-test-a")
        rows.append({"schema_version": 2, "run_id": RUN, "t_recv": START + 9, "bot": start["bot"],
                     "app_id": start["app_id"], "type": "_connection_closed", "session_id": start["session_id"]})
        self.assertIs(verdict(rows)["checks"]["simultaneous_app_connections"], False)

    def test_fault_driver_readback_must_match_declared_artifact(self):
        rows = valid_rows()
        next(r for r in rows if r.get("type") == "_fault_driver_readback")["readback_id"] = "unrelated-readback"
        self.assertIs(verdict(rows)["checks"]["fault_isolated_to_one_app"], False)

    def test_fault_receipt_bytes_must_match_manifest_digest_and_ids(self):
        doc = with_driver_hash(manifest())
        bad = driver_receipt_bytes(doc).replace(b"readback-b-001", b"other-readback")
        with self.assertRaisesRegex(checker.EvidenceError, "fault driver receipt"):
            verdict(doc=doc, driver_bytes=bad)

    def test_latency_over_two_seconds_fails(self):
        rows = valid_rows()
        next(r for r in rows if r.get("event_id") == "event-hostd-test-a")["create_time"] = int(START * 1000)
        receipt = verdict(rows)
        self.assertIs(receipt["checks"]["P0-1_receive_lag_under_2s"], False)
        self.assertIs(receipt["ok"], False)

    def test_missing_exact_operation_identifier_fails_manifest_validation(self):
        doc = manifest()
        doc["required_events"][0].pop("event_id")
        with self.assertRaisesRegex(checker.EvidenceError, "exact event_id"):
            verdict(doc=doc)

    def test_malformed_row_fails_clearly(self):
        with self.assertRaisesRegex(checker.EvidenceError, "malformed record"):
            verdict([*valid_rows(), "not-an-event"])
        with self.assertRaisesRegex(checker.EvidenceError, "unknown fields"):
            verdict([{**valid_rows()[0], "payload": "ignored?"}])

    def test_malformed_nested_manifest_fails_clearly(self):
        doc = manifest()
        doc["window"] = []
        with self.assertRaisesRegex(checker.EvidenceError, "window and target"):
            verdict(doc=doc)

    def test_recorder_accepts_only_pre_run_scope_fields(self):
        doc = {key: value for key, value in manifest().items() if key in {"schema_version", "run_id", "window", "target"}}
        run_id, chat_id, apps = checker.validate_recorder_manifest(doc)
        self.assertEqual(run_id, RUN)
        self.assertEqual(chat_id, CHAT)
        self.assertEqual(apps, BOTS)
        with self.assertRaises(checker.EvidenceError):
            checker.evaluate(doc, valid_rows(), driver_receipt_bytes(with_driver_hash(manifest())))

    def test_unknown_manifest_fields_fail_closed(self):
        doc = manifest()
        doc["legacy_ok"] = True
        with self.assertRaisesRegex(checker.EvidenceError, "unknown fields"):
            verdict(doc=doc)

    def test_recorder_sanitizes_message_and_card_bodies_but_keeps_ids(self):
        try:
            import event_recorder
            import lark_oapi as lark
            from lark_oapi.api.im.v1.model.p2_im_message_receive_v1 import P2ImMessageReceiveV1
            from lark_oapi.api.im.v1.model.p2_im_message_reaction_created_v1 import P2ImMessageReactionCreatedV1
            from lark_oapi.api.im.v1.model.p2_im_chat_member_bot_added_v1 import P2ImChatMemberBotAddedV1
            from lark_oapi.event.callback.model.p2_card_action_trigger import P2CardActionTrigger
        except ImportError:
            self.skipTest("lark-oapi is not installed")
        def marshal(model, payload):
            return json.loads(lark.JSON.marshal(model(payload)))
        message = event_recorder.safe_event("im.message.receive_v1", marshal(P2ImMessageReceiveV1, {
            "header": {"event_id": "event-safe"},
            "event": {"message": {"chat_id": CHAT, "message_id": "om-safe", "create_time": 1800000000000,
                                   "content": "private body", "mentions": [{"id": {"open_id": "ou-target"}, "mentioned_type": "bot"}]}}}))
        reaction = event_recorder.safe_event("im.message.reaction.created_v1", marshal(P2ImMessageReactionCreatedV1, {
            "header": {"event_id": "event-reaction"}, "event": {"message_id": "om-safe", "reaction_type": {"emoji_type": "THUMBSUP"},
                "operator_type": "user", "user_id": {"union_id": "on-actor", "open_id": "ou-actor"}, "action_time": "2026-10-04T16:00:00Z"}}))
        added = event_recorder.safe_event("im.chat.member.bot.added_v1", marshal(P2ImChatMemberBotAddedV1, {
            "header": {"event_id": "event-added"}, "event": {"chat_id": CHAT, "name": "test bot"}}))
        card = event_recorder.safe_event("card.action.trigger", marshal(P2CardActionTrigger, {
            "event": {"context": {"open_message_id": "om-card", "open_chat_id": CHAT},
                      "operator": {"union_id": "union-sensitive"},
                      "action": {"value": {"action_id": "action-safe", "card_id": "card-safe", "probe": "marker", "text": "secret body"}}}}))
        self.assertEqual(message["event_id"], "event-safe")
        self.assertEqual(message["message_id"], "om-safe")
        self.assertNotIn("private body", str(message))
        self.assertEqual(card["card_id"], "card-safe")
        self.assertEqual(card["action_id"], "action-safe")
        self.assertNotIn("secret body", str(card))
        self.assertTrue(card["operator_has_union_id"])
        self.assertNotIn("union-sensitive", str(card))
        self.assertEqual(reaction["message_id"], "om-safe")
        self.assertEqual(reaction["reaction_type"], "THUMBSUP")
        self.assertEqual(reaction["actor_namespace"], "user-union")
        self.assertNotIn("actor_app_id", reaction)
        self.assertEqual(reaction["actor_id_hash"], hashlib.sha256(b"user-union\0on-actor").hexdigest())
        self.assertEqual(reaction["action_time"], "2026-10-04T16:00:00Z")
        self.assertNotIn("ou-actor", str(reaction))
        self.assertNotIn("target_app_id", added)  # recipient app is attached by recorder from trusted connection config

    def test_receipt_keeps_identifiers_and_provenance_but_no_message_body_or_secret(self):
        receipt = verdict()
        encoded = str(receipt)
        assert "private message text" not in encoded
        assert "secret-value" not in encoded
        assert "om-probe-message" in encoded
        assert RUN in encoded

    def test_cli_failure_metadata_is_structured_and_redacted(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        import feishu_creds
        payload = {"code": 99991672, "error": {"message": "secret raw server detail", "console_url": "https://open.feishu.cn/page/scope-apply?clientID=cli_test&addons=x", "missing_scopes": ["im:chat:readonly", "secret scope text"]}}
        result = SimpleNamespace(returncode=1, stdout=json.dumps(payload), stderr="private stack secret")
        with patch.object(feishu_creds.subprocess, "run", return_value=result) as run:
            reply = feishu_creds.lark("hostd-test-b", "api", "GET", "/open-apis/calendar/v4/calendars")
        self.assertEqual(reply["error"]["code"], 99991672)
        self.assertEqual(reply["error"]["missing_scopes"], ["im:chat:readonly"])
        self.assertTrue(reply["error"]["console_url"].startswith("https://open.feishu.cn/page/scope-apply?"))
        self.assertNotIn("secret raw server detail", str(reply))
        self.assertNotIn("private stack secret", str(reply))
        self.assertEqual(run.call_args.args[0][:2], [feishu_creds.NODE, feishu_creds.CLI_ENTRY])
        self.assertIn("--as", run.call_args.args[0])
        self.assertNotIn("--profile", run.call_args.args[0])

    def test_cli_checker_smoke_uses_shared_safe_reader_and_returns_receipt(self):
        doc = with_driver_hash(manifest())
        raw_driver = driver_receipt_bytes(doc)
        with tempfile.TemporaryDirectory() as temp_dir:
            os.chmod(temp_dir, 0o700)
            root = Path(temp_dir)
            manifest_path, events_path = root / "manifest.json", root / "events.jsonl"
            driver_path, output_path = root / "driver.json", root / "receipt.json"
            doc["isolation"]["driver"]["receipt_path"] = str(driver_path)
            doc = with_driver_hash(doc)
            raw_driver = driver_receipt_bytes(doc)
            payloads = {
                manifest_path: json.dumps(doc).encode(),
                events_path: ("\n".join(json.dumps(row) for row in valid_rows()) + "\n").encode(),
                driver_path: raw_driver,
            }
            for path, payload in payloads.items():
                path.write_bytes(payload)
                path.chmod(0o600)
            command = [sys.executable, str(Path(checker.__file__)), "--manifest", str(manifest_path),
                       "--events", str(events_path), "--driver-receipt", str(driver_path), "--output", str(output_path)]
            result = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            saved = json.loads(output_path.read_text())
            self.assertIs(saved["ok"], True)
            self.assertEqual(saved["run_id"], RUN)

    def test_cli_legacy_manifest_exits_with_redacted_remedy_not_traceback(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "legacy.json"
            path.write_text(json.dumps({"probe": "hostd-feishu-events", "ok": True}))
            path.chmod(0o600)
            result = subprocess.run([sys.executable, str(Path(checker.__file__)), "--manifest", str(path)],
                                    capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 2)
            self.assertIn("Copy to AI:", result.stderr)
            self.assertNotIn("Traceback", result.stderr)

    def test_cli_zero_exit_api_error_is_normalized_for_existing_callers(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        import feishu_creds
        payload = {"ok": False, "code": 99991672, "error": "raw server detail", "console_url": "https://open.feishu.cn/page/scope-apply?clientID=cli_test", "missing_scopes": ["im:chat:readonly"]}
        result = SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")
        with patch.object(feishu_creds.subprocess, "run", return_value=result):
            reply = feishu_creds.lark("hostd-test-b", "api", "GET", "/open-apis/calendar/v4/calendars")
        self.assertIsInstance(reply["error"], dict)
        self.assertEqual(reply["error"]["code"], 99991672)
        self.assertEqual(reply["error"]["missing_scopes"], ["im:chat:readonly"])
        self.assertNotIn("raw server detail", str(reply))


class ProbeRegressionTests(unittest.TestCase):
    def test_every_p0_1_app_requires_its_own_latency(self):
        rows = valid_rows()
        next(r for r in rows if r.get("event_id") == "event-hostd-test-a").pop("create_time")
        self.assertFalse(verdict(rows)["ok"])

    def test_future_creation_time_cannot_prove_latency(self):
        rows = valid_rows()
        for row in rows:
            if "create_time" in row:
                row["create_time"] = int((START + 10000) * 1000)
        self.assertFalse(verdict(rows)["ok"])

    def test_nonfinite_and_boolean_latency_are_rejected(self):
        for value in (float("nan"), float("inf"), True, "NaN"):
            with self.subTest(value=str(value)):
                rows = valid_rows()
                next(r for r in rows if r.get("event_id") == "event-hostd-test-a")["create_time"] = value
                try:
                    receipt = verdict(rows)
                except checker.EvidenceError:
                    continue
                self.assertFalse(receipt["ok"])

    def test_survivor_delivery_must_precede_session_close(self):
        rows = valid_rows()
        rows.append({"schema_version": 2, "run_id": RUN, "t_recv": START + 20,
                     "bot": "hostd-test-a", "app_id": BOTS["hostd-test-a"],
                     "type": "_connection_closed", "session_id": "session-hostd-test-a"})
        self.assertFalse(verdict(rows)["checks"]["fault_isolated_to_one_app"])

    def test_nested_identifiers_and_unknown_mention_keys_are_rejected(self):
        for extra in ({"id": {"secret": "do-not-copy"}, "mentioned_type": "user"},
                      {"id": "ou-other", "mentioned_type": "user", "body": "do-not-copy"}):
            rows = valid_rows()
            next(r for r in rows if r.get("event_id") == "event-mention")["mentions"].append(extra)
            with self.assertRaises(checker.EvidenceError):
                verdict(rows)
        rows = valid_rows()
        next(r for r in rows if r.get("event_id") == "event-card")["probe_marker"] = {"secret": "do-not-copy"}
        with self.assertRaises(checker.EvidenceError):
            verdict(rows)

    def test_missing_card_marker_does_not_raise_or_invent_card_id(self):
        import event_recorder
        projection = event_recorder.safe_event("card.action.trigger", {
            "event": {"context": {"open_chat_id": CHAT, "open_message_id": "om-card"},
                      "action": {"value": {"probe": "p"}}}})
        self.assertNotIn("card_id", projection)

    def test_user_union_identity_is_stable_across_receiving_apps(self):
        import event_recorder
        hashes = []
        for app, open_id in (("cli-a", "ou-app-a"), ("cli-b", "ou-app-b")):
            projection = event_recorder.safe_event("im.message.reaction.created_v1", {
                "header": {"app_id": app}, "event": {"message_id": "om", "operator_type": "user",
                    "user_id": {"union_id": "on-user", "open_id": open_id},
                    "reaction_type": {"emoji_type": "THUMBSUP"}, "action_time": "1800000000"}})
            self.assertEqual(projection["actor_namespace"], "user-union")
            self.assertNotIn("actor_app_id", projection)
            hashes.append(projection["actor_id_hash"])
        self.assertEqual(hashes, [hashlib.sha256(b"user-union\0on-user").hexdigest()] * 2)

    def test_open_id_without_union_cannot_be_canonical_cross_app_actor(self):
        import event_recorder
        projection = event_recorder.safe_event("im.message.reaction.created_v1", {
            "event": {"message_id": "om", "operator_type": "user", "app_id": "cli-a",
                      "user_id": {"open_id": "ou-local"}}})
        self.assertNotIn("actor_id_hash", projection)

    def test_bot_app_identity_does_not_require_user_id(self):
        import event_recorder
        projection = event_recorder.safe_event("im.message.reaction.created_v1", {
            "event": {"message_id": "om", "operator_type": "app", "app_id": "cli-actor"}})
        self.assertEqual(projection["actor_namespace"], "bot-app")
        self.assertEqual(projection["actor_app_id"], "cli-actor")
        self.assertEqual(projection["actor_id_hash"], hashlib.sha256(b"bot-app\0cli-actor").hexdigest())

    @unittest.skipUnless(SDK_AVAILABLE, "requires the installed Feishu SDK")
    def test_actual_sdk_reaction_recording_reaches_full_checker(self):
        import event_recorder
        from unittest.mock import patch
        from lark_oapi.api.im.v1.model.p2_im_message_receive_v1 import P2ImMessageReceiveV1
        from lark_oapi.api.im.v1.model.p2_im_message_reaction_created_v1 import P2ImMessageReactionCreatedV1
        rows = [r for r in valid_rows() if r.get("type") != "im.message.reaction.created_v1" and not str(r.get("event_id", "")).startswith("association-")]
        doc = manifest()
        actor_hash = hashlib.sha256(b"user-union\0on-canonical-user").hexdigest()
        for expected in doc["required_events"]:
            if expected["type"] == "im.message.reaction.created_v1":
                expected.pop("actor_app_id", None)
                expected.update(actor_namespace="user-union", actor_id_hash=actor_hash)
        for i, (bot, app) in enumerate(BOTS.items()):
            state = {"session_id": f"session-{bot}"}
            receive = P2ImMessageReceiveV1({"header": {"event_id": f"association-{bot}"},
                "event": {"message": {"chat_id": CHAT, "message_id": "om-reaction-message"}}})
            reaction = P2ImMessageReactionCreatedV1({"header": {"event_id": f"event-reaction-{bot}"},
                "event": {"message_id": "om-reaction-message", "reaction_type": {"emoji_type": "THUMBSUP"},
                          "operator_type": "user", "user_id": {"union_id": "on-canonical-user", "open_id": f"ou-{i}"},
                          "action_time": "2026-10-04T16:00:00Z"}})
            with patch.object(event_recorder, "_write", side_effect=lambda path, row: rows.append(row)):
                with patch.object(event_recorder.time, "time", return_value=START + 16.5):
                    event_recorder.record(Path("unused"), RUN, bot, app, state, "im.message.receive_v1", receive)
                with patch.object(event_recorder.time, "time", return_value=START + 17 + i):
                    event_recorder.record(Path("unused"), RUN, bot, app, state, "im.message.reaction.created_v1", reaction)
        # The SDK contains no chat_id on reactions. Association must use the recorded message receipt.
        self.assertTrue(all("chat_id" not in r for r in rows if r.get("type") == "im.message.reaction.created_v1"))
        receipt = verdict(rows, doc)
        self.assertTrue(receipt["ok"], receipt["failed_checks"])
        self.assertTrue(all(e["chat_id"] == CHAT for e in receipt["evidence"] if e["type"] == "im.message.reaction.created_v1"))
        for corruption in ("missing", "other-session", "wrong-chat", "too-late", "before-start", "conflicting-chat"):
            altered = json.loads(json.dumps(rows))
            for row in altered:
                if row.get("event_id") == "association-hostd-test-a":
                    if corruption == "missing": row["message_id"] = "om-unrelated"
                    if corruption == "other-session": row["session_id"] = "unrelated"
                    if corruption == "wrong-chat": row["chat_id"] = "oc-other"
                    if corruption == "too-late": row["t_recv"] = START + 35
                    if corruption == "before-start": row["t_recv"] = START + 0.5
            if corruption == "conflicting-chat":
                duplicate = dict(next(r for r in altered if r.get("event_id") == "association-hostd-test-a"))
                duplicate.update(event_id="association-conflict", chat_id="oc-other")
                altered.append(duplicate)
            self.assertFalse(verdict(altered, doc)["ok"], corruption)

    def test_output_writes_reject_symlink_file_and_all_ancestors(self):
        import event_recorder
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            private = root / "private"
            private.mkdir(mode=0o700)
            nested = private / "nested"
            nested.mkdir(mode=0o700)
            target = nested / "target"
            target.write_text("original")
            target.chmod(0o600)
            alias = root / "alias"
            alias.symlink_to(private, target_is_directory=True)
            output_link = nested / "output.json"
            output_link.symlink_to(target)
            for writer in (lambda p: checker._write_receipt({"ok": True}, p),
                           lambda p: event_recorder._write(p, {"run_id": "fake"})):
                for output in (output_link, alias / "nested" / "events.json"):
                    with self.subTest(output=str(output)), self.assertRaises((OSError, ValueError)):
                        writer(output)
            self.assertEqual(target.read_text(), "original")
            self.assertFalse((nested / "events.json").exists())

    def test_recorder_rejects_malformed_nested_callback_without_copying_it(self):
        import event_recorder
        for payload in ({"event": []}, {"event": {"action": {"value": {"card_id": {"secret": "x"}}}}},
                        {"event": {"action": {"value": {"card_id": "card"}}, "operator": {"union_id": {"secret": "x"}}}},
                        {"event": {"message": {"mentions": [{"id": {"open_id": {"secret": "x"}}}]}}}):
            with self.assertRaises(checker.EvidenceError):
                event_recorder.safe_event("card.action.trigger" if "action" in (payload["event"] or {}) else "im.message.receive_v1", payload)

    def test_reaction_cannot_claim_chat_without_received_message_association(self):
        rows = [r for r in valid_rows() if not str(r.get("event_id", "")).startswith("association-")]
        self.assertFalse(verdict(rows)["ok"])

    def test_canonical_actor_namespace_rejects_inconsistent_actor_app(self):
        rows = valid_rows()
        next(r for r in rows if r.get("event_id") == "event-reaction-hostd-test-a")["actor_app_id"] = "cli-unrelated"
        with self.assertRaises(checker.EvidenceError):
            verdict(rows)

    def test_malformed_recorded_callback_leaves_redacted_failure_marker(self):
        import event_recorder
        from unittest.mock import patch
        rows = valid_rows()
        with patch.object(event_recorder, "_write", side_effect=lambda path, row: rows.append(row)), \
             patch.object(event_recorder.time, "time", return_value=START + 19), \
             patch.object(event_recorder, "_marshal_data", return_value='{"event":{"message":{"message_id":{"secret":"do-not-copy"}}}}'):
            event_recorder.record(Path("unused"), RUN, "hostd-test-a", BOTS["hostd-test-a"],
                                  {"session_id": "session-hostd-test-a"}, "im.message.receive_v1", object())
        self.assertEqual(rows[-1]["type"], "_event_rejected")
        self.assertNotIn("do-not-copy", str(rows[-1]))
        self.assertFalse(verdict(rows)["ok"])

    def test_private_append_and_atomic_manifest_finalize(self):
        import event_recorder
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            events = root / "new" / "events.jsonl"
            event_recorder._write(events, {"run_id": "first"})
            event_recorder._write(events, {"run_id": "second"})
            self.assertEqual([json.loads(line)["run_id"] for line in events.read_text().splitlines()], ["first", "second"])
            self.assertEqual(events.stat().st_mode & 0o777, 0o600)
            manifest_path = events.parent / "manifest.json"
            manifest_path.write_text(json.dumps(manifest()))
            manifest_path.chmod(0o600)
            with patch.object(event_recorder.time, "time", return_value=START + 50):
                event_recorder.finalize_manifest(manifest_path)
            self.assertEqual(json.loads(manifest_path.read_text())["window"]["end"], START + 50)
            self.assertEqual(manifest_path.stat().st_mode & 0o777, 0o600)
            alias = root / "alias"
            alias.symlink_to(events.parent, target_is_directory=True)
            with self.assertRaises((OSError, ValueError)):
                event_recorder.finalize_manifest(alias / "manifest.json")
            linked = events.parent / "hardlinked.jsonl"
            os.link(events, linked)
            with self.assertRaises(checker.EvidenceError):
                event_recorder._write(events, {"run_id": "third"})
            self.assertEqual(len(events.read_text().splitlines()), 2)

    def test_cli_actual_nested_stderr_error_and_readable_card_success(self):
        import feishu_creds
        from unittest.mock import patch
        from types import SimpleNamespace
        error = {"ok": False, "identity": "bot", "error": {"type": "api", "code": 99991672,
                 "message": "raw sensitive server detail", "console_url": "https://open.feishu.cn/page/scope-apply?clientID=cli-test",
                 "missing_scopes": ["calendar:calendar:readonly"]}}
        success = {"ok": True, "identity": "bot", "data": {"items": [{"message_id": "om-card",
                    "msg_type": "interactive", "body": {"content": '{"elements":[{"tag":"markdown","content":"visible probe marker"}]}'}}]}}
        with patch.object(feishu_creds.subprocess, "run", return_value=SimpleNamespace(returncode=1, stdout="", stderr=json.dumps(error))):
            result = feishu_creds.lark("hostd-test-a", "api", "GET", "/open-apis/calendar/v4/calendars")
        self.assertEqual(result["error"]["code"], 99991672)
        self.assertEqual(result["error"]["missing_scopes"], ["calendar:calendar:readonly"])
        self.assertNotIn("raw sensitive server detail", str(result))
        with patch.object(feishu_creds.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout=json.dumps(success), stderr="")):
            result = feishu_creds.lark("hostd-test-a", "api", "GET", "/open-apis/im/v1/messages/om-card")
        self.assertEqual(result, success)

    def test_cli_malformed_array_output_fails_without_exception_or_raw_echo(self):
        import feishu_creds
        from unittest.mock import patch
        from types import SimpleNamespace
        with patch.object(feishu_creds.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout='[{"secret":"do-not-copy"}]', stderr="")):
            result = feishu_creds.lark("hostd-test-a", "api", "GET", "/open-apis/im/v1/messages/om-card")
        self.assertIs(result["ok"], False)
        self.assertNotIn("do-not-copy", str(result))

    @unittest.skipUnless(SDK_AVAILABLE, "requires the installed Feishu SDK")
    def test_actual_sdk_bot_reaction_projection_satisfies_canonical_contract(self):
        import event_recorder
        import lark_oapi as lark
        from lark_oapi.api.im.v1.model.p2_im_message_reaction_created_v1 import P2ImMessageReactionCreatedV1
        rows, doc = valid_rows(), manifest()
        actor_hash = hashlib.sha256(b"bot-app\0cli-actor").hexdigest()
        for expected in doc["required_events"]:
            if expected["type"] == "im.message.reaction.created_v1":
                expected.update(actor_namespace="bot-app", actor_id_hash=actor_hash, operator_type="app", actor_app_id="cli-actor")
        for row in rows:
            if row.get("type") == "im.message.reaction.created_v1":
                payload = {"header": {"event_id": row["event_id"]}, "event": {
                    "message_id": row["message_id"], "reaction_type": {"emoji_type": row["reaction_type"]},
                    "operator_type": "app", "app_id": "cli-actor", "action_time": row["action_time"]}}
                projection = event_recorder.safe_event("im.message.reaction.created_v1", json.loads(lark.JSON.marshal(P2ImMessageReactionCreatedV1(payload))))
                for key in ("chat_id", "reaction_type", "operator_type", "actor_id_hash", "actor_namespace", "actor_app_id", "action_time"):
                    row.pop(key, None)
                row.update(projection)
        self.assertTrue(verdict(rows, doc)["ok"])

    def test_pure_projection_imports_without_site_packages(self):
        code = "import event_recorder as r; assert r.safe_event('card.action.trigger', {'event': {'context': {'open_message_id': 'om-test'}}})['message_id'] == 'om-test'"
        env = dict(os.environ)
        env["PYTHONPATH"] = str(Path(__file__).resolve().parent / "hostd_probes")
        result = subprocess.run([sys.executable, "-S", "-c", code], env=env,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
