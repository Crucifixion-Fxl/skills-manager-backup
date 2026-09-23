# API 契约 — A 类生成器对照表 + drift gate + 手写 last-resort + Resource source-location

> SKILL.md §6 提到的 API 契约详细。看这个 reference 当：选生成器 / 配 CI drift gate / 手写 OpenAPI 时如何标 SSOT / Resource manifest source-location 怎么写。
>
> 本 reference **不**含 §6.1 能力命名 / 引用校验（那条留主 SKILL.md 主线）—— 引用解析的 CI 脚本范本见 [`api-refs-check.md`](./api-refs-check.md)。

## 原则

A 类（`API.spec.definition`）是 catalog 里**最容易撒谎的字段**。engagement audit 一次找出 9 BREAKING + 5 MINOR 漂移 —— 是常态，不是个例。

**能生成的必须生成（+ CI drift gate）；手写要显式声明「此文件即 SSOT」。**

## A 类生成器对照表

| 框架 | 生成命令 | catalog `$text` |
|---|---|---|
| **FastAPI** | `python3 -c "import json; from <pkg>.app import app; json.dump(app.openapi(), open('api/<svc>.openapi.json','w'), ensure_ascii=False, indent=2)"` | `./api/<svc>.openapi.json` |
| **NestJS** | `@nestjs/swagger SwaggerModule.createDocument` + 写文件 | `./api/<svc>.openapi.json` |
| **Express** | `swagger-jsdoc` + 写文件 | `./api/<svc>.openapi.json` |
| **go-zero `.api`** | `goctl api swagger --api <主.api> --dir build/ --filename openapi`（goctl ≥1.7 内置） | `./build/openapi.json` |
| **gRPC `.proto`** | 不生成 —— `spec.type: grpc` + `$text` 直接指 `.proto`（proto 即 SSOT） | `./api/<svc>.proto` |
| **开源部署仓（Letta 等）** | vendor 上游 `openapi.json`（升级时更新） | `./api/upstream.openapi.json` |

CI 都是 `api:gen-openapi` job：装依赖 → 生成 → `git diff --exit-code api/` drift check。

⚠️ **goctl 1.9.2 不支持 `type X struct {…}` 和 `map[string]any`**：`.api` 用了这些语法 → `syntax error: expected 'IDENT', got 'struct'`，归入「手写 SSOT」。

## CI drift gate 配置

完整 3-job 配置（routes / schemas / refs-resolve）+ 配套脚本见：

- [`../scripts/gitlab-ci-snippets/api-drift.yml`](../scripts/gitlab-ci-snippets/api-drift.yml) — `.gitlab-ci.yml` snippet（include 进现有 pipeline）
- [`../scripts/api-drift/check-routes.py`](../scripts/api-drift/check-routes.py) — 路径集合比对
- [`../scripts/api-drift/check-schemas.py`](../scripts/api-drift/check-schemas.py) — 字段集合比对
- [`../scripts/api-drift/check-api-refs.sh`](../scripts/api-drift/check-api-refs.sh) — 引用解析（详见 [api-refs-check.md](./api-refs-check.md)）

模式：每个 job `stage: lint`、`needs: []`、`rules: changes: [routes / types / openapi / 脚本本身]` + `if: $CI_PIPELINE_SOURCE == "schedule"` nightly 兜底（依赖升级带来的间接漂移）。

**两层 gate 必须共存（已有 `redocly lint` 不够 —— 它只管 OpenAPI 自己的语法）**：

- **路径集合**：从 routes.go / FastAPI app routes / `.api` 提取 `(METHOD, PATH)` 集合，跟 `openapi.yaml` `paths:` 比；差集非空 → fail。
- **字段集合**：从 Go struct / Pydantic model / `.api` type 提取 JSON 字段集合，跟 OpenAPI schemas `properties:` 比；diff 非空 → fail。

## 手写 OpenAPI = last resort（4 种例外）

只有以下四种情况允许手写 OpenAPI（其它必须代码生成）：

1. **stdlib `net/http` / Gin / Echo 等没生成器** —— 短期手写，长期建议迁 framework
2. **go-zero `.api` 用了 goctl 不支持的语法** —— 如 `type X struct {…}`、`map[string]any`
3. **占位仓（无代码）** —— 只声明契约还没实现
4. **静态文件布局当 API**（Bazel BCR / APISIX 声明式路由等）

**手写文件头必须标注 SSOT 警告**：

```yaml
# WARNING: 此文件即 SSOT —— <原因>。改 <路由代码文件> 时必须同步改本文件。
# CI 的 api:lint:openapi（redocly lint）只校验语法，无法 drift check。
```

CI 只能跑 lint：

```yaml
api:lint:openapi:
  image: node:20-slim
  script:
    - npx --yes @redocly/cli@latest lint api/<svc>.openapi.yaml
```

**实战样板**：

- FastAPI 生成 → `services/audiences`（`api:gen-openapi` + `api/audiences-bff.openapi.json`）
- stdlib net/http 手写 SSOT → `services/customer-care`（`api/smart-popup.openapi.yaml` + `api:lint:openapi`）
- 已改回生成 + drift check：`services/audiences`、`applications/naturehood`
- 无法自动生成已标 SSOT：`applications/golf` / `infra/letta` / `infra/bcr` / `infra/backend/apisix-gateway`

## §6.2 — Resource source-location 必须指 manifest（hard rule）

Resource（database / s3-bucket / kafka-topic / …）的 source-of-truth 在 GitOps 仓的 manifest，**不是** `catalog-info.yaml`：

- Crossplane Claim（RDS / S3 / IAM）
- K8s native manifest（in-cluster Postgres/Redis）
- Terraform / Helm values
- Prisma schema / migration

Resource 实体必须**显式覆盖** `view-url` + `source-location` 指它的 manifest（否则跳 catalog-info.yaml，对查 RDS spec 的人毫无用处）：

```yaml
kind: Resource
metadata:
  name: engagement-mysql
  annotations:
    backstage.io/source-location: url:https://gitlab.addx.ai/services/value-added/engagement/-/blob/<branch>/backend/k8s/base/mysql-claim.yaml
    backstage.io/view-url: https://gitlab.addx.ai/services/value-added/engagement/-/blob/<branch>/backend/k8s/base/mysql-claim.yaml
spec: { type: database }
```

**找不到精确文件就指目录**（`backend/k8s/base/` 末尾带 `/`）+ description 加 TODO「Crossplane Claim 待补」。

**将来 B 类自动发现**：TeraSky Crossplane Resources 插件 / Kubernetes Ingestor（`infra/backstage#19`）接入后，运行态字段（RDS endpoint、bucket 名、ARN）从 k8s API 自动 sync —— 现在没接，暂手维护。
