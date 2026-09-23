import json
import os
import stat
import subprocess
import tempfile
import unittest
import hashlib
import hmac
import shutil
from pathlib import Path
from typing import Optional
from unittest import mock


SCRIPT = Path(__file__).with_name("create_mr_note.sh")
API_URL = "https://" + ".".join(("gitlab", "addx", "ai")) + "/api/v4"


MOCK_CURL = r'''#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

args = sys.argv[1:]
output = Path(args[args.index("--output") + 1])
method = args[args.index("--request") + 1] if "--request" in args else "GET"
state_path = Path(os.environ["MOCK_CURL_STATE"])
state = json.loads(state_path.read_text()) if state_path.exists() else {"notes": [], "requests": [], "writer_id": 9001}
url = args[-1]
state["requests"].append({
    "method": method,
    "url": url,
    "leaked_env": [key for key in (
        "GITLAB_TOKEN", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy",
        "ALL_PROXY", "all_proxy", "SSL_CERT_FILE", "SSL_CERT_DIR",
        "SSLKEYLOGFILE", "CURL_CA_BUNDLE",
    ) if os.environ.get(key)],
    "argv_contains_token": any("test-token" in arg for arg in args),
})

if method == "GET" and url.endswith("/user"):
    output.write_text(json.dumps({"id": state.get("writer_id", 9001), "username": state.get("writer_username", "reviewer")}))
    status = "200"
elif method == "GET" and "/notes/" in urlparse(url).path:
    note_id = int(urlparse(url).path.rsplit("/", 1)[1])
    note = next((note for note in state["notes"] if note["id"] == note_id), None)
    output.write_text(json.dumps(note if note is not None else {"message": "404"}))
    status = "200" if note is not None else "404"
elif method == "GET":
    query = parse_qs(urlparse(url).query)
    page = int(query.get("page", ["1"])[0])
    if state.get("fail_get_page") == page:
        output.write_text(json.dumps({"message": "injected page failure"}))
        state_path.write_text(json.dumps(state))
        sys.stdout.write("500")
        sys.exit(0)
    per_page = int(query.get("per_page", ["100"])[0])
    start = (page - 1) * per_page
    output.write_text(json.dumps(state["notes"][start:start + per_page]))
    status = "200"
elif method == "POST":
    data_arg = args[args.index("--data-binary") + 1]
    payload = json.loads(Path(data_arg.removeprefix("@")).read_text())
    note_id = len(state["notes"]) + 1
    note = {"id": note_id, "body": payload["body"], "author": {"id": state.get("writer_id", 9001)}}
    state["notes"].insert(0, note)
    output.write_text(json.dumps(note))
    if state.get("post_response_lost_once"):
        state["post_response_lost_once"] = False
        status = "000"
    else:
        status = "201"
else:
    output.write_text(json.dumps({"error": "unexpected mutation"}))
    status = "500"

state_path.write_text(json.dumps(state))
sys.stdout.write(status)
'''


class CreateMrNoteTest(unittest.TestCase):
    REVIEWED_HEAD = "a" * 40

    @staticmethod
    def marker(review_run_id: str, reviewed_head: str = REVIEWED_HEAD) -> str:
        digest = hmac.new(
            b"test-token", f"{review_run_id}:{reviewed_head}".encode(), hashlib.sha256
        ).hexdigest()
        return f"<!-- code-review-run:{digest} -->"

    @staticmethod
    def install_mock(temp_dir: Path) -> None:
        bin_dir = temp_dir / "bin"
        bin_dir.mkdir()
        mock_curl = bin_dir / "curl"
        mock_curl.write_text(MOCK_CURL)
        mock_curl.chmod(mock_curl.stat().st_mode | stat.S_IXUSR)

    def run_script(
        self, temp_dir: Path, body: str, review_run_id: str, *, check: bool = True,
        expected_writer_username: Optional[str] = None, test_mode: bool = True,
        extra_env: Optional[dict[str, str]] = None,
    ) -> subprocess.CompletedProcess[str]:
        body_file = temp_dir / "review.md"
        body_file.write_text(body)
        env = os.environ.copy()
        env["GITLAB_TOKEN"] = "test-token"
        env["MOCK_CURL_STATE"] = str(temp_dir / "curl-state.json")
        env["CODE_REVIEW_CURL_BIN"] = str(temp_dir / "bin" / "curl")
        env["CODE_REVIEW_NODE_BIN"] = shutil.which("node") or "/usr/bin/node"
        env["CODE_REVIEW_CA_FILE"] = "/etc/ssl/cert.pem" if Path("/etc/ssl/cert.pem").exists() else "/etc/ssl/certs/ca-certificates.crt"
        if test_mode:
            env["CODE_REVIEW_TEST_MODE"] = "1"
        if expected_writer_username is not None:
            env["CODE_REVIEW_EXPECTED_WRITER_USERNAME"] = expected_writer_username
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            [
                str(SCRIPT), API_URL, "10", "20",
                str(body_file), review_run_id, self.REVIEWED_HEAD,
            ],
            check=check,
            capture_output=True,
            text=True,
            env=env,
        )

    def test_hostile_node_options_do_not_reach_attested_node(self):
        with tempfile.TemporaryDirectory() as raw_temp_dir:
            temp_dir = Path(raw_temp_dir)
            self.install_mock(temp_dir)
            result = self.run_script(
                temp_dir,
                "review",
                "run-clean-node-env",
                extra_env={"NODE_OPTIONS": "--require=/definitely-not-present/code-review-injection.js"},
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("created_note_id=1", result.stdout)

    def test_proxy_and_ca_environment_are_removed_from_curl_child(self):
        with tempfile.TemporaryDirectory() as raw_temp_dir:
            temp_dir = Path(raw_temp_dir)
            self.install_mock(temp_dir)
            env_patch = {
                "HTTPS_PROXY": "https://attacker.invalid",
                "ALL_PROXY": "socks5://attacker.invalid",
                "SSL_CERT_FILE": "/tmp/attacker-ca",
                "SSL_CERT_DIR": "/tmp/attacker-ca-dir",
                "SSLKEYLOGFILE": "/tmp/attacker-keylog",
                "CURL_CA_BUNDLE": "/tmp/attacker-ca",
            }
            with mock.patch.dict(os.environ, env_patch, clear=False):
                result = self.run_script(temp_dir, "review", "run-clean-env")
            self.assertIn("created_note_id=1", result.stdout)
            state = json.loads((temp_dir / "curl-state.json").read_text())
            self.assertTrue(state["requests"])
            self.assertTrue(all(not request["leaked_env"] for request in state["requests"]))
            self.assertTrue(all(not request["argv_contains_token"] for request in state["requests"]))

    def test_new_reviews_post_new_notes_but_same_run_only_reads_back(self):
        with tempfile.TemporaryDirectory() as raw_temp_dir:
            temp_dir = Path(raw_temp_dir)
            self.install_mock(temp_dir)

            first = self.run_script(temp_dir, "first review", "run-1")
            retry = self.run_script(temp_dir, "changed retry body", "run-1")
            second = self.run_script(temp_dir, "second review", "run-2")

            self.assertIn("created_note_id=1", first.stdout)
            self.assertIn("existing_note_id=1", retry.stdout)
            self.assertIn("created_note_id=2", second.stdout)

            state = json.loads((temp_dir / "curl-state.json").read_text())
            methods = [request["method"] for request in state["requests"]]
            self.assertEqual(
                methods,
                ["GET", "GET", "POST", "GET", "GET", "GET", "GET", "GET", "POST", "GET"],
            )
            self.assertNotIn("PUT", methods)
            self.assertNotIn("PATCH", methods)
            self.assertIn("first review", state["notes"][1]["body"])
            self.assertIn(f"<!-- code-review-head:{self.REVIEWED_HEAD} -->", state["notes"][1]["body"])
            self.assertNotIn("changed retry body", state["notes"][1]["body"])

    def test_expected_writer_mismatch_is_fail_closed_before_note_lookup_or_post(self):
        with tempfile.TemporaryDirectory() as raw_temp_dir:
            temp_dir = Path(raw_temp_dir)
            self.install_mock(temp_dir)
            initial = {"notes": [], "requests": [], "writer_id": 9001, "writer_username": "MR_User"}
            (temp_dir / "curl-state.json").write_text(json.dumps(initial))

            failed = self.run_script(
                temp_dir, "must not post", "run-wrong-writer", check=False,
                expected_writer_username="zlin",
            )

            self.assertEqual(failed.returncode, 3)
            self.assertIn("expected zlin, got MR_User", failed.stderr)
            state = json.loads((temp_dir / "curl-state.json").read_text())
            self.assertEqual([request["method"] for request in state["requests"]], ["GET"])

    def test_public_sha_marker_cannot_suppress_real_review(self):
        with tempfile.TemporaryDirectory() as raw_temp_dir:
            temp_dir = Path(raw_temp_dir)
            self.install_mock(temp_dir)
            public_digest = hashlib.sha256(b"run-public").hexdigest()
            forged = {"id": 77, "body": f"<!-- code-review-run:{public_digest} -->\nforged", "author": {"id": 123}}
            (temp_dir / "curl-state.json").write_text(json.dumps({"notes": [forged], "requests": [], "writer_id": 9001}))

            result = self.run_script(temp_dir, "real review", "run-public")

            self.assertIn("created_note_id=2", result.stdout)
            state = json.loads((temp_dir / "curl-state.json").read_text())
            self.assertIn(self.marker("run-public"), state["notes"][0]["body"])
            self.assertIn("real review", state["notes"][0]["body"])

    def test_existing_marker_on_second_page_is_read_back_without_post(self):
        with tempfile.TemporaryDirectory() as raw_temp_dir:
            temp_dir = Path(raw_temp_dir)
            self.install_mock(temp_dir)
            notes = [{"id": i + 1, "body": f"decoy {i}"} for i in range(100)]
            notes.append({
                "id": 101,
                "body": (
                    f'{self.marker("run-page-2")}\n'
                    f'<!-- code-review-head:{self.REVIEWED_HEAD} -->\noriginal review'
                ),
                "author": {"id": 9001},
            })
            (temp_dir / "curl-state.json").write_text(json.dumps({"notes": notes, "requests": [], "writer_id": 9001}))

            result = self.run_script(temp_dir, "retry body", "run-page-2")

            self.assertIn("existing_note_id=101", result.stdout)
            state = json.loads((temp_dir / "curl-state.json").read_text())
            methods = [request["method"] for request in state["requests"]]
            self.assertEqual(methods, ["GET", "GET", "GET"])
            self.assertEqual(len(state["notes"]), 101)

    def test_copied_hmac_marker_from_other_author_is_not_accepted(self):
        with tempfile.TemporaryDirectory() as raw_temp_dir:
            temp_dir = Path(raw_temp_dir)
            self.install_mock(temp_dir)
            copied = {
                "id": 88,
                "body": (
                    f'{self.marker("run-copied")}\n'
                    f'<!-- code-review-head:{self.REVIEWED_HEAD} -->\ncopied'
                ),
                "author": {"id": 123},
            }
            state = {"notes": [copied], "requests": [], "writer_id": 9001}
            (temp_dir / "curl-state.json").write_text(json.dumps(state))

            result = self.run_script(temp_dir, "real review", "run-copied")

            self.assertIn("created_note_id=2", result.stdout)
            state = json.loads((temp_dir / "curl-state.json").read_text())
            self.assertEqual(state["notes"][0]["author"]["id"], 9001)
            self.assertIn("real review", state["notes"][0]["body"])

    def test_post_response_loss_is_recovered_by_serial_retry(self):
        with tempfile.TemporaryDirectory() as raw_temp_dir:
            temp_dir = Path(raw_temp_dir)
            self.install_mock(temp_dir)
            initial = {"notes": [], "requests": [], "writer_id": 9001, "post_response_lost_once": True}
            (temp_dir / "curl-state.json").write_text(json.dumps(initial))

            failed = self.run_script(temp_dir, "persisted review", "run-loss", check=False)
            retry = self.run_script(temp_dir, "retry body", "run-loss")

            self.assertEqual(failed.returncode, 4)
            self.assertIn("existing_note_id=1", retry.stdout)
            state = json.loads((temp_dir / "curl-state.json").read_text())
            methods = [request["method"] for request in state["requests"]]
            self.assertEqual(methods.count("POST"), 1)
            self.assertEqual(len(state["notes"]), 1)

    def test_pagination_failure_is_fail_closed_without_post(self):
        with tempfile.TemporaryDirectory() as raw_temp_dir:
            temp_dir = Path(raw_temp_dir)
            self.install_mock(temp_dir)
            notes = [{"id": i + 1, "body": f"decoy {i}"} for i in range(100)]
            initial = {"notes": notes, "requests": [], "writer_id": 9001, "fail_get_page": 2}
            (temp_dir / "curl-state.json").write_text(json.dumps(initial))

            failed = self.run_script(temp_dir, "must not post", "run-page-fail", check=False)

            self.assertEqual(failed.returncode, 3)
            state = json.loads((temp_dir / "curl-state.json").read_text())
            methods = [request["method"] for request in state["requests"]]
            self.assertNotIn("POST", methods)


if __name__ == "__main__":
    unittest.main()
