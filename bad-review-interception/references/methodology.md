# 如何在 analytics 库中定位"异常埋点"—— 方法论

> 这是一份**可复用的 playbook**，把本轮盘点的踩坑和有效路径沉淀下来，下次要做类似异常事件盘点（新增业务/新增 App/新品类）时直接照着跑。

## 核心结论

**不要只靠表名 grep**。必须「**表名匹配 + 字段语义搜索**」双轨并行再合并去重。单纯依赖表名会漏掉最有价值的那一类——**在成功事件里同时埋了 `error_code` 字段的表**（例如 `flutter_home_device_bind_ing_hi`、`flutter_home_join_live_hi`、`smart_camera_device_unbind_hi` 这种）。

## 数仓结构前置知识

`analytics` 库的埋点相关表分几层（从原始到聚合）：

| 层 | 命名 | 数量 | 用途 |
|---|---|---|---|
| **DWD** 原始事件 | `dwd_event_<app>_<event>_hi` | ~1763 | 一事件一表，字段最全。**是盘点的主要目标**。 |
| **vDWD** 虚拟视图 | `vdwd_page_*_hi` | ~58 | 对 dwd_event 的视图层，字段已做 rename/白名单。可作为 dwd_event 的补充查询入口。 |
| **DWM** 中间聚合 | `dwm_*_hi` | ~11 | 按主题聚合，schema 统一为 `user_id/event_vendor/name/type/priority/start_time/end_time/duration_seconds/sn/dt`。当前只有 4 张和异常相关（`dwm_exception_category_hi` / `dwm_exception_device_power_on_hi` / `dwm_exception_device_ota_hi` / `dwm_network_offline_exception_hi`），**远不够覆盖所有异常类别**。 |
| **DWS** 汇总 | `dws_exception_unified_hi` | 1 | 对上面 4 张 dwm 的合并。**不完整**，不要当作异常的 single source of truth。 |
| **ADS** 应用层 | `ads_*_hi` | ~23 | 指标/报表专用，通常是某张 dwm/dws 的进一步聚合，偶尔有专用异常表（如 `ads_analytics_safertc_connection_exception_di`）。 |

**踩坑**：看到有 `dws_exception_unified_hi` 这种名字非常诱人的表时不要直接 trust。要先看 schema + 看它实际聚合了哪几张 dwm，再决定能不能作为入口。

## DataHub Schema Search 服务关键行为

服务：`https://datahub-schema-search.addx.live`（Swagger：`/docs`）

| 接口 | 是否可靠 | 备注 |
|---|---|---|
| `GET /api/databases` | ✅ | 列所有库 |
| `GET /api/tables?database=analytics` | ✅ | 列库下所有表名（**无字段**），analytics 库 1943 张 |
| `GET /api/tables/{full_name}` | ⚠️ **部分 dwd_event_* 返回 404** | 详情索引对 `analytics.dwd_event_*` 不全，很多表查不到 |
| `GET /api/search?query=X&database=Y&top_k=N` | ✅ | **向量语义搜索**，返回表名 + schema（含字段列表）。这是获取 dwd_event 字段的**主要方式**。`top_k` 最大 100。 |

**关键点：向量搜索用 `query=<字段名>` 可以搜字段内容**。例如 `query=error_code` 会返回所有 schema 里出现 `error_code` 的表（不仅仅是字段名叫 error_code 的，也包括描述里提到的）。需要自己做精确过滤。

## 标准两轮流程

### Round 1 — 表名匹配（离线、快）

```bash
# 拿到库下全部表
curl -s 'https://datahub-schema-search.addx.live/api/tables?database=analytics' > analytics_tables.json

# 本地 python 过滤
python3 -c "
import json, re
with open('analytics_tables.json') as f:
    data = json.load(f)
dwd_event = [t['table'] for t in data['tables'] if t['table'].startswith('dwd_event')]
kw = re.compile(r'(fail|failed|failure|error|exception|timeout|abort|crash|disconnect|invalid|denied|unreachable|unavailable|unsupport|stuck|lost)', re.I)
print(len(dwd_event), 'dwd_event_* tables total')
print(len([t for t in dwd_event if kw.search(t)]), 'matched')
"
```

**关键词白名单**（亲测有效）：
`fail | failed | failure | error | exception | timeout | abort | crash | disconnect | invalid | denied | unreachable | unavailable | unsupport | stuck | lost`

**有意排除**的歧义关键词：`reset | retry | warn | reject` —— 这几个更多是正常交互（retry 按钮点击、密码 reset 页、警告提示），假阳性太多，建议单独人工 review 而不是直接合入主清单。

**进一步清洗**：过滤掉 UI 层事件（表名以 `_btn_click_hi/_dialog_hi/_toast_hi/_popup_hi/_alert_hi/_page_hi/_banner_hi` 结尾的）——这些是"用户看到错误 UI"而不是"错误发生"，拦截场景下两者都有价值但是逻辑不一样，建议分开收。

### Round 2 — 字段语义搜索（在线、批量）

```bash
# 对 18 个字段关键词分别查询，合并去重
for q in error_code error_msg errcode errmsg err_code err_msg \
         fail failure exception abort crash status_code result_code \
         错误码 错误信息 失败原因 异常 异常类型; do
  curl -s --max-time 60 -o "hits_$q.json" \
    "https://datahub-schema-search.addx.live/api/search?query=$(python3 -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1]))" "$q")&database=analytics&top_k=100"
done
```

**踩坑**：
- **不要用 bash pipe 直接 parse JSON**。DataHub 的响应体经常上百 KB，`subprocess.run` 或 `curl | python` 会在 pipe buffer 里被截断导致 JSON 解析失败。一律 `curl -o 文件` 再从文件里读。
- 用 `--max-time 60` 防悬挂。
- 某些关键词（`failed`、单字的 `fail`）可能返回 0 结果——不是数据没有，是向量嵌入对短词匹配偏弱。**多试几个近义词/语种**（中英文都发），取并集。

### Round 3 — 严格字段过滤（必做）

向量搜索返回的 schema 里会包含很多**继承字段**和 **DBT 中间字段**，必须过滤掉：

```python
import re
# 只保留「领域字段」
domain_kw = re.compile(r'^(error_code|error_msg|errcode|errmsg|err_code|err_msg|fail_reason|failure|exception|abort|crash|result_code|status_code|reason)', re.I)
# 排除基础 schema 继承 + DBT 临时/变更
base_noise = re.compile(r'(_com_base_base_schema|__dbt_alter|__dbt_tmp)', re.I)

# 从 schema blob 里提取字段名（"  - field_name (TYPE): description" 格式）
field_rx = re.compile(r'^  - (\w+)', re.M)
fields = field_rx.findall(schema_text)
domain_fields = [f for f in fields if domain_kw.match(f) and not base_noise.search(f)]
```

**为什么必须过滤 `_com_base_base_schema`**：`error_msg_com_base_base_schema` 这种字段是**所有 tracker 事件**从基础 schema 继承来的通用槽位，几乎每张 `dwd_event_*` 都有。不过滤的话，字段匹配会误伤到 90%+ 的正常事件。

**为什么必须过滤 `__dbt_alter` / `__dbt_tmp`**：DBT 在做 schema 迁移时产生的临时中间字段，不是真实的埋点字段。

### Round 4 — 合并去重 + 三路标记

```python
union = name_hits | field_hits
# 标记每张表的命中来源
for fn in union:
    if fn in name_hits and fn in field_hits: src = 'name+field'  # 最高置信
    elif fn in name_hits: src = 'name-only'                        # 典型 fail/error 表
    else: src = 'field-only'                                       # 新发现——成功事件里埋了 error_code
```

**field-only 的那一批是最值钱的**。表名看不出异常，但 schema 里有 `error_code_event`/`result_code_event`/`reason_event` 等字段，意味着这个事件同时追踪成功和失败两条路径。例子：
- `flutter_home_device_bind_ing_hi` —— 绑定进行中事件，里面有 `error_code_event`、`sys_err_code_event`。对差评拦截来说比 `bind_fail_hi` 更早一步触达用户。
- `flutter_home_join_live_hi` —— 加入直播事件带 `error_code_event`。
- `smart_camera_device_unbind_hi` —— 解绑事件带 `reason_event`，可以追查"为什么退订/为什么解绑"。

## 输出格式建议

至少这些列，方便下游同学用：

```
source | layer | app | event | domain_error_fields | full_name
```

- `source` ∈ `{name, field, name+field}` —— 合并来源
- `layer` ∈ `{dwd, vdwd, dwm, dws, ads}` —— 数仓分层
- `app` —— 归属 App/模块（`smart_camera` / `flutter_home` / `smart_device` / `iot_service` / ...）
- `event` —— 去掉 `dwd_event_<app>_` 前缀之后的事件名
- `domain_error_fields` —— 清洗后的领域错误字段名（便于写 SQL where `error_code IS NOT NULL`）
- `full_name` —— 完整表名（含 database）

**不要**把 `dt` / `session_id` / `user_id` 这些通用维度字段放进 domain_error_fields。

## 下游分析套路

拿到清单后，下一步通常是三件事：

### 1. 频次排序（Superset）

```sql
-- 示例：查某张表近 7 天的日均触发量 + 影响用户数
SELECT dt,
       COUNT(*)          AS occurrences,
       COUNT(DISTINCT user_id) AS affected_uv
FROM analytics.dwd_event_flutter_home_bind_fail_hi
WHERE dt >= date '<today-7>'
GROUP BY dt
ORDER BY dt DESC
LIMIT 100
```

对 N 张表做 batch 统计时，**必须带 dt 分区过滤 + LIMIT**，否则 Athena/Superset 查询成本爆炸。

### 2. 对成功事件里的错误码做筛选

```sql
SELECT error_code_event,
       COUNT(*) AS cnt,
       COUNT(DISTINCT user_id) AS uv
FROM analytics.dwd_event_flutter_home_join_live_hi
WHERE dt >= date '<today-7>'
  AND error_code_event IS NOT NULL
  AND error_code_event != ''
  AND error_code_event != '0'
GROUP BY error_code_event
ORDER BY cnt DESC
LIMIT 50
```

### 3. 跨 App 归一化

同一个事件（如 `bind_fail`）可能在 3 个 App 里都有埋点（`smart_camera`/`flutter_home`/`smart_device`），对比三家的 error_code 分布 —— 高覆盖事件优先做拦截规则。

## 工具链汇总

| 工具 | 用途 | 入口 |
|---|---|---|
| DataHub Schema Search | 列表/查详情/语义搜索 | `https://datahub-schema-search.addx.live` |
| Superset | 跑 SQL 出数 | `https://superset-us.addx.live`（需要 SUPERSET_USERNAME/PASSWORD env） |
| tracker-manager | 埋点平台 SSOT（event_id ↔ event_name ↔ 描述） | `https://us-tracker-management.theunismart.com` |
| skill `addx-engineering:datahub-schema-search` | 封装了上面的 API 调用 | 本仓库 Claude 环境内可直接调用 |
| skill `addx-engineering:superset` | 封装 Superset JWT 登录 + SQL 执行 | 同上 |

## Checklist — 下次再做异常盘点时

- [ ] 先看 `dws_*` / `dwm_*exception*` 里有没有现成的合并层，有的话快速评估覆盖率够不够
- [ ] 拉全量 `dwd_event_*` 表名，用白名单关键词做名字过滤
- [ ] 18 个字段关键词（含中英文）对 DataHub 做向量搜索，curl 写文件避免 buffer 截断
- [ ] 严格过滤 `_com_base_base_schema` / `__dbt_alter` / `__dbt_tmp`
- [ ] 合并去重 + 按 source 三路标记
- [ ] 按 App 归类，剔除纯服务端埋点（`iot_service/iot_local/kiss_safertc/safertc_*/oauth2_*`）如果目标是用户可感知场景
- [ ] 按事件名归一化，识别跨 App 通用事件作为高优先级拦截候选
- [ ] Superset 跑频次，确定 Top-N
- [ ] 反查 tracker-manager 拿 `event_id` 补全清单
