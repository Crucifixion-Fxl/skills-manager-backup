# Design System Tokens & Contract

**维护者:** @向旭良
**目标框架:** Flutter
**同步源:** Paper Project [链接]
**版本:** v1.0.0

---

## 0. 环境准备 (Prerequisites)

在使用本设计系统与 Paper MCP 进行 AI 辅助设计前，必须完成以下三步环境配置。

### 0.1 安装 Paper 客户端

Paper 是本设计系统的配套设计工具，所有组件库和设计稿均托管于此。

1. 前往 [paper.design](https://paper.design) 下载对应平台的客户端（macOS / Windows）
2. 安装完成后，使用团队账号登录
3. 在 Paper 中打开公司组件库文件（见 0.3 节配置）

### 0.2 安装 Paper MCP 服务

MCP（Model Context Protocol）服务使 Claude 能够直接读写 Paper 画布，实现 AI 辅助设计。

在 Claude Code 的 MCP 配置文件（`~/.claude/mcp.json` 或项目级 `.mcp.json`）中添加：

```json
{
  "mcpServers": {
    "paper": {
      "command": "npx",
      "args": ["-y", "@paper-design/mcp"]
    }
  }
}
```

配置完成后重启 Claude Code，确认工具列表中出现 `mcp__paper__*` 系列工具即为成功。

> **验证方法**：在对话中说"调用 Paper get_basic_info"，如返回当前文件信息则配置正确。

### 0.3 配置公司组件库

将以下组件库文件添加到 Paper 的"共享文件"或团队工作区，确保所有成员可访问：

| 文件名 | 用途 | 包含内容 |
|---|---|---|
| `组件库` | 设计系统主文件 | Buttons / Dialogs / Bottom Picker / Icons / Text Fields / Container |

**组件库 Page 清单**（供 AI 扫描时参考）：

| Page 名称 | 内容 |
|---|---|
| `[DS]Kiwibit` | 全量组件库（主 Page，AI 每次设计前必须扫描此 Page） |

**新建设计稿规范**：
- 每个功能模块在组件库文件内新建独立 Page，命名格式：`<FeatureName>`（如 `PostCard`、`Explore`）
- 禁止在 `[DS]Kiwibit` Page 内直接新增设计稿，保持组件库的纯净性

---

## 1. 颜色规范 (Color Palette)

AI 在读取 Paper 设计稿时，如遇到符合以下 Hex 值的填充或描边，必须自动映射为对应的 Flutter 变量。

| 语义名称 (Token) | Hex Value | Flutter 变量引用 | 适用场景说明 |
|---|---|---|---|
| color-primary | #0CC97A | AppColors.primary | 主按钮、高亮状态、交互反馈 |
| color-success | #2AD584 | AppColors.success | 成功提示、正向引导 |
| color-error | #BA1A1A | AppColors.error | 报错、危险操作、删除警告 |
| color-text-main | #181D19 | AppColors.textMain | 正文、主要标题、导航文字 |
| color-text-sub | #797B79 | AppColors.textSub | 辅助说明、占位符 (Placeholder) |
| color-bg-base | #F5F8F5 | AppColors.bgBase | 页面全局背景色 |
| color-border | #E5EAE2 | AppColors.border | 分割线、描边、容器边框 |

---

## 2. 字体与排版 (Typography)

AI 应识别 Paper 中的文本属性（字号、字重），并自动映射为项目的 TextStyle。

| 等级 (Level) | Size (px) | Weight | Line Height | Flutter TextStyle |
|---|---|---|---|---|
| text-h1 | 24 | Bold (700) | 1.2 | AppTextStyles.h1 |
| text-h2 | 20 | SemiBold (600) | 1.3 | AppTextStyles.h2 |
| text-body | 16 | Regular (400) | 1.5 | AppTextStyles.body |
| text-caption | 12 | Light (300) | 1.4 | AppTextStyles.caption |

---

## 3. 间距系统 (Spacing System)

核心逻辑：所有布局间距必须基于 4px 步长。AI 需对 Paper 中的图层间距进行"就近取整"映射。

| 级别 (Token) | 数值 (dp/px) | Flutter 常量引用 | 典型用途建议 |
|---|---|---|---|
| space-xs | 4 | AppSpacing.xs | 元素内极小间距（如 Icon 与文字） |
| space-s | 8 | AppSpacing.s | 组件内子元素间距（如 标题与描述） |
| space-m | 16 | AppSpacing.m | 标准页边距 / 卡片内边距 / 全局缩进 |
| space-l | 24 | AppSpacing.l | 模块间的物理分割、大段落间距 |
| space-xl | 32 | AppSpacing.xl | 页面顶部/底部安全留白、大容器间距 |

---

## 4. 圆角与投影 (Shape & Elevation)

定义 UI 的物理质感和深度。

| 属性类型 | Token 名称 | 数值 | Flutter 实现代码 / 变量 |
|---|---|---|---|
| 圆角 (Radius) | radius-s | 4px | BorderRadius.circular(4) |
| | radius-m | 12px | BorderRadius.circular(12) |
| | radius-full | 999px | StadiumBorder() 或 BoxShape.circle |
| 投影 (Shadow) | shadow-low | Y:2, B:4, O:0.1 | AppShadows.low |
| | shadow-high | Y:8, B:16, O:0.15 | AppShadows.high |

---

## 5. AI 执行指令 (Agent Instructions)

请 Claude (via MCP) 严格遵守以下开发准则：

1. **自动对齐 (Auto-Alignment)**：当 Paper 中的测量值与上述 Token 误差在合理范围内时，必须强制使用 Token 变量，严禁在代码中输出 Hardcode 数字。
2. **偏差预警 (Deviation Warning)**：若检测到设计师在 Paper 中使用了未在此文档定义的颜色或非标间距（如 13px），AI 需询问："*检测到非标设计，是否需要对齐现有 Token 或更新此文档？*"
3. **语义映射 (Semantic Mapping)**：AI 编写 UI 时应理解 Layer 的命名语义。例如：名为 `Button/Primary` 的图层应自动检查 `AppColors.primary` 是否已应用。
4. **组件复用 (Component First)**：在实现 UI 布局前，优先检索 `lib/widgets/common/` 下的已有代码，确保基础样式已通过原子组件封装。
