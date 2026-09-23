from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from user_research import SafeApiError
from user_research.environment import load_local_environment


class EnvironmentTests(unittest.TestCase):
    def test_loads_only_allowed_values_without_overriding_host_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env.local"
            path.write_text(
                "AUDIENCE_PLATFORM_BASE_URL=https://example.test\nAUDIENCE_API_KEY='local-secret'\n"
            )
            with patch.dict(os.environ, {"AUDIENCE_API_KEY": "host-secret"}, clear=True):
                load_local_environment(path)
                self.assertEqual(os.environ["AUDIENCE_API_KEY"], "host-secret")
                self.assertEqual(os.environ["AUDIENCE_PLATFORM_BASE_URL"], "https://example.test")

    def test_rejects_unknown_keys_and_shell_syntax_without_execution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env.local"
            path.write_text("UNREVIEWED_KEY=$(touch should-not-run)\n")
            with self.assertRaisesRegex(SafeApiError, "invalid_env_file"):
                load_local_environment(path)
            self.assertFalse((Path(directory) / "should-not-run").exists())


if __name__ == "__main__":
    unittest.main()
