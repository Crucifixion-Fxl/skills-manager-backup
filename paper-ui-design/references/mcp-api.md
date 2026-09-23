# Paper MCP 工具调用规范

Paper MCP 工具均为 deferred tools，**调用前必须通过 ToolSearch 加载**，否则参数签名不匹配导致报错。

## 正确签名

### create_artboard

```json
{
  "name": "Login",
  "styles": {
    "width": "375px",
    "height": "812px",
    "backgroundColor": "#F5F8F5"
  }
}
```

- `width` / `height` 必须在 `styles` 内，且为**字符串**（`"375px"`），不能传数字
- 默认尺寸：移动端 390×844，桌面 1440×900

### write_html

```json
{
  "targetNodeId": "DL-0",
  "html": "<div>...</div>",
  "mode": "insert-children"
}
```

- 参数名是 `targetNodeId`（不是 `nodeId`）
- `mode`：`insert-children` 追加子节点；`replace` 替换目标节点

### update_styles

```json
{
  "updates": [
    {
      "nodeIds": ["DL-0"],
      "styles": { "display": "flex", "flexDirection": "column" }
    }
  ]
}
```

- 必须用 `updates` 数组格式，不能平铺 `nodeId + styles`

## Artboard 布局陷阱

`create_artboard` 创建的画板默认无布局，**必须立即调用 `update_styles` 设置 flex column**，否则所有子元素堆叠在左上角：

```json
{
  "updates": [{
    "nodeIds": ["<artboard-id>"],
    "styles": { "display": "flex", "flexDirection": "column", "alignItems": "center" }
  }]
}
```

## 默认尺寸参考

| 设备 | 宽 × 高 |
|------|---------|
| 移动端 | 390 × 844 |
| 平板 | 768 × 1024 |
| 桌面 | 1440 × 900 |
