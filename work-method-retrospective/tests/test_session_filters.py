from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import os

import pytest


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_ROOT / "scripts" / "extract_session_evidence.sh"
LEDGER_BUILDER = SKILL_ROOT / "scripts" / "build_scene_ledger.py"


def _load_ledger_builder():
    spec = importlib.util.spec_from_file_location("build_scene_ledger", LEDGER_BUILDER)
    if spec is None or spec.loader is None:
        raise RuntimeError("scene ledger builder could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(provider: str, fixture: Path) -> list[dict]:
    home = fixture.parent.parent
    result = subprocess.run(
        ["bash", str(SCRIPT), "--provider", provider, "--file", str(fixture)],
        capture_output=True,
        text=True,
        check=False,
        env={
            **os.environ,
            "HOME": str(home),
            "CODEX_HOME": str(home / ".codex"),
        },
    )
    assert result.returncode == 0, result.stderr
    return [json.loads(line) for line in result.stdout.splitlines() if line]


def _extracted_text_record(text: str, *, at: str = "2026-09-16T04:00:00Z") -> dict:
    return {
        "kind": "human_input",
        "provider": "codex",
        "at": at,
        "session_kind": "root",
        "trust": "untrusted_evidence",
        "text": text,
        "text_truncated": False,
    }


def test_scene_ledger_is_deterministically_built_from_extractor_records(tmp_path):
    evidence = tmp_path / "evidence.jsonl"
    evidence.write_text(
        "\n".join(
            json.dumps(row, ensure_ascii=False)
            for row in (
                _extracted_text_record("需要先定义验收方法"),
                _extracted_text_record("已补上回归测试", at="2026-09-16T04:01:00Z"),
            )
        )
        + "\n",
        encoding="utf-8",
    )
    evidence.chmod(0o600)
    selection = tmp_path / "selection.json"
    selection.write_text(
        json.dumps(
            {
                "scenes": [
                    {
                        "id": "scene-a",
                        "summary": "人先要求定义验收方法，随后补上了回归测试。",
                        "record_lines": [1, 2],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    selection.chmod(0o600)
    artifact_root = tmp_path / "private"
    output = artifact_root / "round-1" / "quality" / "frozen-snapshot.json"
    output.parent.mkdir(parents=True)
    artifact_root.chmod(0o700)
    output.parent.chmod(0o700)
    builder = _load_ledger_builder()
    builder.write_ledger(
        builder.build_ledger(evidence, selection),
        output,
        artifact_root=artifact_root,
    )
    document = json.loads(output.read_text(encoding="utf-8"))
    scene = document["scenes"][0]
    assert scene["content"] == "人先要求定义验收方法，随后补上了回归测试。"
    assert scene["observed_at"] == "2026-09-16T04:01:00Z"
    assert scene["weekly_report_id"] == "2026-W38"
    assert len(scene["source_records"]) == 2
    assert "text" not in json.dumps(scene["source_records"], ensure_ascii=False)
    assert "需要先定义验收方法" not in json.dumps(
        scene["source_records"], ensure_ascii=False
    )
    assert output.stat().st_mode & 0o777 == 0o600


def test_scene_ledger_builder_rejects_unredacted_sensitive_content(tmp_path):
    evidence = tmp_path / "evidence.jsonl"
    evidence.write_text(
        json.dumps(_extracted_text_record("Authorization: Bearer sk-FAKESECRET123"))
        + "\n",
        encoding="utf-8",
    )
    evidence.chmod(0o600)
    selection = tmp_path / "selection.json"
    selection.write_text(
        json.dumps(
            {
                "scenes": [
                    {
                        "id": "scene-a",
                        "summary": "含敏感数据的记录",
                        "record_lines": [1],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    selection.chmod(0o600)
    builder = _load_ledger_builder()
    with pytest.raises(builder.VALIDATOR.ContractError, match="unredacted sensitive data"):
        builder.build_ledger(evidence, selection)
    output = tmp_path / "private" / "frozen-snapshot.json"
    assert not output.exists()


def test_scene_ledger_builder_rejects_blank_lines_and_output_escape(tmp_path):
    builder = _load_ledger_builder()
    evidence = tmp_path / "evidence.jsonl"
    evidence.write_text(
        json.dumps(_extracted_text_record("safe evidence")) + "\n\n",
        encoding="utf-8",
    )
    evidence.chmod(0o600)
    selection = tmp_path / "selection.json"
    selection.write_text(
        json.dumps(
            {
                "scenes": [
                    {
                        "id": "scene-a",
                        "summary": "safe summary",
                        "record_lines": [1],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    selection.chmod(0o600)
    with pytest.raises(ValueError, match="must not be blank"):
        builder.build_ledger(evidence, selection)

    evidence.write_text(
        json.dumps(_extracted_text_record("safe evidence")) + "\n",
        encoding="utf-8",
    )
    evidence.chmod(0o600)
    document = builder.build_ledger(evidence, selection)
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir(mode=0o700)
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    with pytest.raises(ValueError, match="fixed work-method state root"):
        builder.write_ledger(
            document,
            outside / "frozen-snapshot.json",
            artifact_root=artifact_root,
        )


def test_codex_filter_keeps_evidence_and_drops_reasoning_and_raw_tool_data(tmp_path):
    fixture = tmp_path / ".codex" / "codex.jsonl"
    fixture.parent.mkdir()
    fixture.write_text(
        "\n".join(
            json.dumps(row)
            for row in (
                {"timestamp": "2026-09-16T01:00:00Z", "type": "session_meta", "payload": {"id": "s1", "cwd": "/secret/path"}},
                {"timestamp": "2026-09-16T01:00:01Z", "type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "<environment_context>injected</environment_context>"}]}},
                {"timestamp": "2026-09-16T01:00:02Z", "type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "请修复登录问题"}]}},
                {"timestamp": "2026-09-16T01:00:03Z", "type": "response_item", "payload": {"type": "reasoning", "summary": "private chain of thought"}},
                {"timestamp": "2026-09-16T01:00:04Z", "type": "response_item", "payload": {"type": "custom_tool_call", "name": "exec_command", "call_id": "c1", "arguments": "SECRET_ARGUMENT"}},
                {"timestamp": "2026-09-16T01:00:05Z", "type": "response_item", "payload": {"type": "custom_tool_call_output", "call_id": "c1", "output": "SECRET_OUTPUT"}},
                {"timestamp": "2026-09-16T01:00:05Z", "type": "response_item", "payload": {"type": "custom_tool_call_output", "call_id": "c2", "output": [{"type": "input_text", "text": "{\"exit_code\": 1, \"output\": \"SECRET_FAILURE\"}"}]}},
                {"timestamp": "2026-09-16T01:00:06Z", "type": "response_item", "payload": {"type": "message", "role": "assistant", "phase": "final_answer", "content": [{"type": "output_text", "text": "修复完成"}]}},
                {"timestamp": "2026-09-16T01:00:07Z", "type": "event_msg", "payload": {"type": "item_completed", "item": {"text": "duplicate"}}},
            )
        )
        + "\n",
        encoding="utf-8",
    )

    rows = _run("codex", fixture)
    assert [row["kind"] for row in rows] == [
        "session",
        "human_input",
        "tool_call",
        "tool_result",
        "tool_result",
        "ai_output",
    ]
    rendered = json.dumps(rows, ensure_ascii=False)
    assert "请修复登录问题" in rendered
    assert "修复完成" in rendered
    assert "private chain of thought" not in rendered
    assert "SECRET_ARGUMENT" not in rendered
    assert "SECRET_OUTPUT" not in rendered
    assert "SECRET_FAILURE" not in rendered
    assert "/secret/path" not in rendered
    assert rows[2]["tool"] == "exec_command"
    assert rows[3]["content_characters"] == len("SECRET_OUTPUT")
    assert rows[3]["status"] == "unknown"
    assert rows[4]["status"] == "error"


def test_claude_filter_keeps_visible_text_and_tool_status_only(tmp_path):
    fixture = tmp_path / ".claude" / "claude.jsonl"
    fixture.parent.mkdir()
    fixture.write_text(
        "\n".join(
            json.dumps(row)
            for row in (
                {"timestamp": "2026-09-16T02:00:00Z", "type": "user", "sessionId": "s2", "message": {"content": "分析失败原因"}},
                {"timestamp": "2026-09-16T02:00:01Z", "type": "assistant", "sessionId": "s2", "message": {"content": [{"type": "thinking", "thinking": "private reasoning"}, {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "/secret/file"}}, {"type": "text", "text": "找到原因"}]}},
                {"timestamp": "2026-09-16T02:00:02Z", "type": "user", "sessionId": "s2", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "is_error": False, "content": "SECRET_RESULT"}]}},
            )
        )
        + "\n",
        encoding="utf-8",
    )

    rows = _run("claude", fixture)
    assert [row["kind"] for row in rows] == [
        "human_input",
        "tool_call",
        "ai_output",
        "tool_result",
    ]
    rendered = json.dumps(rows, ensure_ascii=False)
    assert "分析失败原因" in rendered
    assert "找到原因" in rendered
    assert "private reasoning" not in rendered
    assert "/secret/file" not in rendered
    assert "SECRET_RESULT" not in rendered
    assert rows[-1]["content_characters"] == len("SECRET_RESULT")
    assert rows[-1]["status"] == "success"


def test_subagent_prompts_are_not_mislabeled_as_human_input(tmp_path):
    codex = tmp_path / ".codex" / "codex-subagent.jsonl"
    codex.parent.mkdir()
    codex.write_text(
        "\n".join(
            json.dumps(row)
            for row in (
                {"timestamp": "2026-09-16T03:00:00Z", "type": "session_meta", "payload": {"id": "s3", "source": {"subagent": {"thread_spawn": {"parent_thread_id": "p1"}}}}},
                {"timestamp": "2026-09-16T03:00:01Z", "type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Review this diff"}]}},
            )
        )
        + "\n",
        encoding="utf-8",
    )
    claude = tmp_path / ".claude" / "claude-subagent.jsonl"
    claude.parent.mkdir()
    claude.write_text(
        json.dumps(
            {
                "timestamp": "2026-09-16T03:00:00Z",
                "type": "user",
                "sessionId": "s4",
                "isSidechain": True,
                "agentId": "agent-1",
                "message": {"content": "Review this diff"},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    for provider, fixture in (("codex", codex), ("claude", claude)):
        rows = _run(provider, fixture)
        assert not any(row["kind"] == "human_input" for row in rows)
        assert any(row["kind"] == "agent_instruction" for row in rows)


def test_filters_redact_sensitive_text_and_bound_untrusted_fields(tmp_path):
    fixture = tmp_path / ".codex" / "redaction.jsonl"
    fixture.parent.mkdir()
    long_text = "x" * 4500
    fixture.write_text(
        "\n".join(
            json.dumps(row)
            for row in (
                {
                    "timestamp": "2026-09-16T04:00:00Z",
                    "type": "session_meta",
                    "payload": {"id": "session with spaces"},
                },
                {
                    "timestamp": "2026-09-16T04:00:01Z",
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": (
                                    "token=glpat-example-secret user@example.com "
                                    "/home/jchen/private/file "
                                    "config={\"password\":\"json-secret\",\"api_key\":\"json-key\"} "
                                    "token=generic-secret "
                                    "eyJabcdefghijk.eyJabcdefghijk.abcdefghijk "
                                    "/root/private/file C:\\Users\\alice\\private.txt "
                                    "AWS_SECRET_ACCESS_KEY=FAKEAWSSECRET1234567890 "
                                    "LITELLM_MASTER_KEY=FAKELITELLM123456 "
                                    "//registry.npmjs.org/:_authToken=npm_FAKE1234567890 "
                                    "github_pat_FAKE12345678901234567890 "
                                    + long_text
                                ),
                            }
                        ],
                    },
                },
                {
                    "timestamp": "2026-09-16T04:00:02Z",
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {"type": "output_text", "text": "Authorization: Bearer example-secret"}
                        ],
                    },
                },
                {
                    "timestamp": "2026-09-16T04:00:03Z",
                    "type": "response_item",
                    "payload": {
                        "type": "custom_tool_call",
                        "name": "bad tool name <script>",
                        "call_id": "c" * 200,
                    },
                },
            )
        )
        + "\n",
        encoding="utf-8",
    )

    rows = _run("codex", fixture)
    rendered = json.dumps(rows, ensure_ascii=False)
    assert "glpat-example-secret" not in rendered
    assert "user@example.com" not in rendered
    assert "/home/jchen/private/file" not in rendered
    assert "example-secret" not in rendered
    assert "json-secret" not in rendered
    assert "json-key" not in rendered
    assert "generic-secret" not in rendered
    assert "eyJabcdefghijk" not in rendered
    assert "/root/private/file" not in rendered
    assert "C:\\\\Users\\\\alice" not in rendered
    assert "FAKEAWSSECRET" not in rendered
    assert "FAKELITELLM" not in rendered
    assert "npm_FAKE" not in rendered
    assert "github_pat_FAKE" not in rendered
    assert "[REDACTED_CREDENTIAL]" in rendered
    assert "[REDACTED_EMAIL]" in rendered
    assert "$HOME/[REDACTED_PATH]" in rendered
    assert all(row["trust"] == "untrusted_evidence" for row in rows)
    assert rows[1]["text_truncated"] is True
    assert len(rows[1]["text"]) == 4000
    assert len(rows[-1]["call_id"]) == 128
    assert "<" not in rows[-1]["tool"]


def test_synthetic_recommended_plugins_is_not_human_input(tmp_path):
    for provider, root in (("codex", ".codex"), ("claude", ".claude")):
        fixture = tmp_path / provider / root / "synthetic.jsonl"
        fixture.parent.mkdir(parents=True)
        if provider == "codex":
            row = {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "<recommended_plugins>x</recommended_plugins>"}
                    ],
                },
            }
        else:
            row = {
                "type": "user",
                "sessionId": "s5",
                "message": {"content": "<recommended_plugins>x</recommended_plugins>"},
            }
        fixture.write_text(json.dumps(row) + "\n", encoding="utf-8")
        assert _run(provider, fixture) == []


def test_extractor_rejects_outside_symlink_and_oversized_inputs(tmp_path):
    home = tmp_path / "home"
    root = home / ".codex"
    root.mkdir(parents=True)
    outside = tmp_path / "outside.jsonl"
    outside.write_text("{}\n", encoding="utf-8")
    environment = {
        **os.environ,
        "HOME": str(home),
        "CODEX_HOME": str(root),
    }

    def run(path: Path):
        return subprocess.run(
            ["bash", str(SCRIPT), "--provider", "codex", "--file", str(path)],
            capture_output=True,
            text=True,
            check=False,
            env=environment,
        )

    result = run(outside)
    assert result.returncode == 2
    assert "outside the provider session root" in result.stderr

    linked = root / "linked.jsonl"
    linked.symlink_to(outside)
    result = run(linked)
    assert result.returncode == 2
    assert "must not be a symlink" in result.stderr

    oversized = root / "oversized.jsonl"
    with oversized.open("wb") as stream:
        stream.truncate(268435457)
    result = run(oversized)
    assert result.returncode == 3
    limited = json.loads(result.stderr.splitlines()[-1])
    assert limited == {
        "status": "limited",
        "coverage": "needs-evidence",
        "reason": "file_size_limit",
        "scanned_records": 0,
        "uncovered_records": 1,
        "uncovered_files": 1,
    }


def test_extractor_avoids_gnu_only_option_terminators():
    """`sed -n '1,50p' -- FILE` broke the extractor on BSD sed (macOS).

    BSD sed treats `--` as a filename, prints "sed: --: No such file or
    directory" and exits non-zero, which under `set -e` aborted the script
    before any record was emitted. Guard the source so the pattern cannot
    return: session_kind detection must not depend on a GNU-only extension.
    """
    source = SCRIPT.read_text(encoding="utf-8")
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        assert "sed -n" not in stripped, f"non-portable sed reintroduced: {stripped}"


@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_extractor_detects_session_kind_on_bsd_and_gnu(tmp_path, provider):
    """End-to-end guard: the extractor must emit records and resolve
    session_kind on whichever sed/awk flavour the host ships.

    This is the regression that mattered in practice — on macOS the extractor
    exited 1 with zero records, so every retrospective silently degraded to
    needs-evidence while CI (GNU userland) stayed green.
    """
    if provider == "codex":
        root = tmp_path / ".codex"
        session = root / "sessions" / "rollout-test.jsonl"
        records = [
            {
                "type": "session_meta",
                "timestamp": "2026-09-16T04:00:00Z",
                "payload": {"id": "sess-1", "source": {"cli": "codex"}},
            },
            {
                "type": "response_item",
                "timestamp": "2026-09-16T04:01:00Z",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "先定义验收标准"}],
                },
            },
        ]
        env_key = "CODEX_HOME"
    else:
        root = tmp_path / ".claude"
        session = root / "projects" / "demo" / "session.jsonl"
        records = [
            {
                "type": "user",
                "sessionId": "sess-1",
                "timestamp": "2026-09-16T04:01:00Z",
                "isSidechain": False,
                "message": {"role": "user", "content": "先定义验收标准"},
            }
        ]
        # Claude's allowed root is derived as $HOME/.claude, so only HOME is set.
        env_key = None

    session.parent.mkdir(parents=True)
    session.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8"
    )

    env = {**os.environ, "HOME": str(tmp_path)}
    if env_key:
        env[env_key] = str(root)
    result = subprocess.run(
        ["bash", str(SCRIPT), "--provider", provider, "--file", str(session)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode == 0, f"extractor failed: {result.stderr}"
    emitted = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    assert emitted, "extractor produced no records"
    # A root session's user text is the human's own input, never a delegation.
    assert all(record["session_kind"] == "root" for record in emitted)
    assert any(record["kind"] == "human_input" for record in emitted)
