#!/usr/bin/env python3
"""
service-catalog-onboarding skill: API route drift gate (CI 用)

用法：cp $SKILL_DIR/scripts/api-drift/check-routes.py <repo>/scripts/api-drift/
然后在 .gitlab-ci.yml 加 api:drift:routes job —— 见 scripts/gitlab-ci-snippets/api-drift.yml

逻辑：从 routes.go（net/http ServeMux 模式）抽 (METHOD, PATH) 集合，跟 openapi.yaml 的 paths: 集合比；
差集非空 → exit 1。当前匹配 go-zero / net/http（Go 1.22+ ServeMux）；其它语言/框架自行调整 routes.go
路径和正则。stdlib only（不依赖 PyYAML —— 内部用手写的 minimal `paths:` 提取器）。

----- 以下保留原 engagement 仓样板的完整 docstring + 实现 -----

check-routes.py — CI drift gate: routes.go ↔ openapi.yaml route-set parity.

Purpose:
    Compare the (METHOD, PATH) tuples registered in
    backend/internal/handler/routes.go (truth = code) with the paths declared
    in api/engagement.openapi.yaml (the published contract). Surface any
    symmetric difference and exit non-zero so CI fails when contract drifts
    from code.

Why this exists:
    drift report e377020 caught 9 BREAKING contract↔code drifts (wrong
    webhook path, missing fields, ghost field, etc). redocly lint only
    catches OpenAPI syntax bugs — it cannot detect that the contract
    advertises a path the code never registered, or that the code added
    a route nobody documented. This script closes that gap.

Exit codes:
    0  — routes match (no drift)
    1  — drift detected; see stdout for the offending entries
    2  — script-internal error (could not parse a truth file)

If drift is reported:
    Fix the side that's wrong. Per service-catalog-onboarding spec §4.1.x,
    code is the truth — the OpenAPI contract should be re-aligned to match
    routes.go (NOT the other way round). Only update routes.go if the
    contract change was deliberate and reviewed.

Stdlib only (no PyYAML — the OpenAPI parser used here is a hand-rolled
minimal `paths:` extractor that doesn't need full YAML support).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ROUTES_GO = REPO_ROOT / "backend" / "internal" / "handler" / "routes.go"
OPENAPI_YAML = REPO_ROOT / "api" / "engagement.openapi.yaml"

# Matches both mux.Handle("METHOD /path", ...) and
# mux.HandleFunc("METHOD /path", ...). Captures method + path.
ROUTE_RE = re.compile(
    r'mux\.Handle(?:Func)?\(\s*"(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+([^"]+)"'
)

HTTP_METHODS = {"get", "post", "put", "delete", "patch", "head", "options"}


def parse_routes_go(path: Path) -> set[tuple[str, str]]:
    """Extract (METHOD, PATH) tuples from routes.go via regex.

    Go 1.22+ ServeMux pattern syntax: "METHOD /path/{param}". Path-param
    syntax {slug} matches OpenAPI {slug}, so no normalization needed.
    """
    if not path.is_file():
        print(f"ERROR: routes.go not found at {path}", file=sys.stderr)
        sys.exit(2)
    text = path.read_text(encoding="utf-8")
    routes: set[tuple[str, str]] = set()
    for m in ROUTE_RE.finditer(text):
        method = m.group(1).upper()
        route_path = m.group(2).strip()
        routes.add((method, route_path))
    return routes


def parse_openapi_paths(path: Path) -> set[tuple[str, str]]:
    """Extract (METHOD, PATH) tuples from OpenAPI YAML.

    Hand-rolled minimal parser that walks `paths:` block top-level keys and
    their nested HTTP method keys. Avoids PyYAML dependency. Assumes 2-space
    indent (the conventional OpenAPI style); the file in question conforms.
    """
    if not path.is_file():
        print(f"ERROR: openapi.yaml not found at {path}", file=sys.stderr)
        sys.exit(2)
    lines = path.read_text(encoding="utf-8").splitlines()

    # Find the line index where top-level `paths:` block starts.
    paths_start = -1
    for i, line in enumerate(lines):
        # top-level key (no leading whitespace) named exactly "paths:"
        if re.match(r"^paths:\s*$", line):
            paths_start = i
            break
    if paths_start < 0:
        print("ERROR: no top-level `paths:` key found in openapi.yaml",
              file=sys.stderr)
        sys.exit(2)

    routes: set[tuple[str, str]] = set()
    current_path: str | None = None
    # Path entries are indented by 2 spaces. Methods under them by 4.
    # Stop when we hit another top-level key (no indent + alpha).
    for line in lines[paths_start + 1:]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        # Top-level key reached → end of paths block.
        if re.match(r"^[A-Za-z]", line):
            break
        # Path entry: 2-space indent, starts with /, ends with `:`.
        m_path = re.match(r"^  (/[^\s:]+)\s*:\s*$", line)
        if m_path:
            current_path = m_path.group(1)
            continue
        # Method under a path: 4-space indent, lowercase verb, ending `:`.
        if current_path is not None:
            m_method = re.match(r"^    ([a-z]+)\s*:\s*$", line)
            if m_method:
                verb = m_method.group(1)
                if verb in HTTP_METHODS:
                    routes.add((verb.upper(), current_path))
    return routes


def main() -> int:
    code_routes = parse_routes_go(ROUTES_GO)
    contract_routes = parse_openapi_paths(OPENAPI_YAML)

    code_only = code_routes - contract_routes
    contract_only = contract_routes - code_routes

    if not code_only and not contract_only:
        print(f"OK: routes match: {len(code_routes)}/{len(contract_routes)}")
        return 0

    print("FAIL: route drift detected")
    if code_only:
        print(f"  In code but not in contract ({len(code_only)}):")
        for method, p in sorted(code_only):
            print(f"    {method} {p}")
    if contract_only:
        print(f"  In contract but not in code ({len(contract_only)}):")
        for method, p in sorted(contract_only):
            print(f"    {method} {p}")
    print()
    print("Fix: align api/engagement.openapi.yaml with routes.go (code is")
    print("truth) per service-catalog-onboarding spec §4.1.x. If a route")
    print("was deliberately added/removed, update both sides in the same MR.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
