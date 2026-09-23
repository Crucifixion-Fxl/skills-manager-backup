---
name: aliyun-cli
description: 通过 aliyun CLI 管理阿里云资源。当用户提到阿里云、ECS、OSS、RDS、SLB、ACK、Redis、CDN、DNS、NAS、VPC、安全组、阿里云账单、函数计算、日志服务，或需要查询/操作阿里云资源时使用。即使用户只说"看看阿里云的机器"、"查一下 OSS 存储桶"、"阿里云这个月花了多少"也应触发。
---

# aliyun-cli

通过阿里云官方 CLI（aliyun）协助运维和开发人员管理阿里云资源。

## Description

本 Skill 提供阿里云 CLI 的标准化操作流程，覆盖资源查询、状态检查和变更操作。阿里云 API 用法通过 WebSearch 查询官方文档，本 Skill 只记录公司特有的账户信息、命名约定和操作红线。

## 运行环境路由（最高优先级）

先判断当前运行环境，再选择认证方式：

- 当当前 macOS 身份是 `sre-executor`，或 executor prompt 声明了固定 target 时，禁止执行
  `aliyun configure`、profile list/switch、直接 `aliyun`，也禁止读取平台凭据包或添加
  `--profile`、`-p`、`--mode`、AK/SK/token/config 参数。
- 该环境只能执行已经进入 SRE 提案与审批合同的原样命令：

  ```bash
  ./sre-platform-exec aliyun-1015250573365470 -- aliyun <Service> <Action> [参数]
  ```

  `<Action>` 必须使用 legacy PascalCase OpenAPI 名称（例如 `DescribeDomainRecords`）；受管
  wrapper 不启用或执行用户目录下的产品插件，因此不要改成 kebab-case plugin command。
  executor 还会在审批后用本地 ACT dispatcher 包住上述已批准正文。dispatcher 与 ACT 是
  执行控制面，不得写进尚未生成 ACT 的 investigator 提案，也不得改变报告中的批准命令。

  ```bash
  # 合法：固定 wrapper、固定 target、legacy PascalCase Action
  ./sre-platform-exec aliyun-1015250573365470 -- aliyun alidns DescribeDomainRecords --DomainName example.com

  # 非法：绕过 wrapper 并回退个人 profile；必须拒绝，不能执行
  aliyun alidns DescribeDomainRecords --DomainName example.com --profile personal
  ```

- wrapper 缺失、target 不匹配或 wrapper 拒绝时 fail closed，只报告 target 和非敏感错误；
  不得退回本机 profile、default profile 或个人身份。
- SRE 写操作只接受该工作流中针对完整 command 与 verification command 的有效 ACT；本
  Skill 的通用确认格式不能替代 ACT，也不能扩大批准范围。
- 其它终端、开发机和非 executor 环境继续使用下述显式 profile 流程。

受管环境 wrapper 缺失、target 不匹配或拒绝时，使用不含凭据的固定回复，不执行命令：

```text
Aliyun 执行被受管 wrapper 拒绝；未发生资源变更。
target: aliyun-1015250573365470
command_state: not_run
reason: <bounded redacted wrapper error>
next_step: administrator must repair the managed runtime; profile fallback is forbidden
```

## 认证方式

通过 `aliyun configure` 配置多 profile，每个 profile 对应一个账户/环境：

```bash
# 配置新 profile
aliyun configure --profile <profileName> --mode AK

# 查看已有 profile
aliyun configure list

# 切换默认 profile
aliyun configure switch --profile <profileName>
```

所有命令必须显式指定 `--profile`，**不要依赖 default profile**。

## aliyun CLI 基础用法

```bash
aliyun <Service> <Action> --profile <profile> [--region <region>] [--参数名 <值>]
```

常用参数：
- `--profile`：指定配置 profile（必须显式指定）
- `--region`：覆盖 profile 默认区域
- `--output cols=<字段列表>`：表格输出，指定列
- `--output json`：JSON 输出

### 常用区域

| 区域 | 代码 |
|------|------|
| 杭州 | cn-hangzhou |
| 上海 | cn-shanghai |
| 北京 | cn-beijing |
| 深圳 | cn-shenzhen |
| 成都 | cn-chengdu |
| 香港 | cn-hongkong |
| 新加坡 | ap-southeast-1 |
| 美东（弗吉尼亚） | us-east-1 |
| 欧洲（法兰克福） | eu-central-1 |

## 常用服务速查

| 服务 | aliyun Service | 说明 |
|------|---------------|------|
| 云服务器 | `ecs` | ECS 实例管理 |
| 对象存储 | `oss` | OSS 存储桶（部分操作需 ossutil） |
| 云数据库 RDS | `rds` | MySQL/PostgreSQL 实例 |
| 负载均衡 | `slb` | SLB/ALB 实例管理 |
| 容器服务 | `cs` | ACK 集群管理 |
| Redis | `r-kvstore` | Redis 实例 |
| CDN | `cdn` | 内容分发网络 |
| DNS | `alidns` | 域名解析 |
| NAS | `nas` | 文件存储 |
| VPC | `vpc` | 私有网络/交换机/安全组 |
| 云监控 | `cms` | 监控告警 |
| 日志服务 | `sls` | SLS 日志 |
| 函数计算 | `fc` | Serverless 函数 |
| 消息队列 | `ons` | RocketMQ |
| Elasticsearch | `elasticsearch` | ES 集群 |
| 费用中心 | `bssopenapi` | 账单查询 |
| 访问控制 | `ram` | RAM 用户/策略 |
| 密钥管理 | `kms` | 密钥管理 |
| SSL 证书 | `cas` | 证书管理 |

## 执行流程

### Step 1: 执行命令

先应用“运行环境路由”。普通环境可直接执行目标命令；如果报错（如 command not found、认证失败、配置缺失），读取 `references/setup.md` 并按引导帮助用户完成安装和配置。Mac `sre-executor` 不得自行安装或配置，wrapper 报错即停止并交由管理员修复。

### Step 2: 确认操作目标

根据用户描述确定：

1. **目标账户**：确定 `--profile`
   - 先执行 `aliyun configure list` 查看已有 profile
   - 如果只有一个 profile，直接使用
   - 如果有多个 profile，且根据上下文和已有知识无法判断使用哪个 profile，主动询问用户使用哪个 profile
2. **目标区域**：未指定时使用 profile 配置的默认区域，用户指定其他区域时用 `--region` 覆盖
3. **操作内容**：判断读/写操作

本步骤的 profile 选择只适用于普通环境；Mac `sre-executor` 的账户由固定 wrapper target
决定，不得查询或覆盖 profile。

### Step 3: 执行命令

```bash
aliyun <Service> <Action> --profile <profile> [--region <region>] [--参数]
```

Mac `sre-executor` 必须保留获批的 wrapper 命令形态，不使用本节的直接调用示例。

如不确定某个 Action 的参数，可先查看帮助：

```bash
aliyun <Service> <Action> --help
```

## Rules

### 查询范围约束

**默认单 profile 查询**：除非用户明确要求（如"查看所有账户的资源"、"对比各个 profile"），否则只在单个 profile 中查询资源，不要自动遍历所有 profile 进行全局查找。

### 操作分级

#### 只读操作（直接执行）

所有 `Describe*`、`Get*`、`List*`、`Query*` 类 Action 均为只读，可直接执行：

- **ECS**: `DescribeInstances`, `DescribeInstanceStatus`, `DescribeImages`, `DescribeSecurityGroups`, `DescribeDisks` 等
- **OSS**: `ListBuckets`, `GetBucketInfo`（数据操作需 ossutil）
- **RDS**: `DescribeDBInstances`, `DescribeDBInstanceAttribute`, `DescribeBackups`, `DescribeSlowLogs` 等
- **SLB**: `DescribeLoadBalancers`, `DescribeListenerAccessControlAttribute` 等
- **ACK (cs)**: `DescribeClusters`, `DescribeClusterDetail`, `DescribeClusterNodes` 等
- **Redis**: `DescribeInstances`, `DescribeInstanceAttribute` 等
- **CDN**: `DescribeCdnDomainDetail`, `DescribeDomainTrafficData` 等
- **DNS**: `DescribeDomains`, `DescribeDomainRecords` 等
- **VPC**: `DescribeVpcs`, `DescribeVSwitches`, `DescribeSecurityGroups`, `DescribeSecurityGroupAttribute` 等
- **CMS**: `DescribeMetricLast`, `DescribeAlarms` 等
- **RAM**: `ListUsers`, `ListRoles`, `ListPolicies`, `GetUser`, `GetRole` 等
- **费用**: `QueryBill`, `QueryAccountBalance`, `QueryInstanceBill` 等

#### 高危操作（必须用户确认）

以下操作会修改资源状态，**必须在执行前向用户展示完整命令并获得明确确认**：

- **创建资源**: `Create*`, `RunInstances`, `AllocateEipAddress` 等
- **修改资源**: `Modify*`, `Update*`, `Replace*` 等
- **删除资源**: `Delete*`, `Release*`, `Destroy*` 等
- **启停操作**: `StartInstance`, `StopInstance`, `RebootInstance` 等
- **安全相关**: 安全组规则变更、RAM 策略变更
- **DNS 变更**: `AddDomainRecord`, `UpdateDomainRecord`, `DeleteDomainRecord`
- **CDN 操作**: `RefreshObjectCaches`, `PushObjectCache`
- **数据库操作**: `CreateBackup`, `RestoreDBInstance`, `DeleteDBInstance`

确认格式：

```
⚠️ 即将执行写操作：

  Profile: default
  区域: cn-hangzhou
  命令: aliyun ecs StopInstance --InstanceId i-abc123 --profile default

确认执行？(y/n)
```

### 操作红线

1. **禁止未确认的写操作** — 任何写操作都必须二次确认
2. **禁止暴露密钥** — AccessKey、数据库密码等只展示前 4 位 + `***`
3. **禁止大范围删除** — 批量 Release/Delete 必须先列出受影响资源并确认
4. **OSS 数据操作** — aliyun CLI 对 OSS 数据操作支持有限，大量数据操作需使用 `ossutil`，提醒用户
5. **费用查询** — 默认查询最近一个月，避免拉取过大时间范围

### 分页查询（重要）

aliyun CLI 列表接口有分页限制，**必须处理分页以确保结果完整**：

1. 先查 TotalCount 确认资源总数
2. 如果 TotalCount > PageSize，需要多次查询：
   - 第一页：`--PageSize 100 --PageNumber 1`
   - 第二页：`--PageSize 100 --PageNumber 2`
   - 依此类推直到取完
3. 默认 PageSize 通常为 10，建议指定 `--PageSize 100`

### 输出优化

- 使用 `--output cols=<字段>` 表格输出关键字段
- 列表类查询指定 `--PageSize 100` 并结合分页
- 使用 `jq` 过滤 JSON 输出中的关键字段

## SSL 证书续费（CAS V2.0 订阅模式）

阿里云 SSL 证书服务已升级为 **V2.0 订阅模式**，旧版 CPACK 额度模式的 API 不再适用于新购证书。

### 关键区别

| | 旧版 CPACK 模式 | V2.0 订阅模式 |
|---|---|---|
| InstanceId 格式 | `cas-ivauto-*` | `cas-cn-*` / `cas_dv-cn-*` |
| 购买方式 | 购买证书资源包 → 消耗额度 | `bssopenapi CreateInstance` 直接购买订阅实例 |
| 申请证书 | `CreateCertificateForPackageRequest` | `UpdateInstance` → `ApplyCertificate` |
| 续费 | `RenewCertificateOrderForPackageRequest` | 订阅期内自动续签，或重新购买实例 |
| 查询状态 | `DescribeCertificateState --OrderId` | `GetInstanceDetail --InstanceId` |
| 查询证书列表 | `ListUserCertificateOrder --OrderType CPACK` | `ListCertificates` |

### V2.0 购买证书

```bash
# 1. 询价（wildcardSpec 可选值见 DescribePricingModule 返回的 wildcardSpec 字段）
aliyun bssopenapi GetSubscriptionPrice --ProductCode cas --ProductType cas \
  --SubscriptionType Subscription --OrderType NewOrder --profile default \
  --ModuleList.1.ModuleCode wildcardDomainCount \
  --ModuleList.1.Config "wildcardDomainCount:1,wildcardSpec:rap.dv.w" \
  --ModuleList.1.ModuleStatus 1

# 2. 购买（Period 单位为月）
aliyun bssopenapi CreateInstance --ProductCode cas --ProductType cas \
  --SubscriptionType Subscription --Period 12 --profile default \
  --Parameter.1.Code wildcardDomainCount --Parameter.1.Value 1 \
  --Parameter.2.Code wildcardSpec --Parameter.2.Value rap.dv.w
# 返回 InstanceId（如 cas-cn-xxx）和 OrderId
```

常用 wildcardSpec 值：

| 值 | 品牌 |
|---|---|
| `rap.dv.w` | RapidSSL DV 通配符 |
| `geo.dv.w` | GeoTrust DV 通配符 |
| `geo.ov.w` | GeoTrust OV 通配符 |
| `gs.dv.w` | GlobalSign DV 通配符 |
| `ss.dv.w` | DigiCert DV 通配符 |
| `vt.dv.w` | vTrus DV 通配符 |

### V2.0 申请证书（两步流程）

```bash
# Step 1: 设置域名、验证方式、联系人等
aliyun cas UpdateInstance --profile default \
  --InstanceId "cas-cn-xxx" \
  --Domain "*.example.com" \
  --ValidationMethod DNS \
  --GenerateCsrMethod online \
  --KeyAlgorithm RSA_2048 \
  --CertificateName "my-cert-name" \
  --ContactIdList.1 <ContactId>

# 查询联系人 ID：
# aliyun cas ListContact --profile default --ShowSize 50

# Step 2: 提交申请
aliyun cas ApplyCertificate --profile default \
  --InstanceId "cas-cn-xxx"

# Step 3: 获取 DNS 验证记录
aliyun cas GetInstanceDetail --InstanceId "cas-cn-xxx" --profile default
# 返回 DomainValidationList 中包含 ValidationKey、ValidationType、ValidationValue
# 在域名 DNS 服务商添加对应的 TXT 记录

# Step 4: 等待 CA 验证（通常 1-5 分钟）
# 轮询 GetInstanceDetail，Status 从 pending 变为 normal、PendingResult 变为 issued 即签发成功
```

### 注意事项

1. **不要用旧 API 操作 V2.0 实例**：`RenewCertificateOrderForPackageRequest` 和 `CreateCertificateForPackageRequest` 只能操作 `cas-ivauto-*` 格式的旧版 CPACK 实例，对 V2.0 实例会报 `InsufficientQuota`
2. **通配符域名限制**：`UpdateInstance` 设置 Domain 时，通配符证书只能传通配符域名（如 `*.example.com`），不能同时传 `*.example.com,example.com`（会报 `DomainAndProductNotMatch`），但签发后的证书 SANs 会自动包含裸域名
3. **证书有效期 vs 订阅有效期**：V2.0 模式下证书有效期（通常 6-12 个月）与订阅有效期（购买时长）不同，订阅期内证书到期前会自动续签
4. **联系人必填**：`ApplyCertificate` 之前必须通过 `UpdateInstance` 设置 `ContactIdList`，否则报 `MissingParameter`

## Examples

### Good

```
# 查看 ECS 实例
用户：看看阿里云上有哪些机器

AI：查询 ECS 实例：
  [执行 aliyun ecs DescribeInstances --profile default --PageSize 100 --output cols=InstanceId,InstanceName,PublicIpAddress,Status,InstanceType]
```

```
# 写操作确认
用户：帮我重启一下那台 ECS

AI：找到实例：
  i-abc123 (web-server) - Running

  ⚠️ 即将执行写操作：

    Profile: default
    区域: cn-hangzhou
    命令: aliyun ecs RebootInstance --InstanceId i-abc123 --profile default

  确认执行？(y/n)
```

```
# 密钥保护
用户：查一下 RDS 的连接信息

AI：RDS 实例: rm-abc123
  连接地址: rm-abc123.mysql.rds.aliyuncs.com
  端口: 3306
  注意: 数据库密码请通过控制台查看（不在此展示）
```

### Bad

```
# 未确认就执行写操作
用户：停掉那台测试机
AI：已停止。 ← 未展示影响范围和确认

# 暴露密钥
AI：数据库密码为 <LEAKED_SECRET>  ← 应脱敏

# OSS 数据操作用 aliyun CLI
aliyun oss PutObject ...  ← 应提醒用户使用 ossutil
```

## 豁免

| 场景 | 条件 |
|------|------|
| 紧急故障恢复 | 用户明确声明紧急情况，可简化确认流程（仍需展示命令） |
