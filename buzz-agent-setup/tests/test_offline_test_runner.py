import importlib.util
import io
import json
from pathlib import Path
import subprocess
import unittest
from contextlib import redirect_stderr
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_offline_tests.py"
SPEC = importlib.util.spec_from_file_location("buzz_agent_setup_offline_runner", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class OfflineTestRunnerTest(unittest.TestCase):
    def test_runner_executes_real_discovery_and_propagates_success(self):
        result = subprocess.run(
            [
                MODULE.sys.executable,
                str(SCRIPT),
                "--pattern",
                "test_gitlab_buzz_sync_launchd.py",
            ],
            cwd=MODULE.REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout.splitlines()[-1]), {"status": "ok", "returncode": 0})

    def test_runner_canonicalizes_tmpdir_and_uses_fixed_test_root(self):
        completed = subprocess.CompletedProcess([], 0)
        raw_tmp = "/var/tmp"
        with mock.patch.object(MODULE.tempfile, "gettempdir", return_value=raw_tmp), \
             mock.patch.object(MODULE.subprocess, "run", return_value=completed) as run:
            self.assertEqual(MODULE.main(["--pattern", "test_gitlab_buzz_sync*.py", "--verbose"]), 0)

        command = run.call_args.args[0]
        kwargs = run.call_args.kwargs
        self.assertEqual(kwargs["env"]["TMPDIR"], str(Path(raw_tmp).resolve()))
        self.assertEqual(kwargs["cwd"], MODULE.REPO_ROOT)
        self.assertEqual(command[:4], [MODULE.sys.executable, "-m", "unittest", "discover"])
        self.assertEqual(command[command.index("-s") + 1], str(MODULE.TEST_DIR))
        self.assertEqual(command[command.index("-p") + 1], "test_gitlab_buzz_sync*.py")
        self.assertEqual(command[-1], "-v")

    def test_runner_rejects_a_pattern_that_escapes_the_test_root(self):
        with self.assertRaises(SystemExit):
            MODULE.parse_args(["--pattern", "../test_*.py"])

    def test_runner_reports_spawn_failure_without_a_traceback(self):
        stderr = io.StringIO()
        with mock.patch.object(MODULE.subprocess, "run", side_effect=OSError("private path")), \
             redirect_stderr(stderr):
            self.assertEqual(MODULE.main([]), 2)
        self.assertEqual(json.loads(stderr.getvalue()), {"status": "error", "error_type": "OSError"})


if __name__ == "__main__":
    unittest.main()
