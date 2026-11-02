#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""只读核验 Issue 提交/更新回执：同一组实时 GET 同时证明 marker 评论与评审状态。

评论回读单独不构成成功回执。退出码：0 = VERIFIED，或以 closed-reference 角色准入且实时
已关闭的 NOT_APPLICABLE_CLOSED（均为实时，按 result 区分）；1 = BLOCKED /
ISSUE_SYNC_UNVERIFIED（仍输出并可记录结构化回执）；2 = 输入、锁、state 绑定或畸形 history
错误（stderr 为 INPUT_ERROR 固定代码，不写入）。只生成 submitted/revised；merged/final 用
drive_state.py postmerge。
角色只来自 --state history 中 record_issue_binding.py 生成的准入记录；无 --state 或无准入
记录时按 new-work 处理，已关闭 Issue 不会被推断为引用。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import drive_state_io as state_io  # noqa: E402
from issue_binding import binding_for, issue_key, resolve  # noqa: E402
from issue_review_state import (  # noqa: E402
    PHASES,
    SUPPORTED_MODES,
    TERMINAL_STATES,
    UNSUPPORTED_MODES,
    load_owner_host,
    verify,
)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--issue", required=True, help="verified canonical Issue URL")
    result.add_argument("--mr-project", required=True, help="MR project path, e.g. group/project")
    result.add_argument("--mr-iid", required=True, type=int)
    result.add_argument("--revision", required=True, help="exact current MR HEAD SHA")
    result.add_argument("--phase", required=True, choices=PHASES)
    result.add_argument("--note-id", required=True, type=int, help="read-back/reconciled receipt note ID")
    result.add_argument("--project-mode", default="canonical-label", choices=SUPPORTED_MODES + UNSUPPORTED_MODES)
    result.add_argument("--expected-body-file", type=Path, help="exact receipt body that was posted (LF only)")
    result.add_argument("--check-snapshot", type=Path, metavar="FILE",
                        help="offline replay of a GET snapshot; never VERIFIED, never recorded")
    result.add_argument("--state", type=Path, help="drive state; append the receipt to history")
    return result


def binding_from_state(state: dict[str, Any], request: dict[str, Any], host: str | None) -> dict[str, Any] | None:
    """History-resolved binding for this Issue; a retired or conflicting binding cannot get a new receipt."""
    entry = resolve(state["history"], request["mr_iid"], request["mr_project"], host).get(
        issue_key(request["issue_url"], host))
    if entry and entry["conflict"]:
        raise ValueError("binding_role_conflict")
    if entry and entry["status"] == "retired":
        raise ValueError("binding_retired")
    return binding_for(entry)


def append_to_state(path: Path, receipt: dict[str, Any], binding: dict[str, Any] | None = None,
                    host: str | None = None) -> None:
    """Shared locked append; the binding used for the live read must still be the recorded one."""
    request = {key: receipt.get(key) for key in ("issue_url", "mr_iid", "mr_project")}

    def unchanged(state: dict[str, Any]) -> None:
        if binding_from_state(state, request, host) != binding:
            raise ValueError("binding_changed_during_verification")
    state_io.append_protected(path, receipt, head_sha=receipt["revision"], check=unchanged)


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.check_snapshot and args.state:
            raise ValueError("offline_receipt_cannot_be_recorded")
        expected = args.expected_body_file.read_text(encoding="utf-8") if args.expected_body_file else None
        request = {"issue_url": args.issue, "phase": args.phase, "mr_project": args.mr_project,
                   "mr_iid": args.mr_iid, "revision": args.revision, "note_id": args.note_id,
                   "project_mode": args.project_mode, "expected_body": expected}
        snapshot = None
        if args.check_snapshot:
            if args.check_snapshot.stat().st_size > 1024 * 1024:
                raise ValueError("snapshot_too_large")
            snapshot = json.loads(args.check_snapshot.read_text(encoding="utf-8"))
            if not isinstance(snapshot, dict):
                raise ValueError("snapshot_must_be_object")
        host, binding = load_owner_host(), None
        if args.state:  # resolve the role before any network read; the lock is taken only to append
            state = state_io.read_state(args.state)
            state_io.require_binding(state, args.mr_iid, args.mr_project, args.revision)
            binding = binding_from_state(state, request, host)
        receipt = verify(request, snapshot=snapshot, binding=binding)
        if args.state:
            append_to_state(args.state, receipt, binding, host)
    except (OSError, ValueError) as exc:
        code = str(exc) if isinstance(exc, ValueError) else "input_read_failed"
        print(json.dumps({"result": "INPUT_ERROR", "error": code}), file=sys.stderr)
        return 2
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0 if receipt["result"] in TERMINAL_STATES else 1


if __name__ == "__main__":
    raise SystemExit(main())
