# 缓存命中排查指南

> 来源：sg-devops 集群实战验证经验

构建成功但没有 `remote cache hit` 时，按以下顺序排查。

## 1. 编译环境不一致

**现象：** CI 之间能命中，本地宿主机不命中。

**原因：** Bazel 的 action key 由 inputs、command line、toolchain 等决定。不同的操作系统、编译器版本、系统头文件会产生不同的 action key。Docker 镜像不同只是常见的差异来源，真正决定命中的是 action key 是否一致。

**验证：** 用 CI 同款 Docker 镜像在本地构建：

```bash
docker run --rm -it \
    -v "$PWD":/builds/ldeng/bazel_cache_test \
    -w /builds/ldeng/bazel_cache_test \
    <ci-build-image> \
    bash

# in container
bazel clean --expunge
bazel build --config=dev //...
```

如果容器内命中但宿主机不命中，就是环境差异。

**解决方案：**
- 本地开发时用 CI 同款 Docker 镜像构建
- 或使用 Bazel hermetic toolchain（统一工具链，不依赖系统）

## 2. Pipeline 触发方式差异

**现象：** MR 触发的 CI 写入了缓存，但下一次 MR 或本地构建不命中。

**背景：** GitLab 有两类 MR pipeline：
- **标准 MR pipeline**（merge_request_event）：运行在 source branch 上，理论上和分支直接构建的 action key 一致
- **Merged results pipeline**（需要项目设置中启用）：运行在临时合并提交上（`refs/merge-requests/:iid/merge`），action key 和分支直接构建不同

**我们的观察：** 在当前 CI 配置下，MR 触发的 pipeline 和对分支手动触发的 pipeline 之间确实存在缓存不互通。具体原因可能与 GitLab 项目配置（是否启用 merged results pipeline）或 Runner 环境差异有关，尚未完全定位。

**验证：** 对同一个分支直接触发 CI（在 GitLab CI/CD → Run pipeline 手动触发），然后再构建一次看是否命中。

**解决方案：**
- 对目标分支（main）定期手动触发 CI 预热缓存
- 在 CI rules 中加 `$CI_PIPELINE_SOURCE == "web"` 支持手动触发

## 3. 缓存被清空（Pod 重启）

**现象：** 之前能命中，突然全部不命中。

**原因：** 当前 BuildBuddy 使用 emptyDir 作为磁盘缓存，Pod 重启（包括 ArgoCD 重新部署、节点迁移）后缓存丢失。

**验证：** 在 CI 上连续触发两次构建，第一次全部本地编译，第二次看是否命中。

**解决方案：**
- 重启后重新预热（触发一次 CI 构建）
- 后续可改 PVC 持久化缓存

## 4. 网络不通（静默降级）

**现象：** 构建成功，没有报错，但全部是 `processwrapper-sandbox`。

**原因：** Bazel 默认远程缓存连接失败时会 fallback 到本地构建，不报错。

**验证：**

```bash
bazel build --config=dev --remote_local_fallback=false //...
```

加 `--remote_local_fallback=false` 后，如果连不上会直接报错。

**排查方向：**
- DNS 是否解析正常：`nslookup <domain>`
- 网络是否可达（dev 域名需要办公网络，ci 域名需要 VPC 内网）
- 安全组是否允许当前网络访问

## 5. 快速排查流程图

```
构建成功但没有 remote cache hit
  ↓
加 --remote_local_fallback=false 重试
  ├── 报错 → 网络/DNS 问题 → 见 #4
  └── 不报错但仍无 hit
        ↓
      CI 连续跑两次，第二次命中吗？
        ├── 命中 → 缓存被清空过 → 见 #3
        └── 不命中
              ↓
            用 CI 同款 Docker 镜像本地构建，命中吗？
              ├── 命中 → 编译环境差异 → 见 #1
              └── 不命中 → MR merge ref 问题 → 见 #2
```
