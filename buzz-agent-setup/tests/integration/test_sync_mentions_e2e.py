"""L3: who the sync @-mentions on every message type, proven on the real local relay and GitLab.

Skipped unless BUZZ_SYNC_L3=1 and the localstack is up (`stack.py up`, no agents needed). Every case
creates uniquely titled objects in the localstack GitLab project, runs the real script in a subprocess
with an explicit environment and a fresh state directory, and reads the Desk's messages back through
the raw Buzz 0.5.23 CLI. What is asserted is the message's real `p` tag list (ADR-0018):

* the tagged pubkeys are exactly the intended people, in order, deduplicated, at most three;
* unmapped users, non-Channel-members, agents, the commenter/operator themself and everything outside
  the table (tag, release, milestone, wiki, push, feature flag, expiring token) get no `p` tag;
* a comment's `@username` tags only an exact, mapped project member; `@all`, `@everyone`, `nostr:`
  and non-members never produce a tag.

Only 127.0.0.1 services are contacted. Never run it concurrently with another L2/L3 suite: they share the
GitLab project and the Channel. Like the rest of the localstack suites it uses the raw CLI by absolute
path with test keys, never ~/.local/bin/buzz.
"""
from __future__ import annotations

import base64
import datetime as dt
import os
import secrets
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

try:
    from .test_sync_local import (
        CI_YAML, REF_SCRIPTS, Stack, first_line, iso, lines, p_tags, tag_values, utc_now, wait_for,
    )
except ImportError:  # imported as a top-level module
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from test_sync_local import (
        CI_YAML, REF_SCRIPTS, Stack, first_line, iso, lines, p_tags, tag_values, utc_now, wait_for,
    )

ENABLED = os.environ.get("BUZZ_SYNC_L3") == "1"
# main keeps one CI file so a default-branch pipeline can be created through the API and then dropped
# (no runner in the localstack). Its rules only fire for merge requests and API-created pipelines.
MAIN_CI = CI_YAML.replace(
    "rules:\n    - if: '$CI_PIPELINE_SOURCE == \"merge_request_event\"'\n      when: manual\n",
    "rules:\n    - if: '$CI_PIPELINE_SOURCE == \"merge_request_event\"'\n      when: manual\n"
    "    - if: '$CI_PIPELINE_SOURCE == \"api\"'\n      when: manual\n",
)


def ordered_p_tags(event: dict) -> list[str]:
    return [tag[1] for tag in tag_values(event, "p") if len(tag) > 1]


@unittest.skipUnless(ENABLED, "L3 runs only with BUZZ_SYNC_L3=1 and the localstack up")
class SyncMentionsL3Test(unittest.TestCase):
    maxDiff = None
    stack: Stack

    @classmethod
    def setUpClass(cls):
        cls.stack = Stack()
        # registered first, so it runs after the members are removed (class cleanups run last-in first-out)
        cls.addClassCleanup(shutil.rmtree, cls.stack.homes, ignore_errors=True)
        cls.humans: dict[str, str] = {}
        for name in ("dev", "maintainer", "bot", "outsider", "extra"):
            cls.humans[name] = cls.fresh_member(join=True)
        cls.ghost = cls.fresh_member(join=False)  # a key that never joined the Channel
        cls.usernames = {role: cls.stack.users[role]["username"] for role in ("dev", "maintainer", "bot", "outsider")}

    @classmethod
    def fresh_member(cls, *, join: bool) -> str:
        sys.path.insert(0, str(REF_SCRIPTS))
        try:
            import nostrkit as nk  # noqa: PLC0415 - repo helper
        finally:
            sys.path.remove(str(REF_SCRIPTS))
        while True:
            key = secrets.token_bytes(32)
            if 1 <= int.from_bytes(key, "big") < nk.n:
                break
        pubkey = nk.pubkey_xonly(key).hex()  # the secret is discarded: these members never sign anything
        if join:
            cls.stack.ensure_member(pubkey)
            cls.addClassCleanup(
                cls.stack.buzz, "owner", "channels", "remove-member", "--channel", cls.stack.channel,
                "--pubkey", pubkey,
            )
        return pubkey

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bsls-l3-mentions-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.case = self._testMethodName.split("_")[1]
        self.stamp = f"{self.case}x{secrets.token_hex(3)}"  # x-joined: never an issue-branch whitelist match
        self.started_unix = int(time.time()) - 5
        self.since = iso(utc_now() - dt.timedelta(minutes=1))

    # ── helpers ─────────────────────────────────────────────────────────────

    def people(self, *names: str, **explicit: str) -> dict[str, str]:
        """GitLab username -> Buzz pubkey for the named localstack users (plus explicit overrides)."""

        mapped = {self.usernames[name]: self.humans[name] for name in names}
        mapped.update(explicit)
        return mapped

    def sync(self, people: dict[str, str]):
        config = self.stack.config(self.since, people=people)
        run = self.stack.run_sync(config, self.tmp)
        self.assertEqual((run.returncode, run.status), (0, "ok"), run.describe())
        self.assertFalse(run.result.get("summary_requests"), "digest placement must stay retired")
        return run

    def gl(self, role: str, method: str, path: str, body=None, params=None):
        return self.stack.ok(role, method, f"{self.stack.base}/{path}", params, body)

    def thread(self, kind: str, iid: int) -> tuple[str, list[dict]]:
        bindings = self.stack.bindings(kind, iid)
        self.assertEqual(len(bindings), 1, bindings)
        root = bindings[0]["root_event_id"]
        return root, self.stack.publisher_thread(root)

    def containing(self, events: list[dict], needle: str) -> dict:
        found = [event for event in events if needle in str(event.get("content") or "")]
        self.assertEqual(len(found), 1, f"{needle!r} in {[first_line(e)[:90] for e in events]}")
        return found[0]

    def note(self, role: str, kind: str, iid: int, body: str) -> dict:
        noun = "issues" if kind == "issue" else "merge_requests"
        return self.gl(role, "POST", f"{noun}/{iid}/notes", {"body": body})

    def ready_mr(self, title: str, reviewers=("maintainer",), extra_files=None) -> dict:
        branch = f"feature/l3-{self.stamp}"
        files = {f"src/l3_{self.stamp}.py": "print(1)\n", **(extra_files or {})}
        sha = self.stack.branch_with_files(branch, files, f"feat: {title}")
        mr = self.stack.create_mr(branch, title, reviewers)
        wait_for("MR head sha", lambda: self.stack.mr(mr["iid"]).get("sha") == sha, 60)
        return {**mr, "sha": sha, "branch": branch}

    def merge(self, iid: int) -> None:
        wait_for("MR mergeable", lambda: self.gl("maintainer", "GET", f"merge_requests/{iid}",
                                                 params={"with_merge_status_recheck": "true"})
                 .get("detailed_merge_status") == "mergeable", 90)
        self.gl("maintainer", "PUT", f"merge_requests/{iid}/merge")
        wait_for("MR merged", lambda: self.stack.mr(iid).get("state") == "merged", 90)

    def has_ci(self, ref: str) -> bool:
        status, _, _ = self.stack.api("bot", "GET", f"{self.stack.base}/repository/files/.gitlab-ci.yml",
                                      {"ref": ref})
        return status == 200

    def desk_messages_since_start(self) -> list[dict]:
        return self.stack.publisher_channel_messages(self.started_unix)

    # ── Issues ──────────────────────────────────────────────────────────────

    def test_001_new_issue_tags_mapped_assignees_and_a_new_assignee_tags_only_that_person(self):
        """L3-GIS-030 新 Issue 首个事实只 @ 已映射的 assignee（未映射的不 @）；之后换成新的 assignee 只 @ 新的人。

        GitLab CE 每个 Issue 只有一个 assignee：「新增」按替换执行。
        """
        users = self.stack.users
        unmapped_first = self.stack.create_issue(f"L3 {self.stamp} a", assignee_ids=[users["dev"]["id"]])
        mapped_first = self.stack.create_issue(f"L3 {self.stamp} b", assignee_ids=[users["maintainer"]["id"]])
        people = self.people("maintainer")  # the dev user is not mapped
        self.sync(people)
        root_a, desk_a = self.thread("issue", unmapped_first["iid"])
        self.assertEqual(ordered_p_tags(desk_a[1]), [], first_line(desk_a[1]))
        self.assertIn(f"assignees {self.usernames['dev']}", "\n".join(lines(desk_a[1])))
        _, desk_b = self.thread("issue", mapped_first["iid"])
        self.assertEqual(ordered_p_tags(desk_b[1]), [self.humans["maintainer"]], first_line(desk_b[1]))

        self.gl("dev", "PUT", f"issues/{unmapped_first['iid']}", {"assignee_ids": [users["maintainer"]["id"]]})
        self.sync(people)
        desk_a = self.stack.publisher_thread(root_a)
        self.assertEqual(ordered_p_tags(desk_a[-1]), [self.humans["maintainer"]], first_line(desk_a[-1]))
        count = len(desk_a)
        self.sync(people)
        self.assertEqual(len(self.stack.publisher_thread(root_a)), count, "a rerun must send nothing")

    def test_002_closed_new_issue_tags_nobody(self):
        """L3-GIS-031 第一次同步时已关闭的 Issue 不 @ assignee。"""
        issue = self.stack.create_issue(f"L3 {self.stamp} closed", assignee_ids=[self.stack.users["maintainer"]["id"]])
        self.gl("dev", "PUT", f"issues/{issue['iid']}", {"state_event": "close"})
        self.sync(self.people("maintainer"))
        _, desk = self.thread("issue", issue["iid"])
        self.assertTrue(all(ordered_p_tags(event) == [] for event in desk), [ordered_p_tags(e) for e in desk])

    def test_003_issue_comment_tags_named_member_assignee_and_author_but_never_the_rest(self):
        """L3-GIS-032 Issue 评论 → 点名成员、assignee、作者；评论人自己、@all/@everyone、nostr:、非项目成员一律零 p tag。"""
        users = self.stack.users
        issue = self.stack.create_issue(f"L3 {self.stamp} comment", assignee_ids=[users["maintainer"]["id"]])
        people = self.people("dev", "maintainer", "bot", "outsider")
        self.sync(people)
        root, _ = self.thread("issue", issue["iid"])
        sys.path.insert(0, str(REF_SCRIPTS))
        try:
            import nostrkit as nk  # noqa: PLC0415
        finally:
            sys.path.remove(str(REF_SCRIPTS))
        npub = nk.bech32_encode("npub", bytes.fromhex(self.stack.owner["pubkey"]))
        note = self.note("maintainer", "issue", issue["iid"], "\n".join([
            f"please look @{self.usernames['bot']} and @{self.usernames['maintainer']} (that is me)",
            f"@all @everyone nostr:{npub} @{self.usernames['outsider']} @nobody-{self.stamp} @localstack-owner",
            f"mail x@{self.usernames['dev']}.example and `@{self.usernames['dev']}` in code",
        ]))
        self.sync(people)
        desk = self.stack.publisher_thread(root)
        message = self.containing(desk, f"[note:{note['id']}]")
        # named first (bot), then assignees (maintainer = commenter, dropped), then the author (dev)
        self.assertEqual(ordered_p_tags(message), [self.humans["bot"], self.humans["dev"]], first_line(message))
        content = message["content"]
        self.assertNotIn("nostr:npub", content)
        self.assertIn("＠all", content)
        self.assertNotIn(self.stack.owner["pubkey"], ordered_p_tags(message))
        self.assertNotIn(self.humans["outsider"], ordered_p_tags(message))

    # ── Merge requests ──────────────────────────────────────────────────────

    def test_004_mr_comment_tags_named_author_and_reviewer_minus_the_commenter(self):
        """L3-GIS-033 MR 评论 → 点名成员、作者、reviewers（去评论人）。"""
        mr = self.ready_mr(f"L3 {self.stamp} comment")  # author dev, reviewer maintainer
        people = self.people("dev", "maintainer", "bot")
        self.sync(people)
        root, _ = self.thread("mr", mr["iid"])
        note = self.note("maintainer", "mr", mr["iid"], f"LGTM cc @{self.usernames['bot']}")
        self.sync(people)
        message = self.containing(self.stack.publisher_thread(root), f"[note:{note['id']}]")
        self.assertEqual(ordered_p_tags(message), [self.humans["bot"], self.humans["dev"]], first_line(message))

        own = self.note("dev", "mr", mr["iid"], "thanks, will fix")
        self.sync(people)
        message = self.containing(self.stack.publisher_thread(root), f"[note:{own['id']}]")
        self.assertEqual(ordered_p_tags(message), [self.humans["maintainer"]], "the commenter (author) is dropped")

    def test_005_attention_budget_is_three_and_duplicates_collapse(self):
        """L3-GIS-034 超过 3 人按预算截断、去重：点名 4 位不同成员（含重复点名），只发前 3 位，无重复 p tag。"""
        users = self.stack.users
        # the fixture has four human members (root, bot, dev, maintainer); with the commenter dropped only three
        # remain, so the outsider joins the project for this case (and leaves again) to overflow the budget
        self.gl("maintainer", "POST", "members", {"user_id": users["outsider"]["id"], "access_level": 20})
        self.addCleanup(self.stack.ok, "maintainer", "DELETE", f"{self.stack.base}/members/{users['outsider']['id']}")
        mr = self.ready_mr(f"L3 {self.stamp} budget")
        people = self.people("dev", "maintainer", "bot", "outsider", root=self.humans["extra"])
        self.sync(people)
        root, _ = self.thread("mr", mr["iid"])
        named = [self.usernames["bot"], "root", self.usernames["outsider"], self.usernames["dev"]]
        body = " ".join(f"@{name}" for name in [named[0], named[0], *named[1:]])
        note = self.note("maintainer", "mr", mr["iid"], body)
        self.sync(people)
        message = self.containing(self.stack.publisher_thread(root), f"[note:{note['id']}]")
        self.assertEqual(ordered_p_tags(message),
                         [self.humans["bot"], self.humans["extra"], self.humans["outsider"]], first_line(message))

    def test_006_merged_and_closed_tag_the_author_unless_the_author_did_it(self):
        """L3-GIS-035 MR 合并/关闭 → @ 作者；作者自己关闭则零 @；第一次同步就已合并的 MR 零 @。"""
        people = self.people("dev", "maintainer")
        merged = self.ready_mr(f"L3 {self.stamp} merged")
        self.sync(people)
        root, _ = self.thread("mr", merged["iid"])
        self.merge(merged["iid"])
        self.sync(people)
        message = self.containing([e for e in self.stack.publisher_thread(root) if "[state:merged]" in first_line(e)],
                                  "[state:merged]")
        self.assertEqual(ordered_p_tags(message), [self.humans["dev"]], first_line(message))

        self.stamp += "c"
        closed = self.ready_mr(f"L3 {self.stamp} closed")
        self.sync(people)
        root, _ = self.thread("mr", closed["iid"])
        self.gl("maintainer", "PUT", f"merge_requests/{closed['iid']}", {"state_event": "close"})
        wait_for("MR closed", lambda: self.stack.mr(closed["iid"]).get("state") == "closed", 60)
        self.sync(people)
        message = self.containing([e for e in self.stack.publisher_thread(root) if "[state:closed]" in first_line(e)],
                                  "[state:closed]")
        self.assertEqual(ordered_p_tags(message), [self.humans["dev"]], first_line(message))

        self.stamp += "s"
        own = self.ready_mr(f"L3 {self.stamp} self-closed")
        self.sync(people)
        root, _ = self.thread("mr", own["iid"])
        self.gl("dev", "PUT", f"merge_requests/{own['iid']}", {"state_event": "close"})
        wait_for("MR closed", lambda: self.stack.mr(own["iid"]).get("state") == "closed", 60)
        self.sync(people)
        message = self.containing([e for e in self.stack.publisher_thread(root) if "[state:closed]" in first_line(e)],
                                  "[state:closed]")
        self.assertEqual(ordered_p_tags(message), [], "the author closed it: nobody to tell")

        self.stamp += "m"
        late = self.ready_mr(f"L3 {self.stamp} already merged")
        self.merge(late["iid"])
        self.sync(people)
        _, desk = self.thread("mr", late["iid"])
        self.assertTrue(all(ordered_p_tags(event) == [] for event in desk))

    def test_007_approval_tags_the_author(self):
        """L3-GIS-036 MR 被批准 → @ 作者（批准人不是作者）。"""
        mr = self.ready_mr(f"L3 {self.stamp} approve")
        people = self.people("dev", "maintainer")
        self.sync(people)
        root, _ = self.thread("mr", mr["iid"])
        self.gl("maintainer", "POST", f"merge_requests/{mr['iid']}/approve")
        self.sync(people)
        desk = self.stack.publisher_thread(root)
        approvals = [e for e in desk if "events: event-" in "\n".join(lines(e)) or "[events:event-" in e["content"]]
        self.assertEqual(len(approvals), 1, [first_line(e)[:80] for e in desk])
        self.assertEqual(ordered_p_tags(approvals[0]), [self.humans["dev"]], first_line(approvals[0]))

    def test_008_failed_mr_pipeline_tags_the_trigger_user_only(self):
        """L3-GIS-037 MR 流水线失败 → @ 触发人。"""
        extra = {".gitlab-ci.yml": CI_YAML} if not self.has_ci("main") else None
        mr = self.ready_mr(f"L3 {self.stamp} pipeline", extra_files=extra)
        iid = mr["iid"]
        pipeline = wait_for("MR pipeline", lambda: next(iter(
            self.gl("bot", "GET", f"merge_requests/{iid}/pipelines") or []), None), 90)
        people = self.people("dev", "maintainer")
        self.sync(people)
        root, _ = self.thread("mr", iid)
        self.assertEqual(self.stack.rails_drop_pipeline(pipeline["id"])["dropped"], ["test:unit"])
        detail = wait_for("pipeline failed", lambda: (lambda d: d if d.get("status") == "failed" else None)(
            self.gl("bot", "GET", f"pipelines/{pipeline['id']}")), 120, 2)
        trigger = detail["user"]["username"]
        self.sync(people)
        message = self.containing(self.stack.publisher_thread(root), f"[events:pipeline-{pipeline['id']}-failed]")
        self.assertEqual(ordered_p_tags(message), [people[trigger]] if trigger in people else [], first_line(message))
        self.assertIn(trigger, {self.usernames["dev"], self.usernames["maintainer"]})

    # ── Top-level notices ───────────────────────────────────────────────────

    def test_009_failed_default_branch_pipeline_and_failed_deployment_tag_their_user(self):
        """L3-GIS-038 默认分支流水线失败 → @ 触发人；部署失败 → @ 部署人；均是顶层通知。"""
        if not self.has_ci("main"):
            self.gl("maintainer", "POST", "repository/commits", {
                "branch": "main", "commit_message": "ci: main pipeline for L3 mention cases",
                "actions": [{"action": "create", "file_path": ".gitlab-ci.yml", "content": MAIN_CI}]})
        else:
            ci = self.gl("bot", "GET", "repository/files/.gitlab-ci.yml", params={"ref": "main"})
            text = base64.b64decode(ci["content"]).decode("utf-8")
            if 'CI_PIPELINE_SOURCE == "api"' not in text:
                if text != CI_YAML:
                    self.skipTest("main has a CI file this case does not own")
                self.gl("maintainer", "POST", "repository/commits", {
                    "branch": "main", "commit_message": "ci: let the L3 mention cases create a main pipeline",
                    "actions": [{"action": "update", "file_path": ".gitlab-ci.yml", "content": MAIN_CI}]})
        people = self.people("dev", "maintainer")
        pipeline = self.gl("maintainer", "POST", "pipeline", params={"ref": "main"})
        self.assertEqual(self.stack.rails_drop_pipeline(pipeline["id"])["dropped"], ["test:unit"])
        wait_for("pipeline failed", lambda: self.gl("bot", "GET", f"pipelines/{pipeline['id']}")
                 .get("status") == "failed", 120, 2)
        head = self.gl("bot", "GET", "repository/commits/main")["id"]
        self.gl("dev", "POST", "deployments", {
            "environment": f"l3-{self.stamp}", "sha": head, "ref": "main", "tag": False, "status": "failed"})
        self.sync(people)
        desk = self.desk_messages_since_start()
        pipeline_note = self.containing(desk, f"[events:pipeline-{pipeline['id']}-failed]")
        self.assertEqual(ordered_p_tags(pipeline_note), [self.humans["maintainer"]], first_line(pipeline_note))
        self.assertEqual(tag_values(pipeline_note, "e"), [], "a default-branch failure is a top-level notice")
        deployment = self.containing(desk, f"l3-{self.stamp}")
        self.assertEqual(ordered_p_tags(deployment), [self.humans["dev"]], first_line(deployment))

    def test_010_broadcast_notices_and_quiet_types_never_tag(self):
        """L3-GIS-039 tag / release / milestone / 即将过期的 token 有通知但零 @；wiki、push、feature flag 根本不发。"""
        people = self.people("dev", "maintainer", "bot")
        tag = f"v0.0.0-l3-{self.stamp}"
        self.gl("dev", "POST", "repository/tags", {"tag_name": tag, "ref": "main"})
        self.gl("dev", "POST", "releases", {"tag_name": tag, "name": f"L3 {self.stamp}", "description": "l3"})
        self.gl("dev", "POST", "milestones", {"title": f"L3 {self.stamp}"})
        self.gl("maintainer", "POST", "access_tokens", {
            "name": f"l3-{self.stamp}", "scopes": ["read_api"], "access_level": 10,
            "expires_at": (dt.date.today() + dt.timedelta(days=3)).isoformat()})
        # nothing below produces a message at all (policy 2026-09-18: NOTIFIED_OBJECTS)
        self.gl("dev", "POST", "wikis", {"title": f"l3-{self.stamp}", "content": "wiki page"})
        self.stack.branch_with_files(f"feature/l3-push-{self.stamp}", {f"src/p_{self.stamp}.py": "print(0)\n"}, "feat: push")
        self.sync(people)
        desk = self.desk_messages_since_start()
        # this case's own objects all carry the stamp (tag, release, milestone and token names); messages of
        # other cases' objects that fall in the same scan window are judged by their own case
        mine = [e for e in desk if self.stamp in str(e.get("content"))]
        self.assertGreaterEqual(len(mine), 4, [first_line(e)[:80] for e in desk])
        self.assertTrue(all(ordered_p_tags(event) == [] for event in mine),
                        [(first_line(e)[:80], ordered_p_tags(e)) for e in mine if ordered_p_tags(e)])
        for word in (f"l3-push-{self.stamp}", "wiki page"):
            self.assertFalse([e for e in desk if word in str(e.get("content"))], word)

    # ── Nobody who should not be tagged ─────────────────────────────────────

    def test_011_unmapped_non_member_and_agent_people_are_never_tagged(self):
        """L3-GIS-040 映射到 Agent（频道 bot 角色）或未加入频道的 pubkey、未映射的人：零 p tag，且同步不失败。"""
        mr = self.ready_mr(f"L3 {self.stamp} nobody")  # author dev, reviewer maintainer
        people = {
            self.usernames["dev"]: self.stack.role_pubkey,  # an agent
            self.usernames["maintainer"]: self.ghost,  # mapped, but not in the Channel
        }  # the bot user stays unmapped
        self.sync(people)
        root, desk = self.thread("mr", mr["iid"])
        note = self.note("maintainer", "mr", mr["iid"], f"cc @{self.usernames['bot']} @{self.usernames['dev']}")
        self.sync(people)
        desk = self.stack.publisher_thread(root)
        self.assertTrue(all(ordered_p_tags(event) == [] for event in desk),
                        [(first_line(e)[:80], ordered_p_tags(e)) for e in desk])
        self.containing(desk, f"[note:{note['id']}]")


if __name__ == "__main__":
    unittest.main()
