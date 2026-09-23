# 参考项目（addx 真实生产实现）

> 由 [../SKILL.md](../SKILL.md) 路由进入。本文集中列出 addx 体系下两个 Flink 相关仓库的真实文件地图——所有链接已按实际默认分支构造（flink-jobs=`main`、flink-addx=`master`），可直接点开 GitLab。

## 仓库职责切分

| 仓库 | 默认分支 | 职责 |
|---|---|---|
| [`flink-jobs`](https://gitlab.addx.ai/DATA/flink-jobs) | `main` | 业务作业代码 + 单元/IT 测试 + region yaml + 单 fat-jar 多 entry class 构建 |
| [`flink-addx`](https://gitlab.addx.ai/DATA/flink-addx) | `master` | Session 集群部署 + vault/ExternalSecret 配置 + FlinkDeployment CRD + HistoryServer + Ingress |

⚠️ 两仓库默认分支不同（`main` vs `master`）——构造链接时别搞混。

## flink-jobs 文件地图（业务侧，branch=main）

### 文档与流程

| 文件 | 用途 |
|---|---|
| [README.md](https://gitlab.addx.ai/DATA/flink-jobs/-/blob/main/README.md) | 项目总入口：多 job 共一 jar、region 识别、Build、本地跑、部署、首次部署 checklist |
| [docs/superpowers/job-development-workflow.md](https://gitlab.addx.ai/DATA/flink-jobs/-/blob/main/docs/superpowers/job-development-workflow.md) | **项目级 SSOT 工作流**（与本 skill 互补、更细化）—— spec 模板、代码规约、commit 拆分、code review、部署完整流程 |
| [docs/superpowers/specs/](https://gitlab.addx.ai/DATA/flink-jobs/-/tree/main/docs/superpowers/specs) | 设计文档 + ADR（bird-detection 设计、unify-vs-split 决策、redis-sink ADR 等） |
| [docs/superpowers/plans/](https://gitlab.addx.ai/DATA/flink-jobs/-/tree/main/docs/superpowers/plans) | 多 commit 跨度的实施 plan |

### 共享基础设施（写新 job 直接复用，**不要重复造**）

| 文件 | 提供能力 |
|---|---|
| [src/main/java/com/addx/flink/common/ConfigLoader.java](https://gitlab.addx.ai/DATA/flink-jobs/-/blob/main/src/main/java/com/addx/flink/common/ConfigLoader.java) | region 识别（args → env → namespace → 默认 staging-us）+ 加载 `config-<region>.yaml` |
| [src/main/java/com/addx/flink/common/JobConfig.java](https://gitlab.addx.ai/DATA/flink-jobs/-/blob/main/src/main/java/com/addx/flink/common/JobConfig.java) | yaml schema POJO（kafka/redis/s3/jobs 分段） |
| [src/main/java/com/addx/flink/common/KafkaSources.java](https://gitlab.addx.ai/DATA/flink-jobs/-/blob/main/src/main/java/com/addx/flink/common/KafkaSources.java) | `stringSource(cfg, jobName, alias)` / `stringSink(cfg, jobName, alias)` 工厂 |
| [src/main/java/com/addx/flink/common/Checkpointing.java](https://gitlab.addx.ai/DATA/flink-jobs/-/blob/main/src/main/java/com/addx/flink/common/Checkpointing.java) | `enable(env, spec)`：从 spec.checkpoint.intervalMs 覆盖集群默认 |
| [src/main/java/com/addx/flink/common/Parallelism.java](https://gitlab.addx.ai/DATA/flink-jobs/-/blob/main/src/main/java/com/addx/flink/common/Parallelism.java) | `apply(env, spec)`：从 spec.parallelism 应用（覆盖 Web UI 填写） |
| [src/main/java/com/addx/flink/common/RedisClients.java](https://gitlab.addx.ai/DATA/flink-jobs/-/blob/main/src/main/java/com/addx/flink/common/RedisClients.java) | Lettuce 客户端工厂（按 RedisEndpoint 起 RedisClient） |
| [src/main/java/com/addx/flink/common/RedisAsyncSinkBase.java](https://gitlab.addx.ai/DATA/flink-jobs/-/blob/main/src/main/java/com/addx/flink/common/RedisAsyncSinkBase.java) | `RichAsyncFunction` 基类：open/close 生命周期 + 客户端按序释放 |
| [src/main/java/com/addx/flink/common/EnvResolver.java](https://gitlab.addx.ai/DATA/flink-jobs/-/blob/main/src/main/java/com/addx/flink/common/EnvResolver.java) | `passwordEnv: KAFKA_PASSWORD` 类的 env 解析（缺失 fail-fast） |
| [src/main/java/com/addx/flink/common/EnvPlaceholders.java](https://gitlab.addx.ai/DATA/flink-jobs/-/blob/main/src/main/java/com/addx/flink/common/EnvPlaceholders.java) | yaml 原文 `${VAR:-default}` 占位符展开 |

### 业务作业（照抄模板）

| 模板 | 何时抄 | 文件 |
|---|---|---|
| `BirdFirstVisitJob` | 单 key 一次性事件 + Kafka 主流 + Redis 旁路 HASH sink | [main 类](https://gitlab.addx.ai/DATA/flink-jobs/-/blob/main/src/main/java/com/addx/flink/jobs/bird/firstvisit/BirdFirstVisitJob.java) · [Detector](https://gitlab.addx.ai/DATA/flink-jobs/-/blob/main/src/main/java/com/addx/flink/jobs/bird/firstvisit/BirdFirstVisitDetector.java) · [Sink](https://gitlab.addx.ai/DATA/flink-jobs/-/blob/main/src/main/java/com/addx/flink/jobs/bird/firstvisit/RedisFirstVisitSink.java) · [包目录](https://gitlab.addx.ai/DATA/flink-jobs/-/tree/main/src/main/java/com/addx/flink/jobs/bird/firstvisit) |
| `BirdNewSpeciesJob` | 集合去重 + 计数 + 多输出分流 + Redis 旁路 | [main 类](https://gitlab.addx.ai/DATA/flink-jobs/-/blob/main/src/main/java/com/addx/flink/jobs/bird/newspecies/BirdNewSpeciesJob.java) · [Detector](https://gitlab.addx.ai/DATA/flink-jobs/-/blob/main/src/main/java/com/addx/flink/jobs/bird/newspecies/BirdNewSpeciesDetector.java) · [包目录](https://gitlab.addx.ai/DATA/flink-jobs/-/tree/main/src/main/java/com/addx/flink/jobs/bird/newspecies) |

### Region 配置（参考写新 region yaml）

| 文件 | 状态 |
|---|---|
| [src/main/resources/config-staging-us.yaml](https://gitlab.addx.ai/DATA/flink-jobs/-/blob/main/src/main/resources/config-staging-us.yaml) | ✅ 真实运行中，参考首选 |
| [src/main/resources/config-prod-us.yaml](https://gitlab.addx.ai/DATA/flink-jobs/-/blob/main/src/main/resources/config-prod-us.yaml) | ✅ vault 结构齐 |
| [src/main/resources/config-prod-eu.yaml](https://gitlab.addx.ai/DATA/flink-jobs/-/blob/main/src/main/resources/config-prod-eu.yaml) | ✅ vault 结构齐 |
| [src/main/resources/config-prod-cn.yaml](https://gitlab.addx.ai/DATA/flink-jobs/-/blob/main/src/main/resources/config-prod-cn.yaml) | 🚧 skeleton 模板 |

### 测试样板（写新 job 测试照抄）

| 文件 | 覆盖什么 |
|---|---|
| [src/test/java/com/addx/flink/jobs/bird/firstvisit/BirdFirstVisitDetectorTest.java](https://gitlab.addx.ai/DATA/flink-jobs/-/blob/main/src/test/java/com/addx/flink/jobs/bird/firstvisit/BirdFirstVisitDetectorTest.java) | L2 `KeyedOneInputStreamOperatorTestHarness` + 跨 key 独立 + restore |
| [src/test/java/com/addx/flink/jobs/bird/firstvisit/RedisFirstVisitSinkIT.java](https://gitlab.addx.ai/DATA/flink-jobs/-/blob/main/src/test/java/com/addx/flink/jobs/bird/firstvisit/RedisFirstVisitSinkIT.java) | L3 jedis-mock + Lettuce 异步 sink 端到端 |
| [src/test/java/com/addx/flink/jobs/bird/firstvisit/BirdFirstVisitJobIT.java](https://gitlab.addx.ai/DATA/flink-jobs/-/blob/main/src/test/java/com/addx/flink/jobs/bird/firstvisit/BirdFirstVisitJobIT.java) | L3 spring-kafka-test EmbeddedKafkaBroker + MiniCluster 全链路 |
| [src/test/java/com/addx/flink/common/](https://gitlab.addx.ai/DATA/flink-jobs/-/tree/main/src/test/java/com/addx/flink/common) | 基础设施类的单测（ConfigLoader / EnvResolver / Checkpointing 等） |

### 构建与提交

| 文件 | 用途 |
|---|---|
| [pom.xml](https://gitlab.addx.ai/DATA/flink-jobs/-/blob/main/pom.xml) | Flink 2.2 + Java 17 + Kafka connector 4.0 + Lettuce + jedis-mock + spring-kafka-test + shade + jacoco 版本对齐范例 |
| [.gitlab-ci.yml](https://gitlab.addx.ai/DATA/flink-jobs/-/blob/main/.gitlab-ci.yml) | MR-only 触发：build → verify → sonar；maven local repo / sonar 缓存策略 |
| [scripts/run-local.sh](https://gitlab.addx.ai/DATA/flink-jobs/-/blob/main/scripts/run-local.sh) | 本地按短名跑某个 job：`./scripts/run-local.sh BirdFirstVisitJob`（需 VPN + KAFKA_PASSWORD） |
| [.vscode/launch.json](https://gitlab.addx.ai/DATA/flink-jobs/-/blob/main/.vscode/launch.json) | VSCode Run/Debug 配置（密码从未提交的 .env.local 读） |

## flink-addx 文件地图（集群侧，branch=master）

### 集群定义（base，所有 region 共享）

| 文件 | 用途 |
|---|---|
| [k8s/flink/base/flinkdeployment.yaml](https://gitlab.addx.ai/DATA/flink-addx/-/blob/master/k8s/flink/base/flinkdeployment.yaml) | **Session 集群核心**——`FlinkDeployment` CRD、RocksDB / checkpoint / HA / restart-strategy 全套 `flinkConfiguration`、JM/TM 资源、Pod 反亲和 |
| [k8s/flink/base/kustomization.yaml](https://gitlab.addx.ai/DATA/flink-addx/-/blob/master/k8s/flink/base/kustomization.yaml) | base 资源清单（含 SA、HA Role/RoleBinding、FlinkDeployment、HistoryServer 套件、2 个 Ingress） |
| [k8s/flink/base/serviceaccount.yaml](https://gitlab.addx.ai/DATA/flink-addx/-/blob/master/k8s/flink/base/serviceaccount.yaml) · [role-flink-ha.yaml](https://gitlab.addx.ai/DATA/flink-addx/-/blob/master/k8s/flink/base/role-flink-ha.yaml) · [rolebinding-flink-ha.yaml](https://gitlab.addx.ai/DATA/flink-addx/-/blob/master/k8s/flink/base/rolebinding-flink-ha.yaml) | K8s HA 所需的 ServiceAccount + RBAC |
| [k8s/flink/base/deployment-historyserver.yaml](https://gitlab.addx.ai/DATA/flink-addx/-/blob/master/k8s/flink/base/deployment-historyserver.yaml) · [service-historyserver.yaml](https://gitlab.addx.ai/DATA/flink-addx/-/blob/master/k8s/flink/base/service-historyserver.yaml) · [configmap-historyserver.yaml](https://gitlab.addx.ai/DATA/flink-addx/-/blob/master/k8s/flink/base/configmap-historyserver.yaml) | HistoryServer：cancel/failed job 元数据归档查询 |
| [k8s/flink/base/ingress-office.yaml](https://gitlab.addx.ai/DATA/flink-addx/-/blob/master/k8s/flink/base/ingress-office.yaml) · [ingress-history.yaml](https://gitlab.addx.ai/DATA/flink-addx/-/blob/master/k8s/flink/base/ingress-history.yaml) | 办公网 Ingress（Web UI + HistoryServer）；overlay 改 hostname |

### Region overlay

| Region | overlay 目录 |
|---|---|
| `staging-us` | [k8s/flink/overlays/staging-us/](https://gitlab.addx.ai/DATA/flink-addx/-/tree/master/k8s/flink/overlays/staging-us) |
| `prod-us` | [k8s/flink/overlays/prod-us/](https://gitlab.addx.ai/DATA/flink-addx/-/tree/master/k8s/flink/overlays/prod-us) |
| `prod-eu` | [k8s/flink/overlays/prod-eu/](https://gitlab.addx.ai/DATA/flink-addx/-/tree/master/k8s/flink/overlays/prod-eu) |
| `prod-cn` | [k8s/flink/overlays/prod-cn/](https://gitlab.addx.ai/DATA/flink-addx/-/tree/master/k8s/flink/overlays/prod-cn) |

每个 overlay 含：

| 文件 | 用途 |
|---|---|
| `kustomization.yaml` | 拼接 base + region 资源、image transformer、Ingress hostname patch |
| `external-secret.yaml` | vault → K8s Secret `flink-s3-credentials` 同步规则（AWS + Kafka SCRAM + Redis 全合一） |
| `flinkdeployment-patch.yaml` | JM/TM podTemplate env 注入（KAFKA_*、REDIS_*、AWS_*）+ JM replicas=1 + Pod labels (`flink_env`/`flink_cluster`) |
| `entitlement-cdc-job.yaml` / `entitlement-cdc-resources.yaml`（staging-us）| **K8s Job deployer 范本**：用 deployer 镜像通过 REST API 自动提交 Flink job（参考 [deployment.md](deployment.md) §方式 B） |
| `historyserver-config-patch.yaml`（prod-*）| HistoryServer 配置 patch |

### 直接看 staging-us 真实文件

| 文件 | URL |
|---|---|
| External Secret | [overlays/staging-us/external-secret.yaml](https://gitlab.addx.ai/DATA/flink-addx/-/blob/master/k8s/flink/overlays/staging-us/external-secret.yaml) |
| FlinkDeployment patch | [overlays/staging-us/flinkdeployment-patch.yaml](https://gitlab.addx.ai/DATA/flink-addx/-/blob/master/k8s/flink/overlays/staging-us/flinkdeployment-patch.yaml) |
| Kustomization | [overlays/staging-us/kustomization.yaml](https://gitlab.addx.ai/DATA/flink-addx/-/blob/master/k8s/flink/overlays/staging-us/kustomization.yaml) |
| Entitlement CDC Job（K8s Job deployer 范本）| [overlays/staging-us/entitlement-cdc-job.yaml](https://gitlab.addx.ai/DATA/flink-addx/-/blob/master/k8s/flink/overlays/staging-us/entitlement-cdc-job.yaml) |

## 常用 Web UI / 入口地址（已对账真实 overlay ingress patch）

> **prod hostname 无 `prod-` 前缀**——别按 staging-us 的 `flink-staging-us.addx.live` 模式机械外推。

| Region | Flink Web UI | HistoryServer |
|---|---|---|
| staging-us | https://flink-staging-us.addx.live | https://flink-history-staging-us.addx.live |
| prod-us | https://flink-us.addx.live | https://flink-history-us.addx.live |
| prod-eu | https://flink-eu.addx.live | https://flink-history-eu.addx.live |
| prod-cn | https://flink-cn.addx.live | https://flink-history-cn.addx.live |

**集群内 REST**（在 K8s pod 内访问 JM REST，如 deployer Job）：`http://flink-session-rest:8081`

## 链接维护说明

- 链接默认指向 **默认分支 HEAD**（flink-jobs=main、flink-addx=master）；指向稳定版本时改为 `<branch>`/`<tag>`/`<commit>`
- 仓库代码会演化——本 skill 与仓库现状冲突时**以仓库代码为准**（它跟生产一起跑、有 CI 验证）
- **不要引用本机路径**——本 skill 在不同机器、不同用户环境下被加载，本地路径无效
- 发现链接 404：先确认默认分支没改名；再用 GitLab Web 搜索文件实际位置
