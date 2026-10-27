# CN 集群迁移与证据（2026-09-28）

AWS CN 集群已弃用。当前 CN 生产使用腾讯云账号 `100014919455`，独立 staging 和
tech-service 使用账号 `100052802231`。例外：AWS prod-cn（`741924744516`）的存量服务迁入
`100052802231` 的独立 prod 集群 `cn-prod`（`cls-302bzai9`，目录 `tencent-100052802231-cn-prod`，catalog `cn-tke-prod`，
keyword `prod-cn-tke-2231`），与 cn-tech-service 资源隔离。目录存在、旧 DNS 名仍解析、历史 CI job 或历史
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
| prod-cn-tke-2231（AWS prod-cn 迁入目标，2026-10-09 起） | cn-tke-prod | tencent-100052802231-cn-prod | argocd-cn-prod-tke.addx.live / harbor-02231-cn-prod.addx.live（内网） |
| prod-cn-restricted-admin | cn-tke-tech-service | tencent-100052802231-cn-tech-service | argocd-cn-tech-service-tke.addx.live / harbor-02231-cn-tech-service.addx.live |

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

## tech-service 已核验目录准入（2026-10-19）

`cn-tke-tech-service` 已登记 `deployment_status: allowed`、`endpoint_status: verified`，
集群为 `100052802231 / ap-beijing / cls-o0gwzdpz`，amd64 构建 tag 为 `cn-tech-amd64`。
身份、GitOps/live、DNS/TLS、OIDC discovery/登录重定向、Harbor 和实例 Runner 4942
证据及限制见 [准入回执](cn-tech-service-admission-2026-10-19.md)。

- `argocd-cn-tech-service-tke.addx.live` 与 `argocd-cn-tech-service.addx.live` 为
  `cn-tke-tech-service` 内同一 ArgoCD 实例的双入口，用户于 2026-10-19 明确确认。
  旧 `cn-eks-tech-service` 已下线；不能按域名是否带 `-tke` 区分集群或判断迁移状态。
  当前配置的 primary URL 为带 `-tke` 域名，无 `-tke` 域名为 additionalUrls。
  Casdoor 实际 issuer 为
  `https://casdoor-cn-tech-service.addx.live`，不能按域名命名规则改成兼容入口。
- Harbor 默认私网入口为 `harbor-02231-cn-tech-service.addx.live`，受限公网为 `-pub`。
  本轮集群内私网域解析 `10.2.0.6`、公网域解析公网 IP；realm 随请求 host 分别返回，
  不继承 staging 的公网域在 VPC 内解析私网合同。Runner 已实际使用私网镜像和同域 auth key。
- Ops `vault-backend` 仍指向 `vault-cn.addx.live`，JWT mount 为
  `jwt-tke-cn-tech-service-2231`；Builder Vault 入口可达不改变 Ops 密钥域。
- 已验证实例 Runner 4942 在线、未锁定，并有 `cn-tech-amd64` 成功作业；每个项目仍须
  启用实例 Runner，核对 tag/分支权限和实际构建推送。ResourceSpace 项目共享开关已开，
  本轮没有触发该项目的验证流水线。
- 共享 `app-runtime` 只允许已登记仓库/destination/kind，不是整个集群的通用授权。
  ResourceSpace 原要求的 Ubuntu digest 在 `base/ubuntu` 路径返回 404，依赖该镜像的
  构建仍须先补齐精确镜像。目录 PASS 不替代镜像存在性、Application 注册或业务验收。
- `check_deployment_target.py` 继续严格检查 `deployment_status` 和 `endpoint_status`；
  未修改 validator，也不为缺失能力添加绕过。

## 必须按新集群重新验证的能力

- staging 的 `vault-backend` 指向 `http://vault-active.vault.svc:8200`，使用 Kubernetes auth。
  tech-service 的同名 CSS 指向 `https://vault-cn.addx.live`，JWT mount 为
  `jwt-tke-cn-tech-service-2231`。前者不因 CSS 名称变成 ops Vault；后者 ESO mount 不代表
  `sentry-onboard` role 已配置。原 cn-main 的 JWT mount、ServiceAccount 和内网域名不能套用。
- 新 tech-service 的平台入口、实例 Runner 和 ResourceSpace AppProject 已核验，目录准入
  见上方回执。共享 MySQL Composition、COS OIDC/CSI 等逐能力门禁仍保持；
  新应用须按精确 source/destination/kind 核验，不把目录准入等同于所有业务能力可用。
- 原 AWS CN shared-middleware/MSK、Sentry JWT role、开发者 kubectl wrapper、TDD demo
  的已上线记录仅供历史排障。AWS `staging-cn-shared-msk` 已删除；TDD demo 已整体退役不迁。当前 cn-prod、staging、tech-service 三个 `100052802231` 集群必须核验各自
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

## cn-prod 能力状态与存量维护

新 `cn-tke-prod` 的 allowed / verified 是 catalog 准入与已记录的平台入口状态，
不等于所有自助能力就绪。存量路径和 namespace 决策见 [runtime-target.md](runtime-target.md)。

| 能力 | 当前边界与后续动作 |
|---|---|
| 普通 ESO / Vault | `vault-cn-prod`、`vault-backend`，Ops 域；按实际 CSS 核验 writer/reader 与 JWT，不复用 cn-main mount。 |
| 日志标签 | 新 keyword 默认标签 blocked；存量 `prod-cn` 仅配置兼容。见 [日志门禁](logging/README.md#cn-prod-日志映射门禁)。 |
| Sentry onboarding | 现有腾讯 recipe 仅支持 cn-main；cn-prod STOP + Ops Todo，已有 DSN 消费不等于 onboarding。 |
| Ingress / 证书 / SG / WAF | AWS ALB recipe 不适用；须取得该目标 TKE/CLB 已验收合同。 |
| 原生 COS / 托管资源 / CAM | 按原生 workflow 的目标 handoff；readonly provider 不授权新建资源或接管存量。 |
| 基础镜像分发 | 新 Harbor 的持续 fanout 未在下列 base-images 快照登记；精确镜像不存在时 STOP，由平台接入。 |
| Grafana datasource | 外部 dashboard 权威 catalog 尚缺该目标；先在该仓验收 VM/datasource 映射，不复用 cn-main UID。 |
| VM / VMAlert | 精确目标的 CRD/watch、唯一 evaluator、通知链需单独验收，不猜 URL 或继承其他集群配置。 |
| 存量 AWS RDS | 消费已有 per-app 连接需核验 owner、稳定 NAT、SG 白名单及 TLS；不因此套用 AWS IRSA 或创建腾讯 DB。 |
| 开发者 kubectl wrapper | staging-only，不把生产加入支持范围。 |

新 cn-prod 固定 desired state：
[argocd-apps](https://gitlab.addx.ai/DEV/argocd-apps/-/tree/7daa17dc17dc7a0270c23b0062ee8605b1ec8fec/tencent-100052802231-cn-prod)、
[k8s](https://gitlab.addx.ai/DEV/k8s/-/tree/1489d4d04cc60855be1bc951a632cd64e309f5d6/clusters/tencent-100052802231-cn-prod)。
这些配置可核对 ArgoCD/Casdoor/Harbor、Runner、ESO；不能代替 live DNS/证书、
Harbor registry/token realm、GitLab 实际 runner tags 或 Vault 登录/日志入库回执。

跨仓能力缺口基于
[base-images 6ff377b8](https://gitlab.addx.ai/DEV/base-images/-/tree/6ff377b8b4333aa3a59a6967c78d1ec5e6724ffc) 和
[dashboard catalog 891d03b1](https://gitlab.addx.ai/DEV/grafana-dashboards-as-code/-/tree/891d03b103e9df166ceb976091e0f286bd355323)
快照；执行时重新读取对应权威仓，不把历史缺口永久视作现状。
