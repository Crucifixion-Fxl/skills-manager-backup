"""Contract tests for deterministic, low-noise human responsibility mentions."""

from __future__ import annotations

import importlib.util
import re
import unittest
from pathlib import Path


SKILL = Path(__file__).resolve().parents[1]
SCRIPT = SKILL / "scripts" / "buzz_responsible_mentions.py"


def load_module():
    if not SCRIPT.is_file():
        raise AssertionError("buzz_responsible_mentions.py is required")
    spec = importlib.util.spec_from_file_location("buzz_responsible_mentions", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MENTIONS = load_module()

ALICE = "a" * 64
BOB = "b" * 64
CAROL = "c" * 64
DAVE = "d" * 64


def candidate(username: str, source: str = "gitlab.assignees") -> dict[str, str]:
    return {"username": username, "source": source}


def profile(username: str, pubkey: str) -> dict[str, str]:
    return {"name": username, "pubkey": pubkey}


def member(pubkey: str, role: str = "member") -> dict[str, str]:
    return {"pubkey": pubkey, "role": role}


class ResponsibleMentionResolverTest(unittest.TestCase):
    def test_unique_exact_gitlab_username_resolves_current_human_member(self):
        result = MENTIONS.resolve_mentions(
            [candidate("alice")],
            aliases={},
            profiles=[profile("alice", ALICE)],
            members=[member(ALICE)],
        )

        self.assertEqual(result["mentions"], [
            {"username": "alice", "source": "gitlab.assignees", "pubkey": ALICE}
        ])
        self.assertEqual(result["unresolved"], [])

    def test_configured_alias_is_allowed_but_must_still_be_a_human_member(self):
        resolved = MENTIONS.resolve_mentions(
            [candidate("legacy-owner")],
            aliases={"legacy-owner": ALICE},
            profiles=[],
            members=[member(ALICE, "owner")],
        )
        rejected = MENTIONS.resolve_mentions(
            [candidate("legacy-owner")],
            aliases={"legacy-owner": ALICE},
            profiles=[],
            members=[member(ALICE, "bot")],
        )

        self.assertEqual(resolved["mentions"][0]["pubkey"], ALICE)
        self.assertEqual(rejected["mentions"], [])
        self.assertEqual(rejected["unresolved"][0]["reason"], "not_human_member")

    def test_ambiguous_profile_and_non_member_fail_closed(self):
        ambiguous = MENTIONS.resolve_mentions(
            [candidate("alice")],
            aliases={},
            profiles=[profile("alice", ALICE), profile("alice", BOB)],
            members=[member(ALICE), member(BOB)],
        )
        outside = MENTIONS.resolve_mentions(
            [candidate("carol")],
            aliases={},
            profiles=[profile("carol", CAROL)],
            members=[],
        )

        self.assertEqual(ambiguous["unresolved"][0]["reason"], "ambiguous_profile")
        self.assertEqual(outside["unresolved"][0]["reason"], "not_channel_member")

    def test_canvas_sources_are_no_longer_approved(self):
        for source in ("canvas.business_owner", "canvas.action_owner", "canvas.alias"):
            with self.subTest(source=source), self.assertRaises(ValueError):
                MENTIONS.resolve_mentions(
                    [candidate("alice", source)],
                    aliases={}, profiles=[profile("alice", ALICE)], members=[member(ALICE)],
                )

    def test_first_seen_order_deduplicates_pubkeys_and_caps_attention_at_three(self):
        result = MENTIONS.resolve_mentions(
            [
                candidate("alice"),
                candidate("alice", "gitlab.reviewers"),
                candidate("bob"),
                candidate("carol"),
                candidate("dave"),
            ],
            aliases={},
            profiles=[
                profile("alice", ALICE),
                profile("bob", BOB),
                profile("carol", CAROL),
                profile("dave", DAVE),
            ],
            members=[member(ALICE), member(BOB), member(CAROL), member(DAVE)],
        )

        self.assertEqual(
            [item["pubkey"] for item in result["mentions"]],
            [ALICE, BOB, CAROL],
        )
        self.assertEqual(
            [item for item in result["unresolved"] if item["reason"] == "attention_budget"],
            [{"username": "dave", "source": "gitlab.assignees", "reason": "attention_budget"}],
        )

    def test_free_text_and_all_are_not_valid_candidate_sources(self):
        for source, username in (("message.text", "alice"), ("gitlab.comment", "alice"),
                                 ("gitlab.assignees", "all"), ("gitlab.assignees", "@all")):
            with self.subTest(source=source, username=username), self.assertRaises(ValueError):
                MENTIONS.resolve_mentions(
                    [candidate(username, source)],
                    aliases={},
                    profiles=[profile("alice", ALICE)],
                    members=[member(ALICE)],
                )


    def test_pipeline_and_deployment_users_are_structured_sources(self):
        """L1-GIS-223 流水线触发人与部署人来自 GitLab 结构化字段，是合法候选来源。"""
        result = MENTIONS.resolve_mentions(
            [candidate("alice", "gitlab.pipeline_user"), candidate("bob", "gitlab.deployer")],
            aliases={},
            profiles=[profile("alice", ALICE), profile("bob", BOB)],
            members=[member(ALICE), member(BOB)],
        )
        self.assertEqual([item["pubkey"] for item in result["mentions"]], [ALICE, BOB])

    def test_comment_mentions_are_opt_in_for_the_sync_only(self):
        """L1-GIS-224 评论里的 @username 默认仍不是合法来源（Agent 侧门禁不变）；只有 sync 显式放开，且仍受成员/人类/预算约束。"""
        with self.assertRaises(ValueError):
            MENTIONS.resolve_mentions(
                [candidate("alice", "gitlab.comment_mention")],
                aliases={}, profiles=[profile("alice", ALICE)], members=[member(ALICE)],
            )
        opt_in = frozenset({"gitlab.comment_mention"})
        result = MENTIONS.resolve_mentions(
            [candidate("alice", "gitlab.comment_mention"), candidate("bob", "gitlab.comment_mention"),
             candidate("carol", "gitlab.comment_mention"), candidate("dave", "gitlab.comment_mention")],
            aliases={},
            profiles=[profile("alice", ALICE), profile("bob", BOB), profile("carol", CAROL), profile("dave", DAVE)],
            members=[member(ALICE), member(BOB), member(CAROL)],
            extra_sources=opt_in,
        )
        self.assertEqual([item["pubkey"] for item in result["mentions"]], [ALICE, BOB, CAROL])
        self.assertEqual([item["reason"] for item in result["unresolved"]], ["not_channel_member"])
        with self.assertRaises(ValueError):
            MENTIONS.resolve_mentions(
                [candidate("all", "gitlab.comment_mention")],
                aliases={}, profiles=[], members=[], extra_sources=opt_in,
            )


class ChannelRoleTest(unittest.TestCase):
    """Who counts as a human Channel member (skills#136).

    Buzz Channel roles are owner / admin / member / guest / bot. owner, admin and member are people who
    can act on a matter; guest is a restricted role and bot is an Agent, so neither is notified.
    """

    def seat(self, role):
        """A standing seat (`person` locator): the username reaches a pubkey through people_file (aliases)."""
        return MENTIONS.resolve_mentions(
            [candidate("jchen", "people.person")],
            aliases={"jchen": ALICE}, profiles=[], members=[member(ALICE, role)],
        )

    def test_channel_admin_seat_held_by_a_channel_admin_is_notified_like_owner_and_member(self):
        """L1-GIS-233 `channel_admin` 席位上的人在频道里是 admin 角色时照常解析出 mention（owner／member 不变）。"""
        for role in ("owner", "admin", "member"):
            with self.subTest(role=role):
                result = self.seat(role)
                self.assertEqual(result["mentions"], [
                    {"username": "jchen", "source": "people.person", "pubkey": ALICE}
                ])
                self.assertEqual(result["unresolved"], [])

    def test_guest_bot_and_unknown_roles_are_not_human_members(self):
        """L1-GIS-234 guest（受限角色）、bot（Agent）和任何未知或大小写不同的 role 仍是 not_human_member，没有 mention。"""
        for role in ("guest", "bot", "superadmin", "Admin", "OWNER", "", "admin "):
            with self.subTest(role=role):
                result = self.seat(role)
                self.assertEqual(result["mentions"], [])
                self.assertEqual(
                    result["unresolved"],
                    [{"username": "jchen", "source": "people.person", "reason": "not_human_member"}],
                )

    def test_missing_membership_or_role_fails_closed(self):
        """L1-GIS-235 不在频道 → not_channel_member；成员条目缺 role（或 role 不是字符串）是坏快照，整次解析拒绝。"""
        outside = MENTIONS.resolve_mentions(
            [candidate("jchen", "people.person")], aliases={"jchen": ALICE}, profiles=[], members=[member(BOB, "admin")],
        )
        self.assertEqual(outside["mentions"], [])
        self.assertEqual([item["reason"] for item in outside["unresolved"]], ["not_channel_member"])
        for broken in ({"pubkey": ALICE}, {"pubkey": ALICE, "role": None}, {"pubkey": ALICE, "role": 7}):
            with self.subTest(member=broken), self.assertRaises(ValueError):
                MENTIONS.resolve_mentions(
                    [candidate("jchen", "people.person")], aliases={"jchen": ALICE}, profiles=[], members=[broken],
                )

    def test_admin_seats_keep_the_dedupe_and_the_three_person_budget(self):
        """L1-GIS-236 admin 与 owner／member 共用同一个预算：同一个 admin 经两个来源只算一个人；第 4 个人才 attention_budget。"""
        roles = {ALICE: "admin", BOB: "admin", CAROL: "member", DAVE: "owner"}
        members = [member(pubkey, role) for pubkey, role in roles.items()]
        aliases = {"alice": ALICE, "bob": BOB, "carol": CAROL, "dave": DAVE}
        deduped = MENTIONS.resolve_mentions(
            [candidate("alice", "people.person"), candidate("alice", "gitlab.reviewers"),
             candidate("bob", "people.person"), candidate("carol", "gitlab.reviewers")],
            aliases=aliases, profiles=[], members=members,
        )
        self.assertEqual([item["pubkey"] for item in deduped["mentions"]], [ALICE, BOB, CAROL])
        self.assertEqual(deduped["unresolved"], [])
        capped = MENTIONS.resolve_mentions(
            [candidate(name, "people.person") for name in ("alice", "bob", "carol", "dave")],
            aliases=aliases, profiles=[], members=members,
        )
        self.assertEqual([item["pubkey"] for item in capped["mentions"]], [ALICE, BOB, CAROL])
        self.assertEqual(
            capped["unresolved"], [{"username": "dave", "source": "people.person", "reason": "attention_budget"}]
        )

    def test_human_roles_are_exactly_owner_admin_member(self):
        """L1-GIS-237 send gate 与 GitLab 同步共读的常量：owner／admin／member，guest 不放行（jchen 2026-09-21 的决定）。"""
        self.assertEqual(MENTIONS.HUMAN_ROLES, frozenset({"owner", "admin", "member"}))


REPO = SKILL.parents[1]
SKILL_DOCS = (
    SKILL / "SKILL.md",
    SKILL / "references" / "runtime-setup.md",
    SKILL / "references" / "fchac-model.md",
    SKILL / "references" / "scheduled-workflows.md",
    SKILL / "references" / "gitlab-buzz-sync.md",
)
# "owner/member", "owner、member", "owner／member": the pre-#136 two-role list.
OLD_TWO_ROLE_LIST = re.compile(r"owner\s*[/／、]\s*member")


class ResponsibleRoleDocsTest(unittest.TestCase):
    """The documented role list is the code's role list (skills#136); the old two-role wording is gone."""

    def test_docs_state_the_three_human_roles_and_that_guest_and_bot_are_not(self):
        """L1-GIS-245 SKILL／runtime-setup／fchac-model／scheduled-workflows／gitlab-buzz-sync 都写 owner/admin/member，并点明 guest 与 bot 不算。"""
        roles = "/".join(("owner", "admin", "member"))
        self.assertEqual(frozenset(roles.split("/")), MENTIONS.HUMAN_ROLES)
        for path in SKILL_DOCS:
            lines = path.read_text(encoding="utf-8").splitlines()
            with self.subTest(document=path.name):
                # the sentence that lists the human roles is the one that says who is not a human role
                self.assertTrue(
                    any(roles in line and "guest" in line and "bot" in line for line in lines),
                    f"no line of {path.name} lists {roles} together with guest and bot",
                )

    def test_no_document_still_says_only_owner_and_member(self):
        """L1-GIS-246 仓内不再出现「role 只能是 owner/member」一类的两角色写法（含 ADR、测试方案）。"""
        paths = [
            SKILL / "SKILL.md",
            *sorted((SKILL / "references").rglob("*.md")),
            *sorted((REPO / "docs" / "05-adr").glob("*.md")),
            REPO / "docs" / "plans" / "2026-09-13-buzz-agent-setup-gitlab-buzz-sync-test-plan.md",
        ]
        for path in paths:
            with self.subTest(document=path.name):
                self.assertIsNone(OLD_TWO_ROLE_LIST.search(path.read_text(encoding="utf-8")))

    def test_fchac_model_leaves_the_mention_flag_to_the_helper(self):
        """L1-GIS-247 fchac-model 的责任人一节写「helper 内部使用 --mention」：Agent 不自己 --mention，与 SKILL 的调用边界一致（!982 code-review 建议）。"""
        text = (SKILL / "references" / "fchac-model.md").read_text(encoding="utf-8")
        self.assertIn("helper 内部使用 `--mention <pubkey>` 产生显式 `p` tag", text)
        self.assertNotIn("发送使用 `--mention", text)


if __name__ == "__main__":
    unittest.main()
