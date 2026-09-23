import importlib.util
import json
from pathlib import Path


EVAL_DIR = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "worktime_eval_contract", EVAL_DIR / "prepare_cases.py"
)
CONTRACT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONTRACT)


def test_every_eval_has_one_canonical_message_sequence():
    raw = json.loads((EVAL_DIR / "evals.json").read_text(encoding="utf-8"))
    cases = CONTRACT.load_cases(EVAL_DIR / "evals.json")
    assert len(cases) == len(raw["evals"]) == 43
    assert [case["id"] for case in cases] == list(range(1, 44))
    assert all(case["messages"][-1]["role"] == "user" for case in cases)
    assert all(
        ("prompt" in evaluation) != ("messages" in evaluation)
        for evaluation in raw["evals"]
    )


def test_assignment_confirmation_receives_hidden_preview_token():
    cases = {case["id"]: case for case in CONTRACT.load_cases(EVAL_DIR / "evals.json")}
    messages = cases[13]["messages"]
    tool_message = next(message for message in messages if message["role"] == "tool")
    tool_payload = json.loads(tool_message["content"])
    token = tool_payload["data"]["confirmation_token"]
    visible_assistant_text = " ".join(
        message.get("content", "")
        for message in messages
        if message["role"] == "assistant"
    )
    assert token == "eval_opaque_token_13"
    assert token not in visible_assistant_text
    assert token in cases[13]["assertions"][0]["check"]


def test_short_week_uses_dynamic_work_calendar_limit():
    cases = {case["id"]: case for case in CONTRACT.load_cases(EVAL_DIR / "evals.json")}
    short_week = cases[20]
    contract_text = " ".join(
        [short_week["expected_output"]]
        + [assertion["check"] for assertion in short_week["assertions"]]
    )
    assert "max_hours=24" in short_week["messages"][0]["content"]
    assert "20 \u5c0f\u65f6" in contract_text
    assert "\u4e0d\u63d0\u4ea4" in contract_text


def test_short_week_update_fails_closed_before_patch():
    cases = {case["id"]: case for case in CONTRACT.load_cases(EVAL_DIR / "evals.json")}
    short_week_update = cases[21]
    contract_text = " ".join(
        [short_week_update["expected_output"]]
        + [assertion["check"] for assertion in short_week_update["assertions"]]
    )
    assert "max_hours=24" in short_week_update["messages"][0]["content"]
    assert "28 \u5c0f\u65f6" in contract_text
    assert "\u4e0d\u8c03\u7528 PATCH" in contract_text


def test_cross_week_batch_is_previewed_serial_and_fail_closed():
    cases = {case["id"]: case for case in CONTRACT.load_cases(EVAL_DIR / "evals.json")}
    preview_text = " ".join(
        [cases[22]["expected_output"]]
        + [assertion["check"] for assertion in cases[22]["assertions"]]
    )
    serial_text = " ".join(
        [cases[23]["expected_output"]]
        + [assertion["check"] for assertion in cases[23]["assertions"]]
    )
    failure_text = " ".join(
        [cases[24]["expected_output"]]
        + [assertion["check"] for assertion in cases[24]["assertions"]]
    )
    unassigned_text = " ".join(
        [cases[25]["expected_output"]]
        + [assertion["check"] for assertion in cases[25]["assertions"]]
    )
    drift_text = " ".join(
        [cases[26]["expected_output"]]
        + [assertion["check"] for assertion in cases[26]["assertions"]]
    )
    size_limit_text = " ".join(
        [cases[27]["expected_output"]]
        + [assertion["check"] for assertion in cases[27]["assertions"]]
    )
    invalid_confirmation_text = " ".join(
        [cases[28]["expected_output"]]
        + [assertion["check"] for assertion in cases[28]["assertions"]]
    )
    invalid_week_text = " ".join(
        [cases[29]["expected_output"]]
        + [assertion["check"] for assertion in cases[29]["assertions"]]
    )
    reauth_text = " ".join(
        [cases[30]["expected_output"]]
        + [assertion["check"] for assertion in cases[30]["assertions"]]
    )
    pending_text = " ".join(
        [cases[31]["expected_output"]]
        + [assertion["check"] for assertion in cases[31]["assertions"]]
    )
    lost_response_text = " ".join(
        [cases[32]["expected_output"]]
        + [assertion["check"] for assertion in cases[32]["assertions"]]
    )
    capability_text = " ".join(
        [cases[33]["expected_output"]]
        + [assertion["check"] for assertion in cases[33]["assertions"]]
    )
    same_key_text = " ".join(
        [cases[34]["expected_output"]]
        + [assertion["check"] for assertion in cases[34]["assertions"]]
    )
    conflicting_key_text = " ".join(
        [cases[35]["expected_output"]]
        + [assertion["check"] for assertion in cases[35]["assertions"]]
    )
    confirmation_negatives = [cases[case_id] for case_id in (36, 37, 38)]
    capability_negatives = [cases[case_id] for case_id in (39, 40)]
    multiweek_recovery_text = " ".join(
        [cases[41]["expected_output"]]
        + [assertion["check"] for assertion in cases[41]["assertions"]]
    )
    rate_limit_text = " ".join(
        [cases[42]["expected_output"]]
        + [assertion["check"] for assertion in cases[42]["assertions"]]
    )
    queue_failure_text = " ".join(
        [cases[43]["expected_output"]]
        + [assertion["check"] for assertion in cases[43]["assertions"]]
    )

    assert "\u786e\u8ba4\u6279\u91cf\u8865\u586b" in preview_text
    assert "\u4e0d\u63d0\u4ea4" in preview_text
    assert "\u81f3\u5c11\u95f4\u9694 5 \u79d2" in serial_text
    assert "\u4e0d\u5e76\u53d1" in serial_text
    assert "W36 \u4e0d\u53d1\u9001 POST" in failure_text
    assert "\u672a\u6267\u884c" in failure_text
    assert "\u4e0d\u5e73\u5747\u644a\u5206" in unassigned_text
    assert "\u4efb\u4f55 POST \u524d" in drift_text
    assert "\u65e7\u6279\u6b21\u786e\u8ba4\u5931\u6548" in drift_text
    assert "2\u20138" in size_limit_text
    assert "9 \u5468\u8bf7\u6c42\u4e0d\u6267\u884c" in size_limit_text
    assert "5+4" in size_limit_text
    assert "8+1" in size_limit_text
    assert cases[28]["messages"][-1]["content"] == "yes"
    assert "\u7f3a\u5468\u3001\u591a\u5468\u6216\u6362\u5e8f" in invalid_confirmation_text
    assert "\u4e0d\u53d1\u9001 POST" in invalid_confirmation_text
    assert "\u6574\u6279\u4e0d\u8fdb\u5165\u786e\u8ba4" in invalid_week_text
    assert "\u4e0d\u731c 40 \u5c0f\u65f6" in invalid_week_text
    assert "401" in reauth_text
    assert "user/info" in reauth_text
    assert "\u65e7\u5feb\u7167\u548c\u786e\u8ba4\u5931\u6548" in reauth_text
    assert "UNKNOWN/PENDING" in pending_text
    assert "\u540c\u4e00 task_id" in pending_text
    assert "\u4e0d\u91cd\u53d1 POST" in pending_text
    assert "\u65e0 task_id" in lost_response_text
    assert "status/by-operation-key" in lost_response_text
    assert "operation_key" in lost_response_text
    assert "\u7981\u6b62\u65b0\u63d0\u4ea4" in lost_response_text
    assert "retro_batch_operation_key_v1=true" in capability_text
    assert "0 POST" in capability_text
    assert "\u539f task_id/status" in same_key_text
    assert "\u4e0d\u91cd\u590d\u5165\u961f" in same_key_text
    assert "409" in conflicting_key_text
    assert "server_verified" in conflicting_key_text
    assert "failed_reconciled" in conflicting_key_text
    assert all(case["messages"][-1]["role"] == "user" for case in confirmation_negatives)
    assert all("POST" in case["expected_output"] for case in confirmation_negatives)
    assert "version=2" in capability_negatives[0]["messages"][-1]["content"]
    assert "retro_batch_operation_key_v1=false" in capability_negatives[1]["messages"][-1]["content"]
    assert all("0 POST" in case["expected_output"] for case in capability_negatives)
    assert "\u5269\u4f59 2\u20138 \u5468" in multiweek_recovery_text
    assert "\u65b0\u7684\u5b8c\u6574 W35\u2013W38 \u5217\u8868\u786e\u8ba4" in multiweek_recovery_text
    assert "\u4e0d\u91cd\u653e W34" in multiweek_recovery_text
    assert "SUBMIT_TOO_FREQUENT" in rate_limit_text
    assert "\u4e0d\u7528\u65e7\u952e\u81ea\u52a8\u91cd\u8bd5" in rate_limit_text
    assert "status=failed" in queue_failure_text
    assert "WORKTIME_PROJECT_NOT_ALLOWED" in queue_failure_text
    assert "\u4e0d\u900f\u4f20\u539f\u59cb\u5f02\u5e38\u6216\u5806\u6808" in queue_failure_text
