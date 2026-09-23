"""Explicit compatibility entry point for existing legacy API callers."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main(legacy=True))
