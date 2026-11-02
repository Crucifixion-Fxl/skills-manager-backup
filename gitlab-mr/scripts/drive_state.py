#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""gitlab-mr Driver state 的共享写入口：加锁、锁内重读、绑定校验、原子替换。

子命令：event（Driver 返回 / 恢复 / post_merge_local_cleanup 事件）、snapshot（Supervisor
liveness）、waiver、rejection、complete、postmerge（合并后 merged/final Issue receipt）。

记录归属：kind=issue_sync（submitted/revised 评审 receipt）只由 verify_issue_sync_receipt.py
生成；kind=issue_binding（admit/retire）只由 record_issue_binding.py 生成；event 拒绝这两类及
issue_sync_postmerge。postmerge 只接受固定字段（phase=merged|final、issue_url、revision、
note_id、disposition、observed_state，可选 note_url/next_action），写成 kind=issue_sync_postmerge；
它记录父会话自己的原生回读，本脚本不读 GitLab，不证明合并、关闭或业务验收，也不算评审 receipt。

示例：
  drive_state.py event --state "$STATE_FILE" --mr-iid 205 --project-path group/proj \
    --json '{"round": 3, "status": "driver-return"}'
  drive_state.py postmerge --state "$STATE_FILE" --mr-iid 205 --project-path group/proj \
    --json '{"phase": "merged", "issue_url": "https://<host>/group/proj/-/issues/24",
             "revision": "<merge SHA>", "note_id": 831020, "disposition": "kept-open",
             "observed_state": "opened"}'

网络读取在调用前完成。退出码：0 = 已写入（stdout 输出 JSON）；2 = 输入、锁或 state 绑定错误
（stderr 输出 {"result": "INPUT_ERROR", "error": <固定代码>}，未写入）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import drive_state_io as io  # noqa: E402


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    sub = result.add_subparsers(dest="command", required=True)
    for name in ("event", "snapshot", "waiver", "rejection", "complete", "postmerge"):
        command = sub.add_parser(name)
        command.add_argument("--state", required=True, type=Path)
        command.add_argument("--mr-iid", required=True, type=int)
        command.add_argument("--project-path", required=True)
        if name in ("event", "waiver", "postmerge"):
            command.add_argument("--json", required=True, dest="payload", help="one JSON object")
        if name in ("snapshot", "complete"):
            command.add_argument("--head-sha", required=True)
        if name == "snapshot":
            command.add_argument("--pipeline-id", required=True, type=int)
            command.add_argument("--unresolved-discussions", required=True, type=int)
        if name == "rejection":
            command.add_argument("--fingerprint", required=True)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    scope = {"mr_iid": args.mr_iid, "project_path": args.project_path}
    try:
        output: dict[str, object] = {"status": "written", "command": args.command}
        if args.command in ("event", "waiver", "postmerge"):
            try:
                payload = json.loads(args.payload)
            except json.JSONDecodeError:
                raise io.StateError("payload_not_json") from None
            if args.command == "postmerge":
                from issue_review_state import load_owner_host  # noqa: PLC0415

                record = io.append_postmerge(args.state, payload, host=load_owner_host(), **scope)
                output.update(kind=record["kind"], phase=record["phase"])
            else:
                (io.append_event if args.command == "event" else io.append_waiver)(args.state, payload, **scope)
        elif args.command == "snapshot":
            output["no_change_streak"] = io.record_snapshot(
                args.state, head_sha=args.head_sha, pipeline_id=args.pipeline_id,
                unresolved_discussions=args.unresolved_discussions, **scope)
        elif args.command == "rejection":
            output["count"] = io.record_rejection(args.state, args.fingerprint, **scope)
        else:
            io.mark_completed(args.state, head_sha=args.head_sha, **scope)
    except (OSError, ValueError) as exc:
        code = str(exc) if isinstance(exc, io.StateError) else "state_io_failed"
        print(json.dumps({"result": "INPUT_ERROR", "error": code}), file=sys.stderr)
        return 2
    print(json.dumps(output, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
