"""Observable loop progress through real onboarding adapters; offline I/O only.

A 3.2-second transport delay must not delay an independent callback past the
three-second callback budget. No production CLI, HTTP or systemd is executed.
The signed-read wall timer is deliberately neither patched nor disabled.
"""
import asyncio
import json
import signal
import subprocess
import sys
import time
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(TESTS.parent / 'scripts'))
import test_hostd_onboarding_runtime as fixture
from hostd import join_effects as effects
from hostd.onboarding import Operator

DELAY = 3.2
CALLBACK_SECONDS = 3.0


class SlowOnce:
    """Inject delay at the low-level transport, without replacing the adapter."""
    def __init__(self, call, predicate):
        self.call, self.predicate = call, predicate
        self.dispatched = 0

    def __call__(self, *args, **kwargs):
        if not self.dispatched and self.predicate(*args, **kwargs):
            self.dispatched += 1
            time.sleep(DELAY)
        return self.call(*args, **kwargs)


@unittest.skipUnless(fixture.fixture.AESGCM is not None,
                     'real protected catalog needs cryptography; dependency CI runs all cases')
class Responsiveness(unittest.IsolatedAsyncioTestCase):
    # Reuse offline protected fixtures, not their tests or business adapters.
    setUp = fixture.RuntimeTests.setUp
    create = fixture.RuntimeTests.create
    bound = fixture.RuntimeTests.bound

    def callback(self, service):
        row = self.db.join_request('JOIN-12345678')
        return service.enqueue_feed('cli_agent', {
            'type': 'card.action.trigger', 'app': 'cli_agent', 'event_id': 'evt_independent',
            'operator': {'open_id': 'ou_owner', 'union_id': 'on_owner'},
            'context': {'open_chat_id': 'oc_group', 'open_message_id': row['card_message_id']},
            'action': {'value': {'request_id': row['request_id'],
                                  'generation': row['card_generation'], 'decision': 'approve'}}})

    def request(self, *, approved=False):
        self.db.create_join('JOIN-12345678', self.f.pub, self.f.owner, 'cli_agent',
                            'oc_group', kind='channel', binding_id='alpha', now=1000)
        gen = self.db.rotate_card('JOIN-12345678', 'om_current', now=1000)
        if approved:
            self.assertTrue(self.db.decide_join('JOIN-12345678', 'evt_owner', self.f.owner,
                'cli_agent', 'om_current', gen, approved=True, now=1000))
        return self.db.join_request('JOIN-12345678')

    async def progress(self, action, delayed, *, service=None):
        # The observer is armed before the actual operation; the elapsed time
        # includes an event-loop callback, not a fake transport's own timer.
        observed = {}
        async def observer():
            observed['armed'] = time.monotonic()
            await asyncio.sleep(0.05)
            observed['heartbeat'] = time.monotonic() - observed['armed']
            if service is not None:
                observed['toast'] = self.callback(service)
                observed['callback'] = time.monotonic() - observed['armed']
        task = asyncio.create_task(observer())
        await asyncio.sleep(0)
        previous_timer = signal.getitimer(signal.ITIMER_REAL)
        try:
            result = await action()
        finally:
            await task
        self.assertEqual(delayed.dispatched, 1, 'must exercise the actual delayed I/O path')
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), previous_timer,
                         'actual signed-read timer must be restored')
        if service is not None:
            self.assertEqual(observed['toast']['toast']['type'], 'info')
            self.assertLess(observed['callback'], CALLBACK_SECONDS,
                            'independent real card callback exceeded three seconds')
        self.assertLess(observed['heartbeat'], CALLBACK_SECONDS,
                        'independent loop heartbeat exceeded three seconds')
        return result

    async def test_factory_signed_read_keeps_heartbeat_responsive(self):
        slow = SlowOnce(self.w.http, lambda url, *a, **kw: url.endswith('/query'))
        self.w.http = slow
        service = await self.progress(self.create, slow)
        self.assertEqual(service.eligible_apps, ('cli_agent',))

    async def test_discovery_signed_read_keeps_card_callback_responsive(self):
        self.bound(); service = await self.create(); self.request()
        slow = SlowOnce(service.http, lambda url, *a, **kw: url.endswith('/query'))
        service.http = slow; service.relay.http = slow
        answer = await self.progress(lambda: service.discover('cli_agent', 'oc_group'), slow,
                                     service=service)
        self.assertEqual(answer.status, 'bound')
        self.assertEqual(self.db.join_request('JOIN-12345678')['status'], 'requested')

    async def test_fresh_identity_people_bridge_keeps_card_callback_responsive(self):
        self.bound(); service = await self.create(); self.request()
        slow = SlowOnce(service.http, lambda url, *a, **kw: '/people' in url)
        service.http = slow; service.relay.http = slow
        answer = await self.progress(lambda: service.identity.resolve(
            'cli_agent', Operator('ou_owner', 'on_owner'), now=1000), slow, service=service)
        self.assertEqual(answer.pubkey, self.f.owner)
        self.assertEqual(self.db.join_request('JOIN-12345678')['status'], 'requested')

    def bound_effect(self, service):
        # Existing bound request and actual protected config, not an invented
        # effect receipt. Only readback is exercised against these fixture files.
        cfg = json.loads((self.f.root / 'bound.json').read_text())
        cfg['sync_app_id'] = 'cli_agent'
        self.f.write(self.f.root / 'bound.json', json.dumps(cfg))
        (self.f.root / 'state').mkdir(mode=0o700)
        self.f.write(self.f.prompt, effects.legacy.PROMPT_BEGIN + '\n' + fixture.CHANNEL +
                     '\n' + effects.legacy.PROMPT_END)
        self.f.write(self.f.responsible, json.dumps({'channels': [fixture.CHANNEL]}))
        row = self.request(approved=True)
        spec = service.effects.specs[self.f.pub]
        service.effects._plan(row, spec)
        return row

    async def test_effect_apply_bot_roster_keeps_card_callback_responsive(self):
        self.bound(); service = await self.create(); row = self.bound_effect(service)
        # The real BotLarkCli/member normalization executes before the signed
        # membership mutation. Incomplete roster stops before any outward write.
        self.w.complete = False
        slow = SlowOnce(self.w.runner, lambda argv, **kw: '+chat-members-list' in argv)
        service.clients['cli_agent'].runner = slow
        async def apply():
            with self.assertRaises(effects.EffectError):
                await service.effects.apply(row)
        await self.progress(apply, slow, service=service)
        self.assertEqual(self.db.join_request(row['request_id'])['status'], 'approved')
        self.assertFalse(any(url.endswith('/event') for url, _ in self.w.http_calls))

    async def test_effect_readback_actual_process_transport_keeps_card_callback_responsive(self):
        self.bound(); service = await self.create(); row = self.bound_effect(service)
        roster = fixture.gs.sign_event(self.w.relay_key, 39002,
            [['d', fixture.CHANNEL], ['p', self.f.owner, '', 'owner'],
             ['p', self.f.pub, '', 'bot']], '', 1000)
        original_http = service.http
        def http(url, headers, timeout, body=None):
            result = original_http(url, headers, timeout, body)
            if url.endswith('/query') and json.loads(body)[0].get('kinds') == [39002]:
                return 200, json.dumps([roster]).encode()
            return result
        service.relay.http = http
        process_calls = []
        def process_runner(argv, **kw):
            # Actual ProcessOps.unit_status/process call dispatch, never systemd.
            process_calls.append(argv)
            if 'MainPID' in argv:
                return subprocess.CompletedProcess(argv, 1, '', '')
            return subprocess.CompletedProcess(argv, 0, 'LoadState=loaded\nActiveState=active\n', '')
        slow = SlowOnce(process_runner, lambda argv, **kw: 'MainPID' in argv)
        service.effects.runtime = effects.AgentRuntime(effects.ProcessOps(runner=slow, base_env={}))
        answer = await self.progress(lambda: service.effects.readback(row), slow, service=service)
        self.assertFalse(answer.verified)
        self.assertTrue(process_calls)
        self.assertEqual(self.db.join_request(row['request_id'])['status'], 'approved')


if __name__ == '__main__':
    unittest.main()
