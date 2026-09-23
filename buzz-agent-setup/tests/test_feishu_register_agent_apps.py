"""批量注册 agent 飞书应用的脚本（references/scripts/feishu_register_agent_apps.sh 与 feishu_browser.js）。

脚本把 `lark-cli config init --new` 和一个已登录的无头浏览器接起来。测试用假的 lark-cli（bash）和一个
盯着 cmd.txt 的假浏览器线程，覆盖每一条失败路径：任何一步失败都必须非零退出、不打印像成功的那一行、
把后台的 lark-cli 收掉；成功只在 app_id 合法且回读的 app_name 与 agent 名逐字相等时才报。
"""

import atexit
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "references" / "scripts"
SCRIPT = SCRIPTS / "feishu_register_agent_apps.sh"
BROWSER_JS = SCRIPTS / "feishu_browser.js"

_SYSTEM_BINS = {}


def system_bin(*without):
    """脚本要用的系统命令（/usr/bin、/bin 里的全部可执行文件）的符号链接目录，但不含任何 python，也不含 ``without`` 里的命令。

    测试给脚本的 PATH 用它代替 ``/usr/bin:/bin``：CI 的镜像里 python3 在 /usr/local/bin、不在 /usr/bin，本机却在 /usr/bin，
    直接写死 ``/usr/bin:/bin`` 会让「python3 在哪」这件事在本机和 CI 上不一样。
    """
    key = frozenset(without)
    if key not in _SYSTEM_BINS:
        root = Path(tempfile.mkdtemp(prefix="fra-sysbin-"))
        atexit.register(shutil.rmtree, root, ignore_errors=True)
        for src_dir in ("/usr/bin", "/bin"):
            for entry in sorted(os.listdir(src_dir)):
                src = os.path.join(src_dir, entry)
                if entry.startswith("python") or entry in key or (root / entry).exists():
                    continue
                if os.access(src, os.X_OK) and not os.path.isdir(src):
                    (root / entry).symlink_to(src)
        _SYSTEM_BINS[key] = root
    return _SYSTEM_BINS[key]

FAKE_LARK = r'''#!/bin/bash
# 假的 lark-cli：只实现脚本用到的三条命令，行为由环境变量决定。
echo "$@" >> "$FAKE_DIR/lark-calls.log"
# FAKE_FORK：像真的 lark-cli 一样带着子进程干活——一个直接子进程、一个再起孙进程的子进程；其中孙进程一支忽略 TERM。
# 它们的 pid 都记进 descendants.pids，测试据此确认失败之后一个都不剩。
spawn_family() {
  sleep 60 & echo $! >> "$FAKE_DIR/descendants.pids"
  bash -c 'echo $$ >> "$FAKE_DIR/descendants.pids"; (trap "" TERM; sleep 60 & echo $! >> "$FAKE_DIR/descendants.pids"; wait) & sleep 60 & echo $! >> "$FAKE_DIR/descendants.pids"; wait' &
}
case "$1 $2" in
"config init")
  echo $$ > "$FAKE_DIR/cli.pid"
  [ -n "$FAKE_FORK" ] && spawn_family
  [ -n "$FAKE_CLI_DIES" ] && exit 1
  [ -n "$FAKE_NO_URL" ] && { sleep 30 & echo $! > "$FAKE_DIR/child.pid"; wait; exit 0; }
  echo "打开以下链接配置应用:"
  echo "  https://open.feishu.cn/page/cli?user_code=ABCD-1234&lpv=1.0.85&ocv=1.0.85&from=cli"
  echo "等待配置应用..."
  for i in $(seq 1 400); do [ -e "$FAKE_DIR/approved" ] && break; sleep 0.05; done
  [ -e "$FAKE_DIR/approved" ] || exit 1
  [ -n "$FAKE_HANG" ] && { sleep 30 & echo $! > "$FAKE_DIR/child.pid"; wait; }
  exit "${FAKE_INIT_EXIT:-0}"
  ;;
"auth status")
  if [ -n "$FAKE_STATUS_RAW" ]; then echo "$FAKE_STATUS_RAW"; else echo "{\"appId\": \"${FAKE_APP_ID-cli_fake000001}\"}"; fi
  ;;
"api GET")
  if [ -n "$FAKE_API_RAW" ]; then echo "$FAKE_API_RAW"; else
    echo "{\"ok\":true,\"data\":{\"app\":{\"app_name\":\"${FAKE_APP_NAME-nh-x}\",\"app_secret\":\"S3CRET-MUST-NOT-PRINT\"}}}"; fi
  exit "${FAKE_API_EXIT:-0}"
  ;;
*) echo "unexpected: $*" >&2; exit 9;;
esac
'''


class FakeBrowser(threading.Thread):
    """盯着 cmd.txt 的假浏览器：按文件协议回 result.txt / page.txt，提交时放行假 lark-cli。"""

    def __init__(self, browser_dir: Path, fake_dir: Path, *, submit_page="创建成功", fail_step=None, silent=False):
        super().__init__(daemon=True)
        self.dir, self.fake, self.submit_page, self.fail_step, self.silent = browser_dir, fake_dir, submit_page, fail_step, silent
        self.cmds = []
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        cmd_file = self.dir / "cmd.txt"
        while not self._stop.is_set():
            if not self.silent and cmd_file.exists():
                try:
                    cmd = json.loads(cmd_file.read_text())
                except ValueError:
                    time.sleep(0.01)  # a half-written file: the script must never leave one, but do not crash the fake
                    continue
                cmd_file.unlink()
                self.cmds.append(cmd)
                (self.dir / "result.txt").unlink(missing_ok=True)
                if cmd.get("goto"):
                    (self.dir / "page.txt").write_text("创建飞书 CLI 应用\n头像\n名称")
                if cmd.get("click"):
                    (self.dir / "page.txt").write_text(self.submit_page)
                    if self.fail_step == "click":
                        (self.dir / "result.txt").write_text("ERR click timeout")
                        continue
                    if self.submit_page == "创建成功":
                        (self.fake / "approved").write_text("1")
                if self.fail_step == "bad_command":  # what feishu_browser.js answers to a command it could not parse
                    (self.dir / "result.txt").write_text("ERR command is not valid JSON")
                    continue
                if self.fail_step == "goto" and cmd.get("goto"):
                    (self.dir / "result.txt").write_text("ERR net::ERR_CONNECTION_REFUSED")
                    continue
                (self.dir / "result.txt").write_text("OK https://open.feishu.cn/page/cli")
            time.sleep(0.01)


class RegisterScript(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        os.chmod(self.tmp, 0o700)
        self.fake = self.tmp / "fake"
        self.fake.mkdir(mode=0o700)
        self.bin = self.tmp / "bin"
        self.bin.mkdir(mode=0o700)
        lark = self.bin / "lark-cli"
        lark.write_text(FAKE_LARK)
        os.chmod(lark, 0o755)
        self.browser = self.tmp / "browser"
        self.browser.mkdir(mode=0o700)
        (self.browser / "ready.txt").write_text("ready https://open.feishu.cn/page/cli")
        self.agents = self.tmp / "agents"
        # 运行测试的解释器：脚本要的 python3 就放在这个目录里（不在系统命令目录里），像 CI 里的 /usr/local/bin/python3
        self.pydir = self.tmp / "py"
        self.pydir.mkdir(mode=0o700)
        (self.pydir / "python3").symlink_to(sys.executable)
        self.drivers = []
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        for d in self.drivers:
            d.stop()
        for name in ("cli.pid", "child.pid"):
            pid = self.fake / name
            if pid.exists():
                try:
                    os.kill(int(pid.read_text()), signal.SIGKILL)
                except (ProcessLookupError, ValueError):
                    pass
        for pid in self.descendants():
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        self._tmp.cleanup()

    def browser_driver(self, **kw):
        d = FakeBrowser(self.browser, self.fake, **kw)
        d.start()
        self.drivers.append(d)
        return d

    def run_script(self, name, *, env=None, browser_dir=None, path=None):
        # 默认 PATH：桩目录 + 运行测试的解释器所在的目录 + 不含 python 的系统命令目录（CI 的形状：python3 不在 /usr/bin）
        base = {"PATH": path or f"{self.bin}:{self.pydir}:{system_bin()}", "HOME": str(self.tmp), "FAKE_DIR": str(self.fake),
                "LARK_CLI": str(self.bin / "lark-cli"), "FEISHU_BROWSER_DIR": str(browser_dir or self.browser),
                "LARK_AGENTS_HOME": str(self.agents), "URL_WAIT_SECONDS": "2", "STEP_TIMEOUT_SECONDS": "3",
                "SUBMIT_WAIT_SECONDS": "3"}
        base.update(env or {})
        return subprocess.run(["bash", str(SCRIPT), name], capture_output=True, text=True, env=base, timeout=20)

    @staticmethod
    def alive(pid):
        """进程还在运行？已退出但没人收尸的僵尸不算（CI 的容器里 1 号进程不一定会收养并收尸）。"""
        try:
            with open(f"/proc/{pid}/stat") as f:
                return f.read().rsplit(")", 1)[1].split()[0] != "Z"
        except FileNotFoundError:
            # Linux 上 /proc 存在但该 PID 的 stat 不存在，说明进程已经退出；
            # 不要再依赖精简 CI 镜像里未必安装的 ps。
            if Path("/proc").is_dir():
                return False
            # macOS 没有 /proc；ps 的 Z 状态与 Linux /proc 的语义一致。
            try:
                probe = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
            except FileNotFoundError:
                return False
            return probe.returncode == 0 and not probe.stdout.strip().startswith("Z")
        except ProcessLookupError:
            return False

    def descendants(self):
        """假 lark-cli 起的所有子孙进程的 pid（FAKE_FORK）。"""
        pids = self.fake / "descendants.pids"
        return [int(x) for x in pids.read_text().split()] if pids.exists() else []

    def child_alive(self):
        pid = self.fake / "child.pid"
        return pid.exists() and self.alive(int(pid.read_text()))

    def shim(self, name, body):
        """A stand-in for a system command, found first on the script's PATH."""
        path = self.bin / name
        path.write_text("#!/bin/bash\n" + body)
        os.chmod(path, 0o755)

    def cli_alive(self):
        pid = self.fake / "cli.pid"
        return pid.exists() and self.alive(int(pid.read_text()))

    def assert_failed_cleanly(self, result, *, code=1):
        self.assertEqual(result.returncode, code, result.stderr)
        self.assertNotIn("app_id=", result.stdout, "a failed registration must not print a success line")
        self.assertNotIn("S3CRET", result.stdout + result.stderr)
        time.sleep(0.2)
        self.assertFalse(self.cli_alive(), "the background lark-cli must be stopped when the script fails")
        self.assertFalse(self.child_alive(), "and so must whatever it started")
        stragglers = [pid for pid in self.descendants() if self.alive(pid)]
        self.assertEqual(stragglers, [], "every descendant of lark-cli must be gone, not only the process the script started")

    def test_success_reports_only_after_every_check_and_leaves_no_snapshots(self):
        """L1-FRA-001: 成功路径：在确认页填的是 agent 名并点了提交；成功那一行只在 app_id 合法且回读的 app_name 等于 agent 名之后打印；
        每个 agent 的目录是 0700；浏览器目录里没有留下页面快照；不回显 API 响应里的任何其它字段。"""
        driver = self.browser_driver()
        for leftover in ("page.png", "page.html", "qr.png"):  # 上一次留下的快照，也应被清掉
            (self.browser / leftover).write_text("x")
        result = self.run_script("nh-x")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip().splitlines()[-1], "nh-x -> app_id=cli_fake000001 app_name=nh-x")
        self.assertEqual(result.stdout.count("app_id="), 1)
        self.assertNotIn("S3CRET", result.stdout + result.stderr)
        fills = [c["fill"] for c in driver.cmds if c.get("fill")]
        self.assertEqual(fills, [[["input.ud__native-input", "nh-x"]]])
        self.assertTrue(any(c.get("click") == ['button[type="submit"]'] for c in driver.cmds))
        agent = self.agents / "nh-x"
        for path in (agent, agent / "config", agent / "data"):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700, path)
        for leftover in ("page.png", "page.html", "page.txt", "qr.png", "cmd.txt"):
            self.assertFalse((self.browser / leftover).exists(), leftover)
        self.assertFalse(self.cli_alive())
        self.assertFalse((self.browser / "init-nh-x.log").exists(), "the CLI's log is removed after a successful run")

    def test_no_user_code_from_the_cli_fails(self):
        """L1-FRA-002: lark-cli 一直不给 user_code：失败退出，不打印成功行，后台的 lark-cli 被收掉。"""
        self.browser_driver()
        result = self.run_script("nh-x", env={"FAKE_NO_URL": "1"})
        self.assert_failed_cleanly(result)
        self.assertIn("user_code", result.stderr)
        # 失败时日志留下来供排查，但只有自己能读
        log = self.browser / "init-nh-x.log"
        self.assertTrue(log.exists())
        self.assertEqual(stat.S_IMODE(log.stat().st_mode), 0o600)

    def test_a_cli_that_dies_before_the_url_fails_at_once_not_after_the_timeout(self):
        """L1-FRA-002b: lark-cli 还没给 user_code 就退出了：立刻失败，不必等满 user_code 的超时。"""
        self.browser_driver()
        started = time.time()
        result = self.run_script("nh-x", env={"FAKE_CLI_DIES": "1", "URL_WAIT_SECONDS": "30"})
        self.assert_failed_cleanly(result)
        self.assertLess(time.time() - started, 10)
        self.assertIn("user_code", result.stderr)

    def test_a_browser_step_that_errors_fails(self):
        """L1-FRA-003: 浏览器打开确认页或点提交时报 ERR：失败退出、不打印成功行、收掉 lark-cli，错误里带原因。"""
        for step in ("goto", "click"):
            with self.subTest(step=step):
                self.drivers.clear()
                for f in ("approved", "cli.pid"):
                    (self.fake / f).unlink(missing_ok=True)
                d = self.browser_driver(fail_step=step)
                result = self.run_script("nh-x")
                d.stop()
                self.assert_failed_cleanly(result)
                self.assertIn("browser", result.stderr)

    def test_a_browser_that_refuses_a_command_as_invalid_fails_the_run(self):
        """L1-FRA-028: 浏览器把某条命令当作坏命令回 `ERR command is not valid JSON`（feishu_browser.js 遇到坏 cmd.txt 就这样答，
        会话本身继续活着）：脚本把这一步当失败——非零退出、不打印成功行、收掉 lark-cli，stderr 里带这条短原因。（守卫：非 OK 一律失败）"""
        d = self.browser_driver(fail_step="bad_command")
        result = self.run_script("nh-x")
        d.stop()
        self.assert_failed_cleanly(result)
        self.assertIn("browser step failed: ERR command is not valid JSON", result.stderr)

    def test_a_browser_that_never_answers_times_out(self):
        """L1-FRA-004: 浏览器会话没响应（没有 result.txt）：超时后失败退出，不是无限等，也不报成功。"""
        self.browser_driver(silent=True)
        started = time.time()
        result = self.run_script("nh-x")
        self.assert_failed_cleanly(result)
        self.assertLess(time.time() - started, 30)

    def test_the_create_failed_page_fails(self):
        """L1-FRA-005: 确认页显示「创建失败」：失败退出（调用方重试同一个码），不打印成功行。"""
        self.browser_driver(submit_page="创建失败 创建过程中出错了，请重新创建")
        result = self.run_script("nh-x")
        self.assert_failed_cleanly(result)
        self.assertIn("创建失败", result.stderr + result.stdout)

    def test_creation_that_never_finishes_times_out(self):
        """L1-FRA-005b: 确认页一直停在「正在创建…」，或页面已成功但 lark-cli 一直不退出：各自在 SUBMIT_WAIT_SECONDS 后失败，
        不是无限等，也不报成功。"""
        for label, driver_kw, env in (("page stuck", {"submit_page": "0% 正在创建..."}, {}),
                                      ("cli hangs", {}, {"FAKE_HANG": "1"})):
            with self.subTest(label):
                self.drivers.clear()
                for f in ("approved", "cli.pid", "child.pid"):
                    (self.fake / f).unlink(missing_ok=True)
                d = self.browser_driver(**driver_kw)
                started = time.time()
                result = self.run_script("nh-x", env=dict(env, SUBMIT_WAIT_SECONDS="2"))
                d.stop()
                self.assert_failed_cleanly(result)
                self.assertLess(time.time() - started, 15, label)
                self.assertIn("2s", result.stderr, label)

    def test_a_cli_that_exits_nonzero_after_approval_fails(self):
        """L1-FRA-006: 页面显示创建成功但 lark-cli 自己以非零退出：失败退出，不去回读也不报成功。"""
        self.browser_driver()
        result = self.run_script("nh-x", env={"FAKE_INIT_EXIT": "1"})
        self.assert_failed_cleanly(result)

    def test_a_bad_app_id_or_unreadable_status_fails(self):
        """L1-FRA-007: auth status 的 appId 为空、格式不对，或输出不是 JSON：失败退出。"""
        for label, env in (("empty", {"FAKE_APP_ID": ""}), ("not cli_", {"FAKE_APP_ID": "abc123"}),
                           ("only the prefix", {"FAKE_APP_ID": "cli_"}), ("a space in it", {"FAKE_APP_ID": "cli_ab cd"}),
                           ("shell metacharacters", {"FAKE_APP_ID": "cli_ab;rm"}),
                           ("not json", {"FAKE_STATUS_RAW": "not json at all"}), ("no appId", {"FAKE_STATUS_RAW": "{}"})):
            with self.subTest(label):
                self.drivers.clear()
                for f in ("approved", "cli.pid"):
                    (self.fake / f).unlink(missing_ok=True)
                d = self.browser_driver()
                result = self.run_script("nh-x", env=env)
                d.stop()
                self.assert_failed_cleanly(result)

    def test_an_app_name_that_is_not_the_agent_name_fails(self):
        """L1-FRA-008: 回读的 app_name 不等于 agent 名（例如还是预填的「陈敬敏的飞书 CLI」，或只是前缀相同）：失败退出，
        错误里同时写出期望和实际，不打印成功行。这一条就是脚本存在的理由。"""
        for got in ("陈敬敏的飞书 CLI", "nh-x2", "NH-X", " nh-x", ""):
            with self.subTest(got=got):
                self.drivers.clear()
                for f in ("approved", "cli.pid"):
                    (self.fake / f).unlink(missing_ok=True)
                d = self.browser_driver()
                result = self.run_script("nh-x", env={"FAKE_APP_NAME": got})
                d.stop()
                self.assert_failed_cleanly(result)
                self.assertIn("app_name mismatch: expected 'nh-x'", result.stderr)

    def test_an_api_call_that_exits_nonzero_is_not_trusted_even_with_a_good_name(self):
        """L1-FRA-008b: lark-cli api 以非零退出时，即使它的输出里恰好有对的 app_name 也不算数（pipefail）：失败退出。"""
        self.browser_driver()
        result = self.run_script("nh-x", env={"FAKE_API_EXIT": "1"})
        self.assert_failed_cleanly(result)

    def test_an_api_answer_without_a_name_fails(self):
        """L1-FRA-009: 应用信息接口报错或没有 app_name：失败退出。"""
        for raw in ('{"ok":false,"error":{"type":"permission"}}', "garbage", "{}"):
            with self.subTest(raw=raw):
                self.drivers.clear()
                for f in ("approved", "cli.pid"):
                    (self.fake / f).unlink(missing_ok=True)
                d = self.browser_driver()
                result = self.run_script("nh-x", env={"FAKE_API_RAW": raw})
                d.stop()
                self.assert_failed_cleanly(result)

    def test_agent_names_are_an_allowlist(self):
        """L1-FRA-010: agent 名只能是小写字母开头、小写字母数字和连字符、最长 32：空、带 / 或 ..、大写、下划线、过长都退出码 2，
        不创建任何目录，也不动 lark-cli。"""
        for bad in ("", "../x", "a/b", ".hidden", "A-B", "a_b", "1abc", "x" * 33, "a b", "-a"):
            with self.subTest(name=bad):
                result = self.run_script(bad)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertFalse(self.agents.exists())
                self.assertFalse((self.fake / "lark-calls.log").exists())

    def test_the_browser_directory_must_be_private_and_logged_in(self):
        """L1-FRA-011: FEISHU_BROWSER_DIR 必须设置、是自己的 0700 目录、并且浏览器会话已登录（有 ready.txt）：否则退出码 2，
        不起 lark-cli。（目录里会放登录态页面的快照，也是驱动浏览器的命令入口。）"""
        unset = self.run_script("nh-x", env={"FEISHU_BROWSER_DIR": ""})
        self.assertEqual(unset.returncode, 2)
        self.assertIn("FEISHU_BROWSER_DIR must name", unset.stderr)  # 说清楚缺的是什么，而不是「不是目录」
        shared = self.tmp / "shared"
        shared.mkdir()
        os.chmod(shared, 0o755)
        (shared / "ready.txt").write_text("ready")
        self.assertEqual(self.run_script("nh-x", browser_dir=shared).returncode, 2)
        cold = self.tmp / "cold"
        cold.mkdir(mode=0o700)
        self.assertEqual(self.run_script("nh-x", browser_dir=cold).returncode, 2)
        self.assertEqual(self.run_script("nh-x", browser_dir=self.tmp / "missing").returncode, 2)
        # 目录属主不是自己（这里让 id -u 报一个别的 uid）
        self.shim("id", 'echo 4242\n')
        self.assertEqual(self.run_script("nh-x").returncode, 2)
        self.assertFalse((self.fake / "lark-calls.log").exists())

    def test_commands_are_written_atomically(self):
        """L1-FRA-012: 给浏览器的每一条命令都是先写 cmd.txt.tmp 再用 mv 改名成 cmd.txt：驱动方任何时候都读不到写了一半的 cmd.txt。
        （用一个记录参数的 mv 替身观察，比赛跑式的检查可靠。）"""
        mv_log = self.fake / "mv.log"
        self.shim("mv", f'echo "$@" >> "{mv_log}"\nexec /bin/mv "$@"\n')
        driver = self.browser_driver()
        result = self.run_script("nh-x")
        self.assertEqual(result.returncode, 0, result.stderr)
        renames = [line.split() for line in mv_log.read_text().splitlines()]
        self.assertTrue(driver.cmds)
        self.assertEqual(len(renames), len(driver.cmds))
        for args in renames:
            self.assertEqual([Path(a).name for a in args[-2:]], ["cmd.txt.tmp", "cmd.txt"], args)

    def test_a_missing_python3_fails_before_anything_starts(self):
        """L1-FRA-014: PATH 里找不到 python3：一开始就以退出码 2 失败（什么都没做），说清楚缺的是 python3，
        不创建目录、不起 lark-cli。不是先起 lark-cli、等到拼浏览器命令时才在第 N 步报「could not build」。"""
        result = self.run_script("nh-x", path=f"{self.bin}:{system_bin()}")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("python3", result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertFalse(self.agents.exists())
        self.assertFalse((self.fake / "lark-calls.log").exists())

    def test_the_script_uses_the_python3_found_on_its_path(self):
        """L1-FRA-015: 脚本用的是 PATH 上找到的 python3（这里是放在系统命令目录之外的一个记录调用的替身），
        不写死 /usr/bin/python3 之类的位置：一次成功的注册里它被调用过。"""
        log = self.fake / "python.log"
        (self.pydir / "python3").unlink()
        shim = self.pydir / "python3"
        shim.write_text(f'#!/bin/bash\necho "$1" >> "{log}"\nexec "{sys.executable}" "$@"\n')
        os.chmod(shim, 0o755)
        self.browser_driver()
        result = self.run_script("nh-x", path=f"{self.bin}:{self.pydir}:{system_bin()}")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(log.exists() and log.read_text().strip(), "the script never ran the python3 on its PATH")

    def test_failure_cleanup_reaches_every_descendant_of_lark_cli_without_setsid(self):
        """L1-FRA-016: 系统里没有 setsid 时（macOS 就没有），失败清理也要把 lark-cli 和它起的所有子孙进程都收掉——
        包括忽略 TERM 的、以及 lark-cli 自己已经退出后还留着的：不只是脚本直接起的那个进程。
        （假 lark-cli 带着子进程和孙进程干活，失败之后逐个检查 pid 都不在了。）"""
        no_setsid = f"{self.bin}:{self.pydir}:{system_bin('setsid')}"
        self.assertIsNone(shutil.which("setsid", path=no_setsid), "the test's PATH must really have no setsid")
        scenarios = (("no user_code", {"FAKE_NO_URL": "1"}, {}),
                     ("browser step fails", {}, {"fail_step": "goto"}),
                     ("cli hangs after approval", {"FAKE_HANG": "1", "SUBMIT_WAIT_SECONDS": "2"}, {}),
                     ("cli exits non-zero and leaves its children", {"FAKE_INIT_EXIT": "1"}, {}))
        for label, env, driver_kw in scenarios:
            with self.subTest(label):
                self.drivers.clear()
                for pid in self.descendants():  # 上一个场景可能（在有缺陷时）留下的，别让它们跑到测试结束之后
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except (ProcessLookupError, PermissionError):
                        pass
                for f in ("approved", "cli.pid", "child.pid", "descendants.pids"):
                    (self.fake / f).unlink(missing_ok=True)
                d = self.browser_driver(**driver_kw)
                result = self.run_script("nh-x", env=dict(env, FAKE_FORK="1"), path=no_setsid)
                d.stop()
                self.assertTrue(self.descendants(), "the fake lark-cli started no descendants: the scenario tests nothing")
                self.assert_failed_cleanly(result)

    def test_failure_cleanup_reaches_every_descendant_when_setsid_exists_too(self):
        """L1-FRA-016b: 有 setsid 的系统上同样：失败之后 lark-cli 和它的子孙进程一个都不剩。"""
        self.browser_driver()
        result = self.run_script("nh-x", env={"FAKE_NO_URL": "1", "FAKE_FORK": "1"},
                                 path=f"{self.bin}:{self.pydir}:{system_bin()}:/usr/bin")
        self.assertTrue(self.descendants())
        self.assert_failed_cleanly(result)

    def assert_refused_untouched(self, result, *victims):
        """符号链接被拒绝：退出码 2、说明原因、什么都没做（没起 lark-cli），链接指向的目录的权限没被动过。"""
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertIn("symbolic link", result.stderr)
        self.assertFalse((self.fake / "lark-calls.log").exists())
        for victim, mode in victims:
            self.assertEqual(stat.S_IMODE(victim.stat().st_mode), mode, f"{victim} was chmod-ed through a symbolic link")
            self.assertEqual(list(victim.iterdir()), [], f"something was created inside {victim} through a symbolic link")

    def test_a_symlinked_ready_marker_does_not_count_as_a_logged_in_session(self):
        """L1-FRA-022: ready.txt 是符号链接（指向一个存在的文件）：不算「浏览器已登录」，退出码 2、不起 lark-cli。
        （ready.txt 是浏览器会话自己写的普通文件；链接能让一个空目录冒充已登录的会话。）"""
        (self.browser / "ready.txt").unlink()
        real = self.tmp / "somewhere-else"
        real.write_text("ready")
        (self.browser / "ready.txt").symlink_to(real)
        result = self.run_script("nh-x")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("ready.txt", result.stderr)
        self.assertFalse((self.fake / "lark-calls.log").exists())

    def test_symlinked_agent_directories_are_refused_not_followed(self):
        """L1-FRA-023: LARK_AGENTS_HOME、<home>/<agent>、其下的 config / data 任何一个是符号链接：退出码 2、什么都不做——
        不顺着链接去 chmod 700 别处的目录，也不在链接指向的目录里建东西。"""
        victim = self.tmp / "victim"
        for label in ("agents home", "agent dir", "config", "data"):
            with self.subTest(label):
                if self.agents.is_symlink():
                    self.agents.unlink()
                else:
                    shutil.rmtree(self.agents, ignore_errors=True)
                shutil.rmtree(victim, ignore_errors=True)
                victim.mkdir(mode=0o755)
                os.chmod(victim, 0o755)
                if label == "agents home":
                    self.agents.symlink_to(victim)
                else:
                    self.agents.mkdir(mode=0o700)
                    agent = self.agents / "nh-x"
                    if label == "agent dir":
                        agent.symlink_to(victim)
                    else:
                        agent.mkdir(mode=0o700)
                        (agent / label).symlink_to(victim)
                result = self.run_script("nh-x")
                self.assert_refused_untouched(result, (victim, 0o755))

    def test_a_directory_swapped_for_a_symlink_after_creation_is_caught(self):
        """L1-FRA-023b: 检查之后、使用之前，某个 agent 目录被换成符号链接（竞态；这里用 chmod 的替身在真 chmod 之后换掉 data）：
        创建之后的复核发现它不是自己的私有目录，失败退出、不起 lark-cli，也不往链接指向的目录里写。"""
        victim = self.tmp / "victim"
        victim.mkdir(mode=0o700)
        self.shim("chmod", f'''/bin/chmod "$@" || exit $?
case "$*" in *nh-x/data*) /bin/rmdir "{self.agents}/nh-x/data" && /bin/ln -s "{victim}" "{self.agents}/nh-x/data" ;; esac
''')
        result = self.run_script("nh-x")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("private directory", result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertFalse((self.fake / "lark-calls.log").exists())
        self.assertEqual(list(victim.iterdir()), [])

    def test_symlinks_planted_at_the_scripts_scratch_files_are_not_followed(self):
        """L1-FRA-024: 浏览器目录里的 init-<agent>.log 和 cmd.txt.tmp 事先被换成指向别处文件的符号链接：脚本照常完成注册，
        但不往链接指向的文件里写（不截断它、不追加 lark-cli 的输出、不写命令）——创建时用独占方式，不跟随链接。"""
        victims = {}
        for name in ("init-nh-x.log", "cmd.txt.tmp"):
            victim = self.tmp / f"victim-{name}"
            victim.write_text("do not touch")
            (self.browser / name).symlink_to(victim)
            victims[name] = victim
        self.browser_driver()
        result = self.run_script("nh-x")
        self.assertEqual(result.returncode, 0, result.stderr)
        for name, victim in victims.items():
            self.assertEqual(victim.read_text(), "do not touch", f"{name} was written through a symbolic link")

    def test_a_script_that_is_interrupted_still_stops_lark_cli_and_its_children(self):
        """L1-FRA-017: 脚本在等浏览器时被 SIGTERM / SIGINT / SIGHUP 打断（终端关了、被 timeout 杀了）：EXIT 清理照样跑，
        lark-cli 和它的子孙进程一个不剩，不成功也不打印成功行。"""
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            with self.subTest(sig=sig.name):
                self.drivers.clear()
                for pid in self.descendants():
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                for f in ("approved", "cli.pid", "descendants.pids"):
                    (self.fake / f).unlink(missing_ok=True)
                d = self.browser_driver(silent=True)
                env = {"PATH": f"{self.bin}:{self.pydir}:{system_bin()}", "HOME": str(self.tmp), "FAKE_DIR": str(self.fake),
                       "LARK_CLI": str(self.bin / "lark-cli"), "FEISHU_BROWSER_DIR": str(self.browser),
                       "LARK_AGENTS_HOME": str(self.agents), "STEP_TIMEOUT_SECONDS": "30", "FAKE_FORK": "1"}
                # 后台作业和 CI 的 runner 会把「忽略 SIGINT」传给子进程，而 shell 无法 trap 入口就被忽略的信号：
                # 先在 exec 之前把 SIGINT 恢复成默认，测的才是脚本自己对 Ctrl-C 的处理，与测试是从哪里启动的无关。
                launcher = "import os, signal, sys; signal.signal(signal.SIGINT, signal.SIG_DFL); os.execvp('bash', ['bash'] + sys.argv[1:])"
                proc = subprocess.Popen([sys.executable, "-c", launcher, str(SCRIPT), "nh-x"], env=env, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, text=True, start_new_session=True)
                try:
                    deadline = time.time() + 10
                    while time.time() < deadline and not (self.browser / "cmd.txt").exists():  # it is now waiting for the browser
                        time.sleep(0.05)
                    self.assertTrue((self.browser / "cmd.txt").exists(), "the script never reached its first browser step")
                    self.assertTrue(self.cli_alive())
                    # Ctrl-C 送给前台的整个进程组（bash 只在它等的子进程也死于 SIGINT 时才跟着退出）；其它信号只送给脚本
                    os.killpg(proc.pid, sig) if sig == signal.SIGINT else proc.send_signal(sig)
                    out, err = proc.communicate(timeout=10)
                finally:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)  # 兜底：脚本已经退出时这是空操作
                    except ProcessLookupError:
                        pass
                    proc.wait()
                    for stream in (proc.stdout, proc.stderr):
                        stream.close()
                    d.stop()
                self.assertNotEqual(proc.returncode, 0)
                self.assert_failed_cleanly(subprocess.CompletedProcess([], proc.returncode, out, err), code=proc.returncode)


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class BrowserSession(unittest.TestCase):
    def test_refuses_a_shared_working_directory(self):
        """L1-FRA-013: feishu_browser.js 只在自己的 0700 目录里运行（它会把登录态页面的快照写在当前目录、并从这里接收命令）：
        目录对同组或其他人可访问时立刻退出码 2，不去加载浏览器。"""
        with tempfile.TemporaryDirectory() as tmp:
            os.chmod(tmp, 0o755)
            result = subprocess.run(["node", str(BROWSER_JS), "https://example.test/"], cwd=tmp, capture_output=True,
                                    text=True, timeout=30)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("private", result.stderr)
            self.assertEqual(os.listdir(tmp), [])

    # ---- 会话的清理：任何一种退出方式，登录态页面的快照都不能留在目录里 ----

    SNAPSHOTS = ("page.png", "page.html", "page.txt", "qr.png", "ready.txt", "state.txt", "cmd.txt", "result.txt")

    STUB_PLAYWRIGHT = r'''
// 假的 playwright-core：页面一直「已登录」，evaluate / screenshot 可按环境变量报错，关闭上下文时留个记号。
const fs = require('fs');
let isClosed = false; // 像真的 Playwright：上下文关了以后，还在用页面的代码会收到异常
const closedError = () => new Error('page.waitForTimeout: Target page, context or browser has been closed');
const wait = async (ms) => {
  if (isClosed) throw closedError();
  // STUB_CRASH_FILE: the page dies between two commands (a browser crash, the tab closed from outside) once this file exists
  if (process.env.STUB_CRASH_FILE && fs.existsSync(process.env.STUB_CRASH_FILE)) throw new Error('page crashed');
  await new Promise((r) => setTimeout(r, Math.min(ms, 20)));
  if (isClosed) throw closedError(); // 等待中途上下文被关了
};
const page = {
  url: () => 'https://open.feishu.cn/page/cli',
  goto: async (url) => { if (process.env.STUB_CALLS) fs.appendFileSync(process.env.STUB_CALLS, url + '\n'); },
  $: async () => null,
  waitForTimeout: wait,
  evaluate: async () => 'page text',
  screenshot: async ({ path }) => { if (process.env.STUB_SHOT_ERR) throw new Error('screenshot boom'); fs.writeFileSync(path, 'png'); },
  fill: async () => {},
  click: async () => {},
};
module.exports = { chromium: { launchPersistentContext: async () => ({
  pages: () => [page], newPage: async () => page,
  close: async () => { isClosed = true; await new Promise((r) => setTimeout(r, 60)); fs.appendFileSync(process.env.STUB_CLOSED, 'closed\n'); },
}) } };
'''

    def start_session(self, tmp, prepare=None, **extra_env):
        """在一个 0700 的工作目录里，用假的 playwright-core 起 feishu_browser.js；返回 (进程, 工作目录, 「上下文被关闭」的记号文件)。
        prepare(work) 在会话启动之前往工作目录里放东西（例如上一次会话留下的文件）。"""
        work = Path(tmp) / "work"
        work.mkdir(mode=0o700)
        if prepare:
            prepare(work)
        stub = Path(tmp) / "stub" / "playwright-core"
        stub.mkdir(parents=True)
        (stub / "index.js").write_text(self.STUB_PLAYWRIGHT)
        closed = Path(tmp) / "ctx-closed.txt"
        env = dict(os.environ, HOME=str(tmp), NODE_PATH=str(stub.parent), STUB_CLOSED=str(closed), **extra_env)
        proc = subprocess.Popen(["node", str(BROWSER_JS), "https://open.feishu.cn/x"], cwd=work, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: (proc.poll() is None and proc.kill(), proc.wait(), proc.stdout.close(), proc.stderr.close()))
        return proc, work, closed

    def wait_for(self, path, seconds=10):
        deadline = time.time() + seconds
        while time.time() < deadline and not path.exists():
            time.sleep(0.02)
        self.assertTrue(path.exists(), f"{path.name} never appeared")

    def assert_no_snapshots(self, work):
        left = [f for f in self.SNAPSHOTS if (work / f).exists()]
        self.assertEqual(left, [], "the logged-in session's page snapshots and command files must not outlive the session")

    def test_a_quit_leaves_no_snapshots_and_closes_the_browser(self):
        """L1-FRA-018: 正常退出（quit.txt）：退出码 0，登录态页面的快照和命令文件都清掉，浏览器上下文已关闭。（守卫）"""
        with tempfile.TemporaryDirectory() as tmp:
            proc, work, closed = self.start_session(tmp)
            self.wait_for(work / "ready.txt")
            self.assertTrue((work / "page.txt").exists())
            (work / "quit.txt").write_text("1")
            proc.communicate(timeout=10)
            self.assertEqual(proc.returncode, 0)
            self.assert_no_snapshots(work)
            self.assertTrue(closed.exists())

    def test_a_crash_still_cleans_up_and_fails_loudly(self):
        """L1-FRA-019: 主循环里出了没接住的异常（这里是登录之后、两条命令之间页面崩了）：非零退出，原因写进 error.txt，
        而且照样清掉快照和命令文件、关掉浏览器——不是只写 error.txt 就把登录态页面的快照留在磁盘上、浏览器还开着。
        （L1-FRA-027 之前用「cmd.txt 不是合法 JSON」触发它；那现在只算这一步失败，不再崩，所以换成页面崩溃。）"""
        with tempfile.TemporaryDirectory() as tmp:
            crash = Path(tmp) / "crash-now"
            proc, work, closed = self.start_session(tmp, STUB_CRASH_FILE=str(crash))
            self.wait_for(work / "ready.txt")
            crash.write_text("1")
            proc.communicate(timeout=10)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("page crashed", (work / "error.txt").read_text())
            self.assert_no_snapshots(work)
            self.assertTrue(closed.exists(), "the browser context must be closed even when the loop crashes")

    def test_a_crash_while_taking_the_first_snapshot_leaves_no_half_written_snapshot(self):
        """L1-FRA-020: 登录后拍第一份快照时出错（页面文字和 HTML 已写、截图失败）：非零退出，已写出的半份快照也清掉。"""
        with tempfile.TemporaryDirectory() as tmp:
            proc, work, closed = self.start_session(tmp, STUB_SHOT_ERR="1")
            proc.communicate(timeout=10)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("screenshot boom", (work / "error.txt").read_text())
            self.assert_no_snapshots(work)
            self.assertTrue(closed.exists())

    def read_result(self, work, seconds=10):
        """result.txt 一出现且有内容就读出来（写文件不是原子的：刚创建时可能还是空的）。"""
        path = work / "result.txt"
        deadline = time.time() + seconds
        while time.time() < deadline:
            try:
                text = path.read_text()
            except FileNotFoundError:
                text = ""
            if text:
                return text
            time.sleep(0.02)
        self.fail("result.txt never got an answer")

    def test_a_stale_quit_marker_from_an_earlier_session_does_not_end_the_next_one(self):
        """L1-FRA-026: quit.txt 是让会话退出的信号，用完不删；下一次在同一个目录启动时它还在，会话一登录就立刻退出（要重新扫码）。
        所以启动时先清掉旧的 quit.txt：普通文件删掉；符号链接只删链接本身、不去碰它指向的文件。清掉之后新写的 quit.txt 照常让会话退出。"""
        for kind in ("regular file", "symbolic link"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                victim = Path(tmp) / "somebody-elses-file"
                victim.write_text("keep me")

                def stale(work, kind=kind):
                    if kind == "regular file":
                        (work / "quit.txt").write_text("1")
                    else:
                        (work / "quit.txt").symlink_to(victim)

                proc, work, closed = self.start_session(tmp, prepare=stale)
                self.wait_for(work / "ready.txt")
                time.sleep(0.5)
                self.assertIsNone(proc.poll(), "a quit marker left by an earlier session ended this one")
                self.assertTrue((work / "ready.txt").exists())
                self.assertFalse(os.path.lexists(work / "quit.txt"), "the stale marker must be gone")
                self.assertEqual(victim.read_text(), "keep me", "a symbolic link must never be followed")
                (work / "quit.txt").write_text("1")  # a quit asked for during this session still works
                proc.communicate(timeout=10)
                self.assertEqual(proc.returncode, 0)
                self.assertTrue(closed.exists())

    def test_a_bad_command_file_fails_that_step_and_the_session_goes_on(self):
        """L1-FRA-027: cmd.txt 不是合法 JSON（或者根本不是一个 JSON 对象）：只算这一步失败——result.txt 写 `ERR <短原因>`，
        原因里不回显命令内容（里面可能有要填进表单的敏感字符串），cmd.txt 删掉，会话继续等下一条命令：进程还在、
        浏览器没关、ready.txt 还在（不用重新扫码）、error.txt 不出现，之后的合法命令照常执行。"""
        secret = "S3CRET-FORM-VALUE"
        bad_commands = {
            "truncated": '{"fill": [["input", "' + secret + '"',
            "not json": "{not json " + secret,
            "empty": "",
            "a list": '["' + secret + '"]',
            "null": "null",
            "a string": '"' + secret + '"',
            "a number": "7",
        }
        with tempfile.TemporaryDirectory() as tmp:
            calls = Path(tmp) / "calls.txt"
            proc, work, closed = self.start_session(tmp, STUB_CALLS=str(calls))
            self.wait_for(work / "ready.txt")
            for label, text in bad_commands.items():
                with self.subTest(command=label):
                    (work / "result.txt").unlink(missing_ok=True)
                    (work / "cmd.txt").write_text(text)
                    answer = self.read_result(work)
                    self.assertRegex(answer, r"^ERR \S", answer)
                    self.assertLess(len(answer), 100, "a short reason, not the command")
                    self.assertNotIn(secret, answer)
                    self.assertFalse((work / "cmd.txt").exists(), "the bad command must be removed")
                    self.assertIsNone(proc.poll(), "a bad command must not end the session")
                    self.assertTrue((work / "ready.txt").exists(), "the login marker must stay: no new scan needed")
                    self.assertFalse((work / "error.txt").exists())
                    self.assertFalse(closed.exists(), "the browser must stay open")
            (work / "result.txt").unlink(missing_ok=True)
            (work / "cmd.txt").write_text('{"goto": "https://ok.test/"}')
            self.assertTrue(self.read_result(work).startswith("OK"))
            self.assertEqual(calls.read_text().split(), ["https://open.feishu.cn/x", "https://ok.test/"])

    def test_termination_signals_clean_up_too(self):
        """L1-FRA-021: 会话被 SIGTERM / SIGINT / SIGHUP 终止（终端关了、被 kill）：快照和命令文件照样清掉，浏览器上下文已关闭。"""
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            with self.subTest(sig=sig.name), tempfile.TemporaryDirectory() as tmp:
                proc, work, closed = self.start_session(tmp)
                self.wait_for(work / "ready.txt")
                proc.send_signal(sig)
                proc.communicate(timeout=10)
                self.assertNotEqual(proc.returncode, 0)
                self.assert_no_snapshots(work)
                self.assertTrue(closed.exists())
                self.assertFalse((work / "error.txt").exists(), "being stopped on purpose is not an error to report")

    def test_a_symlinked_command_file_is_never_executed(self):
        """L1-FRA-025: cmd.txt 是符号链接（哪怕指向一份合法的命令）：不执行、把链接删掉，会话继续可用——之后写的普通命令照常执行。
        命令文件只按「没有跟随链接地打开的普通文件、属主是自己」读取。"""
        with tempfile.TemporaryDirectory() as tmp:
            calls = Path(tmp) / "calls.txt"
            proc, work, closed = self.start_session(tmp, STUB_CALLS=str(calls))
            self.wait_for(work / "ready.txt")
            victim = Path(tmp) / "evil-command.json"
            victim.write_text('{"goto": "https://evil.test/"}')
            (work / "cmd.txt").symlink_to(victim)
            deadline = time.time() + 5
            while time.time() < deadline and os.path.lexists(work / "cmd.txt"):
                time.sleep(0.02)
            self.assertFalse(os.path.lexists(work / "cmd.txt"), "the symbolic link must be removed")
            self.assertEqual(victim.read_text(), '{"goto": "https://evil.test/"}')
            self.assertEqual(calls.read_text().split(), ["https://open.feishu.cn/x"],  # 只有启动时打开的那一次
                             "a command read through a symbolic link was executed")
            (work / "cmd.txt").write_text('{"goto": "https://ok.test/"}')
            self.wait_for(work / "result.txt")
            self.assertEqual(calls.read_text().split(), ["https://open.feishu.cn/x", "https://ok.test/"])
            self.assertIsNone(proc.poll(), "the session must survive a refused command")

    def test_a_command_file_that_is_not_a_regular_file_is_dropped_unread(self):
        """L1-FRA-025b: cmd.txt 是命名管道（读它会一直阻塞）：不去读、删掉，会话不被卡住，之后的普通命令照常执行。"""
        with tempfile.TemporaryDirectory() as tmp:
            calls = Path(tmp) / "calls.txt"
            proc, work, closed = self.start_session(tmp, STUB_CALLS=str(calls))
            self.wait_for(work / "ready.txt")
            os.mkfifo(work / "cmd.txt")
            deadline = time.time() + 5
            while time.time() < deadline and os.path.lexists(work / "cmd.txt"):
                time.sleep(0.02)
            self.assertFalse(os.path.lexists(work / "cmd.txt"), "the FIFO must be removed")
            (work / "cmd.txt").write_text('{"goto": "https://ok.test/"}')
            self.wait_for(work / "result.txt")
            self.assertEqual(calls.read_text().split(), ["https://open.feishu.cn/x", "https://ok.test/"])
            self.assertIsNone(proc.poll())


class ThreatModelIsWrittenDown(unittest.TestCase):
    """The symbolic-link checks in these scripts are best effort on purpose (accepted by the owner, skills#117): say what they do
    and do not defend against, in the scripts and where the scripts are documented, in the same words."""

    SENTENCE = "威胁模型：单用户、0700 目录；符号链接检查是检查前 + 创建后复核，不防同 uid 的进程。"
    SKILL = SCRIPTS.parents[1]

    def test_the_threat_model_is_stated_in_the_scripts_and_their_docs(self):
        """L1-FRA-029: 威胁模型（单用户、0700 目录；符号链接检查是检查前 + 创建后复核，不防同 uid 的进程）在四处用同一句话写明：
        feishu_register_agent_apps.sh 和 feishu_browser.js 的注释、feishu-group-sync.md「agent 的飞书身份」一节、
        scripts/README.md 里这个脚本的那一行——读的人不会以为符号链接检查能挡住同一个用户下的其他进程。"""
        group_sync = (self.SKILL / "references" / "feishu-group-sync.md").read_text(encoding="utf-8")
        section = group_sync[group_sync.index("## agent 的飞书身份"):group_sync.index("## 边界")]
        rows = [line for line in (SCRIPTS / "README.md").read_text(encoding="utf-8").splitlines()
                if "feishu_register_agent_apps.sh" in line]
        self.assertEqual(len(rows), 1)
        places = {
            "feishu_register_agent_apps.sh": SCRIPT.read_text(encoding="utf-8"),
            "feishu_browser.js": BROWSER_JS.read_text(encoding="utf-8"),
            "feishu-group-sync.md 「agent 的飞书身份」": section,
            "scripts/README.md 的脚本行": rows[0],
        }
        for name, text in places.items():
            with self.subTest(place=name):
                self.assertEqual(text.count(self.SENTENCE), 1, f"{name} must state the threat model exactly once")

if __name__ == "__main__":
    unittest.main()
