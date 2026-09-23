"""TDD contracts for Canvas-owned GitLab -> Buzz role routing."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock


SKILL = Path(__file__).resolve().parents[1]
SCRIPT = SKILL / "scripts" / "gitlab_buzz_route_reply.py"
LOCAL_EXAMPLE = SKILL / "references" / "scripts" / "gitlab-buzz-route-writer.example.json"
CANVAS_EXAMPLE = SKILL / "references" / "gitlab-buzz-routing-canvas.md"


def load_module():
    spec = importlib.util.spec_from_file_location("gitlab_buzz_canvas_route_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ROUTE = load_module()
CHANNEL = "11111111-2222-3333-4444-555555555555"
DESK_PRIVATE_KEY = "2" * 64
DESK = ROUTE.sync.publisher_pubkey_from_private_key(DESK_PRIVATE_KEY)
CANVAS_ADMIN_PRIVATE = "6" * 64
CANVAS_ADMIN = ROUTE.sync.publisher_pubkey_from_private_key(CANVAS_ADMIN_PRIVATE)
UNTRUSTED_PRIVATE = "7" * 64
UNTRUSTED_MEMBER = ROUTE.sync.publisher_pubkey_from_private_key(UNTRUSTED_PRIVATE)
ROOT = "3" * 64
TRIGGER = "4" * 64
SENT = "5" * 64
ROLE_PUBKEY = "9" * 64
PREFIX = (
    "[gitlab-notify:v1][object:issue][type:feature][status:ready]"
    "[state:opened][change:routing]"
)
HEADER = PREFIX + "[project:481][issue:182]"
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


def canvas_text(*, role: str = "feature", prefix: str = PREFIX, extra: str = "") -> str:
    return f"""# Naturehood collaboration

This prose is context for people and Agents.

<!-- gitlab-buzz-routing:v1 -->
| route_id | trigger_prefix | role | reason |
| --- | --- | --- | --- |
| feature-ready | `{prefix}` | `{role}` | `feature+ready` |
<!-- /gitlab-buzz-routing -->
{extra}"""


def event(event_id: str, pubkey: str, content: str, *, kind: int = 9,
          created_at: int = 100, reply_to: str | None = None, sig: str | None = None,
          private_key: str | None = None):
    tags = [["h", CHANNEL]]
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


def canvas_event(*, private_key: str = CANVAS_ADMIN_PRIVATE, content: str | None = None,
                 created_at: int = 90):
    pubkey = ROUTE.sync.publisher_pubkey_from_private_key(private_key)
    body = canvas_text() if content is None else content
    tags = [["h", CHANNEL]]
    canonical = json.dumps(
        [0, pubkey, created_at, 40100, tags, body], ensure_ascii=False, separators=(",", ":")
    ).encode()
    digest = hashlib.sha256(canonical).digest()
    return {
        "id": digest.hex(), "pubkey": pubkey, "kind": 40100, "content": body,
        "tags": tags, "created_at": created_at,
        "sig": ROUTE.sync.nk.schnorr_sign(
            digest, bytes.fromhex(private_key), b"\x00" * 32
        ).hex(),
    }


CANVAS_EVENT = canvas_event()["id"]


class FakeBuzz:
    def __init__(self, facts, canvases):
        self.facts = list(facts)
        self.canvases = list(canvases)
        self.sent = []
        self.canvas_reads = 0

    def canvas_events(self, channel_id):
        self.canvas_reads += 1
        self.canvas_args = channel_id
        return list(self.canvases)

    def channel_messages(self, channel_id, since_unix):
        return [item for item in self.facts if item.get("created_at", 0) >= since_unix]

    def thread(self, channel_id, message_id):
        return list(self.facts)

    def send(self, channel_id, content, reply_to, mention_pubkey):
        self.sent.append((channel_id, content, reply_to, mention_pubkey))
        sent = event(SENT, DESK, content, reply_to=reply_to)
        sent["tags"].append(["p", mention_pubkey])
        self.facts.append(sent)
        return SENT

    def verify(self, channel_id, event_id, content, reply_to, mention_pubkey):
        candidate = next(item for item in self.facts if item.get("id") == event_id)
        ROUTE.verify_route_event(
            candidate, DESK, channel_id, event_id, content, reply_to, mention_pubkey
        )


class CanvasPolicyContractTest(unittest.TestCase):
    def test_canvas_rule_resolves_only_through_code_owned_role_registry(self):
        """L1-GIS-110 Canvas owns match policy; code owns Agent name and stable pubkey."""
        policy = ROUTE.select_canvas_policy([canvas_event()], CHANNEL, CHANNEL_RULE)

        self.assertEqual(policy["event_id"], CANVAS_EVENT)
        route = policy["routes"]["feature-ready"]
        self.assertEqual(route["trigger_prefix"], PREFIX)
        self.assertEqual(route["role"], "feature")
        self.assertEqual(route["mention"], "@feature-agent")
        self.assertEqual(route["mention_pubkey"], ROLE_PUBKEY)
        self.assertEqual(route["reason"], "feature+ready")
        self.assertRegex(route["policy_sha256"], r"^[0-9a-f]{64}$")

    def test_latest_canvas_must_be_signed_by_an_allowlisted_admin(self):
        """L1-GIS-111 An older trusted Canvas cannot mask the visible newer member edit."""
        canvases = [
            canvas_event(created_at=90),
            canvas_event(private_key=UNTRUSTED_PRIVATE, created_at=91),
        ]

        with self.assertRaisesRegex(ROUTE.RouteError, "Canvas author"):
            ROUTE.select_canvas_policy(canvases, CHANNEL, CHANNEL_RULE)

    def test_canvas_envelope_and_single_versioned_table_fail_closed(self):
        """L1-GIS-112 Unsigned, wrong-channel, duplicate-block, or malformed Canvas is unusable."""
        duplicate = canvas_text(extra="\n" + canvas_text())
        cases = [
            {**canvas_event(), "sig": None},
            {**canvas_event(), "tags": [["h", "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"]]},
            {**canvas_event(), "content": canvas_text() + "\ntampered"},
            canvas_event(content=duplicate),
            canvas_event(content=canvas_text().replace("| --- | --- | --- | --- |", "bad")),
        ]
        for candidate in cases:
            with self.subTest(candidate=candidate), self.assertRaises(ROUTE.RouteError):
                ROUTE.select_canvas_policy([candidate], CHANNEL, CHANNEL_RULE)

    def test_canvas_cannot_name_unknown_or_executor_role(self):
        """L1-GIS-113 Canvas cannot mint an identity or cross into an executor boundary."""
        with self.assertRaisesRegex(ROUTE.RouteError, "role"):
            ROUTE.select_canvas_policy(
                [canvas_event(content=canvas_text(role="executor"))], CHANNEL, CHANNEL_RULE
            )

        executor_registry = json.loads(json.dumps(CHANNEL_RULE))
        executor_registry["roles"]["executor"] = {
            "mention": "@nh-sre-executor",
            "mention_pubkey": "8" * 64,
        }
        with self.assertRaises(ROUTE.ConfigError):
            ROUTE.validate_channel_rule(executor_registry)


class CanvasDeskRoutingTest(unittest.TestCase):
    def _config(self, root: str):
        cli = Path(root) / "buzz-0.5.23" / "buzz"
        cli.parent.mkdir()
        cli.write_bytes(b"\x7fELFtest")
        cli.chmod(0o700)
        return {
            "scan_since": "1970-01-01T00:01:00Z",
            "sender_pubkey": DESK,
            "channels": {CHANNEL: CHANNEL_RULE},
            "buzz": {"cli_path": str(cli), "cli_sha256": hashlib.sha256(cli.read_bytes()).hexdigest()},
        }

    def test_local_scan_reads_canvas_once_and_replies_as_desk_on_original_thread(self):
        """L1-GIS-114 One Desk turn routes from one trusted Canvas snapshot without Workflow."""
        initial = HEADER.replace("[status:ready]", "[status:backlog]")
        root = event(
            ROOT, DESK, initial, created_at=100, private_key=DESK_PRIVATE_KEY
        )
        facts = [
            root,
            event(TRIGGER, DESK, HEADER, created_at=101, reply_to=root["id"],
                  private_key=DESK_PRIVATE_KEY),
        ]
        buzz = FakeBuzz(facts, [canvas_event()])
        with tempfile.TemporaryDirectory() as tmp:
            config = self._config(tmp)
            state_dir = Path(tmp) / "state"
            service = ROUTE.RouteReplyService(
                config["channels"], DESK, buzz, state_dir=state_dir
            )
            result = ROUTE.LocalRouteScanner(
                config, service, buzz, state_dir=state_dir, clock=lambda: 200
            ).run()

        self.assertEqual(result["canvas_event_id"], CANVAS_EVENT)
        self.assertEqual((result["matched"], result["sent"]), (1, 1))
        self.assertEqual(buzz.canvas_reads, 1)
        self.assertEqual(buzz.sent[0][2:], (root["id"], ROLE_PUBKEY))
        self.assertIn("@feature-agent", buzz.sent[0][1])
        self.assertIn("[policy:", buzz.sent[0][1])

    def test_cli_reads_complete_canvas_event_not_content_only_canvas_get(self):
        """L1-GIS-115 Author and signature verification use the normalized raw event envelope."""
        with tempfile.TemporaryDirectory() as tmp:
            config = self._config(tmp)
            calls = []

            def runner(args, **kwargs):
                calls.append((args, kwargs))
                return subprocess.CompletedProcess(
                    args, 0, stdout=json.dumps([canvas_event()]), stderr=""
                )

            adapter = ROUTE.RouteBuzzCli(
                config,
                {
                    "HOME": tmp,
                    "PATH": "/usr/bin:/bin",
                    "BUZZ_RELAY_URL": "ws://127.0.0.1:3000",
                    "BUZZ_PRIVATE_KEY": DESK_PRIVATE_KEY,
                },
                runner=runner,
            )
            self.assertEqual(adapter.canvas_events(CHANNEL), [canvas_event()])

        self.assertEqual(len(calls), 1)
        self.assertIn("messages", calls[0][0])
        self.assertIn("40100", calls[0][0])
        self.assertNotIn("canvas", calls[0][0])

    def test_checked_in_runtime_config_has_no_route_table_or_agent_prompt(self):
        """L1-GIS-116 Git holds trust anchors and role identities; admins edit routes in Canvas."""
        config = json.loads(LOCAL_EXAMPLE.read_text(encoding="utf-8"))
        channel = next(iter(config["channels"].values()))
        self.assertEqual(set(channel), {"publisher_pubkey", "canvas_admin_pubkeys", "roles"})
        self.assertNotIn("routes", channel)
        self.assertNotIn("trigger_prefix", json.dumps(config))
        self.assertNotIn("prompt", json.dumps(config).lower())

        canvas = CANVAS_EXAMPLE.read_text(encoding="utf-8")
        self.assertIn("<!-- gitlab-buzz-routing:v1 -->", canvas)
        self.assertIn("| route_id | trigger_prefix | role | reason |", canvas)
        block = canvas.split("<!-- gitlab-buzz-routing:v1 -->", 1)[1].split(
            "<!-- /gitlab-buzz-routing -->", 1
        )[0]
        self.assertNotIn("mention_pubkey", block)
        self.assertNotIn("prompt", block.lower())

    def test_scan_lock_open_failure_is_reported_without_masking_the_cause(self):
        """L1-GIS-121 A filesystem failure cannot become an unbound local error."""
        with tempfile.TemporaryDirectory() as tmp:
            config = self._config(tmp)
            state_dir = Path(tmp) / "state"
            buzz = FakeBuzz([], [canvas_event()])
            service = ROUTE.RouteReplyService(
                config["channels"], DESK, buzz, state_dir=state_dir
            )
            scanner = ROUTE.LocalRouteScanner(
                config, service, buzz, state_dir=state_dir, clock=lambda: 200
            )
            with mock.patch.object(ROUTE.os, "open", side_effect=PermissionError):
                with self.assertRaisesRegex(ROUTE.RouteError, "cannot lock local route scan"):
                    scanner._open_lock()


if __name__ == "__main__":
    unittest.main()
