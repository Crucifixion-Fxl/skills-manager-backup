# Table Component

The table is the most commonly used data display component in Feishu message cards. It supports pagination, column typing, colored tags, user avatars, and more.

## Top-level Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `tag` | string | Yes | Must be `"table"` |
| `page_size` | int | No | Rows per page. Default: `5` |
| `row_height` | enum | No | Row height: `"low"`, `"medium"`, `"high"` |
| `header_style` | object | No | Header row styling (see below) |
| `columns` | array | Yes | Column definitions |
| `rows` | array | Yes | Data rows |

### header_style

| Field | Type | Description |
|-------|------|-------------|
| `text_align` | enum | `"left"`, `"center"`, `"right"` |
| `background_style` | enum | `"grey"`, `"none"` |
| `bold` | bool | Bold header text |

## Column Definition

Each item in the `columns` array defines one column.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | Key used to look up the value in each row |
| `display_name` | string | No | Header text shown to the user. Defaults to `name` if omitted |
| `data_type` | string | Yes | Determines how the cell value is rendered. See enum below |
| `width` | string | No | `"auto"`, `"short"`, `"medium"`, `"long"`, or a pixel value like `"120px"` |
| `horizontal_align` | enum | No | Cell text alignment: `"left"`, `"center"`, `"right"` |

## data_type Enum

This is the most critical field. The `data_type` determines what format the corresponding value in each row must use.

| data_type | Row value format | Description |
|-----------|-----------------|-------------|
| `text` | `"hello"` | Plain string |
| `number` | `42` or `3.14` | Numeric value (int or float) |
| `lark_md` | `"**bold** text"` | Lark-flavored Markdown string |
| `options` | `[{"text":"Running","color":"green"}]` | Array of colored tag objects |
| `persons` | `[{"id":"ou_xxx"}]` | Array of Feishu user ID objects (renders avatars) |
| `date` | `1709222400` | Unix timestamp in seconds |

### Options color enum

The `color` field inside an options tag object must be one of:

`"grey"`, `"red"`, `"orange"`, `"yellow"`, `"green"`, `"blue"`, `"purple"`

## Complete Example: Pod Status Table

A table showing Kubernetes pod status with name, status (colored), restart count, and age.

```json
{
  "tag": "table",
  "page_size": 10,
  "row_height": "medium",
  "header_style": {
    "text_align": "left",
    "background_style": "grey",
    "bold": true
  },
  "columns": [
    {
      "name": "pod_name",
      "display_name": "Pod Name",
      "data_type": "text",
      "width": "long"
    },
    {
      "name": "status",
      "display_name": "Status",
      "data_type": "options",
      "width": "short"
    },
    {
      "name": "restarts",
      "display_name": "Restarts",
      "data_type": "number",
      "width": "short",
      "horizontal_align": "center"
    },
    {
      "name": "age",
      "display_name": "Age",
      "data_type": "text",
      "width": "medium"
    }
  ],
  "rows": [
    {
      "pod_name": "gateway-5d4f8b7c96-x2k9m",
      "status": [
        {
          "text": "Running",
          "color": "green"
        }
      ],
      "restarts": 0,
      "age": "3d 12h"
    },
    {
      "pod_name": "worker-7b9c6d4e88-p3j7n",
      "status": [
        {
          "text": "CrashLoopBackOff",
          "color": "red"
        }
      ],
      "restarts": 15,
      "age": "1d 6h"
    }
  ]
}
```

### Key points about this example

- `status` uses `data_type: "options"` so the row value is an array of `{text, color}` objects.
- `restarts` uses `data_type: "number"` so the row value is a bare integer, not a string.
- `pod_name` and `age` use `data_type: "text"` so the row values are plain strings.
- `header_style` with `background_style: "grey"` and `bold: true` gives a clear visual header.
