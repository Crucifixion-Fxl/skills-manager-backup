# Scheduling Definition

本文件统一定义 Dagster Python 资产调度相关规范，重点约束 `partitions_def` 与 `cron`。

## 1) 调度参数定义

- `partitions_def`：分区粒度定义
- `cron`：调度触发表达式

## 2) partitions_def 规范

- 仅允许以下值：
  - `day_partition_def`
  - `hour_partition_def`
- 选择规则：
  - 天级任务使用 `day_partition_def`
  - 小时级任务使用 `hour_partition_def`
- 若用户未明确分区粒度：必须先让用户选择，禁止默认拍板

## 3) cron 表达式规范

- 使用标准 5 段格式：`<minute> <hour> <day_of_month> <month> <day_of_week>`
- 示例：
  - 每天 01:05：`5 1 * * *`
  - 每小时第 5 分钟：`5 * * * *`
- 若用户未明确 `cron`：必须先让用户选择，禁止默认填值

## 4) partitions_def 与 cron 一致性规则

- `day_partition_def`：
  - 建议使用日频表达式（典型：`M H * * *`）
- `hour_partition_def`：
  - 建议使用小时频表达式（典型：`M * * * *`）
- 当 `partitions_def` 与 `cron` 粒度不一致时：
  - 不得直接落地
  - 必须先提示冲突并让用户确认修正

## 5) 校验清单

- 已明确 `partitions_def`
- 已明确 `cron`
- 已检查二者粒度一致
- 未明确时已完成用户选择并确认

## 6) 模板映射

- 当明确为天调度后，优先使用：
  - [python-asset-template-day.md](python-asset-template-day.md)
- 当明确为小时调度后，优先使用：
  - [python-asset-template-hour.md](python-asset-template-hour.md)
