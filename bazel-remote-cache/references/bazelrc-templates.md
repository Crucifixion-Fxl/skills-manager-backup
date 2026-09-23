# .bazelrc 配置模板

> 同步来源：ldeng/bazel_cache_test/.bazelrc

域名根据 BuildBuddy 部署环境而定，当前值见 [facts.md](facts.md)。

## 推荐 .bazelrc 结构

```bazelrc
# common
build --remote_timeout=60

# CI: read/write cache
build:ci --remote_cache=grpcs://<ci-domain>:443
build:ci --remote_upload_local_results=true
# remote_header injected via CI job command line, not stored here.

# CI: BES for invocation visibility
build:ci --bes_results_url=https://<ui-domain>/invocation/
build:ci --bes_backend=grpcs://<ci-domain>:443

# Dev: read-only cache
build:dev --remote_cache=grpcs://<dev-domain>:443
build:dev --remote_upload_local_results=false
```

## .bazelversion

使用 Bazelisk 管理 Bazel 版本，在项目根目录放 `.bazelversion` 文件：

```
8.5.0
```

## .gitignore

```gitignore
/bazel-*
.bazelrc.user
MODULE.bazel.lock
*.log
```

## 使用方式

| 场景 | 命令 |
|------|------|
| 纯本地构建 | `bazel build //...` |
| CI 构建（读写缓存） | `bazel build --config=ci --remote_header="x-buildbuddy-api-key=${KEY}" --bes_header="x-buildbuddy-api-key=${KEY}" //...` |
| 本地只读缓存 | `bazel build --config=dev //...` |
