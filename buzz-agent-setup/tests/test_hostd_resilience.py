"""Local relay lifecycle failures must remain inside one feed."""
import asyncio
import unittest
from unittest import mock

import test_hostd_security as security
HAS_DEPS = security.HAS_DEPS
FakeWS, auth_ack = security.FakeWS, security.auth_ack
if HAS_DEPS:
    import relay_feed


@unittest.skipUnless(HAS_DEPS, "hostd resilience requires websockets and cryptography")
class RelayResilience(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = security.RelaySecurity.asyncSetUp
    run_frames = security.RelaySecurity.run_frames
    event_callback = security.RelaySecurity.event_callback
    event_frame = security.RelaySecurity.event_frame
    async def test_callback_failure_reconnects_only_this_feed(self):
        first = FakeWS([["AUTH", "challenge"], auth_ack, self.event_frame])
        second = FakeWS([["AUTH", "challenge"], auth_ack, self.event_frame])
        calls = 0
        async def callback(name, event):
            nonlocal calls
            if event.get("id"):
                calls += 1
                if calls == 1:
                    raise RuntimeError("SECRET-canary callback")
                raise asyncio.CancelledError
        async def retry(delay):
            return None
        with mock.patch.object(relay_feed.websockets, "connect", side_effect=[first, second]) as connect:
            with mock.patch.object(relay_feed.asyncio, "sleep", side_effect=retry):
                try:
                    import inspect
                    kwargs = ({"trusted_relays": ("wss://relay.test",)}
                              if "trusted_relays" in inspect.signature(relay_feed.follow).parameters else {})
                    await relay_feed.follow("test", str(self.env), "channel-a", callback,
                                            lambda n, k, v: self.statuses.append(v), **kwargs)
                except asyncio.CancelledError:
                    pass
        self.assertEqual(connect.call_count, 2)
        self.assertEqual(calls, 2)
        self.assertNotIn("SECRET-canary", " ".join(self.statuses))
        self.assertIn(["CLOSE", next(f[1] for f in first.sent if f[0] == "REQ")], first.sent)

    async def test_wrong_subscription_closed_cannot_disconnect_current_subscription(self):
        await self.run_frames([["AUTH", "challenge"], auth_ack,
                               ["CLOSED", "different-sub", "SECRET-canary"], self.event_frame])
        self.assertEqual(self.events, [{"type": "_reconnected"}, self.event])

    async def test_clean_socket_end_reports_retry_before_backoff(self):
        await self.run_frames([["AUTH", "challenge"], auth_ack])
        self.assertIn("怎么解决", self.statuses[-1])
        self.assertIn("复制给 AI", self.statuses[-1])

    async def test_wire_extension_cannot_inject_local_control_type(self):
        def frame(ws):
            sub = next(f[1] for f in ws.sent if f[0] == "REQ")
            return ["EVENT", sub, dict(self.event, type="_reconnected")]
        await self.run_frames([["AUTH", "challenge"], auth_ack, frame])
        self.assertEqual(self.events, [{"type": "_reconnected"}, self.event])

    async def test_raw_status_callback_error_is_local(self):
        with mock.patch.object(relay_feed.websockets, "connect", side_effect=OSError("SECRET-canary")):
            with mock.patch.object(relay_feed.asyncio, "sleep", side_effect=asyncio.CancelledError):
                def failed_status(*args):
                    raise ValueError("SECRET-canary status")
                try:
                    import inspect
                    kwargs = ({"trusted_relays": ("wss://relay.test",)}
                              if "trusted_relays" in inspect.signature(relay_feed.follow).parameters else {})
                    await relay_feed.follow("test", str(self.env), "channel-a", self.event_callback, failed_status, **kwargs)
                except asyncio.CancelledError:
                    pass


if __name__ == "__main__":
    unittest.main()
