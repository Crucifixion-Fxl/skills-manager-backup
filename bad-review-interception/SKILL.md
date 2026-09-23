---
name: bad-review-interception
description: customer-care 项目差评拦截能力的 data-driven 四步闭环编排器——发现异常埋点 → 按 error_code 级统计频次 → 配 SmartPopup 规则 + 多语言自助文档 → 跑 A/B 实验并追踪 scene 专属 proximal metric（绑定 scene 看绑定成功率、直播 scene 看直播成功率…），然后把结论回灌到下一轮的先验权重。只要用户在 customer-care 上下文里提到"差评拦截"、"异常拦截"、"拦截规则"、"SmartPopup 规则"、"分析异常埋点"、"看拦截实验效果"、"新增 X 场景拦截"中的任一个，即使他没明说"走流程"，都应该调用本 skill——因为这类工作里人类最容易跳过频次统计、直接拿差评率当唯一指标、或忽略上一轮的 lessons-learned，skill 的价值就是拦住这些 shortcut 并强制数据链闭环。不用于一次性 bug 排查、纯 UI 调整、或跟差评拦截无关的埋点分析。
---

# Bad Review Interception

## Description

差评拦截是 customer-care 的核心能力：在用户遇到异常、情绪接近"去 Amazon 留差评"的临界点时，用 SmartPopup 主动弹出自助方案，把售后行为从 Amazon 站内转到 App 内。

本 skill 编排这个能力的 **data-driven 四步闭环**——关键词是 "data-driven"：每一步都有明确的输入数据工件和产出数据工件，Step ④ 的结论必须回灌为下一轮的先验，不允许任何一步靠拍脑袋决策。

```
 ① 发现 ───► ② 统计 ───► ③ 配规则 ───► ④ 看 A/B ───► lessons-learned.md ─┐
 │          │            │             │                                  │
 exception- error-code-  rule-config   experiment                         │
 trackers   frequency    + growthbook  results                            │
 .tsv       .md          flag          + proximal/distal metrics          │
 ▲                                                                        │
 └──── 反向回灌：下调失败 scene 优先级 + 更新 gotchas + 刷新 baseline ◄────┘
```

本 skill 不重新实现 API——每一步都委托现成的原子 skill（`datahub-schema-search` / `superset` / `marketing-cms` / `growthbook` / `gitlab-mr` / `tracker-manager`）。它的价值在于**把 4 步用数据工件串起来，并在每一步拦住常见 shortcut**（跳过频次、越过 scene 定义、无视上轮教训等）。

---

## 被调用时先做什么

用户很少会按顺序从 ① 开始——大部分时候他们会从中间某一步切入（"帮我算一下 bind_fail 的频次"、"看下那个 4G 拦截实验结果"）。**不要机械地从 Step ① 开跑**。先判断当前在哪一步：

| 如果用户说 / 现状是 | 从这步开始 |
|---|---|
| "有哪些异常值得拦截" / `exception-trackers.tsv` 不存在或超过 30 天 | Step ① |
| 已有清单但没有频次数据 / "算一下 X 事件的 error_code" | Step ② |
| 已有 Top N 清单，PM 准备配规则 / "加一个 X 场景的拦截" | Step ③ |
| 规则已灰度，需要看效果 / "看下 X 拦截实验结果" | Step ④ |
| 不确定 | 先扫 `customer-care/docs/architecture/smart_popup/analysis/` 看现有产物，再问用户要推进哪一步 |

**跨步回捞**：Step ③/④ 中途发现上游数据有问题（比如 error_code 分布和 Step ② 跑的不一致），允许回到 ②，但必须告诉用户"我要回捞到 Step ②，原因是 XXX"，不要默默重跑。

---

## Rules

这些是**不可协商**的底线——每一条背后都有过去的教训，破坏任一条会让能力失效。

### 1. 数据驱动的四步闭环

四步必须走完，且**每一步的决策必须基于前一步产生的数据工件**，不能凭感觉插入或跳过。**数据链**（都是 git 追踪的文件，不是口头约定）：

```
exception-trackers.tsv  →  error-code-frequency.md  →  rule-config + growthbook  →  lessons-learned.md
       ①                         ②                            ③                           ④
                                                                                           │
                                          ◄──── 反向回灌：下调失败 scene 的 priority_weight ──┘
```

**为什么**：
- 跳过 Step ② 直接配规则 = 在猜哪些异常重要。SmartPopup 规则预算有限（规则包大小影响启动性能，团队实际维护的规则条数在两位数），没有频次数据支撑的规则命中率经验上 <20%。
- 跳过 Step ④ 的 lessons-learned 回灌 = 同样的错会犯第二次。Step ④ 结论（某 scene 下调优先级、某 copy 模式无效、某 error_code 其实是 SDK 噪声…）必须写回 `lessons-learned.md`，下一轮 Step ②/③ 必须读它调整权重。
- 口头传承不算数——"上次我们试过那个方向不行" 这种话，如果不写进 lessons-learned.md，三个月后新同学加入就会重复踩坑。

**唯一例外**：用户明确说"我已经有 Step X 的数据了"，可以从 X+1 开始，但必须先校验他的数据符合上一步产出的 schema（`(event, error_code)` 颗粒度、带 `scene` 和 `proximal_metric` 字段等）。

### 2. 颗粒度必须到 `(event, error_code)` 对

同一张 `bind_fail_hi` 表里，`code=10001`（路由器 mDNS 关闭）和 `code=50007`（SDK 瞬断自动恢复）的根因、用户感知、解决方案完全不同。把它们合并成一条"绑定失败"规则，会给 50007 的用户弹一个他根本不关心的弹窗——这就是骚扰。

### 3. 拦截必须伴随自助方案

每条规则产出时必须同时具备 3 样东西，缺一不能上线：

- **用户现象**：客服视角的 1 句话描述
- **自助步骤**：≤3 步，每一步都是用户自己能做的操作（"检查 WebSocket 连接"不算，"重启路由器"算）
- **降级兜底**：客服工单 / Zendesk 入口，给自助失败的用户

只拦不解决 = 加速骚扰。用户遇到异常时情绪已经在临界，一个"哎呀出问题了请联系客服"的弹窗只会加速他去 Amazon。

### 4. 强制灰度 5% → 50% → 100%

每个阶段观察 ≥24h，盯**客服工单率**和**弹窗崩溃率**有没有反向。直接推全量的规则即使最终方向对，也可能因为 copy 没打磨或触发时机错被 90% 用户看到一次烂弹窗——负面认知比没拦截还糟。

### 5. 每个 scene 必须有自己的 proximal metric

scene 是按业务场景划分的（绑定 / 直播 / 支付 / OTA / 4G VIP 购买 / 登录 等）。每个 scene 都有一个**直接衡量该业务是否成功**的指标，这才是 A/B 实验的核心观测对象：

| Scene | Proximal Metric |
|---|---|
| 绑定 (binding) | `binding_success_rate`（触发拦截后该用户 24h 内是否完成绑定） |
| 直播 (live) | `live_success_rate`（触发后同 session 内是否看成一次直播） |
| 支付 (payment) | `payment_success_rate`（触发后 24h 内是否完成付款） |
| OTA / 固件升级 | `ota_success_rate`（触发后 7 天内是否成功升到新版本） |
| 4G VIP 购买 | `vip_purchase_conversion`（触发后 24h 内是否完成订阅） |
| 登录 / 注册 | `login_success_rate`（触发后是否登录成功） |
| Alexa 连接 | `alexa_link_success_rate`（触发后是否绑定 Alexa 账号成功） |

**为什么**：proximal metric 是 leading indicator——规则有没有帮到用户，在这个指标上立刻能看出来。如果只看差评率，你要等 7 天甚至 14 天才能收到足够的评价样本，而且差评和拦截之间隔着用户的主观决定，信号非常稀释。proximal metric 直接告诉你"这个 scene 下的业务指标有没有动"。

新增 scene 时必须先定义它的 proximal metric，找不到合适的 proximal metric 说明这个 scene 本身就不该拦截（没有可衡量的"成功"定义）。

### 6. 差评率是 distal 兜底，不是唯一指标

差评率 + 客服工单率 + 留存率是**distal 指标**——它们慢、嘈杂，但最终反映用户体验。proximal metric 可能在"点击率上去但用户被骚扰了"的情况下误报正向，所以任何 Step ④ 决策都必须**同时**看：

1. scene 专属 proximal metric（是否解决了具体问题）
2. 差评率 / 客服工单率（是否改善了 distal 结果）
3. 弹窗 CTR / 完成率（过程指标，用于 debug，不参与决策）

CTR 涨了但 proximal metric 不动甚至下跌，往往意味着**规则在骚扰正常用户**——这种情况必须下线。

---

## 数据工件（Data Loop 的载体）

四步之间用以下文件串起来——没有这些文件就没有数据驱动，全凭记忆和口头传承。**每轮迭代都要读所有这些文件，不要只关心当前步**。

| 文件 | 产出者 | 消费者 | 内容 | 更新频率 |
|---|---|---|---|---|
| `docs/architecture/smart_popup/analysis/exception-trackers/exception-trackers.tsv` | Step ① | Step ② | 候选异常埋点清单（`source \| layer \| app \| event \| domain_error_fields \| full_name`） | 月度全量 + 新埋点增量 |
| `docs/architecture/smart_popup/analysis/error-code-frequency.md` | Step ② | Step ③ | Top N `(event, error_code)` 按 `occ × uv × priority_weight` 排序 + 业务描述 + `scene` 字段 | 每次跑 Step ② 重建 |
| `customer-care/app/smart_popup_rules/<scene>_<error_code>.rule` + GrowthBook flag `intercept_<scene>_<code>` | Step ③ | Step ④ | 规则字节码 + 多语言 copy + 实验分桶配置 | 每条新规则一次 |
| `docs/architecture/smart_popup/analysis/lessons-learned.md` | Step ④ | 下一轮 Step ①② | 失败案例、无效 copy 模式、应下调的 scene、发现的新 gotcha、**每个 scene 的 priority_weight** | 每次 Step ④ 决策 |

**`lessons-learned.md` 的 schema**（这是数据链的收口，特别重要）：

```markdown
## scene priority weights
| scene | weight | 理由 |
|---|---|---|
| binding | 1.0  | 默认 |
| live    | 1.2  | Q1 实验 4002 +18% 差评率 → 提升优先级 |
| payment | 0.7  | Q1 实验 3 条规则均中性 → 下调 |
| ota     | 0.0  | 完全不拦 → 用户在 OTA 过程已经在等待，拦截反而 noise |

## 失败案例
- YYYY-MM-DD | intercept_payment_5002 | proximal +2% 非显著 + 差评率 +3% → 下线。假设"显示 error 详情增加信任"不成立。
- ...

## 无效 copy 模式
- "技术用语残留" —— 弹窗里出现 "mDNS" / "WebSocket" 等词，用户恐惧 +，CTR 反降
- ...

## 新发现的 gotcha（补 methodology.md）
- `dwd_event_*_notification_message_hi` 这批表的 error_msg 里其实是 push payload，不是真实错误
- ...
```

Step ② 跑频次时必须读 `scene priority weights` 做加权；Step ③ 配规则时必须读 `无效 copy 模式`；Step ④ 出结论时必须追加一条新的 entry。

---

## Gotchas（数据层的前置认知）

这些不是行为规则，而是数据仓库的**事实**。不知道会在 Step ① 产出错误的清单：

- **数据源是 `analytics.dwd_event_*`**（约 1700+ 张，持续增长）。不要去 `tracking.*` 库，那是旧的、部分覆盖的命名空间。分层：`dwd → vdwd → dwm → dws → ads`。
- **`dws_exception_unified_hi` 不是全量**。名字诱人，但只 union 了 4 张 `dwm_*exception*_hi`（`device_power_on` / `device_ota` / `network_offline` / `category`），缺 binding/live/payment/auth 这些高价值类别。当参考源之一就行。
- **字段语义搜索必须过滤 3 类噪声**，否则 `error_msg_com_base_base_schema` 会误伤 90%+ 正常事件：
  - `*_com_base_base_schema`（基础 schema 继承槽位，几乎每张表都有）
  - `*__dbt_alter`（DBT 迁移中间字段）
  - `*__dbt_tmp`（DBT 临时字段）
- **DataHub `/api/tables/{full_name}` 对 `dwd_event_*` 返回 404**，但 `/api/search?database=analytics` 能拿到字段。用 search 做主入口。
- **`curl | python` 解析 DataHub 响应会被 pipe buffer 截断**（返回体动辄几百 KB），必须 `curl -o 文件` 再 parse。

完整采集脚本和踩坑记录在 [references/methodology.md](references/methodology.md)。

---

## Step ① 发现异常埋点

**委托**：`datahub-schema-search`

**核心动作**：对 `analytics.dwd_event_*` 做**两轮匹配再合并**：

1. **表名匹配**：白名单关键字 `fail|failed|failure|error|exception|timeout|abort|crash|disconnect|invalid|denied|unreachable|unavailable|unsupport|stuck|lost`；**排除** `reset|retry|warn|reject`（容易误伤正常交互）。
2. **字段语义搜索**：对 18 个关键字（`error_code` / `error_msg` / `errcode` / `errmsg` / `err_code` / `err_msg` / `fail` / `failure` / `exception` / `abort` / `crash` / `status_code` / `result_code` / `错误码` / `错误信息` / `失败原因` / `异常` / `异常类型`）分别 query，top_k=100，过滤 Gotchas 里的 3 类噪声字段。
3. **合并去重**，按 source 三路标记：`name-only` / `field-only` / `name+field`。`field-only` 是最值钱的——例如 `flutter_home_device_bind_ing_hi` 在成功路径里埋了 `error_code_event`，比 `bind_fail_hi` 更早一步触达用户。

**产物**：`customer-care/docs/architecture/smart_popup/analysis/exception-trackers/exception-trackers.tsv`，含 `source | layer | app | event | domain_error_fields | full_name`。diff 本轮结果 vs 上轮清单，向用户报告新增项。

完整脚本见 [references/methodology.md](references/methodology.md)。

---

## Step ② 统计 error_code 级频次

**委托**：`superset`（跑 SQL）+ `tracker-manager`（反查 event_id 和描述）

**第 0 步（必做）**：读 `lessons-learned.md` 的 `scene priority weights` 表——这决定了后面排序时用的权重。新 scene 没有记录就默认 weight=1.0。读 `无效 copy 模式` 和 `新发现的 gotcha`，避免本轮重复踩坑。

**SQL 模板**（对每张候选 `dwd_event_*_hi` 跑；必须带 `dt` 分区过滤 + `LIMIT`，否则 Athena 查询成本爆炸）：

```sql
SELECT COALESCE(error_code_event, error_code, 'NULL') AS error_code,
       COALESCE(error_msg_event,  error_msg,  '')    AS error_msg,
       COUNT(*)                                      AS occurrences,
       COUNT(DISTINCT user_id)                       AS affected_uv,
       date(dt)                                      AS day
FROM analytics.dwd_event_<app>_<event>_hi
WHERE dt >= date '<today - 7 days>'
  AND (error_code_event IS NOT NULL OR error_code IS NOT NULL)
GROUP BY 1, 2, 3
ORDER BY occurrences DESC
LIMIT 100
```

**两种表要分开处理**：

- 表名本身就带 `fail/error`（如 `bind_fail_hi`）：那张表所有行都是失败，直接按 `error_code` 分组，不需要过滤 `IS NOT NULL`
- 成功事件里有 `error_code` 字段（如 `device_bind_ing_hi`）：必须加 `error_code IS NOT NULL AND error_code != '0'` 筛出异常路径

**合并排序**：按 `occurrences × affected_uv × scene_priority_weight`（权重从 lessons-learned 读）加权，出 **Top 100 `(event, error_code)` 对**。被 lessons-learned 明确下调到 weight=0 的 scene 直接跳过，不进 Top N。

**业务甄别**（必做）：Top 100 里剔除"用户不可感知"的 error_code——safertc 内部重连码、dbt 数据治理码、SDK 初始化临时码等。判断标准：找客服和 App 研发对齐，能否对应到"用户能描述的现象"。对应不上的直接剔除或放"技术监控"池，不进拦截候选池。

**补齐 event_id**：每张 DataHub 表的描述字段里有 `[埋点平台](...spm/edit/?id=X)`，反查或用 `tracker-manager` skill 补到清单。

**产物**：`customer-care/docs/architecture/smart_popup/analysis/error-code-frequency.md` 或对应 Superset Dashboard，含 Top N 排名 + 每条的 `{用户现象, 技术根因, 是否可自助, scene}`。**`scene` 字段对齐 Rule 5 的 Proximal Metric 表**。

---

## Step ③ 配规则 + 自助文档

**委托**：`marketing-cms`（Payload CMS 多语言 copy）+ `growthbook`（A/B 配置）+ `gitlab-mr`（`.evc` 规则包发布）+ `story-craftsman`（更新 `intercept.md` User Story）

**动作**：

1. 让用户按 [references/rule-config-template.md](references/rule-config-template.md) 给每条 `(event, error_code)` 填配置表。**必填字段包括 `scene` 和 `proximal_metric`**——没填的不能进入下一步。

2. 规则源码在 `customer-care/app/smart_popup_rules/` 下加新规则，走 GitLab CI 打包成 `.evc` 字节码上传到 S3 staging。

3. Payload CMS 里维护 8 种语言 copy（`en/de/es/fr/it/ja/zh-Hans/zh-Hant`，走 Crowdin 翻译），通过 `marketing-cms` skill 批量同步。

4. GrowthBook 里创建 feature flag `intercept_<scene>_<error_code>`，绑 `.evc` 版本，初始灰度 5%。

5. **客服 review**：自助步骤可行吗？话术友好吗？兜底流程能走通吗？客服过不了，规则不上线。

6. 灰度节奏（Rule 4）：5% → 监控 24h → 50% → 观察 3 天 → 100%。

---

## Step ④ 看 A/B 结果

**委托**：`superset`（拉指标）+ `growthbook`（显著性检验）

**指标分三层看**（按 Rule 5/6）：

```
┌─────────────────────────────────────────────────────┐
│ 1. Scene 专属 proximal metric（leading indicator）  │
│    → binding: binding_success_rate                  │
│    → live: live_success_rate                        │
│    → payment: payment_success_rate                  │
│    → ...                                            │
│    必须显著正向，否则规则是无效的                    │
├─────────────────────────────────────────────────────┤
│ 2. Distal 兜底：差评率 / 客服工单率 / 留存率          │
│    必须非反向（允许中性，但不允许恶化）               │
├─────────────────────────────────────────────────────┤
│ 3. 过程指标：CTR / 自助完成率                        │
│    仅用于 debug，不参与上/下线决策                   │
└─────────────────────────────────────────────────────┘
```

**显著性标准**：GrowthBook 里的贝叶斯概率 ≥95%。

**决策树**：

- **proximal ↑ 显著 + distal 非反向** → 推全量。lessons-learned 追加一条"成功案例"，对应 scene 的 `priority_weight` 在下一轮 Step ② 可以保持或略微上调
- **proximal ↑ 但 distal 反向**（CTR 涨但工单也涨 / 差评涨）→ **在骚扰用户**。lessons-learned 追加"失败案例"+ 识别出的"无效 copy 模式"，回 Step ③ 迭代触发时机/copy
- **proximal 中性 + distal 中性** → copy 或触发时机有问题。lessons-learned 追加"中性案例"，回 Step ③
- **proximal 或 distal 显著反向** → **立即下线**。lessons-learned 追加"失败案例"，对应 scene 的 `priority_weight` 下调（如 1.0 → 0.7）；连续 2 次全 scene 规则失败考虑 weight → 0

**数据链闭环的写入要求**（Rule 1 的落地点）：每次 Step ④ 结论必须**至少**写 3 处：

1. GitLab issue 评论区（决策链可追溯，关联 MR 通过 `Closes #N`）
2. `lessons-learned.md` 追加 entry（下一轮 Step ②/③ 读这个）
3. 如果是规则下线或全量推全，smart_popup_rules/ 对应的规则文件也要打 tag 或归档

**未写入 lessons-learned 的 Step ④ 结论视为未完成**——因为下一轮会重复同样的错。

---

## Examples

**场景**：PM 说"我想加一个针对直播失败的拦截"

### ✅ Good

```
AI：差评拦截走 data-driven 四步闭环。我先扫数据链工件判断当前状态：
    - exception-trackers.tsv：3 天前跑的，有 8 张 live 相关候选 ✓
    - lessons-learned.md：live scene weight=1.2（Q1 有一次成功经验）；
      无效 copy 模式里记录了"避免在 live 弹窗里用 'P2P' 这个术语"
    → 可以跳过 Step ①，从 Step ② 开始，并把这两条先验带入

Step ②（委托 superset）：
  - 对 8 张 live_failed/join_live/player_error 表跑 SQL，近 7 天 Top：
    - (live_failed_hi, 4002) "打洞失败"       occ=12430  uv=3201  → 加权 1.2×
    - (live_failed_hi, 4005) "带宽不足"       occ=8220   uv=2145  → 加权 1.2×
    - (live_failed_hi, 9999) "SDK 瞬断自恢复"  occ=50100  uv=4800  ← 剔除：非用户可感知
    - (join_live_hi,   4011) "设备离线"       occ=5010   uv=1890  ← 走客服工单兜底，不拦
    ...
  - 给 4002/4005 对齐 scene=live，proximal_metric=live_success_rate
  - 反查 tracker-manager 拿 event_id=376/377
  - 输出写到 error-code-frequency.md

Step ③：
  - 按 rule-config-template 填两条规则；copy 里按 lessons-learned 的提醒
    避开技术术语（用"网络不稳定"而不是"P2P 打洞失败"）
  - 打包 .evc → 5% 灰度，GrowthBook flag: intercept_live_4002, intercept_live_4005
  - 8 语言 copy 同步到 Payload CMS

Step ④（7 天后）：
  - 4002 组：live_success_rate +12%（p=0.97）+ 差评率 -8%（p=0.94）→ 推全量
    → lessons-learned 追加"成功案例：避开技术术语生效"，live scene weight 保持 1.2
  - 4005 组：live_success_rate +1%（p=0.55）+ CTR 却 +40% → 在骚扰
    → lessons-learned 追加"失败案例 + 无效 copy 模式：'优化你的网络'
      对带宽不足用户等同无效说教"，回 Step ③ 迭代
  - 两条结论都同步写到 GitLab issue 评论区
```

### ❌ Bad

```
AI：好的，我在 smart_popup_rules 里加一条：只要 live_failed 事件触发就弹窗。
```

问题：
- 跳过 Step ①② 直接到 ③（违反 Rule 1 的数据驱动链）——没读 `exception-trackers.tsv`、没读 `lessons-learned.md`，上一轮"避免技术术语"的教训会被重复踩一次
- 没到 `(event, error_code)` 颗粒度（违反 Rule 2）——`code=9999` 的 SDK 瞬断跟 `code=4002` 的 P2P 打洞失败完全不是一回事
- 没配自助方案（违反 Rule 3）
- 没定义 scene 和 proximal metric（违反 Rule 5），Step ④ 只能看 CTR，没法判断规则是否真的有效
- 没走灰度（违反 Rule 4）
- 即使规则上线也没法追溯到 lessons-learned，违反"未写入 lessons-learned 的 Step ④ 结论视为未完成"

---

## 相关 Skill

| Step | 委托 | 用途 |
|---|---|---|
| ① | `datahub-schema-search` | 列表 + 语义搜索 |
| ①② | `tracker-manager` | 埋点平台 SSOT，反查 event_id |
| ② ④ | `superset` | SQL 查频次 / A/B 指标（`SUPERSET_USERNAME` / `SUPERSET_PASSWORD`） |
| ③ | `marketing-cms` | Payload CMS 多语言 copy |
| ③ ④ | `growthbook` | A/B 实验配置 + 显著性（`GROWTHBOOK_API_KEY`） |
| ③ | `gitlab-mr` | `.evc` 规则包发布 MR |
| ③ | `story-craftsman` | 更新 `docs/product/user-stories/intercept.md` |

本 skill 是 `dev-workflow` 在差评拦截场景的特化——通用研发流程编排走 `dev-workflow`，差评拦截特有的四步闭环走本 skill。

## References

- [references/methodology.md](references/methodology.md) — Step ① 的技术 playbook（数据层分层、采集脚本、踩坑细节）
- [references/rule-config-template.md](references/rule-config-template.md) — Step ③ 规则配置必填字段 + 多语言清单 + 上线 checklist
- [customer-care README](https://gitlab.addx.ai/services/customer-care/-/blob/master/README.md)
- [SmartPopup 架构概述](https://gitlab.addx.ai/services/customer-care/-/blob/master/docs/architecture/smart_popup/overview.md)
- [首轮异常埋点清单](https://gitlab.addx.ai/services/customer-care/-/blob/5-exception-tracker-analysis/docs/architecture/smart_popup/analysis/exception-trackers/README.md)
