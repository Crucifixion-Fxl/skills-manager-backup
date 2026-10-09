"""Deterministic public journal reads across atomic publication; private files only."""
import contextlib
import copy
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import recovery_controller as controller
import recovery_inventory
from agent_recovery import _validate
from test_agent_recovery import CHANNELS, snapshot


class RecoverySnapshotAtomicReadsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name).resolve()
        self.directory.chmod(0o700)
        self.generation = "00000000-0000-4000-8000-000000000099"
        self.path = self.directory / (self.generation + ".json")
        self.starting = {"generation": self.generation, "phase": "starting"}
        self.ready = {**self.starting, "phase": "ready"}
        self.write(self.path, self.starting)

    @staticmethod
    def write(path, value):
        raw = value if isinstance(value, bytes) else json.dumps(value).encode()
        with path.open("wb") as output:
            os.fchmod(output.fileno(), 0o600)
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())

    def publish(self, value=None):
        temporary = self.directory / ("." + self.path.name + ".tmp")
        self.write(temporary, self.ready if value is None else value)
        os.replace(temporary, self.path)

    @contextlib.contextmanager
    def at_read(self, point, action=None, *, repeated=False):
        """Publish synchronously at a real descriptor boundary; no sleeps."""
        opened, fstat = os.open, os.fstat
        descriptors = set()
        state = {"fires": 0, "opens": 0, "old_nlinks": []}
        action = self.publish if action is None else action

        def fire(fd):
            before = fstat(fd)
            action()
            after = fstat(fd)
            state["fires"] += 1
            state["old_nlinks"].append((before.st_nlink, after.st_nlink))

        def open_file(path, flags, *args, **kwargs):
            fd = opened(path, flags, *args, **kwargs)
            if Path(path) == self.path:
                descriptors.add(fd)
                state["opens"] += 1
                if point == "after-open" and (repeated or state["fires"] == 0):
                    fire(fd)
            return fd

        def stat_file(fd):
            value = fstat(fd)
            if fd in descriptors and point == "after-first-fstat" and state["fires"] == 0:
                fire(fd)
            return value

        with mock.patch.object(controller.os, "open", side_effect=open_file), \
                mock.patch.object(controller.os, "fstat", side_effect=stat_file):
            yield state

    def read(self):
        return controller.load_snapshots(self.directory)

    def test_same_generation_publication_after_open_before_first_fstat(self):
        with self.at_read("after-open") as state:
            values = self.read()
        self.assertEqual(values, [self.ready])
        self.assertEqual(state["old_nlinks"], [(1, 0)])
        self.assertEqual(state["fires"], 1)

    def test_same_generation_publication_after_first_fstat_during_read(self):
        with self.at_read("after-first-fstat") as state:
            values = self.read()
        self.assertEqual(values, [self.ready])
        self.assertEqual(state["old_nlinks"], [(1, 0)])

    def test_stable_snapshot_and_uncommitted_hidden_temp(self):
        self.write(self.directory / ".uncommitted.tmp", b"not JSON")
        self.assertEqual(self.read(), [self.starting])

    def test_persistent_atomic_churn_fails_closed_with_bounded_opens(self):
        with self.at_read("after-open", repeated=True) as state:
            with self.assertRaises(ValueError):
                self.read()
        self.assertGreaterEqual(state["opens"], 1)
        self.assertLessEqual(state["opens"], 3)
        self.assertEqual(state["fires"], state["opens"])

    def test_unsafe_replacement_is_not_accepted(self):
        for variant in ("mode", "hardlink", "symlink", "oversize"):
            with self.subTest(variant=variant):
                for item in self.directory.iterdir():
                    item.unlink()
                self.write(self.path, self.starting)
                def replace():
                    self.publish()
                    if variant == "mode":
                        self.path.chmod(0o644)
                    elif variant == "hardlink":
                        os.link(self.path, self.directory / ".extra.tmp")
                    elif variant == "symlink":
                        target = self.directory / ".target.tmp"
                        self.path.rename(target)
                        self.path.symlink_to(target)
                    else:
                        self.write(self.path, b"x" * (2 * 1024 * 1024 + 1))
                with self.at_read("after-open", replace):
                    with self.assertRaises((ValueError, OSError)):
                        self.read()

    def test_initial_unsafe_files_fail_closed(self):
        for variant in ("mode", "hardlink", "symlink", "oversize"):
            with self.subTest(variant=variant):
                for item in self.directory.iterdir():
                    item.unlink()
                self.write(self.path, self.starting)
                if variant == "mode":
                    self.path.chmod(0o644)
                elif variant == "hardlink":
                    os.link(self.path, self.directory / ".extra.tmp")
                elif variant == "symlink":
                    target = self.directory / ".target.tmp"
                    self.path.rename(target)
                    self.path.symlink_to(target)
                else:
                    self.write(self.path, b"x" * (2 * 1024 * 1024 + 1))
                with self.assertRaises((ValueError, OSError)):
                    self.read()

    def test_foreign_owner_metadata_is_rejected(self):
        fstat = os.fstat
        def foreign(fd):
            value = list(fstat(fd))
            value[4] = os.geteuid() + 1
            return os.stat_result(value)
        with mock.patch.object(controller.os, "fstat", side_effect=foreign):
            with self.assertRaises(ValueError):
                self.read()
        named_stat = os.stat
        def foreign_named(path, *args, **kwargs):
            value = named_stat(path, *args, **kwargs)
            if Path(path) == self.path and kwargs.get("follow_symlinks") is False:
                altered = list(value)
                altered[4] = os.geteuid() + 1
                return os.stat_result(altered)
            return value
        with self.at_read("after-open"), mock.patch.object(controller.os, "stat", side_effect=foreign_named):
            with self.assertRaises(ValueError):
                self.read()

    def test_inplace_mutation_is_not_a_replacement_retry(self):
        def rewrite():
            self.write(self.path, {**self.starting, "phase": "modified-in-place"})
        with self.at_read("after-first-fstat", rewrite) as state:
            with self.assertRaises(ValueError):
                self.read()
        self.assertEqual(state["opens"], 1)
        self.assertEqual(state["old_nlinks"], [(1, 1)])

    def test_unlink_without_replacement_is_not_ignored(self):
        with self.at_read("after-open", self.path.unlink):
            with self.assertRaises((ValueError, FileNotFoundError)):
                self.read()

    def test_changed_generation_and_duplicate_json_are_rejected(self):
        changed = {**self.ready, "generation": "00000000-0000-4000-8000-000000000199"}
        for value in (changed, b'{"generation":"' + self.generation.encode()
                      + b'","generation":"' + self.generation.encode() + b'"}'):
            with self.subTest(value_type=type(value).__name__):
                self.write(self.path, self.starting)
                with self.at_read("after-open", lambda: self.publish(value)):
                    with self.assertRaises(ValueError):
                        self.read()

    def test_historical_responsibility_remains_visible_after_current_replacement(self):
        current = snapshot(self.generation, phase="starting")
        ready = {**current, "phase": "ready"}
        historical = snapshot("00000000-0000-4000-8000-000000000098", {
            "00000000-0000-4000-8000-000000000001": [dict(channel=CHANNELS[0], root="a" * 64)]})
        history_path = self.directory / (historical["generation"] + ".json")
        self.write(self.path, current)
        self.write(history_path, historical)
        with self.at_read("after-open", lambda: self.publish(ready)):
            values = self.read()
        self.assertEqual({v["generation"] for v in values}, {self.generation, historical["generation"]})
        for value in values:
            _validate(value, current["agent_pubkey"], current["relay"])
        pending = recovery_inventory.pending(values, self.generation)
        self.assertEqual(len(pending[(CHANNELS[0], "a" * 64)]), 1)
        # Invalid historical receipt status must remain visible and deny proof;
        # the new reader never discards an old generation as unavailable.
        historical = copy.deepcopy(historical)
        historical["receipts"][next(iter(historical["receipts"]))] = "unknown"
        self.write(history_path, historical)
        values = self.read()
        with self.assertRaisesRegex(ValueError, "runtime delivery receipts"):
            for value in values:
                _validate(value, current["agent_pubkey"], current["relay"])

    def test_config_env_and_writer_lock_keep_strict_original_reader(self):
        with self.at_read("after-open"):
            with self.assertRaises(ValueError):
                controller.read_private(self.path)
        self.path.unlink()
        self.write(self.directory / ".writer.lock", b"")
        self.assertEqual(self.read(), [])
        (self.directory / ".writer.lock").chmod(0o644)
        with self.assertRaises(ValueError):
            self.read()


if __name__ == "__main__":
    unittest.main()
