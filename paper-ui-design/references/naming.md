# Paper 组件库命名规范

AI 扫描组件库时依赖以下命名约定识别可复用组件。

## Artboard 层（组件类型）

格式：`[DS]<ComponentName>`

| 示例 | 说明 |
|------|------|
| `[DS]Buttons` | 按钮集合 |
| `[DS]Dialogs` | 弹窗集合 |
| `[DS]Text Fields` | 输入框集合 |
| `[DS]Icons` | 图标集合 |
| `[DS]Container` | 卡片/容器集合 |
| `[DS]Bottom Picker` | 底部选择器集合 |

> `[DS]` 前缀是 AI 识别"可复用组件"的唯一标志，缺少此前缀的 Artboard 扫描时跳过。

## Layer 层（变体命名）

格式：`<Type>/<State>` 或 `<Type>/<Size>/<State>`

| 示例 | 说明 |
|------|------|
| `Primary/Regular` | 主色按钮 · 默认态 |
| `Primary/Disabled` | 主色按钮 · 禁用态 |
| `Secondary/Regular` | 次要按钮 · 默认态 |
| `Outlined/Small/Regular` | 描边 · 小尺寸 · 默认态 |
| `Text/Regular` | 文字按钮 · 默认态 |
| `Icon/Filled` | 填充图标变体 |

## 盘点表输出格式

```
**组件库盘点 — <文件名>（<Page名>）**

| Artboard | 内容摘要 |
|----------|----------|
| [DS]Buttons | Primary/Secondary/Outlined/Text × Regular/Medium/Small，pill 形 |
| [DS]Icons   | 8 个线性图标：more / app-logo / share / close / edit / link / user / check |

**可复用组件**：<node ID 和用途>
```
