"""Issue61: a comment readback alone must never become a submitted/revised Issue receipt."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "lib"))

import gitlab_mr_issue_sync_fixture as fx  # noqa: E402
from issue_review_state import (  # noqa: E402
    ReadFailed,
    glab_reader,
    load_owner_host,
    verify,
)
from issue_sync_audit import audit_issue_sync  # noqa: E402

SCRIPT = Path(__file__).parents[1] / "scripts" / "verify_issue_sync_receipt.py"


def run(snap: dict, binding: dict | None = None, **changes) -> dict:
    return verify(fx.request(**changes), reader=fx.reader(snap), host=fx.HOST, binding=binding)


def reference() -> dict:
    """Binding resolved from a live closed-reference admission (release-flowback MR)."""
    return fx.binding(fx.admission("closed-reference"))


def test_owner_host_comes_from_platform_contract() -> None:
    assert load_owner_host() == fx.HOST


def test_mr205_comment_only_readback_is_not_a_receipt() -> None:
    receipt = run(fx.snapshot())

    assert receipt["result"] == "ISSUE_SYNC_UNVERIFIED" and receipt["verified"] is False
    assert receipt["errors"] == ["review_state_missing"]
    assert receipt["observed_state"] == "opened"
    assert receipt["review_state"]["verdict"] == "UNVERIFIED"
    assert receipt["gap"]["reason"] == "review_state_missing"
    assert receipt["gap"]["owner"] and "add_labels" in receipt["gap"]["next_action"]


def test_live_canonical_in_review_label_verifies_with_provenance() -> None:
    receipt = run(fx.in_review())

    assert receipt["result"] == "VERIFIED" and receipt["verified"] is True, receipt["errors"]
    assert receipt["source"] == "live"
    assert receipt["review_state"] == {
        "mode": "canonical-label", "required": "status::in-review",
        "observed_status_labels": ["status::in-review"], "verdict": "VERIFIED",
        "provenance": "live GET issue labels"}
    assert receipt["note_id"] == fx.NOTE_ID and receipt["revision"] == fx.HEAD
    assert receipt["marker"] == fx.marker() and receipt["gap"] is None


@pytest.mark.parametrize("label", ["Status::In-Review", "status::in_review", "status::in-review ",
                                   "status::inreview", "in-review", "status::review"])
def test_review_label_must_match_exactly(label: str) -> None:
    receipt = run(fx.snapshot([label]))
    assert receipt["result"] == "ISSUE_SYNC_UNVERIFIED"
    assert receipt["review_state"]["verdict"] != "VERIFIED"


def test_other_status_or_two_status_labels_are_not_in_review() -> None:
    assert run(fx.snapshot(["status::in-progress"]))["errors"] == ["review_state_missing"]
    assert run(fx.snapshot(["status::in-progress", "status::in-review"]))["errors"] == ["ambiguous_status_labels"]


def test_opened_state_is_not_a_review_state() -> None:
    snap = fx.snapshot(["opened"])
    snap["issue"]["state"] = "opened"
    assert run(snap)["review_state"]["verdict"] == "UNVERIFIED"


def test_self_asserted_body_field_or_caller_boolean_cannot_verify() -> None:
    snap = fx.snapshot()
    snap["note"]["body"] += "review_state: status::in-review\nverified: true\n"
    receipt = verify({**fx.request(), "review_state_verified": True, "verified": True},
                     reader=fx.reader(snap), host=fx.HOST)
    assert receipt["result"] == "ISSUE_SYNC_UNVERIFIED"
    assert receipt["errors"] == ["review_state_missing"]


@pytest.mark.parametrize("mutate, code", [
    (lambda s: s["issue"].update(web_url=fx.ISSUE_URL.replace("/24", "/23")), "issue_identity_mismatch"),
    (lambda s: s["issue"].update(iid=23), "issue_identity_mismatch"),
    (lambda s: s["issue"].update(state="locked"), "issue_not_open"),
    (lambda s: s["issue"].pop("state"), "issue_not_open"),
    (lambda s: s["note"].update(noteable_iid=23), "note_identity_mismatch"),
    (lambda s: s["note"].update(noteable_type="MergeRequest"), "note_identity_mismatch"),
    (lambda s: s["note"].update(system=True), "note_identity_mismatch"),
    (lambda s: s["note"].update(author={"id": 999}), "note_author_mismatch"),
    (lambda s: s["note"].update(body=fx.body(phase="revised")), "note_marker_mismatch"),
    (lambda s: s["note"].update(body=fx.body(head="e" * 40)), "note_marker_mismatch"),
    (lambda s: s["note"].update(body=fx.body() + fx.marker()), "note_marker_mismatch"),
    (lambda s: s["note"].update(body=fx.body().replace("\n", "\r\n")), "note_body_not_lf"),
    (lambda s: s["note"].update(body=fx.marker() + "\nno link\n"), "note_missing_mr_url"),
    (lambda s: s["merge_request"].update(sha="e" * 40), "mr_head_mismatch"),
    (lambda s: s["merge_request"].update(state="merged"), "mr_not_open"),
    (lambda s: s.update(related_merge_requests=[{"project_id": fx.PROJECT_ID, "iid": 204}]), "mr_not_related"),
    (lambda s: s.update(related_merge_requests=[{"project_id": 1, "iid": n} for n in range(100)]),
     "related_mr_page_limit"),
])
def test_wrong_target_revision_or_phase_is_rejected(mutate, code: str) -> None:
    snap = fx.in_review()
    mutate(snap)
    receipt = run(snap)
    assert receipt["result"] == "ISSUE_SYNC_UNVERIFIED" and code in receipt["errors"]


def test_closed_release_flowback_issue_is_terminal_not_applicable_never_in_review() -> None:
    receipt = run(fx.closed(), reference())

    assert receipt["result"] == "NOT_APPLICABLE_CLOSED" and receipt["verified"] is False
    assert receipt["errors"] == [] and receipt["gap"] is None
    assert receipt["observed_state"] == "closed" and receipt["source"] == "live"
    assert (receipt["binding_role"], receipt["binding_role_source"]) == ("closed-reference", "admission")
    assert receipt["review_state"] == {
        "mode": "canonical-label", "required": "status::in-review",
        "observed_status_labels": ["status::done"], "verdict": "NOT_APPLICABLE_CLOSED",
        "provenance": "live GET issue state=closed"}


@pytest.mark.parametrize("mutate, code", [
    (lambda s: s["issue"].update(iid=23), "issue_identity_mismatch"),
    (lambda s: s["note"].update(author={"id": 999}), "note_author_mismatch"),
    (lambda s: s["note"].update(noteable_iid=23), "note_identity_mismatch"),
    (lambda s: s["note"].update(body=fx.body(head="e" * 40)), "note_marker_mismatch"),
    (lambda s: s["note"].update(body=fx.marker() + "\nno link\n"), "note_missing_mr_url"),
    (lambda s: s["merge_request"].update(sha="e" * 40), "mr_head_mismatch"),
    (lambda s: s["merge_request"].update(state="merged"), "mr_not_open"),
    (lambda s: s.update(related_merge_requests=[{"project_id": fx.PROJECT_ID, "iid": 204}]), "mr_not_related"),
])
def test_closed_issue_keeps_every_note_mr_and_binding_check(mutate, code: str) -> None:
    snap = fx.closed()
    mutate(snap)
    receipt = run(snap, reference())
    assert receipt["result"] == "ISSUE_SYNC_UNVERIFIED" and code in receipt["errors"]


def test_closed_issue_native_status_offline_and_caller_flags_do_not_pass() -> None:
    assert run(fx.closed(), reference(), project_mode="native-status")["result"] == "BLOCKED"
    offline = verify(fx.request(), snapshot=fx.closed(), host=fx.HOST, binding=reference())
    assert offline["result"] == "ISSUE_SYNC_UNVERIFIED" and offline["errors"] == ["offline_snapshot_not_live"]
    forged = verify({**fx.request(), "closed_reference": True, "result": "NOT_APPLICABLE_CLOSED"},
                    reader=fx.reader(fx.snapshot()), host=fx.HOST)
    assert forged["result"] == "ISSUE_SYNC_UNVERIFIED" and forged["errors"] == ["review_state_missing"]
    self_asserted = verify({**fx.request(), "binding_role": "closed-reference"}, reader=fx.reader(fx.closed()),
                           host=fx.HOST, binding={"role": "closed-reference", "source": "request"})
    assert self_asserted["result"] == "ISSUE_SYNC_UNVERIFIED"
    assert self_asserted["errors"] == ["binding_role_unresolved"]


def without_catalog(snap: dict) -> tuple[dict, list[str], Callable[[str], Any]]:
    """Drop the label-catalog response (GET fails) and record every endpoint the verifier requests."""
    snap = {key: value for key, value in snap.items() if key != "project_labels"}
    calls: list[str] = []
    read = fx.reader(snap)

    def recording(endpoint: str):
        calls.append(endpoint)
        return read(endpoint)
    return snap, calls, recording


def test_closed_issue_does_not_need_the_label_catalog() -> None:
    snap, calls, read = without_catalog(fx.closed())
    receipt = verify(fx.request(), reader=read, host=fx.HOST, binding=reference())

    assert receipt["result"] == "NOT_APPLICABLE_CLOSED" and receipt["errors"] == [], receipt["errors"]
    assert receipt["review_state"]["provenance"] == "live GET issue state=closed"
    assert fx.LABELS_PATH not in calls
    assert set(calls) == set(fx.endpoints(snap))  # note, MR, HEAD and related-MR reads are all still made


@pytest.mark.parametrize("mutate, code", [
    (lambda s: s["issue"].update(iid=23), "issue_identity_mismatch"),
    (lambda s: s["note"].update(body=fx.body(head="e" * 40)), "note_marker_mismatch"),
    (lambda s: s["merge_request"].update(sha="e" * 40), "mr_head_mismatch"),
    (lambda s: s.update(related_merge_requests=[{"project_id": fx.PROJECT_ID, "iid": 204}]), "mr_not_related"),
])
def test_closed_issue_without_catalog_keeps_binding_checks(mutate, code: str) -> None:
    snap = fx.closed()
    mutate(snap)
    snap, calls, read = without_catalog(snap)
    receipt = verify(fx.request(), reader=read, host=fx.HOST, binding=reference())
    assert receipt["result"] == "ISSUE_SYNC_UNVERIFIED" and code in receipt["errors"]
    assert fx.LABELS_PATH not in calls


def test_opened_issue_still_requires_the_label_catalog() -> None:
    for opened in (fx.in_review(), fx.snapshot()):
        _, calls, read = without_catalog(opened)
        receipt = verify(fx.request(), reader=read, host=fx.HOST)
        assert receipt["result"] == "ISSUE_SYNC_UNVERIFIED" and receipt["errors"] == ["read_failed"]
        assert receipt["review_state"] is None and calls[-1] == fx.LABELS_PATH


def test_closed_issue_without_catalog_native_status_and_offline_still_fail() -> None:
    _, calls, read = without_catalog(fx.closed())
    assert verify(fx.request(project_mode="native-status"), reader=read, host=fx.HOST,
                  binding=reference())["result"] == "BLOCKED"
    offline = verify(fx.request(), snapshot=without_catalog(fx.closed())[0], host=fx.HOST, binding=reference())
    assert offline["result"] == "ISSUE_SYNC_UNVERIFIED" and offline["errors"] == ["offline_snapshot_not_live"]


@pytest.mark.parametrize("issue", [None, [], "closed", {"state": ["closed"]}])
def test_malformed_issue_json_still_reads_catalog_and_fails_closed(issue) -> None:
    snap = fx.closed()
    snap["issue"] = issue
    calls: list[str] = []
    read = fx.reader(snap)
    receipt = verify(fx.request(), reader=lambda endpoint: calls.append(endpoint) or read(endpoint), host=fx.HOST,
                     binding=reference())
    assert receipt["result"] == "ISSUE_SYNC_UNVERIFIED" and "issue_identity_mismatch" in receipt["errors"]
    assert fx.LABELS_PATH in calls


def test_cli_records_closed_reference_when_label_catalog_is_unreadable(tmp_path: Path) -> None:
    state = drive_state(tmp_path, fx.admission("closed-reference"))
    result = cli(tmp_path, without_catalog(fx.closed())[0], "--state", str(state))
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(state.read_text())["history"][1]["result"] == "NOT_APPLICABLE_CLOSED"
    calls = (tmp_path / "bin" / "fixture.json.calls").read_text().split()
    assert "user" in calls and not any("/labels?" in call for call in calls)
    assert fx.SECRET not in result.stdout + result.stderr


def test_expected_body_uses_lf_only_trailing_normalization() -> None:
    assert run(fx.in_review(), expected_body=fx.body().rstrip("\n"))["result"] == "VERIFIED"
    assert "note_body_mismatch" in run(fx.in_review(), expected_body=fx.body() + "extra\n")["errors"]
    with pytest.raises(ValueError, match="expected_body_must_be_lf_text"):
        run(fx.in_review(), expected_body=fx.body().replace("\n", "\r\n"))


def test_unsupported_native_status_mode_is_blocked_not_pretended() -> None:
    receipt = run(fx.in_review(), project_mode="native-status")
    assert receipt["result"] == "BLOCKED" and receipt["verified"] is False
    assert receipt["review_state"]["verdict"] == "BLOCKED"
    assert receipt["gap"]["reason"] == "unsupported_project_mode"
    assert receipt["gap"]["owner"] and receipt["gap"]["next_action"]


def test_project_without_canonical_label_is_a_structured_mode_gap() -> None:
    snap = fx.snapshot()
    snap["project_labels"] = [{"name": "status::in-progress"}]
    receipt = run(snap)
    assert receipt["result"] == "BLOCKED"
    assert receipt["gap"]["reason"] == "canonical_review_label_absent"
    assert "Issue project maintainer" in receipt["gap"]["owner"]


def test_read_failure_is_unverified_without_partial_success() -> None:
    def failing(endpoint: str):
        raise ReadFailed("read_failed")

    receipt = verify(fx.request(), reader=failing, host=fx.HOST)
    assert receipt["result"] == "ISSUE_SYNC_UNVERIFIED" and receipt["errors"] == ["read_failed"]
    assert receipt["review_state"] is None and receipt["note_id"] is None


def test_offline_snapshot_never_verifies() -> None:
    receipt = verify(fx.request(), snapshot=fx.in_review(), host=fx.HOST)
    assert receipt["source"] == "offline-snapshot" and receipt["verified"] is False
    assert receipt["errors"] == ["offline_snapshot_not_live"]


@pytest.mark.parametrize("changes, code", [
    ({"phase": "merged"}, "unsupported_phase"),
    ({"revision": "d7665a9"}, "invalid_revision"),
    ({"revision": fx.HEAD.upper()}, "invalid_revision"),
    ({"note_id": True}, "invalid_note_id"),
    ({"issue_url": "https://evil.example/WEB/micro_app_platform/-/issues/24"}, "untrusted_issue_url"),
    ({"project_mode": "free-text"}, "unknown_project_mode"),
])
def test_invalid_requests_are_rejected_before_reading(changes: dict, code: str) -> None:
    with pytest.raises(ValueError, match=code):
        verify(fx.request(**changes), reader=fx.reader(fx.in_review()), host=fx.HOST)


def test_glab_reader_is_get_only_and_never_leaks_stderr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = fx.install_fake_glab(tmp_path / "bin", fx.in_review())
    for key in ("PATH", "FAKE_GLAB_FIXTURE", "FAKE_GLAB_SECRET"):
        monkeypatch.setenv(key, env[key])
    read = glab_reader(fx.HOST)
    assert read("user")["id"] == fx.AUTHOR_ID
    with pytest.raises(ReadFailed) as caught:
        read("projects/unknown")
    assert fx.SECRET not in str(caught.value)
    receipt = verify(fx.request(), host=fx.HOST)  # default reader is the real glab boundary
    assert receipt["result"] == "VERIFIED"
    assert fx.SECRET not in json.dumps(receipt)


def cli(tmp_path: Path, snap: dict | None, *extra: str) -> subprocess.CompletedProcess[str]:
    env = fx.install_fake_glab(tmp_path / "bin", snap)
    args = ["--issue", fx.ISSUE_URL, "--mr-project", fx.REPO, "--mr-iid", str(fx.MR_IID),
            "--revision", fx.HEAD, "--phase", "submitted", "--note-id", str(fx.NOTE_ID), *extra]
    return subprocess.run([sys.executable, str(SCRIPT), *args], env=env, text=True,
                          capture_output=True, check=False)


def drive_state(tmp_path: Path, *history: dict) -> Path:
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"mr_iid": fx.MR_IID, "project_path": fx.REPO,
                                "last_snapshot": {"head_sha": fx.HEAD}, "history": list(history)}),
                    encoding="utf-8")
    return path


def test_cli_records_verified_live_receipt_in_drive_state(tmp_path: Path) -> None:
    state = drive_state(tmp_path)
    result = cli(tmp_path, fx.in_review(), "--state", str(state))
    assert result.returncode == 0, result.stderr
    history = json.loads(state.read_text())["history"]
    assert len(history) == 1 and history[0]["result"] == "VERIFIED"
    assert history[0] == json.loads(result.stdout)
    calls = (tmp_path / "bin" / "fixture.json.calls").read_text().split()
    assert "user" in calls and any("/labels?" in call for call in calls)


def test_cli_records_closed_reference_with_get_only_reads(tmp_path: Path) -> None:
    state = drive_state(tmp_path, fx.admission("closed-reference"))
    result = cli(tmp_path, fx.closed(), "--state", str(state))
    assert result.returncode == 0, result.stderr
    receipt = json.loads(state.read_text())["history"][1]
    assert receipt == json.loads(result.stdout) and receipt["result"] == "NOT_APPLICABLE_CLOSED"
    assert receipt["review_state"]["verdict"] == "NOT_APPLICABLE_CLOSED"


def test_cli_preserves_comment_only_result_and_exits_nonzero(tmp_path: Path) -> None:
    state = drive_state(tmp_path)
    result = cli(tmp_path, fx.snapshot(), "--state", str(state))
    assert result.returncode == 1
    receipt = json.loads(state.read_text())["history"][0]
    assert receipt["result"] == "ISSUE_SYNC_UNVERIFIED" and receipt["gap"]["reason"] == "review_state_missing"


def test_cli_read_failure_hides_credentials(tmp_path: Path) -> None:
    result = cli(tmp_path, None)
    assert result.returncode == 1
    assert json.loads(result.stdout)["errors"] == ["read_failed"]
    assert fx.SECRET not in result.stdout + result.stderr


def test_cli_offline_snapshot_cannot_be_recorded(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(json.dumps(fx.in_review()), encoding="utf-8")
    state = drive_state(tmp_path)
    result = cli(tmp_path, fx.in_review(), "--check-snapshot", str(snapshot), "--state", str(state))
    assert result.returncode == 2 and json.loads(state.read_text())["history"] == []
    offline = cli(tmp_path, None, "--check-snapshot", str(snapshot))
    assert offline.returncode == 1 and json.loads(offline.stdout)["source"] == "offline-snapshot"


def test_cli_refuses_to_record_for_another_head(tmp_path: Path) -> None:
    state = drive_state(tmp_path)
    data = json.loads(state.read_text())
    data["last_snapshot"]["head_sha"] = "e" * 40
    state.write_text(json.dumps(data), encoding="utf-8")
    result = cli(tmp_path, fx.in_review(), "--state", str(state))
    assert result.returncode == 2 and "state_binding_mismatch" in result.stderr
    assert json.loads(state.read_text())["history"] == []


class Fail(ValueError):
    pass


def audit_state(*receipts: dict, head: str = fx.HEAD) -> dict:
    return {"mr_iid": fx.MR_IID, "project_path": fx.REPO, "last_snapshot": {"head_sha": head},
            "history": [{"round": 1, "status": "done"}, *receipts]}


def live(snap: dict):
    return lambda request, binding=None: verify(request, reader=fx.reader(snap), host=fx.HOST, binding=binding)


def test_audit_requires_and_rereads_a_verified_current_head_receipt() -> None:
    receipt = fx.verified_receipt()
    assert audit_issue_sync(audit_state(receipt), Fail, live(fx.in_review()))[0]["result"] == "VERIFIED"
    with pytest.raises(Fail, match="no submitted/revised Issue receipt"):
        audit_issue_sync(audit_state(), Fail, live(fx.in_review()))


def test_audit_rejects_comment_only_receipt_even_if_label_added_later() -> None:
    comment_only = run(fx.snapshot())
    with pytest.raises(Fail, match="review_state_missing"):
        audit_issue_sync(audit_state(comment_only), Fail, live(fx.in_review()))


def test_audit_rejects_self_asserted_receipt_when_live_state_disagrees() -> None:
    forged = {**run(fx.snapshot()), "result": "VERIFIED", "verified": True, "errors": [], "gap": None}
    forged["review_state"] = {**forged["review_state"], "verdict": "VERIFIED"}
    with pytest.raises(Fail, match="fresh review-state readback failed \\(review_state_missing\\)"):
        audit_issue_sync(audit_state(forged), Fail, live(fx.snapshot()))


def test_audit_rejects_offline_blocked_stale_and_wrong_project_receipts() -> None:
    offline = verify(fx.request(), snapshot=fx.in_review(), host=fx.HOST)
    blocked = run(fx.in_review(), project_mode="native-status")
    for receipt, message in ((offline, "offline_snapshot_not_live"), (blocked, "unsupported_project_mode")):
        with pytest.raises(Fail, match=message):
            audit_issue_sync(audit_state(receipt), Fail, live(fx.in_review()))
    with pytest.raises(Fail, match="no submitted/revised Issue receipt"):
        audit_issue_sync(audit_state(fx.verified_receipt(), head="e" * 40), Fail, live(fx.in_review()))
    other = {**fx.verified_receipt(), "issue_url": fx.ISSUE_URL.replace("/24", "/23")}
    with pytest.raises(Fail, match="not refreshed for current HEAD"):
        audit_issue_sync(audit_state({**other, "revision": "e" * 40}, fx.verified_receipt()), Fail,
                         live(fx.in_review()))
    state = audit_state(fx.verified_receipt())
    state["project_path"] = "WEB/other"
    with pytest.raises(Fail, match="bound to another MR project"):
        audit_issue_sync(state, Fail, live(fx.in_review()))


def closed_receipt() -> dict:
    return run(fx.closed(), reference())


def test_audit_accepts_fresh_live_closed_reference_receipt() -> None:
    fresh = audit_issue_sync(audit_state(fx.admission(), closed_receipt()), Fail, live(fx.closed()), host=fx.HOST)
    assert [item["result"] for item in fresh] == ["NOT_APPLICABLE_CLOSED"]


def test_audit_requires_refresh_when_issue_lifecycle_changed() -> None:
    with pytest.raises(Fail, match="Issue state changed since the recorded receipt"):
        audit_issue_sync(audit_state(fx.admission(), closed_receipt()), Fail, live(fx.in_review()))
    # Issue61 F1: an opened receipt never silently becomes a closed reference; the role decides the reason.
    with pytest.raises(Fail, match=r"readback failed \(binding_role_unresolved\)"):
        audit_issue_sync(audit_state(fx.verified_receipt()), Fail, live(fx.closed()))
    new_work = fx.admission("new-work")
    receipt = run(fx.in_review(), fx.binding(new_work))
    with pytest.raises(Fail, match=r"readback failed \(issue_closed_during_review\)"):
        audit_issue_sync(audit_state(new_work, receipt), Fail, live(fx.closed()))


@pytest.mark.parametrize("forge", [
    lambda r: {**r, "result": "NOT_APPLICABLE_CLOSED", "review_state": {
        **r["review_state"], "verdict": "NOT_APPLICABLE_CLOSED"}},
    lambda r: {**r, "result": "SKIPPED", "status_applicable": False, "reason": "release flowback"},
    lambda r: {**r, "review_state": {**r["review_state"], "verdict": "NOT_APPLICABLE_CLOSED"}},
    lambda r: {**r, "result": ["NOT_APPLICABLE_CLOSED"]},
])
def test_audit_rejects_caller_forged_closed_skip(forge) -> None:
    for live_snap in (fx.in_review(), fx.closed()):
        with pytest.raises(Fail, match="ISSUE_SYNC_UNVERIFIED"):
            audit_issue_sync(audit_state(forge(fx.verified_receipt())), Fail, live(live_snap))


def test_audit_rejects_closed_receipt_with_mismatched_observed_state() -> None:
    with pytest.raises(Fail, match="ISSUE_SYNC_UNVERIFIED"):
        audit_issue_sync(audit_state(fx.admission(), {**closed_receipt(), "observed_state": "opened"}), Fail,
                         live(fx.closed()))


def test_audit_latest_receipt_per_issue_wins_and_note_must_be_unchanged() -> None:
    later_failure = run(fx.snapshot())
    with pytest.raises(Fail, match="review_state_missing"):
        audit_issue_sync(audit_state(fx.verified_receipt(), later_failure), Fail, live(fx.in_review()))
    assert audit_issue_sync(audit_state(later_failure, fx.verified_receipt()), Fail, live(fx.in_review()))
    edited = fx.in_review()
    edited["note"]["body"] = fx.body() + "edited after readback\n"
    with pytest.raises(Fail, match="differs from the recorded receipt"):
        audit_issue_sync(audit_state(fx.verified_receipt()), Fail, live(edited))
