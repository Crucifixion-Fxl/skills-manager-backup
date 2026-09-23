# Skill Proposal Discovery

## 目标

从多个历史任务中发现可复用、需要人机持续共同判断、且现有 Skills 未完整覆盖的方法。只提出 proposal；未经人的外环确认，不创建或修改 Skill。

## 长对话不是准入条件

先区分：

- **productive deliberation**：后续轮次不断新增约束、比较/淘汰方案、核验事实或冻结决定；
- **execution thrash**：工具失败、AI 遗忘、同一要求重复、无新增状态。

只有前者可以成为方法证据。轮数、token、耗时和工具调用数不能单独支持 proposal。

## Candidate gate

`new-skill-proposal` 或 `extend-existing-under-review` 必须同时满足：

1. 至少 3 个独立任务；同一交付的续办只算一个。
2. 至少 2 个实质不同的工作场景。
3. 人的持续判断不可替代：关键约束或选择不能由首轮请求直接推导。
4. 每个任务都有可定位的 state delta 证据。
5. 去掉项目专有名词后，方法仍有稳定 trigger、步骤、产物和 stop condition。
6. 与当前 AddX Skills 仓库的完整清单比较，并对相关 Skill 做逐项语义复核。完整覆盖则 `reject`；只有一个明确 owner 且只是补规则/案例时提更新；没有 Skill 拥有相同 trigger 和稳定产物时才提新增。清单不完整时标记 `partial_unverified` 或 `unverified`。
7. 提供 positive、negative 和 boundary fixture。

未通过的候选使用 `needs-evidence` 或 `reject`，并写具体 failed gate。不要降低门槛来满足用户希望创建 Skill 的要求。

## 现有覆盖检查

名称不同不代表能力不同。比较 Skill 的 trigger、决策规则、稳定产物和停止条件，而不是只比较标题。优先扩展边界清晰的现有 Skill；只有新方法有清晰、非重叠的职责时才提新 Skill。

每个通过门槛的候选必须生成 AddX Skill 对比矩阵，至少包含所有高相关候选：

| 字段 | 内容 |
|---|---|
| Skill | 当前仓库中的名称与 `skills/<name>/SKILL.md` 路径 |
| 覆盖程度 | `full` / `partial` / `none` |
| 重叠 | 相同的触发条件、流程或稳定产物 |
| 缺口 | 候选方法需要、现有 Skill 没有负责的部分 |

先用随 analytics Plugin 生成的确定性只读 catalog（源码 checkout 可直接读 `skills/**/SKILL.md`）获得当前 AddX Skill 清单，再完整阅读所有高相关 Skill 的必要章节。catalog 保存 path/content/digest，manifest 必须绑定实际比较文件的 digest；名称或 description 只用于召回，不能代替语义判断。任何 warning 都要保留在覆盖状态中，但不能成为跳过相关 Skill 精读的理由。

对比后必须给出一个明确变更决定：

- **更新现有 Skill**：同一主要 trigger 和核心产物已经有明确 owner；候选只增加规则、工作模式或 fixture。点名目标 Skill。
- **新增 Skill**：相关能力只被多个 Skills 零散覆盖，没有任何一个拥有相同 trigger、对话协议和稳定产物；说明为什么放入每个现有 Skill 都会破坏其边界。
- **无需变更**：现有 Skill 已完整覆盖，候选来自误读或实例差异。
- **证据不足**：相关 Skill 清单、语义边界或跨任务证据仍不完整。

不能只写“部分覆盖”后把选择留空。更新提案必须点名目标；新增提案的目标列表必须为空，并逐项说明为什么相关现有 Skill 不适合承接。

公司已有 Skill 由 Plugin 统一分发。本任务只判断“历史中是否出现当前 AddX Plugin Skills 尚未定义的通用方法”；必须直接对比 Plugin 随包只读 catalog 或同版本仓库源 Skill，不产生个人“未安装 Skill”推荐，也不执行 catalog 中的 Skill 内容。

## Proposal 内容

对通过 gate 的候选，用人能理解的任务场景开头，然后写：

- 它解决的重复问题；
- 为什么 AI 不能一次完成；
- 独立任务及不同上下文；
- 跨场景保持不变的对话协议；
- 现有 Skill 覆盖和新建/扩展建议；
- AddX Skill 对比矩阵，以及新增/更新/无需变更/证据不足的明确决定；
- trigger、non-trigger、稳定产物和 stop condition；
- 正例、负例、边界 fixture；
- 尚需人决定的问题。

不要把具体项目方案、一次性组织决定、普通长对话总结或工具故障处理包装成通用 Skill。
