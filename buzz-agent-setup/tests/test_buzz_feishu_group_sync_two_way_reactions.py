"""表情双向同步，以及飞书里同意 agent 入群（ADR-0020，engineering/skills#148）。

以前只有 agent 在 Buzz 上打的表情，由它自己的 bot 打到飞书；人的表情两个方向都不同步，飞书里的表情根本不读。ADR-0020（缺省
`reaction_sync: "two_way"`）：

- Buzz → 飞书：频道里的人（以及本机没有凭据、被代发的 agent）打的表情，由本频道 Desk bot 打到飞书对应消息上。飞书对同一条消息、
  同一个表情、同一个操作者只留一个，所以按「消息 × 表情」计数：第一个人打时加，最后一个人撤回时去掉。有自己 bot 的 agent 照旧。
- 飞书 → Buzz：双方都有副本的消息登记在 `rwatch` 里（24 小时；入群申请 7 天），每轮用 `im reactions batch_query` 读一次；已绑定的人打的
  表情由镜像身份在 Buzz 对应事件上打同一个（kind 7，带 `["feishu-author", pubkey]`，镜像要带 x-auth-tag），撤回时镜像发 kind 5。
- 防回声：bot 在飞书上打的表情不回 Buzz；镜像（以及别的机器的镜像）在 Buzz 上打的表情不回飞书。
- 飞书里在入群申请的话题下回复 `/approve JOIN-<id>` / `/deny JOIN-<id>`：除了照常转进 Buzz，镜像还在 Buzz 的申请消息上打 ✅ / ❌，带
  `feishu-author` 和 `["join", "JOIN-<id>"]`——入群申请脚本认这个（`buzz messages send` 加不了标签，所以同意走表情这条路）。

`reaction_sync: "agents_only"` 是原来的行为。沿用 test_buzz_feishu_group_sync.py 的 FakeWorld / Env / 常量。用例编号从 860 起。
"""

import json
import sys
import unittest
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_buzz_feishu_group_sync as base  # noqa: E402
from test_buzz_feishu_group_sync import (  # noqa: E402
    AGENT2_PK, AGENT_APP, AGENT_PK, ALICE_OPEN, ALICE_PK, BOB_OPEN, BOB_PK, CAROL_OPEN, FGS, LARK_CLI, MIRROR_PK, NOW,
    OWNER_APP, T0, TmpCase, deletion_event, event, eid, fmsg, reaction_event, ts, union_of,
)
from test_buzz_feishu_group_sync_two_way_members import AUTH_TAG, DESK_APP, TwoWay, mirror_policy, OTHER_MIRROR_PK  # noqa: E402
from test_buzz_feishu_group_sync_agent_directory import FOREIGN_OWNER_PK, profile  # noqa: E402


def setUpModule():
    base.setUpModule()


def tearDownModule():
    base.tearDownModule()


JOIN_ID = "JOIN-0a1b2c3d"


def at(minutes):
    return NOW + timedelta(minutes=minutes)


def feishu_reaction(emoji_type, operator_open=None, app=None):
    if app is not None:
        return {"emoji_type": emoji_type, "operator": {"operator_id": app, "operator_type": "app"}}
    return {"emoji_type": emoji_type, "operator": {"operator_id": union_of(operator_open), "operator_type": "user"}}


# ================================ L1 ================================


class Maps(unittest.TestCase):
    def test_approve_and_deny_have_feishu_emojis_both_ways(self):
        """L1-FGS-860: 缺省对照表有 ✅ → DONE、❌ → CrossMark；反查表把 emoji_type 换回表情（THUMBSUP 回 👍，不回 "+"）。"""
        self.assertEqual(FGS.DEFAULT_REACTION_MAP["❌"], "CrossMark")
        reverse = FGS.reverse_reaction_map(FGS.DEFAULT_REACTION_MAP)
        self.assertEqual((reverse["DONE"], reverse["CrossMark"], reverse["THUMBSUP"]), ("✅", "❌", "👍"))


class Routing(unittest.TestCase):
    def route(self, author, **kw):
        args = dict(agent_apps={AGENT_PK: AGENT_APP}, human_pubkeys={ALICE_PK, BOB_PK}, agent_pubkeys={AGENT_PK, AGENT2_PK},
                    reaction_map=FGS.DEFAULT_REACTION_MAP)
        args.update(kw)
        return FGS.route_buzz_reaction(reaction_event(1, author, eid(1), "👍"), **args)

    def test_people_and_relayed_agents_react_through_the_desk_bot(self):
        """L1-FGS-861: 在 relay_authors 里的作者（人、被代发的 agent）由 Desk bot 打；有自己 bot 的 agent 照旧用自己的；镜像是 echo，
        别的机器的镜像是 other_mirror；不给 relay_authors（agents_only）就是原来的 reaction_human。"""
        relay = dict(relay_authors={ALICE_PK, BOB_PK, AGENT2_PK}, proxy_app_id=DESK_APP, mirror_pubkey=MIRROR_PK,
                     other_mirrors={OTHER_MIRROR_PK})
        self.assertEqual(self.route(ALICE_PK, **relay), (DESK_APP, "THUMBSUP"))
        self.assertEqual(self.route(AGENT2_PK, **relay), (DESK_APP, "THUMBSUP"))
        self.assertEqual(self.route(AGENT_PK, **relay), (AGENT_APP, "THUMBSUP"))
        self.assertEqual(self.route(MIRROR_PK, **relay), "echo")
        self.assertEqual(self.route(OTHER_MIRROR_PK, **relay), "other_mirror")
        self.assertEqual(self.route(ALICE_PK), "reaction_human")


class ReactionDetails(unittest.TestCase):
    def test_it_asks_in_batches_and_follows_pages(self):
        """L1-FGS-862: `im reactions batch_query`（user 身份，user_id_type 按身份模式，每条最多 10 个——接口上限）；has_more 的消息接着用
        page_token 读；某条消息查失败就不在结果里（它的撤回无从判断）。"""
        calls = []

        def runner(argv, **kw):
            calls.append(argv)
            data = json.loads(argv[argv.index("--data") + 1])
            params = json.loads(argv[argv.index("--params") + 1])
            assert argv[argv.index("--as") + 1] == "user" and params == {"user_id_type": "union_id"}
            assert data["page_size_per_message"] == 10
            q = data["queries"]
            if q[0].get("page_token"):
                details = [{"message_id": "om_a", "has_more": False,
                            "message_reaction_items": [{"emoji_type": "OK", "operator": {"operator_id": "on_x", "operator_type": "user"}}]}]
                fails = []
            else:
                details = [{"message_id": "om_a", "has_more": True, "page_token": "p2",
                            "message_reaction_items": [{"emoji_type": "DONE", "operator": {"operator_id": "on_x", "operator_type": "user"}}]}]
                fails = [{"message_id": "om_b", "fail_reason": "no_permission"}]
            out = {"ok": True, "data": {"success_msg_reaction_details": details, "fail_msg_reaction_details": fails}}
            return base.subprocess.CompletedProcess(argv, 0, json.dumps(out), "")
        lark = FGS.LarkCli(LARK_CLI, {}, runner=runner)
        got = lark.reaction_details(["om_a", "om_b"], "union_id")
        self.assertEqual(got, {"om_a": [("user", "on_x", "DONE"), ("user", "on_x", "OK")]})
        self.assertEqual(calls[0][1:4], ["im", "reactions", "batch_query"])


# ================================ L2-1 : Buzz → 飞书 ================================


class BuzzToFeishu(TwoWay):
    def start(self, **overrides):
        w = self.world()
        w.events = [event(eid(1), BOB_PK, "Buzz 里的话", created_at=T0)]
        env = self.env(**overrides)
        env.round(w)
        return w, env, env.state()["b2f"][eid(1)]

    def test_a_persons_reaction_is_put_there_by_the_desk_bot(self):
        """L2-1-FGS-870: 人在 Buzz 上打 👍 → 飞书那条副本上出现 THUMBSUP，由本频道 Desk bot 打。"""
        w, env, copy = self.start()
        w.events.append(reaction_event(1, ALICE_PK, eid(1), "👍", created_at=ts(NOW) + 20))
        report = env.round(w, now=at(5))
        self.assertEqual(w.reaction_set(), {(copy, "THUMBSUP", DESK_APP)})
        self.assertEqual(report["reactions_added"], 1)

    def test_two_people_share_one_feishu_reaction_until_the_last_withdraws(self):
        """L2-1-FGS-871: Alice、Bob 都打 👍：飞书上只有 Desk bot 的一个；Alice 撤回后它还在（Bob 的还在）；Bob 也撤回，才去掉。"""
        w, env, copy = self.start()
        w.events += [reaction_event(1, ALICE_PK, eid(1), "👍", created_at=ts(NOW) + 20),
                     reaction_event(2, BOB_PK, eid(1), "👍", created_at=ts(NOW) + 21)]
        env.round(w, now=at(5))
        self.assertEqual(w.reaction_set(), {(copy, "THUMBSUP", DESK_APP)})
        w.events.append(deletion_event(1, ALICE_PK, eid(0x9001), created_at=ts(at(6))))
        env.round(w, now=at(7))
        self.assertEqual(w.reaction_set(), {(copy, "THUMBSUP", DESK_APP)})
        w.events.append(deletion_event(2, BOB_PK, eid(0x9002), created_at=ts(at(8))))
        report = env.round(w, now=at(9))
        self.assertEqual(w.reaction_set(), set())
        self.assertEqual(report["reactions_removed"], 1)

    def test_a_relayed_agents_reaction_goes_the_same_way(self):
        """L2-1-FGS-872: 本机没有凭据、被代发的 agent（AGENT2）打的表情也由本频道 Desk bot 打。"""
        w, env, copy = self.start()
        w.events.append(reaction_event(1, AGENT2_PK, eid(1), "👀", created_at=ts(NOW) + 20))
        env.round(w, now=at(5))
        self.assertEqual(w.reaction_set(), {(copy, "GLANCE", DESK_APP)})

    def test_agents_only_keeps_the_old_behaviour(self):
        """L2-1-FGS-873: `reaction_sync: "agents_only"`：人的表情照旧不同步（reaction_human），也不读飞书的表情。"""
        w, env, copy = self.start(reaction_sync="agents_only")
        w.events.append(reaction_event(1, ALICE_PK, eid(1), "👍", created_at=ts(NOW) + 20))
        w.batch_reactions[copy] = [feishu_reaction("THUMBSUP", BOB_OPEN)]
        report = env.round(w, now=at(5))
        self.assertEqual(w.reaction_set(), set())
        self.assertEqual(report["skipped"].get("reaction_human"), 1)
        self.assertEqual(w.batch_reaction_calls, [])

    def test_withdrawal_with_removed_desk_app_fails_one_reaction_and_continues_round(self):
        """L2-1-FGS-874: 旧 Desk app 已移除时，撤销不冒充新 Desk，也不中断同轮其他消息。"""
        w, env, copy = self.start()
        reaction_id = eid(0x9001)
        w.events.append(reaction_event(1, ALICE_PK, eid(1), "👍", created_at=ts(NOW) + 20))
        env.round(w, now=at(5))
        ledger = env.state()
        original = ledger["r2f"][reaction_id]
        self.assertTrue(original.endswith("|desk:" + DESK_APP))
        ledger["r2f"][reaction_id] = original.rsplit("|", 1)[0] + "|desk:cli_removed_desk"
        state_file = env.state_dir / FGS.STATE_FILE
        state_file.write_text(json.dumps(ledger))
        w.events.extend([
            deletion_event(1, ALICE_PK, reaction_id, created_at=ts(at(6))),
            event(eid(2), BOB_PK, "另一条消息", created_at=ts(at(7))),
        ])

        report = env.round(w, now=at(10))

        self.assertEqual(env.state()["r2f"][reaction_id], FGS.FAILED)
        self.assertEqual(report["reactions_failed"], 1)
        self.assertEqual(report["skipped"].get("reaction_sender_unavailable"), 1)
        self.assertEqual(report["to_feishu"], 1)
        self.assertEqual(w.reaction_set(), {(copy, "THUMBSUP", DESK_APP)})
        self.assertNotIn("r2f-del:" + reaction_id, env.state()["attempts"])
        self.assertEqual(env.round(w, now=at(11))["reactions_failed"], 0)


# ================================ L2-1 : 飞书 → Buzz ================================


class FeishuToBuzz(TwoWay):
    def start(self, **overrides):
        w = self.world()
        w.events = [event(eid(1), BOB_PK, "Buzz 里的话", created_at=T0)]
        w.messages = [fmsg("om_in1", ALICE_OPEN, "飞书里的话")]
        env = self.env(**overrides)
        env.round(w)
        return w, env, env.state()["b2f"][eid(1)], env.state()["f2b"]["om_in1"]

    def mirror_reactions(self, w):
        return [e for e in w.relay_writes if e["kind"] == 7]

    def test_a_persons_feishu_reaction_is_put_on_the_buzz_event_by_the_mirror(self):
        """L2-1-FGS-880: 已绑定的人在飞书副本上打 DONE → 镜像在 Buzz 原事件上打 ✅（kind 7，e 指向原事件，feishu-author 是这个人的 pubkey），
        请求带镜像的 x-auth-tag；只打一次（下一轮不重复）。"""
        w, env, copy, _ = self.start()
        w.batch_reactions[copy] = [feishu_reaction("DONE", ALICE_OPEN)]
        report = env.round(w, now=at(5))
        env.round(w, now=at(6))
        (ev,) = self.mirror_reactions(w)
        self.assertEqual((ev["pubkey"], ev["content"]), (MIRROR_PK, "✅"))
        self.assertIn(["e", eid(1)], ev["tags"])
        self.assertIn(["feishu-author", ALICE_PK], ev["tags"])
        self.assertIn(json.loads(AUTH_TAG), ev["tags"])
        (attempt,) = [a for a in w.relay_write_attempts if a["event"]["kind"] == 7]
        self.assertEqual(attempt["auth_tag"], AUTH_TAG)
        self.assertEqual(report["reactions_to_buzz"], 1)

    def test_a_reaction_on_a_feishu_message_lands_on_its_buzz_copy(self):
        """L2-1-FGS-881: 飞书里人发的消息（镜像已转进 Buzz）上的表情，打在 Buzz 里那条镜像消息上。"""
        w, env, _, mirrored = self.start()
        w.batch_reactions["om_in1"] = [feishu_reaction("THUMBSUP", BOB_OPEN)]
        env.round(w, now=at(5))
        (ev,) = self.mirror_reactions(w)
        self.assertEqual((ev["content"], ev["tags"][0]), ("👍", ["e", mirrored]))

    def test_withdrawing_it_in_feishu_withdraws_it_in_buzz(self):
        """L2-1-FGS-882: 飞书里撤回 → 镜像发 kind 5 删掉自己那条 kind 7，只发一次（之后的轮次不再重复）。"""
        w, env, copy, _ = self.start()
        w.batch_reactions[copy] = [feishu_reaction("DONE", ALICE_OPEN)]
        env.round(w, now=at(5))
        (ev,) = self.mirror_reactions(w)
        w.batch_reactions[copy] = []
        report = env.round(w, now=at(6))
        env.round(w, now=at(7))
        (gone,) = [e for e in w.relay_writes if e["kind"] == 5]  # once, not again every round
        self.assertEqual([t for t in gone["tags"] if t[0] != "auth"], [["e", ev["id"]]])  # plus the mirror's auth tag
        self.assertEqual(report["reactions_withdrawn_in_buzz"], 1)

    def test_bots_strangers_and_unknown_emojis_are_left_out(self):
        """L2-1-FGS-883: bot 打的（owner bot、agent bot）不回 Buzz（防回声）；认不出的人（Carol 没绑定）不回；对照表里没有的表情跳过并计数。"""
        w, env, copy, _ = self.start()
        w.batch_reactions[copy] = [feishu_reaction("THUMBSUP", app=OWNER_APP), feishu_reaction("GLANCE", app=AGENT_APP),
                                   feishu_reaction("DONE", CAROL_OPEN), feishu_reaction("SMILE", ALICE_OPEN)]
        report = env.round(w, now=at(5))
        self.assertEqual(self.mirror_reactions(w), [])
        self.assertEqual(report["skipped"].get("reaction_emoji_unmapped"), 1)

    def test_only_recent_messages_are_watched_but_join_requests_for_a_week(self):
        """L2-1-FGS-884: 超过 24 小时的消息不再读表情；带入群申请头（buzz-join:v1 JOIN-…）的消息读 7 天。"""
        w = self.world()
        w.events = [event(eid(1), BOB_PK, "普通消息", created_at=T0),
                    event(eid(2), AGENT_PK, f"请同意我加入本群\nbuzz-join:v1 {JOIN_ID}", created_at=T0)]
        env = self.env()
        env.round(w)
        plain, request = env.state()["b2f"][eid(1)], env.state()["b2f"][eid(2)]
        w.batch_reaction_calls.clear()
        env.round(w, now=NOW + timedelta(days=3))
        asked = {m for call in w.batch_reaction_calls for m in call}
        self.assertIn(request, asked)
        self.assertNotIn(plain, asked)

    def test_the_mirrors_own_reactions_do_not_come_back(self):
        """L2-1-FGS-885: 镜像在 Buzz 上打的表情（从飞书来的）下一轮不会再被打回飞书。"""
        w, env, copy, _ = self.start()
        w.batch_reactions[copy] = [feishu_reaction("DONE", ALICE_OPEN)]
        env.round(w, now=at(5))
        report = env.round(w, now=at(6))
        self.assertEqual(w.reaction_set(), set())
        self.assertGreaterEqual(report["skipped"].get("echo", 0), 1)

    def test_a_lost_answer_does_not_double_the_reaction(self):
        """L2-1-FGS-887: 镜像打表情时 relay 收下了、应答丢了：下一轮重发的是同一个事件，relay 说已有，记为已送达；Buzz 上只有一个，账本记着它。"""
        w, env, copy, _ = self.start()
        w.batch_reactions[copy] = [feishu_reaction("DONE", ALICE_OPEN)]
        w.relay_write_fail = ["lost"]
        env.round(w, now=at(5))
        env.round(w, now=at(6))
        env.round(w, now=at(7))
        mine = [e for e in w.events if e["kind"] == 7 and e["pubkey"] == MIRROR_PK]
        self.assertEqual(len(mine), 1)
        self.assertIn(mine[0]["id"], env.state()["f2r"].values())

    def test_two_people_share_one_mirror_reaction(self):
        """L2-1-FGS-888: 飞书里 Alice、Bob 都打 DONE：relay 对同一身份、同一目标、同一表情只留一个，所以 Buzz 上只有镜像的一个 ✅（不每轮失败）；
        Alice 撤回后它还在，Bob 也撤回才撤。"""
        w, env, copy, _ = self.start()
        w.batch_reactions[copy] = [feishu_reaction("DONE", ALICE_OPEN), feishu_reaction("DONE", BOB_OPEN)]
        report = env.round(w, now=at(5))
        env.round(w, now=at(6))
        self.assertEqual(len([e for e in w.events if e["kind"] == 7 and e["pubkey"] == MIRROR_PK]), 1)
        self.assertEqual(report["reactions_failed"], 0)
        w.batch_reactions[copy] = [feishu_reaction("DONE", BOB_OPEN)]
        env.round(w, now=at(7))
        self.assertEqual([e for e in w.relay_writes if e["kind"] == 5], [])
        w.batch_reactions[copy] = []
        env.round(w, now=at(8))
        self.assertEqual(len([e for e in w.relay_writes if e["kind"] == 5]), 1)

    def test_a_failed_read_is_counted_not_alarmed(self):
        """L2-1-FGS-886: batch_query 失败：记 reactions_failed，不需要关注；读不到的消息不做任何撤回判断。"""
        w, env, copy, _ = self.start()
        w.batch_reactions[copy] = [feishu_reaction("DONE", ALICE_OPEN)]
        env.round(w, now=at(5))
        w.batch_reactions[copy] = []
        w.batch_reaction_fail = ["api"]
        report = env.round(w, now=at(6))
        self.assertGreaterEqual(report["reactions_failed"], 1)
        self.assertFalse(FGS.needs_attention(report))
        self.assertEqual([e for e in w.relay_writes if e["kind"] == 5], [])


# ================================ L2-1 : 飞书里同意入群 ================================


class FeishuApprovals(TwoWay):
    def start(self):
        """agent 在 Buzz 发了入群申请，已镜像到飞书（成了一个话题的根）。"""
        w = self.world()
        w.events = [event(eid(1), AGENT_PK, f"请同意我加入本群\nbuzz-join:v1 {JOIN_ID}", created_at=T0)]
        env = self.env()
        env.round(w)
        return w, env, env.state()["b2f"][eid(1)]

    def reply(self, w, root, text, sender=ALICE_OPEN, n=1):
        w.threads.setdefault(root, []).append(fmsg(f"om_r{n}", sender, text, thread_id="omt_" + root, when=NOW + timedelta(minutes=4)))
        for m in w.messages:
            if m["message_id"] == root:
                m["thread_id"] = "omt_" + root

    def approvals(self, w):
        return [e for e in w.relay_writes if any(t[0] == "join" for t in e["tags"])]

    def test_approve_in_the_thread_goes_out_as_a_tagged_reply(self):
        """L2-1-FGS-890: 在申请的话题里回复 `/approve JOIN-<id>`：这条回复由镜像在进程内签名发出（kind 9，回在申请的话题里），带 feishu-author
        （这个人）和 join 标签（这个申请）——`buzz messages send` 加不了标签；只发一次（不再经 CLI 另发一份），下一轮不重复。
        （不用表情转：relay 对同一身份、同一目标、同一表情只留一个 reaction，别人先点的 ✅ 会把它挡掉。）"""
        w, env, root = self.start()
        self.reply(w, root, f"/approve {JOIN_ID}")
        report = env.round(w, now=at(5))
        env.round(w, now=at(6))
        (ev,) = self.approvals(w)
        self.assertEqual((ev["kind"], ev["pubkey"]), (9, MIRROR_PK))
        self.assertIn(["e", eid(1), "", "reply"], ev["tags"])
        self.assertIn(["feishu-author", ALICE_PK], ev["tags"])
        self.assertIn(["join", JOIN_ID], ev["tags"])
        self.assertIn(json.loads(AUTH_TAG), ev["tags"])  # like the CLI's own events: the mirror's NIP-OA auth tag rides along
        self.assertTrue(ev["content"].endswith(f"/approve {JOIN_ID}"))
        self.assertEqual(sum(f"/approve {JOIN_ID}" in e["content"] for e in w.mirrored() if e["kind"] == 9), 1)
        self.assertEqual(report["approvals_to_buzz"], 1)

    def test_deny_too_and_a_lost_answer_is_not_sent_twice(self):
        """L2-1-FGS-891: `/deny JOIN-<id>` 同样；relay 收下了、应答却丢了：下一轮重发的是同一个事件（同一个 id，relay 说 duplicate），不会多一条。"""
        w, env, root = self.start()
        self.reply(w, root, f"/deny {JOIN_ID}")
        w.relay_write_fail = ["lost"]
        env.round(w, now=at(5))
        env.round(w, now=at(6))
        env.round(w, now=at(7))
        self.assertEqual(len([e for e in w.events if any(t[0] == "join" for t in e["tags"])]), 1)
        (ev,) = [e for e in w.events if any(t[0] == "join" for t in e["tags"])]
        self.assertTrue(ev["content"].endswith(f"/deny {JOIN_ID}"))

    def test_only_an_exact_command_in_a_thread_from_a_member_counts(self):
        """L2-1-FGS-892: 不在话题里、多了别的字、id 格式不对、认不出的人（Carol 没绑定）说的：都不打。"""
        w, env, root = self.start()
        w.messages.append(fmsg("om_top", ALICE_OPEN, f"/approve {JOIN_ID}", when=NOW + timedelta(minutes=4)))
        self.reply(w, root, f"好的 /approve {JOIN_ID}", n=1)
        self.reply(w, root, "/approve JOIN-xyz", n=2)
        self.reply(w, root, f"/approve {JOIN_ID}", sender=CAROL_OPEN, n=3)
        env.round(w, now=at(5))
        self.assertEqual(self.approvals(w), [])


# ================================ L2-1 : 别的机器的镜像 ================================


class OtherMirrorReactions(TwoWay):
    def test_another_mirrors_reactions_stay_where_they_are(self):
        """L2-1-FGS-895: 别的机器的镜像（30177 声明了 feishu.mirror）在 Buzz 上打的表情（从它那个群来的）不打进本机的群。"""
        w = self.world()
        w.members.append({"pubkey": OTHER_MIRROR_PK, "role": "bot"})
        w.relay_events = [profile(OTHER_MIRROR_PK, FOREIGN_OWNER_PK), mirror_policy(FOREIGN_OWNER_PK, OTHER_MIRROR_PK)]
        w.events = [event(eid(1), BOB_PK, "Buzz 里的话", created_at=T0)]
        env = self.env()
        env.round(w)
        w.events.append(reaction_event(1, OTHER_MIRROR_PK, eid(1), "👍", created_at=ts(NOW) + 20))
        report = env.round(w, now=at(5))
        self.assertEqual(w.reaction_set(), set())
        self.assertEqual(report["skipped"].get("other_mirror"), 1)


if __name__ == "__main__":
    unittest.main()
