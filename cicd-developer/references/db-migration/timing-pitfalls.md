## 3. 时序坑：必须理解的两件事

### ⚠️ 时序坑 1：PreSync hook 跑在 ExternalSecret 同步之前

ArgoCD 的 sync 顺序固定为：

```
PreSync hooks  →  普通资源 (Deployment/Service/ExternalSecret/...)  →  PostSync hooks
```

也就是说当 PreSync Job 启动的那一刻，**ExternalSecret 资源对象本身可能还没被 apply 到集群**。如果你这次 commit 改了 ExternalSecret 的 spec（加字段、改 Vault 路径），新的 ExternalSecret 还没生效，ESO 还没机会重新拉 Vault 写入 Secret，**migration Job Pod 拿到的 Secret 内容是上一次 sync 后的旧版本**。

**正确做法（二选一）：**

- **A**（推荐）：**不要在同一个 commit 里同时改 ExternalSecret 和镜像 tag**。如果新版镜像需要新字段，分两个 commit：
  1. 先 commit ExternalSecret 改动 + 一个无关紧要的小改动触发 sync。等 ArgoCD sync 完 + ESO 写入完成（约 1-2 分钟），在 ArgoCD UI 里看目标 Secret 的 Data（开发者无 kubectl 权限）确认新字段已经在；必要时让运维 `kubectl get secret -o yaml` 核对
  2. 再 commit 镜像/代码改动，触发 migration Job 跑

- **B**：把 ExternalSecret 也声明为 PreSync hook 并设更小的 sync-wave。**不推荐**，会让 ExternalSecret 的生命周期和普通资源不一致，未来很难维护

### ⚠️ 时序坑 2：Job Pod 的 envFrom 是启动时刻的快照

K8s 的 `envFrom: secretRef` 在 Pod 启动那一刻把 Secret 内容注入到环境变量。**Pod 跑起来之后 Secret 即使被更新，已经在跑的 Pod 看不到**——只有重新创建的 Pod 才能拿到新值。

含义：
- 不能依赖"反正待会 ESO 会同步过来"。Secret 必须在 Job Pod 启动那一刻就是正确的
- 如果发现 migration Job 拿到的 Secret 不对，**修完 Secret 后必须 delete Job 让 ArgoCD 重新创建**，不能等（开发者在 ArgoCD UI 里对 Job 资源点 Delete，ArgoCD 会重建；不能也不需要 `kubectl delete`）
- 主应用 Deployment 也是同样道理：Secret 改了之后需要 restart 才能让 Pod 看到新值——开发者在 ArgoCD UI → Rollout → Restart 操作（或者用 [Reloader](https://github.com/stakater/Reloader)，但目前公司没部署）

---
