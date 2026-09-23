# Setting Override — Observability 闭环
# Setting Override Observability Loop

调完 admin API 之后, **必须**用三个数据源闭环验证, 缺一不可:
After hitting the admin API, you **must** close the loop with three data sources:

| 数据源 | 答的问题 | 入口 |
|:--|:--|:--|
| 1. admin API (`stats` / `get`) | DB 真的写进去了吗? 内容对不对? | `scripts/admin_cli.py --env <env> stats` / `... get --sn <sn>` |
| 2. Thanos / Prometheus 指标 | 设备真的拉到并合并了吗? 有没有失败? | `http://thanos-<env>.addx.live/api/v1/query` |
| 3. 应用日志 | 单 SN 出问题时找根因 | troubleshooting 平台 / Loki / `grep "setting override"` |

## 1. 指标(Counter)/ Counter metrics

来源 `SettingOverrideObservability.java`,共 3 条 Counter:

### 1.1 `setting_override_applied_total` — 设备命中并合并成功

| Tag | 取值 |
|:--|:--|
| `modelNo` | 设备型号 (空时归 `unknown`) |
| `experiment` | experiment 标签 (空时归 `(empty)`) |

> 此指标**每当设备主动调 `/deviceMsg/setting` 命中且合并成功时 +1**。upsert 写库不会让它涨;必须等设备拉。

### 1.2 `setting_override_failed_total` — 覆盖路径任一阶段抛异常

| Tag | 取值(封闭枚举,4 条序列) |
|:--|:--|
| `stage` | `fetch` / `parse` / `merge` / `emit`(越界落 `unknown` 兜底) |

> 这是**最关键的报警指标**。任何 stage 非零都意味着 inviolable invariant 被触发(响应已回退到合并前)——必须查根因。

### 1.3 `setting_override_admin_request_total` — admin API 调用审计

| Tag | 取值 |
|:--|:--|
| `action` | `upsert` / `delete` / `query` / `list` / `stats` |
| `result` | `success` / `validation_error` / `server_error` / `not_found` |

## 2. Thanos URL 模板 / Thanos host mapping

| `--env` | Thanos host |
|:--|:--|
| `staging-us` | `http://thanos-staging-us.addx.live` |
| `staging-eu` | `http://thanos-staging-eu.addx.live` |
| `staging-cn` | `http://thanos-cn.addx.live` |
| `prod-us` | `http://thanos-prod-us.addx.live` |
| `prod-eu` | `http://thanos-prod-eu.addx.live` |
| `prod-cn` | `http://thanos-cn.addx.live` (CN 不带 `prod-` 前缀) |

> Thanos 不需要鉴权,直接 `curl` 即可。

## 3. 必备 PromQL 查询 / Required PromQL queries

### 3.1 命中速率(过去 5 分钟,按 experiment 拆分)
```promql
sum by (experiment) (
  rate(setting_override_applied_total{experiment=~"<prefix>_.*"}[5m])
)
```

### 3.2 命中累计增量(过去 30 分钟)
```promql
sum by (experiment) (
  increase(setting_override_applied_total{experiment=~"<prefix>_.*"}[30m])
)
```

### 3.3 失败检查(任何 stage 非零 = 异常)
```promql
sum by (stage) (
  increase(setting_override_failed_total[30m])
)
```

期望返回空 vector / 0 行。任何非零结果都需要立刻去日志查根因。
Expect empty vector / no rows. Any non-zero result requires immediate log investigation.

### 3.4 admin 写入审计
```promql
sum by (action, result) (
  increase(setting_override_admin_request_total{action=~"upsert|delete"}[30m])
)
```

期望 `result="success"` 远大于其它,`result=validation_error / server_error` 应为 0。
Expect `success` dominates; `validation_error` and `server_error` should be zero.

## 4. 日志关键字 / Log keywords

| 事件 | Level | grep 模式 | 字段 |
|:--|:--|:--|:--|
| 命中并合并成功 | INFO | `setting override applied` | `sn=<sn> modelNo=<X> experiment=<tag> overriddenKeys=[...]` |
| 覆盖路径异常 | ERROR | `setting override failed` | `sn=<sn> stage=<fetch\|parse\|merge\|emit>` + 堆栈 |
| admin API 审计 | INFO | `setting override admin action` | `accessKey=<X> action=<X> snCount=<N> experiment=<tag> requestId=<uuid> result=<X>` |
| 观测层自身降级 | WARN | `setting override observability` | 仅 metric/log 自身吞异常,不影响主链路 |

合并 grep:
```bash
grep -E "setting override (applied|failed|admin action) " <log>
```

## 5. 验证脚本一键跑 / Run-it-all helper

`scripts/verify.py` 把上面 4 个维度封装成单条命令。

```bash
python3 scripts/setting_override/verify.py --env prod-us \
  --experiment pir_sensitivity_2026q2_sensitive \
  --experiment pir_sensitivity_2026q2_insensitive \
  --sample-sn 0003d06bf5c5c57438fbad8f89d00bb9 \
  --sample-sn 00021c61305617cc0e340cf62c22fde1
```

输出包括:
- DB stats 是否覆盖到目标 experiment
- 抽样 SN 的 row 是否符合预期(experiment / active / expireAt)
- 过去 30 min 命中累计增量 + 当前 5m 速率
- 过去 30 min `failed_total` 是否全 0
- 过去 30 min admin upsert 成功率

任何一项不达标 → exit code 非零 + 高亮报错。
Any failed check → non-zero exit code with red flag.

## 6. 期望基线 / Expected baselines

执行完一次完整 upsert 后, 一段时间内应该能看到:

| 时间窗 | 期望 |
|:--|:--|
| 写完 0-1 min | `applied_total` 开始有零星增量(部分设备本来就在拉) |
| 写完 5-10 min | 命中速率显著上涨, `failed_total` 仍为 0 |
| 写完 30 min | `applied_total` 30m increase 应在 SN 数的 ~5-30% 范围(取决于设备活跃度) |
| 任何时间 | `failed_total` 任何 stage 非零 → 立即查日志 |
