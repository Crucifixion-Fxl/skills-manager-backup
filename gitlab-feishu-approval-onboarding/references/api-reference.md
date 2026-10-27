# 界面与接口参考

核对日期：2026-10-12。来源是服务源码、只读集群核对和真实联调反馈，不是推测的开放 API。
页面实时显示优先；标签或状态与本文不一致时重新核对，不强行执行旧步骤。

## 服务版本

| 能力 | 状态 |
|---|---|
| 接入申请、转交、配置替换、新 MR 自动发现、标签触发 | 已部署：集群镜像 `2437e67`（main `1a71e49`） |
| 链接预填、停用分支、撤回申请、作者优先可修改、接入审批人可未绑定、无合并权限与标签缺失提示、驳回/重开后新一轮、离职改派、立即合并（忽略 Pipeline） | [MR !42](https://gitlab.addx.ai/DEV/gitlab-feishu-approval/-/merge_requests/42) 已合并（main `8b8dc46`），部署后可用 |

判断依据是页面实际控件：没有「停用此分支的审批」「撤回申请」、预填提示或作者优先仍是禁用状态时，按旧版本处理，说明对应能力尚不可用，不用接口或脚本绕过。

## 页面定位

入口 `https://gitlab-feishu-approval.addx.live/projects`，申请详情 `/enrollments/{id}`。
页面是原生 HTML + JavaScript，没有 UI 框架和侧边栏。优先用当前快照里的可见文字；下面的 ID 只用于辅助定位。

| 可见文字 | 控件 ID | 说明 |
|---|---|---|
| 在飞书中打开 | `open-feishu` | 没有原生桥接时出现；进入飞书窗口后重新读取 |
| 在飞书中重新验证 | `login` | 重试原生验证，不是浏览器 OAuth |
| 绑定 / 更新 GitLab 授权 | `bind` | 本人绑定，不能替别人绑定；机器人/服务账号会被拒绝 |
| 代码仓库地址 / 目标合并分支 | `repository` / `branch` | 修改任一项会使已读配置失效 |
| 读取项目配置 | `inspect` | 读取后才显示规则与名单 |
| 何时发起 MR 审批 / 触发标签 | `trigger-mode` / `trigger-label` | 默认 `feishu-approval`，区分大小写、完整匹配 |
| （标签提示） | `label-status` | 「项目中还没有标签」时需先在 GitLab 创建 |
| 添加审批人 | `add-reviewer` | 名单顺序用于 AND；「无合并权限」仅提示 |
| 姓名、别名或拼音 | `person-query` | 单字、别名、完整拼音；最多 20 人 |
| MR 审批方式 | `mode` | `OR` / `AND` |
| 作者有合并权限时，优先交给作者 | `author-first` | 新版可修改，只影响之后新建的流程 |
| 选择审批人 / 更换审批人 | `choose-manager` | 接入审批人；未绑定可选，非 Maintainer 不可选 |
| 提交飞书接入审批 | `submit` | 本 Skill 唯一的业务写入动作 |
| 停用此分支的审批 | `disable` | 仅 Maintainer/Owner；用户明确要求时由用户或在授权下点击 |
| 撤回申请 | `withdraw` | 申请详情页，仅申请人 |
| 查看本次申请 / 检查上次提交的申请 | 动态链接 | 读取实际链接，不拼造申请 ID |
| 同意并启用此配置 / 转交 / 拒绝 | `approve` / `transfer` / `reject` | 不在本 Skill 的范围内 |

切换应用后页面会清空表单，回来时自动恢复仓库和分支并重新读取；人员需要重新确认。
冷启动显示「通讯录正在加载，自动重试中…」时等页面自动重试，不连续点击。

## 预填参数

`/projects` 可识别这些查询参数，读取项目配置后填入表单，并显示「部分设置已按链接预填，请核对后提交」：

| 参数 | 取值 |
|---|---|
| `repository` | 仓库完整 HTTPS 地址 |
| `branch` | 准确分支名 |
| `mode` | `OR` / `AND` |
| `trigger` | `label` / `all` |
| `label` | 触发标签 |
| `author_first` | `1` / `0` |

参数暂存在本页会话里，经过飞书登录和 GitLab 绑定跳转后仍会保留。人员不能预填。
飞书端内打开：`https://applink.feishu.cn/client/web_url/open?mode=window&url=<编码后的完整地址>`。

## 业务模型

- 配置唯一键：组织＋GitLab 实例＋不可变项目 ID＋准确目标分支；仓库名只用于展示。
- 申请人要能用本人 GitLab 授权读取项目和分支。接入审批人批准时必须在职、已绑定，并且是 Maintainer / Owner。
- MR 审批人是可搜索的本组织在职成员，可未绑定；合并时始终以点击者实时的 GitLab 合并权限为准。
- 每次批准生成新版本，只影响之后新建的流程。`base_version` 是读取时的版本：并发修改要重新读取，不能覆盖。
- 停用后不再发现新 MR；重新获批即启用，「启用时间」从那一刻重新计算。
- 提交、飞书投递、批准启用、新 MR 待审批是四个独立状态，不能由前一个推断后一个。

## 接口契约：用于理解与定位，不是调用指南

接口依赖飞书原生登录的会话；写请求还校验同源 Origin、CSRF、版本和真实权限。Skill 只走 UI，没有独立 API key。

| 方法与路径 | 重点 |
|---|---|
| GET `/api/project-configuration` | `repository`、`branch` → `configuration`（含 `disabled`）与 `discovery` |
| POST `/api/project-configuration` | `action=disable`，本人须是 Maintainer/Owner |
| GET `/api/project-people` | `query` 搜人；`person` 预检接入审批人；`reviewer` 预估合并权限 |
| GET `/api/project-label` | 标签是否存在 |
| POST `/api/enrollments` | 创建待审批申请（页面生成 `request_id`），不会直接启用 |
| GET/POST `/api/enrollments/{id}` | 详情；`approve/reject/transfer` 由审批人操作，`withdraw` 由申请人操作 |

人员 ID 只来自页面的验证搜索结果，不请用户提供，也不从别的飞书应用拷贝。

## 故障处理

| 现象 | 下一步 |
|---|---|
| 在飞书中打开 / feishu_client_required | 普通浏览器不能登录。交付预填链接，由用户在飞书中打开 |
| 正在验证身份 / 401 | 等原生登录自动完成；需要授权时交用户本人处理 |
| 需要绑定 GitLab / 428 | 用户点「绑定 / 更新 GitLab 授权」，用本人账号 |
| 仓库或分支无法读取 / 403、404 | 核对完整路径、准确分支和本人读取权限；GitLab 可能隐藏无权访问的项目，不能直接说项目不存在 |
| 没有匹配人员 | 用飞书姓名、别名、单字或完整拼音，不用 GitLab 用户名、手机号或邮箱 |
| 接入审批人不可选 | 已绑定但不是 Maintainer/Owner，换人；未绑定可以选 |
| 审批人显示「无合并权限」 | 可以保留；告诉用户此人同意后，需转交有权限的人才能合并 |
| 项目中还没有标签 | 找有标签管理权限的人在 GitLab 创建，不要换成近似标签 |
| 配置或审批状态已变化 / 409 | 先确认是不是同一申请结果未知；否则重新读取，核对用户原意 |
| 提交超时 / 上次提交结果待确认 | 用同一账号点「检查上次提交的申请」：存在就复用；明确不存在才用原编号重试；仍不确定就报告待确认 |
| 审批已结束 / 410 | 不能据此判断通过与否，回项目页读取实际启用版本 |
| 批准人的 GitLab 授权已失效 / 已没有项目 Maintainer 权限 | 新 MR 暂停或由名单中的维护者代读；提交新申请更换批准人 |
| 目标分支不存在 / 项目不可用 | 分支已删除改名或项目已归档；确认后重新接入正确分支，或停用此分支 |
| 已启用但旧 MR 没有待办 | 启用前创建的 MR 不补发，用启用后新建的 MR 验证 |
| 新 MR 超过 10 分钟仍无待办 | 核对启用时间、准确目标分支、标签大小写与全称、非草稿；不要重复提交申请 |

## 可核查来源

以下链接固定在 main `8b8dc46`；服务后续变更时同步更新提交号并重新核对。

- [项目接入页面](https://gitlab.addx.ai/DEV/gitlab-feishu-approval/-/blob/8b8dc4686d438a7aef4300990a87e59389a2cc44/internal/server/web/projects.html) · [脚本](https://gitlab.addx.ai/DEV/gitlab-feishu-approval/-/blob/8b8dc4686d438a7aef4300990a87e59389a2cc44/internal/server/web/projects.js)
- [接口与权限校验](https://gitlab.addx.ai/DEV/gitlab-feishu-approval/-/blob/8b8dc4686d438a7aef4300990a87e59389a2cc44/internal/server/enrollment.go)
- [新 MR 发现](https://gitlab.addx.ai/DEV/gitlab-feishu-approval/-/blob/8b8dc4686d438a7aef4300990a87e59389a2cc44/internal/server/discovery.go)
- [接入说明与限制](https://gitlab.addx.ai/DEV/gitlab-feishu-approval/-/blob/8b8dc4686d438a7aef4300990a87e59389a2cc44/docs/project-onboarding.md)
- [固定原生登录](https://gitlab.addx.ai/DEV/gitlab-feishu-approval/-/blob/8b8dc4686d438a7aef4300990a87e59389a2cc44/internal/server/web/client-login.js)
