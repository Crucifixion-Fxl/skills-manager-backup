# ArgoCD Application 模板

> 同步来源：DEV/argocd-apps/aws-<account-id>-<cluster>/buildbuddy-<cluster>.yaml

在 `argocd-apps` 仓库注册 BuildBuddy 应用。当前集群和镜像路径见 [facts.md](facts.md)。

## Application YAML

```yaml
# argocd-apps/aws-<account-id>-<cluster>/buildbuddy-<cluster>.yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: buildbuddy-<cluster>
  namespace: argo-cd
  labels:
    app: buildbuddy
    env: <cluster>
  annotations:
    notifications.argoproj.io/subscribe.on-deployed.feishu-ops: ""
    notifications.argoproj.io/subscribe.on-health-degraded.feishu-ops: ""
    notifications.argoproj.io/subscribe.on-sync-failed.feishu-ops: ""
    argocd-image-updater.argoproj.io/image-list: app=<harbor-host>/cicd/<cluster>/buildbuddy
    argocd-image-updater.argoproj.io/app.update-strategy: newest-build
    argocd-image-updater.argoproj.io/app.allow-tags: "regexp:^[a-f0-9]{7,40}$"
    argocd-image-updater.argoproj.io/write-back-method: argocd
    argocd-image-updater.argoproj.io/app.kustomize.image-name: buildbuddy
    argocd-image-updater.argoproj.io/app.platforms: linux/amd64
spec:
  project: default
  source:
    repoURL: https://gitlab.addx.ai/DEV/buildbuddy.git
    targetRevision: main
    path: k8s/overlays/<cluster>
  destination:
    server: https://kubernetes.default.svc
    namespace: buildbuddy
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
```

## 注意事项

1. ArgoCD 需要有 `DEV/buildbuddy` 仓库的读权限，首次部署需联系运维在 ArgoCD Settings → Repositories 中注册
2. `argocd-apps` 仓库的 main 是受保护分支，需要通过 MR 合入
3. Image Updater 会自动更新镜像 tag 为最新的 commit SHA
