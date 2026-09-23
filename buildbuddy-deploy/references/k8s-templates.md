# K8s 配置模板

> 同步来源：DEV/buildbuddy/k8s/base/ 和 DEV/buildbuddy/k8s/overlays/sg-devops/

BuildBuddy 应用的 Kubernetes 配置模板。当前部署的具体值（版本、域名、镜像路径等）见 [facts.md](facts.md)。

## 目录结构

```
buildbuddy/
├── .gitlab-ci.yml
├── k8s/
│   ├── base/
│   │   ├── kustomization.yaml
│   │   ├── deployment.yaml
│   │   ├── service.yaml
│   │   ├── configmap.yaml
│   │   ├── ingress-ui.yaml         # UI 查看（office, HTTP）
│   │   ├── ingress-ci.yaml         # CI 写缓存（internal, gRPC）
│   │   └── ingress-dev.yaml        # 开发者只读（office, gRPC）
│   └── overlays/sg-devops/
│       ├── kustomization.yaml
│       └── serviceaccount.yaml
```

## base/configmap.yaml

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: buildbuddy-config
  labels:
    app: buildbuddy
data:
  config.yaml: |
    app:
      build_buddy_url: "https://buildbuddy-sg-ui.addx.live"
      events_api_url: "grpcs://buildbuddy-sg-ci.addx.live:443"
      cache_api_url: "grpcs://buildbuddy-sg-ci.addx.live:443"

    # onprem: only disk cache supported
    cache:
      disk:
        root_directory: /data/cache
      max_size_bytes: 10000000000  # 10GB

    storage:
      enable_chunked_event_logs: true
      chunk_file_size_bytes: 3000000
```

> **注意：** 不要使用 `auth.enable_anonymous_usage` 和 `cache.s3`，这些是企业版功能，onprem 版会崩溃。

## base/deployment.yaml

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: buildbuddy
  labels:
    app: buildbuddy
spec:
  replicas: 1
  selector:
    matchLabels:
      app: buildbuddy
  template:
    metadata:
      labels:
        app: buildbuddy
    spec:
      containers:
        - name: buildbuddy
          image: buildbuddy
          args:
            - "--config_file=/config.yaml"
          ports:
            - name: http
              containerPort: 8080
            - name: grpc
              containerPort: 1985
          env:
            - name: AWS_REGION
              value: "ap-southeast-1"
          volumeMounts:
            - name: config
              mountPath: /config.yaml
              subPath: config.yaml
            - name: data
              mountPath: /data
          resources:
            requests:
              cpu: "500m"
              memory: "1Gi"
            limits:
              cpu: "2000m"
              memory: "4Gi"
          # v2.200+ requires ?server-type=buildbuddy-server
          livenessProbe:
            httpGet:
              path: /healthz?server-type=buildbuddy-server
              port: http
            initialDelaySeconds: 30
            periodSeconds: 10
          readinessProbe:
            httpGet:
              path: /readyz?server-type=buildbuddy-server
              port: http
            initialDelaySeconds: 5
            periodSeconds: 5
      volumes:
        - name: config
          configMap:
            name: buildbuddy-config
        - name: data
          emptyDir: {}  # Pod restart clears cache; consider PVC for persistence
```

## base/service.yaml

```yaml
apiVersion: v1
kind: Service
metadata:
  name: buildbuddy
  labels:
    app: buildbuddy
spec:
  type: ClusterIP
  selector:
    app: buildbuddy
  ports:
    - name: http
      port: 8080
      targetPort: 8080
    - name: grpc
      port: 1985
      targetPort: 1985
```

## Ingress（三域名模型）

按角色命名，不按网络形态命名。

### base/ingress-ui.yaml — UI 查看

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: buildbuddy-ui
  labels:
    ingress.addx.io/sg: office
  annotations:
    alb.ingress.kubernetes.io/healthcheck-path: /healthz?server-type=buildbuddy-server
    alb.ingress.kubernetes.io/backend-protocol-version: HTTP1
spec:
  ingressClassName: alb
  rules:
    - host: buildbuddy-sg-ui.addx.live
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: buildbuddy
                port:
                  number: 8080
```

### base/ingress-ci.yaml — CI 写缓存

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: buildbuddy-ci
  labels:
    ingress.addx.io/sg: internal
  annotations:
    alb.ingress.kubernetes.io/scheme: internal
    alb.ingress.kubernetes.io/healthcheck-path: /healthz?server-type=buildbuddy-server
    alb.ingress.kubernetes.io/healthcheck-protocol: HTTP
    alb.ingress.kubernetes.io/backend-protocol-version: GRPC
    # header gate WAF patched in overlay
spec:
  ingressClassName: alb
  rules:
    - host: buildbuddy-sg-ci.addx.live
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: buildbuddy
                port:
                  number: 1985
```

### base/ingress-dev.yaml — 开发者只读

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: buildbuddy-dev
  labels:
    ingress.addx.io/sg: office
  annotations:
    alb.ingress.kubernetes.io/healthcheck-path: /healthz?server-type=buildbuddy-server
    alb.ingress.kubernetes.io/healthcheck-protocol: HTTP
    alb.ingress.kubernetes.io/backend-protocol-version: GRPC
    # readonly WAF patched in overlay
spec:
  ingressClassName: alb
  rules:
    - host: buildbuddy-sg-dev.addx.live
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: buildbuddy
                port:
                  number: 1985
```

## overlays/sg-devops/kustomization.yaml

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

namespace: buildbuddy

resources:
  - ../../base
  - serviceaccount.yaml

images:
  - name: buildbuddy
    newName: harbor-12571-sg-devops.addx.live/cicd/sg-devops/buildbuddy
    newTag: "<version>"  # sync with .gitlab-ci.yml BUILDBUDDY_VERSION

patches:
  # Host overrides
  - patch: |-
      - op: replace
        path: /spec/rules/0/host
        value: buildbuddy-sg-ui.addx.live
    target:
      kind: Ingress
      name: buildbuddy-ui
  - patch: |-
      - op: replace
        path: /spec/rules/0/host
        value: buildbuddy-sg-ci.addx.live
    target:
      kind: Ingress
      name: buildbuddy-ci
  - patch: |-
      - op: replace
        path: /spec/rules/0/host
        value: buildbuddy-sg-dev.addx.live
    target:
      kind: Ingress
      name: buildbuddy-dev
  # WAF header gate on CI ingress
  - patch: |-
      - op: add
        path: /metadata/annotations/alb.ingress.kubernetes.io~1wafv2-acl-arn
        value: "<ci-gate-waf-arn>"
    target:
      kind: Ingress
      name: buildbuddy-ci
  # WAF readonly on Dev ingress
  - patch: |-
      - op: add
        path: /metadata/annotations/alb.ingress.kubernetes.io~1wafv2-acl-arn
        value: "<readonly-waf-arn>"
    target:
      kind: Ingress
      name: buildbuddy-dev
  # IRSA ServiceAccount binding
  - patch: |-
      - op: add
        path: /spec/template/spec/serviceAccountName
        value: buildbuddy
    target:
      kind: Deployment
      name: buildbuddy
  # amd64 node scheduling
  - patch: |-
      - op: add
        path: /spec/template/spec/nodeSelector
        value:
          kubernetes.io/arch: amd64
    target:
      kind: Deployment
      name: buildbuddy
  # Harbor imagePullSecrets
  - patch: |-
      - op: add
        path: /spec/template/spec/imagePullSecrets
        value:
          - name: harbor-registry-secret
    target:
      kind: Deployment
      name: buildbuddy
```

## overlays/sg-devops/serviceaccount.yaml

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: buildbuddy
  namespace: buildbuddy
  annotations:
    eks.amazonaws.com/role-arn: arn:aws:iam::<account-id>:role/crossplane-app-buildbuddy-irsa
```
