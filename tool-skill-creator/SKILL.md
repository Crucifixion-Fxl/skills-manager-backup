---
name: tool-skill-creator
description: 内部工具 Skill 化的全流程项目经理。引导用户完成工具分档决策、信息采集、SKILL.md 编写、格式验证和迭代优化五个阶段，最终产出可用的工具类 Skill。当用户说"把 XX 工具做成 Skill"、"帮我 Skill 化 XX 系统"、或需要将内部工具（SaaS/自建系统/管理后台）接入 AI Agent 生态时触发。
---

# tool-skill-creator

内部工具 Skill 化的项目经理 — 引导用户从零完成工具类 Skill 的创建。

## Description

本 Skill 覆盖内部工具 Skill 化的完整生命周期，分为五个阶段：

| 阶段 | 名称 | 产出 |
|------|------|------|
| Phase 1 | 分档决策 | 工具档位（T1/T2/T3/T3-Browser）+ 工具信息卡 |
| Phase 2 | 信息采集 | 根据档位收集必要的技术信息（API 文档、Swagger、页面结构等） |
| Phase 3 | 编写 | SKILL.md + references/（若需要）+ AGENTS.md 条目 |
| Phase 4 | 验证 | 通过 `validate.py` 格式验证 + 质量检查清单 |
| Phase 5 | 迭代优化 | 委托 skill-creator 创建评估用例，反复优化直到满意 |

最终产出物：

- `<tool-name>/SKILL.md` — 工具操作手册
- `<tool-name>/references/` — API 文档（T3/T3-Browser 需要）
- `AGENTS.md` 对应档位表格新增条目

本 Skill 专注于工具 Skill 化的流程管控和信息采集。评估与迭代环节委托给 [skill-creator](https://github.com/anthropics/skills/blob/main/skills/skill-creator/SKILL.md) 执行。

编写规范和模板详见 [内部工具 Skill 化指南](../docs/05-user-guide/tool-skill-guide.md)，本 Skill 不重复其中内容，而是引用并按流程执行。

## Rules

### Rule 1 — Phase 1: 搜索现有 Skill + 分档决策

收到用户的 Skill 化请求后，**先搜索是否有现成 Skill 可用**：

1. 搜索社区 Skill：`npx skills find <工具名>` 或访问 [skills.sh](https://skills.sh)
2. 搜索 ClawHub：`clawhub search <工具名>` 或访问 [clawhub.ai](https://clawhub.ai)
3. 如果找到匹配的 Skill → 引导用户安装（`npx skills add <repo> --skill <name>`）→ 在此基础上定制公司规则即可，跳到 Phase 3
4. 如果没有现成 Skill → 继续分档决策

分档决策树：

```
新工具需要 Skill 化
  └─ 有官方 MCP Server？ ─── 是 ──→ T1
       └─ 否
          └─ 有公开 REST API + 官方文档？ ─── 是 ──→ T2
               └─ 否
                  └─ 有 Swagger/OpenAPI？ ─── 是 ──→ T3
                       └─ 否 ──→ T3-Browser
```

确定档位后，输出工具信息卡并请用户确认：

```
【工具信息卡】
- 工具名称：<name>
- 访问地址：<URL>
- 认证方式：<MCP / Bearer Token / Basic Auth / 浏览器登录>
- 判定档位：<T1 / T2 / T3 / T3-Browser>
- 判定依据：<简述原因>
```

### Rule 2 — Phase 2: 信息采集

#### 通用：询问使用文档

在进入档位路径前，先问用户：**"这个工具有没有使用文档（如飞书文档）？有的话请提供链接。"**

如果有飞书文档，按 [`feishu-channel-rules`](../lark/feishu-channel-rules/SKILL.md)
使用本地策略批准的 profile 执行
`"$APPROVED_NODE" "$APPROVED_LARK_CLI_ENTRY" --profile <approved-profile> docs +fetch --as user --doc <飞书文档URL>`，提取业务规则、
操作流程和注意事项，作为编写 SKILL.md 的重要输入。身份或权限核验失败时停止，不回退到
旧 Lark MCP 或业务应用。

然后根据档位执行对应的信息采集路径。**每个路径都包含实际探索步骤** — API 文档告诉你"怎么调用"，但业务规则（命名约定、数据模型、操作约束、工作流程）只能通过实际使用工具、观察真实数据来发现。

#### 通用：获取认证凭据（所有档位）

大部分内部工具需要 SSO 登录才能访问 API。在探索前必须先帮用户获取 token：

1. **检查环境变量**：先看 `AGENTS.md` 中对应工具的环境变量是否已设置（如 `echo $GRAFANA_TOKEN`）
2. **如果 token 未设置**，通过浏览器 MCP 帮用户登录获取：
   - 使用 agent-browser 打开工具登录页（见 Step 2a）
   - **让用户在浏览器中手动完成 SSO 登录**（AI 不要代替用户输入密码）
   - 登录成功后，AI 通过 `page.evaluate(() => document.cookie)` 获取 Cookie，或从 Network 拦截中提取 Bearer Token
   - 将获取的 token 用于后续 API 探索（注意：token 有时效性，仅用于当次探索，不要硬编码进 Skill）
3. **告知用户**如何获取长期 token（如 Grafana → Settings → Service Accounts → Add Token），写入 Skill 的认证说明

#### T2 路径（有公开 REST API）

1. 确认认证方式（Bearer Token / Basic Auth / API Key）和环境变量命名
2. **获取 token 后，实际调用 API 探索数据**：
   - 调用 list/search 类 GET 端点，观察返回的数据结构和实际内容
   - 发现命名约定（如自定义指标前缀 `addx_`、资源命名格式）
   - 识别数据模型和层级关系（从返回的 JSON 结构中提炼）
   - 发现常用查询模式和过滤条件
3. **不要把 API 端点文档写进 Skill** — 对于有公开文档的工具（Grafana、GitLab、Sentry 等），AI 可以通过 Context7 MCP 或 WebSearch 自行查阅 API 用法
4. 将探索中发现的**公司特有业务规则**总结给用户确认
5. 询问公司特定约定（AI 无法从 API 发现的隐性规则）

#### T3 路径（有 Swagger/OpenAPI）

1. 获取 Swagger/OpenAPI 文档 URL（如 `/swagger/index.html`、`/api-docs`）
2. **通过 agent-browser 登录获取 token**（见上方"通用：获取认证凭据"），然后访问 Swagger URL 了解 API 结构
3. **实际调用 API 探索业务数据**（用获取的 token）：
   - 调用查询端点，观察真实数据的结构和层级
   - 发现业务状态机（资源有哪些状态、状态如何流转）
   - 识别操作的前置条件（如"操作 B 必须在操作 A 之后"）
   - 发现数据约束（如"某字段只能递增"、"已发布的资源不可回退"）
4. SKILL.md 中记录 Swagger URL，让 AI 每次操作时动态查阅。**不要把 Swagger 的 API 端点列表抄进 Skill**
5. 请用户确认常用业务流程和操作红线

#### T3-Browser 路径（无 API 文档的自建系统）

这是最重的路径，需要通过浏览器抓取页面结构和 API。

**Step 2a: 安装 agent-browser**

```bash
npx skills add vercel-labs/agent-browser --skill agent-browser --agent claude-code -y
```

基本操作模式：`agent-browser open <url>` → `agent-browser snapshot -i`（获取 @e 元素引用）→ `agent-browser click/fill @eN`

**Step 2b: 浏览器抓取页面结构**

1. 使用 agent-browser 打开目标系统 → 引导用户登录
2. 登录后用 `page.evaluate()` 获取侧边栏/导航栏 HTML
3. 根据 class 名前缀识别 UI 框架：

| 前缀 | 框架 | 菜单项选择器 | 子菜单标题选择器 |
|------|------|------------|--------------|
| `.arco-` | Arco Design | `.arco-menu-item` | `.arco-menu-inline-header` |
| `.el-` | Element UI | `.el-menu-item` | `.el-submenu__title` |
| `.ant-` | Ant Design | `.ant-menu-item` | `.ant-menu-submenu-title` |

**Step 2c: 自动抓取脚本**

编写自动遍历脚本：自动展开所有菜单 → 逐一点击菜单项 → 拦截 XHR/Fetch 请求 → 记录 API 端点 → 生成 `references/api-reference.md`。

**Step 2d: 常见坑点**（务必提前规避）

1. **agent-browser 未安装**: 先运行 `npx skills add vercel-labs/agent-browser --skill agent-browser --agent claude-code -y`
2. **菜单选择器匹配失败**: 先 `agent-browser snapshot -i` 获取 sidebar HTML 分析 class 名，不要猜框架
3. **SPA 路由导航失败**: 从登录后 URL 确认路由模式（hash `/#/` vs history `/path/`）
4. **子菜单折叠**: 很多组件子菜单默认 `display:none`，需先 click header 展开
5. **DOM 动态变化**: 每次点击后重新查询选择器，不要缓存旧 DOM 引用

### Rule 3 — Phase 3: 编写

#### 选择模板

根据档位选择对应模板（模板详见 [tool-skill-guide.md 第 5 章](../docs/05-user-guide/tool-skill-guide.md#5-工具类-skill-编写规范)）：

| 档位 | 模板 | Skill 厚度 |
|------|------|-----------|
| T1 | T1 模板（有官方 MCP） | < 100 行 |
| T2 | T2 模板（有公开 REST API） | < 150 行 |
| T3 | T3 模板（自建系统） | < 200 行 |
| T3-Browser | T3-Browser 模板 | < 300 行 |

#### Frontmatter 规则

```yaml
---
name: <tool-name>        # 必须与目录名一致，小写字母+数字+连字符
description: <做什么 + 何时触发>  # 中文，一句话说清用途和触发条件
---
```

#### 核心原则：只写 AI 查不到的内容

Skill 的价值在于**公司特有的业务规则**，而非 API 文档的重复。对于 Grafana、Prometheus、GitLab、Sentry 等有公开文档的工具，AI 可以通过 Context7 MCP 或 WebSearch 自行查阅 API 用法，不需要写在 Skill 里。

| 要写进 Skill | 不要写进 Skill |
|-------------|--------------|
| 内部 URL、环境变量、认证方式 | API 端点列表、curl 示例、参数说明 |
| 公司命名约定（Dashboard 命名格式、标签体系） | REST API 调用语法、HTTP 方法 |
| 操作红线（禁止删除生产资源、修改前需确认） | 官方文档已有的使用教程 |
| 业务流程（工单流转、审批链路、状态机） | PromQL/SQL 语法参考 |
| 通过探索发现的隐性规则（阈值约定、ID 映射表） | 分页参数、过滤条件等通用模式 |

简单判断：**如果这条信息在官方文档或 Google 能找到，就不要写进 Skill。** 只写入从内部系统探索中发现的、外部无法获取的公司特有知识。

AI 查阅文档的优先级：**Context7 MCP**（`resolve-library-id` → `get-library-docs`）> WebSearch > Skill 中手动记录。

Skill 中**不要放静态文档链接**（如 `https://grafana.com/docs/...`），而是写"API 用法通过 Context7 MCP 查询"。静态链接会过时，Context7 始终返回最新版本。

#### 必需章节

每个 SKILL.md 必须包含以下章节（验证脚本会检查）：

- `## Description` — 工具概述、适用场景
- `## Rules` — 公司特有的业务规则和操作流程（不是 API 端点列表）

#### 创建 references/

T3-Browser 档位需要创建 `references/` 目录存放抓取的 API 文档。T3 档位不需要下载 Swagger，在 SKILL.md 中记录 Swagger URL 即可（API 会更新，避免过时）：

```
# T3（记录 Swagger URL，不下载）
<tool-name>/
└── SKILL.md              # 内含 Swagger URL，AI 动态访问

# T3-Browser（需要 references/）
<tool-name>/
├── SKILL.md
└── references/
    └── api-reference.md   # 浏览器抓取生成的 API 文档
```

#### 更新 AGENTS.md（必须）

Skill 创建完成后，**必须**更新仓库根目录的 `AGENTS.md`：

1. 在对应档位表格的 **Skill 列**填入 Skill 名称（如 `tracker-manager`）
2. 如果工具尚未在表格中，新增一行（含 URL、认证方式、Skill 名）
3. 对应档位：T2 → `## T2: 有公开 REST API`，T3 → `## T3: 自建系统`，T3-Browser → `## T3-Browser`

示例：将 `tracker-manager` Skill 注册到 AGENTS.md：
```
| 埋点平台 | `https://us-analytics-management.theunismart.com` | 无（需逆向） | ... | `tracker-manager` |
```

### Rule 4 — Phase 4: 验证

#### 格式验证

运行验证脚本，必须 0 errors：

```bash
uv run python scripts/validate.py --skill <tool-name>
```

常见错误及修复：

| 错误信息 | 修复方式 |
|---------|---------|
| 缺少 YAML frontmatter | 添加 `---` 包裹的 name + description |
| frontmatter.name 必须与父目录名一致 | 确保 name 字段和目录名相同 |
| 缺少必需章节 | 添加 `## Description` / `## Rules` |

#### 质量检查

按档位执行对应的质量检查清单（清单详见 [tool-skill-guide.md 第 8 章](../docs/05-user-guide/tool-skill-guide.md#8-质量检查清单)）：

- 通用项：name 与目录一致、description 包含做什么+何时触发、**已通过实际探索发现并记录了业务规则**
- T1 专项：MCP 安装方式已说明、已通过 MCP 探索发现命名/标签约定、行数 < 100
- T2 专项：认证方式已说明、已通过 API 探索发现数据模型和业务约束、行数 < 150
- T3 专项：Swagger URL 已记录、已通过 API 探索发现操作流程和数据约束、行数 < 200
- T3-Browser 专项：UI 框架已识别、菜单结构已记录、API 文档已放入 references/、行数 < 300

#### 功能验证

格式和质量检查通过后，实际使用 Skill 对目标工具执行 1-2 个查询操作，验证 Skill 中的操作流程能正常工作：

- **仅执行只读/查询操作**（GET 请求、页面浏览、数据查看），**禁止任何修改、创建或删除操作**
- T1：通过 MCP 执行一次查询（如查看 dashboard 列表）
- T2：用 curl 调用一个 GET 端点（如查询指标列表）
- T3：访问 Swagger URL 确认可达，调用一个 GET 端点
- T3-Browser：通过浏览器 MCP 登录并导航到一个页面，确认菜单结构和选择器正确
- 验证结果与 Skill 中描述的操作步骤一致，如有偏差则修正 SKILL.md

### Rule 5 — Phase 5: 迭代优化

验证通过后，委托 skill-creator 进行评估与优化：

1. **创建测试用例**：根据 Skill 的业务场景，编写 2-3 个真实的测试 Prompt，保存到 `evals/evals.json`（该格式由 skill-creator 定义）
2. **对比评估**：运行 with-skill vs without-skill 对比，确认 Skill 确实带来了提升
3. **用户评审**：请用户检查生成结果，收集反馈
4. **优化迭代**：根据反馈修改 SKILL.md，重新运行验证和评估
5. **重复**：直到用户满意为止

评估与迭代的具体方法由 [skill-creator](https://github.com/anthropics/skills/blob/main/skills/skill-creator/SKILL.md) 提供。

## Examples

### Bad

```
用户："把 Zendesk 做成 Skill"
AI：（跳过分档，直接写 SKILL.md）→ 猜错认证方式、写了 T2 不需要的 API 文档、未更新 AGENTS.md
```

```
用户："把运营后台做成 Skill"
AI：（跳过 UI 框架识别，猜测用 .el-menu-item）→ 实际是 Ant Design，选择器全部失败
```

### Good

```
用户："把 Zendesk 做成 Skill"
AI：先搜索 npx skills find zendesk → 发现社区已有 zendesk-query Skill → 引导安装 → 在此基础上添加公司约定
```

```
用户："把运营后台做成 Skill"
AI：Phase 1 判定 T3-Browser → Phase 2 安装 agent-browser → snapshot -i 识别 Ant Design → 自动遍历 45 菜单项 → Phase 3 写 SKILL.md + api-reference.md → Phase 4 validate.py 通过 → Phase 5 委托 skill-creator 迭代
```

## References

- [内部工具 Skill 化指南](../docs/05-user-guide/tool-skill-guide.md) — 分档策略、编写模板、质量检查清单
- [skill-creator](https://github.com/anthropics/skills/blob/main/skills/skill-creator/SKILL.md) — Anthropic 官方 Skill 创建工具（Phase 5 评估与迭代）
- [AGENTS.md 模板](../AGENTS.md) — 全公司内部工具清单
- [addx-console-admin 实战示例](../addx-console-admin/SKILL.md) — T3-Browser 档位的完整实战参考
