# recipes/

Recipe 是可渲染的产物模板：workflow 提供 slot，recipe 决定输出文件的结构。稳定事实和
决策表属于 `references/`，不应复制到模板或本 README。

## 使用合同

1. workflow 必须明确指出 recipe 路径和每个填入的 `{{slot}}`。
2. 只能填 slot 或使用该 recipe 说明的变体；不得为“方便”重画 YAML、删字段或把模板改造成
   另一种资源。
3. 新资源 kind 没有匹配 recipe 时 STOP 并产 Ops Todo，不能以 inline YAML 补洞。
4. 渲染 YAML 后必须通过该 workflow 声明的 validator；完整的 manifest 集合再用
   `validate.sh` 校验。

## 目录

| 目录 | 产物 |
|---|---|
| `k8s/` | 原生 workload、Service、Ingress、ExternalSecret、ServiceAccount、Kustomization |
| `argocd/` | Application |
| `crossplane/` | Crossplane managed resources、ProviderConfig、IAM 和数据连接链路 |
| `clickhouse/` | 当前受支持的 ClickHouse StatefulSet base、专用 overlay 与 Password Generator/ExternalSecret 链 |
| `sentry/` | Sentry onboarding 资源 |
| `db-migration/` | PreSync migration Job |
| `grafana/` | Grafana Dashboard JSON source template |
| `victoriametrics/` | 应用仓 VMServiceScrape / VMPodScrape 与已注册业务 VMRule template |
| `ci/` | GitLab CI 和 Dockerfile |
| `docs/` | cd-requirements、summary 和 Ops Todo 输出 |

各 area 的局部设计约束放在同目录 README；请以实际文件和 workflow 引用为准，不维护手写
文件数量或重复目录清单。Grafana 与 VictoriaMetrics 模板只由各自命名的 workflow 使用。
