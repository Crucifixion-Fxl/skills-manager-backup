# recipes/k8s/

原生 Kubernetes 产物模板：workload、Service、Ingress、ConfigMap、ExternalSecret、ServiceAccount、
Kustomization、guarded one-shot Job 和 suspended CronJob。具体 slot 和资源字段以对应模板头部为准，不在这里维护
会漂移的文件清单。

普通 guarded one-shot 使用单容器模板。固定任务镜像没有 shell，或任务退出后仍需通过
Kubernetes API 交接 artifact 时，使用 artifact-handoff 模板；它固定
`artifact-stage` / `run` / `artifact-relay` 身份并由同一个 validator 覆盖所有 regular
和 init container 的资源与安全上下文。

周期任务使用 `suspended-cronjob.yaml.tmpl`，并完整执行
`workflows/new-cronjob.md`。新 source 固定 suspended；不得单独复制模板后直接激活。

## 共同契约

- 新的无状态应用默认使用 Rollout；有状态服务只使用其 workflow 明确支持的 StatefulSet recipe。
- workload 的 pod template 必须有 `app` 和 `env` label。base 提供 `app`，overlay patch 注入
  `env`，因为 kustomize labels block 不会可靠地下沉到 Rollout/StatefulSet pod template。
- Service selector 必须选中同一 app 的 workload；内部服务保持 ClusterIP，Ingress 的暴露条件由
  `workflows/add-ingress.md` 决定。
- 非敏感配置使用 ConfigMap；凭据通过对应 ExternalSecret recipe 从 Vault 读取，不能 inline。
- IRSA 与普通 ServiceAccount 是不同合同：只有明确需要 AWS workload identity 时才使用
  `serviceaccount-irsa.yaml.tmpl`。
- 新的 `platform.addx.io/v1alpha1 Database` shared claim 使用
  `shared-database-claim.yaml.tmpl`：`platform.addx.io/app-slug` 保留 canonical kebab-case
  app，`spec.app` 仅由它把 `-` 机械替换为 `_` 得到；标准 `{phase}-{app}` namespace 的后缀
  必须等于该 slug。需要同一 owner 的独立 PostgreSQL role/database 时，保持这两个 owner
  字段不变，只填 PostgreSQL-only lower_snake `spec.purpose`；平台派生
  `<spec.app>_<purpose>`，Vault key 固定为 `postgres-<purpose-as-kebab>`。禁止填 host、
  administrator、Vault path、owner 或 privileges。

日志接入见 `references/logging/README.md`；日志丢失诊断见
`troubleshooting/log-pipeline-silent-loss.md`。
