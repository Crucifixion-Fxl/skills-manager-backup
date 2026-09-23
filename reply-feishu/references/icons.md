# Standard Icons

Feishu Card icon reference for use in buttons, column headers, and other components.

## Usage Format

```json
{"tag": "standard_icon", "token": "check-circle_outlined", "color": "green"}
```

The `color` field is optional. When omitted, the icon uses the default theme color.

## Supported Colors

| Color | Typical Use |
|-------|-------------|
| `blue` | Links, info, primary actions |
| `green` | Success, approved, healthy |
| `red` | Error, danger, critical |
| `orange` | Warning, attention needed |
| `grey` | Disabled, secondary, neutral |
| `purple` | Special, custom categories |
| `yellow` | Caution, pending review |
| `neutral` | Default theme color |

---

## Icon Tokens by Category

### Status

| Token | Description | Suggested Color |
|-------|-------------|-----------------|
| `check-circle_outlined` | Success / done | `green` |
| `close-circle_outlined` | Error / failed | `red` |
| `info-circle_outlined` | Information | `blue` |
| `warning_outlined` | Warning | `orange` |
| `time-circle_outlined` | Pending / time | `grey` |
| `loading_outlined` | Loading / in progress | `blue` |

### Actions

| Token | Description | Suggested Color |
|-------|-------------|-----------------|
| `edit_outlined` | Edit | `blue` |
| `delete_outlined` | Delete | `red` |
| `copy_outlined` | Copy | `grey` |
| `download_outlined` | Download | `blue` |
| `search_outlined` | Search | `grey` |
| `add_outlined` | Add / create | `blue` |
| `refresh_outlined` | Refresh | `grey` |

### Objects

| Token | Description | Suggested Color |
|-------|-------------|-----------------|
| `file_outlined` | File | `grey` |
| `folder_outlined` | Folder | `orange` |
| `image_outlined` | Image | `purple` |
| `link_outlined` | Link / URL | `blue` |
| `calendar_outlined` | Calendar / schedule | `blue` |
| `mail_outlined` | Email | `grey` |
| `chat_outlined` | Chat / message | `blue` |

### Data

| Token | Description | Suggested Color |
|-------|-------------|-----------------|
| `chart-bar_outlined` | Chart / analytics | `blue` |
| `dashboard_outlined` | Dashboard | `purple` |
| `data_outlined` | Data / database | `green` |
| `server_outlined` | Server | `grey` |
| `cloud_outlined` | Cloud | `blue` |

### Navigation

| Token | Description |
|-------|-------------|
| `arrow-left_outlined` | Navigate left / back |
| `arrow-right_outlined` | Navigate right / forward |
| `arrow-up_outlined` | Navigate up |
| `arrow-down_outlined` | Navigate down |
| `chevron-right_outlined` | Expand / drill down |
| `chevron-down_outlined` | Collapse / dropdown |
| `more_outlined` | More actions (...) |
| `close_outlined` | Close / dismiss |

---

## Examples

Button with icon:

```json
{
  "tag": "button",
  "text": {"tag": "plain_text", "content": "Deploy"},
  "type": "primary",
  "icon": {"tag": "standard_icon", "token": "cloud_outlined", "color": "neutral"}
}
```

Status indicator in column header:

```json
{
  "tag": "column",
  "elements": [
    {
      "tag": "div",
      "text": {
        "tag": "lark_md",
        "content": "Build Passed"
      },
      "icon": {"tag": "standard_icon", "token": "check-circle_outlined", "color": "green"}
    }
  ]
}
```

---

> This is a curated subset of commonly used icons. Full icon list available at:
> https://open.feishu.cn/document/feishu-cards/enumerations-for-icons
