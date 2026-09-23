#!/usr/bin/env python3
"""Print the Feishu open-platform scope-apply URL for one agent app: every scope in feishu-agent-app-scopes.txt in one link.

Usage: feishu_scope_apply_url.py <app_id>      (app_id looks like cli_xxxxxxxx)
Exit:  0 the URL on stdout; 2 bad usage or app_id (nothing on stdout).

There is no API that adds scopes to an app (see feishu-group-sync.md, LCV-11); the owner opens this link once and confirms.
"""

import re
import sys
from pathlib import Path
from urllib.parse import quote

SCOPES = Path(__file__).resolve().parents[1] / "feishu-agent-app-scopes.txt"


def main(argv: list[str]) -> int:
    if len(argv) != 1 or not re.fullmatch(r"cli_[0-9A-Za-z_]+", argv[0]):
        print("usage: feishu_scope_apply_url.py <app_id>  (app_id like cli_xxxxxxxx)", file=sys.stderr)
        return 2
    scopes = [line.strip() for line in SCOPES.read_text(encoding="utf-8").splitlines()
              if line.strip() and not line.startswith("#")]
    print(f"https://open.feishu.cn/page/scope-apply?clientID={argv[0]}&scopes={quote(','.join(scopes), safe='')}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
