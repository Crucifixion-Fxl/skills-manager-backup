# source-location / view-url / edit-url + B 类 annotations 完整规则

> SKILL.md §3 提到的 annotations 详细。看这个 reference 当：写 catalog-info.yaml 的 annotations 段、Backstage 实体页 "View Source" 跳错、需要查所有可挂注解列表。

## hard rule — source-location / view-url / edit-url 必须 per-entity 显式覆盖

默认从 `managed-by-location` 派生 → 整仓所有实体的「View Source」都跳 `catalog-info.yaml`，不精准。每个实体显式覆盖：

**写法纪律**（**指向哪 = 扫仓库实际文件结构来定，不套模板**）：

| 实体类 | 怎么扫 |
|---|---|
| Component | 扫这个 Component 的代码"最有代表性的入口"在哪：可能是某个子目录、也可能是某个具体文件（`cmd/<svc>/main.go`、`pubspec.yaml`、`package.json`）。**不要默认一个不存在的子目录**——扫不到精确入口就指 README 所在目录。指文件用 `-/blob/...`，指目录末尾带 `/` 用 `-/tree/...` |
| Resource | 扫它的 manifest 在哪（`k8s/` `crossplane-claims/` `helm/values*.yaml` `terraform/` `prisma/schema.prisma` …）。找不到精确文件就指它所在目录（末尾带 `/`）+ description 加 TODO「manifest 待补」 |
| API | 不显式加（`definition.$text` 已隐含定位它的契约文件） |
| System / Domain | 一般不加（无单一 source 文件） |

写法：

```yaml
annotations:
  backstage.io/source-location: url:https://gitlab.addx.ai/<group>/<repo>/-/tree/<branch>/<subdir>/   # url: 前缀 + 末尾 /
  backstage.io/view-url: https://gitlab.addx.ai/<group>/<repo>/-/tree/<branch>/<subdir>/              # 不带 url: 前缀
  backstage.io/edit-url: https://gitlab.addx.ai/<group>/<repo>/-/edit/<branch>/<subdir>/<entry>.md    # 指代表性入口（README.md 优先）
```

分支 = 仓当前 catalog dev 分支（如 `add-catalog-info`），merge 到 `main` 后批量改 `main`。**实战样板**：`services/value-added/engagement` 的 `catalog-info.yaml`（4 Component + 4 Resource 全部显式覆盖；MR !126）。

## TechDocs 注解 — 见 [techdocs-setup.md](./techdocs-setup.md)（Approach A vs B + `techdocs-entity` 深链规则）

## 其它注解（按需）

- [ ] `backstage.io/adr-location: <path>` —— **有 ADR 的 Component 必填**
- [ ] `a4x.io/cicd-app-name: <app>` —— **join key** = `DEV/argocd-apps` 里那个 ArgoCD `Application` 的 `metadata.labels.app`（如 `customer-care-api`）。门户靠它把集群里 Crossplane 资源 / ArgoCD App / IAM Role 连回 Component（`infra/backstage#19`）。⚠️ Component 名可能 ≠ app 名（仓 `services/customer-care` 产出 `customer-care-api`）
- [ ] `a4x.io/troubleshooting-id: <service>`（可选）—— 自建 troubleshooting 平台标识（fallback 到 `kubernetes-id` / `metadata.name`，`infra/backstage#16`）
- 部署后取消注释填实际值：`backstage.io/kubernetes-id` / `argocd/app-name` / `sentry.io/project-slug` / `prometheus.io/rule|alert` / `pagerduty.com/integration-key` / `grafana/dashboard-selector|alert-label-selector` / `nexus-repository-manager/*`（library）/ `crashlytics/*` + App Store / Google Play（mobile-app）/ `memfault/*`（firmware）。K8s 资源也要带 label `backstage.io/kubernetes-id: <app>` 才能被 K8s 插件抓到。

## Resource source-location 必须指 manifest（hard rule）

Resource（database / s3-bucket / kafka-topic / …）的 source-of-truth 在 GitOps 仓的 manifest，**不是** `catalog-info.yaml`：典型 Crossplane Claim（RDS / S3 / IAM）/ K8s native manifest（in-cluster Postgres/Redis）/ Terraform/Helm values / Prisma schema/migration。

Resource 实体必须**显式覆盖** `view-url` + `source-location` 指它的 manifest（否则跳 catalog-info.yaml，对查 RDS spec 的人毫无用处）：

```yaml
kind: Resource
metadata:
  name: engagement-mysql
  annotations:
    backstage.io/source-location: url:https://gitlab.addx.ai/services/value-added/engagement/-/blob/<branch>/backend/k8s/base/mysql-claim.yaml
    backstage.io/view-url: https://gitlab.addx.ai/services/value-added/engagement/-/blob/<branch>/backend/k8s/base/mysql-claim.yaml
spec: { type: database }
```

**找不到精确文件就指目录**（`backend/k8s/base/` 末尾带 `/`）+ description 加 TODO「Crossplane Claim 待补」。**将来 B 类自动发现**：TeraSky Crossplane Resources 插件 / Kubernetes Ingestor（`infra/backstage#19`）接入后，运行态字段（RDS endpoint、bucket 名、ARN）从 k8s API 自动 sync —— 现在没接，暂手维护。

## Crossplane Resource 注解

Crossplane 开的资源：加注解 `terasky.backstage.io/crossplane-claim: <namespace>/<claim-name>` 指向集群 Claim（TeraSky Crossplane Resources 插件，见 `infra/backstage#19`）。
