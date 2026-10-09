"""trackman_hub.py and the vendored cloud_ingest package, offline with in-memory fakes (no Hub, no network).

Run: python3 -m unittest discover -s skills/hardware/trackman-shot-export/tests -p 'test_*.py' -v
Needs rerun-sdk==0.38.1, pyarrow, numpy (the RRD writers use them).
"""
import builtins
import importlib
import io
import json
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import trackman_hub  # noqa: E402
from cloud_ingest import cli, fakes  # noqa: E402
from cloud_ingest import shots as S  # noqa: E402

URL = "https://tm-short.me/EXAMPLE1"
DATASET, SESSION = "indoor_validation", "SESSION-1"


def report() -> bytes:
    strokes = [{"Id": "s1", "Time": "2026-09-13T08:00:00Z", "Measurement": {"BallSpeed": 50.5, "BallTrajectory": [
        {"X": 0, "Y": 0, "Z": 0}, {"X": 1, "Y": 2, "Z": 3}]}},
               {"Id": "s2", "Time": "2026-09-13T09:30:00Z", "Measurement": {"BallSpeed": 40.0}}]
    return json.dumps({"StrokeGroups": [{"Id": "g", "Club": "7Iron", "Strokes": strokes}]}).encode()


def resources():
    lm = [S.LmRecord("A", 1_789_286_400_500_000_000, 9_000_000_000, 1_789_000_000_000)]  # 2026-09-13T08:00:00.5Z
    session = S.SessionInputs(lm, [], [], {"recording": "s3://rec"}, [],
                              properties={"session:started_at": "2026-09-13T07:00:00.000Z",
                                          "session:ended_at": "2026-09-13T09:00:00.000Z"})
    return dict(registry=fakes.FakeRegistry([]), status=fakes.FakeStatusWriter(),
                sessions=fakes.FakeSessions({(DATASET, SESSION): session}),
                fetcher=fakes.FakeFetcher({URL: report()}), submitter=fakes.FakeSubmitter())


def run(argv, res):
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.dict(os.environ, {"TRACKMAN_URLS": URL}), redirect_stdout(out), redirect_stderr(err):
        code = cli.main(trackman_hub.argv_with_executor([*argv, "--dataset", DATASET, "--session", SESSION]),
                        resources=res)
    return code, out.getvalue(), err.getvalue()


class TrackmanHubTest(unittest.TestCase):
    def test_vendored_package_needs_no_dagster_or_collect_sdk(self):
        real = builtins.__import__

        def guarded(name, *args, **kwargs):
            if name.split(".")[0] in ("dagster", "collect_sdk"):
                raise ImportError(name)
            return real(name, *args, **kwargs)

        with mock.patch("builtins.__import__", guarded):
            for module in ("cli", "pipeline", "hub_direct", "shots", "rrd", "records", "adapters.trackman"):
                importlib.reload(importlib.import_module(f"cloud_ingest.{module}"))

    def test_preview_is_read_only(self):
        res = resources()
        code, out, err = run(["preview"], res)
        self.assertEqual(code, 0, err)
        self.assertNotIn(URL, out + err)
        result = json.loads(out)
        self.assertEqual(result["window"]["from"], "session properties")
        self.assertEqual(result["links"], [{"link": 1, "strokes": 2, "in_window": 1, "outside_window": 1,
                                            "no_timezone_or_time": 0}])
        self.assertEqual(result["counts"]["matched"], 1)
        self.assertEqual((res["submitter"].attempts, res["registry"].rows, res["status"].records), ([], [], []))

    def test_import_attaches_the_link_and_writes_trackman_and_shots(self):
        res = resources()
        code, out, err = run(["import"], res)
        self.assertEqual(code, 0, err)
        self.assertNotIn(URL, out + err)
        result = json.loads(out)
        (row,) = res["registry"].rows
        self.assertEqual((row.dataset, row.recording_id, row.link_id, row.url), (DATASET, SESSION, result["link_ids"][0], URL))
        layers = [(s["dataset"], s["layer"], s["source_record_id"]) for s in res["submitter"].submits]
        self.assertEqual(layers, [(DATASET, "trackman", f"trackman:{SESSION}"), (DATASET, "shots", f"lm_shots:{SESSION}")])
        shots_status = [r for r in res["status"].records if r.source == "lm_shots"][-1]
        self.assertIn(f"skill:trackman-shot-export@{trackman_hub.SKILL_VERSION}", shots_status.detail)

    def test_links_never_come_from_argv(self):
        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
            cli.build_parser().parse_args(["import", "--dataset", "d", "--session", "s", URL])


if __name__ == "__main__":
    unittest.main()


class ParserParityTest(unittest.TestCase):
    """The CSV exporter and the vendored Hub parser must split a report into the same shots and points."""

    def test_same_shots_and_trajectories(self):
        import importlib.util

        from cloud_ingest.adapters import trackman

        spec = importlib.util.spec_from_file_location("export_trackman", SCRIPTS / "export_trackman.py")
        exporter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(exporter)
        placeholder = {"StrokeGroups": [{"Id": "g", "Strokes": [{"Id": "p"}]}]}
        for doc in (json.loads(report()), placeholder):
            shots = trackman.parse_report(json.dumps(doc).encode(), link_id="L1")
            results, trajectories, _, _ = exporter.split_reports([(doc, {})])
            self.assertEqual(len(shots), len(results))
            for shot, ref in zip(shots, trajectories):
                self.assertEqual(shot.shot_key, ref["shot_key"])
                for variant in trackman.VARIANTS:
                    self.assertEqual(shot.ball_trajectory(variant), (ref["measurements"][variant] or {}).get("BallTrajectory"))
            _, points = exporter.build_csv_tables([(doc, {})], "x")
            self.assertEqual(sum(len(s.ball_trajectory(v) or []) for s in shots for v in trackman.VARIANTS), len(points))


class WindowGuardTest(unittest.TestCase):
    def test_reversed_window_is_refused_before_anything_is_written(self):
        res = resources()
        code, out, err = run(["import", "--window-start", "2026-09-13T10:00:00Z", "--window-end", "2026-09-13T09:00:00Z"], res)
        self.assertEqual(code, 2)
        self.assertIn("is not before end", err)
        self.assertEqual((res["registry"].rows, res["submitter"].attempts, res["submitter"].deletes), ([], [], []))
