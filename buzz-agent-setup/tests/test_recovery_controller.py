"""L2 controller wiring: real process, real files/SQLite, relay and systemd boundary fakes."""
import copy
import fcntl
import hashlib
import json
import os
import select
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from recovery_controller import run_round, load_config
from test_agent_recovery import Relay, CHANNELS, snapshot, seed_originals
from recovery_relay import wire
from test_recovery_runtime import prewarm_child

KEY = "1" * 64
OWNER = wire._signer_pubkey(KEY)
AGENT_KEY = "2" * 64
AGENT = wire._signer_pubkey(AGENT_KEY)


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.runtime = self.base / "runtime"
        self.runtime.mkdir(mode=0o700)
        self.state = self.base / "state"
        self.state.mkdir(mode=0o700)
        self.child = prewarm_child()
        self.pid = self.child.pid
        self.digest = hashlib.sha256(Path(f"/proc/{self.pid}/exe").read_bytes()).hexdigest()
        stat = Path(f"/proc/{self.pid}/stat").read_text().rsplit(") ", 1)[1].split()
        self.current = snapshot("00000000-0000-0000-0000-000000000099")
        self.current.update(agent_pubkey=AGENT, relay="https://relay.invalid", pid=self.pid,
                            process_start_ticks=stat[19], boot_id=Path("/proc/sys/kernel/random/boot_id").read_text().strip())
        self.current["runtime_policy"]["owner"] = OWNER
        self.old = copy.deepcopy(self.current)
        self.old.update(generation="00000000-0000-0000-0000-000000000098", boot_id="00000000-0000-0000-0000-000000000001",
                        active={"00000000-0000-0000-0000-000000000001": [dict(channel=CHANNELS[0], root="a" * 64)]})
        seed_originals(self.old)
        for sources in self.old["input_sources"].values():
            sources[0]["signed_author"] = OWNER
        self.write_snapshot(self.current)
        self.write_snapshot(self.old)
        self.owner_env = self.base / "owner.env"
        self.write(self.owner_env, f"BUZZ_PRIVATE_KEY={KEY}\nBUZZ_RELAY_URL=https://relay.invalid\n")
        self.agent_env = self.base / "agent.env"
        self.write(self.agent_env, f"BUZZ_PRIVATE_KEY={AGENT_KEY}\nBUZZ_ACP_AGENT_OWNER={OWNER}\nBUZZ_RELAY_URL=https://relay.invalid\nBUZZ_ACP_RECOVERY_DIR={self.runtime}\nBUZZ_ACP_RECOVERY_REVISION={'a' * 40}\nBUZZ_ACP_BINARY_SHA256={self.digest}\n")
        self.config = dict(version=1, owner_pubkey=OWNER, relay_url="https://relay.invalid", relay_pubkey="b" * 64,
                           owner_env_file=str(self.owner_env), state_dir=str(self.state), agents=[
                               dict(name="test-dev", pubkey=AGENT, unit="buzz-test-dev.service", env_file=str(self.agent_env),
                                    journal_dir=str(self.runtime), revision="a" * 40, binary_sha256=self.digest)])
        self.relay = Relay()
        self.relay.owner = OWNER

    def tearDown(self):
        self.child.terminate()
        self.child.wait(timeout=5)
        self.child.stdout.close()
        self.tmp.cleanup()

    def write(self, path, content):
        path.write_text(content)
        path.chmod(0o600)

    def write_snapshot(self, value):
        self.write(self.runtime / (value["generation"] + ".json"), json.dumps(value))

    def run_round(self):
        return run_round(self.config, main_pid=lambda unit: self.pid, transport=self.relay)

    def test_round_discovers_previous_boot_thread_and_dispatches_automatically(self):
        result = self.run_round()
        self.assertEqual(result["agents"]["test-dev"]["continued"], 1)
        self.assertEqual(self.run_round()["agents"]["test-dev"]["continued"], 0)

    def test_missing_or_malformed_actual_policy_never_uses_desired_env_as_authority(self):
        valid = dict(owner=OWNER, respond_to="allowlist", allowlist=["a" * 64])
        # Keep public and actual modes equal so a drift guard cannot hide a
        # missing private-schema check (especially duplicates/extra fields).
        self.relay.validate_route = lambda *args: "allowlist"
        for n, policy in enumerate((None, {}, {**valid, "owner": "bad"},
                                    {**valid, "respond_to": "unknown"},
                                    {**valid, "allowlist": ["a" * 64, "a" * 64]},
                                    {**valid, "allowlist": "a" * 64},
                                    {**valid, "extra": True})):
            with self.subTest(policy=policy):
                state = self.base / ("policy-case-" + str(n))
                state.mkdir(mode=0o700)
                self.config["state_dir"] = str(state)
                self.current["runtime_policy"] = policy
                self.write_snapshot(self.current)
                entry = self.run_round()["agents"]["test-dev"]
                self.assertEqual(entry["continued"], 0, "missing actual runtime policy granted authority")
                self.assertTrue(entry["errors"])
                self.assertFalse(any(e["content"] == "@test-dev continue" for e in self.relay.events.values()))

    def test_actual_owner_mismatch_and_nobody_mode_cannot_be_overridden_by_owner_env(self):
        for n, policy in enumerate((dict(owner="a" * 64, respond_to="anyone", allowlist=[]),
                                    dict(owner=OWNER, respond_to="nobody", allowlist=[]))):
            with self.subTest(policy=policy):
                state = self.base / ("denied-policy-" + str(n))
                state.mkdir(mode=0o700)
                self.config["state_dir"] = str(state)
                self.current["runtime_policy"] = policy
                self.write_snapshot(self.current)
                entry = self.run_round()["agents"]["test-dev"]
                self.assertEqual(entry["continued"], 0)
                self.assertIn("runtime_policy_denied", entry["errors"])
                self.assertTrue(any("owner" in e["content"] and not any(t[0] == "p" for t in e["tags"])
                                    for e in self.relay.events.values()))

    def test_public_policy_drift_never_overrides_actual_running_policy(self):
        self.current["runtime_policy"] = dict(owner=OWNER, respond_to="owner-only", allowlist=[])
        self.write_snapshot(self.current)
        self.relay.validate_route = lambda *args: "anyone"
        entry = self.run_round()["agents"]["test-dev"]
        self.assertEqual(entry["continued"], 0)
        self.assertIn("runtime_policy_denied", entry["errors"])

    def test_sleeping_provider_is_not_woken_under_mismatched_actual_authority(self):
        self.current.update(phase="starting", runtime_policy=dict(owner=OWNER, respond_to="nobody", allowlist=[]))
        self.write_snapshot(self.current)
        entry = self.run_round()["agents"]["test-dev"]
        self.assertIn("runtime_policy_denied", entry["errors"])
        self.assertFalse(entry.get("prewarming", False))
        self.assertFalse(select.select([self.child.stdout], [], [], 0.05)[0])

    def test_verified_sleeping_provider_is_signalled_but_not_continued_before_ready(self):
        self.current["phase"] = "starting"
        self.write_snapshot(self.current)
        entry = self.run_round()["agents"]["test-dev"]
        self.assertEqual(entry["continued"], 0)
        self.assertIn("runtime_not_ready", entry["errors"])
        self.assertTrue(select.select([self.child.stdout], [], [], 1)[0], "verified idle provider never received prewarm")
        self.assertEqual(self.child.stdout.readline(), b"prewarmed\n")
        self.assertTrue(entry["prewarming"])
        self.assertFalse(any("启动成功" in e["content"] for e in self.relay.events.values()))
        self.current["phase"] = "ready"
        self.write_snapshot(self.current)
        self.assertEqual(self.run_round()["agents"]["test-dev"]["continued"], 1)

    def test_no_pending_work_does_not_wake_sleeping_provider(self):
        self.current["phase"] = "starting"
        self.old["active"] = {}
        self.old["triggers"] = {}
        self.old["receipts"] = dict.fromkeys(self.old["receipts"], "completed")
        self.write_snapshot(self.current)
        self.write_snapshot(self.old)
        entry = self.run_round()["agents"]["test-dev"]
        self.assertEqual(entry["continued"], 0)
        self.assertFalse(entry.get("prewarming", False))
        self.assertEqual(entry["errors"], [])
        self.assertFalse(select.select([self.child.stdout], [], [], 0.05)[0])

    def test_unverified_revision_or_route_cannot_request_prewarm(self):
        self.current["phase"] = "starting"
        self.write_snapshot(self.current)
        for invalid in ("revision", "route", "stopping"):
            with self.subTest(invalid=invalid):
                self.config["agents"][0]["revision"] = ("b" if invalid == "revision" else "a") * 40
                self.relay.refuse = {CHANNELS[0]} if invalid == "route" else set()
                self.current["phase"] = "stopping" if invalid == "stopping" else "starting"
                self.write_snapshot(self.current)
                entry = self.run_round()["agents"]["test-dev"]
                self.assertFalse(entry.get("prewarming", False))
                self.assertFalse(select.select([self.child.stdout], [], [], 0.05)[0])

    def test_private_native_writer_lock_allows_recovery_without_taking_the_lock(self):
        path = self.runtime / ".writer.lock"
        self.write(path, "")
        before = path.stat()
        with path.open("rb") as writer:
            fcntl.flock(writer, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.run_round()["agents"]["test-dev"]
            self.assertEqual(result["continued"], 1)
            self.assertEqual(result["errors"], [])
        after = path.stat()
        self.assertEqual((before.st_ino, before.st_size, before.st_mtime_ns),
                         (after.st_ino, after.st_size, after.st_mtime_ns))

    def test_unsafe_native_writer_lock_never_dispatches(self):
        path = self.runtime / ".writer.lock"
        target = self.base / "lock-target"
        self.write(target, "")
        for variant in ("symlink", "hardlink", "permissive", "nonempty", "directory"):
            with self.subTest(variant=variant):
                if variant == "symlink":
                    path.symlink_to(self.owner_env)
                elif variant == "hardlink":
                    os.link(target, path)
                elif variant == "directory":
                    path.mkdir(mode=0o700)
                else:
                    self.write(path, "not a lock" if variant == "nonempty" else "")
                    if variant == "permissive":
                        path.chmod(0o644)
                try:
                    result = self.run_round()["agents"]["test-dev"]
                    self.assertEqual(result["continued"], 0)
                    self.assertIn("recovery_state_or_service_unverified", result["errors"])
                    self.assertEqual(self.relay.published, [])
                finally:
                    path.rmdir() if variant == "directory" else path.unlink()

    def test_fifo_writer_lock_is_rejected_without_blocking_the_controller(self):
        os.mkfifo(self.runtime / ".writer.lock", mode=0o600)
        script = ("import sys; from recovery_controller import load_snapshots; "
                  "load_snapshots(sys.argv[1])")
        result = subprocess.run([sys.executable, "-c", script, str(self.runtime)],
                                env=dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "scripts")),
                                capture_output=True, text=True, timeout=3, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ValueError", result.stderr)

    def test_unrecognized_hidden_lock_is_not_ignored(self):
        self.write(self.runtime / ".other.lock", "")
        result = self.run_round()["agents"]["test-dev"]
        self.assertEqual(result["continued"], 0)
        self.assertIn("recovery_state_or_service_unverified", result["errors"])
        self.assertEqual(self.relay.published, [])

    def test_owner_https_and_agent_wss_are_the_same_verified_community(self):
        self.config["relay_url"] = "wss://relay.invalid"
        self.write(self.agent_env, self.agent_env.read_text().replace("https://relay.invalid", "wss://relay.invalid"))
        for value in (self.current, self.old):
            value["relay"] = "wss://relay.invalid"
            self.write_snapshot(value)
        self.assertEqual(self.run_round()["agents"]["test-dev"]["continued"], 1)

    def test_concurrent_live_generation_is_not_mistaken_for_interruption(self):
        self.old["boot_id"] = self.current["boot_id"]
        self.write_snapshot(self.old)
        result = self.run_round()
        self.assertEqual(result["agents"]["test-dev"]["continued"], 0)
        self.assertIn("ambiguous_live_generations", result["agents"]["test-dev"]["errors"])

    def test_dead_agent_posts_known_thread_failure_not_continue(self):
        self.child.terminate()
        self.child.wait(timeout=5)
        result = self.run_round()
        self.assertEqual(result["agents"]["test-dev"]["continued"], 0)
        self.assertTrue(result["agents"]["test-dev"]["errors"])
        self.assertTrue(any("启动状态尚未核实" in e["content"] for e in self.relay.events.values()))

    def test_wrong_loaded_revision_never_claims_startup_success(self):
        self.config["agents"][0]["revision"] = "b" * 40
        result = self.run_round()
        self.assertEqual(result["agents"]["test-dev"]["continued"], 0)
        self.assertTrue(result["agents"]["test-dev"]["errors"])
        self.assertFalse(any("启动成功" in e["content"] for e in self.relay.events.values()))

    def test_symlink_snapshot_is_rejected_without_reading_target(self):
        path = self.runtime / (self.old["generation"] + ".json")
        path.unlink()
        path.symlink_to(self.owner_env)
        result = self.run_round()
        self.assertTrue(result["agents"]["test-dev"]["errors"])
        self.assertEqual(self.relay.published, [])

    def test_config_rejects_permissive_files_duplicate_keys_and_duplicate_agents(self):
        path = self.base / "config.json"
        self.write(path, json.dumps(self.config))
        self.assertEqual(load_config(path), self.config)
        path.chmod(0o644)
        with self.assertRaises(ValueError):
            load_config(path)
        self.write(path, '{"version":1,"version":1}')
        with self.assertRaises(ValueError):
            load_config(path)
        self.config["agents"].append(copy.deepcopy(self.config["agents"][0]))
        self.write(path, json.dumps(self.config))
        with self.assertRaises(ValueError):
            load_config(path)

    def test_one_broken_agent_does_not_prevent_valid_agent(self):
        bad = copy.deepcopy(self.config["agents"][0])
        bad.update(name="bad-dev", pubkey="c" * 64, unit="buzz-bad-dev.service", journal_dir=str(self.base / "missing"))
        self.config["agents"].append(bad)
        result = self.run_round()
        self.assertEqual(result["agents"]["test-dev"]["continued"], 1)
        self.assertTrue(result["agents"]["bad-dev"]["errors"])


if __name__ == "__main__":
    unittest.main()
