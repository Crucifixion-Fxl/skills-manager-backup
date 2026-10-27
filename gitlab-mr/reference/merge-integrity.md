# 合并内容完整性检查

`scripts/check_merge_integrity.py` 发现 `target <- source` 合并中会被**静默**丢失的内容。它补的是
release parity 之外的一类问题：commit 都在历史里、CI 全绿、没有冲突提示，但文件内容没进来。

```bash
git fetch origin "+refs/heads/$TARGET_BRANCH:refs/remotes/origin/$TARGET_BRANCH"
uv run <skill-path>/scripts/check_merge_integrity.py "origin/$TARGET_BRANCH" HEAD           # 本地候选
uv run <skill-path>/scripts/check_merge_integrity.py <target_sha> <mr_head_sha> --json     # Auditor
# 合入的其他长期分支提交：只 fetch 长期分支，再列出需要单独检查的提交
git fetch origin $(git ls-remote --heads origin | awk '{print $2}' \
  | grep -E '^refs/heads/(main|master|staging|release/.+|hotfix/.+)$' | sed 's#^refs/heads/\(.*\)$#+&:refs/remotes/origin/\1#')
uv run <skill-path>/scripts/check_merge_integrity.py <target_sha> <mr_head_sha> --sync-parents
```

fetch 之后又有新的长期分支 push 时会报 "not available locally"（exit 2），重新执行上面的 fetch。

- 基于 `git merge-tree --write-tree`（Git ≥ 2.38）重放合并，不写 ref、index 或工作区；会在对象库留下可被 gc 回收的 loose object。
- 始终在仓库顶层运行，关闭 quotePath / diff.relative / 差异比较里的改名检测（合并重放本身保留 Git 默认的改名检测），路径按 NUL 分隔：中文、Tab、从子目录调用都不影响结论。
- 实测耗时：CLOUD/iot-service-unified 普通 MR 0.15–0.5s（含数十个合并的 release→master 约 15s），release 合流 10–15s，含数百个合并的长期分支互合约 1 分钟。
- 退出码：`0` 未发现问题；`1` 有 FAIL，停止 push / MR 写入并交给用户；`2` 无法完成检查（Git 版本、浅克隆、坏 ref、非仓库目录、octopus 合并、意外异常），不得视为通过。

## 何时必跑（Rule 14）

满足任一即适用，判定是机械的：

1. 目标分支是 `main` / `master` / `staging` / `release/*` / `hotfix/*`；
2. `git rev-list --merges origin/<target>..HEAD` 非空（候选里有合并提交，包括 retarget 与手工冲突解决）。

`validate_drive_audit.py` 会自行重算这两条，`applicable: false` 只在两条都不满足时被接受。

时机：push 前、创建或更新 MR 前（含 `glab mr update --target-branch`，先对新目标跑）、Driver 每次 push 前、Auditor 完工审计时。候选 HEAD 或目标 SHA 变化即重跑。

## 检查项

输出按 A–E 排列，级别为 `FAIL` / `UNVERIFIED` / `WARN` / `INFO` / `OK`。B–E 有 WARN / FAIL / UNVERIFIED 时不再输出该项的 OK 行；A 在多个 merge-base 时总有一条列出各 base 的 WARN，其后仍给出 FAIL 或 OK 结论。MR 描述附这些结论行。

| 检查 | FAIL 含义 | 处理 |
|---|---|---|
| **A** 交叉合并 | 多个 merge-base；非冲突文件在合并结果里停在 target 某个 base 的旧版本，另一个 base 与 source 已是新内容 | 先在 target 上用普通提交补回这些文件，再重做合流（不要在合流合并里 `checkout <ref> --` 取回，会被 B / 反向 A 判 FAIL）；target 有非合并提交改过该文件时只报 WARN（可能是刻意回退） |
| **B** source 范围内的 evil merge | `target..source` 的合并提交相对自动合并结果，把非冲突文件整份重置成一侧父提交或该合并的 merge-base 版本、删除，或替换了二进制 | 重做该合并（只改冲突文件），或按下文换目标 |
| **C** 内容未送达 | source 自某个 merge-base 后改过、target 自该 base 未改过的非冲突文件，合并后仍是 target 版本 | 同 A；target 自另一个 base 有非合并提交改过该文件时降为 WARN |
| **D** 只由合并带来的删除 | 合并会让 target 减少行数或删除文件（含二进制），而 base..source 中没有任何非合并提交改过该文件 | 删除来自早先 evil merge 的扩散（例如 release 合回 master）；先在 source 用恢复提交补回再合并 |
| **E** target 历史里的 evil merge | `source..target` 的合并相对自动合并丢掉的行（二进制按整份比较），source 仍持有（跟踪改名），本次合并结果里却没有 | 用新的恢复提交补回；重新合流不会带回 |

- `B WARN`：合并提交里手工删了部分行（报告 `lost_lines`）或只新增了行。逐个确认删除是有意的，建议改为合并后的普通 commit。
- `E FAIL` 的 `origin`：`source` 表示 source 自 merge-base 后改过该路径（与本 MR 相关）；`target-history` 表示 target 早已丢失、source 只是仍持有旧内容（不是本 MR 引入，但合并后依旧缺失）。两种都要处理或豁免。
- 补回后即使 target 文件多了别的行，只要被丢的行都在，E 即为 OK。
- 合并内部复核：`target..source` 里的每个双父合并 M 还会被当作一次独立合并（`M^1 <- M^2` 与 `M^2 <- M^1` 两个方向，结果 `M`）再跑 A / C，结论带 `inside <M>` 和 `commit`。预先做好合并再提交的合流分支（有冲突的 release 合流在 GitLab 上只能这样提：从 target 切出再合 source，如 !3614；或按 GitLab "本地解决冲突"从 source 切出再合 target）里，外层合并基本是快进，A / C 看不到问题，丢失只在这个合并内部。first-parent 上的 M 还会对其内部跑 E（只计整份重置 / 删除 / 二进制替换，部分删行见 B），因为预合并分支已包含 target，外层 E 无可查范围；其冲突文件（外层不会再显示为冲突）以 `C WARN` 列出，逐个复核并附进 MR 描述。非 first-parent 合并的冲突已在各自 MR 中审过，只汇总为一条 `C INFO`。反向复核的提示中 "target" 指该方向上的 ours 侧父提交。
- `UNVERIFIED`：octopus 合并（source 或 target 侧）无法重放，整体 exit 2。

## 豁免

FAIL 是有意为之（例如刻意回退）时，只能由用户确认后，由父会话写入 Driver state 的 `merge_integrity_waivers`：

```json
{"head_sha": "<MR HEAD>", "target_sha": "<目标 SHA>", "checks": ["A", "C"], "files": ["<FAIL 列出的全部文件>"],
 "approved_by": "<用户>", "approved_at_utc": "2026-10-27T03:00:00Z", "reason": "<为什么这是有意的>"}
```

`validate_drive_audit.py` 只接受绑定同一 HEAD / 目标 SHA、`checks` / `files` 为非空字符串数组且覆盖全部 FAIL、签字字段非空、`approved_at_utc` 为 UTC ISO-8601 的豁免；HEAD 或目标变化后豁免失效。同一完整性 FAIL 被 Auditor 连续拒绝 2 次（state `merge_integrity_rejections`）时停止重派，按 `stuck` 交给用户。

## Auditor 证据与校验

Auditor 在 MR 所在仓库内 fetch 目标分支与 `refs/merge-requests/<iid>/head`，分别 `rev-parse` 得到两个 SHA，报告保存为
`/tmp/gitlab-mr-integrity-<project>-<iid>-<head12>.json`（细节见 [background-drive.md](background-drive.md) 3.5）。
`validate_drive_audit.py` 也必须在该仓库内运行：它用 `git ls-remote origin` 核对目标 SHA 仍是远端分支当前值、
要求两个提交在本地可用，核对报告 hash、SHA 绑定、A–E 齐全、`exit_code` / `failed` / `unverified` 与结论一致，
再对同一对 SHA 重跑检查并逐条比较（check / level / files），不一致即拒绝；`--sync-parents` 列出的提交必须各有一份 `sync_reports`，同样校验与重跑（需要已 fetch 全部长期分支）。

## 禁止的操作与 Driver 边界

- 用"把新目标分支 merge 进旧基线分支，再把文件树换成新目标的版本"来换目标分支。
- 对非冲突文件执行 `git checkout --ours/--theirs`、`git checkout <ref> -- <path>`、整目录覆盖、`git read-tree` 或 `git merge -s ours`。
- source 是共享长期分支（release 合流 MR）时 rebase 或强推它；Driver 只做校验，不做手工合并、不改 target。

换目标分支：

```bash
# 分支里没有合并提交：rebase --onto
git rebase --onto "origin/$NEW_TARGET" "$(git merge-base HEAD "origin/$OLD_TARGET")"
# 分支里已有（可能坏的）合并提交：在新目标上新建分支，只 cherry-pick 本 MR 自己的非合并提交
git switch -c "<branch>-retarget" "origin/$NEW_TARGET"
git cherry-pick $(git rev-list --reverse --no-merges "<branch>" "^origin/$OLD_TARGET" "^origin/$NEW_TARGET")
uv run <skill-path>/scripts/check_merge_integrity.py "origin/$NEW_TARGET" HEAD   # exit 0
```

已开 MR 上 force-with-lease 或换源分支前需用户确认。rebase 后范围内没有合并提交，B 恒为 OK，丢失只能由 A/C/D/E 发现，所以换目标后仍要跑完整检查。

## 已知限制

- 只判断"内容是否被静默丢弃"，不判断改动在语义上是否正确；冲突文件的解决结果由 code-review 负责。
- B 对"合并提交里删了部分行"（含整份重置后又改了一点）只报 WARN：在 CLOUD/iot-service-unified 实测，正常 MR 中 11 个合并提交都含这类适配性修改（改名、调整测试、合并 contract 条目），按行删除判 FAIL 会让它们全部失败；行数、"一侧新增的行"或"一侧新增的行全部消失"都无法把它们与 evil merge 区分开。整份重置 / 删除 / 二进制替换仍是 FAIL，部分丢失若已进入 target，外层 E 在直接对比时按行发现；预合并分支内部见下文 E 的限制。
- E 对 `target-history` 也判 FAIL：这会让 target 上与本 MR 无关的历史丢失阻塞本 MR，需要先补回或豁免；这是为了不漏掉单 merge-base 和改名后的丢失而有意做的取舍。
- D 以"净行数减少或文件消失"为信号，不识别文件内被替换但行数不减的丢失；base..source 里任何非合并提交改过该文件都会让 D 放行。
- target 侧（`source..target`）的合并只由 E 检查 evil merge，不在其内部复核交叉合并；这类丢失不是本 MR 引入的，会在对应 MR 或后续合流里被发现。
- 预合并分支内部的 E 只计整份重置：target 历史里 evil merge 对"target 之后又改过的文件"造成的部分丢失可能漏掉。因此 Step 2.87 要求对 `--sync-parents` 列出的每个提交（MR first-parent 合并中、曾是某条长期分支 tip 且不在 target 中的父提交；把 target 合入 source 的合流合并也计入 source 自己那条线，只保留最新的）再跑 `<target_sha> <该提交>`；Auditor 写入 `sync_reports`，`validate_drive_audit.py` 自行重算该列表并强制，exit 1 同样阻塞。
- `validate_drive_audit.py` 的 `head_sha` 绑定 Driver 回读的 snapshot（与其他证据一致），不单独 `ls-remote` MR head ref。
- `--sync-parents` 依赖长期分支当前仍存在、且被合入的提交在它的 first-parent 链上；分支已删除或提交只在其侧支上时不会要求 sync 报告。豁免按 HEAD / 目标 SHA 绑定，不单独绑定 `merged_sha`。
- 改名跟踪依赖 Git 相似度检测，内容很短或改动很大的文件可能跟不上，此时按原路径判断。
- 路径含通配符字符时依赖 `--literal-pathspecs`，目前没有专门的回归用例。
- `hotfix/*` 与 release 合流类 MR 在 `mr_mode` 中没有独立分类，仍按目标分支走现有模式。

## 事故复盘（2026-10，CLOUD/iot-service-unified）

1. 10-09～10-12：6 个 MR（!3479 / !3094 / !3474 / !3472 / !3473 / !3475；!3472 含同需求早期 !3456）合入 `release/20261015`（合并点 `569985a71c`）。
2. 10-14：`release/20261022` 从 master `0f3a7a659c` 切出，不含上述 MR。
3. 10-20：基于 `569985a71c` 的功能分支执行 retarget 合并 `4577966ec5`：把 1022 merge 进来后把文件树换成 1022，只留 3 个对讲文件。随 !3499 合入 1022 后，1022 的**历史**包含 1015@10-12，**内容**却没有那 6 个 MR。
4. 10-21：!3614 将 1015 合入 1022，出现两个 merge-base（`0f3a7a659c`、`569985a71c`）；Git 认为这些改动"已合并且被删除"，140 个文件静默保留旧版本，冲突列表里没有它们。
5. 若再把 1022 合回 master，会把 master 上的这些改动一起删除。

本脚本的结论（实跑）：retarget 合并 `B FAIL`（153 个文件）/ `C FAIL`（144，合并内部复核）；!3614 实际提交的预合并分支 head `f231944ebd` → `A FAIL`（140）/ `C FAIL`（139），均指向其内部合并 `5424696142`；直接对比两个 release 分支时为 `A`（140）/ `C`（139）/ `E FAIL`（154，指向 `4577966ec5`）；1022→master `A` / `B`（153）/ `C` / `D FAIL`（122）。同一棵合流树、父提交顺序对调（从 source 侧做的预合并）同样 `A` / `C FAIL`。2026-09-28 以来 4 个 release/master 分支上的 43 个合并逐一重放，只有 !3614、!3499 两个事故合并 FAIL，其余 41 个 exit 0。
