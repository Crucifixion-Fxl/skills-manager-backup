"""Collect beside real fixture/helper as test_hostd_relay_auth_shutdown.py.

Outer collector pins source origins and mandatory Docker Python 3.11.17.
This test also collects on host Python 3.12.3 without interpreter rejection.
"""
import asyncio
import time
import unittest
from unittest import mock

import test_hostd_delivery_notice_wiring as fixture
from hostd.onboarding_runtime import OnboardingRuntime
from low_queue_socket import LowQueueSocket

setUpModule = fixture.setUpModule
tearDownModule = fixture.tearDownModule


def await_chain(coro):
    visited = set()
    while coro is not None and id(coro) not in visited:
        visited.add(id(coro))
        yield coro
        coro = (getattr(coro, 'cr_await', None)
                or getattr(coro, 'gi_yieldfrom', None)
                or getattr(coro, 'ag_await', None))


async def cancel_and_join_owned(tasks):
    pending = {task for task in tasks if not task.done()}
    for _ in range(2):
        if not pending:
            break
        for task in pending:
            task.cancel()
        _, pending = await asyncio.wait(pending, timeout=3)
    if pending:
        raise AssertionError('retained relay test-owned tasks did not join during bounded cleanup')
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


class RelayAuthShutdown(unittest.IsolatedAsyncioTestCase):
    async def test_matching_auth_ok_and_original_shutdown_join_relay_supervisor(self):
        original_methods = {name: getattr(OnboardingRuntime, name)
                            for name in ('run', 'start', 'drain', 'close')}
        original_create = OnboardingRuntime.create.__func__
        world = fixture.WiringWorld(self)
        host = await world.root()
        self.assertIs(type(host), world.hd.Hostd)
        self.assertIs(type(host.onboarding), OnboardingRuntime)
        self.assertIs(OnboardingRuntime.create.__func__, original_create)
        for name, original in original_methods.items():
            self.assertIs(getattr(host.onboarding, name).__func__, original)
        self.assertIsNone(host.onboarding._task)
        lane = world.lanes['initial']
        relay = world.hd.relay_feed
        original_follow = relay.follow
        root_methods = {name: getattr(world.hd.Hostd, name)
                        for name in ('_spawn', 'supervise', '_shutdown', 'on_relay', 'set_status', 'replay_since')}
        mirror_env = lane.cfg['mirror_env_file']
        # Real protected read/parser; real follow reads this exact file again.
        protected_env = relay._env(mirror_env)
        self.assertEqual(fixture.gs._signer_pubkey(protected_env['BUZZ_PRIVATE_KEY']),
                         fixture.base.MIRROR_PK)
        self.assertEqual(host.mirrors[lane.name], fixture.base.MIRROR_PK)
        self.assertEqual(protected_env['BUZZ_RELAY_URL'], 'https://relay.test')
        socket = LowQueueSocket(self, mirror_pubkey=fixture.base.MIRROR_PK,
                                channel=lane.channel, kinds=relay.KINDS,
                                challenge='SYNTHETIC_RELAY_SHUTDOWN_CHALLENGE')
        replay_provider = lambda: host.replay_since(lane.name)

        def connect(url):
            self.assertEqual(url, 'wss://relay.test')
            return socket

        owned = set()
        supervisor = None
        shutdown_box = []
        callback_handle = None
        with mock.patch.object(relay, '_connect', connect):
            try:
                supervisor = host._spawn('relay', lane.name, lambda: original_follow(
                    lane.name, mirror_env, lane.channel, host.on_relay, host.set_status,
                    trusted_relays=host.onboarding.config.trusted_relays,
                    replay_since=replay_provider))
                owned.add(supervisor)
                self.assertEqual(host._tasks, {supervisor})
                deadline = asyncio.get_running_loop().time() + 3
                getter = None
                while getter is None:
                    # 3.11: actual wait_for child task; 3.12+: actual supervisor
                    # await-chain. A real pending Queue.get is mandatory.
                    for task in asyncio.all_tasks():
                        for coro in await_chain(task.get_coro()):
                            frame = getattr(coro, 'cr_frame', None)
                            waiter = task._fut_waiter
                            if (getattr(coro, 'cr_code', None) is asyncio.Queue.get.__code__
                                    and frame is not None
                                    and frame.f_locals.get('self') is socket.queue
                                    and waiter in socket.queue._getters
                                    and not waiter.done()
                                    and socket.auth_event is not None):
                                getter = waiter
                                owned.add(task)
                                break
                        if getter is not None:
                            break
                    if getter is not None:
                        break
                    self.assertFalse(supervisor.done(), 'real relay supervisor ended before AUTH OK waiter')
                    self.assertLess(asyncio.get_running_loop().time(), deadline,
                                    'SETUP: actual relay AUTH OK queue getter never became pending')
                    await asyncio.sleep(0)

                follow_frame = next((getattr(coro, 'cr_frame', None)
                                     for coro in await_chain(supervisor.get_coro())
                                     if getattr(coro, 'cr_code', None) is original_follow.__code__), None)
                self.assertIsNotNone(follow_frame, 'SETUP: original follow frame is required')
                state = follow_frame.f_locals
                self.assertFalse(state['authed'])
                self.assertFalse(state['subscribed'])
                self.assertEqual(state['auth_id'], socket.auth_event['id'])
                self.assertEqual(state['url'], 'wss://relay.test')
                self.assertIs(state['replay_since'], replay_provider)
                self.assertIs(state['on_event'].__self__, host)
                self.assertIs(state['on_event'].__func__, root_methods['on_relay'])
                self.assertIs(state['on_status'].__self__, host)
                self.assertIs(state['on_status'].__func__, root_methods['set_status'])
                self.assertIs(type(state['since']), int)
                self.assertGreaterEqual(state['since'], 0)
                self.assertLessEqual(state['since'], int(time.time()))
                socket.expected_since = state['since']
                remaining = state['auth_deadline'] - asyncio.get_running_loop().time()
                self.assertGreater(remaining, 0, 'SETUP: original 30s AUTH deadline elapsed')
                self.assertLessEqual(remaining, 30)
                self.assertFalse(socket.released_ok)
                self.assertIsNone(socket.request)
                self.assertTrue(host._running)
                self.assertFalse(socket.exited)

                def deliver_ok_then_schedule_original_shutdown():
                    socket.release_actual_matching_ok(getter)
                    shutdown = asyncio.create_task(host._shutdown())
                    shutdown_box.append(shutdown)
                    owned.add(shutdown)

                # One same-loop callback wakes the real pre-auth getter, then
                # schedules the real close/cancel/join ordering. No fake drain,
                # cancellation counter or substituted wait_for is used.
                callback_handle = asyncio.get_running_loop().call_soon(
                    deliver_ok_then_schedule_original_shutdown)
                await asyncio.sleep(0)
                self.assertEqual(len(shutdown_box), 1, 'SETUP: original shutdown was not scheduled')
                shutdown = shutdown_box[0]
                done, pending = await asyncio.wait({shutdown, supervisor}, timeout=3)
                # If the actual relay reached subscription, validate its real
                # root reconnect callback and catch-up filters even on RED.
                if socket.request is not None:
                    self.assertEqual(host.dirty[lane.name], {'notice', 'members', 'buzz', 'feishu'})
                    self.assertIsNone(host.threads[lane.name])
                    self.assertTrue(host.wake[lane.name].is_set())
                    self.assertEqual(host.status['bindings'][lane.name]['relay'], 'connected')
                self.assertFalse(pending,
                                 'original relay AUTH supervisor and root shutdown must both finish within 3s')
                self.assertEqual(done, {shutdown, supervisor})
                self.assertTrue(socket.exited, 'original socket context must exit before shutdown returns')
                self.assertFalse(shutdown.cancelled())
                shutdown.result()
                if not supervisor.cancelled():
                    supervisor.result()
                self.assertIsNone(host.onboarding)
                self.assertIsNone(host.runtime_store)
                self.assertIs(relay.follow, original_follow)
                for name, original in root_methods.items():
                    self.assertIs(getattr(host, name).__func__, original)
            finally:
                if callback_handle is not None:
                    callback_handle.cancel()
                shutdown = shutdown_box[0] if shutdown_box else None
                # After a target RED, this additional cancellation reaches the
                # real authed Queue.get. Only retained owned tasks are joined.
                await cancel_and_join_owned(owned - ({shutdown} if shutdown else set()))
                if shutdown is None:
                    shutdown = asyncio.create_task(host._shutdown())
                    owned.add(shutdown)
                _, pending = await asyncio.wait({shutdown}, timeout=3)
                if pending:
                    await cancel_and_join_owned(pending)
                await asyncio.gather(*owned, return_exceptions=True)
                if socket.entered:
                    self.assertTrue(socket.exited, 'cleanup must reap the original socket context')
                if socket.request is not None:
                    self.assertEqual(socket.close_subscription, socket.subscription)


if __name__ == '__main__':
    unittest.main(verbosity=2)
