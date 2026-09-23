# Promotion 组件详解

Promotion 是轻量级的触达点素材，用于 popup 弹窗、banner 横幅、video 状态条等场景。核心是一张图片 + 一个跳转链接。

## Promotion 顶层字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `key` | string | Yes | 唯一标识符，被 GrowthBook variation value 引用 |
| `components` | array | Yes | 组件数组（通常只有 1 个 imageCard） |
| `_status` | enum | Auto | `draft` / `published` |

## imageCard 组件

| 字段 | 类型 | 说明 |
|------|------|------|
| `blockType` | string | 固定为 `"imageCard"` |
| `image` | media ID (string) | i18n 字段，每个 locale 可设不同图片。通用图片需写入所有 locale（见 SKILL.md Locale 规则） |
| `actionLink` | string | 点击后的 deep link（见下方格式表） |
| `dismissible` | boolean | 是否可关闭。关闭后该 experimentKey 下永不再展示 |
| `skipNoThanks` | boolean | `true` = 关闭后允许再次展示（不调 noThanks 接口）。用于需要重复曝光的素材（如试用期提醒）。默认 `false` |

### 创建前必须确认的配置项

创建 promotion 时，主动向用户确认以下配置（不要自行假设默认值）：

| 配置项 | 问法 | 为什么重要 |
|--------|------|-----------|
| `dismissible` | "用户可以关闭这个弹窗吗？" | `false` 时没有关闭按钮，用户必须点击或等待消失 |
| `skipNoThanks` | "关闭后还需要再次展示吗？" | `false`（默认）= 关了就永远不再展示该用户；`true` = 下次还会弹出。试用期提醒等需要反复触达的场景应选 `true` |
| `actionLink` | "点击后跳转到哪？"（提供下方格式表供选择） | 决定用户点击后的行为 |

### 创建示例

```json
{
  "key": "popup_vip_trial_a",
  "components": [{
    "blockType": "imageCard",
    "image": "media_id_here",
    "actionLink": "smart-camera://payment/free_license_page?source=home_promo_popup",
    "dismissible": true
  }],
  "_status": "published"
}
```

### actionLink 格式

所有 deep link 以 `smart-camera://` 开头。常用模式：

| 跳转目标 | 格式 | 参数说明 |
|----------|------|---------|
| CMS Paywall 页 | `smart-camera://payment/cms_paywall_page?slot_name={slot}&solution_id={paywall_key}&source={source}` | `slot_name`: GrowthBook feature flag key; `solution_id`: paywall 的 key; `source`: 埋点来源 |
| 免费试用引导页 | `smart-camera://payment/free_license_page?source={source}` | 可选 `&is_gen3=true` 标记 Gen3 |
| 试用引导页（喂鸟器） | `smart-camera://payment/free_trial_guide_page?slot_name={slot}&one_time_product_id={id}&device_num=1&ios_pay_type=2&android_pay_type=0` | 一次性商品场景 |
| 外部网页 | `smart-camera://system/browser?url={encoded_url}` | 在 App 内打开网页 |

**source 常见值**：`home_promo_popup`, `home_promo_banner`, `gen3_video_description`, `gen3_library_entry`

配置 actionLink 时，根据 promotion 的触达点类型（popup/banner/video）选择对应的 source 值。

### image 字段是 i18n 的

`image` 字段启用了多语言，每个 locale 可以关联不同的图片。App 根据用户设备语言请求对应 locale 的图片。

- **通用图片（无文案）**：将同一张图片写入所有 locale
- **含文案图片**：`en` 必须提供；`de`/`fr`/`it`/`es`/`pt` 大概率有本地化版本；其余 locale 用 `en` 兜底

API 不指定 locale 时默认写入 `en`。用 `locale=all` 查看各 locale 的值：
```bash
GET /api/promotions/{id}?locale=all&depth=0
# 返回: "image": {"en": "media_id_en", "de": "media_id_de", ...}
```

### media 上传

图片需先上传到 media 集合获取 ID，再填入 imageCard 的 `image` 字段。

```bash
curl -X POST /api/media \
  -F "file=@image.jpg" \
  -F '_payload={"alt":"description"}'
```

`alt` 字段必须通过 `_payload` JSON 传递，直接用 `-F "alt=xxx"` 不生效。图片建议控制在 200KB 以内。

## 已知约束

- **key 唯一性**：CMS 创建时校验 key 是否重复，重复则报错并返回已有记录的 ID
- **image 是 i18n 字段**：通用图片需写入所有 locale；含文案图片按语言版本写入，未提供的 locale 用 `en` 兜底
- **dismissible 的行为**：用户点击关闭按钮后，App 调用 noThanks 接口，该 experimentKey 下的内容永久不再展示给该用户
- **skipNoThanks 允许重复展示**：设为 `true` 时，关闭按钮不调 noThanks 接口，下次仍可展示。适用于试用期提醒等需要反复触达的场景
