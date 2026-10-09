"""Unix-only console acceptance contract, using temporary Store/socket files."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from hostd.store import Store, BindingRecord
try:
    from hostd import console
except ImportError:
    console = None

AGENT = "a" * 64
OWNER = "b" * 64
REMOTE = "c" * 64


class ConsoleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.assertIsNotNone(console, "hostd.console must be implemented")
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / "state" / "hostd.db")
        self.store.reconcile_bindings([BindingRecord("alpha", "channel_alpha", "oc_alpha", "cli_alpha",
            "/secret-profile/token-do-not-show", "/private/config", "/private/data", mirror_pubkey="d" * 64)], now=100)
        self.store.register_agent(AGENT, owner_pubkey=OWNER, app_id="cli_alpha", config_path="/secret/config", now=100)
        ref = hashlib.sha256(b"buzz-feishu-chat:v1:oc_alpha").hexdigest()
        self.store.record_agent_chat(AGENT, "oc_alpha", ref, binding_id="alpha", now=100)
        self.store.record_connection("feishu", "cli_alpha", binding_id="alpha", status="connected", now=101)
        self.store.record_connection("relay", "unrelated", status="failed", now=101)
        self.server = console.ConsoleServer(self.store, self.root / "runtime", request_timeout=0.15,
                                            heartbeat_interval=0.05, allowed_hosts=("hostd.local",))
        await self.server.start()
        self.token = self.server.token_path.read_text().strip()
        self.writers = []

    async def asyncTearDown(self):
        if hasattr(self, "server"):
            for writer in self.writers:
                writer.close()
                await writer.wait_closed()
            await self.server.close()
            self.store.close()
            self.tmp.cleanup()

    async def request(self, method="GET", target="/api/graph", *, token=True, headers=None, body=b"", raw=None, idempotency=True):
        reader, writer = await asyncio.open_unix_connection(self.server.socket_path)
        self.writers.append(writer)
        if raw is None:
            fields = {"Host": "hostd.local", "Connection": "close"}
            if token:
                fields["Authorization"] = "Bearer " + self.token
            if method == "POST":
                fields.update({"Origin": "http://hostd.local", "X-Hostd-Request": "1",
                               "Content-Type": "application/json", "Content-Length": str(len(body))})
                if idempotency:
                    fields['Idempotency-Key'] = secrets.token_hex(32)
            fields.update(headers or {})
            raw = (method + " " + target + " HTTP/1.1\r\n" +
                   "".join(k + ": " + v + "\r\n" for k, v in fields.items()) + "\r\n").encode() + body
        writer.write(raw)
        await writer.drain()
        data = await asyncio.wait_for(reader.read(), 1)
        head, payload = data.split(b"\r\n\r\n", 1)
        status = int(head.split(b" ", 2)[1])
        result = json.loads(payload) if b"application/json" in head else payload.decode()
        return status, result, head

    def assert_notice(self, payload):
        encoded = json.dumps(payload, ensure_ascii=False)
        self.assertIn("怎么解决", encoded)
        self.assertIn("复制给 AI", encoded)
        self.assertNotIn(self.token, encoded)
        self.assertNotIn("Traceback", encoded)

    async def test_socket_token_private_random_and_exact_uid(self):
        self.assertEqual(self.server.socket_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.server.token_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.server.socket_path.parent.stat().st_mode & 0o777, 0o700)
        self.assertGreaterEqual(len(self.token), 43)
        with patch.object(self.server, "_peer_uid", return_value=os.geteuid() + 1):
            status, payload, _ = await self.request()
        self.assertEqual(status, 403)
        self.assert_notice(payload)

    async def test_all_routes_need_token_and_wrong_origin_never_reads(self):
        for target in ("/", "/api/graph", "/api/events", "/api/bindings/alpha"):
            status, payload, _ = await self.request(target=target, token=False)
            self.assertEqual(status, 401)
            self.assert_notice(payload)
        for fields in ({"Authorization": "Bearer wrong"}, {"Origin": "https://evil.example"},
                       {"Host": "evil.example"}, {"Origin": "null"}, {"Sec-Fetch-Site": "cross-site"}):
            status, payload, _ = await self.request(headers=fields)
            self.assertIn(status, (401, 403))
            self.assert_notice(payload)

    async def test_graph_uses_stored_status_and_omits_private_columns(self):
        status, graph, head = await self.request()
        self.assertEqual(status, 200)
        binding = next(n for n in graph["nodes"] if n["id"] == "binding:alpha")
        self.assertEqual(binding["status"], "active")
        connection = next(n for n in graph["nodes"] if n["kind"] == "connection")
        self.assertEqual(connection["status"], "connected")
        text = json.dumps(graph)
        for hidden in ("oc_alpha", "/secret", "/private", "token-do-not-show", OWNER, "unrelated", self.token):
            self.assertNotIn(hidden, text)
        self.assertIn(b"Cache-Control: no-store", head)
        self.assertNotIn(b"Access-Control-Allow-Origin", head)

    async def test_details_keep_cursor_delivery_status_and_hide_actor_ids(self):
        delivery = self.store.reserve_delivery("alpha", "om_test|on_private_person|THUMBSUP", "f2r", now=103)
        self.store.fail_delivery(delivery.id, now=104)
        status, detail, _ = await self.request(target="/api/bindings/alpha")
        self.assertEqual(status, 200)
        self.assertEqual(detail["status"], "active")
        self.assertEqual(detail["deliveries"][0]["status"], "failed")
        self.assertNotIn("on_private_person", str(detail))
        self.assertNotIn("source_id", str(detail))
        self.assert_notice(detail["deliveries"][0]["notice"])

    async def test_unknown_ids_query_paths_and_approval_endpoints_rejected(self):
        for target in ("/api/bindings/unknown", "/api/agents/unknown", "/api/operations/unknown",
                       "/api/graph?path=/etc/passwd", "/api/bindings/%2e%2e", "/../../etc/passwd"):
            status, payload, _ = await self.request(target=target)
            self.assertIn(status, (400, 404))
            self.assert_notice(payload)
        for target in ("/api/bindings/create", "/api/join/approve", "/api/credentials"):
            status, payload, _ = await self.request("POST", target, body=b"{}")
            self.assertEqual(status, 404)
            self.assert_notice(payload)

    async def test_local_agent_and_related_public_registration_visibility(self):
        ref = "e" * 64
        self.server.public_view = lambda: console.PublicTopology(bindings=(
            console.PublicBinding("channel_remote", ref, "f" * 64, 120, (AGENT,)),
            console.PublicBinding("channel_unseen", "1" * 64, "2" * 64, 120, (REMOTE,))),
            agents=(console.PublicAgent(REMOTE, "cli_remote", ("channel_remote",)),
                    console.PublicAgent("3" * 64, "cli_unseen", ("channel_unseen",))))
        status, graph, _ = await self.request()
        self.assertEqual(status, 200)
        text = json.dumps(graph)
        self.assertIn("channel_remote", text)
        self.assertIn("agent:" + REMOTE, text)
        self.assertNotIn("channel_unseen", text)
        self.assertNotIn("cli_unseen", text)
        self.assertNotIn("f" * 64, text)
        remote_nodes = [n for n in graph["nodes"] if n["visibility"] == "public"]
        self.assertTrue(remote_nodes)
        self.assertTrue(all(n["status"] == "public" for n in remote_nodes))
        status, payload, _ = await self.request("POST", "/api/agents/" + REMOTE + "/restart", body=b"{}")
        self.assertEqual(status, 404)
        self.assert_notice(payload)

    async def test_pause_queue_waits_for_actual_callback_and_store_readback(self):
        from hostd.console_operations import ConsoleReceipt, current_intent
        receipts = {}
        gate, invoked = asyncio.Event(), asyncio.Event()
        async def action(kind, target, operation):
            self.assertEqual((kind, target, operation), ("binding", "alpha", "pause"))
            invoked.set()
            await gate.wait()
            with self.store.transaction():
                self.store.conn.execute("UPDATE binding SET status='paused' WHERE binding_id='alpha'")
            intent = current_intent()
            receipts[intent.operation_id] = ConsoleReceipt(intent.operation_id, 'paused', hashlib.sha256(intent.operation_id.encode()).hexdigest())
            return console.ActionResult(True, "paused")
        self.server.action = action
        self.server.operation_readback = lambda intent: receipts.get(intent.operation_id)
        status, receipt, _ = await self.request("POST", "/api/bindings/alpha/pause", body=b"{}")
        self.assertEqual(status, 202)
        self.assertEqual(receipt["status"], "queued")
        await asyncio.wait_for(invoked.wait(), 1)
        status, observed, _ = await self.request(target="/api/operations/" + receipt["id"])
        self.assertEqual(observed["status"], "dispatched")
        self.assertEqual(observed["readback"]["status"], "active")
        gate.set()
        await self.server.wait_operation(receipt["id"])
        status, observed, _ = await self.request(target="/api/operations/" + receipt["id"])
        self.assertEqual(observed["status"], "completed")
        self.assertEqual(observed["readback"]["status"], "paused")
        self.assertIn("已暂停", observed["message"])

    async def test_success_claim_without_matching_readback_fails(self):
        async def action(*args):
            return console.ActionResult(True, "paused")
        self.server.action = action
        status, receipt, _ = await self.request("POST", "/api/bindings/alpha/pause", body=b"{}")
        await self.server.wait_operation(receipt["id"])
        _, result, _ = await self.request(target="/api/operations/" + receipt["id"])
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["readback"]["status"], "active")
        self.assert_notice(result)

    async def test_operations_fail_closed_without_driver_and_redact_exceptions(self):
        status, payload, _ = await self.request("POST", "/api/bindings/alpha/pause", body=b"{}")
        self.assertEqual(status, 503)
        self.assert_notice(payload)
        async def action(*args):
            raise RuntimeError("secret and private@example.com")
        self.server.action = action
        _, receipt, _ = await self.request("POST", "/api/bindings/alpha/backfill", body=b"{}")
        await self.server.wait_operation(receipt["id"])
        _, result, _ = await self.request(target="/api/operations/" + receipt["id"])
        self.assertEqual(result["status"], "unknown")
        self.assertNotIn("private@example.com", str(result))
        self.assert_notice(result)

    async def test_resume_backfill_and_agent_restart_require_explicit_readback(self):
        from hostd.console_operations import ConsoleReceipt, current_intent
        receipts = {}
        async def action(kind, target, operation):
            if operation == "resume":
                with self.store.transaction():
                    self.store.conn.execute("UPDATE binding SET status='active' WHERE binding_id='alpha'")
            if kind == 'binding':
                intent = current_intent()
                observed = 'active' if operation == 'resume' else 'backfilled'
                receipts[intent.operation_id] = ConsoleReceipt(intent.operation_id, observed, hashlib.sha256(intent.operation_id.encode()).hexdigest())
            return console.ActionResult(True, {"resume": "active", "backfill": "backfilled", "restart": "restarted"}[operation])
        self.server.action = action
        self.server.operation_readback = lambda intent: receipts.get(intent.operation_id)
        for target in ("/api/bindings/alpha/resume", "/api/bindings/alpha/backfill", "/api/agents/" + AGENT + "/restart"):
            _, receipt, _ = await self.request("POST", target, body=b"{}")
            await self.server.wait_operation(receipt["id"])
            _, result, _ = await self.request(target="/api/operations/" + receipt["id"])
            self.assertEqual(result["status"], 'unknown' if '/agents/' in target else 'completed')

    async def test_writes_require_same_origin_marker_and_empty_object_only(self):
        for fields, body in (({"Origin": ""}, b"{}"), ({"X-Hostd-Request": ""}, b"{}"),
                             ({"Content-Type": "text/plain"}, b"{}"), ({}, b'{"path":"/etc/passwd"}'),
                             ({}, b"[]"), ({}, b"null")):
            status, payload, _ = await self.request("POST", "/api/bindings/alpha/pause", headers=fields, body=body)
            self.assertIn(status, (400, 403, 415))
            self.assert_notice(payload)

    async def test_http_duplicate_lengths_transfer_encoding_and_large_header_rejected(self):
        common = b"Host: hostd.local\r\nAuthorization: Bearer " + self.token.encode() + b"\r\n"
        for suffix in (b"Content-Length: 0\r\nContent-Length: 0\r\n", b"Transfer-Encoding: chunked\r\n",
                       b"X-Large: " + b"x" * 20000 + b"\r\n"):
            status, payload, _ = await self.request(raw=b"GET /api/graph HTTP/1.1\r\n" + common + suffix + b"\r\n")
            self.assertIn(status, (400, 413, 431))
            self.assert_notice(payload)

    async def test_slow_incomplete_header_times_out_and_releases_client(self):
        reader, writer = await asyncio.open_unix_connection(self.server.socket_path)
        self.writers.append(writer)
        writer.write(b"GET /api/graph HTTP/1.1\r\nHost:")
        await writer.drain()
        data = await asyncio.wait_for(reader.read(), 0.5)
        self.assertIn(b"408", data.split(b"\r\n", 1)[0])
        self.assertNotIn(self.token.encode(), data)

    async def test_sse_real_store_change_and_disconnect_cleanup(self):
        reader, writer = await asyncio.open_unix_connection(self.server.socket_path)
        self.writers.append(writer)
        writer.write(("GET /api/events HTTP/1.1\r\nHost: hostd.local\r\nAuthorization: Bearer " + self.token + "\r\n\r\n").encode())
        await writer.drain()
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 1)
        self.assertIn(b"text/event-stream", head)
        packet = await asyncio.wait_for(reader.readuntil(b"\n\n"), 1)
        self.assertIn(b"event: snapshot", packet)
        self.store.record_connection("feishu", "cli_alpha", binding_id="alpha", status="failed", error_code="permission", now=110)
        self.server.notify()
        packet = await asyncio.wait_for(reader.readuntil(b"\n\n"), 1)
        self.assertIn(b'"status":"failed"', packet)
        self.assertNotIn(self.token.encode(), packet)
        writer.close()
        await writer.wait_closed()
        for _ in range(30):
            if self.server.subscriber_count == 0:
                break
            await asyncio.sleep(0.01)
        self.assertEqual(self.server.subscriber_count, 0)

    async def test_read_snapshot_with_concurrent_store_writer(self):
        errors = []
        def worker():
            try:
                for n in range(10):
                    self.store.record_connection("feishu", "cli_alpha", binding_id="alpha", status="connected", now=200+n)
                    self.server.notify()
            except Exception as exc:
                errors.append(type(exc).__name__)
        thread = threading.Thread(target=worker)
        thread.start()
        for _ in range(4):
            self.assertEqual((await self.request())[0], 200)
        thread.join()
        self.assertEqual(errors, [])

    async def test_static_ui_has_real_graph_operations_and_no_auth_entry_or_mock_data(self):
        # A read-only source mount may have a different UID from the test
        # process. Serve the exact shipped bytes from this run's owned fixture;
        # production ownership checks and unsafe-file tests remain unchanged.
        asset = self.root / 'console_ui.html'
        asset.write_bytes(console.UI_PATH.read_bytes())
        with patch.object(console, 'UI_PATH', asset):
            status, html, head = await self.request(target="/")
        self.assertEqual(status, 200)
        for text in ("/api/graph", "/api/events", "/api/operations/", "pause", "resume", "backfill", "restart", "复制给 AI"):
            self.assertIn(text, html)
        for text in (self.token, "localStorage", "sessionStorage", 'type="password"', "模拟事件", "Mock data"):
            self.assertNotIn(text, html)
        self.assertIn(b"Content-Security-Policy:", head)
        self.assertIn(b"connect-src 'self'", head)

    async def test_close_cancels_real_driver_without_false_completion_or_task_error(self):
        entered = asyncio.Event()
        async def action(*args):
            entered.set()
            await asyncio.Event().wait()
        self.server.action = action
        _, receipt, _ = await self.request("POST", "/api/bindings/alpha/pause", body=b"{}")
        await asyncio.wait_for(entered.wait(), 1)
        task = self.server._operation_tasks[receipt["id"]]
        await self.server.close()
        self.assertTrue(task.done())
        self.assertEqual(self.server._operations[receipt["id"]]["status"], "unknown")

    async def test_incomplete_body_has_total_deadline(self):
        reader, writer = await asyncio.open_unix_connection(self.server.socket_path)
        self.writers.append(writer)
        writer.write(("POST /api/bindings/alpha/pause HTTP/1.1\r\nHost: hostd.local\r\nAuthorization: Bearer " + self.token +
                      "\r\nOrigin: http://hostd.local\r\nX-Hostd-Request: 1\r\nContent-Type: application/json\r\nContent-Length: 20\r\n\r\n{}").encode())
        await writer.drain()
        data = await asyncio.wait_for(reader.read(), 0.5)
        self.assertIn(b"408", data.split(b"\r\n", 1)[0])
        self.assertEqual(self.server._operations, {})


    async def test_static_asset_symlink_cannot_serve_token_file(self):
        link = self.root / "ui-link.html"
        link.symlink_to(self.server.token_path)
        with patch.object(console, "UI_PATH", link, create=True):
            status, payload, _ = await self.request(target="/")
        self.assertEqual(status, 500)
        self.assert_notice(payload)

    async def test_local_identifier_punctuation_is_usable_without_path_decoding(self):
        self.store.reconcile_bindings([BindingRecord("alpha:beta..x", "channel_more", "oc_more", "cli_alpha",
            "/cfg", "/private/config", "/private/data")], now=105)
        status, detail, _ = await self.request(target="/api/bindings/alpha:beta..x")
        self.assertEqual(status, 200)
        self.assertEqual(detail["id"], "alpha:beta..x")

    async def test_client_and_operation_queue_caps_do_not_execute_extra_work(self):
        self.server.max_clients = 1
        reader, writer = await asyncio.open_unix_connection(self.server.socket_path)
        self.writers.append(writer)
        writer.write(b"GET /api/graph HTTP/1.1\r\n")
        await writer.drain()
        await asyncio.sleep(0.01)
        other_reader, other_writer = await asyncio.open_unix_connection(self.server.socket_path)
        self.writers.append(other_writer)
        try:
            data = await asyncio.wait_for(other_reader.read(), 0.3)
        except ConnectionResetError:
            data = b""
        self.assertTrue(not data or b"503" in data.split(b"\r\n", 1)[0])
        self.assertLessEqual(len(self.server._clients), 1)
        writer.close()
        await writer.wait_closed()
        await asyncio.sleep(0.02)
        self.server.max_clients = 32
        gate = asyncio.Event()
        async def action(*args):
            await gate.wait()
            return console.ActionResult(False, "")
        self.server.action = action
        self.server.max_operations = 1
        _, receipt, _ = await self.request("POST", "/api/bindings/alpha/pause", body=b"{}")
        status, payload, _ = await self.request("POST", "/api/agents/" + AGENT + "/restart", body=b"{}")
        self.assertEqual(status, 503)
        self.assert_notice(payload)
        self.assertEqual(len(self.server._operations), 1)
        status, payload, _ = await self.request("POST", "/api/bindings/alpha/pause", body=b"{}")
        self.assertEqual(status, 503)
        self.assert_notice(payload)
        gate.set()
        await self.server.wait_operation(receipt["id"])


    async def test_failures_in_graph_history_and_binding_details_have_repair_prompt(self):
        async def action(*args):
            return console.ActionResult(False, "")
        self.server.action = action
        _, receipt, _ = await self.request("POST", "/api/bindings/alpha/backfill", body=b"{}")
        await self.server.wait_operation(receipt["id"])
        _, graph, _ = await self.request()
        self.assert_notice(graph["operations"][-1]["notice"])
        with self.store.transaction():
            self.store.conn.execute("UPDATE binding SET status='conflict' WHERE binding_id='alpha'")
        _, graph, _ = await self.request()
        binding = next(n for n in graph["nodes"] if n["id"] == "binding:alpha")
        self.assertEqual(binding["status"], "conflict")
        self.assert_notice(binding["notice"])
        _, detail, _ = await self.request(target="/api/bindings/alpha")
        self.assert_notice(detail["notice"])


    async def test_selected_binding_history_is_not_hidden_by_other_binding_traffic(self):
        self.store.reconcile_bindings([BindingRecord("beta", "channel_beta", "oc_beta", "cli_alpha",
            "/cfg", "/private/config", "/private/data")], now=100)
        wanted = self.store.reserve_delivery("alpha", "om_wanted", "f2b", now=101)
        self.store.fail_delivery(wanted.id, now=102)
        for n in range(101):
            self.store.reserve_delivery("beta", "om_other_" + str(n), "f2b", now=200+n)
        _, detail, _ = await self.request(target="/api/bindings/alpha")
        self.assertEqual([r["id"] for r in detail["deliveries"]], [wanted.id])



class ConsolePathTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_ancestor_symlink_wrong_permissions_and_existing_socket_rejected(self):
        self.assertIsNotNone(console)
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with Store(root / "state" / "db") as store:
                unsafe = root / "unsafe"
                unsafe.mkdir(mode=0o755)
                private = root / "private"
                private.mkdir(mode=0o700)
                nested = private / "nested"
                nested.mkdir(mode=0o700)
                alias = root / "alias"
                alias.symlink_to(private, target_is_directory=True)
                for path in (unsafe, alias / "nested"):
                    server = console.ConsoleServer(store, path)
                    with self.assertRaises(console.ConsoleError):
                        await server.start()
                    await server.close()
                runtime = root / "runtime"
                runtime.mkdir(mode=0o700)
                target = runtime / "target"
                target.write_text("do-not-overwrite")
                target.chmod(0o600)
                (runtime / "console.token").symlink_to(target)
                server = console.ConsoleServer(store, runtime)
                with self.assertRaises(console.ConsoleError):
                    await server.start()
                self.assertEqual(target.read_text(), "do-not-overwrite")
                await server.close()
                (runtime / "console.token").unlink()
                (runtime / "console.sock").write_text("do-not-remove")
                server = console.ConsoleServer(store, runtime)
                with self.assertRaises(console.ConsoleError):
                    await server.start()
                self.assertEqual((runtime / "console.sock").read_text(), "do-not-remove")
                await server.close()

    async def test_existing_token_wrong_permissions_hardlink_and_restart_cleanup(self):
        self.assertIsNotNone(console)
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with Store(root / "state" / "db") as store:
                runtime = root / "runtime"
                runtime.mkdir(mode=0o700)
                token = runtime / "console.token"
                token.write_text("A" * 43 + "\n")
                token.chmod(0o644)
                server = console.ConsoleServer(store, runtime)
                with self.assertRaises(console.ConsoleError):
                    await server.start()
                await server.close()
                token.chmod(0o600)
                linked = runtime / "other-token"
                os.link(token, linked)
                with self.assertRaises(console.ConsoleError):
                    await server.start()
                await server.close()
                linked.unlink()
                await server.start()
                self.assertEqual(server.token_path.read_text(), "A" * 43 + "\n")
                await server.close()
                self.assertFalse(server.socket_path.exists())
                await server.start()
                await server.close()



if __name__ == "__main__":
    unittest.main()
