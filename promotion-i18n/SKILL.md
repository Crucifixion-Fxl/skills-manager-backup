---
name: promotion-i18n
description: 基于 figma-i18n 通用流程，将 Figma promotion 设计稿翻译为多语言版本，导出压缩图片并上传到 Marketing CMS。当用户需要为 popup/banner/video 素材做多语言适配并上传 CMS、或提到"promotion 翻译"、"多语言图片"、"promotion i18n"时触发。
---

# Promotion I18n

将 Figma 英文 promotion 设计稿 → 多语言翻译（figma-i18n）→ 导出压缩 → 上传 CMS。

**基于 `figma-i18n` 通用流程**，额外处理导出、压缩和 CMS 上传。

## 输入

| 参数 | 必填 | 说明 |
|------|------|------|
| Figma frame URL 或 nodeId | ✅ | 英文版设计稿 frame |
| CMS promotion key | ✅ | 要更新的 promotion（如 `popup_vip_direct_monthly_a`） |
| CMS Token | ✅ | 用户提供 Bearer Token |
| 目标语言 | 可选 | 默认 `en, de, fr, pt, es, it` |
| Crowdin Glossary ID | 可选 | 默认 `583582`（VicoHome Product Glossary） |

## 执行流程

### Step 1-5: 按 `figma-i18n` skill 执行

执行 figma-i18n 的完整流程（提取文案 → 术语匹配 → 翻译 → 克隆多语言 frame → 溢出检查），确保所有语言 frame 无溢出后进入下一步。

### Step 6: 导出 @2x PNG

用 Figma REST API 导出（不用 Plugin API，因为 Plugin 沙盒无 fetch）：

```bash
FIGMA_TOKEN=$(python3 -c "import json; d=json.load(open('$HOME/.claude.json')); print(d['mcpServers']['figma']['headers']['X-Figma-Token'])")

# 批量导出所有语言 frame
curl -s -H "X-Figma-Token: $FIGMA_TOKEN" \
  "https://api.figma.com/v1/images/{fileKey}?ids={node1},{node2},...&format=png&scale=2"
```

下载到 `/tmp/promotion_i18n/{promotionKey}_{locale}.png`。

### Step 7: 压缩

按 image-compress skill 的工具优先级压缩：

```bash
# pngquant（首选）
pngquant --quality=65-85 --output {output} {input}

# sharp-cli（备选）
cd /tmp && npx --yes sharp-cli -i {input} -o {output} --optimise --effort 6

# 目标：每张 ≤ 200KB
```

### Step 8: 上传 CMS + 更新 promotion

```bash
BASE_URL="https://marketing-cms-staging-us.addx.live"

# 1. 上传图片到 media
curl --globoff -s -X POST \
  -H "Authorization: Bearer ${TOKEN}" \
  -F "file=@{file_path}" \
  -F "_payload={\"alt\":\"${promotionKey}_${locale}\"}" \
  "$BASE_URL/api/media"

# 2. 更新 promotion 对应 locale 的图片
curl --globoff -s -X PATCH \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  "$BASE_URL/api/promotions/${PROMO_ID}?locale=${locale}" \
  -d '{"components":[{"id":"${BLOCK_ID}","blockType":"imageCard","image":"${MEDIA_ID}","actionLink":"...","dismissible":true,"skipNoThanks":false}],"_status":"published"}'

# ⚠️ BLOCK_ID 必须先通过 GET 获取！不带 id 会导致 image=None，清空所有 locale 的图片关联
```

**Locale 策略**：
- en, de, fr, pt, es, it → 各自翻译版图片
- 其余 12 个 locale → 用 en 英文图兜底

## Examples

### Bad

```bash
# PATCH promotion 不带 block id — 导致所有 locale 图片关联丢失
curl -X PATCH "$BASE_URL/api/promotions/123?locale=de" \
  -d '{"components":[{"blockType":"imageCard","image":"456"}]}'
# → image=None，en/de/fr/... 所有语言图片全部丢失 🚨

# 用 Plugin API 导出图片 — 沙盒无 fetch，无法下载
const bytes = await node.exportAsync({ format: "PNG", constraint: { type: "SCALE", value: 2 } });
// → 只能获取 Uint8Array，无法保存到本地文件系统
```

### Good

```bash
# 先 GET 获取 block id，再 PATCH 带上 id
BLOCK_ID=$(curl -s "$BASE_URL/api/promotions/123?locale=en" | jq -r '.components[0].id')
curl -X PATCH "$BASE_URL/api/promotions/123?locale=de" \
  -d "{\"components\":[{\"id\":\"$BLOCK_ID\",\"blockType\":\"imageCard\",\"image\":\"456\"}]}"
# → 只更新 de 的图片，其他 locale 不受影响 ✅

# 用 Figma REST API 导出（不受插件沙盒限制）
curl -s -H "X-Figma-Token: $TOKEN" \
  "https://api.figma.com/v1/images/{fileKey}?ids={nodeId}&format=png&scale=2"
# → 返回 CDN URL，可直接 curl 下载
```

## 操作红线

- 不删除 CMS 已有的 promotion 或 media
- 上传 CMS 前先确认 promotion key 和 action link 正确
- **🚨 PATCH promotion 必须带 block `id`（血泪教训，两次翻车）**：
  1. 先 `GET /api/promotions/{id}?locale=en` 获取 `components[0].id`（即 block id）
  2. PATCH 时 body 中 `components[].id` 必须填入该值
  3. 不带 id → image 字段写入 None → 所有 locale（含 en）图片关联全部丢失
  4. 修复后必须验证 en locale 的图片是否正常
- **图片格式统一 PNG**：导出 `format=png&scale=2`，压缩用 pngquant 或 sharp-cli，目标按用户要求
