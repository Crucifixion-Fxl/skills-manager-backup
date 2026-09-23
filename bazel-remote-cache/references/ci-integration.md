# GitLab CI 集成

> 同步来源：ldeng/bazel_cache_test/.gitlab-ci.yml

## CI Job 配置示例

```yaml
default:
  image: <ci-build-image>
  tags:
    - <runner-tag>

stages:
  - build

bazel-build:
  stage: build
  script:
    - |
      if [ -z "${BUILDBUDDY_API_KEY:-}" ]; then
        echo "Set BUILDBUDDY_API_KEY in GitLab CI variables"
        exit 1
      fi
    - bazel version
    - echo "=== Build with remote cache ==="
    - bazel build --config=ci
        --remote_header="x-buildbuddy-api-key=${BUILDBUDDY_API_KEY}"
        --bes_header="x-buildbuddy-api-key=${BUILDBUDDY_API_KEY}"
        //<target>
    - echo "=== Build completed ==="
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
    - if: $CI_PIPELINE_SOURCE == "web"
```

## 前置条件

1. 在 GitLab 项目 Settings → CI/CD → Variables 配置 `BUILDBUDDY_API_KEY`
2. CI 镜像中需要有 Bazelisk（或 Bazel）
3. 项目根目录有 `.bazelversion` 和 `.bazelrc`

## 缓存预热

不同触发方式的 pipeline 可能产生不同的缓存 key：

- **标准 MR pipeline**（merge_request_event）：运行在 source branch 上，理论上和分支直接构建一致
- **Merged results pipeline**（需项目设置启用）：运行在临时合并提交上，action key 和分支构建不同

在我们当前 CI 配置下观察到 MR 触发和分支直接触发之间存在缓存不互通。建议对目标分支（如 main）单独触发一次构建预热缓存：
- 在 GitLab CI/CD → Run pipeline 页面手动触发
- 在 rules 中加 `$CI_PIPELINE_SOURCE == "web"` 支持手动触发

## 缓存命中判断

看 Bazel 构建日志末尾：

```
INFO: 10 processes: 6 remote cache hit, 4 internal.
```

- `remote cache hit` — 命中远程缓存
- `processwrapper-sandbox` / `linux-sandbox` — 本地编译（结果会上传到缓存）
- `internal` — Bazel 内部 action（不走缓存，正常现象）
