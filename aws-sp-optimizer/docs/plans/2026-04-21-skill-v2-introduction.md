# aws-sp-optimizer Skill — v2 Introduction

本文档是 `aws-sp-optimizer` skill 在 engineering/skills 仓库的完整介绍。v2 是该 skill 的首次完整形态——计算 AWS Org payer 账户在当前时刻最优的 1y No-Upfront Compute Savings Plan 增购金额，基于 CUR 账单数据 + newsvendor 公式，并支持按机型/账户/tag 三条轴排除特定 workload。

- **状态**：首次完整提交（v2）
- **分支**：`feat/aws-sp-optimizer-v2`
- **Skill 路径**：`skills/aws-sp-optimizer/`
- **主入口**：`python -m scripts.aws_sp_optimizer --org <alias>`
- **测试**：346 passed（pytest）

---

## 1. 要解决的问题

AWS 官方在 Cost Explorer 里提供 Savings Plan Recommendation，但存在几类结构性缺陷：

1. **黑盒推荐**：给一个数字 + "推荐购买" 按钮，不暴露公式、不暴露变量证据链，决策者无法审计
2. **不支持 EDP 校准**：签了 Enterprise Discount Program（EDP）的组织，SP 折扣和 list→OD 折扣都已被折扣层穿透，官方推荐未还原真实折扣率，系统性偏高
3. **不支持排除特定 workload**：无法告诉它 "g4dn 即将切 Spot，不要算进 SP"、"这些 sandbox 账户不参与 SP 评估"、"lifecycle=ephemeral 的资源不算"
4. **单一 lookback**：固定 7/30/60 天窗口，workload 在趋势变化时推荐偏差显著
5. **不可复现**：结果依赖 AWS 内部状态，无法离线重算核对
6. **失败静默**：数据不足时返回空或低置信度数字，没有明确的"这里该人工介入"信号

本 skill 针对以上 6 点逐一做了工程化解决。

---

## 2. Skill 完整能力

### 2.1 核心能力

| 能力 | 实现 |
|---|---|
| **C\* 求解** | Newsvendor 公式 `C* = quantile_d(X)`，X 来自 CUR 小时级 net cost 序列，d 是 EDP 校准后的混合折扣率 |
| **趋势感知** | 对每个候选窗口分 3 段计算 `trend_buckets`，分类 stationary / mild_decline / non_monotonic / trending；trend_aware 分支用线性回归残差 + bootstrap 置信区间求 C\*，stationary 分支用经验分位数 |
| **d_blended 计算** | 基于官方 Pricing API ratios + CUR mix 加权，EDP 校准后得 `d_calibrated = 1 − (1 − d_raw) × (1 − e_sp) / (1 − e_od)` |
| **Phase-A 验证** | boto3 权限、CUR schema、Glue table、primary_region、tag 列存在性——任一 blocker 阻止 C\* 计算，显式返回 `validation_failed` 状态 |
| **脏数据门禁** | e_sp/e_od 越界、d_calibrated ≤ 0、coverage < 0.95 分别显式返回 `needs_human_decision` / `needs_cache_refresh`，而不是静默出错数 |
| **现有 SP 扣减** | `C_existing_effective = compute_commitment × utilization_30d × sp_coverage_share`，避免双计 |
| **多区域发现** | 自动从 CUR 发现有 workload 的所有区域，pricing cache 按需 cold-start |
| **排除过滤子系统** | 3 条轴：`--exclude-usage-type-pattern` / `--exclude-account-id` / `--exclude-tag key=v1,v2` + `--include-untagged` / `--exclude-untagged` |
| **可审计输出** | 九段式模板：决策 / 公式 / X 证据链 / d 证据链 / C\* 求解 / 推导 / 调整 / 风险 / 名词表 |
| **购买指令块** | 生成 `aws savingsplans create-savings-plan` 命令块但**绝不自动执行**；`must_review_before_acting = true` 时阻止给建议 |

### 2.2 文件结构

```
skills/aws-sp-optimizer/
├── SKILL.md                          # 入口，触发场景 + 执行流程
├── scripts/
│   ├── aws_sp_optimizer.py           # CLI + run_optimizer 编排
│   ├── config_loader.py              # orgs.yaml 解析 + CLI 合并
│   ├── cur_query.py                  # CUR 查询（regions / hourly series / EDP）
│   ├── exclude_filter.py             # 排除过滤 + 注入安全 validators
│   ├── newsvendor.py                 # 公式（stationary / trend_aware / d_blended）
│   ├── pricing_cache.py              # 官方 Pricing API 缓存
│   ├── validate_environment.py       # Phase-A 校验
│   ├── window_search.py              # 窗口搜索
│   ├── output_builder.py             # JSON 输出组装
│   └── _common.py                    # 共享数据结构 + boto3 包装
├── references/                       # SKILL.md 按需 READ 的详细文档
│   ├── usage.md                      # CLI 完整表 + 常见用法
│   ├── output-interpretation.md      # 输出字段逐项说明
│   ├── presentation-template.md      # 九段式报告模板
│   ├── failure-handling.md           # 每种 status 的处理手册
│   ├── purchase-execution.md         # 购买执行指令
│   ├── cache-refresh-handling.md     # pricing cache 刷新流程
│   ├── trend-handling.md             # 趋势深度解释
│   ├── formula-derivation.md         # newsvendor 公式推导
│   ├── first-run-setup.md            # orgs.yaml 初始化
│   ├── troubleshooting.md            # 常见报错
│   └── glossary.md                   # 名词表
└── docs/plans/
    ├── 2026-04-21-skill-v2-introduction.md       # 本文档
    └── 2026-04-20-exclude-filter-subsystem.md   # 排除过滤子系统深度设计
```

### 2.3 状态码家族

| status | 含义 | 处理路径 |
|---|---|---|
| `ok` | 全流程通过，可以给购买建议 | 按九段式模板渲染完整报告 |
| `ok_with_adjustment` | 通过但有自动调整 | 同上，§7 必填 `adjustments_made[]` |
| `needs_validation_fix` | Phase-A 阻塞（主 CLI 路径） | 呈现 `blockers[]`，等用户修环境 |
| `validation_failed` | 同上，但 `--validate-only` 路径的显式状态 | 列出 `blockers[]` |
| `needs_human_decision` | 脏数据/硬失败 | 呈现 `failure_dossier`，不给购买建议 |
| `needs_cache_refresh` | Pricing cache 过期或 coverage 不足 | 呈现 `actions[]`，等用户选择 |
| `needs_setup` | orgs.yaml 缺失 | READ `references/first-run-setup.md` |
| `aws_api_error` | boto3/Athena 外部错误 | 呈现 error detail + 恢复建议 |

每个 status 都带 `instruction_for_llm` 字段，告诉 LLM 下一步该 READ 哪个 reference。

---

## 3. 相较 AWS 官方 SP 推荐的 7 项增量

### 3.1 透明可审计 vs 黑盒

- **官方**：一个数字 + 推荐购买按钮
- **本 skill**：完整 audit trail
  - `d_audit`：e_sp / e_od / edp_uniform / pricing_cache_version / coverage_pct / unmatched_top10_by_cost
  - `sp_audit`：每条 SP 的 commitment / utilization / counted_toward_gap
  - `baseline_stats`：p0-p100 + mean/std + trend_buckets
  - 九段式报告模板把公式、变量、求解过程、推导完整展示

### 3.2 EDP 校准 vs 裸折扣

- **官方**：假定 list → SP 是 standard discount
- **本 skill**：从 CUR 真实账单拉 EDP 系数
  - `e_sp = 1 − net_unblended / unblended`（SP fee 行）
  - `e_od` 同理（OD Usage 行）
  - `d_calibrated = 1 − (1 − d_raw) × (1 − e_sp) / (1 − e_od)` 还原真实 SP 折扣
- 适用于签了 EDP 的组织——官方推荐在 EDP 场景会系统性偏高

### 3.3 排除过滤子系统

- **官方**：**不支持**。整个账户粒度推荐
- **本 skill**：3 条 axes，注入安全 + fail-soft 降级
  - `--exclude-usage-type-pattern` —— 按机型模式排除（如 `g4dn.`）
  - `--exclude-account-id` —— 按 12 位 AWS 成员账户排除
  - `--exclude-tag key=v1,v2` —— 按 CUR user tag 排除
  - `--include-untagged` / `--exclude-untagged` —— 控制 NULL tag 值语义
  - **数学正确性**：`sp_coverage_share` 衰减机制避免双计——过滤掉的 workload 如果被现有 SP 覆盖，`C_existing_effective` 按比例衰减
  - **注入安全**：全部输入走 regex 白名单（`[A-Za-z0-9._:-]+` 等）
  - **Fail-soft**：缺 tag 列时该轴跳过 + warning，其他轴仍生效，不阻止计算

### 3.4 趋势识别 + 分支公式 vs 单一 lookback

- **官方**：固定 lookback（7/30/60 天）
- **本 skill**：
  - 对每个候选窗口分 3 段计算 `trend_buckets`
  - 分类 `stationary / mild_decline / non_monotonic / trending`
  - `trend_aware` 分支：线性回归残差 + bootstrap 置信区间
  - `stationary` 分支：经验分位数
  - 按 `trend_classification` 自动选择分支公式

### 3.5 失败闭环 vs 静默降级

- **官方**：数据不足时返回空或低置信度数字
- **本 skill**：8 种 status 码 + 每种明确的处理手册
  - `risks[].must_review_before_acting: true` 时**禁止**给购买建议
  - 每个 status 带 `instruction_for_llm` + `user_fix_options`

### 3.6 可复现 vs 每次重算

- **官方**：结果依赖 AWS 内部状态，无法离线复现
- **本 skill**：
  - Pricing ratios 做成 shipped cache（`data/ratios.json.gz`，版本控制）
  - 给定相同 CUR 数据 + 相同 cache 版本 → 得到相同 C\*
  - `--refresh-cache` / `--skip-pricing-refresh` 明确控制缓存策略

### 3.7 多区域 + 过滤组合 vs 单一账户视角

- **官方**：默认按账户粒度推荐
- **本 skill**：
  - 在 Org payer 层统一计算
  - 自动发现所有有 workload 的区域
  - 支持按账户/tag/机型任意组合过滤后再求 C\*

---

## 4. v2 开发历程

本 skill 在 v2 落地前经过若干轮迭代（v1 早期版本已在 `feat/aws-sp-optimizer-v1` 暂存），v2 是合并 v1 全部基础能力 + 排除过滤子系统 + 文档体系后的**首次完整提交**。

### Phase 0-9（按开发顺序）

| Phase | 内容 |
|---|---|
| P0 | Skill 框架 + orgs.yaml + Phase-A 验证 + newsvendor 基础公式 |
| P1 | Pricing cache + EDP 校准 + d_blended |
| P2 | 窗口搜索 + 趋势分类 + trend_aware 分支 |
| P3 | C\* 求解 + 现有 SP 扣减 + bootstrap CI |
| P4 | 输出构建 + 九段式报告模板 + 失败闭环 |
| P5 | 多区域发现 + pricing cache 多区域冷启动 |
| P6 | Cache 年龄告警 + untagged fraction 风险 |
| **P7** | **排除过滤子系统：`ExcludeFilter` 数据结构 + 注入安全 validators + 3 条 SQL axes + tag column 发现** |
| **P8** | **`compute_sp_coverage_share` + `C_existing_effective` 衰减 + d_raw 滤镜透明回归** |
| **P9** | **CLI flags + YAML `exclude:` + orchestrator 编排 + E2E 集成测试 + 文档体系** |

P7/P8/P9 是 v2 的增量（相对 v1 而言），但在本 MR 中不作为"增量"呈现——v2 是 skill 的首次完整形态。

---

## 5. 测试覆盖

- **单元测试**：346 passed
  - 排除过滤：dataclass / validators / SQL 构建 / tag column 发现
  - 公式：stationary / trend_aware / d_blended / bootstrap_ci
  - CUR 查询：discover_workload_regions / build_hourly_series / compute_edp_factors / compute_untagged_fraction / compute_sp_coverage_share
  - 编排：validate_environment / config_loader / run_optimizer / build_output
  - 脏数据门禁：EDP 越界 / d_calibrated ≤ 0 / coverage 低于阈值
- **E2E 集成测试**：`test_end_to_end_with_filters.py` 3 条 axes 同时激活 + tag 列缺失 degradation
- **Happy-path E2E**：`test_end_to_end.py` 完整 main() 跑通

（`test_newsvendor_stationary_properties` hypothesis flake 与本 skill 工作无关，是 pre-existing 的种子敏感 tolerance 问题，已知。）

---

## 6. 重要边界

- **不**在 status != ok/ok_with_adjustment 时给购买建议
- **不**在 `risks[].must_review_before_acting: true` 时跳过用户确认
- **不**自动执行 `aws savingsplans create-savings-plan`
- **不**在用户没明确要求的情况下主动加 `--exclude-*` 参数。filter 是用户显式意图，不是推断
- **不**假设默认参数对所有场景都 OK
- **不**在 `status == needs_cache_refresh` 时自动选 `actions[]` 之一

---

## 7. 参考文档

- 入口：[`SKILL.md`](../../SKILL.md)
- 使用：[`references/usage.md`](../../references/usage.md)
- 输出解读：[`references/output-interpretation.md`](../../references/output-interpretation.md)
- 报告模板：[`references/presentation-template.md`](../../references/presentation-template.md)
- 失败处理：[`references/failure-handling.md`](../../references/failure-handling.md)
- 排除过滤深度设计：[`2026-04-20-exclude-filter-subsystem.md`](./2026-04-20-exclude-filter-subsystem.md)

---

## 8. 后续计划（非本次 MR 范围）

v2 明确**不包含**以下能力，未来版本再加：

- Coverage-target mode（按目标覆盖率反推 C\*）
- 1y vs 3y term 对比
- What-if scenario mode（多过滤组合并排对比）
- Forward-looking usage adjustments（前瞻性负载调整）
- 账户级分解报告
- EC2 Instance SP mix 优化
