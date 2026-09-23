"""Execute documented shell commands with harmless local command doubles."""
import os
import json
from pathlib import Path
import re
import subprocess

import pytest

SKILL = Path(__file__).resolve().parents[2] / "SKILL.md"


def block_under(heading):
    section = SKILL.read_text().split(heading, 1)[1]
    return re.search(r"```bash\n(.*?)\n```", section, re.S).group(1)


def _python3_argv_shim(tmp_path):
    """PATH shim recording argv and emitting valid collector JSON."""
    bin_dir = tmp_path / "shim-bin"
    bin_dir.mkdir(exist_ok=True)
    log = tmp_path / "argv.log"
    shim = bin_dir / "python3"
    shim.write_text(
        '#!/bin/sh\n'
        'printf "%s\\n" "$@" >> "' + str(log) + '"\n'
        'echo \'{"available":true}\'\n'
    )
    shim.chmod(0o755)
    return bin_dir, log


def test_claude_collector_command_follows_selected_range(tmp_path):
    block = block_under("#### 4.2 Claude Code Usage")
    bin_dir, log = _python3_argv_shim(tmp_path)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}",
           "WEEK_START": "2026-08-31", "WEEK_END": "2026-09-04",
           "SKILL_DIR": str(tmp_path / "skill")}
    r = subprocess.run(["bash", "-c", block + '\nprintf "%s" "$CLAUDE_USAGE_JSON"'],
                       env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    argv = log.read_text().splitlines()
    assert argv[0].endswith("collect_claude_usage.py")
    assert "--since" in argv and argv[argv.index("--since") + 1] == "2026-08-31"
    assert "--until" in argv and argv[argv.index("--until") + 1] == "2026-09-04"
    assert r.stdout.strip() == '{"available":true}'


def test_zcode_collector_command_follows_selected_range(tmp_path):
    block = block_under("#### 4.3 ZCode Local Usage")
    bin_dir, log = _python3_argv_shim(tmp_path)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}",
           "WEEK_START": "2026-08-31", "WEEK_END": "2026-09-04",
           "SKILL_DIR": str(tmp_path / "skill")}
    r = subprocess.run(["bash", "-c", block], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    argv = log.read_text().splitlines()
    assert argv[0].endswith("collect_zcode_usage.py")
    assert argv[argv.index("--since") + 1] == "2026-08-31"
    assert argv[argv.index("--until") + 1] == "2026-09-04"


def test_langfuse_collector_command_follows_selected_range(tmp_path):
    block = block_under("#### 4.0 Langfuse Process Analytics")
    bin_dir, log = _python3_argv_shim(tmp_path)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "WEEK_START": "2026-08-31",
        "WEEK_END": "2026-09-04",
        "SKILL_DIR": str(tmp_path / "skill"),
    }
    result = subprocess.run(["bash", "-c", block], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    argv = log.read_text().splitlines()
    assert argv[0].endswith("collect_langfuse_analytics.py")
    assert argv[argv.index("--since") + 1] == "2026-08-31"
    assert argv[argv.index("--until") + 1] == "2026-09-04"


def test_langfuse_habit_contract_is_profile_based_and_cross_task():
    text = SKILL.read_text()
    section = text.split("#### 4.0 Langfuse Process Analytics", 1)[1].split(
        "**Local Provider Usage", 1
    )[0]

    assert "habits.tdd.distribution" in section
    assert "habits.eval_driven.distribution" in section
    assert "opportunities" in section
    assert "unknown/not_applicable" in section
    assert "不得输出 `composite`" in section
    assert "员工排名" in section
    for work_kind in (
        "coding",
        "skill_authoring",
        "data_analysis",
        "design_docs",
        "operations",
        "research",
    ):
        assert work_kind in section


def test_eval_rule_calibration_requires_one_human_review_task_per_round():
    text = SKILL.read_text()
    section = text.split("### Eval rule calibration inner/outer loop", 1)[1].split(
        "## Data Collection", 1
    )[0]

    assert "one GitLab Task" in section
    assert "round_id" in section
    assert "same round_id" in section
    assert "new round_id" in section
    assert "append-only comment" in section
    assert "human outer-loop" in section
    assert "rule_diff + anonymized golden case" in section


def test_weekly_report_forces_work_method_review_before_publication():
    text = SKILL.read_text()
    section = text.split("### Mandatory work-method review", 1)[1].split(
        "### Eval rule calibration inner/outer loop", 1
    )[0]

    assert "work-method-retrospective" in section
    assert "eval-driven-review" in section
    assert "每次" in section
    assert "至少 3 个独立任务" in section
    assert "awaiting_human_review" in section
    assert "不得自动发布" in section
    assert "最近 4 份周报 / 28 天" in section
    assert "skill-proposal-discovery" in section
    assert "workflow_friction_summary" in section
    assert "workflow_friction_signals_and_impact" in section
    assert "workflow_friction_action" in section
    assert "CI/CD" in section
    assert "data_state" in section
    assert "instrumentation_missing" in section
    assert "query_blocked" in section
    assert "200,000" in section
    assert "120,000" in section


def test_efficiency_eval_has_structured_before_after_slots_in_html():
    text = SKILL.read_text()
    required_slots = {
        "efficiency_gate_mode",
        "efficiency_metric",
        "baseline_input",
        "candidate_input",
        "input_reduction",
        "baseline_scene_reconstruction",
        "candidate_scene_reconstruction",
        "baseline_evidence_traceability",
        "candidate_evidence_traceability",
        "baseline_recommendation_actionability",
        "candidate_recommendation_actionability",
        "baseline_skill_decision_accuracy",
        "candidate_skill_decision_accuracy",
        "baseline_human_readability",
        "candidate_human_readability",
        "required_claim_gate",
        "unsupported_claim_gate",
        "privacy_gate",
        "critical_gate_result",
        "baseline_report_digest_prefix",
        "candidate_report_digest_prefix",
    }

    missing = sorted(slot for slot in required_slots if "{{" + slot + "}}" not in text)
    assert not missing
    assert 'colspan="2"' not in text.split("<b>效率 Eval", 1)[1].split("</table>", 1)[0]
    assert "efficiency_gate_summary" not in text


def test_output_stops_for_digest_bound_human_review_before_any_delivery():
    text = SKILL.read_text()
    output = text.split("## Output", 1)[1].split("## Error Handling", 1)[0]

    review = output.index("awaiting_human_review")
    stop = output.index("stop this turn")
    copy = output.index("copy to `$OUTPUTS_DIR`")
    publish = output.index("`weekly-report-publish`")
    worktime = output.index("`worktime-filing`")
    assert review < stop < copy < publish < worktime
    assert "SHA-256" in output
    assert "new top-level user message" in output
    assert "Recompute the HTML digest" in output
    assert "addx.weekly_report_approval.v1" in output
    assert "--approval-receipt /tmp/weekly-report-<WEEK_END>.approval.json" in output
    assert "Immediately invoke" not in output

    example = text.split("### Good Example", 1)[1].split("### Bad Example", 1)[0]
    assert example.index("工作方法复盘 ⏳ 待确认") < example.index(
        "cp /tmp/weekly-report"
    )
    assert "--approval-receipt /tmp/weekly-report-2026-04-05.approval.json" in example


def test_legacy_keyword_skill_candidate_logic_is_removed():
    text = SKILL.read_text()

    assert "SKILL_INDEX_JSON" not in text
    assert "Skill 化候选" not in text
    assert "建议封装为 skill" not in text
    assert "充分覆盖 ✓" not in text
    assert "work-method-retrospective" in text
    assert "AddX Skill 语义对比" in text


@pytest.mark.parametrize("body,rc", [("", 1), ('{"message":"unauthorized"}', 0), ("", 0)])
def test_pagination_snippet_rejects_failed_or_invalid_response(body, rc):
    block = block_under("### 0. Pagination Helper")
    env = {**os.environ, "FAKE_BODY": body, "FAKE_RC": str(rc), "GITLAB_HOST": "gitlab.addx.ai"}
    stub = 'glab() { printf "%s" "$FAKE_BODY"; return "$FAKE_RC"; };\n'
    r = subprocess.run(["bash", "-c", stub + block + '\nglab_paginate "merge_requests"'],
                       env=env, capture_output=True, text=True)
    assert r.returncode != 0
    assert r.stdout.strip() != "[]"


def test_documented_mr_pipeline_uses_real_selector():
    block = block_under("### 2. User Merge Requests")
    rows = [{"iid": 1, "created_at": "2026-08-31T00:30:00+08:00"},
            {"iid": 2, "created_at": "2026-09-05T00:30:00+08:00"}]
    env = {**os.environ, "WEEK_START": "2026-08-31", "WEEK_END": "2026-09-04",
           "PYTHONPATH": str(SKILL.parent / "scripts"), "SKILL_DIR": str(SKILL.parent),
           "FAKE_BODY": json.dumps(rows)}
    stub = 'glab_paginate() { printf "%s" "$FAKE_BODY"; };\n'
    r = subprocess.run(["bash", "-c", stub + block + '\nprintf "%s" "$MR_GROUPS_JSON"'],
                       env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert [m["iid"] for m in json.loads(r.stdout)["created"]] == [1]


def test_documented_diff_pipeline_preserves_unknown():
    block = block_under("### 3. Per-Project Code Stats")
    env = {**os.environ, "PYTHONPATH": str(SKILL.parent / "scripts"),
           "GITLAB_HOST": "gitlab.addx.ai", "SKILL_DIR": str(SKILL.parent)}
    stub = 'glab() { printf \'%s\' \'{"overflow":true,"changes":[]}\'; };\n'
    r = subprocess.run(["bash", "-c", stub + block + '\nprintf "%s" "$MR_STATS_JSON"'],
                       env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["additions"] is None


@pytest.mark.parametrize("heading,output,body,expected", [
    ("### 2. User Merge Requests", "MR_GROUPS_JSON",
     [{"iid": 1, "created_at": "2026-08-31T00:30:00+08:00"},
      {"iid": 2, "created_at": "2026-09-05T00:30:00+08:00"}],
     {"created": [{"iid": 1, "created_at": "2026-08-31T00:30:00+08:00"}], "merged": [], "closed": []}),
    ("### 3. Per-Project Code Stats", "MR_STATS_JSON",
     {"overflow": False, "changes": [{"diff": "@@ -0,0 +1 @@\n+line"}]},
     {"complete": True, "additions": 1, "deletions": 0}),
])
def test_documented_import_ignores_cwd_and_pythonpath_shadow(tmp_path, heading, output, body, expected):
    # Harmless trap: no network or data access, just fail if the wrong module runs.
    trap = 'raise RuntimeError("untrusted helper was imported")\n'
    (tmp_path / "collect_gitlab_activity.py").write_text(trap)
    env = {**os.environ, "WEEK_START": "2026-08-31", "WEEK_END": "2026-09-04",
           "SKILL_DIR": str(SKILL.parent), "GITLAB_HOST": "gitlab.addx.ai",
           "PYTHONPATH": str(tmp_path), "FAKE_BODY": json.dumps(body)}
    stub = 'glab_paginate() { printf "%s" "$FAKE_BODY"; }; glab() { printf "%s" "$FAKE_BODY"; };\n'
    command = stub + block_under(heading) + '\nprintf "%s" "$' + output + '"'
    result = subprocess.run(["bash", "-c", command], cwd=tmp_path, env=env,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "untrusted helper" not in result.stderr
    assert json.loads(result.stdout) == expected
