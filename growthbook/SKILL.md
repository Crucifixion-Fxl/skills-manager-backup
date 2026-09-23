---
name: growthbook
description: Query GrowthBook for A/B experiments, feature flags, and experiment results via REST API. Use when checking experiment status, toggling feature flags, viewing A/B test results, managing metrics, or any task involving experiments, feature flags, A/B testing, or GrowthBook.
---

# growthbook

通过 GrowthBook REST API 管理 A/B 实验、Feature Flag 和实验指标。API 用法通过 Context7 MCP 查询，此处只记录公司特有的规则。

## Description

适用场景：A/B 实验管理、Feature Flag 开关、实验结果分析、指标巡检。

- **前端地址**：`https://us-ab-management.addx.live`（UI）
- **API 后端地址**：`https://us-ab-management-api.addx.live`（REST API）

> 注意：前端和 API 后端是不同地址，API 请求必须发到 `-api` 子域名。

## 消费链路排查

当用户问的是“开关/实验为什么没生效”“客户端为什么没有变化”“这个 flag 到底有没有被消费”时，先走**消费优先**流程，再回到平台配置：

1. **先找消费点**
   - 搜索代码里的 flag / experiment 名称、别名、桥接参数、封装方法、常量
   - 优先确认 Flutter / iOS / Android / 服务端是否真的读取了这个值
2. **再追消费后行为**
   - 看默认值、fallback、缓存、异步刷新、环境分支、灰度条件、优先级
   - 区分“找到了消费方但未命中”与“当前仓库范围内未发现消费方”
3. **最后才回 GrowthBook 平台**
   - 校验环境、targeting、rules、experiment-ref、feature version、rollout 覆盖范围
   - 先消费链路，后平台配置，避免把“没人读”误判成“平台没配对”

> 这类问题的结论必须写清楚：`已消费` / `当前搜索范围未发现消费方` / `消费了但未生效`。

## Rules

### 认证

| 变量 | 说明 | 必需 |
|------|------|------|
| `GROWTHBOOK_API_KEY` | Secret API Key（Settings → API Keys） | 是 |

所有请求：`curl -s -H "Authorization: Bearer $GROWTHBOOK_API_KEY" "https://us-ab-management-api.addx.live/api/v1/..."`

### 分页注意

- 默认 `limit=10`，**必须显式设置** `limit=100`（最大值）
- `limit` 超过 100 会返回空结果
- 检查 `hasMore` 字段判断是否有更多数据，用 `offset` 翻页

### 三个环境

| 环境 | 说明 |
|------|------|
| `production` | 生产环境 |
| `staging` | 预发布测试 |
| `pre` | 预生产环境 |

所有 55 个 Feature Flag 都同时配置了这三个环境。

### SDK 连接

3 个 SDK 连接，按环境划分，命名为 `camera` / `camera-staging` / `camera-pre`。主要服务于摄像头 App 端。

### 项目（Projects）

| 项目 | 说明 |
|------|------|
| `ftgp` | 主业务项目（Free Trial Guide Page 相关） |
| `退订` | 退订相关实验 |
| `Sample Data` | 演示数据（可忽略） |

统计引擎均为 **Bayesian**。

### 实验概况（100+ 个）

- **48 running** / 48 stopped / 4 draft
- 实验主要围绕**订阅付费优化**（核心业务）

**实验命名规范**：`{功能}_{产品}` 或 `{日期}-{场景}-{设备类型}-{环境}`

| 命名模式 | 示例 |
|---------|------|
| `{功能}_{品牌}` | `cancel_subscription_vicohome`、`new_payment_package` |
| `{场景} 素材实验` | `home_no_1_bird 正式素材实验`、`0823新轮播素材实验` |
| `{日期}-{场景}-{设备}-{环境}-{折扣}` | `0119-2026 常态化-banner-鸟-单设备-prod-15%off` |
| `{功能} （staging/prod）` | `退订 staging 实验`、`喂鸟器素材实验-prod` |

**实验类型分布**：

| 类型 | 说明 |
|------|------|
| 营销素材实验 | 轮播图、Banner、弹窗的素材/文案 A/B 测试 |
| 定价实验 | 折扣力度（25%/30%/31%/35%/37%/43% OFF）、默认选中商品 |
| 功能开关实验 | 退订入口、Free Trial 天数、播放器性能 |
| 促销活动实验 | 黑五/Cyber Monday/节日营销 banner 和 popup |

### Feature Flag 概况（55 个）

| valueType | 数量 | 典型用途 |
|-----------|------|---------|
| `string` | 22 | 素材 ID / 方案配置 / 页面路由 |
| `boolean` | 18 | 功能开关 |
| `number` | 14 | 数值参数（天数、价格、配置） |
| `json` | 1 | 复杂配置（广告 splash config） |

**核心 Feature Flag**：

| Flag | 类型 | 说明 |
|------|------|------|
| `new_payment_package` | boolean | 新支付套餐 |
| `cancel_subscription` | boolean | 退订入口开关 |
| `home_no_1` / `home_no_1_bird` | string | 首页轮播图素材 |
| `awareness_free_trial_day` | number | Free Trial 天数 |
| `seven_days_free_trial` | number | 7 天试用配置 |
| `noonlight_plan` | boolean | Noonlight 安全服务 |
| `home_ai_chatbot` | boolean | 智能客服入口 |
| `person-pet-vehicle-detector` | boolean | 人/宠物/车辆检测 |
| `video-summary-provider` | string | 视频摘要 AI 供应商（默认 gemini） |
| `bird-summary-provider` | string | 鸟类摘要 AI 供应商（默认 gemini） |
| `admob_splash_config` | json | 广告开屏配置 |

### 数据源

| 名称 | 类型 | 说明 |
|------|------|------|
| My Datasource | Athena | 主数据源 |
| Snowplow | Athena | 事件追踪数据 |
| Sample Data Source | Postgres | 演示数据（可忽略） |

### 指标体系（100 个，64 个活跃）

| 类型 | 数量 | 说明 |
|------|------|------|
| `binomial` | 94 | 转化率类指标（是/否） |
| `revenue` | 3 | 收入类指标 |
| `count` | 2 | 计数类指标 |
| `duration` | 1 | 时长类指标 |

**核心指标命名前缀**：

| 前缀 | 说明 |
|------|------|
| `ftgp_*` | Free Trial Guide Page 漏斗指标（曝光→点击→支付） |
| `Banner *` | Banner 营销漏斗（曝光→点击→支付成功） |
| `弹窗*` | 弹窗营销漏斗 |
| `退订率` / `流失率` | 用户留存相关 |

### 操作红线

- **实验写操作（创建/停止/修改 variation）必须用户确认后才执行**
- Feature Flag toggle 前必须告知用户影响范围（哪个环境）
- **禁止**在 production 直接删除正在运行的实验
- 实验结果查看是只读操作，可直接执行无需确认

### 常见工作流

1. **查看实验状态**：`/api/v1/experiments?limit=100` → 按 `status` 筛选 → 查看详情
2. **分析实验结果**：获取实验结果 → 查看各 variation 指标 → `chance_to_beat_control > 0.95` 判定显著
3. **Feature Flag 管理**：`/api/v1/features?limit=100` → 检查 `defaultValue` 和各环境 rules → 确认后 toggle
4. **指标巡检**：`/api/v1/metrics?limit=100` → 检查指标定义（注意 36 个已归档）
5. **消费链路排查**：先查代码是否消费，再查平台配置是否命中目标环境 / 目标人群
6. **创建实验并关联 Feature Flag**（见下方陷阱和详细参考）：
   - Step 1：`POST /api/v1/experiments`（必填 `datasourceId=ds_405gzm1nlqc3zcjh`、`assignmentQueryId=user_id`、`trackingKey`）
   - Step 2：`POST /api/v1/experiments/{id}` 更新 phase，用 `condition` 字段设置定向条件（非 `targetingCondition`）
   - Step 3：读取 Feature Flag 完整规则 → 追加 experiment-ref 规则 → **全量写回**（API 是全量替换，非追加！）
   - Step 4：验证规则数量、内容，以及其他环境（尤其 production）未受影响

### 创建实验关键陷阱

| 陷阱 | 说明 |
|------|------|
| Feature Flag rules 全量替换 | `POST /features/{id}` 更新任一环境的 rules 时，会完全覆盖该环境所有已有规则，**必须先读取再追加写回** |
| phase condition 字段名 | 请求体用 `condition`（JSON 字符串），响应返回 `targetingCondition`；创建时传 `targetingCondition` 不生效 |
| experiment-ref 的 condition | 在 Feature API 层设置 experiment-ref 规则的 condition **不生效**，定向条件必须在实验 phase 层设置 |

详细流程和代码示例见 [references/REFERENCE.md](references/REFERENCE.md)。

## Examples

### Bad

```bash
# limit 超过 100 → 返回空结果
GET /api/v1/experiments?limit=200

# 直接 toggle 生产环境 Flag → 违反操作红线
POST /api/v1/features/cancel_subscription/toggle  {"environment": "production"}

# 只提交新规则 → 覆盖所有已有规则！
POST /api/v1/features/home_promo_popup -d '{"environments":{"staging":{"rules":[只有新规则]}}}'

# 创建实验时传 targetingCondition → 不生效
POST /api/v1/experiments -d '{"phases":[{"targetingCondition":"..."}]}'
```

### Good

```bash
# 列出所有 running 实验（必须加 limit=100）
curl -s -H "Authorization: Bearer $GROWTHBOOK_API_KEY" \
  "https://us-ab-management-api.addx.live/api/v1/experiments?limit=100" \
  | jq '.experiments[] | select(.status=="running") | {name, status}'

# 查看 Feature Flag 状态
curl -s -H "Authorization: Bearer $GROWTHBOOK_API_KEY" \
  "https://us-ab-management-api.addx.live/api/v1/features?limit=100" \
  | jq '.features[] | {id, valueType, defaultValue}'

# 安全更新 Feature Flag 规则：先读取，追加，全量写回
current_rules=$(curl -s -H "Authorization: Bearer $GROWTHBOOK_API_KEY" \
  "https://us-ab-management-api.addx.live/api/v1/features/home_promo_popup" \
  | jq '.feature.environments.staging.rules')
updated_rules=$(echo "$current_rules" | jq '. + [{"type":"experiment-ref","experimentId":"exp_xxx","enabled":true,"variations":[]}]')
curl -s -X POST -H "Authorization: Bearer $GROWTHBOOK_API_KEY" -H "Content-Type: application/json" \
  "https://us-ab-management-api.addx.live/api/v1/features/home_promo_popup" \
  -d "{\"environments\":{\"staging\":{\"enabled\":true,\"rules\":$updated_rules}}}"

# 创建实验后单独设置 phase 定向条件（用 condition 字段）
curl -s -X POST -H "Authorization: Bearer $GROWTHBOOK_API_KEY" -H "Content-Type: application/json" \
  "https://us-ab-management-api.addx.live/api/v1/experiments/exp_xxx" \
  -d '{"phases":[{"name":"Main","dateStarted":"2026-01-01T00:00:00.000Z","coverage":1,"condition":"{\"country\":\"US\"}","variationWeights":[0.5,0.5]}]}'
```
