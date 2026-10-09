"""hub_data.py probe / export against a fake catalog client (no rerun SDK, no network).

Run: python3 -m unittest discover -s skills/data/rerun-hub-data/tests -p 'test_*.py' -v
The export tests need pyarrow; they skip without it.
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import hub_data  # noqa: E402

try:
    import pyarrow as pa
    import pyarrow.parquet as pq
except ImportError:  # pragma: no cover - CI installs pyarrow
    pa = None

TOKEN = "eyJ-secret-token-value"


class FakeFrame:
    def __init__(self, table):
        self.table = table
        self.filters = []

    def schema(self):
        return self.table.schema

    def filter(self, expr):
        self.filters.append(expr)
        return self

    def select(self, *names):
        return FakeFrame(self.table.select(list(names)))

    def limit(self, count):
        return FakeFrame(self.table.slice(0, count))

    def to_arrow_table(self):
        return self.table


class FakeView:
    def __init__(self, table, segments):
        self.table, self.segments = table, segments
        self.calls = []
        self.ranges = True

    def segment_ids(self):
        return list(self.segments)

    def filter_segments(self, ids):
        self.calls.append(("filter_segments", list(ids)))
        return self

    def filter_contents(self, exprs):
        self.calls.append(("filter_contents", list(exprs)))
        return self

    def reader(self, index):
        self.calls.append(("reader", index))
        return FakeFrame(self.table)

    def segment_table(self):
        names = ["rerun_segment_id"] + (["log_time:start", "log_time:end"] if self.ranges else [])
        return SimpleNamespace(schema=lambda: SimpleNamespace(names=names))

    def schema(self):
        return SimpleNamespace(
            index_columns=lambda: [SimpleNamespace(name="log_time"), SimpleNamespace(name="device_mono_ns")],
            component_columns=lambda: [
                SimpleNamespace(name="/lm/metrics/ball_speed:Scalars:scalars"),
                SimpleNamespace(name="/shots:status"),
                SimpleNamespace(name="/calibration/summary:TextDocument:text"),
            ],
        )


class FakeClient:
    def __init__(self, view, table_rows=None):
        self.view, self.table_rows = view, table_rows

    def version_info(self):
        return SimpleNamespace(version="0.38.1")

    def dataset_names(self):
        return ["demo", "demo__assets", "demo__blueprint", "launch_monitor.default_external_user"]

    def table_names(self):
        return ["launch_monitor.ingest_registry"]

    def get_dataset(self, name):
        return self.view

    def get_table(self, name):
        return SimpleNamespace(reader=lambda: FakeFrame(self.table_rows))


def run(main, argv, client):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv, client=client)
    return code, out.getvalue(), err.getvalue()


class ConnectionTests(unittest.TestCase):
    def test_missing_url_and_token_are_actionable(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(hub_data.HubError, "RERUN_HUB_URL"):
                hub_data.connection_settings(None)
            with self.assertRaisesRegex(hub_data.HubError, "RERUN_HUB_TOKEN"):
                hub_data.connection_settings("rerun+http://h:51234")

    def test_http_console_address_is_rejected(self):
        with mock.patch.dict(os.environ, {"RERUN_HUB_TOKEN": TOKEN}, clear=True):
            with self.assertRaisesRegex(hub_data.HubError, "gRPC address"):
                hub_data.connection_settings("http://h:8877")

    def test_scrub_removes_the_token(self):
        with mock.patch.dict(os.environ, {"RERUN_HUB_TOKEN": TOKEN}):
            self.assertEqual(hub_data.scrub(f"bad {TOKEN}!"), "bad <token>!")

    def test_system_entries_are_hidden(self):
        self.assertEqual(hub_data.user_datasets(["b", "a__assets", "a", "a__blueprint"]), ["a", "b"])


@unittest.skipIf(pa is None, "pyarrow not installed")
class ProbeTests(unittest.TestCase):
    def setUp(self):
        rows = pa.table({"shot_key": ["s:1"], "segment_id": ["s"], "start_ns": [20_000_000_000]})
        self.client = FakeClient(FakeView(None, ["s", "t"]), rows)

    def test_survey_lists_timelines_and_columns_by_entity(self):
        code, out, _ = run(hub_data.main, ["probe", "--dataset", "demo", "--table", "launch_monitor.ingest_registry", "--json"], self.client)
        self.assertEqual(code, 0)
        report = json.loads(out)
        self.assertEqual(report["datasets"], ["demo", "launch_monitor.default_external_user"])
        self.assertEqual(report["dataset"]["timelines"], ["log_time", "device_mono_ns"])
        self.assertEqual(report["dataset"]["entities"]["/lm/metrics/ball_speed"], ["Scalars:scalars"])
        self.assertEqual(report["table"]["rows_sample"][0]["shot_key"], "s:1")

    def test_text_output_names_the_column_form(self):
        code, out, _ = run(hub_data.main, ["probe", "--dataset", "demo"], self.client)
        self.assertEqual(code, 0)
        self.assertIn("timelines (reader index=...): log_time, device_mono_ns", out)
        self.assertIn("/calibration/summary  TextDocument:text", out)
        self.assertIn("latest-at / get_index_ranges / dataloader: supported", out)

    def test_old_hub_without_index_ranges_is_flagged(self):
        self.client.view.ranges = False
        code, out, _ = run(hub_data.main, ["probe", "--dataset", "demo", "--json"], self.client)
        self.assertEqual(code, 0)
        self.assertFalse(json.loads(out)["dataset"]["server_supports_latest_at_and_dataloader"])


@unittest.skipIf(pa is None, "pyarrow not installed")
class ExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        table = pa.table({
            "rerun_segment_id": ["s", "s"],
            "device_mono_ns": pa.array([1, 2], pa.duration("ns")),
            "/a:Scalars:scalars": pa.array([[1.0], [2.0]], pa.list_(pa.float64())),
            "/b:Scalars:scalars": pa.array([[3.0], [4.0]], pa.list_(pa.float64())),
        })
        self.view = FakeView(table, ["s", "t"])
        self.client = FakeClient(self.view)

    def tearDown(self):
        self.tmp.cleanup()

    def args(self, *extra, out="x.parquet"):
        return ["export", "--dataset", "demo", "--index", "device_mono_ns", "--out", str(self.dir / out), *extra]

    def test_segments_must_be_named_or_all_segments_opted_in(self):
        code, _, err = run(hub_data.main, self.args(), self.client)
        self.assertEqual(code, 2)
        self.assertIn("--all-segments", err)

    def test_unknown_segment_is_refused_before_reading(self):
        code, _, err = run(hub_data.main, self.args("--segment", "nope"), self.client)
        self.assertEqual(code, 2)
        self.assertIn("nope", err)
        self.assertNotIn(("reader", "device_mono_ns"), self.view.calls)

    def test_parquet_export_narrows_and_writes_provenance(self):
        code, out, _ = run(hub_data.main, self.args("--segment", "s", "--contents", "/a/**", "--columns", "/a:Scalars:scalars"), self.client)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.view.calls[:2], [("filter_segments", ["s"]), ("filter_contents", ["/a/**"])])
        table = pq.read_table(self.dir / "x.parquet")
        self.assertEqual(table.column_names, ["rerun_segment_id", "device_mono_ns", "/a:Scalars:scalars"])
        meta = json.loads((self.dir / "x.parquet.provenance.json").read_text())
        self.assertEqual((meta["segments"], meta["index_kind"], meta["rows"]), (["s"], "duration", 2))

    def test_existing_output_needs_force(self):
        self.assertEqual(run(hub_data.main, self.args("--segment", "s"), self.client)[0], 0)
        code, _, err = run(hub_data.main, self.args("--segment", "s"), self.client)
        self.assertEqual(code, 2)
        self.assertIn("--force", err)
        self.assertEqual(run(hub_data.main, self.args("--segment", "s", "--force"), self.client)[0], 0)

    def test_csv_keeps_nested_values_as_json(self):
        code, _, _ = run(hub_data.main, self.args("--all-segments", out="x.csv"), self.client)
        self.assertEqual(code, 0)
        self.assertIn("[1.0]", (self.dir / "x.csv").read_text())

    def test_unknown_timeline_and_column_are_refused(self):
        code, _, err = run(hub_data.main, ["export", "--dataset", "demo", "--segment", "s", "--index", "sensor_time", "--out", str(self.dir / "y.parquet")], self.client)
        self.assertEqual(code, 2)
        self.assertIn("hub_data.py probe", err)
        code, _, err = run(hub_data.main, self.args("--segment", "s", "--columns", "/zzz:Scalars:scalars"), self.client)
        self.assertEqual(code, 2)
        self.assertIn("/zzz", err)

    def test_bounds_follow_the_timeline_kind(self):
        self.assertEqual(hub_data.parse_bound("5", "integer"), 5)
        with self.assertRaises(hub_data.HubError):
            hub_data.parse_bound("2026-01-01", "integer")
        try:
            import numpy as np
        except ImportError:
            return
        self.assertEqual(hub_data.parse_bound("7", "duration"), np.timedelta64(7, "ns"))
        self.assertEqual(hub_data.parse_bound("1970-01-01T00:00:01Z", "timestamp"), np.datetime64(1_000_000_000, "ns"))


if __name__ == "__main__":
    unittest.main()
