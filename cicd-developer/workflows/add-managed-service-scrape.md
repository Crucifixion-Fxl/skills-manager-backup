---
name: add-managed-service-scrape
description: 给云托管中间件（RDS/Redis/Kafka 及各云等价物）接入指标采集——部署 provider exporter（YACE / stackdriver-exporter / tencentcloud-exporter）读云监控 API，用 ExternalSecret 从 Vault 取只读凭据，VMServiceScrape 抓取，VMRule 转成跨云 managed_* 契约，Argo CD 自动录入。区别于 manage-victoriametrics-scrape.md（后者只抓应用自身 pod 的 /metrics）。
---

# Workflow：add-managed-service-scrape

## 目的

给**云托管中间件**（AWS RDS/ElastiCache/MSK、GCP Cloud SQL/Memorystore、腾讯云
TencentDB/CKafka）接入指标采集。这类资源没有可直接抓的集群内 pod，指标在云监控 API
里。末态：

- 一个 provider exporter Deployment 在**该云自己的目标集群**里运行（就近部署），读云监控
  API 并在 `/metrics` 暴露 raw series
- 凭据经 Vault + ExternalSecret 注入（只读、最小权限，绝不裸 Secret）
- `VMServiceScrape` 让 vmagent 抓这个 exporter
- `VMRule` recording rules 把 raw series 转成跨云 `managed_*` 契约
- Argo CD Application/AppProject 录入，合并后自动同步、自动抓取

**不变式（不可协商）**：adapter 是只读旁路采集器，故障绝不影响业务健康。绝不给 adapter
写权限、admission webhook 或跨 namespace 耦合。

开始前读：

- `references/victoriametrics/managed-services-metrics.md`（能力矩阵、managed_* 契约、凭据、坑）
- `references/victoriametrics/README.md`（VMServiceScrape 契约）
- `references/vault-paths/resolver.md` + `references/vault-paths/instances.yaml`（凭据路径与 Vault 实例）
- `references/data/clusters.yaml`（目标集群、`vault_css`、Harbor、argocd 目录）
- [application NodePool placement](../references/nodepool-placement.md)（普通共享池、架构与 NAT 证据）

## 进入条件

- 目标是云托管中间件（不是应用自身 pod；后者用 `manage-victoriametrics-scrape.md`）
- 目标集群装了 VictoriaMetrics Operator（CRD `operator.victoriametrics.com` 存在）与 ESO
- 该 provider 的 exporter 镜像已在目标集群 Harbor（否则走 base-images relay 先同步，见镜像同步规范）

## Step 1. 确认 provider / 目标集群 / 采集范围

[precondition]
  - 用户请求给某类云托管中间件加监控

[action]
  - 从 `references/victoriametrics/managed-services-metrics.md` 的支持矩阵确认 provider + engine
    与对应 exporter：AWS→YACE、GCP→stackdriver-exporter、腾讯云→tencentcloud-exporter。
  - 按**就近部署**定目标集群：adapter 必须跑在该云自己的 prod 集群，指标进该集群 VM。跨云采集
    是 STOP（egress 成本、凭据/数据驻留边界、爆炸半径）。
  - 定采集范围：AWS/GCP 用 auto-discovery（新实例自动纳入）；腾讯云用 `only_include_instances`
    白名单，只列 prod 实例 id，明确排除 staging/pre/test。
  - 只读清点目标资源确认有东西可采（如 `aws rds describe-db-instances`、`gcloud sql instances
    list`、`tccli cdb DescribeDBInstances`）。0 个实例则无需部署。
  - 生成 exporter 前按 NodePool placement reference 解析准确目标的池合同与
    默认 private subnet/NAT 或已确认网络例外的证据。`metrics-collector` namespace、只读 IAM 或 observability
    Project 均不使 exporter 成为平台控制器；不得放到 `cicd-system` / `crossplane-providers`
    专用池。普通 exporter 默认共享，例外按 reference 记录明确的能力/隔离依据。
    所有 provider 默认私有子网 + 稳定 NAT；特殊网络要求须按 reference 明确确认，不能因能力池而豁免。
    EKS 使用已核验的 nodepool、架构、容量和目标已提供的网络约束；NodeClass 通过
    `nodeClassRef` 及运行证据核验，不向 Pod selector/required affinity 注入
    `karpenter.k8s.aws/ec2nodeclass`。network 标签仅在 live 合同提供时使用。TKE/GKE 沿用各自已核验的
    原生池合同。默认保留 amd64，只有 exporter 镜像及目标池架构均核验后才可调整。
    placement 或 reference 第 4 节对应网络路径的证据不足则 STOP + Ops Todo。

[validate]
  - provider/engine 与 exporter 对得上；目标集群就是该云的集群，不是别的云。
  - 腾讯云白名单里没有非 prod 实例。
  - exporter 的目标池身份、架构/容量、必要 tolerations 与默认 NAT/已确认替代路径证据均已记录；
    默认共享池之外的例外有 reference 要求的明确依据。

[output]
  - provider、exporter、目标集群（含 argocd 目录 / Harbor / vault_css）、采集范围与实例清单

## Step 2. 准备只读凭据并写入正确的 Vault

[precondition]
  - Step 1 的 provider 与目标集群已定

[action]
  - 在云侧创建**最小权限只读**身份：
    - AWS：IAM policy（`cloudwatch:GetMetricData`/`ListMetrics`/`tag:GetResources` +
      `rds/elasticache/kafka:Describe*`）+ IAM user + AK/SK。
    - GCP：service account + `roles/monitoring.viewer` + JSON key。
    - 腾讯云：CAM 子用户 + **Monitor readonly 加每个产品的 readonly**（CDB/PostgreSQL/CKafka/Redis）
      + API 密钥。prod 用独立子用户，绝不复用 staging 的（无跨环境）。
  - 用 resolver 规范路径 `secret/{env}/app/metrics-collector/<key>` 写入目标集群 `vault_css`
    指向的 Vault 实例（见 `instances.yaml`）。ExternalSecret 的 `remoteRef.key` 去掉 `secret/`
    前缀。key 名：AWS `aws-credentials`、GCP `gcp-sa-key`、腾讯云 `tencent-credentials`。
  - 凭据不落本地文件、不进 Git、不回显明文。

[validate]
  - 权限是 readonly，无 write/admin/跨账号/跨环境。
  - 从 Vault 读回凭据能实际调通云监控 API（只读验证），再进下一步。
  - 腾讯云确认 Monitor + 产品双 readonly 都附加了。

[output]
  - 云侧身份标识、Vault 路径、凭据链路验证结果

## Step 3. 写 adapter overlay（exporter + ExternalSecret + Service + NetworkPolicy + VMServiceScrape）

[precondition]
  - Step 2 凭据就位并验证

[action]
  - 在 `controllers/metrics-collector/config/prod/<cluster>/` 建 overlay：
    - `namespace.yaml`（`metrics-collector` namespace）
    - adapter：ExternalSecret(vault-backend) + exporter Deployment（按 reference 的单池
      AND / 显式多池 OR 变体写完整 placement，默认含 `kubernetes.io/arch: amd64`；
      arch 不能替代已批准的目标池身份。镜像用目标集群 Harbor 路径，readonly rootfs、drop ALL cap、
      非 root）+ Service（命名 port `metrics`）+ VMServiceScrape
    - `network-policies.yaml`：ingress 仅 vmagent 抓 metrics 端口；egress 仅 DNS + 443 到云 API
    - `kustomization.yaml` 显式列全部文件
  - exporter 特有：AWS YACE 用 discovery jobs（region + 指标列表）；GCP stackdriver 用
    `--google.project-id` + `--monitoring.metrics-type-prefixes`；腾讯云用 config 的 products +
    `only_include_instances` 白名单，凭据用环境变量 `TENCENTCLOUD_SECRET_ID/KEY`，region 在
    `credential:` 块下。
  - **probe**：exporter 若同步现采云 API（腾讯云单次 collect 8-18s），probe 用 `tcpSocket` 不用
    `httpGet /metrics`，否则 probe 超时会 CrashLoopBackOff。
  - **scrape interval/timeout**：单 adapter 采多产品/多实例时 collect 慢，VMServiceScrape 用
    `interval: 5m` + `scrapeTimeout: 4m`，否则整个 scrape 超时丢全部指标。

[validate]
  - 本地 `kustomize build`（或 `kubectl kustomize`）通过，不 apply 集群。
  - 无裸 Secret 承载凭据；exporter 只读、无 webhook/finalizer。
  - 最终 Deployment render 仅允许 Step 1 已核验的共享池或有明确依据的能力例外；
    每个 OR term 的池、架构/容量和适用网络约束完整，无 ec2nodeclass Pod 约束，
    镜像支持所有允许架构，未增加平台池 toleration。

[output]
  - overlay 文件路径、exporter 配置、scrape 参数

## Step 4. 写 recording rules（raw → managed_*）

[precondition]
  - Step 3 overlay 就绪（先部署观察 raw 指标名，或已知该 provider 的 raw 命名）

[action]
  - 先确认目标集群 `vmalert` 的 `selectAllByDefault`（`kubectl -n victoria-metrics get vmalert
    -o jsonpath=...`）：
    - `true` → VMRule 放 `metrics-collector` namespace（挨着 adapter）
    - `false` → VMRule **必须**放 `victoria-metrics` namespace，否则被静默忽略、managed_* 不产出
  - 写 `VMRule`，把观察到的 raw series（`aws_*`/`stackdriver_*`/`qce_*`）映射到 `managed_sql_*`
    / `managed_cache_*` / `managed_broker_*`，带 label `__managed_source__` /
    `__managed_contract__: v1` / `provider` / `engine`（见 reference 的契约清单）。
  - 云监控数据稀疏（5m 一个点）时，expr 用 `last_over_time(raw[10m])` 且 group `interval: 5m`，
    否则 vmalert 瞬时求值踩 staleness 产 0 samples。

[validate]
  - `kustomize build` 通过；record 名与 managed_* 契约一致；label 齐全。
  - VMRule namespace 与该集群 vmalert 的 selectAll 语义匹配。

[output]
  - VMRule 文件、group/record 清单、namespace 决策依据

## Step 5. Argo 录入（Application + AppProject + Kyverno exception）+ boundary lint

[precondition]
  - Step 3/4 的 overlay 与 rules 已在 observability-platform 仓合并（取到 merge SHA）

[action]
  - 在 argocd-apps 的 `<cluster>/` 建：
    - `appproject-platform-observability.yaml`（sync-wave -1，sourceRepos 锁
      observability-platform.git，只 whitelist 本 overlay 用到的精确 GVK；腾讯云 exporter 有
      config ConfigMap 时 whitelist ConfigMap）
    - `metrics-collector.yaml`（Application，targetRevision pin 到 merge SHA，path 指向 overlay，
      syncPolicy automated + ServerSideApply）
  - 目标集群若有 Kyverno `require-rollout-for-new-apps` 且 `metrics-collector` 不在 exclude，
    在 `DEV/k8s` 的 `clusters/<cluster>/cicd/kyverno/post-install/` 加 PolicyException，精确限定该
    adapter Deployment，只豁免 require-rollout。
  - argocd boundary lint：为新集群/新 path 加 per-cluster path contract 与 NetworkPolicy 到
    `tools/argocd_boundary_lint.py` 的 platform-observability whitelist，并同步相关 test 断言。

[validate]
  - `python3 tools/lint-argocd-boundary.py` 对新文件零 finding；相关 unittest 全过。
  - Application project 用 `platform-observability`，destination namespace 精确。

[output]
  - Application/AppProject/PolicyException 文件、lint 与 test 结果

## Step 6. 合并、同步、验证 managed_* + 不变式

[precondition]
  - Step 5 各仓 MR pipeline 全绿、由用户合并

[action]
  - 按各仓 GitOps 规范推进合并；Application targetRevision advance 到含全部改动的 SHA。
  - Argo sync 后（AppProject wave -1 先，Application 后）确认 adapter pod Running、ExternalSecret
    SecretSynced、YACE/exporter `/metrics` 产出 raw、raw 进 VM。
  - 读回实际 Pod/Node 的目标池身份、架构/容量，另核对 NodeClass 供给配置及运行证据，
    按 reference 第 4 节核对默认 NAT 出口或已确认替代路径，完成运行态 placement 验收。
  - 等一个 scrape 周期 + 一个 recording rule group interval 后，用 **vmalert 同款 datasource
    `vmselect-victoria-metrics`（headless，不是 -public）** 查 `managed_*{provider="..."}`
    确认产出。
  - 验证不变式：删 adapter pod，确认业务 Application 全程 Healthy、唯一 Progressing 是
    metrics-collector 自身。

[validate]
  - 每个 managed_* 有预期 series 数（对应实例数）。
  - adapter 故障期间零业务 Application 受影响。

[output]
  - managed_* 产出验证、不变式验证结果、live 集群状态

## 开发者接入新实例（部署后日常）

- AWS/GCP：auto-discovery，新建 RDS/Redis/Kafka **零操作**，下一采集周期自动纳入并产出 managed_*。
- 腾讯云：白名单模式，新建 prod 实例需把实例 id 加进 adapter config 的 `only_include_instances`，
  走一个小 MR + advance SHA；改成 auto-discovery 会把 staging/test 也采进来，需权衡。
