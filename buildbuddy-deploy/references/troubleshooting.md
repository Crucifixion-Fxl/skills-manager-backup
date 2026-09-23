# 踩坑记录与故障排查

> 同步来源：sg-devops 集群实战部署经验

部署 BuildBuddy 过程中遇到的所有问题及解决方案，按严重程度排序。

## 目录

1. [Pod CrashLoopBackOff: no cache configured](#1-pod-crashloopbackoff-no-cache-configured)
2. [Pod CrashLoopBackOff: Server type unknown](#2-pod-crashloopbackoff-server-type-unknown)
3. [ArgoCD SyncFailed: CRD not found](#3-argocd-syncfailed-crd-not-found)
4. [CI Job 卡住: crane 镜像没有 shell](#4-ci-job-卡住-crane-镜像没有-shell)
5. [CI Job 失败: Harbor 401 Unauthorized](#5-ci-job-失败-harbor-401-unauthorized)
6. [CI Job 失败: 镜像版本不存在](#6-ci-job-失败-镜像版本不存在)
7. [Ingress 无法访问: DNS 未生效](#7-ingress-无法访问-dns-未生效)
8. [Ingress 无法访问: 安全组拦截](#8-ingress-无法访问-安全组拦截)
9. [ALB 返回 404](#9-alb-返回-404)

---

## 1. Pod CrashLoopBackOff: no cache configured

**现象：**
```
WRN No flags correspond to YAML input at 'auth.enable_anonymous_usage'.
WRN No flags correspond to YAML input at 'cache.s3'.
FTL rpc error: code = FailedPrecondition desc = no cache configured
```

**原因：** `buildbuddy-app-onprem`（社区版）不支持 `auth.enable_anonymous_usage` 和 `cache.s3`，这些是企业版功能。

**修复：** 使用 `cache.disk` 替代：

```yaml
cache:
  disk:
    root_directory: /data/cache
  max_size_bytes: 10000000000  # 10GB
```

移除 `auth.enable_anonymous_usage` 配置。

---

## 2. Pod CrashLoopBackOff: Server type unknown

**现象：**
```
WRN Readiness check returning error: Server type: '' unknown (did not match: "buildbuddy-server")
WRN Liveness check returning error: Server type: '' unknown (did not match: "buildbuddy-server")
```

Pod 启动成功但被 K8s 反复杀死。

**原因：** BuildBuddy v2.200+ 的健康检查端点需要 `server-type` 查询参数。

**修复：** 同时更新 K8s probe 和 ALB healthcheck annotation：

```yaml
# deployment.yaml
livenessProbe:
  httpGet:
    path: /healthz?server-type=buildbuddy-server
    port: http

# all ingress files
annotations:
  alb.ingress.kubernetes.io/healthcheck-path: /healthz?server-type=buildbuddy-server
```

---

## 3. ArgoCD SyncFailed: CRD not found

**现象：**
```
The Kubernetes API could not find wafv2.aws.m.upbound.io/WebACL for requested resource.
Make sure the "WebACL" CRD is installed on the destination cluster.
```

**原因：** WAF 等 Crossplane 资源需要 Crossplane provider CRD，应用集群没有安装。

**修复：** 所有 Crossplane 资源（WAF 等）必须放在 `DEV/crossplane-infra` 仓库，不能放在应用仓库。应用仓库只保留纯 K8s 原生资源。

---

## 4. CI Job 卡住: crane 镜像没有 shell

**现象：**
```
exec: "sh": executable file not found in $PATH
ERROR: Job failed: prepare environment: waiting for pod running: timed out
```

**原因：** `gcr.io/go-containerregistry/crane:latest` 是 distroless 镜像，没有 shell。

**修复：** 使用 `:debug` 标签：

```yaml
image:
  name: gcr.io/go-containerregistry/crane:debug
  entrypoint: [""]
```

---

## 5. CI Job 失败: Harbor 401 Unauthorized

**现象：**
```
Error: HEAD https://harbor-xxx/v2/.../manifests/...: unexpected status code 401 Unauthorized
```

**原因：** crane 默认读 `~/.docker/config.json`，Runner 的凭据在 `/kaniko/.docker-secret/config.json`。

**修复：**

```yaml
script:
  - mkdir -p $HOME/.docker && cp /kaniko/.docker-secret/config.json $HOME/.docker/config.json 2>/dev/null || true
```

---

## 6. CI Job 失败: 镜像版本不存在

**现象：**
```
Error: MANIFEST_UNKNOWN: Failed to fetch "v2.55.1"
```

**原因：** BuildBuddy 版本号持续递增（非语义化版本），需要先确认版本存在。

**修复：**

```bash
docker run --rm gcr.io/go-containerregistry/crane:debug ls gcr.io/flame-public/buildbuddy-app-onprem | sort -V | tail -20
```

---

## 7. Ingress 无法访问: DNS 未生效

**现象：** 浏览器报 `DNS_PROBE_FINISHED_NXDOMAIN`。

**排查：**

1. 用外部 DNS 测试：`nslookup buildbuddy-sg-ui.addx.live 8.8.8.8`
2. 如果外部能解析但本地不行 → 本地 DNS 缓存
3. Windows: `ipconfig /flushdns`
4. Chrome: `chrome://net-internals/#dns` → Clear host cache
5. 关闭所有浏览器窗口重新打开

---

## 8. Ingress 无法访问: 安全组拦截

**现象：** DNS 解析正常但连接超时。

**原因：** Kyverno 注入的安全组限制了访问来源。

**排查：**

1. 确认当前出口 IP：`curl -s ifconfig.me`
2. UI/Dev 入口用 `office` 安全组 — 需在办公网络访问
3. CI 入口用 `internal` 安全组 — 需在 VPC 内网访问
4. 如果不在对应网络 → 联系运维或切换网络

---

## 9. ALB 返回 404

**现象：** 直接访问 ALB 地址返回 HTTP 404。

**原因：** 正常。ALB 根据 Host header 路由，直接访问 ALB 地址的 Host 不匹配 Ingress 规则中的域名。

**验证：** ALB 返回 404（而非连接超时）说明 ALB 和 Pod 都正常。DNS 配好后用域名访问即可。
