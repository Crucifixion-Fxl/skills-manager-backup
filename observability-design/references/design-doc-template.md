# 可观测性设计文档模板

`:design` 工作流产出。写一份完整的设计文档，成为后续实施 / 审计 / 新人阅读的 SSOT。

## 文件路径规范

```text
docs/plans/YYYY-MM-DD-<feature>-observability-design.md
docs/plans/YYYY-MM-DD-<feature>-observability-data-pipeline-design.md
docs/plans/YYYY-MM-DD-<feature>-observability-data-pipeline-impl-plan.md
```

设计落地后，把核心结构归档到：

```text
docs/architecture/<service>/observability.md
```

## 文档结构（标准 11 段）

```markdown
# <Feature> 可观测性设计

| 文档状态 | 设计 | YYYY-MM-DD |
|---------|------|-----------|
| Issue   | #NN  |           |

## 1. 业务目标

## 2. 决策问题清单

## 3. 指标定义

## 4. 数据模型 / 口径

## 5. 采集计划

## 6. 数据链路 / 消费端 / 告警

## 7. Current / Target / Gap / Tracking

## 8. 实施方案

## 9. 依赖与前置条件

## 10. 验收标准

## 11. 回滚 / 降级策略
```

### 1. 业务目标

先写清楚三件事：

- 这次观测要保护或提升什么业务结果
- 如果观测缺失，可能造成什么用户或业务伤害
- 这次设计刻意不解决什么边界外问题

反模式：只写“补齐可观测性”这种没有结果导向的话。

### 2. 决策问题清单

先写清楚这次设计要支持哪些**产品 / 运营 / oncall 决策**。每个问题必须是可回答、可追踪、可行动的，不能写成空泛的“健康度”。

建议分成两栏：

```markdown
### 2.1 业务效果（PM / 运营视角）
1. 功能上线后是否提升了 <核心业务结果> ？
2. 哪个 scene / recipe 在漏斗里损耗最大？

### 2.2 系统稳定（SRE / oncall 视角）
1. 哪个依赖异常会导致 silent downgrade？
2. 哪条链路会先报警，谁负责处理？
```

### 3. 指标定义

每个指标都必须回链到至少一个决策问题。表内必须能看出“为什么采这个指标”，否则删掉。

```markdown
### 3.1 北极星 - 近端
| 指标名 | 业务问题 | 公式 | 分母 | 维度 | 阈值方向 |

### 3.2 北极星 - 远端
| 指标名 | 业务问题 | 公式 | 分母 | 维度 | 阈值方向 |

### 3.3 漏斗效率
| 指标名 | 业务问题 | 公式 | 分母 | 维度 | 阈值方向 |

### 3.4 交互质量
| 指标名 | 业务问题 | 公式 | 分母 | 维度 | 阈值方向 |

### 3.5 护栏（业务 + 系统安全）
| 指标名 | 业务问题 | 公式 | 分母 | 维度 | 阈值方向 |

### 3.6 系统稳定指标（OTel / Prometheus）
| metric 名 | 业务问题 | 类型 | labels | 说明 |
```

### 4. 数据模型 / 口径

这部分先定义“怎么算”，再决定“采什么”。

```markdown
### 4.1 核心事实粒度
- 例如：一行一次 `scene_entry`

### 4.2 关键分母
- 例如：`scene_entry`
- 例如：`shown=true`

### 4.3 关键维度
- 例如：`scene_id`
- 例如：`variation_key`
- 例如：`dt`

### 4.4 时间窗口 / 回填
- 即时
- D7
- D30
- 5 分钟滑窗
```

### 5. 采集计划

采集计划必须服务已定义好的指标和口径，不允许因为“SDK 方便”或“平台支持”就临时扩字段。

```markdown
### 5.1 事件
| 事件 | 服务的指标 | 必填字段 | 触发时机 |

### 5.2 上下文 / tags
| 字段 | 服务的指标或问题 | 来源 |

### 5.3 配置维度
| 表 / 配置 | 用途 | 更新方式 |
```

### 6. 数据链路 / 消费端 / 告警

按三链路分小节。**每条链路一张 mermaid 图 + 一张四要素表 + 一张"逻辑指标 → 物理制品"crosswalk 表。**

每个小节先描述要支持的问题，再写数据和通道，最后写消费端和告警路径。

**crosswalk 是这一节的硬核输出**——§3 定义的每条逻辑指标，到了消费端都有**具体承接制品**。没有这张表 = 后面写代码/配置时谁做谁的、叫什么、在哪找，全靠口口相传。见 §6.5 模板。

```markdown
### 6.1 链路 ① 业务指标链路
<mermaid 架构图>

| 要素 | 实现 |
|------|------|
| 数据源 | ... |
| 传输通道 | ... |
| 存储 | ... |
| 消费端 | ... |
| 责任人 | ... |

### 6.2 链路 ② 错误链路
<同上>

### 6.3 链路 ③ 系统指标链路
<同上>

### 6.4 链路交叉佐证
| 故障场景 | 链路① | 链路② | 链路③ |
|---------|-------|-------|-------|

### 6.5 逻辑指标 → 消费端制品 crosswalk（**强制输出**）

把 §3 的每条业务指标列出来，对上消费端的**具体工件 id / 路径**。一行一个指标，一列一个消费端。没有出现在某消费端的单元格填 `—`（并思考是否是 gap）。

**只做映射**——指标的 layer / 公式 / 分母 / 维度 / 阈值方向 / 适用子类型**不重复**在这张表里，读者回 §3（按 layer 分组的定义表）查。本表的子小节分组也和 §3 保持一致（near/far-star → funnel/interaction → guardrail → diagnostic）。

| 逻辑指标 | GrowthBook factMetric id | Superset dataset.metric_name | SLA rule id | Grafana panel / alert |
|---------|------------------------|------------------------------|-------------|----------------------|
| touchpoint_ctr | `touchpoint_ctr` | `engagement_funnel.ctr` | `touchpoint_ctr_sla_daily` | `eng-business/ctr` |
| touchpoint_locked_ctr | `touchpoint_locked_ctr` | `engagement_funnel.locked_ctr` | — | — |
| per_user_5min_impression_p99 | `per_user_5min_impression_p99` | `engagement_guardrail.p99` | `touchpoint_user_impression_p99` | `eng-guardrail/p99` |

**列含义**：
- **消费端 id 列**：用各工具的原生 id 形式（不是 display name），方便 cross-ref
- **空单元格**：表示该指标在此消费端不落地；若该指标**应该**落地，则在 §7.3 Gap 表登一条
- **漂移对账**：消费端数字不对 → 本表反查 id → 追到各工具的 config 定义 → 再追到 §3 公式 / §4 分母；任一层不一致 = gap

**为什么不把 Layer / 适用子类型 / 口径 SSOT 当列放进来**：这些字段在 §3 定义表里已经存在，crosswalk 复制一遍 = 两张表维护两份真源，迟早漂移。§6.5 只做"逻辑指标 → 物理制品 id"的单一职责查找表。读者需要完整定义时回 §3 查（anchor link 直达）。

```

### 7. Current / Target / Gap / Tracking

这部分是强制项，不能省略。每个 gap 必须说明当前状态、目标状态、阻塞原因、对应 issue。

```markdown
### 7.1 Current
| 事项 | 现状 | 证据 |

### 7.2 Target
| 事项 | 目标态 | 证据 / 约束 |

### 7.3 Gap
| gap | 影响 | 优先级 | owner |

### 7.4 Tracking
| gap | issue | 状态 | 下次动作 |
```

### 8. 实施方案

列具体的代码 / 配置变更：

```markdown
### 8.1 新文件
| 文件 | 职责 |

### 8.2 修改文件
| 文件 | 变更 |

### 8.3 不改（防止 scope 膨胀）
- ...
```

### 9. 依赖与前置条件

- 基础设施：Sentry project、Prometheus、Grafana 数据源、PagerDuty 路由
- 数据：埋点 schema、数仓表、回填任务、数据质量校验
- 人员 / 权限：owner、oncall、平台权限
- 跨仓库依赖：dbt、infra、app、admin 等

### 10. 验收标准

可勾选的 checklist，不要写模糊描述：

```markdown
- [ ] 指标能回答对应决策问题
- [ ] 分母、粒度、窗口、维度已固定
- [ ] 采集事件和字段在 staging 可验证
- [ ] 数据链路可跑通
- [ ] Dashboard / 告警能被对应角色消费
- [ ] 关键 gap 已绑定 issue
```

### 11. 回滚 / 降级策略

| 场景 | 操作 |
|------|------|

## 必含的 Mermaid 图

1. **总览图**（三链路数据流）— 在 §6 开头
2. **业务指标链路图**（数据源 / 通道 / 存储 / 消费端）— 在 §6.1
3. **漏斗图**（如有）— 展示业务事件之间的关系
4. **故障场景交叉表** — 文字表但可用 mermaid sequence 图增强

## 指标定义表的标准列

**业务指标 / AB 指标 §3 定义表（6 + 1 列，第 7 列 polymorphic domain 必填）：**

| 列 | 内容 |
|---|------|
| 指标名 | 业务读得懂的名字 |
| 业务问题 | 这个指标回答的具体决策问题 |
| 公式 | SQL 伪代码，分子/分母明确 |
| 分母 | 明确是 scene_entry / shown=true / 其他 |
| 维度 | 切片维度（scene × variation × date） |
| 阈值方向 | 越高越好 / 越低越好 / 偏离基线告警 |
| 适用子类型 | polymorphic 域必填（PROACTIVE / FEATURE_GATE / ios / android / tier-A / ...）；普适指标写 `全部`，留白视作 gap |

**§6.5 crosswalk 表（5 列，只做映射）：**

见上方 §6.5 章节说明——crosswalk 只有 5 列（指标名 + 4 个消费端 id 列），layer / 公式 / 适用子类型 等**不重复**，全部回 §3 定义表查。两张表职责正交，避免双真源。

**OTel metric（4 列）：**

| 列 | 内容 |
|---|------|
| metric 名 | `<service>_<业务>_<动词/名词>_<单位>` |
| 类型 | Counter / Histogram / Gauge / Observable Gauge |
| labels | 低基数，≤ 20 种值 |
| 说明 | 回答哪个业务问题 |

## 反模式

| 反模式 | 正解 |
|-------|------|
| 先写采集字段，再回头定义分母和粒度 | 先写 §4 数据模型 / 口径，再写 §5 采集计划 |
| 指标表没有“业务问题”这一列 | 每个指标标注来源问题，否则删掉 |
| 三链路的 mermaid 图合并成一张 | 分开画，合并图只在 §6 开头 |
| 实施方案写伪代码不写文件路径 | 必须到文件级 |
| 验收写“测试通过”这种模糊描述 | 必须可勾选 checkbox |
| 忘写降级策略 | 观测性系统自己不能是 SPOF，必有降级路径 |
| §3 指标表写完就去写 §7 / §8，不列"逻辑指标 → 物理制品"crosswalk（§6.5） | 没有 crosswalk = 谁落到哪个工具里、叫什么 id、漂移时怎么对账，都靠口头 |
| polymorphic domain（多子类型）不写"适用子类型"列 | 留白 = 所有消费端都按"全部"装，上线后才发现某子类型指标恒为 0 / 恒为 NULL |
| 生成的 config-as-code 文件（`metrics.yml` / `datasets.yml`）顶部没写"口径 SSOT 指针" | 每个 code artefact 顶部留一行 `# 口径 SSOT: <docs path>#<anchor>`，新人读代码不用猜 SQL 来源 |
