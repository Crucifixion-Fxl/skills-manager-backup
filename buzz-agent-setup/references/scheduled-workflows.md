# 定时分析 Workflow

业务 Channel 用 Buzz `schedule` 唤醒角色 Agent 做**只读分析**。方法在对应 Skill；本页只规定目录、cron 专属参数和最终报告的站立受众。GitLab → Buzz 同步不是 Workflow，见 [gitlab-buzz-sync.md](gitlab-buzz-sync.md)。

下发：`"$BUZZ_CLI" workflows create --channel <CH> --yaml "$(cat <file>)"`。改现网 yaml 是跨出 skills 仓的写操作，要另走 ACT。

## 谁可以有 schedule

| 角色 | schedule |
|---|---|
| `-desk` | 交付进展 |
| 平台 Desk（如 `gitsecops-desk`，在职能 Channel 里） | 平台反馈周报（`platform-feedback-summary.yaml`），只读分析 |
| `-bi` | 数据复盘 |
| `-sre` / `-investigator` | 巡检（已有则保持） |
| `-dev` | **只读分析**（pipeline 健康、架构坏味道）；无实现 / 写仓 schedule |
| `-debt` | 若频道部署了 `-debt`，月度坏味道改唤醒它，不唤醒 `-dev` |
| `-feature` / `-qa` / executor | 无 |

## 目录（模板在 `references/analysis-workflows/`）

| 模板 | 默认 cron（UTC） | 唤醒 | Skill |
|---|---|---|---|
| `analysis-workflows/delivery-progress.yaml` | `30 0 * * 2-6` | `-desk` | `delivery-progress-analysis` |
| `analysis-workflows/data-review.yaml` | `30 0 * * 2-6` | `-bi` | `data-review-analysis` |
| `analysis-workflows/pipeline-health.yaml` | `30 1 * * 2` | `-dev` | `gitlab-pipeline-health` |
| `analysis-workflows/architecture-smell.yaml` | `30 1 1 * *` | `-debt` 否则 `-dev` | `architecture-smell-scan` |
| `analysis-workflows/platform-feedback-summary.yaml` | `30 6 * * 2` | 平台 Desk（职能 Channel） | 本页「平台反馈周报」 |

缺 Skill、缺运行参数（对象 / 周期 / 分析时点 / 对比窗口 / full refresh 等适用项）则配置验收失败，禁止临场猜上一份报告。

## cron 的星期字段：relay 里 1=周日

**relay 的星期字段 1=周日，周一=2，周一到周五=2-6；名字写法未验证。** relay 按 Quartz 风格读 cron 第 5 位：1=周日、2=周一 … 7=周六，不是常见的「1=周一、1-5=周一到周五」。抄模板、改 cron 都按这张表写：

| 想要 | 星期字段 |
|---|---|
| 每周一 | `2` |
| 周一到周五 | `2-6` |
| 每天 | `*` |

- 只用数字 1-7。`MON`、`MON-FRI` 这类名字写法没有验证过，模板和现网都不要用，本页也不把它当作可用写法。
- 证据（2026-09-21，UTC，来自频道里 Workflow 触发消息的时间；`buzz workflows runs` 返回空，不能用它验证）：按「1=周一」写的旧值 `30 0 * * 1-5` 在 09-16 周三、09-17 周四、09-20 周日、09-21 周一触发，09-18 周五没有 00:30 那次；旧值 `30 1 * * 1` 只在 09-20 周日 01:30 触发，09-21 周一没有。也就是 `1` 落在周日、`1-5` 落在周日到周四，周一到周五要写 `2-6`。
- 月度模板（`architecture-smell.yaml`）写的是日期 `1`、星期字段是 `*`，不受影响。
- `tests/test_workflow_cron_weekday.py` 按这个约定把每个模板的星期字段展开成具体星期几并断言落点（周报只在周一、日报周一到周五且不含周日、平台周报与 pipeline 分析同一天且更晚）；新增模板要在测试的 `SHAPES` 里登记。

## Workflow 维护须知：update／delete／runs 在这个 relay 上的行为

三个 `buzz workflows` 子命令的行为和字面意思不一样（relay 0.2.1，2026-09-21 实测）。改现网 Workflow 前先按这里核对：

- **`update` 只会替换同一把 key 写的 Workflow。** 用另一把 key 更新（例如频道 owner 改别人写的），relay 不替换，而是多出第二条同 id 的定义：`workflows list` 同时列出两条，看不出哪条生效。要改别人写的 Workflow，让原作者用自己的 key 更新，或先和作者协调，不要用 owner 的 key 硬改。证据（2026-09-21）：云服务成本频道的 Workflow `f1ab9984` 由 qlv 在 03:06 创建，jchen 的 key 在 07:52 更新后，同一个 id 出现了两条（一条还是 `canvas_alias`，一条是 `person`）。
- **`delete` 只返回 `accepted`，不生效。** 要退役一条 Workflow，就把它 `update` 成不会触发的 noop：schedule 类把 cron 改成 `0 0 31 2 *`（2 月 31 日，永远不会到），并在名字里标 `RETIRED`；webhook 类见 [runtime-setup.md](runtime-setup.md)「7. Workflow」里 webhook＋noop 的做法。退役也是 `update`，同样受上一条约束：要用创建它的那把 key。
- **`workflows runs` 返回空，不能用来验证是否触发。** 看 Workflow 发出的触发消息的时间，对照 cron（星期字段见上一节「cron 的星期字段」）。

## Setup 必问席位

一次问齐，写入 Canvas 站立受众表，并写进每条 Workflow 的最终报告指令。用户名必须已在 `people_file` 且是当前 Channel human member（role 是 owner/admin/member；guest 与 bot 不算，所以 `channel_admin` 常见的频道 admin 可以）：

| seat | 问法 | 用在 |
|---|---|---|
| `channel_admin` | Channel admin 的 GitLab 用户名是什么（常为 Canvas admin） | 每条定时**最终报告** |
| `pm` | 产品经理的 GitLab 用户名是什么 | 进展 / 数据复盘最终报告 |
| `core_eng` | 核心研发的 GitLab 用户名是什么 | 周期结果（pipeline / 坏味道，以及其它周期报告） |

席位空缺则该 seat 写 `未通知：<原因>`，不得用显示名或自由文本补人。Setup 时顺手核对每个席位人在该 Channel 的 role（pubkey 用 `people_file` 查、role 看 `channels members`）：guest、bot 或不在频道里的人，helper 会写 `未通知：<用户名>(not_human_member)`／`(not_channel_member)`，第一次定时报告才发现就晚了，所以在 setup 时换人或先改 role。

Canvas 块（owner 签发的最新 kind 40100；格式错则整块忽略，不回退旧 Canvas）：

```markdown
<!-- buzz-workflow-audience:v1 -->
| seat | GitLab 用户名 |
| --- | --- |
| channel_admin | <username> |
| pm | <username> |
| core_eng | <username> |
<!-- /buzz-workflow-audience:v1 -->
```

通知仍只发 helper 的 `person` locator（`{"kind":"person","username":"<GitLab 用户名>"}`）。Workflow yaml 复制这些用户名，Agent 不从聊天正文猜席位。

## 最终报告怎么通知

交互消息保持注意力预算：普通进展 / FYI 不通知人。

**例外**：定时 Workflow 的**最终报告**（不是 ack / pickup）视为需要人看，helper sources 按序：

1. `channel_admin`
2. 进展或数据复盘再加 `pm`
3. 周期结果再加 `core_eng`

去重后仍最多 3 人。ack 不加站立受众。禁止全体广播。helper 失败不得降级直发。

## 长报告：写成飞书文档，频道只发摘要

最终报告超过约 8 行（或含表格、每日重复）时，不要整篇发进频道。**产出报告的 agent 自己**用 bot 身份建飞书文档、只读授权给频道绑定的群、发摘要＋链接；Buzz Thread 里只发 3～5 行摘要和文档链接，站立受众通知和行动消息规则不变。命令、scope 预检与缺权限时的处理见 [feishu-doc-report.md](feishu-doc-report.md)。不得用 owner 的 `--as user` 代发。

## 结果通知谁：席位 + 行动消息

定时 workflow 的结果要到「对应的人」手里，靠两层，都走同一个确定性 helper（GitLab 同步用的也是同一套 `buzz_responsible_mentions` 规则：只 @ 当前 Channel 的真人成员（role 是 owner/admin/member）、去重、最多 3 人、`未通知：<原因>`）：

1. **站立席位**：最终报告本身按上一节的 `person` 席位通知（`channel_admin`、按类型加 `pm`／`core_eng`）。
2. **行动消息**：报告里「需要人处理」的每一项（**最多 5 项**，没有就不发），在**同一 Thread** 再发一条短消息，一句话写清事项和链接，sources 用该项的**责任人**。责任人只来自 GitLab 的结构化字段（MR／Issue 的作者、assignee、reviewer），不从正文猜。同一个人在同一 Thread 只通知一次。

责任人怎么变成 Buzz 的人：helper 用 `person` locator（或 GitLab locator 取出的用户名）查 owner-only 的 `people_file`（GitLab 用户名 → Buzz pubkey，与 GitLab 同步是同一份映射，每次发送现读）；不在 `people_file` 的人，行动消息里写 `未通知：<用户名>(profile_not_found)`，不是静默丢掉。这只是查表，发送时仍会重新核对该人是当前 Channel 的 human member。

三条边界：一条消息最多 3 人（超过按首见顺序截断并写明）；席位与责任人是同一 helper、同一预算，不要为了多通知几个人绕过 helper；注意力预算不变——没有需要某个人行动的事项，就只有最终报告的席位通知。

行动消息和站立席位都在 workflow 正文里写（模板已带）；agent prompt 里通用的责任人通知片段不用改。

## 平台 Desk 转交与顺序

职能 Channel 的平台 Desk（如 `gitsecops-desk`）被拉进各业务 Channel 后，业务 `-dev` 的 pipeline 分析要把**平台层根因**（CI 模板、runner、镜像仓库、跨仓权限）交给它，业务仓的事实只由 `-dev` 用自己的身份取数，Desk 不持业务仓凭据：

1. workflow 只 @ 业务 `-dev`（不改：入口不变），正文里写：最终报告发出后，在同一 Thread **转交平台 Desk 一次**。
2. 转交**每次必做**：有平台层根因就逐条列出（根因、证据 job／pipeline 链接、影响项目路径），没有就写 `无平台层根因`。不能省略——Desk 的回复回执（Issue 链接，或「已收到，无平台层根因」）是这个频道「本周已出完」的唯一完成信号。
3. Desk 只查重、建或更新 Issue、在同一 Thread 回 Issue 链接；不重新分析，不 @ 回 `-dev`。转交格式与 Desk pubkey 写在该业务 Channel Canvas 的「平台 Agent」一节，不写进 Agent prompt（不必重启）；`-dev` 用 `--mention <desk pubkey>` 发送前先核对该 pubkey 在本 Channel 是 `role=bot`，因为 Canvas 成员可编辑，不能只信文本。这是 Agent→注册 Agent 的转交，不是通知人，不走责任人 helper，也不能借它 @ 人。
4. **顺序**：`weekly-mr-pipeline-health`（默认周一 01:30 UTC，`30 1 * * 2`）→ `-dev` 出报告并转交 → Desk 建单并回执 → 平台反馈周报（默认周一 06:30 UTC，`30 6 * * 2`，星期字段必须和前者相同，给分析和建单留 ≥3 小时余量，测试里有校验）。周报比任何一个业务频道的 pipeline workflow 早，就会把「还没出完」误报成「没有反馈」。

## 平台反馈周报

只读分析，由平台 Desk 在职能 Channel 里执行，模板 `analysis-workflows/platform-feedback-summary.yaml`。

- 对象：平台需求的中央 Issue 仓；窗口 `[触发时 − 7 天, 触发时)`，UTC，新建按 `created_at`，状态变化按 `updated_at`；对比上一份同口径周报，没有就写「首期，无对比」。
- 数据只来自 Issue 的标题、链接、标签、状态、`来源频道` 首行、影响项目路径和计数（分页拉全，写明总数），加上 Desk 自己在各业务 Channel 发出的转交回执；**不读、不引用业务 Channel 的 Thread 正文**，报告只发在职能 Channel。Issue 若在 public 仓，正文只放证据链接和一句话现象。
- 骨架（没有内容的段落不写）：`一分钟结论`（≤5 行）→ `收到的反馈`（来源频道 × 类型计数表，来源写不出就写「未记录」）→ `重复出现`（≥2 个频道或连续 2 周的同一根因）→ `处理状态`（新建／进行中／已关闭／超 7 天无人动）→ `转交回执`（已转交 N 条／无平台层根因／**待转交：<频道>**）→ `需要人决定`（Issue 链接、决定什么、建议、找谁）。
- 缺哪个频道的回执就写 `待转交：<频道>`，不补分析、不等待。最终报告用站立席位通知（`channel_admin`、`core_eng`），「需要人决定」逐项发行动消息，责任人取该 Issue 的 assignees。
- 中央 Issue 仓的标签、标题和来源首行约定属于 Desk 的 owner prompt，不属于本 Skill；本页不写具体仓库。

## 反例

- 给 `-dev` 配「实现 Issue / 提 MR」的 schedule
- 把 GitLab 同步 tick 改成会通知人的 Workflow
- 最终报告用正文 `@名字` 而不走 helper
- 把周报数字写进本页或 Skill
- 平台反馈周报排在业务 pipeline 分析之前，或把「没有转交」当成「没有反馈」
- 星期字段按「1=周一、1-5=周一到周五」写（relay 里 1=周日：周报落到周日，日报跑周日到周四、漏周五），或用未验证的名字写法（`MON`、`MON-FRI`）
- 转交时没有平台层根因就不发，导致周报无法区分「没出完」和「没问题」
- 给平台 Desk 签业务仓 token 来「就近读 pipeline」，而不是让业务 `-dev` 取数后转交
- 行动消息的收件人从报告正文里猜，或绕过 helper 直接 `@名字`
