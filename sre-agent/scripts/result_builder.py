#!/usr/bin/env python3
"""
ResultBuilder — Execution subagent 用于构建 result.json 的 Builder 类。
"""

import json
import os
from datetime import datetime, timezone


_MIN_SUMMARY_LENGTH = 20


class ResultBuilder:
    """Builder for execution result.json files."""

    def __init__(self, cg_id, solution_idx, output_dir):
        self._cg_id = cg_id
        self._solution_idx = solution_idx
        self._output_dir = output_dir
        self._status = None
        self._summary = None
        self._changes_made = None
        self._verification = None
        self._error_detail = None
        self._skip_reason = None

    def _check_not_set(self):
        if self._status is not None:
            raise ValueError(
                f"Status already set to '{self._status}'. "
                "Cannot call multiple set methods."
            )

    def set_success(self, summary, changes_made=None, verification=None):
        """Mark execution as successful."""
        self._check_not_set()
        self._status = "success"
        self._summary = summary
        self._changes_made = changes_made or []
        self._verification = verification

    def set_failure(self, summary, error_detail):
        """Mark execution as failed."""
        self._check_not_set()
        self._status = "failure"
        self._summary = summary
        self._error_detail = error_detail

    def set_skipped(self, reason):
        """Mark execution as skipped (self-healed)."""
        self._check_not_set()
        self._status = "skipped_self_healed"
        self._summary = reason
        self._skip_reason = reason

    def _validate(self):
        errors = []
        if not self._status:
            errors.append("Missing: call set_success(), set_failure(), or set_skipped()")
        if (
            self._status in ("success", "failure")
            and self._summary
            and len(self._summary) < _MIN_SUMMARY_LENGTH
        ):
            errors.append(
                f"summary too short (min {_MIN_SUMMARY_LENGTH} chars), "
                f"got {len(self._summary)} chars"
            )
        if self._status == "failure" and not self._error_detail:
            errors.append("set_failure() requires non-empty error_detail")
        return errors

    def save(self):
        """Validate and write result.json. Returns True on success, False on failure."""
        errors = self._validate()
        if errors:
            print("VALIDATION ERRORS:")
            for e in errors:
                print(f"  - {e}")
            return False

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        result = {
            "cg_id": self._cg_id,
            "solution_idx": self._solution_idx,
            "status": self._status,
            "executed_at": now,
        }
        if self._status == "success":
            result["summary"] = self._summary
            result["changes_made"] = self._changes_made
            if self._verification:
                result["verification"] = self._verification
        elif self._status == "failure":
            result["summary"] = self._summary
            result["error_detail"] = self._error_detail
            result["rollback_performed"] = False
        elif self._status == "skipped_self_healed":
            result["summary"] = self._summary
            result["reason"] = self._skip_reason

        os.makedirs(self._output_dir, exist_ok=True)
        path = os.path.join(self._output_dir, "result.json")
        with open(path, "w") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"Result saved to {path}")
        return True
