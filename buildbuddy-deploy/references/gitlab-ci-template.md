# GitLab CI 模板

> 同步来源：DEV/buildbuddy/.gitlab-ci.yml

BuildBuddy 仓库的 CI 配置，包含 kustomize 校验和镜像同步。当前版本号和 Runner tag 见 [facts.md](facts.md)。

## .gitlab-ci.yml

```yaml
variables:
  IMAGE_BASE: "${HARBOR_REGISTRY}/cicd"
  BUILDBUDDY_VERSION: "<verified-version>"  # run crane ls to check
  BUILDBUDDY_UPSTREAM: "gcr.io/flame-public/buildbuddy-app-onprem"

default:
  tags:
    - sg-amd64
  retry:
    max: 2
    when:
      - runner_system_failure
      - stuck_or_timeout_failure

stages:
  - validate
  - build

validate-k8s:
  stage: validate
  image: registry.k8s.io/kustomize/kustomize:v5.4.3
  script:
    - kustomize build k8s/overlays/sg-devops/ > /tmp/rendered.yaml
    - |
      if grep -q "REQUIRE_UPDATE_" /tmp/rendered.yaml; then
        echo "WAF ARN placeholder detected."
        if [ "$CI_COMMIT_BRANCH" = "main" ] || [ "$ENFORCE_WAF_ARN" = "true" ]; then
          echo "ERROR: main branch requires real WAF ACL ARN."
          exit 1
        fi
        echo "WARNING: non-main pipeline continues with placeholder."
      fi
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
    - if: $CI_COMMIT_BRANCH == "main"

build-mirror:
  stage: build
  image:
    # MUST use :debug tag — :latest is distroless without shell
    name: gcr.io/go-containerregistry/crane:debug
    entrypoint: [""]
  script:
    # crane reads ~/.docker/config.json, copy from kaniko secret
    - mkdir -p $HOME/.docker && cp /kaniko/.docker-secret/config.json $HOME/.docker/config.json 2>/dev/null || true
    - echo "Mirroring BuildBuddy ${BUILDBUDDY_VERSION} to Harbor..."
    - crane copy ${BUILDBUDDY_UPSTREAM}:${BUILDBUDDY_VERSION} ${IMAGE_BASE}/sg-devops/buildbuddy:${BUILDBUDDY_VERSION}
    - crane copy ${BUILDBUDDY_UPSTREAM}:${BUILDBUDDY_VERSION} ${IMAGE_BASE}/sg-devops/buildbuddy:${CI_COMMIT_SHA}
    - echo "Mirror complete."
  rules:
    - if: $CI_COMMIT_BRANCH == "main"
      changes:
        - .gitlab-ci.yml
    - if: $CI_COMMIT_BRANCH == "main"
      when: manual
      allow_failure: true
```

## 关键注意事项

1. **crane:debug 而非 crane:latest** — distroless 镜像没有 shell，Runner 无法执行 script
2. **拷贝 docker config** — crane 默认读 `~/.docker/config.json`，Runner 的凭据在 `/kaniko/.docker-secret/`
3. **版本验证** — 修改 BUILDBUDDY_VERSION 前先确认版本存在：
   ```bash
   docker run --rm gcr.io/go-containerregistry/crane:debug ls gcr.io/flame-public/buildbuddy-app-onprem | sort -V | tail -20
   ```
4. **WAF 占位符检查** — main 分支强制要求真实 WAF ARN，非 main 分支允许占位符用于开发迭代
5. **镜像同步非全自动** — 只有 `.gitlab-ci.yml` 文件变更时自动触发 `build-mirror`，其他 main push 需手动点击执行
