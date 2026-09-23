# Prompt Template Standard

所有 subagent prompt 模板必须遵循以下结构。

## 统一结构

| Section | 内容 | 位置要求 |
|---------|------|---------|
| §0 CONTRACT | 强制产出物声明 | 最顶部（# 标题后第一个 ##） |
| §1 输入数据 | 动态变量（本次任务独有） | CONTRACT 之后 |
| §2 状态机/流程 | 有序阶段定义 | 输入数据之后 |
| §3 产出格式 | Builder API 调用示例 | 状态机之后 |
| §4 约束与安全规则 | 只读约束、CG-84 安全规则、密钥规则 | 产出格式之后 |
| §5 参考资源索引 | 按需读取的文件路径表格 | 约束之后 |
| §6 Checkpoint 规则 | 通用格式 + 阶段名表格 | 参考索引之后 |
| §7 CONTRACT ECHO | 重复 §0 核心约束（3行） | 文件最末尾 |

## 内容放置规则

| 数据特征 | 处理方式 | 例子 |
|----------|---------|------|
| 动态 + 每次不同 | 注入 prompt | alert_data_json |
| 静态 + 短 (<20行) + 关键 | inline | 安全规则 |
| 静态 + 长 + 部分需要 | 路径索引 + 按需读取 | infra/*.md |
| 静态 + 长 + 特定阶段 | 在对应阶段指引读取 | root-cause-standard.md |

## 设计原则

- **P1 Contract-First**: 产出物契约放首尾（primacy + recency bias）
- **P2 三层防御**: Prompt → Builder → Dispatcher Gate
- **P3 最小注入**: 只注入动态数据，静态数据按需加载
- **P4 单一出口**: 所有路径汇聚 FINALIZE
- **P5 Fail-Loud**: 产出物缺失 = 系统主动检测
