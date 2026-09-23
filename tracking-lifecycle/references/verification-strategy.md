# 埋点验证分层策略

埋点验证按测试层级分为四层，每层有明确的验证目标、运行环境和适用场景。
层级越低反馈越快，应尽量在低层级发现问题。

## 分层总览

| 层级 | 验证目标 | 环境 | 反馈速度 | CI 卡控 |
|------|---------|------|---------|--------|
| L1 单测 | SDK 调用逻辑正确 | 本地，无网络 | 秒级 | 是 |
| L2 集成测试 | 事件数据结构正确 | 本地，无网络 | 秒级 | 是 |
| L3 沙盒验证 | 上报格式正确，collector 能收到 | 沙盒 collector | 分钟级 | 是 |
| 发布前确认 | 全链路数据管道跑通 | Staging | 5-10 分钟 | 否（有延迟） |

## 层级递进关系

每一层只验证上一层**信任但未验证**的假设：

| 层级 | 验证什么 | 信任什么 |
|------|---------|---------|
| L1 | 我调 SDK 的方式对不对 | 信任 SDK 本身 |
| L2 | UI 交互后产出的事件数据对不对 | 信任数据管道 |
| L3 | 上报格式和 collector 对接对不对 | 信任数仓管道 |
| 发布前确认 | Snowplow → Athena → MySQL 全链路对不对 | 不信任任何东西 |

失败时定位清晰：L1 挂了是调用逻辑错，L2 挂了是事件组装错，L3 挂了是上报格式/schema 错，发布前确认挂了是管道/部署问题。

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

**环境**：沙盒 Collector `https://us-prod-log-sandbox.theunismart.com`
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

## 发布前确认 — Staging 全链路验证

**目标**：确认数据在真实管道中完整落库。

**环境**：Staging（Snowplow → Kinesis → S3 → Athena → MySQL）

**方式**：
1. 代码部署到 staging 环境
2. E2E 测试执行用户流程，触发埋点上报
3. 等待数据同步（5-10 分钟）
4. 调用平台 `GET /api/release/getPublishedEvents?application={name}&version={ver}` 查询发布状态
5. 在平台 UI `/check/dashboard/` 页面确认事件验证状态

**验证内容**：
- `event_version_status` 中 `goods_latest_dvce_created_tstamp` > `bad_latest_dvce_created_tstamp`
- `goods_latest_dvce_created_tstamp` > 事件创建/更新时间

**适用场景**：
- 发布前最终确认
- 验证数仓管道完整性

**不适用**：
- CI 自动化卡控（有 5-10 分钟延迟，不适合）
- 开发阶段快速反馈

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
发布前确认（平台 validate API）
  ↓ 通过
可以发布
```

任何一层失败，在当前层修复后重跑，不跳到下一层。
