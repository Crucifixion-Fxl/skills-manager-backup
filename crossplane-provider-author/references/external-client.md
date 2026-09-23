# ExternalClient：Observe / Create / Update / Delete

> **何时读**：写 controller 实现时。

## 接口签名

```go
type ExternalClient interface {
    Observe(ctx, cr) (ExternalObservation, error)  // 每轮 reconcile 都调
    Create(ctx, cr)  (ExternalCreation, error)     // Observe 返回 ResourceExists=false 时调
    Update(ctx, cr)  (ExternalUpdate, error)       // Observe 返回 ResourceUpToDate=false 时调
    Delete(ctx, cr)  (ExternalDelete, error)       // CR 被删（finalizer 触发）时调
    Disconnect(ctx)  error                         // 关闭 client，通常 return nil
}
```

**Observe 是核心**：另外三个只是 Observe 的执行反作用。Observe 写错会无限 reconcile 或丢 external-name（外部资源被泄漏）。

## Observe 的决策矩阵

参考 [`internal/controller/datasource/datasource.go:228-262`](https://gitlab.addx.ai/DEV/provider-ninedata/-/blob/main/internal/controller/datasource/datasource.go#L228)：

```go
func (c *external) Observe(ctx, cr) (managed.ExternalObservation, error) {
    externalName := meta.GetExternalName(cr)

    var found *ExternalResource
    var err error
    if externalName != "" {
        found, err = c.service.GetByID(ctx, externalName)        // 已创建过 → 按 ID 查
    } else {
        found, err = c.service.GetByName(ctx, cr.Spec.ForProvider.Name)  // 第一次 → 按 name 查（adopt）
    }
    if err != nil {
        return managed.ExternalObservation{}, errors.Wrap(err, "observe")
    }

    if found == nil {
        return managed.ExternalObservation{ResourceExists: false}, nil  // 触发 Create
    }

    // adopt：第一次 observe 成功就 pin external-name
    if externalName == "" {
        meta.SetExternalName(cr, found.ID)
    }

    cr.Status.AtProvider.ID = found.ID
    cr.Status.SetConditions(xpv1.Available())

    return managed.ExternalObservation{
        ResourceExists:   true,
        ResourceUpToDate: found.Name == cr.Spec.ForProvider.Name,  // 只比对可更新字段
    }, nil
}
```

## 4 条铁律

1. **必须幂等** — 每轮 reconcile 都会跑，除了写 status 和 external-name 不能有副作用
2. **External-name 是主键** — 创建后 pin 在 annotation；后续 Observe 永远优先按 ID 查；**不要用 `metadata.name` 当外部主键**
3. **支持 adopt（import）** — external-name 为空时按 `spec.forProvider.name` 兜底；找到就 pin。这是 Crossplane 把"外部已存在资源"GitOps 化的唯一办法
4. **`ResourceUpToDate` 只比对 API 真支持更新的字段** — 比对了不可变字段会触发 Update → 外部 API 失败或 delete+recreate → external-name 失效，下游全部失联

## Create

```go
func (c *external) Create(ctx, cr) (managed.ExternalCreation, error) {
    cr.Status.SetConditions(xpv1.Creating())

    // 1. resolve 字段（处理 *FromSecretRef）
    host, err := c.resolveHost(ctx, cr)
    if err != nil { return managed.ExternalCreation{}, err }
    password, err := c.readPassword(ctx, cr)
    if err != nil { return managed.ExternalCreation{}, err }

    // 2. 调外部 API
    id, err := c.service.Create(ctx, &CreateRequest{
        Name:     cr.Spec.ForProvider.Name,
        Host:     host,
        Password: password,
        // ...
    })
    if err != nil { return managed.ExternalCreation{}, errors.Wrap(err, "create") }

    // 3. pin external-name
    meta.SetExternalName(cr, id)
    cr.Status.AtProvider.ID = id

    return managed.ExternalCreation{}, nil
}
```

## Update

只更新 API 真支持的字段：

```go
func (c *external) Update(ctx, cr) (managed.ExternalUpdate, error) {
    externalName := meta.GetExternalName(cr)
    if externalName == "" {
        return managed.ExternalUpdate{}, errors.New("cannot update: external-name is empty")
    }
    if err := c.service.Update(ctx, &UpdateRequest{
        ID:   externalName,
        Name: cr.Spec.ForProvider.Name,    // NineData 只支持改 name
    }); err != nil {
        return managed.ExternalUpdate{}, errors.Wrap(err, "update")
    }
    return managed.ExternalUpdate{}, nil
}
```

## Delete

```go
func (c *external) Delete(ctx, cr) (managed.ExternalDelete, error) {
    cr.Status.SetConditions(xpv1.Deleting())

    externalName := meta.GetExternalName(cr)
    if externalName == "" {
        return managed.ExternalDelete{}, nil  // 没 external-name 等于没建过，no-op
    }
    if err := c.service.Delete(ctx, externalName); err != nil {
        return managed.ExternalDelete{}, errors.Wrap(err, "delete")
    }
    return managed.ExternalDelete{}, nil
}
```

## Connect：从 ProviderConfig 拼出 ExternalClient

```go
type connector struct {
    kube         client.Client
    usage        *resource.ProviderConfigUsageTracker
    newServiceFn func(creds []byte) (*serviceClients, error)
}

func (c *connector) Connect(ctx, cr) (managed.TypedExternalClient[*v1alpha1.YourCR], error) {
    if err := c.usage.Track(ctx, cr); err != nil {
        return nil, errors.Wrap(err, "cannot track ProviderConfig usage")
    }

    // 同时支持 ProviderConfig 和 ClusterProviderConfig
    var cd apisv1alpha1.ProviderCredentials
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

    // 用 runtime 自带 extractor 读 Secret
    data, err := resource.CommonCredentialExtractor(ctx, cd.Source, c.kube, cd.CommonCredentialSelectors)
    if err != nil {
        return nil, err
    }

    svc, err := c.newServiceFn(data)
    if err != nil {
        return nil, err
    }

    return &external{service: svc, kube: c.kube}, nil
}
```

## Setup：注册 controller

```go
// 直接版本（不带 safe-start gate）
func Setup(mgr ctrl.Manager, o controller.Options) error {
    name := managed.ControllerName(v1alpha1.YourCRGroupKind)

    opts := []managed.ReconcilerOption{
        managed.WithTypedExternalConnector[*v1alpha1.YourCR](&connector{
            kube:         mgr.GetClient(),
            usage:        resource.NewProviderConfigUsageTracker(mgr.GetClient(), &apisv1alpha1.ProviderConfigUsage{}),
            newServiceFn: newServiceClient,
        }),
        managed.WithLogger(o.Logger.WithValues("controller", name)),
        managed.WithPollInterval(o.PollInterval),
        managed.WithRecorder(event.NewAPIRecorder(mgr.GetEventRecorderFor(name))),
    }
    // ... 加 ManagementPolicies / ChangeLogger / metrics ...

    r := managed.NewReconciler(mgr, resource.ManagedKind(v1alpha1.YourCRGroupVersionKind), opts...)
    return ctrl.NewControllerManagedBy(mgr).
        Named(name).
        WithOptions(o.ForControllerRuntime()).
        WithEventFilter(resource.DesiredStateChanged()).
        For(&v1alpha1.YourCR{}).
        Complete(ratelimiter.NewReconciler(name, r, o.GlobalRateLimiter))
}

// 带 safe-start gate 的版本（生产用这个）
func SetupGated(mgr ctrl.Manager, o controller.Options) error {
    o.Gate.Register(func() {
        if err := Setup(mgr, o); err != nil {
            panic(errors.Wrap(err, "cannot setup YourCR controller"))
        }
    }, v1alpha1.YourCRGroupVersionKind)
    return nil
}
```

详见 [packaging-deployment.md](packaging-deployment.md) 的 safe-start 小节。

## 多下游资源场景的 Observe

如果一个业务 CR 对应多个外部子资源（比如 Grafana folder + dashboard + alert），Observe 要挨个查，全在才算 `ResourceExists=true`，全一致才算 `ResourceUpToDate=true`。完整 walkthrough 见 [walkthrough-prom-grafana.md](walkthrough-prom-grafana.md)。
