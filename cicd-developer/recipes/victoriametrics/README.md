# VictoriaMetrics scrape recipes

这两个带 slot 的模板用于**应用自己的 Kustomize overlay**，不属于 Grafana Dashboard
仓库，也不创建或配置 VictoriaMetrics 平台组件。

- 默认选择 `vm-service-scrape.yaml.tmpl`：已有 Service 暴露命名 metrics port 时使用；
  它只会发现匹配 selector 的 Service。
- 只有没有合理 Service、且开发者明确说明必须按 Pod 发现时，才使用
  `vm-pod-scrape.yaml.tmpl`。

两者都必须使用现有 Workload / Service 的精确标签和命名端口，并由
`workflows/manage-victoriametrics-scrape.md` 的全 overlay render gate 验证。
不要写 IP、域名、静态 target、Bearer Token、Basic Auth 或任意 Grafana 连接信息。

`gke-managed-dcgm-exporter-vm-pod-scrape.yaml.tmpl` 是唯一的无 slot 静态例外：
它只对应 GKE 管理的 `gke-managed-system/dcgm-exporter`，只能复制到已有的
`DEV/k8s` cluster-owned `victoria-metrics-config` Argo directory source。该 source
没有也不应新建 Kustomization。不要把它当作跨 namespace 通用模板；必须先完成 workflow
规定的 GKE、CRD、精确 Pod 和 `metrics:9400` live read-only evidence，且只能以
`--repo-context k8s` 校验该闭合 profile。

## Application alert rules

`vm-alert-rule.yaml.tmpl` belongs only to `workflows/manage-vmalert-rules.md`.
Its destination is the registered evaluator namespace; its alert namespace label
is the business namespace. Validate slot substitution and YAML quoting with
`validators/check_vmalert_rules.py`; do not use it as a scrape recipe.
