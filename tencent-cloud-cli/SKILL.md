---
name: tencent-cloud-cli
description: 通过 tccli 管理腾讯云资源。当用户提到腾讯云、CVM、TKE、COS、CLB、CDB、Redis、CDN、DNSPod、SSL、CFS、VPC、安全组、腾讯云账单、云函数、日志服务，或需要查询/操作腾讯云资源时使用。即使用户只说"看看腾讯云的机器"、"查一下 TKE 集群"、"腾讯云这个月花了多少"也应触发。
---

# tencent-cloud-cli

## Description

首次接入/变更扫描与日常认证入口见 [SaaS 接入](references/saas-access.md)；已有平台业务契约与授权门禁仍在本 Skill 维护。

通过腾讯云官方 CLI（tccli）协助运维和开发人员管理腾讯云资源。

## 账户信息

CN 当前集群全部在腾讯云，AWS CN 集群已弃用。按环境选择账号，不能再默认 prod 账号。

| 环境 | 主账号 UIN | Region | GitOps 集群目录 |
|------|-----------|--------|----------------|
| prod / cn-main | `100014919455` | `ap-beijing` | `tencent-100014919455-cn-main` |
| staging | `100052802231` | `ap-beijing` | `tencent-100052802231-cn-staging` |
| tech-service | `100052802231` | `ap-beijing` | `tencent-100052802231-cn-tech-service` |

目录、服务入口和取证方法见 [CN 腾讯云环境事实与核验入口](../k8s-ops/references/cn-tencent-inventory.md)。旧 `100050722703` staging、`100014919455` tech-service 目录可能残留，不能作为当前环境映射。老账号 `100014919455` 下还残留旧 staging 集群 `cls-i860hdh9`（VPC `vpc-jxxz6aiq`，名为 `staing-cn`），已无业务、待下线；用 `--profile` 指向老账号查到同名 `staging-cn` 集群时不要当成当前 staging，当前 staging 是新账号的 `cls-riukakjb`。prod 账号的 AppId `1302606863` 不适用于新账号；新账号 AppId、子账号 UIN、SecretId 和 profile 名均须从已授权配置/身份接口核验，不可按 UIN 推算。

## 认证方式

通过 `tccli configure` 配置多 profile，每个 profile 对应一个账户/环境：

```bash
# 配置新 profile
tccli configure --profile <profileName>

# 查看已有 profile（通过列出配置文件）
ls ~/.tccli/*.configure | xargs -n1 basename | sed 's/.configure$//'

# 切换默认 profile（修改配置文件）
# 注意：tccli 没有内置的 switch 命令，建议始终显式指定 --profile
```

所有命令必须显式指定 `--profile`，避免依赖默认 profile。

## tccli 基础用法

```bash
tccli <Service> <Action> [--profile <profile>] [--region <region>] [--参数名 <值>]
```

常用参数：
- `--profile`：指定配置 profile（必须显式指定）
- `--region`：覆盖默认区域（默认 ap-beijing）
- `--output json`：JSON 输出（默认）
- `--filter`：JMESPath 过滤

### 腾讯云常用区域

| 区域 | 代码 |
|------|------|
| 北京 | ap-beijing |
| 上海 | ap-shanghai |
| 广州 | ap-guangzhou |
| 成都 | ap-chengdu |
| 重庆 | ap-chongqing |
| 香港 | ap-hongkong |
| 新加坡 | ap-singapore |

## 常用服务速查

| 服务 | tccli Service | 说明 |
|------|--------------|------|
| 云服务器 | `cvm` | CVM 实例管理 |
| 容器服务 | `tke` | TKE 集群管理 |
| 对象存储 | 不支持（用 coscmd） | COS 存储桶 |
| 负载均衡 | `clb` | CLB 实例管理 |
| 云数据库 MySQL | `cdb` | MySQL 实例 |
| 云数据库 Redis | `redis` | Redis 实例 |
| CDN | `cdn` | 内容分发网络 |
| DNS | `dnspod` | 域名解析 |
| SSL 证书 | `ssl` | 证书管理 |
| 私有网络 | `vpc` | VPC/子网/安全组 |
| 文件存储 | `cfs` | CFS 文件系统 |
| 云函数 | `scf` | Serverless 函数 |
| 消息队列 | `ckafka` | Kafka 实例 |
| Elasticsearch | `es` | ES 集群 |
| 云监控 | `monitor` | 监控告警 |
| 费用中心 | `billing` | 账单查询 |
| 访问管理 | `cam` | IAM 用户/策略 |
| 密钥管理 | `kms` | 密钥管理 |
| 日志服务 | `cls` | 日志检索 |

## 执行流程

### Step 1: 检查本机工具和凭据来源

先检查 `tccli --version` 和已有 profile 名称。工具不存在、认证失败或配置缺失时读取 `references/setup.md`；优先使用本机已有配置和用户授权的凭据来源，不先执行未选定账号的资源命令。

### Step 2: 确认操作目标

根据用户描述确定：

1. **目标账户**：按上表确定主账号 UIN，然后枚举本机已有 profile 名称。profile 即使只有一个也必须验证，不默认使用 `default`，也不假定 `old`、`new` 或集群目录名是可用 profile。
   - 用腾讯云 STS `GetCallerIdentity` 对候选 profile 做只读校验，核对返回的 `AccountId`（主账号 UIN）与目标 UIN 一致；不能用子账号 `UserId` / `PrincipalId` 代替；仅展示身份标识，不输出凭据。
   - 已验证新账号 profile 可用于 staging 和 tech-service，但仍须核对目标集群 ID；共用账号不代表共用集群。
   - 找不到匹配身份时报告缺口，不退回旧账号。`DescribeRegions` 成功只能证明 API 可访问，不能证明选对账户。
2. **目标区域**：当前三个 CN 集群使用 `ap-beijing`，命令显式传 `--region ap-beijing`；其他资源区域以任务证据为准。
3. **操作内容**：判断读/写操作

### Step 3: 执行命令

所有命令必须显式指定 `--profile`，**不要依赖 default profile**：

```bash
tccli <Service> <Action> --profile <profile> [--region <region>] [--参数]
```

如不确定某个 Action 的参数，可先查看帮助：

```bash
tccli <Service> <Action> --help
```

## Rules

### 查询范围约束

**默认单 profile 查询**：除非用户明确要求（如"查看所有账户的资源"、"对比各个 profile"），否则只在单个 profile 中查询资源，不要自动遍历所有 profile 进行全局查找。

### 操作分级

#### 只读操作（直接执行）

所有 `Describe*`、`Get*`、`List*` 类 Action 均为只读，可直接执行：

- **CVM**: `DescribeInstances`, `DescribeInstancesStatus`, `DescribeImages`, `DescribeKeyPairs`, `DescribeSecurityGroups` 等
- **TKE**: `DescribeClusters`, `DescribeClusterInstances`, `DescribeClusterNodePools`, `DescribeClusterEndpoints` 等
- **CLB**: `DescribeLoadBalancers`, `DescribeListeners`, `DescribeTargets` 等
- **CDB**: `DescribeDBInstances`, `DescribeBackups`, `DescribeSlowLogs`, `DescribeDBPrice` 等
- **Redis**: `DescribeInstances`, `DescribeInstanceMonitorBigKey`, `DescribeSlowLog` 等
- **CDN**: `DescribeDomains`, `DescribeCdnData`, `DescribePurgeQuota`, `ListTopData` 等
- **DNSPod**: `DescribeDomainList`, `DescribeRecordList`, `DescribeDomain` 等
- **SSL**: `DescribeCertificates`, `DescribeCertificate` 等
- **VPC**: `DescribeVpcs`, `DescribeSubnets`, `DescribeSecurityGroups`, `DescribeSecurityGroupPolicies`, `DescribeNetworkInterfaces` 等
- **CFS**: `DescribeCfsFileSystems`, `DescribeMountTargets` 等
- **Monitor**: `GetMonitorData`, `DescribeAlarmPolicies`, `DescribeBasicAlarmList` 等
- **Billing**: `DescribeBillSummaryByProduct`, `DescribeBillDetail`, `DescribeAccountBalance` 等
- **CAM**: `ListUsers`, `ListPolicies`, `GetUser`, `GetRole` 等
- **CLS**: `SearchLog`, `DescribeLogsets`, `DescribeTopics` 等
- **SCF**: `ListFunctions`, `GetFunction`, `GetFunctionLogs` 等

#### 高危操作（必须用户确认）

以下操作会修改资源状态，**必须在执行前向用户展示完整命令并获得明确确认**：

- **创建资源**: `Create*`, `RunInstances` 等
- **修改资源**: `Modify*`, `Update*`, `Reset*` 等
- **删除资源**: `Delete*`, `Terminate*`, `Destroy*` 等
- **启停操作**: `StartInstances`, `StopInstances`, `RebootInstances` 等
- **安全相关**: 安全组规则变更、CAM 策略变更
- **DNS 变更**: `CreateRecord`, `ModifyRecord`, `DeleteRecord`
- **CDN 操作**: `PurgeUrlsCache`, `PurgePathCache`, `PushUrlsCache`
- **数据库操作**: `CreateBackup`, `RestoreDBInstanceFromBackup`, `IsolateDBInstance`

确认格式：

```
⚠️ 即将执行写操作：

  账户: 腾讯云 prod-cn (AppId: 1302606863)
  区域: ap-beijing
  命令: tccli cvm StopInstances --InstanceIds '["ins-abc123"]'

确认执行？(y/n)
```

### 操作红线

1. **禁止未确认的写操作** — 任何写操作都必须二次确认
2. **禁止暴露密钥** — SecretKey、数据库密码等只展示前 4 位 + `***`
3. **禁止大范围删除** — 批量 Terminate/Delete 必须先列出受影响资源并确认
4. **COS 操作** — tccli 不直接支持 COS 数据操作，需使用 `coscmd` 工具，提醒用户
5. **费用查询** — 默认查询最近一个月，避免拉取过大时间范围

### 分页查询（重要）

tccli 列表接口有分页限制，**必须处理分页以确保结果完整**：

1. 先查 TotalCount 确认资源总数：`tccli <Service> <Action> --filter 'TotalCount'`
2. 如果 TotalCount > Limit，需要多次查询：
   - 第一页：`--Limit 100 --Offset 0`
   - 第二页：`--Limit 100 --Offset 100`
   - 依此类推直到取完
3. 使用 JMESPath `contains()` 等客户端过滤时，必须确保已获取全部数据，否则会遗漏结果
4. 资源数量会变化，按目标账号实时 `TotalCount` 分页；不要沿用历史 prod 账号的实例数，也不要假定单次 `--Limit 100` 覆盖全部

### 输出优化

- 使用 `--filter` (JMESPath) 过滤关键字段，避免输出过长
- 列表类查询默认 Limit 通常为 20，建议指定 `--Limit 100` 并结合分页

## Examples

### Good

```
# 查看 CVM 实例
用户：看看腾讯云上有哪些机器

AI：查询 CVM 实例：
  [先校验目标账户，然后执行 tccli cvm DescribeInstances --profile <已验证 profile> --region ap-beijing --filter 'InstanceSet[*].{InstanceId:InstanceId,InstanceName:InstanceName,PublicIpAddresses:PublicIpAddresses,PrivateIpAddresses:PrivateIpAddresses,InstanceState:InstanceState,InstanceType:InstanceType}']
```

```
# 查看 TKE 集群
用户：看看腾讯云的 K8s 集群

AI：查询 TKE 集群：
  [按环境选择并校验主账号后，执行 tccli tke DescribeClusters --profile <已验证 profile> --region ap-beijing]
  展示实际返回的 ClusterId / ClusterName，不把本机 context 别名当成云端集群名。
```

```
# 写操作确认
用户：帮我重启一下那台 CVM

AI：找到实例：
  ins-abc123 (web-server) - RUNNING

  ⚠️ 即将执行写操作：

    账户: 腾讯云 prod-cn (AppId: 1302606863)
    区域: ap-beijing
    命令: tccli cvm RebootInstances --profile <已验证 profile> --region ap-beijing --InstanceIds '["ins-abc123"]'

  确认执行？(y/n)
```

### Bad

```
# 未确认就执行写操作
用户：停掉那台测试机
AI：已停止。 ← 未展示影响范围和确认

# 暴露密钥
AI：数据库密码为 MyP@ssw0rd123  ← 应脱敏

# COS 操作用 tccli
tccli cos PutObject ...  ← tccli 不支持 COS 数据操作，应用 coscmd
```

## 豁免

| 场景 | 条件 |
|------|------|
| 紧急故障恢复 | 用户明确声明紧急情况，可简化确认流程（仍需展示命令） |
