# Feishu Card Content Components

## plain_text

Renders plain (unformatted) text. Supports sizing, coloring, alignment, and an optional leading icon.

### Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `tag` | string | Yes | Must be `"plain_text"` |
| `content` | string | Yes | The text to display |
| `text_size` | enum | No | Font size. Values: `"normal"`, `"heading"`, `"xxxx-large"`, `"xxx-large"`, `"xx-large"`, `"x-large"`, `"large"`, `"medium"`, `"small"`, `"x-small"` |
| `text_color` | string | No | Text color. Values: `"default"`, `"grey"`, `"red"`, `"green"`, `"blue"`, `"orange"`, `"purple"` |
| `text_align` | enum | No | Alignment: `"left"`, `"center"`, `"right"` |
| `lines` | int | No | Max lines to display; overflow is truncated with ellipsis |
| `icon` | object | No | Leading icon: `{"tag": "standard_icon", "token": "...", "color": "blue"}` |

### Example

```json
{
  "tag": "plain_text",
  "content": "Pipeline completed in 3m 42s",
  "text_size": "large",
  "text_color": "green",
  "text_align": "left",
  "icon": {
    "tag": "standard_icon",
    "token": "check-circle_outlined",
    "color": "green"
  }
}
```

---

## markdown

Renders rich text using a subset of Markdown syntax. Also referred to as `rich_text` in some contexts — both use `tag: "markdown"`.

### Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `tag` | string | Yes | Must be `"markdown"` |
| `content` | string | Yes | Markdown text (see supported syntax below) |
| `text_size` | enum | No | Same values as `plain_text` |
| `text_align` | enum | No | `"left"`, `"center"`, `"right"` |
| `href` | object | No | Map of link keys to URL objects for click tracking |

### Supported Markdown syntax

| Syntax | Example |
|--------|---------|
| Bold | `**bold**` |
| Italic | `*italic*` |
| Strikethrough | `~~strike~~` |
| Link | `[text](url)` |
| At user | `<at id=ou_xxx></at>` |
| Emoji | `:smile:` |

**Not supported in cards:** headers (h1-h6), images, code blocks.

### `href` field

Maps placeholder link keys in content to actual URLs. Useful for click tracking.

```json
{
  "tag": "markdown",
  "content": "View the [dashboard]($dashboard_link) for details.",
  "href": {
    "dashboard_link": {
      "url": "https://grafana.example.com/d/abc123"
    }
  }
}
```

### Example

```json
{
  "tag": "markdown",
  "content": "**Alert resolved** — CPU usage on `gateway-pod-7f` dropped below threshold.\n\nHandled by <at id=ou_abc123def></at> :thumbsup:",
  "text_size": "normal",
  "text_align": "left"
}
```

---

## img

Displays an uploaded image. The image must first be uploaded via the `POST /im/v1/images` API to obtain an `img_key`.

### Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `tag` | string | Yes | Must be `"img"` |
| `img_key` | string | Yes | Image key returned by the image upload API |
| `alt` | object | No | Alt text: `{"tag": "plain_text", "content": "description"}` |
| `preview` | bool | No | When `true`, image can be clicked to enlarge |
| `compact_width` | bool | No | When `true`, image renders at compact width |
| `mode` | enum | No | Display mode: `"fit_horizontal"` (fit width) or `"crop_center"` (center crop) |

### Example

```json
{
  "tag": "img",
  "img_key": "img_v3_02ab_metrics_chart_xyz789",
  "alt": {
    "tag": "plain_text",
    "content": "CPU usage chart for the last 24 hours"
  },
  "preview": true,
  "compact_width": false,
  "mode": "fit_horizontal"
}
```

---

## note

Renders a grey footer-style note at the bottom of the card. Only `plain_text` and `img` elements are allowed inside.

### Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `tag` | string | Yes | Must be `"note"` |
| `elements` | array | Yes | Array of `plain_text` or `img` elements only |

### Example

```json
{
  "tag": "note",
  "elements": [
    {
      "tag": "img",
      "img_key": "img_v3_bot_avatar_abc",
      "alt": { "tag": "plain_text", "content": "bot icon" }
    },
    {
      "tag": "plain_text",
      "content": "Sent by ClawPlex Bot | 2026-03-05 14:30 UTC"
    }
  ]
}
```
