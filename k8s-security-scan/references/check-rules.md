# K8s Security Scan 完整规则目录

每条规则包含：ID、严重级别、检测目标（YAML 路径）、判定逻辑、边界情况。

`cicd-developer` 的 Review / Scan 模式和 `references/data/hard-rules.yaml` 是 SSOT；本文件是 pipeline scanner 的镜像。两边不一致时，以 `cicd-developer` 为准并回修本文件。

## Category 0: 仓库边界 (BND)

适用范围：业务应用仓 `k8s/` overlay。`argocd-apps` 与 `crossplane-infra` 本身不使用本类规则。

### BND-01 CRITICAL: ArgoCD Application 不放应用仓

| 属性 | 值 |
|------|-----|
| 检测 | `kind: Application` 出现在业务应用仓 `k8s/` |
| 判定 | CRITICAL |
| 期望 | Application YAML 放 `DEV/argocd-apps/<cluster>/` |

### BND-02 CRITICAL: 中心化权限/边界资源不放应用仓

| 属性 | 值 |
|------|-----|
| 检测 kind | IAM Role/RolePolicy/Policy、ProviderConfig、WAFv2、NineData ProviderConfig、SecurityGroupIngressRule |
| 判定 | 出现在业务应用仓 `k8s/` overlay → CRITICAL |
| 期望 | 放 `DEV/crossplane-infra/<cluster>/`，集中审批 |
| 原因 | 这些资源改的是共享权限、共享 SG 或平台边界，爆炸半径不属于单个应用仓 |

### BND-03 PASS: app-owned 数据面 CR 可留应用仓

| 属性 | 值 |
|------|-----|
| 允许 kind | S3 Bucket/Policy/PublicAccessBlock/SSE/Versioning/Lifecycle、RDS Instance、Aurora Cluster/ClusterInstance、ElastiCache、CloudFront Distribution/OAC/BucketPolicy |
| 条件 | `providerConfigRef` 指 per-app ProviderConfig，连接 Secret 与应用 namespace 对齐 |
| 说明 | 这些资源随应用生命周期管理；不要误报边界错误。staging/tech-service 是否允许 app-owned 另按 hard rule #29 判定 |

## Category 1: Crossplane 资源治理 (CRX)

适用 Kind：所有 apiVersion 包含 `aws.m.upbound.io`、`aws.upbound.io`、`s3.aws`、`rds.aws`、`elasticache.aws`、`dynamodb.aws`、`sqs.aws`、`sns.aws`、`lambda.aws`、`cloudfront.aws` 的资源。

### CRX-01 CRITICAL: providerConfigRef 必须有 kind

| 属性 | 值 |
|------|-----|
| 检测路径 | `spec.providerConfigRef.kind` |
| 判定 | 字段不存在 → CRITICAL |
| 期望值 | `ClusterProviderConfig`（monolithic provider）或 `ProviderConfig`（individual provider，仅 CloudFront） |
| 原因 | 不写 kind 默认匹配 namespace-scoped `ProviderConfig`，如果不存在则静默失败，不报任何错误 |

### CRX-02 CRITICAL: providerConfigRef.name 禁止 default

| 属性 | 值 |
|------|-----|
| 检测路径 | `spec.providerConfigRef.name` |
| 判定 | 值为 `default` → CRITICAL |
| 原因 | default ProviderConfig 使用 CrossplaneProviderRole 的全部权限，Kyverno 在运行时也会拦截 |

### CRX-03 WARNING: providerConfigRef 在 crossplane-infra 中必须有对应 ClusterProviderConfig

| 属性 | 值 |
|------|-----|
| 检测路径 | `spec.providerConfigRef.name` |
| 跨仓库查找 | 在 crossplane-infra 的目标集群目录中搜索 `kind: ClusterProviderConfig`，`metadata.name` 匹配 |
| 判定 | 未找到匹配的 ClusterProviderConfig → WARNING |
| 环境映射 | staging-us → `aws-390709477306-us-staging/`；pre/prod-us → `aws-302571458622-us-prod/`；staging-eu → `aws-390709477306-eu-staging/`；pre/prod-eu → `aws-740315635167-eu-prod/`；staging-cn → `aws-801447536674-cn-staging/`；dev-cn → `aws-801447536674-cn-dev/`；pre/prod-cn → `aws-741924744516-cn-prod/` |

### CRX-04 WARNING: RolePolicy 最小权限

| 属性 | 值 |
|------|-----|
| 检测路径 | crossplane-infra 中 `kind: RolePolicy` 的 `spec.forProvider.policy` JSON |
| 判定 | `Resource: "*"` 且 `Action` 含通配符（如 `s3:*`、`rds:*`）→ WARNING |
| 豁免 | ElastiCache（`elasticache:*` + `Resource: "*"` 因 AWS ARN 限制允许）、CloudFront 同理 |
| 建议 | Resource 按 app 名称前缀限定：`arn:aws:s3:::{app}-*` |

### CRX-05 WARNING: Crossplane 资源必须有标签

| 属性 | 值 |
|------|-----|
| 检测路径 | `spec.forProvider.tags` |
| 判定 | 缺少 `app`、`env`、`managed-by` 中任一 → WARNING |
| 期望值 | `app: {app-name}`、`env: {overlay-name}`、`managed-by: crossplane` |

### CRX-06 INFO: CloudFront 用 ProviderConfig

| 属性 | 值 |
|------|-----|
| 检测路径 | apiVersion 含 `cloudfront` 的资源的 `spec.providerConfigRef` |
| 判定 | CloudFront 资源使用 `kind: ClusterProviderConfig` → INFO（应使用 `ProviderConfig`，apiVersion `aws.upbound.io/v1beta1`） |
| 原因 | CloudFront provider 是 individual provider family（`aws.upbound.io`），不是 monolithic（`aws.m.upbound.io`） |

---

## Category 2: Ingress 安全 (ING)

适用 Kind：`Ingress`（`networking.k8s.io/v1`）

### ING-01 WARNING: 公网 Ingress 需标注

| 属性 | 值 |
|------|-----|
| 检测路径 | `metadata.labels["ingress.addx.io/sg"]` 和 `metadata.annotations["alb.ingress.kubernetes.io/scheme"]` |
| 判定 | 无 sg 标签且 scheme 非 internal（或无 scheme 注解）→ WARNING |
| 说明 | 公网暴露的 API 需要显式意识，提醒开发者确认是否需要公网访问 |

### ING-02 WARNING: 内部 API 建议使用 SG 标签

| 属性 | 值 |
|------|-----|
| 判定 | 辅助性建议，与 ING-01 配合。无 sg 标签时提示可选 `office`（仅办公 IP）或 `internal`（VPC 内部） |

### ING-03 CRITICAL: host 必须 *.addx.live

| 属性 | 值 |
|------|-----|
| 检测路径 | `spec.rules[*].host` |
| 判定 | host 不匹配 `*.addx.live` → CRITICAL |
| 正则 | `^[a-z0-9-]+\.addx\.live$` |

### ING-04 WARNING: 必须指定 healthcheck-path

| 属性 | 值 |
|------|-----|
| 检测路径 | `metadata.annotations["alb.ingress.kubernetes.io/healthcheck-path"]` |
| 判定 | 注解不存在 → WARNING |
| 常见值 | `/health`、`/actuator/health`、`/healthz` |

### ING-05 CRITICAL: 禁止手写 certificate-arn

| 属性 | 值 |
|------|-----|
| 检测路径 | `metadata.annotations["alb.ingress.kubernetes.io/certificate-arn"]` |
| 判定 | 注解存在 → CRITICAL |
| 原因 | ALB 根据 host 自动发现 `*.addx.live` 通配符证书，手写会导致证书固定、难以轮换 |

### ING-06 CRITICAL: 禁止无说明手写 security-groups

| 属性 | 值 |
|------|-----|
| 检测路径 | `metadata.annotations["alb.ingress.kubernetes.io/security-groups"]` |
| 判定 | 注解存在且没有批准说明 / 明确自定义 SG 场景 → CRITICAL |
| 原因 | 默认 SG 由 Kyverno 根据 `ingress.addx.io/sg` 标签自动注入；手写 SG 容易绕过安全策略。跨账号、自定义 allowlist、WAF/SG 联动等例外必须在 MR 中说明 |

### ING-07 INFO: TKE Ingress 须用 qcloud

| 属性 | 值 |
|------|-----|
| 检测路径 | overlay 路径含 `cn-k8s` 或 `tencent` 且 Ingress 的 `spec.ingressClassName` |
| 判定 | TKE 环境使用 `alb` 而非 `qcloud` → INFO |
| 说明 | TKE 使用腾讯云 CLB，ingressClassName 应为空（通过注解 `kubernetes.io/ingress.class: qcloud` 指定） |

### ING-08 WARNING: 禁止手写 Kyverno 管理的注解

| 属性 | 值 |
|------|-----|
| 检测注解 | `scheme`、`target-type`、`listen-ports`、`ssl-redirect`（前缀 `alb.ingress.kubernetes.io/`） |
| 判定 | 这些注解存在 → WARNING |
| 豁免 | `scheme: internal` 允许显式指定（内网服务） |
| 原因 | Kyverno 会自动注入这些默认值，手写可能与策略冲突 |

---

## Category 3: IRSA / ServiceAccount (IRSA)

适用 Kind：`ServiceAccount`（`v1`）

### IRSA-01 WARNING: role-arn 在 crossplane-infra 中必须存在

| 属性 | 值 |
|------|-----|
| 检测路径 | `metadata.annotations["eks.amazonaws.com/role-arn"]` |
| 跨仓库查找 | 从 ARN 中提取 role name，在 crossplane-infra 中搜索 `kind: Role` 的 `crossplane.io/external-name` 匹配 |
| 判定 | 有 role-arn 注解但 crossplane-infra 中找不到对应 Role → WARNING |

### IRSA-02 WARNING: role ARN 命名规范

| 属性 | 值 |
|------|-----|
| 检测路径 | `metadata.annotations["eks.amazonaws.com/role-arn"]` |
| 判定 | role name 不以 `crossplane-app-` 开头 → WARNING |
| 期望 | `crossplane-app-{app}-irsa` 或 `crossplane-app-{app}` |
| 原因 | CrossplaneHubPolicy 只允许管理 `crossplane-app-*` 角色 |

### IRSA-03 WARNING: IRSA Role 必须有 permissionsBoundary

| 属性 | 值 |
|------|-----|
| 跨仓库查找 | crossplane-infra 中对应 Role 的 `spec.forProvider.permissionsBoundary` |
| 判定 | 字段不存在 → WARNING |
| 期望 | `arn:aws:iam::{account}:policy/CrossplaneAppBoundary` |

### IRSA-04 INFO: ARN partition 匹配

| 属性 | 值 |
|------|-----|
| 检测路径 | `metadata.annotations["eks.amazonaws.com/role-arn"]` |
| 判定 | CN 环境（overlay 含 `-cn`）使用 `arn:aws:` 而非 `arn:aws-cn:` → INFO |
| 反之 | 非 CN 环境使用 `arn:aws-cn:` → INFO |

---

## Category 4: 密钥管理 (SEC)

### SEC-01 CRITICAL: 禁止硬编码 K8s Secret

| 属性 | 值 |
|------|-----|
| 检测 | `kind: Secret` 且包含 `data:` 或 `stringData:` 字段 |
| 判定 | CRITICAL |
| 豁免 | `type: kubernetes.io/dockerconfigjson`（imagePullSecret）允许 |
| 建议 | 改用 ExternalSecret + Vault |

### SEC-02 WARNING: Vault 路径规范

| 属性 | 值 |
|------|-----|
| 检测路径 | ExternalSecret 的 `spec.data[*].remoteRef.key` |
| 判定 | 新应用不匹配 `{env}/app/{app}/{key}` 或 `{env}/<platform>/application/{app}/{key}` 模式 → WARNING；历史老路径只提示不阻断 |
| 正则 | `^(dev|staging|pre|prod)/(app/[a-z0-9-]+|[a-z0-9-]+/application/[a-z0-9-]+)(/[a-z0-9._-]+)+$` |

### SEC-03 CRITICAL: secretStoreRef 必须匹配 Vault 域

| 属性 | 值 |
|------|-----|
| 检测路径 | ExternalSecret 的 `spec.secretStoreRef` |
| 判定 | `kind` 非 `ClusterSecretStore` → CRITICAL；Ops 域使用 `vault-backend`，Builder 域 dev/staging 使用 `vault-builder-backend`，域不匹配 → CRITICAL |

### SEC-04 CRITICAL: ConfigMap 禁止敏感值

| 属性 | 值 |
|------|-----|
| 检测路径 | ConfigMap 的 `data` 字段的 key 和 value |
| 敏感 key 模式 | `password`、`secret`、`token`、`api_key`、`apikey`、`private_key`、`credentials`、`connection_string`、`access_key`、`secret_key`（不区分大小写） |
| 敏感 value 模式 | 以 `sk-`、`AKIA`、`ghp_`、`gho_`、`ghs_` 开头的值 |
| 判定 | key 或 value 匹配 → CRITICAL |
| 豁免 | value 为空字符串或占位符（如 `TODO`、`REPLACE_ME`、`changeme`）时降级为 WARNING |

### SEC-05 WARNING: ExternalSecret creationPolicy

| 属性 | 值 |
|------|-----|
| 检测路径 | `spec.target.creationPolicy` |
| 判定 | 非 `Owner` → WARNING |
| 原因 | Owner 策略确保 ExternalSecret 被删除时 Secret 一起清理 |

---

## Category 5: Workload / Kyverno baseline (DEP)

适用 Kind：`Rollout`、`StatefulSet`、`Deployment`；`Job` / `CronJob` 跳过探针规则，但仍检查 resources。

### DEP-01 CRITICAL: livenessProbe

| 属性 | 值 |
|------|-----|
| 检测路径 | `spec.template.spec.containers[*].livenessProbe` |
| 判定 | 不存在或未显式写 `periodSeconds` → CRITICAL |
| 豁免 | Job / CronJob |
| 原因 | Kyverno `require-probes` Enforce 会准入拦截；显式 `periodSeconds` 避免沿用不可控默认 |

### DEP-02 CRITICAL: readinessProbe

| 属性 | 值 |
|------|-----|
| 检测路径 | `spec.template.spec.containers[*].readinessProbe` |
| 判定 | 不存在或未显式写 `periodSeconds` → CRITICAL |
| 豁免 | Job / CronJob |
| 原因 | Kyverno `require-probes` Enforce 会准入拦截 |

### DEP-03 CRITICAL: resource requests/limits

| 属性 | 值 |
|------|-----|
| 检测路径 | `spec.template.spec.containers[*].resources.requests` 和 `.limits` |
| 判定 | containers 或 initContainers 任一缺少 requests/limits 的 cpu/memory → CRITICAL |
| 原因 | Kyverno `require-resources` Enforce 会准入拦截 |

### DEP-04 INFO: non-root

| 属性 | 值 |
|------|-----|
| 检测路径 | `spec.template.spec.securityContext.runAsNonRoot` 或 `spec.template.spec.containers[*].securityContext.runAsNonRoot` |
| 判定 | 未设置 → INFO |

### DEP-05 CRITICAL: 镜像标签禁止 latest

| 属性 | 值 |
|------|-----|
| 检测路径 | `spec.template.spec.containers[*].image` |
| 判定 | tag 为 `latest` 或无 tag → 检查 overlay `kustomization.yaml` 是否有 `images:` 段覆盖 |
| 逻辑 | base 用 `latest` 占位 + overlay 有 `images:` 覆盖 → PASS；否则 → CRITICAL |
| 原因 | Image Updater 通过 kustomize images 覆盖 tag |

### DEP-06 CRITICAL: imagePullSecrets

| 属性 | 值 |
|------|-----|
| 检测路径 | `spec.template.spec.imagePullSecrets` |
| 判定 | 不存在或不含 `harbor-registry-secret` → CRITICAL |
| 期望 | `name: harbor-registry-secret` |
| 原因 | Argo Rollouts / hook Job pod 不可靠继承 default ServiceAccount 的 imagePullSecrets；缺失时从私有 Harbor 匿名拉镜像导致 401 ImagePullBackOff |

### DEP-07 INFO: imagePullPolicy

| 属性 | 值 |
|------|-----|
| 检测路径 | `spec.template.spec.containers[*].imagePullPolicy` |
| 判定 | 非 `Always` → INFO（Image Updater 流程需要 Always 确保拉取最新镜像） |

### DEP-08 INFO: revisionHistoryLimit

| 属性 | 值 |
|------|-----|
| 检测路径 | `spec.revisionHistoryLimit` |
| 判定 | 未设置（默认 10）→ INFO |
| 推荐值 | 3~5，避免旧 ReplicaSet 堆积占用 etcd 存储 |
| 原因 | 默认保留 10 个历史版本的 ReplicaSet，大量 Deployment 时会增加 etcd 和 API Server 负担 |

---

## Category 6: 通用 K8s 安全 (K8S)

适用 Kind：`Deployment`、`StatefulSet`、`DaemonSet`、`Job`、`CronJob`

### K8S-01 CRITICAL: hostNetwork

| 检测路径 | `spec.template.spec.hostNetwork` |
|------|-----|
| 判定 | 值为 `true` → CRITICAL |

### K8S-02 CRITICAL: hostPID

| 检测路径 | `spec.template.spec.hostPID` |
|------|-----|
| 判定 | 值为 `true` → CRITICAL |

### K8S-03 CRITICAL: hostIPC

| 检测路径 | `spec.template.spec.hostIPC` |
|------|-----|
| 判定 | 值为 `true` → CRITICAL |

### K8S-04 CRITICAL: privileged

| 检测路径 | `spec.template.spec.containers[*].securityContext.privileged` 和 `initContainers[*]` |
|------|-----|
| 判定 | 值为 `true` → CRITICAL |

### K8S-05 CRITICAL: 危险 capabilities

| 检测路径 | `spec.template.spec.containers[*].securityContext.capabilities.add` |
|------|-----|
| 危险列表 | `SYS_ADMIN`、`NET_ADMIN`、`NET_RAW`、`SYS_PTRACE`、`SYS_MODULE`、`DAC_OVERRIDE` |
| 判定 | add 中包含危险 capability → CRITICAL |

### K8S-06 WARNING: default ServiceAccount

| 检测路径 | `spec.template.spec.serviceAccountName` |
|------|-----|
| 判定 | 值为 `default` 或未指定（隐式 default）→ WARNING |
| 豁免 | 应用不使用任何 AWS 资源时降级为 INFO |

---

## Category 7: ArgoCD Application 注册 (APP)

跨仓库验证，在 argocd-apps 仓库对应集群目录中搜索。

### APP-01 WARNING: Application 是否已注册

| 属性 | 值 |
|------|-----|
| 搜索 | argocd-apps 的集群目录中搜索文件名或 `metadata.name` 含 `{app-name}` 的 Application YAML |
| 判定 | 未找到 → WARNING（新应用首次部署时降级为 INFO） |

### APP-02 WARNING: source.path 匹配

| 属性 | 值 |
|------|-----|
| 检测路径 | Application 的 `spec.source.path` |
| 判定 | 不匹配应用实际 overlay 路径（如 `k8s/overlays/staging-us`）→ WARNING |

### APP-03 CRITICAL: destination.namespace 匹配且前缀式命名

| 属性 | 值 |
|------|-----|
| 检测路径 | Application 的 `spec.destination.namespace` |
| 对比 | 应用 overlay 的 `kustomization.yaml` 中的 `namespace` 字段 |
| 判定 | 二者不匹配，或业务 namespace 不是 `{phase}-{app}` 前缀式 → CRITICAL |
| 期望 | `staging-<app>`、`prod-<app>`、`pre-<app>`、`canary-<app>`、`test-<app>`、`dev-<app>` |
| 原因 | fluent-bit-base business-namespace gate 只放行 `default` 或指定前缀；后缀式 `<app>-staging` / 裸 `<app>` 会静默丢日志 |

### APP-04 WARNING: Image Updater 注解

| 属性 | 值 |
|------|-----|
| 检测注解 | `argocd-image-updater.argoproj.io/image-list`、`update-strategy`、`allow-tags` |
| 判定 | 业务 app opt-in Image Updater 时缺少任一注解 → WARNING；平台/Helm 自管 app 或开源 `base/` 固定镜像不报 |
| 期望 | `allow-tags: "regexp:^[a-f0-9]{7,40}$"`（commit SHA 格式） |

### APP-05 WARNING: Image registry 区域匹配

| 属性 | 值 |
|------|-----|
| 检测路径 | `image-list` 注解中的 registry 地址 |
| 判定 | 镜像 registry 与目标集群 Harbor 不匹配 → WARNING |
| 说明 | 优先使用本集群 Harbor。历史 `harbor-us-internal.addx.live` / `registry-harbor-cn.addx.live` 只作兼容，新增应用不要继续扩散 |

### APP-06 INFO: targetRevision 分支规范

| 属性 | 值 |
|------|-----|
| 检测路径 | `spec.source.targetRevision` |
| 期望 | staging 环境 → `staging`；pre 环境 → `pre`；prod 环境 → `main` |
| 判定 | 不匹配 → INFO |

---

## Category 8: Workload 命名与路由 (WRK)

### WRK-01 CRITICAL: Workload 名称必须等于 pod app label

| 属性 | 值 |
|------|-----|
| 检测 kind | Rollout / StatefulSet / Deployment |
| 检测路径 | `metadata.name`、`spec.template.metadata.labels.app` |
| 判定 | app label 缺失，或 workload 名与 app label 不一致 → CRITICAL |
| 期望 | workload 名是 `{app}`，不带环境后缀 |
| 原因 | fluent-bit 日志路由、Service selector、ArgoCD/Application 命名都依赖稳定 app label |

### WRK-02 CRITICAL: Service selector 必须选中 workload

| 属性 | 值 |
|------|-----|
| 检测 kind | Service |
| 检测路径 | `spec.selector` |
| 判定 | selector 在同扫描集内选不中任何 workload pod template labels → CRITICAL |
| 期望 | Service 名是 `{app}` 或 `{app}-<suffix>`，selector 至少包含 `app: {app}` 且能匹配 |
| 原因 | Service selector 选空不会 apply 报错，但流量会静默转发到空 endpoints |

### WRK-03 WARNING: 新应用避免原生 Deployment

| 属性 | 值 |
|------|-----|
| 检测 kind | Deployment |
| 判定 | 新应用使用原生 Deployment → WARNING，需人工确认是否命中平台豁免 |
| 期望 | 默认用 Argo Rollout；网关、Flink、单实例工具等按平台豁免清单处理 |

---

## 环境 → 集群目录映射表

| Overlay 名称 | crossplane-infra 目录 | argocd-apps 目录 | 区域 | Partition |
|-------------|----------------------|------------------|------|-----------|
| staging-us | aws-390709477306-us-staging | aws-390709477306-us-staging | US | aws |
| pre-us | aws-302571458622-us-prod | aws-302571458622-us-prod | US | aws |
| prod-us | aws-302571458622-us-prod | aws-302571458622-us-prod | US | aws |
| staging-eu | aws-390709477306-eu-staging | aws-390709477306-eu-staging | EU | aws |
| pre-eu | aws-740315635167-eu-prod | aws-740315635167-eu-prod | EU | aws |
| prod-eu | aws-740315635167-eu-prod | aws-740315635167-eu-prod | EU | aws |
| staging-cn | aws-801447536674-cn-staging | aws-801447536674-cn-staging | CN | aws-cn |
| pre-cn | aws-741924744516-cn-prod | aws-741924744516-cn-prod | CN | aws-cn |
| prod-cn | aws-741924744516-cn-prod | aws-741924744516-cn-prod | CN | aws-cn |
| *-data-us | aws-769494896000-us-data | aws-769494896000-us-data | US | aws |
| *-data-eu | aws-769494896000-eu-data | aws-769494896000-eu-data | EU | aws |
| dev-cn / *-cn-dev | aws-801447536674-cn-dev | aws-801447536674-cn-dev | CN | aws-cn |

## Harbor Registry 映射

| 集群区域 | Registry |
|---------|----------|
| US / EU / SG / staging / GKE | 本集群 Harbor（如 `harbor-00249-us-tech.addx.live`、`harbor-39070-us-staging.addx.live`、`harbor-a4xp-us-prod.addx.live`） |
| CN (EKS) | 本集群 Harbor（如 `harbor-74192-cn-prod.addx.live`、`harbor-80144-cn-staging.addx.live`），旧 `registry-harbor-cn.addx.live` 仅作兼容 |
| CN (TKE) | `harbor-cn.addx.live` |
