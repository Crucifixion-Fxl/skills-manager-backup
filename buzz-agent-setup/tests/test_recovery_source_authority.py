"""Real Nostr source signatures through the production controller, fake HTTP only."""
import copy
from pathlib import Path
import tempfile
import unittest

import test_agent_recovery as journal_fixtures
import test_recovery_relay as signed
from agent_recovery import RecoveryStore, recover

MEMBER_KEY = "4" * 64
MEMBER = signed.wire._signer_pubkey(MEMBER_KEY)


class OriginalAuthorityTests(unittest.TestCase):
    add = signed.RelayTests.add
    http = signed.RelayTests.http

    def setUp(self):
        signed.RelayTests.setUp(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "recovery.sqlite3"
        self.current = journal_fixtures.snapshot("00000000-0000-0000-0000-000000000099")
        self.current["agent_pubkey"] = signed.AGENT
        self.current["runtime_policy"]["owner"] = signed.OWNER
        self.old = copy.deepcopy(self.current)
        self.old["generation"] = "00000000-0000-0000-0000-000000000098"
        self.bind(self.root)

    def bind(self, event, effective=None):
        route = dict(channel=signed.CHANNEL, root=self.root["id"])
        source = dict(event_id=event["id"], signed_author=event["pubkey"], kind="signed-event", route=route)
        if effective is not None:
            source.update(kind="relay-workflow", effective_author=effective)
        turn = f"00000000-0000-0000-0000-{len(self.old['active']) + 1:012d}"
        self.old["active"][turn] = [route]
        self.old["triggers"][turn] = [event["id"]]
        self.old["receipts"][event["id"]] = "active"
        self.old["input_sources"][event["id"]] = [source]
        return source

    def source_request(self, *, key=MEMBER_KEY, extra=(), effective=None):
        event = self.add(key, 9, [["h", signed.CHANNEL], ["e", self.root["id"], "", "root"],
                                ["e", self.root["id"], "", "reply"], *extra], "original task")
        self.bind(event, effective)
        return event

    def roster(self, role="member"):
        self.events = [e for e in self.events if e["kind"] != 39002]
        self.add(signed.RELAY_KEY, 39002, signed.member_tags() + [["p", MEMBER, "", role]], "")

    def policy_mode(self, mode, allowlist=()):
        self.events = [e for e in self.events if e["kind"] != 30177]
        self.add(signed.OWNER_KEY, 30177, [["d", signed.AGENT]], signed.json.dumps({"respond_to": mode}))
        self.current["runtime_policy"].update(respond_to=mode, allowlist=list(allowlist))

    def run_recovery(self):
        with RecoveryStore(self.db) as store:
            return recover(store, self.transport, agent_name="test-dev", agent_pubkey=signed.AGENT,
                           relay="https://relay.invalid", current=self.current, snapshots=[self.old], runtime_verified=True)

    def delivered_work(self):
        return {work for e in self.events if e["content"] == "@test-dev continue"
                for tag in e["tags"] if tag[0] == "recovery" for work in tag[3:]}

    def assert_denied_subset(self, source_event):
        result = self.run_recovery()
        self.assertEqual(result["continued"], 1, "valid owner work in the same Thread must still resume")
        self.assertEqual(self.delivered_work(), {self.root["id"]}, "owner continuation laundered denied original work")
        self.assertTrue(result["errors"])
        notices = [e for e in self.events if e["kind"] == 9 and "原请求" in e["content"]]
        self.assertTrue(notices, "denied work must have an actionable original-Thread notice")
        self.assertTrue(all(["e", self.root["id"], "", "reply"] in e["tags"]
                            and not any(t[0] == "p" for t in e["tags"]) for e in notices))
        self.assertEqual(self.old["receipts"][source_event["id"]], "active")

    def test_owner_positive_control(self):
        result = self.run_recovery()
        events = result.pop("continued_events")
        self.assertEqual(result, {"continued": 1, "errors": []})
        self.assertEqual(len(events), 1)
        self.assertEqual(self.delivered_work(), {self.root["id"]})

    def test_removed_requester_cannot_borrow_owner_membership(self):
        self.policy_mode("anyone")
        self.assert_denied_subset(self.source_request())

    def test_member_outside_actual_people_policy_is_not_owner_authority(self):
        self.roster()
        self.assert_denied_subset(self.source_request())

    def test_anyone_accepts_current_member_positive_control(self):
        self.roster()
        self.policy_mode("anyone")
        request = self.source_request()
        result = self.run_recovery()
        events = result.pop("continued_events")
        self.assertEqual(result, {"continued": 1, "errors": []})
        self.assertEqual(len(events), 1)
        self.assertEqual(self.delivered_work(), {self.root["id"], request["id"]})

    def test_allowlist_accepts_current_member_but_not_unlisted_member(self):
        self.roster()
        self.policy_mode("allowlist")
        request = self.source_request()
        self.assert_denied_subset(request)
        self.current["runtime_policy"]["allowlist"] = [MEMBER]
        self.assertEqual(self.run_recovery()["continued"], 1, "denied work must remain retryable after permission restoration")
        self.assertIn(request["id"], self.delivered_work())
        self.assertEqual(sum("启动成功" in e["content"] for e in self.events), 1,
                         "changing authorized subset must not duplicate Thread startup notices")

    def test_owner_attestation_does_not_bypass_an_explicit_allowlist(self):
        """Code review P1 on !1043: recovery_relay.py's owner-attestation
        fallback recognizes an actor who IS the owner under an alternate,
        cryptographically-attested identity -- that is what owner-only mode
        means. allowlist mode promises a specific, closed set of pubkeys;
        it must not accept an owner-attested actor the owner never actually
        put on this Agent's list, or the allowlist's guarantee is silently
        weaker than configured."""
        self.roster()
        self.policy_mode("allowlist")
        self.add(MEMBER_KEY, 0, [signed.auth_tag(agent=MEMBER)], "{}")
        self.assert_denied_subset(self.source_request())

    def test_guest_requester_is_not_a_writable_group_member(self):
        self.roster("guest")
        self.policy_mode("anyone")
        self.assert_denied_subset(self.source_request())

    def test_invalid_original_signature_never_uses_private_metadata_as_proof(self):
        self.roster()
        self.policy_mode("anyone")
        request = self.source_request()
        request["content"] = "tampered"
        self.assert_denied_subset(request)

    def test_original_event_must_match_the_persisted_signer(self):
        self.roster()
        self.policy_mode("anyone")
        request = self.source_request()
        self.old["input_sources"][request["id"]][0]["signed_author"] = signed.OWNER
        self.assert_denied_subset(request)

    def test_valid_sibling_attestation_uses_current_owner_policy(self):
        self.roster("bot")
        self.add(MEMBER_KEY, 0, [signed.auth_tag(agent=MEMBER)], "{}")
        request = self.source_request()
        result = self.run_recovery()
        events = result.pop("continued_events")
        self.assertEqual(result, {"continued": 1, "errors": []})
        self.assertEqual(len(events), 1)
        self.assertEqual(self.delivered_work(), {self.root["id"], request["id"]})

    def test_claimed_sibling_without_auth_signature_is_not_owner(self):
        self.roster("bot")
        self.add(MEMBER_KEY, 0, [["auth", signed.OWNER, "", "0" * 128]], "{}")
        self.assert_denied_subset(self.source_request())

    def test_workflow_effective_requester_must_still_be_a_member(self):
        self.policy_mode("anyone")
        request = self.source_request(key=signed.RELAY_KEY, effective=MEMBER, extra=[
            ["buzz:workflow", "true"], ["buzz:workflow-owner", MEMBER], ["buzz:workflow-mention", signed.AGENT]])
        self.assert_denied_subset(request)

    def test_workflow_positive_control_preserves_verified_effective_requester(self):
        self.roster()
        self.policy_mode("allowlist", [MEMBER])
        request = self.source_request(key=signed.RELAY_KEY, effective=MEMBER, extra=[
            ["buzz:workflow", "true"], ["buzz:workflow-owner", MEMBER], ["buzz:workflow-mention", signed.AGENT]])
        result = self.run_recovery()
        events = result.pop("continued_events")
        self.assertEqual(result, {"continued": 1, "errors": []})
        self.assertEqual(len(events), 1)
        self.assertEqual(self.delivered_work(), {self.root["id"], request["id"]})

    def test_forged_workflow_owner_cannot_become_effective_requester(self):
        self.roster()
        request = self.source_request(effective=signed.OWNER, extra=[
            ["buzz:workflow", "true"], ["buzz:workflow-owner", signed.OWNER], ["buzz:workflow-mention", signed.AGENT]])
        self.assert_denied_subset(request)


if __name__ == "__main__":
    unittest.main()
