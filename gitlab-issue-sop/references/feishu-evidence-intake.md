# 飞书证据 → GitLab 预研 Issue

适用于用户要求从飞书群、文档、会议纪要或妙记创建/更新 Issue。把本页与 `gitlab-issue-sop`、[Label SSOT](label-system.md) 一起使用；飞书命令的具体参数以当前安装的 `lark-shared`、`lark-im`、`lark-doc`、`lark-calendar`、`lark-vc`、`lark-minutes` Skill 和 `lark-cli ... --help` 为准。命令提示要求读取内嵌 Skill 时，先用同一 CLI 的 `skills read` 读取版本匹配的指南（如 `lark-doc` 的 fetch/history），不能仅凭 `--help` 猜参数。**原始来源仍是飞书，Issue 记录可追溯的摘要和决策，不复制整篇私有文档或逐字稿。**

## 1. 收集最小输入

先解析用户已给出的链接和本次会话的授权，只追问仍阻碍定位的字段。可一次发这张短表：

| 输入 | 用途与缺省处理 |
|---|---|
| GitLab 项目 URL/path；已有 Issue URL/IID 或“按主题新建” | 必填。没有目标项目无法写回；已有 Issue 先读取，未给 Issue 则跨仓查重。 |
| 飞书群链接或 `oc_...`，原始文档/妙记/会议链接 | 至少一项；多群逐一记录。只有模糊名称时先搜索并消歧。 |
| 其他需加入的飞书群／Buzz Channel ID | 主动询问是否还有来源；新群须核实 Agent 成员资格、自助申请或请管理员邀请，获准后将群 ID 写入定时 Workflow prompt 并回读。 |
| 日期起止与时区、项目/模块关键词 | 优先从消息/文档日期和用户上下文推断合理窗口并说明；宽窗口分段搜索，别仅查最近一页。 |
| 希望得到什么：预研清单、现有 Issue 评论、会议纪要摘要、Board | 未说明时先做只读证据表和 Issue 映射，再执行已授权的写回。 |
| 飞书授权身份/profile、允许的应用与身份、GitLab 身份 | 用户给出固定运行时、hash、profile、`--as` 或资源范围时，严格按其门禁执行；不能换 bot、别的 profile 或连接器绕过拒读。 |
| 决策人/assignee、模块列、优先级与状态、写入范围、通知对象 | 已有 Issue/群成员可核实后填；不能唯一确定时先问。未明确排序时优先级留待 triage，不猜 P2；“搁置/放弃”必须有决策来源。群 PM/TPM/开发身份只作可核实的角色信息；成员邀请/权限变更须单独有授权。 |

示例提问：“请给我目标 GitLab 项目（及已有 Issue）、要查的飞书群/文档链接、日期范围，以及你希望新建预研 Issue 还是补评论。若有指定的飞书 profile/用户身份或模块负责人，也请一并给出。”

## 2. 身份与权限门禁

1. 检查 CLI 来源、版本与用户指定的二进制/hash 门禁；检查 `whoami`、profile、App ID、用户 open_id、`default-as`、`strict-mode`、token 状态。每个业务命令显式传指定 profile 和 `--as user`（若用户批准的是其他身份，按批准身份）。不要把 token、secret、完整 auth JSON 写入 Issue 或日志。
2. 用最小只读调用验证各能力：群消息、文档当前内容/历史、会议搜索/纪要、妙记详情/逐字稿。分别记录 **应用 scope 是否开通 → 用户 OAuth 是否授权 → 用户是否有目标资源访问权**。`auth status` 成功不证明某个妙记可读。
3. Scope 未开通时，先给应用管理员准确的 scope/开发者后台链接；应用已开通但用户未授权时，才用 `auth login --scope <精确 scope>` 发起用户授权，保存可点击授权链接并等待结果。授权后回读 `auth status` 和原资源。设备流过期只重发一次有效链接，不反复要求扫码。
4. 资源级拒读时保留错误码、资源 URL/token 和操作。普通文档若当前 CLI 支持，可在本次写授权覆盖下用 `drive +apply-permission --token <文档 URL> --perm view --remark <用途>` 发出给所有者的访问申请，并读回申请结果。**该命令目前不接受 `minutes` 类型，不得拿妙记 token 假装文档申请成功。**
5. 妙记拒读而缺自动申请 API 时：输出原始妙记链接及“请所有者授予当前用户查看权限”的简短文案。若用户已要求提醒且收件人可唯一解析，用批准的 `lark-im` 身份给申请人/资源所有者发送一次含链接的飞书提醒，记录消息 ID；无法唯一确定对象时先问。不要凭 owner_id 推断可直接私聊，也不要擅自扩大共享范围。
6. 为拒读资源保留待复查项：资源 URL/token、目标用户身份、拒读错误、申请/提醒时间与 ID、下次检查时间。当前会话可做有上限的复查（例如 5、15、30 分钟，按剩余时间调整），每次重新读取**同一资源**；成功后立即读取内容并继续 Issue 映射。若任务运行环境有经授权的持久调度器，可安排后续复查并回链状态；否则在会话结束时报告“待授权/待复查”，不能声称后台持续监控。仍无权时停止该来源的内容推断，其他可读来源继续处理。

## 3. 自动发现相关会议与完整来源

1. 从群消息和文档提取会议标题、日期、组织者/参会人、日历 Event URL、会议链接、妙记 URL、`meeting_id`、`minute_token`，建立候选表并标注每条线索。群消息分页到时间窗边界，读取相关 thread、转发和附件；不要用首屏结果代表全量。
2. 对**已结束**会议，用 `lark-vc +search` 按项目/模块关键词与日期窗口搜索，覆盖所有分页；跨度超过 CLI 上限时分月搜索。仅有日历 Event 线索时用 `lark-calendar` 定位 Event，并以 `calendar-event-id` 查用户绑定的 `meeting_notes`。搜索结果与原始群聊时间/参会信息交叉核对，避免同名误配。
3. 每个可信候选用 `lark-vc +notes` 查 AI 纪要、用户绑定纪要和逐字稿，用 `lark-vc +recording` 找 `minute_token`；再用 `lark-minutes` 查妙记元信息和所需产物。用户要求总结会议内容时才读正文/逐字稿。`meeting_notes`、`note_doc_token`、`verbatim_doc_token`、妙记录制是不同产物，分别标记是否存在和是否可读。
4. 相关文档读取当前版本，并在权限允许时读 revision 列表/历史正文和评论。只对确实可回读的旧版本作语义比较；仅有当前 revision 号或修改时间时，标记“历史不可见，不能还原变更原因”。每个提及的文档、会议和妙记都保留**完整可打开 URL**；群消息优先深链到消息/附件，不能生成可靠深链时给群链接 + 时间 + message ID。
5. 结果至少区分：`未发现会议`、`找到会议但无纪要/录制`、`纪要存在但资源级拒读`、`已读取纪要/逐字稿`、`搜索不完整`。空摘要/空待办不等于无会议，更不等于已读逐字稿。

最小命令骨架（实际执行用用户批准并验证的 CLI 可执行基线，每条业务命令都带批准的 `--profile` 和 `--as`；先读各命令的匹配 Skill）：

```bash
lark-cli im +chat-messages-list --chat-id '<oc_...>' --start '<ISO8601>' --end '<ISO8601>' --order asc --profile '<profile>' --as user
lark-cli docs +fetch --doc '<document URL>' --scope full --profile '<profile>' --as user
lark-cli docs +history-list --doc '<document URL>' --profile '<profile>' --as user
lark-cli vc +search --query '<project/module>' --start '<YYYY-MM-DD>' --end '<YYYY-MM-DD>' --profile '<profile>' --as user
lark-cli vc +notes --meeting-ids '<meeting_id>' --profile '<profile>' --as user
lark-cli vc +recording --meeting-ids '<meeting_id>' --profile '<profile>' --as user
lark-cli vc +notes --minute-tokens '<minute_token>' --profile '<profile>' --as user
```

这些调用的分页、时间窗和资源类型要按响应继续处理；示例不是已完成覆盖的证明。妙记逐字稿可能默认落到当前目录，优先指定安全输出目录，处理后不要把逐字稿、token 或临时下载文件提交到仓库。

## 4. 证据表与 Issue 写回

先做逐条证据表：`主题 | 事实/讨论/候选/决策 | 来源 URL | 来源时间和时区 | 文档 revision 或会议产物 | 可读状态 | 对应 Issue/模块 | 待验证问题`。说话者只有可核实时才标角色；自动转写的术语、数字和人名若含混，标“转写待核”并回链时间戳。会议/供应商判断不能冒充实测，群聊建议不能冒充 PM 确认。敏感个人信息、密钥和完整转写不要写进 Issue。

1. 先读取目标及相关仓库的 open/closed Issue，并按 SOP 去重；目标已存在则追加证据，主题不同且有独立验证产出时才新建。来源无法完整读取时标明覆盖缺口，不猜测“没有重复项”。
2. 新建预研 Issue 用 [预研模板](issue-template.md#预研-issue-模板)：原始问题、验证路径、决策人、可验证退出条件和完整来源。类型选 `type::research`（原生 Work Item Type 已配置时遵循原生模式）；按已治理的模块标签设置一栏。尚未确定的能力、供应商数据和群聊观点保留“待验证”。
3. 原始描述作为 intake 快照；后来的群聊、文档 revision 和会议证据写**追加式 comment**。同一来源事件通过资源 token/message ID、revision、会议时间戳与既有 comment 比对；已经写过就跳过，只把真实新发现或纠正信息追加，必要时注明 supersedes 哪条旧结论。
4. 每条 comment 带日期、来源性质、关键结论与不确定性、受影响的问题、完整原始链接、下一步/责任人。已确认的需求变化才走主 SOP 的 requirement revision 与 Gate；预研讨论不自动改变 AC 或触发开发。
5. 从文档和会议中抽取有明确阶段目标的时间窗口、交付物与评审门槛。先查已有 Milestone 去重，再按阶段创建/更新；描述中链接完整来源和相关 revision，区分原文日期、按月份末日转换的目标日和待确认日期。不能为“月初”等模糊表述捏造精确 due date，也不能把预研阶段与 APP 发版 Milestone 混用。一个 Issue 只能挂一个 Milestone：将其关联到当前验证产出的阶段，后续打样/调试/评审没有独立 Issue 时保持该阶段 Milestone 空置，不拿现有研究 Issue 冒充交付任务。逐项 GET 回读 Milestone 与 Issue 关联。
6. GitLab 写入前核对当前请求的授权、项目及身份；逐条 GET 回读 Issue、comment、assignee、label 和 Board list。标签缺失按 [Label SSOT](label-system.md) 处理，不隐式创建同义标签。最终报告覆盖的群/文档/会议数、已写回 Issue 与 Milestone 链接、跳过重复项、权限缺口、待决策项及复查时间。

评论示例：

```markdown
## 预研证据补充（YYYY-MM-DD）

- 来源：[会议妙记](https://example.feishu.cn/minutes/<minute_token>)，00:12:10–00:16:30，自动转写，术语待核。
- 讨论：与会者提出单基站覆盖风险，供应方仅提供原始 IQ/开发板参考；实际精度尚未测定。
- 影响：继续比较单/多基站和自研/第三方算法；现有 AC 不变。
- 下一步：<负责人> 在 <场景> 用原始数据与真值复测。
```

## 5. 预研 Board

用户要求“一个模块一栏”时，先按现有 Issue 划出互斥、长期可复用的两级领域模块；一条 Issue 各有一个一级、二级模块，跨模块依赖用 related Issue/comment 表达。按 [Board Setup](board-setup.md#项目模块-board) 建板：`module::*` 的一级值是板列，`submodule::*` 的二级值用于筛选，`type::research` 是工作类型；优先级用 `priority::*`，开放状态用 `status::*`。项目级模块标签仅在 SSOT 的项目例外门禁全部满足后创建。研究与开发 Issue 共用模块标签；GitLab Free/CE 不能持久给 Board 设置 `type::research` 过滤器，因此板列也会包含后续开发 Issue，须按类型核对预研范围。搁置记录 `status::on-hold` 和复查日期；整单放弃记录 `resolution::abandoned`、决策依据并原生关闭。
