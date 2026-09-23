"""L3 route-reply compatibility service against the real local Buzz relay.

Skipped unless ``BUZZ_SYNC_L3=1``.  The HTTP test launches the shipped service
as a subprocess and only drives its public route.  It uses a dedicated
localstack identity, the raw pinned Buzz CLI, the pinned relay 0.2.1 and the real Role
Agent.  It does not claim to cover relay ``call_webhook`` or public HTTPS;
those remain L4 because relay SSRF policy intentionally rejects loopback.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import unittest

try:
    from .e2e_support import ENABLED, E2ECase, wait_for
    from .test_sync_local import STATE_FILE, p_tags, tag_values
except ImportError:  # imported as a top-level module
    from e2e_support import ENABLED, E2ECase, wait_for
    from test_sync_local import STATE_FILE, p_tags, tag_values


SKILL = Path(__file__).resolve().parents[2]
SCRIPT = SKILL / "scripts" / "gitlab_buzz_route_reply.py"
ROUTE_SECRET_ENV = "GITLAB_BUZZ_ROUTE_SECRET"
COMPAT_RELAY_IMAGE = "ghcr.io/block/buzz:0.2.1"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def post_json(url: str, body: dict, secret: str) -> tuple[int, dict]:
    request = urllib.request.Request(
        url,
        method="POST",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "X-Route-Secret": secret},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=20) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


@unittest.skipUnless(ENABLED, "L3 runs only with BUZZ_SYNC_L3=1 and the localstack Role agent running")
class RouteReplyE2ETest(E2ECase):
    required_agents = ("role",)

    @classmethod
    def setUpClass(cls):
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if COMPAT_RELAY_IMAGE not in (state.get("images") or {}):
            raise AssertionError(f"compatibility routing L3 requires relay image {COMPAT_RELAY_IMAGE}")
        super().setUpClass()

    def start_service(self) -> tuple[subprocess.Popen, str, str]:
        secret = secrets.token_urlsafe(36)
        config = {
            "secret_env": ROUTE_SECRET_ENV,
            "sender_pubkey": self.stack.route["pubkey"],
            "channels": {self.stack.channel: {
                "publisher_pubkey": self.stack.desk["pubkey"],
                "canvas_admin_pubkeys": [self.stack.owner["pubkey"]],
                "roles": {
                    "role": {
                        "mention": "@buzz-sync-role",
                        "mention_pubkey": self.stack.role_pubkey,
                    }
                },
            }},
            "buzz": {"cli_path": str(self.stack.cli), "cli_sha256": hashlib.sha256(self.stack.cli.read_bytes()).hexdigest()},
        }
        config_path = self.tmp / "route-reply.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        config_path.chmod(0o600)
        port = free_port()
        home = self.tmp / "route-home"
        home.mkdir(mode=0o700)
        env = {
            "HOME": str(home),
            "PATH": "/usr/bin:/bin",
            "BUZZ_RELAY_URL": self.stack.relay_http,
            "BUZZ_PRIVATE_KEY": self.stack.route["secret"],
            ROUTE_SECRET_ENV: secret,
        }
        if self.stack.route.get("auth_tag"):
            env["BUZZ_AUTH_TAG"] = self.stack.route["auth_tag"]
        proc = subprocess.Popen(
            [sys.executable, str(SCRIPT), "--config", str(config_path),
             "--state-dir", str(self.tmp / "route-state"), "--listen", f"127.0.0.1:{port}"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )

        def ready():
            if proc.poll() is not None:
                stdout, stderr = proc.communicate(timeout=1)
                self.fail(f"route service exited {proc.returncode}: {(stdout + stderr)[-500:]}")
            try:
                status, _ = post_json(f"http://127.0.0.1:{port}/wrong", {}, secret)
                return status == 404
            except OSError:
                return False

        wait_for("route-reply HTTP server", ready, 20, 0.2)
        self.addCleanup(self.stop_service, proc)
        return proc, f"http://127.0.0.1:{port}/v1/route-replies", secret

    @staticmethod
    def stop_service(proc: subprocess.Popen) -> None:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        if proc.stdout is not None:
            proc.stdout.close()
        if proc.stderr is not None:
            proc.stderr.close()

    def test_001_http_to_real_buzz_thread_is_idempotent_and_wakes_role(self):
        """L3-GIS-015 / L3-GIS-DEMO-23 HTTP → verified Desk fact + Canvas → dedicated writer → real Thread/p-tag → Role reply; retry is a no-op."""
        _, url, secret = self.start_service()
        issue = self.stack.create_issue(f"l3 fallback {self.stamp}", "type::feature,status::ready")
        run = self.stack.run_sync(self.stack.config(self.since, people={}), self.tmp)
        self.assertEqual((run.returncode, run.status), (0, "ok"), run.describe())
        root = self.only_root("issue", issue["iid"])
        request = {
            "channel_id": self.stack.channel,
            "message_id": root["id"],
            "route_id": "feature-ready",
        }

        status, sent = post_json(url, request, secret)
        self.assertEqual(status, 200, sent)
        self.assertEqual(sent["status"], "sent")
        self.assertEqual(sent["root_event_id"], root["id"])

        def route_event():
            return next((event for event in self.stack.thread(root["id"])
                         if event.get("id") == sent["event_id"]), None)

        routed = wait_for("route-reply event readback", route_event, 30, 0.5)
        self.assertEqual(routed["pubkey"], self.stack.route["pubkey"])
        self.assertEqual(p_tags(routed), {self.stack.role_pubkey})
        self.assertEqual([tag[:4] for tag in tag_values(routed, "e")], [["e", root["id"], "", "reply"]])
        self.wait_role(root["id"])

        retry_status, duplicate = post_json(url, request, secret)
        self.assertEqual(retry_status, 200, duplicate)
        self.assertEqual(duplicate, {"status": "duplicate", "event_id": sent["event_id"],
                                     "root_event_id": root["id"]})
        time.sleep(2)
        self.assertEqual(len([event for event in self.stack.thread(root["id"])
                              if event.get("pubkey") == self.stack.route["pubkey"]]), 1)

        denied_status, denied = post_json(url, request, "wrong-secret")
        self.assertEqual(denied_status, 401, denied)
        self.assertNotIn(secret, json.dumps(denied))

    def test_002_workflow_bearer_literal_is_visible_to_channel_bots(self):
        """L3-GIS-DEMO-23 Characterization: workflows get exposes a fake X-Route-Secret literal to owner, Desk and Role."""
        marker = "FAKE_ROUTE_SECRET_" + secrets.token_hex(12)
        yaml = f"""name: gitlab-route-secret-{self.stamp}
trigger:
  on: message_posted
steps:
  - id: probe
    action: call_webhook
    url: https://gitlab-buzz.example/v1/route-replies
    headers:
      X-Route-Secret: {marker}
"""
        created = self.stack.buzz("owner", "workflows", "create", "--channel", self.stack.channel, "--yaml", yaml)
        workflow_id = created["workflow_id"]
        self.addCleanup(self.delete_workflow, workflow_id)
        for identity in ("owner", "desk", "role"):
            with self.subTest(identity=identity):
                detail = self.stack.buzz(identity, "workflows", "get", "--workflow", workflow_id)
                self.assertIn(marker, json.dumps(detail))


if __name__ == "__main__":
    unittest.main()
