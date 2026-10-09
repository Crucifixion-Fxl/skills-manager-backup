---
name: aws-cli
description: 通过 AWS CLI 管理多账户、多区域 AWS 资源。当用户提到 AWS、EC2、S3、RDS、Lambda、CloudWatch、ECS、EKS、IAM、SQS、SNS、DynamoDB、CloudFront、Route53、ACM、Secrets Manager、Parameter Store、Cost Explorer，或需要查询/操作 AWS 云资源、查看 AWS 费用账单时使用。即使用户只说"查一下美区的服务器"、"看看 prod 的 EC2"、"AWS 这个月花了多少"也应触发。
---

# aws-cli

## Description

首次接入/变更扫描与日常认证入口见 [SaaS 接入](references/saas-access.md)；已有平台业务契约与授权门禁仍在本 Skill 维护。

通过 AWS CLI 协助运维和开发人员管理公司多账户、多区域的 AWS 资源。覆盖所有常见 AWS 服务的读操作，写操作需用户确认。

## 账户体系

当前 AWS 集群覆盖 US/EU/SG 等区域。**AWS CN 集群已弃用，当前 CN 环境全部在腾讯云**：prod=`100014919455`，staging / tech-service=`100052802231`。CN 日常任务改用 `tencent-cloud-cli`；只有明确查询历史 AWS CN 资源时才使用下方 legacy 账号，不由旧 profile 存在推断仍有活跃集群。

### 账户映射表

> **Profile 选择规则**：下表中的 profile 是共享环境的参考名称，不保证与每位操作者本机 `~/.aws/config` 中的名称完全相同。`aws configure list-profiles` 的结果才是本机可用 profile 的权威来源；不得假设 `aws-<账号ID>-<env>`、`prod-us` 或任何其他固定命名模式，也不得依赖 `default` profile。对目标账户完成只读身份校验后，才可选用实际 profile。

| 参考 Profile | Account ID | Region | 分区 | 用途 |
|---------|-----------|--------|------|------|
| `aws-302571458622-us-prod` | 302571458622 | us-east-1 | aws | 美区生产 |
| `aws-002497567426-us-tech-service` | 002497567426 | us-east-1 | aws | 美区技术服务（工作负载逐步迁 584） |
| `aws-584949097249-us-tech-service` | 584949097249 | us-east-1 | aws | 美区技术服务（002 的迁移目标） |
| `aws-769494896000-us-data` | 769494896000 | us-east-1 | aws | 美区数据平台（加 `--region eu-central-1` 即 eu-data） |
| `aws-459574162536-us-old` | 459574162536 | us-east-1 | aws | 美区旧账号 |
| `aws-390709477306-us-staging` | 390709477306 | us-east-1 | aws | 美区 staging（与 eu-staging 同账号） |
| `aws-390709477306-eu-staging` | 390709477306 | eu-central-1 | aws | 欧区 staging（同账号 390709477306） |
| `aws-740315635167-eu-prod` | 740315635167 | eu-central-1 | aws | 欧区生产 |
| `aws-010840394398-eu-tech-service` | 010840394398 | eu-central-1 | aws | 欧区技术服务 |
| `aws-741924744516-cn-prod` | 741924744516 | cn-north-1 | aws-cn | **已弃用 AWS CN 集群的历史账号**；国区生产 |
| `aws-589899215075-cn-tech-service` | 589899215075 | cn-north-1 | aws-cn | **已弃用 AWS CN 集群的历史账号**；国区技术服务 |
| `aws-801447536674-cn-dev` | 801447536674 | cn-north-1 | aws-cn | **已弃用 AWS CN 集群的历史账号**；国区开发（`cn-eks-dev` 已删除，`cn-eks-staging` 已停业务；S3 bucket 按要求保留，禁止删除） |
| `aws-437416304740-cn-old` | 437416304740 | cn-north-1 | aws-cn | **已弃用 AWS CN 集群的历史账号**；国区旧账号 |
| `aws-125710977284-sg-devops` | 125710977284 | ap-southeast-1 | aws | DevOps 基础设施（新加坡） |
| `aws-343938550037-kr-dev` | 343938550037 | ap-northeast-2 | aws | 开发（韩国；EKS 集群已删，账号尚存） |

> 国区 dev/staging 当前统一在腾讯云 `100052802231` / `cls-riukakjb`（`staging-cn`）；不要复用旧 AWS dev profile 或访问旧 `cn-eks-dev` / `cn-eks-staging`。历史账号仍可能保留迁移资源（如 S3），历史盘点需用户明确范围。801447536674 的 S3 bucket 与 object 均需保留：Crossplane 管理角色已挂 `RetirementS3DeletionGuard`（Deny `s3:*`），不要解除，也不要为“清理”发起任何 S3 删除或生命周期变更。

### 区域速查

用户提到的关键词与参考 profile 的映射：

| 关键词 | 优先匹配参考 Profile |
|--------|-----------------|
| 美区/US/美国 + 生产/prod | `aws-302571458622-us-prod` |
| 美区 + 技术服务/tech | `aws-002497567426-us-tech-service`（迁移目标 `aws-584949097249-us-tech-service`） |
| 美区 + staging | `aws-390709477306-us-staging` |
| 美区 + 数据/data | `aws-769494896000-us-data` |
| 欧区/EU/欧洲 + 生产/prod | `aws-740315635167-eu-prod` |
| 欧区 + 技术服务/tech | `aws-010840394398-eu-tech-service` |
| 欧区 + staging | `aws-390709477306-eu-staging` |
| 欧区 + 数据/data | `aws-769494896000-us-data --region eu-central-1` |
| 国区/CN/中国 + 生产/prod | 转 `tencent-cloud-cli`，账号 `100014919455` / `cn-main` |
| 国区 + 技术服务/tech | 转 `tencent-cloud-cli`，账号 `100052802231` / `cn-tech-service` |
| 国区 + 开发/dev | 先核对当前 TKE 应用落点；不沿用已弃用 CN Dev 集群 |
| 国区 + staging | 转 `tencent-cloud-cli`，账号 `100052802231` / `cn-staging` |
| devops/运维 | `aws-125710977284-sg-devops` |

## 执行流程

### Step 1: 发现并校验本机 profile

先以只读方式确认本机可用 profile，并对候选 profile 校验目标账户；不要由账户映射表反推本机 profile 名称：

```bash
aws configure list-profiles
aws sts get-caller-identity --profile <candidate-profile> --output json
```

仅对根据用户目标和上表已缩小范围的候选 profile 进行校验。只有当 STS 返回的 `Account` 与目标账户 ID 一致时，才可将该实际 profile 用于后续命令。若找不到匹配 profile、认证失效或 AWS CLI 不可用，读取 `references/setup.md` 并按引导处理；不要改用 `default`，也不要要求用户粘贴长期凭证。

同一账户可能通过多个区域别名 profile 复用凭据。参考 profile 不存在、
存在但没有凭据，或其名称与目标环境不一致时，不代表账户不可访问：继续在
本机列表中寻找合理候选，并用 STS 校验 `Account`。例如 staging US/EU 共用
账户 `390709477306`，本机可用的 EU 候选可能仍带 `us-staging` 前缀。只有
STS 账户匹配后才可使用，并为目标集群显式传入 `--region`；不得为了修正
名称而写入 `~/.aws/config`、复制凭据或要求用户重新提供长期密钥。

### Step 2: 确认操作目标

根据用户描述确定：

1. **目标账户与实际 profile**：匹配上方账户映射表，确定目标账户 ID 和参考 profile；再以 Step 1 的 STS 结果选择匹配的本机实际 profile。
   - 如果多个已验证 profile 指向同一目标账户，结合配置的默认区域或用户指定区域选择；仍无法判断时，询问用户选择哪个已验证 profile。
   - 如果上下文可以明确推断（如“查看美区生产环境的 EC2”），先验证一个本机候选 profile 指向账户 `302571458622`，再告知用户实际使用的 profile 并执行。
2. **目标区域**：单区域 profile 可使用其已核对的默认 region；集群已知、同账户跨区域或 profile 名称与环境不一致时，必须显式传入 `--region`，不能从 profile 名称推断区域
3. **操作内容**：判断读/写操作

### 跨账户可用区映射

AWS 的可用区名称（如 `us-east-1a`）是账户本地别名，同一个名称在不同账户中不保证指向同一物理可用区。跨账户分析 VPC Peering、Transit Gateway、Kafka、RDS、EKS 调度或跨 AZ 费用时，**必须按 `ZoneId` 映射**，不得直接比较 `ZoneName`。

分别使用已经过 STS 校验的源、目标账户 profile 查询：

```bash
aws ec2 describe-availability-zones \
  --profile <source-profile> \
  --region <region> \
  --all-availability-zones \
  --query 'AvailabilityZones[].{ZoneName:ZoneName,ZoneId:ZoneId,State:State}' \
  --output json

aws ec2 describe-availability-zones \
  --profile <target-profile> \
  --region <region> \
  --all-availability-zones \
  --query 'AvailabilityZones[].{ZoneName:ZoneName,ZoneId:ZoneId,State:State}' \
  --output json
```

以相同 `ZoneId` 连接两边结果，输出明确的 `源账户 ZoneName -> ZoneId -> 目标账户 ZoneName` 映射，再用目标账户的 `ZoneName` 配置子网或 Kubernetes topology selector。还应通过 `describe-subnets`、EC2 实例的 `SubnetId`/`Placement.AvailabilityZone` 或 Pod 所在节点验证实际落点。若任一侧查不到相同 `ZoneId`，停止推断并报告缺口。

### Step 3: 执行命令

所有命令必须显式指定 `--profile`，**不要依赖 default profile**：

```bash
aws <service> <command> --profile <profile> [--region <region>] [--output json|table|text]
```

## Rules

### 查询范围约束

**默认单 profile 查询**：除非用户明确要求（如"查看所有账户的资源"、"对比各个 profile"），否则只在单个 profile 中查询资源，不要自动遍历所有 profile 进行全局查找。

### 操作分级

#### 只读操作（直接执行）

所有 `describe-*`、`list-*`、`get-*`、`head-*` 类命令均为只读，可直接执行：

- **EC2**: `describe-instances`, `describe-security-groups`, `describe-vpcs`, `describe-subnets` 等
- **S3**: `ls`, `head-object`（注意 `s3 cp`/`s3 sync` 下载方向为只读）
- **RDS**: `describe-db-instances`, `describe-db-clusters` 等
- **ECS**: `describe-clusters`, `describe-services`, `describe-tasks`, `list-services` 等
- **EKS**: `describe-cluster`, `list-clusters` 等
- **Lambda**: `list-functions`, `get-function`, `get-function-configuration` 等
- **CloudWatch**: `get-metric-data`, `describe-alarms`, `get-log-events`, `filter-log-events` 等
- **IAM**: `list-users`, `list-roles`, `list-policies`, `get-role`, `get-policy` 等
- **SQS**: `list-queues`, `get-queue-attributes` 等
- **SNS**: `list-topics`, `list-subscriptions` 等
- **DynamoDB**: `describe-table`, `list-tables`, `scan`（小表）, `query` 等
- **CloudFront**: `list-distributions`, `get-distribution` 等
- **Route53**: `list-hosted-zones`, `list-resource-record-sets` 等
- **ACM**: `list-certificates`, `describe-certificate` 等
- **Secrets Manager**: `list-secrets`（注意：`get-secret-value` 会暴露密钥，需提醒用户）
- **SSM Parameter Store**: `get-parameter`, `get-parameters-by-path` 等
- **STS**: `get-caller-identity`
- **CloudFormation**: `describe-stacks`, `list-stacks` 等
- **ECR**: `describe-repositories`, `list-images`, `describe-images` 等
- **ElastiCache**: `describe-cache-clusters`, `describe-replication-groups` 等
- **Elasticsearch/OpenSearch**: `describe-domain`, `list-domain-names` 等
- **Cost Explorer**: `get-cost-and-usage` 等

#### 高危操作（必须用户确认）

以下操作会修改资源状态，**必须在执行前向用户展示完整命令并获得明确确认**：

- **创建资源**: `create-*`, `run-instances`, `put-*` 等
- **修改资源**: `modify-*`, `update-*`, `tag-resource`, `untag-resource` 等
- **删除资源**: `delete-*`, `terminate-instances`, `deregister-*`, `remove-*` 等
- **启停操作**: `start-instances`, `stop-instances`, `reboot-instances` 等
- **S3 写操作**: `s3 cp`（上传方向）, `s3 rm`, `s3 sync`（上传方向）, `s3 mb`, `s3 rb`
- **安全相关**: `authorize-security-group-ingress/egress`, `revoke-*`, IAM 写操作
- **数据库操作**: `create-db-snapshot`, `restore-db-instance-*`, `delete-db-instance` 等
- **DNS 变更**: `change-resource-record-sets`
- **Lambda 部署**: `update-function-code`, `update-function-configuration`, `publish-version`
- **Secrets 写入**: `create-secret`, `update-secret`, `put-secret-value`

确认格式：

```
⚠️ 即将执行写操作：

  账户: 美区生产 (302571458622)
  Profile: <本机已验证的 profile>
  区域: us-east-1
  命令: aws ec2 terminate-instances --instance-ids i-0abc123 --profile <本机已验证的 profile>

确认执行？(y/n)
```

### 操作红线

1. **禁止在生产环境执行未确认的写操作** — 映射为生产账户的任何写操作都必须二次确认
2. **禁止暴露完整密钥** — `get-secret-value` 结果中如包含密钥/密码，只展示前 4 位 + `***`
3. **禁止大范围删除** — 批量删除操作（如 `s3 rm --recursive`、批量 terminate）必须先列出受影响资源并确认
4. **DynamoDB scan 大表保护** — 对未知大小的表执行 scan 时，先用 `describe-table` 检查 ItemCount，超过 10000 条时提醒用户并建议使用 `--max-items` 或 `query`
5. **Cost Explorer 查询** — 默认查询最近 30 天，避免拉取过大时间范围导致超时

### 输出优化

- 资源列表类查询默认使用 `--output table` 提升可读性
- 需要程序化处理时使用 `--output json`
- 使用 `--query` (JMESPath) 过滤关键字段，避免输出过长
- 对于大量结果，使用 `--max-items` 限制返回数量

## Examples

### Good

```
# 跨账户查询
用户：帮我看看美区和欧区生产的 EC2 实例

AI：查询两个区域的 EC2 实例：

  美区生产 (302571458622):
  [先验证，再执行 aws ec2 describe-instances --profile <本机已验证的美区生产 profile> --query ...]

  欧区生产 (740315635167):
  [先验证，再执行 aws ec2 describe-instances --profile <本机已验证的欧区生产 profile> --query ...]
```

```
# 写操作确认
用户：帮我把 dev 环境的那台测试 EC2 停掉

AI：找到以下实例：
  i-0abc123 (test-server) - running

  ⚠️ 即将执行写操作：

    账户: 开发环境 (343938550037)
    Profile: <本机已验证的开发环境 profile>
    区域: ap-northeast-2
    命令: aws ec2 stop-instances --instance-ids i-0abc123 --profile <本机已验证的开发环境 profile>

  确认执行？(y/n)
```

```
# 密钥保护
用户：查一下账号 002497567426 的某个 secret

AI：查到 Secret: my-api-key
  值: sk-a1b2****（已脱敏，如需完整值请确认）
```

```
# 跨账户 AZ 映射
用户：把生产账户 Kafka 的 us-east-1b 对齐到技术服务账户

AI：
  [分别校验两个本机 profile，并查询 describe-availability-zones]
  源账户 us-east-1b -> use1-az2 -> 目标账户 us-east-1d

  因此目标账户的子网或 Pod selector 应使用 us-east-1d；不能直接沿用 us-east-1b。
```

### Bad

```
# 未指定 profile
aws ec2 describe-instances  ← 缺少 --profile，会使用 default

# 未确认就执行写操作
用户：删掉这个 S3 bucket
AI：已删除。 ← 未展示影响范围和确认

# 暴露密钥
AI：Secret 值为 sk-a1b2c3d4e5f6g7h8i9j0  ← 完整暴露

# 跨账户直接比较 AZ 名称
源账户和目标账户都叫 us-east-1b，所以它们是同一个物理 AZ。 ← 错误，必须比较 ZoneId
```

## 豁免

| 场景 | 条件 |
|------|------|
| 紧急故障恢复 | 用户明确声明紧急情况，可简化确认流程（仍需展示命令） |
| 已核验的活跃 dev 环境 | 开发环境的非删除类写操作可简化确认 |

## 原生 CLI 浏览器授权与远程消费

保留已有授权 profile、STS 或密钥方式；优先恢复其原有认证，不复制浏览器 Cookie。IAM Identity Center 使用已配置的 `aws sso login --profile <selected-profile>`；控制台 IAM／联合身份可在 CLI >=2.32.0 使用 `aws login --profile <selected-profile>`，远端无浏览器时使用 `--remote`，在任务浏览器打开官方原始授权 URL，再私密回传授权码。`--remote` 是跨设备 OAuth 授权码流程，不能称 device-code grant，也不能把远端认证成功当作本机凭据已就绪。禁止将 URL 中的授权参数、授权码或缓存凭据放入日志、聊天、argv 或报告。需要用户登录或 MFA 时将任务页置前并有界检测，之后继续官方交换。

`aws login` 可能更新该 profile 的 login_session 与官方缓存，现有会话不匹配时不要自动覆盖；新会话使用任务隔离配置与缓存机制，先确认实际支持路径，不能通过改 HOME 影响其他工具。登录要求既有 SignInLocalDevelopmentAccess 权限时，缺权只记录，不为验收添加 IAM policy。登录后分别核本机、远端 STS 的目标 Account／subject，再做有界授权读取；不默认 profile、不遍历所有账户或退回历史 CN。清理按官方 profile 作用域操作，不能无 profile logout 清掉既有会话。

官方安装入口支持用户目录安装，先下载并审查脚本、固定同一版本和已存在路径保护，再设置任务专用 XDG_DATA_HOME／XDG_BIN_HOME；不覆盖系统 CLI 或修改全局 PATH。Linux 检查隔离 keyring 中的官方 PGP 签名；macOS 核官方 Apple Developer team。安装及版本／profile 名称发现只证明运行条件，不能记为真实身份或资源验收。见 [官方登录说明](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-sign-in.html) 与 [官方安装说明](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html)。

### `aws login` 的配置与缓存隔离

控制台登录的官方缓存目录支持 `AWS_LOGIN_CACHE_DIRECTORY`。任务登录同时固定任务独有的 `AWS_CONFIG_FILE`、`AWS_SHARED_CREDENTIALS_FILE` 和 `AWS_LOGIN_CACHE_DIRECTORY`，并显式给 `--profile`；不修改 HOME，不读取或复制其他 profile 内容。这些环境变量应传给登录、STS／后续原生读取和指定 profile 的 logout 全链，而非只传给安装器。密钥文件路径可指向任务空文件；配置仅写已选 profile 的公共 region 等字段，OAuth session／缓存由官方 CLI 写入。报告只记录路径、profile、权限／身份核验和清理布尔值，绝不保存缓存内容。

`AWS_LOGIN_CACHE_DIRECTORY` 仅适用于 console-login provider；不能推断它也隔离 IAM Identity Center 的 SSO cache。SSO 路径需另核官方实现／隔离能力，不因覆盖 config 文件就声称所有缓存隔离完成。缺明确账号、region 或 CLI 授权权限时保持待输入，不新建长期 Key、不改 IAM policy。正常 OAuth 创建的本地缓存属于登录流程，但不能把工具准备／离线配置测试计为真实鉴权。参考 [login provider 设置](https://docs.aws.amazon.com/sdkref/latest/guide/feature-login-credentials.html)。
