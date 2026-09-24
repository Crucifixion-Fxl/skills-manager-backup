"""Offline contracts for the local Buzz alignment auditor (skills#140 P1)."""

from __future__ import annotations

import importlib.util
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "audit_local_alignment.py"
COMPARE_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "compare_local_alignment_receipts.py"
)
SKILL_ROOT = SCRIPT.parent.parent
LAUNCHER = SKILL_ROOT / "references/scripts/run-agent.py"
GAP_TAXONOMY = SKILL_ROOT / "references/local-alignment-gap-taxonomy.md"
EXPECTED = "a" * 40
OLD = "b" * 40


def load_module():
    spec = importlib.util.spec_from_file_location("audit_local_alignment", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load audit_local_alignment")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_compare_module():
    spec = importlib.util.spec_from_file_location(
        "compare_local_alignment_receipts", COMPARE_SCRIPT
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load receipt comparator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_launcher_module():
    spec = importlib.util.spec_from_file_location("canonical_run_agent", LAUNCHER)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load canonical launcher")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LocalAlignmentFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name).resolve()
        self.agents = self.home / ".config/buzz/agents"
        self.units = self.home / ".config/systemd/user"
        self.release = (
            self.home / ".local/share/buzz-agent-setup/releases" / EXPECTED
        )
        self.workdir = self.home / "buzz-agent-work/demo-dev"
        for path in (self.agents, self.units, self.release / "scripts", self.workdir / ".claude"):
            path.mkdir(parents=True, exist_ok=True)
            self.trust_directories(path)
        (self.home / "buzz-agent-work").chmod(0o755)
        self.workdir.chmod(0o755)
        (self.workdir / ".git").mkdir()
        self._write_release()
        self._write_agent()
        self._write_background_units()
        self._write_plugins()

    def tearDown(self) -> None:
        for path in self.home.rglob("*"):
            try:
                path.chmod(path.stat().st_mode | stat.S_IWUSR)
            except OSError:
                pass
        self.temp.cleanup()

    def write(self, path: Path, text: str, mode: int = 0o600) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.trust_directories(path.parent)
        path.write_text(text, encoding="utf-8")
        path.chmod(mode)
        claude_root = self.home / ".claude-buzz"
        if claude_root in path.parents:
            claude_root.chmod(0o700)

    def trust_directories(self, path: Path) -> None:
        current = path
        while current != self.home:
            if current.exists():
                current.chmod(0o755)
            current = current.parent

    def _write_release(self) -> None:
        scripts = {
            "gitlab_buzz_sync.py": "def load_config(path): return {}\ndef validate_config(value): return None\n",
            "gitlab_buzz_route_reply.py": "def load_config(path, mode='scan'): return {'channels': {'x'}}\n",
            "gitlab_buzz_desk_runner.py": "def load_manifest(path): return {}\ndef inventory(value): return []\n",
            "gitlab_buzz_sync_timer.py": "# fixture\n",
            "buzz_feishu_group_sync.py": "# fixture\n",
            "gitlab_todo_sync.py": "# fixture\n",
            "buzz_send_with_responsible_mentions.py": "# fixture\n",
            "buzz_agent_join_requests.py": "# fixture\n",
            "buzz_acp_media_proxy.py": "# media proxy fixture\n",
        }
        for name, body in scripts.items():
            self.write(self.release / "scripts" / name, body, 0o444)
        self.write(
            self.release / "references/scripts/run-agent.py",
            LAUNCHER.read_text(encoding="utf-8"),
            0o444,
        )
        self.write(
            self.release / "references/agent-prompt-contract.md",
            (SKILL_ROOT / "references/agent-prompt-contract.md").read_text(
                encoding="utf-8"
            ),
            0o444,
        )
        self.write(
            self.release / "SKILL.md",
            "---\nname: buzz-agent-setup\n---\n",
            0o444,
        )
        records = {}
        for path in sorted(self.release.rglob("*")):
            if path.is_file():
                records[path.relative_to(self.release).as_posix()] = {
                    "mode": stat.S_IMODE(path.stat().st_mode),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
        self.write(
            self.release / ".release-manifest.json",
            json.dumps({"version": 1, "commit": EXPECTED, "files": records}),
            0o444,
        )
        for path in self.release.rglob("*"):
            if path.is_dir():
                path.chmod(0o555)
        self.release.chmod(0o555)

    def _write_agent(self) -> None:
        responsible = self.agents / "demo/responsible/demo-dev.json"
        people = self.agents / "people.json"
        self.write(
            people,
            json.dumps({"owner": "1" * 64}),
        )
        cli = self.home / ".local/opt/buzz-0.5.23/usr/bin/buzz"
        cli_body = b"\x7fELFfixture-buzz"
        cli.parent.mkdir(parents=True, exist_ok=True)
        self.trust_directories(cli.parent)
        cli.write_bytes(cli_body)
        cli.chmod(0o555)
        self.cli_digest = hashlib.sha256(cli_body).hexdigest()
        state_dir = self.home / ".local/state/buzz/demo-dev"
        state_dir.mkdir(parents=True)
        self.trust_directories(state_dir.parent)
        state_dir.chmod(0o700)
        adapter = self.home / ".local/lib/buzz-agents/claude-agent-acp"
        self.write(adapter, "#!/bin/sh\nexit 0\n", 0o555)
        claude = self.home / ".local/bin/claude-buzz"
        install = self.home / ".claude-buzz/plugins/cache/addx/addx/revision"
        effective_plugins = json.dumps(
            [
                {
                    "id": "addx@addx",
                    "enabled": True,
                    "installPath": str(install),
                }
            ]
        )
        self.write(
            claude,
            "#!/bin/sh\nprintf '%s\\n' " + repr(effective_plugins) + "\n",
            0o555,
        )
        (self.home / ".local/bin/buzz-acp").symlink_to("/usr/bin/true")
        (self.home / ".local/bin").chmod(0o755)
        buzz_acp = Path("/usr/bin/true")
        buzz_acp_digest = hashlib.sha256(buzz_acp.read_bytes()).hexdigest()
        proxy_source = self.release / "scripts/buzz_acp_media_proxy.py"
        proxy_digest = hashlib.sha256(proxy_source.read_bytes()).hexdigest()
        proxy_dir = (
            self.home / ".local/share/buzz-agent-setup/acp-media-proxy" / proxy_digest
        )
        proxy = proxy_dir / adapter.name
        self.write(proxy, proxy_source.read_text(encoding="utf-8"), 0o555)
        proxy_dir.chmod(0o700)
        self.write(
            responsible,
            json.dumps(
                {
                    "version": 2,
                    "sender_pubkey": "2" * 64,
                    "state_dir": str(state_dir),
                    "gitlab": {
                        "base_url": "https://gitlab.example.test",
                        "token_env": "GITLAB_TOKEN",
                        "projects": [1],
                    },
                    "channels": ["00000000-0000-4000-8000-000000000001"],
                    "people_file": str(people),
                    "buzz": {
                        "cli_path": str(cli),
                        "cli_sha256": self.cli_digest,
                    },
                }
            ),
        )
        contract = load_module().load_prompt_contract()
        prompt = "\n".join(
            marker.replace("<RELEASE>", str(self.release))
            for marker in [
                *contract["common"]["required"],
                *contract["dev"]["required"],
            ]
        )
        self.write(self.agents / "demo-dev.prompt.md", prompt)
        self.write(
            self.agents / "demo-dev.env",
            "\n".join(
                [
                    "BUZZ_RELAY_URL=wss://relay.example.test",
                    "BUZZ_PRIVATE_KEY='fixture-secret-never-print'",
                    "BUZZ_ACP_AGENT_OWNER=owner",
                    "BUZZ_ACP_CHANNELS=00000000-0000-4000-8000-000000000001",
                    f"BUZZ_ACP_BINARY={buzz_acp}",
                    f"BUZZ_ACP_BINARY_SHA256={buzz_acp_digest}",
                    f"BUZZ_ACP_AGENT_COMMAND={proxy}",
                    f"BUZZ_ACP_MEDIA_ADAPTER_COMMAND={adapter}",
                    f"BUZZ_ACP_MEDIA_BUZZ_CLI={cli}",
                    f"CLAUDE_CODE_EXECUTABLE={claude}",
                    f"HARNESS_CLAUDE_WRAPPER={claude}",
                    f"CLAUDE_CONFIG_DIR={self.home / '.claude-buzz'}",
                    f"AGENT_WORKDIR={self.workdir}",
                    f"BUZZ_AGENT_SAFE_PATH={self.home / '.local/bin'}:/usr/bin:/bin",
                    f"BUZZ_ACP_SYSTEM_PROMPT_FILE={self.agents / 'demo-dev.prompt.md'}",
                    f"BUZZ_RESPONSIBLE_CONFIG={responsible}",
                ]
            )
            + "\n",
        )
        self.write(
            self.agents / "run-agent.py",
            (SKILL_ROOT / "references/scripts/run-agent.py").read_text(
                encoding="utf-8"
            ),
            0o500,
        )
        self.write(
            self.agents / "local-alignment-roles.json",
            json.dumps({"version": 1, "roles": {"demo-dev": "dev"}}),
        )
        self.write(
            self.units / "buzz-local-demo-dev.service",
            "[Service]\nType=exec\nUMask=0077\nNoNewPrivileges=yes\n"
            "UnsetEnvironment=LD_PRELOAD LD_AUDIT LD_LIBRARY_PATH PYTHONHOME PYTHONPATH PYTHONINSPECT PYTHONSTARTUP BASH_ENV ENV NODE_OPTIONS PERL5OPT RUBYOPT GLIBC_TUNABLES GCONV_PATH LOCPATH NLSPATH MALLOC_TRACE RES_OPTIONS HOSTALIASES TZDIR\n"
            "ExecStart=/usr/bin/python3 -I %h/.config/buzz/agents/run-agent.py demo-dev\n"
            "Restart=on-failure\nRestartSec=5s\n"
            "StandardOutput=append:%h/.config/buzz/agents/demo-dev.log\n"
            "StandardError=append:%h/.config/buzz/agents/demo-dev.log\n"
            "[Install]\nWantedBy=default.target\n",
            0o644,
        )
        settings = {
            "sandbox": {
                "enabled": True,
                "failIfUnavailable": True,
                "allowUnsandboxedCommands": False,
                "filesystem": {
                    "denyRead": ["~/"],
                    "allowRead": [
                        ".",
                        str(self.release),
                        str(responsible),
                        str(people),
                    ],
                    "allowWrite": [str(self.home / ".local/state/buzz/demo-dev")],
                },
            },
            "permissions": {
                "deny": [
                    "Read(~/.config/buzz/**)",
                    "Read(~/.bash_secrets)",
                    "Read(~/.claude/**)",
                    "Read(~/.claude-buzz/.credentials.json)",
                    "Read(~/.claude-buzz/projects/**)",
                    "Read(~/.config/glab-cli/**)",
                ]
            },
        }
        self.write(
            self.workdir / ".claude/settings.local.json", json.dumps(settings)
        )
        self.write(
            self.home / ".claude-buzz/settings.json",
            json.dumps(
                {
                    "enabledPlugins": {"addx@addx": True},
                    "sandbox": {
                        "allowUnsandboxedCommands": False,
                        "credentials": {"envVars": [{"name": "OWNER_TOKEN", "mode": "deny"}]},
                    }
                }
            ),
        )

    def _write_background_units(self) -> None:
        manifest = self.agents / "demo-gitlab-buzz/desk-runner.json"
        sync_config = self.agents / "demo-gitlab-buzz/sync.json"
        route_config = self.agents / "demo-gitlab-buzz/route.json"
        cli = self.home / ".local/opt/buzz-0.5.23/usr/bin/buzz"
        cli_pin = {
            "cli_path": str(cli),
            "cli_sha256": hashlib.sha256(cli.read_bytes()).hexdigest(),
        }
        channel = "00000000-0000-4000-8000-000000000001"
        sender = "2" * 64
        self.write(
            sync_config,
            json.dumps(
                {
                    "channel_id": channel,
                    "publisher_pubkey": sender,
                    "since": "2026-09-13T00:00:00Z",
                    "people": {},
                    "gitlab": {
                        "base_url": "https://gitlab.example.test",
                        "token_env": "GITLAB_TOKEN",
                        "bot_user_id": 7,
                        "bot_username": "project_1_bot_x",
                        "projects": [1],
                    },
                    "buzz": cli_pin,
                }
            ),
        )
        self.write(
            route_config,
            json.dumps(
                {
                    "scan_since": "2026-09-13T00:00:00Z",
                    "sender_pubkey": sender,
                    "channels": {
                        channel: {
                            "publisher_pubkey": sender,
                            "canvas_admin_pubkeys": ["4" * 64],
                            "roles": {
                                "feature": {
                                    "mention": "@feature-agent",
                                    "mention_pubkey": "5" * 64,
                                }
                            },
                        }
                    },
                    "buzz": cli_pin,
                }
            ),
        )
        self.write(
            manifest,
            json.dumps(
                {
                    "version": 1,
                    "release_dir": str(self.release),
                    "sync": [
                        {
                            "config": str(sync_config),
                            "state_dir": str(self.home / ".local/state/buzz/sync"),
                        }
                    ],
                    "route": {
                        "config": str(route_config),
                        "state_dir": str(self.home / ".local/state/buzz/route"),
                    },
                }
            ),
        )
        # Add the manifest to the desk env used by the deterministic launcher.
        with (self.agents / "demo-dev.env").open("a", encoding="utf-8") as handle:
            handle.write(f"BUZZ_DESK_RUNNER_MANIFEST={manifest}\n")
        self.write(
            self.units / "gitlab-buzz-sync-demo.service",
            "[Unit]\nAfter=network-online.target\n"
            "[Service]\nType=oneshot\nUMask=0077\nNoNewPrivileges=yes\n"
            "TimeoutStartSec=20min\n"
            "Environment=BUZZ_SYNC_ENV_FILE=%h/.config/buzz/agents/demo-dev.env\n"
            f"ExecStart=%h/.config/buzz/sync/gitlab-buzz-sync-launch.sh /usr/bin/python3 {self.release}/scripts/gitlab_buzz_sync_timer.py\n",
            0o644,
        )
        sync_reference = (SKILL_ROOT / "references/systemd/README.md").read_text(
            encoding="utf-8"
        )
        sync_launcher = re.search(
            r"<!-- template:sync-timer-launcher -->\s*```bash\n(.*?)\n```",
            sync_reference,
            re.S,
        )
        assert sync_launcher is not None
        self.write(
            self.home / ".config/buzz/sync/gitlab-buzz-sync-launch.sh",
            sync_launcher.group(1) + "\n",
            0o700,
        )
        self.write(
            self.units / "gitlab-buzz-sync-demo.timer",
            "[Timer]\nOnActiveSec=1min\nOnBootSec=2min\n"
            "OnUnitActiveSec=300\nPersistent=true\n"
            "Unit=gitlab-buzz-sync-demo.service\n"
            "[Install]\nWantedBy=timers.target\n",
            0o644,
        )
        self.write(
            self.units / "buzz-feishu-demo.service",
            "[Unit]\nAfter=network-online.target\n"
            "[Service]\nType=oneshot\nUMask=0077\nNoNewPrivileges=yes\n"
            "TimeoutStartSec=10min\nEnvironment=PATH=/usr/local/bin:/usr/bin:/bin\n"
            f"ExecStart=/usr/bin/python3 {self.release}/scripts/buzz_feishu_group_sync.py round --config /fixed/config --state-dir /fixed/state\n"
            "SuccessExitStatus=3\nNice=5\n",
            0o644,
        )
        self.write(
            self.units / "buzz-feishu-demo.timer",
            "[Timer]\nOnActiveSec=1min\nOnBootSec=2min\n"
            "OnUnitActiveSec=1min\nAccuracySec=15s\n"
            "Unit=buzz-feishu-demo.service\n"
            "[Install]\nWantedBy=timers.target\n",
            0o644,
        )
        todo_reference = (
            SKILL_ROOT / "references/systemd/personal-todo-sync.md"
        ).read_text(encoding="utf-8")
        todo_launcher = re.search(
            r"<!-- template:todo-timer-launcher -->\s*```bash\n(.*?)\n```",
            todo_reference,
            re.S,
        )
        assert todo_launcher is not None
        self.write(
            self.home / ".config/buzz/todo/gitlab-todo-sync-launch.sh",
            todo_launcher.group(1) + "\n",
            0o700,
        )
        self.write(self.home / ".config/buzz/todo/owner.env", "FIXTURE=1\n", 0o600)
        self.write(
            self.units / "gitlab-todo-sync-owner.service",
            "[Unit]\nAfter=network-online.target\n"
            "[Service]\nType=oneshot\nUMask=0077\nNoNewPrivileges=yes\n"
            "TimeoutStartSec=5min\n"
            "Environment=BUZZ_TODO_ENV_FILE=%h/.config/buzz/todo/owner.env\n"
            f"ExecStart=%h/.config/buzz/todo/gitlab-todo-sync-launch.sh /usr/bin/python3 {self.release}/scripts/gitlab_todo_sync.py\n",
            0o644,
        )
        self.write(
            self.units / "gitlab-todo-sync-owner.timer",
            "[Timer]\nOnActiveSec=1min\nOnBootSec=2min\n"
            "OnUnitActiveSec=600\nPersistent=true\n"
            "Unit=gitlab-todo-sync-owner.service\n"
            "[Install]\nWantedBy=timers.target\n",
            0o644,
        )
        self.write(
            self.home / ".config/buzz/join/config.json",
            json.dumps(
                {
                    "version": 1,
                    "owner_pubkey": "1" * 64,
                    "buzz": {"cli_path": str(cli), "cli_sha256": self.cli_digest},
                    "state_dir": str(self.home / ".local/state/buzz/join"),
                    "agents": [
                        {
                            "name": "demo-dev",
                            "env_file": str(self.agents / "demo-dev.env"),
                            "unit": "buzz-local-demo-dev.service",
                            "log_file": str(self.agents / "demo-dev.log"),
                            "capabilities": {"summary": "demo", "repos": []},
                        }
                    ],
                }
            ) + "\n",
            0o600,
        )
        self.write(
            self.units / "buzz-agent-join.service",
            "[Service]\nType=oneshot\nUMask=0077\nNoNewPrivileges=yes\n"
            "TimeoutStartSec=10min\n"
            "Environment=BUZZ_JOIN_CONFIG=%h/.config/buzz/join/config.json\n"
            f"ExecStart=/usr/bin/python3 {self.release}/scripts/buzz_agent_join_requests.py\n",
            0o644,
        )
        self.write(
            self.units / "buzz-agent-join.timer",
            "[Timer]\nOnActiveSec=1min\nOnBootSec=2min\n"
            "OnUnitActiveSec=120\nPersistent=true\n"
            "Unit=buzz-agent-join.service\n"
            "[Install]\nWantedBy=timers.target\n",
            0o644,
        )

    def _write_plugins(self) -> None:
        install = self.home / ".claude-buzz/plugins/cache/addx/addx/revision"
        manifest = json.loads(
            (self.release / ".release-manifest.json").read_text(encoding="utf-8")
        )
        skill_root = install / "skills/buzz-agent-setup"
        for relative, record in manifest["files"].items():
            source = self.release / relative
            target = skill_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            self.trust_directories(target.parent)
            target.write_bytes(source.read_bytes())
            target.chmod(0o755 if record["mode"] & 0o111 else 0o644)
        self.write(
            self.home / ".claude-buzz/plugins/installed_plugins.json",
            json.dumps(
                {
                    "version": 2,
                    "plugins": {
                        "addx@addx": [
                            {
                                "installPath": str(install),
                                "gitCommitSha": EXPECTED,
                                "version": "revision",
                                "scope": "user",
                            }
                        ]
                    },
                }
            ),
            0o644,
        )
        (self.home / ".claude-buzz").chmod(0o700)


class LocalAlignmentAuditTest(LocalAlignmentFixture):
    def run_audit(self):
        module = load_module()
        return module.audit_home(self.home, EXPECTED)

    def test_group_writable_nonsticky_ancestor_is_always_untrusted(self) -> None:
        module = load_module()
        parent = self.home / "private-group-writable"
        child = parent / "child"
        child.mkdir(parents=True)
        parent.chmod(0o770)
        child.chmod(0o700)
        self.assertFalse(module.trusted_directory_chain(child, os.geteuid()))

    @unittest.skipUnless(Path("/proc").is_dir(), "requires Linux process state")
    def test_bounded_child_timeout_kills_the_whole_process_group(self) -> None:
        module = load_module()
        pid_file = self.home / "grandchild.pid"
        code = (
            "import subprocess,sys,time; from pathlib import Path; "
            "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
            "Path(sys.argv[1]).write_text(str(child.pid),encoding='ascii'); "
            "time.sleep(60)"
        )
        with self.assertRaises(subprocess.TimeoutExpired):
            module.run_bounded_bytes(
                [sys.executable, "-I", "-c", code, str(pid_file)],
                env={"PATH": "/usr/bin:/bin"},
                timeout=3,
            )
        grandchild = int(pid_file.read_text(encoding="ascii"))
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            stat_path = Path(f"/proc/{grandchild}/stat")
            if not stat_path.exists():
                break
            try:
                fields = stat_path.read_text(encoding="ascii").split()
            except FileNotFoundError:
                break
            if len(fields) > 2 and fields[2] == "Z":
                break
            time.sleep(0.05)
        else:
            self.fail("grandchild survived bounded process-group timeout")

    def test_good_fixture_covers_every_local_runtime_surface(self) -> None:
        module = load_module()
        report = module.audit_home(self.home, EXPECTED)
        self.assertTrue(report["ok"], report)
        self.assertEqual(
            {
                "agents": 1,
                "sync_services": 1,
                "feishu_services": 1,
                "todo_services": 1,
                "join_services": 1,
                "harnesses": 1,
                "buzz_cli_binaries": 1,
                "transient_services": 0,
                "lookup_only_units": 0,
            },
            report["inventory"],
        )
        self.assertEqual(["demo-dev"], report["inventory_ids"]["agents"])
        self.assertEqual(
            ["claude:agent:demo-dev"],
            report["inventory_ids"]["harnesses"],
        )
        self.assertEqual(
            ["buzz-cli"],
            report["inventory_ids"]["buzz_cli_binaries"],
        )
        auditor = module.Auditor(self.home, EXPECTED)
        auditor.cli_paths = {Path("/first/buzz"), Path("/second/buzz")}
        self.assertEqual(["buzz-cli"], auditor.public_cli_identities())
        self.assertEqual(
            ["buzz-local-demo-dev.service"],
            report["inventory_ids"]["agent_services"],
        )
        self.assertEqual(
            [
                "buzz-agent-join.timer",
                "buzz-feishu-demo.timer",
                "gitlab-buzz-sync-demo.timer",
                "gitlab-todo-sync-owner.timer",
            ],
            report["inventory_ids"]["persistent_timers"],
        )
        launcher = load_launcher_module()
        _workdir, binary, _argv, _environment, digest = launcher.prepare_launch(
            "demo-dev",
            home=self.home,
            uid=os.geteuid(),
            username="fixture-user",
        )
        descriptor = launcher._open_binary(binary, os.geteuid(), digest)
        os.close(descriptor)
        categories = {item["category"] for item in report["checks"]}
        self.assertTrue(
            {
                "agent_env",
                "agent_prompt",
                "responsible_config",
                "buzz_cli",
                "media_proxy",
                "sandbox",
                "sync_release",
                "feishu_release",
                "todo_release",
                "join_release",
                "plugin_revision",
                "shared_launcher",
            }.issubset(categories),
            categories,
        )
        self.assertEqual(
            {gap_id: "pass" for gap_id in module.GAP_IDS},
            {gap_id: value["status"] for gap_id, value in report["gaps"].items()},
        )
        self.assertTrue(
            any(
                item["category"] == "sandbox"
                and item["subject"] == "agent:demo-dev"
                and item["status"] == "pass"
                for item in report["checks"]
            ),
            report,
        )

    def test_a_business_agent_requires_the_join_service_by_default(self) -> None:
        """A shipped join flow must not disappear behind an absent optional timer."""
        (self.units / "buzz-agent-join.service").unlink()
        (self.units / "buzz-agent-join.timer").unlink()

        report = self.run_audit()

        self.assertFalse(report["ok"], report)
        self.assertTrue(
            any(
                item["category"] == "join_release"
                and item["status"] == "fail"
                and item["code"] == "required_service_not_deployed"
                for item in report["checks"]
            ),
            report,
        )

    def test_join_config_must_cover_every_applicable_business_agent(self) -> None:
        config = self.home / ".config/buzz/join/config.json"
        value = json.loads(config.read_text(encoding="utf-8"))
        value["agents"] = []
        config.write_text(json.dumps(value), encoding="utf-8")

        report = self.run_audit()

        self.assertFalse(report["ok"], report)
        self.assertTrue(any(item["category"] == "join_release"
                            and item["code"] == "agent_coverage_mismatch"
                            and item["status"] == "fail" for item in report["checks"]), report)

    def test_join_config_agent_paths_must_match_the_discovered_agent(self) -> None:
        config = self.home / ".config/buzz/join/config.json"
        value = json.loads(config.read_text(encoding="utf-8"))
        value["agents"][0]["env_file"] = str(self.agents / "somebody-else.env")
        value["agents"][0]["unit"] = "buzz-local-somebody-else.service"
        config.write_text(json.dumps(value), encoding="utf-8")

        report = self.run_audit()

        self.assertFalse(report["ok"], report)
        self.assertTrue(any(item["category"] == "join_release"
                            and item["code"] == "agent_binding_mismatch"
                            and item["status"] == "fail" for item in report["checks"]), report)

    def test_business_agent_requires_channels_for_default_on_join_management(self) -> None:
        env = self.agents / "demo-dev.env"
        env.write_text("\n".join(line for line in env.read_text(encoding="utf-8").splitlines()
                                  if not line.startswith("BUZZ_ACP_CHANNELS=")) + "\n", encoding="utf-8")

        report = self.run_audit()

        self.assertFalse(report["ok"], report)
        self.assertTrue(any(item["category"] == "agent_env" and item["code"] == "required_keys_missing"
                            and "BUZZ_ACP_CHANNELS" in item.get("detail", "") for item in report["checks"]), report)

    def test_platform_agent_needs_neither_channels_nor_join_config_coverage(self) -> None:
        roles = self.agents / "local-alignment-roles.json"
        value = json.loads(roles.read_text(encoding="utf-8"))
        value["roles"]["demo-dev"] = "platform-desk"
        roles.write_text(json.dumps(value), encoding="utf-8")
        contract = load_module().load_prompt_contract()
        prompt = "\n".join(marker.replace("<RELEASE>", str(self.release))
                           for marker in [*contract["common"]["required"],
                                          *contract["platform-desk"]["required"]])
        (self.agents / "demo-dev.prompt.md").write_text(prompt, encoding="utf-8")
        env = self.agents / "demo-dev.env"
        env.write_text("\n".join(line for line in env.read_text(encoding="utf-8").splitlines()
                                  if not line.startswith("BUZZ_ACP_CHANNELS=")) + "\n", encoding="utf-8")
        config = self.home / ".config/buzz/join/config.json"
        join_value = json.loads(config.read_text(encoding="utf-8"))
        join_value["agents"] = []
        config.write_text(json.dumps(join_value), encoding="utf-8")

        report = self.run_audit()

        self.assertTrue(report["ok"], report)

    def test_live_join_timer_must_be_enabled_and_active(self) -> None:
        module = load_module()
        auditor = module.Auditor(self.home, EXPECTED)
        auditor._verify_user_manager = True
        completed = subprocess.CompletedProcess([], 1, stdout=b"", stderr=b"")
        with mock.patch.object(module, "run_bounded_text", return_value=(completed, "disabled\n")):
            auditor.audit_join_timer_runtime()

        codes = {item["code"] for item in auditor.checks if item["status"] == "fail"}
        self.assertIn("timer_not_enabled", codes)
        self.assertIn("timer_not_active", codes)

    def test_receipt_comparison_rejects_same_count_identity_replacement(self) -> None:
        comparator = load_compare_module()
        before = self.run_audit()
        after = json.loads(json.dumps(before))
        comparator.compare(before, after, EXPECTED)
        after["inventory_ids"]["agents"] = ["replacement-dev"]
        after["inventory_ids"]["agent_services"] = [
            "buzz-local-replacement-dev.service"
        ]
        with self.assertRaises(ValueError):
            comparator.compare(before, after, EXPECTED)
        after = json.loads(json.dumps(before))
        after["inventory_ids"]["harnesses"] = ["codex:agent:demo-dev"]
        with self.assertRaises(ValueError):
            comparator.compare(before, after, EXPECTED)

    def test_receipt_comparison_rejects_internally_forged_receipts(self) -> None:
        comparator = load_compare_module()
        before = self.run_audit()
        mutations = {
            "missing checks": lambda value: value.pop("checks"),
            "forged summary": lambda value: value["summary"].update(
                {"pass": 1, "fail": 0, "unknown": 0, "not_applicable": 0}
            ),
            "empty inventory": lambda value: value["inventory"].update(
                {key: 0 for key in value["inventory"]}
            ),
            "forged gaps": lambda value: value["gaps"]["LA-01"].update(
                {"status": "pass", "pass": 1, "fail": 0, "unknown": 0, "not_applicable": 0}
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                after = json.loads(json.dumps(before))
                mutate(after)
                with self.assertRaises(ValueError):
                    comparator.compare(before, after, EXPECTED)

    def test_release_manifest_rejects_content_drift_and_extra_files(self) -> None:
        script = self.release / "scripts/buzz_feishu_group_sync.py"
        original = script.read_text(encoding="utf-8")
        script.chmod(0o644)
        script.write_text(original + "# drift\n", encoding="utf-8")
        script.chmod(0o444)
        report = self.run_audit()
        self.assertTrue(
            any(item["code"] == "release_manifest_invalid" for item in report["checks"]),
            report,
        )
        script.chmod(0o644)
        script.write_text(original, encoding="utf-8")
        script.chmod(0o444)
        extra = self.release / "scripts/untracked.py"
        extra.parent.chmod(0o755)
        self.write(extra, "# extra\n", 0o444)
        extra.parent.chmod(0o555)
        report = self.run_audit()
        self.assertTrue(
            any(item["code"] == "release_manifest_invalid" for item in report["checks"]),
            report,
        )

    def test_enabled_plugin_requires_the_complete_skill_tree(self) -> None:
        installed = (
            self.home
            / ".claude-buzz/plugins/cache/addx/addx/revision"
            / "skills/buzz-agent-setup/scripts/gitlab_buzz_sync.py"
        )
        original = installed.read_text(encoding="utf-8")
        installed.write_text(
            original + "# stale indirect dependency\n",
            encoding="utf-8",
        )
        report = self.run_audit()
        self.assertTrue(
            any(
                item["category"] == "plugin_revision"
                and item["code"] == "installed_skill_tree_mismatch"
                for item in report["checks"]
            ),
            report,
        )

        installed.write_text(original, encoding="utf-8")
        installed.unlink()
        report = self.run_audit()
        self.assertTrue(
            any(
                item["category"] == "plugin_revision"
                and item["code"] == "installed_skill_tree_mismatch"
                for item in report["checks"]
            ),
            report,
        )

    def test_release_manifest_is_required_even_without_deployed_units(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            empty_home = Path(raw).resolve()
            (empty_home / ".config/systemd/user").mkdir(parents=True)
            report = load_module().audit_home(empty_home, EXPECTED)
        self.assertTrue(
            any(
                item["category"] == "release_integrity"
                and item["code"] == "release_manifest_invalid"
                for item in report["checks"]
            ),
            report,
        )
        self.assertEqual("fail", report["gaps"]["LA-06"]["status"])

    def test_each_pin_must_converge_on_the_expected_full_sha(self) -> None:
        targets = (
            (self.units / "buzz-feishu-demo.service", "feishu_release"),
            (self.units / "gitlab-todo-sync-owner.service", "todo_release"),
            (self.units / "buzz-agent-join.service", "join_release"),
            (self.agents / "demo-dev.prompt.md", "agent_prompt"),
            (self.agents / "demo-gitlab-buzz/desk-runner.json", "sync_release"),
            (
                self.home / ".claude-buzz/plugins/installed_plugins.json",
                "plugin_revision",
            ),
        )
        for path, category in targets:
            with self.subTest(path=path.name, category=category):
                original = path.read_text(encoding="utf-8")
                path.write_text(original.replace(EXPECTED, OLD), encoding="utf-8")
                report = self.run_audit()
                failures = [
                    item for item in report["checks"] if item["status"] == "fail"
                ]
                self.assertFalse(report["ok"])
                self.assertTrue(
                    any(item["category"] == category for item in failures),
                    failures,
                )
                path.write_text(original, encoding="utf-8")

    def test_historical_twelve_gap_taxonomy_has_red_fixtures(self) -> None:
        taxonomy = GAP_TAXONOMY.read_text(encoding="utf-8")
        self.assertEqual(
            [f"LA-{index:02d}" for index in range(1, 13)],
            re.findall(r"^\| (LA-\d{2}) \|", taxonomy, flags=re.MULTILINE),
        )
        prompt = self.agents / "demo-dev.prompt.md"
        env = self.agents / "demo-dev.env"
        responsible = self.agents / "demo/responsible/demo-dev.json"
        sandbox = self.workdir / ".claude/settings.local.json"
        manifest = self.agents / "demo-gitlab-buzz/desk-runner.json"
        plugin = self.home / ".claude-buzz/plugins/installed_plugins.json"
        agent_unit = self.units / "buzz-local-demo-dev.service"
        targets = {
            "LA-01": (agent_unit, "agent_unit", lambda text: text.replace("Type=exec", "Type=simple")),
            "LA-02": (env, "agent_env", lambda text: re.sub(r"^BUZZ_ACP_BINARY=.*\n", "", text, flags=re.MULTILINE)),
            "LA-03": (prompt, "agent_prompt", lambda text: text.replace(text.splitlines()[0], "REMOVED", 1)),
            "LA-04": (responsible, "responsible_config", lambda text: text.replace('"version": 2', '"version": 1')),
            "LA-05": (sandbox, "sandbox", lambda text: text.replace('"enabled": true', '"enabled": false', 1)),
            "LA-06": (prompt, "agent_prompt", lambda text: text.replace(EXPECTED, OLD)),
            "LA-07": (manifest, "sync_release", lambda text: text.replace(EXPECTED, OLD)),
            "LA-08": (self.units / "buzz-feishu-demo.service", "feishu_release", lambda text: text.replace(EXPECTED, OLD)),
            "LA-09": (self.units / "gitlab-todo-sync-owner.service", "todo_release", lambda text: text.replace(EXPECTED, OLD)),
            "LA-10": (self.units / "buzz-agent-join.service", "join_release", lambda text: text.replace(EXPECTED, OLD)),
            "LA-11": (env, "agent_env", lambda text: re.sub(r"^BUZZ_ACP_BINARY_SHA256=.*$", "BUZZ_ACP_BINARY_SHA256=" + "0" * 64, text, flags=re.MULTILINE)),
            "LA-12": (plugin, "plugin_revision", lambda text: text.replace(EXPECTED, OLD)),
        }
        for gap, (path, category, mutate) in targets.items():
            with self.subTest(gap=gap):
                original = path.read_text(encoding="utf-8")
                path.chmod(0o600 if path.suffix != ".service" else 0o644)
                path.write_text(mutate(original), encoding="utf-8")
                report = self.run_audit()
                self.assertFalse(report["ok"])
                self.assertTrue(
                    any(
                        item["category"] == category and item["status"] == "fail"
                        for item in report["checks"]
                    ),
                    report,
                )
                self.assertEqual("fail", report["gaps"][gap]["status"], report)
                path.write_text(original, encoding="utf-8")

    def test_agent_unit_cannot_wrap_or_append_the_expected_launcher(self) -> None:
        unit = self.units / "buzz-local-demo-dev.service"
        unit.write_text(
            "[Service]\nExecStart=/bin/sh -c %h/.config/buzz/agents/run-demo-dev.sh\n",
            encoding="utf-8",
        )
        report = self.run_audit()
        self.assertFalse(report["ok"])
        self.assertTrue(
            any(
                item["category"] == "agent_unit"
                and item["code"] == "launcher_mismatch"
                for item in report["checks"]
            ),
            report,
        )

    def test_direct_units_bind_the_actual_argv_to_the_expected_release(self) -> None:
        service = self.units / "buzz-feishu-demo.service"
        service.write_text(
            "[Service]\nType=oneshot\nUMask=0077\nNoNewPrivileges=yes\n"
            "TimeoutStartSec=10min\nEnvironment=PATH=/usr/bin\n"
            f"ExecStart=/bin/true /tmp/scripts/buzz_feishu_group_sync.py round --config {self.release}/scripts/buzz_feishu_group_sync.py --state-dir /fixed/state\n",
            encoding="utf-8",
        )
        report = self.run_audit()
        self.assertFalse(report["ok"])
        self.assertTrue(
            any(
                item["category"] == "feishu_release"
                and item["code"] == "entrypoint_contract_mismatch"
                for item in report["checks"]
            ),
            report,
        )

    def test_unit_dropins_fail_unknown_without_reading_their_content(self) -> None:
        secret = "drop-in-secret-must-never-appear"
        self.write(
            self.units / "buzz-feishu-demo.service.d/override.conf",
            f"[Service]\nEnvironment=TOKEN={secret}\n",
            0o644,
        )
        report = self.run_audit()
        self.assertFalse(report["ok"])
        self.assertTrue(
            any(item["code"] == "unit_dropins_not_inspected" for item in report["checks"]),
            report,
        )
        self.assertNotIn(secret, json.dumps(report))

    def test_cross_lookup_and_type_dropins_are_unknown_without_reading_content(self) -> None:
        secret = "must-never-enter-the-receipt"
        dropin = self.home / ".config/systemd/user.control/service.d/override.conf"
        self.write(
            dropin,
            f"[Service]\nEnvironment=TOKEN={secret}\n",
            0o644,
        )
        report = self.run_audit()
        self.assertFalse(report["ok"])
        self.assertTrue(
            any(item["code"] == "unit_dropins_not_inspected" for item in report["checks"]),
            report,
        )
        self.assertNotIn(secret, json.dumps(report))
        dropin.unlink()
        dropin.parent.rmdir()

        shadow_secret = "shadow-unit-secret-must-never-enter-the-receipt"
        self.write(
            self.home
            / ".config/systemd/user.control/gitlab-buzz-sync-demo.service",
            f"[Service]\nEnvironment=TOKEN={shadow_secret}\n",
            0o644,
        )
        report = self.run_audit()
        self.assertFalse(report["ok"])
        self.assertTrue(
            any(
                item["code"] == "unit_shadow_candidate_not_inspected"
                for item in report["checks"]
            ),
            report,
        )
        self.assertNotIn(shadow_secret, json.dumps(report))

    def test_lookup_only_unit_is_inventory_unknown_without_reading_content(self) -> None:
        secret = "lookup-only-secret-must-never-appear"
        lookup = self.home / ".local/share/systemd/user"
        self.write(
            lookup / "gitlab-buzz-sync-extra.service",
            f"[Service]\nEnvironment=OWNER_TOKEN={secret}\n",
            0o644,
        )
        lookup.chmod(0o755)
        report = self.run_audit()
        self.assertFalse(report["ok"])
        self.assertEqual(1, report["inventory"]["lookup_only_units"])
        self.assertTrue(
            any(item["code"] == "unit_outside_primary_directory" for item in report["checks"]),
            report,
        )
        self.assertEqual("unknown", report["gaps"]["LA-07"]["status"])
        self.assertNotIn(secret, json.dumps(report))

    def test_world_writable_unit_and_release_entrypoint_fail_closed(self) -> None:
        unit = self.units / "buzz-feishu-demo.service"
        unit.chmod(0o666)
        report = self.run_audit()
        self.assertFalse(report["ok"])
        self.assertTrue(
            any(item["code"] == "unit_not_regular_or_symlink" for item in report["checks"]),
            report,
        )
        unit.chmod(0o644)
        entrypoint = self.release / "scripts/buzz_feishu_group_sync.py"
        entrypoint.chmod(0o644)
        report = self.run_audit()
        self.assertFalse(report["ok"])
        self.assertTrue(
            any(
                item["code"] in {"release_entrypoint_invalid", "release_manifest_invalid"}
                for item in report["checks"]
            ),
            report,
        )

    def test_timer_comments_cannot_spoof_effective_values(self) -> None:
        self.write(
            self.units / "gitlab-buzz-sync-demo.timer",
            "[Timer]\n# OnUnitActiveSec=300\nOnUnitActiveSec=999\n"
            "# Persistent=true\nPersistent=false\n",
            0o644,
        )
        report = self.run_audit()
        codes = {item["code"] for item in report["checks"] if item["status"] == "fail"}
        self.assertIn("timer_interval_mismatch", codes)
        self.assertIn("timer_not_persistent", codes)

    def test_timer_and_service_must_keep_the_complete_template_contract(self) -> None:
        service = self.units / "buzz-feishu-demo.service"
        original_service = service.read_text(encoding="utf-8")
        service.write_text(
            original_service.replace(
                "ExecStart=", "ExecStartPre=/usr/bin/true\nExecStart=", 1
            ),
            encoding="utf-8",
        )
        report = self.run_audit()
        self.assertTrue(
            any(item["code"] == "service_baseline_mismatch" for item in report["checks"]),
            report,
        )
        service.write_text(original_service, encoding="utf-8")

        timer = self.units / "buzz-feishu-demo.timer"
        original_timer = timer.read_text(encoding="utf-8")
        timer.write_text(
            original_timer.replace(
                "OnUnitActiveSec=1min", "OnUnitActiveSec=1min\nRandomizedDelaySec=1h"
            ),
            encoding="utf-8",
        )
        report = self.run_audit()
        self.assertTrue(
            any(item["code"] == "timer_template_mismatch" for item in report["checks"]),
            report,
        )

    def test_unit_environment_rejects_empty_or_multiple_assignments(self) -> None:
        service = self.units / "gitlab-buzz-sync-demo.service"
        original = service.read_text(encoding="utf-8")
        for replacement in (
            "Environment=BUZZ_SYNC_ENV_FILE=",
            "Environment=BUZZ_SYNC_ENV_FILE=%h/.config/buzz/agents/demo-dev.env OTHER=x",
            "Environment=BUZZ_SYNC_ENV_FILE=%h/.config/buzz/agents/demo-dev.env\nEnvironment=OTHER=x",
        ):
            with self.subTest(replacement=replacement):
                service.write_text(
                    re.sub(r"Environment=BUZZ_SYNC_ENV_FILE=.*", replacement, original),
                    encoding="utf-8",
                )
                report = self.run_audit()
                self.assertTrue(
                    any(
                        item["code"] in {
                            "service_baseline_mismatch",
                            "service_environment_contract_mismatch",
                        }
                        for item in report["checks"]
                    ),
                    report,
                )
        service.write_text(original, encoding="utf-8")

    def test_launcher_content_not_only_permissions_is_a_gate(self) -> None:
        launcher = self.home / ".config/buzz/sync/gitlab-buzz-sync-launch.sh"
        launcher.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
        report = self.run_audit()
        self.assertFalse(report["ok"])
        self.assertTrue(
            any(item["code"] == "launcher_template_mismatch" for item in report["checks"]),
            report,
        )

    def test_agent_shared_launcher_content_must_match_canonical_template(self) -> None:
        launcher = self.agents / "run-agent.py"
        launcher.chmod(0o700)
        launcher.write_text("#!/usr/bin/python3\nraise SystemExit(0)\n", encoding="utf-8")
        launcher.chmod(0o500)
        report = self.run_audit()
        self.assertFalse(report["ok"])
        self.assertTrue(
            any(
                item["category"] == "shared_launcher"
                and item["subject"] == "launcher:run-agent.py"
                and item["code"] == "launcher_template_mismatch"
                for item in report["checks"]
            ),
            report,
        )

    def test_read_only_audit_never_executes_the_target_release_launcher(self) -> None:
        marker = self.home / "target-launcher-executed"
        launcher = self.release / "references/scripts/run-agent.py"
        for path in (self.release, launcher.parent, launcher.parent.parent):
            path.chmod(0o755)
        launcher.chmod(0o644)
        launcher.write_text(
            "from pathlib import Path\n"
            f"Path({str(marker)!r}).write_text('executed', encoding='utf-8')\n",
            encoding="utf-8",
        )
        launcher.chmod(0o444)
        manifest = self.release / ".release-manifest.json"
        manifest.chmod(0o644)
        value = json.loads(manifest.read_text(encoding="utf-8"))
        record = value["files"]["references/scripts/run-agent.py"]
        record["sha256"] = hashlib.sha256(launcher.read_bytes()).hexdigest()
        manifest.write_text(json.dumps(value), encoding="utf-8")
        manifest.chmod(0o444)
        for path in self.release.rglob("*"):
            if path.is_dir():
                path.chmod(0o555)
        self.release.chmod(0o555)

        report = self.run_audit()
        self.assertFalse(marker.exists())
        self.assertFalse(report["ok"])
        self.assertTrue(
            any(item["code"] == "launcher_template_mismatch" for item in report["checks"]),
            report,
        )

    def test_agent_unit_cannot_append_arguments_to_canonical_exec(self) -> None:
        unit = self.units / "buzz-local-demo-dev.service"
        unit.write_text(
            "[Service]\nExecStart=/usr/bin/python3 -I "
            "%h/.config/buzz/agents/run-agent.py demo-dev --extra\n",
            encoding="utf-8",
        )
        report = self.run_audit()
        self.assertFalse(report["ok"])
        self.assertTrue(
            any(
                item["category"] == "agent_unit"
                and item["subject"] == "agent:demo-dev"
                and item["code"] == "launcher_mismatch"
                for item in report["checks"]
            ),
            report,
        )

    def test_service_and_timer_symlinks_fail_closed(self) -> None:
        service = self.units / "buzz-feishu-demo.service"
        real_service = self.units / "buzz-feishu-demo.service.real"
        service.rename(real_service)
        service.symlink_to(real_service.name)
        report = self.run_audit()
        self.assertFalse(report["ok"])
        self.assertTrue(
            any(
                item["category"] == "feishu_release"
                and item["code"] == "unit_not_regular_or_symlink"
                for item in report["checks"]
            ),
            report,
        )

        service.unlink()
        real_service.rename(service)
        timer = self.units / "buzz-feishu-demo.timer"
        real_timer = self.units / "buzz-feishu-demo.timer.real"
        timer.rename(real_timer)
        timer.symlink_to(real_timer.name)
        report = self.run_audit()
        self.assertFalse(report["ok"])
        self.assertTrue(
            any(
                item["category"] == "feishu_release"
                and item["code"] == "timer_missing_not_regular_or_symlink"
                for item in report["checks"]
            ),
            report,
        )

    def test_known_orphan_timer_is_reported_as_unknown(self) -> None:
        (self.units / "buzz-feishu-demo.service").unlink()
        report = self.run_audit()
        self.assertFalse(report["ok"])
        self.assertTrue(
            any(
                item["category"] == "inventory"
                and item["subject"] == "timer:buzz-feishu-demo.timer"
                and item["code"] == "orphan_timer_without_service"
                and item["status"] == "unknown"
                for item in report["checks"]
            ),
            report,
        )
        self.assertEqual("unknown", report["gaps"]["LA-08"]["status"])

    def test_agent_timer_is_never_part_of_the_persistent_contract(self) -> None:
        self.write(
            self.units / "buzz-local-demo-dev.timer",
            "[Timer]\nOnActiveSec=1min\nUnit=buzz-local-demo-dev.service\n",
            0o644,
        )
        report = self.run_audit()
        self.assertTrue(
            any(item["code"] == "unexpected_agent_timer" for item in report["checks"]),
            report,
        )

    def test_manager_need_daemon_reload_is_unknown(self) -> None:
        module = load_module()
        auditor = module.Auditor(self.home, EXPECTED)
        auditor._verify_user_manager = True
        unit = self.units / "buzz-local-demo-dev.service"
        output = "\n".join(
            [
                f"FragmentPath={unit}",
                "DropInPaths=",
                "NeedDaemonReload=yes",
                f"ExecStart={{ argv[]=/usr/bin/python3 -I {self.agents}/run-agent.py demo-dev ; }}",
                "UMask=0077",
                "NoNewPrivileges=yes",
                f"UnsetEnvironment={module.SYSTEMD_UNSET_ENVIRONMENT}",
                "Restart=on-failure",
                "RestartUSec=5s",
            ]
        )
        with mock.patch.object(
            module.subprocess,
            "run",
            return_value=mock.Mock(returncode=0, stdout=output),
        ):
            self.assertFalse(
                auditor.verify_effective_unit(unit, "agent_unit", "agent:demo-dev")
            )
        self.assertTrue(
            any(item["code"] == "effective_unit_surface_unverified" for item in auditor.checks),
            auditor.checks,
        )

    def test_manager_loaded_deleted_unit_is_discovered(self) -> None:
        module = load_module()
        auditor = module.Auditor(self.home, EXPECTED)
        auditor._verify_user_manager = True
        auditor._unit_search_cache = [self.units]
        results = [
            mock.Mock(returncode=0, stdout=""),
            mock.Mock(
                returncode=0,
                stdout="buzz-local-deleted.service loaded inactive dead deleted\n",
            ),
        ]
        with mock.patch.object(module.subprocess, "run", side_effect=results) as invoked:
            discovered = auditor.discover_lookup_only_units(
                {path.name for path in self.units.glob("*")}
            )
        for call in invoked.call_args_list:
            self.assertIn("--plain", call.args[0])
            self.assertIn("--full", call.args[0])
        self.assertIn("buzz-local-deleted.service", discovered)
        self.assertTrue(
            any(item["code"] == "unit_outside_primary_directory" for item in auditor.checks),
            auditor.checks,
        )

    def test_buzz_cli_digest_mismatch_fails_closed_without_leaking_digest(self) -> None:
        cli = self.home / ".local/opt/buzz-0.5.23/usr/bin/buzz"
        cli.chmod(0o755)
        cli.write_bytes(b"\x7fELFtampered")
        cli.chmod(0o555)
        report = self.run_audit()
        self.assertFalse(report["ok"])
        self.assertTrue(
            any(
                item["category"] == "buzz_cli"
                and item["code"] == "binary_or_digest_mismatch"
                for item in report["checks"]
            ),
            report,
        )
        self.assertNotIn(self.cli_digest, json.dumps(report))

    def test_buzz_cli_requires_the_canonical_release_and_effective_execute_bit(self) -> None:
        responsible = self.agents / "demo/responsible/demo-dev.json"
        value = json.loads(responsible.read_text(encoding="utf-8"))
        wrong = self.home / ".local/opt/buzz-0.6.0/usr/bin/buzz"
        self.write(wrong, "\x7fELFfixture-buzz", 0o555)
        value["buzz"] = {
            "cli_path": str(wrong),
            "cli_sha256": hashlib.sha256(wrong.read_bytes()).hexdigest(),
        }
        self.write(responsible, json.dumps(value))
        report = self.run_audit()
        self.assertTrue(
            any(item["code"] == "binary_or_digest_mismatch" for item in report["checks"]),
            report,
        )

        canonical = self.home / ".local/opt/buzz-0.5.23/usr/bin/buzz"
        value["buzz"] = {
            "cli_path": str(canonical),
            "cli_sha256": hashlib.sha256(canonical.read_bytes()).hexdigest(),
        }
        self.write(responsible, json.dumps(value))
        canonical.chmod(0o400)
        report = self.run_audit()
        self.assertTrue(
            any(item["code"] == "binary_or_digest_mismatch" for item in report["checks"]),
            report,
        )

    def test_media_proxy_must_match_the_target_release(self) -> None:
        env = (self.agents / "demo-dev.env").read_text(encoding="utf-8")
        raw = next(
            line.split("=", 1)[1]
            for line in env.splitlines()
            if line.startswith("BUZZ_ACP_AGENT_COMMAND=")
        )
        proxy = Path(raw)
        proxy.chmod(0o755)
        proxy.write_text("# stale proxy\n", encoding="utf-8")
        proxy.chmod(0o555)
        report = self.run_audit()
        self.assertFalse(report["ok"])
        self.assertTrue(
            any(
                item["category"] == "media_proxy"
                and item["code"] == "media_proxy_contract_mismatch"
                for item in report["checks"]
            ),
            report,
        )

    def test_transient_units_are_found_by_name_without_reading_content(self) -> None:
        module = load_module()
        runtime = self.home / "run/user/1000"
        transient = runtime / "systemd/transient"
        secret = "transient-secret-must-never-appear"
        self.write(
            transient / "gitlab-buzz-sync-personal.timer",
            f"[Service]\nEnvironment=OWNER_TOKEN={secret}\n",
            0o644,
        )
        auditor = module.Auditor(self.home, EXPECTED, runtime)
        report = auditor.run()
        self.assertFalse(report["ok"])
        self.assertEqual(1, report["inventory"]["transient_services"])
        self.assertTrue(
            any(
                item["code"] == "transient_unit_content_not_inspected"
                for item in report["checks"]
            ),
            report,
        )
        self.assertEqual("unknown", report["gaps"]["LA-07"]["status"])
        self.assertNotIn(secret, json.dumps(report))

    def test_untrusted_transient_directory_is_unknown(self) -> None:
        module = load_module()
        runtime = self.home / "run/user/1000"
        self.write(runtime / "systemd/transient", "not a directory\n", 0o600)
        report = module.Auditor(self.home, EXPECTED, runtime).run()
        self.assertTrue(
            any(item["code"] == "transient_inventory_untrusted" for item in report["checks"]),
            report,
        )
        for gap_id in ("LA-07", "LA-08", "LA-09", "LA-10"):
            self.assertEqual("unknown", report["gaps"][gap_id]["status"])

    def test_malformed_release_manifest_still_returns_a_structured_receipt(self) -> None:
        manifest = self.release / ".release-manifest.json"
        manifest.chmod(0o644)
        manifest.write_text('["version", "commit", "files"]\n', encoding="utf-8")
        manifest.chmod(0o444)
        report = self.run_audit()
        self.assertFalse(report["ok"])
        self.assertTrue(
            any(item["code"] == "release_manifest_invalid" for item in report["checks"]),
            report,
        )

    def test_oversized_file_and_invalid_sync_config_fail_closed(self) -> None:
        module = load_module()
        prompt = self.agents / "demo-dev.prompt.md"
        original_prompt = prompt.read_text(encoding="utf-8")
        prompt.write_bytes(b"x" * (module.MAX_TEXT_BYTES + 1))
        prompt.chmod(0o600)
        report = self.run_audit()
        self.assertFalse(report["ok"])
        self.assertTrue(
            any(
                item["category"] == "agent_prompt"
                and item["code"] == "unsafe_unreadable_or_oversized_file"
                for item in report["checks"]
            ),
            report,
        )

        self.write(prompt, original_prompt)
        sync_config = self.agents / "demo-gitlab-buzz/sync.json"
        self.write(sync_config, "{}")
        report = self.run_audit()
        self.assertFalse(report["ok"])
        self.assertTrue(
            any(
                item["category"] == "sync_release"
                and item["code"] == "manifest_or_sync_config_invalid"
                for item in report["checks"]
            ),
            report,
        )

    def test_prompt_and_sandbox_drift_are_both_reported(self) -> None:
        prompt = self.agents / "demo-dev.prompt.md"
        prompt.write_text(
            prompt.read_text() + "\ncanvas_alias\n",
            encoding="utf-8",
        )
        settings = self.workdir / ".claude/settings.local.json"
        payload = json.loads(settings.read_text())
        payload["sandbox"]["filesystem"]["allowRead"].remove(str(self.release))
        settings.write_text(json.dumps(payload), encoding="utf-8")
        report = self.run_audit()
        failed = {item["category"] for item in report["checks"] if item["status"] == "fail"}
        self.assertIn("agent_prompt", failed)
        self.assertIn("sandbox", failed)

    def test_responsible_config_matches_the_runtime_v2_contract(self) -> None:
        path = self.agents / "demo/responsible/demo-dev.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["sender_pubkey"] = "not-a-pubkey"
        self.write(path, json.dumps(value))
        report = self.run_audit()
        self.assertFalse(report["ok"])
        self.assertTrue(
            any(
                item["category"] == "responsible_config"
                and item["code"] == "contract_not_v2"
                for item in report["checks"]
            ),
            report,
        )

    def test_responsible_config_validation_never_imports_the_deployed_helper(self) -> None:
        marker = self.home / "deployed-helper-ran"
        helper = self.release / "scripts/buzz_send_with_responsible_mentions.py"
        helper.chmod(0o644)
        helper.write_text(
            f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n",
            encoding="utf-8",
        )
        helper.chmod(0o444)
        auditor = load_module().Auditor(self.home, EXPECTED)
        people, state_dir = auditor.audit_responsible_config(
            "demo-dev", self.agents / "demo/responsible/demo-dev.json"
        )
        self.assertIsNotNone(people)
        self.assertIsNotNone(state_dir)
        self.assertFalse(marker.exists())
        self.assertFalse(
            any(item["status"] in {"fail", "unknown"} for item in auditor.checks),
            auditor.checks,
        )

    def test_sandbox_rejects_broad_read_and_write_exceptions(self) -> None:
        path = self.workdir / ".claude/settings.local.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["sandbox"]["filesystem"]["allowRead"].append("/")
        value["sandbox"]["filesystem"]["allowWrite"].append(str(self.home))
        self.write(path, json.dumps(value))
        report = self.run_audit()
        self.assertFalse(report["ok"])
        self.assertTrue(
            any(
                item["category"] == "sandbox"
                and item["code"] == "sandbox_baseline_mismatch"
                for item in report["checks"]
            ),
            report,
        )

    def test_sandbox_rejects_multiple_project_settings_sources(self) -> None:
        local = self.workdir / ".claude/settings.local.json"
        self.write(
            self.workdir / ".claude/settings.json",
            local.read_text(encoding="utf-8"),
        )
        report = self.run_audit()
        self.assertTrue(
            any(item["code"] == "settings_sources_ambiguous" for item in report["checks"]),
            report,
        )

    def test_sandbox_rejects_unknown_keys_invalid_paths_and_missing_read_denies(self) -> None:
        path = self.workdir / ".claude/settings.local.json"
        original = path.read_text(encoding="utf-8")
        mutations = []
        value = json.loads(original)
        value["sandbox"]["allowAllUnixSockets"] = True
        mutations.append(value)
        value = json.loads(original)
        value["sandbox"]["filesystem"]["allowRead"].append("relative/ignored")
        mutations.append(value)
        value = json.loads(original)
        value["permissions"]["deny"].remove("Read(~/.config/buzz/**)")
        mutations.append(value)
        for value in mutations:
            with self.subTest(keys=sorted(value["sandbox"])):
                self.write(path, json.dumps(value))
                report = self.run_audit()
                self.assertFalse(report["ok"])
                self.assertTrue(
                    any(
                        item["category"] == "sandbox"
                        and item["code"] == "sandbox_baseline_mismatch"
                        for item in report["checks"]
                    ),
                    report,
                )
        self.write(path, original)

    def test_shared_credential_policy_requires_deny_entries(self) -> None:
        path = self.home / ".claude-buzz/settings.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["sandbox"]["credentials"]["envVars"][0]["mode"] = "allow"
        self.write(path, json.dumps(value))
        report = self.run_audit()
        self.assertFalse(report["ok"])
        self.assertTrue(
            any(item["code"] == "shared_sandbox_policy_missing" for item in report["checks"]),
            report,
        )

    def test_every_prompt_contract_marker_has_a_red_mutation(self) -> None:
        module = load_module()
        contract = module.load_prompt_contract()
        prompt = self.agents / "demo-dev.prompt.md"
        original = prompt.read_text(encoding="utf-8")
        required = [
            marker.replace("<RELEASE>", str(self.release))
            for marker in [
                *contract["common"]["required"],
                *contract["dev"]["required"],
            ]
        ]
        for marker in required:
            with self.subTest(marker=marker):
                prompt.write_text(original.replace(marker, "REMOVED"), encoding="utf-8")
                report = self.run_audit()
                self.assertTrue(
                    any(
                        item["category"] == "agent_prompt"
                        and item["status"] == "fail"
                        for item in report["checks"]
                    ),
                    report,
                )
        for marker in contract["common"]["forbidden"]:
            with self.subTest(forbidden=marker):
                prompt.write_text(original + "\n" + marker + "\n", encoding="utf-8")
                report = self.run_audit()
                self.assertTrue(
                    any(
                        item["category"] == "agent_prompt"
                        and item["status"] == "fail"
                        for item in report["checks"]
                    ),
                    report,
                )
        prompt.write_text(original, encoding="utf-8")

    def test_every_supported_prompt_role_has_a_complete_red_contract(self) -> None:
        module = load_module()
        contract = module.load_prompt_contract()
        self.assertEqual(module.KNOWN_PROMPT_ROLES, set(contract))
        prompt = self.agents / "demo-dev.prompt.md"
        for role in sorted(module.KNOWN_PROMPT_ROLES - {"common"}):
            required = [
                marker.replace("<RELEASE>", str(self.release))
                for marker in [
                    *contract["common"]["required"],
                    *contract[role]["required"],
                ]
            ]
            original = "\n".join(required) + "\n"
            for marker in required:
                with self.subTest(role=role, marker=marker):
                    self.write(prompt, original.replace(marker, "REMOVED"))
                    auditor = module.Auditor(self.home, EXPECTED)
                    auditor.prompt_roles = {"demo-dev": role}
                    auditor.audit_prompt("demo-dev", prompt)
                    self.assertTrue(
                        any(item["status"] == "fail" for item in auditor.checks),
                        auditor.checks,
                    )

    def test_prompt_keywords_do_not_replace_the_full_contract(self) -> None:
        prompt = self.agents / "demo-dev.prompt.md"
        prompt.write_text(
            "Issue Thread reply-to responsible_mentions DEV-ASSESSMENT "
            f"{self.release}/scripts/buzz_send_with_responsible_mentions.py\n",
            encoding="utf-8",
        )
        report = self.run_audit()
        self.assertTrue(
            any(
                item["category"] == "agent_prompt"
                and item["code"] == "required_markers_missing"
                for item in report["checks"]
            ),
            report,
        )

    def test_codex_and_grok_plugin_metadata_are_supported_offline(self) -> None:
        module = load_module()
        codex_home = self.home / ".codex-buzz"
        revision = "c" * 40
        self.write(
            codex_home / "config.toml",
            "[marketplaces.addx]\n"
            'source_type = "git"\n'
            'source = "git@gitlab.addx.ai:engineering/skills.git"\n'
            "\n"
            '[plugins."addx@addx"]\n'
            "enabled = true\n",
            0o644,
        )
        revision_release = self.release.parent / revision
        revision_skill = revision_release / "SKILL.md"
        self.write(
            revision_skill,
            "---\nname: buzz-agent-setup\n---\n",
            0o444,
        )
        self.write(
            revision_release / ".release-manifest.json",
            json.dumps(
                {
                    "version": 1,
                    "commit": revision,
                    "files": {
                        "SKILL.md": {
                            "mode": 0o444,
                            "sha256": hashlib.sha256(revision_skill.read_bytes()).hexdigest(),
                        }
                    },
                }
            ),
            0o444,
        )
        revision_release.chmod(0o555)
        auditor = module.Auditor(self.home, revision)
        codex_install = codex_home / "plugins/cache/addx/addx/1.0.0"
        self.write(
            codex_install / ".codex-marketplace-install.json",
            json.dumps(
                {
                    "source_type": "git",
                    "source": "git@gitlab.addx.ai:engineering/skills.git",
                    "ref_name": None,
                    "sparse_paths": [],
                    "revision": revision,
                }
            ),
            0o644,
        )
        self.write(
            codex_install / "skills/buzz-agent-setup/SKILL.md",
            "---\nname: buzz-agent-setup\n---\n",
            0o644,
        )
        codex_binary = self.home / ".local/bin/codex"
        codex_marker = self.home / "codex-was-executed"
        self.write(
            codex_binary,
            f"#!/bin/sh\nprintf ran > {codex_marker}\nexit 9\n",
            0o700,
        )
        codex_runtime = json.dumps(
            [
                "demo-dev",
                str(codex_home),
                str(codex_binary),
                str(self.workdir),
                f"{self.home / '.local/bin'}:/usr/bin:/bin",
            ]
        )
        auditor.audit_codex_plugin(codex_runtime)
        self.assertFalse(codex_marker.exists())

        unexpected = codex_home / "plugins/cache/addx/addx/README"
        self.write(unexpected, "unexpected\n", 0o644)
        extra_entry = module.Auditor(self.home, revision)
        extra_entry.audit_codex_plugin(codex_runtime)
        self.assertTrue(
            any(item["code"] == "codex_cache_unreadable" for item in extra_entry.checks),
            extra_entry.checks,
        )
        unexpected.unlink()

        # A matching repository path on another host is not canonical even
        # when the install metadata repeats the same hostile source.
        self.write(
            codex_home / "config.toml",
            "[marketplaces.addx]\n"
            'source_type = "git"\n'
            'source = "git@attacker.example:engineering/skills.git"\n\n'
            '[plugins."addx@addx"]\n'
            "enabled = true\n",
            0o644,
        )
        self.write(
            codex_install / ".codex-marketplace-install.json",
            json.dumps(
                {
                    "source_type": "git",
                    "source": "git@attacker.example:engineering/skills.git",
                    "ref_name": None,
                    "sparse_paths": [],
                    "revision": revision,
                }
            ),
            0o644,
        )
        hostile = module.Auditor(self.home, revision)
        hostile.audit_codex_plugin(codex_runtime)
        self.assertTrue(
            any(
                item["code"] == "codex_marketplace_invalid"
                and item["status"] == "fail"
                for item in hostile.checks
            ),
            hostile.checks,
        )
        self.write(
            codex_home / "config.toml",
            "[marketplaces.addx]\n"
            'source_type = "git"\n'
            'source = "git@gitlab.addx.ai:engineering/skills.git"\n\n'
            '[plugins."addx@addx"]\n'
            "enabled = true\n",
            0o644,
        )

        grok_home = self.home / ".grok"
        grok_install = grok_home / "installed-plugins/skills-fixture"
        self.write(
            grok_install / "skills/buzz-agent-setup/SKILL.md",
            "---\nname: buzz-agent-setup\n---\n",
            0o644,
        )
        self.write(
            grok_home / "installed-plugins/registry.json",
            json.dumps(
                {
                    "version": 1,
                    "repos": {
                        "skills-fixture": {
                            "kind": {
                                "type": "Git",
                                "url": "git@gitlab.example:engineering/skills.git",
                                "commit": revision,
                            },
                            "path": str(grok_install),
                        }
                    },
                }
            ),
            0o644,
        )
        auditor.audit_grok_plugin(json.dumps(["demo-dev", str(grok_home)]))
        self.assertTrue(
            all(item["status"] == "pass" for item in auditor.checks),
            auditor.checks,
        )

        # A different installed revision, or a second target-looking cache,
        # cannot be treated as the one enabled static install.
        newer = "d" * 40
        self.write(
            codex_install / ".codex-marketplace-install.json",
            json.dumps(
                {
                    "source_type": "git",
                    "source": "git@gitlab.addx.ai:engineering/skills.git",
                    "ref_name": None,
                    "sparse_paths": [],
                    "revision": newer,
                }
            ),
            0o644,
        )
        unused_target = codex_home / "plugins/cache/addx/addx/1.0.0+meta"
        self.write(
            unused_target / ".codex-marketplace-install.json",
            json.dumps(
                {
                    "source_type": "git",
                    "source": "git@gitlab.addx.ai:engineering/skills.git",
                    "ref_name": None,
                    "sparse_paths": [],
                    "revision": revision,
                }
            ),
            0o644,
        )
        self.write(
            unused_target / "skills/buzz-agent-setup/SKILL.md",
            "---\nname: buzz-agent-setup\n---\n",
            0o644,
        )
        mismatch = module.Auditor(self.home, revision)
        mismatch.audit_codex_plugin(codex_runtime)
        self.assertTrue(
            any(
                item["category"] == "plugin_revision"
                and item["status"] == "fail"
                for item in mismatch.checks
            ),
            mismatch.checks,
        )

        malformed = codex_home / "config.toml"
        self.write(
            malformed,
            "this is not valid TOML\n"
            "[marketplaces.addx]\n"
            'source_type = "git"\n'
            'source = "git@gitlab.addx.ai:engineering/skills.git"\n\n'
            '[plugins."addx@addx"]\n'
            "enabled = true\n",
            0o644,
        )
        invalid = module.Auditor(self.home, revision)
        invalid.audit_codex_plugin(codex_runtime)
        self.assertTrue(
            any(item["code"] == "codex_config_invalid" for item in invalid.checks),
            invalid.checks,
        )

        unresolved = module.Auditor(self.home, revision)
        unresolved.audit_codex_plugin(
            json.dumps(
                [
                    "demo-dev",
                    str(codex_home),
                    str(self.home / ".local/bin/not-the-runtime-codex"),
                    str(self.workdir),
                    f"{self.home / '.local/bin'}:/usr/bin:/bin",
                ]
            )
        )
        self.assertTrue(
            any(
                item["code"] == "codex_runtime_unresolved"
                and item["status"] == "unknown"
                for item in unresolved.checks
            ),
            unresolved.checks,
        )

        shorthand = module.Auditor(self.home, revision)
        shorthand.register_harness(
            "demo-dev",
            {
                "BUZZ_ACP_AGENT_COMMAND": "/trusted/codex-acp",
                "CODEX_HOME": "$HOME/.codex-buzz",
                "CODEX_PATH": "$HOME/.local/bin/codex",
            },
            (
                self.workdir,
                {
                    "PATH": f"{self.home / '.local/bin'}:/usr/bin:/bin",
                    "BUZZ_ACP_AGENT_COMMAND": "/trusted/codex-acp",
                    "CODEX_HOME": "$HOME/.codex-buzz",
                    "CODEX_PATH": "$HOME/.local/bin/codex",
                },
            ),
        )
        self.assertEqual(
            {("unknown", "agent:demo-dev:codex-runtime-unresolved")},
            shorthand.harnesses,
        )

        misleading = module.Auditor(self.home, revision)
        misleading.register_harness(
            "demo-dev",
            {"BUZZ_ACP_AGENT_COMMAND": "/trusted/codex-acp"},
            (
                self.workdir,
                {
                    "PATH": f"{self.home / '.local/bin'}:/usr/bin:/bin",
                    "BUZZ_ACP_AGENT_COMMAND": "/trusted/fake-codex-acp-wrapper",
                    "CODEX_HOME": str(codex_home),
                    "CODEX_PATH": str(codex_binary),
                },
            ),
        )
        self.assertEqual(
            {("unknown", "agent:demo-dev")},
            misleading.harnesses,
        )

    def test_unknown_discovery_and_forbidden_env_key_fail_closed(self) -> None:
        (self.units / "buzz-local-demo-dev.service").unlink()
        report = self.run_audit()
        self.assertFalse(report["ok"])
        self.assertTrue(any(item["status"] == "unknown" for item in report["checks"]))

        self.write(
            self.units / "buzz-local-demo-dev.service",
            "[Service]\nExecStart=/usr/bin/python3 -I "
            "%h/.config/buzz/agents/run-agent.py demo-dev\n",
            0o644,
        )
        with (self.agents / "demo-dev.env").open("a", encoding="utf-8") as handle:
            handle.write("buzz_private_key=never-print-this-either\n")
        report = self.run_audit()
        self.assertFalse(report["ok"])
        self.assertTrue(
            any(
                item["category"] == "agent_env" and item["status"] == "fail"
                for item in report["checks"]
            )
        )

    def test_agent_env_rejects_shell_execution_and_runtime_injection(self) -> None:
        env = self.agents / "demo-dev.env"
        original = env.read_text(encoding="utf-8")
        for line, code in (
            ("EXTRA=$(touch /tmp/should-never-run)\n", "unsupported_env_syntax"),
            ("PYTHONPATH='/tmp/inject'\n", "forbidden_keys"),
            ("RUN_HOME='/tmp/replace-launcher-home'\n", "forbidden_keys"),
        ):
            with self.subTest(code=code):
                env.write_text(original + line, encoding="utf-8")
                report = self.run_audit()
                self.assertFalse(report["ok"])
                self.assertTrue(
                    any(
                        item["category"] == "agent_env" and item["code"] == code
                        for item in report["checks"]
                    ),
                    report,
                )
        env.write_text(original, encoding="utf-8")

    def test_launcher_preflight_is_part_of_the_audit_gate(self) -> None:
        env = self.agents / "demo-dev.env"
        original = env.read_text(encoding="utf-8")
        for mutated in (
            re.sub(r"^BUZZ_RELAY_URL=.*\n", "", original, flags=re.MULTILINE),
            re.sub(r"^CLAUDE_CONFIG_DIR=.*\n", "", original, flags=re.MULTILINE),
            re.sub(
                r"^CLAUDE_CONFIG_DIR=.*$",
                f"CLAUDE_CONFIG_DIR={self.home / '.claude-glm'}",
                original,
                flags=re.MULTILINE,
            ),
            re.sub(
                r"^BUZZ_AGENT_SAFE_PATH=.*$",
                "BUZZ_AGENT_SAFE_PATH=/tmp",
                original,
                flags=re.MULTILINE,
            ),
            re.sub(
                r"^AGENT_WORKDIR=.*$",
                f"AGENT_WORKDIR={self.home}",
                original,
                flags=re.MULTILINE,
            ),
            re.sub(r"^BUZZ_ACP_BINARY=.*\n", "", original, flags=re.MULTILINE),
            re.sub(
                r"^BUZZ_ACP_BINARY_SHA256=.*$",
                "BUZZ_ACP_BINARY_SHA256=" + "0" * 64,
                original,
                flags=re.MULTILINE,
            ),
        ):
            with self.subTest(mutated=mutated.splitlines()[0]):
                env.write_text(mutated, encoding="utf-8")
                report = self.run_audit()
                self.assertFalse(report["ok"])
                self.assertTrue(
                    any(
                        item["category"] == "agent_env"
                        and item["code"]
                        in {"required_keys_missing", "launcher_preflight_failed"}
                        for item in report["checks"]
                    ),
                    report,
                )
        env.write_text(original, encoding="utf-8")

    def test_static_claude_plugin_registry_must_be_enabled(self) -> None:
        settings = self.home / ".claude-buzz/settings.json"
        value = json.loads(settings.read_text(encoding="utf-8"))
        value["enabledPlugins"]["addx@addx"] = False
        settings.write_text(json.dumps(value), encoding="utf-8")
        report = self.run_audit()
        self.assertFalse(report["ok"])
        self.assertTrue(
            any(item["code"] == "plugin_not_enabled" for item in report["checks"]),
            report,
        )

    def test_claude_project_and_managed_settings_cannot_disable_the_plugin(self) -> None:
        project = self.workdir / ".claude/settings.local.json"
        value = json.loads(project.read_text(encoding="utf-8"))
        value["enabledPlugins"] = {"addx@addx": False}
        self.write(project, json.dumps(value))
        report = self.run_audit()
        self.assertTrue(
            any(item["code"] == "plugin_not_enabled" for item in report["checks"]),
            report,
        )

        value.pop("enabledPlugins")
        self.write(project, json.dumps(value))
        managed_root = self.home / "managed-claude"
        self.write(
            managed_root / "managed-settings.json",
            json.dumps({"enabledPlugins": {"addx@addx": False}}),
        )
        module = load_module()
        auditor = module.Auditor(
            self.home,
            EXPECTED,
            managed_settings_root=managed_root,
        )
        auditor.audit_claude_plugin(
            json.dumps(
                [
                    "demo-dev",
                    str(self.home / ".local/bin/claude-buzz"),
                    str(self.home / ".claude-buzz"),
                    str(self.workdir),
                    f"{self.home / '.local/bin'}:/usr/bin:/bin",
                ]
            )
        )
        self.assertTrue(
            any(item["code"] == "plugin_not_enabled" for item in auditor.checks),
            auditor.checks,
        )

    def test_claude_wrapper_is_never_executed_by_the_read_only_audit(self) -> None:
        wrapper = self.home / ".local/bin/claude-buzz"
        marker = self.home / "claude-wrapper-ran"
        wrapper.chmod(0o755)
        wrapper.write_text(
            f"#!/bin/sh\nprintf ran > {marker}\nexit 9\n", encoding="utf-8"
        )
        wrapper.chmod(0o555)
        report = self.run_audit()
        self.assertTrue(report["ok"], report)
        self.assertFalse(marker.exists())

    def test_stock_text_only_rollback_remains_launchable(self) -> None:
        env = self.agents / "demo-dev.env"
        lines = env.read_text(encoding="utf-8").splitlines()
        adapter = next(
            line.split("=", 1)[1]
            for line in lines
            if line.startswith("BUZZ_ACP_MEDIA_ADAPTER_COMMAND=")
        )
        lines = [
            f"BUZZ_ACP_AGENT_COMMAND={adapter}"
            if line.startswith("BUZZ_ACP_AGENT_COMMAND=")
            else line
            for line in lines
            if not line.startswith(
                ("BUZZ_ACP_MEDIA_ADAPTER_COMMAND=", "BUZZ_ACP_MEDIA_BUZZ_CLI=")
            )
        ]
        lines.append("BUZZ_ACP_MEDIA_MODE=stock_text_only")
        env.write_text("\n".join(lines) + "\n", encoding="utf-8")
        report = self.run_audit()
        self.assertTrue(report["ok"], report)
        self.assertTrue(
            any(
                item["category"] == "media_proxy"
                and item["code"] == "stock_text_only"
                and item["status"] == "not_applicable"
                for item in report["checks"]
            ),
            report,
        )

    def test_missing_prompt_role_map_is_unknown(self) -> None:
        (self.agents / "local-alignment-roles.json").unlink()
        report = self.run_audit()
        self.assertFalse(report["ok"])
        self.assertTrue(
            any(item["code"] == "prompt_role_map_missing" for item in report["checks"]),
            report,
        )
        self.assertEqual("unknown", report["gaps"]["LA-03"]["status"])

    def test_untrusted_registry_values_never_enter_receipts(self) -> None:
        secret = "token-like-secret\n\x1b[31m"
        path = self.home / ".claude-buzz/plugins/installed_plugins.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["plugins"]["addx@addx"][0]["gitCommitSha"] = secret
        self.write(path, json.dumps(value))
        report = self.run_audit()
        self.assertFalse(report["ok"])
        rendered = json.dumps(report, ensure_ascii=False)
        self.assertNotIn("token-like-secret", rendered)
        self.assertNotIn("31m", rendered)

    def test_report_never_contains_values_or_pubkeys(self) -> None:
        report = self.run_audit()
        rendered = json.dumps(report, ensure_ascii=False)
        for forbidden in (
            "fixture-secret-never-print",
            "1" * 64,
            "2" * 64,
            self.cli_digest,
        ):
            self.assertNotIn(forbidden, rendered)
        inventory = json.dumps(report["inventory_ids"])
        self.assertNotIn(str(self.workdir), inventory)
        self.assertNotIn(str(self.home / ".local/bin"), inventory)

    def test_cli_json_exit_code_is_fail_closed(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "-I",
                str(SCRIPT),
                "--home",
                str(self.home),
                "--expected-sha",
                EXPECTED,
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        self.assertTrue(json.loads(completed.stdout)["ok"])
        (self.agents / "people.json").chmod(0o644)
        failed = subprocess.run(
            [
                sys.executable,
                "-I",
                str(SCRIPT),
                "--home",
                str(self.home),
                "--expected-sha",
                EXPECTED,
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(2, failed.returncode, failed.stdout + failed.stderr)
        self.assertFalse(json.loads(failed.stdout)["ok"])

    def test_cli_refuses_a_nonisolated_python_parent(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--home",
                str(self.home),
                "--expected-sha",
                EXPECTED,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(2, completed.returncode)
        self.assertIn("/usr/bin/python3 -I", completed.stderr)

    def test_cli_refuses_loader_injection_even_with_isolated_python(self) -> None:
        child_env = dict(os.environ)
        child_env["LD_LIBRARY_PATH"] = "/tmp/untrusted-loader-path"
        completed = subprocess.run(
            [
                sys.executable,
                "-I",
                str(SCRIPT),
                "--home",
                str(self.home),
                "--expected-sha",
                EXPECTED,
            ],
            env=child_env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(2, completed.returncode)
        self.assertIn("clean environment", completed.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
