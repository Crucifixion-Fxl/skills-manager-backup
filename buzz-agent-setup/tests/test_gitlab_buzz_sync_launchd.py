import plistlib
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


SKILL = Path(__file__).resolve().parents[1]
RUNBOOK = SKILL / "references" / "launchd" / "README.md"
SYSTEMD_RUNBOOK = SKILL / "references" / "systemd" / "README.md"
SYNC = SKILL / "references" / "gitlab-buzz-sync.md"
RUNTIME = SKILL / "references" / "runtime-setup.md"
SCRIPTS_README = SKILL / "references" / "scripts" / "README.md"


def plist_block(text: str, marker: str) -> dict[str, object]:
    match = re.search(
        rf"<!-- template:{re.escape(marker)} -->\s*```xml\n(.*?)\n```",
        text,
        re.S,
    )
    if match is None:
        raise AssertionError(f"plist template {marker} is required")
    return plistlib.loads(match.group(1).encode("utf-8"))


def stage_zero_shell(text: str) -> str:
    section = text.split("## 3. 阶段 0：真实 launchd 探针后立即卸载", 1)[1]
    match = re.search(r"```bash\n(.*?)\n```", section, re.S)
    if match is None:
        raise AssertionError("stage 0 shell block is required")
    return match.group(1)


class LaunchdDeploymentContractTest(unittest.TestCase):
    def run_stage_zero(
        self,
        states: list[str],
        *,
        bootout_status: int = 0,
        bootstrap_status: int = 0,
    ) -> tuple[subprocess.CompletedProcess[str], list[str], list[dict[str, object]]]:
        runbook = RUNBOOK.read_text(encoding="utf-8")
        shell = stage_zero_shell(runbook).replace(
            '[ "$attempt" -lt 240 ]',
            '[ "$attempt" -lt 4 ]',
        )
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            home = root / "home"
            agents = home / "Library" / "LaunchAgents"
            agents.mkdir(parents=True)
            plist = agents / "ai.addx.gitlab-buzz-sync.<channel>.plist"
            plist.write_bytes(
                plistlib.dumps(
                    {
                        "Label": "ai.addx.gitlab-buzz-sync.<channel>",
                        "StartInterval": 300,
                    }
                )
            )
            calls = root / "launchctl.calls"
            state_file = root / "launchctl.states"
            state_file.write_text("\n".join(states) + "\n", encoding="utf-8")
            booted_out = root / "booted-out"
            bootstrapped = root / "bootstrapped"
            bindir = root / "bin"
            bindir.mkdir()
            (bindir / "launchctl").write_text(
                "#!/bin/sh\n"
                'printf "%s\\n" "$*" >> "$LAUNCHCTL_CALLS"\n'
                'if [ "$1" = bootstrap ]; then [ "$LAUNCHCTL_BOOTSTRAP_STATUS" -eq 0 ] && : > "$LAUNCHCTL_BOOTSTRAPPED"; exit "$LAUNCHCTL_BOOTSTRAP_STATUS"; fi\n'
                'if [ "$1" = bootout ]; then [ "$LAUNCHCTL_BOOTOUT_STATUS" -eq 0 ] && : > "$LAUNCHCTL_BOOTED_OUT"; exit "$LAUNCHCTL_BOOTOUT_STATUS"; fi\n'
                'if [ "$1" = print ]; then\n'
                '  [ -e "$LAUNCHCTL_BOOTSTRAPPED" ] || exit 1\n'
                '  [ ! -e "$LAUNCHCTL_BOOTED_OUT" ] || exit 1\n'
                '  state="$(head -n 1 "$LAUNCHCTL_STATES")"\n'
                '  tail -n +2 "$LAUNCHCTL_STATES" > "$LAUNCHCTL_STATES.next"\n'
                '  mv "$LAUNCHCTL_STATES.next" "$LAUNCHCTL_STATES"\n'
                '  case "$state" in\n'
                '    print_error) exit 5 ;;\n'
                '    waiting) echo "state = waiting"; echo "last exit code = (never exited)" ;;\n'
                '    running) echo "state = running"; echo "last exit code = (never exited)" ;;\n'
                '    failed) echo "state = exited"; echo "last exit code = 7" ;;\n'
                '    *) echo "state = exited"; echo "last exit code = 0" ;;\n'
                '  esac\n'
                "fi\n",
                encoding="utf-8",
            )
            (bindir / "plutil").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            (bindir / "sleep").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            buddy = bindir / "PlistBuddy"
            buddy.write_text(
                "#!/usr/bin/env python3\n"
                "import plistlib, sys\n"
                "path = sys.argv[-1]\n"
                "command = sys.argv[-2]\n"
                "with open(path, 'rb') as stream: value = plistlib.load(stream)\n"
                "if command == 'Delete :StartInterval': value.pop('StartInterval')\n"
                "elif command.startswith('Set :Label '): value['Label'] = command.removeprefix('Set :Label ')\n"
                "else: raise SystemExit(2)\n"
                "with open(path, 'wb') as stream: plistlib.dump(value, stream)\n",
                encoding="utf-8",
            )
            for executable in bindir.iterdir():
                executable.chmod(0o755)
            env = os.environ.copy()
            env.update(
                {
                    "HOME": str(home),
                    "PATH": f"{bindir}:{env['PATH']}",
                    "LAUNCHCTL_CALLS": str(calls),
                    "LAUNCHCTL_STATES": str(state_file),
                    "LAUNCHCTL_BOOTED_OUT": str(booted_out),
                    "LAUNCHCTL_BOOTSTRAPPED": str(bootstrapped),
                    "LAUNCHCTL_BOOTOUT_STATUS": str(bootout_status),
                    "LAUNCHCTL_BOOTSTRAP_STATUS": str(bootstrap_status),
                }
            )
            shell = shell.replace("/usr/libexec/PlistBuddy", str(buddy))
            result = subprocess.run(
                ["bash", "-c", shell],
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
            )
            probe_values = [plistlib.loads(path.read_bytes()) for path in agents.glob("*.probe.plist.*")]
            return result, calls.read_text(encoding="utf-8").splitlines(), probe_values

    def test_launchd_uses_the_same_fixed_launcher_and_zero_argument_entrypoint(self):
        runbook = RUNBOOK.read_text(encoding="utf-8")
        job = plist_block(runbook, "sync-launchd-plist")

        self.assertEqual(job["Label"], "ai.addx.gitlab-buzz-sync.<channel>")
        self.assertEqual(job["StartInterval"], 300)
        self.assertFalse(job["RunAtLoad"])
        self.assertEqual(
            job["ProgramArguments"],
            [
                "<home>/.config/buzz/sync/gitlab-buzz-sync-launch.sh",
                "/usr/bin/python3",
                "<immutable-release>/scripts/gitlab_buzz_sync_timer.py",
            ],
        )
        self.assertEqual(
            job["EnvironmentVariables"],
            {"BUZZ_SYNC_ENV_FILE": "<home>/.config/buzz/agents/<desk>.env"},
        )
        serialized = plistlib.dumps(job).decode("utf-8")
        for forbidden in (
            "GITLAB_TOKEN",
            "BUZZ_PRIVATE_KEY",
            "BUZZ_AUTH_TAG",
            "buzz-acp",
            "claude",
            "codex",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, serialized)

    def test_launchd_runbook_keeps_manual_probe_enable_and_rollback_fail_closed(self):
        runbook = RUNBOOK.read_text(encoding="utf-8")
        required = (
            "plutil -lint",
            "set -euo pipefail",
            "launchctl bootstrap",
            "launchctl kickstart",
            "launchctl print",
            "launchctl bootout",
            "(never exited)",
            "last exit code = 0",
            "trap cleanup_probe EXIT",
            "launchd probe timed out",
            "L4 receipt v3",
            "单 writer",
            "cursor、outbox 与 binding",
            "0600",
            "0700",
        )
        for text in required:
            with self.subTest(required=text):
                self.assertIn(text, runbook)
        first_bootstrap = runbook.index("launchctl bootstrap")
        wait_for_exit = runbook.index("仅数字 exit code", first_bootstrap)
        exit_zero = runbook.index("last exit code = 0", wait_for_exit)
        probe_bootout = runbook.index("\nif ! cleanup_probe; then\n", exit_zero)
        enable_heading = runbook.index("L4 receipt v3 通过后启用")
        second_bootstrap = runbook.index("launchctl bootstrap", probe_bootout + 1)
        self.assertLess(first_bootstrap, wait_for_exit)
        self.assertLess(wait_for_exit, exit_zero)
        self.assertLess(exit_zero, probe_bootout)
        self.assertLess(probe_bootout, enable_heading)
        self.assertLess(enable_heading, second_bootstrap)

    def test_stage_zero_timeout_never_boots_out_a_running_writer(self):
        result, calls, probe_values = self.run_stage_zero(["running"] * 5)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("launchd probe timed out", result.stderr)
        self.assertIn("left loaded one-shot", result.stderr)
        self.assertFalse(any(call.startswith("bootout ") for call in calls))
        self.assertEqual(len(probe_values), 1)
        self.assertNotIn("StartInterval", probe_values[0])
        self.assertRegex(str(probe_values[0]["Label"]), r"^ai\.addx\.gitlab-buzz-sync\.<channel>\.probe\.")

    def test_stage_zero_timeout_waiting_never_boots_out_a_possible_writer(self):
        result, calls, probe_values = self.run_stage_zero(["waiting"] * 5)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not terminal; left loaded one-shot", result.stderr)
        self.assertFalse(any(call.startswith("bootout ") for call in calls))
        self.assertEqual(len(probe_values), 1)
        self.assertNotIn("StartInterval", probe_values[0])

    def test_stage_zero_boots_out_once_and_removes_probe_after_writer_exits(self):
        result, calls, probe_values = self.run_stage_zero(["waiting", "running", "exited", "exited"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sum(call.startswith("bootout ") for call in calls), 1)
        self.assertEqual(probe_values, [])

    def test_stage_zero_failed_writer_is_cleaned_up_and_fails(self):
        result, calls, probe_values = self.run_stage_zero(["failed", "failed"])
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(sum(call.startswith("bootout ") for call in calls), 1)
        self.assertEqual(probe_values, [])

    def test_stage_zero_retains_recovery_plist_when_bootout_fails(self):
        result, calls, probe_values = self.run_stage_zero(["exited", "exited"], bootout_status=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("and recovery plist", result.stderr)
        self.assertEqual(sum(call.startswith("bootout ") for call in calls), 1)
        self.assertEqual(len(probe_values), 1)
        self.assertNotIn("StartInterval", probe_values[0])

    def test_stage_zero_bootstrap_failure_never_boots_out_an_existing_label(self):
        result, calls, probe_values = self.run_stage_zero([], bootstrap_status=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(call.startswith("bootout ") for call in calls))
        self.assertEqual(probe_values, [])
        self.assertFalse(
            any(
                call.startswith("print ") and call.endswith("/ai.addx.gitlab-buzz-sync.<channel>")
                for call in calls
            )
        )

    def test_stage_zero_retains_recovery_material_when_loaded_state_is_unreadable(self):
        result, calls, probe_values = self.run_stage_zero(["print_error", "print_error"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("state unreadable; retained", result.stderr)
        self.assertFalse(any(call.startswith("bootout ") for call in calls))
        self.assertEqual(len(probe_values), 1)
        self.assertNotIn("StartInterval", probe_values[0])

    def test_shared_launcher_is_documented_as_macos_portable(self):
        systemd = SYSTEMD_RUNBOOK.read_text(encoding="utf-8")
        self.assertIn("python3 realpath/stat", systemd)
        self.assertNotIn('realpath -e -- "$ENVFILE"', systemd)
        self.assertNotIn('stat -c %u -- "$ENVFILE"', systemd)
        self.assertNotIn('stat -c %a -- "$ENVFILE"', systemd)

    def test_skill_entrypoints_route_macos_operators_to_launchd(self):
        for path in (SKILL / "SKILL.md", SYNC, RUNTIME, SCRIPTS_README):
            with self.subTest(path=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertIn("launchd", text)
                self.assertIn("launchd/README.md", text)


if __name__ == "__main__":
    unittest.main()
