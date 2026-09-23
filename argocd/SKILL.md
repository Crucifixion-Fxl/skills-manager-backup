---
name: argocd
description: Argo CD GitOps 部署管理。查看应用同步状态、触发同步、回滚部署、排查部署问题时使用。
---

# argocd

Argo CD GitOps 持续部署平台，管理 Kubernetes 应用的声明式部署。

> API 用法可查阅 Swagger (`https://argocd-us-tech-service.addx.live/swagger/index.html`) 或 [官方文档](https://argo-cd.readthedocs.io/en/stable/developer-guide/api-docs/)，此处只记录公司特有的规则。

## Description

适用场景：查看应用部署状态、触发同步、回滚部署、排查部署问题。

## Rules

### 多集群连接信息

当前受管 Kubernetes fleet 有 16 个集群；每个集群都有独立 ArgoCD 实例。权威集群清单以 `~/Project/A4x/k8s` 的 `clusters/` 目录和 `~/Project/A4x/argocd-apps` 根目录为准。

| 集群 | argocd-apps 目录 | ArgoCD 地址 | kubectl context |
|------|------------------|-------------|-----------------|
| sg-devops | `aws-125710977284-sg-devops/` | `https://argocd-sg-devops.addx.live` | `arn:aws:eks:ap-southeast-1:125710977284:cluster/sg-eks` |
| us-prod | `aws-302571458622-us-prod/` | `https://argocd-us.addx.live` | `arn:aws:eks:us-east-1:302571458622:cluster/us-eks` |
| us-tech-service | `aws-002497567426-us-tech-service/` | `https://argocd-us-tech-service.addx.live` | `arn:aws:eks:us-east-1:002497567426:cluster/us-eks` |
| us-data | `aws-769494896000-us-data/` | `https://argocd-us-data.addx.live` | `arn:aws:eks:us-east-1:769494896000:cluster/us-prod-data` |
| us-staging | `aws-390709477306-us-staging/` | `https://argocd-us-staging.addx.live` | `arn:aws:eks:us-east-1:390709477306:cluster/us-eks-staging` |
| us-prod-gke | `gcp-a4xcloud-p-us-us-prod/` | `https://argocd-us-prod-gke.addx.live` | `gke_a4xcloud-p-us_us-east4_us-prod-east4-gke` |
| us-tech-service-gke | `gcp-a4xcloud-tech-service-us-us-tech-service/` | `https://argocd-us-tech-service-gke.addx.live` | `gke_a4xcloud-tech-service-us_us-east4_us-tech-service-east4-gke` |
| eu-prod | `aws-740315635167-eu-prod/` | `https://argocd-eu.addx.live` | `arn:aws:eks:eu-central-1:740315635167:cluster/eu-eks` |
| eu-tech-service | `aws-010840394398-eu-tech-service/` | `https://argocd-eu-tech-service.addx.live` | `arn:aws:eks:eu-central-1:010840394398:cluster/eu-eks-tech-service` |
| eu-data | `aws-769494896000-eu-data/` | `https://argocd-eu-data.addx.live` | `arn:aws:eks:eu-central-1:769494896000:cluster/eu-prod-data` |
| eu-staging | `aws-390709477306-eu-staging/` | `https://argocd-eu-staging.addx.live` | `arn:aws:eks:eu-central-1:390709477306:cluster/eu-eks-staging` |
| cn-prod | `aws-741924744516-cn-prod/` | `https://argocd-cn.addx.live` | `arn:aws-cn:eks:cn-north-1:741924744516:cluster/cn-eks` |
| cn-tech-service | `aws-589899215075-cn-tech-service/` | `https://argocd-cn-tech-service.addx.live` | `arn:aws-cn:eks:cn-north-1:589899215075:cluster/cn-eks-tech-service` |
| cn-dev | `aws-801447536674-cn-dev/` | `https://argocd-cn-dev.addx.live` | `arn:aws-cn:eks:cn-north-1:801447536674:cluster/cn-eks-dev` |
| cn-staging | `aws-801447536674-cn-staging/` | `https://argocd-cn-staging.addx.live` | `arn:aws-cn:eks:cn-north-1:801447536674:cluster/cn-eks-staging` |
| cn-main (TKE) | `tencent-100014919455-cn-main/` | `https://argocd-cn-k8s.addx.live` | `tke-cn-k8s` |

**认证方式：**
- Web UI：Casdoor SSO（飞书账号登录），点击 "LOG IN VIA CASDOOR"
- API：`Authorization: Bearer $ARGOCD_AUTH_TOKEN`，自签证书加 `-k`
- 凭据来源：优先用环境变量或用户显式提供的 token；Codex 不读取 `~/.claude/password`。需要本地持久化时使用 Codex 专用 `~/.codex/password` 或用户指定的凭据文件。

**ArgoCD namespace：** 所有集群统一为 `argo-cd`

### App of Apps 模式

所有集群均启用了 App of Apps 模式，通过 Root Application 自动管理子应用。

**核心架构：**
- 每个集群有一个 `root-apps` Application，监听 `DEV/argocd-apps` 仓库的对应目录
- 仓库地址：`https://gitlab.addx.ai/DEV/argocd-apps.git`（main 分支）
- Git 凭据：Deploy Token `Argocd-deploy`（Reporter，只读），通过 ExternalSecret 从 Vault 同步

**目录映射（argocd-apps 仓库 → 集群）：** 使用上方表格的 `{cloud}-{accountId}-{cluster}/` 目录。`argocd-apps` 根目录中可能残留历史目录，变更前以 `k8s/clusters/` 和当前 root Application 为准，不要按旧短目录名创建新应用。

**Application / Project 划分：** 一个业务仓库可有多个独立渲染的 Application，每个
Application（含全部 sources）只绑定一个 AppProject。按权限职责划分 runtime/infra，
批准的平台 claim 可留 runtime；不能在同一 Application 内按 kind 自动分流。
每个资源只有一个 Application 管理，父 overlay 不得重复渲染已交给 infra 的对象。
存量拆分须独立评审 prune/finalizer、tracking 与反向交接；只改 Project 不完成资源拆分。
只能使用目标集群已批准的精确合同，owner 专属名称仍是待实现目标；平台 IAM/ProviderConfig
边界和审批不因命名改变。完整规则见
[部署权限事实表](../cicd-developer/references/data/permission-boundaries.yaml)。

**应用上线流程：**
1. 在 `argocd-apps` 仓库对应集群目录下添加 Application YAML
2. 提交 MR 到 main 分支
3. MR 合并后，Root Application 自动检测并创建应用
4. 无需手动 `kubectl apply`

**基础设施 YAML 位置（k8s 仓库）：**
- 集群级 CICD 配置：`clusters/<cluster-dir>/cicd/`
- ArgoCD values：`clusters/<cluster-dir>/cicd/argocd/values-override.yaml`
- Self-manage Applications：`clusters/<cluster-dir>/cicd/self-manage/`
- Git 凭据 / ESO / Kyverno 等：先查同集群 `cicd/` 下现有文件，不要使用旧 `eks/` 或 `tke/` 路径
- Vault KV 路径：`secret/cicd/argocd/git-credentials`（三个 Vault 实例均有）

### 状态矩阵

| Sync Status | Health Status | 含义 | 处理方式 |
|-------------|---------------|------|---------|
| `Synced` | `Healthy` | 正常 | 无需操作 |
| `OutOfSync` | `Healthy` | 有未部署变更 | 安全，可同步 |
| `Synced` | `Degraded` | 已部署但运行异常 | 查 Pod 日志和事件 |
| `OutOfSync` | `Degraded` | 未部署且运行异常 | 先修 Degraded 再同步 |
| `Synced` | `Progressing` | 正在部署中 | 等待完成 |

### Argo CD 3.x 逐资源健康证据

Argo CD 3.x 默认 `controller.resource.health.persist=false`，逐资源 health 存在
appTree 外部缓存，不写入 `Application.status.resources[].health`。先检查：

```bash
kubectl --context "$KUBE_CONTEXT" -n argo-cd get application "$APP" \
  -o jsonpath='{.status.resourceHealthSource}{"\n"}'
```

结果为 `appTree` 时，`status.resources[].health` 为空是正常行为，不能据此断言
custom health 未生效。通过已认证的 Argo CLI/UI/API 查询 resource tree：

```bash
argocd app resources "$APP" --argocd-context "$ARGOCD_CONTEXT" \
  --app-namespace argo-cd --output tree=detailed
```

这里使用 `app resources` 查询 controller 缓存的资源树。`app get --output tree=detailed`
在部分 3.x CLI 中仍从 Application 状态绘制资源表，Health 可能为空，不能把它当作
appTree 证据。本次在 CLI `v3.5.1+109ca7c.dirty` 验证：前者返回子资源的 Health/Reason，
后者逐资源 Health 为空。

若 Argo 会话不可用，但已授权且具备目标集群的 Kubernetes/Argo 读取权限，可用 core
模式读取同一资源树；显式选择目标上下文，不切换全局 context：

```bash
argocd app resources "$APP" --core --kube-context "$KUBE_CONTEXT" \
  --app-namespace argo-cd --output tree=detailed
```

同一工作环境的 core 调用按顺序执行，避免本地临时服务端口冲突。core 的权限或缓存
读取失败时仍报告证据缺口，不能用 Lua 本地计算冒充 controller 的已缓存结果。

API 等价入口是
`GET /api/v1/applications/{app}/resource-tree?appNamespace=argo-cd`。认证不可用时必须
报告缺少 appTree 证据，不得把 Application CR 的空 health 当作失败或成功。对于
custom health，可用 live resource 与 live `argocd-cm` 执行
`argocd admin settings resource-overrides health`，它能证明 Lua 对当前对象的计算
结果，但不能单独证明 controller 已把结果写入 appTree。

不要为了方便 `kubectl` 查询而开启
`controller.resource.health.persist=true`；官方说明该设置会增加 Application CR
更新频率和 application-controller/Kubernetes API 负载。整体 Application health
仍是直接子资源 health 的聚合值，排障时还要核对目标资源自身的 conditions/events。

参考：[argocd app resources 命令](https://argo-cd.readthedocs.io/en/stable/user-guide/commands/argocd_app_resources/)、
[Argo CD 3.0 health 存储变更](https://argo-cd.readthedocs.io/en/release-3.3/operator-manual/upgrading/2.14-3.0/#health-status-in-the-application-cr)、
[Argo CD 3.3 参数说明](https://argo-cd.readthedocs.io/en/release-3.3/operator-manual/argocd-cmd-params-cm-yaml/)。

### 操作红线

- **按变更类型选择回滚**：业务纯镜像回退复用已验证的旧制品，走下方精确 pin 与执行交接，不以 revert 后重新构建作为必经步骤；配置变更通过源仓库 revert / fix MR 收敛，数据与不可逆副作用另行评估。
- **生产执行必须有具体授权**：先展示目标集群、Application、镜像与 digest、操作范围和影响。已有用户明确审阅并批准的精确方案仍匹配当前证据时，直接按该授权执行，不重复确认；授权不覆盖的目标、动作或后续 release 不得顺带执行。
- Application 原生 `rollback` 要求 auto-sync 已关闭；App of Apps 的 root 可能恢复 child 的自动同步配置，必须核对实际管理关系。直接 Rollout `undo` 修改期望模板，可能被 Argo CD selfHeal 覆盖；`abort` 仅适用于尚未完成且有可用 stable 版本的发布，不能当作已完成发布的历史回退。
- 回滚候选以实际保留的 Application history、Rollout/ReplicaSet 历史与 Harbor 制品证据为准。Application 默认历史上限为 10，可由 `revisionHistoryLimit` 调整；这不是统一回滚授权范围，也不保证有 10 个可用旧版本。历史缺失不得伪造，上一条部署记录不等于最后健康版本。
- **禁止通过 API 删除生产环境应用**
- **禁止直接修改 argocd-apps 仓库 main 分支**，必须通过 MR
- 父 Application 管理 child Application CR 时，先读取 child 的 live
  `argocd.argoproj.io/sync-options`：desired 删除 `Replace=true` 的同一次 sync 仍可能
  执行 replace。必须从 root `status.operationState.syncResult.resources[].message` 验证实际
  verb；root `Succeeded` 不能证明 child status/history 被保留
- 任何 Application history/status live 修复都必须先确认 CRD 是否暴露 status
  subresource。若只能 patch 主资源，会增加 generation 并触发 reconcile；未经用户对
  最终对象清单和 patch 方案的明确批准不得执行，且不得恢复旧 `operationState`

### 常见工作流

**部署健康巡检：** 列出所有应用 → 筛选非 Synced/Healthy → 查看 resource-tree 定位异常资源 → 检查日志

**业务纯镜像快速回滚执行交接：**

1. 从同一插件读取 [business-image-rollback 合同](../cicd-developer/references/business-image-rollback.md)
   和 [历史版本查询 playbook](../cicd-developer/troubleshooting/business-image-rollback-history.md)。
   由 `cicd-developer` 查询候选、准备 pin/release Git MR；本 skill 只接收其明确的执行交接。
   首版只执行已纳管、单 source Kustomize 的 Deployment/Rollout；Helm、多 sources 等
   未支持形态只查询并输出 Ops Todo，不从相似流程推导执行。
   兄弟 skill 或合同不可用时，停在只读证据与方案，不猜测写入步骤。
2. 按合同核对 Application history 中当次 source/image override、Rollout/ReplicaSet、Harbor
   tag/digest 与架构，确认旧制品与当前配置、数据库兼容。Git revision、history ID、Rollout
   revision 和镜像 tag/digest 分开记录；没有健康证据的候选不得称为 last-known-good。
   source 使用移动分支时，须有可核验的短暂冻结及执行前后同一 source SHA 证据；缺失即
   STOP，不通过修改 `targetRevision` 绕过。本通路按合同固定完整镜像 alias 集合。
3. **pin 生效本身可能触发自动回退**：具体授权必须先覆盖 pin MR 合并/生效、必要的 live
   override 及验收，不能先合并再申请执行批准。授权后确认 live Image Updater 的各 alias
   `allow-tags` 已分别限制到审阅的目标 SHA。
   Git recovery seed 可能被 root 的 `ignoreDifferences` 保留 live 值，故 MR 合并不证明镜像
   已切换。保留 auto-sync/selfHeal，不为本通路暂停全局 updater 或关闭自动同步。
4. pin 已生效后，若 updater 已自动切到目标，直接验收，不重复写。仍需要切换时，在上述
   具体授权范围内，按合同修改准确 Application 的 live image override，保留完整 alias
   集合和所有无关字段。执行前重新取证并检查目标 UID、source SHA、当前镜像、
   pin 和操作状态；并发变化、未知进行中的 operation、缺少权限或合同任一
   STOP 条件触发时停止，重新核对方案，不改走 Rollout `undo` 或另一条写路径。
5. 关联本次 operation/history、workload/ReplicaSet 镜像、Pod 实际 imageID 的平台对应
   digest，并完成约定业务探针与观察窗口。`Synced/Healthy` 或 API 成功不足以单独宣称
   回滚完成；没有新 operation 时，也不能把旧 history 当作本次执行证明。
6. 保留 pin 与恢复证据，解除 pin 走独立 release 交接：先确认新候选与恢复后的选版结果，
   防止重新选中坏版本。不得因 TTL 到期、回滚成功或笼统“恢复自动发布”而自行放宽策略；
   仅在 release 的具体范围已有授权时执行其声明动作。

**添加新应用：**

1. 开发者在应用 Git 仓库创建 K8s 配置（base + overlays）
2. 运维在 Vault 中创建密钥路径
3. 在 `DEV/argocd-apps` 仓库对应集群目录下添加 Application YAML，提交 MR
4. MR 合并后 Root Application 自动创建应用

### Hook-only 变更的同步判定

只修改 `PreSync`、`Sync` 或 `PostSync` Hook template 的 commit 可能被 Argo CD
compare 到最新 revision，但不一定创建新的 sync operation。禁止仅凭
`status.sync.revision` 已更新就断言 Hook 已执行。

执行或验证 Hook-only 变更时：

1. 合并前记录 `status.operationState.startedAt`、
   `status.operationState.syncResult.revision` 和最新 `status.history` ID/revision。
2. 合并后先观察 automated sync。只有出现新的 operation/history，且 operation
   revision 对应目标 commit，才能把本次执行归因给该 commit。
3. 同时捕获 Hook Job/Pod 的 UID、selector、实际节点、退出码和日志。带
   `HookSucceeded` 删除策略的对象可能很快被清理，不能等到事后只看 Application
   状态。
4. 若 compare revision 已更新但 operation/history 没变化，结论必须是“已比较，
   未证明执行”。需要手工 sync 时，生产环境仍必须说明影响和回滚并等待明确确认。
5. Image Updater 或其他参数更新可能随后触发真正的 automated sync。通过
   operation 时间、revision 和参数差异区分触发源，不要把后续 sync 错算成最初的
   Hook-only commit。

CLI/API 返回成功也不是最终证据；必须以 Application operation state、history 和
Hook runtime 证据闭环。如果认证失败或 token 过期，应明确报告没有发生 operation，
不得静默改用另一条生产写路径。

## Examples

### Bad

```
把 Rollout undo 当成不会被覆盖的一键回滚，或 pin 未生效就修改 live image override
→ desired state / Image Updater 仍可能将坏版本推回；必须遵守具体授权与 pin 执行交接
```

```
直接在 argocd-apps main 分支 push Application YAML
→ 违反红线：必须通过 MR 流程
```

### Good

```
业务纯镜像回退：
1. 查询 prod-payment 的部署历史、ReplicaSet 与 Harbor，核实候选旧镜像和业务健康证据
2. cicd-developer 准备精确 pin MR 与 live 计划；用户审阅并授权合并、生效、必要切换与验收
3. 新取证与批准的集群、Application、source SHA 和目标 digest 一致；合并并确认 live pin 生效
4. 保留 auto-sync/selfHeal；已自动切回则直接验收，否则按合同修改准确 image override
5. 以本次 operation/history、workload、Pod digest 和业务观察窗口证明恢复；保持 pin
6. 修复版本就绪后另走 release 流程，不直接放开 updater 重选坏版本
```

```
添加新应用：
1. 在 argocd-apps 仓库 `aws-302571458622-us-prod/` 目录下创建 my-service.yaml
2. 提交到 feat/add-my-service 分支，创建 MR
3. MR 合并后确认 ArgoCD UI 中应用自动出现且 Synced + Healthy
```

```
验证 Hook-only 变更：
1. 记录当前 operation startedAt、syncResult revision 和 history ID
2. 合并后发现 sync revision 已更新，但 operation/history 不变
3. 报告“已比较，未证明 Hook 执行”，不宣称部署完成
4. 获得生产手工 sync 批准后触发同步
5. 以新 history、Hook Pod UID、exit 0、日志和实际节点完成验证
```
