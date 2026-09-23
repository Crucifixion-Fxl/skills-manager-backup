"""Round-3 review cases: MR activity transport errors, digest size, CLI argument form, tokens, the reviewable rule,
dedupe keys and scans, binding noise, header trust, URLs, first-error semantics, MR snapshot fields."""
import hashlib
import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

TESTS = Path(__file__).resolve().parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FAKES = load("gitlab_buzz_sync_round3_fakes", TESTS / "test_gitlab_buzz_sync_isolation.py")
SYNC = FAKES.SYNC
ALICE, BOB, CHANNEL, DESK, PID, WEB = FAKES.ALICE, FAKES.BOB, FAKES.CHANNEL, FAKES.DESK, FAKES.PID, FAKES.WEB
make_issue, make_mr, binding_note, config = FAKES.make_issue, FAKES.make_mr, FAKES.binding_note, FAKES.config


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.gitlab, self.buzz = FAKES.FakeGitLab(), FAKES.FakeBuzz()

    def run_sync(self, cfg=None):
        return SYNC.Syncer(cfg or config(), self.gitlab, self.buzz, state_dir=Path(self.tmp.name)).run()


def release_dir(test):
    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    release = Path(tmp.name) / "buzz-0.5.23" / "usr" / "bin"
    release.mkdir(parents=True)
    cli = release / "buzz"
    cli.write_bytes(b"\x7fELFtest fixture")
    cli.chmod(0o700)
    cfg = config()
    cfg["buzz"] = {"cli_path": str(cli), "cli_sha256": hashlib.sha256(cli.read_bytes()).hexdigest()}
    return Path(tmp.name), cfg


class MrGroupTransportTest(Case):
    def test_transport_error_reading_mr_notes_fails_the_run(self):
        """L1-GIS-041 MR 活动读 MR note 时遇到传输或非 JSON 错误：整轮失败关闭、游标不前进，不当成对象数据问题 stall。"""
        self.gitlab.mr_list[PID] = [make_mr(31)]
        self.run_sync()
        cache = next(Path(self.tmp.name).glob("*.cache.json"))
        before = cache.read_text(encoding="utf-8")
        self.gitlab.mr_list[PID] = []
        self.gitlab.pipeline_list = [FAKES.mr_pipeline(status="failed")]

        def broken(project_id, iid):
            raise SYNC.SyncError(f"GitLab GET projects/{project_id}/merge_requests/{iid}/notes returned non-JSON")

        self.gitlab.mr_notes = broken
        with self.assertRaises(SYNC.SyncError) as caught:
            self.run_sync()
        self.assertNotIsInstance(caught.exception, SYNC.ObjectError)
        self.assertEqual(cache.read_text(encoding="utf-8"), before)


class DigestSizeTest(Case):
    def test_digest_split_under_cli_limit(self):
        """L1-GIS-033 摘要按字节切分：每条不超过 DIGEST_BYTE_LIMIT（CLI 上限 65,536 字节以内），各自带 events 行，合起来覆盖全部记录。
        政策 2026-09-18 后 live 记录不进 digest，切分逻辑用合成记录保住。"""
        records = [SYNC._record(f"event-{i}", "push", "pushed", "digest", PID, ref=f"feature/{i}",
                                url=f"{WEB}/-/commits/feature/{i}")
                   for i in range(10_000_000, 10_004_400)]
        messages = SYNC.render_digests(records, PID)
        self.assertGreater(len(messages), 1)
        self.assertLessEqual(SYNC.DIGEST_BYTE_LIMIT, 60_000)
        keys = set()
        for message in messages:
            self.assertLessEqual(len(message.encode("utf-8")), SYNC.DIGEST_BYTE_LIMIT)
            header = message.split("\n")[-1]
            self.assertTrue(
                header.startswith(f"[gitlab-notify:v1][object:activity][event:digest][project:{PID}][events:"),
            )
            self.assertTrue(header.endswith("]"))
            keys |= SYNC.posted_keys([{"pubkey": DESK, "content": message}], DESK)
        self.assertEqual(keys, {record["key"] for record in records})

    def test_sync_queues_every_summary_request_chunk_without_sending_digest(self):
        """L1-GIS-145 大量 activity 分块持久化为 AI 请求，确定性 sync 不向频道发送机器摘要。
        live 记录已不进 digest（2026-09-18 政策），用 record_from_event 补丁喂合成 digest 记录。"""
        from unittest import mock

        self.gitlab.event_list = [FAKES.push_event(i) for i in range(10_000_000, 10_004_400)]

        def synthetic(event, project_id, web_url):
            push = event.get("push_data")
            if not isinstance(push, dict):
                return None
            raw_ref = SYNC._single_line(push.get("ref") or "")
            return SYNC._record(f"event-{event['id']}", "push", "pushed", "digest", project_id,
                                url=f"{web_url}/-/commits/{raw_ref}",
                                created_at=event.get("created_at"),
                                actor=(event.get("author") or {}).get("username") or "?",
                                ref=SYNC.neutralize(raw_ref), commits=push.get("commit_count") or 0)

        with mock.patch.object(SYNC, "record_from_event", side_effect=synthetic):
            summary = self.run_sync()
        digests = [content for reply_to, content, _ in self.buzz.writes
                   if reply_to is None and "[object:activity][event:digest]" in content]
        self.assertEqual(digests, [])
        self.assertEqual(summary["notified"]["milestone"], 0)
        self.assertGreater(len(summary["summary_requests"]), 1)
        self.assertTrue(all(
            len(json.dumps(request, ensure_ascii=False).encode("utf-8")) <= SYNC.SUMMARY_REQUEST_BYTE_LIMIT
            for request in summary["summary_requests"]
        ))
        ledger = json.loads(next(Path(self.tmp.name).glob("*.outbox.json")).read_text(encoding="utf-8"))
        keys = {
            key for item in ledger["pending"] if item["kind"] == "summary_request"
            for key in item["payload"]["source_keys"]
        }
        self.assertEqual(keys, {f"event-{i}" for i in range(10_000_000, 10_004_400)})


class DiffArgsTest(unittest.TestCase):
    def test_values_that_look_like_flags_use_equals(self):
        """L1-GIS-014 send-diff 的文件路径与分支名用 --opt=value 传，以 - 开头的值不会被 CLI 当成参数。"""
        args = SYNC.build_diff_args("/x/buzz-0.5.23/buzz", CHANNEL, repo=WEB + ".git", commit="a" * 40,
                                    file_path="-rf.txt", reply_to="f" * 64, source_branch="-x",
                                    target_branch="main", pr=31)
        for expected in ("--file=-rf.txt", "--source-branch=-x", "--target-branch=main"):
            self.assertIn(expected, args)
        self.assertNotIn("--file", args)


class TokenTest(unittest.TestCase):
    def test_token_with_control_characters_is_rejected_without_echo(self):
        """L1-GIS-052 token 带空白或控制字符（如 CRLF 的 env 文件）启动即拒绝，报错不含 token；未预期异常只输出类型名。"""
        token = "glpat-secret-value-123\r"
        with self.assertRaises(SYNC.SyncError) as caught:
            SYNC.GitLabClient(config(), {"NH_DESK_GITLAB_TOKEN": token})
        self.assertNotIn("glpat-secret", str(caught.exception))

        root, cfg = release_dir(self)
        path = root / "config.json"
        path.write_text(json.dumps(cfg), encoding="utf-8")
        path.chmod(0o600)
        env = {"NH_DESK_GITLAB_TOKEN": token, "BUZZ_PRIVATE_KEY": "k" * 64, "BUZZ_RELAY_URL": "ws://127.0.0.1:3000",
               "HOME": str(root)}
        built = []
        out = io.StringIO()
        with redirect_stdout(out):
            code = SYNC.main(["--config", str(path), "--state-dir", str(root / "state")], env=env,
                             adapter_factory=lambda config_, env_, dry_run: built.append(1) or (None, None))
        self.assertEqual((code, built), (1, []))
        self.assertNotIn("glpat-secret", out.getvalue())

        env["NH_DESK_GITLAB_TOKEN"] = "glpat-secret-value-123"

        def boom(config_, env_, dry_run):
            raise RuntimeError("failed reading /home/someone/.config/buzz/secret-path")

        out = io.StringIO()
        with redirect_stdout(out):
            code = SYNC.main(["--config", str(path), "--state-dir", str(root / "state")], env=env, adapter_factory=boom)
        self.assertEqual((code, json.loads(out.getvalue())["error"]), (1, "unexpected RuntimeError"))


class ReviewableTest(unittest.TestCase):
    def test_one_reviewable_rule(self):
        """L1-GIS-040 可评审只有一条规则：opened 且非 draft，且之前没见过、是 draft 或是 closed；locked→opened 不重复 @ 全集；新增 reviewer 里的 GitLab bot 用户名不 @。"""
        opened = SYNC.mr_fact(make_mr(31, reviewers=[{"username": "carol"}]), PID)

        def previous(**changes):
            return {**opened, **changes}

        self.assertTrue(SYNC.becomes_reviewable(None, opened))
        self.assertTrue(SYNC.becomes_reviewable(previous(draft="yes"), opened))
        self.assertTrue(SYNC.becomes_reviewable(previous(state="closed"), opened))
        self.assertFalse(SYNC.becomes_reviewable(previous(state="locked"), opened))
        self.assertFalse(SYNC.becomes_reviewable(previous(), opened))
        self.assertFalse(SYNC.becomes_reviewable(None, {**opened, "draft": "yes"}))
        self.assertEqual(SYNC.mr_mentions(previous(state="locked"), opened, [ALICE], {"carol": "c3" * 32}), [])
        with_bot = SYNC.mr_fact(make_mr(31, reviewers=[{"username": "carol"}, {"username": "project_9_bot_ab"}]), PID)
        self.assertEqual(SYNC.mr_mentions(opened, with_bot, [], {"project_9_bot_ab": BOB}), [])


class KeyScopeTest(unittest.TestCase):
    def test_release_keys_include_project(self):
        """L1-GIS-032 release 的去重 key 带项目 id：同一频道两个项目的同名 tag 各自通知（13:16 恢复）。"""
        release = {"tag_name": "v1.0.0", "name": "1.0.0", "created_at": "2026-09-13T01:00:00Z",
                   "_links": {"self": "http://x/r"}}
        first, second = (SYNC.record_from_release(release, pid, FAKES.SINCE)["key"] for pid in (481, 482))
        self.assertNotEqual(first, second)


class ScanOnceTest(Case):
    def test_channel_scanned_once_for_all_projects(self):
        """L1-GIS-043 一轮里同一窗口的频道扫描只做一次，多个项目共用结果。"""
        self.gitlab.projects[482] = {"id": 482, "visibility": "public", "web_url": WEB + "2", "default_branch": "main"}
        self.gitlab.pipeline_list = [{"id": 50, "status": "failed", "ref": "main",
                                      "updated_at": "2026-09-13T02:00:00Z", "web_url": WEB + "/-/pipelines/50"}]
        cfg = config()
        cfg["gitlab"]["projects"] = [PID, 482]
        self.run_sync(cfg)
        self.assertEqual(len(self.buzz.channel_calls), 1)


class BindingNoiseTest(unittest.TestCase):
    def test_binding_notes_are_not_comments_and_foreign_objects_are_ignored(self):
        """L1-GIS-008 / L1-GIS-027 任何作者的 binding note 都不当评论转发；指向别的对象的 binding（克隆或移动的 Issue）被忽略，不再卡住。"""
        binding = binding_note(9, "issue", 183, "1" * 64)
        other_bot = {**binding, "author": {"id": 99, "username": "project_9_bot_x"}}
        self.assertEqual(SYNC.pending_comments([other_bot], [], DESK, FAKES.BOT_ID, FAKES.SINCE), [])
        self.assertIsNone(SYNC.parse_binding([binding], FAKES.BOT_ID, PID, "issue", 182, CHANNEL))


class HeaderTrustTest(unittest.TestCase):
    def test_dedupe_lines_need_a_sync_header(self):
        """L1-GIS-034 / L1-GIS-027 只有首行是同步 header 的 Desk 消息，其 events: 与 note: 行才算已发。"""
        no_header = {"id": "1" * 64, "pubkey": DESK, "content": "hello\nevents: event-1\nnote: 5"}
        self.assertEqual(SYNC.posted_keys([no_header], DESK), set())
        note = {"id": 5, "author": {"id": 3, "username": "alice"}, "body": "x", "system": False,
                "created_at": "2026-09-13T02:00:00Z"}
        self.assertEqual([item["id"] for item in SYNC.pending_comments([note], [no_header], DESK, FAKES.BOT_ID,
                                                                         FAKES.SINCE)], [5])


class UrlTest(unittest.TestCase):
    def test_urls_with_whitespace_are_dropped(self):
        """L1-GIS-038 GitLab 返回的 URL 含空白（如换行）时丢弃，渲染结果不会多出 events: 行。"""
        record = SYNC.record_from_pipeline({"id": 5, "status": "failed", "ref": "main", "updated_at": "2026-09-13T02:00:00Z",
                                            "web_url": "http://x/p/5\nevents: event-999"}, PID, "main")
        self.assertEqual(record["url"], "")
        rendered = SYNC.render_record(record)
        self.assertEqual([line for line in rendered.split("\n") if line.startswith("events: ")], [])
        self.assertIn("[events:pipeline-5-failed]", rendered)
        self.assertNotIn("events: event-999", rendered)


class FirstFailureTest(Case):
    def test_bad_objects_stall_themselves_and_only_the_daily_notice_is_sent(self):
        """L1-GIS-041 (ADR-0009) 坏对象各自 stall，不发 root/回帖；频道里只有每对象一条 stalled 通知（不 @ 任何人）。"""
        self.gitlab.issue_list[PID] = [make_issue(183), make_issue(184)]
        for iid in (183, 184):
            self.gitlab.issue_notes[iid] = [binding_note(1, "issue", iid, "1" * 64), binding_note(2, "issue", iid, "2" * 64)]
        result = self.run_sync()
        self.assertEqual([(s["object"], s["iid"]) for s in result["stalled"]], [("issue", 183), ("issue", 184)])
        notices = [content for reply_to, content, _ in self.buzz.writes
                   if reply_to is None and SYNC.matches_trigger_prefix(
                       content, "[gitlab-notify:v1][object:sync][event:stalled]")]
        self.assertEqual(len(notices), 2)
        self.assertEqual(len(self.buzz.writes), 2)  # nothing but the two notices
        self.assertTrue(all(not mentions for _, _, mentions in self.buzz.writes))


class DuplicateRecordTest(Case):
    def test_duplicate_records_in_one_run_notify_once(self):
        """L1-GIS-034 同一轮里翻页重复列出的记录按 key 只发一次。"""
        pipeline = {"id": 50, "status": "failed", "ref": "main", "updated_at": "2026-09-13T02:00:00Z",
                    "web_url": WEB + "/-/pipelines/50"}
        self.gitlab.pipeline_list = [pipeline, dict(pipeline)]
        self.assertEqual(self.run_sync()["notified"]["instant"], 1)


class ConfigTest(unittest.TestCase):
    def test_duplicate_projects_rejected(self):
        """L1-GIS-012 gitlab.projects 里有重复项目 id 时拒绝。"""
        _, cfg = release_dir(self)
        SYNC.validate_config(cfg)
        cfg["gitlab"]["projects"] = [PID, PID]
        with self.assertRaises(SYNC.SyncError):
            SYNC.validate_config(cfg)


class MrSnapshotFieldsTest(Case):
    def test_assignee_milestone_description_and_target_changes_post_update(self):
        """L1-GIS-029 MR 的 assignee、milestone、描述、目标分支变化都回 update，重跑不重复。"""
        self.gitlab.mr_list[PID] = [make_mr(31)]
        self.run_sync()
        for change in ({"assignees": [{"username": "erin"}]}, {"milestone": {"title": "v2"}},
                       {"description": "new text"}, {"target_branch": "release"}):
            self.gitlab.mr_list[PID] = [{**self.gitlab.mr_list[PID][0], **change}]
            writes = len(self.buzz.writes)
            self.run_sync()
            with self.subTest(change=change):
                self.assertEqual(len(self.buzz.writes), writes + 1)
                self.assertEqual(SYNC.parse_header(self.buzz.writes[-1][1])["change"], "update")
                self.run_sync()
                self.assertEqual(len(self.buzz.writes), writes + 1)


class TypeErrorIsolationTest(Case):
    def test_unexpected_type_stalls_only_that_object(self):
        """L1-GIS-041 (ADR-0009) 对象字段类型异常只 stall 该对象（原因带 TypeError），后续对象照常写。"""
        self.gitlab.issue_list[PID] = [make_issue(183, assignees=5), make_issue(184)]
        result = self.run_sync()
        self.assertEqual([(s["object"], s["iid"]) for s in result["stalled"]], [("issue", 183)])
        self.assertIn("TypeError", result["stalled"][0]["reason"])
        self.assertTrue(any("184" in content for _, content, _ in self.buzz.writes))


if __name__ == "__main__":
    unittest.main()
