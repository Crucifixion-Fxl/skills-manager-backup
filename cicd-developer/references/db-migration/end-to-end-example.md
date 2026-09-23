## 6. 完整端到端示例

最小可运行的目录结构（从这个目录直接 `kustomize build` 应该不报错）：

```
my-app/
├── k8s/
│   ├── base/
│   │   ├── kustomization.yaml
│   │   ├── rollout.yaml
│   │   ├── service.yaml
│   │   └── serviceaccount.yaml
│   └── overlays/
│       └── staging-us/
│           ├── kustomization.yaml
│           ├── configmap.yaml
│           ├── external-secret.yaml      ← 模式 A 的单 ES
│           └── migration-job.yaml         ← PreSync hook
```

**`base/kustomization.yaml`：**

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

resources:
  - serviceaccount.yaml
  - rollout.yaml
  - service.yaml
```

**`base/rollout.yaml`（envFrom 部分）：**

```yaml
spec:
  template:
    spec:
      serviceAccountName: my-app
      imagePullSecrets:
        - name: harbor-registry-secret
      containers:
        - name: my-app
          image: my-app
          envFrom:
            - configMapRef:
                name: my-app-config
            - secretRef:
                name: my-app-secret    # ← 和 migration Job 同一个 Secret
```

**`overlays/staging-us/kustomization.yaml`：**

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

namespace: staging-us

resources:
  - ../../base
  - configmap.yaml
  - external-secret.yaml
  - migration-job.yaml

images:
  - name: my-app
    newName: harbor-39070-us-staging.addx.live/cicd/staging-us/my-app
```

**`overlays/staging-us/external-secret.yaml`（模式 A）：**

```yaml
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: my-app-secret
spec:
  refreshInterval: 1h
  secretStoreRef:
    kind: ClusterSecretStore
    name: vault-builder-backend # staging-us resolves to clusters.yaml -> vault_css=vault-builder-backend
  target:
    name: my-app-secret
    creationPolicy: Owner
  dataFrom:
    - extract:
        key: staging/app/my-app/config
    - extract:
        key: staging/rds/application/my-app/database
```

**`overlays/staging-us/migration-job.yaml`：** 见 [recipes/db-migration/presync-hook.yaml.tmpl](../../recipes/db-migration/presync-hook.yaml.tmpl)。

**`overlays/staging-us/configmap.yaml`：**

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: my-app-config
data:
  LOG_LEVEL: "info"
  HTTP_ADDR: ":8080"
```

---
