"""Strict recovery authority parsing; callers must verify complete Nostr events.

No display name, message content, or claimed owner is an authorization input.
NIP-10 resolution follows buzz-core; ambiguous markers fail closed instead of
using the native parser's last-marker fallback on an untrusted recovery route.
"""
import hashlib
import json
import re

from buzz_feishu_group_sync import sync

HEX64 = re.compile(r"[0-9a-f]{64}")
MEMBER_ROLES = frozenset({"owner", "admin", "member", "guest", "bot"})


def runtime_policy(value):
    """Validate native's private snapshot, never infer it from desired env."""
    if (not isinstance(value, dict) or set(value) != {"owner", "respond_to", "allowlist"}
            or not isinstance(value["owner"], str) or not HEX64.fullmatch(value["owner"])
            or value["respond_to"] not in ("anyone", "nobody", "owner-only", "allowlist")):
        raise ValueError("invalid actual runtime response policy")
    people = value["allowlist"]
    if (not isinstance(people, list) or len(people) > 4096
            or any(not isinstance(key, str) or not HEX64.fullmatch(key) for key in people)
            or people != sorted(set(people))
            or value["respond_to"] != "allowlist" and people):
        raise ValueError("invalid private runtime people list")
    return value


def tags(event, name):
    values = event["tags"]
    if not isinstance(values, list) or any(not isinstance(t, list) or not t
                                            or not all(isinstance(v, str) for v in t) for t in values):
        raise ValueError("malformed recovery event tags")
    return [tag for tag in values if tag[0] == name]


def exact_tag(event, name, value):
    if tags(event, name) != [[name, value]]:
        raise ValueError("ambiguous recovery event binding")


def canonical_thread(event, channel):
    if event["kind"] != 9:
        raise ValueError("recovery source is not a message")
    exact_tag(event, "h", channel)
    markers = {}
    for tag in tags(event, "e"):
        if len(tag) < 4 or tag[3] not in ("root", "reply"):
            continue
        if not re.fullmatch(r"[0-9a-fA-F]{64}", tag[1]) or tag[3] in markers:
            raise ValueError("ambiguous recovery Thread markers")
        markers[tag[3]] = tag[1].lower()
    # A lone root marker is top-level, exactly as buzz-core nip10::resolve.
    return markers.get("root", markers["reply"]) if "reply" in markers else event["id"]


def workflow_author(event, relay, agent):
    """Same relay-self/explicit-mention contract as native verified_workflow_owner.

    The caller must verify the complete original event's signature first.
    Untrusted workflow-like tags never override an ordinary signed author.
    """
    if event["kind"] != 9 or event["pubkey"] != relay:
        return None
    owners, mentions = tags(event, "buzz:workflow-owner"), tags(event, "buzz:workflow-mention")
    if (tags(event, "buzz:workflow") != [["buzz:workflow", "true"]]
            or len(owners) != 1 or len(owners[0]) != 2 or not HEX64.fullmatch(owners[0][1])
            or any(len(t) != 2 or not HEX64.fullmatch(t[1]) for t in mentions)
            or len({t[1] for t in mentions}) != len(mentions)
            or ["buzz:workflow-mention", agent] not in mentions):
        return None
    return owners[0][1]


def membership(event, channel):
    exact_tag(event, "d", channel)
    result = {}
    for tag in tags(event, "p"):
        # Relay's signed NIP-29 format is [p, pubkey, relay_url, role].
        if (len(tag) != 4 or not HEX64.fullmatch(tag[1]) or tag[3] not in MEMBER_ROLES
                or tag[1] in result):
            raise ValueError("ambiguous recovery membership")
        result[tag[1]] = tag[3]
    return result


def latest(events):
    """NIP-01 replacement ordering, including deterministic timestamp ties."""
    return min(events, key=lambda e: (-e["created_at"], e["id"]), default=None)


def attested_owner(profile, agent):
    """Verify the NIP-OA credential, not just the claimed owner field.

    Matches buzz-sdk verify_auth_tag's identity contract: validate condition
    syntax and signature. Current permission is a separate policy/roster gate.
    """
    attestations = tags(profile, "auth")
    if len(attestations) != 1 or len(attestations[0]) != 4:
        raise ValueError("ambiguous owner attestation")
    _, owner, conditions, signature = attestations[0]
    if (not HEX64.fullmatch(owner) or owner == agent
            or not re.fullmatch(r"[0-9a-f]{128}", signature)):
        raise ValueError("invalid owner attestation")
    if conditions:
        for clause in conditions.split("&"):
            match = re.fullmatch(r"(kind=|created_at<|created_at>)(0|[1-9][0-9]{0,9})", clause)
            if not match or int(match[2]) > (65535 if match[1] == "kind=" else 4294967295):
                raise ValueError("invalid attestation conditions")
    digest = hashlib.sha256(f"nostr:agent-auth:{agent}:{conditions}".encode()).digest()
    if not sync.nk.schnorr_verify(digest, bytes.fromhex(owner), bytes.fromhex(signature)):
        raise ValueError("owner attestation signature invalid")
    return owner


def policy_body(event, agent):
    exact_tag(event, "d", agent)

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("ambiguous recovery policy JSON")
            result[key] = value
        return result

    def reject_constant(_value):
        raise ValueError("invalid recovery policy JSON")

    body = json.loads(event["content"], object_pairs_hook=unique, parse_constant=reject_constant)
    if not isinstance(body, dict):
        raise ValueError("invalid recovery policy object")
    return body
