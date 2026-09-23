---
name: pod-runtime-crash
description: Pod 启动后崩溃（CrashLoopBackOff / OOMKilled / 探针失败 / CreateContainerConfigError）。诊断顺序：先 ArgoCD UI 看 logs + events，再判 6 种常见根因。
---

# Playbook：Pod 运行时崩溃

## Validator gate

This route declares `related_validators: []`: no static validator can prove a
runtime crash root cause. Use the live logs, Events, Pod status, image identity,
and controller state required below as the deterministic evidence gate. Do not
run `validate.sh` against a repository root or unrendered Helm
`templates/*.yaml`. If the repair changes a Helm chart or values, render the
exact release with its production values and validate the rendered manifests,
then run the repository's own chart and code tests.

## 症状

```
kubectl get pods -n <ns>
```

Pod 状态：`CrashLoopBackOff` / `Error` / `OOMKilled` / `CreateContainerConfigError`。重启计数持续涨。

应用层：业务无响应；ALB 健康检查失败把后端摘了；下游服务连不上。

## 诊断顺序（开发者优先走 ArgoCD UI）

### Step 1. 看 logs

ArgoCD UI → 找到 Pod → **Logs** tab（开发者自助，等价运维 `kubectl logs <pod> -n <ns>`）。

关注：
- 应用代码 panic / fatal log（最常见——业务逻辑错）
- "missing env var" / "cannot connect to..." → 跳 `troubleshooting/secrets-env-missing.md`
- "no such file or directory" → 镜像里缺资源 / config 路径错
- 完全无日志 → 应用还没启动到 main，跳 Step 2 看 events

### Step 2. 看 events

ArgoCD UI → Pod → **Events** tab（等价 `kubectl describe pod <pod> -n <ns>`）。

关注：
- `Killing: Container <name> failed liveness probe, will be restarted` → 探针失败
- `OOMKilled` → 内存超 limits（看 Step 5）
- `FailedMount` → PVC / Secret / ConfigMap 没就绪
- `Failed to pull image` → 跳 `troubleshooting/image-pull-failure.md`
- `CreateContainerConfigError` → Secret / ConfigMap 引用错（看 Step 4）

### Step 3. 根据信号选模式

| 信号 | 模式 |
|---|---|
| logs 显示应用启动几秒后 panic / fatal | **模式 1：应用代码错** |
| logs 显示 "missing env" / "DB connection refused" | **模式 2：env / Secret 缺失** —— 跳 secrets-env-missing playbook |
| events: liveness probe failed | **模式 3：探针配置错** |
| events: OOMKilled | **模式 4：内存超 limits** |
| events: FailedMount Secret/ConfigMap | **模式 5：envFrom 引用对象不存在** |
| events: ImagePullBackOff | **模式 6** —— 跳 image-pull-failure playbook |
| status: CreateContainerConfigError | **模式 5（同）** |

## 各模式修法

### 模式 1：应用代码错

业务逻辑 panic / 启动初始化失败。**这不是部署问题**——是业务方的 bug。

**联动**：业务方修复 → 重新 CI 推 image → ArgoCD sync 拉新 SHA → Rollout 滚新版本。Image Updater 自动写回 sha，无需手动 sync（除非 image-updater 也卡了，跳 argocd-app-stuck-phase-running playbook）。

**恢复候选**：转到 [历史版本与快速回滚 playbook](business-image-rollback-history.md)，
核对旧镜像、健康证据及当前配置/数据兼容性，再准备精确 pin MR 和 `argocd` 执行交接。
无需先 revert 源代码重建镜像。不能直接选“上一个 revision”执行 Rollout undo：Argo CD
可能覆盖工作负载改动，Image Updater 也可能再次选中坏镜像。pin 合并本身就可能触发部署，
必须在具体生产授权之后。只读权限和 UI 中有按钮不代表具备执行权限。

### 模式 2：env / Secret 缺失

跳 `troubleshooting/secrets-env-missing.md`。沿 Pod → Secret → ExternalSecret → ClusterSecretStore → Vault 链反查。

### 模式 3：探针配置错

ArgoCD UI 看 Rollout / StatefulSet 的 probe 配置：

- **path 不对**：应用没暴露 `/health` 端点（业务方代码加 healthz handler）
- **port 不对**：probe 写的 port 跟 container port 不一致
- **initialDelaySeconds 太短**：应用启动慢（cold start / 大 DB migration），probe 先于应用就绪
- **timeout 太短**：应用响应慢，3s 不够

修法：改 `k8s/base/rollout.yaml` 或 `statefulset.yaml` 的 probe 段，commit + ArgoCD sync。

### 模式 4：OOMKilled

容器内存 working set 超过 `resources.limits.memory`。kernel 直接 SIGKILL，应用层捕获不到。

**先排查**：
- 看 `kubectl top pod <pod>` 历史峰值（运维跑或看 Grafana）
- 看应用是否有内存泄漏（同 metric 持续涨）
- 是否最近改了行为（如新 cron job 拉大量数据）

**修法**：
- 短期：调高 `resources.limits.memory`（改 base/rollout.yaml；遵守 cost-tiering）
- 长期：业务方修内存泄漏 / 减少缓存 / 流式处理替代全量加载

JVM 应用：检查 `-XX:MaxRAMPercentage` 配置（dockerfile-java.txt 模板默认 75%）；不写让 JVM 默认按 cgroup memory limit 算。

### 模式 5：envFrom 引用对象不存在

events 显示：
```
Error: secret "<app>-db-secret" not found
Error: configmap "<app>-config" not found
```

**先看是不是 ExternalSecret 没就绪**：
```bash
# 运维执行
kubectl -n <ns> get externalsecret <es-name>
# READY=False → 跳 secrets-env-missing playbook
```

**ConfigMap 没就绪**：
- ConfigMap 在 git 里写了吗？
- ArgoCD 同步成功了吗？（看 Application 的 Synced / Healthy 状态）
- 引用对象名字拼错？（Pod spec 的 `secretRef.name` / `configMapRef.name` 跟实际对象名要严格匹配）

### 模式 6：ImagePullBackOff

跳 `troubleshooting/image-pull-failure.md`。

## ArgoCD UI 查询速查（以实际只读权限为准）

| 想看 | UI 路径 | kubectl 等价（仅运维） |
|---|---|---|
| Pod 日志 | App → Pod → Logs | `kubectl logs <pod> -n <ns>` |
| Pod 事件 | App → Pod → Events | `kubectl describe pod <pod> -n <ns>` |
| Rollout 历史 | App → Rollout → History | `kubectl argo rollouts history <name>` |

回滚、promote、重跑 Job 均为写操作，需按对应运维流程验证权限、影响及授权；不属于本表
的查询能力。排查 Secret 只查引用、状态与事件，不读取或展示实际值。

## 为什么不走 <替代方案>

- **改 restartPolicy: Never** —— 只是不重启而已，不解决根因；Pod 仍然 Error
- **加 initContainer sleep 拖延启动** —— hack，掩盖问题；探针 + initialDelaySeconds 才是正解
- **改 livenessProbe.failureThreshold 拉很大** —— 让应用永远不重启，问题更隐蔽
- **设置 resources.limits.memory: 0 让 OOM 不杀**（K8s 不支持这种语法，但有人想试）—— 没用，cgroup 还是会管
- **删 Pod 让 K8s 重建** —— Pod 是 Rollout 管的，单删 Pod 立刻被 RS 重建，且新 Pod 还会 crash

## 参考

- recipes/k8s/rollout.yaml.tmpl（probe + resources 默认值 + 注释）
- references/cost-tiering/<kind>.yaml（资源 limits 分档建议）
- troubleshooting/secrets-env-missing.md
- troubleshooting/image-pull-failure.md
- troubleshooting/argocd-app-stuck-phase-running.md（如果 Rollout 不滚 / sync 卡）
