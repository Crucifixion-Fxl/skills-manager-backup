---
name: argocd-app-stuck-phase-running
description: ArgoCD app `phase=Running` 卡几小时；sync 不完成；manifest 变更不生效。这个面上的症状下面有 5 种不同根因——playbook 先把模式判出来再修。
---

# Playbook：ArgoCD app 卡 `phase=Running`

## 症状

```
kubectl -n argo-cd get app <app> -o jsonpath='{.status.operationState.phase}'
```

返回 `Running`，已经 Running >30 分钟。同时：

- `kubectl -n argo-cd get app <app> -o yaml | yq '.status.operationState'` 显示 `phase: Running`，`startedAt` 是 30 分钟前
- 新 commit / Image Updater 写回都触发不了 fresh sync
- ArgoCD UI 显示 "Syncing" 永远转

**5 种不同根因都长这个面**。每种修法不一样。**不要** 没判别清楚就乱套修法。

## 修复授权

本 playbook 默认只收集证据并给出修复建议。下面任何 `patch`、`delete`、`refresh` 或
`promote` 命令都**不得**在诊断请求中执行；命令出现在文档中不是授权。用户明确要求实施后，
先说明目标、影响和回滚方式，优先提交 GitOps 修复；涉及 ArgoCD 控制面的 live patch/delete
必须交给相应的操作流程执行。

## 诊断顺序

### Step 0. 先用 ArgoCD CLI 只读收集证据（可选）

如果当前会话有 ArgoCD CLI 登录态，AI 可以先跑只读命令做定位：

```
argocd app get <app> -o json
argocd app resources <app>
argocd app history <app>
argocd app manifests <app>
argocd app diff <app>
```

这些命令只能用于收集 Application 状态、资源 health、history revision、rendered manifests 和 diff。**不要** 在定位阶段执行会改变状态的命令，例如：

```
argocd app sync --force --replace <app>
argocd app rollback <app> <revision>
argocd app terminate-op <app>
argocd app delete <app>
argocd app refresh <app> --hard
```

如果没有 CLI 登录态，用下面的 kubectl 只读命令继续。

### Step 1. 一把抓 4 个信号

```
APP=<app-name>
NS=argo-cd

# 信号 A：operation state
kubectl -n $NS get app $APP -o yaml | yq '
  .status.operationState | {
    phase: .phase,
    retryCount: .retryCount,
    message: .message,
    startedAt: .startedAt,
    finishedAt: .finishedAt,
    syncResultEmpty: (.syncResult.resources | length == 0)
  }
'

# 信号 A2：当前是否仍有真正的 in-flight operation
kubectl -n $NS get app $APP -o yaml | yq '.operation // null'

# 信号 A3：App 汇总状态
kubectl -n $NS get app $APP -o yaml | yq '{
  sync: .status.sync.status,
  health: .status.health.status,
  operation: (.operation // null),
  operationStatePhase: (.status.operationState.phase // ""),
  operationStateFinishedAt: (.status.operationState.finishedAt // "")
}'

# 信号 B：rollout pause 状态（BlueGreen / Canary）
kubectl get rollout -A | grep -E "^$NS|<app-namespace>"
# 对匹配的 rollout：
kubectl -n <app-ns> get rollout <name> -o yaml | yq '.status | {
  phase: .phase,
  pauseConditions: .pauseConditions,
  message: .message
}'

# 信号 C：ArgoCD application-controller pod 状态（self-managed 集群才看）
kubectl -n argo-cd get pods -l app.kubernetes.io/name=argocd-application-controller -o yaml | yq '
  .items[] | {
    name: .metadata.name,
    restarts: .status.containerStatuses[0].restartCount,
    lastState: .status.containerStatuses[0].lastState
  }
'

# 信号 D：跑着的 pod 里有没有奇怪进程残留（罕见）
kubectl -n <app-ns> get pods -l app=<app> -o yaml | yq '
  .items[] | {
    name: .metadata.name,
    cmd: .spec.containers[0].command
  }
'
```

### Step 2. 多个信号对照 5 种模式

| 信号 A（operation） | 信号 B（rollout） | 信号 C（controller） | 信号 D（process） | 模式 |
|---|---|---|---|---|
| `phase=Running`、`retryCount=null`、`syncResult.resources=[]`、message 含 "waiting for completion of hook" | 正常 | 正常 | 正常 | **模式 1：僵尸 operation（hook 卡住）** |
| `phase=Running`、retryCount 正常 | rollout `phase=Paused`、`pauseConditions[]` 含 BlueGreenPause 或 CanaryPauseStep | 正常 | 正常 | **模式 2：BlueGreen / Canary 暂停等 promote** |
| `phase=Running`、message 含 "sync timeout"、retryCount=null | 正常 | controller pod RESTARTS>0、`lastState.reason=OOMKilled` 等 | 正常 | **模式 3：Controller OOM / crash** |
| `phase=Running` | 正常 | 正常 | container PID 1 是 `sleep infinity` 或类似 | **模式 4：sleep-infinity 残留（Deployment->Job 转换没清干净）** |
| `.operation=null`、`.status.sync.status=Synced`、`.status.health.status=Healthy`，但 `.status.operationState.phase=Running` 且有 `finishedAt` | 正常 | 正常 | 正常 | **模式 5：stale operationState（UI 假 Syncing）** |

信号匹配不上任一行 → STOP，把原始信号给用户。**新模式**——根因诊断完后写新 playbook，**不要** 凭印象猜。

## 各模式修法

### 模式 1 —— 僵尸 operation

operation 在跑一个永不完成的 hook（BeforeHookCreation + retry=3 = 永远循环；HookSucceeded 至少最终 finalize Failed 然后退避）。

```
kubectl -n argo-cd patch app <app> --type=json \
  -p '[{"op":"remove","path":"/operation"}]'

# 强制 fresh refresh
argocd app refresh <app> --hard
```

验证：
```
kubectl -n argo-cd get app <app> -o jsonpath='{.status.operationState.phase}'
# 期望：空 / 不是 Running，或新 sync 已启动
```

如果 hook 本身有问题（如 PreSync Job 里有 `sleep infinity` 残留脚本），还要修脚本——见模式 4。

### 模式 2 —— BlueGreen / Canary 暂停等 promote

从 ArgoCD 角度这 **不是** 卡——是正确行为，sync 在等人按 promote 按钮。**不要** 在这里 remove /operation。

```
kubectl argo rollouts promote <rollout-name> -n <app-namespace>
```

ArgoCD sync 在 rollout 恢复后几秒完成。

如果用户期望的是自动 promote（不是暂停）：Rollout YAML 里某处 `autoPromotionEnabled: false`。改 manifest 而不是手动 promote。

如果 Rollout 不是单纯 pause，而是 `stableRS` 仍指向 bootstrap / 不存在镜像，同时 Application live image 已经是发布 SHA，跳到 [image-pull-failure.md](image-pull-failure.md) 的“模式 6”。这类问题不要靠 remove `/operation` 修。

### 模式 3 —— Controller OOM / crash

argocd-application-controller pod OOMKilled，重启比能 drain 的 operation 还快。新 sync 排队但永远 finalize 不了。

```
# 确认
kubectl -n argo-cd describe pod -l app.kubernetes.io/name=argocd-application-controller | grep -A3 "Last State"

# 临时拉高内存 limit（立即修）
kubectl -n argo-cd patch statefulset argocd-application-controller \
  --type=json -p='[{"op":"replace","path":"/spec/template/spec/containers/0/resources/limits/memory","value":"8Gi"}]'

# 删 pod 让新 limit 生效
kubectl -n argo-cd delete pod -l app.kubernetes.io/name=argocd-application-controller
```

如获授权实施，必须把同样改动提交到
`k8s/clusters/<cluster>/cicd/argocd/values-override.yaml`（或该集群实际 ArgoCD values override）
让它永久生效；否则 ArgoCD self-manage 会回滚。

验证：
```
kubectl -n argo-cd get pods -l app.kubernetes.io/name=argocd-application-controller -w
# 期望：RESTARTS 保持 0，所有卡住的 app 在 ~5min 内 drain 到 Succeeded
```

如果多 app 同时卡，新 controller 会按序处理；**不要** 同时每个都 remove /operation——会做无用功。

### 模式 4 —— sleep-infinity 残留

Deployment 转成 Job 的过去某次迁移里，Job manifest 里留了 `command: ["sleep", "infinity"]`（debug 时加的）。每次 sync 启一个新 Job 一直 sleep。

```
# 看
kubectl get job <name> -n <app-ns> -o yaml | yq '.spec.template.spec.containers[0]'
# 找 command: [sleep, infinity] 之类
```

去 app 仓库改 `k8s/` manifest：去掉 sleep command（Job 的 `command:` 应该是真业务命令，或者镜像 ENTRYPOINT 正确时不写）。commit + sync 后：

```
# 杀掉正在跑的卡住 Job
kubectl delete job <name> -n <app-ns>
```

下次 sync 会建一个正常 exit 0 的 Job。

### 模式 5 —— stale operationState（UI 假 Syncing）

Application 已经没有真正 in-flight operation，业务资源也都 `Synced / Healthy`，但
`.status.operationState.phase` 还残留 `Running`，并且同一个 operationState 里已经有
`finishedAt`。ArgoCD UI 可能因此一直显示 "Syncing"。

**判定条件必须全部满足**：

```bash
kubectl -n argo-cd get app <app> -o json | jq -r '{
  sync: .status.sync.status,
  health: .status.health.status,
  operation: (.operation // null),
  operationStatePhase: (.status.operationState.phase // ""),
  operationStateFinishedAt: (.status.operationState.finishedAt // "")
}'
```

期望看到：

- `sync=Synced`
- `health=Healthy`
- `operation=null`
- `operationStatePhase=Running`
- `operationStateFinishedAt` 非空

修法分两步。

Step 1：只清 stale status，不动 spec / 业务资源：

```bash
kubectl -n argo-cd patch application.argoproj.io/<app> --type=json \
  -p '[{"op":"remove","path":"/status/operationState"}]'
```

验证 UI 不再假 Syncing：

```bash
kubectl -n argo-cd get app <app> -o json | jq -r '{
  sync: .status.sync.status,
  health: .status.health.status,
  operation: (.operation // null),
  operationState: (.status.operationState // null)
}'
```

Step 2：如果 UI 的 "Sync OK" 徽标消失，触发一次 no-op sync 让 ArgoCD 写回干净的
`operationState.phase=Succeeded`。优先使用 ArgoCD CLI；如果 CLI 在当前环境卡住，
可用 Application `operation` 字段触发同 revision sync。

```bash
REVISION=$(kubectl -n argo-cd get app <app> -o jsonpath='{.status.sync.revision}')

kubectl -n argo-cd patch application.argoproj.io/<app> --type=merge -p "{
  \"operation\": {
    \"initiatedBy\": {\"username\": \"noop-sync\"},
    \"sync\": {
      \"revision\": \"${REVISION}\",
      \"syncOptions\": [
        \"CreateNamespace=false\",
        \"ServerSideApply=true\",
        \"RespectIgnoreDifferences=true\"
      ]
    }
  }
}"
```

验证：

```bash
kubectl -n argo-cd get app <app> -o json | jq -r '{
  sync: .status.sync.status,
  health: .status.health.status,
  operation: (.operation // null),
  operationState: {
    phase: .status.operationState.phase,
    message: .status.operationState.message,
    finishedAt: .status.operationState.finishedAt
  }
}'
```

期望：

- `operation=null`
- `operationState.phase=Succeeded`
- `operationState.message="successfully synced (all tasks run)"`

如果 Step 1 后 controller 很快又写回 `Running`，说明不是 stale status；回到 Step 1
重新判定其他模式。

## 为什么不走 <替代方案>

- **`argocd sync --force --replace <app>`** —— 模式 1 不解决（operation 在 in-progress，不是 sync queue）。模式 3 在 controller 健康之后才有用。
- **重启 ArgoCD repo-server / dex-server / redis** —— 无关。卡的状态在 application-controller。重启别的东西没用。
- **删 Application 重建** —— 模式 1/3 都能修，但丢 sync history。先试针对性修法。
- **加 `argocd.argoproj.io/sync-options: Force=true` annotation** —— 没用；卡的是 operation phase，不是 options。
- **只清 `status.operationState` 后就结束** —— 模式 5 会让 UI 不再假 Syncing，但 "Sync OK" 可能因为缺少最近一次 operation result 而消失；需要 no-op sync 写回 `Succeeded`。

## 参考

- Hook 卡死反例：BeforeHookCreation + retry 与 HookSucceeded 清理语义不同，不能只看 Pod 还在不在。
- Controller OOM 反例：ArgoCD 自管理时，controller OOM 需要先临时扩容并重建 controller pod，才能恢复自愈。
- BlueGreen pause 反例：`autoPromotionEnabled: false` 是等待人工 promote，不是 zombie pod。
- Deployment 到 Job 迁移反例：旧 `deploy.sh` / 启动命令残留可能让新工作负载永远不退出。
- stale status 反例：`.operation=null` 且 `finishedAt` 已存在时，清的是过期 status，不是终止真实 sync；清后用 no-op sync 恢复 UI 的 Sync OK operation result。
