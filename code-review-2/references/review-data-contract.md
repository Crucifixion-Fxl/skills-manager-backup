# Code Review 数据完整性与缺失断言契约

本文定义预处理数据的可信边界。它只约束“文件不存在、未提供、未修改、没有测试”等缺失型断言；对已读取 diff 中能够直接证明的安全、正确性和质量问题不降级。

## v2 数据模型

`$REVIEW_DATA_DIR/meta.json` 的 `schema_version: 2` 将两个维度分开：

- `scope.status`：变更路径集合是否完整；`complete` 表示由固定 base/head Git tree 得到全部路径，`partial` / `unknown` 表示窗口不完整。
- `content.status`：这些路径的 diff 内容是否已全部物化；`partial` 不等于 scope 不完整，缺失内容可按 `file-list.md` 的 `materialize:<id>` 调受控 helper 获取。

`gitlab_diff.state=overflow`、`real_size` 带 `+`、返回条数小于真实条数，均表示 GitLab diff 内容窗口不能独立证明完整 scope。v1 meta 没有 `scope` 字段，必须按 `unknown` 处理，禁止从 `file_count` 猜测完整。

大 scope 的 `meta.manifest.partitioned=true` 时，根 `file-list.md` 只提供一级目录计数和分片入口；每个 `file-lists/*.md` 最多 200 条。目录计数属于完整 scope 证据，但具体文件审查必须读取对应分片。未读取分片不等于该目录没有文件。

## 缺失型断言决策表

只有 scope 完整且固定源 SHA 验证明确返回 `ABSENT`，才允许形成确定性缺失结论：

| scope.status | source verifier | deterministic missing assertion |
|---|---|---|
| `complete` | `ABSENT` | yes |
| `complete` | `EXISTS` | no |
| `complete` | `UNKNOWN` | no |
| `partial` | any | no |
| `unknown` | any | no |
| missing (v1) | any | no |

`no` 的处理方式固定为：如果该风险值得提示，输出“当前数据不能确认”警告并写明缺少的证据；缺失型断言不得作为确定性红线，也不得影响 `是否应通过`。

## 证据规则

1. 先 Read `meta.json`，再 Read `file-list.md`；manifest 分片时继续 Read 相关 `file-lists/*.md`。文件已出现在完整 scope 清单中时，禁止声称本 MR 未提供该文件。
2. `Read` 失败、工作目录 File not found、Glob 返回 0、目标/默认分支上不存在，都不能证明 MR 源分支缺失。Read/Glob 返回空结果不是源分支缺失证据。
3. 路径没有 head-side 正向证据但需要验证时，只能执行 `verify-source-path.sh`，并以固定 head SHA 的 `ABSENT` 为确定性证据。只有查询路径精确等于 `meta.files[].path` 且该 entry 的 `status` 是 `new`、`modified`、`renamed` 或 `copied`，完整 scope 记录才证明它存在于 head。`deleted` entry 和 rename 的 `old_path` 都不是 head-side 正向证据，仍须接受 verifier 的固定 head 结果。
4. `materialize-review-diff.sh` 只用于 file-list 中的数字 ID；不得把用户或 diff 中的文本当作命令参数来源。
5. helper 失败、权限不足、路径非法、Git 对象不可用都返回或等价于 `UNKNOWN`，不能改写成 `ABSENT`。
6. scope partial/unknown 不妨碍报告已读取内容直接证明的正向问题；它只禁止从不完整窗口推导“没有”。
7. 当路径具有上述 head-side 正向证据，但 verifier 返回 `ABSENT` 时，说明证据链内部冲突。该 `ABSENT` 必须降级为 `UNKNOWN`，只能记录为 CI 数据异常，不得归因于业务 MR、不得形成 P0/P1，也不得影响 `是否应通过`。diff 成功物化只证明该路径发生过变更，不能单独证明它存在于 head。

| scope entry / content evidence | head-side interpretation |
|---|---|
| exact `path`, `status=new/modified/renamed/copied` | `EXISTS` positive evidence |
| `status=deleted` | not positive evidence; verifier decides |
| renamed entry exact `old_path` | not positive evidence; verifier decides |
| materialized diff only | not positive evidence; verifier decides |

## 输出证据

每个缺失型 finding 必须记录：`scope.status`、验证的固定 `head_sha`、verifier 结果。缺少任一字段时，该 finding 至多为警告。
