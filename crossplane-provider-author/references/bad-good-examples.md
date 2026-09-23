# Bad / Good 对比集

> **何时读**：code review 前自检；review 别人的 provider PR。

下面所有对比都是从 provider-ninedata 真实 review 抽象出来的。

## 1. Observe 的 adopt 逻辑

### ❌ Bad Example

```go
func (c *external) Observe(ctx, cr) (managed.ExternalObservation, error) {
    externalName := meta.GetExternalName(cr)
    if externalName == "" {
        // 第一次 reconcile：直接返回不存在，让 runtime 去 Create
        return managed.ExternalObservation{ResourceExists: false}, nil
    }
    found, err := c.service.GetByID(ctx, externalName)
    // ...
}
```

**问题**：用户如果手写一个 CR 想"纳管"已经存在于外部系统的资源，这段代码会再创建一份，造成重名冲突或资源重复。

### ✅ Good Example

```go
func (c *external) Observe(ctx, cr) (managed.ExternalObservation, error) {
    externalName := meta.GetExternalName(cr)
    var found *R
    var err error
    if externalName != "" {
        found, err = c.service.GetByID(ctx, externalName)
    } else {
        // adopt：按 spec.forProvider.name 兜底查外部，存在就 pin external-name
        found, err = c.service.GetByName(ctx, cr.Spec.ForProvider.Name)
    }
    if err != nil { return managed.ExternalObservation{}, err }
    if found == nil {
        return managed.ExternalObservation{ResourceExists: false}, nil
    }
    if externalName == "" {
        meta.SetExternalName(cr, found.ID)  // pin 主键
    }
    // ...
}
```

## 2. 凭据字段设计

### ❌ Bad Example

```go
type ProviderConfigSpec struct {
    Endpoint        string `json:"endpoint"`
    AccessKeyID     string `json:"accessKeyId"`      // 写明文
    AccessKeySecret string `json:"accessKeySecret"`  // 写明文
}
```

**问题**：凭据直接落在 CR `spec` 里，`kubectl get providerconfig -o yaml` 即可泄漏；ArgoCD 会把 secret 落盘到 Git。

### ✅ Good Example

```go
type ProviderConfigSpec struct {
    Credentials ProviderCredentials `json:"credentials"`
}
type ProviderCredentials struct {
    Source xpv1.CredentialsSource `json:"source"`      // "Secret" / "Environment" / ...
    xpv1.CommonCredentialSelectors `json:",inline"`    // secretRef: {namespace, name, key}
}
// Secret 里 credentials.json 字段用 JSON 塞所有凭据：
// { "endpoint": "...", "access_key_id": "...", "access_key_secret": "..." }
```

凭据由 ExternalSecret 从 Vault 拉取，永远不落 Git。

## 3. 字段 drift 检测

### ❌ Bad Example

```go
upToDate := found.Name == cr.Spec.ForProvider.Name &&
            found.Host == cr.Spec.ForProvider.Host &&
            found.Port == *cr.Spec.ForProvider.Port    // Host/Port 不可变但也被对比
return managed.ExternalObservation{ResourceExists: true, ResourceUpToDate: upToDate}, nil
```

**问题**：NineData API 只支持改 name，其他字段变了会触发 Update → API 失败或 delete+recreate，外部 ID 变更，`crossplane.io/external-name` 失效，后续资源全部失联。

### ✅ Good Example

```go
// 只比对 API 真正支持更新的字段
upToDate := found.Name == cr.Spec.ForProvider.Name
if found.Host != cr.Spec.ForProvider.Host {
    // 不可变字段的 drift 检测放在 Observe 里日志/告警，但不自动 Update
    // TODO: emit event — tell user to delete+redeclare if they need this change
}
return managed.ExternalObservation{ResourceExists: true, ResourceUpToDate: upToDate}, nil
```

## 4. 字段同时支持 literal + Secret 引用

### ❌ Bad Example

```go
type DataSourceParameters struct {
    Host     string `json:"host"`     // 只能写死字符串
    Username string `json:"username"`
    Password string `json:"password"` // 明文 + 不能引用上游 CR 产出的 Secret
}
```

**问题**：

- Password 明文 = 凭据落 Git
- Host/Username 没法引用上游 Crossplane 创建的 RDS connection secret，组合能力被切断

### ✅ Good Example

```go
type DataSourceParameters struct {
    Host              string                  `json:"host,omitempty"`
    HostFromSecretRef *xpv1.SecretKeySelector `json:"hostFromSecretRef,omitempty"`

    Username              string                  `json:"username,omitempty"`
    UsernameFromSecretRef *xpv1.SecretKeySelector `json:"usernameFromSecretRef,omitempty"`

    PasswordSecretRef *xpv1.SecretKeySelector `json:"passwordSecretRef,omitempty"` // 只准从 Secret 读
}

// controller 侧 helper
func (c *external) resolveHost(ctx, cr) (string, error) {
    if ref := cr.Spec.ForProvider.HostFromSecretRef; ref != nil && ref.Name != "" {
        return c.readSecretKey(ctx, cr, ref)
    }
    if cr.Spec.ForProvider.Host == "" {
        return "", errors.New("either host or hostFromSecretRef must be set")
    }
    return cr.Spec.ForProvider.Host, nil
}
```

## 5. ProviderConfig 只支持一种 kind

### ❌ Bad Example

```go
// connector.Connect 里只处理 Namespaced
pc := &apisv1alpha1.ProviderConfig{}
if err := c.kube.Get(ctx, types.NamespacedName{Name: ref.Name, Namespace: cr.GetNamespace()}, pc); err != nil {
    return nil, err
}
```

**问题**：用户写 `kind: ClusterProviderConfig` 时 Connect 报 "no kind ProviderConfig found"，新手很容易踩。

### ✅ Good Example

```go
ref := cr.GetProviderConfigReference()
switch ref.Kind {
case "ProviderConfig":
    pc := &apisv1alpha1.ProviderConfig{}
    if err := c.kube.Get(ctx, types.NamespacedName{Name: ref.Name, Namespace: cr.GetNamespace()}, pc); err != nil {
        return nil, err
    }
    cd = pc.Spec.Credentials
case "ClusterProviderConfig":
    cpc := &apisv1alpha1.ClusterProviderConfig{}
    if err := c.kube.Get(ctx, types.NamespacedName{Name: ref.Name}, cpc); err != nil {
        return nil, err
    }
    cd = cpc.Spec.Credentials
default:
    return nil, errors.Errorf("unsupported provider config kind: %s", ref.Kind)
}
```

## 6. Observe 副作用

### ❌ Bad Example

```go
func (c *external) Observe(ctx, cr) (managed.ExternalObservation, error) {
    found, _ := c.service.GetByID(ctx, externalName)
    if found != nil && found.Name != cr.Spec.ForProvider.Name {
        // 顺手就改了
        c.service.Update(ctx, &UpdateRequest{ID: externalName, Name: cr.Spec.ForProvider.Name})
    }
    return managed.ExternalObservation{ResourceExists: true, ResourceUpToDate: true}, nil
}
```

**问题**：Observe 里执行了写操作。如果失败你只能吞掉错误（因为 ResourceUpToDate=true 意味着 runtime 不会再调 Update），用户永远不知道发生过什么。

### ✅ Good Example

```go
func (c *external) Observe(ctx, cr) (managed.ExternalObservation, error) {
    found, err := c.service.GetByID(ctx, externalName)
    if err != nil { return managed.ExternalObservation{}, err }
    return managed.ExternalObservation{
        ResourceExists:   true,
        ResourceUpToDate: found.Name == cr.Spec.ForProvider.Name,
    }, nil
}
// runtime 自动调 Update
```

Observe 只读 + 写 status；写操作放 Create/Update/Delete。

## 7. SetupGated vs Setup

### ❌ Bad Example

```go
// internal/controller/register.go
func SetupGated(mgr ctrl.Manager, o controller.Options) error {
    return datasource.Setup(mgr, o)   // 直接调 Setup，没经过 gate
}
```

**问题**：CRD 没装时 controller 启动会 panic，整个 provider CrashLoopBackoff，连其他已部署 CRD 也无法 reconcile。

### ✅ Good Example

```go
// internal/controller/<resource>/<resource>.go
func SetupGated(mgr ctrl.Manager, o controller.Options) error {
    o.Gate.Register(func() {
        if err := Setup(mgr, o); err != nil {
            panic(errors.Wrap(err, "cannot setup DataSource controller"))
        }
    }, v1alpha1.DataSourceGroupVersionKind)
    return nil
}

// internal/controller/register.go
func SetupGated(mgr ctrl.Manager, o controller.Options) error {
    for _, setup := range []func(ctrl.Manager, controller.Options) error{
        config.Setup,
        datasource.SetupGated,    // ← Gated
    } {
        if err := setup(mgr, o); err != nil { return err }
    }
    return nil
}
```

加上 `package/crossplane.yaml` 的 `capabilities: [safe-start]`。
