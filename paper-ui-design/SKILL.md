---
name: paper-ui-design
description: 基于 User Story 与设计系统 Token，调用 Paper MCP 在新 Page 上增量设计 UI。当用户说"用 Paper 设计 XX 界面"、"调用 Paper 做 UI"、"根据 US 出设计稿"时触发。适用于移动端页面、底部弹框、详情页等任意 UI 设计任务。
---

# paper-ui-design

通过 Paper MCP 将 User Story 转化为可交付设计稿。Token 规范见 [ds_token.md](ds_token.md)，组件命名约定见 [references/naming.md](references/naming.md)，MCP 工具调用规范见 [references/mcp-api.md](references/mcp-api.md)。

## Description

两个阶段：**阅读**（读 US + Token，扫描组件库）→ **构建**（增量 write_html，截图 Review）。

## Rules

### 强制前置步骤

1. **静默读取 Token**：每次启动先读 `ds_token.md`，提取色板、字体、间距、Radius。文件不存在则停止并提示用户提供。
2. **扫描组件库（不可跳过）**：设计前必须读取用户当前打开的 Paper 组件库 Page，对所有 `[DS]` 前缀 Artboard 截图并汇报盘点表。若用户未打开 Paper，提示其完成后输入 Y 再继续。用户输入 N 时重新提示，此步骤无法跳过。
3. **Design Brief**：开始 `write_html` 前必须输出设计摘要（色板、字体、间距节奏、视觉方向一句话）。

### 构建规则

- **每次 `write_html` 只写一个视觉分组**（header / 卡片 shell / 按钮组 / 列表行）。禁止一次写入整个页面。
- **创建 Artboard 后立即设置 `display:flex, flexDirection:column`**，否则子元素全部堆叠在左上角。
- **每 2-3 次 `write_html` 必须截图**，按 Spacing / Typography / Contrast / Alignment / Clipping / Repetition 六项评估，发现问题立即修复。
- **UI 文案默认英文**，无论用户以何种语言描述需求。
- **优先复用**：能用 `x-paper-clone` 或 `duplicate_nodes` 复用已有组件时不重新写 HTML。
- **完成后调用 `finish_working_on_nodes`** 释放工作锁。

### DS Token 对齐

颜色、字号、圆角、间距必须与 `ds_token.md` 定义值对齐。检测到非标值（如未定义的 hex 或 `13px` 间距）时询问：「检测到非标设计，是否对齐现有 Token 或更新文档？」

### Page 管理

- 设计稿在组件库文件内新建独立 Page，命名：`<FeatureName>`（如 `Login`、`PostCard`）。
- 禁止在组件库 Page 内直接新增设计稿。

## Workflow

```
[静默] 读取 ds_token.md

Phase 1 — 准备
  Step 1：需求确认
    → 飞书链接：按 feishu-channel-rules 使用已核验的 lark-cli user profile 读取
    → 粘贴文本 / 口述：整理后输出需求概要，自动继续
  Step 2：引导用户打开 Paper 组件库 → 等待 Y（不可跳过）
  Step 3：get_basic_info → 逐一 get_screenshot → 输出组件盘点表
  Step 4：输出 Design Brief → 引导新建 Page → 等待用户切换

Phase 2 — 构建
  Step 5：get_basic_info 确认 Page → create_artboard → update_styles(flex column)
  Step 6：按层级增量 write_html（每组一次调用）
  Step 7：每 2-3 步 get_screenshot → Review → 修复
  Step 8：finish_working_on_nodes → 汇报 US 覆盖情况
```

## Examples

### ✅ Good

```
需求：登录页（Logo + 用户名 + 密码 + 登录按钮 + 协议）

create_artboard → update_styles(flex column)      ← 必须！
write_html → Brand 区域（Logo + 标题）
write_html → Form（用户名 + 密码输入框）
get_screenshot → Review → Clipping ⚠ → 修复
write_html → 登录按钮
write_html → 协议勾选行
get_screenshot → Review → 全部通过 ✓
finish_working_on_nodes
```

### ❌ Bad

```
# 错误 1：一次写入整个页面（禁止）
write_html → 完整 HTML 含 Logo + 表单 + 按钮 + 协议（100+ 行）

# 错误 2：忘记设置 flex column（导致子元素全部堆叠）
create_artboard → 直接 write_html（未调用 update_styles）

# 错误 3：跳过组件库扫描（禁止）
用户输入 N → AI 跳过扫描直接开始设计
```

## References

- [ds_token.md](ds_token.md) — 设计系统 Token（色板 / 字体 / 间距 / Radius），用户可编辑
- [references/naming.md](references/naming.md) — Paper 组件库 Artboard 与 Layer 命名规范
- [references/mcp-api.md](references/mcp-api.md) — Paper MCP 工具正确调用签名与常见错误
