# TruffleHog JSONL 字段说明

TruffleHog 的 `--json` 输出是 JSONL（每行一个 JSON 对象）。

## 报告建议字段

| 字段 | 用途 |
|---|---|
| `DetectorName` | 凭证类型/检测器名称 |
| `Verified` | 是否被 TruffleHog 验证有效 |
| `Redacted` | 可安全展示的脱敏值 |
| `SourceMetadata` | 文件、提交、平台来源信息 |

## 禁止字段

| 字段 | 规则 |
|---|---|
| `Raw` | 严禁输出或落盘 |
| 完整凭证明文 | 严禁进入报告 |

## 常见提取路径

- 文件路径：
  `SourceMetadata.Data.Git.file`（或对应平台字段）
- 提交哈希：
  `SourceMetadata.Data.Git.commit`
- 作者：
  `SourceMetadata.Data.Git.email`（或对应平台字段）
- 详情链接：
  平台字段中的链接（若存在）

## 最小汇总模板

- 扫描目标
- 流程类型
- TruffleHog 版本
- 结果模式
- 输出目录
- `verified` 数量
- `unknown` 数量（如启用）
- 重点 verified 结果（`DetectorName`、文件、commit、`Redacted`）
