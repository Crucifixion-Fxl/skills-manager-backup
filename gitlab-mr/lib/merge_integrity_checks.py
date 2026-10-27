"""check_merge_integrity 的 A–E 检查与报告结构。判据说明见脚本 docstring 与 reference/merge-integrity.md。"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from merge_integrity_git import (
    GitError, _decode, authored, blob, changed, content_lines, describe, git, git_bytes, renames, rev, short,
    simulate,
)

CHECKS = ("A", "B", "C", "D", "E")


@dataclass
class Report:
    target: str
    source: str
    target_sha: str
    source_sha: str
    findings: list[dict] = field(default_factory=list)

    def add(self, check: str, level: str, message: str, files: list[str] | None = None, **extra: object) -> None:
        self.findings.append(
            {"check": check, "level": level, "message": message, "files": sorted(files or []), **extra}
        )

    def ok_unless_reported(self, check: str, message: str) -> None:
        if not any(f["check"] == check for f in self.findings):
            self.add(check, "OK", message)

    @property
    def failed(self) -> bool:
        return any(f["level"] == "FAIL" for f in self.findings)

    @property
    def unverified(self) -> bool:
        return any(f["level"] == "UNVERIFIED" for f in self.findings)

    def exit_code(self) -> int:
        return 1 if self.failed else 2 if self.unverified else 0


@dataclass
class Drop:
    """Content an automatic merge would keep but the recorded merge does not."""

    path: str
    lines: Counter          # dropped text lines (empty for binary)
    binary_oid: str | None  # auto-merge object id when the file is binary
    added_only: bool        # the merge only added lines on top of the automatic result
    reset: bool             # deleted, binary replaced, or reset to one parent's version


def two_parents(merge: str) -> tuple[str, str] | None:
    parents = git("log", "-1", "--format=%P", merge).split()
    return (parents[0], parents[1]) if len(parents) == 2 else None


def merge_drops(merge: str, parents: tuple[str, str]) -> tuple[str, list[Drop]]:
    """Compare a recorded merge with its automatic replay outside conflict files; returns (auto tree, drops)."""
    auto, conflicts = simulate(*parents)
    sides = (*parents, *git("merge-base", "--all", *parents).split())
    drops: list[Drop] = []
    for path in sorted(changed(auto, merge) - conflicts):
        a_oid, m_oid = blob(auto, path), blob(merge, path)
        if a_oid == "-":
            continue  # the merge introduced a file the replay does not have: an addition
        a_lines, m_lines = content_lines(a_oid), content_lines(m_oid)
        if a_lines is None or (m_lines is None and m_oid != "-"):
            drops.append(Drop(path, Counter(), a_oid, False, True))
            continue
        lost = Counter(a_lines) - Counter(m_lines or ())
        reset = m_oid == "-" or m_oid in {blob(p, path) for p in sides}  # a parent's or a base's version
        drops.append(Drop(path, lost, None, not lost, reset))
    return auto, drops


def present(lines: Counter, ref: str, paths: set[str], binary_oid: str | None) -> bool:
    """True when ref holds the dropped content at one of the candidate paths."""
    for path in paths:
        oid = blob(ref, path)
        if binary_oid is not None:
            if oid == binary_oid:
                return True
            continue
        have = content_lines(oid)
        if have is not None and not (lines - Counter(have)):
            return True
    return False


def check_a(report: Report, t: str, s: str, bases: list[str], tree: str, conflicts: set[str],
            inner: str = "") -> None:
    """inner: the merge commit being re-checked as its own merge (empty for target <- source)."""
    if len(bases) == 1:
        if not inner:
            report.add("A", "OK", f"single merge base {short(bases[0])}")
        return
    if not inner:
        report.add("A", "WARN", f"{len(bases)} merge bases (criss-cross): " + "; ".join(describe(b) for b in bases))
    where = f"inside {describe(inner)}: " if inner else ""
    fail: dict[tuple[str, str], list[str]] = {}
    warn: set[str] = set()
    for old in bases:
        for new in bases:
            if old == new:
                continue
            for path in sorted(changed(old, new) - conflicts):
                if not (blob(t, path) == blob(old, path) and blob(tree, path) == blob(t, path) != blob(s, path)):
                    continue
                if authored(f"{old}..{t}", path):
                    warn.add(path)
                else:
                    fail.setdefault((old, new), []).append(path)
    for (old, new), paths in fail.items():
        report.add(
            "A", "FAIL",
            f"{where}{len(paths)} non-conflict file(s) stay at the target's base {short(old)} although base {short(new)} and "
            f"the source are newer; find the merge that brought {short(new)} into the target without its content",
            paths, old_base=short(old), new_base=short(new), **({"commit": short(inner)} if inner else {}),
        )
    if warn:
        report.add("A", "WARN", f"{where}{len(warn)} file(s) kept at a version the target changed on purpose (non-merge "
                   "commit); confirm the revert is intended", sorted(warn))
    if not fail and not inner:
        report.add("A", "OK", "no non-conflict file is silently reverted by the extra merge base(s)")


def check_b(report: Report) -> None:
    merges = git("rev-list", "--merges", f"{report.target_sha}..{report.source_sha}").split()
    for merge in merges:
        parents = two_parents(merge)
        if parents is None:
            report.add("B", "UNVERIFIED", f"octopus merge {describe(merge)} cannot be replayed", commit=short(merge))
            continue
        _, drops = merge_drops(merge, parents)
        reset = [d.path for d in drops if d.reset]
        trimmed = [d for d in drops if not d.reset and not d.added_only]
        edited = [d.path for d in drops if d.added_only]
        if reset:
            report.add("B", "FAIL", f"{describe(merge)}: {len(reset)} non-conflict file(s) reset to one parent, deleted "
                       "or replaced (binary) although the automatic merge keeps both sides", reset, commit=short(merge))
        if trimmed:
            lines = sum(sum(d.lines.values()) for d in trimmed)
            report.add("B", "WARN", f"{describe(merge)}: {len(trimmed)} non-conflict file(s) lose {lines} line(s) of "
                       "the automatic merge inside the merge commit; confirm each removal is intended",
                       [d.path for d in trimmed], commit=short(merge), lost_lines=lines)
        if edited:
            report.add("B", "WARN", f"{describe(merge)}: {len(edited)} non-conflict file(s) only gained lines inside "
                       "the merge; prefer a separate commit", edited, commit=short(merge))
    report.ok_unless_reported("B", f"{len(merges)} merge commit(s) in target..source keep the automatic merge content")


def check_c(report: Report, t: str, s: str, bases: list[str], tree: str, conflicts: set[str],
            inner: str = "") -> None:
    where, extra = (f"inside {describe(inner)}: ", {"commit": short(inner)}) if inner else ("", {})
    lost: dict[str, set[str]] = {}
    for base in bases:
        for p in sorted((changed(base, s) - changed(base, t)) - conflicts):
            if blob(tree, p) == blob(t, p) and blob(s, p) != blob(t, p):
                lost.setdefault(p, set()).add(base)
    fail, warn = [], []
    for path, path_bases in sorted(lost.items()):
        # The target changed the file on purpose since another base: a deliberate revert, not a silent loss.
        (warn if any(authored(f"{b}..{t}", path) for b in bases if b not in path_bases) else fail).append(path)
    if fail:
        report.add("C", "FAIL", f"{where}{len(fail)} source-only file(s) stay at the target's version", fail, **extra)
    if warn:
        report.add("C", "WARN", f"{where}{len(warn)} file(s) stay at a version the target changed on purpose; "
                   "confirm", warn, **extra)
    if not inner:
        report.ok_unless_reported("C", f"all source-only files arrive; conflicts: {len(conflicts)}")


def check_inner_merges(report: Report) -> None:
    """Re-check every merge in target..source as its own merge, in both directions (M^1 <-> M^2 = M).

    A pre-merged sync branch (the usual way to submit a conflicting release sync) has the target as merge base, so
    the outer A/C see nothing; the silent revert lives inside the branch's merge commit.
    """
    rng = f"{report.target_sha}..{report.source_sha}"
    first_parent = set(git("rev-list", "--first-parent", "--merges", rng).split())
    other_conflicts: set[str] = set()
    others = 0
    for merge in git("rev-list", "--merges", rng).split():
        parents = two_parents(merge)
        if parents is None:
            continue  # reported as UNVERIFIED by B
        bases = git("merge-base", "--all", *parents).split()
        if not bases:
            continue
        _, conflicts = simulate(*parents)
        if conflicts and merge in first_parent:  # resolved by hand on the branch: the outer merge shows no conflict
            report.add("C", "WARN", f"inside {describe(merge)}: {len(conflicts)} conflict file(s) were resolved by "
                       "hand before this MR; review each resolution", sorted(conflicts), commit=short(merge))
        elif conflicts:
            others += 1
            other_conflicts |= conflicts
        # Either side may be the one losing content: syncs are branched from the target or from the source.
        for ours, theirs in (parents, parents[::-1]):
            check_a(report, ours, theirs, bases, merge, conflicts, inner=merge)
            check_c(report, ours, theirs, bases, merge, conflicts, inner=merge)
            if merge in first_parent:  # a pre-merged sync already contains the target, so the outer E sees nothing
                check_e(report, ours, theirs, bases, merge, inner=merge)
    if others:
        report.add("C", "INFO", f"{len(other_conflicts)} conflict file(s) were resolved inside {others} earlier "
                   "merge(s) of the source history (already reviewed in their own MRs)", sorted(other_conflicts))


def check_d(report: Report, bases: list[str], tree: str, conflicts: set[str]) -> None:
    t, s = report.target_sha, report.source_sha
    shrinking: list[str] = []
    for item in git_bytes("diff", "--no-renames", "--no-ext-diff", "--numstat", "-z", t, tree)[1].split(b"\0"):
        if not item:
            continue
        added, deleted, path = _decode(item).split("\t", 2)
        if path in conflicts:
            continue
        if added == "-":  # binary: count a removed file as a deletion
            if blob(tree, path) == "-" and blob(t, path) != "-":
                shrinking.append(path)
        elif int(deleted) > int(added):
            shrinking.append(path)
    merge_only = [p for p in shrinking if not any(authored(f"{b}..{s}", p) for b in bases)]
    if merge_only:
        report.add("D", "FAIL", f"{len(merge_only)} file(s) would lose content in the target although no non-merge "
                   "commit in the source changed them", merge_only)
    report.ok_unless_reported("D", "no removal in the target comes only from merge commits")


def check_e(report: Report, t: str, s: str, bases: list[str], tree: str, inner: str = "") -> None:
    where = f"inside {describe(inner)} (as its own merge): " if inner else ""
    merges = git("rev-list", "--merges", f"{s}..{t}").split()
    for merge in merges:
        parents = two_parents(merge)
        if parents is None:
            report.add("E", "UNVERIFIED", f"{where}octopus merge {describe(merge)} in the target cannot be replayed",
                       commit=short(merge))
            continue
        auto, drops = merge_drops(merge, parents)
        # E is line-level: any dropped line the source still has. Inside a merge only whole-file resets count;
        # partial removals there are usually adaptation edits (see B).
        drops = [d for d in drops if not d.added_only and (d.reset or not inner)]
        if not drops:
            continue
        # Follow renames from the automatic result, which still holds the dropped content.
        to_source, to_tree = renames(auto, s), renames(auto, tree)
        own, latent = [], []
        for d in drops:
            if not present(d.lines, s, {d.path} | to_source.get(d.path, set()), d.binary_oid):
                continue  # the source does not carry the dropped content either
            if present(d.lines, tree, {d.path} | to_tree.get(d.path, set()), d.binary_oid):
                continue  # already restored
            names = {d.path} | to_source.get(d.path, set())
            (own if any(p in changed(b, s) for b in bases for p in names) else latent).append(d.path)
        if own:
            report.add("E", "FAIL", f"{where}{describe(merge)} in the target dropped content of {len(own)} file(s) that the "
                       "source carries; this merge will not bring it back (restore it with new commits)",
                       own, commit=short(merge), origin="source")
        if latent:
            report.add("E", "FAIL", f"{where}{describe(merge)} in the target already lost content of {len(latent)} file(s) "
                       "the source still carries (not introduced by this MR); restore it in the target or get a "
                       "signed waiver", latent, commit=short(merge), origin="target-history")
    if not inner:
        report.ok_unless_reported("E", f"{len(merges)} target-only merge commit(s) dropped nothing the source carries")


def run(target: str, source: str) -> Report:
    report = Report(target, source, rev(target), rev(source))
    bases = git("merge-base", "--all", report.target_sha, report.source_sha).split()
    if not bases:
        raise GitError(f"{target} and {source} have no common history (merge base)")
    tree, conflicts = simulate(report.target_sha, report.source_sha)
    t, s = report.target_sha, report.source_sha
    check_a(report, t, s, bases, tree, conflicts)
    check_b(report)
    check_c(report, t, s, bases, tree, conflicts)
    check_inner_merges(report)
    check_d(report, bases, tree, conflicts)
    check_e(report, t, s, bases, tree)
    report.findings.sort(key=lambda f: (CHECKS.index(f["check"]), f["level"] == "OK"))
    return report
