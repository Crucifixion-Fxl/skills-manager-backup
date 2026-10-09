#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["rerun-sdk[catalog]==0.38.1", "pyarrow", "numpy"]
# ///
"""Read-only data access to a rerun-hub (self-hosted Rerun catalog) with the official rerun-sdk.

    hub_data.py probe [--dataset NAME] [--table NAME] [--json]
    hub_data.py export --dataset D --segment S --index TIMELINE [--contents '/trackman/**']
        [--start X] [--end Y] [--columns C ...] [--limit N] --out F.parquet

$RERUN_HUB_URL is the gRPC address (rerun+http://...); $RERUN_HUB_TOKEN is never printed. Run probe
first so entity paths, columns and timelines come from the Hub. export needs named segments (or an
explicit --all-segments) and a timeline; --start/--end are integer ns for duration/integer
timelines, ISO-8601 (UTC) or epoch ns for timestamps, half-open. It writes <out>.provenance.json.
Exit code 2 = a condition the user must fix. rerun/numpy/datafusion are imported lazily.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

SDK_VERSION = "0.38.1"
URL_ENV = "RERUN_HUB_URL"
TOKEN_ENV = "RERUN_HUB_TOKEN"
# Catalog entries the SDK lists next to every dataset; they hold assets and blueprints, not data.
SYSTEM_SUFFIXES = ("__assets", "__blueprint")


class HubError(Exception):
    """A condition the user must fix (missing env, version mismatch); printed without a traceback."""


def connection_settings(url: str | None) -> tuple[str, str]:
    url = url or os.environ.get(URL_ENV, "")
    token = os.environ.get(TOKEN_ENV, "")
    if not url:
        raise HubError(f"set {URL_ENV} (the gRPC address from the Hub doc environments table) or pass --url")
    if not url.startswith(("rerun+http://", "rerun+https://")):
        raise HubError(f"{URL_ENV} must be the gRPC address (rerun+http:// or rerun+https://), got {url!r}")
    if not token:
        raise HubError(f"set {TOKEN_ENV}; ask the Hub maintainer for a read token (never paste it into code or chat)")
    return url, token


def connect(url: str | None):
    import rerun as rr

    local = getattr(rr, "__version__", "")
    if local != SDK_VERSION:
        raise HubError(f"rerun-sdk {local or '?'} installed; this Hub speaks {SDK_VERSION}. pip install 'rerun-sdk[catalog]=={SDK_VERSION}'")
    address, token = connection_settings(url)
    client = rr.catalog.CatalogClient(address, token=token)
    server = client.version_info().version
    if server != SDK_VERSION:
        raise HubError(f"server runs {server}, SDK is {SDK_VERSION}; stop and ask the Hub maintainer")
    return client


def user_datasets(names: list[str]) -> list[str]:
    return sorted(name for name in names if not name.endswith(SYSTEM_SUFFIXES))


def scrub(text: str) -> str:
    """Never let the token reach stdout, files or logs, even inside an error message."""
    token = os.environ.get(TOKEN_ENV, "")
    return text.replace(token, "<token>") if token else text


def fail(exc: BaseException) -> int:
    print(f"error: {scrub(str(exc))}", file=sys.stderr)
    return 2


def survey(client, dataset: str | None, table: str | None, sample: int) -> dict:
    report = {
        "sdk_version": SDK_VERSION,
        "server_version": client.version_info().version,
        "datasets": user_datasets(client.dataset_names()),
        "tables": sorted(client.table_names()),
    }
    if dataset:
        entry = client.get_dataset(name=dataset)
        segments = entry.segment_ids()
        schema = entry.schema()
        by_entity: dict[str, list[str]] = defaultdict(list)
        for column in schema.component_columns():
            path, _, rest = column.name.partition(":")
            by_entity[path].append(rest)
        timelines = [column.name for column in schema.index_columns()]
        ranges = bool(timelines) and f"{timelines[0]}:start" in entry.segment_table().schema().names
        report["dataset"] = {
            "name": dataset,
            "server_supports_latest_at_and_dataloader": ranges,
            "segment_count": len(segments),
            "segments_sample": segments[:sample],
            "timelines": timelines,
            "entities": {path: sorted(parts) for path, parts in sorted(by_entity.items())},
        }
    if table:
        frame = client.get_table(name=table).reader()
        rows = frame.limit(sample).to_arrow_table()
        report["table"] = {
            "name": table,
            "columns": [f"{field.name}: {field.type}" for field in rows.schema],
            "rows_sample": rows.to_pylist(),
        }
    return report


def render(report: dict) -> str:
    lines = [f"server {report['server_version']} / sdk {report['sdk_version']}", "datasets: " + ", ".join(report["datasets"]), "tables: " + ", ".join(report["tables"])]
    dataset = report.get("dataset")
    if dataset:
        lines.append(f"\n[{dataset['name']}] {dataset['segment_count']} segments, e.g. {', '.join(dataset['segments_sample'])}")
        lines.append("timelines (reader index=...): " + ", ".join(dataset["timelines"]))
        lines.append("latest-at / get_index_ranges / dataloader: " + ("supported" if dataset["server_supports_latest_at_and_dataloader"] else "NOT supported by this Hub version (needs m1 0f44ab3+); align locally"))
        lines.append("columns, as <entity>:<Archetype>:<component>:")
        for path, parts in dataset["entities"].items():
            lines.append(f"  {path}  " + "  ".join(parts))
    table = report.get("table")
    if table:
        lines.append(f"\n[table {table['name']}]")
        lines.extend(f"  {column}" for column in table["columns"])
        lines.extend(f"  {json.dumps(row, ensure_ascii=False, default=str)}" for row in table["rows_sample"])
    return "\n".join(lines)


FORMATS = (".parquet", ".csv")


def parse_bound(value: str | None, kind: str):
    """A window bound as the scalar DataFusion compares the index column with, or None when open."""
    if value is None:
        return None
    if kind == "integer":
        if not value.lstrip("-").isdigit():
            raise HubError(f"integer timeline bounds are integers, got {value!r}")
        return int(value)
    import numpy as np

    if kind == "timestamp":
        if value.lstrip("-").isdigit():
            return np.datetime64(int(value), "ns")
        stamp = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=dt.timezone.utc)
        return np.datetime64(stamp.astimezone(dt.timezone.utc).replace(tzinfo=None), "ns")
    if not value.lstrip("-").isdigit():
        raise HubError(f"duration timeline bounds are integer nanoseconds, got {value!r}")
    return np.timedelta64(int(value), "ns")


def index_kind(arrow_type) -> str:
    import pyarrow as pa

    if pa.types.is_timestamp(arrow_type):
        return "timestamp"
    if pa.types.is_duration(arrow_type):
        return "duration"
    return "integer"


def window(frame, index: str, start, end):
    if start is None and end is None:
        return frame
    from datafusion import col, lit

    if start is not None:
        frame = frame.filter(col(index) >= lit(start))
    if end is not None:
        frame = frame.filter(col(index) < lit(end))
    return frame


def build_query(client, args):
    view = client.get_dataset(name=args.dataset)
    if args.segment:
        known = set(view.segment_ids())
        missing = [segment for segment in args.segment if segment not in known]
        if missing:
            raise HubError(f"segments not in {args.dataset}: {', '.join(missing)} (run: hub_data.py probe --dataset {args.dataset})")
        view = view.filter_segments(args.segment)
    if args.contents:
        view = view.filter_contents(args.contents)
    frame = view.reader(index=args.index)
    names = frame.schema().names
    if args.index not in names:
        raise HubError(f"timeline {args.index!r} not in this view; timelines come from: hub_data.py probe --dataset {args.dataset}")
    kind = index_kind(frame.schema().field(args.index).type)
    frame = window(frame, args.index, parse_bound(args.start, kind), parse_bound(args.end, kind))
    if args.columns:
        unknown = [name for name in args.columns if name not in names]
        if unknown:
            raise HubError(f"unknown columns: {', '.join(unknown)}")
        keep = ["rerun_segment_id", args.index] + [name for name in args.columns if name not in ("rerun_segment_id", args.index)]
        frame = frame.select(*keep)
    if args.limit:
        frame = frame.limit(args.limit)
    return frame, kind


def write(table, out: Path) -> None:
    import pyarrow as pa
    import pyarrow.csv as pcsv
    import pyarrow.parquet as pq

    if out.suffix == ".parquet":
        return pq.write_table(table, out)
    # Nested columns (lists, structs) have no CSV form; keep them as JSON text.
    flat = {}
    for name in table.column_names:
        column = table.column(name)
        if pa.types.is_nested(column.type):
            column = pa.array([None if value is None else json.dumps(value, default=str) for value in column.to_pylist()], pa.string())
        flat[name] = column
    pcsv.write_csv(pa.table(flat), out)


def provenance(args, kind: str, rows: int, columns: list[str]) -> dict:
    return {
        "dataset": args.dataset, "segments": args.segment or "ALL", "contents": args.contents or "ALL",
        "index": args.index, "index_kind": kind, "start": args.start, "end": args.end, "limit": args.limit,
        "rows": rows, "columns": columns, "rerun_sdk": SDK_VERSION,
        "exported_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }


def _probe(args, client) -> int:
    report = survey(client, args.dataset, args.table, args.sample)
    print(scrub(json.dumps(report, ensure_ascii=False, indent=2, default=str) if args.json else render(report)))
    return 0


def _export(args, client) -> int:
    if not args.segment and not args.all_segments:
        raise HubError("name segments with --segment (repeatable) or pass --all-segments explicitly")
    if args.segment and args.all_segments:
        raise HubError("--segment and --all-segments are mutually exclusive")
    if args.out.suffix not in FORMATS:
        raise HubError(f"--out must end with {' or '.join(FORMATS)}")
    meta = args.out.with_name(args.out.name + ".provenance.json")
    if not args.force and (args.out.exists() or meta.exists()):
        raise HubError(f"{args.out} exists; pass --force to overwrite")
    client = client or connect(args.url)
    frame, kind = build_query(client, args)
    table = frame.to_arrow_table()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    write(table, args.out)
    record = provenance(args, kind, table.num_rows, table.column_names)
    meta.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    summary = {"out": str(args.out), "provenance": str(meta), "rows": table.num_rows, "columns": table.num_columns}
    print(scrub(json.dumps(summary, ensure_ascii=False) if args.json else f"wrote {table.num_rows} rows x {table.num_columns} columns to {args.out} (provenance: {meta.name})"))
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    root.add_argument("--url", help="gRPC address; default $RERUN_HUB_URL")
    commands = root.add_subparsers(dest="command", required=True)
    probe = commands.add_parser("probe", help="survey datasets, timelines, columns, tables")
    probe.add_argument("--dataset")
    probe.add_argument("--table")
    probe.add_argument("--sample", type=int, default=5, help="how many segment ids / table rows to show")
    probe.add_argument("--json", action="store_true", help="machine-readable output")
    export = commands.add_parser("export", help="export one narrowed query to parquet or csv")
    export.add_argument("--dataset", required=True)
    export.add_argument("--segment", action="append", default=[], help="repeatable")
    export.add_argument("--all-segments", action="store_true", help="read every segment (explicit opt-in)")
    export.add_argument("--contents", action="append", default=[], help="entity path expression, e.g. '/trackman/**'; repeatable")
    export.add_argument("--index", required=True, help="timeline name from probe")
    export.add_argument("--start")
    export.add_argument("--end")
    export.add_argument("--columns", nargs="+", help="component columns to keep (exact names from probe)")
    export.add_argument("--limit", type=int, help="first N rows only (sampling)")
    export.add_argument("--out", required=True, type=Path)
    export.add_argument("--force", action="store_true", help="overwrite an existing output file")
    export.add_argument("--json", action="store_true", help="machine-readable summary")
    return root


def main(argv: list[str] | None = None, client=None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "probe":
            return _probe(args, client or connect(args.url))
        return _export(args, client)
    except HubError as exc:
        return fail(exc)


if __name__ == "__main__":
    sys.exit(main())
