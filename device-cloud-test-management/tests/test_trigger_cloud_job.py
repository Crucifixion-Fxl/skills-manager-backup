"""Regression tests for the packaged Device Cloud trigger."""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "trigger_cloud_job.py"
SPEC = importlib.util.spec_from_file_location("device_cloud_trigger", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
TRIGGER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = TRIGGER
SPEC.loader.exec_module(TRIGGER)


def test_missing_tests_ref_resolves_remote_default_branch() -> None:
    completed = subprocess.CompletedProcess(
        args=["git", "ls-remote"],
        returncode=0,
        stdout="ref: refs/heads/main\tHEAD\n",
        stderr="",
    )

    with patch.object(TRIGGER.subprocess, "run", return_value=completed) as run:
        resolved = TRIGGER.resolve_tests_ref("https://example.invalid/tests.git", None)

    assert resolved == "main"
    run.assert_called_once_with(
        [
            "git",
            "ls-remote",
            "--symref",
            "https://example.invalid/tests.git",
            "HEAD",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )


def test_explicit_tests_ref_does_not_query_remote() -> None:
    with patch.object(TRIGGER.subprocess, "run") as run:
        resolved = TRIGGER.resolve_tests_ref(
            "https://example.invalid/tests.git",
            "release-1",
        )

    assert resolved == "release-1"
    run.assert_not_called()


def test_batch_mode_reuses_authenticated_client_without_keychain_read(
    tmp_path: Path,
) -> None:
    batch_file = tmp_path / "batch.json"
    batch_file.write_text(
        json.dumps(
            [{
                "name": "login",
                "tags": "@uid=login",
                "resources": [{"name": "phone", "type": "PHONE"}],
            }]
        ),
        encoding="utf-8",
    )
    args = argparse.Namespace(
        endpoint=None,
        env="prod-cn",
        plan_name="reused-auth-plan",
        metadata=None,
        env_file=None,
        inject_env=None,
        baseline_env_file=None,
        batch=str(batch_file),
        concurrency=1,
        dry_run=False,
        plan_type="manual",
        allow_duplicate_plan=False,
        no_wait=False,
    )
    auth_client = object()

    with (
        patch.object(
            TRIGGER,
            "DeviceCloudAuthClient",
            side_effect=AssertionError("must not authenticate twice"),
        ),
        patch.object(TRIGGER, "create_plan_once", return_value=("123", True)) as create,
        patch.object(TRIGGER, "_run_batch_schedule", return_value=({}, 1)),
        patch.object(TRIGGER, "_print_batch_summary", return_value=0),
    ):
        result = TRIGGER.batch_mode(
            args,
            auth_client=auth_client,
            launched_by="tester@example.com",
        )

    assert result == 0
    assert create.call_args.args[1] is auth_client
    assert create.call_args.args[4] == "tester@example.com"
