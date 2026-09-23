# Vertical HTML 文档写作参考

本文是 `architect` 的 vertical-first 详细写法参考。`architect/SKILL.md` 只保留准入、目录和硬规则；当需要实际创建、重构或 review `docs/architecture/verticals/<vertical>/` 时，按本文执行。

Vertical 的面向人正文使用同一套 HTML 基础模板：统一 header、导航、大纲、`.dm-*` 图语言、ADR 链接和 SSOT 表。新 ADR 不套 HTML 模板，固定按 [`adr-format.md`](adr-format.md) 使用机器友好的 Markdown。

## 1. 写作入口

先判断是否真的需要 vertical：

| 问题 | 结论 |
|---|---|
| 是否跨端云 / 多系统闭环？ | 是 → 可以建 vertical；否 → 留在 system/component 子树。 |
| 是否需要端云字段、flow、测试统一对齐？ | 是 → vertical 视图有价值。 |
| 是否只是横切 infra？ | 是 → 放 `docs/architecture/<topic>.html`，不进 verticals。 |
| 是否有实现进度要写？ | 当前完成度、验证结果、剩余风险写 `PROGRESS.html` 或项目既有进度页，归 `dev-workflow`；不要写进设计 SSOT。 |

## 2. 推荐顺序

1. 读输入：product US / PRD、已有 ADR、`catalog-info.yaml`、相关代码目录、现有 vertical docs。
2. 画文档地图：列出这个 vertical 中每类问题的 HTML SSOT，先定“不重复什么”。
3. 写 `index.html`：先把架构图、核心流程、DDD 边界、代码结构和 SSOT 表放进总入口。
4. 拆细节：端内细节放 `app/index.html`，云端细节放 `backend/index.html`，跨端契约放 `contracts/api.html`，跨边界 sequence 放 `flows/*.html`。
5. 补 integration/service：只要被外部集成或对外暴露 reusable capability，就写 `integration.html` / `services.html`。
6. 对照 checklist：检查 `index.html` 是否能独立回答“是什么、谁负责、什么时候触发、走什么机制、禁止什么、去哪看细节”。

## 3. 目录职责

| 文件 / 目录 | 职责 | 不写什么 |
|---|---|---|
| `index.html` | 设计驾驶舱：业务价值、架构图、核心流程、DDD 边界、代码结构、SSOT 导航、关键规则 | 不展开端内实现细节，不复制 ADR 全文，不写当前进度流水账 |
| `domain.html` | vertical 统一语言：术语、ID、数据生产 ownership、不变量、canonical catalog | 不复写全局 `domain-model.html`，不维护所有字段 schema |
| `integration.html` | 宿主 App / 其它 vertical 如何接入、传入什么依赖、不能调用什么内部对象 | 不写内部状态机细节 |
| `services.html` | 对外 public service / API / SDK 能力表、调用语义、稳定性承诺 | 不写 private helper 或 repository |
| `app/index.html` | App / Flutter 端内部机制、package 边界、端内状态机 | 不重复 cloud 逻辑 |
| `backend/index.html` | 后端 Component 协作、表 ownership、API / job / event 归属 | 不重复 App 端本地状态 |
| `contracts/api.html` | 端云契约、字段映射、版本演进、兼容策略 | 不写业务背景长文 |
| `data/schema.html` | 数据 ownership、schema、迁移、retention、索引 | 不替代 contracts |
| `flows/*.html` | 跨端云 sequence、关键 user flow、失败恢复 | 不放单端私有 helper 调用 |
| `adrs/*.md` + `adrs/README.md` | vertical 内部决策推理和机器可读索引 | 不写多个决策在一个文件；不新增 HTML ADR |
| `testing/strategy.html` | L1-L4 分层测试策略、fixture、异常矩阵、追溯 | 不写本轮测试执行结果 |
| `PROGRESS.html` | 当前实现、验证、缺口、下一步 | 不写设计机制 SSOT；由 `dev-workflow` 维护 |

## 4. `index.html` 内容模块

`index.html` 必须让读者在第一屏之后知道这个 vertical 的边界和入口。推荐结构：

```html
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Vertical: <name> · 技术方案</title>
  <style>
    /* 同一 HTML shell；复用项目 site.css / .dm-* 语义色 */
  </style>
</head>
<body>
  <main>
    <nav aria-label="Vertical 文档导航">
      <a href="./domain.html">Domain</a>
      <a href="./integration.html">Integration</a>
      <a href="./services.html">Services</a>
      <a href="./contracts/api.html">Contracts</a>
      <a href="./flows/">Flows</a>
      <a href="./adrs/README.md">ADRs</a>
    </nav>

    <header>
      <p>Vertical · <name></p>
      <h1><name> 技术方案</h1>
      <p>一句话说明这个 vertical 为用户或业务解决什么问题。</p>
    </header>

    <section id="boundary">
      <h2>1. 业务价值与边界</h2>
      <table>
        <tr><th>维度</th><th>App / Client</th><th>Cloud / Backend</th></tr>
        <tr><td>职责</td><td>...</td><td>...</td></tr>
        <tr><td>权威数据</td><td>...</td><td>...</td></tr>
        <tr><td>不拥有</td><td>...</td><td>...</td></tr>
      </table>
    </section>

    <section id="architecture">
      <h2>2. 整体架构</h2>
      <svg role="img" aria-label="<vertical> 端云职责、数据流和外部边界图">...</svg>
      <p class="dm-figcap">图 1 · 一句话解释图。</p>
      <ul class="dm-legend">...</ul>
    </section>

    <section id="flows"><h2>3. 核心流程</h2></section>
    <section id="ddd"><h2>4. DDD 边界</h2></section>
    <section id="code"><h2>5. 代码结构</h2></section>
    <section id="ssot"><h2>6. 问题 -> SSOT -> 引用方式</h2></section>
    <section id="rules"><h2>7. 关键边界规则</h2></section>
  </main>
</body>
</html>
```

## 5. `domain.html` 写法

`domain.html` 不只是词汇表。它要帮助 App、Backend、QA 和 AI Agent 对同一个概念形成稳定映射。

必须包含：

- 术语 / ID / scope：如 `device_sn`、`player_id`、`shot_id` 的生成方、唯一性和生命周期。
- 数据生产 ownership：哪个端或服务生产 canonical 数据。
- 不变量：字段必须满足什么约束，什么时候允许为空。
- 与全局 `domain-model.html` 的关系：全局术语只链接，不复写。

反模式：

- 把所有 DB 字段复制到 `domain.html`。
- 只写中文解释，没有精确英文代码名。
- 把全局领域模型复制一份，导致 `domain-model.html` drift。

## 6. `integration.html` 写法

触发条件：

- 其它 vertical / App shell / SDK/package / backend component 要调用本 vertical。
- 本 vertical 提供 reusable capability。

必须写：

- 宿主要传入什么依赖 / context。
- 宿主允许调用的 public API / service / package。
- 禁止路径：不能直接写什么表、不能 import 什么 internal、不能绕过什么 gateway。
- 初始化顺序、错误边界、降级语义。

`integration.html` 是“别人如何正确使用我”，不是“我内部如何实现”。

## 7. `services.html` 写法

对外 service/API/SDK 必须列清：

| 能力 | 调用入口 | 稳定性 | 幂等性 | 错误语义 | Owner |
|---|---|---|---|---|---|

规则：

- public 能力进入 services。
- internal helper 不进入 `services.html`。
- service 如果有多个端实现，必须说明 App / Backend / Web 哪边是 owner。

## 8. `flows/*.html` 写法

只放跨边界流程。每个流程必须包含：

- 触发器：谁触发，用户动作还是系统事件。
- 参与方：App / Backend / Embedded / External。
- 顺序约束：哪些必须先发生，哪些可异步。
- 失败恢复：超时、重试、重复、离线、回滚。
- 观测点：关键 log / metric / trace / event。

复杂流程优先用 `visual-documentation-skills:flowchart-creator` 的思路，再用项目 `.dm-*` 图语言实现。

## 9. 防 drift checklist

提交或 review vertical 文档前检查：

- `index.html` 有架构图、核心流程图、DDD 边界、代码结构和 `问题 -> SSOT -> 引用方式` 表。
- 每个复杂问题只有一个 HTML SSOT；其它文档链接过去，不复制。
- `domain.html` 明确 ID、ownership、不变量，而不只是词汇表。
- 跨端流程在 `flows/`，单端内部细节在端内目录。
- 被外部集成的 vertical 有 `integration.html`；对外 service 有 `services.html`。
- 当前实现差距和进度状态分离：差距写设计文档，进度写 `PROGRESS.html` 或项目进度页。
- 测试策略只写 strategy 和 scenario；具体通过哪些命令写进度页。
- 新 ADR 使用 `NNNN-*.md` + YAML front matter + 五个固定 H2 章节；既有 HTML ADR 只保留历史兼容，不要求批量迁移。
