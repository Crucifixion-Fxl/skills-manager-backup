# NocoDB 表结构：AmazonApi.lingxing

## 项目和表

- **项目**：`AmazonApi`
- **表**：`lingxing`

## 完整字段定义

| 字段 | NocoDB 类型 | 可选值/说明 | 赋值规则 |
|------|------------|------------|----------|
| `Id` | ID | 自增 | 自动生成 |
| `name` | SingleLineText | API 中文名称（必填） | 从文档标题或 sidebar 解析 |
| `path` | LongText | API 路径 | 从文档解析 |
| `table` | SingleLineText | 数仓表名（≤45字符，全小写，下划线连接） | `ods_lx_` + 路径缩写 + 数据后缀（`_di`/`_df`） |
| `business` | MultiSelect | `f`,`c`,`b` | 默认 `f,c,b` |
| `method` | SingleSelect | `POST`,`GET` | 从文档解析 |
| `req_body_var` | MultiSelect | `sid`,`start_date`,`end_date`,`report_date` 等 | 从文档必填参数解析，无则 null |
| `req_body_fixed` | JSON | 固定请求体参数 | 从文档解析，无则 null |
| `is_paged` | Checkbox | 0/1 | 文档有分页参数→1，否则→0 |
| `page_size` | Number | 每页条数 | 默认 100 |
| `schedule` | SingleSelect | `di`,`hi`,`wi` | 统一填 `di`，包括 `_df` 后缀的表也填 `di`。仅 `hi`/`wi` 按实际调度频率区分 |
| `dt_type` | SingleSelect | `date`,`time` | 时间参数为日期→`date`，时间戳→`time`，无则 null |
| `interval_type` | SingleSelect | `is_right_exclusive` | 时间区间左闭右开时填，否则 null |
| `enabled` | Checkbox | 0/1 | 默认 1 |
| `CreatedAt`/`UpdatedAt` | DateTime | 自动维护 | 自动生成 |

## 操作示例

NocoDB 认证和通用操作规范参见 nocodb skill。以下仅列出本表特有的查询模式：

```bash
# 查询所有已录入记录（用于去重）
curl -s "https://nocodb.addx.live/api/v1/db/data/v1/AmazonApi/lingxing?limit=200" \
  -H "xc-token: $NOCODB_TOKEN"

# 按 path 查重
curl -s "https://nocodb.addx.live/api/v1/db/data/v1/AmazonApi/lingxing?where=(path,eq,/erp/sc/data/seller/lists)" \
  -H "xc-token: $NOCODB_TOKEN"

# 新增记录（完整字段，含 name）
curl -s -X POST "https://nocodb.addx.live/api/v1/db/data/v1/AmazonApi/lingxing" \
  -H "xc-token: $NOCODB_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"查询所有站点","path":"/erp/sc/data/seller/allMarketplace","table":"ods_lx_erp_d_seller_all_mkt_df","business":"f,c,b","method":"GET","req_body_var":null,"req_body_fixed":null,"is_paged":0,"page_size":100,"schedule":"di","dt_type":null,"interval_type":null,"enabled":1}'
```
