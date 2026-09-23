# 验证契约

## 计划

每个信号阶段使用统一字段，避免代码、串口和示波器各说一套单位：

```json
{
  "name": "pwm-1000hz",
  "serial_marker": "expected_freq_hz=1000",
  "settle_seconds": 1.0,
  "hold_seconds": 6.0,
  "expected": {
    "frequency_hz": 1000,
    "duty_percent": 50
  },
  "tolerance": {
    "frequency_percent": 1.0,
    "duty_percentage_points": 2.0
  }
}
```

容差必须来自器件规格、时钟误差、仪器精度或用户验收要求；没有依据时明确标为待确认，不能擅自使用固定百分比。

## 结果

`measurements.json` 中每个阶段至少记录：

| 字段 | 含义 |
|---|---|
| `expected` | 代码或规格推导的期望值 |
| `measured` | 示波器返回的原始数值和单位 |
| `error` | 绝对误差和相对误差 |
| `tolerance` | 本阶段实际使用的阈值及依据 |
| `serial_marker` | 对齐本次测量窗口的原始日志 |
| `screenshot` | 包含量程、时基、触发和测量栏的原图 |
| `scope_error` | 本轮结束时的仪器错误队列 |
| `verdict` | `pass`、`fail` 或 `inconclusive` |

## 判定

- 所有必测指标均在容差内且证据完整，阶段为 `pass`。
- 任一必测指标超差且采集条件有效，阶段为 `fail`。
- 无稳定触发、串口未对齐、探头接法不可信、SCPI 报错或证据缺失，阶段为 `inconclusive`。
- 总结论不得高于最弱必测阶段：存在 `fail` 则总结果为 `fail`；没有 `fail` 但存在 `inconclusive` 则总结果为 `inconclusive`。
