# CRD 类型设计

> **何时读**：每加一个新资源类型时。

CRD 的 Go 类型必须遵循 Crossplane 三段式约定：`<Resource>Parameters`（用户可写）+ `<Resource>Observation`（外部反填）+ `Spec`/`Status`。

## 标准模板

参考 [`apis/ninedata/v1alpha1/datasource_types.go`](https://gitlab.addx.ai/DEV/provider-ninedata/-/blob/main/apis/ninedata/v1alpha1/datasource_types.go)：

```go
// DataSourceParameters = 用户可写字段（spec.forProvider）
type DataSourceParameters struct {
    DatasourceType string `json:"datasourceType"`
    Name           string `json:"name"`

    // 可选：支持 literal 或 SecretKeySelector 二选一
    Host              string                    `json:"host,omitempty"`
    HostFromSecretRef *xpv1.SecretKeySelector   `json:"hostFromSecretRef,omitempty"`

    // 端口同样支持 Secret 引用
    Port              *int32                    `json:"port,omitempty"`
    PortFromSecretRef *xpv1.SecretKeySelector   `json:"portFromSecretRef,omitempty"`

    // 敏感字段只能从 Secret 读
    PasswordSecretRef *xpv1.SecretKeySelector `json:"passwordSecretRef,omitempty"`

    // 不可变字段加注释提醒维护者
    EnvID    string `json:"envId"`
    RegionID string `json:"regionId"`
}

// DataSourceObservation = 外部系统返回、反填给用户看的字段（status.atProvider）
type DataSourceObservation struct {
    DatasourceID string `json:"datasourceId,omitempty"`  // 也写到 external-name annotation
    OrgID        string `json:"orgId,omitempty"`
}

type DataSourceSpec struct {
    xpv2.ManagedResourceSpec `json:",inline"`             // v2: 嵌入含 ProviderConfigRef + ManagementPolicies
    ForProvider              DataSourceParameters `json:"forProvider"`
}

type DataSourceStatus struct {
    xpv1.ResourceStatus `json:",inline"`                  // Conditions(Ready/Synced)
    AtProvider          DataSourceObservation `json:"atProvider,omitempty"`
}

// +kubebuilder:object:root=true
// +kubebuilder:printcolumn:name="READY",type="string",JSONPath=".status.conditions[?(@.type=='Ready')].status"
// +kubebuilder:printcolumn:name="SYNCED",type="string",JSONPath=".status.conditions[?(@.type=='Synced')].status"
// +kubebuilder:printcolumn:name="EXTERNAL-NAME",type="string",JSONPath=".metadata.annotations.crossplane\\.io/external-name"
// +kubebuilder:printcolumn:name="HOST",type="string",JSONPath=".spec.forProvider.host"
// +kubebuilder:printcolumn:name="AGE",type="date",JSONPath=".metadata.creationTimestamp"
// +kubebuilder:subresource:status
// +kubebuilder:resource:scope=Namespaced,categories={crossplane,managed,<name>}
type DataSource struct {
    metav1.TypeMeta   `json:",inline"`
    metav1.ObjectMeta `json:"metadata,omitempty"`
    Spec   DataSourceSpec   `json:"spec"`
    Status DataSourceStatus `json:"status,omitempty"`
}
```

## 6 条铁律

1. **三段式必须分开**（Parameters / Observation / Spec+Status） — Crossplane runtime 靠反射读这个分离来跑 managed policy（`Create`/`Update`/`Delete`/`Observe`）和 late-init；合并在一个 struct 里会让 drift 检测失效
2. **敏感字段必须 `*xpv1.SecretKeySelector`**（password / token / apikey） — 不要收 literal string，code review 会打回
3. **外部 ID 不写进 spec**，只写到 `status.atProvider`，并同时 mirror 到 `crossplane.io/external-name` annotation
4. **`<Field>FromSecretRef` 双轨**：任何"运行时才知道值、且对方可能是另一个 CR"的字段，除了 literal 以外都要支持引用 Secret（见下"FromSecretRef 模式"）
5. **不可变字段加注释警示** — provider-ninedata 的 DataSource 只有 `name` 可更新，其他字段变化会触发 delete+recreate；我们在 Observe 里只对比 name，不可变字段 drift 不自动处理
6. **Scope 默认 Namespaced**（绝大多数场景）— Cluster scope 留给真正全局的基础设施

## FromSecretRef 模式

很多时候 CR 的某些字段**在 apply 时还不知道**（比如把 Crossplane 创建的 RDS 注册为 DataSource，那时 RDS endpoint 还没有）。provider-ninedata v0.1.2 加了 `hostFromSecretRef` / `portFromSecretRef` / `usernameFromSecretRef` 就是解决这个。

**用户视角的 CR**：

```yaml
spec:
  forProvider:
    hostFromSecretRef:      # 上游 RDS CR 生成的 connection secret
      name: my-rds-connection
      key: endpoint
    portFromSecretRef:
      name: my-rds-connection
      key: port
    passwordSecretRef:
      name: my-rds-connection
      key: password
```

**控制器侧的 resolve helper**：

```go
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

不支持 `*FromSecretRef` 就等于切断了 provider 和其他 provider 的协作链 — Crossplane 的组合能力依赖于"下游 CR 能引用上游 CR 产出的 Secret"。

## kubebuilder 标记速查

| 标记 | 作用 |
|------|------|
| `+kubebuilder:validation:Enum=A;B;C` | 字段值白名单 |
| `+kubebuilder:default=foo` | 字段默认值 |
| `+kubebuilder:validation:Required` | 字段必填 |
| `+kubebuilder:printcolumn:name=...` | `kubectl get` 时多一列 |
| `+kubebuilder:subresource:status` | status 走子资源 update（必须有） |
| `+kubebuilder:resource:scope=Namespaced` | 资源 scope |
| `+kubebuilder:resource:categories={crossplane,managed,<name>}` | `kubectl get crossplane/managed/<name>` 可选别名 |

## groupversion_info.go

每个 API group/version 一份，定义 `Group` + `Version` + `SchemeBuilder`：

```go
// +kubebuilder:object:generate=true
// +groupName=ninedata.crossplane.io
// +versionName=v1alpha1
package v1alpha1

const (
    Group   = "ninedata.crossplane.io"
    Version = "v1alpha1"
)

var (
    SchemeGroupVersion = schema.GroupVersion{Group: Group, Version: Version}
    SchemeBuilder = &scheme.Builder{GroupVersion: SchemeGroupVersion}
)
```

## 常见走偏

- 把 ID / 时间戳放进 `Parameters` → 用户改不动，drift 永远不一致
- 用 `metadata.name` 当外部主键 → K8s name 受 DNS-1123 限制，外部系统宽松得多；用 external-name annotation
- `Observation` 里漏掉关键反馈字段（如 endpoint URL） → 用户没法在 status 里看到，需要 kubectl describe 才能查
