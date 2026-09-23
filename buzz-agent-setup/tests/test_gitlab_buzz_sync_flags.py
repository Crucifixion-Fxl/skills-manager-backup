import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
SCRIPT = SKILL / "scripts" / "gitlab_buzz_sync.py"


def load_module():
    spec = importlib.util.spec_from_file_location("gitlab_buzz_sync_flags", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SYNC = load_module()

DESK = "d" * 64
CHANNEL = "00000000-0000-4000-8000-0000000000c1"
BOT_ID = 7
BOT = "buzz-sync-bot"
PID = 481
WEB = "http://127.0.0.1:8929/buzz-sync-test/pilot"
SINCE = "2026-09-13T00:00:00Z"
SERVER_TIME = "2026-09-13T04:00:00Z"


def token(token_id=11, name="deploy-bot", expires_at="2026-09-18", active=True, revoked=False):
    return {"id": token_id, "name": name, "expires_at": expires_at, "active": active, "revoked": revoked,
            "scopes": ["read_api"], "access_level": 30}


class TokenRecordTest(unittest.TestCase):
    def test_access_token_record(self):
        """L1-GIS-032 7 天内到期的有效 token 每天提醒一次；更远、已吊销、已失效或已过期不提醒。"""
        record = SYNC.record_from_access_token(token(), PID, WEB, "2026-09-13")
        self.assertEqual((record["object"], record["event"], record["placement"], record["key"]),
                         ("access_token", "expiring", "instant", "access_token-11-2026-09-13"))
        self.assertEqual(record["title"], "deploy-bot (2026-09-18)")
        self.assertIsNotNone(SYNC.record_from_access_token(token(expires_at="2026-09-20"), PID, WEB, "2026-09-13"))
        self.assertIsNotNone(SYNC.record_from_access_token(token(expires_at="2026-09-13"), PID, WEB, "2026-09-13"))
        for bad in (token(expires_at="2026-09-21"), token(revoked=True), token(active=False),
                    token(expires_at="2026-09-12"), token(expires_at=None)):
            with self.subTest(bad=bad):
                self.assertIsNone(SYNC.record_from_access_token(bad, PID, WEB, "2026-09-13"))

    def test_top_level_header_accepts_new_objects(self):
        """L1-GIS-033 顶层 header 支持 access_token。"""
        rendered = SYNC.render_record(SYNC.record_from_access_token(token(), PID, WEB, "2026-09-13"))
        header = SYNC.parse_header(rendered)
        self.assertEqual(
            {key: header[key] for key in ("object", "event", "project")},
            {"object": "access_token", "event": "expiring", "project": PID},
        )
        self.assertEqual(header.get("events"), ["access_token-11-2026-09-13"])


class OptionalEndpointTest(unittest.TestCase):
    def test_optional_paged_returns_none_on_forbidden(self):
        """L1-GIS-032 无权限（401/403/404）的可选接口返回 None，其他错误照常失败关闭。"""
        client = object.__new__(SYNC.GitLabClient)

        def forbidden(method, path, params=None, body=None):
            raise SYNC.GitLabHTTPError(method, path, 403)

        client.request = forbidden
        self.assertIsNone(client.optional_paged(f"projects/{PID}/access_tokens", {}))

        def broken(method, path, params=None, body=None):
            raise SYNC.GitLabHTTPError(method, path, 500)

        client.request = broken
        with self.assertRaises(SYNC.SyncError):
            client.optional_paged(f"projects/{PID}/access_tokens", {})
        self.assertTrue(issubclass(SYNC.GitLabHTTPError, SYNC.SyncError))


class FakeGitLab:
    def __init__(self):
        self.user = {"id": BOT_ID, "username": BOT}
        self.tokens = [token()]

    def current_user(self):
        return self.user

    def scan_time(self):
        return SERVER_TIME

    def project(self, project_id):
        return {"id": project_id, "default_branch": "main", "web_url": WEB, "visibility": "public"}

    def issues(self, project_id, updated_after):
        return []

    def merge_requests(self, project_id, updated_after):
        return []

    def members(self, project_id):
        return []

    def events(self, project_id, after_date):
        return []

    def pipelines(self, project_id, updated_after):
        return []

    def deployments(self, project_id, updated_after):
        return []

    def releases(self, project_id):
        return []

    def access_tokens(self, project_id):
        return None if self.tokens is None else list(self.tokens)


class FakeBuzz:
    def __init__(self):
        self.events = []
        self.writes = []

    def send(self, content, reply_to=None, mentions=()):
        event_id = hashlib.sha256(f"{len(self.events)}:{content}".encode()).hexdigest()
        self.events.append({"id": event_id, "pubkey": DESK, "kind": 9, "created_at": 1000 + len(self.events),
                            "tags": [["h", CHANNEL]], "content": content})
        self.writes.append((reply_to, content))
        return event_id

    def channel_messages(self, since_unix):
        return list(self.events)


class TokenSyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.gitlab = FakeGitLab()
        self.buzz = FakeBuzz()
        self.config = {
            "channel_id": CHANNEL, "publisher_pubkey": DESK, "since": SINCE, "include_confidential": False,
            "exclude": [], "diff": {"enabled": False, "private": False},
            "gitlab": {"base_url": "http://127.0.0.1:8929", "token_env": "NH_DESK_GITLAB_TOKEN",
                       "bot_user_id": BOT_ID, "bot_username": BOT, "projects": [PID]},
            "buzz": {}, "people": {},
        }

    def tearDown(self):
        self.tmp.cleanup()

    def run_sync(self):
        return SYNC.Syncer(self.config, self.gitlab, self.buzz, state_dir=Path(self.tmp.name)).run()

    def test_tokens_notify_once(self):
        """L1-GIS-032 / L1-GIS-034 到期 token 发一条即时通知；重跑、删缓存不重复。
        2026-09-18 政策：flag/release/push 类即时通知已下线，token 是仅存的例行即时类。"""
        summary = self.run_sync()
        objects = [SYNC.parse_header(content)["object"] for _, content in self.buzz.writes]
        self.assertEqual(objects, ["access_token"])
        self.assertEqual(summary["notified"]["instant"], 1)
        self.assertEqual(summary["unavailable"], [])
        count = len(self.buzz.writes)
        self.run_sync()
        for cache in Path(self.tmp.name).glob("*.cache.json"):
            cache.unlink()
        self.run_sync()
        self.assertEqual(len(self.buzz.writes), count)

    def test_unavailable_endpoints_are_reported_not_fatal(self):
        """L1-GIS-032 token 接口无权限时跳过并记入 unavailable，其余同步照常。"""
        self.gitlab.tokens = None
        summary = self.run_sync()
        self.assertEqual(summary["status"], "ok")
        self.assertEqual(summary["unavailable"], [f"{PID}:access_tokens"])
        self.assertEqual(self.buzz.writes, [])


if __name__ == "__main__":
    unittest.main()
