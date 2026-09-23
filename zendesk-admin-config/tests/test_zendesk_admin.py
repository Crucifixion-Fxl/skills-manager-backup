from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).parents[1] / "scripts" / "zendesk_admin.py"
SPEC = importlib.util.spec_from_file_location("zendesk_admin", SCRIPT)
assert SPEC and SPEC.loader
zendesk_admin = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = zendesk_admin
SPEC.loader.exec_module(zendesk_admin)


def base_config() -> dict:
    return {
        "schema_version": 1,
        "tenant": {"subdomain": "neopace"},
        "unresolved_decisions": [],
        "groups": [],
        "ticket_fields": [
            {
                "key": "topic",
                "title": "Topic",
                "type": "tagger",
                "active": True,
                "custom_field_options": [{"name": "Other", "value": "topic_other"}],
            }
        ],
        "ticket_forms": [
            {
                "key": "support",
                "name": "NeoPace Customer Support",
                "active": True,
                "append_field_keys": ["topic"],
            }
        ],
        "triggers": [],
    }


def live_state(form_field_ids: list[int] | None = None) -> dict:
    return {
        "tenant": "neopace",
        "resources": {
            "groups": [],
            "ticket_fields": [
                {
                    "id": 101,
                    "title": "Topic",
                    "type": "tagger",
                    "active": True,
                    "custom_field_options": [{"id": 8, "name": "Other", "value": "topic_other"}],
                }
            ],
            "ticket_forms": [
                {
                    "id": 201,
                    "name": "NeoPace Customer Support",
                    "active": True,
                    "ticket_field_ids": form_field_ids if form_field_ids is not None else [1, 2],
                }
            ],
            "triggers": [],
        },
    }


class FakeClient:
    subdomain = "neopace"

    def __init__(self, state: dict):
        self.state = state
        self.calls: list[tuple[str, str, dict]] = []

    def list_all(self, resource: str):
        return json.loads(json.dumps(self.state["resources"][resource]))

    def post(self, path: str, payload: dict):
        self.calls.append(("POST", path, payload))
        singular = next(iter(payload))
        resource = singular + "s"
        body = dict(payload[singular])
        body["id"] = 900 + len(self.calls)
        self.state["resources"][resource].append(body)
        return {singular: body}

    def put(self, path: str, payload: dict):
        self.calls.append(("PUT", path, payload))
        singular = next(iter(payload))
        resource = singular + "s"
        remote_id = int(path.rsplit("/", 1)[1].removesuffix(".json"))
        for item in self.state["resources"][resource]:
            if int(item["id"]) == remote_id:
                item.update(payload[singular])
                break
        return payload


class ZendeskAdminTests(unittest.TestCase):
    def test_zendesk_default_false_option_is_not_reported_as_drift(self):
        state = live_state(form_field_ids=[1, 2, 101])
        state["resources"]["ticket_fields"][0]["custom_field_options"][0]["default"] = False

        plan = zendesk_admin.build_plan(base_config(), state)

        topic = next(operation for operation in plan["operations"] if operation["key"] == "topic")
        self.assertEqual(topic["action"], "noop")

    def test_config_refuses_enabled_unresolved_resource(self):
        config = base_config()
        config["groups"] = [{"key": "support", "name": "Support", "decision_required": True}]
        with self.assertRaisesRegex(zendesk_admin.ConfigError, "requires a business decision"):
            zendesk_admin.validate_config(config)

    def test_config_refuses_form_reference_to_disabled_field(self):
        config = base_config()
        config["ticket_fields"][0]["enabled"] = False
        with self.assertRaisesRegex(zendesk_admin.ConfigError, "disabled or unknown"):
            zendesk_admin.validate_config(config)

    def test_environment_cannot_redirect_config_to_another_tenant(self):
        old = os.environ.get("ZENDESK_SUBDOMAIN")
        os.environ["ZENDESK_SUBDOMAIN"] = "other"
        try:
            with self.assertRaisesRegex(zendesk_admin.ConfigError, "does not match"):
                zendesk_admin.ZendeskClient.from_environment("neopace")
        finally:
            if old is None:
                os.environ.pop("ZENDESK_SUBDOMAIN", None)
            else:
                os.environ["ZENDESK_SUBDOMAIN"] = old

    def test_oauth_client_credentials_are_exchanged_for_bearer_token(self):
        class Response:
            def __init__(self, payload):
                self.payload = json.dumps(payload).encode()

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return self.payload

        client = zendesk_admin.ZendeskClient(
            subdomain="neopace", client_id="client-id", client_secret="client-secret"
        )
        with mock.patch.object(
            zendesk_admin.urllib.request,
            "urlopen",
            side_effect=[Response({"access_token": "short-lived-token"}), Response({"user": {"role": "admin"}})],
        ) as opener:
            result = client.get("users/me")
        self.assertEqual(result["user"]["role"], "admin")
        token_request = opener.call_args_list[0].args[0]
        api_request = opener.call_args_list[1].args[0]
        self.assertEqual(token_request.full_url, "https://neopace.zendesk.com/oauth/tokens")
        self.assertEqual(token_request.get_header("Content-type"), "application/json")
        self.assertEqual(json.loads(token_request.data)["grant_type"], "client_credentials")
        self.assertEqual(api_request.get_header("Authorization"), "Bearer short-lived-token")

    def test_duplicate_live_identity_fails_closed(self):
        state = live_state()
        state["resources"]["ticket_fields"].append(dict(state["resources"]["ticket_fields"][0]))
        with self.assertRaisesRegex(zendesk_admin.ConfigError, "Multiple live"):
            zendesk_admin.build_plan(base_config(), state)

    def test_ticket_field_type_change_requires_separate_migration(self):
        state = live_state()
        state["resources"]["ticket_fields"][0]["type"] = "text"
        with self.assertRaisesRegex(zendesk_admin.ConfigError, "immutable type"):
            zendesk_admin.build_plan(base_config(), state)

    def test_plan_is_noop_when_field_and_form_are_already_converged(self):
        plan = zendesk_admin.build_plan(base_config(), live_state(form_field_ids=[1, 101, 2]))
        self.assertEqual(plan["summary"], {"create": 0, "update": 0, "noop": 2})
        self.assertFalse(zendesk_admin.plan_has_drift(plan))

    def test_plan_appends_field_without_removing_existing_form_fields(self):
        state = live_state(form_field_ids=[1, 2])
        plan = zendesk_admin.build_plan(base_config(), state)
        form_op = next(operation for operation in plan["operations"] if operation["resource"] == "ticket_forms")
        self.assertEqual(form_op["action"], "update")
        body = zendesk_admin.resolve_form_body(
            form_op["after"], base_config(), state, state["resources"]["ticket_forms"][0]
        )
        self.assertEqual(body["ticket_field_ids"], [1, 2, 101])

    def test_disabled_resource_never_enters_plan(self):
        config = base_config()
        config["triggers"] = [{
            "key": "routing", "title": "Routing", "enabled": False, "decision_required": True
        }]
        plan = zendesk_admin.build_plan(config, live_state(form_field_ids=[1, 101, 2]))
        self.assertFalse(any(operation["key"] == "routing" for operation in plan["operations"]))

    def test_new_field_forces_existing_form_update_in_same_plan(self):
        config = base_config()
        state = live_state(form_field_ids=[1, 2])
        state["resources"]["ticket_fields"] = []
        plan = zendesk_admin.build_plan(config, state)
        actions = {(operation["resource"], operation["action"]) for operation in plan["operations"]}
        self.assertIn(("ticket_fields", "create"), actions)
        self.assertIn(("ticket_forms", "update"), actions)

    def test_new_form_inherits_default_system_fields(self):
        config = base_config()
        state = live_state(form_field_ids=[1, 2])
        state["resources"]["ticket_forms"] = [{
            "id": 200, "name": "Default form", "default": True, "ticket_field_ids": [1, 2, 3]
        }]
        body = zendesk_admin.resolve_form_body(
            {"name": "NeoPace Customer Support", "append_field_keys": ["topic"]},
            config,
            state,
            None,
        )
        self.assertEqual(body["ticket_field_ids"], [1, 2, 3, 101])

    def test_apply_requires_exact_plan_sha_before_any_write(self):
        config = base_config()
        client = FakeClient(live_state(form_field_ids=[1, 2]))
        plan = zendesk_admin.build_plan(config, zendesk_admin.snapshot(client))
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(zendesk_admin.ConfigError, "exactly match"):
                zendesk_admin.apply_plan(client, config, plan, "wrong", Path(directory))
        self.assertEqual(client.calls, [])

    def test_full_snapshot_plan_apply_verify_flow_converges(self):
        config = base_config()
        state = live_state(form_field_ids=[1, 2])
        state["resources"]["ticket_fields"] = []
        state["resources"]["ticket_forms"] = [{
            "id": 200, "name": "Default form", "default": True, "active": True,
            "ticket_field_ids": [1, 2],
        }]
        client = FakeClient(state)
        before = zendesk_admin.snapshot(client)
        plan = zendesk_admin.build_plan(config, before)
        self.assertEqual(plan["summary"], {"create": 2, "update": 0, "noop": 0})
        with tempfile.TemporaryDirectory() as directory:
            backup_dir = Path(directory) / "backups"
            zendesk_admin.apply_plan(
                client, config, plan, plan["plan_sha256"], backup_dir
            )
            backups = list(backup_dir.glob("pre-apply-*.json"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(os.stat(backups[0]).st_mode & 0o777, 0o600)
        verify_plan = zendesk_admin.build_plan(config, zendesk_admin.snapshot(client))
        self.assertFalse(zendesk_admin.plan_has_drift(verify_plan))
        self.assertEqual(verify_plan["summary"], {"create": 0, "update": 0, "noop": 2})

    def test_secure_output_is_owner_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plan.json"
            zendesk_admin.secure_write_json(path, {"ok": True})
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
