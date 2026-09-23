# Interactive Components

Feishu Card interactive elements for collecting user input and triggering actions.

> Components marked "form-only" MUST be placed inside a `form` container element.

---

## 1. Button (`tag: "button"`)

A clickable button that triggers callbacks or opens URLs.

### Schema

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `tag` | string | Yes | Must be `"button"` |
| `text` | object | Yes | Button label. `{"tag":"plain_text","content":"..."}` |
| `type` | enum | No | `"default"` / `"primary"` / `"danger"` / `"text"`. Default: `"default"` |
| `size` | enum | No | `"medium"` / `"small"` / `"tiny"`. Default: `"medium"` |
| `icon` | object | No | Icon before text. `{"tag":"standard_icon","token":"..."}` |
| `disabled` | bool | No | Disable the button |
| `behaviors` | array | Yes (for action) | Action on click (see below) |
| `name` | string | Form-only | Field name when inside a `form` container |
| `confirm` | object | No | Confirmation dialog before action |

### Behavior types

**Callback** — sends event to your server:

```json
[{"type": "callback", "value": {"action": "approve", "request_id": "123"}}]
```

**Open URL** — opens a link:

```json
[{"type": "open_url", "default_url": "https://example.com", "pc_url": "", "ios_url": "", "android_url": ""}]
```

### Confirm dialog format

```json
{
  "title": {"tag": "plain_text", "content": "Are you sure?"},
  "text": {"tag": "plain_text", "content": "This action cannot be undone."}
}
```

### Example

```json
{
  "tag": "button",
  "text": {"tag": "plain_text", "content": "Approve Request"},
  "type": "primary",
  "size": "medium",
  "icon": {"tag": "standard_icon", "token": "check-circle_outlined"},
  "confirm": {
    "title": {"tag": "plain_text", "content": "Confirm Approval"},
    "text": {"tag": "plain_text", "content": "This will approve the deployment request."}
  },
  "behaviors": [
    {"type": "callback", "value": {"action": "approve", "request_id": "req-42"}}
  ]
}
```

---

## 2. Input (`tag: "input"`) — form-only

A text input field for free-form user input.

### Schema

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `tag` | string | Yes | Must be `"input"` |
| `name` | string | Yes | Field name in form submission payload |
| `placeholder` | object | No | Hint text. `{"tag":"plain_text","content":"..."}` |
| `label` | object | No | Label above the input. `{"tag":"plain_text","content":"..."}` |
| `default_value` | string | No | Pre-filled value |
| `max_length` | int | No | Maximum character count |
| `input_type` | enum | No | `"text"` / `"multiline"` / `"password"`. Default: `"text"` |

### Example

```json
{
  "tag": "input",
  "name": "reason",
  "label": {"tag": "plain_text", "content": "Rejection Reason"},
  "placeholder": {"tag": "plain_text", "content": "Enter the reason for rejection..."},
  "input_type": "multiline",
  "max_length": 500,
  "default_value": ""
}
```

---

## 3. Static Select (`tag: "select_static"`) — form-only

A dropdown selector with pre-defined options.

### Schema

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `tag` | string | Yes | Must be `"select_static"` |
| `name` | string | Yes | Field name in form submission payload |
| `placeholder` | object | No | Hint text when nothing selected |
| `label` | object | No | Label above the select |
| `options` | array | Yes | List of option objects (see below) |
| `initial_option` | string | No | `value` of the default selected option |

### Option format

```json
{"text": {"tag": "plain_text", "content": "Display Text"}, "value": "option_value"}
```

### Example

```json
{
  "tag": "select_static",
  "name": "priority",
  "label": {"tag": "plain_text", "content": "Priority Level"},
  "placeholder": {"tag": "plain_text", "content": "Select priority..."},
  "initial_option": "medium",
  "options": [
    {"text": {"tag": "plain_text", "content": "High"}, "value": "high"},
    {"text": {"tag": "plain_text", "content": "Medium"}, "value": "medium"},
    {"text": {"tag": "plain_text", "content": "Low"}, "value": "low"}
  ]
}
```

---

## 4. Person Select (`tag: "select_person"`) — form-only

A people picker that searches the organization directory. Returns `open_id` of selected person(s).

### Schema

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `tag` | string | Yes | Must be `"select_person"` |
| `name` | string | Yes | Field name in form submission payload |
| `placeholder` | object | No | Hint text |
| `label` | object | No | Label above the picker |
| `multi_select` | bool | No | Allow selecting multiple people. Default: `false` |

### Example

```json
{
  "tag": "select_person",
  "name": "assignee",
  "label": {"tag": "plain_text", "content": "Assign To"},
  "placeholder": {"tag": "plain_text", "content": "Select a team member..."},
  "multi_select": false
}
```

---

## 5. Date Picker (`tag: "picker_date"`) — form-only

A calendar-style date selector.

### Schema

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `tag` | string | Yes | Must be `"picker_date"` |
| `name` | string | Yes | Field name in form submission payload |
| `placeholder` | object | No | Hint text |
| `label` | object | No | Label above the picker |
| `initial_date` | string | No | Default date in `YYYY-MM-DD` format |

### Example

```json
{
  "tag": "picker_date",
  "name": "due_date",
  "label": {"tag": "plain_text", "content": "Due Date"},
  "placeholder": {"tag": "plain_text", "content": "Select a date..."},
  "initial_date": "2026-03-15"
}
```

---

## 6. DateTime Picker (`tag: "picker_datetime"`) — form-only

A combined date and time selector.

### Schema

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `tag` | string | Yes | Must be `"picker_datetime"` |
| `name` | string | Yes | Field name in form submission payload |
| `placeholder` | object | No | Hint text |
| `label` | object | No | Label above the picker |
| `initial_datetime` | string | No | Default value in `YYYY-MM-DD HH:mm` format |

### Example

```json
{
  "tag": "picker_datetime",
  "name": "meeting_time",
  "label": {"tag": "plain_text", "content": "Meeting Time"},
  "placeholder": {"tag": "plain_text", "content": "Select date and time..."},
  "initial_datetime": "2026-03-10 14:30"
}
```

---

## 7. Checker (`tag: "checker"`) — form-only

A checkbox element. Can be standalone or grouped with an "overall" header for check/uncheck-all behavior.

### Schema

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `tag` | string | Yes | Must be `"checker"` |
| `name` | string | Yes | Field name in form submission payload |
| `checked` | bool | No | Default checked state. Default: `false` |
| `text` | object | No | Label text. `{"tag":"plain_text","content":"..."}` |
| `overall` | bool | No | If `true`, acts as group header (check/uncheck all children). Default: `false` |
| `disabled` | bool | No | Disable the checkbox |

### Example

```json
{
  "tag": "checker",
  "name": "select_all",
  "text": {"tag": "plain_text", "content": "Select All"},
  "checked": false,
  "overall": true,
  "disabled": false
}
```

Individual item:

```json
{
  "tag": "checker",
  "name": "item_backup",
  "text": {"tag": "plain_text", "content": "Backup database before deploy"},
  "checked": true,
  "disabled": false
}
```

---

## 8. Overflow (`tag: "overflow"`)

A "..." dropdown menu for secondary actions. Can be used outside forms.

### Schema

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `tag` | string | Yes | Must be `"overflow"` |
| `options` | array | Yes | List of option objects (same format as `select_static`) |
| `behaviors` | array | No | Click actions (callback or open_url) |

### Example

```json
{
  "tag": "overflow",
  "options": [
    {"text": {"tag": "plain_text", "content": "Edit"}, "value": "edit"},
    {"text": {"tag": "plain_text", "content": "Duplicate"}, "value": "duplicate"},
    {"text": {"tag": "plain_text", "content": "Delete"}, "value": "delete"}
  ],
  "behaviors": [
    {"type": "callback", "value": {"action": "overflow_click"}}
  ]
}
```

---

## Form Container Reference

Interactive form-only components must be wrapped in a `form` container. The form collects all named field values and submits them together.

```json
{
  "tag": "form",
  "name": "deploy_form",
  "elements": [
    {"tag": "input", "name": "version", "label": {"tag": "plain_text", "content": "Version"}, "placeholder": {"tag": "plain_text", "content": "e.g. v1.2.3"}},
    {"tag": "select_static", "name": "env", "label": {"tag": "plain_text", "content": "Environment"}, "options": [
      {"text": {"tag": "plain_text", "content": "Staging"}, "value": "staging"},
      {"text": {"tag": "plain_text", "content": "Production"}, "value": "prod"}
    ]},
    {"tag": "picker_date", "name": "deploy_date", "label": {"tag": "plain_text", "content": "Deploy Date"}},
    {"tag": "checker", "name": "confirm_backup", "text": {"tag": "plain_text", "content": "Database backup completed"}, "checked": false},
    {"tag": "button", "name": "submit_btn", "text": {"tag": "plain_text", "content": "Submit"}, "type": "primary", "behaviors": [{"type": "callback", "value": {"action": "submit_deploy"}}]}
  ]
}
```

On submit, your server receives all field values keyed by `name`.
