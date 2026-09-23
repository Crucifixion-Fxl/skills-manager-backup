---
name: reply-feishu
description: "仅生成 Feishu Card 2.0 或文本消息内容，不选择发送工具、应用或身份。需要表格、图表、按钮、表单、多栏等飞书富消息格式时使用。"
metadata:
  author: clawplex
  version: "1.0"
---

# reply-feishu: Feishu Card 2.0 Reply Guide

## Description

Use this skill when the reply channel is **Feishu** and you want to produce rich
interactive messages. The skill guides you to pick the right card type based on
content semantics and generate valid Card JSON v2.

This is a **format-only** skill. It never chooses the transport, application,
profile, or identity. Actual delivery must follow `feishu-channel-rules`; ordinary
messages use the verified active `lark-cli` profile with `--as user`.

If no reply skill is loaded (e.g. Web API channel), output plain markdown instead.

### Reply format decision tree

| Content characteristic | Card type | When to use |
|---|---|---|
| Plain text / markdown reply | `streaming_text` | Default format; typewriter streaming effect |
| Time-series data, statistics | `chart` | VChart line / bar / pie / area / gauge / ... |
| Structured list, comparison data | `table` | Feishu table component with typed columns |
| Dangerous operation needs confirmation | `confirm` | Confirm / Cancel buttons before execution |
| Collect multiple input parameters | `form` | Input fields, dropdowns, datetime pickers |
| Long-running task started | `status` | Progress card, updated in-place when done |

### Output format

Your reply **must** be one of the following JSON structures.

**Card reply (interactive)**

```json
{
  "msg_type": "interactive",
  "card": {
    "schema": "2.0",
    "config": { ... },
    "header": { ... },
    "body": { "elements": [ ... ] }
  }
}
```

**Text reply (fallback)**

```json
{
  "msg_type": "text",
  "content": "Plain text content here"
}
```

### Card JSON 2.0 base structure

Every card follows this skeleton:

```json
{
  "schema": "2.0",
  "config": {
    "update_multi": true
  },
  "header": {
    "title": { "tag": "plain_text", "content": "Card Title" },
    "template": "blue"
  },
  "body": {
    "elements": []
  }
}
```

### Header template colors

| Template | Use case |
|----------|----------|
| `blue` | Streaming text (default) |
| `turquoise` | Chart / data visualization |
| `green` | Table / success status |
| `red` | Confirm (danger) |
| `violet` | Form |
| `orange` | Status (in-progress) |

### Component reference files

For detailed component specs, read the corresponding reference file under `references/`.

| Need | Reference file | Key components |
|------|---------------|----------------|
| Card skeleton, config, header | `references/card-structure.md` | `schema`, `config`, `header` |
| Text, image, divider, note | `references/content.md` | `markdown`, `img`, `divider`, `note` |
| Column layout, form, collapse | `references/containers.md` | `column_set`, `form`, `collapsible_panel` |
| Table component | `references/table.md` | `table` columns, rows, data types |
| Chart (VChart) component | `references/chart.md` | `chart`, `chart_spec`, VChart types |
| Buttons, overflow, icons | `references/interactive.md` | `action`, `button`, `select_static`, `overflow` |
| Icon names and usage | `references/icons.md` | Icon tag format, common icon names |

## Rules

- **Format only**: Never use this Skill to select an MCP, application, bot, profile,
  or user identity. Pass the generated payload to the transport selected by
  `feishu-channel-rules`.
- **30 KB max**: Card JSON must not exceed 30 KB. For large datasets, paginate or summarize.
- **Streaming + reply mutual exclusion**: Streaming cards cannot be used with a
  quoted-message reply. Send them as a new message, while keeping the same transport
  and identity selected by `feishu-channel-rules`.
- **Card update is full replace**: Updating a card replaces the entire card JSON. Always send the complete card.
- **VChart spec**: The `chart_spec` field must conform to VChart specification. Supported types: `line`, `bar`, `area`, `pie`, `radar`, `gauge`, `funnel`, `scatter`, `treemap`, `heatmap`, `waterfall`, `circularProgress`, `linearProgress`.
- **Always provide fallback**: If card JSON is invalid or the Feishu API rejects it, always include a meaningful `content` field as plain text backup.
- **Use semantic card type**: Match the card type to content semantics (table for structured data, chart for time-series, form for user input). Do not default everything to markdown.

## Examples

### Good — Chart card for time-series data

```json
{
  "msg_type": "interactive",
  "card": {
    "schema": "2.0",
    "header": {
      "title": { "tag": "plain_text", "content": "Redis Memory Usage (Last 24h)" },
      "template": "turquoise"
    },
    "body": {
      "elements": [
        {
          "tag": "chart",
          "chart_spec": {
            "type": "line",
            "data": [{ "values": [
              { "time": "00:00", "memory_gb": 2.1 },
              { "time": "06:00", "memory_gb": 3.5 },
              { "time": "12:00", "memory_gb": 5.2 },
              { "time": "18:00", "memory_gb": 4.8 }
            ]}],
            "xField": "time",
            "yField": "memory_gb",
            "title": { "text": "Memory Usage (GB)" }
          },
          "aspect_ratio": "16:9",
          "preview": true
        },
        {
          "tag": "markdown",
          "content": "Peak **5.2 GB** at 12:00. Check batch jobs running at that time."
        }
      ]
    }
  }
}
```

### Good — Interactive form card

```json
{
  "msg_type": "interactive",
  "card": {
    "schema": "2.0",
    "header": {
      "title": { "tag": "plain_text", "content": "Create Scheduled Task" },
      "template": "violet"
    },
    "body": {
      "elements": [
        {
          "tag": "form",
          "name": "create_scheduled_task",
          "elements": [
            {
              "tag": "input",
              "name": "task_description",
              "placeholder": { "tag": "plain_text", "content": "Describe the task" },
              "label": { "tag": "plain_text", "content": "Task Description" }
            },
            {
              "tag": "select_static",
              "name": "frequency",
              "placeholder": { "tag": "plain_text", "content": "Select frequency" },
              "label": { "tag": "plain_text", "content": "Execution Frequency" },
              "options": [
                { "text": { "tag": "plain_text", "content": "Daily" }, "value": "daily" },
                { "text": { "tag": "plain_text", "content": "Weekly" }, "value": "weekly" }
              ]
            },
            {
              "tag": "button",
              "text": { "tag": "plain_text", "content": "Create Task" },
              "type": "primary",
              "name": "submit_btn",
              "behaviors": [{ "type": "callback", "value": { "action": "create_task" } }]
            }
          ]
        }
      ]
    }
  }
}
```

### Bad — Using plain text for structured/tabular data

```json
{
  "msg_type": "text",
  "content": "User A: 100 orders\nUser B: 80 orders\nUser C: 60 orders"
}
```

Use `table` or `chart` card type instead — plain text loses structure and readability.

### Bad — Partial card update (not supported)

```json
{
  "msg_type": "interactive",
  "card": {
    "body": {
      "elements": [{ "tag": "markdown", "content": "Updated text only" }]
    }
  }
}
```

Card updates are full-replace. Always send the complete card JSON including `schema`, `header`, and all `body.elements`.
