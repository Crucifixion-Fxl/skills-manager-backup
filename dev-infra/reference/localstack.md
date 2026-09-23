# LocalStack 选型参考（dev-infra Rule 1b 配套文档）

> 本文档配合 SKILL.md `Rule 1b — AWS 依赖的本地化分级` 使用。
> Rule 1b 决定**要不要用** LocalStack；本文档解决**用哪个 tier、哪些功能可用**。

---

## 1. 关键事实（决定选型前必须先知道）

### 1.1 LocalStack 始终跑在 docker-compose 里
LocalStack 不是 docker-compose 的"替代方案"——它本身就是一个 docker-compose service，归在 **Layer 1（全局共享 Infra）**。所以决策从来不是 "docker-compose vs LocalStack"，而是：

> **对每个 AWS 依赖，L1 这一格放什么镜像？**
>
> - 放开源等价镜像（postgres / redis / kafka / flink / minio / pgvector …）
> - 还是放 `localstack/localstack` 镜像（Community 版）
> - 还是放 `localstack/localstack-pro` 镜像（需 LOCALSTACK_AUTH_TOKEN）

三种本质都是 docker-compose service，只是镜像不一样。

### 1.2 开源仓库已归档（2026-03-23）
```
github.com/localstack/localstack → READ-ONLY since 2026-03-23
```
- License 仍是 Apache 2.0，已存在的 Community 镜像可以继续拉取
- 新功能/新 AWS 服务**不会**进 Community；活跃开发迁到了 "LocalStack for AWS" 闭源项目
- **影响**：Community 已是"维护态"；作为长期基础设施时可持续性下降，建议 pin 镜像版本 + 镜像同步到内部 Harbor

### 1.3 Tier 命名对应关系
| 官网 Plan 名 | 镜像 | 是否需要 Token |
|---|---|---|
| **Hobby** | `localstack/localstack` | ❌ 不需要（这就是 Community / OSS 版） |
| **Base** | `localstack/localstack-pro` | ✅ 需要 `LOCALSTACK_AUTH_TOKEN` |
| **Ultimate** | `localstack/localstack-pro` | ✅ 需要 token + license tier |
| **Enterprise** | 同上 + 企业附加（SSO / Operator / 私有部署） | ✅ |

⚠️ 在官方 service 详情页看到 `Included in Plans: Hobby Base Ultimate` 即等同于"Community 支持"。

---

## 2. Community Edition 支持的服务清单（权威）

**取证方法**：交叉验证两个来源——
1. 归档前的 OSS 源码目录 `localstack-core/localstack/services/`（每个目录 = 一个 Community 实现）
2. 每个服务文档页 `https://docs.localstack.cloud/aws/services/{service}/` 顶部的 `Included in Plans:` badge 含 `Hobby`

**最后核对日期**：2026-04-30

### 2.1 Community 完整服务列表（39 个）

| 类别 | 服务 | OSS 目录 | AWS 服务名 |
|---|---|---|---|
| **Compute** | Lambda | `lambda_/` | AWS Lambda |
|  | EC2 | `ec2/` | Elastic Compute Cloud |
| **Containers** | ECR | `ecr/` | Elastic Container Registry |
| **Storage** | S3 | `s3/` | Simple Storage Service |
|  | S3 Control | `s3control/` | S3 Account-level controls |
| **Database** | DynamoDB | `dynamodb/` | DynamoDB |
|  | DynamoDB Streams | `dynamodbstreams/` | DynamoDB Streams |
|  | Redshift | `redshift/` | Redshift（注意：仿真度有限） |
| **Messaging / Streaming** | SQS | `sqs/` | Simple Queue Service |
|  | SNS | `sns/` | Simple Notification Service |
|  | EventBridge | `events/` | EventBridge |
|  | EventBridge Scheduler | `scheduler/` | EventBridge Scheduler |
|  | Kinesis Data Streams | `kinesis/` | Kinesis Data Streams |
|  | Kinesis Firehose | `firehose/`, `kinesisfirehose/` | Data Firehose |
| **Workflow** | Step Functions | `stepfunctions/` | Step Functions |
|  | SWF | `swf/` | Simple Workflow Service |
| **Identity / Security** | IAM | `iam/` | IAM（注意：Community 无 Policy Enforcement） |
|  | STS | `sts/` | Security Token Service |
|  | KMS | `kms/` | Key Management Service |
|  | Secrets Manager | `secretsmanager/` | Secrets Manager |
|  | ACM | `acm/`, `certificatemanager/` | Certificate Manager |
| **Networking / DNS** | Route 53 | `route53/` | Route 53 |
|  | Route 53 Resolver | `route53resolver/` | Route 53 Resolver |
| **API** | API Gateway | `apigateway/` | API Gateway（v1 较完整，v2 有限） |
| **DevOps / Provisioning** | CloudFormation | `cloudformation/` | CloudFormation |
|  | CloudWatch | `cloudwatch/` | CloudWatch Metrics |
|  | CloudWatch Logs | `logs/` | CloudWatch Logs |
|  | Config | `configservice/` | AWS Config |
|  | Resource Groups | `resource_groups/` | Resource Groups |
|  | Resource Groups Tagging | `resourcegroupstaggingapi/` | Tagging API |
|  | Systems Manager | `ssm/` | SSM Parameter Store + 部分 SSM |
| **Search** | Elasticsearch Service | `es/` | Elasticsearch（旧版 API） |
|  | OpenSearch Service | `opensearch/` | OpenSearch |
| **Email** | SES | `ses/` | Simple Email Service |
| **Misc** | Support | `support/` | AWS Support API（stub） |
|  | Transcribe | `transcribe/` | Transcribe（基础） |
| **内部** | CDK | `cdk/` | 不是 AWS 服务，是 CDK 适配器 |

### 2.2 Community **不支持**的常用服务（必须 Base/Ultimate）

下面这些是开发中**最容易踩坑**的——你以为能用，但启动后会报 `not yet implemented or pro feature`：

| 服务 | 最低 tier | 备注 |
|---|---|---|
| **RDS** | Base | 内嵌真 Postgres/MySQL 进程 |
| **ElastiCache** | Base | 内嵌真 Redis |
| **Cognito** | Base | User Pool / Identity Pool |
| **API Gateway v2 (HTTP/WebSocket)** | Base | v1 在 Community 可用 |
| **AppConfig** | Base |  |
| **CodeBuild / CodeCommit / CodeArtifact** | Base |  |
| **IoT Core** | Base |  |
| **ECS / Fargate** | Base | EKS 在 Ultimate |
| **EKS** | Ultimate | 但本地用 kind/k3d 更标准 |
| **Glue / Athena / EMR** | Ultimate |  |
| **MSK (Managed Kafka)** | Ultimate | 本地直接跑 confluent/cp-kafka |
| **Managed Service for Apache Flink** | Ultimate | 本地直接跑 flink 官方镜像 |
| **AppSync** | Ultimate |  |
| **Bedrock / SageMaker** | Ultimate |  |
| **Organizations / CloudTrail** | Ultimate |  |
| **S3 Vectors** | ❌ 截至 2026-04 不支持 | 本地用 pgvector / Qdrant / Weaviate |
| **DocumentDB** | Pro 系列 | 本地用 mongo |
| **Neptune** | Pro 系列 | 本地用 neo4j |
| **Chaos Engineering** | Enterprise | Ultimate 也没有 |

### 2.3 Community 支持但**功能受限**（最坑的灰色地带）

服务名出现在 Community 列表 ≠ 你要用的 operation 也免费。常见陷阱：

| 服务 | Community 支持 | Pro 才有 |
|---|---|---|
| **Lambda** | 创建/调用/Function URL/SQS-DynamoDB-Kinesis 触发器/热重载 | Lambda Layers、Provisioned Concurrency、MSK/Self-managed Kafka 触发器 |
| **S3** | 全部基础 API | S3 Object Lambda、S3 Replication、S3 Notifications 高级路由 |
| **IAM** | API 调用、resource 创建 | **Policy Enforcement**（按 policy 真拒请求）、Policy Streams |
| **Step Functions** | 基础执行 | Express workflows 高级特性、外部 service integrations |
| **Redshift** | API stub | 真实 query engine |
| **DynamoDB** | 全部基础 | Global Tables、Backup/Restore 部分 API |
| **SES** | SMTP/API stub，邮件落本地 outbox | 高级模板、专用 IP |

---

## 3. Tier 边界查询的官方入口

> Base/Ultimate 的服务范围会持续变化（新服务上线、tier 调整），**不要把具体清单写死在代码或文档里**。需要时按以下入口查最新：

### 3.1 官方权威页面

| 用途 | URL |
|---|---|
| **Plan 对比 + 服务清单**（按 tier 粗粒度） | https://docs.localstack.cloud/aws/licensing/ |
| **单个服务 tier badge**（最准） | https://docs.localstack.cloud/aws/services/{service}/ |
| **Coverage 索引**（所有服务名 + 详情链接） | https://docs.localstack.cloud/references/coverage/ |
| **License 类型**（Hobby/Base/Ultimate/Enterprise 的能力差） | https://docs.localstack.cloud/aws/licensing/ |
| **Pricing 价格** | https://www.localstack.cloud/pricing |

### 3.2 单服务实战查法

判断"X 服务的 Y operation 在免费版能不能用"，按如下三步走，文档总会滞后，最后一步是兜底：

```bash
# 1) 文档 badge 看 tier
open https://docs.localstack.cloud/aws/services/{service}/
# 找页面顶部 "Included in Plans: Hobby Base Ultimate" — 含 Hobby 即 Community 支持

# 2) 文档 operation 表看具体接口
open https://docs.localstack.cloud/aws/services/{service}/#api-coverage
# 看每个 API 标 ✅（实现）/ ⚠️（stub）/ ❌（未实现）/ Pro

# 3) 实测（最可靠）—— 启动 Community 镜像跑你真实要用的 SDK 调用
docker run --rm -d --name ls-probe -p 4566:4566 localstack/localstack:latest
sleep 5
aws --endpoint-url=http://localhost:4566 {service} {operation} ...
# 报错信息直接告诉你 "not yet implemented or pro feature"
docker stop ls-probe
```

---

## 4. dev-infra 落地决策（结合 Rule 1 / Rule 1b）

### 4.1 决策树

```
新增一个 AWS 服务依赖到本地 stack
        │
        ▼
是否有成熟开源等价镜像？
├── 是 ──► docker-compose 跑该开源镜像（首选）
│         例：RDS → postgres、ElastiCache → redis、MSK → confluent/cp-kafka、
│            Flink → flink、ES/OpenSearch → opensearch、S3 Vectors → pgvector/qdrant
│
└── 否 ──► 该服务在 §2.1 Community 列表里？
          ├── 是 ──► docker-compose 跑 localstack/localstack（Community）
          │
          └── 否 ──► 评估三选一：
                    ├── A. 自己写 mock / stub（成本最低，仿真度最差）
                    ├── B. 调真实 staging AWS（最真实，需 cleanup + 成本可控）
                    └── C. LocalStack Pro（仅当团队已有 license 且高频使用）
```

### 4.2 docker-compose 接入 LocalStack 的标准片段

```yaml
# docker-compose.dev.yml — Layer 1 共享 infra
services:
  localstack:
    image: localstack/localstack:3.x          # ⚠️ pin 版本，归档后不再更新
    container_name: ls-${WORKTREE_ID:-default}
    ports:
      - "127.0.0.1:4566:4566"                  # 单端点
      - "127.0.0.1:4510-4559:4510-4559"        # RDS/ElastiCache 等需要这个动态端口段（仅 Pro）
    environment:
      - SERVICES=s3,sqs,sns,lambda,events,stepfunctions,kms,secretsmanager
        # ⬆️ 显式列出，启动快得多；不列则启动所有 Community 服务
      - PERSISTENCE=0                          # CI 设 0，本地 dev 想保留状态可设 1（Pro 才稳定）
      - DEBUG=0
      - LAMBDA_EXECUTOR=docker-reuse           # Lambda 触发更接近真实
      # - LOCALSTACK_AUTH_TOKEN=${LS_TOKEN}    # ⬅️ 仅 Pro 镜像需要
    volumes:
      - "/var/run/docker.sock:/var/run/docker.sock"  # Lambda 起 docker 容器需要
      - "ls-data:/var/lib/localstack"
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://localhost:4566/_localstack/health"]
      interval: 5s
      timeout: 3s
      retries: 30

volumes:
  ls-data:
```

应用侧：

```python
# Python boto3
import boto3
s3 = boto3.client("s3", endpoint_url="http://localhost:4566")

# Node.js AWS SDK v3
import { S3Client } from "@aws-sdk/client-s3";
const s3 = new S3Client({ endpoint: "http://localhost:4566", forcePathStyle: true });
```

### 4.3 与 dev-infra 现有 Rule 的协同

| dev-infra Rule | 在 LocalStack 场景下的映射 |
|---|---|
| **Rule 1**（服务分层） | LocalStack 容器 = Layer 1 共享 infra |
| **Rule 2**（动态端口）/ **Rule 12a**（运行时探测） | LocalStack 默认端口 4566；多 worktree 共用一个 LocalStack 容器（与 Rule 12b 共享 MySQL 同思路）足够，不需要每个 worktree 各起一份 |
| **Rule 4**（幂等 ensure-up） | `ensure_localstack_up` 走和 `ensure_mysql` 一样的模式 |
| **Rule 4c**（健康探活） | 探活点 = `curl http://localhost:4566/_localstack/health`，必须等返回 `{"services": {...}}` 全 `running` |
| **Rule 9**（操作红线） | 禁止把 `LOCALSTACK_AUTH_TOKEN` 提交到 Git；token 进 Vault / `.env.local` |

---

## 5. 常见误区

| ❌ 误区 | ✅ 真相 |
|---|---|
| "LocalStack 是 docker-compose 的替代品" | LocalStack 自己就是一个 docker-compose service，跑在 L1 |
| "服务名在 Community 列表里 = 全功能" | 见 §2.3，operation 粒度可能仍是 Pro |
| "Community 免费 = 永远首选" | 仓库已归档（§1.2），开源等价镜像更可持续 |
| "用 LocalStack 测 RDS 比 Postgres 真实" | LocalStack 的 RDS 内嵌 Postgres 实例，跟你直接跑 postgres 镜像本质一样，但版本/扩展受限——直接跑 postgres 更真 |
| "Pro 是按机器/CI 计费" | 是 **per developer license**，CI 不消耗个人 license（见 pricing 页 FAQ） |

---

## 6. Community 归档之后：fork 与替代品

LocalStack OSS（2026-03-23 归档）后，社区**没有出现一个清晰的、被广泛认可的"社区版接班人"**。下面是 2026-04 实测的现状盘点。

### 6.1 LocalStack 的 fork 现状（不推荐用）

```bash
# 截至 2026-04-30 的活跃 fork（github.com/localstack/localstack/forks）
localstack-bb       # 原维护者另起的仓库，stars 仅 4，定位不明
cyber-eternal/...   # 个人 fork，stars 2
cptthura-alt/...    # 个人 fork，stars 1
SNiTEBoBy/...       # 个人 fork，stars 1
```

**结论**：当前没有任何 fork 形成"社区共识接班"。原维护者的 `localstack-bb` 看着像是定向继续，但官方没有发声明把它推为 Community successor。**不要赌任何一个 fork 会跑出来**——选 fork 等于把维护责任放到一个 4-star 项目肩上。

### 6.2 真正的替代品（按场景）

> **核心原则：尽可能真实。** 仿真度由高到低：真实 AWS > 开源镜像 > LocalStack Pro > LocalStack Community > **Moto** > 自写 stub。Moto 是末位备选，不是默认。

#### A. **Moto + moto_server**（仿真度末位，仅在受限场景用）

- **GitHub**: https://github.com/getmoto/moto · Apache 2.0 · 最新 release 5.1.22 (2026-03)
- **活跃度**: 10,604 commits、67 releases、2.2k forks · 健康活跃
- **覆盖**: 100+ AWS 服务（API 数量多，但**仿真深度普遍浅于 LocalStack**）
- **核心局限（这就是它末位的原因）**:
  - ❌ Lambda 默认是 stub，不真跑你的代码（要真跑得开 DockerLambda 开关，体验差）
  - ❌ 跨服务事件链路（S3→Lambda→DynamoDB）需要自己拼，不像 LocalStack 一站式
  - ❌ 无持久化（默认进程内存，重启全丢）
  - ❌ RDS/Cognito/MSK 等只有 API stub，无内嵌真实进程（LocalStack Pro 有）
  - ❌ 无 Resource Browser UI，调试只能看 `/moto-api/` JSON
  - ❌ 状态隔离弱：跨测试 reset 需要手工调 `/moto-api/reset`
- **唯一优势**: 启动快（~2s vs LocalStack ~20s）、轻量（~50MB vs ~500MB）、Apache 2.0 活跃维护
- **运行方式**:
  - 进程内 decorator（Python 测试场景）：`@mock_aws` / `@mock_s3` 等
  - **standalone HTTP server**（多语言通用、docker-compose 友好）：

```yaml
# docker-compose.dev.yml — moto 替代 LocalStack
services:
  moto:
    image: motoserver/moto:latest      # 也可用 ghcr.io/getmoto/motoserver:latest
    ports:
      - "127.0.0.1:5000:5000"
    environment:
      - MOTO_PORT=5000
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://localhost:5000/moto-api/"]
      interval: 5s
      timeout: 3s
      retries: 20
```

应用侧只需把 endpoint 从 `:4566` 改到 `:5000`：

```python
boto3.client("s3", endpoint_url="http://localhost:5000")
```

**Moto vs LocalStack Community 对比（仿真度视角）**：

| 维度 | LocalStack Community | Moto (server mode) |
|---|---|---|
| **仿真深度**（核心指标） | ✅ 高（接近真服务行为） | ⚠️ 中（API 形状对，深度浅） |
| Lambda 真实执行你的代码 | ✅（用 docker-in-docker 真跑） | ⚠️ 默认 stub，不真跑（开关开了也别扭） |
| 事件链路（S3→Lambda→DDB）| ✅ 一站式 | ⚠️ 需要自己拼 |
| 持久化 | ✅ PERSISTENCE=1 | ❌ 进程内存，重启丢 |
| Web UI 控制台 | ✅ Resource Browser | ⚠️ 仅 `/moto-api/` JSON |
| AWS 服务 API 覆盖 | ~39 个完整 | 100+ 但深度浅 |
| 启动速度 | ~20s | ~2s |
| 内存占用 | ~500MB | ~50MB |
| 维护状态 | ❌ 已归档 2026-03（pin 版本仍可用） | ✅ 活跃（2026-03 发版） |
| License | Apache 2.0（冻结） | Apache 2.0（活跃） |

**选型规则（fidelity-first）**：

- ✅ **优先 LocalStack Community**——只要你的依赖在 §2.1 那 39 个服务里，LocalStack 仿真度更高，归档但镜像仍可用、pin 版本足够稳
- ⚠️ **降级到 Moto** 仅当满足以下**任一**条件：
  1. CI 单元测试需要 < 5s 启动 × 多 worker 并行（LocalStack 太重）
  2. 依赖的服务 LocalStack Community 不支持，且团队没有 Pro license
  3. 测试场景只需要"API 形状对"（如 SDK 调用是否拼对参数），不关心服务行为
  4. LocalStack Community 的某个 operation 在你 pin 的版本里有 bug，Moto 修了
- ❌ **不要把 Moto 当默认**——它是 fallback，不是 primary

**一个务实的混合做法**：本地开发用 LocalStack Community（仿真度优先），CI 单元测试用 Moto（速度优先），集成/E2E 用真实 staging AWS（最真）。

#### B. **S3 替代品**（MinIO 也已归档，2026-04-25）

⚠️ **重要**：MinIO 这个老牌 S3 替代品也于 2026-04-25 归档，导向商业的 AIStor。OSS 世界的 S3 兼容生态正在重塑。当前可选：

| 方案 | License | 维护状态 | 备注 |
|---|---|---|---|
| **SeaweedFS** | Apache 2.0 | ✅ 活跃（4.22, 2026-04） | 当前最佳 OSS S3 兼容方案；单二进制启动；`weed server -s3` 一行起来 |
| **AIStor Free** | 商业 free 版 | ✅ MinIO 公司维护 | MinIO 的官方继任者；非纯开源 |
| **Garage** | AGPL-3.0 | ✅ 活跃（Deuxfleurs 维护） | 轻量、focus geo-distributed；功能比 MinIO 少 |
| **Moto S3**（in `motoserver/moto`）| Apache 2.0 | ✅ | 适合纯测试，不适合存大量数据 |
| **LocalStack Community S3**（pin 版本）| Apache 2.0 冻结 | ⚠️ 维护态 | 已有项目继续用没问题 |

推荐：
- **测试场景** → Moto S3（最快）
- **本地开发需要稳定 S3 服务**（含上传、签名 URL、生命周期等）→ SeaweedFS

```yaml
# docker-compose.dev.yml — SeaweedFS 替代 MinIO/LocalStack S3
services:
  s3:
    image: chrislusf/seaweedfs:latest
    command: server -s3 -dir=/data
    ports:
      - "127.0.0.1:8333:8333"          # S3 API
      - "127.0.0.1:9333:9333"          # Master / admin
    volumes:
      - seaweed-data:/data

volumes:
  seaweed-data:
```

```python
# 应用侧
import boto3
s3 = boto3.client(
    "s3",
    endpoint_url="http://localhost:8333",
    aws_access_key_id="any",
    aws_secret_access_key="any",
    region_name="us-east-1",
)
```

#### C. **其他单服务替代品**（已散见于 §4.1，重申一下）

| AWS 服务 | OSS 替代（与 LocalStack 无关，永远首选） |
|---|---|
| RDS | postgres / mysql 官方镜像 |
| ElastiCache | redis |
| DynamoDB | DynamoDB Local（`amazon/dynamodb-local`，AWS 官方提供） |
| MSK | confluentinc/cp-kafka |
| Managed Flink | flink:1.18 |
| Cognito | hydra / authentik / keycloak（OAuth2/OIDC） |
| SES | mailhog / mailpit（SMTP 收信） |
| SQS / SNS | RabbitMQ / NATS（如果可换协议）；否则 Moto |
| Step Functions | Temporal（更强的 workflow 引擎）；测试场景用 Moto |
| OpenSearch | opensearchproject/opensearch |
| S3 Vectors | pgvector / qdrant / weaviate / milvus |
| Bedrock | Ollama / vLLM / 本地模型 |

### 6.3 选型建议（fidelity-first 决策树）

> **核心原则：尽可能真实。** 从最真实的实现开始往下退，每退一步都要有明确理由。

```
仿真度梯度（从高到低）:
  ① 真实生产 AWS              （只在 staging/prod 用）
  ② 真实 staging AWS dev 账号 ─── 优先于任何 mock；预算/速率允许就用
  ③ 开源等价镜像（postgres/redis/kafka/...） ─── 数据面 = 生产，永远首选
  ④ LocalStack Pro            ─── 团队有 license 时，覆盖 RDS/Cognito 等
  ⑤ LocalStack Community      ─── AWS 控制面 + 事件链路，仿真度高于 Moto
  ⑥ Moto Server               ─── 末位备选，仅 CI 单元测试或 Pro 不可达时
  ⑦ 自写 stub                 ─── 万不得已

对每个 AWS 依赖按这个梯度选最高可达的那一档。
```

**反向问句（从默认 LocalStack 开始往下退）**：

1. 这服务能跑真 staging AWS 吗？预算和速率允许吗？→ **能就用真的**
2. 有成熟开源镜像吗？（postgres / redis / kafka / seaweedfs / pgvector）→ **有就用开源镜像，比 LocalStack 更真**
3. 团队有 LocalStack Pro license + 服务在 Base/Ultimate 列表？→ **用 Pro**
4. 服务在 Community 列表里？→ **用 LocalStack Community（pin 版本）**
5. 以上都不行 → 才考虑 **Moto**（且只在 CI 加速 / 形状对就够 的场景）

**绝不**直接跳到 Moto 当默认 — 它的 Lambda 不真跑、跨服务事件不顺、无持久化，仿真度全面低于 LocalStack。

### 6.4 dev-infra 的镜像可持续性策略

任何 OSS 关键依赖（LocalStack、MinIO、moto、SeaweedFS …）都要做以下"防归档"动作：

1. **Pin 镜像版本**：`image: motoserver/moto:5.1.22`（不要 `:latest`）
2. **同步到内部 Harbor**：`harbor.internal/library/moto:5.1.22`，避免上游下架
3. **记录归档日期**：在 docker-compose 注释里写明 `# upstream archived YYYY-MM-DD if applicable`
4. **半年复核一次**：把这些依赖列进 dev-infra 的复核 checklist，类似 SLA 半年体检
5. **关键服务做迁移演练**：`make dev-with-alt-stack` 可选 target，能切到备选方案验证

---

## 7. 引用与最后核对

**LocalStack 生态**
- LocalStack OSS 仓库（archived 2026-03-23）：https://github.com/localstack/localstack
- License & Plans：https://docs.localstack.cloud/aws/licensing/
- Coverage 索引：https://docs.localstack.cloud/references/coverage/
- Pricing：https://www.localstack.cloud/pricing
- Apache 2.0 License：归档仓库根目录 LICENSE.txt

**替代品**
- Moto（末位备选，仿真度低但启动快）：https://github.com/getmoto/moto · Docker 镜像 `motoserver/moto`
- Moto server mode 文档：https://docs.getmoto.org/en/latest/docs/server_mode.html
- SeaweedFS（S3 替代，MinIO 后继）：https://github.com/seaweedfs/seaweedfs
- MinIO 仓库（archived 2026-04-25 → AIStor）：https://github.com/minio/minio
- DynamoDB Local（AWS 官方）：`amazon/dynamodb-local`

**本文档最后核对日期**：2026-04-30
**复核触发条件**：
1. 当 dev-infra 项目实际接入新 AWS 服务时
2. 每半年定期检查归档仓库状态 + 官方 licensing 页变化
