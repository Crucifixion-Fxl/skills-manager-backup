# Format & Deploy Scripts Reference

Reference implementations for the dashboard formatter and Grafana deploy script. Copy and adapt to your project (paths, env vars, service name).

---

## 1. Format Script (fmt.py)

**Purpose**: Read dashboard template JSON, group panels by row sections, assign sequential panel IDs, and recalculate `gridPos` (2-column layout: row full-width, content panels 12×8 in a grid). Run after editing the template and before committing or deploying.

**Usage**:
```bash
python3 path/to/fmt.py path/to/template.json -o path/to/template.json
# Without -o: writes to formatted_template.json in the same dir as input
```

**Logic**:
- Parse JSON; require top-level `panels` array.
- Iterate panels: when `type == "row"`, start a new section; otherwise append to current section's content panels.
- Rebuild `panels`: for each section, emit row panel (id, gridPos `h:1 w:24 x:0 y:current_y`), then content panels in a 2-column grid (`h:8 w:12`, `x: (i%2)*12`, `y: current_y + (i//2)*8`).
- Assign panel IDs sequentially from 1.
- Write JSON with `indent=2`, `ensure_ascii=False`.

**Full reference implementation** (Python 3):

```python
import argparse
import json
import os

def format_grafana_template(input_file, output_file):
    """Read template, group by row sections, assign IDs and gridPos, write result."""
    try:
        with open(input_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"Error: Input file not found at {input_file}")
        return
    except json.JSONDecodeError:
        print(f"Error: Could not decode JSON from {input_file}")
        return

    if 'panels' not in data or not isinstance(data['panels'], list):
        print("No panels found in template")
        return

    panels = data['panels']
    sections = []
    current_section = {'row': None, 'panels': []}

    for panel in panels:
        if panel.get('type') == 'row':
            if current_section['row'] is not None or current_section['panels']:
                sections.append(current_section)
            current_section = {'row': panel, 'panels': []}
        else:
            current_section['panels'].append(panel)

    if current_section['row'] is not None or current_section['panels']:
        sections.append(current_section)

    new_panels = []
    current_y = 0
    panel_id = 1

    for section in sections:
        if section['row']:
            section['row']['id'] = panel_id
            section['row']['gridPos'] = {'h': 1, 'w': 24, 'x': 0, 'y': current_y}
            new_panels.append(section['row'])
            panel_id += 1
            current_y += 1

        for i, panel in enumerate(section['panels']):
            panel['id'] = panel_id
            panel['gridPos'] = {
                'h': 8,
                'w': 12,
                'x': (i % 2) * 12,
                'y': current_y + (i // 2) * 8
            }
            new_panels.append(panel)
            panel_id += 1

        if section['panels']:
            num_rows = (len(section['panels']) + 1) // 2
            current_y += num_rows * 8

    data['panels'] = new_panels

    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"Successfully formatted template written to {output_file}")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Format Grafana template JSON file')
    parser.add_argument('input_template', help='Path to input template JSON file')
    parser.add_argument('-o', '--output', help='Path to output file (default: same dir, formatted_template.json)')
    args = parser.parse_args()
    output_path = args.output or os.path.join(os.path.dirname(os.path.abspath(args.input_template)), 'formatted_template.json')
    format_grafana_template(args.input_template, output_path)
```

---

## 2. Deploy Script (deploy.py)

**Purpose**: Deploy dashboard from `template.json` and alert rules from `alerts/rules.json` to Grafana, with staging vs prod and optional prod guard. Support both legacy alerting (alerts in dashboards or Ruler by folder name) and unified alerting (Ruler or Provisioning API by folder UID).

### 2.1 Environment & config

- **Auth**: `GRAFANA_TOKEN` (or e.g. `GRAFANA_V12_TOKEN` for a second instance).
- **URL**: `GRAFANA_URL` (optional; default your Grafana base URL).
- **Prod guard**: require e.g. `DEPLOY_PROD=true` for `--env prod` (use in CI only).
- **Per-env config** (e.g. `ENV_CONFIG["staging"]` / `["prod"]`):
  - `datasource_uids`: list of Prometheus datasource UIDs.
  - `jobs`: dict region → Prometheus job name (e.g. `{"us": "prod-us-myservice"}`).
  - `main_dashboard_uid`, `main_dashboard_title`: for the single metrics dashboard.
  - **Legacy**: `alert_folder` (folder name for Ruler).
  - **Provisioning (v9+)**: `alert_folder_uid` (folder UID for Provisioning API).
  - `dashboard_uid_prefix`, `dashboard_title_prefix`, `tags_prefix`: for per-region alert dashboards/groups.

Obtain datasource UIDs and folder UIDs via a **detect** command: `GET /api/datasources`, `GET /api/folders`, `GET /api/v1/provisioning/alert-rules`.

### 2.2 Commands

| Command | Description |
|---------|-------------|
| `detect` | Print Grafana version, alerting mode, datasources (and optional region→job mapping), contact points. |
| `deploy-dashboard [--env staging\|prod] [--folder-id ID]` | POST template as dashboard; set `dashboard.id=null`, `uid`/`title` from config; optional `folderId`. |
| `deploy-alerts [--env staging\|prod] [--dry-run] [--folder \| --folder-uid]` | Deploy rules per region; substitute `${job}` in each rule's `expr` with `jobs[region]`. |
| `deploy-all [--env ...]` | Run deploy-dashboard then deploy-alerts. |
| `list-alerts` | List alert rules (filter by your service name). |
| `delete-alerts [--env ...]` | Remove deployed alert dashboards or rule groups. |

### 2.3 Dashboard deploy (shared)

- Load `template.json`, set `id: null`, `uid` and `title` from `ENV_CONFIG[env]`.
- `POST /api/dashboards/db` with body: `{ "dashboard": {...}, "overwrite": true, "message": "...", "folderId": <optional> }`.

### 2.4 Alert deploy: legacy (alerts in dashboards)

- For each region: build a hidden dashboard with one "graph" panel per rule; each panel has `alert` with `conditions` (evaluator from rule's `condition`/`threshold`), `for`, `notifications` from a `notifications` map (e.g. severity → channel UID).
- `POST /api/dashboards/db` per region.

### 2.5 Alert deploy: unified (Ruler API, folder by name)

- For each region: build a rule group: `{ "name": "<group>", "interval": "1m", "rules": [ { "grafana_alert": { "title", "condition", "data": [query, reduce, threshold], "no_data_state", "exec_err_state" }, "for", "labels", "annotations" } ] }`. Replace `${job}` in expr with `jobs[region]`.
- `POST /api/ruler/grafana/api/v1/rules/{folder}` with that group; `folder` is the folder **name**.

### 2.6 Alert deploy: unified (Provisioning API, v9+)

- **Do not** use Ruler for instances that require Provisioning (e.g. v12 returns 403). Use folder **UID**.
- For each region: build `AlertRuleGroup`: `{ "title": "<group>", "folderUid": "<uid>", "interval": 60, "rules": [ { "title", "condition", "data", "noDataState", "execErrState", "folderUID", "for", "ruleGroup", "labels", "annotations" } ] }`. Replace `${job}` in each rule's query model with `jobs[region]`.
- `PUT /api/v1/provisioning/folder/{folderUid}/rule-groups/{groupName}` (URL-encode `groupName`).
- Get `folderUid` from `GET /api/folders` or `GET /api/v1/provisioning/alert-rules`; configure it in `ENV_CONFIG` as `alert_folder_uid`.

### 2.7 Detect command

- `GET /api/health` → version, database.
- Alerting mode: try `GET /api/ruler/grafana/api/v1/rules` → unified; else `GET /api/alerts` → legacy.
- Datasources: `GET /api/datasources/uid/{uid}` for each configured UID; match to region by name and map to `jobs[region]`.
- Contact points: legacy `GET /api/alert-notifications`, unified `GET /api/v1/provisioning/contact-points`.

### 2.8 rules.json shape

- `rules`: array of `{ "name", "expr", "condition", "threshold", "for", "severity", "summary" }`.
- `expr` must contain a placeholder (e.g. `${job}`) that the deploy script replaces with the job name per region (alerts cannot use dashboard template variables).
- Optional `notifications`: map `severity → channel UID` (legacy); ignored when using Provisioning.

---

## Summary

- **Format**: Run after every template edit; use the script above or equivalent to normalize IDs and grid.
- **Deploy**: Use detect to fill datasource UIDs and folder UIDs; deploy dashboard via `/api/dashboards/db`; deploy alerts via legacy dashboard, Ruler (folder name), or Provisioning (folder UID) depending on Grafana version. Always staging first, then prod with a guard.
