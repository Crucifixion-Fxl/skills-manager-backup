# TechDocs 完整 setup — Approach A vs B + mkdocs.yml + mermaid hook + 排查

> SKILL.md §5 提到的 TechDocs 详细设置。看这个 reference 当：第一次配 TechDocs、Backstage Docs 标签 404 / Mermaid 不渲染、`techdocs-entity-path` 深链跳错。

## Approach A vs B — 选哪个？

| 维度 | A（多 mkdocs） | B（单 mkdocs + 深链，**推荐**） |
|---|---|---|
| 仓内 mkdocs.yml 数量 | 每 Component 一个 | 仅仓根一个 |
| Component 注解 | 各自 `techdocs-ref: dir:<path>` | host 用 `techdocs-ref: dir:.`；其它用 `techdocs-entity` + `techdocs-entity-path` |
| 深链稳定性 | 跨 Component 链接靠相对路径，重命名易断 | path 由 catalog 实体名生成，稳定 |
| TechDocs 构建任务数 | N 个 = N 次构建 | 1 次构建覆盖所有 |
| 维护成本 | 高（每目录维护 mkdocs.yml + nav） | 低（仓根一份） |
| 适用 | 真正独立、文档体量大、跨 Component 几乎无引用 | monorepo / Component 紧密协作 / 文档结构能镜像 catalog |

**推荐 B**：跟 §4 docs/ 镜像 catalog 一起用 —— 一份 mkdocs.yml + 一棵 docs/ 树 + 注解深链 = Component / System / API 实体页都精准跳到自己那一节。

## Approach B 注解样例（engagement 仓为模板）

```yaml
# host Component（装载 TechDocs 站点）
metadata: { name: engagement-service, annotations: { backstage.io/techdocs-ref: "dir:." } }
---
# 其它 Component 深链到 host 的子路径
metadata:
  name: engagement-admin
  annotations:
    backstage.io/techdocs-entity: component:default/engagement-service
    backstage.io/techdocs-entity-path: /architecture/engagement-console/engagement-admin/
---
# System 实体深链到 System 总览
kind: System
metadata:
  name: engagement-backend
  annotations:
    backstage.io/techdocs-entity: component:default/engagement-service
    backstage.io/techdocs-entity-path: /architecture/engagement-backend/
---
# API 实体深链到所属 Component 的 API 章节
kind: API
metadata:
  name: touchpoint-api
  annotations:
    backstage.io/techdocs-entity: component:default/engagement-service
    backstage.io/techdocs-entity-path: /architecture/engagement-backend/engagement-service/
```

Domain 实体（SKILL.md §1）也可加 `techdocs-entity` 指业务仓 host（深链 `path: /` = `docs/index.md` = Domain 总览）。**不能**同时写 `techdocs-ref` 和 `techdocs-entity`（一实体只能一个来源）。

## mkdocs.yml + mermaid_hook.py + docs/index.md（必需 3 件套）

完整模板：[`scripts/mkdocs.yml.template`](../scripts/mkdocs.yml.template)（~25 行，不写显式 nav）+ [`scripts/mermaid_hook.py`](../scripts/mermaid_hook.py)（让 Mermaid 渲染）。

**为什么不写显式 nav**：URL 是从**文件路径**生成的（`docs/architecture/<system>/<component>/index.md` → `/architecture/<system>/<component>/`），跟 nav 配置无关；`techdocs-entity-path` 引用的就是这个 URL。显式 nav 只在确实要改顺序/中文标签时塞少数几条。

**`docs/index.md` 必须有**（或 `docs/README.md`）—— 配了 `techdocs-ref: dir:.` 但没 `index.md` 时门户「Docs」标签直接 404（`engineering/architecture` 踩过：有 `docs/architecture/` 子目录但没根 `docs/index.md`）。

**Mermaid 图必须配 `mermaid_hook.py`**：RHDH 自带的 `mkdocs-techdocs-core` 覆盖了 `markdown_extensions`，```mermaid``` 围栏会被当普通高亮代码块输出，门户的 TechDocs Mermaid addon 不认。hook 把围栏换成 `<pre class="mermaid"><code>…HTML 转义后的图源…</code></pre>` —— 节点换行用 `<br/>` **不用 `\n`**。

## 不工作了怎么排查

annotation 不是所有 RHDH bundle 都支持 —— 具体看你的门户跑的是哪个 plugin-techdocs（**版本是 `infra/backstage` 决定的，本 skill 不维护版本号**）：

1. 看镜像 tag：`infra/backstage` 仓 `helm/values.yaml` 的 `image.tag`。
2. 实测 plugin 版本：`docker exec <rhdh-container> ls /opt/app-root/src/dynamic-plugins-root/ | grep '^backstage-plugin-techdocs-[0-9]'`。
3. 实测 annotation 是否被读：`docker exec <rhdh> grep -l "techdocs-entity-path" /opt/app-root/src/dynamic-plugins-root/backstage-plugin-techdocs-*/dist-scalprum/static/*.chunk.js` —— 命中 = 支持。

实测不支持时的临时 workaround：每个非-host 实体加一条 `metadata.links` 直链
```yaml
links:
  - title: "📖 Docs"
    url: "https://<rhdh-host>/docs/<host-namespace>/<host-kind>/<host-name>/<path>/"
    icon: docs
```
⚠️ **这种 link 是绝对 URL，会写死 hostname**，跨 staging/prod/本机三个 baseUrl 会断 —— 千万别把本机 IP（`192.168.x.y:8444`）写到要 commit 的 yaml 里。能用 annotation 就不要写 links；门户 bundle 升上来后删掉所有 workaround links。
