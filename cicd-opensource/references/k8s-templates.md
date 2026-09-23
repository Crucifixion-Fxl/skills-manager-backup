# K8s 配置模板（开源项目）

开源项目的 K8s 配置模板。与 cicd-developer 的主要区别：镜像来自 Harbor `base/`、不用 Image Updater、版本固定。

## 目录结构

```
k8s/
├── base/
│   ├── kustomization.yaml
│   ├── rollout.yaml           # 无状态应用
│   ├── service.yaml
│   ├── ingress.yaml           # 可选
│   └── configmap.yaml         # 开源项目通常需要较多配置
└── overlays/
    ├── staging-us/
    │   ├── kustomization.yaml
    │   ├── configmap.yaml     # 环境特定配置
    │   └── external-secret.yaml
    └── prod-us/
```

## base/kustomization.yaml

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

resources:
  - rollout.yaml
  - service.yaml
  - ingress.yaml       # 如果有
  - configmap.yaml     # 如果有
```

## base/rollout.yaml（无状态应用）

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Rollout
metadata:
  name: my-app
spec:
  revisionHistoryLimit: 5
  replicas: 1
  selector:
    matchLabels:
      app: my-app
  strategy:
    canary:
      maxSurge: 1
      maxUnavailable: 0
  template:
    metadata:
      labels:
        app: my-app
    spec:
      imagePullSecrets:
        - name: harbor-registry-secret   # Kyverno 自动同步
      containers:
        - name: my-app
          image: my-app:placeholder       # overlay 中覆盖为 Harbor base/ 路径
          ports:
            - containerPort: 3000
          env:
            - name: DATABASE_URL
              valueFrom:
                secretKeyRef:
                  name: my-app-secret
                  key: DATABASE_URL
          envFrom:
            - configMapRef:
                name: my-app-config
          livenessProbe:
            httpGet:
              path: /health
              port: 3000
            initialDelaySeconds: 30       # 开源项目启动可能较慢
            periodSeconds: 20
            failureThreshold: 3
          readinessProbe:
            httpGet:
              path: /health
              port: 3000
            initialDelaySeconds: 10
            periodSeconds: 10
          resources:
            requests:
              cpu: 100m
              memory: 256Mi
            limits:
              cpu: 500m
              memory: 512Mi
```

## base/statefulset.yaml（有状态应用）

需要持久化存储的应用使用 StatefulSet。

```yaml
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: my-app
spec:
  serviceName: my-app
  replicas: 1
  selector:
    matchLabels:
      app: my-app
  template:
    metadata:
      labels:
        app: my-app
    spec:
      imagePullSecrets:
        - name: harbor-registry-secret
      containers:
        - name: my-app
          image: my-app:placeholder
          ports:
            - containerPort: 5672
          volumeMounts:
            - name: data
              mountPath: /var/lib/my-app/data
          resources:
            requests:
              cpu: 100m
              memory: 256Mi
            limits:
              cpu: 500m
              memory: 1Gi
  volumeClaimTemplates:
    - metadata:
        name: data
      spec:
        accessModes: ["ReadWriteOnce"]
        storageClassName: gp3              # EKS 默认 StorageClass
        resources:
          requests:
            storage: 10Gi
```

> **StorageClass 说明：**
> - EKS 集群：`gp3`（默认）
> - GKE 集群：`standard-rwo`
> - TKE 集群：`cbs`

## base/service.yaml

```yaml
apiVersion: v1
kind: Service
metadata:
  name: my-app
spec:
  selector:
    app: my-app
  ports:
    - port: 80
      targetPort: 3000
  type: ClusterIP
```

### Headless Service（StatefulSet 使用）

```yaml
apiVersion: v1
kind: Service
metadata:
  name: my-app
spec:
  clusterIP: None                # Headless
  selector:
    app: my-app
  ports:
    - port: 5672
      targetPort: 5672
```

## base/ingress.yaml

与 cicd-developer 完全一致，Kyverno 自动注入 ALB 配置。

### 办公室访问（大多数内部工具）

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: my-app
  labels:
    ingress.addx.io/sg: office
  annotations:
    alb.ingress.kubernetes.io/healthcheck-path: /health
spec:
  ingressClassName: alb
  rules:
    - host: my-app.addx.live
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: my-app
                port:
                  number: 80
```

### 公网 API

```yaml
metadata:
  name: my-app
  annotations:
    alb.ingress.kubernetes.io/healthcheck-path: /health
spec:
  ingressClassName: alb
  rules:
    - host: my-app.addx.live
```

### TKE 集群（腾讯云 CLB）

```yaml
metadata:
  annotations:
    kubernetes.io/ingress.class: qcloud
  labels:
    ingress.addx.io/sg: office
```

## base/configmap.yaml

开源项目通常有大量配置。对于配置文件型的应用，用 ConfigMap 挂载：

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: my-app-config
data:
  # 环境变量型配置
  LOG_LEVEL: "info"
  TZ: "UTC"
```

### 挂载配置文件

部分开源项目需要挂载完整配置文件（如 nginx.conf、prometheus.yml）：

```yaml
# configmap.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: my-app-file-config
data:
  app.conf: |
    # 配置文件内容
    server {
      listen 80;
      ...
    }
---
# rollout.yaml 中添加 volume
spec:
  template:
    spec:
      containers:
        - name: my-app
          volumeMounts:
            - name: config
              mountPath: /etc/my-app/app.conf
              subPath: app.conf
      volumes:
        - name: config
          configMap:
            name: my-app-file-config
```

## overlays/staging-us/kustomization.yaml

**关键区别：** 镜像路径指向 Harbor `base/`，且必须指定 `newTag`（固定版本）。

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

namespace: staging-my-app

resources:
  - ../../base
  - external-secret.yaml
  - configmap.yaml

images:
  - name: my-app                                          # 与 base rollout.yaml 中 image 名称匹配（冒号前）
    newName: harbor-39070-us-staging.addx.live/base/my-app # Harbor base/ 路径
    newTag: "1.0.0"                                       # 固定版本号

patches:
  - patch: |-
      - op: replace
        path: /spec/rules/0/host
        value: my-app-staging-us.addx.live
    target:
      kind: Ingress
      name: my-app
```

> **镜像路径格式：** `<HARBOR_REGISTRY>/base/<name>:<tag>`。其中 `<name>` 对应 images.yaml 中的 `name` 字段。

### 不同集群的 Harbor 地址

在 overlay 中使用具体的 Harbor 地址（不同于 cicd-developer 使用 `<HARBOR_REGISTRY>` 变量，因为这里没有 CI 注入）：

| 集群 | Harbor 地址 |
|------|-------------|
| us-eks-tech-service | `harbor-00249-us-tech.addx.live` |
| us-eks-prod | `harbor-30257-us-prod.addx.live` |
| us-staging | `harbor-39070-us-staging.addx.live` |
| eu-eks-tech-service | `harbor-01084-eu-tech.addx.live` |
| eu-eks-prod | `harbor-74031-eu-prod.addx.live` |
| eu-staging | `harbor-39070-eu-staging.addx.live` |
| cn-eks-tech-service | `harbor-58989-cn-tech.addx.live` |
| cn-prod | `harbor-74192-cn-prod.addx.live` |
| cn-eks-dev | `harbor-80144-cn-dev.addx.live` |
| cn-staging | `harbor-80144-cn-staging.addx.live` |
| sg-devops | `harbor-12571-sg-devops.addx.live` |
| us-prod-data | `harbor-76949-us-data.addx.live` |
| eu-prod-data | `harbor-76949-eu-data.addx.live` |
| cn-k8s (TKE) | `harbor-cn.addx.live` |
| us-tech-service-gke | `harbor-a4xt-us-tech.addx.live` |
| us-prod-gke | `harbor-a4xp-us-prod.addx.live` |

## overlays/staging-us/external-secret.yaml

与 cicd-developer 完全一致：

```yaml
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: my-app-secret
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: vault-builder-backend
    kind: ClusterSecretStore
  target:
    name: my-app-secret
    creationPolicy: Owner
  data:
    - secretKey: ADMIN_PASSWORD
      remoteRef:
        key: staging/app/my-app/config
        property: admin_password
```

> Builder 域新应用优先使用 `secret/{env}/app/<app>/<key>` 规范；ExternalSecret 的 `remoteRef.key` 通常不带 `secret/` 前缀。历史 Ops 路径保持现状，不强制迁移。

## overlays/staging-us/configmap.yaml

环境特定的配置覆盖：

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: my-app-config
data:
  LOG_LEVEL: "debug"           # staging 环境开启 debug
  DATABASE_HOST: "xxx.rds.amazonaws.com"
  TZ: "UTC"
```

## ArgoCD Application

在 `DEV/argocd-apps` 对应集群目录创建 YAML。

**关键区别：不包含 Image Updater 注解。**

```yaml
# 文件路径: aws-390709477306-us-staging/my-app-staging-us.yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: my-app-staging-us
  namespace: argo-cd
  labels:
    app: my-app
    env: staging-us
  annotations:
    # 飞书部署通知（必须添加）
    notifications.argoproj.io/subscribe.on-deployed.feishu-ops: ""
    notifications.argoproj.io/subscribe.on-health-degraded.feishu-ops: ""
    notifications.argoproj.io/subscribe.on-sync-failed.feishu-ops: ""
    # ⚠️ 注意：不配置 argocd-image-updater 注解！开源镜像使用固定版本
spec:
  project: default
  source:
    repoURL: https://gitlab.addx.ai/DEV/my-app-deploy.git
    targetRevision: main                    # 开源项目通常用 main 分支
    path: k8s/overlays/staging-us
  destination:
    server: https://kubernetes.default.svc
    namespace: staging-my-app
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
```

### ArgoCD Application 与 cicd-developer 的区别

| 维度 | cicd-developer | cicd-opensource |
|------|---------------|-----------------|
| Image Updater 注解 | 必须配置 | **不配置** |
| targetRevision | 环境分支（staging/pre/main） | 通常 `main`（配置仓库无环境分支） |
| repoURL | 应用源代码仓库 | 独立配置仓库（`*-deploy`） |

### argocd-apps 目录映射

| 目录 | 集群 |
|------|------|
| `aws-125710977284-sg-devops/` | sg-devops |
| `aws-302571458622-us-prod/` | us-eks-prod |
| `aws-740315635167-eu-prod/` | eu-eks-prod |
| `aws-002497567426-us-tech-service/` | us-eks-tech-service |
| `aws-010840394398-eu-tech-service/` | eu-eks-tech-service |
| `aws-769494896000-us-data/` | us-prod-data |
| `aws-769494896000-eu-data/` | eu-prod-data |
| `aws-390709477306-us-staging/` | us-staging |
| `aws-390709477306-eu-staging/` | eu-staging |
| `aws-589899215075-cn-tech-service/` | cn-eks-tech-service |
| `aws-741924744516-cn-prod/` | cn-prod |
| `aws-801447536674-cn-dev/` | cn-eks-dev |
| `aws-801447536674-cn-staging/` | cn-staging |
| `tencent-100014919455-cn-main/` | cn-k8s (TKE) |
| `gcp-a4xcloud-tech-service-us-us-tech-service/` | us-tech-service-gke |
| `gcp-a4xcloud-p-us-us-prod/` | us-prod-gke |

## HPA（水平自动扩缩容）

生产环境建议配置 HPA：

```yaml
# overlays/prod-us/hpa.yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: my-app
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment      # 或 argoproj.io/v1alpha1 Rollout
    name: my-app
  minReplicas: 2
  maxReplicas: 10
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
```

## PVC（持久化存储）

### 独立 PVC（Rollout 使用）

如果 Rollout 需要持久化（如 Grafana 的 plugin 目录），使用独立 PVC：

```yaml
# base/pvc.yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: my-app-data
spec:
  accessModes: ["ReadWriteOnce"]
  storageClassName: gp3
  resources:
    requests:
      storage: 10Gi
---
# rollout.yaml 中引用
spec:
  template:
    spec:
      containers:
        - volumeMounts:
            - name: data
              mountPath: /var/lib/my-app
      volumes:
        - name: data
          persistentVolumeClaim:
            claimName: my-app-data
```

> **注意：** Rollout 使用独立 PVC 时，`replicas` 只能为 1（ReadWriteOnce 不支持多 Pod 共享）。需要多副本 + 持久化时使用 StatefulSet。
