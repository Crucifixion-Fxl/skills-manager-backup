"""Observed native target labels use the same fail-closed Fetch path.

CDP wire/process boundary is synthetic; actual guard, event, startup and cleanup
implementations are exercised. Native support remains a separate component gate.
"""
import asyncio
import hashlib
import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parent))
import test_hostd_console_access as fixture
from hostd import console_browser as browser

class NativeTargetTests(unittest.IsolatedAsyncioTestCase):
    module=fixture.ConsoleAccessTests.module
    browser_module=fixture.ConsoleAccessTests.browser_module
    admit=fixture.ConsoleAccessTests.admit
    start=fixture.ConsoleAccessTests.start
    browser_plan=fixture.ConsoleAccessTests.browser_plan
    async def asyncSetUp(self):
        await fixture.ConsoleAccessTests.asyncSetUp(self);await self.start()
    async def asyncTearDown(self):await fixture.ConsoleAccessTests.asyncTearDown(self)
    async def guard_case(self,kind):
        calls=[]
        async def send(session,method,params):calls.append((session,method,params));return {}
        guard=browser.FetchGuard(self.access,send)
        await guard.attach('synthetic-session',target_type=kind)
        self.assertEqual([call[1] for call in calls],['Fetch.enable','Runtime.runIfWaitingForDebugger'])
        self.assertEqual(calls[0][2],{'patterns':[{'urlPattern':'*','requestStage':'Request'}]})
        self.assertEqual(guard._sessions,{'synthetic-session'})
    async def test_observed_background_page_fetch_before_resume(self):
        await self.guard_case('background_page')
    async def test_observed_browser_ui_fetch_before_resume(self):
        await self.guard_case('browser_ui')
    async def test_observed_literal_other_fetch_before_resume(self):
        await self.guard_case('other')
    async def test_existing_page_iframe_and_worker_types_keep_fetch(self):
        for kind in ('page','iframe','worker','service_worker','shared_worker'):
            with self.subTest(kind=kind):await self.guard_case(kind)
    async def test_observed_types_fetch_error_remains_unresumed(self):
        for kind in ('background_page','browser_ui','other'):
            with self.subTest(kind=kind):
                calls=[]
                async def send(session,method,params):
                    calls.append(method)
                    if method=='Fetch.enable':raise browser.BrowserError()
                    return {}
                guard=browser.FetchGuard(self.access,send)
                with self.assertRaises(browser.BrowserError):await guard.attach('failed-session',target_type=kind)
                self.assertEqual(calls,['Fetch.enable']);self.assertEqual(guard._sessions,set())
    async def test_unknown_types_remain_pending_without_commands(self):
        # Reviewed literal `other` now attempts Fetch; arbitrary labels remain rejected.
        for kind in ('tab','browser','unreviewed_target'):
            with self.subTest(kind=kind):
                calls=[]
                async def send(session,method,params):calls.append(method);return {}
                guard=browser.FetchGuard(self.access,send)
                with self.assertRaises(browser.BrowserError):await guard.attach('unknown-session',target_type=kind)
                self.assertEqual(calls,[]);self.assertEqual(guard._sessions,set())
    async def test_actual_attached_event_autoattach_fetch_resume_order(self):
        for kind in ('background_page','browser_ui','other'):
            with self.subTest(kind=kind):
                ops=fixture.BrowserOps();plan=self.browser_plan()
                owned=browser.OwnedBrowser(plan,process_ops=ops)
                owned._guard=browser.FetchGuard(self.access,owned._send)
                await owned._event({'method':'Target.attachedToTarget','params':{'sessionId':'actual-shaped-session',
                    'targetInfo':{'targetId':'actual-shaped-target','type':kind},'waitingForDebugger':True}})
                self.assertEqual(await owned._ready_target('actual-shaped-target'),'actual-shaped-session')
                self.assertEqual([row[1] for row in ops.cdp],['Target.setAutoAttach','Fetch.enable','Runtime.runIfWaitingForDebugger'])
                self.assertEqual(ops.cdp[0][2],{'autoAttach':True,'waitForDebuggerOnStart':True,'flatten':True})
                self.assertFalse(owned._event_failed)
    async def test_one_failed_native_target_blocks_aggregate_startup_navigation(self):
        test=self
        for kind in ('background_page','browser_ui','other','unreviewed_target'):
            with self.subTest(kind=kind):
                class EventOps(fixture.BrowserOps):
                    async def command(self,method,params,*,session=None):
                        if method=='Target.setAutoAttach' and session is None:
                            self.cdp.append((session,method,params))
                            await self.event_sink({'method':'Target.attachedToTarget','params':{'sessionId':'failed-native',
                                'targetInfo':{'targetId':'native-extra','type':kind},'waitingForDebugger':True}})
                            return {}
                        if method=='Fetch.enable' and session=='failed-native':
                            self.cdp.append((session,method,params));raise browser.BrowserError()
                        return await super().command(method,params,session=session)
                ops=EventOps();base=self.browser_plan();ops.sha=hashlib.sha256(base.chrome_binary.read_bytes()).hexdigest()
                plan=browser.BrowserPlan.check(self.access,chrome_binary=base.chrome_binary,chrome_sha256=ops.sha,
                    profile_dir=self.client_dir/('native-'+kind.replace('_','-')))
                self.browser=browser.OwnedBrowser(plan,process_ops=ops)
                with self.assertRaises(browser.BrowserError):await self.browser.start()
                self.assertTrue(self.browser._event_failed);self.assertNotEqual(self.browser.readback()['status'],'connected')
                self.assertFalse(any(method=='Page.navigate' for _,method,_ in ops.cdp))
                self.assertFalse(any(session=='failed-native' and method=='Runtime.runIfWaitingForDebugger' for session,method,_ in ops.cdp))
                self.assertTrue(self.browser.readback()['reaped'])
                attempts=[method for session,method,_ in ops.cdp if session=='failed-native']
                self.assertEqual(attempts,['Target.setAutoAttach','Fetch.enable'] if kind!='unreviewed_target' else ['Target.setAutoAttach'])
    async def test_literal_other_failed_fetch_changes_current_state_to_pending(self):
        class FailedFetchOps(fixture.BrowserOps):
            async def command(self,method,params,*,session=None):
                self.cdp.append((session,method,params))
                if method=='Fetch.enable':raise browser.BrowserError()
                return {}
        ops=FailedFetchOps()
        owned=browser.OwnedBrowser(self.browser_plan(),process_ops=ops)
        owned._guard=browser.FetchGuard(self.access,owned._send)
        owned._status='connected'
        await owned._event({'method':'Target.attachedToTarget','params':{'sessionId':'failed-other',
            'targetInfo':{'targetId':'literal-other-target','type':'other'},'waitingForDebugger':True}})
        self.assertTrue(owned._event_failed)
        self.assertEqual(owned.readback()['status'],'pending')
        self.assertTrue(owned._targets['literal-other-target']['failed'])
        self.assertEqual(owned._guard._sessions,set())
        with self.assertRaises(browser.BrowserError):await owned._ready_target('literal-other-target')
        self.assertEqual([row[1] for row in ops.cdp],['Target.setAutoAttach','Fetch.enable'])
        self.assertFalse(any(row[1] in ('Runtime.runIfWaitingForDebugger','Page.navigate') for row in ops.cdp))
