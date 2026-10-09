"""Prove ssh -L between two containers.

Container "webhost" listens on 127.0.0.1 only, standing in for Chrome's debug
port or x11vnc. Container "laptop" shares a Docker network with it. A direct
TCP connection to webhost:port is refused. The same port through
ssh -L port:127.0.0.1:port answers. The forward arguments come from the skill.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path


SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL / "scripts"))

IMAGE = "web-access-tunnel:test"
PROBE_PORT = 9222


def docker_ready() -> bool:
    if os.environ.get("WEB_ACCESS_SKIP_DOCKER") == "1":
        return False
    if shutil.which("docker") is None:
        return False
    probe = subprocess.run(["docker", "info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return probe.returncode == 0


@unittest.skipUnless(docker_ready(), "docker is not available")
class SshTunnelDockerTest(unittest.TestCase):
    def test_laptop_reaches_loopback_service_only_through_ssh(self):
        import remote_web_session as rws

        forward = rws.ssh_forward_args(
            PROBE_PORT, "tunnel", "webhost",
            extra_options=(
                "-i", "/tmp/key",
                "-o", "IdentitiesOnly=yes",
                "-o", "StrictHostKeyChecking=no",
                "-o", "UserKnownHostsFile=/dev/null",
            ),
        )
        self.assertIn(f"127.0.0.1:{PROBE_PORT}:127.0.0.1:{PROBE_PORT}", forward)
        self.assertIn("GatewayPorts=no", forward)
        self.assertNotIn("0.0.0.0", " ".join(forward))

        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            self._write_fixtures(work)
            self._build(work)
            network = f"rws-{os.getpid()}"
            host_name = f"rws-host-{os.getpid()}"
            subprocess.run(["docker", "network", "create", network], check=True, capture_output=True)
            try:
                host = subprocess.run(
                    [
                        "docker", "run", "-d", "--name", host_name, "--network", network,
                        "--network-alias", "webhost",
                        "-v", f"{work / 'authorized_keys'}:/mnt/authorized_keys:ro",
                        "-v", f"{work / 'probe.py'}:/mnt/probe.py:ro",
                        "-v", f"{work / 'entry.sh'}:/entry.sh:ro",
                        IMAGE, "bash", "/entry.sh",
                    ],
                    check=True, capture_output=True, text=True,
                )
                self._wait_log(host_name, "probe-ready")
                self._wait_loopback_probe(host_name)
                direct = self._run_on_laptop(network, work, textwrap.dedent(f"""
                    import socket
                    try:
                        socket.create_connection(("webhost", {PROBE_PORT}), 2).close()
                    except OSError as exc:
                        print("direct-refused")
                    else:
                        print("direct-open")
                """))
                self.assertIn("direct-refused", direct.stdout, direct.stderr)
                self.assertNotIn("direct-open", direct.stdout)
                tunneled = self._run_on_laptop(network, work, self._tunnel_script(forward))
                self.assertIn("tunnel-ok", tunneled.stdout, tunneled.stderr)
                self.assertNotIn("cookie", tunneled.stdout.lower())
            finally:
                subprocess.run(["docker", "rm", "-f", host_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                subprocess.run(["docker", "network", "rm", network], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _tunnel_script(self, forward: list[str]) -> str:
        return textwrap.dedent(f"""
            import os, shutil, socket, subprocess, time
            shutil.copy("/mnt/key", "/tmp/key")
            os.chmod("/tmp/key", 0o600)
            proc = None
            deadline = time.time() + 20
            while time.time() < deadline:
                if proc is None or proc.poll() is not None:
                    proc = subprocess.Popen({forward!r}, stderr=subprocess.PIPE)
                    time.sleep(0.3)
                try:
                    client = socket.create_connection(("127.0.0.1", {PROBE_PORT}), 1)
                except OSError:
                    time.sleep(0.2)
                    continue
                client.sendall(b"GET / HTTP/1.0\\r\\nHost: 127.0.0.1\\r\\n\\r\\n")
                body = client.recv(1024)
                client.close()
                proc.terminate()
                if b"tunnel-probe" in body:
                    print("tunnel-ok")
                else:
                    print("tunnel-bad")
                break
            else:
                err = b""
                if proc is not None and proc.stderr is not None:
                    err = proc.stderr.read(400)
                if proc is not None:
                    proc.terminate()
                print("tunnel-timeout", err)
        """)

    def _run_on_laptop(self, network: str, work: Path, program: str):
        script = work / "laptop.py"
        script.write_text(program, encoding="utf-8")
        return subprocess.run(
            [
                "docker", "run", "--rm", "--network", network,
                "-v", f"{work / 'id_ed25519'}:/mnt/key:ro",
                "-v", f"{script}:/laptop.py:ro",
                IMAGE, "python3", "/laptop.py",
            ],
            check=False, capture_output=True, text=True, timeout=60,
        )

    def _write_fixtures(self, work: Path):
        key = work / "id_ed25519"
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
            check=True, capture_output=True,
        )
        key.chmod(0o600)
        (work / "authorized_keys").write_text((work / "id_ed25519.pub").read_text(encoding="utf-8"), encoding="utf-8")
        (work / "probe.py").write_text(textwrap.dedent(f"""
            from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
            class Handler(BaseHTTPRequestHandler):
                def do_GET(self):
                    body = b"tunnel-probe"
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                def log_message(self, fmt, *args):
                    return
            print("probe-ready", flush=True)
            ThreadingHTTPServer(("127.0.0.1", {PROBE_PORT}), Handler).serve_forever()
        """), encoding="utf-8")
        (work / "entry.sh").write_text(textwrap.dedent("""
            #!/bin/bash
            set -eu
            cp /mnt/authorized_keys /home/tunnel/.ssh/authorized_keys
            chown tunnel:tunnel /home/tunnel/.ssh/authorized_keys
            chmod 600 /home/tunnel/.ssh/authorized_keys
            ssh-keygen -A
            mkdir -p /run/sshd
            /usr/sbin/sshd
            exec python3 /mnt/probe.py
        """), encoding="utf-8")
        (work / "Dockerfile").write_text(textwrap.dedent("""
            FROM python:3.12-slim
            RUN apt-get update \\
             && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends openssh-server openssh-client \\
             && rm -rf /var/lib/apt/lists/* \\
             && useradd -m -s /bin/bash tunnel \\
             && mkdir -p /home/tunnel/.ssh /run/sshd \\
             && chown tunnel:tunnel /home/tunnel/.ssh \\
             && chmod 700 /home/tunnel/.ssh \\
             && printf 'PasswordAuthentication no\\nKbdInteractiveAuthentication no\\nPermitRootLogin no\\nAllowUsers tunnel\\nPubkeyAuthentication yes\\n' > /etc/ssh/sshd_config.d/tunnel.conf
        """), encoding="utf-8")

    def _build(self, work: Path):
        subprocess.run(
            ["docker", "build", "-t", IMAGE, str(work)],
            check=True, timeout=300,
        )

    def _wait_loopback_probe(self, name: str):
        deadline = time.time() + 20
        last = ""
        while time.time() < deadline:
            probe = subprocess.run(
                [
                    "docker", "exec", name, "python3", "-c",
                    "import urllib.request; "
                    "print(urllib.request.urlopen('http://127.0.0.1:9222', timeout=2).read().decode())",
                ],
                check=False, capture_output=True, text=True,
            )
            last = probe.stdout + probe.stderr
            if "tunnel-probe" in probe.stdout:
                return
            time.sleep(0.3)
        self.fail(f"{name} loopback probe did not answer: {last}")

    def _wait_log(self, name: str, needle: str):
        deadline = time.time() + 20
        while time.time() < deadline:
            logs = subprocess.run(
                ["docker", "logs", name], check=False, capture_output=True, text=True,
            )
            if needle in logs.stdout or needle in logs.stderr:
                return
            time.sleep(0.3)
        self.fail(f"{name} did not log {needle}")


if __name__ == "__main__":
    unittest.main()
