"""Remote web session: loopback browser, temporary profile, ssh -L takeover.

The operator host has no screen the person can see. Chrome or x11vnc may listen
only on 127.0.0.1. The person reaches that port through ssh local forwarding.
A batch profile is created for one login and deleted afterwards. Output must
not carry cookies or tokens.
"""

from __future__ import annotations

import json
import os
import re
import socket
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL = Path(__file__).resolve().parents[1]
SCRIPT = SKILL / "scripts" / "remote_web_session.py"
DOC = SKILL / "SKILL.md"

sys.path.insert(0, str(SKILL / "scripts"))


def load_module():
    import remote_web_session as module
    return module


DOC_FACTS = ('Casdoor', '飞书', 'Cloudflare', 'Token', 'Session', 'once', 'save', '本机', '远程', 'VNC', '身份', '权限')


class SessionPlanTest(unittest.TestCase):
    def setUp(self):
        self.rws = load_module()

    def test_chrome_argv_binds_loopback_only(self):
        profile = Path("/tmp/batch-a")
        argv = self.rws.chrome_argv("/usr/bin/google-chrome", profile, 9222)
        text = " ".join(argv)
        self.assertIn("--remote-debugging-address=127.0.0.1", argv)
        self.assertIn("--remote-debugging-port=9222", argv)
        self.assertIn("--user-data-dir=/tmp/batch-a", argv)
        self.assertIn("--remote-allow-origins=http://127.0.0.1:9222", argv)
        self.assertNotIn("--remote-allow-origins=*", argv)
        self.assertNotIn("--no-sandbox", argv)
        self.assertNotIn("0.0.0.0", text)
        with self.assertRaises(self.rws.PlanError):
            self.rws.chrome_argv("/usr/bin/google-chrome", profile, 9222, bind="0.0.0.0")

    def test_vnc_argv_listens_on_loopback_without_a_password_flag(self):
        argv = self.rws.vnc_argv("/usr/bin/x11vnc", ":99", 5900)
        self.assertEqual(argv[0], "/usr/bin/x11vnc")
        self.assertIn("127.0.0.1", argv)
        self.assertNotIn("0.0.0.0", argv)
        self.assertNotIn("-passwd", argv)
        with self.assertRaises(self.rws.PlanError):
            self.rws.vnc_argv("/usr/bin/x11vnc", ":99", 5900, bind="0.0.0.0")
        with self.assertRaises(self.rws.PlanError):
            self.rws.vnc_argv("/usr/bin/x11vnc", "evil", 5900)

    def test_ssh_forward_targets_the_remote_loopback(self):
        argv = self.rws.ssh_forward_args(9222, "owner", "build-host")
        self.assertIn("127.0.0.1:9222:127.0.0.1:9222", argv)
        self.assertIn("GatewayPorts=no", argv)
        self.assertIn("owner@build-host", argv)
        self.assertNotIn("0.0.0.0", argv)
        allowed = self.rws.ssh_forward_args(
            9222, "tunnel", "webhost",
            extra_options=(
                "-i", "/tmp/key",
                "-o", "IdentitiesOnly=yes",
                "-o", "StrictHostKeyChecking=no",
                "-o", "UserKnownHostsFile=/dev/null",
            ),
        )
        self.assertIn("/tmp/key", allowed)
        for extra in (
            ("-L", "9222:10.1.2.3:9222"),
            ("-o", "ProxyCommand=/tmp/x"),
            ("-o", "GatewayPorts=yes"),
            ("-o", "LocalForward=9222:10.1.2.3:80"),
            ("-g",),
        ):
            with self.assertRaises(self.rws.PlanError):
                self.rws.ssh_forward_args(9222, "owner", "build-host", extra_options=extra)
        with self.assertRaises(self.rws.PlanError):
            self.rws.ssh_forward_args(9222, "owner", "bad host")
        with self.assertRaises(self.rws.PlanError):
            self.rws.ssh_forward_args(0, "owner", "build-host")

    def test_profile_is_a_fresh_private_directory_and_is_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "profiles"
            root.mkdir(mode=0o700)
            profile = self.rws.allocate_profile(root, "canva-1")
            self.assertEqual(profile, root / "canva-1")
            self.assertEqual(stat.S_IMODE(profile.stat().st_mode), 0o700)
            with self.assertRaises(self.rws.PlanError):
                self.rws.allocate_profile(root, "canva-1")
            self.rws.delete_profile(root, "canva-1")
            self.assertFalse(profile.exists())

    def test_profile_rejects_loose_roots_and_paths_outside_the_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "profiles"
            root.mkdir(mode=0o755)
            with self.assertRaises(self.rws.PlanError):
                self.rws.allocate_profile(root, "batch")
            root.chmod(0o700)
            outside = Path(tmp) / "outside"
            outside.mkdir()
            with self.assertRaises(self.rws.PlanError):
                self.rws.delete_profile(root, "../outside")
            with self.assertRaises(self.rws.PlanError):
                self.rws.allocate_profile(root, "../outside")

    def test_symlink_roots_and_symlink_profiles_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            real = base / "profiles"
            real.mkdir(mode=0o700)
            linked_root = base / "linked-root"
            linked_root.symlink_to(real)
            with self.assertRaises(self.rws.PlanError):
                self.rws.allocate_profile(linked_root, "batch-1")
            self.assertFalse((real / "batch-1").exists())

            elsewhere = base / "elsewhere"
            elsewhere.mkdir(mode=0o700)
            marker = elsewhere / "keep.txt"
            marker.write_text("keep", encoding="utf-8")
            batch_link = real / "batch-1"
            batch_link.symlink_to(elsewhere)
            with self.assertRaises(self.rws.PlanError):
                self.rws.allocate_profile(real, "batch-1")
            with self.assertRaises(self.rws.PlanError):
                self.rws.delete_profile(real, "batch-1")
            self.assertTrue(batch_link.is_symlink())
            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
            self.assertTrue(elsewhere.is_dir())

    def test_card_names_the_site_and_drops_secrets(self):
        card = self.rws.takeover_card(
            mode="cdp",
            port=9222,
            ssh_user="owner",
            ssh_host="build-host",
            site="Canva cookie=super-secret token=also-secret",
        )
        self.assertIn("Canva", card)
        self.assertIn("127.0.0.1:9222:127.0.0.1:9222", card)
        self.assertIn("http://127.0.0.1:9222", card)
        self.assertEqual(
            card.splitlines()[-1],
            " ".join(self.rws.ssh_forward_args(9222, "owner", "build-host")),
        )
        self.assertNotIn("super-secret", card)
        self.assertNotIn("also-secret", card)
        self.assertNotIn("cookie=", card.lower())
        header = self.rws.takeover_card(
            mode="cdp",
            port=9222,
            ssh_user="owner",
            ssh_host="build-host",
            site="Canva Authorization: Bearer header-secret token: colon-secret access_token=assigned-secret",
        )
        self.assertIn("Canva", header)
        self.assertNotIn("header-secret", header)
        self.assertNotIn("colon-secret", header)
        self.assertNotIn("assigned-secret", header)
        with self.assertRaises(self.rws.PlanError):
            self.rws.takeover_card(
                mode="cdp",
                port=9222,
                ssh_user="owner",
                ssh_host="build-host",
                site="Canva\nssh -L 9222:10.1.2.3:9222",
            )
        vnc = self.rws.takeover_card(
            mode="vnc", port=5900, ssh_user="owner", ssh_host="build-host", site="飞书开放平台",
        )
        self.assertIn("飞书开放平台", vnc)
        self.assertIn("5900:127.0.0.1:5900", vnc)

    def test_listen_table_accepts_only_loopback(self):
        port = 9222
        loopback = "   0: 0100007F:2406 00000000:0000 0A 00000000:00000000 00:00000000 00000000 0 0 1 1 0 0 0 0 0\n"
        wildcard = "   0: 00000000:2406 00000000:0000 0A 00000000:00000000 00:00000000 00000000 0 0 1 1 0 0 0 0 0\n"
        self.assertTrue(self.rws.loopback_only(loopback, "", port))
        self.assertFalse(self.rws.loopback_only(wildcard, "", port))
        self.assertFalse(self.rws.loopback_only("", "", port))
        v6_loop = "   0: 00000000000000000000000001000000:2406 00000000000000000000000000000000:0000 0A 00000000:00000000 00:00000000 00000000 0 0 1 1 0 0 0 0 0\n"
        v6_any = "   0: 00000000000000000000000000000000:2406 00000000000000000000000000000000:0000 0A 00000000:00000000 00:00000000 00000000 0 0 1 1 0 0 0 0 0\n"
        self.assertTrue(self.rws.loopback_only("", v6_loop, port))
        self.assertFalse(self.rws.loopback_only(loopback, v6_any, port))


class CliTest(unittest.TestCase):
    def test_plan_prints_json_without_secrets_and_delete_removes_the_profile(self):
        self.assertTrue(SCRIPT.is_file(), "remote_web_session.py is required")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "profiles"
            root.mkdir(mode=0o700)
            plan = subprocess.run(
                [
                    sys.executable, str(SCRIPT), "plan",
                    "--mode", "cdp",
                    "--batch", "canva-1",
                    "--port", "9222",
                    "--root", str(root),
                    "--chrome", "/usr/bin/google-chrome",
                    "--ssh-user", "owner",
                    "--ssh-host", "build-host",
                    "--site", "Canva token=should-not-appear",
                ],
                check=False, capture_output=True, text=True,
            )
            self.assertEqual(plan.returncode, 0, plan.stderr)
            self.assertNotIn("should-not-appear", plan.stdout)
            payload = json.loads(plan.stdout)
            self.assertEqual(payload["bind"], "127.0.0.1")
            self.assertEqual(payload["endpoint"], "http://127.0.0.1:9222")
            self.assertIn("--remote-debugging-address=127.0.0.1", payload["argv"])
            self.assertNotIn("--no-sandbox", payload["argv"])
            profile = Path(payload["profile"])
            self.assertTrue(profile.is_dir())
            self.assertEqual(stat.S_IMODE(profile.stat().st_mode), 0o700)
            removed = subprocess.run(
                [sys.executable, str(SCRIPT), "delete", "--root", str(root), "--batch", "canva-1"],
                check=False, capture_output=True, text=True,
            )
            self.assertEqual(removed.returncode, 0, removed.stderr)
            self.assertFalse(profile.exists())

    def test_help_and_a_widened_bind_fail_closed(self):
        help_run = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"],
            check=False, capture_output=True, text=True,
        )
        self.assertEqual(help_run.returncode, 0)
        self.assertIn("usage:", help_run.stdout.lower())
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "profiles"
            root.mkdir(mode=0o700)
            refused = subprocess.run(
                [
                    sys.executable, str(SCRIPT), "plan",
                    "--mode", "cdp", "--batch", "b", "--port", "9222",
                    "--root", str(root), "--chrome", "/usr/bin/google-chrome",
                    "--ssh-user", "owner", "--ssh-host", "build-host",
                    "--site", "feishu", "--bind", "0.0.0.0",
                ],
                check=False, capture_output=True, text=True,
            )
            self.assertNotEqual(refused.returncode, 0)
            self.assertFalse((root / "b").exists())


class SkillDocTest(unittest.TestCase):
    def test_the_skill_teaches_the_general_takeover_flow(self):
        text = DOC.read_text(encoding="utf-8")
        for fact in DOC_FACTS:
            with self.subTest(fact=fact):
                self.assertIn(fact, text)
        self.assertNotIn("buzz-agent-setup", text)

    def test_entrypoint_references_resolve_after_relocation(self):
        text = DOC.read_text(encoding='utf-8')
        self.assertRegex(text, r'(?m)^name: web-access$')
        for target in re.findall(r'\]\(([^)]+)\)', text):
            if not target.startswith(('http:', 'https:')):
                self.assertTrue((DOC.parent / target).resolve().is_file(), target)

    def test_local_browser_and_remote_takeover_share_one_entrypoint(self):
        text = DOC.read_text(encoding='utf-8')
        self.assertIn('本机优先使用浏览器协议或任务内浏览器控制，避免抢占系统鼠标', text)
        self.assertIn('浏览器必须跑在远端时按用户授权和可用接管工具选择方式，不默认安装 VNC/noVNC', text)
        self.assertNotIn('浏览器必须跑在远端时默认 VNC/noVNC', text)
        self.assertIn('本任务浏览器标签置前（bringToFront）', text)
        self.assertIn('用户操作期间不点击、填充或导航该页面', text)
        self.assertIn('不读取秘密输入框的有界状态检查', text)
        self.assertIn('未知网站也能建立浏览器会话和人工接管', text)
        reference = (SKILL / 'references/browser-handoff.md').read_text()
        self.assertIn('用户明确选择', reference)
        self.assertIn('不默认安装 VNC/noVNC', reference)
        self.assertNotIn('普通用户默认 VNC/noVNC', reference)
        self.assertIn('不打印 cookie', reference)
        self.assertIn('不打印 token', reference)
        self.assertIn('其他本地用户', reference)
        self.assertIn('save 保留私有 profile', reference)


class ChromeLoopbackTest(unittest.TestCase):
    def test_real_chrome_debug_port_is_reachable_only_on_loopback(self):
        chrome = Path("/usr/bin/google-chrome")
        if not chrome.is_file():
            self.skipTest("google-chrome is not installed")
        rws = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "profiles"
            root.mkdir(mode=0o700)
            profile = rws.allocate_profile(root, "chrome-probe")
            sock = socket.socket()
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
            sock.close()
            argv = rws.chrome_argv(str(chrome), profile, port)
            err_path = Path(tmp) / "chrome.err"
            err_fh = err_path.open("wb")
            proc = subprocess.Popen(
                argv, stdout=subprocess.DEVNULL, stderr=err_fh, start_new_session=True,
            )
            try:
                self._wait_until_listening(port, err_path)
                tcp = Path("/proc/net/tcp").read_text(encoding="utf-8", errors="replace")
                tcp6 = Path("/proc/net/tcp6").read_text(encoding="utf-8", errors="replace") if Path("/proc/net/tcp6").is_file() else ""
                self.assertTrue(rws.loopback_only(tcp, tcp6, port), "chrome listened outside loopback")
                with socket.create_connection(("127.0.0.1", port), 2) as client:
                    # Chrome 151 closes an HTTP/1.0 request without a body, and keeps the
                    # HTTP/1.1 socket open after the JSON, so read until the payload arrives.
                    client.settimeout(3)
                    client.sendall(
                        f"GET /json/version HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n".encode()
                    )
                    chunks = []
                    try:
                        while b"Browser" not in b"".join(chunks):
                            part = client.recv(4096)
                            if not part:
                                break
                            chunks.append(part)
                    except TimeoutError:
                        pass
                    body = b"".join(chunks)
                self.assertIn(b"Browser", body)
                self.assertNotIn(b"cookie", body.lower())
            finally:
                err_fh.close()
                self._stop(proc)
                rws.delete_profile(root, "chrome-probe")
                self.assertFalse(profile.exists())

    def _wait_until_listening(self, port: int, err_path: Path):
        deadline = __import__("time").time() + 15
        while __import__("time").time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), 0.2):
                    return
            except OSError:
                pass
            __import__("time").sleep(0.1)
        tail = err_path.read_text(encoding="utf-8", errors="replace")[-500:]
        tail = re.sub(r"(?i)ws://\S+", "[redacted]", tail)
        self.fail(f"chrome debug port {port} did not open; tail={tail!r}")

    def _stop(self, proc: subprocess.Popen):
        if proc.poll() is not None:
            return
        try:
            os.killpg(proc.pid, 15)
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, 9)
            proc.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
