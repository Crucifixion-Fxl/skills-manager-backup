"""L2: only the approved first recovery service/timer pair may change topology."""
import copy

from test_audit_local_alignment import LocalAlignmentFixture, EXPECTED, load_module, load_compare_module


class RecoveryInstallReceiptTests(LocalAlignmentFixture):
    def setUp(self):
        super().setUp()
        self.compare = load_compare_module().compare
        self.after = load_module().audit_home(self.home, EXPECTED)
        self.assertTrue(self.after["ok"])
        for name in ("buzz-agent-recovery.service", "buzz-agent-recovery.timer"):
            (self.units / name).unlink()
        self.before = load_module().audit_home(self.home, EXPECTED)
        self.assertEqual(self.before["inventory"]["recovery_services"], 0)
        self.assertTrue(any(c["code"] == "required_service_not_deployed"
                            and c["category"] == "recovery_release" for c in self.before["checks"]))

    def test_L2_REC_RECEIPT_001_explicit_first_install_allows_only_canonical_pair(self):
        try:
            self.compare(self.before, self.after, EXPECTED, allow_recovery_install=True)
        except ValueError as error:
            self.fail(f"approved first recovery pair was rejected: {error}")

    def test_L2_REC_RECEIPT_002_no_implicit_exception_to_topology_preservation(self):
        with self.assertRaises(ValueError):
            self.compare(self.before, self.after, EXPECTED)

    def test_L2_REC_RECEIPT_003_install_permission_cannot_hide_other_identity_changes(self):
        for identity in ("agents", "agent_services", "harnesses", "sync_services", "join_services"):
            with self.subTest(identity=identity):
                after = copy.deepcopy(self.after)
                after["inventory_ids"][identity] = ["unrelated-replacement"]
                with self.assertRaises(ValueError):
                    self.compare(self.before, after, EXPECTED, allow_recovery_install=True)

    def test_L2_REC_RECEIPT_004_noncanonical_service_or_timer_is_not_the_approved_addition(self):
        for identity, wrong in (("recovery_services", "buzz-foreign-recovery.service"),
                                ("persistent_timers", "buzz-foreign-recovery.timer")):
            with self.subTest(identity=identity):
                after = copy.deepcopy(self.after)
                original = "buzz-agent-recovery." + ("service" if identity == "recovery_services" else "timer")
                after["inventory_ids"][identity] = sorted(wrong if v == original else v for v in after["inventory_ids"][identity])
                with self.assertRaises(ValueError):
                    self.compare(self.before, after, EXPECTED, allow_recovery_install=True)

    def test_L2_REC_RECEIPT_005_existing_install_is_stable_and_removal_never_allowed(self):
        self.compare(self.after, self.after, EXPECTED, allow_recovery_install=True)
        with self.assertRaises(ValueError):
            self.compare(self.after, self.before, EXPECTED, allow_recovery_install=True)

    def test_L2_REC_RECEIPT_006_post_audit_must_pass_and_old_shape_is_not_invented(self):
        for before, after in ((self.before, self.before), (copy.deepcopy(self.before), self.after)):
            with self.subTest(post_pass=after["ok"]):
                if after is self.after:
                    before["inventory"].pop("recovery_services")
                    before["inventory_ids"].pop("recovery_services")
                with self.assertRaises(ValueError):
                    self.compare(before, after, EXPECTED, allow_recovery_install=True)
