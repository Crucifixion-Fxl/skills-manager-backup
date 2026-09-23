# buzz-agent-setup 脚本

零外部依赖（纯 Python 实现 BIP-340 + bech32；WebSocket 用 Node ≥22 的内置 `WebSocket`）。

| 脚本 | 用途 |
|---|---|
| `nostrkit.py` | BIP-340 schnorr 签/验 + secp256k1 + bech32（nsec/npub）。**自带 BIP-340 官方测试向量自检**，改动后先跑一遍 |
| `mint-agent.py <name>…` | 铸 agent 身份：密钥对 + owner 的 NIP-OA 背书 → `BUZZ_AUTH_TAG`。`-h`／`--help` 只打印用法（不读 owner 密钥、不铸密钥）；名字必须匹配 `[A-Za-z0-9][A-Za-z0-9._-]{0,62}`，详见下文「mint-agent 用法」 |
| `nostr_sign.py` | stdin 收 `{seckey,kind,tags,content}` → stdout 出完整签名事件（NIP-01 id + sig） |
| `publish_event.mjs` | NIP-42 认证后发布任意事件（challenge 是运行时才拿到的，必须现签）。**发 kind:30177 策略事件就靠它**；退役 agent 时也用它发 kind 5 删除事件（只能带一个 `a` 标签目标，见 [runtime-setup.md](../runtime-setup.md)「9. 退役 Agent」） |
| `../../scripts/provision_gitlab_agent_token.py` | operator-only 配置 helper：GitLab 管理员在当前任务明确授权后，可信配置进程复用用户本地已登录的 `glab` 创建每 Agent 独立的 Project Access Token，随机 operation 名、sidecar lock、CAS env、PENDING journal、按档位设置 external（Planner／Reporter 标 external，会提 MR 的 Developer 不标）、唯一项目验证和无密钥 receipt。捕获异常自动回滚／撤销；突发终止按 journal 用 `glab api` 对账，不引导 UI。authorization ref 仅是证据，注册 Agent 必须读不到 helper 与 operator glab 配置 |
| `../../scripts/gitlab_agent_project_tokens.py` | 严格校验 owner 控制的多仓 token map（0600、裸 host、唯一 project id/path/token env、固定 profile），并提供确定性项目选择；映射只包含标识和变量名，不含 secret。 |
| `../../scripts/provision_gitlab_agent_tokens.py` | operator-only 多项目 provisioning／rotation：按 map 为同一 Agent 创建独立 Project Access Token，按 profile 设置 external（Planner／Reporter 标 external，Developer 不标），逐把验证 membership；对 external bot 额外验证 Internal 隔离，在同一 0600 env 原子注入；失败只清理本次操作并保留无密钥 journal，`--rotate` 要求 bot user id 不变。 |
| `../../scripts/gitlab_project_token.py` | 运行时受控 GitLab wrapper：owner launcher 用 `BUZZ_GITLAB_PROJECT_TOKEN_MAP` 固定 map 路径，只能以 map 中的 project id/path 选择 token，host 和 env 名不能由调用方传入；每次请求先回读目标项目身份，拒绝绝对 URL、跨项目路径和 map 外项目；`POST`／`PUT`／`PATCH`／`DELETE` 只有经过 L4 receipt 门禁才可执行。 |
| `../../scripts/gitlab_l4_receipt.py` | 校验 owner 固定的 L4 写入回执：0600 非 symlink、精确 map/head/current provisioning receipt 绑定、token/bot 身份、GitLab/runtime 验证证据、四种写方法 2xx 响应摘要和逐项目 canary；既可被 wrapper 调用，也可用 `--mapping`／`--receipt`／`--provisioning-receipt`／`--head-sha` 输出 JSON 验证结果。provisioning receipt 变化后旧回执不会继续授权。 |
| `gitlab-agent-project-tokens.example.json` | 四个 FAC 项目的非 secret 映射示例；仅用于复制结构，实际 token 值必须由 operator helper 写入 0600 env。 |
| `../../scripts/broker.py` + `broker_admin.py` + `buzz_job.py` | buzz-broker：沙箱外跑 docker／任意命令的窄接口（专用 Unix 用户 + rootless docker + 按令牌认策略），给需要 `docker`／testcontainers／有头 Chrome L3 的开发类 agent 用；机制、已验证事实、已知的坑、残余风险见 [../buzz-broker.md](../buzz-broker.md)；测试 `../../tests/test_broker.py`、`test_admin.py`（含真实 bwrap 隔离验证）|
| `../../scripts/gitlab_buzz_sync.py` | **当前方案的确定性同步核心**：作为 Desk-owned Agent Step 由 owner timer 启动，继承 launcher 的 Desk 身份白名单环境变量，按 owner 固定的 config/state 参数运行；每个 Issue/MR 一个 Thread，milestone 独立门牌 Thread，失败类变更发顶层即时通知，其余类型按 2026-09-18 通知政策停发；binding note 存在 GitLab；描述或评论里的 origin 标记可以指向本频道任何人的顶层消息（[ADR-0014](../../../../docs/05-adr/0014-allow-a-human-top-level-message-as-an-origin-root.md)），对象的事实回复到那个话题，标记合法但根不可用时回退自开门牌并在报告的 `origin_fallbacks` 里记一条。Role 路由由同一轮 runner 的 Canvas route gate 完成。协议与运行面见 [../gitlab-buzz-sync.md](../gitlab-buzz-sync.md)；身份与 outbox 决策见 [ADR-0004](../../../../docs/05-adr/0004-run-gitlab-sync-as-desk-owned-agent-step.md)；受众对账已由 [ADR-0006](../../../../docs/05-adr/0006-channel-membership-is-the-audience-consent.md) 废除 |
| `../../scripts/gitlab_buzz_summary_publish.py` | **摘要 publisher**（2026-09-18 政策后仅排空升级前遗留的 pending request，新摘要不再产生）：runner 认领 pending request 后，timer 入口用 `template_summary` 生成一条单行文本并附 facts 哈希回执；publisher 从 owner manifest 固定目的地，校验回执绑定、文本过滤与 facts 语义一致，严格 readback 后 ACK。发送结果未知只证明不重发；明确被拒只重发绑定内容；目标、request、state、身份均不可由模型选择 |
| `feishu_browser.js` + `feishu_register_agent_apps.sh` | **代 owner 批量注册 agent 的飞书应用**（#110）：无头浏览器保持飞书登录态（owner 只扫一次码），脚本对每个 agent 起 `lark-cli config init --new`、在确认页填应用名并提交、等 lark-cli 结束，再回读 `appId` 与 `app_name`。**只有 app_id 合法、且 `app_name` 与 agent 名逐字相等才打印唯一的成功行并退出 0**；任何一步失败（拿不到 user_code、浏览器步骤出错或不响应、页面「创建失败」、lark-cli 非零退出、回读对不上）都非零退出、不打印成功行，并把后台 lark-cli **连同它的所有子孙进程**（它独占一个进程组，靠 python3 建，不依赖 `setsid` 命令）一起收掉；被信号打断时同样。依赖 bash 和 PATH 上的 python3（不假设位置，缺了就退出码 2、什么都不做）。`LARK_AGENTS_HOME`、每个 agent 的目录、`ready.txt` 是符号链接一律拒绝（退出码 2），不顺着链接去 chmod 或写别处。威胁模型：单用户、0700 目录；符号链接检查是检查前 + 创建后复核，不防同 uid 的进程。浏览器会话（`feishu_browser.js`）无论正常退出、出错还是被信号终止，都清掉登录态页面的快照并关掉浏览器；启动时先清掉上次留下的 `quit.txt`，坏 `cmd.txt`（不是合法 JSON 对象）只算这一步失败（`result.txt` 写 `ERR <短原因>`，会话不崩、不用重新扫码）。浏览器要在一个自己的 0700 目录里运行并把它交给脚本（`FEISHU_BROWSER_DIR`，登录后有 `ready.txt`）；agent 名只能是小写字母开头的小写字母 / 数字 / 连字符（≤32）。二维码只在要发时才刷新，用完删登录态目录。做法与坑见 [../feishu-group-sync.md](../feishu-group-sync.md)「agent 的飞书身份」；测试 `../../tests/test_feishu_register_agent_apps.py` |
| `feishu_scope_apply_url.py` | 给一个 agent 飞书应用生成「免审 scope 一条链接」（清单 [../feishu-agent-app-scopes.txt](../feishu-agent-app-scopes.txt)），创建应用后交给 owner 点一次；没有 API 能加 scope | [../feishu-group-sync.md](../feishu-group-sync.md) |
| `feishu_doc_publish.py` | 长报告 Markdown（含 Buzz 上的图片）→ 飞书文档：agent 用自己的 bot 建文档、图片下载后内嵌、分块追加、只读授权给群，stdout 打 `{document_id,url}` | [../feishu-doc-report.md](../feishu-doc-report.md) |
| `../../scripts/buzz_feishu_group_sync.py` | **Buzz Channel ↔ 飞书群（本地 CLI 路线，#110）**：`preflight`（新建群 / 关联已有群的权限前提）、`create-chat`、`bind`、`round`（成员对账 + 消息双向同步 + agent 的 Buzz reaction 同步成飞书表情，owner timer 每分钟调用，文件锁防并发）。每轮先核对身份，再向 bridge 的签名接口取最新的绑定关系（不缓存，取不到整轮中止）；bridge 应用的 open_id 在 owner 应用里认不出人，所以人的身份按配置 `identity` 认：`union_id`（默认，用应答的 `union_ids`；飞书发信人按单条消息的两种 id 精确配对）或 `email`（应答的 `emails` 经通讯录搜索、逐字相等才认换成 open_id，state 里只存 sha256），映射不到的一律不猜，前置是 bridge 已部署 union_id 回填与新响应字段（infra/buzz-deploy#77）：owner profile、镜像 key、各 agent profile 必须是配置里的应用与身份；飞书→Buzz 只用镜像身份，人的 Buzz 发言经 owner 应用 bot，agent 只经它自己的 profile，默认发成**卡片**（配置 `message_format`：`card` 缺省 / `text` 一键退回；标题是消息第一行、副标题是「发言人 · #频道」、超出预览的全文折叠、按钮只用 https 链接不用 `buzz://`、@ 用邮箱 → open_id → 纯文字、绝不用 union_id、整张 < 30 KB；飞书拒绝卡片内容时回退成文字，结果不确定时不回退）；表情只同步 agent 的 reaction，由它自己的 bot 打（人的不同步），撤销只认 reaction 作者自己的 kind 5，表情失败只计数不触发「需要关注」。Buzz 里的话题回复在飞书上没有可挂的父消息时，先把话题根补发到飞书（和普通镜像同一条发送路径、同一个幂等键与账本语义；根再老也补，只补有新回复的话题；`buzz messages thread --depth-limit 0` 取根，一轮最多取 20 个话题，同一话题只取一次），再把回复发成话题回复；根发送结果未知时回复等它、同一个键重试，根不可镜像或放弃、取话题连续失败时回复退回顶层，不丢回复，报告有 `thread_roots_backfilled` / `thread_root_unavailable` / `thread_root_failed` / `thread_roots_deferred`。@ 只来自显式 mentions 实体与 p tag，正文与署名里的 @、nostr:、<at> 一律中和。可选配置 `feishu_sender_allowlist`（非空、64 位小写 hex 的 Buzz pubkey，去重后最多 50 个，缺省不限制）设了以后，飞书 → Buzz 只镜像名单里的人：先判身份再判名单，名单外的已映射的人不发送、不写账本，`skipped` 计 `sender_not_allowed`；话题回复同样，@ 的对象不受限，不影响 Buzz → 飞书，用于个人频道 / 只认 owner 的 agent（风险缩小不是消除，见参考文档「只让特定的人的发言进 Buzz」）。设计、采访问题、预检与退出码见 [../feishu-group-sync.md](../feishu-group-sync.md)；测试 `../../tests/test_buzz_feishu_group_sync.py`、`test_buzz_feishu_group_sync_cards.py` 与 `test_buzz_feishu_group_sync_allowlist.py`（L1 + L2-1，无网络） **图片双向同步**：Buzz 事件的 imeta 附件（镜像身份 `buzz media get <sha256>[.ext]` 下载，魔数 / 大小 / sha256 校验）在文字（或卡片）之后由同一个发送者作为图片消息发出（`lark-cli --image`，相对路径、每张图一个幂等键与账本项，图片跟着文字去的话题走），飞书里人发的图片（owner 的 user 身份下载、去掉 EXIF / ICC 等元数据）作为镜像身份的附件发到 Buzz，文件 / 音频 / 视频不转，失败只影响那一张图（`images_failed` 需要关注、`images_skipped` 只计数）。**非成员发言的仅上下文镜像**：可选配置 `feishu_unmapped_senders`（`"context"` 缺省 | `"skip"` 显式关闭）让映射不到频道成员的人的飞书发言以 `[飞书·非成员] 姓名：正文` 进 Buzz——图片走成员同一条校验、去元数据与上传路径，真实 @agent 产生 p tag 并唤醒它、不能 @ 到人；与 `feishu_sender_allowlist` 互斥（已配名单且未写本键时隐式为 `"skip"`），报告多一个 `context_to_buzz`；测试 `test_buzz_feishu_group_sync_context.py`。**反方向对称**：可选配置 `buzz_unmapped_senders`（`"skip"` 缺省 | `"context"`）让既不是验证过的频道人类成员、也不是配置的 agent 的 Buzz 作者，以「名字（Buzz·非成员）：正文」镜像进飞书——同样能 @ 到 agent（真的 `<at>`）、不能 @ 到人，`agent_bot_unavailable` 不受影响，计入既有的 `to_feishu`（不新增报告字段）；测试 `test_buzz_feishu_group_sync_buzz_context.py`。 |
| `../../scripts/buzz_acp_media_proxy.py` | **stock Buzz 的透明图片输入层**：保持真实 adapter basename 的 NDJSON stdio proxy，从 prompt 的 `Tags:` 解析 imeta，以 Agent 现有身份调用 stock `buzz media get`，核对 size／SHA-256／magic MIME 后追加不含 `uri` 的 inline ACP image block；默认由 setup 安装，失败只保留 text prompt。安装、回滚与真实非成员图片 L4 见 [../acp-media-proxy.md](../acp-media-proxy.md)；测试 `../../tests/test_buzz_acp_media_proxy.py`。 |
| `../../scripts/buzz_responsible_mentions.py` | 将 GitLab 结构化 owner/reviewer/assignee 或站立席位的 GitLab 用户名（`person` locator）确定性解析为当前 Channel 的 human member（role 是 owner/admin/member）pubkey；用户名经 owner-only `people_file` 映射到 pubkey、去重、最多 3 人、拒绝 guest／bot／非成员／歧义／`@all`，供所有 Agent 发行动消息前使用 |
| `../../scripts/buzz_agent_join_requests.py` + `buzz-agent-join.example.json` | **入群申请**（ADR-0018）：owner 定时任务以各业务角色 agent 的身份发现清单外的新频道、在群里发申请，只认 owner 签名的 ✅／`/approve JOIN-<id>`，同意后改 env 清单／责任人配置／prompt 频道表并在空闲时重启、核对订阅。示例配置是占位值、不含 secret，键严格校验。运行手册见 [../agent-channel-join.md](../agent-channel-join.md) |
| `buzz-responsible-mentions.example.json` | `BUZZ_RESPONSIBLE_CONFIG` 指向的 0600 配置示例（责任人 helper `buzz_send_with_responsible_mentions.py`）：占位 sender／Channel／pubkey，不含 secret；键严格校验，`version` 是整数 `1`、`gitlab.projects` 是整数 project id 列表。字段类型表见 [../runtime-setup.md](../runtime-setup.md)「发消息前的责任人注意力门禁」 |
| `gitlab-buzz-sync.example.json` | 同步脚本的频道配置示例：占位频道 UUID 与 pubkey，不含 secret；键是严格校验的，只能用示例里出现的这些 |
| `../../scripts/gitlab_buzz_route_reply.py` | relay 0.2.1 的确定性 Canvas route gate。默认由 Desk 在同步后的同一 turn 以 `--scan-once` 调用，使用 Desk 身份读取最新可信 raw kind 40100 Canvas、匹配 Bridge fact，并在 canonical Thread 回复；同一程序的 HTTP listener 只是降级 adapter |
| `gitlab-buzz-route-writer.example.json` | 默认本地模式配置：`scan_since`、同一个 Desk sender/publisher、Canvas admin allowlist、code-owned Role id→mention/pubkey 与 Buzz CLI pin；不含 route table、prompt 或 secret |
| `../gitlab-buzz-routing-canvas.md` | Channel admin 可编辑的严格版本路由块。只含 route id、完整同步 header prefix、Role id 与 reason |
| `../workflows/route-type-status-fallback.yaml` | HTTP 降级 Workflow 模板。`<route-reply-url>` 是占位域名；`X-Route-Secret` 是 Channel 成员可读 bearer，不是系统 secret。默认本地 gate 和本模板不能同时启用 |
| `gitlab-buzz-route-reply.example.json` | HTTP adapter 配置示例；路由仍来自最新可信 Canvas，Role identity 来自 code registry；真实 bearer 只从 `secret_env` 指定的环境变量注入 |
| `../../scripts/gitlab_buzz_sync_timer.py` | **timer 入口**（[ADR-0008](../../../../docs/05-adr/0008-run-gitlab-sync-from-owner-systemd-timer.md)）：owner 的主机调度器经白名单 launcher（校验 0600 env、丢弃白名单外变量、secret 不进 argv）零参数启动；运行一次 runner（sync + route），再为已认领的 summary request 生成 `template_summary` 并经 publisher 发布；无 LLM，首错非零退出。部署见 Linux [systemd](../systemd/README.md) 或 macOS [launchd](../launchd/README.md) |
| `../../scripts/run_offline_tests.py` | macOS/Linux 统一离线测试入口：把 `TMPDIR` 规范化为 canonical 路径后在固定 tests 根执行 unittest discovery；可用 `--pattern 'test_gitlab_buzz_sync*.py'` 缩小范围 |
| `../../scripts/gitlab_buzz_people_generate.py` | **人员映射生成器**（`people` / 共享 `people_file`，skills#110、buzz-deploy#77）：把 GitLab 项目成员的用户名按 `username@a4x.io`（localpart 兜底，不同邮箱对不同公钥歧义即 `unmapped`）连到 Buzz 公钥。**推荐 API 模式**：`--people-api-base-url` + `--signer-env-file`，向 bridge 的 `GET /bind/api/channels/{channel}/people` 用 owner/admin 的 Buzz key 做 NIP-98 签名取该频道现存成员的 `emails`（bridge 须部署带 `emails` 的接口并开 `CHANNEL_PEOPLE_EMAILS_ENABLED`；key 只在本进程签名；无 `emails`、非 200、超时、响应不合法、频道对不上一律 fail closed、不改任何文件）。`--export`（`ops export-people` 文件）是回退 / 离线来源，两者互斥。`--people-file` + 可重复的 `--config` 走共享模式（每个频道各取一次并合并）；**同一邮箱绑了多把公钥（API 模式）选一把**：先剔除会被拒的，再取在最多个已配置频道里是成员的、并列取 hex 最小的，警告只带用户名 / 前 8 位 / 放弃数，`--export` 不变；手填优先、不删除、Desk/agent 公钥拒写、只打印用户名与计数。用法、参数表与迁移步骤见 [../gitlab-buzz-sync.md](../gitlab-buzz-sync.md)「people 的生成器」；测试 `../../tests/test_gitlab_buzz_people_generate*.py`（API 模式在 `test_gitlab_buzz_people_generate_api.py`，假 bridge，无网络） |
| `../gitlab-buzz-sync.desk-prompt.md` | 普通 Desk 提示词片段：解释同步由 timer 完成、Channel 消息不能触发同步；Desk 不运行任何同步脚本 |
| `gitlab-buzz-sync.example.json` 里的名字 | 都是占位：`bot_username` 按 project access token 的 bot 用户名形式写（`project_<id>_bot_<hash>`），`token_env` 换成自己的变量名 |
| `../../tests/test_gitlab_buzz_sync*.py` | 同步与路由的 L1／L2-1 离线测试，CI 必跑（`test_gitlab_buzz_sync_human_origin.py` 用照真实 `buzz messages thread` 输出建模的 fake 覆盖 origin 指向人的顶层消息，夹具在 `../../tests/fixtures/buzz_thread/`）。`tests/integration/` 下是 L2-2（真脚本 × 本地 relay × 本地 GitLab）、L2-3（`stack.py timer-run` 以 Desk 身份运行 timer 入口，验证 Channel 消息不能触发同步）、L3（可信 Canvas → Desk gate → 原 Thread 唤醒 Role Agent），默认 skip，需本地 localstack，运行方式见测试方案 §3.1 |
| `../../scripts/issue_thread_router.py` | **已取代**（见上一行与 ADR，清理阶段删除）。每业务 Channel 的 Desk 一实例：Buzz relay scheduler 按 Channel schedule 唤醒 Desk 后，脚本作为同一 Agent worker turn 的本地子进程，先回读 GitLab project id／URL／public visibility，再按 `created_at ASC` 分页拉稳定 Issue universe，以 GitLab `Date - 1s` 固定 scan boundary、本地 `updated_at` 窗口和 Git 配置中 pin 的 `deployment_baseline.max_iid` 确定性区分 new/update，维护 waterline 与 Issue↔Thread 双向绑定并输出 `desk_actions`；new 或路由事实／内容／policy 变化时写内嵌 policy/digest 的 snapshot checkpoint，Note-only `updated_at` 只吸收到本地 state，避免自触发；历史 checkpoint／已完成 action 按其旧 policy 验证，routes／Agent pubkey 变化保留状态机判定并在身份变化时重指派，旧 policy pending action 或未迁移的 `status_order` 变化 fail closed；state 丢失时从同一 baseline、GitLab binding/action/checkpoint Notes 与 Buzz facts 原子重建，缺 pin／模糊旧历史／部分恢复 fail closed；private／internal project，以及 `confidential` 非显式布尔 `false`、state 非精确 `opened|closed` 的 Issue，都在任何 Buzz／machine Note write 前 fail closed；它是 Desk 组件，不是独立 Agent；放在 Skill 根 `scripts/` 以进入仓库脚本安全扫描 |
| `issue-thread-router.example.json` | 不含 secret 的业务 Channel 路由模板；Agent 明示 `kind`，状态顺序与路由规则版本化；首次 `--initialize` 后将输出 pin 到 `gitlab.deployment_baseline` 并经 Git MR 评审 |
| `../../tests/test_issue_thread_router.py` | 路由矩阵、身份／origin、非法状态跳转、executor target、closed、poll replay、三类 Note 稳定读取／readback、state-loss 恢复、checkpoint 防自触发、A→B→A 版本身份、action/checkpoint crash windows、routing policy epoch、fresh audience TOCTOU、严格物理第一行 marker 与 raw Issue prompt-injection 隔离的离线行为测试 |
| `../../tests/test_fchac_contract.py` | FCHAC reference 路由、完整角色、BI 边界、通用 ACT 拒绝项与真实／mock 验证边界的离线契约测试 |

以下两段描述**已取代的旧 router**，保留作参考。当前 GitLab 同步见 [../gitlab-buzz-sync.md](../gitlab-buzz-sync.md)：它用精确 Channel 成员快照替代 public-only 门禁，并由 Desk-owned Agent Step 使用 Desk identity 发布。

router 的协议消息与 machine Notes 不回显 raw title／description／labels；非法 route label grammar 只得到 `(invalid)`／digest，不进入 marker。这些字段只在 `GET project → GET Issue → GET project` fresh gate 后作为 stdout `untrusted_issue` 数据帧交给同 turn Desk。root／lifecycle／receipt marker 只接受 LF 物理第一行逐字相等。pending source 落后时 stdout 固定 `source_stale=true, required_outcome=desk-only`，ack 硬拒绝 role mention；旧 action ack 后下一轮处理新 snapshot。这些是旧 router 自身的门禁，不代表当前 Bridge candidate 的运行拓扑。

## 典型流程

```bash
export PATH="$HOME/.nvm/versions/node/v24.14.0/bin:$PATH"   # 内置 WebSocket 要 Node ≥22
BUZZ_CLI=/home/jchen/.local/opt/buzz-0.5.23/usr/bin/buzz
# 必须是实际 absolute、regular、executable、非 symlink 的 ELF；不要调用不可靠的 --version
# 也不要用会加载 owner key 的 ~/.local/bin/buzz wrapper（自动化／脚本／测试里禁用；区别见下文「wrapper 与原始 ELF」）

# 1. 铸身份（先落到 Agent 配置目录，随后拆进各自 env）
install -d -m 700 "$HOME/.config/buzz/agents"
(umask 077; python3 mint-agent.py nh-desk nh-dev nh-bi > \
  "$HOME/.config/buzz/agents/new-keys.json")

# 2. 用 agent 自己的身份注册档案 + 入频道
( set -a; source ~/.config/buzz/agents/nh-desk.env; set +a
  "$BUZZ_CLI" users set-profile --name nh-desk --about "..."
  "$BUZZ_CLI" channels join --channel <CH> )

# 3. owner 身份设 bot 角色
"$BUZZ_CLI" channels add-member --channel <CH> --pubkey <agent_hex> --role bot

# 4. owner 身份发 kind:30177（第 3 个必要条件，最容易漏）
EVENT="$HOME/.config/buzz/agents/nh-desk-30177.json"
install -m 600 /dev/null "$EVENT"
$EDITOR "$EVENT"  # 写入 kind=30177、d=<agent pubkey>、name／parallelism／respond_to

# 私钥不进 argv 或 shell history；只在一次性子 shell 隐式读取，结束即释放
(
  read -rs -p "Owner private key: " BUZZ_OWNER_SECKEY; echo
  export BUZZ_OWNER_SECKEY
  node publish_event.mjs wss://buzz-sg.addx.live "$EVENT"
)

# 5. 验证三个条件都成立（别只看 OK true）
"$BUZZ_CLI" channels members --channel <CH>          # role 应为 bot
# 30177 用 REQ {kinds:[30177],authors:[<owner>]} 回读确认 content 里的 respond_to
```

**AI 非交互执行第 4 步**（owner 密钥在 `~/.config/buzz/env` 里是 `nsec`，而 `publish_event.mjs` 要 64 位 hex，上面的 `read -rs` 只适合人手敲）：在一次性子 shell 里把 nsec 解成 hex，只经环境变量交给 node，不进 argv、不打印、不落盘：

```bash
export PATH="$HOME/.nvm/versions/node/v24.14.0/bin:$PATH"   # 系统 node 是 20，内置 WebSocket 要 ≥22
( set -a; . ~/.config/buzz/env; set +a
  SK=$(cd <skill>/references/scripts && python3 -c 'import os,nostrkit; print(nostrkit.bech32_decode(os.environ["BUZZ_PRIVATE_KEY"],"nsec").hex())')
  [ ${#SK} -eq 64 ] || { echo "bad key"; exit 3; }
  BUZZ_OWNER_SECKEY="$SK" node <skill>/references/scripts/publish_event.mjs wss://buzz-sg.addx.live "$EVENT" )
```

铸身份时的临时密钥文件（`mint-agent.py` 的 stdout）用完立刻 `shred -u`；env 文件用 `O_EXCL|0600` 创建，不覆盖已有文件。私有 Channel 里 Agent 不能自己 `channels join`（`restricted: channel is private`），只能由该频道的 owner/admin `add-member --role bot`（agent 的 `channel_add_policy` 是 `owner_only` 时，操作者还必须是 agent 的 owner，两个条件都要满足，见 [../runtime-setup.md](../runtime-setup.md)「谁能把 agent 拉进频道」）；先 `join` 再 `add-member` 的顺序对公开 Channel 有效，私有 Channel 直接 `add-member`。

GitLab → Buzz 全量变更同步的频道配置、Desk 提示词、运行面、停用回滚与切换清单见 [../gitlab-buzz-sync.md](../gitlab-buzz-sync.md)；先从仓库根离线跑一遍 `python3 skills/buzz-agent-setup/scripts/run_offline_tests.py --pattern 'test_gitlab_buzz_sync*.py'`。

relay 0.2.1 默认由 Desk 使用自己的 Buzz env 运行 `gitlab_buzz_route_reply.py --config <config>
--state-dir <0700-state-dir> --scan-once`。route table 从最新可信 Canvas 读取；publisher、Canvas admin
allowlist 与 Role identity 留在 0600 code config。state dir 持久保存 cursor 与每个
source+route+policy 的 PENDING/ACKED；不确定写入只回读恢复，不自动重发，停用前不能删除。
这个默认路径不需要 Workflow，不监听端口；Agent prompt、skills 与 SaaS scope 不进入 Canvas 或 route config。

仅在 Desk 无法执行本地固定命令时才评估 HTTP adapter。该进程有 32 并发、5 秒 body read、120
请求/分钟的 fail-safe；stderr JSON 审计不记录 header/body，HTTPS ingress 还必须独立做限流、
buffering 与告警。停用时先删 fallback Workflow，确认无在途调用，再停止服务、轮换 bearer、撤销
HTTP sender membership 并归档 state；切回默认 Desk gate 前不得保留旧规则。

旧 Desk Issue polling component（已取代）的 Buzz schedule 部署、prompt 契约与验收清单见 [../issue-thread-routing.md](../issue-thread-routing.md)。先运行：

```bash
python3 ../../scripts/run_offline_tests.py
python3 ../../scripts/issue_thread_router.py --config issue-thread-router.example.json \
  --resolve-route feature ready opened
```

> owner 私钥用完即焚：优先从隐藏 stdin 读入一次性子 shell 的环境，不落盘、不留在 shell history，
> **也别放命令行参数**——`/proc/<pid>/cmdline` 是 `-r--r--r--`，同机任何用户 `ps aux` 就能拿走；
> `/proc/<pid>/environ` 是 `0400`，只有属主读得到。若工具支持 stdin，直接走 stdin。

## mint-agent 用法

```bash
python3 mint-agent.py -h            # 或 --help：只打印用法，退出 0
python3 mint-agent.py nh-desk nh-dev  # 铸两个身份，JSON 输出到 stdout（含 nsec，见上文落盘方式）
```

- **`-h`／`--help` 只打印用法**，退出码 0，**不读 owner 密钥，不生成任何密钥**；用法文字里没有任何密钥字段。**不要为了试参数而铸密钥**：修复前的脚本没有 `--help`，会把 `--help` 当成 agent 名字，真的铸出一对密钥、用 owner 密钥签好 NIP-OA 背书，并把 nsec 打到 stdout。若曾经那样跑过，把那次输出当作已泄露：丢掉那对身份、不要拿去用，重定向落过盘的文件 `shred -u`。
- **名字规则** `[A-Za-z0-9][A-Za-z0-9._-]{0,62}`（首字符字母或数字，最长 63 个字符；`jchen-ubuntu-todo`、`nh-desk` 都合法）。以 `-` 开头的参数、含空格／斜杠／`;`／换行的名字、空串、超长名字都不合规：stderr 报错，**退出码 2**，stdout 没有任何密钥材料，也**不读 owner 密钥**。所有参数先整体校验再铸：混在合法名字里有一个坏名字，整批拒绝，不会铸出一半。
- 没有参数是用法错误：用法写到 stderr，退出码 2。合法名字的行为不变。
- **输出里的 `auth_tag` 是裸 JSON 字符串**（`["auth","<owner_pub>","","<sig>"]`），拼进 0600 env 时**必须整段包单引号**：`BUZZ_AUTH_TAG='["auth","<owner_pub>","","<sig>"]'`。不加引号，启动器或手工 `set -a; . <name>.env` 用 `source` 读，shell 会吃掉里面的双引号，变成 `[auth,<hex>,,<sig>]`，owner 背书就坏了，而且**不报任何本地错误**：buzz-acp 启动后立刻 `Auth failed: restricted: not a relay member`、每 5 秒重启一轮；用同一份坏 env 跑的 `users set-profile` 还返回 `accepted:true`，profile 里的 NIP-OA 背书却是坏的。用脚本把 `auth_tag` 写进 env 时同样要带引号（不要 `printf 'BUZZ_AUTH_TAG=%s\n'`）。写完自检（只看是否仍带双引号，不打印私钥）：

  ```bash
  ( set -a; . "$HOME/.config/buzz/agents/<name>.env"; set +a; echo "$BUZZ_AUTH_TAG" | cut -c1-8 )   # 必须是 ["auth",
  ```

  已经踩到的补救与症状见 [../troubleshooting.md](../troubleshooting.md)「Auth failed: restricted: not a relay member」。

## `~/.local/bin/buzz` wrapper 与原始 ELF 的区别

`~/.local/bin/buzz` 是会加载 owner 密钥（`~/.config/buzz/env`）的包装器，所以**在测试、脚本、定时任务和任何自动化里仍然禁用**：这些场景只用 `buzz-0.5.23` 原始 ELF 的绝对路径，显式传环境变量和（测试）密钥。这不等于人不能用它：**交互式的 owner 操作**（加／移成员、发 reaction／标记等）本来就是以 owner 身份做的，在用户授权时可以用这个 wrapper；上文「不要用 wrapper」说的是自动化里不能用，不是禁止 owner 本人交互。

## 已知限制

- `nostrkit.py` 的 `point_mul` 是朴素 double-and-add，**不是常量时间实现**，存在时序侧信道。
  这些脚本是给人手工配 agent 用的一次性工具，够用；**别把它搬进会被外部反复触发签名的服务**，
  那种场景请用经过审计的库（如 `secp256k1` / `noble-curves`）。
- 自检（`python3 nostrkit.py`）用显式 `raise` 而非 `assert`：`python3 -O` 会把 `assert` 整条剥掉，
  用 assert 写的自检在 `-O` 下会**假绿**（破坏测试向量也照样打印 OK 并退出 0）。
