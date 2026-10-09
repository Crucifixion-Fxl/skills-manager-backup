import importlib.util
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).with_name("feishu_login_qr_retry.py")
SPEC = importlib.util.spec_from_file_location("feishu_login_qr_retry", SCRIPT)
qr = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(qr)


class RetryTests(unittest.TestCase):
    def test_login_stops_after_first_receipted_qr(self):
        with tempfile.TemporaryDirectory() as folder:
            args = Namespace(browser_dir=Path(folder), max_sends=10, resend_seconds=.001,
                             poll_seconds=.001, qr_timeout_seconds=.001)
            with patch.object(qr, "logged_in", side_effect=[False, True]), \
                 patch.object(qr, "wait_for_qr", return_value=1), \
                 patch.object(qr, "send_and_readback") as send:
                self.assertEqual(qr.run(args), 0)
                self.assertEqual(send.call_count, 2)

    def test_ten_receipted_qrs_is_hard_limit(self):
        with tempfile.TemporaryDirectory() as folder:
            args = Namespace(browser_dir=Path(folder), max_sends=10, resend_seconds=.0001,
                             poll_seconds=.001, qr_timeout_seconds=.001)
            with patch.object(qr, "logged_in", return_value=False), \
                 patch.object(qr, "wait_for_qr", side_effect=range(1, 11)), \
                 patch.object(qr, "send_and_readback") as send:
                self.assertEqual(qr.run(args), 1)
                self.assertEqual(send.call_count, 20)

    def test_ready_marker_without_confirmed_state_is_not_login(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            (directory / "ready.txt").write_text("ready")
            (directory / "state.txt").write_text("QR 1 123")
            self.assertFalse(qr.logged_in(directory))
            (directory / "state.txt").write_text("LOGGED_IN https://open.feishu.cn/page/cli?user_code=x")
            self.assertTrue(qr.logged_in(directory))


if __name__ == "__main__":
    unittest.main()
