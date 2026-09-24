import hashlib
import importlib.util
import json
import subprocess
import tempfile
import unittest
import urllib.error
from unittest import mock
from pathlib import Path
from types import SimpleNamespace

SKILL = Path(__file__).resolve().parents[1]
SCRIPT = SKILL / "scripts" / "gitlab_buzz_sync.py"


def load_module():
    spec = importlib.util.spec_from_file_location("gitlab_buzz_sync_adapters", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SYNC = load_module()

DESK = "d" * 64
CHANNEL = "00000000-0000-4000-8000-0000000000c1"
OTHER_CHANNEL = "00000000-0000-4000-8000-0000000000c2"
EVENT = "e" * 64
ROOT = "f" * 64
BOT = {"id": 7, "username": "buzz-sync-bot"}


class FakeResponse:
    def __init__(self, body, headers=None):
        self.body = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, size=-1):
        self.read_size = size
        return self.body if size < 0 else self.body[:size]


class FakeOpener:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        self.timeouts = getattr(self, "timeouts", []) + [timeout]
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def gitlab_config():
    return {"gitlab": {"base_url": "http://127.0.0.1:8929", "token_env": "NH_DESK_GITLAB_TOKEN",
                       "bot_user_id": BOT["id"], "bot_username": BOT["username"], "projects": [481]}}


def client(responses):
    opener = FakeOpener(responses)
    return SYNC.GitLabClient(gitlab_config(), {"NH_DESK_GITLAB_TOKEN": "glpat-test-token"}, opener=opener), opener


class GitLabAdapterTest(unittest.TestCase):
    def test_requires_token(self):
        """L2-1-GIS-001 缺 GitLab token 时构造即失败。"""
        with self.assertRaises(SYNC.SyncError):
            SYNC.GitLabClient(gitlab_config(), {}, opener=FakeOpener([]))

    def test_request_sends_token_and_parses_json(self):
        """L2-1-GIS-001 请求带 PRIVATE-TOKEN、拼 api/v4 路径与查询，并解析 JSON 与小写 header。"""
        gitlab, opener = client([FakeResponse({"id": 481}, {"X-Next-Page": ""})])
        value, headers = gitlab.request("GET", "projects/481", params={"a": "1"})
        self.assertEqual(value, {"id": 481})
        self.assertIn("x-next-page", headers)
        request = opener.requests[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:8929/api/v4/projects/481?a=1")
        self.assertEqual(request.get_header("Private-token"), "glpat-test-token")

    def test_request_errors_fail_closed(self):
        """L2-1-GIS-001 HTTP 错误（含被拒绝的重定向）、网络错误、非 JSON 都失败关闭，且错误里不带 token。"""
        redirect = urllib.error.HTTPError("http://127.0.0.1:8929/api/v4/user", 302, "Found", {}, None)
        for response, expected_status in (
            (urllib.error.HTTPError("u", 403, "Forbidden", {}, None), 403),
            (redirect, 302),
        ):
            gitlab, _ = client([response])
            with self.assertRaises(SYNC.GitLabHTTPError) as caught:
                gitlab.request("GET", "user")
            self.assertEqual(caught.exception.status, expected_status)
            self.assertNotIn("glpat-test-token", str(caught.exception))
        for response in (urllib.error.URLError("refused"), FakeResponse(b"<html>")):
            gitlab, _ = client([response])
            with self.assertRaises(SYNC.SyncError):
                gitlab.request("GET", "user")

    def test_no_redirect_handler(self):
        """L2-1-GIS-001 重定向处理器拒绝跟随，避免 token 跨 origin。"""
        handler = SYNC.NoRedirectHandler()
        self.assertIsNone(handler.redirect_request(None, None, 302, "Found", {}, "http://evil.test/"))

    def test_paged(self):
        """L2-1-GIS-001 翻页直到没有下一页；页码不前进或返回非列表时失败。"""
        gitlab, opener = client([FakeResponse([{"id": 1}], {"X-Next-Page": "2"}),
                                 FakeResponse([{"id": 2}], {"X-Next-Page": ""})])
        self.assertEqual(gitlab.paged("projects/481/issues", {"state": "all"}), [{"id": 1}, {"id": 2}])
        self.assertIn("page=2", opener.requests[1].full_url)
        gitlab, _ = client([FakeResponse([], {"X-Next-Page": "1"})])
        with self.assertRaises(SYNC.SyncError):
            gitlab.paged("x", {})
        gitlab, _ = client([FakeResponse({"not": "list"})])
        with self.assertRaises(SYNC.SyncError):
            gitlab.paged("x", {})

    def test_events_paginate_beyond_100(self):
        """L1-GIS-107 / L2-1-GIS-001 列表接口读取后续页，不会静默丢掉第 101 条（以 events 为例；
        2026-09-18 政策后 releases/feature_flags 不再拉取）。"""
        first_page = [{"id": index} for index in range(100)]
        page_101 = {"id": 100}
        gitlab, opener = client([
            FakeResponse(first_page, {"X-Next-Page": "2"}),
            FakeResponse([page_101], {"X-Next-Page": ""}),
        ])

        events = gitlab.events(481, "2026-09-12")

        self.assertEqual(len(events), 101)
        self.assertEqual(events[-1], page_101)
        self.assertEqual(len(opener.requests), 2)
        self.assertIn("after=2026-09-12", opener.requests[0].full_url)
        self.assertIn("sort=asc", opener.requests[0].full_url)
        self.assertIn("page=1", opener.requests[0].full_url)
        self.assertIn("per_page=100", opener.requests[0].full_url)
        self.assertIn("page=2", opener.requests[1].full_url)

    def test_response_page_and_run_budgets_fail_closed(self):
        """L2-1-GIS-085 Response bytes, page count and total GitLab run time all have hard bounds."""
        oversized = FakeResponse(b"x" * (SYNC.GITLAB_RESPONSE_MAX_BYTES + 1))
        gitlab, _ = client([oversized])
        with self.assertRaisesRegex(SYNC.SyncError, "response.*large"):
            gitlab.request("GET", "user")
        self.assertEqual(oversized.read_size, SYNC.GITLAB_RESPONSE_MAX_BYTES + 1)

        gitlab, _ = client([FakeResponse([], {"X-Next-Page": str(SYNC.GITLAB_PAGE_MAX + 1)})])
        with self.assertRaisesRegex(SYNC.SyncError, "pagination"):
            gitlab.paged("projects/481/issues", {})

        ticks = iter((10.0, 10.0 + SYNC.GITLAB_RUN_BUDGET_SECONDS + 1))
        gitlab = SYNC.GitLabClient(
            gitlab_config(), {"NH_DESK_GITLAB_TOKEN": "glpat-test-token"},
            opener=FakeOpener([FakeResponse(BOT)]), clock=lambda: next(ticks),
        )
        with self.assertRaisesRegex(SYNC.SyncError, "run budget"):
            gitlab.request("GET", "user")

    def test_current_user_scan_time_and_project(self):
        """L2-1-GIS-001 从 GET /user 的 Date 取扫描时间；缺 Date 失败；project/MR 回读不符失败。"""
        gitlab, _ = client([FakeResponse(BOT, {"Date": "Sun, 13 Sep 2026 04:00:00 GMT"})])
        self.assertEqual(gitlab.current_user(), BOT)
        self.assertEqual(gitlab.scan_time(), "2026-09-13T04:00:00Z")
        gitlab, _ = client([FakeResponse(BOT, {})])
        gitlab.current_user()
        with self.assertRaises(SYNC.SyncError):
            gitlab.scan_time()
        gitlab, _ = client([FakeResponse({"id": 999})])
        with self.assertRaises(SYNC.SyncError):
            gitlab.project(481)
        gitlab, _ = client([FakeResponse({"iid": 32})])
        with self.assertRaises(SYNC.SyncError):
            gitlab.merge_request(481, 31)

    def test_add_note_reads_back_author_and_body(self):
        """L2-1-GIS-001 写 note 后按 id 回读；作者或正文不符失败关闭；Issue 与 MR 路径不同。"""
        body = "<!-- gitlab-buzz-binding:v1 {} -->"
        gitlab, opener = client([FakeResponse({"id": 9}),
                                 FakeResponse({"id": 9, "author": BOT, "body": body})])
        self.assertEqual(gitlab.add_note(481, 182, body), 9)
        self.assertTrue(opener.requests[0].full_url.endswith("/projects/481/issues/182/notes"))
        self.assertTrue(opener.requests[1].full_url.endswith("/projects/481/issues/182/notes/9"))
        gitlab, opener = client([FakeResponse({"id": 9}),
                                 FakeResponse({"id": 9, "author": BOT, "body": body})])
        gitlab.add_mr_note(481, 31, body)
        self.assertTrue(opener.requests[0].full_url.endswith("/projects/481/merge_requests/31/notes"))
        for readback in ({"id": 9, "author": {"id": 8, "username": "x"}, "body": body},
                         {"id": 9, "author": BOT, "body": body + "!"}):
            gitlab, _ = client([FakeResponse({"id": 9}), FakeResponse(readback)])
            with self.assertRaises(SYNC.SyncError):
                gitlab.add_note(481, 182, body)
        gitlab, _ = client([FakeResponse({"message": "no id"})])
        with self.assertRaises(SYNC.SyncError):
            gitlab.add_note(481, 182, body)


class FakeRunner:
    def __init__(self):
        self.calls = []
        self.handlers = {}

    def __call__(self, args, input=None, capture_output=True, text=True, timeout=None, check=False, env=None):
        self.calls.append({"args": args, "input": input, "env": env})
        handler = self.handlers[tuple(args[1:3])]
        result = handler(args, input)
        if isinstance(result, Exception):
            raise result
        if isinstance(result, SimpleNamespace):
            return result
        return SimpleNamespace(returncode=0, stdout=json.dumps(result))


class BuzzAdapterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        release = Path(cls.tmp.name) / "buzz-0.5.23" / "usr" / "bin"
        release.mkdir(parents=True)
        cls.cli = release / "buzz"
        cls.cli.write_bytes(b"\x7fELFtest fixture")
        cls.cli.chmod(0o700)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def config(self):
        return {
            "channel_id": CHANNEL, "publisher_pubkey": DESK, **gitlab_config(),
            "buzz": {"cli_path": str(self.cli), "cli_sha256": hashlib.sha256(self.cli.read_bytes()).hexdigest()},
        }

    def env(self):
        return {"BUZZ_RELAY_URL": "ws://127.0.0.1:3000", "BUZZ_PRIVATE_KEY": "test-key",
                "NH_DESK_GITLAB_TOKEN": "glpat-test-token", "HOME": "/tmp"}

    def buzz(self):
        runner, sleeps = FakeRunner(), []
        adapter = SYNC.BuzzCli(self.config(), self.env(), runner=runner, sleeper=sleeps.append)
        return adapter, runner, sleeps

    def event(self, content, *, kind=9, reply_to=None, tags=(), pubkey=DESK, channel=CHANNEL, event_id=EVENT):
        all_tags = [["h", channel]]
        if reply_to:
            all_tags.append(["e", reply_to, "", "reply"])
        all_tags.extend(list(tag) for tag in tags)
        return {"id": event_id, "pubkey": pubkey, "kind": kind, "tags": all_tags, "content": content}

    def test_construction_validates_relay_cli_and_env(self):
        """L2-1-GIS-002 relay URL、CLI 路径与私钥缺失都在构造时失败。"""
        env = self.env()
        env["BUZZ_RELAY_URL"] = "ws://buzz.example.test"
        with self.assertRaises(SYNC.SyncError):
            SYNC.BuzzCli(self.config(), env)
        env = {k: v for k, v in self.env().items() if k != "BUZZ_PRIVATE_KEY"}
        with self.assertRaises(SYNC.SyncError):
            SYNC.BuzzCli(self.config(), env)

    def test_command_env_and_failures(self):
        """L2-1-GIS-002 子进程 env 不含 GitLab token；非零退出、非 JSON、无法执行都失败关闭。"""
        buzz, runner, _ = self.buzz()
        runner.handlers[("messages", "thread")] = lambda args, content: {"events": []}
        buzz.thread(ROOT)
        env = runner.calls[0]["env"]
        self.assertNotIn("NH_DESK_GITLAB_TOKEN", env)
        self.assertEqual(env["BUZZ_PRIVATE_KEY"], "test-key")
        self.assertEqual(runner.calls[0]["args"][0], str(self.cli))
        for outcome in (SimpleNamespace(returncode=2, stdout="{}"), SimpleNamespace(returncode=0, stdout="nope"),
                        FileNotFoundError("buzz"), subprocess.TimeoutExpired("buzz", 45)):
            runner.handlers[("messages", "thread")] = lambda args, content, outcome=outcome: outcome
            with self.subTest(outcome=type(outcome).__name__), self.assertRaises(SYNC.SyncError):
                buzz.thread(ROOT)

    def test_send_reads_back_exact_destination_and_mentions(self):
        """L2-1-GIS-002 发送后回读：内容、作者、频道、Thread、p tag 必须完全一致；找不到时重试后失败。"""
        buzz, runner, sleeps = self.buzz()
        content = "[gitlab-notify:v1][object:activity][event:digest][project:481]\nevents: event-1"
        runner.handlers[("messages", "send")] = lambda args, stdin: {"accepted": True, "event_id": EVENT}
        attempts = []

        def thread(args, stdin):
            attempts.append(1)
            return {"events": [] if len(attempts) == 1 else [self.event(content, reply_to=ROOT,
                                                                         tags=(("p", "a1" * 32),))]}

        runner.handlers[("messages", "thread")] = thread
        with mock.patch.object(SYNC, "verify_nostr_event_signature") as verify:
            self.assertEqual(buzz.send(content, reply_to=ROOT, mentions=["a1" * 32]), EVENT)
        verify.assert_called_once()
        self.assertEqual(sleeps, [0.5])
        send_args = runner.calls[0]["args"]
        self.assertEqual(send_args[-4:], ["--reply-to", ROOT, "--mention", "a1" * 32])
        self.assertEqual(runner.calls[0]["input"], content)

        bad_events = [
            self.event(content + "x", reply_to=ROOT),
            self.event(content, reply_to=ROOT, pubkey="c" * 64),
            self.event(content, reply_to=ROOT, channel=OTHER_CHANNEL),
            self.event(content, reply_to="0" * 64),
            self.event(content, reply_to=ROOT, tags=(("p", "b2" * 32),)),
        ]
        for bad in bad_events:
            runner.handlers[("messages", "thread")] = lambda args, stdin, bad=bad: {"events": [bad]}
            with self.subTest(bad=bad), self.assertRaises(SYNC.SyncError):
                buzz.send(content, reply_to=ROOT)
        runner.handlers[("messages", "thread")] = lambda args, stdin: {"events": []}
        with self.assertRaises(SYNC.SyncError):
            buzz.send(content, reply_to=ROOT)
        runner.handlers[("messages", "send")] = lambda args, stdin: {"accepted": False}
        with self.assertRaises(SYNC.SyncError):
            buzz.send(content)

    def test_edit_and_status_reaction_require_exact_readback(self):
        """L2-1-GIS-128 edit 覆盖原事件；reaction 收敛为 Desk 的唯一当前 Git 状态。"""
        buzz, runner, _ = self.buzz()
        content = "✅ **已合并**\n[gitlab-notify:v1][object:mr][state:merged][draft:no][change:lifecycle][transition:none][project:481][mr:31]"
        edit_id = "a" * 64
        runner.handlers[("messages", "edit")] = lambda args, stdin: {
            "accepted": True, "event_id": edit_id,
        }
        runner.handlers[("messages", "thread")] = lambda args, stdin: {"events": [
            self.event(content, kind=40003, tags=(("e", ROOT),), event_id=edit_id),
        ]}

        with mock.patch.object(SYNC, "verify_nostr_event_signature") as verify:
            self.assertEqual(buzz.edit(ROOT, content), edit_id)
        verify.assert_called_once()
        self.assertEqual(runner.calls[0]["args"][-4:], ["--event", ROOT, "--content", content])

        reads = iter((
            {"reactions": [{"emoji": "👀", "pubkeys": [DESK]}]},
            {"reactions": [{"emoji": "✅", "pubkeys": [DESK]}]},
            {"reactions": [{"emoji": "✅", "pubkeys": [DESK]}]},
        ))
        runner.handlers[("reactions", "get")] = lambda args, stdin: next(reads)
        runner.handlers[("reactions", "remove")] = lambda args, stdin: {"accepted": True}
        runner.handlers[("reactions", "add")] = lambda args, stdin: {"accepted": True}

        buzz.set_status_reaction(ROOT, "✅")
        self.assertTrue(buzz.status_reaction_matches(ROOT, "✅"))
        reaction_calls = [call["args"][1:3] for call in runner.calls if call["args"][1] == "reactions"]
        self.assertEqual(reaction_calls, [
            ["reactions", "get"], ["reactions", "remove"], ["reactions", "add"],
            ["reactions", "get"], ["reactions", "get"],
        ])

    def test_thread_rejects_unsigned_publisher_facts_before_card_selection(self):
        """L2-1-GIS-144 relay 不能用伪造 publisher kind9/edit 劫持紧凑状态卡。"""
        buzz, runner, _ = self.buzz()
        unsigned_original = self.event(
            "👀 **可评审**\n[gitlab-notify:v1][object:mr][state:opened][draft:no]"
            "[change:lifecycle][transition:reviewable][project:481][mr:31]",
            kind=9, pubkey=DESK,
        )
        runner.handlers[("messages", "thread")] = lambda args, stdin: {"events": [unsigned_original]}
        with self.assertRaisesRegex(SYNC.SyncError, "id or signature"):
            buzz.thread(ROOT)

        forged = self.event(
            "forged\n[gitlab-notify:v1][object:mr][state:opened][draft:no][change:activity]"
            "[transition:none][project:481][mr:31][reaction:failure][rev:999999999]",
            kind=40003, tags=(("e", ROOT),), pubkey=DESK,
        )
        runner.handlers[("messages", "thread")] = lambda args, stdin: {"events": [forged]}

        with self.assertRaisesRegex(SYNC.SyncError, "id or signature"):
            buzz.thread(ROOT)

        forged["tags"][0] = ["h", OTHER_CHANNEL]
        with self.assertRaisesRegex(SYNC.SyncError, "invalid publisher fact envelope"):
            buzz.thread(ROOT)

    def test_compact_edits_explicitly_scan_kind_40003_and_verify_matching_overlays(self):
        """R4：root thread 不含 edit 时，adapter 用 messages get 显式读取并验签目标层。"""
        buzz, runner, _ = self.buzz()
        original = self.event(
            "👀 **可评审**\n[gitlab-notify:v1][object:mr][state:opened][draft:no]"
            "[change:lifecycle][transition:reviewable][project:481][mr:31]",
            event_id=ROOT,
        )
        original["created_at"] = 100
        overlay = self.event(
            "✅ **已合并**\n[gitlab-notify:v1][object:mr][state:merged][draft:no]"
            "[change:lifecycle][transition:none][project:481][mr:31][rev:1]",
            kind=40003, tags=(("e", ROOT),), event_id="a" * 64,
        )
        overlay.update({"created_at": 101, "sig": "b" * 128})
        runner.handlers[("messages", "get")] = lambda args, stdin: {"events": [overlay]}

        with mock.patch.object(SYNC, "verify_nostr_event_signature") as verify:
            self.assertEqual(buzz.compact_edits([original]), [overlay])

        verify.assert_called_once_with(overlay, label="Buzz compact status overlay")
        args = runner.calls[0]["args"]
        self.assertEqual(args[args.index("--kinds") + 1], "40003")
        self.assertEqual(args[args.index("--since") + 1], "100")

    def test_thread_retries_a_transient_cli_read_error(self):
        """L2-1-GIS-004 Relay 刚接受 root 时 thread 暂不可读，重试查询而不重复发送。"""
        buzz, runner, sleeps = self.buzz()
        attempts = []

        def thread(args, stdin):
            attempts.append(1)
            if len(attempts) == 1:
                return SimpleNamespace(returncode=2, stdout="{}")
            return {"events": [self.event("root", event_id=ROOT)]}

        runner.handlers[("messages", "thread")] = thread

        self.assertEqual([event["id"] for event in buzz.thread(ROOT)], [ROOT])
        self.assertEqual(len(attempts), 2)
        self.assertEqual(sleeps, [0.5])

    def test_send_diff_reads_back_kind_commit_and_file(self):
        """L2-1-GIS-002 Diff 回读 kind 40008、Thread、commit 与 file tag。"""
        buzz, runner, _ = self.buzz()
        sha = "a" * 40
        runner.handlers[("messages", "send-diff")] = lambda args, stdin: {"accepted": True, "event_id": EVENT}
        good = self.event("diff --git a/x b/x\n", kind=40008, reply_to=ROOT, tags=(("commit", sha), ("file", "x")))
        runner.handlers[("messages", "thread")] = lambda args, stdin: {"events": [good]}
        kwargs = dict(repo="http://127.0.0.1:8929/g/p.git", commit=sha, file_path="x", reply_to=ROOT,
                      source_branch="feature/x", target_branch="main", pr=31)
        self.assertEqual(buzz.send_diff("diff --git a/x b/x\n", **kwargs), EVENT)
        for bad in (dict(good, kind=9), self.event("d", kind=40008, reply_to=ROOT, tags=(("commit", sha), ("file", "y")))):
            runner.handlers[("messages", "thread")] = lambda args, stdin, bad=bad: {"events": [bad]}
            with self.subTest(bad=bad), self.assertRaises(SYNC.SyncError):
                buzz.send_diff("d", **kwargs)

    def test_search_channel_scan_and_members(self):
        """L2-1-GIS-002 root 搜索命中上限或作者不符失败；频道扫描只留本频道；成员响应有坏记录就失败。"""
        buzz, runner, _ = self.buzz()
        runner.handlers[("messages", "search")] = lambda args, stdin: [
            self.event("x", event_id=f"{i:064x}") for i in range(SYNC.SEARCH_LIMIT)]
        url = "http://127.0.0.1:8929/buzz-sync-test/pilot/-/issues/182"
        with self.assertRaises(SYNC.ObjectError):
            buzz.search_roots(url, "issue", 182, 1_000_000)
        runner.handlers[("messages", "search")] = lambda args, stdin: [self.event("x", pubkey="c" * 64)]
        with self.assertRaises(SYNC.SyncError):
            buzz.search_roots(url, "issue", 182, 1_000_000)
        search_args = runner.calls[-1]["args"]
        self.assertEqual(search_args[search_args.index("--author") + 1], DESK)
        self.assertEqual(search_args[search_args.index("--query") + 1], "/-/issues/182")  # relay path tail (#106)
        self.assertEqual(search_args[search_args.index("--since") + 1], "1000000")
        runner.handlers[("messages", "get")] = lambda args, stdin: [
            self.event("mine", event_id="1" * 64), self.event("elsewhere", event_id="2" * 64, channel=OTHER_CHANNEL)]
        self.assertEqual([e["content"] for e in buzz.channel_messages(1_000_000)], ["mine"])
        get_args = runner.calls[-1]["args"]
        self.assertEqual(get_args[get_args.index("--channel") + 1], CHANNEL)
        self.assertEqual(get_args[get_args.index("--since") + 1], "1000000")
        runner.handlers[("channels", "members")] = lambda args, stdin: [
            {"pubkey": "a1" * 32, "role": "member"}]
        self.assertEqual(buzz.channel_members(), {"a1" * 32: "member"})
        for bad in ({"unexpected": True}, [{"pubkey": "a1" * 32, "role": "member"},
                                             {"pubkey": "bad", "role": "bot"}]):
            runner.handlers[("channels", "members")] = lambda args, stdin, bad=bad: bad
            with self.subTest(bad=bad), self.assertRaises(SYNC.SyncError):
                buzz.channel_members()


class MainHelpersTest(unittest.TestCase):
    def test_load_config_and_redact(self):
        """L2-1-GIS-001 配置文件读不到或不是对象时失败；错误输出里的 secret 被遮蔽。"""
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SYNC.SyncError):
                SYNC.load_config(Path(tmp) / "missing.json")
            path = Path(tmp) / "list.json"
            path.write_text("[]", encoding="utf-8")
            with self.assertRaises(SYNC.SyncError):
                SYNC.load_config(path)
        env = {"BUZZ_PRIVATE_KEY": "nsec1supersecret", "NH_DESK_GITLAB_TOKEN": "glpat-abcdefgh", "HOME": "/home/x"}
        self.assertEqual(SYNC.redact("key nsec1supersecret token glpat-abcdefgh home /home/x", env),
                         "key *** token *** home /home/x")

    def test_load_config_requires_an_owner_only_real_file(self):
        """L1-GIS-126 Config mode and symlink checks fail before credentials or adapters are used."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "config.json"
            config.write_text("{}", encoding="utf-8")
            config.chmod(0o600)
            self.assertEqual(SYNC.load_config(config), {})

            config.chmod(0o644)
            with self.assertRaisesRegex(SYNC.SyncError, "owner-only regular file"):
                SYNC.load_config(config)

            config.chmod(0o600)
            link = root / "linked.json"
            link.symlink_to(config)
            with self.assertRaisesRegex(SYNC.SyncError, "owner-only regular file"):
                SYNC.load_config(link)


if __name__ == "__main__":
    unittest.main()
