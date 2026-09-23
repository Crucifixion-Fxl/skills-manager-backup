# Naturehood BI 产出示例

> Canonical eval 示例；聚合数字不是当前生产事实。它演示实际读数后的报告形态。

📊 VicoNature 订阅页头图实验复盘 · 2026-09-16

问题：头图 treatment 是否不仅提高 Free Trial 开始率，也提高最终付费转化？
覆盖：cached asset recheck + fresh Chart data · 2026-09-09—09-15 UTC · control 1,800 / treatment 2,000

## 一分钟结论

1. ✅ Free Trial 开始率从 8.0% 升至 10.0%，`+2.0pp`（95% CI `+0.17—+3.83pp`，`p≈0.032`）；数据支持“头图促进开始试用”这一背景假设。
2. ⏸ 主要业务指标付费转化为 4.0% vs 4.4%，`+0.4pp`（95% CI `-0.88—+1.68pp`，`p≈0.540`），现有样本不能证明付费改善。
3. 试用后的付费完成率为 50% vs 44%，`-6.0pp`，但区间跨 0；这是需要继续观察的漏斗质量信号，不是已确认伤害。

## 背景与待验假设

- [冻结 Channel 背景](../evals/fixtures/naturehood-bi-insight.md#channel-and-issue-background)：`fixture-product-lead` 于 `2026-09-09 03:20 UTC` 认为头图更清楚，预计提升 Free Trial 开始率，同时担心新增试用是否转成付费。
- [Issue #167](https://gitlab.addx.ai/applications/naturehood/-/issues/167) 和 `jchen` 于 `2026-09-11 01:23 UTC` 发布的 [note 595510](https://gitlab.addx.ai/applications/naturehood/-/issues/167#note_595510) 只提供任务与历史发现范围，不作为效果证据。

## 数据事实与 insight

| 指标 | control | treatment | 差异 | 判定 |
|:-----|--------:|----------:|-----:|:-----|
| Free Trial 开始率 | 144/1,800 = 8.0% | 200/2,000 = 10.0% | +2.0pp | ✅ 数据支持领先指标改善 |
| 付费转化／曝光 | 72/1,800 = 4.0% | 88/2,000 = 4.4% | +0.4pp | ⏸ 区间跨 0，主要结果未验证 |
| 试用后付费完成率 | 72/144 = 50.0% | 88/200 = 44.0% | -6.0pp | ⏸ 方向偏弱但样本不足 |

Insight：treatment 更擅长让用户开始试用，但暂未证明创造更多付费；新增试用可能包含较低意向用户。当前最有价值的决策不是“立即全量”，而是继续按主要指标收样，并补看退款和留存，防止只优化漏斗上游。

## 现在要做

- 保持当前实验分流；付费转化差异的 95% CI 下界高于 `0pp` 才建议全量，上界低于 `-0.5pp` 才因伤害停止，否则收样到每组 `4,000` 曝光后再评估。不要用 Free Trial 开始率单独决定全量。
- 增加退款与首周期留存作为 guardrail；当前数据无法判断新增试用的长期价值。
- 下一轮优先检查来源渠道和新老用户切片，验证低意向流量是否集中在特定 segment。

## 数据证据

- [Dashboard 665](https://superset-us.addx.live/superset/dashboard/vico-nature-paywall-header-image-production/)：本轮 Chart data 聚合来源。
- [GrowthBook experiment](https://us-ab-management.addx.live/experiment/exp_iilm21mtr59thn)：assignment、phase 与 variation 定义。
- [冻结聚合回执及完整口径](../evals/fixtures/naturehood-bi-insight.md#data-read-receipts)。

边界：这是离线 eval 示例；没有退款、留存或其他同期实验数据。“数据支持 Free Trial 开始率改善”不等于已经证明长期收入提升。
