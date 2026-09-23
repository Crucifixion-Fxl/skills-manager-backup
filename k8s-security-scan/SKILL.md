---
name: k8s-security-scan
description: 扫描应用 k8s/ 目录的安全合规性，跨仓库验证 Crossplane 权限和 ArgoCD 注册。当 MR 修改 k8s/ YAML 文件时在 CI pipeline 中触发。
---

# k8s-security-scan

扫描应用仓库 `k8s/base/` 和 `k8s/overlays/*/` 中的 Kubernetes YAML 配置，跨仓库验证 Crossplane 权限申请和 ArgoCD Application 注册，输出结构化安全合规报告。

> 本 Skill 用于 MR pipeline 阶段的"左移"安全检查。不适用于基础设施仓库（k8s、argocd-apps、crossplane-infra 本身）。

> 规则源：`cicd-developer` 的 Review / Scan 模式、`references/data/hard-rules.yaml` 和 validators 是部署合规 SSOT。若本 skill 与 `cicd-developer` 冲突，以 `cicd-developer` 为准并回修本 skill。能读取 `~/.codex/skills/cicd-developer/validators/validate.sh` 时，先对待扫文件跑它；validator PASS 仍要继续做静态核查。

## 执行流程

### Step 1: 范围检测

确定扫描范围：

1. **App 名称**：从 `k8s/base/deployment.yaml` 的 `metadata.name` 或仓库名推断
2. **目标环境**：从 `k8s/overlays/` 子目录名确定（staging-us、prod-cn 等）
3. **云平台**：从 overlay 名称推断（`*-cn` → AWS China，`*-us/*-eu` → AWS Global）
4. **变更范围**：如有 MR diff，聚焦变更文件；否则全量扫描

### Step 2: 跨仓库验证源

CI pipeline 中克隆两个仓库用于跨仓库验证；Codex 本地执行时优先使用 `~/Project/A4x/{crossplane-infra,argocd-apps}` 已有 checkout，不要重复 clone 到 `/tmp`。

```bash
git clone --depth 1 https://gitlab-ci-token:${CI_JOB_TOKEN}@gitlab.addx.ai/DEV/crossplane-infra.git /tmp/crossplane-infra
git clone --depth 1 https://gitlab-ci-token:${CI_JOB_TOKEN}@gitlab.addx.ai/DEV/argocd-apps.git /tmp/argocd-apps
```

**环境 → 集群目录映射**（用于定位跨仓库文件）：

| Overlay 环境 | crossplane-infra 搜索目录 | argocd-apps 搜索目录 |
|-------------|--------------------------|---------------------|
| staging-us | `aws-390709477306-us-staging/` | 同名目录 |
| pre-us / prod-us | `aws-302571458622-us-prod/` | 同名目录 |
| staging-eu | `aws-390709477306-eu-staging/` | 同名目录 |
| pre-eu / prod-eu | `aws-740315635167-eu-prod/` | 同名目录 |
| staging-cn | `aws-801447536674-cn-staging/` | 同名目录 |
| dev-cn | `aws-801447536674-cn-dev/` | 同名目录 |
| pre-cn / prod-cn | `aws-741924744516-cn-prod/` | 同名目录 |

### Step 3: YAML 扫描

解析 `k8s/base/` 和每个 `k8s/overlays/*/` 中的所有 YAML 文件。按 Kind 分类：

- **Rollout / StatefulSet / Deployment** → Workload 命名、Kyverno baseline、DEP/K8S 规则；新应用 Deployment 只作为需确认项
- **Ingress** → ING 规则
- **ExternalSecret / PushSecret** → SEC 规则
- **ServiceAccount** → IRSA 规则
- **ConfigMap** → SEC-04 敏感值检测
- **Secret** → SEC-01 硬编码检测
- **Bucket / Instance / Cluster / Function 等 Crossplane 资源** → CRX 规则

### Step 4: 执行检查规则

7 大类检查，每条规则有唯一 ID 和严重级别。详细规则见 [references/check-rules.md](references/check-rules.md)。

**严重级别**：

| 级别 | 效果 | 示例 |
|------|------|------|
| CRITICAL | Pipeline 失败，必须修复 | 缺少 kind、ConfigMap 有密码、hostNetwork |
| WARNING | 通过但提示，建议修复 | 缺少探针、公网 Ingress 未标注、缺少标签 |
| INFO | 建议性 | non-root、ARN partition |

**规则总览**：

| 类别 | ID 前缀 | 规则数 | 检查范围 |
|------|---------|--------|----------|
| Crossplane 资源治理 | CRX | 6 | providerConfigRef、权限最小化、标签 |
| Ingress 安全 | ING | 8 | 公网暴露、SG 管控、Kyverno 注入冲突 |
| IRSA / ServiceAccount | IRSA | 4 | role-arn 存在性、命名规范、permissionsBoundary |
| 密钥管理 | SEC | 5 | 禁止硬编码、Vault 路径规范 |
| Workload / Kyverno baseline | DEP / WRK | 12 | 探针、资源、镜像标签、imagePullSecrets、命名/selector |
| 通用 K8s 安全 | K8S | 6 | hostNetwork、privileged、capabilities |
| ArgoCD Application | APP | 6 | 注册状态、路径匹配、Image Updater 配置 |
| 仓库边界 | BND | 3 | Application、IAM/ProviderConfig/WAF/NineData ProviderConfig/SecurityGroupIngressRule 是否放错仓 |

### Step 5: 跨仓库验证

**crossplane-infra 验证**（CRX-03、IRSA-01~03）：

1. 在 crossplane-infra 对应集群目录下搜索 `ClusterProviderConfig`，验证 `metadata.name` 匹配应用的 `providerConfigRef.name`
2. 搜索 IRSA Role（`crossplane-app-{app}-irsa`），验证 `permissionsBoundary` 存在
3. 检查 RolePolicy 的 Resource 字段是否遵循最小权限
4. 检查应用仓 overlay 是否误放 IAM / ProviderConfig / WAFv2 / NineData ProviderConfig / SecurityGroupIngressRule 等中心化资源；app-owned 数据面 CR（RDS/Aurora/ElastiCache/S3/CloudFront Distribution+OAC）允许留在应用 overlay

**argocd-apps 验证**（APP-01~06）：

1. 在 argocd-apps 对应集群目录下搜索以 app 名称开头的 Application YAML
2. 验证 `source.path` 匹配应用实际 overlay 路径
3. 验证 `destination.namespace` 匹配 kustomization.yaml 的 namespace
4. 验证 Image Updater 注解完整性和 registry 区域匹配

### Step 6: 生成报告

输出结构化 Markdown 报告：

```markdown
# K8s Security Scan Report

**App**: {name} | **Verdict**: PASS / FAIL / WARN | **Date**: {date}
**Environments**: staging-cn, prod-us

## Summary

| Severity | Count |
|----------|-------|
| CRITICAL | 0 |
| WARNING  | 3 |
| INFO     | 1 |

## CRITICAL (Pipeline Blocks)

| ID | File | Rule | Finding | Fix |
|----|------|------|---------|-----|
| (none) | | | | |

## WARNING (Advisory)

| ID | File | Rule | Finding | Fix |
|----|------|------|---------|-----|
| CRX-05 | overlays/staging-cn/s3-bucket.yaml | 缺少标签 | 未设置 managed-by tag | 添加 `managed-by: crossplane` |
| DEP-01 | base/deployment.yaml | 缺少 livenessProbe | 未配置存活探针 | 添加 httpGet /health 探针 |
| APP-04 | (argocd-apps) | Image Updater 未配置 | factory-service-staging-cn 缺少 image-list 注解 | 参照模板添加注解 |

## INFO (Suggestion)

| ID | File | Rule | Finding | Fix |
|----|------|------|---------|-----|
| DEP-04 | base/deployment.yaml | 建议 non-root | 未设置 runAsNonRoot | 添加 securityContext |

## Cross-Repo Validation

| Check | Repo | Status | Detail |
|-------|------|--------|--------|
| ClusterProviderConfig exists | crossplane-infra | PASS | `factory-service` found in aws-589899215075-cn-tech-service/ |
| IRSA Role exists | crossplane-infra | PASS | `crossplane-app-factory-service-irsa` found |
| permissionsBoundary set | crossplane-infra | PASS | CrossplaneAppBoundary attached |
| Application registered | argocd-apps | PASS | factory-service-staging-cn found |
| source.path matches | argocd-apps | PASS | k8s/overlays/staging-cn |
```

**Verdict 判定逻辑**：
- 有 CRITICAL → `FAIL`
- 无 CRITICAL 有 WARNING → `WARN`
- 全部通过 → `PASS`

## Rules

### 红线（CRITICAL 规则）

1. **providerConfigRef 必须有 kind** (CRX-01) — 不写 kind 会导致 Crossplane 静默失败
2. **providerConfigRef.name 禁止 default** (CRX-02) — Kyverno 会在运行时拦截，提前发现
3. **host 必须 *.addx.live** (ING-03) — 公司域名管控
4. **禁止手写 certificate-arn** (ING-05) — ALB 自动发现证书
5. **禁止无说明手写 security-groups** (ING-06) — 优先用 `ingress.addx.io/sg` 标签；只有批准的自定义 SG/跨账号场景可例外
6. **禁止硬编码 K8s Secret** (SEC-01) — 必须用 ExternalSecret + Vault
7. **ConfigMap 禁止敏感值** (SEC-04) — password/secret/token/api_key 等
8. **secretStoreRef 必须匹配域** (SEC-03) — Ops 域用 `vault-backend`；Builder 域 staging/dev 用 `vault-builder-backend`
9. **禁止 hostNetwork/PID/IPC** (K8S-01~03) — 容器隔离
10. **禁止 privileged 容器** (K8S-04)
11. **禁止危险 capabilities** (K8S-05) — SYS_ADMIN, NET_ADMIN 等
12. **Kyverno baseline 必须满足** (#22) — containers/initContainers 都有 requests+limits 的 cpu/memory；非 Job/CronJob workload 有 liveness+readiness 且显式 `periodSeconds`
13. **Pod template 必须显式 `imagePullSecrets: [{name: harbor-registry-secret}]`** (DEP-06/#24) — Argo Rollouts/Hook Job 不可靠继承 default SA imagePullSecrets
14. **业务 namespace 必须前缀式 `{phase}-{app}`** (APP-03/#31) — `staging-foo` / `prod-foo`，不是 `foo-staging` 或裸 `foo`
15. **workload/service 命名必须可路由** (WRK-01/#32) — Rollout/StatefulSet 名等于 pod `app` label；Service selector 必须选中 workload
16. **应用仓不得放中心化边界资源** (BND-*) — IAM/ProviderConfig/WAFv2/NineData ProviderConfig/SecurityGroupIngressRule/ArgoCD Application 放错仓即阻断

### 豁免规则

| 场景 | 条件 | 规则 ID |
|------|------|---------|
| Base workload `image: app:latest` | 占位符，Image Updater 会覆盖；只在 overlay kustomization.yaml 无 images 段时告警 | DEP-05 |
| ElastiCache/CloudFront IAM `Resource: "*"` | AWS ARN 限制无法细化，需在 RolePolicy 中有注释说明 | CRX-04 |
| Ingress `scheme: internal` | 内网服务显式指定 internal 是允许的 | ING-08 |
| 应用不使用 Crossplane | overlays 中无 Crossplane 资源时跳过 CRX 和 IRSA 检查 | CRX-*, IRSA-* |
| 应用未在 argocd-apps 注册 | 新应用首次部署时 APP 类警告降级为 INFO | APP-* |
| 平台/Helm 自管 Application | 没有 Image Updater 注解时不报 APP-04；Image Updater 只对业务 app opt-in 检查 | APP-04 |

## Examples

### Bad

#### 1. Crossplane 缺少 kind（CRX-01 CRITICAL）

```yaml
providerConfigRef:
  name: my-app
  # 缺少 kind: ClusterProviderConfig → Crossplane 静默失败，不会报错
```

#### 2. ConfigMap 包含敏感值（SEC-04 CRITICAL）

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: my-app-config
data:
  DATABASE_PASSWORD: "mypassword123"    # SEC-04: 密码不应出现在 ConfigMap
  API_KEY: "sk-abc123"                  # SEC-04: API Key 不应出现在 ConfigMap
```

#### 3. Ingress 无说明手写安全组（ING-06 CRITICAL）

```yaml
metadata:
  annotations:
    alb.ingress.kubernetes.io/security-groups: sg-xxx  # ING-06: 无批准说明时禁止
    alb.ingress.kubernetes.io/certificate-arn: arn:aws:acm:...  # ING-05: 自动发现
    alb.ingress.kubernetes.io/scheme: internet-facing   # ING-08: Kyverno 注入
```

#### 4. 硬编码 Secret（SEC-01 CRITICAL）

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: my-app-secret
type: Opaque
data:
  DB_PASSWORD: cGFzc3dvcmQ=  # SEC-01: 必须用 ExternalSecret + Vault
```

### Good

#### 1. 正确的 providerConfigRef

```yaml
providerConfigRef:
  name: my-app
  kind: ClusterProviderConfig    # CRX-01: 必须指定 kind
```

#### 2. 敏感值通过 ExternalSecret 管理

```yaml
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: my-app-secret
spec:
  secretStoreRef:
    name: vault-builder-backend  # SEC-03: Builder 域 staging/dev 使用 vault-builder-backend；Ops 域使用 vault-backend
    kind: ClusterSecretStore
  target:
    name: my-app-secret
    creationPolicy: Owner        # SEC-05: Owner 策略
  data:
    - secretKey: DATABASE_PASSWORD
      remoteRef:
        key: staging/app/my-app/database  # SEC-02: {env}/app/{app}/{key}
        property: DATABASE_PASSWORD
```

#### 3. 最简 Ingress（Kyverno 自动注入）

```yaml
metadata:
  name: my-app
  labels:
    ingress.addx.io/sg: office          # Kyverno 自动注入 office SG
  annotations:
    alb.ingress.kubernetes.io/healthcheck-path: /health  # ING-04: 必须指定
spec:
  ingressClassName: alb
  rules:
    - host: my-app.addx.live            # ING-03: *.addx.live 域名
```

#### 4. 完整的 workload 安全配置

```yaml
spec:
  template:
    spec:
      imagePullSecrets:
        - name: harbor-registry-secret  # DEP-06 / hard rule #24
      serviceAccountName: my-app        # K8S-06: 非 default
      containers:
        - name: my-app
          image: my-app:latest          # DEP-05: 占位符，overlay images 覆盖
          resources:                    # DEP-03
            requests:
              cpu: 100m
              memory: 256Mi
            limits:
              cpu: "1"
              memory: 1Gi
          livenessProbe:                # DEP-01
            httpGet:
              path: /health
              port: 8080
            periodSeconds: 10
          readinessProbe:               # DEP-02
            httpGet:
              path: /health
              port: 8080
            periodSeconds: 10
```

## References

- [references/check-rules.md](references/check-rules.md) — 完整规则目录（含 YAML 检测路径、边界情况、平台差异）
- [IRSA 自助申请指南](https://gitlab.addx.ai/DEV/crossplane-infra/-/blob/main/docs/irsa-template.md) — IRSA Role 模板和集群参数
- [CICD 开发者指南](https://a4x-paas.feishu.cn/wiki/KEKqwe7e7ieK1CkWG0LcEbU1n4d) — 完整部署流程文档
