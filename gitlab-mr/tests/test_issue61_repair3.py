"""Issue61 round-2 re-review findings R1-R3: self-contradictory terminal receipts, malformed
``last_snapshot`` on the snapshot writer, and Issue URL sub-paths in retirement detection.

All GitLab reads go to the GET-only fake glab or an in-memory reader; nothing writes to GitLab.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import gitlab_mr_issue_sync_fixture as fx  # noqa: E402
from test_issue61_repair2 import active_history, audit_state, receipt, scope  # noqa: E402
from test_issue61_review_hardening import (  # noqa: E402
    BINDING,
    RECEIPT,
    STATE_CLI,
    binding_args,
    fake_glab,
    history,
    lib_audit,
    receipt_args,
    run,
    state_file,
)

INPUT_ERROR = {"result": "INPUT_ERROR", "error": "state_snapshot_malformed"}


# --- R2: the snapshot writer never repairs a present non-object last_snapshot ----------------------------


def raw_state(tmp_path: Path, **fields) -> Path:
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"mr_iid": fx.MR_IID, "project_path": fx.REPO, **fields, "history": []}),
                    encoding="utf-8")
    return path


def snapshot_args(path: Path) -> list[str]:
    return ["snapshot", *scope(path), "--head-sha", fx.HEAD, "--pipeline-id", "7", "--unresolved-discussions", "0"]


@pytest.mark.parametrize("value", [["x"], [], "head", "", 5, 0, True, False])
def test_r2_snapshot_rejects_a_present_non_object_last_snapshot(tmp_path: Path, value) -> None:
    import drive_state_io as io

    path = raw_state(tmp_path, last_snapshot=value)
    before = path.read_bytes()
    result = run(STATE_CLI, *snapshot_args(path))
    assert result.returncode == 2 and "Traceback" not in result.stderr, result.stderr
    assert json.loads(result.stderr) == INPUT_ERROR and result.stdout == ""
    assert path.read_bytes() == before
    with io.locked(path, timeout=0.5):  # the failed writer released the shared lock
        pass


@pytest.mark.parametrize("fields", [{}, {"last_snapshot": None}], ids=["missing", "null"])
def test_r2_missing_or_null_last_snapshot_is_initialized_by_the_first_snapshot(tmp_path: Path, fields) -> None:
    path = raw_state(tmp_path, **fields)
    first = run(STATE_CLI, *snapshot_args(path))
    assert first.returncode == 0, first.stderr
    assert json.loads(first.stdout)["no_change_streak"] == 0
    state = json.loads(path.read_text(encoding="utf-8"))
    assert {k: state["last_snapshot"][k] for k in ("head_sha", "pipeline_id", "unresolved_discussions")} == {
        "head_sha": fx.HEAD, "pipeline_id": 7, "unresolved_discussions": 0}
    assert state["last_snapshot"]["checked_at"] and state["history"] == []
    second = run(STATE_CLI, *snapshot_args(path))
    assert second.returncode == 0 and json.loads(second.stdout)["no_change_streak"] == 1


@pytest.mark.parametrize("fields", [{}, {"last_snapshot": None}, {"last_snapshot": ["x"]}],
                         ids=["missing", "null", "list"])
def test_r2_head_bound_writers_still_require_a_matching_snapshot(tmp_path: Path, fields) -> None:
    path = raw_state(tmp_path, **fields)
    before = path.read_bytes()
    env = fake_glab(tmp_path, fx.in_review())
    for script, args in ((BINDING, binding_args("admit", path, fx.HEAD, fx.ISSUE_URL, "--binding-role", "new-work")),
                         (RECEIPT, receipt_args(path)),
                         (STATE_CLI, ["complete", *scope(path), "--head-sha", fx.HEAD])):
        result = run(script, *args, env=env)
        assert result.returncode == 2 and "Traceback" not in result.stderr, (script.name, result.stderr)
        assert json.loads(result.stderr) == {"result": "INPUT_ERROR", "error": "state_binding_mismatch"}
        assert path.read_bytes() == before


def test_r2_non_head_writers_ignore_and_preserve_a_malformed_last_snapshot(tmp_path: Path) -> None:
    path = raw_state(tmp_path, last_snapshot="head")
    payload = {"phase": "merged", "issue_url": fx.ISSUE_URL, "revision": "c" * 40, "note_id": 900001,
               "disposition": "kept-open", "observed_state": "opened"}
    for args in (["event", *scope(path), "--json", json.dumps({"round": 1})],
                 ["postmerge", *scope(path), "--json", json.dumps(payload)],
                 ["waiver", *scope(path), "--json", json.dumps({"head_sha": fx.HEAD})],
                 ["rejection", *scope(path), "--fingerprint", "fp"]):
        result = run(STATE_CLI, *args)
        assert result.returncode == 0, (args[0], result.stderr)
    assert json.loads(path.read_text(encoding="utf-8"))["last_snapshot"] == "head"


# --- R1: a terminal receipt that contradicts its own observed_state is a conflict -------------------------


def contradictory(kind: str) -> dict:
    if kind == "verified_observed_closed":  # only observed_state edited
        return {**fx.verified_receipt(), "observed_state": "closed"}
    if kind == "verified_observed_null":
        return {**fx.verified_receipt(), "observed_state": None}
    if kind == "verified_observed_list":
        return {**fx.verified_receipt(), "observed_state": ["closed"]}
    if kind == "verified_observed_removed":
        return {k: v for k, v in fx.verified_receipt().items() if k != "observed_state"}
    if kind == "failed_verdict_verified_observed_closed":  # result and observed_state edited, verdict kept
        return {**fx.verified_receipt(), "observed_state": "closed", "result": "ISSUE_SYNC_UNVERIFIED"}
    if kind == "result_and_state_edited":  # review_state.verdict still says VERIFIED
        return {**fx.verified_receipt(), "observed_state": "closed", "result": "NOT_APPLICABLE_CLOSED"}
    na = receipt(fx.closed(), fx.admission("closed-reference"))
    assert na["result"] == "NOT_APPLICABLE_CLOSED"
    return {**na, "observed_state": "opened", "binding_role": "new-work", "binding_role_source": "default"}


KINDS = ["verified_observed_closed", "verified_observed_null", "verified_observed_list", "verified_observed_removed",
         "failed_verdict_verified_observed_closed", "result_and_state_edited", "na_observed_opened"]


@pytest.mark.parametrize("kind", KINDS)
def test_r1_contradictory_receipt_blocks_the_closed_reference_producer(tmp_path: Path, kind: str) -> None:
    edited = contradictory(kind)
    produced = fx.admission("closed-reference", history=[edited])
    assert produced["result"] == "REJECTED" and "binding_role_conflict" in produced["errors"], produced["errors"]
    state = state_file(tmp_path, edited)
    cli = run(BINDING, *binding_args("admit", state, fx.HEAD, fx.ISSUE_URL, "--binding-role", "closed-reference"),
              env=fake_glab(tmp_path, fx.closed()))
    assert cli.returncode == 1 and "Traceback" not in cli.stderr, cli.stderr
    assert "binding_role_conflict" in json.loads(cli.stdout)["errors"]
    assert history(state) == [edited]


@pytest.mark.parametrize("kind", KINDS)
def test_r1_contradictory_receipt_never_audits_as_a_closed_reference(kind: str) -> None:
    admitted = fx.admission("closed-reference")  # a genuine producer record appended after the edit
    verdict = lib_audit(audit_state(contradictory(kind), admitted, receipt(fx.closed(), admitted)), fx.closed())
    assert verdict.startswith("FAIL ISSUE_SYNC_UNVERIFIED: binding_role_conflict"), verdict


def test_r1_legitimate_and_nonterminal_observations_are_preserved(tmp_path: Path) -> None:
    from issue_binding import issue_key, resolve

    key = issue_key(fx.ISSUE_URL, fx.HOST)
    admitted = fx.admission("closed-reference", snap=fx.in_review())
    opened, closed = receipt(fx.in_review(), admitted), receipt(fx.closed(), admitted)
    assert lib_audit(audit_state(admitted, opened, closed), fx.closed()) == "AUDIT PASS"
    # A failed observation of a closed Issue before any admission is not new-work evidence.
    unresolved = receipt(fx.closed())
    assert (unresolved["result"], unresolved["observed_state"]) == ("ISSUE_SYNC_UNVERIFIED", "closed")
    assert fx.admission("closed-reference", history=[unresolved])["result"] == "ADMITTED"
    # A failed observation of an opened Issue stays new-work evidence, not a conflict.
    failed = receipt(fx.snapshot(labels=[]))
    assert failed["result"] != "VERIFIED" and failed["observed_state"] == "opened"
    entry = resolve([failed], fx.MR_IID, fx.REPO, fx.HOST)[key]
    assert (entry["conflict"], entry["new_work_evidence"]) == (False, True)
    # A failed receipt whose Issue state was neither opened nor closed may still carry a terminal verdict.
    odd = {**failed, "observed_state": None, "review_state": {**failed["review_state"], "verdict": "VERIFIED"}}
    assert resolve([odd], fx.MR_IID, fx.REPO, fx.HOST)[key]["conflict"] is False
    # Legacy receipts without role fields (the pre-#61 producer shape) stay valid.
    legacy = {k: v for k, v in fx.verified_receipt().items() if k not in ("binding_role", "binding_role_source")}
    assert lib_audit(audit_state(legacy), fx.in_review()) == "AUDIT PASS"
    new_work = fx.admission("new-work")
    assert lib_audit(audit_state(new_work, receipt(fx.in_review(), new_work)), fx.in_review()) == "AUDIT PASS"


@pytest.mark.parametrize("field, value", [("result", ["VERIFIED"]), ("result", {"VERIFIED": 1}),
                                          ("review_state", {"verdict": ["VERIFIED"]}),
                                          ("review_state", ["VERIFIED"]), ("observed_state", ["closed"])])
def test_r1_unhashable_result_shapes_stay_structured(tmp_path: Path, field: str, value) -> None:
    edited = {**fx.verified_receipt(), field: value}
    verdict = lib_audit(audit_state(edited), fx.in_review())
    assert verdict.startswith("FAIL ISSUE_SYNC_UNVERIFIED"), verdict
    env = fake_glab(tmp_path, fx.closed())
    for script, args in ((BINDING, lambda p: binding_args("admit", p, fx.HEAD, fx.ISSUE_URL,
                                                          "--binding-role", "closed-reference")),
                         (RECEIPT, lambda p: receipt_args(p))):
        path = state_file(tmp_path, edited, name=f"{script.stem}.json")
        result = run(script, *args(path), env=env)
        assert result.returncode in (1, 2) and "Traceback" not in result.stderr, result.stderr


# --- R3: an Issue URL with a trailing sub-path is still a current mention ---------------------------------


URL = fx.ISSUE_URL
SUB_PATHS = {
    "designs_file": (f"See {URL}/designs/mock.png\n", "mr_binding_context_ambiguous"),
    "designs_dir": (f"Related: {URL}/designs/\n", "mr_binding_context_ambiguous"),
    "legacy_path_sub": (f"See {URL.replace('/-/issues/', '/issues/')}/designs/x.png\n",
                        "mr_binding_context_ambiguous"),
    "work_item_sub": (f"See {URL.replace('/issues/', '/work_items/')}/designs/x\n", "mr_binding_context_ambiguous"),
    "sub_with_query": (f"See {URL}/designs/mock.png?version=3#top\n", "mr_binding_context_ambiguous"),
    "closing_sub": (f"Closes {URL}/designs/mock.png\n", "binding_still_declared"),
    "issue_line_sub": (f"Issue: {URL}/designs/mock.png\n", "binding_still_declared"),
}


@pytest.mark.parametrize("name", sorted(SUB_PATHS))
def test_r3_retirement_rejects_issue_url_sub_paths(name: str) -> None:
    description, code = SUB_PATHS[name]
    snap = fx.in_review()
    snap["merge_request"]["description"] = description
    record = fx.retirement(snap=snap, history=active_history())
    assert record["result"] == "REJECTED" and record["errors"][0] == code, (name, record["errors"])


def test_r3_retire_cli_and_audit_recheck_detect_a_sub_path(tmp_path: Path) -> None:
    from issue_binding import issue_key, recheck_retirement, resolve

    hist = active_history()
    snap = fx.in_review()
    snap["merge_request"]["description"] = f"See {URL}/designs/mock.png\n"
    path = state_file(tmp_path, *hist)
    cli = run(BINDING, *binding_args("retire", path, fx.HEAD, URL, "--reason", "moved"),
              env=fake_glab(tmp_path, snap))
    assert cli.returncode == 1 and json.loads(cli.stdout)["errors"] == ["mr_binding_context_ambiguous"]
    assert history(path) == hist
    retired = fx.retirement(history=hist)
    entry = resolve([*hist, retired], fx.MR_IID, fx.REPO, fx.HOST)[issue_key(URL, fx.HOST)]
    assert recheck_retirement(entry, fx.reader(snap), fx.REPO, fx.MR_IID, fx.HOST) == [
        "mr_binding_context_ambiguous"]


@pytest.mark.parametrize("description", [
    f"See {URL}0/designs/mock.png\n",  # Issue 240
    f"See {URL.replace('gitlab.addx.ai', 'gitlab.example.com')}/designs/mock.png\n",
    f"See {URL.replace('WEB/micro_app_platform', 'WEB/other_app')}/designs/mock.png\n",
    f"See {URL.replace('/issues/24', '/merge_requests/24')}/diffs\n",
    "Issue: #240\nSee https://gitlab.addx.ai/WEB/micro_app_platform/-/issues/23/designs/x.png\n",
])
def test_r3_iid_host_and_project_boundaries_still_allow_retirement(description: str) -> None:
    snap = fx.in_review()
    snap["merge_request"]["description"] = description
    record = fx.retirement(snap=snap, history=active_history())
    assert record["result"] == "RETIRED" and record["mr_declares_issue"] is False, (description, record["errors"])


def test_r3_retire_then_readmit_still_works_with_a_sub_path_elsewhere() -> None:
    hist = active_history()
    retired = fx.retirement(history=hist)
    assert retired["result"] == "RETIRED"
    again = copy.deepcopy(fx.in_review())
    again["merge_request"]["description"] = f"Issue: {URL}/designs/mock.png\n"
    assert fx.admission("new-work", history=[*hist, retired], snap=again)["result"] == "ADMITTED"
