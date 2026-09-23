"""Allow ``python -m user_research capabilities`` after a wheel install."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
