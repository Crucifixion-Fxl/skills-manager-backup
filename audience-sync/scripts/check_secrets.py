"""Deterministic repository secret-pattern gate without secret-value output."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

EXCLUDED_DIRECTORIES = frozenset(
    {".git", ".pytest_cache", ".ruff_cache", ".venv", "build", "dist", "__pycache__"}
)
MAX_FILE_BYTES = 2 * 1024 * 1024
RULES = (
    ("private_key", re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("gitlab_token", re.compile(rb"glpat-[A-Za-z0-9_-]{20,}")),
    ("audience_personal_key", re.compile(rb"awpk_v[12]_[A-Za-z0-9_-]{24,}")),
    ("openai_key", re.compile(rb"sk-[A-Za-z0-9]{20,}")),
    ("aws_access_key", re.compile(rb"AKIA[0-9A-Z]{16}")),
)


def findings(root: Path) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root)
        if any(part in EXCLUDED_DIRECTORIES for part in relative.parts):
            continue
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                result.append((relative.as_posix(), "oversized_file"))
                continue
            content = path.read_bytes()
        except OSError:
            result.append((relative.as_posix(), "unreadable_file"))
            continue
        for name, pattern in RULES:
            if pattern.search(content):
                result.append((relative.as_posix(), name))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    root = parser.parse_args().root.resolve()
    discovered = findings(root)
    if discovered:
        for relative, rule in discovered:
            print(f"secret_check_failed:{relative}:{rule}")
        return 1
    print("secret_check_ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
