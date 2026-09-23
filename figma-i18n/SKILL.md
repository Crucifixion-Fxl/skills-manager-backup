---
name: figma-i18n
description: 将 Figma 设计稿自动翻译为多语言版本，优先匹配 Crowdin Glossary 已有术语。当用户需要翻译 Figma 设计稿、多语言素材适配、或提到"Figma 翻译"、"多语言设计稿"、"figma i18n"、"翻译设计稿"时触发。
---

# Figma I18n

将 Figma 英文设计稿 → 术语匹配 → 翻译 → 克隆多语言 frame → 溢出检查。

## Crowdin Glossary 映射

不同产品线使用不同的 Glossary，根据上下文自动选择：

| 产品线 | Crowdin Project | Project ID | Glossary ID | Glossary 名称 |
|--------|----------------|-----------|-------------|--------------|
| VicoHome (通用) | app_oem | 883524 | **583582** | VicoHome Product Glossary |
| app_oem 专属 | app_oem | 883524 | 680066 | app_oem's Glossary |

> 用户未指定 Glossary ID 时，默认使用 **583582**（VicoHome Product Glossary，含 13 个产品功能术语 × 18 种语言）。

## 输入

| 参数 | 必填 | 说明 |
|------|------|------|
| Figma frame URL 或 nodeId | ✅ | 英文版设计稿 frame |
| 目标语言 | 可选 | 默认 `en, de, fr, pt, es, it` |
| Crowdin Glossary ID | 可选 | 默认 `583582`（VicoHome Product Glossary） |

## 输出

- Figma 内：每种语言一个克隆 frame（命名 `{原名}_{locale}`），已完成溢出检查

## 执行流程

### Step 1: 提取英文文案

用 `mcp__figma__use_figma` 遍历 frame 中所有 TEXT 节点：

```javascript
// Figma Plugin API
const frame = figma.root.findOne(n => n.id === "{nodeId}");
const texts = frame.findAll(n => n.type === "TEXT");
return texts.map(t => ({
  id: t.id,
  name: t.name,
  characters: t.characters,
  fontSize: t.fontSize,
  fontName: JSON.stringify(t.fontName)
}));
```

输出文案清单，让用户确认哪些需要翻译（排除 "9:41"、"SAMPLE" 等不翻译的文案）。

### Step 2: 术语匹配（Crowdin Glossary）

> 如果用户未提供 Glossary ID，跳过此步骤，直接进入 Step 3。

从 Crowdin Glossary API 拉取术语表，与 Step 1 提取的文案做匹配：

```bash
# 拉取 glossary 所有 terms（token 内联，不用环境变量）
curl -s -H "Authorization: Bearer {CROWDIN_TOKEN}" \
  "https://api.crowdin.com/api/v2/glossaries/{glossaryId}/terms?limit=500"
```

**匹配策略**：
1. **Exact match**：文案完全等于某个 glossary term（如 "AI Event Descriptions"）
2. **Substring match**：文案包含某个 glossary term（如 "Try AI Event Descriptions Free" 包含 "AI Event Descriptions"）
3. 匹配到的术语直接用 Crowdin 已有翻译，不二次翻译

输出匹配结果表：

| 文案 | 匹配术语 | 来源 |
|------|---------|------|
| AI Event Descriptions | AI Event Descriptions | Crowdin Glossary |
| Try Free for 7 Days | — | 需 Claude 翻译 |
| Security Level | Security Level | Crowdin Glossary |

### Step 3: 翻译

Claude 翻译未匹配的文案，合并 Glossary 已有翻译，输出完整对照表让用户确认：

| 原文 | 来源 | de | fr | pt | es | it |
|------|------|----|----|----|----|-----|
| AI Event Descriptions | Glossary | AI-Ereignis... | Descriptions... | ... | ... | ... |
| Try Free for 7 Days | Claude | ... | ... | ... | ... | ... |

**翻译约束**：
- CTA 按钮文案长度 ≤ 英文 × 1.3（避免溢出）
- 保持品牌 tone：专业但不冰冷
- App 内 UI 文案风格，简洁直接
- Glossary 术语翻译原样使用，不修改

### Step 4: 生成多语言 Figma frame

用 `mcp__figma__use_figma` 克隆并替换文案：

```javascript
// 对每种语言
const original = figma.root.findOne(n => n.id === "{nodeId}");
await figma.loadFontAsync({ family: "SF Pro", style: "Bold" });
await figma.loadFontAsync({ family: "SF Pro", style: "Semibold" });
await figma.loadFontAsync({ family: "SF Pro", style: "Regular" });
await figma.loadFontAsync({ family: "SF Pro", style: "Medium" });

const clone = original.clone();
clone.name = `${original.name}_${locale}`;

// 替换文案 — 用 characters 内容匹配，不用 ID（克隆后 ID 会变）
const textNodes = clone.findAll(n => n.type === "TEXT");
for (const node of textNodes) {
  if (translations[node.characters]) {
    node.characters = translations[node.characters];
  }
}
```

**注意**：克隆后子节点 ID 会变。用 `characters` 内容匹配 TEXT 节点，不要用 ID。

幂等处理：先查找同名 frame，存在则删除再重建。

### Step 5: 溢出检查

克隆完成后，用 `mcp__figma__get_screenshot` 对每个语言 frame 截图，逐一检查：

1. **文案是否溢出容器**：文字被截断、超出按钮/卡片边界
2. **排版是否错乱**：换行位置不合理、文字重叠
3. **CTA 按钮**：重点关注按钮文案，德语/法语等语言普遍比英语长 20-40%

**发现溢出时**：
- 列出溢出的 locale + 具体文案 + 截图
- 提供两种修复选项让用户选择：
  - **A. 缩短翻译文案**：在保持含义的前提下精简译文
  - **B. 调整 Figma UI**：缩小字号 / 扩展容器宽度 / 改为多行
- 用户确认修复方案后，执行修改并重新截图验证
- 循环直到所有 locale 无溢出

## Examples

### Bad

```javascript
// 用克隆后的 ID 匹配文案 — ID 已变，匹配不到
const node = clone.findOne(n => n.id === "123:456");
node.characters = translations["Try Free"];
// → 报错: Cannot read property 'characters' of null

// 不加载字体就修改文案 — Figma 报错
const text = clone.findAll(n => n.type === "TEXT")[0];
text.characters = "Kostenlos testen";
// → Error: Cannot write to node without loading font first

// 直接翻译 Glossary 术语，忽略已有翻译
// "AI Event Descriptions" → Claude 翻译为 "KI-Ereignisbeschreibungen"
// 但 Crowdin Glossary 已有标准译法 "AI-Ereignisbeschreibungen"
```

### Good

```javascript
// 用 characters 内容匹配（克隆后 ID 变化，内容不变）
const textNodes = clone.findAll(n => n.type === "TEXT");
for (const node of textNodes) {
  if (translations[node.characters]) {
    await figma.loadFontAsync(node.fontName);  // 先加载字体
    node.characters = translations[node.characters];
  }
}

// Glossary 术语直接用 Crowdin 已有翻译，不二次翻译
// "AI Event Descriptions" → Crowdin Glossary: "AI-Ereignisbeschreibungen" ✅
```

## 关键约束

1. **不修改原始英文 frame**，只克隆
2. **字体加载**：改文案前必须 `loadFontAsync`，否则 Figma 报错
3. **克隆后 ID 变化**：用 `characters` 内容匹配 TEXT 节点，不用 ID
4. **Crowdin Token**：内联到命令中，不用环境变量传递

## 操作红线

- 不删除 Figma 中任何已有 frame
- 不修改 Figma 原始英文 frame
- 翻译结果必须用户确认后再写入 Figma
- Glossary 术语翻译不可擅自修改，若觉得不准确需告知用户而非自行替换
- 溢出检查必须截图验证，不能只靠字符数估算
