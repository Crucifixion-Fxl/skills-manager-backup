# UCloud API 常用 Action 速查

`ucloud api` 的参数格式为 `--Key Value` 的键值对，**所有参数都紧跟在 `--Action` 后面**。

## 重要：参数格式

```bash
# 正确格式 — 所有参数都是 --Key Value 键值对
ucloud api --Action GetBalance

# 正确格式 — 带参数
ucloud api --Action ListUBillDetail --BillingCycle 2026-04 --Limit 100

# 错误 — 不要在 ucloud api 命令中使用 --profile / --json 等 CLI flag
# 这些会被当作 API 参数解析导致报错
ucloud api --Action GetBalance --profile default  # ❌ 报错
ucloud api --Action GetBalance --json              # ❌ 报错
```

**关键规则**：
- `ucloud api` 只接受 `--Action`、`--local-file`、`--repeats`、`--concurrent` 这几个选项
- **`--profile` 不能用于 `ucloud api`**，需要提前通过 `ucloud config` 设置 active profile
- 其他所有 `--Key Value` 都会作为 API 参数传递
- 返回值默认是 JSON，不需要指定 `--json`

## 账单管理（UBill）

### GetBalance — 获取账户余额

```bash
ucloud api --Action GetBalance
```

无需额外参数。返回 `Amount`（账户余额）、`AmountFreeze`（冻结金额）等。

### ListUBillOverview — 账单总览

```bash
ucloud api --Action ListUBillOverview --BillingCycle 2026-04 --Dimension product
```

| 参数 | 必填 | 说明 |
|------|------|------|
| BillingCycle | 是 | 账期，格式 YYYY-MM |
| Dimension | 是 | 维度：`product`（按产品）/ `project`（按项目）/ `user`（按用户） |
| HideUnpaid | 否 | 0=未支付（默认）, 1=已支付 |

### ListUBillDetail — 账单明细

```bash
ucloud api --Action ListUBillDetail --BillingCycle 2026-04 --Limit 100
```

| 参数 | 必填 | 说明 |
|------|------|------|
| BillingCycle | 是 | 账期，格式 YYYY-MM |
| Limit | 否 | 每页条数，默认 25，最大 100 |
| Offset | 否 | 偏移量，默认 0 |
| ResourceTypes | 否 | 资源类型过滤（可多个） |
| ResourceIds | 否 | 资源 ID 过滤（可多个） |
| ChargeType | 否 | 计费方式过滤 |
| OrderType | 否 | 订单类型过滤 |
| ShowZero | 否 | 是否显示零消费，0=否, 1=是 |

### GetBillDataFileUrl — 下载账单文件

```bash
ucloud api --Action GetBillDataFileUrl --BillingCycle 2026-04 --BillType 1
```

| 参数 | 必填 | 说明 |
|------|------|------|
| BillingCycle | 是 | 账期，格式 YYYY-MM |
| BillType | 是 | 0=总览报表, 1=明细报表 |
| Format | 否 | `csv` 或 `pdf` |
| PaidType | 否 | 0=未支付（仅当月）, 1=已支付 |

### DescribeOrderDetailInfo — 订单明细

```bash
ucloud api --Action DescribeOrderDetailInfo --BeginTime 1711900800 --EndTime 1714492800
```

| 参数 | 必填 | 说明 |
|------|------|------|
| BeginTime | 是 | 开始时间（Unix 时间戳） |
| EndTime | 是 | 结束时间（Unix 时间戳） |
| Limit | 否 | 每页条数 |
| Offset | 否 | 偏移量 |
| ResourceTypes | 否 | 资源类型过滤 |
| OrderTypes | 否 | 订单类型过滤 |

## 云主机（UHost）— 补充 CLI 未覆盖的 API

CLI 内建 `ucloud uhost` 已覆盖大部分操作。以下是 CLI 没有的 API：

### DescribeUHostInstance — 详细实例信息（比 CLI 更多字段）

```bash
ucloud api --Action DescribeUHostInstance --Limit 100
```

| 参数 | 必填 | 说明 |
|------|------|------|
| Region | 否 | 区域（默认用 profile 配置） |
| ProjectId | 否 | 项目 ID |
| UHostIds.N | 否 | 实例 ID 列表（如 --UHostIds.0 uhost-xxx） |
| Limit | 否 | 每页条数，默认 20，最大 100 |
| Offset | 否 | 偏移量 |
| Tag | 否 | 业务组过滤 |
| SubnetId | 否 | 子网过滤 |
| VPCId | 否 | VPC 过滤 |

### GetUHostInstancePrice — 询价

```bash
ucloud api --Action GetUHostInstancePrice --CPU 4 --Memory 16384 --MachineType G --GpuType V100 --GPU 1 --ChargeType Month --Quantity 1
```

### CreateUHostInstance — 创建实例（GPU 机型）

```bash
ucloud api --Action CreateUHostInstance \
  --ImageId uimage-xxx \
  --LoginMode Password --Password <password> \
  --CPU 8 --Memory 32768 \
  --MachineType G --GpuType V100 --GPU 1 \
  --ChargeType Month \
  --Name gpu-training-01
```

| 关键参数 | 说明 |
|---------|------|
| MachineType | 机型：N(标准), C(高主频), G(GPU), O(快杰) |
| GpuType | GPU型号：K80, P40, V100, T4, T4S, A100 等 |
| GPU | GPU 卡数 |
| ChargeType | Year/Month/Dynamic/Postpay/Spot |

## 对象存储（US3/UFile）

US3 管理类 API 可通过 `ucloud api` 调用，数据操作需用 `us3cli`。

### DescribeBucket — 查看存储桶

```bash
ucloud api --Action DescribeBucket
```

### GetUFileDailyReport — US3 日报

```bash
ucloud api --Action GetUFileDailyReport --BucketName mybucket --StartTime 1711900800 --EndTime 1714492800
```

## API 文档查询

如果需要的 Action 不在本速查表中，可通过以下方式查询：

1. **Go SDK 文档**（最准确的参数列表）：`https://pkg.go.dev/github.com/ucloud/ucloud-sdk-go/services/<服务名>`
   - 常用服务名：`uhost`, `udisk`, `unet`, `ulb`, `udb`, `umem`, `ubill`, `ufile`, `vpc`
2. **官方 API 文档**：`https://docs.ucloud.cn/api/<服务名>-api/`
