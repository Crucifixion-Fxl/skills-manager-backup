"""Strict v9 original-work bindings, independent of the controller outbox.

An owner continuation is a delivery, never a new requester. Native snapshots
are private runtime evidence; the signed original and current authority still
require relay readback before publication and at native admission.
"""
from collections import defaultdict
import uuid

from recovery_authority import HEX64
from buzz_feishu_group_sync import _nip01_event_verified
from recovery_attempt import attempt_id
import recovery_lifecycle

DEFERRED_REASONS = {"recovery_source_unverified", "recovery_source_not_member", "recovery_source_policy_denied",
                    "recovery_owner_not_member", "recovery_agent_not_bot", "recovery_channel_not_subscribed",
                    "recovery_authority_changed"}


def deferred(snapshot):
    values = snapshot.get("deferred")
    receipts = snapshot["receipts"]
    if (not isinstance(values, dict) or len(values) > 4096
            or set(values) != {key for key, status in receipts.items() if status == "blocked"}
            or not set(values) <= set(snapshot["recovery_receipts"])):
        raise ValueError("orphan deferred receipt")
    for input_id, value in values.items():
        if (not isinstance(value, dict) or set(value) != {"attempt", "reason", "next_check_at", "checks", "notice"}
                or not isinstance(value["attempt"], str) or not HEX64.fullmatch(value["attempt"])
                or not isinstance(value["reason"], str) or value["reason"] not in DEFERRED_REASONS
                or type(value["next_check_at"]) is not int or not 0 <= value["next_check_at"] <= 253402300799
                or type(value["checks"]) is not int or not 0 <= value["checks"] <= 32):
            raise ValueError("invalid deferred receipt")
        notice(snapshot, input_id, value)


def notice(snapshot, input_id, pending):
    value = pending["notice"]
    if value is None:
        return
    sources = snapshot["input_sources"].get(input_id)
    if (not isinstance(value, dict) or set(value) != {"event", "delivered"}
            or type(value["delivered"]) is not bool
            or not isinstance(sources, list) or not sources):
        raise ValueError("invalid deferred notice")
    event = value["event"]
    channel, root = route(source(sources[0])["route"])
    tags = [["h", channel], ["e", root, "", "root"], ["e", root, "", "reply"],
            ["buzz:recovery-notice", input_id, pending["reason"]]]
    if (not isinstance(event, dict) or not isinstance(event.get("content"), str)
            or len(event["content"].encode()) > 2048
            or event.get("kind") != 9 or event.get("pubkey") != snapshot["agent_pubkey"]
            or event.get("tags") != tags or not _nip01_event_verified(event)):
        raise ValueError("invalid signed deferred notice")


def route(value):
    if (not isinstance(value, dict) or set(value) != {"channel", "root"}
            or not isinstance(value["channel"], str)
            or str(uuid.UUID(value["channel"])) != value["channel"]
            or not isinstance(value["root"], str) or not HEX64.fullmatch(value["root"])):
        raise ValueError("invalid original source route")
    return value["channel"], value["root"]


def source(value):
    if not isinstance(value, dict):
        raise ValueError("invalid original source")
    fields = {"event_id", "signed_author", "kind", "route"}
    if value.get("kind") == "relay-workflow":
        fields.add("effective_author")
    elif value.get("kind") != "signed-event":
        raise ValueError("invalid original source attribution")
    if set(value) != fields or any(not isinstance(value[k], str) or not HEX64.fullmatch(value[k])
                                  for k in fields - {"kind", "route"}):
        raise ValueError("invalid original source identity")
    if value.get("effective_author") == value["signed_author"]:
        raise ValueError("ambiguous original source attribution")
    route(value["route"])
    return value


def validate(snapshot):
    receipts, inputs, triggers = (snapshot.get(key) for key in ("receipts", "input_sources", "triggers"))
    recoveries = snapshot.get("recovery_receipts")
    attempts = snapshot.get("recovery_attempts")
    if (not isinstance(receipts, dict) or not isinstance(inputs, dict) or inputs.keys() != receipts.keys()
            or not isinstance(triggers, dict) or triggers.keys() != snapshot["active"].keys()
            or not isinstance(recoveries, list) or len(recoveries) != len(set(recoveries))
            or not set(recoveries) <= receipts.keys()
            or not isinstance(attempts, dict) or len(attempts) > 8192 or set(attempts) != set(recoveries)):
        raise ValueError("recovery_receipt_inconsistent")
    deferred(snapshot)
    originals, active, terminal = {}, set(), set()
    for input_id, sources in inputs.items():
        if (not isinstance(sources, list) or not 1 <= len(sources) <= 256
                or any(not isinstance(s, dict) for s in sources)):
            raise ValueError("invalid original source set")
        ids = [source(s)["event_id"] for s in sources]
        if ids != sorted(set(ids)) or len({route(s["route"]) for s in sources}) != 1:
            raise ValueError("ambiguous original source set")
        if input_id not in recoveries and ids != [input_id]:
            raise ValueError("ordinary input changed its WorkID")
        if input_id in recoveries:
            channel, root = route(sources[0]["route"])
            expected = attempt_id(snapshot["relay"], snapshot["agent_pubkey"], snapshot["generation"], channel, root, ids)
            if attempts[input_id] != expected or snapshot["deferred"].get(input_id, {}).get("attempt", expected) != expected:
                raise ValueError("invalid recovery attempt binding")
        for item in sources:
            work = item["event_id"]
            if work in originals and originals[work] != item:
                raise ValueError("original source identity changed")
            originals[work] = item
            if receipts[input_id] == "active":
                if work in active:
                    raise ValueError("ambiguous original source ownership")
                active.add(work)
            elif receipts[input_id] in ("completed", "cancelled"):
                terminal.add(work)
    owned_inputs = set()
    for turn, routes in snapshot["active"].items():
        bindings = triggers[turn]
        if (not isinstance(bindings, list) or not 1 <= len(bindings) <= 256
                or any(not isinstance(i, str) or not HEX64.fullmatch(i) for i in bindings)
                or len(bindings) != len(set(bindings)) or owned_inputs.intersection(bindings)
                or any(receipts.get(i) != "active" for i in bindings)):
            raise ValueError("recovery_receipt_inconsistent")
        expected = {route(s["route"]) for i in bindings for s in inputs[i]}
        if not isinstance(routes, list) or not 1 <= len(routes) <= 256 or expected != {route(r) for r in routes}:
            raise ValueError("recovery_receipt_inconsistent")
        owned_inputs.update(bindings)
    if owned_inputs != {i for i, status in receipts.items() if status == "active"}:
        raise ValueError("recovery_receipt_inconsistent")
    recovery_lifecycle.validate(snapshot)
    return originals, active, terminal


def pending(snapshots, current_generation):
    """Deduplicate stable work; completion and current ownership beat old copies."""
    originals, active, terminal, current = {}, set(), set(), set()
    generations = {}
    for snapshot in snapshots:
        generation = snapshot["generation"]
        if generation in generations and generations[generation] != snapshot:
            raise ValueError("ambiguous runtime generation")
        generations[generation] = snapshot
        sources, running, ended = validate(snapshot)
        for work, item in sources.items():
            if work in originals and originals[work] != item:
                raise ValueError("original source identity changed")
            originals[work] = item
        terminal.update(ended)
        (current if generation == current_generation else active).update(running)
    groups = defaultdict(list)
    for work in sorted(active - terminal - current):
        item = originals[work]
        groups[route(item["route"])].append(item)
    return groups
