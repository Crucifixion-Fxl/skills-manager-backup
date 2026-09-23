# Container Components

Feishu Message Card container components that hold and organize child elements.

---

## column_set

Multi-column layout container. Arranges child columns horizontally.

### Fields

| Field | Type | Required | Description |
|---|---|---|---|
| `tag` | string | yes | Must be `"column_set"` |
| `columns` | array | yes | Array of column objects |
| `flex_mode` | enum | no | How columns fill width: `"none"` / `"stretch"` / `"bisect"` / `"trisect"` / `"flow"` |
| `background_style` | enum | no | `"default"` / `"grey"` / `"filled"` |
| `horizontal_spacing` | string | no | Spacing between columns: `"default"` / `"small"` / `"large"` |
| `margin` | string | no | Outer margin of the column set |

### Column Object Fields

Each item in `columns` is a column object:

| Field | Type | Required | Description |
|---|---|---|---|
| `tag` | string | yes | Must be `"column"` |
| `width` | string | no | `"weighted"` (proportional, default), pixel value like `"200px"`, or `"auto"` |
| `weight` | int | no | Relative weight when `width="weighted"` (default 1) |
| `vertical_align` | enum | no | `"top"` / `"center"` / `"bottom"` |
| `elements` | array | yes | Child components inside this column |
| `background_style` | enum | no | `"default"` / `"grey"` / `"filled"` |
| `padding` | string | no | Inner padding of the column |

### Example

Two-column layout: left column with text, right column with a metric.

```json
{
  "tag": "column_set",
  "flex_mode": "stretch",
  "horizontal_spacing": "default",
  "background_style": "grey",
  "columns": [
    {
      "tag": "column",
      "width": "weighted",
      "weight": 2,
      "vertical_align": "center",
      "elements": [
        {
          "tag": "markdown",
          "content": "**Service Health**\nAll systems operational. Last checked 2 minutes ago."
        }
      ]
    },
    {
      "tag": "column",
      "width": "weighted",
      "weight": 1,
      "vertical_align": "center",
      "elements": [
        {
          "tag": "markdown",
          "content": "**Uptime**\n99.97%"
        },
        {
          "tag": "img",
          "img_key": "img_v3_02a1_green_check",
          "alt": {
            "tag": "plain_text",
            "content": "healthy"
          }
        }
      ]
    }
  ]
}
```

---

## form_container

Form container that groups interactive input components and a submit button. On submit, all named fields inside the form are collected into a `form_value` dictionary and sent via callback.

### Fields

| Field | Type | Required | Description |
|---|---|---|---|
| `tag` | string | yes | Must be `"form"` |
| `name` | string | yes | Unique form identifier, returned in callback payload |
| `elements` | array | yes | Interactive components (inputs, selects, etc.) plus a submit button |

The submit button inside a form must use `behaviors` with type `"callback"`:

```json
{
  "type": "callback",
  "value": { "action": "submit_form" }
}
```

When the user clicks submit, the callback payload includes `form_value` -- a dict mapping each element's `name` to its current value.

### Example

A form with a text input, a static select, and a submit button.

```json
{
  "tag": "form",
  "name": "ticket_form",
  "elements": [
    {
      "tag": "input",
      "name": "title",
      "placeholder": {
        "tag": "plain_text",
        "content": "Enter ticket title"
      },
      "label": {
        "tag": "plain_text",
        "content": "Title"
      },
      "required": true
    },
    {
      "tag": "select_static",
      "name": "priority",
      "placeholder": {
        "tag": "plain_text",
        "content": "Select priority"
      },
      "options": [
        {
          "text": { "tag": "plain_text", "content": "High" },
          "value": "high"
        },
        {
          "text": { "tag": "plain_text", "content": "Medium" },
          "value": "medium"
        },
        {
          "text": { "tag": "plain_text", "content": "Low" },
          "value": "low"
        }
      ]
    },
    {
      "tag": "button",
      "text": {
        "tag": "plain_text",
        "content": "Submit"
      },
      "type": "primary",
      "behaviors": [
        {
          "type": "callback",
          "value": {
            "action": "create_ticket"
          }
        }
      ]
    }
  ]
}
```

---

## interactive_container

A container that makes its entire area clickable. Wraps child elements and attaches click behaviors (URL navigation or callback) to the whole container surface.

### Fields

| Field | Type | Required | Description |
|---|---|---|---|
| `tag` | string | yes | Must be `"interactive"` |
| `width` | string | no | `"fill"` (default), `"auto"`, or pixel value like `"400px"` |
| `height` | string | no | Pixel value like `"80px"` |
| `background_style` | enum | no | `"default"` / `"grey"` / `"filled"` |
| `elements` | array | yes | Child components displayed inside the container |
| `behaviors` | array | yes | Click actions for the entire container area |

### Behavior Types

- **open_url**: `{"type": "open_url", "default_url": "https://..."}`
- **callback**: `{"type": "callback", "value": {"key": "value"}}`

### Example

A clickable summary card that links to a details page.

```json
{
  "tag": "interactive",
  "width": "fill",
  "height": "80px",
  "background_style": "grey",
  "behaviors": [
    {
      "type": "open_url",
      "default_url": "https://monitor.example.com/incidents/INC-2024"
    }
  ],
  "elements": [
    {
      "tag": "column_set",
      "flex_mode": "stretch",
      "columns": [
        {
          "tag": "column",
          "width": "weighted",
          "weight": 3,
          "vertical_align": "center",
          "elements": [
            {
              "tag": "markdown",
              "content": "**INC-2024** Database connection pool exhausted\nStatus: **Investigating** | Severity: P1"
            }
          ]
        },
        {
          "tag": "column",
          "width": "auto",
          "vertical_align": "center",
          "elements": [
            {
              "tag": "markdown",
              "content": "View Details >"
            }
          ]
        }
      ]
    }
  ]
}
```

---

## collapsible_panel

An expandable/collapsible panel. The header is always visible; the body elements are revealed when the user expands the panel.

### Fields

| Field | Type | Required | Description |
|---|---|---|---|
| `tag` | string | yes | Must be `"collapsible_panel"` |
| `expanded` | bool | no | Whether the panel starts expanded (default `false`) |
| `header` | object | yes | Always-visible header content |
| `header.title` | object | yes | Plain text title: `{"tag": "plain_text", "content": "..."}` |
| `vertical_spacing` | string | no | Spacing between header and body content |
| `elements` | array | yes | Child components revealed on expand |
| `background_style` | enum | no | `"default"` / `"grey"` / `"filled"` |

### Example

An expandable "Show details" section with hidden markdown content.

```json
{
  "tag": "collapsible_panel",
  "expanded": false,
  "background_style": "grey",
  "header": {
    "title": {
      "tag": "plain_text",
      "content": "Show deployment details"
    }
  },
  "vertical_spacing": "default",
  "elements": [
    {
      "tag": "markdown",
      "content": "**Deployment Summary**\n- Image: `registry.example.com/api:v2.4.1`\n- Replicas: 3/3 ready\n- Rollout started: 2026-03-05 14:32 UTC\n- Duration: 47s\n\n**Changed Files**\n- `src/handlers/auth.py` (modified)\n- `k8s/overlays/prod/deployment.yaml` (modified)"
    },
    {
      "tag": "markdown",
      "content": "**Rollback Command**\n```\nkubectl rollout undo deployment/api -n prod\n```"
    }
  ]
}
```
