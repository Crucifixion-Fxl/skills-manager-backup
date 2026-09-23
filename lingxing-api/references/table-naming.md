# table 名缩写规则

表名格式：`ods_lx_` + 路径缩写 + 后缀（`_di`/`_df`/`_hi`/`_wi`），总长 ≤ 45 字符。

## 路径前缀缩写

| 路径前缀 | 缩写 |
|----------|------|
| `erp/sc/routing` | `erp_rt` |
| `erp/sc/data` | `erp_d` |
| `basicOpen` | `bo` |
| `basicOpen/multiplatform/ads` | `bo_mp_ads` |
| `basicOpen/finance` | `bo_fin` |
| `basicOpen/openapi` | `bo_api` |
| `amzStaServer/openapi` | `sta_api` |
| `pb/openapi/newad` | `pb_newad` |
| `pb/openapi` | `pb_api` |
| `promotionApi/open/promotion` | `promo` |
| `inventory/center` | `inv_ctr` |

## 常用词缩写

| 原词 | 缩写 | 原词 | 缩写 |
|------|------|------|------|
| inventory | inv | shipment | ship |
| report/reports | rpt | detail/details | dtl |
| warehouse | wh | storage | stg |
| purchase | po | management | mgmt |
| campaign | cmpn | product | prod |
| keyword | kw | target/targeting | tgt |
| aggregate | agg | summary | sum |
| overseas | ovs | local | loc |
| performance | perf | information | info |
| logistics | logi | transaction | txn |
| negative | neg | recommend | rec |
| monthly | mon | seller | slr |
| statement | stmt | category | cat |
| inbound | inb | delivery | dlvr |

## 缩写优先级

超长时按以下顺序递进：

1. **路径前缀缩写**：匹配上表中的路径前缀
2. **常用词缩写**：替换上表中的长词
3. **去除冗余词**：`get`、`list`、`query`、`open`、`api`、`data`、`all`、`new` 等填充词可移除

**禁止截断**：不得直接截断单词导致不可读。
