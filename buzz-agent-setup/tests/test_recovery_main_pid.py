"""L1 manager read boundary: clean context, bounded output and safe failures.

Only OS/filesystem/subprocess boundaries are doubles. No user services are
changed; these tests do not constitute installed systemd or Feishu acceptance.
"""
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import recovery_controller as controller


class MainPidTests(unittest.TestCase):
    def setUp(self):
        self.runtime = "/run/user/1234"
        self.directory = self.enterContext(patch.object(controller, "private_directory", side_effect=Path))
        self.command = self.enterContext(patch.object(controller.subprocess, "run",
            return_value=subprocess.CompletedProcess([], 0, "123\n")))
        self.enterContext(patch.dict(os.environ, {"XDG_RUNTIME_DIR": self.runtime,
            "DBUS_SESSION_BUS_ADDRESS": "unix:path=/untrusted/bus", "BUZZ_PRIVATE_KEY": "never-inherit",
            "SYSTEMD_BUS_ADDRESS": "unix:path=/another/bus", "LD_PRELOAD": "/untrusted/library"}, clear=True))

    def test_L1_REC_PID_001_fixed_manager_context_and_bounded_output(self):
        self.assertEqual(controller.systemd_main_pid("buzz-test-dev.service"), 123)
        args, options = self.command.call_args
        self.assertEqual(args[0], ["/usr/bin/systemctl", "--user", "show", "buzz-test-dev.service",
                                   "--property=MainPID", "--value"])
        self.assertEqual(options["env"], dict(XDG_RUNTIME_DIR=self.runtime, PATH="/usr/bin:/bin", LANG="C.UTF-8"))
        self.directory.assert_called_once_with(Path(self.runtime))
        self.assertEqual(options["timeout"], 10)
        self.assertEqual(options.get("stderr"), subprocess.DEVNULL)
        self.assertNotEqual(options.get("stdout"), subprocess.PIPE)
        self.assertTrue(callable(options.get("preexec_fn")))

    def test_L1_REC_PID_002_missing_interactive_env_uses_current_uid_runtime(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(controller.systemd_main_pid("buzz-test-dev.service"), 123)
        self.assertEqual(self.command.call_args.kwargs["env"].get("XDG_RUNTIME_DIR"), f"/run/user/{os.geteuid()}")

    def test_L1_REC_PID_003_manager_timeout_is_a_fixed_public_failure(self):
        for error in (subprocess.TimeoutExpired("secret-command", 10, output="private-output"),
                      subprocess.CalledProcessError(2, "secret-command", output="private-output"),
                      OSError("private-path")):
            with self.subTest(error=type(error).__name__):
                self.command.side_effect = error
                with self.assertRaises(Exception) as caught:
                    controller.systemd_main_pid("buzz-test-dev.service")
                self.assertIsInstance(caught.exception, ValueError)
                self.assertEqual(str(caught.exception), "agent systemd state unavailable")

    def test_L1_REC_PID_004_only_canonical_bounded_ascii_pid_is_accepted(self):
        for output in ("１２３\n", "00123\n", "2147483648\n", "9" * 1000, "123\n456\n", "", "-1\n"):
            with self.subTest(output=output[:20]):
                self.command.return_value = subprocess.CompletedProcess([], 0, output)
                with self.assertRaises(ValueError):
                    controller.systemd_main_pid("buzz-test-dev.service")
        for value in (0, 1, 123, 2147483647):
            self.command.return_value = subprocess.CompletedProcess([], 0, f"{value}\n")
            self.assertEqual(controller.systemd_main_pid("buzz-test-dev.service"), value)

    def test_L1_REC_PID_005_invalid_unit_is_rejected_before_any_command(self):
        for unit in ("--all", "other.service", "buzz-test.service\n", "buzz-test.timer", "buzz-../test.service"):
            with self.subTest(unit=unit), self.assertRaises(ValueError):
                controller.systemd_main_pid(unit)
        self.command.assert_not_called()

    def test_L1_REC_PID_006_untrusted_runtime_is_rejected_without_contacting_bus(self):
        self.directory.side_effect = ValueError("unsafe runtime")
        with self.assertRaises(ValueError):
            controller.systemd_main_pid("buzz-test-dev.service")
        self.command.assert_not_called()


if __name__ == "__main__":
    unittest.main()
