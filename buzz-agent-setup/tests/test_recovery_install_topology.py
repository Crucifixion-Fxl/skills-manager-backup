"""L2: recovery units belong to the full installation topology, not a side list."""
import json

from test_audit_local_alignment import LocalAlignmentFixture, EXPECTED, load_module


class RecoveryTopologyTests(LocalAlignmentFixture):
    def audit(self, runtime=None):
        return load_module().Auditor(self.home, EXPECTED, runtime_dir=runtime).run()

    def test_L2_REC_TOPO_001_controller_and_timer_have_exact_inventory_identities(self):
        report = self.audit()
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["inventory"].get("recovery_services"), 1)
        self.assertEqual(report["inventory_ids"].get("recovery_services"), ["buzz-agent-recovery.service"])
        self.assertIn("buzz-agent-recovery.timer", report["inventory_ids"]["persistent_timers"])

    def test_L2_REC_TOPO_002_orphan_recovery_timer_is_unknown_inventory(self):
        (self.units / "buzz-agent-recovery.service").unlink()
        report = self.audit()
        self.assertTrue(any(c["code"] == "orphan_timer_without_service"
                            and c["subject"] == "timer:buzz-agent-recovery.timer" for c in report["checks"]))

    def test_L2_REC_TOPO_003_shadow_recovery_units_are_detected_without_reading_secrets(self):
        (self.units / "buzz-agent-recovery.service").unlink()
        shadow = self.home / ".local/share/systemd/user/buzz-agent-recovery.service"
        self.write(shadow, "SHADOW-SECRET-DO-NOT-PRINT", 0o644)
        report = self.audit()
        self.assertIn("buzz-agent-recovery.service", report["inventory_ids"]["lookup_only_units"])
        self.assertNotIn("SHADOW-SECRET-DO-NOT-PRINT", json.dumps(report))

    def test_L2_REC_TOPO_004_transient_recovery_timer_is_not_a_persistent_install(self):
        runtime = self.home / "user-runtime"
        self.write(runtime / "systemd/transient/buzz-agent-recovery.timer", "TRANSIENT-SECRET-DO-NOT-PRINT")
        runtime.chmod(0o700)
        report = self.audit(runtime)
        self.assertIn("buzz-agent-recovery.timer", report["inventory_ids"]["transient_units"])
        self.assertFalse(report["ok"])
        self.assertNotIn("TRANSIENT-SECRET-DO-NOT-PRINT", json.dumps(report))
