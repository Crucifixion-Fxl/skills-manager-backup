---
name: oci-cli
description: 通过 OCI CLI 管理 Oracle Cloud 资源。当用户提到 Oracle Cloud、OCI、Oracle Compute、Oracle Object Storage、Oracle VCN、Oracle Block Volume、Oracle Load Balancer、Oracle Autonomous DB、OCI 账单、OCI 费用、OCI 流量，或需要查询/操作 Oracle Cloud 资源时使用。即使用户只说"查一下 Oracle 上的实例"、"看看 OCI 的网络配置"、"OCI 这个月花了多少钱"也应触发。
---

# oci-cli

## Description

通过 OCI CLI 协助管理 Oracle Cloud Infrastructure 资源。覆盖 Compute、Object Storage、VCN、Usage/Cost 等核心服务的读操作，写操作需用户确认。

## 账户体系

公司使用单租户（Tenancy）、多区域部署架构。

### Profile 映射表

| Profile | Tenancy | Region | 用途 |
|---------|---------|--------|------|
| `oci-a4x-us-prod` | a4x | us-ashburn-1 | 生产环境 |

Profile 命名规则：`oci-{tenancy名}-{区域缩写}-{用途}`，与公司 AWS/GCP 的 `云-账户标识-区域-用途` 风格一致。

### 区域速查

| 关键词 | Region Identifier | Region Key |
|--------|-------------------|------------|
| 美东/Ashburn/US East | us-ashburn-1 | IAD |
| 澳洲/Sydney | ap-sydney-1 | SYD |
| 巴西/Sao Paulo | sa-saopaulo-1 | GRU |
| 德国/Frankfurt | eu-frankfurt-1 | FRA |
| 意大利/Milan | eu-milan-1 | LIN |
| 日本/Osaka | ap-osaka-1 | KIX |
| 日本/Tokyo | ap-tokyo-1 | NRT |
| 新加坡/Singapore | ap-singapore-1 | SIN |
| 南非/Johannesburg | af-johannesburg-1 | JNB |
| 英国/London | uk-london-1 | LHR |
| 美中/Chicago | us-chicago-1 | ORD |
| 美西/Phoenix | us-phoenix-1 | PHX |
| 美西/San Jose | us-sanjose-1 | SJC |

用户未指定区域时，使用 `~/.oci/config` 中 `[oci-a4x-us-prod]` profile 配置的 region。

## 执行流程

### Step 1: 执行命令

直接执行目标命令。如果报错（如 command not found、认证失败、配置缺失），读取 `references/setup.md` 并按引导帮助用户完成安装和配置。

### Step 2: 确认操作目标

根据用户描述确定：

1. **目标区域**：匹配上方区域速查表，需要时用 `--region` 指定
2. **Compartment**：大多数操作需要 `--compartment-id`，先用 `oci iam compartment list` 获取可用 compartment
3. **操作内容**：判断读/写操作

### Step 3: 执行命令

所有命令显式指定 `--profile oci-a4x-us-prod`：

```bash
oci <service> <resource> <action> --profile oci-a4x-us-prod [--region <region>] [其他参数]
```

## 常用服务速查

### Compute

```bash
# 列出实例
oci compute instance list --compartment-id <ocid> --profile oci-a4x-us-prod

# 查看实例详情
oci compute instance get --instance-id <ocid> --profile oci-a4x-us-prod

# 列出可用 shape
oci compute shape list --compartment-id <ocid> --profile oci-a4x-us-prod

# 列出镜像
oci compute image list --compartment-id <ocid> --profile oci-a4x-us-prod
```

### Object Storage

```bash
# 列出 namespace
oci os ns get --profile oci-a4x-us-prod

# 列出 bucket
oci os bucket list --compartment-id <ocid> --namespace-name <ns> --profile oci-a4x-us-prod

# 列出 bucket 中的对象
oci os object list --bucket-name <name> --namespace-name <ns> --profile oci-a4x-us-prod

# 下载对象（只读）
oci os object get --bucket-name <name> --name <key> --file <local-path> --namespace-name <ns> --profile oci-a4x-us-prod
```

### VCN (Virtual Cloud Network)

```bash
# 列出 VCN
oci network vcn list --compartment-id <ocid> --profile oci-a4x-us-prod

# 列出子网
oci network subnet list --compartment-id <ocid> --vcn-id <ocid> --profile oci-a4x-us-prod

# 列出安全列表
oci network security-list list --compartment-id <ocid> --vcn-id <ocid> --profile oci-a4x-us-prod

# 列出 NSG (Network Security Group)
oci network nsg list --compartment-id <ocid> --profile oci-a4x-us-prod

# 列出路由表
oci network route-table list --compartment-id <ocid> --vcn-id <ocid> --profile oci-a4x-us-prod
```

### IAM

```bash
# 列出 compartment
oci iam compartment list --profile oci-a4x-us-prod

# 列出用户
oci iam user list --profile oci-a4x-us-prod

# 列出策略
oci iam policy list --compartment-id <ocid> --profile oci-a4x-us-prod
```

### Usage / Cost（账单与用量查询）

tenancy-id 从 `~/.oci/config` 的 `tenancy=` 字段获取。

```bash
# 按服务查看本月费用
oci usage-api usage-summary request-summarized-usages --profile oci-a4x-us-prod \
  --tenant-id <tenancy-ocid> \
  --time-usage-started "2025-03-01T00:00:00Z" \
  --time-usage-ended "2025-04-01T00:00:00Z" \
  --granularity MONTHLY --query-type COST \
  --group-by '["service"]' --output json

# 按 SKU 查看用量明细（如出站流量）
oci usage-api usage-summary request-summarized-usages --profile oci-a4x-us-prod \
  --tenant-id <tenancy-ocid> \
  --time-usage-started "2025-03-01T00:00:00Z" \
  --time-usage-ended "2025-04-01T00:00:00Z" \
  --granularity MONTHLY --query-type USAGE \
  --group-by '["service","skuName","unit"]' --output json
```

常用 group-by 字段：`service`、`skuName`、`skuPartNumber`、`unit`、`compartmentName`、`region`。

## Rules

### 操作分级

#### 只读操作（直接执行）

所有 `list`、`get` 类命令均为只读，可直接执行：

- **Compute**: `instance list`, `instance get`, `shape list`, `image list`, `vnic-attachment list`
- **Object Storage**: `ns get`, `bucket list`, `object list`, `object get`（下载）, `object head`
- **VCN**: `vcn list`, `subnet list`, `security-list list`, `nsg list`, `route-table list`
- **IAM**: `compartment list`, `user list`, `group list`, `policy list`
- **Block Volume**: `volume list`, `volume get`, `volume-backup list`
- **Load Balancer**: `load-balancer list`, `load-balancer get`
- **Usage API**: `usage-summary request-summarized-usages`（费用和用量查询）

#### 高危操作（必须用户确认）

以下操作会修改资源状态，**必须在执行前向用户展示完整命令并获得明确确认**：

- **创建资源**: `create` 类命令
- **修改资源**: `update` 类命令
- **删除资源**: `delete`, `terminate` 类命令
- **启停操作**: `instance action --action STOP/START/RESET`
- **Object Storage 写操作**: `object put`, `object delete`, `bucket create`, `bucket delete`
- **网络变更**: 安全列表/NSG 规则的增删改
- **IAM 变更**: 用户、组、策略的创建和修改

确认格式：

```
⚠️ 即将执行写操作：

  账户: oci-a4x-us-prod
  区域: us-ashburn-1
  命令: oci compute instance action --action STOP --instance-id ocid1.instance.oc1.iad.xxx --profile oci-a4x-us-prod

确认执行？(y/n)
```

### 操作红线

1. **禁止未确认的写操作** — 任何写操作都必须二次确认
2. **禁止暴露完整密钥** — Secret 或 API Key 内容只展示前 4 位 + `***`
3. **禁止大范围删除** — 批量删除操作必须先列出受影响资源并确认
4. **Compartment 明确性** — 操作前确认目标 compartment，避免在根 compartment 执行意外操作

### 输出优化

- 默认使用 `--output table` 提升可读性
- 需要程序化处理时使用 `--output json`
- 使用 `--query` (JMESPath) 过滤关键字段，避免输出过长
- 对于大量结果，使用 `--limit` 限制返回数量

## Examples

### Good

```
# 查询实例
用户：帮我看看 Ashburn 的 Compute 实例

AI：[先获取 compartment 列表]
    [执行 oci compute instance list --compartment-id <ocid> --profile oci-a4x-us-prod --region us-ashburn-1 --output table]
```

```
# 写操作确认
用户：帮我停掉那台测试实例

AI：找到以下实例：
  ocid1.instance.oc1.iad.xxx (test-server) - RUNNING

  ⚠️ 即将执行写操作：

    账户: oci-a4x-us-prod
    区域: us-ashburn-1
    命令: oci compute instance action --action STOP --instance-id ocid1.instance.oc1.iad.xxx --profile oci-a4x-us-prod

  确认执行？(y/n)
```

```
# 密钥保护
用户：查一下 Vault 里的某个 secret

AI：查到 Secret: my-api-key
  值: sk-a1b2****（已脱敏，如需完整值请确认）
```

### Bad

```
# 未指定 profile
oci compute instance list --compartment-id xxx  ← 缺少 --profile

# 未确认就执行写操作
用户：删掉这个 bucket
AI：已删除。 ← 未展示影响范围和确认

# 暴露密钥
AI：API Key 为 -----BEGIN RSA PRIVATE KEY-----...  ← 完整暴露
```

## 豁免

| 场景 | 条件 |
|------|------|
| 紧急故障恢复 | 用户明确声明紧急情况，可简化确认流程（仍需展示命令） |
