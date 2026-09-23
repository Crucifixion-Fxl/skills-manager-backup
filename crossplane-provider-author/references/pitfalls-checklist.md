# 常见踩坑 + 上线 checklist

> **何时读**：MR 提交前必读；上线第一个版本前对照。

## 10 条踩坑（按出现频率排序）

1. **忘了跑 `make generate`** → CI 报 "zz_generated.deepcopy.go 过期" / "CRD 没更新"。每改 `apis/**/*_types.go` 都要重跑
2. **敏感字段用了 literal string** → code review 会打回；正确做法永远是 `*xpv1.SecretKeySelector`
3. **Observe 里 pure read 之外写了副作用** → reconcile 行为不可预测；只允许 `meta.SetExternalName` 和 `cr.Status.AtProvider*`，其他写操作放 Create/Update
4. **用 `metadata.name` 当外部主键** → K8s name 受限（63 字符、DNS-1123），外部系统名称更宽松，用户会疑惑；永远用 external-name annotation
5. **ResourceUpToDate 对比了不可变字段** → 触发 Update，可能造成外部资源删除重建；只比对"API 支持更新"的那几个字段，其他字段变化留给用户手动处理
6. **ProviderConfig 的 `ClusterProviderConfig` 忘了注册** → 用户写 `kind: ClusterProviderConfig` 时 Connect 报 "unsupported provider config kind"；register.go 里两种都要调
7. **CRD scope 默认写了 Cluster** → 用户没法在业务 ns 引用同 ns 的 Secret；绝大部分场景应该是 Namespaced
8. **生产环境把外部系统 IP 白名单对准 pod IP** → 节点重建就挂；pod 必须调度到 nat-egress NodePool，走稳定 NAT IP（见 Codex 全局 `AGENTS.md` 网络安全红线 #1）
9. **忘了 `safe-start` capability** → CRD 顺序部署问题一出现就 CrashLoopBackoff
10. **第一版就想 cover 所有字段** → 先只做 MVP（3-4 个核心字段 + 可见状态），然后按真实需求扩；provider-ninedata v0.1.0 只有 DataSource CRUD，v0.1.2 才加 FromSecretRef，v0.1.3 才加 CloudProfile

## 上线 Checklist

提交 MR 前对照打勾。

### 代码层

- [ ] 从 provider-template 起步，`make provider.prepare` 跑过
- [ ] 至少一个资源类型有 `ForProvider` / `Observation` / `Spec` / `Status` 四段式
- [ ] Observe 支持 adopt（external-name 为空时按 name 查）
- [ ] 敏感字段全部 `*SecretRef`
- [ ] 可能被上游 CR 引用的字段有 `*FromSecretRef` 版本
- [ ] `ProviderConfig` + `ClusterProviderConfig` 都注册
- [ ] Client 包独立，签名/鉴权有单元测试（`Sign()` 可直接测）
- [ ] 控制器有 httptest.Server-based 测试覆盖主路径
- [ ] `package/crossplane.yaml` 声明 `safe-start` capability
- [ ] 每个资源 controller 用 `SetupGated` 而不是 `Setup`

### 文档 + 例子

- [ ] `examples/` 有 ProviderConfig 范例 + 至少一个资源 CR 范例
- [ ] README 里写清楚凭据 schema（Secret 的 JSON 字段）+ Vault 路径
- [ ] README 列了已知不可变字段（用户改了会失败）

### 部署

- [ ] `Makefile` 的 `XPKG_REG_ORGS` 指向 SG Harbor `harbor-12571-sg-devops.addx.live/base`
- [ ] CI 跑通：`go test`、`make generate`、`make build`、`make xpkg.build`
- [ ] k8s 仓库准备好 `Provider` + `ControllerConfig` + `ProviderConfig` + `ExternalSecret` YAML
- [ ] `ControllerConfig` 上的 `nodeSelector` 调度到 `nat-egress` NodePool
- [ ] argocd-apps 仓库有 Application 指过来
- [ ] Vault 里 `secret/cicd/<provider>/api-credentials` 已写入（JSON 格式）

### 上线验证

- [ ] `kubectl get providers.pkg.crossplane.io provider-<name>` `Healthy=True`
- [ ] `kubectl describe clusterproviderconfig <name>-default` 没报错
- [ ] pod exec 进去 `curl checkip.amazonaws.com` 看到的是 NAT EIP，与 `aws ec2 describe-nat-gateways` 一致
- [ ] apply 一个测试 CR，`kubectl describe <kind>` 看到 `Ready=True, Synced=True`
- [ ] 改一下 CR 可更新字段（如 name），观察 controller 调用 Update
- [ ] 删除测试 CR，观察控制器调用 Delete，外部资源真的被删
- [ ] 首个真实消费者 pilot 跑通（创建→更新→删除全链路）

### 后续维护

- [ ] 在 README "Status" 小节写清楚 v0.x 已做什么、v0.y 计划做什么（参考 provider-ninedata README 的 M1/M2/M3/M4 milestone 风格）
- [ ] 把版本号写进 `internal/version/version.go`，CI 用 `go build -X` 注入

## 特殊情况

### 资源 API 不支持 GET，怎么办

NineData 没有"按 ID 单查"接口，provider-ninedata 的处理是 list + 客户端 filter（`internal/clients/ninedata/datasource.go` 的 `findDataSource`）。可接受的折衷：

- list 按 100/page 翻页
- 客户端过滤
- 注释清楚为什么这么做、未来 API 升级后怎么改

如果资源数量上千，再考虑：

- 加 client-side 缓存（注意 TTL，不要让 Observe 因为缓存返回过期数据）
- 推动外部系统加 single-get API

### 外部系统不支持 idempotent Create

很多 SaaS 的 Create 是 "name 唯一"。Observe 里的 adopt（按 name 查）配合"如果已存在就 pin external-name + 跳过 Create" 就解决了，参考 [external-client.md](external-client.md)。

### 外部系统 rate limit

- `controller.Options.MaxConcurrentReconciles` 默认 10，可调小
- `--poll` 参数（默认 1m）调大降低轮询频率
- `do()` 里捕获 429 → 返回特殊 error → 让 reconciler retry with backoff

### 多 group / 多 version

provider-template 一个 group 一个 v1alpha1。如果你后面要稳定到 v1，需要：

- 加 `apis/<group>/v1/` 目录
- `apis/<group>/<group>.go` 同时注册多版本
- 在 v1alpha1 的 storage version 切换前，写好 conversion webhook（v2 已支持原生 multi-version）

### 升级 crossplane-runtime

- 跟 `provider-template` 的 release 走，每次 v2 minor 升级前先看 `provider-template` changelog
- `xpv1` → `xpv2` 大版本切换会涉及 ManagedResourceSpec 的重大改动；动手前先在分支验证
