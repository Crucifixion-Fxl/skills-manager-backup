# SSOT × Glossary × 口径透明 —— 多轮调查的文档纪律

> 多轮 EDA 调查过程中，**结论会翻转、变量定义会被简写稀释、阈值会被不同 agent 用不同口径**。本文档规定了防止文档"腐烂"的三条纪律：**SSOT**（单一事实源）、**Glossary**（术语/变量/参数定义源）、**口径透明**（每个定义写清 SQL / 计算公式）。
>
> 不守这三条，跑到第 5 轮时你自己都会忘了 Q22 里 "suspect" 和 Q25 里 "suspect" 是不是同一回事（real example；见 [case-study-kf226-solar.md](case-study-kf226-solar.md) R3-R5）。

## 1. 问题：为什么多轮 EDA 的文档会腐烂

经典腐烂形态：

1. **结论飘移**：第 2 轮写 "KF226 是 top 异常"，第 3 轮发现假的但只把第 3 轮 doc 改了，第 2 轮 doc 还保留旧结论 → 后来的 reviewer 两个都看就懵了
2. **口径漂移**：同一个词 "suspect" 在不同 round 指不同东西（cohort heuristic / ads 表全量 / popup 最严 filter），跨 round 结论对比产生**虚假信号**
3. **变量定义散落**：`battery_min` / `bm` / `bm_min_7d` 在 3 个文件里写成 3 种，读者根本不知道是不是同一字段
4. **阈值硬编码又私自改**：`individual_threshold=0.2` 某 agent 改成 0.3 没人看到，下一轮结论对比时对不上
5. **撤回偷偷改**：结论被推翻，直接改文字不标"撤回"，追溯史丢失

这四条本 skill 的 reference `case-study-kf226-solar.md` 的 R3 Q25 (CG625-BD2 17×) / R4 Q26 (新装 40.2%) / R5 Q31 (other 55.94%) 都踩过。

## 2. 三条纪律

### 2.1 SSOT · 每条结论只在一个文件里说一次

**规则**：

- 场景 doc 里 **`04-findings.md` 是 LIVING SSOT**（见 [layered-doc-template.md](layered-doc-template.md)），包含当前生效的所有结论 / 规则 / 参数
- 其他所有文档（README / 01 / 02 / 05 / 06 / issue / MR description / slack 转述）**只引用**，不复制内容
- 引用用 **相对链接 + anchor**：`见 [04-findings §5.2 severity band](04-findings.md#52-severity-band)`
- `03-analysis/0N-*.md` 是 **append-only 证据账本**：每轮一个文件，写完冻结，新证据进新文件；**不回去改旧 round doc**
- 结论被推翻时：
  1. 04-findings 里**删掉**旧结论，写入新结论（living）
  2. `ARCHIVE.md` 加一条 "原 X → 修订 Y → round Z" 映射（追溯史保留）
  3. 03-analysis/0N-*.md **保持原样**（那是 round N 跑出来的 evidence，改了就没意义）

**Why**：结论翻转在多轮调查里很常见。如果 4 个文件各有一份结论，翻转时你只会改一处，剩下三处继续流传。

### 2.2 Glossary · 变量 / 参数 / 术语的唯一定义源

**每个场景 doc 必须有 `glossary.md`**，包含：

| 段 | 内容 |
|---|---|
| §A 原始字段 | 列数仓原始字段 + 关键口径（例如 `charging_mode=3` 含义），pointer 指向数仓 schema |
| §B 派生字段 | dbt 派生字段的**精确计算式**（SQL 表达或公式），例如 `pir_solar_ratio = pir_solar_count / pir_events` |
| §C 关键业务术语 | 例如 "cohort_suspect" / "ads_suspect" / "popup_suspect" 三档的定义 + 规模量级 |
| §D 业务分类 / 分段 | severity band 的边界 + 实际分布；pattern type 的定义；age segment 的阈值 |
| §E 阈值参数 | 每个参数的 **默认值 + 含义 + 何时调** |
| §F 写法约定 | 同一字段不同简写的对应表（`battery_min` vs `bm` vs `bm_min_7d` 是一回事）|

**引用规则**：

- 其他文档看到第一次出现的术语/字段**必须**跨链到 glossary，例如 `cohort_suspect（见 [glossary §C.1](glossary.md#c1-cohort_suspect)）`
- glossary 里一个词**只定义一次**。新 round 如果发现口径不对，改定义 + 写入 ARCHIVE "口径修订"
- PR / MR 里涉及新术语时，reviewer 必须看到 glossary 更新

### 2.3 口径透明 · 每个定义写清 SQL / 计算公式

**规则**：

- `glossary §B 派生字段` 每条必须写**具体计算式**，不是自然语言解释
  - ❌ "`avg_solar_ratio` 是设备 7 天的平均太阳能比率"
  - ✅ "`avg_solar_ratio = AVG(pir_solar_ratio) OVER 7-day window, where pir_solar_ratio = pir_solar_count / pir_events`"
- 业务分段每条写**精确边界**（含 `<` 还是 `<=`）
  - ❌ "`critical` 是电量接近耗尽"
  - ✅ "`critical` = `battery_min_7d < 5`（严格小于 5，不包括 5）"
- Cohort / filter 每条写完整 filter 链
  - ❌ "`cohort_suspect` 是异常设备"
  - ✅ "`cohort_suspect` = 在 `solar_required` cohort（`model cohort_mean_ratio >= 0.5 AND cohort_devices >= 50`）内，该设备 7d `avg_solar_ratio < 0.2 AND events >= 10`"
- 用**代理指标**时**必须标注**："`unbind` 当前用 `late window (bind+30d+) 完全无设备状态上报` 作代理；正式 unbind 事件表待 DE 补齐"

**Why**：`case-study-kf226-solar.md` R3 Q25 vs R4 Q27 vs R5 Q31 "CG625-BD2 异常率" 三个数字分别是 1.08% / 0.009% / 14.78%，**三人三口径同一句话**，差 3 个数量级。如果每次都写清楚 SQL filter，跨 round 对比时一眼能发现"哦这个不可比"。

## 3. 每轮 Round 结束时的 SSOT/Glossary 自检

在 [loop-pattern.md](loop-pattern.md) Step 7 Consolidate 后，**进 Step 8 Revise 前必须通过下列自检**：

- [ ] 本轮是否推翻了 04-findings 里的某条结论？如果是：
  - [ ] 04-findings 是否**删除**了旧结论并写入新结论？
  - [ ] ARCHIVE.md 是否记了 "原 X → 修订 Y → round N" 映射？
  - [ ] 03-analysis/0N-*.md 是否**保持原样**（没改旧 round 的证据）？
- [ ] 本轮是否引入了新变量 / 字段 / 参数 / 业务分段？如果是：
  - [ ] glossary 相应段是否更新？
  - [ ] 定义是否写了**精确计算式** / 边界 / filter 链？
  - [ ] 用代理指标的话是否标注"代理 + 正式源待补"？
- [ ] 本轮结论文字里出现的术语，在 glossary 里**都能找到单一定义**？
- [ ] 本轮某个术语跟上一轮同名但不同口径？如果是：
  - [ ] 有没有 **rename** 避免歧义（例如 `suspect` → `cohort_suspect` / `ads_suspect` / `popup_suspect` 三档）？
  - [ ] 新增的命名是否进了 glossary §C？
  - [ ] ARCHIVE.md 是否记了 "术语 X 在 R≤N 指 A，R≥N+1 指 B，R<N 结论对照时要按旧口径"？

有任一项 NO，**不得进下一轮**。

## 4. 文件布局（推荐）

```
docs/scenarios/<scenario>/
├── README.md                   # 薄索引（指 04 和 glossary，不陈述结论）
├── 01-background.md            # 稳定：问题定义 / 目标 / 范围
├── 02-data-sources.md          # 稳定：原始字段 + 数据缺口（不含派生字段，派生进 glossary）
├── 03-analysis/                # 证据账本，append-only，每 round 一个文件
│   ├── README.md
│   ├── 01-round1-*.md
│   ├── 02-round2-*.md
│   └── ...
├── 04-findings.md              # ⭐ LIVING SSOT · 当前生效结论
├── 05-algorithm.md             # LIVING · 算法设计（引用 04 + glossary，不重复规则）
├── 06-deployment.md            # LIVING · 部署 / 灰度（引用 05，不重复算法）
├── glossary.md                 # ⭐ SSOT · 所有变量 / 参数 / 分段的唯一定义源
└── ARCHIVE.md                  # 撤回 / 修订映射表
```

## 5. 反例 vs 正例

### 反例

```markdown
# README.md
## 🎯 最新结论
1. 新装 7 天异常率 29.36%，胶塞假设成立
2. KF226 是主要问题机型

# 04-findings.md
## 强信号
- 新装 7 天异常率 29.36%

# 05-algorithm.md
## 输入
- 主信号: charging_mode=3
- Suspect 判据: avg_solar_ratio < 0.2
```

问题：
- 结论 "29.36%" 在 README + 04 两个地方出现 → R4 Q26 发现是 40.2% 时只改了 04，README 没改
- "suspect" 在 05 里写了定义但在 03-analysis/04-segments.md 里 Q10 用的又是另一个口径
- "battery_min" 在多个文档简写成 `bm`，没统一

### 正例

```markdown
# README.md
## 文档结构
| 类型 | 文件 | 说明 |
| SSOT | [04-findings.md](04-findings.md) | 现行结论 |
| SSOT | [glossary.md](glossary.md) | 变量/参数定义 |
| 历史 | [ARCHIVE.md](ARCHIVE.md) | 撤回结论映射 |

结论见 [04-findings](04-findings.md)。

# 04-findings.md §4.1
新装 <7d 异常率 = **40.2%**（源 [Q26](03-analysis/08-...)；定义 见 [glossary §D.3](glossary.md#d3-device-age-segment)）

# glossary.md §D.3
| segment | 条件 | 含义 |
| <7d | bind_age < 7（严格小于 7 天，不含 7）| 新装期 |

# ARCHIVE.md
| R2 "新装 7 天异常率 29.36%" | 修订为 40.2%（源 R4 Q26，存活偏差重测） |
```

所有文档只引用 SSOT，不复制；定义唯一；修订有追溯。

## 6. 与其他 skill 的关系

- `layered-doc-template.md`：布局基础；本文档给出纪律
- `hypothesis-discipline.md`：措辞三态（✅/❌/⏸）；本文档补"跨 round 定义漂移"陷阱
- `loop-pattern.md`：Step 7 后按本文档 §3 做自检
- `parallel-dispatch-guide.md`：派并行 agent 时 prompt 必须写清**本次用哪档 suspect 定义**，避免子 agent 各用各的口径
- `survivorship-bias-and-cohort-drift.md`：time-based cohort 的专题；本文档是**通用**纪律

## 7. 沉淀来源

本文档来自 services/customer-care#75 session 的真实教训（2026-04-19 到 2026-04-20 共 7 轮 EDA）：

- R3 Q25 / R4 Q27 / R5 Q31 三轮"CG625-BD2 异常率"分别报 1.08% / 0.009% / 14.78%，**三个不同口径被用作可比数字**，导致 R5 才发现是假信号
- R5 定稿 "other 55.94% 主战场"，R6 Q36 一个 solar_required 过滤就把它降到 13.68%，因为 R5 没把 filter 链写清口径
- R3/R4 的 04-findings.md 一直带着 R2 的 "KF226 29.36% 黄金窗口" 旧结论，R7 user 指出存活偏差后才发现一直没删
- 6 轮里 `battery_min` / `bm_min_7d` / `bm` 三种写法混用，reviewer 每次都要问"这是不是同一字段"

这些坑都在本 skill 补这份纪律之前发生。下一个 session 就应该一开始就按本文档执行。
