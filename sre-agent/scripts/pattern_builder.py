#!/usr/bin/env python3
"""
PatternBuilder — Pattern Extraction subagent 用于构建 pattern_candidate.yaml 的 Builder 类。
"""

import os
import re
from datetime import datetime, timezone

import yaml


_VALID_RISKS = {"critical", "high", "medium", "low"}
_PATTERN_ID_RE = re.compile(r"^[a-z][a-z0-9\-]*$")


class PatternBuilder:
    """Builder for pattern_candidate.yaml files."""

    def __init__(self, cg_id, solution_idx, output_dir):
        self._cg_id = cg_id
        self._solution_idx = solution_idx
        self._output_dir = output_dir
        self._pattern = None

    def set_pattern(self, pattern_id, description, match_conditions, execution_steps, risk):
        """Set the pattern data. Required."""
        if risk not in _VALID_RISKS:
            raise ValueError(
                f"risk must be one of {sorted(_VALID_RISKS)}, got '{risk}'"
            )
        if not _PATTERN_ID_RE.match(pattern_id):
            raise ValueError(
                f"pattern_id must be lowercase letters, digits, and hyphens "
                f"(starting with letter), got '{pattern_id}'"
            )
        self._pattern = {
            "pattern_id": pattern_id,
            "description": description,
            "match_conditions": match_conditions,
            "execution_steps": execution_steps,
            "risk": risk,
        }

    def _validate(self):
        errors = []
        if not self._pattern:
            errors.append("Missing: call set_pattern() before save()")
            return errors
        if not self._pattern["match_conditions"]:
            errors.append("match_conditions must not be empty")
        if not self._pattern["execution_steps"]:
            errors.append("execution_steps must not be empty")
        return errors

    def save(self):
        """Validate and write pattern_candidate.yaml. Returns True/False."""
        errors = self._validate()
        if errors:
            print("VALIDATION ERRORS:")
            for e in errors:
                print(f"  - {e}")
            return False

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        data = {
            "cg_id": self._cg_id,
            "solution_idx": self._solution_idx,
            "created_at": now,
            "pattern": self._pattern,
        }

        os.makedirs(self._output_dir, exist_ok=True)
        path = os.path.join(self._output_dir, "pattern_candidate.yaml")
        with open(path, "w") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        print(f"Pattern candidate saved to {path}")
        return True
