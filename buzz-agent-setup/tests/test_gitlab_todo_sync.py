"""TDD contract for the personal-channel GitLab todo sync (ADR-0013).

The script pulls the owner's own pending GitLab todos with the owner's PAT, posts one
message per todo into the owner's personal Buzz Channel, and marks a todo done in
GitLab only when a trusted author replies `todo:done:<id>`.  No LLM sits in this path.
"""

from __future__ import annotations

import http.client
import http.server
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest import mock
import urllib.error


SKILL = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL / "scripts"
import sys  # noqa: E402

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import gitlab_buzz_sync as sync  # noqa: E402


def load():
    path = SCRIPTS / "gitlab_todo_sync.py"
    if not path.is_file():
        raise AssertionError("gitlab_todo_sync.py is required")
    spec = importlib.util.spec_from_file_location("gitlab_todo_sync_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


OWNER = "a" * 64
PUBLISHER = "b" * 64
ASSISTANT = "c" * 64
STRANGER = "d" * 64
DESK = "e" * 64
CHANNEL = "11111111-2222-3333-4444-555555555555"
PAT = "glpat-SECRET-owner-token"
ODD_PAT = 'glpat-A"B\\C-é'  # JSON-escaping changes how this token looks in serialised output
SINCE = 1_789_776_000  # 2026-09-19T00:00:00Z
NOW = 1_789_819_200  # 2026-09-19T12:00:00Z: after `since` and after every default todo's created_at
DAY = 86400
PUBLISHER_KEY = "01" * 32
REAL_PUBLISHER = sync.publisher_pubkey_from_private_key(PUBLISHER_KEY)


def make_config(**over) -> dict:
    cfg = {
        "version": 1,
        "channel_id": CHANNEL,
        "publisher_pubkey": PUBLISHER,
        "owner_pubkey": OWNER,
        "desk_pubkey": DESK,
        "done_authors": [OWNER, ASSISTANT],
        "buzz": {"cli_path": "/opt/buzz-0.5.23/usr/bin/buzz", "cli_sha256": "e" * 64},
        "gitlab": {"base_url": "https://gitlab.example", "username": "jchen",
                   "token_env": "GITLAB_TODO_TOKEN"},
        "todo": {"actions": ["*"], "since": "2026-09-19T00:00:00Z", "max_per_run": 20,
                 "mark_done": True},
    }
    for key, value in over.items():
        section = cfg.get(key)
        if isinstance(section, dict) and isinstance(value, dict):
            cfg[key] = {**section, **value}
        else:
            cfg[key] = value
    return cfg


def make_todo(todo_id: int, *, action="review_requested", target_type="MergeRequest", iid=45,
              project_id=1175, title="修复登录超时", body="请看下 diff", created="2026-09-19T05:00:00Z",
              author="alice") -> dict:
    return {
        "id": todo_id, "state": "pending", "action_name": action, "target_type": target_type,
        "body": body, "created_at": created,
        "author": {"username": author, "name": author.title()},
        "project": {"id": project_id, "path_with_namespace": "grp/proj"},
        "target": {"id": 9000 + todo_id, "iid": iid, "title": title,
                   "web_url": f"https://gitlab.example/grp/proj/-/merge_requests/{iid}"},
        "target_url": f"https://gitlab.example/grp/proj/-/merge_requests/{iid}#note_1",
    }


def make_group_todo(todo_id: int, *, web_url: str, iid=5, target_type="Epic") -> dict:
    """A group-level target (epic): GitLab gives no `project` object."""

    todo = make_todo(todo_id, target_type=target_type, iid=iid)
    todo["project"] = None
    todo["target"]["web_url"] = web_url
    todo["target_url"] = web_url
    return todo


def header_for(todo_id: int, action="review_requested", slug="merge_request") -> str:
    return f"[gitlab-todo:v1][action:{action}][target:{slug}][id:{todo_id}]"


def make_record(todo_id: int, state="ACKED", *, target="1175:MergeRequest:45", reply_to=None,
                created=NOW, delivered_at=NOW, **extra) -> dict:
    rec = {"state": state, "target": target, "header": header_for(todo_id), "reply_to": reply_to,
           "created": created, "delivered_at": delivered_at}
    if state in {"ACKED", "DONE", "RESOLVED"}:
        rec["event_id"] = f"{todo_id:064x}"
    rec.update(extra)
    return rec


class FakeGitLab:
    def __init__(self, todos=None, username="jchen", log=None):
        self.todos = list(todos or [])
        self.username = username
        self.marked: list[int] = []
        self.mark_error: Exception | None = None
        self.mark_errors: dict[int, Exception] = {}  # per todo id; mark_error applies to every id
        self.mark_calls: list[int] = []
        self.fetches = 0
        self.truncated = False
        self.log = log if log is not None else []

    def user(self):
        self.log.append("user")
        return {"username": self.username}

    def pending_todos(self):
        self.log.append("fetch")
        self.fetches += 1
        return list(self.todos)

    def mark_done(self, todo_id: int):
        self.log.append("mark")
        self.mark_calls.append(todo_id)
        if todo_id in self.mark_errors:
            raise self.mark_errors[todo_id]
        if self.mark_error:
            raise self.mark_error
        self.marked.append(todo_id)
        self.todos = [t for t in self.todos if t["id"] != todo_id]


class FakeBuzz:
    def __init__(self, members=None, log=None, publisher=PUBLISHER):
        self.publisher = publisher
        self.members = members if members is not None else {
            OWNER: "owner", publisher: "bot", ASSISTANT: "bot", DESK: "bot"}
        self.sent: list[dict] = []
        self.events: list[dict] = []
        self.send_error: Exception | None = None
        self.reject_when = None  # callable(content, reply_to) -> bool: raise BuzzSendRejected
        self.send_calls = 0
        self.scans: list[int] = []
        self.reactions: list[dict] = []  # kind 7 events; `channel_messages` (kind 9 only) never returns them
        self.reaction_scans: list[int] = []
        self.reaction_error: Exception | None = None
        self.log = log if log is not None else []
        self.now = NOW

    def channel_members(self):
        self.log.append("members")
        return dict(self.members)

    def send(self, content, reply_to=None, mentions=()):
        self.log.append("send")
        self.send_calls += 1
        if self.send_error:
            raise self.send_error
        if self.reject_when and self.reject_when(content, reply_to):
            raise sync.BuzzSendRejected("rejected")
        event_id = f"{len(self.sent) + 1:064x}"
        self.sent.append({"id": event_id, "content": content, "reply_to": reply_to,
                          "mentions": tuple(mentions)})
        tags = [["h", CHANNEL]] + ([["e", reply_to, "", "reply"]] if reply_to else [])
        self.events.append({"id": event_id, "pubkey": self.publisher, "content": content, "tags": tags,
                            "kind": 9, "created_at": self.now})
        return event_id

    def channel_messages(self, since_unix):
        self.log.append("scan")
        self.scans.append(since_unix)
        return [dict(e) for e in self.events
                if not isinstance(e.get("created_at"), int) or e["created_at"] >= since_unix]

    def channel_reactions(self, since_unix):
        self.log.append("reactions")
        self.reaction_scans.append(since_unix)
        if self.reaction_error:
            raise self.reaction_error
        return [dict(e) for e in self.reactions
                if not (isinstance(e.get("created_at"), int) and not isinstance(e.get("created_at"), bool))
                or e["created_at"] >= since_unix]

    def react(self, author, emoji, to, at=None):
        """A kind 7 reaction as the relay returns it: one `e` tag naming the reacted-to message, no `h`, no `p`."""

        event_id = f"{0xe500 + len(self.reactions):064x}"
        self.reactions.append({"id": event_id, "kind": 7, "pubkey": author, "content": emoji,
                               "tags": [["e", to]], "created_at": self.now + 5 if at is None else at})
        return event_id

    def reply(self, author, content, to, at=None):
        event_id = f"{0xf000 + len(self.events):064x}"
        self.events.append({"id": event_id, "pubkey": author, "content": content, "kind": 9,
                            "tags": [["h", CHANNEL], ["e", to, "", "reply"]],
                            "created_at": self.now + 5 if at is None else at})
        return event_id

    def post(self, author, content, at=None, tags=None):
        """A message that is not a reply: only the Channel `h` tag, unless `tags` replaces the whole list (even with junk)."""

        event_id = f"{0xf000 + len(self.events):064x}"
        self.events.append({"id": event_id, "pubkey": author, "content": content, "kind": 9,
                            "tags": [["h", CHANNEL]] if tags is None else tags,
                            "created_at": self.now + 5 if at is None else at})
        return event_id


class Harness(unittest.TestCase):
    def setUp(self):
        self.mod = load()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / "state"

    def run_sync(self, gitlab, buzz, config=None, clock=lambda: NOW):
        return self.mod.run(config or make_config(), env={"GITLAB_TODO_TOKEN": PAT},
                            state_dir=self.state, gitlab=gitlab, buzz=buzz, clock=clock)

    def state_json(self):
        return json.loads((self.state / "todo-state.json").read_text())

    def write_state(self, todos=None, threads=None, raw=None):
        self.state.mkdir(mode=0o700, parents=True, exist_ok=True)
        text = raw if raw is not None else json.dumps(
            {"version": 1, "todos": todos or {}, "threads": threads or {}})
        (self.state / "todo-state.json").write_text(text)

    def write_state_bytes(self, raw: bytes):
        self.state.mkdir(mode=0o700, parents=True, exist_ok=True)
        (self.state / "todo-state.json").write_bytes(raw)

    def crash_after_send(self, buzz):
        original = buzz.send

        def crashing(content, reply_to=None, mentions=()):
            original(content, reply_to, mentions)
            raise sync.SyncError("readback lost")

        buzz.send = crashing
        return original


class ConfigTest(Harness):
    def test_valid_config_passes(self):
        """L1-PTS-001 合法配置通过校验。"""
        self.mod.validate_config(make_config())

    def test_publisher_must_not_be_a_done_author(self):
        """L1-PTS-002 发布者不能是 done 标记的可信作者，否则 GitLab 原文里的 todo:done 行能自我确认。"""
        with self.assertRaises(sync.SyncError):
            self.mod.validate_config(make_config(done_authors=[OWNER, PUBLISHER]))

    def test_desk_must_be_a_distinct_pubkey(self):
        for desk in (None, "bad", OWNER, PUBLISHER, ASSISTANT):
            with self.subTest(desk=desk), self.assertRaises(sync.SyncError):
                self.mod.validate_config(make_config(desk_pubkey=desk))
        missing = make_config()
        missing.pop("desk_pubkey")
        with self.assertRaises(sync.SyncError):
            self.mod.validate_config(missing)

    def test_token_env_is_fixed_and_distinct_from_agent_token(self):
        """L1-PTS-003 人的 PAT 只走 GITLAB_TODO_TOKEN，不与 Agent 的 GITLAB_TOKEN 混用。"""
        with self.assertRaises(sync.SyncError):
            self.mod.validate_config(make_config(gitlab={"token_env": "GITLAB_TOKEN"}))

    def test_rejects_insecure_base_url_unknown_keys_and_audience(self):
        """L1-PTS-004 拒绝 http、未知键与已废除的 audience 块。"""
        for bad in (
            make_config(gitlab={"base_url": "http://gitlab.example"}),
            make_config(extra=1),
            make_config(audience={"mode": "x"}),
            make_config(todo={"max_per_run": 0}),
            make_config(done_authors=[]),
            make_config(owner_pubkey="nope"),
        ):
            with self.assertRaises(sync.SyncError, msg=str(bad)):
                self.mod.validate_config(bad)

    def test_load_config_requires_private_file(self):
        """L1-PTS-005 配置文件必须 0600、非 symlink。"""
        path = Path(self.tmp.name) / "cfg.json"
        path.write_text(json.dumps(make_config()))
        os.chmod(path, 0o644)
        with self.assertRaises(sync.SyncError):
            self.mod.load_config(path)
        os.chmod(path, 0o600)
        self.assertEqual(self.mod.load_config(path)["channel_id"], CHANNEL)

    def test_base_url_must_be_a_plain_https_origin(self):
        """L1-PTS-006 base_url 拒绝 userinfo、反斜杠、空白/控制字符、非法端口、无主机、path/query/fragment。"""
        bad_urls = (
            "https://user:pw@evil.example",
            "https://user@evil.example",
            "https://gitlab.addx.ai\\@evil.com",
            "https://gitlab.addx.ai:abc",
            "https://gitlab.example:99999",
            "https://gitlab.example\n",
            " https://gitlab.example",
            "https://gitlab.exam ple",
            "https://gitlab.example\t",
            "https://gitlab.example\x00",
            "https://gitlab.example\\evil.com",
            "https://gitlab.exämple",
            "https://gitlab.example#",
            "https://:443",
            "https://",
            "https://gitlab.example/path",
            "https://gitlab.example/?q=1",
            "https://gitlab.example?",
            "https://gitlab.example#frag",
            "http://gitlab.example",
            "ftp://gitlab.example",
            "gitlab.example",
            "",
            None,
            123,
        )
        for bad in bad_urls:
            with self.subTest(base_url=bad):
                with self.assertRaises(sync.SyncError):
                    self.mod.validate_config(make_config(gitlab={"base_url": bad}))
        for good in ("https://gitlab.example", "https://gitlab.example/", "https://gitlab.example:8443"):
            with self.subTest(base_url=good):
                self.mod.validate_config(make_config(gitlab={"base_url": good}))

    def test_other_config_branches_are_rejected(self):
        """L1-PTS-007 done_authors、since、max_per_run、state_dir、actions、mark_done 的非法值一律拒绝。"""
        def todo(**kw):
            return make_config(todo=kw)

        bad_configs = {
            "duplicate done author": make_config(done_authors=[OWNER, OWNER]),
            "non-hex done author": make_config(done_authors=[OWNER, "z" * 64]),
            "uppercase done author": make_config(done_authors=[OWNER, "A" * 64]),
            "done_authors not a list": make_config(done_authors=OWNER),
            "since missing": {**make_config(), "todo": {"actions": ["*"], "max_per_run": 20}},
            "since not a string": todo(since=1789776000),
            "since without timezone": todo(since="2026-09-19T00:00:00"),
            "since garbage": todo(since="yesterday"),
            "since placeholder": todo(since="<now-utc>"),
            "max_per_run above the cap": todo(max_per_run=51),
            "max_per_run zero": todo(max_per_run=0),
            "max_per_run bool": todo(max_per_run=True),
            "max_per_run string": todo(max_per_run="5"),
            "relative state_dir": make_config(state_dir="state/todo"),
            "non-string state_dir": make_config(state_dir=5),
            "actions empty": todo(actions=[]),
            "actions not a list": todo(actions="assigned"),
            "actions uppercase": todo(actions=["Assigned"]),
            "actions empty name": todo(actions=[""]),
            "actions non-string": todo(actions=[1]),
            "actions injection": todo(actions=["assigned;drop"]),
            "mark_done not a bool": todo(mark_done="yes"),
            "unknown todo key": todo(bogus=1),
            "todo not an object": make_config(todo=[]),
            "buzz extra key": make_config(buzz={"extra": 1}),
            "channel id uppercase": make_config(channel_id="AAAAAAAA-2222-3333-4444-555555555555"),
            "config not an object": [],
        }
        for label, bad in bad_configs.items():
            with self.subTest(label):
                with self.assertRaises(sync.SyncError):
                    self.mod.validate_config(bad)
        self.mod.validate_config(todo(max_per_run=50))
        self.mod.validate_config(make_config(state_dir="/var/lib/todo"))
        self.mod.validate_config(todo(actions=["assigned", "build_failed", "*"]))


    def test_done_emojis_is_optional_and_accepts_well_formed_lists(self):
        """L1-PTS-107 todo.done_emojis 可选（缺省 = ["✅"]）；合法列表通过：单个、多个、带 U+FE0F、带肤色修饰、ZWJ 序列（U+200D 是格式字符，不是空白/控制字符）、恰好 32 个字符。"""
        self.mod.validate_config(make_config())
        for good in (["✅"], ["✔️", "✅"], ["✅\ufe0f"], ["👍🏽"], ["👨\u200d👩\u200d👧"], ["x" * 32], ["😀" * 32]):
            with self.subTest(done_emojis=good):
                self.mod.validate_config(make_config(todo={"done_emojis": good}))

    def test_done_emojis_rejects_everything_that_is_not_a_short_clean_distinct_list_of_strings(self):
        """L1-PTS-108 done_emojis 非法值一律 SyncError（不是 TypeError）：非列表、空列表、非字符串项、空串、只有 U+FE0F、超过 32 个字符、含空白或控制字符、重复（U+FE0F 变体算重复）、不可哈希的项。"""
        bad_lists = {
            "a bare string": "✅",
            "none": None,
            "a dict": {"✅": 1},
            "empty list": [],
            "non-string item": [5],
            "null item": [None],
            "bool item": [True],
            "list item": [["✅"]],
            "duplicate list items": [["✅"], ["✅"]],
            "dict item": [{"a": 1}],
            "empty string": [""],
            "only a variation selector": ["\ufe0f"],
            "33 characters": ["x" * 33],
            "33 emoji characters": ["😀" * 33],
            "trailing space": ["✅ "],
            "leading space": [" ✅"],
            "inner space": ["✅ ✅"],
            "tab": ["✅\t"],
            "newline": ["✅\n"],
            "NUL": ["\x00"],
            "DEL": ["✅\x7f"],
            "escape": ["\x1b[31m"],
            "no-break space": ["✅\u00a0"],
            "line separator": ["\u2028"],
            "duplicate": ["✅", "✅"],
            "duplicate up to U+FE0F": ["✔️", "✔"],
            "duplicate up to U+FE0F, other order": ["✔", "✔️"],
            "one good and one bad": ["✅", ""],
        }
        self.mod.validate_config(make_config(todo={"done_emojis": ["✅"]}))  # control: the key itself is known
        for label, bad in bad_lists.items():
            with self.subTest(label):
                with self.assertRaises(sync.SyncError) as ctx:
                    self.mod.validate_config(make_config(todo={"done_emojis": bad}))
                self.assertIn("done_emojis", str(ctx.exception))
                self.assertNotIn("unknown keys", str(ctx.exception))

    def test_done_emojis_is_a_todo_key_only_and_unknown_keys_are_still_refused(self):
        """L1-PTS-109 done_emojis 只在 todo 段里认；顶层、gitlab 段里出现，或拼成 done_emoji，仍是未知键。"""
        self.mod.validate_config(make_config(todo={"done_emojis": ["✅"]}))  # control: it is a todo key
        for bad in (make_config(done_emojis=["✅"]), make_config(gitlab={"done_emojis": ["✅"]}),
                    make_config(todo={"done_emoji": ["✅"]}), make_config(todo={"done_reactions": ["✅"]}),
                    make_config(todo={"bogus": 1, "done_emojis": ["✅"]})):
            with self.subTest(bad=bad):
                with self.assertRaises(sync.SyncError):
                    self.mod.validate_config(bad)


class FakeResponse:
    def __init__(self, payload=None, headers=None, raw=None, read_error=None):
        self.payload = raw if raw is not None else json.dumps(payload).encode()
        self.headers = headers or {}
        self.read_error = read_error

    def read(self, n=-1):
        if self.read_error:
            raise self.read_error
        return self.payload if n is None or n < 0 else self.payload[:n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeOpener:
    def __init__(self, routes, on_open=None):
        self.routes = routes
        self.on_open = on_open
        self.requests = []
        self.timeouts = []

    def open(self, request, timeout=None):
        self.requests.append((request.get_method(), request.full_url, dict(request.header_items())))
        self.timeouts.append(timeout)
        if self.on_open:
            self.on_open()
        key = (request.get_method(), request.full_url.split("?")[0])
        result = self.routes[key]
        if callable(result):
            result = result(request)
        if isinstance(result, Exception):
            raise result
        return result


TODOS_URL = "https://gitlab.example/api/v4/todos"


def page_of(request) -> int:
    return int(re.search(r"[?&]page=(\d+)", request.full_url).group(1))


class GitLabClientTest(Harness):
    def client(self, routes, *, clock=None, on_open=None):
        opener = FakeOpener(routes, on_open)
        kwargs = {"clock": clock} if clock else {}
        return self.mod.TodoGitLab(make_config(), {"GITLAB_TODO_TOKEN": PAT}, opener=opener, **kwargs), opener

    def test_only_three_endpoints_are_reachable(self):
        """L1-PTS-010 人的 api PAT 被代码限定为 GET /user、GET /todos、POST /todos/:id/mark_as_done。"""
        client, opener = self.client({})
        for method, path in (
            ("GET", "projects/1175"), ("GET", "projects/1/issues"), ("PUT", "projects/1/issues/2"),
            ("DELETE", "todos/5"), ("POST", "todos/mark_as_done"), ("POST", "todos/5/restore"),
            ("GET", "personal_access_tokens/self"), ("POST", "personal_access_tokens/1/rotate"),
            ("POST", "todos/abc/mark_as_done"), ("GET", "todos/5/../../projects"),
        ):
            with self.assertRaises(sync.SyncError, msg=f"{method} {path}"):
                client.request(method, path)
        self.assertEqual(opener.requests, [])

    def test_a_legal_path_with_the_wrong_method_is_refused(self):
        """L1-PTS-014 白名单是「方法 × 路径」：合法路径配错误方法也被拒，opener 一次都不会被调用。"""
        client, opener = self.client({})
        for method, path in (("POST", "user"), ("GET", "todos/5/mark_as_done"), ("POST", "todos"),
                             ("PUT", "user"), ("DELETE", "todos"), ("PUT", "todos/5/mark_as_done"),
                             ("GET", "/todos/5/mark_as_done")):
            with self.assertRaises(sync.SyncError, msg=f"{method} {path}"):
                client.request(method, path)
        self.assertEqual(opener.requests, [])

    def test_default_opener_refuses_redirects(self):
        """L1-PTS-015 默认 opener 含 NoRedirectHandler：令牌不会被 302 带到别的地址。"""
        client = self.mod.TodoGitLab(make_config(), {"GITLAB_TODO_TOKEN": PAT})
        self.assertTrue(any(isinstance(h, sync.NoRedirectHandler) for h in client.opener.handlers))

    def test_a_302_is_not_followed_and_the_token_is_sent_once(self):
        """L1-PTS-016 真实本地 HTTP 服务返回 302：不跟随，令牌只发给第一个地址一次。"""
        hits = {"first": [], "second": []}

        class Second(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                hits["second"].append(dict(self.headers))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"[]")

            def log_message(self, *args):
                pass

        second = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Second)

        class First(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                hits["first"].append(dict(self.headers))
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{second.server_address[1]}/steal")
                self.end_headers()

            def log_message(self, *args):
                pass

        first = http.server.ThreadingHTTPServer(("127.0.0.1", 0), First)
        for server in (first, second):
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
        env = {"no_proxy": "127.0.0.1", "NO_PROXY": "127.0.0.1"}
        with mock.patch.dict(os.environ, env):
            for name in ("http_proxy", "HTTP_PROXY", "all_proxy", "ALL_PROXY"):
                os.environ.pop(name, None)
            client = self.mod.TodoGitLab(make_config(), {"GITLAB_TODO_TOKEN": PAT})
            client.api = f"http://127.0.0.1:{first.server_address[1]}/api/v4"
            with self.assertRaises(sync.SyncError) as ctx:
                client.user()
        self.assertNotIn(PAT, str(ctx.exception))
        self.assertEqual(len(hits["first"]), 1)
        self.assertEqual({k.lower(): v for k, v in hits["first"][0].items()}.get("private-token"), PAT)
        self.assertEqual(hits["second"], [])

    def test_token_goes_only_in_private_token_header_to_base_origin(self):
        """L1-PTS-011 PAT 只出现在 PRIVATE-TOKEN 头，URL 与错误信息里都没有。"""
        client, opener = self.client({("GET", "https://gitlab.example/api/v4/user"): FakeResponse(
            {"username": "jchen"})})
        client.user()
        _, url, headers = opener.requests[0]
        self.assertNotIn(PAT, url)
        self.assertEqual(headers.get("Private-token"), PAT)
        client, _ = self.client({("GET", "https://gitlab.example/api/v4/user"): urllib.error.HTTPError(
            "https://gitlab.example/api/v4/user", 401, "no", {}, io.BytesIO(b""))})
        with self.assertRaises(sync.SyncError) as ctx:
            client.user()
        self.assertNotIn(PAT, str(ctx.exception))

    def test_pending_todos_uses_pending_state_and_fails_closed_when_pagination_does_not_advance(self):
        """L1-PTS-012 只取 state=pending；分页不推进（同页、倒退、非数字）整轮失败，不当作全集。"""
        client, opener = self.client({("GET", TODOS_URL): FakeResponse(
            [make_todo(1)], headers={"x-next-page": ""})})
        self.assertEqual([t["id"] for t in client.pending_todos()], [1])
        self.assertIn("state=pending", opener.requests[0][1])
        self.assertFalse(client.truncated)
        for label, next_page in (("same page", "1"), ("backwards", "0"), ("garbage", "two")):
            client, _ = self.client({("GET", TODOS_URL): FakeResponse([make_todo(1)], headers={"x-next-page": next_page})})
            with self.subTest(label), self.assertRaises(sync.SyncError):
                client.pending_todos()

    def test_page_budget_is_thirty_pages_then_truncates_instead_of_failing(self):
        """L1-PTS-017 pending 列表上限 30 页：更多时不抛错，返回已拉到的部分并标 truncated（否则第三方灌 500 条即可让整轮永久失败）。"""
        def endless(request):
            page = page_of(request)
            return FakeResponse([make_todo(page * 100 + i, iid=i) for i in range(3)],
                                headers={"x-next-page": str(page + 1)})

        client, opener = self.client({("GET", TODOS_URL): endless})
        found = client.pending_todos()
        self.assertTrue(client.truncated)
        self.assertEqual(len(opener.requests), 30)
        self.assertEqual(len(found), 90)
        self.assertEqual(page_of(mock.Mock(full_url=opener.requests[-1][1])), 30)

    def test_exactly_thirty_pages_is_complete_not_truncated(self):
        """L1-PTS-018 第 30 页就是最后一页（没有下一页）：完整，不算截断；下一次调用会复位 truncated。"""
        def thirty(request):
            page = page_of(request)
            return FakeResponse([make_todo(page)], headers={"x-next-page": str(page + 1) if page < 30 else ""})

        client, opener = self.client({("GET", TODOS_URL): thirty})
        self.assertEqual(len(client.pending_todos()), 30)
        self.assertFalse(client.truncated)
        client.truncated = True
        client.pending_todos()
        self.assertFalse(client.truncated)

    def test_budget_exhausted_between_pages_truncates_instead_of_failing(self):
        """L1-PTS-01E 60 秒预算在翻页中途耗尽：返回已拉到的前几页并标 truncated，不整轮失败（预算与 30 页上限不能各自把整轮变成永久失败）。"""
        clock = {"t": 0.0}

        def endless(request):
            page = page_of(request)
            return FakeResponse([make_todo(page)], headers={"x-next-page": str(page + 1)})

        client, opener = self.client({("GET", TODOS_URL): endless}, clock=lambda: clock["t"],
                                     on_open=lambda: clock.update(t=clock["t"] + 30))
        found = client.pending_todos()  # page 1 and 2 spend the whole 60 seconds; page 3 is refused
        self.assertEqual([t["id"] for t in found], [1, 2])
        self.assertTrue(client.truncated)
        self.assertEqual(len(opener.requests), 2)

    def test_only_budget_exhaustion_truncates_other_errors_still_fail(self):
        """L1-PTS-01F 只有预算耗尽转截断：翻页中途的 HTTP 500、非法 JSON 照旧抛错；第一页就没有预算（一条都没拉到）也抛错。"""
        def second_page_fails(request):
            if page_of(request) == 1:
                return FakeResponse([make_todo(1)], headers={"x-next-page": "2"})
            return urllib.error.HTTPError(TODOS_URL, 500, "err", {}, io.BytesIO(b""))

        client, _ = self.client({("GET", TODOS_URL): second_page_fails})
        with self.assertRaises(sync.SyncError):
            client.pending_todos()
        self.assertFalse(client.truncated)

        def second_page_garbage(request):
            if page_of(request) == 1:
                return FakeResponse([make_todo(1)], headers={"x-next-page": "2"})
            return FakeResponse(raw=b"<html>")

        client, _ = self.client({("GET", TODOS_URL): second_page_garbage})
        with self.assertRaises(sync.SyncError):
            client.pending_todos()
        client, opener = self.client({("GET", TODOS_URL): FakeResponse([make_todo(1)])})
        client.spent = 60  # e.g. the identity check already used the whole budget
        with self.assertRaises(sync.SyncError):
            client.pending_todos()
        self.assertEqual(opener.requests, [])

    def test_mark_done_gone_todo_is_success(self):
        """L1-PTS-013 标 done 时 GitLab 已没有该 todo（404）视为完成。"""
        url = "https://gitlab.example/api/v4/todos/7/mark_as_done"
        client, opener = self.client({("POST", url): urllib.error.HTTPError(url, 404, "nf", {}, io.BytesIO(b""))})
        client.mark_done(7)
        client, opener = self.client({("POST", url): urllib.error.HTTPError(url, 500, "err", {}, io.BytesIO(b""))})
        with self.assertRaises(sync.SyncError):
            client.mark_done(7)

    def test_http_client_exceptions_become_sync_errors(self):
        """L1-PTS-019 IncompleteRead / BadStatusLine / LineTooLong / InvalidURL 转成脱敏的 SyncError，不带着 traceback 逃出去。"""
        url = "https://gitlab.example/api/v4/user"
        errors = (http.client.IncompleteRead(b"par", 10), http.client.BadStatusLine("junk"),
                  http.client.LineTooLong("header line"), http.client.InvalidURL("bad"),
                  http.client.RemoteDisconnected("gone"))
        for exc in errors:
            client, _ = self.client({("GET", url): exc})
            with self.subTest(type(exc).__name__):
                with self.assertRaises(sync.SyncError) as ctx:
                    client.user()
                self.assertNotIn(PAT, str(ctx.exception))
        client, _ = self.client({("GET", url): FakeResponse({}, read_error=http.client.IncompleteRead(b"x"))})
        with self.assertRaises(sync.SyncError):
            client.user()

    def test_response_size_cap_is_five_mebibytes(self):
        """L1-PTS-01A 响应体超过 5 MiB 失败关闭；恰好 5 MiB 的合法 JSON 仍被接受。"""
        cap = 5 * 1024 * 1024
        url = "https://gitlab.example/api/v4/user"
        client, _ = self.client({("GET", url): FakeResponse(raw=b"{}" + b" " * (cap - 1))})
        with self.assertRaises(sync.SyncError):
            client.user()
        client, _ = self.client({("GET", url): FakeResponse(raw=b"{}" + b" " * (cap - 2))})
        self.assertEqual(client.user(), {})

    def test_non_json_and_non_list_todos_fail_closed(self):
        """L1-PTS-01B 非 JSON、todos 不是列表：失败关闭。"""
        client, _ = self.client({("GET", TODOS_URL): FakeResponse(raw=b"<html>")})
        with self.assertRaises(sync.SyncError):
            client.pending_todos()
        client, _ = self.client({("GET", TODOS_URL): FakeResponse({"todos": []})})
        with self.assertRaises(sync.SyncError):
            client.pending_todos()

    def test_budget_counts_request_time_only_not_buzz_scans(self):
        """L1-PTS-01C 60 秒预算只累计 GitLab 请求本身：两次请求之间 Buzz 扫描等多久都不占预算。"""
        clock = {"t": 1000.0}
        url = "https://gitlab.example/api/v4/user"
        client, opener = self.client({("GET", url): FakeResponse({})}, clock=lambda: clock["t"],
                                     on_open=lambda: clock.update(t=clock["t"] + 10))
        for _ in range(6):
            client.user()
            clock["t"] += 100  # a slow Buzz channel scan between GitLab calls
        with self.assertRaises(sync.SyncError):  # 6 x 10s of requests = the whole 60s budget
            client.user()
        self.assertEqual(len(opener.requests), 6)

    def test_request_timeout_shrinks_with_the_remaining_budget(self):
        """L1-PTS-01D 单次请求超时 = min(20 秒, 剩余预算)。"""
        clock = {"t": 0.0}
        url = "https://gitlab.example/api/v4/user"
        client, opener = self.client({("GET", url): FakeResponse({})}, clock=lambda: clock["t"],
                                     on_open=lambda: clock.update(t=clock["t"] + 25))
        client.user()
        client.user()
        client.user()  # 2 x 25s already spent: only 10s of the 60s budget are left
        self.assertEqual(opener.timeouts, [20, 20, 10])


class GateTest(Harness):
    def test_explicit_desk_bot_is_allowed_but_cannot_be_a_human(self):
        config = make_config(desk_pubkey=STRANGER)
        buzz = FakeBuzz({OWNER: "owner", PUBLISHER: "bot", ASSISTANT: "bot", STRANGER: "bot"})
        self.assertEqual(self.run_sync(FakeGitLab([make_todo(1)]), buzz, config)["delivered"], 1)
        for role in ("member", "admin"):
            with self.subTest(role=role), self.assertRaises(sync.SyncError):
                self.run_sync(FakeGitLab([make_todo(1)]),
                              FakeBuzz({OWNER: "owner", PUBLISHER: "bot", STRANGER: role}), config)

    def test_missing_explicit_desk_fails_closed(self):
        with self.assertRaises(sync.SyncError):
            self.run_sync(FakeGitLab([make_todo(1)]),
                          FakeBuzz({OWNER: "owner", PUBLISHER: "bot", ASSISTANT: "bot"}))

    def test_wrong_gitlab_identity_fails_closed(self):
        """L1-PTS-020 PAT 属于别人时整轮失败：不取 todo、不发消息。"""
        gitlab, buzz = FakeGitLab([make_todo(1)], username="mallory"), FakeBuzz()
        with self.assertRaises(sync.SyncError):
            self.run_sync(gitlab, buzz)
        self.assertEqual((gitlab.fetches, buzz.sent), (0, []))

    def test_channel_with_another_human_fails_closed(self):
        """L1-PTS-021 个人 Channel 里出现第二个真人成员，todo 不发（否则你的待办泄给别人）。"""
        buzz = FakeBuzz({OWNER: "owner", STRANGER: "member", PUBLISHER: "bot"})
        gitlab = FakeGitLab([make_todo(1)])
        with self.assertRaises(sync.SyncError):
            self.run_sync(gitlab, buzz)
        self.assertEqual((gitlab.fetches, buzz.sent), (0, []))

    def test_owner_and_publisher_must_be_members(self):
        """L1-PTS-022 owner 不在频道或发布者不是 bot 成员时失败。"""
        for members in ({PUBLISHER: "bot"}, {OWNER: "owner"}, {OWNER: "owner", PUBLISHER: "member"}):
            with self.assertRaises(sync.SyncError, msg=str(members)):
                self.run_sync(FakeGitLab([make_todo(1)]), FakeBuzz(members))

    def test_an_extra_bot_member_fails_closed(self):
        """L1-PTS-023 成员集合必须 ⊆ {owner, publisher} ∪ done_authors：多出来的 bot 也能读到待办，整轮失败关闭。"""
        for role in ("bot", "member", "admin"):
            gitlab = FakeGitLab([make_todo(1)])
            buzz = FakeBuzz({OWNER: "owner", PUBLISHER: "bot", ASSISTANT: "bot", DESK: "bot", STRANGER: role})
            with self.subTest(role), self.assertRaises(sync.SyncError):
                self.run_sync(gitlab, buzz)
            self.assertEqual((gitlab.fetches, buzz.sent), (0, []))

    def test_member_set_may_be_smaller_than_the_allowed_set(self):
        """L1-PTS-024 done_authors 里的助手还没入频道时不算违规：门禁是「子集」而非「相等」。"""
        buzz = FakeBuzz({OWNER: "owner", PUBLISHER: "bot", DESK: "bot"})
        result = self.run_sync(FakeGitLab([make_todo(1)]), buzz)
        self.assertEqual(result["delivered"], 1)

    def test_a_second_human_who_is_a_done_author_still_fails_closed(self):
        """L1-PTS-026 done_authors 里的第二个成员是真人（role 不是 bot）：即使在允许集合里，单真人门禁也整轮失败，不取 todo、不发。"""
        for role in ("member", "admin"):
            gitlab = FakeGitLab([make_todo(1)])
            buzz = FakeBuzz({OWNER: "owner", PUBLISHER: "bot", DESK: "bot", ASSISTANT: role})
            with self.subTest(role), self.assertRaises(sync.SyncError):
                self.run_sync(gitlab, buzz)
            self.assertEqual((gitlab.fetches, buzz.sent), (0, []))

    def test_a_done_author_bot_outside_done_authors_is_not_waved_through(self):
        """L1-PTS-025 done_authors 只有 owner 时，助手 bot 不在允许集合里，整轮失败。"""
        buzz = FakeBuzz({OWNER: "owner", PUBLISHER: "bot", DESK: "bot", ASSISTANT: "bot"})
        with self.assertRaises(sync.SyncError):
            self.run_sync(FakeGitLab([make_todo(1)]), buzz, make_config(done_authors=[OWNER]))


class DeliveryTest(Harness):
    def test_new_todo_posts_header_and_mentions_owner_only(self):
        """L1-PTS-030 一条新 todo → 一条带机器可读 header 的顶层消息，只 @ owner。"""
        gitlab, buzz = FakeGitLab([make_todo(123)]), FakeBuzz()
        result = self.run_sync(gitlab, buzz)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["delivered"], 1)
        (sent,) = buzz.sent
        lines = sent["content"].split("\n")
        self.assertEqual(lines[-1], "[gitlab-todo:v1][action:review_requested][target:merge_request][id:123]")
        self.assertIsNone(sent["reply_to"])
        self.assertEqual(sent["mentions"], (OWNER,))
        for needle in ("修复登录超时", "grp/proj", "alice", "todo:done:123",
                       "https://gitlab.example/grp/proj/-/merge_requests/45"):
            self.assertIn(needle, sent["content"])
        self.assertEqual(self.state_json()["todos"]["123"]["state"], "ACKED")

    def test_result_reports_every_counter(self):
        """L1-PTS-030B 每轮结果含 status、marked_done、resolved、rejected、truncated、seen、delivered、filtered、invalid。"""
        result = self.run_sync(FakeGitLab([make_todo(1)]), FakeBuzz())
        self.assertEqual(result, {"status": "ok", "marked_done": 0, "resolved": 0, "rejected": 0,
                                  "truncated": False, "seen": 1, "delivered": 1, "filtered": 0, "invalid": 0})

    def test_same_target_replies_in_the_first_todo_thread(self):
        """L1-PTS-031 同一 Issue/MR 的后续 todo 回原 Thread，不另开顶层。"""
        gitlab, buzz = FakeGitLab([make_todo(1, action="assigned"), make_todo(2, action="mentioned")]), FakeBuzz()
        self.run_sync(gitlab, buzz)
        first, second = buzz.sent
        self.assertIsNone(first["reply_to"])
        self.assertEqual(second["reply_to"], first["id"])
        other = make_todo(3, iid=99)
        gitlab.todos = [other]
        self.run_sync(gitlab, buzz)
        self.assertIsNone(buzz.sent[2]["reply_to"])

    def test_same_iid_in_different_projects_does_not_share_a_thread(self):
        """L1-PTS-031B 不同项目里 iid 相同的 MR 是两个目标：各开各的 Thread。"""
        todos = [make_todo(1, project_id=1, iid=45), make_todo(2, project_id=2, iid=45)]
        buzz = FakeBuzz()
        self.run_sync(FakeGitLab(todos), buzz)
        self.assertEqual([m["reply_to"] for m in buzz.sent], [None, None])

    def test_group_level_targets_are_keyed_by_their_url_not_just_the_iid(self):
        """L1-PTS-031C 群组级目标（epic）没有 project.id：key 带上目标 web_url，不同群组同 iid 不混线；同一 epic 仍回同一 Thread。"""
        epic_a = make_group_todo(1, web_url="https://gitlab.example/groups/a/-/epics/5", iid=5)
        epic_b = make_group_todo(2, web_url="https://gitlab.example/groups/b/-/epics/5", iid=5)
        epic_a2 = make_group_todo(3, web_url="https://gitlab.example/groups/a/-/epics/5", iid=5)
        buzz = FakeBuzz()
        self.run_sync(FakeGitLab([epic_a, epic_b, epic_a2]), buzz)
        first, second, third = buzz.sent
        self.assertIsNone(first["reply_to"])
        self.assertIsNone(second["reply_to"])
        self.assertEqual(third["reply_to"], first["id"])
        keys = {self.mod.target_key(t) for t in (epic_a, epic_b)}
        self.assertEqual(len(keys), 2)
        self.assertEqual(self.mod.target_key(epic_a), self.mod.target_key(epic_a2))
        no_url = make_group_todo(4, web_url="", iid=5)
        no_url_item = make_group_todo(5, web_url="", iid=5, target_type="WorkItem")
        self.assertNotEqual(self.mod.target_key(no_url), self.mod.target_key(no_url_item))

    def test_project_targets_keep_the_project_scoped_key(self):
        """L1-PTS-031D 有 project.id 的目标：key 只随 (project, 类型, iid) 变化，与 web_url 无关。"""
        one = make_todo(1, project_id=7, iid=3)
        moved = make_todo(2, project_id=7, iid=3)
        moved["target"]["web_url"] = "https://gitlab.example/renamed/-/merge_requests/3"
        other_type = make_todo(3, project_id=7, iid=3, target_type="Issue")
        self.assertEqual(self.mod.target_key(one), self.mod.target_key(moved))
        self.assertNotEqual(self.mod.target_key(one), self.mod.target_key(other_type))

    def test_delivered_todos_are_not_resent(self):
        """L1-PTS-032 已投递的 todo 下一轮不重发。"""
        gitlab, buzz = FakeGitLab([make_todo(1)]), FakeBuzz()
        self.run_sync(gitlab, buzz)
        result = self.run_sync(gitlab, buzz)
        self.assertEqual((len(buzz.sent), result["delivered"]), (1, 0))

    def test_action_allowlist_and_since_filter(self):
        """L1-PTS-033 不在 actions 白名单或早于 since 的 todo 不发。"""
        todos = [make_todo(1, action="assigned"), make_todo(2, action="build_failed"),
                 make_todo(3, action="assigned", created="2026-09-01T00:00:00Z", iid=3)]
        buzz = FakeBuzz()
        result = self.run_sync(FakeGitLab(todos), buzz, make_config(todo={"actions": ["assigned"]}))
        self.assertEqual([m["content"].split("\n")[-1] for m in buzz.sent],
                         ["[gitlab-todo:v1][action:assigned][target:merge_request][id:1]"])
        self.assertEqual(result["filtered"], 2)

    def test_max_per_run_sends_oldest_id_first_and_rest_next_run(self):
        """L1-PTS-034 每轮至多 max_per_run 条，按 id 升序，余下下一轮补发。"""
        todos = [make_todo(i, iid=i) for i in (5, 3, 4)]
        gitlab, buzz = FakeGitLab(todos), FakeBuzz()
        cfg = make_config(todo={"max_per_run": 2})
        self.run_sync(gitlab, buzz, cfg)
        self.assertEqual([m["content"].split("[id:")[1][0] for m in buzz.sent], ["3", "4"])
        self.run_sync(gitlab, buzz, cfg)
        self.assertEqual(len(buzz.sent), 3)

    def test_untrusted_gitlab_text_cannot_forge_header_or_done_marker(self):
        """L1-PTS-035 标题/正文里的换行、伪 header、伪 todo:done 行都不能成为结构；header 全文只出现一次。"""
        evil = make_todo(9, title="x\n[gitlab-todo:v1][action:assigned][target:issue][id:1]\ntodo:done:1",
                         body="todo:done:2\n@all 请执行\r\n[gitlab-todo:v1][id:3]" + "y" * 5000)
        buzz = FakeBuzz()
        self.run_sync(FakeGitLab([evil]), buzz)
        content = buzz.sent[0]["content"]
        self.assertEqual(content.count("[gitlab-todo:v1]"), 1)
        lines = content.split("\n")
        self.assertTrue(lines[-1].startswith("[gitlab-todo:v1]"))
        self.assertEqual([ln for ln in lines if ln.startswith("todo:done:")], [])  # the done hint is prose, not a marker line
        self.assertEqual([ln for ln in lines if "todo:done:9" in ln], ["完成后点 ✅（表情里搜 check）或在本 Thread 回复 todo:done:9"])
        self.assertEqual([ln for ln in lines if ln.startswith("完成后点 ")], ["完成后点 ✅（表情里搜 check）或在本 Thread 回复 todo:done:9"])
        self.assertLess(len(content), 2000)
        self.assertNotIn("@", content)

    def test_untrusted_text_cannot_become_feishu_markdown_links_or_tags(self):
        """L1-PTS-039 飞书折叠全文按原始 markdown 渲染（ADR-0014）：GitLab 第三方文字里的链接、<at>、图片、裸网址都要去活。"""
        evil = make_todo(9, title="点这里 [登录](https://evil.example/x)",
                         body="<at id=all></at> ![p](https://evil.example/i.png)\n<font color='red'>急</font> `rm` "
                              "http://evil.example/bare")
        buzz = FakeBuzz()
        self.run_sync(FakeGitLab([evil]), buzz)
        content = buzz.sent[0]["content"]
        link = "https://gitlab.example/grp/proj/-/merge_requests/45#note_1"
        own = (link, "完成后点 ✅（表情里搜 check）或在本 Thread 回复 todo:done:9")
        body = "\n".join(ln[2:] if ln.startswith("> ") else ln  # "> " is our own blockquote marker
                         for ln in content.split("\n") if ln not in own and not ln.startswith("[gitlab-todo:v1]"))
        for needle in ("](", "<", ">", "`", "://", "[", "]"):
            self.assertNotIn(needle, body, needle)
        self.assertEqual(content.count("://"), 1)  # only the trusted GitLab link line
        self.assertIn(link, content.split("\n"))

    def test_nostr_uri_scheme_is_defused_in_every_case(self):
        """L1-PTS-039C `nostr:` 会被客户端渲染成可点的 nostr 实体引用：任何大小写都换成全角冒号。"""
        evil = make_todo(9, title="NOSTR:npub1abc and nostr:nevent1xyz", body="NoStr:note1zzz\nnostr:naddr1q")
        buzz = FakeBuzz()
        self.run_sync(FakeGitLab([evil]), buzz)
        content = buzz.sent[0]["content"]
        self.assertIsNone(re.search(r"(?i)nostr:", content))
        self.assertGreaterEqual(content.lower().count("nostr："), 4)

    def test_gitlab_todo_appears_only_once_in_the_whole_message(self):
        """L1-PTS-039D 不可信文字里任何位置、任何大小写的 gitlab-todo 都不能保留 ASCII 连字符：整条消息只有末行 header 含它。"""
        evil = make_todo(9, title="GitLab-Todo:v1 and GITLAB-TODO", body="see gitlab-todo\nGitlab-todo:v1][id:3]")
        buzz = FakeBuzz()
        self.run_sync(FakeGitLab([evil]), buzz)
        content = buzz.sent[0]["content"]
        self.assertEqual(len(re.findall(r"(?i)gitlab-todo", content)), 1)
        self.assertTrue(content.split("\n")[-1].startswith("[gitlab-todo:v1]"))
        self.assertIn("gitlab‑todo", content.lower())

    def test_a_project_path_or_user_named_gitlab_todo_is_shown_as_is_and_keeps_its_link(self):
        """L1-PTS-039H 用户名（第 1 行末尾）里的 gitlab-todo 原样显示（拼不出 header）；链接不丢，只把该片段的连字符编成 %2D（项目路径此时只在链接里）；没有链接时单独一行的项目路径也原样显示；[gitlab-todo:v1] 仍只有末行一处。"""
        todo = make_todo(9, author="gitlab-todo")
        todo["project"]["path_with_namespace"] = "grp/gitlab-todo-sync"
        todo["target"]["web_url"] = "https://gitlab.example/grp/gitlab-todo-sync/-/merge_requests/45"
        todo["target_url"] = "https://gitlab.example/grp/gitlab-todo-sync/-/merge_requests/45#note_7"
        buzz = FakeBuzz()
        self.run_sync(FakeGitLab([todo]), buzz)
        lines = buzz.sent[0]["content"].split("\n")
        self.assertTrue(lines[0].endswith(" · gitlab-todo"), lines[0])
        self.assertEqual(lines[1], "https://gitlab.example/grp/gitlab%2Dtodo-sync/-/merge_requests/45#note_7")
        self.assertEqual(lines[2], "> 请看下 diff")
        self.assertEqual(len(lines), 5)  # title, link, the one excerpt line, hint, header: no project line
        self.assertEqual(buzz.sent[0]["content"].count("[gitlab-todo:v1]"), 1)
        self.assertTrue(lines[-1].startswith("[gitlab-todo:v1]"))
        self.assertNotRegex(lines[1], r"(?i)gitlab-todo")
        nolink = make_todo(10, author="gitlab-todo")
        nolink["project"]["path_with_namespace"] = "grp/gitlab-todo-sync"
        nolink["target"]["web_url"] = nolink["target_url"] = "https://evil.example/y"
        buzz = FakeBuzz()
        self.run_sync(FakeGitLab([nolink]), buzz)
        content = buzz.sent[0]["content"]
        self.assertEqual(content.split("\n")[1], "grp/gitlab-todo-sync")  # no link: the project path shows as is, on its own line
        self.assertEqual(content.count("[gitlab-todo:v1]"), 1)
        self.assertNotIn("://", content)

    def test_link_encoding_is_case_insensitive_and_length_is_checked_after_it(self):
        """L1-PTS-039I Gitlab-Todo 任何大小写都编码；编码后的长度才受 300 字符上限约束（每处多 2 个字符）。"""
        prefix = "https://gitlab.example/"
        self.assertEqual(self.link_of(prefix + "Gitlab-Todo/x-y"), prefix + "Gitlab%2DTodo/x-y")
        self.assertEqual(self.link_of(prefix + "a/GITLAB-TODO/b-GitLab-todo"), prefix + "a/GITLAB%2DTODO/b-GitLab%2Dtodo")
        fits = prefix + "gitlab-todo" + "a" * (298 - len(prefix) - 11)  # 298 raw, 300 once encoded
        self.assertEqual(len(self.link_of(fits)), 300)
        self.assertIsNone(self.link_of(fits + "a"))

    def test_a_gitlab_host_named_gitlab_todo_gets_no_link_rather_than_a_broken_one(self):
        """L1-PTS-039J base_url 主机名本身含 gitlab-todo 时无法只编码路径：丢链接，而不是发一个含 ASCII gitlab-todo 或被编坏主机名的链接。"""
        config = make_config(gitlab={"base_url": "https://gitlab-todo.example"})
        todo = make_todo(9)
        todo["target"]["web_url"] = "https://gitlab-todo.example/grp/proj/-/merge_requests/45"
        todo["target_url"] = "https://gitlab-todo.example/grp/proj/-/merge_requests/45#note_1"
        self.assertIsNone(self.mod._link(todo, config))
        buzz = FakeBuzz()
        self.run_sync(FakeGitLab([todo]), buzz, config)
        content = buzz.sent[0]["content"]
        self.assertNotIn("://", content)
        self.assertEqual(len(re.findall(r"(?i)gitlab-todo", content)), 1)

    def test_link_line_only_accepts_the_configured_gitlab_origin_without_markdown_chars(self):
        """L1-PTS-040A 链接行只接受配置的 GitLab 源，且不含会被当作 markdown 的字符。"""
        urls = ("https://evil.example/x", "https://gitlab.example/a b", "https://gitlab.example/a](https://evil.example)")
        for n, url in enumerate(urls):
            todo = make_todo(70 + n, iid=70 + n)
            todo["target"]["web_url"] = url
            todo["target_url"] = url
            buzz = FakeBuzz()
            self.run_sync(FakeGitLab([todo]), buzz)
            self.assertNotIn("://", buzz.sent[0]["content"], url)  # no link line at all: nothing to fall back to

    def link_of(self, url):
        todo = make_todo(70)
        todo["target"]["web_url"] = url
        todo["target_url"] = url
        return self.mod._link(todo, make_config())

    def test_link_origin_match_is_exact_not_a_string_prefix(self):
        """L1-PTS-040B 域名前缀陷阱：https://gitlab.example.evil.com、gitlab.examplex、userinfo 都不是配置的源。"""
        for url in ("https://gitlab.example.evil.com/x", "https://gitlab.examplex/x",
                    "https://gitlab.example@evil.com/x", "http://gitlab.example/x", "//gitlab.example/x",
                    "https://gitlab.example"):
            with self.subTest(url):
                self.assertIsNone(self.link_of(url))
        self.assertEqual(self.link_of("https://gitlab.example/g/p/-/issues/1#note_2"),
                         "https://gitlab.example/g/p/-/issues/1#note_2")

    def test_link_length_and_charset_are_bounded(self):
        """L1-PTS-040C 链接 ≤ 300 字符；空白、markdown 字符、控制/零宽/bidi 字符、@、反斜杠一律不出链接。"""
        prefix = "https://gitlab.example/"
        self.assertEqual(len(self.link_of(prefix + "a" * (300 - len(prefix)))), 300)
        self.assertIsNone(self.link_of(prefix + "a" * (301 - len(prefix))))
        for ch in (" ", "\t", "\n", "[", "]", "<", ">", "(", ")", "`", "\\", "@", '"', "​", "‮", "\x00"):
            with self.subTest(char=repr(ch)):
                self.assertIsNone(self.link_of(prefix + "a" + ch + "b"))

    def test_clean_strips_control_bidi_and_zero_width_characters(self):
        """L1-PTS-040D 不可信文字里的控制、bidi、零宽、私用字符全部去掉，压成一行。"""
        dirty = "a\x00b‮c​d﻿e⁦f\x1bg hi\nj\tk"
        cleaned = self.mod._clean(dirty, 200)
        self.assertNotRegex(cleaned, r"[\x00-\x1f​-‏ -‮⁦-⁩﻿]")
        self.assertEqual(cleaned, "a b c d e f g h i j k")

    def test_summary_and_total_length_are_bounded(self):
        """L1-PTS-040E 摘要最多 3 行、总长有上限：超长正文与超长标题不会撑出长消息。"""
        many = make_todo(5, body="\n".join(f"line {n}" for n in range(20)))
        excerpt = [ln for ln in self.mod.render_todo(many, make_config()).split("\n") if ln.startswith("> ")]
        self.assertEqual(len(excerpt), 3)
        huge = make_todo(6, title="t" * 1000, body="\n".join("z" * 400 for _ in range(30)))
        text = self.mod.render_todo(huge, make_config())
        excerpt = [ln for ln in text.split("\n") if ln.startswith("> ")]
        self.assertTrue(all(len(ln) <= 2 + 160 for ln in excerpt))
        self.assertLessEqual(sum(len(ln) - 2 for ln in excerpt), 300 + 160)
        self.assertLessEqual(len(text), 900)

    def test_first_line_is_a_readable_summary_for_the_feishu_preview(self):
        """L1-PTS-039B 飞书预览只取清洗后前 120 字：第一行必须是「动作 · 编号 标题 · 发起人」（标题被截断时发起人后缀仍完整），机器 header 放末行。"""
        todo = make_todo(5, title="标" * 300)
        buzz = FakeBuzz()
        self.run_sync(FakeGitLab([todo]), buzz)
        lines = buzz.sent[0]["content"].split("\n")
        self.assertTrue(lines[0].startswith("请你评审 · !45 "))
        self.assertLessEqual(len(lines[0]), 120)
        self.assertNotIn("gitlab-todo", lines[0])
        self.assertTrue(lines[0].endswith(" · alice"), lines[0])
        self.assertEqual(lines[1], "https://gitlab.example/grp/proj/-/merge_requests/45#note_1")  # no project line behind line 1

    def test_no_action_label_contains_an_ascii_at_sign(self):
        """L1-PTS-039E 任何动作标签都不含 ASCII @；每个动作渲染出的整条消息也不含 @，第一行 ≤ 120。"""
        for action, label in self.mod.ACTION_LABELS.items():
            self.assertNotIn("@", label, action)
        self.assertEqual(self.mod.ACTION_LABELS["directly_addressed"], "直接点名了你")
        for action in [*self.mod.ACTION_LABELS, "brand_new_action"]:
            with self.subTest(action):
                content = self.mod.render_todo(make_todo(5, action=action), make_config())
                self.assertNotIn("@", content)
                self.assertLessEqual(len(content.split("\n")[0]), 120)

    def test_first_line_is_capped_at_110_even_with_a_huge_ref_and_title(self):
        """L1-PTS-039F 第一行整体截到 110 字符以内：很长的 iid、target_type、标题都撑不出去。"""
        huge_iid = make_todo(5, title="标" * 300)
        huge_iid["target"]["iid"] = "9" * 500
        huge_type = make_todo(6, target_type="X" * 300, title="标" * 300)
        huge_type["target"]["iid"] = None
        for todo in (huge_iid, huge_type):
            first = self.mod.render_todo(todo, make_config()).split("\n")[0]
            self.assertLessEqual(len(first), 110)
            self.assertNotIn("\n", first)

    def test_first_line_ref_cannot_carry_markdown_or_mentions(self):
        """L1-PTS-039G 编号（iid）与目标类型也是不可信文字：第一行里不出现 @、方括号、://。"""
        todo = make_todo(5)
        todo["target"]["iid"] = "1@all](https://evil.example/x)"
        first = self.mod.render_todo(todo, make_config()).split("\n")[0]
        for needle in ("@", "[", "]", "://", "<"):
            self.assertNotIn(needle, first)
        odd_type = make_todo(6, target_type="Foo[bar](https://evil.example)@all")
        odd_type["target"]["iid"] = None
        first = self.mod.render_todo(odd_type, make_config()).split("\n")[0]
        for needle in ("@", "[", "]", "://"):
            self.assertNotIn(needle, first)

    def test_send_failure_stops_the_round_and_keeps_pending(self):
        """L1-PTS-036 发送失败（结果未知）：整轮失败、后续不发、状态保持 PENDING 等下一轮对账。"""
        gitlab, buzz = FakeGitLab([make_todo(1, iid=1), make_todo(2, iid=2)]), FakeBuzz()
        buzz.send_error = sync.SyncError("relay down")
        with self.assertRaises(sync.SyncError):
            self.run_sync(gitlab, buzz)
        self.assertEqual(self.state_json()["todos"]["1"]["state"], "PENDING")
        self.assertNotIn("2", self.state_json()["todos"])

    def test_pending_is_on_disk_before_the_message_is_sent(self):
        """L1-PTS-038 发送发生的那一刻 PENDING 已经落盘（kill -9 时没有 finally 兜底）。"""
        gitlab, buzz = FakeGitLab([make_todo(1)]), FakeBuzz()
        seen = []
        original_send = buzz.send

        def spy(content, reply_to=None, mentions=()):
            seen.append(self.state_json()["todos"]["1"]["state"])
            return original_send(content, reply_to, mentions)

        buzz.send = spy
        self.run_sync(gitlab, buzz)
        self.assertEqual(seen, ["PENDING"])

    def test_invalid_todos_are_counted_and_never_delivered(self):
        """L1-PTS-03A id 为 0/负数/字符串/布尔/缺失，或 created_at 缺失/非法/无时区：计入 invalid，不发。"""
        bad = [make_todo(0), make_todo(-3), make_todo(4), make_todo(5), make_todo(6), make_todo(7),
               make_todo(8, created="garbage"), make_todo(9, created="2026-09-19T05:00:00")]
        bad[2]["id"] = "4"
        bad[3]["id"] = True
        del bad[4]["id"]
        bad[5]["created_at"] = None
        buzz = FakeBuzz()
        result = self.run_sync(FakeGitLab(bad), buzz)
        self.assertEqual((result["invalid"], result["delivered"], result["seen"]), (8, 0, 8))
        self.assertEqual(buzz.sent, [])

    def test_duplicate_ids_in_one_batch_are_delivered_once(self):
        """L1-PTS-03B 同一批里 id 重复（分页漂移）只发一次。"""
        buzz = FakeBuzz()
        result = self.run_sync(FakeGitLab([make_todo(7), make_todo(7), make_todo(8, iid=46)]), buzz)
        self.assertEqual(len(buzz.sent), 2)
        self.assertEqual(result["delivered"], 2)
        self.assertEqual(sorted(self.state_json()["todos"]), ["7", "8"])

    def test_truncated_pending_list_still_delivers_and_reports_truncated(self):
        """L1-PTS-03C pending 超过 30 页时不整轮失败：处理已拉到的部分并在结果里带 truncated:true。"""
        gitlab, buzz = FakeGitLab([make_todo(1)]), FakeBuzz()
        gitlab.truncated = True
        result = self.run_sync(gitlab, buzz)
        self.assertEqual((result["status"], result["delivered"], result["truncated"]), ("ok", 1, True))
        self.assertEqual(len(buzz.sent), 1)


BASE = "https://gitlab.example"


def real_todo(todo_id, action, target_type, iid, *, title, body, target_url, web_url, author="alice",
              project="infra/buzz-deploy"):
    """The shapes GitLab really sends: `target_url` is the todo's own link, `target.web_url` the target page."""

    todo = make_todo(todo_id, action=action, target_type=target_type, iid=iid, title=title, body=body, author=author)
    todo["project"]["path_with_namespace"] = project
    todo["target"]["web_url"] = web_url
    todo["target_url"] = target_url
    return todo


class TodoLayoutTest(Harness):
    """Concise layout: title · author, bare link (else the project path), [excerpt], one done hint line, header."""

    ISSUE = f"{BASE}/infra/buzz-deploy/-/issues/67"
    MR = f"{BASE}/infra/buzz-deploy/-/merge_requests/45"

    def assigned(self, **over):
        args = dict(title="补齐个人待办的说明", body="补齐个人待办的说明", target_url=self.ISSUE, web_url=self.ISSUE)
        args.update(over)
        return real_todo(53663, "assigned", "Issue", 67, **args)

    def mentioned(self, **over):
        args = dict(title="升级依赖", body="@jchen 这个 diff 你看下\n我怀疑 lockfile 没同步", author="bob",
                    target_url=f"{self.MR}#note_623176", web_url=self.MR)
        args.update(over)
        return real_todo(53664, "mentioned", "MergeRequest", 45, **args)

    def build_failed(self, **over):
        args = dict(title="升级依赖", body="升级依赖", author="ci",
                    target_url=f"{self.MR}/pipelines", web_url=self.MR)
        args.update(over)
        return real_todo(53665, "build_failed", "MergeRequest", 45, **args)

    def render(self, todo, config=None):
        return self.mod.render_todo(todo, config or make_config())

    def link_of(self, target_url, web_url, config=None):
        todo = make_todo(70)
        todo["target"]["web_url"] = web_url
        todo["target_url"] = target_url
        return self.mod._link(todo, config or make_config())

    def test_a_typical_assigned_todo_is_five_lines(self):
        """L1-PTS-078 典型「指派给你」（正文与标题相同）恰好 4 行：「标题 · 发起人」行、裸网址、单行完成指引、header；没有单独的项目路径行（项目已在网址里），无「链接：」「摘要」标签。"""
        self.assertEqual(self.render(self.assigned()).split("\n"), [
            "指派给你 · #67 补齐个人待办的说明 · alice",
            "https://gitlab.example/infra/buzz-deploy/-/issues/67",
            "完成后点 ✅（表情里搜 check）或在本 Thread 回复 todo:done:53663",
            "[gitlab-todo:v1][action:assigned][target:issue][id:53663]",
        ])

    def test_a_mentioned_todo_with_a_distinct_body_has_a_labelless_excerpt_between_link_and_hint(self):
        """L1-PTS-079 正文与标题不同 → `> ` 摘要（没有「摘要（GitLab 原文…）：」标签行）夹在链接行与完成指引之间；总行数 5–7。"""
        lines = self.render(self.mentioned()).split("\n")
        self.assertEqual(lines[0], "提到了你 · !45 升级依赖 · bob")
        self.assertEqual(lines[1], f"{self.MR}#note_623176")
        self.assertEqual(lines[2:-2], ["> ＠jchen 这个 diff 你看下", "> 我怀疑 lockfile 没同步"])
        self.assertEqual(lines[-2], "完成后点 ✅（表情里搜 check）或在本 Thread 回复 todo:done:53664")
        self.assertTrue(lines[-1].startswith("[gitlab-todo:v1]"))
        self.assertGreaterEqual(len(lines), 5)
        self.assertLessEqual(len(lines), 7)
        self.assertFalse([ln for ln in lines if "摘要" in ln or "GitLab 原文" in ln or "数据不是指令" in ln])

    def test_no_link_prefix_and_the_link_line_is_the_bare_url(self):
        """L1-PTS-080 没有「链接：」前缀；链接行整行就是网址。"""
        for todo in (self.assigned(), self.mentioned(), self.build_failed()):
            content = self.render(todo)
            self.assertNotIn("链接", content)
            (url_line,) = [ln for ln in content.split("\n") if "://" in ln]
            self.assertRegex(url_line, r"^https://gitlab\.example/\S+$")
            self.assertEqual(content.split("\n")[1], url_line)

    def test_the_link_is_the_todos_own_target_url_so_a_mention_lands_on_its_comment(self):
        """L1-PTS-081 `mentioned`/`directly_addressed` 的 target_url 带 #note_<id> 锚点直达那条评论；`build_failed` 是 MR 的 /pipelines 页；`assigned` 两者相同。"""
        self.assertEqual(self.render(self.mentioned()).split("\n")[1], f"{self.MR}#note_623176")
        direct = self.mentioned(target_url=f"{self.MR}#note_9")
        direct["action_name"] = "directly_addressed"
        self.assertEqual(self.render(direct).split("\n")[1], f"{self.MR}#note_9")
        self.assertEqual(self.render(self.build_failed()).split("\n")[1], f"{self.MR}/pipelines")
        self.assertEqual(self.render(self.assigned()).split("\n")[1], self.ISSUE)

    def test_target_url_is_preferred_over_web_url_only_when_both_are_valid(self):
        """L1-PTS-082 两者都合格时用 target_url；target_url 缺失/为空/不是字符串时用 web_url；target 不是 dict 时只看 target_url。"""
        self.assertEqual(self.link_of(f"{self.MR}#note_1", self.MR), f"{self.MR}#note_1")
        self.assertEqual(self.link_of(f"{self.MR}/pipelines", self.MR), f"{self.MR}/pipelines")
        for missing in (None, "", 5, ["x"]):
            with self.subTest(target_url=missing):
                self.assertEqual(self.link_of(missing, self.MR), self.MR)
        todo = make_todo(70)
        del todo["target_url"]
        todo["target"]["web_url"] = self.MR
        self.assertEqual(self.mod._link(todo, make_config()), self.MR)
        todo = make_todo(70)
        todo["target"] = None
        todo["target_url"] = f"{self.MR}#note_4"
        self.assertEqual(self.mod._link(todo, make_config()), f"{self.MR}#note_4")

    def test_an_invalid_target_url_falls_back_to_the_valid_web_url_and_both_invalid_gives_no_link(self):
        """L1-PTS-083 target_url 不合格（别的源、域名前缀陷阱、含 []<>()、含空白、超长）→ 回退 web_url；两者都不合格 → 没有链接行。"""
        long_tail = "a" * 301
        bad = ("https://evil.example/x", "https://gitlab.example.evil.com/x", "http://gitlab.example/x",
               f"{self.MR}#a](https://evil.example)", f"{self.MR}#<b>", f"{self.MR}#(x)", f"{self.MR}#a b",
               f"{self.MR}#a\tb", f"{self.MR}#{long_tail}")
        for url in bad:
            with self.subTest(target_url=url):
                self.assertEqual(self.link_of(url, self.MR), self.MR)
                self.assertIsNone(self.link_of(url, url))
                self.assertIsNone(self.link_of(url, "https://evil.example/y"))
                self.assertIsNone(self.link_of(url, None))
        for url in bad:  # and the other way round: a good target_url is not spoiled by a bad web_url
            with self.subTest(web_url=url):
                self.assertEqual(self.link_of(f"{self.MR}#note_1", url), f"{self.MR}#note_1")
        todo = self.mentioned(target_url="https://evil.example/x", web_url="https://evil.example/y")
        content = self.render(todo)
        self.assertNotIn("://", content)
        lines = content.split("\n")
        self.assertEqual(lines[1], "infra/buzz-deploy")  # no link line: the project path takes its place, right behind line 1
        self.assertEqual(lines[2][:2], "> ")  # and the excerpt follows it

    def test_the_gitlab_todo_hyphen_is_encoded_and_length_checked_on_target_url_too(self):
        """L1-PTS-084 target_url 上同样：gitlab-todo 片段编成 %2D；300 字符上限按编码后算，超了就回退 web_url，不是丢链接；主机名含 gitlab-todo 时两者都不出链接。"""
        prefix = f"{BASE}/"
        self.assertEqual(self.link_of(prefix + "grp/gitlab-todo-sync/-/issues/1#note_2", self.MR),
                         prefix + "grp/gitlab%2Dtodo-sync/-/issues/1#note_2")
        self.assertEqual(self.link_of(prefix + "a/GITLAB-TODO/b#n", self.MR), prefix + "a/GITLAB%2DTODO/b#n")
        fits = prefix + "gitlab-todo" + "a" * (298 - len(prefix) - 11)  # 298 raw, 300 once encoded
        self.assertEqual(len(self.link_of(fits, self.MR)), 300)
        self.assertEqual(self.link_of(fits + "a", self.MR), self.MR)
        config = make_config(gitlab={"base_url": "https://gitlab-todo.example"})
        host = "https://gitlab-todo.example/grp/proj/-/issues/1"
        self.assertIsNone(self.link_of(host + "#note_1", host, config))

    def test_the_excerpt_is_left_out_when_the_body_says_nothing_the_title_does_not(self):
        """L1-PTS-085 清洗后的正文等于清洗后的标题、或正文为空 → 没有摘要：空白/换行/控制字符/被去活的括号之差、超过标题上限的长标题、None、只有空白都算。"""
        long_title = "标题" * 75  # 150 characters: the title line is cut at 100, the comparison must not be
        cases = {
            "identical": ("补齐说明", "补齐说明"),
            "whitespace": ("补齐  说明", "  补齐\n说明 \r\n"),
            "control": ("补齐 说明", "补齐\x00说明\u200b"),  # a control character cleans to a space
            "neutralised": ("修 [x] 的 <y>", "修 ［x］ 的 ＜y＞"),
            "long": (long_title, long_title),
            "very-long": (long_title * 4, long_title * 4),  # longer than the 160-character excerpt line cap as well
            "empty": ("补齐说明", ""),
            "none": ("补齐说明", None),
            "blank": ("补齐说明", " \n\t \r\n "),
            "control-only": ("补齐说明", "\x00\x1b​"),
        }
        for name, (title, body) in cases.items():
            with self.subTest(name):
                content = self.render(self.assigned(title=title, body=body))
                self.assertEqual([ln for ln in content.split("\n") if ln.startswith("> ")], [], content)
                self.assertEqual(len(content.split("\n")), 4)

    def test_the_excerpt_appears_when_the_body_differs_from_the_title_even_slightly(self):
        """L1-PTS-086 正文与标题只要有一点不同就出摘要（只有 `> ` 行，没有标签行）；正文里含标题也算不同。"""
        for body in ("补齐说明。", "补齐说明\n补充一行", "请看：补齐说明", "补齐"):
            with self.subTest(body):
                content = self.render(self.assigned(title="补齐说明", body=body))
                lines = content.split("\n")
                excerpt = [ln for ln in lines if ln.startswith("> ")]
                self.assertTrue(excerpt)
                self.assertEqual(lines[2:2 + len(excerpt)], excerpt)  # right behind the link line
                self.assertEqual(len(lines), 4 + len(excerpt))

    def test_the_excerpt_is_three_lines_and_three_hundred_characters_at_most(self):
        """L1-PTS-087 常量 EXCERPT_LINES=3、EXCERPT_MAX=300、每行 160；边界：累计到 300 才停，299 还要再收一行。"""
        self.assertEqual((self.mod.EXCERPT_LINES, self.mod.EXCERPT_MAX, self.mod.EXCERPT_LINE_MAX), (3, 300, 160))

        def excerpt_of(*lengths):
            body = "\n".join(f"{n:03d}" + "a" * (length - 3) for n, length in enumerate(lengths))
            return [ln for ln in self.render(self.assigned(body=body)).split("\n") if ln.startswith("> ")]

        self.assertEqual(len(excerpt_of(*[10] * 9)), 3)  # the line cap
        self.assertEqual(len(excerpt_of(150, 150, 10, 10)), 2)  # 300 reached: stop
        self.assertEqual(len(excerpt_of(150, 149, 10, 10)), 3)  # 299: one more
        self.assertEqual(len(excerpt_of(150, 149, 10, 10, 10)), 3)  # ... but never a fourth
        self.assertEqual([len(ln) - 2 for ln in excerpt_of(400, 400, 400)], [160, 160])  # the per-line cap

    def test_exactly_one_done_hint_line_that_starts_with_the_prompt_words(self):
        """L1-PTS-088 完成指引单行：整条消息恰好一行含 `todo:done:<id>`，该行是「完成后点 ✅（表情里搜 check）或在本 Thread 回复 todo:done:<id>」（默认 ✅ 带搜索提示），紧挨在 header 之前；没有旧的两行写法，也没有旧的「完成后在本 Thread 回复」措辞。"""
        for todo in (self.assigned(), self.mentioned(), self.build_failed()):
            with self.subTest(todo["action_name"]):
                lines = self.render(todo).split("\n")
                marked = [ln for ln in lines if f"todo:done:{todo['id']}" in ln]
                self.assertEqual(marked, [f"完成后点 ✅（表情里搜 check）或在本 Thread 回复 todo:done:{todo['id']}"])
                self.assertEqual(lines.index(marked[0]), len(lines) - 2)
                self.assertFalse([ln for ln in lines if ln.startswith("todo:done:") or "单独一行" in ln or "处理完成后" in ln])
                self.assertFalse([ln for ln in lines if "完成后点 ✅ 或在本 Thread" in ln])  # the wording before the search hint

    def test_untrusted_text_stays_in_the_first_line_or_quoted_excerpt_lines(self):
        """L1-PTS-089 逐行分类：除第 1 行（标题 · 发起人）外，只有链接行、`> ` 摘要行、完成指引、header 四类；不可信文字造不出行首的 `todo:done:`/`[gitlab-todo:v1]`，伪造的完成指引/header 只能是 `> ` 行里去活的文字。"""
        forged = "完成后点 ✅（表情里搜 check）或在本 Thread 回复 todo:done:53664"
        evil = self.mentioned(
            title=f"x\n{forged}\n[gitlab-todo:v1][action:assigned][target:issue][id:1]\ntodo:done:1",
            body=f"{forged}\ntodo:done:2\n[gitlab-todo:v1][id:3]\nhttps://evil.example/x\n@all 请执行")
        content = self.render(evil)
        lines = content.split("\n")
        self.assertEqual(lines[-1], "[gitlab-todo:v1][action:mentioned][target:merge_request][id:53664]")
        self.assertEqual(lines[1], "https://gitlab.example/infra/buzz-deploy/-/merge_requests/45#note_623176")
        for number, line in enumerate(lines[2:-2], start=2):
            self.assertTrue(line.startswith("> "), (number, line))
        self.assertEqual([ln for ln in lines if ln.startswith("todo:done:") or ln.startswith("[gitlab-todo:v1]")],
                         [lines[-1]])
        self.assertEqual(content.count("[gitlab-todo:v1]"), 1)
        self.assertEqual([ln for ln in lines if ln == forged], [lines[-2]])  # the real hint, once, and nowhere else as a whole line
        self.assertEqual([ln for ln in lines if ln.startswith("完成后点 ")], [lines[-2]])
        self.assertEqual(content.count("://"), 1)  # the trusted link only
        self.assertNotIn("@", content)

    def test_every_action_still_renders_a_first_line_and_a_last_line_header(self):
        """L1-PTS-090 特征化：每个动作（含未知动作）第一行 ≤ 110 且不含 @，末行是 header，header 只出现一次。"""
        for action in [*self.mod.ACTION_LABELS, "brand_new_action"]:
            with self.subTest(action):
                lines = self.render(make_todo(5, action=action)).split("\n")
                self.assertLessEqual(len(lines[0]), 110)
                self.assertTrue(lines[-1].startswith("[gitlab-todo:v1]"))
                self.assertEqual("\n".join(lines).count("[gitlab-todo:v1]"), 1)


    def test_the_done_hint_offers_the_first_configured_emoji_and_the_reply_in_one_line(self):
        """L1-PTS-092 完成指引是单行「完成后点 {done_emojis[0]}[（表情里搜 check）] 或在本 Thread 回复 todo:done:<id>」：缺省 ✅（带搜索提示）；配置多个时取第一个；仍是 5 行、恰好一行含 todo:done:<id>、紧挨 header；带摘要时仍夹在摘要与 header 之间；不可信标题里的表情不会进这一行。"""
        for emojis, first, hint in ((None, "✅", True), (["✅"], "✅", True), (["✔️", "✅"], "✔️", False),
                                    (["✅", "✔️"], "✅", True), (["🎉"], "🎉", False)):
            with self.subTest(done_emojis=emojis):
                config = make_config() if emojis is None else make_config(todo={"done_emojis": emojis})
                lead = f"完成后点 {first}（表情里搜 check）" if hint else f"完成后点 {first} "
                todo = self.assigned(title="补齐个人待办的说明 👍", body="补齐个人待办的说明 👍")
                lines = self.render(todo, config).split("\n")
                self.assertEqual(lines[2:], [f"{lead}或在本 Thread 回复 todo:done:53663", header_for(53663, "assigned", "issue")])
                self.assertEqual(len(lines), 4)
                self.assertEqual([ln for ln in lines if "todo:done:53663" in ln], [lines[-2]])
                excerpt = self.render(self.mentioned(), config).split("\n")
                self.assertEqual(excerpt[-2], f"{lead}或在本 Thread 回复 todo:done:53664")
                self.assertTrue(excerpt[-3].startswith("> "))

    def test_the_search_hint_appears_exactly_when_the_first_done_emoji_is_the_check_mark(self):
        """L1-PTS-092C 只有 done_emojis[0]（去掉 U+FE0F 后）等于 ✅ 才写「（表情里搜 check）」：缺省、["✅"]、["✅"] 加 U+FE0F（带 FE0F）、["✅", "✔️"] 都带；["✔️", "✅"]（✅ 不是第一个）、["👍"]、["✔️"]、["✔"] 不带，且这些消息与提示前的版式逐字不变（「完成后点 {E} 或在本 Thread 回复 todo:done:<id>」）；提示只出现一次、仍是单行、位置在摘要之后 header 之前。"""
        cases = {
            "default": (None, "完成后点 ✅（表情里搜 check）或在本 Thread 回复 todo:done:53663"),
            "check": (["✅"], "完成后点 ✅（表情里搜 check）或在本 Thread 回复 todo:done:53663"),
            "check + FE0F": (["✅\ufe0f"], "完成后点 ✅\ufe0f（表情里搜 check）或在本 Thread 回复 todo:done:53663"),
            "check first, more": (["✅", "✔️"], "完成后点 ✅（表情里搜 check）或在本 Thread 回复 todo:done:53663"),
            "check is not first": (["✔️", "✅"], "完成后点 ✔️ 或在本 Thread 回复 todo:done:53663"),
            "thumbs up": (["👍"], "完成后点 👍 或在本 Thread 回复 todo:done:53663"),
            "heavy check mark": (["✔️"], "完成后点 ✔️ 或在本 Thread 回复 todo:done:53663"),
            "bare heavy check mark": (["✔"], "完成后点 ✔ 或在本 Thread 回复 todo:done:53663"),
            "check-box": (["☑️"], "完成后点 ☑️ 或在本 Thread 回复 todo:done:53663"),
        }
        for name, (emojis, expected) in cases.items():
            with self.subTest(name):
                config = make_config() if emojis is None else make_config(todo={"done_emojis": emojis})
                lines = self.render(self.assigned(), config).split("\n")
                self.assertEqual(lines[-2], expected)
                self.assertEqual(len(lines), 4)
                self.assertEqual([ln for ln in lines if "todo:done:53663" in ln], [expected])
                self.assertEqual(sum(ln.count("check") for ln in lines), 1 if "check" in expected else 0)
                self.assertEqual("\n".join(lines).count("表情里搜"), 1 if "check" in expected else 0)
                excerpt = self.render(self.mentioned(), config).split("\n")
                self.assertEqual(excerpt[-2], expected.replace("53663", "53664"))
                self.assertTrue(excerpt[-3].startswith("> "))
                self.assertEqual(excerpt[:-2], self.render(self.mentioned(), make_config()).split("\n")[:-2])
                self.assertTrue(excerpt[-1].startswith("[gitlab-todo:v1]"))

    def test_a_delivered_message_carries_the_configured_emoji_in_its_hint(self):
        """L1-PTS-092B 走完整投递（run）：发出去的消息里完成指引用配置的第一个表情，而不是写死的 ✅。"""
        buzz = FakeBuzz()
        self.run_sync(FakeGitLab([make_todo(9)]), buzz, make_config(todo={"done_emojis": ["✔️", "✅"]}))
        self.assertEqual(buzz.sent[0]["content"].split("\n")[-2], "完成后点 ✔️ 或在本 Thread 回复 todo:done:9")  # ✅ is not first: no search hint

    def test_a_delivered_message_with_the_default_done_emoji_carries_the_search_hint(self):
        """L1-PTS-092D 走完整投递（run）：不配 done_emojis（缺省 ✅）时发出去的消息带「（表情里搜 check）」，且仍只有一行含 todo:done:<id>。"""
        buzz = FakeBuzz()
        self.run_sync(FakeGitLab([make_todo(9)]), buzz)
        lines = buzz.sent[0]["content"].split("\n")
        self.assertEqual(lines[-2], "完成后点 ✅（表情里搜 check）或在本 Thread 回复 todo:done:9")
        self.assertEqual([ln for ln in lines if "todo:done:9" in ln], [lines[-2]])


    def todo_by(self, author, title="补齐个人待办的说明", **over):
        """A real-shaped assigned todo whose author's username is `author` (a str, or any other JSON value)."""

        todo = self.assigned(title=title, **over)
        todo["author"] = {"username": author, "name": "x"}
        return todo

    def test_the_author_is_the_tail_of_line_one_and_there_is_no_project_line_behind_a_link(self):
        """L1-PTS-096 发起人并入第 1 行末尾「 · {username}」；有合格链接时消息里没有单独的项目路径行，项目路径只以链接的一部分出现一次（不重复）。"""
        for todo, first, link in (
                (self.assigned(), "指派给你 · #67 补齐个人待办的说明 · alice", self.ISSUE),
                (self.mentioned(), "提到了你 · !45 升级依赖 · bob", f"{self.MR}#note_623176"),
                (self.build_failed(), "流水线失败 · !45 升级依赖 · ci", f"{self.MR}/pipelines")):
            with self.subTest(first):
                content = self.render(todo)
                lines = content.split("\n")
                self.assertEqual((lines[0], lines[1]), (first, link))
                self.assertEqual(content.count("infra/buzz-deploy"), 1)  # inside the link, nowhere else
                self.assertNotIn("infra/buzz-deploy", lines[0])
                self.assertFalse([ln for ln in lines if ln == "infra/buzz-deploy" or ln.startswith("infra/buzz-deploy ")])
        buzz = FakeBuzz()
        self.run_sync(FakeGitLab([make_todo(9)]), buzz)  # the same through the delivery path
        content = buzz.sent[0]["content"]
        self.assertEqual(content.count("grp/proj"), 1)
        self.assertEqual(content.split("\n")[0], "请你评审 · !45 修复登录超时 · alice")
        self.assertEqual(len(content.split("\n")), 5)  # title, link, the excerpt line "> 请看下 diff", hint, header

    def test_line_one_stays_within_110_and_the_author_suffix_is_never_the_part_that_gets_cut(self):
        """L1-PTS-096B 第 1 行总长 ≤ 110（LINE1_MAX）：先截标题，「 · 发起人」后缀总是完整；后缀里的用户名最多 32 个字符（`_ident` 的结果再截）；标题装得下时原样保留；300 字标题 + 超长作者名、超长 iid、超长 target_type 都成立；无 ASCII @。"""
        self.assertEqual(self.mod.LINE1_MAX, 110)
        for title_len in (0, 1, 40, 60, 75, 76, 100, 300):
            for author in ("a", "alice", "b" * 31, "c" * 32, "d" * 33, "e" * 100, "f" * 500):
                with self.subTest(title_len=title_len, author_len=len(author)):
                    first = self.render(self.todo_by(author, title="标" * title_len)).split("\n")[0]
                    suffix = " · " + author[:32]
                    self.assertTrue(first.endswith(suffix), first)
                    self.assertLessEqual(len(first), 110)
                    self.assertNotIn("@", first)
                    base = "指派给你 · #67" + (" " + "标" * title_len if title_len else "")
                    if len(base) + len(suffix) <= 110:
                        self.assertEqual(first, base + suffix)  # it fits: nothing is cut
                    else:
                        self.assertEqual(len(first), 110)  # it does not: the title gives way, to the character
        long_title = self.render(self.todo_by("g" * 100, title="标" * 300)).split("\n")[0]
        self.assertEqual(long_title, "指派给你 · #67 " + "标" * (110 - len("指派给你 · #67 ") - 35) + " · " + "g" * 32)
        huge_iid = self.assigned(title="标" * 300)
        huge_iid["target"]["iid"] = "9" * 500
        huge_type = self.assigned(title="标" * 300)
        huge_type["target_type"], huge_type["target"]["iid"] = "X" * 300, None
        for todo in (huge_iid, huge_type):
            first = self.render(todo).split("\n")[0]
            self.assertTrue(first.endswith(" · alice"), first)
            self.assertLessEqual(len(first), 110)

    def test_the_author_suffix_is_left_out_when_there_is_no_usable_author(self):
        """L1-PTS-097 作者缺失、不是 dict、username 缺失/None/空串、或清洗后为空（`_ident` 返回 `?`，例如全是 @ # 这类字符）→ 第 1 行没有「 · 」后缀，也没有 `?`；作者字符集之外的字符先被 `_ident` 去掉（`al!ice` → `alice`，`@evil` → `evil`）。"""
        for name, author in (("empty", ""), ("none", None), ("only filtered characters", "@#!$%"),
                             ("only a question mark", "?"), ("only spaces", "   ")):
            with self.subTest(name):
                first = self.render(self.todo_by(author, title="补齐个人待办的说明")).split("\n")[0]
                self.assertEqual(first, "指派给你 · #67 补齐个人待办的说明")
                self.assertNotIn("?", first)
        for name, mutate in (("no author key", lambda t: t.pop("author")), ("author is None", lambda t: t.update(author=None)),
                             ("author is a string", lambda t: t.update(author="alice")),
                             ("no username key", lambda t: t.update(author={"name": "Alice"}))):
            with self.subTest(name):
                todo = self.assigned()
                mutate(todo)
                first = self.render(todo).split("\n")[0]
                self.assertEqual(first, "指派给你 · #67 补齐个人待办的说明")
        long_only = self.render(self.todo_by("", title="标" * 300)).split("\n")[0]
        self.assertEqual(len(long_only), 110)  # no suffix: the whole line is the title's, cut at LINE1_MAX as before
        self.assertFalse(long_only.endswith(" ·"))
        for raw, shown in (("al!ice", "alice"), ("@evil", "evil"), ("a b", "ab")):
            with self.subTest(raw):
                self.assertEqual(self.render(self.todo_by(raw)).split("\n")[0], f"指派给你 · #67 补齐个人待办的说明 · {shown}")

    def test_without_a_qualified_link_the_project_path_takes_the_link_lines_place(self):
        """L1-PTS-098 target_url 与 web_url 都不合格（`_link` 返回 None）时，第 1 行之后补一行只含项目路径（`_ident`：去掉字符集外的字符）；摘要仍在它之后、完成指引与 header 之前；4 行仍是 4 行；这条消息里没有 `://`，项目路径只出现一次；有合格链接时不出现这一行。"""
        for name, todo, rest in (
                ("assigned", self.assigned(target_url="https://evil.example/x", web_url="https://evil.example/y"), []),
                ("no url at all", self.assigned(target_url=None, web_url=None), []),
                ("mentioned", self.mentioned(target_url="http://gitlab.example/x", web_url="https://evil.example/y"),
                 ["> ＠jchen 这个 diff 你看下", "> 我怀疑 lockfile 没同步"])):
            with self.subTest(name):
                self.assertIsNone(self.mod._link(todo, make_config()))
                content = self.render(todo)
                lines = content.split("\n")
                self.assertEqual(lines[1], "infra/buzz-deploy")
                self.assertEqual(lines[2:-2], rest)
                self.assertEqual(lines[-2], f"完成后点 ✅（表情里搜 check）或在本 Thread 回复 todo:done:{todo['id']}")
                self.assertEqual(len(lines), 4 + len(rest))
                self.assertNotIn("://", content)
                self.assertEqual(content.count("infra/buzz-deploy"), 1)
        odd = self.assigned(target_url=None, web_url=None)
        odd["project"]["path_with_namespace"] = "grp/pro[j]x@y z:w"
        self.assertEqual(self.render(odd).split("\n")[1], "grp/projxyzw")  # `_ident`, so nothing here can form structure
        with_link = self.render(self.assigned()).split("\n")
        self.assertNotIn("infra/buzz-deploy", with_link[0] + with_link[2] + with_link[3])

    def test_real_shaped_messages_render_in_full(self):
        """L1-PTS-099 三条真实形状的消息整条逐字比对：assigned（4 行）、带 #note_ 锚点与摘要的 mentioned（5 行）、指向 /pipelines 的 build_failed（4 行）；再加一条没有合格链接的 assigned（项目路径行取代链接行，仍 4 行）。"""
        done = "完成后点 ✅（表情里搜 check）或在本 Thread 回复 todo:done:"
        self.assertEqual(self.render(self.assigned()), "\n".join([
            "指派给你 · #67 补齐个人待办的说明 · alice", self.ISSUE, f"{done}53663",
            header_for(53663, "assigned", "issue")]))
        self.assertEqual(self.render(self.mentioned()), "\n".join([
            "提到了你 · !45 升级依赖 · bob", f"{self.MR}#note_623176", "> ＠jchen 这个 diff 你看下", "> 我怀疑 lockfile 没同步",
            f"{done}53664", header_for(53664, "mentioned", "merge_request")]))
        self.assertEqual(self.render(self.build_failed()), "\n".join([
            "流水线失败 · !45 升级依赖 · ci", f"{self.MR}/pipelines", f"{done}53665",
            header_for(53665, "build_failed", "merge_request")]))
        self.assertEqual(self.render(self.assigned(target_url=None, web_url="https://evil.example/y")), "\n".join([
            "指派给你 · #67 补齐个人待办的说明 · alice", "infra/buzz-deploy", f"{done}53663",
            header_for(53663, "assigned", "issue")]))


class RejectedSendTest(Harness):
    def test_rejected_reply_is_retried_once_as_a_top_level_message(self):
        """L1-PTS-03D 回复被拒（Thread 根不存在等）：清掉该目标的 Thread 记录，改成顶层重试一次，后续待办回新根。"""
        buzz = FakeBuzz()
        buzz.reject_when = lambda content, reply_to: reply_to is not None
        gitlab = FakeGitLab([make_todo(1, action="assigned")])
        self.run_sync(gitlab, buzz)
        (root,) = buzz.sent
        gitlab.todos = [make_todo(1, action="assigned"), make_todo(2, action="mentioned")]
        result = self.run_sync(gitlab, buzz)
        second = buzz.sent[1]
        self.assertIsNone(second["reply_to"])
        self.assertEqual((result["delivered"], result["rejected"]), (1, 0))
        state = self.state_json()
        self.assertEqual(state["todos"]["2"]["state"], "ACKED")
        self.assertIsNone(state["todos"]["2"]["reply_to"])
        self.assertEqual(state["threads"]["1175:MergeRequest:45"], second["id"])
        self.assertNotEqual(second["id"], root["id"])
        self.assertEqual(buzz.send_calls, 3)  # root, the rejected reply, the top-level retry

    def test_a_poison_todo_is_rejected_only_after_a_later_send_succeeds(self):
        """L1-PTS-03E 逐条毒药：顶层仍被拒的那条，先只删掉 PENDING（不落 REJECTED）；同轮后面有一次发送成功，才置 REJECTED（终态、记 rejected_at、计入 rejected），后面照常投递，下一轮不再重试。"""
        buzz = FakeBuzz()
        buzz.reject_when = lambda content, reply_to: "[id:1]" in content
        seen = []
        original = buzz.send

        def spy(content, reply_to=None, mentions=()):
            seen.append((content.rsplit("[id:", 1)[1][0], dict(self.state_json()["todos"])))
            return original(content, reply_to, mentions)

        buzz.send = spy
        gitlab = FakeGitLab([make_todo(1, iid=1), make_todo(2, iid=2), make_todo(3, iid=3)])
        result = self.run_sync(gitlab, buzz)
        self.assertEqual((result["delivered"], result["rejected"]), (2, 1))
        self.assertEqual([m["content"].split("[id:")[1][0] for m in buzz.sent], ["2", "3"])
        on_disk_when_2_was_sent = dict(seen)["2"]
        self.assertNotIn("1", on_disk_when_2_was_sent)  # neither PENDING nor REJECTED yet: nothing proves the refusal is per-message
        state = self.state_json()["todos"]
        self.assertEqual((state["1"]["state"], state["2"]["state"], state["3"]["state"]),
                         ("REJECTED", "ACKED", "ACKED"))
        self.assertEqual(state["1"]["rejected_at"], NOW)
        calls = buzz.send_calls
        result = self.run_sync(gitlab, buzz)
        self.assertEqual((buzz.send_calls, result["delivered"], result["rejected"]), (calls, 0, 0))
        self.assertEqual(self.state_json()["todos"]["1"]["state"], "REJECTED")

    def test_reply_and_retry_both_rejected_ends_in_rejected_once_a_later_send_succeeds(self):
        """L1-PTS-03F 回复被拒、顶层重试也被拒、后面有发送成功：REJECTED，且不遗留指向死根的 Thread 记录。"""
        gitlab, buzz = FakeGitLab([make_todo(1, action="assigned")]), FakeBuzz()
        self.run_sync(gitlab, buzz)
        buzz.reject_when = lambda content, reply_to: "[id:2]" in content
        gitlab.todos += [make_todo(2, action="mentioned"), make_todo(3, iid=99)]
        result = self.run_sync(gitlab, buzz)
        self.assertEqual((result["delivered"], result["rejected"]), (1, 1))
        state = self.state_json()
        self.assertEqual(state["todos"]["2"]["state"], "REJECTED")
        self.assertEqual(state["todos"]["3"]["state"], "ACKED")
        self.assertNotIn("1175:MergeRequest:45", state["threads"])

    def test_systemic_rejection_fails_the_round_loudly_and_loses_nothing(self):
        """L1-PTS-03H 系统性拒收（每次发送都被拒）：连续 2 条被拒就整轮 SyncError（rc 1），不再发第 3 条；state 里没有 REJECTED、被拒的也没留 PENDING；撤销拒收条件后下一轮全部投递。"""
        buzz = FakeBuzz()
        buzz.reject_when = lambda content, reply_to: True
        gitlab = FakeGitLab([make_todo(n, iid=n) for n in (1, 2, 3)])
        with self.assertRaises(sync.SyncError) as ctx:
            self.run_sync(gitlab, buzz)
        self.assertIn("2 consecutive", str(ctx.exception))
        self.assertEqual(buzz.send_calls, 2)
        self.assertEqual(self.state_json()["todos"], {})
        buzz.reject_when = None
        result = self.run_sync(gitlab, buzz)
        self.assertEqual((result["delivered"], result["rejected"]), (3, 0))
        self.assertEqual({r["state"] for r in self.state_json()["todos"].values()}, {"ACKED"})

    def test_a_poison_todo_last_in_the_round_fails_it_because_nothing_proves_it_is_per_message(self):
        """L1-PTS-03I 被拒的是本轮最后一条：没有后续成功可证明拒收是逐条的，整轮失败，该条保持已删除（下一轮重试）；前面成功的照常落盘。"""
        buzz = FakeBuzz()
        buzz.reject_when = lambda content, reply_to: "[id:2]" in content
        gitlab = FakeGitLab([make_todo(1, iid=1), make_todo(2, iid=2)])
        with self.assertRaises(sync.SyncError) as ctx:
            self.run_sync(gitlab, buzz)
        self.assertIn("rejected", str(ctx.exception))
        todos = self.state_json()["todos"]
        self.assertEqual({k: v["state"] for k, v in todos.items()}, {"1": "ACKED"})
        buzz.reject_when = None
        result = self.run_sync(gitlab, buzz)
        self.assertEqual((result["delivered"], result["rejected"]), (1, 0))
        self.assertEqual(self.state_json()["todos"]["2"]["state"], "ACKED")

    def test_a_success_between_two_rejections_resets_the_consecutive_count(self):
        """L1-PTS-03J 拒、成、拒、成：两次拒收不相邻，不触发「连续 2 条」，两条都在各自的后续成功之后置 REJECTED；成功发送之后被拒的那一条不会被先前的成功「顺带」放过。"""
        buzz = FakeBuzz()
        buzz.reject_when = lambda content, reply_to: any(f"[id:{n}]" in content for n in (1, 3))
        gitlab = FakeGitLab([make_todo(n, iid=n) for n in (1, 2, 3, 4)])
        result = self.run_sync(gitlab, buzz)
        self.assertEqual((result["delivered"], result["rejected"]), (2, 2))
        states = {k: v["state"] for k, v in self.state_json()["todos"].items()}
        self.assertEqual(states, {"1": "REJECTED", "2": "ACKED", "3": "REJECTED", "4": "ACKED"})

    def test_two_rejections_in_a_row_fail_even_if_a_later_send_would_succeed(self):
        """L1-PTS-03K 拒、拒、成：第 2 条被拒时就停（阈值是连续 2 条），不去碰第 3 条；两条被拒的都不落 REJECTED。"""
        buzz = FakeBuzz()
        buzz.reject_when = lambda content, reply_to: "[id:3]" not in content
        gitlab = FakeGitLab([make_todo(n, iid=n) for n in (1, 2, 3)])
        with self.assertRaises(sync.SyncError):
            self.run_sync(gitlab, buzz)
        self.assertEqual(buzz.send_calls, 2)
        self.assertEqual(self.state_json()["todos"], {})

    def test_unknown_send_outcome_still_fails_the_round_and_keeps_pending(self):
        """L1-PTS-03G 只有「确定没发出去」才隔离；结果未知（普通 SyncError）仍整轮失败关闭、保留 PENDING。"""
        gitlab, buzz = FakeGitLab([make_todo(1, iid=1), make_todo(2, iid=2)]), FakeBuzz()
        buzz.send_error = sync.SyncError("Buzz send outcome is unknown: no event id")
        with self.assertRaises(sync.SyncError):
            self.run_sync(gitlab, buzz)
        self.assertEqual(self.state_json()["todos"]["1"]["state"], "PENDING")
        self.assertEqual(buzz.send_calls, 1)


class ReconcileTest(Harness):
    def test_pending_is_reconciled_from_channel_before_resending(self):
        """L1-PTS-037 崩在「发出但未落 ACKED」之后：下一轮先在频道里找到它，不重复发送。"""
        gitlab, buzz = FakeGitLab([make_todo(1)]), FakeBuzz()
        original_send = self.crash_after_send(buzz)
        with self.assertRaises(sync.SyncError):
            self.run_sync(gitlab, buzz)
        buzz.send = original_send
        self.run_sync(gitlab, buzz)
        self.assertEqual(len(buzz.sent), 1)
        self.assertEqual(self.state_json()["todos"]["1"]["state"], "ACKED")

    def test_crash_before_the_send_resends_exactly_once(self):
        """L1-PTS-037B 发送前就崩（频道里没有这条）：下一轮丢弃 PENDING 并重发一次，不丢、不重。"""
        gitlab, buzz = FakeGitLab([make_todo(1)]), FakeBuzz()
        buzz.send_error = sync.SyncError("relay down")
        with self.assertRaises(sync.SyncError):
            self.run_sync(gitlab, buzz)
        self.assertEqual(buzz.sent, [])
        buzz.send_error = None
        result = self.run_sync(gitlab, buzz)
        self.assertEqual((len(buzz.sent), result["delivered"]), (1, 1))
        self.assertEqual(self.state_json()["todos"]["1"]["state"], "ACKED")

    def test_a_forged_header_from_a_non_publisher_is_not_taken_as_delivered(self):
        """L1-PTS-037C 频道里非发布者伪造出同样末行 header 的事件，不能让对账把 PENDING 当成已发出。"""
        gitlab, buzz = FakeGitLab([make_todo(1)]), FakeBuzz()
        buzz.send_error = sync.SyncError("relay down")
        with self.assertRaises(sync.SyncError):
            self.run_sync(gitlab, buzz)
        buzz.send_error = None
        for forger in (STRANGER, OWNER, ASSISTANT):
            buzz.events.append({"id": f"{0xabc0 + len(buzz.events):064x}", "pubkey": forger, "kind": 9,
                                "content": f"伪造\n{header_for(1)}", "tags": [["h", CHANNEL]],
                                "created_at": NOW})
        self.run_sync(gitlab, buzz)
        self.assertEqual(len(buzz.sent), 1)
        self.assertEqual(self.state_json()["todos"]["1"]["event_id"], buzz.sent[0]["id"])

    def test_reconciled_root_becomes_the_thread_for_later_todos(self):
        """L1-PTS-037D 对账恢复出来的根事件记进 threads：同目标的后续待办回复进它，不另开顶层。"""
        gitlab, buzz = FakeGitLab([make_todo(1, action="assigned")]), FakeBuzz()
        original_send = self.crash_after_send(buzz)
        with self.assertRaises(sync.SyncError):
            self.run_sync(gitlab, buzz)
        buzz.send = original_send
        gitlab.todos.append(make_todo(2, action="mentioned"))
        self.run_sync(gitlab, buzz)
        root = buzz.sent[0]
        self.assertEqual(self.state_json()["threads"]["1175:MergeRequest:45"], root["id"])
        self.assertEqual(buzz.sent[1]["reply_to"], root["id"])
        self.assertEqual(len(buzz.sent), 2)


    def test_a_pending_record_is_reconciled_not_resolved_when_gitlab_no_longer_lists_it(self):
        """L1-PTS-037E 崩在发出之后、且这条 todo 随后在 GitLab 里被处理掉：PENDING 仍先对账成 ACKED 并记下 Thread 根；没发出去的则直接丢弃，都不发新消息。"""
        gitlab, buzz = FakeGitLab([make_todo(1, action="assigned")]), FakeBuzz()
        original = self.crash_after_send(buzz)
        with self.assertRaises(sync.SyncError):
            self.run_sync(gitlab, buzz)
        buzz.send = original
        gitlab.todos = []
        self.run_sync(gitlab, buzz)
        state = self.state_json()
        self.assertEqual(state["todos"]["1"]["state"], "ACKED")
        self.assertEqual(state["threads"]["1175:MergeRequest:45"], buzz.sent[0]["id"])
        other, lost = FakeGitLab([make_todo(2, iid=46)]), FakeBuzz()
        lost.send_error = sync.SyncError("relay down")
        with self.assertRaises(sync.SyncError):
            self.run_sync(other, lost)
        lost.send_error, other.todos = None, []
        self.run_sync(other, lost)
        self.assertNotIn("2", self.state_json()["todos"])
        self.assertEqual(lost.sent, [])


class StateFileTest(Harness):
    def test_corrupt_or_misshapen_state_fails_closed_without_resetting(self):
        """L1-PTS-046 todo-state.json 损坏或形状错误：整轮失败关闭，文件原样保留（不静默重置成空状态而重发全部待办）。"""
        good = make_record(1)
        cases = {
            "not json": "{nope",
            "truncated json": '{"version": 1, "todos": {',
            "not an object": "[]",
            "wrong version": json.dumps({"version": 2, "todos": {}, "threads": {}}),
            "todos not an object": json.dumps({"version": 1, "todos": [], "threads": {}}),
            "threads not an object": json.dumps({"version": 1, "todos": {}, "threads": []}),
            "record not an object": json.dumps({"version": 1, "todos": {"1": "x"}, "threads": {}}),
            "unknown record state": json.dumps({"version": 1, "todos": {"1": {**good, "state": "WEIRD"}}, "threads": {}}),
            "record without header": json.dumps({"version": 1, "todos": {"1": {k: v for k, v in good.items() if k != "header"}},
                                                 "threads": {}}),
            "acked record without an event id": json.dumps({"version": 1, "todos": {"1": {k: v for k, v in good.items() if k != "event_id"}},
                                                            "threads": {}}),
            "record with a non-int delivered_at": json.dumps({"version": 1, "todos": {"1": {**good, "delivered_at": "x"}},
                                                              "threads": {}}),
            "non-string thread root": json.dumps({"version": 1, "todos": {}, "threads": {"k": 5}}),
        }
        for label, raw in cases.items():
            self.write_state(raw=raw)
            gitlab, buzz = FakeGitLab([make_todo(1)]), FakeBuzz()
            with self.subTest(label), self.assertRaises(sync.SyncError):
                self.run_sync(gitlab, buzz)
            self.assertEqual((self.state / "todo-state.json").read_text(), raw, label)
            self.assertEqual((gitlab.fetches, buzz.sent), (0, []), label)

    def test_a_record_without_the_reply_to_key_is_refused_in_every_state(self):
        """L1-PTS-046C reply_to 键必须存在（值可以是 null）：缺它的记录在任何状态下都拒绝加载，文件原样保留（否则对账时 KeyError 逃出 SyncError 路径）。"""
        for state in ("PENDING", "ACKED", "DONE", "RESOLVED", "REJECTED"):
            record = make_record(1, state)
            del record["reply_to"]
            raw = json.dumps({"version": 1, "todos": {"1": record}, "threads": {}})
            self.write_state(raw=raw)
            gitlab, buzz = FakeGitLab([make_todo(1)]), FakeBuzz()
            with self.subTest(state):
                with self.assertRaises(sync.SyncError):
                    self.run_sync(gitlab, buzz)
                self.assertEqual((self.state / "todo-state.json").read_text(), raw)
                self.assertEqual((gitlab.fetches, buzz.sent), (0, []))

    def test_state_written_by_the_previous_script_still_loads(self):
        """L1-PTS-046D 旧脚本写出的合法 state（每条记录都带 reply_to，null 或字符串）仍能加载，五种状态都在。"""
        old = {
            "version": 1,
            "threads": {"1175:MergeRequest:45": "f" * 64},
            "todos": {
                "1": {"state": "PENDING", "target": "1175:MergeRequest:45", "header": header_for(1),
                      "reply_to": None, "created": NOW, "delivered_at": NOW},
                "2": {"state": "ACKED", "target": "1175:MergeRequest:45", "header": header_for(2),
                      "reply_to": "f" * 64, "created": NOW, "delivered_at": NOW, "event_id": "2" * 64},
                "3": {"state": "DONE", "target": "1175:MergeRequest:45", "header": header_for(3),
                      "reply_to": None, "created": NOW, "delivered_at": NOW, "event_id": "3" * 64, "done_at": NOW},
                "4": {"state": "RESOLVED", "target": "1175:MergeRequest:45", "header": header_for(4),
                      "reply_to": "f" * 64, "created": NOW, "delivered_at": NOW, "event_id": "4" * 64,
                      "resolved_at": NOW},
                "5": {"state": "REJECTED", "target": "1175:MergeRequest:45", "header": header_for(5),
                      "reply_to": None, "created": NOW, "delivered_at": NOW, "rejected_at": NOW},
            },
        }
        self.write_state(raw=json.dumps(old))
        loaded = self.mod.load_state(self.state)
        self.assertEqual({k: v["state"] for k, v in loaded["todos"].items()},
                         {"1": "PENDING", "2": "ACKED", "3": "DONE", "4": "RESOLVED", "5": "REJECTED"})
        self.assertEqual(self.run_sync(FakeGitLab([]), FakeBuzz())["status"], "ok")

    def test_state_that_is_not_utf8_fails_closed_as_a_sync_error(self):
        """L1-PTS-046E state 文件含非 UTF-8 字节：SyncError（不是 UnicodeDecodeError 逃出去），文件原样保留，不取 todo。"""
        raw = b'{"version": 1, "todos": {}, "threads": {"k": "\xff\xfe"}}'
        self.write_state_bytes(raw)
        gitlab, buzz = FakeGitLab([make_todo(1)]), FakeBuzz()
        with self.assertRaises(sync.SyncError) as ctx:
            self.run_sync(gitlab, buzz)
        self.assertNotIn("\\xff", str(ctx.exception))
        self.assertEqual((self.state / "todo-state.json").read_bytes(), raw)
        self.assertEqual((gitlab.fetches, buzz.sent), (0, []))

    def test_load_state_and_load_config_raise_sync_errors_for_every_unreadable_input(self):
        """L1-PTS-046F load_state / load_config 自己就把非 UTF-8、深度嵌套（RecursionError）、读不了（路径是目录）转成 SyncError，不靠入口再兜一层。"""
        self.state.mkdir(mode=0o700, parents=True)
        path = self.state / "todo-state.json"
        for label, prepare in (
            ("non-UTF-8", lambda: path.write_bytes(b'{"version": 1, "x": "\xff"}')),
            ("nested", lambda: path.write_text("[" * 200000)),
        ):
            prepare()
            with self.subTest(f"state {label}"), self.assertRaises(sync.SyncError):
                self.mod.load_state(self.state)
            path.unlink()
        path.mkdir()  # exists() is true, reading it is an OSError
        with self.subTest("state is a directory"), self.assertRaises(sync.SyncError):
            self.mod.load_state(self.state)
        cfg = Path(self.tmp.name) / "cfg.json"
        for label, prepare in (
            ("non-UTF-8", lambda: cfg.write_bytes(b'{"version": 1, "x": "\xff"}')),
            ("nested", lambda: cfg.write_text("[" * 200000)),
        ):
            prepare()
            cfg.chmod(0o600)
            with self.subTest(f"config {label}"), self.assertRaises(sync.SyncError):
                self.mod.load_config(cfg)

    def test_well_formed_state_of_every_state_name_loads(self):
        """L1-PTS-046B 五种状态名 PENDING/ACKED/DONE/RESOLVED/REJECTED 的合法记录都能加载。"""
        todos = {str(n): make_record(n, state) for n, state in enumerate(
            ("PENDING", "ACKED", "DONE", "RESOLVED", "REJECTED"), start=1)}
        self.write_state(todos, {"1175:MergeRequest:45": "f" * 64})
        result = self.run_sync(FakeGitLab([]), FakeBuzz())
        self.assertEqual(result["status"], "ok")


class MarkDoneTest(Harness):
    def delivered(self, **cfg):
        gitlab, buzz = FakeGitLab([make_todo(123)]), FakeBuzz()
        config = make_config(**cfg)
        self.run_sync(gitlab, buzz, config)
        return gitlab, buzz, config, buzz.sent[0]["id"]

    def test_trusted_done_marker_marks_the_todo_done_once(self):
        """L1-PTS-040 可信作者回复 todo:done:<id> → 只调一次 mark_as_done，状态转 DONE。"""
        gitlab, buzz, config, root = self.delivered()
        buzz.reply(ASSISTANT, "todo:done:123", root)
        buzz.reply(OWNER, "todo:done:123", root)
        for _ in range(2):
            self.run_sync(gitlab, buzz, config)
        self.assertEqual(gitlab.marked, [123])
        self.assertEqual(self.state_json()["todos"]["123"]["state"], "DONE")

    def test_marker_must_be_the_first_line_from_a_trusted_author(self):
        """L1-PTS-041 陌生人、发布者自己、非首行的标记一律忽略。"""
        gitlab, buzz, config, root = self.delivered()
        buzz.reply(STRANGER, "todo:done:123", root)
        buzz.reply(PUBLISHER, "todo:done:123", root)
        buzz.reply(OWNER, "我看了下\ntodo:done:123", root)
        buzz.reply(OWNER, "todo:done:123 谢谢", root)
        self.run_sync(gitlab, buzz, config)
        self.assertEqual(gitlab.marked, [])

    def test_a_valid_first_line_followed_by_prose_is_accepted(self):
        """L1-PTS-041B 首行是合法标记、后面接正文（助手常这么回）也算数。"""
        gitlab, buzz, config, root = self.delivered()
        buzz.reply(ASSISTANT, "todo:done:123\n已处理：MR 已 approve，无需再跟。", root)
        self.run_sync(gitlab, buzz, config)
        self.assertEqual(gitlab.marked, [123])

    def test_done_authors_of_only_the_assistant_ignores_the_owner(self):
        """L1-PTS-041C done_authors 只配助手时：owner 的标记被忽略，助手的被接受。"""
        gitlab, buzz, config, root = self.delivered(done_authors=[ASSISTANT])
        buzz.reply(OWNER, "todo:done:123", root)
        self.run_sync(gitlab, buzz, config)
        self.assertEqual(gitlab.marked, [])
        buzz.reply(ASSISTANT, "todo:done:123", root)
        self.run_sync(gitlab, buzz, config)
        self.assertEqual(gitlab.marked, [123])

    def test_only_todos_this_script_delivered_can_be_marked(self):
        """L1-PTS-042 标记范围被限定在本脚本投递过的 todo：别的 id 的标记不会触发写。"""
        gitlab, buzz, config, root = self.delivered()
        gitlab.todos.append(make_todo(555, iid=77, created="2026-08-01T00:00:00Z"))
        buzz.reply(OWNER, "todo:done:555", root)
        self.run_sync(gitlab, buzz, config)
        self.assertNotIn(555, gitlab.marked)

    def test_mark_done_disabled_never_writes(self):
        """L1-PTS-043 mark_done=false 时永不写 GitLab。"""
        gitlab, buzz, config, root = self.delivered(todo={"mark_done": False})
        buzz.reply(OWNER, "todo:done:123", root)
        self.run_sync(gitlab, buzz, config)
        self.assertEqual(gitlab.marked, [])

    def test_mark_done_failure_keeps_acked_and_retries(self):
        """L1-PTS-044 GitLab 写失败：本轮失败、状态仍 ACKED，下一轮重试。"""
        gitlab, buzz, config, root = self.delivered()
        buzz.reply(OWNER, "todo:done:123", root)
        gitlab.mark_error = sync.SyncError("HTTP 500")
        with self.assertRaises(sync.SyncError):
            self.run_sync(gitlab, buzz, config)
        self.assertEqual(self.state_json()["todos"]["123"]["state"], "ACKED")
        gitlab.mark_error = None
        self.run_sync(gitlab, buzz, config)
        self.assertEqual(gitlab.marked, [123])

    def test_mark_done_failure_does_not_stop_delivery_but_still_fails_the_round(self):
        """L1-PTS-044B mark_done 失败不拖停投递：新待办照发、状态落盘，随后本轮仍以原错误失败（下一轮重试标记）。"""
        gitlab, buzz, config, root = self.delivered()
        buzz.reply(OWNER, "todo:done:123", root)
        gitlab.mark_error = sync.SyncError("HTTP 500")
        gitlab.todos.append(make_todo(124, iid=46))
        with self.assertRaises(sync.SyncError) as ctx:
            self.run_sync(gitlab, buzz, config)
        self.assertIn("HTTP 500", str(ctx.exception))
        self.assertEqual(len(buzz.sent), 2)
        todos = self.state_json()["todos"]
        self.assertEqual((todos["123"]["state"], todos["124"]["state"]), ("ACKED", "ACKED"))
        gitlab.mark_error = None
        self.run_sync(gitlab, buzz, config)
        self.assertEqual(gitlab.marked, [123])
        self.assertEqual(len(buzz.sent), 2)

    def two_delivered(self):
        gitlab, buzz = FakeGitLab([make_todo(1, iid=1), make_todo(2, iid=2), make_todo(3, iid=3)]), FakeBuzz()
        self.run_sync(gitlab, buzz)
        roots = {m["content"].split("[id:")[1].split("]")[0]: m["id"] for m in buzz.sent}
        return gitlab, buzz, roots

    def test_one_permanently_failing_mark_does_not_starve_the_markers_after_it(self):
        """L1-PTS-044D id 1 的 mark_as_done 一直 403：id 2 排在后面的标记仍被处理；本轮仍以该错误失败，1 仍是 ACKED，下一轮重试。"""
        gitlab, buzz, roots = self.two_delivered()
        gitlab.mark_errors[1] = sync.GitLabHTTPError("POST", "todos/1/mark_as_done", 403)
        buzz.reply(OWNER, "todo:done:1", roots["1"], at=NOW + 5)
        buzz.reply(OWNER, "todo:done:2", roots["2"], at=NOW + 6)
        with self.assertRaises(sync.SyncError) as ctx:
            self.run_sync(gitlab, buzz)
        self.assertIn("403", str(ctx.exception))
        self.assertEqual(gitlab.marked, [2])
        todos = self.state_json()["todos"]
        self.assertEqual((todos["1"]["state"], todos["2"]["state"]), ("ACKED", "DONE"))
        gitlab.mark_errors.clear()
        self.run_sync(gitlab, buzz)
        self.assertEqual(gitlab.marked, [2, 1])

    def test_the_last_mark_error_is_the_one_raised_and_a_failed_id_is_tried_once_per_round(self):
        """L1-PTS-044E 多条标记失败时抛最后一个错误；同一个失败 id 被两位作者各标一次，本轮也只打一次 GitLab。"""
        gitlab, buzz, roots = self.two_delivered()
        gitlab.mark_errors = {1: sync.SyncError("first HTTP 500"), 3: sync.SyncError("last HTTP 502")}
        buzz.reply(OWNER, "todo:done:1", roots["1"], at=NOW + 5)
        buzz.reply(ASSISTANT, "todo:done:1", roots["1"], at=NOW + 6)
        buzz.reply(OWNER, "todo:done:2", roots["2"], at=NOW + 7)
        buzz.reply(OWNER, "todo:done:3", roots["3"], at=NOW + 8)
        with self.assertRaises(sync.SyncError) as ctx:
            self.run_sync(gitlab, buzz)
        self.assertEqual(str(ctx.exception), "last HTTP 502")
        self.assertEqual(gitlab.mark_calls, [1, 2, 3])
        self.assertEqual(gitlab.marked, [2])

    def test_reconcile_progress_is_persisted_when_mark_done_fails(self):
        """L1-PTS-044C mark_done 失败的那一轮，对账已经把 PENDING 推进成 ACKED 并落盘。"""
        gitlab, buzz, config, root = self.delivered()
        gitlab.todos.append(make_todo(124, iid=46))
        original = self.crash_after_send(buzz)
        with self.assertRaises(sync.SyncError):
            self.run_sync(gitlab, buzz, config)
        self.assertEqual(self.state_json()["todos"]["124"]["state"], "PENDING")
        buzz.send = original
        buzz.reply(OWNER, "todo:done:123", root)
        gitlab.mark_error = sync.SyncError("HTTP 500")
        with self.assertRaises(sync.SyncError):
            self.run_sync(gitlab, buzz, config)
        self.assertEqual(self.state_json()["todos"]["124"]["state"], "ACKED")
        self.assertEqual(len(buzz.sent), 2)

    def test_no_channel_scan_when_nothing_is_awaiting_done(self):
        """L1-PTS-045 没有待确认完成的 todo 时不扫描频道消息。"""
        gitlab, buzz = FakeGitLab([]), FakeBuzz()
        self.run_sync(gitlab, buzz)
        self.assertEqual(buzz.scans, [])

    def test_a_marker_older_than_the_delivery_is_ignored(self):
        """L1-PTS-047 完成标记绑定投递时间：早于 delivered_at-300 秒的标记（提前伪造、或标在旧消息上）不算。"""
        gitlab, buzz = FakeGitLab([make_todo(123)]), FakeBuzz()
        self.run_sync(gitlab, buzz)  # 123 delivered at NOW and stays awaiting: it keeps the scan window open
        gitlab.todos += [make_todo(124, iid=46), make_todo(125, iid=47)]
        later = NOW + 1000
        self.run_sync(gitlab, buzz, clock=lambda: later)
        roots = {m["content"].split("[id:")[1].split("]")[0]: m["id"] for m in buzz.sent}
        buzz.reply(OWNER, "todo:done:124", roots["124"], at=later - 300)  # exactly on the lower bound
        buzz.reply(OWNER, "todo:done:125", roots["125"], at=later - 301)  # one second too old
        buzz.reply(OWNER, "todo:done:123", roots["123"], at=NOW + 10)  # fine for 123: delivered before it
        self.run_sync(gitlab, buzz, clock=lambda: later + 60)
        self.assertEqual(sorted(gitlab.marked), [123, 124])
        self.assertEqual(self.state_json()["todos"]["125"]["state"], "ACKED")

    def test_events_without_a_usable_created_at_are_ignored(self):
        """L1-PTS-048 created_at 缺失、非整数、布尔的事件不能确认完成，也不会让排序崩掉。"""
        gitlab, buzz, config, root = self.delivered()
        for created in (None, "1789819300", 1789819300.5, True):
            buzz.events.append({"id": f"{0xe000 + len(buzz.events):064x}", "pubkey": OWNER, "kind": 9,
                                "content": "todo:done:123", "created_at": created,
                                "tags": [["h", CHANNEL], ["e", root, "", "reply"]]})
        buzz.events.append({"id": f"{0xe100:064x}", "pubkey": OWNER, "kind": 9, "content": "todo:done:123",
                            "tags": [["h", CHANNEL], ["e", root, "", "reply"]]})
        self.run_sync(gitlab, buzz, config)
        self.assertEqual(gitlab.marked, [])
        self.assertEqual(self.state_json()["todos"]["123"]["state"], "ACKED")


class MarkDoneThreadBindingTest(Harness):
    """A done marker only counts as a reply into the Thread of the todo it names (P1 from the MR code review).

    The Thread of a delivered todo is {its own event id, the Thread root it was posted under}.  Only the ids in the
    marker's `e` tags are looked at, never their root/reply markers.
    """

    WORKFLOW_KEY = "9" * 64  # what a relay-signed Workflow message looks like: not a member, not a trusted author

    def delivered(self, *todos):
        """A fresh state directory each time, so a subTest loop can call this again."""

        shutil.rmtree(self.state, ignore_errors=True)
        todos = todos or (make_todo(123),)
        gitlab, buzz = FakeGitLab(list(todos)), FakeBuzz()
        self.run_sync(gitlab, buzz)
        return gitlab, buzz, {str(t["id"]): m["id"] for t, m in zip(todos, buzz.sent)}

    def two_threads(self):
        return self.delivered(make_todo(1, iid=1), make_todo(2, iid=2))

    def same_thread(self):
        gitlab, buzz, ids = self.delivered(make_todo(1, iid=45), make_todo(2, iid=45, action="mentioned"))
        self.assertEqual([m["reply_to"] for m in buzz.sent], [None, ids["1"]])  # 2 was posted under 1's Thread
        return gitlab, buzz, ids

    def assertStillPending(self, gitlab, tid="123"):
        self.assertEqual(gitlab.mark_calls, [])
        self.assertEqual(self.state_json()["todos"][tid]["state"], "ACKED")

    def test_a_marker_posted_elsewhere_in_the_channel_is_ignored_and_the_todo_stays_pending(self):
        """L1-PTS-049 顶层消息（没有 e tag）里的 todo:done:<id> 不算：不调 mark_as_done、仍 ACKED；之后真正回复该 Thread 的标记照常生效（忽略不是永久拉黑）。"""
        gitlab, buzz, ids = self.delivered()
        buzz.post(OWNER, "todo:done:123")
        buzz.post(ASSISTANT, "todo:done:123")
        self.run_sync(gitlab, buzz)
        self.assertStillPending(gitlab)
        buzz.reply(ASSISTANT, "todo:done:123", ids["123"])
        self.run_sync(gitlab, buzz)
        self.assertEqual(gitlab.marked, [123])
        self.assertEqual(self.state_json()["todos"]["123"]["state"], "DONE")

    def test_a_marker_replying_into_another_todos_thread_is_ignored(self):
        """L1-PTS-049B 回复的是别的待办（另一个目标）的 Thread：不算，两个方向都一样；被回复的那条待办自己也没被标。"""
        gitlab, buzz, ids = self.two_threads()
        buzz.reply(OWNER, "todo:done:1", ids["2"])
        buzz.reply(OWNER, "todo:done:2", ids["1"])
        self.run_sync(gitlab, buzz)
        self.assertEqual(gitlab.mark_calls, [])
        todos = self.state_json()["todos"]
        self.assertEqual((todos["1"]["state"], todos["2"]["state"]), ("ACKED", "ACKED"))

    def test_a_marker_replying_to_an_unrelated_message_is_ignored(self):
        """L1-PTS-049C 回复的是无关消息（Workflow 唤醒消息、本人随手一句、Channel 里根本不存在的事件）：不算。"""
        gitlab, buzz, ids = self.delivered()
        wake = buzz.post(self.WORKFLOW_KEY, f"@助手 有一条新的 GitLab 待办（消息 {ids['123']}）。")
        chatter = buzz.post(OWNER, "在忙，晚点看")
        buzz.reply(ASSISTANT, "todo:done:123", wake)
        buzz.reply(OWNER, "todo:done:123", chatter)
        buzz.reply(ASSISTANT, "todo:done:123", "0" * 64)  # an event the Channel scan does not even contain
        self.run_sync(gitlab, buzz)
        self.assertStillPending(gitlab)

    def test_a_marker_replying_to_the_delivered_todo_message_is_accepted(self):
        """L1-PTS-049D 【特征化，旧代码本来就绿】回复原待办消息（e = 该待办的 event_id，顶层待办）→ 接受，mark_as_done 一次。"""
        gitlab, buzz, ids = self.delivered()
        buzz.reply(ASSISTANT, "todo:done:123", ids["123"])
        self.run_sync(gitlab, buzz)
        self.assertEqual(gitlab.marked, [123])

    def test_replying_to_the_thread_root_of_a_shared_thread_is_accepted_but_not_to_a_sibling_todo(self):
        """L1-PTS-049E 待办 2 归并进待办 1 的 Thread：回复 Thread 根（2 的 reply_to）→ 接受【这一半旧代码本来就绿】；回复的是 Thread 里另一条待办（既不是 1 的 event_id，1 是顶层、没有别的根）→ 对 1 不接受。"""
        gitlab, buzz, ids = self.same_thread()
        buzz.reply(OWNER, "todo:done:2", ids["1"], at=NOW + 5)  # the Thread root
        self.run_sync(gitlab, buzz)
        self.assertEqual(gitlab.marked, [2])
        buzz.reply(OWNER, "todo:done:1", ids["2"], at=NOW + 6)  # the sibling todo's own message
        self.run_sync(gitlab, buzz)
        self.assertEqual(gitlab.marked, [2])
        self.assertEqual(self.state_json()["todos"]["1"]["state"], "ACKED")

    def test_replying_to_the_own_message_of_a_todo_in_a_shared_thread_is_accepted(self):
        """L1-PTS-049F 【特征化，旧代码本来就绿】待办 2 在共用 Thread 里，回复它自己那条消息（event_id，不是根）→ 接受：event_id 与 Thread 根是两个独立的入口。"""
        gitlab, buzz, ids = self.same_thread()
        buzz.reply(OWNER, "todo:done:2", buzz.sent[1]["id"])
        self.run_sync(gitlab, buzz)
        self.assertEqual(gitlab.marked, [2])

    def test_the_thread_recorded_in_state_is_what_counts_and_threads_are_not_shared_between_todos(self):
        """L1-PTS-049G state 里 reply_to=R、event_id=E 的 ACKED 记录：e=R 或 e=E 接受；别的 id 不接受；顶层待办（reply_to=None）也不能借用别人的根 R。判定按记录逐条做，不是「频道里任何一个已知 id 都行」。"""
        root, other = f"{0x99:064x}", f"{0x98:064x}"
        self.write_state({"1": make_record(1, reply_to=root), "2": make_record(2, reply_to=root),
                          "3": make_record(3, reply_to=None), "4": make_record(4, reply_to=None)})
        buzz = FakeBuzz()
        buzz.reply(OWNER, "todo:done:1", root)  # the Thread root
        buzz.reply(OWNER, "todo:done:2", f"{2:064x}")  # its own event id
        buzz.reply(OWNER, "todo:done:3", other)  # a stranger's id
        buzz.reply(OWNER, "todo:done:3", root)  # somebody else's Thread root
        buzz.reply(OWNER, "todo:done:4", f"{3:064x}")  # the message of another todo
        gitlab = FakeGitLab([make_todo(n, iid=n) for n in (1, 2, 3, 4)])
        self.run_sync(gitlab, buzz)
        self.assertEqual(sorted(gitlab.marked), [1, 2])
        todos = self.state_json()["todos"]
        self.assertEqual((todos["3"]["state"], todos["4"]["state"]), ("ACKED", "ACKED"))

    def test_a_top_level_todo_has_no_thread_root_that_a_missing_or_null_e_id_could_match(self):
        """L1-PTS-049H 顶层待办 reply_to=None：e tag 缺 id、id 为 null/空串/非字符串的标记都不能靠「None == None」或空值蒙混。"""
        gitlab, buzz, ids = self.delivered()
        for tag in (["e"], ["e", None], ["e", ""], ["e", 0], ["e", False], ["e", []], ["e", {}], ["e", ["x"]]):
            buzz.post(OWNER, "todo:done:123", tags=[["h", CHANNEL], tag])
        self.run_sync(gitlab, buzz)
        self.assertStillPending(gitlab)

    def test_an_e_tag_id_must_be_the_exact_hex64_event_id(self):
        """L1-PTS-049I e tag 的 id 必须恰好等于那条消息的 64 位 hex id：前缀、多一位、含空白或非 hex 的都不算；记录里本身不是 hex64 的 id（损坏的 state）也不能被同样的字符串匹配上。"""
        gitlab, buzz, ids = self.delivered()
        real = ids["123"]
        for bad in (real[:63], real + "0", " " + real, real + "\n", real[:32] + "g" + real[33:], "z" * 64):
            buzz.post(OWNER, "todo:done:123", tags=[["h", CHANNEL], ["e", bad, "", "reply"]])
        self.run_sync(gitlab, buzz)
        self.assertStillPending(gitlab)
        self.write_state({"7": make_record(7, event_id="not-a-hex-id", reply_to="Also-Not-Hex")})
        buzz = FakeBuzz()
        buzz.reply(OWNER, "todo:done:7", "not-a-hex-id")
        buzz.reply(OWNER, "todo:done:7", "Also-Not-Hex")
        gitlab = FakeGitLab([make_todo(7, iid=7)])
        self.run_sync(gitlab, buzz)
        self.assertEqual(gitlab.mark_calls, [])

    def test_only_the_id_matters_never_the_root_or_reply_marker(self):
        """L1-PTS-049J 【特征化，旧代码本来就绿】e tag 没有标记位、标记位是 root/reply/别的、带多余字段，都按 id 接受（不依赖客户端写不写标记位）。"""
        for tail in ([], [""], ["", "root"], ["", "reply"], ["wss://relay.example", "reply", "x"], ["", "mention"]):
            with self.subTest(tail=tail):
                gitlab, buzz, ids = self.delivered()
                buzz.post(OWNER, "todo:done:123", tags=[["h", CHANNEL], ["e", ids["123"], *tail]])
                self.run_sync(gitlab, buzz)
                self.assertEqual(gitlab.marked, [123])

    def test_every_e_tag_of_the_marker_is_looked_at_not_just_the_first_or_the_last(self):
        """L1-PTS-049K 一条标记带多个 e tag：任何一个是本待办的 Thread 就接受（前、中、后位置都试）；全都不是就不接受。"""
        noise = [["e", f"{0x77:064x}", "", "root"], ["e", f"{0x78:064x}", "", "reply"]]
        for position in ("first", "middle", "last"):
            with self.subTest(position=position):
                gitlab, buzz, ids = self.delivered()
                right = ["e", ids["123"], "", "reply"]
                tags = {"first": [right, *noise], "middle": [noise[0], right, noise[1]],
                        "last": [*noise, right]}[position]
                buzz.post(OWNER, "todo:done:123", tags=[["h", CHANNEL], *tags])
                self.run_sync(gitlab, buzz)
                self.assertEqual(gitlab.marked, [123])
        gitlab, buzz, ids = self.delivered()
        buzz.post(OWNER, "todo:done:123", tags=[["h", CHANNEL], *noise])
        self.run_sync(gitlab, buzz)
        self.assertStillPending(gitlab)

    def test_only_e_tags_count_a_p_or_h_tag_carrying_the_id_does_not(self):
        """L1-PTS-049L 只有小写 `e` tag 算：`p`、`h`、`t`、`a` tag（含大写 `E`）里带着那条消息的 id 也不行；tag 名要在第一位。"""
        gitlab, buzz, ids = self.delivered()
        real = ids["123"]
        for tags in ([["p", real]], [["h", real]], [["t", real]], [["a", real]], [["x", "e", real]],
                     [[["e"], real]], [["ee", real]], [["e ", real]], [["E", real]]):
            buzz.post(OWNER, "todo:done:123", tags=[["h", CHANNEL], *tags])
        self.run_sync(gitlab, buzz)
        self.assertStillPending(gitlab)

    def test_malformed_tags_never_crash_and_never_confirm(self):
        """L1-PTS-049M tags 缺失/None/字符串/数字/字典/混着非 list、空 list、None：不崩、不确认；同一条里混着一个合法的 e tag 时，坏项被跳过、合法项照算。"""
        gitlab, buzz, ids = self.delivered()
        real = ids["123"]
        for tags in (None, "e", 5, True, {"e": real}, real, [], [[]], [None], ["e", real], [5], [["h", CHANNEL], "e"],
                     [[None, real]], [{"e": real}], [[["e"], real]]):
            event = {"id": f"{0xd000 + len(buzz.events):064x}", "pubkey": OWNER, "kind": 9, "created_at": NOW + 5,
                     "content": "todo:done:123", "tags": tags}
            buzz.events.append(event)
        buzz.events.append({"id": f"{0xd100:064x}", "pubkey": OWNER, "kind": 9, "created_at": NOW + 5,
                            "content": "todo:done:123"})  # no `tags` key at all
        self.run_sync(gitlab, buzz)
        self.assertStillPending(gitlab)
        buzz.post(ASSISTANT, "todo:done:123", tags=[5, None, [], "e", ["e"], ["e", None], ["e", real, "", "reply"]])
        self.run_sync(gitlab, buzz)
        self.assertEqual(gitlab.marked, [123])

    def test_a_valid_thread_does_not_lift_the_other_checks(self):
        """L1-PTS-049N 回复了正确的 Thread 也绕不过其他门：陌生人、发布者自己、非首行、早于投递时间、别的 id 的标记照旧不算（Thread 绑定是「另外」的一道，不是替代）。"""
        gitlab, buzz, ids = self.delivered()
        root = ids["123"]
        buzz.reply(STRANGER, "todo:done:123", root)
        buzz.reply(PUBLISHER, "todo:done:123", root)
        buzz.reply(OWNER, "先说两句\ntodo:done:123", root)
        buzz.reply(OWNER, "todo:done:123", root, at=NOW - 301)
        buzz.reply(OWNER, "todo:done:124", root)  # right Thread, an id this script never delivered
        self.run_sync(gitlab, buzz)
        self.assertStillPending(gitlab)

    def test_the_same_id_marked_by_two_trusted_authors_calls_gitlab_once_whichever_order(self):
        """L1-PTS-049O 同一 id 被两位可信作者各标一次仍只调一次 GitLab；一个在 Thread 外、一个在 Thread 内时，不论先后，都只有 Thread 内那条生效。"""
        for order in ("outside_first", "inside_first", "both_inside"):
            with self.subTest(order=order):
                gitlab, buzz, ids = self.delivered()
                outside = (lambda at: buzz.post(OWNER, "todo:done:123", at=at))
                inside = (lambda at, who=ASSISTANT: buzz.reply(who, "todo:done:123", ids["123"], at=at))
                first, second = {"outside_first": (outside, inside), "inside_first": (inside, outside),
                                 "both_inside": (inside, lambda at: inside(at, OWNER))}[order]
                first(NOW + 5)
                second(NOW + 6)
                self.run_sync(gitlab, buzz)
                self.assertEqual(gitlab.mark_calls, [123])
                self.assertEqual(self.state_json()["todos"]["123"]["state"], "DONE")

    def test_a_marker_outside_the_thread_does_not_use_up_the_failed_slot_of_a_real_one(self):
        """L1-PTS-049P GitLab 出错时：Thread 外的标记不触发那一次写，也不占「本轮已失败」；Thread 内的标记才触发，失败后下一轮照常重试。"""
        gitlab, buzz, ids = self.delivered()
        buzz.post(OWNER, "todo:done:123", at=NOW + 5)
        gitlab.mark_error = sync.SyncError("HTTP 500")
        self.run_sync(gitlab, buzz)  # the stray marker is ignored: there is nothing to fail
        self.assertEqual(gitlab.mark_calls, [])
        buzz.reply(ASSISTANT, "todo:done:123", ids["123"], at=NOW + 6)
        with self.assertRaises(sync.SyncError):
            self.run_sync(gitlab, buzz)
        self.assertEqual(gitlab.mark_calls, [123])
        gitlab.mark_error = None
        self.run_sync(gitlab, buzz)
        self.assertEqual(gitlab.marked, [123])

    def test_the_event_id_recorded_by_reconcile_is_a_valid_thread_target(self):
        """L1-PTS-049Q 发出后崩溃、下一轮由对账补出 event_id 的待办：回复那条消息照常接受（Thread 绑定用的是对账写进 state 的 id）；别的顶层消息、不存在的 id 不行。"""
        gitlab, buzz = FakeGitLab([make_todo(123)]), FakeBuzz()
        original = self.crash_after_send(buzz)
        with self.assertRaises(sync.SyncError):
            self.run_sync(gitlab, buzz)
        buzz.send = original
        event_id = buzz.events[0]["id"]
        buzz.post(OWNER, "todo:done:123", at=NOW + 4)
        buzz.reply(OWNER, "todo:done:123", f"{0x55:064x}", at=NOW + 5)
        self.run_sync(gitlab, buzz)
        self.assertStillPending(gitlab)
        buzz.reply(ASSISTANT, "todo:done:123", event_id, at=NOW + 6)
        self.run_sync(gitlab, buzz)
        self.assertEqual(gitlab.marked, [123])


class ReactionDoneTest(Harness):
    """A trusted author's ✅ reaction on the todo's own message marks it done, next to the `todo:done:<id>` reply.

    A reaction event (kind 7) carries one `e` tag with the id of the message it was put on: no `h`, no `p`.  It counts
    only for the ACKED todo whose own `event_id` that is: not for the Thread root, not for a sibling todo.
    """

    def delivered(self, *todos, **cfg):
        """A fresh state directory each time, so a subTest loop can call this again."""

        shutil.rmtree(self.state, ignore_errors=True)
        todos = todos or (make_todo(123),)
        gitlab, buzz, config = FakeGitLab(list(todos)), FakeBuzz(), make_config(**cfg)
        self.run_sync(gitlab, buzz, config)
        return gitlab, buzz, config, {str(t["id"]): m["id"] for t, m in zip(todos, buzz.sent)}

    def seeded(self, records, *, listed=None, **cfg):
        """State written by hand (ACKED records carry event_id 000…<id>); GitLab lists `listed` (default: all) as pending."""

        shutil.rmtree(self.state, ignore_errors=True)
        self.write_state({str(n): rec for n, rec in records.items()})
        gitlab = FakeGitLab([make_todo(n, iid=n) for n in (records if listed is None else listed)])
        return gitlab, FakeBuzz(), make_config(**cfg)

    def raw_reaction(self, buzz, **fields):
        event = {"id": f"{0xe900 + len(buzz.reactions):064x}", "kind": 7, "pubkey": OWNER, "content": "✅",
                 "created_at": NOW + 5, "tags": [["e", "0" * 64]], **fields}
        buzz.reactions.append({k: v for k, v in event.items() if v is not ...})

    def assertNothingMarked(self, gitlab, tid="123"):
        self.assertEqual(gitlab.mark_calls, [])
        self.assertEqual(self.state_json()["todos"][tid]["state"], "ACKED")

    # -- what counts ---------------------------------------------------------

    def test_a_trusted_authors_check_mark_on_the_todo_message_marks_it_done_once(self):
        """L1-PTS-110 owner 或助手对那条待办消息点 ✅ → 只调一次 mark_as_done，状态 DONE、记 done_at，marked_done 计 1；再跑一轮不再调。"""
        for author in (OWNER, ASSISTANT):
            with self.subTest(author=author[:1]):
                gitlab, buzz, config, ids = self.delivered()
                buzz.react(author, "✅", ids["123"])
                result = self.run_sync(gitlab, buzz, config)
                self.assertEqual((gitlab.marked, result["marked_done"]), ([123], 1))
                record = self.state_json()["todos"]["123"]
                self.assertEqual((record["state"], record["done_at"]), ("DONE", NOW))
                self.run_sync(gitlab, buzz, config)
                self.assertEqual(gitlab.mark_calls, [123])

    def test_the_default_is_the_check_mark_only_and_u_fe0f_is_ignored_on_both_sides(self):
        """L1-PTS-111 缺省 done_emojis=["✅"]；比较前两边都去掉 U+FE0F：✅+FE0F 算，配置里写 ✔️（带 FE0F）时裸 ✔ 也算，反之亦然；配置了别的表情后 ✅ 不再算（缺省值不会偷偷并进来）；自定义列表里每一项都算。"""
        cases = [
            ("✅\ufe0f", None, True), ("✅", None, True), ("✔", None, False), ("✔️", None, False),
            ("✔", ["✔️"], True), ("✔️", ["✔️"], True), ("✔️", ["✔"], True), ("✅", ["✅\ufe0f"], True),
            ("✅", ["✔️"], False), ("✅", ["✔️", "✅"], True), ("✔️", ["✔️", "✅"], True), ("👍", ["✔️", "✅"], False),
        ]
        for emoji, configured, counts in cases:
            with self.subTest(emoji=emoji, configured=configured):
                cfg = {} if configured is None else {"todo": {"done_emojis": configured}}
                gitlab, buzz, config, ids = self.delivered(**cfg)
                buzz.react(OWNER, emoji, ids["123"])
                self.run_sync(gitlab, buzz, config)
                self.assertEqual(gitlab.marked, [123] if counts else [])

    def test_only_a_configured_done_author_counts_not_a_stranger_the_publisher_or_the_workflow(self):
        """L1-PTS-112 陌生人、发布者自己、Workflow 服务公钥的 ✅ 不算；done_authors 只配助手时 owner 的 ✅ 不算、助手的算。"""
        gitlab, buzz, config, ids = self.delivered()
        for outsider in (STRANGER, PUBLISHER, "9" * 64):
            buzz.react(outsider, "✅", ids["123"])
        self.run_sync(gitlab, buzz, config)
        self.assertNothingMarked(gitlab)
        gitlab, buzz, config, ids = self.delivered(done_authors=[ASSISTANT])
        buzz.react(OWNER, "✅", ids["123"])
        self.run_sync(gitlab, buzz, config)
        self.assertNothingMarked(gitlab)
        buzz.react(ASSISTANT, "✅", ids["123"])
        self.run_sync(gitlab, buzz, config)
        self.assertEqual(gitlab.marked, [123])

    # -- what does not count -------------------------------------------------

    def test_other_reactions_and_look_alike_contents_are_not_done_signals(self):
        """L1-PTS-113 个人 agent 的已读回执 👀，以及 👍 + ❌ ✔️ 🎉 空串、✅✅（不做子串匹配）、✅ 前后带空白/换行/字母/零宽字符、:white_check_mark:、只有 U+FE0F，都不是完成信号；待办保持 ACKED。"""
        contents = ("👀", "👍", "+", "❌", "✔️", "🎉", "", "\ufe0f", "✅✅", "✅ ", " ✅", "✅\n", "a✅", "✅a", "✅\u200b",
                    "✅✅\ufe0f", ":white_check_mark:", "white_check_mark")
        for content in contents:
            with self.subTest(content=content):
                gitlab, buzz, config, ids = self.delivered(make_todo(123, iid=1), make_todo(124, iid=2))
                buzz.react(OWNER, content, ids["123"])
                buzz.react(ASSISTANT, content, ids["123"])
                buzz.react(OWNER, "✅", ids["124"])  # control: the scan runs and a real ✅ next to it counts
                self.run_sync(gitlab, buzz, config)
                self.assertEqual(gitlab.mark_calls, [124])
                self.assertEqual(self.state_json()["todos"]["123"]["state"], "ACKED")

    def test_a_reaction_whose_content_is_not_a_string_never_crashes_and_never_counts(self):
        """L1-PTS-114 content 缺失、None、数字、列表、字典的 kind 7 事件：不崩、不算；同批里正常的 ✅ 照常生效。"""
        gitlab, buzz, config, ids = self.delivered()
        for content in (None, 5, True, ["✅"], {"✅": 1}, 1.5):
            self.raw_reaction(buzz, content=content, tags=[["e", ids["123"]]])
        self.raw_reaction(buzz, content=..., tags=[["e", ids["123"]]])  # no `content` key at all
        self.run_sync(gitlab, buzz, config)
        self.assertNothingMarked(gitlab)
        buzz.react(OWNER, "✅", ids["123"])
        self.run_sync(gitlab, buzz, config)
        self.assertEqual(gitlab.marked, [123])

    def test_only_kind_7_events_count_even_if_the_reader_hands_back_something_else(self):
        """L1-PTS-114B 读回来的事件里混着 kind 9（比如内容就是 ✅ 的普通消息）或缺 kind 的：不算 reaction，不能确认完成；同批里 kind 7 的 ✅ 照常生效。"""
        gitlab, buzz, config, ids = self.delivered()
        for kind in (9, 1, "7", 7.5, None, True, ...):
            self.raw_reaction(buzz, kind=kind, tags=[["e", ids["123"]]])
        self.run_sync(gitlab, buzz, config)
        self.assertNothingMarked(gitlab)
        self.raw_reaction(buzz, tags=[["e", ids["123"]]])
        self.run_sync(gitlab, buzz, config)
        self.assertEqual(gitlab.marked, [123])

    def test_a_reaction_only_counts_on_the_todo_message_itself_not_on_another_todos(self):
        """L1-PTS-115 两个不同目标的待办：点在 2 的消息上只标 2，不标 1；反向同理；点在无关消息（Workflow 唤醒、本人随手一句、频道里不存在的 id）上谁也不标。"""
        gitlab, buzz, config, ids = self.delivered(make_todo(1, iid=1), make_todo(2, iid=2))
        wake = buzz.post("9" * 64, f"@助手 有一条新的 GitLab 待办（消息 {ids['1']}）。")
        chatter = buzz.post(OWNER, "在忙，晚点看")
        for target in (wake, chatter, "0" * 64, f"{0x55:064x}"):
            buzz.react(OWNER, "✅", target)
        self.run_sync(gitlab, buzz, config)
        self.assertEqual(gitlab.mark_calls, [])
        buzz.react(OWNER, "✅", ids["2"])
        self.run_sync(gitlab, buzz, config)
        self.assertEqual(gitlab.marked, [2])
        self.assertEqual(self.state_json()["todos"]["1"]["state"], "ACKED")

    def test_a_reaction_on_the_thread_root_marks_only_the_todo_that_is_that_root_never_its_siblings(self):
        """L1-PTS-116 待办 2 归并进待办 1 的 Thread（2.reply_to = 1 的消息）：点在 1 的消息上只标 1、不标 2（Thread 根不算 2 的入口）；点在 2 自己的消息上只标 2。这与文字标记不同：回复 Thread 根仍算（见 L1-PTS-049E）。"""
        gitlab, buzz, config, ids = self.delivered(make_todo(1, iid=45), make_todo(2, iid=45, action="mentioned"))
        self.assertEqual([m["reply_to"] for m in buzz.sent], [None, ids["1"]])
        buzz.react(OWNER, "✅", ids["1"])
        self.run_sync(gitlab, buzz, config)
        self.assertEqual(gitlab.marked, [1])
        self.assertEqual(self.state_json()["todos"]["2"]["state"], "ACKED")
        buzz.react(OWNER, "✅", ids["2"])
        self.run_sync(gitlab, buzz, config)
        self.assertEqual(gitlab.marked, [1, 2])

    def test_a_thread_root_that_is_not_a_todo_message_is_not_an_entry_either(self):
        """L1-PTS-117 state 里 1、2 号待办共用一个不是待办消息的 Thread 根 R（reply_to=R）：点在 R 上谁也不标；点在各自的 event_id 上只标各自。顶层待办（reply_to=None）也不能借用别人的根。"""
        root, other = f"{0x99:064x}", f"{0x98:064x}"
        gitlab, buzz, config = self.seeded({1: make_record(1, reply_to=root), 2: make_record(2, reply_to=root),
                                            3: make_record(3, reply_to=None)})
        buzz.react(OWNER, "✅", root)
        buzz.react(OWNER, "✅", other)
        buzz.react(OWNER, "✅", f"{3:064x}", at=NOW + 6)  # 3's own message: marks 3 only
        self.run_sync(gitlab, buzz, config)
        self.assertEqual(gitlab.marked, [3])
        buzz.react(OWNER, "✅", f"{2:064x}", at=NOW + 7)
        self.run_sync(gitlab, buzz, config)
        self.assertEqual(gitlab.marked, [3, 2])
        self.assertEqual(self.state_json()["todos"]["1"]["state"], "ACKED")

    def test_the_e_tag_id_must_be_the_exact_hex64_and_only_lowercase_e_tags_count(self):
        """L1-PTS-118 e tag 的 id 必须恰好是那条待办的 64 位小写 hex：前缀、多一位、大写、含空白/换行、非 hex 都不算；记录里本身不是 hex64 的 id（损坏的 state）也不能被同样的字符串匹配；名字必须恰为小写 `e`（p/h/t/a/E/ee/`e `、tag 名不在第一位都不算）。"""
        gitlab, buzz, config = self.seeded({0xab: make_record(0xab)})  # …00ab: the upper-case form differs
        real = f"{0xab:064x}"
        self.assertNotEqual(real, real.upper())
        for bad in (real[:63], real + "0", real.upper(), " " + real, real + "\n", real[:32] + "g" + real[33:], "z" * 64):
            self.raw_reaction(buzz, tags=[["e", bad]])
        for tags in ([["p", real]], [["h", real]], [["t", real]], [["a", real]], [["x", "e", real]], [[["e"], real]],
                     [["ee", real]], [["e ", real]], [["E", real]], [["p", real], ["h", real]]):
            self.raw_reaction(buzz, tags=tags)
        self.run_sync(gitlab, buzz, config)
        self.assertNothingMarked(gitlab, str(0xab))
        buzz.react(OWNER, "✅", real)  # the exact id, for contrast
        self.run_sync(gitlab, buzz, config)
        self.assertEqual(gitlab.marked, [0xab])
        gitlab, buzz, config = self.seeded({7: make_record(7, event_id="not-a-hex-id", reply_to="Also-Not-Hex")})
        buzz.react(OWNER, "✅", "not-a-hex-id")
        buzz.react(OWNER, "✅", "Also-Not-Hex")
        self.run_sync(gitlab, buzz, config)
        self.assertEqual(gitlab.mark_calls, [])

    def test_e_tag_extras_and_position_do_not_matter_but_malformed_tags_never_crash_or_confirm(self):
        """L1-PTS-119 e tag 带标记位/中继地址/多余字段照常按 id 认；一条 reaction 带多个 e tag 时前、中、后位置都行，全不是就不行；tags 缺失/None/字符串/数字/字典/混着非 list 的坏项：不崩、不算，同一条里混着合法 e tag 时坏项被跳过、合法项照算。"""
        real_tails = ([], [""], ["", "root"], ["", "reply"], ["wss://relay.example", "reply", "x"])
        for tail in real_tails:
            with self.subTest(tail=tail):
                gitlab, buzz, config, ids = self.delivered()
                self.raw_reaction(buzz, tags=[["e", ids["123"], *tail]])
                self.run_sync(gitlab, buzz, config)
                self.assertEqual(gitlab.marked, [123])
        noise = [["e", f"{0x77:064x}", "", "root"], ["e", f"{0x78:064x}"]]
        for position in ("first", "middle", "last"):
            with self.subTest(position=position):
                gitlab, buzz, config, ids = self.delivered()
                right = ["e", ids["123"]]
                tags = {"first": [right, *noise], "middle": [noise[0], right, noise[1]], "last": [*noise, right]}[position]
                self.raw_reaction(buzz, tags=tags)
                self.run_sync(gitlab, buzz, config)
                self.assertEqual(gitlab.marked, [123])
        gitlab, buzz, config, ids = self.delivered()
        real = ids["123"]
        self.raw_reaction(buzz, tags=noise)
        for tags in (None, "e", 5, True, {"e": real}, real, [], [[]], [None], ["e", real], [5], [["h", CHANNEL], "e"],
                     [[None, real]], [{"e": real}], [["e"]], [["e", None]], [["e", ""]], [["e", 0]], [["e", [real]]]):
            self.raw_reaction(buzz, tags=tags)
        self.raw_reaction(buzz, tags=...)  # no `tags` key at all
        self.run_sync(gitlab, buzz, config)
        self.assertNothingMarked(gitlab)
        self.raw_reaction(buzz, tags=[5, None, [], "e", ["e"], ["e", None], ["e", real]])
        self.run_sync(gitlab, buzz, config)
        self.assertEqual(gitlab.marked, [123])

    def test_one_reaction_naming_two_todo_messages_in_its_e_tags_marks_both(self):
        """L1-PTS-119B 判定是「e tag 里有一个 id 等于该待办的 event_id」：一条 reaction 的 e tag 同时列了两条待办的消息 → 两条都标（各调一次）；只列了别的 id → 都不标。"""
        gitlab, buzz, config, ids = self.delivered(make_todo(1, iid=1), make_todo(2, iid=2))
        self.raw_reaction(buzz, tags=[["e", ids["2"]], ["e", ids["1"]]])
        self.run_sync(gitlab, buzz, config)
        self.assertEqual(gitlab.mark_calls, [1, 2])  # in message-id order, whatever order the tags come in

    def test_a_reaction_older_than_the_delivery_is_ignored_with_the_same_300_second_slack_as_a_text_marker(self):
        """L1-PTS-120 created_at 早于 delivered_at-300 的 ✅ 不算，正好在下界上算（同文字标记的 L1-PTS-047）。"""
        # 9 was delivered long before and keeps the scan window open far enough back to see the too-old reactions
        # `created` differs from `delivered_at` on purpose: the bound is the delivery time, whatever `created` says
        gitlab, buzz, config = self.seeded({1: make_record(1, created=NOW + 5000), 2: make_record(2, created=NOW - 5000),
                                            3: make_record(3), 9: make_record(9, delivered_at=NOW - 1000)})
        buzz.react(OWNER, "✅", f"{1:064x}", at=NOW - 300)  # exactly on the lower bound
        buzz.react(OWNER, "✅", f"{2:064x}", at=NOW - 301)  # one second too old
        buzz.react(OWNER, "✅", f"{3:064x}", at=NOW + 10)
        self.run_sync(gitlab, buzz, config, clock=lambda: NOW + 60)
        self.assertEqual(sorted(gitlab.marked), [1, 3])
        self.assertEqual(self.state_json()["todos"]["2"]["state"], "ACKED")

    def test_a_reaction_without_a_usable_created_at_is_ignored(self):
        """L1-PTS-121 created_at 缺失、None、字符串、浮点、布尔的 kind 7 事件不能确认完成，也不会让排序崩掉（记录的 delivered_at=0，布尔 True==1 与 0.5 若被当数字比较就会过线）；同一条待办上整数 created_at 的 ✅ 照常生效。"""
        gitlab, buzz, config = self.seeded({1: make_record(1, created=0, delivered_at=0)})
        target = f"{1:064x}"
        for created in (None, "1789819300", NOW + 0.5, 5.0, True, False):
            self.raw_reaction(buzz, tags=[["e", target]], created_at=created)
        self.raw_reaction(buzz, tags=[["e", target]], created_at=...)  # no `created_at` key
        self.run_sync(gitlab, buzz, config)
        self.assertNothingMarked(gitlab, "1")
        buzz.react(OWNER, "✅", target, at=NOW - 10)
        self.run_sync(gitlab, buzz, config)
        self.assertEqual(gitlab.marked, [1])

    def test_a_reaction_only_settles_an_acked_todo(self):
        """L1-PTS-122 DONE、RESOLVED（GitLab 已不再列出）、REJECTED 的记录，点它们的 ✅ 不调 GitLab；从没送达的 PENDING（对账找不到消息）被丢弃，点它的 id 也不标。旁边 ACKED 的待办照常被标（证明扫描确实跑了）。"""
        records = {1: make_record(1, "DONE", done_at=NOW - 10), 2: make_record(2, "RESOLVED", resolved_at=NOW - 10),
                   3: make_record(3, "REJECTED", rejected_at=NOW - 10), 4: make_record(4, "PENDING"),
                   9: make_record(9)}
        gitlab, buzz, config = self.seeded(records, listed=[1, 3, 9])  # 4 is not listed: it is dropped, not re-sent
        for tid in (1, 2, 3, 4, 9):
            buzz.react(OWNER, "✅", f"{tid:064x}")
        self.run_sync(gitlab, buzz, config)
        self.assertEqual(gitlab.mark_calls, [9])
        todos = self.state_json()["todos"]
        self.assertEqual([todos[t]["state"] for t in ("1", "2", "3", "9")], ["DONE", "RESOLVED", "REJECTED", "DONE"])
        self.assertNotIn("4", todos)

    def test_a_reaction_on_a_reconciled_todo_counts_once_the_message_id_is_known(self):
        """L1-PTS-123 发出后崩溃、下一轮对账补出 event_id 的待办：点那条消息的 ✅ 照常生效；点别的顶层消息不算。"""
        gitlab, buzz = FakeGitLab([make_todo(123)]), FakeBuzz()
        original = self.crash_after_send(buzz)
        with self.assertRaises(sync.SyncError):
            self.run_sync(gitlab, buzz)
        buzz.send = original
        event_id = buzz.events[0]["id"]
        other = buzz.post(OWNER, "随手一句", at=NOW + 4)
        buzz.react(OWNER, "✅", other, at=NOW + 5)
        self.run_sync(gitlab, buzz)
        self.assertNothingMarked(gitlab)
        buzz.react(ASSISTANT, "✅", event_id, at=NOW + 6)
        self.run_sync(gitlab, buzz)
        self.assertEqual(gitlab.marked, [123])

    def test_mark_done_disabled_never_scans_reactions_or_writes(self):
        """L1-PTS-124 mark_done=false：不读 reaction（也不读文字标记）、永不写 GitLab。"""
        gitlab, buzz, config, ids = self.delivered(todo={"mark_done": False})
        buzz.react(OWNER, "✅", ids["123"])
        self.run_sync(gitlab, buzz, config)
        self.assertEqual((gitlab.mark_calls, buzz.reaction_scans, buzz.scans), ([], [], []))
        self.assertEqual(self.state_json()["todos"]["123"]["state"], "ACKED")
        self.run_sync(gitlab, buzz, make_config())  # control: the flag was the only thing holding it back
        self.assertEqual(gitlab.marked, [123])

    # -- one path for both kinds of signal -----------------------------------

    def test_a_text_marker_and_a_reaction_and_two_authors_call_gitlab_only_once_for_one_todo(self):
        """L1-PTS-125 同一待办既有文字标记又有 reaction、或两位作者都点了 ✅、或三者齐全 → 只调一次 mark_as_done，marked_done 计 1。"""
        combos = {
            "text and reaction": lambda b, r: (b.reply(ASSISTANT, "todo:done:123", r, at=NOW + 5), b.react(OWNER, "✅", r, at=NOW + 6)),
            "reaction and text": lambda b, r: (b.react(OWNER, "✅", r, at=NOW + 5), b.reply(ASSISTANT, "todo:done:123", r, at=NOW + 6)),
            "two reactions": lambda b, r: (b.react(OWNER, "✅", r, at=NOW + 5), b.react(ASSISTANT, "✅", r, at=NOW + 6)),
            "all three": lambda b, r: (b.react(OWNER, "✅", r, at=NOW + 5), b.reply(OWNER, "todo:done:123", r, at=NOW + 6),
                                       b.react(ASSISTANT, "✅", r, at=NOW + 7)),
        }
        for label, act in combos.items():
            with self.subTest(label):
                gitlab, buzz, config, ids = self.delivered()
                act(buzz, ids["123"])
                result = self.run_sync(gitlab, buzz, config)
                self.assertEqual((gitlab.mark_calls, result["marked_done"]), ([123], 1))
                self.assertEqual(self.state_json()["todos"]["123"]["state"], "DONE")

    def test_a_failing_mark_does_not_block_the_others_and_a_failed_id_is_tried_once_per_round(self):
        """L1-PTS-126 待办 1 的 mark 失败（不论标记是文字还是 reaction）不挡后面的待办；同一个失败 id 被文字标记和 reaction 各标一次，本轮也只打一次 GitLab；失败的仍是 ACKED，下一轮重试。"""
        for first_is_reaction in (True, False):
            with self.subTest(first_is_reaction=first_is_reaction):
                gitlab, buzz, config, ids = self.delivered(make_todo(1, iid=1), make_todo(2, iid=2))
                gitlab.mark_errors[1] = sync.GitLabHTTPError("POST", "todos/1/mark_as_done", 403)
                if first_is_reaction:
                    buzz.react(OWNER, "✅", ids["1"], at=NOW + 5)
                    buzz.reply(OWNER, "todo:done:2", ids["2"], at=NOW + 6)
                else:
                    buzz.reply(OWNER, "todo:done:1", ids["1"], at=NOW + 5)
                    buzz.react(OWNER, "✅", ids["2"], at=NOW + 6)
                buzz.react(ASSISTANT, "✅", ids["1"], at=NOW + 7)  # a second signal for the failing id
                buzz.reply(ASSISTANT, "todo:done:1", ids["1"], at=NOW + 8)
                with self.assertRaises(sync.SyncError) as ctx:
                    self.run_sync(gitlab, buzz, config)
                self.assertIn("403", str(ctx.exception))
                self.assertEqual(gitlab.mark_calls, [1, 2])
                todos = self.state_json()["todos"]
                self.assertEqual((todos["1"]["state"], todos["2"]["state"]), ("ACKED", "DONE"))
                gitlab.mark_errors.clear()
                self.run_sync(gitlab, buzz, config)
                self.assertEqual(gitlab.marked, [2, 1])

    def test_every_mark_is_saved_before_the_next_gitlab_call_whichever_signal_it_came_from(self):
        """L1-PTS-127B 每标完一条就落盘（崩溃时不重复标）：处理下一条待办、调 GitLab 之前，磁盘上的 state 里前一条已经是 DONE——文字标记与 reaction 都一样。"""
        gitlab, buzz, config, ids = self.delivered(*(make_todo(n, iid=n) for n in (1, 2, 3)))
        on_disk = []
        original = gitlab.mark_done

        def spying(todo_id):
            on_disk.append((todo_id, {t: r["state"] for t, r in self.state_json()["todos"].items()}))
            original(todo_id)

        gitlab.mark_done = spying
        buzz.react(OWNER, "✅", ids["1"], at=NOW + 5)
        buzz.reply(OWNER, "todo:done:2", ids["2"], at=NOW + 6)
        buzz.react(OWNER, "✅", ids["3"], at=NOW + 7)
        self.run_sync(gitlab, buzz, config)
        self.assertEqual([tid for tid, _ in on_disk], [1, 2, 3])
        self.assertEqual(on_disk[1][1]["1"], "DONE")  # by reaction
        self.assertEqual(on_disk[2][1]["2"], "DONE")  # by text marker

    def test_text_markers_and_reactions_are_processed_together_in_created_at_order(self):
        """L1-PTS-127 文字标记与 reaction 按 created_at 统一排序处理（不是先文字后 reaction）：1 号 reaction（+5）与 4 号文字标记（+6）都失败时，先 1 后 4，抛的是最后一个错误（4 的）；成功的 2（文字 +7）、3（reaction +8）照常标。"""
        gitlab, buzz, config, ids = self.delivered(*(make_todo(n, iid=n) for n in (1, 2, 3, 4)))
        gitlab.mark_errors = {1: sync.SyncError("first HTTP 500"), 4: sync.SyncError("last HTTP 502")}
        buzz.react(OWNER, "✅", ids["1"], at=NOW + 5)
        buzz.reply(OWNER, "todo:done:4", ids["4"], at=NOW + 6)
        buzz.reply(OWNER, "todo:done:2", ids["2"], at=NOW + 7)
        buzz.react(OWNER, "✅", ids["3"], at=NOW + 8)
        with self.assertRaises(sync.SyncError) as ctx:
            self.run_sync(gitlab, buzz, config)
        self.assertEqual(str(ctx.exception), "last HTTP 502")
        self.assertEqual(gitlab.mark_calls, [1, 4, 2, 3])
        self.assertEqual(gitlab.marked, [2, 3])

    def test_a_failing_reaction_scan_does_not_stop_delivery_but_fails_the_round_at_the_end(self):
        """L1-PTS-128 读 reaction 出 SyncError：本轮新待办照发、状态落盘，随后以该错误失败；恢复后下一轮把 ✅ 补上。"""
        gitlab, buzz, config, ids = self.delivered()
        buzz.react(OWNER, "✅", ids["123"])
        buzz.reaction_error = sync.SyncError("reactions HTTP 500")
        gitlab.todos.append(make_todo(124, iid=46))
        with self.assertRaises(sync.SyncError) as ctx:
            self.run_sync(gitlab, buzz, config)
        self.assertIn("reactions HTTP 500", str(ctx.exception))
        self.assertEqual(len(buzz.sent), 2)
        todos = self.state_json()["todos"]
        self.assertEqual((todos["123"]["state"], todos["124"]["state"]), ("ACKED", "ACKED"))
        buzz.reaction_error = None
        self.run_sync(gitlab, buzz, config)
        self.assertEqual(gitlab.marked, [123])
        self.assertEqual(len(buzz.sent), 2)

    # -- when to scan --------------------------------------------------------

    def test_nothing_awaiting_means_no_message_scan_at_all_not_even_for_reactions(self):
        """L1-PTS-129 没有等待完成的 ACKED 时，`messages get` 一次都不发（kind 9 与 kind 7 都不发）：空 state、只有 DONE、只有已 RESOLVED 的、ACKED 但 GitLab 已不再列出（本轮转 RESOLVED）。"""
        cases = {
            "empty": ({}, []),
            "only done": ({1: make_record(1, "DONE", done_at=NOW - 5)}, [1]),
            "only resolved": ({1: make_record(1, "RESOLVED", resolved_at=NOW - 5)}, []),
            "acked but gone from gitlab": ({1: make_record(1)}, []),
        }
        for label, (records, listed) in cases.items():
            with self.subTest(label):
                gitlab, buzz, config = self.seeded(records, listed=listed)
                buzz.react(OWNER, "✅", f"{1:064x}")
                self.run_sync(gitlab, buzz, config)
                self.assertEqual((buzz.scans, buzz.reaction_scans, gitlab.mark_calls), ([], [], []))
        gitlab, buzz, config = self.seeded({1: make_record(1)})  # control: one ACKED todo does trigger both scans
        self.run_sync(gitlab, buzz, config)
        self.assertEqual((len(buzz.scans), len(buzz.reaction_scans)), (1, 1))

    def test_the_reaction_scan_uses_the_same_window_as_the_text_scan(self):
        """L1-PTS-130 reaction 扫描与文字标记扫描同一个 since：最老待确认投递往前 300 秒（判定下界的余量），最多回看 30 天。"""
        for delivered, expected in ((NOW - 5000, NOW - 5300), (NOW - 40 * DAY, NOW - 30 * DAY)):
            with self.subTest(delivered_offset=delivered - NOW):
                gitlab, buzz, config = self.seeded({1: make_record(1, delivered_at=delivered),
                                                    2: make_record(2, delivered_at=NOW - 10)})
                self.run_sync(gitlab, buzz, config)
                self.assertEqual((buzz.scans, buzz.reaction_scans), ([expected], [expected]))

    def test_the_reaction_scan_happens_after_the_message_scan_and_before_delivery(self):
        """L1-PTS-131 顺序：取 pending → 扫文字 → 扫 reaction → 投递。"""
        log: list[str] = []
        gitlab, buzz = FakeGitLab([make_todo(1)], log=log), FakeBuzz(log=log)
        self.run_sync(gitlab, buzz)
        gitlab.todos.append(make_todo(2, iid=46))
        log.clear()
        self.run_sync(gitlab, buzz)
        self.assertLess(log.index("fetch"), log.index("scan"))
        self.assertLess(log.index("scan"), log.index("reactions"))
        self.assertLess(log.index("reactions"), log.index("send"))


class ScanWindowTest(Harness):
    """The Buzz scan windows, observed through the `since` argument the fake records."""

    def test_reconcile_scans_from_the_oldest_pending_minus_300_seconds(self):
        """L1-PTS-075 PENDING 对账只扫最老那条 created 往前 300 秒之后的消息（取最小值、余量正好 300）。"""
        self.write_state({"1": make_record(1, "PENDING", created=NOW - 1000),
                          "2": make_record(2, "PENDING", created=NOW - 2000)})
        buzz = FakeBuzz()
        self.run_sync(FakeGitLab([]), buzz)
        self.assertEqual(buzz.scans, [NOW - 2000 - 300])

    def test_marker_scan_starts_300_seconds_before_the_oldest_delivery(self):
        """L1-PTS-076 标记扫描与 reaction 扫描从最老的待确认投递往前 300 秒开始（取最小值、余量正好 300 = 判定下界的余量）。"""
        self.write_state({"1": make_record(1, delivered_at=NOW - 5000), "2": make_record(2, delivered_at=NOW - 2000)})
        buzz = FakeBuzz()
        self.run_sync(FakeGitLab([make_todo(1), make_todo(2)]), buzz)
        self.assertEqual((buzz.scans, buzz.reaction_scans), ([NOW - 5000 - 300], [NOW - 5000 - 300]))

    def test_marker_scan_is_capped_at_thirty_days(self):
        """L1-PTS-077 标记与 reaction 扫描最多回看 30 天（从 now 算）：比 30 天更老的投递被封顶；封顶线两侧各取较晚者，封顶线上 delivered-300 == 封顶 → 取封顶，多 1 秒才越过。"""
        cap = NOW - 30 * DAY
        cases = ((NOW - 40 * DAY, cap), (cap - 30, cap), (cap + 100, cap), (cap + 300, cap), (cap + 301, cap + 1),
                 (cap + 400, cap + 100))
        for delivered, expected in cases:
            self.tmp.cleanup()
            self.tmp = tempfile.TemporaryDirectory()
            self.state = Path(self.tmp.name) / "state"
            self.write_state({"1": make_record(1, delivered_at=delivered)})
            buzz = FakeBuzz()
            self.run_sync(FakeGitLab([make_todo(1)]), buzz)
            with self.subTest(delivered_offset=delivered - NOW):
                self.assertEqual((buzz.scans, buzz.reaction_scans), ([expected], [expected]))

    def one_awaiting(self, delivered_at, tid=1):
        """One ACKED todo (message id 000…<tid>) delivered at `delivered_at`, still listed by GitLab; fresh state each call."""

        shutil.rmtree(self.state, ignore_errors=True)
        self.write_state({str(tid): make_record(tid, delivered_at=delivered_at)})
        return FakeGitLab([make_todo(tid, iid=tid)]), FakeBuzz()

    def signal(self, buzz, kind, at, tid=1):
        """A trusted `todo:done` reply (`kind` "text") or a ✅ on the todo's own message ("reaction") created at `at`."""

        if kind == "text":
            buzz.reply(OWNER, f"todo:done:{tid}", f"{tid:064x}", at=at)
        else:
            buzz.react(OWNER, "✅", f"{tid:064x}", at=at)

    def test_a_single_todos_done_signals_are_accepted_exactly_from_delivered_minus_300_for_text_and_reaction(self):
        """L1-PTS-076B 单个 ACKED 待办（delivered_at=D）：文字标记与 ✅ 各自，D-300、D-61、D-60、D、D+1 生效，D-301 忽略。旧代码扫描起点是 D-60，D-300..D-61 的合法信号根本读不到（fake 按 since 过滤）。"""
        delivered = NOW - 5000
        for kind in ("text", "reaction"):
            for offset, accepted in ((-301, False), (-300, True), (-61, True), (-60, True), (0, True), (1, True)):
                with self.subTest(kind=kind, offset=offset):
                    gitlab, buzz = self.one_awaiting(delivered)
                    self.signal(buzz, kind, delivered + offset)
                    self.run_sync(gitlab, buzz)
                    self.assertEqual(gitlab.mark_calls, [1] if accepted else [])
                    self.assertEqual(self.state_json()["todos"]["1"]["state"], "DONE" if accepted else "ACKED")

    def test_both_scans_start_at_or_before_delivered_minus_300_and_at_the_same_second(self):
        """L1-PTS-076C 单个待办：传给 channel_messages 与 channel_reactions 的 since 相等，且 ≤ D-300（正好 D-300，不多留也不少留）。"""
        delivered = NOW - 5000
        gitlab, buzz = self.one_awaiting(delivered)
        self.run_sync(gitlab, buzz)
        self.assertEqual(len(buzz.scans), 1)
        self.assertEqual(len(buzz.reaction_scans), 1)
        self.assertEqual(buzz.scans, buzz.reaction_scans)
        self.assertLessEqual(buzz.scans[0], delivered - 300)
        self.assertEqual(buzz.scans[0], delivered - 300)

    def test_a_signal_older_than_the_thirty_day_cap_is_never_read_but_one_on_the_cap_is(self):
        """L1-PTS-077B 投递早于 now-30 天：since 封顶为 now-30 天（不是 delivered-300，也不从 delivered 起算）；封顶线上的信号读得到并生效，早 1 秒的读不到（文字与 reaction 各一组）。"""
        cap = NOW - 30 * DAY
        delivered = NOW - 40 * DAY
        for kind in ("text", "reaction"):
            for at, accepted in ((cap - 1, False), (cap, True)):
                with self.subTest(kind=kind, offset_from_cap=at - cap):
                    gitlab, buzz = self.one_awaiting(delivered)
                    self.signal(buzz, kind, at)
                    self.run_sync(gitlab, buzz)
                    self.assertEqual((buzz.scans, buzz.reaction_scans), ([cap], [cap]))
                    self.assertEqual(gitlab.mark_calls, [1] if accepted else [])

    def test_with_several_awaiting_todos_the_scan_starts_300_seconds_before_the_oldest_delivery(self):
        """L1-PTS-076D 多个 ACKED 待办：since 取最早的 delivered_at-300（与记录顺序无关）；只数 ACKED，更老的 DONE/RESOLVED 记录不拉早窗口；最老那条待办在 D1-300 的信号也读得到。"""
        oldest, newer = NOW - 5000, NOW - 2000
        for label, records in (
            ("oldest first", {"1": make_record(1, delivered_at=oldest), "2": make_record(2, delivered_at=newer)}),
            ("oldest last", {"2": make_record(2, delivered_at=newer), "1": make_record(1, delivered_at=oldest)}),
            ("older non-awaiting ignored", {"2": make_record(2, delivered_at=newer), "1": make_record(1, delivered_at=oldest),
                                            "3": make_record(3, "DONE", delivered_at=NOW - 9000),
                                            "4": make_record(4, "RESOLVED", delivered_at=NOW - 9500)}),
        ):
            for kind in ("text", "reaction"):
                with self.subTest(label=label, kind=kind):
                    shutil.rmtree(self.state, ignore_errors=True)
                    self.write_state(records)
                    gitlab, buzz = FakeGitLab([make_todo(n, iid=n) for n in (1, 2)]), FakeBuzz()
                    self.signal(buzz, kind, oldest - 300)
                    self.run_sync(gitlab, buzz)
                    self.assertEqual((buzz.scans, buzz.reaction_scans), ([oldest - 300], [oldest - 300]))
                    self.assertEqual(gitlab.mark_calls, [1])


class ResolveAndOrderTest(Harness):
    def test_acked_todo_gone_from_pending_becomes_resolved_and_is_never_scanned_again(self):
        """L1-PTS-070 ACKED 的 todo 已不在 pending（人在 GitLab 里处理了）→ RESOLVED，并记 resolved_at；此后不再为它扫描频道。"""
        gitlab, buzz = FakeGitLab([make_todo(1), make_todo(2, iid=46)]), FakeBuzz()
        self.run_sync(gitlab, buzz)
        gitlab.todos = [make_todo(2, iid=46)]
        buzz.scans.clear()
        result = self.run_sync(gitlab, buzz, clock=lambda: NOW + 600)
        todos = self.state_json()["todos"]
        self.assertEqual((todos["1"]["state"], todos["2"]["state"]), ("RESOLVED", "ACKED"))
        self.assertEqual(todos["1"]["resolved_at"], NOW + 600)
        self.assertEqual(result["resolved"], 1)
        gitlab.todos = []
        buzz.scans.clear()
        result = self.run_sync(gitlab, buzz, clock=lambda: NOW + 1200)
        self.assertEqual(self.state_json()["todos"]["2"]["state"], "RESOLVED")
        self.assertEqual(buzz.scans, [])
        self.assertEqual(result["resolved"], 1)
        result = self.run_sync(gitlab, buzz, clock=lambda: NOW + 1800)
        self.assertEqual((buzz.scans, result["resolved"]), ([], 0))

    def resolved_then_listed_again(self):
        gitlab, buzz = FakeGitLab([make_todo(1)]), FakeBuzz()
        self.run_sync(gitlab, buzz)
        root = buzz.sent[0]["id"]
        gitlab.todos = []
        self.run_sync(gitlab, buzz, clock=lambda: NOW + 600)
        self.assertEqual(self.state_json()["todos"]["1"]["state"], "RESOLVED")
        gitlab.todos = [make_todo(1)]  # restored in GitLab: pending again
        return gitlab, buzz, root

    def test_a_resolved_todo_that_is_pending_again_goes_back_to_acked_without_a_resend(self):
        """L1-PTS-071 RESOLVED 不是终点：同一 id 又出现在 pending 列表里，退回 ACKED（清掉 resolved_at）；同一轮和之后都不重发。"""
        gitlab, buzz, root = self.resolved_then_listed_again()
        result = self.run_sync(gitlab, buzz, clock=lambda: NOW + 1200)
        rec = self.state_json()["todos"]["1"]
        self.assertEqual(rec["state"], "ACKED")
        self.assertNotIn("resolved_at", rec)
        self.assertEqual((len(buzz.sent), result["delivered"], result["resolved"]), (1, 0, 0))
        self.assertEqual(rec["event_id"], root)
        self.run_sync(gitlab, buzz, clock=lambda: NOW + 1800)
        self.assertEqual(len(buzz.sent), 1)

    def test_a_revived_todo_can_still_be_marked_done_by_a_trusted_marker(self):
        """L1-PTS-071B 退回 ACKED 之后 todo:done 标记照常生效：mark_as_done 只调一次、转 DONE（RESOLVED 期间标记不生效，回来后才生效）。"""
        gitlab, buzz, root = self.resolved_then_listed_again()
        self.run_sync(gitlab, buzz, clock=lambda: NOW + 1200)
        self.assertEqual(gitlab.marked, [])
        buzz.reply(OWNER, "todo:done:1", root, at=NOW + 1300)
        self.run_sync(gitlab, buzz, clock=lambda: NOW + 1500)
        self.assertEqual(gitlab.marked, [1])
        self.assertEqual(self.state_json()["todos"]["1"]["state"], "DONE")

    def test_a_marker_already_waiting_is_applied_in_the_round_that_revives_the_todo(self):
        """L1-PTS-071C 顺序：收敛（RESOLVED→ACKED）在标记扫描之前，所以回来的那一轮就能处理已经在频道里的标记。"""
        gitlab, buzz, root = self.resolved_then_listed_again()
        buzz.reply(OWNER, "todo:done:1", root, at=NOW + 700)
        self.run_sync(gitlab, buzz, clock=lambda: NOW + 1200)
        self.assertEqual(gitlab.marked, [1])
        self.assertEqual(len(buzz.sent), 1)

    def test_a_truncated_list_still_revives_a_resolved_todo_it_contains(self):
        """L1-PTS-071D 被截断的列表不能证明「缺席」，但能证明「在场」：列表里出现的 RESOLVED 照样退回 ACKED（不做的话它的完成标记会被永远忽略）。"""
        gitlab, buzz, _ = self.resolved_then_listed_again()
        gitlab.truncated = True
        self.run_sync(gitlab, buzz, clock=lambda: NOW + 1200)
        self.assertEqual(self.state_json()["todos"]["1"]["state"], "ACKED")

    def test_truncated_pending_list_skips_the_resolved_convergence(self):
        """L1-PTS-072 pending 被截断时不做 RESOLVED 收敛：列表里缺一个 id 不代表它已被处理。"""
        gitlab, buzz = FakeGitLab([make_todo(1)]), FakeBuzz()
        self.run_sync(gitlab, buzz)
        gitlab.todos, gitlab.truncated = [], True
        result = self.run_sync(gitlab, buzz)
        self.assertEqual(self.state_json()["todos"]["1"]["state"], "ACKED")
        self.assertEqual((result["resolved"], result["truncated"]), (0, True))
        gitlab.truncated = False
        result = self.run_sync(gitlab, buzz)
        self.assertEqual(self.state_json()["todos"]["1"]["state"], "RESOLVED")
        self.assertEqual((result["resolved"], result["truncated"]), (1, False))

    def test_pending_todos_are_fetched_before_any_channel_scan(self):
        """L1-PTS-073 顺序：门禁 → 取 pending → 对账/标记（扫 Buzz）→ 投递。先取 pending，扫描窗口才不含已 RESOLVED 的。"""
        log: list[str] = []
        gitlab, buzz = FakeGitLab([make_todo(1)], log=log), FakeBuzz(log=log)
        self.run_sync(gitlab, buzz)
        buzz.reply(OWNER, "todo:done:1", buzz.sent[0]["id"])
        gitlab.todos.append(make_todo(2, iid=46))
        log.clear()
        self.run_sync(gitlab, buzz)
        self.assertLess(log.index("user"), log.index("members"))
        self.assertLess(log.index("members"), log.index("fetch"))
        self.assertLess(log.index("fetch"), log.index("scan"))
        self.assertLess(log.index("scan"), log.index("send"))

    def test_prune_drops_old_done_and_resolved_but_keeps_the_rest(self):
        """L1-PTS-074 DONE 与 RESOLVED 超过 90 天删除；ACKED、REJECTED（要人工处理）、未到期的保留。"""
        old, edge, fresh = NOW - 91 * DAY, NOW - 90 * DAY, NOW - 89 * DAY
        self.write_state({
            "1": make_record(1, "DONE", done_at=old), "2": make_record(2, "DONE", done_at=fresh),
            "3": make_record(3, "RESOLVED", resolved_at=old), "4": make_record(4, "RESOLVED", resolved_at=fresh),
            "5": make_record(5, "ACKED", delivered_at=old), "6": make_record(6, "REJECTED", rejected_at=old),
            "7": make_record(7, "DONE", done_at=edge),
        })
        self.run_sync(FakeGitLab([make_todo(5)]), FakeBuzz())
        self.assertEqual(sorted(self.state_json()["todos"]), ["2", "4", "5", "6", "7"])


def reaction_event(index, created, emoji="✅", to=None, **extra):
    """A kind 7 event the way the relay returns it: one `e` tag, no `h`, no `p`."""

    return {"id": f"{index:064x}", "kind": 7, "pubkey": OWNER, "content": emoji, "created_at": created,
            "tags": [["e", to or f"{0xabc:064x}"]], **extra}


class TodoBuzzTest(Harness):
    """`TodoBuzz.channel_reactions`, exercised through the real `make_buzz` with a fake `runner` (no relay is contacted)."""

    def buzz_with(self, respond):
        calls = []

        def runner(argv, **kwargs):
            calls.append(list(argv[1:]))
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(respond(calls[-1], len(calls))), stderr="")

        env = {"GITLAB_TODO_TOKEN": PAT, "BUZZ_PRIVATE_KEY": "k" * 8, "BUZZ_RELAY_URL": "wss://relay.test", "PATH": "/bin"}
        with mock.patch.object(sync, "validate_buzz_cli_path", return_value=Path("/opt/buzz/usr/bin/buzz")):
            return self.mod.make_buzz(make_config(), env, runner=runner), calls

    def test_make_buzz_returns_a_buzz_cli_that_can_read_reactions(self):
        """L1-PTS-140 make_buzz 返回 TodoBuzz（仍是 BuzzCli 的子类，channel_members 等照旧），带 channel_reactions；runner 注入方式不变。"""
        buzz, _ = self.buzz_with(lambda args, n: [{"pubkey": OWNER, "role": "owner"}])
        self.assertIsInstance(buzz, self.mod.TodoBuzz)
        self.assertIsInstance(buzz, sync.BuzzCli)
        self.assertTrue(callable(getattr(buzz, "channel_reactions", None)))
        self.assertEqual(buzz.channel_members(), {OWNER: "owner"})

    def test_the_command_reads_kind_7_from_the_configured_channel_since_the_given_second(self):
        """L1-PTS-141 命令是 `messages get --channel <CH> --kinds 7 --since <s> --limit 200`（首页没有 --before；since 取整）；只返回 kind 7 事件。"""
        buzz, calls = self.buzz_with(lambda args, n: [reaction_event(1, 2000)])
        result = buzz.channel_reactions(1000.9)
        self.assertEqual(calls, [["messages", "get", "--channel", CHANNEL, "--kinds", "7", "--since", "1000", "--limit", "200"]])
        self.assertEqual([e["id"] for e in result], [f"{1:064x}"])

    def test_reactions_have_no_h_tag_so_nothing_is_filtered_by_channel_tag_and_only_kind_7_is_returned(self):
        """L1-PTS-142 relay 返回的 reaction 只有一个 e tag、没有 h tag：不按 h tag 过滤（哪怕带了别的 h）；返回值只含 kind 7，混进来的 kind 9、缺 kind 的事件被丢掉。"""
        page = [reaction_event(1, 2000), reaction_event(2, 2001, tags=[["e", "f" * 64], ["h", "another-channel"]]),
                reaction_event(3, 2002, tags=[["e", "f" * 64], ["h", CHANNEL]]),
                reaction_event(4, 2003, kind=9, content="todo:done:1"),
                {k: v for k, v in reaction_event(5, 2004).items() if k != "kind"}]
        buzz, _ = self.buzz_with(lambda args, n: page)
        self.assertEqual(sorted(e["id"] for e in buzz.channel_reactions(1000)), [f"{n:064x}" for n in (1, 2, 3)])

    def test_the_answer_may_be_wrapped_the_way_channel_messages_tolerates(self):
        """L1-PTS-142B 与 channel_messages 一样，事件从返回 JSON 里按「有 id/pubkey/content/tags」找出来，不要求最外层就是列表。"""
        buzz, _ = self.buzz_with(lambda args, n: {"messages": [reaction_event(1, 2000), reaction_event(2, 2001)]})
        self.assertEqual(len(buzz.channel_reactions(1000)), 2)

    def test_full_pages_are_followed_backwards_by_the_before_cursor_and_deduplicated(self):
        """L1-PTS-143 满页（200）→ 用本页最小 created_at 作 --before 翻下一页（--before 是闭区间，所以按 id 去重）；不满页即止；没有结果上限。"""
        page1 = [reaction_event(i + 1, 2000 + i) for i in range(200)]
        page2 = [reaction_event(1, 2000)] + [reaction_event(1000 + i, 1950 + i) for i in range(49)]
        buzz, calls = self.buzz_with(lambda args, n: page2 if "--before" in args else page1)
        self.assertEqual(len(buzz.channel_reactions(1000)), 249)
        self.assertEqual(len(calls), 2)
        self.assertNotIn("--before", calls[0])
        self.assertEqual(calls[1][calls[1].index("--before") + 1], "2000")
        for call in calls:
            self.assertEqual(call[:9], ["messages", "get", "--channel", CHANNEL, "--kinds", "7", "--since", "1000", "--limit"])
            self.assertEqual(call[9], "200")

    def test_a_short_first_page_is_the_only_page(self):
        """L1-PTS-143B 首页不满 200 条就不再翻页。"""
        buzz, calls = self.buzz_with(lambda args, n: [reaction_event(i + 1, 2000 + i) for i in range(199)])
        self.assertEqual(len(buzz.channel_reactions(1000)), 199)
        self.assertEqual(len(calls), 1)

    def test_more_than_one_page_in_a_single_second_fails_closed(self):
        """L1-PTS-144 一个整页全在同一秒、翻下一页什么新的也没有 → 报「同一秒多于一页」，不静默丢 reaction。"""
        page = [reaction_event(i + 1, 3000) for i in range(200)]
        buzz, calls = self.buzz_with(lambda args, n: page)
        with self.assertRaisesRegex(sync.SyncError, "same-second"):
            buzz.channel_reactions(1000)
        self.assertEqual(len(calls), 2)

    def test_the_page_limit_is_the_shared_constant_and_exceeding_it_fails_closed(self):
        """L1-PTS-145 页数上限沿用 CHANNEL_PAGE_MAX：一直有新的整页时，读满这么多页后报「超过页数上限」。"""
        buzz, calls = self.buzz_with(
            lambda args, n: [reaction_event(n * 1000 + i, 900_000 - n * 300 + i) for i in range(200)])
        with self.assertRaisesRegex(sync.SyncError, "page limit"):
            buzz.channel_reactions(1000)
        self.assertEqual(len(calls), sync.CHANNEL_PAGE_MAX)

    def test_a_full_page_without_created_at_cannot_be_paged_and_fails_closed(self):
        """L1-PTS-146 整页里有事件缺 created_at（或不是正整数）→ 没法翻页，报错而不是猜。"""
        for bad in (None, "2000", 0, -1, 2000.5, True):
            with self.subTest(created_at=bad):
                page = [reaction_event(i + 1, 2000 + i) for i in range(199)] + [reaction_event(999, bad)]
                buzz, _ = self.buzz_with(lambda args, n: page)
                with self.assertRaisesRegex(sync.SyncError, "created_at"):
                    buzz.channel_reactions(1000)

    def test_a_cli_failure_is_a_sync_error_like_any_other_buzz_read(self):
        """L1-PTS-147 CLI 非零退出 → 抛 SyncError（BuzzCliError），由 mark_done 阶段按既有方式暂存、末尾抛出。"""
        def runner(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 2, stdout="", stderr="boom")

        env = {"GITLAB_TODO_TOKEN": PAT, "BUZZ_PRIVATE_KEY": "k" * 8, "BUZZ_RELAY_URL": "wss://relay.test"}
        with mock.patch.object(sync, "validate_buzz_cli_path", return_value=Path("/opt/buzz/usr/bin/buzz")):
            buzz = self.mod.make_buzz(make_config(), env, runner=runner)
        with self.assertRaises(sync.SyncError):
            buzz.channel_reactions(1000)


class EntrypointTest(Harness):
    def config_file(self, **over) -> Path:
        cfg = make_config(publisher_pubkey=REAL_PUBLISHER, state_dir=str(self.state), **over)
        path = Path(self.tmp.name) / "todo.json"
        path.write_text(json.dumps(cfg))
        path.chmod(0o600)
        return path

    def env(self, **over):
        return {"GITLAB_TODO_CONFIG": str(self.config_file()), "GITLAB_TODO_TOKEN": PAT,
                "BUZZ_PRIVATE_KEY": PUBLISHER_KEY, **over}

    def call_main(self, env, gitlab=None, buzz=None):
        out = io.StringIO()
        gitlab = gitlab or FakeGitLab([make_todo(1)])
        buzz = buzz or FakeBuzz(publisher=REAL_PUBLISHER)
        with mock.patch.object(self.mod, "TodoGitLab", lambda config, env: gitlab), \
                mock.patch.object(self.mod, "make_buzz", lambda config, env, **kw: buzz):
            code = self.mod.main([], env=env, stdout=out)
        return code, out.getvalue(), buzz

    def test_main_requires_the_config_env_and_takes_no_arguments(self):
        """L1-PTS-050 入口零参数、只读 GITLAB_TODO_CONFIG；缺失时 rc 2 且输出是指明缺哪个变量的 JSON。"""
        out = io.StringIO()
        code = self.mod.main([], env={"GITLAB_TODO_TOKEN": PAT}, stdout=out)
        self.assertEqual(code, 2)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["status"], "error")
        self.assertIn("GITLAB_TODO_CONFIG", payload["error"])
        with self.assertRaises(SystemExit):
            self.mod.main(["--config", "x"], env={})

    def test_main_success_prints_one_json_line_and_exits_zero(self):
        """L1-PTS-050B 成功：rc 0，stdout 恰好一行 JSON，status ok，且真的发了消息。"""
        code, out, buzz = self.call_main(self.env())
        self.assertEqual(code, 0)
        self.assertEqual(out.count("\n"), 1)
        payload = json.loads(out)
        self.assertEqual((payload["status"], payload["delivered"]), ("ok", 1))
        self.assertEqual(len(buzz.sent), 1)
        self.assertNotIn(PAT, out)

    def test_main_reports_locked_with_exit_zero(self):
        """L1-PTS-050C 撞锁：rc 0，status locked，不发。"""
        with self.mod.state_lock(self.state):
            code, out, buzz = self.call_main(self.env())
        self.assertEqual((code, json.loads(out)), (0, {"status": "locked"}))
        self.assertEqual(buzz.sent, [])

    def test_main_rejects_a_private_key_that_does_not_match_the_publisher(self):
        """L1-PTS-050D BUZZ_PRIVATE_KEY 派生出的公钥与 config.publisher_pubkey 不一致：rc 1，不发。"""
        wrong = "02" * 32
        code, out, buzz = self.call_main(self.env(BUZZ_PRIVATE_KEY=wrong))
        self.assertEqual(code, 1)
        payload = json.loads(out)
        self.assertEqual(payload["status"], "error")
        self.assertIn("publisher_pubkey", payload["error"])
        self.assertNotIn(wrong, out)
        self.assertEqual(buzz.sent, [])
        code, out, _ = self.call_main(self.env(BUZZ_PRIVATE_KEY="not-a-key"))
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["status"], "error")

    def test_main_sync_error_exits_one_and_redacts_the_pat_from_the_error_text(self):
        """L1-PTS-051 SyncError：rc 1、JSON 错误；错误文本里真的带着 PAT 时输出也不得含它（含 JSON 转义后的形态）。"""
        for token in (PAT, ODD_PAT):
            gitlab = FakeGitLab()
            gitlab.user = lambda token=token: (_ for _ in ()).throw(sync.SyncError(f"boom {token} boom"))
            env = self.env(GITLAB_TODO_TOKEN=token)
            code, out, _ = self.call_main(env, gitlab=gitlab)
            with self.subTest(token=token):
                self.assertEqual(code, 1)
                payload = json.loads(out)
                self.assertEqual(payload["status"], "error")
                self.assertIn("boom", payload["error"])
                self.assertNotIn(token, out)
                self.assertNotIn(json.dumps(token, ensure_ascii=False)[1:-1], out)
                self.assertNotIn(token, payload["error"])
                self.assertIn("***", payload["error"])

    def test_main_redacts_the_publisher_private_key_from_error_text(self):
        """L1-PTS-051D 错误文本里真的带着 BUZZ_PRIVATE_KEY 时，输出也不得含它（含 JSON 转义后的形态）。"""
        gitlab = FakeGitLab()
        gitlab.user = lambda: (_ for _ in ()).throw(sync.SyncError(f"boom {PUBLISHER_KEY} boom"))
        code, out, _ = self.call_main(self.env(), gitlab=gitlab)
        self.assertEqual(code, 1)
        payload = json.loads(out)
        self.assertNotIn(PUBLISHER_KEY, out)
        self.assertEqual(payload["error"], "boom *** boom")

    def test_main_non_utf8_config_exits_one_with_json(self):
        """L1-PTS-051E 配置文件含非 UTF-8 字节：rc 1 与脱敏 JSON，不带 traceback 逃出去。"""
        env = self.env()
        Path(env["GITLAB_TODO_CONFIG"]).write_bytes(b'{"version": 1, "note": "\xff\xfe"}')
        code, out, buzz = self.call_main(env)
        self.assertEqual(code, 1)
        payload = json.loads(out)
        self.assertEqual(payload["status"], "error")
        self.assertNotIn("xff", out.lower())
        self.assertEqual(buzz.sent, [])

    def test_main_non_utf8_state_exits_one_with_json(self):
        """L1-PTS-051F state 文件含非 UTF-8 字节：rc 1 与 JSON；文件原样保留，不重发任何待办。"""
        raw = b"\xff\xfe\x00garbage"
        self.write_state_bytes(raw)
        code, out, buzz = self.call_main(self.env())
        self.assertEqual((code, json.loads(out)["status"]), (1, "error"))
        self.assertEqual((self.state / "todo-state.json").read_bytes(), raw)
        self.assertEqual(buzz.sent, [])

    def test_main_absurdly_nested_config_exits_one_with_json(self):
        """L1-PTS-051G 深度嵌套的配置 JSON（json 抛 RecursionError）：rc 1 与 JSON，不带 traceback。"""
        env = self.env()
        Path(env["GITLAB_TODO_CONFIG"]).write_text("[" * 200000)
        code, out, buzz = self.call_main(env)
        self.assertEqual((code, json.loads(out)["status"]), (1, "error"))
        self.assertEqual(buzz.sent, [])

    def test_main_a_nul_in_state_dir_exits_one_with_json(self):
        """L1-PTS-051H state_dir 里的 NUL（os 抛 ValueError: embedded null byte）：rc 1 与 JSON，不带 traceback，不发。"""
        env = self.env()
        cfg = make_config(publisher_pubkey=REAL_PUBLISHER, state_dir=str(self.state) + "\x00x")
        Path(env["GITLAB_TODO_CONFIG"]).write_text(json.dumps(cfg))
        code, out, buzz = self.call_main(env)
        self.assertEqual((code, json.loads(out)["status"]), (1, "error"))
        self.assertEqual(buzz.sent, [])

    def test_main_os_error_and_http_exceptions_exit_one_with_json(self):
        """L1-PTS-051B OSError 与 http.client.HTTPException 不带 traceback 逃出：rc 1，仍是脱敏 JSON。"""
        for exc in (OSError(f"disk {PAT}"), PermissionError(f"denied {PAT}"),
                    http.client.IncompleteRead(PAT.encode()), http.client.BadStatusLine(PAT)):
            gitlab = FakeGitLab()
            gitlab.user = lambda exc=exc: (_ for _ in ()).throw(exc)
            code, out, _ = self.call_main(self.env(), gitlab=gitlab)
            with self.subTest(type(exc).__name__):
                self.assertEqual(code, 1)
                self.assertEqual(json.loads(out)["status"], "error")
                self.assertNotIn(PAT, out)

    def test_main_config_errors_exit_one_with_json(self):
        """L1-PTS-051C 配置文件权限不对或缺 state_dir：rc 1、JSON。"""
        env = self.env()
        Path(env["GITLAB_TODO_CONFIG"]).chmod(0o644)
        code, out, _ = self.call_main(env)
        self.assertEqual((code, json.loads(out)["status"]), (1, "error"))
        cfg = make_config(publisher_pubkey=REAL_PUBLISHER)
        path = Path(self.tmp.name) / "nostate.json"
        path.write_text(json.dumps(cfg))
        path.chmod(0o600)
        code, out, _ = self.call_main({**env, "GITLAB_TODO_CONFIG": str(path)})
        self.assertEqual(code, 1)
        self.assertIn("state_dir", json.loads(out)["error"])

    def test_make_buzz_gives_the_cli_child_an_env_without_the_owner_pat(self):
        """L1-PTS-052 生产路径 make_buzz → BuzzCli → child_env：Buzz CLI 子进程拿不到人的 PAT，也拿不到无关密钥。"""
        seen = []

        def runner(argv, **kwargs):
            seen.append(kwargs["env"])
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(
                [{"pubkey": OWNER, "role": "owner"}]), stderr="")

        env = {"GITLAB_TODO_TOKEN": PAT, "BUZZ_PRIVATE_KEY": "k" * 8, "BUZZ_RELAY_URL": "wss://relay.test",
               "BUZZ_AUTH_TAG": "tag", "HOME": "/h", "PATH": "/bin", "OTHER_SECRET": "x", "GITLAB_TOKEN": "agent"}
        with mock.patch.object(sync, "validate_buzz_cli_path", return_value=Path("/opt/buzz/usr/bin/buzz")):
            buzz = self.mod.make_buzz(make_config(), env, runner=runner)
            self.assertEqual(buzz.channel_members(), {OWNER: "owner"})
        (child,) = seen
        for secret in ("GITLAB_TODO_TOKEN", "OTHER_SECRET", "GITLAB_TOKEN"):
            self.assertNotIn(secret, child)
        self.assertNotIn(PAT, child.values())
        self.assertEqual(child["BUZZ_PRIVATE_KEY"], "k" * 8)
        self.assertEqual(child["BUZZ_RELAY_URL"], "wss://relay.test")

    def test_concurrent_run_reports_locked(self):
        """L1-PTS-053 同一 state 目录同时只跑一轮，撞锁返回 locked、不发不写。"""
        gitlab, buzz = FakeGitLab([make_todo(1)]), FakeBuzz()
        with self.mod.state_lock(self.state):
            result = self.run_sync(gitlab, buzz)
        self.assertEqual(result["status"], "locked")
        self.assertEqual(buzz.sent, [])


if __name__ == "__main__":
    unittest.main()
