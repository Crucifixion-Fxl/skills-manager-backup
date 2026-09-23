# MR TDD Level 评估标准

本文定义单个 MR 的 TDD 过程证据等级，是 `testing-strategy` 对外提供的
唯一 TDD Level 标准。`code-review` 负责读取证据并输出结果，不得复制或另造等级。

TDD Level 与 L1-L4 测试层级是两个正交维度：

- L1-L4 回答“在哪一层验证”；
- T0-T4 回答“这个变更的测试是否真正先于实现、可追溯且可复现”。

## 1. 非门禁属性

TDD Level 是观测项，不是合并门禁：

- 不改变 code-review 的 `conclusion`、`score`、`should_pass`；
- 不因等级低而自动生成 `red_lines`；
- 不把 `UNVERIFIED` 当作失败；
- 允许 `T0` 与“Review 通过”同时存在。

但底层事实仍按原规则独立判断。例如项目明确要求 bug fix 必须有回归测试时，
“缺少测试”可以依据该项目规则形成 finding；阻断的是缺测试这个事实，不是 TDD Level。

## 2. 适用性

| 变更 | applicability | 说明 |
|:---|:---|:---|
| 新增或修改业务行为 | `applicable` | 必须评估 |
| Bug fix | `applicable` | 必须评估 red-green 与左移追溯 |
| API、schema、状态机行为变化 | `applicable` | 必须评估正确目标层级 |
| 纯重构且可证明行为不变 | `not_applicable` | 输出 `N/A`，回归覆盖另行审查 |
| 纯 docs / comment / format | `not_applicable` | 输出 `N/A` |
| 纯 CI / build / infra 调整 | `not_applicable` | 输出 `N/A`；其配置测试仍按普通质量规则检查 |
| Review scope 不完整，无法判断是否存在行为变化 | `unknown` | 输出 `UNVERIFIED`，不得猜适用性 |
| 已确认行为变化，但缺 red revision 或测试执行证据 | `applicable` | 输出 `UNVERIFIED`，不得猜成 `T0-T4` |

assessment unit 是一个可独立描述的行为变化（AC、bug、API contract 或状态转换）。混合 MR 的
overall level 使用以下确定性聚合规则：

1. 只要有一个 unit 的等级无法核实，overall level 为 `UNVERIFIED`；适用行为已确认时 overall
   applicability 仍为 `applicable`，只有适用性本身未知时才是 `unknown`；
2. 否则，对所有 `applicable` unit 取最低等级，例如 `T3 + T0 => T0`；
3. 只有不存在任何 `applicable` unit 时，overall 才是 `N/A`；
4. summary 必须列出所有 unit 及各自等级，evidence / gaps 必须能对应回 unit。

这样 overall 表达“这个 MR 的所有适用行为共同达到的最高等级”，不会用一个高等级行为掩盖
另一个没有测试的行为。

## 3. 等级定义

| Level | 名称 | 必须同时满足 |
|:---|:---|:---|
| `T0` | No relevant test | 已确认存在适用的行为变化，但没有测试覆盖该行为 |
| `T1` | Test present | 有相关测试，但只能证明测试存在；属于 test-after、同 commit 无法判序，或 red 证据未验证 |
| `T2` | Red-Green verified | 同一目标测试在 exact red revision 上因预期缺失行为失败，并在后续 exact green / head revision 上通过 |
| `T3` | Traceable and correctly layered | 满足 T2；测试目标层级符合 `testing-strategy` 的场景决策表；关联 AC 或 bug；bug fix 完成左移追溯 |
| `T4` | Fail-loud and governed | 满足 T3；有 fail-loud / mutation 等防假绿证据；所需测试进入 revision-bound CI 门禁并有执行 receipt |
| `N/A` | Not applicable | 经证据确认本 MR 不改变需要 TDD 的行为 |
| `UNVERIFIED` | Evidence unavailable | 适用性或等级所需证据不完整；不是失败等级 |

等级取已证明满足的最高档，不用“看起来应该”补足证据。`T1` 不等于坏：它明确区分了
“有回归保护”和“已证明 test-first”。

## 4. 目标测试层级

先依据 `testing-strategy` 的 TDD 决策表选择能最早捕获问题的层级：

| 场景 | 最低 red 证据 |
|:---|:---|
| crash / 局部状态 bug | L1 reproduction |
| API contract 变化 | L2-1 expected schema / contract red |
| DB、Redis、MQ 或 migration 行为 | L2-2 真实 infra red |
| 跨服务集成 bug | L2-1 contract red + 必要的 L3 red |
| UI 用户行为 AC | L3 黑盒 red |
| 无 UI technical vertical | public API / package harness / HTTP roundtrip L3 red |
| 新算法、排序或评分 | L1 table-driven red |

高层测试不能替代本可在低层捕获的逻辑错误；低层测试也不能替代跨边界 AC。

## 5. 证据优先级

从强到弱：

1. 在 exact red revision 与后续 exact green / head revision 上执行同一 test target：前者按预期失败，后者通过；
2. 分别绑定 exact red revision 与 green / head revision 的 CI artifact / test report；
3. MR commit 顺序、test diff 与 implementation diff 能证明 test 先出现，但未执行回放；
4. commit message、MR 描述或作者自述。

只有 1 或 2 可以证明 `T2`。3 最多支持 `T1`；4 只能作为待核线索，不能提升等级。

red 必须是“预期原因失败”。编译失败、fixture 缺失、环境不可用或无关断言失败，都不算有效 red。

## 6. 防假绿与左移

`T3` / `T4` 额外核对：

- 测试断言用户 / 业务行为，而不是只断言一个中间写入；
- fixture、threshold、seed 和目标分支有前置断言；
- bug 在 L3/L4 被发现时，说明为什么更左侧没有捕获，并在可行的最左层补保护；
- `T4` 的 fail-loud 证据要说明故意破坏了哪个生产分支以及测试如何失败；
- CI receipt 绑定本次 head revision，而不是引用历史绿色 pipeline。

## 7. Code Review 输出契约

每个远程 MR review 都必须输出：

```json
{
  "level": "T2",
  "applicability": "applicable",
  "summary": "API contract bug 在 L2-1 完成 red-green 回放；尚无左移追溯。",
  "evidence": [
    "red revision abc123: test-api-contract 因缺少字段失败",
    "green revision def456: 同一 target 通过"
  ],
  "gaps": [
    "未记录为何 L1 无法更早捕获"
  ]
}
```

约束：

- `level` 只能是 `T0`、`T1`、`T2`、`T3`、`T4`、`N/A`、`UNVERIFIED`；
- `applicability` 只能是 `applicable`、`not_applicable`、`unknown`；
- `N/A` 必须对应 `not_applicable`；
- `UNVERIFIED` 可对应 `applicable`（行为已确认、等级证据不足）或 `unknown`（适用性也不确定），
  不得伪装成 `T0`；
- `T0-T4` 必须对应 `applicable`；
- `T0-T4` 和 `N/A` 的 evidence 至少一条，且必须包含可定位的 SHA、test target、文件或 artifact；
- level 不参与 gate，底层 finding 另行进入正常 Review 规则。

消费者必须把缺失、格式无效或相互矛盾的 assessment 规范化为 `UNVERIFIED / unknown` 并继续
发布核心 Review，不能因为观测信号采集失败而制造新的合并门禁。持久化信号必须只保留上述
五个字段并设置条目 / 长度上限；未知字段和超限载荷不得进入 MR marker。
消费者只接受同一 Review 评论中恰好一个 canonical TDD marker；零个或多个都按 `UNVERIFIED`
处理，禁止取第一个、最后一个或最高等级。生产者必须转义模型文本中的保留 marker 前缀。

## 8. 受限 CI 的证据上限

本地远程 MR Review 可在授权范围内回放 exact red / green revisions。受限 CI Review 只能读取
调用方显式提供的材料；若 review-data 只有 fixed base/head diff、没有 red revision 或绑定 revision
的测试 receipt，则：

- 仍可依据完整 diff 输出 `T0`、`T1` 或 `N/A`；
- 不得把 commit message、diff 顺序或作者自述提升成 `T2`；
- `T2-T4` 仅在调用方提供 exact red/green revision + target + result（以及相应高阶证据）时可达；
- 材料不足以判断适用性或覆盖范围时输出 `UNVERIFIED`。

这是一条输入能力边界，不是 TDD gate。后续预处理器若要提高自动评估上限，应提供受控的
red/green revision 执行记录或 CI artifact receipt；在该通路落地前不得伪造高等级。
