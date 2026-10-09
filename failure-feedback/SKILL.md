---
name: failure-feedback
description: 在原 Agent 会话读取 AddX Hook 自动诊断、解释事实与根因候选、预览或确认 Issue 草稿、核实提交状态。用于 Claude Code、Codex local、Grok Build 及已接入受控 operation wrapper 的 Agent；无需新 Web 页面。
---

# 原会话故障反馈

## Description

遇到注册操作失败、收到 `AddX failure feedback` 上下文或用户询问诊断进度时使用。
Hook 捕捉只是触发器，用户交互仍在当前 Agent session。产品使用沿用其产品 Skill（Host 使用 [device-cloud-host-usage](../../hardware/device-cloud-host-usage/SKILL.md)）。

## Rules

- [通用需求与实现计划](https://gitlab.addx.ai/engineering/skills/-/blob/codex/190-agent-failure-feedback/docs/requirements/190/plan.md)，Work Item: skills#190。
- [运行命令与配置](../../../agent-feedback/README.md)。默认禁用，配置有 scope 和 runtime；不得为排障擅自修改用户的信任配置／安装 Plugin。
- Hook 不上传完整 prompt、命令或 stdout/stderr；只捕捉 pack 登记操作。读日志仍需原 scope 授权，不能搜索整个电脑、任意项目或全量用户数据。
- 没有验证原生 runtime 的真实回执时显示 PARTIAL。Grok passive Hook stdout 被忽略；在同会话用 MCP／CLI inbox，不承诺主动唤醒 idle Agent。

## 会话内处理

1. 当前失败已确认时简短说明诊断正在排队，原任务可继续。不要将取消、正常预期非零、HTTP202 或缺终态解释成 Bug；退出码0单独不证明业务成功。
2. 若有 MCP，调用 `feedback_inbox`；否则按 README 在当前会话用 `feedback.py inbox`。session 路由必须绑定当前 runtime 的原 session，不猜另一个 session，不用目录名代替。
3. 一句话解释结果，再给关键事实、未验证假设、证据缺口与可执行建议。默认引擎的 `AI_UNAVAILABLE / UNKNOWN` 是确定性摘要，不是已完成 AI RCA。可在用户已有授权范围内使用现有日志工具补证；模型文本不自行升级为已证实事实。
4. 呈现后再调用 `feedback_ack`／CLI `ack`，绑定 episode 和准确 revision。读取不等于呈现；结果更新后旧 ack 不能删除新结果。
5. 用户需要反馈时调用 `feedback_draft`／CLI `draft`，在原会话展示公开草稿、目标仓库、revision、内容 hash 和查重状态。没有执行查重就明确“待提交前查重”，不能写成“未找到重复项”。
6. 用户明确确认该草稿后，将原样 proposal 存为私有临时 JSON，通过 `submit --proposal <file> --confirm-hash <displayed-hash>` 调用。只批准目标、内容和 revision 相同的草稿；用户改稿或出现多个草稿时重新绑定具体修订，不沿用旧确认。当前本地版本仅提交其生成的确定性草稿；编辑自定义正文沿用 [gitlab-issue-sop](../../collaboration/gitlab-issue-sop/SKILL.md) 的受控流程。
7. `LINKED` 时在同会话展示已回读 URL。`WRITE_AMBIGUOUS` 时说明正在核实，调用 `reconcile` 查询稳定 marker；禁止再创建相同 Issue。`WRITE_PENDING` 时等待当前写者，不绕过事务锁。

## 写入边界

- 此 Skill 本身不授予外部写入权限。`draft` 需要用户确认和独立 reviewed policy 文件；身份、owner、目标、权限、查重或预算不足时保留草稿。
- 本地版本拒绝多人 `auto`，直到中央 coordinator 和项目预授权真实验收；不要通过把 policy 改为 draft 或重建 DB 绕过该门槛。
- [gitlab-issue-sop](../../collaboration/gitlab-issue-sop/SKILL.md) 是 Issue label／归属／生命周期唯一规则；不创建临时 label，不关闭 Root Issue 冒称上线。
- 会话结束后台任务仍可完成，恢复原会话读 inbox。不默认另建会话、转 Web、发群消息或 DM。

## Examples

用户：“刚才插件安装失败，是什么原因？”

Agent：在原会话读 inbox，说明“已确认下载阶段失败；权限与网络原因仍待验证”，列明证据缺口和只读核对步骤；呈现准确 revision 后 ack。不能说“已确认网络故障”并自动重放安装。

用户：“把这个草稿提交。”

Agent：绑定当前展示的草稿和 hash，在 policy 允许时查重、提交、回读；若回执丢失则进入 WRITE_AMBIGUOUS，核实后给出原 Issue 链接，不重试 POST。
