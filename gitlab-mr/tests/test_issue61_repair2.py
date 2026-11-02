"""Issue61 hardening re-review findings N1-N7: chronological role evidence, post-merge writer, malformed
action/state shapes, retirement reference detection, audit compatibility and docs.

All GitLab reads go to the GET-only fake glab or an in-memory reader; nothing writes to GitLab.
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import gitlab_mr_issue_sync_fixture as fx  # noqa: E402
from test_issue61_review_hardening import (  # noqa: E402
    BINDING,
    RECEIPT,
    STATE_CLI,
    Fail,
    binding_args,
    fake_glab,
    history,
    lib_audit,
    receipt_args,
    run,
    state_file,
)

MERGE_SHA = "c" * 40
OTHER_PROJECT = "WEB/other_app"


def audit_state(*items: dict) -> dict:
    return {"mr_iid": fx.MR_IID, "project_path": fx.REPO, "last_snapshot": {"head_sha": fx.HEAD},
            "history": list(items)}


def receipt(snap: dict, admission: dict | None = None) -> dict:
    from issue_review_state import verify

    return verify(fx.request(), reader=fx.reader(snap), host=fx.HOST,
                  binding=fx.binding(admission) if admission else None)


# --- N1: role evidence is resolved chronologically --------------------------------------------------------


def laundered_opened_receipt() -> dict:
    """A default receipt that observed the Issue opened, edited to claim a closed-reference admission."""
    laundered = copy.deepcopy(fx.verified_receipt())
    assert laundered["observed_state"] == "opened"
    laundered.update(binding_role="closed-reference", binding_role_source="admission")
    return laundered


def test_n1_receipt_self_claim_cannot_erase_new_work_evidence_for_the_producer(tmp_path: Path) -> None:
    laundered = laundered_opened_receipt()
    produced = fx.admission("closed-reference", history=[laundered])
    assert produced["result"] == "REJECTED" and "binding_role_conflict" in produced["errors"]
    state = state_file(tmp_path, laundered)
    cli = run(BINDING, *binding_args("admit", state, fx.HEAD, fx.ISSUE_URL, "--binding-role", "closed-reference"),
              env=fake_glab(tmp_path, fx.closed()))
    assert cli.returncode == 1 and "binding_role_conflict" in json.loads(cli.stdout)["errors"]
    assert history(state) == [laundered]


def test_n1_laundered_history_never_audits_as_a_closed_reference() -> None:
    laundered = laundered_opened_receipt()
    admitted = fx.admission("closed-reference")  # a genuine producer record appended after the edit
    na = receipt(fx.closed(), admitted)
    assert na["result"] == "NOT_APPLICABLE_CLOSED"
    verdict = lib_audit(audit_state(laundered, admitted, na), fx.closed())
    assert verdict.startswith("FAIL ISSUE_SYNC_UNVERIFIED") and "binding_role_conflict" in verdict


@pytest.mark.parametrize("make", [
    # a default opened receipt after a closed-reference admission (impossible through the producers)
    lambda adm: [adm, fx.verified_receipt(), receipt(fx.closed(), adm)],
    # a receipt claiming another role than the admission in force at its index
    lambda adm: [fx.admission("new-work"), {**fx.verified_receipt(), "binding_role": "closed-reference",
                                            "binding_role_source": "admission"}],
])
def test_n1_receipt_inconsistent_with_the_admission_in_force_is_a_conflict(make) -> None:
    from issue_binding import issue_key, resolve

    items = make(fx.admission("closed-reference"))
    entry = resolve(items, fx.MR_IID, fx.REPO, fx.HOST)[issue_key(fx.ISSUE_URL, fx.HOST)]
    assert entry["conflict"] is True
    assert "binding_role_conflict" in lib_audit(audit_state(*items), fx.closed())


def test_n1_declared_reference_admitted_open_then_closed_still_passes() -> None:
    admitted = fx.admission("closed-reference", snap=fx.in_review())
    opened = receipt(fx.in_review(), admitted)
    closed = receipt(fx.closed(), admitted)
    assert (opened["result"], closed["result"]) == ("VERIFIED", "NOT_APPLICABLE_CLOSED")
    assert lib_audit(audit_state(admitted, opened, closed), fx.closed()) == "AUDIT PASS"
    new_work = fx.admission("new-work")
    assert lib_audit(audit_state(new_work, receipt(fx.in_review(), new_work)), fx.in_review()) == "AUDIT PASS"
    # Legacy/default receipts before any admission stay valid new-work evidence.
    legacy = {k: v for k, v in fx.verified_receipt().items() if k not in ("binding_role", "binding_role_source")}
    assert lib_audit(audit_state(legacy), fx.in_review()) == "AUDIT PASS"


@pytest.mark.parametrize("forge", [
    lambda r: {**r, "binding": None},
    lambda r: {**r, "binding": {**r["binding"], "contract_version": "issue-lifecycle/v1"}},
    lambda r: {**r, "binding": {**r["binding"], "issue_url": fx.ISSUE_URL.replace("/24", "/23")}},
    lambda r: {**r, "binding": {**r["binding"], "issue_iid": 23}},
    lambda r: {**r, "binding": {**r["binding"], "state": None}},
    lambda r: {**r, "binding": {**r["binding"], "gitlab_host": "evil.example"}},
    lambda r: {**r, "native_relation_listed": False},
    lambda r: {**r, "mr_project_id": None},
    lambda r: {**r, "action": ["admit"]},
])
def test_n1_admission_without_a_consistent_lifecycle_identity_is_malformed(tmp_path: Path, forge) -> None:
    admitted = fx.admission("closed-reference")
    assert admitted["result"] == "ADMITTED"
    forged = forge(admitted)
    verdict = lib_audit(audit_state(forged, receipt(fx.closed(), admitted)), fx.closed())
    assert verdict.startswith("FAIL ISSUE_SYNC_UNVERIFIED: malformed"), verdict
    state = state_file(tmp_path, forged)
    cli = run(BINDING, *binding_args("admit", state, fx.HEAD, fx.ISSUE_URL, "--binding-role", "closed-reference"),
              env=fake_glab(tmp_path, fx.closed()))
    assert cli.returncode == 2 and "Traceback" not in cli.stderr
    assert json.loads(cli.stderr) == {"result": "INPUT_ERROR", "error": "malformed_binding_record"}


# --- N2: locked post-merge / final writer ---------------------------------------------------------------


def postmerge(phase: str, **changes) -> dict:
    record = {"phase": phase, "issue_url": fx.ISSUE_URL, "revision": MERGE_SHA, "note_id": 900001,
              "disposition": "kept-open", "observed_state": "opened"}
    record.update(changes)
    return {key: value for key, value in record.items() if value is not None}


def scope(path: Path) -> list[str]:
    return ["--state", str(path), "--mr-iid", str(fx.MR_IID), "--project-path", fx.REPO]


def test_n2_merged_and_final_records_have_a_locked_writer(tmp_path: Path) -> None:
    admitted = fx.admission("new-work")
    verified = receipt(fx.in_review(), admitted)
    # Old states may hold a hand-written merged entry under the previous docs; it stays append-only/ignored.
    legacy = {"kind": "issue_sync", "phase": "merged", "issue_url": fx.ISSUE_URL, "mr_iid": fx.MR_IID,
              "revision": MERGE_SHA, "result": "closed"}
    path = state_file(tmp_path, legacy, admitted, verified)
    merged = run(STATE_CLI, "postmerge", *scope(path), "--json", json.dumps(postmerge("merged")))
    assert merged.returncode == 0, merged.stderr
    final = run(STATE_CLI, "postmerge", *scope(path), "--json", json.dumps(postmerge(
        "final", revision="sha256:" + "d" * 64, disposition="closed", observed_state="closed",
        note_id=900002, note_url=f"{fx.ISSUE_URL}#note_900002", next_action="acceptance owner reviews rollout")))
    assert final.returncode == 0, final.stderr
    items = history(path)
    assert items[:3] == [legacy, admitted, verified]
    records = items[3:]
    assert [(r["kind"], r["phase"]) for r in records] == [("issue_sync_postmerge", "merged"),
                                                          ("issue_sync_postmerge", "final")]
    for record in records:
        assert record["schema"] == "gitlab-mr-issue-sync-postmerge/v1" and record["mr_iid"] == fx.MR_IID
        assert record["mr_project"] == fx.REPO and record["source"] == "parent-readback"
        assert record["native_verified_by_writer"] is False and record["recorded_at_utc"]
        assert "result" not in record and "verified" not in record
    assert path.stat().st_mode & 0o777 == 0o600
    # Post-merge records never count as, or replace, the submitted/revised review receipts.
    assert lib_audit(json.loads(path.read_text()), fx.in_review()) == "AUDIT PASS"


@pytest.mark.parametrize("payload, code", [
    (postmerge("submitted"), "invalid_postmerge_phase"),
    (postmerge("revised"), "invalid_postmerge_phase"),
    ({**postmerge("merged"), "kind": "issue_sync"}, "protected_history_kind"),
    ({**postmerge("merged"), "kind": "issue_binding"}, "protected_history_kind"),
    ({**postmerge("merged"), "result": "VERIFIED"}, "unexpected_postmerge_field"),
    ({**postmerge("merged"), "binding_role": "closed-reference"}, "unexpected_postmerge_field"),
    ({**postmerge("merged"), "schema": "gitlab-mr-issue-sync-receipt/v1"}, "unexpected_postmerge_field"),
    (postmerge("merged", issue_url="https://evil.example/WEB/micro_app_platform/-/issues/24"),
     "untrusted_issue_url"),
    (postmerge("merged", revision="abc"), "invalid_postmerge_revision"),
    (postmerge("merged", revision="sha256:" + "d" * 64), "invalid_postmerge_revision"),
    (postmerge("merged", note_id=0), "invalid_postmerge_note_id"),
    (postmerge("final", disposition="closed", observed_state="opened"), "postmerge_disposition_state_mismatch"),
    (postmerge("final", disposition="reopened"), "invalid_postmerge_disposition"),
    (postmerge("merged", note_url="https://evil.example/x"), "invalid_postmerge_note_url"),
    (["merged"], "postmerge_must_be_object"),
])
def test_n2_postmerge_writer_is_narrow(tmp_path: Path, payload, code: str) -> None:
    path = state_file(tmp_path)
    result = run(STATE_CLI, "postmerge", *scope(path), "--json", json.dumps(payload))
    assert result.returncode == 2 and "Traceback" not in result.stderr, result.stderr
    assert json.loads(result.stderr) == {"result": "INPUT_ERROR", "error": code}
    assert history(path) == []


def test_n2_generic_event_still_rejects_review_binding_and_postmerge_kinds(tmp_path: Path) -> None:
    path = state_file(tmp_path)
    for forged in ({"kind": "issue_sync", "phase": "merged"}, {"kind": "issue_sync_postmerge", "phase": "merged"},
                   fx.admission(), fx.verified_receipt()):
        result = run(STATE_CLI, "event", *scope(path), "--json", json.dumps(forged))
        assert result.returncode == 2 and "protected_history_kind" in result.stderr
    assert history(path) == []


def test_n2_concurrent_postmerge_and_event_writers_all_survive(tmp_path: Path) -> None:
    import drive_state_io as io

    path = state_file(tmp_path)
    commands = [[str(STATE_CLI), "postmerge", *scope(path), "--json",
                 json.dumps(postmerge("merged" if n % 2 else "final", note_id=900100 + n))] for n in range(6)]
    commands += [[str(STATE_CLI), "event", *scope(path), "--json", json.dumps({"round": n})] for n in range(6)]
    with io.locked(path):
        procs = [subprocess.Popen([sys.executable, *command], text=True, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE) for command in commands]
        time.sleep(1.0)
    results = [(proc.wait(timeout=30), proc.stderr.read()) for proc in procs]
    assert all(code == 0 for code, _ in results), results
    items = history(path)
    assert sorted(i["note_id"] for i in items if i.get("kind") == "issue_sync_postmerge") == list(
        range(900100, 900106))
    assert sorted(i["round"] for i in items if "round" in i) == list(range(6))


# --- N3: malformed action types ----------------------------------------------------------------------------


@pytest.mark.parametrize("action", [["admit"], {"x": 1}, 7, None])
def test_n3_unhashable_or_wrong_type_action_is_a_structured_error_in_both_producers(tmp_path: Path,
                                                                                    action) -> None:
    record = {**fx.admission("new-work"), "action": action}
    env = fake_glab(tmp_path, fx.in_review())
    for script, args in (
            (RECEIPT, lambda p: receipt_args(p)),
            (BINDING, lambda p: binding_args("admit", p, fx.HEAD, fx.ISSUE_URL, "--binding-role", "new-work"))):
        path = state_file(tmp_path, record, name=f"{script.stem}.json")
        result = run(script, *args(path), env=env)
        assert result.returncode == 2 and "Traceback" not in result.stderr, result.stderr
        assert json.loads(result.stderr) == {"result": "INPUT_ERROR", "error": "malformed_binding_record"}
        assert history(path) == [record]
    assert lib_audit(audit_state(record, fx.verified_receipt()), fx.in_review()).startswith(
        "FAIL ISSUE_SYNC_UNVERIFIED: malformed")


@pytest.mark.parametrize("action", [["admit"], {"admit": 1}])
def test_n3_library_produce_rejects_unhashable_actions(action) -> None:
    from issue_binding import produce

    with pytest.raises(ValueError, match="unsupported_binding_action"):
        produce(action, {"issue_url": fx.ISSUE_URL}, [], reader=fx.reader(fx.in_review()), host=fx.HOST)


# --- N4: non-dict last_snapshot ----------------------------------------------------------------------------


@pytest.mark.parametrize("snapshot", [["x"], "head", 5, None])
def test_n4_non_dict_last_snapshot_is_a_state_binding_error(tmp_path: Path, snapshot) -> None:
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"mr_iid": fx.MR_IID, "project_path": fx.REPO, "last_snapshot": snapshot,
                                "history": []}), encoding="utf-8")
    before = path.read_bytes()
    env = fake_glab(tmp_path, fx.in_review())
    commands = [(BINDING, binding_args("admit", path, fx.HEAD, fx.ISSUE_URL, "--binding-role", "new-work")),
                (RECEIPT, receipt_args(path)),
                (STATE_CLI, ["complete", *scope(path), "--head-sha", fx.HEAD])]
    for script, args in commands:
        result = run(script, *args, env=env)
        assert result.returncode == 2 and "Traceback" not in result.stderr, (script.name, result.stderr)
        assert json.loads(result.stderr) == {"result": "INPUT_ERROR", "error": "state_binding_mismatch"}
        assert path.read_bytes() == before


# --- N5: retirement fails closed on any current reference to the same Issue -------------------------------


URL = fx.ISSUE_URL
REFERENCES = {
    "closes_keyword": f"Closes {URL}\n",
    "fixes_short": "Fixes #24\n",
    "short_ref_hash": "Issue: #24\n",
    "short_ref_project": f"Issue: {fx.REPO}#24\n",
    "issues_plural": f"Issues: {URL}\n",
    "url_next_line": f"Issue:\n{URL}\n",
    "trailing_slash": f"Issue: {URL}/\n",
    "note_fragment": f"Issue: {URL}#note_1\n",
    "upper_host": "Issue: " + URL.replace("gitlab.addx.ai", "GitLab.ADDX.ai") + "\n",
    "markdown_table": f"| Issue | {URL} |\n",
    "related_prose": f"Related: {URL}\n",
    "mixed_case_project": f"Issue: {URL.replace('micro_app_platform', 'Micro_App_Platform')}\n",
    "http_scheme": f"Issue: {URL.replace('https://', 'http://')}\n",
    "legacy_path": f"Issue: {URL.replace('/-/issues/', '/issues/')}\n",
}


def active_history() -> list[dict]:
    admitted = fx.admission("new-work")
    return [admitted, receipt(fx.in_review(), admitted)]


@pytest.mark.parametrize("name", sorted(REFERENCES))
def test_n5_retirement_rejects_current_references_to_the_issue(name: str) -> None:
    snap = fx.in_review()
    snap["merge_request"]["description"] = REFERENCES[name]
    record = fx.retirement(snap=snap, history=active_history())
    assert record["result"] == "REJECTED", (name, record)
    assert record["errors"][0] in ("binding_still_declared", "mr_binding_context_ambiguous"), record["errors"]
    assert record["mr_declares_issue"] is not False


@pytest.mark.parametrize("description", [
    "",
    "Issue: https://gitlab.addx.ai/WEB/micro_app_platform/-/issues/23\n",
    "Issue: #240\nSee WEB/micro_app_platform#2400 and other/project#24\n",
    "Moved scope to https://gitlab.addx.ai/WEB/micro_app_platform/-/issues/245\n",
])
def test_n5_legitimate_retirement_still_succeeds(description: str) -> None:
    snap = fx.in_review()
    snap["merge_request"]["description"] = description
    record = fx.retirement(snap=snap, history=active_history())
    assert record["result"] == "RETIRED" and record["mr_declares_issue"] is False, record
    assert record["native_unlink"] is False


def test_n5_short_reference_is_resolved_against_the_mr_project() -> None:
    hist = [fx.admission("new-work", mr_project=OTHER_PROJECT)]
    for description, expected in (("Issue: #24\n", "RETIRED"),  # #24 of WEB/other_app, not the target
                                  (f"Issue: {fx.REPO}#24\n", "REJECTED"),
                                  ("Issue: micro_app_platform#24\n", "REJECTED")):  # same namespace
        snap = fx.in_review(mr_project=OTHER_PROJECT)
        snap["merge_request"]["description"] = description
        record = fx.retirement(snap=snap, history=hist, mr_project=OTHER_PROJECT)
        assert record["result"] == expected, (description, record)


def test_n5_null_description_stays_a_structured_gap() -> None:
    snap = fx.in_review()
    snap["merge_request"]["description"] = None
    record = fx.retirement(snap=snap, history=active_history())
    assert record["result"] == "REJECTED" and record["errors"] == ["mr_binding_context_unreadable"]


def test_n5_audit_recheck_fails_when_a_closing_reference_reappears() -> None:
    from issue_binding import issue_key, recheck_retirement, resolve

    hist = active_history()
    retired = fx.retirement(history=hist)
    assert retired["result"] == "RETIRED"
    entry = resolve([*hist, retired], fx.MR_IID, fx.REPO, fx.HOST)[issue_key(fx.ISSUE_URL, fx.HOST)]
    assert recheck_retirement(entry, fx.reader(fx.undeclared()), fx.REPO, fx.MR_IID, fx.HOST) == []
    again = fx.in_review()
    again["merge_request"]["description"] = "Closes #24\n"
    assert recheck_retirement(entry, fx.reader(again), fx.REPO, fx.MR_IID, fx.HOST) == ["binding_still_declared"]
    readmitted = fx.admission("new-work", history=[*hist, retired], snap=again)
    assert readmitted["result"] == "ADMITTED"


# --- N6: compatibility of the audit entry point and verifier callbacks -----------------------------------


def test_n6_old_import_path_forwards_lazily() -> None:
    program = ("import sys; sys.path.insert(0, sys.argv[1]); import issue_review_state as m; "
               "assert 'issue_sync_audit' not in sys.modules; f = m.audit_issue_sync; "
               "import inspect; print(callable(f))")
    result = subprocess.run([sys.executable, "-I", "-c", program, str(Path(fx.__file__).parents[1] / "lib")],
                            text=True, capture_output=True, check=False)
    assert result.returncode == 0 and result.stdout.strip() == "True", result.stderr
    from issue_review_state import audit_issue_sync, verify

    calls: list[dict] = []

    def old_verifier(request):  # the pre-hardening callback signature
        calls.append(request)
        return verify(request, reader=fx.reader(fx.in_review()), host=fx.HOST)
    fresh = audit_issue_sync(audit_state(fx.verified_receipt()), Fail, old_verifier)
    assert [item["result"] for item in fresh] == ["VERIFIED"] and len(calls) == 1


def test_n6_old_verifier_with_a_new_work_admission_is_supported() -> None:
    """Safe: the old default role is new-work, and the audit itself compares the stored Issue identity."""
    from issue_review_state import audit_issue_sync, verify

    admitted = fx.admission("new-work")
    calls: list[dict] = []

    def old_verifier(request):
        calls.append(request)
        return verify(request, reader=fx.reader(fx.in_review()), host=fx.HOST)
    fresh = audit_issue_sync(audit_state(admitted, receipt(fx.in_review(), admitted)), Fail, old_verifier)
    assert [item["result"] for item in fresh] == ["VERIFIED"] and len(calls) == 1


def test_n6_reference_context_is_never_silently_dropped_and_callback_runs_at_most_once() -> None:
    from issue_review_state import audit_issue_sync, verify

    admitted = fx.admission("closed-reference")
    calls: list[dict] = []

    def old_verifier(request):
        calls.append(request)
        return verify(request, reader=fx.reader(fx.closed()), host=fx.HOST)
    with pytest.raises(Fail, match="verifier_binding_unsupported") as caught:
        audit_issue_sync(audit_state(admitted, receipt(fx.closed(), admitted)), Fail, old_verifier,
                         reader=fx.reader(fx.closed()), host=fx.HOST)
    assert calls == [] and "malformed" not in str(caught.value)


def test_n6_verifier_programming_error_is_not_reported_as_malformed_history() -> None:
    from issue_sync_audit import audit_issue_sync

    calls = []

    def broken(request, binding=None):
        calls.append(request)
        raise TypeError("bug in injected verifier")
    with pytest.raises(Fail) as caught:
        audit_issue_sync(audit_state(fx.verified_receipt()), Fail, broken, host=fx.HOST)
    assert "malformed" not in str(caught.value) and "verifier_error (TypeError)" in str(caught.value)
    assert len(calls) == 1

    def not_a_dict(request, binding=None):
        return ["VERIFIED"]
    with pytest.raises(Fail, match="fresh readback is not an object"):
        audit_issue_sync(audit_state(fx.verified_receipt()), Fail, not_a_dict, host=fx.HOST)


# --- N7: writer ownership documented in the shared CLI -----------------------------------------------------


def test_n7_drive_state_docstring_names_each_producer() -> None:
    doc = STATE_CLI.read_text(encoding="utf-8").split('"""')[1]
    assert "verify_issue_sync_receipt.py" in doc and "record_issue_binding.py" in doc
    assert "postmerge" in doc and "issue_sync_postmerge" in doc
