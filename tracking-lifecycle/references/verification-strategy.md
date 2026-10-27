# 埋点验证分层策略

埋点验证按测试层级分为四层，每层有明确的验证目标、运行环境和适用场景。
层级越低反馈越快，应尽量在低层级发现问题。

## 分层总览

| 层级 | 验证目标 | 环境 | 反馈速度 | CI 卡控 |
|------|---------|------|---------|--------|
| L1 单测 | SDK 调用逻辑正确 | 本地，无网络 | 秒级 | 是 |
| L2 集成测试 | 事件数据结构正确 | 本地，无网络 | 秒级 | 是 |
| L3 沙盒验证 | 上报格式正确，collector 能收到 | 沙盒 collector | 分钟级 | 是 |
| L4 入仓验证 | 真实数据入仓、数量与字段正确 | Staging | 取决于实际水位 | 是（项目 hook + verify-staging） |

## 层级递进关系

每一层只验证上一层**信任但未验证**的假设：

| 层级 | 验证什么 | 信任什么 |
|------|---------|---------|
| L1 | 我调 SDK 的方式对不对 | 信任 SDK 本身 |
| L2 | UI 交互后产出的事件数据对不对 | 信任数据管道 |
| L3 | 上报格式和 collector 对接对不对 | 信任数仓管道 |
| L4 | 指定 Staging Collector 到实际数仓源表的链路、数量与字段是否正确 | 不以平台状态代替本次样本入仓证据 |

失败时按层定位：L1 检查调用逻辑，L2 检查事件组装，L3 检查上报格式/schema，L4 核对环境、查询、数据水位及实际入仓结果；不能把查询失败直接判成丢点。

## L1 — 单元测试（mock SDK）

**目标**：验证业务代码对 SDK 的调用参数正确。

**方式**：mock SDK 接口，触发业务动作，verify 调用参数。

**断言对象**：
- 事件名 / point
- 调用参数（字段名 + 值）
- 调用次数

**适用场景**：
- 所有端（Flutter、Android、iOS、后端）
- 每个埋点事件至少一个 L1 用例

**不适用**：
- 验证事件间的顺序关系
- 验证 UI 交互触发链路

**详细 mock 方案**：参见 [testing-guide.md](testing-guide.md) L1 部分。

## L2 — 集成测试（test sink）

**目标**：验证 UI 交互 → 事件产出的完整链路，事件数据结构正确。

**方式**：注入 test sink 替换真实 reporter，捕获事件列表后做结构化断言。

**断言对象**：
- 事件名 / point
- payload 字段与值
- 触发次数
- 事件顺序（可选）

**适用场景**：
- 有 UI 交互的端（Flutter、Android、iOS）
- 需要验证 "用户操作 A 产生事件 B" 的场景
- 需要验证去重逻辑（如曝光只触发一次）

**不适用**：
- 后端 SELF_DEFINE 事件（无 UI，L1 已够）
- 需要验证真实网络上报格式

**详细 test sink 方案**：参见 [testing-guide.md](testing-guide.md) L2 部分。

## L3 — 沙盒验证

**目标**：验证真实 SDK 上报到 collector 的格式正确，schema 已注册。

**环境**：以下为 US 沙盒示例；实际 Micro 查询服务通过 `TRACKING_SANDBOX_BASE_URL` 显式指定，SDK Collector 另按本次沙盒环境配置。
- collector 实时可查，适合纳入 CI 自动化卡控
- 查询接口：`/micro/good`（有效事件）、`/micro/bad`（无效事件）

**方式**：
1. 配置 SDK 指向沙盒 collector
2. 测试用例触发埋点上报
3. 运行 `sandbox-check.js` 查询并比对结果

**验证内容**：
- 事件是否出现在 `/good` 列表（schema 已注册、格式合规）
- `/bad` 列表中的错误（schema not found、missing required field、type mismatch）
- 事件参数与 tracking-design.md 定义一致

**适用场景**：
- CI 流程中的埋点格式卡控
- 确认 schema 已正确注册到平台
- 验证 base_schemas 附加是否正确

**不适用**：
- 验证数仓落库（沙盒没有完整的 Athena → MySQL 链路）
- 本地快速反馈（有网络延迟，应先通过 L1/L2）

## L4 — Staging 入仓验证

**目标**：确认数据在真实管道中完整落库。

**环境**：`events/staging-target.yaml` 中经核实的 Collector、区域、数仓身份和实际源表，不假定所有部署都有相同管道。

**方式**：
1. 同一构建产物部署到目标 staging，通过真实 SDK 和业务流程触发事件；记录 runId、事件 ID、版本与操作时间。
2. 按 [Staging 入仓查询](staging-event-query.md) 通过 Superset REST API → SQL Lab → Athena 查询，优先事件视图，确认物理源表、分区和水位。
3. 同时核对 good/bad、去重前后条数、预期次数、公共字段和业务字段。日志、Micro 结果及平台汇总状态不能替代本次样本的数仓证据。
4. 自动门禁由项目 hook 输出证据，`verify-staging` 校验 runId、目标、构建和结果；缺少绑定证据的人工查询只报告其实际核对范围，不冒充自动 PASS。

**验证内容**：
- 以本次场景预期核对完整事件身份、次数、字段及 bad，保留查询 ID、时间窗、分区、水位和结果。
- 水位未到为 `WAREHOUSE_DELAY`；权限、查询或来源未确认时保留未确认，不记作零事件或验收通过。
- 平台发布状态和 `event_version_status` 可作辅助诊断，但“最新 good 晚于 bad”不能证明本次所有事件都正确。

**适用场景**：
- 发布前最终确认
- 验证数仓管道完整性

**不适用**：
- 开发阶段快速反馈
- 用生产表替代 Staging 验收

## 选择决策树

```
开发者写完埋点代码
  ↓
先跑 L1 单测（mock SDK，verify 调用）
  ↓ 通过
再跑 L2 集成测试（test sink，assert 事件数据）
  ↓ 通过
CI 中跑 L3 沙盒验证（真实上报，sandbox-check.js）
  ↓ 通过
代码合并部署到 staging
  ↓
L4 入仓验证（实际数仓 good/bad + verify-staging）
  ↓ 通过
执行工单校验与发布门禁（按 Skill 主流程）
```

任何一层失败，在当前层修复后重跑，不跳到下一层。
