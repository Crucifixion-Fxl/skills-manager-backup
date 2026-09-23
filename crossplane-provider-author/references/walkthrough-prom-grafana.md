# 实战 walkthrough：给 Prometheus / Grafana 写一个 onboarding provider

> **何时读**：想看完整端到端示例，或想把 SKILL.md 的规则代入实际项目时。

**目标**：让开发者写一段 CR 就能自动在 Prometheus 配好 scrape 规则 + 在 Grafana 建好 folder + dashboard + alert receiver，不用再手动进 UI 或写 PR。

## 第 0 步：判断该不该写

| 选项 | 适用条件 | 工时 |
|------|----------|------|
| 直接用官方 `grafana/crossplane-provider-grafana` | 资源类型够用（Folder/Dashboard/DataSource 齐全），只需 GitOps 化 | 1 天接入 |
| 用 Grafana provisioning YAML（sidecar/configmap） | 只管 dashboard，不想引入 CRD | 0.5 天 |
| **自己写 provider-<yourname>** | 需要业务定制逻辑（如"每个服务自动注册到对应 team 的 folder，带标准 alert receiver"），且需要接入公司内部系统（审批、Casdoor group、Vault path 推导） | 3-5 天首版 + 长期维护 |

第三种才适用本 walkthrough。下面假设你确认要自己写。

## 第 1 步：定义 CRD（API 设计先行）

先想清楚资源语义。Grafana/Prometheus 场景下**不要**直接把原生资源 1:1 映射（那你就等于重新发明 grafana-operator），而是做**业务封装 CR**：

```go
// apis/observability/v1alpha1/appobservability_types.go
type AppObservabilityParameters struct {
    // 业务身份
    AppName   string `json:"appName"`           // 服务名，用来派生 folder / dashboard 名
    Team      string `json:"team"`              // 对应 Grafana team / alert receiver / Casdoor group
    Env       string `json:"env"`               // staging/prod

    // Prometheus scrape 配置（可选，也支持 Service annotation 二选一）
    ScrapeEndpoint string `json:"scrapeEndpoint,omitempty"` // http://pod:8080/metrics
    ScrapeInterval string `json:"scrapeInterval,omitempty"`

    // Dashboard 来源：三选一
    DashboardJSONFromConfigMapRef *corev1.ConfigMapKeySelector `json:"dashboardJSONFromConfigMapRef,omitempty"`
    DashboardTemplateID           string                       `json:"dashboardTemplateId,omitempty"` // 平台预制模板
    DashboardGrafanaNetID         string                       `json:"dashboardGrafanaNetId,omitempty"`

    // Alert receiver（飞书群 webhook，从 Secret 读）
    AlertWebhookFromSecretRef *xpv1.SecretKeySelector `json:"alertWebhookFromSecretRef,omitempty"`

    // 可选：开启 SLO 模板
    EnableSLOTemplate bool `json:"enableSLOTemplate,omitempty"`
}

type AppObservabilityObservation struct {
    GrafanaFolderUID     string `json:"grafanaFolderUid,omitempty"`
    GrafanaDashboardUID  string `json:"grafanaDashboardUid,omitempty"`
    PrometheusRuleName   string `json:"prometheusRuleName,omitempty"`
    AlertChannelID       string `json:"alertChannelId,omitempty"`
}
```

**设计要点**：

- 一个业务 CR 对应多个下游资源（folder + dashboard + rule + receiver）— Observe 里要挨个查，有一个不在就返回 `ResourceUpToDate=false`
- 敏感字段（webhook URL）走 `*SecretRef`
- 允许 dashboard 来源多样（inline / 平台模板 / grafana.net 公共模板）

## 第 2 步：Client 包分层

```
internal/clients/
├── grafana/
│   ├── client.go          # http.Client + Authorization: Bearer <API token>
│   ├── folder.go          # POST /api/folders
│   ├── dashboard.go       # POST /api/dashboards/db
│   └── alert_channel.go   # POST /api/alert-notifications
└── prometheus/
    ├── client.go          # 读写 PrometheusRule CRD（不是 Prom API，是 Operator CRD）
    └── rule.go            # SetRule(ctx, RuleGroup)
```

**关键抉择**：Prometheus 部分**不要直接改 Prometheus config**。kube-prometheus-stack 已经提供 `PrometheusRule` CRD，你的 client 应该是"一个封装了 controller-runtime client 的 helper"去 CRUD `PrometheusRule` — 这等于嵌套一层 K8s 资源管理，符合 Kubernetes 原生思路，也避免和 kube-prometheus-stack 的 reloader 打架。

## 第 3 步：ExternalClient 主流程

```go
func (c *external) Observe(ctx context.Context, cr *v1alpha1.AppObservability) (managed.ExternalObservation, error) {
    folderTitle := derivedFolderTitle(cr)        // e.g. "prod-payment-service"
    ruleName    := derivedRuleName(cr)
    channelName := derivedChannelName(cr)

    // 分别查 4 个下游资源
    folder, err1   := c.grafana.GetFolderByTitle(ctx, folderTitle)
    dashboard, err2 := c.grafana.GetDashboardByUID(ctx, meta.GetExternalName(cr))
    rule, err3     := c.prom.GetRule(ctx, cr.Namespace, ruleName)
    channel, err4  := c.grafana.GetAlertChannelByName(ctx, channelName)

    // 收一下错（任何 retry-able 错误直接返回）
    if firstErr := firstNonNil(err1, err2, err3, err4); firstErr != nil {
        return managed.ExternalObservation{}, firstErr
    }

    // 全空 = 完全没建过
    if folder == nil && dashboard == nil && rule == nil && channel == nil {
        return managed.ExternalObservation{ResourceExists: false}, nil
    }

    // 写 status
    if folder != nil    { cr.Status.AtProvider.GrafanaFolderUID = folder.UID }
    if dashboard != nil { cr.Status.AtProvider.GrafanaDashboardUID = dashboard.UID }
    if rule != nil      { cr.Status.AtProvider.PrometheusRuleName = rule.Name }
    if channel != nil   { cr.Status.AtProvider.AlertChannelID = channel.ID }

    allExist := folder != nil && dashboard != nil && rule != nil && channel != nil
    upToDate := allExist &&
        dashboardChecksum(dashboard) == desiredChecksum(cr) &&
        prometheusRuleEqual(rule, desiredRule(cr)) &&
        channelEqual(channel, desiredChannel(cr))

    if allExist {
        cr.Status.SetConditions(xpv1.Available())
    }

    return managed.ExternalObservation{
        ResourceExists:   allExist,
        ResourceUpToDate: upToDate,
    }, nil
}

func (c *external) Create(ctx context.Context, cr *v1alpha1.AppObservability) (managed.ExternalCreation, error) {
    cr.Status.SetConditions(xpv1.Creating())

    // 按依赖顺序：folder → dashboard → rule → channel
    folder, err := c.grafana.CreateFolder(ctx, derivedFolderTitle(cr))
    if err != nil { return managed.ExternalCreation{}, err }

    dashJSON, err := c.resolveDashboardJSON(ctx, cr)
    if err != nil { return managed.ExternalCreation{}, err }
    dash, err := c.grafana.CreateDashboard(ctx, folder.UID, dashJSON)
    if err != nil { return managed.ExternalCreation{}, err }
    meta.SetExternalName(cr, dash.UID)  // pin 主键

    if err := c.prom.SetRule(ctx, cr.Namespace, derivedRuleName(cr), desiredRule(cr)); err != nil {
        return managed.ExternalCreation{}, err
    }

    webhook, err := c.readAlertWebhook(ctx, cr)
    if err != nil { return managed.ExternalCreation{}, err }
    _, err = c.grafana.CreateAlertChannel(ctx, derivedChannelName(cr), webhook)
    if err != nil { return managed.ExternalCreation{}, err }

    return managed.ExternalCreation{}, nil
}

// Update 跟 Create 类似，但每一步用 Upsert 而不是 Create
// Delete 反向：channel → rule → dashboard → folder
```

## 第 4 步：用户视角的 CR

```yaml
# Vault 里先写好 webhook：
# secret/staging/app/payment-service/alert.feishu_webhook = "https://open.feishu.cn/..."
---
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: payment-alert-webhook
  namespace: payment-staging
spec:
  refreshInterval: 1h
  secretStoreRef: { kind: ClusterSecretStore, name: vault-backend }
  target: { name: payment-alert-webhook }
  data:
    - secretKey: webhook
      remoteRef:
        key: secret/staging/app/payment-service/alert
        property: feishu_webhook
---
apiVersion: observability.crossplane.io/v1alpha1
kind: AppObservability
metadata:
  name: payment-service
  namespace: payment-staging
spec:
  forProvider:
    appName: payment-service
    team: payment-team
    env: staging
    scrapeEndpoint: http://payment-service:8080/metrics
    scrapeInterval: 30s
    dashboardTemplateId: golang-service-standard-v2
    alertWebhookFromSecretRef:
      name: payment-alert-webhook
      key: webhook
    enableSLOTemplate: true
  providerConfigRef:
    name: observability-default
    kind: ClusterProviderConfig
```

apply 后开发者 `kubectl describe appobservability payment-service` 应该看到：

```
Status:
  AtProvider:
    GrafanaFolderUID:     abc123
    GrafanaDashboardUID:  def456
    PrometheusRuleName:   payment-service-rules
    AlertChannelID:       channel-789
  Conditions:
    Type    Status  Reason
    Ready   True    Available
    Synced  True    ReconcileSuccess
```

## 第 5 步：部署

参考 [packaging-deployment.md](packaging-deployment.md) 的"三仓库 GitOps 部署"小节。简略：

- **Vault** 写凭据：
  - `secret/cicd/observability-onboarding/grafana-token` - Grafana API token
  - `secret/cicd/observability-onboarding/prometheus-kubeconfig` - 跨集群操作 PrometheusRule CRD 的 kubeconfig（也可以走 IRSA + 同集群 ServiceAccount）
- **k8s 仓库** 加 ArgoCD Application 指向 provider 镜像 + ProviderConfig + ExternalSecret，pod 用 `nat-egress` NodePool
- **argocd-apps 仓库** 加 root Application
- 验证：apply 上面那段 CR，去 Grafana UI 确认 folder/dashboard 已就绪

## 第 6 步：给业务方出一份 `examples/` 模板

参考 provider-ninedata 的 `examples/` 目录结构：

```
examples/
├── provider/
│   └── config.yaml           # ProviderConfig + 样例 Secret
└── observability/
    └── appobservability.yaml # 业务 CR 范例（带注释，让用户拷贝改字段即可）
```

业务方只需要复制 + 改 4 个字段（appName / team / env / scrapeEndpoint）就能用。

## 关键设计决策回顾

1. **业务封装 CR vs 原生 CR**：选业务封装。Crossplane 的核心价值就是"把多步操作压成一个声明式 API"，重新发明 grafana-operator 没意义。
2. **Prometheus 走 PrometheusRule CRD 而非直改 config**：避免和 kube-prometheus-stack 打架。
3. **Dashboard 多源（inline / 平台模板 / grafana.net）**：覆盖 80% 真实场景。
4. **Alert webhook 走 Secret + ExternalSecret**：飞书 webhook 是凭据级别敏感，不能落 Git。
5. **Observe 全资源对账**：任何一个子资源缺失或不一致就触发 Update，确保 GitOps 收敛。

## 后续 v0.2/v0.3 演进方向

- **v0.2**：加 Grafana team 自动绑定（按 `team` 字段从 Casdoor 查 group → 同步到 Grafana team）
- **v0.3**：SLO 模板（`enableSLOTemplate=true` 时自动加一组 burn-rate alert）
- **v0.4**：ServiceMonitor / PodMonitor 模式（替代 Prometheus scrape config，跟 prometheus-operator 风格一致）
- **v1.0**：稳定 API + conversion webhook
