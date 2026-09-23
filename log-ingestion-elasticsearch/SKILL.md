---
name: log-ingestion-elasticsearch
description: 引导将新应用接入 FluentBit→Kafka→Vector→ES 日志收集体系。含前置调研 hard-stop、baseline 捕获、分阶段 apply + 健康门禁、10 跳端到端证据链验证。当运维人员说"接入日志收集"、"配置日志"、"新应用日志"、"把 XX 的日志接上"、"onboard logging"、"日志接入"，或需要将任何新服务加入现有日志管道时使用此 Skill。即使用户只提到某个新应用需要看日志、查日志，也应考虑触发。
---

# 日志收集接入

将新部署的应用容器日志接入现有 `FluentBit → Kafka → Vector → Elasticsearch` 管道，让运维能在 ES 中按服务维度检索日志。

## 描述

本 skill 把新应用接入既有日志收集管道。覆盖 US / EU / CN 三个地区，适配 K8s ConfigMap（US/EU）与主机 TOML（CN）两种配置托管模式。

**适用场景**：运维人员说"接入日志收集"、"配置日志"、"新应用日志"、"把 XX 的日志接上"、"onboard logging"、"日志接入"，或需要将新服务加入现有日志管道的任何场景。

**不适用**：查询 ES 现有日志（用 `troubleshooting` skill）、告警排查（用 `sla-alert-analysis` skill）、从零搭建日志栈（本 skill 假设管道已存在）。

**核心动作**：参数采集 → 前置调研 hard-stop → baseline 捕获 → 分 AZ 改 ConfigMap → 分阶段 apply + 健康门禁 → 10 跳端到端证据链验证。

## 规则

这些规则每一条都对应真实付过代价的故障场景，按顺序贯穿执行，不得裁剪。

1. **前置调研 7 问未全答清不得动配置**。改错集群、误填 container 名、忽略"空壳"集群、Vector transform 模板选错是最常见的静默失败源头。清单见 [references/preflight-checklist.md](references/preflight-checklist.md)。
2. **`{app}` 和 `{container}` 要分别确认**。业务名（ArgoCD app / 服务名）和 K8s container 真实名经常不相等。FB Path glob 用 `{container}`；topic / ES index / FB Tag / ConfigMap 数据键用 `{app}`。
3. **任何 apply 前必须先采 baseline**。没有 baseline 就无法区分"变更后某 topic rate 下降"是本次变更导致的还是本来就这样；"lag 涨了"是真故障还是 rebalance 瞬间。6 项 baseline 见 Step 3。
4. **分阶段 apply，每阶段过 4 Gate**。3 AZ 必须灰度滚动（1 AZ → 其他 2 → Vector），每 Stage 过 G1-G4（rollout / pod 健康 / 无 topic rate 下降 / 无持续 lag）才进下一个。Stage D 30 分钟观察期不能省。
5. **"pod Running"不算 done，必须走完 10 跳证据链**。应用容器 → 文件匹配 → FB 同节点 → Kafka output → 实际 offset 增长 → Vector 订阅 → Vector 不落后 → ES 索引 → ES 文档 → 样本字段展开正确。任一跳缺证据回到排障，不得"提前宣布胜利"。
6. **CN 所有配置均未纳入 git，修改前必须先备份**。Vector 是主机 TOML（`/etc/vector/`），FluentBit 仓库配置可能落后于集群实际；以 kubectl 导出的实际配置为准。
7. **不碰 Helm 共享基础**。eu-prod 存在 "Helm release + git zone DS 共存" 拓扑——Helm release 的 ConfigMap / SA / CR / CRB / Service 作为共享基础保留，改动 git zone DS 时不得误删 Helm release 资源。
8. **空壳集群需要专门接管流程**。看见 FB pods Running 不等于管道在工作——tech-service 类集群可能只是 Helm 装的"空壳"，OUTPUT 只往 stdout 写。判别方式和接管模板见 Step 4。
9. **每次修改 ConfigMap 后必须 rollout restart** 对应的 DaemonSet / Deployment 才能生效，不存在"ConfigMap 热加载"。
10. **地区命名规则有差异，CN ES index 还按服务类型三分**。US/EU topic 和 ES index 同为 `addx-{region}-{env}-{app}`。CN topic 统一是 `addx-cn-{env}-{app}`（不管什么服务）；但 CN ES index 按目标 Vector toml 文件分三套：`_backend.toml` → `k8s-{env}-cn-{app}`、`_ai.toml` → `a4x-algorithm-cn-{env}-{app}`（部分服务有 `-k8s` 后缀存量漂移，以同文件现有 entry 为准）、`a4x_cn_all_kiss.toml` → `addx-cn-{env}-kiss`。写错会让日志进 Kafka 却从未落到 ES。
11. **不支持灰度（gray）环境和 S3 数据仓库备份**。收到这类请求直接说明范围外。

## 架构

```
应用容器 → FluentBit (DaemonSet, ns:logging)
         → Kafka (topic 自动创建)
         → Vector → Elasticsearch (按日索引)
```

### 支持范围

| 地区 | FluentBit | FluentBit 托管 | Vector | Vector 托管 |
|------|-----------|---------------|--------|-------------|
| US | K8s ConfigMap × 3 AZ | Git (DEV/k8s) | K8s ConfigMap | Git (DEV/k8s) |
| EU | K8s ConfigMap × 3 AZ | Git (DEV/k8s) | K8s ConfigMap | Git (DEV/k8s) |
| CN | K8s ConfigMap × 1 | Git (DEV/k8s)，可能落后 | 主机 TOML | 直接在主机上管理 |

环境：staging / pre / prod（CN 额外支持 test）。

### CN 特殊基础设施

- Vector：独立 CVM `cn-public-log-vector-01`（公网 `49.232.30.205` / 内网 `10.0.64.16`），systemd 服务
- 配置目录 `/etc/vector/`，按 `a4x_cn_{env}_{type}.toml` 组织
- ES：独立实例 `10.0.5.20:9200`（非 K8s 内的 ES）
- Kafka：腾讯云 CKafka 托管 `cn-public-log-kafka`（`10.0.5.12:9092`）

---

## 实施步骤

### Step 0: 前置调研（hard-stop）

必须在任何配置修改之前通过 6 个问题的验证。**任一问题答不上就 STOP**——不得"先写 MR 再说"、不得猜值、不得绕过。

完整命令清单与决策表见 [references/preflight-checklist.md](references/preflight-checklist.md)。

| # | 问题 | 典型失败代价 |
|---|---|---|
| Q1 | 目标服务部署在**哪个 kubectl context**？（不是地区名，不是 ArgoCD URL） | 改错集群，整套变更打水漂 |
| Q2 | **真实 container 名**是什么？（不是 ArgoCD app 名，不是业务名） | FB path glob 永远匹配不到任何文件 |
| Q3 | **log 文件 glob** 是否唯一匹配目标 container？ | 误匹配同 ns 其他服务，或漏匹配 |
| Q4 | 目标集群**真的有工作中的日志管道吗**？（不是"有 FB pod 在跑"） | 遇到"空壳"集群，FB 只往 stdout 写 |
| Q5 | 目标集群能**到达 log kafka brokers** 吗？ | 网络不通导致 FB 启动后狂报连接失败 |
| Q6 | FluentBit 是**谁管的**（git / Helm / Terraform / ArgoCD）？ | 选错修改模式：Helm 被 Terraform 反弹、ArgoCD 自动 sync 覆盖 |
| Q7 | 应用日志的**行格式**是什么（JSON / 纯文本 / 混合）？ | Vector transform 模板选错（strict `parse_json!` 对非 JSON panic），ES 收到 0 业务文档 |

常见错走场景（命中立刻停下回 Step 0）：

- ArgoCD URL `argocd-eu-tech-service.addx.live` → 目标**不在** `eu-prod`，而在 `eu-tech-service`
- ArgoCD app 名 `naturehood-api-staging-eu` → 实际 Deployment 叫 `naturehood`，container 名也叫 `naturehood`
- 集群里有 FB DS Running → 可能只是 Helm 空壳，OUTPUT 是 stdout

### Step 1: 收集参数

从用户请求提取，缺失主动询问。语义与示例见 [references/preflight-checklist.md §1](references/preflight-checklist.md)。

| 参数 | 含义 | 用于 |
|------|------|------|
| `app` | 业务/服务逻辑名 | topic / ES index / FB Tag / ConfigMap 数据键 |
| `container` | K8s container 真实名（Q2 验证过） | **仅** FB Path glob 最后一段 |
| `namespace` | 应用所在 K8s namespace | FB Path glob 中间段 |
| `environments` | 接入环境列表 | conf 文件名、topic 名 |
| `regions` | 接入地区列表 | 选择目标集群 |

关键事实：`{app}` 和 `{container}` 可以完全不同。本 skill 开发时的真实案例：`{app}=naturehood-api`，`{container}=naturehood`——**不要假设二者相等**。

未指定环境/地区时默认全量（US/EU/CN × staging/pre/prod）。

### Step 2: 读取现有配置

进入 k8s 仓库并拉最新：

```bash
cd ~/Project/A4x/k8s
git checkout master && git pull
```

> 默认分支是 `master`（不是 `main`），写错会报 "pathspec did not match any file(s)"。

配置模板、命名规范、跨集群拓扑见 [references/config-templates.md](references/config-templates.md)。

**关键事实：FluentBit 和 Vector 可能不在同一集群**。FB 是 DaemonSet，只能 tail 本集群节点日志，改哪个 FB 看应用所在集群（Q1 结果）。Vector 是集中消费者，每地区只有一个"主" prod 集群真正运行 Vector，改 vector-config 只改那一个。

US/EU 文件路径（基础路径 `~/Project/A4x/k8s/clusters/`）：

| 地区 | FluentBit ConfigMap（按 Q1 选集群） | Vector ConfigMap（固定主集群） |
|------|---|---|
| US tech-service | `aws-002497567426-us-tech-service/fluent-bit/fluent-bit-configmap-us-east-1{a,b,c}.yaml` | `aws-302571458622-us-prod/vector/configmap/vector-config.yaml` |
| US prod | `aws-302571458622-us-prod/fluent-bit/fluent-bit-configmap-us-east-1{a,b,c}.yaml` | （同上） |
| EU tech-service | `aws-010840394398-eu-tech-service/fluent-bit/fluent-bit-configmap-eu-central-1{a,b,c}.yaml` | `aws-740315635167-eu-prod/vector/configmap/vector-config.yaml` |
| EU prod | `aws-740315635167-eu-prod/fluent-bit/fluent-bit-configmap-eu-central-1{a,b,c}.yaml` | （同上） |

**CN 文件路径**：
- FluentBit: 集群实际 ConfigMap（仓库可能不是最新）
- Vector: 主机 `/etc/vector/`（需 SSH 到 `49.232.30.205` 读取）

读取时重点关注：
- FluentBit 找最后一个 `@INCLUDE input-*` 和 `@INCLUDE output-kafka-*` 行作为插入点
- Vector 找对应环境的 sources/transforms/sinks TOML section
- 从已有条目提取 Kafka Broker 地址和 ES endpoint

### Step 3: Baseline 捕获

在任何 apply 前把现有 pipeline 的健康指标快照到 `${A4X_RESOURCE_CHANGES_DIR:-$HOME/Project/A4x/resource_changes}/<YYYY-MM-DD>/<change-slug>/baseline/`。

完整命令与 PromQL 见 [references/baseline-queries.md](references/baseline-queries.md)。

必采 6 项：

| # | 内容 | 文件 |
|---|---|---|
| B1 | FluentBit + Vector pod 列表和健康 | `fb-pods-before.txt` / `vector-pods-before.txt` |
| B2 | 每个 topic 的 5m produce rate | `topic-produce-rate-before.json` |
| B3 | 每个 topic 的总 offset 快照 | `topic-offsets-before.json` |
| B4 | 每个 consumer group 的 lag | `consumer-lag-before.json` |
| B5 | 新 topic 先验检查（防重名） | 命令输出 |
| B6 | 新增 DS 前节点资源 headroom | `nodes-resource-before.txt` |

**CN 额外**：修改前强制备份当前 ConfigMap 和主机 TOML：

```bash
BACKUP_DIR=${A4X_RESOURCE_CHANGES_DIR:-$HOME/Project/A4x/resource_changes}/cn-log-config-backups/$(date +%Y%m%d-%H%M%S)
mkdir -p $BACKUP_DIR
kubectl --context tencent-100014919455-cn-main -n logging get cm fluent-bit -o yaml > $BACKUP_DIR/fluent-bit-cm.yaml
scp -i ~/.ssh_addx/id_rsa root@49.232.30.205:/etc/vector/*.toml $BACKUP_DIR/
```

### Step 4: 修改 FluentBit

先开分支：

```bash
git checkout -b feat/log-onboarding-{app}
```

Path glob 3 段结构（用 `{container}` 不是 `{app}` 做匹配、约束 namespace 避免跨 ns 误匹配、约束 container 名避免同 ns 其他服务误匹配）和完整模板见 [references/config-templates.md](references/config-templates.md)。

**US/EU（多 AZ @INCLUDE 模式）**——每个 AZ 的 ConfigMap 都要改，每环境：

1. `fluent-bit.conf` 数据键 INPUT 区域末尾加 `@INCLUDE input-{app}-{env}.conf`
2. `fluent-bit.conf` 数据键 OUTPUT 区域末尾加 `@INCLUDE output-kafka-{app}-{env}.conf`
3. 新增 `input-{app}-{env}.conf` 数据键（`Path /var/log/containers/{container}-*_{namespace}_{container}-*.log`）
4. 新增 `output-kafka-{app}-{env}.conf` 数据键（topic = `addx-{region}-{env}-{app}`）

**3 个 AZ 文件内容完全相同，唯一区别是 OUTPUT 的 Kafka Broker 地址**。

**空壳集群接管**（Q6 判定为"仅 Helm 空壳"）：走 "eu-prod 共存模式"——git 仓库新建 `clusters/<new-cluster>/fluent-bit/` 目录，复制 eu-prod 6 文件模板（3 AZ ConfigMap + 3 AZ DaemonSet）并调整：

- ConfigMap 只保留 1 套 INPUT/OUTPUT（仅接入目标服务），避免继承 eu-prod 全量配置
- DaemonSet `serviceAccountName: fluent-bit`（复用 Helm 创建的 SA）
- DaemonSet Kafka broker 按 AZ 指向 eu-prod log kafka 集群（先做 Q5 连通性验证）

Apply 新 DS 后手动 `kubectl delete ds fluent-bit -n logging` 删除 Helm 原 DS（保留 SA/CR/CRB/CM/Service）。注意 Terraform 反弹风险见 preflight Q6 决策表。

**CN（单文件内联模式）**——fluent-bit.conf 不用 @INCLUDE，INPUT/OUTPUT 直接写在 fluent-bit.conf 中。每环境在对应区域末尾追加 INPUT 和 OUTPUT 块。

> CN git 仓库配置可能不是最新。以 Step 3 备份的实际配置为准修改。改完既 push 到 git 仓库（保持同步）也 kubectl apply。

### Step 5: 修改 Vector

Vector 配置模板见 [references/config-templates.md](references/config-templates.md)。

**先按 Q7 日志格式选 transform 模板**（A / B / C），再追加。`config-templates.md` 里的"Vector transform 模板选择"表和"已有服务 → 模板索引"是选型依据。**默认选模板 A**（纯文本/最安全），除非日志行确实是结构化 JSON。选错会导致事件被 strict `parse_json!` 静默丢弃、ES 收到 0 业务文档。

**US/EU（K8s ConfigMap TOML）**——配置在 ConfigMap 中按环境分组为多个 TOML section（`sources-{env}.toml` / `transforms-{env}.toml` / `sinks-{env}.toml`）。每环境：

1. **Source**：`sources-{env}.toml` 的 topics 数组追加新 topic
2. **Transform**：按 Q7 选定的模板 A/B/C，在 `transforms-{env}.toml` 追加对应 remap 块
3. **Sink**：`sinks-{env}.toml` 追加 Elasticsearch sink 块

**CN（主机 TOML 文件）**——按服务类型分文件：

| 文件 | 覆盖范围 |
|------|---------|
| `a4x_cn_{env}_backend.toml` | 后端服务（iot-service, auth 等） |
| `a4x_cn_{env}_ai.toml` | AI 服务（gpu-infer, smart-multimedia 等） |

通过 SSH 修改（跳板机 `152.136.37.192` 或直连 `49.232.30.205`）。每环境在对应文件中追加 sources topic / transform / sink 三块。改完 `systemctl restart vector`。

### Step 6: 提交 MR（US/EU 部分）

```bash
git add -A
git commit -m "feat: onboard {app} logging for {regions}"
```

用 `gitlab-mr` skill 创建 MR 到 DEV/k8s 仓库的 **master** 分支。等 MR 合并后再进 Step 7。

> CN 改动不走 MR（配置未进 git 管理），直接走 Step 7 apply + 证据链。

### Step 7: 分阶段 apply + 健康门禁

**不准一把梭 apply**。按灰度顺序执行，每阶段必须通过 4 Gate 才能进入下一阶段：

```
Stage A: 先 1 个 AZ 的 FluentBit (CM + DS)
  └─ G1-G4 全通过 ┐
Stage B: 其他 2 个 AZ 的 FluentBit
  └─ G1-G4 全通过 ┤
Stage C: Vector CM + rollout restart
  └─ G1-G4 全通过 ┘
Stage D: 等待 30 分钟（让 5m 窗口完整滚过 rollout 空白期）
  ↓
Stage E: 证据链验证（Step 8）
```

Gate 判据和命令见 [references/verification-runbook.md §1](references/verification-runbook.md)：

| Gate | 检查内容 | 典型卡点 |
|---|---|---|
| **G1** | `kubectl rollout status` 成功（`ds` for FB，`deploy` for Vector） | 超时 → 回滚 |
| **G2** | Pod 全 Running、0 restart、逐 pod grep `error\|warn\|fail\|unknown` = 0（排除白名单） | 真 error → STOP；假阳见 runbook §2 |
| **G3** | 对比 baseline B2：无 topic 5m rate 下降 > 50% | 命中先用 1m 窗口复核，大概率是"窗口拖尾"假阳 |
| **G4** | 对比 baseline B4：无 consumer lag > max(3×baseline, 500) 且持续增长 | 命中先看 `deriv[2m:]` 正负，rebalance 瞬间不算 |

**Stage D 30 分钟观察期不能省**——rollout 期间的空白让 5m 窗口拖尾最长 5 分钟，30 分钟留足多轮观察让 drift 暴露或恢复。

**CN 单 stage**：CN 只有 1 个 FluentBit ConfigMap，无 AZ 灰度概念。Stage A = Stage B，直接走一轮 G1-G4。

apply 前**必须**按 `ops-guardrails` 的资源变更审批要求出变更审批表，等用户批准后执行。具体命令模板见 [references/config-templates.md §4](references/config-templates.md)。

apply 后**必须**记录到 `${A4X_RESOURCE_CHANGES_DIR:-$HOME/Project/A4x/resource_changes}/<YYYY-MM-DD>/<slug>/`（`SUMMARY.md` + `backup/`）。

### Step 8: 端到端证据链验证

**只查"FB pod Running"是不够的**。必须走完 10 跳证据链，任一跳缺证据就不算通过。

详细命令见 [references/verification-runbook.md §3](references/verification-runbook.md)，完成判据见 [§4](references/verification-runbook.md)。

10 跳概览：

1. 应用容器真实存在（`kubectl get pods`）
2. 容器日志文件名匹配 FB glob（用 busybox 挂 hostPath 验证）
3. FB pod 与 app pod 在同一节点
4. FB 注册了 kafka output 到正确 topic（FB pod logs）
5. **Kafka 实际收到消息**且 offset 持续增长（`kafka_topic_partition_current_offset`）
6. Vector 订阅了新 topic 且有 lag 数据点（`kafka_consumergroup_lag`）
7. Vector 不落后（lag ≤ 阈值且不增长）
8. ES 索引存在且 green（集群内 pod 访问 `/_cat/indices`）
9. ES 有文档（`/_count > 0` 且持续增长）
10. 样本文档字段展开正确（`_search` 采样 3 条，业务字段是顶层而不是堆在 `message` 里）

---

## 示例

### Bad：跳过 Step 0 直接动配置

用户说"把 naturehood-api 接到日志收集"，AI 看到 ArgoCD app 名 `naturehood-api-staging-eu`，想当然地把 `container_name: naturehood-api` 写进 FluentBit Path glob，推 MR，pipeline 绿，apply 上线。**结果：ES 里 0 条日志。**

根因：真实 Deployment 叫 `naturehood`，container 也叫 `naturehood`——ArgoCD app 名是业务标签，不是工作负载名。glob `/var/log/containers/*naturehood-api*.log` 永远匹配不到任何文件，FB 本身运行正常、Kafka 不报错、Vector 不报错，**整条管道"静默失败"**。必须回 Step 0 Q2：`kubectl get deploy -n <ns>` 拿真实名字。

### Good：Step 0 6 问全部答清再动手

接同一个需求时先停下来跑完 Q1-Q6：

1. Q1 kubectl context → `eu-prod`（确认不是 `eu-tech-service`）
2. Q2 container 名 → `kubectl get deploy -n default naturehood -o jsonpath='{.spec.template.spec.containers[*].name}'` → `naturehood`
3. Q3 glob 唯一性 → 进 FB pod `ls /var/log/containers/ | grep naturehood` → 确认 `/var/log/containers/naturehood-*_default_naturehood-*.log` 唯一匹配
4. Q4 管道在位性 → `kubectl logs -n logging ds/fluent-bit | grep -E 'kafka|flush'` 看到 Kafka flush 日志（不是"空壳" stdout）
5. Q5 网络可达 → `kubectl exec -n logging ds/fluent-bit -- nc -zv <kafka-broker> 9092` 通
6. Q6 管理方 → 是 git 管理还是 Helm / Terraform / ArgoCD 托管

全部通过后进 Step 2-8。**流程没有"静默失败"空间**——每一跳都有证据。
