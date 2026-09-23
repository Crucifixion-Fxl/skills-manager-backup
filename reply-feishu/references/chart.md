# Chart Component

The chart component renders interactive VChart visualizations inside Feishu message cards. It supports line, bar, pie, area, gauge, and funnel chart types.

## Top-level Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `tag` | string | Yes | Must be `"chart"` |
| `chart_spec` | object | Yes | VChart specification (see below) |
| `aspect_ratio` | string | No | Aspect ratio: `"16:9"`, `"1:1"`, `"2:1"` |
| `preview` | bool | No | Enable click-to-enlarge preview |
| `height` | string | No | Fixed height in pixels, e.g. `"300px"`. Alternative to `aspect_ratio` |

## chart_spec Core Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | string | Yes | Chart type: `"line"`, `"bar"`, `"pie"`, `"area"`, `"gauge"`, `"funnel"`, `"scatter"`, `"treemap"`, `"heatmap"`, `"waterfall"`, `"radar"`, `"circularProgress"`, `"linearProgress"`, `"wordCloud"` |
| `data` | array | Yes | Data source: `[{"values": [...]}]` |
| `xField` | string | Cartesian | X-axis data key (for line, bar, area) |
| `yField` | string | Cartesian | Y-axis data key (for line, bar, area) |
| `categoryField` | string | Non-cartesian | Category key (for pie, funnel — used instead of `xField`) |
| `valueField` | string | Non-cartesian | Value key (for pie, funnel — used instead of `yField`) |
| `seriesField` | string | No | Key for multi-series grouping (splits data into multiple lines/bars) |
| `title` | object | No | Chart title: `{"text": "My Chart"}` |
| `legends` | object | No | Legend config: `{"visible": true}` |
| `color` | array | No | Color palette, e.g. `["#4e79a7", "#f28e2b"]` |
| `axes` | array | No | Axis config array (see common patterns) |
| `stack` | bool | No | Enable stacking for bar/area charts |

## Line Chart

Time series or continuous data. Uses `xField` and `yField`.

```json
{
  "tag": "chart",
  "aspect_ratio": "16:9",
  "preview": true,
  "chart_spec": {
    "type": "line",
    "title": {
      "text": "API Latency (ms)"
    },
    "data": [
      {
        "values": [
          { "time": "10:00", "latency": 120 },
          { "time": "10:15", "latency": 135 },
          { "time": "10:30", "latency": 98 },
          { "time": "10:45", "latency": 142 },
          { "time": "11:00", "latency": 110 }
        ]
      }
    ],
    "xField": "time",
    "yField": "latency"
  }
}
```

## Bar Chart

Categorical comparison. Uses `xField` and `yField`.

```json
{
  "tag": "chart",
  "aspect_ratio": "16:9",
  "chart_spec": {
    "type": "bar",
    "title": {
      "text": "Deployments by Environment"
    },
    "data": [
      {
        "values": [
          { "env": "staging", "count": 42 },
          { "env": "pre", "count": 18 },
          { "env": "prod", "count": 7 }
        ]
      }
    ],
    "xField": "env",
    "yField": "count",
    "color": ["#4e79a7", "#f28e2b", "#e15759"]
  }
}
```

## Pie Chart

Proportional data. Uses `categoryField` and `valueField` instead of x/y.

```json
{
  "tag": "chart",
  "aspect_ratio": "1:1",
  "chart_spec": {
    "type": "pie",
    "title": {
      "text": "Error Distribution"
    },
    "data": [
      {
        "values": [
          { "type": "Timeout", "count": 45 },
          { "type": "5xx", "count": 30 },
          { "type": "4xx", "count": 25 }
        ]
      }
    ],
    "categoryField": "type",
    "valueField": "count",
    "legends": {
      "visible": true
    }
  }
}
```

## Area Chart

Like a line chart but with a filled region beneath the line. Uses `xField` and `yField`.

```json
{
  "tag": "chart",
  "aspect_ratio": "16:9",
  "chart_spec": {
    "type": "area",
    "title": {
      "text": "Memory Usage (MB)"
    },
    "data": [
      {
        "values": [
          { "time": "00:00", "memory": 512 },
          { "time": "06:00", "memory": 640 },
          { "time": "12:00", "memory": 890 },
          { "time": "18:00", "memory": 720 },
          { "time": "24:00", "memory": 580 }
        ]
      }
    ],
    "xField": "time",
    "yField": "memory",
    "color": ["#59a14f"]
  }
}
```

## Gauge Chart

Single metric display. Uses special fields for the arc shape. Data format is `[{values: [{value: N}]}]`.

```json
{
  "tag": "chart",
  "aspect_ratio": "1:1",
  "height": "200px",
  "chart_spec": {
    "type": "gauge",
    "title": {
      "text": "CPU Usage"
    },
    "data": [
      {
        "values": [
          { "value": 75 }
        ]
      }
    ],
    "startAngle": -225,
    "endAngle": 45,
    "pointer": {
      "visible": true
    }
  }
}
```

## Funnel Chart

Conversion or pipeline stages. Uses `categoryField` and `valueField`.

```json
{
  "tag": "chart",
  "aspect_ratio": "16:9",
  "chart_spec": {
    "type": "funnel",
    "title": {
      "text": "CI/CD Pipeline"
    },
    "data": [
      {
        "values": [
          { "stage": "Commits", "count": 120 },
          { "stage": "Builds", "count": 95 },
          { "stage": "Tests Passed", "count": 78 },
          { "stage": "Deployed", "count": 42 }
        ]
      }
    ],
    "categoryField": "stage",
    "valueField": "count",
    "legends": {
      "visible": true
    }
  }
}
```

## Common Patterns

### Multi-series Line Chart

Add `seriesField` to split data into multiple lines by a category key. Each unique value in the series field becomes a separate line.

```json
{
  "tag": "chart",
  "aspect_ratio": "16:9",
  "chart_spec": {
    "type": "line",
    "title": {
      "text": "Latency by Service"
    },
    "data": [
      {
        "values": [
          { "time": "10:00", "latency": 120, "service": "gateway" },
          { "time": "10:00", "latency": 85, "service": "worker" },
          { "time": "10:30", "latency": 135, "service": "gateway" },
          { "time": "10:30", "latency": 90, "service": "worker" },
          { "time": "11:00", "latency": 110, "service": "gateway" },
          { "time": "11:00", "latency": 75, "service": "worker" }
        ]
      }
    ],
    "xField": "time",
    "yField": "latency",
    "seriesField": "service",
    "legends": {
      "visible": true
    },
    "color": ["#4e79a7", "#f28e2b"]
  }
}
```

### Stacked Bar Chart

Add `stack: true` along with `seriesField` to stack bars on top of each other.

```json
{
  "tag": "chart",
  "aspect_ratio": "16:9",
  "chart_spec": {
    "type": "bar",
    "title": {
      "text": "Incidents by Severity"
    },
    "data": [
      {
        "values": [
          { "month": "Jan", "count": 5, "severity": "P0" },
          { "month": "Jan", "count": 12, "severity": "P1" },
          { "month": "Feb", "count": 3, "severity": "P0" },
          { "month": "Feb", "count": 8, "severity": "P1" },
          { "month": "Mar", "count": 1, "severity": "P0" },
          { "month": "Mar", "count": 6, "severity": "P1" }
        ]
      }
    ],
    "xField": "month",
    "yField": "count",
    "seriesField": "severity",
    "stack": true,
    "legends": {
      "visible": true
    },
    "color": ["#e15759", "#f28e2b"]
  }
}
```

### Dual Axis

Use the `axes` array to configure two Y-axes with `orient: "left"` and `orient: "right"`.

```json
{
  "tag": "chart",
  "aspect_ratio": "16:9",
  "chart_spec": {
    "type": "line",
    "data": [
      {
        "values": [
          { "time": "10:00", "requests": 1200, "errors": 3 },
          { "time": "10:30", "requests": 1500, "errors": 7 },
          { "time": "11:00", "requests": 1100, "errors": 2 }
        ]
      }
    ],
    "xField": "time",
    "yField": "requests",
    "axes": [
      { "orient": "left", "title": { "text": "Requests" } },
      { "orient": "right", "title": { "text": "Errors" } },
      { "orient": "bottom" }
    ]
  }
}
```
