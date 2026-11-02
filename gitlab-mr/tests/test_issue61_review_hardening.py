"""Issue61 independent-review findings F1/F3/F4/F5: binding role, retirement, payload shapes, shared state I/O.

All GitLab reads go to the GET-only fake glab or an in-memory reader; nothing writes to GitLab.
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "lib"))

import gitlab_mr_issue_sync_fixture as fx  # noqa: E402

SCRIPTS = Path(__file__).parents[1] / "scripts"
RECEIPT = SCRIPTS / "verify_issue_sync_receipt.py"
BINDING = SCRIPTS / "record_issue_binding.py"
STATE_CLI = SCRIPTS / "drive_state.py"
AUDIT = SCRIPTS / "validate_drive_audit.py"
HEAD2 = "b" * 40
OTHER_IID, OTHER_ID, OTHER_NOTE = 99, 23099, 990001
OTHER_URL = f"https://{fx.HOST}/{fx.REPO}/-/issues/{OTHER_IID}"


class Fail(ValueError):
    pass


# --- helpers --------------------------------------------------------------------------------------------


def state_file(tmp_path: Path, *history: dict, head: str = fx.HEAD, name: str = "state.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps({"mr_iid": fx.MR_IID, "project_path": fx.REPO, "last_snapshot": {"head_sha": head},
                                "history": list(history)}), encoding="utf-8")
    return path


def history(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["history"]


def other_issue(snap: dict, head: str = fx.HEAD) -> dict:
    """A second Issue (99) natively related to the same MR, as live GETs return it."""
    snap = copy.deepcopy(snap)
    snap["issue"].update(iid=OTHER_IID, id=OTHER_ID, web_url=OTHER_URL)
    marker = f"<!-- gitlab-mr-issue-sync:v1:submitted:{fx.PROJECT_ID}:{OTHER_IID}:{fx.MR_IID}:{head} -->"
    snap["note"].update(id=OTHER_NOTE, noteable_iid=OTHER_IID,
                        body=f"{marker}\nMR submitted: {fx.mr_url()}\nHEAD: {head}\nNext gate: review\n")
    return snap


def table(primary: dict, second: dict | None = None) -> dict:
    """GET table for Issue 24 (+ optional Issue 99); the MR response comes from ``primary``."""
    result = fx.endpoints(primary)
    if second is not None:
        path = f"projects/{quote(fx.REPO, safe='')}/issues/{OTHER_IID}"
        result.update({path: second["issue"], f"{path}/notes/{OTHER_NOTE}": second["note"],
                       f"{path}/related_merge_requests?per_page=100": second["related_merge_requests"]})
    return result


def fake_glab(tmp_path: Path, primary: dict, second: dict | None = None, name: str = "bin") -> dict:
    env = fx.install_fake_glab(tmp_path / name, primary)
    Path(env["FAKE_GLAB_FIXTURE"]).write_text(json.dumps(table(primary, second)), encoding="utf-8")
    return env


def run(script: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(script), *args], text=True, capture_output=True, check=False,
                          env=env if env is not None else dict(os.environ))


def receipt_args(state: Path, head: str = fx.HEAD, url: str = fx.ISSUE_URL, note: int = fx.NOTE_ID) -> list[str]:
    return ["--issue", url, "--mr-project", fx.REPO, "--mr-iid", str(fx.MR_IID), "--revision", head,
            "--phase", "submitted", "--note-id", str(note), "--state", str(state)]


def binding_args(action: str, state: Path, head: str = fx.HEAD, url: str = fx.ISSUE_URL, *extra: str) -> list[str]:
    return [action, "--state", str(state), "--issue", url, "--mr-project", fx.REPO, "--mr-iid", str(fx.MR_IID),
            "--revision", head, *extra]


def move_head(path: Path, head: str) -> None:
    """The Supervisor records a new MR HEAD through the shared writer."""
    result = run(STATE_CLI, "snapshot", "--state", str(path), "--mr-iid", str(fx.MR_IID), "--project-path", fx.REPO,
                 "--head-sha", head, "--pipeline-id", "7", "--unresolved-discussions", "0")
    assert result.returncode == 0, result.stderr


def lib_audit(state: dict, primary: dict, second: dict | None = None) -> str:
    from issue_review_state import ReadFailed, verify
    from issue_sync_audit import audit_issue_sync

    endpoints = table(primary, second)

    def reader(endpoint: str):
        if endpoint not in endpoints:
            raise ReadFailed("read_failed")
        return copy.deepcopy(endpoints[endpoint])
    try:
        audit_issue_sync(state, Fail, lambda request, binding=None: verify(
            request, reader=reader, host=fx.HOST, binding=binding), reader=reader, host=fx.HOST)
        return "AUDIT PASS"
    except Fail as exc:
        return f"FAIL {exc}"


def file_audit(path: Path, primary: dict, second: dict | None = None) -> str:
    return lib_audit(json.loads(path.read_text(encoding="utf-8")), primary, second)


# --- F1: binding role -----------------------------------------------------------------------------------


def test_r1_new_work_issue_closed_during_review_is_never_a_passing_closed_reference(tmp_path: Path) -> None:
    """Reviewer R1: VERIFIED at HEAD1, Issue closed, new push, documented re-run -> must stay unverified."""
    state = state_file(tmp_path)
    first = run(RECEIPT, *receipt_args(state), env=fake_glab(tmp_path, fx.in_review(), name="h1"))
    assert first.returncode == 0 and json.loads(first.stdout)["result"] == "VERIFIED"
    move_head(state, HEAD2)
    refreshed = run(RECEIPT, *receipt_args(state, HEAD2), env=fake_glab(tmp_path, fx.closed(HEAD2), name="h2"))
    receipt = json.loads(refreshed.stdout)
    assert refreshed.returncode == 1 and receipt["result"] == "ISSUE_SYNC_UNVERIFIED"
    assert receipt["verified"] is False and receipt["gap"]["next_action"]
    assert "FAIL ISSUE_SYNC_UNVERIFIED" in file_audit(state, fx.closed(HEAD2))


def test_admitted_new_work_closed_mid_review_names_the_actionable_reason(tmp_path: Path) -> None:
    state = state_file(tmp_path)
    env = fake_glab(tmp_path, fx.in_review())
    assert run(BINDING, *binding_args("admit", state, fx.HEAD, fx.ISSUE_URL, "--binding-role", "new-work"),
               env=env).returncode == 0
    assert run(RECEIPT, *receipt_args(state), env=env).returncode == 0
    closed = run(RECEIPT, *receipt_args(state), env=fake_glab(tmp_path, fx.closed(), name="closed"))
    receipt = json.loads(closed.stdout)
    assert closed.returncode == 1 and receipt["errors"] == ["issue_closed_during_review"]
    assert (receipt["binding_role"], receipt["binding_role_source"]) == ("new-work", "admission")
    assert "do not reopen" in receipt["gap"]["next_action"]
    verdict = file_audit(state, fx.closed())
    assert "issue_closed_during_review" in verdict
    retag = run(BINDING, *binding_args("admit", state, fx.HEAD, fx.ISSUE_URL, "--binding-role", "closed-reference"),
                env=fake_glab(tmp_path, fx.closed(), name="retag"))
    assert retag.returncode == 1 and json.loads(retag.stdout)["errors"] == ["binding_role_conflict"]
    assert [item.get("result") for item in history(state)] == ["ADMITTED", "VERIFIED", "ISSUE_SYNC_UNVERIFIED"]


def test_declared_reference_may_be_open_at_admission_and_close_later(tmp_path: Path) -> None:
    """Do not reject historical opened receipts indiscriminately: the declared role decides."""
    state = state_file(tmp_path)
    opened = fake_glab(tmp_path, fx.in_review(), name="opened")
    admitted = run(BINDING, *binding_args("admit", state, fx.HEAD, fx.ISSUE_URL, "--binding-role",
                                          "closed-reference"), env=opened)
    assert admitted.returncode == 0 and json.loads(admitted.stdout)["binding"]["state"] == "opened"
    assert json.loads(run(RECEIPT, *receipt_args(state), env=opened).stdout)["result"] == "VERIFIED"
    move_head(state, HEAD2)
    closed_env = fake_glab(tmp_path, fx.closed(HEAD2), name="closed")
    later = run(RECEIPT, *receipt_args(state, HEAD2), env=closed_env)
    receipt = json.loads(later.stdout)
    assert later.returncode == 0 and receipt["result"] == "NOT_APPLICABLE_CLOSED" and receipt["verified"] is False
    calls = Path(closed_env["FAKE_GLAB_FIXTURE"] + ".calls").read_text().split()
    assert not any("/labels?" in call for call in calls)
    assert file_audit(state, fx.closed(HEAD2)) == "AUDIT PASS"


def test_closed_reference_admission_cannot_relabel_established_new_work() -> None:
    from issue_binding import produce

    opened_receipt = fx.verified_receipt()  # default-role receipt that observed the Issue opened
    request = {"issue_url": fx.ISSUE_URL, "mr_project": fx.REPO, "mr_iid": fx.MR_IID, "revision": fx.HEAD,
               "binding_role": "closed-reference"}
    rejected = produce("admit", request, [opened_receipt], reader=fx.reader(fx.closed()), host=fx.HOST)
    assert rejected["result"] == "REJECTED" and rejected["errors"] == ["binding_role_conflict"]
    new_work = fx.admission("new-work")
    assert produce("admit", request, [new_work], reader=fx.reader(fx.closed()),
                   host=fx.HOST)["errors"] == ["binding_role_conflict"]
    # A hand-appended reference admission after new-work evidence is caught by the audit, too.
    forged = {**fx.admission("closed-reference")}
    receipt = fx.verified_receipt()
    assert "binding_role_conflict" in lib_audit(
        {"mr_iid": fx.MR_IID, "project_path": fx.REPO, "last_snapshot": {"head_sha": fx.HEAD},
         "history": [new_work, receipt, forged]}, fx.in_review())


def test_self_asserted_receipt_role_is_not_role_proof() -> None:
    from issue_review_state import verify

    forged = verify(fx.request(), reader=fx.reader(fx.closed()), host=fx.HOST,
                    binding=fx.binding(fx.admission("closed-reference")))
    assert forged["result"] == "NOT_APPLICABLE_CLOSED"
    state = {"mr_iid": fx.MR_IID, "project_path": fx.REPO, "last_snapshot": {"head_sha": fx.HEAD},
             "history": [forged]}  # the receipt claims an admission, but history holds none
    # Issue61 N1: an unbacked admission claim is now a loud history conflict (it used to reach the role check).
    assert "FAIL ISSUE_SYNC_UNVERIFIED: binding_role_conflict" in lib_audit(state, fx.closed())


@pytest.mark.parametrize("mutate, role, code", [
    (lambda s: s["issue"].update(state="closed"), "new-work", "issue_not_opened"),
    (lambda s: s["issue"].update(assignees=[]), "new-work", "issue_unassigned"),
    (lambda s: s["issue"].update(assignees=[]), "closed-reference", "issue_unassigned"),
    (lambda s: s["issue"].update(state="locked"), "closed-reference", "issue_not_opened"),
    (lambda s: s.update(related_merge_requests=[]), "closed-reference", "mr_not_related"),
    (lambda s: s["merge_request"].update(sha="e" * 40), "new-work", "mr_head_mismatch"),
    (lambda s: s["merge_request"].update(state="merged"), "closed-reference", "mr_not_open"),
    (lambda s: s["issue"].update(web_url=OTHER_URL), "new-work", "issue_identity_mismatch"),
])
def test_admission_requires_live_identity_relationship_and_role_state(mutate, role: str, code: str) -> None:
    snap = fx.in_review()
    mutate(snap)
    record = fx.admission(role, snap=snap)
    assert record["result"] == "REJECTED" and code in record["errors"]


def test_unknown_or_missing_role_is_never_a_reference(tmp_path: Path) -> None:
    from issue_binding import produce

    with pytest.raises(ValueError, match="unknown_binding_role"):
        produce("admit", {"issue_url": fx.ISSUE_URL, "mr_project": fx.REPO, "mr_iid": fx.MR_IID,
                          "revision": fx.HEAD, "binding_role": "reference"}, [], reader=fx.reader(fx.closed()),
                host=fx.HOST)
    state = state_file(tmp_path)
    cli = run(BINDING, *binding_args("admit", state, fx.HEAD, fx.ISSUE_URL, "--binding-role", "skip"))
    assert cli.returncode == 2 and history(state) == []
    unresolved = run(RECEIPT, *receipt_args(state), env=fake_glab(tmp_path, fx.closed()))
    assert unresolved.returncode == 1 and json.loads(unresolved.stdout)["errors"] == ["binding_role_unresolved"]


def test_closed_reference_native_status_stays_blocked() -> None:
    from issue_review_state import verify

    blocked = verify(fx.request(project_mode="native-status"), reader=fx.reader(fx.closed()), host=fx.HOST,
                     binding=fx.binding(fx.admission()))
    assert blocked["result"] == "BLOCKED" and blocked["errors"] == ["unsupported_project_mode"]


# --- F4: authoritative active set and controlled retirement --------------------------------------------


def two_bound_issues(tmp_path: Path) -> tuple[Path, dict]:
    """Issues 24 and 99 admitted as new-work and VERIFIED at HEAD1 through the real producers."""
    state = state_file(tmp_path)
    env = fake_glab(tmp_path, fx.in_review(), other_issue(fx.in_review()), name="h1")
    for url, note in ((fx.ISSUE_URL, fx.NOTE_ID), (OTHER_URL, OTHER_NOTE)):
        admitted = run(BINDING, *binding_args("admit", state, fx.HEAD, url, "--binding-role", "new-work"), env=env)
        assert admitted.returncode == 0, admitted.stdout + admitted.stderr
        produced = run(RECEIPT, *receipt_args(state, fx.HEAD, url, note), env=env)
        assert produced.returncode == 0, produced.stdout + produced.stderr
    return state, env


def test_r7_valid_binding_is_retired_by_a_preserved_record_and_stops_blocking(tmp_path: Path) -> None:
    state, _ = two_bound_issues(tmp_path)
    assert file_audit(state, fx.in_review(), other_issue(fx.in_review())) == "AUDIT PASS"
    move_head(state, HEAD2)
    head2 = fx.in_review(HEAD2)  # MR description still declares only Issue 24
    second = other_issue(fx.in_review(HEAD2), HEAD2)
    env = fake_glab(tmp_path, head2, second, name="h2")
    assert run(RECEIPT, *receipt_args(state, HEAD2), env=env).returncode == 0
    blocked = file_audit(state, head2, second)  # dropping Issue 99 from the MR does not retire it
    assert "receipt not refreshed for current HEAD" in blocked and OTHER_URL in blocked
    retired = run(BINDING, *binding_args("retire", state, HEAD2, OTHER_URL, "--reason",
                                         "scope moved to a follow-up MR"), env=env)
    record = json.loads(retired.stdout)
    assert retired.returncode == 0 and record["result"] == "RETIRED", record
    assert record["native_relation_listed"] is True and record["native_unlink"] is False
    assert record["mr_declares_issue"] is False and record["issue_id"] == OTHER_ID
    assert file_audit(state, head2, second) == "AUDIT PASS"
    kinds = [(item.get("kind"), item.get("result")) for item in history(state)]
    assert kinds.count(("issue_sync", "VERIFIED")) == 3  # nothing deleted: append-only history
    refused = run(RECEIPT, *receipt_args(state, HEAD2, OTHER_URL, OTHER_NOTE), env=env)
    assert refused.returncode == 2 and "binding_retired" in refused.stderr


def test_retirement_of_the_still_declared_canonical_binding_is_rejected(tmp_path: Path) -> None:
    state, env = two_bound_issues(tmp_path)
    before = history(state)
    still = run(BINDING, *binding_args("retire", state, fx.HEAD, fx.ISSUE_URL, "--reason", "drop it"), env=env)
    assert still.returncode == 1 and json.loads(still.stdout)["errors"] == ["binding_still_declared"]
    assert history(state) == before


@pytest.mark.parametrize("description", ["**Issue:** " + fx.ISSUE_URL, "Work Item: " + fx.ISSUE_URL + ".",
                                         "- Root Issue: <" + fx.ISSUE_URL.replace("/issues/", "/work_items/") + ">"])
def test_declaration_forms_keep_the_binding_current(description: str) -> None:
    from issue_binding import declares_issue, issue_key

    assert declares_issue(description, issue_key(fx.ISSUE_URL, fx.HOST), fx.HOST) is True
    assert declares_issue("Related: " + fx.ISSUE_URL, issue_key(fx.ISSUE_URL, fx.HOST), fx.HOST) is False
    assert declares_issue(None, issue_key(fx.ISSUE_URL, fx.HOST), fx.HOST) is None


def test_retirement_rejects_unknown_forged_or_stale_evidence(tmp_path: Path) -> None:
    from issue_binding import produce

    request = {"issue_url": fx.ISSUE_URL, "mr_project": fx.REPO, "mr_iid": fx.MR_IID, "revision": fx.HEAD,
               "reason": "dropped"}
    assert produce("retire", request, [], reader=fx.reader(fx.undeclared()),
                   host=fx.HOST)["errors"] == ["binding_not_active"]
    admitted = [fx.admission("new-work")]
    swapped = fx.undeclared()
    swapped["issue"]["id"] = 1  # same URL, different native Issue identity
    assert "binding_identity_mismatch" in produce("retire", request, admitted, reader=fx.reader(swapped),
                                                  host=fx.HOST)["errors"]
    moved = fx.undeclared(head=HEAD2)
    assert "mr_head_mismatch" in produce("retire", request, admitted, reader=fx.reader(moved), host=fx.HOST)["errors"]
    with pytest.raises(ValueError, match="retirement_reason_required"):
        produce("retire", {**request, "reason": "  "}, admitted, reader=fx.reader(fx.undeclared()), host=fx.HOST)
    state = state_file(tmp_path, *admitted)
    cli = run(BINDING, *binding_args("retire", state, fx.HEAD, fx.ISSUE_URL, "--reason", ""),
              env=fake_glab(tmp_path, fx.undeclared()))
    assert cli.returncode == 2 and "retirement_reason_required" in cli.stderr and history(state) == admitted


@pytest.mark.parametrize("forge", [
    lambda r: {**r, "reason": ""},
    lambda r: {**r, "errors": ["binding_still_declared"]},
    lambda r: {**r, "native_unlink": True},
    lambda r: {**r, "schema": "self-asserted"},
    lambda r: {**r, "result": "ADMITTED"},
    lambda r: {**r, "issue_url": ["not", "a", "url"]},
])
def test_forged_or_malformed_retirement_records_fail_the_audit(forge) -> None:
    admitted, receipt = fx.admission("new-work"), fx.verified_receipt()
    retired = fx.retirement(history=[admitted, receipt])
    assert retired["result"] == "RETIRED", retired
    state = {"mr_iid": fx.MR_IID, "project_path": fx.REPO, "last_snapshot": {"head_sha": fx.HEAD},
             "history": [admitted, receipt, forge(retired)]}
    verdict = lib_audit(state, fx.undeclared())
    assert verdict.startswith("FAIL ISSUE_SYNC_UNVERIFIED") and "malformed" in verdict


def test_retired_binding_declared_again_fails_until_readmitted(tmp_path: Path) -> None:
    state, _ = two_bound_issues(tmp_path)
    env = fake_glab(tmp_path, fx.in_review(), other_issue(fx.in_review()), name="retire")
    assert run(BINDING, *binding_args("retire", state, fx.HEAD, OTHER_URL, "--reason", "follow-up"),
               env=env).returncode == 0
    redeclared = fx.in_review()
    redeclared["merge_request"]["description"] += f"Issue: {OTHER_URL}\n"
    verdict = file_audit(state, redeclared, other_issue(fx.in_review()))
    assert "retirement no longer holds (binding_still_declared)" in verdict
    env = fake_glab(tmp_path, redeclared, other_issue(fx.in_review()), name="readmit")
    assert run(BINDING, *binding_args("admit", state, fx.HEAD, OTHER_URL, "--binding-role", "new-work"),
               env=env).returncode == 0
    assert "receipt not refreshed" in file_audit(state, redeclared, other_issue(fx.in_review()))
    assert run(RECEIPT, *receipt_args(state, fx.HEAD, OTHER_URL, OTHER_NOTE), env=env).returncode == 0
    assert file_audit(state, redeclared, other_issue(fx.in_review())) == "AUDIT PASS"


def test_admitted_but_unreceipted_issue_is_not_silently_dropped(tmp_path: Path) -> None:
    state = state_file(tmp_path)
    env = fake_glab(tmp_path, fx.in_review(), other_issue(fx.in_review()))
    for url in (fx.ISSUE_URL, OTHER_URL):
        assert run(BINDING, *binding_args("admit", state, fx.HEAD, url, "--binding-role", "new-work"),
                   env=env).returncode == 0
    assert run(RECEIPT, *receipt_args(state), env=env).returncode == 0
    verdict = file_audit(state, fx.in_review(), other_issue(fx.in_review()))
    assert "receipt not refreshed for current HEAD" in verdict and OTHER_URL in verdict


def test_legacy_receipt_only_binding_can_be_retired_without_hand_editing(tmp_path: Path) -> None:
    """Migration: a pre-admission state (receipts only) retires an old Issue through the producer."""
    old_other = {**fx.verified_receipt(), "issue_url": OTHER_URL, "issue_id": OTHER_ID}
    for key in ("binding_role", "binding_role_source"):
        old_other.pop(key)
    legacy = {key: value for key, value in fx.verified_receipt(HEAD2).items()
              if key not in ("binding_role", "binding_role_source")}
    state = state_file(tmp_path, old_other, legacy, head=HEAD2)
    head2, second = fx.in_review(HEAD2), other_issue(fx.in_review(HEAD2), HEAD2)
    assert "not refreshed for current HEAD" in file_audit(state, head2, second)
    retired = run(BINDING, *binding_args("retire", state, HEAD2, OTHER_URL, "--reason", "binding dropped"),
                  env=fake_glab(tmp_path, head2, second))
    assert retired.returncode == 0, retired.stdout + retired.stderr
    assert file_audit(state, head2, second) == "AUDIT PASS"


# --- F3: malformed shapes give the structured contract ---------------------------------------------------


def audit_cli(tmp_path: Path, state: dict, snap: dict, *flags: str) -> subprocess.CompletedProcess[str]:
    """Only the Issue-sync part matters here; release/integrity checks are not what fails first."""
    from issue_sync_audit import audit_issue_sync  # noqa: F401  (import check)

    path = tmp_path / "audit-state.json"
    path.write_text(json.dumps(state), encoding="utf-8")
    program = ("import json, sys; sys.path.insert(0, sys.argv[1]); import validate_drive_audit as v; "
               "v.validate = lambda state, audit: v.audit_issue_sync(state, v.AuditError); "
               "raise SystemExit(v.main(sys.argv[2:]))")
    audit = tmp_path / "audit.json"
    audit.write_text("{}", encoding="utf-8")
    return subprocess.run([sys.executable, "-c", program, str(SCRIPTS), "--state", str(path), "--audit-result",
                           str(audit), *flags], text=True, capture_output=True, check=False,
                          env=fake_glab(tmp_path, snap, name="audit-bin"))


@pytest.mark.parametrize("bad", [["not", "a", "string"], 7, None, "http://evil.example/x/-/issues/1"])
def test_r4_malformed_history_issue_url_is_a_structured_audit_fail(tmp_path: Path, bad) -> None:
    state = {"mr_iid": fx.MR_IID, "project_path": fx.REPO, "last_snapshot": {"head_sha": fx.HEAD},
             "history": [{**fx.verified_receipt(), "issue_url": bad}]}
    assert lib_audit(state, fx.in_review()).startswith("FAIL ISSUE_SYNC_UNVERIFIED: malformed")
    result = audit_cli(tmp_path, state, fx.in_review(), "--json")
    assert result.returncode == 1 and "Traceback" not in result.stderr, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "fail" and "malformed" in payload["error"]
    plain = audit_cli(tmp_path, state, fx.in_review())
    assert plain.returncode == 1 and plain.stderr.startswith("AUDIT FAIL: ISSUE_SYNC_UNVERIFIED")


@pytest.mark.parametrize("history_value", [{"kind": "issue_sync"}, "history", [["nested"], 3, None]])
def test_malformed_history_shapes_fail_closed(history_value) -> None:
    state = {"mr_iid": fx.MR_IID, "project_path": fx.REPO, "last_snapshot": {"head_sha": fx.HEAD},
             "history": history_value}
    assert lib_audit(state, fx.in_review()).startswith("FAIL ISSUE_SYNC_UNVERIFIED")


@pytest.mark.parametrize("catalog", [7, "status::in-review", {"name": "status::in-review"}, None])
def test_r5_malformed_label_catalog_is_a_structured_receipt(tmp_path: Path, catalog) -> None:
    from issue_review_state import verify

    snap = fx.in_review()
    snap["project_labels"] = catalog
    receipt = verify(fx.request(), reader=fx.reader(snap), host=fx.HOST)
    assert receipt["result"] == "ISSUE_SYNC_UNVERIFIED" and receipt["errors"] == ["malformed_response"]
    result = run(RECEIPT, *receipt_args(state_file(tmp_path))[:-2], env=fake_glab(tmp_path, snap))
    assert result.returncode == 1 and "Traceback" not in result.stderr
    assert json.loads(result.stdout)["gap"]["reason"] == "malformed_response"


@pytest.mark.parametrize("record", [
    {"kind": "issue_binding"},
    {"kind": "issue_binding", "issue_url": fx.ISSUE_URL, "schema": "gitlab-mr-issue-binding/v1"},
    "admission",
])
def test_malformed_admission_records_fail_closed_in_producer_and_audit(tmp_path: Path, record) -> None:
    state = {"mr_iid": fx.MR_IID, "project_path": fx.REPO, "last_snapshot": {"head_sha": fx.HEAD},
             "history": [record, fx.verified_receipt()]}
    verdict = lib_audit(state, fx.in_review())
    if isinstance(record, dict):
        assert verdict.startswith("FAIL ISSUE_SYNC_UNVERIFIED: malformed"), verdict
        path = state_file(tmp_path, record)
        result = run(RECEIPT, *receipt_args(path), env=fake_glab(tmp_path, fx.in_review()))
        assert result.returncode == 2 and "Traceback" not in result.stderr
        assert json.loads(result.stderr)["result"] == "INPUT_ERROR"
    else:  # non-object entries are not issue-sync records and cannot drop the receipted Issue
        assert verdict == "AUDIT PASS"


def test_offline_snapshot_shape_is_validated_before_indexing() -> None:
    from issue_review_state import verify

    with pytest.raises(ValueError, match="snapshot_must_be_object"):
        verify(fx.request(), snapshot=[fx.in_review()], host=fx.HOST)


# --- F5: shared state I/O ---------------------------------------------------------------------------------


def test_r6_writer_waiting_on_the_lock_rereads_and_keeps_a_concurrent_entry(tmp_path: Path) -> None:
    """Deterministic interleaving: another writer changes the file while the producer waits for the lock."""
    import drive_state_io as io

    path = state_file(tmp_path)
    env = fake_glab(tmp_path, fx.in_review())
    with io.locked(path):
        producer = subprocess.Popen([sys.executable, str(RECEIPT), *receipt_args(path)], env=env, text=True,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        deadline = time.monotonic() + 8
        while not Path(env["FAKE_GLAB_FIXTURE"] + ".calls").exists() or len(
                Path(env["FAKE_GLAB_FIXTURE"] + ".calls").read_text().split()) < 6:
            assert time.monotonic() < deadline and producer.poll() is None
            time.sleep(0.02)
        time.sleep(0.2)  # the producer has finished its GETs and now waits on the lock
        state = json.loads(path.read_text())
        state["history"].append({"round": 9, "status": "driver-return"})
        state["last_snapshot"]["pipeline_id"] = 77
        path.write_text(json.dumps(state), encoding="utf-8")  # the other writer, inside the same lock
    out, err = producer.communicate(timeout=20)
    assert producer.returncode == 0, out + err
    final = json.loads(path.read_text())
    assert [item.get("round") or item.get("result") for item in final["history"]] == [9, "VERIFIED"]
    assert final["last_snapshot"]["pipeline_id"] == 77
    assert path.stat().st_mode & 0o777 == 0o600


def test_concurrent_legitimate_writers_all_survive(tmp_path: Path) -> None:
    """Real multiprocess collision across the producer, binding, event, snapshot, waiver and rejection CLIs."""
    import drive_state_io as io

    path = state_file(tmp_path)
    data = json.loads(path.read_text())
    data.update(no_change_streak=0, merge_integrity_waivers=[], merge_integrity_rejections={"fingerprint": "",
                                                                                             "count": 0})
    path.write_text(json.dumps(data), encoding="utf-8")
    env = fake_glab(tmp_path, fx.in_review(), other_issue(fx.in_review()))
    scope = ["--state", str(path), "--mr-iid", str(fx.MR_IID), "--project-path", fx.REPO]
    commands = [[str(STATE_CLI), "event", *scope, "--json", json.dumps({"round": n, "status": "done"})]
                for n in range(8)]
    commands += [[str(STATE_CLI), "snapshot", *scope, "--head-sha", fx.HEAD, "--pipeline-id", "5",
                  "--unresolved-discussions", "1"],
                 [str(STATE_CLI), "waiver", *scope, "--json", json.dumps({"head_sha": fx.HEAD, "checks": ["A"]})],
                 [str(STATE_CLI), "rejection", *scope, "--fingerprint", "h:t:A"],
                 [str(BINDING), *binding_args("admit", path, fx.HEAD, OTHER_URL, "--binding-role", "new-work")],
                 [str(RECEIPT), *receipt_args(path)]]
    with io.locked(path):  # every writer starts and then contends for the same lock
        procs = [subprocess.Popen([sys.executable, *command], env=env, text=True, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE) for command in commands]
        time.sleep(1.0)
    results = [(proc.wait(timeout=30), proc.stderr.read()) for proc in procs]
    assert all(code == 0 for code, _ in results), results
    final = json.loads(path.read_text())
    assert sorted(item["round"] for item in final["history"] if "round" in item) == list(range(8))
    assert {item.get("kind") for item in final["history"]} >= {"issue_sync", "issue_binding"}
    assert final["last_snapshot"]["pipeline_id"] == 5 and final["last_snapshot"]["unresolved_discussions"] == 1
    assert final["merge_integrity_waivers"] == [{"head_sha": fx.HEAD, "checks": ["A"]}]
    assert final["merge_integrity_rejections"] == {"fingerprint": "h:t:A", "count": 1}
    assert not [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]


def test_failed_mutation_leaves_no_partial_write_and_releases_the_lock(tmp_path: Path,
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    import drive_state_io as io

    path = state_file(tmp_path)
    before = path.read_bytes()

    def boom(state: dict) -> None:
        state["history"].append({"round": 1})
        raise RuntimeError("writer crashed")
    with pytest.raises(RuntimeError):
        io.update(path, boom, mr_iid=fx.MR_IID, project_path=fx.REPO)
    assert path.read_bytes() == before
    real = os.replace
    monkeypatch.setattr(io.os, "replace", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        io.append_event(path, {"round": 2}, mr_iid=fx.MR_IID, project_path=fx.REPO)
    monkeypatch.setattr(io.os, "replace", real)
    assert path.read_bytes() == before and not [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    started = time.monotonic()
    io.append_event(path, {"round": 3}, mr_iid=fx.MR_IID, project_path=fx.REPO)  # lock was released
    assert time.monotonic() - started < 1 and history(path) == [{"round": 3}]


def test_lock_timeout_is_a_structured_input_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import drive_state_io as io

    path = state_file(tmp_path)
    holder = subprocess.Popen([sys.executable, "-c", (
        "import sys, time; sys.path.insert(0, sys.argv[1]); import drive_state_io as io\n"
        "from pathlib import Path\nwith io.locked(Path(sys.argv[2])):\n print('held', flush=True); time.sleep(5)"),
        str(Path(io.__file__).parent), str(path)], stdout=subprocess.PIPE, text=True)
    assert holder.stdout.readline().strip() == "held"
    monkeypatch.setattr(io, "LOCK_TIMEOUT_SECONDS", 0.3)
    with pytest.raises(io.StateError, match="state_lock_timeout"):
        with io.locked(path, timeout=0.3):
            pass
    holder.kill()
    holder.wait()


def test_protected_history_only_comes_from_its_producer(tmp_path: Path) -> None:
    path = state_file(tmp_path)
    scope = ["--state", str(path), "--mr-iid", str(fx.MR_IID), "--project-path", fx.REPO]
    for forged in (fx.verified_receipt(), fx.admission(), {"kind": "issue_binding"}, {"schema": "x", "round": 1}):
        result = run(STATE_CLI, "event", *scope, "--json", json.dumps(forged))
        assert result.returncode == 2 and "protected_history_kind" in result.stderr
    wrong = run(STATE_CLI, "event", "--state", str(path), "--mr-iid", "1", "--project-path", fx.REPO,
                "--json", '{"round": 1}')
    assert wrong.returncode == 2 and "state_binding_mismatch" in wrong.stderr
    stale = run(STATE_CLI, "complete", *scope, "--head-sha", HEAD2)
    assert stale.returncode == 2 and "state_binding_mismatch" in stale.stderr
    assert history(path) == [] and json.loads(path.read_text()).get("completed") is None
    done = run(STATE_CLI, "complete", *scope, "--head-sha", fx.HEAD)
    assert done.returncode == 0 and json.loads(path.read_text())["completed"] is True


def test_snapshot_streak_is_computed_under_the_lock(tmp_path: Path) -> None:
    import drive_state_io as io

    path = state_file(tmp_path)
    scope = {"mr_iid": fx.MR_IID, "project_path": fx.REPO}
    values = {"head_sha": fx.HEAD, "pipeline_id": 1, "unresolved_discussions": 0}
    assert io.record_snapshot(path, **scope, **values) == 0  # pipeline id appeared: a change
    assert io.record_snapshot(path, **scope, **values) == 1
    assert io.record_snapshot(path, **scope, **values) == 2
    assert io.record_snapshot(path, **scope, **{**values, "pipeline_id": 2}) == 0
    with pytest.raises(io.StateError, match="invalid_head_sha"):
        io.record_snapshot(path, **scope, **{**values, "head_sha": "abc"})


def test_initializer_writer_never_replaces_existing_history(tmp_path: Path) -> None:
    import drive_state_io as io

    path = state_file(tmp_path, {"round": 1, "status": "done"})
    with pytest.raises(io.StateError, match="state_exists_with_history"):
        io.create(path, {"mr_iid": fx.MR_IID, "history": []})
    assert history(path) == [{"round": 1, "status": "done"}]
    fresh = tmp_path / "fresh.json"
    io.create(fresh, {"mr_iid": fx.MR_IID, "project_path": fx.REPO, "history": []})
    assert fresh.stat().st_mode & 0o777 == 0o600


def test_receipt_append_rejects_a_binding_that_changed_during_the_live_read(tmp_path: Path) -> None:
    sys.path.insert(0, str(SCRIPTS))
    import verify_issue_sync_receipt as producer

    path = state_file(tmp_path, fx.admission("new-work"))
    receipt = fx.verified_receipt()  # produced with the default role, before the admission landed
    with pytest.raises(ValueError, match="binding_changed_during_verification"):
        producer.append_to_state(path, receipt, None, fx.HOST)
    assert [item["kind"] for item in history(path)] == ["issue_binding"]
