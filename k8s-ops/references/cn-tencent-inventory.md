# CN 腾讯云环境事实与核验入口

## 范围与证据版本

用户于 **2026-09-28** 明确：AWS CN 集群已弃用，最新 CN 集群全部在腾讯云；prod 主账号为 `100014919455`，staging 和 tech-service 主账号为 `100052802231`。这是当前环境选择规则，不意味着所有历史 AWS 数据库、代理、存储或旧腾讯云依赖均已删除。

首次 CN 集群迁移核对使用以下远端快照，没有使用本机 checkout 中未提交的修改：

- `DEV/argocd-apps` `origin/main`：`27c65192843a1977a68edc69da11fd6b17b86db9`
- `DEV/k8s` `origin/master`：`cf0a1febe5491f660933056260011853f4277c37`

本次 cn-tech-service 目标命名核对使用 `DEV/k8s` `origin/master`
`29144fcbf7bbcea02e0bf2c57232bc564a6daf4c` 和 `DEV/argocd-apps` `origin/main`
`ad43ebcb7ff3a0475f0325f9a91b74252363e1eb`；新域名仍为用户指定的待切换目标，见下表。

后续操作先更新远端引用，再查目标 Application 的 `source` / `sources`（含 revision、Helm values 和 Kustomize patches）以及 live root Application。仓库中目录存在、旧 context 仍可连接、旧 fleet scanner 仍有目标，都不能单独证明该环境仍然有效。

## 当前目标

| 环境 | 腾讯云主账号 | Region | GitOps 目录（两仓同名，k8s 加 `clusters/`） | ArgoCD |
|---|---|---|---|---|
| CN prod / cn-main | `100014919455` | `ap-beijing` | `tencent-100014919455-cn-main` | `argocd-cn-k8s.addx.live` |
| CN staging | `100052802231` | `ap-beijing` | `tencent-100052802231-cn-staging` | `argocd-cn-staging.addx.live` |
| CN tech-service | `100052802231` | `ap-beijing` | `tencent-100052802231-cn-tech-service` | `argocd-cn-tech-service-tke.addx.live`（目标，待切换） |

`cn-main` 是 prod 集群的规范目录名；扫描时不要额外再加一个代表旧 AWS 的 `cn-prod`，导致重复计算或连接旧集群。业务的 `prod-cn` overlay / namespace 名可以继续存在。

集群 ID 与 context：

- prod：现有 k8s `cicd/argocd/values.yaml` / `DEPLOYMENT.md` 记录 TKE `cls-kr3pti7p`；常见本机别名 `tke-cn-k8s`，使用前仍须核验。
- staging：`argocd-apps/docs/plans/2026-09-24-tencent-staging-ingress-account-mapping.md` 和 k8s `clusters/tencent-100052802231-cn-staging/harbor/node-resolution/README.md` 明确账号 `100052802231`、集群 `cls-riukakjb`、区域 `ap-beijing`。kubeconfig 用 `tccli tke DescribeClusterKubeconfig --ClusterId cls-riukakjb --IsExtranet true`；公网 API endpoint 从部分网络会间歇 `connection reset`，可改从办公网跳板执行。
- 旧 staging（勿作目标）：`100014919455` / `cls-i860hdh9` / VPC `vpc-jxxz6aiq`（CIDR 与新 staging 相同，均为 `172.17.0.0/16`，不能按 IP 段判断归属）。
- tech-service：本次读取的集群入口配置未提供可靠 cluster ID；不得按账户 ID、目录名或旧腾讯集群推算。使用已授权的腾讯云 TKE 查询与本机 kubeconfig 核验后设置 `CN_TECH_CONTEXT`。
- 新账号 profile 名是本地约定，不能假定存在 `new` 或 `tencent-100052802231-*`。使用 `tccli sts GetCallerIdentity --profile <candidate> --region ap-beijing` 校验 `AccountId`（主账号 UIN），再核验目标 cluster ID / API server。不得打印 SecretId、SecretKey、token 或 kubeconfig 凭据。

`GetCallerIdentity.AccountId` 的含义见[腾讯云 STS 官方 API 文档](https://cloud.tencent.com/document/api/1312/66098)。

## CN tech-service 目标域名（待切换）

用户于 **2026-09-28** 明确以下地址作为目标命名，技能先更新。全部状态均为
`pending-cutover`：不表示 DNS、证书、Ingress、认证、镜像或 Vault 已完成迁移。
执行前读取目标 TKE 的当前 Application/Ingress/SecretStore 和实际服务身份，核验后才使用；
未完成时暂停该目标操作，不自动连接旧 AWS CN，也不把旧 Git 声明当作已部署或 fallback 授权。

| 服务 | 目标地址 | 状态与用途 |
|---|---|---|
| Vault Builder | `https://vault-cn.builder.addx.live` | `pending-cutover`；cn-tech-service Builder 目标入口，TKE 部署归属和认证待核验 |
| Harbor 内网 | `harbor-02231-cn-tech-service.addx.live` | `pending-cutover`；目标集群内默认 registry |
| Harbor 公网 | `harbor-02231-cn-tech-service-pub.addx.live` | `pending-cutover`；获准的外部访问及 SG 同步目标 |
| ArgoCD | `https://argocd-cn-tech-service-tke.addx.live` | `pending-cutover` |
| Casdoor | `https://casdoor-cn-tech-service-tke.addx.live` | `pending-cutover`；ArgoCD OIDC issuer/回调须随实际切换核验 |
| VictoriaMetrics | `https://victoria-metrics-cn-tech-service-tke.addx.live` | `pending-cutover`；VMCluster 查询 API 仍需实际 tenant/path |
| VMAlert | `https://vm-alert-cn-tech-service-tke.addx.live` | `pending-cutover` |

VictoriaMetrics 的目标 Prometheus API base 示例为
`https://victoria-metrics-cn-tech-service-tke.addx.live/select/0/prometheus`；
执行前核验实际 tenant，`0` 不是本轮已确认的运行态租户，入口与查询路径仍待切换验收。

两个 Harbor 地址的目标设计是**同一实例的双入口**，不自动复制镜像或迁移 runner、
Image Updater、pull-secret。采用内网镜像前，核验精确 registry hostname 的 `auths`、
实际 `externalURL` 和 `/v2/` 返回的 token realm，以及调用端对 registry/realm 的 DNS、TLS 和网络。
tech-service 尚无本轮双入口部署回执，不能照搬 staging 已验收的 split DNS/公网 token realm
结论，也不能因内网不可达就静默切公网。

### 迁移前 Git 声明（固定快照，非自动回退地址）

以下均来自 k8s `29144fcbf7bbcea02e0bf2c57232bc564a6daf4c` 的
`clusters/tencent-100052802231-cn-tech-service/`。对应的
[ArgoCD Application](https://gitlab.addx.ai/DEV/argocd-apps/-/blob/ad43ebcb7ff3a0475f0325f9a91b74252363e1eb/tencent-100052802231-cn-tech-service/argocd.yaml)
及同目录组件 Application 引用 k8s `master`，但 Git 声明不证明 live 已同步。

| 组件 | 迁移前声明 | 固定来源 |
|---|---|---|
| Harbor | `harbor-cn-tech-service-2231-tke.addx.live`；`externalURL` 同域名 | [Harbor values:9,52](https://gitlab.addx.ai/DEV/k8s/-/blob/29144fcbf7bbcea02e0bf2c57232bc564a6daf4c/clusters/tencent-100052802231-cn-tech-service/harbor/values-override.yaml#L9) |
| ArgoCD / Casdoor issuer | `argocd-cn-tech-service-2231-tke.addx.live` / `casdoor-cn-tech-service-2231-tke.addx.live` | [ArgoCD values:200,208](https://gitlab.addx.ai/DEV/k8s/-/blob/29144fcbf7bbcea02e0bf2c57232bc564a6daf4c/clusters/tencent-100052802231-cn-tech-service/cicd/argocd/values-self-managed.yaml#L200) |
| VictoriaMetrics / VMAlert | `victoria-metrics-cn-tech-service-2231-tke.addx.live` / `vm-alert-cn-tech-service-2231-tke.addx.live` | [VM values:258,403](https://gitlab.addx.ai/DEV/k8s/-/blob/29144fcbf7bbcea02e0bf2c57232bc564a6daf4c/clusters/tencent-100052802231-cn-tech-service/victoria-metrics-stack/values-override.yaml#L258) |
| Ops ESO | `vault-backend` → `https://vault-cn.addx.live`；JWT `jwt-tke-cn-tech-service-2231` | [ClusterSecretStore:4-20](https://gitlab.addx.ai/DEV/k8s/-/blob/29144fcbf7bbcea02e0bf2c57232bc564a6daf4c/clusters/tencent-100052802231-cn-tech-service/cicd/external-secrets-config/cluster-secret-store.yaml#L4) |

Builder 目标入口**不是**上述 Ops Store 的新地址。`vault-policies`
[固定实例表](https://gitlab.addx.ai/DEV/vault-policies/-/blob/6da445c8e7ceea3dd153464742ee23f265bfe794/README.md#L91)
区分 `vault-cn.addx.live` 与 `vault-cn.builder.addx.live`；既有 Builder URL 不证明其宿主已迁入新 TKE。
[2026-07-13 Builder 回执](https://gitlab.addx.ai/DEV/k8s/-/blob/29144fcbf7bbcea02e0bf2c57232bc564a6daf4c/docs/plans/2026-07-13-cn-tech-vault-builder-stateful-ondemand-migration.md#L3)
仅记录 AWS `589899215075`，且明确这些文件是未受 ArgoCD 管理的旧 Helm 记录。
新 tech-service Builder 的部署、Store、mount、issuer、role、audience、SA 和密钥归属均待核验。

域名去掉 `2231` 不改变认证或数据标识：保留 Ops 的 JWT mount、`external-secrets` role、
`external-secrets/external-secrets` SA、audience `vault`；真实 Kubernetes JWT issuer 另行读取，
不能替换成 Casdoor/Vault 域名。`prod/cicd/application/tke-tech-service-cn-2231/` KV 前缀、
metrics 的 `cn-tech-service-2231` 标签及 SG 调用者的 `jwt-eks-sg-devops` 也不随域名改名。

## 集群专属配置

| 目标 | Harbor | ESO 当前 Store / 地址 | Auth |
|---|---|---|---|
| prod / cn-main | `harbor-cn.addx.live` | `vault-backend` → `https://vault-cn-internal.addx.live` | Kubernetes mount `kubernetes` |
| staging（2231） | `harbor-02231-cn-staging.addx.live`（内网默认）；`harbor-02231-cn-staging-pub.addx.live`（受限公网）；`harbor-cn-staging.addx.live`（同实例兼容入口，runner/Image Updater 现用） | `vault-backend` → `http://vault-active.vault.svc:8200`（本集群）；公网入口 `vault-cn-staging.addx.live` → `43.179.178.102`（新账号 CLB `lb-kz89y6gx`） | Kubernetes mount `kubernetes` |
| tech-service（2231） | `harbor-02231-cn-tech-service.addx.live`（内网目标）；`harbor-02231-cn-tech-service-pub.addx.live`（公网目标）；均待切换 | `vault-backend` → `https://vault-cn.addx.live`（Ops Git 声明，保持） | JWT path `jwt-tke-cn-tech-service-2231`（保持） |

staging Harbor 的后续更新及 tech-service 的待切换目标按各自说明使用；其余值来自各集群 `cicd/external-secrets-config/cluster-secret-store.yaml`、`cicd/argocd-image-updater/values*-override.yaml`、`cicd/argocd/values*.yaml`，并与 `argocd-apps/<cluster>/argocd.yaml` 的源配置核对。不要用历史的“CN staging 必须叫 vault-builder-backend”或“TKE 全部同集群 Kubernetes Auth”覆盖真实配置；Store 名称不等于权限域，仍须核查 server、auth 和 Vault policy，不能把 staging 指到生产密钥域。

staging Harbor 双入口依据用户于 2026-09-28 的最新确认及
[固定部署回执 ce9e5133](https://gitlab.addx.ai/DEV/k8s/-/blob/ce9e51334789c5accac3cb580e44cbb1685214c5/clusters/tencent-100052802231-cn-staging/harbor/private-entry/README.md)，
覆盖上方旧 Git 快照中的该地址。两域名访问同一 Harbor；内网为集群默认入口，SG 等外部同步使用
`-pub`。canonical token realm 仍使用 `-pub`，但在目标 VPC 内也解析到内网 CLB。
新私网镜像需要精确主机名的 pull-secret `auths` 条目。旧 `harbor-cn-staging.addx.live`
仍为兼容入口，runner / Image Updater / 业务镜像并未由域名新增自动迁移。
实际操作按 [Harbor 访问说明](../../../delivery/harbor/SKILL.md) 核验；不把固定回执当成本轮 live 验证。

CLB、证书、TLS、pull secret 也不能跨账号复制：

- staging 业务迁移计划明确旧 `existLbId` 在新账号需变更；引用的旧业务 overlay 路径可以不变，最终值来自 Application patches。`existLbId` 可能不可原地变更，发现迁移错误不等于获准删除/重建 Ingress。
- staging `cicd/bootstrap/ingress-certificate-references.yaml` 使用目标账号的证书引用；不能默认继承旧 `addx-live-2023-wgyxwfot`。
- tech-service ArgoCD values 当前使用 `kubernetes.io/ingress.existLbId` 和 `ingress.cloud.tencent.com/certificate`，TLS 终结在 CLB、`tls: false`。因此“TKE 必须有 spec.tls”不是跨集群通则。
- CBS 存储类也有差异：当前 staging Casdoor / Elasticsearch values 使用 `cbs`，tech-service Harbor / VictoriaMetrics values 使用 `cbs-topo`。从目标组件 values 与 live StorageClass 核验，不按 `cloud=tencent` 统一默认。
- 地址和 values 证明 Git 中的期望配置；运行时可达性、证书有效性、CLB 归属、安全组和完整迁移验收仍需只读实测。

## 已弃用或不再作为默认目标的映射

| 历史目录 / 目标 | 当前处理 |
|---|---|
| `aws-741924744516-cn-prod`、旧 AWS `cn-prod` | prod 目标改为 `tencent-100014919455-cn-main` |
| `aws-589899215075-cn-tech-service` | tech-service 目标改为 `tencent-100052802231-cn-tech-service` |
| `aws-801447536674-cn-staging` / `cn-eks-staging` | staging 目标改为 `tencent-100052802231-cn-staging`；2026-10-04 无业务、Crossplane provider 全部停用、runner 4001 暂停 |
| `aws-801447536674-cn-dev` / `cn-dev` / `cn-eks-dev` | 已弃用，EKS 控制面已删除；不新建 CN Dev 集群，开发联调改用 `staging-cn`（`tencent-100052802231-cn-staging`），`dev-cn` keyword 仍 STOP |
| `tencent-100050722703-cn-staging`（旧腾讯 `100014919455` 的 staging-cn VPC `vpc-jxxz6aiq` / `cls-i860hdh9`） | 旧 staging，不是当前新账号 staging；业务副本全部 0、ArgoCD 全部 `skip-reconcile`、指向其 CLB 的 DNS 已停用，仅待集群下线 |
| `tencent-100014919455-cn-tech-service` | 旧 tech-service 目录，不是当前新账号 tech-service |
| `cn-eks-*` / `arn:aws-cn:eks:*` | 仅在明确历史排查范围下使用，不作为默认连接或扫描 fallback |

**CN dev/staging 统一结论（2026-10-04）**：AWS `cn-eks-dev`、AWS `cn-eks-staging` 与旧腾讯
staging-cn VPC 的工作负载已全部收敛到 `tencent-100052802231-cn-staging`（`cls-riukakjb`）。
此结论**只**覆盖这三个 staging 来源；`cn-main`（prod 集群 `cls-kr3pti7p`）中少数长期存在的
`staging-cn` namespace 服务不属于本次迁移，按其 Application 实际 destination 单独处理，
不得改指新 staging，也不能当成“已迁移”。

CI：GitLab runner 4542（`tke-staging-runner`，位于 `cls-riukakjb`）在服务端持有
`tke-cn-staging-amd64`、`tke-staging-amd64`、`tke-staging` 及兼容 tag `cn-staging-amd64`、`cn-staging`；
AWS runner 4001 已暂停。runner 用 registration token 注册，tag 以 `GET /runners/<id>` 为准，
k8s values 中的 tags 不会自动同步到 GitLab。无 arm64 runner。

目录残留和迁移中的业务副本应单独核对。`cn-main` 中仍可能存在历史 staging 应用，新的 staging 也可能继续读取旧账号 CDB 或迁移代理；先查具体 Application、实际 endpoint 和数据依赖，不做账号 ID 的机械替换，不因“集群已迁移”删除保留依赖，也不反过来把这些依赖当作 AWS CN 集群仍是当前目标。
