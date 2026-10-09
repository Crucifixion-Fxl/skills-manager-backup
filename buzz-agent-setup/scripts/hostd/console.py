"""Private Unix HTTP console. Store facts are read-only; operations use the root driver.

Browser authentication bootstrap is deliberately outside this module: an approved
operator client supplies Authorization on every request. No TCP listener, token
URL, credential form, binding creation or second approval path is provided.
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import hmac
import inspect
import json
import os
from pathlib import Path
import re
import secrets
import socket
import stat
import struct
import time
from typing import Callable

HEADER_LIMIT = 16384
BODY_LIMIT = 1024
RESPONSE_LIMIT = 1024 * 1024
UI_PATH = Path(__file__).with_name("console_ui.html").absolute()
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}\Z")
HEX = re.compile(r"[0-9a-f]{64}\Z")


class ConsoleError(ValueError):
    """Fixed safe notice only; never retain exceptions or server bodies."""


@dataclass(frozen=True)
class ActionResult:
    ok: bool
    observed: str  # paused, active, backfilled, restarted; obtained by the root driver


@dataclass(frozen=True)
class PublicBinding:
    channel_id: str
    chat_ref: str
    signer_pubkey: str
    heartbeat_at: int
    local_agent_ids: tuple[str, ...]


@dataclass(frozen=True)
class PublicAgent:
    pubkey: str
    app_id: str | None
    channel_ids: tuple[str, ...]


@dataclass(frozen=True)
class PublicTopology:
    bindings: tuple[PublicBinding, ...] = ()
    agents: tuple[PublicAgent, ...] = ()


_FAILURES = {
    "auth": ("还没有通过本机访问验证。", "请通过已授权的本机访问工具重新打开控制台。", "帮我检查 hostd 本机控制台的访问工具与文件权限，不要显示访问令牌。"),
    "origin": ("这次访问来自未授权的页面。", "请从已授权的本机控制台重新操作。", "帮我检查 hostd 控制台的访问来源与本机转发配置，不要显示访问令牌。"),
    "request": ("这次请求的格式无法识别。", "请重新打开控制台，再重复刚才的操作。", "帮我检查 hostd 控制台与访问工具是否兼容，不要输出凭据。"),
    "missing": ("本机没有这个对象，或它不在可见范围内。", "请刷新关系图，选择本机正在管理的对象。", "帮我核对 hostd 本机注册表和可见范围，不要输出凭据或个人信息。"),
    "driver": ("本机运维入口还没有接好，这次操作没有执行。", "请先接好 hostd 的运维处理器，再重复操作。", "帮我检查 hostd 控制台与实际运维处理器的连接，不要输出凭据。"),
    "action": ("这次操作还没有得到成功的执行回执。", "请保留原操作并核验回执；结果未知时不要重新派发。", "帮我核验 hostd 原运维操作的执行回执与当前状态，结果未知时不要重复派发，不要输出凭据或消息正文。"),
    "busy": ("本机正在处理其他请求，请稍后再试。", "请等待当前操作结束，再刷新控制台。", "帮我检查 hostd 控制台的请求和运维队列是否积压，不要输出凭据。"),
    "timeout": ("访问工具没有及时发完请求。", "请重新连接本机控制台后再试。", "帮我检查 hostd 本机访问工具的连接，不要输出访问令牌。"),
    "state": ("本机状态暂时无法安全读取。", "请检查注册表、运行目录权限与数据版本，然后刷新。", "帮我检查 hostd 本机注册表和运行目录的权限与完整性，不要输出凭据或消息正文。"),
    "permission": ("这条连接还没有所需权限。", "请核对该应用的权限，修复后等待连接恢复。", "帮我检查 hostd 应用所需权限与连接状态，不要输出凭据。"),
    "binding": ("这个绑定现在无法正常同步。", "请检查绑定认领、同步 bot 的群权限和连接状态。", "帮我检查 hostd 绑定认领、同步 bot 的群权限与连接状态，不要输出凭据或消息正文。"),
    "connection": ("这条连接还没有恢复。", "请核对网络、应用配置和连接状态。", "帮我检查 hostd 连接的网络和配置，不要输出凭据或消息正文。"),
    "delivery": ("这条投递还没有确认送达。", "请查看连接状态和投递回执，再决定是否需要补洞。", "帮我检查 hostd 投递状态与回执，不要输出消息正文或凭据。"),
}


def notice(kind):
    message, remedy, prompt = _FAILURES[kind]
    return {"message": message, "remedy": "怎么解决：" + remedy, "copy_to_ai": "复制给 AI：" + prompt}


def _error(kind="state"):
    return ConsoleError("\n".join(notice(kind).values()))


def _id(value, *, hexadecimal=False):
    if not isinstance(value, str) or not (HEX if hexadecimal else IDENTIFIER).fullmatch(value):
        raise _error()
    return value


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _private_directory(path):
    if not isinstance(path, Path) or not path.is_absolute() or ".." in path.parts:
        raise _error()
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        for part in path.parts[1:]:
            try:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            except FileNotFoundError:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            os.close(fd)
            fd = child
        meta = os.fstat(fd)
        if meta.st_uid != os.geteuid() or stat.S_IMODE(meta.st_mode) != 0o700:
            raise _error()
        return fd
    except BaseException:
        os.close(fd)
        raise


class _RequestError(Exception):
    def __init__(self, status, kind="request"):
        self.status, self.kind = status, kind


class ConsoleServer:
    def __init__(self, store, runtime_dir: str | Path, *, action: Callable | None = None,
                 public_view: Callable | None = None, operation_readback: Callable | None = None, allowed_hosts=("hostd.local",),
                 request_timeout=2.0, heartbeat_interval=30.0, action_timeout=30.0,
                 max_clients=32, max_operations=128):
        self.store, self.runtime_dir = store, Path(runtime_dir)
        self.socket_path = self.runtime_dir / "console.sock"
        self.token_path = self.runtime_dir / "console.token"
        self.action, self.public_view = action, public_view
        self.operation_readback = operation_readback
        self._coordinator = None
        self.allowed_hosts = frozenset(allowed_hosts)
        if (not self.allowed_hosts or any(not isinstance(host, str) or not re.fullmatch(r"[a-z0-9.-]+(?::[0-9]{1,5})?", host) for host in self.allowed_hosts)
                or not all(isinstance(v, (float, int)) and not isinstance(v, bool) and 0 < v <= 300
                           for v in (request_timeout, heartbeat_interval, action_timeout))
                or type(max_clients) is not int or not 1 <= max_clients <= 128
                or type(max_operations) is not int or not 1 <= max_operations <= 1024):
            raise _error()
        self.request_timeout, self.heartbeat_interval, self.action_timeout = request_timeout, heartbeat_interval, action_timeout
        self.max_clients, self.max_operations = max_clients, max_operations
        self._server = self._loop = self._dir_fd = self._socket_inode = None
        self._token = ""
        self._clients, self._writers, self._subscribers = set(), set(), set()
        self._operations, self._operation_tasks = OrderedDict(), {}
        self._revision, self._closing = 0, False

    @property
    def subscriber_count(self):
        return len(self._subscribers)

    async def start(self):
        if self._server is not None:
            raise _error()
        listener = None
        try:
            self._dir_fd = _private_directory(self.runtime_dir)
            # Never replace a pre-existing socket or unrelated file, including a stale socket.
            try:
                os.stat("console.sock", dir_fd=self._dir_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise _error()
            try:
                token_fd = os.open("console.token", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                                   0o600, dir_fd=self._dir_fd)
            except FileExistsError:
                token_fd = os.open("console.token", os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                                   dir_fd=self._dir_fd)
                try:
                    meta = os.fstat(token_fd)
                    if not stat.S_ISREG(meta.st_mode) or meta.st_uid != os.geteuid() or stat.S_IMODE(meta.st_mode) != 0o600 or meta.st_nlink != 1:
                        raise _error()
                    self._token = os.read(token_fd, 128).decode("ascii").strip()
                    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", self._token):
                        raise _error()
                finally:
                    os.close(token_fd)
            else:
                try:
                    self._token = secrets.token_urlsafe(32)
                    os.write(token_fd, (self._token + "\n").encode("ascii"))
                    os.fsync(token_fd)
                finally:
                    os.close(token_fd)
            from .console_operations import ConsoleCoordinator
            self._coordinator = ConsoleCoordinator(self.store, self._dispatch_action,
                self._authorize_action, self._readback_action, timeout=self.action_timeout)
            await self._coordinator.restore(self._principal())
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            listener.setblocking(False)
            # Bind through the pinned parent, avoiding pathname replacement races.
            listener.bind(f"/proc/self/fd/{self._dir_fd}/console.sock")
            os.chmod("console.sock", 0o600, dir_fd=self._dir_fd, follow_symlinks=False)
            meta = os.stat("console.sock", dir_fd=self._dir_fd, follow_symlinks=False)
            self._socket_inode = (meta.st_dev, meta.st_ino)
            listener.listen(self.max_clients)
            self._loop = asyncio.get_running_loop()
            self._closing = False
            self._server = await asyncio.start_unix_server(self._handle, sock=listener, limit=HEADER_LIMIT)
        except BaseException as exc:
            if listener is not None:
                listener.close()
            await self.close()
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise _error() from None
        return self

    async def close(self):
        self._closing = True
        if self._server is not None:
            self._server.close()
        if self._coordinator is not None:
            await self._coordinator.close()
            self._coordinator = None
        tasks = list(self._operation_tasks.values()) + list(self._clients)
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for writer in tuple(self._writers):
            writer.close()
        # Python 3.12+ waits for accepted transports here. Cancel clients first
        # so an idle SSE heartbeat cannot consume the service stop budget.
        if self._server is not None:
            await self._server.wait_closed()
            self._server = None
        self._writers.clear()
        self._subscribers.clear()
        if self._dir_fd is not None:
            if self._socket_inode is not None:
                try:
                    meta = os.stat("console.sock", dir_fd=self._dir_fd, follow_symlinks=False)
                    if (meta.st_dev, meta.st_ino) == self._socket_inode and stat.S_ISSOCK(meta.st_mode):
                        os.unlink("console.sock", dir_fd=self._dir_fd)
                except FileNotFoundError:
                    pass
            os.close(self._dir_fd)
            self._dir_fd = self._socket_inode = None
        self._token = ""

    def _peer_uid(self, writer):
        peer = writer.get_extra_info("socket")
        if peer is None or not hasattr(socket, "SO_PEERCRED"):
            return None  # unsupported peer verification fails closed
        return struct.unpack("3i", peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))[1]

    def _store_rows(self, *, binding_id=None, agent_id=None):
        queries = {
            "bindings": "SELECT binding_id,channel_id,chat_id,sync_app_id,mirror_pubkey,chat_ref,status,claimed_at,heartbeat_at FROM binding ORDER BY binding_id LIMIT 1001",
            "agents": "SELECT pubkey,app_id,status,updated_at FROM agent ORDER BY pubkey LIMIT 2001",
            "chats": "SELECT agent_id,chat_id,chat_ref,binding_id,status,updated_at FROM agent_chat ORDER BY agent_id,chat_id LIMIT 4001",
            "connections": "SELECT kind,identity,binding_id,status,connected_at,last_event_at,reconnects,error_code,updated_at FROM connection ORDER BY kind,identity LIMIT 4001",
            "deliveries": "SELECT id,binding_id,agent_id,direction,stream,status,source_at,attempts,created_at,updated_at FROM delivery ORDER BY updated_at DESC,id LIMIT 100",
            "joins": "SELECT request_id,agent_id,binding_id,kind,status,created_at,updated_at,deadline FROM join_request ORDER BY updated_at DESC,request_id LIMIT 100",
            "cursors": "SELECT binding_id,agent_id,stream,position,updated_at FROM cursor ORDER BY binding_id,agent_id,stream LIMIT 4001",
            "pause_fences": "SELECT binding_id,state FROM console_pause LIMIT 1001",
        }
        delivery_params = ()
        if binding_id is not None:
            queries["deliveries"] = queries["deliveries"].replace(" FROM delivery ORDER BY", " FROM delivery WHERE binding_id=? ORDER BY")
            delivery_params = (binding_id,)
        elif agent_id is not None:
            queries["deliveries"] = queries["deliveries"].replace(" FROM delivery ORDER BY", " FROM delivery WHERE agent_id=? ORDER BY")
            delivery_params = (agent_id,)
        try:
            with self.store._lock:
                self.store._check_files()
                self.store.conn.execute("SAVEPOINT console_read")
                try:
                    rows = {name: [dict(row) for row in self.store.conn.execute(query, delivery_params if name == "deliveries" else ())] for name, query in queries.items()}
                finally:
                    self.store.conn.execute("RELEASE console_read")
            if (len(rows["bindings"]) > 1000 or len(rows["agents"]) > 2000 or
                    any(len(rows[name]) > 4000 for name in ("chats", "connections", "cursors"))):
                raise _error()
            stopping = {row['binding_id'] for row in rows['pause_fences'] if row['state'] == 'stopping'}
            for binding in rows['bindings']:
                if binding['binding_id'] in stopping and binding['status'] not in ('retired', 'conflict'):
                    binding['status'] = 'stopping'
            return rows
        except Exception:
            raise _error() from None

    def _topology(self, rows):
        nodes, edges = {}, []
        bindings = {row["binding_id"]: row for row in rows["bindings"]}
        agents = {row["pubkey"]: row for row in rows["agents"]}
        apps = {row["sync_app_id"] for row in rows["bindings"]} | {row["app_id"] for row in rows["agents"] if row["app_id"]}
        def node(identity, kind, label, status, visibility="local", **extra):
            nodes.setdefault(identity, {"id": identity, "kind": kind, "label": label,
                                        "status": status, "visibility": visibility, **extra})
        def edge(source, target, kind):
            item = {"source": source, "target": target, "kind": kind}
            if item not in edges:
                edges.append(item)
        def group(chat_id, chat_ref, visibility="local"):
            ref = chat_ref or hashlib.sha256(("buzz-feishu-chat:v1:" + chat_id).encode()).hexdigest()
            _id(ref, hexadecimal=True)
            key = "group:" + ref
            node(key, "group", "群 " + ref[:8], "observed" if visibility == "local" else "public", visibility, chat_ref=ref[:8])
            return key
        node("host:local", "host", "本机注册表", "observed")
        for binding in rows["bindings"]:
            key = "binding:" + _id(binding["binding_id"])
            channel = "channel:" + _id(binding["channel_id"])
            extra = {"notice": notice("binding")} if binding["status"] in {"degraded", "conflict"} else {}
            node(key, "binding", "绑定 " + binding["binding_id"], binding["status"], heartbeat_at=binding["heartbeat_at"], **extra)
            node(channel, "channel", "频道 " + binding["channel_id"], "observed")
            chat = group(binding["chat_id"], binding["chat_ref"])
            app = "app:" + _id(binding["sync_app_id"])
            node(app, "app", "同步 bot " + binding["sync_app_id"], "observed")
            edge(chat, key, "binding"); edge(key, channel, "binding"); edge(key, app, "sync_bot"); edge("host:local", key, "holds")
            if binding["mirror_pubkey"]:
                mirror = "mirror:" + _id(binding["mirror_pubkey"], hexadecimal=True)
                node(mirror, "mirror", "mirror " + binding["mirror_pubkey"][:8], "observed")
                edge(key, mirror, "mirror")
        for agent in rows["agents"]:
            key = "agent:" + _id(agent["pubkey"], hexadecimal=True)
            node(key, "agent", "agent " + agent["pubkey"][:8], agent["status"], updated_at=agent["updated_at"])
            edge("host:local", key, "holds")
            if agent["app_id"]:
                app = "app:" + _id(agent["app_id"])
                node(app, "app", "bot " + agent["app_id"], "observed")
                edge(key, app, "own_bot")
        for chat in rows["chats"]:
            agent = agents.get(chat["agent_id"])
            if agent is None or agent["status"] == "retired":
                continue
            chat_key = group(chat["chat_id"], chat["chat_ref"])
            edge("agent:" + agent["pubkey"], chat_key, "in_group")
            binding = bindings.get(chat["binding_id"])
            if binding:
                edge("agent:" + agent["pubkey"], "channel:" + binding["channel_id"], "member")
        for connection in rows["connections"]:
            if connection["binding_id"] not in bindings and connection["identity"] not in apps | set(agents):
                continue
            if connection["identity"].startswith(("ou_", "on_")):
                continue
            key = "connection:" + connection["kind"] + ":" + _id(connection["identity"])
            extra = {k: connection[k] for k in ("kind", "connected_at", "last_event_at", "reconnects", "updated_at")}
            extra.pop("kind")
            if connection["status"] in {"failed", "disconnected", "backoff"}:
                extra["notice"] = notice("permission" if connection["error_code"] == "permission" else "connection")
            node(key, "connection", {"feishu": "飞书长连接", "relay": "relay 连接", "outlet": "发送器"}[connection["kind"]], connection["status"], **extra)
            target = "binding:" + connection["binding_id"] if connection["binding_id"] in bindings else (
                "agent:" + connection["identity"] if connection["identity"] in agents else "app:" + connection["identity"])
            edge(key, target, "connection")
        related_channels = {row["channel_id"] for row in rows["bindings"]}
        public = self.public_view() if self.public_view is not None else PublicTopology()
        if not isinstance(public, PublicTopology) or len(public.bindings) + len(public.agents) > 2000:
            raise _error()
        for binding in public.bindings:
            if not isinstance(binding, PublicBinding) or not any(a in agents and agents[a]["status"] != "retired" for a in binding.local_agent_ids):
                continue
            channel_id = _id(binding.channel_id)
            ref = _id(binding.chat_ref, hexadecimal=True)
            signer = _id(binding.signer_pubkey, hexadecimal=True)
            if type(binding.heartbeat_at) is not int or binding.heartbeat_at < 0:
                raise _error()
            if channel_id in related_channels:
                continue  # never overwrite local binding facts with public registration
            related_channels.add(channel_id)
            key, channel = "registration:" + channel_id, "channel:" + channel_id
            node(key, "registration", "登记 " + channel_id, "public", "public", heartbeat_at=binding.heartbeat_at,
                 signer_ref=hashlib.sha256(signer.encode()).hexdigest()[:8], visibility_note="只知道公开登记；看不到对方的连接、错误或运行主机。")
            node(channel, "channel", "频道 " + channel_id, "public", "public")
            chat = group("", ref, "public")
            edge(chat, key, "binding"); edge(key, channel, "binding")
            for agent_id in binding.local_agent_ids:
                if agent_id in agents:
                    edge("agent:" + agent_id, channel, "member")
        for agent in public.agents:
            if not isinstance(agent, PublicAgent):
                raise _error()
            channels = set(agent.channel_ids) & related_channels
            if not channels or agent.pubkey in agents:
                continue
            key = "agent:" + _id(agent.pubkey, hexadecimal=True)
            node(key, "agent", "公开 agent " + agent.pubkey[:8], "public", "public",
                 visibility_note="只知道公开资料；看不到它在哪台机器运行或是否在线。")
            if agent.app_id:
                app_id = _id(agent.app_id)
                if not app_id.startswith("cli_"):
                    raise _error()
                app = "app:" + app_id
                node(app, "app", "bot " + app_id, "public", "public")
                edge(key, app, "own_bot")
            for channel_id in sorted(channels):
                edge(key, "channel:" + channel_id, "member")
        return {"nodes": list(nodes.values()), "edges": edges}

    def graph(self):
        rows = self._store_rows()
        graph = self._topology(rows)
        graph["events"] = [self._delivery(row) for row in rows["deliveries"]]
        graph["requests"] = [{k: row[k] for k in ("request_id", "agent_id", "binding_id", "kind", "status", "updated_at", "deadline")}
                             for row in rows["joins"] if row["agent_id"] in {a["pubkey"] for a in rows["agents"]}]
        self._refresh_operations()
        graph["operations"] = [{**{k: op[k] for k in ("id", "kind", "target", "operation", "status", "message", "updated_at")},
                                **({"notice": notice("action")} if op["status"] in {"unknown", "rejected"} else {})}
                               for op in list(self._operations.values())[-30:]]
        graph["visibility_note"] = "只有本机状态和与本机 agent 有关的公开资料；对方机器的运行状态不可见。"
        return graph

    def _delivery(self, row):
        item = {k: row[k] for k in ("id", "binding_id", "agent_id", "direction", "stream", "status", "source_at", "attempts", "updated_at")}
        if row["status"] in {"failed", "unknown", "waiting_receipt"}:
            item["notice"] = notice("delivery")
        return item

    def details(self, kind, target):
        rows = self._store_rows(binding_id=target if kind == "binding" else None, agent_id=target if kind == "agent" else None)
        if kind == "binding":
            row = next((r for r in rows["bindings"] if r["binding_id"] == target), None)
            if row is None:
                raise _RequestError(404, "missing")
            return {"id": target, "status": row["status"], "channel_id": row["channel_id"],
                    "chat_ref": (row["chat_ref"] or hashlib.sha256(("buzz-feishu-chat:v1:" + row["chat_id"]).encode()).hexdigest())[:8],
                    "sync_app_id": row["sync_app_id"], "heartbeat_at": row["heartbeat_at"],
                    "cursors": [{k: c[k] for k in ("agent_id", "stream", "position", "updated_at")} for c in rows["cursors"] if c["binding_id"] == target],
                    "deliveries": [self._delivery(d) for d in rows["deliveries"] if d["binding_id"] == target],
                    "member_reconciliation": {"status": "unobserved", "message": "注册表还没有保存成员对账结果。"},
                    **({"notice": notice("binding")} if row["status"] in {"degraded", "conflict"} else {})}
        if kind == "agent":
            row = next((r for r in rows["agents"] if r["pubkey"] == target), None)
            if row is None:
                raise _RequestError(404, "missing")
            return {"id": target, "status": row["status"], "app_id": row["app_id"], "updated_at": row["updated_at"],
                    "deliveries": [self._delivery(d) for d in rows["deliveries"] if d["agent_id"] == target],
                    "unit_state": "unobserved", "message": "注册表还没有保存 agent 进程的运行状态。"}
        raise _RequestError(404, "missing")

    def notify(self):
        """Call after authoritative Store mutations; safe from root-loop or worker threads."""
        if self._loop is not None and not self._closing:
            self._loop.call_soon_threadsafe(self._publish)

    def _publish(self):
        if self._closing or not self._subscribers:
            return
        try:
            payload = self.graph()
        except Exception:
            payload = {"ok": False, "notice": notice("state")}
        self._revision += 1
        packet = b"id: " + str(self._revision).encode() + b"\nevent: snapshot\ndata: " + _json(payload) + b"\n\n"
        if len(packet) > RESPONSE_LIMIT:
            packet = b"event: failure\ndata: " + _json({"ok": False, "notice": notice("state")}) + b"\n\n"
        for queue in tuple(self._subscribers):
            if queue.full():
                queue.get_nowait()  # coalesce to the current authoritative snapshot
            queue.put_nowait(packet)

    def _principal(self, uid=None):
        uid = os.geteuid() if uid is None else uid
        # Peer UID and the authenticated token stay private; SQL stores only
        # this server-derived identity. No business field can choose a principal.
        return hmac.new(self._token.encode(),
            ("hostd-console-principal:v1:" + str(uid)).encode(), hashlib.sha256).hexdigest()

    def _authorize_action(self, intent):
        try:
            return self.details(intent.kind, intent.target)["status"] != "retired"
        except Exception:
            return False

    async def _dispatch_action(self, kind, target, operation):
        if self.action is None:
            raise _error("driver")
        return await self.action(kind, target, operation)

    async def _readback_action(self, intent):
        if self.operation_readback is None:
            return None
        result = self.operation_readback(intent)
        return await result if inspect.isawaitable(result) else result

    def _operation_view(self, operation):
        if isinstance(operation, dict):
            return json.loads(json.dumps(operation))
        row = operation
        try:
            status = self.details(row.kind, row.target)["status"]
        except Exception:
            status = "retired"
        expected = {"pause": "paused", "resume": "active", "backfill": "backfilled", "restart": "restarted"}[row.action]
        result = {"id": row.id, "kind": row.kind, "target": row.target,
                  "operation": row.action, "status": row.status,
                  "updated_at": row.updated_at,
                  "readback": {"id": row.target, "status": status}}
        if row.status == "completed":
            result['observed'] = expected
            result["message"] = {"pause": "这个绑定已暂停。", "resume": "这个绑定已恢复。",
                "backfill": "这次补洞已完成。", "restart": "这个 agent 的重启已得到运行回执。"}[row.action]
        elif row.status in {"unknown", "rejected"}:
            result.update(notice("action"))
            if row.action == 'pause' and status == 'stopping':
                result['message'] = '暂停请求已保存；当前工作是否结束仍待核验，结果未知时不会重复派发。'
        elif row.action == 'pause' and status == 'stopping':
            result['phase'] = 'stopping'
            result['message'] = '暂停请求已保存，已停止安排新轮次，正在等待当前工作结束。'
        else:
            result["message"] = "操作已排队，等待实际执行回执。"
        return result

    def _refresh_operations(self):
        if self._coordinator is None or not self._token:
            return
        rows = self.store.console_operations(self._principal(), limit=min(128, self.max_operations))
        self._operations = OrderedDict((row.id, self._operation_view(row)) for row in reversed(rows))

    def _enqueue(self, kind, target, operation, *, principal, request_key_hash):
        existing = self.store.console_request(principal, request_key_hash)
        if existing is not None:
            if (existing.kind, existing.target, existing.action) != (kind, target, operation):
                raise _RequestError(409, "busy")
            return self._operation_view(existing)
        detail = self.details(kind, target)
        if detail["status"] == "retired":
            raise _RequestError(409, "missing")
        if self.action is None or self._coordinator is None:
            raise _RequestError(503, "driver")
        if len([task for task in self._operation_tasks.values() if not task.done()]) >= self.max_operations:
            raise _RequestError(503, "busy")
        try:
            reservation = self._coordinator.enqueue(principal, request_key_hash, kind, target, operation)
        except Exception:
            raise _RequestError(409, "busy") from None
        row = reservation.record
        result = self._operation_view(row)
        self._operations[row.id] = result
        if reservation.created:
            self._operation_tasks[row.id] = asyncio.create_task(self._watch_operation(row.id, principal))
        self.notify()
        return result

    async def _watch_operation(self, identity, principal):
        try:
            await self._coordinator.wait_operation(identity, principal)
        finally:
            self._operation_tasks.pop(identity, None)
            self._refresh_operations()
            self.notify()

    async def wait_operation(self, identity):
        row = await self._coordinator.wait_operation(identity, self._principal())
        self._refresh_operations()
        return self._operation_view(row)

    async def _read_request(self, reader):
        deadline = self._loop.time() + self.request_timeout
        try:
            header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), self.request_timeout)
            if len(header) > HEADER_LIMIT:
                raise _RequestError(431)
            lines = header[:-4].decode("ascii").split("\r\n")
            if any(len(line) > 2048 for line in lines) or len(lines) > 33:
                raise _RequestError(431)
            method, target, version = lines[0].split(" ")
            if version != "HTTP/1.1" or method not in {"GET", "POST"} or not re.fullmatch(r"/[A-Za-z0-9_.:/-]*", target) or any(part in {".", ".."} for part in target.split("/")):
                raise _RequestError(400)
            fields = {}
            for line in lines[1:]:
                name, value = line.split(":", 1)
                if not re.fullmatch(r"[A-Za-z0-9-]+", name) or name.lower() in fields or value.startswith("\t"):
                    raise _RequestError(400)
                fields[name.lower()] = value.strip()
            if "transfer-encoding" in fields or "upgrade" in fields or "expect" in fields:
                raise _RequestError(400)
            length = fields.get("content-length", "0")
            if not re.fullmatch(r"[0-9]{1,4}", length) or int(length) > BODY_LIMIT:
                raise _RequestError(413)
            if method == "GET" and int(length):
                raise _RequestError(400)
            body = await asyncio.wait_for(reader.readexactly(int(length)), max(0.001, deadline - self._loop.time()))
            return method, target, fields, body
        except asyncio.TimeoutError:
            raise _RequestError(408, "timeout") from None
        except asyncio.LimitOverrunError:
            raise _RequestError(431) from None
        except (UnicodeError, ValueError, asyncio.IncompleteReadError):
            raise _RequestError(400) from None

    def _authenticate(self, writer, fields, method):
        peer_uid = self._peer_uid(writer)
        if peer_uid != os.geteuid():
            raise _RequestError(403, "auth")
        host = fields.get("host")
        if host not in self.allowed_hosts:
            raise _RequestError(403, "origin")
        origin = fields.get("origin")
        if origin is not None and origin != "http://" + host:
            raise _RequestError(403, "origin")
        if fields.get("sec-fetch-site") not in (None, "same-origin", "none"):
            raise _RequestError(403, "origin")
        auth = fields.get("authorization", "")
        if not hmac.compare_digest(auth, "Bearer " + self._token):
            raise _RequestError(401, "auth")
        if method == "POST":
            if origin != "http://" + host or fields.get("x-hostd-request") != "1":
                raise _RequestError(403, "origin")
            if fields.get("content-type") != "application/json":
                raise _RequestError(415)
        return self._principal(peer_uid)

    async def _send(self, writer, status, payload, *, html=False):
        content = payload.encode("utf-8") if html else _json(payload)
        if len(content) > RESPONSE_LIMIT:
            status, content, html = 507, _json({"ok": False, "notice": notice("state")}), False
        reason = {200: "OK", 202: "Accepted", 400: "Bad Request", 401: "Unauthorized", 403: "Forbidden", 404: "Not Found", 408: "Request Timeout", 409: "Conflict", 413: "Content Too Large", 415: "Unsupported Media Type", 431: "Request Header Fields Too Large", 500: "Internal Server Error", 503: "Service Unavailable", 507: "Insufficient Storage"}.get(status, "Error")
        fields = [f"HTTP/1.1 {status} {reason}", "Content-Type: " + ("text/html; charset=utf-8" if html else "application/json; charset=utf-8"),
                  f"Content-Length: {len(content)}", "Cache-Control: no-store", "Connection: close", "X-Content-Type-Options: nosniff", "Referrer-Policy: no-referrer"]
        if html:
            fields.append("Content-Security-Policy: " + self._csp(payload))
        writer.write(("\r\n".join(fields) + "\r\n\r\n").encode() + content)
        await asyncio.wait_for(writer.drain(), self.request_timeout)

    def _read_ui(self):
        """Read the fixed public asset without following links or accepting special files."""
        path = UI_PATH
        if not path.is_absolute() or ".." in path.parts:
            raise _error()
        directory = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        fd = None
        try:
            for part in path.parts[1:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory)
                os.close(directory)
                directory = child
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=directory)
            meta = os.fstat(fd)
            if not stat.S_ISREG(meta.st_mode) or meta.st_uid != os.geteuid() or meta.st_nlink != 1 or meta.st_size > 256 * 1024:
                raise _error()
            chunks, length = [], 0
            while True:
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                length += len(chunk)
                if length > 256 * 1024:
                    raise _error()
                chunks.append(chunk)
            final = os.fstat(fd)
            if (final.st_size, final.st_mtime_ns) != (meta.st_size, meta.st_mtime_ns):
                raise _error()
            return b"".join(chunks).decode("utf-8")
        finally:
            if fd is not None:
                os.close(fd)
            os.close(directory)

    def _csp(self, html):
        import base64
        hashes = {}
        for tag in ("style", "script"):
            blocks = re.findall(r"<" + tag + r">(.*?)</" + tag + r">", html, re.S)
            hashes[tag] = " ".join("'sha256-" + base64.b64encode(hashlib.sha256(block.encode()).digest()).decode() + "'" for block in blocks)
        return "default-src 'none'; connect-src 'self'; script-src " + hashes["script"] + "; style-src " + hashes["style"] + "; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"

    async def _sse(self, reader, writer):
        queue = asyncio.Queue(maxsize=2)
        self._subscribers.add(queue)
        disconnected = asyncio.create_task(reader.read(1))
        pending = None
        try:
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nCache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\nConnection: close\r\n\r\n")
            initial = b"event: snapshot\ndata: " + _json(self.graph()) + b"\n\n"
            if len(initial) > RESPONSE_LIMIT:
                raise _error()
            writer.write(initial)
            await asyncio.wait_for(writer.drain(), self.request_timeout)
            while not self._closing:
                pending = asyncio.create_task(queue.get())
                done, _ = await asyncio.wait((pending, disconnected), timeout=self.heartbeat_interval, return_when=asyncio.FIRST_COMPLETED)
                if disconnected in done:
                    break
                if pending in done:
                    packet = pending.result()
                else:
                    pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)
                    packet = b": keepalive\n\n"
                writer.write(packet)
                await asyncio.wait_for(writer.drain(), self.request_timeout)
        finally:
            self._subscribers.discard(queue)
            for task in (pending, disconnected):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(*(t for t in (pending, disconnected) if t is not None), return_exceptions=True)

    async def _handle(self, reader, writer):
        task = asyncio.current_task()
        if self._closing:
            writer.close()
            return
        if len(self._clients) >= self.max_clients:
            try:
                await self._send(writer, 503, {"ok": False, "notice": notice("busy")})
            except Exception:
                pass
            writer.close()
            return
        self._clients.add(task); self._writers.add(writer)
        streaming = False
        try:
            method, target, fields, body = await self._read_request(reader)
            principal = self._authenticate(writer, fields, method)
            if method == "POST":
                try:
                    parsed = json.loads(body)
                except (ValueError, UnicodeError):
                    raise _RequestError(400) from None
                if type(parsed) is not dict or parsed:
                    raise _RequestError(400)
                match = re.fullmatch(r"/api/(bindings|agents)/([A-Za-z0-9][A-Za-z0-9_.:-]{0,255})/(pause|resume|backfill|restart)", target)
                if not match or (match[1] == "agents") != (match[3] == "restart"):
                    raise _RequestError(404, "missing")
                request_key = fields.get("idempotency-key", "")
                if not HEX.fullmatch(request_key):
                    raise _RequestError(400)
                result = self._enqueue("binding" if match[1] == "bindings" else "agent", match[2], match[3],
                    principal=principal, request_key_hash=hashlib.sha256(request_key.encode()).hexdigest())
                await self._send(writer, 202, result)
            elif target == "/":
                html = self._read_ui()
                await self._send(writer, 200, html, html=True)
            elif target == "/api/graph":
                await self._send(writer, 200, self.graph())
            elif target == "/api/events":
                streaming = True
                await self._sse(reader, writer)
            else:
                match = re.fullmatch(r"/api/(bindings|agents|operations)/([A-Za-z0-9][A-Za-z0-9_.:-]{0,255})", target)
                if not match:
                    raise _RequestError(404, "missing")
                if match[1] == "operations":
                    record = await self._coordinator.reconcile_operation(match[2], principal) if HEX.fullmatch(match[2]) else None
                    if record is None:
                        raise _RequestError(404, "missing")
                    result = self._operation_view(record)
                else:
                    result = self.details("binding" if match[1] == "bindings" else "agent", match[2])
                await self._send(writer, 200, result)
        except _RequestError as exc:
            if not streaming:
                try:
                    await self._send(writer, exc.status, {"ok": False, "notice": notice(exc.kind)})
                except (ConnectionError, asyncio.TimeoutError):
                    pass
        except (ConnectionError, asyncio.TimeoutError):
            pass
        except asyncio.CancelledError:
            raise
        except Exception:
            if not streaming:
                try:
                    await self._send(writer, 500, {"ok": False, "notice": notice("state")})
                except (ConnectionError, asyncio.TimeoutError):
                    pass
        finally:
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), 0.5)
            except (ConnectionError, asyncio.TimeoutError):
                pass
            self._clients.discard(task); self._writers.discard(writer)
