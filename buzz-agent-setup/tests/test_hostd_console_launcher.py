"""Console handoff only claims page/graph evidence after browser readback."""
import asyncio
import contextlib
import io
import os
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from hostd import console_launcher as launcher
from hostd.console_access import AccessError


class VerifyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.access = SimpleNamespace(origin='http://127.0.0.1:43210', _validate=mock.Mock())
        self.value = dict(page_verified=True, graph_verified=True, nodes=17, edges=20)
        self.targets = [{'type': 'page', 'url': self.access.origin + '/', 'targetId': 'owned'}]
        async def send(session, method, params):
            if method == 'Target.getTargets':
                return {'targetInfos': self.targets}
            self.assertEqual(session, 'owned-session')
            self.assertEqual(method, 'Runtime.evaluate')
            expression = params['expression']
            self.assertIn("fetch('/api/graph'", expression)
            self.assertIn("document.querySelector('#access')", expression)
            self.assertNotIn('Authorization', expression)
            self.assertNotIn('console.token', expression)
            self.assertNotIn('innerText', expression)
            return {'result': {'value': self.value}}
        self.browser = SimpleNamespace(_send=mock.AsyncMock(side_effect=send), _event_failed=False,
                                       _ready_target=mock.AsyncMock(return_value='owned-session'))
        self.pause = mock.patch.object(launcher.asyncio, 'sleep', new=mock.AsyncMock())
        self.pause.start()
        self.addCleanup(self.pause.stop)

    async def test_only_verified_metadata_is_returned(self):
        self.value['unexpected_private_data'] = 'sensitive-marker'
        receipt = await launcher.verify(self.browser, self.access)
        self.assertEqual(receipt, dict(page_verified=True, graph_verified=True, nodes=17, edges=20))
        self.assertNotIn('sensitive-marker', str(receipt))

    async def test_wrong_page_cannot_claim_verified(self):
        self.targets[0]['url'] = 'http://127.0.0.1:43211/'
        with self.assertRaises(AccessError):
            await launcher.verify(self.browser, self.access)
        self.browser._ready_target.assert_not_awaited()

    async def test_failed_or_missing_page_graph_evidence_is_rejected(self):
        for value in (None, {}, dict(self.value, graph_verified=False), dict(self.value, nodes=True)):
            self.value = value
            with self.assertRaises(AccessError):
                await launcher.verify(self.browser, self.access)

    async def test_interception_failure_stops_verification(self):
        self.browser._event_failed = True
        with self.assertRaises(AccessError):
            await launcher.verify(self.browser, self.access)
        self.browser._send.assert_not_awaited()


class LauncherTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.root.chmod(0o700)
        self.chrome = self.root / 'chrome'
        shutil.copyfile('/usr/bin/true', self.chrome)
        self.chrome.chmod(0o700)
        self.args = SimpleNamespace(runtime_dir=str(self.root), chrome_binary=str(self.chrome),
                                    display=':106', xauthority=None, verify_only=True)

    async def test_verified_launch_closes_and_removes_private_client(self):
        access = SimpleNamespace(start=mock.AsyncMock(), close=mock.AsyncMock(), origin='http://127.0.0.1:40000')
        browser = SimpleNamespace(start=mock.AsyncMock(), close=mock.AsyncMock())
        evidence = dict(page_verified=True, graph_verified=True, nodes=1, edges=0)
        with mock.patch.object(launcher.ConsoleAccess, 'check', return_value=access), \
             mock.patch.object(launcher.BrowserPlan, 'check'), \
             mock.patch.object(launcher, 'OwnedBrowser', return_value=browser), \
             mock.patch.object(launcher, 'verify', new=mock.AsyncMock(return_value=evidence)), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            await launcher.launch(self.args)
        self.assertIn('current_owned_browser_only', output.getvalue())
        self.assertIn('"page_verified": true', output.getvalue())
        browser.close.assert_awaited_once()
        access.close.assert_awaited_once()
        self.assertEqual(list(self.root.iterdir()), [self.chrome])

    async def test_failed_verification_emits_no_success_and_cleans(self):
        access = SimpleNamespace(start=mock.AsyncMock(), close=mock.AsyncMock())
        browser = SimpleNamespace(start=mock.AsyncMock(), close=mock.AsyncMock())
        with mock.patch.object(launcher.ConsoleAccess, 'check', return_value=access), \
             mock.patch.object(launcher.BrowserPlan, 'check'), \
             mock.patch.object(launcher, 'OwnedBrowser', return_value=browser), \
             mock.patch.object(launcher, 'verify', new=mock.AsyncMock(side_effect=AccessError())), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            with self.assertRaises(AccessError):
                await launcher.launch(self.args)
        self.assertEqual(output.getvalue(), '')
        browser.close.assert_awaited_once()
        access.close.assert_awaited_once()
        self.assertEqual(list(self.root.iterdir()), [self.chrome])

    def test_browser_digest_rejects_symlinks_and_unsafe_binary(self):
        link = self.root / 'alias'
        link.symlink_to(self.chrome)
        for path in (link, self.root, Path('relative')):
            with self.assertRaises((AccessError, OSError, ValueError)):
                launcher.chrome_digest(path)
        self.chrome.chmod(0o777)
        with self.assertRaises(AccessError):
            launcher.chrome_digest(self.chrome)

    def test_cli_requires_explicit_display_without_echoing_values(self):
        with contextlib.redirect_stderr(io.StringIO()) as output:
            code = launcher.main(['--unexpected', 'sensitive-marker'])
        self.assertEqual(code, 1)
        self.assertNotIn('sensitive-marker', output.getvalue())
        self.assertIn('复制给 AI', output.getvalue())


if __name__ == '__main__':
    unittest.main()
