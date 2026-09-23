import os
import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1]
SCRIPT = SCRIPT_DIR / "create_mr_note.sh"
RUNTIME_ATTESTATION = SCRIPT_DIR / "runtime_attestation.sh"
CI_PATH = SCRIPT_DIR.parents[2] / ".gitlab-ci.yml"
API_URL = "https://" + ".".join(("gitlab", "addx", "ai")) + "/api/v4"
REVIEWED_HEAD = "a" * 40


class CreateMrNoteRuntimeTest(unittest.TestCase):
    def test_ci_installs_fixed_runtime_dependencies(self):
        ci = CI_PATH.read_text()
        self.assertIn("apt-get install -y -qq curl nodejs", ci)

    def test_runtime_tools_are_absolute_and_curlrc_is_disabled(self):
        script = SCRIPT.read_text()
        runtime = RUNTIME_ATTESTATION.read_text()
        self.assertIn("runtime_attestation.sh", script)
        self.assertIn("CURL_BIN=/usr/bin/curl", runtime)
        self.assertIn("runtime overrides are test-only", runtime)
        self.assertIn("CODE_REVIEW_CURL_SHA256", runtime)
        self.assertIn("CODE_REVIEW_NODE_SHA256", runtime)
        self.assertIn("runtime SHA-256 mismatch", runtime)
        self.assertIn("runtime canonical path mismatch", runtime)
        self.assertIn("MACOS_APPROVED_NODE_SHA256", runtime)
        self.assertIn("/usr/local/bin/node", runtime)
        self.assertIn('/usr/bin/uname -s', runtime)
        self.assertIn('Darwin)', runtime)
        self.assertIn('Linux)', runtime)
        self.assertEqual(script.count("curl_clean -sS"), 4)
        self.assertIn("clean_exec", script)
        self.assertIn("/usr/bin/env -i", runtime)
        self.assertIn("\"${CURL_BIN}\" -q --noproxy '*' --cacert \"${CA_FILE}\" --header @-", script)
        self.assertNotIn("$(curl ", script)
        self.assertNotIn("$(node ", script)
        self.assertNotIn('PRIVATE-TOKEN: ${GITLAB_TOKEN}', script)
        self.assertNotIn("header_tmp", script)

    def test_linux_uses_local_node_when_system_node_is_missing(self):
        with tempfile.TemporaryDirectory() as raw_temp_dir:
            temp_dir = Path(raw_temp_dir)
            local_node = temp_dir / "usr-local-node"
            local_node.write_text("local node")
            local_node.chmod(0o700)

            selected = subprocess.run(
                [
                    "/bin/bash", "-c",
                    'source "$1"; '
                    'select_node_runtime Linux "$2" "$3" "$4"; '
                    'printf "%s|%s|%s" "$NODE_BIN" "$node_owner" "$node_uses_built_in_pin"',
                    "runtime-test", str(RUNTIME_ATTESTATION),
                    str(temp_dir / "missing-system-node"), str(local_node),
                    str(temp_dir / "macos-node"),
                ],
                check=False, capture_output=True, text=True,
            )

            self.assertEqual(selected.returncode, 0, selected.stderr)
            self.assertEqual(selected.stdout, f"{local_node}|0|0")

    def test_production_rejects_runtime_overrides_without_attestation(self):
        with tempfile.TemporaryDirectory() as raw_temp_dir:
            temp_dir = Path(raw_temp_dir)
            mock_curl = temp_dir / "http-client"
            mock_curl.write_text("#!/usr/bin/env bash\nexit 99\n")
            mock_curl.chmod(0o700)
            body_file = temp_dir / "review.md"
            body_file.write_text("review")
            env = os.environ.copy()
            env["GITLAB_TOKEN"] = "production-token"
            env["CODE_REVIEW_CURL_BIN"] = str(mock_curl)

            failed = subprocess.run(
                [str(SCRIPT), API_URL, "10", "20", str(body_file), "run-unpinned", REVIEWED_HEAD],
                check=False, capture_output=True, text=True, env=env,
            )

            self.assertEqual(failed.returncode, 1)
            self.assertIn("runtime overrides are test-only", failed.stderr)

    def test_production_requires_runtime_hash_attestation(self):
        with tempfile.TemporaryDirectory() as raw_temp_dir:
            temp_dir = Path(raw_temp_dir)
            body_file = temp_dir / "review.md"
            body_file.write_text("review")
            env = os.environ.copy()
            env["GITLAB_TOKEN"] = "production-token"
            for key in (
                "CODE_REVIEW_TEST_MODE", "CODE_REVIEW_CURL_BIN",
                "CODE_REVIEW_NODE_BIN", "CODE_REVIEW_CA_FILE",
                "CODE_REVIEW_CURL_SHA256", "CODE_REVIEW_NODE_SHA256",
            ):
                env.pop(key, None)

            failed = subprocess.run(
                [str(SCRIPT), API_URL, "10", "20", str(body_file), "run-missing-attestation", REVIEWED_HEAD],
                check=False, capture_output=True, text=True, env=env,
            )

            self.assertEqual(failed.returncode, 1)
            self.assertIn("runtime SHA-256 attestation is required", failed.stderr)

    def run_metadata_check(self, path: Path, digest: str, trusted_owner: str):
        return subprocess.run(
            [
                "/bin/bash", "-c",
                'source "$1"; verify_tool_metadata "$2" fixture "$3" "$4"',
                "runtime-test", str(RUNTIME_ATTESTATION), str(path), digest, trusted_owner,
            ],
            check=False, capture_output=True, text=True,
        )

    def test_metadata_attestation_checks_hash_mode_and_leaf_symlink(self):
        with tempfile.TemporaryDirectory() as raw_temp_dir:
            temp_dir = Path(raw_temp_dir)
            tool = temp_dir.resolve() / "tool"
            tool.write_bytes(b"trusted runtime")
            tool.chmod(0o700)
            digest = hashlib.sha256(tool.read_bytes()).hexdigest()
            owner = str(os.getuid())

            self.assertEqual(self.run_metadata_check(tool, digest, owner).returncode, 0)

            wrong = self.run_metadata_check(tool, "0" * 64, owner)
            self.assertEqual(wrong.returncode, 1)
            self.assertIn("SHA-256 mismatch", wrong.stderr)

            tool.chmod(0o720)
            writable = self.run_metadata_check(tool, digest, owner)
            self.assertEqual(writable.returncode, 1)
            self.assertIn("group/other writable", writable.stderr)

            tool.chmod(0o700)
            link = temp_dir / "tool-link"
            link.symlink_to(tool)
            symlink = self.run_metadata_check(link, digest, owner)
            self.assertEqual(symlink.returncode, 1)
            self.assertIn("must not be a symlink", symlink.stderr)

            wrong_owner = self.run_metadata_check(tool, digest, "0")
            if os.getuid() != 0:
                self.assertEqual(wrong_owner.returncode, 1)
                self.assertIn("owner is not trusted", wrong_owner.stderr)

    def test_metadata_attestation_rejects_ancestor_symlink(self):
        with tempfile.TemporaryDirectory() as raw_temp_dir:
            temp_dir = Path(raw_temp_dir)
            temp_dir = temp_dir.resolve()
            real_dir = temp_dir / "real"
            real_dir.mkdir()
            tool = real_dir / "tool"
            tool.write_bytes(b"trusted runtime")
            tool.chmod(0o700)
            alias = temp_dir / "alias"
            alias.symlink_to(real_dir, target_is_directory=True)
            digest = hashlib.sha256(tool.read_bytes()).hexdigest()

            failed = self.run_metadata_check(alias / "tool", digest, str(os.getuid()))
            self.assertEqual(failed.returncode, 1)
            self.assertIn("runtime canonical path mismatch", failed.stderr)

    def test_test_mode_rejects_non_test_token_before_mock_execution(self):
        with tempfile.TemporaryDirectory() as raw_temp_dir:
            temp_dir = Path(raw_temp_dir)
            body_file = temp_dir / "review.md"
            body_file.write_text("review")
            env = os.environ.copy()
            env.update({
                "GITLAB_TOKEN": "production-token",
                "CODE_REVIEW_TEST_MODE": "1",
                "CODE_REVIEW_CURL_BIN": str(temp_dir / "missing-curl"),
                "CODE_REVIEW_NODE_BIN": str(temp_dir / "missing-node"),
                "CODE_REVIEW_CA_FILE": str(temp_dir / "missing-ca"),
            })

            failed = subprocess.run(
                [str(SCRIPT), API_URL, "10", "20", str(body_file), "run-test-token", REVIEWED_HEAD],
                check=False, capture_output=True, text=True, env=env,
            )
            self.assertEqual(failed.returncode, 1)
            self.assertIn("test mode requires the non-production test token", failed.stderr)


if __name__ == "__main__":
    unittest.main()
