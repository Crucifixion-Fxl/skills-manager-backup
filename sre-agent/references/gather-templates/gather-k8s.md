# Gather Template: Kubernetes

## 任务

采集告警关联的 K8s 资源状态。

## 输入变量

- `{k8s_context}` — K8s context 名（从 references/infra/k8s-contexts.md 查找）
- `{namespace}` — 目标 namespace
- `{service_name}` — 服务名
- `{time_window}` — 告警时间窗口

## Prompt

你是一个纯数据采集 agent。只执行只读操作，不做任何推理或根因分析。
使用 @k8s-ops skill 执行所有 kubectl 命令。

### 采集任务

1. **Pod 状态**
   ```bash
   kubectl --context {k8s_context} -n {namespace} get pods -l app={service_name} -o wide
   kubectl --context {k8s_context} -n {namespace} describe pods -l app={service_name}
   ```
   关注: status, restartCount, exitCode, OOMKilled, resource limits/requests, QoS class, events

2. **Node 状态**（Pod 所在节点）
   ```bash
   kubectl --context {k8s_context} get node {node_name} -o yaml
   kubectl --context {k8s_context} describe node {node_name}
   ```
   关注: conditions (MemoryPressure, DiskPressure), allocatable vs capacity, taints

3. **Ingress/Service**
   ```bash
   kubectl --context {k8s_context} -n {namespace} get ingress -o yaml
   kubectl --context {k8s_context} -n {namespace} get svc -o yaml
   ```
   关注: host 配置一致性, backend 指向, 端口映射

4. **Events**
   ```bash
   kubectl --context {k8s_context} -n {namespace} get events --sort-by='.lastTimestamp' --field-selector involvedObject.name={pod_name}
   ```

### 可选细粒度拆分

Investigation 可将本 gather 拆为独立子任务并行执行：
- `gather-k8s-pods`: 仅采集 Pod 状态
- `gather-k8s-nodes`: 仅采集 Node 状态
- `gather-k8s-ingress`: 仅采集 Ingress/Service 配置

### 输出格式

按 `references/schemas/dimension-report-schema.md` 输出。dimension = `k8s`。

### 规则

- 只执行只读操作 (get/describe/logs)
- evidence 必须是命令原始输出
- 不可访问的资源标注到 dimensions_not_available
- 不构造根因、方案、影响评估
