"""TDD contract for relay-0.2.1 local routing and the optional HTTP adapter."""
from __future__ import annotations

from collections import Counter
from contextlib import redirect_stdout
import importlib.util
import hashlib
import io
import json
from pathlib import Path
import re
import socket
import stat
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
import unittest
from unittest import mock


SKILL = Path(__file__).resolve().parents[1]
SCRIPT = SKILL / "scripts" / "gitlab_buzz_route_reply.py"
TEMPLATE = SKILL / "references" / "workflows" / "route-type-status-fallback.yaml"
EXAMPLE = SKILL / "references" / "scripts" / "gitlab-buzz-route-reply.example.json"
LOCAL_EXAMPLE = SKILL / "references" / "scripts" / "gitlab-buzz-route-writer.example.json"
REFERENCE = SKILL / "references" / "gitlab-buzz-sync.md"
SCRIPTS_README = SKILL / "references" / "scripts" / "README.md"


def load_module():
    spec = importlib.util.spec_from_file_location("gitlab_buzz_route_reply_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ROUTE = load_module()
CHANNEL = "11111111-2222-3333-4444-555555555555"
OTHER_CHANNEL = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
BRIDGE_PRIVATE_KEY = "1" * 64
DESK = ROUTE.sync.publisher_pubkey_from_private_key(BRIDGE_PRIVATE_KEY)
PRIVATE_KEY = "2" * 64
SENDER = ROUTE.sync.publisher_pubkey_from_private_key(PRIVATE_KEY)
ROOT = "3" * 64
TRIGGER = "4" * 64
SENT = "5" * 64
ROLE_PUBKEY = "9" * 64
CANVAS_ADMIN_PRIVATE = "6" * 64
CANVAS_ADMIN = ROUTE.sync.publisher_pubkey_from_private_key(CANVAS_ADMIN_PRIVATE)
ROUTE_ID = "feature-ready"
CHANNEL_RULE = {
    "publisher_pubkey": DESK,
    "canvas_admin_pubkeys": [CANVAS_ADMIN],
    "roles": {
        "feature": {
            "mention": "@feature-agent",
            "mention_pubkey": ROLE_PUBKEY,
        }
    },
}
ROUTE_PREFIX = (
    "[gitlab-notify:v1][object:issue][type:feature][status:ready]"
    "[state:opened][change:routing]"
)
CANVAS_TEXT = f"""# Channel

<!-- gitlab-buzz-routing:v1 -->
| route_id | trigger_prefix | role | reason |
| --- | --- | --- | --- |
| {ROUTE_ID} | `{ROUTE_PREFIX}` | `feature` | `feature+ready` |
<!-- /gitlab-buzz-routing -->
"""
ROUTE_RULE = ROUTE.parse_canvas_routes(CANVAS_TEXT, CHANNEL_RULE["roles"])[ROUTE_ID]
HEADER = (
    "[gitlab-notify:v1][object:issue][type:feature][status:ready]"
    "[state:opened][change:routing][project:481][issue:182]"
)


def event(event_id, pubkey, content, *, channel=CHANNEL, reply_to=None, kind=9, created_at=100,
          sig=None, private_key=None):
    tags = [["h", channel]]
    if reply_to is not None:
        tags.append(["e", reply_to, "", "reply"])
    value = {
        "id": event_id,
        "pubkey": pubkey,
        "kind": kind,
        "content": content,
        "tags": tags,
        "created_at": created_at,
    }
    if private_key is not None:
        if ROUTE.sync.publisher_pubkey_from_private_key(private_key) != pubkey:
            raise AssertionError("private key does not match event pubkey")
        canonical = json.dumps(
            [0, pubkey, created_at, kind, tags, content], ensure_ascii=False, separators=(",", ":")
        ).encode()
        digest = hashlib.sha256(canonical).digest()
        value["id"] = digest.hex()
        value["sig"] = ROUTE.sync.nk.schnorr_sign(
            digest, bytes.fromhex(private_key), b"\x00" * 32
        ).hex()
    elif sig is not None:
        value["sig"] = sig
    return value


def canvas_event(*, private_key=CANVAS_ADMIN_PRIVATE, content=CANVAS_TEXT, created_at=90):
    pubkey = ROUTE.sync.publisher_pubkey_from_private_key(private_key)
    tags = [["h", CHANNEL]]
    canonical = json.dumps(
        [0, pubkey, created_at, 40100, tags, content], ensure_ascii=False, separators=(",", ":")
    ).encode()
    digest = hashlib.sha256(canonical).digest()
    return {
        "id": digest.hex(), "pubkey": pubkey, "kind": 40100, "content": content,
        "tags": tags, "created_at": created_at,
        "sig": ROUTE.sync.nk.schnorr_sign(
            digest, bytes.fromhex(private_key), b"\x00" * 32
        ).hex(),
    }


CANVAS_EVENT = canvas_event()["id"]


def payload(**overrides):
    value = {"channel_id": CHANNEL, "message_id": TRIGGER, "route_id": ROUTE_ID}
    value.update(overrides)
    return value


class FakeBuzz:
    def __init__(self, events, *, canvases=None, publish_on_send=True, fail_after_send=False, sender=SENDER):
        self.events = list(events)
        self.canvases = [canvas_event()] if canvases is None else list(canvases)
        self.sent = []
        self.publish_on_send = publish_on_send
        self.fail_after_send = fail_after_send
        self.sender = sender

    def thread(self, channel_id, message_id):
        self.thread_args = (channel_id, message_id)
        return list(self.events)

    def canvas_events(self, channel_id):
        self.canvas_args = channel_id
        return list(self.canvases)

    def channel_messages(self, channel_id, since_unix):
        self.scan_args = (channel_id, since_unix)
        return [item for item in self.events if item.get("created_at", 0) >= since_unix]

    def send(self, channel_id, content, reply_to, mention_pubkey):
        self.sent.append((channel_id, content, reply_to, mention_pubkey))
        if self.fail_after_send:
            raise ROUTE.RouteError("ambiguous Buzz send failure")
        if self.publish_on_send:
            sent = event(SENT, self.sender, content, channel=channel_id, reply_to=reply_to)
            sent["tags"].append(["p", mention_pubkey])
            self.events.append(sent)
        return SENT

    def verify(self, channel_id, event_id, content, reply_to, mention_pubkey):
        candidate = next((item for item in self.events if item.get("id") == event_id), None)
        if candidate is None:
            raise ROUTE.RouteError("Buzz route reply could not be read back")
        ROUTE.verify_route_event(candidate, self.sender, channel_id, event_id, content, reply_to, mention_pubkey)


class RequestContractTest(unittest.TestCase):
    def test_strict_request_accepts_only_safe_policy_values(self):
        """L1-GIS-056 The endpoint accepts only the three server-bounded route selectors."""
        self.assertEqual(ROUTE.parse_request(payload()), payload())

        invalid = [
            payload(extra="no"),
            payload(channel_id=OTHER_CHANNEL.upper()),
            payload(message_id="short"),
            payload(route_id="Feature Ready"),
            payload(route_id="executor!"),
        ]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ROUTE.RequestError):
                ROUTE.parse_request(value)

    def test_runtime_config_pins_cli_and_keeps_http_secret_out_of_buzz_env(self):
        """L1-GIS-056 Runtime config is strict, pins the CLI, and uses a non-BUZZ secret variable."""
        with tempfile.TemporaryDirectory() as tmp:
            cli = Path(tmp) / "buzz-0.5.23" / "buzz"
            cli.parent.mkdir()
            cli.write_bytes(b"\x7fELFtest")
            cli.chmod(0o700)
            config = {
                "secret_env": "GITLAB_BUZZ_ROUTE_SECRET",
                "sender_pubkey": SENDER,
                "channels": {CHANNEL: CHANNEL_RULE},
                "buzz": {"cli_path": str(cli), "cli_sha256": hashlib.sha256(cli.read_bytes()).hexdigest()},
            }
            self.assertEqual(ROUTE.validate_config(config), config)

            local = {**config, "scan_since": "1970-01-01T00:01:00Z", "sender_pubkey": DESK}
            local.pop("secret_env")
            self.assertEqual(ROUTE.validate_config(local, mode="scan"), local)
            config_path = Path(tmp) / "route.json"
            config_path.write_text(json.dumps(local), encoding="utf-8")
            config_path.chmod(0o600)
            self.assertEqual(ROUTE.load_config(config_path, mode="scan"), local)
            config_path.chmod(0o644)
            with self.assertRaisesRegex(ROUTE.ConfigError, "owner-only"):
                ROUTE.load_config(config_path, mode="scan")
            config_path.chmod(0o600)
            linked_config = Path(tmp) / "linked-route.json"
            linked_config.symlink_to(config_path)
            with self.assertRaisesRegex(ROUTE.ConfigError, "symlink"):
                ROUTE.load_config(linked_config, mode="scan")
            with self.assertRaisesRegex(ROUTE.ConfigError, "secret_env"):
                ROUTE.validate_config(local, mode="http")
            with self.assertRaisesRegex(ROUTE.ConfigError, "scan_since"):
                ROUTE.validate_config(config, mode="scan")

            for broken in [
                {**config, "extra": True},
                {**config, "secret_env": "BUZZ_ROUTE_SECRET"},
                {**config, "channels": {OTHER_CHANNEL.upper(): CHANNEL_RULE}},
                {**config, "channels": {CHANNEL: {**CHANNEL_RULE, "extra": True}}},
                {**config, "channels": {CHANNEL: {**CHANNEL_RULE, "publisher_pubkey": "bad"}}},
                {**config, "channels": {CHANNEL: {**CHANNEL_RULE, "canvas_admin_pubkeys": []}}},
                {**config, "channels": {CHANNEL: {**CHANNEL_RULE, "roles": {}}}},
                {**config, "channels": {CHANNEL: {**CHANNEL_RULE, "roles": {
                    "executor": {"mention": "@nh-sre-executor", "mention_pubkey": "8" * 64}
                }}}},
                {**config, "buzz": {**config["buzz"], "cli_sha256": "0" * 64}},
            ]:
                with self.subTest(broken=broken), self.assertRaises(ROUTE.ConfigError):
                    ROUTE.validate_config(broken)

            duplicate_prefix = CANVAS_TEXT.replace(
                "<!-- /gitlab-buzz-routing -->",
                f"| feature-ready-copy | `{ROUTE_PREFIX}` | `feature` | `feature+ready` |\n"
                "<!-- /gitlab-buzz-routing -->",
            )
            with self.assertRaisesRegex(ROUTE.RouteError, "trigger_prefix"):
                ROUTE.parse_canvas_routes(duplicate_prefix, CHANNEL_RULE["roles"])

    def test_local_sender_is_publisher_but_policy_identities_stay_distinct(self):
        """L1-GIS-123 Local Desk authors facts/routes; admins and Roles remain separate identities."""
        with tempfile.TemporaryDirectory() as tmp:
            cli = Path(tmp) / "buzz-0.5.23" / "buzz"
            cli.parent.mkdir()
            cli.write_bytes(b"\x7fELFtest")
            cli.chmod(0o700)
            config = {
                "scan_since": "1970-01-01T00:01:00Z",
                "sender_pubkey": DESK,
                "channels": {CHANNEL: CHANNEL_RULE},
                "buzz": {"cli_path": str(cli), "cli_sha256": hashlib.sha256(cli.read_bytes()).hexdigest()},
            }
            broken = (
                {**config, "sender_pubkey": SENDER},
                {**config, "channels": {CHANNEL: {
                    **CHANNEL_RULE, "canvas_admin_pubkeys": [DESK]
                }}},
                {**config, "channels": {CHANNEL: {
                    **CHANNEL_RULE, "canvas_admin_pubkeys": [ROLE_PUBKEY]
                }}},
            )
            for value in broken:
                with self.subTest(value=value), self.assertRaises(ROUTE.ConfigError):
                    ROUTE.validate_config(value, mode="scan")

            http = {**config, "secret_env": "GITLAB_BUZZ_ROUTE_SECRET", "sender_pubkey": SENDER}
            http.pop("scan_since")
            self.assertEqual(ROUTE.validate_config(http, mode="http"), http)
            with self.assertRaisesRegex(ROUTE.ConfigError, "HTTP sender and publisher"):
                ROUTE.validate_config({**http, "sender_pubkey": DESK}, mode="http")

    def test_one_process_accepts_exactly_one_channel_bearer_scope(self):
        """L1-GIS-093 A bearer cannot authenticate routes for a second Channel in the same process."""
        with tempfile.TemporaryDirectory() as tmp:
            cli = Path(tmp) / "buzz-0.5.23" / "buzz"
            cli.parent.mkdir()
            cli.write_bytes(b"\x7fELFtest")
            cli.chmod(0o700)
            config = {
                "secret_env": "GITLAB_BUZZ_ROUTE_SECRET",
                "sender_pubkey": SENDER,
                "channels": {CHANNEL: CHANNEL_RULE, OTHER_CHANNEL: CHANNEL_RULE},
                "buzz": {"cli_path": str(cli), "cli_sha256": hashlib.sha256(cli.read_bytes()).hexdigest()},
            }

            with self.assertRaisesRegex(ROUTE.ConfigError, "exactly one channel"):
                ROUTE.validate_config(config)

    def test_mr_route_requires_the_reviewable_transition_prefix(self):
        """L1-GIS-070 Fallback MR route 只接受 Bridge 明确标记的 reviewable transition。"""
        with tempfile.TemporaryDirectory() as tmp:
            cli = Path(tmp) / "buzz-0.5.23" / "buzz"
            cli.parent.mkdir()
            cli.write_bytes(b"\x7fELFtest")
            cli.chmod(0o700)
            mr_prefix = (
                "[gitlab-notify:v1][object:mr][state:opened][draft:no][change:lifecycle]"
                "[transition:reviewable]"
            )
            roles = {"review": {"mention": "@review-agent", "mention_pubkey": ROLE_PUBKEY}}
            canvas = CANVAS_TEXT.replace(
                f"| {ROUTE_ID} | `{ROUTE_PREFIX}` | `feature` | `feature+ready` |",
                f"| mr-review | `{mr_prefix}` | `review` | `mr+reviewable` |",
            )
            self.assertIn("mr-review", ROUTE.parse_canvas_routes(canvas, roles))
            invalid = canvas.replace("transition:reviewable", "transition:none")
            with self.assertRaises(ROUTE.RouteError):
                ROUTE.parse_canvas_routes(invalid, roles)


class RouteReplyServiceTest(unittest.TestCase):
    def service(self, events, *, state_dir=None, **buzz_kwargs):
        if state_dir is None:
            temporary = tempfile.TemporaryDirectory()
            self.addCleanup(temporary.cleanup)
            state_dir = Path(temporary.name) / "route-state"
        buzz = FakeBuzz(events, **buzz_kwargs)
        service = ROUTE.RouteReplyService({CHANNEL: CHANNEL_RULE}, SENDER, buzz, state_dir=state_dir)
        return service, buzz

    def test_state_directory_must_be_owner_only_and_not_a_symlink(self):
        """L1-GIS-097 Durable idempotency state cannot be redirected or read by another OS principal."""
        with tempfile.TemporaryDirectory() as tmp:
            broad = Path(tmp) / "broad"
            broad.mkdir(mode=0o755)
            with self.assertRaisesRegex(ROUTE.ConfigError, "owner-only real directory"):
                self.service([], state_dir=broad)

            private = Path(tmp) / "private"
            private.mkdir(mode=0o700)
            linked = Path(tmp) / "linked"
            linked.symlink_to(private, target_is_directory=True)
            with self.assertRaisesRegex(ROUTE.ConfigError, "owner-only real directory"):
                self.service([], state_dir=linked)

    def test_top_level_and_threaded_desk_events_resolve_to_canonical_root(self):
        """L1-GIS-057 A verified Bridge root routes to itself; a verified Bridge reply routes to its one root tag."""
        cases = [
            ([event(TRIGGER, DESK, HEADER)], TRIGGER),
            ([event(ROOT, DESK, HEADER), event(TRIGGER, DESK, HEADER, reply_to=ROOT)], ROOT),
        ]
        for events, expected_root in cases:
            with self.subTest(root=expected_root):
                service, buzz = self.service(events)
                result = service.route(payload())

                self.assertEqual(result, {"status": "sent", "event_id": SENT, "root_event_id": expected_root})
                self.assertEqual(len(buzz.sent), 1)
                channel, content, reply_to, mention_pubkey = buzz.sent[0]
                self.assertEqual((channel, reply_to), (CHANNEL, expected_root))
                self.assertEqual(mention_pubkey, ROLE_PUBKEY)
                self.assertTrue(content.startswith("@feature-agent "))
                self.assertIn("feature+ready", content)
                self.assertIn(f"[gitlab-route:v2][source:{TRIGGER}]", content)
                self.assertIn(f"[policy:{ROUTE_RULE['policy_sha256']}]", content)

    def test_forged_or_ambiguous_trigger_fails_closed_without_send(self):
        """L1-GIS-058 Wrong author/channel/kind/header/root or unknown channel never produces a Buzz write."""
        invalid = [
            ([event(TRIGGER, SENDER, HEADER)], payload()),
            ([event(TRIGGER, DESK, HEADER, channel=OTHER_CHANNEL)], payload()),
            ([event(TRIGGER, DESK, HEADER, kind=40008)], payload()),
            ([event(TRIGGER, DESK, "forged")], payload()),
            ([event(ROOT, DESK, HEADER), {**event(TRIGGER, DESK, HEADER),
                                          "tags": [["h", CHANNEL], ["e", ROOT, "", "reply"],
                                                   ["e", "6" * 64, "", "reply"]]}], payload()),
            ([event(TRIGGER, DESK, HEADER)], payload(channel_id=OTHER_CHANNEL)),
        ]
        for events, request in invalid:
            with self.subTest(events=events, request=request):
                service, buzz = self.service(events)
                with self.assertRaises(ROUTE.RouteError):
                    service.route(request)
                self.assertEqual(buzz.sent, [])

    def test_existing_sender_marker_makes_retry_idempotent(self):
        """L1-GIS-059 A retry finds the sender-authored exact marker in the canonical Thread and does not resend."""
        routed = ROUTE.render_route_message(payload(), ROUTE_RULE)
        sent = event(SENT, SENDER, routed, reply_to=ROOT)
        sent["tags"].append(["p", ROLE_PUBKEY])
        events = [
            event(ROOT, DESK, HEADER),
            event(TRIGGER, DESK, HEADER, reply_to=ROOT),
            sent,
        ]
        service, buzz = self.service(events)

        self.assertEqual(service.route(payload()),
                         {"status": "duplicate", "event_id": SENT, "root_event_id": ROOT})
        self.assertEqual(buzz.sent, [])

    def test_accepted_but_temporarily_invisible_send_is_never_repeated_and_recovers(self):
        """L1-GIS-097 Accepted writes stay PENDING until readback; retries recover them without a second send."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        state_dir = Path(temporary.name) / "route-state"
        source_events = [event(ROOT, DESK, HEADER), event(TRIGGER, DESK, HEADER, reply_to=ROOT)]
        service, buzz = self.service(source_events, state_dir=state_dir, publish_on_send=False)

        with self.assertRaisesRegex(ROUTE.RouteError, "read back"):
            service.route(payload())
        self.assertEqual(len(buzz.sent), 1)

        restarted, retry_buzz = self.service(source_events, state_dir=state_dir, publish_on_send=False)
        with self.assertRaisesRegex(ROUTE.RouteError, "still pending"):
            restarted.route(payload())
        self.assertEqual(retry_buzz.sent, [])

        routed = ROUTE.render_route_message(payload(), ROUTE_RULE)
        visible = event(SENT, SENDER, routed, reply_to=ROOT)
        visible["tags"].append(["p", ROLE_PUBKEY])
        retry_buzz.events.append(visible)
        self.assertEqual(restarted.route(payload()), {
            "status": "duplicate", "event_id": SENT, "root_event_id": ROOT,
        })
        self.assertEqual(retry_buzz.sent, [])

        operation_files = list(state_dir.glob("route-reply-*/*.json"))
        self.assertEqual(len(operation_files), 1)
        self.assertEqual(json.loads(operation_files[0].read_text())["status"], "ACKED")
        self.assertEqual(stat.S_IMODE(state_dir.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(operation_files[0].stat().st_mode), 0o600)

    def test_ambiguous_send_failure_leaves_pending_and_retry_never_resends(self):
        """L1-GIS-098 A crash-window PENDING without event id is reconciled only by its signed route marker."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        state_dir = Path(temporary.name) / "route-state"
        source_events = [event(ROOT, DESK, HEADER), event(TRIGGER, DESK, HEADER, reply_to=ROOT)]
        service, buzz = self.service(source_events, state_dir=state_dir, fail_after_send=True)

        with self.assertRaisesRegex(ROUTE.RouteError, "ambiguous"):
            service.route(payload())
        self.assertEqual(len(buzz.sent), 1)

        restarted, retry_buzz = self.service(source_events, state_dir=state_dir)
        with self.assertRaisesRegex(ROUTE.RouteError, "still pending"):
            restarted.route(payload())
        self.assertEqual(retry_buzz.sent, [])

        routed = ROUTE.render_route_message(payload(), ROUTE_RULE)
        recovered = event("6" * 64, SENDER, routed, reply_to=ROOT)
        recovered["tags"].append(["p", ROLE_PUBKEY])
        retry_buzz.events.append(recovered)
        self.assertEqual(restarted.route(payload())["event_id"], "6" * 64)
        self.assertEqual(retry_buzz.sent, [])

    def test_acked_operation_blocks_resend_when_relay_read_is_temporarily_incomplete(self):
        """L1-GIS-099 Durable ACK state remains the idempotency authority when relay history is incomplete."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        state_dir = Path(temporary.name) / "route-state"
        source_events = [event(ROOT, DESK, HEADER), event(TRIGGER, DESK, HEADER, reply_to=ROOT)]
        service, buzz = self.service(source_events, state_dir=state_dir)
        self.assertEqual(service.route(payload())["status"], "sent")
        self.assertEqual(len(buzz.sent), 1)

        restarted, retry_buzz = self.service(source_events, state_dir=state_dir, publish_on_send=False)
        with self.assertRaisesRegex(ROUTE.RouteError, "recorded route reply"):
            restarted.route(payload())
        self.assertEqual(retry_buzz.sent, [])

    def test_canvas_route_change_cannot_escape_existing_operation_state(self):
        """L1-GIS-099 A Canvas route change shares the Channel+sender ledger and fails closed."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        state_dir = Path(temporary.name) / "route-state"
        source_events = [event(ROOT, DESK, HEADER), event(TRIGGER, DESK, HEADER, reply_to=ROOT)]
        service, _ = self.service(source_events, state_dir=state_dir)
        self.assertEqual(service.route(payload())["status"], "sent")

        changed_canvas = CANVAS_TEXT.replace("feature+ready", "feature+triage")
        buzz = FakeBuzz(source_events, canvases=[canvas_event(content=changed_canvas)], publish_on_send=False)
        restarted = ROUTE.RouteReplyService({CHANNEL: CHANNEL_RULE}, SENDER, buzz, state_dir=state_dir)
        with self.assertRaisesRegex(ROUTE.RouteError, "does not match"):
            restarted.route(payload())
        self.assertEqual(buzz.sent, [])

    def test_retry_idempotency_is_bound_to_source_and_route_not_mutable_reason_copy(self):
        """L1-GIS-069 Server-side copy changes cannot duplicate one source route decision."""
        routed = ROUTE.render_route_message(payload(), ROUTE_RULE)
        sent = event(SENT, SENDER, routed.replace("feature+ready", "older-copy"), reply_to=ROOT)
        sent["tags"].append(["p", ROLE_PUBKEY])
        events = [
            event(ROOT, DESK, HEADER),
            event(TRIGGER, DESK, HEADER, reply_to=ROOT),
            sent,
        ]
        service, buzz = self.service(events)

        self.assertEqual(service.route(payload())["status"], "duplicate")
        self.assertEqual(buzz.sent, [])

    def test_signed_marker_with_wrong_role_tag_fails_closed_without_resend(self):
        """L1-GIS-100 A malformed prior write is not accepted as success and is never duplicated."""
        routed = ROUTE.render_route_message(payload(), ROUTE_RULE)
        malformed = event(SENT, SENDER, routed, reply_to=ROOT)
        malformed["tags"].append(["p", "8" * 64])
        service, buzz = self.service([
            event(ROOT, DESK, HEADER),
            event(TRIGGER, DESK, HEADER, reply_to=ROOT),
            malformed,
        ])

        with self.assertRaisesRegex(ROUTE.RouteError, "invalid channel, thread or mention"):
            service.route(payload())
        self.assertEqual(buzz.sent, [])

    def test_request_cannot_route_outside_the_server_side_role_allowlist(self):
        """L1-GIS-067 A readable Workflow bearer cannot select another Role or any executor target."""
        allowed_service, allowed_buzz = self.service([event(TRIGGER, DESK, HEADER)])
        self.assertEqual(allowed_service.route(payload())["status"], "sent")
        self.assertEqual(len(allowed_buzz.sent), 1, "positive control must exercise the allowlist path")

        service, buzz = self.service([event(TRIGGER, DESK, HEADER)])

        with self.assertRaises(ROUTE.RouteError):
            service.route(payload(route_id="other-role"))

        self.assertEqual(buzz.sent, [])

    def test_route_id_is_server_bound_to_full_trigger_prefix_and_stable_pubkey(self):
        """L1-GIS-070 A readable bearer cannot replay one route against another protocol fact or display-name twin."""
        wrong_fact = HEADER.replace("[type:feature]", "[type:bug]")
        service, buzz = self.service([event(TRIGGER, DESK, wrong_fact)])
        with self.assertRaises(ROUTE.RouteError):
            service.route(payload())
        self.assertEqual(buzz.sent, [])

        service, buzz = self.service([event(TRIGGER, DESK, HEADER)])
        self.assertEqual(service.route(payload())["status"], "sent")
        self.assertEqual(buzz.sent[0][-1], ROLE_PUBKEY)


class HttpDispatchTest(unittest.TestCase):
    class StubService:
        def __init__(self):
            self.requests = []

        def route(self, request):
            self.requests.append(request)
            return {"status": "sent", "event_id": SENT, "root_event_id": ROOT}

    def test_endpoint_requires_exact_path_json_and_constant_secret(self):
        """L1-GIS-060 HTTP dispatch authenticates before JSON parsing and never returns the supplied secret."""
        service = self.StubService()
        raw = json.dumps(payload()).encode()
        ok = ROUTE.dispatch_http("/v1/route-replies", {"Content-Type": "application/json",
                                                       "X-Route-Secret": "s" * 32}, raw, "s" * 32, service)
        self.assertEqual(ok[0], 200)
        self.assertEqual(json.loads(ok[1])["status"], "sent")
        self.assertEqual(service.requests, [payload()])

        for path, headers, body, expected in [
            ("/wrong", {"Content-Type": "application/json", "X-Route-Secret": "s" * 32}, raw, 404),
            ("/v1/route-replies", {"Content-Type": "application/json", "X-Route-Secret": "wrong"}, raw, 401),
            ("/v1/route-replies", {"Content-Type": "text/plain", "X-Route-Secret": "s" * 32}, raw, 415),
            ("/v1/route-replies", {"Content-Type": "application/json", "X-Route-Secret": "s" * 32}, b"{", 400),
        ]:
            with self.subTest(path=path, expected=expected):
                status, response = ROUTE.dispatch_http(path, headers, body, "s" * 32, service)
                self.assertEqual(status, expected)
                self.assertNotIn("s" * 32, response.decode())

    def test_partial_body_cannot_block_an_independent_request(self):
        """L1-GIS-084 A slow partial upload is time-bounded and cannot occupy the only HTTP worker."""
        service = self.StubService()
        server = ROUTE.build_server("127.0.0.1", 0, "s" * 32, service)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address
        slow = socket.create_connection((host, port), timeout=1)
        slow.sendall(
            b"POST /v1/route-replies HTTP/1.1\r\n"
            b"Host: 127.0.0.1\r\nContent-Type: application/json\r\n"
            b"X-Route-Secret: " + b"s" * 32 + b"\r\nContent-Length: 128\r\n\r\n{"
        )
        try:
            request = urllib.request.Request(
                f"http://{host}:{port}/v1/route-replies",
                method="POST",
                data=json.dumps(payload()).encode(),
                headers={"Content-Type": "application/json", "X-Route-Secret": "s" * 32},
            )
            started = time.monotonic()
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=1) as response:
                self.assertEqual(response.status, 200)
            self.assertLess(time.monotonic() - started, 1)
        finally:
            slow.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_process_rate_limit_is_deterministic_and_recovers_after_its_window(self):
        """L1-GIS-093 Compatibility HTTP ingress has a process-level abuse budget before routing."""
        clock = iter((0.0, 1.0, 2.0, 61.0))
        limiter = ROUTE.RequestRateLimiter(limit=2, window_seconds=60, clock=lambda: next(clock))

        self.assertTrue(limiter.allow())
        self.assertTrue(limiter.allow())
        self.assertFalse(limiter.allow())
        self.assertTrue(limiter.allow())

    def test_http_listener_enforces_the_process_rate_limit(self):
        """L1-GIS-093 The public HTTP execution path returns 429 before a second route decision."""
        service = self.StubService()
        server = ROUTE.build_server("127.0.0.1", 0, "s" * 32, service)
        server.rate_limiter = ROUTE.RequestRateLimiter(limit=1, window_seconds=60, clock=lambda: 0.0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address
        request = urllib.request.Request(
            f"http://{host}:{port}/v1/route-replies",
            method="POST",
            data=json.dumps(payload()).encode(),
            headers={"Content-Type": "application/json", "X-Route-Secret": "s" * 32},
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=1) as response:
                self.assertEqual(response.status, 200)
            with self.assertRaises(urllib.error.HTTPError) as denied:
                opener.open(request, timeout=1)
            self.assertEqual(denied.exception.code, 429)
            self.assertEqual(len(service.requests), 1)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_audit_record_contains_route_coordinates_but_never_headers_or_body(self):
        """L1-GIS-093 Structured audit identifies the decision without recording bearer or arbitrary payload."""
        raw = json.dumps(payload(extra="must-not-be-logged")).encode()
        record = ROUTE.audit_record(409, ROUTE.ENDPOINT, raw)

        self.assertEqual(record, {
            "event": "route_reply_http",
            "status": 409,
            "path": ROUTE.ENDPOINT,
            "channel_id": CHANNEL,
            "message_id": TRIGGER,
            "route_id": ROUTE_ID,
        })
        self.assertNotIn("must-not-be-logged", json.dumps(record))


class BuzzAdapterTest(unittest.TestCase):
    def test_canvas_window_must_be_provably_complete(self):
        """L1-GIS-124 A full unpageable Canvas window fails closed instead of guessing latest policy."""
        adapter = object.__new__(ROUTE.RouteBuzzCli)
        adapter.command = lambda _args: [
            event(f"{index:064x}", DESK, CANVAS_TEXT, kind=40100) for index in range(200)
        ]
        with self.assertRaisesRegex(ROUTE.RouteError, "Canvas event window is incomplete"):
            adapter.canvas_events(CHANNEL)

    def test_message_pagination_keeps_inclusive_boundary_and_rejects_same_second_overflow(self):
        """L1-GIS-125 Inclusive --before paging neither drops a boundary second nor guesses past overflow."""
        page_limit = ROUTE.sync.CHANNEL_PAGE_LIMIT
        page_one = [
            event(f"{index + 1:064x}", "7" * 64, "noise", created_at=2001)
            for index in range(page_limit - 1)
        ] + [event(f"{page_limit:064x}", "7" * 64, "noise", created_at=2000)]
        older = event(f"{page_limit + 1:064x}", "7" * 64, "older", created_at=1999)
        calls = []

        def paged(args):
            calls.append(args)
            return page_one if len(calls) == 1 else [page_one[-1], older]

        adapter = object.__new__(ROUTE.RouteBuzzCli)
        adapter.command = paged
        found = adapter.channel_messages(CHANNEL, 1000)
        self.assertEqual(len(found), page_limit + 1)
        self.assertEqual(calls[1][calls[1].index("--before") + 1], "2000")

        boundary_page = [
            event(f"{index + 1:064x}", "7" * 64, "noise", created_at=2000)
            for index in range(page_limit)
        ]
        stuck = object.__new__(ROUTE.RouteBuzzCli)
        stuck.command = lambda _args: boundary_page
        with self.assertRaisesRegex(ROUTE.RouteError, "same-second messages"):
            stuck.channel_messages(CHANNEL, 1000)

    def test_cli_acceptance_and_readback_are_separate_and_http_secret_is_not_forwarded(self):
        """L1-GIS-061 The shim records acceptance before verifying author/channel/root/mention without its secret."""
        routed = ROUTE.render_route_message(payload(), ROUTE_RULE)
        sent_event = event(SENT, SENDER, routed, reply_to=ROOT)
        sent_event["tags"].append(["p", ROLE_PUBKEY])
        responses = [
            subprocess.CompletedProcess([], 0, stdout=json.dumps([event(ROOT, DESK, HEADER)]), stderr=""),
            subprocess.CompletedProcess([], 0, stdout=json.dumps({"event_id": SENT, "accepted": True}), stderr=""),
            subprocess.CompletedProcess([], 0, stdout=json.dumps([event(ROOT, DESK, HEADER), sent_event]), stderr=""),
        ]
        calls = []

        def runner(args, **kwargs):
            calls.append((args, kwargs))
            return responses.pop(0)

        with tempfile.TemporaryDirectory() as tmp:
            cli = Path(tmp) / "buzz-0.5.23" / "buzz"
            cli.parent.mkdir()
            cli.write_bytes(b"\x7fELFtest")
            cli.chmod(0o700)
            config = {
                "secret_env": "GITLAB_BUZZ_ROUTE_SECRET",
                "sender_pubkey": SENDER,
                "channels": {CHANNEL: CHANNEL_RULE},
                "buzz": {"cli_path": str(cli), "cli_sha256": hashlib.sha256(cli.read_bytes()).hexdigest()},
            }
            adapter = ROUTE.RouteBuzzCli(config, {
                "HOME": tmp,
                "PATH": "/usr/bin:/bin",
                "BUZZ_RELAY_URL": "ws://127.0.0.1:3000",
                "BUZZ_PRIVATE_KEY": PRIVATE_KEY,
                "GITLAB_BUZZ_ROUTE_SECRET": "s" * 32,
            }, runner=runner, sleeper=lambda _: None)

            self.assertEqual(adapter.thread(CHANNEL, ROOT), [event(ROOT, DESK, HEADER)])
            self.assertEqual(adapter.send(CHANNEL, routed, ROOT, ROLE_PUBKEY), SENT)
            adapter.verify(CHANNEL, SENT, routed, ROOT, ROLE_PUBKEY)

        self.assertEqual(len(calls), 3)
        self.assertIn("--reply-to", calls[1][0])
        self.assertIn(ROLE_PUBKEY, calls[1][0])
        self.assertEqual(calls[1][1]["input"], routed)
        self.assertTrue(all("GITLAB_BUZZ_ROUTE_SECRET" not in call[1]["env"] for call in calls))

    def test_sender_key_is_verified_before_any_cli_or_relay_call(self):
        """L1-GIS-101 Local route-writer cannot send once with the wrong configured identity."""
        with tempfile.TemporaryDirectory() as tmp:
            cli = Path(tmp) / "buzz-0.5.23" / "buzz"
            cli.parent.mkdir()
            cli.write_bytes(b"\x7fELFtest")
            cli.chmod(0o700)
            config = {
                "scan_since": "1970-01-01T00:01:00Z",
                "sender_pubkey": "f" * 64,
                "channels": {CHANNEL: CHANNEL_RULE},
                "buzz": {"cli_path": str(cli), "cli_sha256": hashlib.sha256(cli.read_bytes()).hexdigest()},
            }
            calls = []
            with self.assertRaisesRegex(ROUTE.ConfigError, "does not match"):
                ROUTE.RouteBuzzCli(config, {
                    "HOME": tmp,
                    "PATH": "/usr/bin:/bin",
                    "BUZZ_RELAY_URL": "ws://127.0.0.1:3000",
                    "BUZZ_PRIVATE_KEY": PRIVATE_KEY,
                }, runner=lambda *args, **kwargs: calls.append((args, kwargs)))
            self.assertEqual(calls, [])


class LocalRouteScannerTest(unittest.TestCase):
    def local_config(self, root):
        cli = Path(root) / "buzz-0.5.23" / "buzz"
        cli.parent.mkdir(exist_ok=True)
        cli.write_bytes(b"\x7fELFtest")
        cli.chmod(0o700)
        return {
            "scan_since": "1970-01-01T00:01:00Z",
            "sender_pubkey": DESK,
            "channels": {CHANNEL: CHANNEL_RULE},
            "buzz": {"cli_path": str(cli), "cli_sha256": hashlib.sha256(cli.read_bytes()).hexdigest()},
        }

    def test_local_scan_routes_only_a_new_signed_bridge_fact_without_workflow_or_secret(self):
        """L1-GIS-102 A local scan maps policy server-side and sends exactly one canonical Thread mention."""
        old_root_header = HEADER.replace("[status:ready]", "[status:backlog]").replace(
            "[issue:182]", "[issue:181]"
        )
        root = event(ROOT, DESK, old_root_header, created_at=90, private_key=BRIDGE_PRIVATE_KEY)
        facts = [
            root,
            event(TRIGGER, DESK, HEADER, reply_to=root["id"], created_at=101,
                  private_key=BRIDGE_PRIVATE_KEY),
            event("6" * 64, "7" * 64, HEADER, reply_to=root["id"], created_at=102),
        ]
        buzz = FakeBuzz(facts, sender=DESK)
        with tempfile.TemporaryDirectory() as tmp:
            service = ROUTE.RouteReplyService(
                {CHANNEL: CHANNEL_RULE}, DESK, buzz, state_dir=Path(tmp) / "state"
            )
            scanner = ROUTE.LocalRouteScanner(
                self.local_config(tmp), service, buzz, state_dir=Path(tmp) / "state", clock=lambda: 200
            )
            result = scanner.run()

        self.assertEqual(result, {
            "status": "ok", "scanned": 3, "matched": 1, "sent": 1, "duplicate": 0,
            "cursor": 200, "canvas_event_id": CANVAS_EVENT,
        })
        self.assertEqual(len(buzz.sent), 1)
        self.assertEqual(buzz.sent[0][2:], (root["id"], ROLE_PUBKEY))
        self.assertEqual(buzz.scan_args, (CHANNEL, 60))

    def test_scan_cursor_and_route_ledger_survive_restart_without_duplicate_mentions(self):
        """L1-GIS-103 Timer overlap and process restart do not route one source fact twice."""
        facts = [event(TRIGGER, DESK, HEADER, created_at=190, private_key=BRIDGE_PRIVATE_KEY)]
        buzz = FakeBuzz(facts, sender=DESK)
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp) / "state"
            first_service = ROUTE.RouteReplyService(
                {CHANNEL: CHANNEL_RULE}, DESK, buzz, state_dir=state_dir
            )
            first = ROUTE.LocalRouteScanner(
                self.local_config(tmp), first_service, buzz, state_dir=state_dir, clock=lambda: 200
            ).run()
            restarted_service = ROUTE.RouteReplyService(
                {CHANNEL: CHANNEL_RULE}, DESK, buzz, state_dir=state_dir
            )
            second = ROUTE.LocalRouteScanner(
                self.local_config(tmp), restarted_service, buzz, state_dir=state_dir, clock=lambda: 220
            ).run()

        self.assertEqual(first["sent"], 1)
        self.assertEqual(second["sent"], 0)
        self.assertEqual(second["matched"], 0)
        self.assertEqual(len(buzz.sent), 1)

    def test_failed_route_does_not_advance_scan_cursor(self):
        """L1-GIS-104 A readback or send failure is retried from the same durable scan boundary."""
        buzz = FakeBuzz([
            event(TRIGGER, DESK, HEADER, created_at=101, private_key=BRIDGE_PRIVATE_KEY)
        ], fail_after_send=True, sender=DESK)
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp) / "state"
            service = ROUTE.RouteReplyService({CHANNEL: CHANNEL_RULE}, DESK, buzz, state_dir=state_dir)
            scanner = ROUTE.LocalRouteScanner(
                self.local_config(tmp), service, buzz, state_dir=state_dir, clock=lambda: 200
            )
            with self.assertRaises(ROUTE.RouteError):
                scanner.run()
            self.assertFalse(scanner.cursor_path.exists())

    def test_http_and_local_modes_share_one_channel_publisher_lease(self):
        """L1-GIS-123 HTTP fallback excludes a local scanner before either can read or write Buzz."""
        scan_buzz = FakeBuzz([
            event(TRIGGER, DESK, HEADER, created_at=101, private_key=BRIDGE_PRIVATE_KEY)
        ], sender=DESK)
        http_buzz = FakeBuzz([], sender=SENDER)
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp) / "state"
            http_service = ROUTE.RouteReplyService(
                {CHANNEL: CHANNEL_RULE}, SENDER, http_buzz, state_dir=state_dir
            )
            scan_service = ROUTE.RouteReplyService(
                {CHANNEL: CHANNEL_RULE}, DESK, scan_buzz, state_dir=state_dir
            )
            scanner = ROUTE.LocalRouteScanner(
                self.local_config(tmp), scan_service, scan_buzz,
                state_dir=state_dir, clock=lambda: 200,
            )

            lease = http_service.acquire_mode("http")
            try:
                with self.assertRaisesRegex(ROUTE.RouteError, "HTTP fallback.*active"):
                    scanner.run()
            finally:
                http_service.release_mode(lease)

            self.assertEqual(scan_buzz.sent, [])
            self.assertFalse(hasattr(scan_buzz, "canvas_args"))
            self.assertFalse(hasattr(scan_buzz, "scan_args"))
            self.assertEqual(http_service.mode_lock_path, scan_service.mode_lock_path)

    def test_simultaneous_modes_elect_one_writer_and_the_loser_writes_zero(self):
        """L1-GIS-125 A true scan/HTTP start race has one winner and a zero-write loser."""
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp) / "state"
            services = {
                "scan": ROUTE.RouteReplyService(
                    {CHANNEL: CHANNEL_RULE}, DESK, FakeBuzz([], sender=DESK), state_dir=state_dir
                ),
                "http": ROUTE.RouteReplyService(
                    {CHANNEL: CHANNEL_RULE}, SENDER, FakeBuzz([], sender=SENDER), state_dir=state_dir
                ),
            }
            start = threading.Barrier(2)
            loser_finished = threading.Event()
            outcomes: dict[str, str] = {}
            writes: list[str] = []

            def contend(mode: str) -> None:
                start.wait(timeout=2)
                try:
                    lease = services[mode].acquire_mode(mode)
                except ROUTE.RouteError:
                    outcomes[mode] = "lost"
                    loser_finished.set()
                    return
                outcomes[mode] = "won"
                # Keep the winning lifecycle lease until the other mode has
                # actually attempted startup.  If both modes acquire, both
                # wait out the timeout and the two writes make the oracle red.
                loser_finished.wait(timeout=2)
                writes.append(mode)
                services[mode].release_mode(lease)

            threads = [threading.Thread(target=contend, args=(mode,)) for mode in ("scan", "http")]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=5)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(Counter(outcomes.values()), Counter({"won": 1, "lost": 1}))
        self.assertEqual(len(writes), 1, outcomes)
        loser = next(mode for mode, outcome in outcomes.items() if outcome == "lost")
        self.assertNotIn(loser, writes)

    def test_mode_lease_rejects_sender_topology_mismatch(self):
        """L1-GIS-124 The shared lease also gates scan/HTTP identity topology."""
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp) / "state"
            scan_service = ROUTE.RouteReplyService(
                {CHANNEL: CHANNEL_RULE}, DESK, FakeBuzz([], sender=DESK), state_dir=state_dir
            )
            http_service = ROUTE.RouteReplyService(
                {CHANNEL: CHANNEL_RULE}, SENDER, FakeBuzz([], sender=SENDER), state_dir=state_dir
            )

            with self.assertRaisesRegex(ROUTE.ConfigError, "scan sender"):
                http_service.acquire_mode("scan")
            with self.assertRaisesRegex(ROUTE.ConfigError, "HTTP sender"):
                scan_service.acquire_mode("http")

    def test_dry_run_validates_the_source_but_never_sends_or_advances(self):
        """L1-GIS-105 Deployment preflight can validate route facts without producing a mention."""
        buzz = FakeBuzz([
            event(TRIGGER, DESK, HEADER, created_at=101, private_key=BRIDGE_PRIVATE_KEY)
        ], sender=DESK)
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp) / "state"
            service = ROUTE.RouteReplyService({CHANNEL: CHANNEL_RULE}, DESK, buzz, state_dir=state_dir)
            scanner = ROUTE.LocalRouteScanner(
                self.local_config(tmp), service, buzz, state_dir=state_dir, clock=lambda: 200
            )
            result = scanner.run(dry_run=True)

            self.assertEqual(result["would_send"], 1)
            self.assertNotIn("sent", result)
            self.assertEqual(buzz.sent, [])
            self.assertFalse(scanner.cursor_path.exists())

    def test_bridge_fact_requires_canonical_id_and_valid_bip340_signature(self):
        """L1-GIS-122 A copied publisher pubkey cannot authenticate a forged route fact."""
        signed = event(
            "ignored", DESK, HEADER, created_at=101, private_key=BRIDGE_PRIVATE_KEY
        )
        with tempfile.TemporaryDirectory() as tmp:
            buzz = FakeBuzz([], sender=DESK)
            service = ROUTE.RouteReplyService(
                {CHANNEL: CHANNEL_RULE}, DESK, buzz, state_dir=Path(tmp) / "state"
            )
            scanner = ROUTE.LocalRouteScanner(
                self.local_config(tmp), service, buzz, state_dir=Path(tmp) / "state"
            )
            self.assertEqual(
                scanner._publisher_event(signed),
                (signed["id"], signed["created_at"], signed["content"]),
            )
            for forged in (
                {**signed, "id": "0" * 64},
                {**signed, "sig": "0" * 128},
                {key: value for key, value in signed.items() if key != "sig"},
            ):
                with self.subTest(forged=forged), self.assertRaisesRegex(
                    ROUTE.RouteError, "id or signature"
                ):
                    scanner._publisher_event(forged)


class MainContractTest(unittest.TestCase):
    def test_main_rejects_dry_run_without_scan_and_non_loopback_listener(self):
        """L1-GIS-108 CLI mode flags fail closed before config or credentials are loaded."""
        common = ["--config", "missing.json", "--state-dir", "state"]
        with self.assertRaisesRegex(ROUTE.ConfigError, "--dry-run requires --scan-once"):
            ROUTE.main([*common, "--dry-run"], env={})
        with self.assertRaisesRegex(ROUTE.ConfigError, "loopback host"):
            ROUTE.main([*common, "--listen", "0.0.0.0:8787"], env={})

    def test_main_scan_once_wires_local_components_and_returns_json(self):
        """L1-GIS-109 Scan mode loads scan config and forwards dry-run to the one-shot scanner."""
        config = {"channels": {CHANNEL: CHANNEL_RULE}, "sender_pubkey": SENDER}
        scanner = mock.Mock()
        scanner.run.return_value = {"status": "ok", "scanned": 0, "matched": 0}
        state_dir = Path("private-state")
        with (
            mock.patch.object(ROUTE, "load_config", return_value=config) as load,
            mock.patch.object(ROUTE, "RouteBuzzCli") as buzz_type,
            mock.patch.object(ROUTE, "RouteReplyService") as service_type,
            mock.patch.object(ROUTE, "LocalRouteScanner", return_value=scanner) as scanner_type,
            redirect_stdout(io.StringIO()) as output,
        ):
            result = ROUTE.main(
                ["--config", "route.json", "--state-dir", str(state_dir), "--scan-once", "--dry-run"],
                env={"PATH": "/usr/bin:/bin"},
            )

        self.assertEqual(result, 0)
        load.assert_called_once_with(Path("route.json"), mode="scan")
        buzz_type.assert_called_once_with(config, {"PATH": "/usr/bin:/bin"})
        service_type.assert_called_once_with(
            config["channels"], SENDER, buzz_type.return_value, state_dir=state_dir
        )
        scanner_type.assert_called_once_with(
            config, service_type.return_value, buzz_type.return_value, state_dir=state_dir
        )
        scanner.run.assert_called_once_with(dry_run=True)
        self.assertEqual(json.loads(output.getvalue()), scanner.run.return_value)


class DocumentationContractTest(unittest.TestCase):
    def test_local_route_writer_is_the_0_2_1_default_and_needs_no_workflow_or_secret(self):
        """L1-GIS-106 Versioned policy replaces Workflow/webhook while Agent prompts remain Agent config."""
        config = json.loads(LOCAL_EXAMPLE.read_text(encoding="utf-8"))
        self.assertEqual(set(config), {"scan_since", "sender_pubkey", "channels", "buzz"})
        self.assertNotIn("secret", json.dumps(config).lower())
        self.assertNotIn("webhook", json.dumps(config).lower())

        reference = REFERENCE.read_text(encoding="utf-8")
        readme = SCRIPTS_README.read_text(encoding="utf-8")
        for text in (reference, readme):
            self.assertIn("--scan-once", text)
            self.assertIn("不需要 Workflow", text)
            self.assertIn("不监听", text)
            self.assertIn("Agent prompt", text)
        self.assertIn("call_webhook", reference)
        self.assertIn("非默认", reference)

    def test_fallback_workflow_keeps_policy_in_channel_and_secret_out_of_git(self):
        """L1-GIS-062 The fallback template has the native filter but calls one authenticated route endpoint."""
        text = TEMPLATE.read_text(encoding="utf-8")
        self.assertRegex(text, r"(?m)^\s*on:\s*message_posted\s*$")
        self.assertRegex(text, r"(?m)^\s*action:\s*call_webhook\s*$")
        self.assertRegex(text, r"(?m)^\s*url:\s*<route-reply-url>/v1/route-replies\s*$")
        self.assertEqual(text.count("X-Route-Secret:"), 1)
        self.assertIn('X-Route-Secret: "<route-secret>"', text)
        for variable in ("trigger.channel_id", "trigger.message_id"):
            self.assertIn("{{" + variable + "}}", text)
        self.assertIn('"route_id":"feature-ready"', text)
        self.assertNotIn('"mention":', text)
        self.assertNotIn('"reason":', text)
        self.assertNotIn("reply_in_thread", text)
        self.assertNotIn("str_contains", text)
        self.assertRegex(
            " ".join(re.search(r"(?ms)^\s*filter:\s*>-?\s*\n(.*?)\n\s*steps:", text).group(1).split()),
            r'^trigger_author == "[^"]+" && str_starts_with\(trigger_text, '
            r'"\[gitlab-notify:v1\]\[object:issue\]\[type:[^\]]+\]\[status:[^\]]+\]'
            r'\[state:(opened|closed)\]\[change:routing\]"\)$',
        )

    def test_example_and_reference_define_a_removable_compatibility_service(self):
        """L1-GIS-063 Docs distinguish placeholder URL, optional service, secret injection and native cutover."""
        config = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        self.assertEqual(set(config), {"secret_env", "sender_pubkey", "channels", "buzz"})
        self.assertEqual(config["secret_env"], "GITLAB_BUZZ_ROUTE_SECRET")
        self.assertNotIn("secret", json.dumps(config).replace("secret_env", ""))

        reference = REFERENCE.read_text(encoding="utf-8")
        readme = SCRIPTS_README.read_text(encoding="utf-8")
        for needle in ("gitlab_buzz_route_reply.py", "route-type-status-fallback.yaml",
                       "gitlab-buzz-route-reply.example.json"):
            self.assertIn(needle, reference)
            self.assertIn(needle, readme)
        self.assertIn("占位域名", reference)
        self.assertIn("reply_in_thread", reference)
        self.assertIn("--state-dir", reference)
        self.assertIn("`PENDING`", reference)
        self.assertIn("不能自动重发", reference)
        self.assertIn("停用", reference)


if __name__ == "__main__":
    unittest.main()
