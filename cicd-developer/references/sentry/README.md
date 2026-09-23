# 集群前置 + Sentry 自助集群配置表

`workflows/add-sentry.md` 跑出来的 manifest 能起来，依赖**集群侧前置已就绪**。新集群首次使用前**运维需做** 7 项。

跟 `recipes/sentry/README.md` 的分工：
- 本文件：**集群侧前置 + per-cluster 配置事实**（VAULT_ADDR / mount / 镜像路径 / TKE vs EKS 差异）
- recipes README：**模板使用 + 跨模板设计原则**（流程链路 / 统一 `base/` 镜像规则 / CN NAT 调度）
- `references/data/sentry-instances.yaml`：新接入的区域 prod 实例、relay 和按应用环境区分的项目名；不改变本表的 Vault 域与路径

## 7 项集群前置（运维配，切换实例时重新核验相关项）

| # | 前置项 | 做什么 | 如何验证 |
|---|---|---|---|
| 1 | **镜像可达** | 平台批准的 `base/sentry-onboard:<immutable-tag>` 在本集群 Harbor —— 统一 `<local-harbor>/base/sentry-onboard`（DEV/base-images 扇出到**所有**集群，含 staging EKS + eu-prod-data + TKE） | 节点 `docker pull <local-harbor>/base/sentry-onboard:<immutable-tag>` 成功 |
| 2 | **Vault policy** | `sentry-onboard` 存在（[vault-policies/policies/ops/shared/sentry-onboard.hcl](https://gitlab.addx.ai/DEV/vault-policies/-/blob/main/policies/ops/shared/sentry-onboard.hcl)）| `vault read sys/policies/acl/sentry-onboard` |
| 3 | **Vault auth role** | 集群对应 JWT mount 下 role `sentry-onboard`，绑定 policy `sentry-onboard`，并允许 app-scoped SA：`bound_claims_type=glob`，`bound_claims.sub=system:serviceaccount:*:*-sentry-onboard`，`bound_audiences=[https://kubernetes.default.svc]`。TKE cn-main 使用 `jwt-tke-cn-main`，不要回退到 native `kubernetes` allow-list。 | `vault read auth/<mount>/role/sentry-onboard`；用目标 namespace 跑一次 `<app>-sentry-onboard` projected token login smoke |
| 4 | **CoreDNS rewrite** | sentry-* hostname 指向本区域 internal ALB/CLB（见 [k8s/clusters/*/cicd/coredns/coredns-patch.yaml](https://gitlab.addx.ai/DEV/k8s/-/tree/master/clusters)） | Pod 内 `dig sentry-{us,eu,cn}.addx.live` 返回本 VPC 内网 IP |
| 5 | **Sentry DSN ClusterSecretStore** 已配且可用 | 必须指向 sentry-onboard Job 写入 DSN 的 Vault 实例；不要用目标集群默认 app CSS 名字猜 Vault 域 | `kubectl get clustersecretstore <SENTRY_DSN_CSS>` READY=True |
| 6 | **（仅 AWS CN）NAT 出口 NodePool** | 589 的 `nat-egress` + 741 的 `sentry-egress` + 801 的 `nat-outbound` NodePool | `kubectl get nodepool -A \| grep -E 'nat-egress\|sentry-egress\|nat-outbound'` |
| 7 | **实际 Vault 的 Sentry endpoint/token** | Job 的 `VAULT_ADDR` 指向的 Vault 中，`secret/cicd/sentry/endpoints` 和 `secret/cicd/sentry/tokens` 都含目标 `INSTANCE` 的字段；builder 与 ops Vault 分别核验，不能因 ops 中存在而假定 builder 已有 | 使用该 Job 身份验证读权限及目标 key 存在，仅报告 key/状态；不回显 token/DSN。管理 API 与事件 ingest 分别实测 |

## 集群配置事实

表中列出的 EKS/TKE 集群可按 `workflows/add-sentry.md` 自助接入；**cn-dev** 仍因
`SENTRY_DSN_CSS` builder cutover 未确认而 STOP。GCP 的 Sentry 自助尚未支持，即使 Harbor
已有镜像也不能据此放行。

直接用作 ConfigMap 的 `VAULT_ADDR` / `VAULT_K8S_MOUNT` 以及 ExternalSecret 的 `SENTRY_DSN_CSS`（EKS 用 `recipes/sentry/onboard-config-eks.yaml.tmpl`；TKE cn-main 用 `recipes/sentry/onboard-config-tke.yaml.tmpl` 已预设 vault-cn-internal + mount=jwt-tke-cn-main）：

| 集群 | VAULT_ADDR | VAULT_K8S_MOUNT | SENTRY_DSN_CSS | 额外要求 |
|---|---|---|---|---|
| aws-002 us-tech-service | `https://vault-us-internal-new.addx.live` | `jwt-eks-tech-service` | `vault-backend` | — |
| aws-302 us-prod | `https://vault-us-internal-new.addx.live` | `jwt-eks-prod` | `vault-backend` | — |
| aws-769 us-data | `https://vault-us-internal-new.addx.live` | `jwt-prod-data` | `vault-backend` | — |
| aws-769 eu-data | `https://vault-eu-internal.addx.live` | `jwt-prod-data` | `vault-backend` | — |
| aws-010 eu-tech-service | `https://vault-eu-internal.addx.live` | `jwt-eks-tech-service` | `vault-backend` | — |
| aws-740 eu-prod | `https://vault-eu-internal.addx.live` | `jwt-eks-prod` | `vault-backend` | — |
| aws-390 us-staging | `https://vault-us.builder.addx.live` | `jwt-eks-us-staging` | `vault-builder-backend` | — |
| aws-390 eu-staging | `https://vault-eu.builder.addx.live` | `jwt-eks-eu-staging` | `vault-builder-backend` | — |
| aws-589 cn-tech-service | `https://vault-cn.addx.live`（公网走 NAT） | `jwt-eks-tech-service` | `vault-backend` | **nodeSelector**: `node-group=nat-egress` + toleration |
| aws-741 cn-prod | `https://vault-cn.addx.live`（公网走 NAT） | `jwt-eks-data-ops` | `vault-backend` | **nodeSelector**: `node-group=sentry-egress` + toleration |
| aws-801 cn-staging | `https://vault-cn.builder.addx.live`（公网走 NAT） | `jwt-eks-cn-staging` | `vault-builder-backend` | **nodeSelector**: `node-group=nat-outbound` + toleration |
| aws-801 cn-dev | `https://vault-cn.addx.live`（公网走 NAT） | `jwt-eks-dev` | **STOP: re-verify after builder cutover** | 普通 app Secret 已使用 `vault-builder-backend` 指向 builder Vault；继续使用 ops Vault 写 DSN 前必须先提供同实例 DSN CSS，或把 DSN 写入 builder Vault |
| tencent cn-main (TKE) | `https://vault-cn-internal.addx.live` | `jwt-tke-cn-main` | `vault-backend` | Image 用 `harbor-cn.addx.live/base/sentry-onboard`；显式 `imagePullSecrets`；使用 projected `vault-token` |

**为什么 AWS CN 用公网 Vault 域名**：AWS CN VPC 与腾讯云 TKE 无 peering。`vault-cn-internal.addx.live` 只在 TKE 集群内解析到内网 IP；AWS CN Pod 走 NAT 出口（NAT EIP 已在 Vault SG 白名单）访问公网 `vault-cn.addx.live`。

**为什么这里不直接用 `clusters.yaml -> vault_css`**：Sentry DSN 是 sentry-onboard Job 写入的 platform 凭据，ExternalSecret 必须读同一个 Vault 实例。builder staging 三集群写读都走 builder Vault，可用 `vault-builder-backend`；cn-dev 仍是例外，若 sentry-onboard 继续写 `vault-cn.addx.live`，必须先提供指向 ops Vault 的专用 DSN CSS，不能复用普通 app 的 builder CSS。

## ⚠️ TKE cn-main 与 EKS 模板的差异（必逐项核对）

**复制 EKS overlay 改 namespace 是不够的**。任何一项跟集群类型不匹配，Job 都跑不起来：

| 字段 | EKS 模板 | TKE cn-main 模板 |
|---|---|---|
| `VAULT_ADDR` | `https://vault-<region>-internal[-new].addx.live` | `https://vault-cn-internal.addx.live` |
| `VAULT_K8S_MOUNT` | `jwt-eks-prod` / `jwt-eks-tech-service` 等 | `jwt-tke-cn-main` |
| `VAULT_K8S_TOKEN_PATH` | `/var/run/secrets/vault/token` | `/var/run/secrets/vault/token` |
| Job container `image` | `<local-harbor>/base/sentry-onboard:<immutable-tag>`（统一 base/，每集群自己的 Harbor host） | `harbor-cn.addx.live/base/sentry-onboard:<immutable-tag>`（同一条 base/ 规则） |
| Job `nodeSelector` | EKS 内网默认即可；AWS CN 4 个 EKS target 额外加 NAT NodePool | 只设 `kubernetes.io/arch=amd64`；**不设 AWS CN NAT NodePool** |
| Job `volumes: vault-token` projected SA token + `volumeMounts` | 必须设 | 必须设（audience `https://kubernetes.default.svc`） |
| `imagePullSecrets` | **必须显式** `harbor-registry-secret`（hard rule #24 对 hook Job pod 同样适用；EKS 虽有 Kyverno mutate 兜底，模板与 add-sentry [validate] 仍要求显式写） | **必须显式**（TKE 无 mutation webhook） |

> **不在此表的字段**（TKE/EKS 契约一致）：`APP` / 可选 `PROJECT_SLUG`、`DSN_HOST` / `PLATFORM` / `INSTANCE` / `TEAM` / `APP_TYPE` / `ENV` / `BRAND` / `VAULT_K8S_ROLE` / `VAULT_PATH_SCHEMA`（新接入应用统一 `platform`）。ServiceAccount 名不是固定值，必须是 `<app>-sentry-onboard`。跨实例迁移时不要把 `APP` 换成项目名，详见 [项目与实例迁移](project-migration.md)。

## 对照表如何反查

**mount 名跟集群同名 ≠ 同 mount，仅凭名字猜易张冠李戴** —— 必须按下表**双向核对 mount ↔ OIDC ID**，不能只看 mount 名推断属于哪个集群。

CN 三行（aws-589 / aws-741 / aws-801）`vault-cn` 网络访问受限，未交叉验证，**新接入前用下面命令再核一次**：

```bash
# 1. 取目标集群的 OIDC issuer 后 32 位
kubectl --context=<cluster> create token default | cut -d. -f2 | base64 -d | jq -r .iss
# 输出形如 https://oidc.eks.<region>.amazonaws.com/id/0209FEA8BC39906A3C61A505CB7804D1

# 2. 列出对应 Vault 实例所有 jwt mount 的 oidc_discovery_url
for m in $(vault read -format=json sys/auth | jq -r '.data | to_entries[] | select(.value.type=="jwt") | .key'); do
  url=$(vault read -format=json auth/${m%/}/config | jq -r .data.oidc_discovery_url)
  echo "  ${m%/} -> $url"
done
# 找 url 后 32 位匹配集群 issuer 的 mount，即正确的 VAULT_K8S_MOUNT
```

## 关联

- `workflows/add-sentry.md`：消费本文件的配置表填模板
- `recipes/sentry/`：四类资源的 EKS/TKE 模板变体 + 统一 `base/` 镜像规则 + CN NAT 调度
- `troubleshooting/sentry-onboard-issues.md`：失败模式诊断（多种症状回指本表 mount / image 配置）
