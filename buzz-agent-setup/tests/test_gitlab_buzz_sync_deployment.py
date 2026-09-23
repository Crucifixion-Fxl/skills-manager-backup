import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL = Path(__file__).resolve().parents[1]
RUNBOOK = SKILL / "references" / "systemd" / "README.md"
DESK_PROMPT = SKILL / "references" / "gitlab-buzz-sync.desk-prompt.md"


class BridgeDeploymentContractTest(unittest.TestCase):
    def test_sync_is_a_desk_owned_step_not_an_extra_systemd_service(self):
        """L1-GIS-104 Sync runs in Desk; no obsolete daemon/unit remains deployable."""
        systemd_dir = SKILL / "references" / "systemd"
        self.assertFalse((systemd_dir / "gitlab-buzz-sync@.service").exists())
        self.assertFalse((SKILL / "scripts" / "gitlab_buzz_sync_service.py").exists())
        self.assertFalse((SKILL / "scripts" / "gitlab_buzz_sync_trigger.py").exists())

        runbook = RUNBOOK.read_text(encoding="utf-8")
        for required in (
            "Desk-owned Agent Step",
            "Desk identity",
            "白名单环境变量",
            "0600",
            "0700",
            "lock、cursor 与 outbox",
        ):
            with self.subTest(required=required):
                self.assertIn(required, runbook)
        self.assertNotIn("gitlab-buzz-%i", runbook)

    def test_runbook_requires_revision_pin_identity_and_fixed_argv_checks(self):
        """L1-GIS-106 Acceptance proves provenance, Desk identity and no prompt-controlled argv."""
        runbook = RUNBOOK.read_text(encoding="utf-8")

        for required in (
            "40 位 commit SHA",
            "0600",
            "0700",
            "Desk",
            "GitLab token",
            "publisher_pubkey",
            "固定命令",
            "消息正文",
            "PENDING",
            "ACKED",
            "schedule",
        ):
            with self.subTest(required=required):
                self.assertIn(required, runbook)

    def test_default_route_path_has_no_second_service_or_timer(self):
        """L1-GIS-118 Route runs inside the Desk turn, not as a second identity or scheduler."""
        systemd_dir = SKILL / "references" / "systemd"
        self.assertFalse((systemd_dir / "gitlab-buzz-route-writer@.service").exists())
        self.assertFalse((systemd_dir / "gitlab-buzz-route-writer@.timer").exists())

    def test_runbook_keeps_canvas_desk_and_agent_config_boundaries_explicit(self):
        """L1-GIS-119 Canvas is editable policy while trust anchors and Agent policy stay in code."""
        runbook = RUNBOOK.read_text(encoding="utf-8")
        for required in (
            "不需要路由 Workflow",
            "Desk identity",
            "Canvas admin allowlist",
            "Agent prompt",
            "Role identity",
            "GitLab token",
            "--scan-once",
            "--dry-run",
        ):
            with self.subTest(required=required):
                self.assertIn(required, runbook)
        for forbidden in ("gitlab-buzz-route-<instance>", "gitlab-buzz-route-writer@.timer"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, runbook)



def fenced_blocks(text: str, language: str) -> list[str]:
    return re.findall(rf"```{language}\n(.*?)\n```", text, re.S)


def unit_block(runbook: str, marker: str) -> str:
    match = re.search(rf"<!-- template:{re.escape(marker)} -->\s*```ini\n(.*?)\n```", runbook, re.S)
    if match is None:
        raise AssertionError(f"unit template {marker} is required")
    return match.group(1)


def unit_values(block: str) -> dict[str, list[str]]:
    values: dict[str, list[str]] = {}
    for line in block.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", ";", "[")):
            continue
        key, _, value = line.partition("=")
        values.setdefault(key.strip(), []).append(value.strip())
    return values


class OwnerTimerDeploymentContractTest(unittest.TestCase):
    def test_runbook_ships_oneshot_service_and_persistent_300s_timer_templates(self):
        """L1-GIS-189 ADR-0008 unit templates: oneshot service → whitelist launcher → timer entrypoint, 300s Persistent timer."""
        runbook = RUNBOOK.read_text(encoding="utf-8")
        self.assertIn("# gitlab-buzz-sync-<channel>.service", unit_block(runbook, "sync-timer-service"))
        service = unit_values(unit_block(runbook, "sync-timer-service"))
        timer = unit_values(unit_block(runbook, "sync-timer-timer"))

        self.assertEqual(service.get("Type"), ["oneshot"])
        self.assertEqual(len(service.get("ExecStart", [])), 1)
        exec_argv = service["ExecStart"][0].split()
        self.assertEqual(exec_argv[0], "%h/.config/buzz/sync/gitlab-buzz-sync-launch.sh")
        self.assertEqual(exec_argv[1:], ["/usr/bin/python3", "<immutable-release>/scripts/gitlab_buzz_sync_timer.py"])
        self.assertEqual(service.get("Environment"), ["BUZZ_SYNC_ENV_FILE=%h/.config/buzz/agents/<desk>.env"])
        self.assertNotIn("EnvironmentFile", service)
        for hardening in ("NoNewPrivileges", "UMask", "TimeoutStartSec"):
            with self.subTest(hardening=hardening):
                self.assertIn(hardening, service)

        self.assertEqual(timer.get("OnUnitActiveSec"), ["300"])
        self.assertEqual(timer.get("Persistent"), ["true"])
        self.assertEqual(timer.get("Unit"), ["gitlab-buzz-sync-<channel>.service"])
        self.assertEqual(timer.get("WantedBy"), ["timers.target"])
        self.assertIn("OnBootSec", timer)

        units = unit_block(runbook, "sync-timer-service") + unit_block(runbook, "sync-timer-timer")
        for forbidden in ("buzz-acp", "claude", "codex", "HEARTBEAT", "gitlab_buzz_desk_runner.py --", "--summary"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, units)

    def test_runbook_orders_enable_disable_rollback_and_journal_commands(self):
        """L1-GIS-189 Enable after dry-run; disable = stop+disable timer first; state kept; journalctl is the private log."""
        runbook = RUNBOOK.read_text(encoding="utf-8")
        for required in (
            "systemctl --user daemon-reload",
            "systemctl --user start gitlab-buzz-sync-<channel>.service",
            "systemctl --user enable --now gitlab-buzz-sync-<channel>.timer",
            "systemctl --user disable --now gitlab-buzz-sync-<channel>.timer",
            "systemctl --user is-active gitlab-buzz-sync-<channel>.service",
            "journalctl --user -u gitlab-buzz-sync-<channel>.service",
            "systemctl --user list-timers",
            "loginctl enable-linger",
            "cursor、outbox 与 binding",
            "不会出现两个 writer",
        ):
            with self.subTest(required=required):
                self.assertIn(required, runbook)
        disable = runbook.index("systemctl --user disable --now gitlab-buzz-sync-<channel>.timer")
        idle = runbook.index("systemctl --user is-active gitlab-buzz-sync-<channel>.service")
        self.assertLess(disable, idle)
        for forbidden in ("BUZZ_ACP_HEARTBEAT", "PERMISSION_REQUESTS", "reject_once", "promote"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, runbook)

    @unittest.skipUnless(shutil.which("bash") and Path("/usr/bin/python3").is_file(), "needs bash and /usr/bin/python3")
    def test_launcher_passes_only_the_whitelist_and_rejects_other_commands(self):
        """L1-GIS-190 The documented launcher validates the 0600 env and execs the entrypoint with the sync whitelist only."""
        runbook = RUNBOOK.read_text(encoding="utf-8")
        match = re.search(r"<!-- template:sync-timer-launcher -->\s*```bash\n(.*?)\n```", runbook, re.S)
        self.assertIsNotNone(match, "launcher template is required")
        # L1-GIS-204 secrets never become argv: /proc/<pid>/cmdline of the transient env(1) is world-readable.
        exec_lines = [line for line in match.group(1).splitlines() if line.lstrip().startswith("exec ")]
        self.assertEqual(len(exec_lines), 1)
        self.assertNotRegex(match.group(1), r"exec[^\n]*(=\"?\$|\$\{PASS|\$\{!)")
        self.assertNotIn("env -i", exec_lines[0])
        with tempfile.TemporaryDirectory() as tmp:
            # macOS 的 tempfile 默认可返回 /var/... 这个 /private/var/... 的 symlink 路径；
            # launcher 有意拒绝非 canonical 入口，测试夹具必须先规范化自身根目录。
            root = Path(tmp).resolve()
            launcher = root / "gitlab-buzz-sync-launch.sh"
            launcher.write_text(match.group(1) + "\n", encoding="utf-8")
            launcher.chmod(0o700)
            release = root / "releases" / ("a" * 40)
            (release / "scripts").mkdir(parents=True)
            entry = release / "scripts" / "gitlab_buzz_sync_timer.py"
            entry.write_text("import json, os, sys\nprint(json.dumps({'argv': sys.argv[1:], 'env': dict(os.environ)}))\n",
                             encoding="utf-8")
            env_file = root / "desk.env"
            env_file.write_text(
                "BUZZ_RELAY_URL=https://relay.test\n"
                "BUZZ_PRIVATE_KEY=test-desk-key\n"
                "BUZZ_AUTH_TAG='[\"auth\",\"owner\",\"\",\"sig\"]'\n"
                "BUZZ_DESK_RUNNER_MANIFEST=/owner/manifest.json\n"
                "BUZZ_GITLAB_PROJECT_TOKEN_MAP=/owner/project-token-map.json\n"
                "GITLAB_TOKEN=test-gitlab-token\n"
                "BUZZ_ACP_AGENT_COMMAND=/opt/claude-agent-acp\n"
                "ANTHROPIC_AUTH_TOKEN=must-not-leak\n",
                encoding="utf-8",
            )
            env_file.chmod(0o600)
            base_env = {"HOME": str(root), "USER": "desk", "LOGNAME": "desk", "PATH": "/usr/bin:/bin",
                        "BUZZ_SYNC_ENV_FILE": str(env_file), "LEAKED_PARENT": "parent-only"}

            ok = subprocess.run([str(launcher), "/usr/bin/python3", str(entry)], env=base_env,
                                capture_output=True, text=True, timeout=30)
            self.assertEqual(ok.returncode, 0, ok.stderr)
            seen = json.loads(ok.stdout)
            self.assertEqual(seen["argv"], [])
            expected_env = {
                "HOME", "USER", "LOGNAME", "PATH", "LANG", "BUZZ_RELAY_URL",
                "BUZZ_PRIVATE_KEY", "BUZZ_AUTH_TAG", "BUZZ_DESK_RUNNER_MANIFEST",
                "BUZZ_GITLAB_PROJECT_TOKEN_MAP", "GITLAB_TOKEN",
            }
            # Apple injects these non-secret toolchain/runtime variables after exec;
            # the launcher still proves caller-provided variables were removed.
            if sys.platform == "darwin":
                expected_env.update(
                    {"__CF_USER_TEXT_ENCODING", "CPATH", "LIBRARY_PATH", "MANPATH", "SDKROOT"}
                )
            self.assertEqual(set(seen["env"]), expected_env)
            self.assertNotIn("LEAKED_PARENT", seen["env"])
            self.assertNotIn("ANTHROPIC_AUTH_TOKEN", seen["env"])
            self.assertNotIn("BUZZ_ACP_AGENT_COMMAND", seen["env"])
            self.assertEqual(seen["env"]["BUZZ_AUTH_TAG"], '["auth","owner","","sig"]')

            # Existing single-repo sync configs remain valid when no project map is configured.
            single_env = root / "desk-single-repo.env"
            single_env.write_text(env_file.read_text(encoding="utf-8").replace(
                "BUZZ_GITLAB_PROJECT_TOKEN_MAP=/owner/project-token-map.json\n", ""), encoding="utf-8")
            single_env.chmod(0o600)
            single_run = subprocess.run(
                [str(launcher), "/usr/bin/python3", str(entry)],
                env={**base_env, "BUZZ_SYNC_ENV_FILE": str(single_env)},
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(single_run.returncode, 0, single_run.stderr)
            self.assertNotIn("BUZZ_GITLAB_PROJECT_TOKEN_MAP", json.loads(single_run.stdout)["env"])

            if sys.platform == "darwin" and str(root).startswith("/private/var/"):
                raw_root = Path(str(root).removeprefix("/private"))
                raw_env = {
                    **base_env,
                    "HOME": str(raw_root),
                    "BUZZ_SYNC_ENV_FILE": str(raw_root / "desk.env"),
                }
                aliased = subprocess.run(
                    [
                        str(raw_root / "gitlab-buzz-sync-launch.sh"),
                        "/usr/bin/python3",
                        str(raw_root / "releases" / ("a" * 40) / "scripts" / "gitlab_buzz_sync_timer.py"),
                    ],
                    env=raw_env,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                self.assertEqual(aliased.returncode, 0, aliased.stderr)

            rejected = {
                "relative interpreter": [str(launcher), "python3", str(entry)],
                "other script": [str(launcher), "/usr/bin/python3", str(release / "scripts" / "gitlab_buzz_sync.py")],
                "extra argv": [str(launcher), "/usr/bin/python3", str(entry), "--config", "/tmp/x"],
            }
            for label, argv in rejected.items():
                with self.subTest(rejected=label):
                    run = subprocess.run(argv, env=base_env, capture_output=True, text=True, timeout=30)
                    self.assertNotEqual(run.returncode, 0)
                    self.assertEqual(run.stdout, "")

            env_file.chmod(0o640)
            loose = subprocess.run([str(launcher), "/usr/bin/python3", str(entry)], env=base_env,
                                   capture_output=True, text=True, timeout=30)
            self.assertNotEqual(loose.returncode, 0)
            self.assertEqual(loose.stdout, "")
            env_file.chmod(0o600)
            link = root / "link.env"
            os.symlink(env_file, link)
            symlinked = subprocess.run([str(launcher), "/usr/bin/python3", str(entry)],
                                       env={**base_env, "BUZZ_SYNC_ENV_FILE": str(link)},
                                       capture_output=True, text=True, timeout=30)
            self.assertNotEqual(symlinked.returncode, 0)
            self.assertEqual(symlinked.stdout, "")


if __name__ == "__main__":
    unittest.main()
