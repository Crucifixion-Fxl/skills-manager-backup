---
name: argocd-fleet-scan
description: 扫描全部 EKS/TKE/GKE 集群的 ArgoCD Application 异常状态（Sync failed / Degraded / OutOfSync / 长期 Progressing），按"集群基础设施 vs 业务应用"分级，渲染成可过滤/搜索的 HTML 静态页（App 名直接点击跳转该集群 ArgoCD UI）。用于日常健康巡检、变更后批量验证、值班 oncall 一眼摸排 fleet 状态。
---

# argocd-fleet-scan

本地 fallback 扫描工具：直接 `kubectl` 读 16 个集群 `argo-cd` ns 下的 Application CRD，避开逐个 ArgoCD token / SSO 登录。并行扫描，按状态 × 组件类型分类，输出单文件 HTML 报告（可过滤、可搜索、可直接点 App 跳转到该集群 ArgoCD UI 的 resource tree 视图）。

> 日常主入口优先使用在线服务 `argocd-fleet-scanner`：`https://argocd-fleet.addx.live`，部署在 `sg-devops`，每 5 分钟通过 ArgoCD REST API 扫 16 集群并推送 P0/P1 增量飞书告警。本 skill 只在以下场景使用：在线服务不可达、需要离线 HTML、需要验证 `kubectl` RBAC/context、或变更后想从本机做一次独立复核。

**适用场景：**

- 在线 scanner 不可用时的临时 fleet 健康巡检
- 集群级变更（升级、cicd-base bump、批量 rollout）后批量验证
- 值班 oncall 接到"某集群有应用挂了"时快速摸排是不是 fleet-wide 问题
- 修复后验证某个 staging 是否恢复 0 异常

## 调用方式

- 全量扫描：默认行为，扫全部 16 个集群（约 30~60 秒）
- 指定范围：用户自然语言传入，例如：
  - "扫一下所有 prod 集群"  → 仅 prod-tier 6 个
  - "扫 us 区"  → us-prod / us-tech-service / us-data / us-staging / us-prod-gke / us-tech-service-gke
  - "只看 cn-staging"  → 单集群
  - "只看 staging"  → us-staging / eu-staging / cn-staging

## 状态矩阵

| Sync Status | Health Status | 含义 | 优先级 |
|-------------|--------------|------|--------|
| `Synced`    | `Healthy`    | 正常，无需操作 | — |
| `OutOfSync` | `Healthy`    | Git 有未部署变更（drift） | P2 |
| `Synced`    | `Degraded`   | 已部署但运行异常（Pod CrashLoop、副本不足、Job 失败等） | **P0/P1** |
| `OutOfSync` | `Degraded`   | 未部署且运行异常 | **P0/P1** |
| `Synced`    | `Progressing` (短期 < 阈值) | 正在 rollout / 重启中 | 忽略 |
| `Synced`    | `Progressing` (长期 ≥ 阈值) | rollout 卡住、副本起不来、Hook 卡住 | **P0/P1** |
| `Synced`    | `Missing`    | Resource 缺失（被人手动删除 / namespace 不在） | **P0/P1** |
| `*`         | `Suspended`  | 资源被 Argo Rollouts 暂停 | 信息 |
| `*`         | `Unknown`    | ArgoCD controller 拿不到状态 | P0 |
| `Unknown`   | `*`          | 仓库 / target revision 拉不到（SyncError / ComparisonError） | **P0** |
| `*`         | `*` + 当前仍有 drift、不健康或 Error/Failed condition，且 `OperationState.Phase = Failed/Error` | Active sync failed（apply 报错、Hook 失败） | **P0/P1** |

**长期 Progressing 阈值默认 15 分钟**：`now - status.health.lastTransitionTime > 900s` 且当前仍为 Progressing。

**历史失败过滤**：ArgoCD 会保留最近一次 `operationState`。如果当前快照已经是 `Synced/Healthy` 且没有 Error/Failed condition，不要把历史 `Failed/Error` phase 继续报成 `SyncFailed`。

## 严重度分级

按 **组件性质** × **状态严重程度** × **syncPolicy / drift age** 三个维度，落到 P0/P1/P2/P3：

| 组件类型 | Degraded / SyncFailed / 长 Progressing / Missing / Unknown | OutOfSync (Healthy) |
|---------|-----|-----|
| **集群基础设施** (CICD 平台 + 控制面 operator) | **P0** — 整个集群部署能力受影响，立即处理 | **P1** — 平台层 drift，未生效的 cicd-base 变更 |
| **业务应用** (业务服务 + 数据组件) | **P1** — 单个业务受损 | **P1** SelfHealStuck / **P2** stale drift / **P3** 等待自愈 — 见下方细分 |

**OutOfSync (Healthy) 业务应用进一步细分**：

| 子规则 | 等级 | 说明 |
|--------|------|------|
| 同 revision 在 `status.history` 出现 ≥ 2 次 (SelfHealStuck) | **P1** | selfHeal 已成功 sync 当前 desired revision ≥ 2 次但 app 仍 OutOfSync = 反复 sync 收敛不了 (ignoreDifferences 缺失 / hook 没改变状态 / 资源被外部改回)，真故障早期信号 |
| `selfHeal=true` 且 history 无重复 | **P3** 等待自愈 | controller 正在 reconcile；多数是 image-updater 刚 commit 还没下一轮 sync，自愈率高，默认折叠 |
| `selfHeal=false` (含 automated=false 全 manual 或 automated=true 但 selfHeal=false) | **P2** stale drift | 真需要人盯——manual sync 漏了，或 OOB 改了 live 资源 automated 不会修 |

**SelfHealStuck 信号原理**：ArgoCD `status.history` 只记录**成功的部署**。同 revision 出现 ≥ 2 次 = 多次成功 sync 但 app 仍 OutOfSync = selfHeal 在原地踏步。这是**单帧 snapshot 可靠信号**——`opAge` / `reconcileAge` 每次重试都会刷新（无声重置），`revision-match` 在 sync 刚结束的窗口里也短暂为 true（误报），唯独 history 重复次数累加可信。

**P3 在 UI 默认折叠**：顶部按钮默认选中 `Active`（= P0+P1+P2），手动点 `P3` 或 `All` 才显示。日常巡检不被 image-updater 瞬时 drift 淹没，但需要时一键展开。

### 基础设施判定（按 ArgoCD App 名称匹配）

正则模式（小写匹配，full match）：

```
^(
  # ArgoCD 自身 + GitOps 工具链
  argocd|argocd-image-updater|argo-rollouts|
  # SSO / 身份
  casdoor(-.*)?|
  # 云资源声明 (Crossplane)
  crossplane|crossplane-.*|
  # 密钥同步
  external-secrets|external-secrets-.*|cluster-secret-store-.*|
  # Vault 密钥/授权同步（gitlab-vault-sync-builder 裸名是 infra，但区域
  # -builder-eu/-us 是开发者自助授权刷新，ESO 数据面不受影响 → 留 app，
  # 故 -ops 用 (-.*)? 全收、-builder 只收裸名，不能一把梭）
  gitlab-vault-sync-ops(-.*)?|gitlab-vault-sync-builder|vault-policies-sync|vault-sync|
  # 策略 / 准入
  kyverno|kyverno-.*|
  # 证书
  cert-manager|
  # 镜像仓库 / 制品库
  harbor(-.*)?|nexus(-.*)?|
  # CI 构建缓存
  buildbuddy(-.*)?|
  # 日志管道
  fluent-bit-.*|vector(-.*)?|logstash|
  # Ingress / 网络
  apisix-gateway-.*|aws-load-balancer-controller|egress-proxy|
  # 集群 DNS
  coredns(-.*)?|
  # 节点 / 调度
  karpenter(-.*)?|metrics-server|
  # CI runner
  gitlab-runner-.*|gitlab-runner-rbac|
  # 通用 operator
  flink-operator|flink-kubernetes-operator|telepresence|eck-operator|elastic-operator|
  # 平台 RBAC
  oidc-dev-rbac(-.*)?|
  # 平台辅助
  domain-exporter|
  # 监控 / 可观测栈（fleet-wide，平台维护）
  # 注意 grafana 必须带 (-.*)? —— 早期漏写后缀组导致 grafana-eu /
  # grafana-bootstrap / grafana-resources 全部漏判成 app（2026-05-28 修）
  prometheus(-.*)?|grafana(-.*)?|loki|
  vm-(operator|stack|agent)|vmagent|vmalert|vmsingle|vmcluster|blackbox-exporter|yace|meta-monitoring|observability-.*|
  # 集群入口
  root-apps|
  # 命名后缀显式声明 infra 的
  .*-infra
)$
```

**`.*-infra` catch-all 例外**：`dvc-remote-infra-*` 是 ML 团队的数据栈（DVC remote），名字带 `-infra` 但**必须留 app**（业务/ML 团队负责，非平台控制面）。`.*-infra` 会误吃裸 `dvc-remote-infra`（带后缀的 `dvc-remote-infra-staging-us` 因 `-infra` 不在结尾而侥幸逃过）。RE2/jq 处理方式：在 infra 正则匹配**之前**先用 `^dvc-remote-infra` 排除，命中则强制归 app。fleet-scanner Go 服务用 `appExcludeRegex` 实现此排除（classify.go），本 jq 流程需在 `.category` 赋值前加同等判断。

> **2026-05-28 fleet 全量审计**（278 个 live app 名跨 16 集群，多 agent judge→对抗 verify→synthesize）新增上述 infra 项。对抗复核**否掉**的 4 个过度提升（保持 app/P1）：`gitlab-vault-sync-builder-eu`/`-us`、`graylog-staging-us`（单集群 DATA 团队审计非 fleet-wide）、`pagerduty-bootstrap`（只同步 PD SaaS 配置，真实派单走 vm-stack/Alertmanager）。

不在以上模式即算业务应用。Crossplane 派生应用（`crossplane-infra` / `crossplane-conn-patcher` / `crossplane-compositions`）由 `.*-infra` 和显式名兜底。

**边界 case 处理（不一刀切）：**

- `flink-prod-us` — 不是 `flink-operator`，是业务 Flink 集群本身 → 业务应用
- `cicd-sentry-*` — CICD 平台 Sentry 实例 → 算基础设施
- `etcd-staging-us`、`clickhouse-staging-us` — staging 共享数据组件 → 视作业务应用（数据栈，业务团队负责）
- `dvc-remote-*` — 数据 infra → 业务应用（ML 数据栈，由 ML 团队负责）
- 不确定的边界 case 一律落业务应用，扫描结果里加注 `?` 由人工二次确认

## 集群列表

| 区域 | 集群 | tier | kubectl context |
|------|------|------|-----------------|
| US | us-prod | prod | `arn:aws:eks:us-east-1:302571458622:cluster/us-eks` |
| US | us-tech-service | prod | `arn:aws:eks:us-east-1:002497567426:cluster/us-eks` |
| US | us-data | prod | `arn:aws:eks:us-east-1:769494896000:cluster/us-prod-data` |
| US | us-staging | staging | `arn:aws:eks:us-east-1:390709477306:cluster/us-eks-staging` |
| US | us-prod-gke | prod | `gke_a4xcloud-p-us_us-east4_us-prod-east4-gke` |
| US | us-tech-service-gke | prod | `gke_a4xcloud-tech-service-us_us-east4_us-tech-service-east4-gke` |
| EU | eu-prod | prod | `arn:aws:eks:eu-central-1:740315635167:cluster/eu-eks` |
| EU | eu-tech-service | prod | `arn:aws:eks:eu-central-1:010840394398:cluster/eu-eks-tech-service` |
| EU | eu-data | prod | `arn:aws:eks:eu-central-1:769494896000:cluster/eu-prod-data` |
| EU | eu-staging | staging | `arn:aws:eks:eu-central-1:390709477306:cluster/eu-eks-staging` |
| CN | cn-prod | prod | `arn:aws-cn:eks:cn-north-1:741924744516:cluster/cn-eks` |
| CN | cn-tech-service | prod | `arn:aws-cn:eks:cn-north-1:589899215075:cluster/cn-eks-tech-service` |
| CN | cn-main (TKE) | prod | `tke-cn-k8s` |
| CN | cn-staging | staging | `arn:aws-cn:eks:cn-north-1:801447536674:cluster/cn-eks-staging` |
| CN | cn-dev | dev | `arn:aws-cn:eks:cn-north-1:801447536674:cluster/cn-eks-dev` |
| SG | sg-devops | shared | `arn:aws:eks:ap-southeast-1:125710977284:cluster/sg-eks` |

**ArgoCD namespace 统一为 `argo-cd`**。所有集群都启用 App of Apps，所以扫 Application CRD 即覆盖所有 GitOps 管理的资源。

### ArgoCD UI 入口（HTML 报告点击跳转用）

| Cluster | ArgoCD host | App 链接格式 |
|---------|-------------|-------------|
| us-prod / eu-prod / cn-prod | `argocd-{us,eu,cn}.addx.live` | `https://<host>/applications/argo-cd/<app>?view=tree` |
| {us,eu,cn}-tech-service | `argocd-{us,eu,cn}-tech-service.addx.live` | 同上 |
| {us,eu}-data | `argocd-{us,eu}-data.addx.live` | 同上 |
| cn-main (TKE) | `argocd-cn-k8s.addx.live` | 同上 |
| {us,eu,cn}-staging | `argocd-{us,eu,cn}-staging.addx.live` | 同上 |
| cn-dev | `argocd-cn-dev.addx.live` | 同上 |
| sg-devops | `argocd-sg-devops.addx.live` | 同上 |
| us-prod-gke / us-tech-service-gke | `argocd-us-prod-gke.addx.live` / `argocd-us-tech-service-gke.addx.live` | 同上 |

完整映射写在 `generate_report.py` 的 `CLUSTER_ARGOCD` 字典里，新集群上线时同步两处（SKILL.md 表格 + python 字典）。

**踩坑注意**：us-staging / eu-staging 在 Phase 3 cleanup 完成前还保留老 hostname `argocd-staging-{us,eu}.addx.live`，但新报告统一指向新 hostname（`argocd-{us,eu}-staging.addx.live`），不要混用。

### kubectl context 注意事项

部分 staging 集群默认 context 不在 `argo-cd` ns 有 list 权限，必须用 `*-admin` context（如 `us-eks-staging-admin` / `eu-eks-staging-admin`）。短名 `us-staging` 是默认到 `staging-us` ns 的用户级 context，list applications 会 403。

## 执行流程

### Step 1：并行扫描全部目标集群

每个集群一个后台进程并行跑，避免串行等 16 次 RTT。每个进程把异常 App 序列化成单行 JSON 写入临时文件，主进程聚合。

```bash
THRESHOLD_SECONDS=900       # 长 Progressing 阈值 + selfHeal 卡住阈值（15 min）
NOISE_THRESHOLD_SECONDS=1800 # P3 噪音档阈值：automated+OutOfSync 且 opAge < 30min 算瞬时
TMPDIR=$(mktemp -d)

scan_cluster() {
  local NAME=$1 CTX=$2 TIER=$3
  kubectl --context="$CTX" -n argo-cd get applications -o json 2>/dev/null | \
    jq -r --arg cluster "$NAME" --arg tier "$TIER" --argjson th $THRESHOLD_SECONDS '
      .items[] | (.status.sync.revision // "") as $sync_rev | {
        cluster: $cluster,
        tier: $tier,
        name: .metadata.name,
        sync: .status.sync.status,
        health: .status.health.status,
        ht: .status.health.lastTransitionTime,
        op_phase: .status.operationState.phase,
        op_msg: .status.operationState.message,
        op_finished_at: .status.operationState.finishedAt,
        reconciled_at: .status.reconciledAt,
        automated: (.spec.syncPolicy.automated != null),
        selfHeal:  ((.spec.syncPolicy.automated.selfHeal // false) == true),
        # status.history 是 ArgoCD 维护的"成功部署"日志 (默认最近 10 条).
        # 同一个 revision 出现 >= 2 次 = selfHeal 已经成功 sync 这个 rev 至少 2 次,
        # 但 app 仍 OutOfSync = sync 收敛失败 (ignoreDifferences 缺失 / hook 没改变状态 / 资源被外部改回).
        history_same_rev_count: (
          (.status.history // []) | map(select(.revision == $sync_rev)) | length
        ),
        conditions: [
          .status.conditions[]? | select(.type | test("Error|Failed"))
          | { type: .type, msg: .message }
        ],
        revision: ($sync_rev | .[:8]),
      }
      # 计算时间字段 (秒)
      | . + {
          healthAge:    (if .ht             then (now - (.ht             | fromdateiso8601)) else 0 end),
          opAge:        (if .op_finished_at then (now - (.op_finished_at | fromdateiso8601)) else null end),
          reconcileAge: (if .reconciled_at  then (now - (.reconciled_at  | fromdateiso8601)) else null end),
        }
      # syncAge = 离 OutOfSync 至少多久（用 opAge 兜底，没 op 用 reconcileAge）
      | . + { syncAge: (.opAge // .reconcileAge // 0) }
      # 标记异常类型
      | . + { anomaly:
          [
            (if .sync == "OutOfSync" then "OutOfSync" else empty end),
            (if .sync == "Unknown"   then "SyncUnknown" else empty end),
            (if .health == "Degraded"    then "Degraded"      else empty end),
            (if .health == "Missing"     then "Missing"       else empty end),
            (if .health == "Unknown"     then "HealthUnknown" else empty end),
            (if .health == "Progressing" and .healthAge >= $th then "LongProgressing" else empty end),
            # ArgoCD 会保留最近一次 operationState。只有当前快照仍有 drift、
            # 不健康或 Error/Failed condition 时，Failed/Error phase 才算 active SyncFailed；
            # 否则是历史失败噪音。
            (if (.op_phase == "Failed" or .op_phase == "Error")
                and (.sync != "Synced" or .health != "Healthy" or (.conditions | length) > 0)
              then "SyncFailed" else empty end),
            (if (.conditions | length) > 0 then "Condition:" + ([.conditions[].type] | unique | join(",")) else empty end),
            # SelfHealStuck: selfHeal 已成功 sync 当前 desired revision >= 2 次 (history 出现 >= 2 次),
            # 但 app 仍 OutOfSync = 同 revision 反复 sync 但收敛不了, 单帧可靠信号.
            # 不用 syncAge/opAge (selfHeal 每次尝试都会刷新, 会被无声重置).
            (if .sync == "OutOfSync" and .selfHeal
                and .op_phase == "Succeeded"
                and (.history_same_rev_count >= 2)
              then "SelfHealStuck" else empty end)
          ]
        }
      | select(.anomaly | length > 0)
      | @json
    ' > "$TMPDIR/$NAME.json" 2>"$TMPDIR/$NAME.err" &
}

# 调度（按用户指定的范围裁剪此列表）
scan_cluster us-prod               arn:aws:eks:us-east-1:302571458622:cluster/us-eks                    prod
scan_cluster us-tech-service       arn:aws:eks:us-east-1:002497567426:cluster/us-eks                    prod
scan_cluster us-data               arn:aws:eks:us-east-1:769494896000:cluster/us-prod-data              prod
scan_cluster us-staging            arn:aws:eks:us-east-1:390709477306:cluster/us-eks-staging            staging
scan_cluster us-prod-gke           gke_a4xcloud-p-us_us-east4_us-prod-east4-gke                         prod
scan_cluster us-tech-service-gke   gke_a4xcloud-tech-service-us_us-east4_us-tech-service-east4-gke      prod
scan_cluster eu-prod               arn:aws:eks:eu-central-1:740315635167:cluster/eu-eks                 prod
scan_cluster eu-tech-service       arn:aws:eks:eu-central-1:010840394398:cluster/eu-eks-tech-service    prod
scan_cluster eu-data               arn:aws:eks:eu-central-1:769494896000:cluster/eu-prod-data           prod
scan_cluster eu-staging            arn:aws:eks:eu-central-1:390709477306:cluster/eu-eks-staging         staging
scan_cluster cn-prod               arn:aws-cn:eks:cn-north-1:741924744516:cluster/cn-eks                prod
scan_cluster cn-tech-service       arn:aws-cn:eks:cn-north-1:589899215075:cluster/cn-eks-tech-service   prod
scan_cluster cn-main               tke-cn-k8s                                                            prod
scan_cluster cn-staging            arn:aws-cn:eks:cn-north-1:801447536674:cluster/cn-eks-staging        staging
scan_cluster cn-dev                arn:aws-cn:eks:cn-north-1:801447536674:cluster/cn-eks-dev            dev
scan_cluster sg-devops             arn:aws:eks:ap-southeast-1:125710977284:cluster/sg-eks               shared

wait

# 汇总
cat "$TMPDIR"/*.json 2>/dev/null > "$TMPDIR/all.ndjson"

# 报告每个集群的"扫不通"状况（context 错 / kubeconfig 没权限）
for f in "$TMPDIR"/*.err; do
  [ -s "$f" ] && echo "WARN: $(basename $f .err) context unreachable:" && head -3 "$f"
done
```

**关键点：**

- 每个集群 jq 直接计算 `healthAge`，主进程不再做时间运算
- `conditions` 只保留 type 含 Error/Failed 的（SyncError / ComparisonError / SharedResourceWarning），过滤掉 OrphanedResource 之类信息条目
- `revision[:8]` 取短 commit，便于报告里看到当前部署的 commit
- `2>"$TMPDIR/$NAME.err"` 把 kubectl/网络错误捕获，扫描完后告警"哪个集群没扫通"——别让一个 context 失败吃掉整个报告

### Step 2：分类 + 分级

读取 `all.ndjson`，对每行：

1. 用 infra 正则匹配 `name` → `category = infra | app`
2. `severity` 决定（按优先级从高到低，命中即终止）：
   - **SelfHealStuck**（OutOfSync + selfHeal=true + 同 revision history ≥ 2 次）→ **P1**（无论 infra/app；selfHeal 收敛失败即真故障早期信号）
   - **真异常**（Degraded / SyncFailed / Long Progressing / Missing / HealthUnknown / SyncUnknown / Condition:*）：
     - infra → **P0**
     - app → **P1**
   - **OutOfSync only + infra** → **P1**（基础设施层 drift，未生效的 cicd-base 变更）
   - **OutOfSync only + app + selfHeal=true** → **P3**（等待自愈；controller 正在 reconcile，可能 image-updater 刚 commit 还没下一轮 sync）
   - **OutOfSync only + app + selfHeal=false**（含 automated=false 或 automated=true 但 selfHeal=false）→ **P2**（真 drift，需要人盯——要么 manual sync 漏了，要么 OOB 改了 live 资源 automated 不会修）
3. 多个 anomaly 取最严重的展示，但报告里同一行列出全部 tag

```bash
INFRA_RE='^(argocd|argocd-image-updater|argo-rollouts|casdoor(-.*)?|crossplane|crossplane-.*|external-secrets|external-secrets-.*|cluster-secret-store-.*|kyverno|kyverno-.*|cert-manager|harbor(-.*)?|nexus(-.*)?|buildbuddy(-.*)?|fluent-bit-.*|vector(-.*)?|logstash|apisix-gateway-.*|aws-load-balancer-controller|egress-proxy|coredns(-.*)?|karpenter(-.*)?|metrics-server|gitlab-runner-.*|gitlab-runner-rbac|gitlab-vault-sync-ops(-.*)?|gitlab-vault-sync-builder|vault-policies-sync|vault-sync|flink-operator|flink-kubernetes-operator|telepresence|eck-operator|elastic-operator|oidc-dev-rbac(-.*)?|domain-exporter|prometheus(-.*)?|grafana(-.*)?|loki|vm-(operator|stack|agent)|vmagent|vmalert|vmsingle|vmcluster|blackbox-exporter|yace|meta-monitoring|observability-.*|root-apps|.*-infra|cicd-sentry-.*)$'

# APP_EXCLUDE: `.*-infra` catch-all 会误吃 dvc-remote-infra-*（ML 数据栈，必须留 app）。
# 在 INFRA_RE 匹配之前先排除。与 fleet-scanner Go 服务的 appExcludeRegex 等价。
APP_EXCLUDE_RE='^dvc-remote-infra'

jq -c --arg ire "$INFRA_RE" --arg xre "$APP_EXCLUDE_RE" '
  .category = (if (.name | test($xre)) then "app" elif (.name | test($ire)) then "infra" else "app" end)
  | . as $r
  | .severity = (
      # SelfHealStuck (history 同 rev ≥ 2 次) 永远是 P1, 不分 infra/app
      if   ($r.anomaly | any(. == "SelfHealStuck"))     then "P1"
      # 其他真异常: infra → P0, app → P1
      elif ($r.anomaly | any(. != "OutOfSync"))         then (if $r.category == "infra" then "P0" else "P1" end)
      # 纯 OutOfSync: infra 直接 P1; app 看 selfHeal — true = P3 等待自愈, false = P2 stale drift
      elif $r.category == "infra"                       then "P1"
      elif $r.selfHeal                                  then "P3"
      else                                                   "P2"
      end
    )
' "$TMPDIR/all.ndjson" > "$TMPDIR/classified.ndjson"
```

### Step 3：渲染 HTML 报告

把 `classified.ndjson` 喂给本 skill 自带的 `generate_report.py`，产出一个**自包含的 HTML 静态页**（embedded CSS + 轻量 JS，无外部 CDN，可离线打开）：

```bash
SKILL_DIR="$HOME/.codex/skills/argocd-fleet-scan"
# 兜底：装在源仓库 ~/Project/A4x/skills/skills/argocd-fleet-scan 也能跑
[ ! -f "$SKILL_DIR/generate_report.py" ] && SKILL_DIR="$HOME/Project/A4x/skills/skills/argocd-fleet-scan"

OUT="/tmp/argocd-fleet-scan-$(date +%Y%m%d-%H%M).html"
UNREACH=$(for f in "$TMPDIR"/*.err; do [ -s "$f" ] && basename "$f" .err; done | paste -sd,)

python3 "$SKILL_DIR/generate_report.py" "$TMPDIR/classified.ndjson" "$OUT" "$UNREACH"
echo "Open: file://$OUT"
```

**报告包含：**

1. **顶栏统计**：扫描时间、扫通/不可达集群数、总异常数、阈值
2. **不可达 banner**（红黄色）：列出哪些集群没扫到，对应行不在统计中
3. **5 个统计卡**：总异常 / P0 / P1 / P2 / infra:app 比例
4. **过滤控件**：
   - 严重度按钮（All / P0 / P1 / P2，带计数）
   - 类别下拉（infra / app）
   - 集群下拉（动态根据扫描结果生成）
   - 全文搜索（匹配 app 名 / op_msg / 任意可见文本）
5. **主表**：Sev / Cluster (→ 该集群 applications 列表) / **App** (→ 该集群 resource tree 视图) / Category / Anomaly chips / Sync·Health / Age / Last Op Msg / Rev
   - app 链接格式见上文「ArgoCD UI 入口」表
   - anomaly chips 按异常类型上色（Degraded 红、SyncFailed 橙、ComparisonError 紫、OutOfSync 蓝）
6. **集群汇总表**：全部 16 集群 P0/P1/P2 计数，不可达集群标黄 pill
7. **底栏 footnote**：解释链接行为 + 严重度规则 + chip 含义

**报告位置约定**：`/tmp/argocd-fleet-scan-YYYYMMDD-HHMM.html`，告诉用户 `file://` 路径直接浏览器打开，无需起服务器。

**新增集群时**：同时更新 SKILL.md 的「ArgoCD UI 入口」表 和 `generate_report.py` 的 `CLUSTER_ARGOCD` 字典，缺一处会导致该集群行的 App 名变成纯文本（不可点击）。

### Step 4：根因 hint（仅 P0）

对每个 P0 异常给出排查方向（不替代真正诊断）：

| 异常类型 | 排查命令 |
|---------|---------|
| `Degraded` | `kubectl --context=<CTX> -n <NS> get pod -l app.kubernetes.io/instance=<APP>` 看 Pod 状态 / Events |
| `SyncFailed` | `kubectl --context=<CTX> -n argo-cd get app <APP> -o yaml \| yq '.status.operationState'` 看 apply 错误 |
| `LongProgressing` | 多数是 Rollout 卡 AnalysisRun 失败 / Hook 卡住 / Pod 起不来；先查 `kubectl get rollout / job / pod -n <NS>` |
| `Missing` | 资源被人手动删除或 namespace 缺失；查 ArgoCD UI events 或 `kubectl get ns` |
| `HealthUnknown` / `SyncUnknown` | ArgoCD controller 或仓库连不上；查 `kubectl -n argo-cd logs deploy/argocd-application-controller` |
| `Condition:ComparisonError` | targetRevision 拉不到（仓库 token 过期 / 分支不存在）；查 ArgoCD UI 应用的 Conditions |

排查命令直接给到具体集群 context + app + ns，便于复制即用。

## 操作红线

- **只读扫描**：本 skill 全程只读，绝不触发 sync / refresh / rollback。需要后续修复时引导用户走 `argocd` skill。
- **不要把扫描结果当成自动派单依据**：长 Progressing 阈值 15 min 在 rollout/canary 场景可能误报，先看 healthAge 和 Pod 状态做人工判断。
- **GitOps 仓库责任人不在本 skill 范围**：如果需要派单，参考 `ingress-security-scan` skill 的 Step 2 用 git log 查责任人。

## Examples

### Bad

```
扫完直接生成"24 个 OutOfSync 应用需要立即处理" 的告警
→ OutOfSync 多数是 image-updater 自动 commit、staging 应用的 manual drift，绝大多数不是紧急。
   必须区分 OutOfSync(Healthy) 和真异常，OutOfSync 单独放 P2。
```

```
看到 fluent-bit-us-east-1a 是 Progressing 就报警
→ fluent-bit DaemonSet 滚动重启时短暂 Progressing 是正常的。必须用 healthAge ≥ 阈值过滤。
```

```
把 root-apps OutOfSync 当 P2 业务 drift
→ root-apps 是基础设施入口（App of Apps），出 drift 说明有人改了 argocd-apps 仓库且未 sync，
   按 P1 (infra + OutOfSync) 处理，提示用户立即看 argocd-apps 仓库最新 commit。
```

### Good

```
"扫一下 prod 集群"
→ 仅扫 us-prod / us-tech-service / us-data / us-prod-gke / us-tech-service-gke /
       eu-prod / eu-tech-service / eu-data / cn-prod / cn-tech-service / cn-main = 11 个 prod-tier
→ HTML 报告: 顶栏严重度按钮默认 All；用户用集群下拉细看，或 P0 按钮先看红的
→ 文本回复里给 P0 + P1 真异常的简短摘要 + 报告 file:// 路径
```

```
"扫 cn-staging"
→ 单集群快速扫，3 秒返回
→ 还是用 HTML 报告（统一格式），但集群下拉只有一个值
→ 文本回复里直接把异常清单列完
```

```
报告里 1 个集群 "context unreachable"，主流程不挂
→ HTML 顶部黄色 banner 列不可达集群名，集群汇总表里该行打 unreachable pill
→ 告诉用户该集群对应的 kubeconfig 可能要 refresh（`aws eks update-kubeconfig` 等）
```

```
新加了一个集群（e.g. cn-eks-staging-2）
→ 同时改两处：SKILL.md「ArgoCD UI 入口」表 + generate_report.py 的 CLUSTER_ARGOCD 字典
→ 不改 python 字典: 报告里该集群 App 列不可点击（degrade 但不报错）
→ 不改 SKILL.md 表: 后续维护人不知道这集群有 ArgoCD，文档失真
```
