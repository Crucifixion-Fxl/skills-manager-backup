# Staging 埋点入仓查询

本指引用于 L4 人工回读及业务项目数仓适配器的查询设计，不新增 CLI 查询命令。
统一通过 **Superset REST API → SQL Lab → Athena** 执行只读 SQL。
认证、数据库连接选择和同步/异步 API 使用 [Superset Skill](../../../data/superset/SKILL.md)，不在此复制凭据或登录实现。
本地日志、SDK 调用成功、Collector 收到及 Micro good 均不能替代数仓证据。

## 先确认环境和查询对象

1. 核对本次 Collector、区域、数仓连接与 `events/staging-target.yaml`。下文 `analytics_staging` 是已核验的 US Staging 示例；其他部署必须现场确认。不能因 namespace 为 `prod` 就判定生产，也不能用生产表代替 Staging 验收。
2. 先查元数据定位 `dwd_event_<application_key>_<event_name>_hi` 事件专属视图，确认存在、列定义、来源和权限。`application_key` 是埋点应用命名标识，不是手机包名；例如 `smart_camera` 对应的视图仍需用 `app_id` 区分宿主应用。
3. 读取视图定义，核对它实际展开的物理表及分区。逻辑视图不是独立物理表，不能仅凭使用视图宣称扫描更少；自动化报告须记录真实源表，并与 target 允许表一致。
4. 先确认 good/bad 的当前分区和水位覆盖测试窗口。旧表长期不更新、视图不存在、权限不足或查询失败，都不能解释为零事件或丢点。

## 查询顺序与范围

| 对象 | 使用条件 | 必需过滤 |
|---|---|---|
| `analytics_staging.dwd_event_<application_key>_<event_name>_hi` | 正常事件首选入口 | 有上下界的 `dvce_created_tstamp`；核实后的应用、业务账号、runId/事件 ID、触点 |
| `analytics_staging.dwd_base_event_hi` | 已确认新事件视图尚未生成且确需验证，或有其他明确必要原因；执行前记录原因与范围 | 有上下界的 `dvce_created_tstamp` **及**明确 `event_name`，再按实际列限定应用与测试样本 |
| `analytics_staging.dwd_bad_events_hi` | 每次完整验收均查询；good 有数据也不能省略 | 有上下界的 `dt` 分区窗口，关联目标事件 ID 或可核实的测试样本身份 |

非必要禁止直接查基表；未找视图或视图返回空结果都不是兜底理由。
视图生成有延迟，视图不存在不等于事件没有上报。
必要时可交叉核对经确认的 Staging ODS，须单独核实该表的字段、分区和水位，不能照搬 DWD SQL。

- 只选验证必需字段，不用 `SELECT *`，不导出无关用户或整份原始 contexts。
- 统一记录时区，窗口使用 `>= start AND < end`；事件时间、Collector 接收时间、入仓时间分别记录。bad 的 `dt` 含义须从元数据核实，按实际处理延迟覆盖相关分区，不能机械照搬 good 的窄事件时间窗口。
- `LIMIT` 只限制返回行数，不能替代分区过滤或证明完整性；达到上限时缩小范围或做有界聚合，未取全不能验收通过。
- 不枚举整表 `$partitions` 来找分区。建表语句读取失败时，使用可用元数据或经核实的定义；仍无法确认则记录未确认，不猜字段或改配置绕过。
- 提交查询超时或结果未知时，先按查询 ID/客户端查询标识回读状态，不盲目重提。同步/异步按查询耗时与当前能力选择，一次失败不代表某种模式永久不可用。

## 核对账号字段，再执行有界查询

顶层 `user_id` 可能是匿名值或负数占位值；先核对其与公共上下文中业务账号的语义。
不能因为顶层账号查不到就判丢点，也不能盲用 `COALESCE`（非空占位值不会回退）。
已核验的 `smart_camera` 示例视图把业务账号映射为 `user_id_com_base_base_schema`，把事件字段映射为 `*_event`；其他视图须重新核对，不能假定相同。

以下仅展示查询形状；账号、宿主、触点和时间必须替换为本次授权测试范围，列名及类型须先核实：

```sql
SELECT event_id, dvce_created_tstamp, event_version,
       user_id_com_base_base_schema AS business_user_id,
       app_build_com_snowplowanalytics_mobile_application AS app_build,
       source_event, result_event, duration_ms_event,
       device_number_event, is_vip_event
FROM analytics_staging.dwd_event_smart_camera_flutter_paywall_loading_finished_hi
WHERE dvce_created_tstamp >= TIMESTAMP '2026-10-21 12:59:46'
  AND dvce_created_tstamp <  TIMESTAMP '2026-10-21 13:03:10'
  AND user_id_com_base_base_schema = 123456
  AND app_id = 'com.example.staging'
  AND source_event = 'vn_device_bind_success'
ORDER BY dvce_created_tstamp, event_id
LIMIT 20;
```

有 runId 或发送端事件 ID 时，使用已核实的列继续限定；没有时只能按账号、版本、触点与操作时间作有界人工核对，明确缺少逐次发送关联证据，不能伪造 runId 或声称生成了自动 L4 PASS。
账号过滤无结果时，可在相同时间、平台、版本和触点内有界排查账号切换归因，只返回定位所需字段。

## 完整验收与报告

1. 同时核对 good 与 bad，按 `event_id` 去重，报告原始条数、唯一事件数、重复数及相对预期次数的缺失。重复不能被去重后静默隐藏。
2. 比较事件版本、App 版本、公共字段、业务结果/耗时、设备数、VIP 状态和触点归因；具体断言由本次 scenario 决定，不把上述示例字段强加给所有事件。
3. bad 即使缺少 event_name 或未成功解析公共字段，也不能被简单事件名过滤排除后判为零。按实际 bad 结构关联事件 ID、测试身份或受限原始载荷，报告可关联范围和无法解析的缺口；只输出必要的错误类别及脱敏字段。
4. 保存 SQL、查询 ID、连接/环境、视图及物理表、时间窗、分区、水位、查询时间、预期/实测结果和证据位置。数仓 good/bad 均完整且可关联，才能给出该场景的入仓验收结论。
5. 查询失败、水位未覆盖、来源不明或 bad 无法核实，记录“未确认”；已确认水位滞后记 `WAREHOUSE_DELAY`，都不能记零事件 PASS。技术入仓通过不代表随机分组、看板口径或业务指标验收通过。

自动门禁仍按 [TDD 契约](tdd-contracts.md) 和 [Skill L4 流程](../SKILL.md#l4-staging-技术-tdd) 绑定 runId、目标摘要及同一构建产物；本指引不放宽这些校验。
