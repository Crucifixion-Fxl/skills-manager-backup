# Component links 从 k8s ingress 派生 — website-only 规则

> SKILL.md §7 提到的 Component 🌐 link 详细。看这个 reference 当：决定要不要给 Component 打 🌐 link、不知道域名从哪扫、想自动化提取 ingress host。

## 规则

- **只有 `type: website` 的 Component**（admin / UI tier）的 prod/staging 域名加 🌐 `links` —— 这些是用户浏览器打得开的 URL。
- **`type: service`（backend 微服务）的 ingress host 不打 🌐 link** —— 它是内部服务间路由 / API Gateway 入口，不是浏览器 URL。想表达 API 入口走 `kind: API` 实体的 `definition.$text`（已经能定位 OpenAPI）。

判据：终端用户（外部访客 / B 端运营 / 开发自己）会**直接在浏览器里**打开吗？是 → website Component → 打 🌐；否 → service Component → 不打。

## 提取位置

| 仓型 | 扫哪 |
|---|---|
| **multi-env**（K8s overlays 形态）| `k8s/<svc>/overlays/<env>-<region>/runtime/ingress.yaml` 的 `spec.rules[].host`（prod + staging 各一条 link） |
| **single base**（单环境 / 仅有 base）| `<svc>/k8s/base/ingress.yaml` 的 `spec.rules[].host` |
| **Helm**（values 驱动 host）| `helm/values-<env>.yaml` 的 `ingress.hosts` 段 |
| **Vercel / Cloudflare Pages 等托管**| 部署平台 dashboard / `vercel.json` `alias` 字段 |

按 overlay 路径 / values 文件名推断 prod / staging / dev / preview 标签写在 `title` 里（用 emoji 区分 🌐 Production / 🧪 Staging / 🔧 Dev）。

## 实例 — engagement-admin

`services/value-added/engagement` 仓 `catalog-info.yaml`（line ~157 附近）—— 静态写法 + 计划用下面的 sync 脚本辅助：

```yaml
kind: Component
metadata:
  name: engagement-admin
  links:
    # 域名提取自 k8s/admin/overlays/<env>-us-tech/runtime/ingress.yaml 的 spec.rules[].host
    - { title: "🌐 Production", url: "https://engagement-admin.addx.live", icon: web }
    - { title: "🌐 Staging",    url: "https://engagement-admin-staging.addx.live", icon: web }
spec: { type: website }
```

对比同仓 `engagement-service`（`type: service`）则**不打** 🌐 link，只留仓库目录链接，注释说明：

```yaml
kind: Component
metadata:
  name: engagement-service
  links:
    - { title: 仓库, url: "https://gitlab.addx.ai/services/value-added/engagement/-/tree/main/backend/", icon: github }
    # ingress host 是内部路由，不是浏览器 URL；API 入口走 kind: API 实体的 definition.$text
spec: { type: service }
```

## scripts/sync-ingress-links.py — 起步版（建议输出，不直接改 yaml）

```bash
python3 scripts/sync-ingress-links.py <repo-root>
```

行为：

1. 扫 `k8s/**/ingress*.yaml` → 提取 `spec.rules[].host`
2. 反查 `catalog-info.yaml` 找 `spec.type == "website"` 的 Component
3. **只对 website Component 输出 `[suggest]`**（service Component 输出 `[skip]` + 提示）
4. 按 overlay 路径推断 prod / staging / dev / preview 标签

**输出样例**：

```
[suggest] Component engagement-admin:
  - { title: "🌐 Production", url: "https://engagement-admin.addx.live", icon: web }
  - { title: "🌐 Staging",    url: "https://engagement-admin-staging.addx.live", icon: web }

[skip] Component engagement-service (type=service): ingress host 是内部路由，不打 🌐 link
  发现 host: engagement-service.addx.live, engagement-service-staging.addx.live
```

⚠️ **目前只输出建议到 stdout，不直接改 catalog-info.yaml**（避 yq comment-preserving + 多 doc YAML 写回的复杂度）—— 让 PR review 决定是否合入。

后续 CI gate 化时再实现 in-place 写回（依赖 `ruamel.yaml` 等 round-trip 库）。

脚本：[`../scripts/sync-ingress-links.py`](../scripts/sync-ingress-links.py)。

## 反例

- ❌ 给 `type: service` 的 backend 微服务打 🌐 link（用户根本不该浏览那个域名，混入门户的 "Production" 列表会误导值班）
- ❌ 把 K8s internal cluster URL（`http://engagement-service.svc.cluster.local`）写成 link（不是 http(s)，catalog policy 拒，且无业务价值）
- ❌ 把开发本机 IP（`192.168.x.y:8080`）写到 commit 的 yaml 里
