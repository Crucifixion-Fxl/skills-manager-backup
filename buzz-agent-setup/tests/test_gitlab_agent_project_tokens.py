import argparse
import datetime as dt
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


MAP = load("gitlab_agent_project_tokens", ROOT / "scripts/gitlab_agent_project_tokens.py")
L4 = load("gitlab_l4_receipt", ROOT / "scripts/gitlab_l4_receipt.py")
WRAPPER = load("gitlab_project_token", ROOT / "scripts/gitlab_project_token.py")
PROVISION = load("provision_gitlab_agent_tokens", ROOT / "scripts/provision_gitlab_agent_tokens.py")


class Response:
    def __init__(self, value, status=200):
        self.value = value
        self.status = status

    def read(self):
        return json.dumps(self.value).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class Opener:
    def __init__(self, project_id=388, project_path="FAC/factory-app", token="app-secret"):
        self.project_id = project_id
        self.project_path = project_path
        self.token = token
        self.requests = []

    def open(self, request, timeout=30):
        self.requests.append(request)
        self.assert_token = request.get_header("Private-token")
        if request.full_url.endswith(f"/projects/{self.project_id}/"):
            return Response({"id": self.project_id, "path_with_namespace": self.project_path})
        return Response({"id": 17, "iid": 1, "title": "canary"})


class FakeGlab:
    def __init__(self, fail_project=None, rotate_bot=None, fail_rotate_project=None):
        self.calls = []
        self.tokens = {}
        self.fail_project = fail_project
        self.rotate_bot = rotate_bot
        self.fail_rotate_project = fail_rotate_project
        self.next_id = 1000
        self.bot_external = {}

    def request_list(self, endpoint):
        project_id = int(endpoint.split("/projects/")[1].split("/")[0])
        return list(self.tokens.get(project_id, []))

    def request(self, endpoint, *, method="GET", payload=None, allow_empty=False):
        self.calls.append((method, endpoint, payload, allow_empty))
        if endpoint == "/user":
            return {"id": 400, "username": "admin", "is_admin": True}
        if endpoint.startswith("/projects/FAC%2F"):
            path = endpoint[len("/projects/"):].replace("%2F", "/")
            project_id = {"FAC/factory-app": 388, "FAC/factory-service": 392,
                          "FAC/iot-model-service": 396, "FAC/iot-model-ui": 397}[path]
            return {"id": project_id, "path_with_namespace": path}
        if endpoint.startswith("/projects/") and endpoint.endswith("/access_tokens") and method == "POST":
            project_id = int(endpoint.split("/projects/")[1].split("/")[0])
            if project_id == self.fail_project:
                raise PROVISION.ProvisionError("simulated create failure")
            self.next_id += 1
            token_id = self.next_id
            bot_id = project_id + 10000
            self.tokens.setdefault(project_id, []).append({"id": token_id, "name": payload["name"], "revoked": False})
            return {"id": token_id, "user_id": bot_id, "name": payload["name"], "token": f"secret-{project_id}",
                    "access_level": payload["access_level"], "scopes": payload["scopes"], "expires_at": payload["expires_at"]}
        if endpoint.startswith("/projects/") and "/access_tokens/" in endpoint and method == "DELETE":
            project_id = int(endpoint.split("/projects/")[1].split("/")[0])
            self.tokens[project_id] = []
            return {}
        if endpoint.startswith("/users/") and method == "PUT":
            user_id = int(endpoint.split("/")[-1])
            self.bot_external[user_id] = payload["external"]
            return {}
        if endpoint.startswith("/users/"):
            user_id = int(endpoint.split("/")[-1])
            return {"id": user_id, "username": f"project_{user_id}_bot",
                    "external": self.bot_external.get(user_id, True), "state": "active"}
        if endpoint.startswith("/projects/") and endpoint.endswith("/rotate"):
            project_id = int(endpoint.split("/projects/")[1].split("/")[0])
            if project_id == self.fail_rotate_project:
                raise PROVISION.ProvisionError("simulated rotation failure")
            self.next_id += 1
            old = int(endpoint.split("/")[-2])
            bot_id = self.rotate_bot if self.rotate_bot is not None else project_id + 10000
            return {"id": self.next_id, "user_id": bot_id, "token": f"rotated-{project_id}",
                    "name": "rotated", "expires_at": payload.get("expires_at") if payload else None}
        raise AssertionError((method, endpoint, payload, allow_empty))


class FakeTokenClient:
    def __init__(self, host, token):
        self.host = host
        self.token = token

    def get_object(self, endpoint):
        if endpoint == "/user":
            project_id = int(self.token.split("-")[-1])
            return {"id": project_id + 10000, "username": "project_bot"}
        project_id = int(endpoint.split("/")[-1])
        paths = {388: "FAC/factory-app", 392: "FAC/factory-service", 396: "FAC/iot-model-service", 397: "FAC/iot-model-ui"}
        return {"id": project_id, "path_with_namespace": paths[project_id]}

    def get_all(self, endpoint, params):
        if params.get("membership") == "true":
            project_id = int(self.token.split("-")[-1])
            return [{"id": project_id}]
        return []


class ProjectTokenMapTest(unittest.TestCase):
    def test_unknown_and_duplicate_keys_fail_closed(self):
        base = {"version": 1, "host": "gitlab.addx.ai", "projects": [
            {"project_id": 1, "project_path": "FAC/a", "token_env": "FAC_A_TOKEN", "profile": "planner"},
        ]}
        with self.assertRaises(MAP.ProjectTokenMapError):
            MAP.validate_mapping({**base, "unexpected": True})
        duplicate = {**base, "projects": base["projects"] * 2}
        with self.assertRaisesRegex(MAP.ProjectTokenMapError, "duplicate"):
            MAP.validate_mapping(duplicate)

    def test_mapping_sorts_projects_and_rejects_reserved_env(self):
        raw = {"version": 1, "host": "gitlab.addx.ai", "projects": [
            {"project_id": 2, "project_path": "FAC/b", "token_env": "FAC_B_TOKEN", "profile": "planner"},
            {"project_id": 1, "project_path": "FAC/a", "token_env": "FAC_A_TOKEN", "profile": "reporter"},
        ]}
        mapping = MAP.validate_mapping(raw)
        self.assertEqual([entry.project_id for entry in mapping.projects], [1, 2])
        with self.assertRaises(MAP.ProjectTokenMapError):
            MAP.validate_mapping({**raw, "projects": [{**raw["projects"][0], "token_env": "GITLAB_TOKEN"}, raw["projects"][1]]})

    def test_secure_file_loader_requires_0600(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "map.json"
            path.write_text(json.dumps({"version": 1, "host": "gitlab.addx.ai", "projects": [
                {"project_id": 1, "project_path": "FAC/a", "token_env": "FAC_A_TOKEN", "profile": "planner"},
            ]}))
            path.chmod(0o644)
            with self.assertRaisesRegex(MAP.ProjectTokenMapError, "0600"):
                MAP.load_mapping(path)


class WrapperTest(unittest.TestCase):
    @staticmethod
    def _developer_mapping(profile="developer"):
        return MAP.validate_mapping({"version": 1, "host": "gitlab.addx.ai", "projects": [
            {"project_id": 388, "project_path": "FAC/factory-app", "token_env": "FAC_APP_TOKEN", "profile": profile},
        ]})

    @staticmethod
    def _write_provisioning_receipt(path, mapping):
        profiles = {
            "planner": (15, ["api"], True),
            "reporter": (20, ["api", "read_repository"], True),
            "developer": (30, ["api", "write_repository"], False),
        }
        receipt = {
            "schema_version": "2.0",
            "mapping": MAP.mapping_to_public_dict(mapping),
            "tokens": [{
                "project_id": entry.project_id,
                "project_path": entry.project_path,
                "profile": entry.profile,
                "token_id": 1000 + index,
                "bot_user_id": 2000 + index,
                "access_level": profiles[entry.profile][0],
                "scopes": profiles[entry.profile][1],
                "bot_external": profiles[entry.profile][2],
                "membership_project_ids": [entry.project_id],
            } for index, entry in enumerate(mapping.projects)],
            "secret_material_in_receipt": False,
        }
        path.write_text(json.dumps(receipt), encoding="utf-8")
        path.chmod(0o600)
        return L4.canonical_json_sha256(receipt)

    @staticmethod
    def _write_l4_receipt(path, mapping, *, head_sha="a" * 40, provisioning_sha256="0" * 64, **overrides):
        profiles = {
            "planner": (15, ["api"], True),
            "reporter": (20, ["api", "read_repository"], True),
            "developer": (30, ["api", "write_repository"], False),
        }
        receipt = {
            "schema_version": WRAPPER._L4_RECEIPT_SCHEMA,
            "status": "verified",
            "map_sha256": MAP.mapping_sha256(mapping),
            "head_sha": head_sha,
            "provisioning_receipt_sha256": provisioning_sha256,
            "write_methods": sorted(WRAPPER._WRITE_METHODS),
            "projects": [{
                "project_id": entry.project_id,
                "project_path": entry.project_path,
                "profile": entry.profile,
                "token_id": 1000 + index,
                "bot_user_id": 2000 + index,
                "access_level": profiles[entry.profile][0],
                "scopes": profiles[entry.profile][1],
                "bot_external": profiles[entry.profile][2],
                "membership_project_ids": [entry.project_id],
                "write_canary": {
                    "status": "passed",
                    "method_results": [{
                        "method": method,
                        "http_status": 200,
                        "endpoint": "/issues",
                        "response_sha256": "c" * 64,
                    } for method in sorted(WRAPPER._WRITE_METHODS)],
                } if entry.profile == "developer" else "not_applicable",
            } for index, entry in enumerate(mapping.projects)],
            "evidence": {
                "gitlab_version": "18.0.0",
                "gitlab_revision": "gitlab-revision",
                "verified_at": "2026-09-21T00:00:00+00:00",
                "verified_by": "operator",
                "canary_note_id": 12345,
                "runtime": {"pid": 1234, "workers": 1},
            },
            "ordinary_agent_gitlab_writes_enabled": True,
            "l4_canary": "passed",
        }
        receipt.update(overrides)
        path.write_text(json.dumps(receipt), encoding="utf-8")
        path.chmod(0o600)

    def test_wrapper_selects_mapped_env_and_verifies_target(self):
        mapping = self._developer_mapping()
        opener = Opener()
        client = WRAPPER.GitLabProjectClient(mapping, project_id=388, environ={"FAC_APP_TOKEN": "app-secret"}, opener=opener)
        value = client.request("/issues", method="GET")
        self.assertEqual(value["iid"], 1)
        self.assertEqual(opener.assert_token, "app-secret")
        self.assertEqual(opener.requests[0].full_url, "https://gitlab.addx.ai/api/v4/projects/388/")

    def test_wrapper_denies_every_non_get_method_without_verified_l4_receipt(self):
        mapping = self._developer_mapping()
        opener = Opener()
        client = WRAPPER.GitLabProjectClient(mapping, project_id=388,
                                              environ={"FAC_APP_TOKEN": "app-secret"}, opener=opener)
        for method in sorted(WRAPPER._WRITE_METHODS):
            with self.subTest(method=method):
                with self.assertRaisesRegex(WRAPPER.ProjectTokenRequestError, "L4 write receipt"):
                    client.request("/issues", method=method, payload={"title": "canary"})
        with self.assertRaisesRegex(WRAPPER.ProjectTokenRequestError, "L4 write receipt"):
            client._request("/issues", method="POST", payload={"title": "canary"})
        self.assertEqual(opener.requests, [])

    def test_wrapper_allows_writes_only_for_a_verified_map_and_head_bound_receipt(self):
        mapping = self._developer_mapping()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            root.chmod(0o700)
            receipt = root / "l4-receipt.json"
            provisioning = root / "provisioning-receipt.json"
            provisioning_sha256 = self._write_provisioning_receipt(provisioning, mapping)
            head_sha = "a" * 40
            self._write_l4_receipt(receipt, mapping, head_sha=head_sha, provisioning_sha256=provisioning_sha256)
            environ = {
                "FAC_APP_TOKEN": "app-secret",
                WRAPPER.OWNER_L4_RECEIPT_ENV: str(receipt),
                WRAPPER.OWNER_L4_HEAD_ENV: head_sha,
                WRAPPER.OWNER_PROVISIONING_RECEIPT_ENV: str(provisioning),
            }
            opener = Opener()
            client = WRAPPER.GitLabProjectClient(mapping, project_id=388, environ=environ, opener=opener)
            value = client.request("/issues", method="POST", payload={"title": "canary"})
        self.assertEqual(value["iid"], 1)
        self.assertEqual([request.method for request in opener.requests], ["GET", "POST"])

    def test_wrapper_rejects_pending_or_mismatched_l4_receipts(self):
        mapping = self._developer_mapping()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            root.chmod(0o700)
            cases = (
                ({"ordinary_agent_gitlab_writes_enabled": False, "l4_canary": "pending"}, "write canary"),
                ({"map_sha256": "0" * 64}, "map binding"),
                ({"head_sha": "b" * 40}, "head binding"),
                ({"write_methods": ["POST"]}, "every write method"),
                ({"evidence": {}}, "evidence"),
            )
            for index, (overrides, message) in enumerate(cases):
                with self.subTest(case=index):
                    receipt = root / f"l4-receipt-{index}.json"
                    provisioning = root / f"provisioning-receipt-{index}.json"
                    provisioning_sha256 = self._write_provisioning_receipt(provisioning, mapping)
                    head_sha = "a" * 40
                    receipt_overrides = dict(overrides)
                    receipt_head = receipt_overrides.pop("head_sha", head_sha)
                    self._write_l4_receipt(receipt, mapping, head_sha=receipt_head,
                                            provisioning_sha256=provisioning_sha256, **receipt_overrides)
                    environ = {
                        "FAC_APP_TOKEN": "app-secret",
                        WRAPPER.OWNER_L4_RECEIPT_ENV: str(receipt),
                        WRAPPER.OWNER_L4_HEAD_ENV: head_sha,
                        WRAPPER.OWNER_PROVISIONING_RECEIPT_ENV: str(provisioning),
                    }
                    opener = Opener()
                    client = WRAPPER.GitLabProjectClient(mapping, project_id=388, environ=environ, opener=opener)
                    with self.assertRaisesRegex(WRAPPER.ProjectTokenRequestError, message):
                        client.request("/issues", method="POST", payload={"title": "canary"})
                    self.assertEqual(opener.requests, [])

    def test_wrapper_keeps_non_developer_profiles_read_only_after_l4_receipt(self):
        mapping = self._developer_mapping(profile="reporter")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            root.chmod(0o700)
            receipt = root / "l4-receipt.json"
            provisioning = root / "provisioning-receipt.json"
            provisioning_sha256 = self._write_provisioning_receipt(provisioning, mapping)
            head_sha = "a" * 40
            self._write_l4_receipt(receipt, mapping, head_sha=head_sha, provisioning_sha256=provisioning_sha256)
            environ = {
                "FAC_APP_TOKEN": "app-secret",
                WRAPPER.OWNER_L4_RECEIPT_ENV: str(receipt),
                WRAPPER.OWNER_L4_HEAD_ENV: head_sha,
                WRAPPER.OWNER_PROVISIONING_RECEIPT_ENV: str(provisioning),
            }
            with self.assertRaisesRegex(WRAPPER.ProjectTokenRequestError, "profile"):
                WRAPPER.GitLabProjectClient(mapping, project_id=388, environ=environ, opener=Opener()).request(
                    "/issues", method="DELETE"
                )

    def test_wrapper_invalidates_l4_receipt_after_token_rotation_receipt_changes(self):
        mapping = self._developer_mapping()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            root.chmod(0o700)
            receipt = root / "l4-receipt.json"
            provisioning = root / "provisioning-receipt.json"
            provisioning_sha256 = self._write_provisioning_receipt(provisioning, mapping)
            head_sha = "a" * 40
            self._write_l4_receipt(receipt, mapping, head_sha=head_sha, provisioning_sha256=provisioning_sha256)
            rotated = json.loads(provisioning.read_text())
            rotated["tokens"][0]["token_id"] = 2000
            provisioning.write_text(json.dumps(rotated), encoding="utf-8")
            provisioning.chmod(0o600)
            environ = {
                "FAC_APP_TOKEN": "app-secret",
                WRAPPER.OWNER_L4_RECEIPT_ENV: str(receipt),
                WRAPPER.OWNER_L4_HEAD_ENV: head_sha,
                WRAPPER.OWNER_PROVISIONING_RECEIPT_ENV: str(provisioning),
            }
            opener = Opener()
            with self.assertRaisesRegex(WRAPPER.ProjectTokenRequestError, "provisioning binding"):
                WRAPPER.GitLabProjectClient(mapping, project_id=388, environ=environ, opener=opener).request(
                    "/issues", method="POST", payload={"title": "canary"}
                )
            self.assertEqual(opener.requests, [])

    def test_wrapper_rejects_a_provisioning_receipt_without_token_identity_contract(self):
        mapping = self._developer_mapping()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            root.chmod(0o700)
            receipt = root / "l4-receipt.json"
            provisioning = root / "provisioning-receipt.json"
            incomplete = {
                "schema_version": "2.0",
                "mapping": MAP.mapping_to_public_dict(mapping),
                "tokens": [{"project_id": 388, "token_id": 1000}],
                "secret_material_in_receipt": False,
            }
            provisioning.write_text(json.dumps(incomplete), encoding="utf-8")
            provisioning.chmod(0o600)
            self._write_l4_receipt(
                receipt, mapping, head_sha="a" * 40,
                provisioning_sha256=L4.canonical_json_sha256(incomplete),
            )
            environ = {
                "FAC_APP_TOKEN": "app-secret",
                WRAPPER.OWNER_L4_RECEIPT_ENV: str(receipt),
                WRAPPER.OWNER_L4_HEAD_ENV: "a" * 40,
                WRAPPER.OWNER_PROVISIONING_RECEIPT_ENV: str(provisioning),
            }
            with self.assertRaisesRegex(WRAPPER.ProjectTokenRequestError, "token identity"):
                WRAPPER.GitLabProjectClient(mapping, project_id=388, environ=environ, opener=Opener()).request(
                    "/issues", method="POST", payload={"title": "canary"}
                )

    def test_wrapper_rejects_target_identity_mismatch(self):
        class WrongTarget(Opener):
            def open(self, request, timeout=30):
                self.requests.append(request)
                if request.full_url.endswith("/projects/388/"):
                    return Response({"id": 999, "path_with_namespace": "other/project"})
                return Response({"id": 17})

        mapping = MAP.validate_mapping({"version": 1, "host": "gitlab.addx.ai", "projects": [
            {"project_id": 388, "project_path": "FAC/factory-app", "token_env": "FAC_APP_TOKEN", "profile": "developer"},
        ]})
        with self.assertRaisesRegex(WRAPPER.ProjectTokenRequestError, "does not match"):
            WRAPPER.GitLabProjectClient(mapping, project_id=388, environ={"FAC_APP_TOKEN": "app-secret"}, opener=WrongTarget()).request("/issues")

    def test_wrapper_rejects_unmapped_and_arbitrary_paths(self):
        mapping = MAP.validate_mapping({"version": 1, "host": "gitlab.addx.ai", "projects": [
            {"project_id": 388, "project_path": "FAC/factory-app", "token_env": "FAC_APP_TOKEN", "profile": "developer"},
        ]})
        with self.assertRaises(WRAPPER.ProjectTokenRequestError):
            WRAPPER.GitLabProjectClient(mapping, project_id=999, environ={"FAC_APP_TOKEN": "x"})
        with self.assertRaises(WRAPPER.ProjectTokenRequestError):
            WRAPPER._safe_endpoint("https://other.example/api")
        with self.assertRaises(WRAPPER.ProjectTokenRequestError):
            WRAPPER._safe_endpoint("/projects/999/issues")
        for escaped in ("/%2e%2e/999/issues", "/issues/%2e%2e/999", "/issues%2f..%2f999",
                        "/%252e%252e/999/issues", "/issues/%5c..%5c999"):
            with self.subTest(endpoint=escaped):
                with self.assertRaises(WRAPPER.ProjectTokenRequestError):
                    WRAPPER._safe_endpoint(escaped)

    def test_owner_pinned_map_rejects_caller_selected_config(self):
        with tempfile.TemporaryDirectory() as temp:
            owner = Path(temp) / "owner.json"
            other = Path(temp) / "other.json"
            owner.write_text("{}")
            other.write_text("{}")
            with mock.patch.dict(os.environ, {WRAPPER.OWNER_MAP_ENV: str(owner)}):
                with self.assertRaises(WRAPPER.ProjectTokenRequestError):
                    WRAPPER._resolve_config_path(str(other))

    def test_owner_pinned_symlink_is_rejected_by_secure_loader(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "target.json"
            link = root / "map.json"
            target.write_text(json.dumps({"version": 1, "host": "gitlab.addx.ai", "projects": [
                {"project_id": 388, "project_path": "FAC/factory-app", "token_env": "FAC_APP_TOKEN", "profile": "developer"},
            ]}))
            target.chmod(0o600)
            link.symlink_to(target)
            with mock.patch.dict(os.environ, {WRAPPER.OWNER_MAP_ENV: str(link)}):
                with self.assertRaisesRegex(MAP.ProjectTokenMapError, "non-symlink"):
                    MAP.load_mapping(WRAPPER._resolve_config_path(None))

    def test_missing_owner_pin_is_fail_closed(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(WRAPPER.ProjectTokenRequestError, "owner pin"):
                WRAPPER._resolve_config_path("/tmp/map.json")

    def test_wrapper_never_follows_redirects_with_a_project_token(self):
        self.assertIsNone(WRAPPER.NoRedirect().redirect_request(None, None, 302, "Found", {}, "https://other.example"))


class BatchProvisionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.root.chmod(0o700)
        self.env = self.root / "agent.env"
        self.env.write_text("AGENT_NAME=fac\n#FAC_FACTORY_APP_GITLAB_TOKEN=\n#FAC_FACTORY_SERVICE_GITLAB_TOKEN=\n#FAC_IOT_MODEL_SERVICE_GITLAB_TOKEN=\n#FAC_IOT_MODEL_UI_GITLAB_TOKEN=\n", encoding="utf-8")
        self.env.chmod(0o600)
        self.mapping_path = self.root / "map.json"
        self.mapping_path.write_text(json.dumps({"version": 1, "host": "gitlab.addx.ai", "projects": [
            {"project_id": 388, "project_path": "FAC/factory-app", "token_env": "FAC_FACTORY_APP_GITLAB_TOKEN", "profile": "developer"},
            {"project_id": 392, "project_path": "FAC/factory-service", "token_env": "FAC_FACTORY_SERVICE_GITLAB_TOKEN", "profile": "developer"},
            {"project_id": 396, "project_path": "FAC/iot-model-service", "token_env": "FAC_IOT_MODEL_SERVICE_GITLAB_TOKEN", "profile": "developer"},
            {"project_id": 397, "project_path": "FAC/iot-model-ui", "token_env": "FAC_IOT_MODEL_UI_GITLAB_TOKEN", "profile": "reporter"},
        ]}), encoding="utf-8")
        self.mapping_path.chmod(0o600)
        self.receipt = self.root / "receipts" / "agent.json"
        self.args = argparse.Namespace(mapping=str(self.mapping_path), env_file=str(self.env), receipt=str(self.receipt),
                                       agent_name="fac-agent", expires_at=(dt.datetime.now(dt.timezone.utc).date() + dt.timedelta(days=30)).isoformat(),
                                       authorized_admin="admin", authorization_ref="issue-122", glab=None)

    def test_four_project_canary_provisions_named_tokens_without_secret_receipt(self):
        glab = FakeGlab()
        with mock.patch.object(PROVISION.secrets, "token_hex", return_value="a" * 32):
            receipt = PROVISION.provision(self.args, glab=glab, token_client_factory=FakeTokenClient)
        content = self.env.read_text()
        for project_id in (388, 392, 396, 397):
            self.assertIn(f"secret-{project_id}", content)
        self.assertEqual(len(receipt["tokens"]), 4)
        self.assertNotIn("secret-", self.receipt.read_text())
        self.assertFalse(receipt["ordinary_agent_gitlab_writes_enabled"])
        by_project = {item["project_id"]: item for item in receipt["tokens"]}
        for project_id in (388, 392, 396):
            self.assertIs(by_project[project_id]["bot_external"], False)
            self.assertIs(by_project[project_id]["internal_isolation_checked"], False)
            self.assertIsNone(by_project[project_id]["unexpected_internal_project_ids"])
        self.assertIs(by_project[397]["bot_external"], True)
        self.assertIs(by_project[397]["internal_isolation_checked"], True)
        self.assertEqual(by_project[397]["unexpected_internal_project_ids"], [])

    def test_partial_failure_revokes_only_this_operation_and_restores_env(self):
        glab = FakeGlab(fail_project=396)
        with mock.patch.object(PROVISION.secrets, "token_hex", return_value="b" * 32):
            with self.assertRaises(PROVISION.ProvisionError):
                PROVISION.provision(self.args, glab=glab, token_client_factory=FakeTokenClient)
        self.assertIn("#FAC_FACTORY_APP_GITLAB_TOKEN=", self.env.read_text())
        self.assertEqual(glab.tokens.get(388), [])
        self.assertEqual(glab.tokens.get(392), [])
        self.assertFalse(self.receipt.exists())

    def test_journal_cleanup_failure_removes_success_receipt_before_rollback(self):
        glab = FakeGlab()
        operation_id = "9" * 32
        journal_path = self.receipt.parent / f".{self.receipt.name}.{operation_id}.pending.json"
        original_unlink = Path.unlink
        failed_once = False

        def fail_first_journal_unlink(path, missing_ok=False):
            nonlocal failed_once
            if path == journal_path and not failed_once:
                failed_once = True
                raise OSError("simulated journal cleanup failure")
            return original_unlink(path, missing_ok=missing_ok)

        with mock.patch.object(PROVISION.secrets, "token_hex", return_value=operation_id), \
                mock.patch.object(Path, "unlink", new=fail_first_journal_unlink):
            with self.assertRaisesRegex(PROVISION.ProvisionError, "unexpected provisioning failure"):
                PROVISION.provision(self.args, glab=glab, token_client_factory=FakeTokenClient)
        self.assertFalse(self.receipt.exists())
        self.assertIn("#FAC_FACTORY_APP_GITLAB_TOKEN=", self.env.read_text())
        self.assertTrue(all(not values for values in glab.tokens.values()))

    def test_out_of_scope_membership_is_rejected_for_each_four_project_canary(self):
        class BroadTokenClient(FakeTokenClient):
            def __init__(self, host, token):
                super().__init__(host, token)
                self.project_id = int(token.split("-")[-1])

            def get_all(self, endpoint, params):
                value = super().get_all(endpoint, params)
                if params.get("membership") == "true" and self.project_id == self.bad_project:
                    value.append({"id": 999})
                return value

        for bad_project in (388, 392, 396, 397):
            with self.subTest(project_id=bad_project):
                BroadTokenClient.bad_project = bad_project
                glab = FakeGlab()
                with mock.patch.object(PROVISION.secrets, "token_hex", return_value="e" * 32):
                    with self.assertRaisesRegex(PROVISION.ProvisionError, "not isolated"):
                        PROVISION.provision(self.args, glab=glab, token_client_factory=BroadTokenClient)
                self.assertTrue(all(not values for values in glab.tokens.values()))
                self.assertIn("#FAC_IOT_MODEL_SERVICE_GITLAB_TOKEN=", self.env.read_text())

    def test_env_change_between_verification_and_write_fails_closed(self):
        env_path = self.env

        class ConcurrentTokenClient(FakeTokenClient):
            def get_all(self, endpoint, params):
                value = super().get_all(endpoint, params)
                if params.get("visibility") == "internal":
                    env_path.write_text(env_path.read_text() + "CONCURRENT=1\n")
                    env_path.chmod(0o600)
                return value

        with mock.patch.object(PROVISION.secrets, "token_hex", return_value="f" * 32):
            with self.assertRaisesRegex(PROVISION.ProvisionError, "changed after preflight"):
                PROVISION.provision(self.args, glab=FakeGlab(), token_client_factory=ConcurrentTokenClient)
        self.assertIn("CONCURRENT=1", self.env.read_text())

    def test_rotation_keeps_bot_identity(self):
        glab = FakeGlab()
        with mock.patch.object(PROVISION.secrets, "token_hex", return_value="c" * 32):
            PROVISION.provision(self.args, glab=glab, token_client_factory=FakeTokenClient)
        rotate_args = argparse.Namespace(mapping=str(self.mapping_path), env_file=str(self.env), receipt=str(self.receipt), expires_at=None, authorized_admin="admin", glab=None)
        original = json.loads(self.receipt.read_text())
        # Fake rotation uses old id + 10000, which matches each original bot id.
        with mock.patch.object(PROVISION.secrets, "token_hex", return_value="d" * 32):
            rotated = PROVISION.rotate(rotate_args, glab=glab, token_client_factory=FakeTokenClient)
        self.assertEqual([item["bot_user_id"] for item in rotated["tokens"]], [item["bot_user_id"] for item in original["tokens"]])
        self.assertEqual([item["bot_external"] for item in rotated["tokens"]], [item["bot_external"] for item in original["tokens"]])
        self.assertTrue(all("rotated-" in self.env.read_text() for _ in [0]))

    def test_partial_rotation_keeps_journal_and_does_not_claim_success(self):
        glab = FakeGlab()
        with mock.patch.object(PROVISION.secrets, "token_hex", return_value="1" * 32):
            PROVISION.provision(self.args, glab=glab, token_client_factory=FakeTokenClient)
        original_env = self.env.read_text()
        rotate_args = argparse.Namespace(mapping=str(self.mapping_path), env_file=str(self.env), receipt=str(self.receipt), expires_at=None, authorized_admin="admin", glab=None)
        glab.fail_rotate_project = 392
        with mock.patch.object(PROVISION.secrets, "token_hex", return_value="2" * 32):
            with self.assertRaisesRegex(PROVISION.ProvisionError, "rotation failure"):
                PROVISION.rotate(rotate_args, glab=glab, token_client_factory=FakeTokenClient)
        self.assertEqual(self.env.read_text(), original_env)
        journals = list(self.receipt.parent.glob("*.pending.json"))
        self.assertEqual(len(journals), 1)
        journal = json.loads(journals[0].read_text())
        self.assertEqual(journal["state"], "MANUAL_RECONCILIATION_REQUIRED")
        self.assertIsNotNone(journal["rotated"][0]["new_token_id"])

    def test_batch_second_invocation_is_rejected_by_env_lock(self):
        with PROVISION.single._exclusive_env_lock(self.env):
            with self.assertRaisesRegex(PROVISION.ProvisionError, "another.*active"):
                PROVISION.provision(self.args, glab=FakeGlab(), token_client_factory=FakeTokenClient)


if __name__ == "__main__":
    unittest.main()
