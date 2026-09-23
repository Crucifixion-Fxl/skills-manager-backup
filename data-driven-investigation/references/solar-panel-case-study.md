# Case Study: Solar Panel / KF226 投诉归因 — 8 轮 EDA 完整演进（2026-04-20 v2）

> v2 升级自 [case-study-kf226-solar.md](case-study-kf226-solar.md)（v1 覆盖 R1-R3）。v2 覆盖**全 8 轮 + v0.3→v0.4→v0.5 业务重构 + Stage 2.0 / Stage 3 lifecycle gap**。
>
> 原始证据链：customer-care issue #75 `docs/scenarios/smart-popup/solar-panel/`；方法论沉淀：同目录 `docs/methodology/solar-analysis-playbook.md`。

---

## 问题起源

客服反馈：kiwibit KF226 摄像机用户差评集中在"太阳能板坏了"。PM 猜测根因是**插头胶塞过紧**导致用户未插到位。需要数据驱动验证并设计 SmartPopup 拦截。

初始假设（2026-04-19）：

```
原假设：KF226 太阳能充电不良是因为胶塞插不紧。受影响用户群可通过数据识别。
证据来源：客服工单 + PM 直觉
可证伪条件：
  - KF226 异常率接近 / 低于同级 cohort → 推翻胶塞假设或 KF226 归因
  - 异常用户分布均匀无模式 → 假设需细化
```

---

## Round-by-Round 演进（8 轮全景）

| Round | 日期 | 核心动作 | 产出 / 纠偏 |
|---|---|---|---|
| R1 | 04-19 | baseline cohort 验证 | 1,549 suspect / 4 数据缺口（solar_sn / chg_v_event / is_solar / KF226 model_no）发现 |
| R2 | 04-19 | pattern / segment EDA | PIR 撞 bytes 限额 → **升级 dws 中间层** MR !3037/!3038 |
| R2.5 | 04-19 | 基础设施上线 | dws 709K 行落库，R3 可重跑 |
| R3 | 04-19 | is_solar + deep-dive Q14-Q20 | "KF226 归因不成立" → ⏸ **暂无法验证**（识别缺失纪律） |
| R4 | 04-19 | cohort_suspect 统一口径 | Q26 产品反转："14d filter 是 trap"；Q29 "AI 全 0" 被 R5 Q32 证伪 |
| R5 | 04-19 | Q31-Q35 统一口径 rerun | Q25 "17× baseline" 撤回；ads 不是 ground truth |
| R6 | 04-19 | Q36-Q40 真 outlier 重排 | "CG625-BD2 族" 撤回；跨 CG/CQ/SS 三族 |
| R7 | 04-20 | **存活偏差量化** | User 一句话推翻 6 轮解读；**v0.4 业务重构** |
| R8 | 04-20 | KF226 identity 最终澄清 | V1→V2→V3 三版纠偏；KF226 = KF126A-A4XG1 硬件同批次 |
| R8+ | 04-20 | v0.5 Stage 1 落地 + Q51 | **snapshot gap 暴露** → Stage 3 stateful lifecycle |

---

## 反转链（session 最贵的教训）

### 反转 1 · KF226 Identity — 三版演进（R1 → R8）

| 版本 | 结论 | 为什么错 |
|---|---|---|
| PM-1 (2026-04-19) | "KF226 是太阳能相机" | ❌ 实际是喂鸟器（category=11/14） |
| V1 (R8 Q44 agent) | "KF226 = KF126A release name" | ⚠️ 措辞粗糙；dim 无 cross-map |
| V2 (我 Q48 后) | "KF226 和 KF126A 无关" | ❌ 过度绝对化，忽略 A4XG1 硬件同批次 |
| V3 (2026-04-20 User) | "硬件同 A4XG1 PCB 批次，dim 独立 model_no" | ✅ 最终 |

**教训**：Subject identity 不明时，假设状态 = `⏸ 暂无法验证`，不是 `❌ 证伪`。业务 SKU ↔ 内部 model_no **必须建映射表**（见 [ssot-and-glossary-discipline.md](ssot-and-glossary-discipline.md)）。

### 反转 2 · "时间效应" → 存活偏差（R4 → R7）

R4 Q26：新装 <7d 异常率 40.2% → ≥90d 稳态 13.5%，单调递减 → 解读为"设备稳定下来"。  
R7 User 一句话："新用户失败率高，是因为失败了就会去退货！" → Q41 量化：abnormal 组 30d 失联率 **17.88% vs healthy 4.72% = 3.79×** → 失败设备根本熬不到 ≥14d cohort。

**教训**：任何 `age_bucket × rate` 单调曲线结论前**必须先跑退出事件检查**。已沉淀为 [survivorship-bias-and-cohort-drift.md](survivorship-bias-and-cohort-drift.md) + skill MR !258。

### 反转 3 · "CG625-BD2 17× baseline" → 伪信号（R3 → R5/R6）

R3 Q25 在 ads 全量跑得 "CG625-BD2 1.08% vs other 0.06% = 17×"。  
R5 Q31 用 **cohort_suspect 真口径**：BD2 14.78% vs other 55.94% → BD2 反而是**低** outlier。  
R6 Q36 进一步用 solar_required 过滤后：other=13.68% ≈ BD2 12.84%，没有显著差异。

**教训**：ads 表是**产品决策后样本**，不是 ground truth。口径漂移在 session 里极易发生，必须建 suspect 三态词表（见 [ssot-and-glossary-discipline.md](ssot-and-glossary-discipline.md)）。

### 反转 4 · 硬阈值 → 自适应（R5 → v0.5/Stage 2）

R5 Q31 用 `cohort_mean_min=0.5` 硬阈 → 产生 "other 55.94% 主战场" 伪信号（非太阳能机型污染）。R8 Q44/Q47 实测：

- feeder_bird segment median=0.725，MAD_norm=0.09 → 适合 `median − k·MAD` 公式
- outdoor_camera segment median=0.191，MAD_norm=0.26（双峰）→ 必须 `p75` fallback
- 硬阈 0.5 会把 KF126 整类排除，漏检所有喂鸟器

**教训**：阈值**分布形态感知**。已沉淀为 [adaptive-thresholds-discipline.md](adaptive-thresholds-discipline.md) + skill MR !261。

### 反转 5 · snapshot 漏报最严重异常（R8+ Q51）

v0.5 Stage 1 ads 按 7d snapshot 重算：7d 无上报 → 从候选池删除。  
Q51 查 `KF126A-A4XG1` 43 台激活设备：**34 台（79%）7d 完全无上报** → solar dead → battery 耗尽 → offline → 从 snapshot 消失 → SmartPopup 认为"没问题" → 最该救的人群被漏掉。

**教训**：异常信号 = **持续状态**，不是 daily snapshot。必须 stateful sticky flag（`lifecycle_status` 状态机 + `offline_suspected`）。设计落地于 [DATA/dbt!3057](https://gitlab.addx.ai/DATA/dbt/-/merge_requests/3057)。

---

## 业务目标的三次重构（v0.3 → v0.4 → v0.5+）

SmartPopup 的业务定位随 EDA 深入**两次重构**（每次都是 User 一句话推动）：

| 版本 | 业务目标 | 成功指标 | 触发策略 | 推动 Round |
|---|---|---|---|---|
| **v0.1-v0.2** | 找异常设备推 RMA / 自查 | 弹窗命中率 / critical 召回率 | `≥14d 主路 + <7d critical 旁路` | R1-R6 |
| **v0.3** | 在退货 / unbind 前拦截新装失败 | <7d 失败设备 30d unbind 率降幅 | `D3/D7/D14/D30 四段递进` | R7 存活偏差 |
| **v0.4** | 把不可避免的退货引导到自家 RMA 通道 | `direct_rma_win_rate >= 50%` | D14/D30 主打"比 Amazon 快的直接换新" | R7 商业层升级 |
| **v0.5+** | **产品质量风险早期预警系统** | 新品 6 月存活率 + BSR 稳定度 | `is_new_product_risk` tier-1 急迫通道（D1/D3 + 客服外呼）| 04-20 kiwibit 叙事 |

### v0.4 核心：Amazon 退货的三层复利成本

1. **退货率指标打击** — Amazon `return rate` 超类目中位 → 算法惩罚 → Buy Box / 广告受限
2. **自然流量下降** — BSR / 搜索排名受退货率 + 差评连坐 → 流量跌 20-40%，反弹 3-6 个月
3. **差评扩散** — 每颗星 ~10-15% conversion 损失 + 首屏负面

v0.4 目标：`direct_rma_win_rate` 从 baseline <20% → **>= 50%**。

### v0.5+ 核心：小 cohort 更敏感

漏报代价 = 产品死；误报代价 = 用户稍微被打扰。不对称代价结构决定：

- `size_tier=micro`（<50 台）**不保护，急救**（反传统统计保守思维）
- `is_new_product_risk=true`（`cohort_age<60d + devices ∈ [5,200] + suspect_rate>40%`）覆写 micro 门槛
- `cohort_devices >= 5` 起就出信号（不是 50）

### v0.5+ kiwibit 是最清晰 case

- **KF126A `cohort_mean_ratio=0.008` vs segment median 0.837 = 93× 偏离** → 产品线级异常（`mismatch_investigate`）
- **KF226 43 台未量产 + 即将 retail** → 新品期典型
- **34 台 dormant** → snapshot 漏报死亡螺旋示范
- **BC11110B 29 台新相机 SKU** → 换代新品伴生 case

---

## Stage 2.0 / Stage 3 Infrastructure 升级

Session 末尾推进的两条 dbt MR，都是"EDA 发现的结构性 gap"驱动的基础设施升级：

| Stage | MR | 解决什么 | 关键字段 |
|---|---|---|---|
| Stage 1 | !3040/!3041 → !3050 | v0.5 ads 建立（cohort_segment + size_tier + product_line_status 列占位） | `cohort_segment` / `size_tier` |
| **Stage 2.0** | !3054 | adaptive threshold 判定（Layer 0 产品线判定 + is_new_product_risk + micro 放宽）| `product_line_status`（真判定）/ `is_new_product_risk` |
| **Stage 3** | !3057 | stateful lifecycle（sticky flag + offline_suspected） | `ads_device_lifecycle_state_df` / `lifecycle_status` |

**教训**：EDA session 应该能**在线升级数据基础设施**，不是分析→发现→写报告，而是分析→发现→推 dbt MR→重跑。本 session 推了 4 个 dbt MR + 2 个 skill MR + 1 个 docs MR。

---

## 本 case 沉淀为 skill 的 anti-patterns

本次 session 的 8 轮教训已在 skills 仓陆续沉淀：

| # | 教训 | 对应 skill 产出 |
|---|---|---|
| 1 | 数据缺口要在 Step 4 Assess Data 显性评估 | SKILL.md Step 3/4 + Rigid Checklist |
| 2 | 措辞 ⏸ ≠ ❌（KF226 归因 3 次纠偏） | [hypothesis-discipline.md](hypothesis-discipline.md) anti-pattern #3 |
| 3 | 方法选型走 3 路（catalog + 独立思考 + WebSearch） | [method-selection-guide.md](method-selection-guide.md) |
| 4 | ads 表是产品决策后样本**不是 ground truth** | [hypothesis-discipline.md](hypothesis-discipline.md) + suspect 三态词表 |
| 5 | 并发 agent × rate limit 防护 | [parallel-dispatch-guide.md](parallel-dispatch-guide.md) + memory `feedback_cron_rate_limit_protection.md` |
| 6 | ⭐⭐ 时间 cohort × 单调趋势 → 必查存活偏差 | [survivorship-bias-and-cohort-drift.md](survivorship-bias-and-cohort-drift.md) + MR !258 |
| 7 | SSOT / glossary 纪律（口径漂移防护）| [ssot-and-glossary-discipline.md](ssot-and-glossary-discipline.md) + MR !259 |
| 8 | 阈值分布形态感知（不写死常量）| [adaptive-thresholds-discipline.md](adaptive-thresholds-discipline.md) + MR !261 |
| 9 | Subject Identity Check / 业务 SKU ↔ model_no 映射 | （待后续 skill 补丁候选） |
| 10 | Stateful vs snapshot：异常信号是持续状态 | （待后续 skill 补丁候选） |
| 11 | 业务目标反问（Amazon 复利成本）/ 新品风险早期预警 | （customer-care 侧 `docs/methodology/solar-analysis-playbook.md` §7/§8）|

#9/#10/#11 是本 case study v2 新暴露的结构性教训，后续可作新 skill MR 候选。

---

## Skill 使用序列（按 Round 真实动作）

| Round | 动作 | invoke skill |
|---|---|---|
| 0 | 查 "solar charging" 相关字段 | `addx:datahub-schema-search` |
| 1 | 跑 cohort baseline（5 并行 agent） | `addx:superset` SQL Lab + `superpowers:dispatching-parallel-agents` |
| 1 | 4 数据缺口发现 | `auto-memory` 写 project 记忆 |
| 2 | PIR 撞 bytes 限额 | `addx:gitlab-mr` 推 dbt !3037/!3038 |
| 2 | 异常用户清单固化 | `addx:superset` 建 dataset 2799 + dashboard 476 |
| 2 | 数据缺口需 DE 跟进 | `addx:gitlab-issue-sop` 建 issue #75 |
| 3 | "KF226 归因不成立" 纪律校准 | `auto-memory` 写 `project_solar_popup_cross_tenant.md` |
| 4-6 | 统一 cohort_suspect 口径 | 全部 Round 用同一 glossary §C |
| 7 | 存活偏差 | `WebSearch`（业界 survivorship 检验方法）+ `addx:gitlab-mr` skill !258 |
| 8 | KF226 三版纠偏 | `auto-memory` 改写 `project_kf226_model_dim_unregistered.md` |
| 8+ | v0.5 Stage 1 / Stage 2 / Stage 3 推进 | `addx:gitlab-mr` !3050/!3054/!3057 |
| 固化 | 方法论 playbook | customer-care docs/methodology/solar-analysis-playbook.md |

---

## 下一轮分析（solar v2）5 + 11 步起点

本 case study 对应的 **playbook 11 步 checklist**（完整见 customer-care `docs/methodology/solar-analysis-playbook.md §11`）：

1. 读 glossary §A-§D / §C.4（SKU 映射）/ §E（阈值）/ playbook
2. 业务目标反问 + 定义"成功"（不是 CTR）
3. Subject Identity Check + SKU↔model_no 映射
4. 数据源选对（优先 status，不是 PIR）+ sanity-check SQL
5. Cohort 自适应 + size_tier 感知小 cohort + product_line_status 三档
6. 3 路找方法 + 备选方案 + 已知局限
7. 每结论打 ✅ / ❌ / ⏸
8. 单调趋势必查存活偏差
9. snapshot ≠ truth，stateful lifecycle 是终态
10. 新品早期预警意识
11. LIVING SSOT 不复制 + issue note 定期给 PM/stakeholder 看

---

## 与 v1 的关系

[case-study-kf226-solar.md](case-study-kf226-solar.md) v1 保留，专讲**前 3 轮**（基础 loop pattern 如何展开 + 方法选型 + 数据缺口暴露）。v2（本文档）是**全景**（8 轮 + v0.3/v0.4/v0.5+ 三次业务重构 + Stage 2.0/Stage 3 infrastructure 演进 + 5 次反转链 + 11 条教训）。

**新读者建议**：先读 v1 了解"一个 Round 长啥样"，再读 v2 理解"session 级演进和反转"。
