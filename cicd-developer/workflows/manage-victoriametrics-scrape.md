---
name: manage-victoriametrics-scrape
description: 在调用方应用 overlay 创建受限 VM scrape，或为唯一 GKE-managed DCGM profile 更新已有 cluster-owned directory source 并验证其证据。
---

# Workflow：manage-victoriametrics-scrape

## 目的

为已有应用接入 VictoriaMetrics 指标采集。默认产物属于**调用方应用仓库**与其目标
Kustomize overlay；它不是 Grafana Dashboard，也绝不写入
`DEV/grafana-dashboards-as-code`。唯一例外是已验证的 GKE-managed
`gke-managed-system/dcgm-exporter`：其固定 `VMPodScrape` 只属于 `DEV/k8s` 内既有
cluster-owned `victoria-metrics-config` Argo directory source。本 workflow 不访问
Grafana、不读取任何 Token、不执行 `kubectl apply`，也不创建 VictoriaMetrics Operator、
VMAgent、VMCluster 或任意通用 cluster-wide discovery，更不修改 GKE-managed exporter；
它只为该精确托管目标生成受限 scrape CR。

开始前读：

- `references/victoriametrics/README.md`
- `recipes/victoriametrics/README.md`
- `references/data/clusters.yaml`

## Step 1. 确认 profile、目标和资源归属

[precondition]
  - 用户请求创建或修改 VictoriaMetrics scrape 资源，而不是 Grafana Dashboard

[action]
  - 先分类为以下两个 profile；不匹配时 STOP + observability/SRE Ops Todo，不能由名字相近的
    DCGM、跨 namespace selector 或 cluster-wide discovery 推断例外：
    1. **application（默认）**：收集 `$environment`、`$cluster`、`$namespace`、
       `$target_overlay`、`$service_manifest`、`$service_name`、`$selector_label_key`、
       `$selector_label_value`、`$metrics_port`、`$metrics_path` 和 `$scrape_interval`。默认
       选择 `VMServiceScrape`；只有没有合适 Service、且明确说明按 Pod 发现必要性时才选
       `VMPodScrape`。
    2. **gke-managed-dcgm（唯一例外）**：只接受精确 GKE-managed
       `gke-managed-system/dcgm-exporter`，并固定写入已有
       `DEV/k8s/clusters/<gcp-cluster>/victoria-metrics-config/` active Argo directory source。
       不收集可改变 selector、endpoint、namespace 或 relabel 的 slot。
  - application：读取目标 Service 与 Workload；selector 必须精确匹配一个同 namespace 对象；
    `$metrics_port` 必须是 Service 和容器均存在的**命名** port。默认 path 是 `/metrics`，
    interval 至少 `30s`。
  - gke-managed-dcgm：在任何写入前只读确认全部证据：目标为 GKE；VictoriaMetrics Operator
    CRD 存在；`gke-managed-system` 中精确
    `app.kubernetes.io/name=gke-managed-dcgm-exporter` selector 返回非空 Pod；每个目标 Pod 至少一个
    exporter container 暴露命名 `metrics` TCP `9400`；以及现有
    `clusters/<gcp-cluster>/victoria-metrics-config/` 是 active Argo source。可使用只读
    `kubectl api-resources --api-group=operator.victoriametrics.com`、`kubectl get pod` 和
    `kubectl get pod -o json` 收集证据，但不执行任何 mutation。
  - 无只读权限、CRD/Pod/port/source 任一证据缺失，或目标不是该托管组件，均 STOP + Ops Todo；
    不在应用仓安装 CRD，也不新建 exporter、VMAgent 或 Argo source。

[validate]
  - application 不接受 `namespaceSelector`、Pod IP、域名、静态 target 或宽泛 selector；
    selector 仅可选同 namespace 的唯一 Service/Workload。
  - gke-managed-dcgm 只能使用静态 recipe 的闭合合同，不能改为任意 GKE workload、任意
    namespace、端口、path、interval、timeout、`honorLabels` 或 relabel。
  - 不接受 Bearer Token、Authorization/Basic Auth/OAuth、远程 URL、Grafana URL 或数据源 UID
    作为任一 profile 的 scrape 输入。

[output]
  - profile、目标仓/路径、精确 selector、命名 port、path、interval，以及 CRD 及 profile 特有
    live read-only evidence 或 Ops Todo

## Step 2. 生成受限 manifest

[precondition]
  - Step 1 的 profile、目标对象和 CRD 前置已确认

[action]
  - application：在 `$target_overlay` 创建新文件：
    - `VMServiceScrape` 使用 `recipes/victoriametrics/vm-service-scrape.yaml.tmpl`；
    - `VMPodScrape` 使用 `recipes/victoriametrics/vm-pod-scrape.yaml.tmpl`。
    只填 `{{scrape_name}}`、`{{app}}`、`{{selector_label_key}}`、
    `{{selector_label_value}}`、`{{metrics_port}}`、`{{metrics_path}}`、
    `{{scrape_interval}}`。在同一 overlay 的 `kustomization.yaml -> resources` 显式加入新文件；
    若 overlay 已以 `namespace:` 注入 namespace，不额外写 `metadata.namespace`。
  - gke-managed-dcgm：只复制
    `recipes/victoriametrics/gke-managed-dcgm-exporter-vm-pod-scrape.yaml.tmpl` 到既有
    `victoria-metrics-config` raw Argo directory source。该静态 recipe 没有 slot，不创建、要求或
    修改 `kustomization.yaml`，不修改 GKE managed resource。
  - 不改 base 中无关 Service，不加入 annotation-based scrape，也不把采集凭据放入 ConfigMap、
    Secret 或 manifest。

[validate]
  - application 新文件只含 `operator.victoriametrics.com/v1beta1` 的 `VMServiceScrape` 或
    `VMPodScrape`，metadata name 在该 namespace 内唯一。
  - gke-managed-dcgm 新文件仅含静态的
    `VMPodScrape/gke-managed-dcgm-exporter` 合同；来源 namespace、selector、唯一 endpoint 和
    唯一 `^DCGM_.*` keep relabel 均不可扩张。

[output]
  - application 的文件路径和 `kustomization.yaml` 修改，或 DCGM 的既有 Argo directory source 文件
    路径；所选 profile 和具体 selector

## Step 3. 验证资源和真实 target

[precondition]
  - Step 2 的 candidate 已写入正确的 source owner

[action]
  - application：将准确 overlay 渲染到临时目录；优先 `kustomize build`，仅在它不可用时使用
    `kubectl kustomize`。不要对集群 apply：

    ```bash
    rendered_dir="$(mktemp -d -t cicd-vm-scrape-XXXXXX)"
    if command -v kustomize >/dev/null 2>&1; then
      kustomize build "$target_overlay" > "$rendered_dir/rendered.yaml"
    else
      kubectl kustomize "$target_overlay" > "$rendered_dir/rendered.yaml"
    fi
    python3 "$skill_root/validators/check_victoriametrics_scrapes.py" \
      --repo-context app "$rendered_dir" --require-target-match
    bash "$skill_root/validators/validate.sh" --repo-context app "$rendered_dir"
    ```
  - gke-managed-dcgm：把静态 candidate 文件复制到独立临时目录后验证；不要扫描整个已有
    `victoria-metrics-config`，不要把它并入 application workspace，也不要求凭空 render 出 managed
    DaemonSet。随后再运行 `DEV/k8s` 对 changed file / cluster directory 声明的既有校验：

    ```bash
    candidate_dir="$(mktemp -d -t cicd-gke-dcgm-XXXXXX)"
    cp "$victoria_metrics_config_file" "$candidate_dir/gpu-dcgm-scrape.yaml"
    python3 "$skill_root/validators/check_victoriametrics_scrapes.py" \
      --repo-context k8s "$candidate_dir" --require-target-match
    bash "$skill_root/validators/validate.sh" \
      --repo-context k8s "$candidate_dir"
    ```

[validate]
  - application 的 `--require-target-match` 必须确认每个 selector 只匹配一个同 namespace
    Service 或 Workload，且命名 endpoint port 存在。
  - gke-managed-dcgm 的 `--require-target-match` 只可依赖静态 identity gate；managed DaemonSet
    不在 Git source 时跳过 render 内 workload match，但 Step 1 保存的 live read-only target/port/CRD
    evidence 仍为必需条件。
  - 两个 validator 及 application Kustomize render 均必须 exit 0；任一失败即 STOP，报告原始错误，
    不得为通过校验放宽 selector、降低 interval、去掉 `honorLabels` 或改成静态 target。

[output]
  - render（仅 application）、静态 DCGM contract、两个 validator 的结果，以及 live evidence，或可复现的
    原始失败

## Step 4. 提交正确 source owner 的正常 MR

[precondition]
  - Step 3 所有适用校验通过

[action]
  - application：按调用方应用仓库既有 Git/MR 规范提交 scrape manifest 和 `kustomization.yaml`。
  - gke-managed-dcgm：仅按 `DEV/k8s` 既有 Git/MR 规范提交 static recipe 的目标 directory 文件；
    不创建 caller application overlay 或 Kustomization。
  - MR 描述写明 profile、cluster、namespace、target/selector、命名 port、path、interval、CRD 与
    profile 必需 evidence，以及 Step 3 validator 结果。

[validate]
  - 提交不得包含 Token、kubeconfig、Grafana 配置、生成的临时 render 文件或远程 metrics URL。
  - 不把 application MR 投到 Grafana Dashboard 仓，也不把 DCGM profile 投到应用仓；Grafana Dashboard
    需要另走 `manage-grafana-dashboard.md`。

[output]
  - 正确 source owner 的 MR、部署前置，以及合并后由既有 GitOps/Argo CD 同步的预期
