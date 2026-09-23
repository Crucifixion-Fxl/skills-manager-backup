# 翻译规则和约定

## 支持的语言

| 语言代码 | 语言名称 |
|----------|----------|
| `en-us`  | English (United States) |
| `zh-cn`  | Chinese (Simplified) |
| `zh-tw`  | Chinese (Traditional) |
| `fr`     | French |
| `de`     | German |
| `it`     | Italian |
| `ja`     | Japanese |
| `ko`     | Korean |
| `ru`     | Russian |
| `es`     | Spanish |
| `fi`     | Finnish |
| `cs`     | Czech |
| `pl`     | Polish |
| `pt`     | Portuguese |
| `pt-br`  | Portuguese (Brazil) |
| `he`     | Hebrew |
| `vi`     | Vietnamese |
| `ar`     | Arabic |
| `tr`     | Turkish |
| `id`     | Indonesian |
| `th`     | Thai |
| `fil`    | Filipino |
| `ms`     | Malay |

## 输出格式规范

翻译结果必须是**严格的 JSON**，格式如下：

```json
{ "title": "翻译后的标题", "content": "翻译后的 HTML 内容" }
```

**强制约束**：
- 仅输出上述 JSON，不得有任何其他内容
- 不得添加 Markdown 代码块（如 ` ```json ` 包裹）
- 不得在 JSON 前后附加说明、注释或标识符（如 "json"、"JSON"）

## HTML 处理规则

- 保持所有 HTML 标签、属性完整不变
- 不修改任何 URL、超链接、图片 src
- 不翻译技术术语、产品名称、代码片段
- 只翻译 HTML 标签之间的**可读文本内容**

## 质量标准

- 译文语言自然，符合目标语言表达习惯，避免直译生硬
- 技术术语保持一致（同一文章内同一术语用同一译法）
- 产品名称、UI 元素名称不翻译，保留原文
