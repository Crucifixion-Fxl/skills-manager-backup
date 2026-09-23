# recipes/sentry/

Sentry 自助接入为每个 target 生成 ConfigMap、ServiceAccount、Job、ExternalSecret 四类相互依赖的
app-scoped manifest，必须一起使用。ConfigMap 和 Job 各有 EKS / TKE 变体，避免单一模板被 hand-edit。

## 文件清单

| 文件 | 角色 |
|---|---|
| `onboard-config-eks.yaml.tmpl` | ConfigMap（**EKS**：填 8 个槽位含 instance / vault_addr 完整 URL / mount / env） |
| `onboard-config-tke.yaml.tmpl` | ConfigMap（**TKE cn-main**：固定 INSTANCE=cn-prod / VAULT_ADDR=vault-cn-internal / MOUNT=jwt-tke-cn-main；填 app / platform / team / sentry_app_type / target.env，staging 不得继承 ENV=prod）|
| `onboard-sa.yaml.tmpl` | ServiceAccount（EKS/TKE 通用；填 app，生成 `<app>-sentry-onboard`）|
| `onboard-job-eks.yaml.tmpl` | Job（**EKS**：填 app / image / is_aws_cn；AWS CN 4 个 EKS target 加 NAT egress NodePool）|
| `onboard-job-tke.yaml.tmpl` | Job（**TKE cn-main**：固定 harbor-cn/base/ image + projected vault-token + 显式 imagePullSecrets；填 app / 不可变 sentry_onboard_image_tag）|
| `externalsecret.yaml.tmpl` | ExternalSecret（EKS/TKE 通用；填 app / env / namespace / cluster_secret_store）从 Vault 拉 DSN 渲染 K8s Secret `<app>-sentry-dsn` |

workflow `add-sentry.md` Step 4 按 `cluster.cloud` 路由：`aws` → EKS 变体；`tencent` → TKE 变体。

ExternalSecret 的 ClusterSecretStore **不可按目标集群默认 CSS 猜**。Sentry DSN
由 sentry-onboard Job 写入 ConfigMap `VAULT_ADDR` 指向的 Vault；ExternalSecret
必须读同一个 Vault 实例。builder dev/staging 普通 app Secret 使用
`vault-builder-backend` 指向 builder Vault；cn-dev Sentry DSN 若仍由 Job 写入
ops Vault，就必须配置同实例 DSN CSS。填 `{{cluster_secret_store}}` 时必须查
`references/sentry/README.md` 的
`SENTRY_DSN_CSS`，不能从 `clusters.yaml -> vault_css` 或 store 名字推断。

## 跨模板设计：自助流程链路

跟 v1 老 `sentry-onboarding` skill（运维手动 REST API）区别：

| 维度 | 老 skill | 新自助流程（本目录） |
|---|---|---|
| 谁执行 | 运维 | 开发者在 MR 里声明 |
| 触发 | 手动跑 AI skill | ArgoCD sync 自动触发 |
| Project 创建 | 跑 skill 时一次 | Job 幂等，每次 sync 跑一次，自动 recover |
| DSN 交接 | skill 打印给运维手动写 Vault | Job 自动写 Vault → ES 自动注入 Pod |
| prod 批准 | 需运维账号 | Internal Integration Token 已有 Project:Admin scope，MR 即自助 |

**新应用 100% 走本流程；老 skill 不再用**（SKILL.md "不干的事" 段已明确）。

### ArgoCD sync 后的链路

```
ArgoCD sync
   ↓
sentry-onboard Job 跑（每次 sync 重跑，幂等）
   ↓ 用 SA JWT 换 Vault token
Job 读 secret/cicd/sentry/tokens/{instance} 拿 admin token
   ↓ 调 Sentry API POST /api/0/teams/{org}/{team}/projects/
Sentry 建 project（已存在 idempotent）
   ↓
Job 写 DSN 到 Vault platform 路径 secret/{env}/sentry/application/{app}/project
   ↓ refreshInterval=1m
ExternalSecret 读 DSN 渲染 K8s Secret <app>-sentry-dsn
   ↓
Pod env SENTRY_DSN 拿到（用 secretKeyRef）
```

## ⭐ schema 必须对齐（最常见的失败模式）

`onboard-config-{eks,tke}.yaml.tmpl` 的 `VAULT_PATH_SCHEMA` 跟 `externalsecret.yaml.tmpl` 的 `remoteRef.key` 必须 **一致**：

- 两边都 `platform` → 写 / 读路径 `secret/{env}/sentry/application/<app>/project`
- 两边都 `legacy` → 写 / 读路径 `secret/{env}/app/<app>/sentry-dsn`

**新接入应用强制 platform schema**（本 recipe 默认）。

不一致 = Job 写新路径 ExternalSecret 拉旧路径 = `<app>-sentry-dsn` Secret 永远空。详见 `troubleshooting/sentry-onboard-issues.md`。

## 共享 namespace 命名规则

新接入应用所有 Sentry 资源都必须 app-scoped。`prod-us` / `prod-eu` / `prod-cn`
这类共享 namespace 里，裸名会让多个 ArgoCD Application 争同一个资源。

| Kind | 名字 |
|---|---|
| ServiceAccount | `<app>-sentry-onboard` |
| ConfigMap | `<app>-sentry-onboard-config` |
| Job | `<app>-sentry-onboard` |
| ExternalSecret / target Secret | `<app>-sentry-dsn` |

禁止新接入继续使用裸名 `sentry-onboard`、`sentry-onboard-config`、`sentry-dsn`。

## ⚠️ 镜像路径：所有集群统一 `base/sentry-onboard`

sentry-onboard 现在是**每个集群都有的统一 `base/` 工具镜像**（由 DEV/base-images 扇出到全部 15 个目标 Harbor，含 3 个 staging EKS + eu-prod-data + TKE + 2 个 GKE）。**不再按集群挑路径**——一条规则覆盖全 fleet。（注：GKE Harbor 也收到镜像，但 `cloud==gcp` 的 Sentry 自助是独立的、当前仍未支持项 → STOP，见 workflows/add-sentry.md，与镜像无关。）一条规则：

```
<local-harbor>/base/sentry-onboard:<approved-sha>
```

- `<local-harbor>` = 本集群自己的 Harbor 主机（如 `harbor-00249-us-tech.addx.live`；TKE 是 `harbor-cn.addx.live`）。查 `references/data/clusters.yaml -> clusters[].harbor_url` 取本集群 host，拼上 `/base/sentry-onboard`。
- tag = 平台批准的不可变 SHA（当前 `a5b195bd9ea97e78bcb488c946d7c734cd22118a`，由 workflow 填 `{{sentry_onboard_image_tag}}`）；**禁止 `latest`**。
- Kyverno `require-harbor-image-path` 全 fleet 放行 `base/`（只拦 flat `cicd/<app>` 与 `library/`），统一 `base/` 路径合规、无需任何豁免。

> 历史：早先 sentry-onboard 走 per-cluster `cicd/<env>-<region>/sentry-onboard`，且 staging EKS / eu-prod-data 因 CI 无对应 build job 而**无镜像 → STOP**。现已被 base-images 扇出统一为 `base/`，**那个 staging / eu-data 缺口已修复，这些集群现在可以正常接 Sentry**。

## ⚠️ AWS CN 4 个 EKS target 必须 NAT egress 调度

`cn-prod` / `cn-tech-service` / `cn-dev` / `cn-staging` 的 Pod 默认走节点 EIP 出网，**不在** Sentry CLB 白名单里，会 timeout/504。Job 模板必须加 NodePool 调度：

| 集群 | nodeSelector | toleration |
|---|---|---|
| 589 cn-tech-service | `node-group: nat-egress` | `dedicated=nat-egress` |
| 741 cn-prod | `node-group: sentry-egress` | `dedicated=sentry-egress` |
| 801 cn-staging | `node-group: nat-outbound` | `dedicated=nat-outbound` |
| 801 cn-dev | `node-group: nat-outbound` | `dedicated=nat-outbound` |

US/EU/TKE 集群不需要（走 internal ALB/CLB 本内网）。

## C 端应用 DSN 域名改写

公司多品牌（kiwibit / vicoo / vicohome / safemo），C 端能看到的 DSN 不能含 `addx.live`（品牌关联暴露）：

| Sentry APP_TYPE | 使用场景 | Prod DSN 域名 |
|---|---|---|
| `backend` | 后端 Pod 内 SDK | 原 DSN；必要时用 `DSN_HOST` 指定已验证的 ingest host |
| `admin` | 内部管理后台 | 同上 |
| `mobile-app` | DSN 随 App 二进制发布 | **必改写** `glitch-{region}.{brand}.{tld}`，要填 BRAND |
| `web-frontend` | DSN 进前端 JS bundle | 同上 |

Staging 不自动使用品牌 relay；可用 `DSN_HOST` 指定已验证的 ingest host。C 端 prod 的品牌 relay 优先级更高，不能用 `DSN_HOST` 绕过 BRAND 或 CN relay 限制。

## 独立项目与事件入口

两种 ConfigMap 模板都提供可选 `PROJECT_SLUG`、`DSN_HOST` 注释槽位。只有确认目标不可变镜像已支持后才启用：前者默认 `APP`，后者默认不覆盖 host；K8s 资源名和 Vault 路径始终使用 `APP`。staging 迁入 prod 时复用同应用项目，以 SDK environment 区分并保留 `ENV=staging`，完整前置和旧 Job 防回写步骤见 [项目与实例迁移](../../references/sentry/project-migration.md)。

新接入由 workflow 读取 `references/data/sentry-instances.yaml`：staging 显式启用两字段，使用本区域 prod 实例和 `<app>`。backend/admin 用已验证区域私网 relay；staging 移动端/浏览器必须验证品牌公网 ingest 和实际终端可达后显式填 DSN_HOST，不能回退到私网 relay，BRAND 自动改写仅对 prod 生效。模板保留可选形态是为了兼容历史配置，不允许生成器忽略 workflow 的 staging 必填接线。TKE cn-main 的 ENV 来自 target，staging/prod 共用 ops Vault 但路径首段分别保持 staging/prod。

`BRAND` 必填条件：Sentry `APP_TYPE ∈ {mobile-app, web-frontend}` 且 `ENV=prod`。
值：`kiwibit` / `vicoo` / `vicohome` / `safemo`。

⚠️ **cn 区 prod 当前无品牌 relay 域名** —— C 端应用暂不能接 cn-prod，先用 us/eu region 或等运维建 cn 区 relay。

## Sentry team slug 纪律

`TEAM` 是目标 Sentry org 里的真实 team slug，不是固定默认值。新接入前必须从目标
Sentry 实例的 team 列表确认；prod org 常见 `backend` / `frontend`，staging / legacy
org 也可能不同，以实际查询结果为准。

如果 Job 日志里 `GET /api/0/teams/{org}/{team}/projects/` 返回 404，通常不是 project
slug 问题，而是 `TEAM` 在该 org 不存在。先修 ConfigMap 的 `TEAM`，再重跑 hook。

## 关联

- workflow: `workflows/add-sentry.md`
- 失败模式诊断: `troubleshooting/sentry-onboard-issues.md`
- Vault platform 定义: `references/vault-paths/platforms.yaml -> platforms.sentry`
- 运维前置: references/sentry/README.md（Vault JWT role / admin token / NAT egress NodePool / CoreDNS rewrite）
