"""remote_login.js 本体：用假的 playwright-core（tests/fake_playwright）让它在没有浏览器的环境里跑起来，钉住会话协议的行为。
没有 node 时整个文件跳过。"""
import base64
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from test_qr_enlarge import encode, pattern  # noqa: E402

SCRIPT = os.path.abspath(os.path.join(HERE, "..", "scripts", "remote_login.js"))
FAKE_MODULES = os.path.join(HERE, "fake_playwright", "node_modules")
PNG_B64 = base64.b64encode(encode(8, 8, 6, pattern(8, 8, 4))).decode()
FEISHU = "https://accounts.feishu.cn/accounts/page/login?app_id=1&redirect_uri=https%3A%2F%2Fexample.test%2Fcb"


@unittest.skipUnless(shutil.which("node"), "node not installed")
class SessionBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = os.path.join(self.tmp.name, "work")
        os.mkdir(self.work, 0o700)
        self.state_file = os.path.join(self.tmp.name, "page-state.json")
        self.log_file = os.path.join(self.tmp.name, "fake-pw.log")
        self.profile = os.path.join(self.tmp.name, "profile")
        self.procs = []
        self.addCleanup(self.stop_all)
        self.set_page(url=FEISHU, text="扫码登录\n扫码成功\n", canvas=True, refresh=False)
        open(self.log_file, "w").close()

    def set_page(self, **kw):
        cur = {}
        if os.path.exists(self.state_file):
            with open(self.state_file) as f:
                cur = json.load(f)
        cur.update(kw)
        with open(self.state_file + ".new", "w") as f:  # replaced atomically: the session's fake reads this file while we rewrite it
            json.dump(cur, f)
        os.replace(self.state_file + ".new", self.state_file)

    def start(self, url=FEISHU, **env):
        e = {**os.environ, "NODE_PATH": FAKE_MODULES, "CHROMIUM_PATH": "/bin/true", "REMOTE_LOGIN_PROFILE": self.profile,
             "FAKE_PW_STATE": self.state_file, "FAKE_PW_LOG": self.log_file, "FAKE_PW_PNG_B64": PNG_B64, **env}
        p = subprocess.Popen(["node", SCRIPT, url], cwd=self.work, env=e, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.procs.append(p)
        return p

    def stop_all(self):
        for p in self.procs:
            if p.poll() is None:
                p.send_signal(signal.SIGKILL)
            p.communicate()

    def path(self, name):
        return os.path.join(self.work, name)

    def read(self, name):
        try:
            with open(self.path(name)) as f:
                return f.read()
        except OSError:
            return None

    def wait_for(self, predicate, what, timeout=6):
        end = time.time() + timeout
        while time.time() < end:
            if predicate():
                return
            time.sleep(0.05)
        self.fail(f"timed out waiting for {what}")

    def log_ops(self):
        with open(self.log_file) as f:
            return [json.loads(line) for line in f if line.strip()]

    def command(self, cmd):
        with open(self.path("cmd.txt.tmp"), "w") as f:
            f.write(cmd if isinstance(cmd, str) else json.dumps(cmd))
        self.rm("result.txt")
        os.replace(self.path("cmd.txt.tmp"), self.path("cmd.txt"))
        self.wait_for(lambda: self.read("result.txt") is not None, "result.txt")
        return self.read("result.txt")

    def rm(self, name):
        try:
            os.remove(self.path(name))
        except OSError:
            pass

    def logged_in_session(self, url="https://site.example/home"):
        self.set_page(url=url, text="home")
        p = self.start(url)
        self.wait_for(lambda: self.read("ready.txt") is not None, "ready.txt")
        return p


class Startup(SessionBase):
    def test_a_non_private_working_directory_is_refused(self):
        os.chmod(self.work, 0o755)
        p = self.start()
        _, err = p.communicate(timeout=10)
        self.assertEqual(p.returncode, 2)
        self.assertIn("private", err)

    def test_leftovers_of_a_killed_session_are_cleared_before_anything_else(self):
        for name, body in {"state.txt": "LOGGED_IN https://old.example/", "ready.txt": "ready", "url.txt": "https://old.example/",
                           "cmd.txt": json.dumps({"goto": "https://stale.example/"}), "shoot.txt": "", "error.txt": "old",
                           "run.log": "old", "qr-big.png": "x", "cmd.txt.tmp": "x", "quit.txt": ""}.items():
            with open(self.path(name), "w") as f:
                f.write(body)
        self.start()
        self.wait_for(lambda: self.read("run.pid") is not None, "run.pid")
        time.sleep(0.5)
        for name in ("ready.txt", "state.txt", "cmd.txt", "error.txt", "qr-big.png", "cmd.txt.tmp", "quit.txt"):
            self.assertIsNone(self.read(name), f"stale {name} survived startup")
        self.assertNotIn("https://stale.example/", json.dumps(self.log_ops()), "a stale command must never run")

    def test_the_profile_directory_is_created_and_tightened_to_0700(self):
        os.mkdir(self.profile, 0o755)
        self.start()
        self.wait_for(lambda: any(o["op"] == "launch" for o in self.log_ops()), "launch")
        self.assertEqual(oct(os.stat(self.profile).st_mode & 0o777), "0o700")

    def test_sandbox_is_off_by_default_and_can_be_switched_on(self):
        self.start()
        self.wait_for(lambda: any(o["op"] == "launch" for o in self.log_ops()), "launch")
        self.assertIn("--no-sandbox", [o for o in self.log_ops() if o["op"] == "launch"][0]["args"])
        self.stop_all()
        open(self.log_file, "w").close()
        self.start(REMOTE_LOGIN_SANDBOX="1")
        self.wait_for(lambda: any(o["op"] == "launch" for o in self.log_ops()), "launch (sandbox on)")
        self.assertNotIn("--no-sandbox", [o for o in self.log_ops() if o["op"] == "launch"][0]["args"])


class LoginWaitPhase(SessionBase):
    def test_quit_txt_stops_a_session_that_is_still_waiting_for_the_scan(self):
        p = self.start()
        self.wait_for(lambda: self.read("run.pid") is not None, "run.pid")
        with open(self.path("quit.txt"), "w") as f:
            f.write("quit")
        p.communicate(timeout=10)
        self.assertEqual(p.returncode, 0)

    def test_giving_up_waiting_does_not_pretend_the_login_happened(self):
        p = self.start(REMOTE_LOGIN_LOGIN_TIMEOUT="2")
        seen_ready, end = False, time.time() + 8
        while p.poll() is None and time.time() < end:
            seen_ready = seen_ready or self.read("ready.txt") is not None
            time.sleep(0.05)
        self.assertIsNotNone(p.poll(), "the session must give up after REMOTE_LOGIN_LOGIN_TIMEOUT, not wait forever")
        p.communicate()
        self.assertFalse(seen_ready, "ready.txt means 'logged in'")
        self.assertEqual(p.returncode, 3)

    def test_only_a_real_http_page_off_the_feishu_host_counts_as_logged_in(self):
        self.set_page(url="about:blank")
        self.start("about:blank")
        self.wait_for(lambda: self.read("url.txt") is not None, "url.txt")
        time.sleep(0.6)
        self.assertIsNone(self.read("ready.txt"), "about:blank is not a login")
        self.set_page(url="https://evil.example/?next=accounts.feishu.cn")  # the Feishu host name only appears in the query string
        self.wait_for(lambda: self.read("ready.txt") is not None, "ready.txt for a non-Feishu host")

    def test_a_page_that_redirects_to_the_login_page_after_loading_is_not_a_login(self):
        """goto() resolves on domcontentloaded: an app page that bounces to accounts.feishu.cn client-side looks fine for a moment."""
        app = "https://open.feishu.cn/app"
        self.set_page(url=app, text="loading", flip={"waits": 1, "url": FEISHU, "text": "扫码登录\n扫码成功\n"})
        self.start(app)
        self.wait_for(lambda: (self.read("url.txt") or "").startswith("https://accounts.feishu.cn"), "url.txt on the login page")
        time.sleep(0.6)
        self.assertIsNone(self.read("ready.txt"), "a page that ends up on the login host must not be reported as logged in")
        self.assertFalse((self.read("state.txt") or "").startswith("LOGGED_IN"))
        open(self.path("shoot.txt"), "w").close()  # and it is still in the wait-for-scan phase: it answers a QR request
        self.wait_for(lambda: (self.read("state.txt") or "").startswith("QR"), "a QR from the still-waiting session")

    def test_shoot_txt_refreshes_only_an_expired_code_and_writes_qr_png(self):
        self.set_page(refresh=True)
        self.start()
        self.wait_for(lambda: self.read("run.pid") is not None, "run.pid")
        open(self.path("shoot.txt"), "w").close()
        self.wait_for(lambda: (self.read("state.txt") or "").startswith("QR 1"), "state QR 1")
        self.assertIn("refresh", [o["op"] for o in self.log_ops()])
        self.assertTrue(os.path.getsize(self.path("qr.png")) > 0)

    def test_shoot_txt_does_not_refresh_while_the_phone_confirmation_is_pending(self):
        self.set_page(text="扫码登录\n请在飞书移动端确认登录\n", refresh=True)
        self.start()
        self.wait_for(lambda: self.read("run.pid") is not None, "run.pid")
        open(self.path("shoot.txt"), "w").close()
        self.wait_for(lambda: self.read("shoot.txt") is None, "shoot.txt consumed")
        time.sleep(0.4)
        ops = [o["op"] for o in self.log_ops()]
        self.assertNotIn("refresh", ops)
        self.assertNotIn("qr_shot", ops)


class CommandPhase(SessionBase):
    def test_deleting_the_working_directory_ends_the_session_and_closes_the_browser(self):
        """`touch quit.txt; rm -rf workdir` races the poll: the session must still exit, with its browser closed (no orphan Chromium)."""
        p = self.logged_in_session()
        shutil.rmtree(self.work)
        try:
            p.wait(timeout=8)
        except subprocess.TimeoutExpired:
            self.fail("the session kept running after its working directory was deleted")
        _, err = p.communicate()
        self.assertIn({"op": "close"}, self.log_ops(), "the browser must be closed even though the working directory is gone")
        self.assertNotIn("Unhandled", err, err[-300:])
        self.assertNotIn("at Object.writeFileSync", err, "an error handler that throws again leaves a stack trace and a dirty exit")

    def test_a_bad_command_fails_only_that_step(self):
        p = self.logged_in_session()
        self.assertTrue(self.command("this is not json").startswith("ERR"))
        self.assertIsNone(p.poll(), "the session must survive a bad command")
        self.assertIsNotNone(self.read("ready.txt"))
        self.assertEqual(self.command({"goto": "https://site.example/next"}), "OK https://site.example/next")

    def test_a_stale_result_is_not_readable_while_the_next_command_runs(self):
        self.logged_in_session()
        with open(self.path("result.txt"), "w") as f:
            f.write("OK https://stale.example/")
        with open(self.path("cmd.txt"), "w") as f:
            json.dump({"goto": "https://site.example/SLOW"}, f)
        self.wait_for(lambda: self.read("cmd.txt") is None, "the command to be picked up")
        self.assertNotEqual(self.read("result.txt"), "OK https://stale.example/", "the caller would take the previous result for this one")
        self.wait_for(lambda: (self.read("result.txt") or "").startswith("OK"), "the new result")
        self.assertEqual(self.read("result.txt"), "OK https://site.example/SLOW")

    def test_a_failed_step_reports_err_and_the_next_command_still_works(self):
        self.logged_in_session()
        self.assertTrue(self.command({"click": ["#MISSING"]}).startswith("ERR"))
        self.assertTrue(self.command({"wait": 1}).startswith("OK"))

    def test_quit_txt_ends_a_logged_in_session(self):
        p = self.logged_in_session()
        with open(self.path("quit.txt"), "w") as f:
            f.write("quit")
        p.communicate(timeout=10)
        self.assertEqual(p.returncode, 0)

    def test_shoot_command_works_after_login_and_refuses_while_confirming(self):
        self.logged_in_session()
        self.set_page(url=FEISHU, text="扫码登录\n", canvas=True, refresh=True)
        self.assertTrue(self.command({"shoot": True}).startswith("OK"))
        self.assertTrue((self.read("state.txt") or "").startswith("QR "))
        self.set_page(text="请在飞书移动端确认登录\n", refresh=True)
        before = [o["op"] for o in self.log_ops()].count("refresh")
        self.assertTrue(self.command({"shoot": True}).startswith("ERR"))
        self.assertEqual([o["op"] for o in self.log_ops()].count("refresh"), before, "no refresh while confirming")

    def test_shoot_without_a_qr_canvas_is_an_error_not_a_stale_success(self):
        self.logged_in_session()
        self.set_page(canvas=False)
        self.assertTrue(self.command({"shoot": True}).startswith("ERR"))


class OpaqueAndOddUrls(SessionBase):
    def test_a_data_or_blob_url_payload_never_reaches_the_files(self):
        for url in ("data:text/html,TOPSECRET123", "blob:https://evil.example/TOPSECRET123"):
            with self.subTest(url=url):
                self.set_page(url=url)
                p = self.start(url)
                self.wait_for(lambda: self.read("url.txt") is not None, "url.txt")
                time.sleep(0.3)
                self.assertNotIn("TOPSECRET123", self.read("url.txt"))
                self.assertIsNone(self.read("ready.txt"), "an opaque URL is not a login")
                p.send_signal(signal.SIGKILL); p.communicate()
                self.rm("run.pid")

    def test_error_messages_lose_userinfo_and_the_query_whatever_the_case(self):
        self.logged_in_session()
        result = self.command({"goto": "HTTPS://user:hunter2@site.example/FAILME?code=TOPSECRET123"})
        self.assertTrue(result.startswith("ERR"))
        for leaked in ("hunter2", "TOPSECRET123", "user:"):
            self.assertNotIn(leaked, result)

    def test_a_page_that_cannot_be_read_is_never_refreshed(self):
        self.logged_in_session()
        self.set_page(url=FEISHU, text="扫码登录\n", canvas=True, refresh=True, evalFails=True)  # mid-navigation: text unreadable
        before = [o["op"] for o in self.log_ops()].count("refresh")
        self.assertTrue(self.command({"shoot": True}).startswith("ERR"))
        self.assertEqual([o["op"] for o in self.log_ops()].count("refresh"), before, "fail closed: cannot rule out a pending confirmation")

    def test_the_home_directory_itself_is_not_accepted_as_the_profile(self):
        p = self.start(REMOTE_LOGIN_PROFILE=self.tmp.name, HOME=self.tmp.name)
        _, err = p.communicate(timeout=10)
        self.assertEqual(p.returncode, 2)
        self.assertIn("profile", err)

    def test_crash_leftovers_of_the_command_channel_are_cleaned_on_exit(self):
        p = self.logged_in_session()
        for name in ("cmd.txt.tmp", "shoot.txt"):
            with open(self.path(name), "w") as f:
                f.write("x")
        with open(self.path("quit.txt"), "w") as f:
            f.write("q")
        p.communicate(timeout=10)
        for name in ("cmd.txt.tmp", "shoot.txt", "quit.txt", "url.txt", "state.txt"):
            self.assertIsNone(self.read(name), f"{name} survived a normal exit")


class NothingSensitiveIsWrittenBesidesPageContent(SessionBase):
    SECRET = "TOPSECRET123"

    def test_urls_are_recorded_without_query_or_fragment(self):
        self.logged_in_session()
        result = self.command({"goto": f"https://site.example/callback?code={self.SECRET}#access_token={self.SECRET}"})
        self.assertEqual(result, "OK https://site.example/callback")
        time.sleep(0.3)
        for name in ("url.txt", "state.txt", "result.txt", "ready.txt", "run.log"):
            self.assertNotIn(self.SECRET, self.read(name) or "", name)

    def test_the_start_url_secret_never_reaches_the_files(self):
        self.set_page(url=f"https://site.example/cb?code={self.SECRET}", text="home")
        self.start(f"https://site.example/cb?code={self.SECRET}")
        self.wait_for(lambda: self.read("ready.txt") is not None, "ready.txt")
        for name in ("url.txt", "state.txt", "ready.txt", "run.log"):
            self.assertNotIn(self.SECRET, self.read(name) or "", name)

    def test_error_messages_do_not_echo_query_strings(self):
        self.logged_in_session()
        result = self.command({"goto": f"https://site.example/FAILME?code={self.SECRET}"})
        self.assertTrue(result.startswith("ERR"))
        self.assertNotIn(self.SECRET, result)


FS_HOOK = """
const fs = require('fs'); const out = process.env.HOOK_LOG; let busy = false;
const note = (line) => { if (busy) return; busy = true; try { fs.appendFileSync(out, line + '\\n'); } finally { busy = false; } };
const w = fs.writeFileSync; fs.writeFileSync = function (p, ...a) { note('write ' + p); return w.call(this, p, ...a); };
const r = fs.renameSync; fs.renameSync = function (a, b) { note('rename ' + a + ' ' + b); return r.call(this, a, b); };
"""


class SessionRobustness(SessionBase):
    """Findings of the second independent review: races, a second start, odd files in the working directory, exit paths."""

    def test_a_failing_snapshot_at_the_login_transition_does_not_kill_the_session(self):
        self.set_page(url=FEISHU, text="扫码登录\n")
        p = self.start()
        self.wait_for(lambda: self.read("url.txt") is not None, "url.txt")
        self.set_page(url="https://site.example/cb", evalFails=True)  # the redirect chain destroyed the page context
        self.wait_for(lambda: self.read("ready.txt") is not None, "ready.txt despite the failing snapshot")
        self.assertIsNone(p.poll(), "the login happened: the session must keep running")
        self.assertIsNone(self.read("error.txt"))

    def test_a_second_start_in_the_same_directory_leaves_the_live_session_alone(self):
        p = self.logged_in_session()
        before = {n: self.read(n) for n in ("ready.txt", "state.txt", "run.pid")}
        p2 = self.start()
        _, err = p2.communicate(timeout=10)
        self.assertEqual(p2.returncode, 2)
        self.assertIn("already running", err)
        self.assertIsNone(p.poll(), "the first session must still be running")
        for name, body in before.items():
            self.assertEqual(self.read(name), body, f"{name} was touched by the refused second start")

    GATE = "const fs = require('fs'); while (!fs.existsSync(process.env.GATE_FILE)) Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 2);"

    def race(self, n=8):
        """n sessions parked at a gate inside the directory, released at the same instant: every one of them claims run.pid at once."""
        gate, go = os.path.join(self.tmp.name, "gate.js"), os.path.join(self.tmp.name, "go")
        with open(gate, "w") as f:
            f.write(self.GATE)
        self.set_page(url="https://site.example/home", text="home")
        procs = [self.start("https://site.example/home", NODE_OPTIONS=f"--require={gate}", GATE_FILE=go) for _ in range(n)]
        time.sleep(1.5)  # every node has started and is spinning at the gate
        open(go, "w").close()
        self.wait_for(lambda: sum(p.poll() is not None for p in procs) >= n - 1, f"{n - 1} of {n} to be refused", timeout=15)
        alive = [p for p in procs if p.poll() is None]
        self.assertEqual(len(alive), 1, "exactly one session may own the directory")
        for p in procs:
            if p is not alive[0]:
                self.assertEqual(p.returncode, 2, "the losers are refused, not crashed")
        self.wait_for(lambda: self.read("ready.txt") is not None, "the winner to log in")
        self.assertEqual(self.read("run.pid"), str(alive[0].pid), "run.pid names the winner, not a loser")
        self.assertIsNone(alive[0].poll(), "no loser may have damaged the winner")

    def test_simultaneous_starts_leave_exactly_one_session(self):
        self.race()

    def test_sixteen_simultaneous_starts_leave_exactly_one_session(self):
        self.race(16)

    def test_simultaneous_starts_over_a_stale_run_pid_still_leave_exactly_one_session(self):
        with open(self.path("run.pid"), "w") as f:
            f.write("2999999")  # a crashed session's leftover
        self.race()

    def start_logged_in_and_wait(self):
        self.set_page(url="https://site.example/home", text="home")
        p = self.start("https://site.example/home")
        self.wait_for(lambda: self.read("ready.txt") is not None, "ready.txt")
        return p

    def test_a_run_pid_that_is_not_a_pid_does_not_block_a_new_session(self):
        with open(self.path("run.pid"), "w") as f:
            f.write("garbage")
        p = self.start_logged_in_and_wait()
        self.assertEqual(self.read("run.pid"), str(p.pid))

    def test_a_pid_reused_by_an_unrelated_live_process_does_not_block_a_new_session(self):
        other = subprocess.Popen(["sleep", "30"])
        self.addCleanup(other.kill)
        with open(self.path("run.pid"), "w") as f:
            f.write(str(other.pid))  # alive, but not a remote_login session
        p = self.start_logged_in_and_wait()
        self.assertEqual(self.read("run.pid"), str(p.pid))

    def test_a_takeover_lock_left_by_a_crashed_starter_does_not_block_forever(self):
        with open(self.path("run.pid"), "w") as f:
            f.write("2999999")
        with open(self.path("run.pid.takeover"), "w") as f:
            f.write("")
        old = time.time() - 60
        os.utime(self.path("run.pid.takeover"), (old, old))
        p = self.start_logged_in_and_wait()
        self.assertEqual(self.read("run.pid"), str(p.pid))
        self.assertFalse(os.path.exists(self.path("run.pid.takeover")))

    def test_ending_a_session_removes_its_run_pid_and_only_its_own(self):
        p = self.logged_in_session()
        self.assertEqual(self.read("run.pid"), str(p.pid))
        with open(self.path("quit.txt"), "w") as f:
            f.write("quit")
        p.communicate(timeout=10)
        self.assertFalse(os.path.exists(self.path("run.pid")))

    def test_a_stale_run_pid_of_a_dead_process_does_not_block_a_new_session(self):
        with open(self.path("run.pid"), "w") as f:
            f.write("2999999")
        self.set_page(url="https://site.example/home", text="home")
        self.start("https://site.example/home")
        self.wait_for(lambda: self.read("ready.txt") is not None, "ready.txt")

    def test_a_directory_named_cmd_txt_is_removed_and_the_session_carries_on(self):
        p = self.logged_in_session()
        os.mkdir(self.path("cmd.txt"))
        self.wait_for(lambda: not os.path.lexists(self.path("cmd.txt")), "the directory to be removed")
        self.assertIsNone(p.poll())
        os.mkdir(self.path("cmd.txt"), 0o000)  # cannot even be opened: the open itself fails, not the type check
        self.wait_for(lambda: not os.path.lexists(self.path("cmd.txt")), "the unreadable directory to be removed")
        self.assertIsNone(p.poll())
        self.assertTrue(self.command({"wait": 1}).startswith("OK "))

    def test_a_symbolic_link_or_a_fifo_as_cmd_txt_is_removed_unread(self):
        p = self.logged_in_session()
        victim = os.path.join(self.tmp.name, "victim.json")
        with open(victim, "w") as f:
            json.dump({"goto": "https://evil.example/"}, f)
        os.symlink(victim, self.path("cmd.txt"))
        self.wait_for(lambda: not os.path.lexists(self.path("cmd.txt")), "the link to be removed")
        self.assertTrue(os.path.exists(victim), "the link is removed, never what it points at")
        os.mkfifo(self.path("cmd.txt"))
        self.wait_for(lambda: not os.path.lexists(self.path("cmd.txt")), "the fifo to be removed")
        self.assertIsNone(p.poll())
        self.assertNotIn("evil.example", json.dumps(self.log_ops()), "a linked command must never run")

    def test_polled_files_are_replaced_atomically_never_written_in_place(self):
        hook, out = os.path.join(self.tmp.name, "hook.js"), os.path.join(self.tmp.name, "fs.log")
        with open(hook, "w") as f:
            f.write(FS_HOOK)
        self.set_page(url="https://site.example/home", text="home")
        self.start("https://site.example/home", NODE_OPTIONS=f"--require={hook}", HOOK_LOG=out)
        self.wait_for(lambda: self.read("ready.txt") is not None, "ready.txt")
        self.assertTrue(self.command({"wait": 1}).startswith("OK "))
        with open(out) as f:
            lines = f.read().splitlines()
        for name in ("state.txt", "ready.txt", "result.txt", "url.txt", "page.txt"):
            self.assertNotIn(f"write {name}", lines, f"{name} is polled by other processes: it must not be written in place")
            self.assertIn(f"write {name}.tmp", lines, name)
            self.assertIn(f"rename {name}.tmp {name}", lines, name)

    def test_ending_the_session_removes_every_page_snapshot_and_marker(self):
        p = self.logged_in_session()
        self.assertTrue(self.command({"wait": 1}).startswith("OK "))
        for name in ("page.txt", "page.html", "page.png", "url.txt", "ready.txt", "state.txt", "result.txt"):
            self.assertTrue(os.path.exists(self.path(name)), f"{name} should exist while the session runs")
        with open(self.path("quit.txt"), "w") as f:
            f.write("quit")
        p.communicate(timeout=10)
        for name in ("page.txt", "page.html", "page.png", "url.txt", "ready.txt", "state.txt", "result.txt", "quit.txt"):
            self.assertFalse(os.path.lexists(self.path(name)), f"{name} outlived the session: it holds a logged-in page or a marker")

    def test_files_are_private_and_a_logged_in_session_records_where_it_landed(self):
        self.logged_in_session()
        self.command({"wait": 1})
        for name in ("url.txt", "page.txt", "page.html", "page.png", "state.txt", "ready.txt"):
            self.assertEqual(os.stat(self.path(name)).st_mode & 0o077, 0, f"{name} must be 0600")
        self.assertEqual(self.read("ready.txt"), "ready https://site.example/home")
        self.assertEqual(self.read("state.txt"), "LOGGED_IN https://site.example/home")

    def test_commands_fill_click_and_wait_run_and_leave_a_snapshot(self):
        self.logged_in_session()
        self.assertEqual(self.command({"fill": [["#user", "alice"]], "click": ["#go"], "wait": 5}), "OK https://site.example/home")
        ops = self.log_ops()
        self.assertIn({"op": "fill", "sel": "#user"}, ops)
        self.assertIn({"op": "click", "sel": "#go"}, ops)
        self.assertIn({"op": "wait", "ms": 5}, ops, "the wait step of the command")
        self.assertNotIn("alice", json.dumps(ops), "what was typed into a form is never logged")

    def test_a_json_array_is_not_a_command(self):
        self.logged_in_session()
        self.assertTrue(self.command("[1, 2]").startswith("ERR command is not valid JSON"))
        self.assertTrue(self.command({"wait": 1}).startswith("OK "), "the session must survive it")

    def test_the_qr_counter_counts_up_in_the_wait_phase(self):
        self.start()
        self.wait_for(lambda: self.read("url.txt") is not None, "url.txt")
        for n in (1, 2):
            open(self.path("shoot.txt"), "w").close()
            self.wait_for(lambda: (self.read("state.txt") or "").startswith(f"QR {n} "), f"QR {n}")

    def test_the_login_phase_publishes_the_live_page_text(self):
        self.set_page(text="扫码登录\n请在飞书移动端确认登录\n")
        self.start()
        self.wait_for(lambda: "确认登录" in (self.read("page.txt") or ""), "page.txt with the confirmation phrase")

    def test_terminating_signals_close_the_browser_and_exit_with_their_codes(self):
        for sig, code in ((signal.SIGTERM, 143), (signal.SIGINT, 130), (signal.SIGHUP, 129)):
            with self.subTest(sig=sig.name):
                self.stop_all()
                open(self.log_file, "w").close()
                p = self.logged_in_session()
                p.send_signal(sig)
                p.communicate(timeout=10)
                self.assertEqual(p.returncode, code)
                self.assertIn({"op": "close"}, self.log_ops())
                self.assertIsNone(self.read("ready.txt"), "cleanup runs on a signal too")

    def test_an_error_nobody_handles_exits_1_and_says_why(self):
        p = self.start("https://FAILME.example/")
        p.communicate(timeout=10)
        self.assertEqual(p.returncode, 1)
        self.assertIn("FAILME", self.read("error.txt") or "")

    def test_the_newest_installed_chromium_is_used_by_number_not_by_text(self):
        home = os.path.join(self.tmp.name, "home")
        for n in (9, 10):
            d = os.path.join(home, ".cache", "ms-playwright", f"chromium-{n}", "chrome-linux64")
            os.makedirs(d)
            open(os.path.join(d, "chrome"), "w").close()
        self.start(HOME=home, CHROMIUM_PATH="")
        self.wait_for(lambda: any(o["op"] == "launch" for o in self.log_ops()), "launch")
        launch = [o for o in self.log_ops() if o["op"] == "launch"][0]
        self.assertTrue(launch["executablePath"].endswith("chromium-10/chrome-linux64/chrome"), launch["executablePath"])
        self.assertEqual(launch["viewport"], {"width": 1280, "height": 900})

    def test_giving_up_waits_for_the_configured_seconds_not_less(self):
        t0 = time.time()
        p = self.start(REMOTE_LOGIN_LOGIN_TIMEOUT="2")
        p.communicate(timeout=10)
        self.assertGreaterEqual(time.time() - t0, 1.8, "REMOTE_LOGIN_LOGIN_TIMEOUT is in seconds")
        self.assertEqual(p.returncode, 3)


if __name__ == "__main__":
    unittest.main()
