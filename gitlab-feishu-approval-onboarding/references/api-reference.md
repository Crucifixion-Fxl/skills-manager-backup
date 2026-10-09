# 界面与接口参考

核对日期：2026-09-28。已部署服务源提交 `c3212e7563a9a2e4f49f9539653d0b4a5ee3ad39`，
对应 main 合并提交 `64a369780462296da499becac6a25bf4e4ce144c`。
本参考来自实际服务源码、公网只读核对与本次会话的真实联调反馈，不是推测的开放 API。
实时页面优先；标签或状态不一致时重新核对，不强行执行旧步骤。

## 标签触发版本依赖

2026-09-30 新增步骤对应服务 [标签触发需求](https://gitlab.addx.ai/DEV/gitlab-feishu-approval/-/issues/2) 的候选实现，尚未以本 Skill 声明部署完成。实际页面必须出现以下触发控件才可提交标签配置；缺少时停止依赖动作并报告版本不支持。旧源码链接仍仅证明原有功能。

## 页面定位

应用入口：`https://gitlab-feishu-approval.addx.live/projects`。
原生 HTML + JavaScript，无 UI 框架和侧边栏，不依赖 `.el-`、`.ant-` 或 `.arco-` 选择器。
飞书窗口工具优先使用当前快照中的标签和按钮。普通浏览器只提供端内入口，不能承载登录后的代填。
下列 ID 是源码核对的辅助定位，不应在页面不可见时强行操作。

| 可见文字 | 控件 ID | 说明 |
|---|---|---|
| 在飞书中打开 | `open-feishu` | 没有原生桥接能力时出现，进入飞书窗口后重新读取状态 |
| 在飞书中重新验证 | `login` | 主动重试原生验证；不是浏览器 OAuth 登录 |
| 绑定 / 更新 GitLab 授权 | `bind` | 绑定本人账号；不能替别人绑定 |
| 代码仓库地址 / 目标合并分支 | `repository` / `branch` | 修改任何一个都会使已读配置失效 |
| 读取项目配置 | `inspect` | 读取后才显示名单和规则 |
| 何时发起 MR 审批 | `trigger-mode` | 新版：添加指定标签后 / 全部 MR；两者均非草稿才触发 |
| 触发标签 | `trigger-label` | 新版：默认 feishu-approval，区分大小写、完整匹配 |
| 添加审批人 | `add-reviewer` | 添加 MR 审批人；名单顺序用于 AND |
| 姓名、别名或拼音 | `person-query` | 支持单字与完整拼音；最多返回 20 人 |
| MR 审批方式 | `mode` | `OR` / `AND` |
| 作者有合并权限时，优先交给作者 | `author-first` | 已启用配置中禁用，首次批准后锁定 |
| 选择审批人 / 更换审批人 | `choose-manager` | 指定本次接入申请的审批人，执行管理权限预检 |
| 提交飞书接入审批 | `submit` | 唯一的本 Skill 业务写入动作 |
| 查看本次申请 / 检查上次提交的申请 | 动态链接 | 读取实际链接，不拼造申请 ID |
| 同意并启用此配置 / 转交 / 拒绝 | `approve` / `transfer` / `reject` | 不在本 Skill 的申请提交范围 |

所有自动化步骤先看实时状态再定位。切换标签页会清空敏感表单，恢复后可能需要重新读取和填写。
首次冷缓存搜索出现「通讯录正在加载，自动重试中…」时允许页面自动重试，不连续点击读取或搜索。

## 业务模型

- 配置唯一键：组织＋GitLab 实例＋不可变项目 ID＋准确目标分支；仓库名用于展示。
- 申请人要能用本人 GitLab 授权读取项目和分支；接入批准人必须有项目管理权限。
- MR 审批人是可搜索的本组织在职成员，可未绑定；接入审批人必须已绑定并通过 Maintainer / Owner 预检。
- `author_first` 首次批准后不可变；后续更新生成新版本，只影响新建 MR 工作流。
- `base_version` 是读取时的当前版本；并发配置变化应重新读取并展示差异，不能强行覆盖。
- 提交、飞书投递、批准启用、新 MR 待审批是四个不同状态，不能由前一个推断后一个成功。

## 接口契约：用于理解与定位，不是复制凭证的调用指南

接口使用经飞书原生登录验证的安全会话；写请求还校验同源 Origin、CSRF、版本和真实权限。
普通执行走 UI，由页面携带这些信息。没有独立 API key；不能把 GitLab PAT 或飞书 tenant token 当作服务登录凭证。

首次原生验证统一经过 `/auth/feishu/client`，只使用 SDK 返回的一次性授权码；URL 查询参数中的 code/state 不作登录凭证。
旧 `/auth/feishu/callback` 返回 410，不能用于恢复登录。这里的原生入口限制不是设备证明。

| 方法与路径 | 参数/响应重点 | 边界 |
|---|---|---|
| GET `/api/session` | 当前用户会话与页面所需防伪信息 | 不输出或导出完整响应，不把安全字段写入回执 |
| GET `/api/project-configuration` | `repository`、`branch`；响应含 `configuration` 与 `discovery` | 本人项目读取权限；旧配置不因读取而变化 |
| GET `/api/project-people` | 同一仓库分支＋`query`；或选中人员后的 `person` | 搜索姓名；`person` 执行接入审批人能力预检 |
| POST `/api/enrollments` | 页面生成 `request_id`；仓库、分支、基线版本、接入审批人、MR 审批人、OR/AND、作者优先；新版还必须显式传 trigger_mode（all/label），label 模式需 trigger_label | 创建待审批申请，不能直接启用配置 |
| GET `/api/enrollments/{id}` | 当前申请、候选版本与本人是否可决定 | 仅允许授权参与者读取；终态不再显示详情 |
| POST `/api/enrollments/{id}` | `approve/reject/transfer` 与 revision | 本 Skill 不调用，交指定审批人在飞书处理 |

人员 ID 只取已验证搜索结果，由页面保存；不请用户记忆 ID，不从另一飞书应用拷贝 ID。
未绑定标记只表示绑定状态；不能据此推断项目读写权限。

## 故障处理

| 现象 | 下一步 |
|---|---|
| 在飞书中打开 / feishu_client_required | 使用页面入口切到本组织飞书窗口并重新读取；工具无法操作该窗口时交用户填写，明确尚未提交 |
| 正在验证身份 / 401 | 等待固定原生登录页自动验证并返回；需要用户授权时交用户完成，不改用旧浏览器登录 |
| 在飞书中重新验证 | 用户明确重试后重新读取实际页面；不能依据点击本身报告登录成功 |
| 身份验证未完成 / 客户端授权错误 | 记录可见错误码，交管理员核对固定登录地址和飞书应用配置；不自行关闭校验或扩展可信域名 |
| 需要绑定 GitLab / 428 | 用户点「绑定 / 更新 GitLab 授权」，使用本人账号 |
| 仓库或分支无法读取 / 403、404、校验失败 | 核对精确仓库路径、目标分支、本人的项目读取权限；GitLab 可能隐藏无权访问的项目，不直接断言不存在 |
| 没有匹配人员 | 用飞书姓名、别名、单字或完整拼音；等待冷缓存。不是 GitLab 用户名、手机号或邮箱搜索 |
| 接入审批人不可选 | 对方先本人绑定，确认在职与项目 Maintainer / Owner；不能因此改用管理员账号 |
| 配置或审批状态已变化 / 409 | 先确认是否为同一申请的未知提交结果；否则重新读取版本并核对用户原意，不自动覆盖并发更新 |
| 申请已提交 / 有查看链接 | 仅报告已受理待审批；回读链接确认配置，再由接入审批人在飞书处理 |
| 提交超时、上次提交结果待确认 | 同一账号点「检查上次提交的申请」。存在则复用回执；仅在确认不存在且页面允许时沿用同编号、同配置重试；状态未知时不重复提交 |
| 审批已结束 / 410 | 不能据此判断通过还是拒绝；回到项目页读取实际启用版本 |
| 后台读取异常 | 按页面提示更新批准人的本人 GitLab 授权，或由有权限人员通过新的配置申请接替；不直接改数据库 |
| 等待作者绑定 GitLab | 仅作者优先场景：作者本人登录并绑定后再检测，不用维护者权限推断作者能力 |
| 用户要求标签触发但没有「何时发起 MR 审批」 | 当前页面版本不支持，先等待服务升级，不能提交全量规则代替 |
| 新 MR 无待办且已启用标签规则 | 核对首次接入时间、精确目标分支、标签大小写和完整名称、非草稿状态；不要重复提交配置催送 |
| 已启用但旧 MR 没有待办 | 首次启用前的 MR 不补发；用启用后新建且目标分支匹配的 MR 验证 |

## 可核查来源

- [实际页面与控件](https://gitlab.addx.ai/DEV/gitlab-feishu-approval/-/blob/c3212e7563a9a2e4f49f9539653d0b4a5ee3ad39/internal/server/web/projects.html)
- [提交、选人及未知结果恢复](https://gitlab.addx.ai/DEV/gitlab-feishu-approval/-/blob/c3212e7563a9a2e4f49f9539653d0b4a5ee3ad39/internal/server/web/projects.js)
- [接口与接入身份校验](https://gitlab.addx.ai/DEV/gitlab-feishu-approval/-/blob/c3212e7563a9a2e4f49f9539653d0b4a5ee3ad39/internal/server/enrollment.go)
- [自动发现与真实作者权限](https://gitlab.addx.ai/DEV/gitlab-feishu-approval/-/blob/c3212e7563a9a2e4f49f9539653d0b4a5ee3ad39/internal/server/discovery.go)
- [使用说明与现有限制](https://gitlab.addx.ai/DEV/gitlab-feishu-approval/-/blob/c3212e7563a9a2e4f49f9539653d0b4a5ee3ad39/docs/project-onboarding.md)

- [固定原生登录与错误边界](https://gitlab.addx.ai/DEV/gitlab-feishu-approval/-/blob/c3212e7563a9a2e4f49f9539653d0b4a5ee3ad39/internal/server/web/client-login.js)

标签触发候选实现（已提交 MR，尚未以此声明部署）：

- [配置表单与触发提示](https://gitlab.addx.ai/DEV/gitlab-feishu-approval/-/blob/2437e670b132470f4b4847ac9a3b798edcda9e98/internal/server/web/projects.html)
- [标签、草稿与既有流程去重](https://gitlab.addx.ai/DEV/gitlab-feishu-approval/-/blob/2437e670b132470f4b4847ac9a3b798edcda9e98/internal/server/discovery.go)
- [接口字段、兼容与发布约束](https://gitlab.addx.ai/DEV/gitlab-feishu-approval/-/blob/2437e670b132470f4b4847ac9a3b798edcda9e98/docs/requirements/2/plan.md)
