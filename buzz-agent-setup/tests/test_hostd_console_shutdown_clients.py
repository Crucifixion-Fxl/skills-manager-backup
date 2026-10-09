"""Actual UDS clients must not consume shutdown budget waiting for SSE heartbeat."""
import asyncio
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from hostd.console import ConsoleServer
from hostd.store import Store


class ShutdownClients(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='.console-close-', dir=Path.home())
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / 'state.sqlite3')
        self.server = ConsoleServer(self.store, self.root / 'runtime')
        await self.server.start()
        self.writers = []
        self.closing = None

    async def asyncTearDown(self):
        for writer in self.writers:
            writer.close()
            await writer.wait_closed()
        if self.closing:
            await self.closing
        await self.server.close()
        self.store.close()
        self.tmp.cleanup()

    async def client(self, sse=False):
        reader, writer = await asyncio.open_unix_connection(self.server.socket_path)
        self.writers.append(writer)
        if sse:
            writer.write(('GET /api/events HTTP/1.1\r\nHost: hostd.local\r\nAuthorization: Bearer '
                          + self.server._token + '\r\n\r\n').encode())
            await writer.drain()
            await asyncio.wait_for(reader.readuntil(b'\n\n'), 1)
        return reader, writer

    async def test_default_heartbeat_sse_and_partial_http_close_promptly(self):
        self.assertEqual(self.server.heartbeat_interval, 30)
        await self.client(sse=True)
        await self.client()
        await asyncio.sleep(0)
        self.closing = asyncio.create_task(self.server.close())
        await asyncio.wait_for(asyncio.shield(self.closing), 2)
        self.assertFalse(self.server._clients)
        self.assertFalse(self.server._writers)
        self.assertFalse(self.server._subscribers)
        self.assertIsNone(self.server._server)
        self.assertFalse(self.server.socket_path.exists())

    async def test_shutdown_still_awaits_operation_coordinator(self):
        entered, release = asyncio.Event(), asyncio.Event()
        class Coordinator:
            async def close(self):
                entered.set()
                await release.wait()
        self.server._coordinator = Coordinator()
        await self.client(sse=True)
        self.closing = asyncio.create_task(self.server.close())
        try:
            await asyncio.wait_for(entered.wait(), 2)
            self.assertFalse(self.closing.done())
            self.assertFalse(self.server._server.is_serving())
        finally:
            release.set()
        await asyncio.wait_for(asyncio.shield(self.closing), 2)


if __name__ == '__main__':
    unittest.main()
