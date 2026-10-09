"""Oracle for the L4-148 receipt: the Feishu group <-> Buzz channel membership experience (engineering/skills#148).

Each case in references/feishu-member-sync-l4.md has one `assert_*` below. Buzz events must be complete signed
NIP-01 events and are re-verified here; Feishu records and local observations (PIDs, subscription time, Feishu
member lists) are read back by the collector. A case is either `passed` with its evidence or `not_run` with a
reason; `not_run` never counts as complete.
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import buzz_feishu_group_sync as fgs  # noqa: E402

REQUIRED_CASES = tuple(f"L4-148-{n:02d}" for n in range(1, 10))
MEMBER_PROVENANCE = ("feishu-member-op", "feishu-member-stream", "feishu-member-seq")
JOIN_RE = re.compile(r"buzz-join:v1 (JOIN-[0-9a-f]{8})")

_verified: dict[str, bool] = {}


def _signed(event, label):
    """Schnorr verification is slow in pure Python; cache it on the complete serialized event."""
    key = json.dumps(event, sort_keys=True, ensure_ascii=False)
    if key not in _verified:
        _verified[key] = fgs._nip01_event_verified(event)
    assert _verified[key], f"{label}: id or signature does not verify"
    return event


def _tags(event, name):
    return [tag[1] for tag in event["tags"] if len(tag) > 1 and tag[0] == name]


def _root(event):
    """The Thread root a reply points at (NIP-10 marked root, else the single e tag)."""
    marked = [tag[1] for tag in event["tags"] if len(tag) > 3 and tag[0] == "e" and tag[3] == "root"]
    if marked:
        return marked[0]
    plain = _tags(event, "e")
    return plain[0] if len(plain) == 1 else None


def _join_id(request):
    """The request's own header: `send_once` appends it as the last line, so anything after it is not a real request."""
    lines = request["content"].splitlines()
    found = JOIN_RE.fullmatch(lines[-1]) if lines else None
    assert found, "request: buzz-join:v1 header is not the last line"
    return found.group(1)


def _in_channel(event, meta, label):
    assert _tags(event, "h") == [meta["channel"]], f"{label}: not in the test channel"


def _member_event(event, meta, kind, pubkey, label, *, role=None, provenance=True):
    _signed(event, label)
    assert event["kind"] == kind, f"{label}: expected kind {kind}, got {event['kind']}"
    _in_channel(event, meta, label)
    p_tags = _tags(event, "p")
    assert p_tags and p_tags[0] == pubkey, f"{label}: first p tag is not the expected member"
    if role is not None:
        assert _tags(event, "role") == [role], f"{label}: expected role={role}"
    if provenance:
        assert event["pubkey"] in meta["signer_pubkeys"], f"{label}: not signed by the group-sync signer"
        missing = [name for name in MEMBER_PROVENANCE if not _tags(event, name)]
        assert not missing, f"{label}: missing feishu-member provenance tags {missing}"


def _feishu_in_group(message, meta, label):
    assert message["chat_id"] == meta["feishu_chat"], f"{label}: not in the Feishu group under test"


def _agent_message(event, meta, label, *, channel=None):
    _signed(event, label)
    assert event["kind"] == 9, f"{label}: not a chat message"
    assert event["pubkey"] == meta["agent"]["pubkey"], f"{label}: not signed by the agent"
    assert _tags(event, "h") == [channel or meta["channel"]], f"{label}: not in the test channel"


def _mirror(event, meta, label):
    _signed(event, label)
    assert event["pubkey"] in meta["mirror_pubkeys"], f"{label}: not signed by a trusted mirror"
    _in_channel(event, meta, label)


def _approval_chain(case, meta, approval, label):
    """Shared tail of 02/03: owner approval mirrored -> real restart -> subscribed -> activation reply."""
    request = _signed(case["request_event"], f"{label} request")
    join = _join_id(request)
    _mirror(approval, meta, f"{label} mirror approval")
    assert _tags(approval, "feishu-author") == [meta["owner"]["pubkey"]], \
        f"{label}: mirrored approval feishu-author is not the owner"
    assert _tags(approval, "join") == [join], f"{label}: mirrored approval does not name {join}"
    restart = case["restart"]
    assert restart["old_main_pid"] != restart["new_main_pid"], f"{label}: agent restart not observed"
    assert restart["restarted_at"] >= approval["created_at"], f"{label}: restart happened before approval"
    active = case["active_event"]
    _agent_message(active, meta, f"{label} activation")
    assert restart["restarted_at"] <= restart["subscribed_at"] <= active["created_at"], \
        f"{label}: activation announced before the new process subscribed"
    assert _root(active) == request["id"], f"{label}: activation is not in the request Thread"
    assert active["content"].startswith("已开通"), f"{label}: activation text"
    assert active["content"].rstrip().endswith(f"buzz-join:v1 {join} active"), f"{label}: activation header"


def assert_join_request(case, meta):
    agent = meta["agent"]
    assert agent["app_id"] not in case["feishu_bots_before"], "01: agent already in the Feishu group"
    assert agent["app_id"] in case["feishu_bots_after"], "01: agent bot not in the Feishu group after the pull"
    add = case["add_event"]
    _member_event(add, meta, 9000, agent["pubkey"], "01 add", role="bot")
    request = case["request_event"]
    _agent_message(request, meta, "01 request")
    assert add["created_at"] <= request["created_at"], "01: request before the membership event"
    assert _root(request) is None, "01: request must start its own Thread"
    join = _join_id(request)
    lines = request["content"].splitlines()
    assert f"/approve {join}" in lines, "01: /approve command is not on its own line"
    assert meta["owner"]["pubkey"] in _tags(request, "p"), "01: owner not mentioned on the request"
    _feishu_in_group(case["request_feishu"], meta, "01 request")
    assert f"/approve {join}" in case["request_feishu"]["text"].splitlines(), \
        "01: request did not reach the Feishu group with the command on its own line"
    intro = case["intro_feishu"]
    _feishu_in_group(intro, meta, "01 intro")
    assert intro["sender_type"] == "app" and intro["sender_id"] in {agent["app_id"], meta["desk_app_id"]}, \
        "01: intro not sent by the agent or Desk bot"
    assert agent["name"] in intro["text"], "01: intro does not name the agent"


def assert_checkmark_approval(case, meta):
    reaction = case["feishu_reaction"]
    assert reaction["message_id"] == case["request_feishu"]["message_id"], "02: ✅ not on the Feishu request"
    assert reaction["emoji_type"] in {"CheckMark", "DONE"}, "02: not a ✅ reaction"
    assert reaction["operator_id"] == meta["owner"]["feishu_open_id"], "02: ✅ was not the owner's"
    mirror = case["mirror_reaction"]
    _mirror(mirror, meta, "02 mirror reaction")
    assert mirror["kind"] == 7 and _tags(mirror, "e") == [case["request_event"]["id"]], \
        "02: mirrored ✅ does not target the request"
    _approval_chain(case, meta, mirror, "02")
    active_feishu = case["active_feishu"]
    _feishu_in_group(active_feishu, meta, "02 activation")
    assert active_feishu["root_id"] == case["request_feishu"]["message_id"], \
        "02: activation not in the Feishu request Thread"


def assert_command_approval(case, meta):
    command = case["feishu_command"]
    join = _join_id(case["request_event"])
    assert command["sender_id"] == meta["owner"]["feishu_open_id"], "03: command not sent by the owner"
    assert command["root_id"] == case["request_feishu"]["message_id"], "03: command is not a Thread reply"
    assert not command["mentions"], "03: command must work without @"
    assert command["text"].strip() == f"/approve {join}", "03: command text"
    mirror = case["mirror_command"]
    _mirror(mirror, meta, "03 mirror command")  # verified again in _approval_chain with owner / JOIN tags
    assert mirror["kind"] == 9 and _root(mirror) == case["request_event"]["id"], \
        "03: mirrored command not in the request Thread"
    _approval_chain(case, meta, mirror, "03")


def _feedback(event, trigger, meta, label, *, text, thread):
    _agent_message(event, meta, label)
    assert _root(event) == thread, f"{label}: feedback not in the same Thread"
    assert text in event["content"], f"{label}: feedback does not say {text}"
    assert f"feedback-{trigger['id']}" in event["content"], f"{label}: feedback not bound to its trigger"


def assert_rejections(case, meta):
    request = _signed(case["request_event"], "04 request")
    other = case["non_owner_reaction"]
    _mirror(other, meta, "04 non-owner reaction")
    assert _tags(other, "feishu-author") and _tags(other, "feishu-author") != [meta["owner"]["pubkey"]], \
        "04: non-owner reaction must name a non-owner feishu-author"
    _feedback(case["non_owner_feedback"], other, meta, "04 non-owner", text="审批未生效", thread=request["id"])
    stray = case["stray_command"]
    _mirror(stray, meta, "04 stray command")
    assert _root(stray) != request["id"], "04: stray command must be outside the request Thread"
    _feedback(case["stray_feedback"], stray, meta, "04 stray", text="审批未生效", thread=stray["id"])
    early = case["pending_mention"]
    _signed(early, "04 pending mention")
    assert meta["agent"]["pubkey"] in _tags(early, "p"), "04: pending mention does not @ the agent"
    _feedback(case["pending_feedback"], early, meta, "04 pending", text="等待 owner", thread=early["id"])
    assert not case["active_events_before_owner"], "04: activated without the owner's approval"
    assert case["state_after"] == "REQUESTED" and case["rounds_observed"] >= 2, \
        "04: request state must stay REQUESTED over at least two rounds"


def assert_mention_after_activation(case, meta):
    mention = case["mirror_mention"]
    _mirror(mention, meta, "05 mirrored mention")
    assert meta["agent"]["pubkey"] in _tags(mention, "p"), "05: mirrored mention lost the agent p tag"
    reply = case["agent_reply"]
    _agent_message(reply, meta, "05 agent reply")
    thread = _root(mention) or mention["id"]
    assert _root(reply) == thread and reply["created_at"] >= mention["created_at"], \
        "05: agent reply not in the same Thread"
    assert case["feishu_mention"]["mentions"], "05: Feishu message did not @ the agent"
    reply_feishu = case["reply_feishu"]
    _feishu_in_group(reply_feishu, meta, "05 reply")
    assert reply_feishu["root_id"] == case["feishu_mention"]["message_id"], "05: reply not in the Feishu Thread"


def assert_busy_not_interrupted(case, meta):
    task = _signed(case["task_event"], "06 task")
    task_channel = _tags(task, "h")
    assert len(task_channel) == 1 and task_channel != [meta["channel"]], \
        "06: the busy task must run in another channel the agent already serves"
    done = case["task_done_event"]
    _agent_message(done, meta, "06 task done", channel=task_channel[0])
    assert _root(done) == task["id"] and "DONE" in done["content"], "06: task did not finish in its Thread"
    assert case["busy_at_approval"] is True, "06: agent was not observed busy when the approval arrived"
    assert task["created_at"] < case["approval_at"] < done["created_at"], "06: approval did not arrive mid-task"
    restart = case["restart"]
    assert restart["old_main_pid"] != restart["new_main_pid"], "06: restart not observed"
    assert restart["restarted_at"] >= done["created_at"], "06: agent restarted before its task finished (interrupted)"


def assert_agent_membership(case, meta):
    agent = meta["agent"]
    remove = case["feishu_remove"]
    assert agent["app_id"] in remove["bots_before"] and agent["app_id"] not in remove["bots_after"], \
        "07: agent was not removed in Feishu"
    _member_event(remove["event"], meta, 9001, agent["pubkey"], "07 Feishu remove -> 9001")
    assert remove["event"]["created_at"] >= remove["at"], "07: 9001 predates the Feishu removal"
    add, drop = case["buzz_add"], case["buzz_remove"]
    _member_event(add["event"], meta, 9000, agent["pubkey"], "07 Buzz add", role="bot", provenance=False)
    assert agent["app_id"] in add["bots_after"] and add["observed_at"] >= add["event"]["created_at"], \
        "07: Buzz add did not reach the Feishu group"
    _member_event(drop["event"], meta, 9001, agent["pubkey"], "07 Buzz remove", provenance=False)
    assert agent["app_id"] not in drop["bots_after"] and drop["observed_at"] >= drop["event"]["created_at"], \
        "07: Buzz remove did not reach the Feishu group"


def assert_person_membership(case, meta):
    person = meta["colleague"]
    add, drop = case["feishu_add_person"], case["feishu_remove_person"]
    assert person["feishu_open_id"] in add["users_after"], "08: person not added in Feishu"
    _member_event(add["event"], meta, 9000, person["pubkey"], "08 Feishu add person", role="member")
    assert add["event"]["created_at"] >= add["at"], "08: 9000 predates the Feishu add"
    assert person["feishu_open_id"] not in drop["users_after"], "08: person still in the Feishu group"
    _member_event(drop["event"], meta, 9001, person["pubkey"], "08 Feishu remove person")
    assert drop["event"]["created_at"] >= drop["at"], "08: 9001 predates the Feishu removal"


def assert_reactions(case, meta):
    target = _signed(case["target_event"], "09 target")
    reaction = case["feishu_reaction"]
    assert reaction["message_id"] == case["target_feishu"]["message_id"], "09: Feishu reaction not on the target"
    mirror = case["mirror_reaction"]
    _mirror(mirror, meta, "09 mirror reaction")
    assert mirror["kind"] == 7 and _tags(mirror, "e") == [target["id"]], "09: mirrored reaction target"
    assert _tags(mirror, "feishu-author") == [meta["colleague"]["pubkey"]], \
        "09: mirrored reaction does not name the human feishu-author"
    withdraw = case["mirror_withdraw"]
    _mirror(withdraw, meta, "09 withdraw")
    assert withdraw["kind"] == 5 and _tags(withdraw, "e") == [mirror["id"]] \
        and withdraw["created_at"] >= case["feishu_withdrawn_at"], "09: Feishu withdraw did not reach Buzz"
    buzz = _signed(case["buzz_reaction"], "09 Buzz reaction")
    assert buzz["kind"] == 7 and _tags(buzz, "e") == [target["id"]], "09: Buzz reaction target"
    back = case["feishu_reaction_from_buzz"]
    assert back["message_id"] == case["target_feishu"]["message_id"] and back["operator_type"] == "app" \
        and back["at"] >= buzz["created_at"], "09: Buzz reaction did not reach the Feishu message"


CASES = {
    "L4-148-01": assert_join_request,
    "L4-148-02": assert_checkmark_approval,
    "L4-148-03": assert_command_approval,
    "L4-148-04": assert_rejections,
    "L4-148-05": assert_mention_after_activation,
    "L4-148-06": assert_busy_not_interrupted,
    "L4-148-07": assert_agent_membership,
    "L4-148-08": assert_person_membership,
    "L4-148-09": assert_reactions,
}


def assert_receipt(receipt, *, require_complete=True):
    meta, cases = receipt["meta"], receipt["cases"]
    assert meta["suite"] == "L4-148", "not an L4-148 receipt"
    for case_id in REQUIRED_CASES:
        assert case_id in cases, f"{case_id}: missing"
        case = cases[case_id]
        if case["status"] == "not_run":
            assert case.get("reason"), f"{case_id}: not_run without a reason"
            assert not require_complete, f"{case_id}: not_run ({case['reason']})"
            continue
        assert case["status"] == "passed", f"{case_id}: unknown status {case['status']!r}"
        try:
            CASES[case_id](case, meta)
        except (KeyError, TypeError) as exc:
            raise AssertionError(f"{case_id}: malformed evidence ({type(exc).__name__}: {exc})") from exc
