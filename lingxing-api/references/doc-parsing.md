# 领星文档解析注意事项

## 必填字段可能含 HTML 标签

文档中「必填」列的值可能被 HTML 标签包裹（如 `<font color="red">是</font>`），解析时必须先 strip 所有 HTML 标签，再判断纯文本是否为 `是`。

## 仅录入必填参数

只录入文档中标记为「必填=是」的参数（strip HTML 后判断），非必填参数不录入 `req_body_var`。

## 分页参数排除

以下参数不写入 `req_body_var`，但用于判定 `is_paged=1`：

`offset`、`length`、`limit`、`page`、`pageSize`、`page_size`、`page_no`、`pageNum`、`pageNo`、`size`、`paging`

## 非必填更新时间参数

部分 API 有非必填的更新时间类参数（如 `update_time_start`、`listing_update_start_time` 等）。**不要自动将这些参数加入 `req_body_var`**，仅在用户明确要求时才录入。`req_body_var` 严格只包含文档标记为「必填=是」的非分页参数。
