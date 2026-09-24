"""agent 的 owner 把飞书 app_id 公开进该 agent 的 kind:30177（ADR-0019，engineering/skills#147）。

`scripts/buzz_agent_feishu_app.py` 只做一件事：用 owner 的 key 读这个 agent 当前的 30177 head（作者是 owner、d 是 agent），在 content 里
合并 `"feishu": {"app_id": …}`，其余字段与标签逐字保留，签名发到 relay 的 `POST /events`，再用 `POST /query` 回读，核对 head 就是刚发的那条。

- relay 上没有这个 agent 的 30177（owner 签的）就拒绝：它不新建策略（那是铸身份的事，见 scripts/README.md）。
- 已经是同一个 app_id 就什么都不发。
- 发之前把旧事件备份成 0600 文件；`--dry-run` 只读不写。
- key 只从 0600 的 owner env 文件读，在进程内签名；不进 argv、不进输出。

relay 用假的：真验 NIP-98（POST、u、payload）与事件 id / 签名，按 NIP-33 保留每个 (作者, kind, d) 的最新一条。用例编号 L1-FAA-001 起。
"""

import base64
import functools
import hashlib
import importlib.util
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

TESTS = Path(__file__).resolve().parent
SCRIPT = TESTS.parent / "scripts" / "buzz_agent_feishu_app.py"
SPEC = importlib.util.spec_from_file_location("buzz_agent_feishu_app", SCRIPT)
FAA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FAA)
NK = FAA.fgs.sync.nk
_MEMO = []


def setUpModule():
    """Pure-python signatures are slow: memoize them, and pin the signing randomness so the memo hits (as the group sync tests do)."""
    for name in ("pubkey_xonly", "schnorr_sign", "schnorr_verify"):
        _MEMO.append(mock.patch.object(NK, name, functools.lru_cache(maxsize=None)(getattr(NK, name))))
    for module in (FAA.secrets, FAA.fgs.secrets):
        _MEMO.append(mock.patch.object(module, "token_bytes", lambda n: bytes(n)))
    for patch in _MEMO:
        patch.start()


def tearDownModule():
    while _MEMO:
        _MEMO.pop().stop()

OWNER_KEY = "c2" * 32
OWNER_PK = NK.pubkey_xonly(bytes.fromhex(OWNER_KEY)).hex()
OTHER_KEY = "c4" * 32
OTHER_PK = NK.pubkey_xonly(bytes.fromhex(OTHER_KEY)).hex()
AGENT = "ab" * 32
APP = "cli_a940faa4ec381bc5"
RELAY = "https://relay.test"
NOW = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc)
T = int(NOW.timestamp())


def signed(key, kind, tags, content, created_at):
    pubkey = NK.pubkey_xonly(bytes.fromhex(key)).hex()
    ser = json.dumps([0, pubkey, created_at, kind, tags, content], separators=(",", ":"), ensure_ascii=False)
    event_id = hashlib.sha256(ser.encode()).hexdigest()
    sig = NK.schnorr_sign(bytes.fromhex(event_id), bytes.fromhex(key), bytes(32)).hex()
    return {"id": event_id, "pubkey": pubkey, "created_at": created_at, "kind": kind, "tags": tags, "content": content,
            "sig": sig}


POLICY_CONTENT = {"name": "nh-dev", "parallelism": 2, "respond_to": "allowlist",
                  "respond_to_allowlist": ["11" * 32, "22" * 32], "future_field": {"x": [1, 2]}}


class FakeRelay:
    def __init__(self):
        self.events = [signed(OWNER_KEY, 30177, [["d", AGENT], ["alt", "managed agent"]], json.dumps(POLICY_CONTENT), T - 3600)]
        self.published = []
        self.queries = []
        self.reject = None  # (status, body) for the next /events
        self.drop_publish = False  # accept but do not store: the readback must notice

    def verify_auth(self, header, url, body):
        ev = json.loads(base64.b64decode(header[len("Nostr "):]))
        tags = {t[0]: t[1] for t in ev["tags"]}
        assert ev["kind"] == 27235 and tags["u"] == url and tags["method"] == "POST", ev
        assert tags["payload"] == hashlib.sha256(body).hexdigest()
        assert abs(ev["created_at"] - T) <= 60
        return ev["pubkey"]

    def __call__(self, url, headers, timeout, body=None):
        assert body is not None, "every relay call is a POST"
        signer = self.verify_auth(headers["Authorization"], url, body)
        if url == f"{RELAY}/query":
            filters = json.loads(body)
            self.queries.append({"filters": filters, "signer": signer})

            def match(ev, f):
                return ((not f.get("kinds") or ev["kind"] in f["kinds"])
                        and (not f.get("authors") or ev["pubkey"] in f["authors"])
                        and (not f.get("#d") or any(t[0] == "d" and t[1] in f["#d"] for t in ev["tags"])))
            return 200, json.dumps([e for e in self.events if any(match(e, f) for f in filters)]).encode()
        if url == f"{RELAY}/events":
            ev = json.loads(body)
            ser = json.dumps([0, ev["pubkey"], ev["created_at"], ev["kind"], ev["tags"], ev["content"]],
                             separators=(",", ":"), ensure_ascii=False)
            assert hashlib.sha256(ser.encode()).hexdigest() == ev["id"]
            assert NK.schnorr_verify(bytes.fromhex(ev["id"]), bytes.fromhex(ev["pubkey"]), bytes.fromhex(ev["sig"]))
            self.published.append({"event": ev, "signer": signer})
            if self.reject:
                return self.reject
            if not self.drop_publish:
                d = [t[1] for t in ev["tags"] if t[0] == "d"]
                self.events = [e for e in self.events
                               if not (e["pubkey"] == ev["pubkey"] and e["kind"] == ev["kind"]
                                       and [t[1] for t in e["tags"] if t[0] == "d"] == d)] + [ev]
            return 200, json.dumps({"event_id": ev["id"], "accepted": True, "message": ""}).encode()
        raise AssertionError(url)


class Case(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        os.chmod(self.tmp, 0o700)
        self.owner_env = self.tmp / "owner.env"
        nsec = NK.bech32_encode("nsec", bytes.fromhex(OWNER_KEY))
        self.secret_forms = {OWNER_KEY, nsec}
        self.owner_env.write_text(f"BUZZ_PRIVATE_KEY={nsec}\nBUZZ_RELAY_URL={RELAY}\n")
        os.chmod(self.owner_env, 0o600)
        self.backups = self.tmp / "backups"
        self.locks = self.tmp / "locks"
        self.lock_root = mock.patch.object(FAA, "LOCK_DIR", self.locks, create=True)
        self.lock_root.start()
        self.relay = FakeRelay()

    def tearDown(self):
        self.lock_root.stop()
        self._tmp.cleanup()

    def run_main(self, *extra, agent=AGENT, app=APP, backup=None):
        out, err = io.StringIO(), io.StringIO()
        argv = ["--owner-env", str(self.owner_env), "--agent", agent, "--app-id", app,
                "--backup-dir", str(backup or self.backups), *extra]
        code = FAA.main(argv, http=self.relay, now=NOW, stdout=out, stderr=err)
        for text in (out.getvalue(), err.getvalue()):
            for secret in self.secret_forms:
                self.assertNotIn(secret, text)
        return code, (json.loads(out.getvalue()) if out.getvalue().strip() else None), err.getvalue()

    def head(self):
        return max((e for e in self.relay.events if e["pubkey"] == OWNER_PK and e["kind"] == 30177),
                   key=lambda e: (e["created_at"], e["id"]))


class Publish(Case):
    def test_it_merges_the_app_id_and_keeps_everything_else(self):
        """L1-FAA-001: 发出的新 30177：作者是 owner、d 与其余标签原样、created_at 晚于旧的；content 只多了 feishu.app_id，其余字段逐字相同；
        回读核对通过，输出 published 与新旧事件 id（不带 key）。"""
        old = self.head()
        code, out, _ = self.run_main()
        self.assertEqual(code, 0)
        new = self.head()
        self.assertNotEqual(new["id"], old["id"])
        self.assertEqual(new["pubkey"], OWNER_PK)
        self.assertEqual(new["tags"], old["tags"])
        self.assertGreater(new["created_at"], old["created_at"])
        content = json.loads(new["content"])
        self.assertEqual(content.pop("feishu"), {"app_id": APP})
        self.assertEqual(content, POLICY_CONTENT)
        self.assertEqual(out["action"], "published")
        self.assertEqual((out["event_id"], out["previous_event"]), (new["id"], old["id"]))
        self.assertEqual({p["signer"] for p in self.relay.published}, {OWNER_PK})
        self.assertEqual(self.relay.queries[0]["filters"], [{"kinds": [30177], "authors": [OWNER_PK], "#d": [AGENT]}])

    def test_the_old_event_is_backed_up_owner_only(self):
        """L1-FAA-002: 发之前旧事件原样备份到 --backup-dir（0700 目录、0600 文件），出错时能手工重发回去。"""
        old = self.head()
        self.run_main()
        files = list(self.backups.iterdir())
        self.assertEqual(len(files), 1)
        self.assertEqual(json.loads(files[0].read_text()), old)
        self.assertEqual(stat.S_IMODE(files[0].stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.backups.stat().st_mode), 0o700)

    def test_other_feishu_keys_survive(self):
        """L1-FAA-003: content 里已有 feishu 对象（将来可能有别的键）：只改 app_id，别的键保留。"""
        self.relay.events = [signed(OWNER_KEY, 30177, [["d", AGENT]],
                                    json.dumps({"name": "x", "feishu": {"app_id": "cli_old00000000000001", "note": "keep"}}), T - 10)]
        code, _, _ = self.run_main()
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(self.head()["content"])["feishu"], {"app_id": APP, "note": "keep"})

    def test_the_latest_owner_policy_is_the_base(self):
        """L1-FAA-004: 以 owner 签的最新一条为底（旧的、别人签的都不算）。"""
        newer = dict(POLICY_CONTENT, parallelism=5)
        self.relay.events += [signed(OWNER_KEY, 30177, [["d", AGENT]], json.dumps(newer), T - 60),
                              signed(OTHER_KEY, 30177, [["d", AGENT]], json.dumps({"name": "forged"}), T - 5)]
        self.run_main()
        content = json.loads(self.head()["content"])
        self.assertEqual(content["parallelism"], 5)
        self.assertEqual(content["feishu"], {"app_id": APP})

    def test_an_invalid_newer_policy_cannot_hide_the_valid_head(self):
        """L1-FAA-005: relay 应答里伪造的较新 30177 不是 head；必须忽略它并继续基于旧的有效策略合并。"""
        fake = signed(OWNER_KEY, 30177, [["d", AGENT]], json.dumps({"name": "attacker"}), T - 1)
        fake["content"] = json.dumps({"name": "tampered-after-signing"})
        self.relay.events.append(fake)
        code, _, _ = self.run_main()
        self.assertEqual(code, 0)
        content = json.loads(self.head()["content"])
        self.assertEqual(content["name"], POLICY_CONTENT["name"])
        self.assertNotIn("tampered-after-signing", content.values())


class Refuse(Case):
    def test_dry_run_writes_nothing(self):
        """L1-FAA-010: --dry-run 只读：不发 /events、不写备份，输出 would_publish。"""
        code, out, _ = self.run_main("--dry-run")
        self.assertEqual(code, 0)
        self.assertEqual(out["action"], "would_publish")
        self.assertEqual(self.relay.published, [])
        self.assertFalse(self.backups.exists())

    def test_an_unchanged_app_id_is_not_republished(self):
        """L1-FAA-011: 已经是同一个 app_id：什么都不发，输出 unchanged。"""
        self.run_main()
        published = len(self.relay.published)
        code, out, _ = self.run_main()
        self.assertEqual((code, out["action"]), (0, "unchanged"))
        self.assertEqual(len(self.relay.published), published)

    def test_no_policy_no_publish(self):
        """L1-FAA-012: relay 上没有 owner 签的这个 agent 的 30177（只有别人签的也算没有）：退出码 2，不新建。"""
        self.relay.events = [signed(OTHER_KEY, 30177, [["d", AGENT]], "{}", T - 5)]
        code, _, err = self.run_main()
        self.assertEqual(code, 2)
        self.assertEqual(self.relay.published, [])
        self.assertIn("30177", err)

    def test_an_invalid_policy_is_not_a_policy(self):
        """L1-FAA-018: id / sig / 已签 content 任一被改过都不可用；只有这种 30177 时退出 2 且不发布。"""
        original = self.relay.events[0]
        for field, value in (("id", "00" * 32), ("sig", "00" * 64), ("content", json.dumps({"name": "tampered"}))):
            with self.subTest(field=field):
                bad = dict(original)
                bad[field] = value
                self.relay = FakeRelay()
                self.relay.events = [bad]
                code, _, _ = self.run_main()
                self.assertEqual((code, self.relay.published), (2, []))

    def test_bad_input_is_refused_before_the_network(self):
        """L1-FAA-013: agent 不是 64 位 hex（或 npub）、app_id 不像 app_id：退出码 2，一次请求都不发。"""
        for agent, app in (("xyz", APP), (AGENT, "ou_notanapp"), (AGENT, "cli_bad id")):
            with self.subTest(agent=agent, app=app):
                code, _, _ = self.run_main(agent=agent, app=app)
                self.assertEqual(code, 2)
        self.assertEqual((self.relay.queries, self.relay.published), ([], []))

    def test_a_content_that_is_not_an_object_is_refused(self):
        """L1-FAA-014: 现有 content 不是 JSON 对象：不知道怎么合并，退出码 2，不发。"""
        self.relay.events = [signed(OWNER_KEY, 30177, [["d", AGENT]], "not json", T - 5)]
        code, _, _ = self.run_main()
        self.assertEqual((code, self.relay.published), (2, []))

    def test_a_rejection_or_a_readback_mismatch_fails(self):
        """L1-FAA-015: relay 拒绝（非 200 或 accepted=false）或回读的 head 不是刚发的那条：退出码 1。"""
        for reject in ((400, b'{"error":"invalid"}'), (200, b'{"event_id":"x","accepted":false,"message":"blocked"}')):
            with self.subTest(reject=reject):
                self.relay = FakeRelay()
                self.relay.reject = reject
                code, _, err = self.run_main()
                self.assertEqual(code, 1)
                self.assertIn("did not accept", err)  # said as a refusal, not left for the readback to notice
        self.relay = FakeRelay()
        self.relay.drop_publish = True
        code, _, err = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("readback", err)

    def test_a_concurrent_run_is_refused(self):
        """L1-FAA-017: 两个进程同时改同一 owner 的 30177 会互相覆盖：锁按 owner pubkey 放在稳定状态目录，
        不由可变的 --backup-dir 决定。所以即使另一次运行指向不同备份目录，也必须退出 1，不读、不发。"""
        import fcntl
        self.locks.mkdir(mode=0o700)
        with open(self.locks / f"{OWNER_PK}.lock", "w") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            code, _, err = self.run_main(backup=self.tmp / "different-backups")
        self.assertEqual((code, self.relay.queries, self.relay.published), (1, [], []))
        self.assertIn("another", err)

    def test_the_owner_env_must_be_owner_only(self):
        """L1-FAA-016: owner env 不是 0600：拒绝读（退出码 2），不发任何请求。"""
        os.chmod(self.owner_env, 0o644)
        code, _, _ = self.run_main()
        self.assertEqual(code, 2)
        self.assertEqual(self.relay.queries, [])


# ================================ --mirror（ADR-0020） ================================

MIRROR_KEY = "c5" * 32
MIRROR = NK.pubkey_xonly(bytes.fromhex(MIRROR_KEY)).hex()  # 群同步的镜像身份：由 owner 用 NIP-OA 背书


def mirror_profile(owner=OWNER_PK, name="naturehood-feishu-mirror", at=T - 7200):
    """镜像自己的真实 NIP-01 kind 0。"""
    content = json.dumps({"name": name, "display_name": name})
    return signed(MIRROR_KEY, 0, [["auth", owner, "", "ab" * 64]], content, at)


class Mirror(Case):
    """`--mirror`：在镜像身份的 kind:30177 里声明 `"feishu": {"mirror": true}`，别的机器的群同步就不会把它当 agent 代发，入群申请脚本也认它
    转来的同意（它的 owner 是频道 owner/admin 时）。镜像本来没有 30177，所以这里可以新建：name 取它 kind 0 的名字（或 --name），
    parallelism 1，respond_to "owner-only"（Buzz Desktop 能解析的最保守的值；镜像不跑 harness，谁 @ 它都不会回）。"""

    def run_mirror(self, *extra, agent=MIRROR):
        out, err = io.StringIO(), io.StringIO()
        argv = ["--owner-env", str(self.owner_env), "--agent", agent, "--mirror", "--backup-dir", str(self.backups), *extra]
        code = FAA.main(argv, http=self.relay, now=NOW, stdout=out, stderr=err)
        for text in (out.getvalue(), err.getvalue()):
            for secret in self.secret_forms:
                self.assertNotIn(secret, text)
        return code, (json.loads(out.getvalue()) if out.getvalue().strip() else None), err.getvalue()

    def mirror_head(self):
        return max((e for e in self.relay.events if e["kind"] == 30177 and ["d", MIRROR] in e["tags"]),
                   key=lambda e: (e["created_at"], e["id"]))

    def test_a_mirror_without_a_policy_gets_one(self):
        """L1-FAA-020: 镜像还没有 30177：新建一条（作者 owner、d 是镜像），content 是 name（取自它的 kind 0）、parallelism 1、respond_to
        "owner-only"、feishu.mirror true；回读核对通过，输出 published / created。"""
        self.relay.events.append(mirror_profile())
        code, out, _ = self.run_mirror()
        self.assertEqual(code, 0)
        head = self.mirror_head()
        self.assertEqual((head["pubkey"], head["tags"]), (OWNER_PK, [["d", MIRROR]]))
        self.assertEqual(json.loads(head["content"]), {"name": "naturehood-feishu-mirror", "parallelism": 1,
                                                        "respond_to": "owner-only", "feishu": {"mirror": True}})
        self.assertEqual((out["action"], out["created"]), ("published", True))

    def test_an_existing_policy_only_gains_the_flag(self):
        """L1-FAA-021: 已有 30177：只在 content 里加 feishu.mirror（其余字段、标签原样）；已经是 true 就什么都不发（unchanged）。"""
        self.relay.events += [mirror_profile(), signed(OWNER_KEY, 30177, [["d", MIRROR]],
                                                       json.dumps({"name": "m", "parallelism": 1, "respond_to": "owner-only"}), T - 60)]
        code, out, _ = self.run_mirror()
        self.assertEqual((code, out["created"]), (0, False))
        self.assertEqual(json.loads(self.mirror_head()["content"]),
                         {"name": "m", "parallelism": 1, "respond_to": "owner-only", "feishu": {"mirror": True}})
        published = len(self.relay.published)
        code, out, _ = self.run_mirror()
        self.assertEqual((code, out["action"], len(self.relay.published)), (0, "unchanged", published))

    def test_only_the_mirrors_own_owner_may_declare_it(self):
        """L1-FAA-022: 镜像的 kind 0 声明的 owner 不是这把 key（或根本读不到它的 kind 0）：退出码 2、不发——这样发出去的声明谁也不会认。"""
        for events in ([mirror_profile(owner=OTHER_PK)], []):
            with self.subTest(events=len(events)):
                self.relay = FakeRelay()
                self.relay.events += events
                code, _, err = self.run_mirror()
                self.assertEqual((code, self.relay.published), (2, []))
                self.assertIn("owner", err)

    def test_a_tampered_profile_cannot_authorize_a_mirror_declaration(self):
        """L1-FAA-025: 镜像 kind 0 的 auth 只有在 id 与 BIP-340 签名都通过时才能授权 owner 新建 30177。"""
        bad = mirror_profile(owner=OTHER_PK)
        bad["tags"] = [["auth", OWNER_PK, "", "ab" * 64]]
        self.relay.events.append(bad)
        code, _, _ = self.run_mirror()
        self.assertEqual((code, self.relay.published), (2, []))

    def test_the_name_can_be_given_and_dry_run_says_it_would_create(self):
        """L1-FAA-023: --name 覆盖新建时的名字；--dry-run 只读，输出 would_publish、created true。"""
        self.relay.events.append(mirror_profile())
        code, out, _ = self.run_mirror("--dry-run", "--name", "feishu-mirror-nh")
        self.assertEqual((code, out["action"], out["created"], self.relay.published), (0, "would_publish", True, []))
        code, _, _ = self.run_mirror("--name", "feishu-mirror-nh")
        self.assertEqual(json.loads(self.mirror_head()["content"])["name"], "feishu-mirror-nh")

    def test_exactly_one_of_app_id_and_mirror(self):
        """L1-FAA-024: --app-id 与 --mirror 必须恰好给一个：两个都给或都不给，退出码 2，不发请求。"""
        for argv in (["--mirror", "--app-id", APP], []):
            with self.subTest(argv=argv):
                err = io.StringIO()
                code = FAA.main(["--owner-env", str(self.owner_env), "--agent", MIRROR, *argv], http=self.relay, now=NOW,
                                stdout=io.StringIO(), stderr=err)
                self.assertEqual(code, 2)
        self.assertEqual(self.relay.queries, [])


if __name__ == "__main__":
    unittest.main()
