"""Artifact preflight: live, read-only binding checks and fail-closed fixtures."""
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/check_artifact_binding.py"
ROOT_URL = "https://gitlab.addx.ai/WEB/micro_app_platform/-/issues/23"
WORK_URL = "https://gitlab.addx.ai/engineering/skills/-/issues/302"
REPOSITORY = "engineering/skills"


def issue(project_id, iid, global_id, url):
    return {"id": global_id, "project_id": project_id, "iid": iid,
            "web_url": url, "state": "opened", "assignees": [{"id": 22}],
            "updated_at": "2026-10-16T06:00:00Z", "description": "DO_NOT_ECHO_SECRET"}


class BindingTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.is_file(), "Missing read-only artifact binding guard")
        spec = importlib.util.spec_from_file_location("artifact_binding", SCRIPT)
        self.guard = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.guard)
        self.root = issue(773, 23, 22058, ROOT_URL)
        self.work = issue(1021, 302, 22067, WORK_URL)
        self.links = {"root": [{**self.work, "link_type": "relates_to"}],
                      "work": [{**self.root, "link_type": "relates_to"}]}

    def check(self, **changes):
        values = dict(root=self.root, work=self.work, links=self.links,
                      repository=REPOSITORY)
        values.update(changes)
        return self.guard.validate_binding(**values)

    def run_cli(self, args, responses=()):
        output = io.StringIO()
        with patch.object(self.guard, "read_api", side_effect=responses) as reader:
            with contextlib.redirect_stdout(output):
                code = self.guard.main(args)
        return code, json.loads(output.getvalue()), reader

    def test_valid_cross_repository_and_single_repository(self):
        self.assertEqual([], self.check())
        self.assertEqual([], self.check(root=self.work, links={}))

    def test_owner_ssot_supplies_the_single_trusted_https_instance(self):
        self.assertTrue(callable(getattr(self.guard, "load_owner_host", None)))
        with tempfile.TemporaryDirectory() as directory:
            owner = Path(directory) / "owner.json"
            owner.write_text(json.dumps({"instances": ["https://gitlab.addx.ai"]}))
            self.assertEqual("gitlab.addx.ai", self.guard.load_owner_host(owner))

    def test_malformed_or_ambiguous_owner_ssot_fails_closed(self):
        self.assertTrue(callable(getattr(self.guard, "load_owner_host", None)))
        bad_instances = ([], ["https://one.example", "https://two.example"],
                         ["http://gitlab.addx.ai"], ["https://user:secret@gitlab.addx.ai"],
                         ["https://gitlab.addx.ai:443"], ["https://gitlab.addx.ai/path"],
                         ["https://gitlab.addx.ai?token=secret"], ["https://gitlab.addx.ai#x"],
                         ["https://gitlab.addx.ai?"], ["https://gitlab.addx.ai#"],
                         ["https://gitlab.addx.ai/"], ["\nhttps://gitlab.addx.ai"],
                         [None], "https://gitlab.addx.ai")
        with tempfile.TemporaryDirectory() as directory:
            owner = Path(directory) / "owner.json"
            for instances in bad_instances:
                with self.subTest(instances=instances):
                    owner.write_text(json.dumps({"instances": instances}))
                    self.assertIsNone(self.guard.load_owner_host(owner))
            owner.write_text("not-json")
            self.assertIsNone(self.guard.load_owner_host(owner))
            self.assertIsNone(self.guard.load_owner_host(owner.with_name("missing.json")))

    def test_missing_owner_keeps_help_available_and_blocks_network(self):
        spec = importlib.util.spec_from_file_location("binding_without_owner", SCRIPT)
        missing_owner_guard = importlib.util.module_from_spec(spec)
        with patch.object(Path, "read_text", side_effect=FileNotFoundError):
            spec.loader.exec_module(missing_owner_guard)
        self.assertIsNone(missing_owner_guard.HOST)
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as result:
            missing_owner_guard.main(["--help"])
        self.assertEqual(0, result.exception.code)
        with patch.object(self.guard, "HOST", None):
            code, result, reader = self.run_cli([
                "--root-issue", ROOT_URL, "--work-item", WORK_URL,
                "--repository", REPOSITORY])
            self.assertNotEqual(0, code)
            self.assertFalse(result["verified"])
            reader.assert_not_called()
            with patch.object(self.guard.subprocess, "run") as run:
                with self.assertRaises(ValueError):
                    self.guard.read_api("/safe/glab", "projects/773/issues/23")
                run.assert_not_called()

    def test_closed_or_unassigned_issue_is_rejected(self):
        for role in ("root", "work"):
            for delta in ({"state": "closed"}, {"assignees": []}):
                with self.subTest(role=role, delta=delta):
                    target = copy.deepcopy(getattr(self, role))
                    target.update(delta)
                    self.assertTrue(self.check(**{role: target}))

    def test_wrong_repository_or_identity_is_rejected(self):
        self.assertTrue(self.check(repository="engineering/other"))
        for delta in ({"iid": 999}, {"id": None}, {"project_id": -1}):
            self.assertTrue(self.check(work={**self.work, **delta}))

    def test_missing_or_forged_native_link_is_rejected(self):
        self.assertTrue(self.check(links={}))
        self.assertTrue(self.check(links={"root": self.links["root"], "work": []}))
        forged = copy.deepcopy(self.links)
        forged["work"][0]["id"] = 99
        self.assertTrue(self.check(links=forged))

    def test_untrusted_urls_are_rejected_without_api_call(self):
        for bad in ("http://gitlab.addx.ai/a/b/-/issues/1",
                    "https://gitlab.addx.ai.evil/a/b/-/issues/1",
                    "https://user:secret@gitlab.addx.ai/a/b/-/issues/1",
                    "https://gitlab.addx.ai:444/a/b/-/issues/1",
                    "https://gitlab.addx.ai/a/%2e%2e/-/issues/1",
                    "\n" + ROOT_URL, ROOT_URL.replace("/WEB/", "/W\nEB/"),
                    ROOT_URL + "?token=secret", ROOT_URL + "#note_1"):
            with self.subTest(url=bad):
                code, result, reader = self.run_cli([
                    "--root-issue", bad, "--work-item", WORK_URL,
                    "--repository", REPOSITORY])
                self.assertNotEqual(0, code)
                self.assertFalse(result["verified"])
                reader.assert_not_called()
                self.assertNotIn("secret", json.dumps(result))

    def test_live_cli_checks_both_link_directions_and_redacts_body(self):
        code, result, reader = self.run_cli([
            "--root-issue", ROOT_URL, "--work-item", WORK_URL,
            "--repository", REPOSITORY],
            [self.root, self.work, self.links["work"], self.links["root"]])
        self.assertEqual(0, code)
        self.assertTrue(result["verified"])
        self.assertEqual("live", result["mode"])
        self.assertFalse(result["authorizes_write"])
        self.assertIn("checked_at", result)
        self.assertEqual(4, reader.call_count)
        self.assertNotIn("DO_NOT_ECHO_SECRET", json.dumps(result))

    def test_same_issue_uses_no_links_and_binds_requested_identity(self):
        args = ["--root-issue", WORK_URL, "--work-item", WORK_URL,
                "--repository", REPOSITORY]
        code, result, reader = self.run_cli(args, [self.work, self.work])
        self.assertEqual(0, code)
        self.assertEqual(2, reader.call_count)
        code, result, reader = self.run_cli(args, [self.root, self.work])
        self.assertNotEqual(0, code)
        self.assertFalse(result["verified"])

    def test_single_issue_may_belong_to_another_project(self):
        code, result, reader = self.run_cli(
            ["--issue", ROOT_URL, "--repository", REPOSITORY], [self.root])
        self.assertEqual(0, code, result)
        self.assertTrue(result["verified"])
        self.assertEqual("single_issue", result["relationship_scope"])
        self.assertEqual(1, reader.call_count)
        self.assertNotIn("DO_NOT_ECHO_SECRET", json.dumps(result))

    def test_single_issue_still_requires_open_assigned_matching_issue(self):
        for bad in ({**self.root, "state": "closed"}, {**self.root, "assignees": []},
                    {**self.root, "web_url": WORK_URL}):
            code, result, _ = self.run_cli(["--issue", ROOT_URL, "--repository", REPOSITORY], [bad])
            self.assertNotEqual(0, code)
            self.assertFalse(result["verified"])

    def test_single_issue_rejects_mixed_or_missing_arguments(self):
        for args in (["--issue", ROOT_URL, "--work-item", WORK_URL, "--repository", REPOSITORY],
                     ["--issue", ROOT_URL]):
            code, result, reader = self.run_cli(args)
            self.assertNotEqual(0, code)
            reader.assert_not_called()

    def test_offline_single_issue_fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory) / "snapshot.json"
            snapshot.write_text(json.dumps({"issue": self.root, "repository": REPOSITORY}))
            code, result, reader = self.run_cli(["--check-snapshot", str(snapshot)])
        self.assertEqual(0, code, result)
        self.assertEqual("OFFLINE_VALID", result["verdict"])
        self.assertFalse(result["verified"])
        reader.assert_not_called()

    def test_read_api_uses_explicit_get_and_never_exposes_error_output(self):
        with patch.object(self.guard.subprocess, "run") as run:
            run.return_value.stdout = '{"state":"opened"}'
            self.assertEqual({"state": "opened"}, self.guard.read_api("/safe/glab", "projects/773/issues/23"))
            argv = run.call_args.args[0]
            self.assertEqual(["/safe/glab", "api", "--hostname", "gitlab.addx.ai",
                              "--method", "GET", "projects/773/issues/23"], argv)
            self.assertFalse(run.call_args.kwargs.get("shell", False))
        with patch.object(self.guard, "read_api", side_effect=RuntimeError("DO_NOT_ECHO_SECRET")):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = self.guard.main(["--root-issue", ROOT_URL, "--work-item", WORK_URL,
                                        "--repository", REPOSITORY])
            self.assertNotEqual(0, code)
            self.assertNotIn("DO_NOT_ECHO_SECRET", output.getvalue())

    def test_offline_fixture_cannot_authorize_and_never_uses_network(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory) / "snapshot.json"
            snapshot.write_text(json.dumps({"root": self.root, "work": self.work,
                                            "links": self.links, "repository": REPOSITORY}))
            code, result, reader = self.run_cli(["--check-snapshot", str(snapshot)])
        self.assertEqual(0, code)
        self.assertEqual("offline", result["mode"])
        self.assertEqual("OFFLINE_VALID", result["verdict"])
        self.assertFalse(result["verified"])
        self.assertFalse(result["authorizes_write"])
        reader.assert_not_called()


if __name__ == "__main__":
    unittest.main()
