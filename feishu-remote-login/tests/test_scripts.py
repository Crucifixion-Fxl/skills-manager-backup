"""send_qr.sh / watch_login.sh / remote_login.js：用假的浏览器会话和假的 lark-cli 跑通每条路径。

假会话 = 一个线程，按 remote_login.js 的文件协议响应 shoot.txt（登录等待阶段）和 cmd.txt {"shoot":true}（命令模式）。
假 lark-cli = 一个记录参数的 shell 脚本，行为和真的一样：发图片时先输出 "uploading image: ..." 再输出 JSON。
"""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.abspath(os.path.join(HERE, "..", "scripts"))
sys.path.insert(0, HERE)
from test_qr_enlarge import encode, pattern  # noqa: E402  (reuse the PNG encoder)

SEND, WATCH, BROWSER = (os.path.join(SCRIPTS, n) for n in ("send_qr.sh", "watch_login.sh", "remote_login.js"))
CONTEXT = "用途：正在为某任务登录内部站点（测试）"
QR_PNG = encode(8, 8, 6, pattern(8, 8, 4))

FAKE_LARK = r"""#!/bin/bash
# records every call; FAKE_LARK_FAIL=text|image makes that call return ok:false
log="${FAKE_LARK_LOG:?}"
args="$*"; printf '%s\n' "${args//$'\n'/\\n}" >> "$log"   # one line per call: the text argument itself contains newlines
case "$*" in
  *--image*) echo "uploading image: qr-big.png"; [ "${FAKE_LARK_FAIL:-}" = image ] && { echo '{"ok": false}'; exit 1; } ;;
  *--text*) [ "${FAKE_LARK_FAIL:-}" = text ] && { echo '{"ok": false}'; exit 1; } ;;
esac
echo '{"ok": true, "data": {"message_id": "om_fake"}}'
"""


class FakeSession(threading.Thread):
    """Answers QR requests the way remote_login.js does, and can flip the page to "logged in" or "confirm on phone"."""

    def __init__(self, workdir, command_mode=False, png=None):
        super().__init__(daemon=True)
        self.dir, self.stop_evt, self.shots, self.png = workdir, threading.Event(), 0, png or QR_PNG
        self._w("url.txt", "https://accounts.feishu.cn/accounts/page/login?app_id=39")
        self._w("state.txt", "QR 0 0")
        self._w("page.txt", "扫码登录\n请使用 豆包/飞书移动端 扫码\n扫码成功\n")  # the static "扫码成功" is always on the real page
        if command_mode:
            self._w("ready.txt", "ready")

    def _w(self, name, text):
        with open(os.path.join(self.dir, name), "w") as f:
            f.write(text)

    def _has(self, name):
        return os.path.exists(os.path.join(self.dir, name))

    def run(self):
        while not self.stop_evt.is_set():
            wants = False
            command_mode = self._has("ready.txt")   # like the real session: shoot.txt only before login, cmd.txt only after
            if not command_mode and self._has("shoot.txt"):
                os.remove(os.path.join(self.dir, "shoot.txt")); wants = True
            if command_mode and self._has("cmd.txt"):
                with open(os.path.join(self.dir, "cmd.txt")) as f:
                    wants = json.load(f).get("shoot") is True
                os.remove(os.path.join(self.dir, "cmd.txt"))
            with open(os.path.join(self.dir, "state.txt")) as f:
                logged_in = f.read().startswith("LOGGED_IN")
            if wants and not logged_in:  # a logged-in page has no QR to refresh
                with open(os.path.join(self.dir, "qr.png"), "wb") as f:
                    f.write(self.png)
                self.shots += 1
                self._w("state.txt", f"QR {self.shots} {time.time_ns()}")
            time.sleep(0.05)

    def log_in(self, host="site.example"):
        self._w("url.txt", f"https://{host}/home")

    def stop(self):
        self.stop_evt.set()
        self.join(timeout=2)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = os.path.join(self.tmp.name, "work")
        os.mkdir(self.work, 0o700)
        self.bin = os.path.join(self.tmp.name, "fake-lark-cli")
        with open(self.bin, "w") as f:
            f.write(FAKE_LARK)
        os.chmod(self.bin, os.stat(self.bin).st_mode | stat.S_IXUSR)
        self.log = os.path.join(self.tmp.name, "lark.log")
        open(self.log, "w").close()

    def env(self, **extra):
        return {**os.environ, "LARK_CLI": self.bin, "FAKE_LARK_LOG": self.log, "REMOTE_LOGIN_OWNER_OPEN_ID": "ou_test", **extra}

    def run_sh(self, script, *args, timeout=30, **env):
        return subprocess.run(["bash", script, *args], capture_output=True, text=True, timeout=timeout, env=self.env(**env))

    def lark_calls(self):
        with open(self.log) as f:
            return [line for line in f.read().splitlines() if line]

    def session(self, **kw):
        s = FakeSession(self.work, **kw)
        s.start()
        self.addCleanup(s.stop)
        return s

    @staticmethod
    def last_json(stdout):
        return json.loads(stdout.strip().splitlines()[-1])


class SyntaxAndHelp(Base):
    def test_shell_scripts_parse(self):
        for script in (SEND, WATCH):
            self.assertEqual(subprocess.run(["bash", "-n", script]).returncode, 0, script)

    def test_help_exits_zero_and_prints_usage(self):
        for script in (SEND, WATCH):
            r = self.run_sh(script, "--help")
            self.assertEqual(r.returncode, 0)
            self.assertIn("--to <open_id>", r.stdout)
            self.assertNotIn("set -u", r.stdout, "help must print the comment block only, not code")

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_browser_script_parses_and_has_help(self):
        self.assertEqual(subprocess.run(["node", "--check", BROWSER]).returncode, 0)
        for flag in ("--help", "-h"):
            r = subprocess.run(["node", BROWSER, flag], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, flag)
            self.assertIn("usage", r.stdout, flag)


class SendQrGuards(Base):
    def test_refuses_to_send_an_image_without_context_text(self):
        self.session()
        r = self.run_sh(SEND, "--to", "ou_test", "--workdir", self.work)
        self.assertEqual(r.returncode, 2)
        self.assertIn("without a context text", r.stdout)
        self.assertEqual(self.lark_calls(), [], "nothing may be sent")

    def test_usage_errors(self):
        self.assertEqual(self.run_sh(SEND, "--context", CONTEXT).returncode, 2)  # no --to
        self.assertEqual(self.run_sh(SEND, "--to", "x", "--context", CONTEXT, "--as", "root").returncode, 2)
        self.assertEqual(self.run_sh(SEND, "--to", "x", "--context", CONTEXT, "--wait-seconds", "abc").returncode, 2)
        self.assertEqual(self.run_sh(SEND, "--to", "x", "--context", CONTEXT, "--workdir", "/nonexistent").returncode, 2)
        self.assertEqual(self.run_sh(SEND, "--bogus").returncode, 2)

    def test_no_session_in_directory(self):
        r = self.run_sh(SEND, "--to", "ou_test", "--context", CONTEXT, "--workdir", self.work)
        self.assertEqual(r.returncode, 3)
        self.assertIn("no live remote_login.js session", r.stdout)

    def test_session_that_gives_no_qr_is_reported_not_sent(self):
        FakeSession(self.work)  # files exist, but nobody answers shoot.txt
        r = self.run_sh(SEND, "--to", "ou_test", "--context", CONTEXT, "--workdir", self.work, "--wait-seconds", "1")
        self.assertEqual(r.returncode, 3)
        self.assertEqual(self.lark_calls(), [])


class SendQrFlow(Base):
    def assert_text_then_image(self, label="第1张"):
        calls = self.lark_calls()
        self.assertEqual(len(calls), 2, calls)
        text, image = calls
        self.assertIn("--as bot --user-id ou_test --text", text)
        for needle in (label, CONTEXT, "只扫最新一张", "确认登录", "约 25 秒失效"):
            self.assertIn(needle, text)
        self.assertIn("--as bot --user-id ou_test --image qr-big.png", image)
        self.assertNotIn("/", image.split("--image")[1], "lark-cli rejects absolute image paths")
        self.assertTrue(os.path.getsize(os.path.join(self.work, "qr-big.png")) > os.path.getsize(os.path.join(self.work, "qr.png")))

    def test_login_wait_phase_uses_shoot_txt_and_sends_text_before_image(self):
        self.session()
        r = self.run_sh(SEND, "--to", "ou_test", "--context", CONTEXT, "--workdir", self.work)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.last_json(r.stdout)["status"], "sent")
        self.assert_text_then_image()

    def test_command_mode_uses_shoot_command(self):
        self.session(command_mode=True)
        r = self.run_sh(SEND, "--to", "ou_test", "--context", CONTEXT, "--workdir", self.work, "--label", "第2张")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assert_text_then_image("第2张")

    def test_a_label_that_looks_like_a_number_stays_a_string(self):
        self.session()
        r = self.run_sh(SEND, "--to", "ou_test", "--context", CONTEXT, "--workdir", self.work, "--label", "#2")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(self.last_json(r.stdout)["label"], "#2")

    def test_the_sent_line_says_when_it_was_sent(self):
        self.session()
        r = self.run_sh(SEND, "--to", "ou_test", "--context", CONTEXT, "--workdir", self.work)
        out = self.last_json(r.stdout)
        self.assertEqual(out["status"], "sent")
        self.assertRegex(out["at"], r"^\d\d:\d\d:\d\d$")

    def test_a_qr_that_cannot_be_enlarged_never_sends_the_stale_big_image(self):
        self.session(png=b"this is not a png")
        with open(os.path.join(self.work, "qr-big.png"), "wb") as f:
            f.write(QR_PNG)  # the previous code, already expired
        r = self.run_sh(SEND, "--to", "ou_test", "--context", CONTEXT, "--workdir", self.work, "--wait-seconds", "3")
        self.assertEqual(r.returncode, 3, r.stdout)
        self.assertEqual(self.lark_calls(), [], "the expired QR must not go out under a new caption")

    def test_context_can_come_from_a_file(self):
        self.session()
        ctx = os.path.join(self.tmp.name, "ctx.txt")
        with open(ctx, "w") as f:
            f.write(CONTEXT)
        r = self.run_sh(SEND, "--to", "ou_test", "--context-file", ctx, "--workdir", self.work)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn(CONTEXT, self.lark_calls()[0])

    def test_dry_run_prints_the_text_and_sends_nothing(self):
        self.session()
        r = self.run_sh(SEND, "--to", "ou_test", "--context", CONTEXT, "--workdir", self.work, "--dry-run")
        self.assertEqual(r.returncode, 0)
        out = self.last_json(r.stdout)
        self.assertEqual(out["status"], "dry_run")
        self.assertIn(CONTEXT, out["text"])
        self.assertEqual(self.lark_calls(), [])

    def test_already_logged_in_sends_nothing(self):
        s = self.session()
        s._w("state.txt", "LOGGED_IN https://site.example/")
        s.log_in()  # the page really is off accounts.feishu.cn
        r = self.run_sh(SEND, "--to", "ou_test", "--context", CONTEXT, "--workdir", self.work, "--wait-seconds", "1")
        self.assertEqual((r.returncode, self.last_json(r.stdout)["status"]), (0, "logged_in"))
        self.assertEqual(self.lark_calls(), [])

    def test_text_failure_stops_before_the_image(self):
        self.session()
        r = self.run_sh(SEND, "--to", "ou_test", "--context", CONTEXT, "--workdir", self.work, FAKE_LARK_FAIL="text")
        self.assertEqual(r.returncode, 4)
        self.assertEqual(len(self.lark_calls()), 1, "an image must never go out without its text")

    def test_image_failure_is_reported(self):
        self.session()
        r = self.run_sh(SEND, "--to", "ou_test", "--context", CONTEXT, "--workdir", self.work, FAKE_LARK_FAIL="image")
        self.assertEqual(r.returncode, 5)


class OptionParsing(Base):
    def test_a_trailing_option_without_a_value_fails_fast_instead_of_spinning(self):
        cases = [(SEND, ["--to"]), (SEND, ["--to", "x", "--context"]), (SEND, ["--to", "x", "--context", "c", "--workdir"]),
                 (WATCH, ["--to", "x", "--context", "c", "--max"]), (WATCH, ["--to"]), (WATCH, ["--to", "x", "--context", "c", "--workdir"])]
        for script, args in cases:
            with self.subTest(script=os.path.basename(script), args=args):
                r = self.run_sh(script, *args, timeout=5)  # a hang would raise TimeoutExpired
                self.assertEqual(r.returncode, 2)
                self.assertEqual(self.last_json(r.stdout)["status"], "error")

    def test_every_json_line_stays_valid_when_values_contain_quotes(self):
        r = self.run_sh(SEND, "--to", "x", "--context", "c", "--workdir", '/no"such\\dir')
        self.assertEqual(self.last_json(r.stdout)["status"], "error")
        self.session()
        r = self.run_sh(SEND, "--to", "ou_test", "--context", CONTEXT, "--workdir", self.work, "--label", 'a"b\\c')
        self.assertEqual(self.last_json(r.stdout)["label"], 'a"b\\c')

    def test_workdir_must_be_a_private_directory(self):
        os.chmod(self.work, 0o755)
        for script in (SEND, WATCH):
            r = self.run_sh(script, "--to", "ou_test", "--context", "c", "--workdir", self.work)
            self.assertEqual(r.returncode, 2, script)
            self.assertIn("private", r.stdout)

    def test_context_file_is_size_capped(self):
        big = os.path.join(self.tmp.name, "big.txt")
        with open(big, "w") as f:
            f.write("x" * 5000)
        r = self.run_sh(SEND, "--to", "ou_test", "--context-file", big, "--workdir", self.work)
        self.assertEqual(r.returncode, 2)
        self.assertIn("too large", r.stdout)

    def test_without_an_owner_open_id_nothing_is_sent_to_anyone(self):
        self.session()
        for script, args in ((SEND, ("--to", "ou_test", "--context", CONTEXT, "--workdir", self.work)),
                             (SEND, ("--to", "ou_test", "--context", CONTEXT, "--workdir", self.work, "--dry-run")),
                             (WATCH, ("--to", "ou_test", "--context", CONTEXT, "--workdir", self.work, "--max", "2"))):
            r = self.run_sh(script, *args, REMOTE_LOGIN_OWNER_OPEN_ID="")
            self.assertEqual(r.returncode, 2, (script, r.stdout))
            self.assertIn("REMOTE_LOGIN_OWNER_OPEN_ID is required", r.stdout)
        self.assertEqual(self.lark_calls(), [])

    def test_the_watcher_refuses_a_recipient_that_is_not_the_owner_before_it_starts_watching(self):
        self.session()
        t0 = time.time()
        r = self.run_sh(WATCH, "--to", "ou_other", "--context", CONTEXT, "--workdir", self.work, "--max", "30", "--resend", "1")
        self.assertLess(time.time() - t0, 5, "must fail at start, not at the first resend")
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertEqual(self.lark_calls(), [])

    def test_recipient_is_pinned_to_the_owner_when_configured(self):
        self.session()
        r = self.run_sh(SEND, "--to", "ou_other", "--context", CONTEXT, "--workdir", self.work, REMOTE_LOGIN_OWNER_OPEN_ID="ou_owner")
        self.assertEqual(r.returncode, 2)
        self.assertIn("owner", r.stdout)
        self.assertEqual(self.lark_calls(), [])
        r = self.run_sh(SEND, "--to", "ou_owner", "--context", CONTEXT, "--workdir", self.work, REMOTE_LOGIN_OWNER_OPEN_ID="ou_owner")
        self.assertEqual(r.returncode, 0, r.stdout)


class SendQrSessionStates(Base):
    def test_stale_logged_in_state_while_still_on_the_feishu_page_is_not_a_login(self):
        s = self.session(command_mode=True)
        s._w("state.txt", "LOGGED_IN https://site.example/")  # left over from before the SSO click; the page is back on Feishu
        s.stop()  # nobody answers the shoot, like a page whose canvas has not rendered yet
        r = self.run_sh(SEND, "--to", "ou_test", "--context", CONTEXT, "--workdir", self.work, "--wait-seconds", "1")
        self.assertEqual(r.returncode, 3, r.stdout)
        self.assertNotEqual(self.last_json(r.stdout)["status"], "logged_in")
        self.assertEqual(self.lark_calls(), [])

    def test_a_stale_qr_png_is_never_sent(self):
        self.session(command_mode=True).stop()  # nobody answers, so no new QR is produced
        with open(os.path.join(self.work, "qr.png"), "wb") as f:
            f.write(QR_PNG)  # left over from an earlier round
        r = self.run_sh(SEND, "--to", "ou_test", "--context", CONTEXT, "--workdir", self.work, "--wait-seconds", "1")
        self.assertEqual(r.returncode, 3)
        self.assertEqual(self.lark_calls(), [])
        self.assertFalse(os.path.exists(os.path.join(self.work, "qr-big.png")))

    def test_dry_run_does_not_touch_the_live_session(self):
        s = self.session()
        r = self.run_sh(SEND, "--to", "ou_test", "--context", CONTEXT, "--workdir", self.work, "--dry-run")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(s.shots, 0, "a dry run must not refresh the QR the owner may be about to scan")
        self.assertFalse(os.path.exists(os.path.join(self.work, "shoot.txt")))

    def test_refuses_to_refresh_while_the_phone_confirmation_is_pending(self):
        s = self.session()
        s._w("page.txt", "扫码登录\n扫码成功\n请在飞书移动端确认登录\n")
        r = self.run_sh(SEND, "--to", "ou_test", "--context", CONTEXT, "--workdir", self.work)
        self.assertEqual(r.returncode, 6, r.stdout)
        self.assertEqual(self.last_json(r.stdout)["status"], "awaiting_phone_confirmation")
        self.assertEqual((s.shots, self.lark_calls()), (0, []))

    def test_a_pending_command_is_not_overwritten(self):
        self.session(command_mode=True).stop()
        pending = os.path.join(self.work, "cmd.txt")
        with open(pending, "w") as f:
            f.write('{"goto":"https://site.example/"}')
        r = self.run_sh(SEND, "--to", "ou_test", "--context", CONTEXT, "--workdir", self.work, "--wait-seconds", "1")
        self.assertEqual(r.returncode, 3)
        self.assertIn("pending", r.stdout)
        with open(pending) as f:
            self.assertEqual(f.read(), '{"goto":"https://site.example/"}')


class HostAndNumberEdgeCases(Base):
    def test_a_login_page_on_a_non_default_port_is_still_the_login_page(self):
        s = self.session()
        s._w("url.txt", "https://accounts.feishu.cn:8443/accounts/page/login")
        r = self.run_sh(SEND, "--to", "ou_test", "--context", CONTEXT, "--workdir", self.work)
        self.assertEqual(self.last_json(r.stdout)["status"], "sent", r.stdout)
        r = self.run_sh(WATCH, "--to", "ou_test", "--context", CONTEXT, "--workdir", self.work, "--max", "4", "--resend", "999")
        self.assertEqual(self.last_json(r.stdout)["status"], "timeout", "a port must not turn the login page into 'logged in'")

    def test_an_opaque_url_is_not_a_login(self):
        s = self.session()
        s._w("url.txt", "blob:(opaque)")
        r = self.run_sh(WATCH, "--to", "ou_test", "--context", CONTEXT, "--workdir", self.work, "--max", "4", "--resend", "999")
        self.assertEqual(self.last_json(r.stdout)["status"], "timeout")

    def test_numbers_with_a_leading_zero_are_decimal_not_octal(self):
        self.session()
        r = self.run_sh(SEND, "--to", "ou_test", "--context", CONTEXT, "--workdir", self.work, "--wait-seconds", "08")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.last_json(r.stdout)["status"], "sent")
        r = self.run_sh(SEND, "--to", "ou_test", "--context", CONTEXT, "--workdir", self.work, "--wait-seconds", "99999999999999999999")
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("too large", r.stdout)


class WatchLogin(Base):
    ARGS = ("--to", "ou_test", "--context", CONTEXT)

    def watch(self, *extra, **kw):
        return self.run_sh(WATCH, *self.ARGS, "--workdir", self.work, *extra, **kw)

    def test_requires_context(self):
        r = self.run_sh(WATCH, "--to", "ou_test", "--workdir", self.work)
        self.assertEqual(r.returncode, 2)
        self.assertIn("context text is required", r.stdout)

    def test_login_is_detected_from_the_url_leaving_feishu(self):
        s = self.session()
        threading.Timer(1.5, s.log_in).start()
        r = self.watch("--max", "20", "--resend", "999")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(self.last_json(r.stdout), {"status": "logged_in", "host": "site.example", "sent": 1})

    def test_scanned_and_waiting_for_the_phone_never_triggers_a_refresh(self):
        s = self.session()
        s._w("page.txt", "扫码登录\n扫码成功\n请在飞书移动端确认登录\n")
        r = self.watch("--max", "6", "--resend", "1")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.last_json(r.stdout), {"status": "timeout", "sent": 1})
        self.assertEqual(self.lark_calls(), [], "a refresh here would invalidate the login the person is confirming")

    def test_context_file_is_used_for_resends(self):
        self.session()
        ctx = os.path.join(self.tmp.name, "ctx.txt")
        with open(ctx, "w") as f:
            f.write("用途：来自文件的说明")
        r = self.run_sh(WATCH, "--to", "ou_test", "--context-file", ctx, "--workdir", self.work, "--max", "6", "--resend", "1", "--max-resends", "1")
        texts = [c for c in self.lark_calls() if "--text" in c]
        self.assertEqual(len(texts), 1, r.stdout)
        self.assertIn("用途：来自文件的说明", texts[0])

    def test_a_session_that_has_ended_is_reported_at_once_not_after_the_timeout(self):
        self.session()
        threading.Timer(1.5, lambda: os.remove(os.path.join(self.work, "url.txt"))).start()  # what the session's exit cleanup does
        t0 = time.time()
        r = self.watch("--max", "60", "--resend", "999")
        self.assertLess(time.time() - t0, 10, "must not wait out --max for a session that is gone")
        self.assertEqual(r.returncode, 3, r.stdout)
        self.assertEqual(self.last_json(r.stdout), {"status": "session_ended", "sent": 1})

    def test_leading_zero_numbers_are_decimal_and_absurd_ones_are_refused(self):
        self.session()
        r = self.run_sh(WATCH, *self.ARGS, "--workdir", self.work, "--max", "08", "--resend", "0008", "--max-resends", "00")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)   # ran to its 8s timeout, no arithmetic error
        self.assertEqual(self.last_json(r.stdout), {"status": "timeout", "sent": 1})
        self.assertNotIn("value too great for base", r.stderr)
        self.assertIn("timeout after 8s", r.stdout, "08 means eight")
        for flag in ("--max", "--resend", "--max-resends"):
            r = self.run_sh(WATCH, *self.ARGS, "--workdir", self.work, flag, "99999999999999999999")
            self.assertEqual(r.returncode, 2, (flag, r.stdout))
            self.assertIn("too large", r.stdout)

    def test_static_scan_success_text_does_not_count_as_scanned(self):
        s = self.session()  # page.txt has the always-present "扫码成功" and nothing else
        r = self.watch("--max", "8", "--resend", "1", "--max-resends", "1")
        self.assertIn("resend #2", r.stdout)
        texts = [c for c in self.lark_calls() if "--text" in c]
        self.assertEqual(len(texts), 1)
        self.assertIn("第2张（上一张已过期）", texts[0])
        self.assertIn(CONTEXT, texts[0], "re-sent codes carry the context text too")
        self.assertEqual(s.shots, 1)

    def test_interrupt_still_prints_a_structured_status(self):
        import signal
        self.session()
        proc = subprocess.Popen(["bash", WATCH, *self.ARGS, "--workdir", self.work, "--max", "60", "--resend", "999"],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=self.env())
        proc.stdout.readline()  # the first "page:" line: printed only after the trap is installed
        proc.send_signal(signal.SIGTERM)
        out, _ = proc.communicate(timeout=10)
        self.assertEqual(proc.returncode, 130)
        self.assertEqual(self.last_json(out)["status"], "interrupted")

    def test_the_confirmation_phrase_is_found_anywhere_in_the_page_not_just_the_top(self):
        s = self.session()
        s._w("page.txt", ("这是一行很长的页面说明文字用来把后面的内容挤出前面的字节窗口\n" * 6) + "扫码登录\n请在飞书移动端确认登录\n")
        r = self.watch("--max", "6", "--resend", "1")
        self.assertEqual(self.last_json(r.stdout), {"status": "timeout", "sent": 1})
        self.assertEqual(self.lark_calls(), [], "refreshing now would void the login being confirmed on the phone")
        self.assertNotIn("phone confirmation is pending", r.stdout, "the watcher itself must see the phrase; send_qr.sh's own guard is only a backstop")

    def test_failed_resends_do_not_count_as_sent_and_are_still_capped(self):
        self.session()
        r = self.watch("--max", "12", "--resend", "1", "--max-resends", "2", FAKE_LARK_FAIL="text")
        out = self.last_json(r.stdout)
        self.assertEqual(out["status"], "timeout")
        self.assertEqual(out["sent"], 1, "a resend that failed to send is not a sent QR")
        self.assertEqual(len([c for c in self.lark_calls() if "--text" in c]), 2, "attempts are capped too")
        self.assertIn("failed", r.stdout)

    def test_login_is_reported_only_for_a_real_http_page(self):
        s = self.session()
        s._w("url.txt", "about:blank")
        threading.Timer(2.5, lambda: s.log_in("site.example")).start()
        r = self.watch("--max", "20", "--resend", "999")
        self.assertEqual(self.last_json(r.stdout)["host"], "site.example", "about:blank is not a login")

    def test_numeric_options_are_validated(self):
        for opt in ("--max", "--resend", "--max-resends"):
            with self.subTest(opt):
                r = self.run_sh(WATCH, "--to", "x", "--context", "c", "--workdir", self.work, opt, "abc")
                self.assertEqual(r.returncode, 2)

    def test_the_sending_identity_is_forwarded_on_resends(self):
        self.session()
        self.watch("--max", "5", "--resend", "1", "--max-resends", "1", "--as", "user")
        sends = [c for c in self.lark_calls() if "--text" in c or "--image" in c]
        self.assertTrue(sends and all("--as user" in c for c in sends), sends)

    def test_resends_are_capped(self):
        self.session()
        r = self.watch("--max", "14", "--resend", "1", "--max-resends", "2")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.last_json(r.stdout)["sent"], 3)  # the first code + 2 resends
        self.assertEqual(len([c for c in self.lark_calls() if "--image" in c]), 2)


if __name__ == "__main__":
    unittest.main()
