"""Offline regressions for the assembled hostd sources and SDK child lifecycle."""
import asyncio
import contextlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

TESTS = Path(__file__).resolve().parent
SCRIPTS = TESTS.parent / "scripts"
for path in (SCRIPTS, SCRIPTS / "hostd", TESTS):
    sys.path.insert(0, str(path))

try:
    import websockets
except ImportError:
    HAS_WEBSOCKETS = False
else:
    HAS_WEBSOCKETS = True
try:
    import lark_oapi
except ImportError:
    HAS_DEPS = False
else:
    HAS_DEPS = True
    import feishu_feed as ff

# Wiring/locks/Round use only standard library. Replace just the external relay adapter
# while loading the real assembler when the CI environment lacks websockets.
import registry
import test_hostd_unit as old
spec = importlib.util.spec_from_file_location("hostd_wiring_main", SCRIPTS / "hostd" / "__main__.py")
hd = importlib.util.module_from_spec(spec)
prior_relay = sys.modules.get('relay_feed')
try:
    if not HAS_WEBSOCKETS:
        sys.modules['relay_feed'] = mock.Mock(follow=mock.AsyncMock())
    spec.loader.exec_module(hd)
finally:
    if not HAS_WEBSOCKETS:
        if prior_relay is None:
            sys.modules.pop('relay_feed', None)
        else:
            sys.modules['relay_feed'] = prior_relay


class FakeWorker:
    def __init__(self, *args, **kwargs):
        self.claim_at = 0
        self.known_threads = {"known"}
        self.calls = []
        self.outlet_responsibilities = set()

    def run(self, dirty, *, threads=None):
        self.calls.append((set(dirty), threads))
        return {"hostd": {"verdict": "off"}}


class Wiring(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmpctx = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpctx.cleanup)
        self.tmp = Path(self.tmpctx.name)
        reg = registry.Registry()
        for name in ("alpha", "beta"):
            cfg = self.tmp / (name + ".json")
            cfg.write_text(json.dumps({"mirror_pubkey": old.base.MIRROR_PK, "mirror_env_file": "unused"}))
            cfg.chmod(0o600)
            reg.bindings[name] = registry.Binding(name, cfg, self.tmp / name, "ch_" + name, "oc_" + name,
                                                "cli_" + name, str(self.tmp / ("cfg_" + name)), str(self.tmp / ("data_" + name)), "ws://localhost")
        with mock.patch.object(hd.bw, "Worker", FakeWorker):
            self.h = hd.Hostd(reg, self.tmp / "status.json")
        self.h.app_lock_dir = self.tmp / "locks"

    async def test_assembly_restarts_failed_source_without_stopping_other_binding(self):
        retries = 0
        continued = asyncio.Event()
        blocked = asyncio.Event()
        async def feed(app, bindings):
            nonlocal retries
            if app == "cli_alpha":
                retries += 1
                if retries == 1:
                    raise RuntimeError("secret credential + raw body")
                await blocked.wait()
            else:
                # Observe the startup round before injecting a distinct event;
                # otherwise legitimate dirty-event coalescing can yield one run.
                while self.h.status['bindings']['beta']['runs'] < 1:
                    await asyncio.sleep(.005)
                self.h.mark("beta", "buzz")
                while self.h.status['bindings']['beta']['runs'] < 2:
                    await asyncio.sleep(.005)
                continued.set()
                await blocked.wait()
        async def follow(*args):
            await blocked.wait()
        with mock.patch.object(self.h, "feishu_child", feed), mock.patch.object(hd.relay_feed, "follow", follow), \
                mock.patch.object(hd, "DEBOUNCE", .001), mock.patch.object(hd, "TASK_RETRY", .005):
            task = asyncio.create_task(self.h.main())
            try:
                await asyncio.wait_for(continued.wait(), 2)
                self.assertGreaterEqual(retries, 2)
                self.assertGreaterEqual(len(self.h.workers["beta"].calls), 2)
                self.assertFalse(task.done())
                self.assertNotIn("secret credential", self.h.status_file.read_text())
            finally:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    async def test_nonzero_round_error_count_is_reported_without_retry(self):
        completed = asyncio.Event()
        original_round = self.h._round
        async def observed_round(*args, **kwargs):
            result = await original_round(*args, **kwargs)
            completed.set()
            return result
        with mock.patch.object(self.h.workers["alpha"], "run", return_value={"errors": 1, "hostd": {"verdict": "off"}}) as run, \
                mock.patch.object(self.h, "_round", observed_round), \
                mock.patch.object(hd, "DEBOUNCE", .001):
            task = asyncio.create_task(self.h.worker("alpha"))
            try:
                self.h.mark("alpha", "buzz")
                await asyncio.wait_for(completed.wait(), 2)
                self.assertEqual(self.h.status["bindings"]["alpha"]["runs"], 1)
                self.assertEqual(self.h.status["bindings"]["alpha"]["errors"], 1)
                self.assertEqual(run.call_count, 1)
                self.assertNotIn("alpha", self.h.retry_tasks)
                self.assertNotIn("alpha", self.h.retry_phases)
            finally:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    async def _check_supervisor_restart(self,component):
        calls = 0
        recovered = asyncio.Event()
        async def fails_once(*args):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise ValueError("private body")
            recovered.set()
            await asyncio.Event().wait()
        async def idle(*args):
            await asyncio.Event().wait()
        with mock.patch.object(self.h, "worker", fails_once if component == "worker" else idle), \
                mock.patch.object(self.h, "claims", fails_once if component == "claims" else idle), \
                mock.patch.object(self.h, "feishu_child", idle), \
                mock.patch.object(hd.relay_feed, "follow", idle), mock.patch.object(hd, "TASK_RETRY", .001):
            task = asyncio.create_task(self.h.main())
            try:
                await asyncio.wait_for(recovered.wait(), .5)
                self.assertFalse(task.done())
            finally:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task


    async def test_supervisor_restarts_worker_independently(self):
        await self._check_supervisor_restart("worker")

    async def test_supervisor_restarts_claims_independently(self):
        await self._check_supervisor_restart("claims")

    async def test_runner_observes_fatal_root_without_waiting_for_signal(self):
        async def broken():
            raise RuntimeError("fatal root")
        with mock.patch.object(self.h, "main", broken):
            with self.assertRaisesRegex(RuntimeError, "fatal root"):
                await asyncio.wait_for(hd.run_until_stopped(self.h, asyncio.Event()), .2)

    async def test_actual_child_stderr_drained_and_cancellation_reaps(self):
        # This is a real isolated child, using no credentials and no network.
        proc = await asyncio.create_subprocess_exec(sys.executable, "-u", "-c",
            "import os,time; os.write(2,b'RAW_SECRET_BODY'*100000); print('{\"type\":\"_connected\"}',flush=True); time.sleep(30)",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        async def spawn(*args, **kwargs):
            return proc
        with mock.patch.object(hd.asyncio, "create_subprocess_exec", spawn):
            task = asyncio.create_task(self.h.feishu_child("cli_alpha", [self.h.reg.bindings["alpha"]]))
            try:
                for _ in range(100):
                    if self.h.status["apps"].get("cli_alpha", {}).get("feishu") == "connected":
                        break
                    await asyncio.sleep(.005)
                self.assertEqual(self.h.status["apps"].get("cli_alpha", {}).get("feishu"), "connected")
                self.assertNotIn("RAW_SECRET_BODY", self.h.status_file.read_text())
            finally:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await asyncio.wait_for(task, .5)
                if proc.returncode is None:
                    proc.kill()
                    await proc.communicate()
            self.assertIsNotNone(proc.returncode)

    async def test_assembled_cancellation_reaps_child_with_stdout_backpressure(self):
        proc = await asyncio.create_subprocess_exec(sys.executable, "-u", "-c",
            "import os,time; print('{\"type\":\"_connected\"}',flush=True); time.sleep(.1); os.write(1,b'x'*1000000); time.sleep(30)",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        async def spawn(*args, **kwargs):
            return proc
        async def idle(*args):
            await asyncio.Event().wait()
        self.h.reg.bindings = {"alpha": self.h.reg.bindings["alpha"]}
        with mock.patch.object(hd.asyncio, "create_subprocess_exec", spawn), \
                mock.patch.object(hd.relay_feed, "follow", idle), mock.patch.object(hd, "CHILD_STOP_TIMEOUT", .02):
            task = asyncio.create_task(self.h.main())
            try:
                await asyncio.sleep(.15)
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await asyncio.wait_for(task, .5)
                self.assertIsNotNone(proc.returncode)
            finally:
                task.cancel()
                if proc.returncode is None:
                    proc.kill()
                await proc.communicate()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    async def test_child_escalates_to_kill_when_term_ignored(self):
        proc = await asyncio.create_subprocess_exec(sys.executable, "-u", "-c",
            "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); print('ready',flush=True); time.sleep(30)",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await proc.stdout.readline()
        with mock.patch.object(hd, "CHILD_STOP_TIMEOUT", .02):
            try:
                await asyncio.wait_for(hd.stop_child(proc), .3)
            finally:
                if proc.returncode is None:
                    proc.kill()
                    await proc.wait()
        self.assertEqual(proc.returncode, -9)

    async def test_connecting_is_not_connected_and_connected_reconciles_members(self):
        await self.h.on_feishu("cli_alpha", {"type": "_connecting"}, {"oc_alpha": "alpha"})
        self.assertEqual(self.h.status["apps"]["cli_alpha"]["feishu"], "connecting")
        self.assertFalse(self.h.dirty["alpha"])
        await self.h.on_feishu("cli_alpha", {"type": "_connected"}, {"oc_alpha": "alpha"})
        self.assertEqual(self.h.dirty["alpha"], {"members", "feishu", "notice"})
        self.assertIsNone(self.h.threads["alpha"])

    async def test_only_actual_member_events_trigger_member_reconciliation(self):
        for kind in ('bot.added', 'bot.deleted', 'user.added', 'user.deleted', 'user.withdrawn'):
            self.h.dirty['alpha'].clear()
            await self.h.on_feishu('cli_alpha', {'type': 'im.chat.member.' + kind + '_v1',
                'chat_id': 'oc_alpha'}, {'oc_alpha': 'alpha'})
            self.assertIn('members', self.h.dirty['alpha'])

    async def test_waiting_notice_does_not_occupy_binding_lock_or_block_new_message(self):
        await self.h._notice_lane.acquire()
        self.h.mark('alpha', 'notice')
        with mock.patch.object(hd, 'DEBOUNCE', .001):
            task = asyncio.create_task(self.h.worker('alpha'))
            try:
                await asyncio.sleep(.02)
                self.assertFalse(self.h.binding_locks['alpha'].locked())
                self.assertEqual(self.h.workers['alpha'].calls, [])
                self.h.mark('alpha', 'feishu')
                async def completed():
                    while not self.h.workers['alpha'].calls:
                        await asyncio.sleep(.005)
                await asyncio.wait_for(completed(), 2)
                self.assertEqual(self.h.workers['alpha'].calls[0][0], {'feishu'})
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                self.h._notice_lane.release()
                await self.h._shutdown()

    async def test_onboarding_diagnostics_write_failure_preserves_invite_route(self):
        self.h.onboarding = mock.Mock()
        self.h.onboarding.enqueue_feed.return_value = {'toast': {'type': 'success'}}
        with mock.patch.object(self.h, 'save_status', side_effect=OSError('disk unavailable')):
            # The original final status write may still fail, after routing.
            with self.assertRaises(OSError):
                await self.h.on_feishu('cli_alpha', {
                    'type': 'im.chat.member.bot.added_v1', 'chat_id': 'oc_alpha'},
                    {'oc_alpha': 'alpha'})
        self.h.onboarding.enqueue_feed.assert_called_once()
        self.assertEqual(self.h.dirty['alpha'], {'members', 'notice'})
        self.assertTrue(self.h.wake['alpha'].is_set())

    async def test_lost_root_and_reaction_route_complete_catchup(self):
        for event in ({"type": "im.message.receive_v1", "root_id": "missing"},
                      {"type": "im.message.reaction.created_v1", "message_id": "missing"},
                      {"type": "im.message.reaction.deleted_v1", "message_id": "missing"}):
            self.h.dirty["alpha"].clear()
            self.h.wake["alpha"].clear()
            self.h.threads["alpha"] = set()
            await self.h.on_feishu("cli_alpha", dict(event, chat_id="oc_alpha"), {"oc_alpha": "alpha"})
            self.assertIsNone(self.h.threads["alpha"])
            self.assertEqual(self.h.dirty["alpha"], {"feishu", "notice"})
            self.assertTrue(self.h.wake["alpha"].is_set())
        self.h.dirty["alpha"].clear()
        self.h.wake["alpha"].clear()
        self.h.threads["alpha"] = set()
        await self.h.on_feishu("cli_alpha", {"type": "im.message.receive_v1", "root_id": "known", "chat_id": "oc_alpha"}, {"oc_alpha": "alpha"})
        self.assertEqual(self.h.dirty["alpha"], {"feishu", "notice"})
        self.assertTrue(self.h.wake["alpha"].is_set())
        self.assertEqual(self.h.threads["alpha"], {"known"})


class AppLock(unittest.TestCase):
    def test_exclusive_across_processes_private_and_released(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "locks"
            with hd.app_lock("app_test", path):
                self.assertEqual(path.stat().st_mode & 0o777, 0o700)
                self.assertEqual(next(path.iterdir()).stat().st_mode & 0o777, 0o600)
                script = "import sys;sys.path.insert(0,sys.argv[1]);from hostd.__main__ import app_lock;from pathlib import Path\nwith app_lock('app_test',Path(sys.argv[2])): pass"
                result = subprocess.run([sys.executable, "-c", script, str(SCRIPTS), str(path)], capture_output=True)
                self.assertNotEqual(result.returncode, 0)
            with hd.app_lock("app_test", path):
                pass

    def test_rejects_symlink_or_shared_lock_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shared = root / "shared"
            shared.mkdir(mode=0o755)
            link = root / "link"
            link.symlink_to(shared)
            for path in (shared, link):
                with self.subTest(path=path), self.assertRaises(ValueError):
                    with hd.app_lock("app_test", path):
                        self.fail("unsafe directory accepted")


@unittest.skipUnless(HAS_DEPS, "requires hostd dependency environment")
class SDKEnvelope(unittest.TestCase):
    def test_card_keeps_trusted_action_envelope_and_queued_toast(self):
        raw = {"header": {"event_id": "evt1"}, "event": {"operator": {"open_id": "operator"},
            "token": "sensitive_callback_token", "action": {"value": {"request_id": "req", "choice": "allow"}},
            "context": {"open_chat_id": "chat", "open_message_id": "card"}, "current_message": {"message_id": "card"}}}
        builder = mock.Mock()
        for etype in ff.EVENTS:
            getattr(builder, "register_p2_" + etype.replace(".", "_")).return_value = builder
        builder.register_p2_card_action_trigger.return_value = builder
        rows = []
        with mock.patch.object(ff.lark.EventDispatcherHandler, "builder", return_value=builder), \
                mock.patch.object(ff.lark.JSON, "marshal", return_value=json.dumps(raw)), mock.patch.object(ff, "emit", rows.append):
            ff.handler("app")
            callback = builder.register_p2_card_action_trigger.call_args.args[0]
            response = callback(object())
        row = rows[0]
        self.assertEqual(row["event_id"], "evt1")
        for key in ("operator", "token", "action", "context", "current_message"):
            self.assertEqual(row[key], raw["event"][key])
        self.assertIn("排队", response.toast.content)
        self.assertNotIn("已批准", response.toast.content)

    def test_initial_connect_status_uses_actual_sdk_completed_handshake(self):
        # SDK's private hook is intentionally tested against the installed implementation.
        rows = []
        connection = mock.Mock()
        with mock.patch("lark_oapi.ws.client.ExpiringCache"), mock.patch.object(ff, "emit", rows.append), mock.patch.object(ff.lark.ws.Client, "_get_conn_url", return_value="wss://transport.test?device_id=d&service_id=1"):
            client = ff.EventClient("app", "test_secret")
            with mock.patch("lark_oapi.ws.client.websockets.connect", new=mock.AsyncMock(return_value=connection)), \
                    mock.patch("lark_oapi.ws.client.loop.create_task", side_effect=lambda coro: coro.close()):
                asyncio.run(client._connect())
            self.assertEqual([r["type"] for r in rows], ["_connected"])
            self.assertIs(client._conn, connection)

    def test_failed_sdk_handshake_never_reports_connected(self):
        rows = []
        with mock.patch("lark_oapi.ws.client.ExpiringCache"), mock.patch.object(ff, "emit", rows.append), mock.patch.object(ff.lark.ws.Client, "_get_conn_url", side_effect=RuntimeError("secret URL")):
            client = ff.EventClient("app", "test_secret")
            with self.assertRaises(RuntimeError):
                asyncio.run(client._connect())
        self.assertEqual(rows, [])


class TopLevel(old.OnlyThreads):
    # Inherits the existing behavioral Round regressions as well.
    def test_empty_thread_filter_still_delivers_top_level(self):
        world = self.world()
        env = old.base.Env(self.tmp)
        world.thread_polls = []
        report = self.round_f2b(env, world, old.base.NOW, only_threads=set())
        self.assertEqual(report["to_buzz"], 2)
        self.assertEqual(world.thread_polls, [])


if __name__ == "__main__":
    unittest.main()
