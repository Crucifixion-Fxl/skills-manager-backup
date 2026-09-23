#!/usr/bin/env python3
"""
service-catalog-onboarding skill: API schema field-set drift gate (CI 用)

用法：cp $SKILL_DIR/scripts/api-drift/check-schemas.py <repo>/scripts/api-drift/
然后在 .gitlab-ci.yml 加 api:drift:schemas job —— 见 scripts/gitlab-ci-snippets/api-drift.yml

逻辑：从 Go struct（backend/internal/types/*.go，json tag）抽字段集合，跟 OpenAPI
components.schemas.<Name>.properties 比；diff 非空 → exit 1。修改 MAPPING 字典适配本仓
的"哪些 struct 跟哪些 schema 对". 依赖 PyYAML（CI 里 `pip install pyyaml`）。

----- 以下保留原 engagement 仓样板的完整 docstring + 实现 -----

check-schemas.py — CI drift gate: Go struct field-set ↔ OpenAPI schema parity.

Purpose:
    For each Go request/response struct in backend/internal/types/*.go listed
    in MAPPING below, extract its JSON-tagged field set (with required-vs-
    optional inferred from the `omitempty` tag) and compare against the
    matching schema under components.schemas.<Name> in
    api/engagement.openapi.yaml. Report any missing or extra fields.

Why this exists:
    drift report e377020 caught fields that existed in code but were
    omitted from the contract (TouchpointView.experimentKey, slotType,
    actionType, paywallId, context) plus a ghost field on the contract
    side that didn't exist in code. redocly lint can't see this — it has
    no view into the Go source. This script does.

Exit codes:
    0  — all mapped structs match on field-SET (no drift)
    1  — field-SET drift detected; see stdout for the offending fields
    2  — script-internal error (cannot open a truth file, missing PyYAML, etc.)

A note on required-vs-optional:
    The script ALSO reports required-set divergence (Go non-`omitempty`
    field that isn't in OpenAPI `required:`, or vice versa) — but only as
    a WARNING (does not affect exit code). Reason: `omitempty` is purely
    a Go JSON-OUT marshalling flag and is a flawed proxy for inbound
    contract requirednesss; the server may legitimately tolerate zero
    values without a `omitempty` tag. The CMSWebhookReq description in
    api/engagement.openapi.yaml documents exactly this case. We surface
    the divergence for human review without blocking CI.

If field-set drift is reported:
    Fix the side that's wrong. Per service-catalog-onboarding spec §4.1.x,
    code is the truth — re-align api/engagement.openapi.yaml to match the
    Go struct (NOT the other way round). Only update the Go struct if the
    contract change was reviewed and rolled out to all clients.

Coverage:
    The MAPPING table below is intentionally a curated subset (the highest-
    value structs — those carried in request/response bodies that App / SDK
    parses). Adding more is one new entry plus making sure the struct
    parses with the simple regex-based extractor below. Embedded struct
    BaseRequest is unrolled inline so its fields appear on every request.

Dependencies:
    - Python 3.10+ stdlib
    - PyYAML (install inline in CI: `pip install pyyaml`)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    import yaml  # type: ignore
except ImportError:
    print("ERROR: PyYAML not installed. CI: `pip install pyyaml`. "
          "Local: `pip install pyyaml` or `apt install python3-yaml`.",
          file=sys.stderr)
    sys.exit(2)

REPO_ROOT = Path(__file__).resolve().parents[2]
TYPES_DIR = REPO_ROOT / "backend" / "internal" / "types"
OPENAPI_YAML = REPO_ROOT / "api" / "engagement.openapi.yaml"

# Go struct name → OpenAPI components.schemas key.
# Keep this curated; not every internal type belongs on the wire.
MAPPING = {
    "EvaluateReq": "EvaluateReq",
    "EngagementReq": "EngagementReq",
    "ConvertReq": "ConvertReq",
    "TouchpointView": "TouchpointView",
    "CMSWebhookReq": "CMSWebhookReq",
    "TouchpointContext": "TouchpointContext",
}

# Match a `type <Name> struct { ... }` block. Handles single-line definitions
# poorly but Go convention is multi-line with one field per line, which is
# what every struct in backend/internal/types/ uses.
STRUCT_HEADER_RE = re.compile(r"^type\s+(\w+)\s+struct\s*\{\s*$")
# Field with a json tag: `Name Type \`json:"name,omitempty"\``
FIELD_JSON_TAG_RE = re.compile(r'`json:"([^"]+)"')
# Embedded struct: a single capitalized identifier on a line, no json tag.
# Anchor on a line that's just an identifier, optional whitespace.
EMBEDDED_RE = re.compile(r"^\s*([A-Z]\w*)\s*$")


class GoField:
    __slots__ = ("name", "optional")

    def __init__(self, name: str, optional: bool):
        self.name = name
        self.optional = optional

    def __repr__(self) -> str:
        return f"GoField({self.name!r}, optional={self.optional})"


def parse_go_structs(types_dir: Path) -> dict[str, list[GoField]]:
    """Parse all *.go files in types_dir; return {struct_name: [GoField, ...]}.

    Embedded struct fields (e.g. `BaseRequest`) are NOT inlined here — they
    are kept as forward-references and resolved in resolve_embeds() below.
    """
    if not types_dir.is_dir():
        print(f"ERROR: types dir not found at {types_dir}", file=sys.stderr)
        sys.exit(2)

    # Two-pass: collect raw fields incl. embeds → resolve embeds.
    raw: dict[str, list] = {}  # name -> list of (kind, payload)
    # kind="field": payload=GoField; kind="embed": payload=struct_name str.

    for go_file in sorted(types_dir.glob("*.go")):
        text = go_file.read_text(encoding="utf-8")
        lines = text.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i]
            m = STRUCT_HEADER_RE.match(line)
            if not m:
                i += 1
                continue
            struct_name = m.group(1)
            fields: list = []
            i += 1
            depth = 1  # we're inside the opening `{`
            while i < len(lines) and depth > 0:
                inner = lines[i]
                # Track nested braces just in case (not used by current types).
                depth += inner.count("{") - inner.count("}")
                if depth <= 0:
                    break
                stripped = inner.strip()
                if not stripped or stripped.startswith("//"):
                    i += 1
                    continue
                # JSON-tagged field?
                tag = FIELD_JSON_TAG_RE.search(inner)
                if tag:
                    parts = tag.group(1).split(",")
                    json_name = parts[0]
                    optional = "omitempty" in parts[1:]
                    if json_name and json_name != "-":
                        fields.append(("field",
                                       GoField(json_name, optional)))
                    i += 1
                    continue
                # Embedded struct? (line is just `Identifier` with no tag,
                # not a closing brace)
                if not stripped.startswith("}"):
                    em = EMBEDDED_RE.match(inner)
                    if em:
                        fields.append(("embed", em.group(1)))
                i += 1
            raw[struct_name] = fields
    return resolve_embeds(raw)


def resolve_embeds(
    raw: dict[str, list],
) -> dict[str, list[GoField]]:
    """Inline embedded struct fields recursively."""
    resolved: dict[str, list[GoField]] = {}

    def resolve(name: str, seen: set[str]) -> list[GoField]:
        if name in resolved:
            return resolved[name]
        if name in seen:
            # cycle guard; embed cycles aren't legal Go but be safe
            return []
        seen = seen | {name}
        out: list[GoField] = []
        seen_json: set[str] = set()
        for kind, payload in raw.get(name, []):
            if kind == "embed":
                for f in resolve(payload, seen):
                    if f.name in seen_json:
                        continue
                    out.append(f)
                    seen_json.add(f.name)
            else:  # field
                if payload.name in seen_json:
                    continue
                out.append(payload)
                seen_json.add(payload.name)
        resolved[name] = out
        return out

    for name in raw:
        resolve(name, set())
    return resolved


def parse_openapi_schemas(
    yaml_path: Path,
) -> dict[str, tuple[set[str], set[str]]]:
    """Return {schema_name: (property_names, required_names)}."""
    if not yaml_path.is_file():
        print(f"ERROR: openapi.yaml not found at {yaml_path}", file=sys.stderr)
        sys.exit(2)
    with yaml_path.open("r", encoding="utf-8") as f:
        spec = yaml.safe_load(f)
    schemas = (
        spec.get("components", {}).get("schemas", {})
        if isinstance(spec, dict)
        else {}
    )
    out: dict[str, tuple[set[str], set[str]]] = {}
    for name, schema in schemas.items():
        if not isinstance(schema, dict):
            continue
        props = schema.get("properties", {}) or {}
        required = schema.get("required", []) or []
        out[name] = (set(props.keys()), set(required))
    return out


def main() -> int:
    go_structs = parse_go_structs(TYPES_DIR)
    schemas = parse_openapi_schemas(OPENAPI_YAML)

    field_drift = False  # affects exit code
    req_warnings = 0     # informational only
    matched = 0

    for go_name, schema_name in MAPPING.items():
        if go_name not in go_structs:
            print(f"FAIL: Go struct {go_name} not found in "
                  f"backend/internal/types/")
            field_drift = True
            continue
        if schema_name not in schemas:
            print(f"FAIL: OpenAPI schema {schema_name} not found in "
                  f"components.schemas")
            field_drift = True
            continue

        fields = go_structs[go_name]
        code_field_names = {f.name for f in fields}
        code_required = {f.name for f in fields if not f.optional}
        schema_field_names, schema_required = schemas[schema_name]

        missing_in_schema = code_field_names - schema_field_names
        extra_in_schema = schema_field_names - code_field_names
        # Required-set divergence: WARNING only — `omitempty` is a Go JSON
        # marshal flag, not a true requiredness signal (handlers may
        # validate fields without `omitempty` being present, or tolerate
        # zero values without it). Surfaced for human review, not gated.
        common = code_field_names & schema_field_names
        req_in_code_only = (code_required & common) - schema_required
        req_in_schema_only = (schema_required & common) - code_required

        has_field_drift = bool(missing_in_schema) or bool(extra_in_schema)
        has_req_warning = bool(req_in_code_only) or bool(req_in_schema_only)

        if not has_field_drift and not has_req_warning:
            matched += 1
            print(f"OK: {go_name} ↔ {schema_name} "
                  f"({len(code_field_names)} fields)")
            continue

        if has_field_drift:
            field_drift = True
            print(f"FAIL: {go_name} ↔ {schema_name} field-set drift")
            if missing_in_schema:
                print(f"  In code but not in OpenAPI schema "
                      f"({len(missing_in_schema)}):")
                for n in sorted(missing_in_schema):
                    print(f"    + {n}")
            if extra_in_schema:
                print(f"  In OpenAPI schema but not in code "
                      f"({len(extra_in_schema)}):")
                for n in sorted(extra_in_schema):
                    print(f"    - {n} (ghost field)")
        else:
            # field-set matches; just count as matched-with-warning
            matched += 1
            print(f"OK: {go_name} ↔ {schema_name} "
                  f"({len(code_field_names)} fields, with required-set "
                  f"warning)")

        if has_req_warning:
            req_warnings += 1
            if req_in_code_only:
                print(f"  WARN: required in code (no omitempty) but "
                      f"optional in OpenAPI "
                      f"({len(req_in_code_only)}):")
                for n in sorted(req_in_code_only):
                    print(f"    ! {n}")
            if req_in_schema_only:
                print(f"  WARN: required in OpenAPI but optional in code "
                      f"(omitempty) ({len(req_in_schema_only)}):")
                for n in sorted(req_in_schema_only):
                    print(f"    ! {n}")

    print()
    if field_drift:
        print(f"Result: {matched}/{len(MAPPING)} schemas match on "
              f"field-set — field drift detected.")
        print("Fix: align api/engagement.openapi.yaml with the Go struct")
        print("(code is truth) per service-catalog-onboarding spec §4.1.x.")
        print("Only edit the Go struct if the contract change was reviewed.")
        return 1

    if req_warnings:
        print(f"Result: all {matched}/{len(MAPPING)} schemas match on "
              f"field-set; {req_warnings} have required-set warnings "
              f"(informational, not gating).")
    else:
        print(f"Result: all {matched}/{len(MAPPING)} schemas match.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
