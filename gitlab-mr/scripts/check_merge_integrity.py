#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# ///
"""合并内容完整性检查：发现 target <- source 合并中会被静默丢失的内容。

基于 `git merge-tree --write-tree`（Git >= 2.38）重放合并。不写 ref、index 或工作区；
merge-tree 会在对象库中留下可被 gc 回收的 loose object。始终在仓库顶层运行，关闭
quotePath / relative / renames，路径按 NUL 分隔解析，因此中文、Tab、子目录均可。

退出码：0 未发现问题；1 发现完整性问题（停止并升级给用户）；2 无法完成检查
（参数、环境、octopus 合并等），不得视为通过。

检查项（按 A–E 输出）：
  A 交叉合并：多个 merge-base 时，非冲突文件在合并结果里停在 target 某个 base 的旧版本，
    而另一个 base 与 source 已是新内容。target 有非合并提交改过该文件时降为 WARN（可能是
    刻意回退，需人工确认）。
  B source 范围内的 evil merge：target..source 中的合并提交相对自动合并结果，把非冲突文件
    整份重置成父提交或 merge-base 版本、删除或换了二进制 -> FAIL；部分删行或只新增行 -> WARN。
  C 内容未送达：source 自某个 merge-base 后改过、target 自该 base 未改过的非冲突文件，
    合并结果仍是 target 版本 -> FAIL；target 自另一个 base 有非合并提交改过时降为 WARN。
  D 只由合并带来的删除：合并会让 target 减少行数或删除文件（含二进制），而 base..source
    中没有任何非合并提交改过该文件 -> FAIL。
  E target 历史中的 evil merge：source..target 中的合并相对自动合并丢掉的行，source 仍持有
    （按改名跟踪）而本次合并结果里没有 -> FAIL（只能用新提交恢复）；按 source 自 base 是否改过
    该路径区分"本 MR 相关"与"target 历史已丢失"。source 不持有被丢弃内容（如 `-s ours`）时不计入。
  合并内部复核：target..source 中每个双父合并 M 按两个方向（M^1 <-> M^2，结果 M）再跑 A / C；
    first-parent 上的 M 还跑 E（只计整份重置），并以 C WARN 列出其冲突文件，其余合并的冲突汇总为 INFO。
    覆盖预先做好合并再提交的合流分支。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from merge_integrity_checks import Report, run, short  # noqa: E402
from merge_integrity_git import GitError, _decode, git_bytes, git_version, is_shallow, rev, set_root  # noqa: E402
from merge_integrity_sync import sync_parents  # noqa: E402

MIN_GIT = (2, 38)


def print_text(report: Report, limit: int) -> None:
    print(f"== merge integrity: {report.target} ({short(report.target_sha)}) <- {report.source} ({short(report.source_sha)})")
    for finding in report.findings:
        print(f"{finding['check']}  {finding['level']:<10} {finding['message']}")
        files = finding["files"] if finding["level"] != "OK" else []
        for path in files[:limit]:
            print(f"          {path}")
        if len(files) > limit:
            print(f"          ... ({len(files) - limit} more)")
    print(f"exit {report.exit_code()}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("target", help="目标分支 ref 或 SHA，例如 origin/release/20261022")
    parser.add_argument("source", help="源 ref 或 SHA，例如 HEAD 或 MR head SHA")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--limit", type=int, default=20, help="文本输出中每项最多列出的文件数")
    parser.add_argument("--sync-parents", action="store_true",
                        help="只列出还需单独检查的、合入的其他长期分支提交（需要 origin 与长期分支已 fetch）")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(errors="backslashreplace")
    try:
        if git_version() < MIN_GIT:
            print("git >= 2.38 required (merge-tree --write-tree)", file=sys.stderr)
            return 2
        code, out = git_bytes("rev-parse", "--show-toplevel", check=False)
        if code != 0:
            print("not inside a git work tree", file=sys.stderr)
            return 2
        set_root(_decode(out).strip())
        if is_shallow():
            print("shallow clone: merge bases and history are incomplete; run `git fetch --unshallow`", file=sys.stderr)
            return 2
        if args.sync_parents:
            for sha in sync_parents(rev(args.target), rev(args.source), GitError):
                print(sha)
            return 0
        report = run(args.target, args.source)
    except GitError as err:
        print(err, file=sys.stderr)
        return 2
    except Exception as err:  # noqa: BLE001 - any unexpected failure is "check not completed", never "problem found"
        print(f"merge integrity check could not complete: {type(err).__name__}: {err}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({
            "target": report.target, "source": report.source,
            "target_sha": report.target_sha, "source_sha": report.source_sha,
            "exit_code": report.exit_code(), "failed": report.failed, "unverified": report.unverified,
            "findings": report.findings,
        }, ensure_ascii=True, indent=2))
    else:
        print_text(report, args.limit)
    return report.exit_code()


if __name__ == "__main__":
    sys.exit(main())
