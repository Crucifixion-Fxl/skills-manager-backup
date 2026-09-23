---
name: aws-sp-optimizer
description: 计算 AWS Org payer 账户在当前时刻的最优 1y no-upfront Compute SP 增购金额。基于 CUR 数据 + newsvendor 公式 (C* = quantile_d(X))，自动校验前置条件、处理趋势异常、给出可购买建议或风险提示。当用户问"应该买多少 Compute SP"、"SP 增购评估"、"SP 优化决策"、"核对 AWS Cost Explorer SP 推荐"时使用。
---

# AWS SP Optimizer

计算 AWS Org 在当前时刻的最优 1y no-upfront Compute SP 增购金额。

## 触发场景
- 用户问"应该买多少 Compute SP" / "SP 增购建议"
- 用户要核对 AWS Cost Explorer 的 SP 推荐
- 季度 cost optimization 时决策 SP
- 用户想排除某些机型/账户/tag 的 SP 增购评估（如 "g4dn 要切 spot，不要算进 SP"）

**不适用**：
- RI 推荐
- 3y SP / Partial Upfront / All Upfront
- SP 续约决策
- 自动执行购买

## 执行流程

### Step 0: 检查配置
```bash
ls ~/.config/aws-sp-optimizer/orgs.yaml 2>&1
```
- 不存在或为空 → READ `references/first-run-setup.md`
- 损坏 → READ `references/troubleshooting.md` 的 "config_invalid" 段
- 有效 → 继续 Step 1

### Step 1: 运行 optimizer
READ `references/usage.md`

### Step 2: 解读输出
READ `references/output-interpretation.md`

当用户传了 `--exclude-*` 参数，输出 `context.applied_exclude_filter` 会被填充；此时 `sp_coverage_share < 1.0` 是**预期行为**（现有 SP 的一部分正在覆盖被排除的 workload）。

### Step 3: 按 status 分支
- `status: "ok"` 且 `risks` 空 → READ `references/presentation-template.md`，按九段式模板渲染完整报告（默认含公式、变量证据链、求解过程、图示、名词表）
- `status: "ok"` 且 `risks[].must_review_before_acting: true` → READ `references/failure-handling.md` 的"风险点处理"段（严禁直接给购买建议）
- `status: "ok_with_adjustment"` → READ `references/presentation-template.md`，按九段式模板渲染；§7 必填 `adjustments_made[]` 内容
- `status: "needs_human_decision"` → READ `references/failure-handling.md` 的"硬失败处理"段
- `status: "needs_setup"` → 回 Step 0
- `status: "needs_cache_refresh"` → READ `references/cache-refresh-handling.md`

### Step 4: 趋势深度解释
输出 `baseline.trend_classification` 非 stable/increasing 时 READ `references/trend-handling.md`

### Step 5: 购买执行
用户决定购买时 READ `references/purchase-execution.md`

### Step 6: 公式深入追问
presentation-template §2/§6 已内联默认公式与最小推导。仅当用户追问 trend_aware 分支、d_blended 加权细节、或非平稳扩展等深入问题时，READ `references/formula-derivation.md` 补充。

### Step 7: 意外错误
READ `references/troubleshooting.md`

## 重要边界

- **不要**在 status != ok/ok_with_adjustment 时直接给购买建议
- **不要**在 `risks[].must_review_before_acting: true` 时跳过用户确认
- **不要**自动执行 `aws savingsplans create-savings-plan`
- **不要**自己改 `~/.config/aws-sp-optimizer/orgs.yaml`
- **不要**假设默认参数对所有场景都 OK
- **不要**在 `status == needs_cache_refresh` 时自动选择 `actions[]` 之一，必须等用户确认
- **不要**在用户没明确要求的情况下主动加 `--exclude-*` 参数。filter 是用户显式意图，不是推断。

## 快速参考

- 主命令：`python -m scripts.aws_sp_optimizer --org <alias>`
- 常用参数：`--window-days`、`--window-end`、`--prefer freshness|sample-size|balanced`
- 调试：`python -m scripts.aws_sp_optimizer --org <alias> 2>&1 >/dev/null`
- `--refresh-cache`：强制重建 shipped pricing cache（覆盖 `data/ratios.json.gz`）

## Examples

### ❌ Bad — 凭感觉给数字

用户：我应该增购多少 Compute SP？

错误响应（不跑 skill 直接猜）：
> 建议你买个 $50/hr 的 1y NU Compute SP 吧，差不多够用。

问题：
- 没有 CUR 数据支撑
- 忽略了现有 SP 的 effective coverage
- 没有计算 d_blended 或 quantile_d(X)
- 没有检查趋势（`must_review_before_acting` gate 绕过）
- 建议值可能严重偏离 newsvendor 最优

### ✅ Good — 走 skill 流程

用户：我应该增购多少 Compute SP？

正确响应：
```bash
python -m scripts.aws_sp_optimizer --org a4x-us
```

然后按 `status` 分支处理：
- `status: "ok"` / `"ok_with_adjustment"` → READ `references/presentation-template.md`，按九段式模板渲染完整报告（§1 决策 / §2 公式 / §3 X 证据链 / §4 d 证据链 / §5 C* 求解 / §6 推导 / §7 调整 / §8 风险 / §9 名词表）
- `risks[].must_review_before_acting: true` → READ `references/failure-handling.md`，**不**给购买建议
- `status: "needs_human_decision"` → READ `references/failure-handling.md` 的"硬失败处理"段
- `status: "needs_setup"` → READ `references/first-run-setup.md`

用户决定购买时才 READ `references/purchase-execution.md` 并呈现 `purchase_instructions` 里的命令块。**永远不自动执行 `aws savingsplans create-savings-plan`**。

### ❌ Bad — `needs_cache_refresh` 时自动选择

```json
{ "status": "needs_cache_refresh", "reason": "cache_stale_over_1year", ... }
```

错误响应（不等用户确认，直接执行）：
> 好的，我来帮你刷新 cache，运行 `--refresh-cache`…

问题：
- 未展示 `actions[]` 三个选项
- 未等用户确认就执行了有副作用的命令（会修改 `data/ratios.json.gz`）
- 用户可能更倾向于 `pull_from_git` 或 `skip_refresh_once`

### ✅ Good — 展示选项，等用户选

```json
{ "status": "needs_cache_refresh", "reason": "cache_stale_over_1year", ... }
```

正确响应：READ `references/cache-refresh-handling.md`，向用户呈现三个选项：

- A: `pull_from_git` — 若远端已有更新缓存，git pull 即可（最快）
- B: `rebuild_locally` — 本地重建 pricing cache，~5-15 分钟，然后 commit data/
- C: `skip_refresh_once` — 接受旧缓存风险，本次跳过刷新直接继续

等用户明确选择后再执行对应操作。
