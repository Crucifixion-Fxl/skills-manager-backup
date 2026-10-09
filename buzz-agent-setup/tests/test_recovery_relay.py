"""Real signatures at an injected HTTP boundary; no invitation workflow involved."""
import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import buzz_feishu_group_sync as wire
from recovery_relay import RecoveryRelay, RouteBlocked

OWNER_KEY, AGENT_KEY, RELAY_KEY = "1" * 64, "2" * 64, "3" * 64
OWNER, AGENT, RELAY = map(wire._signer_pubkey, (OWNER_KEY, AGENT_KEY, RELAY_KEY))
CHANNEL = "00000000-0000-0000-0000-000000000001"


def auth_tag(agent=AGENT, owner_key=OWNER_KEY, conditions=""):
    digest = hashlib.sha256(f"nostr:agent-auth:{agent}:{conditions}".encode()).digest()
    signature = wire.sync.nk.schnorr_sign(digest, bytes.fromhex(owner_key), bytes(32)).hex()
    return ["auth", wire._signer_pubkey(owner_key), conditions, signature]


def member_tags(owner_role="owner", agent_role="bot"):
    return [["d", CHANNEL], ["p", OWNER, "", owner_role], ["p", AGENT, "", agent_role]]


class RelayTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.now = dt.datetime(2026, 9, 24, tzinfo=dt.timezone.utc)
        self.root = self.add(OWNER_KEY, 9, [["h", CHANNEL]], "original task")
        self.add(RELAY_KEY, 39002, member_tags(), "")
        self.profile = self.add(AGENT_KEY, 0, [auth_tag()], "{}")
        self.policy = self.add(OWNER_KEY, 30177, [["d", AGENT]], '{"respond_to":"owner-only"}')
        self.calls = []
        self.transport = RecoveryRelay("wss://relay.invalid", OWNER_KEY, RELAY, http=self.http, now=lambda: self.now)

    def add(self, key, kind, tags, content):
        event = wire.sign_event(key, kind, tags, content, int(self.now.timestamp()))
        self.events.append(event)
        return event

    def http(self, url, headers, timeout, body=None):
        self.calls.append((url, headers, body))
        data = json.loads(body)
        if url.endswith("/events"):
            self.assertTrue(wire._nip01_event_verified(data))
            self.events.append(data)
            return 200, json.dumps({"accepted": True, "event_id": data["id"]}).encode()
        self.assertTrue(url.endswith("/query"))
        rows = []
        for query in data:
            for event in self.events:
                if "ids" in query and event["id"] not in query["ids"]:
                    continue
                if "kinds" in query and event["kind"] not in query["kinds"]:
                    continue
                if "authors" in query and event["pubkey"] not in query["authors"]:
                    continue
                if any(not any(t[:2] == [key[1:], val] for t in event["tags"] for val in values)
                       for key, values in query.items() if key.startswith("#")):
                    continue
                rows.append(copy.deepcopy(event))
        return 200, json.dumps(rows).encode()

    def test_signed_original_thread_membership_and_owner_policy(self):
        self.transport.validate_route(CHANNEL, self.root["id"], AGENT)
        event = self.transport.sign("@test-dev continue", [["h", CHANNEL], ["p", AGENT]])
        self.transport.publish(event)
        self.assertEqual(self.transport.lookup(event["id"]), event)
        self.assertTrue(all(url.startswith("https://relay.invalid/") for url, _, _ in self.calls))
        self.assertTrue(all(headers["Authorization"].startswith("Nostr ") for _, headers, _ in self.calls))

    def test_cross_channel_root_cannot_authorize_a_send(self):
        with self.assertRaises(ValueError):
            self.transport.validate_route("00000000-0000-0000-0000-000000000002", self.root["id"], AGENT)

    def test_invalid_root_signature_is_not_trusted(self):
        self.root["content"] = "tampered"
        with self.assertRaises(ValueError):
            self.transport.validate_route(CHANNEL, self.root["id"], AGENT)

    def test_unsigned_or_wrong_relay_membership_is_not_authority(self):
        self.events = [e for e in self.events if e["kind"] != 39002]
        self.add(OWNER_KEY, 39002, member_tags(), "")
        with self.assertRaises(ValueError):
            self.transport.validate_route(CHANNEL, self.root["id"], AGENT)

    def test_removed_agent_has_safe_visible_cause_without_rejoining(self):
        self.events = [e for e in self.events if e["kind"] != 39002]
        self.add(RELAY_KEY, 39002, member_tags()[:2], "")
        with self.assertRaises(RouteBlocked) as caught:
            self.transport.validate_route(CHANNEL, self.root["id"], AGENT)
        self.assertEqual(caught.exception.code, "agent_not_member")
        self.assertTrue(caught.exception.notice_allowed)

    def test_owner_without_access_does_not_send_private_thread_notification(self):
        self.events = [e for e in self.events if e["kind"] != 39002]
        self.add(RELAY_KEY, 39002, [member_tags()[0], member_tags()[2]], "")
        with self.assertRaises(RouteBlocked) as caught:
            self.transport.validate_route(CHANNEL, self.root["id"], AGENT)
        self.assertFalse(caught.exception.notice_allowed)

    def test_disabled_agent_policy_is_visible_and_never_overridden(self):
        self.events.remove(self.policy)
        self.add(OWNER_KEY, 30177, [["d", AGENT]], '{"respond_to":"nobody"}')
        with self.assertRaises(RouteBlocked) as caught:
            self.transport.validate_route(CHANNEL, self.root["id"], AGENT)
        self.assertEqual(caught.exception.code, "agent_not_responding")

    def test_unverified_lookup_is_not_relay_acceptance(self):
        event = self.transport.sign("continue", [["h", CHANNEL]])
        self.events.append({**event, "sig": "0" * 128})
        with self.assertRaises(ValueError):
            self.transport.lookup(event["id"])

    def test_relay_origin_disallows_credentials_query_fragment_and_plain_remote_http(self):
        for origin in ("https://user:pass@relay.invalid", "https://relay.invalid/?token=secret",
                       "https://relay.invalid/#fragment", "http://relay.invalid"):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                RecoveryRelay(origin, OWNER_KEY, RELAY)

    def test_reply_is_not_accepted_as_the_original_thread_root(self):
        """L2-REC-AUTH-001 Canonical route, not merely a same-channel event."""
        for markers in ([['e', self.root['id'], '', 'reply']],
                        [['e', self.root['id'], '', 'root'], ['e', self.root['id'], '', 'reply']]):
            with self.subTest(markers=markers):
                reply = self.add(OWNER_KEY, 9, [["h", CHANNEL], *markers], "a reply, not the root")
                with self.assertRaises(ValueError):
                    self.transport.validate_route(CHANNEL, reply["id"], AGENT)

    def test_lone_root_marker_remains_top_level_like_native_nip10(self):
        """L2-REC-AUTH-002 Positive control for native root-only semantics."""
        top = self.add(OWNER_KEY, 9, [["h", CHANNEL], ["e", self.root["id"], "", "root"]], "top-level")
        self.transport.validate_route(CHANNEL, top["id"], AGENT)

    def test_conflicting_or_malformed_thread_markers_cannot_authorize_recovery(self):
        for markers in ([['e', 'a' * 64, '', 'root'], ['e', 'b' * 64, '', 'root']],
                        [['e', 'broken', '', 'reply']],
                        [['e', self.root['id'], '', 'root'], ['e', self.root['id'], '', 'root']]):
            with self.subTest(markers=markers):
                root = self.add(OWNER_KEY, 9, [["h", CHANNEL], *markers], "ambiguous")
                with self.assertRaises(ValueError):
                    self.transport.validate_route(CHANNEL, root["id"], AGENT)

    def test_listed_non_bot_agent_is_blocked_with_actionable_notice(self):
        for role in ("owner", "admin", "member", "guest"):
            with self.subTest(role=role):
                self.events = [e for e in self.events if e["kind"] != 39002]
                self.add(RELAY_KEY, 39002, member_tags(agent_role=role), "")
                with self.assertRaises(RouteBlocked) as caught:
                    self.transport.validate_route(CHANNEL, self.root["id"], AGENT)
                self.assertEqual(caught.exception.code, "agent_not_bot")
                self.assertTrue(caught.exception.notice_allowed)
                self.assertIn("owner", caught.exception.reason)

    def test_guest_owner_has_no_authority_to_send_a_private_notice(self):
        self.events = [e for e in self.events if e["kind"] != 39002]
        self.add(RELAY_KEY, 39002, member_tags(owner_role="guest"), "")
        with self.assertRaises(RouteBlocked) as caught:
            self.transport.validate_route(CHANNEL, self.root["id"], AGENT)
        self.assertFalse(caught.exception.notice_allowed)

    def test_malformed_membership_never_falls_back_to_an_older_valid_roster(self):
        self.now += dt.timedelta(seconds=1)
        for tags in ([["d", CHANNEL], ["p", OWNER], ["p", AGENT]],
                     member_tags() + [["p", AGENT, "", "member"]],
                     member_tags(agent_role="unknown"), member_tags() + [["d", CHANNEL]]):
            with self.subTest(tags=tags):
                bad = self.add(RELAY_KEY, 39002, tags, "")
                try:
                    with self.assertRaises(ValueError):
                        self.transport.validate_route(CHANNEL, self.root["id"], AGENT)
                finally:
                    self.events.remove(bad)

    def test_claimed_owner_requires_a_valid_unambiguous_nip_oa_attestation(self):
        self.events.remove(self.profile)
        valid = auth_tag()
        for tags in ([["auth", OWNER]], [["auth", OWNER, "", "0" * 128]],
                     [valid, valid], [auth_tag(agent=OWNER)],
                     [auth_tag(conditions="created_at<01")]):
            with self.subTest(tags=tags):
                profile = self.add(AGENT_KEY, 0, tags, "{}")
                try:
                    with self.assertRaises(RouteBlocked) as caught:
                        self.transport.validate_route(CHANNEL, self.root["id"], AGENT)
                    self.assertEqual(caught.exception.code, "agent_owner_unverified")
                    self.assertTrue(caught.exception.notice_allowed)
                finally:
                    self.events.remove(profile)

    def test_policy_json_and_target_tags_must_not_be_ambiguous(self):
        self.events.remove(self.policy)
        for tags, content in (([["d", AGENT]], '{"respond_to":"nobody","respond_to":"anyone"}'),
                              ([["d", AGENT], ["d", OWNER]], '{"respond_to":"anyone"}'),
                              ([["d", AGENT, "extra"]], '{"respond_to":"anyone"}')):
            with self.subTest(content=content, tags=tags):
                policy = self.add(OWNER_KEY, 30177, tags, content)
                try:
                    with self.assertRaises(RouteBlocked):
                        self.transport.validate_route(CHANNEL, self.root["id"], AGENT)
                finally:
                    self.events.remove(policy)


if __name__ == "__main__":
    unittest.main()
