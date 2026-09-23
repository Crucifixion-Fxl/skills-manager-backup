"""High-confidence credential scan.

This is not a complete secret scanner. It matches a fixed set of token and
connection-string shapes and ignores split fixtures. It does not replace a
dedicated secret-scanning product.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

EXCLUDED = frozenset(
    {".git", ".pytest_cache", ".ruff_cache", ".venv", "build", "dist", "__pycache__"}
)
MAX_FILE_BYTES = 2 * 1024 * 1024
RULES = (
    ("private_key", re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("gitlab_token", re.compile(rb"glpat-[A-Za-z0-9_-]{20,}")),
    # Contiguous token only. Tests concatenate awpk fixtures so the source text does not match.
    ("audience_personal_key", re.compile(rb"awpk_v[12]_[A-Za-z0-9_-]{24,}")),
    ("openai_key", re.compile(rb"sk-[A-Za-z0-9]{20,}")),
    ("aws_access_key", re.compile(rb"AKIA[0-9A-Z]{16}")),
    ("aws_temporary_access_key", re.compile(rb"ASIA[0-9A-Z]{16}")),
    (
        "aws_secret_access_key",
        re.compile(rb"(?i)aws_secret_access_key[\"'\s:=]{1,8}[A-Za-z0-9/+=]{40}"),
    ),
    (
        "database_url",
        re.compile(rb"(?i)(?:postgres|postgresql|mysql|mongodb(?:\+srv)?)://[^:\s]+:[^@\s]+@"),
    ),
    (
        "bearer_token",
        re.compile(rb"(?i)(?:authorization:\s*)?bearer\s+eyJ[A-Za-z0-9_-]{20,}"),
    ),
)


def findings(root: Path) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root)
        if any(part in EXCLUDED for part in relative.parts):
            continue
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                result.append((relative.as_posix(), "oversized_file"))
                continue
            content = path.read_bytes()
        except OSError:
            result.append((relative.as_posix(), "unreadable_file"))
            continue
        result.extend((relative.as_posix(), rule) for rule in _matching_rules(content))
    return result


def _matching_rules(content: bytes) -> list[str]:
    return [name for name, pattern in RULES if pattern.search(content)]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    discovered = findings(parser.parse_args().root.resolve())
    if discovered:
        for relative, rule in discovered:
            print(f"secret_check_failed:{relative}:{rule}")
        return 1
    print("secret_check_ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
