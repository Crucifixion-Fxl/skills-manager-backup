"""L2 real files/fsync/lock/process crash; systemd integration is a later boundary.

The transaction owns only three fixed recovery deployment files, never Agent
envs or runtime journals/outboxes. File publication is not service readiness.
"""
import base64
import copy
import importlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))


class RecoveryInstallFilesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve()
        self.module = importlib.import_module("recovery_install_files")
        self.targets = {
            "config": self.home / ".config/buzz/recovery/config.json",
            "service": self.home / ".config/systemd/user/buzz-agent-recovery.service",
            "timer": self.home / ".config/systemd/user/buzz-agent-recovery.timer",
        }
        self.new = {name: ("new-" + name).encode() for name in self.targets}
        for name, path in self.targets.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(("old-" + name).encode())
            path.chmod(0o600 if name == "config" else 0o644)
        self.original = self.snapshot()
        state = self.home / ".local/state/buzz-recovery/controller"
        state.mkdir(parents=True, mode=0o700)
        self.database = state / "demo-dev.sqlite3"
        with sqlite3.connect(self.database) as db:
            db.execute("CREATE TABLE pending (event TEXT)")
            db.execute("INSERT INTO pending VALUES ('signed-event-must-be-preserved')")
        self.database.chmod(0o600)
        # The host uses umask 0002. Make the fixture's ancestry genuinely
        # trusted; production must reject group-writable ancestors.
        for directory in self.home.rglob("*"):
            if directory.is_dir():
                directory.chmod(0o700)
        self.state_bytes = self.database.read_bytes()

    def snapshot(self):
        return {name: (path.read_bytes(), stat.S_IMODE(path.stat().st_mode)) if path.exists() else None
                for name, path in self.targets.items()}

    def transaction(self):
        return self.module.InstallationFiles(self.home, self.new)

    def fail_service_replace(self):
        original = os.replace
        failed = False

        def replace(source, target, *args, **kwargs):
            nonlocal failed
            if Path(target) == self.targets["service"] and not failed:
                failed = True
                raise OSError("fixture write failure: do not publish raw error")
            return original(source, target, *args, **kwargs)
        return mock.patch.object(os, "replace", side_effect=replace)

    def test_L2_REC_FILES_001_backups_precede_publication_and_success_is_not_readiness(self):
        with self.transaction() as tx:
            self.assertEqual(self.snapshot(), self.original)
            record = json.loads(tx.journal.read_text())
            self.assertEqual(record["phase"], "prepared")
            self.assertEqual(stat.S_IMODE(tx.journal.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(tx.journal.parent.stat().st_mode), 0o700)
            for name, (data, mode) in self.original.items():
                self.assertEqual(base64.b64decode(record["files"][name]["before"]["data"]), data)
                self.assertEqual(record["files"][name]["before"]["mode"], mode)
            tx.publish()
            result = tx.commit()
        self.assertTrue(result["files_published"])
        self.assertFalse(result["installed"])
        self.assertEqual(json.loads(tx.journal.read_text())["phase"], "committed")
        for name, path in self.targets.items():
            self.assertEqual(path.read_bytes(), self.new[name])
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600 if name == "config" else 0o644)
        self.assertEqual(self.database.read_bytes(), self.state_bytes)

    def test_L2_REC_FILES_002_partial_write_failure_restores_exact_old_files(self):
        with self.assertRaises(self.module.InstallationFileError) as caught:
            with self.transaction() as tx, self.fail_service_replace():
                tx.publish()
        self.assertEqual(caught.exception.code, "installation_write_failed")
        self.assertEqual(self.snapshot(), self.original)
        self.assertEqual(json.loads(tx.journal.read_text())["phase"], "rolled_back")
        self.assertNotIn("fixture write", json.dumps(caught.exception.public()))
        self.assertEqual(self.database.read_bytes(), self.state_bytes)

    def test_L2_REC_FILES_003_failed_first_install_removes_only_its_new_files(self):
        for path in self.targets.values():
            path.unlink()
        before = self.snapshot()
        with self.assertRaises(self.module.InstallationFileError):
            with self.transaction() as tx, self.fail_service_replace():
                tx.publish()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.database.read_bytes(), self.state_bytes)

    def test_L2_REC_FILES_004_post_rename_directory_fsync_failure_is_not_success(self):
        original = os.fsync
        failed = False

        def fsync(fd):
            nonlocal failed
            if (not failed and stat.S_ISDIR(os.fstat(fd).st_mode)
                    and Path(f"/proc/self/fd/{fd}").resolve() == self.targets["config"].parent
                    and self.targets["config"].read_bytes() == self.new["config"]):
                failed = True
                raise OSError("fixture directory durability failure")
            return original(fd)

        with self.assertRaises(self.module.InstallationFileError):
            with self.transaction() as tx, mock.patch.object(os, "fsync", side_effect=fsync):
                tx.publish()
        self.assertTrue(failed, "must reach post-rename directory fsync")
        self.assertEqual(self.snapshot(), self.original)

    def test_L2_REC_FILES_005_rollback_never_overwrites_concurrent_owner_changes(self):
        with self.assertRaises(self.module.InstallationFileError) as caught:
            with self.transaction() as tx:
                tx.publish()
                self.targets["service"].write_bytes(b"concurrent-owner-edit")
                raise OSError("later validation failed")
        self.assertEqual(caught.exception.code, "installation_rollback_conflict")
        self.assertEqual(self.targets["service"].read_bytes(), b"concurrent-owner-edit")
        self.assertEqual(self.targets["config"].read_bytes(), self.new["config"])
        self.assertEqual(json.loads(tx.journal.read_text())["phase"], "rollback_blocked")

    def test_L2_REC_FILES_006_private_target_symlink_or_hardlink_is_not_overwritten(self):
        path = self.targets["config"]
        other = self.home / "other-file"
        other.write_bytes(b"other-owner-file")
        other.chmod(0o600)
        for kind in ("symlink", "hardlink"):
            with self.subTest(kind=kind):
                path.unlink()
                if kind == "symlink":
                    path.symlink_to(other)
                else:
                    os.link(other, path)
                with self.assertRaises(self.module.InstallationFileError):
                    with self.transaction():
                        self.fail("unsafe target was accepted")
                self.assertEqual(other.read_bytes(), b"other-owner-file")
                path.unlink()
                path.write_bytes(b"old-config")
                path.chmod(0o600)

    def test_L2_REC_FILES_007_simultaneous_install_is_rejected_and_lock_is_released(self):
        with self.transaction() as tx:
            with self.assertRaises(self.module.InstallationFileError) as caught:
                with self.transaction():
                    self.fail("second installer entered")
            self.assertEqual(caught.exception.code, "installation_busy")
            tx.publish()
            tx.commit()
        with self.transaction() as next_tx:
            next_tx.publish()
            next_tx.commit()

    def test_L2_REC_FILES_008_real_process_exit_leaves_recoverable_backup_and_blocks_new_install(self):
        code = ("import os,sys; from pathlib import Path; "
                f"sys.path.insert(0,{str(SCRIPTS)!r}); "
                "from recovery_install_files import InstallationFiles; "
                f"tx=InstallationFiles(Path({str(self.home)!r}),{self.new!r}); "
                "tx.__enter__(); tx.publish(); os._exit(0)")
        child = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, timeout=10)
        self.assertEqual(child.returncode, 0, child.stderr.decode())
        with self.assertRaises(self.module.InstallationFileError) as caught:
            with self.transaction():
                self.fail("unfinished installation was ignored")
        self.assertEqual(caught.exception.code, "installation_interrupted")
        self.assertEqual(self.targets["config"].read_bytes(), self.new["config"])
        journals = list((self.home / ".local/state/buzz-recovery/install").glob("*/journal.json"))
        self.assertEqual(len(journals), 1)
        record = json.loads(journals[0].read_text())
        self.assertEqual(record["phase"], "published")
        self.assertEqual(base64.b64decode(record["files"]["config"]["before"]["data"]), b"old-config")

    def test_L2_REC_FILES_009_after_runtime_start_failure_preserves_new_state_for_forward_fix(self):
        with self.assertRaises(self.module.InstallationFileError) as caught:
            with self.transaction() as tx:
                tx.publish()
                tx.runtime_started()
                with sqlite3.connect(self.database) as db:
                    db.execute("INSERT INTO pending VALUES ('new-signed-pending')")
                raise OSError("actual tick could have published")
        self.assertEqual(caught.exception.code, "installation_runtime_unverified")
        self.assertEqual(json.loads(tx.journal.read_text())["phase"], "forward_fix_required")
        self.assertEqual(self.targets["config"].read_bytes(), self.new["config"])
        with sqlite3.connect(self.database) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM pending").fetchone()[0], 2)

    def test_L2_REC_FILES_010_payload_bounds_and_exact_target_set_are_checked_before_writes(self):
        for invalid in ({**self.new, "outside": b"bad"}, {**self.new, "config": b"x" * (128 * 1024 + 1)}):
            with self.subTest(keys=list(invalid)):
                with self.assertRaises(self.module.InstallationFileError):
                    with self.module.InstallationFiles(self.home, invalid):
                        self.fail("invalid payload accepted")
                self.assertEqual(self.snapshot(), self.original)

    def test_L2_REC_FILES_011_commit_rechecks_the_actual_published_files(self):
        with self.assertRaises(self.module.InstallationFileError) as caught:
            with self.transaction() as tx:
                tx.publish()
                self.targets["timer"].write_bytes(b"owner-changed-timer")
                tx.commit()
        self.assertEqual(caught.exception.code, "installation_rollback_conflict")
        self.assertEqual(self.targets["timer"].read_bytes(), b"owner-changed-timer")
        self.assertEqual(self.targets["config"].read_bytes(), self.new["config"])

    def test_L2_REC_FILES_012_corrupt_terminal_backup_is_not_treated_as_completed_history(self):
        with self.transaction() as tx:
            tx.publish()
            tx.commit()
        original = json.loads(tx.journal.read_text())
        for corruption in ("bool-version", "extra-field", "bad-base64", "wrong-mode", "missing-before"):
            with self.subTest(corruption=corruption):
                record = copy.deepcopy(original)
                if corruption == "bool-version":
                    record["version"] = True
                elif corruption == "extra-field":
                    record["ignored"] = "must-not-be-ignored"
                elif corruption == "bad-base64":
                    record["files"]["config"]["before"]["data"] = "not$base64"
                elif corruption == "wrong-mode":
                    record["files"]["config"]["before"]["mode"] = 0o666
                else:
                    record["files"]["config"].pop("before")
                tx.journal.write_text(json.dumps(record))
                with self.assertRaises(self.module.InstallationFileError) as caught:
                    with self.transaction() as unexpected:
                        unexpected.publish()
                        unexpected.commit()
                self.assertEqual(caught.exception.code, "installation_interrupted")
        tx.journal.write_text(json.dumps(original))

    def test_L2_REC_FILES_013_uncommitted_normal_exit_is_an_abort_not_success(self):
        with self.transaction() as tx:
            tx.publish()
        self.assertEqual(self.snapshot(), self.original)
        self.assertEqual(json.loads(tx.journal.read_text())["phase"], "rolled_back")

    def test_L2_REC_FILES_014_changed_backup_is_preserved_and_blocks_commit(self):
        with self.assertRaises(self.module.InstallationFileError) as caught:
            with self.transaction() as tx:
                tx.publish()
                changed = json.loads(tx.journal.read_text())
                changed["version"] = 99
                tampered = json.dumps(changed).encode()
                tx.journal.write_bytes(tampered)
                tx.commit()
        self.assertEqual(caught.exception.code, "installation_interrupted")
        self.assertEqual(tx.journal.read_bytes(), tampered)
        self.assertEqual(self.targets["config"].read_bytes(), self.new["config"])

    def test_L2_REC_FILES_015_failed_rollback_preserves_backup_and_blocks_retry(self):
        original = os.replace

        def replace(source, target, *args, **kwargs):
            if Path(target) == self.targets["config"] and Path(source).read_bytes() == b"old-config":
                raise OSError("fixture rollback disk failure")
            return original(source, target, *args, **kwargs)

        with self.assertRaises(self.module.InstallationFileError) as caught:
            with mock.patch.object(os, "replace", side_effect=replace), self.transaction() as tx:
                tx.publish()
                raise OSError("fixture post-publication validation failed")
        self.assertEqual(caught.exception.code, "installation_rollback_unverified")
        record = json.loads(tx.journal.read_text())
        self.assertEqual(record["phase"], "rolling_back")
        self.assertEqual(base64.b64decode(record["files"]["config"]["before"]["data"]), b"old-config")
        with self.assertRaises(self.module.InstallationFileError) as blocked:
            with self.transaction():
                self.fail("unverified rollback was ignored")
        self.assertEqual(blocked.exception.code, "installation_interrupted")
        self.assertEqual(self.database.read_bytes(), self.state_bytes)

    def test_L2_REC_FILES_016_unverified_backup_durability_never_changes_deployment_files(self):
        original = os.fsync

        def fsync(fd):
            path = Path(f"/proc/self/fd/{fd}").resolve()
            if (stat.S_ISDIR(os.fstat(fd).st_mode) and path.parent.name == "install"
                    and (path / "journal.json").exists()):
                raise OSError("fixture backup-directory durability failure")
            return original(fd)

        with self.assertRaises(self.module.InstallationFileError):
            with mock.patch.object(os, "fsync", side_effect=fsync), self.transaction():
                self.fail("backup durability failure was ignored")
        self.assertEqual(self.snapshot(), self.original)
        with self.assertRaises(self.module.InstallationFileError) as caught:
            with self.transaction():
                self.fail("uncertain backup was overwritten")
        self.assertEqual(caught.exception.code, "installation_interrupted")

    def test_L2_REC_FILES_017_maximum_payload_and_backups_fit_the_journal_bound(self):
        for name, path in self.targets.items():
            path.write_bytes(b"o" * self.module.LIMIT)
            self.new[name] = b"n" * self.module.LIMIT
        with self.transaction() as tx:
            tx.publish()
            tx.commit()
        with self.transaction() as another:
            another.publish()
            another.commit()
        self.assertEqual(self.database.read_bytes(), self.state_bytes)

    def manager_before(self):
        return dict(service_present=True, timer_present=True, timer_was_enabled=True, timer_was_active=True)

    def test_L2_REC_FILES_018_manager_rollback_is_inside_lock_after_exact_file_restore(self):
        calls = []

        def restore(previous):
            calls.append(previous)
            self.assertEqual(self.snapshot(), self.original)
            self.assertEqual(json.loads(tx.journal.read_text())["phase"], "rolling_back")
            with self.assertRaises(self.module.InstallationFileError) as busy:
                with self.transaction():
                    self.fail("rollback manager ran after releasing installation lock")
            self.assertEqual(busy.exception.code, "installation_busy")

        with self.assertRaises(self.module.InstallationFileError):
            with self.module.InstallationFiles(self.home, self.new, rollback_manager=restore) as tx:
                tx.manager_before(self.manager_before())
                tx.manager_step("quiesced")
                tx.publish()
                raise OSError("fixture reload failure")
        self.assertEqual(calls, [self.manager_before()])
        self.assertEqual(json.loads(tx.journal.read_text())["phase"], "rolled_back")

    def test_L2_REC_FILES_019_failed_manager_restore_cannot_mark_rollback_completed(self):
        def unavailable(previous):
            raise ValueError("private manager error")

        with self.assertRaises(self.module.InstallationFileError) as caught:
            with self.module.InstallationFiles(self.home, self.new, rollback_manager=unavailable) as tx:
                tx.manager_before(self.manager_before())
                tx.manager_step("quiesced")
                tx.publish()
        self.assertEqual(caught.exception.code, "installation_rollback_unverified")
        self.assertEqual(json.loads(tx.journal.read_text())["phase"], "rolling_back")
        self.assertEqual(self.snapshot(), self.original)
        with self.assertRaises(self.module.InstallationFileError):
            with self.transaction():
                self.fail("unverified manager rollback was ignored")

    def test_L2_REC_FILES_020_no_old_manager_restore_after_runtime_may_have_published(self):
        restore = mock.Mock()
        with self.assertRaises(self.module.InstallationFileError):
            with self.module.InstallationFiles(self.home, self.new, rollback_manager=restore) as tx:
                tx.manager_before(self.manager_before())
                tx.manager_step("quiesced")
                tx.publish()
                tx.manager_step("reloaded")
                tx.runtime_started()
                raise OSError("tick failure")
        restore.assert_not_called()
        self.assertEqual(self.targets["config"].read_bytes(), self.new["config"])

    def test_L2_REC_FILES_021_each_manager_step_is_durable_and_complete_before_commit(self):
        with self.module.InstallationFiles(self.home, self.new, rollback_manager=lambda prior: None) as tx:
            before = self.manager_before()
            tx.manager_before(before)
            before["timer_was_enabled"] = False  # Caller mutation cannot change the durable baseline.
            self.assertIsInstance(json.loads(tx.journal.read_text()).get("manager"), dict)
            self.assertEqual(json.loads(tx.journal.read_text())["manager"]["before"], self.manager_before())
            tx.manager_step("quiesced")
            tx.publish()
            tx.manager_step("reloaded")
            tx.runtime_started()
            with self.assertRaises(self.module.InstallationFileError):
                tx.commit()
            for step in ("tick_completed", "timer_enabled", "post_audit_passed"):
                tx.manager_step(step)
                self.assertEqual(json.loads(tx.journal.read_text())["manager"]["steps"][-1], step)
            tx.commit()
        with self.transaction() as next_tx:
            next_tx.publish()
            next_tx.commit()

    def test_L2_REC_FILES_022_malformed_or_out_of_order_manager_evidence_never_advances_journal(self):
        with self.module.InstallationFiles(self.home, self.new, rollback_manager=lambda prior: None) as tx:
            for bad in ({}, {**self.manager_before(), "timer_present": False},
                        {**self.manager_before(), "timer_was_active": 1},
                        {**self.manager_before(), "ignored": "secret"}):
                previous = tx.journal.read_bytes()
                with self.subTest(before=bad), self.assertRaises(self.module.InstallationFileError):
                    tx.manager_before(bad)
                self.assertEqual(tx.journal.read_bytes(), previous)
            tx.manager_before(self.manager_before())
            for step in ("reloaded", "tick_completed", "unknown"):
                previous = tx.journal.read_bytes()
                with self.subTest(step=step), self.assertRaises(self.module.InstallationFileError):
                    tx.manager_step(step)
                self.assertEqual(tx.journal.read_bytes(), previous)

    def test_L2_REC_FILES_023_corrupt_terminal_manager_evidence_blocks_following_install(self):
        with self.transaction() as tx:
            tx.publish()
            tx.commit()
        record = json.loads(tx.journal.read_text())
        record["manager"] = dict(before=self.manager_before(), steps=["post_audit_passed"])
        tx.journal.write_text(json.dumps(record))
        with self.assertRaises(self.module.InstallationFileError):
            with self.transaction():
                self.fail("incomplete manager evidence accepted as committed installation")
