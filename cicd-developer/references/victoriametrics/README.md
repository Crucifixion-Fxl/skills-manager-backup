# VictoriaMetrics scrape contract

`VMServiceScrape` and the exceptional `VMPodScrape` are normally application-owned
declarative manifests. Put them in the same application repository and target
Kustomize overlay as the Service / Workload being scraped. They are not
Grafana Dashboard sources and do not belong in
`DEV/grafana-dashboards-as-code`.

The current in-use contract is VictoriaMetrics Operator
`operator.victoriametrics.com/v1beta1`, with a named Service/container port,
`/metrics`, and a minimum `30s` interval. A `VMServiceScrape` must match one
specific in-namespace Service after the exact overlay is rendered; broad
selectors are rejected. `VMPodScrape` is only for the documented no-Service
case and must match one concrete Rollout, Deployment, or StatefulSet.

## GKE-managed DCGM: the sole cluster-owned exception

The only exception is the exact GKE-managed
`gke-managed-system/dcgm-exporter`. It is not an application workload and its
fixed `VMPodScrape` belongs only in the existing cluster-owned
`DEV/k8s` `victoria-metrics-config` Argo directory source. That source is raw
Argo directory content: do not create, infer, or require a `kustomization.yaml`.
The profile is a closed contract: the source namespace, exporter label, `metrics`
port, endpoint fields, `honorLabels: true`, and `^DCGM_.*` metric allowlist are
all fixed. It requires the workflow's live read-only evidence for the GKE target,
operator CRD, exact Pods and `metrics:9400` port before a source MR; never edit,
apply, or restart the GKE-managed exporter.

GPU scheduling data and GPU telemetry are different sources:

- NVIDIA Device Plugin registers and reports `nvidia.com/gpu`; it is not a full
  device telemetry exporter.
- Kubernetes API / kube-state-metrics exposes GPU capacity, allocatable, request,
  and limit data.
- DCGM exposes device utilization, framebuffer use, temperature, power, clocks,
  energy, PCIe/NVLink, and SM/DRAM/Tensor/FP pipeline activity.

The currently verified GKE-managed exporter exposes these 21 families; the
`^DCGM_.*` rule intentionally follows the managed exporter, so availability and
per-model series counts can change with its version and GPU capabilities:

- framebuffer/device: `DCGM_FI_DEV_FB_FREE`, `DCGM_FI_DEV_FB_TOTAL`,
  `DCGM_FI_DEV_FB_USED`, `DCGM_FI_DEV_GPU_TEMP`, `DCGM_FI_DEV_GPU_UTIL`,
  `DCGM_FI_DEV_MEMORY_TEMP`, `DCGM_FI_DEV_MEM_COPY_UTIL`,
  `DCGM_FI_DEV_POWER_USAGE`, `DCGM_FI_DEV_SM_CLOCK`, and
  `DCGM_FI_DEV_TOTAL_ENERGY_CONSUMPTION`;
- profiling: `DCGM_FI_PROF_DRAM_ACTIVE`, `DCGM_FI_PROF_GR_ENGINE_ACTIVE`,
  `DCGM_FI_PROF_NVLINK_RX_BYTES`, `DCGM_FI_PROF_NVLINK_TX_BYTES`,
  `DCGM_FI_PROF_PCIE_RX_BYTES`, `DCGM_FI_PROF_PCIE_TX_BYTES`,
  `DCGM_FI_PROF_PIPE_FP16_ACTIVE`, `DCGM_FI_PROF_PIPE_FP32_ACTIVE`,
  `DCGM_FI_PROF_PIPE_FP64_ACTIVE`, `DCGM_FI_PROF_PIPE_TENSOR_ACTIVE`, and
  `DCGM_FI_PROF_SM_ACTIVE`.

For example:

```promql
avg by (cluster, namespace, pod, container, modelName) (
  DCGM_FI_DEV_GPU_UTIL
)
```

```promql
100 * DCGM_FI_DEV_FB_USED / DCGM_FI_DEV_FB_TOTAL
```

```promql
sum(kube_node_status_allocatable{resource="nvidia_com_gpu"})
-
sum(kube_pod_container_resource_requests{resource="nvidia_com_gpu"})
```

`honorLabels: true` preserves the exporter attribution labels including
`namespace`, `pod`, `container`, `Hostname`, `UUID`, and `modelName`. Instant
queries may include stale autoscaled nodes in their lookback window; use a
freshness-aware current count when needed:

```promql
count((time() - timestamp(DCGM_FI_DEV_GPU_UTIL)) <= 60)
```

The managed exporter currently does **not** establish coverage for ECC, XID,
throttle reasons, PID/process utilization, fan speed, or detailed MIG metrics.
Do not claim those are covered.

Never use scrape CRs to carry remote targets, credentials, bearer tokens,
authorization headers, Basic Auth, OAuth settings, or Grafana settings. Use an
existing in-cluster Service whenever possible. Confirm the target cluster has
the VictoriaMetrics Operator CRD before merging; lack of that platform
precondition is an Ops Todo, not a reason to add a CRD or VM agent to the app
repository.

The following are platform-owned and are intentionally outside this workflow:
`VMAgent`, `VMCluster`, `VMAlert`, `VMStaticScrape`, operator/CRD
installation, any cluster-wide discovery other than the exact documented DCGM
profile, remote write, retention, and scrape credentials. Escalate those to the
observability/SRE owner with the desired target, namespace, labels, port, path,
interval, and reason.

Application alerting `VMRule` is supported only by
`workflows/manage-vmalert-rules.md` and `references/victoriametrics/alerting.md`,
after platform registration. This scrape workflow does not generate alert rules.
