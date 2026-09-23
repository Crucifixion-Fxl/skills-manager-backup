# CI/Docker pip 私服鉴权模板(过渡期 GIT_CONFIG insteadOf)

> **前置条件**:业务服务所在 GitLab project 必须被加入 `CLOUD/a4x-logger-sdk` 的
> "Settings → CI/CD → Job Token Permissions" allowlist。
> allowlist 已覆盖 `services` / `applications` / `CLOUD` 三个 group,这三个 group 下的
> project 无需额外申请。跨 group 的项目找 SDK owner 加 allowlist。

> **当前安装方式**:公司 Nexus 是 2.14.x,不支持 PyPI 仓库(PyPI format 是 Nexus 3.0+ 功能)。
> SRE 已确认将升级到 3.x。**升级完成前 Python SDK 通过 GitLab git+https 直接安装**,走
> CI Job Token + `GIT_CONFIG_*` insteadOf 模式——跟 Go 业务接入同款,业务团队套现有模板即可。
> Nexus 3.x 上线后按 §5 末尾的长期态切换。

SDK 已发布 tag `python/v1.0.0`(tag 命名沿用 monorepo 多语言前缀风格,跟 `go/v0.2.2` 同款)。

## §1 pip install 模式(GIT_CONFIG insteadOf + CI_JOB_TOKEN)

这是基于 SDK commit `ac4cbde`(docs/architecture/python/usage.md 附录 C)验证的标准接入方式。

### requirements.txt(干净 URL,不带 token)

```
a4x-logger @ git+https://gitlab.addx.ai/CLOUD/a4x-logger-sdk.git@python/v1.0.0#subdirectory=python
```

`requirements.txt` 只写 https URL,不内嵌任何 token。鉴权由 CI 侧 `GIT_CONFIG_*` 注入(见 §2)。

### pyproject.toml 备选

如果业务项目用 `pyproject.toml` 管理依赖(setuptools / hatch / poetry),在
`[project].dependencies` 里等价写法:

```toml
[project]
dependencies = [
  "a4x-logger @ git+https://gitlab.addx.ai/CLOUD/a4x-logger-sdk.git@python/v1.0.0#subdirectory=python",
]
```

pip 在安装时读取同样的 URL,鉴权机制完全一致。

### 手动安装(CI script 里直接跑 pip install)

如果不用 requirements.txt,也可以直接在 `.gitlab-ci.yml` script 里一步完成:

```bash
git config --global \
  "url.https://gitlab-ci-token:${CI_JOB_TOKEN}@gitlab.addx.ai/.insteadOf" \
  "https://gitlab.addx.ai/"

pip install \
  "git+https://gitlab.addx.ai/CLOUD/a4x-logger-sdk.git@python/v1.0.0#subdirectory=python"
```

**`GIT_CONFIG_*` 环境变量方式(推荐)**:GitLab Runner 支持通过环境变量注入 git 配置,
等价于上面的 `git config --global`,且不产生 `~/.gitconfig` 文件残留:

```yaml
variables:
  GIT_CONFIG_COUNT: "1"
  GIT_CONFIG_KEY_0: "url.https://gitlab-ci-token:${CI_JOB_TOKEN}@gitlab.addx.ai/.insteadOf"
  GIT_CONFIG_VALUE_0: "https://gitlab.addx.ai/"
```

GitLab Runner 启动 job 时自动注入 `CI_JOB_TOKEN`,**业务方无需维护任何 secret**。

## §2 `.gitlab-ci.yml` 完整模板

### Python image 版本选择

SDK 自身 CI(`ci/python.yml`)使用 `python:3.9-slim`(floor 版本,
见 `docs/architecture/version-matrix.md §4.1`)。
业务方可选 3.9–3.12 任意小版本,**必须钉死小版本**(不用浮动 tag):

| 推荐 | 原因 |
|---|---|
| `python:3.11-slim` | 新项目默认选择,LTS 周期覆盖 2027 |
| `python:3.9-slim` | 与 SDK floor 版本保持一致;老项目无迁移成本 |
| 不推荐 `python:3.11`(无 slim 后缀) | 镜像体积是 slim 的 4 倍,无额外收益 |
| 不推荐 `python:latest` | 浮动 tag 导致 build 不可复现 |

### 完整 `.gitlab-ci.yml`

```yaml
stages:
  - test
  - build

# --- 全局变量 ---
variables:
  # pip wheel 缓存目录,GitLab cache 才能命中
  PIP_CACHE_DIR: "$CI_PROJECT_DIR/.pip-cache"
  # GIT_CONFIG_* 三行等价于 git config --global url."...".insteadOf "..."
  # CI_JOB_TOKEN 由 GitLab Runner 自动注入,无需在 Variables UI 维护
  GIT_CONFIG_COUNT: "1"
  GIT_CONFIG_KEY_0: "url.https://gitlab-ci-token:${CI_JOB_TOKEN}@gitlab.addx.ai/.insteadOf"
  GIT_CONFIG_VALUE_0: "https://gitlab.addx.ai/"

# --- 可复用锚点 ---
.python-base: &python-base
  # 钉死小版本,可复现 build
  image: python:3.11-slim
  cache:
    key: "${CI_COMMIT_REF_SLUG}-pip"
    fallback_keys:
      - "${CI_DEFAULT_BRANCH}-pip"
    paths:
      - .pip-cache/

# --- test job ---
test:
  stage: test
  <<: *python-base
  rules:
    - if: $CI_MERGE_REQUEST_ID
    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH
  before_script:
    # python:3.11-slim 不自带 git,pip 从 gitlab git+https 安装依赖需要 git
    - apt-get update -qq && apt-get install -y --no-install-recommends git
    - pip install --upgrade pip
  script:
    - pip install -r requirements.txt
    - pip install pytest ruff mypy
    - ruff check .
    - mypy src/
    - pytest -x tests/

# --- build image job(见 §3 Dockerfile) ---
build-image:
  stage: build
  image:
    name: gcr.io/kaniko-project/executor:v1.23.2-debug
    entrypoint: [""]
  rules:
    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH
  script:
    - cp /kaniko/.docker-secret/config.json /kaniko/.docker/config.json
    # CI_JOB_TOKEN 通过 --build-arg 传入 Dockerfile builder stage
    # kaniko 会忽略 Dockerfile 没 ARG 声明的 build-arg,安全透传
    - >-
      /kaniko/executor
      --context "${CI_PROJECT_DIR}"
      --dockerfile "Dockerfile"
      --build-arg "CI_JOB_TOKEN=${CI_JOB_TOKEN}"
      --destination "${CI_REGISTRY_IMAGE}:${CI_COMMIT_SHA}"
      --customPlatform=linux/amd64
      --cache=true
      --cache-repo="${CI_REGISTRY_IMAGE}/cache"
```

**关键决策说明**:

| 点 | 做法 | 原因 |
|---|---|---|
| Python image 钉小版本 | `python:3.11-slim` | 可复现 build;浮动 tag 不同时间拉到不同镜像 |
| pip cache key 带分支 | `${CI_COMMIT_REF_SLUG}-pip` | 不同分支依赖可能不同,避免 stale wheel 跨分支污染 |
| `GIT_CONFIG_*` 放全局 variables | 全 job 共享 | 多 job 不用重复写;Runner 自动注入 CI_JOB_TOKEN |
| `apt-get install git` | before_script | slim 镜像不自带 git;pip 从 git+https URL 安装需要 git |

## §3 Dockerfile 双 stage(builder 装 pip,runtime 不带 token)

**核心安全原则**:
- `ARG CI_JOB_TOKEN` 和 `git config insteadOf` 只能出现在 builder stage
- `git config` + `pip install` + `rm -f ~/.gitconfig` **必须在同一个 RUN 指令内完成**
- kaniko / BuildKit 的 layer snapshot 在 RUN 结束时取 diff;同层清理后 `~/.gitconfig` 不存在,cache layer 里无残留
- 禁止在 credential RUN 里启用 `set -x`(xtrace):xtrace 会把展开后的 token 写进 build log

```dockerfile
# ==========================================================
# Stage 1: builder — 装 git + pip,安装 SDK 及依赖
# ==========================================================
FROM python:3.11-slim AS builder

# CI_JOB_TOKEN 由 kaniko --build-arg 传入
# 本地 docker build 时 ARG 为空,git-config 分支自动 skip
ARG CI_JOB_TOKEN=""

WORKDIR /build

# python:3.11-slim 不自带 git,pip 从 git+https 安装需要 git
RUN apt-get update -qq \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# 关键:git config + pip install + rm 必须在同一个 RUN。
# 原因:
#   1. 不能 set -x(xtrace) — 展开后 CI_JOB_TOKEN 明文进 build log
#   2. 不能跨 RUN — 第一个 RUN 产生 ~/.gitconfig,kaniko 会把这个 layer
#      推到 cache-repo,含明文 token 的 ~/.gitconfig 随之泄漏
#   3. 必须同层 rm -f ~/.gitconfig — layer snapshot 结束时文件不存在,
#      cache 里无残留
RUN set -eu; \
    if [ -n "$CI_JOB_TOKEN" ]; then \
      git config --global \
        "url.https://gitlab-ci-token:${CI_JOB_TOKEN}@gitlab.addx.ai/.insteadOf" \
        "https://gitlab.addx.ai/"; \
    fi; \
    pip install --prefix=/install -r requirements.txt; \
    rm -f /root/.gitconfig

# ==========================================================
# Stage 2: runtime — 只含 Python + wheel,无 git,无 token
# ==========================================================
FROM python:3.11-slim

# 从 builder 只拷贝装好的 wheel,不带任何 git 配置或 token 痕迹
COPY --from=builder /install /usr/local

# 创建非 root 用户
RUN groupadd -g 1001 appuser && useradd -u 1001 -g appuser appuser

COPY app/ /app/
WORKDIR /app

USER appuser
EXPOSE 8080
CMD ["python", "main.py"]
```

**`pip install --prefix=/install` 说明**:

将所有 wheel 安装到独立目录 `/install`,runtime stage 只需 `COPY --from=builder /install /usr/local`
整体拷贝,比 `--target` 更符合 Python import 路径约定(无需额外设置 `PYTHONPATH`)。

### CI 侧静态检查(防 token 泄漏回归)

建议加一个 lint job,在每个 MR 上阻止 Dockerfile 改动不慎将 token 带入 runtime stage:

```yaml
dockerfile-credential-check:
  stage: test
  image: python:3.11-slim
  rules:
    - if: $CI_MERGE_REQUEST_ID
  timeout: 2m
  script:
    - |
      set -eu
      FAILED=0
      for df in $(find . -maxdepth 3 -name Dockerfile -not -path './node_modules/*'); do
        # Check 1: runtime(最后一个 FROM)stage 不含 CI_JOB_TOKEN / git insteadOf
        final_line=$(grep -n '^FROM ' "$df" | tail -1 | cut -d: -f1)
        runtime=$(tail -n +"$final_line" "$df")
        if echo "$runtime" | grep -qE 'CI_JOB_TOKEN|git config.*(--global|insteadOf)'; then
          echo "FAIL: $df runtime stage references credential"
          FAILED=1
        fi

        # Check 2: 含 git config insteadOf 的 RUN 必须同层清理 ~/.gitconfig
        normalized=$(sed -e ':a' -e '/\\$/{N;s/\\\n[[:space:]]*/ /;ba}' "$df")
        while IFS= read -r line; do
          if echo "$line" | grep -qE 'git config.*insteadOf'; then
            if ! echo "$line" | grep -qE 'rm[^;]*\.gitconfig'; then
              echo "FAIL: $df has credential RUN without same-layer cleanup"
              FAILED=1
            fi
            if echo "$line" | grep -qE 'set -[a-z]*x'; then
              echo "FAIL: $df credential RUN enables xtrace — token will be echoed to log"
              FAILED=1
            fi
          fi
        done <<< "$(echo "$normalized" | grep '^RUN ')"
      done
      [ $FAILED -ne 0 ] && exit 1
      echo "OK: all Dockerfiles clean"
```

## §4 本地开发鉴权

### 方式 A:SSH(推荐)

开发者本机 SSH key 已加到 GitLab account 后(公司 Python dev 通常一次性配过),
直接用 SSH URL 安装:

```bash
pip install 'git+ssh://git@gitlab.addx.ai/CLOUD/a4x-logger-sdk.git@python/v1.0.0#subdirectory=python'
```

如果想复用与 CI 相同的 `requirements.txt`(干净 https URL),配一次性 git insteadOf
把 https 重定向到 SSH:

```bash
git config --global url."git@gitlab.addx.ai:".insteadOf "https://gitlab.addx.ai/"
```

配完后 `pip install -r requirements.txt` 在本机和 CI 走同一份文件,**本地 / CI 体验一致**。

### 方式 B:Personal Access Token + `~/.netrc`

不用 SSH 时,用 GitLab Personal Access Token 配 `~/.netrc`(pip 在 git clone 时会读取):

```
machine gitlab.addx.ai
login <your-gitlab-username>
password <your-personal-access-token>
```

`~/.netrc` 权限设置:

```bash
chmod 600 ~/.netrc
```

Personal Access Token 需要 `read_repository` scope。

### 方式 C:Personal Access Token + `~/.gitconfig` insteadOf

```bash
git config --global \
  "url.https://<username>:<token>@gitlab.addx.ai/.insteadOf" \
  "https://gitlab.addx.ai/"
```

效果与 `~/.netrc` 等价,选择哪种按个人习惯。

### 本地 vs CI 凭据对照

| 场景 | 凭据来源 | 写在哪里 |
|---|---|---|
| 本地开发(SSH) | GitLab SSH key | `~/.ssh/` |
| 本地开发(https) | Personal Access Token | `~/.netrc` 或 `~/.gitconfig` |
| GitLab CI | CI_JOB_TOKEN(自动注入) | `GIT_CONFIG_*` 环境变量 |
| Docker build(CI) | CI_JOB_TOKEN via `--build-arg` | Dockerfile builder stage RUN 内(同层清理) |

## §5 包源稳定性(tag 锁定 + reproducible build)

### tag 锁定

`python/v1.0.0` 是已发布的 immutable tag,SDK 不会移动或删除已发布 tag。
`requirements.txt` 里引用这个 tag 后,pip 每次安装拉到的 wheel 字节完全一致。

### pip wheel cache

GitLab CI cache 配置(§2 已包含)保证:
- 同分支多次 pipeline 共享 wheel cache,避免重复从 GitLab 克隆 SDK
- 分支间 fallback 到 default branch cache,cold start 也不慢

### reproducible build:pip-compile 锁全部传递依赖

`requirements.txt` 里的 git+https URL 是直接依赖锁定。
如果业务项目需要锁定所有传递依赖(完全可复现),用 `pip-tools`:

```bash
# 安装 pip-tools
pip install pip-tools

# requirements.in — 只写直接依赖(包含 SDK URL)
# a4x-logger @ git+https://gitlab.addx.ai/CLOUD/a4x-logger-sdk.git@python/v1.0.0#subdirectory=python
# fastapi>=0.100,<1.0
# ...

# 生成锁文件(包含所有传递依赖的精确版本)
pip-compile requirements.in -o requirements.txt

# CI 用锁文件安装(--no-deps 跳过 resolver,严格按锁文件)
pip install --no-deps -r requirements.txt
```

`requirements.txt`(由 pip-compile 生成)应提交到 Git。

### 长期态:Nexus 3.x PyPI 私服(SRE 升级完成后)

Nexus 升级到 3.x 后,从 git+https 迁移到标准 PyPI:

**步骤 1**:`requirements.txt` 改为标准版本约束:

```
a4x-logger>=1.0.0,<2.0.0
```

**步骤 2**:`.gitlab-ci.yml` 移除 `GIT_CONFIG_*` 三行,改为配 pip index:

```yaml
variables:
  PIP_INDEX_URL: "https://nexus.addx.live/nexus/repository/pypi-internal/simple/"
  PIP_TRUSTED_HOST: "nexus.addx.live"
```

**步骤 3**:Dockerfile builder stage 移除 `apt-get install git` 和 git config RUN,
直接 `pip install -r requirements.txt`。
