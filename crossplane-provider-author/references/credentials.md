# 凭据 + ProviderConfig

> **何时读**：接入认证或写 ExternalSecret 时。

## 设计原则：一个 Secret 装一切

不要为每类凭据写一个 ProviderConfig 字段。Crossplane 标准做法是：`ProviderConfig.spec.credentials.secretRef` 指一个 Secret，Secret 里 `credentials.json` 用 JSON 塞所有你要的字段。这样 ExternalSecret 模板也能复用。

## controller 侧的 credsEnvelope

provider-ninedata 的实践（[`internal/controller/datasource/datasource.go:70-115`](https://gitlab.addx.ai/DEV/provider-ninedata/-/blob/main/internal/controller/datasource/datasource.go#L70)）：

```go
// 自己定义 Secret payload 的 JSON schema
type credsEnvelope struct {
    Endpoint        string `json:"endpoint"`            // API 地址（不同环境/区域不同）
    AccessKeyID     string `json:"access_key_id"`
    AccessKeySecret string `json:"access_key_secret"`

    // 可选扩展字段 — 例如旁路到某个 backing DB 的凭据
    MetaDBHost     string `json:"meta_db_host,omitempty"`
    MetaDBPassword string `json:"meta_db_password,omitempty"`
}

// Connect 里统一解析（接续 external-client.md 的 Connect 模式）
func newServiceClient(raw []byte) (*serviceClients, error) {
    var e credsEnvelope
    if err := json.Unmarshal(raw, &e); err != nil {
        return nil, errors.Wrap(err, "parse credentials JSON")
    }
    if e.AccessKeyID == "" || e.AccessKeySecret == "" {
        return nil, errors.New("credentials must include access_key_id and access_key_secret")
    }
    if e.Endpoint == "" {
        return nil, errors.New("credentials must include endpoint")
    }
    return &serviceClients{
        api: yourpkg.New(e.Endpoint, yourpkg.Credentials{
            AccessKeyID:     e.AccessKeyID,
            AccessKeySecret: e.AccessKeySecret,
        }),
    }, nil
}
```

## 同时支持 Namespaced + Cluster 两种 ProviderConfig

参考 [`internal/controller/config/config.go`](https://gitlab.addx.ai/DEV/provider-ninedata/-/blob/main/internal/controller/config/config.go)：

```go
// Setup 注册两种 PC reconciler
func Setup(mgr ctrl.Manager, o controller.Options) error {
    if err := setupNamespacedProviderConfig(mgr, o); err != nil {
        return err
    }
    return setupClusterProviderConfig(mgr, o)
}

func setupNamespacedProviderConfig(mgr ctrl.Manager, o controller.Options) error {
    name := providerconfig.ControllerName(v1alpha1.ProviderConfigGroupKind)
    of := resource.ProviderConfigKinds{
        Config:    v1alpha1.ProviderConfigGroupVersionKind,
        Usage:     v1alpha1.ProviderConfigUsageGroupVersionKind,
        UsageList: v1alpha1.ProviderConfigUsageListGroupVersionKind,
    }
    r := providerconfig.NewReconciler(mgr, of,
        providerconfig.WithLogger(o.Logger.WithValues("controller", name)),
        providerconfig.WithRecorder(event.NewAPIRecorder(mgr.GetEventRecorderFor(name))))
    return ctrl.NewControllerManagedBy(mgr).
        Named(name).
        WithOptions(o.ForControllerRuntime()).
        For(&v1alpha1.ProviderConfig{}).
        Watches(&v1alpha1.ProviderConfigUsage{}, &resource.EnqueueRequestForProviderConfig{}).
        Complete(ratelimiter.NewReconciler(name, r, o.GlobalRateLimiter))
}
// setupClusterProviderConfig 同理，把所有 Namespaced 换成 Cluster 版本
```

`ClusterProviderConfig` 全集群共享一份，适合 infra 级外部服务；`ProviderConfig` per-namespace 适合多租户场景。两种都暴露给用户选择。

## 用户视角：CR 怎么写

```yaml
# 1. ProviderConfig（指 Secret）
apiVersion: <name>.crossplane.io/v1alpha1
kind: ClusterProviderConfig
metadata:
  name: <name>-overseas
spec:
  credentials:
    source: Secret
    secretRef:
      namespace: crossplane-system
      name: <name>-api-credentials
      key: credentials.json    # 必须叫 credentials.json，对应 envelope JSON

---
# 2. 业务 CR 引用 PC
apiVersion: <name>.crossplane.io/v1alpha1
kind: YourResource
metadata:
  name: my-resource
  namespace: my-app
spec:
  forProvider:
    name: my-resource
    # ...
  providerConfigRef:
    name: <name>-overseas
    kind: ClusterProviderConfig    # 或 ProviderConfig
```

## 生产 Secret：必须由 ExternalSecret 从 Vault 拉

**永远不要 `kubectl create secret`**。模板：

```yaml
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: <provider>-credentials
  namespace: crossplane-system
spec:
  refreshInterval: 1h
  secretStoreRef:
    kind: ClusterSecretStore
    name: vault-backend           # Ops 集群；Builder 集群用 vault-builder-backend
  target:
    name: <provider>-credentials
    creationPolicy: Owner
  data:
    - secretKey: credentials.json    # 必须是 JSON 整段，对应 credsEnvelope
      remoteRef:
        key: secret/cicd/<provider>/api-credentials
        property: credentials.json
```

**Vault 路径规范**：

- Provider 是平台基础设施 → `secret/cicd/<provider>/<key>`（参考仓库 `AGENTS.md` / cicd-developer 路径决策树）
- Provider 是某个 app 自己用 → `secret/{env}/app/<app>/<key>`

Vault 内的值就是一段 JSON：

```json
{
  "endpoint": "https://api.example.com",
  "access_key_id": "AK...",
  "access_key_secret": "SK..."
}
```

## 何时不用 Vault → ExternalSecret

- 非常少。一定要本地开发 / kind 集群临时测：手动 `kubectl create secret generic` 一次性使用，但 README 必须明确这是临时方案
- CI 的 smoke test：用 env var 注入测试用临时凭据，跑完即销毁

## 踩坑

- **`credentials.json` 不是 JSON** → `json.Unmarshal` 静默 panic 之前会先报 `parse credentials`，但解释信息会丢；写 envelope 时永远校验所有必需字段
- **secretRef.namespace 写错** → 报错很隐晦（"cannot get credentials"）；ProviderConfig 是 cluster scope 但 secretRef 必须显式 namespace
- **Casing 不统一** → 外部系统 SDK 用 camelCase，envelope 用 snake_case 是常见做法（保持和外部系统 SDK 习惯一致），但要在 README 明确告诉用户 JSON 字段名
- **Secret 数据没被 base64 解码** → 用 `stringData:` 而不是 `data:`，避免手动 base64
