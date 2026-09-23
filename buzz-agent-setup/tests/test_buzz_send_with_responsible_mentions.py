"""Execution-boundary tests for structured responsible-person sends."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest


SKILL = Path(__file__).resolve().parents[1]
SCRIPT = SKILL / "scripts" / "buzz_send_with_responsible_mentions.py"
CHANNEL = "11111111-2222-3333-4444-555555555555"
SENDER_PRIVATE = "1" * 64
ROOT = "2" * 64
EVENT = "3" * 64
ALICE, BOB, CAROL, DAVE = "a" * 64, "b" * 64, "c" * 64, "d" * 64


def load_module():
    if not SCRIPT.is_file():
        raise AssertionError("buzz_send_with_responsible_mentions.py is required")
    spec = importlib.util.spec_from_file_location("buzz_send_responsible_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PEOPLE = {"alice": ALICE, "bob": BOB, "carol": CAROL, "dave": DAVE}


class FakeSources:
    def candidates(self, locators):
        return [
            {"username": name, "source": "gitlab.reviewers"}
            for name in ("alice", "bob", "carol", "dave")
        ]


class StaticSources:
    def __init__(self, username):
        self.username = username

    def candidates(self, locators):
        return [{"username": self.username, "source": "gitlab.reviewers"}]


class ResponsibleSendBoundaryTest(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        cli = self.root / "buzz-0.5.23" / "buzz"
        cli.parent.mkdir()
        cli.write_bytes(b"\x7fELFtest")
        cli.chmod(0o700)
        sender = self.module.sync.publisher_pubkey_from_private_key(SENDER_PRIVATE)
        self.config = {
            "version": 2,
            "sender_pubkey": sender,
            "state_dir": str(self.root / "state"),
            "gitlab": {
                "base_url": "http://127.0.0.1:8929",
                "token_env": "AGENT_GITLAB_TOKEN",
                "projects": [481],
            },
            "channels": [CHANNEL],
            "buzz": {
                "cli_path": str(cli),
                "cli_sha256": hashlib.sha256(cli.read_bytes()).hexdigest(),
            },
            "people_file": str(self.root / "people.json"),
        }
        self.config_path = self.write("config.json", self.config)
        self.request = {
            "version": 1,
            "channel_id": CHANNEL,
            "reply_to": ROOT,
            "content": "Please review the structured change.",
            "sources": [{
                "kind": "gitlab",
                "project_id": 481,
                "object": "mr",
                "iid": 31,
                "field": "reviewers",
            }],
        }
        self.input_path = self.write("input.json", self.request)
        self.people_path = self.write("people.json", PEOPLE)
        self.env = {
            "BUZZ_PRIVATE_KEY": SENDER_PRIVATE,
            "BUZZ_RELAY_URL": "ws://127.0.0.1:3000",
            "AGENT_GITLAB_TOKEN": "glpat-test-secret",
            "HOME": str(self.root),
            "PATH": "/usr/bin:/bin",
            "POISON": "--mention ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
        }

    def write(self, name, value):
        path = self.root / name
        path.write_text(json.dumps(value), encoding="utf-8")
        path.chmod(0o600)
        return path

    def runner(self, roles=None):
        calls, sent = [], {}
        roles = roles or {}

        def run(argv, **kwargs):
            calls.append((list(argv), kwargs))
            command = tuple(argv[1:3])
            if command == ("channels", "members"):
                body = [
                    {"pubkey": pubkey, "role": roles.get(pubkey, "member")}
                    for pubkey in (ALICE, BOB, CAROL, DAVE)
                ]
            elif command == ("messages", "send"):
                sent["content"] = kwargs["input"]
                sent["mentions"] = [argv[index + 1] for index, value in enumerate(argv) if value == "--mention"]
                body = {"accepted": True, "event_id": EVENT}
            elif command == ("messages", "thread"):
                sent.setdefault("reads", 0)
                sent["reads"] += 1
                if sent["reads"] == 1:
                    body = {"events": []}
                else:
                    body = {"events": [{
                        "id": EVENT,
                        "pubkey": self.config["sender_pubkey"],
                        "kind": 9,
                        "content": sent["content"],
                        "tags": [["h", CHANNEL], ["e", ROOT, "", "reply"],
                                 *[["p", value] for value in sent["mentions"]]],
                    }]}
            else:
                raise AssertionError(argv)
            return SimpleNamespace(returncode=0, stdout=json.dumps(body), stderr="")

        return run, calls, sent

    def test_fresh_resolve_caps_three_records_fourth_and_strictly_reads_back(self):
        """L1-GIS-131 Fresh members/profiles, max3, explicit p tags and strict retry-only readback."""
        run, calls, sent = self.runner()
        result = self.module.execute(
            self.config_path,
            self.input_path,
            env=self.env,
            runner=run,
            source_reader=FakeSources(),
            sleeper=lambda _: None,
        )

        self.assertEqual(result["status"], "sent")
        self.assertEqual(sent["mentions"], [ALICE, BOB, CAROL])
        self.assertIn("未通知：dave(attention_budget)", sent["content"])
        commands = [tuple(call[0][1:3]) for call in calls]
        self.assertEqual(commands.count(("messages", "send")), 1)
        self.assertEqual(commands.count(("messages", "thread")), 2)
        self.assertEqual(commands.count(("channels", "members")), 1)
        self.assertNotIn(("users", "get"), commands)
        for _, kwargs in calls:
            self.assertNotIn("AGENT_GITLAB_TOKEN", kwargs["env"])
            self.assertNotIn("POISON", kwargs["env"])

        retry_run, retry_calls, _ = self.runner()
        # The accepted event is now visible immediately; durable state must
        # verify it without re-reading sources or issuing a second send.
        saved_content = sent["content"]
        saved_mentions = list(sent["mentions"])

        def visible_retry(argv, **kwargs):
            retry_calls.append((list(argv), kwargs))
            self.assertEqual(tuple(argv[1:3]), ("messages", "thread"))
            return SimpleNamespace(returncode=0, stdout=json.dumps({"events": [{
                "id": EVENT,
                "pubkey": self.config["sender_pubkey"],
                "kind": 9,
                "content": saved_content,
                "tags": [["h", CHANNEL], ["e", ROOT, "", "reply"],
                         *[["p", value] for value in saved_mentions]],
            }]}), stderr="")

        duplicate = self.module.execute(
            self.config_path,
            self.input_path,
            env=self.env,
            runner=visible_retry,
            source_reader=FakeSources(),
            sleeper=lambda _: None,
        )
        self.assertEqual(duplicate["status"], "duplicate")
        self.assertEqual(len(retry_calls), 1)

    def test_free_text_source_and_at_all_fail_before_buzz(self):
        """L1-GIS-132 Free text, caller p tags and broadcast names never reach the CLI."""
        for source, content, extra in (
            ({"kind": "free_text", "text": "alice"}, "action", {}),
            (self.request["sources"][0], "please ping @all", {}),
            (self.request["sources"][0], "action", {"mentions": [ALICE]}),
        ):
            request = {**self.request, "sources": [source], "content": content, **extra}
            path = self.write("invalid.json", request)
            calls = []
            with self.subTest(source=source, content=content, extra=extra), self.assertRaises(
                self.module.SendError
            ):
                self.module.execute(
                    self.config_path, path, env=self.env,
                    runner=lambda *a, **k: calls.append((a, k)), source_reader=FakeSources(),
                )
            self.assertEqual(calls, [])

    def test_bot_only_resolution_fails_without_a_message_send(self):
        """L1-GIS-134 A structured source that resolves only to a bot is a zero-send rejection."""
        run, calls, _ = self.runner(roles={DAVE: "bot"})

        with self.assertRaisesRegex(self.module.SendError, "verified human"):
            self.module.execute(
                self.config_path,
                self.input_path,
                env=self.env,
                runner=run,
                source_reader=StaticSources("dave"),
                    sleeper=lambda _: None,
            )

        commands = [tuple(call[0][1:3]) for call in calls]
        self.assertNotIn(("messages", "send"), commands)

    def test_username_missing_from_people_file_or_unreadable_file_never_sends(self):
        """L1-GIS-135 A username the people file cannot place, or a file that is not owner-only, is a zero-send rejection."""
        cases = (
            ("verified human", lambda: self.write("people.json", {"bob": BOB})),
            ("people_file", lambda: self.people_path.chmod(0o644)),
            ("people_file", lambda: self.people_path.unlink()),
            ("humans only", lambda: self.write("people.json", {"alice": self.config["sender_pubkey"]})),
        )
        for message, break_it in cases:
            self.people_path = self.write("people.json", PEOPLE)
            break_it()
            run, calls, _ = self.runner()
            with self.subTest(message=message), self.assertRaisesRegex(self.module.SendError, message):
                self.module.execute(
                    self.config_path, self.input_path, env=self.env, runner=run,
                    source_reader=StaticSources("alice"), sleeper=lambda _: None,
                )
            commands = [tuple(call[0][1:3]) for call in calls]
            self.assertNotIn(("messages", "send"), commands)
            self.assertNotIn(("users", "get"), commands)

    def test_canvas_alias_locator_profile_names_and_channel_admin_config_are_gone(self):
        """L1-GIS-136 Canvas aliases and same-name Buzz profiles are not a source any more; old config is rejected."""
        request = {**self.request, "sources": [{"kind": "canvas_alias", "alias": "alice"}]}
        with self.assertRaisesRegex(self.module.SendError, "source kind must be gitlab or person"):
            self.module.execute(
                self.config_path, self.write("canvas.json", request), env=self.env,
                runner=lambda *a, **k: None, source_reader=FakeSources(),
            )
        legacy = {**self.config, "version": 1, "channels": {CHANNEL: {"canvas_admin_pubkeys": ["e" * 64]}}}
        with self.assertRaises(self.module.SendError):
            self.module.load_config(self.write("legacy.json", legacy))
        # A same-named Buzz profile no longer resolves anyone: the runner has no `users get` branch and would raise.
        run, calls, _ = self.runner()
        self.write("people.json", {})
        with self.assertRaisesRegex(self.module.SendError, "verified human"):
            self.module.execute(
                self.config_path, self.input_path, env=self.env, runner=run,
                source_reader=StaticSources("alice"), sleeper=lambda _: None,
            )

    def test_person_locator_resolves_only_through_the_people_file_and_live_membership(self):
        """L1-GIS-137 A standing-seat username is a lookup key: the pubkey comes from people_file, the role from Buzz."""
        request = {**self.request, "sources": [{"kind": "person", "username": "bob"}]}
        run, calls, sent = self.runner()
        result = self.module.execute(
            self.config_path, self.write("person.json", request), env=self.env, runner=run,
            sleeper=lambda _: None,
        )
        self.assertEqual(result["status"], "sent")
        self.assertEqual(sent["mentions"], [BOB])
        self.assertNotIn(("users", "get"), [tuple(call[0][1:3]) for call in calls])
        for bad in ({"kind": "person", "username": "all"}, {"kind": "person", "username": "b", "pubkey": BOB}):
            with self.subTest(bad=bad), self.assertRaises(self.module.SendError):
                self.module.execute(
                    self.config_path, self.write("bad.json", {**self.request, "sources": [bad]}),
                    env=self.env, runner=lambda *a, **k: None,
                )

    def send_seats(self, names, roles):
        request = {**self.request, "sources": [{"kind": "person", "username": name} for name in names]}
        run, calls, sent = self.runner(roles=roles)
        return request, run, calls, sent

    def test_channel_admin_seat_held_by_a_channel_admin_is_sent_with_its_p_tag(self):
        """L1-GIS-238 定时报告的 `channel_admin` 席位（person locator）上的人在频道里是 admin：status=sent，有对应 p tag，没有 `未通知`。"""
        request, run, calls, sent = self.send_seats(["bob"], {BOB: "admin"})
        result = self.module.execute(
            self.config_path, self.write("seat.json", request), env=self.env, runner=run,
            sleeper=lambda _: None,
        )
        self.assertEqual(result["status"], "sent")
        self.assertEqual(sent["mentions"], [BOB])
        self.assertNotIn("未通知", sent["content"])
        self.assertEqual(result["unresolved"], [])
        self.assertNotIn(("users", "get"), [tuple(call[0][1:3]) for call in calls])

    def test_guest_seat_is_reported_not_notified_and_a_guest_alone_never_sends(self):
        """L1-GIS-239 guest 席位不 @：与 admin 席位同发时正文写 `未通知：…(not_human_member)`；只有 guest 时整条不发。"""
        request, run, calls, sent = self.send_seats(["alice", "bob"], {ALICE: "admin", BOB: "guest"})
        result = self.module.execute(
            self.config_path, self.write("mixed.json", request), env=self.env, runner=run,
            sleeper=lambda _: None,
        )
        self.assertEqual(result["status"], "sent")
        self.assertEqual(sent["mentions"], [ALICE])
        self.assertIn("未通知：bob(not_human_member)", sent["content"])

        request, run, calls, sent = self.send_seats(["bob"], {BOB: "guest"})
        with self.assertRaisesRegex(self.module.SendError, "verified human"):
            self.module.execute(
                self.config_path, self.write("guest.json", request), env=self.env, runner=run,
                sleeper=lambda _: None,
            )
        self.assertNotIn(("messages", "send"), [tuple(call[0][1:3]) for call in calls])

    def test_cli_only_accepts_input_file_not_mentions_or_content(self):
        """L1-GIS-133 Business text and mention pubkeys never enter shell argv."""
        with self.assertRaises(SystemExit):
            self.module.main(["--mention", ALICE], env={})


REFS = SKILL / "references"
CONFIG_EXAMPLE = REFS / "scripts" / "buzz-responsible-mentions.example.json"
HEX64 = "[0-9a-f]{64}"


class ResponsibleConfigExampleTest(unittest.TestCase):
    """The shipped config example is the field/type contract the helper enforces."""

    def setUp(self):
        self.module = load_module()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        cli = self.root / "buzz-0.5.23" / "buzz"
        cli.parent.mkdir()
        cli.write_bytes(b"\x7fELFtest")
        cli.chmod(0o700)
        self.cli = cli

    def example(self):
        self.assertTrue(CONFIG_EXAMPLE.is_file(), "buzz-responsible-mentions.example.json is required")
        return json.loads(CONFIG_EXAMPLE.read_text(encoding="utf-8"))

    def localised(self, config):
        """Swap only the three machine-local values the example cannot know."""
        config = json.loads(json.dumps(config))
        config["state_dir"] = str(self.root / "state")
        config["people_file"] = str(self.root / "people.json")
        config["buzz"] = {
            "cli_path": str(self.cli),
            "cli_sha256": hashlib.sha256(self.cli.read_bytes()).hexdigest(),
        }
        return config

    def write(self, config):
        path = self.root / "config.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        path.chmod(0o600)
        return path

    def test_example_types_match_the_contract(self):
        """L1-GIS-226 Example keys are exactly the contract keys, with the documented JSON types."""
        config = self.example()
        self.assertEqual(set(config), set(self.module.CONFIG_KEYS))
        self.assertIs(type(config["version"]), int)
        self.assertEqual(config["version"], 2)
        self.assertRegex(config["sender_pubkey"], f"^{HEX64}$")
        self.assertTrue(config["state_dir"].startswith("/abs/"))
        self.assertEqual(set(config["gitlab"]), set(self.module.GITLAB_KEYS))
        self.assertIsInstance(config["gitlab"]["base_url"], str)
        self.assertIsInstance(config["gitlab"]["token_env"], str)
        projects = config["gitlab"]["projects"]
        self.assertIsInstance(projects, list)
        self.assertTrue(projects)
        for project in projects:
            self.assertIs(type(project), int)
        self.assertTrue(config["channels"])
        for channel_id in config["channels"]:
            self.assertRegex(channel_id, r"^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$")
        self.assertTrue(config["people_file"].startswith("/abs/"))
        self.assertEqual(set(config["buzz"]), {"cli_path", "cli_sha256"})
        self.assertTrue(config["buzz"]["cli_path"].startswith("/abs/"))
        self.assertRegex(config["buzz"]["cli_sha256"], f"^{HEX64}$")

    def test_example_passes_the_helper_config_validation(self):
        """L1-GIS-227 The example, with only machine-local paths swapped, passes load_config unchanged."""
        config = self.localised(self.example())
        loaded = self.module.load_config(self.write(config))
        self.assertEqual(loaded["version"], 2)
        self.assertEqual(loaded["gitlab"]["projects"], config["gitlab"]["projects"])

    def test_example_rejects_string_version_and_string_project_ids(self):
        """L1-GIS-228 The two documented pitfalls fail closed: version "1" and projects ["<id>"]."""
        base = self.localised(self.example())
        wrong_version = json.loads(json.dumps(base))
        wrong_version["version"] = "2"
        with self.assertRaisesRegex(self.module.SendError, "keys or version do not match the contract"):
            self.module.load_config(self.write(wrong_version))
        wrong_projects = json.loads(json.dumps(base))
        wrong_projects["gitlab"]["projects"] = [str(project) for project in base["gitlab"]["projects"]]
        with self.assertRaisesRegex(self.module.SendError, "gitlab.projects must be a non-empty unique project list"):
            self.module.load_config(self.write(wrong_projects))

    def test_config_example_is_documented_where_operators_look(self):
        """L1-GIS-229 runtime-setup and the scripts README point at the example and state the types and modes."""
        runtime = (REFS / "runtime-setup.md").read_text(encoding="utf-8")
        readme = (REFS / "scripts" / "README.md").read_text(encoding="utf-8")
        name = "buzz-responsible-mentions.example.json"
        self.assertIn(name, runtime)
        self.assertIn(name, readme)
        for needle in (
            "BUZZ_RESPONSIBLE_CONFIG",
            "`version` 是整数 `2`",
            "`projects` 是整数 project id 列表",
            "写成字符串会被拒绝",
            "0600",
            "0700",
        ):
            self.assertIn(needle, runtime, needle)


if __name__ == "__main__":
    unittest.main()
