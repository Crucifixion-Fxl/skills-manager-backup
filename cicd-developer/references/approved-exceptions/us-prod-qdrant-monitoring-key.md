# us-prod qdrant 只读监控 key：单个 ExternalSecret

Work Item: https://gitlab.addx.ai/engineering/skills/-/issues/366
Root Issue: https://gitlab.addx.ai/DEV/k8s/-/issues/158
Consumer MR: https://gitlab.addx.ai/DEV/k8s/-/merge_requests/3434

平台 owner 于 2026-10-26 直接批准的固定单例，不是新的应用接入路线，也不是可由调用方
选择的 policy waiver。

## 为什么需要例外

us-prod 老 Prometheus 下线前，VictoriaMetrics 要接管 `qdrant` 采集（prod-us 3 + pre-us 1
个 target）。qdrant `/metrics` 要求只读 API key。该 key 由 ALGO/flywheel!409 的 PushSecret
`flywheel-qdrant-monitoring-key-push`（prod-us，`IfNotExists`）写入 Vault 业务路径。
VMAgent 只能引用 `victoria-metrics` namespace 内的 Secret；应用仓自助采集流程
（`workflows/manage-victoriametrics-scrape.md`）不接受 Bearer 鉴权，通用能力不存在。
hard-rule 36 的默认禁令因此在这里命中，DEV/k8s !3434 的 code-review（job 2345201）
据此判红线；该判断本身正确，保留为证据。

## 固定范围

唯一放行的来源是 `DEV/k8s`（`--repo-context k8s`）中的
`clusters/aws-302571458622-us-prod/victoria-metrics-config/qdrant-monitoring-key.yaml`。
同目录的 [固定 fixture](us-prod-qdrant-monitoring-key.yaml) 与源文件字节相同，完整文件
SHA-256 为 `3c01a87c18a5e89819c82c11483b99636bfb3c76581484277d96c73ac54ed2a1`。

| 字段 | 值 |
|---|---|
| Kind | `external-secrets.io/v1` ExternalSecret，仅 1 个文档 |
| Name / namespace | `qdrant-monitoring-key` / `victoria-metrics` |
| Store | `ClusterSecretStore/vault-backend`（既有，不改策略） |
| Vault | KV v2 `prod/qdrant/application/flywheel-aws/monitoring-apikey`，property `read-only-api-key` |
| Target | 同名 Secret，key `read-only-api-key`，`Owner` / `Retain` |

消费方是同目录 middleware.yaml 里的 `VMScrapeConfig/qdrant-cec82e12`，只按 key 名引用。
key 为 qdrant 只读 key，不能写入或管理 collection。

## 边界

- check_repo_boundary.py 对上述路径做完整文件 hash 闭合校验；任何字段、文档数、
  格式变化、symlink 或换路径都不继承本例外。
- 不放行其它 qdrant 环境（eu、gcp、cn、pre 独立 key 等）、其它业务 Vault 路径、
  PushSecret、SecretStore、RBAC 或 Vault policy 变更。
- flywheel 若轮换只读 key，PushSecret `IfNotExists` 不会覆盖 Vault；需由 flywheel owner
  更新 Vault 值，本文件无需改动。
- 本合同只是 review 证据，不授权部署，不代替 ESO `SecretSynced`、4 个 target `up=1`
  和告警输入的运行验收。
