# 反例索引 — 速查 5 个最常踩的坑

> SKILL.md §8 提到的反例完整版。看这个 reference 当：catalog-info.yaml 被 catalog policy 拒、Backstage 实体页字段空 / 跳错、不确定某种写法是否合法。
>
> 每条反例 = ❌ Bad 写法 + 报错信号 + ✅ 正确写法 + 涉及的 SKILL.md 章节链接。

## ❌ Bad 1 — tag 含斜杠

```yaml
metadata:
  tags:
    - layer/base-platform   # ❌ tag 不能有 /，catalog policy 拒绝整个实体
```

**报错**：`InputError: tag <X> is invalid`，整个实体不进 catalog。

**正确**：`layer-base-platform`（kebab-case，只含 `[a-z0-9+#-]`）。

> 涉及：SKILL.md §2 Component metadata.tags checklist

---

## ❌ Bad 2 — links.url 用相对路径

```yaml
metadata:
  links:
    - title: 接入文档
      url: /docs/integrate            # ❌ 相对路径
    - title: 源码
      url: ./backend/main.go          # ❌ 相对路径
    - title: 仓库
      url: git@gitlab.addx.ai:foo.git # ❌ SSH URL，不是 http(s)
```

**报错**：catalog policy fail（`links.0.url is not valid`），整个实体处理失败。

**正确**：用完整 https URL：

```yaml
metadata:
  links:
    - title: 接入文档
      url: https://gitlab.addx.ai/services/foo/-/tree/main/docs/integrate.md
    - title: 仓库
      url: https://gitlab.addx.ai/services/foo
      icon: github
```

> 涉及：SKILL.md §2 Component metadata.links checklist

---

## ❌ Bad 3 — FastAPI 服务手写 OpenAPI 而非生成

```yaml
spec:
  definition:
    $text: ./api/foo.openapi.yaml   # ❌ 手写的，跟路由实现漂移
```

**症状**：catalog 实体页显示的 API 跟实际线上行为不一致；客户端按 catalog 生成 SDK 调用 → 404 / 422。engagement audit 一次发现 9 BREAKING + 5 MINOR 漂移。

**正确**：CI 跑 `app.openapi()` 生成 → drift check（详见 [api-contract-drift.md](./api-contract-drift.md)）：

```yaml
# .gitlab-ci.yml
api:gen-openapi:
  stage: lint
  script:
    - python3 -c "import json; from foo.app import app; json.dump(app.openapi(), open('api/foo.openapi.json','w'), ensure_ascii=False, indent=2)"
    - git diff --exit-code api/   # drift check
```

手写**只是 last resort 4 种例外**（stdlib / `.api` 不兼容 / 占位仓 / 静态 BCR/APISIX），且文件头必须标 `WARNING: 此文件即 SSOT`。

> 涉及：SKILL.md §6 / [api-contract-drift.md](./api-contract-drift.md)

---

## ❌ Bad 4 — `providesApis` 引用 catalog 不存在的实体

```yaml
spec:
  providesApis:
    - my-custom-api   # ❌ catalog 无此 kind: API、本 MR 也没新增
                      #    → service-catalog-search 反查失败 → CI fail
```

**症状**：Backstage 实体页 Relations 段挂"未知实体"，引用悬空，catalog ingestion 报 warning。

**正确（两选一）**：

1. **本 MR 同时新增 `kind: API name=my-custom-api`**（CI 当 valid，避 ingestion 时延误报）：

   ```yaml
   ---
   kind: API
   metadata:
     name: my-custom-api
     title: My Custom API
   spec:
     type: openapi
     lifecycle: experimental
     owner: group:default/team-foo
     definition:
       $text: ./api/my-custom-api.openapi.yaml
   ```

2. **改引用现有名**（先用 `service-catalog-search` skill 反查能力 / 直查 catalog REST API：`GET /api/catalog/entities?filter=kind=API,metadata.name=<name>`）。

catalog 是 SSOT，无需预先去 markdown 清单注册（旧规则已废止）。

> 涉及：SKILL.md §6.1（能力命名 / 引用校验 canonical）+ [api-refs-check.md](./api-refs-check.md)

---

## ❌ Bad 5 — backend 微服务打 🌐 link

```yaml
kind: Component
metadata:
  name: engagement-service
  links:
    - title: "🌐 Production"
      url: "https://engagement-service.addx.live"   # ❌ service Component 的 ingress 是内部路由
spec:
  type: service
```

**症状**：门户上 service Component 错位地挂着"线上"按钮 → 值班 / 新人误以为这是用户访问入口，点开是 503 / API JSON。

**正确**：

- **service Component 不打 🌐 link**，只留仓库目录链接
- API 入口走 `kind: API` 实体的 `definition.$text`（已经能定位 OpenAPI / proto）
- 只有 `type: website` 的 Component（admin / 落地页 / 控制台）才打 🌐 link 指 prod/staging 域名

```yaml
kind: Component
metadata:
  name: engagement-service
  links:
    - { title: 仓库, url: "https://gitlab.addx.ai/services/value-added/engagement/-/tree/main/backend/", icon: github }
    # ingress host 是内部路由，不是浏览器 URL；API 入口走 kind: API 实体的 definition.$text
spec:
  type: service
```

> 涉及：SKILL.md §7 / [website-links.md](./website-links.md)

---

## ✅ Good 综合范例

```yaml
kind: Component
metadata:
  name: novu-service
  title: Novu Push Service
  description: 统一推送通道封装（FCM / APNs / WebPush），对内提供 push 能力
  tags: [layer-base-platform, push, no-direct-fcm]   # ✅ 合法 tag（无 /）
  links:
    - title: 仓库
      url: https://gitlab.addx.ai/services/novu      # ✅ 绝对 https URL
      icon: github
  annotations:
    backstage.io/source-location: url:https://gitlab.addx.ai/services/novu/-/tree/main/backend/
    backstage.io/view-url: https://gitlab.addx.ai/services/novu/-/tree/main/backend/
    backstage.io/edit-url: https://gitlab.addx.ai/services/novu/-/edit/main/README.md
    backstage.io/techdocs-ref: dir:.
    backstage.io/adr-location: docs/architecture/notification/novu-service/adrs
    a4x.io/cicd-app-name: novu-api
spec:
  type: service                                       # ✅ 不打 🌐 link
  lifecycle: production
  owner: group:default/team-platform
  system: notification
  providesApis: [push-api]                            # ✅ 同 catalog 内有 kind: API name=push-api
---
kind: API
metadata:
  name: push-api
spec:
  type: openapi
  lifecycle: production
  owner: group:default/team-platform
  system: notification
  definition:
    $text: ./api/push.openapi.json                    # ✅ CI api:gen-openapi 生成 + drift check
```
