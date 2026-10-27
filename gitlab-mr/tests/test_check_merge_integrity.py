"""check_merge_integrity.py：用最小仓库复现事故链路，并覆盖代码评审给出的漏报 / 误报场景。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "check_merge_integrity.py"
ISOLATED = {
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "Test User", "GIT_AUTHOR_EMAIL": "tests@example.com",
    "GIT_COMMITTER_NAME": "Test User", "GIT_COMMITTER_EMAIL": "tests@example.com",
}


class Repo:
    def __init__(self, path: Path, branch: str = "master") -> None:
        self.path = path
        path.mkdir(parents=True)
        self.git("init", "-q", "-b", branch)
        self.git("config", "commit.gpgsign", "false")

    def git(self, *args: str, cwd: Path | None = None) -> str:
        return subprocess.run(["git", *args], cwd=cwd or self.path, text=True, capture_output=True, check=True,
                              env={**os.environ, **ISOLATED}).stdout.strip()

    def write(self, relative: str, content: str | bytes) -> None:
        path = self.path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        (path.write_bytes if isinstance(content, bytes) else lambda c: path.write_text(c, encoding="utf-8"))(content)

    def commit(self, message: str) -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD")

    def rev(self, ref: str) -> str:
        return self.git("rev-parse", ref)

    def check(self, target: str, source: str, cwd: Path | None = None, env: dict | None = None) -> tuple[int, dict]:
        result = subprocess.run([sys.executable, str(SCRIPT), target, source, "--json"], cwd=cwd or self.path,
                                text=True, capture_output=True, env={**os.environ, **ISOLATED, **(env or {})})
        assert result.returncode in (0, 1, 2), result.stderr
        report = json.loads(result.stdout) if result.stdout else {}
        if report:
            assert report["failed"] == (result.returncode == 1)
            assert report["exit_code"] == result.returncode
        return result.returncode, report


def levels(report: dict) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {check: set() for check in "ABCDE"}
    for finding in report["findings"]:
        out[finding["check"]].add(finding["level"])
    return out


def fails(report: dict) -> set[str]:
    return {c for c, lv in levels(report).items() if "FAIL" in lv}


def files(report: dict, check_id: str, level: str = "FAIL") -> set[str]:
    return {p for f in report["findings"] if f["check"] == check_id and f["level"] == level for p in f["files"]}


def retarget_by_tree_replacement(repo: Repo, feature: str, new_target: str, keep: list[str]) -> str:
    """The incident's bad retarget: merge the new target, replace the tree, keep only the feature's files."""
    repo.git("merge", "-q", "--no-ff", "--no-commit", new_target)
    repo.git("rm", "-q", "-r", "--cached", ".")
    repo.git("read-tree", new_target)
    repo.git("checkout-index", "-a", "-f")
    for path in keep:
        repo.git("checkout", feature, "--", path)
    repo.git("clean", "-q", "-fd")
    return repo.commit("merge: retarget feature to the new release")


def incident(tmp: Path, support: str = "app/support.txt", criss_cross: bool = True) -> tuple[Repo, dict[str, str]]:
    repo = Repo(tmp / "repo")
    repo.write("app/shared.txt", "shared\n")
    repo.write(support, "v1\n")
    repo.commit("base")
    repo.git("checkout", "-q", "-b", "release/A")
    repo.write(support, "v1\nsupport query v2\n")
    repo.write("app/collection.txt", "bird collection api\n" * 5)
    repo.commit("feat: business MRs on release A")
    repo.git("checkout", "-q", "master")
    repo.write("app/hotfix.txt", "hotfix\n")
    repo.commit("hotfix on master")
    repo.git("checkout", "-q", "-b", "release/B")
    repo.write("app/b-only.txt", "b feature\n")
    repo.commit("feat: release B work")
    repo.git("checkout", "-q", "-b", "feat/intercom-rel-A", "release/A")
    repo.write("app/intercom.txt", "intercom\n")
    feature = repo.commit("feat: intercom capability")
    refs = {"retarget_target": repo.rev("release/B")}
    refs["retarget"] = retarget_by_tree_replacement(repo, feature, "release/B", ["app/intercom.txt"])
    repo.git("checkout", "-q", "release/B")
    repo.git("merge", "-q", "--no-ff", "-m", "Merge intercom into release/B", "feat/intercom-rel-A")
    refs["b_before_sync"] = repo.rev("HEAD")
    repo.git("checkout", "-q", "release/A")
    if criss_cross:
        repo.git("merge", "-q", "--no-ff", "-m", "sync master hotfix into release/A", "master")
    repo.write("app/late.txt", "late fix on A\n")
    refs["a"] = repo.commit("fix: late change on release A")
    repo.git("checkout", "-q", "release/B")
    repo.git("merge", "-q", "--no-ff", "-m", "merge release/A into release/B", "release/A")
    refs["b_after_sync"] = repo.rev("HEAD")
    repo.git("checkout", "-q", "master")
    repo.git("merge", "-q", "--no-ff", "-m", "merge release/A into master", "release/A")
    refs["master"] = repo.rev("HEAD")
    return repo, refs


# ---- the incident chain ------------------------------------------------------------------


def test_retarget_by_merge_and_tree_replacement_is_an_evil_merge(tmp_path: Path) -> None:
    repo, refs = incident(tmp_path)
    code, report = repo.check(refs["retarget_target"], refs["retarget"])
    assert code == 1
    assert fails(report) == {"B", "C"}  # C: re-checked inside the merge, release A's content stays behind
    assert {"app/support.txt", "app/collection.txt"} <= files(report, "B")


def test_release_sync_after_evil_merge_silently_keeps_old_content(tmp_path: Path) -> None:
    repo, refs = incident(tmp_path)
    assert len(repo.git("merge-base", "--all", refs["b_before_sync"], refs["a"]).split()) == 2
    code, report = repo.check(refs["b_before_sync"], refs["a"])
    assert code == 1
    assert fails(report) == {"A", "C", "E"}
    assert "app/support.txt" in files(report, "A") & files(report, "C") & files(report, "E")
    # Git really leaves the content behind while delivering the later change.
    assert repo.git("show", f"{refs['b_after_sync']}:app/support.txt") == "v1"
    assert repo.git("show", f"{refs['b_after_sync']}:app/late.txt") == "late fix on A"


def test_release_to_master_would_propagate_the_deletion(tmp_path: Path) -> None:
    repo, refs = incident(tmp_path)
    code, report = repo.check(refs["master"], refs["b_after_sync"])
    assert code == 1
    assert "D" in fails(report)
    assert "app/support.txt" in files(report, "D")


def test_single_merge_base_loss_is_caught_from_target_history(tmp_path: Path) -> None:
    repo, refs = incident(tmp_path, criss_cross=False)
    assert len(repo.git("merge-base", "--all", refs["b_before_sync"], refs["a"]).split()) == 1
    code, report = repo.check(refs["b_before_sync"], refs["a"])
    assert code == 1
    assert fails(report) == {"E"}
    assert "app/support.txt" in files(report, "E")


# ---- round-2 review: line-level drops, renames, restores ----------------------------------


def test_lines_trimmed_inside_a_merge_are_a_counted_warning(tmp_path: Path) -> None:
    repo = Repo(tmp_path / "trim", branch="main")
    repo.write("x.txt", "keep\nmain change\n")
    repo.commit("base")
    repo.git("checkout", "-q", "-b", "feature")
    repo.write("x.txt", "keep\nmain change\nfeature\n")
    repo.commit("feat: touch x")
    repo.git("checkout", "-q", "main")
    repo.write("y.txt", "y\n")
    repo.commit("main")
    repo.git("checkout", "-q", "feature")
    repo.git("merge", "-q", "--no-ff", "--no-commit", "main")
    repo.write("x.txt", "keep\nfeature\n")
    repo.commit("merge main, silently drop a line")
    code, report = repo.check("main", "feature")
    assert code == 0  # partial removals inside merges are usually adaptation edits; see known limitations
    warn = [f for f in report["findings"] if f["check"] == "B" and f.get("lost_lines")]
    assert [(f["files"], f["lost_lines"]) for f in warn] == [(["x.txt"], 1)]


def test_target_evil_merge_still_fails_when_source_edits_the_file_later(tmp_path: Path) -> None:
    repo, refs = incident(tmp_path, criss_cross=False)
    repo.git("checkout", "-q", refs["a"])
    repo.write("app/support.txt", "support header v3\nv1\nsupport query v2\n")  # no overlap with the drop
    source = repo.commit("fix: extend support on A")
    code, report = repo.check(refs["b_before_sync"], source)
    assert code == 1
    by_origin = {f["origin"]: set(f["files"]) for f in report["findings"] if f["check"] == "E" and f["level"] == "FAIL"}
    assert by_origin == {"source": {"app/support.txt"}, "target-history": {"app/collection.txt"}}


def test_target_evil_merge_is_tracked_through_a_source_rename(tmp_path: Path) -> None:
    repo, refs = incident(tmp_path, criss_cross=False)
    repo.git("checkout", "-q", refs["a"])
    repo.git("mv", "app/support.txt", "app/support_query.txt")
    source = repo.commit("refactor: rename support")
    code, report = repo.check(refs["b_before_sync"], source)
    assert code == 1
    assert "app/support.txt" in files(report, "E")


def test_restore_with_extra_lines_in_the_target_passes_e(tmp_path: Path) -> None:
    repo, refs = incident(tmp_path, criss_cross=False)
    repo.git("checkout", "-q", refs["b_before_sync"])
    repo.write("app/support.txt", "v1\nsupport query v2\nrestored by !3690\n")
    repo.write("app/collection.txt", "bird collection api\n" * 5)
    target = repo.commit("restore lost MRs")
    code, report = repo.check(target, refs["a"])
    assert levels(report)["E"] == {"OK"}, report
    assert code == 0


def test_latent_target_loss_is_labelled_as_target_history(tmp_path: Path) -> None:
    repo, refs = incident(tmp_path, criss_cross=False)
    code, report = repo.check(refs["b_before_sync"], refs["a"])
    assert code == 1
    origins = {f.get("origin") for f in report["findings"] if f["check"] == "E" and f["level"] == "FAIL"}
    assert origins == {"target-history"}


def test_deliberate_revert_on_the_target_downgrades_c_to_warning(tmp_path: Path) -> None:
    repo, refs = incident(tmp_path)
    repo.git("checkout", "-q", refs["b_before_sync"])
    repo.write("app/support.txt", "reverting\n")
    repo.commit("revert support query (step 1)")
    repo.write("app/support.txt", "v1\n")
    target = repo.commit("revert support query (step 2)")
    code, report = repo.check(target, refs["a"])
    assert "app/support.txt" in files(report, "C", "WARN")
    assert "app/support.txt" not in files(report, "C")
    assert "app/support.txt" in files(report, "A", "WARN")
    assert "app/support.txt" not in files(report, "A")


def test_shallow_clone_exits_2(tmp_path: Path) -> None:
    repo, refs = incident(tmp_path)
    clone = tmp_path / "shallow"
    repo.git("clone", "-q", "--depth", "1", "--no-local", "-b", "release/B", str(repo.path), str(clone))
    result = subprocess.run([sys.executable, str(SCRIPT), "HEAD", "HEAD"], cwd=clone, text=True,
                            capture_output=True, env={**os.environ, **ISOLATED})
    assert result.returncode == 2
    assert "shallow" in result.stderr


def test_ok_lines_are_not_printed_next_to_warnings(tmp_path: Path) -> None:
    repo, refs = incident(tmp_path)
    _, report = repo.check(refs["b_before_sync"], refs["a"])
    for check_id in "BCDE":
        found = levels(report)[check_id]
        assert not ({"OK"} < found), (check_id, found)


# ---- round-3 review: pre-merged sync branch, merge renames, boundaries ---------------------


def test_pre_merged_sync_branch_is_checked_inside_its_merge(tmp_path: Path) -> None:
    """GitLab release syncs with conflicts are submitted as a branch that already holds the merge (!3614)."""
    repo, refs = incident(tmp_path)
    repo.git("checkout", "-q", "-b", "merge/A-into-B", refs["b_before_sync"])
    repo.git("merge", "-q", "--no-ff", "-m", "merge release/A into release/B", refs["a"])
    repo.write("app/compile-fix.txt", "fix\n")
    sync = repo.commit("fix: compile after sync")
    code, report = repo.check(refs["b_before_sync"], sync)
    assert code == 1
    assert "app/support.txt" in files(report, "A") & files(report, "C")
    assert {f.get("commit") for f in report["findings"] if f["check"] in "AC" and f["level"] == "FAIL"} \
        == {repo.rev("HEAD~1")[:10]}


def test_pre_merged_sync_after_partial_restore_still_sees_target_history_loss(tmp_path: Path) -> None:
    """Restore only some files on the target, redo the pre-merged sync: E must look inside the sync merge."""
    repo, refs = incident(tmp_path)
    repo.git("checkout", "-q", "-b", "restore", refs["b_before_sync"])
    repo.write("app/support.txt", "v1\nsupport query v2\n")
    target = repo.commit("restore support only")
    repo.git("checkout", "-q", "-b", "merge/A-into-B-2", target)
    repo.git("merge", "-q", "--no-ff", "-m", "redo sync", refs["a"])
    code, report = repo.check(target, "merge/A-into-B-2")
    assert code == 1
    assert "app/collection.txt" in files(report, "E")
    assert "app/support.txt" not in files(report, "E")


def test_source_side_pre_merged_sync_after_partial_restore_sees_target_history_loss(tmp_path: Path) -> None:
    repo, refs = incident(tmp_path)
    repo.git("checkout", "-q", "-b", "restore", refs["b_before_sync"])
    repo.write("app/support.txt", "v1\nsupport query v2\n")
    target = repo.commit("restore support only")
    repo.git("checkout", "-q", "-b", "merge/B-into-A-2", refs["a"])
    repo.git("merge", "-q", "--no-ff", "-m", "redo sync from the source side", target)
    code, report = repo.check(target, "merge/B-into-A-2")
    assert code == 1
    assert "app/collection.txt" in files(report, "E")


def test_conflicts_of_non_first_parent_merges_are_only_info(tmp_path: Path) -> None:
    repo = Repo(tmp_path / "info", branch="main")
    repo.write("f.txt", "base\n")
    repo.commit("base")
    repo.git("checkout", "-q", "-b", "topic")
    repo.write("f.txt", "topic\n")
    repo.commit("topic edits f")
    repo.git("checkout", "-q", "-b", "side", "main")
    repo.write("f.txt", "side\n")
    repo.commit("side edits f")
    subprocess.run(["git", "merge", "-q", "topic"], cwd=repo.path, capture_output=True, env={**os.environ, **ISOLATED})
    repo.write("f.txt", "side\ntopic\n")
    repo.commit("merge topic into side, resolve f")
    repo.git("checkout", "-q", "-b", "feature", "main")
    repo.write("g.txt", "g\n")
    repo.commit("feature")
    repo.git("merge", "-q", "--no-ff", "-m", "merge side", "side")
    code, report = repo.check("main", "feature")
    assert code == 0, report
    assert files(report, "C", "INFO") == {"f.txt"}
    assert not files(report, "C", "WARN")


def test_adaptation_edit_in_target_history_is_not_an_inner_e_failure(tmp_path: Path) -> None:
    """A release merge that adapts (trims) a topic line must not fail later feature syncs (real 378e61b3da shape)."""
    repo = Repo(tmp_path / "adapt", branch="release")
    repo.write("a.txt", "a\n")
    repo.commit("base")
    repo.git("checkout", "-q", "-b", "topic")
    repo.write("a.txt", "a\nkeep K\nold call L\n")
    repo.commit("topic")
    repo.git("checkout", "-q", "-b", "feature")
    repo.write("f.txt", "f\n")
    repo.commit("feature")
    repo.git("checkout", "-q", "release")
    repo.write("r.txt", "r\n")
    repo.commit("release work")
    repo.git("merge", "-q", "--no-ff", "--no-commit", "topic")
    repo.write("a.txt", "a\nkeep K\n")  # adapt: drop the line that no longer compiles
    repo.commit("merge topic, adapt a")
    repo.git("checkout", "-q", "feature")
    repo.git("merge", "-q", "--no-ff", "-m", "sync release", "release")
    code, report = repo.check("release", "feature")
    assert code == 0, report


def test_pre_merged_sync_branch_from_the_source_side_is_checked(tmp_path: Path) -> None:
    """GitLab's "resolve locally" recipe: branch from the source, merge the target into it."""
    repo, refs = incident(tmp_path)
    repo.git("checkout", "-q", "-b", "merge/B-into-A", refs["a"])
    repo.git("merge", "-q", "--no-ff", "-m", "merge release/B into release/A", refs["b_before_sync"])
    code, report = repo.check(refs["b_before_sync"], "merge/B-into-A")
    assert code == 1
    assert "app/support.txt" in files(report, "A") & files(report, "C")


def test_conflicts_resolved_inside_a_pre_merged_branch_are_listed(tmp_path: Path) -> None:
    repo = Repo(tmp_path / "xours", branch="rel22")
    repo.write("svc.txt", "a\nb\nc\n")
    repo.commit("base")
    repo.git("checkout", "-q", "-b", "rel15")
    repo.write("svc.txt", "a\nfix on 15\nc\n")
    repo.commit("fix on 15")
    repo.git("checkout", "-q", "rel22")
    repo.write("svc.txt", "a\nchange on 22\nc\n")
    repo.commit("change on 22")
    repo.git("checkout", "-q", "-b", "sync")
    repo.git("merge", "-q", "-X", "ours", "-m", "sync 15 into 22", "rel15")
    _, report = repo.check("rel22", "sync")
    assert files(report, "C", "WARN") == {"svc.txt"}


def test_evil_merge_hidden_by_a_rename_needs_rename_aware_replay(tmp_path: Path) -> None:
    repo = Repo(tmp_path / "ren", branch="main")
    repo.write("x.txt", "".join(f"line {i}\n" for i in range(20)))
    repo.commit("base")
    repo.git("checkout", "-q", "-b", "feature")
    repo.write("x.txt", "".join(f"line {i}\n" for i in range(20)) + "feature line\n")
    repo.commit("feat: extend x")
    repo.git("checkout", "-q", "main")
    repo.git("mv", "x.txt", "y.txt")
    main_y = repo.commit("rename x to y")
    repo.git("checkout", "-q", "feature")
    repo.git("merge", "-q", "--no-ff", "--no-commit", "main")
    repo.git("checkout", main_y, "--", "y.txt")  # keep main's y.txt, dropping the feature line
    repo.commit("merge main")
    assert "feature line" not in repo.git("show", "HEAD:y.txt")
    code, report = repo.check("main", "feature")
    assert code == 1
    assert "y.txt" in files(report, "B")


def test_merge_resetting_a_file_to_the_merge_base_is_a_reset(tmp_path: Path) -> None:
    repo = Repo(tmp_path / "rb", branch="main")
    repo.write("x.txt", "x\n")
    base = repo.commit("base")
    repo.git("checkout", "-q", "-b", "feature")
    repo.write("x.txt", "x\nfeature\n")
    repo.commit("feat")
    repo.git("checkout", "-q", "main")
    repo.write("x.txt", "main\nx\n")
    repo.commit("main")
    repo.git("checkout", "-q", "feature")
    repo.git("merge", "-q", "--no-ff", "--no-commit", "main")
    repo.git("checkout", base, "--", "x.txt")
    repo.commit("merge main, reset x to base")
    code, report = repo.check("main", "feature")
    assert code == 1
    assert files(report, "B") == {"x.txt"}


def test_line_replaced_inside_a_merge_is_not_a_d_removal(tmp_path: Path) -> None:
    repo = Repo(tmp_path / "rep", branch="main")
    repo.write("z.txt", "z\nkeep\n")
    repo.commit("base")
    repo.git("checkout", "-q", "-b", "feature")
    repo.write("f.txt", "f\n")
    repo.commit("feat")
    repo.git("checkout", "-q", "main")
    repo.write("m.txt", "m\n")
    repo.commit("main")
    repo.git("checkout", "-q", "feature")
    repo.git("merge", "-q", "--no-ff", "--no-commit", "main")
    repo.write("z.txt", "Z\nkeep\n")  # one line replaced: net zero lines
    repo.commit("merge main, adapt z")
    code, report = repo.check("main", "feature")
    assert "z.txt" not in files(report, "D")
    assert code == 0


def test_target_side_octopus_merge_is_unverified(tmp_path: Path) -> None:
    repo = Repo(tmp_path / "oct-t", branch="main")
    repo.write("x.txt", "x\n")
    repo.commit("base")
    repo.git("checkout", "-q", "-b", "feature")
    repo.write("f.txt", "f\n")
    repo.commit("feat")
    for name in ("o1", "o2"):
        repo.git("checkout", "-q", "-b", name, "main")
        repo.write(f"{name}.txt", f"{name}\n")
        repo.commit(name)
    repo.git("checkout", "-q", "main")
    repo.git("merge", "-q", "--no-ff", "-m", "octopus", "o1", "o2")
    code, report = repo.check("main", "feature")
    assert code == 2
    assert levels(report)["E"] == {"UNVERIFIED"}


# ---- false negatives found in review -----------------------------------------------------


@pytest.mark.parametrize("support", ["app/支持.txt", "app/with\ttab.txt"])
def test_non_ascii_and_tab_paths_are_compared_correctly(tmp_path: Path, support: str) -> None:
    repo, refs = incident(tmp_path, support=support, criss_cross=False)
    code, report = repo.check(refs["b_before_sync"], refs["a"])
    assert code == 1
    assert files(report, "E") >= {support}


def test_relative_diff_config_and_subdirectory_do_not_hide_loss(tmp_path: Path) -> None:
    repo, refs = incident(tmp_path, criss_cross=False)
    root_code, root_report = repo.check(refs["b_before_sync"], refs["a"])
    repo.git("config", "diff.relative", "true")
    code, report = repo.check(refs["b_before_sync"], refs["a"], cwd=repo.path / "app")
    assert (code, report["findings"]) == (root_code, root_report["findings"])
    assert code == 1
    assert {"app/support.txt", "app/collection.txt"} <= files(report, "E")


def test_merge_deleting_a_file_both_parents_have_is_dropped(tmp_path: Path) -> None:
    repo = Repo(tmp_path / "del")
    repo.write("keep.txt", "k\n")
    repo.write("img.bin", b"\x00\x01binary\x00" * 20)
    repo.commit("base")
    repo.git("checkout", "-q", "-b", "feature")
    repo.write("f.txt", "feature\n")
    repo.commit("feat")
    repo.git("checkout", "-q", "master")
    repo.write("m.txt", "main\n")
    repo.commit("main")
    repo.git("checkout", "-q", "feature")
    repo.git("merge", "-q", "--no-ff", "--no-commit", "master")
    repo.git("rm", "-q", "img.bin", "keep.txt")
    repo.commit("sync master and silently drop a binary and a text file")
    code, report = repo.check("master", "feature")
    assert code == 1
    assert files(report, "B") == {"img.bin", "keep.txt"}
    assert {"img.bin", "keep.txt"} <= files(report, "D")


def test_octopus_merge_is_unverified_not_passed(tmp_path: Path) -> None:
    repo = Repo(tmp_path / "oct")
    repo.write("a.txt", "a\n")
    repo.commit("base")
    for name in ("x", "y"):
        repo.git("checkout", "-q", "-b", name, "master")
        repo.write(f"{name}.txt", f"{name}\n")
        repo.commit(name)
    repo.git("checkout", "-q", "-b", "feature", "master")
    repo.write("f.txt", "f\n")
    repo.commit("f")
    repo.git("merge", "-q", "-m", "octopus", "x", "y")
    code, report = repo.check("master", "feature")
    assert code == 2
    assert "UNVERIFIED" in levels(report)["B"]


# ---- false positives found in review -----------------------------------------------------


def test_regular_feature_merge_passes(tmp_path: Path) -> None:
    repo = Repo(tmp_path / "clean", branch="main")
    repo.write("a.txt", "one\ntwo\n")
    repo.commit("base")
    repo.git("checkout", "-q", "-b", "feature")
    repo.write("a.txt", "one\n")  # an authored deletion is fine
    repo.write("b.txt", "new\n")
    repo.commit("feat: change")
    repo.git("checkout", "-q", "main")
    repo.write("c.txt", "main\n")
    repo.commit("main moves on")
    repo.git("checkout", "-q", "feature")
    repo.git("merge", "-q", "--no-ff", "-m", "sync main", "main")
    code, report = repo.check("main", "feature")
    assert code == 0, report
    assert {lv for lvs in levels(report).values() for lv in lvs} == {"OK"}


def test_authored_deletion_from_a_subdirectory_passes(tmp_path: Path) -> None:
    """Pathspecs must resolve from the repository root, not the caller's directory."""
    repo = Repo(tmp_path / "subdir")
    repo.write("pkg/a.txt", "one\ntwo\nthree\n")
    repo.write("pkg/other/b.txt", "b\n")
    repo.commit("base")
    repo.git("checkout", "-q", "-b", "feature")
    repo.write("pkg/a.txt", "one\n")
    repo.commit("feat: trim a")
    repo.git("config", "diff.relative", "true")
    code, report = repo.check("master", "feature", cwd=repo.path / "pkg" / "other")
    assert code == 0, report


def test_source_rename_with_deleted_lines_passes(tmp_path: Path) -> None:
    repo = Repo(tmp_path / "rename")
    repo.write("old.txt", "".join(f"line {i}\n" for i in range(30)))
    repo.commit("base")
    repo.git("checkout", "-q", "-b", "feature")
    repo.git("mv", "old.txt", "new.txt")
    repo.write("new.txt", "".join(f"line {i}\n" for i in range(20)))
    repo.commit("rename and trim")
    code, report = repo.check("master", "feature")
    assert code == 0, report


def test_target_rename_with_source_edit_passes(tmp_path: Path) -> None:
    repo = Repo(tmp_path / "trename")
    repo.write("X.java", "".join(f"class line {i}\n" for i in range(30)))
    repo.commit("base")
    repo.git("checkout", "-q", "-b", "feature")
    repo.write("X.java", "".join(f"class line {i}\n" for i in range(30)) + "source edit\n")
    repo.commit("edit X")
    repo.git("checkout", "-q", "master")
    repo.git("mv", "X.java", "Y.java")
    repo.commit("rename X to Y")
    code, report = repo.check("master", "feature")
    assert "C" not in fails(report) and "D" not in fails(report), report


def test_criss_cross_then_source_continues_passes(tmp_path: Path) -> None:
    repo = Repo(tmp_path / "cc")
    repo.write("f.txt", "v1\n")
    repo.write("g.txt", "g\n")
    repo.commit("base")
    repo.git("checkout", "-q", "-b", "a")
    repo.write("f.txt", "v2\n")
    repo.commit("a: v2")
    repo.git("checkout", "-q", "-b", "b", "master")
    repo.write("g.txt", "g2\n")
    repo.commit("b: g2")
    a_tip, b_tip = repo.rev("a"), repo.rev("b")
    repo.git("merge", "-q", "--no-ff", "-m", "b <- a", a_tip)
    repo.git("checkout", "-q", "a")
    repo.git("merge", "-q", "--no-ff", "-m", "a <- b", b_tip)
    repo.write("f.txt", "v3\n")
    repo.commit("a: v3")
    code, report = repo.check("b", "a")
    assert code == 0, report
    assert "FAIL" not in levels(report)["A"]


def test_dropping_a_third_party_change_with_ours_is_not_attributed_to_source(tmp_path: Path) -> None:
    repo = Repo(tmp_path / "ours")
    repo.write("f.txt", "v1\n")
    repo.commit("base")
    repo.git("checkout", "-q", "-b", "source")
    repo.write("s.txt", "s\n")
    repo.commit("source work")
    repo.git("checkout", "-q", "-b", "x", "master")
    repo.write("f.txt", "x change\n")
    repo.commit("x change")
    repo.git("checkout", "-q", "master")
    repo.git("merge", "-q", "-s", "ours", "-m", "drop x on purpose", "x")
    repo.write("f.txt", "target change\n")
    repo.commit("target edits f")
    code, report = repo.check("master", "source")
    assert "E" not in fails(report), report


def test_conflict_files_are_not_reported_as_silent(tmp_path: Path) -> None:
    repo = Repo(tmp_path / "conflict")
    repo.write("f.txt", "base\n")
    repo.commit("base")
    repo.git("checkout", "-q", "-b", "feature")
    repo.write("f.txt", "feature\n")
    repo.commit("feature edits f")
    repo.git("checkout", "-q", "master")
    repo.write("f.txt", "master\n")
    repo.commit("master edits f")
    code, report = repo.check("master", "feature")
    assert code == 0, report
    assert all(not f["files"] or "f.txt" not in f["files"] for f in report["findings"] if f["level"] == "FAIL")


def test_hand_resolved_conflict_inside_source_merge_is_not_a_drop(tmp_path: Path) -> None:
    repo = Repo(tmp_path / "resolved")
    repo.write("f.txt", "base\n")
    repo.commit("base")
    repo.git("checkout", "-q", "-b", "feature")
    repo.write("f.txt", "feature\n")
    repo.commit("feature edits f")
    repo.git("checkout", "-q", "master")
    repo.write("f.txt", "master\n")
    repo.commit("master edits f")
    repo.git("checkout", "-q", "feature")
    subprocess.run(["git", "merge", "-q", "master"], cwd=repo.path, capture_output=True,
                   env={**os.environ, **ISOLATED})
    repo.write("f.txt", "master\n")  # resolve the conflict by taking master's side
    repo.commit("merge master, resolve f")
    code, report = repo.check("master", "feature")
    assert code == 0, report
    assert files(report, "C", "WARN") == {"f.txt"}  # the hand resolution is surfaced for review


def test_edit_inside_merge_is_only_a_warning(tmp_path: Path) -> None:
    repo = Repo(tmp_path / "edit", branch="main")
    repo.write("文档.md", "a\n")
    repo.write("x.txt", "x\n")
    repo.commit("base")
    repo.git("checkout", "-q", "-b", "feature")
    repo.write("x.txt", "x\nfeature\n")
    repo.commit("feat")
    repo.git("checkout", "-q", "main")
    repo.write("y.txt", "y\n")
    repo.commit("main")
    repo.git("checkout", "-q", "feature")
    repo.git("merge", "-q", "--no-ff", "--no-commit", "main")
    repo.write("文档.md", "a\nnote added during merge\n")
    repo.commit("merge main and touch doc")
    code, report = repo.check("main", "feature")
    assert code == 0
    assert levels(report)["B"] == {"WARN"}
    assert files(report, "B", "WARN") == {"文档.md"}


# ---- exit codes ----------------------------------------------------------------------------


@pytest.mark.parametrize("as_json", [False, True])
def test_ascii_only_stdout_does_not_crash_on_non_ascii_paths(tmp_path: Path, as_json: bool) -> None:
    repo, refs = incident(tmp_path, support="app/支持.txt", criss_cross=False)
    result = subprocess.run([sys.executable, str(SCRIPT), refs["b_before_sync"], refs["a"], *(["--json"] * as_json)],
                            cwd=repo.path, capture_output=True, env={**os.environ, **ISOLATED, "PYTHONIOENCODING": "ascii"})
    assert result.returncode == 1
    assert b"Traceback" not in result.stderr
    if as_json:
        assert "app/支持.txt" in files(json.loads(result.stdout), "E")


def test_bad_ref_exits_2_with_reason(tmp_path: Path) -> None:
    repo = Repo(tmp_path / "bad")
    repo.write("a.txt", "a\n")
    repo.commit("base")
    result = subprocess.run([sys.executable, str(SCRIPT), "nope", "HEAD"], cwd=repo.path, text=True,
                            capture_output=True, env={**os.environ, **ISOLATED})
    assert result.returncode == 2
    assert "unknown commit: nope" in result.stderr


def test_outside_a_repository_exits_2(tmp_path: Path) -> None:
    result = subprocess.run([sys.executable, str(SCRIPT), "HEAD", "HEAD"], cwd=tmp_path, text=True,
                            capture_output=True, env={**os.environ, **ISOLATED, "GIT_CEILING_DIRECTORIES": str(tmp_path)})
    assert result.returncode == 2
    assert "not inside a git work tree" in result.stderr


def test_missing_git_binary_exits_2(tmp_path: Path) -> None:
    result = subprocess.run([sys.executable, str(SCRIPT), "HEAD", "HEAD"], cwd=tmp_path, text=True,
                            capture_output=True, env={**os.environ, "PATH": str(tmp_path)})
    assert result.returncode == 2
    assert "could not complete" in result.stderr
