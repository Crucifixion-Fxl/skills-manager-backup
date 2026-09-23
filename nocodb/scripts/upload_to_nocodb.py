# /// script
# requires-python = ">=3.10"
# dependencies = ["openpyxl", "requests"]
# ///
"""Upload CSV/Excel file to NocoDB table.

Parses a local CSV or Excel file, optionally creates a new NocoDB table,
and bulk-inserts the data. Designed for the Superset skill upload workflow.

Usage:
    uv run scripts/upload_to_nocodb.py --file data.csv --project BI --table my_table [options]
"""
import argparse
import csv
import json
import os
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import openpyxl
import requests


COLUMN_MAP_CACHE_DIR = Path(
    os.environ.get("NOCODB_COLUMN_MAP_DIR", str(Path.home() / ".cache" / "nocodb_column_maps"))
)


def _safe_segment(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", s)


def column_map_path(base_url: str, project: str, table: str) -> Path:
    host = urlparse(base_url).hostname or "nocodb"
    return COLUMN_MAP_CACHE_DIR / f"{_safe_segment(host)}__{_safe_segment(project)}__{_safe_segment(table)}.json"


def load_column_map_arg(value: str | None) -> dict[str, str]:
    """Accept inline JSON or @path/to/file.json. Returns {} if value is None."""
    if not value:
        return {}
    if value.startswith("@"):
        path = Path(value[1:]).expanduser()
        if not path.exists():
            output_json({"status": "error", "message": f"--column-map file not found: {path}"})
            sys.exit(1)
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError as e:
            output_json({"status": "error", "message": f"Invalid JSON in {path}: {e}"})
            sys.exit(1)
    else:
        try:
            data = json.loads(value)
        except json.JSONDecodeError as e:
            output_json({"status": "error", "message": f"Invalid --column-map JSON: {e}"})
            sys.exit(1)
    if not isinstance(data, dict):
        output_json({"status": "error", "message": "--column-map must be a JSON object"})
        sys.exit(1)
    return {str(k): str(v) for k, v in data.items()}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Upload CSV/Excel to NocoDB")
    p.add_argument("--file", required=True, help="Local file path (.csv/.xlsx/.xls)")
    p.add_argument(
        "--project",
        required=True,
        help=(
            "NocoDB project name (required, no default). Pass explicitly each run — "
            "the skill mandates per-run user confirmation of project + table; a default "
            "would let agents silently target AmazonApi. See SKILL.md Rule #7."
        ),
    )
    p.add_argument("--table", required=True, help="NocoDB table name")
    p.add_argument("--base-url", default="https://nocodb.addx.live", help="NocoDB base URL")
    p.add_argument("--token", default=None, help="xc-token (or set NOCODB_TOKEN env var)")
    p.add_argument("--sheet", default=None, help="Excel sheet name (default: first sheet)")
    p.add_argument("--create-table", action="store_true", help="Create table if not exists")
    p.add_argument("--preview", action="store_true", help="Preview only, do not upload")
    p.add_argument("--batch-size", type=int, default=100, help="Bulk insert batch size (default 100)")
    p.add_argument(
        "--column-map",
        default=None,
        help=(
            "JSON mapping of original->new column names, applied BEFORE normalization. "
            "Inline JSON or @path/to/map.json. Use to translate Chinese headers "
            "(new tables) or align file headers with an existing table's schema. "
            "Example: '{\"姓名\":\"name\"}' or @~/.cache/nocodb_column_maps/host__proj__table.json"
        ),
    )
    return p.parse_args()


def has_chinese(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text))


def normalize_name(name: str, fallback: str = "col") -> str:
    """Sanitize a column/table name to Athena/Hive convention: lowercase + [a-z0-9_]."""
    s = re.sub(r"[^a-z0-9_]+", "_", name.strip().lower()).strip("_")
    if not s:
        s = fallback
    if s[0].isdigit():
        s = "_" + s
    return s


def normalize_headers(headers: list[str]) -> tuple[list[str], list[dict], list[str]]:
    """Return (new_headers, mapping[{original,normalized,changed}], collisions)."""
    seen: dict[str, int] = {}
    new_headers: list[str] = []
    mapping: list[dict] = []
    for i, h in enumerate(headers):
        norm = normalize_name(h, fallback=f"col_{i}")
        # disambiguate collisions
        base = norm
        n = seen.get(base, 0)
        if n > 0:
            norm = f"{base}_{n}"
        seen[base] = n + 1
        new_headers.append(norm)
        mapping.append({"original": h, "normalized": norm, "changed": h != norm})
    collisions = [m["normalized"] for m in mapping if m["changed"]]
    return new_headers, mapping, collisions


def read_csv(path: str) -> tuple[list[str], list[dict]]:
    with open(path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        rows = [row for row in reader]
    return headers, rows


def read_excel(path: str, sheet_name: str | None = None) -> tuple[list[str], list[dict]]:
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb[sheet_name] if sheet_name else wb.active
    rows_iter = ws.iter_rows(values_only=True)
    headers = [str(h) if h is not None else f"col_{i}" for i, h in enumerate(next(rows_iter))]
    rows = []
    for row in rows_iter:
        record = {}
        for i, val in enumerate(row):
            if i < len(headers):
                record[headers[i]] = str(val) if val is not None else None
        rows.append(record)
    wb.close()
    return headers, rows


def output_json(data: dict) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def get_token(args: argparse.Namespace) -> str:
    import os
    token = args.token or os.environ.get("NOCODB_TOKEN", "")
    if not token:
        output_json({"status": "error", "message": "xc-token required: use --token or set NOCODB_TOKEN env var"})
        sys.exit(1)
    return token


def api_get(base_url: str, path: str, token: str) -> Any:
    r = requests.get(f"{base_url}/{path}", headers={"xc-token": token}, timeout=30)
    r.raise_for_status()
    return r.json()


def api_post(base_url: str, path: str, token: str, data: Any) -> Any:
    r = requests.post(f"{base_url}/{path}", headers={"xc-token": token}, json=data, timeout=60)
    if not r.ok:
        output_json({"status": "error", "http_code": r.status_code, "message": r.text[:500]})
        sys.exit(1)
    return r.json()


def find_project(base_url: str, token: str, project_name: str) -> dict | None:
    resp = api_get(base_url, "api/v1/db/meta/projects/", token)
    for p in resp.get("list", []):
        if p["title"] == project_name:
            return p
    return None


def find_table(base_url: str, token: str, project_id: str, table_name: str) -> dict | None:
    resp = api_get(base_url, f"api/v1/db/meta/projects/{project_id}/tables", token)
    for t in resp.get("list", []):
        if t["title"] == table_name:
            return t
    return None


SYSTEM_COLUMN_UIDTS = {
    "ID",
    "CreatedTime",
    "LastModifiedTime",
    "CreatedBy",
    "LastModifiedBy",
    "ForeignKey",
}


def get_table_columns(base_url: str, token: str, table_id: str) -> list[str]:
    """Fetch user-defined column titles of an existing NocoDB table.

    Excludes primary key, system-managed columns (created_at / updated_at /
    created_by / updated_by) and any other column NocoDB marks as system=true.
    Without this filter the schema diff against the file headers would always
    report `in_table_not_in_file` and block every existing-table upload.
    """
    resp = api_get(base_url, f"api/v1/db/meta/tables/{table_id}", token)
    cols = resp.get("columns", []) or []
    out: list[str] = []
    for c in cols:
        title = c.get("title") or c.get("column_name")
        if not title:
            continue
        if c.get("pk") or c.get("system"):
            continue
        if c.get("uidt") in SYSTEM_COLUMN_UIDTS:
            continue
        out.append(title)
    return out


def diff_headers(file_headers: list[str], table_columns: list[str]) -> dict:
    file_set = set(file_headers)
    table_set = set(table_columns)
    return {
        "in_file_not_in_table": sorted(file_set - table_set),
        "in_table_not_in_file": sorted(table_set - file_set),
        "matched": sorted(file_set & table_set),
    }


def create_nocodb_table(base_url: str, token: str, project_id: str, table_name: str, columns: list[str]) -> dict:
    """Create a NocoDB table with all string columns."""
    col_defs = [
        {
            "column_name": "id", "title": "Id",
            "dt": "int", "dtx": "integer", "ct": "int(11)",
            "nrqd": False, "rqd": True, "pk": True, "un": True, "ai": True,
            "uidt": "ID", "dtxp": "11",
        }
    ]
    for col in columns:
        col_defs.append({
            "column_name": col, "title": col,
            "dt": "text", "dtx": "specificType", "ct": "text",
            "nrqd": True, "rqd": False, "pk": False, "un": False, "ai": False,
            "uidt": "LongText", "dtxp": "",
        })

    payload = {"title": table_name, "table_name": table_name, "columns": col_defs}
    return api_post(base_url, f"api/v1/db/meta/projects/{project_id}/tables", token, payload)


def bulk_insert(base_url: str, token: str, project_name: str, table_name: str,
                rows: list[dict], batch_size: int) -> int:
    total = 0
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        api_post(base_url, f"api/v1/db/data/bulk/v1/{project_name}/{table_name}", token, batch)
        total += len(batch)
    return total


def validate_table_name_or_exit(table: str) -> None:
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", table):
        output_json({
            "status": "error",
            "message": (
                f"Table name '{table}' does not match Athena convention "
                "(lowercase + [a-z0-9_], must start with letter or underscore)."
            ),
            "suggestion": normalize_name(table, fallback="my_table"),
        })
        sys.exit(1)


def read_input_file(file_path: Path, sheet: str | None) -> tuple[list[str], list[dict]]:
    ext = file_path.suffix.lower()
    if ext == ".csv":
        return read_csv(str(file_path))
    if ext in (".xlsx", ".xls"):
        return read_excel(str(file_path), sheet)
    output_json({"status": "error", "message": f"Unsupported file type: {ext}. Use .csv/.xlsx/.xls"})
    sys.exit(1)


def resolve_column_map(args: argparse.Namespace) -> tuple[dict[str, str], str | None]:
    """Resolution order: --column-map (explicit) > sidecar cache > none."""
    column_map = load_column_map_arg(args.column_map)
    if column_map:
        return column_map, "explicit"
    cache_path = column_map_path(args.base_url, args.project, args.table)
    if not cache_path.exists():
        return {}, None
    try:
        loaded = json.loads(cache_path.read_text())
    except json.JSONDecodeError:
        return {}, None
    if not isinstance(loaded, dict) or not loaded:
        return {}, None
    return loaded, f"cache:{cache_path}"


def apply_column_map_or_exit(
    headers: list[str], rows: list[dict], column_map: dict[str, str], source: str | None
) -> tuple[list[str], list[dict]]:
    """Rename headers + rows; abort if mapping causes duplicate target columns."""
    new_headers = [column_map.get(h, h) for h in headers]
    target_to_sources: dict[str, list[str]] = {}
    for orig, new in zip(headers, new_headers):
        target_to_sources.setdefault(new, []).append(orig)
    collisions = {tgt: srcs for tgt, srcs in target_to_sources.items() if len(srcs) > 1}
    if collisions:
        output_json({
            "status": "error",
            "message": (
                "--column-map / sidecar produces duplicate target columns. "
                "Multiple source headers would collapse into a single column "
                "and data would be silently lost. Fix the mapping so each "
                "target column has at most one source."
            ),
            "collisions": collisions,
            "column_map_source": source,
        })
        sys.exit(1)
    new_rows = [
        {new_headers[i]: row.get(headers[i]) for i in range(len(headers))}
        for row in rows
    ]
    return new_headers, new_rows


def block_chinese_or_exit(headers: list[str]) -> None:
    chinese_cols = [h for h in headers if has_chinese(h)]
    if not chinese_cols:
        return
    output_json({
        "status": "error",
        "message": (
            "Chinese column names remain after applying --column-map. "
            "Provide an English translation for each Chinese header via --column-map "
            "(JSON mapping of original→english). The agent should propose translations "
            "based on column semantics and ask the user to confirm before re-running."
        ),
        "chinese_columns": chinese_cols,
        "hint_example": "--column-map '{\"" + chinese_cols[0] + "\":\"<english_name>\"}'",
    })
    sys.exit(1)


def emit_preview(
    file_path: Path,
    create_table: bool,
    headers: list[str],
    new_headers: list[str],
    rows: list[dict],
    renamed_rows: list[dict],
    headers_changed: list[dict],
) -> None:
    payload: dict = {
        "status": "preview",
        "file": str(file_path),
        "create_table": create_table,
        "columns": new_headers if create_table else headers,
        "total_rows": len(rows),
        "sample_rows": renamed_rows[:5],
    }
    if create_table:
        payload["columns_original"] = headers
        payload["renamed"] = headers_changed
        payload["note"] = "Columns auto-normalized to Athena convention (new table)."
    else:
        payload["note"] = (
            "Existing-table mode: original column names preserved. "
            "Insert will fail if file headers don't match the live table schema."
        )
    output_json(payload)


def write_sidecar_after_create(
    args: argparse.Namespace,
    file_original_headers: list[str],
    column_map: dict[str, str],
    name_mapping: list[dict],
) -> str | None:
    """Persist effective `file_original → final_table_column` map for future imports."""
    intermediate_to_final = {m["original"]: m["normalized"] for m in name_mapping}
    effective_map: dict[str, str] = {}
    for orig in file_original_headers:
        intermediate = column_map.get(orig, orig)
        final = intermediate_to_final.get(intermediate, intermediate)
        if orig != final:
            effective_map[orig] = final
    if not effective_map:
        return None
    cache_path = column_map_path(args.base_url, args.project, args.table)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(effective_map, ensure_ascii=False, indent=2))
    return str(cache_path)


def verify_existing_table_schema_or_exit(
    base_url: str, token: str, table_id: str, headers: list[str]
) -> None:
    live_cols = get_table_columns(base_url, token, table_id)
    diff = diff_headers(headers, live_cols)
    if not (diff["in_file_not_in_table"] or diff["in_table_not_in_file"]):
        return
    output_json({
        "status": "error",
        "message": (
            "File headers do not match the existing table schema. "
            "Existing tables MUST NOT be auto-renamed (would break historical "
            "data and downstream sync). Use --column-map to align the file "
            "headers to the live table column names, or pre-edit the source file."
        ),
        "table_columns": live_cols,
        "file_headers": headers,
        "diff": diff,
        "hint": (
            "If columns are semantically the same but named differently, pass "
            "--column-map '{\"file_col\":\"table_col\", ...}' to rename in-flight."
        ),
    })
    sys.exit(1)


def main() -> None:
    args = parse_args()
    file_path = Path(args.file)
    if not file_path.exists():
        output_json({"status": "error", "message": f"File not found: {args.file}"})
        sys.exit(1)

    validate_table_name_or_exit(args.table)
    headers, rows = read_input_file(file_path, args.sheet)
    file_original_headers = list(headers)

    column_map, column_map_source = resolve_column_map(args)
    if column_map:
        headers, rows = apply_column_map_or_exit(headers, rows, column_map, column_map_source)

    block_chinese_or_exit(headers)

    # Auto-normalize to Athena convention only when creating a new table.
    # Existing-table writes must preserve headers to match live schema.
    headers_changed: list[dict] = []
    name_mapping: list[dict] = []
    if args.create_table:
        new_headers, name_mapping, _ = normalize_headers(headers)
        renamed_rows = [
            {new_headers[i]: row.get(headers[i]) for i in range(len(headers))}
            for row in rows
        ]
        headers_changed = [m for m in name_mapping if m["changed"]]
    else:
        new_headers = headers
        renamed_rows = rows

    if args.preview:
        emit_preview(file_path, args.create_table, headers, new_headers, rows, renamed_rows, headers_changed)
        return

    headers = new_headers
    rows = renamed_rows

    token = get_token(args)
    project = find_project(args.base_url, token, args.project)
    if not project:
        output_json({"status": "error", "message": f"Project not found: {args.project}"})
        sys.exit(1)

    table = find_table(args.base_url, token, project["id"], args.table)
    sidecar_written: str | None = None
    if table is None:
        if not args.create_table:
            output_json({
                "status": "error",
                "message": f"Table '{args.table}' not found in project '{args.project}'. Use --create-table to create it.",
            })
            sys.exit(1)
        create_nocodb_table(args.base_url, token, project["id"], args.table, headers)
        sidecar_written = write_sidecar_after_create(args, file_original_headers, column_map, name_mapping)
    else:
        verify_existing_table_schema_or_exit(args.base_url, token, table["id"], headers)

    # Bulk insert
    inserted = bulk_insert(args.base_url, token, args.project, args.table, rows, args.batch_size)

    output_json({
        "status": "success",
        "project": args.project,
        "table": args.table,
        "columns": headers,
        "renamed": headers_changed,
        "rows_inserted": inserted,
        "column_map_used": column_map_source,
        "column_map_saved": sidecar_written,
    })


if __name__ == "__main__":
    main()
