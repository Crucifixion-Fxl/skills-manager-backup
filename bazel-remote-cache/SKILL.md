---
name: bazel-remote-cache
description: 指导开发者在 Bazel 项目中接入和使用远程缓存（BuildBuddy）。覆盖 .bazelrc 配置、CI 集成、缓存命中条件、本地验证方法。当用户提到 Bazel 远程缓存、bazel remote cache、构建加速、缓存命中率、bazel --remote_cache、配置 remote cache、为什么没有命中缓存时使用。即使用户只说"加速构建"、"共享编译缓存"、"构建太慢了"也应触发。
---

# bazel-remote-cache

指导开发者在 Bazel 项目中接入和使用 BuildBuddy 远程缓存，加速 CI 和本地构建。

> **前提：** BuildBuddy 服务端已部署完成。本 skill 只覆盖客户端接入和使用，不涉及服务端部署（见 `buildbuddy-deploy` skill）。域名、CI 镜像等具体值根据部署环境而定，当前值见 [facts.md](references/facts.md)。

## Description

### 三种构建模式

| 模式 | 命令 | 读远程缓存 | 写远程缓存 | 使用场景 |
|------|------|-----------|-----------|----------|
| 不加 config | `bazel build //...` | 否 | 否 | 纯本地构建 |
| `--config=ci` | CI job 中使用 | 是 | 是 | CI 构建，上传缓存供其他构建复用 |
| `--config=dev` | `bazel build --config=dev //...` | 是 | 否 | 开发者本地复用 CI 缓存加速构建 |

### 缓存命中的关键因素

远程缓存是否命中取决于 Bazel 的 action key（由 inputs、command line、toolchain 等决定）。以下因素会影响命中：

1. **编译环境一致性** — 工具链、系统库、编译器版本不同会导致 action key 不同。建议 CI 和本地使用相同的 Docker 镜像来对齐环境，这是最简单的排除方式
2. **pipeline 触发方式** — 标准 MR pipeline（merge_request_event）运行在 source branch 上，理论上和分支构建一致。如果启用了 merged results pipeline，CI 会在临时合并提交上构建，action key 会不同。在我们当前 CI 配置下观察到 MR 触发和分支直接触发之间存在缓存不互通，建议对目标分支单独触发构建预热缓存
3. **缓存存活** — 当前使用 emptyDir，Pod 重启后缓存丢失。重启后需要重新预热

> 详细排查指南见 [cache-hit-guide.md](references/cache-hit-guide.md)

## Rules

1. **API Key 不入库**。`--remote_header` 和 `--bes_header` 通过 CI 变量注入，不写在 `.bazelrc` 中。泄露 Key 会让任何人通过 WAF 门禁访问 CI 入口。

2. **BES 也需要传 header**。`--remote_header` 只作用于 remote cache 请求，BES 请求需要 `--bes_header` 单独传。两个都不传会被 WAF 拦截。

3. **`--config=dev` 不上传缓存**。`--remote_upload_local_results=false`，开发者本地构建不会污染 CI 缓存。

4. **注意 pipeline 触发方式对缓存的影响**。标准 MR pipeline 运行在 source branch 上，理论上不影响 action key。但如果启用了 merged results pipeline，CI 构建的是临时合并提交，action key 会不同。在我们当前配置下观察到不同触发方式之间缓存不互通，建议对目标分支单独触发构建预热。

5. **缓存命中看 Bazel 原生日志**。构建结束后看 `INFO: N processes:` 行：
   - `remote cache hit` — 命中了远程缓存
   - `processwrapper-sandbox` / `linux-sandbox` — 本地编译（结果会上传，如果启用了 upload）

## 话题索引

| 需求 | 参考文档 |
|------|----------|
| .bazelrc 配置模板 | [bazelrc-templates.md](references/bazelrc-templates.md) |
| GitLab CI 集成 | [ci-integration.md](references/ci-integration.md) |
| 缓存命中排查 | [cache-hit-guide.md](references/cache-hit-guide.md) |
| 当前部署域名等具体值 | [facts.md](references/facts.md) |

## Examples

### ❌ Bad

```bazelrc
# API Key 写在 .bazelrc 里 — 泄露风险
build:ci --remote_header=x-buildbuddy-api-key=sk-xxxx
```

```yaml
# CI job 只传了 remote_header，没传 bes_header — BES 上报被 WAF 拦截
script:
  - bazel build --config=ci --remote_header="x-buildbuddy-api-key=${KEY}" //...
```

```bazelrc
# dev config 开启了上传 — 本地构建会污染 CI 缓存
build:dev --remote_upload_local_results=true
```

### ✅ Good

```bazelrc
# .bazelrc 只写连接配置，不写 Key
build:ci --remote_cache=grpcs://<ci-domain>:443
build:ci --remote_upload_local_results=true
# remote_header injected via CI job command line, not stored here.
```

```yaml
# CI job 同时传 remote_header 和 bes_header
script:
  - bazel build --config=ci
      --remote_header="x-buildbuddy-api-key=${BUILDBUDDY_API_KEY}"
      --bes_header="x-buildbuddy-api-key=${BUILDBUDDY_API_KEY}"
      //...
```

```bazelrc
# dev config 只读不写
build:dev --remote_cache=grpcs://<dev-domain>:443
build:dev --remote_upload_local_results=false
```
