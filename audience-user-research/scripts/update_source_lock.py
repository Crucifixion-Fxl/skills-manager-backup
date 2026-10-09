"""Generate or verify hashes for the distributable Skill source."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "contracts" / "source.json"
CONTRACT_SOURCE = {
    "availability": "reviewed_feature_commit",
    "repository": "services/audiences",
    "revision": "febf67312b1fe32821bba84d7037add7981143bc",
}
CONTRACTS = {
    "audience-platform-public-v1.openapi.json": {
        "sha256": "eb9140c5c4874283b8fa381aed3148a5dd66d426a9f3a23510b6dee5a6e06740",
        "source_path": "audience-workflow/api/audience-platform-public-v1.openapi.json",
    },
    "audience-platform-public-v2.openapi.json": {
        "sha256": "0c610de3b33a4a30c9b7229244cc3aeaa0edf246030d0c4721dbffd7fb4d4325",
        "source_path": "audience-workflow/api/audience-platform-public-v2.openapi.json",
    },
    "project-control-plane.openapi.json": {
        "availability": "reviewed_feature_commit",
        "repository": "services/audiences",
        "revision": "febf67312b1fe32821bba84d7037add7981143bc",
        "sha256": "9b4732b21da0194aecc1ba696ed1ed51f259fa792493b3ba9fcbdc97cd1f2b07",
        "source_path": "audience-workflow/api/project-control-plane.openapi.json",
    },
}
EXCLUDED_PARTS = frozenset(
    {".git", ".pytest_cache", ".ruff_cache", ".venv", "build", "dist", "__pycache__"}
)


def _declared_files() -> tuple[str, ...]:
    roots = (
        ".gitignore",
        "MANIFEST.in",
        "README.md",
        "SKILL.md",
        "agents",
        "contracts",
        "evals",
        "pyproject.toml",
        "references",
        "scripts",
        "setup.py",
        "src",
        "tests",
    )
    found: set[str] = set()
    for entry in roots:
        path = ROOT / entry
        candidates = (path,) if path.is_file() else path.rglob("*")
        for candidate in candidates:
            if not candidate.is_file() or candidate.is_symlink():
                continue
            relative = candidate.relative_to(ROOT)
            if relative.as_posix() == "contracts/source.json":
                continue
            if any(part in EXCLUDED_PARTS or part.endswith(".egg-info") for part in relative.parts):
                continue
            found.add(relative.as_posix())
    return tuple(sorted(found))


def expected_lock() -> dict[str, object]:
    owner = json.loads((ROOT / "contracts/semantic-owner.json").read_text(encoding="utf-8"))
    if set(owner) != {"repository", "origin", "availability", "revision"}:
        raise ValueError("invalid_semantic_owner")
    revision = owner.get("revision")
    if not isinstance(revision, str) or len(revision) != 40:
        raise ValueError("invalid_semantic_owner")
    files = {
        relative: hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        for relative in _declared_files()
    }
    for name, metadata in CONTRACTS.items():
        if files.get(f"contracts/{name}") != metadata["sha256"]:
            raise ValueError("contract_source_mismatch")
    return {
        "schema_version": 5,
        "semantic_owner_file": owner,
        "contract_source": CONTRACT_SOURCE,
        "contracts": CONTRACTS,
        "files": files,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    try:
        expected = json.dumps(expected_lock(), indent=2, sort_keys=True) + "\n"
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        print("source_lock_generation_failed")
        return 1
    if arguments.check:
        if TARGET.read_text(encoding="utf-8") != expected:
            print("source_lock_drift")
            return 1
        print("source_lock_ok")
        return 0
    TARGET.write_text(expected, encoding="utf-8")
    print("source_lock_updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
