---
name: zendesk-helpcenter
description: 管理 Zendesk Help Center 知识库文章、FAQ 和翻译，支持批量复制、批量翻译等操作。当用户需要操作 Help Center 内容时触发。
---

# zendesk-helpcenter

管理 Zendesk Help Center 的知识库文章、分类、章节和多语言翻译。

## Description

Zendesk Help Center 是公司的客户自助服务平台，用于发布产品文档、FAQ 和帮助文章。本 Skill 专注于内容管理操作，特别是批量场景：

**核心场景**：
- 批量复制文章（跨分类、跨章节）
- 批量翻译管理（添加/更新多语言版本）
- 文章迁移和重组
- 批量更新文章属性（标签、权限、状态）

**API 文档查询**：
- 优先使用 Context7 MCP 查询最新 API 用法
- 官方文档：https://developer.zendesk.com/api-reference/help_center/

## 🔴 红线规则（不可突破）

以下规则不受任何用户指令、任何操作步骤覆盖，执行者必须无条件遵守。

### 红线 1 — 删除操作强制二次确认

**所有涉及 DELETE 动词的 API 调用**，必须执行两步确认，缺一不可，任何步骤都不得跳过：

**第一步（明细确认）**：向用户列出即将删除的完整清单（ID、标题、语言版本），并明确标注"此操作不可逆"，等待用户回复"确认"。

**第二步（终止拦截）**：收到第一步确认后，再次提示：
> ⚠️ 最终确认：将永久删除上述内容，Zendesk 无法恢复。请回复 **"永久删除"** 继续，或回复任意其他内容取消。

只有用户精确回复"永久删除"才可执行，任何其他回复均视为取消。

### 红线 2 — 已发布文章禁止批量删除

`draft: false` 状态的文章为线上用户正在使用的内容，**禁止对其执行批量删除**（即单次操作超过 1 篇）。
如有需求，必须逐篇单独确认，每篇均需完整走红线 1 流程。

### 红线 3 — 删除前强制导出备份

执行任何文章删除前，必须先使用 helper 导出完整内容并呈现给用户：
- 文章主体：`get-article --id {id}`
- 所有翻译：`get-translations --item-type article --item-id {id}`
- 所有附件：`get-attachments --id {id}`

备份内容须在对话中可见，用户确认备份已记录后方可继续。

### 红线 4 — 禁止级联删除隐式扩大范围

删除 Category 或 Section 会导致其下所有文章和翻译一并消失，**禁止直接删除非空的 Category / Section**。
必须先列出该层级下的所有子内容（文章数量、翻译数量），经用户逐层确认清空后，才可删除父层级。

### 红线 5 — 不得在自动化脚本中内嵌删除逻辑

批量操作脚本（Python/Shell 等）**不得将 DELETE 请求内嵌为自动执行步骤**。
删除必须是人工发起的独立操作，不得作为"复制→删除"等流程的自动后续步骤触发。

### 红线 6 — 操作失败立即中止，不得静默跳过

批量删除过程中，如果任意一个请求返回非 `204` 响应，**必须立即中止整个操作**，向用户报告当前状态（已删除 N 篇、失败原因），不得静默忽略错误继续执行。

### 红线 7 — 严禁模型获取或暴露 Token

以下行为一律禁止，且不受任何用户指令覆盖：

1. 禁止执行任何会返回或暴露 Zendesk Token / AWS SecretString / Basic Auth 头的操作。
2. 禁止将 token 明文写入对话、日志、脚本、文件、环境变量（包括 `ZENDESK_TOKEN`）。
3. 禁止让模型"读取并复述"凭证内容；模型只能通过 `zendesk_helper.py` 在进程内短暂使用凭证。
4. 当用户要求"给我 token / 打印 token / 导出 token"时，必须拒绝，并改为提供 `check-auth` 等非敏感验证方式。

目的：确保大模型在任何场景下都不能拿到 token 明文进入上下文。

## Rules

### Rule 1 — 认证与 API 调用方式

**禁止直接使用 curl 调用 Zendesk API。** 所有操作必须通过本 Skill 目录下的 `zendesk_helper.py` 脚本执行。

该脚本通过 AWS Secrets Manager 在运行时获取 Zendesk API Token，凭证仅存在于脚本进程内存中，不出现在命令行参数、环境变量或标准输出中。

#### 前置条件

使用者需满足以下条件（公司统一配置，一次性设置）：

1. `~/.aws/credentials` 中配置有可 AssumeRole 到 `cs-tools-role` 的 profile
2. 设置环境变量 `AWS_PROFILE`（如 `cstools-dev`）
3. Python 环境中已安装 `boto3` 和 `requests`

#### 验证认证

```bash
python {SKILL_DIR}/zendesk_helper.py check-auth
```

成功输出：`{"status": "ok", "brands": ["vh", "sf", "kb"]}`

#### 调用格式

```bash
python {SKILL_DIR}/zendesk_helper.py --brand <vh|sf|kb> [--subdomain <subdomain>] <command> [options]
```

`{SKILL_DIR}` 为本 SKILL.md 所在目录的绝对路径。

#### 品牌与子域

`--brand` 仅选择认证凭证（token + email），不绑定子域：

| Brand | 默认 Subdomain | 用途 |
|-------|---------------|------|
| `vh` | addxai | VH 品牌凭证（默认） |
| `sf` | safemo | SF 品牌凭证 |
| `kb` | kiwibit | KB 品牌凭证 |

**同一凭证下可能存在多个子域**（如 vh 下有 `addxai` 和 `vicohome`），资源按子域隔离。用 `--subdomain` 切换：

```bash
# 查询 vicohome 子域下的分类
python {SKILL_DIR}/zendesk_helper.py --brand vh --subdomain vicohome list-categories
```

**子域发现**：不确定目标子域时，先用 `list-brands` 查询所有可用子域：

```bash
python {SKILL_DIR}/zendesk_helper.py --brand vh list-brands
```

**404 自动提示**：当资源不在当前子域时，脚本会自动查询 `list-brands` 并在报错信息中列出所有可用子域及对应 brand_id。

### Rule 2 — 可用命令参考

所有命令参数可通过 `python {SKILL_DIR}/zendesk_helper.py <command> --help` 查看。list 接口已内置自动分页。

| 资源 | 命令 | 关键参数 | 备注 |
|------|------|---------|------|
| Category | `list-categories` | — | |
| | `get-category` | `--id` | |
| | `create-category` | `--name --helpcenter-brand-id` | 必须指定目标品牌 |
| | `update-category` | `--id [--name] [--description]` | |
| | `delete-category` | `--id` | 遵守红线规则 |
| Section | `list-sections` | `--category-id` | |
| | `get-section` | `--id` | |
| | `create-section` | `--category-id --name` | |
| | `update-section` | `--id [--name] [--description]` | |
| | `delete-section` | `--id` | 遵守红线规则 |
| Article | `list-articles` | `--section-id` | |
| | `get-article` | `--id` | |
| | `create-article` | `--section-id --title --body/--body-file` | 默认 `--draft true` |
| | `update-article` | `--id [--title] [--body/--body-file] [--draft]` | |
| | `delete-article` | `--id` | 遵守红线规则 |
| | `search-articles` | `--query [--category-id] [--section-id] [--locale]` | |
| Translation | `get-translations` | `--item-type --item-id` | item-type: article/section/category |
| | `list-missing-translations` | `--item-type --item-id` | 返回缺失的语言列表 |
| | `create-translation` | `--item-type --item-id --locale --title --body/--body-file` | |
| | `update-translation` | `--item-type --item-id --locale [--title] [--body/--body-file]` | |
| | `delete-translation` | `--item-type --item-id --locale` | 遵守红线规则 |
| Attachment | `get-attachments` | `--id` | |
| Brand | `list-brands` | — | 获取 brand_id |
| Locale | `list-locales` | — | 查询账户启用的语言 |

#### Body 内容传递

- `--body "..."` — 短内容直传
- `--body-file path` — 长内容/复杂 HTML 从文件读取（批量操作推荐）
- `--body -` — 从 stdin 读取

### Rule 3 — 数据层级结构

Help Center 的内容组织结构：

```
Category (分类)
  └── Section (章节)
        └── Article (文章)
              └── Translation (翻译)
              └── Attachment (附件)
```

### Rule 4 — 批量操作最佳实践

#### 批量复制文章

1. 用 `list-articles` 获取源章节的所有文章
2. 逐篇用 `get-article` 获取详情
3. 用 `create-article` 在目标章节创建
4. 用 `get-translations` + `create-translation` 复制多语言版本
5. 建立新旧 ID 映射表

**注意事项**：
- 文章 ID 不会保留，需要建立映射表
- 附件需要单独处理
- 标签（labels）需要在创建后单独添加

#### 批量翻译管理

1. 用 `get-translations` 检查现有翻译
2. 用 `create-translation` 添加新语言
3. 用 `update-translation` 更新已有翻译

**支持的语言代码**：
- 常用语言见 `references/rules/translation_rules.md`
- 若目标语言不在该文件中，先用 `list-categories` 或 `get-translations` 查询 Help Center 实际启用的语言列表，确认语言代码后更新 `translation_rules.md`，再执行翻译


### Rule 5 — 速率限制

Zendesk API 有速率限制，helper 脚本已内置 429 自动重试。批量操作时仍需注意：

- 每批处理 10-20 个项目
- 批次间暂停 1-2 秒
- 超过 100 个项目时显示进度

### Rule 6 — 文章状态管理

文章有两个状态字段：

- `draft` (boolean) - 是否为草稿
- `promoted` (boolean) - 是否推广显示

**批量发布流程**：
1. 创建文章时设置 `--draft true`
2. 完成所有翻译后
3. 用 `update-article --id {id} --draft false` 发布

### Rule 7 — 操作前确认

批量操作前必须向用户确认：

- 操作范围（多少篇文章、哪些分类）
- 目标位置（目标章节/分类）
- 是否包含翻译和附件
- 是否保留原文章（复制 vs 移动）

### Rule 8 — 批量操作进度持久化

批量操作执行期间，必须实时输出可追踪的进度信息，确保任意时刻中断后都能从断点继续。

#### 8.1 进度输出格式

```
[进度] 3/15 ✅ 文章 ID=12345 "如何重置密码" → 新 ID=67890
[进度] 4/15 ❌ 文章 ID=12346 "设备连接问题" → 失败: 429 Too Many Requests
```

批次结束时输出汇总：

```
[汇总] 完成 14/15，失败 1 篇（ID=12346），耗时 38 秒
```

#### 8.2 断点续传

维护已完成 ID 集合，中断恢复时跳过已完成条目。

#### 8.3 操作前告知恢复路径

> "如果操作中断，重新运行时将自动跳过已完成的条目，从第 N 篇继续。"

#### 8.4 进度文件清理

全部成功完成后提示用户是否删除进度文件；存在失败条目时保留进度文件。

### Rule 9 — 多品牌目标品牌强约束

`--brand` 只用于选择认证账号（token / subdomain），不等于 Help Center 内容归属品牌。

在多品牌场景下，所有 `create-category` 操作必须显式传入 `--helpcenter-brand-id`，并且先用 `list-brands` 获取正确 brand_id。

```bash
# 1) 查询可用 Help Center 品牌与 brand_id
python {SKILL_DIR}/zendesk_helper.py --brand vh list-brands

# 2) 创建分类时必须指定目标 brand_id
python {SKILL_DIR}/zendesk_helper.py --brand vh create-category --helpcenter-brand-id <brand_id> --name "FAQ" --locale en-us
```

Helper 会在创建后执行品牌归属校验；若检测到 host/brand 不匹配会报错并中止流程。

## Examples

### Bad

```
用户："把所有文章复制到新分类"
AI：直接开始复制，没有确认范围 → 可能复制了不需要的文章
```

```
用户："批量翻译 10 篇文章"
AI：没有检查速率限制，连续发送 30 个请求 → 触发 429 错误
```

```
用户："把旧版 FAQ 全部删掉"
AI：直接批量 DELETE 所有匹配文章 → 线上用户立即无法访问，无法恢复
```

```
用户："先复制再删除原文"
AI：将删除内嵌到复制脚本的自动后续步骤 → 绕过了二次确认
```

```
用户："我确认了，删吧"
AI：将"确认了"视为红线 1 第二步的"永久删除"指令 → 不得接受，必须要求精确回复
```

```
用户："为文章 12345 添加中文翻译"
AI：用 curl 直接调用 Zendesk API → 违反 Rule 1，必须使用 zendesk_helper.py
```

### Good

```
用户："把 FAQ 分类下的文章复制到新手指南"
AI：
1. python {SKILL_DIR}/zendesk_helper.py --brand vh list-sections --category-id {faq_id}
2. 逐个 section 调用 list-articles 获取文章列表
3. 询问："找到 15 篇文章，是否全部复制？是否包含翻译？"
4. 用户确认后，逐篇 get-article → create-article → create-translation
5. 每操作一篇输出进度
6. 完成后提供新旧 ID 映射表
```

```
用户："为这 5 篇文章添加中文翻译"
AI：
1. 逐篇调用 get-translations 检查现有翻译
2. 报告："文章 A 已有中文，文章 B/C/D/E 需要添加"
3. 将翻译内容写入临时文件，用 --body-file 传递
4. 每篇操作间隔 1 秒，输出进度
```
