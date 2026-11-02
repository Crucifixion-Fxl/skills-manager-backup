#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""只读实时核验并记录 Issue 绑定准入（admit）或受控退休（retire），追加到 drive state history。

admit：--binding-role new-work（实时 opened、有 assignee、MR 为 related MR）或 closed-reference
（release 回流引用，实时 opened/closed）。同一 Issue 首次记录的角色固定，不能为得到
NOT_APPLICABLE_CLOSED 改标。retire：MR 描述已不再以任何形式引用该 Issue（声明行、closing
关键字、URL、#iid、group/project#iid）、身份与 MR HEAD 实时一致且给出原因；历史 cross-reference
可保留，记录不声称原生解绑。不写 GitLab。退出码：0 = 已记录；1 = REJECTED（输出结构化结果，
不写入）；2 = 输入、锁、state 绑定或畸形 history 错误（stderr 为 INPUT_ERROR 固定代码）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import drive_state_io as state_io  # noqa: E402
from issue_binding import history_errors, issue_key, produce, resolve  # noqa: E402
from issue_review_state import ROLES, load_owner_host  # noqa: E402


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    sub = result.add_subparsers(dest="action", required=True)
    for action in ("admit", "retire"):
        command = sub.add_parser(action)
        command.add_argument("--state", required=True, type=Path)
        command.add_argument("--issue", required=True, help="verified canonical Issue URL")
        command.add_argument("--mr-project", required=True)
        command.add_argument("--mr-iid", required=True, type=int)
        command.add_argument("--revision", required=True, help="exact current MR HEAD SHA")
        if action == "admit":
            command.add_argument("--binding-role", required=True, choices=ROLES)
        else:
            command.add_argument("--reason", required=True, help="one-line scoped retirement reason")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    request = {"issue_url": args.issue, "mr_project": args.mr_project, "mr_iid": args.mr_iid,
               "revision": args.revision, "binding_role": getattr(args, "binding_role", None),
               "reason": getattr(args, "reason", None)}
    try:
        host = load_owner_host()
        state = state_io.read_state(args.state)
        state_io.require_binding(state, args.mr_iid, args.mr_project, args.revision)
        record = produce(args.action, request, state["history"], host=host)  # live GETs, no lock held

        def still_valid(locked_state: dict) -> None:
            entry = resolve(locked_state["history"], args.mr_iid, args.mr_project, host).get(
                issue_key(args.issue, host))
            if history_errors(args.action, entry, record["binding_role"]):
                raise state_io.StateError("binding_history_changed")
        if record["result"] != "REJECTED":
            state_io.append_protected(args.state, record, head_sha=args.revision, check=still_valid)
    except (OSError, ValueError) as exc:
        code = str(exc) if isinstance(exc, ValueError) else "input_read_failed"
        print(json.dumps({"result": "INPUT_ERROR", "error": code}), file=sys.stderr)
        return 2
    print(json.dumps(record, ensure_ascii=False, sort_keys=True))
    return 1 if record["result"] == "REJECTED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
