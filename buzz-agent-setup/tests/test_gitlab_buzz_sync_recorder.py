"""Safety contracts for the local GitLab fixture recorder."""

import importlib.util
import unittest
from pathlib import Path


RECORDER_PATH = (
    Path(__file__).resolve().parent / "fixtures" / "gitlab_buzz_sync" / "record_gitlab.py"
)


def load_recorder():
    spec = importlib.util.spec_from_file_location("gitlab_buzz_sync_recorder_test", RECORDER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RECORDER = load_recorder()


class RecorderCredentialLifecycleTest(unittest.TestCase):
    def test_every_created_project_access_token_is_revoked_by_id(self):
        """L1-GIS-091 Recorder cleanup revokes each temporary project token and clears its ledger."""
        class FakeGitLab:
            project_id = 481
            created_project_access_token_ids = [71, 72]

            def __init__(self):
                self.calls = []

            def ok(self, role, method, path, *, expect):
                self.calls.append((role, method, path, expect))

        gitlab = FakeGitLab()
        RECORDER.revoke_created_access_tokens(gitlab)

        self.assertEqual(gitlab.calls, [
            ("maintainer", "DELETE", "projects/481/access_tokens/71", (200, 204, 404)),
            ("maintainer", "DELETE", "projects/481/access_tokens/72", (200, 204, 404)),
        ])
        self.assertEqual(gitlab.created_project_access_token_ids, [])


if __name__ == "__main__":
    unittest.main()
