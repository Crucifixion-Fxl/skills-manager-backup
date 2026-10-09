"""REC-001/018: installer planning uses the whole canonical inventory, read-only.

L2: actual deployment files and the real alignment auditor. Only the native
capability subprocess boundary is replaced; no service or relay is mutated.
"""
import copy
import importlib
import json
from pathlib import Path
import sys
from unittest import mock

from test_audit_local_alignment import LocalAlignmentFixture, EXPECTED

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from recovery_relay import wire
from audit_local_alignment import load_prompt_contract


class RecoveryInstallFixture(LocalAlignmentFixture):
    def setUp(self):
        super().setUp()
        self.installer = importlib.import_module("install_agent_recovery")
        self.initial_recovery_files = {}
        for path in (self.home / ".config/buzz/recovery/config.json",
                     self.units / "buzz-agent-recovery.service",
                     self.units / "buzz-agent-recovery.timer"):
            self.initial_recovery_files[path.name] = path.read_text()
            path.unlink()
        self.owner = wire._signer_pubkey("1" * 64)
        self.agent = wire._signer_pubkey("2" * 64)
        env_file = self.agents / "demo-dev.env"
        text = env_file.read_text().replace("'fixture-secret-never-print'", "2" * 64)
        text = text.replace("BUZZ_ACP_AGENT_OWNER=owner", "BUZZ_ACP_AGENT_OWNER=" + self.owner)
        self.write(env_file, text)
        self.owner_env = self.home / "owner.env"
        self.write(self.owner_env, f"BUZZ_PRIVATE_KEY={'1' * 64}\nBUZZ_RELAY_URL=wss://relay.example.test\n")
        self.probe = mock.patch.object(self.installer, "probe_binary", return_value=None)
        self.probe.start()
        self.addCleanup(self.probe.stop)

    def plan(self):
        return self.installer.prepare_install(
            home=self.home, revision=EXPECTED, owner_env_file=self.owner_env,
            relay_pubkey="b" * 64,
        )


class RecoveryInstallPlanTests(RecoveryInstallFixture):
    def test_L2_REC_INSTALL_001_new_install_plans_default_on_without_writes(self):
        before = {p: p.read_bytes() for p in self.home.rglob("*") if p.is_file() and not p.is_symlink()}
        plan = self.plan()
        self.assertEqual(plan["config"]["agents"][0]["pubkey"], self.agent)
        self.assertEqual([a["name"] for a in plan["config"]["agents"]], ["demo-dev"])
        self.assertEqual(plan["config"]["owner_pubkey"], self.owner)
        self.assertTrue(plan["default_enabled"])
        self.assertEqual(plan["timer_seconds"], 15)
        self.assertFalse(plan["installed"])
        after = {p: p.read_bytes() for p in self.home.rglob("*") if p.is_file() and not p.is_symlink()}
        self.assertEqual(before, after, "planning must not materialize config or service files")

    def test_L2_REC_INSTALL_002_missing_agent_env_blocks_instead_of_omitting_agent(self):
        (self.agents / "demo-dev.env").unlink()
        with self.assertRaises(self.installer.PlanError) as caught:
            self.plan()
        self.assertEqual(caught.exception.code, "alignment_required")
        self.assertIn("demo-dev", json.dumps(caught.exception.public()))

    def test_L2_REC_INSTALL_003_owner_mismatch_never_rewrites_agent_authority(self):
        path = self.agents / "demo-dev.env"
        self.write(path, path.read_text().replace(self.owner, "c" * 64))
        before = path.read_bytes()
        with self.assertRaises(self.installer.PlanError) as caught:
            self.plan()
        self.assertEqual(caught.exception.code, "agent_identity_unverified")
        self.assertEqual(path.read_bytes(), before)
        self.assertNotIn("2" * 64, json.dumps(caught.exception.public()))

    def test_L2_REC_INSTALL_004_does_not_silently_retire_old_inventory(self):
        config = self.plan()["config"]
        old = copy.deepcopy(config["agents"][0])
        old.update(name="removed-dev", pubkey="e" * 64, unit="buzz-local-removed-dev.service",
                   env_file=str(self.agents / "removed-dev.env"),
                   journal_dir=str(self.home / ".local/state/buzz-recovery/removed-dev/runtime"))
        config["agents"].append(old)
        path = self.home / ".config/buzz/recovery/config.json"
        self.write(path, json.dumps(config))
        before = path.read_bytes()
        with self.assertRaises(self.installer.PlanError) as caught:
            self.plan()
        self.assertEqual(caught.exception.code, "removed_agent_requires_reconciliation")
        self.assertEqual(path.read_bytes(), before)

    def test_L2_REC_INSTALL_005_existing_owner_state_cannot_be_reassigned(self):
        config = self.plan()["config"]
        config["owner_pubkey"] = "c" * 64
        self.write(self.home / ".config/buzz/recovery/config.json", json.dumps(config))
        with self.assertRaises(self.installer.PlanError) as caught:
            self.plan()
        self.assertEqual(caught.exception.code, "recovery_state_binding_changed")

    def test_L2_REC_INSTALL_006_same_owner_target_upgrade_preserves_state_path(self):
        config = self.plan()["config"]
        config["agents"][0]["revision"] = "d" * 40
        self.write(self.home / ".config/buzz/recovery/config.json", json.dumps(config))
        result = self.plan()
        self.assertEqual(result["config"]["state_dir"], config["state_dir"])
        self.assertEqual(result["config"]["agents"][0]["revision"], EXPECTED)
        self.assertFalse(result["installed"])

    def test_L2_REC_INSTALL_007_unverified_binary_is_a_visible_blocker(self):
        self.probe.stop()
        # The fixture's real ELF /usr/bin/true has no recovery capability.
        with self.assertRaises(self.installer.PlanError) as caught:
            self.plan()
        self.assertEqual(caught.exception.code, "binary_recovery_capability_unverified")
        self.assertIn("升级", caught.exception.public()["remediation"])

    def test_L2_REC_INSTALL_008_same_revision_content_drift_is_not_trusted(self):
        path = self.release / "scripts/recovery_controller.py"
        path.chmod(0o644)
        path.write_text("# tampered\n")
        path.chmod(0o444)
        with self.assertRaises(self.installer.PlanError) as caught:
            self.plan()
        self.assertEqual(caught.exception.code, "alignment_required")

    def test_L2_REC_INSTALL_009_full_inventory_includes_non_join_managed_platform(self):
        original = self.plan()["config"]
        self.write(self.home / ".config/buzz/recovery/config.json", json.dumps(original))
        for source, target in (
            (self.agents / "demo-dev.env", self.agents / "platform-desk.env"),
            (self.agents / "demo/responsible/demo-dev.json", self.agents / "demo/responsible/platform-desk.json"),
            (self.units / "buzz-local-demo-dev.service", self.units / "buzz-local-platform-desk.service"),
            (self.workdir / ".claude/settings.local.json",
             self.home / "buzz-agent-work/platform-desk/.claude/settings.local.json"),
        ):
            value = source.read_text().replace("demo-dev", "platform-desk")
            if target.suffix == ".env":
                value = "\n".join(line for line in value.splitlines() if not line.startswith("BUZZ_ACP_CHANNELS="))
                value = value.replace("BUZZ_PRIVATE_KEY=" + "2" * 64, "BUZZ_PRIVATE_KEY=" + "3" * 64) + "\n"
            self.write(target, value, source.stat().st_mode & 0o777)
        (self.home / "buzz-agent-work/platform-desk/.git").mkdir()
        state = self.home / ".local/state/buzz/platform-desk"
        state.mkdir()
        state.chmod(0o700)
        roles = self.agents / "local-alignment-roles.json"
        value = json.loads(roles.read_text())
        value["roles"]["platform-desk"] = "platform-desk"
        self.write(roles, json.dumps(value))
        contract = load_prompt_contract()
        prompt = "\n".join(marker.replace("<RELEASE>", str(self.release))
                           for marker in [*contract["common"]["required"], *contract["platform-desk"]["required"]])
        self.write(self.agents / "platform-desk.prompt.md", prompt)
        result = self.plan()
        self.assertEqual([a["name"] for a in result["config"]["agents"]], ["demo-dev", "platform-desk"])
        self.assertEqual(result["config"]["agents"][1]["pubkey"], wire._signer_pubkey("3" * 64))
        self.assertEqual(result["config"]["agents"][0], original["agents"][0])
        self.assertEqual(len(json.loads((self.home / ".config/buzz/join/config.json").read_text())["agents"]), 1)

    def test_L2_REC_INSTALL_010_agent_relay_or_runtime_path_mismatch_is_visible(self):
        path = self.agents / "demo-dev.env"
        original = path.read_text()
        for old, new in (("wss://relay.example.test", "wss://other.example.test"),
                         (str(self.home / ".local/state/buzz-recovery/demo-dev/runtime"), "/tmp/foreign-journal")):
            with self.subTest(field=old):
                self.write(path, original.replace(old, new))
                with self.assertRaises(self.installer.PlanError) as caught:
                    self.plan()
                self.assertIn(caught.exception.code, ("agent_identity_unverified", "alignment_required"))
                self.assertFalse(caught.exception.public()["installed"])
        self.write(path, original)

    def test_L2_REC_INSTALL_011_existing_agent_identity_and_unsafe_config_cannot_be_rebound(self):
        original = self.plan()["config"]
        path = self.home / ".config/buzz/recovery/config.json"
        for field, value in (("pubkey", "e" * 64), ("journal_dir", "/tmp/foreign-journal"),
                             ("env_file", str(self.home / "foreign.env"))):
            with self.subTest(field=field):
                config = copy.deepcopy(original)
                config["agents"][0][field] = value
                self.write(path, json.dumps(config))
                with self.assertRaises(self.installer.PlanError) as caught:
                    self.plan()
                self.assertEqual(caught.exception.code, "recovery_state_binding_changed")
        self.write(path, json.dumps(original), 0o644)
        with self.assertRaises(self.installer.PlanError) as caught:
            self.plan()
        self.assertEqual(caught.exception.code, "existing_recovery_config_unverified")

    def test_L2_REC_INSTALL_012_host_audit_uses_actual_user_manager_runtime_directory(self):
        import os
        import audit_local_alignment
        runtime = self.home / "user-runtime"
        runtime.mkdir(mode=0o700)
        calls = []

        def unavailable(arguments, **kwargs):
            if arguments[:2] == ["/usr/bin/systemctl", "--user"]:
                calls.append(kwargs["env"])
            raise OSError("fixture user manager unavailable")

        # Only the host-home selector and external subprocess boundary differ.
        # The real auditor must discover unknown state and block installation.
        with mock.patch.object(Path, "home", return_value=self.home), \
                mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(runtime)}), \
                mock.patch.object(audit_local_alignment, "run_bounded_text", side_effect=unavailable):
            with self.assertRaises(self.installer.PlanError) as caught:
                self.plan()
        self.assertEqual(caught.exception.code, "alignment_required")
        self.assertTrue(calls, "the host audit must consult the actual user manager")
        self.assertTrue(all(env.get("XDG_RUNTIME_DIR") == str(runtime) for env in calls))
        self.assertTrue(any(check["status"] == "unknown" for check in caught.exception.checks))

    def test_L2_REC_INSTALL_013_existing_unsafe_recovery_unit_is_not_an_installation_exemption(self):
        config = self.plan()["config"]
        self.write(self.home / ".config/buzz/recovery/config.json", json.dumps(config))
        service = self.units / "buzz-agent-recovery.service"
        timer = self.units / "buzz-agent-recovery.timer"
        self.write(timer, self.initial_recovery_files[timer.name], 0o644)
        canonical = self.initial_recovery_files[service.name]
        for text in (canonical + "ExecStop=/bin/false\n", canonical.replace("-I ", ""),
                     canonical.replace("PATH=/usr/bin:/bin", "PATH=/untrusted"),
                     canonical.replace("UMask=0077", "UMask=0000")):
            with self.subTest(text=text):
                self.write(service, text, 0o644)
                with self.assertRaises(self.installer.PlanError) as caught:
                    self.plan()
                self.assertEqual(caught.exception.code, "alignment_required")
        self.write(service, canonical, 0o666)
        with self.assertRaises(self.installer.PlanError):
            self.plan()

    def test_L2_REC_INSTALL_014_plan_retains_full_before_audit_for_exact_topology_comparison(self):
        report = self.plan().get("before_audit")
        self.assertIsInstance(report, dict)
        self.assertEqual(report["inventory_ids"]["recovery_services"], [])
        self.assertEqual(report["inventory_ids"]["agents"], ["demo-dev"])
