"""Process and CLI acceptance with a local fake CLI; no network credentials."""
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from core import Store, build_request
from listener import listen, stream_events
from transport import Lark, send_request
from daemon import verify_bot


class Runtime(unittest.TestCase):
    def test_live_cli_bot_info_flat_data_shape(self):
        lark = Lark({"node":"/node","entry":"/entry","profile":"personal","app_id":"app","owner_id":"ou_owner","owner_name":"Owner","bot_id":"ou_bot"})
        with patch.object(lark,"verify_user"), patch.object(lark,"call",return_value={"activate_status":2,"open_id":"ou_bot"}):
            verify_bot(lark)
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self.tmp.name)
        self.store = Store(self.directory / "s.sqlite")
        self.store.bind("s", "app", "ou_owner", "oc_dm", "om_root")
        self.event = {"type": "im.message.receive_v1", "message_id": "om_reply", "chat_id": "oc_dm", "chat_type": "p2p",
                      "sender_type": "user", "sender_id": "ou_owner", "root_id": "om_root", "content": "继续"}

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_malformed_event_is_visible_and_next_valid_event_routes(self):
        statuses = list(stream_events(self.store, "app", ["not json", "[]", json.dumps(self.event)]))
        self.assertEqual(statuses, ["malformed", "malformed", "queued"])

    def test_sqlite_is_private(self):
        self.assertEqual((self.directory / "s.sqlite").stat().st_mode & 0o777, 0o600)

    def test_bot_requires_explicit_transport_policy(self):
        lark = Lark({"node": "/node", "entry": "/entry", "profile": "personal", "app_id": "app", "owner_id": "ou_owner", "owner_name": "Owner"})
        with self.assertRaises(ValueError):
            listen(self.store, lark)

    def test_missing_runtime_configuration_has_actionable_error(self):
        with self.assertRaises(ValueError):
            Lark(None)

    def test_listener_ready_open_stdin_and_exact_profile(self):
        config = {"node": sys.executable, "entry": "/fake.py", "profile": "personal", "app_id": "app", "owner_id": "ou_owner", "owner_name": "Owner", "bot_transport_approved": True}
        lark = Lark(config)
        captured = []
        class Process:
            stdin = io.StringIO()
            stdout = io.StringIO(json.dumps(self.event) + "\n")
            stderr = io.StringIO("[event] ready event_key=im.message.receive_v1\n")
            def wait(self, timeout):
                return 0
        proc = Process()
        def factory(argv, **kwargs):
            captured.append((argv, kwargs))
            return proc
        with patch.object(lark, "verify_user"), patch("listener.Path.home", return_value=self.directory), patch("sys.stdout", new_callable=io.StringIO):
            listen(self.store, lark, factory)
        argv = captured[0][0]
        self.assertEqual(argv[:4], [sys.executable, "/fake.py", "--profile", "personal"])
        self.assertEqual(argv[-2:], ["--as", "bot"])
        self.assertEqual(captured[0][1]["stdin"], subprocess.PIPE)
        self.assertTrue(proc.stdin.closed)
        self.assertEqual(self.store.inbox("s")[0]["body"], "继续")

    def test_local_cli_replay_inbox_ack(self):
        def cli(args, stdin=None):
            result = subprocess.run([sys.executable, str(SCRIPTS / "collab.py"), "--db", str(self.directory / "s.sqlite"), *args], input=stdin, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            return [json.loads(line) for line in result.stdout.splitlines()]
        result = cli(["replay", "--app", "app"], json.dumps(self.event) + "\n")
        self.assertEqual(result[0]["status"], "queued")
        entry = cli(["inbox", "--session", "s"])[0]["entries"][0]
        cli(["ack", "--session", "s", "--id", str(entry["id"])])
        self.assertEqual(cli(["inbox", "--session", "s"])[0]["entries"], [])

    def test_send_request_cli_rejects_without_baseline_before_network(self):
        p = build_request({"kind": "gitlab_issue", "url": "https://gitlab.addx.ai/t/r/-/issues/7"}, "ou_person", "背景", "需求", "证据", "r1")
        config = {"node": "/does-not-exist", "entry": "/also-missing", "profile": "personal", "app_id": "app", "owner_id": "ou_owner", "owner_name": "Owner"}
        cfg = self.directory / "config.json"
        cfg.write_text(json.dumps(config))
        result = subprocess.run([sys.executable, str(SCRIPTS / "collab.py"), "--db", str(self.directory / "s.sqlite"), "--config", str(cfg), "send-request", "--session", "s"], input=json.dumps(p), text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("完整基线", result.stderr)

    def test_user_payload_is_stdin_not_shell_code(self):
        config = {"node": "/node", "entry": "/entry", "profile": "personal", "app_id": "app", "owner_id": "ou_owner", "owner_name": "Owner"}
        lark = Lark(config)
        text = '`touch /tmp/should-not-exist` $(secret) "quotes"\nhello'
        with patch("transport.subprocess.run", return_value=subprocess.CompletedProcess([], 0, '{"ok":true,"identity":"user","data":{}}', "")) as run:
            lark.call(["im", "+messages-send", "--text", "-"], "user", text)
        self.assertEqual(run.call_args.kwargs["input"], text)
        self.assertNotIn("shell", run.call_args.kwargs)
        self.assertNotIn(text, str(run.call_args.args[0]))

    def test_cli_user_success_requires_matching_identity(self):
        lark = Lark({"node": "/node", "entry": "/entry", "profile": "personal", "app_id": "app", "owner_id": "ou_owner", "owner_name": "Owner"})
        for envelope in [{"ok": True, "identity": "bot", "data": {}}, {"code": 0, "data": {}}, {"ok": False, "identity": "user"}]:
            with patch("transport.subprocess.run", return_value=subprocess.CompletedProcess([], 0, json.dumps(envelope), "")), self.assertRaises(ValueError):
                lark.call(["task", "tasks", "get"], "user")

    def test_write_timeout_is_unknown_and_not_retried(self):
        p = build_request({"kind": "gitlab_issue", "url": "https://gitlab.addx.ai/t/r/-/issues/7"}, "ou_person", "背景", "需求", "证据", "r1")
        class Transport:
            count = 0
            def verify_user(self):
                pass
            def call(self, *args):
                self.count += 1
                raise subprocess.TimeoutExpired("fake", 1)
        fake = Transport()
        self.assertEqual(send_request(fake, p)["status"], "outcome_unknown")
        self.assertEqual(fake.count, 1)


if __name__ == "__main__":
    unittest.main()
