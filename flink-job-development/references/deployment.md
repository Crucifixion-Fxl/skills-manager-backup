# 部署 / 集群 / Vault / 可观测性

> 由 [../SKILL.md](../SKILL.md) 路由进入。本文以 **addx 生产体系**为依据描述真实部署形态（集群仓库 [`flink-addx`](https://gitlab.addx.ai/DATA/flink-addx) + 业务仓库 [`flink-jobs`](https://gitlab.addx.ai/DATA/flink-jobs)）。其他体系按各自约定调整。升级 / savepoint 详见 [state-time-checkpoint.md](state-time-checkpoint.md)。

## 一图看清两仓库职责

```text
flink-addx (branch=master)               flink-jobs (branch=main)
集群仓库（基础设施 + vault）              业务仓库（job 代码 + region yaml）
─────────────────────────                ─────────────────────────
k8s/flink/base/                          src/main/java/com/addx/flink/
  ├─ flinkdeployment.yaml                  ├─ common/   (ConfigLoader 等)
  │   └─ FlinkDeployment "flink-session"   └─ jobs/<pkg>/<Job>.java (main)
  ├─ HistoryServer + Ingress 套件         src/main/resources/
  └─ ServiceAccount + HA Role/RoleBinding   └─ config-<region>.yaml
k8s/flink/overlays/<region>/             pom.xml          (单 fat-jar)
  ├─ external-secret.yaml ← vault         scripts/run-local.sh
  ├─ flinkdeployment-patch.yaml ← env     .gitlab-ci.yml  (MR-only build/test/sonar)
  └─ kustomization.yaml
```

**核心约定**：
- 集群是 **Session Cluster**（`metadata.name: flink-session`），多 job 共享 JM/TM
- 每个 region 一套 overlay（staging-us / prod-us / prod-eu / prod-cn）
- vault 凭据通过 ExternalSecret 同步成同名 K8s Secret，**集中在一个 Secret `flink-s3-credentials` 里**（含 AWS + Kafka SCRAM + Redis）
- 业务作业以 fat-jar 形式提交，多种提交方式（见下文 §作业提交方式）

## Session Cluster 实际配置（来自 flink-addx base + overlay）

### 集群定义 [`k8s/flink/base/flinkdeployment.yaml`](https://gitlab.addx.ai/DATA/flink-addx/-/blob/master/k8s/flink/base/flinkdeployment.yaml)

关键 `flinkConfiguration`（已验证、勿凭印象改）：

```yaml
flinkConfiguration:
  taskmanager.numberOfTaskSlots: "2"

  # StateBackend：RocksDB（生产唯一选择）
  state.backend.type: rocksdb
  state.backend.incremental: "true"
  state.backend.rocksdb.localdir: /opt/flink/rocksdb-state
  state.backend.rocksdb.memory.managed: "true"

  # Checkpoint：5 分钟 interval，保留 3 个，超时 10 分钟，cancel 不删
  execution.checkpointing.interval: "300000"
  execution.checkpointing.min-pause: "30000"
  execution.checkpointing.mode: EXACTLY_ONCE
  execution.checkpointing.externalized-checkpoint-retention: RETAIN_ON_CANCELLATION
  execution.checkpointing.num-retained: "3"
  execution.checkpointing.timeout: "600000"
  execution.checkpointing.max-concurrent-checkpoints: "1"

  # State 存储（每 region 不同 bucket，下表见 overlay）
  state.checkpoints.dir: s3://a4x-us-datalake-staging/flink/checkpoint/
  state.savepoints.dir: s3://a4x-us-datalake-staging/flink/savepoints/

  # HA：基于 K8s，元数据落 S3
  high-availability.type: kubernetes
  high-availability.storageDir: s3://a4x-us-datalake-staging/flink/ha/

  # 重启：指数退避，避免 source/sink 永久 fail 时狂重启
  restart-strategy.type: exponential-delay
  restart-strategy.exponential-delay.initial-backoff: 30 s
  restart-strategy.exponential-delay.max-backoff: 10 min
  restart-strategy.exponential-delay.backoff-multiplier: "2.0"

  # HistoryServer 归档（cancel/失败的 job 历史可在 flink-history-<region>.addx.live 查）
  jobmanager.archive.fs.dir: s3://a4x-us-datalake-staging/flink/history/

  parallelism.default: "1"   # 业务作业必须在 yaml 或代码里显式设
  resourcemanager.taskmanager-timeout: "600000"
```

JM/TM 资源（来自 base）：

| 角色 | replicas | CPU | Memory | 备注 |
|---|---|---|---|---|
| JobManager | 1 | 1 | 2048m | **故意单实例**——Web UI / `flink run` 上传的 jar 在单 JM 的 /tmp 里，多 JM 时 ALB 可能切到另一台拿不到 jar。HA 由 `high-availability.type=kubernetes` 提供（JM Pod 挂后自动重建+从 checkpoint 恢复） |
| TaskManager | 自动 | 2 | 2048m | 2 slots/TM；挂 `rocksdb-state` emptyDir 到 `/opt/flink/rocksdb-state`（容量见下） |

### Overlay 各 region 差异（已对账 [`flink-addx/k8s/flink/overlays/`](https://gitlab.addx.ai/DATA/flink-addx/-/tree/master/k8s/flink/overlays) 真实值）

每 region 通过 `overlays/<region>/flinkdeployment-patch.yaml` JSON-merge 覆盖 base：

| Region | S3 bucket | namespace | Web UI hostname | HistoryServer | Pod labels (`flink_env`/`flink_cluster`) |
|---|---|---|---|---|---|
| `staging-us` | `a4x-us-datalake-staging` | `staging-us` | `flink-staging-us.addx.live` | `flink-history-staging-us.addx.live` | ✅ 已配 |
| `prod-us` | `a4x-us-datalake`（**无 `-prod` 后缀**） | `prod-us` | `flink-us.addx.live` | `flink-history-us.addx.live` | ❌ 未配 |
| `prod-eu` | `a4x-eu-datalake-prod` | `prod-eu` | `flink-eu.addx.live` | `flink-history-eu.addx.live` | ❌ 未配 |
| `prod-cn` | `a4x-cn-datalake-prod` | `prod-cn` | `flink-cn.addx.live` | `flink-history-cn.addx.live` | ❌ 未配 |

> - **prod hostname 无 `prod-` 前缀**——`flink-us.addx.live` 不是 `flink-prod-us.addx.live`。
> - **Pod labels `flink_env` / `flink_cluster` 目前只在 staging-us overlay 配**；prod 三 region 都未设——用 `flink_env` 区分 metric 维度时**当前在 prod 不可用**，新增 alert/dashboard 前需先补 prod overlay 的 labels。
> - **`web.cancel.enable: "true"` 在 4 个 overlay 都配了**，但**不在 base**——base 默认 false（Flink 1.11+），overlay 显式开启 Cancel 按钮。
> - **Prometheus reporter（`metrics.reporter.prom.*`）目前只在 staging-us overlay 配**——prod 未启用；启用前要先把 plugin jar bake 进 `harbor-00249-us-tech.addx.live/cicd/<region>/flink` 镜像。

⚠️ **Kustomize 用 JSON merge patch（不是 strategic merge）**——`containers[]` 在 patch 里必须完全重列 base 的 env，否则 base 的 AWS_* env 会被替换丢失。改 base 的 container 字段时**记得同步改 4 个 overlay 的 patch**。

## Vault 配置归属（业务仓库 vs 集群仓库）

**职责切分明确**：

| 仓库 | 写什么 | 改什么时改它 |
|---|---|---|
| **业务仓库** [`flink-jobs`](https://gitlab.addx.ai/DATA/flink-jobs) | 只声明 env 变量名引用（`passwordEnv: KAFKA_PASSWORD`）+ `${VAR:-default}` 占位符 | 新增 env 引用；改业务逻辑 |
| **集群仓库** [`flink-addx`](https://gitlab.addx.ai/DATA/flink-addx) | vault path → K8s Secret 同步 + Pod env 注入 | vault 路径变更；新增 vault key；新 region |

**契约**：业务 yaml 里 `passwordEnv: <ENV_NAME>` 的 env 名 **必须** 与集群侧 `secretKeyRef` 注入的 env 名一致。

### 集群侧：ExternalSecret 同步 vault [`overlays/staging-us/external-secret.yaml`](https://gitlab.addx.ai/DATA/flink-addx/-/blob/master/k8s/flink/overlays/staging-us/external-secret.yaml)

addx 当前把所有 Flink job 用到的凭据（AWS、Kafka、Redis）合并到**一个 Secret `flink-s3-credentials`**：

```yaml
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: flink-s3-credentials
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: vault-backend
    kind: ClusterSecretStore
  target:
    name: flink-s3-credentials       # 同步成 K8s Secret，供 flinkdeployment-patch 引用
    creationPolicy: Owner
  data:
    # AWS 凭据 —— vault path: flink/staging-us
    - { secretKey: aws-access-key-id,     remoteRef: { key: flink/staging-us, property: aws.access-key-id } }
    - { secretKey: aws-secret-access-key, remoteRef: { key: flink/staging-us, property: aws.secret-access-key } }
    # Kafka SCRAM —— vault path: staging/kafka/application/flink/scram
    - { secretKey: kafka-bootstrap-servers, remoteRef: { key: staging/kafka/application/flink/scram, property: bootstrap-servers } }
    - { secretKey: kafka-username,          remoteRef: { key: staging/kafka/application/flink/scram, property: username } }
    - { secretKey: kafka-password,          remoteRef: { key: staging/kafka/application/flink/scram, property: password } }
    - { secretKey: kafka-sasl-mechanism,    remoteRef: { key: staging/kafka/application/flink/scram, property: sasl-mechanism } }
    - { secretKey: kafka-security-protocol, remoteRef: { key: staging/kafka/application/flink/scram, property: security-protocol } }
    # Redis —— vault path: staging/redis/application/flink/cache
    - { secretKey: redis-host, remoteRef: { key: staging/redis/application/flink/cache, property: REDIS_HOST } }
    - { secretKey: redis-port, remoteRef: { key: staging/redis/application/flink/cache, property: REDIS_PORT } }
```

> staging-us Redis 当前**未开 ACL**，故 vault 中无 user/password；启用 ACL 后补 `redis-username` / `redis-password` 两项 + 同步改 `flinkdeployment-patch.yaml` env 注入。

### 集群侧：Pod env 注入 [`overlays/staging-us/flinkdeployment-patch.yaml`](https://gitlab.addx.ai/DATA/flink-addx/-/blob/master/k8s/flink/overlays/staging-us/flinkdeployment-patch.yaml)

`spec.jobManager.podTemplate.spec.containers[0].env` 和 `spec.taskManager.podTemplate.spec.containers[0].env` **都要列全**：

```yaml
env:
  # AWS（base 已含，patch 必须重列因 JSON merge 替换整 list）
  - { name: AWS_ACCESS_KEY_ID,     valueFrom: { secretKeyRef: { name: flink-s3-credentials, key: aws-access-key-id } } }
  - { name: AWS_SECRET_ACCESS_KEY, valueFrom: { secretKeyRef: { name: flink-s3-credentials, key: aws-secret-access-key } } }
  - { name: AWS_REGION,         value: us-east-1 }
  - { name: AWS_DEFAULT_REGION, value: us-east-1 }
  # Kafka SCRAM
  - { name: KAFKA_BOOTSTRAP_SERVERS, valueFrom: { secretKeyRef: { name: flink-s3-credentials, key: kafka-bootstrap-servers } } }
  - { name: KAFKA_USERNAME,          valueFrom: { secretKeyRef: { name: flink-s3-credentials, key: kafka-username } } }
  - { name: KAFKA_PASSWORD,          valueFrom: { secretKeyRef: { name: flink-s3-credentials, key: kafka-password } } }
  - { name: KAFKA_SASL_MECHANISM,    valueFrom: { secretKeyRef: { name: flink-s3-credentials, key: kafka-sasl-mechanism } } }
  - { name: KAFKA_SECURITY_PROTOCOL, valueFrom: { secretKeyRef: { name: flink-s3-credentials, key: kafka-security-protocol } } }
  # Redis（仅 host/port，未开 ACL）
  - { name: REDIS_HOST, valueFrom: { secretKeyRef: { name: flink-s3-credentials, key: redis-host } } }
  - { name: REDIS_PORT, valueFrom: { secretKeyRef: { name: flink-s3-credentials, key: redis-port } } }
```

JM 上跑 job main()，缺任何 env 会在 submit 时 fail-fast。TM 端理论上不需要 KAFKA_*（JobGraph 序列化后已带 `sasl.jaas.config`），但仍 mirror 一份保 JM/TM 对称 + 未来 job 在 TM 读 env 不炸。

### 改密码 / 加 vault 路径 / 加 env / 加 region 走哪里

| 改 | 操作 |
|---|---|
| Vault 现有密码轮换 | 仅改 vault；K8s ExternalSecret 1h 内自动 refresh，**两个仓库都不动** |
| 立即生效轮换 | `kubectl annotate externalsecret flink-s3-credentials force-sync=$(date +%s) --overwrite -n <region>` |
| 加 vault path（如新加 MongoDB 密码） | 1. 改 flink-addx `external-secret.yaml` 加 data 条目；2. 改 `flinkdeployment-patch.yaml` 加 env 注入；3. 改 flink-jobs `config-<region>.yaml` 加 `passwordEnv: <NEW_ENV>` |
| 加新 region | 1. flink-addx 新建 `overlays/<region>/` 全套；2. flink-jobs 新建 `config-<region>.yaml` |
| 改业务逻辑 | 仅 flink-jobs |

## 作业提交方式（两种实际在用的）

### 方式 A：Web UI Submit New Job（**flink-jobs 当前 bird job 用这个**）

适合：日常迭代、单 fat-jar 多 entry class 灵活提交。

1. 本地 `mvn clean package` 产 `target/flink-jobs-0.1.0.jar`
2. 打开 region 对应 Web UI（详见 [reference-projects.md §Web UI / 入口地址](reference-projects.md)；prod 无 `prod-` 前缀）
3. **Submit New Job → Add New** → 选本地 jar 上传
4. 在 jar 行点开 → 填表：
   - **Entry Class**：`com.addx.flink.jobs.<pkg>.<JobName>Job`（pom 默认 main 留空也行；非默认必须显式填）
   - **Parallelism**：**留空**（代码里 `Parallelism.apply(env, spec)` 接管 yaml 的 `jobs.<job>.parallelism`，Web UI 填了会被覆盖）
   - **Program Arguments**：留空默认 `staging-us`；或 `--region <name>`（接受 `staging-us` / `prod-us` / `us` / `prod-eu` / `eu` / `prod-cn` / `cn`）
   - **Savepoint Path**：升级时填上次 savepoint 路径（首次提交留空）
5. **Submit**
6. 验证：**Running Jobs** 出现 → JM 日志第一行 INFO `region=... job=... ...` → 5 分钟内 Checkpoints tab 出首个 completed checkpoint

### 方式 B：K8s Job deployer（**flink-addx 的 entitlement-cdc 用这个**）

适合：GitOps 化管理、jar 版本跟 Helm/Kustomize 一起灰度。

参考 [`k8s/flink/overlays/staging-us/entitlement-cdc-job.yaml`](https://gitlab.addx.ai/DATA/flink-addx/-/blob/master/k8s/flink/overlays/staging-us/entitlement-cdc-job.yaml)：一次性 K8s Job 跑 deployer 镜像（jar 已 bake 进镜像），entrypoint 通过 Flink REST API 自动 cancel 旧 job + upload + run。

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: my-job-deployer
  annotations: { argocd.argoproj.io/sync-options: Replace=true,Force=true }
spec:
  backoffLimit: 3
  ttlSecondsAfterFinished: 86400
  template:
    spec:
      restartPolicy: Never
      nodeSelector: { kubernetes.io/arch: amd64 }
      containers:
        - name: deployer
          image: harbor-00249-us-tech.addx.live/cicd/staging-us/my-job-deployer:staging
          envFrom:
            - configMapRef: { name: my-job-config }
            - secretRef:    { name: my-job-secrets }
          env:
            # Operator 自动建的 Session 集群 REST Service
            - { name: FLINK_REST,  value: "http://flink-session-rest:8081" }
            - { name: ENTRY_CLASS, value: "com.addx.MyJob" }
            - { name: JOB_NAME,    value: "my-job" }
```

deployer 脚本逻辑（参考 entitlement-cdc）：
1. `GET /jobs/overview` 列 RUNNING 同名 job → `PATCH /jobs/<id>?mode=cancel` 幂等取消
2. `POST /jars/upload` 上传镜像内 jar
3. `POST /jars/{id}/run` 用 `entryClass` + `programArgsList=[...]` 启动

> 用 `Replace=true,Force=true` 因为 K8s Job spec 不可变；改 jar 时 ArgoCD 必须 delete + create。

## 首次部署 checklist

**集群侧（Session 集群，一次性确认）**：
- [ ] [`flink-addx`](https://gitlab.addx.ai/DATA/flink-addx) overlay 已 ArgoCD sync 进 K8s
- [ ] `FlinkDeployment flink-session` 状态 `STABLE`，JM Pod ready 1/1，TM Pod 至少 1 个 Running
- [ ] `ExternalSecret flink-s3-credentials` 状态 `SecretSynced`，K8s Secret 真有内容（`kubectl get secret flink-s3-credentials -o yaml`）
- [ ] Web UI `https://flink-<region>.addx.live` 能打开（验 Ingress + DNS）

**业务作业（每个新 job）**：
- [ ] 输入 topic 已建 + partition 数 ≥ 该 job parallelism（与 Kafka ops 确认）
- [ ] 输出 topic 已建（如 Kafka 集群未开 auto.create）
- [ ] [`flink-jobs`](https://gitlab.addx.ai/DATA/flink-jobs) `src/main/resources/config-<region>.yaml` 已加 `jobs.<job>` 块（含 `consumerGroup` / `parallelism` / `topics` / `redis`）
- [ ] 业务 yaml 引用的 env 名（如 `passwordEnv: REDIS_PASSWORD`）在集群侧 patch 已注入
- [ ] 本地 `export KAFKA_PASSWORD=$(vault kv get -field=password secret/staging/kafka/application/flink/scram)` 后 `./scripts/run-local.sh <ShortJobName>` 能连上 broker（VPN required）
- [ ] `mvn -B verify -Dshade.skip=true` 全绿

**提交后验证**：
- [ ] **Running Jobs** 列表出现，状态 RUNNING
- [ ] JM 日志一行 INFO 打全 `region=... job=... group=... checkpointMs=... parallelism=... input=... output=...`
- [ ] **5 分钟内** Checkpoints tab 出首个 completed checkpoint（cluster 默认 interval=300000，业务可在 yaml `jobs.<job>.checkpoint.intervalMs` 覆盖）
- [ ] 业务侧 smoke：发一条测试事件 → 看 output topic / Redis HASH 符合预期
- [ ] 故障恢复：`kubectl delete pod -l component=taskmanager -n <region>`，作业自动从最近 checkpoint 恢复（Checkpoints tab 的 "Restored Checkpoint" 非空）

## 升级（替换运行中的作业）

详见 [state-time-checkpoint.md](state-time-checkpoint.md) §升级流程。要点：

### 方式 A 提交的作业（Web UI 上传）

1. **触发 savepoint**：
   - CLI（推荐，CI/CD 友好）：
     ```bash
     # JM REST 经 Ingress（office 网络）
     curl -X POST https://flink-staging-us.addx.live/jobs/<job-id>/savepoints \
          -H "Content-Type: application/json" \
          -d '{"target-directory": "s3://a4x-us-datalake-staging/flink/savepoints/", "cancel-job": true}'
     # 返回 request-id；轮询 GET .../savepoints/<request-id> 拿 location
     ```
   - Web UI：Job Details 页 **Stop** 按钮 → 弹框填 Savepoint Path
   - **`Cancel` 按钮 ≠ savepoint**——base 集群配 `web.cancel.enable: true` 暴露了 Cancel，但 Cancel **只取消不 savepoint**，升级**不要点 Cancel**
2. **新 jar 提交**：Web UI Submit → Add New → 选新 jar → Entry Class + **Savepoint Path 填上一步路径** → Submit

### 方式 B 提交的作业（K8s Job deployer）

改 deployer 镜像 tag 或 manifest → ArgoCD sync 触发 Job 重跑：
- Job entrypoint 内置 cancel + upload + run 逻辑
- ⚠️ 当前 entitlement-cdc deployer 是 `cancel` 而非 `stop-with-savepoint`——大状态 job 用此模式前需扩展 deployer 脚本支持 savepoint

## 可观测性

### 已配但当前 disabled：Prometheus metrics（仅 staging-us overlay）

[`overlays/staging-us/flinkdeployment-patch.yaml`](https://gitlab.addx.ai/DATA/flink-addx/-/blob/master/k8s/flink/overlays/staging-us/flinkdeployment-patch.yaml) 保留了 reporter 配置：

```yaml
metrics.reporter.prom.factory.class: org.apache.flink.metrics.prometheus.PrometheusReporterFactory
metrics.reporter.prom.port: 9249-9260
metrics.reporter.prom.filter.includes: "*:*:*"
```

但当前**不工作**——base 镜像 flink:2.2.0 不包 `flink-metrics-prometheus-2.2.0.jar`，启用 plugin 时 docker-entrypoint.sh 报错 + JM/TM crash。**prod 三 region overlay 没配 reporter**。

启用前的 prereq：先把 plugin jar bake 进自定义镜像（`harbor-00249-us-tech.addx.live/cicd/<region>/flink`）+ 修 docker-entrypoint.sh 的 `ENABLE_BUILT_IN_PLUGINS` env；启用后还要在 4 个 overlay 都加 reporter 配置 + 在 prod overlay 补 `flink_env` / `flink_cluster` pod labels（否则 metric 抓不到环境维度）。

### HistoryServer

cancel / failed 的 job 元数据归档到 `s3://.../flink/history/`，可在 `https://flink-history-<region>.addx.live` 查询历史 job + checkpoint。

### 业务侧应做的

- 启动日志一行 INFO 打全 `region/job/group/checkpointMs/parallelism/input/output`——线上排查从这里开始
- 算子 `open()` 注册 `Counter` / `Histogram`，关键判定点 `+1`（即使 Prometheus 暂未启用，Web UI 也能查 metric tab）
- 大状态 job 用 `RichFunction.open()` 把 region/job 灌进 MDC，所有 log 自带上下文

## 部署特有常见坑

| 坑 | 现象 | 解决 |
|---|---|---|
| Overlay patch 改了 env 但启动后 base AWS_* 丢 | JM 报 S3 AccessDenied | JSON merge patch 替换整 `containers[0].env` list，patch 必须重列 base 的所有 env |
| Overlay 漏改 TM `volumeMounts` | RocksDB 写到 Pod overlayfs，超 ephemeral-storage 被 evict | TM patch 必须重列 `volumeMounts: [{name: rocksdb-state, mountPath: /opt/flink/rocksdb-state}]` |
| `secretKeyRef.key` 名拼写错 | Pod 启动卡 ContainerCreating | `kubectl describe pod` 看 events；名要严格对齐 ExternalSecret 的 `secretKey` |
| ExternalSecret 状态 `SecretSyncedError` | vault path 不对 / vault-backend ClusterSecretStore 没权限 | `kubectl describe externalsecret`；检查 vault policy + ClusterSecretStore 配置 |
| 改 vault 后 Pod env 没更新 | ExternalSecret 1h refresh 周期 | `kubectl annotate externalsecret flink-s3-credentials force-sync=$(date +%s) --overwrite` |
| JM Pod replicas > 1 后 Web UI 上传 jar 找不到 | jar 在另一 JM 的 /tmp | 当前 staging-us / prod-us 都锁 `replicas: 1`（base 默认 2，overlay 改成 1）；改回多副本前需先迁到 K8s Job deployer 方式 |
| `transactional.id` 多 job 撞 | Kafka coordinator 端事务互相 abort | 业务 main() 设 `setTransactionalIdPrefix(JOB_NAME + "-" + REGION)` 保证唯一 |
| 升级点 Cancel 不点 Stop | state 回到上次 checkpoint（cancel 后 checkpoint 保留是因 `RETAIN_ON_CANCELLATION`，但跨版本不兼容） | 一律走 CLI `flink stop` / REST API / Stop 按钮，**不点 Cancel** |
| 改 parallelism 直接重启 | keyed state 重分布异常 | 必须 stop-with-savepoint 后用新 parallelism 提交 |

## 下一步

- 看真实文件 / GitLab 链接 → [reference-projects.md](reference-projects.md)
- savepoint 升级细节 → [state-time-checkpoint.md](state-time-checkpoint.md)
- 业务代码模板（用 `ConfigLoader` 等 helper） → [datastream.md](datastream.md)
