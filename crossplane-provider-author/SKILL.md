---
name: crossplane-provider-author
description: 指导开发者从零写一个 Crossplane Provider 把外部系统（SaaS / 内部平台 / 监控系统）变成 Kubernetes CRD。基于 provider-template v2 + crossplane-runtime v2，以 gitlab.addx.ai/DEV/provider-ninedata 为参考实现。当开发者说"给 XX 写一个 crossplane provider"、"让应用通过 K8s CR 自动接入 Prometheus/Grafana/Sentry/NineData 类平台"、"把这个 OpenAPI 包成一个 managed resource"，或者已有资源需要 GitOps 化纳管时触发。
---

# crossplane-provider-author

教你写一个 Crossplane Provider：把外部系统（任何有 REST/OpenAPI 的平台）的资源生命周期，变成 Kubernetes CRD + Controller。

参考实现：[gitlab.addx.ai/DEV/provider-ninedata](https://gitlab.addx.ai/DEV/provider-ninedata)。

## Description

本 skill 采用**渐进式披露**：SKILL.md 只是一张索引 + 决策树，每个主题的代码模板、踩坑细节都按需进 [references/](references/) 文档查阅。第一次只需要扫读 SKILL.md，等真要写某个环节时再点进对应 reference。

## Rules

### Step 0 — 决策：是否真要写 Provider

| 选项 | 适用场景 | 工时 |
|------|----------|------|
| 直接用社区 / 上游 provider | 资源类型够用，只需 GitOps 化 | 1 天接入 |
| Terraform / 一次性脚本 | 配置极少变动 / 一次性 | 0.5 天 |
| **写自定义 Provider** | 业务封装逻辑 + 需要被其他 K8s 资源引用 + 长期维护 | 3-5 天首版 |

只有第三种适用本 skill。决定要写之后，按下面顺序推进，每一步对应一个 reference 文档。

### Step 1 — 起步：从 provider-template 起步，**别从零写**

详见 → [references/scaffolding.md](references/scaffolding.md)

要点：clone `crossplane/provider-template` → `make provider.prepare provider=<Name>` → `make provider.addtype provider=<Name> group=<g> kind=<K>` → `make generate`。`zz_generated.*.go` 和 `package/crds/*.yaml` 都是产物，**永远不要手编辑**。

### Step 2 — 设计 CRD 类型（API 先行）

详见 → [references/crd-design.md](references/crd-design.md)

铁律：

1. 类型必须三段式：`<Resource>Parameters`（spec.forProvider）+ `<Resource>Observation`（status.atProvider）+ `Spec`/`Status`
2. 敏感字段必须走 `*xpv1.SecretKeySelector`，不收 literal string
3. 外部 ID 不写进 spec；mirror 到 `crossplane.io/external-name` annotation + status
4. 可能被上游 CR 引用的字段提供 `<Field>FromSecretRef` 双轨
5. 不可变字段加注释警示，且 `ResourceUpToDate` 不对比它们
6. Scope 默认 Namespaced（多数场景）

### Step 3 — 实现 ExternalClient（Observe 是核心）

详见 → [references/external-client.md](references/external-client.md)

四方法分工：`Observe` 是每轮 reconcile 都跑的状态对账；`Create`/`Update`/`Delete` 只是 Observe 反作用的执行器。

**Observe 必须记住的 4 件事**：

- 必须**幂等**（除写 status 和 external-name 外不能有副作用）
- **External-name 是主键**：创建后 pin 在 annotation；之后用它而不是 K8s `metadata.name` 定位资源
- **支持 adopt**：external-name 为空时按 `spec.forProvider.name` 兜底查找 → 找到就 pin
- **`ResourceUpToDate` 只比对 API 真支持更新的字段**；不可变字段 drift 不要自动 Update（参考 provider-ninedata：只对比 name）

### Step 4 — 凭据：ProviderConfig + 单 Secret JSON envelope

详见 → [references/credentials.md](references/credentials.md)

要点：

- 一个 ProviderConfig 收一个 Secret，Secret 里 `credentials.json` 用 JSON 塞所有字段（endpoint / AK / SK / 可选扩展）
- 同时注册 `ProviderConfig`（Namespaced）和 `ClusterProviderConfig`（Cluster）两种 kind
- 生产 Secret **永远由 ExternalSecret 从 Vault 拉**，路径规范：`secret/cicd/<provider>/api-credentials`

### Step 5 — Client 包独立 + httptest 测试

详见 → [references/client-and-testing.md](references/client-and-testing.md)

要点：

- `internal/clients/<name>/` 独立成包，不依赖 K8s
- 一个统一的 `do(method, path, body, out)` 封装签名 + envelope 解析
- `Sign()` / `Now func() time.Time` 暴露给测试
- 控制器测试用 `httptest.Server` mock 远端，不要 mock crossplane-runtime

### Step 6 — 打包 + 部署（safe-start + 三仓库 GitOps）

详见 → [references/packaging-deployment.md](references/packaging-deployment.md)

要点：

- 每个资源用 `SetupGated` 而不是 `Setup`；`package/crossplane.yaml` 声明 `safe-start` capability
- Makefile 的 `XPKG_REG_ORGS` 指 `harbor-12571-sg-devops.addx.live/base`
- 部署走 base-images / k8s / argocd-apps 三仓库（以仓库 `AGENTS.md` 和 k8s GitOps 文档为准）
- 出口 IP 绑 `nat-egress` NodePool（Codex 全局 `AGENTS.md` 网络安全红线 #1）

### Step 7 — 写完前对照 checklist & 常见踩坑

详见 → [references/pitfalls-checklist.md](references/pitfalls-checklist.md)

提交 MR 前**必须**对照 checklist 自检。10 条按出现频率排序的踩坑都在这里。

## Examples

完整 Bad/Good 对比组在 [references/bad-good-examples.md](references/bad-good-examples.md)。下面只放 1 组锚点示例，让你立刻看出"风格"。

### ❌ Bad Example — Observe 不兜底按 name 查（破坏 adopt 模式）

```go
func (c *external) Observe(ctx, cr) (managed.ExternalObservation, error) {
    externalName := meta.GetExternalName(cr)
    if externalName == "" {
        return managed.ExternalObservation{ResourceExists: false}, nil  // 直接走 Create
    }
    found, err := c.service.GetByID(ctx, externalName)
    // ...
}
```

问题：用户手写 CR 想纳管已存在的外部资源时，会被重复创建。

### ✅ Good Example — Observe 支持 adopt

```go
externalName := meta.GetExternalName(cr)
if externalName != "" {
    found, err = c.service.GetByID(ctx, externalName)
} else {
    found, err = c.service.GetByName(ctx, cr.Spec.ForProvider.Name)  // 兜底
}
if found != nil && externalName == "" {
    meta.SetExternalName(cr, found.ID)  // pin 主键
}
```

更多对比（凭据字段设计、字段 drift 检测）见 [references/bad-good-examples.md](references/bad-good-examples.md)。

## 实战示范

教学性的端到端走查：**给 Prometheus / Grafana 写一个 onboarding provider**（让开发者 apply 一段 CR 就自动建 folder + dashboard + scrape rule + alert receiver）。

完整 walkthrough（含 CRD 设计、Client 包分层、ExternalClient 实现、部署 YAML）→ [references/walkthrough-prom-grafana.md](references/walkthrough-prom-grafana.md)

## 话题索引

| 阶段 | 文档 | 何时读 |
|------|------|--------|
| 起步脚手架 | [references/scaffolding.md](references/scaffolding.md) | 第一次起 provider |
| CRD 类型设计 | [references/crd-design.md](references/crd-design.md) | 加新资源类型时 |
| ExternalClient 实现 | [references/external-client.md](references/external-client.md) | 写 Observe/Create/Update/Delete 时 |
| 凭据 + ProviderConfig | [references/credentials.md](references/credentials.md) | 接入认证或写 ExternalSecret 时 |
| Client 包 + 测试 | [references/client-and-testing.md](references/client-and-testing.md) | 写 HTTP client 或单测时 |
| 打包 + 部署 | [references/packaging-deployment.md](references/packaging-deployment.md) | 上线前 |
| Bad/Good 对比 | [references/bad-good-examples.md](references/bad-good-examples.md) | code review 前自检 |
| 踩坑 + checklist | [references/pitfalls-checklist.md](references/pitfalls-checklist.md) | MR 前必读 |
| 实战 walkthrough | [references/walkthrough-prom-grafana.md](references/walkthrough-prom-grafana.md) | 想看完整端到端示例时 |

## 参考资料

- **本地实现**：`~/Project/A4x/provider-ninedata/` — ~600 行完整实现，按 Step 1-7 都有代码落点
- [Crossplane 官方 runtime API](https://pkg.go.dev/github.com/crossplane/crossplane-runtime/v2)（用 context7 MCP 查 `/crossplane/docs`）
- [provider-template](https://github.com/crossplane/provider-template) — 脚手架起点
- [provider-aws (upjet-based)](https://github.com/crossplane-contrib/provider-upjet-aws) — 大型 provider 参考；小 provider 不需要 upjet
