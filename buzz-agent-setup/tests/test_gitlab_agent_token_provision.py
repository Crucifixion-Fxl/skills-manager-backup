import argparse
import datetime as dt
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


TESTS = Path(__file__).resolve().parent
SCRIPT = TESTS.parent / "scripts" / "provision_gitlab_agent_token.py"
SPEC = importlib.util.spec_from_file_location("provision_gitlab_agent_token", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class FakeGlab:
    def __init__(
        self, *, admin=True, existing=None, ambiguous_create=False,
        ambiguous_without_visibility=False, interrupt_create=False,
        malformed_create=False, created_overrides=None,
    ):
        self.admin = admin
        self.tokens = list(existing or [])
        self.ambiguous_create = ambiguous_create
        self.ambiguous_without_visibility = ambiguous_without_visibility
        self.interrupt_create = interrupt_create
        self.malformed_create = malformed_create
        self.created_overrides = dict(created_overrides or {})
        self.bot_external = True
        self.ignore_external_write = False
        self.calls = []

    def request_list(self, endpoint):
        self.calls.append(("GET-LIST", endpoint, None, False))
        return list(self.tokens)

    def request(self, endpoint, *, method="GET", payload=None, allow_empty=False):
        self.calls.append((method, endpoint, payload, allow_empty))
        if endpoint == "/user":
            return {"id": 400, "username": "jchen", "is_admin": self.admin}
        if endpoint == "/projects/biz-ops%2Fscm":
            return {"id": 1312, "path_with_namespace": "biz-ops/scm", "visibility": "private"}
        if endpoint == "/projects/1312/access_tokens" and method == "POST":
            created = {
                "id": 963,
                "user_id": 989,
                "name": payload["name"],
                "token": "one-time-test-secret",
                "access_level": payload["access_level"],
                "scopes": payload["scopes"],
                "expires_at": payload["expires_at"],
            }
            created.update(self.created_overrides)
            self.tokens = [{key: value for key, value in created.items() if key != "token"}]
            if self.ambiguous_without_visibility:
                self.tokens = []
                raise MODULE.ProvisionError("simulated ambiguous create without list visibility")
            if self.interrupt_create:
                raise KeyboardInterrupt
            if self.ambiguous_create:
                raise MODULE.ProvisionError("simulated lost create response")
            if self.malformed_create:
                return {"name": created["name"], "token": created["token"]}
            return created
        if endpoint == "/users/989":
            if method == "PUT" and not self.ignore_external_write:
                self.bot_external = payload["external"]
            return {"id": 989, "username": "project_1312_bot_test", "external": self.bot_external, "state": "active"}
        if endpoint == "/projects/1312/access_tokens/963" and method == "DELETE":
            self.tokens = []
            return {}
        raise AssertionError((method, endpoint, payload, allow_empty))


class FakeTokenClient:
    memberships = [{"id": 1312}]
    internal = []

    def __init__(self, host, token):
        if host != "gitlab.addx.ai" or token != "one-time-test-secret":
            raise AssertionError("unexpected token client input")

    def get_object(self, endpoint):
        if endpoint == "/user":
            return {"id": 989, "username": "project_1312_bot_test"}
        if endpoint == "/projects/1312":
            return {"id": 1312, "path_with_namespace": "biz-ops/scm"}
        raise AssertionError(endpoint)

    def get_all(self, endpoint, params):
        if endpoint != "/projects":
            raise AssertionError(endpoint)
        if params.get("membership") == "true":
            return list(self.memberships)
        if params.get("visibility") == "internal":
            return list(self.internal)
        raise AssertionError(params)


class GitLabAgentTokenProvisionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.root.chmod(0o700)
        self.env = self.root / "scm-desk.env"
        self.env.write_text("AGENT_NAME=scm-desk\n#GITLAB_TOKEN=\n", encoding="utf-8")
        self.env.chmod(0o600)
        self.receipt = self.root / "receipts" / "scm-desk.json"
        self.args = argparse.Namespace(
            host="gitlab.addx.ai",
            project="biz-ops/scm",
            agent_name="scm-desk",
            profile="planner",
            token_name="scm-desk-test",
            expires_at=(dt.datetime.now(dt.timezone.utc).date() + dt.timedelta(days=30)).isoformat(),
            env_file=str(self.env),
            receipt=str(self.receipt),
            authorized_admin="jchen",
            authorization_ref="current-task:user-approved",
            glab=None,
            external="auto",
        )

    def provision(self, glab=None, token_client_factory=FakeTokenClient):
        with mock.patch.object(MODULE.secrets, "token_hex", return_value="0123456789abcdef0123456789abcdef"):
            return MODULE.provision(
                self.args,
                glab=glab or FakeGlab(),
                token_client_factory=token_client_factory,
            )

    def test_happy_path_injects_only_env_and_writes_nonsecret_receipt(self):
        receipt = self.provision()

        self.assertIn("GITLAB_TOKEN=one-time-test-secret", self.env.read_text(encoding="utf-8"))
        self.assertEqual(self.env.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.receipt.stat().st_mode & 0o777, 0o600)
        self.assertNotIn("one-time-test-secret", json.dumps(receipt))
        self.assertNotIn("one-time-test-secret", self.receipt.read_text(encoding="utf-8"))
        self.assertEqual(receipt["gitlab"]["membership_project_ids"], [1312])
        self.assertEqual(receipt["gitlab"]["unexpected_internal_project_ids"], [])
        self.assertFalse(receipt["secret_material_in_receipt"])
        self.assertEqual(receipt["operation_id"], "0123456789abcdef0123456789abcdef")

    def external_writes(self, glab):
        return [payload for method, endpoint, payload, _ in glab.calls
                if method == "PUT" and endpoint == "/users/989"]

    def test_planner_and_reporter_bots_stay_external(self):
        """No MR pipelines to run: keep the Internal-visibility isolation."""
        for profile in ("planner", "reporter"):
            with self.subTest(profile=profile):
                self.args.profile = profile
                self.env.write_text("AGENT_NAME=scm-desk\n#GITLAB_TOKEN=\n", encoding="utf-8")
                self.receipt.unlink(missing_ok=True)
                glab = FakeGlab()
                receipt = self.provision(glab)
                self.assertEqual(self.external_writes(glab), [{"external": True}])
                self.assertIs(receipt["gitlab"]["bot_external"], True)
                self.assertEqual(receipt["gitlab"]["unexpected_internal_project_ids"], [])

    def test_developer_bot_is_not_external_so_mr_pipelines_can_read_the_ci_config_project(self):
        """CI config lives in an internal project (engineering/ci-templates); an external bot cannot read it
        and every MR pipeline it triggers fails at creation with no jobs."""
        self.args.profile = "developer"
        glab = FakeGlab()
        receipt = self.provision(glab)
        self.assertEqual(self.external_writes(glab), [{"external": False}])
        self.assertIs(receipt["gitlab"]["bot_external"], False)
        self.assertIsNone(receipt["gitlab"]["unexpected_internal_project_ids"])
        self.assertEqual(receipt["gitlab"]["membership_project_ids"], [1312])

    def test_non_external_bot_seeing_other_internal_projects_is_expected_not_an_error(self):
        class SeesInternal(FakeTokenClient):
            internal = [{"id": 1312}, {"id": 42}, {"id": 43}]
        self.args.profile = "developer"
        receipt = self.provision(token_client_factory=SeesInternal)
        self.assertIs(receipt["gitlab"]["bot_external"], False)

    def test_non_external_bot_with_extra_membership_is_still_rejected(self):
        class BroadMembership(FakeTokenClient):
            memberships = [{"id": 1312}, {"id": 42}]
        self.args.profile = "developer"
        with self.assertRaises(MODULE.ProvisionError):
            self.provision(token_client_factory=BroadMembership)
        self.assertIn("#GITLAB_TOKEN=", self.env.read_text(encoding="utf-8"))

    def test_external_override_wins_over_the_profile_default(self):
        for profile, override, expected in (("developer", "yes", True), ("planner", "no", False)):
            with self.subTest(profile=profile, override=override):
                self.args.profile, self.args.external = profile, override
                self.env.write_text("AGENT_NAME=scm-desk\n#GITLAB_TOKEN=\n", encoding="utf-8")
                self.receipt.unlink(missing_ok=True)
                glab = FakeGlab()
                receipt = self.provision(glab)
                self.assertEqual(self.external_writes(glab), [{"external": expected}])
                self.assertIs(receipt["gitlab"]["bot_external"], expected)

    def test_external_readback_mismatch_fails_closed_for_both_directions(self):
        for profile in ("developer", "planner"):
            with self.subTest(profile=profile):
                self.args.profile = profile
                self.env.write_text("AGENT_NAME=scm-desk\n#GITLAB_TOKEN=\n", encoding="utf-8")
                glab = FakeGlab()
                glab.ignore_external_write = True
                glab.bot_external = profile == "developer"  # server keeps the opposite of what we asked for
                with self.assertRaises(MODULE.ProvisionError):
                    self.provision(glab)
                self.assertIn("#GITLAB_TOKEN=", self.env.read_text(encoding="utf-8"))

    def test_cli_default_is_auto_and_rejects_unknown_values(self):
        base = ["--host", "h", "--project", "g/p", "--agent-name", "a", "--profile", "developer", "--token-name", "t",
                "--expires-at", "2027-01-01", "--env-file", "e", "--receipt", "r", "--authorized-admin", "x",
                "--authorization-ref", "y"]
        self.assertEqual(MODULE.parse_args(base).external, "auto")
        self.assertEqual(MODULE.parse_args(base + ["--external", "no"]).external, "no")
        with self.assertRaises(SystemExit), mock.patch("sys.stderr"):
            MODULE.parse_args(base + ["--external", "maybe"])

    def test_receipt_says_whether_internal_isolation_was_checked(self):
        """`null` and `[]` are both falsy: a consumer must not read a non-external bot as 'no leak'."""
        for profile, checked in (("planner", True), ("developer", False)):
            with self.subTest(profile=profile):
                self.args.profile = profile
                self.env.write_text("AGENT_NAME=scm-desk\n#GITLAB_TOKEN=\n", encoding="utf-8")
                self.receipt.unlink(missing_ok=True)
                receipt = self.provision()
                self.assertIs(receipt["gitlab"]["internal_isolation_checked"], checked)

    def test_external_planner_seeing_other_internal_projects_is_rejected_and_rolled_back(self):
        """The internal-visibility check moved under `if want_external`: pin that it still fires for planner."""
        class SeesInternal(FakeTokenClient):
            internal = [{"id": 1312}, {"id": 42}]
        self.args.profile = "planner"
        glab = FakeGlab()
        with self.assertRaises(MODULE.ProvisionError):
            self.provision(glab, token_client_factory=SeesInternal)
        self.assertIn("#GITLAB_TOKEN=", self.env.read_text(encoding="utf-8"))
        self.assertTrue(any(method == "DELETE" for method, _, _, _ in glab.calls))

    def test_caller_without_the_external_attribute_gets_the_profile_default(self):
        """Older in-process callers build the Namespace without `external`."""
        del self.args.external
        self.args.profile = "developer"
        glab = FakeGlab()
        receipt = self.provision(glab)
        self.assertEqual(self.external_writes(glab), [{"external": False}])
        self.assertIs(receipt["gitlab"]["bot_external"], False)

    def test_unknown_external_value_is_rejected_before_any_remote_write(self):
        for bad in ("Yes", True, None, "maybe"):
            with self.subTest(bad=bad):
                self.args.external = bad
                self.env.write_text("AGENT_NAME=scm-desk\n#GITLAB_TOKEN=\n", encoding="utf-8")
                self.receipt.unlink(missing_ok=True)
                glab = FakeGlab()
                with self.assertRaises(MODULE.ProvisionError):
                    self.provision(glab)
                self.assertFalse([c for c in glab.calls if c[0] in ("POST", "PUT")])

    def test_non_admin_is_rejected_before_token_creation(self):
        glab = FakeGlab(admin=False)
        with self.assertRaisesRegex(MODULE.ProvisionError, "instance admin"):
            self.provision(glab=glab)
        self.assertEqual([call[1] for call in glab.calls], ["/user"])
        self.assertEqual(self.env.read_text(encoding="utf-8"), "AGENT_NAME=scm-desk\n#GITLAB_TOKEN=\n")

    def test_existing_token_is_not_overwritten(self):
        self.env.write_text("GITLAB_TOKEN=already-present\n", encoding="utf-8")
        self.env.chmod(0o600)
        with self.assertRaisesRegex(MODULE.ProvisionError, "refusing to overwrite"):
            self.provision()

    def test_unresolved_pending_journal_blocks_retry_before_remote_calls(self):
        self.receipt.parent.mkdir(mode=0o700)
        pending = self.receipt.parent / f".{self.receipt.name}.old-operation.pending.json"
        pending.write_text('{"state":"PENDING"}\n', encoding="utf-8")
        pending.chmod(0o600)
        glab = FakeGlab()
        with self.assertRaisesRegex(MODULE.ProvisionError, "unresolved pending journal"):
            self.provision(glab=glab)
        self.assertEqual(glab.calls, [])

    def test_verification_failure_revokes_token_and_leaves_placeholder(self):
        class BroadTokenClient(FakeTokenClient):
            memberships = [{"id": 1312}, {"id": 1021}]

        glab = FakeGlab()
        with self.assertRaisesRegex(MODULE.ProvisionError, "not isolated"):
            self.provision(glab=glab, token_client_factory=BroadTokenClient)
        self.assertEqual(self.env.read_text(encoding="utf-8"), "AGENT_NAME=scm-desk\n#GITLAB_TOKEN=\n")
        self.assertFalse(self.receipt.exists())
        self.assertIn(("DELETE", "/projects/1312/access_tokens/963", None, True), glab.calls)

    def test_duplicate_token_name_is_rejected_before_create(self):
        server_name = "scm-desk-test--op-0123456789abcdef0123456789abcdef"
        glab = FakeGlab(existing=[{"id": 777, "name": server_name, "revoked": False}])
        with self.assertRaisesRegex(MODULE.ProvisionError, "unexpectedly already exists"):
            self.provision(glab=glab)
        self.assertNotIn("POST", [call[0] for call in glab.calls])

    def test_ambiguous_create_response_revokes_exact_name_match(self):
        glab = FakeGlab(ambiguous_create=True)
        with self.assertRaisesRegex(MODULE.ProvisionError, "lost create response"):
            self.provision(glab=glab)
        self.assertIn(("DELETE", "/projects/1312/access_tokens/963", None, True), glab.calls)
        self.assertEqual(self.env.read_text(encoding="utf-8"), "AGENT_NAME=scm-desk\n#GITLAB_TOKEN=\n")

    def test_ambiguous_create_without_visible_match_retains_pending_journal(self):
        glab = FakeGlab(ambiguous_without_visibility=True)
        with self.assertRaisesRegex(MODULE.ProvisionError, "found 0"):
            self.provision(glab=glab)
        journals = list((self.root / "receipts").glob("*.pending.json"))
        self.assertEqual(len(journals), 1)
        journal = json.loads(journals[0].read_text(encoding="utf-8"))
        self.assertEqual(journal["state"], "MANUAL_RECONCILIATION_REQUIRED")
        self.assertNotIn("one-time-test-secret", journals[0].read_text(encoding="utf-8"))

    def test_keyboard_interrupt_during_create_reconciles_then_propagates(self):
        glab = FakeGlab(interrupt_create=True)
        with self.assertRaises(KeyboardInterrupt):
            self.provision(glab=glab)
        self.assertIn(("DELETE", "/projects/1312/access_tokens/963", None, True), glab.calls)
        self.assertEqual(list((self.root / "receipts").glob("*.json")), [])

    def test_malformed_create_response_reconciles_and_revokes_by_name(self):
        glab = FakeGlab(malformed_create=True)
        with self.assertRaisesRegex(MODULE.ProvisionError, "missing id"):
            self.provision(glab=glab)
        self.assertIn(("DELETE", "/projects/1312/access_tokens/963", None, True), glab.calls)
        self.assertEqual(self.env.read_text(encoding="utf-8"), "AGENT_NAME=scm-desk\n#GITLAB_TOKEN=\n")

    def test_server_parameter_mismatch_revokes_before_injection(self):
        glab = FakeGlab(created_overrides={"expires_at": "2099-12-31"})
        with self.assertRaisesRegex(MODULE.ProvisionError, "outside the exact authorized parameters"):
            self.provision(glab=glab)
        self.assertIn(("DELETE", "/projects/1312/access_tokens/963", None, True), glab.calls)
        self.assertEqual(self.env.read_text(encoding="utf-8"), "AGENT_NAME=scm-desk\n#GITLAB_TOKEN=\n")

    def test_keyboard_interrupt_after_create_still_revokes(self):
        class InterruptTokenClient:
            def __init__(self, host, token):
                raise KeyboardInterrupt

        glab = FakeGlab()
        with self.assertRaises(KeyboardInterrupt):
            self.provision(glab=glab, token_client_factory=InterruptTokenClient)
        self.assertIn(("DELETE", "/projects/1312/access_tokens/963", None, True), glab.calls)
        self.assertEqual(list((self.root / "receipts").glob("*.json")), [])

    def test_keyboard_interrupt_during_final_receipt_removes_it_and_rolls_back(self):
        original_write = MODULE._write_receipt

        def write_then_interrupt(path, value):
            original_write(path, value)
            if path == self.receipt:
                raise KeyboardInterrupt

        glab = FakeGlab()
        with mock.patch.object(MODULE, "_write_receipt", side_effect=write_then_interrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.provision(glab=glab)
        self.assertFalse(self.receipt.exists())
        self.assertEqual(self.env.read_text(encoding="utf-8"), "AGENT_NAME=scm-desk\n#GITLAB_TOKEN=\n")
        self.assertIn(("DELETE", "/projects/1312/access_tokens/963", None, True), glab.calls)
        self.assertEqual(list((self.root / "receipts").glob("*.json")), [])

    def test_receipt_race_preserves_competing_file(self):
        original_write = MODULE._write_receipt
        competing = {"operation_id": "another-operation", "state": "COMPLETE"}

        def inject_competitor(path, value):
            if path == self.receipt:
                path.write_text(json.dumps(competing) + "\n", encoding="utf-8")
                path.chmod(0o600)
            original_write(path, value)

        glab = FakeGlab()
        with mock.patch.object(MODULE, "_write_receipt", side_effect=inject_competitor):
            with self.assertRaisesRegex(MODULE.ProvisionError, "must not already exist"):
                self.provision(glab=glab)
        self.assertEqual(json.loads(self.receipt.read_text(encoding="utf-8")), competing)
        self.assertEqual(self.env.read_text(encoding="utf-8"), "AGENT_NAME=scm-desk\n#GITLAB_TOKEN=\n")
        self.assertIn(("DELETE", "/projects/1312/access_tokens/963", None, True), glab.calls)
        journals = list((self.root / "receipts").glob("*.pending.json"))
        self.assertEqual(len(journals), 1)

    def test_env_is_rolled_back_if_injection_reports_failure_after_write(self):
        original_inject = MODULE._inject_token

        def write_then_fail(path, original, injected):
            original_inject(path, original, injected)
            raise MODULE.ProvisionError("simulated post-write failure")

        glab = FakeGlab()
        with mock.patch.object(MODULE, "_inject_token", side_effect=write_then_fail):
            with self.assertRaisesRegex(MODULE.ProvisionError, "post-write failure"):
                self.provision(glab=glab)
        self.assertEqual(self.env.read_text(encoding="utf-8"), "AGENT_NAME=scm-desk\n#GITLAB_TOKEN=\n")
        self.assertIn(("DELETE", "/projects/1312/access_tokens/963", None, True), glab.calls)

    def test_concurrent_env_change_is_preserved_and_journal_retained(self):
        env_path = self.env

        class ConcurrentWriterTokenClient(FakeTokenClient):
            def get_all(self, endpoint, params):
                result = super().get_all(endpoint, params)
                if params.get("visibility") == "internal":
                    env_path.write_text("AGENT_NAME=scm-desk\nGITLAB_TOKEN=concurrent-value\n", encoding="utf-8")
                    env_path.chmod(0o600)
                return result

        glab = FakeGlab()
        with self.assertRaisesRegex(MODULE.ProvisionError, "changed after preflight"):
            self.provision(glab=glab, token_client_factory=ConcurrentWriterTokenClient)
        self.assertIn("GITLAB_TOKEN=concurrent-value", self.env.read_text(encoding="utf-8"))
        self.assertIn(("DELETE", "/projects/1312/access_tokens/963", None, True), glab.calls)
        journals = list((self.root / "receipts").glob("*.pending.json"))
        self.assertEqual(len(journals), 1)

    def test_symlink_and_loose_mode_are_rejected(self):
        link = self.root / "linked.env"
        link.symlink_to(self.env)
        self.args.env_file = str(link)
        with self.assertRaisesRegex(MODULE.ProvisionError, "non-symlink"):
            self.provision()

        self.args.env_file = str(self.env)
        self.env.chmod(0o640)
        with self.assertRaisesRegex(MODULE.ProvisionError, "0600"):
            self.provision()

    def test_glab_client_uses_local_login_not_inherited_token(self):
        seen = {}

        def runner(command, **kwargs):
            seen["command"] = command
            seen.update(kwargs)
            return subprocess.CompletedProcess(command, 0, stdout='{"id":400}', stderr="")

        with mock.patch.dict(os.environ, {
            "GITLAB_TOKEN": "must-not-be-used",
            "GITLAB_ACCESS_TOKEN": "also-must-not-be-used",
            "OAUTH_TOKEN": "also-no",
        }):
            client = MODULE.GlabClient("gitlab.addx.ai", glab_path=shutil.which("true"), runner=runner)
            client.request("/user")
        self.assertNotIn("GITLAB_TOKEN", seen["env"])
        self.assertNotIn("GITLAB_ACCESS_TOKEN", seen["env"])
        self.assertNotIn("OAUTH_TOKEN", seen["env"])
        self.assertNotIn("must-not-be-used", " ".join(seen["command"]))

    def test_registered_agent_runtime_is_rejected(self):
        with mock.patch.dict(os.environ, {"BUZZ_AGENT_NAME": "scm-desk"}):
            with self.assertRaisesRegex(MODULE.ProvisionError, "operator-only"):
                self.provision()

    def test_env_lock_rejects_concurrent_transaction(self):
        with MODULE._exclusive_env_lock(self.env):
            with self.assertRaisesRegex(MODULE.ProvisionError, "another.*active"):
                with MODULE._exclusive_env_lock(self.env):
                    pass

    def test_receipt_lock_rejects_concurrent_transaction(self):
        with MODULE._exclusive_receipt_lock(self.receipt):
            with self.assertRaisesRegex(MODULE.ProvisionError, "another transaction owns"):
                with MODULE._exclusive_receipt_lock(self.receipt):
                    pass


if __name__ == "__main__":
    unittest.main()
