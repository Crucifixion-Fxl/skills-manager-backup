"""L3 responsible-send boundary against the real local Buzz relay.

Skipped unless BUZZ_SYNC_L3=1.  The test invokes the shipped send wrapper—not
a hand-built ``buzz messages send`` command—so fresh membership/profile reads,
the p-tag budget, durable readback and zero-send rejection share production's
public execution boundary.
"""

from __future__ import annotations

import importlib.util
import hashlib
import json
import subprocess
import unittest
from pathlib import Path

try:
    from .e2e_support import ENABLED, E2ECase
    from .test_sync_local import p_tags, tag_values
except ImportError:  # imported as a top-level module
    from e2e_support import ENABLED, E2ECase
    from test_sync_local import p_tags, tag_values


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "buzz_send_with_responsible_mentions.py"


def load_sender():
    spec = importlib.util.spec_from_file_location("buzz_responsible_send_e2e", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SENDER = load_sender()


class StaticStructuredSource:
    """Stand in for an already-read approved GitLab field, not message prose."""

    def __init__(self, username: str):
        self.username = username

    def candidates(self, locators):
        return [{"username": self.username, "source": "gitlab.reviewers"}]


@unittest.skipUnless(ENABLED, "L3 runs only with BUZZ_SYNC_L3=1 and the localstack up")
class ResponsibleMentionsE2ETest(E2ECase):
    required_agents = ()

    def write_json(self, name: str, value: dict) -> Path:
        path = self.tmp / name
        path.write_text(json.dumps(value), encoding="utf-8")
        path.chmod(0o600)
        return path

    def config(self, suffix: str, people: dict | None = None) -> Path:
        self.write_json(f"responsible-{suffix}.people.json", people or {})
        return self.write_json(f"responsible-{suffix}.config.json", {
            "version": 2,
            "sender_pubkey": self.stack.desk["pubkey"],
            "state_dir": str(self.tmp / f"responsible-{suffix}.state"),
            "gitlab": {
                "base_url": "http://127.0.0.1:8929",
                "token_env": "LOCALSTACK_GITLAB_TOKEN",
                "projects": [self.stack.project_id],
            },
            "channels": [self.stack.channel],
            "people_file": str(self.tmp / f"responsible-{suffix}.people.json"),
            "buzz": {
                "cli_path": str(self.stack.cli),
                "cli_sha256": hashlib.sha256(self.stack.cli.read_bytes()).hexdigest(),
            },
        })

    def request(self, name: str, root: str, content: str, **extra) -> Path:
        return self.write_json(f"responsible-{name}.input.json", {
            "version": 1,
            "channel_id": self.stack.channel,
            "reply_to": root,
            "content": content,
            "sources": [{
                "kind": "gitlab",
                "project_id": self.stack.project_id,
                "object": "mr",
                "iid": 1,
                "field": "reviewers",
            }],
            **extra,
        })

    def sender_env(self) -> dict[str, str]:
        return {
            "HOME": str(self.tmp),
            "PATH": "/usr/bin:/bin",
            "BUZZ_RELAY_URL": self.stack.relay_http,
            "BUZZ_PRIVATE_KEY": self.stack.desk["secret"],
            "BUZZ_AUTH_TAG": self.stack.desk["auth_tag"],
            "LOCALSTACK_GITLAB_TOKEN": self.stack.tokens["bot"],
        }

    def test_001_real_gitlab_author_becomes_one_read_back_human_p_tag(self):
        gitlab_username = self.stack.users["dev"]["username"]
        issue = self.stack.create_issue(f"responsible source {self.stamp}")
        root = self.sent_id(self.stack.buzz(
            "owner", "messages", "send", "--channel", self.stack.channel,
            "--content", f"responsible mention root {self.stamp}",
        ))
        config = self.config("human", {gitlab_username: self.stack.owner["pubkey"]})
        request = self.request(
            "human",
            root,
            "action required: review the evidence",
            sources=[{
                "kind": "gitlab",
                "project_id": self.stack.project_id,
                "object": "issue",
                "iid": issue["iid"],
                "field": "author",
            }],
        )
        result = SENDER.execute(
            config,
            request,
            env=self.sender_env(),
            runner=subprocess.run,
        )
        self.assertEqual(result["status"], "sent")
        self.assertEqual(result["mentions"], [self.stack.owner["pubkey"]])
        sent = result["event_id"]
        event = next(item for item in self.channel_events() if item["id"] == sent)

        self.assertEqual(event["pubkey"], self.stack.desk["pubkey"])
        self.assertEqual(p_tags(event), {self.stack.owner["pubkey"]})
        reply_roots = {tag[1] for tag in tag_values(event, "e") if len(tag) > 1}
        self.assertEqual(reply_roots, {root})

        duplicate = SENDER.execute(
            config,
            request,
            env=self.sender_env(),
            runner=subprocess.run,
        )
        self.assertEqual(duplicate["status"], "duplicate")
        self.assertEqual(
            [item["id"] for item in self.stack.thread(root) if item.get("pubkey") == self.stack.desk["pubkey"]],
            [sent],
        )

    def test_002_bot_and_free_text_owner_fail_before_send(self):
        root = self.sent_id(self.stack.buzz(
            "owner", "messages", "send", "--channel", self.stack.channel,
            "--content", f"responsible rejection root {self.stamp}",
        ))
        config = self.config("rejected", {"buzz-sync-role": self.stack.role_pubkey})
        bot_request = self.request("bot", root, "action required: review the evidence")
        before = [item["id"] for item in self.stack.thread(root)]
        with self.assertRaisesRegex(SENDER.SendError, "verified human"):
            SENDER.execute(
                config,
                bot_request,
                env=self.sender_env(),
                runner=subprocess.run,
                source_reader=StaticStructuredSource("buzz-sync-role"),
            )
        self.assertEqual([item["id"] for item in self.stack.thread(root)], before)

        free_text = self.request(
            "free-text",
            root,
            "action required",
            sources=[{"kind": "free_text", "text": "localstack-owner"}],
        )
        with self.assertRaises(SENDER.SendError):
            SENDER.execute(config, free_text, env=self.sender_env(), runner=subprocess.run)
        self.assertEqual([item["id"] for item in self.stack.thread(root)], before)


if __name__ == "__main__":
    unittest.main()
