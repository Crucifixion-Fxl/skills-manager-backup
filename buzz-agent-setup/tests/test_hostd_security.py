"""Offline S0 security contracts; no live identities or network endpoints."""
import asyncio
import importlib.util
import inspect
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HOSTD = Path(__file__).resolve().parents[1] / "scripts" / "hostd"
sys.path.insert(0, str(HOSTD))
sys.path.insert(0, str(HOSTD.parent))
import registry

HAS_DEPS = bool(importlib.util.find_spec("cryptography") and importlib.util.find_spec("websockets"))
if HAS_DEPS:
    import relay_feed
    import secrets_store
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def private_file(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value if isinstance(value, bytes) else value.encode())
    path.chmod(0o600)
    return path


class RegistrySecurity(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def config(self, name, channel="channel-a", chat="oc_a", app="cli_a", profile="a"):
        env = private_file(self.root / name / "mirror.env", "BUZZ_RELAY_URL=https://relay.test\n")
        cfg = {"chat_id": chat, "channel_id": channel, "desk_pubkey": "desk",
               "agents": {"desk": {"app_id": app, "lark_config_dir": "/cfg/" + profile,
                                    "lark_data_dir": "/data/" + profile}}, "mirror_env_file": str(env)}
        return private_file(self.root / name / "config.json", json.dumps(cfg))

    def assert_notice(self, notice):
        self.assertIn("怎么解决", notice)
        self.assertIn("复制给 AI", notice)

    def test_duplicate_chat_rejects_both_without_arbitrary_winner(self):
        self.config("a")
        self.config("b", channel="channel-b")
        reg = registry.load(self.root)
        self.assertFalse(reg.bindings)
        self.assertEqual(set(reg.skipped), {"a", "b"})
        for notice in reg.skipped.values():
            self.assert_notice(notice)

    def test_duplicate_channel_rejects_both(self):
        self.config("a")
        self.config("b", chat="oc_b")
        self.assertFalse(registry.load(self.root).bindings)

    def test_conflicting_app_profile_rejects_all_affected_bindings(self):
        self.config("a")
        self.config("b", channel="channel-b", chat="oc_b", profile="b")
        self.assertFalse(registry.load(self.root).bindings)

    def test_one_app_may_group_distinct_bindings_using_same_profile(self):
        self.config("a")
        self.config("b", channel="channel-b", chat="oc_b")
        self.assertEqual(len(registry.load(self.root).by_app()["cli_a"]), 2)

    def test_malformed_config_is_local_and_sanitized(self):
        self.config("good")
        path = self.config("bad", channel="channel-b", chat="oc_b")
        path.write_text('{"SECRET-canary": broken')
        reg = registry.load(self.root)
        self.assertEqual(set(reg.bindings), {"good"})
        self.assert_notice(reg.skipped["bad"])
        self.assertNotIn("SECRET-canary", reg.skipped["bad"])

    def test_incomplete_shape_does_not_abort_other_bindings(self):
        self.config("good")
        self.config("bad").write_text('["SECRET-canary"]')
        reg = registry.load(self.root)
        self.assertEqual(set(reg.bindings), {"good"})
        self.assert_notice(reg.skipped["bad"])

    def test_parser_recursion_error_is_local(self):
        self.config("good")
        self.config("bad").write_text("[" * 1500 + "0" + "]" * 1500)
        original = json.loads
        def parse(value):
            if value.startswith(b"[["):
                raise RecursionError("SECRET-canary")
            return original(value)
        with mock.patch.object(registry.json, "loads", side_effect=parse):
            reg = registry.load(self.root)
        self.assertEqual(set(reg.bindings), {"good"})
        self.assert_notice(reg.skipped["bad"])

    def test_unsafe_mirror_env_is_rejected(self):
        self.config("a")
        (self.root / "a" / "mirror.env").chmod(0o644)
        reg = registry.load(self.root)
        self.assertFalse(reg.bindings)
        self.assert_notice(reg.skipped["a"])

    def test_symlink_config_is_rejected(self):
        path = self.config("a")
        real = path.with_name("actual.json")
        path.rename(real)
        path.symlink_to(real)
        self.assertFalse(registry.load(self.root).bindings)

    def test_nonowner_config_is_rejected(self):
        self.config("a")
        with mock.patch.object(os, "geteuid", return_value=os.geteuid() + 1):
            self.assertFalse(registry.load(self.root).bindings)


@unittest.skipUnless(HAS_DEPS, "hostd security requires cryptography and websockets")
class SecretsSecurity(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.cfg = self.root / "cfg"
        self.data = self.root / "data"
        self.key = bytes(range(32))
        self.secret = "SECRET-canary-123"
        self.config = private_file(self.cfg / "config.json", json.dumps({"apps": [{"appId": "cli_a"}]}))
        self.master = private_file(self.data / "lark-cli" / "master.key", self.key)
        nonce = bytes(range(12))
        self.blob = private_file(self.master.with_name("appsecret_cli_a.enc"),
                                 nonce + AESGCM(self.key).encrypt(nonce, self.secret.encode(), None))

    def read(self):
        return secrets_store.app_secret("cli_a", str(self.cfg), str(self.data))

    def assert_safe_failure(self):
        with self.assertRaises(SystemExit) as caught:
            self.read()
        message = str(caught.exception)
        self.assertIn("怎么解决", message)
        self.assertIn("复制给 AI", message)
        self.assertNotIn(self.secret, message)
        self.assertNotIn(str(self.root), message)

    def test_private_regular_store_decrypts(self):
        self.assertEqual(self.read(), self.secret)

    def test_each_credential_file_requires_exact_0600(self):
        for path in (self.config, self.master, self.blob):
            for mode in (0o644, 0o400, 0o700):
                with self.subTest(file=path.name, mode=mode):
                    path.chmod(mode)
                    self.assert_safe_failure()
                    path.chmod(0o600)

    def test_each_credential_file_rejects_symlinks(self):
        for path in (self.config, self.master, self.blob):
            with self.subTest(file=path.name):
                real = path.with_name(path.name + ".real")
                path.rename(real)
                path.symlink_to(real)
                self.assert_safe_failure()
                path.unlink()
                real.rename(path)

    def test_parent_symlink_is_rejected(self):
        real = self.data.with_name("actual-data")
        self.data.rename(real)
        self.data.symlink_to(real, target_is_directory=True)
        self.assert_safe_failure()

    def test_wrong_owner_is_rejected(self):
        with mock.patch.object(os, "geteuid", return_value=os.geteuid() + 1):
            self.assert_safe_failure()

    def test_invalid_ciphertext_has_sanitized_failure(self):
        self.blob.write_bytes(b"SECRET-canary-corrupted")
        self.assert_safe_failure()

    def test_malformed_profile_has_sanitized_failure(self):
        self.config.write_text('{"SECRET-canary":broken')
        self.assert_safe_failure()

    def test_app_identifier_cannot_escape_store(self):
        self.config.write_text(json.dumps({"apps": [{"appId": "../SECRET-canary"}]}))
        with self.assertRaises(SystemExit) as caught:
            secrets_store.app_secret("../SECRET-canary", str(self.cfg), str(self.data))
        self.assertNotIn("SECRET-canary", str(caught.exception))

    def test_fd_read_does_not_reopen_replaced_path(self):
        import safety
        path = private_file(self.root / "race", "original")
        replacement = private_file(self.root / "replacement", "SECRET-canary")
        original_fstat = os.fstat
        def replacing(fd):
            meta = original_fstat(fd)
            if meta.st_ino == path.stat().st_ino:
                replacement.replace(path)
            return meta
        with mock.patch.object(os, "fstat", side_effect=replacing):
            self.assertEqual(safety.read_owned(path), b"original")


class FakeWS:
    def __init__(self, frames):
        self.frames = frames
        self.sent = []
        self.process_redirect = lambda exc: exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.frames:
            raise StopAsyncIteration
        frame = self.frames.pop(0)
        if callable(frame):
            frame = frame(self)
        return frame if isinstance(frame, str) else json.dumps(frame)

    async def send(self, payload):
        self.sent.append(json.loads(payload))


def auth_ack(ws, value=True, event_id=None):
    event = next(frame[1] for frame in reversed(ws.sent) if frame[0] == "AUTH")
    return ["OK", event["id"] if event_id is None else event_id, value, "SECRET-canary"]


@unittest.skipUnless(HAS_DEPS, "hostd security requires cryptography and websockets")
class RelaySecurity(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.key = "0" * 63 + "1"
        self.env = private_file(self.root / "mirror.env", "BUZZ_PRIVATE_KEY=" + self.key + "\nBUZZ_RELAY_URL=wss://relay.test\n")
        self.events = []
        self.statuses = []
        self.event = relay_feed.gs.sign_event(self.key, 9, [["h", "channel-a"]], "test", 100)

    async def event_callback(self, name, event):
        self.events.append(event)

    async def run_frames(self, frames, *, trust=True, on_event=None):
        self.ws = FakeWS(frames)
        with mock.patch.object(relay_feed.websockets, "connect", return_value=self.ws) as connect:
            with mock.patch.object(relay_feed.asyncio, "sleep", side_effect=asyncio.CancelledError):
                try:
                    args = ({"trusted_relays": ("wss://relay.test",)}
                            if trust and "trusted_relays" in inspect.signature(relay_feed.follow).parameters else {})
                    await relay_feed.follow("test", str(self.env), "channel-a", on_event or self.event_callback,
                                            lambda n, k, v: self.statuses.append(v), **args)
                except asyncio.CancelledError:
                    pass
        return connect

    def event_frame(self, ws):
        sub = next(f[1] for f in ws.sent if f[0] == "REQ")
        return ["EVENT", sub, self.event]

    async def test_arbitrary_host_is_rejected_before_connect_or_sign(self):
        with mock.patch.object(relay_feed.gs, "sign_event") as sign:
            connect = await self.run_frames([], trust=False)
        connect.assert_not_called()
        sign.assert_not_called()
        self.assertIn("怎么解决", self.statuses[0])
        self.assertIn("复制给 AI", self.statuses[0])

    async def test_nonloopback_plain_transport_is_rejected_even_when_allowlisted(self):
        with self.assertRaises(ValueError):
            relay_feed._ws_url("ws://relay.test", ("ws://relay.test",))
        self.env.write_text("BUZZ_PRIVATE_KEY=" + self.key + "\nBUZZ_RELAY_URL=ws://relay.test\n")
        connect = await self.run_frames([])
        connect.assert_not_called()

    async def test_exact_origin_rejects_credentials_paths_queries_and_fragments(self):
        for url in ("wss://user:SECRET-canary@relay.test", "wss://relay.test/path", "wss://relay.test?x",
                    "wss://relay.test#x", "wss://relay.test.evil.test", "wss://relay.test:99",
                    "wss://relay.test\\evil.test", "wss://relay.test\n", "wss://RELAY.test"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                relay_feed._ws_url(url, ("wss://relay.test",))

    async def test_explicit_allowlist_and_loopback_are_canonicalized(self):
        self.assertEqual(relay_feed._ws_url("https://relay.test", ("wss://relay.test",)), "wss://relay.test")
        self.assertEqual(relay_feed._ws_url("http://127.0.0.1:1234"), "ws://127.0.0.1:1234")
        self.assertEqual(relay_feed._ws_url("ws://[::1]:1234"), "ws://[::1]:1234")
        self.assertEqual(relay_feed._ws_url("ws://localhost:1234"), "ws://localhost:1234")

    async def test_handshake_redirect_cannot_reach_another_origin(self):
        from websockets.http11 import Response
        from websockets.datastructures import Headers
        from websockets.exceptions import InvalidStatus
        factory = getattr(relay_feed, "_connect", relay_feed.websockets.connect)
        for target in ("wss://evil.test", "wss://relay.test:444", "wss://relay.test/path", "ws://relay.test"):
            with self.subTest(target=target):
                connector = factory("wss://relay.test")
                redirect = InvalidStatus(Response(302, "Found", Headers({"Location": target})))
                result = connector.process_redirect(redirect)
                self.assertIsInstance(result, Exception)

    async def test_unsafe_env_is_rejected_before_connect(self):
        self.env.chmod(0o644)
        connect = await self.run_frames([])
        connect.assert_not_called()

    async def test_malformed_frames_survive_locally_until_valid_event(self):
        bad = ["broken JSON", [], {}, None, True, ["AUTH"], ["AUTH", {}], ["OK"], ["OK", "x", True],
               ["EVENT"], ["CLOSED"], ["NOTICE", "SECRET-canary"]]
        await self.run_frames(bad + [["AUTH", "challenge"], auth_ack, self.event_frame])
        self.assertEqual(self.events, [{"type": "_reconnected"}, self.event])
        self.assertNotIn("SECRET-canary", " ".join(self.statuses))

    async def test_only_matching_auth_id_and_strict_true_can_subscribe(self):
        frames = [["AUTH", "challenge"], lambda ws: auth_ack(ws, event_id="f" * 64),
                  lambda ws: auth_ack(ws, value="true"), lambda ws: auth_ack(ws, value=1), auth_ack, self.event_frame]
        await self.run_frames(frames)
        self.assertEqual(sum(f[0] == "REQ" for f in self.ws.sent), 1)
        self.assertEqual(len(self.events), 2)

    async def test_event_before_auth_is_ignored(self):
        await self.run_frames([["EVENT", "guessed", self.event], ["AUTH", "challenge"], auth_ack, self.event_frame])
        self.assertEqual(len(self.events), 2)

    async def test_arbitrary_event_dictionary_cannot_trigger_reconnected(self):
        def bad(ws):
            sub = next(f[1] for f in ws.sent if f[0] == "REQ")
            return ["EVENT", sub, {"type": "_reconnected"}]
        await self.run_frames([["AUTH", "challenge"], auth_ack, bad, self.event_frame])
        self.assertEqual(self.events, [{"type": "_reconnected"}, self.event])

    async def test_invalid_signature_wrong_channel_or_kind_never_reaches_callback(self):
        invalid = [dict(self.event, sig="0" * 128),
                   relay_feed.gs.sign_event(self.key, 9, [["h", "other"]], "test", 100),
                   relay_feed.gs.sign_event(self.key, 30177, [["h", "channel-a"]], "test", 100)]
        def frame(event):
            return lambda ws: ["EVENT", next(f[1] for f in ws.sent if f[0] == "REQ"), event]
        await self.run_frames([["AUTH", "challenge"], auth_ack] + [frame(e) for e in invalid] + [self.event_frame])
        self.assertEqual(self.events, [{"type": "_reconnected"}, self.event])

    async def test_auth_rejection_is_sanitized(self):
        await self.run_frames([["AUTH", "challenge"], lambda ws: auth_ack(ws, value=False)])
        self.assertFalse(any(f[0] == "REQ" for f in self.ws.sent))
        self.assertNotIn("SECRET-canary", " ".join(self.statuses))
        self.assertTrue(all("怎么解决" in s and "复制给 AI" in s for s in self.statuses))

    async def test_auth_wire_contains_signed_event_and_never_private_key(self):
        await self.run_frames([["AUTH", "challenge"], auth_ack])
        auth = next(f[1] for f in self.ws.sent if f[0] == "AUTH")
        self.assertEqual(auth["kind"], 22242)
        self.assertTrue(relay_feed.gs._nip01_event_verified(auth))
        self.assertNotIn(self.key, json.dumps(self.ws.sent))

    async def test_cancellation_unsubscribes_active_subscription(self):
        async def cancel(name, event):
            if event.get("id"):
                raise asyncio.CancelledError
        await self.run_frames([["AUTH", "challenge"], auth_ack, self.event_frame], on_event=cancel)
        sub = next(f[1] for f in self.ws.sent if f[0] == "REQ")
        self.assertIn(["CLOSE", sub], self.ws.sent)


if __name__ == "__main__":
    unittest.main()
