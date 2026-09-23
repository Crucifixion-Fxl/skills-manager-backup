# Feishu Card JSON 2.0 — Top-Level Structure

## Top-Level Fields

A Feishu Interactive Card (JSON 2.0) has four top-level fields:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `schema` | string | Yes | Must be `"2.0"` |
| `config` | object | No | Card-level configuration |
| `header` | object | No | Card header (title, icon, color) |
| `body` | object | No | Card body with component elements |
| `i18n_body` | object | No | Multi-language body, keyed by locale |

---

## `config`

Optional card-level settings.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `update_multi` | bool | `false` | When `true`, all recipients see updated card content (shared card updates) |
| `enable_forward` | bool | `true` | When `false`, card cannot be forwarded |
| `style` | object | — | Card width settings |

### `config.style`

```json
{
  "width": {
    "default": "default",
    "pc": "compact",
    "mobile": "fill"
  }
}
```

- `default`: `"default"` or `"compact"` or `"fill"`
- `pc`: override for desktop
- `mobile`: override for mobile

---

## `header`

Card header with title, optional subtitle, color template, and icon.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `title` | object | Yes | `{"tag": "plain_text", "content": "Title text"}` |
| `subtitle` | object | No | `{"tag": "plain_text", "content": "Subtitle text"}` |
| `template` | string | No | Header background color |
| `icon` | object | No | Standard icon: `{"tag": "standard_icon", "token": "icon_token"}` |
| `ud_icon` | object | No | Custom icon via uploaded image: `{"tag": "custom_icon", "img_key": "img_v3_..."}` |
| `text_tag_list` | array | No | Array of tag badges displayed in the header |

### `header.template` color values

`blue` | `turquoise` | `green` | `red` | `violet` | `orange` | `grey` | `indigo` | `wathet` | `yellow` | `lime` | `purple` | `carmine`

### `header.text_tag_list` item format

```json
{
  "tag": "text_tag",
  "text": { "tag": "plain_text", "content": "Tag Label" },
  "color": "blue"
}
```

---

## `body`

Contains an `elements` array. Each element is a component (plain_text, markdown, image, note, columns, etc.).

```json
{
  "elements": [
    { "tag": "markdown", "content": "Hello **world**" }
  ]
}
```

---

## `i18n_body`

Multi-language support. Keys are locale strings (`"zh_cn"`, `"en_us"`, `"ja_jp"`, etc.), values have the same structure as `body`.

```json
{
  "i18n_body": {
    "zh_cn": {
      "elements": [
        { "tag": "markdown", "content": "你好 **世界**" }
      ]
    },
    "en_us": {
      "elements": [
        { "tag": "markdown", "content": "Hello **world**" }
      ]
    }
  }
}
```

When `i18n_body` is present and matches the user's locale, it takes precedence over `body`.

---

## Example 1: Minimal Card

A simple card with one markdown text element.

```json
{
  "schema": "2.0",
  "body": {
    "elements": [
      {
        "tag": "markdown",
        "content": "Task **#1024** has been completed."
      }
    ]
  }
}
```

---

## Example 2: Full-Featured Card

Card with config, colored header with icon and subtitle, multiple body elements.

```json
{
  "schema": "2.0",
  "config": {
    "update_multi": true,
    "enable_forward": true,
    "style": {
      "width": {
        "default": "default",
        "pc": "compact",
        "mobile": "fill"
      }
    }
  },
  "header": {
    "title": {
      "tag": "plain_text",
      "content": "Deployment Report"
    },
    "subtitle": {
      "tag": "plain_text",
      "content": "Production Environment"
    },
    "template": "green",
    "icon": {
      "tag": "standard_icon",
      "token": "rocket_outlined"
    },
    "text_tag_list": [
      {
        "tag": "text_tag",
        "text": { "tag": "plain_text", "content": "Success" },
        "color": "green"
      },
      {
        "tag": "text_tag",
        "text": { "tag": "plain_text", "content": "v2.4.1" },
        "color": "blue"
      }
    ]
  },
  "body": {
    "elements": [
      {
        "tag": "markdown",
        "content": "**Service:** gateway\n**Branch:** main\n**Commit:** `a1b2c3d`\n\nAll health checks passed."
      },
      {
        "tag": "img",
        "img_key": "img_v3_deploy_metrics_abc123",
        "alt": { "tag": "plain_text", "content": "Deployment metrics" },
        "preview": true,
        "mode": "fit_horizontal"
      },
      {
        "tag": "note",
        "elements": [
          {
            "tag": "plain_text",
            "content": "Deployed by CI/CD pipeline at 2026-03-05 14:30 UTC"
          }
        ]
      }
    ]
  }
}
```
