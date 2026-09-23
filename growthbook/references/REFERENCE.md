# GrowthBook API 操作详细参考

## 消费优先的排查原则

当现象是“开关已存在但行为没变”时，先确认代码是否消费，再看平台配置：

1. 先搜索代码里的 flag / experiment 名称、别名、桥接参数和封装方法
2. 如果找到消费点，继续追默认值、缓存、fallback、环境分支和灰度条件
3. 如果当前搜索范围内找不到消费点，先结论为“未发现消费方”，不要直接写成“平台没生效”
4. 最后再回到 GrowthBook 平台校验 environment / rules / experiment-ref / rollout 目标

> 结论写法建议：`已消费` / `当前搜索范围未发现消费方` / `消费了但未生效`

## 创建实验的完整流程

### Step 1：创建实验对象

```bash
curl -s -X POST \
  -H "Authorization: Bearer $GROWTHBOOK_API_KEY" \
  -H "Content-Type: application/json" \
  "https://us-ab-management-api.addx.live/api/v1/experiments" \
  -d '{
    "name": "实验名称",
    "trackingKey": "experiment_tracking_key",
    "datasourceId": "ds_405gzm1nlqc3zcjh",
    "assignmentQueryId": "user_id",
    "hashAttribute": "userId",
    "hashVersion": 2,
    "variations": [
      {"key": "0", "name": "Control"},
      {"key": "1", "name": "Variation 1"}
    ]
  }'
```

- `datasourceId`、`assignmentQueryId`、`trackingKey` 为**必填字段**，否则返回 400
- 实验创建后默认为 `draft` 状态

### Step 2：设置定向条件（通过更新 phase）

定向条件在实验的 **phase** 中设置，使用 `condition` 字段传入：

```bash
curl -s -X POST \
  -H "Authorization: Bearer $GROWTHBOOK_API_KEY" \
  -H "Content-Type: application/json" \
  "https://us-ab-management-api.addx.live/api/v1/experiments/{experimentId}" \
  -d '{
    "phases": [{
      "name": "Main",
      "dateStarted": "2026-01-01T00:00:00.000Z",
      "coverage": 1,
      "condition": "{\"country\": \"US\", \"isUserVip\": true}",
      "variationWeights": [0.5, 0.5]
    }]
  }'
```

> **关键**：API 请求体中使用 `condition` 字段（JSON 字符串），API 返回时映射为 `targetingCondition`。创建实验时传入 `phases.targetingCondition` 不会生效，必须在创建后通过 POST 更新实验时在 phase 中用 `condition` 字段设置。

### Step 3：关联到 Feature Flag

将实验作为 `experiment-ref` 规则添加到 Feature Flag。

> **致命陷阱：Feature Flag 更新 API 是全量替换！**
>
> `POST /api/v1/features/{featureId}` 更新 `environments.{env}.rules` 时，会**完全替换**该环境的所有规则。
>
> **必须**先读取当前完整规则列表，在末尾追加新规则，再整体提交。否则会丢失所有已有规则！

正确流程：

```bash
# 1. 先读取当前完整 Feature Flag 配置
curl -s -H "Authorization: Bearer $GROWTHBOOK_API_KEY" \
  "https://us-ab-management-api.addx.live/api/v1/features/{featureId}" \
  > /tmp/current_feature.json

# 2. 在已有规则末尾追加新 experiment-ref 规则（用脚本处理）
# 3. 提交更新（包含所有原有规则 + 新规则）
curl -s -X POST \
  -H "Authorization: Bearer $GROWTHBOOK_API_KEY" \
  -H "Content-Type: application/json" \
  "https://us-ab-management-api.addx.live/api/v1/features/{featureId}" \
  -d @/tmp/updated_feature.json

# 4. 验证更新后规则数量和内容与预期一致
```

experiment-ref 规则结构：

```json
{
  "type": "experiment-ref",
  "experimentId": "exp_xxx",
  "enabled": true,
  "variations": [
    {"variationId": "var_xxx", "value": "310"},
    {"variationId": "var_yyy", "value": "311"}
  ],
  "coverage": 1,
  "condition": "",
  "savedGroupTargeting": []
}
```

> **注意**：experiment-ref 规则的 `condition` 字段通过 Feature API 设置**不会生效**。定向条件必须在实验的 phase 层设置（见 Step 2）。

### Step 4：验证

更新后**必须验证**：
1. 规则数量正确（原有数量 + 新增数量）
2. 原有规则的 experimentId、enabled、variations 等内容完全一致
3. 新规则的配置正确
4. 其他环境（特别是 production）未受影响

## Feature Flag 安全操作规则

### 核心原则

1. **只操作指定环境**：更新 staging 时，payload 中只包含 `staging` 环境，不要触碰 `production` 和 `pre`
2. **先备份后操作**：更新前将当前配置保存到本地文件
3. **全量读取-追加-全量写回**：永远不要只提交新规则，必须包含所有已有规则
4. **操作后验证**：对比更新前后的规则列表，确保无遗漏无篡改

### 致命错误示例

```bash
# 错误：只提交新规则，导致覆盖所有已有规则！
curl -X POST .../features/home_promo_popup -d '{
  "environments": {
    "staging": {
      "enabled": true,
      "rules": [只有新规则]  # ← 原有 18 条规则全部丢失！
    }
  }
}'
```

### 正确做法

```bash
# 正确：读取 → 追加 → 整体写回
# 1) 读取当前完整规则
current_rules=$(curl -s ... | jq '.feature.environments.staging.rules')
# 2) 追加新规则到数组末尾
updated_rules=$(echo $current_rules | jq '. + [新规则]')
# 3) 整体提交
curl -X POST ... -d "{\"environments\":{\"staging\":{\"enabled\":true,\"rules\":$updated_rules}}}"
# 4) 验证规则数量和内容
```

## API 踩坑记录

| 问题 | 原因 | 正确做法 |
|------|------|---------|
| 创建实验返回 400 | 缺少 `datasourceId`、`assignmentQueryId`、`trackingKey` | 三个字段必填，datasourceId 用 `ds_405gzm1nlqc3zcjh` |
| phase targetingCondition 为空 | 创建时传 `targetingCondition` 不生效 | 创建后用 POST 更新，phase 中用 `condition` 字段 |
| Feature Flag 规则全部丢失 | 更新 API 是全量替换 rules | 先读取完整规则，追加后整体写回 |
| experiment-ref 规则 condition 不生效 | Feature API 不支持设置 experiment-ref 的 condition | 定向条件在实验 phase 层设置 |

## References

- [GrowthBook REST API](https://docs.growthbook.io/api/)
- [GrowthBook Targeting Conditions](https://docs.growthbook.io/features/targeting)
- [GrowthBook Feature Flag Rules](https://docs.growthbook.io/features/rules)
- [UpdateExperimentPayload Schema](https://raw.githubusercontent.com/growthbook/growthbook/main/packages/back-end/src/api/openapi/payload-schemas/UpdateExperimentPayload.yaml)
