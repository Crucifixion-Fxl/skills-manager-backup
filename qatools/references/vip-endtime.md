# 精确修改 VIP 到期时间

## 目录

- [适用与安全边界](#适用与安全边界)
- [Step 1—查询并选择目标 VIP](#step-1查询并选择目标-vip)
- [Step 2—计算并写入到期时间](#step-2计算并写入到期时间)
- [Step 3—回读和业务验证](#step-3回读和业务验证)
- [失败处理](#失败处理)

## 适用与安全边界

用该操作把 internal 测试账号的一条既有 VIP 调整到指定到期时间，模拟按 `daysSinceVipEnd` 划分的召回、续费、宽限期或过期分群。不要用它创建 VIP；账号没有目标 VIP 时，先用 Operation 2、3 或 4 创建。

- 仅限 staging 的 `internal=1` 测试账号。写请求必须同时带 Step 1 的 `userId` 以触发 qa-tools `internalCheck`；iot-service 还会按 `userVipId` 反查真实 owner 并执行 `InternalUserGuard`。
- 必须先查询并由 `productName`、`freeTrial`、当前 `endTime` 等字段确认目标记录，再传精确 `userVipId`。
- 默认只传 `newEndTime`；除非用户明确要求同时修改生效时间，否则禁止传 `newEffectiveTime`。
- 时间戳必须是 Unix epoch 秒，不是毫秒。写入前记录原 `endTime`，用户要求恢复时使用同一接口写回原值。

## Step 1—查询并选择目标 VIP

`POST /api/vip/list`

```bash
VIP_LIST=$(curl -sS -X POST "$QA_TOOLS_URL/api/vip/list" \
  -H "Authorization: Bearer $QA_TOOLS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"userId":1161554,"tierServiceType":0}')

echo "$VIP_LIST" | jq '.data[] | {
  userVipId, productName, effectiveTime, endTime, freeTrial, tradeNo, orderSn
}'
```

`tierServiceType` 的 `0` 表示云存储，`1` 表示 4G 流量。仅当响应 `result == 0` 且已唯一确认目标行后继续。保存该行原 `endTime`，不要选择 `productName` 为空或业务语义不明的行。

## Step 2—计算并写入到期时间

`POST /api/vip/endtime` 必须传 `userId`、`userVipId`、`newEndTime`；`newEffectiveTime` 可选且默认省略。

```bash
DAYS_AGO=15
NEW_END_TIME=$(( $(date +%s) - DAYS_AGO * 86400 ))

curl -sS -X POST "$QA_TOOLS_URL/api/vip/endtime" \
  -H "Authorization: Bearer $QA_TOOLS_TOKEN" \
  -H "Content-Type: application/json" \
  -d "$(jq -nc \
    --argjson userId 1161554 \
    --argjson userVipId 2066360 \
    --argjson newEndTime "$NEW_END_TIME" \
    '{userId:$userId,userVipId:$userVipId,newEndTime:$newEndTime}')" \
  | jq '{result,msg,data,error}'
```

| 目标区间 | 建议取样 |
|---------|---------|
| D0–D7 | 1 天前；写后立即确认 `isUserVip=false` |
| D8–D30 | 15 天前 |
| D31–D90 | 45 天前 |
| D91+ | 120 天前 |

## Step 3—回读和业务验证

1. 再调 `POST /api/vip/list`，断言目标 `userVipId.endTime` 已变化。
2. 调目标系统的 profile/evaluate 接口，断言 `isUserVip=false`、`daysSinceVipEnd` 位于目标区间；若业务依赖历史周期，同时核对历史周期字段未丢失。
3. 如果目标系统有独立属性缓存，按其刷新机制重试。`/api/vip/endtime` 已触发 iot-service 设备套餐缓存刷新，但不承诺替其他服务清理所有独立缓存。
4. 输出 `userId`、`userVipId`、原/新到期时间、目标区间和业务验证结果。不得输出 Authorization token。

## 失败处理

| 状态 / 现象 | 处理 |
|-------------|------|
| 403 / `NOT_INTERNAL_USER` / `NOT_TEST_USER` | 停止；改用 Operation 1/2 创建 `@qa.test` 账号，禁止绕过 guard |
| `userVipId is required` | 重新执行 Step 1，禁止只传 userId |
| `newEndTime is required` | 传 epoch 秒整数，检查是否误用了毫秒 |
| `更新VIP结束时间失败` | 回读列表确认记录仍存在，再查 qa-tools/iot-service staging 日志 |
| 写成功但目标分群未变化 | 先读 profile 属性，再核对 App 版本、设备数、购买历史等完整条件 |
