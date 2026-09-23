# CI/Docker 私有 Module 鉴权模板

> **前置条件**:要接入的服务所在 GitLab project **必须**被 allowlist 进 `CLOUD/a4x-logger-sdk` 的 "Settings → CI/CD → Job Token Permissions"。找 SDK owner(或 CLOUD 组 admin)加。本模板假定这一步已完成。

SDK 是私有 module,任何非 SDK 仓库本地的 build / test / image build 都要配鉴权才能 `go get` / `go mod download` 到 SDK。

## GitLab CI 端

### Job 级别环境变量(`.gitlab-ci.yml`)

任何跑 `go mod download` / `go build` / `go test` 的 job 都要加:

```yaml
lint-go:
  stage: lint
  image: $GO_IMAGE
  rules: *mr-push-rules
  variables:
    # Auth for private Go modules from gitlab.addx.ai/CLOUD/a4x-logger-sdk.
    # Needed on cold-cache runners where GOMODCACHE miss forces fresh fetch.
    # Requires target project (this one) in a4x-logger-sdk's Job Token allowlist.
    GIT_CONFIG_COUNT: "1"
    GIT_CONFIG_KEY_0: "url.https://gitlab-ci-token:${CI_JOB_TOKEN}@gitlab.addx.ai/.insteadOf"
    GIT_CONFIG_VALUE_0: "https://gitlab.addx.ai/"
  before_script:
    # golang:alpine 不自带 git,go toolchain fetch VCS 需要
    - apk add --no-cache git
  script:
    - cd server && go vet ./... && go build ./...
```

**关键点**:
- `GIT_CONFIG_COUNT/KEY_0/VALUE_0` 是 Go 1.18+ 认可的 git insteadOf 配置机制,通过环境变量而非 `.gitconfig` 文件,避免文件残留
- `CI_JOB_TOKEN` 由 GitLab Runner 自动注入,scope 只对当前 pipeline 生效,Job 结束即失效
- `apk add git` 仅 alpine 基础镜像需要,debian/ubuntu 镜像不需要

### 涉及到的典型 job 清单

| job | 需要 | 原因 |
|---|---|---|
| `lint-go` / `test-unit` | ✅ | `go vet` / `go build` 会触发 module fetch |
| `test-integration` / `test-l2` | ✅ | 运行 `go test` |
| `test-e2e` / `test-l3` | ✅ | 同上 |
| `build-kaniko`(image build) | ✅(方式不同,见下) | Dockerfile 里跑 `go mod download` |

## Kaniko image build 端

kaniko 和 CI job 环境变量不互通,要通过 `--build-arg` 把 `CI_JOB_TOKEN` 传进构建上下文。

### `.gitlab-ci.yml` kaniko 调用

```yaml
.build-kaniko:
  stage: build
  image:
    name: $KANIKO_IMAGE
    entrypoint: [""]
  variables:
    DOCKER_CONFIG: /kaniko/.docker
  script:
    - cp /kaniko/.docker-secret/config.json /kaniko/.docker/config.json
    # CI_JOB_TOKEN 通过 build-arg 传到 Dockerfile,go mod download 时用
    # kaniko 会忽略 Dockerfile 没 ARG 声明的 build-arg,hub/frontend 等非 Go 镜像
    # 安全继承这个模板,不会报错。
    - >-
      /kaniko/executor
      --context "${BUILD_CONTEXT}"
      --dockerfile "${BUILD_DOCKERFILE}"
      --build-arg "HARBOR_REGISTRY=${HARBOR_REGISTRY}"
      --build-arg "CI_JOB_TOKEN=${CI_JOB_TOKEN}"
      --destination "${IMAGE_NAME}:${CI_COMMIT_SHA}"
      --customPlatform=linux/amd64
      --cache=true
      --cache-repo="${IMAGE_NAME}/cache"
```

### Dockerfile

**核心安全要求**:token 不能泄露到 image layer 或 kaniko cache。**credential 配置 + use + cleanup 必须在同一个 RUN**。

```dockerfile
FROM --platform=linux/amd64 golang:1.25-alpine AS builder

# CI_JOB_TOKEN 由 kaniko --build-arg 传入。本地 docker build 时 ARG 为空,
# git-config 分支自动 skip,fetch 公共 module 不受影响。
ARG CI_JOB_TOKEN=""

WORKDIR /app
RUN apk add --no-cache git

COPY server/go.mod server/go.sum ./server/

# 关键:git config + go mod download + rm 必须在**同一个 RUN**。
# 原因:
# 1. 不能用 `set -x`(xtrace),否则展开后的 `gitlab-ci-token:<TOKEN>@...`
#    会被 kaniko 抓进 build log → 任何 CI log 查看者都能拿到 token
# 2. 不能跨 RUN:第一个 RUN 产生 ~/.gitconfig 文件,kaniko cache 会把这个
#    layer 推到 cache-repo,~/.gitconfig 里的明文 token 随之 leak
# 3. 必须在本层内 `rm -f ~/.gitconfig`,layer 文件系统 snapshot 结束时
#    ~/.gitconfig 不存在,cache 里自然没有
RUN set -eu; \
    if [ -n "$CI_JOB_TOKEN" ]; then \
      git config --global "url.https://gitlab-ci-token:${CI_JOB_TOKEN}@gitlab.addx.ai/.insteadOf" "https://gitlab.addx.ai/"; \
    fi; \
    cd server && go mod download; \
    rm -f /root/.gitconfig

COPY server/ ./server/
RUN cd <module-dir> && CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -o /app/<binary-name> .


# 运行时镜像 —— COPY --from=builder 只拉二进制,runtime stage 天然无 token
FROM --platform=linux/amd64 alpine:3.20
RUN apk --no-cache add ca-certificates
RUN addgroup -g 1001 -S appuser && adduser -S appuser -u 1001
COPY --from=builder /app/<binary> /usr/local/bin/<binary>
USER appuser
EXPOSE 8080
CMD ["<binary>", "-f", "/app/etc/<service>.yaml"]
```

### CI 侧静态检查(防回归)

强烈建议加一个 lint job 阻止未来 Dockerfile 改动不慎泄露 token:

```yaml
dockerfile-credential-check:
  stage: lint
  image: alpine:3.20
  rules: *mr-push-rules
  timeout: 2m
  script:
    - |
      set -eu
      FAILED=0
      for df in $(find . -maxdepth 3 -name Dockerfile -not -path './node_modules/*'); do
        # Check 1: runtime (last FROM) stage 不含 CI_JOB_TOKEN / git-credential 配置
        final_line=$(grep -n '^FROM ' "$df" | tail -1 | cut -d: -f1)
        runtime=$(tail -n +"$final_line" "$df")
        if echo "$runtime" | grep -qE 'CI_JOB_TOKEN|git config.*(--global|insteadOf)'; then
          echo "FAIL: $df runtime stage references credential"
          FAILED=1
        fi

        # Check 2: 含 git config insteadOf 的 RUN 必须同层清理
        normalized=$(sed -e ':a' -e '/\\$/{N' -e 's/\\\n[[:space:]]*/ /' -e 'ba' -e '}' "$df")
        echo "$normalized" | grep '^RUN ' | while IFS= read -r line; do
          if echo "$line" | grep -qE 'git config.*insteadOf'; then
            if ! echo "$line" | grep -qE 'rm[^;]*\.gitconfig|git config --unset'; then
              echo "FAIL: $df has credential RUN without same-layer cleanup"
              echo "::RUN_LEAK::"
            fi
            # Check 3: 不允许 -x(xtrace)
            if echo "$line" | grep -qE '(^|[[:space:];&|(])set -[a-z]*x[a-z]*([[:space:];&|)]|$)'; then
              echo "FAIL: $df credential RUN enables xtrace — token will be echoed"
              echo "::RUN_XTRACE::"
            fi
          fi
        done > /tmp/rc
        grep -qE '::RUN_(LEAK|XTRACE)::' /tmp/rc && FAILED=1
      done
      [ $FAILED -ne 0 ] && exit 1
      echo "OK: all Dockerfiles clean"
```

这个 check 会在每个 MR 上跑,任何未来的 Dockerfile 改动如果破坏同层清理 / `-x` 被重新启用,CI 会红。

## 本地开发鉴权

开发者本地首次 `go get gitlab.addx.ai/CLOUD/a4x-logger-sdk/go@v0.2.2` 时如果遇到 auth failure,配一次性 git credential:

```bash
# Option A: SSH(推荐,如果已配过 SSH key)
git config --global url."git@gitlab.addx.ai:".insteadOf "https://gitlab.addx.ai/"
export GOPRIVATE=gitlab.addx.ai

# Option B: Personal Access Token
git config --global url."https://<user>:<token>@gitlab.addx.ai/".insteadOf "https://gitlab.addx.ai/"
export GOPRIVATE=gitlab.addx.ai
```

`GOPRIVATE` 告诉 go toolchain 不走 module proxy,直接 VCS 拉(proxy 不支持私有 repo)。

## 本地 replace vs CI 鉴权的张力

**场景**:SDK 还在活跃迭代时,开发者想在**本地联调 SDK 源码**(改 SDK → 立刻在业务项目生效,不用每次 tag release)。典型做法是 go.mod 加:

```go
replace gitlab.addx.ai/CLOUD/a4x-logger-sdk/go => ../a4x-logger-sdk/go
```

**张力**:`replace` 本地好用,但 **CI 里路径不存在**(CI runner 没 `../a4x-logger-sdk/go`),CI 要走 `go mod download` 拉真实 module —— 两种需求冲突。

### 3 种解法(按推荐程度)

| 选项 | 做法 | 适用 |
|---|---|---|
| **A. `go.work`(推荐,Go 1.18+)** | 本地用 `go.work` 指向 SDK 源码(`.gitignore` 掉);go.mod 保持纯净。CI 默认不读 workspace(`GOWORK=off` 或无 go.work 文件)| 多数场景;本地 dev + CI 干净 |
| **B. CI 脚本剥 replace** | CI 的 `go mod download` 前加 `sed -i '/^replace.*a4x-logger-sdk/d' go.mod` | 不想引入 go.work;需要 CI 临时修改 go.mod |
| **C. Conditional build file** | 把 replace 放单独 `go.mod.dev` → 本地 symlink,CI 不 symlink | 罕见;只在特殊 toolchain 限制下用 |

### 选 A 的完整做法(多数项目用这个)

**本地**:

```bash
# 项目根目录,SDK 在同级 ../a4x-logger-sdk/
cd <project-root>
go work init
go work use .
go work use ../a4x-logger-sdk/go
echo "go.work" >> .gitignore        # 不要 commit,个人偏好
echo "go.work.sum" >> .gitignore
```

**CI**:无需改动 — 没有 `go.work` 文件 CI 自动走标准 `go.mod` + `GOPRIVATE` + `CI_JOB_TOKEN`。

**风险**:`.gitignore` 里 go.work 万一漏了会被 commit,CI 会读到 workspace 文件然后炸(路径不存在)。Dockerfile 构建时可以多一层保险:`COPY` 时显式排除 `go.work`,或 `GOWORK=off` 强关闭 workspace。

**什么时候跟团队对齐**:SDK tag 发布稳定(release 频率 < 每月 1 次)后,开发者基本不需要本地 replace/workspace,迁回纯 go.mod 流程就行。

## 为什么要这么严

本文档里这几条"credential 必须同层清理"、"credential RUN 禁止 `-x`"、"加静态 CI check"的规则,都是从真实接入踩坑事件来的。具体案例(包括发生在哪个 MR 的哪一轮 review、什么 commit 修复的)见 [case-studies/naturehood.md](case-studies/naturehood.md) 的 "Challenge 3 / 4 / 7"。

**教训抽象**:
- credential 不能跨 layer 残留(kaniko cache push 到 cache-repo,任何能读 cache 的人能拿 token)
- credential RUN 不能启 xtrace(`-x`)(build log 抓进 stdout,任何能读 CI log 的人能拿 token)
- 这两类问题**肉眼 review 会漏**,所以必须加静态 check 防回归 → 本文档上面的 `dockerfile-credential-check` job 就是这个防回归机制
