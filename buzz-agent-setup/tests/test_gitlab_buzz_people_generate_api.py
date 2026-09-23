"""The people generator's bridge-API mode: emails come from the bridge's signed, per-channel endpoint.

GET {base_url}/bind/api/channels/{channel}/people, NIP-98 signed by a channel owner/admin's Buzz key, answers
{"channel", "as_of", "people", "union_ids", "emails"} (emails only when the bridge turned
CHANNEL_PEOPLE_EMAILS_ENABLED on). The generator joins those emails with the project's GitLab usernames exactly
as it joins the `ops export-people` file, and fails closed (no file touched) on anything it cannot trust.

Test IDs are L1-GIS-PA-nnn (people, API mode). The bridge is an in-process fake that really verifies the NIP-98
header; GitLab is a local HTTP server. Nothing here talks to a real bridge, relay or Feishu.
"""

import base64
import contextlib
import functools
import hashlib
import http.server
import importlib
import importlib.util
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

TESTS = Path(__file__).resolve().parent
SCRIPTS = TESTS.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


MODULE = _load("gitlab_buzz_people_generate_api_under_test", "gitlab_buzz_people_generate.py")
NK = importlib.import_module("gitlab_buzz_sync").nk
SYNC = importlib.import_module("gitlab_buzz_sync")

# Pure-Python BIP-340 costs about 0.12 s to sign and 0.14 s to verify; every API call does both. The functions are
# pure, so cache them and pin the nonce randomness (as the feishu sync tests do) to keep the suite quick.
_MEMO = []


def setUpModule():
    for name in ("pubkey_xonly", "schnorr_sign", "schnorr_verify"):
        _MEMO.append(mock.patch.object(NK, name, functools.lru_cache(maxsize=None)(getattr(NK, name))))
    _MEMO.append(mock.patch("secrets.token_bytes", lambda n: bytes(n)))
    for patch in _MEMO:
        patch.start()


def tearDownModule():
    while _MEMO:
        _MEMO.pop().stop()


KEY_A = "11" * 32
KEY_B = "22" * 32
KEY_C = "33" * 32
AGENT_1 = "aa" * 32
AGENT_2 = "cc" * 32
PUBLISHER = "bb" * 32

CHAN_1 = "0b6bb6ce-2a10-4c3f-8f0a-2ff59d4f7a8e"
CHAN_2 = "7d10832a-0d23-4486-82db-7aa51b97e5f4"
ORIGIN = "https://bridge.example.test"
OWNER_KEY = "c2" * 32
OWNER_PK = NK.pubkey_xonly(bytes.fromhex(OWNER_KEY)).hex()
OWNER_NSEC = NK.bech32_encode("nsec", bytes.fromhex(OWNER_KEY))
BODY_MARKER = "SECRET-BODY-MARKER"
NOW0 = datetime(2026, 9, 20, 12, 0, 0, tzinfo=timezone.utc)
MAX_BYTES = 1 << 20


def people_url(channel, origin=ORIGIN):
    return f"{origin}/bind/api/channels/{channel}/people"


class Clock:
    """The generator's `now`: every call is a new second, so a header signed for an earlier request cannot pass
    for a later one."""

    def __init__(self):
        self.last = NOW0 - timedelta(seconds=1)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        self.last = self.last + timedelta(seconds=1)
        return self.last


class FakeBridge:
    """GET /bind/api/channels/{channel}/people as the bridge serves it: it really verifies the NIP-98 header
    (kind, u and method tags, BIP-340 signature, the clock) and only answers a channel owner or admin."""

    def __init__(self, clock):
        self.clock = clock
        self.channels = {}
        self.override = {}
        self.requests = []

    def set_channel(self, channel, email_to_pubkey, roles=None, with_emails=True):
        by_key = {}
        for address, pubkey in email_to_pubkey.items():
            by_key.setdefault(pubkey, []).append(address)
        self.channels[channel] = {
            "roles": {OWNER_PK: "owner"} if roles is None else dict(roles),
            "keys": sorted(by_key),
            "emails": {pk: sorted(set(v)) for pk, v in by_key.items()} if with_emails else None,
        }

    def http_get(self, url, headers, timeout):
        self.requests.append({"url": url, "headers": dict(headers), "timeout": timeout})
        channel = url.split("/channels/", 1)[1].split("/", 1)[0] if "/channels/" in url else ""
        over = self.override.get(channel)
        if over and over[0] == "raise":
            raise over[1]
        if over and over[0] == "raw":
            return over[1], over[2]
        status, body = self._serve(channel, url, headers)
        if over and over[0] == "doc" and status == 200:
            out = over[1](json.loads(body))
            return 200, out if isinstance(out, bytes) else json.dumps(out).encode()
        return status, body

    def _serve(self, channel, url, headers):
        not_found = (404, b'{"error":"not_found"}')
        info = self.channels.get(channel)
        if info is None or url != people_url(channel):
            return not_found
        signer = self._verify_nip98(headers.get("Authorization", ""), "GET", url)
        if signer is None:
            return 401, b'{"error":"invalid_signature"}'
        if info["roles"].get(signer) not in ("owner", "admin"):
            return not_found
        doc = {
            "channel": channel,
            "as_of": self.clock.last.isoformat().replace("+00:00", "Z"),
            "people": {pk: "ou_" + pk[:8] for pk in info["keys"]},
            "union_ids": {pk: "on_" + pk[:8] for pk in info["keys"]},
        }
        if info["emails"] is not None:
            doc["emails"] = info["emails"]
        return 200, json.dumps(doc).encode()

    def _verify_nip98(self, header, method, url):
        if not header.startswith("Nostr ") or len(header) > 8192 or any(c in header[6:] for c in " \t\r\n"):
            return None
        try:
            ev = json.loads(base64.b64decode(header[6:], validate=True))
            if set(ev) != {"id", "pubkey", "created_at", "kind", "tags", "content", "sig"}:
                return None
            body = json.dumps([0, ev["pubkey"], ev["created_at"], ev["kind"], ev["tags"], ev["content"]],
                              separators=(",", ":"), ensure_ascii=False)
            if hashlib.sha256(body.encode()).hexdigest() != ev["id"]:
                return None
            if not NK.schnorr_verify(bytes.fromhex(ev["id"]), bytes.fromhex(ev["pubkey"]), bytes.fromhex(ev["sig"])):
                return None
            us = [t[1] for t in ev["tags"] if t[0] == "u"]
            ms = [t[1] for t in ev["tags"] if t[0] == "method"]
            if ev["kind"] != 27235 or us != [url] or [m.upper() for m in ms] != [method] or ev["content"] != "":
                return None
            # The signature must be for *this* request: the second the generator read from its clock just now.
            if ev["created_at"] != int(self.clock.last.timestamp()):
                return None
            return ev["pubkey"]
        except (ValueError, KeyError, TypeError, IndexError):
            return None


class FakeGitLab(threading.Thread):
    """members/all per project id; records every request path and headers."""

    def __init__(self, members):
        super().__init__(daemon=True)
        self.members = {int(pid): list(names) for pid, names in members.items()}
        self.requests = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                outer.requests.append((self.path, {k: v for k, v in self.headers.items()}))
                parts = urlsplit(self.path)
                segments = parts.path.split("/")
                if len(segments) < 7 or segments[3] != "projects" or segments[5:7] != ["members", "all"]:
                    self.send_error(404)
                    return
                names = outer.members.get(int(segments[4]))
                if names is None:
                    self.send_error(404)
                    return
                page = int(parse_qs(parts.query).get("page", ["1"])[0])
                rows = [{"id": i, "username": n} for i, n in enumerate(names[(page - 1) * 100:page * 100], start=1)]
                body = json.dumps(rows).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("x-next-page", str(page + 1) if page * 100 < len(names) else "")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def run(self):
        self.server.serve_forever(poll_interval=0.01)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


class ApiCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.addCleanup(self.tmp.cleanup)
        self.gitlab = FakeGitLab({1312: [], 1313: []})
        self.gitlab.start()
        self.addCleanup(self.gitlab.stop)
        os.environ["GL_TOKEN_A"] = "test-token-a"
        os.environ["GL_TOKEN_B"] = "test-token-b"
        self.addCleanup(lambda: [os.environ.pop(k, None) for k in ("GL_TOKEN_A", "GL_TOKEN_B")])
        self.clock = Clock()
        self.bridge = FakeBridge(self.clock)
        self.signer_env = self.write_private("signer.env", f"BUZZ_PRIVATE_KEY={OWNER_KEY}\n")
        self.api = {"base_url": ORIGIN, "signer_env_file": str(self.signer_env)}
        self.people_file = self.root / "people.json"

    # ---- files --------------------------------------------------------------------------------------
    def write_private(self, name, value, mode=0o600):
        path = self.root / name
        path.write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")
        os.chmod(path, mode)
        return path

    def channel_config(self, name, channel=CHAN_1, projects=(1312,), token_env="GL_TOKEN_A", agents=(AGENT_1,),
                       people=None, **extra):
        value = {
            "channel_id": channel,
            "publisher_pubkey": PUBLISHER,
            "since": "2026-09-13T00:00:00Z",
            "agent_pubkeys": list(agents),
            "people": dict(people or {}),
            "gitlab": {"base_url": self.gitlab.base_url, "token_env": token_env, "bot_user_id": 989,
                       "bot_username": "project_1312_bot_abcdef", "projects": list(projects)},
            "buzz": {"cli_path": "/opt/buzz/buzz", "cli_sha256": "0" * 64},
        }
        value.update(extra)
        return self.write_private(name, value)

    def listing(self):
        """Every file in the working directory except the lock files (a lock is not a result)."""
        return {p.name: p.read_bytes() for p in self.root.iterdir() if p.is_file() and not p.name.endswith(".lock")}

    def shared_content(self):
        return json.loads(self.people_file.read_text(encoding="utf-8"))

    # ---- runs ---------------------------------------------------------------------------------------
    def run_single(self, config, dry_run=False, **kw):
        kw.setdefault("api", self.api)
        kw.setdefault("http", self.bridge.http_get)
        kw.setdefault("now", self.clock)
        return MODULE.run(config, None, "a4x.io", dry_run, **kw)

    def run_shared(self, configs, dry_run=False, people_file=None, **kw):
        kw.setdefault("api", self.api)
        kw.setdefault("http", self.bridge.http_get)
        kw.setdefault("now", self.clock)
        return MODULE.run_shared(list(configs), None, people_file or self.people_file, "a4x.io", dry_run, **kw)

    def call(self, argv, **kw):
        kw.setdefault("http", self.bridge.http_get)
        kw.setdefault("now", self.clock)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = MODULE.main(argv, **kw)
        return code, out.getvalue(), err.getvalue()

    def api_argv(self, *configs, people_file=None):
        argv = []
        for config in configs:
            argv += ["--config", str(config)]
        if people_file is not None:
            argv += ["--people-file", str(people_file)]
        return argv + ["--people-api-base-url", ORIGIN, "--signer-env-file", str(self.signer_env)]

    def assert_fails_closed(self, run, before, *, contains=(), forbids=()):
        """The run raises GenerateError naming the reason, and no file in the working directory changed."""
        with self.assertRaises(MODULE.GenerateError) as ctx:
            run()
        message = str(ctx.exception)
        for needle in contains:
            self.assertIn(needle, message)
        for needle in (OWNER_KEY, OWNER_NSEC, "Nostr ", BODY_MARKER, "@a4x.io", *forbids):
            self.assertNotIn(needle, message)
        self.assertEqual(self.listing(), before)
        return message


class SingleChannelTest(ApiCase):
    def test_emails_of_the_configs_channel_are_joined_with_the_project_members(self):
        """L1-GIS-PA-001 API 模式：GET 该配置 channel_id 的接口恰好一次，emails 按 username@a4x.io 精确匹配写进 people。"""
        self.gitlab.members[1312] = ["jchen", "qwang", "project_1312_bot_deadbeef"]
        self.bridge.set_channel(CHAN_1, {"jchen@a4x.io": KEY_A, "qwang@a4x.io": KEY_B, "outsider@a4x.io": KEY_C})
        config = self.channel_config("config.json")
        result = self.run_single(config)
        self.assertEqual([r["url"] for r in self.bridge.requests], [people_url(CHAN_1)])
        self.assertEqual((result["added"], result["kept_manual"], result["unmapped"]), (2, 0, []))
        self.assertTrue(result["written"])
        self.assertEqual(json.loads(config.read_text())["people"], {"jchen": KEY_A, "qwang": KEY_B})
        self.assertEqual(config.stat().st_mode & 0o777, 0o600)
        self.assertEqual(result["source"], "api")
        self.assertEqual(result["channels"], 1)

    def test_localpart_fallback_and_ambiguity_keep_their_meaning(self):
        """L1-GIS-PA-002 匹配规则不变：username@域名精确匹配、localpart 兜底、歧义即 unmapped，绝不猜。"""
        self.gitlab.members[1312] = ["ali", "sam", "nobody"]
        self.bridge.set_channel(CHAN_1, {"ali@addx.ai": KEY_A, "sam@addx.ai": KEY_B, "sam@addx.live": KEY_C})
        config = self.channel_config("config.json")
        result = self.run_single(config)
        self.assertEqual(result["added"], 1)
        self.assertEqual(result["unmapped"], ["nobody", "sam"])
        self.assertEqual(json.loads(config.read_text())["people"], {"ali": KEY_A})

    def test_the_domain_option_still_picks_the_exact_address(self):
        """L1-GIS-PA-003 --domain 仍决定精确匹配的域名（默认 a4x.io）。"""
        self.gitlab.members[1312] = ["jchen"]
        self.bridge.set_channel(CHAN_1, {"jchen@example.test": KEY_A, "jchen@other.test": KEY_B})
        config = self.channel_config("config.json")
        result = MODULE.run(config, None, "example.test", False, api=self.api, http=self.bridge.http_get,
                            now=self.clock)
        self.assertEqual(result["added"], 1)
        self.assertEqual(json.loads(config.read_text())["people"], {"jchen": KEY_A})

    def test_manual_entries_win_and_nothing_is_deleted(self):
        """L1-GIS-PA-004 手填优先、不删除：同名不同 pubkey 只警告不覆盖；不在任何项目里的旧条目原样保留。"""
        self.gitlab.members[1312] = ["jchen", "qwang"]
        self.bridge.set_channel(CHAN_1, {"jchen@a4x.io": KEY_A, "qwang@a4x.io": KEY_C})
        config = self.channel_config("config.json", people={"qwang": KEY_B, "retired": KEY_C})
        result = self.run_single(config)
        self.assertEqual((result["added"], result["kept_manual"]), (1, 1))
        self.assertEqual(result["warn"], ["qwang: manual entry kept (differs from the bridge)"])
        self.assertEqual(json.loads(config.read_text())["people"], {"qwang": KEY_B, "retired": KEY_C, "jchen": KEY_A})

    def test_desk_and_agent_pubkeys_are_still_refused(self):
        """L1-GIS-PA-005 发布者与 agent 的 pubkey 在 API 模式下同样被拒，且不写进 people。"""
        self.gitlab.members[1312] = ["zlin", "jchen", "ok"]
        self.bridge.set_channel(CHAN_1, {"zlin@a4x.io": AGENT_1, "jchen@a4x.io": PUBLISHER, "ok@a4x.io": KEY_A})
        config = self.channel_config("config.json")
        result = self.run_single(config)
        self.assertEqual((result["added"], result["rejected_agent_key"]), (1, 2))
        self.assertEqual(json.loads(config.read_text())["people"], {"ok": KEY_A})
        blob = json.dumps(result)
        for key in (AGENT_1, PUBLISHER, KEY_A):
            self.assertNotIn(key, blob)

    def test_dry_run_asks_the_bridge_but_writes_nothing(self):
        """L1-GIS-PA-006 --dry-run 照常取接口、报告 changed，但不写任何文件。"""
        self.gitlab.members[1312] = ["jchen"]
        self.bridge.set_channel(CHAN_1, {"jchen@a4x.io": KEY_A})
        config = self.channel_config("config.json")
        before = self.listing()
        result = self.run_single(config, dry_run=True)
        self.assertEqual(len(self.bridge.requests), 1)
        self.assertTrue(result["changed"])
        self.assertFalse(result["written"])
        self.assertEqual(self.listing(), before)

    def test_the_written_config_loads_in_the_sync(self):
        """L1-GIS-PA-007 写出的配置 sync 一定加载得了（humans-only 规则同旧模式）。"""
        self.gitlab.members[1312] = ["jchen", "qwang"]
        self.bridge.set_channel(CHAN_1, {"jchen@a4x.io": KEY_A})
        config = self.channel_config("config.json", people={"qwang": KEY_B})
        self.run_single(config)
        loaded = SYNC.load_config(config)
        with mock.patch.object(SYNC, "validate_buzz_cli_path"):
            SYNC.validate_config(loaded)
        self.assertEqual(loaded["people"], {"qwang": KEY_B, "jchen": KEY_A})

    def test_an_empty_emails_object_maps_nobody_and_changes_nothing(self):
        """L1-GIS-PA-008 频道里暂时没有可映射的人（emails 是 {}）：不算错，全部 unmapped，不写。"""
        self.gitlab.members[1312] = ["jchen"]
        self.bridge.set_channel(CHAN_1, {})
        config = self.channel_config("config.json")
        before = self.listing()
        result = self.run_single(config)
        self.assertEqual((result["added"], result["unmapped"], result["written"]), (0, ["jchen"], False))
        self.assertEqual(self.listing(), before)

    def test_a_channel_with_several_configs_is_asked_once(self):
        """L1-GIS-PA-009 同一频道的多份配置（共享模式）只向接口取一次。"""
        self.gitlab.members = {1312: ["alice"], 1313: ["bob"]}
        self.bridge.set_channel(CHAN_1, {"alice@a4x.io": KEY_A, "bob@a4x.io": KEY_B})
        a = self.channel_config("a.json", projects=(1312,))
        b = self.channel_config("b.json", projects=(1313,), token_env="GL_TOKEN_B")
        result = self.run_shared([a, b])
        self.assertEqual(len(self.bridge.requests), 1)
        self.assertEqual(result["channels"], 1)
        self.assertEqual(self.shared_content(), {"alice": KEY_A, "bob": KEY_B})


class SigningTest(ApiCase):
    def test_each_request_carries_its_own_nip98_signature(self):
        """L1-GIS-PA-010 Authorization 是按本次请求（GET、该频道的 URL、发请求那一秒）签的；每个频道一次，互不通用。"""
        self.gitlab.members = {1312: ["alice"], 1313: ["bob"]}
        self.bridge.set_channel(CHAN_1, {"alice@a4x.io": KEY_A})
        self.bridge.set_channel(CHAN_2, {"bob@a4x.io": KEY_B})
        a = self.channel_config("a.json", CHAN_1, (1312,))
        b = self.channel_config("b.json", CHAN_2, (1313,), token_env="GL_TOKEN_B")
        result = self.run_shared([a, b])  # the fake answers 401 unless every header verifies
        self.assertEqual(result["added"], 2)
        self.assertEqual([r["url"] for r in self.bridge.requests], [people_url(CHAN_1), people_url(CHAN_2)])
        headers = [r["headers"]["Authorization"] for r in self.bridge.requests]
        self.assertNotEqual(headers[0], headers[1])
        events = [json.loads(base64.b64decode(h[6:])) for h in headers]
        self.assertEqual([e["pubkey"] for e in events], [OWNER_PK, OWNER_PK])
        self.assertEqual([e["created_at"] for e in events], [int(NOW0.timestamp()), int(NOW0.timestamp()) + 1])
        for request in self.bridge.requests:
            self.assertEqual(set(request["headers"]), {"Authorization", "Accept"})
            self.assertTrue(0 < request["timeout"] <= 30)

    def test_a_header_for_another_url_or_time_is_refused_by_the_fake(self):
        """L1-GIS-PA-011 对照：伪 bridge 确实会拒绝别的 URL / 过期时间的头（验签不是摆设）。"""
        url = people_url(CHAN_1)
        self.bridge.set_channel(CHAN_1, {"a@a4x.io": KEY_A})
        bridge = importlib.import_module("buzz_feishu_group_sync")
        self.clock()
        good = bridge.nip98_header(OWNER_KEY, "GET", url, self.clock.last)
        self.assertEqual(self.bridge.http_get(url, {"Authorization": good}, 5)[0], 200)
        other = bridge.nip98_header(OWNER_KEY, "GET", people_url(CHAN_2), self.clock.last)
        self.assertEqual(self.bridge.http_get(url, {"Authorization": other}, 5)[0], 401)
        stale = bridge.nip98_header(OWNER_KEY, "GET", url, self.clock.last - timedelta(seconds=1))
        self.assertEqual(self.bridge.http_get(url, {"Authorization": stale}, 5)[0], 401)

    def test_the_signer_key_may_be_hex_or_nsec(self):
        """L1-GIS-PA-012 signer env 文件里的 BUZZ_PRIVATE_KEY 写 hex 或 nsec 都行（含 export 前缀与引号）。"""
        self.gitlab.members[1312] = ["alice"]
        self.bridge.set_channel(CHAN_1, {"alice@a4x.io": KEY_A})
        for label, text in (("nsec", f"BUZZ_PRIVATE_KEY={OWNER_NSEC}\n"),
                            ("export+quotes", f"# comment\nexport BUZZ_PRIVATE_KEY=\"{OWNER_KEY}\"\nOTHER=1\n")):
            with self.subTest(label=label):
                env = self.write_private(f"{label}.env".replace("+", "_"), text)
                config = self.channel_config(f"config-{label}.json".replace("+", "_"))
                result = self.run_single(config, api={"base_url": ORIGIN, "signer_env_file": str(env)})
                self.assertEqual(result["added"], 1)

    def test_a_bad_signer_env_file_is_refused_before_any_request(self):
        """L1-GIS-PA-013 signer env 文件权限过松、是符号链接、缺 key、key 不合法、不存在：都拒绝，不发请求，错误不含 key。"""
        self.gitlab.members[1312] = ["alice"]
        self.bridge.set_channel(CHAN_1, {"alice@a4x.io": KEY_A})
        config = self.channel_config("config.json")
        wide = self.write_private("wide.env", f"BUZZ_PRIVATE_KEY={OWNER_KEY}\n", mode=0o644)
        real = self.write_private("real.env", f"BUZZ_PRIVATE_KEY={OWNER_KEY}\n")
        link = self.root / "link.env"
        link.symlink_to(real)
        no_key = self.write_private("nokey.env", "SOMETHING_ELSE=1\n")
        bad_hex = self.write_private("badhex.env", "BUZZ_PRIVATE_KEY=zz" + "0" * 62 + "\n")
        bad_nsec = self.write_private("badnsec.env", "BUZZ_PRIVATE_KEY=nsec1qqqqqqqq\n")
        zero = self.write_private("zero.env", "BUZZ_PRIVATE_KEY=" + "00" * 32 + "\n")
        cases = {"wide": wide, "symlink": link, "no key": no_key, "bad hex": bad_hex, "bad nsec": bad_nsec,
                 "zero key": zero, "missing": self.root / "missing.env"}
        before = self.listing()
        for label, path in cases.items():
            with self.subTest(label=label):
                self.assert_fails_closed(
                    lambda: self.run_single(config, api={"base_url": ORIGIN, "signer_env_file": str(path)}),
                    before)
                self.assertEqual(self.bridge.requests, [])

    def test_a_malformed_people_api_address_is_refused_before_any_request(self):
        """L1-GIS-PA-014 base_url 必须恰好是 https://主机[:端口]，signer 路径必须是绝对路径；否则不发请求。"""
        self.gitlab.members[1312] = ["alice"]
        self.bridge.set_channel(CHAN_1, {"alice@a4x.io": KEY_A})
        config = self.channel_config("config.json")
        before = self.listing()
        bad_urls = ("http://bridge.example.test", ORIGIN + "/", ORIGIN + "/bind", "https://u:p@bridge.example.test",
                    ORIGIN + "?x=1", ORIGIN + "#f", "ftp://bridge.example.test", "", "bridge.example.test")
        for bad in bad_urls:
            with self.subTest(base_url=bad):
                self.assert_fails_closed(
                    lambda: self.run_single(config, api={"base_url": bad, "signer_env_file": str(self.signer_env)}),
                    before)
        with self.subTest(signer="relative"):
            self.assert_fails_closed(
                lambda: self.run_single(config, api={"base_url": ORIGIN, "signer_env_file": "signer.env"}), before)
        self.assertEqual(self.bridge.requests, [])

    def test_the_default_transport_is_the_feishu_sync_one(self):
        """L1-GIS-PA-015 命令行不注入 http 时走 buzz_feishu_group_sync._http_get（不跟随重定向、不走代理、只认 http(s)、正文有上限）。"""
        self.gitlab.members[1312] = ["alice"]
        self.bridge.set_channel(CHAN_1, {"alice@a4x.io": KEY_A})
        config = self.channel_config("config.json")
        bridge = importlib.import_module("buzz_feishu_group_sync")
        with mock.patch.object(bridge, "_http_get", side_effect=self.bridge.http_get) as transport:
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = MODULE.main(self.api_argv(config), now=self.clock)
        self.assertEqual(code, 0, err.getvalue())
        self.assertEqual([c.args[0] for c in transport.call_args_list], [people_url(CHAN_1)])

    def test_a_config_without_a_usable_channel_id_is_refused(self):
        """L1-GIS-PA-016 配置缺 channel_id 或它不是小写 uuid：API 模式拒绝，不发请求。"""
        self.gitlab.members[1312] = ["alice"]
        self.bridge.set_channel(CHAN_1, {"alice@a4x.io": KEY_A})
        for label, value in (("missing", None), ("not a uuid", "general"), ("upper", CHAN_1.upper()), ("int", 7)):
            with self.subTest(label=label):
                config = self.channel_config("config.json")
                data = json.loads(config.read_text())
                data.pop("channel_id")
                if value is not None:
                    data["channel_id"] = value
                config.write_text(json.dumps(data))
                before = self.listing()
                self.assert_fails_closed(lambda: self.run_single(config), before, contains=("channel_id",))
                self.assertEqual(self.bridge.requests, [])


    def test_exactly_one_email_source_is_required_of_the_functions_too(self):
        """L1-GIS-PA-017 库函数同样要求恰好一个来源（都没有、两个都给都拒绝），不只靠命令行拦。"""
        self.gitlab.members[1312] = ["alice"]
        self.bridge.set_channel(CHAN_1, {"alice@a4x.io": KEY_A})
        config = self.channel_config("config.json")
        export = self.write_private("export.json", {"alice@a4x.io": KEY_A})
        before = self.listing()
        for label, call in (
            ("single, none", lambda: MODULE.run(config, None, "a4x.io", False)),
            ("single, both", lambda: MODULE.run(config, export, "a4x.io", False, api=self.api, http=self.bridge.http_get)),
            ("shared, none", lambda: MODULE.run_shared([config], None, self.people_file, "a4x.io", False)),
            ("shared, both", lambda: MODULE.run_shared([config], export, self.people_file, "a4x.io", False,
                                                       api=self.api, http=self.bridge.http_get)),
        ):
            with self.subTest(label=label):
                self.assert_fails_closed(call, before, contains=("exactly one",))
        self.assertEqual(self.bridge.requests, [])

    def test_the_default_clock_is_the_real_utc_time(self):
        """L1-GIS-PA-018 不注入 now 时，签名用的是真实的当前 UTC 时间（bridge 只认 ±60 秒）。"""
        self.gitlab.members[1312] = ["alice"]
        self.bridge.set_channel(CHAN_1, {"alice@a4x.io": KEY_A})
        config = self.channel_config("config.json")
        seen = []

        def transport(url, headers, timeout):
            seen.append(json.loads(base64.b64decode(headers["Authorization"][6:]))["created_at"])
            return 200, json.dumps({"channel": CHAN_1, "people": {}, "emails": {}}).encode()

        self.run_single(config, http=transport, now=None)
        self.assertEqual(len(seen), 1)
        self.assertLess(abs(seen[0] - time.time()), 10)


    def test_a_local_problem_fails_before_the_bridge_is_asked(self):
        """L1-GIS-PA-019 本地就能发现的问题（缺 GitLab token 环境变量）先于任何 bridge 请求报出，单配置与共享模式都是。"""
        self.gitlab.members = {1312: ["alice"], 1313: ["bob"]}
        self.bridge.set_channel(CHAN_1, {"alice@a4x.io": KEY_A})
        self.bridge.set_channel(CHAN_2, {"bob@a4x.io": KEY_B})
        a = self.channel_config("a.json", CHAN_1, (1312,))
        b = self.channel_config("b.json", CHAN_2, (1313,), token_env="GL_TOKEN_B")
        os.environ.pop("GL_TOKEN_B")
        before = self.listing()
        self.assert_fails_closed(lambda: self.run_shared([a, b]), before, contains=("GL_TOKEN_B",))
        self.assert_fails_closed(lambda: self.run_single(b), before, contains=("GL_TOKEN_B",))
        self.assertEqual(self.bridge.requests, [])


class SecrecyTest(ApiCase):
    def test_the_key_never_reaches_a_child_process_the_environment_or_any_output(self):
        """L1-GIS-PA-020 key 只在本进程里签名：不起子进程、不进 os.environ、不进 GitLab 请求；bridge 收不到 GitLab token；输出里没有 key/头/token/邮箱/pubkey。"""
        self.gitlab.members[1312] = ["alice", "ghost"]
        self.bridge.set_channel(CHAN_1, {"alice@a4x.io": KEY_A})
        config = self.channel_config("config.json")
        env_before = dict(os.environ)
        boom = AssertionError("no child process may be started")
        with mock.patch.object(subprocess, "Popen", side_effect=boom), mock.patch.object(os, "system", side_effect=boom), \
                mock.patch.object(os, "fork", side_effect=boom), mock.patch.object(os, "execv", side_effect=boom), \
                mock.patch.object(os, "posix_spawn", side_effect=boom):
            code, out, err = self.call(self.api_argv(config))
        self.assertEqual(code, 0, err)
        self.assertEqual(dict(os.environ), env_before)
        self.assertFalse(any(OWNER_KEY in v or OWNER_NSEC in v for v in os.environ.values()))
        header = self.bridge.requests[0]["headers"]["Authorization"]
        for text in (out, err):
            for secret in (OWNER_KEY, OWNER_NSEC, header, header[6:], "Nostr ", "test-token-a", KEY_A, "alice@a4x.io",
                           "@a4x.io"):
                self.assertNotIn(secret, text)
        gitlab_headers = [h for _, h in self.gitlab.requests]
        self.assertTrue(gitlab_headers)
        for h in gitlab_headers:
            lowered = {k.lower(): v for k, v in h.items()}
            self.assertEqual(lowered.get("private-token"), "test-token-a")
            self.assertNotIn("authorization", lowered)
        for r in self.bridge.requests:
            self.assertNotIn("private-token", {k.lower() for k in r["headers"]})
            self.assertNotIn("test-token-a", json.dumps(r))

    def test_a_failure_prints_no_response_body_email_or_key(self):
        """L1-GIS-PA-021 失败时 stderr 不回显响应正文、邮箱、key、Authorization 头。"""
        self.gitlab.members[1312] = ["alice"]
        self.bridge.set_channel(CHAN_1, {"alice@a4x.io": KEY_A})
        config = self.channel_config("config.json")
        self.bridge.override[CHAN_1] = ("raw", 500, f'{BODY_MARKER} alice@a4x.io {KEY_A}'.encode())
        code, out, err = self.call(self.api_argv(config))
        self.assertEqual((code, out), (1, ""))
        self.assertIn("people-generate:", err)
        for secret in (BODY_MARKER, "alice@a4x.io", KEY_A, OWNER_KEY, OWNER_NSEC, "Nostr "):
            self.assertNotIn(secret, err)


class FailClosedTest(ApiCase):
    def setUp(self):
        super().setUp()
        self.gitlab.members[1312] = ["alice", "bob"]
        self.bridge.set_channel(CHAN_1, {"alice@a4x.io": KEY_A, "bob@a4x.io": KEY_B})
        self.config = self.channel_config("config.json", people={"carol": KEY_C})
        self.before = self.listing()

    def expect_failure(self, override, *, contains=(), forbids=()):
        self.bridge.override[CHAN_1] = override
        message = self.assert_fails_closed(lambda: self.run_single(self.config), self.before,
                                           contains=(CHAN_1, *contains), forbids=forbids)
        self.bridge.override.pop(CHAN_1)
        return message

    def test_emails_absent_means_the_bridge_switch_is_off(self):
        """L1-GIS-PA-030 响应没有 emails（bridge 没开 CHANNEL_PEOPLE_EMAILS_ENABLED）：整次失败，报错指明开关和 --export 回退，不改文件。"""
        self.bridge.set_channel(CHAN_1, {"alice@a4x.io": KEY_A}, with_emails=False)
        message = self.assert_fails_closed(lambda: self.run_single(self.config), self.before,
                                           contains=(CHAN_1, "CHANNEL_PEOPLE_EMAILS_ENABLED", "--export"))
        self.assertNotIn(KEY_A, message)

    def test_a_non_200_answer_fails_the_whole_run_with_its_status(self):
        """L1-GIS-PA-031 401/403/404/429/500/503 与不跟随的 302：整次失败，报状态码与频道，不回显正文。"""
        for code in (401, 403, 404, 429, 500, 503, 302):
            with self.subTest(status=code):
                self.expect_failure(("raw", code, f'{{"error":"{BODY_MARKER}"}}'.encode()), contains=(f"HTTP {code}",))

    def test_a_404_explains_the_owner_or_admin_requirement(self):
        """L1-GIS-PA-032 404（签名者不是该频道 owner/admin，或频道不存在）的报错点明「签名者必须是 owner 或 admin」。"""
        self.bridge.set_channel(CHAN_1, {"alice@a4x.io": KEY_A}, roles={"dd" * 32: "owner"})
        self.assert_fails_closed(lambda: self.run_single(self.config), self.before,
                                 contains=(CHAN_1, "HTTP 404", "owner or admin"))

    def test_a_401_explains_the_signature_was_refused(self):
        """L1-GIS-PA-033 401（验签没过：钟不准、base_url 与 bridge 的 BIND_PUBLIC_ORIGIN 不一致）的报错提示这两点。"""
        message = self.expect_failure(("raw", 401, b'{"error":"invalid_signature"}'), contains=("HTTP 401",))
        self.assertIn("BIND_PUBLIC_ORIGIN", message)

    def test_timeouts_and_network_errors_fail_the_run_without_echoing_the_cause(self):
        """L1-GIS-PA-034 超时、连接被拒、socket 超时：整次失败，不带异常文字。"""
        for exc in (TimeoutError("timed out to SECRET-HOST"), OSError("connection refused SECRET-HOST"),
                    socket.timeout("SECRET-HOST"), ConnectionResetError("SECRET-HOST")):
            with self.subTest(exc=type(exc).__name__):
                self.expect_failure(("raise", exc), forbids=("SECRET-HOST",))

    def test_a_body_that_is_not_the_contract_fails_the_run(self):
        """L1-GIS-PA-035 200 但正文不是 JSON / 不是对象 / 缺 people / people 畸形：整次失败。"""
        good = {"channel": CHAN_1, "as_of": "2026-09-20T12:00:00Z", "people": {KEY_A: "ou_a"}, "union_ids": {},
                "emails": {KEY_A: ["alice@a4x.io"]}}
        cases = {
            "html": b"<html>" + BODY_MARKER.encode() + b"</html>",
            "empty": b"",
            "not utf-8": b"\xff\xfe" + BODY_MARKER.encode(),
            "list": json.dumps([good]).encode(),
            "string": json.dumps(BODY_MARKER).encode(),
            "no people": json.dumps({k: v for k, v in good.items() if k != "people"}).encode(),
            "people is a list": json.dumps(dict(good, people=[KEY_A])).encode(),
            "bad person key": json.dumps(dict(good, people={"XYZ": "ou_a"})).encode(),
            "bad open id": json.dumps(dict(good, people={KEY_A: "alice@a4x.io"})).encode(),
        }
        for label, body in cases.items():
            with self.subTest(label=label):
                self.expect_failure(("raw", 200, body))

    def test_an_answer_for_another_channel_fails_the_run(self):
        """L1-GIS-PA-036 响应里的 channel 不是请求的那个：整次失败。"""
        self.expect_failure(("doc", lambda doc: dict(doc, channel=CHAN_2)))
        self.expect_failure(("doc", lambda doc: {k: v for k, v in doc.items() if k != "channel"}))

    def test_an_oversized_answer_fails_the_run(self):
        """L1-GIS-PA-037 响应超过 1 MiB 上限：整次失败（传输层报超限与正文本身超限两条路都覆盖）。"""
        self.expect_failure(("raw", 200, b"{" + b" " * (MAX_BYTES + 1) + b"}"))
        bridge = importlib.import_module("buzz_feishu_group_sync")
        self.expect_failure(("raise", bridge.GroupSyncError("people API answer is larger than the cap")))

    def test_malformed_emails_fail_the_run(self):
        """L1-GIS-PA-038 emails 不是 {64hex: [邮箱…]}（不是对象、键不是 pubkey、值不是列表、元素不是含 @ 的字符串）：整次失败。"""
        cases = {
            "list": ["alice@a4x.io"],
            "null": None,
            "string": "alice@a4x.io",
            "bad key": {"XYZ": ["alice@a4x.io"]},
            "upper key": {("ab" * 32).upper(): ["alice@a4x.io"]},
            "value is a string": {KEY_A: "alice@a4x.io"},
            "value is an object": {KEY_A: {"alice@a4x.io": 1}},
            "item is a number": {KEY_A: [7]},
            "item has no at": {KEY_A: ["alice"]},
            "item is empty": {KEY_A: [""]},
            "item has no local part": {KEY_A: ["@a4x.io"]},
            "item has no domain": {KEY_A: ["alice@"]},
            "item is blank around the at": {KEY_A: [" @ "]},
            "item has a blank local part": {KEY_A: [" @a4x.io"]},
            "item has a blank domain": {KEY_A: ["alice@ "]},
        }
        for label, emails in cases.items():
            with self.subTest(label=label):
                self.expect_failure(("doc", lambda doc, e=emails: dict(doc, emails=e)))

    def test_extra_top_level_fields_are_tolerated(self):
        """L1-GIS-PA-039 契约允许以后多出顶层字段（如 union_ids、as_of 之外的新字段）：照常取用 emails。"""
        self.bridge.override[CHAN_1] = ("doc", lambda doc: dict(doc, future_field={"x": 1}))
        result = self.run_single(self.config)
        self.assertEqual(result["added"], 2)

    def test_upper_case_addresses_are_matched_case_insensitively(self):
        """L1-GIS-PA-040 邮箱大小写、首尾空白不敏感（契约说小写，宽松处理不破坏匹配）：大小写不同的精确地址仍是精确匹配，不会掉进 localpart 歧义。"""
        self.gitlab.members[1312] = ["alice", "bob", "sam"]
        self.bridge.override[CHAN_1] = ("doc", lambda doc: dict(doc, emails={
            KEY_A: [" Alice@A4X.io "], KEY_B: ["bob@a4x.io", "sam@addx.ai"], KEY_C: ["Sam@A4X.IO"]}))
        result = self.run_single(self.config)
        self.assertEqual((result["added"], result["unmapped"]), (3, []))
        self.assertEqual(json.loads(self.config.read_text())["people"],
                         {"carol": KEY_C, "alice": KEY_A, "bob": KEY_B, "sam": KEY_C})


class SharedApiTest(ApiCase):
    def two_channels(self, ch1, ch2, members=None):
        self.gitlab.members = members or {1312: ["alice", "carol"], 1313: ["bob", "carol"]}
        self.bridge.set_channel(CHAN_1, ch1)
        self.bridge.set_channel(CHAN_2, ch2)
        a = self.channel_config("a.json", CHAN_1, (1312,))
        b = self.channel_config("b.json", CHAN_2, (1313,), token_env="GL_TOKEN_B", agents=(AGENT_2,))
        return a, b

    def test_each_channel_is_asked_once_and_the_answers_are_merged(self):
        """L1-GIS-PA-050 共享模式：每个配置的频道各取一次，emails 合并，写一份共享文件（0600）；各配置一字不改。"""
        a, b = self.two_channels({"alice@a4x.io": KEY_A, "carol@a4x.io": KEY_C},
                                 {"bob@a4x.io": KEY_B, "carol@a4x.io": KEY_C})
        before = (a.read_bytes(), b.read_bytes())
        result = self.run_shared([a, b])
        self.assertEqual([r["url"] for r in self.bridge.requests], [people_url(CHAN_1), people_url(CHAN_2)])
        self.assertEqual((result["added"], result["channels"], result["configs"]), (3, 2, 2))
        self.assertEqual(self.shared_content(), {"alice": KEY_A, "bob": KEY_B, "carol": KEY_C})
        self.assertEqual(self.people_file.stat().st_mode & 0o777, 0o600)
        self.assertEqual((a.read_bytes(), b.read_bytes()), before)

    def test_a_person_who_maps_to_two_different_keys_gets_one_of_them_written(self):
        """L1-GIS-PA-051 同一 username 在两个频道映射到不同 pubkey：写一把而不是丢掉（有意的行为变更：以前是不猜、进 unmapped，
        那个人一条消息也收不到）。两个频道各一把，并列，取 hex 字典序最小的；其余人照写；unmapped 里没有他；
        警告只有用户名、选中那把的前 8 位和放弃的数量，输出里没有邮箱和完整 pubkey。"""
        a, b = self.two_channels({"alice@a4x.io": KEY_A, "carol@a4x.io": KEY_C},
                                 {"alice@a4x.io": KEY_B, "bob@a4x.io": KEY_B, "carol@a4x.io": KEY_C},
                                 {1312: ["alice", "carol"], 1313: ["bob"]})
        result = self.run_shared([a, b])
        self.assertEqual(self.shared_content(), {"alice": KEY_A, "bob": KEY_B, "carol": KEY_C})
        self.assertEqual((result["added"], result["unmapped"]), (3, []))
        self.assertEqual(result["warn"], ["alice: address maps to several keys, chose 11111111, dropped 1"])
        blob = json.dumps(result)
        for secret in (KEY_A, KEY_B, KEY_C, "@a4x.io"):
            self.assertNotIn(secret, blob)

    def test_a_conflict_never_overrides_a_manual_entry_or_deletes_it(self):
        """L1-GIS-PA-052 冲突的人若已在共享文件里（手填）：原条目原样保留，不被覆盖也不被删。"""
        a, b = self.two_channels({"alice@a4x.io": KEY_A}, {"alice@a4x.io": KEY_B},
                                 {1312: ["alice"], 1313: []})
        self.write_private("people.json", {"alice": KEY_C})
        result = self.run_shared([a, b])
        self.assertEqual(self.shared_content(), {"alice": KEY_C})
        self.assertFalse(result["written"])

    def test_one_person_two_addresses_with_one_key_is_no_conflict(self):
        """L1-GIS-PA-053 同一个 pubkey 在两个频道用不同域名的邮箱：localpart 兜底不算歧义；不同 pubkey 才算。"""
        a, b = self.two_channels({"sam@addx.ai": KEY_A, "tim@addx.ai": KEY_B},
                                 {"sam@addx.live": KEY_A, "tim@addx.live": KEY_C},
                                 {1312: ["sam", "tim"], 1313: []})
        result = self.run_shared([a, b])
        self.assertEqual(self.shared_content(), {"sam": KEY_A})
        self.assertEqual(result["unmapped"], ["tim"])

    def test_a_channel_that_cannot_be_read_fails_the_whole_run_and_names_it(self):
        """L1-GIS-PA-054 某个频道取不到（签名者在那里不是 owner/admin，接口 404）：整次失败、不写共享文件，报错指明是哪个频道。"""
        a, b = self.two_channels({"alice@a4x.io": KEY_A}, {"bob@a4x.io": KEY_B})
        self.bridge.channels[CHAN_2]["roles"] = {OWNER_PK: "member"}
        before = self.listing()
        self.assert_fails_closed(lambda: self.run_shared([a, b]), before,
                                 contains=(CHAN_2, "HTTP 404"), forbids=(CHAN_1,))
        self.assertFalse(self.people_file.exists())

    def test_a_channel_without_emails_fails_the_whole_run_and_names_it(self):
        """L1-GIS-PA-055 只有其中一个频道的响应没有 emails：整次失败，报错点名那个频道；共享文件不建。"""
        a, b = self.two_channels({"alice@a4x.io": KEY_A}, {"bob@a4x.io": KEY_B})
        self.bridge.set_channel(CHAN_2, {"bob@a4x.io": KEY_B}, with_emails=False)
        before = self.listing()
        self.assert_fails_closed(lambda: self.run_shared([a, b]), before,
                                 contains=(CHAN_2, "CHANNEL_PEOPLE_EMAILS_ENABLED"), forbids=(CHAN_1,))
        self.assertFalse(self.people_file.exists())

    def test_existing_shared_entries_win_and_are_never_deleted_in_api_mode(self):
        """L1-GIS-PA-056 共享文件里已有条目在 API 模式下同样优先、不删除（含已不在任何频道里的人）。"""
        a, b = self.two_channels({"alice@a4x.io": KEY_A, "carol@a4x.io": KEY_C}, {"bob@a4x.io": KEY_B})
        self.write_private("people.json", {"alice": KEY_B, "retired": KEY_C})
        result = self.run_shared([a, b])
        self.assertEqual(result["kept_manual"], 1)
        self.assertEqual(self.shared_content(), {"alice": KEY_B, "retired": KEY_C, "bob": KEY_B, "carol": KEY_C})

    def test_a_key_forbidden_in_any_config_is_refused(self):
        """L1-GIS-PA-057 任一 config 的 agent/publisher pubkey 都不许进共享文件（取并集校验），即使它来自另一个频道的响应。"""
        a, b = self.two_channels({"alice@a4x.io": AGENT_2, "carol@a4x.io": KEY_C}, {"bob@a4x.io": PUBLISHER},
                                 {1312: ["alice", "carol"], 1313: ["bob"]})
        result = self.run_shared([a, b])
        self.assertEqual((result["added"], result["rejected_agent_key"]), (1, 2))
        self.assertEqual(self.shared_content(), {"carol": KEY_C})

    def test_dry_run_asks_the_bridge_and_creates_no_file_at_all(self):
        """L1-GIS-PA-058 共享模式 --dry-run：照常取接口，但不建共享文件也不建锁文件（目录清单前后一致）。"""
        a, b = self.two_channels({"alice@a4x.io": KEY_A}, {"bob@a4x.io": KEY_B})
        before = sorted(p.name for p in self.root.iterdir())
        result = self.run_shared([a, b], dry_run=True)
        self.assertEqual(len(self.bridge.requests), 2)
        self.assertTrue(result["changed"])
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), before)

    def test_a_bad_config_among_several_refuses_the_run_before_asking_the_bridge(self):
        """L1-GIS-PA-059 多个配置里有一个不合格（宽权限）：整次拒绝，什么都不写。"""
        a, b = self.two_channels({"alice@a4x.io": KEY_A}, {"bob@a4x.io": KEY_B})
        os.chmod(b, 0o644)
        with self.assertRaises(MODULE.GenerateError):
            self.run_shared([a, b])
        self.assertFalse(self.people_file.exists())


class SameMailboxManyKeysTest(ApiCase):
    """One address bound to several verified keys is one person with several keys: @-ing any of them reaches him, dropping the
    person means he gets nothing. So one key is written (the one that is a member of the most configured channels; ties go to
    the smallest hex, so the same input always gives the same output) and a warning says so."""

    def channels_emails(self, per_channel):
        """The bridge answers exactly these `emails` ({pubkey: [address, ...]}; a key that is listed is a member) per channel."""
        for channel, emails in per_channel.items():
            self.bridge.set_channel(channel, {})
            self.bridge.override[channel] = ("doc", lambda doc, e=emails: dict(doc, emails=e))

    @staticmethod
    def written(path):
        """What the run wrote there; None when it wrote nothing (a missing file is a failed assertion, not an error)."""
        return json.loads(path.read_text()) if path.exists() else None

    def two_configs(self, members, agents=((AGENT_1,), (AGENT_2,))):
        self.gitlab.members = members
        a = self.channel_config("a.json", CHAN_1, (1312,), agents=agents[0])
        b = self.channel_config("b.json", CHAN_2, (1313,), token_env="GL_TOKEN_B", agents=agents[1])
        return a, b

    def test_the_key_in_the_most_channels_wins_over_a_smaller_hex(self):
        """L1-GIS-PA-070 同一邮箱两把 key：在更多个已配置频道里是成员的那把胜出，哪怕另一把 hex 更小；警告只有用户名、
        选中那把的前 8 位、放弃的数量。成员按「这把 key 出现在该频道的 emails 里」算，不要求那个频道里它绑的也是这个邮箱。"""
        a, b = self.two_configs({1312: ["alice"], 1313: []})
        for n, (label, chan2) in enumerate((("member of the second channel under another address", {KEY_B: ["b-old@example.test"]}),
                                            ("bound to the same address there too", {KEY_B: ["alice@a4x.io"]}))):
            with self.subTest(label=label):
                self.channels_emails({CHAN_1: {KEY_A: ["alice@a4x.io"], KEY_B: ["alice@a4x.io"]}, CHAN_2: chan2})
                people_file = self.root / f"people-{n}.json"
                result = self.run_shared([a, b], people_file=people_file)
                self.assertEqual(self.written(people_file), {"alice": KEY_B})
                self.assertEqual((result["added"], result["unmapped"]), (1, []))
                self.assertEqual(result["warn"], ["alice: address maps to several keys, chose 22222222, dropped 1"])
                blob = json.dumps(result)
                for secret in (KEY_A, KEY_B, "a4x.io", "example.test"):
                    self.assertNotIn(secret, blob)

    def test_a_tie_goes_to_the_smallest_hex_whatever_the_order(self):
        """L1-GIS-PA-071 并列（成员的频道数一样）取 hex 字典序最小的，与频道、配置、响应里的先后无关：同样的输入永远得到同样的输出。"""
        a, b = self.two_configs({1312: ["alice"], 1313: []})
        self.channels_emails({CHAN_1: {KEY_C: ["alice@a4x.io"], KEY_B: ["alice@a4x.io"]}, CHAN_2: {}})
        first = self.run_shared([a, b], people_file=self.root / "first.json")
        second = self.run_shared([b, a], people_file=self.root / "second.json")
        self.assertEqual(self.written(self.root / "first.json"), {"alice": KEY_B})
        self.assertEqual(self.written(self.root / "second.json"), {"alice": KEY_B})
        self.assertEqual(first["warn"], second["warn"])
        self.assertEqual(first["warn"], ["alice: address maps to several keys, chose 22222222, dropped 1"])

    def test_the_single_channel_run_picks_too_and_counts_what_it_dropped(self):
        """L1-GIS-PA-072 单配置（写回内联 people）同样选一把：同一个频道里一个邮箱绑了三把，并列取最小，警告里放弃 2 把。"""
        self.gitlab.members[1312] = ["alice"]
        self.channels_emails({CHAN_1: {KEY_C: ["alice@a4x.io"], KEY_B: ["alice@a4x.io"], KEY_A: ["alice@a4x.io"]}})
        config = self.channel_config("config.json")
        result = self.run_single(config)
        self.assertEqual(json.loads(config.read_text())["people"], {"alice": KEY_A})
        self.assertEqual((result["added"], result["unmapped"]), (1, []))
        self.assertEqual(result["warn"], ["alice: address maps to several keys, chose 11111111, dropped 2"])

    def test_a_key_that_would_be_refused_is_never_the_one_chosen(self):
        """L1-GIS-PA-073 被拒的 pubkey（Desk / agent / publisher）先剔除再选：即使它 hex 最小、在最多个频道里，也不会因此被选中；
        选的是剩下的人的 key。候选全是被拒的：什么都不写，记一次拒写、警告说被拒。"""
        a, b = self.two_configs({1312: ["alice", "bob"], 1313: []}, agents=((KEY_A,), (AGENT_2,)))
        self.channels_emails({
            CHAN_1: {KEY_A: ["alice@a4x.io"], KEY_B: ["alice@a4x.io"], KEY_C: ["bob@a4x.io"], PUBLISHER: ["bob@a4x.io"]},
            CHAN_2: {KEY_A: ["alice@a4x.io"], PUBLISHER: ["bob@a4x.io"]},
        })
        result = self.run_shared([a, b])
        self.assertEqual(self.written(self.people_file), {"alice": KEY_B, "bob": KEY_C})
        self.assertEqual((result["added"], result["rejected_agent_key"], result["unmapped"]), (2, 0, []))
        self.assertNotIn(KEY_A, json.dumps(result))
        self.assertIn("alice: address maps to several keys, chose 22222222, dropped 1", result["warn"],
                      "the refused key was one of the keys given up")
        self.channels_emails({CHAN_1: {KEY_A: ["alice@a4x.io"], PUBLISHER: ["alice@a4x.io"]}, CHAN_2: {}})
        self.people_file.unlink(missing_ok=True)
        result = self.run_shared([a, b])
        self.assertFalse(self.people_file.exists(), "nothing but refused keys: nothing to write")
        self.assertEqual((result["added"], result["rejected_agent_key"], result["written"]), (0, 1, False))
        self.assertTrue(any(w.startswith("alice:") and "refused" in w for w in result["warn"]), result["warn"])

    def test_a_manual_entry_still_wins_and_is_never_deleted(self):
        """L1-GIS-PA-074 手填优先、不删除照旧：邮箱绑了多把 key 的人若共享文件里已有条目，原条目原样保留，不被选中的那把覆盖；
        手填的是候选之一（哪怕不是会被选中的那把）就不警告，手填的不是任何候选才警告「手填与来源不同」。"""
        a, b = self.two_configs({1312: ["alice"], 1313: []})
        self.channels_emails({CHAN_1: {KEY_A: ["alice@a4x.io"], KEY_B: ["alice@a4x.io"]}, CHAN_2: {}})
        for label, manual, warned in (("one of the candidates", KEY_B, False), ("not a candidate", KEY_C, True)):
            with self.subTest(label=label):
                self.write_private("people.json", {"alice": manual, "retired": KEY_C})
                result = self.run_shared([a, b])
                self.assertEqual(self.shared_content(), {"alice": manual, "retired": KEY_C})
                self.assertEqual((result["added"], result["kept_manual"], result["written"]), (0, 1, False))
                self.assertEqual(any("manual entry kept" in w for w in result["warn"]), warned, result["warn"])

    def test_only_one_address_is_that_one_person_other_names_are_unrelated(self):
        """L1-GIS-PA-075 只有「同一个邮箱的多把 key」才选一把：不同用户名互不相干；localpart 兜底时不同邮箱（可能是不同的人）
        对到不同 key 仍然不猜、进 unmapped；同一个邮箱（这里没有 username@域名 的精确匹配，靠 localpart 兜底找到）的多把 key 才选。"""
        self.gitlab.members[1312] = ["alice", "bob", "sam", "tim"]
        self.channels_emails({CHAN_1: {
            KEY_A: ["alice@a4x.io", "sam@addx.ai"], KEY_B: ["alice@a4x.io", "sam@addx.live"], KEY_C: ["bob@a4x.io"],
            "55" * 32: ["tim@addx.ai"], "44" * 32: ["tim@addx.ai"],
        }})
        config = self.channel_config("config.json")
        result = self.run_single(config)
        self.assertEqual(json.loads(config.read_text())["people"], {"alice": KEY_A, "bob": KEY_C, "tim": "44" * 32})
        self.assertEqual(result["unmapped"], ["sam"])
        warns = sorted(w.split(":")[0] for w in result["warn"])
        self.assertEqual(warns, ["alice", "sam", "tim"], result["warn"])
        self.assertIn("sam: ambiguous localpart, kept unmapped", result["warn"])

    def test_without_channel_data_nothing_is_chosen(self):
        """L1-GIS-PA-081 选一把只在有频道数据（API 模式）时才发生：没有 member_channels（--export 走的路径）时，
        同一个邮箱对到几把 key 仍然不猜、进 unmapped——直接调匹配函数钉住，免得导出模式被这次改动悄悄带偏。"""
        emails = {"alice@a4x.io": {KEY_A, KEY_B}}
        added, kept, rejected, unmapped, warn = MODULE._match_usernames({"alice"}, emails, {}, set(), "a4x.io", "export", None)
        self.assertEqual((added, kept, rejected, unmapped), ({}, 0, 0, ["alice"]))
        self.assertEqual(warn, ["alice: address maps to several keys, kept unmapped"])
        added, _, _, unmapped, _ = MODULE._match_usernames({"alice"}, emails, {}, set(), "a4x.io", "bridge", {})
        self.assertEqual((added, unmapped), ({"alice": KEY_A}, []))

    def test_a_person_in_more_configured_channels_is_preferred_even_when_a_channel_is_named_twice(self):
        """L1-GIS-PA-080 「频道数」按去重后的频道算：两份配置指向同一个频道，只算一个频道。一把 key 在被两份配置指向的频道里、
        另一把在别的频道里：各算 1，并列，取 hex 最小的（不是因为频道被重复点名就多算一票）。"""
        self.gitlab.members = {1312: ["alice"], 1313: []}
        a = self.channel_config("a.json", CHAN_1, (1312,))
        c = self.channel_config("c.json", CHAN_1, (1312,), token_env="GL_TOKEN_B", agents=(AGENT_2,))
        b = self.channel_config("b.json", CHAN_2, (1313,), token_env="GL_TOKEN_B")
        self.channels_emails({CHAN_1: {KEY_B: ["alice@a4x.io"]}, CHAN_2: {KEY_A: ["alice@a4x.io"]}})
        result = self.run_shared([a, c, b])
        self.assertEqual(len(self.bridge.requests), 2)
        self.assertEqual(self.written(self.people_file), {"alice": KEY_A})
        self.assertEqual(result["warn"], ["alice: address maps to several keys, chose 11111111, dropped 1"])


class CommandLineTest(ApiCase):
    def test_api_arguments_run_the_api_mode(self):
        """L1-GIS-PA-060 命令行：--people-api-base-url + --signer-env-file 走 API 模式（单配置写回内联 people），输出只有计数与用户名。"""
        self.gitlab.members[1312] = ["alice", "ghost"]
        self.bridge.set_channel(CHAN_1, {"alice@a4x.io": KEY_A})
        config = self.channel_config("config.json")
        code, out, err = self.call(self.api_argv(config))
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        self.assertEqual((result["added"], result["unmapped"], result["source"]), (1, ["ghost"], "api"))
        self.assertEqual(json.loads(config.read_text())["people"], {"alice": KEY_A})

    def test_api_arguments_with_people_file_run_the_shared_mode(self):
        """L1-GIS-PA-061 命令行：--config 可重复 + --people-file + API 参数走共享 API 模式。"""
        self.gitlab.members = {1312: ["alice"], 1313: ["bob"]}
        self.bridge.set_channel(CHAN_1, {"alice@a4x.io": KEY_A})
        self.bridge.set_channel(CHAN_2, {"bob@a4x.io": KEY_B})
        a = self.channel_config("a.json", CHAN_1, (1312,))
        b = self.channel_config("b.json", CHAN_2, (1313,), token_env="GL_TOKEN_B")
        code, out, err = self.call(self.api_argv(a, b, people_file=self.people_file))
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["channels"], 2)
        self.assertEqual(self.shared_content(), {"alice": KEY_A, "bob": KEY_B})
        for key in (KEY_A, KEY_B):
            self.assertNotIn(key, out)

    def test_export_and_api_arguments_are_mutually_exclusive(self):
        """L1-GIS-PA-062 --export 与 API 参数互斥：给了任意一个 API 参数再给 --export，报清楚的错、退出 1、不发请求、不改文件。"""
        self.gitlab.members[1312] = ["alice"]
        self.bridge.set_channel(CHAN_1, {"alice@a4x.io": KEY_A})
        config = self.channel_config("config.json")
        export = self.write_private("export.json", {"alice@a4x.io": KEY_A})
        before = self.listing()
        for label, extra in (("both", ["--people-api-base-url", ORIGIN, "--signer-env-file", str(self.signer_env)]),
                             ("base url", ["--people-api-base-url", ORIGIN]),
                             ("signer", ["--signer-env-file", str(self.signer_env)])):
            with self.subTest(label=label):
                code, out, err = self.call(["--config", str(config), "--export", str(export), *extra])
                self.assertEqual((code, out), (1, ""))
                self.assertIn("--export", err)
                self.assertIn("mutually exclusive", err)
                self.assertEqual(self.listing(), before)
        self.assertEqual(self.bridge.requests, [])

    def test_both_api_arguments_are_needed(self):
        """L1-GIS-PA-063 只给 --people-api-base-url 或只给 --signer-env-file：报错点名缺的那个，不发请求。"""
        config = self.channel_config("config.json")
        for given, missing in (("--people-api-base-url", "--signer-env-file"),
                               ("--signer-env-file", "--people-api-base-url")):
            with self.subTest(given=given):
                value = ORIGIN if given == "--people-api-base-url" else str(self.signer_env)
                code, out, err = self.call(["--config", str(config), given, value])
                self.assertEqual((code, out), (1, ""))
                self.assertIn(f"{given} needs {missing}", err)
        self.assertEqual(self.bridge.requests, [])

    def test_a_source_is_required(self):
        """L1-GIS-PA-064 既没有 --export 也没有 API 参数：报错并列出两种来源（推荐 API、--export 回退）。"""
        config = self.channel_config("config.json")
        code, out, err = self.call(["--config", str(config)])
        self.assertEqual((code, out), (1, ""))
        self.assertIn("--people-api-base-url", err)
        self.assertIn("--export", err)

    def test_several_configs_still_need_people_file_in_api_mode(self):
        """L1-GIS-PA-065 API 模式下给多个 --config 而没有 --people-file，仍然报错。"""
        a = self.channel_config("a.json", CHAN_1)
        b = self.channel_config("b.json", CHAN_2)
        code, _, err = self.call(self.api_argv(a, b))
        self.assertEqual(code, 1)
        self.assertIn("--people-file", err)
        self.assertEqual(self.bridge.requests, [])

    def test_the_export_mode_needs_neither_the_bridge_module_nor_a_signer(self):
        """L1-GIS-PA-066 旧的 --export 模式不加载 bridge 客户端、不读签名 key、不发任何 bridge 请求，行为不变。"""
        self.gitlab.members[1312] = ["alice"]
        config = self.channel_config("config.json")
        export = self.write_private("export.json", {"alice@a4x.io": KEY_A})
        with mock.patch.object(MODULE, "_bridge", side_effect=AssertionError("the export mode must not need it")):
            code, out, err = self.call(["--config", str(config), "--export", str(export)], http=None)
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        self.assertEqual((result["added"], result["source"]), (1, "export"))
        self.assertNotIn("channels", result)
        self.assertEqual(json.loads(config.read_text())["people"], {"alice": KEY_A})
        self.assertEqual(self.bridge.requests, [])

    def test_help_documents_both_sources(self):
        """L1-GIS-PA-067 --help 写明 API 模式（推荐）与 --export（回退/离线）、所需的 bridge 开关与签名者角色，仍承诺手填优先、不删除。"""
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"COLUMNS": "10000"}), contextlib.redirect_stdout(out), \
                self.assertRaises(SystemExit) as ctx:
            MODULE.main(["--help"])
        self.assertEqual(ctx.exception.code, 0)
        text = " ".join(out.getvalue().split())
        for needle in ("--people-api-base-url", "--signer-env-file", "--export", "CHANNEL_PEOPLE_EMAILS_ENABLED",
                       "owner or admin", "manual entries win", "nothing is ever deleted", "fallback"):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)


    def test_help_documents_the_several_keys_rule(self):
        """L1-GIS-PA-079 --help 写明：同一邮箱绑了几把 key 时选在最多个已配置频道里是成员的那把、并列取 hex 最小、警告只带前 8 位与放弃的数量。"""
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"COLUMNS": "10000"}), contextlib.redirect_stdout(out), \
                self.assertRaises(SystemExit):
            MODULE.main(["--help"])
        text = " ".join(out.getvalue().split())
        for needle in ("most configured channels", "smallest hex", "first 8 hex digits"):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)

if __name__ == "__main__":
    unittest.main()
