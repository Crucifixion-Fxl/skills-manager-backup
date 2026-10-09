# CN 集群迁移与证据（2026-09-28）

AWS CN 集群已弃用。当前 CN 生产使用腾讯云账号 `100014919455`，独立 staging 和
tech-service 使用账号 `100052802231`。目录存在、旧 DNS 名仍解析、历史 CI job 或历史
允许项都不构成新部署准入证据。禁止回退到 AWS CN，或把旧腾讯 staging 账号
`100050722703`、旧 `100014919455` tech-service 当成当前目标。

## staging-cn 统一目标（2026-10-04 起）

CN 所有 dev / staging 工作负载的唯一目标是 `cn-tke-staging`（腾讯云 `100052802231` /
`ap-beijing` / `cls-riukakjb`，GitOps 目录 `tencent-100052802231-cn-staging`）。以下旧 staging
来源全部退役，不再作为部署、构建、排障默认或 fallback：

| 旧来源 | 现状（2026-10-04 只读核实） | 替代 |
|---|---|---|
| AWS `801447536674` `cn-eks-dev` | EKS 控制面已删除，`dev-cn` keyword 仍 STOP | `staging-cn` |
| AWS `801447536674` `cn-eks-staging` | 无业务 workload；Crossplane provider 全部 0 副本；GitLab runner 4001 已暂停 | `staging-cn` |
| 旧腾讯 `100014919455` staging-cn VPC（`vpc-jxxz6aiq` / `cls-i860hdh9`，GitOps 目录 `tencent-100050722703-cn-staging`） | 业务副本全部 0；ArgoCD 全部 `skip-reconcile`；指向其 CLB 的 DNS 已停用 | `staging-cn` |

范围限定：只针对上述 staging-cn VPC / 集群。`cn-main`（`100014919455` prod 集群
`cls-kr3pti7p`）里长期存在的少量 `staging-cn` namespace 服务**不在本次迁移范围**，仍按其所在
Application 的实际 destination 处理，不能据此认为它们已迁到新 staging，也不能把它们改指新集群。

CI runner：GitLab instance runner 4542（`tke-staging-runner`，运行在 `cls-riukakjb`）同时持有
`tke-cn-staging-amd64`、`tke-staging-amd64`、`tke-staging` 与存量兼容 tag `cn-staging-amd64`、
`cn-staging`。AWS runner 4001 已暂停并改 tag 为 `retired-cn-staging-amd64-4001`。因此存量
`.gitlab-ci.yml` 里的 `cn-staging-amd64` 现在落到 TKE；新 CI 仍应显式写 `tke-cn-staging-amd64`。
没有 `cn-staging-arm64` 接管者，新集群也无 arm64 节点：请求 arm64 的 job 会一直 pending，
不能宣称 CN staging 支持 ARM64。runner 注入 `HARBOR_REGISTRY=harbor-cn-staging.addx.live`，
任何写死推送 `harbor-80144-cn-staging.addx.live` 的 job 在 TKE 上必然失败，应删除对应 AWS 构建 job。

| 环境 | catalog name | argocd-apps 目录 | ArgoCD / Harbor |
|---|---|---|---|
| prod-cn / prod-cn-tke | cn-k8s | tencent-100014919455-cn-main | argocd-cn-k8s.addx.live / harbor-cn.addx.live |
| staging-cn / staging-cn-tke（CN 唯一 dev/staging 目标） | cn-tke-staging | tencent-100052802231-cn-staging | argocd-cn-staging.addx.live / harbor-02231-cn-staging.addx.live（内网） |
| dev-cn（已退役，STOP；改用 staging-cn） | cn-eks-dev | — | — |
| prod-cn-restricted-admin（域名目标待切换） | cn-tke-tech-service | tencent-100052802231-cn-tech-service | argocd-cn-tech-service-tke.addx.live / harbor-02231-cn-tech-service.addx.live |

## 数据来源

用户于 2026-09-28 确认账号、AWS CN 退役范围及 staging Harbor 内外网地址；下列仓库固定版本交叉验证目录和配置。
配置快照只证明 desired state；部署回执证明其记录时点的验收，不替代执行时重新核验。

- [argocd-apps 27c65192](https://gitlab.addx.ai/DEV/argocd-apps/-/tree/27c65192843a1977a68edc69da11fd6b17b86db9)：三个当前 CN 目录内 Application source/valueFiles。
- [k8s cf0a1feb](https://gitlab.addx.ai/DEV/k8s/-/tree/cf0a1febe5491f660933056260011853f4277c37)：相同目录下 `cicd/argocd/values-self-managed.yaml`、`cicd/casdoor/values-override.yaml`、`cicd/external-secrets-config/cluster-secret-store.yaml` 和 `harbor/values-*.yaml`。
- **历史快照**：上述 `k8s cf0a1feb` 中 staging 的 `cicd/gitlab-runner/values-override-amd64.yaml` 声明 `tke-cn-staging-amd64`、`HARBOR_REGISTRY=harbor-cn-staging.addx.live`；不能据此声称 runner 已改用新内网域名。
- **runner tag 生效位置**：该 runner 用 registration token 注册，tag 以 GitLab 服务端记录为准；k8s `9ddfdea6` 在 values 中追加的 `cn-staging-amd64,cn-staging` 并未生效，2026-10-04 由 GitLab 管理员在 runner 4542 上补齐。核验 tag 时读 `GET /runners/<id>`，不要只看 values。
- [Harbor 内网入口部署回执（k8s ce9e5133）](https://gitlab.addx.ai/DEV/k8s/-/blob/ce9e51334789c5accac3cb580e44cbb1685214c5/clusters/tencent-100052802231-cn-staging/harbor/private-entry/README.md)：固定版本记录内外网入口和拉取验收；它不表示该变更已合入 `master`，本轮技能修订未重新执行 live 云验证。

## staging Harbor 内外网合同

- `harbor_url=harbor-02231-cn-staging.addx.live` 是 `staging-cn` 和 `staging-cn-tke` 的部署默认值，仅在目标 VPC 内解析；Pod 镜像默认使用该内网域名。
- `harbor_public_url=harbor-02231-cn-staging-pub.addx.live` 是同一 registry 的外部访问与 SG 同步入口，在目标 VPC 内也解析到私网，VPC 外走受限公网。不是第二套 Harbor，也不需要复制两份镜像。
- canonical token realm 为 `https://harbor-02231-cn-staging-pub.addx.live/service/token`；内网拉取仍须验证 registry 与 token 服务均可达且落到预期私网。
- `harbor-cn-staging.addx.live` 是**同一 Harbor 实例**的兼容入口（2026-10-05 只读核实：VPC 内三个域名均解析到 `172.17.2.3`，公网 `/v2/` 的 token realm 均为 `-pub`），不是另一套 Harbor。它当前仍是 runner `HARBOR_REGISTRY`、Image Updater registry（`api_url`/`prefix`）和约 105 个运行中容器的实际镜像域名；`harbor-cn-staging-new.addx.live` 为另一个公网入口，仅极少量镜像在用。新项目默认仍用内网 `harbor-02231-cn-staging.addx.live`。本次 catalog 更新不自动迁移现存 image refs 或 Secret。切换消费者前，核查 runner 实际 `HARBOR_REGISTRY`、构建/部署 image refs、Docker `auths` 和 Image Updater registry/凭据配置中的**精确 hostname**。采用内网镜像的 pull secret 必须包含 `harbor-02231-cn-staging.addx.live` 对应 auth key；同一有效 robot 可复用，但旧域名的凭据条目不能证明新域名已配置。缺失时由平台同步机制补齐并验证后再切换，禁止静默替换现存镜像或 Secret。

## tech-service 目标域名（待切换）

用户于 2026-09-28 明确“作为目标命名，但是 skill 先改”。catalog 的 tech-service URL
按目标填写，`endpoint_status: pending-cutover`；不能当作 DNS、证书、服务或迁移已验收。
完整七个入口与固定版本的旧声明对照见 [CN 集群清单](../../../infrastructure/k8s-ops/references/cn-tencent-inventory.md)。

- ArgoCD 为 `argocd-cn-tech-service-tke.addx.live`，Casdoor 为
  `casdoor-cn-tech-service-tke.addx.live`；实施时同步核验 server URL、OIDC issuer/discovery、
  client redirect URI 和 webhook 入口，不能只改 Ingress host。
- Harbor 内网目标 `harbor-02231-cn-tech-service.addx.live`；外部同步目标
  `harbor-02231-cn-tech-service-pub.addx.live`。同一实例双入口是目标合同；还没有 staging
  那样的私网部署回执，不能声称 token realm、split DNS、镜像同步或 auth aliases 已就绪。
- `vault_builder_url=https://vault-cn.builder.addx.live` 单独记录 Builder 服务目标入口，
  不改变 `domain=ops`、`vault=vault-cn-prod`、`vault_css=vault-backend`。
  Vault 服务部署位置、应用密钥域、ESO 认证是三个不同问题；域名存在不证明迁往新 TKE。
- VictoriaMetrics 查询 base 为 `https://victoria-metrics-cn-tech-service-tke.addx.live/select/0/prometheus`；
  VMAlert 为 `https://vm-alert-cn-tech-service-tke.addx.live`，两者用途不同。
- URL 命名不会改变 JWT mount、KV 路径、ServiceAccount、指标 `cluster` 标签或 runner tag。
  独立 runner 与业务准入仍未核验，`deployment_status=blocked` 保留；即便只把它误改成
  `allowed`，准入检查仍因 `endpoint_status=pending-cutover` 拒绝 Build。待平台切换并核验后
  通过新的源仓库变更将状态登记为 `verified`，禁止 workflow 自行放行。
  `endpoint_status` 如显式提供，只接受 `pending-cutover` / `verified`；空值或未知值视为
  catalog 错误并拒绝解析。既有其他集群未设置该字段时保持原准入规则，不据缺省推断 live 已验证。

## 必须按新集群重新验证的能力

- staging 的 `vault-backend` 指向 `http://vault-active.vault.svc:8200`，使用 Kubernetes auth。
  tech-service 的同名 CSS 指向 `https://vault-cn.addx.live`，JWT mount 为
  `jwt-tke-cn-tech-service-2231`。前者不因 CSS 名称变成 ops Vault；后者 ESO mount 不代表
  `sentry-onboard` role 已配置。原 cn-main 的 JWT mount、ServiceAccount 和内网域名不能套用。
- 新 tech-service 只核验到平台 Application，业务 AppProject/部署准入未证实，
  catalog 标为 `blocked`；restricted-admin keyword 保留正确目标映射，但禁止 Build。
  该目录也未声明独立 build runner，catalog 不提供虚构 tag。
  需要构建时先核验 runner/Harbor 对应关系；镜像已存在的部署仍须检查具体目标 readiness。
- 原 AWS CN shared-middleware/MSK、Sentry JWT role、开发者 kubectl wrapper、TDD demo
  的已上线记录仅供历史排障。AWS `staging-cn-shared-msk` 已删除；TDD demo 已整体退役不迁。当前两个 `100052802231` 集群必须核验各自
  XRD/Composition/ProviderConfig、Vault writer/reader、身份权限和镜像分发；不能自动复制 AWS ARN、
  MSK broker、NAT NodePool、旧账号 CAM role 或 cn-main 专用 Sentry 模板。
- `check_db_resource_contracts.py` 的 producer 投影仍对应现有 AWS Composition（staging 使用
  `vault-builder-backend`）；它不证明新 TKE 的 writer/CSS 合同。CN shared claim 能力开放前，
  平台需同步校准该投影与目标 Composition，不能以现有静态 PASS 宣称 CN 交付验收完成。
- staging/tech-service 仍遵守共享中间件策略；能力缺失时输出 Ops Todo，不能借迁移创建
  app-owned 实例、共享 root 凭据或自托管数据库。prod-cn 使用腾讯云资源流程；AWS RDS/IRSA/S3
  workflow 不因 region=cn 而适用。
- 当前 CN Ingress 必须按对应 TKE controller/CLB、证书、实际 SG 与 WAF 合同生成。
  不能把 ALB 模板只补一段 TLS 就当成 TKE 模板。公共 DNS 前按真实后端重新核验暴露面。
- 保留的 AWS CN catalog 项均为 `retired`；历史 ARN、validator 兼容测试和精确 legacy exception
  不授予 Build 准入。旧应用迁移需单独核验 source/target，不因 keyword 改路由而静默移动存量。
