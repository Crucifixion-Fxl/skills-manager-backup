# references/

`references/` 保存 workflow、playbook 和 recipe 共用的稳定事实、约束和 resolver。
它不保存可直接生成的 YAML（放 `recipes/`），也不保存一步步执行程序（放 `workflows/`）。

## 权威来源

| 位置 | 用途 |
|---|---|
| `data/routes-build.yaml` | Build 意图、workflow、internal calls 与 planned stop 的唯一路由表 |
| `data/routes-troubleshoot.yaml` | 故障症状、playbook、相关 validator 的唯一路由表 |
| `data/clusters.yaml`、`data/env-keywords.yaml` | 集群、环境、Harbor、Vault CSS、runner 和 ArgoCD 事实 |
| `data/stop-conditions.yaml`、`data/hard-rules.yaml` | 全局停止条件和不可违背的部署规则 |
| `data/*boundary*.yaml`、`data/resource-handling-rules.yaml` | 源码仓边界、权限和资源归属 |
| `cost-tiering/` | 按资源和环境确定规格、immutable 字段和 prod 门禁 |
| `vault-paths/` | Vault 实例、路径规则和 resolver |
| `cluster-access/`、`object-storage/`、`shared-middleware/` | 领域前置条件和受支持能力 |
| `provider-release/` | 受保护 Crossplane Provider 内部发布基础设施的固定 runner、Harbor、credential mount、verifier、scanner、immutable retention 和 GitOps write-set 合同 |
| `logging/`、`sentry/`、`db-migration/`、`image-automation/`、`tdd-platform/` | 专题事实和约束 |
| `grafana/` | Dashboard-as-Code target repository、隔离 checkout、数据源解析与安全边界 |
| `victoriametrics/` | 应用仓 scrape 资源的所有权、最小约束与平台边界 |
| `review.md` | Review/Scan 模式的只读输出合同 |
| `deployment-tracking.md`、`ops-escalation.md` | 部署执行留痕与平台阻塞时的运维协调合同 |

## 使用规则

- workflow 必须读取它所引用的权威文件；不得从本 README、历史案例或手写计数推导事实。
- 路由、集群、平台枚举和 Vault 规则发生变化时，修改对应 YAML，不在 README 维护镜像表。
- Grafana Dashboard JSON 校验始终由目标 Dashboard 仓库 `scripts/` 执行；本 skill 不复制该
  仓库 policy，也不访问 Grafana 或其凭据。
- 事实无法唯一解析时 STOP；不要选择“最像”的环境、集群、Vault 路径或权限边界。
- 历史审计、事故复盘和迁移过程不属于运行时 skill 内容；保留在提交历史或独立设计记录中。

## 维护

新增专题时，先确认它是共享事实而不是模板或 workflow；将入口放在这里并让具体 workflow
显式引用。删除专题前先更新其 route、workflow、playbook 和本地链接，再运行 route 自检与
Markdown 链接测试。
