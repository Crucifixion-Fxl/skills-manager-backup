"""Check canonical host configuration without invoking a business route."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from user_research import AudienceClient, AudienceClientConfig, SafeApiError
from user_research.environment import load_local_environment


def main() -> int:
    try:
        load_local_environment(Path(__file__).resolve().parents[1] / ".env.local")
        AudienceClient(AudienceClientConfig.from_environment())
    except (SafeApiError, ValueError) as exc:
        print(f"preflight_failed:{getattr(exc, 'code', 'invalid_configuration')}")
        return 1
    print("preflight_ok:configuration")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
