# 排查部署问题

## 镜像拉取失败

### ImagePullBackOff: base-images 镜像未同步

**现象：** Pod 状态 `ImagePullBackOff`，Events 显示 `Failed to pull image`

**原因：** base-images Pipeline 尚未完成同步，或镜像名/tag 不匹配

**排查：**
1. 确认 base-images MR 已合并：`git log --oneline main` 查看
2. 确认 Pipeline 成功：GitLab → DEV/base-images → CI/CD → Pipelines
3. 在目标集群 Harbor UI 搜索镜像：`base/<name>:<tag>`
4. 确认 overlay kustomization.yaml 中 `newName`/`newTag` 与 Harbor 中一致

**修复：**
- Pipeline 未触发：手动运行 Pipeline（images.yaml 变更后才会触发）
- Pipeline 失败：查看失败 Job 日志，常见原因是源镜像不存在或网络超时
- 镜像名不匹配：修正 images.yaml 中的 `name`/`tag` 或 overlay 中的路径

### ImagePullBackOff: Harbor 地址错误

**现象：** `Failed to pull image "harbor-xxx/base/my-app:1.0.0": unauthorized`

**原因：** overlay 中写了错误的 Harbor 地址（不是目标集群的 Harbor）

**修复：** 对照 k8s-templates.md 中的 Harbor 地址映射表修正。

### CN 集群拉取 Docker Hub 镜像超时

**现象：** CN 集群 Pod 拉取 `docker.io/xxx` 超时

**原因：** CN 集群无法直接访问 Docker Hub，必须使用 Harbor base/ 路径

**修复：** 确保 overlay kustomization.yaml 的 `newName` 使用 CN 集群 Harbor 地址：`harbor-58989-cn-tech.addx.live/base/<name>`

## ArgoCD 问题

### Application OutOfSync 但不自动同步

**原因：** syncPolicy 未配置 `automated`

**修复：** 确认 ArgoCD Application YAML 包含：
```yaml
syncPolicy:
  automated:
    prune: true
    selfHeal: true
```

### Application 报 ComparisonError

**原因：** ArgoCD 无法访问 Git 仓库

**排查：**
1. 确认 `Argocd-deploy` 用户已添加为 Reporter
2. 确认 repoURL 正确（`https://gitlab.addx.ai/DEV/<repo>.git`）
3. 确认 path 指向正确的 overlay 目录

### 配置仓库分支问题

**现象：** ArgoCD 报 `targetRevision not found`

**原因：** 开源项目配置仓库通常用 `main` 分支，不像业务应用有 staging/pre/main 分支

**修复：** 确认 ArgoCD Application 的 `targetRevision` 与实际分支名一致。

## Pod 运行问题

### CrashLoopBackOff: 配置缺失

**现象：** Pod 启动后立即退出，日志显示配置项缺失

**排查：**
1. `kubectl logs <pod-name> -n <namespace>` 查看启动日志
2. 对照开源项目文档确认必要的环境变量/配置文件
3. 检查 ConfigMap 和 ExternalSecret 是否包含所有必要配置

**常见遗漏：**
- 数据库连接信息（host/port/user/password/dbname）
- 应用 URL（很多应用需要知道自己的访问地址）
- 许可证/密钥（部分应用需要）

### CrashLoopBackOff: 权限问题

**现象：** 日志显示 `Permission denied` 或 `mkdir: cannot create directory`

**原因：** 开源镜像以非 root 用户运行，但挂载的 PVC 权限不对

**修复：** 在 rollout/statefulset 中添加 securityContext：
```yaml
spec:
  template:
    spec:
      securityContext:
        fsGroup: 1000              # 镜像中用户的 GID
      containers:
        - name: my-app
          securityContext:
            runAsUser: 1000        # 镜像中用户的 UID
```

> 不同镜像的 UID/GID 不同，查看 Dockerfile 或运行 `docker inspect <image>` 确认。

### OOMKilled: 内存不足

**现象：** Pod 被 OOM Kill，状态 `OOMKilled`

**原因：** Java/Elasticsearch 等应用默认使用大量内存

**修复：** 增大 resources.limits.memory，并配置应用内存参数：
- Java: `-Xmx` 设为 container memory 的 50-75%
- Elasticsearch: `ES_JAVA_OPTS` 设为 container memory 的 50%
- Node.js: `--max-old-space-size`

## ExternalSecret 问题

### SecretSyncedError: Vault 路径不存在

**现象：** ExternalSecret 状态 `SecretSyncedError`

**原因：** Vault 中尚未创建对应路径

**修复：** 联系运维或应用 owner 创建 Vault 路径并写入值。Builder 域新应用使用 `secret/{env}/app/<app>/<key>`；历史 Ops 路径保持现状，不要为了排障强行迁移。

## PVC 问题

### Pending: StorageClass 不存在

**现象：** PVC 状态 `Pending`，Events 显示 `storageclass "xxx" not found`

**修复：** 使用正确的 StorageClass：
- EKS: `gp3`
- GKE: `standard-rwo`
- TKE: `cbs`

### StatefulSet 缩容后 PVC 残留

**现象：** 缩容后旧 PVC 不会自动删除

**说明：** 这是 K8s 设计行为，防止数据丢失。如确认不需要，手动删除：
```bash
kubectl delete pvc data-my-app-1 -n <namespace>
```

## 版本升级问题

### 升级后 Pod 不更新

**排查：**
1. 确认 images.yaml 新版本已合并并同步完成
2. 确认 overlay kustomization.yaml 的 `newTag` 已更新
3. 确认 ArgoCD Application 状态为 `Synced`
4. 如果 ArgoCD 显示 `OutOfSync`，手动 Sync

### 升级后应用不兼容

**预防：** 升级前阅读开源项目 Release Notes，关注：
- Breaking Changes
- 数据库 Migration 要求
- 配置项变更
- 依赖版本变更

**回滚：** 修改 overlay kustomization.yaml 的 `newTag` 回旧版本，ArgoCD 自动回滚。
